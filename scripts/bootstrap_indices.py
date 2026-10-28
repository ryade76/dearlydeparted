#!/usr/bin/env python3
"""
Bootstrap Elasticsearch, Qdrant, and Neo4j schema for local pipeline.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any, Dict, List, Sequence, Tuple

from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from neo4j import GraphDatabase

LOGGER = logging.getLogger("bootstrap_indices")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Bootstrap ES/Qdrant/Neo4j for RouterKit pipeline")
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--es-doc-index", default="kb_docs")
    ap.add_argument("--es-chunk-index", default="kb_chunks")
    ap.add_argument("--es-graphrag-index", default="kb_graphrag_artifacts")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--qdrant-collection", default="kb_chunks_vec")
    ap.add_argument("--qdrant-graphrag-collection", default="kb_graphrag_artifacts_vec")
    ap.add_argument("--qdrant-timeout-sec", type=int, default=120)
    ap.add_argument("--vector-size", type=int, default=4096)
    ap.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="change_me_local")
    ap.add_argument("--skip-graphrag-artifacts", action="store_true")
    ap.add_argument("--recreate", action="store_true", help="Delete and recreate indexes/collection")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def es_doc_mapping() -> Dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "dynamic": True,
            "properties": {
                "doc_id": {"type": "keyword"},
                "path": {"type": "keyword"},
                "source_root": {"type": "keyword"},
                "ext": {"type": "keyword"},
                "size_bytes": {"type": "long"},
                "mtime_date": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "effective_date": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "mtime_year": {"type": "integer"},
                "effective_year": {"type": "integer"},
                "mtime_epoch_day": {"type": "integer"},
                "effective_epoch_day": {"type": "integer"},
                "first_bucket": {"type": "keyword"},
                "sub_bucket": {"type": "keyword"},
                "semantic_domain": {"type": "keyword"},
                "semantic_decision": {"type": "keyword"},
                "semantic_score": {"type": "integer"},
                "semantic_margin": {"type": "integer"},
                "doc_type": {"type": "keyword"},
                "artifact_type": {"type": "keyword"},
                "sensitivity_level": {"type": "keyword"},
                "tags": {"type": "keyword"},
            },
        },
    }


def es_chunk_mapping() -> Dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "dynamic": True,
            "properties": {
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "path": {"type": "keyword"},
                "source_root": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "text": {"type": "text"},
                "mtime_date": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "effective_date": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "mtime_year": {"type": "integer"},
                "effective_year": {"type": "integer"},
                "mtime_epoch_day": {"type": "integer"},
                "effective_epoch_day": {"type": "integer"},
                "first_bucket": {"type": "keyword"},
                "sub_bucket": {"type": "keyword"},
                "semantic_domain": {"type": "keyword"},
                "semantic_decision": {"type": "keyword"},
                "artifact_type": {"type": "keyword"},
                "doc_type": {"type": "keyword"},
                "sensitivity_level": {"type": "keyword"},
                "tags": {"type": "keyword"},
            },
        },
    }


def es_graphrag_mapping() -> Dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "dynamic": True,
            "properties": {
                "artifact_id": {"type": "keyword"},
                "artifact_type": {"type": "keyword"},
                "artifact_generation": {"type": "keyword"},
                "extraction_method": {"type": "keyword"},
                "extraction_model": {"type": "keyword"},
                "retrieval_mode": {"type": "keyword"},
                "text": {"type": "text"},
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "path": {"type": "keyword"},
                "source_root": {"type": "keyword"},
                "mtime_date": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "effective_date": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "mtime_year": {"type": "integer"},
                "effective_year": {"type": "integer"},
                "mtime_epoch_day": {"type": "integer"},
                "effective_epoch_day": {"type": "integer"},
                "temporal_span_from": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "temporal_span_to": {"type": "date", "format": "strict_date_optional_time||yyyy-MM-dd"},
                "temporal_span_years": {"type": "float"},
                "semantic_domain": {"type": "keyword"},
                "semantic_decision": {"type": "keyword"},
                "first_bucket": {"type": "keyword"},
                "sub_bucket": {"type": "keyword"},
                "entity_label": {"type": "keyword"},
                "entity_name": {"type": "keyword"},
                "relation_type": {"type": "keyword"},
                "src_id": {"type": "keyword"},
                "src_type": {"type": "keyword"},
                "dst_id": {"type": "keyword"},
                "dst_type": {"type": "keyword"},
                "community_id": {"type": "keyword"},
                "community_scope": {"type": "keyword"},
                "community_method": {"type": "keyword"},
                "taxonomy_level": {"type": "keyword"},
                "taxonomy_id": {"type": "keyword"},
                "taxonomy_card_id": {"type": "keyword"},
                "domain_hint": {"type": "keyword"},
                "bucket_description": {"type": "text"},
            },
        },
    }


def bootstrap_es(
    es: Elasticsearch,
    doc_index: str,
    chunk_index: str,
    graphrag_index: str,
    recreate: bool,
    skip_graphrag_artifacts: bool,
) -> None:
    es_60 = es.options(request_timeout=60)
    es_120 = es.options(request_timeout=120)
    es_180 = es.options(request_timeout=180)
    index_plan: List[Tuple[str, Dict[str, Any]]] = [
        (doc_index, es_doc_mapping()),
        (chunk_index, es_chunk_mapping()),
    ]
    if not skip_graphrag_artifacts:
        index_plan.append((graphrag_index, es_graphrag_mapping()))

    for index_name, body in index_plan:
        exists = es_60.indices.exists(index=index_name)
        if exists and recreate:
            LOGGER.info("Deleting Elasticsearch index=%s", index_name)
            es_120.indices.delete(index=index_name)
            exists = False
        if not exists:
            LOGGER.info("Creating Elasticsearch index=%s", index_name)
            try:
                es_180.indices.create(index=index_name, body=body)
            except Exception as exc:
                # On slow local disks, ES may time out while still creating the index in background.
                LOGGER.warning("Index create for %s timed out/errored: %s", index_name, exc)
                exists_after = es_60.indices.exists(index=index_name)
                if not exists_after:
                    raise
                LOGGER.info("Index %s exists after timeout; continuing", index_name)
        else:
            LOGGER.info("Elasticsearch index exists: %s", index_name)
            # Merge any newly added fields into existing mappings.
            try:
                desired_props = (
                    body.get("mappings", {}).get("properties", {})
                    if isinstance(body.get("mappings"), dict)
                    else {}
                )
                if isinstance(desired_props, dict) and desired_props:
                    current = es_120.indices.get_mapping(index=index_name)
                    cur_index = current.get(index_name, {}) if isinstance(current, dict) else {}
                    cur_mappings = cur_index.get("mappings", {}) if isinstance(cur_index.get("mappings"), dict) else {}
                    cur_props = cur_mappings.get("properties", {}) if isinstance(cur_mappings.get("properties"), dict) else {}
                    for field_name, field_body in desired_props.items():
                        if field_name in cur_props:
                            continue
                        try:
                            es_120.indices.put_mapping(index=index_name, properties={field_name: field_body})
                        except Exception as field_exc:
                            LOGGER.warning(
                                "Skipping mapping field %s.%s due to error: %s",
                                index_name,
                                field_name,
                                field_exc,
                            )
            except Exception as exc:
                LOGGER.warning("Failed to update mapping for %s: %s", index_name, exc)


def qdrant_call(desc: str, fn: Any, retries: int = 4, backoff_sec: float = 1.5) -> Any:
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= retries:
                raise
            sleep_s = backoff_sec * attempt
            LOGGER.warning(
                "Qdrant call failed (%s), attempt=%d/%d, retrying in %.1fs: %s",
                desc,
                attempt,
                retries,
                sleep_s,
                exc,
            )
            time.sleep(sleep_s)


def bootstrap_qdrant_collection(
    client: QdrantClient,
    *,
    collection: str,
    vector_size: int,
    recreate: bool,
    payload_fields: Sequence[Tuple[str, Any]],
) -> None:
    exists = bool(qdrant_call("collection_exists", lambda: client.collection_exists(collection_name=collection)))
    if exists and recreate:
        LOGGER.info("Deleting Qdrant collection=%s", collection)
        qdrant_call("delete_collection", lambda: client.delete_collection(collection_name=collection))
        exists = False

    if not exists:
        LOGGER.info("Creating Qdrant collection=%s", collection)
        try:
            qdrant_call(
                "create_collection",
                lambda: client.create_collection(
                    collection_name=collection,
                    vectors_config=qm.VectorParams(size=int(vector_size), distance=qm.Distance.COSINE),
                ),
            )
        except Exception as exc:
            # Local disks can cause request timeout while creation still succeeds.
            LOGGER.warning("Qdrant create_collection errored: %s", exc)
            exists_after = bool(
                qdrant_call("collection_exists_post_create", lambda: client.collection_exists(collection_name=collection))
            )
            if not exists_after:
                raise
            LOGGER.info("Qdrant collection exists after create error; continuing")
    else:
        LOGGER.info("Qdrant collection exists: %s", collection)

    for field, schema in payload_fields:
        try:
            qdrant_call(
                f"create_payload_index:{field}",
                lambda: client.create_payload_index(
                    collection_name=collection,
                    field_name=field,
                    field_schema=schema,
                    wait=True,
                ),
                retries=3,
            )
        except Exception as exc:
            # Non-fatal: may already exist or timeout on slow startup.
            LOGGER.warning("Skipping payload index %s due to error: %s", field, exc)


def bootstrap_neo4j(uri: str, user: str, password: str) -> None:
    constraints: List[str] = [
        "CREATE CONSTRAINT document_doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
        "CREATE CONSTRAINT chunk_chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
        "CREATE CONSTRAINT domain_id IF NOT EXISTS FOR (d:Domain) REQUIRE d.id IS UNIQUE",
        "CREATE CONSTRAINT bucket_id IF NOT EXISTS FOR (b:Bucket) REQUIRE b.id IS UNIQUE",
        "CREATE CONSTRAINT taxonomy_root_id IF NOT EXISTS FOR (t:TaxonomyRoot) REQUIRE t.id IS UNIQUE",
        "CREATE CONSTRAINT taxonomy_first_bucket_id IF NOT EXISTS FOR (t:TaxonomyFirstBucket) REQUIRE t.id IS UNIQUE",
        "CREATE CONSTRAINT taxonomy_sub_bucket_id IF NOT EXISTS FOR (t:TaxonomySubBucket) REQUIRE t.id IS UNIQUE",
        "CREATE CONSTRAINT taxonomy_card_id IF NOT EXISTS FOR (t:TaxonomyCard) REQUIRE t.card_id IS UNIQUE",
        "CREATE CONSTRAINT community_id IF NOT EXISTS FOR (c:Community) REQUIRE c.community_id IS UNIQUE",
        "CREATE CONSTRAINT system_name IF NOT EXISTS FOR (n:System) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT vendor_name IF NOT EXISTS FOR (n:Vendor) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT project_name IF NOT EXISTS FOR (n:Project) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (n:Person) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT location_name IF NOT EXISTS FOR (n:Location) REQUIRE n.name IS UNIQUE",
    ]

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        for stmt in constraints:
            session.run(stmt)
    driver.close()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    LOGGER.info("Bootstrapping Elasticsearch")
    es = Elasticsearch(args.es_url)
    bootstrap_es(
        es,
        args.es_doc_index,
        args.es_chunk_index,
        args.es_graphrag_index,
        bool(args.recreate),
        bool(args.skip_graphrag_artifacts),
    )

    LOGGER.info("Bootstrapping Qdrant")
    qdr = QdrantClient(url=args.qdrant_url, timeout=float(args.qdrant_timeout_sec))
    base_payload_fields: List[Tuple[str, Any]] = [
        ("semantic_domain", qm.PayloadSchemaType.KEYWORD),
        ("semantic_decision", qm.PayloadSchemaType.KEYWORD),
        ("first_bucket", qm.PayloadSchemaType.KEYWORD),
        ("sub_bucket", qm.PayloadSchemaType.KEYWORD),
        ("artifact_type", qm.PayloadSchemaType.KEYWORD),
        ("doc_type", qm.PayloadSchemaType.KEYWORD),
        ("sensitivity_level", qm.PayloadSchemaType.KEYWORD),
        ("mtime_year", qm.PayloadSchemaType.INTEGER),
        ("effective_year", qm.PayloadSchemaType.INTEGER),
        ("mtime_epoch_day", qm.PayloadSchemaType.INTEGER),
        ("effective_epoch_day", qm.PayloadSchemaType.INTEGER),
    ]
    bootstrap_qdrant_collection(
        qdr,
        collection=args.qdrant_collection,
        vector_size=int(args.vector_size),
        recreate=bool(args.recreate),
        payload_fields=base_payload_fields,
    )
    if not args.skip_graphrag_artifacts:
        graphrag_payload_fields: List[Tuple[str, Any]] = [
            ("artifact_type", qm.PayloadSchemaType.KEYWORD),
            ("artifact_generation", qm.PayloadSchemaType.KEYWORD),
            ("extraction_method", qm.PayloadSchemaType.KEYWORD),
            ("retrieval_mode", qm.PayloadSchemaType.KEYWORD),
            ("semantic_domain", qm.PayloadSchemaType.KEYWORD),
            ("semantic_decision", qm.PayloadSchemaType.KEYWORD),
            ("first_bucket", qm.PayloadSchemaType.KEYWORD),
            ("sub_bucket", qm.PayloadSchemaType.KEYWORD),
            ("relation_type", qm.PayloadSchemaType.KEYWORD),
            ("entity_label", qm.PayloadSchemaType.KEYWORD),
            ("community_id", qm.PayloadSchemaType.KEYWORD),
            ("community_scope", qm.PayloadSchemaType.KEYWORD),
            ("community_method", qm.PayloadSchemaType.KEYWORD),
            ("doc_id", qm.PayloadSchemaType.KEYWORD),
            ("chunk_id", qm.PayloadSchemaType.KEYWORD),
            ("src_id", qm.PayloadSchemaType.KEYWORD),
            ("src_type", qm.PayloadSchemaType.KEYWORD),
            ("dst_id", qm.PayloadSchemaType.KEYWORD),
            ("dst_type", qm.PayloadSchemaType.KEYWORD),
            ("taxonomy_level", qm.PayloadSchemaType.KEYWORD),
            ("taxonomy_id", qm.PayloadSchemaType.KEYWORD),
            ("taxonomy_card_id", qm.PayloadSchemaType.KEYWORD),
            ("domain_hint", qm.PayloadSchemaType.KEYWORD),
            ("mtime_year", qm.PayloadSchemaType.INTEGER),
            ("effective_year", qm.PayloadSchemaType.INTEGER),
            ("mtime_epoch_day", qm.PayloadSchemaType.INTEGER),
            ("effective_epoch_day", qm.PayloadSchemaType.INTEGER),
        ]
        bootstrap_qdrant_collection(
            qdr,
            collection=args.qdrant_graphrag_collection,
            vector_size=int(args.vector_size),
            recreate=bool(args.recreate),
            payload_fields=graphrag_payload_fields,
        )

    LOGGER.info("Bootstrapping Neo4j")
    bootstrap_neo4j(args.neo4j_uri, args.neo4j_user, args.neo4j_password)

    LOGGER.info("Bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
