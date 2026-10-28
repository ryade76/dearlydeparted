#!/usr/bin/env python3
"""
OpenAI-compatible chat provider wrapper .
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import json
import requests


@dataclass
class ChatConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.1
    top_p: float = 0.9
    max_completion_tokens: int = 1200
    timeout_sec: int = 120
    max_attempts: int = 3
    retry_backoff_sec: float = 1.5


def load_chat_config() -> ChatConfig:
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("CHAT_MODEL", "gpt-4.1").strip()
    return ChatConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=float(os.getenv("CHAT_TEMPERATURE", "0.1")),
        top_p=float(os.getenv("CHAT_TOP_P", "0.9")),
        max_completion_tokens=int(os.getenv("CHAT_MAX_COMPLETION_TOKENS", "1200")),
        timeout_sec=int(os.getenv("CHAT_TIMEOUT_SEC", "120")),
        max_attempts=int(os.getenv("CHAT_MAX_ATTEMPTS", "3")),
        retry_backoff_sec=float(os.getenv("CHAT_RETRY_BACKOFF_SEC", "1.5")),
    )


class ChatProvider:
    def __init__(self, cfg: Optional[ChatConfig] = None) -> None:
        self.cfg = cfg or load_chat_config()
        if not self.cfg.base_url:
            raise ValueError("OPENAI_BASE_URL is not set")
        if not self.cfg.api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        if not self.cfg.model:
            raise ValueError("CHAT_MODEL is not set")

    @property
    def completions_url(self) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/chat/completions"

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            # Send max_completion_tokens (some OpenAI-compatible gateways reject max_tokens).
            "max_completion_tokens": int(
                self.cfg.max_completion_tokens if max_completion_tokens is None else max_completion_tokens
            ),
        }
        temperature_value = self.cfg.temperature if temperature is None else temperature
        top_p_value = self.cfg.top_p if top_p is None else top_p
        if temperature_value is not None:
            payload["temperature"] = float(temperature_value)
        if top_p_value is not None:
            payload["top_p"] = float(top_p_value)
        user_value = str(user or "").strip() or str(self.cfg.api_key or "").strip()
        if user_value:
            payload["user"] = user_value

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }
        attempts = max(1, int(self.cfg.max_attempts))
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(
                    self.completions_url,
                    headers=headers,
                    json=payload,
                    timeout=int(self.cfg.timeout_sec),
                )
            except Exception as exc:
                last_error = f"request failed: {exc}"
                if attempt < attempts:
                    time.sleep(max(0.0, float(self.cfg.retry_backoff_sec) * attempt))
                    continue
                raise RuntimeError(f"Chat request error: {exc}") from exc

            if resp.status_code < 300:
                data = resp.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Chat response is not a JSON object")
                return data

            last_error = f"{resp.status_code}: {resp.text[:500]}"
            if resp.status_code == 400:
                err_lower = str(resp.text or "").lower()
                adjusted = False
                unsupported_param = ("unsupported value" in err_lower) or ("unsupported parameter" in err_lower)
                if unsupported_param and "temperature" in err_lower and "temperature" in payload:
                    payload.pop("temperature", None)
                    adjusted = True
                if unsupported_param and "top_p" in err_lower and "top_p" in payload:
                    payload.pop("top_p", None)
                    adjusted = True
                if adjusted and attempt < attempts:
                    continue
            retryable = resp.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
            if retryable and attempt < attempts:
                time.sleep(max(0.0, float(self.cfg.retry_backoff_sec) * attempt))
                continue
            raise RuntimeError(f"Chat error {resp.status_code}: {resp.text[:500]}")

        raise RuntimeError(f"Chat failed after retries: {last_error or 'unknown error'}")

    @staticmethod
    def extract_text(resp_obj: Dict[str, Any]) -> str:
        direct = resp_obj.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        choices = resp_obj.get("choices")
        if not isinstance(choices, list) or not choices:
            output = resp_obj.get("output")
            if isinstance(output, list):
                chunks: List[str] = []
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if isinstance(content, str) and content.strip():
                        chunks.append(content.strip())
                if chunks:
                    return "\n".join(chunks).strip()
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content", "")
        if isinstance(message.get("text"), str) and str(message.get("text")).strip():
            return str(message.get("text")).strip()
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            # Handle multimodal content array structures.
            chunks: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if not isinstance(txt, str):
                        txt = item.get("content")
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt)
            return "\n".join(chunks).strip()
        text = first.get("text")
        if isinstance(text, str):
            return text.strip()
        return ""

    def _build_payload(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        user: Optional[str] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "max_completion_tokens": int(
                self.cfg.max_completion_tokens if max_completion_tokens is None else max_completion_tokens
            ),
        }
        temperature_value = self.cfg.temperature if temperature is None else temperature
        top_p_value = self.cfg.top_p if top_p is None else top_p
        if temperature_value is not None:
            payload["temperature"] = float(temperature_value)
        if top_p_value is not None:
            payload["top_p"] = float(top_p_value)
        user_value = str(user or "").strip() or str(self.cfg.api_key or "").strip()
        if user_value:
            payload["user"] = user_value
        if stream:
            payload["stream"] = True
        return payload

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.api_key}",
        }

    async def async_complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Non-streaming async completion using httpx."""
        payload = self._build_payload(
            messages, temperature=temperature, top_p=top_p,
            max_completion_tokens=max_completion_tokens, user=user,
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(float(self.cfg.timeout_sec))) as client:
            resp = await client.post(self.completions_url, json=payload, headers=self._headers)
            if resp.status_code >= 300:
                raise RuntimeError(f"Async chat error {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("Chat response is not a JSON object")
            return data

    async def async_stream_complete(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        user: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Async streaming generator yielding tokens from the LLM."""
        payload = self._build_payload(
            messages, temperature=temperature, top_p=top_p,
            max_completion_tokens=max_completion_tokens, user=user, stream=True,
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(float(self.cfg.timeout_sec))) as client:
            async with client.stream("POST", self.completions_url, json=payload, headers=self._headers) as response:
                if response.status_code >= 300:
                    body = await response.aread()
                    raise RuntimeError(f"Streaming error {response.status_code}: {body.decode()[:500]}")

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue
