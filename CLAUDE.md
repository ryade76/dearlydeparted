# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Dearly Departed is a routing-first GraphRAG RAG system for capturing a departing employee's knowledge: their documents, policies, contracts, meeting notes, logs and photos. It classifies every document through a calibrated taxonomy before retrieval, then uses that classification to structure how retrieval works downstream.

## Key Commands

```bash
# Environment setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_pipeline.txt

# Docker services
make up              # Start core (ES/Qdrant/Neo4j) + model services (vllm-embed/vllm-rerank)
make up-core         # Data stores only
make up-models       # GPU model services only
make up-v2           # Full v2 stack (core + models + query-api + agent-api-v2 + frontend-v2)
make down            # Stop everything
make health          # Health check all services

# Full pipeline (in order)
make bootstrap       # Create ES indices, Qdrant collections, Neo4j constraints
make ingest-metadata # Pass A: ES + Neo4j (no embedding needed)
make ingest-vectors  # Pass B: Qdrant vectors (requires vllm-embed healthy)
make index-graphrag  # Pass C: GraphRAG artifacts + taxonomy enrichment + communities

# Evaluation and verification
make verify-ingest       # Check doc/chunk parity across all stores
make smoke-retrieval     # Quick retrieval smoke test
make smoke-v2            # Quick v2 agent smoke test
make eval-retrieval      # Retrieval quality eval against labeled queries
make calibrate-retrieval # Grid search over mode + top-k
make eval-v2             # v2 agent answer quality eval
make validate-full       # Run both evals with gate thresholds, persist reports

# Corpus calibration (offline, before ingest)
python3 corpus_routerkit.py --root . --out <output_dir> --rules rules_active.json \
  --benchmark-file benchmark_fixed_300.jsonl [flags...]
```

## Architecture (Three-Phase Pipeline)

### Phase A: Offline Calibration (`corpus_routerkit.py`)
Scans the raw corpus, classifies every document via **deterministic keyword/phrase/regex scoring** against a 15-domain taxonomy (`rules_active.json`). No LLM calls per document — the LLM is used only in the optional offline calibrator to suggest rule improvements. Outputs `documents.jsonl` (one record per document, schema v5) with routing decisions, confidence scores, entities, and metadata. Validated by 6 acceptance gates against a 300-item benchmark before rules are promoted.

### Phase A.5: Staged Ingest + GraphRAG Indexing
- `ingest_worker.py` — Chunks documents (token-based: 1200 tokens, 100 overlap), embeds via Qwen3-Embedding-8B (4096-dim), upserts to ES/Qdrant/Neo4j. Supports staged execution via `--skip-es`, `--skip-qdrant`, `--skip-neo4j` flags.
- `scripts/graphrag_index.py` — Dedicated GraphRAG indexing. Reads from ES (not files). Builds 9+ artifact types (text_unit, entity_description, relationship, claim, community_report, taxonomy_first_bucket, taxonomy_sub_bucket, taxonomy_bucket_card, taxonomy_domain_profile). Creates taxonomy graph in Neo4j and Louvain communities. Supports optional additive LLM extraction (`--enable-llm-graphrag`) for richer entity descriptions, typed relationships, factual claims, and narrative community reports.

### Phase B: Online Retrieval (`services/query_api.py` port 8080, `services/v2/api.py` port 8082)
Taxonomy-aware query decomposer (lexical + optional LLM) routes queries to mode/domain/bucket hints. Default mode is `auto` (decomposer chooses). 4-source retrieval (ES BM25 chunks, Qdrant dense chunks, ES GraphRAG artifacts, Qdrant GraphRAG artifacts) → RRF fusion (K=60) → Qwen3-Reranker-8B rerank (top-40 pool) → intent-aware rebalancing → top-k results. Four modes: `basic` (chunks only), `local` (+ entities/relationships/claims/taxonomy cards), `global` (+ community reports/domain profiles), `drift` (local+global). Query-time `graphrag_generation_mode` (both/llm/deterministic) and `graphrag_artifact_types` filters control which artifacts compete. Intent-aware rescue passes add text evidence when initial retrieval is claim/taxonomy-heavy. The v2 agent returns answers with verified citations, streamed over SSE.

