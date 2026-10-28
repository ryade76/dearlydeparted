# Dearly Departed — Developer Guide

## 1. System Overview

Dearly Departed is a routing-first GraphRAG RAG system over a corpus captured from a departing employee's files. Documents are classified through a calibrated 15-domain taxonomy before retrieval, and that classification structures how retrieval works downstream.

```
                                ┌─────────────────┐
                                │   Raw Corpus     │
                                │  Captured docs   │
                                └────────┬─────────┘
                                         │
                          Phase A: Offline Calibration
                          (corpus_routerkit.py — deterministic)
                                         │
                                         ▼
                                ┌─────────────────┐
                                │ documents.jsonl  │
                                │ router.json      │
                                │ bucket_cards     │
                                └────────┬─────────┘
                                         │
                    Phase A.5: Staged Ingest + GraphRAG Indexing
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
           ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
           │ Elasticsearch │    │    Qdrant     │    │    Neo4j      │
           │  BM25 chunks  │    │  Dense vecs   │    │  Graph +      │
           │  GraphRAG     │    │  4096-dim      │    │  Taxonomy     │
           └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
                  │                    │                    │
                  └────────────────────┼────────────────────┘
                                       │
                            Phase B: Online Retrieval
                      ┌────────────────┴────────────────┐
                      │                                 │
               ┌──────┴──────┐                   ┌──────┴──────┐
               │  query-api  │                   │ agent-api-v2│
               │  port 8080  │◄──────────────────│  port 8082  │
               │  /retrieve  │   retrieval call  │  /v2/ask    │
               └─────────────┘                   └──────┬──────┘
                                                        │
                                                 ┌──────┴──────┐
                                                 │ frontend-v2 │
                                                 │  port 3001  │
                                                 │  React+TW   │
                                                 └─────────────┘
```

### Services (8 containers)

| Service | Image | Port | Profile |
|---|---|---|---|
| elasticsearch | `elasticsearch:8.15.3` | 9200 | core, app-v2 |
| qdrant | `qdrant:v1.13.4` | 6333, 6334 | core, app-v2 |
| neo4j | `neo4j:5.26` | 7474, 7687 | core, app-v2 |
| vllm-embed | NGC vLLM (Qwen3-Embedding-8B) | 8001 | models, app-v2 |
| vllm-rerank | NGC vLLM (Qwen3-Reranker-8B) | 8002 | models, app-v2 |
| query-api | `dearly-departed-app:local` | 8080 | app-v2 |
| agent-api-v2 | `dearly-departed-app:local` | 8082 | app-v2 |
| frontend-v2 | `dearly-departed-frontend-v2:local` | 3001 | app-v2 |

### Data Stores

| Store | Indices / Collections | Purpose |
|---|---|---|
| Elasticsearch | `kb_docs`, `kb_chunks`, `kb_graphrag_artifacts` | Lexical/BM25 search + document metadata |
| Qdrant | `kb_chunks_vec`, `kb_graphrag_artifacts_vec` | Dense vector search (4096-dim, cosine) |
| Neo4j | Document, Chunk, Domain, Bucket, Entity, TaxonomyRoot, FirstBucket, SubBucket, Card, Community nodes | Graph structure + taxonomy hierarchy |

---

## 2. Quick Start

### Prerequisites

- Docker with Compose v2
- NVIDIA GPU with Docker GPU runtime (for model services)
- Python 3.10+ with venv (for pipeline scripts)
- Node 20+ (for frontend development only)

### First-time setup

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — required: OPENAI_API_KEY, HUGGING_FACE_HUB_TOKEN, NEO4J_PASSWORD

# 2. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_pipeline.txt

# 3. Start full v2 stack (core + models + query-api + agent-v2 + frontend-v2)
make up-v2

# 4. Create indices and schemas
make bootstrap

# 5. Ingest documents (requires vllm-embed healthy)
make ingest-full

# 6. Build GraphRAG artifacts + taxonomy
make index-graphrag

