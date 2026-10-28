"""State machine runner — async generator that yields SSE events at every transition."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, AsyncGenerator

from services.v2.agent.states import AgentContext, AgentState
from services.v2.sse import events
from services.v2.observability.spans import traced_span, set_vd_attributes
from services.v2.observability.tracing import langfuse_observe

if TYPE_CHECKING:
    from services.v2.agent.classify import classify_handler
    from services.v2.agent.plan import plan_handler
    from services.v2.agent.execute import execute_handler
    from services.v2.agent.react import react_handler
    from services.v2.agent.synthesize import synthesize_handler
    from services.v2.agent.generate import generate_handler
    from services.v2.agent.verify import verify_handler
    from services.v2.agent.emit import emit_handler

log = logging.getLogger("v2.runner")


# State handler registry — filled lazily to avoid circular imports
_HANDLERS = {}


def _get_handler(state: AgentState):
    if not _HANDLERS:
        from services.v2.agent.classify import classify_handler
        from services.v2.agent.plan import plan_handler
        from services.v2.agent.execute import execute_handler
        from services.v2.agent.react import react_handler
        from services.v2.agent.synthesize import synthesize_handler
        from services.v2.agent.generate import generate_handler
        from services.v2.agent.verify import verify_handler
        from services.v2.agent.emit import emit_handler

        _HANDLERS[AgentState.CLASSIFY] = classify_handler
        _HANDLERS[AgentState.PLAN] = plan_handler
        _HANDLERS[AgentState.EXECUTE] = execute_handler
        _HANDLERS[AgentState.REACT] = react_handler
        _HANDLERS[AgentState.SYNTHESIZE] = synthesize_handler
        _HANDLERS[AgentState.GENERATE] = generate_handler
        _HANDLERS[AgentState.VERIFY] = verify_handler
        _HANDLERS[AgentState.EMIT_FINAL] = emit_handler
    return _HANDLERS.get(state)


@langfuse_observe("run_agent")
async def run_agent(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Run the v2 agent state machine, yielding SSE events."""
    yield events.session_start(ctx.trace_id, ctx.query)

    while ctx.current_state not in (AgentState.DONE, AgentState.ERROR):
        handler = _get_handler(ctx.current_state)
        if handler is None:
            yield events.error_event(
                f"No handler for state {ctx.current_state.value}",
                ctx.current_state.value,
            )
            ctx.current_state = AgentState.ERROR
            break

        prev_state = ctx.current_state
        t0 = time.monotonic()

        try:
            with traced_span(f"agent.{prev_state.value}", {"dd.trace_id": ctx.trace_id}) as span:
                set_vd_attributes(
                    span, trace_id=ctx.trace_id, state=prev_state.value,
                    intent=ctx.intent, strategy=ctx.strategy,
                )
                async for sse_event in handler(ctx):
                    yield sse_event
        except Exception as exc:
            log.exception("Handler %s failed: %s", prev_state.value, exc)
            yield events.error_event(str(exc), prev_state.value, recoverable=False)
            ctx.current_state = AgentState.ERROR
            ctx.error_message = str(exc)
            break

        elapsed_ms = (time.monotonic() - t0) * 1000
        ctx.state_timings[prev_state.value] = elapsed_ms

        if ctx.current_state != prev_state:
            yield events.state_transition(
                prev_state.value,
                ctx.current_state.value,
                elapsed_ms,
                {"evidence_count": len(ctx.evidence), "tool_calls": len(ctx.tool_call_log)},
            )

    yield events.done()
