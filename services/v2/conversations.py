"""Conversation session store — in-memory with JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from services.v2 import config

log = logging.getLogger("v2.conversations")

CONVERSATIONS_DIR = os.path.join(config.TRACE_PERSIST_DIR, "conversations")


class ConversationTurn(BaseModel):
    role: str = ""  # "user" or "assistant"
    query: str = ""
    answer: str = ""
    trace_id: str = ""
    timestamp: float = Field(default_factory=time.time)


class ConversationSession(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    title: str = "New conversation"
    created_at: float = Field(default_factory=time.time)
    last_active_at: float = Field(default_factory=time.time)
    turns: List[ConversationTurn] = []


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_sessions: Dict[str, ConversationSession] = {}


def _persist(session: ConversationSession) -> None:
    """Write session to disk."""
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    path = os.path.join(CONVERSATIONS_DIR, f"{session.id}.json")
    with open(path, "w") as f:
        json.dump(session.model_dump(), f, indent=2, default=str)


def init_conversations() -> None:
    """Load all persisted conversations from disk into memory."""
    if not os.path.exists(CONVERSATIONS_DIR):
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
        return

    count = 0
    for fname in os.listdir(CONVERSATIONS_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(CONVERSATIONS_DIR, fname)
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
            session = ConversationSession(**data)
            _sessions[session.id] = session
            count += 1
        except Exception as exc:
            log.warning("Failed to load conversation %s: %s", fname, exc)

    log.info("Loaded %d conversations from disk", count)


def create_conversation(title: str = "") -> ConversationSession:
    """Create a new conversation session."""
    session = ConversationSession(title=title or "New conversation")
    _sessions[session.id] = session
    _persist(session)
    log.info("Created conversation %s", session.id)
    return session


def get_conversation(conversation_id: str) -> Optional[ConversationSession]:
    """Get a conversation by ID."""
    return _sessions.get(conversation_id)


def list_conversations(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """List conversations sorted by last_active_at descending."""
    sessions = sorted(_sessions.values(), key=lambda s: s.last_active_at, reverse=True)
    sessions = sessions[offset : offset + limit]
    return [
        {
            "id": s.id,
            "title": s.title,
            "turn_count": len(s.turns),
            "created_at": s.created_at,
            "last_active_at": s.last_active_at,
        }
        for s in sessions
    ]


def add_turn(
    conversation_id: str,
    query: str,
    answer: str,
    trace_id: str = "",
) -> Optional[ConversationTurn]:
    """Add a turn to a conversation. Returns the turn or None if not found."""
    session = _sessions.get(conversation_id)
    if session is None:
        return None

    turn = ConversationTurn(
        role="user",
        query=query,
        answer=answer,
        trace_id=trace_id,
    )
    session.turns.append(turn)
    session.last_active_at = time.time()

    # Auto-title from first query
    if len(session.turns) == 1 and session.title == "New conversation":
        session.title = query[:60] + ("..." if len(query) > 60 else "")

    _persist(session)
    return turn


def get_history_for_context(conversation_id: str) -> List[Dict[str, str]]:
    """Get conversation history formatted for LLM context injection.

    Returns list of {query, answer} dicts for prior turns.
    """
    session = _sessions.get(conversation_id)
    if session is None:
        return []
    return [{"query": t.query, "answer": t.answer} for t in session.turns]


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation session."""
    if conversation_id not in _sessions:
        return False

    del _sessions[conversation_id]
    path = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
    if os.path.exists(path):
        os.remove(path)

    log.info("Deleted conversation %s", conversation_id)
    return True


def update_title(conversation_id: str, title: str) -> bool:
    """Update a conversation's title."""
    session = _sessions.get(conversation_id)
    if session is None:
        return False
    session.title = title
    _persist(session)
    return True
