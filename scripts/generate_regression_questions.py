#!/usr/bin/env python3
"""
Generate 5 × 2500 regression evaluation questions from actual corpus content.

Reads chunks from Elasticsearch, groups by document/domain, then calls
LLM to extract factual questions grounded in the real text. Each
question includes expected answer terms and source paths for automated
scoring.

Requirements:
  - Elasticsearch running with ingested chunks (kb_chunks)
  - LLM endpoint reachable (OPENAI_BASE_URL + OPENAI_API_KEY)

Usage:
    python scripts/generate_regression_questions.py \\
      --output-dir evals/v2/regression/ \\
      --sets 5 --per-set 2500 \\
      --es-url http://localhost:9200 \\
      --concurrency 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# ── Paths ───────────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent
DOCS_PATH = BASE / "kb_artifacts_full_promo_calibrated_run3" / "documents.jsonl"
RULES_PATH = BASE / "rules_active.json"

# ── ES config ───────────────────────────────────────────────────────────────
ES_CHUNK_INDEX = "kb_chunks"
ES_SCROLL_SIZE = 500

# ── Category targets per set (2500 total) ───────────────────────────────────
CATEGORY_TARGETS = {
    "factual_lookup": 400,
    "procedure_steps": 250,
    "troubleshooting": 200,
    "entity_centric": 300,
    "temporal": 200,
    "cross_domain_synthesis": 150,
    "comparison": 150,
    "taxonomy_navigation": 100,
    "graph_traversal": 150,
    "document_lookup": 100,
    "multi_tool_chain": 100,
    "adversarial_no_answer": 200,
    "ownership_role": 100,
    "definition": 100,
}

VALID_CATEGORIES = set(CATEGORY_TARGETS.keys())

# Categories the LLM should generate
LLM_CATEGORIES = [
    "factual_lookup", "procedure_steps", "troubleshooting",
    "entity_centric", "temporal", "cross_domain_synthesis",
    "comparison", "graph_traversal", "document_lookup",
    "multi_tool_chain", "ownership_role", "definition",
]

# ── LLM prompt ──────────────────────────────────────────────────────────────
QUESTION_EXTRACTION_PROMPT = """You are an evaluation question generator for a retrieval-augmented IT knowledge base.

Given these text passages from an enterprise IT document corpus, generate factual questions that can be answered from the text.

For EACH passage, generate 2-4 questions of DIFFERENT types chosen from:
- factual_lookup: Direct fact questions ("What is X?", "Where is Y documented?", "What does Z refer to?")
- procedure_steps: Process/how-to questions ("How do you X?", "What are the steps for Y?", "What is the procedure for Z?")
- troubleshooting: Problem-diagnosis questions ("How do you fix X?", "What causes Y?", "What should you do if Z fails?")
- entity_centric: Person/system/vendor questions ("What does [name] manage?", "Who is responsible for X?", "What role does [person] play?")
- temporal: Time-based questions ("When was X implemented?", "What changed in [year]?", "What is the timeline for X?")
- cross_domain_synthesis: Questions spanning multiple topics ("How does X relate to Y across departments?")
- comparison: Comparative questions ("How does X differ from Y?", "Compare X and Y approaches")
- graph_traversal: Relationship questions ("How is X connected to Y?", "What systems depend on X?")
- document_lookup: Full document questions ("What is the complete content of this document about?", "Summarize the key points in this document")
- multi_tool_chain: Complex questions requiring multiple lookups ("What does [person] manage and how does it connect to [system]?")
- ownership_role: Ownership/responsibility questions ("Who owns X?", "What team manages Y?")
- definition: Definition questions ("What is X defined as?", "Define Y in this organization's context")

IMPORTANT RULES:
1. Questions MUST be answerable from the provided text — do not invent facts
2. answer_terms MUST be specific terms/phrases that appear in the passage text
3. Each question should be unique and test a different fact
4. Vary question phrasing — don't repeat the same template
5. Include the document path in must_cite_paths when the question is about a specific document
6. For entity_centric questions, include the entity name in answer_terms
7. For temporal questions, include the date/year in answer_terms

