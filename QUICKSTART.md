# Dearly Departed — Developer Quickstart

Get from a fresh clone to querying your own document corpus.

## Prerequisites

| Requirement | Check |
|---|---|
| Docker with Compose v2 | `docker compose version` |
| NVIDIA GPU + Docker GPU runtime | `docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi` |
| NGC login (for vLLM images) | `docker login nvcr.io` (user: `$oauthtoken`, password: NGC API key) |
| Python 3.10+ | `python3 --version` |
| HuggingFace token | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| OpenAI-compatible API key | Required for LLM-powered answers |

Node 20+ is only needed for frontend development (`make dev-frontend`).

## 1. Clone & Configure

```bash
git clone <repo-url> && cd dearly-departed

# Create your environment file
cp .env.example .env
```

Edit `.env` — the critical variables:

```bash
# Required
HUGGING_FACE_HUB_TOKEN=hf_...        # For model downloads
NEO4J_PASSWORD=your_password          # Graph database auth
OPENAI_API_KEY=your_key               # OpenAI-compatible LLM
OPENAI_BASE_URL=https://...           # LLM endpoint URL
CHAT_MODEL=GPT-5.2                    # Model ID

# GPU memory (adjust for your GPU — defaults assume 2x GPUs)
EMBED_GPU_MEMORY_UTILIZATION=0.45     # Embedding model VRAM fraction
RERANK_GPU_MEMORY_UTILIZATION=0.45    # Reranker model VRAM fraction
```

Set up Python:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_pipeline.txt
```

## 2. Start the Stack

A single command brings up everything — data stores, GPU model services, APIs, and frontend:

```bash
make up-v2
```

This starts 8 services:

| Service | Port | What it does |
|---|---|---|
| Elasticsearch | 9200 | BM25 lexical search + document metadata |
| Qdrant | 6333 | Dense vector search (4096-dim, cosine) |
| Neo4j | 7474 / 7687 | Graph structure + taxonomy hierarchy |
| vllm-embed | 8001 | Qwen3-Embedding-8B embedding model |
| vllm-rerank | 8002 | Qwen3-Reranker-8B reranking model |
| query-api | 8080 | 4-source retrieval with RRF fusion + rerank |
| agent-api-v2 | 8082 | LLM-powered answers with citations |
| frontend-v2 | 3001 | Web UI |

**First startup takes 5-10 minutes** — the GPU models need to download and load into VRAM.

## 3. Verify Services Are Healthy

```bash
# Quick health check (data stores + models)
make health

# Check all containers
docker compose -f docker-compose.dearly-departed.yml ps
```

Wait until `dd-vllm-embed` and `dd-vllm-rerank` show `(healthy)`. These take the longest.

```bash
# v2 agent health (includes model + data store status)
make health-v2
```

## 4. Prepare Your Corpus

Dearly Departed classifies every document through a 15-domain taxonomy before retrieval. This is done offline by `corpus_routerkit.py`.

Collect the departing employee's files (exported drives, notes, runbooks, logs, spreadsheets, contracts) into one directory. **Get the employee's consent and your organization's approval before capturing their files**, and keep the corpus out of version control (`corpus/` and `unarchived/` are gitignored). The sensitivity rules in `rules_active.json` flag likely PII/credentials and the default `--sample-mode redacted` masks emails, IPs and SSNs in samples.

Place your documents in a directory (PDFs, DOCX, PPTX, XLSX, TXT, HTML, etc.):

```bash
# Install optional document parsers
pip install -r routerkit_requirements_optional.txt

# Run calibration
python3 corpus_routerkit.py \
  --root /path/to/your/corpus \
  --out my_artifacts \
  --rules rules_active.json \
  --sample-mode full
```

This scans your corpus and produces:
- `my_artifacts/documents.jsonl` — one record per document with routing decisions
- `my_artifacts/router.json` — taxonomy structure for GraphRAG
- `my_artifacts/bucket_cards.jsonl` — rich bucket descriptions

Update `.env` to point at your artifacts:

```bash
ROUTERKIT_DOCS=my_artifacts/documents.jsonl
```

And update the Makefile variables (or pass them on the command line):

```bash
export ROUTERKIT_DOCS=my_artifacts/documents.jsonl
export TAXONOMY_ROUTER_JSON=my_artifacts/router.json
export TAXONOMY_BUCKET_CARDS=my_artifacts/bucket_cards.jsonl
```

**Customizing the taxonomy:** Edit `rules_active.json` to define your own domains, keywords, and artifact types. The existing file is tuned for an IT-operations role — adapt it to the departing employee's job. Each domain uses keyword/phrase/regex scoring with no LLM calls per document.

## 5. Bootstrap & Ingest

### Create schemas (one-time)

```bash
make bootstrap
```

Creates Elasticsearch indices (`kb_docs`, `kb_chunks`, `kb_graphrag_artifacts`), Qdrant collections (`kb_chunks_vec`, `kb_graphrag_artifacts_vec`), and Neo4j constraints.

### Ingest documents (3 stages)

```bash
# Stage A: Metadata into ES + Neo4j (fast, no GPU needed)
make ingest-metadata

# Stage B: Embed chunks into Qdrant (requires vllm-embed healthy)
make ingest-vectors

