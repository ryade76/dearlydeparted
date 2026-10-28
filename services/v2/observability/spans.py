"""Span helper utilities for v2 tracing."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Optional

log = logging.getLogger("v2.spans")


@contextmanager
def traced_span(name: str, attributes: Optional[Dict[str, Any]] = None):
    """Context manager that creates an OTel span if tracing is available."""
    from services.v2.observability.tracing import get_tracer

    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, str(v) if not isinstance(v, (str, int, float, bool)) else v)
                except Exception:
                    pass
        yield span


def set_span_attributes(span, attributes: Dict[str, Any]) -> None:
    """Safely set attributes on a span."""
    if span is None:
        return
    for k, v in attributes.items():
        try:
            span.set_attribute(k, str(v) if not isinstance(v, (str, int, float, bool)) else v)
        except Exception:
            pass


# --- GenAI Semantic Convention Constants ---
GENAI_SYSTEM = "gen_ai.system"
GENAI_REQUEST_MODEL = "gen_ai.request.model"
GENAI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GENAI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GENAI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GENAI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GENAI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reasons"


def set_llm_span_attributes(
    span,
    model: str = "",
    system: str = "openai",
    max_tokens: int = 0,
    temperature: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    finish_reason: str = "",
) -> None:
    """Set GenAI semantic conventions on an LLM call span."""
    if span is None:
        return
    attrs = {
        GENAI_SYSTEM: system,
        GENAI_REQUEST_MODEL: model,
    }
    if max_tokens:
        attrs[GENAI_REQUEST_MAX_TOKENS] = max_tokens
    if temperature:
        attrs[GENAI_REQUEST_TEMPERATURE] = temperature
    if input_tokens:
        attrs[GENAI_USAGE_INPUT_TOKENS] = input_tokens
    if output_tokens:
        attrs[GENAI_USAGE_OUTPUT_TOKENS] = output_tokens
    if finish_reason:
        attrs[GENAI_RESPONSE_FINISH_REASON] = finish_reason
    set_span_attributes(span, attrs)


def set_vd_attributes(
    span,
    trace_id: str = "",
    state: str = "",
    intent: str = "",
    complexity: str = "",
    strategy: str = "",
    tool_name: str = "",
    evidence_count: int = 0,
) -> None:
    """Set Dearly Departed custom attributes (dd.* namespace) on a span."""
    if span is None:
        return
    attrs: Dict[str, Any] = {}
    if trace_id:
        attrs["dd.trace_id"] = trace_id
    if state:
        attrs["dd.state"] = state
    if intent:
        attrs["dd.intent"] = intent
    if complexity:
        attrs["dd.complexity"] = complexity
    if strategy:
        attrs["dd.strategy"] = strategy
    if tool_name:
        attrs["dd.tool_name"] = tool_name
    if evidence_count:
        attrs["dd.evidence_count"] = evidence_count
    set_span_attributes(span, attrs)
