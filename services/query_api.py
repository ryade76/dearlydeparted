#!/usr/bin/env python3
"""
Hybrid retrieval API over Elasticsearch + Qdrant + Neo4j.

NOTE: This module serves dual roles:
  1. The query-api FastAPI app (port 8080) — shared backend for both v1 and v2 agents.
  2. Utility functions (load_taxonomy_router, build_taxonomy_index, lexical_*_candidates)
     imported directly by v2 tools.
The FastAPI app and retrieval pipeline are NOT deprecated — they remain the
canonical retrieval backend for all agent versions.
"""

from __future__ import annotations

import json
import logging
import os
import hashlib
from pathlib import Path
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

from elasticsearch import Elasticsearch
from fastapi import FastAPI, HTTPException
from neo4j import GraphDatabase
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
import requests

try:
    from services.chat_provider import ChatProvider
except Exception:
    ChatProvider = None  # type: ignore[assignment]


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_bucket_keyword_hints(raw: str) -> Dict[str, List[str]]:
    txt = str(raw or "").strip()
    if not txt:
        return {}
    try:
        obj = json.loads(txt)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for key, vals in obj.items():
        bid = str(key or "").strip()
        if not bid:
            continue
        items: List[str] = []
        if isinstance(vals, list):
            for v in vals:
                t = str(v or "").strip().lower()
                if t:
                    items.append(t)
        elif isinstance(vals, str):
            t = vals.strip().lower()
            if t:
                items.append(t)
        if items:
            out[bid] = items
    return out


def _ensure_local_no_proxy() -> None:
    required = ["127.0.0.1", "localhost", "::1"]
    cur = str(os.getenv("NO_PROXY") or os.getenv("no_proxy") or "").strip()
    items = [x.strip() for x in cur.split(",") if x.strip()]
    seen = {x.lower() for x in items}
    for host in required:
        if host.lower() not in seen:
            items.append(host)
    merged = ",".join(items)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def _http_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    host = (urlparse(str(url)).hostname or "").lower()
    if host in {"127.0.0.1", "localhost"}:
        with requests.Session() as sess:
            sess.trust_env = False
            return sess.request(method, url, **kwargs)
    return requests.request(method, url, **kwargs)


_ensure_local_no_proxy()