# 7. Verify everything
make health
make smoke-retrieval
make smoke-v2
```

See [QUICKSTART.md](../QUICKSTART.md) for the full guide — including bringing your own corpus, troubleshooting, and API examples.

---

## 3. API Reference — Query API (port 8080)

Source: `services/query_api.py`

### `POST /retrieve`

Taxonomy-aware multi-source retrieval with RRF fusion, reranking, and intent-aware rebalancing.

#### Request (`RetrieveRequest`)

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | **required** | Search query text |
| `domain` | string | `""` | Filter to a specific domain (e.g. `"network_security"`) |
| `mode` | string | `"auto"` | Retrieval mode: `basic`, `local`, `global`, `drift`, `auto` |
| `top_k` | int | 8 | Number of results to return (1–100) |
| `include_graph` | bool | `true` | Include graph summary in response |
| `decompose` | bool | `true` | Enable query decomposition |
| `source_path_hint` | string | `""` | Hint a specific source document path |
| `diagnostics_include_rank_trace` | bool | `false` | Include per-hit ranking trace in diagnostics |
| `date_from` | string | `""` | Date filter start (ISO format) |
| `date_to` | string | `""` | Date filter end (ISO format) |
| `temporal_basis` | string | `"effective"` | Date field to filter on: `effective` or `mtime` |
| `temporal_boost` | string | `""` | Temporal boost mode: `""`, `recency`, `none` |
| `graphrag_generation_mode` | string | `"both"` | GraphRAG filter: `both`, `llm`, `deterministic` |
| `graphrag_use_llm` | bool | `true` | Include LLM-generated GraphRAG artifacts |
| `graphrag_use_deterministic` | bool | `true` | Include deterministic GraphRAG artifacts |
| `graphrag_artifact_types` | list[string] | `[]` | Restrict to specific artifact types (empty = all) |

#### Response (`RetrieveResponse`)

```json
{
  "query": "network security procedures",
  "domain": "network_security",
  "mode": "local",
  "top_k": 10,
  "graphrag_generation_mode": "both",
  "graphrag_use_llm": true,
  "graphrag_use_deterministic": true,
  "rerank_applied": true,
  "hit_count": 10,
  "hits": [ /* see hit structure below */ ],
  "graph": { /* see graph structure below */ },
  "decomposition": { /* see decomposition structure below */ },
  "diagnostics": { /* see diagnostics structure below */ }
}
```

**Hit structure** (`Dict[str, Any]`):

| Field | Type | Description |
|---|---|---|
| `chunk_id` | string | Chunk ID (`{doc_id}:{chunk_index}`) |
| `artifact_id` | string | GraphRAG artifact ID (empty for chunks) |
| `artifact_type` | string | Type: `""` for chunks, or `entity_description`, `relationship`, etc. |
| `path` | string | Source document path |
| `text` / `text_preview` | string | Content text or preview |
| `semantic_domain` | string | Classified domain |
| `first_bucket` | string | First-level taxonomy bucket |
| `sub_bucket` | string | Sub-level taxonomy bucket |
| `fusion_score` | float | RRF fusion score |
| `rerank_score` | float | Reranker score (0 if not reranked) |
| `sources` | list | Retrieval sources that found this hit |
| `retrieval_mode` | string | Retrieval mode that includes this hit type |
| `mtime_date` | string | Document modification date |
| `effective_date` | string | Document effective date |

**Graph structure** (from `graph_summary()`):

```json
{
  "top_domains": [{"domain": "network_security", "count": 42}],
  "top_entities": [{"label": "system", "name": "firewall", "count": 15}],
  "top_communities": [{"community_id": "1", "scope": "domain", "count": 12}],
  "top_taxonomy_first_buckets": [{"first_bucket": "security", "count": 8}],
  "top_taxonomy_sub_buckets": [{"sub_bucket": "patch_mgmt", "count": 5}],
  "temporal_counts": {"2023": 45, "2024": 120}
}
```

**Decomposition structure**:

```json
{
  "original_query": "...",
  "sub_queries": ["sub query 1", "sub query 2"],
  "domain_hints": ["network_security"],
  "mode_hints": ["local"],
  "bucket_hints": ["security"],
  "decomposer_method": "lexical+llm"
}
```

**Diagnostics structure** (when `QUERY_DIAGNOSTICS_ENABLED=1`):

```json
{
  "intent_profile": {"primary": "factual_lookup", "all": [...]},
  "candidate_mix": {
    "initial": {"chunks": 40, "artifacts": 20},
    "post_rescue": {"chunks": 50, "artifacts": 20},
    "pre_rerank": {"total": 70},
    "post_rerank": {"total": 40},
    "final": {"total": 10}
  },
  "rebalancing": {"applied": true, "text_floor_enforced": true},
  "rescue_steps": [{"type": "basic_mode", "hits_added": 5}]
}
```

#### Example

```bash
curl --noproxy '*' -X POST http://localhost:8080/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the network security patch procedures?",
    "mode": "auto",
    "top_k": 10
  }'
```

### `GET /health`

Returns service health status including backend connectivity, decomposer state, and retrieval controls.

```bash
curl --noproxy '*' http://localhost:8080/health
```

---

## 4. API Reference — Agent API v2 (port 8082)

Source: `services/v2/api.py`

The v2 agent implements a streaming state-machine architecture with SSE events, multi-turn conversation memory, and two-pass citation verification.

### `POST /v2/ask/stream` — SSE Streaming

Primary endpoint. Returns Server-Sent Events stream with real-time thinking, tool calls, generation tokens, and verified citations.

### `POST /v2/ask` — JSON Response

Non-streaming variant. Collects all SSE events internally, returns final `AskResponseV2` JSON.

#### Request (`AskRequestV2`)

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | **required** | Question text |
| `conversation_id` | string | `""` | Link to prior conversation (enables multi-turn) |
| `domain` | string | `""` | Domain filter hint |
| `mode` | string | `"auto"` | Retrieval mode: `basic`, `local`, `global`, `drift`, `auto` |
| `top_k` | int | 40 | Number of evidence items to retrieve |
| `source_path_hint` | string | `""` | Hint a specific source document |
| `date_from` | string | `""` | Date filter start |
| `date_to` | string | `""` | Date filter end |
| `temporal_basis` | string | `"effective"` | Date field: `effective` or `mtime` |
| `temporal_inference` | string | `"auto"` | Temporal inference: `auto` or `off` |
| `graphrag_generation_mode` | string | `"both"` | GraphRAG filter: `both`, `llm`, `deterministic` |
| `graphrag_artifact_types` | list[string] | `[]` | Restrict artifact types |
| `max_context_tokens` | int | 350000 | Token budget for evidence context |
| `max_completion_tokens` | int | 8000 | Max LLM output tokens |
| `strategy` | string | `"auto"` | Execution strategy: `auto`, `react`, `plan_execute` |
| `max_tool_calls` | int | 15 | Max tool invocations allowed |
| `include_thinking` | bool | `true` | Include reasoning in SSE stream |
| `include_tool_details` | bool | `true` | Include tool call details in stream |
| `temperature` | float | 0.1 | LLM temperature |
| `top_p` | float | 0.9 | LLM top-p |
| `user` | string | `""` | User identifier for tracking |

#### Response (`AskResponseV2`)

```json
{
  "answer": "The network security patch procedures are documented in...",
  "citations": [
    {
      "index": 1,
      "item_id": "abc123:0",
      "path": "policies/network_security_patch.docx",
      "text_excerpt": "First 200 chars of evidence...",
      "verified": true,
      "verification_method": "token_overlap",
      "verification_score": 0.85,
      "domain": "network_security",
      "bucket": "security",
      "date": "2024-12-15"
    }
  ],
  "retrieval": {
    "tool_calls": 3,
    "evidence_count": 42,
    "context_tokens": 125000
  },
  "model": "GPT-5.2",
  "trace_id": "a1b2c3d4e5f6",
  "timing": {
    "classify": 450,
    "react": 3200,
    "synthesize": 120,
    "generate": 8500,
    "verify": 1200,
    "emit_final": 50
  },
  "diagnostics": {
    "intent": "factual_lookup",
    "complexity": "moderate",
    "strategy": "react",
    "verification_passed": true
  }
}
```

#### Citation Verification Methods

| Method | Description |
|---|---|
| `token_overlap` | Passed via token overlap (score ≥ 0.30) |
| `llm_nli` | Passed via LLM natural language inference (borderline 0.15–0.30) |
| `no_evidence` | Citation references non-existent evidence |
| `nli_failed` | LLM NLI call failed |

#### Examples

```bash
# JSON response
curl --noproxy '*' -X POST http://localhost:8082/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "What backup systems are in use?", "top_k": 40}'