Return a JSON array of objects:
```json
[
  {
    "question": "What network speed was verified during NDT testing?",
    "category": "factual_lookup",
    "answer_terms": ["900", "Mb/s", "NDT"],
    "forbidden_terms": [],
    "must_cite_paths": ["path/to/source.docx"],
    "difficulty": "easy",
    "source_excerpt": "NDT tests verified 900+ Mb/s throughput on the backbone."
  }
]
```

Passages:
"""

# ── Adversarial question templates (no LLM needed) ─────────────────────────
ADVERSARIAL_TEMPLATES = [
    "What is {fake_entity}'s role in the organization's quantum computing operations?",
    "Describe the {fake_entity} deployment at the organization's Mars rover facility.",
    "What cryptocurrency mining procedures does the organization follow?",
    "How does the organization handle {fake_entity} integration with their teleportation system?",
    "What are the steps for deploying {fake_entity} on the organization's space station module?",
    "Who manages the organization's underwater data center?",
    "What is the organization's policy on employee time travel research?",
    "Describe the neural interface system used by the organization's network engineers.",
    "What cloud gaming infrastructure does the organization maintain for lab scientists?",
    "How does {fake_entity} connect to the organization's weather control system?",
    "What procedures exist for the organization's holographic display maintenance?",
    "Describe the organization's agreement with {fake_entity} for lunar data storage.",
    "What is the organization's autonomous vehicle fleet management system?",
    "How does the organization use {fake_entity} for DNA sequencing in the data center?",
    "What are the organization's procedures for quantum-encrypted blockchain audits?",
    "Describe the {fake_entity} satellite uplink at the main building.",
    "What virtual reality training system does the IT team use?",
    "How is {fake_entity} integrated with the organization's robotic maintenance drones?",
    "What are the organization's guidelines for AI-generated art in technical documentation?",
    "Describe the {fake_entity} warp drive simulation running on the organization's servers.",
    "What is the organization's food delivery drone network configuration?",
    "How does the organization handle {fake_entity} social media moderation?",
    "What smart contract systems run on the organization's network?",
    "Describe the organization's procedures for managing {fake_entity} NFT assets.",
    "What augmented reality wayfinding system does the main building use?",
    "How does {fake_entity} power the organization's anti-gravity research computing?",
    "What are the organization's protocols for communicating with extraterrestrial signals?",
    "Describe the {fake_entity} deepfake detection system at the organization.",
    "What metaverse presence does the IT team maintain?",
    "How is {fake_entity} used in the organization's fusion reactor control systems?",
]

FAKE_ENTITIES = [
    "QuantumLeap", "NeuraStar", "CyberNexus", "DataForge Prime",
    "SkyNet Solutions", "BioSync", "HoloTech Industries", "ZetaCloud",
    "OmniCorp", "TechnoVerse", "NebulaNet", "CryptoGuard Elite",
    "Galactic Systems", "PhantomWare", "Aethernet", "VoidCompute",
    "TerraNode", "Synthex", "MegaFrame", "PulseNet",
    "HyperGrid", "QuantumForge", "NeoLogic", "StarBridge Systems",
    "InfiniteStack", "GhostProtocol", "MatrixCore", "PlasmaNet",
    "ChronoSync", "FluxEngine",
]

# ── Taxonomy navigation templates (deterministic) ──────────────────────────
TAXONOMY_TEMPLATES = [
    "What topics fall under the {domain_name} domain in the knowledge base?",
    "List the document categories within the {bucket} bucket.",
    "What types of content are classified under {domain_name}?",
    "Navigate to the {bucket} taxonomy area — what sub-buckets exist?",
    "How are {domain_name} documents organized in the taxonomy?",
    "What is the hierarchical structure of the {bucket} category?",
    "Show the taxonomy path for documents in the {bucket} bucket.",
    "What domains relate to {topic}?",
    "Which taxonomy buckets contain information about {topic}?",
    "Describe the classification structure for {domain_name} content.",
]


# ── ES helpers ──────────────────────────────────────────────────────────────
def fetch_all_chunks(es_url: str) -> List[Dict[str, Any]]:
    """Scroll through all chunks in ES and return them."""
    url = f"{es_url}/{ES_CHUNK_INDEX}/_search?scroll=2m"
    body = {"size": ES_SCROLL_SIZE, "query": {"match_all": {}},
            "_source": ["chunk_id", "doc_id", "path", "text",
                        "semantic_domain", "first_bucket", "sub_bucket",
                        "mtime_date", "effective_date", "chunk_index"]}
    headers = {"Content-Type": "application/json"}

    resp = requests.post(url, json=body, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    scroll_id = data["_scroll_id"]
    hits = [h["_source"] for h in data["hits"]["hits"]]
    total = data["hits"]["total"]["value"]
    print(f"  ES total chunks: {total}")

    while len(hits) < total:
        resp = requests.post(
            f"{es_url}/_search/scroll",
            json={"scroll": "2m", "scroll_id": scroll_id},
            headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        batch = [h["_source"] for h in data["hits"]["hits"]]
        if not batch:
            break
        hits.extend(batch)
        scroll_id = data["_scroll_id"]

    # Clean up scroll
    try:
        requests.delete(f"{es_url}/_search/scroll",
                        json={"scroll_id": scroll_id},
                        headers=headers, timeout=10)
    except Exception:
        pass

    print(f"  Fetched {len(hits)} chunks")
    return hits


def group_chunks_by_doc(chunks: List[Dict]) -> Dict[str, List[Dict]]:
    """Group chunks by document path."""
    by_doc: Dict[str, List[Dict]] = defaultdict(list)
    for c in chunks:
        by_doc[c.get("path", "unknown")].append(c)
    # Sort chunks within each doc by chunk_index
    for path in by_doc:
        by_doc[path].sort(key=lambda x: x.get("chunk_index", 0))
    return dict(by_doc)


def group_chunks_by_domain(chunks: List[Dict]) -> Dict[str, List[Dict]]:
    """Group chunks by semantic domain."""
    by_domain: Dict[str, List[Dict]] = defaultdict(list)
    for c in chunks:
        by_domain[c.get("semantic_domain", "unknown")].append(c)
    return dict(by_domain)


# ── LLM helpers ─────────────────────────────────────────────────────────────
def build_llm_prompt(chunk_batch: List[Dict]) -> str:
    """Build the LLM prompt for a batch of chunks."""
    prompt = QUESTION_EXTRACTION_PROMPT
    for i, chunk in enumerate(chunk_batch, 1):
        path = chunk.get("path", "unknown")
        domain = chunk.get("semantic_domain", "unknown")
        bucket = chunk.get("first_bucket", "unknown")
        date = chunk.get("mtime_date", "") or chunk.get("effective_date", "")
        text = chunk.get("text", "")[:3000]  # Cap text length
        prompt += f"\n---\n[PASSAGE {i} - path: {path}, domain: {domain}, bucket: {bucket}, date: {date}]\n{text}\n"
    prompt += "\n---\n\nReturn ONLY a valid JSON array. No markdown, no explanation."
    return prompt


def call_llm(prompt: str, base_url: str, api_key: str, model: str,
             max_retries: int = 3) -> str:
    """Call LLM and return text response."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an evaluation question generator. Return only valid JSON arrays."},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": 4000,
        "temperature": 0.7,
        "user": api_key,
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            if resp.status_code < 300:
                data = resp.json()
                # Extract text using OpenAI-compatible response format
                text = _extract_text(data)
                return text
            if resp.status_code in {408, 429, 500, 502, 503, 504} and attempt < max_retries:
                time.sleep(2 * attempt)
                continue
            # 400-level: try removing unsupported params
            if resp.status_code == 400 and attempt < max_retries:
                err = resp.text.lower()
                if "temperature" in err:
                    payload.pop("temperature", None)
                    continue
            raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:300]}")
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 * attempt)
                continue
            raise

    return ""


