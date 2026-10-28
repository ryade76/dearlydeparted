#!/usr/bin/env python3
"""
Smoke-test retrieval across Elasticsearch, Qdrant, and Neo4j.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional

import requests
from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from neo4j import GraphDatabase

LOGGER = logging.getLogger("smoke_test_queries")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Smoke test ES/Qdrant/Neo4j retrieval")
    ap.add_argument("--query", default="network security patch procedure")
    ap.add_argument("--domain", default="", help="Optional semantic domain filter")
    ap.add_argument("--top-k", type=int, default=5)
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
    ap.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="change_me_local")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


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

        resp = requests.post(embed_url, json=payload, timeout=int(timeout_sec))
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


def run_es(es: Elasticsearch, index: str, query: str, domain: str, top_k: int) -> List[Dict[str, Any]]:
    must: List[Any] = [{"match": {"text": {"query": query}}}]
    filt: List[Any] = [{"term": {"semantic_decision": "routed"}}]
    if domain:
        filt.append({"term": {"semantic_domain": domain}})

    body = {
        "size": int(top_k),
        "query": {
            "bool": {
                "must": must,
                "filter": filt,
            }
        },
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
                "artifact_type": src.get("artifact_type"),
                "text_preview": str(src.get("text", ""))[:220],
            }
        )
    return results


def run_qdrant(client: QdrantClient, collection: str, vector: List[float], domain: str, top_k: int) -> List[Dict[str, Any]]:
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
                "artifact_type": payload.get("artifact_type"),
                "text_preview": str(payload.get("text", ""))[:220],
            }
        )
    return results


def run_neo4j(uri: str, user: str, password: str, domain: str) -> Dict[str, Any]:
    driver = GraphDatabase.driver(uri, auth=(user, password))
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

        top_entities = session.run(
            """
            MATCH (d:Document)-[:HAS_ENTITY]->(e)
            RETURN labels(e)[0] AS label, e.name AS name, count(*) AS n
            ORDER BY n DESC
            LIMIT 5
            """
        )
        out["top_entities"] = [
            {"label": r["label"], "name": r["name"], "count": int(r["n"])} for r in top_entities
        ]

    driver.close()
    return out


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    vector = embed_query(
        args.embed_url,
        args.embed_model,
        args.query,
        int(args.embed_dim),
        int(args.embed_timeout_sec),
        args.embed_query_instruction,
    )

    es = Elasticsearch(args.es_url)
    es_hits = run_es(es, args.es_chunk_index, args.query, args.domain, int(args.top_k))

    qdr = QdrantClient(url=args.qdrant_url, timeout=float(args.qdrant_timeout_sec))
    qdr_hits = run_qdrant(qdr, args.qdrant_collection, vector, args.domain, int(args.top_k))

    neo = run_neo4j(args.neo4j_uri, args.neo4j_user, args.neo4j_password, args.domain)

    report = {
        "query": args.query,
        "domain_filter": args.domain,
        "top_k": int(args.top_k),
        "elasticsearch_hits": es_hits,
        "qdrant_hits": qdr_hits,
        "neo4j": neo,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
