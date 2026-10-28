PYTHON ?= .venv/bin/python
COMPOSE_FILE ?= docker-compose.dearly-departed.yml
COMPOSE ?= docker compose -f $(COMPOSE_FILE)
LOCAL_NO_PROXY ?= 127.0.0.1,localhost,elasticsearch,qdrant,neo4j,vllm-embed,vllm-rerank,query-api,agent-api-v2
export NO_PROXY := $(LOCAL_NO_PROXY)
export no_proxy := $(LOCAL_NO_PROXY)

ES_URL ?= http://127.0.0.1:9200
ES_DOC_INDEX ?= kb_docs
ES_CHUNK_INDEX ?= kb_chunks
QDRANT_URL ?= http://127.0.0.1:6333
NEO4J_URI ?= bolt://127.0.0.1:7687
NEO4J_USER ?= neo4j
NEO4J_PASSWORD ?= change_me_local
QUERY_API_URL ?= http://127.0.0.1:8080/retrieve

EMBED_URL ?= http://127.0.0.1:8001/v1/embeddings
EMBED_MODEL ?= Qwen/Qwen3-Embedding-8B
VECTOR_DIM ?= 4096
TOKENIZER_MODEL ?= Qwen/Qwen3-Embedding-8B
CHUNK_MODE ?= auto
CHUNK_CHARS ?= 1600
CHUNK_OVERLAP ?= 200
CHUNK_TOKENS ?= 1200
CHUNK_OVERLAP_TOKENS ?= 100
EMBED_BATCH_SIZE ?= 1
ARTIFACT_EMBED_BATCH_SIZE ?= 1
EMBED_MAX_ATTEMPTS ?= 3
EMBED_RETRY_BACKOFF_SEC ?= 3.0
ES_GRAPHRAG_INDEX ?= kb_graphrag_artifacts
QDRANT_GRAPHRAG_COLLECTION ?= kb_graphrag_artifacts_vec
QUERY_MODE ?= basic
EVAL_RETRIEVAL_BACKEND ?= query-api
VERIFY_EXPECTED_CHUNKS_SOURCE ?= auto
VERIFY_CHUNK_MODE ?= $(if $(filter auto,$(CHUNK_MODE)),tokens,$(CHUNK_MODE))
VERIFY_ALLOW_DOC_DRIFT ?= 0
VERIFY_ALLOW_CHUNK_DRIFT ?= 0
V2_API_URL ?= http://127.0.0.1:8082
V2_EVAL_TASKS ?= evals/v2/tasks.sample.jsonl
RETRIEVAL_EVAL_FILE ?= evals/retrieval/queries.sample.jsonl
VALIDATION_REPORT_DIR ?= reports/validation
RETRIEVAL_EVAL_MIN_PASS_RATE ?= 0.80

