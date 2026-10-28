"""SSE event type definitions and serialization."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sse_encode(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


# --- Event constructors ---

def session_start(trace_id: str, query: str) -> str:
    return sse_encode("session_start", {
        "trace_id": trace_id,
        "query": query,
        "timestamp": _ts(),
    })


def state_transition(from_state: str, to_state: str, duration_ms: float, context_summary: Optional[Dict] = None) -> str:
    return sse_encode("state_transition", {
        "from_state": from_state,
        "to_state": to_state,
        "duration_ms": round(duration_ms, 1),
        "context_summary": context_summary or {},
    })


def thinking(content: str, structured: Optional[Dict] = None) -> str:
    return sse_encode("thinking", {
        "content": content,
        "structured": structured or {},
    })


def tool_call_start(tool_call_id: str, tool_name: str, arguments: Dict[str, Any], reason: str = "") -> str:
    return sse_encode("tool_call_start", {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": reason,
    })


def tool_call_result(tool_call_id: str, tool_name: str, duration_ms: float, result_summary: Dict[str, Any]) -> str:
    return sse_encode("tool_call_result", {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "duration_ms": round(duration_ms, 1),
        "result_summary": result_summary,
    })


def tool_call_error(tool_call_id: str, error: str) -> str:
    return sse_encode("tool_call_error", {
        "tool_call_id": tool_call_id,
        "error": error,
    })


def context_assembled(token_count: int, section_breakdown: Dict[str, int]) -> str:
    return sse_encode("context_assembled", {
        "token_count": token_count,
        "section_breakdown": section_breakdown,
    })


def generation_start(model: str, context_tokens: int) -> str:
    return sse_encode("generation_start", {
        "model": model,
        "context_tokens": context_tokens,
    })


def generation_token(token: str) -> str:
    return sse_encode("generation_token", {"token": token})


def generation_complete(total_tokens: int, finish_reason: str) -> str:
    return sse_encode("generation_complete", {
        "total_tokens": total_tokens,
        "finish_reason": finish_reason,
    })


def citation_verified(
    citation_index: int,
    item_id: str,
    path: str,
    verified: bool,
    method: str,
    confidence: float,
) -> str:
    return sse_encode("citation_verified", {
        "citation_index": citation_index,
        "item_id": item_id,
        "path": path,
        "verified": verified,
        "method": method,
        "confidence": round(confidence, 3),
    })


def verification_result(total_citations: int, verified_count: int, pass_rate: float) -> str:
    return sse_encode("verification_result", {
        "total_citations": total_citations,
        "verified_count": verified_count,
        "pass_rate": round(pass_rate, 3),
    })


def answer_complete(
    answer: str,
    citations: List[Dict[str, Any]],
    timing: Dict[str, float],
    diagnostics: Dict[str, Any],
    model: str = "",
) -> str:
    return sse_encode("answer_complete", {
        "answer": answer,
        "citations": citations,
        "timing": timing,
        "diagnostics": diagnostics,
        "model": model,
    })


def error_event(error: str, state: str, recoverable: bool = False) -> str:
    return sse_encode("error", {
        "error": error,
        "state": state,
        "recoverable": recoverable,
    })


def done() -> str:
    return sse_encode("done", {})
