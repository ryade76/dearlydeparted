#!/usr/bin/env python3
"""
Evaluate retrieval behavior against a JSONL query suite.

Backends:
- query-api (recommended): evaluates fused + GraphRAG mode + rerank path.
- hybrid: evaluates direct ES+Qdrant baseline.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger("eval_retrieval")


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    host = (urlparse(str(url)).hostname or "").lower()
    if host in {"127.0.0.1", "localhost"}:
        with requests.Session() as sess:
            sess.trust_env = False
            return sess.request(method, url, **kwargs)
    return requests.request(method, url, **kwargs)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Evaluate ES+Qdrant retrieval with expected constraints")
    ap.add_argument("--eval-file", default="eval_queries.template.jsonl")
    ap.add_argument("--backend", choices=["query-api", "hybrid"], default="query-api")
    ap.add_argument("--query-api-url", default="http://127.0.0.1:8080/retrieve")
    ap.add_argument("--mode", choices=["basic", "local", "global", "drift"], default="basic")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--es-chunk-index", default="kb_chunks")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--qdrant-collection", default="kb_chunks_vec")
    ap.add_argument("--qdrant-timeout-sec", type=int, default=60)
    ap.add_argument("--embed-url", default="http://127.0.0.1:8001/v1/embeddings")
    ap.add_argument("--embed-model", default="Qwen/Qwen3-Embedding-8B")
    ap.add_argument(
        "--embed-query-instruction",
        default="Given a web search query, retrieve relevant passages that answer the query",
    )
    ap.add_argument("--embed-dim", type=int, default=4096)
    ap.add_argument("--embed-timeout-sec", type=int, default=600)
    ap.add_argument("--output-file", default="", help="Optional path to write JSON report")
    ap.add_argument("--min-pass-rate", type=float, default=1.0)
    ap.add_argument("--fail-on-gates", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def load_queries(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("query"):
                rows.append(obj)
    return rows


def embed_query(
    embed_url: str,
    model: str,
    text: str,
    dim: int,
    timeout_sec: int,
    query_instruction: str,
) -> List[float]:
    embed_text = str(text or "").strip()
    if str(query_instruction or "").strip():
        embed_text = f"Instruct: {str(query_instruction).strip()}\nQuery: {embed_text}"

    include_dim_default = int(dim) > 0
    variant_order = [(False, False), (False, include_dim_default), (True, False), (True, include_dim_default)]

    seen = set()
    errors: List[str] = []
    for include_task, include_dim in variant_order:
        key = (include_task, include_dim)
        if key in seen:
            continue
        seen.add(key)

        payload = {
            "model": model,
            "input": [embed_text],
            "encoding_format": "float",
        }
        if include_task:
            payload["task"] = "query"
        if include_dim:
            payload["dimensions"] = int(dim)

        resp = _request("POST", embed_url, json=payload, timeout=int(timeout_sec))
        if resp.status_code >= 300:
            errors.append(f"{resp.status_code}: {resp.text[:200]}")
            continue

        obj = resp.json()
        data = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(data, list) or not data:
            raise RuntimeError("Embed response missing data")
        emb = data[0].get("embedding")
        if not isinstance(emb, list):
            raise RuntimeError("Embed response missing embedding")
        return [float(x) for x in emb]

    raise RuntimeError(f"Embed failed after compatibility retries: {'; '.join(errors[-4:])}")


def run_es(es: Any, index: str, query: str, domain: str, top_k: int) -> List[Dict[str, Any]]:
    must: List[Any] = [{"match": {"text": {"query": query}}}]
    filt: List[Any] = [{"term": {"semantic_decision": "routed"}}]
    if domain:
        filt.append({"term": {"semantic_domain": domain}})

    body = {
        "size": int(top_k),
        "query": {"bool": {"must": must, "filter": filt}},
    }
    out = es.search(index=index, body=body)
    hits = out.get("hits", {}).get("hits", [])
    results = []
    for h in hits:
        src = h.get("_source", {})
        results.append(
            {
                "score": h.get("_score"),
                "chunk_id": src.get("chunk_id"),
                "path": src.get("path"),
                "semantic_domain": src.get("semantic_domain"),
                "first_bucket": src.get("first_bucket"),
                "sub_bucket": src.get("sub_bucket"),
            }
        )
    return results


def run_qdrant(client: Any, collection: str, vector: List[float], domain: str, top_k: int) -> List[Dict[str, Any]]:
    from qdrant_client.http import models as qm

    must: List[qm.Condition] = [
        qm.FieldCondition(key="semantic_decision", match=qm.MatchValue(value="routed")),
    ]
    if domain:
        must.append(qm.FieldCondition(key="semantic_domain", match=qm.MatchValue(value=domain)))

    filt = qm.Filter(must=must)
    points: Any
    if hasattr(client, "query_points"):
        out = client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )
        points = out.points if hasattr(out, "points") else out
    else:
        points = client.search(
            collection_name=collection,
            query_vector=vector,
            query_filter=filt,
            limit=int(top_k),
            with_payload=True,
        )

    results = []
    for p in points:
        payload = p.payload or {}
        results.append(
            {
                "score": p.score,
                "chunk_id": payload.get("chunk_id"),
                "path": payload.get("path"),
                "semantic_domain": payload.get("semantic_domain"),
                "first_bucket": payload.get("first_bucket"),
                "sub_bucket": payload.get("sub_bucket"),
            }
        )
    return results


def run_query_api(query_api_url: str, query: str, domain: str, mode: str, top_k: int) -> List[Dict[str, Any]]:
    payload = {
        "query": query,
        "domain": domain,
        "mode": mode,
        "top_k": int(top_k),
        "include_graph": False,
    }
    resp = _request("POST", query_api_url, json=payload, timeout=120)
    if resp.status_code >= 300:
        raise RuntimeError(f"query-api failed: {resp.status_code} {resp.text[:280]}")
    obj = resp.json()
    if not isinstance(obj, dict):
        raise RuntimeError("query-api response is not a JSON object")
    hits = obj.get("hits")
    if not isinstance(hits, list):
        return []
    return [h for h in hits if isinstance(h, dict)]


def normalize_expected(exp: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "semantic_domain": str(exp.get("semantic_domain", "") or ""),
        "first_buckets": [str(x) for x in exp.get("first_buckets", []) if str(x).strip()],
        "sub_buckets": [str(x) for x in exp.get("sub_buckets", []) if str(x).strip()],
        "any_of_paths": [str(x) for x in exp.get("any_of_paths", []) if str(x).strip()],
    }


def evaluate_hits(hits: List[Dict[str, Any]], expected: Dict[str, Any]) -> Dict[str, bool]:
    domains = {str(h.get("semantic_domain", "")) for h in hits}
    first_buckets = {str(h.get("first_bucket", "")) for h in hits}
    sub_buckets = {str(h.get("sub_bucket", "")) for h in hits}
    paths = [str(h.get("path", "")) for h in hits]

    domain_ok = (not expected["semantic_domain"]) or (expected["semantic_domain"] in domains)
    fb_ok = (not expected["first_buckets"]) or any(x in first_buckets for x in expected["first_buckets"])
    sb_ok = (not expected["sub_buckets"]) or any(x in sub_buckets for x in expected["sub_buckets"])
    path_ok = (not expected["any_of_paths"]) or any(
        any(pat in path for pat in expected["any_of_paths"]) for path in paths
    )
    return {
        "domain_ok": bool(domain_ok),
        "first_bucket_ok": bool(fb_ok),
        "sub_bucket_ok": bool(sb_ok),
        "path_ok": bool(path_ok),
    }


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    eval_path = Path(args.eval_file).expanduser().resolve()
    if not eval_path.exists():
        raise FileNotFoundError(f"Eval file not found: {eval_path}")

    rows = load_queries(eval_path)
    es = None
    qdr = None
    if args.backend == "hybrid":
        from elasticsearch import Elasticsearch
        from qdrant_client import QdrantClient

        es = Elasticsearch(args.es_url)
        qdr = QdrantClient(url=args.qdrant_url, timeout=float(args.qdrant_timeout_sec))

    results: List[Dict[str, Any]] = []
    for row in rows:
        qid = str(row.get("id", ""))
        query = str(row.get("query", ""))
        expected = normalize_expected(row.get("expected", {}) if isinstance(row.get("expected"), dict) else {})

        es_hits: List[Dict[str, Any]] = []
        qdr_hits: List[Dict[str, Any]] = []
        api_hits: List[Dict[str, Any]] = []
        if args.backend == "query-api":
            api_hits = run_query_api(
                args.query_api_url,
                query,
                expected["semantic_domain"],
                args.mode,
                int(args.top_k),
            )
            combined = api_hits
        else:
            if es is None or qdr is None:
                raise RuntimeError("hybrid backend selected but ES/Qdrant clients are uninitialized")
            vector = embed_query(
                args.embed_url,
                args.embed_model,
                query,
                dim=int(args.embed_dim),
                timeout_sec=int(args.embed_timeout_sec),
                query_instruction=args.embed_query_instruction,
            )
            es_hits = run_es(es, args.es_chunk_index, query, expected["semantic_domain"], int(args.top_k))
            qdr_hits = run_qdrant(qdr, args.qdrant_collection, vector, expected["semantic_domain"], int(args.top_k))
            combined = es_hits + qdr_hits

        checks = evaluate_hits(combined, expected)
        results.append(
            {
                "id": qid,
                "query": query,
                "expected": expected,
                "checks": checks,
                "pass": bool(all(checks.values())),
                "es_hit_count": len(es_hits),
                "qdrant_hit_count": len(qdr_hits),
                "query_api_hit_count": len(api_hits),
            }
        )

    total = len(results)
    passed = sum(1 for r in results if bool(r.get("pass")))
    summary = {
        "eval_file": str(eval_path),
        "backend": args.backend,
        "query_api_url": args.query_api_url if args.backend == "query-api" else "",
        "mode": args.mode,
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
    }
    summary["gates"] = {
        "configured": {"min_pass_rate": float(args.min_pass_rate)},
        "results": {"pass_rate": bool(summary["pass_rate"] >= float(args.min_pass_rate))},
        "pass": bool(summary["pass_rate"] >= float(args.min_pass_rate)),
    }
    report = {"summary": summary, "results": results}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if str(args.output_file or "").strip():
        out_path = Path(args.output_file).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        LOGGER.info("Wrote retrieval eval report: %s", out_path)
    if args.fail_on_gates:
        return 0 if bool(summary["gates"]["pass"]) else 1
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