# SSE streaming
curl -sN -X POST http://localhost:8082/v2/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What backup systems are in use?", "top_k": 40}'

# Multi-turn with conversation
curl -X POST http://localhost:8082/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "Tell me more about those servers", "conversation_id": "<id>"}'
```

### Conversation Management

| Endpoint | Method | Description |
|---|---|---|
| `/v2/conversations` | POST | Create new conversation session |
| `/v2/conversations` | GET | List all conversations (paginated) |
| `/v2/conversations/{id}` | GET | Get conversation with all turns |
| `/v2/conversations/{id}` | PATCH | Update conversation title |
| `/v2/conversations/{id}` | DELETE | Delete conversation |

### Trace Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v2/trace/{trace_id}` | GET | Retrieve persisted trace |
| `/v2/traces` | GET | List recent traces (with limit/offset) |

### `GET /v2/health`

Checks Elasticsearch, Query API (Qdrant), Neo4j, and LLM connectivity.

---

## 5. Retrieval Pipeline Deep Dive

The query-api implements a multi-stage retrieval pipeline:

```
Query
  │
  ├─1─ Decomposition (lexical taxonomy matching + optional LLM)
  │    → sub-queries, domain/mode/bucket hints
  │
  ├─2─ Four-Source Parallel Retrieval
  │    ├─ ES BM25 chunks (kb_chunks)
  │    ├─ Qdrant dense chunks (kb_chunks_vec, 4096-dim cosine)
  │    ├─ ES GraphRAG artifacts (kb_graphrag_artifacts, filtered by retrieval_mode)
  │    └─ Qdrant GraphRAG artifacts (kb_graphrag_artifacts_vec, filtered by retrieval_mode)
  │    + Taxonomy hint sources (ES + Qdrant, first/sub bucket filters)
  │
  ├─3─ RRF Fusion (K=60, equal source weighting)
  │    → Merges all sources into unified ranked list
  │
  ├─4─ Rerank (Qwen3-Reranker-8B, top-40 candidate pool)
  │    → Neural reranking of fused candidates
  │
  ├─5─ Intent Rescue Passes (when text evidence is insufficient)
  │    → Domain/hint relaxation
  │    → Basic-mode retrieval pass
  │    → Row-shape intent-specific queries
  │    → Exact phrase + fuzzy fallback
  │    → Ownership role queries
  │    → ES lexical rescue
  │
  ├─6─ Intent-Aware Rebalancing (4-pass algorithm)
  │    → Pass 1: Text-evidence floor enforcement
  │    → Pass 2: Strict claim/taxonomy/tuple caps + path diversity
  │    → Pass 3: Relaxed saturation caps
  │    → Pass 4: Relaxed path diversity (last resort)
  │
  └─7─ Source-path floor enforcement + final top-k selection
```

### Query Decomposition

The decomposer uses two methods:

1. **Lexical taxonomy matching**: Tokenize query → match against bucket keywords (weighted: path=3, meta=2, sample=1) → domain scoring
2. **Optional LLM decomposition**: LLM call returns `{domain, mode, first_buckets[], sub_buckets[], confidence, reason}` — used when `QUERY_DECOMPOSER_USE_LLM=True` and confidence ≥ 0.45

Mode auto-selection: LLM mode (if confident) > local (if hints found) > basic (default). `QUERY_DECOMPOSER_UPGRADE_BASIC_MODE=True` allows basic→local/global/drift promotion.

### Retrieval Modes

| Mode | What it retrieves |
|---|---|
| `basic` | Document chunks only |
| `local` | Chunks + entities, relationships, claims, taxonomy cards |
| `global` | Chunks + community reports, domain profiles |
| `drift` | All of local + global |
| `auto` | Decomposer selects mode based on query analysis |

### Intent Classification

14 intent types classified via regex patterns:

| Intent | Trigger Pattern | min_text | max_claim | max_taxonomy | rescue_mode |
|---|---|---|---|---|---|
| `troubleshooting` | troubleshoot\|fail\|error\|fix\|resolve | 6 | 0.10 | 0.10 | phrase |
| `risk_detection` | risk\|threat\|vulnerability\|anomaly | 6 | 0.10 | 0.10 | phrase |
| `procedure_steps` | steps?\|procedure\|how to\|workflow | 6 | 0.10 | 0.12 | phrase |
| `compliance_constraint` | must\|required\|shall\|policy\|compliance | 6 | 0.08 | 0.10 | phrase |
| `ownership_role` | what role does.*have\|handle\|manage | 3 | 0.40 | 0.20 | phrase |
| `overview` | overview\|summary\|summarize\|trend | 3 | 0.72 | 0.90 | basic |
| `definition` | what is\|defined as\|definition\|abbreviation | 6 | 0.10 | 0.10 | phrase |
| `recency` | recent\|latest\|newest\|current | 6 | 0.10 | 0.10 | phrase |
| `factual_lookup` | ^(what\|who\|when\|where\|which\|how) | 6 | 0.08 | 0.08 | phrase |
| `timeline_version` | timeline\|chronolog\|version\|revision | 6 | 0.12 | 0.10 | phrase |
| `comparison` | compare\|versus\|vs\|difference | 5 | 0.25 | 0.20 | basic |
| `ownership` | owner\|responsible\|who owns | 4 | 0.22 | 0.22 | phrase |
| `synthesis` | synthesize\|combine\|architecture | 5 | 0.30 | 0.25 | basic |
| `lookup` (default) | fallback | 3 | 0.35 | 0.30 | — |