## Critical Architecture Details

**Classification is deterministic, not LLM-based.** `classify_semantic_domain_detail()` in `corpus_routerkit.py` uses keyword/phrase/regex scoring with weighted source matching (path=3, meta=2, sample=1). Zero LLM calls per document.

**GraphRAG artifacts are pre-materialized text descriptions**, not graph traversals. They compete for retrieval slots via embedding similarity through the same RRF fusion pipeline as regular chunks. Neo4j is queried for metadata summary (`graph_summary()` — now includes taxonomy nodes), not for query-time traversal.

**Query API filters GraphRAG artifacts by `retrieval_mode` field**, not `artifact_type`. New artifact types are automatically compatible as long as they set the correct `retrieval_mode` (basic/local/global). Additional filters: `graphrag_generation_mode` (both/llm/deterministic) filters by `artifact_generation` field; `graphrag_artifact_types` constrains to specific types.

**Query decomposer** in `query_api.py` uses lexical taxonomy matching + optional LLM to auto-select mode, domain, and bucket hints. Default mode is `auto`. `QUERY_DECOMPOSER_UPGRADE_BASIC_MODE=1` allows basic→local/global/drift promotion when LLM confidence >= 0.45.

**Dual GraphRAG code paths exist**: `ingest_worker.py` has legacy 4-artifact GraphRAG code (`--enable-graphrag-artifacts`), but the canonical path is `scripts/graphrag_index.py` called via `make index-graphrag`. The Makefile does NOT pass `--enable-graphrag-artifacts` to any target.

**Intent-aware rebalancing** (`rebalance_ranked_hits()` in `query_api.py`) enforces text-evidence floor, caps claim/taxonomy saturation per query intent (lookup/synthesis/overview), and limits per-path repetition. 4-pass algorithm with progressive constraint relaxation. Controlled by `QUERY_REBALANCE_ENABLED`.

**Intent rescue passes** run automatically when reranked results lack sufficient text evidence. First tries a basic-mode retrieval pass, then falls back to ES lexical rescue with domain relaxation. Controlled by `QUERY_INTENT_RESCUE_PASS`. Diagnostics track rescue hit counts.

**Diagnostics block** in `/retrieve` response (enabled by `QUERY_DIAGNOSTICS_ENABLED`): intent profile, candidate mix at each stage (initial → post-rescue → pre-rerank → post-rerank → final), rebalancing details, fallback steps taken.

**Chat/LLM provider is any OpenAI-compatible endpoint.** Use `max_completion_tokens` (not `max_tokens`). `extract_text()` in `chat_provider.py` handles multiple response formats: `output_text` direct field, `output` array, `message.text`, standard `choices[0].message.content` (string or multimodal array).

**Document IDs are content-addressed**: `SHA256(path|size_bytes|mtime_date|content_hash)`. Chunk IDs are `{doc_id}:{chunk_index}`. This makes ingest idempotent — re-runs upsert, not duplicate.

## Key Files

| File | Role |
|---|---|
| `corpus_routerkit.py` | Main calibration pipeline (~5000 lines): scan, route, calibrate, benchmark, gate, promote |
| `ingest_worker.py` | Document ingest into ES/Qdrant/Neo4j (~1300 lines) |
| `scripts/graphrag_index.py` | Dedicated GraphRAG indexing (~1200 lines): 9+ artifact types + taxonomy graph + communities |
| `services/query_api.py` | FastAPI retrieval API: 4-source retrieval + RRF + rerank + rebalancing. Endpoints: `/retrieve`, `/health`, `/status` |
| `services/v2/api.py` | FastAPI v2 agent API: classify → react → generate → verify state machine, SSE streaming, conversations. Endpoints: `/v2/ask`, `/v2/ask/stream`, `/v2/conversations`, `/v2/health` |
| `services/chat_provider.py` | OpenAI-compatible chat wrapper with retry |
| `rules_active.json` | Taxonomy: 15 domains, 17 artifact types, per-bucket threshold overrides |
| `Makefile` | All automation targets (27+ .PHONY targets) |
| `docker-compose.dearly-departed.yml` | Full service stack: ES, Qdrant, Neo4j, vllm-embed, vllm-rerank, query-api, agent-api |
| `prompts/graphrag/` | System prompts for LLM extraction and community report generation |