# Stage C: Build GraphRAG artifacts + taxonomy + community reports
make index-graphrag
```

Or run stages A+B together:

```bash
make ingest-full        # ES + Qdrant + Neo4j in one pass
make index-graphrag     # Then GraphRAG artifacts
```

**Timing estimates** (~900-document corpus):
- `ingest-metadata`: ~1-2 minutes
- `ingest-vectors`: ~5-10 minutes (GPU-dependent)
- `index-graphrag`: ~5-10 minutes

## 6. Verify

```bash
# Cross-store parity check (doc and chunk counts match across ES/Qdrant/Neo4j)
make verify-ingest

# Quick retrieval smoke test (tests embedding + BM25 + vector search)
make smoke-retrieval

# Quick v2 agent smoke test (SSE streaming + JSON endpoints)
make smoke-v2
```

## 7. Use the App

### Web UI

Open **http://localhost:3001** in your browser.

The UI includes:
- Session sidebar (create/switch/delete conversations)
- Multi-turn chat with conversation memory
- Real-time streaming with tool call visibility
- Citation verification panel
- Context budget and trace viewer

### API

**Retrieval (raw search results):**

```bash
curl -X POST http://localhost:8080/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "network security procedures", "mode": "auto", "top_k": 10}'
```

Retrieval modes: `basic` (chunks only), `local` (+ entities/relationships), `global` (+ community reports), `drift` (local+global), `auto` (decomposer selects).

**Agent v2 — JSON (full response):**

```bash
curl -X POST http://localhost:8082/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "What backup systems are in use?", "top_k": 40}'
```

**Agent v2 — SSE (streaming):**

```bash
curl -sN -X POST http://localhost:8082/v2/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What backup systems are in use?", "top_k": 40}'
```

**Conversation sessions:**

```bash
# Create a session
curl -X POST http://localhost:8082/v2/conversations \
  -H "Content-Type: application/json" -d '{}'

# Ask within a session (enables follow-up questions)
curl -X POST http://localhost:8082/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "Which DNS servers are documented?", "conversation_id": "<id>"}'

# Follow up (the model sees prior turns)
curl -X POST http://localhost:8082/v2/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "Tell me more about those servers", "conversation_id": "<id>"}'
```

## 8. Evaluation (Optional)

```bash
# v2 agent eval suite (sample tasks; generate your own with make generate-regression)
make eval-v2

# Retrieval quality eval
make eval-retrieval

# Full validation with pass/fail gates
make validate-full
```

Eval definitions: `evals/v2/tasks.sample.jsonl` (agent), `evals/retrieval/queries.sample.jsonl` (retrieval).

## Troubleshooting

### GPU models won't start

```bash
# Verify GPU is visible
nvidia-smi

# Check vLLM logs
docker logs dd-vllm-embed --tail 50
docker logs dd-vllm-rerank --tail 50
```

Common issues:
- **OOM**: Lower `EMBED_GPU_MEMORY_UTILIZATION` and `RERANK_GPU_MEMORY_UTILIZATION` in `.env`
- **NGC auth**: Run `docker login nvcr.io` with your NGC API key
- **Single GPU**: Both models need to fit in VRAM. Use `0.45` utilization for each (default)

### Ingest fails with embedding errors

```bash
# Check embedding service health
curl http://localhost:8001/v1/models

# If unhealthy, restart and wait
docker restart dd-vllm-embed
# Wait 2-3 minutes for model to reload
```

### Agent returns empty answers

- Check LLM endpoint: `curl $OPENAI_BASE_URL/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"`
- Verify `CHAT_MODEL` is a valid model ID at your endpoint
- Check agent logs: `docker logs dd-agent-api-v2 --tail 50`

### Proxy issues on corporate networks

The Makefile sets `NO_PROXY` automatically for all local services. If you still see proxy errors:

```bash
export NO_PROXY=127.0.0.1,localhost,elasticsearch,qdrant,neo4j,vllm-embed,vllm-rerank,query-api,agent-api
export no_proxy=$NO_PROXY
```

## Architecture Reference

```
 Your Corpus
      |
      v
 corpus_routerkit.py ---- Deterministic classification (no LLM calls)
      |
      v
 documents.jsonl + router.json + bucket_cards.jsonl
      |
      v
 ingest_worker.py -------- Chunk + embed + upsert to 3 stores
 graphrag_index.py ------- Build 9+ GraphRAG artifact types
      |
      v
 +---------------+  +---------------+  +---------------+
 | Elasticsearch |  |    Qdrant     |  |    Neo4j      |
 | BM25 + metadata|  | 4096-dim vecs |  | Graph + taxonomy|
 +-------+-------+  +-------+-------+  +-------+-------+
         |                   |                   |
         +-------------------+-------------------+
                             |
                    4-source RRF fusion
                    + Qwen3 reranking
                    + intent-aware rebalancing
                             |
              +--------------+--------------+
              |                             |
       query-api :8080              agent-api-v2 :8082
       /retrieve                    /v2/ask (JSON + SSE)
                                    /v2/conversations
                                           |
                                    frontend-v2 :3001
                                    React + Tailwind
```

For full API reference and architecture deep dives, see [docs/DEVELOPER.md](docs/DEVELOPER.md).
