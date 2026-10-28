"""V2 agent configuration — all from environment variables with V2_ prefix where needed."""

from __future__ import annotations

import os


# --- Data store connections ---
ES_URL = os.getenv("ES_URL", "http://localhost:9200")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me_local")

# --- Query API (HTTP delegation) ---
QUERY_API_URL = os.getenv("QUERY_API_URL", "http://127.0.0.1:8080").strip()
QUERY_API_TIMEOUT_SEC = int(os.getenv("QUERY_API_TIMEOUT_SEC", "300"))

# --- LLM (OpenAI-compatible) ---
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1").strip()
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.1"))
CHAT_TOP_P = float(os.getenv("CHAT_TOP_P", "0.9"))
CHAT_MAX_COMPLETION_TOKENS = int(os.getenv("V2_CHAT_MAX_COMPLETION_TOKENS", "8000"))
CHAT_TIMEOUT_SEC = int(os.getenv("CHAT_TIMEOUT_SEC", "300"))

# --- Agent behavior ---
V2_MAX_CONTEXT_TOKENS = int(os.getenv("V2_MAX_CONTEXT_TOKENS", "350000"))
V2_MAX_TOOL_CALLS = int(os.getenv("V2_MAX_TOOL_CALLS", "20"))
V2_REACT_MAX_ITERATIONS = int(os.getenv("V2_REACT_MAX_ITERATIONS", "5"))
V2_VERIFY_MAX_RETRIES = int(os.getenv("V2_VERIFY_MAX_RETRIES", "3"))
V2_VERIFY_OVERLAP_THRESHOLD = float(os.getenv("V2_VERIFY_OVERLAP_THRESHOLD", "0.30"))
V2_VERIFY_NLI_LOW = float(os.getenv("V2_VERIFY_NLI_LOW", "0.10"))

# --- Evidence diversity ---
V2_MAX_EVIDENCE_PER_PATH_STEM = int(os.getenv("V2_MAX_EVIDENCE_PER_PATH_STEM", "3"))
V2_MIN_UNIQUE_PATH_STEMS = int(os.getenv("V2_MIN_UNIQUE_PATH_STEMS", "5"))

# --- Overview query overrides ---
V2_OVERVIEW_MAX_TOOL_CALLS = int(os.getenv("V2_OVERVIEW_MAX_TOOL_CALLS", "40"))
V2_OVERVIEW_MAX_COMPLETION_TOKENS = int(os.getenv("V2_OVERVIEW_MAX_COMPLETION_TOKENS", "64000"))

# --- Taxonomy ---
TAXONOMY_ROUTER_JSON = os.getenv(
    "TAXONOMY_ROUTER_JSON",
    "kb_artifacts_full_promo_calibrated_run3/router.json",
)

# --- Tracing / Observability ---
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL", "http://localhost:3002")
TRACE_PERSIST_DIR = os.getenv("V2_TRACE_PERSIST_DIR", "reports/traces")

# --- Server ---
V2_PORT = int(os.getenv("V2_PORT", "8082"))
