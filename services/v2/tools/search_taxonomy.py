"""search_taxonomy — Navigate taxonomy hierarchy using in-memory index."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.v2.agent.states import AgentContext, EvidenceItem
from services.v2.tools.base import register_tool

log = logging.getLogger("v2.tools.search_taxonomy")

# Initialized at startup via load_taxonomy_router + build_taxonomy_index
_taxonomy_router: Dict[str, Any] = {}
_taxonomy_index: Dict[str, Any] = {}


def init_taxonomy(router_json_path: str) -> None:
    global _taxonomy_router, _taxonomy_index
    from services.query_api import load_taxonomy_router, build_taxonomy_index
    _taxonomy_router = load_taxonomy_router(router_json_path)
    _taxonomy_index = build_taxonomy_index(_taxonomy_router)
    log.info("Taxonomy loaded: %d first buckets", len(_taxonomy_index.get("first", {})))


@register_tool(
    name="search_taxonomy",
    description=(
        "Search and navigate the document taxonomy hierarchy. Find domains, first buckets, "
        "sub-buckets, and their descriptions. Use this to understand how documents are "
        "classified and to discover what topics are covered."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for taxonomy matching"},
            "include_bucket_cards": {"type": "boolean", "description": "Include detailed bucket card descriptions", "default": True},
            "include_domain_profiles": {"type": "boolean", "description": "Include domain-level profiles", "default": False},
        },
        "required": ["query"],
    },
)
async def search_taxonomy(
    ctx: AgentContext,
    query: str,
    include_bucket_cards: bool = True,
    include_domain_profiles: bool = False,
) -> Dict[str, Any]:
    from services.query_api import lexical_taxonomy_candidates, lexical_domain_candidates

    results: Dict[str, Any] = {"taxonomy_matches": [], "domain_matches": []}

    # Taxonomy bucket matches
    if _taxonomy_index:
        tax_candidates = lexical_taxonomy_candidates(query, _taxonomy_index)
        results["taxonomy_matches"] = tax_candidates[:20]
        for candidate in results["taxonomy_matches"]:
            ctx.evidence.append(EvidenceItem(
                item_id=candidate.get("bucket_id") or f"tax_{candidate.get('first_bucket') or ''}",
                evidence_type="taxonomy_bucket_card",
                path="",
                domain=candidate.get("domain") or "",
                bucket=candidate.get("first_bucket") or "",
                text=candidate.get("description") or candidate.get("label") or "",
                score=float(candidate.get("score") or 0),
                source="search_taxonomy",
            ))

    # Domain matches
    if _taxonomy_index:
        domain_candidates = lexical_domain_candidates(query, _taxonomy_index)
        results["domain_matches"] = domain_candidates[:10]
        for candidate in results["domain_matches"]:
            ctx.evidence.append(EvidenceItem(
                item_id=f"dom_{candidate.get('domain') or ''}",
                evidence_type="taxonomy_domain_profile",
                path="",
                domain=candidate.get("domain") or "",
                bucket="",
                text=candidate.get("description") or candidate.get("label") or "",
                score=float(candidate.get("score") or 0),
                source="search_taxonomy",
            ))

    # Full router structure for context
    if include_bucket_cards and _taxonomy_router:
        first_buckets = _taxonomy_router.get("first_buckets", {})
        matched_buckets = set()
        for match in results["taxonomy_matches"]:
            fb = match.get("first_bucket", "")
            if fb:
                matched_buckets.add(fb)

        bucket_cards = []
        for fb_id in matched_buckets:
            fb_data = first_buckets.get(fb_id, {})
            if fb_data:
                bucket_cards.append({
                    "first_bucket": fb_id,
                    "label": fb_data.get("label", ""),
                    "description": fb_data.get("description", ""),
                    "sub_buckets": list(fb_data.get("sub_buckets", {}).keys()),
                })
        results["bucket_cards"] = bucket_cards

    return {
        **results,
        "summary": {
            "hit_count": len(results["taxonomy_matches"]) + len(results["domain_matches"]),
            "matched_buckets": len(results.get("bucket_cards", [])),
        },
    }
