"""Dearly Departed v2 Agent API — FastAPI app on port 8082."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.v2 import config
from services.v2.agent.states import AgentContext
from services.v2.agent.runner import run_agent
from services.v2.conversations import (
    init_conversations,
    create_conversation,
    get_conversation,
    list_conversations,
    delete_conversation,
    get_history_for_context,
    update_title,
)
from services.v2.sse.stream import sse_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v2.api")

app = FastAPI(
    title="Dearly Departed v2 Agent API",
    description="Advanced retrieval-grounded agent with SSE streaming, full-context generation, and citation verification",
    version="2.0.0",
)


# --- Request / Response Models ---

class AskRequestV2(BaseModel):
    query: str
    conversation_id: str = ""
    domain: str = ""
    mode: str = "auto"
    top_k: int = 40
    source_path_hint: str = ""
    date_from: str = ""
    date_to: str = ""
    temporal_basis: str = "effective"
    temporal_inference: str = "auto"
    graphrag_generation_mode: str = "both"
    graphrag_artifact_types: List[str] = Field(default_factory=list)
    max_context_tokens: int = 350000
    max_completion_tokens: int = 8000
    strategy: str = "auto"
    max_tool_calls: int = 15
    include_thinking: bool = True
    include_tool_details: bool = True
    temperature: float = 0.1
    top_p: float = 0.9
    user: str = ""


class CitationResponseV2(BaseModel):
    index: int
    item_id: str
    path: str
    text_excerpt: str = ""
    verified: bool
    verification_method: str = ""
    verification_score: float = 0.0
    domain: str = ""
    bucket: str = ""
    date: str = ""


class RetrievalMetaV2(BaseModel):
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    intent: str = ""
    strategy: str = ""
    context_tokens: int = 0
    evidence_count: int = 0


class DiagnosticsV2(BaseModel):
    complexity: str = ""
    taxonomy_hints: Dict[str, Any] = Field(default_factory=dict)
    verification_passed: bool = False
    retry_count: int = 0


class AskResponseV2(BaseModel):
    answer: str
    citations: List[CitationResponseV2]
    retrieval: RetrievalMetaV2 = Field(default_factory=RetrievalMetaV2)
    model: str = ""
    trace_id: str = ""
    timing: Dict[str, float] = {}
    diagnostics: DiagnosticsV2 = Field(default_factory=DiagnosticsV2)


# --- Startup / Shutdown ---

@app.on_event("startup")
def startup():
    log.info("Starting Dearly Departed v2 API on port %d", config.V2_PORT)

    # 1. HTTP client for query_api
    from services.v2.tools._http import init_http_client
    init_http_client()

    # 2. Direct ES client for document_lookup
    from services.v2.tools.document_lookup import init_es_client
    init_es_client(config.ES_URL)

    # 3. Neo4j driver for traverse_graph
    from services.v2.tools.traverse_graph import init_neo4j_driver
    init_neo4j_driver(config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD)

    # 4. Taxonomy index
    from services.v2.tools.search_taxonomy import init_taxonomy
    try:
        init_taxonomy(config.TAXONOMY_ROUTER_JSON)
    except Exception as exc:
        log.warning("Taxonomy init failed (non-fatal): %s", exc)

    # 5. Import all tools to register them
    import services.v2.tools.search_chunks  # noqa: F401
    import services.v2.tools.search_graphrag  # noqa: F401
    import services.v2.tools.traverse_graph  # noqa: F401
    import services.v2.tools.search_taxonomy  # noqa: F401
    import services.v2.tools.temporal_search  # noqa: F401
    import services.v2.tools.entity_profile  # noqa: F401
    import services.v2.tools.document_lookup  # noqa: F401

    # 6. Tracing
    from services.v2.observability.tracing import init_tracing
    init_tracing()

    # 7. Conversation store
    init_conversations()

    log.info("v2 API startup complete")


@app.on_event("shutdown")
async def shutdown():
    from services.v2.tools._http import close_http_client
    from services.v2.tools.document_lookup import close_es_client
    from services.v2.tools.traverse_graph import close_neo4j_driver

    await close_http_client()
    close_es_client()
    await close_neo4j_driver()
    log.info("v2 API shutdown complete")


# --- Endpoints ---

def _build_context(req: AskRequestV2) -> AgentContext:
    """Build AgentContext from request, loading conversation history if available."""
    history = get_history_for_context(req.conversation_id) if req.conversation_id else []
    return AgentContext(
        query=req.query,
        conversation_id=req.conversation_id,
        conversation_history=history,
        request_params={
            "domain": req.domain,
            "mode": req.mode,
            "top_k": req.top_k,
            "source_path_hint": req.source_path_hint,
            "date_from": req.date_from,
            "date_to": req.date_to,
            "temporal_basis": req.temporal_basis,
            "graphrag_generation_mode": req.graphrag_generation_mode,
            "graphrag_artifact_types": req.graphrag_artifact_types,
            "max_context_tokens": req.max_context_tokens,
            "max_completion_tokens": req.max_completion_tokens,
            "strategy": req.strategy,
            "max_tool_calls": req.max_tool_calls,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "user": req.user,
            "temporal_inference": req.temporal_inference,
            "include_thinking": req.include_thinking,
            "include_tool_details": req.include_tool_details,
        },
    )


@app.post("/v2/ask/stream")
async def ask_stream(req: AskRequestV2):
    """SSE streaming endpoint — primary v2 interface."""
    ctx = _build_context(req)
    return sse_response(run_agent(ctx))


@app.post("/v2/ask", response_model=AskResponseV2)
async def ask(req: AskRequestV2):
    """Non-streaming endpoint — collects all events and returns JSON."""
    ctx = _build_context(req)

    # Consume all events
    async for _ in run_agent(ctx):
        pass

    return AskResponseV2(
        answer=ctx.final_answer,
        citations=[
            CitationResponseV2(
                index=c.index,
                item_id=c.item_id,
                path=c.path,
                text_excerpt=c.text_excerpt,
                verified=c.verified,
                verification_method=c.verification_method,
                verification_score=c.verification_score,
                domain=c.domain,
                bucket=c.bucket,
                date=c.date,
            )
            for c in ctx.citations if c.verified
        ],
        retrieval=RetrievalMetaV2(
            tool_calls=[t.model_dump() for t in ctx.tool_call_log],
            intent=ctx.intent,
            strategy=ctx.strategy,
            context_tokens=ctx.context_token_count,
            evidence_count=len(ctx.evidence),
        ),
        model=config.CHAT_MODEL,
        trace_id=ctx.trace_id,
        timing=ctx.state_timings,
        diagnostics=DiagnosticsV2(
            complexity=ctx.complexity,
            taxonomy_hints=ctx.taxonomy_hints,
            verification_passed=ctx.verification_passed,
            retry_count=ctx.retry_count,
        ),
    )


@app.get("/v2/health")
async def health():
    """Health check — pings all backend services."""
    checks = {}

    # ES
    try:
        from services.v2.tools.document_lookup import get_es
        es = get_es()
        info = es.info()
        checks["elasticsearch"] = {"status": "ok", "version": info.get("version", {}).get("number", "")}
    except Exception as exc:
        checks["elasticsearch"] = {"status": "error", "error": str(exc)}

    # Qdrant (via query_api health)
    try:
        from services.v2.tools._http import get_client
        client = get_client()
        resp = await client.get(f"{config.QUERY_API_URL.rstrip('/')}/health")
        checks["query_api"] = {"status": "ok" if resp.status_code < 300 else "error"}
    except Exception as exc:
        checks["query_api"] = {"status": "error", "error": str(exc)}

    # Neo4j
    try:
        from services.v2.tools.traverse_graph import get_driver
        driver = get_driver()
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS ok")
            await result.single()
        checks["neo4j"] = {"status": "ok"}
    except Exception as exc:
        checks["neo4j"] = {"status": "error", "error": str(exc)}

    # LLM (OpenAI-compatible)
    try:
        from services.v2.tools._http import get_client
        client = get_client()
        resp = await client.get(f"{config.OPENAI_BASE_URL.rstrip('/')}/models")
        checks["llm"] = {"status": "ok" if resp.status_code < 300 else "error"}
    except Exception as exc:
        checks["llm"] = {"status": "error", "error": str(exc)}

    all_ok = all(c.get("status") == "ok" for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "service": "dearly-departed-v2",
        "checks": checks,
    }


@app.get("/v2/trace/{trace_id}")
async def get_trace(trace_id: str):
    """Retrieve a persisted trace by ID."""
    from services.v2.observability.trace_store import load_trace
    trace = load_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


@app.get("/v2/traces")
async def list_traces_endpoint(limit: int = 50, offset: int = 0):
    """List recent traces."""
    from services.v2.observability.trace_store import list_traces
    traces = list_traces(limit=limit, offset=offset)
    return {"traces": traces, "count": len(traces)}


# --- Conversation Endpoints ---

class CreateConversationRequest(BaseModel):
    title: str = ""


class UpdateConversationRequest(BaseModel):
    title: str


@app.post("/v2/conversations")
async def create_conversation_endpoint(req: CreateConversationRequest = CreateConversationRequest()):
    """Create a new conversation session."""
    session = create_conversation(title=req.title)
    return {"conversation_id": session.id, "title": session.title, "created_at": session.created_at}


@app.get("/v2/conversations")
async def list_conversations_endpoint(limit: int = 50, offset: int = 0):
    """List all conversation sessions."""
    conversations = list_conversations(limit=limit, offset=offset)
    return {"conversations": conversations, "count": len(conversations)}


@app.get("/v2/conversations/{conversation_id}")
async def get_conversation_endpoint(conversation_id: str):
    """Get a conversation with all turns."""
    session = get_conversation(conversation_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    return session.model_dump()


@app.patch("/v2/conversations/{conversation_id}")
async def update_conversation_endpoint(conversation_id: str, req: UpdateConversationRequest):
    """Update conversation title."""
    if not update_title(conversation_id, req.title):
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    return {"ok": True}


@app.delete("/v2/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str):
    """Delete a conversation session."""
    if not delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    return {"ok": True}