Intent drives rebalancing constraints: text-evidence floor (`min_text`), claim/taxonomy/tuple ratio caps, rescue strategy, and rerank pool size.

### GraphRAG Artifact Types

18 types compete in the retrieval pipeline:

**Core types**: `text_unit`, `entity_description`, `relationship`, `claim`, `community_report`

**Taxonomy types**: `taxonomy_first_bucket`, `taxonomy_sub_bucket`, `taxonomy_bucket_card`, `taxonomy_domain_profile`

**Structured types**: `log_entry`, `procedure_step`, `procedure_requirement`, `compliance_constraint`, `risk_signal`, `troubleshooting_tuple`

Artifacts are filtered by `retrieval_mode` field (not `artifact_type`). New types are automatically compatible as long as they set the correct `retrieval_mode` (basic/local/global).

---

## 6. V2 Agent Pipeline Deep Dive

The agent-api-v2 implements a state-machine architecture with 10 states:

```
User Query
    ↓
[CLASSIFY] — Determine intent, complexity, strategy via LLM
    ↓
    ├─ strategy="plan_execute"
    │   ↓
    │   [PLAN] — Generate multi-step execution plan via LLM
    │   ↓
    │   [EXECUTE] — DAG-based parallel tool execution
    │
    └─ strategy="react" (default)
        ↓
        [REACT] — Iterative tool-calling loop (max 5 iterations, max 20 tool calls)
            ↓
        [SYNTHESIZE] — Deduplicate + assemble evidence into token-budgeted context
            ↓
        [GENERATE] — LLM generates answer with streaming tokens (GPT-5.2 via an OpenAI-compatible API)
            ↓
        [VERIFY] — Two-pass citation verification
            ├─ Pass 1: Token overlap (threshold 0.30)
            ├─ Pass 2: LLM NLI for borderline (0.15–0.30)
            └─ If verification fails: retry GENERATE (max 3 retries)
            ↓
        [EMIT_FINAL] — Strip unverified citations, persist trace, save conversation turn
            ↓
        [DONE] — Terminal state
```

### AgentContext — Central Data Structure

Source: `services/v2/agent/states.py`

The `AgentContext` carries all state through the pipeline:

```python
class AgentContext(BaseModel):
    # Identity & Session
    trace_id: str              # Auto-generated uuid.uuid4().hex[:16]
    conversation_id: str       # From request (enables multi-turn)
    query: str                 # User question

    # Classification Output
    intent: str                # "factual_lookup", "synthesis", "comparison", etc.
    complexity: str            # "simple", "moderate", "complex"
    strategy: str              # "react" or "plan_execute"
    taxonomy_hints: Dict       # {domains: [...], entity_names: [...]}
    inferred_temporal_range: Dict  # {is_temporal, date_from, date_to}

    # Evidence Accumulation
    evidence: List[EvidenceItem]           # All retrieved evidence items
    graph_traversal_results: List[Dict]    # Neo4j traversal results
    entity_profiles: List[Dict]            # Entity profile enrichments
    tool_call_log: List[ToolCallRecord]    # Record of all tool calls

    # Generation & Verification
    assembled_context: str     # Formatted evidence for LLM
    context_token_count: int   # Tokens in assembled_context
    raw_answer: str            # LLM output (unverified)
    citations: List[CitationV2]  # Verified citations with scores
    verification_passed: bool  # Pass rate ≥ 0.5 or no citations
    retry_count: int           # Generation retry attempts

    # Output
    final_answer: str          # Final answer (unverified citations stripped)
    state_timings: Dict[str, float]  # Execution time per state (ms)
```

### State Handlers

#### CLASSIFY (`services/v2/agent/classify.py`)

Calls LLM (temperature=0.0, max_tokens=500) to classify:
- **Intent**: Query type (factual_lookup, synthesis, comparison, troubleshooting, etc.)
- **Complexity**: simple / moderate / complex
- **Strategy**: react (default) / plan_execute (for complex multi-step queries)
- **Taxonomy hints**: Relevant domains and entity names
- **Temporal range**: Auto-detected date scope

Conversation history (last 5 turns) is injected for multi-turn context. On JSON parse failure, defaults to factual_lookup / simple / react.

#### PLAN (`services/v2/agent/plan.py`)

Only runs when strategy="plan_execute". Generates a DAG of execution steps via LLM:

```python
class PlanStep(BaseModel):
    step_index: int          # Execution order
    description: str         # What this step accomplishes
    tool_name: str           # Registered tool name
    tool_args: Dict          # Arguments for tool
    depends_on: List[int]    # Step indices this depends on
    completed: bool          # Execution status
```

Falls back to ReAct strategy on parse failure.

#### EXECUTE (`services/v2/agent/execute.py`)

Runs plan steps with dependency resolution. Independent steps execute in parallel via `asyncio.gather()`. Circular dependencies are detected and handled gracefully (break loop). Respects `max_tool_calls` budget.

#### REACT (`services/v2/agent/react.py`)

Iterative tool-calling loop:

1. Build prompt with query, intent, taxonomy hints, conversation history, and tool schemas
2. LLM selects tools and arguments (JSON array)
3. Execute selected tools (parallel within iteration)
4. Feed results back to LLM for next iteration
5. Loop until: LLM returns `[]`, tool budget exhausted, or max iterations reached

**Defaults**: max 5 iterations (`V2_REACT_MAX_ITERATIONS`), max 20 tool calls (`V2_MAX_TOOL_CALLS`)

On first-iteration parse failure, falls back to `search_chunks(query, top_k=20)`.

#### SYNTHESIZE (`services/v2/agent/synthesize.py`)