ROUTERKIT_DOCS ?= kb_artifacts_full_promo_calibrated_run3/documents.jsonl
TAXONOMY_ROUTER_JSON ?= kb_artifacts_full_promo_calibrated_run3/router.json
TAXONOMY_BUCKET_CARDS ?= kb_artifacts_full_promo_calibrated_run3/bucket_cards.jsonl
GRAPHRAG_EMBED_BATCH_SIZE ?= $(ARTIFACT_EMBED_BATCH_SIZE)
GRAPHRAG_COMMUNITY_METHOD ?= auto
GRAPHRAG_COMMUNITY_RESOLUTION ?= 1.0
GRAPHRAG_COMMUNITY_MIN_SHARED_ENTITIES ?= 1
GRAPHRAG_COMMUNITY_MIN_DOCS ?= 3
GRAPHRAG_COMMUNITY_MAX_REPORTS ?= 250
GRAPHRAG_QDRANT_UPSERT_BATCH_SIZE ?= 128
GRAPHRAG_QDRANT_UPSERT_WAIT ?= 1
GRAPHRAG_ENABLE_LLM ?= 0
GRAPHRAG_LLM_ARTIFACT_FAMILIES ?= entity_description,relationship,claim,community_report_domain,community_report_graph
GRAPHRAG_LLM_EXTRACTION_BATCH_SIZE ?= 3
GRAPHRAG_LLM_MAX_CHUNKS ?= 0
GRAPHRAG_LLM_MAX_CHARS_PER_CHUNK ?= 1800
GRAPHRAG_LLM_MAX_ENTITIES_PER_CHUNK ?= 8
GRAPHRAG_LLM_MAX_RELATIONSHIPS_PER_CHUNK ?= 8
GRAPHRAG_LLM_MAX_CLAIMS_PER_CHUNK ?= 8
GRAPHRAG_LLM_MAX_GLEANINGS ?= 1
GRAPHRAG_LLM_MAX_ENTITIES_TOTAL ?= 0
GRAPHRAG_LLM_MAX_RELATIONSHIPS_TOTAL ?= 0
GRAPHRAG_LLM_MAX_CLAIMS_TOTAL ?= 0
GRAPHRAG_LLM_COMMUNITY_MAX_DOMAINS ?= 0
GRAPHRAG_LLM_COMMUNITY_MAX_EVIDENCE_ITEMS ?= 80
GRAPHRAG_LLM_TEMPERATURE ?= 0.0
GRAPHRAG_LLM_TOP_P ?= 0.1
GRAPHRAG_LLM_MAX_COMPLETION_TOKENS ?= 3000
GRAPHRAG_LLM_EXTRACTION_PROMPT_FILE ?= prompts/graphrag/extraction_system_prompt.txt
GRAPHRAG_LLM_COMMUNITY_PROMPT_FILE ?= prompts/graphrag/community_system_prompt.txt
ifneq ($(filter 1 true yes on,$(strip $(GRAPHRAG_ENABLE_LLM))),)
GRAPHRAG_LLM_FLAGS := \
	--enable-llm-graphrag \
	--llm-artifact-families $(GRAPHRAG_LLM_ARTIFACT_FAMILIES) \
	--llm-extraction-batch-size $(GRAPHRAG_LLM_EXTRACTION_BATCH_SIZE) \
	--llm-max-chunks $(GRAPHRAG_LLM_MAX_CHUNKS) \
	--llm-max-chars-per-chunk $(GRAPHRAG_LLM_MAX_CHARS_PER_CHUNK) \
	--llm-max-entities-per-chunk $(GRAPHRAG_LLM_MAX_ENTITIES_PER_CHUNK) \
	--llm-max-relationships-per-chunk $(GRAPHRAG_LLM_MAX_RELATIONSHIPS_PER_CHUNK) \
	--llm-max-claims-per-chunk $(GRAPHRAG_LLM_MAX_CLAIMS_PER_CHUNK) \
	--llm-max-gleanings $(GRAPHRAG_LLM_MAX_GLEANINGS) \
	--llm-max-entities-total $(GRAPHRAG_LLM_MAX_ENTITIES_TOTAL) \
	--llm-max-relationships-total $(GRAPHRAG_LLM_MAX_RELATIONSHIPS_TOTAL) \
	--llm-max-claims-total $(GRAPHRAG_LLM_MAX_CLAIMS_TOTAL) \
	--llm-community-max-domains $(GRAPHRAG_LLM_COMMUNITY_MAX_DOMAINS) \
	--llm-community-max-evidence-items $(GRAPHRAG_LLM_COMMUNITY_MAX_EVIDENCE_ITEMS) \
	--llm-temperature $(GRAPHRAG_LLM_TEMPERATURE) \
	--llm-top-p $(GRAPHRAG_LLM_TOP_P) \
	--llm-max-completion-tokens $(GRAPHRAG_LLM_MAX_COMPLETION_TOKENS) \
	$(if $(strip $(GRAPHRAG_LLM_EXTRACTION_PROMPT_FILE)),--llm-extraction-system-prompt-file $(GRAPHRAG_LLM_EXTRACTION_PROMPT_FILE),) \
	$(if $(strip $(GRAPHRAG_LLM_COMMUNITY_PROMPT_FILE)),--llm-community-system-prompt-file $(GRAPHRAG_LLM_COMMUNITY_PROMPT_FILE),)
else
GRAPHRAG_LLM_FLAGS :=
endif
INGEST_STATE_FILE ?= localdata/ingest_state.json
CONTENT_SOURCE ?= prefer_filesystem
FILESYSTEM_ON_MISS ?= fail
CORPUS_ROOT ?= .
CONTENT_SAMPLE_MODE ?= full
CONTENT_MAX_CHARS ?= 120000
CONTENT_PDF_MAX_PAGES ?= 80
CONTENT_PPTX_MAX_SLIDES ?= 120
CONTENT_XLSX_MAX_STRINGS ?= 20000
CONTENT_XLSX_MAX_CHARS ?= 600000
CONTENT_ZIP_MAX_MEMBERS ?= 400
CONTENT_BINARY_MAX_BYTES ?= 8388608
PREFLIGHT_MAX_EMPTY_PDF_RATIO ?= 0.40
PREFLIGHT_EMPTY_PDF_MIN_DOCS ?= 10
WEAK_EXTRACTION_THRESHOLD ?= 55
WEAK_EXTRACTIONS_REPORT ?= reports/weak_extractions.jsonl
WEAK_ROUTERKIT_SUBSET ?= localdata/routerkit_weak_extractions.jsonl

.PHONY: help
help:
	@echo "Targets:"
	@echo "  up-core            Start Elasticsearch, Qdrant, Neo4j"
	@echo "  up-models          Start embedding/rerank model services"
	@echo "  up-eval            Start eval profile services (when defined)"
	@echo "  up                 Start core + models"
	@echo "  down               Stop stack"
	@echo "  ps                 Show stack status"
	@echo "  logs               Tail compose logs"
	@echo "  preflight-gpu      Verify GPU + Docker GPU runtime"
	@echo "  preflight-images   Check configured model image availability"
	@echo "  health             Run infra+model health checks"
	@echo "  bootstrap          Create ES/Qdrant/Neo4j schemas"
	@echo "  ingest-full        Full ingest from ROUTERKIT_DOCS"
	@echo "  ingest-metadata    Ingest docs/chunks into ES+Neo4j (skip vectors)"
	@echo "  ingest-vectors     Ingest vectors into Qdrant only (requires embed service)"
	@echo "  index-graphrag     Dedicated Stage C GraphRAG indexing (incl. taxonomy enrichment)"
	@echo "  index-graphrag-llm Same as index-graphrag with additive LLM extraction enabled"
	@echo "  ingest-graphrag    Alias for index-graphrag"
	@echo "  backfill-graphrag-vectors-lowbatch  Qdrant-only GraphRAG vector backfill (stable low-batch)"
	@echo "  ingest-taxonomy    Taxonomy-only enrichment ingest into Neo4j + GraphRAG indices"
	@echo "  ingest-incremental Incremental ingest with state file"
	@echo "  identify-weak-extractions  Build weak extraction report + optional reingest subset"
	@echo "  smoke-retrieval    Run retrieval smoke test"
	@echo "  verify-ingest      Verify counts across stores"
	@echo "  eval-retrieval     Run retrieval eval on sample suite"
	@echo "  calibrate-retrieval Sweep retrieval top-k and select best"
	@echo "  validate-full      Run retrieval + agent evals with gate thresholds and persist reports"
	@echo ""
	@echo "Dearly Departed v2:"
	@echo "  up-v2              Start full v2 stack (core + models + APIs + frontend)"
	@echo "  down-v2            Stop v2 services"
	@echo "  dev-frontend       Run frontend-v2 dev server locally"
	@echo "  health-v2          Health check v2 agent"
	@echo "  smoke-v2           Quick v2 agent smoke test"
	@echo "  eval-v2            Run v2 agent eval suite"
	@echo "  up-observability   Start Langfuse observability stack"
	@echo ""
	@echo "Regression test suite:"
	@echo "  generate-regression  Generate 5x2500 question sets from ES chunks via LLM"
	@echo "  regression-set-N     Run regression set N (1-5) with trace capture"
	@echo "  regression-full      Run all 5 regression sets sequentially"
	@echo "  regression-quick     Run first 50 questions from set 1 (smoke test)"

.PHONY: up-core
up-core:
	$(COMPOSE) --profile core up -d

.PHONY: up-models
up-models:
	$(COMPOSE) --profile models up -d

.PHONY: dev-frontend
dev-frontend:
	cd frontend-v2 && npm run dev

.PHONY: up-eval
up-eval:
	$(COMPOSE) --profile core --profile models --profile app --profile eval up -d

.PHONY: up
up:
	$(COMPOSE) --profile core --profile models up -d

.PHONY: down
down:
	$(COMPOSE) down

.PHONY: ps
ps:
	$(COMPOSE) ps

.PHONY: logs
logs:
	$(COMPOSE) logs -f --tail=150

.PHONY: preflight-gpu
preflight-gpu:
	nvidia-smi
	docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

.PHONY: preflight-images
preflight-images:
	@bash -lc 'set -euo pipefail; \
	embed_image="$${VLLM_EMBED_IMAGE:-nvcr.io/nvidia/vllm:25.09-py3}"; \
	rerank_image="$${VLLM_RERANK_IMAGE:-nvcr.io/nvidia/vllm:25.09-py3}"; \
	echo "[preflight] checking $$embed_image"; \
	docker manifest inspect "$$embed_image" >/dev/null || { \
	  echo "Failed to access $$embed_image (run: docker login nvcr.io)."; \
	  exit 1; \
	}; \
	echo "[preflight] checking $$rerank_image"; \
	docker manifest inspect "$$rerank_image" >/dev/null || { \
	  echo "Failed to access $$rerank_image (run: docker login nvcr.io)."; \
	  exit 1; \
	}; \
	echo "[preflight] model images are accessible"'

.PHONY: health
health:
	bash scripts/smoke_infra.sh

