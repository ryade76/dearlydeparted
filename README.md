# Dearly Departed

**Capture what an outgoing employee knows before they walk out the door.**

When a long-tenured employee leaves, decades of institutional knowledge go with them: runbooks that only exist in their notes, the reason a firewall rule is the way it is, which vendor contract renews when. Dearly Departed turns the files they leave behind (documents, spreadsheets, logs, meeting notes, procedures, contracts, photos) into a cited, conversational assistant their successors can query.

Under the hood it is a routing-first GraphRAG retrieval system. Every captured document is classified through a calibrated 15-domain taxonomy before retrieval, and that classification structures how evidence is found, ranked and cited downstream. The method was developed and calibrated on a ~900-document IT-operations corpus.

> **Consent & privacy.** Only capture files with the departing employee's knowledge and your organization's approval. Captured corpora, calibration artifacts and data-store volumes are gitignored — never commit them. The taxonomy's sensitivity rules flag likely PII and credentials, and redacted sampling masks emails, IPs and SSNs.

## Architecture

```
 Captured files (departing employee)
       |
       v
 Phase A -- corpus_routerkit.py (deterministic classification)
       |    No LLM calls per doc. Keyword/phrase/regex scoring
       |    against 15-domain taxonomy. 6 acceptance gates.
       v
 documents.jsonl + router.json + bucket_cards.jsonl
       |
       v
 Phase A.5 -- ingest_worker.py + graphrag_index.py
       |    Chunk -> embed (Qwen3-Embedding-8B, 4096-dim) -> upsert
       |    Build 9+ GraphRAG artifact types + taxonomy graph
       v
 +--------------+--------------+--------------+
 | Elasticsearch|    Qdrant    |    Neo4j     |
 |  BM25 chunks |  Dense vecs  |  Graph +     |
 |  GraphRAG    |  4096-dim    |  Taxonomy    |
 +------+-------+------+-------+------+-------+
        +              +              +
        +--------------+--------------+
                       |
        4-source RRF fusion + Qwen3 rerank + rebalance
                       |
              +--------+--------+
              |                 |
       query-api :8080   agent-api-v2 :8082
       /retrieve         /v2/ask (JSON + SSE)
                         /v2/conversations
                                |
                         frontend-v2 :3001
```

## Prerequisites

- Docker with Compose v2
- NVIDIA GPU with Docker GPU runtime
- Python 3.10+ (pipeline scripts)
- Node 20+ (frontend development only)

## Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env: set OPENAI_API_KEY, HUGGING_FACE_HUB_TOKEN, NEO4J_PASSWORD

# 2. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_pipeline.txt

# 3. Start full v2 stack (data stores + GPU models + APIs + frontend)
make up-v2

# 4. Bootstrap + ingest + index
make bootstrap       # Create indices, collections, constraints
make ingest-full     # Chunk, embed, upsert all documents
make index-graphrag  # Build GraphRAG artifacts + taxonomy