Assembles evidence into structured context for the LLM:

1. **Deduplicate** by `item_id` (fallback key: `{path}:{text[:100]}`)
2. **Sort** by score descending
3. **Assemble sections** in priority order:
   - Taxonomy artifacts (bucket cards, domain profiles)
   - Entity profiles
   - Graph traversal results
   - GraphRAG artifacts (entities, relationships, claims, community reports)
   - Text chunks
   - Temporal context note
4. **Format** each item in `<evidence>` XML tags with metadata attributes
5. **Enforce token budget** (`V2_MAX_CONTEXT_TOKENS`, default 350,000; ~4 chars/token estimate)

Evidence format:
```xml
<evidence id="chunk_abc123" type="chunk" path="docs/network.md"
          domain="networking" bucket="switches" date="2024-01-15">
Evidence text here...
</evidence>
```

#### GENERATE (`services/v2/agent/generate.py`)

Builds the LLM message chain:

1. **System message**: `prompts/system_v2.txt` (evidence-based answering, citation format, temporal reasoning)
2. **Conversation history**: Prior turns as user/assistant message pairs
3. **Current turn**: `## Evidence Context\n\n{assembled_context}\n\n## Question\n\n{query}`
4. **Streaming**: Tokens streamed via `async_stream_complete()` with `generation_token` SSE events
5. **Fallback**: If streaming fails, falls back to sync `async_complete()`

LLM: GPT-5.2 via an OpenAI-compatible API (uses `max_completion_tokens`, not `max_tokens`). Default 8000 max output tokens.

#### VERIFY (`services/v2/agent/verify.py`)

Two-pass citation verification:

**Pass 1 — Token Overlap:**
- Extract `[evidence_id]` references from raw answer
- For each citation, compute token overlap between claim sentence and evidence text
- Overlap ≥ 0.30 → **VERIFIED** (method: `token_overlap`)
- 0.15 ≤ overlap < 0.30 → **BORDERLINE** (queued for NLI)
- overlap < 0.15 → **FAILED**

**Pass 2 — LLM NLI (borderline only):**
- Call the LLM with claim + evidence (capped at 500 and 2000 chars)
- Parse verdict: `supported` / `partially_supported` / `not_supported`
- Verified if verdict in (supported, partially_supported) AND confidence ≥ 0.5

**Retry logic**: If pass rate < 0.5 and retries remaining (`V2_VERIFY_MAX_RETRIES`, default 3), transitions back to GENERATE.

#### EMIT_FINAL (`services/v2/agent/emit.py`)

1. Strip unverified `[citation_id]` markers from raw answer
2. Build citation response objects (verified only)
3. Persist trace to `reports/traces/{trace_id}.json`
4. Auto-save conversation turn (if `conversation_id` set, auto-titles from first query)
5. Emit `answer_complete` SSE event

### State Machine Runner

Source: `services/v2/agent/runner.py`

```python
async def run_agent(ctx: AgentContext) -> AsyncGenerator[str, None]:
    while ctx.current_state not in (DONE, ERROR):
        handler = get_handler(ctx.current_state)
        async for event in handler(ctx):
            yield event  # Stream to client
        record_state_timing()
        yield state_transition_event()
```

Each handler is an `AsyncGenerator[str, None]` yielding SSE-formatted events. The runner passes events through transparently, enabling real-time streaming to clients.

---

## 7. V2 Agent Tools

Source: `services/v2/tools/`

Tools are registered via `@register_tool()` decorator with OpenAI function-calling schema. The REACT handler selects tools via LLM; the EXECUTE handler runs plan-specified tools.

### search_chunks

Hybrid BM25 + dense search via query-api `/retrieve`. Returns text passages, entity descriptions, relationships, and other artifacts.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Search query |
| `mode` | string | no | `"auto"` | Retrieval mode |
| `domain` | string | no | `""` | Domain filter |
| `top_k` | int | no | 40 | Number of results |
| `date_from` | string | no | `""` | Start date filter |
| `date_to` | string | no | `""` | End date filter |
| `source_path_hint` | string | no | `""` | Path hint to boost specific documents |

Stores: ES + Qdrant via query-api. Appends all hits to `ctx.evidence`.

### search_graphrag_artifacts

Search GraphRAG knowledge artifacts: entity descriptions, relationships, claims, community reports, taxonomy cards.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Search query |
| `artifact_types` | string[] | no | `[]` | Filter artifact types |
| `retrieval_mode` | string | no | `"local"` | Scope: local / global / drift |
| `domain` | string | no | `""` | Domain filter |
| `generation_mode` | string | no | `"both"` | Filter: both / llm / deterministic |
| `top_k` | int | no | 30 | Number of results |

Stores: ES + Qdrant via query-api. No query decomposition (`decompose=False`).

### temporal_search

Date-filtered retrieval for time-based queries.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Search query |
| `date_from` | string | yes | — | Start date (ISO) |
| `date_to` | string | yes | — | End date (ISO) |
| `temporal_basis` | string | no | `"effective"` | effective / mtime |
| `domain` | string | no | `""` | Domain filter |
| `top_k` | int | no | 40 | Number of results |
| `temporal_boost` | string | no | `""` | recency / none |

Stores: ES + Qdrant via query-api with date filters.

### entity_profile

Comprehensive entity profiling. Combines Neo4j graph relationships with ES entity descriptions and chunk mentions.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `entity_name` | string | yes | — | Entity to profile |
| `entity_type` | string | no | `""` | Type hint: person, system, vendor, project, location |
| `include_mentions` | bool | no | `true` | Include text chunks mentioning entity |

Multi-source composition: Neo4j co-occurrences + entity documents, GraphRAG descriptions + relationships (top-10 each), chunk mentions (top-25). Errors per-source handled gracefully. Profile stored in `ctx.entity_profiles`.

### traverse_graph

Neo4j graph traversal using 6 pre-defined Cypher templates:

| Traversal Type | Required Params | Returns |
|---|---|---|
| `entity_co_occurrences` | `entity_name` | Co-occurring entities with shared document counts |
| `entity_documents` | `entity_name` | All documents mentioning an entity |
| `domain_entities` | `domain` | All entities within a domain |
| `taxonomy_path` | `bucket` | Taxonomy hierarchy: first_bucket → sub-buckets → cards |
| `community_members` | `community_id` | Documents and entities in a community |
| `shared_documents` | `entity_name`, `entity_name_b` | Documents where two entities co-occur |

Additional params: `max_results` (default 50). Results stored in `ctx.graph_traversal_results` (not `ctx.evidence`).

### document_lookup

Full document retrieval by path or document ID.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | one required | Document path (wildcard match) |
| `doc_id` | string | one required | Document ID (exact match) |

Returns: full document metadata, all chunks (max 200), and GraphRAG artifacts (max 50). Queries ES directly (not via query-api). Chunks appended to `ctx.evidence`.

### search_taxonomy

Navigate the 15-domain taxonomy hierarchy.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Search query for taxonomy matching |
| `include_bucket_cards` | bool | no | `true` | Include detailed bucket descriptions |
| `include_domain_profiles` | bool | no | `false` | Include domain-level profiles |

Queries in-memory taxonomy index (loaded from `router.json` at startup). Lexical matching against bucket/domain keywords. Returns up to 20 taxonomy matches and 10 domain matches. Results appended to `ctx.evidence`.

### Tool Summary

| Tool | Primary Store | Query Type | Evidence Type |
|---|---|---|---|
| `search_chunks` | ES+Qdrant via query-api | Hybrid BM25+Dense | chunk, entity_description, relationship, etc. |
| `search_graphrag_artifacts` | ES+Qdrant via query-api | Dense (artifacts) | entity_description, relationship, claim, etc. |
| `temporal_search` | ES+Qdrant via query-api | Hybrid with date filter | chunk |
| `entity_profile` | Neo4j + ES+Qdrant | Multi-query composite | entity_description, relationship, chunk |
| `traverse_graph` | Neo4j | Cypher templates | (stored in graph_traversal_results) |
| `document_lookup` | ES direct | Direct ES queries | chunk |
| `search_taxonomy` | In-memory index | Lexical matching | taxonomy_bucket_card, taxonomy_domain_profile |

---

## 8. SSE Event Types

Source: `services/v2/sse/events.py`

Events are serialized as `event: {type}\ndata: {json}\n\n`.

| Event | Data Fields | Description |
|---|---|---|
| `session_start` | trace_id, query, timestamp | Stream begins |
| `state_transition` | from_state, to_state, duration_ms | State machine transition |
| `thinking` | content, structured | Agent reasoning (if include_thinking=true) |
| `tool_call_start` | tool_call_id, tool_name, arguments, reason | Tool invocation begins |
| `tool_call_result` | tool_call_id, tool_name, duration_ms, result_summary | Tool returns result |
| `tool_call_error` | tool_call_id, error | Tool execution failed |
| `context_assembled` | token_count, section_breakdown | Evidence context ready |
| `generation_start` | model, context_tokens | LLM generation begins |
| `generation_token` | token | Single streamed token |
| `generation_complete` | total_tokens, finish_reason | LLM generation done |
| `citation_verified` | citation_index, item_id, path, verified, method, confidence | Per-citation result |
| `verification_result` | total_citations, verified_count, pass_rate | Verification summary |
| `answer_complete` | answer, citations, timing, diagnostics, model | Final answer |
| `error` | error, state, recoverable | Unrecoverable error |
| `done` | — | Stream terminates |

---

## 9. Conversation Store

Source: `services/v2/conversations.py`

In-memory store with JSON file persistence for multi-turn conversations.

### Models

```python
class ConversationTurn(BaseModel):
    role: str           # "user" or "assistant"
    query: str          # User question
    answer: str         # Agent answer
    trace_id: str       # Link to persisted trace
    timestamp: float

class ConversationSession(BaseModel):
    id: str             # Auto-generated hex ID
    title: str          # Auto-set from first query[:60]
    created_at: float
    last_active_at: float
    turns: List[ConversationTurn]
```

### API

| Function | Description |
|---|---|
| `create_conversation(title)` | Create session, persist to disk |
| `get_conversation(id)` | Retrieve from memory |
| `list_conversations(limit, offset)` | Paginated list sorted by last_active_at desc |
| `add_turn(id, query, answer, trace_id)` | Add turn, auto-title, persist |
| `get_history_for_context(id)` | Prior turns as `{query, answer}` dicts for LLM injection |
| `delete_conversation(id)` | Remove from memory and disk |
| `update_title(id, title)` | Update title, persist |

Storage: `{TRACE_PERSIST_DIR}/conversations/{id}.json`

Multi-turn context: Prior turns are injected into CLASSIFY and REACT prompts as conversation history, and into GENERATE as native user/assistant message pairs.

---

## 10. Environment Variables

### Infrastructure

| Variable | Default | Description |
|---|---|---|
| `ES_URL` | `http://127.0.0.1:9200` | Elasticsearch URL |
| `QDRANT_PORT` | `6333` | Qdrant HTTP port |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt URI |
| `NEO4J_PASSWORD` | `change_me_local` | Neo4j auth password |
| `ES_JAVA_OPTS` | `-Xms4g -Xmx4g` | Elasticsearch JVM options |

### Model Services

| Variable | Default | Description |
|---|---|---|
| `HUGGING_FACE_HUB_TOKEN` | — | HF token for model downloads |
| `EMBED_MODEL` | `Qwen/Qwen3-Embedding-8B` | Embedding model |
| `EMBED_PORT` | `8001` | Embed service port |
| `EMBED_GPU_MEMORY_UTILIZATION` | `0.45` | GPU memory fraction |
| `RERANK_MODEL` | `Qwen/Qwen3-Reranker-8B` | Reranker model |
| `RERANK_PORT` | `8002` | Rerank service port |
| `RERANK_GPU_MEMORY_UTILIZATION` | `0.45` | GPU memory fraction |