.PHONY: bootstrap
bootstrap:
	$(PYTHON) scripts/bootstrap_indices.py \
		--es-url $(ES_URL) \
		--es-doc-index $(ES_DOC_INDEX) \
		--es-chunk-index $(ES_CHUNK_INDEX) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--es-graphrag-index $(ES_GRAPHRAG_INDEX) \
		--qdrant-graphrag-collection $(QDRANT_GRAPHRAG_COLLECTION) \
		--vector-size $(VECTOR_DIM)

.PHONY: ingest-full
ingest-full:
	$(PYTHON) ingest_worker.py \
		--routerkit-documents $(ROUTERKIT_DOCS) \
		--content-source $(CONTENT_SOURCE) \
		--filesystem-on-miss $(FILESYSTEM_ON_MISS) \
		--corpus-root $(CORPUS_ROOT) \
		--content-sample-mode $(CONTENT_SAMPLE_MODE) \
		--content-max-chars $(CONTENT_MAX_CHARS) \
		--content-pdf-max-pages $(CONTENT_PDF_MAX_PAGES) \
		--content-pptx-max-slides $(CONTENT_PPTX_MAX_SLIDES) \
		--content-xlsx-max-strings $(CONTENT_XLSX_MAX_STRINGS) \
		--content-xlsx-max-chars $(CONTENT_XLSX_MAX_CHARS) \
		--content-zip-max-members $(CONTENT_ZIP_MAX_MEMBERS) \
		--content-binary-max-bytes $(CONTENT_BINARY_MAX_BYTES) \
		--preflight-max-empty-pdf-ratio $(PREFLIGHT_MAX_EMPTY_PDF_RATIO) \
		--preflight-empty-pdf-min-docs $(PREFLIGHT_EMPTY_PDF_MIN_DOCS) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--vector-dim $(VECTOR_DIM) \
		--embed-batch-size $(EMBED_BATCH_SIZE) \
		--artifact-embed-batch-size $(ARTIFACT_EMBED_BATCH_SIZE) \
		--embed-max-attempts $(EMBED_MAX_ATTEMPTS) \
		--embed-retry-backoff-sec $(EMBED_RETRY_BACKOFF_SEC) \
		--chunk-mode $(CHUNK_MODE) \
		--chunk-chars $(CHUNK_CHARS) \
		--chunk-overlap $(CHUNK_OVERLAP) \
		--chunk-tokens $(CHUNK_TOKENS) \
		--chunk-overlap-tokens $(CHUNK_OVERLAP_TOKENS) \
		--tokenizer-model $(TOKENIZER_MODEL)

.PHONY: ingest-metadata
ingest-metadata:
	$(PYTHON) ingest_worker.py \
		--routerkit-documents $(ROUTERKIT_DOCS) \
		--content-source $(CONTENT_SOURCE) \
		--filesystem-on-miss $(FILESYSTEM_ON_MISS) \
		--corpus-root $(CORPUS_ROOT) \
		--content-sample-mode $(CONTENT_SAMPLE_MODE) \
		--content-max-chars $(CONTENT_MAX_CHARS) \
		--content-pdf-max-pages $(CONTENT_PDF_MAX_PAGES) \
		--content-pptx-max-slides $(CONTENT_PPTX_MAX_SLIDES) \
		--content-xlsx-max-strings $(CONTENT_XLSX_MAX_STRINGS) \
		--content-xlsx-max-chars $(CONTENT_XLSX_MAX_CHARS) \
		--content-zip-max-members $(CONTENT_ZIP_MAX_MEMBERS) \
		--content-binary-max-bytes $(CONTENT_BINARY_MAX_BYTES) \
		--preflight-max-empty-pdf-ratio $(PREFLIGHT_MAX_EMPTY_PDF_RATIO) \
		--preflight-empty-pdf-min-docs $(PREFLIGHT_EMPTY_PDF_MIN_DOCS) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--skip-qdrant \
		--chunk-mode $(CHUNK_MODE) \
		--chunk-chars $(CHUNK_CHARS) \
		--chunk-overlap $(CHUNK_OVERLAP) \
		--chunk-tokens $(CHUNK_TOKENS) \
		--chunk-overlap-tokens $(CHUNK_OVERLAP_TOKENS) \
		--tokenizer-model $(TOKENIZER_MODEL)

.PHONY: ingest-vectors
ingest-vectors:
	$(PYTHON) ingest_worker.py \
		--routerkit-documents $(ROUTERKIT_DOCS) \
		--content-source $(CONTENT_SOURCE) \
		--filesystem-on-miss $(FILESYSTEM_ON_MISS) \
		--corpus-root $(CORPUS_ROOT) \
		--content-sample-mode $(CONTENT_SAMPLE_MODE) \
		--content-max-chars $(CONTENT_MAX_CHARS) \
		--content-pdf-max-pages $(CONTENT_PDF_MAX_PAGES) \
		--content-pptx-max-slides $(CONTENT_PPTX_MAX_SLIDES) \
		--content-xlsx-max-strings $(CONTENT_XLSX_MAX_STRINGS) \
		--content-xlsx-max-chars $(CONTENT_XLSX_MAX_CHARS) \
		--content-zip-max-members $(CONTENT_ZIP_MAX_MEMBERS) \
		--content-binary-max-bytes $(CONTENT_BINARY_MAX_BYTES) \
		--preflight-max-empty-pdf-ratio $(PREFLIGHT_MAX_EMPTY_PDF_RATIO) \
		--preflight-empty-pdf-min-docs $(PREFLIGHT_EMPTY_PDF_MIN_DOCS) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--vector-dim $(VECTOR_DIM) \
		--embed-batch-size $(EMBED_BATCH_SIZE) \
		--artifact-embed-batch-size $(ARTIFACT_EMBED_BATCH_SIZE) \
		--embed-max-attempts $(EMBED_MAX_ATTEMPTS) \
		--embed-retry-backoff-sec $(EMBED_RETRY_BACKOFF_SEC) \
		--chunk-mode $(CHUNK_MODE) \
		--chunk-chars $(CHUNK_CHARS) \
		--chunk-overlap $(CHUNK_OVERLAP) \
		--chunk-tokens $(CHUNK_TOKENS) \
		--chunk-overlap-tokens $(CHUNK_OVERLAP_TOKENS) \
		--tokenizer-model $(TOKENIZER_MODEL) \
		--skip-es \
		--skip-neo4j

.PHONY: index-graphrag
index-graphrag:
	$(PYTHON) scripts/graphrag_index.py \
		--es-url $(ES_URL) \
		--es-doc-index $(ES_DOC_INDEX) \
		--es-chunk-index $(ES_CHUNK_INDEX) \
		--es-graphrag-index $(ES_GRAPHRAG_INDEX) \
		--qdrant-url $(QDRANT_URL) \
		--qdrant-graphrag-collection $(QDRANT_GRAPHRAG_COLLECTION) \
		--qdrant-upsert-batch-size $(GRAPHRAG_QDRANT_UPSERT_BATCH_SIZE) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--vector-dim $(VECTOR_DIM) \
		--embed-batch-size $(GRAPHRAG_EMBED_BATCH_SIZE) \
		--embed-max-attempts $(EMBED_MAX_ATTEMPTS) \
		--embed-retry-backoff-sec $(EMBED_RETRY_BACKOFF_SEC) \
		--community-method $(GRAPHRAG_COMMUNITY_METHOD) \
		--community-resolution $(GRAPHRAG_COMMUNITY_RESOLUTION) \
		--community-min-shared-entities $(GRAPHRAG_COMMUNITY_MIN_SHARED_ENTITIES) \
		--community-min-docs $(GRAPHRAG_COMMUNITY_MIN_DOCS) \
		--community-max-reports $(GRAPHRAG_COMMUNITY_MAX_REPORTS) \
		--router-taxonomy-json $(TAXONOMY_ROUTER_JSON) \
		--bucket-cards-jsonl $(TAXONOMY_BUCKET_CARDS) $(GRAPHRAG_LLM_FLAGS) \
		$(if $(filter 1 true TRUE yes YES,$(GRAPHRAG_QDRANT_UPSERT_WAIT)),--qdrant-upsert-wait,--no-qdrant-upsert-wait)

.PHONY: index-graphrag-llm
index-graphrag-llm:
	$(MAKE) index-graphrag GRAPHRAG_ENABLE_LLM=1

.PHONY: ingest-graphrag
ingest-graphrag: index-graphrag

.PHONY: backfill-graphrag-vectors-lowbatch
backfill-graphrag-vectors-lowbatch:
	$(PYTHON) scripts/graphrag_index.py \
		--skip-es \
		--skip-taxonomy-neo4j \
		--community-method $(GRAPHRAG_COMMUNITY_METHOD) \
		--community-resolution $(GRAPHRAG_COMMUNITY_RESOLUTION) \
		--community-min-shared-entities $(GRAPHRAG_COMMUNITY_MIN_SHARED_ENTITIES) \
		--community-min-docs $(GRAPHRAG_COMMUNITY_MIN_DOCS) \
		--community-max-reports $(GRAPHRAG_COMMUNITY_MAX_REPORTS) \
		--es-url $(ES_URL) \
		--es-doc-index $(ES_DOC_INDEX) \
		--es-chunk-index $(ES_CHUNK_INDEX) \
		--qdrant-url $(QDRANT_URL) \
		--qdrant-graphrag-collection $(QDRANT_GRAPHRAG_COLLECTION) \
		--qdrant-upsert-batch-size $(GRAPHRAG_QDRANT_UPSERT_BATCH_SIZE) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--vector-dim $(VECTOR_DIM) \
		--embed-batch-size $(GRAPHRAG_EMBED_BATCH_SIZE) \
		--embed-max-attempts $(EMBED_MAX_ATTEMPTS) \
		--embed-retry-backoff-sec $(EMBED_RETRY_BACKOFF_SEC) \
		--router-taxonomy-json $(TAXONOMY_ROUTER_JSON) \
		--bucket-cards-jsonl $(TAXONOMY_BUCKET_CARDS) $(GRAPHRAG_LLM_FLAGS) \
		$(if $(filter 1 true TRUE yes YES,$(GRAPHRAG_QDRANT_UPSERT_WAIT)),--qdrant-upsert-wait,--no-qdrant-upsert-wait)

