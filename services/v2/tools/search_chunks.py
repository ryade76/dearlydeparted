"""search_chunks — Hybrid BM25+dense chunk search via query_api /retrieve."""

from __future__ import annotations

from typing import Any, Dict

from services.v2.agent.states import AgentContext, EvidenceItem
from services.v2.tools.base import register_tool


@register_tool(
    name="search_chunks",
    description=(
        "Search for text chunks and knowledge artifacts across the captured knowledge base using "
        "hybrid BM25 + dense retrieval with RRF fusion and reranking. Returns relevant text "
        "passages, entity descriptions, relationships, and other artifacts from documents. "
        "Use this for factual lookups, finding specific information, or gathering evidence."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "mode": {
                "type": "string",
                "description": "Retrieval mode: 'auto' lets the system choose (recommended), 'basic' for chunks only, 'local' adds entities/relationships, 'global' adds community reports",
                "enum": ["auto", "basic", "local", "global", "drift"],
                "default": "auto",
            },
            "domain": {"type": "string", "description": "Optional domain filter (e.g., it_ops, security, networking)", "default": ""},
            "top_k": {"type": "integer", "description": "Number of results to return", "default": 40},
            "date_from": {"type": "string", "description": "Optional start date filter (ISO format)", "default": ""},
            "date_to": {"type": "string", "description": "Optional end date filter (ISO format)", "default": ""},
            "source_path_hint": {"type": "string", "description": "Optional path hint to boost specific documents", "default": ""},
        },
        "required": ["query"],
    },
)
async def search_chunks(
    ctx: AgentContext,
    query: str,
    mode: str = "auto",
    domain: str = "",
    top_k: int = 40,
    date_from: str = "",
    date_to: str = "",
    source_path_hint: str = "",
) -> Dict[str, Any]:
    from services.v2.tools._http import query_retrieve

    resp = await query_retrieve(
        query=query,
        mode=mode,
        domain=domain,
        top_k=top_k,
        decompose=True,
        date_from=date_from,
        date_to=date_to,
        source_path_hint=source_path_hint,
    )

    # GraphRAG/taxonomy types that need special routing in synthesize.py
    _STRUCTURED_TYPES = {
        "entity_description", "relationship", "claim", "community_report",
        "taxonomy_first_bucket", "taxonomy_sub_bucket", "taxonomy_bucket_card",
        "taxonomy_domain_profile",
    }

    hits = resp.get("hits", [])
    for hit in hits:
        raw_type = hit.get("artifact_type") or ""
        # Keep structured GraphRAG/taxonomy types; treat everything else as text chunk
        etype = raw_type if raw_type in _STRUCTURED_TYPES else "chunk"
        item = EvidenceItem(
            item_id=hit.get("chunk_id") or hit.get("artifact_id") or hit.get("doc_id") or "",
            evidence_type=etype,
            path=hit.get("path") or "",
            domain=hit.get("semantic_domain") or "",
            bucket=hit.get("first_bucket") or "",
            date=hit.get("mtime_date") or hit.get("effective_date") or "",
            text=hit.get("text") or "",
            score=float(hit.get("score") or 0),
            source=hit.get("source") or "",
            metadata={k: v for k, v in hit.items() if k not in ("text",)},
        )
        ctx.evidence.append(item)

    return {
        "hits": hits,
        "summary": {
            "hit_count": len(hits),
            "top_sources": [h.get("path", "") for h in hits[:5]],
            "top_snippets": [h.get("text", "")[:150] for h in hits[:3]],
            "mode": mode,
        },
    }
