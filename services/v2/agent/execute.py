"""EXECUTE state handler — run plan steps, fan-out parallel where possible."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, List

from services.v2.agent.states import AgentContext, AgentState, PlanStep
from services.v2.sse import events
from services.v2.tools.base import execute_tool
from services.v2 import config

log = logging.getLogger("v2.execute")


async def execute_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Execute plan steps with parallel fan-out for independent steps."""
    if not ctx.plan_steps:
        ctx.current_state = AgentState.SYNTHESIZE
        return

    completed_steps: set = set()
    max_steps = len(ctx.plan_steps)

    while len(completed_steps) < max_steps:
        max_tool_calls = ctx.request_params.get("max_tool_calls", config.V2_MAX_TOOL_CALLS)
        if len(ctx.tool_call_log) >= max_tool_calls:
            break

        # Find steps ready to execute (all dependencies completed)
        ready: List[PlanStep] = []
        for step in ctx.plan_steps:
            if step.step_index in completed_steps:
                continue
            if all(dep in completed_steps for dep in step.depends_on):
                ready.append(step)

        if not ready:
            log.warning("No executable steps but %d/%d completed — breaking", len(completed_steps), max_steps)
            break

        # Execute ready steps in parallel
        async def _run_step(step: PlanStep):
            yield_events = []
            tc_id = f"plan_step_{step.step_index}"
            yield_events.append(events.tool_call_start(
                tool_call_id=tc_id,
                tool_name=step.tool_name,
                arguments=step.tool_args,
                reason=step.description,
            ))
            try:
                result = await execute_tool(ctx, step.tool_name, step.tool_args, tool_call_id=tc_id)
                yield_events.append(events.tool_call_result(
                    tool_call_id=tc_id,
                    tool_name=step.tool_name,
                    duration_ms=result["duration_ms"],
                    result_summary=result["result"].get("summary", {}),
                ))
                step.completed = True
            except Exception as exc:
                log.warning("Plan step %d (%s) failed: %s", step.step_index, step.tool_name, exc)
                yield_events.append(events.tool_call_error(
                    tool_call_id=tc_id,
                    error=str(exc),
                ))
                step.completed = True  # Mark as complete even on failure to prevent infinite loop
            return yield_events

        if len(ready) == 1:
            # Single step — execute directly and yield events
            step = ready[0]
            step_events = await _run_step(step)
            for evt in step_events:
                yield evt
            completed_steps.add(step.step_index)
        else:
            # Multiple independent steps — run in parallel
            yield events.thinking(f"Executing {len(ready)} parallel steps: {[s.tool_name for s in ready]}")
            tasks = [_run_step(step) for step in ready]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for step, result in zip(ready, results):
                if isinstance(result, Exception):
                    log.warning("Parallel step %d failed: %s", step.step_index, result)
                    yield events.tool_call_error(
                        tool_call_id=f"plan_step_{step.step_index}",
                        error=str(result),
                    )
                else:
                    for evt in result:
                        yield evt
                completed_steps.add(step.step_index)

    ctx.current_state = AgentState.SYNTHESIZE