.PHONY: ingest-taxonomy
ingest-taxonomy:
	$(PYTHON) scripts/graphrag_index.py \
		--taxonomy-only \
		--es-url $(ES_URL) \
		--es-doc-index $(ES_DOC_INDEX) \
		--es-chunk-index $(ES_CHUNK_INDEX) \
		--es-graphrag-index $(ES_GRAPHRAG_INDEX) \
		--qdrant-url $(QDRANT_URL) \
		--qdrant-graphrag-collection $(QDRANT_GRAPHRAG_COLLECTION) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--vector-dim $(VECTOR_DIM) \
		--embed-batch-size $(GRAPHRAG_EMBED_BATCH_SIZE) \
		--embed-max-attempts $(EMBED_MAX_ATTEMPTS) \
		--embed-retry-backoff-sec $(EMBED_RETRY_BACKOFF_SEC) \
		--router-taxonomy-json $(TAXONOMY_ROUTER_JSON) \
		--bucket-cards-jsonl $(TAXONOMY_BUCKET_CARDS)

.PHONY: ingest-incremental
ingest-incremental:
	$(PYTHON) ingest_worker.py \
		--routerkit-documents $(ROUTERKIT_DOCS) \
		--content-source $(CONTENT_SOURCE) \
		--filesystem-on-miss $(FILESYSTEM_ON_MISS) \
		--corpus-root $(CORPUS_ROOT) \
		--content-sample-mode $(CONTENT_SAMPLE_MODE) \
		--content-max-chars $(CONTENT_MAX_CHARS) \
		--content-pdf-max-pages $(CONTENT_PDF_MAX_PAGES) \
		--content-pptx-max-slides $(CONTENT_PPTX_MAX_SLIDES) \
		--content-xlsx-max-strings $(CONTENT_XLSX_MAX_STRINGS) \
		--content-xlsx-max-chars $(CONTENT_XLSX_MAX_CHARS) \
		--content-zip-max-members $(CONTENT_ZIP_MAX_MEMBERS) \
		--content-binary-max-bytes $(CONTENT_BINARY_MAX_BYTES) \
		--preflight-max-empty-pdf-ratio $(PREFLIGHT_MAX_EMPTY_PDF_RATIO) \
		--preflight-empty-pdf-min-docs $(PREFLIGHT_EMPTY_PDF_MIN_DOCS) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--vector-dim $(VECTOR_DIM) \
		--embed-batch-size $(EMBED_BATCH_SIZE) \
		--artifact-embed-batch-size $(ARTIFACT_EMBED_BATCH_SIZE) \
		--embed-max-attempts $(EMBED_MAX_ATTEMPTS) \
		--embed-retry-backoff-sec $(EMBED_RETRY_BACKOFF_SEC) \
		--chunk-mode $(CHUNK_MODE) \
		--chunk-chars $(CHUNK_CHARS) \
		--chunk-overlap $(CHUNK_OVERLAP) \
		--chunk-tokens $(CHUNK_TOKENS) \
		--chunk-overlap-tokens $(CHUNK_OVERLAP_TOKENS) \
		--tokenizer-model $(TOKENIZER_MODEL) \
		--incremental \
		--state-file $(INGEST_STATE_FILE)

.PHONY: identify-weak-extractions
identify-weak-extractions:
	$(PYTHON) scripts/identify_weak_extractions.py \
		--es-url $(ES_URL) \
		--es-doc-index $(ES_DOC_INDEX) \
		--score-threshold $(WEAK_EXTRACTION_THRESHOLD) \
		--output-jsonl $(WEAK_EXTRACTIONS_REPORT) \
		--routerkit-documents $(ROUTERKIT_DOCS) \
		--write-routerkit-subset $(WEAK_ROUTERKIT_SUBSET)