# 5. Verify
make health
make smoke-retrieval
make smoke-v2
```

Then open **http://localhost:3001** for the web UI.

See [QUICKSTART.md](QUICKSTART.md) for the full guide — including bringing your own corpus, troubleshooting, and API examples.

## Project Structure

```
├── corpus_routerkit.py          # Offline calibration pipeline (~5000 lines)
├── ingest_worker.py             # Document ingest: chunk -> embed -> upsert (~1300 lines)
├── rules_active.json            # 15-domain taxonomy rules
├── Makefile                     # 27+ automation targets
├── docker-compose.dearly-departed.yml
│
├── services/
│   ├── query_api.py             # Retrieval API: 4-source -> RRF -> rerank -> rebalance
│   ├── chat_provider.py         # OpenAI-compatible chat wrapper
│   └── v2/                      # v2 agent: agentic retrieval + multi-turn chat
│       ├── api.py               # FastAPI app with SSE streaming
│       ├── conversations.py     # Chat session persistence
│       └── agent/               # State machine: classify -> react -> generate -> verify
│
├── scripts/
│   ├── graphrag_index.py        # GraphRAG artifact builder (~1200 lines)
│   ├── bootstrap_indices.py     # Create ES/Qdrant/Neo4j schemas
│   ├── eval_retrieval.py        # Retrieval quality evaluation
│   └── verify_ingest_counts.py  # Cross-store parity check
│
├── frontend-v2/                 # React 19 + TypeScript + Tailwind + Vite
│   ├── src/
│   │   ├── api/                 # SSE streaming client
│   │   ├── stores/              # Zustand state (sessions, stream, history)
│   │   └── components/          # Chat, sidebar, tools, trace viewer
│   └── nginx.conf               # Production reverse proxy
│
├── containers/
│   └── app-api/                 # Dockerfile + requirements for APIs
│
├── evals/                       # Evaluation query/task definitions
│   └── v2/                      # v2 eval runner + sample tasks
├── prompts/graphrag/            # LLM extraction + community report prompts
└── docs/DEVELOPER.md            # Full API reference + deep dives
```

## Services

| Service | Port | Description |
|---|---|---|
| Elasticsearch | 9200 | BM25 lexical search + document metadata |
| Qdrant | 6333 | Dense vector search (4096-dim, cosine) |
| Neo4j | 7474/7687 | Graph structure + taxonomy hierarchy |
| vllm-embed | 8001 | Qwen3-Embedding-8B embedding service |
| vllm-rerank | 8002 | Qwen3-Reranker-8B reranking service |
| query-api | 8080 | Retrieval API (`/retrieve`, `/health`) |
| agent-api-v2 | 8082 | Agent API (`/v2/ask`, `/v2/ask/stream`, `/v2/conversations`) |
| frontend-v2 | 3001 | Web UI with chat sessions |
| Langfuse | 3002 | Observability dashboard (optional: `make up-observability`) |

## API Overview

### Query API — `POST /retrieve`

Taxonomy-aware multi-source retrieval with RRF fusion, neural reranking, and intent-aware rebalancing.

```bash
curl -X POST http://localhost:8080/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "network security procedures", "mode": "auto", "top_k": 10}'
```

Retrieval modes: `basic` (chunks only), `local` (+ entities/relationships/claims), `global` (+ community reports), `drift` (local + global), `auto` (decomposer selects).

### Agent v2 API — `POST /v2/ask`

Agentic retrieval with multi-turn conversation memory, structured citations, and streaming.

```bash
# JSON response
curl -X POST http://localhost:8082/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "What backup systems are in use?", "top_k": 40}'

# SSE streaming
curl -sN -X POST http://localhost:8082/v2/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What backup systems are in use?", "top_k": 40}'
```

## Evaluation

```bash
make eval-v2            # v2 agent eval suite (sample tasks included)
make eval-retrieval     # Retrieval quality against labeled queries
make validate-full      # Both evals with pass/fail gates
make verify-ingest      # Cross-store doc/chunk parity check
```

## Key Design Decisions

- **Classification is deterministic** — no LLM calls per document. Keyword/phrase/regex scoring with weighted source matching.
- **GraphRAG artifacts are pre-materialized text** — they compete for retrieval slots via embedding similarity through the same RRF pipeline as regular chunks.
- **Intent-aware rebalancing** — enforces text-evidence floor, caps claim/taxonomy saturation per query intent, limits per-path repetition.
- **Adaptive agent retries** — halves context and doubles token budget on empty LLM output; falls back to retrieval-grounded snippets.
- **Content-addressed IDs** — `SHA256(path|size_bytes|mtime_date|content_hash)` makes ingest idempotent.
- **Multi-turn conversation memory** — prior turns injected as native chat messages; the default 350K-token context budget is configurable via `V2_MAX_CONTEXT_TOKENS`.

## Documentation

- [QUICKSTART.md](QUICKSTART.md) — Developer quickstart (clone to querying)
- [CLAUDE.md](CLAUDE.md) — AI assistant context and architecture reference
- [docs/DEVELOPER.md](docs/DEVELOPER.md) — Full API reference, environment variables, pipeline deep dives

## Environment

Copy `.env.example` to `.env` and configure:

| Variable | Purpose |
|---|---|
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | Any OpenAI-compatible chat endpoint |
| `HUGGING_FACE_HUB_TOKEN` | Model downloads |
| `NEO4J_PASSWORD` | Graph database auth |
| `CHAT_MODEL` | LLM model ID for agent generation |

See [QUICKSTART.md](QUICKSTART.md) for the full setup walkthrough.

## License

[MIT](LICENSE)