def _extract_text(resp_obj: Dict[str, Any]) -> str:
    """Extract text from OpenAI-compatible response (multiple formats)."""
    direct = resp_obj.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    choices = resp_obj.get("choices")
    if not isinstance(choices, list) or not choices:
        output = resp_obj.get("output")
        if isinstance(output, list):
            parts = []
            for item in output:
                if isinstance(item, dict):
                    c = item.get("content")
                    if isinstance(c, str) and c.strip():
                        parts.append(c.strip())
            return "\n".join(parts)
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    msg = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text") or item.get("content")
                if isinstance(t, str) and t.strip():
                    parts.append(t)
        return "\n".join(parts)
    return str(first.get("text", "")).strip()


def parse_llm_questions(text: str) -> List[Dict[str, Any]]:
    """Parse JSON array of questions from LLM response text."""
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines if they are fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "questions" in result:
            return result["questions"]
        return []
    except json.JSONDecodeError:
        # Try to find JSON array in the text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return []


# ── Question record builder ────────────────────────────────────────────────
def make_q(qid: str, category: str, question: str,
           domain_hint: str = "", source_path_hint: str = "",
           must_cite_paths: Optional[List[str]] = None,
           must_include_terms: Optional[List[str]] = None,
           must_not_include_terms: Optional[List[str]] = None,
           expected_intent: Optional[str] = None,
           min_citations: int = 1, difficulty: str = "medium",
           requires_graph_traversal: bool = False,
           requires_temporal: bool = False,
           source_excerpt: str = "") -> Dict[str, Any]:
    return {
        "id": qid,
        "category": category,
        "question": question,
        "domain_hint": domain_hint,
        "source_path_hint": source_path_hint or "",
        "expected": {
            "must_cite_paths": must_cite_paths or [],
            "must_include_terms": must_include_terms or [],
            "must_not_include_terms": must_not_include_terms or [],
            "expected_intent": expected_intent or category,
            "min_citations": min_citations,
        },
        "difficulty": difficulty,
        "requires_graph_traversal": requires_graph_traversal,
        "requires_temporal": requires_temporal,
        "source_excerpt": source_excerpt,
    }