ES_URL = os.getenv("ES_URL", "http://127.0.0.1:9200").strip()
ES_CHUNK_INDEX = os.getenv("ES_CHUNK_INDEX", "kb_chunks").strip()
ES_GRAPHRAG_INDEX = os.getenv("ES_GRAPHRAG_INDEX", "kb_graphrag_artifacts").strip()
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").strip()
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "kb_chunks_vec").strip()
QDRANT_GRAPHRAG_COLLECTION = os.getenv("QDRANT_GRAPHRAG_COLLECTION", "kb_graphrag_artifacts_vec").strip()
QDRANT_TIMEOUT_SEC = int(os.getenv("QDRANT_TIMEOUT_SEC", "60"))
EMBED_URL = os.getenv("EMBED_URL", "http://127.0.0.1:8001/v1/embeddings").strip()
EMBED_MODEL = os.getenv("EMBED_MODEL_RUNTIME", os.getenv("EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")).strip()
EMBED_DIM = int(os.getenv("VECTOR_DIM", "4096"))
EMBED_TIMEOUT_SEC = int(os.getenv("EMBED_TIMEOUT_SEC", "120"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687").strip()
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j").strip()
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me_local").strip()
DEFAULT_TOP_K = int(os.getenv("QUERY_DEFAULT_TOP_K", "8"))
FUSION_K = int(os.getenv("QUERY_FUSION_K", "60"))
QUERY_CANDIDATE_POOL = int(os.getenv("QUERY_CANDIDATE_POOL", "40"))
EMBED_DISABLE_TASK = str(os.getenv("EMBED_DISABLE_TASK", "1")).strip().lower() in {"1", "true", "yes", "on"}
EMBED_DISABLE_DIMENSIONS = str(os.getenv("EMBED_DISABLE_DIMENSIONS", "1")).strip().lower() in {"1", "true", "yes", "on"}
RERANK_ENABLED = str(os.getenv("RERANK_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
RERANK_URL = os.getenv("RERANK_URL", "http://127.0.0.1:8002/v1/rerank").strip()
RERANK_MODEL = os.getenv("RERANK_MODEL_RUNTIME", os.getenv("RERANK_MODEL", "Qwen/Qwen3-Reranker-8B")).strip()
RERANK_TIMEOUT_SEC = int(os.getenv("RERANK_TIMEOUT_SEC", "90"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "0"))
RERANK_STRATEGY = os.getenv("RERANK_STRATEGY", "single").strip().lower()
EMBED_QUERY_INSTRUCTION = os.getenv(
    "EMBED_QUERY_INSTRUCTION",
    "Given a web search query, retrieve relevant passages that answer the query",
).strip()
RERANK_INSTRUCTION = os.getenv(
    "RERANK_INSTRUCTION",
    "Given a web search query, retrieve relevant passages that answer the query",
).strip()
TAXONOMY_ROUTER_JSON = os.getenv("TAXONOMY_ROUTER_JSON", "kb_artifacts_full_promo_calibrated_run3/router.json").strip()
QUERY_DECOMPOSER_ENABLED = _env_bool("QUERY_DECOMPOSER_ENABLED", True)
QUERY_DECOMPOSER_USE_LLM = _env_bool("QUERY_DECOMPOSER_USE_LLM", True)
QUERY_DECOMPOSER_MIN_CONFIDENCE = float(os.getenv("QUERY_DECOMPOSER_MIN_CONFIDENCE", "0.45"))
QUERY_DECOMPOSER_MIN_DOMAIN_SCORE = float(os.getenv("QUERY_DECOMPOSER_MIN_DOMAIN_SCORE", "2.0"))
QUERY_DECOMPOSER_MAX_HINTS = int(os.getenv("QUERY_DECOMPOSER_MAX_HINTS", "3"))
QUERY_DECOMPOSER_UPGRADE_BASIC_MODE = _env_bool("QUERY_DECOMPOSER_UPGRADE_BASIC_MODE", True)
QUERY_DECOMPOSER_MAX_LLM_CANDIDATES = int(os.getenv("QUERY_DECOMPOSER_MAX_LLM_CANDIDATES", "10"))
QUERY_DECOMPOSER_MAX_LLM_KEYWORDS = int(os.getenv("QUERY_DECOMPOSER_MAX_LLM_KEYWORDS", "8"))
QUERY_FIRST_BUCKET_HINT_FLOOR = _env_bool("QUERY_FIRST_BUCKET_HINT_FLOOR", True)
QUERY_FIRST_BUCKET_HINT_FLOOR_MIN_TAXONOMY_COVERAGE = float(
    os.getenv("QUERY_FIRST_BUCKET_HINT_FLOOR_MIN_TAXONOMY_COVERAGE", "0.35")
)
QUERY_BUCKET_KEYWORD_HINTS = _parse_bucket_keyword_hints(
    os.getenv(
        "QUERY_BUCKET_KEYWORD_HINTS_JSON",
        '{"security":["cyber","security","compliance","audit","risk","vulnerability"],'
        '"root_docs":["meeting notes","action items","it operations","it ops"]}',
    )
)
QUERY_DIAGNOSTICS_ENABLED = _env_bool("QUERY_DIAGNOSTICS_ENABLED", True)
QUERY_REBALANCE_ENABLED = _env_bool("QUERY_REBALANCE_ENABLED", True)
QUERY_INTENT_RESCUE_PASS = _env_bool("QUERY_INTENT_RESCUE_PASS", True)
QUERY_RESCUE_TOP_K = int(os.getenv("QUERY_RESCUE_TOP_K", "24"))
QUERY_CANDIDATE_MAX = int(os.getenv("QUERY_CANDIDATE_MAX", "220"))
QUERY_MIN_TEXT_EVIDENCE = int(os.getenv("QUERY_MIN_TEXT_EVIDENCE", "4"))
QUERY_MAX_CLAIM_RATIO = float(os.getenv("QUERY_MAX_CLAIM_RATIO", "0.50"))
QUERY_MAX_TAXONOMY_RATIO = float(os.getenv("QUERY_MAX_TAXONOMY_RATIO", "0.50"))
QUERY_MAX_TROUBLESHOOTING_TUPLE_RATIO = float(os.getenv("QUERY_MAX_TROUBLESHOOTING_TUPLE_RATIO", "0.25"))
QUERY_MAX_PER_PATH = int(os.getenv("QUERY_MAX_PER_PATH", "2"))
QUERY_SOURCE_HINT_MAX_PER_PATH = int(os.getenv("QUERY_SOURCE_HINT_MAX_PER_PATH", "12"))
QUERY_SOURCE_HINT_MIN_FINAL_HITS = int(os.getenv("QUERY_SOURCE_HINT_MIN_FINAL_HITS", "3"))
QUERY_SOURCE_HINT_MIN_FINAL_HITS_TARGETED = int(
    os.getenv("QUERY_SOURCE_HINT_MIN_FINAL_HITS_TARGETED", "6")
)
QUERY_SOURCE_HINT_MIN_FINAL_HITS_EXACT = int(
    os.getenv("QUERY_SOURCE_HINT_MIN_FINAL_HITS_EXACT", "8")
)
QUERY_TROUBLESHOOTING_TUPLE_AS_TEXT = _env_bool("QUERY_TROUBLESHOOTING_TUPLE_AS_TEXT", False)
QUERY_TEMPORAL_BOOST_ENABLED = _env_bool("QUERY_TEMPORAL_BOOST_ENABLED", False)
QUERY_TEMPORAL_BOOST_WEIGHT = float(os.getenv("QUERY_TEMPORAL_BOOST_WEIGHT", "0.15"))
QUERY_REQUIRE_ROUTED_CHUNKS = _env_bool("QUERY_REQUIRE_ROUTED_CHUNKS", False)
APP_BUILD_ID = str(os.getenv("APP_BUILD_ID", "dev")).strip() or "dev"
API_STARTED_AT = datetime.utcnow().isoformat(timespec="seconds") + "Z"

LOGGER = logging.getLogger("query_api")
_EMBED_COMPAT_OMIT_TASK = EMBED_DISABLE_TASK
_EMBED_COMPAT_OMIT_DIMENSIONS = EMBED_DISABLE_DIMENSIONS


class RetrieveRequest(BaseModel):
    query: str
    domain: str = ""
    mode: str = Field(default="auto", pattern="^(basic|local|global|drift|auto)$")
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=100)
    include_graph: bool = True
    decompose: bool = True
    source_path_hint: str = ""
    diagnostics_include_rank_trace: bool = False
    date_from: str = ""
    date_to: str = ""
    temporal_basis: str = Field(default="effective", pattern="^(effective|mtime)$")
    temporal_boost: str = Field(default="", pattern="^(|recency|none)$")
    graphrag_generation_mode: str = Field(default="both", pattern="^(both|llm|deterministic)$")
    graphrag_use_llm: bool = True
    graphrag_use_deterministic: bool = True
    graphrag_artifact_types: List[str] = Field(default_factory=list)


class RetrieveResponse(BaseModel):
    query: str
    domain: str
    mode: str
    top_k: int
    graphrag_generation_mode: str = "both"
    graphrag_use_llm: bool = True
    graphrag_use_deterministic: bool = True
    rerank_applied: bool = False
    hit_count: int
    hits: List[Dict[str, Any]]
    graph: Dict[str, Any] = Field(default_factory=dict)
    decomposition: Dict[str, Any] = Field(default_factory=dict)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


_MODE_VALUES: Set[str] = {"basic", "local", "global", "drift"}
_DECOMPOSER_MODE_VALUES: Set[str] = {"basic", "local", "global", "drift", "auto"}
_GRAPHRAG_ARTIFACT_TYPES: Set[str] = {
    "text_unit",
    "log_entry",
    "procedure_step",
    "procedure_requirement",
    "compliance_constraint",
    "risk_signal",
    "inventory_row",
    "account_status_row",
    "entity_description",
    "relationship",
    "claim",
    "community_report",
    "troubleshooting_tuple",
    "troubleshooting_event",
    "taxonomy_first_bucket",
    "taxonomy_sub_bucket",
    "taxonomy_bucket_card",
    "taxonomy_domain_profile",
}
_taxonomy_router_obj: Dict[str, Any] = {}
_taxonomy_index: Dict[str, Any] = {"first": {}, "sub": {}, "domains": [], "domain_map": {}}
_domain_first_bucket_priors: Dict[str, List[str]] = {}
_decomposer_chat: Any = None
_corpus_temporal_bounds: Dict[str, Dict[str, str]] = {
    "mtime": {"min": "", "max": ""},
    "effective": {"min": "", "max": ""},
}


def _parse_iso_date(value: str) -> date | None:
    txt = str(value or "").strip()
    if not txt:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", txt):
        try:
            return date.fromisoformat(txt)
        except Exception:
            return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", txt)
    if m is not None:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _epoch_day(d: date | None) -> int:
    if d is None:
        return -1
    return int((d - date(1970, 1, 1)).days)


def _resolve_temporal_fields(temporal_basis: str) -> Tuple[str, str]:
    basis = str(temporal_basis or "effective").strip().lower()
    if basis == "mtime":
        return "mtime_date", "mtime_epoch_day"
    return "effective_date", "effective_epoch_day"


def _build_es_temporal_filter(*, temporal_basis: str, date_from: str, date_to: str) -> Dict[str, Any] | None:
    start = str(date_from or "").strip()
    end = str(date_to or "").strip()
    if not start and not end:
        return None
    date_field, _ = _resolve_temporal_fields(temporal_basis)
    rng: Dict[str, Any] = {}
    if start:
        rng["gte"] = start
    if end:
        rng["lte"] = end
    return {"range": {date_field: rng}}


def _build_qdrant_temporal_filter(*, temporal_basis: str, date_from: str, date_to: str) -> qm.FieldCondition | None:
    start = _parse_iso_date(date_from)
    end = _parse_iso_date(date_to)
    if start is None and end is None:
        return None
    _, epoch_field = _resolve_temporal_fields(temporal_basis)
    rng = qm.Range(
        gte=_epoch_day(start) if start is not None else None,
        lte=_epoch_day(end) if end is not None else None,
    )
    return qm.FieldCondition(key=epoch_field, range=rng)


def _append_es_chunk_routing_filter(filt: List[Any]) -> None:
    """
    Taxonomy/routing labels are optional for some records.
    Keep strict routed-only filtering opt-in so missing-taxonomy records still retrieve.
    """
    if QUERY_REQUIRE_ROUTED_CHUNKS:
        filt.append({"term": {"semantic_decision": "routed"}})


def _append_qdrant_chunk_routing_filter(must: List[qm.Condition]) -> None:
    if QUERY_REQUIRE_ROUTED_CHUNKS:
        must.append(qm.FieldCondition(key="semantic_decision", match=qm.MatchValue(value="routed")))


def _extract_hit_temporal_date(hit: Dict[str, Any], temporal_basis: str) -> str:
    basis = str(temporal_basis or "effective").strip().lower()
    if basis == "mtime":
        return str(hit.get("mtime_date", "") or "").strip()
    return str(hit.get("effective_date", "") or hit.get("mtime_date", "") or "").strip()


def _temporal_recency_score(value: str, min_value: str, max_value: str) -> float:
    d = _parse_iso_date(value)
    dmin = _parse_iso_date(min_value)
    dmax = _parse_iso_date(max_value)
    if d is None or dmin is None or dmax is None:
        return 0.0
    lo = _epoch_day(dmin)
    hi = _epoch_day(dmax)
    cur = _epoch_day(d)
    if hi <= lo:
        return 0.0
    raw = (float(cur) - float(lo)) / float(hi - lo)
    return max(0.0, min(1.0, raw))


def _build_temporal_profile(rows: List[Dict[str, Any]], temporal_basis: str) -> Dict[str, Any]:
    dates: List[date] = []
    by_year: Dict[str, int] = defaultdict(int)
    oldest: Dict[str, Any] = {}
    newest: Dict[str, Any] = {}
    for row in rows:
        d_txt = _extract_hit_temporal_date(row, temporal_basis)
        d = _parse_iso_date(d_txt)
        if d is None:
            continue
        dates.append(d)
        by_year[str(d.year)] += 1
        item_id = str(row.get("artifact_id") or row.get("chunk_id") or "").strip()
        if not oldest or d < _parse_iso_date(str(oldest.get("mtime_date", ""))) or _parse_iso_date(str(oldest.get("mtime_date", ""))) is None:
            oldest = {"item_id": item_id, "mtime_date": d.isoformat(), "path": str(row.get("path", ""))}
        if not newest or d > _parse_iso_date(str(newest.get("mtime_date", ""))) or _parse_iso_date(str(newest.get("mtime_date", ""))) is None:
            newest = {"item_id": item_id, "mtime_date": d.isoformat(), "path": str(row.get("path", ""))}
    if not dates:
        return {"available": False}
    dates_sorted = sorted(dates)
    today = datetime.utcnow().date()
    mid = len(dates_sorted) // 2
    median_date = dates_sorted[mid]
    return {
        "available": True,
        "basis": str(temporal_basis or "effective"),
        "result_date_range": [dates_sorted[0].isoformat(), dates_sorted[-1].isoformat()],
        "result_year_distribution": dict(sorted(by_year.items(), key=lambda kv: kv[0])),
        "median_result_age_days": int((today - median_date).days),
        "oldest_result": oldest,
        "newest_result": newest,
    }


def _load_corpus_temporal_bounds(es: Elasticsearch, index_name: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {
        "mtime": {"min": "", "max": ""},
        "effective": {"min": "", "max": ""},
    }
    body = {
        "size": 0,
        "aggs": {
            "mtime_min": {"min": {"field": "mtime_date"}},
            "mtime_max": {"max": {"field": "mtime_date"}},
            "effective_min": {"min": {"field": "effective_date"}},
            "effective_max": {"max": {"field": "effective_date"}},
        },
    }
    try:
        resp = es.search(index=index_name, body=body)
    except Exception as exc:
        LOGGER.warning("temporal bounds load failed: %s", exc)
        return out
    aggs = (resp or {}).get("aggregations", {}) if isinstance(resp, dict) else {}
    for key, scope, kind in (
        ("mtime_min", "mtime", "min"),
        ("mtime_max", "mtime", "max"),
        ("effective_min", "effective", "min"),
        ("effective_max", "effective", "max"),
    ):
        val = ((aggs.get(key) or {}).get("value_as_string") if isinstance(aggs.get(key), dict) else "") or ""
        parsed = _parse_iso_date(str(val))
        if parsed is not None:
            out[scope][kind] = parsed.isoformat()
    return out


def _tokenize_text(value: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def load_taxonomy_router(path_str: str) -> Dict[str, Any]:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        LOGGER.warning("taxonomy router not found: %s", path)
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        LOGGER.warning("failed to load taxonomy router %s: %s", path, exc)
        return {}
    if not isinstance(obj, dict):
        return {}
    return obj


def build_taxonomy_index(router_obj: Dict[str, Any]) -> Dict[str, Any]:
    first_map: Dict[str, Dict[str, Any]] = {}
    sub_map: Dict[str, Dict[str, Any]] = {}
    domains: List[str] = []
    domain_map: Dict[str, Dict[str, Any]] = {}

    rules_obj = router_obj.get("rules")
    if isinstance(rules_obj, dict):
        domain_rows = rules_obj.get("domains")
        if isinstance(domain_rows, list):
            for row in domain_rows:
                if not isinstance(row, dict):
                    continue
                did = str(row.get("id", "")).strip()
                if did:
                    domains.append(did)
                    dname = str(row.get("name", did)).strip() or did
                    ddesc = str(row.get("description", "")).strip()
                    dkeywords = [str(x).strip() for x in (row.get("keywords") or []) if str(x).strip()]
                    dphrases = [str(x).strip() for x in (row.get("phrases") or []) if str(x).strip()]
                    dtokens = _tokenize_text(did) | _tokenize_text(dname) | _tokenize_text(ddesc)
                    for kw in dkeywords[:120]:
                        dtokens |= _tokenize_text(kw)
                    for ph in dphrases[:120]:
                        dtokens |= _tokenize_text(ph)
                    domain_map[did] = {
                        "id": did,
                        "name": dname,
                        "description": ddesc,
                        "keywords": dkeywords,
                        "phrases": dphrases,
                        "tokens": dtokens,
                    }

    first_rows = router_obj.get("first_buckets")
    if not isinstance(first_rows, list):
        first_rows = []

    for fb in first_rows:
        if not isinstance(fb, dict):
            continue
        first_id = str(fb.get("id", "")).strip()
        if not first_id:
            continue
        first_name = str(fb.get("name", first_id)).strip() or first_id
        first_keywords = [str(x).strip() for x in (fb.get("keywords") or []) if str(x).strip()]
        first_prefixes = [str(x).strip() for x in (fb.get("include_path_prefixes") or []) if str(x).strip()]
        first_tokens = _tokenize_text(first_id) | _tokenize_text(first_name)
        for kw in first_keywords[:80]:
            first_tokens |= _tokenize_text(kw)
        first_map[first_id] = {
            "id": first_id,
            "name": first_name,
            "keywords": first_keywords,
            "include_path_prefixes": first_prefixes,
            "tokens": first_tokens,
            "sub_buckets": [],
        }

        sub_rows = fb.get("sub_buckets")
        if not isinstance(sub_rows, list):
            sub_rows = []
        for sb in sub_rows:
            if not isinstance(sb, dict):
                continue
            sub_id = str(sb.get("id", "")).strip()
            if not sub_id:
                continue
            sub_name = str(sb.get("name", sub_id)).strip() or sub_id
            sub_keywords = [str(x).strip() for x in (sb.get("keywords") or []) if str(x).strip()]
            sub_prefixes = [str(x).strip() for x in (sb.get("include_path_prefixes") or []) if str(x).strip()]
            sub_tokens = _tokenize_text(sub_id) | _tokenize_text(sub_name)
            for kw in sub_keywords[:80]:
                sub_tokens |= _tokenize_text(kw)
            sub_map[sub_id] = {
                "id": sub_id,
                "name": sub_name,
                "first_bucket": first_id,
                "keywords": sub_keywords,
                "include_path_prefixes": sub_prefixes,
                "tokens": sub_tokens,
            }
            first_map[first_id]["sub_buckets"].append(sub_id)

    return {
        "first": first_map,
        "sub": sub_map,
        "domains": sorted(set(domains)),
        "domain_map": domain_map,
    }


def lexical_taxonomy_candidates(query: str, index_obj: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    q = str(query or "").strip()
    q_lower = q.lower()
    q_tokens = _tokenize_text(q_lower)
    out_first: List[Dict[str, Any]] = []
    out_sub: List[Dict[str, Any]] = []

    for first_id, row in (index_obj.get("first") or {}).items():
        tokens = set(row.get("tokens") or set())
        score = 0.0
        if first_id.lower() in q_lower:
            score += 8.0
        score += 1.6 * float(len(q_tokens & tokens))
        for kw in row.get("keywords", [])[:50]:
            kw_l = str(kw).lower().strip()
            if kw_l and kw_l in q_lower:
                score += 3.0
        if score > 0.0:
            out_first.append({"id": first_id, "score": round(score, 4)})

    for sub_id, row in (index_obj.get("sub") or {}).items():
        tokens = set(row.get("tokens") or set())
        score = 0.0
        if sub_id.lower() in q_lower:
            score += 10.0
        score += 1.7 * float(len(q_tokens & tokens))
        for kw in row.get("keywords", [])[:50]:
            kw_l = str(kw).lower().strip()
            if kw_l and kw_l in q_lower:
                score += 3.2
        if score > 0.0:
            out_sub.append(
                {
                    "id": sub_id,
                    "first_bucket": str(row.get("first_bucket", "")),
                    "score": round(score, 4),
                }
            )

    out_first.sort(key=lambda x: (-float(x.get("score", 0.0)), str(x.get("id", ""))))
    out_sub.sort(key=lambda x: (-float(x.get("score", 0.0)), str(x.get("id", ""))))
    return {"first": out_first, "sub": out_sub}


def lexical_domain_candidates(query: str, index_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    q = str(query or "").strip()
    q_lower = q.lower()
    q_tokens = _tokenize_text(q_lower)
    out: List[Dict[str, Any]] = []

    for domain_id, row in (index_obj.get("domain_map") or {}).items():
        tokens = set(row.get("tokens") or set())
        score = 0.0
        if domain_id.lower() in q_lower:
            score += 6.0
        score += 1.3 * float(len(q_tokens & tokens))
        for kw in row.get("keywords", [])[:60]:
            kw_l = str(kw).strip().lower()
            if kw_l and kw_l in q_lower:
                score += 2.2
        for phrase in row.get("phrases", [])[:60]:
            ph_l = str(phrase).strip().lower()
            if ph_l and ph_l in q_lower:
                score += 3.2
        if score > 0.0:
            out.append({"id": str(domain_id), "score": round(score, 4)})

    out.sort(key=lambda x: (-float(x.get("score", 0.0)), str(x.get("id", ""))))
    return out


def keyword_first_bucket_hints(query: str, index_obj: Dict[str, Any]) -> List[str]:
    first_map = index_obj.get("first") or {}
    if not first_map or not QUERY_BUCKET_KEYWORD_HINTS:
        return []
    q = str(query or "").strip().lower()
    q_tokens = _tokenize_text(q)
    ranked: List[Tuple[float, str]] = []
    for bucket_id, patterns in QUERY_BUCKET_KEYWORD_HINTS.items():
        if bucket_id not in first_map:
            continue
        score = 0.0
        for pat in patterns:
            p = str(pat or "").strip().lower()
            if not p:
                continue
            if " " in p:
                if p in q:
                    score += 3.0
            else:
                if p in q_tokens:
                    score += 2.2
                elif p in q:
                    score += 0.8
        if score > 0.0:
            ranked.append((score, str(bucket_id)))
    ranked.sort(key=lambda x: (-float(x[0]), str(x[1])))
    out: List[str] = []
    for _, bucket_id in ranked:
        if bucket_id not in out:
            out.append(bucket_id)
        if len(out) >= max(1, int(QUERY_DECOMPOSER_MAX_HINTS)):
            break
    return out


def _extract_json_object(raw: str) -> Dict[str, Any]:
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


def llm_taxonomy_decompose(
    query: str,
    lexical: Dict[str, List[Dict[str, Any]]],
    index_obj: Dict[str, Any],
) -> Dict[str, Any]:
    if _decomposer_chat is None:
        return {}

    first_map = index_obj.get("first") or {}
    sub_map = index_obj.get("sub") or {}
    domain_ids = index_obj.get("domains") or []

    first_candidates = [str(x.get("id", "")) for x in lexical.get("first", []) if str(x.get("id", "")).strip()]
    sub_candidates = [str(x.get("id", "")) for x in lexical.get("sub", []) if str(x.get("id", "")).strip()]

    if not first_candidates:
        first_candidates = sorted(first_map.keys())[: int(QUERY_DECOMPOSER_MAX_LLM_CANDIDATES)]
    else:
        first_candidates = first_candidates[: int(QUERY_DECOMPOSER_MAX_LLM_CANDIDATES)]
    if not sub_candidates:
        sub_candidates = sorted(sub_map.keys())[: int(QUERY_DECOMPOSER_MAX_LLM_CANDIDATES)]
    else:
        sub_candidates = sub_candidates[: int(QUERY_DECOMPOSER_MAX_LLM_CANDIDATES)]

    first_payload: List[Dict[str, Any]] = []
    for fid in first_candidates:
        row = first_map.get(fid) or {}
        first_payload.append(
            {
                "id": fid,
                "name": str(row.get("name", fid)),
                "keywords": list(row.get("keywords", []))[: int(QUERY_DECOMPOSER_MAX_LLM_KEYWORDS)],
                "sub_buckets": list(row.get("sub_buckets", []))[: int(QUERY_DECOMPOSER_MAX_HINTS * 4)],
            }
        )

    sub_payload: List[Dict[str, Any]] = []
    for sid in sub_candidates:
        row = sub_map.get(sid) or {}
        sub_payload.append(
            {
                "id": sid,
                "name": str(row.get("name", sid)),
                "first_bucket": str(row.get("first_bucket", "")),
                "keywords": list(row.get("keywords", []))[: int(QUERY_DECOMPOSER_MAX_LLM_KEYWORDS)],
            }
        )

    system_prompt = (
        "You are a retrieval planner for a GraphRAG system. "
        "Return ONLY valid JSON with fields: "
        "domain (string), mode (basic|local|global|drift), "
        "first_buckets (array), sub_buckets (array), confidence (0..1), reason (string). "
        "Pick IDs only from provided candidates. Keep arrays short."
    )
    user_payload = {
        "query": str(query or ""),
        "allowed_domains": list(domain_ids),
        "candidates": {
            "first_buckets": first_payload,
            "sub_buckets": sub_payload,
        },
        "selection_rules": {
            "prefer_local_for_specific_how_to_and_incident_questions": True,
            "prefer_global_for_summary_trend_status_rollup_questions": True,
            "use_drift_for_exploratory_cross_domain_questions": True,
            "max_first_buckets": int(QUERY_DECOMPOSER_MAX_HINTS),
            "max_sub_buckets": int(QUERY_DECOMPOSER_MAX_HINTS),
        },
    }
    try:
        resp_obj = _decomposer_chat.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            temperature=0.0,
            top_p=0.1,
            max_completion_tokens=600,
        )
    except Exception as exc:
        LOGGER.warning("query decomposer LLM call failed: %s", exc)
        return {}

    raw = _decomposer_chat.extract_text(resp_obj)
    parsed = _extract_json_object(raw)
    if not parsed:
        return {}

    mode = str(parsed.get("mode", "basic")).strip().lower()
    if mode not in _MODE_VALUES:
        mode = "basic"

    first_buckets = [str(x).strip() for x in (parsed.get("first_buckets") or []) if str(x).strip()]
    first_buckets = [x for x in first_buckets if x in first_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]

    sub_buckets = [str(x).strip() for x in (parsed.get("sub_buckets") or []) if str(x).strip()]
    sub_buckets = [x for x in sub_buckets if x in sub_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]

    # If the model only picked sub-buckets, infer first-bucket hints.
    if sub_buckets and not first_buckets:
        inferred = [str((sub_map.get(s) or {}).get("first_bucket", "")).strip() for s in sub_buckets]
        first_buckets = [x for x in inferred if x and x in first_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]

    domain = str(parsed.get("domain", "")).strip()
    if domain and domain_ids and domain not in set(domain_ids):
        domain = ""

    confidence_raw = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "domain": domain,
        "mode": mode,
        "first_buckets": first_buckets,
        "sub_buckets": sub_buckets,
        "confidence": confidence,
        "reason": str(parsed.get("reason", "")).strip(),
    }


def build_query_decomposition(
    query: str,
    requested_domain: str,
    requested_mode: str,
    *,
    enabled: bool,
) -> Dict[str, Any]:
    requested_domain = str(requested_domain or "").strip()
    requested_mode = str(requested_mode or "basic").strip().lower()
    if requested_mode not in _DECOMPOSER_MODE_VALUES:
        requested_mode = "basic"

    plan: Dict[str, Any] = {
        "enabled": bool(enabled and QUERY_DECOMPOSER_ENABLED),
        "used_llm": False,
        "requested_domain": requested_domain,
        "requested_mode": requested_mode,
        "effective_domain": requested_domain,
        "effective_mode": "basic" if requested_mode == "auto" else requested_mode,
        "first_bucket_hints": [],
        "sub_bucket_hints": [],
        "lexical_first_bucket_candidates": [],
        "lexical_sub_bucket_candidates": [],
        "lexical_domain_candidates": [],
        "keyword_bucket_hints": [],
        "domain_prior_bucket_hints": [],
        "confidence": 0.0,
        "reason": "",
    }
    if not bool(enabled and QUERY_DECOMPOSER_ENABLED):
        return plan

    first_map = _taxonomy_index.get("first") or {}
    sub_map = _taxonomy_index.get("sub") or {}
    if not first_map and not sub_map:
        plan["reason"] = "taxonomy unavailable"
        return plan

    lexical = lexical_taxonomy_candidates(query, _taxonomy_index)
    lexical_first = lexical.get("first", [])[: int(QUERY_DECOMPOSER_MAX_HINTS * 3)]
    lexical_sub = lexical.get("sub", [])[: int(QUERY_DECOMPOSER_MAX_HINTS * 3)]
    lexical_domains = lexical_domain_candidates(query, _taxonomy_index)
    plan["lexical_first_bucket_candidates"] = lexical_first
    plan["lexical_sub_bucket_candidates"] = lexical_sub
    plan["lexical_domain_candidates"] = lexical_domains[: int(QUERY_DECOMPOSER_MAX_HINTS * 3)]

    first_hints = [str(x.get("id", "")).strip() for x in lexical_first if str(x.get("id", "")).strip()]
    first_hints = [x for x in first_hints if x in first_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]
    sub_hints = [str(x.get("id", "")).strip() for x in lexical_sub if str(x.get("id", "")).strip()]
    sub_hints = [x for x in sub_hints if x in sub_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]

    llm_plan: Dict[str, Any] = {}
    if QUERY_DECOMPOSER_USE_LLM:
        llm_plan = llm_taxonomy_decompose(query, lexical, _taxonomy_index)
        if llm_plan:
            plan["used_llm"] = True
            plan["confidence"] = float(llm_plan.get("confidence", 0.0))
            plan["reason"] = str(llm_plan.get("reason", "")).strip()
            llm_conf_hint = float(llm_plan.get("confidence", 0.0) or 0.0)
            llm_first = [str(x).strip() for x in llm_plan.get("first_buckets", []) if str(x).strip()]
            llm_sub = [str(x).strip() for x in llm_plan.get("sub_buckets", []) if str(x).strip()]
            if llm_first and llm_conf_hint >= float(QUERY_DECOMPOSER_MIN_CONFIDENCE):
                first_hints = [x for x in llm_first if x in first_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]
            if llm_sub and llm_conf_hint >= float(QUERY_DECOMPOSER_MIN_CONFIDENCE):
                sub_hints = [x for x in llm_sub if x in sub_map][: int(QUERY_DECOMPOSER_MAX_HINTS)]

    effective_domain = requested_domain
    effective_mode = "basic" if requested_mode == "auto" else requested_mode

    llm_conf = float(plan.get("confidence", 0.0) or 0.0)
    llm_domain = str(llm_plan.get("domain", "")).strip() if llm_plan else ""
    llm_mode = str(llm_plan.get("mode", "")).strip().lower() if llm_plan else ""

    if not effective_domain and llm_domain and llm_conf >= float(QUERY_DECOMPOSER_MIN_CONFIDENCE):
        effective_domain = llm_domain
    if not effective_domain and lexical_domains:
        top_dom = lexical_domains[0]
        top_id = str(top_dom.get("id", "")).strip()
        top_score = float(top_dom.get("score", 0.0) or 0.0)
        if top_id and top_score >= float(QUERY_DECOMPOSER_MIN_DOMAIN_SCORE):
            effective_domain = top_id

    keyword_hints = keyword_first_bucket_hints(query, _taxonomy_index)
    domain_prior_hints: List[str] = []
    if effective_domain:
        priors = _domain_first_bucket_priors.get(effective_domain, [])
        domain_prior_hints = [str(x).strip() for x in priors if str(x).strip() and str(x).strip() in first_map]
    merged_hints: List[str] = []
    for bucket_id in list(keyword_hints) + list(domain_prior_hints) + list(first_hints):
        bid = str(bucket_id or "").strip()
        if not bid or bid not in first_map or bid in merged_hints:
            continue
        merged_hints.append(bid)
        if len(merged_hints) >= int(QUERY_DECOMPOSER_MAX_HINTS):
            break
    if merged_hints:
        first_hints = merged_hints
    plan["keyword_bucket_hints"] = keyword_hints
    plan["domain_prior_bucket_hints"] = domain_prior_hints[: int(QUERY_DECOMPOSER_MAX_HINTS)]

    if requested_mode == "auto":
        if llm_mode in _MODE_VALUES and llm_mode != "basic" and llm_conf >= float(QUERY_DECOMPOSER_MIN_CONFIDENCE):
            effective_mode = llm_mode
        elif sub_hints or first_hints:
            effective_mode = "local"
        else:
            effective_mode = "basic"
    elif requested_mode == "basic" and QUERY_DECOMPOSER_UPGRADE_BASIC_MODE:
        if llm_mode in _MODE_VALUES and llm_mode != "basic" and llm_conf >= float(QUERY_DECOMPOSER_MIN_CONFIDENCE):
            effective_mode = llm_mode

    plan["effective_domain"] = effective_domain
    plan["effective_mode"] = effective_mode
    plan["first_bucket_hints"] = first_hints
    plan["sub_bucket_hints"] = sub_hints
    return plan


_SYNTHESIS_QUERY_RE = re.compile(
    r"\b(synthes(?:is|ize)|combine|compare|workflow|playbook|prerequisite|constraint|architecture briefing|incident[- ]response|capability map)\b",
    re.IGNORECASE,
)
_FACTUAL_QUERY_RE = re.compile(
    r"^\s*(what|who|when|where|which|how)\b|\b(what is|who is|when did|where is|define)\b",
    re.IGNORECASE,
)
_COMPARISON_QUERY_RE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference|tradeoff|pros and cons)\b",
    re.IGNORECASE,
)
_OWNERSHIP_QUERY_RE = re.compile(
    r"\b(owner|ownership|responsible|responsibility|who owns|who is accountable|point of contact)\b",
    re.IGNORECASE,
)
_PEOPLE_ROLE_QUERY_RE = re.compile(
    r"(?:what role does|what does|what did|who is|what is the role of|what are .{0,30} responsibilities)\b.{1,60}\b(?:have|do|handle|manage|work on|run|maintain|support|oversee|own)\b",
    re.IGNORECASE,
)
_OVERVIEW_QUERY_RE = re.compile(
    r"\b(overview|high[- ]level|summary|summarize|trend|landscape|status|rollup|broad)\b",
    re.IGNORECASE,
)
_INFRA_OVERVIEW_QUERY_RE = re.compile(
    r"\b("
    r"(?:what|how)\s+does\b[^?]{0,140}\b(?:look\s+like|structured|organized)|"
    r"(?:dns|domain\s+name\s+system|network|infrastructure|architecture|topology)\s+"
    r"(?:infrastructure|architecture|topology|design|layout|look\s+like)"
    r")\b",
    re.IGNORECASE,
)
_INCIDENT_QUERY_RE = re.compile(
    r"\b(incident|failure mode|recovery|postmortem|root cause|outage|runbook)\b",
    re.IGNORECASE,
)
_TROUBLESHOOT_QUERY_RE = re.compile(
    r"\b("
    r"troubleshoot(?:ing)?|"
    r"root cause|"
    r"why did|why does|"
    r"fail(?:ed|ure|ing)?|"
    r"error|"
    r"remediat(?:e|ion)|"
    r"fix(?:ed|ing)?|"
    r"resolved?"
    r")\b",
    re.IGNORECASE,
)
_RISK_QUERY_RE = re.compile(
    r"\b(risk|threat|indicator|signal|anomal(?:y|ies)|alert|detection|exposure|vulnerab(?:ility|le))\b",
    re.IGNORECASE,
)
_PROCEDURE_STEPS_QUERY_RE = re.compile(
    r"\b(steps?|procedure|prior to|before starting|how to|sequence|workflow|instruction(?:s)?)\b",
    re.IGNORECASE,
)
_COMPLIANCE_CONSTRAINT_QUERY_RE = re.compile(
    r"\b(must|required|required to|shall|constraint|policy|compliance|control|prohibited|not allowed)\b",
    re.IGNORECASE,
)
_DEFINITION_QUERY_RE = re.compile(
    r"\b(what is described as|defined as|definition|refer to|abbreviation|value of|set to|stand for|means?)\b",
    re.IGNORECASE,
)
_ACCOUNT_LOOKUP_QUERY_RE = re.compile(
    r"\b("
    r"username|"
    r"badge(?:\s+number)?|"
    r"account\s+status|"
    r"locked\s+date|"
    r"service(?:\(s\)|s)?|"
    r"direct\s+phone|"
    r"extension"
    r")\b",
    re.IGNORECASE,
)
_TIMELINE_QUERY_RE = re.compile(
    r"\b(timeline|chronolog|version|revision|rev\b|changed?|update(?:d)?|date|history|when did)\b",
    re.IGNORECASE,
)
_RECENCY_QUERY_RE = re.compile(
    r"\b(recent(?:ly)?|latest|newest|most recent|what's new|current(?:ly)?|outdated|stale|up[- ]to[- ]date)\b",
    re.IGNORECASE,
)
_COMPUTER_IP_OS_QUERY_RE = re.compile(
    r"\b(computer(?:\s+name)?|ip(?:\s+address)?|operating\s+system|\bos\b)\b",
    re.IGNORECASE,
)
_PROCEDURE_PAIR_QUERY_RE = re.compile(
    r"\b(prior to starting work|before starting work|job site|available at the job site)\b",
    re.IGNORECASE,
)
_EXACT_VALUE_QUERY_RE = re.compile(
    r"\b(value of|set to|field\b|what is the value|which value)\b",
    re.IGNORECASE,
)


def _hit_item_id(row: Dict[str, Any]) -> str:
    return str(row.get("artifact_id") or row.get("chunk_id") or "").strip()


def _hit_artifact_type(row: Dict[str, Any]) -> str:
    txt = str(row.get("artifact_type") or "").strip().lower()
    if txt:
        return txt
    if str(row.get("artifact_id") or "").strip():
        return "graphrag_artifact"
    return "chunk"


def _is_text_evidence_hit(row: Dict[str, Any]) -> bool:
    artifact_id = str(row.get("artifact_id") or "").strip()
    if not artifact_id:
        return True
    artifact_type = _hit_artifact_type(row)
    if artifact_type in {
        "chunk",
        "text_unit",
        "log_entry",
        "procedure_step",
        "procedure_requirement",
        "compliance_constraint",
        "risk_signal",
        "inventory_row",
        "account_status_row",
        "troubleshooting_event",
    }:
        return True
    if artifact_type == "troubleshooting_tuple" and QUERY_TROUBLESHOOTING_TUPLE_AS_TEXT:
        return True
    return False


def _is_troubleshooting_tuple_hit(row: Dict[str, Any]) -> bool:
    return _hit_artifact_type(row) in {"troubleshooting_tuple", "troubleshooting_event"}


def _is_claim_hit(row: Dict[str, Any]) -> bool:
    return _hit_artifact_type(row) == "claim"


def _is_taxonomy_hit(row: Dict[str, Any]) -> bool:
    return _hit_artifact_type(row).startswith("taxonomy_")


def _hit_path_key(row: Dict[str, Any]) -> str:
    path = str(row.get("path") or "").strip()
    if path:
        return path
    return str(row.get("doc_id") or _hit_item_id(row)).strip()


def _row_shape_rescue_queries(query: str, intent: str) -> Tuple[List[str], List[str]]:
    q = str(query or "").strip()
    intent_s = str(intent or "").strip().lower()
    if not q:
        return [], []
    queries: List[str] = []
    patterns: List[str] = []
    if _COMPUTER_IP_OS_QUERY_RE.search(q):
        queries.append(f'{q} "computer name" "operating system" "ip address"')
        patterns.append("computer|ip address|operating system")
    if _ACCOUNT_LOOKUP_QUERY_RE.search(q):
        queries.append(f'{q} "username" "status" "locked date" "service"')
        patterns.append("username|status|locked date|service")
    if intent_s in {"procedure_steps", "troubleshooting"} and _PROCEDURE_PAIR_QUERY_RE.search(q):
        queries.append(f'{q} "prior to starting work" "must be available at the job site"')
        patterns.append("prior to starting work|must be available at the job site")
    if intent_s == "risk_detection" or _RISK_QUERY_RE.search(q):
        queries.append(f'{q} "script" "logs" "daily report" "needs work"')
        patterns.append("script|logs|report|limitation")
    if intent_s == "compliance_constraint" or _COMPLIANCE_CONSTRAINT_QUERY_RE.search(q):
        queries.append(f'{q} "must" "required" "policy" "control"')
        patterns.append("must|required|policy|control")
    if intent_s == "definition" or _is_definition_like_query(q):
        queries.append(f'{q} "defined as" "refers to" "value is"')
        patterns.append("defined as|refers to|value is")
    return queries, patterns


def _is_definition_like_query(query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return False
    # Do not treat "what does ... look like" architecture/overview prompts as strict definitions.
    if re.search(r"\bwhat does\b[^?]{0,120}\blook like\b", q):
        return False
    return bool(_DEFINITION_QUERY_RE.search(q))


def _is_overview_like_query(query: str) -> bool:
    q = str(query or "").strip()
    if not q:
        return False
    if _OVERVIEW_QUERY_RE.search(q):
        return True
    if _INFRA_OVERVIEW_QUERY_RE.search(q):
        if re.search(r"\b(dns|domain\s+name\s+system|network|infrastructure|architecture|topology)\b", q, re.IGNORECASE):
            return True
    return False


def _extract_requested_person_name(query: str) -> str:
    q = str(query or "").strip()
    if not q:
        return ""
    name_pat = r"([A-Za-z][A-Za-z'`.-]+(?:\s+[A-Za-z][A-Za-z'`.-]+){1,2})"
    patterns = (
        rf"\bwhat does\s+{name_pat}\s+do\b",
        rf"\bwhat\s+role\s+does\s+{name_pat}\s+have\b",
        rf"\bwho is\s+{name_pat}\b",
        rf"\bwhat is\s+{name_pat}['’]s\s+(?:role|job|title)\b",
        rf"\b(?:role|job|title)\s+of\s+{name_pat}\b",
        rf"\btell me about\s+{name_pat}\b",
    )
    for pat in patterns:
        m = re.search(pat, q, flags=re.IGNORECASE)
        if m is None:
            continue
        cand = str(m.group(1) or "").strip()
        parts = [p for p in re.findall(r"[A-Za-z][A-Za-z'`.-]*", cand) if p]
        if len(parts) < 2:
            continue
        return " ".join(parts[:3]).strip()
    return ""


def _ownership_role_rescue_queries(query: str) -> Tuple[List[str], List[str]]:
    q = str(query or "").strip()
    if not q:
        return [], []
    requested_name = _extract_requested_person_name(q)
    queries: List[str] = []
    patterns: List[str] = []
    if requested_name:
        name_parts = [p for p in re.findall(r"[A-Za-z][A-Za-z'`.-]*", requested_name) if p]
        if len(name_parts) >= 2:
            first = str(name_parts[0]).strip()
            last = str(name_parts[-1]).strip()
            if first and last:
                queries.append(f'"{first} {last}" role title hired as engineer technician manager')
                queries.append(f'"{last}, {first}" role title hired as engineer technician manager')
                queries.append(f'"{first} {last}" adjunct technician tech liaison group leader responsibilities')
                patterns.extend(["first last role", "last, first role", "first last adjunct/technician"])
    else:
        queries.append(f'{q} "hired as" role title engineer technician manager')
        patterns.append("hired as|role|title")
    return queries, patterns


def _path_matches(candidate_path: str, source_path: str) -> bool:
    cand = str(candidate_path or "").strip()
    src = str(source_path or "").strip()
    if not cand or not src:
        return False
    cand_norm = cand.replace("\\", "/").rstrip("/").lower()
    src_norm = src.replace("\\", "/").rstrip("/").lower()
    if cand_norm == src_norm:
        return True
    cand_parts = [p for p in cand_norm.split("/") if p]
    src_parts = [p for p in src_norm.split("/") if p]
    if not cand_parts or not src_parts:
        return False
    # If only a basename is provided, allow basename matching.
    if len(src_parts) == 1 or len(cand_parts) == 1:
        return cand_parts[-1] == src_parts[-1]
    if cand_norm.endswith("/" + src_norm) or src_norm.endswith("/" + cand_norm):
        return True
    suffix_overlap = 0
    for a, b in zip(reversed(cand_parts), reversed(src_parts)):
        if a != b:
            break
        suffix_overlap += 1
    required_suffix = min(3, len(cand_parts), len(src_parts))
    return suffix_overlap >= max(2, required_suffix)


def _path_rank_for_rows(rows: List[Dict[str, Any]], source_path: str) -> int:
    if not str(source_path or "").strip():
        return 0
    for idx, row in enumerate(rows, start=1):
        if _path_matches(str(row.get("path") or ""), source_path):
            return int(idx)
    return 0


def _build_rank_trace(rows: List[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows[: max(1, int(limit))], start=1):
        out.append(
            {
                "rank": int(idx),
                "item_id": _hit_item_id(row),
                "path": str(row.get("path") or ""),
                "artifact_type": _hit_artifact_type(row),
                "fusion_score": float(row.get("fusion_score", 0.0) or 0.0),
                "rerank_score": float(row.get("rerank_score", 0.0) or 0.0),
            }
        )
    return out


def build_query_intent_profile(query: str, requested_mode: str, effective_mode: str) -> Dict[str, Any]:
    q = str(query or "")
    q_terms = re.findall(r"[a-z0-9]+", q.lower())
    q_phrases = [str(p).strip().lower() for p in re.findall(r"[\"“]([^\"”]{2,80})[\"”]", q) if str(p).strip()]
    exact_value_query = bool(_EXACT_VALUE_QUERY_RE.search(q))
    intent = "lookup"
    requires_text = True
    min_text = max(1, int(QUERY_MIN_TEXT_EVIDENCE))
    max_claim_ratio = max(0.0, min(1.0, float(QUERY_MAX_CLAIM_RATIO)))
    max_taxonomy_ratio = max(0.0, min(1.0, float(QUERY_MAX_TAXONOMY_RATIO)))
    max_tuple_ratio = max(0.0, min(1.0, float(QUERY_MAX_TROUBLESHOOTING_TUPLE_RATIO)))
    max_per_path = max(1, int(QUERY_MAX_PER_PATH))
    rescue_mode = ""
    rerank_pool_target = max(int(QUERY_CANDIDATE_POOL), 40)

    if _TROUBLESHOOT_QUERY_RE.search(q):
        intent = "troubleshooting"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.10)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.10)
        max_tuple_ratio = min(max_tuple_ratio, 0.25)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _RISK_QUERY_RE.search(q):
        intent = "risk_detection"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.10)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.10)
        max_tuple_ratio = min(max_tuple_ratio, 0.25)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _PROCEDURE_STEPS_QUERY_RE.search(q):
        intent = "procedure_steps"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.10)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.12)
        max_tuple_ratio = min(max_tuple_ratio, 0.20)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _COMPLIANCE_CONSTRAINT_QUERY_RE.search(q):
        intent = "compliance_constraint"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.08)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.10)
        max_tuple_ratio = min(max_tuple_ratio, 0.15)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _PEOPLE_ROLE_QUERY_RE.search(q):
        intent = "ownership_role"
        min_text = max(min_text, 3)
        max_claim_ratio = min(max_claim_ratio, 0.40)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.20)
        max_tuple_ratio = min(max_tuple_ratio, 0.20)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 64)
    elif _is_overview_like_query(q):
        intent = "overview"
        min_text = max(3, min_text)
        max_claim_ratio = min(0.60, max_claim_ratio + 0.12)
        max_taxonomy_ratio = min(0.70, max_taxonomy_ratio + 0.20)
        max_tuple_ratio = min(0.30, max_tuple_ratio + 0.08)
        rescue_mode = "basic"
        rerank_pool_target = max(rerank_pool_target, 56)
    elif _is_definition_like_query(q):
        intent = "definition"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.10)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.10)
        max_tuple_ratio = min(max_tuple_ratio, 0.15)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _RECENCY_QUERY_RE.search(q):
        intent = "recency"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.10)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.10)
        max_tuple_ratio = min(max_tuple_ratio, 0.15)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _ACCOUNT_LOOKUP_QUERY_RE.search(q) and _FACTUAL_QUERY_RE.search(q):
        # Prevent generic "date" in account/status questions from being routed to timeline intent.
        intent = "factual_lookup"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.08)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.08)
        max_tuple_ratio = min(max_tuple_ratio, 0.10)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _TIMELINE_QUERY_RE.search(q):
        intent = "timeline_version"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.12)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.10)
        max_tuple_ratio = min(max_tuple_ratio, 0.20)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _FACTUAL_QUERY_RE.search(q) and len(q_terms) <= 16:
        intent = "factual_lookup"
        min_text = max(min_text, 6)
        max_claim_ratio = min(max_claim_ratio, 0.08)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.08)
        max_tuple_ratio = min(max_tuple_ratio, 0.10)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 80)
    elif _COMPARISON_QUERY_RE.search(q):
        intent = "comparison"
        min_text = max(min_text, 5)
        max_claim_ratio = min(max_claim_ratio, 0.25)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.20)
        max_tuple_ratio = min(max_tuple_ratio, 0.20)
        rescue_mode = "basic"
        rerank_pool_target = max(rerank_pool_target, 64)
    elif _OWNERSHIP_QUERY_RE.search(q):
        intent = "ownership"
        min_text = max(min_text, 4)
        max_claim_ratio = min(max_claim_ratio, 0.22)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.22)
        max_tuple_ratio = min(max_tuple_ratio, 0.18)
        rescue_mode = "phrase"
        rerank_pool_target = max(rerank_pool_target, 64)
    elif _SYNTHESIS_QUERY_RE.search(q) or _INCIDENT_QUERY_RE.search(q):
        intent = "synthesis"
        min_text = max(min_text, 5)
        max_claim_ratio = min(max_claim_ratio, 0.30)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.25)
        max_tuple_ratio = min(max_tuple_ratio, 0.25)
        rescue_mode = "basic"
        rerank_pool_target = max(rerank_pool_target, 56)
    elif _is_overview_like_query(q):
        intent = "overview"
        min_text = max(2, min_text - 1)
        max_claim_ratio = min(0.65, max_claim_ratio + 0.15)
        max_taxonomy_ratio = min(0.75, max_taxonomy_ratio + 0.25)
        max_tuple_ratio = min(0.35, max_tuple_ratio + 0.10)
        rerank_pool_target = max(rerank_pool_target, 48)
    else:
        # Default lookup mode prefers chunk/text-unit evidence and limits claim saturation.
        min_text = max(3, min_text)
        max_claim_ratio = min(max_claim_ratio, 0.35)
        max_taxonomy_ratio = min(max_taxonomy_ratio, 0.30)
        max_tuple_ratio = min(max_tuple_ratio, 0.20)
        rerank_pool_target = max(rerank_pool_target, 48)

    return {
        "intent": intent,
        "requested_mode": str(requested_mode or ""),
        "effective_mode": str(effective_mode or ""),
        "query_text": str(q),
        "query_terms": q_terms[:40],
        "query_phrases": q_phrases[:12],
        "exact_value_query": bool(exact_value_query),
        "requires_text_evidence": bool(requires_text),
        "min_text_evidence": int(max(1, min_text)),
        "max_claim_ratio": float(max(0.0, min(1.0, max_claim_ratio))),
        "max_taxonomy_ratio": float(max(0.0, min(1.0, max_taxonomy_ratio))),
        "max_troubleshooting_tuple_ratio": float(max(0.0, min(1.0, max_tuple_ratio))),
        "max_per_path": int(max_per_path),
        "rescue_mode": rescue_mode,
        "rerank_pool_target": int(max(1, rerank_pool_target)),
    }


