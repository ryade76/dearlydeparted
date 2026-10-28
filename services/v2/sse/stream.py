"""SSE StreamingResponse wrapper for FastAPI."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi.responses import StreamingResponse


def sse_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    """Wrap an async generator of SSE-encoded strings into a FastAPI StreamingResponse."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