def question_hash(q: str) -> str:
    """Deterministic hash for dedup."""
    return hashlib.sha256(q.lower().strip().encode()).hexdigest()[:16]


# ── Generate LLM-based questions from chunks ───────────────────────────────
def generate_llm_questions(
    chunks: List[Dict],
    base_url: str,
    api_key: str,
    model: str,
    target_count: int,
    concurrency: int = 5,
    batch_size: int = 5,
) -> List[Dict[str, Any]]:
    """Generate questions by calling LLM on chunk batches."""

    # Group by domain for balanced sampling
    by_domain = group_chunks_by_domain(chunks)
    domains = sorted(by_domain.keys())

    # Build batches: sample across domains for diversity
    batches: List[List[Dict]] = []
    # First pass: within-domain batches
    for domain in domains:
        domain_chunks = by_domain[domain]
        random.shuffle(domain_chunks)
        for i in range(0, len(domain_chunks), batch_size):
            batch = domain_chunks[i:i + batch_size]
            if len(batch) >= 2:  # Need at least 2 chunks per batch
                batches.append(batch)

    # Second pass: cross-domain batches for synthesis/comparison questions
    all_shuffled = list(chunks)
    random.shuffle(all_shuffled)
    for i in range(0, len(all_shuffled), batch_size):
        batch = all_shuffled[i:i + batch_size]
        # Only add if batch spans 2+ domains
        domains_in_batch = set(c.get("semantic_domain", "") for c in batch)
        if len(domains_in_batch) >= 2 and len(batch) >= 2:
            batches.append(batch)

    random.shuffle(batches)

    # We need roughly target_count / 2.5 batches (each batch yields ~2.5 Qs)
    needed_batches = int(target_count / 2.0) + 100  # overshoot for dedup losses
    batches = batches[:needed_batches]

    print(f"  Processing {len(batches)} chunk batches (target: {target_count} questions)...")

    questions: List[Dict[str, Any]] = []
    seen_hashes: Set[str] = set()
    errors = 0

    def process_batch(batch_idx: int, batch: List[Dict]) -> List[Dict[str, Any]]:
        prompt = build_llm_prompt(batch)
        try:
            text = call_llm(prompt, base_url, api_key, model)
            raw_qs = parse_llm_questions(text)
        except Exception as exc:
            print(f"    Batch {batch_idx}: LLM error: {exc}")
            return []

        result = []
        for rq in raw_qs:
            if not isinstance(rq, dict) or "question" not in rq:
                continue
            category = rq.get("category", "factual_lookup")
            if category not in VALID_CATEGORIES:
                category = "factual_lookup"
            # Find the source chunk for this question
            source_path = ""
            source_domain = ""
            if rq.get("must_cite_paths"):
                source_path = rq["must_cite_paths"][0] if isinstance(rq["must_cite_paths"], list) else ""
            if not source_path and batch:
                source_path = batch[0].get("path", "")
            if batch:
                source_domain = batch[0].get("semantic_domain", "")

            result.append({
                "question": rq["question"],
                "category": category,
                "answer_terms": rq.get("answer_terms", []),
                "forbidden_terms": rq.get("forbidden_terms", []),
                "must_cite_paths": rq.get("must_cite_paths", [source_path] if source_path else []),
                "difficulty": rq.get("difficulty", "medium"),
                "source_excerpt": rq.get("source_excerpt", ""),
                "domain_hint": source_domain,
                "source_path_hint": source_path,
            })
        return result

    # Process batches with concurrency
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {}
        for i, batch in enumerate(batches):
            futures[executor.submit(process_batch, i, batch)] = i

        completed = 0
        for future in as_completed(futures):
            completed += 1
            try:
                batch_qs = future.result()
                for bq in batch_qs:
                    h = question_hash(bq["question"])
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        questions.append(bq)
            except Exception as exc:
                errors += 1
                print(f"    Batch error: {exc}")

            if completed % 50 == 0:
                print(f"    Progress: {completed}/{len(batches)} batches, {len(questions)} questions generated")

            # Stop early if we have enough
            if len(questions) >= target_count * 1.2:
                # Cancel remaining futures
                for f in futures:
                    f.cancel()
                break

    print(f"  Generated {len(questions)} unique questions ({errors} errors)")
    return questions


