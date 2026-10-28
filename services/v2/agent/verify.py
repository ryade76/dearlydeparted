"""VERIFY state handler — two-pass citation verification."""

from __future__ import annotations

import json
import logging
import re
from typing import AsyncGenerator, Dict, List, Set, Tuple

from services.v2.agent.states import AgentContext, AgentState, CitationV2
from services.v2.sse import events
from services.v2 import config
from services.v2.observability.spans import traced_span, set_llm_span_attributes

log = logging.getLogger("v2.verify")


def _build_file_url(path: str, metadata: dict) -> str:
    """Build file_url for citation. Skip CSVs — they may contain multi-email
    archives and raw CSV isn't useful to view inline regardless."""
    if not path:
        return ""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext == "csv":
        return ""
    return f"/files/{path}"


def _extract_citations(text: str) -> List[str]:
    """Extract [evidence_id] references from generated text."""
    return re.findall(r'\[([^\]]+)\]', text)


def _build_integer_alias_map(assembled_context: str) -> Dict[str, str]:
    """Map sequential integers '1','2',... to evidence IDs in assembled-context order.

    Used to rescue LLM outputs that cite [1] instead of [actual_chunk_id].
    The order matches the <evidence id="..."> tags as the LLM sees them.
    """
    ids = re.findall(r'<evidence\s[^>]*id="([^"]+)"', assembled_context)
    return {str(i + 1): eid for i, eid in enumerate(ids)}


def _resolve_integer_citations(text: str, alias_map: Dict[str, str]) -> str:
    """Replace [N] with [real_evidence_id] when N is a pure integer in alias_map."""
    def _replace(match: re.Match) -> str:
        cid = match.group(1)
        if cid in alias_map:
            log.info("Resolved integer citation [%s] -> [%s]", cid, alias_map[cid])
            return f"[{alias_map[cid]}]"
        return match.group(0)
    return re.sub(r'\[(\d+)\]', _replace, text)


def _stem(word: str) -> str:
    """Minimal suffix-stripping stemmer for English inflections.

    Handles the most common morphological variations in IT documents
    (plural, past tense, gerund, trailing -e) without external dependencies.
    """
    if len(word) <= 3:
        return word
    # Plural / verb -s forms
    if word.endswith('sses'):
        return word[:-2]            # processes → process
    if word.endswith('ies') and len(word) > 5:
        return word[:-3] + 'y'     # policies → policy
    if word.endswith('es') and len(word) > 4:
        return word[:-2]            # services → servic, provides → provid
    if word.endswith('ss'):
        return word                 # process stays
    if word.endswith('s') and len(word) > 4:
        return word[:-1]            # servers → server
    # Past tense / gerund
    if word.endswith('ied') and len(word) > 5:
        return word[:-3] + 'y'     # modified → modify
    if word.endswith('ed') and len(word) > 4:
        return word[:-2]            # provided → provid, managed → manag
    if word.endswith('ing') and len(word) > 5:
        return word[:-3]            # providing → provid
    # Trailing -e (aligns "service" with "servic" from "services")
    if word.endswith('e') and len(word) > 4:
        return word[:-1]            # service → servic, provide → provid
    return word


def _tokenize(text: str) -> Set[str]:
    """Tokenize with minimal suffix stemming for improved overlap scoring."""
    words = re.findall(r'\b[a-z]{2,}\b', text.lower())
    return {_stem(w) for w in words}


def _token_overlap_score(claim_text: str, evidence_text: str) -> float:
    """Compute token overlap ratio between claim and evidence."""
    claim_tokens = _tokenize(claim_text)
    evidence_tokens = _tokenize(evidence_text)
    if not claim_tokens or not evidence_tokens:
        return 0.0
    overlap = claim_tokens & evidence_tokens
    return len(overlap) / len(claim_tokens)


_ABBREV_RE = re.compile(
    r'\b(?:e\.g|i\.e|et al|vs|Fig|Dr|Mr|Mrs|Ms|Prof|Sr|Jr|U\.S|approx|dept|est|no|pp|vol)\.',
    re.IGNORECASE,
)


def _find_citing_sentence(text: str, citation_id: str) -> str:
    """Extract the sentence or segment that contains [citation_id].

    Tries multiple strategies in order of precision:
    1. Abbreviation-guarded sentence split (most precise for overlap/NLI)
    2. Line split (handles markdown bullets and numbered lists)
    3. Paragraph split (broader context, still better than query fallback)
    4. Window fallback (200-char window around the marker)
    """
    marker = f"[{citation_id}]"
    if marker not in text:
        return ""

    # Strategy 1: Sentence split with abbreviation guard
    protected = _ABBREV_RE.sub(lambda m: m.group(0).replace('.', '\x00'), text)
    for sentence in re.split(r'(?<=[.!?])\s+', protected):
        sentence = sentence.replace('\x00', '.')
        if marker in sentence:
            clean = re.sub(r'\[[^\]]+\]', '', sentence).strip()
            if clean:
                return clean

    # Strategy 2: Line split (handles bullets, numbered lists)
    for line in text.split('\n'):
        if marker in line:
            clean = re.sub(r'\[[^\]]+\]', '', line).strip()
            if clean:
                return clean

    # Strategy 3: Paragraph split
    for para in re.split(r'\n{2,}', text):
        if marker in para:
            clean = re.sub(r'\[[^\]]+\]', '', para).strip()
            if clean:
                return clean

    # Strategy 4: 200-char window around the marker
    idx = text.find(marker)
    if idx >= 0:
        start = max(0, idx - 200)
        end = min(len(text), idx + len(marker) + 200)
        clean = re.sub(r'\[[^\]]+\]', '', text[start:end]).strip()
        if clean:
            return clean

    return ""


