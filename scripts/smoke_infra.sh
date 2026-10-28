#!/usr/bin/env bash
set -euo pipefail

ES_URL="${ES_URL:-http://127.0.0.1:9200}"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
NEO4J_HTTP_URL="${NEO4J_HTTP_URL:-http://127.0.0.1:7474}"
EMBED_MODELS_URL="${EMBED_MODELS_URL:-http://127.0.0.1:8001/v1/models}"
RERANK_MODELS_URL="${RERANK_MODELS_URL:-http://127.0.0.1:8002/v1/models}"
CHECK_MODELS="${CHECK_MODELS:-1}"
CHECK_APP="${CHECK_APP:-0}"
QUERY_API_URL="${QUERY_API_URL:-http://127.0.0.1:8080/health}"
AGENT_API_URL="${AGENT_API_URL:-http://127.0.0.1:8082/v2/health}"
LOCAL_NO_PROXY="${LOCAL_NO_PROXY:-127.0.0.1,localhost,elasticsearch,qdrant,neo4j,vllm-embed,vllm-rerank,query-api,agent-api-v2}"
export NO_PROXY="${NO_PROXY:-$LOCAL_NO_PROXY}"
export no_proxy="${no_proxy:-$LOCAL_NO_PROXY}"
CURL_BASE=(curl --noproxy '*' -fsS)

echo "[health] Elasticsearch: ${ES_URL}"
"${CURL_BASE[@]}" "${ES_URL}/_cluster/health" >/dev/null

echo "[health] Qdrant: ${QDRANT_URL}"
if ! "${CURL_BASE[@]}" "${QDRANT_URL}/healthz" >/dev/null; then
  "${CURL_BASE[@]}" "${QDRANT_URL}" >/dev/null
fi

echo "[health] Neo4j HTTP: ${NEO4J_HTTP_URL}"
"${CURL_BASE[@]}" "${NEO4J_HTTP_URL}" >/dev/null

if [[ "${CHECK_MODELS}" == "1" ]]; then
  echo "[health] Embedding model service: ${EMBED_MODELS_URL}"
  "${CURL_BASE[@]}" "${EMBED_MODELS_URL}" >/dev/null

  echo "[health] Rerank model service: ${RERANK_MODELS_URL}"
  "${CURL_BASE[@]}" "${RERANK_MODELS_URL}" >/dev/null
fi

if [[ "${CHECK_APP}" == "1" ]]; then
  echo "[health] Query API: ${QUERY_API_URL}"
  "${CURL_BASE[@]}" "${QUERY_API_URL}" >/dev/null

  echo "[health] Agent API: ${AGENT_API_URL}"
  "${CURL_BASE[@]}" "${AGENT_API_URL}" >/dev/null
fi

echo "[health] all checks passed"
