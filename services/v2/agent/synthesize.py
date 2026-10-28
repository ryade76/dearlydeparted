"""SYNTHESIZE state handler — assemble all evidence into structured context."""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Dict, List

from collections import defaultdict

from services.v2.agent.states import AgentContext, AgentState, EvidenceItem
from services.v2.sse import events
from services.v2 import config
from services.v2.utils import path_stem

log = logging.getLogger("v2.synthesize")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def _format_evidence(item: EvidenceItem, idx: int) -> str:
    """Format a single evidence item with XML tags for citation anchoring."""
    attrs = f'id="{item.item_id}" type="{item.evidence_type}" path="{item.path}"'
    if item.domain:
        attrs += f' domain="{item.domain}"'
    if item.bucket:
        attrs += f' bucket="{item.bucket}"'
    if item.date:
        attrs += f' date="{item.date}"'
    return f"<evidence {attrs}>\n{item.text}\n</evidence>"


async def synthesize_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Assemble all retrieved evidence into structured context for generation."""
    yield events.thinking(f"Assembling context from {len(ctx.evidence)} evidence items...")

    max_tokens = ctx.request_params.get("max_context_tokens", config.V2_MAX_CONTEXT_TOKENS)
    sections: Dict[str, List[str]] = {
        "taxonomy": [],
        "entity_profiles": [],
        "graph_traversal": [],
        "graphrag_artifacts": [],
        "text_chunks": [],
        "temporal": [],
    }
    section_tokens: Dict[str, int] = {k: 0 for k in sections}

    # Deduplicate evidence by item_id
    seen_ids = set()
    unique_evidence = []
    for item in ctx.evidence:
        key = item.item_id or f"{item.path}:{item.text[:100]}"
        if key not in seen_ids:
            seen_ids.add(key)
            unique_evidence.append(item)

    # Sort: higher score first within each type
    unique_evidence.sort(key=lambda x: x.score, reverse=True)

    # Path-stem diversity: prevent near-duplicate documents from flooding context.
    # Only applies to text chunks — structured artifacts pass through unmodified.
    max_per_stem = config.V2_MAX_EVIDENCE_PER_PATH_STEM
    _CHUNK_TYPES = {"chunk", "text_unit", "procedure_step"}
    stem_counts: Dict[str, int] = defaultdict(int)
    promoted = []
    demoted = []
    for item in unique_evidence:
        if item.evidence_type not in _CHUNK_TYPES:
            promoted.append(item)
            continue
        stem = path_stem(item.path) if item.path else ""
        stem_counts[stem] += 1
        if stem_counts[stem] <= max_per_stem:
            promoted.append(item)
        else:
            demoted.append(item)
    # Append demoted items at end so they can still fill token budget
    unique_evidence = promoted + demoted
    if demoted:
        log.info("Path-stem diversity: promoted %d, demoted %d items from %d stems",
                 len(promoted), len(demoted), len(stem_counts))

    # 1. Taxonomy context
    for item in unique_evidence:
        if item.evidence_type in ("taxonomy_first_bucket", "taxonomy_sub_bucket", "taxonomy_bucket_card", "taxonomy_domain_profile"):
            formatted = _format_evidence(item, len(sections["taxonomy"]))
            tokens = _estimate_tokens(formatted)
            sections["taxonomy"].append(formatted)
            section_tokens["taxonomy"] += tokens

    # 2. Entity profiles (from ctx.entity_profiles)
    for profile in ctx.entity_profiles:
        profile_text = f"<entity_profile name=\"{profile.get('entity_name', '')}\">\n"
        if profile.get("descriptions"):
            for desc in profile["descriptions"]:
                profile_text += f"  Description: {desc.get('text', '')}\n"
        if profile.get("relationships"):
            for rel in profile["relationships"]:
                profile_text += f"  Relationship: {rel.get('text', '')}\n"
        if profile.get("co_occurring_entities"):
            co_names = [f"{r.get('entity_name', '')} ({r.get('entity_type', '')})" for r in profile["co_occurring_entities"][:15]]
            profile_text += f"  Co-occurring entities: {', '.join(co_names)}\n"
        if profile.get("documents"):
            doc_paths = [r.get("path", "") for r in profile["documents"][:10]]
            profile_text += f"  Found in documents: {', '.join(doc_paths)}\n"
        profile_text += "</entity_profile>"
        sections["entity_profiles"].append(profile_text)
        section_tokens["entity_profiles"] += _estimate_tokens(profile_text)

    # 3. Graph traversal results
    for traversal in ctx.graph_traversal_results:
        trav_text = f"<graph_traversal type=\"{traversal.get('traversal_type', '')}\" params=\"{traversal.get('params', {})}\">\n"
        for record in traversal.get("records", [])[:30]:
            trav_text += f"  {record}\n"
        trav_text += "</graph_traversal>"
        sections["graph_traversal"].append(trav_text)
        section_tokens["graph_traversal"] += _estimate_tokens(trav_text)

    # 4. GraphRAG artifacts (structured knowledge)
    graphrag_types = {"entity_description", "relationship", "claim", "community_report"}
    for item in unique_evidence:
        if item.evidence_type in graphrag_types:
            formatted = _format_evidence(item, len(sections["graphrag_artifacts"]))
            tokens = _estimate_tokens(formatted)
            sections["graphrag_artifacts"].append(formatted)
            section_tokens["graphrag_artifacts"] += tokens

    # 5. Text evidence (chunks + all other text-bearing artifact types)
    # Catch-all: anything not already routed to taxonomy or graphrag_artifacts
    taxonomy_types = {"taxonomy_first_bucket", "taxonomy_sub_bucket", "taxonomy_bucket_card", "taxonomy_domain_profile"}
    handled_types = taxonomy_types | graphrag_types
    for item in unique_evidence:
        if item.evidence_type not in handled_types:
            formatted = _format_evidence(item, len(sections["text_chunks"]))
            tokens = _estimate_tokens(formatted)
            sections["text_chunks"].append(formatted)
            section_tokens["text_chunks"] += tokens

    # 6. Temporal context
    if ctx.inferred_temporal_range.get("is_temporal"):
        temporal_note = (
            f"<temporal_context>\n"
            f"  Query has temporal scope: {ctx.inferred_temporal_range.get('date_from', 'unknown')} "
            f"to {ctx.inferred_temporal_range.get('date_to', 'unknown')}\n"
            f"  Prefer evidence within this date range.\n"
            f"</temporal_context>"
        )
        sections["temporal"].append(temporal_note)
        section_tokens["temporal"] += _estimate_tokens(temporal_note)

    # Assemble in priority order, respecting token budget
    total_tokens = 0
    context_parts = []

    # Intent-aware priority ordering
    if ctx.intent == "broad_overview":
        # Overview queries: lead with community reports/domain profiles for structural framing
        section_order = ["graphrag_artifacts", "taxonomy", "graph_traversal", "entity_profiles", "text_chunks", "temporal"]
    else:
        section_order = ["taxonomy", "entity_profiles", "graph_traversal", "graphrag_artifacts", "text_chunks", "temporal"]

    for section_name in section_order:
        for piece in sections[section_name]:
            piece_tokens = _estimate_tokens(piece)
            if total_tokens + piece_tokens > max_tokens:
                log.info("Context budget reached at %d tokens (section: %s)", total_tokens, section_name)
                break
            context_parts.append(piece)
            total_tokens += piece_tokens

    ctx.assembled_context = "\n\n".join(context_parts)
    ctx.context_token_count = total_tokens

    yield events.context_assembled(
        token_count=total_tokens,
        section_breakdown={k: v for k, v in section_tokens.items() if v > 0},
    )

    yield events.thinking(f"Context assembled: {total_tokens} tokens across {len(context_parts)} evidence items")

    ctx.current_state = AgentState.GENERATE
