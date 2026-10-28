#!/usr/bin/env python3
"""
Identify weak extraction documents from Elasticsearch and optionally build a
routerkit documents.jsonl subset for targeted re-ingest.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Set

from elasticsearch import Elasticsearch

LOGGER = logging.getLogger("identify_weak_extractions")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Identify weak extraction docs from kb_docs")
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--es-doc-index", default="kb_docs")
    ap.add_argument("--score-threshold", type=int, default=55)
    ap.add_argument("--size", type=int, default=5000, help="Max docs to scan per request (<=10000)")
    ap.add_argument("--output-jsonl", required=True, help="Where to write weak doc rows")
    ap.add_argument("--routerkit-documents", default="", help="Optional source documents.jsonl path")
    ap.add_argument(
        "--write-routerkit-subset",
        default="",
        help="Optional path to write filtered documents.jsonl subset for targeted re-ingest",
    )
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def fetch_weak_docs(es: Elasticsearch, index: str, threshold: int, size: int) -> List[Dict[str, Any]]:
    query = {
        "size": max(1, min(10000, int(size))),
        "_source": [
            "doc_id",
            "path",
            "semantic_domain",
            "first_bucket",
            "sub_bucket",
            "content_source",
            "chunk_count",
            "extraction_quality_score",
            "extraction_quality_flags",
            "extraction_char_count",
            "extraction_token_count",
        ],
        "query": {
            "bool": {
                "should": [
                    {"range": {"extraction_quality_score": {"lte": int(threshold)}}},
                    {"bool": {"must_not": [{"exists": {"field": "extraction_quality_score"}}]}},
                ],
                "minimum_should_match": 1,
            }
        },
    }
    resp = es.search(index=index, body=query)
    hits = resp.get("hits", {}).get("hits", [])
    rows: List[Dict[str, Any]] = []
    for h in hits:
        src = h.get("_source", {}) if isinstance(h.get("_source"), dict) else {}
        rows.append(
            {
                "doc_id": str(src.get("doc_id", "")),
                "path": str(src.get("path", "")),
                "semantic_domain": str(src.get("semantic_domain", "")),
                "first_bucket": str(src.get("first_bucket", "")),
                "sub_bucket": str(src.get("sub_bucket", "")),
                "content_source": str(src.get("content_source", "")),
                "chunk_count": int(src.get("chunk_count", 0) or 0),
                "extraction_quality_score": int(src.get("extraction_quality_score", -1) or -1),
                "extraction_quality_flags": [str(x) for x in (src.get("extraction_quality_flags") or []) if str(x).strip()],
                "extraction_char_count": int(src.get("extraction_char_count", 0) or 0),
                "extraction_token_count": int(src.get("extraction_token_count", 0) or 0),
            }
        )
    return rows


def load_routerkit_rows(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict) and str(obj.get("path", "")).strip():
                out.append(obj)
    return out


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    es = Elasticsearch(str(args.es_url).strip())
    weak_rows = fetch_weak_docs(es, str(args.es_doc_index).strip(), int(args.score_threshold), int(args.size))

    out_path = Path(args.output_jsonl).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in weak_rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    LOGGER.info("Wrote weak extraction report: %s (rows=%d)", out_path, len(weak_rows))

    subset_path = str(args.write_routerkit_subset or "").strip()
    routerkit_path = str(args.routerkit_documents or "").strip()
    if subset_path and routerkit_path:
        rk_path = Path(routerkit_path).expanduser().resolve()
        if not rk_path.exists():
            raise FileNotFoundError(f"routerkit documents not found: {rk_path}")
        rows = load_routerkit_rows(rk_path)
        weak_paths: Set[str] = {str(r.get("path", "")).strip() for r in weak_rows if str(r.get("path", "")).strip()}
        subset = [r for r in rows if str(r.get("path", "")).strip() in weak_paths]
        out_subset = Path(subset_path).expanduser().resolve()
        out_subset.parent.mkdir(parents=True, exist_ok=True)
        with out_subset.open("w", encoding="utf-8") as f:
            for row in subset:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
        LOGGER.info("Wrote routerkit subset: %s (rows=%d)", out_subset, len(subset))
        LOGGER.info(
            "Suggested re-ingest: .venv/bin/python ingest_worker.py --routerkit-documents %s --content-source filesystem --corpus-root .",
            out_subset,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