def summarize_hit_mix(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    artifact_counts: Dict[str, int] = defaultdict(int)
    source_counts: Dict[str, int] = defaultdict(int)
    retrieval_mode_counts: Dict[str, int] = defaultdict(int)
    generation_counts: Dict[str, int] = defaultdict(int)
    unique_paths: Set[str] = set()
    text_evidence = 0
    claim_hits = 0
    taxonomy_hits = 0
    missing_taxonomy_fields = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "unknown").strip()
        if source:
            source_counts[source] += 1

        artifact_type = _hit_artifact_type(row)
        artifact_counts[artifact_type] += 1
        if _is_text_evidence_hit(row):
            text_evidence += 1
        if _is_claim_hit(row):
            claim_hits += 1
        if _is_taxonomy_hit(row):
            taxonomy_hits += 1
        if not str(row.get("first_bucket") or "").strip() and not str(row.get("sub_bucket") or "").strip():
            missing_taxonomy_fields += 1

        retrieval_mode = str(row.get("retrieval_mode") or "").strip()
        if retrieval_mode:
            retrieval_mode_counts[retrieval_mode] += 1
        generation = str(row.get("artifact_generation") or "").strip() or "deterministic"
        generation_counts[generation] += 1

        path_key = _hit_path_key(row)
        if path_key:
            unique_paths.add(path_key)

    return {
        "total_hits": int(len(rows)),
        "text_evidence_hits": int(text_evidence),
        "claim_hits": int(claim_hits),
        "taxonomy_hits": int(taxonomy_hits),
        "missing_taxonomy_fields_hits": int(missing_taxonomy_fields),
        "unique_paths": int(len(unique_paths)),
        "artifact_type_counts": dict(sorted(artifact_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "retrieval_mode_counts": dict(sorted(retrieval_mode_counts.items())),
        "generation_counts": dict(sorted(generation_counts.items())),
    }


def merge_rescue_hits(primary: List[Dict[str, Any]], rescue: List[Dict[str, Any]], source_label: str) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}

    for rank, row in enumerate(primary, start=1):
        item_id = _hit_item_id(row)
        if not item_id:
            continue
        cur = dict(row)
        cur_score = float(cur.get("fusion_score", 0.0) or 0.0)
        if cur_score <= 0.0:
            cur_score = 1.0 / float(FUSION_K + rank)
            cur["fusion_score"] = cur_score
        sources = sorted(set(str(s) for s in (cur.get("sources") or []) if str(s)))
        if sources:
            cur["sources"] = sources
        by_id[item_id] = cur

    for rank, row in enumerate(rescue, start=1):
        item_id = _hit_item_id(row)
        if not item_id:
            continue
        bonus = 1.0 / float(FUSION_K + rank)
        if item_id in by_id:
            cur = by_id[item_id]
            cur["fusion_score"] = float(cur.get("fusion_score", 0.0) or 0.0) + bonus
            sources = set(str(s) for s in (cur.get("sources") or []) if str(s))
            sources.add(source_label)
            cur["sources"] = sorted(sources)
            continue
        cur = dict(row)
        cur["fusion_score"] = float(cur.get("fusion_score", 0.0) or 0.0) + bonus
        sources = set(str(s) for s in (cur.get("sources") or []) if str(s))
        sources.add(source_label)
        cur["sources"] = sorted(sources)
        by_id[item_id] = cur

    ranked = sorted(
        by_id.values(),
        key=lambda row: (
            -float(row.get("fusion_score", 0.0) or 0.0),
            int(row.get("rank") or 9999),
            str(_hit_item_id(row)),
        ),
    )
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(ranked, start=1):
        out.append(
            {
                **row,
                "rank": idx,
                "text_preview": str(row.get("text", ""))[:400],
            }
        )
    return out


def rebalance_ranked_hits(
    hits: List[Dict[str, Any]],
    top_k: int,
    profile: Dict[str, Any],
    *,
    source_path_hint: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    ranked = [dict(h) for h in hits if isinstance(h, dict)]
    top_n = max(1, int(top_k))
    if not ranked:
        return [], {"applied": False, "reason": "no_hits"}

    before_ids = [_hit_item_id(row) for row in ranked[:top_n]]
    before_mix = summarize_hit_mix(ranked[:top_n])

    min_text = int(max(1, profile.get("min_text_evidence", QUERY_MIN_TEXT_EVIDENCE)))
    claim_ratio = float(profile.get("max_claim_ratio", QUERY_MAX_CLAIM_RATIO))
    taxonomy_ratio = float(profile.get("max_taxonomy_ratio", QUERY_MAX_TAXONOMY_RATIO))
    tuple_ratio = float(profile.get("max_troubleshooting_tuple_ratio", QUERY_MAX_TROUBLESHOOTING_TUPLE_RATIO))
    max_per_path = int(max(1, profile.get("max_per_path", QUERY_MAX_PER_PATH)))
    src_hint = str(source_path_hint or "").strip()
    intent_name = str(profile.get("intent", "")).strip().lower()
    query_text = str(profile.get("query_text", "")).strip().lower()
    query_terms = {
        str(t).strip().lower()
        for t in (profile.get("query_terms") or [])
        if str(t).strip()
    }
    query_phrases = [
        str(p).strip().lower()
        for p in (profile.get("query_phrases") or [])
        if str(p).strip()
    ]
    exact_value_query = bool(profile.get("exact_value_query", False))
    field_name = ""
    if query_text:
        m_field = re.search(r'\bfield\s+[\"“]?([a-z0-9_./ -]{2,64})[\"”]?', query_text)
        if m_field:
            field_name = str(m_field.group(1) or "").strip().lower().strip(" .,:;")
    if src_hint:
        max_per_path = max(max_per_path, min(top_n, max(1, int(QUERY_SOURCE_HINT_MAX_PER_PATH))))
    source_hint_min_hits = 0
    if src_hint and intent_name in {
        "risk_detection",
        "procedure_steps",
        "compliance_constraint",
        "definition",
        "factual_lookup",
        "lookup",
        "timeline_version",
    }:
        min_hits_floor = int(max(1, QUERY_SOURCE_HINT_MIN_FINAL_HITS))
        min_hits_floor = max(min_hits_floor, int(max(1, QUERY_SOURCE_HINT_MIN_FINAL_HITS_TARGETED)))
        if exact_value_query:
            min_hits_floor = max(min_hits_floor, int(max(1, QUERY_SOURCE_HINT_MIN_FINAL_HITS_EXACT)))
        source_hint_min_hits = min(top_n, max(1, int(min_hits_floor)))

    claim_cap = int(top_n * max(0.0, min(1.0, claim_ratio)))
    taxonomy_cap = int(top_n * max(0.0, min(1.0, taxonomy_ratio)))
    tuple_cap = int(top_n * max(0.0, min(1.0, tuple_ratio)))
    if claim_ratio > 0 and claim_cap == 0:
        claim_cap = 1
    if taxonomy_ratio > 0 and taxonomy_cap == 0:
        taxonomy_cap = 1
    if tuple_ratio > 0 and tuple_cap == 0:
        tuple_cap = 1

    selected: List[Dict[str, Any]] = []
    selected_ids: Set[str] = set()
    path_counts: Dict[str, int] = defaultdict(int)
    claim_count = 0
    taxonomy_count = 0
    tuple_count = 0
    text_count = 0

    def can_add(row: Dict[str, Any], *, enforce_caps: bool, enforce_path: bool) -> bool:
        item_id = _hit_item_id(row)
        if not item_id or item_id in selected_ids:
            return False
        if enforce_path:
            key = _hit_path_key(row)
            if key and path_counts[key] >= max_per_path:
                return False
        if enforce_caps:
            if _is_claim_hit(row) and claim_count >= claim_cap:
                return False
            if _is_taxonomy_hit(row) and taxonomy_count >= taxonomy_cap:
                return False
            if _is_troubleshooting_tuple_hit(row) and tuple_count >= tuple_cap:
                return False
        return True

    def add_row(row: Dict[str, Any]) -> None:
        nonlocal claim_count, taxonomy_count, tuple_count, text_count
        item_id = _hit_item_id(row)
        if not item_id:
            return
        selected.append(dict(row))
        selected_ids.add(item_id)
        key = _hit_path_key(row)
        if key:
            path_counts[key] += 1
        if _is_claim_hit(row):
            claim_count += 1
        if _is_taxonomy_hit(row):
            taxonomy_count += 1
        if _is_troubleshooting_tuple_hit(row):
            tuple_count += 1
        if _is_text_evidence_hit(row):
            text_count += 1

    # Pass 1: guarantee text evidence coverage.
    for row in ranked:
        if len(selected) >= top_n or text_count >= min_text:
            break
        if not _is_text_evidence_hit(row):
            continue
        if can_add(row, enforce_caps=False, enforce_path=True):
            add_row(row)

    # Pass 2: fill with strict saturation/path caps.
    for row in ranked:
        if len(selected) >= top_n:
            break
        if can_add(row, enforce_caps=True, enforce_path=True):
            add_row(row)

    # Pass 3: relax claim/taxonomy caps when needed.
    for row in ranked:
        if len(selected) >= top_n:
            break
        if can_add(row, enforce_caps=False, enforce_path=True):
            add_row(row)

    # Pass 4: relax path diversity as last resort.
    for row in ranked:
        if len(selected) >= top_n:
            break
        if can_add(row, enforce_caps=False, enforce_path=False):
            add_row(row)

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(selected[:top_n], start=1):
        out.append(
            {
                **row,
                "rank": idx,
                "text_preview": str(row.get("text", ""))[:400],
            }
        )

    source_hint_injected = 0
    if src_hint and source_hint_min_hits > 0 and out:
        current_source_ids: Set[str] = {
            _hit_item_id(row)
            for row in out
            if _path_matches(str(row.get("path") or ""), src_hint) and _hit_item_id(row)
        }
        source_candidates = [
            dict(row)
            for row in ranked
            if _path_matches(str(row.get("path") or ""), src_hint)
            and _hit_item_id(row)
            and _hit_item_id(row) not in current_source_ids
        ]
        def _text_quality_score(txt: str) -> float:
            raw = str(txt or "")
            if not raw:
                return 0.0
            alpha = len(re.findall(r"[A-Za-z]", raw))
            total = max(1, len(raw))
            ratio = float(alpha) / float(total)
            penalty = 0.0
            low = raw.lower()
            if "[content_types].xml" in low or "rels/.rels" in low:
                penalty += 0.25
            if "<?xml" in low or "xmlns:" in low:
                penalty += 0.08
            if "pk |" in low or low.startswith("pk"):
                penalty += 0.12
            return max(0.0, ratio - penalty)

        def _source_candidate_score(row: Dict[str, Any]) -> Tuple[int, int, int, int, float, int]:
            txt = str(row.get("text") or row.get("text_preview") or "")
            low = txt.lower()
            term_hits = sum(1 for t in query_terms if t and t in low)
            phrase_hits = sum(1 for p in query_phrases if p and p in low)
            field_hits = 1 if field_name and field_name in low else 0
            numeric_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", low))
            text_bonus = 1 if _is_text_evidence_hit(row) else 0
            quality = _text_quality_score(txt)
            rank_penalty = -int(row.get("rank") or 9999)
            return (
                int(field_hits),
                int(phrase_hits),
                int(term_hits),
                int(numeric_hits),
                float(quality),
                int(text_bonus + rank_penalty),
            )

        source_candidates = sorted(source_candidates, key=_source_candidate_score, reverse=True)
        for cand in source_candidates:
            if len(current_source_ids) >= int(source_hint_min_hits):
                break
            cand_text = str(cand.get("text") or cand.get("text_preview") or "")
            cand_quality = _text_quality_score(cand_text)
            if _is_text_evidence_hit(cand) and cand_quality < 0.12:
                continue
            replace_idx = -1
            for idx in range(len(out) - 1, -1, -1):
                cur = out[idx]
                if _path_matches(str(cur.get("path") or ""), src_hint):
                    continue
                replace_idx = idx
                break
            if replace_idx < 0:
                break
            out[replace_idx] = {
                **cand,
                "rank": int(replace_idx + 1),
                "text_preview": str(cand.get("text", ""))[:400],
            }
            current_source_ids.add(_hit_item_id(cand))
            source_hint_injected += 1
        for idx, row in enumerate(out, start=1):
            row["rank"] = int(idx)

    after_ids = [_hit_item_id(row) for row in out]
    after_mix = summarize_hit_mix(out)
    final_source_hits = (
        sum(1 for row in out if _path_matches(str(row.get("path") or ""), src_hint))
        if src_hint
        else 0
    )
    diag = {
        "applied": True,
        "changed": bool(before_ids != after_ids),
        "required_min_text_evidence": int(min_text),
        "claim_cap": int(claim_cap),
        "taxonomy_cap": int(taxonomy_cap),
        "troubleshooting_tuple_cap": int(tuple_cap),
        "max_per_path": int(max_per_path),
        "before": before_mix,
        "after": after_mix,
        "source_hint": {
            "active": bool(src_hint),
            "max_per_path_effective": int(max_per_path),
            "min_final_hits_target": int(source_hint_min_hits),
            "final_hits": int(final_source_hits),
            "injected_hits": int(source_hint_injected),
        },
    }
    return out, diag


def enforce_first_bucket_hint_floor(
    selected: List[Dict[str, Any]],
    ranked_pool: List[Dict[str, Any]],
    first_bucket_hints: List[str],
    top_k: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    top_n = max(1, int(top_k))
    hints = [str(x or "").strip() for x in (first_bucket_hints or []) if str(x or "").strip()]
    if not hints:
        return selected[:top_n], {"applied": False, "reason": "no_first_bucket_hints"}

    out = [dict(r) for r in selected[:top_n] if isinstance(r, dict)]
    ranked = [dict(r) for r in ranked_pool if isinstance(r, dict)]
    if not ranked:
        return out, {"applied": False, "reason": "empty_ranked_pool"}

    target = str(hints[0]).strip()
    if not target:
        return out, {"applied": False, "reason": "empty_target_hint"}

    def _normalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows[:top_n], start=1):
            normalized.append(
                {
                    **row,
                    "rank": idx,
                    "text_preview": str(row.get("text", ""))[:400],
                }
            )
        return normalized

    selected_idx = next((i for i, row in enumerate(out) if str(row.get("first_bucket") or "").strip() == target), -1)
    if selected_idx >= 0:
        return _normalize(out), {
            "applied": False,
            "reason": "target_first_bucket_already_present",
            "target_first_bucket": target,
            "selected_rank": int(selected_idx + 1),
        }

    candidate_rows = [row for row in ranked if str(row.get("first_bucket") or "").strip() == target]
    if not candidate_rows:
        return _normalize(out), {
            "applied": False,
            "reason": "target_first_bucket_not_found_in_pool",
            "target_first_bucket": target,
        }
    preferred = [row for row in candidate_rows if _is_text_evidence_hit(row)]
    chosen = dict(preferred[0] if preferred else candidate_rows[0])
    chosen_id = _hit_item_id(chosen)
    if not chosen_id:
        return _normalize(out), {"applied": False, "reason": "candidate_missing_item_id", "target_first_bucket": target}

    chosen_rank = next((i for i, row in enumerate(ranked, start=1) if _hit_item_id(row) == chosen_id), 0)
    selected_chosen_idx = next((i for i, row in enumerate(out) if _hit_item_id(row) == chosen_id), -1)
    if selected_chosen_idx >= 0:
        return _normalize(out), {
            "applied": False,
            "reason": "candidate_already_present",
            "target_first_bucket": target,
            "selected_rank": int(selected_chosen_idx + 1),
            "chosen_rank": int(chosen_rank),
        }

    replace_idx = -1
    for idx in range(len(out) - 1, -1, -1):
        row = out[idx]
        row_fb = str(row.get("first_bucket") or "").strip()
        if row_fb == target:
            continue
        if _is_taxonomy_hit(row) or _is_claim_hit(row) or _is_troubleshooting_tuple_hit(row) or not _is_text_evidence_hit(row):
            replace_idx = idx
            break
    if replace_idx < 0 and out:
        replace_idx = len(out) - 1
    if replace_idx < 0:
        return _normalize(out), {"applied": False, "reason": "no_replacement_slot", "target_first_bucket": target}

    replaced_item_id = _hit_item_id(out[replace_idx])
    out[replace_idx] = chosen
    normalized = _normalize(out)
    return normalized, {
        "applied": True,
        "reason": "inserted_target_first_bucket_hit",
        "target_first_bucket": target,
        "chosen_rank": int(chosen_rank),
        "inserted_item_id": chosen_id,
        "replaced_item_id": replaced_item_id,
    }


def enforce_source_path_floor(
    selected: List[Dict[str, Any]],
    ranked_pool: List[Dict[str, Any]],
    source_path_hint: str,
    top_k: int,
    query: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    src = str(source_path_hint or "").strip()
    top_n = max(1, int(top_k))
    if not src:
        return selected[:top_n], {"applied": False, "reason": "no_source_hint"}

    ranked = [dict(r) for r in ranked_pool if isinstance(r, dict)]
    out = [dict(r) for r in selected[:top_n] if isinstance(r, dict)]
    if not ranked:
        return out, {"applied": False, "reason": "empty_ranked_pool"}

    available_rank = _path_rank_for_rows(ranked, src)
    if available_rank <= 0:
        return out, {"applied": False, "reason": "path_not_found"}

    def _normalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows[:top_n], start=1):
            normalized.append(
                {
                    **row,
                    "rank": idx,
                    "text_preview": str(row.get("text", ""))[:400],
                }
            )
        return normalized

    selected_idx = next((i for i, row in enumerate(out) if _path_matches(str(row.get("path") or ""), src)), -1)
    src_rows = [dict(row) for row in ranked if _path_matches(str(row.get("path") or ""), src)]
    src_text_rows = [row for row in src_rows if _is_text_evidence_hit(row)]

    q_tokens = set(_query_keywords(query, max_terms=16))
    q_phrases = [str(p or "").strip().lower() for p in _query_phrases(query, max_phrases=10) if str(p or "").strip()]
    q_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", str(query or "")))
    q_low = str(query or "").lower()
    path_tokens = set(re.findall(r"[a-z0-9][a-z0-9_.-]*", src.lower()))
    focus_tokens = {tok for tok in q_tokens if tok not in path_tokens}
    if not focus_tokens:
        focus_tokens = q_tokens
    money_requested = bool(re.search(r"\b(budget|cost|price|funding|amount)\b|\$", str(query or "").lower()))
    cause_requested = bool(re.search(r"\b(cause|caused|root cause|identified|device|why)\b", q_low))
    exact_time_requested = bool(re.search(r"\b(date|time|timestamp)\b", q_low))

    def _candidate_score(row: Dict[str, Any]) -> Tuple[int, int, int, int, int, int, float, int]:
        txt = str(row.get("text", "") or row.get("text_preview", "")).lower()
        overlap = 0
        if focus_tokens:
            for tok in focus_tokens:
                if tok and tok in txt:
                    overlap += 1
        ratio = float(overlap) / float(max(1, len(focus_tokens))) if focus_tokens else 0.0
        phrase_hits = sum(1 for phrase in q_phrases if phrase and phrase in txt)
        number_hits = 0
        if q_numbers:
            for num in q_numbers:
                if re.search(rf"\b{re.escape(num)}\b", txt):
                    number_hits += 1
        cause_hits = 0
        if cause_requested and re.search(
            r"\b(cause|caused|because|due to|identified|trace(?:d)?\s+(?:the\s+)?problem\s+to|root cause|disconnected it)\b",
            txt,
        ):
            cause_hits = 1
        time_hits = 0
        if exact_time_requested and re.search(
            r"\b(?:mon|tue|wed|thu|fri|sat|sun)\s+[a-z]{3}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+[a-z]{2,5}\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}:\d{2}",
            txt,
        ):
            time_hits = 1
        money_bonus = 0
        if money_requested and re.search(r"\$\s*\d|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b", txt):
            money_bonus = 1
        return (
            int(phrase_hits),
            int(cause_hits),
            int(time_hits),
            int(number_hits),
            int(overlap),
            int(money_bonus),
            float(ratio),
            -int(row.get("rank", 9999) or 9999),
        )

    chosen: Dict[str, Any] | None = None
    candidate_pool = src_text_rows if src_text_rows else src_rows
    if candidate_pool:
        candidate_pool = sorted(candidate_pool, key=_candidate_score, reverse=True)
        chosen = dict(candidate_pool[0])
    if chosen is None:
        return out, {"applied": False, "reason": "no_match_after_scan"}

    chosen_id = _hit_item_id(chosen)
    chosen_rank = next((i for i, row in enumerate(ranked, start=1) if _hit_item_id(row) == chosen_id), available_rank)
    selected_chosen_idx = next((i for i, row in enumerate(out) if _hit_item_id(row) == chosen_id), -1)

    if selected_chosen_idx >= 0:
        if selected_chosen_idx == 0:
            return out, {
                "applied": False,
                "reason": "already_has_best_source_hit",
                "available_rank": int(available_rank),
                "selected_rank": int(selected_chosen_idx + 1),
                "chosen_rank": int(chosen_rank),
            }
        moved = out.pop(selected_chosen_idx)
        out.insert(0, moved)
        return _normalize(out), {
            "applied": True,
            "reason": "promoted_best_source_hit",
            "available_rank": int(available_rank),
            "selected_rank_before": int(selected_chosen_idx + 1),
            "chosen_rank": int(chosen_rank),
            "inserted_item_id": _hit_item_id(moved),
            "replaced_item_id": "",
        }

    replaced_item_id = ""
    if selected_idx >= 0:
        replaced = out[selected_idx]
        replaced_item_id = _hit_item_id(replaced)
        out[selected_idx] = chosen
        chosen_idx = selected_idx
    else:
        replace_idx = -1
        for idx in range(len(out) - 1, -1, -1):
            row = out[idx]
            if _path_matches(str(row.get("path") or ""), src):
                continue
            if _is_taxonomy_hit(row) or _is_claim_hit(row) or _is_troubleshooting_tuple_hit(row) or not _is_text_evidence_hit(row):
                replace_idx = idx
                break
        if replace_idx < 0 and out:
            replace_idx = len(out) - 1
        if replace_idx < 0:
            return out, {"applied": False, "reason": "no_replacement_slot"}
        replaced = out[replace_idx]
        replaced_item_id = _hit_item_id(replaced)
        out[replace_idx] = chosen
        chosen_idx = replace_idx

    if chosen_idx > 0:
        moved = out.pop(chosen_idx)
        out.insert(0, moved)

    normalized = _normalize(out)
    return normalized, {
        "applied": True,
        "reason": "forced_source_path_best_overlap_hit",
        "available_rank": int(available_rank),
        "selected_rank_before": int(selected_idx + 1 if selected_idx >= 0 else 0),
        "chosen_rank": int(chosen_rank),
        "inserted_item_id": _hit_item_id(chosen),
        "replaced_item_id": replaced_item_id,
    }


def embed_query(text: str) -> List[float]:
    global _EMBED_COMPAT_OMIT_TASK, _EMBED_COMPAT_OMIT_DIMENSIONS

    embed_text = str(text or "").strip()
    if EMBED_QUERY_INSTRUCTION:
        embed_text = f"Instruct: {EMBED_QUERY_INSTRUCTION}\nQuery: {embed_text}"

    include_task_default = not _EMBED_COMPAT_OMIT_TASK
    include_dim_default = int(EMBED_DIM) > 0 and not _EMBED_COMPAT_OMIT_DIMENSIONS

    variant_order: List[Tuple[bool, bool]] = [
        (False, False),
        (False, include_dim_default),
        (include_task_default, False),
        (include_task_default, include_dim_default),
    ]

    seen: set[Tuple[bool, bool]] = set()
    errors: List[str] = []
    for include_task, include_dim in variant_order:
        key = (include_task, include_dim)
        if key in seen:
            continue
        seen.add(key)

        payload: Dict[str, Any] = {
            "model": EMBED_MODEL,
            "input": [embed_text],
            "encoding_format": "float",
        }
        if include_task:
            payload["task"] = "query"
        if include_dim:
            payload["dimensions"] = int(EMBED_DIM)

        resp = _http_request("POST", EMBED_URL, json=payload, timeout=int(EMBED_TIMEOUT_SEC))
        if resp.status_code >= 300:
            errors.append(f"{resp.status_code}: {resp.text[:220]}")
            continue

        obj = resp.json()
        data = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(data, list) or not data:
            raise RuntimeError("Embedding response missing data")
        emb = data[0].get("embedding")
        if not isinstance(emb, list):
            raise RuntimeError("Embedding response missing vector")

        if include_task_default and not include_task and not _EMBED_COMPAT_OMIT_TASK:
            LOGGER.warning("Embedding compatibility mode enabled: omitting `task`")
            _EMBED_COMPAT_OMIT_TASK = True
        if include_dim_default and not include_dim and not _EMBED_COMPAT_OMIT_DIMENSIONS:
            LOGGER.warning("Embedding compatibility mode enabled: omitting `dimensions`")
            _EMBED_COMPAT_OMIT_DIMENSIONS = True
        return [float(v) for v in emb]

    raise RuntimeError(f"Embedding error after compatibility retries: {'; '.join(errors[-4:])}")


def _es_chunk_row(hit: Dict[str, Any], *, rank: int, source: str) -> Dict[str, Any]:
    src = hit.get("_source", {})
    return {
        "source": source,
        "rank": rank,
        "score": float(hit.get("_score") or 0.0),
        "chunk_id": src.get("chunk_id"),
        "doc_id": src.get("doc_id"),
        "path": src.get("path"),
        "text": src.get("text", ""),
        "semantic_domain": src.get("semantic_domain"),
        "first_bucket": src.get("first_bucket"),
        "sub_bucket": src.get("sub_bucket"),
        "artifact_type": src.get("artifact_type"),
        "doc_type": src.get("doc_type"),
        "sensitivity_level": src.get("sensitivity_level"),
        "mtime_date": src.get("mtime_date", ""),
        "effective_date": src.get("effective_date", ""),
        "mtime_year": src.get("mtime_year", -1),
        "effective_year": src.get("effective_year", -1),
        "mtime_epoch_day": src.get("mtime_epoch_day", -1),
        "effective_epoch_day": src.get("effective_epoch_day", -1),
    }


def _query_keywords(query: str, max_terms: int = 12) -> List[str]:
    stop = {
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "into",
        "what",
        "where",
        "when",
        "which",
        "about",
        "using",
        "these",
        "those",
        "across",
        "areas",
        "area",
        "documents",
        "document",
        "include",
        "including",
        "explain",
        "build",
        "based",
    }
    out: List[str] = []
    seen: Set[str] = set()
    for tok in re.findall(r"[a-z0-9][a-z0-9_/.-]*", str(query or "").lower()):
        if len(tok) <= 2 or tok in stop or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= int(max_terms):
            break
    return out


def _query_phrases(query: str, max_phrases: int = 10) -> List[str]:
    raw = str(query or "").strip()
    if not raw:
        return []
    out: List[str] = []
    seen: Set[str] = set()

    quoted = re.findall(r'"([^"]{3,120})"', raw)
    candidates = [q.strip() for q in quoted if q and q.strip()]

    proper = re.findall(r"\b[A-Z][A-Za-z0-9_.-]{2,}(?:\s+[A-Z][A-Za-z0-9_.-]{2,}){0,2}\b", raw)
    candidates.extend([p.strip() for p in proper if p and p.strip()])

    for cand in candidates:
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
        if len(out) >= int(max_phrases):
            return out

    # Fallback phrases from query keywords when no explicit phrase exists.
    kws = _query_keywords(raw, max_terms=8)
    if len(kws) >= 2:
        phrase = " ".join(kws[:2])
        if phrase.lower() not in seen:
            out.append(phrase)

    # Add compact lexical n-grams so source-path rescue can prefer exact
    # operational snippets (especially noisy logbook chunks).
    toks = [k for k in kws if k]
    for n in (4, 3, 2):
        if len(toks) < n:
            continue
        for i in range(0, len(toks) - n + 1):
            phrase = " ".join(toks[i : i + n]).strip()
            key = phrase.lower()
            if not phrase or len(phrase) < 6 or key in seen:
                continue
            seen.add(key)
            out.append(phrase)
            if len(out) >= int(max_phrases):
                return out[: int(max_phrases)]
    return out[: int(max_phrases)]


def es_search(
    es: Elasticsearch,
    query: str,
    domain: str,
    top_k: int,
    *,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    must: List[Any] = [{"match": {"text": {"query": query}}}]
    filt: List[Any] = []
    _append_es_chunk_routing_filter(filt)
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)

    body = {"size": int(top_k), "query": {"bool": {"must": must, "filter": filt}}}
    out = es.search(index=ES_CHUNK_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for rank, h in enumerate(hits, start=1):
        rows.append(_es_chunk_row(h, rank=rank, source="elasticsearch"))
    return rows


def es_taxonomy_hint_search(
    es: Elasticsearch,
    query: str,
    domain: str,
    top_k: int,
    *,
    field: str,
    values: List[str],
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    hint_values = [str(v).strip() for v in values if str(v).strip()]
    if not hint_values:
        return []

    filt: List[Any] = [{"terms": {field: hint_values}}]
    _append_es_chunk_routing_filter(filt)
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)
    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": query}}}],
                "filter": filt,
            }
        },
    }
    out = es.search(index=ES_CHUNK_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for rank, h in enumerate(hits, start=1):
        rows.append(_es_chunk_row(h, rank=rank, source=f"es_hint_{field}"))
    return rows


def es_text_rescue_search(
    es: Elasticsearch,
    query: str,
    domain: str,
    top_k: int,
    *,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    terms = _query_keywords(query, max_terms=14)
    should: List[Any] = [
        {"match": {"text": {"query": query}}},
    ]
    if terms:
        should.append(
            {
                "simple_query_string": {
                    "query": " ".join(terms),
                    "fields": ["text"],
                    "default_operator": "or",
                }
            }
        )
    filt: List[Any] = []
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)
    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "should": should,
                "minimum_should_match": 1,
                "filter": filt,
            }
        },
    }
    out = es.search(index=ES_CHUNK_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for rank, h in enumerate(hits, start=1):
        rows.append(_es_chunk_row(h, rank=rank, source="es_text_rescue"))
    return rows


def es_exact_phrase_rescue_search(
    es: Elasticsearch,
    query: str,
    domain: str,
    top_k: int,
    *,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    phrases = _query_phrases(query, max_phrases=10)
    should: List[Any] = [
        {"match": {"text": {"query": query}}},
    ]
    for phrase in phrases:
        should.append({"match_phrase": {"text": {"query": phrase, "boost": 4.0}}})

    if phrases:
        quoted_join = " | ".join(f'"{p}"' for p in phrases)
        should.append(
            {
                "simple_query_string": {
                    "query": quoted_join,
                    "fields": ["text"],
                    "default_operator": "or",
                    "boost": 2.5,
                }
            }
        )

    filt: List[Any] = []
    _append_es_chunk_routing_filter(filt)
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)
    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "should": should,
                "minimum_should_match": 1,
                "filter": filt,
            }
        },
    }
    out = es.search(index=ES_CHUNK_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for rank, h in enumerate(hits, start=1):
        rows.append(_es_chunk_row(h, rank=rank, source="es_phrase_rescue"))
    return rows


def es_source_path_rescue_search(
    es: Elasticsearch,
    query: str,
    source_path_hint: str,
    domain: str,
    top_k: int,
    *,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    src = str(source_path_hint or "").strip()
    if not src:
        return []
    src_name = Path(src).name
    query_terms = _query_keywords(query, max_terms=14)
    path_should: List[Any] = [
        {"match_phrase": {"path": {"query": src, "boost": 12.0}}},
    ]
    text_should: List[Any] = [
        {"match": {"text": {"query": query, "boost": 3.0}}},
    ]
    if src_name:
        path_should.append({"match_phrase": {"path": {"query": src_name, "boost": 9.0}}})
        path_should.append(
            {
                "simple_query_string": {
                    "query": f'"{src_name}"',
                    "fields": ["path^7"],
                    "default_operator": "or",
                    "boost": 6.0,
                }
            }
        )
    if query_terms:
        text_should.append(
            {
                "simple_query_string": {
                    "query": " ".join(query_terms),
                    "fields": ["text"],
                    "default_operator": "or",
                    "boost": 2.0,
                }
            }
        )
    # Source-path rescue is precision-targeted by exact path and should not
    # exclude abstained documents for the hinted file.
    filt: List[Any] = []
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)
    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "must": [
                    {"bool": {"should": path_should, "minimum_should_match": 1}},
                    {"bool": {"should": text_should, "minimum_should_match": 1}},
                ],
                "filter": filt,
            }
        },
    }
    out = es.search(index=ES_CHUNK_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rank, h in enumerate(hits, start=1):
        row = _es_chunk_row(h, rank=rank, source="es_source_path_rescue")
        rows.append(row)
        item_id = _hit_item_id(row)
        if item_id:
            seen_ids.add(item_id)

    # Always append a path-only scan for hinted files so log-heavy/low-lexical
    # chunks remain available for downstream source-aware evidence selection.
    scan_size = max(int(top_k), 40)
    scan_body = {
        "size": int(scan_size),
        "sort": [{"chunk_index": {"order": "asc"}}],
        "query": {"bool": {"filter": [{"term": {"path": src}}]}},
    }
    if temporal_filter is not None:
        scan_body["query"]["bool"]["filter"].append(temporal_filter)
    scan_out = es.search(index=ES_CHUNK_INDEX, body=scan_body)
    scan_hits = scan_out.get("hits", {}).get("hits", [])
    next_rank = len(rows) + 1
    for h in scan_hits:
        row = _es_chunk_row(h, rank=next_rank, source="es_source_path_scan")
        item_id = _hit_item_id(row)
        if item_id and item_id in seen_ids:
            continue
        rows.append(row)
        if item_id:
            seen_ids.add(item_id)
        next_rank += 1
    return rows


def _normalize_artifact_types(values: List[str]) -> List[str]:
    out: List[str] = []
    for item in values:
        txt = str(item or "").strip().lower()
        if not txt:
            continue
        if txt not in _GRAPHRAG_ARTIFACT_TYPES:
            LOGGER.warning("Ignoring unsupported graphrag artifact type filter: %s", txt)
            continue
        if txt not in out:
            out.append(txt)
    return out


def _apply_es_graphrag_generation_filters(
    filt: List[Any],
    *,
    use_llm: bool,
    use_deterministic: bool,
    artifact_types: List[str],
) -> bool:
    if not bool(use_llm or use_deterministic):
        return False
    if bool(use_llm) and not bool(use_deterministic):
        filt.append({"term": {"artifact_generation": "llm"}})
    elif bool(use_deterministic) and not bool(use_llm):
        filt.append({"bool": {"must_not": [{"term": {"artifact_generation": "llm"}}]}})
    if artifact_types:
        filt.append({"terms": {"artifact_type": artifact_types}})
    return True


def es_graphrag_search(
    es: Elasticsearch,
    query: str,
    domain: str,
    mode: str,
    top_k: int,
    *,
    use_llm: bool,
    use_deterministic: bool,
    artifact_types: List[str],
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    filt: List[Any] = []
    mode_s = str(mode or "basic").strip().lower()
    if mode_s in {"local", "global"}:
        filt.append({"term": {"retrieval_mode": mode_s}})
    elif mode_s == "drift":
        filt.append({"terms": {"retrieval_mode": ["local", "global"]}})
    else:
        # basic mode intentionally does not use GraphRAG artifacts.
        return []
    if not _apply_es_graphrag_generation_filters(
        filt,
        use_llm=bool(use_llm),
        use_deterministic=bool(use_deterministic),
        artifact_types=_normalize_artifact_types(artifact_types),
    ):
        return []
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)

    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": query}}}],
                "filter": filt,
            }
        },
    }
    out = es.search(index=ES_GRAPHRAG_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for rank, h in enumerate(hits, start=1):
        src = h.get("_source", {})
        rows.append(
            {
                "source": "es_graphrag",
                "rank": rank,
                "score": float(h.get("_score") or 0.0),
                "artifact_id": src.get("artifact_id"),
                "artifact_type": src.get("artifact_type"),
                "artifact_generation": src.get("artifact_generation"),
                "retrieval_mode": src.get("retrieval_mode"),
                "chunk_id": src.get("chunk_id"),
                "doc_id": src.get("doc_id"),
                "path": src.get("path"),
                "text": src.get("text", ""),
                "semantic_domain": src.get("semantic_domain"),
                "first_bucket": src.get("first_bucket"),
                "sub_bucket": src.get("sub_bucket"),
                "mtime_date": src.get("mtime_date", ""),
                "effective_date": src.get("effective_date", ""),
                "mtime_year": src.get("mtime_year", -1),
                "effective_year": src.get("effective_year", -1),
                "mtime_epoch_day": src.get("mtime_epoch_day", -1),
                "effective_epoch_day": src.get("effective_epoch_day", -1),
            }
        )
    return rows


def es_graphrag_hint_search(
    es: Elasticsearch,
    query: str,
    domain: str,
    mode: str,
    top_k: int,
    *,
    field: str,
    values: List[str],
    use_llm: bool,
    use_deterministic: bool,
    artifact_types: List[str],
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    hint_values = [str(v).strip() for v in values if str(v).strip()]
    if not hint_values:
        return []
    filt: List[Any] = []
    mode_s = str(mode or "basic").strip().lower()
    if mode_s in {"local", "global"}:
        filt.append({"term": {"retrieval_mode": mode_s}})
    elif mode_s == "drift":
        filt.append({"terms": {"retrieval_mode": ["local", "global"]}})
    else:
        return []
    if not _apply_es_graphrag_generation_filters(
        filt,
        use_llm=bool(use_llm),
        use_deterministic=bool(use_deterministic),
        artifact_types=_normalize_artifact_types(artifact_types),
    ):
        return []
    if domain:
        filt.append({"term": {"semantic_domain": domain}})
    filt.append({"terms": {field: hint_values}})
    temporal_filter = _build_es_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        filt.append(temporal_filter)

    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": query}}}],
                "filter": filt,
            }
        },
    }
    out = es.search(index=ES_GRAPHRAG_INDEX, body=body)
    hits = out.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for rank, h in enumerate(hits, start=1):
        src = h.get("_source", {})
        rows.append(
            {
                "source": f"es_graphrag_hint_{field}",
                "rank": rank,
                "score": float(h.get("_score") or 0.0),
                "artifact_id": src.get("artifact_id"),
                "artifact_type": src.get("artifact_type"),
                "artifact_generation": src.get("artifact_generation"),
                "retrieval_mode": src.get("retrieval_mode"),
                "chunk_id": src.get("chunk_id"),
                "doc_id": src.get("doc_id"),
                "path": src.get("path"),
                "text": src.get("text", ""),
                "semantic_domain": src.get("semantic_domain"),
                "first_bucket": src.get("first_bucket"),
                "sub_bucket": src.get("sub_bucket"),
                "mtime_date": src.get("mtime_date", ""),
                "effective_date": src.get("effective_date", ""),
                "mtime_year": src.get("mtime_year", -1),
                "effective_year": src.get("effective_year", -1),
                "mtime_epoch_day": src.get("mtime_epoch_day", -1),
                "effective_epoch_day": src.get("effective_epoch_day", -1),
            }
        )
    return rows