# ── Generate adversarial questions (no LLM) ─────────────────────────────────
def generate_adversarial(count: int, seed: int) -> List[Dict[str, Any]]:
    """Generate adversarial no-answer questions deterministically."""
    rng = random.Random(seed)
    questions = []
    seen = set()

    for i in range(count * 3):  # Overshoot for dedup
        if len(questions) >= count:
            break
        tpl = rng.choice(ADVERSARIAL_TEMPLATES)
        entity = rng.choice(FAKE_ENTITIES)
        q_text = tpl.format(fake_entity=entity)
        h = question_hash(q_text)
        if h in seen:
            continue
        seen.add(h)
        questions.append({
            "question": q_text,
            "category": "adversarial_no_answer",
            "answer_terms": [],
            "forbidden_terms": [],
            "must_cite_paths": [],
            "difficulty": "hard",
            "source_excerpt": "",
            "domain_hint": "",
            "source_path_hint": "",
        })

    return questions[:count]


# ── Generate taxonomy navigation questions (no LLM) ────────────────────────
def generate_taxonomy_nav(rules: Dict, count: int, seed: int) -> List[Dict[str, Any]]:
    """Generate taxonomy navigation questions from rules_active.json."""
    rng = random.Random(seed)
    questions = []
    seen = set()

    domains = rules.get("domains", [])
    for i in range(count * 3):
        if len(questions) >= count:
            break
        dom = rng.choice(domains)
        dom_id = dom.get("id", "unknown")
        dom_name = dom.get("name", dom_id)
        kws = dom.get("keywords", [])
        topic = rng.choice(kws) if kws else dom_name
        buckets = dom.get("first_buckets", [])
        bucket = rng.choice(buckets) if buckets else dom_id

        tpl = rng.choice(TAXONOMY_TEMPLATES)
        q_text = tpl.format(domain_name=dom_name, bucket=bucket, topic=topic)
        h = question_hash(q_text)
        if h in seen:
            continue
        seen.add(h)
        questions.append({
            "question": q_text,
            "category": "taxonomy_navigation",
            "answer_terms": [dom_name.lower().split()[0]] if dom_name else [],
            "forbidden_terms": [],
            "must_cite_paths": [],
            "difficulty": "medium",
            "source_excerpt": "",
            "domain_hint": dom_id,
            "source_path_hint": "",
        })

    return questions[:count]


