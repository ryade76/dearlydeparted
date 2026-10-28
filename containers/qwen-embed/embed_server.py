#!/usr/bin/env python3
"""
Host embedding server for macOS Metal/MPS.

Exposes an OpenAI-compatible embeddings endpoint:
  POST /v1/embeddings

Default model:
  Qwen/Qwen3-Embedding-8B
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoTokenizer
import uvicorn

LOGGER = logging.getLogger("embed_server")

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-8B"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
DEFAULT_MAX_LENGTH = 8192
DEFAULT_BATCH_SIZE = 4

QUERY_INSTRUCTION = (
    "Given a user query, retrieve the most relevant passages from the corpus."
)


class EmbeddingRequest(BaseModel):
    model: Optional[str] = None
    input: Union[str, List[str]]
    encoding_format: Optional[str] = "float"
    dimensions: Optional[int] = Field(default=None, ge=32)
    user: Optional[str] = None
    task: Optional[str] = Field(default="document", pattern="^(document|query)$")
    instruction: Optional[str] = None
    normalize: Optional[bool] = True


class EmbeddingItem(BaseModel):
    object: str = "embedding"
    index: int
    embedding: List[float]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: List[EmbeddingItem]
    model: str
    usage: EmbeddingUsage


@dataclass
class EmbedRuntimeConfig:
    model_name: str
    batch_size: int
    max_length: int
    device_preference: str
    dtype_preference: str


class Embedder:
    def __init__(self, cfg: EmbedRuntimeConfig) -> None:
        self.cfg = cfg
        self.model_name = cfg.model_name
        self.batch_size = int(max(1, cfg.batch_size))
        self.max_length = int(max(128, cfg.max_length))
        self.device = self._resolve_device(cfg.device_preference)
        self.dtype_preference = (cfg.dtype_preference or "auto").strip().lower()
        self.tokenizer = None
        self.model = None
        self._load_model()

    @staticmethod
    def _resolve_device(preference: str) -> str:
        pref = (preference or "auto").strip().lower()
        if pref in {"mps", "metal"}:
            return "mps" if torch.backends.mps.is_available() else "cpu"
        if pref == "cpu":
            return "cpu"
        if pref == "cuda":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _resolve_dtype(self) -> torch.dtype:
        pref = self.dtype_preference
        if pref == "float32":
            return torch.float32
        if pref == "float16":
            return torch.float16
        if pref == "bfloat16":
            return torch.bfloat16
        if self.device in {"mps", "cuda"}:
            return torch.float16
        return torch.bfloat16

    def _load_model(self) -> None:
        torch_dtype = self._resolve_dtype()
        LOGGER.info("Loading model=%s device=%s dtype=%s", self.model_name, self.device, torch_dtype)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        tokenizer_max = int(getattr(self.tokenizer, "model_max_length", 0) or 0)
        if tokenizer_max > 0 and tokenizer_max < 1000000:
            if self.max_length > tokenizer_max:
                LOGGER.info(
                    "Capping max_length from %d to tokenizer/model limit %d",
                    self.max_length,
                    tokenizer_max,
                )
            self.max_length = max(32, min(self.max_length, tokenizer_max))
        try:
            self.model = AutoModel.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                torch_dtype=torch_dtype,
            )
        except Exception as exc:
            if torch_dtype == torch.float32:
                raise
            LOGGER.warning(
                "Model load failed with dtype=%s (%s), retrying with float32",
                torch_dtype,
                exc,
            )
            self.model = AutoModel.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                torch_dtype=torch.float32,
            )
        self.model.to(self.device)
        self.model.eval()
        LOGGER.info("Model loaded")

    def _token_count(self, texts: Sequence[str]) -> int:
        assert self.tokenizer is not None
        total = 0
        for text in texts:
            enc = self.tokenizer(text, add_special_tokens=True, truncation=True, max_length=self.max_length)
            total += len(enc.get("input_ids", []))
        return int(total)

    @staticmethod
    def _mean_pool(last_hidden: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        mask = attention_mask.unsqueeze(-1).expand(last_hidden.size()).float()
        masked = last_hidden * mask
        denom = torch.clamp(mask.sum(dim=1), min=1e-9)
        return masked.sum(dim=1) / denom

    def _fallback_encode(self, texts: Sequence[str], normalize: bool) -> List[List[float]]:
        assert self.tokenizer is not None
        assert self.model is not None

        all_vecs: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = list(texts[i : i + self.batch_size])
            tok = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            tok = {k: v.to(self.device) for k, v in tok.items()}
            with torch.no_grad():
                out = self.model(**tok)
            if hasattr(out, "last_hidden_state"):
                pooled = self._mean_pool(out.last_hidden_state, tok["attention_mask"])
            elif isinstance(out, tuple) and len(out) > 0:
                pooled = out[0][:, 0, :]
            else:
                raise RuntimeError("Unsupported model output format for fallback embedding")

            if normalize:
                pooled = F.normalize(pooled, p=2, dim=1)
            vecs = pooled.detach().float().cpu().tolist()
            all_vecs.extend(vecs)
        return all_vecs

    def encode(
        self,
        texts: Sequence[str],
        *,
        task: str,
        instruction: Optional[str],
        normalize: bool,
        dimensions: Optional[int],
    ) -> Dict[str, Any]:
        assert self.model is not None
        assert self.tokenizer is not None

        clean = [str(t or "") for t in texts]
        if not clean:
            return {"embeddings": [], "prompt_tokens": 0}

        instruction_text = instruction
        if not instruction_text and task == "query":
            instruction_text = QUERY_INSTRUCTION

        start = time.time()
        vectors: Optional[List[List[float]]] = None

        if hasattr(self.model, "encode"):
            try:
                kwargs: Dict[str, Any] = {
                    "batch_size": self.batch_size,
                }
                if instruction_text:
                    kwargs["instruction"] = instruction_text
                if normalize:
                    kwargs["normalize_embeddings"] = True

                raw = self.model.encode(clean, **kwargs)
                if isinstance(raw, torch.Tensor):
                    vectors = raw.detach().float().cpu().tolist()
                else:
                    vectors = [list(map(float, x)) for x in raw]
            except Exception as exc:
                LOGGER.warning("model.encode failed (%s), falling back to mean pooling", exc)

        if vectors is None:
            vectors = self._fallback_encode(clean, normalize=bool(normalize))

        if dimensions is not None:
            dims = int(dimensions)
            if dims <= 0:
                raise ValueError("dimensions must be positive")
            clipped: List[List[float]] = []
            for vec in vectors:
                if len(vec) >= dims:
                    clipped.append(vec[:dims])
                else:
                    clipped.append(vec + [0.0] * (dims - len(vec)))
            vectors = clipped

        prompt_tokens = self._token_count(clean)
        elapsed = time.time() - start
        LOGGER.info(
            "Embedded %d texts (%d tokens) in %.2fs", len(clean), prompt_tokens, elapsed
        )

        return {
            "embeddings": vectors,
            "prompt_tokens": int(prompt_tokens),
        }


def create_app(cfg: EmbedRuntimeConfig) -> FastAPI:
    app = FastAPI(title="Local Embed Server", version="1.0.0")
    runtime: Dict[str, Any] = {}

    @app.on_event("startup")
    def _startup() -> None:
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        runtime["embedder"] = Embedder(cfg)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        emb = runtime.get("embedder")
        return {
            "ok": emb is not None,
            "model": cfg.model_name,
            "device": emb.device if emb is not None else "unknown",
        }

    @app.get("/v1/models")
    def list_models() -> Dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": cfg.model_name,
                    "object": "model",
                    "owned_by": "local",
                }
            ],
        }

    @app.post("/v1/embeddings", response_model=EmbeddingResponse)
    def create_embeddings(req: EmbeddingRequest) -> EmbeddingResponse:
        emb: Embedder = runtime.get("embedder")
        if emb is None:
            raise HTTPException(status_code=503, detail="Embedder not initialized")

        model_name = req.model or emb.model_name
        if model_name != emb.model_name:
            raise HTTPException(
                status_code=400,
                detail=f"Loaded model is {emb.model_name}; requested {model_name}",
            )

        inputs = req.input if isinstance(req.input, list) else [req.input]
        if not inputs:
            raise HTTPException(status_code=400, detail="input is empty")

        try:
            out = emb.encode(
                inputs,
                task=(req.task or "document").lower(),
                instruction=req.instruction,
                normalize=bool(req.normalize),
                dimensions=req.dimensions,
            )
        except Exception as exc:
            LOGGER.exception("Embedding request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        vectors = out["embeddings"]
        data = [
            EmbeddingItem(index=i, embedding=vec)
            for i, vec in enumerate(vectors)
        ]
        usage = EmbeddingUsage(
            prompt_tokens=int(out["prompt_tokens"]),
            total_tokens=int(out["prompt_tokens"]),
        )
        return EmbeddingResponse(
            data=data,
            model=model_name,
            usage=usage,
        )

    return app


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Host embedding server for local RAG")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cpu", "cuda", "metal"])
    ap.add_argument("--dtype", default="auto", choices=["auto", "float32", "float16", "bfloat16"])
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    cfg = EmbedRuntimeConfig(
        model_name=str(args.model),
        batch_size=int(args.batch_size),
        max_length=int(args.max_length),
        device_preference=str(args.device),
        dtype_preference=str(args.dtype),
    )
    app = create_app(cfg)
    uvicorn.run(app, host=str(args.host), port=int(args.port), log_level=str(args.log_level).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
