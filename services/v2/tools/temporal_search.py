"""temporal_search — Time-filtered retrieval via query_api /retrieve."""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.v2.agent.states import AgentContext, EvidenceItem
from services.v2.tools.base import register_tool


@register_tool(
    name="temporal_search",
    description=(
        "Search for documents and chunks filtered by time range. Use this when the query "
        "involves dates, time periods, or when recent/historical information is specifically needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "date_from": {"type": "string", "description": "Start date (ISO format, e.g., 2024-01-01)"},
            "date_to": {"type": "string", "description": "End date (ISO format, e.g., 2024-12-31)"},
            "temporal_basis": {
                "type": "string",
                "description": "Date field to filter on: 'effective' (content date) or 'mtime' (file modification date)",
                "enum": ["effective", "mtime"],
                "default": "effective",
            },
            "domain": {"type": "string", "description": "Optional domain filter", "default": ""},
            "top_k": {"type": "integer", "description": "Number of results", "default": 40},
            "temporal_boost": {
                "type": "string",
                "description": "Temporal relevance boosting: 'recency' boosts recent documents, 'none' disables temporal boosting, '' for default",
                "enum": ["", "recency", "none"],
                "default": "",
            },
        },
        "required": ["query", "date_from", "date_to"],
    },
)
async def temporal_search(
    ctx: AgentContext,
    query: str,
    date_from: str,
    date_to: str,
    temporal_basis: str = "effective",
    domain: str = "",
    top_k: int = 40,
    temporal_boost: str = "",
) -> Dict[str, Any]:
    from services.v2.tools._http import query_retrieve

    resp = await query_retrieve(
        query=query,
        mode="auto",
        domain=domain,
        top_k=top_k,
        decompose=True,
        date_from=date_from,
        date_to=date_to,
        temporal_basis=temporal_basis,
        temporal_boost=temporal_boost,
    )

    hits = resp.get("hits", [])
    for hit in hits:
        item = EvidenceItem(
            item_id=hit.get("chunk_id") or hit.get("artifact_id") or "",
            evidence_type=hit.get("artifact_type") or "chunk",
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
            "date_range": f"{date_from} to {date_to}",
            "temporal_basis": temporal_basis,
            "top_snippets": [h.get("text", "")[:150] for h in hits[:3]],
        },
    }
