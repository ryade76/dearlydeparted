"""Shared HTTP client for query_api calls."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from services.v2 import config

log = logging.getLogger("v2.tools.http")

# Initialized at startup
_client: Optional[httpx.AsyncClient] = None


def init_http_client() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=httpx.Timeout(float(config.QUERY_API_TIMEOUT_SEC)))


def get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized — call init_http_client() at startup")
    return _client


async def close_http_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def query_retrieve(
    query: str,
    mode: str = "auto",
    domain: str = "",
    top_k: int = 20,
    decompose: bool = True,
    date_from: str = "",
    date_to: str = "",
    temporal_basis: str = "effective",
    temporal_boost: str = "",
    source_path_hint: str = "",
    graphrag_artifact_types: Optional[List[str]] = None,
    graphrag_generation_mode: str = "both",
    graphrag_use_llm: bool = True,
    graphrag_use_deterministic: bool = True,
) -> Dict[str, Any]:
    """POST to query_api /retrieve and return JSON response."""
    client = get_client()
    url = f"{config.QUERY_API_URL.rstrip('/')}/retrieve"

    payload = {
        "query": query,
        "domain": domain,
        "mode": mode,
        "top_k": top_k,
        "include_graph": True,
        "decompose": decompose,
        "source_path_hint": source_path_hint or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "temporal_basis": temporal_basis or "effective",
        "temporal_boost": temporal_boost or "",
        "graphrag_generation_mode": graphrag_generation_mode or "both",
        "graphrag_use_llm": graphrag_use_llm,
        "graphrag_use_deterministic": graphrag_use_deterministic,
        "graphrag_artifact_types": [str(x).strip() for x in (graphrag_artifact_types or []) if str(x).strip()],
    }

    resp = await client.post(url, json=payload)
    if resp.status_code >= 300:
        raise RuntimeError(f"query-api error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("query-api response is not a JSON object")
    return data