def qdrant_search(
    client: QdrantClient,
    vector: List[float],
    domain: str,
    top_k: int,
    *,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    must: List[qm.Condition] = []
    _append_qdrant_chunk_routing_filter(must)
    if domain:
        must.append(qm.FieldCondition(key="semantic_domain", match=qm.MatchValue(value=domain)))
    temporal_filter = _build_qdrant_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        must.append(temporal_filter)
    filt = qm.Filter(must=must)

    points: Any
    if hasattr(client, "query_points"):
        out = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )
        points = out.points if hasattr(out, "points") else out
    else:
        points = client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )

    rows: List[Dict[str, Any]] = []
    for rank, p in enumerate(points, start=1):
        payload = p.payload or {}
        rows.append(
            {
                "source": "qdrant",
                "rank": rank,
                "score": float(p.score or 0.0),
                "chunk_id": payload.get("chunk_id"),
                "doc_id": payload.get("doc_id"),
                "path": payload.get("path"),
                "text": payload.get("text", ""),
                "semantic_domain": payload.get("semantic_domain"),
                "first_bucket": payload.get("first_bucket"),
                "sub_bucket": payload.get("sub_bucket"),
                "artifact_type": payload.get("artifact_type"),
                "doc_type": payload.get("doc_type"),
                "sensitivity_level": payload.get("sensitivity_level"),
                "mtime_date": payload.get("mtime_date", ""),
                "effective_date": payload.get("effective_date", ""),
                "mtime_year": payload.get("mtime_year", -1),
                "effective_year": payload.get("effective_year", -1),
                "mtime_epoch_day": payload.get("mtime_epoch_day", -1),
                "effective_epoch_day": payload.get("effective_epoch_day", -1),
            }
        )
    return rows


