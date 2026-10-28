#!/usr/bin/env python3
"""
Ingest RouterKit artifacts into Elasticsearch, Qdrant, and Neo4j.

Expected input:
  documents.jsonl from corpus_routerkit.py

This worker:
- creates deterministic doc/chunk IDs
- chunks sample text deterministically (char or tokenizer-based)
- calls local embedding endpoint (/v1/embeddings)
- upserts docs/chunks into ES
- upserts vectors into Qdrant
- upserts document/entity graph into Neo4j
- optionally creates GraphRAG retrieval artifacts and indexes them in ES/Qdrant
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from collections import defaultdict

try:
    from elasticsearch import Elasticsearch
    from elasticsearch.helpers import bulk as es_bulk
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Missing Elasticsearch client: pip install elasticsearch") from exc

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Missing Qdrant client: pip install qdrant-client") from exc

try:
    from neo4j import GraphDatabase
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Missing Neo4j driver: pip install neo4j") from exc

try:
    from transformers import AutoTokenizer
except Exception:  # pragma: no cover - optional; fallback chunking is supported.
    AutoTokenizer = None  # type: ignore[assignment]

try:
    from corpus_routerkit import extract_sample_and_core as routerkit_extract_sample_and_core
    from corpus_routerkit import ext_lower as routerkit_ext_lower
except Exception:
    routerkit_extract_sample_and_core = None  # type: ignore[assignment]
    routerkit_ext_lower = None  # type: ignore[assignment]

LOGGER = logging.getLogger("ingest_worker")

DEFAULT_EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
DEFAULT_TOKENIZER_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_DISABLE_TASK = str(os.getenv("EMBED_DISABLE_TASK", "1")).strip().lower() in {"1", "true", "yes", "on"}
EMBED_DISABLE_DIMENSIONS = str(os.getenv("EMBED_DISABLE_DIMENSIONS", "1")).strip().lower() in {"1", "true", "yes", "on"}
_EMBED_COMPAT_OMIT_TASK = EMBED_DISABLE_TASK
_EMBED_COMPAT_OMIT_DIMENSIONS = EMBED_DISABLE_DIMENSIONS

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[A-Za-z][.)])\s+")
_HEADING_RE = re.compile(r"^\s*[A-Z][A-Z0-9 _:/-]{4,}\s*$")
_LOG_PATH_HINT_RE = re.compile(
    r"(?:^|/)(?:logs?|incident|incidents|troubleshoot|debug|alerts?|events?|call-?in)(?:/|$)",
    re.IGNORECASE,
)
_LOG_TIMESTAMP_RE = re.compile(
    r"(?:"
    r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\b|"
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+[A-Z]{2,5}\s+\d{4}\b"
    r")",
    re.IGNORECASE,
)
_LOG_SEVERITY_RE = re.compile(r"\b(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|SEV[0-9])\b", re.IGNORECASE)
_KV_SIGNAL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.-]{1,40}=[^\s]+")
_LOG_EXT_HINTS = {"log", "out", "err", "trace", "trc", "jsonl", "ndjson"}
_CSV_MAIL_ROW_RE = re.compile(r"(?im)^email_row_id:\s*(\d+)\s*$")
_CSV_MAIL_HEADER_HINT_RE = re.compile(
    r'"subject"\s*,\s*"body"\s*,\s*"from:\s*\(name\)"',
    re.IGNORECASE,
)


@dataclass
class IngestDoc:
    doc_id: str
    path: str
    source_root: str
    ext: str
    size_bytes: int
    mtime_date: str
    mtime_year: int
    mtime_epoch_day: int
    effective_date: str
    effective_year: int
    effective_epoch_day: int
    sha256: str
    sample_text: str
    content_hash: str
    first_bucket: str
    sub_bucket: str
    semantic_domain: str
    semantic_decision: str
    semantic_score: int
    semantic_margin: int
    semantic_abstain_reason: str
    doc_type: str
    artifact_type: str
    sensitivity_level: str
    sensitivity_flags: List[str]
    tags: List[str]
    entities_systems: List[str]
    entities_vendors: List[str]
    entities_projects: List[str]
    entities_locations: List[str]
    entities_people: List[str]
    content_source: str
    extraction_char_count: int
    extraction_token_count: int
    extraction_quality_score: int
    extraction_quality_flags: List[str]


@dataclass
class ChunkDoc:
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int
    token_estimate: int
    vector: Optional[List[float]]
    metadata: Dict[str, Any]


@dataclass
class GraphArtifact:
    artifact_id: str
    artifact_type: str
    text: str
    metadata: Dict[str, Any]
    vector: Optional[List[float]]


def norm_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None:
        return []
    txt = str(value).strip()
    return [txt] if txt else []


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def deterministic_doc_id(path: str, size_bytes: int, mtime_date: str, content_hash: str) -> str:
    base = f"{path}|{size_bytes}|{mtime_date}|{content_hash}"
    return sha256_hex(base)


def _parse_iso_date(value: str) -> Optional[date]:
    txt = str(value or "").strip()
    if not txt:
        return None
    # Common mtime shape in router artifacts.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", txt)
    if m is not None:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    # Fallback for full ISO date-time strings.
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _epoch_day(d: Optional[date]) -> int:
    if d is None:
        return -1
    return int((d - date(1970, 1, 1)).days)


def _derive_temporal_fields(mtime_date_raw: str) -> Dict[str, Any]:
    parsed = _parse_iso_date(mtime_date_raw)
    mtime_date_norm = parsed.isoformat() if parsed is not None else str(mtime_date_raw or "").strip()
    mtime_year = int(parsed.year) if parsed is not None else -1
    mtime_epoch_day = _epoch_day(parsed)
    # Until content-date parsing is added, effective date mirrors mtime.
    return {
        "mtime_date": mtime_date_norm,
        "mtime_year": mtime_year,
        "mtime_epoch_day": mtime_epoch_day,
        "effective_date": mtime_date_norm,
        "effective_year": mtime_year,
        "effective_epoch_day": mtime_epoch_day,
    }


_TOKENIZER_CACHE: Dict[str, Any] = {}


def get_tokenizer(model_name: str) -> Any:
    if AutoTokenizer is None:
        raise RuntimeError(
            "Token chunking requested but transformers is unavailable. "
            "Install transformers or switch to --chunk-mode chars."
        )
    key = str(model_name).strip() or DEFAULT_TOKENIZER_MODEL
    tok = _TOKENIZER_CACHE.get(key)
    if tok is None:
        tok = AutoTokenizer.from_pretrained(key, trust_remote_code=True, use_fast=True)
        _TOKENIZER_CACHE[key] = tok
    return tok


def chunk_text_chars(text: str, chunk_chars: int, overlap_chars: int) -> List[Tuple[int, int, int, int, str]]:
    clean = str(text or "").strip()
    if not clean:
        return [(0, 0, -1, -1, "")]
    if chunk_chars <= 0:
        return [(0, len(clean), -1, -1, clean)]

    overlap = max(0, min(overlap_chars, max(0, chunk_chars - 1)))
    step = max(1, chunk_chars - overlap)

    out: List[Tuple[int, int, int, int, str]] = []
    start = 0
    n = len(clean)
    while start < n:
        end = min(n, start + chunk_chars)
        piece = clean[start:end]
        out.append((start, end, -1, -1, piece))
        if end >= n:
            break
        start += step
    return out


def chunk_text_tokens(text: str, chunk_tokens: int, overlap_tokens: int, tokenizer: Any) -> List[Tuple[int, int, int, int, str]]:
    clean = str(text or "").strip()
    if not clean:
        return [(0, 0, 0, 0, "")]
    if chunk_tokens <= 0:
        token_ids = tokenizer.encode(clean, add_special_tokens=False)
        return [(-1, -1, 0, int(len(token_ids)), clean)]

    token_ids = tokenizer.encode(clean, add_special_tokens=False)
    if not token_ids:
        return [(-1, -1, 0, 0, clean)]

    overlap = max(0, min(overlap_tokens, max(0, chunk_tokens - 1)))
    step = max(1, chunk_tokens - overlap)

    out: List[Tuple[int, int, int, int, str]] = []
    start = 0
    n = len(token_ids)
    while start < n:
        end = min(n, start + chunk_tokens)
        win = token_ids[start:end]
        piece = tokenizer.decode(
            win,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        ).strip()
        # Char offsets are undefined for token windows without a full offset map.
        out.append((-1, -1, start, end, piece))
        if end >= n:
            break
        start += step
    return out


def _split_structural_units(text: str) -> List[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n{2,}", clean) if b and b.strip()]
    units: List[str] = []

    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        bullet_like = sum(1 for ln in lines if _BULLET_RE.search(ln)) >= max(1, len(lines) // 4)
        heading_like = len(lines) == 1 and _HEADING_RE.search(lines[0]) is not None

        if heading_like:
            units.append(lines[0])
            continue
        if bullet_like:
            units.extend(lines)
            continue
        if len(block) > 2200:
            parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(block) if p.strip()]
            if parts:
                units.extend(parts)
                continue
        units.append(block)

    if not units:
        return [clean]
    return units


def _decode_token_window(tokenizer: Any, token_ids: Sequence[int]) -> str:
    if not token_ids:
        return ""
    return str(
        tokenizer.decode(
            list(token_ids),
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
    ).strip()


def _chunk_oversized_token_ids(
    token_ids: Sequence[int],
    *,
    tokenizer: Any,
    chunk_tokens: int,
    overlap_tokens: int,
    source_cursor: int,
) -> Tuple[List[Tuple[int, int, int, int, str]], int]:
    if not token_ids:
        return [], int(source_cursor)
    n = len(token_ids)
    overlap = max(0, min(overlap_tokens, max(0, chunk_tokens - 1)))
    step = max(1, chunk_tokens - overlap)
    out: List[Tuple[int, int, int, int, str]] = []

    start = 0
    while start < n:
        end = min(n, start + chunk_tokens)
        piece_ids = token_ids[start:end]
        piece = _decode_token_window(tokenizer, piece_ids)
        out.append((-1, -1, int(source_cursor + start), int(source_cursor + end), piece))
        if end >= n:
            break
        start += step
    next_cursor = int(source_cursor + max(1, n - overlap))
    return out, next_cursor


def chunk_text_tokens_structured(
    text: str,
    chunk_tokens: int,
    overlap_tokens: int,
    tokenizer: Any,
) -> List[Tuple[int, int, int, int, str]]:
    clean = str(text or "").strip()
    if not clean:
        return [(0, 0, 0, 0, "")]
    if chunk_tokens <= 0:
        token_ids = tokenizer.encode(clean, add_special_tokens=False)
        return [(-1, -1, 0, int(len(token_ids)), clean)]

    overlap = max(0, min(overlap_tokens, max(0, chunk_tokens - 1)))
    units = _split_structural_units(clean)
    if not units:
        return chunk_text_tokens(clean, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens, tokenizer=tokenizer)

    encoded_units: List[Tuple[str, List[int]]] = []
    for u in units:
        u_txt = str(u or "").strip()
        if not u_txt:
            continue
        u_ids = tokenizer.encode(u_txt, add_special_tokens=False)
        if u_ids:
            encoded_units.append((u_txt, u_ids))
    if not encoded_units:
        return chunk_text_tokens(clean, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens, tokenizer=tokenizer)

    out: List[Tuple[int, int, int, int, str]] = []
    source_cursor = 0
    current_parts: List[str] = []
    current_ids: List[int] = []

    def flush_current() -> None:
        nonlocal source_cursor, current_parts, current_ids
        if not current_ids:
            return
        piece = "\n\n".join(current_parts).strip()
        token_len = len(current_ids)
        out.append((-1, -1, int(source_cursor), int(source_cursor + token_len), piece))
        step = max(1, token_len - overlap)
        source_cursor += int(step)
        if overlap > 0:
            tail_ids = current_ids[-overlap:]
            tail_txt = _decode_token_window(tokenizer, tail_ids)
            current_ids = list(tail_ids) if tail_txt else []
            current_parts = [tail_txt] if tail_txt else []
        else:
            current_ids = []
            current_parts = []

    for unit_txt, unit_ids in encoded_units:
        unit_len = len(unit_ids)
        if unit_len > chunk_tokens:
            flush_current()
            pieces, source_cursor = _chunk_oversized_token_ids(
                unit_ids,
                tokenizer=tokenizer,
                chunk_tokens=chunk_tokens,
                overlap_tokens=overlap,
                source_cursor=source_cursor,
            )
            out.extend(pieces)
            current_parts = []
            current_ids = []
            continue

        proposed = len(current_ids) + unit_len
        if current_ids and proposed > chunk_tokens:
            flush_current()
        current_parts.append(unit_txt)
        current_ids.extend(unit_ids)

    flush_current()
    return out if out else [(-1, -1, 0, 0, clean)]


def _is_log_boundary_line(line: str) -> bool:
    ln = str(line or "").strip()
    if not ln:
        return False
    if _LOG_TIMESTAMP_RE.search(ln):
        return True
    if re.match(r"^\s*(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|SEV[0-9])\b", ln, flags=re.IGNORECASE):
        return True
    if re.match(r"^\s*\[(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|SEV[0-9])\]", ln, flags=re.IGNORECASE):
        return True
    if ln.startswith("{") and ln.endswith("}") and ("\"time\"" in ln or "\"timestamp\"" in ln or "\"level\"" in ln):
        return True
    return False


def split_log_entries(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    expanded = raw.replace("\r\n", "\n").replace("\r", "\n")
    expanded = re.sub(r"={3,}", "\n", expanded)
    expanded = re.sub(
        r"(?<!\n)(?=" + _LOG_TIMESTAMP_RE.pattern + r")",
        "\n",
        expanded,
    )
    lines = expanded.split("\n")

    entries: List[str] = []
    current: List[str] = []
    for line in lines:
        ln = str(line or "").rstrip()
        if not ln.strip():
            if current:
                current.append("")
            continue
        if _is_log_boundary_line(ln):
            if current:
                ent = "\n".join(current).strip()
                if ent:
                    entries.append(ent)
            current = [ln]
        else:
            if not current:
                current = [ln]
            else:
                current.append(ln)
    if current:
        ent = "\n".join(current).strip()
        if ent:
            entries.append(ent)

    if entries:
        return entries
    return [raw]


def split_csv_mail_entries(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if "email_row_id:" not in raw.lower():
        return []
    expanded = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n{2,}", expanded) if b and b.strip()]
    entries: List[str] = []
    for b in blocks:
        if _CSV_MAIL_ROW_RE.search(b) and ("Subject:" in b or "Body:" in b):
            entries.append(b)
    return entries


def chunk_text_csv_mail_entries(
    text: str,
    chunk_tokens: int,
    overlap_tokens: int,
    tokenizer: Any,
) -> List[Tuple[int, int, int, int, str]]:
    clean = str(text or "").strip()
    if not clean:
        return [(0, 0, 0, 0, "")]
    if chunk_tokens <= 0:
        token_ids = tokenizer.encode(clean, add_special_tokens=False)
        return [(-1, -1, 0, int(len(token_ids)), clean)]

    entries = split_csv_mail_entries(clean)
    if not entries:
        return chunk_text_tokens_structured(
            clean,
            chunk_tokens=chunk_tokens,
            overlap_tokens=overlap_tokens,
            tokenizer=tokenizer,
        )

    overlap = max(0, min(overlap_tokens, max(0, chunk_tokens - 1)))
    out: List[Tuple[int, int, int, int, str]] = []
    source_cursor = 0
    for ent in entries:
        entry_txt = str(ent or "").strip()
        if not entry_txt:
            continue
        entry_ids = tokenizer.encode(entry_txt, add_special_tokens=False)
        if not entry_ids:
            continue
        if len(entry_ids) > chunk_tokens:
            pieces, source_cursor = _chunk_oversized_token_ids(
                entry_ids,
                tokenizer=tokenizer,
                chunk_tokens=chunk_tokens,
                overlap_tokens=overlap,
                source_cursor=source_cursor,
            )
            out.extend(pieces)
            continue
        out.append((-1, -1, int(source_cursor), int(source_cursor + len(entry_ids)), entry_txt))
        source_cursor += int(max(1, len(entry_ids) - overlap))
    if out:
        return out
    return chunk_text_tokens_structured(
        clean,
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
        tokenizer=tokenizer,
    )


def is_csv_mail_document(doc: IngestDoc, text: str) -> bool:
    ext = str(doc.ext or "").strip().lower().lstrip(".")
    if ext != "csv":
        return False
    source = str(doc.content_source or "").strip().lower()
    sample = str(text or "")
    if "csv_mail_rows" in source:
        return True
    if "email_row_id:" in sample.lower() and ("Subject:" in sample or "Body:" in sample):
        return True
    if _CSV_MAIL_HEADER_HINT_RE.search(sample):
        return True
    return False


def chunk_text_log_entries(
    text: str,
    chunk_tokens: int,
    overlap_tokens: int,
    tokenizer: Any,
) -> List[Tuple[int, int, int, int, str]]:
    clean = str(text or "").strip()
    if not clean:
        return [(0, 0, 0, 0, "")]
    if chunk_tokens <= 0:
        token_ids = tokenizer.encode(clean, add_special_tokens=False)
        return [(-1, -1, 0, int(len(token_ids)), clean)]

    overlap = max(0, min(overlap_tokens, max(0, chunk_tokens - 1)))
    overlap_entries = 1 if overlap > 0 else 0
    entries = split_log_entries(clean)
    encoded: List[Tuple[str, List[int]]] = []
    for ent in entries:
        ent_txt = str(ent or "").strip()
        if not ent_txt:
            continue
        ent_ids = tokenizer.encode(ent_txt, add_special_tokens=False)
        if ent_ids:
            encoded.append((ent_txt, ent_ids))
    if not encoded:
        return chunk_text_tokens_structured(clean, chunk_tokens=chunk_tokens, overlap_tokens=overlap, tokenizer=tokenizer)

    out: List[Tuple[int, int, int, int, str]] = []
    source_cursor = 0
    i = 0
    while i < len(encoded):
        parts: List[str] = []
        tok_count = 0
        j = i
        while j < len(encoded):
            ent_txt, ent_ids = encoded[j]
            if len(ent_ids) > chunk_tokens:
                if parts:
                    break
                pieces, source_cursor = _chunk_oversized_token_ids(
                    ent_ids,
                    tokenizer=tokenizer,
                    chunk_tokens=chunk_tokens,
                    overlap_tokens=overlap,
                    source_cursor=source_cursor,
                )
                out.extend(pieces)
                j += 1
                i = j
                break
            if parts and (tok_count + len(ent_ids)) > chunk_tokens:
                break
            parts.append(ent_txt)
            tok_count += len(ent_ids)
            j += 1
        if not parts:
            continue
        piece = "\n".join(parts).strip()
        out.append((-1, -1, int(source_cursor), int(source_cursor + tok_count), piece))
        step = max(1, tok_count - overlap)
        source_cursor += int(step)
        if j >= len(encoded):
            break
        if overlap_entries > 0:
            i = max(i + 1, j - overlap_entries)
        else:
            i = j

    return out if out else chunk_text_tokens_structured(clean, chunk_tokens=chunk_tokens, overlap_tokens=overlap, tokenizer=tokenizer)


def is_log_like_document(doc: IngestDoc, text: str) -> bool:
    ext = str(doc.ext or "").strip().lower().lstrip(".")
    path = str(doc.path or "").strip().lower()
    sample = str(text or "")
    if ext in _LOG_EXT_HINTS:
        return True
    if _LOG_PATH_HINT_RE.search(path):
        return True
    ts_hits = len(_LOG_TIMESTAMP_RE.findall(sample))
    sev_hits = len(_LOG_SEVERITY_RE.findall(sample))
    kv_hits = len(_KV_SIGNAL_RE.findall(sample))
    if ts_hits >= 2 and (sev_hits >= 1 or kv_hits >= 3):
        return True
    if ("traceback" in sample.lower() or "exception" in sample.lower()) and (ts_hits >= 1 or sev_hits >= 1):
        return True
    return False


def resolve_chunk_mode_for_doc(doc: IngestDoc, requested_mode: str, tokenizer: Optional[Any]) -> str:
    mode = str(requested_mode or "auto").strip().lower()
    if mode == "auto":
        if tokenizer is None:
            return "chars"
        if is_csv_mail_document(doc, doc.sample_text):
            return "csv_mail"
        if is_log_like_document(doc, doc.sample_text):
            return "log_entries"
        return "tokens_structured"
    if mode in {"tokens", "tokens_structured", "log_entries", "csv_mail"} and tokenizer is None:
        return "chars"
    if mode in {"chars", "tokens", "tokens_structured", "log_entries", "csv_mail"}:
        return mode
    return "chars"


def chunk_text(
    text: str,
    *,
    chunk_mode: str,
    chunk_chars: int,
    overlap_chars: int,
    chunk_tokens: int,
    overlap_tokens: int,
    tokenizer: Optional[Any],
) -> List[Tuple[int, int, int, int, str]]:
    mode = str(chunk_mode or "chars").strip().lower()
    if mode == "tokens":
        if tokenizer is None:
            raise RuntimeError("Token mode requested without tokenizer")
        return chunk_text_tokens(text, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens, tokenizer=tokenizer)
    if mode == "tokens_structured":
        if tokenizer is None:
            raise RuntimeError("Structured token mode requested without tokenizer")
        return chunk_text_tokens_structured(
            text,
            chunk_tokens=chunk_tokens,
            overlap_tokens=overlap_tokens,
            tokenizer=tokenizer,
        )
    if mode == "log_entries":
        if tokenizer is None:
            raise RuntimeError("Log-entry mode requested without tokenizer")
        return chunk_text_log_entries(
            text,
            chunk_tokens=chunk_tokens,
            overlap_tokens=overlap_tokens,
            tokenizer=tokenizer,
        )
    if mode == "csv_mail":
        if tokenizer is None:
            raise RuntimeError("CSV-mail mode requested without tokenizer")
        return chunk_text_csv_mail_entries(
            text,
            chunk_tokens=chunk_tokens,
            overlap_tokens=overlap_tokens,
            tokenizer=tokenizer,
        )
    return chunk_text_chars(text, chunk_chars=chunk_chars, overlap_chars=overlap_chars)


def assess_extraction_quality(text: str, ext: str, content_source: str) -> Dict[str, Any]:
    clean = str(text or "").strip()
    ext_l = str(ext or "").strip().lower()
    source = str(content_source or "").strip().lower()
    char_count = len(clean)
    tokens = [tok for tok in clean.replace("\n", " ").split() if tok]
    token_count = len(tokens)
    lower_tokens = [t.lower() for t in tokens if t]
    unique_ratio = (len(set(lower_tokens)) / float(token_count)) if token_count > 0 else 0.0
    short_token_ratio = (
        sum(1 for t in tokens if len(t) <= 2) / float(token_count)
        if token_count > 0
        else 0.0
    )

    flags: List[str] = []
    if char_count < 300:
        flags.append("low_char_count")
    if token_count < 60:
        flags.append("sparse_tokens")
    if token_count >= 80 and unique_ratio < 0.22:
        flags.append("low_token_diversity")
    if token_count >= 80 and short_token_ratio > 0.55:
        flags.append("short_token_noise")
    if ext_l in {"pdf", "doc", "docx", "ppt", "pptx"} and token_count < 120:
        flags.append("under_extracted_structured_doc")
    if "extract_failed" in source or "empty_extract" in source:
        flags.append("extractor_no_content")

    score = 100
    penalties = {
        "low_char_count": 20,
        "sparse_tokens": 20,
        "low_token_diversity": 20,
        "short_token_noise": 20,
        "under_extracted_structured_doc": 20,
        "extractor_no_content": 25,
    }
    for flag in flags:
        score -= int(penalties.get(flag, 10))
    score = max(0, min(100, score))
    return {
        "char_count": int(char_count),
        "token_count": int(token_count),
        "quality_score": int(score),
        "quality_flags": flags,
    }


def update_doc_extraction_quality(doc: IngestDoc) -> None:
    q = assess_extraction_quality(doc.sample_text, doc.ext, doc.content_source)
    doc.extraction_char_count = int(q["char_count"])
    doc.extraction_token_count = int(q["token_count"])
    doc.extraction_quality_score = int(q["quality_score"])
    doc.extraction_quality_flags = [str(x) for x in (q.get("quality_flags") or []) if str(x).strip()]


def parse_routerkit_doc(obj: Dict[str, Any]) -> IngestDoc:
    path = str(obj.get("path", "")).strip()
    source_root = str(obj.get("source_root", "root")).strip() or "root"

    file_obj = obj.get("file") if isinstance(obj.get("file"), dict) else {}
    routing = obj.get("routing") if isinstance(obj.get("routing"), dict) else {}
    semantic = routing.get("semantic") if isinstance(routing.get("semantic"), dict) else {}
    structural = routing.get("structural") if isinstance(routing.get("structural"), dict) else {}
    sample = obj.get("sample") if isinstance(obj.get("sample"), dict) else {}
    facets = obj.get("facets") if isinstance(obj.get("facets"), dict) else {}

    sample_text = str(sample.get("text", "") or "").strip()
    if not sample_text:
        # Fallback to metadata text when extraction is empty.
        sample_text = " ".join(
            [
                path,
                str(facets.get("doc_type", "")),
                str(facets.get("artifact_type", "")),
                " ".join(norm_list(facets.get("tags"))),
                " ".join(norm_list(facets.get("entities_projects"))),
            ]
        ).strip()

    content_hash = sha256_hex(sample_text)
    size_bytes = int(file_obj.get("size_bytes", 0) or 0)
    mtime_date = str(file_obj.get("mtime_date", ""))
    temporal = _derive_temporal_fields(mtime_date)

    doc_id = deterministic_doc_id(path, size_bytes, str(temporal["mtime_date"]), content_hash)

    doc = IngestDoc(
        doc_id=doc_id,
        path=path,
        source_root=source_root,
        ext=str(file_obj.get("ext", "")),
        size_bytes=size_bytes,
        mtime_date=str(temporal["mtime_date"]),
        mtime_year=int(temporal["mtime_year"]),
        mtime_epoch_day=int(temporal["mtime_epoch_day"]),
        effective_date=str(temporal["effective_date"]),
        effective_year=int(temporal["effective_year"]),
        effective_epoch_day=int(temporal["effective_epoch_day"]),
        sha256=str(file_obj.get("sha256", "")),
        sample_text=sample_text,
        content_hash=content_hash,
        first_bucket=str(structural.get("first_bucket", routing.get("first_bucket", "root_docs"))),
        sub_bucket=str(structural.get("sub_bucket", routing.get("sub_bucket", "root_docs"))),
        semantic_domain=str(semantic.get("domain", facets.get("semantic_domain", "general"))),
        semantic_decision=str(semantic.get("decision", facets.get("semantic_decision", "abstained"))),
        semantic_score=int(semantic.get("score", 0) or 0),
        semantic_margin=int(semantic.get("margin", 0) or 0),
        semantic_abstain_reason=str(semantic.get("abstain_reason", "")),
        doc_type=str(facets.get("doc_type", "other")),
        artifact_type=str(facets.get("artifact_type", "other")),
        sensitivity_level=str(facets.get("sensitivity_level", "normal")),
        sensitivity_flags=norm_list(facets.get("sensitivity_flags")),
        tags=norm_list(facets.get("tags")),
        entities_systems=norm_list(facets.get("entities_systems")),
        entities_vendors=norm_list(facets.get("entities_vendors")),
        entities_projects=norm_list(facets.get("entities_projects")),
        entities_locations=norm_list(facets.get("entities_locations")),
        entities_people=norm_list(facets.get("entities_people")),
        content_source="routerkit_sample",
        extraction_char_count=0,
        extraction_token_count=0,
        extraction_quality_score=0,
        extraction_quality_flags=[],
    )
    update_doc_extraction_quality(doc)
    return doc


def resolve_filesystem_doc_text(
    doc: IngestDoc,
    *,
    corpus_root: Path,
    sample_mode: str,
    max_chars: int,
    pdf_max_pages: int,
    pptx_max_slides: int,
    xlsx_max_strings: int,
    xlsx_max_chars: int,
    zip_max_members: int,
    binary_max_bytes: int,
) -> Tuple[str, str]:
    rel_path = str(doc.path or "").strip()
    if not rel_path:
        return "", "missing_path"

    candidate = Path(rel_path)
    fs_path = candidate if candidate.is_absolute() else (corpus_root / candidate)
    try:
        fs_path = fs_path.resolve()
    except Exception:
        pass

    if not fs_path.exists() or not fs_path.is_file():
        return "", f"not_found:{fs_path}"

    ext = str(doc.ext or "").strip().lower()
    if not ext:
        if routerkit_ext_lower is not None:
            try:
                ext = str(routerkit_ext_lower(fs_path) or "")
            except Exception:
                ext = ""
        if not ext:
            ext = fs_path.suffix.lower().lstrip(".")
    if not ext:
        ext = "(noext)"

    limit_chars = int(max_chars)
    if limit_chars <= 0:
        limit_chars = 2000000

    # Prefer row-aware Outlook CSV parsing when ingesting full filesystem text.
    # This preserves per-email boundaries before chunking.
    sample_mode_l = str(sample_mode or "full").strip().lower()
    if ext == "csv" and sample_mode_l == "full":
        csv_text, csv_method = _sample_csv_mail_rows(fs_path, max_chars=limit_chars)
        if csv_text:
            return csv_text, f"filesystem:{csv_method}"

    if routerkit_extract_sample_and_core is not None:
        try:
            _, sample, method, _ = routerkit_extract_sample_and_core(
                fs_path,
                ext=ext,
                limit_chars=limit_chars,
                pdf_max_pages=int(pdf_max_pages),
                pptx_max_slides=int(pptx_max_slides),
                xlsx_max_strings=int(xlsx_max_strings),
                xlsx_max_chars=int(xlsx_max_chars),
                zip_max_members=int(zip_max_members),
                binary_max_bytes=int(binary_max_bytes),
                sample_mode=str(sample_mode or "full"),
            )
            text = str(sample or "").strip()
            if text:
                return text, f"filesystem:{method or 'extract'}"
            return "", "empty_extract"
        except Exception as exc:
            LOGGER.warning("Filesystem extraction failed for %s (%s): %s", doc.path, ext, exc)

    # Minimal fallback for plain text files when RouterKit extractor is unavailable.
    try:
        with fs_path.open("r", encoding="utf-8", errors="replace") as f:
            text = f.read(limit_chars).strip()
        if text:
            return text, "filesystem:text_fallback"
    except Exception:
        pass
    return "", "extract_failed"


def _sample_csv_mail_rows(
    fs_path: Path,
    *,
    max_chars: int,
    max_rows: int = 400,
    max_subject_chars: int = 240,
    max_body_chars: int = 2200,
) -> Tuple[str, str]:
    try:
        with fs_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = [str(x) for x in (reader.fieldnames or []) if str(x).strip()]
            if not fieldnames:
                return "", "csv_mail:no_header"

            norm_pairs: List[Tuple[str, str]] = []
            for raw in fieldnames:
                norm = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(raw).replace("\ufeff", "").lower())).strip("_")
                if norm:
                    norm_pairs.append((norm, str(raw)))

            def pick(*terms: str) -> str:
                req = [str(t).strip().lower() for t in terms if str(t).strip()]
                for norm_name, raw_name in norm_pairs:
                    if all(t in norm_name for t in req):
                        return raw_name
                return ""

            subject_col = pick("subject")
            body_col = pick("body")
            from_name_col = pick("from", "name")
            from_addr_col = pick("from", "address")
            to_name_col = pick("to", "name")
            to_addr_col = pick("to", "address")
            cc_name_col = pick("cc", "name")
            sent_col = pick("sent")
            received_col = pick("received")
            categories_col = pick("categories")
            importance_col = pick("importance")
            sensitivity_col = pick("sensitivity")

            has_mailish = bool(subject_col and body_col and (from_name_col or from_addr_col or to_name_col or to_addr_col))
            if not has_mailish:
                mail_style_hits = 0
                for norm_name, _raw_name in norm_pairs:
                    if norm_name.startswith("from_") or norm_name.startswith("to_") or norm_name.startswith("cc_") or norm_name.startswith("bcc_"):
                        mail_style_hits += 1
                    elif norm_name in {"subject", "body", "sent", "received", "importance", "sensitivity", "categories"}:
                        mail_style_hits += 1
                if mail_style_hits < 4 or not (subject_col or body_col):
                    return "", "csv_mail:not_detected"

            def clean_cell(value: Any, max_len: int) -> str:
                txt = str(value or "")
                txt = re.sub(r"\s+", " ", txt.replace("\r\n", "\n").replace("\r", "\n")).strip()
                if max_len > 0 and len(txt) > max_len:
                    txt = txt[: max(0, int(max_len) - 3)].rstrip() + "..."
                return txt

            blocks: List[str] = []
            total = 0
            emitted = 0
            forced_trimmed = 0
            for row_idx, row in enumerate(reader, start=1):
                if emitted >= int(max_rows):
                    break
                if not isinstance(row, dict):
                    continue

                subject = clean_cell(row.get(subject_col, ""), max_subject_chars)
                body = clean_cell(row.get(body_col, ""), max_body_chars)
                from_name = clean_cell(row.get(from_name_col, ""), 220)
                from_addr = clean_cell(row.get(from_addr_col, ""), 220)
                to_name = clean_cell(row.get(to_name_col, ""), 220)
                to_addr = clean_cell(row.get(to_addr_col, ""), 220)
                cc_name = clean_cell(row.get(cc_name_col, ""), 220)
                sent = clean_cell(row.get(sent_col, ""), 120)
                received = clean_cell(row.get(received_col, ""), 120)
                categories = clean_cell(row.get(categories_col, ""), 160)
                importance = clean_cell(row.get(importance_col, ""), 80)
                sensitivity = clean_cell(row.get(sensitivity_col, ""), 80)

                if not subject and not body:
                    continue

                lines = [f"email_row_id: {row_idx}"]
                if subject:
                    lines.append(f"Subject: {subject}")
                if from_name or from_addr:
                    lines.append(f"From: {from_name} <{from_addr}>".strip())
                if to_name or to_addr:
                    lines.append(f"To: {to_name} <{to_addr}>".strip())
                if cc_name:
                    lines.append(f"CC: {cc_name}")
                if sent:
                    lines.append(f"Sent: {sent}")
                if received:
                    lines.append(f"Received: {received}")
                if categories:
                    lines.append(f"Categories: {categories}")
                if importance:
                    lines.append(f"Importance: {importance}")
                if sensitivity:
                    lines.append(f"Sensitivity: {sensitivity}")
                if body:
                    lines.append(f"Body: {body}")
                block = "\n".join(lines).strip()
                if not block:
                    continue

                extra = len(block) + (2 if blocks else 0)
                if total + extra > int(max_chars):
                    # Keep at least one trimmed row if the first email exceeds
                    # max_chars; prevents false "no_rows" fallback.
                    if not blocks:
                        budget = max(1, int(max_chars) - total)
                        trimmed = block[:budget].strip()
                        if len(block) > budget and budget > 3:
                            trimmed = trimmed[: max(0, budget - 3)].rstrip() + "..."
                        if trimmed:
                            block = trimmed
                            extra = len(block)
                            blocks.append(block)
                            total += extra
                            emitted += 1
                            forced_trimmed += 1
                    break
                blocks.append(block)
                total += extra
                emitted += 1

            if not blocks:
                return "", "csv_mail:no_rows"
            return "\n\n".join(blocks), f"csv_mail_rows:{emitted}:forced_trimmed:{forced_trimmed}"
    except Exception as exc:
        return "", f"csv_mail:parse_error:{type(exc).__name__}"


def qdrant_call_with_retry(desc: str, fn: Any, retries: int = 4, backoff_sec: float = 1.5) -> Any:
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= retries:
                raise
            sleep_s = backoff_sec * attempt
            LOGGER.warning(
                "Qdrant call failed (%s), attempt=%d/%d, retrying in %.1fs: %s",
                desc,
                attempt,
                retries,
                sleep_s,
                exc,
            )
            time.sleep(sleep_s)


def qdrant_point_id(chunk_id: str) -> str:
    # Qdrant point IDs must be uint64 or UUID; use deterministic UUID5.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(chunk_id)))


def read_routerkit_documents(path: Path, limit: int) -> List[IngestDoc]:
    docs: List[IngestDoc] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            doc = parse_routerkit_doc(obj)
            if not doc.path:
                continue
            docs.append(doc)
            if limit > 0 and len(docs) >= limit:
                break
    return docs


def to_doc_record(doc: IngestDoc, chunk_count: int) -> Dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "path": doc.path,
        "source_root": doc.source_root,
        "size_bytes": doc.size_bytes,
        "mtime_date": doc.mtime_date,
        "mtime_year": int(doc.mtime_year),
        "mtime_epoch_day": int(doc.mtime_epoch_day),
        "effective_date": doc.effective_date,
        "effective_year": int(doc.effective_year),
        "effective_epoch_day": int(doc.effective_epoch_day),
        "ext": doc.ext,
        "sha256": doc.sha256,
        "first_bucket": doc.first_bucket,
        "sub_bucket": doc.sub_bucket,
        "semantic_domain": doc.semantic_domain,
        "semantic_decision": doc.semantic_decision,
        "semantic_score": doc.semantic_score,
        "semantic_margin": doc.semantic_margin,
        "semantic_abstain_reason": doc.semantic_abstain_reason,
        "doc_type": doc.doc_type,
        "artifact_type": doc.artifact_type,
        "sensitivity_level": doc.sensitivity_level,
        "sensitivity_flags": doc.sensitivity_flags,
        "tags": doc.tags,
        "content_source": doc.content_source,
        "entities": {
            "systems": doc.entities_systems,
            "vendors": doc.entities_vendors,
            "projects": doc.entities_projects,
            "locations": doc.entities_locations,
            "people": doc.entities_people,
        },
        "content_hash": doc.content_hash,
        "chunk_count": int(chunk_count),
        "extraction_char_count": int(doc.extraction_char_count),
        "extraction_token_count": int(doc.extraction_token_count),
        "extraction_quality_score": int(doc.extraction_quality_score),
        "extraction_quality_flags": list(doc.extraction_quality_flags),
    }


def build_chunks(
    doc: IngestDoc,
    *,
    chunk_mode: str,
    chunk_chars: int,
    overlap_chars: int,
    chunk_tokens: int,
    overlap_tokens: int,
    tokenizer: Optional[Any],
) -> List[ChunkDoc]:
    requested_mode = str(chunk_mode or "auto").strip().lower()
    resolved_mode = resolve_chunk_mode_for_doc(doc, requested_mode, tokenizer)
    if resolved_mode != requested_mode:
        LOGGER.debug(
            "Chunk mode resolved for %s: requested=%s resolved=%s",
            doc.path,
            requested_mode,
            resolved_mode,
        )
    spans = chunk_text(
        doc.sample_text,
        chunk_mode=resolved_mode,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
        tokenizer=tokenizer,
    )
    rows: List[ChunkDoc] = []
    for idx, (char_start, char_end, token_start, token_end, text) in enumerate(spans):
        chunk_id = f"{doc.doc_id}:{idx}"
        token_est = max(1, int(token_end - token_start)) if token_start >= 0 and token_end >= token_start else (max(1, len(text.split())) if text else 0)
        log_entries = split_log_entries(text) if resolved_mode == "log_entries" else []
        csv_mail_entries = split_csv_mail_entries(text) if resolved_mode == "csv_mail" else []
        csv_row_ids = [int(x) for x in _CSV_MAIL_ROW_RE.findall(text)] if resolved_mode == "csv_mail" else []
        log_levels = sorted(
            {str(x).upper() for x in _LOG_SEVERITY_RE.findall(text)}
        ) if resolved_mode == "log_entries" else []
        ts_match = _LOG_TIMESTAMP_RE.search(text) if resolved_mode == "log_entries" else None
        structural_markers = (
            int(len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)]|[A-Za-z][.)])\s+", str(text or ""))))
            if resolved_mode in {"tokens_structured", "log_entries", "csv_mail"}
            else 0
        )
        meta = {
            "chunk_id": chunk_id,
            "doc_id": doc.doc_id,
            "path": doc.path,
            "source_root": doc.source_root,
            "mtime_date": doc.mtime_date,
            "mtime_year": int(doc.mtime_year),
            "mtime_epoch_day": int(doc.mtime_epoch_day),
            "effective_date": doc.effective_date,
            "effective_year": int(doc.effective_year),
            "effective_epoch_day": int(doc.effective_epoch_day),
            "chunk_index": idx,
            "chunk_mode": resolved_mode,
            "chunk_mode_requested": requested_mode,
            "chunk_mode_resolved": resolved_mode,
            "log_entry_count": int(len(log_entries)) if resolved_mode == "log_entries" else 0,
            "log_levels": log_levels,
            "log_first_timestamp": str(ts_match.group(0)) if ts_match is not None else "",
            "csv_mail_entry_count": int(len(csv_mail_entries)) if resolved_mode == "csv_mail" else 0,
            "csv_mail_row_ids": csv_row_ids[:8] if resolved_mode == "csv_mail" else [],
            "csv_mail_row_id_min": int(min(csv_row_ids)) if csv_row_ids else -1,
            "csv_mail_row_id_max": int(max(csv_row_ids)) if csv_row_ids else -1,
            "structural_marker_count": int(structural_markers),
            "char_start": char_start,
            "char_end": char_end,
            "token_start": token_start,
            "token_end": token_end,
            "token_estimate": token_est,
            "first_bucket": doc.first_bucket,
            "sub_bucket": doc.sub_bucket,
            "semantic_domain": doc.semantic_domain,
            "semantic_decision": doc.semantic_decision,
            "semantic_score": doc.semantic_score,
            "semantic_margin": doc.semantic_margin,
            "artifact_type": doc.artifact_type,
            "doc_type": doc.doc_type,
            "sensitivity_level": doc.sensitivity_level,
            "tags": doc.tags,
            "content_source": doc.content_source,
            "extraction_char_count": int(doc.extraction_char_count),
            "extraction_token_count": int(doc.extraction_token_count),
            "extraction_quality_score": int(doc.extraction_quality_score),
            "extraction_quality_flags": list(doc.extraction_quality_flags),
            "entities_systems": doc.entities_systems,
            "entities_vendors": doc.entities_vendors,
            "entities_projects": doc.entities_projects,
            "entities_people": doc.entities_people,
            "entities_locations": doc.entities_locations,
        }
        rows.append(
            ChunkDoc(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                chunk_index=idx,
                text=text,
                char_start=char_start,
                char_end=char_end,
                token_estimate=token_est,
                vector=None,
                metadata=meta,
            )
        )
    return rows


def embed_batch(
    embed_url: str,
    model: str,
    texts: Sequence[str],
    *,
    task: str,
    timeout_sec: int,
    dimensions: Optional[int],
) -> List[List[float]]:
    global _EMBED_COMPAT_OMIT_TASK, _EMBED_COMPAT_OMIT_DIMENSIONS

    base_input = list(texts)
    include_task_default = bool(task) and not _EMBED_COMPAT_OMIT_TASK
    include_dim_default = dimensions is not None and not _EMBED_COMPAT_OMIT_DIMENSIONS

    # Prefer the strict OpenAI-compatible minimal payload first, then expand.
    variant_order: List[Tuple[bool, bool]] = [
        (False, False),
        (False, include_dim_default),
        (include_task_default, False),
        (include_task_default, include_dim_default),
    ]

    tried: set[Tuple[bool, bool]] = set()
    errors: List[str] = []
    for include_task, include_dim in variant_order:
        key = (include_task, include_dim)
        if key in tried:
            continue
        tried.add(key)

        payload: Dict[str, Any] = {
            "model": model,
            "input": base_input,
            "encoding_format": "float",
        }
        if include_task and task:
            payload["task"] = task
        if include_dim and dimensions is not None:
            payload["dimensions"] = int(dimensions)

        resp = requests.post(embed_url, json=payload, timeout=timeout_sec)
        if resp.status_code >= 300:
            errors.append(f"{resp.status_code}: {resp.text[:220]}")
            continue

        obj = resp.json()
        data = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Embedding response missing data array")

        ordered = sorted(data, key=lambda x: int(x.get("index", 0)))
        vectors: List[List[float]] = []
        for item in ordered:
            emb = item.get("embedding")
            if not isinstance(emb, list):
                raise RuntimeError("Embedding item missing embedding vector")
            vectors.append([float(v) for v in emb])

        if include_task_default and not include_task and not _EMBED_COMPAT_OMIT_TASK:
            LOGGER.warning("Embedding endpoint compatibility mode enabled: omitting `task` in requests")
            _EMBED_COMPAT_OMIT_TASK = True
        if include_dim_default and not include_dim and not _EMBED_COMPAT_OMIT_DIMENSIONS:
            LOGGER.warning("Embedding endpoint compatibility mode enabled: omitting `dimensions` in requests")
            _EMBED_COMPAT_OMIT_DIMENSIONS = True
        return vectors

    raise RuntimeError(
        "Embedding request failed after compatibility retries: "
        + "; ".join(errors[-4:] if len(errors) > 4 else errors)
    )


def embed_batch_resilient(
    embed_url: str,
    model: str,
    texts: Sequence[str],
    *,
    task: str,
    timeout_sec: int,
    dimensions: Optional[int],
    max_attempts: int,
    retry_backoff_sec: float,
) -> List[List[float]]:
    if not texts:
        return []

    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            return embed_batch(
                embed_url,
                model,
                texts,
                task=task,
                timeout_sec=timeout_sec,
                dimensions=dimensions,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= int(max_attempts):
                break
            sleep_s = float(retry_backoff_sec) * attempt
            LOGGER.warning(
                "Embedding batch failed (size=%d) attempt=%d/%d: %s; retrying in %.1fs",
                len(texts),
                attempt,
                int(max_attempts),
                exc,
                sleep_s,
            )
            time.sleep(max(0.0, sleep_s))

    # If a multi-input batch is unstable, split recursively to isolate bad items
    # and survive transient model restarts.
    if len(texts) > 1:
        mid = len(texts) // 2
        left = embed_batch_resilient(
            embed_url,
            model,
            texts[:mid],
            task=task,
            timeout_sec=timeout_sec,
            dimensions=dimensions,
            max_attempts=max_attempts,
            retry_backoff_sec=retry_backoff_sec,
        )
        right = embed_batch_resilient(
            embed_url,
            model,
            texts[mid:],
            task=task,
            timeout_sec=timeout_sec,
            dimensions=dimensions,
            max_attempts=max_attempts,
            retry_backoff_sec=retry_backoff_sec,
        )
        return left + right

    err_msg = str(last_error) if last_error is not None else "unknown error"
    raise RuntimeError(f"Embedding failed for single text after retries: {err_msg}") from last_error


def chunks_to_es_actions(index: str, chunks: Sequence[ChunkDoc]) -> Iterable[Dict[str, Any]]:
    for c in chunks:
        body = {
            **c.metadata,
            "text": c.text,
        }
        yield {
            "_op_type": "index",
            "_index": index,
            "_id": c.chunk_id,
            "_source": body,
        }


def docs_to_es_actions(index: str, docs: Sequence[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for d in docs:
        yield {
            "_op_type": "index",
            "_index": index,
            "_id": d["doc_id"],
            "_source": d,
        }


def artifact_id(prefix: str, raw: str) -> str:
    return f"{prefix}:{sha256_hex(raw)}"


def build_graphrag_artifacts(doc_records: Sequence[Dict[str, Any]], chunks: Sequence[ChunkDoc]) -> List[GraphArtifact]:
    artifacts: List[GraphArtifact] = []

    # TextUnit artifacts are chunk-level retrieval units.
    for c in chunks:
        meta = {
            "artifact_id": artifact_id("text_unit", c.chunk_id),
            "artifact_type": "text_unit",
            "retrieval_mode": "basic",
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "path": c.metadata.get("path", ""),
            "source_root": c.metadata.get("source_root", ""),
            "semantic_domain": c.metadata.get("semantic_domain", ""),
            "semantic_decision": c.metadata.get("semantic_decision", ""),
            "first_bucket": c.metadata.get("first_bucket", ""),
            "sub_bucket": c.metadata.get("sub_bucket", ""),
        }
        artifacts.append(
            GraphArtifact(
                artifact_id=str(meta["artifact_id"]),
                artifact_type="text_unit",
                text=c.text,
                metadata=meta,
                vector=None,
            )
        )

    # Entity description artifacts aggregate entity mentions across documents.
    entity_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
    entity_fields = [
        ("system", "systems"),
        ("vendor", "vendors"),
        ("project", "projects"),
        ("person", "people"),
        ("location", "locations"),
    ]
    for rec in doc_records:
        entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
        for label, key in entity_fields:
            for name in norm_list(entities.get(key)):
                k = (label, name)
                cur = entity_stats.setdefault(
                    k,
                    {
                        "count": 0,
                        "domains": set(),
                        "paths": set(),
                    },
                )
                cur["count"] += 1
                cur["domains"].add(str(rec.get("semantic_domain", "")))
                cur["paths"].add(str(rec.get("path", "")))

    for (label, name), stats in entity_stats.items():
        domains = sorted(d for d in stats["domains"] if d)
        sample_paths = sorted(p for p in stats["paths"] if p)[:5]
        mentions = int(stats["count"])
        text = (
            f"Entity description: {label} '{name}'. "
            f"Mentions={mentions}. Domains={', '.join(domains) if domains else 'unknown'}. "
            f"Sample paths={'; '.join(sample_paths) if sample_paths else 'none'}."
        )
        aid = artifact_id("entity", f"{label}|{name}")
        artifacts.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="entity_description",
                text=text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "entity_description",
                    "retrieval_mode": "local",
                    "entity_label": label,
                    "entity_name": name,
                    "entity_mentions": mentions,
                    "semantic_domain": domains[0] if domains else "",
                    "domains": domains,
                    "sample_paths": sample_paths,
                },
                vector=None,
            )
        )

    # Relationship artifacts capture doc->entity references for graph-style expansion.
    for rec in doc_records:
        entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
        doc_id = str(rec.get("doc_id", ""))
        path = str(rec.get("path", ""))
        domain = str(rec.get("semantic_domain", ""))
        first_bucket = str(rec.get("first_bucket", ""))
        sub_bucket = str(rec.get("sub_bucket", ""))
        for label, key in entity_fields:
            for name in norm_list(entities.get(key)):
                rel_key = f"{doc_id}|{label}|{name}"
                aid = artifact_id("relationship", rel_key)
                text = (
                    f"Relationship: document '{path}' (domain={domain}) references "
                    f"{label} '{name}'."
                )
                artifacts.append(
                    GraphArtifact(
                        artifact_id=aid,
                        artifact_type="relationship",
                        text=text,
                        metadata={
                            "artifact_id": aid,
                            "artifact_type": "relationship",
                            "retrieval_mode": "local",
                            "relation_type": "DOCUMENT_REFERENCES_ENTITY",
                            "src_type": "document",
                            "src_id": doc_id,
                            "dst_type": label,
                            "dst_id": f"{label}:{name}",
                            "entity_label": label,
                            "entity_name": name,
                            "path": path,
                            "semantic_domain": domain,
                            "first_bucket": first_bucket,
                            "sub_bucket": sub_bucket,
                        },
                        vector=None,
                    )
                )

    # Community report artifacts summarize domain-level communities.
    by_domain: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in doc_records:
        by_domain[str(rec.get("semantic_domain", "general") or "general")].append(rec)

    for domain, rows in by_domain.items():
        doc_count = len(rows)
        chunk_count = sum(int(r.get("chunk_count", 0) or 0) for r in rows)
        bucket_counts: Dict[str, int] = defaultdict(int)
        tag_counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            bucket_counts[str(r.get("first_bucket", ""))] += 1
            for tag in norm_list(r.get("tags")):
                tag_counts[tag] += 1
        top_buckets = [k for k, _ in sorted(bucket_counts.items(), key=lambda x: x[1], reverse=True)[:5] if k]
        top_tags = [k for k, _ in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:8] if k]
        aid = artifact_id("community", domain)
        text = (
            f"Community report for domain '{domain}'. "
            f"Documents={doc_count}, Chunks={chunk_count}. "
            f"Top first buckets={', '.join(top_buckets) if top_buckets else 'n/a'}. "
            f"Top tags={', '.join(top_tags) if top_tags else 'n/a'}."
        )
        artifacts.append(
            GraphArtifact(
                artifact_id=aid,
                artifact_type="community_report",
                text=text,
                metadata={
                    "artifact_id": aid,
                    "artifact_type": "community_report",
                    "retrieval_mode": "global",
                    "community_id": f"domain:{domain}",
                    "semantic_domain": domain,
                    "doc_count": doc_count,
                    "chunk_count": chunk_count,
                    "top_buckets": top_buckets,
                    "top_tags": top_tags,
                },
                vector=None,
            )
        )

    return artifacts


def artifacts_to_es_actions(index: str, artifacts: Sequence[GraphArtifact]) -> Iterable[Dict[str, Any]]:
    for a in artifacts:
        body = {**a.metadata, "text": a.text}
        yield {
            "_op_type": "index",
            "_index": index,
            "_id": a.artifact_id,
            "_source": body,
        }


def upsert_neo4j(
    neo_driver: Any,
    doc_records: Sequence[Dict[str, Any]],
    chunks: Sequence[ChunkDoc],
) -> None:
    def neo4j_doc_props(rec: Dict[str, Any]) -> Dict[str, Any]:
        props = {k: v for k, v in rec.items() if k != "entities"}
        entities = rec.get("entities", {}) if isinstance(rec.get("entities"), dict) else {}
        props["entities_systems"] = norm_list(entities.get("systems"))
        props["entities_vendors"] = norm_list(entities.get("vendors"))
        props["entities_projects"] = norm_list(entities.get("projects"))
        props["entities_people"] = norm_list(entities.get("people"))
        props["entities_locations"] = norm_list(entities.get("locations"))
        return props

    def write_doc(tx: Any, rec: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (d:Document {doc_id: $doc_id})
            SET d += $props
            MERGE (dom:Domain {id: $semantic_domain})
            MERGE (d)-[:IN_DOMAIN]->(dom)
            MERGE (fb:Bucket {id: $first_bucket})
            SET fb.kind = 'first_bucket'
            MERGE (sb:Bucket {id: $sub_bucket})
            SET sb.kind = 'sub_bucket'
            MERGE (d)-[:IN_BUCKET]->(fb)
            MERGE (d)-[:IN_SUB_BUCKET]->(sb)
            """,
            doc_id=rec["doc_id"],
            semantic_domain=rec["semantic_domain"],
            first_bucket=rec["first_bucket"],
            sub_bucket=rec["sub_bucket"],
            props=neo4j_doc_props(rec),
        )

        entity_lists = {
            "System": rec.get("entities", {}).get("systems", []),
            "Vendor": rec.get("entities", {}).get("vendors", []),
            "Project": rec.get("entities", {}).get("projects", []),
            "Person": rec.get("entities", {}).get("people", []),
            "Location": rec.get("entities", {}).get("locations", []),
        }
        for label, items in entity_lists.items():
            for name in items:
                if not name:
                    continue
                tx.run(
                    f"""
                    MERGE (e:{label} {{name: $name}})
                    MERGE (d:Document {{doc_id: $doc_id}})
                    MERGE (d)-[:HAS_ENTITY]->(e)
                    """,
                    name=name,
                    doc_id=rec["doc_id"],
                )

    def write_chunk(tx: Any, chunk: ChunkDoc) -> None:
        tx.run(
            """
            MERGE (c:Chunk {chunk_id: $chunk_id})
            SET c += $props
            MERGE (d:Document {doc_id: $doc_id})
            MERGE (c)-[:PART_OF]->(d)
            """,
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            props={**chunk.metadata, "text": chunk.text},
        )

    with neo_driver.session() as session:
        for rec in doc_records:
            session.execute_write(write_doc, rec)
        for c in chunks:
            session.execute_write(write_chunk, c)


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "documents": {}}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"version": 1, "documents": {}}
    if not isinstance(obj, dict):
        return {"version": 1, "documents": {}}
    if not isinstance(obj.get("documents"), dict):
        obj["documents"] = {}
    return obj


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Ingest RouterKit artifacts into ES/Qdrant/Neo4j")
    ap.add_argument("--routerkit-documents", required=True, help="Path to documents.jsonl")
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--es-doc-index", default="kb_docs")
    ap.add_argument("--es-chunk-index", default="kb_chunks")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--qdrant-collection", default="kb_chunks_vec")
    ap.add_argument("--qdrant-timeout-sec", type=int, default=120)
    ap.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default="change_me_local")
    ap.add_argument("--embed-url", default="http://127.0.0.1:8001/v1/embeddings")
    ap.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--embed-timeout-sec", type=int, default=600)
    ap.add_argument("--embed-task", default="document", choices=["document", "query"])
    ap.add_argument("--vector-dim", type=int, default=4096, help="Request embedding dimensions")
    ap.add_argument("--embed-batch-size", type=int, default=8)
    ap.add_argument("--artifact-embed-batch-size", type=int, default=16)
    ap.add_argument("--embed-max-attempts", type=int, default=3)
    ap.add_argument("--embed-retry-backoff-sec", type=float, default=3.0)
    ap.add_argument(
        "--chunk-mode",
        default="auto",
        choices=["auto", "chars", "tokens", "tokens_structured", "log_entries", "csv_mail"],
    )
    ap.add_argument("--chunk-chars", type=int, default=1600)
    ap.add_argument("--chunk-overlap", type=int, default=200)
    ap.add_argument("--chunk-tokens", type=int, default=1200)
    ap.add_argument("--chunk-overlap-tokens", type=int, default=100)
    ap.add_argument("--tokenizer-model", default=DEFAULT_TOKENIZER_MODEL)
    ap.add_argument("--content-source", choices=["routerkit_sample", "filesystem", "prefer_filesystem"], default="routerkit_sample")
    ap.add_argument("--corpus-root", default=".", help="Root used to resolve document paths for filesystem extraction")
    ap.add_argument(
        "--filesystem-on-miss",
        choices=["fallback", "skip", "fail"],
        default="fallback",
        help=(
            "Behavior when filesystem extraction yields no text: "
            "fallback=keep routerkit sample, skip=skip doc, fail=abort run"
        ),
    )
    ap.add_argument("--content-sample-mode", choices=["none", "redacted", "full"], default="full")
    ap.add_argument("--content-max-chars", type=int, default=20000, help="Max chars extracted per doc when reading from filesystem")
    ap.add_argument("--content-pdf-max-pages", type=int, default=20)
    ap.add_argument("--content-pptx-max-slides", type=int, default=40)
    ap.add_argument("--content-xlsx-max-strings", type=int, default=10000)
    ap.add_argument("--content-xlsx-max-chars", type=int, default=200000)
    ap.add_argument("--content-zip-max-members", type=int, default=200)
    ap.add_argument("--content-binary-max-bytes", type=int, default=4 * 1024 * 1024)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-es", action="store_true")
    ap.add_argument("--skip-qdrant", action="store_true")
    ap.add_argument("--skip-neo4j", action="store_true")
    ap.add_argument("--enable-graphrag-artifacts", action="store_true")
    ap.add_argument("--graphrag-only", action="store_true", help="Only write GraphRAG retrieval artifacts")
    ap.add_argument("--es-graphrag-index", default="kb_graphrag_artifacts")
    ap.add_argument("--qdrant-graphrag-collection", default="kb_graphrag_artifacts_vec")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--incremental", action="store_true", help="Skip unchanged docs using state file")
    ap.add_argument("--state-file", default="localdata/ingest_state.json")
    ap.add_argument("--write-chunks-jsonl", default="", help="Optional path to write emitted chunks as JSONL")
    ap.add_argument("--weak-docs-jsonl", default="", help="Optional path to write low-quality extraction doc records")
    ap.add_argument("--weak-quality-threshold", type=int, default=55, help="Write docs with extraction_quality_score <= threshold")
    ap.add_argument(
        "--preflight-max-empty-pdf-ratio",
        type=float,
        default=0.40,
        help="Fail ingest when empty-pdf ratio exceeds this value (-1 disables)",
    )
    ap.add_argument(
        "--preflight-empty-pdf-min-docs",
        type=int,
        default=10,
        help="Minimum number of PDFs before enforcing empty-pdf preflight ratio gate",
    )
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    docs_path = Path(args.routerkit_documents).expanduser().resolve()
    if not docs_path.exists():
        raise FileNotFoundError(f"RouterKit documents not found: {docs_path}")

    docs = read_routerkit_documents(docs_path, limit=int(args.limit))
    LOGGER.info("Loaded %d docs from %s", len(docs), docs_path)

    content_source_mode = str(args.content_source or "routerkit_sample").strip().lower()
    filesystem_on_miss = str(args.filesystem_on_miss or "fallback").strip().lower()
    if content_source_mode == "filesystem" and filesystem_on_miss == "fallback":
        # Pure filesystem mode should not silently degrade to routerkit samples.
        filesystem_on_miss = "skip"
    corpus_root = Path(args.corpus_root).expanduser().resolve()

    state_file = Path(args.state_file).expanduser().resolve()
    state = load_state(state_file)
    state_docs: Dict[str, str] = state.get("documents", {}) if isinstance(state.get("documents"), dict) else {}

    filtered_docs: List[IngestDoc] = []
    prefilter_skipped = 0
    for d in docs:
        if (
            args.incremental
            and content_source_mode == "routerkit_sample"
            and state_docs.get(d.path) == d.content_hash
        ):
            prefilter_skipped += 1
            continue
        filtered_docs.append(d)

    LOGGER.info(
        "Docs selected for ingest pre-filter: %d (skipped=%d)",
        len(filtered_docs),
        prefilter_skipped,
    )
    if not filtered_docs:
        return 0

    es_client = None if args.skip_es else Elasticsearch(args.es_url)
    qdr_client = (
        None
        if args.skip_qdrant
        else QdrantClient(url=args.qdrant_url, timeout=float(args.qdrant_timeout_sec))
    )
    neo_driver = None
    if not args.skip_neo4j:
        neo_driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))

    chunk_mode = str(args.chunk_mode or "auto").strip().lower()
    tokenizer = None
    if chunk_mode in {"auto", "tokens", "tokens_structured", "log_entries", "csv_mail"}:
        tokenizer_model = str(args.tokenizer_model or args.embed_model or DEFAULT_TOKENIZER_MODEL)
        try:
            tokenizer = get_tokenizer(tokenizer_model)
            LOGGER.info("Using tokenizer model for chunking: %s (requested_mode=%s)", tokenizer_model, chunk_mode)
        except Exception as exc:
            LOGGER.warning("Tokenizer unavailable (%s). Falling back to char chunking mode.", exc)
            chunk_mode = "chars"

    doc_records: List[Dict[str, Any]] = []
    all_chunks: List[ChunkDoc] = []
    fs_used = 0
    fs_fallback = 0
    fs_empty_or_missing = 0
    runtime_incremental_skipped = 0
    processed_state_updates: Dict[str, str] = {}
    if content_source_mode in {"filesystem", "prefer_filesystem"}:
        LOGGER.info(
            "Filesystem content mode enabled (mode=%s, on_miss=%s, root=%s, sample_mode=%s, max_chars=%d)",
            content_source_mode,
            filesystem_on_miss,
            corpus_root,
            args.content_sample_mode,
            int(args.content_max_chars),
        )
        if args.incremental:
            LOGGER.warning(
                "Incremental skip decisions are based on documents.jsonl content hashes, "
                "not live filesystem extraction output."
            )

    for i, doc in enumerate(filtered_docs, start=1):
        if content_source_mode in {"filesystem", "prefer_filesystem"}:
            fs_text, fs_method = resolve_filesystem_doc_text(
                doc,
                corpus_root=corpus_root,
                sample_mode=args.content_sample_mode,
                max_chars=int(args.content_max_chars),
                pdf_max_pages=int(args.content_pdf_max_pages),
                pptx_max_slides=int(args.content_pptx_max_slides),
                xlsx_max_strings=int(args.content_xlsx_max_strings),
                xlsx_max_chars=int(args.content_xlsx_max_chars),
                zip_max_members=int(args.content_zip_max_members),
                binary_max_bytes=int(args.content_binary_max_bytes),
            )
            if fs_text:
                doc.sample_text = fs_text
                doc.content_source = fs_method
                doc.content_hash = sha256_hex(fs_text)
                doc.doc_id = deterministic_doc_id(doc.path, doc.size_bytes, doc.mtime_date, doc.content_hash)
                fs_used += 1
            else:
                if filesystem_on_miss == "fail":
                    raise RuntimeError(
                        f"Filesystem extraction failed for {doc.path} ({fs_method}) with --filesystem-on-miss=fail"
                    )
                if filesystem_on_miss == "skip":
                    fs_empty_or_missing += 1
                    continue
                doc.content_source = f"routerkit_fallback:{fs_method}"
                fs_fallback += 1
            update_doc_extraction_quality(doc)
        if args.incremental and content_source_mode in {"filesystem", "prefer_filesystem"}:
            if state_docs.get(doc.path) == doc.content_hash:
                runtime_incremental_skipped += 1
                continue

        chunks = build_chunks(
            doc,
            chunk_mode=chunk_mode,
            chunk_chars=int(args.chunk_chars),
            overlap_chars=int(args.chunk_overlap),
            chunk_tokens=int(args.chunk_tokens),
            overlap_tokens=int(args.chunk_overlap_tokens),
            tokenizer=tokenizer,
        )
        doc_records.append(to_doc_record(doc, chunk_count=len(chunks)))
        all_chunks.extend(chunks)
        processed_state_updates[doc.path] = doc.content_hash
        if i % 250 == 0:
            LOGGER.info("Prepared %d/%d docs", i, len(filtered_docs))

    if content_source_mode in {"filesystem", "prefer_filesystem"}:
        LOGGER.info(
            "Filesystem content resolution summary: used=%d fallback_to_routerkit_sample=%d no_content=%d",
            fs_used,
            fs_fallback,
            fs_empty_or_missing,
        )
    if runtime_incremental_skipped:
        LOGGER.info("Incremental runtime skips after filesystem resolution: %d", runtime_incremental_skipped)

    weak_threshold = int(args.weak_quality_threshold)
    weak_docs = [
        rec
        for rec in doc_records
        if int(rec.get("extraction_quality_score", 0) or 0) <= weak_threshold
    ]
    avg_quality = (
        sum(int(rec.get("extraction_quality_score", 0) or 0) for rec in doc_records) / float(len(doc_records))
        if doc_records
        else 0.0
    )
    LOGGER.info(
        "Extraction quality summary: avg=%.2f weak_docs=%d threshold<=%d",
        avg_quality,
        len(weak_docs),
        weak_threshold,
    )

    max_empty_pdf_ratio = float(args.preflight_max_empty_pdf_ratio)
    min_pdf_docs = max(0, int(args.preflight_empty_pdf_min_docs))
    if max_empty_pdf_ratio >= 0:
        pdf_rows = [rec for rec in doc_records if str(rec.get("ext", "")).strip().lower() == "pdf"]
        pdf_total = len(pdf_rows)
        def _pdf_effectively_empty(rec: Dict[str, Any]) -> bool:
            char_count = int(rec.get("extraction_char_count", 0) or 0)
            src = str(rec.get("content_source", "") or "").strip().lower()
            if char_count <= 0:
                return True
            # In filesystem/prefer_filesystem modes, fallback payloads indicate
            # extractor failure even if routerkit sample text is present.
            if src.startswith("routerkit_fallback:"):
                return True
            if "empty_extract" in src or "extract_failed" in src:
                return True
            return False

        pdf_empty = sum(1 for rec in pdf_rows if _pdf_effectively_empty(rec))
        pdf_ratio = (pdf_empty / float(pdf_total)) if pdf_total > 0 else 0.0
        LOGGER.info(
            "PDF extraction preflight: empty=%d/%d ratio=%.3f threshold<=%.3f",
            pdf_empty,
            pdf_total,
            pdf_ratio,
            max_empty_pdf_ratio,
        )
        if pdf_total >= min_pdf_docs and pdf_ratio > max_empty_pdf_ratio:
            raise RuntimeError(
                "Preflight failed: empty PDF extraction ratio "
                f"{pdf_empty}/{pdf_total} ({pdf_ratio:.3f}) exceeds threshold {max_empty_pdf_ratio:.3f}. "
                "Install/enable PDF extractors or override threshold explicitly."
            )

    if args.weak_docs_jsonl:
        weak_path = Path(args.weak_docs_jsonl).expanduser().resolve()
        weak_path.parent.mkdir(parents=True, exist_ok=True)
        with weak_path.open("w", encoding="utf-8") as f:
            for rec in weak_docs:
                row = {
                    "doc_id": rec.get("doc_id"),
                    "path": rec.get("path"),
                    "semantic_domain": rec.get("semantic_domain", ""),
                    "first_bucket": rec.get("first_bucket", ""),
                    "sub_bucket": rec.get("sub_bucket", ""),
                    "extraction_quality_score": int(rec.get("extraction_quality_score", 0) or 0),
                    "extraction_quality_flags": list(rec.get("extraction_quality_flags") or []),
                    "extraction_char_count": int(rec.get("extraction_char_count", 0) or 0),
                    "extraction_token_count": int(rec.get("extraction_token_count", 0) or 0),
                    "chunk_count": int(rec.get("chunk_count", 0) or 0),
                    "content_source": rec.get("content_source", ""),
                }
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
        LOGGER.info("Wrote weak extraction doc list: %s (%d docs)", weak_path, len(weak_docs))

    LOGGER.info(
        "Prepared %d documents and %d chunks (chunk_mode=%s)",
        len(doc_records),
        len(all_chunks),
        chunk_mode,
    )

    graphrag_artifacts: List[GraphArtifact] = []
    if args.enable_graphrag_artifacts:
        graphrag_artifacts = build_graphrag_artifacts(doc_records, all_chunks)
        type_counts: Dict[str, int] = defaultdict(int)
        for a in graphrag_artifacts:
            type_counts[str(a.artifact_type)] += 1
        LOGGER.info(
            "Prepared %d GraphRAG artifacts: %s",
            len(graphrag_artifacts),
            ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())),
        )

    if args.write_chunks_jsonl:
        out_path = Path(args.write_chunks_jsonl).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for c in all_chunks:
                row = {**c.metadata, "text": c.text}
                f.write(json.dumps(row) + "\n")
        LOGGER.info("Wrote chunk snapshot: %s", out_path)

    if not args.dry_run and not args.skip_qdrant and not args.graphrag_only:
        LOGGER.info("Requesting embeddings for %d chunks", len(all_chunks))
        for i in range(0, len(all_chunks), int(args.embed_batch_size)):
            batch = all_chunks[i : i + int(args.embed_batch_size)]
            vecs = embed_batch_resilient(
                args.embed_url,
                args.embed_model,
                [c.text for c in batch],
                task=args.embed_task,
                timeout_sec=int(args.embed_timeout_sec),
                dimensions=int(args.vector_dim) if int(args.vector_dim) > 0 else None,
                max_attempts=int(args.embed_max_attempts),
                retry_backoff_sec=float(args.embed_retry_backoff_sec),
            )
            if len(vecs) != len(batch):
                raise RuntimeError(f"Embedding count mismatch: got {len(vecs)} expected {len(batch)}")
            for c, v in zip(batch, vecs):
                c.vector = v
            if (i // int(args.embed_batch_size)) % 50 == 0:
                LOGGER.info("Embedded %d/%d chunks", min(i + len(batch), len(all_chunks)), len(all_chunks))

    if not args.dry_run and not args.skip_qdrant and graphrag_artifacts:
        LOGGER.info("Requesting embeddings for %d GraphRAG artifacts", len(graphrag_artifacts))
        for i in range(0, len(graphrag_artifacts), int(args.artifact_embed_batch_size)):
            batch = graphrag_artifacts[i : i + int(args.artifact_embed_batch_size)]
            vecs = embed_batch_resilient(
                args.embed_url,
                args.embed_model,
                [a.text for a in batch],
                task="document",
                timeout_sec=int(args.embed_timeout_sec),
                dimensions=int(args.vector_dim) if int(args.vector_dim) > 0 else None,
                max_attempts=int(args.embed_max_attempts),
                retry_backoff_sec=float(args.embed_retry_backoff_sec),
            )
            if len(vecs) != len(batch):
                raise RuntimeError(f"GraphRAG embedding count mismatch: got {len(vecs)} expected {len(batch)}")
            for a, v in zip(batch, vecs):
                a.vector = v
            if (i // int(args.artifact_embed_batch_size)) % 50 == 0:
                LOGGER.info(
                    "Embedded %d/%d GraphRAG artifacts",
                    min(i + len(batch), len(graphrag_artifacts)),
                    len(graphrag_artifacts),
                )

    if not args.dry_run and es_client is not None and not args.graphrag_only:
        es_write = es_client.options(request_timeout=120)
        LOGGER.info("Indexing docs -> Elasticsearch index=%s", args.es_doc_index)
        es_bulk(es_write, docs_to_es_actions(args.es_doc_index, doc_records), refresh=False)
        LOGGER.info("Indexing chunks -> Elasticsearch index=%s", args.es_chunk_index)
        es_bulk(es_write, chunks_to_es_actions(args.es_chunk_index, all_chunks), refresh=False)

    if not args.dry_run and es_client is not None and graphrag_artifacts:
        es_write = es_client.options(request_timeout=120)
        LOGGER.info("Indexing GraphRAG artifacts -> Elasticsearch index=%s", args.es_graphrag_index)
        es_bulk(es_write, artifacts_to_es_actions(args.es_graphrag_index, graphrag_artifacts), refresh=False)

    if not args.dry_run and qdr_client is not None and not args.graphrag_only:
        LOGGER.info("Upserting vectors -> Qdrant collection=%s", args.qdrant_collection)
        batch_size = 128
        for i in range(0, len(all_chunks), batch_size):
            batch = all_chunks[i : i + batch_size]
            points: List[PointStruct] = []
            for c in batch:
                if c.vector is None:
                    raise RuntimeError(f"Missing vector for chunk {c.chunk_id}")
                payload = {**c.metadata, "text": c.text}
                points.append(PointStruct(id=qdrant_point_id(c.chunk_id), vector=c.vector, payload=payload))
            qdrant_call_with_retry(
                "upsert",
                lambda: qdr_client.upsert(
                    collection_name=args.qdrant_collection,
                    wait=False,
                    points=points,
                ),
            )

    if not args.dry_run and qdr_client is not None and graphrag_artifacts:
        LOGGER.info("Upserting GraphRAG vectors -> Qdrant collection=%s", args.qdrant_graphrag_collection)
        batch_size = 128
        for i in range(0, len(graphrag_artifacts), batch_size):
            batch = graphrag_artifacts[i : i + batch_size]
            points = []
            for a in batch:
                if a.vector is None:
                    raise RuntimeError(f"Missing vector for GraphRAG artifact {a.artifact_id}")
                payload = {**a.metadata, "text": a.text}
                points.append(PointStruct(id=qdrant_point_id(a.artifact_id), vector=a.vector, payload=payload))
            qdrant_call_with_retry(
                "upsert_graphrag",
                lambda: qdr_client.upsert(
                    collection_name=args.qdrant_graphrag_collection,
                    wait=False,
                    points=points,
                ),
            )

    if not args.dry_run and neo_driver is not None and not args.graphrag_only:
        LOGGER.info("Upserting graph -> Neo4j")
        upsert_neo4j(neo_driver, doc_records, all_chunks)

    for pth, ch in processed_state_updates.items():
        state_docs[pth] = ch
    state["documents"] = state_docs
    if args.incremental:
        save_state(state_file, state)
        LOGGER.info("Updated state file: %s", state_file)

    if neo_driver is not None:
        neo_driver.close()

    LOGGER.info("Ingest complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
