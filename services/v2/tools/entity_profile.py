"""entity_profile — Build complete entity profile from multiple stores."""

from __future__ import annotations

from typing import Any, Dict

from services.v2.agent.states import AgentContext, EvidenceItem
from services.v2.tools.base import register_tool


@register_tool(
    name="entity_profile",
    description=(
        "Build a comprehensive profile for a named entity (person, system, vendor, project, or location). "
        "Combines Neo4j graph relationships with ES entity descriptions and chunk mentions. "
        "Use this when you need detailed information about a specific entity."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity_name": {"type": "string", "description": "The name of the entity to profile"},
            "entity_type": {
                "type": "string",
                "description": "Optional type hint: person, system, vendor, project, location",
                "default": "",
            },
            "include_mentions": {
                "type": "boolean",
                "description": "Include text chunks that mention this entity",
                "default": True,
            },
        },
        "required": ["entity_name"],
    },
)
async def entity_profile(
    ctx: AgentContext,
    entity_name: str,
    entity_type: str = "",
    include_mentions: bool = True,
) -> Dict[str, Any]:
    from services.v2.tools._http import query_retrieve
    from services.v2.tools.traverse_graph import traverse_graph

    profile: Dict[str, Any] = {"entity_name": entity_name, "entity_type": entity_type}

    # 1. Get co-occurring entities from Neo4j
    try:
        co_result = await traverse_graph(
            ctx,
            traversal_type="entity_co_occurrences",
            entity_name=entity_name,
            max_results=30,
        )
        profile["co_occurring_entities"] = co_result.get("records", [])
    except Exception as exc:
        profile["co_occurring_entities"] = []
        profile["co_occurrence_error"] = str(exc)

    # 2. Get documents mentioning entity from Neo4j
    try:
        doc_result = await traverse_graph(
            ctx,
            traversal_type="entity_documents",
            entity_name=entity_name,
            max_results=30,
        )
        profile["documents"] = doc_result.get("records", [])
    except Exception as exc:
        profile["documents"] = []
        profile["documents_error"] = str(exc)

    # 3. Get entity descriptions from GraphRAG
    try:
        desc_resp = await query_retrieve(
            query=entity_name,
            mode="local",
            top_k=10,
            decompose=False,
            graphrag_artifact_types=["entity_description"],
        )
        desc_hits = desc_resp.get("hits", [])
        profile["descriptions"] = [
            {"text": h.get("text", ""), "path": h.get("path", "")}
            for h in desc_hits
        ]
        for h in desc_hits:
            ctx.evidence.append(EvidenceItem(
                item_id=h.get("artifact_id") or "",
                evidence_type="entity_description",
                path=h.get("path") or "",
                domain=h.get("semantic_domain") or "",
                text=h.get("text") or "",
                score=float(h.get("score") or 0),
                source="entity_profile",
            ))
    except Exception as exc:
        profile["descriptions"] = []
        profile["descriptions_error"] = str(exc)

    # 4. Get relationships from GraphRAG
    try:
        rel_resp = await query_retrieve(
            query=entity_name,
            mode="local",
            top_k=10,
            decompose=False,
            graphrag_artifact_types=["relationship"],
        )
        rel_hits = rel_resp.get("hits", [])
        profile["relationships"] = [
            {"text": h.get("text", ""), "path": h.get("path", "")}
            for h in rel_hits
        ]
        for h in rel_hits:
            ctx.evidence.append(EvidenceItem(
                item_id=h.get("artifact_id") or "",
                evidence_type="relationship",
                path=h.get("path") or "",
                text=h.get("text") or "",
                score=float(h.get("score") or 0),
                source="entity_profile",
            ))
    except Exception as exc:
        profile["relationships"] = []
        profile["relationships_error"] = str(exc)

    # 5. Get chunk mentions (use local mode to also surface GraphRAG artifacts)
    if include_mentions:
        try:
            mention_resp = await query_retrieve(
                query=entity_name,
                mode="local",
                top_k=25,
                decompose=False,
            )
            mention_hits = mention_resp.get("hits", [])
            profile["mentions"] = [
                {"text": h.get("text", "")[:500], "path": h.get("path", "")}
                for h in mention_hits
            ]
            for h in mention_hits:
                ctx.evidence.append(EvidenceItem(
                    item_id=h.get("chunk_id") or "",
                    evidence_type="chunk",
                    path=h.get("path") or "",
                    text=h.get("text") or "",
                    score=float(h.get("score") or 0),
                    source="entity_profile",
                ))
        except Exception:
            profile["mentions"] = []

    ctx.entity_profiles.append(profile)

    return {
        "profile": profile,
        "summary": {
            "hit_count": (
                len(profile.get("co_occurring_entities", []))
                + len(profile.get("documents", []))
                + len(profile.get("descriptions", []))
            ),
            "document_count": len(profile.get("documents", [])),
            "co_occurring_count": len(profile.get("co_occurring_entities", [])),
        },
    }