## Data Stores

| Store | Indices/Collections | Purpose |
|---|---|---|
| Elasticsearch | `kb_docs`, `kb_chunks`, `kb_graphrag_artifacts` | Lexical/BM25 search + document metadata |
| Qdrant | `kb_chunks_vec`, `kb_graphrag_artifacts_vec` | Dense vector search (4096-dim, cosine) |
| Neo4j | Document/Chunk/Domain/Bucket/Entity + TaxonomyRoot/FirstBucket/SubBucket/Card/Community nodes | Graph structure + taxonomy hierarchy |

## Docker Compose Profiles

- `core` — elasticsearch, qdrant, neo4j
- `models` — vllm-embed (port 8001), vllm-rerank (port 8002)
- `app-v2` — query-api (port 8080), agent-api-v2 (port 8082), frontend-v2 (port 3001)
- `observability` — Langfuse (port 3002)

## Calibration Artifacts

The production-quality calibration output is `kb_artifacts_full_promo_calibrated_run3/`. Key files:
- `documents.jsonl` — one record per document with routing decision (routed or abstained)
- `router.json` — Structural routing taxonomy for GraphRAG taxonomy enrichment
- `bucket_cards.jsonl` — Rich bucket descriptions with domain hints
- `acceptance_report.json` — 6-gate results (all PASS)

## Environment

Requires `.env` (copy from `.env.example`). Key variables:
- `OPENROUTER_API_KEY` — For offline calibrator LLM calls
- `HUGGING_FACE_HUB_TOKEN` — For model downloads
- `NEO4J_PASSWORD` — Graph database auth
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `CHAT_MODEL` — Chat provider
- `GRAPHRAG_ENABLE_LLM=1` — Opt-in additive LLM extraction during `make index-graphrag`
- `QUERY_DIAGNOSTICS_ENABLED` / `QUERY_REBALANCE_ENABLED` / `QUERY_INTENT_RESCUE_PASS` — Retrieval pipeline controls

## LLM Extraction (Opt-In)

`GRAPHRAG_ENABLE_LLM=1` (or `make index-graphrag-llm`) enables additive LLM extraction during GraphRAG indexing. Controlled by `--llm-artifact-families` with 5 independently-gated families: `entity_description`, `relationship`, `claim`, `community_report_domain`, `community_report_graph`. LLM artifacts coexist with deterministic ones (additive, not replacement). Multi-pass "gleaning" (`--llm-max-gleanings`) for higher recall. 11 typed relationship types for entity→entity edges. Prompt files in `prompts/graphrag/`.

## Chat Provider

All LLM calls (agent generation, query decomposition, LLM extraction) go through `services/chat_provider.py`. Key constraints:
- Use `max_completion_tokens` (NOT `max_tokens`)
- `temperature` and `top_p` supported
- Validate model IDs with `GET /v1/models` before promoting configs

## Evaluation Suites

Eval query/task files live in `evals/`:
- `evals/retrieval/queries.sample.jsonl` — Retrieval eval queries (used by `make eval-retrieval`)
- `evals/v2/tasks.sample.jsonl` — Sample v2 agent tasks (used by `make eval-v2`); generate corpus-specific sets with `make generate-regression`
- `make validate-full` runs both evals with pass/fail gate thresholds and persists reports

## Known Limitations

- No formal unit test suite — quality is measured through eval scripts (`make eval-retrieval`, `make eval-v2`, `make validate-full`)
- RRF fusion uses fixed K=60 with equal source weighting — no learned weights
- Neo4j graph is not traversed at query time — only queried for metadata summary
