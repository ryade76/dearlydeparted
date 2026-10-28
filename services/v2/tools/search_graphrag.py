"""search_graphrag_artifacts — Search GraphRAG artifacts via query_api /retrieve."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.v2.agent.states import AgentContext, EvidenceItem
from services.v2.tools.base import register_tool


@register_tool(
    name="search_graphrag_artifacts",
    description=(
        "Search GraphRAG knowledge artifacts: entity descriptions, relationships, claims, "
        "community reports, taxonomy cards, and domain profiles. Use this for understanding "
        "entities, their relationships, and high-level summaries of topics."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "artifact_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Filter to specific artifact types. Options: entity_description, relationship, "
                    "claim, community_report, taxonomy_first_bucket, taxonomy_sub_bucket, "
                    "taxonomy_bucket_card, taxonomy_domain_profile, text_unit"
                ),
                "default": [],
            },
            "retrieval_mode": {
                "type": "string",
                "description": "Retrieval scope: 'local' for entities/relationships/claims, 'global' for community reports/domain profiles, 'drift' for both",
                "enum": ["local", "global", "drift"],
                "default": "local",
            },
            "domain": {"type": "string", "description": "Optional domain filter", "default": ""},
            "generation_mode": {
                "type": "string",
                "description": "Filter by generation mode: both, llm, deterministic",
                "default": "both",
            },
            "top_k": {"type": "integer", "description": "Number of results", "default": 30},
        },
        "required": ["query"],
    },
)
async def search_graphrag_artifacts(
    ctx: AgentContext,
    query: str,
    artifact_types: Optional[List[str]] = None,
    retrieval_mode: str = "local",
    domain: str = "",
    generation_mode: str = "both",
    top_k: int = 30,
) -> Dict[str, Any]:
    from services.v2.tools._http import query_retrieve

    resp = await query_retrieve(
        query=query,
        mode=retrieval_mode,
        domain=domain,
        top_k=top_k,
        decompose=False,
        graphrag_artifact_types=artifact_types or [],
        graphrag_generation_mode=generation_mode,
    )

    hits = resp.get("hits", [])
    for hit in hits:
        item = EvidenceItem(
            item_id=hit.get("artifact_id") or hit.get("chunk_id") or "",
            evidence_type=hit.get("artifact_type") or "graphrag",
            path=hit.get("path") or "",
            domain=hit.get("semantic_domain") or "",
            bucket=hit.get("first_bucket") or "",
            date=hit.get("mtime_date") or "",
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
            "artifact_types_found": list({h.get("artifact_type", "") for h in hits}),
            "top_snippets": [h.get("text", "")[:150] for h in hits[:3]],
            "mode": retrieval_mode,
        },
    }