# ── Balance categories ──────────────────────────────────────────────────────
def balance_categories(
    llm_questions: List[Dict],
    adversarial: List[Dict],
    taxonomy: List[Dict],
    per_set: int,
) -> List[Dict]:
    """Balance questions to match category targets."""
    by_cat: Dict[str, List[Dict]] = defaultdict(list)

    for q in llm_questions:
        by_cat[q["category"]].append(q)
    for q in adversarial:
        by_cat["adversarial_no_answer"].append(q)
    for q in taxonomy:
        by_cat["taxonomy_navigation"].append(q)

    result: List[Dict] = []
    for cat, target in CATEGORY_TARGETS.items():
        available = by_cat.get(cat, [])
        if len(available) >= target:
            result.extend(available[:target])
        else:
            # Take all available, then fill from overflow of other categories
            result.extend(available)
            deficit = target - len(available)
            if deficit > 0:
                # Try to fill from factual_lookup overflow (largest category)
                overflow_cat = "factual_lookup" if cat != "factual_lookup" else "entity_centric"
                overflow = by_cat.get(overflow_cat, [])
                remaining = overflow[CATEGORY_TARGETS.get(overflow_cat, 0):]
                for q in remaining[:deficit]:
                    q_copy = dict(q)
                    q_copy["category"] = cat
                    result.append(q_copy)

    # Trim to per_set
    return result[:per_set]


# ── Assign to sets ──────────────────────────────────────────────────────────
def assign_to_sets(
    all_questions: List[Dict],
    num_sets: int,
    per_set: int,
    global_seen: Set[str],
) -> List[List[Dict]]:
    """Assign questions to sets with cross-set dedup."""
    # Group by category
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for q in all_questions:
        h = question_hash(q["question"])
        if h not in global_seen:
            global_seen.add(h)
            by_cat[q["category"]].append(q)

    sets: List[List[Dict]] = [[] for _ in range(num_sets)]

    # Round-robin by category
    for cat, qs in by_cat.items():
        random.shuffle(qs)
        for i, q in enumerate(qs):
            set_idx = i % num_sets
            if len(sets[set_idx]) < per_set:
                sets[set_idx].append(q)

    return sets