def qdrant_taxonomy_hint_search(
    client: QdrantClient,
    vector: List[float],
    domain: str,
    top_k: int,
    *,
    field: str,
    values: List[str],
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    hint_values = [str(v).strip() for v in values if str(v).strip()]
    if not hint_values:
        return []

    must: List[qm.Condition] = [qm.FieldCondition(key=field, match=qm.MatchAny(any=hint_values))]
    _append_qdrant_chunk_routing_filter(must)
    if domain:
        must.append(qm.FieldCondition(key="semantic_domain", match=qm.MatchValue(value=domain)))
    temporal_filter = _build_qdrant_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        must.append(temporal_filter)
    filt = qm.Filter(must=must)

    points: Any
    if hasattr(client, "query_points"):
        out = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )
        points = out.points if hasattr(out, "points") else out
    else:
        points = client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )

    rows: List[Dict[str, Any]] = []
    for rank, p in enumerate(points, start=1):
        payload = p.payload or {}
        rows.append(
            {
                "source": f"qdrant_hint_{field}",
                "rank": rank,
                "score": float(p.score or 0.0),
                "chunk_id": payload.get("chunk_id"),
                "doc_id": payload.get("doc_id"),
                "path": payload.get("path"),
                "text": payload.get("text", ""),
                "semantic_domain": payload.get("semantic_domain"),
                "first_bucket": payload.get("first_bucket"),
                "sub_bucket": payload.get("sub_bucket"),
                "artifact_type": payload.get("artifact_type"),
                "doc_type": payload.get("doc_type"),
                "sensitivity_level": payload.get("sensitivity_level"),
                "mtime_date": payload.get("mtime_date", ""),
                "effective_date": payload.get("effective_date", ""),
                "mtime_year": payload.get("mtime_year", -1),
                "effective_year": payload.get("effective_year", -1),
                "mtime_epoch_day": payload.get("mtime_epoch_day", -1),
                "effective_epoch_day": payload.get("effective_epoch_day", -1),
            }
        )
    return rows


def qdrant_source_path_rescue_search(
    client: QdrantClient,
    vector: List[float],
    source_path_hint: str,
    domain: str,
    top_k: int,
    *,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    src = str(source_path_hint or "").strip()
    if not src:
        return []
    must: List[qm.Condition] = [
        qm.FieldCondition(key="path", match=qm.MatchValue(value=src)),
    ]
    if domain:
        must.append(qm.FieldCondition(key="semantic_domain", match=qm.MatchValue(value=domain)))
    temporal_filter = _build_qdrant_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        must.append(temporal_filter)
    filt = qm.Filter(must=must)

    points: Any
    if hasattr(client, "query_points"):
        out = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )
        points = out.points if hasattr(out, "points") else out
    else:
        points = client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )

    rows: List[Dict[str, Any]] = []
    for rank, p in enumerate(points, start=1):
        payload = p.payload or {}
        rows.append(
            {
                "source": "qdrant_source_path_rescue",
                "rank": rank,
                "score": float(p.score or 0.0),
                "chunk_id": payload.get("chunk_id"),
                "doc_id": payload.get("doc_id"),
                "path": payload.get("path"),
                "text": payload.get("text", ""),
                "semantic_domain": payload.get("semantic_domain"),
                "first_bucket": payload.get("first_bucket"),
                "sub_bucket": payload.get("sub_bucket"),
                "artifact_type": payload.get("artifact_type"),
                "doc_type": payload.get("doc_type"),
                "sensitivity_level": payload.get("sensitivity_level"),
                "mtime_date": payload.get("mtime_date", ""),
                "effective_date": payload.get("effective_date", ""),
                "mtime_year": payload.get("mtime_year", -1),
                "effective_year": payload.get("effective_year", -1),
                "mtime_epoch_day": payload.get("mtime_epoch_day", -1),
                "effective_epoch_day": payload.get("effective_epoch_day", -1),
            }
        )
    return rows


def _apply_qdrant_graphrag_generation_filters(
    must: List[qm.Condition],
    must_not: List[qm.Condition],
    *,
    use_llm: bool,
    use_deterministic: bool,
    artifact_types: List[str],
) -> bool:
    if not bool(use_llm or use_deterministic):
        return False
    if bool(use_llm) and not bool(use_deterministic):
        must.append(qm.FieldCondition(key="artifact_generation", match=qm.MatchValue(value="llm")))
    elif bool(use_deterministic) and not bool(use_llm):
        must_not.append(qm.FieldCondition(key="artifact_generation", match=qm.MatchValue(value="llm")))
    types = _normalize_artifact_types(artifact_types)
    if types:
        must.append(qm.FieldCondition(key="artifact_type", match=qm.MatchAny(any=types)))
    return True


