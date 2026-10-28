"""Tool registry and base definitions for v2 agent tools."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.v2.agent.states import AgentContext, EvidenceItem, ToolCallRecord

log = logging.getLogger("v2.tools")

# Global registry: tool_name -> (function, openai_schema)
_TOOL_REGISTRY: Dict[str, tuple] = {}


def register_tool(name: str, description: str, parameters: Dict[str, Any]):
    """Decorator to register a tool function with its OpenAI function-calling schema."""
    def decorator(fn):
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        _TOOL_REGISTRY[name] = (fn, schema)
        return fn
    return decorator


def get_tool_schemas() -> List[Dict[str, Any]]:
    return [schema for _, schema in _TOOL_REGISTRY.values()]


def get_tool_fn(name: str) -> Optional[Callable]:
    entry = _TOOL_REGISTRY.get(name)
    return entry[0] if entry else None


async def execute_tool(
    ctx: AgentContext,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_call_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a registered tool and record it in the context."""
    fn = get_tool_fn(tool_name)
    if fn is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    if tool_call_id is None:
        tool_call_id = f"tc_{uuid.uuid4().hex[:8]}"

    from services.v2.observability.spans import traced_span, set_vd_attributes
    t0 = time.monotonic()
    try:
        with traced_span(f"tool.{tool_name}") as span:
            set_vd_attributes(span, tool_name=tool_name)
            result = await fn(ctx, **arguments)
        elapsed_ms = (time.monotonic() - t0) * 1000
        record = ToolCallRecord(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            result_summary=result.get("summary", {}),
            duration_ms=elapsed_ms,
        )
        ctx.tool_call_log.append(record)
        return {"tool_call_id": tool_call_id, "result": result, "duration_ms": elapsed_ms}
    except Exception as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        record = ToolCallRecord(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            error=str(exc),
            duration_ms=elapsed_ms,
        )
        ctx.tool_call_log.append(record)
        raise