async def verify_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Two-pass citation verification: token overlap + LLM NLI for borderline."""
    yield events.thinking("Verifying citations...")

    # Resolve integer citations ([1], [2], etc.) to real evidence IDs
    _int_alias = _build_integer_alias_map(ctx.assembled_context)
    if _int_alias:
        ctx.raw_answer = _resolve_integer_citations(ctx.raw_answer, _int_alias)

    citation_ids = _extract_citations(ctx.raw_answer)
    # Deduplicate while preserving order
    seen = set()
    unique_ids = []
    for cid in citation_ids:
        if cid not in seen:
            seen.add(cid)
            unique_ids.append(cid)

    # Build evidence lookup
    evidence_map: Dict[str, str] = {}
    evidence_meta: Dict[str, Dict] = {}
    for item in ctx.evidence:
        if item.item_id:
            evidence_map[item.item_id] = item.text
            evidence_meta[item.item_id] = {
                "path": item.path,
                "domain": item.domain,
                "bucket": item.bucket,
                "date": item.date,
                "doc_type": item.metadata.get("doc_type", ""),
                "file_url": _build_file_url(item.path, item.metadata),
            }

    verified_citations: List[CitationV2] = []
    borderline_ids: List[Tuple[str, str, str]] = []  # (citation_id, claim_text, evidence_text)

    # Pass 1: Token overlap
    for idx, cid in enumerate(unique_ids):
        evidence_text = evidence_map.get(cid, "")
        meta = evidence_meta.get(cid, {})

        if not evidence_text:
            # Citation references non-existent evidence
            log.warning(
                "Citation [%s] has no evidence entry. Known IDs (first 5): %s",
                cid, list(evidence_map.keys())[:5],
            )
            citation = CitationV2(
                index=idx,
                item_id=cid,
                path=meta.get("path", ""),
                verified=False,
                verification_method="no_evidence",
                verification_score=0.0,
                doc_type=meta.get("doc_type", ""),
                file_url=meta.get("file_url", ""),
            )
            verified_citations.append(citation)
            yield events.citation_verified(idx, cid, meta.get("path", ""), False, "no_evidence", 0.0)
            continue

        claim_text = _find_citing_sentence(ctx.raw_answer, cid)
        if not claim_text:
            claim_text = ctx.query  # Fallback to query

        overlap = _token_overlap_score(claim_text, evidence_text)

        if overlap >= config.V2_VERIFY_OVERLAP_THRESHOLD:
            # Pass 1: verified
            citation = CitationV2(
                index=idx,
                item_id=cid,
                path=meta.get("path", ""),
                text_excerpt=evidence_text[:200],
                verified=True,
                verification_method="token_overlap",
                verification_score=overlap,
                domain=meta.get("domain", ""),
                bucket=meta.get("bucket", ""),
                date=meta.get("date", ""),
                doc_type=meta.get("doc_type", ""),
                file_url=meta.get("file_url", ""),
            )
            verified_citations.append(citation)
            yield events.citation_verified(idx, cid, meta.get("path", ""), True, "token_overlap", overlap)
        elif overlap >= config.V2_VERIFY_NLI_LOW:
            # Borderline — queue for NLI
            borderline_ids.append((cid, claim_text, evidence_text))
            # Placeholder
            verified_citations.append(CitationV2(
                index=idx,
                item_id=cid,
                path=meta.get("path", ""),
                text_excerpt=evidence_text[:200],
                verified=False,  # Will be updated by NLI
                verification_method="pending_nli",
                verification_score=overlap,
                domain=meta.get("domain", ""),
                bucket=meta.get("bucket", ""),
                date=meta.get("date", ""),
                doc_type=meta.get("doc_type", ""),
                file_url=meta.get("file_url", ""),
            ))
        else:
            # Clear fail
            log.info(
                "Citation [%s] clear-fail: overlap=%.3f (threshold=%.2f), "
                "claim_len=%d, evidence_len=%d, claim='%s'",
                cid, overlap, config.V2_VERIFY_NLI_LOW,
                len(claim_text), len(evidence_text), claim_text[:80],
            )
            citation = CitationV2(
                index=idx,
                item_id=cid,
                path=meta.get("path", ""),
                text_excerpt=evidence_text[:200],
                verified=False,
                verification_method="token_overlap",
                verification_score=overlap,
                domain=meta.get("domain", ""),
                bucket=meta.get("bucket", ""),
                date=meta.get("date", ""),
                doc_type=meta.get("doc_type", ""),
                file_url=meta.get("file_url", ""),
            )
            verified_citations.append(citation)
            yield events.citation_verified(idx, cid, meta.get("path", ""), False, "token_overlap", overlap)

    # Pass 2: LLM NLI for borderline citations
    if borderline_ids:
        yield events.thinking(f"Running NLI verification on {len(borderline_ids)} borderline citations...")
        try:
            from services.chat_provider import ChatProvider
            import os

            nli_prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "verify_nli_template.txt")
            with open(nli_prompt_path, "r") as f:
                nli_template = f.read()

            chat = ChatProvider()

            for cid, claim_text, evidence_text in borderline_ids:
                prompt = nli_template.format(
                    claim=claim_text[:500],
                    evidence=evidence_text[:2000],
                )
                try:
                    with traced_span(f"llm.verify_nli.{cid}") as span:
                        resp = await chat.async_complete(
                            messages=[
                                {"role": "system", "content": "You are a citation verification judge. Respond only with valid JSON."},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.0,
                            max_completion_tokens=300,
                            user=ctx.request_params.get("user", ""),
                        )
                        set_llm_span_attributes(span, model=config.CHAT_MODEL, max_tokens=300, temperature=0.0)
                    text = chat.extract_text(resp).strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                        if text.endswith("```"):
                            text = text[:-3]
                        text = text.strip()
                    nli_result = json.loads(text)

                    verdict = nli_result.get("verdict", "not_supported")
                    confidence = float(nli_result.get("confidence", 0.0))
                    verified = verdict in ("supported", "partially_supported") and confidence >= 0.5

                    # Update the placeholder
                    for c in verified_citations:
                        if c.item_id == cid and c.verification_method == "pending_nli":
                            c.verified = verified
                            c.verification_method = "llm_nli"
                            c.verification_score = confidence
                            break

                    meta = evidence_meta.get(cid, {})
                    yield events.citation_verified(
                        next(i for i, c in enumerate(verified_citations) if c.item_id == cid),
                        cid, meta.get("path", ""), verified, "llm_nli", confidence,
                    )
                except Exception as exc:
                    log.warning("NLI verification failed for %s: %s", cid, exc)
                    # Keep borderline as unverified
                    for c in verified_citations:
                        if c.item_id == cid and c.verification_method == "pending_nli":
                            c.verification_method = "nli_failed"
                            break
        except Exception as exc:
            log.warning("NLI pass failed entirely: %s", exc)

    ctx.citations = verified_citations
    verified_count = sum(1 for c in verified_citations if c.verified)
    total = len(verified_citations)
    pass_rate = verified_count / total if total > 0 else 1.0

    ctx.verification_passed = pass_rate >= 0.5 or total == 0

    yield events.verification_result(
        total_citations=total,
        verified_count=verified_count,
        pass_rate=pass_rate,
    )

    # Retry logic: if verification fails and we haven't hit max retries
    if not ctx.verification_passed and ctx.retry_count < config.V2_VERIFY_MAX_RETRIES:
        ctx.retry_count += 1
        yield events.thinking(f"Verification failed (pass_rate={pass_rate:.2f}), retrying generation (attempt {ctx.retry_count})")
        ctx.current_state = AgentState.GENERATE
    else:
        ctx.current_state = AgentState.EMIT_FINAL
