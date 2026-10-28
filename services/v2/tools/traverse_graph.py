"""traverse_graph — Neo4j Cypher traversal with 6 pre-defined templates."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.v2.agent.states import AgentContext
from services.v2.tools.base import register_tool

log = logging.getLogger("v2.tools.traverse_graph")

# Initialized at startup
_driver = None


def init_neo4j_driver(uri: str, user: str, password: str) -> None:
    global _driver
    from neo4j import AsyncGraphDatabase
    _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))


def get_driver():
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialized")
    return _driver


async def close_neo4j_driver() -> None:
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


# --- 6 Cypher templates matching actual Neo4j schema ---

TEMPLATES = {
    "entity_co_occurrences": {
        "cypher": (
            "MATCH (d:Document)-[:HAS_ENTITY]->(e {name: $name}), "
            "(d)-[:HAS_ENTITY]->(other) "
            "WHERE other.name <> e.name "
            "RETURN labels(other)[0] AS entity_type, other.name AS entity_name, "
            "count(d) AS shared_doc_count "
            "ORDER BY shared_doc_count DESC LIMIT $limit"
        ),
        "params": ["name"],
        "description": "Find entities that co-occur with a given entity through shared documents",
    },
    "entity_documents": {
        "cypher": (
            "MATCH (d:Document)-[:HAS_ENTITY]->(e {name: $name}) "
            "RETURN d.doc_id AS doc_id, d.path AS path, d.semantic_domain AS domain, "
            "d.mtime_date AS mtime_date "
            "ORDER BY d.mtime_date DESC LIMIT $limit"
        ),
        "params": ["name"],
        "description": "Find all documents mentioning an entity",
    },
    "domain_entities": {
        "cypher": (
            "MATCH (d:Document)-[:IN_DOMAIN]->(:Domain {id: $domain}), "
            "(d)-[:HAS_ENTITY]->(e) "
            "RETURN labels(e)[0] AS entity_type, e.name AS entity_name, "
            "count(DISTINCT d) AS doc_count "
            "ORDER BY doc_count DESC LIMIT $limit"
        ),
        "params": ["domain"],
        "description": "All entities in a domain",
    },
    "taxonomy_path": {
        "cypher": (
            "MATCH (root:TaxonomyRoot)-[:HAS_FIRST_BUCKET]->(fb:TaxonomyFirstBucket) "
            "WHERE fb.id = $bucket "
            "OPTIONAL MATCH (fb)-[:HAS_SUB_BUCKET]->(sb) "
            "OPTIONAL MATCH (sb)-[:HAS_CARD]->(c:TaxonomyCard) "
            "RETURN fb.id AS first_bucket, fb.label AS first_bucket_label, "
            "collect(DISTINCT {id: sb.id, label: sb.label}) AS sub_buckets, "
            "collect(DISTINCT {card_id: c.card_id, title: c.title}) AS cards"
        ),
        "params": ["bucket"],
        "description": "Walk taxonomy hierarchy for a first bucket",
    },
    "community_members": {
        "cypher": (
            "MATCH (d:Document)-[:IN_COMMUNITY]->(c:Community {community_id: $id}) "
            "OPTIONAL MATCH (d)-[:HAS_ENTITY]->(e) "
            "RETURN d.doc_id AS doc_id, d.path AS path, "
            "collect(DISTINCT {type: labels(e)[0], name: e.name}) AS entities "
            "LIMIT $limit"
        ),
        "params": ["id"],
        "description": "Documents and entities in a community",
    },
    "shared_documents": {
        "cypher": (
            "MATCH (d:Document)-[:HAS_ENTITY]->(a {name: $name_a}), "
            "(d)-[:HAS_ENTITY]->(b {name: $name_b}) "
            "RETURN d.doc_id AS doc_id, d.path AS path, "
            "d.semantic_domain AS domain, d.mtime_date AS mtime_date "
            "ORDER BY d.mtime_date DESC LIMIT $limit"
        ),
        "params": ["name_a", "name_b"],
        "description": "Documents where two entities co-occur",
    },
}


@register_tool(
    name="traverse_graph",
    description=(
        "Traverse the Neo4j knowledge graph using pre-defined query templates. "
        "Find entity co-occurrences, entity documents, domain entities, taxonomy paths, "
        "community members, or shared documents between entities. "
        "Note: entities connect through shared documents, not direct edges."
    ),
    parameters={
        "type": "object",
        "properties": {
            "traversal_type": {
                "type": "string",
                "description": "The type of graph traversal to perform",
                "enum": list(TEMPLATES.keys()),
            },
            "entity_name": {
                "type": "string",
                "description": "Primary entity name (for entity_co_occurrences, entity_documents)",
                "default": "",
            },
            "entity_name_b": {
                "type": "string",
                "description": "Second entity name (for shared_documents)",
                "default": "",
            },
            "domain": {
                "type": "string",
                "description": "Domain ID (for domain_entities)",
                "default": "",
            },
            "bucket": {
                "type": "string",
                "description": "First bucket ID (for taxonomy_path)",
                "default": "",
            },
            "community_id": {
                "type": "string",
                "description": "Community ID (for community_members)",
                "default": "",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return",
                "default": 50,
            },
        },
        "required": ["traversal_type"],
    },
)
async def traverse_graph(
    ctx: AgentContext,
    traversal_type: str,
    entity_name: str = "",
    entity_name_b: str = "",
    domain: str = "",
    bucket: str = "",
    community_id: str = "",
    max_results: int = 50,
) -> Dict[str, Any]:
    template = TEMPLATES.get(traversal_type)
    if not template:
        raise ValueError(f"Unknown traversal type: {traversal_type}. Valid: {list(TEMPLATES.keys())}")

    # Build params from template requirements
    params: Dict[str, Any] = {"limit": max_results}
    param_map = {
        "name": entity_name,
        "name_a": entity_name,
        "name_b": entity_name_b,
        "domain": domain,
        "bucket": bucket,
        "id": community_id,
    }
    for p in template["params"]:
        val = param_map.get(p, "")
        if not val:
            raise ValueError(f"Traversal '{traversal_type}' requires parameter '{p}'")
        params[p] = val

    driver = get_driver()
    records = []
    async with driver.session() as session:
        result = await session.run(template["cypher"], **params)
        async for record in result:
            records.append(dict(record))

    ctx.graph_traversal_results.append({
        "traversal_type": traversal_type,
        "params": {k: v for k, v in params.items() if k != "limit"},
        "records": records,
    })

    return {
        "records": records,
        "summary": {
            "hit_count": len(records),
            "traversal_type": traversal_type,
        },
    }