# ── Finalize question records ───────────────────────────────────────────────
def finalize_questions(
    raw_questions: List[Dict],
    set_idx: int,
) -> List[Dict[str, Any]]:
    """Convert raw questions to final JSONL format with IDs."""
    counters: Dict[str, int] = defaultdict(int)
    result = []

    for q in raw_questions:
        cat = q["category"]
        counters[cat] += 1
        qid = f"reg{set_idx}_{cat}_{counters[cat]:04d}"

        # Map category to expected intent
        intent_map = {
            "factual_lookup": "factual_lookup",
            "procedure_steps": "procedure_steps",
            "troubleshooting": "troubleshooting",
            "entity_centric": "factual_lookup",
            "temporal": "timeline_version",
            "cross_domain_synthesis": "synthesis",
            "comparison": "comparison",
            "taxonomy_navigation": "factual_lookup",
            "graph_traversal": "factual_lookup",
            "document_lookup": "factual_lookup",
            "multi_tool_chain": "synthesis",
            "adversarial_no_answer": "factual_lookup",
            "ownership_role": "ownership_role",
            "definition": "definition",
        }

        result.append(make_q(
            qid=qid,
            category=cat,
            question=q["question"],
            domain_hint=q.get("domain_hint", ""),
            source_path_hint=q.get("source_path_hint", ""),
            must_cite_paths=q.get("must_cite_paths"),
            must_include_terms=q.get("answer_terms"),
            must_not_include_terms=q.get("forbidden_terms"),
            expected_intent=intent_map.get(cat, cat),
            min_citations=0 if cat == "adversarial_no_answer" else 1,
            difficulty=q.get("difficulty", "medium"),
            requires_graph_traversal=cat in ("graph_traversal", "entity_centric"),
            requires_temporal=cat == "temporal",
            source_excerpt=q.get("source_excerpt", ""),
        ))

    return result


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Generate regression question sets from ES chunks via LLM")
    parser.add_argument("--output-dir", default="evals/v2/regression/",
                        help="Output directory for JSONL files")
    parser.add_argument("--sets", type=int, default=5,
                        help="Number of question sets to generate")
    parser.add_argument("--per-set", type=int, default=2500,
                        help="Questions per set")
    parser.add_argument("--es-url", default=os.getenv("ES_URL", "http://localhost:9200"),
                        help="Elasticsearch URL")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Concurrent LLM calls")
    parser.add_argument("--batch-size", type=int, default=5,
                        help="Chunks per LLM batch")
    args = parser.parse_args()

    # Load LLM config from env
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("CHAT_MODEL", "gpt-4.1").strip()
    if not base_url or not api_key:
        print("ERROR: OPENAI_BASE_URL and OPENAI_API_KEY must be set in environment")
        sys.exit(1)

    # Load rules for taxonomy questions
    print("Loading rules...")
    with open(RULES_PATH, "r") as f:
        rules = json.load(f)

    # Fetch all chunks from ES
    print("Fetching chunks from Elasticsearch...")
    chunks = fetch_all_chunks(args.es_url)
    if not chunks:
        print("ERROR: No chunks found in ES. Run 'make ingest-full' first.")
        sys.exit(1)

    # Calculate total questions needed (with overshoot for dedup losses)
    total_target = args.sets * args.per_set
    # LLM categories need: total - adversarial - taxonomy across all sets
    adversarial_total = args.sets * CATEGORY_TARGETS["adversarial_no_answer"]
    taxonomy_total = args.sets * CATEGORY_TARGETS["taxonomy_navigation"]
    llm_target = total_target - adversarial_total - taxonomy_total
    # Overshoot by 30% for dedup and category balancing
    llm_target_with_overshoot = int(llm_target * 1.3)

    # Generate LLM questions using different chunk sampling per seed
    all_llm_questions: List[Dict] = []
    global_seen: Set[str] = set()

    for set_idx in range(1, args.sets + 1):
        seed = 1000 * set_idx + set_idx
        random.seed(seed)
        print(f"\n{'='*60}")
        print(f"Generating questions for set {set_idx} (seed={seed})...")
        print(f"{'='*60}")

        # Shuffle chunks with this seed for different sampling
        shuffled_chunks = list(chunks)
        random.shuffle(shuffled_chunks)

        # Generate LLM questions from shuffled chunks
        set_llm_target = int(llm_target_with_overshoot / args.sets)
        llm_qs = generate_llm_questions(
            shuffled_chunks, base_url, api_key, model,
            target_count=set_llm_target,
            concurrency=args.concurrency,
            batch_size=args.batch_size,
        )

        # Generate adversarial questions
        adv_qs = generate_adversarial(
            CATEGORY_TARGETS["adversarial_no_answer"], seed)

        # Generate taxonomy questions
        tax_qs = generate_taxonomy_nav(
            rules, CATEGORY_TARGETS["taxonomy_navigation"], seed)

        # Balance categories
        balanced = balance_categories(llm_qs, adv_qs, tax_qs, args.per_set)
        random.shuffle(balanced)

        # Finalize with IDs
        final = finalize_questions(balanced, set_idx)

        # Write output
        os.makedirs(args.output_dir, exist_ok=True)
        output_path = os.path.join(args.output_dir, f"set_{set_idx}.jsonl")
        with open(output_path, "w") as f:
            for q in final:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")

        # Stats
        cat_counts = defaultdict(int)
        for q in final:
            cat_counts[q["category"]] += 1

        print(f"\n  Set {set_idx}: {len(final)} questions written to {output_path}")
        print(f"  Category distribution:")
        for cat in sorted(cat_counts.keys()):
            target = CATEGORY_TARGETS.get(cat, 0)
            print(f"    {cat:30s} {cat_counts[cat]:4d} / {target:4d}")

    print(f"\n{'='*60}")
    print(f"DONE: {args.sets} sets generated in {args.output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
