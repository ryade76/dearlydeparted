#!/usr/bin/env python3
"""
Verify ingest parity across RouterKit source docs and storage backends.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from transformers import AutoTokenizer
except Exception:  # pragma: no cover - optional
    AutoTokenizer = None  # type: ignore[assignment]

LOGGER = logging.getLogger("verify_ingest_counts")
DEFAULT_TOKENIZER_MODEL = "Qwen/Qwen3-Embedding-8B"
_TOKENIZER_CACHE: Dict[str, Any] = {}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Verify ingest document/chunk counts across stores")
    ap.add_argument("--routerkit-documents", required=True, help="Path to RouterKit documents.jsonl")
    ap.add_argument("--chunk-mode", choices=["chars", "tokens"], default="tokens")
    ap.add_argument("--chunk-chars", type=int, default=1600)
    ap.add_argument("--chunk-overlap", type=int, default=200)
    ap.add_argument("--chunk-tokens", type=int, default=1200)
    ap.add_argument("--chunk-overlap-tokens", type=int, default=100)
    ap.add_argument("--tokenizer-model", default=DEFAULT_TOKENIZER_MODEL)
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--es-doc-index", default="kb_docs")
    ap.add_argument("--es-chunk-index", default="kb_chunks")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--qdrant-collection", default="kb_chunks_vec")
    ap.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="change_me_local")
    ap.add_argument("--skip-es", action="store_true")
    ap.add_argument("--skip-qdrant", action="store_true")
    ap.add_argument("--skip-neo4j", action="store_true")
    ap.add_argument("--allow-doc-drift", type=int, default=0, help="Allowed absolute doc count drift")
    ap.add_argument("--allow-chunk-drift", type=int, default=0, help="Allowed absolute chunk count drift")
    ap.add_argument(
        "--expected-chunks-source",
        choices=["auto", "routerkit_estimate", "es_doc_chunk_sum", "es_chunk_count"],
        default="auto",
        help=(
            "How expected chunk count is derived. "
            "'auto' prefers Elasticsearch doc chunk_count sum, then ES chunk index count, then RouterKit estimate."
        ),
    )
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def get_tokenizer(model_name: str) -> Any:
    if AutoTokenizer is None:
        raise RuntimeError(
            "Token chunk verification requested but transformers is unavailable. "
            "Install transformers or use --chunk-mode chars."
        )
    key = str(model_name).strip() or DEFAULT_TOKENIZER_MODEL
    tok = _TOKENIZER_CACHE.get(key)
    if tok is None:
        tok = AutoTokenizer.from_pretrained(key, trust_remote_code=True, use_fast=True)
        _TOKENIZER_CACHE[key] = tok
    return tok


def extract_sample_text(obj: Dict[str, Any]) -> str:
    sample = obj.get("sample") if isinstance(obj.get("sample"), dict) else {}
    facets = obj.get("facets") if isinstance(obj.get("facets"), dict) else {}
    path = str(obj.get("path", "")).strip()
    sample_text = str(sample.get("text", "") or "").strip()
    if sample_text:
        return sample_text
    fallback = " ".join(
        [
            path,
            str(facets.get("doc_type", "")),
            str(facets.get("artifact_type", "")),
            " ".join(str(x) for x in facets.get("tags", []) if str(x).strip()),
            " ".join(str(x) for x in facets.get("entities_projects", []) if str(x).strip()),
        ]
    ).strip()
    return fallback


def load_docs(path: Path) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            rel_path = str(obj.get("path", "")).strip()
            if not rel_path:
                continue
            docs.append(
                {
                    "path": rel_path,
                    "text": extract_sample_text(obj),
                }
            )
    return docs


def count_chunks(
    text: str,
    *,
    chunk_mode: str,
    chunk_chars: int,
    chunk_overlap: int,
    chunk_tokens: int,
    chunk_overlap_tokens: int,
    tokenizer: Optional[Any],
) -> int:
    clean = str(text or "").strip()
    if not clean:
        return 1
    if chunk_mode == "tokens":
        if tokenizer is None:
            raise RuntimeError("Token mode requires tokenizer")
        token_ids = tokenizer.encode(clean, add_special_tokens=False)
        if not token_ids:
            return 1
        if chunk_tokens <= 0:
            return 1
        overlap = max(0, min(chunk_overlap_tokens, max(0, chunk_tokens - 1)))
        step = max(1, chunk_tokens - overlap)
        total = 0
        start = 0
        n = len(token_ids)
        while start < n:
            end = min(n, start + chunk_tokens)
            total += 1
            if end >= n:
                break
            start += step
        return total

    if chunk_chars <= 0:
        return 1
    overlap = max(0, min(chunk_overlap, max(0, chunk_chars - 1)))
    step = max(1, chunk_chars - overlap)
    total = 0
    start = 0
    n = len(clean)
    while start < n:
        end = min(n, start + chunk_chars)
        total += 1
        if end >= n:
            break
        start += step
    return total


def expected_counts(
    docs: List[Dict[str, Any]],
    *,
    chunk_mode: str,
    chunk_chars: int,
    chunk_overlap: int,
    chunk_tokens: int,
    chunk_overlap_tokens: int,
    tokenizer: Optional[Any],
) -> Dict[str, int]:
    chunk_total = 0
    for doc in docs:
        chunk_total += count_chunks(
            str(doc.get("text", "")),
            chunk_mode=chunk_mode,
            chunk_chars=chunk_chars,
            chunk_overlap=chunk_overlap,
            chunk_tokens=chunk_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            tokenizer=tokenizer,
        )
    return {"documents": len(docs), "chunks": int(chunk_total)}


def es_counts(url: str, doc_index: str, chunk_index: str) -> Dict[str, int]:
    from elasticsearch import Elasticsearch

    es = Elasticsearch(url)
    d = es.count(index=doc_index).get("count", 0)
    c = es.count(index=chunk_index).get("count", 0)
    agg = es.search(
        index=doc_index,
        size=0,
        aggs={"chunk_sum": {"sum": {"field": "chunk_count"}}},
    )
    agg_body: Dict[str, Any]
    if isinstance(agg, dict):
        agg_body = agg
    elif hasattr(agg, "body") and isinstance(getattr(agg, "body"), dict):
        agg_body = getattr(agg, "body")
    else:
        agg_body = {}
    chunk_sum = agg_body.get("aggregations", {}).get("chunk_sum", {}).get("value", 0)
    return {
        "documents": int(d),
        "chunks": int(c),
        "doc_chunk_sum": int(round(float(chunk_sum or 0))),
    }


def qdrant_counts(url: str, collection: str) -> Dict[str, int]:
    from qdrant_client import QdrantClient

    client = QdrantClient(url=url, timeout=30.0)
    info = client.get_collection(collection_name=collection)
    points = getattr(info, "points_count", None)
    if points is None:
        # Compatibility fallback for older response shapes.
        points = getattr(getattr(info, "result", object()), "points_count", 0)
    return {"chunks": int(points or 0)}


def neo4j_counts(uri: str, user: str, password: str) -> Dict[str, int]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        d = session.run("MATCH (d:Document) RETURN count(d) AS n").single()
        c = session.run("MATCH (c:Chunk) RETURN count(c) AS n").single()
    driver.close()
    return {
        "documents": int(d["n"]) if d else 0,
        "chunks": int(c["n"]) if c else 0,
    }


def compare(expected: Dict[str, int], actual: Dict[str, Dict[str, int]], allow_doc: int, allow_chunk: int) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    for backend, counts in actual.items():
        if "documents" in counts:
            drift_doc = abs(int(counts["documents"]) - int(expected["documents"]))
            checks.append(
                {
                    "backend": backend,
                    "kind": "documents",
                    "expected": int(expected["documents"]),
                    "actual": int(counts["documents"]),
                    "drift": int(drift_doc),
                    "allowed_drift": int(allow_doc),
                    "pass": bool(drift_doc <= allow_doc),
                }
            )
        if "chunks" in counts:
            drift_chunk = abs(int(counts["chunks"]) - int(expected["chunks"]))
            checks.append(
                {
                    "backend": backend,
                    "kind": "chunks",
                    "expected": int(expected["chunks"]),
                    "actual": int(counts["chunks"]),
                    "drift": int(drift_chunk),
                    "allowed_drift": int(allow_chunk),
                    "pass": bool(drift_chunk <= allow_chunk),
                }
            )

    ok = all(bool(row["pass"]) for row in checks)
    return {"pass": ok, "checks": checks}


def pick_expected_chunks(
    source: str,
    *,
    routerkit_chunk_estimate: int,
    actual: Dict[str, Dict[str, int]],
) -> Dict[str, Any]:
    mode = str(source or "auto").strip().lower()
    es_counts_row = actual.get("elasticsearch", {}) if isinstance(actual.get("elasticsearch"), dict) else {}
    es_doc_chunk_sum = int(es_counts_row.get("doc_chunk_sum", 0) or 0)
    es_chunk_count = int(es_counts_row.get("chunks", 0) or 0)

    if mode == "routerkit_estimate":
        return {"chunks": int(routerkit_chunk_estimate), "source": "routerkit_estimate"}
    if mode == "es_doc_chunk_sum":
        if es_doc_chunk_sum <= 0:
            raise RuntimeError("expected-chunks-source=es_doc_chunk_sum requires Elasticsearch doc chunk_count aggregation")
        return {"chunks": int(es_doc_chunk_sum), "source": "es_doc_chunk_sum"}
    if mode == "es_chunk_count":
        if es_chunk_count <= 0:
            raise RuntimeError("expected-chunks-source=es_chunk_count requires Elasticsearch chunk index count")
        return {"chunks": int(es_chunk_count), "source": "es_chunk_count"}
    if mode != "auto":
        raise RuntimeError(f"Unsupported expected chunk source: {mode}")

    # Auto mode: trust ingest-produced metadata first, then backend chunk cardinality,
    # then fallback to RouterKit sample-text estimate.
    if es_doc_chunk_sum > 0:
        return {"chunks": int(es_doc_chunk_sum), "source": "es_doc_chunk_sum"}
    if es_chunk_count > 0:
        return {"chunks": int(es_chunk_count), "source": "es_chunk_count"}
    return {"chunks": int(routerkit_chunk_estimate), "source": "routerkit_estimate"}


def backend_consistency_checks(actual: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    es_counts_row = actual.get("elasticsearch", {}) if isinstance(actual.get("elasticsearch"), dict) else {}
    if "doc_chunk_sum" in es_counts_row and "chunks" in es_counts_row:
        left = int(es_counts_row.get("doc_chunk_sum", 0) or 0)
        right = int(es_counts_row.get("chunks", 0) or 0)
        drift = abs(left - right)
        checks.append(
            {
                "backend": "elasticsearch",
                "kind": "doc_chunk_sum_vs_chunk_index",
                "expected": left,
                "actual": right,
                "drift": drift,
                "allowed_drift": 0,
                "pass": bool(drift == 0),
            }
        )
    return checks


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    docs_path = Path(args.routerkit_documents).expanduser().resolve()
    if not docs_path.exists():
        raise FileNotFoundError(f"RouterKit documents not found: {docs_path}")

    docs = load_docs(docs_path)
    tokenizer = None
    chunk_mode = str(args.chunk_mode or "chars").strip().lower()
    if chunk_mode == "tokens":
        tokenizer = get_tokenizer(args.tokenizer_model)

    routerkit_expected = expected_counts(
        docs,
        chunk_mode=chunk_mode,
        chunk_chars=int(args.chunk_chars),
        chunk_overlap=int(args.chunk_overlap),
        chunk_tokens=int(args.chunk_tokens),
        chunk_overlap_tokens=int(args.chunk_overlap_tokens),
        tokenizer=tokenizer,
    )

    actual: Dict[str, Dict[str, int]] = {}
    errors: Dict[str, str] = {}

    if not args.skip_es:
        try:
            actual["elasticsearch"] = es_counts(args.es_url, args.es_doc_index, args.es_chunk_index)
        except Exception as exc:
            errors["elasticsearch"] = str(exc)

    if not args.skip_qdrant:
        try:
            actual["qdrant"] = qdrant_counts(args.qdrant_url, args.qdrant_collection)
        except Exception as exc:
            errors["qdrant"] = str(exc)

    if not args.skip_neo4j:
        try:
            actual["neo4j"] = neo4j_counts(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
        except Exception as exc:
            errors["neo4j"] = str(exc)

    expected = {
        "documents": int(routerkit_expected["documents"]),
        "chunks": int(routerkit_expected["chunks"]),
    }
    expected_chunks_source = "routerkit_estimate"
    try:
        picked = pick_expected_chunks(
            str(args.expected_chunks_source),
            routerkit_chunk_estimate=int(routerkit_expected["chunks"]),
            actual=actual,
        )
        expected["chunks"] = int(picked["chunks"])
        expected_chunks_source = str(picked["source"])
    except Exception as exc:
        errors["expected_chunks"] = str(exc)

    result = compare(expected=expected, actual=actual, allow_doc=int(args.allow_doc_drift), allow_chunk=int(args.allow_chunk_drift))
    consistency = backend_consistency_checks(actual)
    if consistency:
        result["checks"].extend(consistency)
        result["pass"] = bool(result["pass"] and all(bool(row.get("pass")) for row in consistency))
    report = {
        "source_documents": str(docs_path),
        "chunking": {
            "chunk_mode": chunk_mode,
            "chunk_chars": int(args.chunk_chars),
            "chunk_overlap": int(args.chunk_overlap),
            "chunk_tokens": int(args.chunk_tokens),
            "chunk_overlap_tokens": int(args.chunk_overlap_tokens),
            "tokenizer_model": args.tokenizer_model if chunk_mode == "tokens" else "",
        },
        "expected_source": {
            "documents": "routerkit_documents_count",
            "chunks": expected_chunks_source,
        },
        "routerkit_estimate": routerkit_expected,
        "expected": expected,
        "actual": actual,
        "errors": errors,
        **result,
    }
    print(json.dumps(report, indent=2))

    if errors:
        LOGGER.error("One or more backends were unreachable; verification failed")
        return 2
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
