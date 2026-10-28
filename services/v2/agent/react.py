"""REACT state handler — simple iterative tool-calling loop."""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from services.v2.agent.states import AgentContext, AgentState
from services.v2.sse import events
from services.v2.tools.base import execute_tool, get_tool_schemas
from services.v2 import config
from services.v2.observability.spans import traced_span, set_llm_span_attributes
from services.v2.utils import path_stem

log = logging.getLogger("v2.react")


def _build_react_tools_prompt(ctx: AgentContext) -> str:
    """Build a tool selection prompt based on classification."""
    intent = ctx.intent
    hints = ctx.taxonomy_hints

    lines = [
        f"Query: {ctx.query}",
        f"Intent: {intent}",
        f"Complexity: {ctx.complexity}",
    ]

    if hints.get("domains"):
        lines.append(f"Likely domains: {', '.join(hints['domains'])}")
    if hints.get("entity_names"):
        lines.append(f"Entities mentioned: {', '.join(hints['entity_names'])}")

    temporal = ctx.inferred_temporal_range
    if temporal.get("is_temporal"):
        lines.append(f"Temporal range: {temporal.get('date_from', '')} to {temporal.get('date_to', '')}")

    # Include conversation history so the agent can make context-aware searches
    if ctx.conversation_history:
        lines.append("")
        lines.append("Conversation history (prior turns):")
        for turn in ctx.conversation_history[-5:]:
            lines.append(f"  User: {turn['query']}")
            answer_preview = turn["answer"][:200]
            if len(turn["answer"]) > 200:
                answer_preview += "..."
            lines.append(f"  Assistant: {answer_preview}")
        lines.append("")
        lines.append(
            "Use this conversation context to refine your searches. "
            "The user may be asking follow-up questions that reference prior answers."
        )

    lines.append("")
    lines.append("Select tools to call to gather evidence for answering this query.")
    lines.append("Respond with a JSON array of tool calls:")
    lines.append('[{"tool_name": "...", "arguments": {...}}, ...]')
    lines.append("")
    lines.append("Guidelines:")
    lines.append("- Start with search_chunks (mode='auto' is default — system auto-selects best retrieval mode)")
    lines.append("- For person/role queries ('who is X', 'what does X do'): ALWAYS call entity_profile first, then search_chunks with targeted queries like 'X role title position' or 'X hired'")
    lines.append("- For infrastructure listing queries ('what X does Y run/use/have'): search broadly first, then search for SPECIFIC names/terms found in initial results")
    lines.append("- For entity/relationship questions: use search_graphrag_artifacts (mode 'local' or 'drift') + traverse_graph")
    lines.append("- For temporal questions: use temporal_search with date ranges")
    lines.append("- For taxonomy questions: use search_taxonomy")
    lines.append("- For adversarial/no-answer: use search_chunks with a basic query to verify no relevant docs exist")
    lines.append("- Use top_k=40 for comprehensive queries — GPT-5.2 handles large context well. Use top_k=15-20 only for narrow lookups")
    lines.append("- IMPORTANT: After reviewing initial results, search for SPECIFIC entity names or terms found — don't just repeat broad queries")
    lines.append("- DOCUMENT-SPECIFIC: When the question mentions a document name, filename, or file type (e.g., 'Windows survey', 'CIS meeting', 'HSPD-12 list'), include the FILENAME in your query or use source_path_hint. Also try document_lookup(path='<partial filename>') to retrieve it directly")
    lines.append("- SEARCH DIVERSITY: After 3+ searches returning the same top_sources, try varying your query (use CONCRETE terms like system names, acronyms, procedure numbers) or use source_path_hint. Domain filters can help for cross-domain queries but ALWAYS do at least one broad search WITHOUT a domain filter first")
    lines.append("- NEAR-DUPLICATE AWARENESS: If top_sources shows multiple versions of the same document (v1/v2/v3, or dated copies), that content is already retrieved. Move on to searching for OTHER evidence")
    lines.append("- When in doubt, retrieve MORE rather than less — the model will filter the relevant information")
    lines.append("- For listing queries, always do at least 2 search passes with different query formulations")
    lines.append("- You can call multiple tools in one response")
    lines.append("- Maximum 5 iterations, aim for 2-3")
    lines.append("")
    lines.append("OVERVIEW QUERY STRATEGY (broad_overview intent, 'lay of the land', operations manual):")
    lines.append("  1. FIRST: search_taxonomy(query='overview', list_all_domains=true) — enumerate ALL domains")
    lines.append("  2. search_graphrag_artifacts(retrieval_mode='global', top_k=40) — community reports & domain profiles")
    lines.append("  3. For EACH major domain: search_chunks(domain=X, top_k=20, mode='drift') — domain-specific content")
    lines.append("  4. traverse_graph(domain_entities, domain=X) for top 5 domains — entity enumeration")
    lines.append("  5. Use ALL 5 iterations. NEVER stop early for overview queries — you need evidence from EVERY domain")
    lines.append("  Valid domain IDs: compliance, customer_support, engineering, executive, finance, general, hr, it_ops, legal, marketing, operations, procurement, product, sales, security")

    lines.append("")
    lines.append("Available tools:")
    schemas = get_tool_schemas()
    for schema in schemas:
        fn = schema.get("function", {})
        lines.append(f"  - {fn['name']}: {fn.get('description', '')}")
        for pname, pdef in fn.get("parameters", {}).get("properties", {}).items():
            lines.append(f"    {pname} ({pdef.get('type', 'string')}): {pdef.get('description', '')}")

    return "\n".join(lines)


async def react_handler(ctx: AgentContext) -> AsyncGenerator[str, None]:
    """Iterative ReAct loop — LLM selects tools, we execute them."""
    from services.chat_provider import ChatProvider

    max_iterations = config.V2_REACT_MAX_ITERATIONS
    chat = ChatProvider()

    for iteration in range(max_iterations):
        max_tool_calls = ctx.request_params.get("max_tool_calls", config.V2_MAX_TOOL_CALLS)
        if len(ctx.tool_call_log) >= max_tool_calls:
            break

        yield events.thinking(f"ReAct iteration {iteration + 1}/{max_iterations}")

        # Build tool selection prompt
        prompt = _build_react_tools_prompt(ctx)

        # Include previous tool results in context
        if ctx.tool_call_log:
            prompt += "\n\nPrevious tool results:\n"
            for tc in ctx.tool_call_log:
                prompt += f"- {tc.tool_name}({tc.arguments}): {tc.result_summary}\n"

            # Show already-retrieved document stems to guide diversity
            seen_stems = set()
            for ev in ctx.evidence:
                stem = path_stem(ev.path) if ev.path else ""
                if stem:
                    seen_stems.add(stem)
            if seen_stems:
                stem_list = ", ".join(sorted(seen_stems)[:20])
                prompt += f"\nDocuments already retrieved ({len(seen_stems)} unique stems): {stem_list}\n"

            prompt += "\nBased on these results, consider:"
            prompt += "\n- Are there specific entity names or terms in the results that you should search for directly?"
            prompt += "\n- Would a different tool (entity_profile, search_graphrag_artifacts) provide missing context?"
            prompt += "\n- If you have enough evidence to fully answer the query, respond with an empty array []."

        try:
            with traced_span(f"llm.react.{iteration}") as span:
                resp = await chat.async_complete(
                    messages=[
                        {"role": "system", "content": "You are a tool-calling agent. Respond only with valid JSON arrays."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_completion_tokens=2000 if ctx.intent == "broad_overview" else 1000,
                    user=ctx.request_params.get("user", ""),
                )
                react_max_tok = 2000 if ctx.intent == "broad_overview" else 1000
                set_llm_span_attributes(span, model=config.CHAT_MODEL, max_tokens=react_max_tok, temperature=0.0)
            text = chat.extract_text(resp).strip()

            # Parse JSON (handle markdown code blocks)
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            tool_calls = json.loads(text)
        except Exception as exc:
            log.warning("ReAct tool selection failed at iteration %d: %s", iteration, exc)
            # Fall back to basic search
            if not ctx.tool_call_log:
                tool_calls = [{"tool_name": "search_chunks", "arguments": {"query": ctx.query, "top_k": 20}}]
            else:
                tool_calls = []

        if not tool_calls:
            break

        # Execute tool calls
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            arguments = tc.get("arguments", {})
            tc_id = f"react_{iteration}_{tool_name}"

            yield events.tool_call_start(
                tool_call_id=tc_id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"ReAct iteration {iteration + 1}",
            )

            try:
                result = await execute_tool(ctx, tool_name, arguments, tool_call_id=tc_id)
                yield events.tool_call_result(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    duration_ms=result["duration_ms"],
                    result_summary=result["result"].get("summary", {}),
                )
            except Exception as exc:
                log.warning("Tool %s failed: %s", tool_name, exc)
                yield events.tool_call_error(
                    tool_call_id=tc_id,
                    error=str(exc),
                )

    ctx.current_state = AgentState.SYNTHESIZE
