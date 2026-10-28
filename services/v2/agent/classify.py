"""CLASSIFY state handler — determine intent, complexity, strategy."""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncGenerator

from services.v2.agent.states import AgentContext, AgentState
from services.v2.sse import events
from services.v2 import config
from services.v2.observability.spans import traced_span, set_llm_span_attributes

log = logging.getLogger("v2.classify")

_CLASSIFY_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "classify_template.txt")


def _load_classify_prompt() -> str:
    with open(_CLASSIFY_PROMPT_PATH, "r") as f:
        return f.read()


async def classify_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Classify the query and set strategy."""
    from services.chat_provider import ChatProvider

    yield events.thinking("Analyzing query intent, complexity, and retrieval strategy...")

    prompt_template = _load_classify_prompt()
    prompt = prompt_template.format(query=ctx.query)

    chat = ChatProvider()
    try:
        with traced_span("llm.classify") as span:
            resp = await chat.async_complete(
                messages=[
                    {"role": "system", "content": "You are a query classifier. Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_completion_tokens=500,
                user=ctx.request_params.get("user", ""),
            )
            set_llm_span_attributes(span, model=config.CHAT_MODEL, max_tokens=500, temperature=0.0)
        text = chat.extract_text(resp)

        # Parse JSON from response (handle markdown code blocks)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        classification = json.loads(text)
    except Exception as exc:
        log.warning("Classification failed, using defaults: %s", exc)
        classification = {
            "intent": "factual_lookup",
            "complexity": "simple",
            "strategy": "react",
            "taxonomy_hints": {},
            "temporal_range": {},
            "reasoning": f"Classification failed: {exc}",
        }

    ctx.intent = classification.get("intent", "factual_lookup")
    ctx.complexity = classification.get("complexity", "simple")
    ctx.strategy = classification.get("strategy", "react")
    ctx.taxonomy_hints = classification.get("taxonomy_hints", {})
    ctx.inferred_temporal_range = classification.get("temporal_range", {})

    # Override from request params if provided
    if ctx.request_params.get("strategy", "auto") != "auto":
        ctx.strategy = ctx.request_params["strategy"]

    yield events.thinking(
        f"Classified as: intent={ctx.intent}, complexity={ctx.complexity}, strategy={ctx.strategy}",
        structured={
            "intent": ctx.intent,
            "complexity": ctx.complexity,
            "strategy": ctx.strategy,
            "taxonomy_hints": ctx.taxonomy_hints,
            "temporal_range": ctx.inferred_temporal_range,
        },
    )

    # Transition
    if ctx.intent == "adversarial_no_answer":
        ctx.strategy = "react"  # Simple path for adversarial
    if ctx.strategy == "plan_execute":
        ctx.current_state = AgentState.PLAN
    else:
        ctx.current_state = AgentState.REACT
