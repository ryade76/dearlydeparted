#!/usr/bin/env python3
"""
Dedicated GraphRAG indexing stage.

This stage is intentionally separated from the metadata/vector ingest worker:
- reads document/chunk records from Elasticsearch
- builds GraphRAG retrieval artifacts
- optionally enriches with taxonomy artifacts from router.json + bucket_cards.jsonl
- upserts taxonomy nodes/links in Neo4j
- embeds and indexes artifacts into Elasticsearch + Qdrant
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk as es_bulk
from elasticsearch.helpers import scan as es_scan
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import networkx as nx
except Exception:
    nx = None  # type: ignore[assignment]

try:
    from services.chat_provider import ChatProvider
except Exception:
    ChatProvider = None  # type: ignore[assignment]

LOGGER = logging.getLogger("graphrag_index")

DEFAULT_EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_DISABLE_TASK = str(os.getenv("EMBED_DISABLE_TASK", "1")).strip().lower() in {"1", "true", "yes", "on"}
EMBED_DISABLE_DIMENSIONS = str(os.getenv("EMBED_DISABLE_DIMENSIONS", "1")).strip().lower() in {"1", "true", "yes", "on"}
_EMBED_COMPAT_OMIT_TASK = EMBED_DISABLE_TASK
_EMBED_COMPAT_OMIT_DIMENSIONS = EMBED_DISABLE_DIMENSIONS
ENTITY_FIELDS: List[Tuple[str, str]] = [
    ("system", "systems"),
    ("vendor", "vendors"),
    ("project", "projects"),
    ("person", "people"),
    ("location", "locations"),
]
DEFAULT_LLM_ARTIFACT_FAMILIES = "entity_description,relationship,claim,community_report_domain,community_report_graph"
ALLOWED_LLM_ARTIFACT_FAMILIES: Set[str] = {
    "entity_description",
    "relationship",
    "claim",
    "community_report_domain",
    "community_report_graph",
}
DEFAULT_LLM_EXTRACTION_SYSTEM_PROMPT = (
    "You are a high-precision information extraction engine for enterprise documents. "
    "Return ONLY valid JSON with this schema: "
    "{chunks:[{chunk_id, entities:[{name,type,description}], "
    "relationships:[{source,target,relationship_type,description}], "
    "claims:[{subject,predicate,object,claim_type,status,date,evidence}]}]}. "
    "Use only facts grounded in the provided chunk text. "
    "Do not hallucinate, do not use markdown, and do not return prose outside JSON."
)
DEFAULT_LLM_COMMUNITY_SYSTEM_PROMPT = (
    "You are a GraphRAG community summarizer. "
    "Write grounded summaries from provided evidence only. "
    "Return only JSON: {summary:string,key_entities:array,key_relationships:array,key_claims:array,open_questions:array}."
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TROUBLESHOOT_SYMPTOM_HINTS = [
    "error",
    "failed",
    "failure",
    "problem",
    "issue",
    "outage",
    "degraded",
    "alarm",
    "not responding",
    "unavailable",
]
_TROUBLESHOOT_CAUSE_HINTS = [
    "because",
    "caused by",
    "due to",
    "root cause",
    "source of",
    "triggered by",
]
_TROUBLESHOOT_REMEDIATION_HINTS = [
    "fix",
    "fixed",
    "resolved",
    "resolution",
    "mitigation",
    "workaround",
    "patched",
    "restart",
    "reboot",
    "replace",
    "added",
    "removed",
    "set ",
    "configured",
]
_TROUBLESHOOT_OUTCOME_HINTS = [
    "resolved",
    "restored",
    "stable",
    "working",
    "cleared",
    "verified",
    "confirmed",
]
_LOG_ENTRY_TIMESTAMP_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+[A-Z]{2,5}\s+\d{4}\b"
)
_PROCEDURE_LINE_RE = re.compile(
    r"^\s*(?:\d{1,3}[.)]|[A-Z][.)]|step\s+\d+|phase\s+\d+|[-*])\s+",
    re.IGNORECASE,
)
_PROCEDURE_SENTENCE_HINT_RE = re.compile(
    r"\b(step|phase|procedure|process|workflow|then|next|before|after|finally|monitor|respond|alert|implement|perform)\b",
    re.IGNORECASE,
)
_COMPLIANCE_CONSTRAINT_RE = re.compile(
    r"\b(must|shall|required|requires?|only|never|always|prohibited|forbidden|not\s+allowed|exit\s+0)\b",
    re.IGNORECASE,
)
_RISK_SIGNAL_RE = re.compile(
    r"\b(risk|threat|indicator|signal|alert|anomal(?:y|ies)|error|fail(?:ed|ure|ing)?|blocked|outage|degraded|vulnerab(?:ility|le)|disk\s+full|/var)\b",
    re.IGNORECASE,
)
_RISK_MECHANISM_RE = re.compile(
    r"\b(script|monitor(?:ing)?|check(?:ing)?|detect(?:ion|ed)?|parse|report|logs?|query|scan|rotate|rotation|daily|hourly|weekly|every\s+\d+)\b",
    re.IGNORECASE,
)
_CONDITION_CLAUSE_RE = re.compile(
    r"\b(?:if|when|unless|before|after|during|while)\b[^.;]{0,180}",
    re.IGNORECASE,
)
_NEXT_STEP_CLAUSE_RE = re.compile(
    r"\b(?:then|next|after that|followed by|finally)\b[^.;]{0,180}",
    re.IGNORECASE,
)
_ACTOR_HINT_RE = re.compile(
    r"\b(?:worked with|contact(?:ed)?|notify|ask|by)\s+([A-Z][A-Za-z0-9_.-]{1,32}(?:\s+[A-Z][A-Za-z0-9_.-]{1,32}){0,2})\b"
)
_RISK_FREQUENCY_RE = re.compile(
    r"\b(?:hourly|daily|weekly|monthly|every\s+\d+\s*(?:min(?:ute)?s?|hours?|days?|weeks?|months?)|\d+\s*(?:x|times)\s*(?:per|a)\s*(?:hour|day|week|month))\b",
    re.IGNORECASE,
)
_RISK_LIMIT_RE = re.compile(
    r"(?:>=|<=|>|<|=)\s*\d+(?:\.\d+)?\s*[A-Za-z%/]*|\b\d+(?:\.\d+)?\s*(?:%|percent|mb|gb|tb|minutes?|hours?|days?)\b",
    re.IGNORECASE,
)
_RISK_ISSUE_HINT_RE = re.compile(
    r"\b(issue|problem|error|failed?|failure|outage|degraded|blocked|incident)\b",
    re.IGNORECASE,
)
_MAX_LOG_ENTRY_ARTIFACTS_PER_CHUNK = 14
_MAX_PROCEDURE_STEP_ARTIFACTS_PER_CHUNK = 8
_MAX_COMPLIANCE_CONSTRAINT_ARTIFACTS_PER_CHUNK = 8
_MAX_RISK_SIGNAL_ARTIFACTS_PER_CHUNK = 8
_MAX_INVENTORY_ROW_ARTIFACTS_PER_CHUNK = 10
_MAX_ACCOUNT_STATUS_ROW_ARTIFACTS_PER_CHUNK = 10
_PROCEDURE_REQUIREMENT_PREREQ_RE = re.compile(
    r"\b(prior to starting work|before starting work|must review[^.]{0,120}requirements?|review[^.]{0,120}prior to starting)\b",
    re.IGNORECASE,
)
_PROCEDURE_REQUIREMENT_SITE_RE = re.compile(
    r"\b(must be available at the job site|shall be available at the job site|available at the job site)\b",
    re.IGNORECASE,
)
_INVENTORY_ROW_FALLBACK_RE = re.compile(
    r"\b(?P<computer>[A-Za-z0-9._-]{2,})\s+"
    r"(?P<os>(?:Microsoft\s+Windows|Windows|Red\s+Hat|RHEL|Ubuntu|CentOS|Linux|macOS|Mac\s+OS(?:\s+X)?)[A-Za-z0-9 ._/-]{0,52})\s+"
    r"(?:(?:[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+)?"
    r"(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\b",
    re.IGNORECASE,
)
_ACCOUNT_STATUS_LABELS_RE = re.compile(
    r"Username:\s*(?P<username>[A-Za-z0-9._-]{2,})\b"
    r".{0,180}?\bStatus:\s*(?P<status>[A-Za-z][A-Za-z0-9 _/-]{1,32})\b"
    r".{0,220}?\bLocked Date:\s*(?P<locked>[A-Za-z0-9:/._-]{3,24})\b"
    r".{0,260}?\bService(?:\(s\)|s)?:\s*(?P<service>[^|;\n\r]{2,120})",
    re.IGNORECASE | re.DOTALL,
)
_ACCOUNT_STATUS_DELIMITED_RE = re.compile(
    r"\b(?P<username>[A-Za-z0-9._-]{2,})\s*[|,;]\s*"
    r"(?P<status>LOCKED|ACTIVE|DISABLED|ENABLED|EXPIRED)\s*[|,;]\s*"
    r"(?P<locked>\d{2}-[A-Z]{3}-\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z0-9:/._-]{3,24})\s*[|,;]\s*"
    r"(?P<service>[A-Za-z0-9._-]{2,})",
    re.IGNORECASE,
)


@dataclass
class ChunkDoc:
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str
    metadata: Dict[str, Any]


@dataclass
class GraphArtifact:
    artifact_id: str
    artifact_type: str
    text: str
    metadata: Dict[str, Any]
    vector: Optional[List[float]]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run dedicated GraphRAG indexing stage")
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--es-doc-index", default="kb_docs")
    ap.add_argument("--es-chunk-index", default="kb_chunks")
    ap.add_argument("--es-graphrag-index", default="kb_graphrag_artifacts")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--qdrant-graphrag-collection", default="kb_graphrag_artifacts_vec")
    ap.add_argument("--qdrant-timeout-sec", type=int, default=120)
    ap.add_argument(
        "--qdrant-upsert-batch-size",
        type=int,
        default=128,
        help="Points per Qdrant upsert request when writing GraphRAG vectors",
    )
    ap.add_argument(
        "--qdrant-upsert-wait",
        action="store_true",
        default=True,
        help="Wait for Qdrant write acknowledgement on each upsert (default: true)",
    )
    ap.add_argument(
        "--no-qdrant-upsert-wait",
        dest="qdrant_upsert_wait",
        action="store_false",
        help="Do not wait for Qdrant write acknowledgement on each upsert (faster backfill)",
    )
    ap.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="change_me_local")
    ap.add_argument("--embed-url", default="http://127.0.0.1:8001/v1/embeddings")
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--vector-dim", type=int, default=4096)
    ap.add_argument("--embed-timeout-sec", type=int, default=600)
    ap.add_argument("--embed-batch-size", type=int, default=16)
    ap.add_argument("--embed-max-attempts", type=int, default=3)
    ap.add_argument("--embed-retry-backoff-sec", type=float, default=3.0)
    ap.add_argument(
        "--community-method",
        default="auto",
        choices=["auto", "louvain", "components", "domain"],
        help="Graph community strategy for global reports (auto prefers louvain when available)",
    )
    ap.add_argument("--community-resolution", type=float, default=1.0, help="Louvain resolution parameter")
    ap.add_argument("--community-min-shared-entities", type=int, default=1, help="Minimum shared entities to connect docs")
    ap.add_argument("--community-min-docs", type=int, default=3, help="Minimum docs required for a graph community report")
    ap.add_argument(
        "--community-max-reports",
        type=int,
        default=250,
        help="Max number of graph community reports to materialize",
    )
    ap.add_argument("--skip-graph-communities", action="store_true")
    ap.add_argument("--enable-llm-graphrag", action="store_true", help="Enable additive LLM extraction artifacts")
    ap.add_argument(
        "--llm-artifact-families",
        default=DEFAULT_LLM_ARTIFACT_FAMILIES,
        help="Comma-separated subset of: entity_description,relationship,claim,community_report_domain,community_report_graph",
    )
    ap.add_argument("--llm-extraction-batch-size", type=int, default=3, help="Number of chunks per LLM extraction call")
    ap.add_argument("--llm-max-chunks", type=int, default=0, help="Optional cap on chunks processed by LLM extraction")
    ap.add_argument("--llm-max-chars-per-chunk", type=int, default=1800, help="Max chunk chars sent to LLM extraction")
    ap.add_argument("--llm-max-entities-per-chunk", type=int, default=8)
    ap.add_argument("--llm-max-relationships-per-chunk", type=int, default=8)
    ap.add_argument("--llm-max-claims-per-chunk", type=int, default=8)
    ap.add_argument("--llm-max-entities-total", type=int, default=0, help="Max LLM entity artifacts (<=0 means unlimited)")
    ap.add_argument(
        "--llm-max-relationships-total",
        type=int,
        default=0,
        help="Max LLM relationship artifacts (<=0 means unlimited)",
    )
    ap.add_argument("--llm-max-claims-total", type=int, default=0, help="Max LLM claim artifacts (<=0 means unlimited)")
    ap.add_argument(
        "--llm-max-gleanings",
        type=int,
        default=1,
        help="Number of additive extraction passes per chunk batch (GraphRAG-style gleaning)",
    )
    ap.add_argument(
        "--llm-community-max-domains",
        type=int,
        default=0,
        help="Max domain community summaries to build (<=0 means unlimited)",
    )
    ap.add_argument("--llm-community-max-evidence-items", type=int, default=80)
    ap.add_argument("--llm-extraction-system-prompt-file", default="", help="Optional system prompt file for chunk extraction")
    ap.add_argument("--llm-community-system-prompt-file", default="", help="Optional system prompt file for domain community summaries")
    ap.add_argument("--llm-temperature", type=float, default=0.0)
    ap.add_argument("--llm-top-p", type=float, default=0.1)
    ap.add_argument("--llm-max-completion-tokens", type=int, default=3000)
    ap.add_argument(
        "--skip-existing-vectors",
        action="store_true",
        default=True,
        help="When writing Qdrant vectors, skip artifacts that already exist in the target collection (default: on)",
    )
    ap.add_argument(
        "--no-skip-existing-vectors",
        dest="skip_existing_vectors",
        action="store_false",
        help="Disable skipping and force re-embedding/upsert for all artifacts",
    )
    ap.add_argument("--router-taxonomy-json", default="")
    ap.add_argument("--bucket-cards-jsonl", default="")
    ap.add_argument("--skip-taxonomy-neo4j", action="store_true")
    ap.add_argument("--taxonomy-only", action="store_true", help="Only index taxonomy artifacts + Neo4j taxonomy graph")
    ap.add_argument("--skip-es", action="store_true")
    ap.add_argument("--skip-qdrant", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-docs", type=int, default=0)
    ap.add_argument("--max-chunks", type=int, default=0)
    ap.add_argument("--max-artifacts", type=int, default=0)
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def norm_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None:
        return []
    txt = str(value).strip()
    return [txt] if txt else []


def cap_limit(value: int) -> Optional[int]:
    """Return a positive cap, or None to indicate unlimited."""
    try:
        v = int(value)
    except Exception:
        return None
    return v if v > 0 else None


def apply_cap(rows: Sequence[Any], cap_value: int) -> List[Any]:
    cap = cap_limit(cap_value)
    items = list(rows)
    if cap is None:
        return items
    return items[:cap]


def parse_llm_artifact_families(raw: str) -> Set[str]:
    out: Set[str] = set()
    for part in str(raw or "").split(","):
        key = str(part).strip().lower()
        if not key:
            continue
        if key in ALLOWED_LLM_ARTIFACT_FAMILIES:
            out.add(key)
        else:
            LOGGER.warning("Ignoring unsupported llm artifact family: %s", key)
    return out


def load_prompt_override(path_value: str, default_text: str) -> str:
    ptxt = str(path_value or "").strip()
    if not ptxt:
        return default_text
    path = Path(ptxt).expanduser().resolve()
    if not path.exists():
        LOGGER.warning("Prompt file not found, using default prompt: %s", path)
        return default_text
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        LOGGER.warning("Failed reading prompt file %s (%s); using default prompt", path, exc)
        return default_text
    txt = str(raw or "").strip()
    if not txt:
        LOGGER.warning("Prompt file is empty, using default prompt: %s", path)
        return default_text
    LOGGER.info("Loaded prompt override: %s", path)
    return txt


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def artifact_id(prefix: str, raw: str) -> str:
    return f"{prefix}:{sha256_hex(raw)}"


def qdrant_point_id(raw: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw)))


def qdrant_call_with_retry(desc: str, fn: Any, retries: int = 4, backoff_sec: float = 1.5) -> Any:
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= retries:
                raise
            sleep_s = backoff_sec * attempt
            LOGGER.warning(
                "Qdrant call failed (%s), attempt=%d/%d, retrying in %.1fs: %s",
                desc,
                attempt,
                retries,
                sleep_s,
                exc,
            )
            time.sleep(sleep_s)


def embed_batch(
    embed_url: str,
    model: str,
    texts: Sequence[str],
    *,
    timeout_sec: int,
    dimensions: Optional[int],
) -> List[List[float]]:
    global _EMBED_COMPAT_OMIT_TASK, _EMBED_COMPAT_OMIT_DIMENSIONS

    include_dim_default = dimensions is not None and not _EMBED_COMPAT_OMIT_DIMENSIONS
    include_task_default = not _EMBED_COMPAT_OMIT_TASK

    variant_order: List[Tuple[bool, bool]] = [
        (False, False),
        (False, include_dim_default),
        (include_task_default, False),
        (include_task_default, include_dim_default),
    ]
    tried: set[Tuple[bool, bool]] = set()
    errors: List[str] = []
    for include_task, include_dim in variant_order:
        key = (include_task, include_dim)
        if key in tried:
            continue
        tried.add(key)

        payload: Dict[str, Any] = {
            "model": model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if include_task:
            payload["task"] = "document"
        if include_dim and dimensions is not None:
            payload["dimensions"] = int(dimensions)

        resp = requests.post(embed_url, json=payload, timeout=int(timeout_sec))
        if resp.status_code >= 300:
            errors.append(f"{resp.status_code}: {resp.text[:220]}")
            continue

        obj = resp.json()
        data = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Embedding response missing data array")
        ordered = sorted(data, key=lambda x: int(x.get("index", 0)))
        vectors: List[List[float]] = []
        for item in ordered:
            emb = item.get("embedding")
            if not isinstance(emb, list):
                raise RuntimeError("Embedding item missing embedding vector")
            vectors.append([float(v) for v in emb])

        if include_task_default and not include_task and not _EMBED_COMPAT_OMIT_TASK:
            LOGGER.warning("Embedding endpoint compatibility mode enabled: omitting `task` in requests")
            _EMBED_COMPAT_OMIT_TASK = True
        if include_dim_default and not include_dim and not _EMBED_COMPAT_OMIT_DIMENSIONS:
            LOGGER.warning("Embedding endpoint compatibility mode enabled: omitting `dimensions` in requests")
            _EMBED_COMPAT_OMIT_DIMENSIONS = True
        return vectors

    raise RuntimeError(
        "Embedding request failed after compatibility retries: "
        + "; ".join(errors[-4:] if len(errors) > 4 else errors)
    )


def embed_batch_resilient(
    embed_url: str,
    model: str,
    texts: Sequence[str],
    *,
    timeout_sec: int,
    dimensions: Optional[int],
    max_attempts: int,
    retry_backoff_sec: float,
) -> List[List[float]]:
    if not texts:
        return []

    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            return embed_batch(
                embed_url,
                model,
                texts,
                timeout_sec=timeout_sec,
                dimensions=dimensions,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= int(max_attempts):
                break
            sleep_s = float(retry_backoff_sec) * attempt
            LOGGER.warning(
                "Embedding batch failed (size=%d) attempt=%d/%d: %s; retrying in %.1fs",
                len(texts),
                attempt,
                int(max_attempts),
                exc,
                sleep_s,
            )
            time.sleep(max(0.0, sleep_s))

    if len(texts) > 1:
        mid = len(texts) // 2
        left = embed_batch_resilient(
            embed_url,
            model,
            texts[:mid],
            timeout_sec=timeout_sec,
            dimensions=dimensions,
            max_attempts=max_attempts,
            retry_backoff_sec=retry_backoff_sec,
        )
        right = embed_batch_resilient(
            embed_url,
            model,
            texts[mid:],
            timeout_sec=timeout_sec,
            dimensions=dimensions,
            max_attempts=max_attempts,
            retry_backoff_sec=retry_backoff_sec,
        )
        return left + right

    err_msg = str(last_error) if last_error is not None else "unknown error"
    raise RuntimeError(f"Embedding failed for single text after retries: {err_msg}") from last_error


def extract_json_object(raw: str) -> Dict[str, Any]:
    txt = str(raw or "").strip()
    if not txt:
        return {}
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    start = txt.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(txt)):
        ch = txt[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                cand = txt[start : i + 1]
                try:
                    obj = json.loads(cand)
                except Exception:
                    return {}
                return obj if isinstance(obj, dict) else {}
    return {}


def _clip_text(value: str, max_chars: int) -> str:
    txt = str(value or "").strip()
    if max_chars <= 0 or len(txt) <= max_chars:
        return txt
    return txt[: max_chars - 3].rstrip() + "..."


def _normalize_entity_label(value: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "system": "system",
        "service": "system",
        "application": "system",
        "tool": "system",
        "vendor": "vendor",
        "supplier": "vendor",
        "project": "project",
        "program": "project",
        "initiative": "project",
        "person": "person",
        "people": "person",
        "owner": "person",
        "team_member": "person",
        "location": "location",
        "site": "location",
        "facility": "location",
    }
    return aliases.get(raw, "system")


def _normalize_rel_type(value: str) -> str:
    raw = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not raw:
        return "RELATED_TO"
    allowed = {
        "RELATED_TO",
        "USES",
        "DEPENDS_ON",
        "HOSTED_ON",
        "DEPLOYED_AT",
        "OWNED_BY",
        "MANAGED_BY",
        "IMPLEMENTS",
        "AFFECTS",
        "REQUIRES",
        "INTERFACES_WITH",
    }
    return raw if raw in allowed else "RELATED_TO"


def _entity_key(row: Dict[str, Any]) -> str:
    name = str(row.get("name", "")).strip().lower()
    label = _normalize_entity_label(str(row.get("type", "")))
    if not name:
        return ""
    return f"{label}|{name}"


def _relationship_key(row: Dict[str, Any]) -> str:
    src = str(row.get("source", "")).strip().lower()
    dst = str(row.get("target", "")).strip().lower()
    rel_type = _normalize_rel_type(str(row.get("relationship_type", "")))
    if not src or not dst:
        return ""
    return f"{src}|{rel_type}|{dst}"


def _claim_key(row: Dict[str, Any]) -> str:
    subj = str(row.get("subject", "")).strip().lower()
    pred = str(row.get("predicate", "")).strip().lower()
    obj = str(row.get("object", "")).strip().lower()
    evidence = str(row.get("evidence", "")).strip().lower()
    if not subj or not pred:
        return ""
    return f"{subj}|{pred}|{obj}|{evidence[:120]}"


def _dedupe_extracted_rows(rows: Sequence[Dict[str, Any]], *, row_type: str, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        if row_type == "entity":
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            normalized = {
                "name": name,
                "type": _normalize_entity_label(str(item.get("type", ""))),
                "description": str(item.get("description", "")).strip(),
            }
            key = _entity_key(normalized)
        elif row_type == "relationship":
            src = str(item.get("source", "")).strip()
            dst = str(item.get("target", "")).strip()
            if not src or not dst:
                continue
            normalized = {
                "source": src,
                "target": dst,
                "relationship_type": _normalize_rel_type(str(item.get("relationship_type", ""))),
                "description": str(item.get("description", "")).strip(),
            }
            key = _relationship_key(normalized)
        else:
            subj = str(item.get("subject", "")).strip()
            pred = str(item.get("predicate", "")).strip()
            if not subj or not pred:
                continue
            normalized = {
                "subject": subj,
                "predicate": pred,
                "object": str(item.get("object", "")).strip(),
                "claim_type": str(item.get("claim_type", "")).strip().lower() or "fact",
                "status": str(item.get("status", "")).strip().lower() or "unknown",
                "date": str(item.get("date", "")).strip(),
                "evidence": str(item.get("evidence", "")).strip(),
            }
            key = _claim_key(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(normalized)
        if int(limit) > 0 and len(out) >= int(limit):
            break
    return out


def _build_already_extracted_hints(rows: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for chunk_id, row in rows.items():
        ents = _dedupe_extracted_rows(row.get("entities", []), row_type="entity", limit=30)
        rels = _dedupe_extracted_rows(row.get("relationships", []), row_type="relationship", limit=30)
        claims = _dedupe_extracted_rows(row.get("claims", []), row_type="claim", limit=30)
        if not ents and not rels and not claims:
            continue
        out[chunk_id] = {
            "entity_keys": [x for x in (_entity_key(e) for e in ents) if x][:30],
            "relationship_keys": [x for x in (_relationship_key(r) for r in rels) if x][:30],
            "claim_keys": [x for x in (_claim_key(c) for c in claims) if x][:30],
        }
    return out


def extract_chunk_semantics_llm(
    chat: Any,
    chunk_batch: Sequence[ChunkDoc],
    *,
    max_chars_per_chunk: int,
    max_entities_per_chunk: int,
    max_relationships_per_chunk: int,
    max_claims_per_chunk: int,
    max_gleanings: int,
    system_prompt: str,
    temperature: float,
    top_p: float,
    max_completion_tokens: int,
) -> Dict[str, Dict[str, Any]]:
    payload_chunks: List[Dict[str, Any]] = []
    for c in chunk_batch:
        payload_chunks.append(
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "path": str(c.metadata.get("path", "")),
                "semantic_domain": str(c.metadata.get("semantic_domain", "")),
                "first_bucket": str(c.metadata.get("first_bucket", "")),
                "sub_bucket": str(c.metadata.get("sub_bucket", "")),
                "text": _clip_text(c.text, int(max_chars_per_chunk)),
            }
        )
    aggregated: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    gleanings = max(1, int(max_gleanings))
    for pass_idx in range(gleanings):
        user_payload: Dict[str, Any] = {
            "task": "extract_entities_relationships_claims" if pass_idx == 0 else "extract_additional_entities_relationships_claims",
            "constraints": {
                "entity_types": ["system", "vendor", "project", "person", "location", "other"],
                "relationship_types": [
                    "RELATED_TO",
                    "USES",
                    "DEPENDS_ON",
                    "HOSTED_ON",
                    "DEPLOYED_AT",
                    "OWNED_BY",
                    "MANAGED_BY",
                    "IMPLEMENTS",
                    "AFFECTS",
                    "REQUIRES",
                    "INTERFACES_WITH",
                ],
                "claim_types": ["fact", "policy", "procedure", "status", "risk", "timeline", "other"],
                "status_values": ["confirmed", "tentative", "historical", "unknown"],
                "max_entities_per_chunk": int(max_entities_per_chunk),
                "max_relationships_per_chunk": int(max_relationships_per_chunk),
                "max_claims_per_chunk": int(max_claims_per_chunk),
                "additional_only": bool(pass_idx > 0),
            },
            "chunks": payload_chunks,
        }
        if pass_idx > 0:
            user_payload["already_extracted"] = _build_already_extracted_hints(aggregated)
            user_payload["instructions"] = (
                "Return only NEW high-confidence extractions not already in already_extracted for each chunk."
            )

        resp_obj = chat.complete(
            [
                {"role": "system", "content": str(system_prompt or DEFAULT_LLM_EXTRACTION_SYSTEM_PROMPT)},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            temperature=float(temperature),
            top_p=float(top_p),
            max_completion_tokens=int(max_completion_tokens),
        )
        raw = chat.extract_text(resp_obj)
        parsed = extract_json_object(raw)
        chunk_rows = parsed.get("chunks") if isinstance(parsed.get("chunks"), list) else []
        for row in chunk_rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("chunk_id", "")).strip()
            if not cid:
                continue
            cur = aggregated.setdefault(cid, {"entities": [], "relationships": [], "claims": []})
            ents = row.get("entities") if isinstance(row.get("entities"), list) else []
            rels = row.get("relationships") if isinstance(row.get("relationships"), list) else []
            claims = row.get("claims") if isinstance(row.get("claims"), list) else []
            cur["entities"].extend([x for x in ents if isinstance(x, dict)])
            cur["relationships"].extend([x for x in rels if isinstance(x, dict)])
            cur["claims"].extend([x for x in claims if isinstance(x, dict)])

    out: Dict[str, Dict[str, Any]] = {}
    for cid, row in aggregated.items():
        out[cid] = {
            "entities": _dedupe_extracted_rows(
                row.get("entities", []),
                row_type="entity",
                limit=max(0, int(max_entities_per_chunk)),
            ),
            "relationships": _dedupe_extracted_rows(
                row.get("relationships", []),
                row_type="relationship",
                limit=max(0, int(max_relationships_per_chunk)),
            ),
            "claims": _dedupe_extracted_rows(
                row.get("claims", []),
                row_type="claim",
                limit=max(0, int(max_claims_per_chunk)),
            ),
        }
    return out


def load_es_records(es: Elasticsearch, index: str, *, max_records: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for hit in es_scan(
        client=es,
        index=index,
        query={"query": {"match_all": {}}},
        preserve_order=False,
        size=500,
        scroll="2m",
    ):
        src = hit.get("_source", {}) if isinstance(hit, dict) else {}
        if isinstance(src, dict):
            rows.append(src)
            if max_records > 0 and len(rows) >= int(max_records):
                break
    return rows


def build_chunk_rows(chunk_sources: Sequence[Dict[str, Any]]) -> List[ChunkDoc]:
    chunks: List[ChunkDoc] = []
    for src in chunk_sources:
        chunk_id = str(src.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        chunk = ChunkDoc(
            chunk_id=chunk_id,
            doc_id=str(src.get("doc_id", "")),
            chunk_index=int(src.get("chunk_index", 0) or 0),
            text=str(src.get("text", "") or ""),
            metadata={
                "chunk_id": chunk_id,
                "doc_id": str(src.get("doc_id", "")),
                "path": str(src.get("path", "")),
                "source_root": str(src.get("source_root", "")),
                "first_bucket": str(src.get("first_bucket", "")),
                "sub_bucket": str(src.get("sub_bucket", "")),
                "semantic_domain": str(src.get("semantic_domain", "")),
                "semantic_decision": str(src.get("semantic_decision", "")),
                "mtime_date": str(src.get("mtime_date", "") or ""),
                "effective_date": str(src.get("effective_date", "") or src.get("mtime_date", "") or ""),
                "mtime_year": int(src.get("mtime_year", -1) or -1),
                "effective_year": int(src.get("effective_year", src.get("mtime_year", -1)) or -1),
                "mtime_epoch_day": int(src.get("mtime_epoch_day", -1) or -1),
                "effective_epoch_day": int(src.get("effective_epoch_day", src.get("mtime_epoch_day", -1)) or -1),
                "artifact_type": str(src.get("artifact_type", "")),
                "doc_type": str(src.get("doc_type", "")),
                "sensitivity_level": str(src.get("sensitivity_level", "")),
                "tags": norm_list(src.get("tags")),
                "entities_systems": norm_list(src.get("entities_systems")),
                "entities_vendors": norm_list(src.get("entities_vendors")),
                "entities_projects": norm_list(src.get("entities_projects")),
                "entities_people": norm_list(src.get("entities_people")),
                "entities_locations": norm_list(src.get("entities_locations")),
            },
        )
        chunks.append(chunk)
    return chunks


def backfill_doc_counts(doc_records: Sequence[Dict[str, Any]], chunks: Sequence[ChunkDoc]) -> List[Dict[str, Any]]:
    chunk_counts: Dict[str, int] = defaultdict(int)
    for c in chunks:
        chunk_counts[c.doc_id] += 1

    out: List[Dict[str, Any]] = []
    for rec in doc_records:
        doc_id = str(rec.get("doc_id", ""))
        item = dict(rec)
        if int(item.get("chunk_count", 0) or 0) <= 0:
            item["chunk_count"] = int(chunk_counts.get(doc_id, 0))
        entities = item.get("entities")
        if not isinstance(entities, dict):
            item["entities"] = {
                "systems": norm_list(item.get("entities_systems")),
                "vendors": norm_list(item.get("entities_vendors")),
                "projects": norm_list(item.get("entities_projects")),
                "people": norm_list(item.get("entities_people")),
                "locations": norm_list(item.get("entities_locations")),
            }
        out.append(item)
    return out


def doc_entity_tokens(rec: Dict[str, Any]) -> List[str]:
    entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
    out: List[str] = []
    for label, key in ENTITY_FIELDS:
        for name in norm_list(entities.get(key)):
            norm = str(name).strip().lower()
            if not norm:
                continue
            out.append(f"{label}:{norm}")
    return out


def build_doc_similarity_edges(
    doc_records: Sequence[Dict[str, Any]],
    *,
    min_shared_entities: int,
) -> Tuple[Dict[str, set[str]], Dict[Tuple[str, str], int]]:
    doc_entities: Dict[str, set[str]] = {}
    entity_docs: Dict[str, List[str]] = defaultdict(list)

    for rec in doc_records:
        doc_id = str(rec.get("doc_id", "")).strip()
        if not doc_id:
            continue
        ents = set(doc_entity_tokens(rec))
        doc_entities[doc_id] = ents
        for ent in ents:
            entity_docs[ent].append(doc_id)

    edge_weights: Dict[Tuple[str, str], int] = defaultdict(int)
    for docs in entity_docs.values():
        uniq = sorted(set(docs))
        if len(uniq) < 2:
            continue
        for a, b in combinations(uniq, 2):
            edge_weights[(a, b)] += 1

    threshold = max(1, int(min_shared_entities))
    filtered_edges: Dict[Tuple[str, str], int] = {}
    for pair, weight in edge_weights.items():
        if int(weight) >= threshold:
            filtered_edges[pair] = int(weight)
    return doc_entities, filtered_edges


def choose_community_method(requested: str, *, has_edges: bool) -> str:
    mode = str(requested or "auto").strip().lower()
    if mode not in {"auto", "louvain", "components", "domain"}:
        mode = "auto"

    if mode == "auto":
        if has_edges and nx is not None and hasattr(nx.algorithms.community, "louvain_communities"):
            return "louvain"
        if has_edges:
            return "components"
        return "domain"

    if mode == "louvain":
        if nx is not None and hasattr(nx.algorithms.community, "louvain_communities"):
            return "louvain"
        LOGGER.warning("Louvain requested but networkx louvain_communities is unavailable; falling back to components")
        return "components" if has_edges else "domain"

    if mode == "components":
        return "components" if has_edges else "domain"

    return "domain"


def detect_doc_communities(
    doc_records: Sequence[Dict[str, Any]],
    *,
    requested_method: str,
    resolution: float,
    min_shared_entities: int,
) -> Tuple[str, List[List[str]], Dict[str, set[str]], Dict[Tuple[str, str], int]]:
    doc_entities, edge_weights = build_doc_similarity_edges(
        doc_records,
        min_shared_entities=int(min_shared_entities),
    )
    doc_ids = sorted(doc_entities.keys())
    method = choose_community_method(requested_method, has_edges=bool(edge_weights))

    communities: List[List[str]] = []
    if method == "domain":
        by_domain: Dict[str, List[str]] = defaultdict(list)
        for rec in doc_records:
            doc_id = str(rec.get("doc_id", "")).strip()
            if not doc_id:
                continue
            domain = str(rec.get("semantic_domain", "")).strip() or "general"
            by_domain[domain].append(doc_id)
        communities = [sorted(set(rows)) for _, rows in sorted(by_domain.items()) if rows]
        return method, communities, doc_entities, edge_weights

    if nx is not None:
        g = nx.Graph()
        g.add_nodes_from(doc_ids)
        for (a, b), w in edge_weights.items():
            g.add_edge(a, b, weight=float(w))
        if method == "louvain":
            groups = nx.algorithms.community.louvain_communities(
                g,
                weight="weight",
                resolution=float(resolution),
                seed=42,
            )
            communities = [sorted(str(x) for x in grp) for grp in groups if grp]
        else:
            communities = [sorted(str(x) for x in grp) for grp in nx.connected_components(g) if grp]
        return method, communities, doc_entities, edge_weights

    # Pure-python connected components fallback.
    parent: Dict[str, str] = {doc_id: doc_id for doc_id in doc_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (a, b), _w in edge_weights.items():
        if a in parent and b in parent:
            union(a, b)

    comp_map: Dict[str, List[str]] = defaultdict(list)
    for doc_id in doc_ids:
        comp_map[find(doc_id)].append(doc_id)
    communities = [sorted(rows) for _, rows in sorted(comp_map.items()) if rows]
    return "components", communities, doc_entities, edge_weights


def build_graph_community_reports(
    doc_records: Sequence[Dict[str, Any]],
    chunks: Sequence[ChunkDoc],
    *,
    community_method: str,
    community_resolution: float,
    min_shared_entities: int,
    min_docs: int,
    max_reports: int,
) -> Tuple[List[GraphArtifact], Dict[str, str], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if not doc_records:
        return [], {}, {}, {"method": "none", "raw_communities": 0, "reported_communities": 0}

    used_method, communities, _doc_entities, edge_weights = detect_doc_communities(
        doc_records,
        requested_method=community_method,
        resolution=float(community_resolution),
        min_shared_entities=int(min_shared_entities),
    )

    docs_by_id: Dict[str, Dict[str, Any]] = {str(r.get("doc_id", "")): r for r in doc_records if str(r.get("doc_id", "")).strip()}
    chunk_counts: Dict[str, int] = defaultdict(int)
    for c in chunks:
        chunk_counts[str(c.doc_id)] += 1

    # Keep larger communities first so report budget is spent on dense clusters.
    ranked = sorted((sorted(set(grp)) for grp in communities if grp), key=lambda g: (-len(g), g[0]))
    artifacts: List[GraphArtifact] = []
    membership: Dict[str, str] = {}
    community_meta: Dict[str, Dict[str, Any]] = {}
    report_limit = max(1, int(max_reports))
    min_docs_i = max(1, int(min_docs))
    emitted = 0
    dropped_too_small = 0

    for grp in ranked:
        if emitted >= report_limit:
            break
        if len(grp) < min_docs_i:
            dropped_too_small += 1
            continue

        rows: List[Dict[str, Any]] = [docs_by_id[d] for d in grp if d in docs_by_id]
        if not rows:
            continue

        domain_counts: Counter[str] = Counter()
        first_counts: Counter[str] = Counter()
        sub_counts: Counter[str] = Counter()
        tag_counts: Counter[str] = Counter()
        entity_counts: Counter[str] = Counter()
        sample_paths: List[str] = []
        chunk_total = 0

        for rec in rows:
            domain = str(rec.get("semantic_domain", "")).strip() or "general"
            domain_counts[domain] += 1
            first_bucket = str(rec.get("first_bucket", "")).strip()
            sub_bucket = str(rec.get("sub_bucket", "")).strip()
            if first_bucket:
                first_counts[first_bucket] += 1
            if sub_bucket:
                sub_counts[sub_bucket] += 1
            for tag in norm_list(rec.get("tags")):
                tag_counts[tag] += 1
            entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
            for label, key in ENTITY_FIELDS:
                for ent in norm_list(entities.get(key)):
                    entity_counts[f"{label}:{ent}"] += 1
            p = str(rec.get("path", "")).strip()
            if p:
                sample_paths.append(p)
            doc_id = str(rec.get("doc_id", "")).strip()
            chunk_total += int(rec.get("chunk_count", 0) or chunk_counts.get(doc_id, 0))

        top_domains = [k for k, _ in domain_counts.most_common(4)]
        top_first = [k for k, _ in first_counts.most_common(6)]
        top_sub = [k for k, _ in sub_counts.most_common(8)]
        top_tags = [k for k, _ in tag_counts.most_common(10)]
        top_entities = [k for k, _ in entity_counts.most_common(10)]
        dominant_domain = top_domains[0] if top_domains else "general"
        span_from, span_to = _temporal_span(
            [str(r.get("effective_date", "") or r.get("mtime_date", "") or "") for r in rows]
        )
        span_years = _temporal_span_years(span_from, span_to)

        community_id = f"graph:{used_method}:{emitted + 1:04d}"
        aid = artifact_id("community_graph", community_id)
        text = (
            f"Graph community report {community_id}. "
            f"Method={used_method}. Documents={len(rows)}, Chunks={chunk_total}. "
            f"Dominant domain={dominant_domain}. "
            f"Top domains={', '.join(top_domains) if top_domains else 'n/a'}. "
            f"Top first buckets={', '.join(top_first) if top_first else 'n/a'}. "
            f"Top sub-buckets={', '.join(top_sub) if top_sub else 'n/a'}. "
            f"Top entities={', '.join(top_entities[:6]) if top_entities else 'n/a'}. "
            f"Representative paths={'; '.join(sorted(set(sample_paths))[:6]) if sample_paths else 'n/a'}."
        )

        meta = {
            "artifact_id": aid,
            "artifact_type": "community_report",
            "artifact_generation": "deterministic",
            "extraction_method": "graph_partitioning",
            "retrieval_mode": "global",
            "community_id": community_id,
            "community_scope": "graph",
            "community_method": used_method,
            "semantic_domain": dominant_domain,
            "doc_count": len(rows),
            "chunk_count": chunk_total,
            "top_domains": top_domains,
            "top_buckets": top_first,
            "top_sub_buckets": top_sub,
            "top_tags": top_tags,
            "top_entities": top_entities,
            "sample_paths": sorted(set(sample_paths))[:12],
            "temporal_span_from": span_from,
            "temporal_span_to": span_to,
            "temporal_span_years": float(span_years),
            "effective_date": span_to,
        }
        artifacts.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="community_report",
                text=text,
                metadata=meta,
                vector=None,
            )
        )

        community_meta[community_id] = {
            "community_id": community_id,
            "scope": "graph",
            "method": used_method,
            "semantic_domain": dominant_domain,
            "doc_count": len(rows),
            "chunk_count": chunk_total,
            "top_domains": top_domains,
            "top_buckets": top_first,
            "top_sub_buckets": top_sub,
            "top_tags": top_tags,
            "top_entities": top_entities,
            "sample_paths": sorted(set(sample_paths))[:12],
            "temporal_span_from": span_from,
            "temporal_span_to": span_to,
            "temporal_span_years": float(span_years),
            "effective_date": span_to,
        }
        for rec in rows:
            doc_id = str(rec.get("doc_id", "")).strip()
            if doc_id:
                membership[doc_id] = community_id
        emitted += 1

    stats = {
        "method": used_method,
        "raw_communities": len(ranked),
        "reported_communities": emitted,
        "dropped_too_small": dropped_too_small,
        "doc_similarity_edges": len(edge_weights),
    }
    return artifacts, membership, community_meta, stats


def _split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(str(text or "").strip()) if p.strip()]
    if parts:
        return parts
    txt = str(text or "").strip()
    return [txt] if txt else []


def _pick_hint_sentence(sentences: Sequence[str], hints: Sequence[str]) -> str:
    for sent in sentences:
        low = sent.lower()
        for hint in hints:
            if hint in low:
                return sent.strip()
    return ""


def _chunk_common_metadata(chunk: ChunkDoc) -> Dict[str, Any]:
    return {
        "path": str(chunk.metadata.get("path", "")).strip(),
        "semantic_domain": str(chunk.metadata.get("semantic_domain", "")).strip(),
        "first_bucket": str(chunk.metadata.get("first_bucket", "")).strip(),
        "sub_bucket": str(chunk.metadata.get("sub_bucket", "")).strip(),
        "source_root": str(chunk.metadata.get("source_root", "")).strip(),
        "mtime_date": str(chunk.metadata.get("mtime_date", "")).strip(),
        "effective_date": str(chunk.metadata.get("effective_date", "")).strip(),
        "mtime_year": int(chunk.metadata.get("mtime_year", -1) or -1),
        "effective_year": int(chunk.metadata.get("effective_year", -1) or -1),
        "mtime_epoch_day": int(chunk.metadata.get("mtime_epoch_day", -1) or -1),
        "effective_epoch_day": int(chunk.metadata.get("effective_epoch_day", -1) or -1),
    }


def _split_lines_and_sentences(text: str) -> List[str]:
    txt = str(text or "").strip()
    if not txt:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for line in txt.splitlines():
        line_s = re.sub(r"\s+", " ", str(line or "").strip())
        if not line_s:
            continue
        fp = line_s.lower()
        if fp in seen:
            continue
        seen.add(fp)
        out.append(line_s)
    for sent in _split_sentences(txt):
        sent_s = re.sub(r"\s+", " ", str(sent or "").strip())
        if not sent_s:
            continue
        fp = sent_s.lower()
        if fp in seen:
            continue
        seen.add(fp)
        out.append(sent_s)
    return out


def _normalize_header_cell(value: str) -> str:
    txt = str(value or "").strip().lower()
    txt = re.sub(r"[\s_/.-]+", " ", txt)
    txt = re.sub(r"[^a-z0-9 ]+", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _split_tabular_cells(line: str) -> List[str]:
    txt = str(line or "").strip()
    if not txt:
        return []
    if "|" in txt:
        return [str(c or "").strip() for c in txt.split("|") if str(c or "").strip()]
    if "\t" in txt:
        return [str(c or "").strip() for c in txt.split("\t") if str(c or "").strip()]
    if txt.count(",") >= 2:
        try:
            row = next(csv.reader([txt]))
            cells = [str(c or "").strip() for c in row if str(c or "").strip()]
            if len(cells) >= 3:
                return cells
        except Exception:
            pass
    if re.search(r"\s{2,}", txt):
        cells = [str(c or "").strip() for c in re.split(r"\s{2,}", txt) if str(c or "").strip()]
        if len(cells) >= 3:
            return cells
    return []


def _inventory_header_map(cells: Sequence[str]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for idx, raw in enumerate(cells):
        col = _normalize_header_cell(raw)
        if not col:
            continue
        if "computer" in col and "computer" not in mapping:
            mapping["computer"] = int(idx)
            continue
        if ("operating system" in col or col == "os" or col.endswith(" os")) and "os" not in mapping:
            mapping["os"] = int(idx)
            continue
        if ("ip address" in col or col == "ip") and "ip" not in mapping:
            mapping["ip"] = int(idx)
            continue
    if {"computer", "os", "ip"}.issubset(set(mapping.keys())):
        return mapping
    return {}


def _account_header_map(cells: Sequence[str]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for idx, raw in enumerate(cells):
        col = _normalize_header_cell(raw)
        if not col:
            continue
        if "username" in col and "username" not in mapping:
            mapping["username"] = int(idx)
            continue
        if col == "status" and "status" not in mapping:
            mapping["status"] = int(idx)
            continue
        if "locked date" in col and "locked" not in mapping:
            mapping["locked"] = int(idx)
            continue
        if (col == "service" or col == "services") and "service" not in mapping:
            mapping["service"] = int(idx)
            continue
    if {"username", "status", "locked", "service"}.issubset(set(mapping.keys())):
        return mapping
    return {}


def _iter_tabular_rows(lines: Sequence[str], start_idx: int, *, expected_min_cells: int, max_rows: int) -> Iterable[List[str]]:
    emitted = 0
    misses = 0
    for j in range(int(start_idx), len(lines)):
        row_cells = _split_tabular_cells(lines[j])
        if len(row_cells) < int(expected_min_cells):
            misses += 1
            if misses >= 2 and emitted > 0:
                break
            continue
        misses = 0
        yield row_cells
        emitted += 1
        if emitted >= int(max_rows):
            break


def _clean_service_value(value: str) -> str:
    txt = str(value or "").strip()
    if not txt:
        return ""
    txt = re.split(
        r"\b(?:Account information|Username:|Status:|Locked Date:|First Name:|Last Name:)\b",
        txt,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return txt.strip(" ,.;")


def _extract_condition_clause(text: str) -> str:
    txt = str(text or "").strip()
    if not txt:
        return ""
    m = _CONDITION_CLAUSE_RE.search(txt)
    if m is None:
        return ""
    return re.sub(r"\s+", " ", str(m.group(0) or "").strip())[:220]


def _extract_next_step_clause(text: str) -> str:
    txt = str(text or "").strip()
    if not txt:
        return ""
    m = _NEXT_STEP_CLAUSE_RE.search(txt)
    if m is None:
        return ""
    return re.sub(r"\s+", " ", str(m.group(0) or "").strip())[:220]


def _extract_actor_hint(text: str) -> str:
    txt = str(text or "").strip()
    if not txt:
        return ""
    m = re.match(
        r"^\s*([A-Z][A-Za-z0-9_.-]{1,32}(?:\s+[A-Z][A-Za-z0-9_.-]{1,32}){0,2})\s+"
        r"(?:will|should|must|can|did|does|is|was|installed?|configured?|updated?|worked|helped|set|added|removed)\b",
        txt,
    )
    if m is not None:
        return str(m.group(1) or "").strip()[:120]
    m = _ACTOR_HINT_RE.search(txt)
    if m is not None:
        return str(m.group(1) or "").strip()[:120]
    return ""


def _extract_frequency_hint(text: str) -> str:
    txt = str(text or "").strip()
    if not txt:
        return ""
    m = _RISK_FREQUENCY_RE.search(txt)
    if m is None:
        return ""
    return re.sub(r"\s+", " ", str(m.group(0) or "").strip())[:120]


def _extract_limit_hint(text: str) -> str:
    txt = str(text or "").strip()
    if not txt:
        return ""
    m = _RISK_LIMIT_RE.search(txt)
    if m is None:
        return ""
    return re.sub(r"\s+", " ", str(m.group(0) or "").strip())[:120]


def _extract_associated_issue(text: str) -> str:
    for sent in _split_sentences(text):
        if _RISK_ISSUE_HINT_RE.search(sent):
            return re.sub(r"\s+", " ", str(sent or "").strip())[:220]
    return ""


def _extract_first_timestamp(text: str) -> str:
    m = _LOG_ENTRY_TIMESTAMP_RE.search(str(text or ""))
    if m is None:
        return ""
    return str(m.group(0) or "").strip()[:80]


def _parse_iso_date(value: str) -> Optional[datetime]:
    txt = str(value or "").strip()
    if not txt:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", txt)
    if m is not None:
        try:
            return datetime.fromisoformat(m.group(1))
        except Exception:
            return None
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except Exception:
        return None


def _temporal_span(values: Sequence[str]) -> Tuple[str, str]:
    parsed: List[datetime] = []
    for v in values:
        d = _parse_iso_date(str(v or ""))
        if d is not None:
            parsed.append(d)
    if not parsed:
        return "", ""
    parsed.sort()
    return parsed[0].date().isoformat(), parsed[-1].date().isoformat()


def _temporal_span_years(span_from: str, span_to: str) -> float:
    d0 = _parse_iso_date(span_from)
    d1 = _parse_iso_date(span_to)
    if d0 is None or d1 is None:
        return 0.0
    days = max(0, (d1.date() - d0.date()).days)
    return float(days) / 365.25


def enrich_artifacts_temporal_metadata(
    artifacts: List[GraphArtifact],
    chunks: Sequence[ChunkDoc],
    doc_records: Sequence[Dict[str, Any]],
) -> None:
    chunk_meta_by_id: Dict[str, Dict[str, Any]] = {
        str(c.chunk_id): dict(c.metadata or {})
        for c in chunks
        if str(c.chunk_id).strip()
    }
    doc_by_id: Dict[str, Dict[str, Any]] = {
        str(r.get("doc_id", "")).strip(): dict(r)
        for r in doc_records
        if str(r.get("doc_id", "")).strip()
    }
    for art in artifacts:
        meta = art.metadata if isinstance(art.metadata, dict) else {}
        cid = str(meta.get("chunk_id", "")).strip()
        did = str(meta.get("doc_id", "")).strip()
        src: Dict[str, Any] = {}
        if cid and cid in chunk_meta_by_id:
            src = chunk_meta_by_id[cid]
        elif did and did in doc_by_id:
            src = doc_by_id[did]

        for key, default in (
            ("mtime_date", ""),
            ("effective_date", ""),
            ("mtime_year", -1),
            ("effective_year", -1),
            ("mtime_epoch_day", -1),
            ("effective_epoch_day", -1),
        ):
            val = meta.get(key, None)
            missing = val is None or val == "" or val == -1
            if missing and key in src:
                meta[key] = src.get(key)

        if (not str(meta.get("effective_date", "")).strip()) and str(meta.get("mtime_date", "")).strip():
            meta["effective_date"] = str(meta.get("mtime_date", "")).strip()
        if int(meta.get("effective_year", -1) or -1) <= 0 and int(meta.get("mtime_year", -1) or -1) > 0:
            meta["effective_year"] = int(meta.get("mtime_year", -1))
        if int(meta.get("effective_epoch_day", -1) or -1) <= 0 and int(meta.get("mtime_epoch_day", -1) or -1) > 0:
            meta["effective_epoch_day"] = int(meta.get("mtime_epoch_day", -1))

        if not str(meta.get("temporal_span_from", "")).strip() and str(meta.get("mtime_date", "")).strip():
            meta["temporal_span_from"] = str(meta.get("mtime_date", "")).strip()
        if not str(meta.get("temporal_span_to", "")).strip() and str(meta.get("effective_date", "")).strip():
            meta["temporal_span_to"] = str(meta.get("effective_date", "")).strip()
        if not str(meta.get("effective_date", "")).strip() and str(meta.get("temporal_span_to", "")).strip():
            meta["effective_date"] = str(meta.get("temporal_span_to", "")).strip()
        if float(meta.get("temporal_span_years", 0.0) or 0.0) <= 0.0:
            meta["temporal_span_years"] = float(
                _temporal_span_years(
                    str(meta.get("temporal_span_from", "")).strip(),
                    str(meta.get("temporal_span_to", "")).strip(),
                )
            )


def _extract_troubleshooting_fields(text: str) -> Dict[str, str]:
    sentences = _split_sentences(text)
    symptom = _pick_hint_sentence(sentences, _TROUBLESHOOT_SYMPTOM_HINTS)
    cause = _pick_hint_sentence(sentences, _TROUBLESHOOT_CAUSE_HINTS)
    remediation = _pick_hint_sentence(sentences, _TROUBLESHOOT_REMEDIATION_HINTS)
    outcome = _pick_hint_sentence(sentences, _TROUBLESHOOT_OUTCOME_HINTS)
    timestamp = _extract_first_timestamp(text)
    action = remediation
    if not action:
        action = _pick_hint_sentence(
            sentences,
            ["restart", "reboot", "replace", "set ", "configured", "modified", "updated", "installed", "removed"],
        )
    return {
        "symptom": symptom,
        "cause": cause,
        "action": action,
        "outcome": outcome,
        "timestamp": timestamp,
        "remediation": remediation,
    }


def _is_log_like_chunk(chunk: ChunkDoc) -> bool:
    path = str(chunk.metadata.get("path", "")).lower()
    text = str(chunk.text or "")
    if "log.file" in path or path.endswith(".log") or "/logs/" in path:
        return True
    if text.count("====") >= 2:
        return True
    return len(_LOG_ENTRY_TIMESTAMP_RE.findall(text)) >= 2


def _split_log_entries(text: str) -> List[str]:
    raw = str(text or "")
    if not raw.strip():
        return []
    expanded = re.sub(r"={3,}", "\n", raw)
    expanded = re.sub(
        r"(?<!\n)(?=\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+[A-Z]{2,5}\s+\d{4}\b)",
        "\n",
        expanded,
    )
    parts = [p.strip() for p in re.split(r"\n{2,}|\n(?=\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b)", expanded) if p and p.strip()]
    if parts:
        return parts
    return [expanded.strip()]


def build_log_entry_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    if not _is_log_like_chunk(chunk):
        return []
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    entries = _split_log_entries(text)
    out: List[GraphArtifact] = []
    seen_fp: set[str] = set()
    for idx, entry in enumerate(entries):
        if len(out) >= int(_MAX_LOG_ENTRY_ARTIFACTS_PER_CHUNK):
            break
        cleaned = re.sub(r"\s+", " ", str(entry or "").strip())
        if len(cleaned) < 24:
            continue
        fp = sha256_hex(cleaned.lower())
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        aid = artifact_id("log_entry", f"{chunk.chunk_id}|{idx}|{fp}")
        out.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="log_entry",
                text=f"Log entry from '{path}': {cleaned}",
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "log_entry",
                    "artifact_generation": "deterministic",
                    "extraction_method": "heuristic_log_entry_split",
                    "retrieval_mode": "local",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "path": path,
                    "source_root": common.get("source_root", ""),
                    "semantic_domain": common.get("semantic_domain", ""),
                    "first_bucket": common.get("first_bucket", ""),
                    "sub_bucket": common.get("sub_bucket", ""),
                    "log_entry_index": int(idx),
                },
                vector=None,
            )
        )
    return out


def build_procedure_step_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    rows = _split_lines_and_sentences(text)
    out: List[GraphArtifact] = []
    seen_fp: set[str] = set()
    for idx, row in enumerate(rows):
        if len(out) >= int(_MAX_PROCEDURE_STEP_ARTIFACTS_PER_CHUNK):
            break
        low = row.lower()
        if not (_PROCEDURE_LINE_RE.search(row) or _PROCEDURE_SENTENCE_HINT_RE.search(low)):
            continue
        if len(row) < 20:
            continue
        fp = sha256_hex(row.lower())
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        actor = _extract_actor_hint(row)
        condition = _extract_condition_clause(row)
        next_step = _extract_next_step_clause(row)
        aid = artifact_id("procedure_step", f"{chunk.chunk_id}|{idx}|{fp}")
        text = f"Procedure step from '{path}': {row}"
        if actor:
            text += f" Actor: {actor}."
        if condition:
            text += f" Condition: {condition}."
        if next_step:
            text += f" Next step: {next_step}."
        out.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="procedure_step",
                text=text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "procedure_step",
                    "artifact_generation": "deterministic",
                    "extraction_method": "heuristic_procedure_step",
                    "retrieval_mode": "local",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "path": path,
                    "source_root": common.get("source_root", ""),
                    "semantic_domain": common.get("semantic_domain", ""),
                    "first_bucket": common.get("first_bucket", ""),
                    "sub_bucket": common.get("sub_bucket", ""),
                    "step_index": int(idx),
                    "step": row,
                    "actor": actor,
                    "condition": condition,
                    "next_step": next_step,
                },
                vector=None,
            )
        )
    return out


def build_compliance_constraint_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    rows = _split_lines_and_sentences(text)
    out: List[GraphArtifact] = []
    seen_fp: set[str] = set()
    for idx, row in enumerate(rows):
        if len(out) >= int(_MAX_COMPLIANCE_CONSTRAINT_ARTIFACTS_PER_CHUNK):
            break
        if not _COMPLIANCE_CONSTRAINT_RE.search(row):
            continue
        if len(row) < 14:
            continue
        fp = sha256_hex(row.lower())
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        aid = artifact_id("compliance_constraint", f"{chunk.chunk_id}|{idx}|{fp}")
        out.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="compliance_constraint",
                text=f"Compliance constraint from '{path}': {row}",
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "compliance_constraint",
                    "artifact_generation": "deterministic",
                    "extraction_method": "heuristic_compliance_constraint",
                    "retrieval_mode": "local",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "path": path,
                    "source_root": common.get("source_root", ""),
                    "semantic_domain": common.get("semantic_domain", ""),
                    "first_bucket": common.get("first_bucket", ""),
                    "sub_bucket": common.get("sub_bucket", ""),
                    "constraint_index": int(idx),
                },
                vector=None,
            )
        )
    return out


def build_risk_signal_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    rows = _split_lines_and_sentences(text)
    out: List[GraphArtifact] = []
    seen_fp: set[str] = set()
    for idx, row in enumerate(rows):
        if len(out) >= int(_MAX_RISK_SIGNAL_ARTIFACTS_PER_CHUNK):
            break
        if not _RISK_SIGNAL_RE.search(row):
            continue
        if not _RISK_MECHANISM_RE.search(row):
            continue
        if len(row) < 18:
            continue
        fp = sha256_hex(row.lower())
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        frequency = _extract_frequency_hint(row)
        limit = _extract_limit_hint(row)
        associated_issue = _extract_associated_issue(row)
        remediation = _pick_hint_sentence(_split_sentences(row), _TROUBLESHOOT_REMEDIATION_HINTS)
        aid = artifact_id("risk_signal", f"{chunk.chunk_id}|{idx}|{fp}")
        text = f"Risk signal from '{path}': {row}"
        if frequency:
            text += f" Frequency: {frequency}."
        if limit:
            text += f" Limit: {limit}."
        if associated_issue:
            text += f" Associated issue: {associated_issue}."
        if remediation:
            text += f" Remediation: {remediation}."
        out.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="risk_signal",
                text=text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "risk_signal",
                    "artifact_generation": "deterministic",
                    "extraction_method": "heuristic_risk_signal",
                    "retrieval_mode": "local",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "path": path,
                    "source_root": common.get("source_root", ""),
                    "semantic_domain": common.get("semantic_domain", ""),
                    "first_bucket": common.get("first_bucket", ""),
                    "sub_bucket": common.get("sub_bucket", ""),
                    "risk_index": int(idx),
                    "signal": row,
                    "frequency": frequency,
                    "limit": limit,
                    "associated_issue": associated_issue,
                    "remediation": remediation,
                },
                vector=None,
            )
        )
    return out


def build_inventory_row_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    rows = [str(x or "").strip() for x in str(chunk.text or "").splitlines() if str(x or "").strip()]
    out: List[GraphArtifact] = []
    seen_fp: set[str] = set()

    def _append_row(computer: str, os_name: str, ip_addr: str, row_idx: int, method: str) -> None:
        comp = str(computer or "").strip()
        osn = str(os_name or "").strip()
        ip = str(ip_addr or "").strip()
        if not comp or not osn or not ip:
            return
        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip) is None:
            return
        if len(out) >= int(_MAX_INVENTORY_ROW_ARTIFACTS_PER_CHUNK):
            return
        fp = sha256_hex(f"{comp.lower()}|{osn.lower()}|{ip.lower()}")
        if fp in seen_fp:
            return
        seen_fp.add(fp)
        aid = artifact_id("inventory_row", f"{chunk.chunk_id}|{row_idx}|{fp}")
        fact = f"Computer {comp} has IP address {ip} and is running {osn}."
        out.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="inventory_row",
                text=f"Inventory row from '{path}': {fact}",
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "inventory_row",
                    "artifact_generation": "deterministic",
                    "extraction_method": method,
                    "retrieval_mode": "local",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "path": path,
                    "source_root": common.get("source_root", ""),
                    "semantic_domain": common.get("semantic_domain", ""),
                    "first_bucket": common.get("first_bucket", ""),
                    "sub_bucket": common.get("sub_bucket", ""),
                    "computer": comp,
                    "ip_address": ip,
                    "operating_system": osn,
                    "row_index": int(row_idx),
                },
                vector=None,
            )
        )

    for i, line in enumerate(rows):
        header = _inventory_header_map(_split_tabular_cells(line))
        if not header:
            continue
        expected_min = max(int(v) for v in header.values()) + 1
        for row_cells in _iter_tabular_rows(
            rows,
            i + 1,
            expected_min_cells=expected_min,
            max_rows=int(_MAX_INVENTORY_ROW_ARTIFACTS_PER_CHUNK),
        ):
            _append_row(
                row_cells[header["computer"]],
                row_cells[header["os"]],
                row_cells[header["ip"]],
                i,
                "header_table_inventory_row",
            )
            if len(out) >= int(_MAX_INVENTORY_ROW_ARTIFACTS_PER_CHUNK):
                return out

    normalized = re.sub(r"\s+", " ", text)
    for i, m in enumerate(_INVENTORY_ROW_FALLBACK_RE.finditer(normalized)):
        _append_row(
            str(m.group("computer") or ""),
            str(m.group("os") or ""),
            str(m.group("ip") or ""),
            i,
            "regex_inventory_row",
        )
        if len(out) >= int(_MAX_INVENTORY_ROW_ARTIFACTS_PER_CHUNK):
            break
    return out


def build_account_status_row_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    rows = [str(x or "").strip() for x in str(chunk.text or "").splitlines() if str(x or "").strip()]
    out: List[GraphArtifact] = []
    seen_fp: set[str] = set()

    def _append_row(username: str, status: str, locked_date: str, service: str, row_idx: int, method: str) -> None:
        user = str(username or "").strip()
        st = str(status or "").strip()
        locked = str(locked_date or "").strip()
        svc = _clean_service_value(service)
        if not user or not st or not locked or not svc:
            return
        if len(out) >= int(_MAX_ACCOUNT_STATUS_ROW_ARTIFACTS_PER_CHUNK):
            return
        fp = sha256_hex(f"{user.lower()}|{st.lower()}|{locked.lower()}|{svc.lower()}")
        if fp in seen_fp:
            return
        seen_fp.add(fp)
        aid = artifact_id("account_status_row", f"{chunk.chunk_id}|{row_idx}|{fp}")
        fact = f"Username {user} has status {st}, locked date {locked}, and listed service {svc}."
        out.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="account_status_row",
                text=f"Account status row from '{path}': {fact}",
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "account_status_row",
                    "artifact_generation": "deterministic",
                    "extraction_method": method,
                    "retrieval_mode": "local",
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "path": path,
                    "source_root": common.get("source_root", ""),
                    "semantic_domain": common.get("semantic_domain", ""),
                    "first_bucket": common.get("first_bucket", ""),
                    "sub_bucket": common.get("sub_bucket", ""),
                    "username": user,
                    "status": st,
                    "locked_date": locked,
                    "service": svc,
                    "row_index": int(row_idx),
                },
                vector=None,
            )
        )

    for i, line in enumerate(rows):
        header = _account_header_map(_split_tabular_cells(line))
        if not header:
            continue
        expected_min = max(int(v) for v in header.values()) + 1
        for row_cells in _iter_tabular_rows(
            rows,
            i + 1,
            expected_min_cells=expected_min,
            max_rows=int(_MAX_ACCOUNT_STATUS_ROW_ARTIFACTS_PER_CHUNK),
        ):
            _append_row(
                row_cells[header["username"]],
                row_cells[header["status"]],
                row_cells[header["locked"]],
                row_cells[header["service"]],
                i,
                "header_table_account_status_row",
            )
            if len(out) >= int(_MAX_ACCOUNT_STATUS_ROW_ARTIFACTS_PER_CHUNK):
                return out

    normalized = re.sub(r"\s+", " ", text)
    for i, m in enumerate(_ACCOUNT_STATUS_LABELS_RE.finditer(normalized)):
        _append_row(
            str(m.group("username") or ""),
            str(m.group("status") or ""),
            str(m.group("locked") or ""),
            str(m.group("service") or ""),
            i,
            "labels_account_status_row",
        )
        if len(out) >= int(_MAX_ACCOUNT_STATUS_ROW_ARTIFACTS_PER_CHUNK):
            return out
    for i, m in enumerate(_ACCOUNT_STATUS_DELIMITED_RE.finditer(normalized)):
        _append_row(
            str(m.group("username") or ""),
            str(m.group("status") or ""),
            str(m.group("locked") or ""),
            str(m.group("service") or ""),
            i,
            "delimited_account_status_row",
        )
        if len(out) >= int(_MAX_ACCOUNT_STATUS_ROW_ARTIFACTS_PER_CHUNK):
            break
    return out


def build_procedure_requirement_artifacts(chunk: ChunkDoc) -> List[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return []
    common = _chunk_common_metadata(chunk)
    path = str(common.get("path", ""))
    sentences = _split_lines_and_sentences(text)
    prereq = ""
    site = ""
    for sent in sentences:
        if not prereq and _PROCEDURE_REQUIREMENT_PREREQ_RE.search(sent):
            prereq = re.sub(r"\s+", " ", sent).strip()
        if not site and _PROCEDURE_REQUIREMENT_SITE_RE.search(sent):
            site = re.sub(r"\s+", " ", sent).strip()
        if prereq and site:
            break
    if not prereq or not site:
        return []
    fp = sha256_hex(f"{chunk.chunk_id}|{prereq.lower()}|{site.lower()}")
    aid = artifact_id("procedure_requirement", fp)
    return [
        GraphArtifact(
            artifact_id=aid,
            artifact_type="procedure_requirement",
            text=f"Procedure requirement pair from '{path}': {prereq} {site}",
            metadata={
                "artifact_id": aid,
                "artifact_type": "procedure_requirement",
                "artifact_generation": "deterministic",
                "extraction_method": "paired_procedure_requirement",
                "retrieval_mode": "local",
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "path": path,
                "source_root": common.get("source_root", ""),
                "semantic_domain": common.get("semantic_domain", ""),
                "first_bucket": common.get("first_bucket", ""),
                "sub_bucket": common.get("sub_bucket", ""),
                "pre_start_requirement": prereq,
                "site_availability_requirement": site,
            },
            vector=None,
        )
    ]


def build_troubleshooting_tuple_artifact(chunk: ChunkDoc) -> Optional[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return None
    fields = _extract_troubleshooting_fields(text)
    symptom = str(fields.get("symptom", "")).strip()
    cause = str(fields.get("cause", "")).strip()
    remediation = str(fields.get("remediation", "")).strip()
    action = str(fields.get("action", "")).strip()
    outcome = str(fields.get("outcome", "")).strip()
    timestamp = str(fields.get("timestamp", "")).strip()
    if not symptom and not cause and not remediation and not action:
        return None

    path = str(chunk.metadata.get("path", "")).strip()
    domain = str(chunk.metadata.get("semantic_domain", "")).strip()
    first_bucket = str(chunk.metadata.get("first_bucket", "")).strip()
    sub_bucket = str(chunk.metadata.get("sub_bucket", "")).strip()
    tuple_key = f"{chunk.chunk_id}|{symptom[:120]}|{cause[:120]}|{action[:120]}|{remediation[:120]}"
    aid = artifact_id("troubleshooting", tuple_key)

    tuple_text = (
        f"Troubleshooting tuple for '{path}'. "
        f"Symptom: {symptom or 'not explicit'}. "
        f"Cause: {cause or 'not explicit'}. "
        f"Action: {action or 'not explicit'}. "
        f"Remediation: {remediation or 'not explicit'}. "
        f"Outcome: {outcome or 'not explicit'}. "
        f"Timestamp: {timestamp or 'not explicit'}."
    )
    return GraphArtifact(
        artifact_id=aid,
        artifact_type="troubleshooting_tuple",
        text=tuple_text,
        metadata={
            "artifact_id": aid,
            "artifact_type": "troubleshooting_tuple",
            "artifact_generation": "deterministic",
            "extraction_method": "heuristic_troubleshooting_tuple",
            "retrieval_mode": "local",
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "path": path,
            "semantic_domain": domain,
            "first_bucket": first_bucket,
            "sub_bucket": sub_bucket,
            "symptom": symptom,
            "cause": cause,
            "action": action,
            "outcome": outcome,
            "timestamp": timestamp,
            "remediation": remediation,
        },
        vector=None,
    )


def build_troubleshooting_event_artifact(chunk: ChunkDoc) -> Optional[GraphArtifact]:
    text = str(chunk.text or "").strip()
    if not text:
        return None
    fields = _extract_troubleshooting_fields(text)
    symptom = str(fields.get("symptom", "")).strip()
    cause = str(fields.get("cause", "")).strip()
    action = str(fields.get("action", "")).strip()
    outcome = str(fields.get("outcome", "")).strip()
    timestamp = str(fields.get("timestamp", "")).strip()
    remediation = str(fields.get("remediation", "")).strip()
    if not symptom and not cause and not action and not remediation:
        return None

    path = str(chunk.metadata.get("path", "")).strip()
    domain = str(chunk.metadata.get("semantic_domain", "")).strip()
    first_bucket = str(chunk.metadata.get("first_bucket", "")).strip()
    sub_bucket = str(chunk.metadata.get("sub_bucket", "")).strip()
    ev_key = f"{chunk.chunk_id}|{symptom[:120]}|{cause[:120]}|{action[:120]}|{outcome[:120]}|{timestamp[:64]}"
    aid = artifact_id("troubleshooting_event", ev_key)
    event_text = (
        f"Troubleshooting event for '{path}'. "
        f"Symptom: {symptom or 'not explicit'}. "
        f"Cause: {cause or 'not explicit'}. "
        f"Action: {action or 'not explicit'}. "
        f"Outcome: {outcome or 'not explicit'}. "
        f"Timestamp: {timestamp or 'not explicit'}."
    )
    return GraphArtifact(
        artifact_id=aid,
        artifact_type="troubleshooting_event",
        text=event_text,
        metadata={
            "artifact_id": aid,
            "artifact_type": "troubleshooting_event",
            "artifact_generation": "deterministic",
            "extraction_method": "heuristic_troubleshooting_event",
            "retrieval_mode": "local",
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "path": path,
            "semantic_domain": domain,
            "first_bucket": first_bucket,
            "sub_bucket": sub_bucket,
            "symptom": symptom,
            "cause": cause,
            "action": action,
            "outcome": outcome,
            "timestamp": timestamp,
            "remediation": remediation,
        },
        vector=None,
    )


def build_core_graphrag_artifacts(doc_records: Sequence[Dict[str, Any]], chunks: Sequence[ChunkDoc]) -> List[GraphArtifact]:
    artifacts: List[GraphArtifact] = []

    for c in chunks:
        meta = {
            "artifact_id": artifact_id("text_unit", c.chunk_id),
            "artifact_type": "text_unit",
            "artifact_generation": "deterministic",
            "extraction_method": "metadata_pipeline",
            "retrieval_mode": "basic",
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "path": c.metadata.get("path", ""),
            "source_root": c.metadata.get("source_root", ""),
            "semantic_domain": c.metadata.get("semantic_domain", ""),
            "semantic_decision": c.metadata.get("semantic_decision", ""),
            "first_bucket": c.metadata.get("first_bucket", ""),
            "sub_bucket": c.metadata.get("sub_bucket", ""),
            "mtime_date": c.metadata.get("mtime_date", ""),
            "effective_date": c.metadata.get("effective_date", "") or c.metadata.get("mtime_date", ""),
            "mtime_year": c.metadata.get("mtime_year", -1),
            "effective_year": c.metadata.get("effective_year", c.metadata.get("mtime_year", -1)),
            "mtime_epoch_day": c.metadata.get("mtime_epoch_day", -1),
            "effective_epoch_day": c.metadata.get("effective_epoch_day", c.metadata.get("mtime_epoch_day", -1)),
        }
        artifacts.append(
            GraphArtifact(
                artifact_id=str(meta["artifact_id"]),
                artifact_type="text_unit",
                text=c.text,
                metadata=meta,
                vector=None,
            )
        )
        troubleshoot_artifact = build_troubleshooting_tuple_artifact(c)
        if troubleshoot_artifact is not None:
            artifacts.append(troubleshoot_artifact)
        troubleshoot_event_artifact = build_troubleshooting_event_artifact(c)
        if troubleshoot_event_artifact is not None:
            artifacts.append(troubleshoot_event_artifact)
        artifacts.extend(build_log_entry_artifacts(c))
        artifacts.extend(build_procedure_step_artifacts(c))
        artifacts.extend(build_procedure_requirement_artifacts(c))
        artifacts.extend(build_compliance_constraint_artifacts(c))
        artifacts.extend(build_risk_signal_artifacts(c))
        artifacts.extend(build_inventory_row_artifacts(c))
        artifacts.extend(build_account_status_row_artifacts(c))

    entity_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rec in doc_records:
        entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
        for label, key in ENTITY_FIELDS:
            for name in norm_list(entities.get(key)):
                k = (label, name)
                cur = entity_stats.setdefault(
                    k,
                    {
                        "count": 0,
                        "domains": set(),
                        "paths": set(),
                    },
                )
                cur["count"] += 1
                cur["domains"].add(str(rec.get("semantic_domain", "")))
                cur["paths"].add(str(rec.get("path", "")))

    for (label, name), stats in entity_stats.items():
        domains = sorted(d for d in stats["domains"] if d)
        sample_paths = sorted(p for p in stats["paths"] if p)[:5]
        mentions = int(stats["count"])
        text = (
            f"Entity description: {label} '{name}'. "
            f"Mentions={mentions}. Domains={', '.join(domains) if domains else 'unknown'}. "
            f"Sample paths={'; '.join(sample_paths) if sample_paths else 'none'}."
        )
        aid = artifact_id("entity", f"{label}|{name}")
        artifacts.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="entity_description",
                text=text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "entity_description",
                    "artifact_generation": "deterministic",
                    "extraction_method": "metadata_aggregation",
                    "retrieval_mode": "local",
                    "entity_label": label,
                    "entity_name": name,
                    "entity_mentions": mentions,
                    "semantic_domain": domains[0] if domains else "",
                    "domains": domains,
                    "sample_paths": sample_paths,
                },
                vector=None,
            )
        )

    for rec in doc_records:
        entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
        doc_id = str(rec.get("doc_id", ""))
        path = str(rec.get("path", ""))
        domain = str(rec.get("semantic_domain", ""))
        first_bucket = str(rec.get("first_bucket", ""))
        sub_bucket = str(rec.get("sub_bucket", ""))
        for label, key in ENTITY_FIELDS:
            for name in norm_list(entities.get(key)):
                rel_key = f"{doc_id}|{label}|{name}"
                aid = artifact_id("relationship", rel_key)
                text = f"Relationship: document '{path}' (domain={domain}) references {label} '{name}'."
                artifacts.append(
                    GraphArtifact(
                        artifact_id=aid,
                        artifact_type="relationship",
                        text=text,
                        metadata={
                            "artifact_id": aid,
                            "artifact_type": "relationship",
                            "artifact_generation": "deterministic",
                            "extraction_method": "metadata_doc_entity",
                            "retrieval_mode": "local",
                            "relation_type": "DOCUMENT_REFERENCES_ENTITY",
                            "src_type": "document",
                            "src_id": doc_id,
                            "dst_type": label,
                            "dst_id": f"{label}:{name}",
                            "entity_label": label,
                            "entity_name": name,
                            "path": path,
                            "semantic_domain": domain,
                            "first_bucket": first_bucket,
                            "sub_bucket": sub_bucket,
                            "mtime_date": str(rec.get("mtime_date", "") or ""),
                            "effective_date": str(rec.get("effective_date", "") or rec.get("mtime_date", "") or ""),
                            "mtime_year": int(rec.get("mtime_year", -1) or -1),
                            "effective_year": int(rec.get("effective_year", rec.get("mtime_year", -1)) or -1),
                            "mtime_epoch_day": int(rec.get("mtime_epoch_day", -1) or -1),
                            "effective_epoch_day": int(rec.get("effective_epoch_day", rec.get("mtime_epoch_day", -1)) or -1),
                        },
                        vector=None,
                    )
                )

    by_domain: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in doc_records:
        by_domain[str(rec.get("semantic_domain", "general") or "general")].append(rec)

    for domain, rows in by_domain.items():
        doc_count = len(rows)
        chunk_count = sum(int(r.get("chunk_count", 0) or 0) for r in rows)
        bucket_counts: Dict[str, int] = defaultdict(int)
        tag_counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            bucket_counts[str(r.get("first_bucket", ""))] += 1
            for tag in norm_list(r.get("tags")):
                tag_counts[tag] += 1
        top_buckets = [k for k, _ in sorted(bucket_counts.items(), key=lambda x: x[1], reverse=True)[:5] if k]
        top_tags = [k for k, _ in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:8] if k]
        span_from, span_to = _temporal_span(
            [str(r.get("effective_date", "") or r.get("mtime_date", "") or "") for r in rows]
        )
        span_years = _temporal_span_years(span_from, span_to)
        aid = artifact_id("community", domain)
        text = (
            f"Community report for domain '{domain}'. "
            f"Documents={doc_count}, Chunks={chunk_count}. "
            f"Top first buckets={', '.join(top_buckets) if top_buckets else 'n/a'}. "
            f"Top tags={', '.join(top_tags) if top_tags else 'n/a'}."
        )
        artifacts.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="community_report",
                text=text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "community_report",
                    "artifact_generation": "deterministic",
                    "extraction_method": "domain_rollup",
                    "retrieval_mode": "global",
                    "community_id": f"domain:{domain}",
                    "community_scope": "domain",
                    "community_method": "domain_bucket",
                    "semantic_domain": domain,
                    "doc_count": doc_count,
                    "chunk_count": chunk_count,
                    "top_buckets": top_buckets,
                    "top_tags": top_tags,
                    "temporal_span_from": span_from,
                    "temporal_span_to": span_to,
                    "temporal_span_years": float(span_years),
                    "effective_date": span_to,
                },
                vector=None,
            )
        )

    for rec in doc_records:
        doc_id = str(rec.get("doc_id", ""))
        path = str(rec.get("path", ""))
        domain = str(rec.get("semantic_domain", ""))
        first_bucket = str(rec.get("first_bucket", ""))
        sub_bucket = str(rec.get("sub_bucket", ""))
        doc_type = str(rec.get("doc_type", ""))
        artifact_type = str(rec.get("artifact_type", ""))
        tags = norm_list(rec.get("tags"))[:12]
        claim_key = f"{doc_id}|{domain}|{first_bucket}|{sub_bucket}|{doc_type}|{artifact_type}"
        aid = artifact_id("claim", claim_key)
        claim_text = (
            f"Claim: document '{path}' belongs to domain '{domain}', first bucket '{first_bucket}', "
            f"sub bucket '{sub_bucket}', doc_type '{doc_type}', artifact_type '{artifact_type}', "
            f"tags={', '.join(tags) if tags else 'none'}."
        )
        artifacts.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="claim",
                text=claim_text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "claim",
                    "artifact_generation": "deterministic",
                    "extraction_method": "routing_assertion",
                    "retrieval_mode": "local",
                    "doc_id": doc_id,
                    "path": path,
                    "semantic_domain": domain,
                    "first_bucket": first_bucket,
                    "sub_bucket": sub_bucket,
                    "doc_type": doc_type,
                    "mtime_date": str(rec.get("mtime_date", "") or ""),
                    "effective_date": str(rec.get("effective_date", "") or rec.get("mtime_date", "") or ""),
                    "mtime_year": int(rec.get("mtime_year", -1) or -1),
                    "effective_year": int(rec.get("effective_year", rec.get("mtime_year", -1)) or -1),
                    "mtime_epoch_day": int(rec.get("mtime_epoch_day", -1) or -1),
                    "effective_epoch_day": int(rec.get("effective_epoch_day", rec.get("mtime_epoch_day", -1)) or -1),
                },
                vector=None,
            )
        )

    return artifacts


def build_llm_domain_community_reports(
    chat: Any,
    domain_inputs: Sequence[Dict[str, Any]],
    *,
    system_prompt: str,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    prompt_text = str(system_prompt or DEFAULT_LLM_COMMUNITY_SYSTEM_PROMPT)
    for row in domain_inputs:
        domain = str(row.get("semantic_domain", "")).strip()
        if not domain:
            continue
        payload = {
            "task": "domain_community_summary",
            "domain": domain,
            "evidence": row,
            "style": {
                "format": "multi_paragraph",
                "sections": ["overview", "operations_and_architecture", "risks_and_actions"],
            },
        }
        try:
            resp = chat.complete(
                [
                    {"role": "system", "content": prompt_text},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
                ],
                temperature=float(temperature),
                top_p=float(top_p),
                max_completion_tokens=int(max_completion_tokens),
            )
            parsed = extract_json_object(chat.extract_text(resp))
        except Exception as exc:
            LOGGER.warning("LLM community summary failed for domain=%s: %s", domain, exc)
            continue
        if not parsed:
            continue
        summary = str(parsed.get("summary", "")).strip()
        if not summary:
            continue
        out[domain] = {
            "summary": summary,
            "key_entities": norm_list(parsed.get("key_entities")),
            "key_relationships": norm_list(parsed.get("key_relationships")),
            "key_claims": norm_list(parsed.get("key_claims")),
            "open_questions": norm_list(parsed.get("open_questions")),
        }
    return out


def build_llm_graph_community_reports(
    chat: Any,
    community_inputs: Sequence[Dict[str, Any]],
    *,
    system_prompt: str,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    prompt_text = str(system_prompt or DEFAULT_LLM_COMMUNITY_SYSTEM_PROMPT)
    for row in community_inputs:
        community_id = str(row.get("community_id", "")).strip()
        if not community_id:
            continue
        payload = {
            "task": "graph_community_summary",
            "community_id": community_id,
            "evidence": row,
            "style": {
                "format": "multi_paragraph",
                "sections": ["overview", "structure_and_dependencies", "risks_and_actions"],
            },
        }
        try:
            resp = chat.complete(
                [
                    {"role": "system", "content": prompt_text},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
                ],
                temperature=float(temperature),
                top_p=float(top_p),
                max_completion_tokens=int(max_completion_tokens),
            )
            parsed = extract_json_object(chat.extract_text(resp))
        except Exception as exc:
            LOGGER.warning("LLM graph community summary failed for community_id=%s: %s", community_id, exc)
            continue
        if not parsed:
            continue
        summary = str(parsed.get("summary", "")).strip()
        if not summary:
            continue
        out[community_id] = {
            "summary": summary,
            "key_entities": norm_list(parsed.get("key_entities")),
            "key_relationships": norm_list(parsed.get("key_relationships")),
            "key_claims": norm_list(parsed.get("key_claims")),
            "open_questions": norm_list(parsed.get("open_questions")),
        }
    return out


def build_llm_graphrag_artifacts(
    chat: Any,
    doc_records: Sequence[Dict[str, Any]],
    chunks: Sequence[ChunkDoc],
    *,
    families: Set[str],
    extraction_batch_size: int,
    max_chunks: int,
    max_chars_per_chunk: int,
    max_entities_per_chunk: int,
    max_relationships_per_chunk: int,
    max_claims_per_chunk: int,
    max_gleanings: int,
    max_entities_total: int,
    max_relationships_total: int,
    max_claims_total: int,
    community_max_domains: int,
    community_max_evidence_items: int,
    extraction_system_prompt: str,
    community_system_prompt: str,
    llm_temperature: float,
    llm_top_p: float,
    llm_max_completion_tokens: int,
) -> Tuple[List[GraphArtifact], Dict[str, Any]]:
    if not chunks:
        return [], {"enabled": False, "reason": "no_chunks"}

    docs_by_id: Dict[str, Dict[str, Any]] = {
        str(r.get("doc_id", "")).strip(): r
        for r in doc_records
        if str(r.get("doc_id", "")).strip()
    }
    selected_chunks: List[ChunkDoc] = list(chunks)
    if int(max_chunks) > 0:
        selected_chunks = selected_chunks[: int(max_chunks)]

    entity_agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rel_agg: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    claim_agg: Dict[str, Dict[str, Any]] = {}
    domains_seen: Counter[str] = Counter()
    parse_failures = 0
    extraction_calls = 0

    batch_size = max(1, int(extraction_batch_size))
    for i in range(0, len(selected_chunks), batch_size):
        batch = selected_chunks[i : i + batch_size]
        extraction_calls += 1
        try:
            extracted = extract_chunk_semantics_llm(
                chat,
                batch,
                max_chars_per_chunk=int(max_chars_per_chunk),
                max_entities_per_chunk=int(max_entities_per_chunk),
                max_relationships_per_chunk=int(max_relationships_per_chunk),
                max_claims_per_chunk=int(max_claims_per_chunk),
                max_gleanings=int(max_gleanings),
                system_prompt=str(extraction_system_prompt or DEFAULT_LLM_EXTRACTION_SYSTEM_PROMPT),
                temperature=float(llm_temperature),
                top_p=float(llm_top_p),
                max_completion_tokens=int(llm_max_completion_tokens),
            )
        except Exception as exc:
            LOGGER.warning("LLM extraction batch failed (%d chunks): %s", len(batch), exc)
            parse_failures += len(batch)
            extracted = {}

        for c in batch:
            row = extracted.get(c.chunk_id) or {}
            doc_meta = docs_by_id.get(c.doc_id) or {}
            domain = str(c.metadata.get("semantic_domain", "")).strip() or str(doc_meta.get("semantic_domain", "")).strip() or "general"
            path = str(c.metadata.get("path", "")).strip() or str(doc_meta.get("path", "")).strip()
            first_bucket = str(c.metadata.get("first_bucket", "")).strip() or str(doc_meta.get("first_bucket", "")).strip()
            sub_bucket = str(c.metadata.get("sub_bucket", "")).strip() or str(doc_meta.get("sub_bucket", "")).strip()
            domains_seen[domain] += 1

            entities = row.get("entities") if isinstance(row.get("entities"), list) else []
            for ent in entities:
                if not isinstance(ent, dict):
                    continue
                name = str(ent.get("name", "")).strip()
                if not name:
                    continue
                label = _normalize_entity_label(str(ent.get("type", "")))
                desc = str(ent.get("description", "")).strip()
                key = (label, name.lower())
                cur = entity_agg.setdefault(
                    key,
                    {
                        "label": label,
                        "name": name,
                        "mentions": 0,
                        "domains": Counter(),
                        "paths": set(),
                        "descriptions": [],
                        "chunk_ids": set(),
                    },
                )
                cur["mentions"] += 1
                cur["domains"][domain] += 1
                if path:
                    cur["paths"].add(path)
                cur["chunk_ids"].add(c.chunk_id)
                if desc:
                    cur["descriptions"].append(desc)

            rels = row.get("relationships") if isinstance(row.get("relationships"), list) else []
            for rel in rels:
                if not isinstance(rel, dict):
                    continue
                src = str(rel.get("source", "")).strip()
                dst = str(rel.get("target", "")).strip()
                if not src or not dst:
                    continue
                rel_type = _normalize_rel_type(str(rel.get("relationship_type", "")))
                desc = str(rel.get("description", "")).strip()
                key = (src.lower(), rel_type, dst.lower())
                cur = rel_agg.setdefault(
                    key,
                    {
                        "source": src,
                        "target": dst,
                        "relation_type": rel_type,
                        "count": 0,
                        "domains": Counter(),
                        "paths": set(),
                        "descriptions": [],
                        "chunk_ids": set(),
                    },
                )
                cur["count"] += 1
                cur["domains"][domain] += 1
                if path:
                    cur["paths"].add(path)
                cur["chunk_ids"].add(c.chunk_id)
                if desc:
                    cur["descriptions"].append(desc)

            claims = row.get("claims") if isinstance(row.get("claims"), list) else []
            for cl in claims:
                if not isinstance(cl, dict):
                    continue
                subj = str(cl.get("subject", "")).strip()
                pred = str(cl.get("predicate", "")).strip()
                obj = str(cl.get("object", "")).strip()
                if not subj or not pred:
                    continue
                claim_type = str(cl.get("claim_type", "")).strip().lower() or "fact"
                status = str(cl.get("status", "")).strip().lower() or "unknown"
                date_txt = str(cl.get("date", "")).strip()
                evidence = str(cl.get("evidence", "")).strip()
                ck = "|".join(
                    [
                        domain.lower(),
                        subj.lower(),
                        pred.lower(),
                        obj.lower(),
                        path.lower(),
                        c.chunk_id.lower(),
                    ]
                )
                claim_agg[ck] = {
                    "subject": subj,
                    "predicate": pred,
                    "object": obj,
                    "claim_type": claim_type,
                    "status": status,
                    "date": date_txt,
                    "evidence": evidence,
                    "semantic_domain": domain,
                    "path": path,
                    "doc_id": c.doc_id,
                    "chunk_id": c.chunk_id,
                    "first_bucket": first_bucket,
                    "sub_bucket": sub_bucket,
                }

        if (i // batch_size) % 50 == 0:
            LOGGER.info(
                "LLM extraction progress: %d/%d chunks (entities=%d relationships=%d claims=%d parse_failures=%d)",
                min(i + len(batch), len(selected_chunks)),
                len(selected_chunks),
                len(entity_agg),
                len(rel_agg),
                len(claim_agg),
                parse_failures,
            )

    artifacts: List[GraphArtifact] = []
    model_name = str(getattr(getattr(chat, "cfg", None), "model", "llm"))
    method_name = "llm_extraction"

    if "entity_description" in families:
        for _, row in apply_cap(list(entity_agg.items()), int(max_entities_total)):
            domains = [k for k, _ in row["domains"].most_common(5)]
            dominant_domain = domains[0] if domains else ""
            sample_paths = sorted(str(x) for x in row["paths"])[:8]
            descriptions = [str(x) for x in row["descriptions"] if str(x).strip()]
            summary = descriptions[0] if descriptions else f"LLM extracted entity {row['label']} '{row['name']}'."
            if len(descriptions) > 1:
                summary = summary + " " + " ".join(descriptions[1:3])
            aid = artifact_id("entity_llm", f"{row['label']}|{row['name']}")
            artifacts.append(
                GraphArtifact(
                    artifact_id=aid,
                    artifact_type="entity_description",
                    text=(
                        f"LLM entity profile: {row['label']} '{row['name']}'. "
                        f"{summary} Mentions={int(row['mentions'])}. "
                        f"Domains={', '.join(domains) if domains else 'unknown'}."
                    ),
                    metadata={
                        "artifact_id": aid,
                        "artifact_type": "entity_description",
                        "artifact_generation": "llm",
                        "extraction_method": method_name,
                        "extraction_model": model_name,
                        "retrieval_mode": "local",
                        "entity_label": row["label"],
                        "entity_name": row["name"],
                        "entity_mentions": int(row["mentions"]),
                        "semantic_domain": dominant_domain,
                        "domains": domains,
                        "sample_paths": sample_paths,
                        "chunk_ids": sorted(str(x) for x in row["chunk_ids"])[:40],
                    },
                    vector=None,
                )
            )

    if "relationship" in families:
        for _, row in apply_cap(list(rel_agg.items()), int(max_relationships_total)):
            domains = [k for k, _ in row["domains"].most_common(4)]
            dominant_domain = domains[0] if domains else ""
            sample_paths = sorted(str(x) for x in row["paths"])[:8]
            descriptions = [str(x) for x in row["descriptions"] if str(x).strip()]
            desc = descriptions[0] if descriptions else ""
            rel_key = f"{row['source']}|{row['relation_type']}|{row['target']}"
            aid = artifact_id("relationship_llm", rel_key)
            artifacts.append(
                GraphArtifact(
                    artifact_id=aid,
                    artifact_type="relationship",
                    text=(
                        f"LLM relationship: {row['source']} {row['relation_type']} {row['target']}. "
                        f"{desc}".strip()
                    ),
                    metadata={
                        "artifact_id": aid,
                        "artifact_type": "relationship",
                        "artifact_generation": "llm",
                        "extraction_method": method_name,
                        "extraction_model": model_name,
                        "retrieval_mode": "local",
                        "relation_type": row["relation_type"],
                        "src_type": "entity",
                        "src_id": row["source"],
                        "dst_type": "entity",
                        "dst_id": row["target"],
                        "semantic_domain": dominant_domain,
                        "domains": domains,
                        "path": sample_paths[0] if sample_paths else "",
                        "sample_paths": sample_paths,
                        "chunk_ids": sorted(str(x) for x in row["chunk_ids"])[:40],
                        "relationship_mentions": int(row["count"]),
                    },
                    vector=None,
                )
            )

    if "claim" in families:
        claim_rows = apply_cap(list(claim_agg.values()), int(max_claims_total))
        for row in claim_rows:
            claim_key = "|".join(
                [
                    str(row.get("semantic_domain", "")),
                    str(row.get("subject", "")),
                    str(row.get("predicate", "")),
                    str(row.get("object", "")),
                    str(row.get("path", "")),
                    str(row.get("chunk_id", "")),
                ]
            )
            aid = artifact_id("claim_llm", claim_key)
            claim_text = (
                f"LLM claim: {row.get('subject','')} {row.get('predicate','')} {row.get('object','')}. "
                f"Type={row.get('claim_type','')}, status={row.get('status','')}, date={row.get('date','')}. "
                f"Evidence: {row.get('evidence','')}"
            )
            artifacts.append(
                GraphArtifact(
                    artifact_id=aid,
                    artifact_type="claim",
                    text=claim_text.strip(),
                    metadata={
                        "artifact_id": aid,
                        "artifact_type": "claim",
                        "artifact_generation": "llm",
                        "extraction_method": method_name,
                        "extraction_model": model_name,
                        "retrieval_mode": "local",
                        "claim_subject": row.get("subject", ""),
                        "claim_predicate": row.get("predicate", ""),
                        "claim_object": row.get("object", ""),
                        "claim_type": row.get("claim_type", ""),
                        "claim_status": row.get("status", ""),
                        "claim_date": row.get("date", ""),
                        "doc_id": row.get("doc_id", ""),
                        "chunk_id": row.get("chunk_id", ""),
                        "path": row.get("path", ""),
                        "semantic_domain": row.get("semantic_domain", ""),
                        "first_bucket": row.get("first_bucket", ""),
                        "sub_bucket": row.get("sub_bucket", ""),
                    },
                    vector=None,
                )
            )

    llm_domain_reports = 0
    if "community_report_domain" in families:
        evidence_by_domain: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"paths": set(), "entities": Counter(), "relationships": Counter(), "claims": []})
        for row in entity_agg.values():
            domains = row["domains"]
            if not domains:
                continue
            d = domains.most_common(1)[0][0]
            evidence_by_domain[d]["entities"][f"{row['label']}:{row['name']}"] += int(row["mentions"])
            for p in row["paths"]:
                evidence_by_domain[d]["paths"].add(str(p))
        for row in rel_agg.values():
            domains = row["domains"]
            if not domains:
                continue
            d = domains.most_common(1)[0][0]
            evidence_by_domain[d]["relationships"][f"{row['source']} {row['relation_type']} {row['target']}"] += int(row["count"])
            for p in row["paths"]:
                evidence_by_domain[d]["paths"].add(str(p))
        for row in claim_agg.values():
            d = str(row.get("semantic_domain", "")).strip() or "general"
            claim_text = f"{row.get('subject','')} {row.get('predicate','')} {row.get('object','')}".strip()
            if claim_text:
                evidence_by_domain[d]["claims"].append(claim_text)
            p = str(row.get("path", "")).strip()
            if p:
                evidence_by_domain[d]["paths"].add(p)

        domain_rows: List[Dict[str, Any]] = []
        for domain, ev in evidence_by_domain.items():
            if not domain:
                continue
            domain_rows.append(
                {
                    "semantic_domain": domain,
                    "top_entities": [k for k, _ in ev["entities"].most_common(20)],
                    "top_relationships": [k for k, _ in ev["relationships"].most_common(20)],
                    "top_claims": list(ev["claims"])[: int(community_max_evidence_items)],
                    "sample_paths": sorted(str(x) for x in ev["paths"])[: int(community_max_evidence_items)],
                }
            )
        domain_rows = apply_cap(
            sorted(domain_rows, key=lambda r: str(r.get("semantic_domain", ""))),
            int(community_max_domains),
        )

        summaries = build_llm_domain_community_reports(
            chat,
            domain_rows,
            system_prompt=str(community_system_prompt or DEFAULT_LLM_COMMUNITY_SYSTEM_PROMPT),
            max_completion_tokens=int(llm_max_completion_tokens),
            temperature=float(llm_temperature),
            top_p=float(llm_top_p),
        )
        for domain_row in domain_rows:
            domain = str(domain_row.get("semantic_domain", ""))
            summary = summaries.get(domain) or {}
            txt = str(summary.get("summary", "")).strip()
            if not txt:
                continue
            aid = artifact_id("community_domain_llm", domain)
            artifacts.append(
                GraphArtifact(
                    artifact_id=aid,
                    artifact_type="community_report",
                    text=txt,
                    metadata={
                        "artifact_id": aid,
                        "artifact_type": "community_report",
                        "artifact_generation": "llm",
                        "extraction_method": "llm_domain_summary",
                        "extraction_model": model_name,
                        "retrieval_mode": "global",
                        "community_id": f"domain_llm:{domain}",
                        "community_scope": "domain",
                        "community_method": "llm",
                        "semantic_domain": domain,
                        "top_entities": norm_list(summary.get("key_entities")),
                        "top_relationships": norm_list(summary.get("key_relationships")),
                        "top_claims": norm_list(summary.get("key_claims")),
                        "open_questions": norm_list(summary.get("open_questions")),
                        "sample_paths": norm_list(domain_row.get("sample_paths")),
                    },
                    vector=None,
                )
            )
            llm_domain_reports += 1

    stats = {
        "enabled": True,
        "families": sorted(families),
        "chunks_considered": len(selected_chunks),
        "extraction_calls": extraction_calls,
        "max_gleanings": int(max_gleanings),
        "parse_failures": parse_failures,
        "entity_candidates": len(entity_agg),
        "relationship_candidates": len(rel_agg),
        "claim_candidates": len(claim_agg),
        "llm_domain_reports": llm_domain_reports,
    }
    return artifacts, stats


def load_json(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return obj if isinstance(obj, dict) else {}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def guess_first_bucket_domain(cards: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    by_first: Dict[str, Counter[str]] = defaultdict(Counter)
    for row in cards:
        first_bucket = str(row.get("first_bucket", "")).strip()
        domain_hint = str(row.get("domain_hint", "")).strip()
        if first_bucket and domain_hint:
            by_first[first_bucket][domain_hint] += 1
    out: Dict[str, str] = {}
    for first_bucket, counts in by_first.items():
        if not counts:
            continue
        out[first_bucket] = counts.most_common(1)[0][0]
    return out


def build_taxonomy_artifacts(router_obj: Dict[str, Any], cards: Sequence[Dict[str, Any]]) -> List[GraphArtifact]:
    artifacts: List[GraphArtifact] = []
    card_by_sub: Dict[str, Dict[str, Any]] = {}
    for row in cards:
        sub_bucket = str(row.get("sub_bucket", "")).strip()
        if sub_bucket and sub_bucket not in card_by_sub:
            card_by_sub[sub_bucket] = row

    first_domain_hint = guess_first_bucket_domain(cards)
    first_buckets = router_obj.get("first_buckets")
    if not isinstance(first_buckets, list):
        first_buckets = []

    for fb in first_buckets:
        if not isinstance(fb, dict):
            continue
        first_bucket = str(fb.get("id", "")).strip()
        if not first_bucket:
            continue
        fb_keywords = norm_list(fb.get("keywords"))[:24]
        fb_prefixes = norm_list(fb.get("include_path_prefixes"))[:8]
        fb_paths = norm_list(fb.get("example_paths"))[:8]
        fb_stats = fb.get("stats", {}) if isinstance(fb.get("stats"), dict) else {}
        fb_files = int(fb_stats.get("files", 0) or 0)
        fb_bytes = int(fb_stats.get("bytes", 0) or 0)
        fb_mtime_min = str(fb_stats.get("mtime_min", "") or "").strip()
        fb_mtime_max = str(fb_stats.get("mtime_max", "") or "").strip()
        fb_span_years = _temporal_span_years(fb_mtime_min, fb_mtime_max)
        sub_buckets = fb.get("sub_buckets") if isinstance(fb.get("sub_buckets"), list) else []
        semantic_domain = str(first_domain_hint.get(first_bucket, "")).strip()
        first_text = (
            f"Taxonomy first bucket '{first_bucket}'. Files={fb_files}, bytes={fb_bytes}. "
            f"Sub-buckets={len(sub_buckets)}. "
            f"Temporal span={fb_mtime_min or 'n/a'} to {fb_mtime_max or 'n/a'}. "
            f"Keywords={', '.join(fb_keywords) if fb_keywords else 'n/a'}. "
            f"Path prefixes={'; '.join(fb_prefixes) if fb_prefixes else 'n/a'}."
        )
        first_id = artifact_id("taxonomy_first_bucket", first_bucket)
        artifacts.append(
            GraphArtifact(
                artifact_id=first_id,
                artifact_type="taxonomy_first_bucket",
                text=first_text,
                metadata={
                    "artifact_id": first_id,
                    "artifact_type": "taxonomy_first_bucket",
                    "artifact_generation": "deterministic",
                    "extraction_method": "taxonomy_router",
                    "retrieval_mode": "global",
                    "taxonomy_level": "first_bucket",
                    "taxonomy_id": first_bucket,
                    "first_bucket": first_bucket,
                    "sub_bucket": "",
                    "semantic_domain": semantic_domain,
                    "semantic_decision": "taxonomy",
                    "keywords": fb_keywords,
                    "sample_paths": fb_paths,
                    "temporal_span_from": fb_mtime_min,
                    "temporal_span_to": fb_mtime_max,
                    "temporal_span_years": float(fb_span_years),
                    "effective_date": fb_mtime_max,
                },
                vector=None,
            )
        )

        for sb in sub_buckets:
            if not isinstance(sb, dict):
                continue
            sub_bucket = str(sb.get("id", "")).strip()
            if not sub_bucket:
                continue
            sb_keywords = norm_list(sb.get("keywords"))[:24]
            sb_prefixes = norm_list(sb.get("include_path_prefixes"))[:8]
            sb_paths = norm_list(sb.get("example_paths"))[:8]
            sb_stats = sb.get("stats", {}) if isinstance(sb.get("stats"), dict) else {}
            sb_files = int(sb_stats.get("files", 0) or 0)
            sb_bytes = int(sb_stats.get("bytes", 0) or 0)
            sb_mtime_min = str(sb_stats.get("mtime_min", "") or "").strip()
            sb_mtime_max = str(sb_stats.get("mtime_max", "") or "").strip()
            sb_span_years = _temporal_span_years(sb_mtime_min, sb_mtime_max)
            card = card_by_sub.get(sub_bucket, {})
            if not isinstance(card, dict):
                card = {}
            domain_hint = str(card.get("domain_hint", semantic_domain)).strip()
            description = str(card.get("bucket_description", "")).strip()
            sb_text = (
                f"Taxonomy sub-bucket '{sub_bucket}' under '{first_bucket}'. Files={sb_files}, bytes={sb_bytes}. "
                f"Temporal span={sb_mtime_min or 'n/a'} to {sb_mtime_max or 'n/a'}. "
                f"Description={description or 'n/a'}. "
                f"Keywords={', '.join(sb_keywords) if sb_keywords else 'n/a'}."
            )
            sb_id = artifact_id("taxonomy_sub_bucket", f"{first_bucket}|{sub_bucket}")
            artifacts.append(
                GraphArtifact(
                    artifact_id=sb_id,
                    artifact_type="taxonomy_sub_bucket",
                    text=sb_text,
                    metadata={
                        "artifact_id": sb_id,
                        "artifact_type": "taxonomy_sub_bucket",
                        "artifact_generation": "deterministic",
                        "extraction_method": "taxonomy_router",
                        "retrieval_mode": "local",
                        "taxonomy_level": "sub_bucket",
                        "taxonomy_id": sub_bucket,
                        "first_bucket": first_bucket,
                        "sub_bucket": sub_bucket,
                        "semantic_domain": domain_hint,
                        "semantic_decision": "taxonomy",
                        "keywords": sb_keywords,
                        "sample_paths": sb_paths,
                        "domain_hint": domain_hint,
                        "temporal_span_from": sb_mtime_min,
                        "temporal_span_to": sb_mtime_max,
                        "temporal_span_years": float(sb_span_years),
                        "effective_date": sb_mtime_max,
                    },
                    vector=None,
                )
            )

            if card:
                common_entities = card.get("common_entities", {}) if isinstance(card.get("common_entities"), dict) else {}
                card_keywords = norm_list(card.get("top_keywords"))[:30]
                card_qs = norm_list(card.get("typical_questions"))[:10]
                card_paths = norm_list(card.get("sample_paths"))[:10]
                card_artifacts = norm_list(card.get("allowed_artifact_types"))[:16]
                card_stats = card.get("stats", {}) if isinstance(card.get("stats"), dict) else {}
                card_files = int(card_stats.get("files", 0) or 0)
                card_bytes = int(card_stats.get("bytes", 0) or 0)
                card_mtime_min = str(card_stats.get("mtime_min", sb_mtime_min) or "").strip()
                card_mtime_max = str(card_stats.get("mtime_max", sb_mtime_max) or "").strip()
                card_span_years = _temporal_span_years(card_mtime_min, card_mtime_max)
                input_hash = str(card.get("input_hash", "")).strip()
                card_key = f"{first_bucket}|{sub_bucket}|{input_hash or sha256_hex(description)}"
                card_id = artifact_id("taxonomy_card", card_key)
                card_text = (
                    f"Taxonomy bucket card for '{sub_bucket}' ({first_bucket}). "
                    f"Description: {description or 'n/a'}. "
                    f"Domain hint: {domain_hint or 'n/a'}. "
                    f"Typical questions: {' | '.join(card_qs) if card_qs else 'n/a'}. "
                    f"Top keywords: {', '.join(card_keywords) if card_keywords else 'n/a'}. "
                    f"Allowed artifact types: {', '.join(card_artifacts) if card_artifacts else 'n/a'}. "
                    f"Temporal span={card_mtime_min or 'n/a'} to {card_mtime_max or 'n/a'}. "
                    f"Files={card_files}, bytes={card_bytes}."
                )
                artifacts.append(
                    GraphArtifact(
                        artifact_id=card_id,
                        artifact_type="taxonomy_bucket_card",
                        text=card_text,
                        metadata={
                            "artifact_id": card_id,
                            "artifact_type": "taxonomy_bucket_card",
                            "artifact_generation": "deterministic",
                            "extraction_method": "taxonomy_bucket_cards",
                            "retrieval_mode": "local",
                            "taxonomy_level": "bucket_card",
                            "taxonomy_id": sub_bucket,
                            "taxonomy_card_id": card_id,
                            "first_bucket": first_bucket,
                            "sub_bucket": sub_bucket,
                            "semantic_domain": domain_hint,
                            "semantic_decision": "taxonomy",
                            "domain_hint": domain_hint,
                            "keywords": card_keywords,
                            "sample_paths": card_paths,
                            "typical_questions": card_qs,
                            "allowed_artifact_types": card_artifacts,
                            "bucket_description": description,
                            "common_entities_systems": norm_list(common_entities.get("systems")),
                            "common_entities_vendors": norm_list(common_entities.get("vendors")),
                            "common_entities_projects": norm_list(common_entities.get("projects")),
                            "common_entities_people": norm_list(common_entities.get("people")),
                            "common_entities_locations": norm_list(common_entities.get("locations")),
                            "temporal_span_from": card_mtime_min,
                            "temporal_span_to": card_mtime_max,
                            "temporal_span_years": float(card_span_years),
                            "effective_date": card_mtime_max,
                        },
                        vector=None,
                    )
                )

    domain_stats = (
        router_obj.get("routing", {})
        if isinstance(router_obj.get("routing"), dict)
        else {}
    )
    semantic_stats = (
        domain_stats.get("semantic", {})
        if isinstance(domain_stats.get("semantic"), dict)
        else {}
    )
    domain_rows = (
        semantic_stats.get("domain", {}).get("stats", [])
        if isinstance(semantic_stats.get("domain"), dict)
        else []
    )
    if isinstance(domain_rows, list):
        for row in domain_rows:
            if not isinstance(row, dict):
                continue
            domain_id = str(row.get("id", "")).strip()
            if not domain_id:
                continue
            files = int(row.get("files", 0) or 0)
            bytes_total = int(row.get("bytes", 0) or 0)
            span_from = str(row.get("mtime_min", "") or "").strip()
            span_to = str(row.get("mtime_max", "") or "").strip()
            span_years = float(_temporal_span_years(span_from, span_to))
            effective_date = span_to or span_from
            aid = artifact_id("taxonomy_domain", domain_id)
            text = (
                f"Taxonomy semantic domain profile '{domain_id}'. "
                f"Files={files}, bytes={bytes_total}."
            )
            if span_from or span_to:
                text += (
                    f" Temporal span: {span_from or 'unknown'} to {span_to or 'unknown'}"
                    f" ({span_years:.2f} years)."
                )
            artifacts.append(
                GraphArtifact(
                    artifact_id=aid,
                    artifact_type="taxonomy_domain_profile",
                    text=text,
                    metadata={
                        "artifact_id": aid,
                        "artifact_type": "taxonomy_domain_profile",
                        "artifact_generation": "deterministic",
                        "extraction_method": "taxonomy_router",
                        "retrieval_mode": "global",
                        "taxonomy_level": "semantic_domain",
                        "taxonomy_id": domain_id,
                        "semantic_domain": domain_id,
                        "semantic_decision": "taxonomy",
                        "domain_file_count": files,
                        "domain_bytes": bytes_total,
                        "temporal_span_from": span_from,
                        "temporal_span_to": span_to,
                        "temporal_span_years": span_years,
                        "effective_date": effective_date,
                    },
                    vector=None,
                )
            )

    return artifacts


def upsert_taxonomy_neo4j(
    neo_driver: Any,
    router_obj: Dict[str, Any],
    cards: Sequence[Dict[str, Any]],
    *,
    source_tag: str,
) -> None:
    first_buckets = router_obj.get("first_buckets")
    if not isinstance(first_buckets, list):
        first_buckets = []

    def write_first_bucket(tx: Any, params: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (root:TaxonomyRoot {id: $root_id})
            ON CREATE SET root.created_at = timestamp()
            SET root.updated_at = timestamp(), root.source = $source_tag
            MERGE (f:TaxonomyFirstBucket {id: $first_bucket})
            SET f.name = $name,
                f.keywords = $keywords,
                f.include_path_prefixes = $include_path_prefixes,
                f.example_paths = $example_paths,
                f.file_count = $file_count,
                f.total_bytes = $total_bytes,
                f.temporal_span_from = $temporal_span_from,
                f.temporal_span_to = $temporal_span_to,
                f.temporal_span_years = $temporal_span_years,
                f.source = $source_tag
            MERGE (root)-[:HAS_FIRST_BUCKET]->(f)
            MERGE (b:Bucket {id: $first_bucket})
            SET b.kind = 'first_bucket'
            MERGE (f)-[:ALIGNS_WITH_BUCKET]->(b)
            WITH f
            MATCH (d:Document {first_bucket: $first_bucket})
            MERGE (d)-[:IN_TAXONOMY_FIRST_BUCKET]->(f)
            """,
            **params,
        )

    def write_sub_bucket(tx: Any, params: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (f:TaxonomyFirstBucket {id: $first_bucket})
            MERGE (s:TaxonomySubBucket {id: $sub_bucket})
            SET s.name = $name,
                s.keywords = $keywords,
                s.include_path_prefixes = $include_path_prefixes,
                s.example_paths = $example_paths,
                s.file_count = $file_count,
                s.total_bytes = $total_bytes,
                s.temporal_span_from = $temporal_span_from,
                s.temporal_span_to = $temporal_span_to,
                s.temporal_span_years = $temporal_span_years,
                s.source = $source_tag
            MERGE (f)-[:HAS_SUB_BUCKET]->(s)
            MERGE (b:Bucket {id: $sub_bucket})
            SET b.kind = 'sub_bucket'
            MERGE (s)-[:ALIGNS_WITH_BUCKET]->(b)
            WITH s
            MATCH (d:Document {sub_bucket: $sub_bucket})
            MERGE (d)-[:IN_TAXONOMY_SUB_BUCKET]->(s)
            """,
            **params,
        )

    def write_bucket_card(tx: Any, params: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (s:TaxonomySubBucket {id: $sub_bucket})
            MERGE (c:TaxonomyCard {card_id: $card_id})
            SET c.first_bucket = $first_bucket,
                c.sub_bucket = $sub_bucket,
                c.domain_hint = $domain_hint,
                c.bucket_description = $bucket_description,
                c.top_keywords = $top_keywords,
                c.typical_questions = $typical_questions,
                c.allowed_artifact_types = $allowed_artifact_types,
                c.sample_paths = $sample_paths,
                c.common_entities_systems = $common_entities_systems,
                c.common_entities_vendors = $common_entities_vendors,
                c.common_entities_projects = $common_entities_projects,
                c.common_entities_people = $common_entities_people,
                c.common_entities_locations = $common_entities_locations,
                c.file_count = $file_count,
                c.total_bytes = $total_bytes,
                c.temporal_span_from = $temporal_span_from,
                c.temporal_span_to = $temporal_span_to,
                c.temporal_span_years = $temporal_span_years,
                c.input_hash = $input_hash,
                c.source = $source_tag
            MERGE (s)-[:HAS_CARD]->(c)
            FOREACH (_ IN CASE WHEN $domain_hint <> '' THEN [1] ELSE [] END |
                MERGE (dom:Domain {id: $domain_hint})
                MERGE (c)-[:HINTS_DOMAIN]->(dom)
            )
            """,
            **params,
        )

    with neo_driver.session() as session:
        for fb in first_buckets:
            if not isinstance(fb, dict):
                continue
            first_bucket = str(fb.get("id", "")).strip()
            if not first_bucket:
                continue
            stats = fb.get("stats", {}) if isinstance(fb.get("stats"), dict) else {}
            session.execute_write(
                write_first_bucket,
                {
                    "root_id": "router_taxonomy",
                    "source_tag": source_tag,
                    "first_bucket": first_bucket,
                    "name": str(fb.get("name", first_bucket)),
                    "keywords": norm_list(fb.get("keywords")),
                    "include_path_prefixes": norm_list(fb.get("include_path_prefixes")),
                    "example_paths": norm_list(fb.get("example_paths")),
                    "file_count": int(stats.get("files", 0) or 0),
                    "total_bytes": int(stats.get("bytes", 0) or 0),
                    "temporal_span_from": str(stats.get("mtime_min", "") or ""),
                    "temporal_span_to": str(stats.get("mtime_max", "") or ""),
                    "temporal_span_years": float(
                        _temporal_span_years(str(stats.get("mtime_min", "") or ""), str(stats.get("mtime_max", "") or ""))
                    ),
                },
            )

            sub_buckets = fb.get("sub_buckets") if isinstance(fb.get("sub_buckets"), list) else []
            for sb in sub_buckets:
                if not isinstance(sb, dict):
                    continue
                sub_bucket = str(sb.get("id", "")).strip()
                if not sub_bucket:
                    continue
                sb_stats = sb.get("stats", {}) if isinstance(sb.get("stats"), dict) else {}
                session.execute_write(
                    write_sub_bucket,
                    {
                        "source_tag": source_tag,
                        "first_bucket": first_bucket,
                        "sub_bucket": sub_bucket,
                        "name": str(sb.get("name", sub_bucket)),
                        "keywords": norm_list(sb.get("keywords")),
                        "include_path_prefixes": norm_list(sb.get("include_path_prefixes")),
                        "example_paths": norm_list(sb.get("example_paths")),
                        "file_count": int(sb_stats.get("files", 0) or 0),
                        "total_bytes": int(sb_stats.get("bytes", 0) or 0),
                        "temporal_span_from": str(sb_stats.get("mtime_min", "") or ""),
                        "temporal_span_to": str(sb_stats.get("mtime_max", "") or ""),
                        "temporal_span_years": float(
                            _temporal_span_years(str(sb_stats.get("mtime_min", "") or ""), str(sb_stats.get("mtime_max", "") or ""))
                        ),
                    },
                )

        for card in cards:
            if not isinstance(card, dict):
                continue
            first_bucket = str(card.get("first_bucket", "")).strip()
            sub_bucket = str(card.get("sub_bucket", "")).strip()
            if not first_bucket or not sub_bucket:
                continue
            common_entities = card.get("common_entities", {}) if isinstance(card.get("common_entities"), dict) else {}
            stats = card.get("stats", {}) if isinstance(card.get("stats"), dict) else {}
            input_hash = str(card.get("input_hash", "")).strip()
            card_key = f"{first_bucket}|{sub_bucket}|{input_hash or sha256_hex(str(card.get('bucket_description', '')))}"
            card_id = artifact_id("taxonomy_card", card_key)
            session.execute_write(
                write_bucket_card,
                {
                    "source_tag": source_tag,
                    "card_id": card_id,
                    "first_bucket": first_bucket,
                    "sub_bucket": sub_bucket,
                    "domain_hint": str(card.get("domain_hint", "")).strip(),
                    "bucket_description": str(card.get("bucket_description", "")).strip(),
                    "top_keywords": norm_list(card.get("top_keywords")),
                    "typical_questions": norm_list(card.get("typical_questions")),
                    "allowed_artifact_types": norm_list(card.get("allowed_artifact_types")),
                    "sample_paths": norm_list(card.get("sample_paths")),
                    "common_entities_systems": norm_list(common_entities.get("systems")),
                    "common_entities_vendors": norm_list(common_entities.get("vendors")),
                    "common_entities_projects": norm_list(common_entities.get("projects")),
                    "common_entities_people": norm_list(common_entities.get("people")),
                    "common_entities_locations": norm_list(common_entities.get("locations")),
                    "file_count": int(stats.get("files", 0) or 0),
                    "total_bytes": int(stats.get("bytes", 0) or 0),
                    "temporal_span_from": str(stats.get("mtime_min", "") or ""),
                    "temporal_span_to": str(stats.get("mtime_max", "") or ""),
                    "temporal_span_years": float(
                        _temporal_span_years(str(stats.get("mtime_min", "") or ""), str(stats.get("mtime_max", "") or ""))
                    ),
                    "input_hash": input_hash,
                },
            )