.PHONY: smoke-retrieval
smoke-retrieval:
	$(PYTHON) scripts/smoke_test_queries.py \
		--query "network security patch procedure" \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--embed-dim $(VECTOR_DIM)

.PHONY: verify-ingest
verify-ingest:
	$(PYTHON) scripts/verify_ingest_counts.py \
		--routerkit-documents $(ROUTERKIT_DOCS) \
		--chunk-mode $(VERIFY_CHUNK_MODE) \
		--chunk-chars $(CHUNK_CHARS) \
		--chunk-overlap $(CHUNK_OVERLAP) \
		--chunk-tokens $(CHUNK_TOKENS) \
		--chunk-overlap-tokens $(CHUNK_OVERLAP_TOKENS) \
		--expected-chunks-source $(VERIFY_EXPECTED_CHUNKS_SOURCE) \
		--allow-doc-drift $(VERIFY_ALLOW_DOC_DRIFT) \
		--allow-chunk-drift $(VERIFY_ALLOW_CHUNK_DRIFT) \
		--tokenizer-model $(TOKENIZER_MODEL) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--neo4j-uri $(NEO4J_URI) \
		--neo4j-user $(NEO4J_USER) \
		--neo4j-password $(NEO4J_PASSWORD)

.PHONY: eval-retrieval
eval-retrieval:
	$(PYTHON) scripts/eval_retrieval.py \
		--eval-file $(RETRIEVAL_EVAL_FILE) \
		--backend $(EVAL_RETRIEVAL_BACKEND) \
		--query-api-url $(QUERY_API_URL) \
		--mode $(QUERY_MODE) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--embed-dim $(VECTOR_DIM)

.PHONY: calibrate-retrieval
calibrate-retrieval:
	$(PYTHON) scripts/calibrate_retrieval.py \
		--eval-file $(RETRIEVAL_EVAL_FILE) \
		--backend $(EVAL_RETRIEVAL_BACKEND) \
		--query-api-url $(QUERY_API_URL) \
		--mode $(QUERY_MODE) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--embed-dim $(VECTOR_DIM)

.PHONY: validate-full
validate-full:
	mkdir -p $(VALIDATION_REPORT_DIR)
	bash -lc 'set -euo pipefail; \
	$(PYTHON) scripts/eval_retrieval.py \
		--eval-file $(RETRIEVAL_EVAL_FILE) \
		--backend $(EVAL_RETRIEVAL_BACKEND) \
		--query-api-url $(QUERY_API_URL) \
		--mode $(QUERY_MODE) \
		--es-url $(ES_URL) \
		--qdrant-url $(QDRANT_URL) \
		--embed-url $(EMBED_URL) \
		--embed-model $(EMBED_MODEL) \
		--embed-dim $(VECTOR_DIM) \
		--output-file $(VALIDATION_REPORT_DIR)/retrieval_eval.json \
		--min-pass-rate $(RETRIEVAL_EVAL_MIN_PASS_RATE) \
		--fail-on-gates'
	$(PYTHON) evals/v2/eval_agent_v2.py \
		--tasks $(V2_EVAL_TASKS) \
		--url $(V2_API_URL) \
		--output $(VALIDATION_REPORT_DIR)/agent_eval_v2.json

# ===================================================================
# Dearly Departed v2 targets
# ===================================================================

.PHONY: up-v2 down-v2 health-v2 smoke-v2 eval-v2 up-observability

up-v2: ## Start full v2 stack (core + models + query-api + agent-v2 + frontend-v2)
	$(COMPOSE) --profile app-v2 up -d --build

down-v2: ## Stop v2 services
	$(COMPOSE) --profile app-v2 down

up-observability: ## Start Langfuse observability stack
	$(COMPOSE) --profile observability up -d

health-v2: ## Health check v2 services
	@curl -sf $(V2_API_URL)/v2/health | python3 -m json.tool || echo "v2 health check failed"

smoke-v2: ## Quick v2 agent smoke test
	@echo "=== v2 SSE Smoke Test ==="
	@curl -sN -X POST $(V2_API_URL)/v2/ask/stream \
		-H 'Content-Type: application/json' \
		-d '{"query":"What backup systems are in use?","top_k":5}' \
		| head -50
	@echo ""
	@echo "=== v2 JSON Smoke Test ==="
	@curl -sf -X POST $(V2_API_URL)/v2/ask \
		-H 'Content-Type: application/json' \
		-d '{"query":"What backup systems are in use?","top_k":5}' \
		| python3 -m json.tool | head -40

eval-v2: ## Run v2 agent eval suite
	$(PYTHON) evals/v2/eval_agent_v2.py \
		--tasks $(V2_EVAL_TASKS) \
		--url $(V2_API_URL) \
		--output reports/eval_v2_$(shell date +%Y%m%d_%H%M%S).json