def qdrant_graphrag_search(
    client: QdrantClient,
    vector: List[float],
    domain: str,
    mode: str,
    top_k: int,
    *,
    use_llm: bool,
    use_deterministic: bool,
    artifact_types: List[str],
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    mode_s = str(mode or "basic").strip().lower()
    must: List[qm.Condition] = []
    must_not: List[qm.Condition] = []
    if mode_s in {"local", "global"}:
        must.append(qm.FieldCondition(key="retrieval_mode", match=qm.MatchValue(value=mode_s)))
    elif mode_s == "drift":
        must.append(qm.FieldCondition(key="retrieval_mode", match=qm.MatchAny(any=["local", "global"])))
    else:
        return []
    if not _apply_qdrant_graphrag_generation_filters(
        must,
        must_not,
        use_llm=bool(use_llm),
        use_deterministic=bool(use_deterministic),
        artifact_types=artifact_types,
    ):
        return []
    if domain:
        must.append(qm.FieldCondition(key="semantic_domain", match=qm.MatchValue(value=domain)))
    temporal_filter = _build_qdrant_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        must.append(temporal_filter)

    filt = qm.Filter(must=must, must_not=must_not)

    points: Any
    if hasattr(client, "query_points"):
        out = client.query_points(
            collection_name=QDRANT_GRAPHRAG_COLLECTION,
            query=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )
        points = out.points if hasattr(out, "points") else out
    else:
        points = client.search(
            collection_name=QDRANT_GRAPHRAG_COLLECTION,
            query_vector=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )

    rows: List[Dict[str, Any]] = []
    for rank, p in enumerate(points, start=1):
        payload = p.payload or {}
        rows.append(
            {
                "source": "qdrant_graphrag",
                "rank": rank,
                "score": float(p.score or 0.0),
                "artifact_id": payload.get("artifact_id"),
                "artifact_type": payload.get("artifact_type"),
                "artifact_generation": payload.get("artifact_generation"),
                "retrieval_mode": payload.get("retrieval_mode"),
                "chunk_id": payload.get("chunk_id"),
                "doc_id": payload.get("doc_id"),
                "path": payload.get("path"),
                "text": payload.get("text", ""),
                "semantic_domain": payload.get("semantic_domain"),
                "first_bucket": payload.get("first_bucket"),
                "sub_bucket": payload.get("sub_bucket"),
                "mtime_date": payload.get("mtime_date", ""),
                "effective_date": payload.get("effective_date", ""),
                "mtime_year": payload.get("mtime_year", -1),
                "effective_year": payload.get("effective_year", -1),
                "mtime_epoch_day": payload.get("mtime_epoch_day", -1),
                "effective_epoch_day": payload.get("effective_epoch_day", -1),
            }
        )
    return rows


def qdrant_graphrag_hint_search(
    client: QdrantClient,
    vector: List[float],
    domain: str,
    mode: str,
    top_k: int,
    *,
    field: str,
    values: List[str],
    use_llm: bool,
    use_deterministic: bool,
    artifact_types: List[str],
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
) -> List[Dict[str, Any]]:
    hint_values = [str(v).strip() for v in values if str(v).strip()]
    if not hint_values:
        return []

    mode_s = str(mode or "basic").strip().lower()
    must: List[qm.Condition] = []
    must_not: List[qm.Condition] = []
    if mode_s in {"local", "global"}:
        must.append(qm.FieldCondition(key="retrieval_mode", match=qm.MatchValue(value=mode_s)))
    elif mode_s == "drift":
        must.append(qm.FieldCondition(key="retrieval_mode", match=qm.MatchAny(any=["local", "global"])))
    else:
        return []
    if not _apply_qdrant_graphrag_generation_filters(
        must,
        must_not,
        use_llm=bool(use_llm),
        use_deterministic=bool(use_deterministic),
        artifact_types=artifact_types,
    ):
        return []

    must.append(qm.FieldCondition(key=field, match=qm.MatchAny(any=hint_values)))
    if domain:
        must.append(qm.FieldCondition(key="semantic_domain", match=qm.MatchValue(value=domain)))
    temporal_filter = _build_qdrant_temporal_filter(temporal_basis=temporal_basis, date_from=date_from, date_to=date_to)
    if temporal_filter is not None:
        must.append(temporal_filter)

    filt = qm.Filter(must=must, must_not=must_not)
    points: Any
    if hasattr(client, "query_points"):
        out = client.query_points(
            collection_name=QDRANT_GRAPHRAG_COLLECTION,
            query=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )
        points = out.points if hasattr(out, "points") else out
    else:
        points = client.search(
            collection_name=QDRANT_GRAPHRAG_COLLECTION,
            query_vector=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )

    rows: List[Dict[str, Any]] = []
    for rank, p in enumerate(points, start=1):
        payload = p.payload or {}
        rows.append(
            {
                "source": f"qdrant_graphrag_hint_{field}",
                "rank": rank,
                "score": float(p.score or 0.0),
                "artifact_id": payload.get("artifact_id"),
                "artifact_type": payload.get("artifact_type"),
                "artifact_generation": payload.get("artifact_generation"),
                "retrieval_mode": payload.get("retrieval_mode"),
                "chunk_id": payload.get("chunk_id"),
                "doc_id": payload.get("doc_id"),
                "path": payload.get("path"),
                "text": payload.get("text", ""),
                "semantic_domain": payload.get("semantic_domain"),
                "first_bucket": payload.get("first_bucket"),
                "sub_bucket": payload.get("sub_bucket"),
                "mtime_date": payload.get("mtime_date", ""),
                "effective_date": payload.get("effective_date", ""),
                "mtime_year": payload.get("mtime_year", -1),
                "effective_year": payload.get("effective_year", -1),
                "mtime_epoch_day": payload.get("mtime_epoch_day", -1),
                "effective_epoch_day": payload.get("effective_epoch_day", -1),
            }
        )
    return rows


def fuse_hits(
    es_hits: List[Dict[str, Any]],
    qdr_hits: List[Dict[str, Any]],
    top_k: int,
    *,
    extra_sources: List[Tuple[str, List[Dict[str, Any]]]] | None = None,
) -> List[Dict[str, Any]]:
    fused: Dict[str, Dict[str, Any]] = {}

    def add(rows: List[Dict[str, Any]], source: str) -> None:
        for row in rows:
            item_id = str(row.get("artifact_id") or row.get("chunk_id") or "")
            if not item_id:
                continue
            r = int(row.get("rank") or 9999)
            rrf = 1.0 / float(FUSION_K + r)
            cur = fused.get(item_id)
            if cur is None:
                item = dict(row)
                item["fusion_score"] = rrf
                item["sources"] = [source]
                fused[item_id] = item
            else:
                cur["fusion_score"] = float(cur.get("fusion_score", 0.0)) + rrf
                sources = set(cur.get("sources", []))
                sources.add(source)
                cur["sources"] = sorted(sources)
                # Prefer richer text payload if one source omitted it.
                if not str(cur.get("text", "")).strip() and str(row.get("text", "")).strip():
                    cur["text"] = row.get("text", "")

    add(es_hits, "elasticsearch")
    add(qdr_hits, "qdrant")
    if extra_sources:
        for source_name, rows in extra_sources:
            add(rows, source_name)

    ranked = sorted(fused.values(), key=lambda x: float(x.get("fusion_score", 0.0)), reverse=True)
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(ranked[: int(top_k)], start=1):
        out.append(
            {
                **row,
                "rank": idx,
                "text_preview": str(row.get("text", ""))[:400],
            }
        )
    return out


def call_rerank_api(query: str, documents: List[str], top_n: int) -> List[Tuple[int, float]]:
    payload: Dict[str, Any] = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": documents,
        "top_n": min(len(documents), max(1, int(top_n))),
    }
    resp = _http_request("POST", RERANK_URL, json=payload, timeout=int(RERANK_TIMEOUT_SEC))
    if resp.status_code >= 300:
        raise RuntimeError(f"Rerank error {resp.status_code}: {resp.text[:320]}")

    obj = resp.json()
    if not isinstance(obj, dict):
        raise RuntimeError("Rerank response is not a JSON object")

    results = obj.get("results")
    if not isinstance(results, list):
        results = obj.get("data")
    if not isinstance(results, list):
        raise RuntimeError("Rerank response missing results array")

    out: List[Tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int):
            continue
        score_raw = item.get("relevance_score", item.get("score", 0.0))
        try:
            score = float(score_raw or 0.0)
        except Exception:
            score = 0.0
        out.append((idx, score))
    return out


def _rerank_document_text(hit: Dict[str, Any]) -> str:
    path = str(hit.get("path", "") or "").strip()
    title = Path(path).name if path else ""
    domain = str(hit.get("semantic_domain", "") or "").strip()
    first_bucket = str(hit.get("first_bucket", "") or "").strip()
    sub_bucket = str(hit.get("sub_bucket", "") or "").strip()
    artifact_type = str(hit.get("artifact_type", "") or "chunk").strip()
    text = str(hit.get("text", "") or hit.get("text_preview", "")).strip()
    text = text[:1400]

    header = [
        f"title={title}" if title else "",
        f"path={path}" if path else "",
        f"domain={domain}" if domain else "",
        f"bucket={first_bucket}/{sub_bucket}" if first_bucket or sub_bucket else "",
        f"artifact_type={artifact_type}",
    ]
    header_txt = " | ".join([h for h in header if h])
    if header_txt:
        return f"{header_txt}\n{text}"
    return text