def upsert_graph_communities_neo4j(
    neo_driver: Any,
    *,
    membership: Dict[str, str],
    community_meta: Dict[str, Dict[str, Any]],
) -> None:
    if not membership:
        return

    def write_community(tx: Any, params: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (c:Community {community_id: $community_id})
            SET c.scope = $scope,
                c.method = $method,
                c.semantic_domain = $semantic_domain,
                c.doc_count = $doc_count,
                c.chunk_count = $chunk_count,
                c.top_domains = $top_domains,
                c.top_buckets = $top_buckets,
                c.top_sub_buckets = $top_sub_buckets,
                c.top_tags = $top_tags,
                c.top_entities = $top_entities,
                c.sample_paths = $sample_paths,
                c.updated_at = timestamp()
            """,
            **params,
        )

    def write_membership(tx: Any, params: Dict[str, Any]) -> None:
        tx.run(
            """
            MATCH (d:Document {doc_id: $doc_id})
            MATCH (c:Community {community_id: $community_id})
            MERGE (d)-[:IN_COMMUNITY]->(c)
            """,
            **params,
        )

    with neo_driver.session() as session:
        for community_id, meta in sorted(community_meta.items()):
            session.execute_write(
                write_community,
                {
                    "community_id": community_id,
                    "scope": str(meta.get("scope", "graph")),
                    "method": str(meta.get("method", "unknown")),
                    "semantic_domain": str(meta.get("semantic_domain", "")),
                    "doc_count": int(meta.get("doc_count", 0) or 0),
                    "chunk_count": int(meta.get("chunk_count", 0) or 0),
                    "top_domains": norm_list(meta.get("top_domains")),
                    "top_buckets": norm_list(meta.get("top_buckets")),
                    "top_sub_buckets": norm_list(meta.get("top_sub_buckets")),
                    "top_tags": norm_list(meta.get("top_tags")),
                    "top_entities": norm_list(meta.get("top_entities")),
                    "sample_paths": norm_list(meta.get("sample_paths")),
                },
            )

        for doc_id, community_id in sorted(membership.items()):
            session.execute_write(
                write_membership,
                {
                    "doc_id": doc_id,
                    "community_id": community_id,
                },
            )


def artifacts_to_es_actions(index: str, artifacts: Sequence[GraphArtifact]) -> Iterable[Dict[str, Any]]:
    for a in artifacts:
        body = {**a.metadata, "text": a.text}
        yield {
            "_op_type": "index",
            "_index": index,
            "_id": a.artifact_id,
            "_source": body,
        }


def dedupe_artifacts(artifacts: Sequence[GraphArtifact]) -> List[GraphArtifact]:
    seen: set[str] = set()
    out: List[GraphArtifact] = []
    for a in artifacts:
        if a.artifact_id in seen:
            continue
        seen.add(a.artifact_id)
        out.append(a)
    return out


def filter_missing_qdrant_vectors(
    qdr: QdrantClient,
    collection_name: str,
    artifacts: Sequence[GraphArtifact],
) -> List[GraphArtifact]:
    if not artifacts:
        return []

    out: List[GraphArtifact] = []
    batch_size = 256
    for i in range(0, len(artifacts), batch_size):
        batch = list(artifacts[i : i + batch_size])
        ids = [qdrant_point_id(a.artifact_id) for a in batch]
        points = qdrant_call_with_retry(
            "retrieve_existing_vectors",
            lambda ids_=ids: qdr.retrieve(
                collection_name=collection_name,
                ids=ids_,
                with_payload=False,
                with_vectors=False,
            ),
        )
        found_ids: set[str] = set()
        for p in points or []:
            pid = getattr(p, "id", "")
            if pid is None:
                continue
            found_ids.add(str(pid))

        for a, pid in zip(batch, ids):
            if pid not in found_ids:
                out.append(a)
    return out


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    es = Elasticsearch(args.es_url)
    qdr: Optional[QdrantClient] = None
    if not args.skip_qdrant:
        qdr = QdrantClient(url=args.qdrant_url, timeout=float(args.qdrant_timeout_sec))

    neo = None
    if not args.skip_taxonomy_neo4j:
        neo = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))

    llm_families = parse_llm_artifact_families(args.llm_artifact_families)
    llm_chat = None
    llm_extraction_system_prompt = DEFAULT_LLM_EXTRACTION_SYSTEM_PROMPT
    llm_community_system_prompt = DEFAULT_LLM_COMMUNITY_SYSTEM_PROMPT
    if args.enable_llm_graphrag:
        llm_extraction_system_prompt = load_prompt_override(
            str(args.llm_extraction_system_prompt_file),
            DEFAULT_LLM_EXTRACTION_SYSTEM_PROMPT,
        )
        llm_community_system_prompt = load_prompt_override(
            str(args.llm_community_system_prompt_file),
            DEFAULT_LLM_COMMUNITY_SYSTEM_PROMPT,
        )
        if ChatProvider is None:
            LOGGER.warning("LLM GraphRAG enabled but ChatProvider import failed; disabling LLM extraction")
        elif not llm_families:
            LOGGER.warning("LLM GraphRAG enabled but no valid llm artifact families selected; disabling")
        else:
            try:
                llm_chat = ChatProvider()
                LOGGER.info(
                    "LLM GraphRAG enabled (model=%s families=%s)",
                    str(getattr(getattr(llm_chat, "cfg", None), "model", "")),
                    ",".join(sorted(llm_families)),
                )
            except Exception as exc:
                LOGGER.warning("Failed to initialize Chat provider for LLM GraphRAG: %s", exc)
                llm_chat = None

    try:
        doc_records: List[Dict[str, Any]] = []
        chunks: List[ChunkDoc] = []
        if not args.taxonomy_only:
            LOGGER.info("Loading documents from Elasticsearch index=%s", args.es_doc_index)
            docs_src = load_es_records(es, args.es_doc_index, max_records=int(args.max_docs))
            LOGGER.info("Loaded %d document records", len(docs_src))
            LOGGER.info("Loading chunks from Elasticsearch index=%s", args.es_chunk_index)
            chunks_src = load_es_records(es, args.es_chunk_index, max_records=int(args.max_chunks))
            LOGGER.info("Loaded %d chunk records", len(chunks_src))
            chunks = build_chunk_rows(chunks_src)
            doc_records = backfill_doc_counts(docs_src, chunks)
            if not doc_records or not chunks:
                raise RuntimeError("No document/chunk records found in Elasticsearch; run metadata ingest first")

        taxonomy_router: Dict[str, Any] = {}
        bucket_cards: List[Dict[str, Any]] = []
        if str(args.router_taxonomy_json).strip():
            p = Path(str(args.router_taxonomy_json)).expanduser().resolve()
            if p.exists():
                LOGGER.info("Loading taxonomy router: %s", p)
                taxonomy_router = load_json(p)
            else:
                LOGGER.warning("router taxonomy file not found: %s", p)
        if str(args.bucket_cards_jsonl).strip():
            p = Path(str(args.bucket_cards_jsonl)).expanduser().resolve()
            if p.exists():
                LOGGER.info("Loading bucket cards: %s", p)
                bucket_cards = load_jsonl(p)
            else:
                LOGGER.warning("bucket cards file not found: %s", p)

        artifacts: List[GraphArtifact] = []
        graph_community_membership: Dict[str, str] = {}
        graph_community_meta: Dict[str, Dict[str, Any]] = {}
        graph_community_stats: Dict[str, Any] = {
            "method": "disabled",
            "raw_communities": 0,
            "reported_communities": 0,
            "dropped_too_small": 0,
            "doc_similarity_edges": 0,
        }
        llm_stats: Dict[str, Any] = {"enabled": False}
        llm_artifact_ids: Set[str] = set()
        if not args.taxonomy_only:
            LOGGER.info("Building core GraphRAG artifacts from ingested corpus records")
            core_artifacts = build_core_graphrag_artifacts(doc_records, chunks)
            LOGGER.info("Built %d core artifacts", len(core_artifacts))
            artifacts.extend(core_artifacts)
            if llm_chat is not None and llm_families:
                LOGGER.info(
                    "Building additive LLM GraphRAG artifacts from chunks (families=%s)",
                    ",".join(sorted(llm_families)),
                )
                llm_artifacts, llm_stats = build_llm_graphrag_artifacts(
                    llm_chat,
                    doc_records,
                    chunks,
                    families=llm_families,
                    extraction_batch_size=int(args.llm_extraction_batch_size),
                    max_chunks=int(args.llm_max_chunks),
                    max_chars_per_chunk=int(args.llm_max_chars_per_chunk),
                    max_entities_per_chunk=int(args.llm_max_entities_per_chunk),
                    max_relationships_per_chunk=int(args.llm_max_relationships_per_chunk),
                    max_claims_per_chunk=int(args.llm_max_claims_per_chunk),
                    max_gleanings=int(args.llm_max_gleanings),
                    max_entities_total=int(args.llm_max_entities_total),
                    max_relationships_total=int(args.llm_max_relationships_total),
                    max_claims_total=int(args.llm_max_claims_total),
                    community_max_domains=int(args.llm_community_max_domains),
                    community_max_evidence_items=int(args.llm_community_max_evidence_items),
                    extraction_system_prompt=llm_extraction_system_prompt,
                    community_system_prompt=llm_community_system_prompt,
                    llm_temperature=float(args.llm_temperature),
                    llm_top_p=float(args.llm_top_p),
                    llm_max_completion_tokens=int(args.llm_max_completion_tokens),
                )
                LOGGER.info("Built %d additive LLM artifacts", len(llm_artifacts))
                artifacts.extend(llm_artifacts)
                llm_artifact_ids = {str(a.artifact_id) for a in llm_artifacts if str(a.artifact_id).strip()}
            if not args.skip_graph_communities:
                LOGGER.info(
                    "Building graph communities (method=%s min_shared_entities=%d min_docs=%d)",
                    args.community_method,
                    int(args.community_min_shared_entities),
                    int(args.community_min_docs),
                )
                (
                    graph_community_artifacts,
                    graph_community_membership,
                    graph_community_meta,
                    graph_community_stats,
                ) = build_graph_community_reports(
                    doc_records,
                    chunks,
                    community_method=str(args.community_method),
                    community_resolution=float(args.community_resolution),
                    min_shared_entities=int(args.community_min_shared_entities),
                    min_docs=int(args.community_min_docs),
                    max_reports=int(args.community_max_reports),
                )
                LOGGER.info(
                    "Built %d graph community reports (method=%s raw=%d dropped_small=%d edges=%d)",
                    len(graph_community_artifacts),
                    graph_community_stats.get("method"),
                    int(graph_community_stats.get("raw_communities", 0) or 0),
                    int(graph_community_stats.get("dropped_too_small", 0) or 0),
                    int(graph_community_stats.get("doc_similarity_edges", 0) or 0),
                )
                artifacts.extend(graph_community_artifacts)
                llm_graph_reports = 0
                if llm_chat is not None and "community_report_graph" in llm_families and graph_community_meta:
                    LOGGER.info(
                        "Building additive LLM graph community reports (communities=%d)",
                        len(graph_community_meta),
                    )
                    graph_rows = sorted(
                        graph_community_meta.values(),
                        key=lambda r: (-int(r.get("doc_count", 0) or 0), str(r.get("community_id", ""))),
                    )
                    graph_rows = apply_cap(graph_rows, int(args.llm_community_max_domains))
                    graph_inputs: List[Dict[str, Any]] = []
                    for row in graph_rows:
                        graph_inputs.append(
                            {
                                "community_id": str(row.get("community_id", "")),
                                "scope": str(row.get("scope", "graph")),
                                "method": str(row.get("method", "")),
                                "semantic_domain": str(row.get("semantic_domain", "")),
                                "doc_count": int(row.get("doc_count", 0) or 0),
                                "chunk_count": int(row.get("chunk_count", 0) or 0),
                                "top_domains": norm_list(row.get("top_domains")),
                                "top_buckets": norm_list(row.get("top_buckets")),
                                "top_sub_buckets": norm_list(row.get("top_sub_buckets")),
                                "top_tags": norm_list(row.get("top_tags")),
                                "top_entities": norm_list(row.get("top_entities"))[: int(args.llm_community_max_evidence_items)],
                                "sample_paths": norm_list(row.get("sample_paths"))[: int(args.llm_community_max_evidence_items)],
                                "temporal_span_from": str(row.get("temporal_span_from", "") or ""),
                                "temporal_span_to": str(row.get("temporal_span_to", "") or ""),
                                "temporal_span_years": float(row.get("temporal_span_years", 0.0) or 0.0),
                            }
                        )
                    graph_summaries = build_llm_graph_community_reports(
                        llm_chat,
                        graph_inputs,
                        system_prompt=llm_community_system_prompt,
                        max_completion_tokens=int(args.llm_max_completion_tokens),
                        temperature=float(args.llm_temperature),
                        top_p=float(args.llm_top_p),
                    )
                    model_name = str(getattr(getattr(llm_chat, "cfg", None), "model", "llm"))
                    llm_graph_artifacts: List[GraphArtifact] = []
                    for row in graph_inputs:
                        community_id = str(row.get("community_id", "")).strip()
                        if not community_id:
                            continue
                        summary = graph_summaries.get(community_id) or {}
                        txt = str(summary.get("summary", "")).strip()
                        if not txt:
                            continue
                        aid = artifact_id("community_graph_llm", community_id)
                        llm_graph_artifacts.append(
                            GraphArtifact(
                                artifact_id=aid,
                                artifact_type="community_report",
                                text=txt,
                                metadata={
                                    "artifact_id": aid,
                                    "artifact_type": "community_report",
                                    "artifact_generation": "llm",
                                    "extraction_method": "llm_graph_summary",
                                    "extraction_model": model_name,
                                    "retrieval_mode": "global",
                                    "community_id": f"graph_llm:{community_id}",
                                    "community_scope": "graph",
                                    "community_method": "llm",
                                    "semantic_domain": str(row.get("semantic_domain", "")),
                                    "doc_count": int(row.get("doc_count", 0) or 0),
                                    "chunk_count": int(row.get("chunk_count", 0) or 0),
                                    "top_domains": norm_list(row.get("top_domains")),
                                    "top_buckets": norm_list(row.get("top_buckets")),
                                    "top_sub_buckets": norm_list(row.get("top_sub_buckets")),
                                    "top_tags": norm_list(row.get("top_tags")),
                                    "top_entities": norm_list(summary.get("key_entities")),
                                    "top_relationships": norm_list(summary.get("key_relationships")),
                                    "top_claims": norm_list(summary.get("key_claims")),
                                    "open_questions": norm_list(summary.get("open_questions")),
                                    "sample_paths": norm_list(row.get("sample_paths")),
                                    "temporal_span_from": str(row.get("temporal_span_from", "") or ""),
                                    "temporal_span_to": str(row.get("temporal_span_to", "") or ""),
                                    "temporal_span_years": float(row.get("temporal_span_years", 0.0) or 0.0),
                                    "effective_date": str(row.get("temporal_span_to", "") or ""),
                                },
                                vector=None,
                            )
                        )
                    llm_graph_reports = len(llm_graph_artifacts)
                    if llm_graph_reports:
                        LOGGER.info("Built %d additive LLM graph community artifacts", llm_graph_reports)
                        artifacts.extend(llm_graph_artifacts)
                        llm_artifact_ids.update(
                            str(a.artifact_id) for a in llm_graph_artifacts if str(a.artifact_id).strip()
                        )
                    if not bool(llm_stats.get("enabled")):
                        llm_stats["enabled"] = True
                if llm_chat is not None and "community_report_graph" in llm_families:
                    llm_stats["llm_graph_reports"] = int(llm_graph_reports)
                if neo is not None and not args.dry_run and graph_community_membership:
                    LOGGER.info(
                        "Upserting graph community memberships into Neo4j (communities=%d docs=%d)",
                        len(graph_community_meta),
                        len(graph_community_membership),
                    )
                    upsert_graph_communities_neo4j(
                        neo,
                        membership=graph_community_membership,
                        community_meta=graph_community_meta,
                    )

        if taxonomy_router:
            LOGGER.info("Building taxonomy enrichment artifacts")
            tax_artifacts = build_taxonomy_artifacts(taxonomy_router, bucket_cards)
            LOGGER.info("Built %d taxonomy artifacts", len(tax_artifacts))
            artifacts.extend(tax_artifacts)
            if neo is not None and not args.dry_run:
                LOGGER.info("Upserting taxonomy graph into Neo4j")
                upsert_taxonomy_neo4j(
                    neo,
                    taxonomy_router,
                    bucket_cards,
                    source_tag=str(Path(args.router_taxonomy_json).name or "router_taxonomy"),
                )

        # Ensure all artifact families carry consistent temporal metadata for
        # date-bounded retrieval and temporal diagnostics.
        enrich_artifacts_temporal_metadata(artifacts, chunks, doc_records)

        artifacts = dedupe_artifacts(artifacts)
        if int(args.max_artifacts) > 0:
            artifacts = artifacts[: int(args.max_artifacts)]
        if not artifacts:
            raise RuntimeError("No GraphRAG artifacts were produced")

        LOGGER.info("Prepared %d total GraphRAG artifacts", len(artifacts))

        if not args.dry_run and not args.skip_es:
            LOGGER.info("Indexing GraphRAG artifacts into Elasticsearch index=%s", args.es_graphrag_index)
            es_bulk(es, artifacts_to_es_actions(args.es_graphrag_index, artifacts), refresh=False)

        if not args.dry_run and not args.skip_qdrant:
            if qdr is None:
                raise RuntimeError("Qdrant client is not initialized")
            artifacts_to_embed = list(artifacts)
            if args.skip_existing_vectors:
                LOGGER.info("Checking existing vectors in Qdrant collection=%s", args.qdrant_graphrag_collection)
                missing = filter_missing_qdrant_vectors(qdr, args.qdrant_graphrag_collection, artifacts)
                missing_by_id: Dict[str, GraphArtifact] = {str(a.artifact_id): a for a in missing}
                # LLM artifacts may change text between runs; always refresh their vectors when enabled.
                if llm_artifact_ids:
                    for a in artifacts:
                        aid = str(a.artifact_id)
                        if aid in llm_artifact_ids:
                            missing_by_id[aid] = a
                artifacts_to_embed = list(missing_by_id.values())
            LOGGER.info(
                "Embedding GraphRAG artifacts (%d/%d need vector writes)",
                len(artifacts_to_embed),
                len(artifacts),
            )

            failed: List[str] = []
            embedded_count = 0
            upserted_count = 0
            batch_size = max(1, int(args.embed_batch_size))
            q_batch_size = max(1, int(args.qdrant_upsert_batch_size))
            pending_upserts: List[GraphArtifact] = []

            def flush_pending_upserts(*, force: bool = False) -> None:
                nonlocal upserted_count, pending_upserts
                while len(pending_upserts) >= q_batch_size or (force and pending_upserts):
                    q_batch = list(pending_upserts[:q_batch_size])
                    pending_upserts = pending_upserts[q_batch_size:]
                    points: List[PointStruct] = []
                    for a in q_batch:
                        if a.vector is None:
                            raise RuntimeError(f"Missing vector for artifact {a.artifact_id}")
                        payload = {**a.metadata, "text": a.text}
                        points.append(
                            PointStruct(
                                id=qdrant_point_id(a.artifact_id),
                                vector=a.vector,
                                payload=payload,
                            )
                        )
                    qdrant_call_with_retry(
                        "upsert_graphrag",
                        lambda pts=points: qdr.upsert(
                            collection_name=args.qdrant_graphrag_collection,
                            wait=bool(args.qdrant_upsert_wait),
                            points=pts,
                        ),
                    )
                    upserted_count += len(q_batch)

            for i in range(0, len(artifacts_to_embed), batch_size):
                batch = artifacts_to_embed[i : i + batch_size]
                embedded_batch: List[GraphArtifact] = []
                try:
                    vecs = embed_batch_resilient(
                        args.embed_url,
                        args.embed_model,
                        [a.text for a in batch],
                        timeout_sec=int(args.embed_timeout_sec),
                        dimensions=(int(args.vector_dim) if int(args.vector_dim) > 0 else None),
                        max_attempts=int(args.embed_max_attempts),
                        retry_backoff_sec=float(args.embed_retry_backoff_sec),
                    )
                    if len(vecs) != len(batch):
                        raise RuntimeError(f"Embedding count mismatch: got {len(vecs)} expected {len(batch)}")
                    for a, v in zip(batch, vecs):
                        a.vector = v
                        embedded_batch.append(a)
                except Exception as exc:
                    LOGGER.warning(
                        "Batch embedding failed for %d artifacts; falling back to single-item retries: %s",
                        len(batch),
                        exc,
                    )
                    for a in batch:
                        try:
                            one = embed_batch_resilient(
                                args.embed_url,
                                args.embed_model,
                                [a.text],
                                timeout_sec=int(args.embed_timeout_sec),
                                dimensions=(int(args.vector_dim) if int(args.vector_dim) > 0 else None),
                                max_attempts=int(args.embed_max_attempts),
                                retry_backoff_sec=float(args.embed_retry_backoff_sec),
                            )
                            if not one or not isinstance(one[0], list):
                                raise RuntimeError("single-item embed response missing vector")
                            a.vector = one[0]
                            embedded_batch.append(a)
                        except Exception as one_exc:
                            failed.append(f"{a.artifact_id}: {one_exc}")

                if embedded_batch:
                    embedded_count += len(embedded_batch)
                    pending_upserts.extend(embedded_batch)
                    flush_pending_upserts(force=False)

                if (i // batch_size) % 20 == 0:
                    LOGGER.info(
                        "Embedded %d/%d artifacts (upserted=%d pending=%d failed=%d)",
                        embedded_count,
                        len(artifacts_to_embed),
                        upserted_count,
                        len(pending_upserts),
                        len(failed),
                    )

            flush_pending_upserts(force=True)

            if not embedded_count and artifacts_to_embed:
                raise RuntimeError("All embedding attempts failed; no vectors available for upsert")
            if failed:
                LOGGER.warning("Skipped %d artifacts due embedding failures", len(failed))

            LOGGER.info(
                "Upserted GraphRAG artifact vectors into Qdrant collection=%s (count=%d)",
                args.qdrant_graphrag_collection,
                upserted_count,
            )

        summary = {
            "ok": True,
            "taxonomy_only": bool(args.taxonomy_only),
            "artifact_count": len(artifacts),
            "artifact_type_counts": dict(sorted(Counter(a.artifact_type for a in artifacts).items())),
            "taxonomy_router_loaded": bool(taxonomy_router),
            "bucket_cards_loaded": int(len(bucket_cards)),
            "graph_communities": graph_community_stats,
            "graph_community_memberships": len(graph_community_membership),
            "llm_artifacts": llm_stats,
            "networkx_available": bool(nx is not None),
            "dry_run": bool(args.dry_run),
        }
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        if neo is not None:
            neo.close()


if __name__ == "__main__":
    raise SystemExit(main())