### Chat Provider

| Variable | Default | Description |
|---|---|---|
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `OPENAI_API_KEY` | — | API key |
| `CHAT_MODEL` | `GPT-5.2` | LLM model ID |
| `CHAT_TEMPERATURE` | `0.1` | Generation temperature |
| `CHAT_TOP_P` | `0.9` | Top-p sampling |
| `CHAT_TIMEOUT_SEC` | `120` | Request timeout |
| `CHAT_MAX_ATTEMPTS` | `3` | Retry attempts |

### Query API

| Variable | Default | Description |
|---|---|---|
| `QUERY_API_PORT` | `8080` | Query API port |
| `QUERY_DEFAULT_TOP_K` | `10` | Default retrieval top-k |
| `QUERY_FUSION_K` | `60` | RRF fusion K parameter |
| `QUERY_CANDIDATE_POOL` | `40` | Candidate pool size for reranking |
| `QUERY_CANDIDATE_MAX` | `220` | Max candidate pool size |
| `RERANK_ENABLED` | `1` | Enable neural reranking |
| `RERANK_TIMEOUT_SEC` | `90` | Reranker timeout |
| `QUERY_DECOMPOSER_ENABLED` | `1` | Enable query decomposition |
| `QUERY_DECOMPOSER_USE_LLM` | `1` | Use LLM for decomposition |
| `QUERY_DECOMPOSER_MIN_CONFIDENCE` | `0.45` | Min LLM confidence for mode upgrade |
| `QUERY_DECOMPOSER_UPGRADE_BASIC_MODE` | `1` | Allow basic→local/global/drift promotion |
| `QUERY_DIAGNOSTICS_ENABLED` | `1` | Include diagnostics in response |
| `QUERY_REBALANCE_ENABLED` | `1` | Enable intent-aware rebalancing |
| `QUERY_INTENT_RESCUE_PASS` | `1` | Enable rescue passes for weak results |
| `QUERY_MIN_TEXT_EVIDENCE` | `4` | Minimum text-evidence hits in results |
| `QUERY_MAX_CLAIM_RATIO` | `0.50` | Max fraction of results that are claims |
| `QUERY_MAX_TAXONOMY_RATIO` | `0.50` | Max fraction that are taxonomy artifacts |
| `QUERY_MAX_PER_PATH` | `2` | Max hits per source document path |

### Agent API v2

| Variable | Default | Description |
|---|---|---|
| `V2_PORT` | `8082` | Agent API v2 port |
| `V2_MAX_CONTEXT_TOKENS` | `350000` | Token budget for evidence context |
| `V2_CHAT_MAX_COMPLETION_TOKENS` | `8000` | Max LLM output tokens |
| `V2_MAX_TOOL_CALLS` | `20` | Max tool invocations per query |
| `V2_REACT_MAX_ITERATIONS` | `5` | Max ReAct iterations |
| `V2_VERIFY_OVERLAP_THRESHOLD` | `0.30` | Token overlap threshold for verification |
| `V2_VERIFY_NLI_LOW` | `0.15` | Lower threshold for NLI queue |
| `V2_VERIFY_MAX_RETRIES` | `3` | Max generation retries on verification failure |
| `V2_TRACE_PERSIST_DIR` | `reports/traces` | Trace persistence directory |
| `QUERY_API_URL` | `http://127.0.0.1:8080` | Query API URL (for v2 tool calls) |
| `QUERY_API_TIMEOUT_SEC` | `120` | Query API call timeout |
| `TAXONOMY_ROUTER_JSON` | `kb_artifacts_.../router.json` | Taxonomy router path |

### Observability

| Variable | Default | Description |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | `""` | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | `""` | Langfuse secret key |
| `LANGFUSE_BASE_URL` | `http://localhost:3002` | Langfuse URL |

### Pipeline / Ingest

| Variable | Default | Description |
|---|---|---|
| `ROUTERKIT_DOCS` | `kb_artifacts_.../documents.jsonl` | Calibration output |
| `VECTOR_DIM` | `4096` | Embedding vector dimension |
| `CHUNK_TOKENS` | `1200` | Tokens per chunk |
| `CHUNK_OVERLAP_TOKENS` | `100` | Chunk overlap in tokens |
| `GRAPHRAG_ENABLE_LLM` | `0` | Enable LLM extraction during GraphRAG indexing |

---

## 11. Frontend Architecture

Source: `frontend-v2/`

### Tech Stack

- **React 19** + **TypeScript** — UI framework
- **Vite** — Build tool (requires Node 20+)
- **Tailwind CSS** — Utility-first styling
- **Zustand** — Client state with localStorage persistence

### Project Structure

```
frontend-v2/src/
  api/
    client.ts          # SSE streaming client, createConversation, deleteConversation
  stores/
    streamStore.ts     # SSE stream state (status, citations, finalAnswer)
    sessionStore.ts    # Chat sessions with localStorage persistence
    historyStore.ts    # Query history
  hooks/
    useAgentStream.ts  # SSE connection hook (ask, cancel)
  components/
    ChatView.tsx       # Multi-turn chat with past turns, auto-session creation
    SessionSidebar.tsx # Session list with create/switch/delete
    Layout.tsx         # App shell with toggleable sidebar
    StreamingMessage.tsx # Real-time token streaming display
    CitationCard.tsx   # Citation verification card
    QueryHistory.tsx   # Recent query history
```

### Session Management

Zustand store with localStorage persistence (key: `dd-chat-sessions`):

- Sessions auto-created on first query
- Auto-titled from first query (first 60 chars)
- Prior turns displayed when switching sessions
- `conversation_id` passed to `/v2/ask` for server-side multi-turn

### CORS Strategy

- **Development**: Vite `server.proxy` rewrites `/api/*` to backend ports
- **Production**: nginx reverse proxy in container serves static files and proxies to Docker internal network

---