# ===================================================================
# Regression test suite (5 × 2500 LLM-generated questions + traces)
# ===================================================================

REGRESSION_DIR ?= evals/v2/regression
REGRESSION_REPORT_DIR ?= reports/regression
REGRESSION_SETS ?= 5
REGRESSION_PER_SET ?= 2500
REGRESSION_CONCURRENCY ?= 5
REGRESSION_RUNNER_CONCURRENCY ?= 1
REGRESSION_TIMEOUT ?= 300

.PHONY: generate-regression regression-set-1 regression-set-2 regression-set-3 regression-set-4 regression-set-5 regression-full regression-quick

generate-regression: ## Generate 5 × 2500 question sets from ES chunks via LLM
	mkdir -p $(REGRESSION_DIR)
	$(PYTHON) scripts/generate_regression_questions.py \
		--output-dir $(REGRESSION_DIR) \
		--sets $(REGRESSION_SETS) \
		--per-set $(REGRESSION_PER_SET) \
		--es-url $(ES_URL) \
		--concurrency $(REGRESSION_CONCURRENCY)

regression-set-1: ## Run regression set 1 with trace capture
	mkdir -p $(REGRESSION_REPORT_DIR)
	$(PYTHON) scripts/run_regression_traces.py \
		--tasks $(REGRESSION_DIR)/set_1.jsonl \
		--url $(V2_API_URL) \
		--output $(REGRESSION_REPORT_DIR)/set_1_traces.jsonl \
		--concurrency $(REGRESSION_RUNNER_CONCURRENCY) \
		--timeout $(REGRESSION_TIMEOUT) \
		--resume

regression-set-2: ## Run regression set 2 with trace capture
	mkdir -p $(REGRESSION_REPORT_DIR)
	$(PYTHON) scripts/run_regression_traces.py \
		--tasks $(REGRESSION_DIR)/set_2.jsonl \
		--url $(V2_API_URL) \
		--output $(REGRESSION_REPORT_DIR)/set_2_traces.jsonl \
		--concurrency $(REGRESSION_RUNNER_CONCURRENCY) \
		--timeout $(REGRESSION_TIMEOUT) \
		--resume

regression-set-3: ## Run regression set 3 with trace capture
	mkdir -p $(REGRESSION_REPORT_DIR)
	$(PYTHON) scripts/run_regression_traces.py \
		--tasks $(REGRESSION_DIR)/set_3.jsonl \
		--url $(V2_API_URL) \
		--output $(REGRESSION_REPORT_DIR)/set_3_traces.jsonl \
		--concurrency $(REGRESSION_RUNNER_CONCURRENCY) \
		--timeout $(REGRESSION_TIMEOUT) \
		--resume

regression-set-4: ## Run regression set 4 with trace capture
	mkdir -p $(REGRESSION_REPORT_DIR)
	$(PYTHON) scripts/run_regression_traces.py \
		--tasks $(REGRESSION_DIR)/set_4.jsonl \
		--url $(V2_API_URL) \
		--output $(REGRESSION_REPORT_DIR)/set_4_traces.jsonl \
		--concurrency $(REGRESSION_RUNNER_CONCURRENCY) \
		--timeout $(REGRESSION_TIMEOUT) \
		--resume

regression-set-5: ## Run regression set 5 with trace capture
	mkdir -p $(REGRESSION_REPORT_DIR)
	$(PYTHON) scripts/run_regression_traces.py \
		--tasks $(REGRESSION_DIR)/set_5.jsonl \
		--url $(V2_API_URL) \
		--output $(REGRESSION_REPORT_DIR)/set_5_traces.jsonl \
		--concurrency $(REGRESSION_RUNNER_CONCURRENCY) \
		--timeout $(REGRESSION_TIMEOUT) \
		--resume

regression-full: ## Run all 5 regression sets sequentially
	$(MAKE) regression-set-1
	$(MAKE) regression-set-2
	$(MAKE) regression-set-3
	$(MAKE) regression-set-4
	$(MAKE) regression-set-5

regression-quick: ## Run first 50 questions from set 1 (smoke test)
	mkdir -p $(REGRESSION_REPORT_DIR)
	head -50 $(REGRESSION_DIR)/set_1.jsonl > $(REGRESSION_DIR)/set_1_quick.jsonl
	$(PYTHON) scripts/run_regression_traces.py \
		--tasks $(REGRESSION_DIR)/set_1_quick.jsonl \
		--url $(V2_API_URL) \
		--output $(REGRESSION_REPORT_DIR)/set_1_quick_traces.jsonl \
		--concurrency $(REGRESSION_RUNNER_CONCURRENCY) \
		--timeout $(REGRESSION_TIMEOUT)