def rerank_hits(query: str, hits: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    if not hits:
        return []

    documents: List[str] = []
    for hit in hits:
        txt = _rerank_document_text(hit)
        documents.append(txt)

    rerank_query = str(query or "").strip()
    if RERANK_INSTRUCTION:
        rerank_query = f"<Instruct>: {RERANK_INSTRUCTION}\n<Query>: {rerank_query}"

    top_n = int(RERANK_TOP_N) if int(RERANK_TOP_N) > 0 else int(top_k)
    top_n = min(len(hits), max(1, top_n))

    results: List[Tuple[int, float]]
    strategy = RERANK_STRATEGY if RERANK_STRATEGY in {"single", "batch", "auto"} else "single"
    if strategy == "batch":
        results = call_rerank_api(rerank_query, documents, top_n)
    elif strategy == "auto":
        try:
            results = call_rerank_api(rerank_query, documents, top_n)
        except Exception as exc:
            LOGGER.warning("Batch rerank failed; falling back to single-doc scoring: %s", exc)
            strategy = "single"
            results = []
    else:
        results = []

    if strategy == "single":
        scored: List[Tuple[int, float]] = []
        for idx, doc in enumerate(documents):
            txt = str(doc or "").strip()
            if not txt:
                continue
            try:
                one = call_rerank_api(rerank_query, [txt], 1)
                score = float(one[0][1]) if one else 0.0
            except Exception:
                score = 0.0
            scored.append((idx, score))
        results = sorted(scored, key=lambda row: row[1], reverse=True)

    reranked: List[Dict[str, Any]] = []
    reranked_ids: set[str] = set()
    seen_indices: set[int] = set()
    for idx, score in results:
        if idx < 0 or idx >= len(hits) or idx in seen_indices:
            continue
        seen_indices.add(idx)
        row = dict(hits[idx])
        row["rerank_score"] = float(score)
        reranked.append(row)
        item_id = str(row.get("artifact_id") or row.get("chunk_id") or "")
        if item_id:
            reranked_ids.add(item_id)

    if not reranked:
        raise RuntimeError("Rerank results were empty or invalid")

    # Preserve fallback recall if top_n is smaller than requested.
    if len(reranked) < int(top_k):
        for row in hits:
            item_id = str(row.get("artifact_id") or row.get("chunk_id") or "")
            if not item_id:
                continue
            if item_id in reranked_ids:
                continue
            reranked.append(dict(row))
            reranked_ids.add(item_id)
            if len(reranked) >= int(top_k):
                break

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(reranked[: int(top_k)], start=1):
        out.append(
            {
                **row,
                "rank": idx,
                "text_preview": str(row.get("text", ""))[:400],
            }
        )
    return out


def graph_summary(driver: Any, domain: str, top_entities: int = 5) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    with driver.session() as session:
        if domain:
            rec = session.run(
                "MATCH (d:Document)-[:IN_DOMAIN]->(dom:Domain {id:$domain}) RETURN count(d) AS n",
                domain=domain,
            ).single()
            out["documents_in_domain"] = int(rec["n"]) if rec else 0

        top_domains = session.run(
            """
            MATCH (d:Document)-[:IN_DOMAIN]->(dom:Domain)
            RETURN dom.id AS domain, count(d) AS n
            ORDER BY n DESC
            LIMIT 5
            """
        )
        out["top_domains"] = [{"domain": r["domain"], "count": int(r["n"])} for r in top_domains]

        ents_query = (
            """
            MATCH (d:Document)-[:HAS_ENTITY]->(e)
            RETURN labels(e)[0] AS label, e.name AS name, count(*) AS n
            ORDER BY n DESC
            LIMIT $limit
            """
            if not domain
            else """
            MATCH (d:Document)-[:IN_DOMAIN]->(:Domain {id:$domain})
            MATCH (d)-[:HAS_ENTITY]->(e)
            RETURN labels(e)[0] AS label, e.name AS name, count(*) AS n
            ORDER BY n DESC
            LIMIT $limit
            """
        )
        ents = session.run(ents_query, limit=int(top_entities), domain=domain) if domain else session.run(ents_query, limit=int(top_entities))
        out["top_entities"] = [{"label": r["label"], "name": r["name"], "count": int(r["n"])} for r in ents]

        community_query = (
            """
            MATCH (d:Document)-[:IN_COMMUNITY]->(c:Community)
            RETURN c.community_id AS community_id,
                   coalesce(c.scope, '') AS scope,
                   coalesce(c.method, '') AS method,
                   count(DISTINCT d) AS n
            ORDER BY n DESC
            LIMIT 8
            """
            if not domain
            else """
            MATCH (d:Document)-[:IN_DOMAIN]->(:Domain {id:$domain})
            MATCH (d)-[:IN_COMMUNITY]->(c:Community)
            RETURN c.community_id AS community_id,
                   coalesce(c.scope, '') AS scope,
                   coalesce(c.method, '') AS method,
                   count(DISTINCT d) AS n
            ORDER BY n DESC
            LIMIT 8
            """
        )
        communities = session.run(community_query, domain=domain) if domain else session.run(community_query)
        out["top_communities"] = [
            {
                "community_id": r["community_id"],
                "scope": r["scope"],
                "method": r["method"],
                "count": int(r["n"]),
            }
            for r in communities
        ]

        first_query = (
            """
            MATCH (d:Document)-[:IN_TAXONOMY_FIRST_BUCKET]->(f:TaxonomyFirstBucket)
            RETURN f.id AS first_bucket, count(DISTINCT d) AS n
            ORDER BY n DESC
            LIMIT 8
            """
            if not domain
            else """
            MATCH (d:Document)-[:IN_DOMAIN]->(:Domain {id:$domain})
            MATCH (d)-[:IN_TAXONOMY_FIRST_BUCKET]->(f:TaxonomyFirstBucket)
            RETURN f.id AS first_bucket, count(DISTINCT d) AS n
            ORDER BY n DESC
            LIMIT 8
            """
        )
        first_buckets = session.run(first_query, domain=domain) if domain else session.run(first_query)
        out["top_taxonomy_first_buckets"] = [
            {"first_bucket": r["first_bucket"], "count": int(r["n"])}
            for r in first_buckets
        ]

        sub_query = (
            """
            MATCH (d:Document)-[:IN_TAXONOMY_SUB_BUCKET]->(s:TaxonomySubBucket)
            RETURN s.id AS sub_bucket, count(DISTINCT d) AS n
            ORDER BY n DESC
            LIMIT 8
            """
            if not domain
            else """
            MATCH (d:Document)-[:IN_DOMAIN]->(:Domain {id:$domain})
            MATCH (d)-[:IN_TAXONOMY_SUB_BUCKET]->(s:TaxonomySubBucket)
            RETURN s.id AS sub_bucket, count(DISTINCT d) AS n
            ORDER BY n DESC
            LIMIT 8
            """
        )
        sub_buckets = session.run(sub_query, domain=domain) if domain else session.run(sub_query)
        out["top_taxonomy_sub_buckets"] = [
            {"sub_bucket": r["sub_bucket"], "count": int(r["n"])}
            for r in sub_buckets
        ]

        try:
            temporal_query = (
                """
                MATCH (d:Document)
                WHERE d.mtime_date IS NOT NULL AND d.mtime_date <> ''
                RETURN substring(d.mtime_date, 0, 4) AS year, count(d) AS n
                ORDER BY year
                """
                if not domain
                else """
                MATCH (d:Document)-[:IN_DOMAIN]->(:Domain {id:$domain})
                WHERE d.mtime_date IS NOT NULL AND d.mtime_date <> ''
                RETURN substring(d.mtime_date, 0, 4) AS year, count(d) AS n
                ORDER BY year
                """
            )
            temporal_rows = session.run(temporal_query, domain=domain) if domain else session.run(temporal_query)
            temporal_counts = {str(r["year"]): int(r["n"]) for r in temporal_rows if str(r.get("year", "")).strip()}
            if temporal_counts:
                out["temporal_counts"] = temporal_counts
        except Exception:
            pass
    return out


def load_domain_first_bucket_priors(es: Elasticsearch, index_name: str) -> Dict[str, List[str]]:
    body = {
        "size": 0,
        "query": {"term": {"semantic_decision": "routed"}},
        "aggs": {
            "domains": {
                "terms": {"field": "semantic_domain", "size": 64},
                "aggs": {"first_buckets": {"terms": {"field": "first_bucket", "size": 20}}},
            }
        },
    }
    out: Dict[str, List[str]] = {}
    try:
        resp = es.search(index=index_name, body=body)
    except Exception as exc:
        LOGGER.warning("domain first-bucket prior load failed: %s", exc)
        return out

    domains = (((resp or {}).get("aggregations") or {}).get("domains") or {}).get("buckets") or []
    for d in domains:
        did = str((d or {}).get("key") or "").strip()
        if not did:
            continue
        buckets = ((d or {}).get("first_buckets") or {}).get("buckets") or []
        vals: List[str] = []
        for b in buckets:
            fb = str((b or {}).get("key") or "").strip()
            if fb:
                vals.append(fb)
        if vals:
            out[did] = vals
    return out


app = FastAPI(title="Dearly Departed Query API", version="0.1.0")

_es: Elasticsearch | None = None
_qdr: QdrantClient | None = None
_neo: Any = None


@app.on_event("startup")
def startup() -> None:
    global _es, _qdr, _neo, _taxonomy_router_obj, _taxonomy_index, _decomposer_chat, _domain_first_bucket_priors, _corpus_temporal_bounds
    _es = Elasticsearch(ES_URL)
    _qdr = QdrantClient(url=QDRANT_URL, timeout=float(QDRANT_TIMEOUT_SEC))
    _neo = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    _taxonomy_router_obj = load_taxonomy_router(TAXONOMY_ROUTER_JSON)
    _taxonomy_index = build_taxonomy_index(_taxonomy_router_obj)
    _domain_first_bucket_priors = load_domain_first_bucket_priors(_es, ES_CHUNK_INDEX)
    _corpus_temporal_bounds = _load_corpus_temporal_bounds(_es, ES_CHUNK_INDEX)
    _decomposer_chat = None
    if QUERY_DECOMPOSER_ENABLED and QUERY_DECOMPOSER_USE_LLM:
        if ChatProvider is None:
            LOGGER.warning("query decomposer LLM disabled: chat provider import failed")
        else:
            try:
                _decomposer_chat = ChatProvider()
            except Exception as exc:
                LOGGER.warning("query decomposer LLM disabled: %s", exc)
    router_path = Path(TAXONOMY_ROUTER_JSON).expanduser().resolve()
    router_sha = ""
    if router_path.exists():
        try:
            router_sha = hashlib.sha256(router_path.read_bytes()).hexdigest()
        except Exception:
            router_sha = ""
    LOGGER.info(
        "query decomposer: enabled=%s llm=%s taxonomy_first=%d taxonomy_sub=%d domain_priors=%d router=%s router_sha256=%s temporal_bounds=%s",
        bool(QUERY_DECOMPOSER_ENABLED),
        bool(_decomposer_chat is not None),
        len((_taxonomy_index.get("first") or {})),
        len((_taxonomy_index.get("sub") or {})),
        len(_domain_first_bucket_priors),
        str(router_path),
        router_sha[:16] if router_sha else "",
        _corpus_temporal_bounds,
    )


@app.on_event("shutdown")
def shutdown() -> None:
    global _neo
    if _neo is not None:
        _neo.close()


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        es_ok = bool(_es and _es.ping())
    except Exception:
        es_ok = False
    try:
        q_ok = False
        if _qdr is not None:
            q_ok = bool(_qdr.get_collections() is not None)
    except Exception:
        q_ok = False
    try:
        e_ok = _http_request("GET", EMBED_URL.replace("/v1/embeddings", "/v1/models"), timeout=5).status_code < 300
    except Exception:
        e_ok = False
    r_ok = True
    if RERANK_ENABLED:
        try:
            r_ok = _http_request("GET", RERANK_URL.replace("/v1/rerank", "/v1/models"), timeout=5).status_code < 300
        except Exception:
            r_ok = False
    return {
        "ok": bool(es_ok and q_ok and e_ok and r_ok),
        "api_started_at": API_STARTED_AT,
        "api_build_id": APP_BUILD_ID,
        "services": {
            "elasticsearch": es_ok,
            "qdrant": q_ok,
            "embedder": e_ok,
            "reranker": r_ok,
        },
        "decomposer": {
            "enabled": bool(QUERY_DECOMPOSER_ENABLED),
            "llm_enabled": bool(QUERY_DECOMPOSER_USE_LLM),
            "llm_ready": bool(_decomposer_chat is not None),
            "taxonomy_first_buckets": len((_taxonomy_index.get("first") or {})),
            "taxonomy_sub_buckets": len((_taxonomy_index.get("sub") or {})),
            "domain_priors": len(_domain_first_bucket_priors),
        },
        "retrieval_controls": {
            "diagnostics_enabled": bool(QUERY_DIAGNOSTICS_ENABLED),
            "rebalance_enabled": bool(QUERY_REBALANCE_ENABLED),
            "intent_rescue_pass": bool(QUERY_INTENT_RESCUE_PASS),
            "require_routed_chunks": bool(QUERY_REQUIRE_ROUTED_CHUNKS),
            "first_bucket_floor_min_taxonomy_coverage": float(
                max(0.0, min(1.0, QUERY_FIRST_BUCKET_HINT_FLOOR_MIN_TAXONOMY_COVERAGE))
            ),
            "min_text_evidence": int(QUERY_MIN_TEXT_EVIDENCE),
            "max_claim_ratio": float(QUERY_MAX_CLAIM_RATIO),
            "max_taxonomy_ratio": float(QUERY_MAX_TAXONOMY_RATIO),
            "max_per_path": int(QUERY_MAX_PER_PATH),
        },
    }


@app.post("/retrieve", response_model=RetrieveResponse)
def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    if _es is None or _qdr is None:
        raise HTTPException(status_code=503, detail="Query services not initialized")

    q = str(req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    requested_mode = str(req.mode or "basic").strip().lower()
    if requested_mode not in _DECOMPOSER_MODE_VALUES:
        raise HTTPException(status_code=400, detail="mode must be one of: basic, local, global, drift, auto")
    date_from = str(req.date_from or "").strip()
    date_to = str(req.date_to or "").strip()
    temporal_basis = str(req.temporal_basis or "effective").strip().lower()
    if temporal_basis not in {"effective", "mtime"}:
        raise HTTPException(status_code=400, detail="temporal_basis must be one of: effective, mtime")
    date_from_parsed = _parse_iso_date(date_from) if date_from else None
    date_to_parsed = _parse_iso_date(date_to) if date_to else None
    if date_from and date_from_parsed is None:
        raise HTTPException(status_code=400, detail="date_from must be ISO date YYYY-MM-DD")
    if date_to and date_to_parsed is None:
        raise HTTPException(status_code=400, detail="date_to must be ISO date YYYY-MM-DD")
    if date_from_parsed is not None and date_to_parsed is not None and date_from_parsed > date_to_parsed:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")

    decomposition = build_query_decomposition(
        q,
        req.domain,
        requested_mode,
        enabled=bool(req.decompose),
    )
    effective_domain = str(decomposition.get("effective_domain", req.domain)).strip()
    effective_mode = str(decomposition.get("effective_mode", "basic")).strip().lower()
    if effective_mode not in _MODE_VALUES:
        effective_mode = "basic"
    first_hints = [str(x).strip() for x in (decomposition.get("first_bucket_hints") or []) if str(x).strip()]
    sub_hints = [str(x).strip() for x in (decomposition.get("sub_bucket_hints") or []) if str(x).strip()]
    graphrag_generation_mode = str(req.graphrag_generation_mode or "both").strip().lower()
    if graphrag_generation_mode == "llm":
        graphrag_use_llm = True
        graphrag_use_deterministic = False
    elif graphrag_generation_mode == "deterministic":
        graphrag_use_llm = False
        graphrag_use_deterministic = True
    else:
        graphrag_use_llm = bool(req.graphrag_use_llm)
        graphrag_use_deterministic = bool(req.graphrag_use_deterministic)
    graphrag_artifact_types = _normalize_artifact_types(req.graphrag_artifact_types or [])
    intent_profile = build_query_intent_profile(q, requested_mode, effective_mode)
    intent_name = str(intent_profile.get("intent", "")).strip().lower()
    source_path_hint = str(req.source_path_hint or "").strip()
    ownership_role_open_domain_guard_applied = False
    ownership_role_open_domain_guard_reason = ""
    if (
        intent_name == "ownership_role"
        and not source_path_hint
        and requested_mode == "auto"
    ):
        ownership_role_open_domain_guard_applied = True
        ownership_role_open_domain_guard_reason = "open_domain_person_role_auto_mode"
        effective_mode = "basic"
        first_hints = []
        sub_hints = []
        if not str(req.domain or "").strip():
            effective_domain = ""
        decomposition["effective_mode"] = effective_mode
        decomposition["effective_domain"] = effective_domain
        decomposition["first_bucket_hints"] = []
        decomposition["sub_bucket_hints"] = []
        decomposition["ownership_role_open_domain_guard"] = {
            "applied": True,
            "reason": ownership_role_open_domain_guard_reason,
        }

    candidate_k = int(req.top_k)
    if RERANK_ENABLED:
        candidate_k = max(
            candidate_k,
            int(QUERY_CANDIDATE_POOL),
            int(intent_profile.get("rerank_pool_target", QUERY_CANDIDATE_POOL)),
        )
    # Keep an expanded internal pool so source-path scan rows and intent rescues
    # can survive to rerank/floor enforcement even when API top_k is small.
    candidate_k = min(int(QUERY_CANDIDATE_MAX), max(1, candidate_k))

    def run_retrieval_pass(
        pass_domain: str,
        pass_mode: str,
        pass_first_hints: List[str],
        pass_sub_hints: List[str],
        pass_graphrag_use_llm: bool,
        pass_graphrag_use_deterministic: bool,
        pass_graphrag_artifact_types: List[str],
        pass_date_from: str,
        pass_date_to: str,
        pass_temporal_basis: str,
    ) -> Dict[str, Any]:
        backend_status: Dict[str, Any] = {
            "es_chunks": {"ok": True, "error": "", "count": 0},
            "embed_query": {"ok": True, "error": ""},
            "qdrant_chunks": {"ok": True, "error": "", "count": 0},
            "es_graphrag": {"ok": True, "error": "", "count": 0},
            "qdrant_graphrag": {"ok": True, "error": "", "count": 0},
            "es_taxonomy_hint": {"ok": True, "error": "", "count": 0},
            "qdrant_taxonomy_hint": {"ok": True, "error": "", "count": 0},
            "es_graphrag_taxonomy_hint": {"ok": True, "error": "", "count": 0},
            "qdrant_graphrag_taxonomy_hint": {"ok": True, "error": "", "count": 0},
        }
        try:
            pass_es_hits = es_search(
                _es,
                q,
                pass_domain,
                candidate_k,
                date_from=pass_date_from,
                date_to=pass_date_to,
                temporal_basis=pass_temporal_basis,
            )
            backend_status["es_chunks"]["count"] = int(len(pass_es_hits))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"elasticsearch retrieval failed: {exc}") from exc

        pass_q_hits: List[Dict[str, Any]] = []
        pass_es_graphrag_hits: List[Dict[str, Any]] = []
        pass_qdr_graphrag_hits: List[Dict[str, Any]] = []
        pass_es_hint_hits: List[Dict[str, Any]] = []
        pass_qdr_hint_hits: List[Dict[str, Any]] = []
        pass_es_graphrag_hint_hits: List[Dict[str, Any]] = []
        pass_qdr_graphrag_hint_hits: List[Dict[str, Any]] = []

        try:
            pass_es_graphrag_hits = es_graphrag_search(
                _es,
                q,
                pass_domain,
                pass_mode,
                candidate_k,
                use_llm=pass_graphrag_use_llm,
                use_deterministic=pass_graphrag_use_deterministic,
                artifact_types=pass_graphrag_artifact_types,
                date_from=pass_date_from,
                date_to=pass_date_to,
                temporal_basis=pass_temporal_basis,
            )
            backend_status["es_graphrag"]["count"] = int(len(pass_es_graphrag_hits))
            if pass_first_hints:
                pass_es_hint_hits.extend(
                    es_taxonomy_hint_search(
                        _es,
                        q,
                        pass_domain,
                        candidate_k,
                        field="first_bucket",
                        values=pass_first_hints,
                        date_from=pass_date_from,
                        date_to=pass_date_to,
                        temporal_basis=pass_temporal_basis,
                    )
                )
                pass_es_graphrag_hint_hits.extend(
                    es_graphrag_hint_search(
                        _es,
                        q,
                        pass_domain,
                        pass_mode,
                        candidate_k,
                        field="first_bucket",
                        values=pass_first_hints,
                        use_llm=pass_graphrag_use_llm,
                        use_deterministic=pass_graphrag_use_deterministic,
                        artifact_types=pass_graphrag_artifact_types,
                        date_from=pass_date_from,
                        date_to=pass_date_to,
                        temporal_basis=pass_temporal_basis,
                    )
                )
            if pass_sub_hints:
                pass_es_hint_hits.extend(
                    es_taxonomy_hint_search(
                        _es,
                        q,
                        pass_domain,
                        candidate_k,
                        field="sub_bucket",
                        values=pass_sub_hints,
                        date_from=pass_date_from,
                        date_to=pass_date_to,
                        temporal_basis=pass_temporal_basis,
                    )
                )
                pass_es_graphrag_hint_hits.extend(
                    es_graphrag_hint_search(
                        _es,
                        q,
                        pass_domain,
                        pass_mode,
                        candidate_k,
                        field="sub_bucket",
                        values=pass_sub_hints,
                        use_llm=pass_graphrag_use_llm,
                        use_deterministic=pass_graphrag_use_deterministic,
                        artifact_types=pass_graphrag_artifact_types,
                        date_from=pass_date_from,
                        date_to=pass_date_to,
                        temporal_basis=pass_temporal_basis,
                    )
                )
            backend_status["es_taxonomy_hint"]["count"] = int(len(pass_es_hint_hits))
            backend_status["es_graphrag_taxonomy_hint"]["count"] = int(len(pass_es_graphrag_hint_hits))
        except Exception as exc:
            pass_es_graphrag_hits = []
            pass_es_hint_hits = []
            pass_es_graphrag_hint_hits = []
            backend_status["es_graphrag"]["ok"] = False
            backend_status["es_graphrag"]["error"] = str(exc)[:220]
            backend_status["es_taxonomy_hint"]["ok"] = False
            backend_status["es_taxonomy_hint"]["error"] = str(exc)[:220]
            backend_status["es_graphrag_taxonomy_hint"]["ok"] = False
            backend_status["es_graphrag_taxonomy_hint"]["error"] = str(exc)[:220]

        vec: List[float] = []
        try:
            vec = embed_query(q)
        except Exception as exc:
            backend_status["embed_query"]["ok"] = False
            backend_status["embed_query"]["error"] = str(exc)[:220]

        if vec:
            try:
                pass_q_hits = qdrant_search(
                    _qdr,
                    vec,
                    pass_domain,
                    candidate_k,
                    date_from=pass_date_from,
                    date_to=pass_date_to,
                    temporal_basis=pass_temporal_basis,
                )
                backend_status["qdrant_chunks"]["count"] = int(len(pass_q_hits))
            except Exception as exc:
                pass_q_hits = []
                backend_status["qdrant_chunks"]["ok"] = False
                backend_status["qdrant_chunks"]["error"] = str(exc)[:220]

            try:
                pass_qdr_graphrag_hits = qdrant_graphrag_search(
                    _qdr,
                    vec,
                    pass_domain,
                    pass_mode,
                    candidate_k,
                    use_llm=pass_graphrag_use_llm,
                    use_deterministic=pass_graphrag_use_deterministic,
                    artifact_types=pass_graphrag_artifact_types,
                    date_from=pass_date_from,
                    date_to=pass_date_to,
                    temporal_basis=pass_temporal_basis,
                )
                backend_status["qdrant_graphrag"]["count"] = int(len(pass_qdr_graphrag_hits))
            except Exception as exc:
                pass_qdr_graphrag_hits = []
                backend_status["qdrant_graphrag"]["ok"] = False
                backend_status["qdrant_graphrag"]["error"] = str(exc)[:220]

            try:
                if pass_first_hints:
                    pass_qdr_hint_hits.extend(
                        qdrant_taxonomy_hint_search(
                            _qdr,
                            vec,
                            pass_domain,
                            candidate_k,
                            field="first_bucket",
                            values=pass_first_hints,
                            date_from=pass_date_from,
                            date_to=pass_date_to,
                            temporal_basis=pass_temporal_basis,
                        )
                    )
                if pass_sub_hints:
                    pass_qdr_hint_hits.extend(
                        qdrant_taxonomy_hint_search(
                            _qdr,
                            vec,
                            pass_domain,
                            candidate_k,
                            field="sub_bucket",
                            values=pass_sub_hints,
                            date_from=pass_date_from,
                            date_to=pass_date_to,
                            temporal_basis=pass_temporal_basis,
                        )
                    )
                backend_status["qdrant_taxonomy_hint"]["count"] = int(len(pass_qdr_hint_hits))
            except Exception as exc:
                pass_qdr_hint_hits = []
                backend_status["qdrant_taxonomy_hint"]["ok"] = False
                backend_status["qdrant_taxonomy_hint"]["error"] = str(exc)[:220]

            try:
                if pass_first_hints:
                    pass_qdr_graphrag_hint_hits.extend(
                        qdrant_graphrag_hint_search(
                            _qdr,
                            vec,
                            pass_domain,
                            pass_mode,
                            candidate_k,
                            field="first_bucket",
                            values=pass_first_hints,
                            use_llm=pass_graphrag_use_llm,
                            use_deterministic=pass_graphrag_use_deterministic,
                            artifact_types=pass_graphrag_artifact_types,
                            date_from=pass_date_from,
                            date_to=pass_date_to,
                            temporal_basis=pass_temporal_basis,
                        )
                    )
                if pass_sub_hints:
                    pass_qdr_graphrag_hint_hits.extend(
                        qdrant_graphrag_hint_search(
                            _qdr,
                            vec,
                            pass_domain,
                            pass_mode,
                            candidate_k,
                            field="sub_bucket",
                            values=pass_sub_hints,
                            use_llm=pass_graphrag_use_llm,
                            use_deterministic=pass_graphrag_use_deterministic,
                            artifact_types=pass_graphrag_artifact_types,
                            date_from=pass_date_from,
                            date_to=pass_date_to,
                            temporal_basis=pass_temporal_basis,
                        )
                    )
                backend_status["qdrant_graphrag_taxonomy_hint"]["count"] = int(len(pass_qdr_graphrag_hint_hits))
            except Exception as exc:
                pass_qdr_graphrag_hint_hits = []
                backend_status["qdrant_graphrag_taxonomy_hint"]["ok"] = False
                backend_status["qdrant_graphrag_taxonomy_hint"]["error"] = str(exc)[:220]
        else:
            # Embed path unavailable: mark all vector search families degraded.
            backend_status["qdrant_chunks"]["ok"] = False
            backend_status["qdrant_chunks"]["error"] = backend_status["embed_query"]["error"] or "embed_query_failed"
            backend_status["qdrant_graphrag"]["ok"] = False
            backend_status["qdrant_graphrag"]["error"] = backend_status["embed_query"]["error"] or "embed_query_failed"
            backend_status["qdrant_taxonomy_hint"]["ok"] = False
            backend_status["qdrant_taxonomy_hint"]["error"] = backend_status["embed_query"]["error"] or "embed_query_failed"
            backend_status["qdrant_graphrag_taxonomy_hint"]["ok"] = False
            backend_status["qdrant_graphrag_taxonomy_hint"]["error"] = backend_status["embed_query"]["error"] or "embed_query_failed"
            if not backend_status["embed_query"]["error"]:
                backend_status["embed_query"]["error"] = "empty_embedding_vector"

        pass_extra_sources: List[Tuple[str, List[Dict[str, Any]]]] = [
            ("es_graphrag", pass_es_graphrag_hits),
            ("qdrant_graphrag", pass_qdr_graphrag_hits),
            ("es_taxonomy_hint", pass_es_hint_hits),
            ("qdrant_taxonomy_hint", pass_qdr_hint_hits),
            ("es_graphrag_taxonomy_hint", pass_es_graphrag_hint_hits),
            ("qdrant_graphrag_taxonomy_hint", pass_qdr_graphrag_hint_hits),
        ]
        pass_fused = fuse_hits(
            pass_es_hits,
            pass_q_hits,
            candidate_k,
            extra_sources=pass_extra_sources,
        )
        return {
            "fused": pass_fused,
            "backend_status": backend_status,
            "degraded": {
                "es_only": bool(not backend_status["qdrant_chunks"]["ok"]),
                "no_graphrag": bool(
                    (not backend_status["es_graphrag"]["ok"] and not backend_status["qdrant_graphrag"]["ok"])
                    or (
                        backend_status["es_graphrag"]["count"] == 0
                        and backend_status["qdrant_graphrag"]["count"] == 0
                    )
                ),
            },
        }

    rerank_applied = False
    diagnostics: Dict[str, Any] = {}
    try:
        fused_candidates = run_retrieval_pass(
            effective_domain,
            effective_mode,
            first_hints,
            sub_hints,
            graphrag_use_llm,
            graphrag_use_deterministic,
            graphrag_artifact_types,
            date_from,
            date_to,
            temporal_basis,
        )
        backend_status_history: Dict[str, Any] = {"primary": fused_candidates.get("backend_status", {})}
        fused_candidates = fused_candidates["fused"]
        response_domain = effective_domain
        fallback_steps: List[str] = []
        diagnostics = {
            "intent_profile": intent_profile,
            "candidate_pool_target": int(candidate_k),
            "initial_candidate_mix": summarize_hit_mix(fused_candidates[: int(req.top_k)]),
            "backend_status": backend_status_history,
            "temporal_filter": {
                "date_from": date_from,
                "date_to": date_to,
                "temporal_basis": temporal_basis,
                "active": bool(date_from or date_to),
            },
            "ownership_role_open_domain_guard": {
                "applied": bool(ownership_role_open_domain_guard_applied),
                "reason": ownership_role_open_domain_guard_reason,
            },
        }
        if source_path_hint:
            diagnostics["source_path_hint"] = source_path_hint
        if not fused_candidates and bool(effective_domain):
            relaxed = run_retrieval_pass(
                "",
                effective_mode,
                first_hints,
                sub_hints,
                graphrag_use_llm,
                graphrag_use_deterministic,
                graphrag_artifact_types,
                date_from,
                date_to,
                temporal_basis,
            )
            backend_status_history["drop_domain"] = relaxed.get("backend_status", {})
            relaxed = relaxed["fused"]
            fallback_steps.append("drop_domain")
            if relaxed:
                fused_candidates = relaxed
                response_domain = ""
        if not fused_candidates and (first_hints or sub_hints):
            relaxed_no_hints = run_retrieval_pass(
                "",
                effective_mode,
                [],
                [],
                graphrag_use_llm,
                graphrag_use_deterministic,
                graphrag_artifact_types,
                date_from,
                date_to,
                temporal_basis,
            )
            backend_status_history["drop_taxonomy_hints"] = relaxed_no_hints.get("backend_status", {})
            relaxed_no_hints = relaxed_no_hints["fused"]
            fallback_steps.append("drop_taxonomy_hints")
            if relaxed_no_hints:
                fused_candidates = relaxed_no_hints
                response_domain = ""

        source_path_rescue_pool: List[Dict[str, Any]] = []
        if QUERY_INTENT_RESCUE_PASS and fused_candidates and bool(intent_profile.get("requires_text_evidence")):
            top_mix = summarize_hit_mix(fused_candidates[: int(req.top_k)])
            min_text = int(intent_profile.get("min_text_evidence", QUERY_MIN_TEXT_EVIDENCE))
            rescue_mode = str(intent_profile.get("rescue_mode", "")).strip().lower()
            row_shape_queries, row_shape_patterns = _row_shape_rescue_queries(q, intent_name)
            row_shape_rescue_applied = False
            row_shape_rescue_added_hits = 0
            source_path_weak = False
            if source_path_hint:
                source_rank_obs = _path_rank_for_rows(
                    fused_candidates[: max(int(req.top_k), int(QUERY_RESCUE_TOP_K))],
                    source_path_hint,
                )
                source_path_weak = bool(source_rank_obs <= 0 or source_rank_obs > int(req.top_k))
            if top_mix.get("text_evidence_hits", 0) < min_text and rescue_mode == "basic" and effective_mode != "basic":
                rescue_rows = run_retrieval_pass(
                    response_domain,
                    "basic",
                    first_hints,
                    sub_hints,
                    graphrag_use_llm,
                    graphrag_use_deterministic,
                    [],
                    date_from,
                    date_to,
                    temporal_basis,
                )["fused"]
                if rescue_rows:
                    fused_candidates = merge_rescue_hits(
                        fused_candidates,
                        rescue_rows[: max(int(QUERY_RESCUE_TOP_K), int(req.top_k))],
                        "intent_rescue_basic",
                    )
                    fallback_steps.append("intent_rescue_basic_mode")
                    diagnostics["intent_rescue_added_hits"] = int(len(rescue_rows))
                    diagnostics["post_rescue_candidate_mix"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])
                    top_mix = summarize_hit_mix(fused_candidates[: int(req.top_k)])

            if source_path_hint:
                source_path_rescue = es_source_path_rescue_search(
                    _es,
                    q,
                    source_path_hint,
                    response_domain,
                    max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                    date_from=date_from,
                    date_to=date_to,
                    temporal_basis=temporal_basis,
                )
                if not source_path_rescue and response_domain:
                    source_path_rescue = es_source_path_rescue_search(
                        _es,
                        q,
                        source_path_hint,
                        "",
                        max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                        date_from=date_from,
                        date_to=date_to,
                        temporal_basis=temporal_basis,
                    )
                qdrant_source_path_rescue: List[Dict[str, Any]] = []
                source_vec: List[float] = []
                try:
                    source_vec = embed_query(q)
                except Exception:
                    source_vec = []
                if source_vec:
                    qdrant_source_path_rescue = qdrant_source_path_rescue_search(
                        _qdr,
                        source_vec,
                        source_path_hint,
                        response_domain,
                        max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                        date_from=date_from,
                        date_to=date_to,
                        temporal_basis=temporal_basis,
                    )
                    if not qdrant_source_path_rescue and response_domain:
                        qdrant_source_path_rescue = qdrant_source_path_rescue_search(
                            _qdr,
                            source_vec,
                            source_path_hint,
                            "",
                            max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                            date_from=date_from,
                            date_to=date_to,
                            temporal_basis=temporal_basis,
                        )
                combined_source_path_rescue = list(source_path_rescue) + list(qdrant_source_path_rescue)
                if combined_source_path_rescue:
                    source_path_rescue_pool = [dict(row) for row in combined_source_path_rescue if isinstance(row, dict)]
                    fused_candidates = merge_rescue_hits(
                        fused_candidates,
                        combined_source_path_rescue,
                        "source_path_rescue",
                    )
                    fallback_steps.append("source_path_rescue")
                    diagnostics["source_path_rescue_added_hits"] = int(len(combined_source_path_rescue))
                    diagnostics["source_path_rescue_es_hits"] = int(len(source_path_rescue))
                    diagnostics["source_path_rescue_qdrant_hits"] = int(len(qdrant_source_path_rescue))
                    diagnostics["post_rescue_candidate_mix"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])

            if row_shape_queries and (
                top_mix.get("text_evidence_hits", 0) < min_text or bool(source_path_weak)
            ) and intent_name in {
                "factual_lookup",
                "troubleshooting",
                "procedure_steps",
                "risk_detection",
                "compliance_constraint",
                "definition",
            }:
                row_shape_hits: List[Dict[str, Any]] = []
                seen_ids: Set[str] = set()
                for row_query in row_shape_queries:
                    rescue_rows = es_text_rescue_search(
                        _es,
                        row_query,
                        response_domain,
                        max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                        date_from=date_from,
                        date_to=date_to,
                        temporal_basis=temporal_basis,
                    )
                    if not rescue_rows and response_domain:
                        rescue_rows = es_text_rescue_search(
                            _es,
                            row_query,
                            "",
                            max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                            date_from=date_from,
                            date_to=date_to,
                            temporal_basis=temporal_basis,
                        )
                    for row in rescue_rows:
                        rid = _hit_item_id(row)
                        if rid and rid in seen_ids:
                            continue
                        if rid:
                            seen_ids.add(rid)
                        row_shape_hits.append(dict(row))
                if row_shape_hits:
                    fused_candidates = merge_rescue_hits(
                        fused_candidates,
                        row_shape_hits,
                        "intent_rescue_row_shape",
                    )
                    fallback_steps.append("intent_rescue_row_shape")
                    row_shape_rescue_applied = True
                    row_shape_rescue_added_hits = int(len(row_shape_hits))
                    diagnostics["post_rescue_candidate_mix"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])
                    top_mix = summarize_hit_mix(fused_candidates[: int(req.top_k)])

            if intent_name in {
                "factual_lookup",
                "ownership",
                "ownership_role",
                "troubleshooting",
                "risk_detection",
                "timeline_version",
                "procedure_steps",
                "compliance_constraint",
                "definition",
            }:
                phrase_rescue = es_exact_phrase_rescue_search(
                    _es,
                    q,
                    response_domain,
                    max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                    date_from=date_from,
                    date_to=date_to,
                    temporal_basis=temporal_basis,
                )
                if not phrase_rescue and response_domain:
                    phrase_rescue = es_exact_phrase_rescue_search(
                        _es,
                        q,
                        "",
                        max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                        date_from=date_from,
                        date_to=date_to,
                        temporal_basis=temporal_basis,
                    )
                if phrase_rescue:
                    fused_candidates = merge_rescue_hits(
                        fused_candidates,
                        phrase_rescue,
                        "intent_rescue_phrase",
                    )
                    fallback_steps.append("intent_rescue_phrase")
                    diagnostics["intent_rescue_phrase_added_hits"] = int(len(phrase_rescue))
                    diagnostics["post_rescue_candidate_mix"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])
                    top_mix = summarize_hit_mix(fused_candidates[: int(req.top_k)])

            if intent_name == "ownership_role":
                role_rescue_queries, role_rescue_patterns = _ownership_role_rescue_queries(q)
                ownership_role_rescue_hits: List[Dict[str, Any]] = []
                seen_role_ids: Set[str] = set()
                for role_query in role_rescue_queries:
                    role_rows = es_text_rescue_search(
                        _es,
                        role_query,
                        response_domain,
                        max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                        date_from=date_from,
                        date_to=date_to,
                        temporal_basis=temporal_basis,
                    )
                    if not role_rows and response_domain:
                        role_rows = es_text_rescue_search(
                            _es,
                            role_query,
                            "",
                            max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                            date_from=date_from,
                            date_to=date_to,
                            temporal_basis=temporal_basis,
                        )
                    for row in role_rows:
                        rid = _hit_item_id(row)
                        if rid and rid in seen_role_ids:
                            continue
                        if rid:
                            seen_role_ids.add(rid)
                        ownership_role_rescue_hits.append(dict(row))
                if ownership_role_rescue_hits:
                    fused_candidates = merge_rescue_hits(
                        fused_candidates,
                        ownership_role_rescue_hits,
                        "intent_rescue_ownership_role",
                    )
                    fallback_steps.append("intent_rescue_ownership_role")
                    diagnostics["ownership_role_rescue_added_hits"] = int(len(ownership_role_rescue_hits))
                    diagnostics["ownership_role_rescue_patterns"] = role_rescue_patterns
                    diagnostics["post_rescue_candidate_mix"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])
                    top_mix = summarize_hit_mix(fused_candidates[: int(req.top_k)])

            if top_mix.get("text_evidence_hits", 0) < min_text:
                lexical_rescue = es_text_rescue_search(
                    _es,
                    q,
                    response_domain,
                    max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                    date_from=date_from,
                    date_to=date_to,
                    temporal_basis=temporal_basis,
                )
                if not lexical_rescue and response_domain:
                    lexical_rescue = es_text_rescue_search(
                        _es,
                        q,
                        "",
                        max(int(QUERY_RESCUE_TOP_K), int(req.top_k)),
                        date_from=date_from,
                        date_to=date_to,
                        temporal_basis=temporal_basis,
                    )
                if lexical_rescue:
                    fused_candidates = merge_rescue_hits(
                        fused_candidates,
                        lexical_rescue,
                        "intent_rescue_lexical",
                    )
                    fallback_steps.append("intent_rescue_lexical")
                    diagnostics["intent_rescue_lexical_added_hits"] = int(len(lexical_rescue))
                    diagnostics["post_rescue_candidate_mix"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])

            diagnostics["row_shape_rescue_applied"] = bool(row_shape_rescue_applied)
            diagnostics["row_shape_rescue_patterns"] = row_shape_patterns
            diagnostics["row_shape_rescue_added_hits"] = int(row_shape_rescue_added_hits)

        if fallback_steps:
            decomposition["fallback_steps"] = fallback_steps
            decomposition["effective_domain_final"] = response_domain
        if source_path_hint:
            decomposition["source_path_hint"] = source_path_hint
        decomposition["graphrag_use_llm"] = graphrag_use_llm
        decomposition["graphrag_use_deterministic"] = graphrag_use_deterministic
        decomposition["graphrag_generation_mode"] = graphrag_generation_mode
        decomposition["graphrag_artifact_types"] = graphrag_artifact_types

        temporal_boost_mode = str(req.temporal_boost or "").strip().lower()
        temporal_boost_active = False
        temporal_boost_trigger = ""
        if temporal_boost_mode == "recency":
            temporal_boost_active = True
            temporal_boost_trigger = "request:recency"
        elif temporal_boost_mode == "none":
            temporal_boost_active = False
            temporal_boost_trigger = "request:none"
        elif QUERY_TEMPORAL_BOOST_ENABLED and intent_name in {"timeline_version"}:
            temporal_boost_active = True
            temporal_boost_trigger = "intent:timeline_version"

        temporal_boost_diag: Dict[str, Any] = {
            "active": bool(temporal_boost_active),
            "weight": float(QUERY_TEMPORAL_BOOST_WEIGHT),
            "trigger": temporal_boost_trigger,
        }
        if temporal_boost_active and fused_candidates:
            bounds = _corpus_temporal_bounds.get(temporal_basis, {}) if isinstance(_corpus_temporal_bounds, dict) else {}
            bmin = str(bounds.get("min", "")).strip()
            bmax = str(bounds.get("max", "")).strip()
            year_dist: Dict[str, int] = defaultdict(int)
            for row in fused_candidates:
                tdate = _extract_hit_temporal_date(row, temporal_basis)
                score = _temporal_recency_score(tdate, bmin, bmax)
                row["temporal_recency_score"] = float(score)
                row["fusion_score"] = float(row.get("fusion_score", 0.0)) + (float(QUERY_TEMPORAL_BOOST_WEIGHT) * float(score))
                d = _parse_iso_date(tdate)
                if d is not None:
                    year_dist[str(d.year)] += 1
            fused_candidates = sorted(
                fused_candidates,
                key=lambda x: float(x.get("fusion_score", 0.0)),
                reverse=True,
            )
            for idx, row in enumerate(fused_candidates, start=1):
                row["rank"] = int(idx)
            temporal_boost_diag["corpus_date_range"] = [bmin, bmax]
            temporal_boost_diag["result_date_distribution"] = dict(sorted(year_dist.items(), key=lambda kv: kv[0]))

        ranked_hits: List[Dict[str, Any]]
        if RERANK_ENABLED and fused_candidates:
            try:
                ranked_hits = rerank_hits(q, fused_candidates, int(candidate_k))
                rerank_applied = True
            except Exception as exc:
                LOGGER.warning("Rerank unavailable; using fused ranking: %s", exc)
                ranked_hits = fused_candidates[: int(candidate_k)]
        else:
            ranked_hits = fused_candidates[: int(candidate_k)]

        rebalance_diag: Dict[str, Any] = {"applied": False}
        if QUERY_REBALANCE_ENABLED and ranked_hits:
            fused, rebalance_diag = rebalance_ranked_hits(
                ranked_hits,
                int(req.top_k),
                intent_profile,
                source_path_hint=source_path_hint,
            )
        else:
            fused = ranked_hits[: int(req.top_k)]

        first_bucket_floor_diag: Dict[str, Any] = {"applied": False}
        if QUERY_FIRST_BUCKET_HINT_FLOOR and ranked_hits and first_hints:
            taxonomy_covered = sum(1 for row in ranked_hits if str(row.get("first_bucket") or "").strip())
            taxonomy_cov = float(taxonomy_covered) / float(max(1, len(ranked_hits)))
            min_cov = max(0.0, min(1.0, float(QUERY_FIRST_BUCKET_HINT_FLOOR_MIN_TAXONOMY_COVERAGE)))
            if taxonomy_cov >= min_cov:
                first_bucket_floor_pool: List[Dict[str, Any]] = [dict(row) for row in ranked_hits if isinstance(row, dict)]
                seen_bucket_floor_ids: set[str] = set()
                for row in first_bucket_floor_pool:
                    item_id = _hit_item_id(row)
                    if item_id:
                        seen_bucket_floor_ids.add(item_id)
                for row in fused_candidates:
                    if not isinstance(row, dict):
                        continue
                    item_id = _hit_item_id(row)
                    if not item_id or item_id in seen_bucket_floor_ids:
                        continue
                    first_bucket_floor_pool.append(dict(row))
                    seen_bucket_floor_ids.add(item_id)
                fused, first_bucket_floor_diag = enforce_first_bucket_hint_floor(
                    fused,
                    first_bucket_floor_pool,
                    first_hints,
                    int(req.top_k),
                )
                first_bucket_floor_diag["taxonomy_coverage"] = float(round(taxonomy_cov, 4))
                first_bucket_floor_diag["min_required_coverage"] = float(round(min_cov, 4))
            else:
                first_bucket_floor_diag = {
                    "applied": False,
                    "reason": "insufficient_taxonomy_coverage",
                    "taxonomy_coverage": float(round(taxonomy_cov, 4)),
                    "min_required_coverage": float(round(min_cov, 4)),
                }

        source_path_diag: Dict[str, Any] = {"applied": False}
        if source_path_hint and ranked_hits:
            source_floor_pool: List[Dict[str, Any]] = [dict(row) for row in ranked_hits if isinstance(row, dict)]
            seen_source_ids: set[str] = set()
            for row in source_floor_pool:
                item_id = _hit_item_id(row)
                if item_id:
                    seen_source_ids.add(item_id)
            for row in fused_candidates:
                if not isinstance(row, dict):
                    continue
                item_id = _hit_item_id(row)
                if not item_id or item_id in seen_source_ids:
                    continue
                source_floor_pool.append(dict(row))
                seen_source_ids.add(item_id)
            for row in source_path_rescue_pool:
                if not isinstance(row, dict):
                    continue
                item_id = _hit_item_id(row)
                if not item_id or item_id in seen_source_ids:
                    continue
                source_floor_pool.append(dict(row))
                seen_source_ids.add(item_id)
            fused, source_path_diag = enforce_source_path_floor(
                fused,
                source_floor_pool,
                source_path_hint,
                int(req.top_k),
                q,
            )

        if QUERY_DIAGNOSTICS_ENABLED:
            diagnostics["candidate_mix_pre_rerank"] = summarize_hit_mix(fused_candidates[: int(req.top_k)])
            diagnostics["candidate_mix_post_rerank"] = summarize_hit_mix(ranked_hits[: int(req.top_k)])
            diagnostics["final_hit_mix"] = summarize_hit_mix(fused)
            diagnostics["rebalancing"] = rebalance_diag
            diagnostics["first_bucket_hint_floor"] = first_bucket_floor_diag
            diagnostics["source_path_rescue"] = source_path_diag
            diagnostics["temporal_profile"] = _build_temporal_profile(fused, temporal_basis)
            diagnostics["temporal_boost"] = temporal_boost_diag
            primary_backend = backend_status_history.get("primary", {}) if isinstance(backend_status_history, dict) else {}
            if isinstance(primary_backend, dict):
                embed_status = primary_backend.get("embed_query", {}) if isinstance(primary_backend.get("embed_query"), dict) else {}
                es_graphrag_status = primary_backend.get("es_graphrag", {}) if isinstance(primary_backend.get("es_graphrag"), dict) else {}
                qdrant_graphrag_status = primary_backend.get("qdrant_graphrag", {}) if isinstance(primary_backend.get("qdrant_graphrag"), dict) else {}
                diagnostics["backend_degraded_flags"] = {
                    "embed_query_failed": bool(not embed_status.get("ok", True)),
                    "es_only": bool(not ((primary_backend.get("qdrant_chunks", {}) if isinstance(primary_backend.get("qdrant_chunks"), dict) else {}).get("ok", True))),
                    "no_graphrag": bool(
                        (
                            (not es_graphrag_status.get("ok", True))
                            or int(es_graphrag_status.get("count", 0) or 0) == 0
                        )
                        and (
                            (not qdrant_graphrag_status.get("ok", True))
                            or int(qdrant_graphrag_status.get("count", 0) or 0) == 0
                        )
                    ),
                }
            if source_path_hint:
                diagnostics["source_path_ranks"] = {
                    "retrieve_rank": int(_path_rank_for_rows(fused_candidates, source_path_hint)),
                    "rerank_rank": int(_path_rank_for_rows(ranked_hits, source_path_hint)),
                    "final_rank": int(_path_rank_for_rows(fused, source_path_hint)),
                }
                if bool(req.diagnostics_include_rank_trace):
                    diagnostics["rank_trace"] = {
                        "retrieve": _build_rank_trace(fused_candidates, limit=max(int(req.top_k), 40)),
                        "rerank": _build_rank_trace(ranked_hits, limit=max(int(req.top_k), 40)),
                        "final": _build_rank_trace(fused, limit=max(int(req.top_k), 40)),
                    }

        graph = {}
        if req.include_graph and _neo is not None:
            graph = graph_summary(_neo, response_domain)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"retrieve failed: {exc}") from exc

    return RetrieveResponse(
        query=q,
        domain=response_domain,
        mode=effective_mode,
        top_k=int(req.top_k),
        graphrag_generation_mode=graphrag_generation_mode,
        graphrag_use_llm=bool(graphrag_use_llm),
        graphrag_use_deterministic=bool(graphrag_use_deterministic),
        rerank_applied=rerank_applied,
        hit_count=len(fused),
        hits=fused,
        graph=graph,
        decomposition=decomposition,
        diagnostics=diagnostics if QUERY_DIAGNOSTICS_ENABLED else {},
    )
