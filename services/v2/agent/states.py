"""Agent state machine definitions: states enum and context model."""

from __future__ import annotations

import enum
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentState(str, enum.Enum):
    CLASSIFY = "classify"
    PLAN = "plan"
    EXECUTE = "execute"
    REACT = "react"
    SYNTHESIZE = "synthesize"
    GENERATE = "generate"
    VERIFY = "verify"
    EMIT_FINAL = "emit_final"
    ERROR = "error"
    DONE = "done"


class ToolCallRecord(BaseModel):
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: Dict[str, Any] = {}
    result_summary: Dict[str, Any] = {}
    duration_ms: float = 0.0
    error: str = ""


class EvidenceItem(BaseModel):
    item_id: str = ""
    evidence_type: str = ""  # chunk, entity_description, relationship, claim, community_report, etc.
    path: str = ""
    domain: str = ""
    bucket: str = ""
    date: str = ""
    text: str = ""
    score: float = 0.0
    source: str = ""  # es_chunks, qdrant_chunks, es_graphrag, qdrant_graphrag, neo4j, etc.
    metadata: Dict[str, Any] = {}


class CitationV2(BaseModel):
    index: int = 0
    item_id: str = ""
    path: str = ""
    text_excerpt: str = ""
    verified: bool = False
    verification_method: str = ""  # token_overlap, llm_nli
    verification_score: float = 0.0
    domain: str = ""
    bucket: str = ""
    date: str = ""
    doc_type: str = ""
    file_url: str = ""


class PlanStep(BaseModel):
    step_index: int = 0
    description: str = ""
    tool_name: str = ""
    tool_args: Dict[str, Any] = {}
    depends_on: List[int] = []
    completed: bool = False


class AgentContext(BaseModel):
    # Identity
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    conversation_id: str = ""
    query: str = ""
    timestamp: float = Field(default_factory=time.time)

    # Conversation history (prior turns from conversation store)
    conversation_history: List[Dict[str, str]] = []  # [{query, answer}, ...]

    # Classification
    intent: str = ""  # factual_lookup, synthesis, comparison, troubleshooting, etc.
    complexity: str = "simple"  # simple, moderate, complex
    strategy: str = "react"  # react, plan_execute
    taxonomy_hints: Dict[str, Any] = {}
    inferred_temporal_range: Dict[str, str] = {}

    # Plan (for plan_execute strategy)
    plan_steps: List[PlanStep] = []
    current_step_index: int = 0

    # Evidence (accumulated across all tool calls)
    evidence: List[EvidenceItem] = []
    graph_traversal_results: List[Dict[str, Any]] = []
    entity_profiles: List[Dict[str, Any]] = []
    tool_call_log: List[ToolCallRecord] = []

    # Generation
    assembled_context: str = ""
    context_token_count: int = 0
    raw_answer: str = ""
    citations: List[CitationV2] = []
    verification_passed: bool = False
    retry_count: int = 0

    # Output
    final_answer: str = ""
    state_timings: Dict[str, float] = {}

    # Current state tracking
    current_state: AgentState = AgentState.CLASSIFY
    error_message: str = ""

    # Request params carried through
    request_params: Dict[str, Any] = {}
