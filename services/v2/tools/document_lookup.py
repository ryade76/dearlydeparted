"""document_lookup — Retrieve full document metadata + all chunks via direct ES."""

from __future__ import annotations

import logging
from typing import Any, Dict

from services.v2.agent.states import AgentContext, EvidenceItem
from services.v2.tools.base import register_tool

log = logging.getLogger("v2.tools.document_lookup")

# Initialized at startup
_es = None


def init_es_client(es_url: str) -> None:
    global _es
    from elasticsearch import Elasticsearch
    _es = Elasticsearch(es_url)
    log.info("ES client initialized: %s", es_url)


def get_es():
    if _es is None:
        raise RuntimeError("ES client not initialized")
    return _es


def close_es_client() -> None:
    global _es
    if _es:
        _es.close()
        _es = None


@register_tool(
    name="document_lookup",
    description=(
        "Look up a specific document by its path or document ID. Returns full document metadata "
        "and all text chunks from that document. Use this when you need the complete content "
        "of a specific document, or when a citation needs more context."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Document file path (partial match supported)", "default": ""},
            "doc_id": {"type": "string", "description": "Document ID (exact match)", "default": ""},
        },
        "required": [],
    },
)
async def document_lookup(
    ctx: AgentContext,
    path: str = "",
    doc_id: str = "",
) -> Dict[str, Any]:
    if not path and not doc_id:
        raise ValueError("Either 'path' or 'doc_id' must be provided")

    es = get_es()
    result: Dict[str, Any] = {"doc": None, "chunks": [], "graphrag_artifacts": []}

    # 1. Find the document in kb_docs
    if doc_id:
        doc_query = {"query": {"term": {"doc_id": doc_id}}}
    else:
        doc_query = {"query": {"wildcard": {"path": {"value": f"*{path}*"}}}, "size": 1}

    doc_resp = es.search(index="kb_docs", body=doc_query)
    doc_hits = doc_resp.get("hits", {}).get("hits", [])
    if doc_hits:
        doc_source = doc_hits[0]["_source"]
        result["doc"] = doc_source
        doc_id = doc_source.get("doc_id", doc_id)

    if not doc_id and not doc_hits:
        return {"doc": None, "chunks": [], "summary": {"hit_count": 0, "error": "Document not found"}}

    # 2. Get all chunks for this document
    chunk_query = {
        "query": {"term": {"doc_id": doc_id}},
        "sort": [{"chunk_index": "asc"}],
        "size": 200,
    }
    chunk_resp = es.search(index="kb_chunks", body=chunk_query)
    chunks = [h["_source"] for h in chunk_resp.get("hits", {}).get("hits", [])]
    result["chunks"] = chunks

    # Add all chunks as evidence
    for chunk in chunks:
        ctx.evidence.append(EvidenceItem(
            item_id=chunk.get("chunk_id") or "",
            evidence_type="chunk",
            path=chunk.get("path") or "",
            domain=chunk.get("semantic_domain") or "",
            bucket=chunk.get("first_bucket") or "",
            date=chunk.get("mtime_date") or "",
            text=chunk.get("text") or "",
            source="document_lookup",
        ))

    # 3. Get GraphRAG artifacts for this document
    artifact_query = {
        "query": {"term": {"doc_id": doc_id}},
        "size": 50,
    }
    artifact_resp = es.search(index="kb_graphrag_artifacts", body=artifact_query)
    artifacts = [h["_source"] for h in artifact_resp.get("hits", {}).get("hits", [])]
    result["graphrag_artifacts"] = artifacts

    return {
        **result,
        "summary": {
            "hit_count": len(chunks),
            "chunk_count": len(chunks),
            "artifact_count": len(artifacts),
            "path": result["doc"].get("path", "") if result["doc"] else path,
        },
    }
