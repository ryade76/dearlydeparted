"""PLAN state handler — generate multi-step tool-call plan for complex queries."""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncGenerator

from services.v2.agent.states import AgentContext, AgentState, PlanStep
from services.v2.sse import events
from services.v2 import config
from services.v2.observability.spans import traced_span, set_llm_span_attributes

log = logging.getLogger("v2.plan")

_PLAN_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "plan_template.txt")


def _load_plan_prompt() -> str:
    with open(_PLAN_PROMPT_PATH, "r") as f:
        return f.read()


async def plan_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Generate a multi-step plan for complex queries."""
    from services.chat_provider import ChatProvider

    yield events.thinking("Generating execution plan for complex query...")

    prompt_template = _load_plan_prompt()
    prompt = prompt_template.format(
        query=ctx.query,
        intent=ctx.intent,
        complexity=ctx.complexity,
        taxonomy_hints=json.dumps(ctx.taxonomy_hints),
        temporal_range=json.dumps(ctx.inferred_temporal_range),
    )

    chat = ChatProvider()
    try:
        with traced_span("llm.plan") as span:
            resp = await chat.async_complete(
                messages=[
                    {"role": "system", "content": "You are a query planner. Respond only with a valid JSON array."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_completion_tokens=2000,
                user=ctx.request_params.get("user", ""),
            )
            set_llm_span_attributes(span, model=config.CHAT_MODEL, max_tokens=2000, temperature=0.0)
        text = chat.extract_text(resp).strip()

        # Parse JSON
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        steps_raw = json.loads(text)
    except Exception as exc:
        log.warning("Plan generation failed, falling back to react: %s", exc)
        ctx.strategy = "react"
        ctx.current_state = AgentState.REACT
        yield events.thinking(f"Plan generation failed ({exc}), falling back to ReAct strategy")
        return

    # Parse plan steps
    ctx.plan_steps = []
    for step_data in steps_raw:
        step = PlanStep(
            step_index=step_data.get("step_index", len(ctx.plan_steps)),
            description=step_data.get("description", ""),
            tool_name=step_data.get("tool_name", ""),
            tool_args=step_data.get("tool_args", {}),
            depends_on=step_data.get("depends_on", []),
        )
        ctx.plan_steps.append(step)

    yield events.thinking(
        f"Plan generated with {len(ctx.plan_steps)} steps",
        structured={"plan_steps": [
            {"step": s.step_index, "tool": s.tool_name, "description": s.description}
            for s in ctx.plan_steps
        ]},
    )

    ctx.current_state = AgentState.EXECUTE