## 12. Docker Compose Services

File: `docker-compose.dearly-departed.yml`

### Profiles

| Profile | Services | Purpose |
|---|---|---|
| `core` | elasticsearch, qdrant, neo4j | Data stores |
| `models` | vllm-embed, vllm-rerank | GPU model services |
| `app-v2` | All 8 services | Full v2 stack (self-contained) |
| `observability` | langfuse | Tracing dashboard |

### Dependency Chain

```
elasticsearch ─┐
 qdrant ────────┤
 neo4j ─────────┼─ query-api ─── agent-api-v2 ─── frontend-v2
 vllm-embed ────┤
 vllm-rerank ───┘
```

### Makefile Targets

#### Service Management

| Target | Description |
|---|---|
| `make up-v2` | Start full v2 stack (core + models + APIs + frontend) |
| `make up-core` | Start Elasticsearch, Qdrant, Neo4j |
| `make up-models` | Start embedding + reranking services |
| `make down` | Stop all services |
| `make ps` | Show service status |
| `make logs` | Tail compose logs |
| `make health` | Run health checks via `scripts/smoke_infra.sh` |
| `make health-v2` | V2 agent health check |

#### Data Pipeline

| Target | Description |
|---|---|
| `make bootstrap` | Create ES indices, Qdrant collections, Neo4j constraints |
| `make ingest-full` | Full ingest from documents.jsonl → ES + Qdrant + Neo4j |
| `make ingest-metadata` | Ingest docs/chunks into ES + Neo4j only (skip vectors) |
| `make ingest-vectors` | Ingest vectors into Qdrant only (requires embed service) |
| `make index-graphrag` | Build GraphRAG artifacts + taxonomy + communities |
| `make index-graphrag-llm` | Same with additive LLM extraction enabled |

#### Verification & Evaluation

| Target | Description |
|---|---|
| `make smoke-retrieval` | Quick retrieval smoke test |
| `make smoke-v2` | V2 agent smoke test |
| `make verify-ingest` | Check doc/chunk parity across ES, Qdrant, Neo4j |
| `make eval-retrieval` | Retrieval quality eval against labeled queries |
| `make eval-v2` | v2 agent eval suite |
| `make validate-full` | Both evals with gate thresholds; persists reports |

---

## 13. Data Pipeline

### Phase A: Corpus Calibration

Script: `corpus_routerkit.py` (~5000 lines)

Scans the raw corpus and classifies every document through deterministic keyword/phrase/regex scoring against a 15-domain taxonomy (`rules_active.json`). **No LLM calls per document.** Outputs `documents.jsonl`, `router.json`, `bucket_cards.jsonl`, and `acceptance_report.json`.

Production output: `kb_artifacts_full_promo_calibrated_run3/`

### Phase A.5: Staged Ingest

Script: `ingest_worker.py` (~1300 lines)

Chunks documents (token-based: 1200 tokens, 100 overlap), embeds via Qwen3-Embedding-8B (4096-dim), upserts to ES/Qdrant/Neo4j. Supports staged execution via `--skip-es`, `--skip-qdrant`, `--skip-neo4j`.

Document IDs are content-addressed: `SHA256(path|size_bytes|mtime_date|content_hash)`. Chunk IDs: `{doc_id}:{chunk_index}`. Ingest is idempotent.

### Phase B: GraphRAG Indexing

Script: `scripts/graphrag_index.py` (~1200 lines)

Reads from Elasticsearch (not files). Builds 9+ artifact types: `text_unit`, `entity_description`, `relationship`, `claim`, `community_report`, `taxonomy_first_bucket`, `taxonomy_sub_bucket`, `taxonomy_bucket_card`, `taxonomy_domain_profile`.

Optional additive LLM extraction (`GRAPHRAG_ENABLE_LLM=1`): richer entity descriptions, typed relationships, factual claims, narrative community reports. LLM artifacts coexist with deterministic ones (additive, not replacement).

---

## 14. Evaluation & Testing

### Smoke Tests

```bash
make smoke-retrieval    # Tests embedding + BM25 + vector search
make smoke-v2           # Tests v2 agent SSE streaming + JSON endpoints
```

### Evaluation Suites

```bash
make eval-v2            # v2 agent eval suite
make eval-retrieval     # Retrieval quality eval against labeled queries
make validate-full      # Both evals with pass/fail gates
make verify-ingest      # Cross-store doc/chunk parity check
```

Eval files: `evals/v2/tasks.sample.jsonl` (agent), `evals/retrieval/queries.sample.jsonl` (retrieval).

---

## 15. Troubleshooting

### Services not healthy

```bash
make health              # Run health checks
make ps                  # Check which containers are running
docker logs dd-query-api --tail 50
docker logs dd-agent-api-v2 --tail 50
docker logs dd-vllm-embed --tail 50
```

### GPU not available

```bash
make preflight-gpu       # Verify nvidia-smi + Docker GPU runtime
```

### Model download failures

Ensure `HUGGING_FACE_HUB_TOKEN` is set and NGC login is configured:
```bash
docker login nvcr.io     # Username: $oauthtoken, Password: <NGC API key>
```

### Corporate proxy issues

The Makefile sets `NO_PROXY` to exclude local Docker services. Both Python APIs call `_ensure_local_no_proxy()` at startup. If issues persist:

```bash
export NO_PROXY=127.0.0.1,localhost,elasticsearch,qdrant,neo4j,vllm-embed,vllm-rerank,query-api,agent-api
curl --noproxy '*' http://localhost:8080/health
```

### Empty agent answers

- Check LLM endpoint: `curl $OPENAI_BASE_URL/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"`
- Verify `CHAT_MODEL` is a valid model ID
- Check agent logs: `docker logs dd-agent-api-v2 --tail 50`

### Ingest count mismatches

```bash
make verify-ingest       # Check doc/chunk parity
make ingest-metadata     # Re-run ES + Neo4j (skip vectors)
make ingest-vectors      # Re-run Qdrant only
```
