"""EMIT_FINAL state handler — format response, persist trace, emit final event."""

from __future__ import annotations

import logging
import re
from typing import AsyncGenerator

from services.v2.agent.states import AgentContext, AgentState
from services.v2.sse import events
from services.v2 import config

log = logging.getLogger("v2.emit")


def _strip_unverified_citations(text: str, verified_ids: set) -> str:
    """Remove citation markers for unverified evidence from the answer text."""
    def replace_citation(match):
        cid = match.group(1)
        if cid in verified_ids:
            return match.group(0)
        return ""
    return re.sub(r'\[([^\]]+)\]', replace_citation, text)


async def emit_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Format final response and persist trace."""
    # Strip unverified citations from answer
    verified_ids = {c.item_id for c in ctx.citations if c.verified}
    final_answer = _strip_unverified_citations(ctx.raw_answer, verified_ids)

    # Clean up excessive blank lines while preserving markdown formatting
    final_answer = re.sub(r'\n{3,}', '\n\n', final_answer).strip()
    ctx.final_answer = final_answer

    # Build citations list for response
    citations_data = [
        {
            "index": c.index,
            "item_id": c.item_id,
            "path": c.path,
            "text_excerpt": c.text_excerpt,
            "verified": c.verified,
            "verification_method": c.verification_method,
            "verification_score": c.verification_score,
            "domain": c.domain,
            "bucket": c.bucket,
            "date": c.date,
            "doc_type": c.doc_type,
            "file_url": c.file_url,
        }
        for c in ctx.citations if c.verified
    ]

    # Timing breakdown
    timing = dict(ctx.state_timings)

    # Diagnostics
    diagnostics = {
        "trace_id": ctx.trace_id,
        "intent": ctx.intent,
        "complexity": ctx.complexity,
        "strategy": ctx.strategy,
        "context_token_count": ctx.context_token_count,
        "evidence_count": len(ctx.evidence),
        "tool_calls": len(ctx.tool_call_log),
        "retry_count": ctx.retry_count,
        "verification_passed": ctx.verification_passed,
        "taxonomy_hints": ctx.taxonomy_hints,
    }

    yield events.answer_complete(
        answer=final_answer,
        citations=citations_data,
        timing=timing,
        diagnostics=diagnostics,
        model=config.CHAT_MODEL,
    )

    # Persist trace
    try:
        from services.v2.observability.trace_store import persist_trace
        await persist_trace(ctx)
    except Exception as exc:
        log.warning("Failed to persist trace %s: %s", ctx.trace_id, exc)

    # Auto-save turn to conversation store
    if ctx.conversation_id:
        try:
            from services.v2.conversations import add_turn
            add_turn(
                conversation_id=ctx.conversation_id,
                query=ctx.query,
                answer=final_answer,
                trace_id=ctx.trace_id,
            )
        except Exception as exc:
            log.warning("Failed to save conversation turn: %s", exc)

    ctx.current_state = AgentState.DONE
