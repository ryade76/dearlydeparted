"""Persist agent traces to disk as JSON for offline analysis."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from services.v2 import config

log = logging.getLogger("v2.trace_store")


async def persist_trace(ctx) -> str:
    """Persist an agent context as a trace JSON file. Returns the file path."""
    from services.v2.agent.states import AgentContext

    trace_dir = config.TRACE_PERSIST_DIR
    os.makedirs(trace_dir, exist_ok=True)

    trace_path = os.path.join(trace_dir, f"{ctx.trace_id}.json")

    trace_data = {
        "trace_id": ctx.trace_id,
        "conversation_id": ctx.conversation_id,
        "query": ctx.query,
        "timestamp": ctx.timestamp,
        "intent": ctx.intent,
        "complexity": ctx.complexity,
        "strategy": ctx.strategy,
        "taxonomy_hints": ctx.taxonomy_hints,
        "inferred_temporal_range": ctx.inferred_temporal_range,
        "answer": ctx.final_answer,
        "raw_answer": ctx.raw_answer,
        "citations": [c.model_dump() for c in ctx.citations],
        "tool_call_log": [t.model_dump() for t in ctx.tool_call_log],
        "state_timings": ctx.state_timings,
        "context_token_count": ctx.context_token_count,
        "evidence_count": len(ctx.evidence),
        "verification_passed": ctx.verification_passed,
        "retry_count": ctx.retry_count,
        "error_message": ctx.error_message,
        "plan_steps": [s.model_dump() for s in ctx.plan_steps],
        "graph_traversal_count": len(ctx.graph_traversal_results),
        "entity_profile_count": len(ctx.entity_profiles),
    }

    with open(trace_path, "w") as f:
        json.dump(trace_data, f, indent=2, default=str)

    log.info("Trace persisted: %s", trace_path)
    return trace_path


def load_trace(trace_id: str) -> Optional[Dict[str, Any]]:
    """Load a persisted trace by ID."""
    trace_path = os.path.join(config.TRACE_PERSIST_DIR, f"{trace_id}.json")
    if not os.path.exists(trace_path):
        return None
    with open(trace_path, "r") as f:
        return json.load(f)


def list_traces(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """List recent traces, sorted by modification time descending."""
    trace_dir = config.TRACE_PERSIST_DIR
    if not os.path.exists(trace_dir):
        return []

    files = []
    for fname in os.listdir(trace_dir):
        if fname.endswith(".json"):
            fpath = os.path.join(trace_dir, fname)
            files.append((fpath, os.path.getmtime(fpath)))

    files.sort(key=lambda x: x[1], reverse=True)
    files = files[offset:offset + limit]

    traces = []
    for fpath, _ in files:
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
                # Return summary, not full trace
                traces.append({
                    "trace_id": data.get("trace_id", ""),
                    "query": data.get("query", ""),
                    "intent": data.get("intent", ""),
                    "strategy": data.get("strategy", ""),
                    "timestamp": data.get("timestamp", 0),
                    "context_token_count": data.get("context_token_count", 0),
                    "evidence_count": data.get("evidence_count", 0),
                    "verification_passed": data.get("verification_passed", False),
                })
        except Exception:
            continue

    return traces
