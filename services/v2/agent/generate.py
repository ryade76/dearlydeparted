"""GENERATE state handler — call the chat model with full context, stream tokens."""

from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

from services.v2.agent.states import AgentContext, AgentState
from services.v2.sse import events
from services.v2 import config
from services.v2.observability.spans import traced_span, set_llm_span_attributes

log = logging.getLogger("v2.generate")

_SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "system_v2.txt")


def _load_system_prompt() -> str:
    with open(_SYSTEM_PROMPT_PATH, "r") as f:
        return f.read()


async def generate_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Generate answer using the chat model with full assembled context."""
    model = config.CHAT_MODEL
    yield events.generation_start(model=model, context_tokens=ctx.context_token_count)

    system_prompt = _load_system_prompt()
    max_completion = ctx.request_params.get("max_completion_tokens", config.CHAT_MAX_COMPLETION_TOKENS)
    temperature = ctx.request_params.get("temperature", config.CHAT_TEMPERATURE)
    top_p = ctx.request_params.get("top_p", config.CHAT_TOP_P)

    messages = [{"role": "system", "content": system_prompt}]

    # Inject conversation history as multi-turn chat
    for turn in ctx.conversation_history:
        messages.append({"role": "user", "content": turn["query"]})
        messages.append({"role": "assistant", "content": turn["answer"]})

    # Current turn: evidence context + new query
    messages.append(
        {"role": "user", "content": f"## Evidence Context\n\n{ctx.assembled_context}\n\n## Question\n\n{ctx.query}"},
    )

    from services.chat_provider import ChatProvider
    chat = ChatProvider()

    try:
        full_text = ""
        token_count = 0
        span_ctx = traced_span("llm.generate")
        span = span_ctx.__enter__()
        set_llm_span_attributes(span, model=model, max_tokens=max_completion, temperature=temperature)
        async for token in chat.async_stream_complete(
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion,
            user=ctx.request_params.get("user", ""),
        ):
            full_text += token
            token_count += 1
            yield events.generation_token(token)

        ctx.raw_answer = full_text
        set_llm_span_attributes(span, output_tokens=token_count, finish_reason="stop")
        span_ctx.__exit__(None, None, None)
        yield events.generation_complete(total_tokens=token_count, finish_reason="stop")

    except Exception as exc:
        try:
            span_ctx.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        # Fallback to sync generation
        log.warning("Async streaming failed (%s), falling back to sync generation", exc)
        resp = await chat.async_complete(
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion,
            user=ctx.request_params.get("user", ""),
        )
        text = chat.extract_text(resp)
        ctx.raw_answer = text

        yield events.generation_token(text)
        yield events.generation_complete(
            total_tokens=len(text) // 4,
            finish_reason=resp.get("choices", [{}])[0].get("finish_reason", "stop") if resp.get("choices") else "stop",
        )

    ctx.current_state = AgentState.VERIFY
