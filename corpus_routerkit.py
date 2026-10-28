#!/usr/bin/env python3
"""
Corpus RouterKit
================

Deterministically builds:
  - archive extraction (optional)
  - inventory (counts, types, time range)
  - per-document profiles + text samples
  - 2-layer routing buckets (structure-driven)
  - facets/tags/sensitivity signals
  - JSON Schemas for agent-friendly parsing/validation

No network access. Optional dependencies (used if installed):
  - pypdf (or PyPDF2), pdfminer.six (PDF)
  - beautifulsoup4 (HTML)
  - openpyxl (XLSX)
  - python-docx (DOCX)
  - python-pptx (PPTX)
  - striprtf (RTF)
  - python-oxmsg and/or extract-msg (MSG)

Default output folder: ./kb_artifacts
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import time
import urllib.error
import urllib.request
import ssl
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import xml.etree.ElementTree as ET

try:
    from pypdf import PdfReader as _PdfReader  # type: ignore
except Exception:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader as _PdfReader  # type: ignore
    except Exception:  # pragma: no cover
        _PdfReader = None  # type: ignore

try:
    from pdfminer.high_level import extract_text as _pdfminer_extract_text  # type: ignore
except Exception:  # pragma: no cover
    _pdfminer_extract_text = None  # type: ignore

try:
    from bs4 import BeautifulSoup as _BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    _BeautifulSoup = None  # type: ignore

try:
    from openpyxl import load_workbook as _load_workbook  # type: ignore
except Exception:  # pragma: no cover
    _load_workbook = None  # type: ignore

try:
    from pptx import Presentation as _PptxPresentation  # type: ignore
except Exception:  # pragma: no cover
    _PptxPresentation = None  # type: ignore

try:
    from docx import Document as _DocxDocument  # type: ignore
except Exception:  # pragma: no cover
    _DocxDocument = None  # type: ignore

try:
    from striprtf.striprtf import rtf_to_text as _rtf_to_text  # type: ignore
except Exception:  # pragma: no cover
    _rtf_to_text = None  # type: ignore

try:
    from oxmsg import Message as _OxMsgMessage  # type: ignore
except Exception:  # pragma: no cover
    _OxMsgMessage = None  # type: ignore

try:
    import extract_msg as _extract_msg  # type: ignore
except Exception:  # pragma: no cover
    _extract_msg = None  # type: ignore


TOOL_NAME = "corpus_routerkit"
TOOL_VERSION = "0.5"
SCHEMA_VERSION = 5


DEFAULT_OUT_DIR = "kb_artifacts"

DEFAULT_CONTAINER_DIRS = {"unarchived", "archives", "archive", "extracted"}

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
    "out",
    ".DS_Store",
    ".Spotlight-V100",
    ".fseventsd",
    "System Volume Information",
}

EXCLUDE_PROFILE_ENUM = ["none", "business_default"]
DEFAULT_EXCLUDE_PROFILE = "business_default"
CALIBRATOR_PROVIDER_ENUM = ["openrouter"]
DEFAULT_CALIBRATOR_PROVIDER = "openrouter"
DEFAULT_CALIBRATOR_MODEL = "moonshotai/kimi-k2.5"
DEFAULT_CALIBRATOR_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_CALIBRATOR_API_KEY_ENV = "OPENROUTER_API_KEY"
CALIBRATOR_DOC_SELECTION_ENUM = ["low_confidence", "all"]
DEFAULT_CALIBRATOR_DOC_SELECTION = "low_confidence"

BUSINESS_NON_CORPUS_EXCLUDE_DIRS = {
    "inventory",
    "localdata",
    "reports",
    "evals",
    "containers",
    "schemas",
    "scripts",
    "services",
    "prompts",
}
BUSINESS_NON_CORPUS_SOURCE_EXTS = {
    "py",
    "pyw",
    "ipynb",
    "js",
    "jsx",
    "ts",
    "tsx",
    "c",
    "cc",
    "cpp",
    "cxx",
    "h",
    "hh",
    "hpp",
    "hxx",
    "java",
    "kt",
    "kts",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "swift",
    "scala",
    "sh",
    "bash",
    "zsh",
    "fish",
    "ps1",
    "psm1",
    "bat",
    "cmd",
    "pl",
    "lua",
}
BUSINESS_NON_CORPUS_EXCLUDE_BASENAMES = {
    "corpus_routerkit.py",
    "eval_routerkit.py",
    "eval_queries.template.jsonl",
    "routerkit_requirements_optional.txt",
}
BUSINESS_NON_CORPUS_EXCLUDE_PATH_REGEX = [
    {"pattern": r"(^|/)kb_artifacts[^/]*(/|$)", "flags": "i"},
    {"pattern": r"(^|/)(localdata|reports|evals|containers|schemas|scripts|services|prompts)(/|$)", "flags": "i"},
]

ARTIFACT_BASENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _short_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:8]


_MANIFEST_CACHE: Dict[str, bool] = {}


def is_routerkit_output_dir(p: Path) -> bool:
    """
    Detect previous RouterKit outputs to avoid self-ingesting artifacts on subsequent runs.
    """
    try:
        pp = str(p.resolve())
    except Exception:
        pp = str(p)
    cached = _MANIFEST_CACHE.get(pp)
    if cached is not None:
        return cached

    manifest = p / "manifest.json"
    if not manifest.exists() or not manifest.is_file():
        _MANIFEST_CACHE[pp] = False
        return False

    try:
        obj = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        ok = (
            isinstance(obj, dict)
            and isinstance(obj.get("tool"), dict)
            and obj["tool"].get("name") == TOOL_NAME
            and "schema_version" in obj
        )
    except Exception:
        ok = False

    _MANIFEST_CACHE[pp] = bool(ok)
    return bool(ok)


DOC_TYPE_ENUM = [
    "word",
    "powerpoint",
    "spreadsheet",
    "pdf",
    "html",
    "text",
    "email",
    "image",
    "visio",
    "archive",
    "other",
]

ARTIFACT_TYPE_ENUM = [
    "meeting_notes",
    "logbook",
    "procedure",
    "policy",
    "plan",
    "strategy",
    "architecture",
    "deck",
    "dataset",
    "diagram",
    "photo",
    "email",
    "web_page",
    "contract",
    "invoice",
    "report",
    "other",
]

SENSITIVITY_LEVEL_ENUM = ["normal", "medium", "high", "critical"]
SENSITIVITY_FLAGS_ENUM = ["security_sensitive", "email", "pii_possible", "credentials_possible"]

SAMPLE_MODE_ENUM = ["none", "redacted", "full"]
KEYWORDS_SOURCE_ENUM = ["path_only", "meta_only", "meta+sample"]
SEMANTIC_DECISION_ENUM = ["routed", "abstained"]
DUPLICATE_KIND_ENUM = ["unique", "exact", "near"]

DEFAULT_DOMAIN_SCORE_THRESHOLD = 3
DEFAULT_DOMAIN_MARGIN_THRESHOLD = 2
DEFAULT_ITOPS_SECURITY_TIE_MAX_GAP = 2
DEFAULT_BENCHMARK_SIZE = 300
DEFAULT_BENCHMARK_MIN_SCORE = 4
DEFAULT_BENCHMARK_MIN_MARGIN = 2

ITOPS_TIEBREAKER_SECURITY_HINT_KEYWORDS = {
    "cyber",
    "security",
    "fisma",
    "nist",
    "hspd12",
    "hspd",
    "multifactor",
    "mfa",
    "authorization",
    "accreditation",
    "audit",
    "vulnerability",
    "threat",
    "forensics",
    "controls",
    "control",
    "policy",
    "compliance",
    "risk",
    "foreign",
}
ITOPS_TIEBREAKER_SECURITY_HINT_PHRASES = [
    "foreign national risk assessment",
    "control assessment",
    "security policy",
    "security controls",
    "multifactor authentication",
    "certification and accreditation",
    "systems and services acquisition",
    "configuration management",
]


STOPWORDS = {
    # English-ish glue
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "s",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
    # File-ish
    "final",
    "draft",
    "copy",
    "v1",
    "v2",
    "v3",
    "version",
    "new",
    "old",
    "update",
    "updated",
    "doc",
    "docs",
    "document",
    "documents",
    "file",
    "files",
    "notes",
}

VENDOR_KEYWORDS = [
    "microsoft",
    "google",
    "amazon",
    "aws",
    "oracle",
    "cisco",
    "vmware",
    "dell",
    "hpe",
    "hp",
    "ibm",
    "salesforce",
    "servicenow",
    "adobe",
    "atlassian",
    "github",
    "gitlab",
    "carahsoft",
    "acquia",
    "box",
    "zoom",
]

LOCATION_HINTS = [
    "new york",
    "chicago",
    "san francisco",
    "seattle",
    "boston",
    "washington",
    "los angeles",
    "austin",
    "atlanta",
    "phoenix",
]

PEOPLE_TOKEN_STOPWORDS = {
    "about",
    "advanced",
    "answering",
    "asset",
    "change",
    "configuration",
    "controls",
    "cyber",
    "data",
    "director",
    "division",
    "engineering",
    "first",
    "group",
    "high",
    "information",
    "it",
    "job",
    "leader",
    "list",
    "logs",
    "level",
    "laboratory",
    "management",
    "meeting",
    "midrange",
    "monthly",
    "name",
    "national",
    "network",
    "notes",
    "office",
    "operations",
    "panel",
    "principal",
    "problem",
    "prnt",
    "resolution",
    "ring",
    "security",
    "service",
    "status",
    "storage",
    "support",
    "switch",
    "system",
    "technology",
    "title",
    "top",
    "technologies",
    "year",
    "avg",
    "cal",
}
PEOPLE_PHRASE_BLOCKLIST = {
    "cyber security",
    "engineering support",
    "group leader",
    "information technology",
    "monthly meeting",
}
EMAIL_LOCAL_STOPWORDS = {
    "admin",
    "alerts",
    "helpdesk",
    "info",
    "it",
    "noreply",
    "no-reply",
    "service",
    "servicedesk",
    "support",
    "system",
    "team",
}
PROJECT_TOKEN_BLOCKLIST = {
    "API",
    "CSV",
    "DOC",
    "DOCX",
    "EMAIL",
    "GIF",
    "HTML",
    "HTTP",
    "HTTPS",
    "IPV4",
    "IPV6",
    "JPG",
    "JSON",
    "PDF",
    "PNG",
    "PPTX",
    "RTF",
    "SQL",
    "TXT",
    "URL",
    "WWW",
    "XLSX",
    "XML",
    "YAML",
}


def default_rules_obj() -> Dict[str, Any]:
    """
    Built-in, industry-agnostic ontology/routing rules.
    Users can override with --rules rules.json.
    """
    return {
        "rules_version": 2,
        "default_domain": "general",
        "ingest": {
            "exclude_dirs": [],
            "exclude_exts": [],
            "exclude_basenames": [],
            "exclude_path_regex": [],
        },
        "semantic_routing": {
            "default_thresholds": {
                "score_threshold": DEFAULT_DOMAIN_SCORE_THRESHOLD,
                "margin_threshold": 1,
            },
            "threshold_overrides": [
                {
                    "id": "security_compliance_buckets",
                    "first_bucket_regex": "(security|cyber|audit|compliance|policy|risk|mfa|vpn|safety|procedure)",
                    "score_threshold": 2,
                    "margin_threshold": 1,
                },
                {
                    "id": "ops_support_buckets",
                    "first_bucket_regex": "(ops|operations|network|support|service|infra|infrastructure|web|docs)",
                    "score_threshold": 2,
                    "margin_threshold": 1,
                },
            ],
        },
        "domains": [
            {
                "id": "general",
                "name": "General",
                "description": "Unclassified or mixed business content.",
                "keywords": [],
                "phrases": [],
                "regex": [],
            },
            {
                "id": "finance",
                "name": "Finance",
                "description": "AP/AR, invoices, budgeting, forecasting, accounting, tax.",
                "keywords": [
                    "invoice",
                    "invoicing",
                    "po",
                    "purchase",
                    "order",
                    "budget",
                    "forecast",
                    "revenue",
                    "expense",
                    "expenses",
                    "capex",
                    "opex",
                    "payable",
                    "receivable",
                    "accounting",
                    "ledger",
                    "tax",
                    "audit",
                ],
                "phrases": ["purchase order", "general ledger", "accounts payable", "accounts receivable"],
                "regex": [],
            },
            {
                "id": "hr",
                "name": "HR",
                "description": "Hiring, onboarding, payroll, benefits, performance.",
                "keywords": [
                    "employee",
                    "employees",
                    "onboarding",
                    "offboarding",
                    "benefits",
                    "payroll",
                    "compensation",
                    "candidate",
                    "recruiting",
                    "resume",
                    "interview",
                    "performance",
                    "review",
                ],
                "phrases": ["performance review", "offer letter", "employee handbook"],
                "regex": [],
            },
            {
                "id": "legal",
                "name": "Legal",
                "description": "Contracts, agreements, NDAs, terms, legal notices.",
                "keywords": [
                    "contract",
                    "agreement",
                    "nda",
                    "msa",
                    "sow",
                    "addendum",
                    "terms",
                    "liability",
                    "indemnification",
                    "confidentiality",
                    "privacy",
                ],
                "phrases": ["statement of work", "terms and conditions"],
                "regex": [],
            },
            {
                "id": "sales",
                "name": "Sales",
                "description": "CRM, pipeline, opportunities, quotes, proposals, accounts.",
                "keywords": ["customer", "client", "pipeline", "opportunity", "deal", "quote", "proposal", "crm", "salesforce"],
                "phrases": ["sales pipeline", "account executive"],
                "regex": [],
            },
            {
                "id": "marketing",
                "name": "Marketing",
                "description": "Campaigns, brand, web, SEO, content, demand gen.",
                "keywords": ["campaign", "brand", "seo", "newsletter", "leads", "webinar", "content", "landing", "utm"],
                "phrases": ["content calendar"],
                "regex": [],
            },
            {
                "id": "customer_support",
                "name": "Customer Support",
                "description": "Support tickets, cases, SLAs, KB articles, incidents.",
                "keywords": ["ticket", "tickets", "case", "cases", "sla", "support", "escalation", "knowledge", "article"],
                "phrases": ["service level", "knowledge base"],
                "regex": [],
            },
            {
                "id": "engineering",
                "name": "Engineering",
                "description": "Design docs, specs, architecture, SDLC, releases.",
                "keywords": ["engineering", "design", "spec", "specs", "requirements", "architecture", "sdlc", "release", "deploy"],
                "phrases": ["design doc", "release notes"],
                "regex": [],
            },
            {
                "id": "it_ops",
                "name": "IT/Ops",
                "description": "Infrastructure, runbooks, networking, backups, on-call.",
                "keywords": [
                    "server",
                    "network",
                    "dns",
                    "dhcp",
                    "switch",
                    "router",
                    "firewall",
                    "storage",
                    "backup",
                    "restore",
                    "runbook",
                    "oncall",
                    "postmortem",
                    "incident",
                ],
                "phrases": ["on call", "disaster recovery"],
                "regex": [],
            },
            {
                "id": "security",
                "name": "Security",
                "description": "Vulnerabilities, incidents, audits, access control, IAM.",
                "keywords": ["security", "vulnerability", "pentest", "incident", "forensics", "mfa", "iam", "vpn", "password"],
                "phrases": ["incident response", "least privilege"],
                "regex": [],
            },
            {
                "id": "compliance",
                "name": "Compliance",
                "description": "SOC 2, ISO, SOX, GDPR, policies, controls.",
                "keywords": ["compliance", "soc2", "soc", "iso", "sox", "gdpr", "hipaa", "policy", "controls", "risk"],
                "phrases": ["soc 2", "control evidence"],
                "regex": [],
            },
            {
                "id": "product",
                "name": "Product",
                "description": "PRDs, roadmaps, backlogs, user stories, feedback.",
                "keywords": ["product", "prd", "roadmap", "backlog", "mvp", "feature", "feedback", "user", "story"],
                "phrases": ["user story", "product roadmap"],
                "regex": [],
            },
            {
                "id": "operations",
                "name": "Operations",
                "description": "OKRs, KPIs, business operations, process, planning.",
                "keywords": ["operations", "okr", "okrs", "kpi", "kpis", "process", "planning", "metrics", "sop"],
                "phrases": ["operating plan"],
                "regex": [],
            },
            {
                "id": "procurement",
                "name": "Procurement/Vendors",
                "description": "Vendors, suppliers, procurement, RFPs, renewals.",
                "keywords": ["vendor", "vendors", "supplier", "procurement", "rfp", "renewal", "pricing", "quote"],
                "phrases": ["vendor management"],
                "regex": [],
            },
            {
                "id": "executive",
                "name": "Executive/Strategy",
                "description": "Strategy, board, leadership updates, quarterly planning.",
                "keywords": ["strategy", "strategic", "board", "leadership", "qbr", "okr", "initiative"],
                "phrases": ["quarterly business review"],
                "regex": [],
            },
        ],
        "artifact_types": [
            {"id": "meeting_notes", "keywords": ["minutes", "agenda", "meeting", "standup"], "phrases": ["one on one"], "doc_types": [], "exts": []},
            {"id": "logbook", "keywords": ["logbook", "journal", "changelog", "syslog"], "phrases": ["event log"], "doc_types": [], "exts": ["log"]},
            {"id": "procedure", "keywords": ["procedure", "sop"], "phrases": ["work instruction"], "doc_types": [], "exts": []},
            {"id": "policy", "keywords": ["policy"], "phrases": [], "doc_types": [], "exts": []},
            {"id": "plan", "keywords": ["plan", "roadmap", "milestone", "timeline"], "phrases": ["project plan", "program plan"], "doc_types": [], "exts": []},
            {"id": "strategy", "keywords": ["strategy", "strategic"], "phrases": [], "doc_types": [], "exts": []},
            {"id": "architecture", "keywords": ["architecture"], "phrases": ["design doc"], "doc_types": [], "exts": []},
            {"id": "contract", "keywords": ["contract", "agreement", "msa", "sow", "nda", "addendum"], "phrases": ["statement of work"], "doc_types": ["word", "pdf"], "exts": []},
            {"id": "invoice", "keywords": ["invoice", "invoicing", "po", "inv"], "phrases": ["purchase order"], "doc_types": [], "exts": []},
            {"id": "report", "keywords": ["report"], "phrases": ["status report", "monthly report", "quarterly report"], "doc_types": [], "exts": []},
            # Defaults-by-doc_type below are still used when rules/heuristics do not match.
            {"id": "deck", "keywords": [], "phrases": [], "doc_types": ["powerpoint"], "exts": []},
            {"id": "dataset", "keywords": [], "phrases": [], "doc_types": ["spreadsheet"], "exts": []},
            {"id": "diagram", "keywords": [], "phrases": [], "doc_types": ["visio"], "exts": []},
            {"id": "web_page", "keywords": [], "phrases": [], "doc_types": ["html"], "exts": ["html", "htm"]},
            {"id": "photo", "keywords": [], "phrases": [], "doc_types": ["image"], "exts": []},
            {"id": "email", "keywords": [], "phrases": [], "doc_types": ["email"], "exts": []},
        ],
        "sensitivity": {
            "flags": [
                {
                    "id": "pii_possible",
                    "path_keywords": ["roster", "ssn", "passport", "dob", "drivers_license", "badge", "employee_list"],
                    "content_keywords": ["social security", "ssn", "date of birth", "passport", "driver license"],
                    "regex": [
                        {"pattern": "\\b\\d{3}-\\d{2}-\\d{4}\\b", "flags": "m"},
                    ],
                },
                {
                    "id": "credentials_possible",
                    "path_keywords": [],
                    "content_keywords": ["password", "passwd", "secret", "token", "private key", "api key", "client_secret"],
                    "regex": [
                        {"pattern": "AKIA[0-9A-Z]{16}", "flags": "i"},
                        {"pattern": "-----BEGIN [A-Z ]*PRIVATE KEY-----", "flags": "m"},
                    ],
                },
                {
                    "id": "security_sensitive",
                    "path_keywords": ["security", "audit", "incident", "vulnerability", "pentest", "mfa", "vpn"],
                    "content_keywords": ["confidential", "restricted", "do not distribute", "internal use only"],
                    "regex": [],
                },
                {"id": "email", "path_keywords": [], "content_keywords": [], "regex": []},
            ],
            "level_map": {
                "critical": ["credentials_possible"],
                "high": ["pii_possible"],
                "medium": ["security_sensitive", "email"],
                "normal": [],
            },
            "redactions": [
                {
                    "id": "email_addr",
                    "pattern": "\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b",
                    "replacement": "[EMAIL]",
                    "flags": "i",
                },
                {
                    "id": "ipv4",
                    "pattern": "\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b",
                    "replacement": "[IPV4]",
                    "flags": "m",
                },
                {
                    "id": "ssn",
                    "pattern": "\\b\\d{3}-\\d{2}-\\d{4}\\b",
                    "replacement": "[SSN]",
                    "flags": "m",
                },
                {
                    "id": "private_key_block",
                    "pattern": "-----BEGIN [A-Z ]*PRIVATE KEY-----[\\s\\S]*?-----END [A-Z ]*PRIVATE KEY-----",
                    "replacement": "[PRIVATE_KEY_BLOCK]",
                    "flags": "m",
                },
                {
                    "id": "kv_secret",
                    "pattern": "(?i)\\b(password|passwd|secret|token|api[_ -]?key|client_secret)\\b\\s*[:=]\\s*([^\\s]+)",
                    "replacement": "\\1=[REDACTED]",
                    "flags": "m",
                },
            ],
        },
    }


@dataclass(frozen=True)
class DomainRule:
    id: str
    name: str
    description: str
    keywords: set[str]
    phrases: List[str]
    regex: List[re.Pattern]
    negative_keywords: set[str]
    negative_phrases: List[str]
    weights: Dict[str, int]


@dataclass(frozen=True)
class ArtifactTypeRule:
    id: str
    keywords: set[str]
    phrases: List[str]
    regex: List[re.Pattern]
    doc_types: set[str]
    exts: set[str]


@dataclass(frozen=True)
class SensitivityFlagRule:
    id: str
    path_keywords: set[str]
    content_keywords: set[str]
    regex: List[re.Pattern]


@dataclass(frozen=True)
class RedactionRule:
    id: str
    pattern: re.Pattern
    replacement: str


@dataclass(frozen=True)
class SemanticThresholdOverride:
    id: str
    first_bucket: str
    sub_bucket: str
    first_bucket_regex: Optional[re.Pattern]
    path_prefix: str
    path_regex: Optional[re.Pattern]
    score_threshold: Optional[int]
    margin_threshold: Optional[int]


@dataclass(frozen=True)
class RulesContext:
    source: str  # path or "<builtin>"
    sha256: str
    raw: Dict[str, Any]
    default_domain: str
    domains: List[DomainRule]
    domain_ids: List[str]
    artifact_types: List[ArtifactTypeRule]
    artifact_type_ids: List[str]
    sensitivity_flags: List[SensitivityFlagRule]
    level_map: Dict[str, List[str]]
    redactions: List[RedactionRule]
    semantic_default_score_threshold: int
    semantic_default_margin_threshold: int
    semantic_threshold_overrides: List[SemanticThresholdOverride]
    ingest_exclude_dirs: set[str]
    ingest_exclude_exts: set[str]
    ingest_exclude_basenames: set[str]
    ingest_exclude_path_regex: List[re.Pattern]


@dataclass(frozen=True)
class SemanticDecision:
    domain: str
    score: int
    margin: int
    decision: str
    abstain_reason: str
    top_candidates: List[Dict[str, Any]]


def _re_flags(flags: str) -> int:
    f = 0
    flags = (flags or "")
    if "i" in flags:
        f |= re.IGNORECASE
    if "m" in flags:
        f |= re.MULTILINE
    if "s" in flags:
        f |= re.DOTALL
    return f


def _compile_regex_list(items: Any) -> List[re.Pattern]:
    out: List[re.Pattern] = []
    if not items:
        return out
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, str):
            pat = it
            flags = re.IGNORECASE
        elif isinstance(it, dict):
            pat = str(it.get("pattern") or "")
            flags = _re_flags(str(it.get("flags") or "i"))
        else:
            continue
        if not pat:
            continue
        try:
            out.append(re.compile(pat, flags))
        except re.error:
            continue
    return out


def _compile_single_regex(item: Any) -> Optional[re.Pattern]:
    if not item:
        return None
    if isinstance(item, str):
        pat = item
        flags = re.IGNORECASE
    elif isinstance(item, dict):
        pat = str(item.get("pattern") or "")
        flags = _re_flags(str(item.get("flags") or "i"))
    else:
        return None
    if not pat:
        return None
    try:
        return re.compile(pat, flags)
    except re.error:
        return None


def _int_or_none(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except Exception:
        return None


def _normalize_phrase(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_set(s: str) -> set[str]:
    return set(tokenize_for_rules(s))


def load_rules(rules_path: str) -> RulesContext:
    source = "<builtin>"
    raw: Dict[str, Any]
    sha = ""
    if rules_path:
        rp = Path(rules_path).expanduser()
        source = norm_path(str(rp))
        try:
            b = rp.read_bytes()
            sha = hashlib.sha256(b).hexdigest()
            raw = json.loads(b.decode("utf-8", errors="replace"))
        except Exception:
            raw = default_rules_obj()
            sha = hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()
            source = "<builtin_fallback>"
    else:
        raw = default_rules_obj()
        sha = hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()

    default_domain = slug(str(raw.get("default_domain") or "general"))

    # Ingest controls
    ingest = raw.get("ingest") if isinstance(raw, dict) else {}
    if not isinstance(ingest, dict):
        ingest = {}
    ingest_exclude_dirs = {str(x).strip() for x in (ingest.get("exclude_dirs") or []) if str(x).strip()}
    ingest_exclude_exts = {str(x).lower().lstrip(".") for x in (ingest.get("exclude_exts") or []) if str(x).strip()}
    ingest_exclude_basenames = {str(x).strip().lower() for x in (ingest.get("exclude_basenames") or []) if str(x).strip()}
    ingest_exclude_path_regex = _compile_regex_list(ingest.get("exclude_path_regex"))

    # Semantic routing confidence defaults + per-bucket overrides.
    sem_cfg = raw.get("semantic_routing") if isinstance(raw, dict) else {}
    if not isinstance(sem_cfg, dict):
        sem_cfg = {}

    sem_def = sem_cfg.get("default_thresholds")
    if not isinstance(sem_def, dict):
        sem_def = {}
    semantic_default_score_threshold = _int_or_none(
        sem_def.get("score_threshold", sem_def.get("score", sem_cfg.get("score_threshold")))
    )
    semantic_default_margin_threshold = _int_or_none(
        sem_def.get("margin_threshold", sem_def.get("margin", sem_cfg.get("margin_threshold")))
    )
    if semantic_default_score_threshold is None:
        semantic_default_score_threshold = DEFAULT_DOMAIN_SCORE_THRESHOLD
    if semantic_default_margin_threshold is None:
        semantic_default_margin_threshold = DEFAULT_DOMAIN_MARGIN_THRESHOLD
    semantic_default_score_threshold = max(0, int(semantic_default_score_threshold))
    semantic_default_margin_threshold = max(0, int(semantic_default_margin_threshold))

    semantic_threshold_overrides: List[SemanticThresholdOverride] = []
    over_items = sem_cfg.get("threshold_overrides")
    if not isinstance(over_items, list):
        over_items = []
    for i, ov in enumerate(over_items):
        if not isinstance(ov, dict):
            continue
        oid = slug(str(ov.get("id") or f"ov_{i+1}"))
        fb_raw = str(ov.get("first_bucket") or "").strip()
        first_bucket = slug(fb_raw) if fb_raw else ""
        sub_bucket = "/".join(slug(x) for x in norm_path(str(ov.get("sub_bucket") or "")).split("/") if x)
        first_bucket_regex = _compile_single_regex(ov.get("first_bucket_regex"))
        path_prefix = norm_path(str(ov.get("path_prefix") or "")).strip("/")
        path_regex = _compile_single_regex(ov.get("path_regex"))
        score_threshold = _int_or_none(ov.get("score_threshold", ov.get("score")))
        margin_threshold = _int_or_none(ov.get("margin_threshold", ov.get("margin")))
        if not any([first_bucket, sub_bucket, first_bucket_regex, path_prefix, path_regex]):
            continue
        if score_threshold is not None:
            score_threshold = max(0, int(score_threshold))
        if margin_threshold is not None:
            margin_threshold = max(0, int(margin_threshold))
        semantic_threshold_overrides.append(
            SemanticThresholdOverride(
                id=oid or f"ov_{i+1}",
                first_bucket=first_bucket,
                sub_bucket=sub_bucket,
                first_bucket_regex=first_bucket_regex,
                path_prefix=path_prefix,
                path_regex=path_regex,
                score_threshold=score_threshold,
                margin_threshold=margin_threshold,
            )
        )

    # Domains
    domains: List[DomainRule] = []
    dom_items = raw.get("domains") if isinstance(raw, dict) else []
    if not isinstance(dom_items, list):
        dom_items = []
    for d in dom_items:
        if not isinstance(d, dict):
            continue
        did = slug(str(d.get("id") or ""))
        if not did:
            continue
        name = str(d.get("name") or did)
        desc = str(d.get("description") or "")
        kw = set(_token_set(" ".join(str(x) for x in (d.get("keywords") or []) if x)))
        phrases = [_normalize_phrase(str(x)) for x in (d.get("phrases") or []) if x]
        phrases = [p for p in phrases if p]
        rx = _compile_regex_list(d.get("regex"))
        nkw = set(_token_set(" ".join(str(x) for x in (d.get("negative_keywords") or []) if x)))
        nphr = [_normalize_phrase(str(x)) for x in (d.get("negative_phrases") or []) if x]
        nphr = [p for p in nphr if p]
        weights_obj = d.get("weights") if isinstance(d.get("weights"), dict) else {}
        weights = {
            "path": int(weights_obj.get("path", 3)),
            "meta": int(weights_obj.get("meta", 2)),
            "sample": int(weights_obj.get("sample", 1)),
            "phrase": int(weights_obj.get("phrase", 4)),
            "regex": int(weights_obj.get("regex", 4)),
            "negative": int(weights_obj.get("negative", 3)),
        }
        domains.append(
            DomainRule(
                id=did,
                name=name,
                description=desc,
                keywords=kw,
                phrases=phrases,
                regex=rx,
                negative_keywords=nkw,
                negative_phrases=nphr,
                weights=weights,
            )
        )

    # De-dupe domains by id (keep first occurrence for determinism).
    dom_seen: set[str] = set()
    dom_final: List[DomainRule] = []
    for dr in domains:
        if dr.id in dom_seen:
            continue
        dom_seen.add(dr.id)
        dom_final.append(dr)
    domains = dom_final

    # Ensure default and 'general' domains exist as explicit choices.
    existing = {d.id for d in domains}
    for missing in {default_domain, "general"}:
        if missing in existing:
            continue
        domains.append(
            DomainRule(
                id=missing,
                name=missing,
                description="",
                keywords=set(),
                phrases=[],
                regex=[],
                negative_keywords=set(),
                negative_phrases=[],
                weights={"path": 3, "meta": 2, "sample": 1, "phrase": 4, "regex": 4, "negative": 3},
            )
        )

    domain_ids = sorted({d.id for d in domains} | {default_domain} | {"general"})
    if default_domain not in domain_ids:
        default_domain = "general"

    # Artifact types
    art_rules: List[ArtifactTypeRule] = []
    art_items = raw.get("artifact_types") if isinstance(raw, dict) else []
    if not isinstance(art_items, list):
        art_items = []
    for r in art_items:
        if not isinstance(r, dict):
            continue
        rid = slug(str(r.get("id") or ""))
        if not rid:
            continue
        kw = set(_token_set(" ".join(str(x) for x in (r.get("keywords") or []) if x)))
        phrases = [_normalize_phrase(str(x)) for x in (r.get("phrases") or []) if x]
        phrases = [p for p in phrases if p]
        rx = _compile_regex_list(r.get("regex"))
        doc_types = {str(x).strip().lower() for x in (r.get("doc_types") or []) if x}
        exts = {str(x).lower().lstrip(".") for x in (r.get("exts") or []) if x}
        art_rules.append(ArtifactTypeRule(id=rid, keywords=kw, phrases=phrases, regex=rx, doc_types=doc_types, exts=exts))

    # De-dupe artifact_type rules by id (keep first).
    art_seen: set[str] = set()
    art_final: List[ArtifactTypeRule] = []
    for ar in art_rules:
        if ar.id in art_seen:
            continue
        art_seen.add(ar.id)
        art_final.append(ar)
    art_rules = art_final

    artifact_type_ids = sorted({r.id for r in art_rules} | set(ARTIFACT_TYPE_ENUM))

    # Sensitivity + redactions
    sens = raw.get("sensitivity") if isinstance(raw, dict) else {}
    if not isinstance(sens, dict):
        sens = {}
    flag_rules: List[SensitivityFlagRule] = []
    for fr in sens.get("flags") or []:
        if not isinstance(fr, dict):
            continue
        fid = slug(str(fr.get("id") or ""))
        if not fid:
            continue
        pk = set(_token_set(" ".join(str(x) for x in (fr.get("path_keywords") or []) if x)))
        ck = set(_token_set(" ".join(str(x) for x in (fr.get("content_keywords") or []) if x)))
        rx = _compile_regex_list(fr.get("regex"))
        flag_rules.append(SensitivityFlagRule(id=fid, path_keywords=pk, content_keywords=ck, regex=rx))

    level_map: Dict[str, List[str]] = {}
    lm = sens.get("level_map")
    if isinstance(lm, dict):
        for k, v in lm.items():
            if not isinstance(v, list):
                continue
            level_map[str(k)] = [slug(str(x)) for x in v if x]
    if not level_map:
        level_map = {
            "critical": ["credentials_possible"],
            "high": ["pii_possible"],
            "medium": ["security_sensitive", "email"],
            "normal": [],
        }

    redactions: List[RedactionRule] = []
    for rr in sens.get("redactions") or []:
        if not isinstance(rr, dict):
            continue
        rid = slug(str(rr.get("id") or ""))
        pat = str(rr.get("pattern") or "")
        repl = str(rr.get("replacement") or "[REDACTED]")
        flags = _re_flags(str(rr.get("flags") or "im"))
        if not pat:
            continue
        try:
            redactions.append(RedactionRule(id=rid or "redaction", pattern=re.compile(pat, flags), replacement=repl))
        except re.error:
            continue

    return RulesContext(
        source=source,
        sha256=sha,
        raw=raw if isinstance(raw, dict) else {},
        default_domain=default_domain,
        domains=domains,
        domain_ids=domain_ids,
        artifact_types=art_rules,
        artifact_type_ids=artifact_type_ids,
        sensitivity_flags=flag_rules,
        level_map=level_map,
        redactions=redactions,
        semantic_default_score_threshold=int(semantic_default_score_threshold),
        semantic_default_margin_threshold=int(semantic_default_margin_threshold),
        semantic_threshold_overrides=semantic_threshold_overrides,
        ingest_exclude_dirs=ingest_exclude_dirs,
        ingest_exclude_exts=ingest_exclude_exts,
        ingest_exclude_basenames=ingest_exclude_basenames,
        ingest_exclude_path_regex=ingest_exclude_path_regex,
    )


def redact_text(text: str, redactions: Sequence[RedactionRule]) -> str:
    out = text or ""
    for rr in redactions:
        try:
            out = rr.pattern.sub(rr.replacement, out)
        except Exception:
            continue
    return out


def _module_version(mod_name: str) -> str:
    try:
        m = __import__(mod_name)
        v = getattr(m, "__version__", "")
        return str(v) if v is not None else ""
    except Exception:
        return ""


def detected_backends() -> Dict[str, Any]:
    return {
        "pypdf_or_pypdf2": {"available": _PdfReader is not None, "version": (_module_version("pypdf") or _module_version("PyPDF2"))},
        "pdfminer_six": {"available": _pdfminer_extract_text is not None, "version": _module_version("pdfminer")},
        "beautifulsoup4": {"available": _BeautifulSoup is not None, "version": _module_version("bs4")},
        "openpyxl": {"available": _load_workbook is not None, "version": _module_version("openpyxl")},
        "python_pptx": {"available": _PptxPresentation is not None, "version": _module_version("pptx")},
        "python_docx": {"available": _DocxDocument is not None, "version": _module_version("docx")},
        "striprtf": {"available": _rtf_to_text is not None, "version": _module_version("striprtf")},
        "oxmsg": {"available": _OxMsgMessage is not None, "version": _module_version("oxmsg")},
        "extract_msg": {"available": _extract_msg is not None, "version": _module_version("extract_msg")},
    }


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def norm_path(p: str) -> str:
    return p.replace("\\", "/")


def ascii_sanitize(s: str) -> str:
    # Normalize a few common Unicode punctuation characters then ASCII-replace.
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s.encode("ascii", "replace").decode("ascii")


def slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def is_artifact_path(p: Path) -> bool:
    base = p.name
    if base in ARTIFACT_BASENAMES:
        return True
    # Office temporary/lock files.
    if base.startswith("~$"):
        return True
    if base.startswith("._"):
        return True
    parts = norm_path(str(p)).split("/")
    if "__MACOSX" in parts:
        return True
    return False


def ext_lower(p: Path) -> str:
    b = p.name.lower()
    i = b.rfind(".")
    return b[i + 1 :] if i != -1 else "(noext)"


_TEXTISH_EXTENSION_ALIASES = {
    "old",
    "new",
    "bak",
    "orig",
    "file",
    "security",
    "crt",
    "cer",
    "pem",
    "key",
    "csr",
    "req",
    "pub",
}


def canonical_extract_ext(p: Path, ext: str) -> str:
    e = (ext or "").strip().lower()
    if not e:
        e = ext_lower(p)
    name = p.name.lower()

    if e in _TEXTISH_EXTENSION_ALIASES:
        return "txt"

    # Historic rolled log exports often use numeric final suffixes (e.g. log.file.07).
    if e.isdigit() and ("log.file." in name or name.startswith("log.") or ".log." in name):
        return "log"

    # Date-tagged request files in this corpus are plain text.
    if re.fullmatch(r"\d{1,2}[a-z]{3}\d{2}", e):
        return "txt"

    return e or "(noext)"


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def file_mtime_date(p: Path) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))


def sha256_file(p: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


class _StopParsing(Exception):
    pass


def xml_text_sample(xml_bytes: bytes, limit_chars: int) -> str:
    """
    Extract text from OpenXML-ish XML by collecting element text nodes of common
    tags (usually ...}t). Uses iterparse to avoid building the full tree.
    """
    out: List[str] = []
    total = 0
    try:
        for _, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
            tag = elem.tag
            if isinstance(tag, str) and tag.endswith("}t") and elem.text:
                t = elem.text
                if t:
                    out.append(t)
                    total += len(t) + 1
                    if total >= limit_chars:
                        raise _StopParsing()
            elem.clear()
    except _StopParsing:
        pass
    except Exception:
        return ""
    return ascii_sanitize(" ".join(out))[:limit_chars]


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return s


def read_text_file(p: Path, limit_chars: int) -> str:
    try:
        # Avoid reading entire large files into memory.
        with p.open("r", encoding="utf-8", errors="replace") as f:
            raw = f.read(limit_chars * 4)
    except Exception:
        return ""
    if p.suffix.lower() in {".html", ".htm"}:
        if _BeautifulSoup is not None:
            try:
                soup = _BeautifulSoup(raw, "html.parser")
                raw = soup.get_text(" ", strip=True)
            except Exception:
                raw = strip_html(raw)
        else:
            raw = strip_html(raw)
    return ascii_sanitize(raw)[:limit_chars]


def _normalize_csv_header_name(name: str) -> str:
    txt = str(name or "").replace("\ufeff", "").strip()
    txt = txt.strip("\"'`")
    txt = txt.lower()
    txt = re.sub(r"[^a-z0-9]+", "_", txt)
    txt = re.sub(r"_+", "_", txt).strip("_")
    return txt


def _clean_csv_cell(value: Any, *, max_chars: int) -> str:
    txt = str(value or "")
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    txt = re.sub(r"\s+", " ", txt).strip()
    if max_chars > 0 and len(txt) > max_chars:
        txt = txt[: max(0, int(max_chars) - 3)].rstrip() + "..."
    return ascii_sanitize(txt)


def _first_matching_csv_field(
    normalized_pairs: Sequence[Tuple[str, str]],
    *,
    required_terms: Sequence[str],
) -> str:
    terms = [str(t).strip().lower() for t in required_terms if str(t).strip()]
    if not terms:
        return ""
    for norm_name, raw_name in normalized_pairs:
        if all(t in norm_name for t in terms):
            return raw_name
    return ""


def _detect_outlook_csv_columns(fieldnames: Sequence[str]) -> Dict[str, str]:
    normalized_pairs: List[Tuple[str, str]] = []
    for raw in fieldnames:
        norm = _normalize_csv_header_name(raw)
        if not norm:
            continue
        normalized_pairs.append((norm, str(raw)))

    subject_col = _first_matching_csv_field(normalized_pairs, required_terms=["subject"])
    body_col = _first_matching_csv_field(normalized_pairs, required_terms=["body"])
    from_name_col = _first_matching_csv_field(normalized_pairs, required_terms=["from", "name"])
    from_addr_col = _first_matching_csv_field(normalized_pairs, required_terms=["from", "address"])
    to_name_col = _first_matching_csv_field(normalized_pairs, required_terms=["to", "name"])
    to_addr_col = _first_matching_csv_field(normalized_pairs, required_terms=["to", "address"])
    cc_name_col = _first_matching_csv_field(normalized_pairs, required_terms=["cc", "name"])
    cc_addr_col = _first_matching_csv_field(normalized_pairs, required_terms=["cc", "address"])
    sent_col = _first_matching_csv_field(normalized_pairs, required_terms=["sent"])
    received_col = _first_matching_csv_field(normalized_pairs, required_terms=["received"])
    categories_col = _first_matching_csv_field(normalized_pairs, required_terms=["categories"])
    importance_col = _first_matching_csv_field(normalized_pairs, required_terms=["importance"])
    sensitivity_col = _first_matching_csv_field(normalized_pairs, required_terms=["sensitivity"])

    has_mail_core = bool(subject_col and body_col and (from_name_col or from_addr_col or to_name_col or to_addr_col))
    mailish_cols = 0
    for norm_name, _raw_name in normalized_pairs:
        if norm_name.startswith("from_") or norm_name.startswith("to_") or norm_name.startswith("cc_") or norm_name.startswith("bcc_"):
            mailish_cols += 1
        elif norm_name in {"subject", "body", "sent", "received", "importance", "sensitivity", "categories"}:
            mailish_cols += 1
    is_outlook_mail_csv = has_mail_core or (mailish_cols >= 4 and bool(subject_col or body_col))

    return {
        "is_outlook_mail_csv": "1" if is_outlook_mail_csv else "0",
        "subject": subject_col,
        "body": body_col,
        "from_name": from_name_col,
        "from_address": from_addr_col,
        "to_name": to_name_col,
        "to_address": to_addr_col,
        "cc_name": cc_name_col,
        "cc_address": cc_addr_col,
        "sent": sent_col,
        "received": received_col,
        "categories": categories_col,
        "importance": importance_col,
        "sensitivity": sensitivity_col,
    }


def extract_csv_mail_sample(
    p: Path,
    limit_chars: int,
    *,
    max_rows: int = 600,
    max_subject_chars: int = 240,
    max_body_chars: int = 2200,
) -> Tuple[str, str, str]:
    """
    Detect Outlook-style mail CSV exports and serialize rows into explicit
    email-entry blocks for downstream chunkers.
    """
    try:
        with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = [str(x) for x in (reader.fieldnames or []) if str(x).strip()]
            if not fieldnames:
                return "", "text", "csv_mail:no_header"

            cols = _detect_outlook_csv_columns(fieldnames)
            if cols.get("is_outlook_mail_csv") != "1":
                return "", "text", "csv_mail:not_detected"

            blocks: List[str] = []
            total = 0
            emitted = 0
            body_trimmed = 0
            forced_trimmed = 0
            for row_idx, row in enumerate(reader, start=1):
                if emitted >= int(max_rows):
                    break
                if not isinstance(row, dict):
                    continue

                subject = _clean_csv_cell(row.get(cols.get("subject", ""), ""), max_chars=max_subject_chars)
                body = _clean_csv_cell(row.get(cols.get("body", ""), ""), max_chars=max_body_chars)
                from_name = _clean_csv_cell(row.get(cols.get("from_name", ""), ""), max_chars=220)
                from_addr = _clean_csv_cell(row.get(cols.get("from_address", ""), ""), max_chars=220)
                to_name = _clean_csv_cell(row.get(cols.get("to_name", ""), ""), max_chars=220)
                to_addr = _clean_csv_cell(row.get(cols.get("to_address", ""), ""), max_chars=220)
                cc_name = _clean_csv_cell(row.get(cols.get("cc_name", ""), ""), max_chars=220)
                sent = _clean_csv_cell(row.get(cols.get("sent", ""), ""), max_chars=120)
                received = _clean_csv_cell(row.get(cols.get("received", ""), ""), max_chars=120)
                categories = _clean_csv_cell(row.get(cols.get("categories", ""), ""), max_chars=160)
                importance = _clean_csv_cell(row.get(cols.get("importance", ""), ""), max_chars=80)
                sensitivity = _clean_csv_cell(row.get(cols.get("sensitivity", ""), ""), max_chars=80)

                if not subject and not body:
                    continue
                if body.endswith("..."):
                    body_trimmed += 1

                lines: List[str] = [f"email_row_id: {row_idx}"]
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
                if total + extra > int(limit_chars):
                    # Keep at least one trimmed email block when the first row
                    # exceeds the sample budget.
                    if not blocks:
                        budget = max(1, int(limit_chars) - total)
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
                return "", "text", "csv_mail:no_rows"
            sample = "\n\n".join(blocks)
            notes = (
                f"csv_mail_rows={emitted};"
                f"csv_mail_body_trimmed={body_trimmed};"
                f"csv_mail_forced_trimmed={forced_trimmed}"
            )
            return sample, "csv_mail_rows", notes
    except Exception as exc:
        return "", "text", f"csv_mail:parse_error={type(exc).__name__}"


def extract_boxnote_text(p: Path, limit_chars: int) -> str:
    """
    Box Notes export is JSON; extract text nodes deterministically.
    """
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""

    parts: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                parts.append(node["text"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(data)
    return ascii_sanitize(" ".join(parts))[:limit_chars]


def extract_pdf_text(p: Path, limit_chars: int, max_pages: int) -> str:
    # Prefer pypdf/PyPDF2 when present; if extraction is empty and pdfminer is
    # available, fall back (often better for tricky PDFs, but slower).
    out: List[str] = []
    if _PdfReader is not None:
        try:
            reader = _PdfReader(str(p))
            for i in range(min(max_pages, len(reader.pages))):
                try:
                    t = reader.pages[i].extract_text() or ""
                except Exception:
                    t = ""
                if t:
                    out.append(t)
                    if sum(len(x) for x in out) >= limit_chars:
                        break
        except Exception:
            out = []

    joined = ascii_sanitize(" ".join(out))[:limit_chars]
    if joined.strip():
        return joined

    if _pdfminer_extract_text is None:
        return joined

    try:
        txt = _pdfminer_extract_text(str(p), page_numbers=list(range(max(0, int(max_pages)))), maxpages=int(max_pages))
    except Exception:
        return joined
    return ascii_sanitize(txt)[:limit_chars]


def extract_eml_sample(p: Path, limit_chars: int, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    """
    Extract a lightweight, deterministic text sample from an .eml file.
    Uses stdlib email parsing (no external deps).
    """
    try:
        import email
        import email.policy

        with p.open("rb") as f:
            raw = f.read(int(max_bytes))
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        return ""

    subj = (msg.get("subject") or "").strip()
    frm = (msg.get("from") or "").strip()
    to = (msg.get("to") or "").strip()
    date = (msg.get("date") or "").strip()

    body_parts: List[str] = []
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = (part.get_content_type() or "").lower()
                if ctype == "text/plain":
                    try:
                        body_parts.append(part.get_content())
                    except Exception:
                        continue
        else:
            body_parts.append(msg.get_content())
    except Exception:
        body_parts = []

    header = f"subject={subj}; from={frm}; to={to}; date={date}"
    blob = " ".join([header] + body_parts)
    return ascii_sanitize(blob)[:limit_chars]


def extract_msg_sample(p: Path, limit_chars: int, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    """
    Extract a lightweight, deterministic text sample from an Outlook .msg file.
    Prefers python-oxmsg; falls back to extract-msg if installed.
    """
    if _OxMsgMessage is not None:
        try:
            msg = _OxMsgMessage.load(str(p))
            subj = (getattr(msg, "subject", "") or "").strip()
            body = (getattr(msg, "body", "") or "").strip()
            html_body = (getattr(msg, "html_body", "") or "").strip()
            chosen = body or html_body
            blob = f"subject={subj}; body={chosen}"
            return ascii_sanitize(blob)[:limit_chars]
        except Exception:
            pass

    if _extract_msg is not None:
        try:
            # Per extract-msg docs, prefer openMsg() over direct construction.
            msg = _extract_msg.openMsg(str(p))
            try:
                subj = (getattr(msg, "subject", "") or "").strip()
                sender = (getattr(msg, "sender", "") or "").strip()
                to = (getattr(msg, "to", "") or "").strip()
                date = (getattr(msg, "date", "") or "").strip()
                body = (getattr(msg, "body", "") or "").strip()
                header = f"subject={subj}; from={sender}; to={to}; date={date}"
                blob = " ".join([header, body])
                return ascii_sanitize(blob)[:limit_chars]
            finally:
                try:
                    msg.close()
                except Exception:
                    pass
        except Exception:
            pass

    return extract_msg_fallback_sample(p, limit_chars, max_bytes=max_bytes)


def extract_openxml_core(z: zipfile.ZipFile) -> Dict[str, str]:
    try:
        core = z.read("docProps/core.xml")
    except KeyError:
        return {}
    try:
        root = ET.fromstring(core)
    except Exception:
        return {}

    ns = {
        "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
        "dc": "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
    }

    def get(xp: str) -> str:
        el = root.find(xp, ns)
        return (el.text or "").strip() if el is not None else ""

    return {
        "title": get(".//dc:title"),
        "subject": get(".//dc:subject"),
        "creator": get(".//dc:creator"),
        "created": get(".//dcterms:created"),
        "modified": get(".//dcterms:modified"),
    }


def extract_docx_sample(p: Path, limit_chars: int) -> Tuple[Dict[str, str], str]:
    core: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(p, "r") as z:
            core = extract_openxml_core(z)
            try:
                xml = z.read("word/document.xml")
            except Exception:
                xml = b""
    except Exception:
        xml = b""

    # Fast path: stream text out of document.xml without full object model parsing.
    if xml:
        s = xml_text_sample(xml, limit_chars)
        if s.strip():
            return core, s

    # Fallback: python-docx can surface text in some edge cases (tables, etc.)
    if _DocxDocument is None:
        return core, ""
    try:
        doc = _DocxDocument(str(p))
        parts: List[str] = []
        total = 0
        for para in getattr(doc, "paragraphs", []):
            t = (getattr(para, "text", "") or "").strip()
            if not t:
                continue
            parts.append(t)
            total += len(t) + 1
            if total >= limit_chars:
                break
        if total < limit_chars:
            for table in getattr(doc, "tables", []):
                for row in getattr(table, "rows", []):
                    for cell in getattr(row, "cells", []):
                        t = (getattr(cell, "text", "") or "").strip()
                        if not t:
                            continue
                        parts.append(t)
                        total += len(t) + 1
                        if total >= limit_chars:
                            break
                    if total >= limit_chars:
                        break
                if total >= limit_chars:
                    break
        return core, ascii_sanitize(" ".join(parts))[:limit_chars]
    except Exception:
        return core, ""


def extract_pptx_sample(p: Path, limit_chars: int, max_slides: int) -> Tuple[Dict[str, str], str]:
    try:
        with zipfile.ZipFile(p, "r") as z:
            core = extract_openxml_core(z)
            slide_names = sorted([n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")])
            out = []
            total = 0
            for sn in slide_names[:max_slides]:
                try:
                    xml = z.read(sn)
                except KeyError:
                    continue
                s = xml_text_sample(xml, limit_chars)
                if s:
                    out.append(s)
                    total += len(s)
                    if total >= limit_chars:
                        break
    except Exception:
        return {}, ""
    joined = ascii_sanitize(" ".join(out))[:limit_chars]
    if joined.strip():
        return core, joined

    if _PptxPresentation is None:
        return core, joined

    # Fallback: python-pptx object model
    try:
        prs = _PptxPresentation(str(p))
        parts: List[str] = []
        total = 0
        for slide in list(getattr(prs, "slides", []))[: int(max_slides)]:
            for shape in getattr(slide, "shapes", []):
                try:
                    if not getattr(shape, "has_text_frame", False):
                        continue
                    t = (getattr(shape, "text", "") or "").strip()
                except Exception:
                    t = ""
                if not t:
                    continue
                parts.append(t)
                total += len(t) + 1
                if total >= limit_chars:
                    break
            if total >= limit_chars:
                break
        return core, ascii_sanitize(" ".join(parts))[:limit_chars]
    except Exception:
        return core, joined


def _xlsx_sheet_names(z: zipfile.ZipFile) -> List[str]:
    try:
        wb = z.read("xl/workbook.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(wb)
    except Exception:
        return []
    names = []
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.endswith("}sheet"):
            n = el.attrib.get("name")
            if n:
                names.append(n)
    return names


def _xlsx_shared_strings_sample(z: zipfile.ZipFile, max_strings: int, max_chars: int) -> List[str]:
    try:
        xml = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    total = 0
    try:
        for _, elem in ET.iterparse(io.BytesIO(xml), events=("end",)):
            if isinstance(elem.tag, str) and elem.tag.endswith("}t") and elem.text:
                t = elem.text.strip()
                if t:
                    t = ascii_sanitize(t)
                    out.append(t)
                    total += len(t) + 1
                    if len(out) >= max_strings or total >= max_chars:
                        break
            elem.clear()
    except Exception:
        return []
    return out


def extract_xlsx_sample(p: Path, limit_chars: int, max_strings: int, max_chars: int) -> Tuple[Dict[str, str], str]:
    core: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(p, "r") as z:
            core = extract_openxml_core(z)
            sheets_zip = _xlsx_sheet_names(z)
            ss = _xlsx_shared_strings_sample(z, max_strings=max_strings, max_chars=max_chars)
    except Exception:
        sheets_zip = []
        ss = []

    # Prefer openpyxl when available, because sharedStrings alone can be misleading.
    if _load_workbook is not None:
        wb = None
        try:
            wb = _load_workbook(filename=str(p), read_only=True, data_only=True)
            sheets = list(getattr(wb, "sheetnames", []) or sheets_zip)
            parts: List[str] = [f"sheets={sheets}"]
            total = 0
            # Budget-based traversal: keep scanning rows/sheets until we consume
            # the sample character budget, with large hard caps for safety.
            row_hard_cap = max(256, int(os.getenv("ROUTER_XLSX_ROW_HARD_CAP", "5000")))
            col_hard_cap = max(16, int(os.getenv("ROUTER_XLSX_COL_HARD_CAP", "64")))
            for sname in sheets:
                try:
                    ws = wb[sname]
                except Exception:
                    continue
                parts.append(f"[sheet:{sname}]")
                for row in ws.iter_rows(min_row=1, max_row=row_hard_cap, max_col=col_hard_cap, values_only=True):
                    vals: List[str] = []
                    for v in row:
                        if v is None:
                            continue
                        sv = str(v).strip()
                        if not sv:
                            continue
                        if len(sv) > 80:
                            sv = sv[:80] + "..."
                        vals.append(sv)
                    if not vals:
                        continue
                    line = " | ".join(vals)
                    parts.append(line)
                    total += len(line) + 1
                    if total >= limit_chars:
                        break
                if total >= limit_chars:
                    break
            sample = ascii_sanitize(" ".join(parts))[:limit_chars]
            if sample.strip():
                return core, sample
        except Exception:
            pass
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass

    sample = f"sheets={sheets_zip}; strings_sample={ss}"
    return core, ascii_sanitize(sample)[:limit_chars]


def extract_rtf_text(p: Path, limit_chars: int) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            raw = f.read(limit_chars * 8)
    except Exception:
        return ""
    if _rtf_to_text is not None:
        try:
            raw = _rtf_to_text(raw)
        except Exception:
            pass
    return ascii_sanitize(raw)[:limit_chars]


def extract_vsdx_sample(p: Path, limit_chars: int) -> Tuple[Dict[str, str], str]:
    try:
        with zipfile.ZipFile(p, "r") as z:
            core = extract_openxml_core(z)
            page_names = []
            try:
                pages_xml = z.read("visio/pages/pages.xml")
                root = ET.fromstring(pages_xml)
                for el in root.iter():
                    if isinstance(el.tag, str) and el.tag.endswith("}Page") and "Name" in el.attrib:
                        page_names.append(el.attrib.get("Name", ""))
            except Exception:
                page_names = []

            page_xml_names = sorted([n for n in z.namelist() if n.startswith("visio/pages/page") and n.endswith(".xml")])
            text_sample = ""
            if page_xml_names:
                try:
                    xml = z.read(page_xml_names[0])
                    text_sample = xml_text_sample(xml, limit_chars)
                except Exception:
                    text_sample = ""
    except Exception:
        return {}, ""
    sample = f"pages={page_names}; text_sample={text_sample}"
    return core, ascii_sanitize(sample)[:limit_chars]


def extract_xps_sample(p: Path, limit_chars: int, max_pages: int = 4) -> str:
    """
    XPS is a zip container of XML fixed pages. Extract a deterministic sample
    from early page XML files, then fall back to member listing.
    """
    try:
        with zipfile.ZipFile(p, "r") as z:
            names = sorted(z.namelist())
            page_files = [n for n in names if n.lower().endswith(".fpage")]
            parts: List[str] = []
            remaining = int(limit_chars)
            for page_name in page_files[: max(1, int(max_pages))]:
                if remaining <= 0:
                    break
                try:
                    xml = z.read(page_name)
                except Exception:
                    continue
                frag = xml_text_sample(xml, max(256, remaining))
                if frag:
                    parts.append(frag)
                    remaining = int(limit_chars) - len(" | ".join(parts))
            if parts:
                return ascii_sanitize(" | ".join(parts))[:limit_chars]
            return extract_zip_members_sample(p, limit_chars, max_members=30)
    except Exception:
        return ""


def extract_zip_members_sample(p: Path, limit_chars: int, max_members: int) -> str:
    try:
        with zipfile.ZipFile(p, "r") as z:
            names = z.namelist()
    except Exception:
        return ""
    sample = f"zip_members_sample={sorted(names)[:max_members]}"
    return ascii_sanitize(sample)[:limit_chars]


def extract_tar_members_sample(p: Path, limit_chars: int, max_members: int) -> str:
    try:
        with tarfile.open(p, "r:*") as t:
            names = [m.name for m in t.getmembers()]
    except Exception:
        return ""
    sample = f"tar_members_sample={sorted(names)[:max_members]}"
    return ascii_sanitize(sample)[:limit_chars]


def ascii_strings_from_bytes(buf: bytes, min_len: int = 6, limit_chars: int = 1200) -> str:
    out = []
    cur: List[str] = []
    total = 0
    for b in buf:
        if 32 <= b <= 126:
            cur.append(chr(b))
        else:
            if len(cur) >= min_len:
                s = "".join(cur)
                out.append(s)
                total += len(s) + 1
                if total >= limit_chars:
                    break
            cur = []
    if total < limit_chars and len(cur) >= min_len:
        s = "".join(cur)
        out.append(s)
    return ascii_sanitize(" | ".join(out))[:limit_chars]


def extract_binary_strings_sample(p: Path, limit_chars: int, max_bytes: int) -> str:
    try:
        with p.open("rb") as f:
            buf = f.read(max_bytes)
    except Exception:
        return ""
    return ascii_strings_from_bytes(buf, min_len=6, limit_chars=limit_chars)


def utf16le_strings_from_bytes(buf: bytes, *, min_len: int = 4, limit_chars: int = 2000) -> str:
    if not buf:
        return ""
    try:
        decoded = buf.decode("utf-16le", errors="ignore")
    except Exception:
        return ""
    out: List[str] = []
    total = 0
    for m in re.finditer(r"[ -~]{%d,}" % int(min_len), decoded):
        t = ascii_sanitize(m.group(0))
        if not t:
            continue
        out.append(t)
        total += len(t) + 1
        if total >= int(limit_chars):
            break
    return ascii_sanitize(" | ".join(out))[: int(limit_chars)]


def extract_msg_fallback_sample(p: Path, limit_chars: int, *, max_bytes: int) -> str:
    """
    Library-free .msg fallback extractor:
    harvest ASCII + UTF-16LE strings and prioritize header/body-like fragments.
    """
    try:
        with p.open("rb") as f:
            buf = f.read(int(max_bytes))
    except Exception:
        return ""

    parts: List[str] = []
    ascii_part = ascii_strings_from_bytes(buf, min_len=4, limit_chars=max(2000, int(limit_chars) * 3))
    utf16_part = utf16le_strings_from_bytes(buf, min_len=4, limit_chars=max(2000, int(limit_chars) * 3))
    if ascii_part:
        parts.extend(ascii_part.split(" | "))
    if utf16_part:
        parts.extend(utf16_part.split(" | "))

    ranked: List[Tuple[int, str]] = []
    for raw in parts:
        t = ascii_sanitize(raw)
        if len(t) < 4:
            continue
        if not re.search(r"[A-Za-z]{3}", t):
            continue
        tl = t.lower()
        score = 0
        if re.search(r"\b(subject|from|to|date|sent|received|cc|bcc)\b", tl):
            score += 4
        if re.search(r"\b(message|incident|alert|update|report|meeting|ticket)\b", tl):
            score += 2
        if "http" in tl:
            score += 1
        ranked.append((score, t))

    if not ranked:
        return ""

    seen: set[str] = set()
    out: List[str] = []
    total = 0
    for _, t in sorted(enumerate(ranked), key=lambda kv: (-kv[1][0], kv[0])):
        chunk = t[1]
        kl = chunk.lower()
        if kl in seen:
            continue
        seen.add(kl)
        out.append(chunk)
        total += len(chunk) + 3
        if total >= int(limit_chars):
            break

    return ascii_sanitize(" | ".join(out))[: int(limit_chars)]


def is_probably_text(buf: bytes) -> bool:
    if not buf:
        return True
    # Null bytes are a strong binary signal.
    if b"\x00" in buf:
        return False
    printable = 0
    for b in buf:
        if 32 <= b <= 126 or b in (9, 10, 13, 12):
            printable += 1
    return (printable / max(1, len(buf))) >= 0.90


def doc_type_from_ext(ext: str) -> str:
    e = (ext or "").lower()
    if e in {"docx", "docm", "dotx", "dotm", "doc"}:
        return "word"
    if e in {"pptx", "pptm", "ppsx", "ppsm", "potx", "potm", "ppt"}:
        return "powerpoint"
    if e in {"xlsx", "xlsm", "xltx", "xltm", "xlsb", "xls", "csv"}:
        return "spreadsheet"
    if e in {"pdf", "xps"}:
        return "pdf"
    if e in {"html", "htm"}:
        return "html"
    if e in {
        "txt",
        "md",
        "rtf",
        "log",
        "json",
        "jsonl",
        "yaml",
        "yml",
        "ini",
        "conf",
        "cfg",
        "xml",
        "boxnote",
        "(noext)",
    } or e in _TEXTISH_EXTENSION_ALIASES:
        return "text"
    if e.isdigit():
        return "text"
    if e in {"msg", "eml"}:
        return "email"
    if e in {"jpg", "jpeg", "png", "gif", "tif", "tiff", "bmp", "heic"}:
        return "image"
    if e in {"vsdx", "vsd"}:
        return "visio"
    if e in {"zip", "tar", "gz", "tgz", "bz2", "xz"}:
        return "archive"
    return "other"


def artifact_type(path: str, doc_type: str, sample: str) -> str:
    pl = path.lower()
    base = os.path.basename(pl)
    s = (sample or "").lower()

    if doc_type == "email":
        return "email"
    if doc_type == "powerpoint":
        return "deck"
    if doc_type == "spreadsheet":
        return "dataset"
    if doc_type == "visio":
        return "diagram"
    if doc_type == "html":
        return "web_page"
    if doc_type == "image":
        return "photo"
    if doc_type == "archive":
        return "other"

    # Keyword-driven (filename + path + small sample)
    blob = f"{pl} {base} {s[:1500]}"
    toks = set(t for t in re.split(r"[^a-z0-9]+", blob) if t)

    if "1:1" in blob or "one on one" in blob or any(t in toks for t in ["minutes", "agenda", "meeting", "standup"]):
        return "meeting_notes"
    if base.endswith(".log") or any(t in toks for t in ["logbook", "journal", "changelog", "syslog"]) or "event log" in blob:
        return "logbook"
    if any(t in toks for t in ["procedure", "sop"]) or "work instruction" in blob or "wi-" in blob:
        return "procedure"
    if "policy" in toks:
        return "policy"
    if any(t in toks for t in ["strategy", "strategic"]):
        return "strategy"
    if "architecture" in toks:
        return "architecture"
    if "plan" in toks or "roadmap" in toks or "program plan" in blob or "project plan" in blob:
        return "plan"
    if any(t in toks for t in ["contract", "agreement", "msa", "sow", "nda", "addendum"]):
        return "contract"
    if "invoice" in toks or ("purchase" in toks and "order" in toks) or "po" in toks or "inv" in toks:
        return "invoice"
    if "report" in toks or "status report" in blob or "monthly report" in blob or "quarterly report" in blob:
        return "report"

    return "other"


def sensitivity_flags(path: str, sample: str) -> List[str]:
    pl = (path or "").lower()
    s = (sample or "").lower()
    flags: List[str] = []

    # PII-ish hints (very heuristic)
    if any(w in pl for w in ["roster", "ssn", "passport", "dob", "drivers_license", "badge", "employee_list"]):
        flags.append("pii_possible")
    if any(w in s for w in ["social security", "ssn", "date of birth", "passport", "driver license"]):
        flags.append("pii_possible")

    # Credentials/secrets
    if any(w in s for w in ["password", "passwd", "secret", "token", "private key", "api key", "client_secret"]):
        flags.append("credentials_possible")

    # Security-ish sensitivity
    if any(w in s for w in ["confidential", "restricted", "do not distribute", "internal use only"]):
        flags.append("security_sensitive")
    if any(w in pl for w in ["security", "audit", "incident", "vulnerability", "pentest", "mfa", "vpn"]):
        flags.append("security_sensitive")

    # Email files
    if pl.endswith(".msg") or pl.endswith(".eml"):
        flags.append("email")

    return sorted(set(flags))


def sensitivity_level(flags: Sequence[str]) -> str:
    fset = set(flags)
    if "credentials_possible" in fset:
        return "critical"
    if "pii_possible" in fset:
        return "high"
    if "security_sensitive" in fset or "email" in fset:
        return "medium"
    return "normal"


def tagger(path: str, sample: str, core_title: str, core_subject: str) -> List[str]:
    blob = " ".join([path, core_title, core_subject, (sample or "")[:1500]]).lower()
    toks = set(tokenize_for_keywords(blob))

    tags: List[str] = []

    # Core business-ish topics
    if "invoice" in toks or ("purchase" in toks and "order" in toks) or any(t in toks for t in ["budget", "forecast", "expense", "revenue", "tax"]):
        tags.append("finance")
    if any(t in toks for t in ["employee", "onboarding", "benefits", "payroll", "resume", "candidate"]) or ("performance" in toks and "review" in toks):
        tags.append("hr")
    if any(t in toks for t in ["contract", "agreement", "msa", "sow", "nda", "legal", "terms", "liability"]):
        tags.append("legal")
    if any(t in toks for t in ["policy", "procedure", "sop"]) or ("work" in toks and "instruction" in toks):
        tags.append("governance")
    if any(t in toks for t in ["audit", "compliance", "iso", "sox", "gdpr", "hipaa"]) or "soc 2" in blob:
        tags.append("compliance")
    if any(t in toks for t in ["security", "mfa", "vpn", "password", "firewall", "incident", "vulnerability"]):
        tags.append("security")
    if any(t in toks for t in ["server", "network", "dns", "dhcp", "switch", "router", "storage", "backup"]):
        tags.append("it_ops")
    if any(t in toks for t in ["project", "roadmap", "milestone", "timeline", "jira", "program"]):
        tags.append("projects")
    if any(t in toks for t in ["meeting", "minutes", "agenda"]) or ("action" in toks and "items" in toks):
        tags.append("meetings")
    if any(t in toks for t in ["vendor", "supplier", "procurement", "rfp", "quote"]):
        tags.append("vendors")
    if any(t in toks for t in ["customer", "client", "opportunity", "pipeline", "crm", "salesforce"]) or ("account" in toks and "executive" in toks):
        tags.append("sales")
    if any(t in toks for t in ["marketing", "campaign", "brand", "seo", "newsletter", "leads"]) or ("content" in toks and "calendar" in toks):
        tags.append("marketing")
    if any(t in toks for t in ["support", "ticket", "case", "sla"]) or ("incident" in toks and "response" in toks) or ("knowledge" in toks and "article" in toks):
        tags.append("customer_support")
    if any(t in toks for t in ["engineering", "architecture", "spec", "requirements", "sdlc"]) or ("design" in toks and "doc" in toks) or ("release" in toks and "notes" in toks):
        tags.append("engineering")
    if any(t in toks for t in ["product", "prd", "backlog", "roadmap", "mvp"]) or ("user" in toks and "story" in toks):
        tags.append("product")
    if any(t in toks for t in ["research", "experiment", "study", "analysis", "prototype"]):
        tags.append("research")
    if any(t in toks for t in ["operations", "runbook", "oncall", "postmortem", "kpi", "okr"]) or "on call" in blob:
        tags.append("operations")
    if any(t in toks for t in ["inventory", "warehouse", "shipment", "logistics", "manufacturing", "bom"]) or ("supply" in toks and "chain" in toks):
        tags.append("supply_chain")

    # stable order + dedupe
    order = [
        "security",
        "compliance",
        "legal",
        "finance",
        "hr",
        "it_ops",
        "sales",
        "marketing",
        "customer_support",
        "engineering",
        "product",
        "research",
        "operations",
        "supply_chain",
        "projects",
        "meetings",
        "governance",
        "vendors",
    ]
    sset = set(tags)
    return [t for t in order if t in sset]


def tokenize_for_keywords(text: str) -> List[str]:
    text = (text or "").lower()
    toks = re.split(r"[^a-z0-9]+", text)
    out = []
    for t in toks:
        if not t:
            continue
        if len(t) < 3:
            continue
        if t.isdigit():
            continue
        if t in STOPWORDS:
            continue
        out.append(t)
    return out


def tokenize_for_rules(text: str) -> List[str]:
    """
    Tokenizer used for rule matching. Keeps common abbreviations (eg 'hr', 'po')
    and does not apply STOPWORDS filtering (rules should be selective).
    """
    text = (text or "").lower()
    toks = re.split(r"[^a-z0-9]+", text)
    out = []
    for t in toks:
        if not t:
            continue
        if len(t) < 2:
            continue
        if t.isdigit():
            continue
        out.append(t)
    return out


def path_matches_any_regex(path: str, patterns: Sequence[re.Pattern]) -> bool:
    pp = norm_path(path)
    for rx in patterns:
        try:
            if rx.search(pp):
                return True
        except Exception:
            continue
    return False


def resolve_semantic_thresholds(
    *,
    path: str,
    first_bucket: str,
    sub_bucket: str,
    default_score_threshold: int,
    default_margin_threshold: int,
    overrides: Sequence[SemanticThresholdOverride],
) -> Tuple[int, int, str]:
    """
    Resolve score/margin thresholds using most-specific matching override.
    Specificity precedence: sub_bucket > first_bucket > path_prefix > first_bucket_regex > path_regex.
    """
    if not overrides:
        return int(default_score_threshold), int(default_margin_threshold), "default"

    rel = norm_path(path).strip("/")
    fb = slug(first_bucket)
    sb = "/".join(slug(x) for x in norm_path(sub_bucket).split("/") if x)

    best: Optional[Tuple[int, int, SemanticThresholdOverride]] = None
    for idx, ov in enumerate(overrides):
        if ov.first_bucket and ov.first_bucket != fb:
            continue
        if ov.sub_bucket and ov.sub_bucket != sb:
            continue
        if ov.first_bucket_regex is not None:
            try:
                if not ov.first_bucket_regex.search(fb):
                    continue
            except Exception:
                continue
        if ov.path_prefix:
            pref = ov.path_prefix.strip("/")
            if not pref:
                pass
            elif not (rel == pref or rel.startswith(pref + "/")):
                continue
        if ov.path_regex is not None:
            try:
                if not ov.path_regex.search(rel):
                    continue
            except Exception:
                continue

        specificity = 0
        if ov.sub_bucket:
            specificity += 16
        if ov.first_bucket:
            specificity += 8
        if ov.path_prefix:
            specificity += 4
        if ov.first_bucket_regex is not None:
            specificity += 2
        if ov.path_regex is not None:
            specificity += 1

        cand = (specificity, -idx, ov)
        if best is None or cand > best:
            best = cand

    if best is None:
        return int(default_score_threshold), int(default_margin_threshold), "default"

    ov = best[2]
    score = int(default_score_threshold if ov.score_threshold is None else ov.score_threshold)
    margin = int(default_margin_threshold if ov.margin_threshold is None else ov.margin_threshold)
    return max(0, score), max(0, margin), ov.id


def _semantic_domain_scores(path: str, core: Dict[str, str], sample: str, rules: RulesContext) -> List[Tuple[str, int]]:
    meta = " ".join([core.get("title", ""), core.get("subject", ""), core.get("creator", "")])
    path_toks = set(tokenize_for_rules(path))
    meta_toks = set(tokenize_for_rules(meta))
    sample_toks = set(tokenize_for_rules((sample or "")[:2000]))
    blob_norm = _normalize_phrase(" ".join([path, meta, (sample or "")[:2000]]))

    scored: List[Tuple[str, int]] = []
    for dr in rules.domains:
        score = 0
        if dr.keywords:
            score += len(dr.keywords & path_toks) * int(dr.weights.get("path", 3))
            score += len(dr.keywords & meta_toks) * int(dr.weights.get("meta", 2))
            score += len(dr.keywords & sample_toks) * int(dr.weights.get("sample", 1))
        if dr.phrases:
            score += sum(1 for ph in dr.phrases if ph and ph in blob_norm) * int(dr.weights.get("phrase", 4))
        if dr.regex:
            for rx in dr.regex:
                try:
                    if rx.search(blob_norm):
                        score += int(dr.weights.get("regex", 4))
                except Exception:
                    continue
        if dr.negative_keywords:
            score -= len(dr.negative_keywords & (path_toks | meta_toks | sample_toks)) * int(dr.weights.get("negative", 3))
        if dr.negative_phrases:
            score -= sum(1 for ph in dr.negative_phrases if ph and ph in blob_norm) * int(dr.weights.get("negative", 3))
        scored.append((dr.id, int(score)))

    if not scored:
        dd = rules.default_domain or "general"
        scored = [(dd, 0)]
    scored = sorted(scored, key=lambda x: (-x[1], x[0]))
    return scored


def _itops_security_tiebreak_domain(
    path: str,
    core: Dict[str, str],
    sample: str,
    scored: Sequence[Tuple[str, int]],
    *,
    max_gap: int = DEFAULT_ITOPS_SECURITY_TIE_MAX_GAP,
) -> str:
    """
    Deterministic domain tie-breaker:
    if it_ops wins by a small gap and strong security/compliance cues are present,
    prefer security/compliance for better domain correctness.
    """
    if not scored:
        return ""
    top_id, top_score = scored[0]
    if top_id != "it_ops":
        return ""

    score_map = {did: int(sc) for did, sc in scored}
    sec_score = int(score_map.get("security", 0))
    cmp_score = int(score_map.get("compliance", 0))
    challenger_score = max(sec_score, cmp_score)
    if challenger_score <= 0:
        return ""

    gap = int(top_score) - int(challenger_score)
    if gap < 0 or gap > int(max_gap):
        return ""

    meta = " ".join([core.get("title", ""), core.get("subject", ""), core.get("creator", "")])
    tokset = set(tokenize_for_rules(" ".join([path, meta, (sample or "")[:2000]])))
    blob_norm = _normalize_phrase(" ".join([path, meta, (sample or "")[:2000]]))

    has_hint_kw = any(k in tokset for k in ITOPS_TIEBREAKER_SECURITY_HINT_KEYWORDS)
    has_hint_phrase = any(ph and ph in blob_norm for ph in ITOPS_TIEBREAKER_SECURITY_HINT_PHRASES)
    if not (has_hint_kw or has_hint_phrase):
        return ""

    if sec_score > cmp_score:
        return "security"
    if cmp_score > sec_score:
        return "compliance"

    security_pref = {"cyber", "incident", "vulnerability", "threat", "forensics", "mfa", "multifactor", "vpn", "iam"}
    compliance_pref = {"compliance", "policy", "controls", "control", "risk", "audit", "governance", "fisma", "nist", "authorization", "accreditation"}
    sec_hits = sum(1 for t in security_pref if t in tokset)
    cmp_hits = sum(1 for t in compliance_pref if t in tokset)
    return "compliance" if cmp_hits >= sec_hits else "security"


def classify_semantic_domain_detail(
    path: str,
    core: Dict[str, str],
    sample: str,
    rules: RulesContext,
    *,
    score_threshold: int,
    margin_threshold: int,
) -> SemanticDecision:
    """
    Deterministic rules-based domain classifier with confidence/abstain signal.
    """
    scored = _semantic_domain_scores(path, core, sample, rules)
    score_map = {did: int(sc) for did, sc in scored}

    tiebreak_domain = _itops_security_tiebreak_domain(path, core, sample, scored)
    if tiebreak_domain:
        top1_id = tiebreak_domain
        top1_score = int(score_map.get(top1_id, 0))
        top2_pool = [int(sc) for did, sc in scored if did not in {top1_id, "it_ops"}]
        top2_score = max(top2_pool) if top2_pool else 0
        scored_for_candidates = [(top1_id, top1_score)] + [(did, int(sc)) for did, sc in scored if did != top1_id]
    else:
        top1_id, top1_score = scored[0]
        top2_score = scored[1][1] if len(scored) > 1 else 0
        scored_for_candidates = [(did, int(sc)) for did, sc in scored]

    margin = int(top1_score - top2_score)

    abstain_reasons: List[str] = []
    if int(top1_score) < int(score_threshold):
        abstain_reasons.append("score_below_threshold")
    if int(margin) < int(margin_threshold):
        abstain_reasons.append("margin_below_threshold")

    default_domain = rules.default_domain or "general"
    if abstain_reasons:
        decision = "abstained"
        domain = default_domain
        reason = ",".join(abstain_reasons)
    else:
        decision = "routed"
        domain = top1_id
        reason = ""

    top_candidates = [{"domain": did, "score": int(sc)} for did, sc in scored_for_candidates[:5]]
    return SemanticDecision(
        domain=domain,
        score=int(top1_score),
        margin=int(margin),
        decision=decision,
        abstain_reason=reason,
        top_candidates=top_candidates,
    )


def classify_semantic_domain(path: str, core: Dict[str, str], sample: str, rules: RulesContext) -> Tuple[str, int]:
    """
    Back-compat helper returning only (domain, score).
    """
    d = classify_semantic_domain_detail(
        path,
        core,
        sample,
        rules,
        score_threshold=DEFAULT_DOMAIN_SCORE_THRESHOLD,
        margin_threshold=DEFAULT_DOMAIN_MARGIN_THRESHOLD,
    )
    return d.domain, int(d.score)


def default_artifact_type_for_doc_type(doc_type: str) -> str:
    dt = (doc_type or "").lower()
    if dt == "email":
        return "email"
    if dt == "powerpoint":
        return "deck"
    if dt == "spreadsheet":
        return "dataset"
    if dt == "visio":
        return "diagram"
    if dt == "html":
        return "web_page"
    if dt == "image":
        return "photo"
    return "other"


def classify_artifact_type(
    path: str,
    *,
    doc_type: str,
    ext: str,
    core: Dict[str, str],
    sample: str,
    rules: RulesContext,
) -> str:
    """
    Apply config-driven artifact_type rules first; fall back to built-in heuristic.
    """
    meta = " ".join([core.get("title", ""), core.get("subject", "")])
    path_toks = set(tokenize_for_rules(path))
    meta_toks = set(tokenize_for_rules(meta))
    sample_toks = set(tokenize_for_rules((sample or "")[:2000]))
    blob_norm = _normalize_phrase(" ".join([path, meta, (sample or "")[:2000]]))

    dt = (doc_type or "").lower()
    ex = (ext or "").lower().lstrip(".")

    best_id = ""
    best_score = 0
    threshold = 2
    for r in rules.artifact_types:
        if r.doc_types and dt not in r.doc_types:
            continue
        if r.exts and ex not in r.exts:
            continue
        score = 0
        if r.keywords:
            score += len(r.keywords & path_toks) * 3
            score += len(r.keywords & meta_toks) * 2
            score += len(r.keywords & sample_toks) * 2
        if r.phrases:
            score += sum(1 for ph in r.phrases if ph and ph in blob_norm) * 4
        if r.regex:
            for rx in r.regex:
                try:
                    if rx.search(blob_norm):
                        score += 4
                except Exception:
                    continue
        if score > best_score or (score == best_score and score > 0 and r.id < best_id):
            best_id = r.id
            best_score = score

    if best_score >= threshold and best_id:
        return best_id

    # Use built-in heuristic, then doc_type default if needed.
    ht = artifact_type(path, doc_type, sample)
    return ht or default_artifact_type_for_doc_type(doc_type)


def sensitivity_flags_with_rules(path: str, sample: str, *, doc_type: str, ext: str, rules: RulesContext) -> List[str]:
    flags = set(sensitivity_flags(path, sample))
    dt = (doc_type or "").lower()
    ex = (ext or "").lower().lstrip(".")
    if dt == "email" or ex in {"msg", "eml"}:
        flags.add("email")

    path_toks = set(tokenize_for_rules(path))
    sample_toks = set(tokenize_for_rules((sample or "")[:3000]))
    for r in rules.sensitivity_flags:
        if r.id not in set(SENSITIVITY_FLAGS_ENUM):
            continue
        hit = False
        if r.path_keywords and (r.path_keywords & path_toks):
            hit = True
        if not hit and r.content_keywords and (r.content_keywords & sample_toks):
            hit = True
        if not hit and r.regex:
            for rx in r.regex:
                try:
                    if rx.search(sample or ""):
                        hit = True
                        break
                except Exception:
                    continue
        if hit:
            flags.add(r.id)

    return sorted(flags)


def sensitivity_level_from_rules(flags: Sequence[str], rules: RulesContext) -> str:
    fset = set(flags)
    for level in ["critical", "high", "medium"]:
        want = set(rules.level_map.get(level, []))
        if want & fset:
            return level
    return "normal"


def looks_like_secret_token(t: str) -> bool:
    """
    Heuristic filter for tokens that should not be promoted into router keywords.
    """
    t = (t or "").strip()
    if not t:
        return False
    if len(t) >= 32 and re.fullmatch(r"[0-9a-f]+", t):
        return True
    if len(t) >= 20:
        has_alpha = any("a" <= c <= "z" for c in t)
        has_digit = any("0" <= c <= "9" for c in t)
        if has_alpha and has_digit:
            digit_ratio = sum(1 for c in t if c.isdigit()) / max(1, len(t))
            if digit_ratio >= 0.30:
                return True
    if t.startswith("akia") and len(t) >= 20:
        return True
    return False


def top_keywords(docs: Sequence["DocRecord"], *, max_keywords: int = 20, keywords_source: str = "meta_only") -> List[str]:
    c = Counter()
    for d in docs:
        parts: List[str] = []
        ks = (keywords_source or "meta_only").lower()
        if ks in {"path_only", "meta_only", "meta+sample"}:
            parts.append(d.path)
        if ks in {"meta_only", "meta+sample"}:
            parts.append(d.openxml_core.get("title", ""))
            parts.append(d.openxml_core.get("subject", ""))
            parts.append(d.openxml_core.get("creator", ""))
        if ks == "meta+sample":
            parts.append(d.sample_text[:1200])

        blob = " ".join(parts)
        for t in tokenize_for_keywords(blob):
            if looks_like_secret_token(t):
                continue
            c[t] += 1
    # prefer stable ordering: (-count, token)
    ranked = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in ranked[:max_keywords]]


def _stable_unique(items: Sequence[str], *, max_items: int = 12) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for x in items:
        t = ascii_sanitize((x or "").strip())
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= int(max_items):
            break
    return out


def _deepcopy_json_obj(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def _extract_json_object_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    # Fast-path: already valid JSON object.
    try:
        x = json.loads(raw)
        if isinstance(x, dict):
            return raw
    except Exception:
        pass

    # Fenced code block.
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # First balanced JSON object scan.
    start = raw.find("{")
    if start < 0:
        return ""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1].strip()
    return ""


def _sanitize_name_list(items: Any, *, lower: bool = False, max_items: int = 128) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return out
    for it in items:
        s = ascii_sanitize(str(it or "").strip())
        if not s:
            continue
        if lower:
            s = s.lower()
        if len(s) > 120:
            s = s[:120]
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= int(max_items):
            break
    return out


def _sanitize_keyword_list(items: Any, *, max_items: int = 128) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return out
    for it in items:
        s = ascii_sanitize(str(it or "").strip()).lower()
        s = re.sub(r"[^a-z0-9_\-]+", " ", s).strip()
        if not s:
            continue
        if len(s) < 2 or len(s) > 48:
            continue
        k = s
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= int(max_items):
            break
    return out


def _sanitize_phrase_list(items: Any, *, max_items: int = 96) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return out
    for it in items:
        s = _normalize_phrase(str(it or ""))
        if not s:
            continue
        if len(s) < 4 or len(s) > 120:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= int(max_items):
            break
    return out


def build_calibrator_digest(
    docs: Sequence["DocRecord"],
    *,
    max_buckets: int,
    samples_per_bucket: int,
    max_snippet_chars: int,
) -> Dict[str, Any]:
    by_first: Dict[str, List["DocRecord"]] = defaultdict(list)
    for d in docs:
        by_first[d.first_bucket].append(d)

    ordered = sorted(by_first.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    chosen = ordered[: max(1, int(max_buckets))]

    buckets = []
    for fb, items in chosen:
        items_sorted = sorted(items, key=lambda d: d.path.lower())
        samps = items_sorted[: max(1, int(samples_per_bucket))]
        dom_counts = Counter(d.semantic_domain for d in items if d.semantic_domain)
        dec_counts = Counter(d.semantic_decision for d in items if d.semantic_decision)
        art_counts = Counter(d.artifact_type for d in items if d.artifact_type)
        ext_counts = Counter(d.ext for d in items if d.ext)

        sample_docs = []
        for d in samps:
            sample_docs.append(
                {
                    "path": d.path,
                    "ext": d.ext,
                    "doc_type": d.doc_type,
                    "artifact_type": d.artifact_type,
                    "semantic_domain": d.semantic_domain,
                    "semantic_decision": d.semantic_decision,
                    "snippet": ascii_sanitize((d.sample_text or "")[: int(max_snippet_chars)]),
                }
            )

        buckets.append(
            {
                "first_bucket": fb,
                "files": len(items),
                "semantic_domain_counts": dict(sorted(dom_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                "semantic_decision_counts": dict(sorted(dec_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                "artifact_type_counts": dict(sorted(art_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                "ext_counts": dict(sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                "keywords": top_keywords(items, max_keywords=25, keywords_source="meta+sample"),
                "sample_docs": sample_docs,
            }
        )

    ext_counts_all = Counter(d.ext for d in docs if d.ext)
    dom_counts_all = Counter(d.semantic_domain for d in docs if d.semantic_domain)
    decision_counts_all = Counter(d.semantic_decision for d in docs if d.semantic_decision)
    art_counts_all = Counter(d.artifact_type for d in docs if d.artifact_type)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "summary": {
            "documents": len(docs),
            "buckets_sampled": len(buckets),
            "semantic_domain_counts": dict(sorted(dom_counts_all.items(), key=lambda kv: (-kv[1], kv[0]))),
            "semantic_decision_counts": dict(sorted(decision_counts_all.items(), key=lambda kv: (-kv[1], kv[0]))),
            "artifact_type_counts": dict(sorted(art_counts_all.items(), key=lambda kv: (-kv[1], kv[0]))),
            "ext_counts": dict(sorted(ext_counts_all.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        "buckets": buckets,
    }


def _calibrator_patch_schema_text() -> str:
    return (
        "{\n"
        '  "notes": [string],\n'
        '  "ingest": {"exclude_dirs":[string], "exclude_exts":[string], "exclude_basenames":[string], "exclude_path_regex":[{"pattern":string,"flags":"i|m|s"}]},\n'
        '  "semantic_routing": {"default_thresholds":{"score_threshold":int,"margin_threshold":int},"threshold_overrides":[{"id":string,"first_bucket":string,"sub_bucket":string,"first_bucket_regex":{"pattern":string,"flags":"i|m|s"},"path_prefix":string,"path_regex":{"pattern":string,"flags":"i|m|s"},"score_threshold":int,"margin_threshold":int}]},\n'
        '  "domains": [{"id":string,"add_keywords":[string],"add_phrases":[string],"add_negative_keywords":[string],"add_negative_phrases":[string]}],\n'
        '  "artifact_types": [{"id":string,"add_keywords":[string],"add_phrases":[string],"add_doc_types":[string],"add_exts":[string]}]\n'
        "}"
    )


def _calibrator_messages(payload: Dict[str, Any], rules: RulesContext) -> List[Dict[str, str]]:
    domain_ids = sorted(set(rules.domain_ids))
    artifact_ids = sorted(set(rules.artifact_type_ids))
    system = (
        "You are Corpus Calibrator. Output ONLY one JSON object.\n"
        "Goal: improve routing precision while preserving routed coverage.\n"
        "Do not invent new domain IDs or artifact_type IDs.\n"
        "Semantic score is a low-range integer rules-hit count (not probability).\n"
        "Prefer keyword/phrase updates and targeted threshold_overrides.\n"
        "Leave semantic_routing.default_thresholds as {} unless broad evidence demands change.\n"
        f"Current default thresholds: score={int(rules.semantic_default_score_threshold)}, margin={int(rules.semantic_default_margin_threshold)}\n"
        "Keep patch small and deterministic.\n"
        "Allowed domain IDs: "
        + json.dumps(domain_ids)
        + "\nAllowed artifact_type IDs: "
        + json.dumps(artifact_ids)
        + "\nReturn this schema exactly:\n"
        + _calibrator_patch_schema_text()
        + "\nNo markdown. No extra prose."
    )
    user = json.dumps(payload, ensure_ascii=True)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _openrouter_chat_completion(
    *,
    api_key: str,
    model: str,
    base_url: str,
    messages: List[Dict[str, str]],
    timeout_sec: int,
    temperature: float,
    max_output_tokens: int,
    insecure_tls: bool,
    strict_json: bool,
) -> Tuple[str, Dict[str, Any]]:
    req_obj = {
        "model": str(model),
        "temperature": float(temperature),
        "messages": messages,
        "max_tokens": int(max_output_tokens),
        # Force content-only completion for providers that expose a reasoning channel.
        "reasoning": {"enabled": False, "exclude": True},
        "include_reasoning": False,
    }
    if bool(strict_json):
        req_obj["response_format"] = {"type": "json_object"}
    body = json.dumps(req_obj).encode("utf-8")
    req = urllib.request.Request(
        str(base_url),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://routerkit.local",
            "X-Title": "Corpus RouterKit Corpus Calibrator",
        },
    )
    ctx = ssl._create_unverified_context() if bool(insecure_tls) else None
    with urllib.request.urlopen(req, timeout=max(5, int(timeout_sec)), context=ctx) as resp:
        raw = resp.read()
    obj = json.loads(raw.decode("utf-8", errors="replace"))
    choices = obj.get("choices") if isinstance(obj, dict) else []
    msg = {}
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            msg = c0.get("message") if isinstance(c0.get("message"), dict) else {}

    # Only parse assistant output content; ignore any provider-specific reasoning fields.
    content = msg.get("content", "")
    if isinstance(content, list):
        parts: List[str] = []
        for it in content:
            if isinstance(it, dict):
                t = it.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(it, str):
                parts.append(it)
        content = "\n".join(parts)
    content_s = content if isinstance(content, str) else str(content)
    return content_s, obj if isinstance(obj, dict) else {}


def sanitize_calibrator_patch(obj: Any, rules: RulesContext) -> Dict[str, Any]:
    patch: Dict[str, Any] = {
        "notes": [],
        "ingest": {"exclude_dirs": [], "exclude_exts": [], "exclude_basenames": [], "exclude_path_regex": []},
        "semantic_routing": {"default_thresholds": {}, "threshold_overrides": []},
        "domains": [],
        "artifact_types": [],
    }
    if not isinstance(obj, dict):
        return patch

    patch["notes"] = _sanitize_name_list(obj.get("notes"), max_items=32)

    ingest = obj.get("ingest")
    if isinstance(ingest, dict):
        patch["ingest"]["exclude_dirs"] = _sanitize_name_list(ingest.get("exclude_dirs"), max_items=96)
        patch["ingest"]["exclude_exts"] = _sanitize_name_list(ingest.get("exclude_exts"), lower=True, max_items=96)
        patch["ingest"]["exclude_basenames"] = _sanitize_name_list(ingest.get("exclude_basenames"), lower=True, max_items=96)
        xpr = []
        for it in ingest.get("exclude_path_regex") or []:
            if isinstance(it, str):
                pat = it.strip()
                fl = "i"
            elif isinstance(it, dict):
                pat = str(it.get("pattern") or "").strip()
                fl = str(it.get("flags") or "i").strip()
            else:
                continue
            if not pat:
                continue
            xpr.append({"pattern": pat, "flags": fl or "i"})
        patch["ingest"]["exclude_path_regex"] = xpr[:48]

    sem = obj.get("semantic_routing")
    if isinstance(sem, dict):
        defs = sem.get("default_thresholds")
        if isinstance(defs, dict):
            st = _int_or_none(defs.get("score_threshold"))
            mt = _int_or_none(defs.get("margin_threshold"))
            d = {}
            if st is not None:
                d["score_threshold"] = max(0, int(st))
            if mt is not None:
                d["margin_threshold"] = max(0, int(mt))
            patch["semantic_routing"]["default_thresholds"] = d
        ovs = []
        for i, it in enumerate(sem.get("threshold_overrides") or []):
            if not isinstance(it, dict):
                continue
            oid = slug(str(it.get("id") or f"cal_{i+1}"))
            fb_raw = str(it.get("first_bucket") or "").strip()
            sb_raw = str(it.get("sub_bucket") or "").strip()
            fb = slug(fb_raw) if fb_raw else ""
            sb = "/".join(slug(x) for x in norm_path(sb_raw).split("/") if x)
            pfx = norm_path(str(it.get("path_prefix") or "")).strip("/")
            fbr = it.get("first_bucket_regex")
            if isinstance(fbr, str):
                fbr = {"pattern": fbr, "flags": "i"}
            prx = it.get("path_regex")
            if isinstance(prx, str):
                prx = {"pattern": prx, "flags": "i"}
            st = _int_or_none(it.get("score_threshold"))
            mt = _int_or_none(it.get("margin_threshold"))
            ov = {
                "id": oid or f"cal_{i+1}",
                "first_bucket": fb,
                "sub_bucket": sb,
                "path_prefix": pfx,
                "score_threshold": max(0, int(st)) if st is not None else None,
                "margin_threshold": max(0, int(mt)) if mt is not None else None,
            }
            if isinstance(fbr, dict) and str(fbr.get("pattern") or "").strip():
                ov["first_bucket_regex"] = {"pattern": str(fbr.get("pattern") or "").strip(), "flags": str(fbr.get("flags") or "i")}
            if isinstance(prx, dict) and str(prx.get("pattern") or "").strip():
                ov["path_regex"] = {"pattern": str(prx.get("pattern") or "").strip(), "flags": str(prx.get("flags") or "i")}
            if not any([ov["first_bucket"], ov["sub_bucket"], ov["path_prefix"], ov.get("first_bucket_regex"), ov.get("path_regex")]):
                continue
            ovs.append(ov)
        patch["semantic_routing"]["threshold_overrides"] = ovs[:96]

    domain_ids = set(rules.domain_ids)
    dom_updates = []
    for it in obj.get("domains") or []:
        if not isinstance(it, dict):
            continue
        did = slug(str(it.get("id") or ""))
        if not did or did not in domain_ids:
            continue
        dom_updates.append(
            {
                "id": did,
                "add_keywords": _sanitize_keyword_list(it.get("add_keywords"), max_items=80),
                "add_phrases": _sanitize_phrase_list(it.get("add_phrases"), max_items=60),
                "add_negative_keywords": _sanitize_keyword_list(it.get("add_negative_keywords"), max_items=80),
                "add_negative_phrases": _sanitize_phrase_list(it.get("add_negative_phrases"), max_items=60),
            }
        )
    patch["domains"] = dom_updates[: len(domain_ids)]

    art_ids = set(rules.artifact_type_ids)
    art_updates = []
    for it in obj.get("artifact_types") or []:
        if not isinstance(it, dict):
            continue
        aid = slug(str(it.get("id") or ""))
        if not aid or aid not in art_ids:
            continue
        art_updates.append(
            {
                "id": aid,
                "add_keywords": _sanitize_keyword_list(it.get("add_keywords"), max_items=80),
                "add_phrases": _sanitize_phrase_list(it.get("add_phrases"), max_items=60),
                "add_doc_types": _sanitize_name_list(it.get("add_doc_types"), lower=True, max_items=32),
                "add_exts": _sanitize_name_list(it.get("add_exts"), lower=True, max_items=32),
            }
        )
    patch["artifact_types"] = art_updates[: len(art_ids)]
    return patch


def apply_calibrator_patch(base_rules: Dict[str, Any], patch: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cand = _deepcopy_json_obj(base_rules if isinstance(base_rules, dict) else {})
    if not isinstance(cand.get("domains"), list):
        cand["domains"] = []
    if not isinstance(cand.get("artifact_types"), list):
        cand["artifact_types"] = []
    if not isinstance(cand.get("ingest"), dict):
        cand["ingest"] = {}
    if not isinstance(cand.get("semantic_routing"), dict):
        cand["semantic_routing"] = {}

    merge_report = {
        "domains_updated": 0,
        "artifact_types_updated": 0,
        "ingest_fields_updated": 0,
        "threshold_overrides_added": 0,
    }

    ingest_patch = patch.get("ingest") if isinstance(patch, dict) else {}
    if isinstance(ingest_patch, dict):
        for k in ["exclude_dirs", "exclude_exts", "exclude_basenames"]:
            cur = cand["ingest"].get(k)
            if not isinstance(cur, list):
                cur = []
            add = ingest_patch.get(k)
            if isinstance(add, list) and add:
                merged = sorted({str(x) for x in cur} | {str(x) for x in add})
                cand["ingest"][k] = merged
                merge_report["ingest_fields_updated"] += 1
        cur_rx = cand["ingest"].get("exclude_path_regex")
        if not isinstance(cur_rx, list):
            cur_rx = []
        add_rx = ingest_patch.get("exclude_path_regex")
        if isinstance(add_rx, list) and add_rx:
            seen = {json.dumps(x, sort_keys=True) for x in cur_rx if isinstance(x, dict)}
            merged_rx = list(cur_rx)
            for it in add_rx:
                if not isinstance(it, dict):
                    continue
                key = json.dumps({"pattern": str(it.get("pattern") or ""), "flags": str(it.get("flags") or "")}, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                merged_rx.append({"pattern": str(it.get("pattern") or ""), "flags": str(it.get("flags") or "i")})
            cand["ingest"]["exclude_path_regex"] = merged_rx
            merge_report["ingest_fields_updated"] += 1

    sem_patch = patch.get("semantic_routing") if isinstance(patch, dict) else {}
    if isinstance(sem_patch, dict):
        defs = sem_patch.get("default_thresholds")
        if isinstance(defs, dict) and defs:
            cur_defs = cand["semantic_routing"].get("default_thresholds")
            if not isinstance(cur_defs, dict):
                cur_defs = {}
            if defs.get("score_threshold") is not None:
                cur_defs["score_threshold"] = int(defs["score_threshold"])
            if defs.get("margin_threshold") is not None:
                cur_defs["margin_threshold"] = int(defs["margin_threshold"])
            cand["semantic_routing"]["default_thresholds"] = cur_defs
        cur_ov = cand["semantic_routing"].get("threshold_overrides")
        if not isinstance(cur_ov, list):
            cur_ov = []
        add_ov = sem_patch.get("threshold_overrides")
        if isinstance(add_ov, list) and add_ov:
            seen = {str(it.get("id")) for it in cur_ov if isinstance(it, dict) and it.get("id")}
            for ov in add_ov:
                if not isinstance(ov, dict):
                    continue
                oid = str(ov.get("id") or "")
                if oid and oid in seen:
                    continue
                if oid:
                    seen.add(oid)
                cur_ov.append(ov)
                merge_report["threshold_overrides_added"] += 1
            cand["semantic_routing"]["threshold_overrides"] = cur_ov

    dom_map: Dict[str, Dict[str, Any]] = {}
    for d in cand.get("domains", []):
        if isinstance(d, dict):
            did = slug(str(d.get("id") or ""))
            if did:
                dom_map[did] = d
    for du in patch.get("domains", []):
        if not isinstance(du, dict):
            continue
        did = slug(str(du.get("id") or ""))
        if not did or did not in dom_map:
            continue
        tgt = dom_map[did]
        for src_k, tgt_k in [
            ("add_keywords", "keywords"),
            ("add_phrases", "phrases"),
            ("add_negative_keywords", "negative_keywords"),
            ("add_negative_phrases", "negative_phrases"),
        ]:
            cur = tgt.get(tgt_k)
            if not isinstance(cur, list):
                cur = []
            add = du.get(src_k)
            if isinstance(add, list) and add:
                tgt[tgt_k] = sorted({str(x) for x in cur} | {str(x) for x in add})
        merge_report["domains_updated"] += 1

    art_map: Dict[str, Dict[str, Any]] = {}
    for a in cand.get("artifact_types", []):
        if isinstance(a, dict):
            aid = slug(str(a.get("id") or ""))
            if aid:
                art_map[aid] = a
    for au in patch.get("artifact_types", []):
        if not isinstance(au, dict):
            continue
        aid = slug(str(au.get("id") or ""))
        if not aid or aid not in art_map:
            continue
        tgt = art_map[aid]
        for src_k, tgt_k in [
            ("add_keywords", "keywords"),
            ("add_phrases", "phrases"),
            ("add_doc_types", "doc_types"),
            ("add_exts", "exts"),
        ]:
            cur = tgt.get(tgt_k)
            if not isinstance(cur, list):
                cur = []
            add = au.get(src_k)
            if isinstance(add, list) and add:
                tgt[tgt_k] = sorted({str(x) for x in cur} | {str(x) for x in add})
        merge_report["artifact_types_updated"] += 1

    base_ver = _int_or_none(cand.get("rules_version")) or 1
    cand["rules_version"] = int(base_ver) + 1
    cand["_calibrator_meta"] = {
        "generated_at": now_iso(),
        "notes": patch.get("notes", []),
        "merge_report": merge_report,
    }
    return cand, merge_report


def _select_calibrator_docs(
    docs: Sequence["DocRecord"],
    *,
    selection_mode: str,
    low_score_threshold: int,
    low_margin_threshold: int,
) -> Tuple[List["DocRecord"], Dict[str, Any]]:
    mode = str(selection_mode or DEFAULT_CALIBRATOR_DOC_SELECTION).strip().lower()
    if mode not in CALIBRATOR_DOC_SELECTION_ENUM:
        mode = DEFAULT_CALIBRATOR_DOC_SELECTION

    if mode == "all":
        selected = list(docs)
        return selected, {
            "selection_mode": mode,
            "source_documents": int(len(docs)),
            "selected_documents": int(len(selected)),
            "low_score_threshold": int(low_score_threshold),
            "low_margin_threshold": int(low_margin_threshold),
        }

    selected: List["DocRecord"] = []
    for d in docs:
        if d.semantic_decision == "abstained":
            selected.append(d)
            continue
        if int(d.semantic_domain_score) <= int(low_score_threshold):
            selected.append(d)
            continue
        if int(d.semantic_domain_margin) <= int(low_margin_threshold):
            selected.append(d)
            continue

    if not selected:
        selected = list(docs)

    return selected, {
        "selection_mode": mode,
        "source_documents": int(len(docs)),
        "selected_documents": int(len(selected)),
        "low_score_threshold": int(low_score_threshold),
        "low_margin_threshold": int(low_margin_threshold),
    }


def run_corpus_calibrator(
    *,
    docs: Sequence["DocRecord"],
    rules: RulesContext,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    api_key_env: str,
    dry_run: bool,
    timeout_sec: int,
    temperature: float,
    max_output_tokens: int,
    insecure_tls: bool,
    max_buckets: int,
    samples_per_bucket: int,
    max_snippet_chars: int,
    doc_selection_mode: str,
    low_score_threshold: int,
    low_margin_threshold: int,
) -> Dict[str, Any]:
    selected_docs, selection_meta = _select_calibrator_docs(
        docs,
        selection_mode=doc_selection_mode,
        low_score_threshold=low_score_threshold,
        low_margin_threshold=low_margin_threshold,
    )
    digest = build_calibrator_digest(
        selected_docs,
        max_buckets=max_buckets,
        samples_per_bucket=samples_per_bucket,
        max_snippet_chars=max_snippet_chars,
    )
    digest_json = json.dumps(digest, sort_keys=True)
    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "feature": "corpus_calibrator",
        "provider": provider,
        "model": model,
        "status": "pending",
        "digest_sha256": hashlib.sha256(digest_json.encode("utf-8")).hexdigest(),
        "digest": digest,
        "patch": {},
        "merge_report": {},
        "candidate_rules": None,
        "request": {
            "base_url": base_url,
            "api_key_env": api_key_env,
            "timeout_sec": int(timeout_sec),
            "insecure_tls": bool(insecure_tls),
            "temperature": float(temperature),
            "max_output_tokens": int(max_output_tokens),
            "max_buckets": int(max_buckets),
            "samples_per_bucket": int(samples_per_bucket),
            "max_snippet_chars": int(max_snippet_chars),
            "dry_run": bool(dry_run),
            "doc_selection_mode": str(doc_selection_mode),
            "low_score_threshold": int(low_score_threshold),
            "low_margin_threshold": int(low_margin_threshold),
            "selection": selection_meta,
        },
    }

    if bool(dry_run):
        out["status"] = "dry_run"
        return out

    if provider != "openrouter":
        out["status"] = "error"
        out["error"] = f"Unsupported provider: {provider}"
        return out

    key = (api_key or "").strip()
    if not key:
        key = os.environ.get(str(api_key_env or DEFAULT_CALIBRATOR_API_KEY_ENV), "").strip()
    if not key:
        out["status"] = "missing_api_key"
        out["error"] = f"Missing API key. Set --calibrator-api-key or env {api_key_env}."
        return out

    msgs = _calibrator_messages(digest, rules)
    out["request"]["prompt_sha256"] = hashlib.sha256(
        json.dumps(msgs, sort_keys=True).encode("utf-8")
    ).hexdigest()
    try:
        content, raw_resp = _openrouter_chat_completion(
            api_key=key,
            model=model,
            base_url=base_url,
            messages=msgs,
            timeout_sec=int(timeout_sec),
            temperature=float(temperature),
            max_output_tokens=int(max_output_tokens),
            insecure_tls=bool(insecure_tls),
            strict_json=True,
        )
    except urllib.error.HTTPError as e:
        # Fallback: some providers/models do not support response_format=json_object.
        code = int(getattr(e, "code", 0) or 0)
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if code == 400 and "response_format" in body.lower():
            try:
                content, raw_resp = _openrouter_chat_completion(
                    api_key=key,
                    model=model,
                    base_url=base_url,
                    messages=msgs,
                    timeout_sec=int(timeout_sec),
                    temperature=float(temperature),
                    max_output_tokens=int(max_output_tokens),
                    insecure_tls=bool(insecure_tls),
                    strict_json=False,
                )
            except urllib.error.HTTPError as e2:
                out["status"] = "error"
                try:
                    body2 = e2.read().decode("utf-8", errors="replace")
                except Exception:
                    body2 = ""
                out["error"] = f"HTTP {getattr(e2, 'code', '')}: {ascii_sanitize(body2)[:1500]}"
                return out
            except Exception as e2:
                out["status"] = "error"
                out["error"] = ascii_sanitize(str(e2))
                return out
        else:
            out["status"] = "error"
            out["error"] = f"HTTP {getattr(e, 'code', '')}: {ascii_sanitize(body)[:1500]}"
            return out
    except Exception as e:
        out["status"] = "error"
        out["error"] = ascii_sanitize(str(e))
        return out

    out["response"] = {"usage": raw_resp.get("usage", {}), "id": raw_resp.get("id", ""), "model": raw_resp.get("model", "")}
    out["response_text_preview"] = ascii_sanitize(content)[:4000]

    json_text = _extract_json_object_text(content)
    if not json_text:
        out["status"] = "parse_error"
        out["error"] = "Model response did not contain a JSON object."
        return out

    try:
        parsed = json.loads(json_text)
    except Exception as e:
        out["status"] = "parse_error"
        out["error"] = f"JSON parse failed: {ascii_sanitize(str(e))}"
        return out

    patch = sanitize_calibrator_patch(parsed, rules)
    cand_rules, merge_report = apply_calibrator_patch(rules.raw, patch)
    out["patch"] = patch
    out["merge_report"] = merge_report
    out["candidate_rules"] = cand_rules
    out["status"] = "ok"
    return out


def _clean_person_candidate(raw: str) -> str:
    s = ascii_sanitize((raw or "").strip())
    if not s:
        return ""

    # Normalize "Last, First [M.]" => "First [M.] Last"
    if "," in s:
        left, right = s.split(",", 1)
        s = (right.strip() + " " + left.strip()).strip()

    s = re.sub(r"[\t\r\n]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -:;,.")
    if not s:
        return ""
    if any(ch.isdigit() for ch in s):
        return ""

    toks = re.findall(r"[A-Za-z][A-Za-z'\-]*\.?", s)
    if len(toks) < 2 or len(toks) > 4:
        return ""
    if len(toks) > 2:
        # Keep 3-token names only when the middle token is an initial.
        if not (len(toks) == 3 and len(toks[1].rstrip(".")) == 1):
            return ""

    cleaned: List[str] = []
    for idx, tok in enumerate(toks):
        base = tok.rstrip(".")
        if not base:
            return ""
        low = base.lower()
        if low in PEOPLE_TOKEN_STOPWORDS:
            return ""
        if base.isupper() and len(base) > 2:
            return ""
        if len(base) == 1:
            if idx in {0, len(toks) - 1}:
                return ""
            cleaned.append(base.upper() + ".")
            continue
        cleaned.append(base[0].upper() + base[1:].lower())

    cand = " ".join(cleaned)
    if cand.lower() in PEOPLE_PHRASE_BLOCKLIST:
        return ""
    return cand


def _person_from_email_local(local: str) -> str:
    ll = (local or "").strip().lower()
    if not ll:
        return ""
    if ll in EMAIL_LOCAL_STOPWORDS:
        return ""
    toks = [t for t in re.split(r"[._\-]+", ll) if t]
    toks = [t for t in toks if t.isalpha() and len(t) >= 2]
    if len(toks) < 2 or len(toks) > 3:
        return ""
    if any(t in EMAIL_LOCAL_STOPWORDS for t in toks):
        return ""
    guess = " ".join(t.capitalize() for t in toks)
    return _clean_person_candidate(guess)


def extract_entity_facets(path: str, core: Dict[str, str], sample: str) -> Dict[str, List[str]]:
    """
    Lightweight deterministic entity extraction for business corpus facets.
    """
    text = " ".join([path, core.get("title", ""), core.get("subject", ""), (sample or "")[:4000]])
    text_l = text.lower()

    systems: List[str] = []
    vendors: List[str] = []
    projects: List[str] = []
    locations: List[str] = []
    people: List[str] = []

    # Systems: hostnames + infrastructure-like identifiers.
    for m in re.finditer(r"\b[a-z0-9][a-z0-9\-]{1,40}\.(?:local|corp|internal|com|net|org|edu|gov)\b", text_l):
        systems.append(m.group(0))
    for m in re.finditer(r"\b[a-z0-9][a-z0-9\-_]{2,40}(?:srv|server|db|vpn|fw|firewall|switch|router)\b", text_l):
        systems.append(m.group(0))

    # Vendors: controlled list for predictable facet quality.
    for v in VENDOR_KEYWORDS:
        if re.search(r"\b" + re.escape(v) + r"\b", text_l):
            vendors.append(v)

    # Projects/programs: title-cased project naming patterns.
    for m in re.finditer(r"\b(?:project|program|initiative)\s+([A-Z][A-Za-z0-9][A-Za-z0-9_\-]{1,40})\b", text):
        projects.append(m.group(1))
    for m in re.finditer(r"\b[A-Z]{2,}[A-Z0-9_\-]{1,20}\b", text):
        tok = m.group(0)
        if tok.upper() in PROJECT_TOKEN_BLOCKLIST:
            continue
        if tok.lower() in {"api", "pdf", "docx", "pptx", "xlsx", "sql", "dns", "dhcp", "vpn", "mfa"}:
            continue
        projects.append(tok)

    # Locations: common place hints + "City, ST" style.
    for loc in LOCATION_HINTS:
        if loc in text_l:
            locations.append(loc.title())
    for m in re.finditer(r"\b([A-Z][a-z]+,\s*[A-Z]{2})\b", text):
        locations.append(m.group(1))

    # People: creator metadata + email local-parts + title-cased names.
    creator = core.get("creator", "")
    if creator:
        for c in re.split(r"[;|]+", creator):
            cand = _clean_person_candidate(c)
            if cand:
                people.append(cand)
    for m in re.finditer(r"\b([a-z0-9._%+\-]+)@[a-z0-9.\-]+\.[a-z]{2,}\b", text_l):
        cand = _person_from_email_local(m.group(1))
        if cand:
            people.append(cand)
    for m in re.finditer(r"\b([A-Z][a-z]{1,40},\s*[A-Z][a-z]{1,40}(?:\s+[A-Z]\.)?)\b", text):
        cand = _clean_person_candidate(m.group(1))
        if cand:
            people.append(cand)

    return {
        "systems": _stable_unique(systems, max_items=12),
        "vendors": _stable_unique(vendors, max_items=12),
        "projects": _stable_unique(projects, max_items=12),
        "locations": _stable_unique(locations, max_items=12),
        "people": _stable_unique(people, max_items=12),
    }


def _simhash64(text: str) -> int:
    toks = tokenize_for_keywords(text)
    if not toks:
        return 0
    weights = Counter(toks)
    vec = [0] * 64
    for tok, w in weights.items():
        h = int(hashlib.sha1(tok.encode("utf-8", errors="ignore")).hexdigest()[:16], 16)
        for i in range(64):
            bit = (h >> i) & 1
            vec[i] += int(w) if bit else -int(w)
    out = 0
    for i, v in enumerate(vec):
        if v >= 0:
            out |= 1 << i
    return out


def _hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _duplicate_feature_blob(d: "DocRecord") -> str:
    return " ".join(
        [
            d.path.lower(),
            d.openxml_core.get("title", "").lower(),
            d.openxml_core.get("subject", "").lower(),
            d.sample_text[:1600].lower(),
        ]
    )


def assign_duplicate_clusters(docs: List["DocRecord"], *, near_hamming_threshold: int = 3) -> List[Dict[str, Any]]:
    """
    Mark docs as unique/exact/near duplicates and return cluster summaries.
    """
    n = len(docs)
    if n == 0:
        return []

    # Exact duplicates by (sha256 if available) else (size + sample hash).
    exact_key_to_idx: Dict[str, List[int]] = defaultdict(list)
    for i, d in enumerate(docs):
        if d.sha256:
            key = "sha256:" + d.sha256
        else:
            sk = hashlib.sha1((d.sample_text[:2000] + "|" + str(d.size_bytes)).encode("utf-8", errors="ignore")).hexdigest()
            key = "soft:" + sk
        exact_key_to_idx[key].append(i)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for idxs in exact_key_to_idx.values():
        if len(idxs) < 2:
            continue
        first = idxs[0]
        for j in idxs[1:]:
            union(first, j)

    # Near duplicates via SimHash bands to avoid full O(n^2).
    simhashes: List[int] = []
    bands: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, d in enumerate(docs):
        sh = _simhash64(_duplicate_feature_blob(d))
        simhashes.append(sh)
        for band in range(4):
            key = (band, (sh >> (band * 16)) & 0xFFFF)
            bands[key].append(i)

    checked_pairs: set[Tuple[int, int]] = set()
    for idxs in bands.values():
        if len(idxs) < 2:
            continue
        if len(idxs) > 300:
            # Avoid pathological collisions.
            continue
        for i in range(len(idxs)):
            a = idxs[i]
            for j in range(i + 1, len(idxs)):
                b = idxs[j]
                if a == b:
                    continue
                p = (a, b) if a < b else (b, a)
                if p in checked_pairs:
                    continue
                checked_pairs.add(p)
                if docs[a].ext != docs[b].ext:
                    continue
                if _hamming64(simhashes[a], simhashes[b]) <= int(near_hamming_threshold):
                    union(a, b)

    clusters: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    out_clusters: List[Dict[str, Any]] = []
    cnum = 0
    for root, idxs in sorted(clusters.items(), key=lambda kv: (len(kv[1]) * -1, min(kv[1]))):
        cnum += 1
        cid = f"dup_{cnum:06d}"
        size = len(idxs)
        kinds = []
        for i in idxs:
            d = docs[i]
            if size == 1:
                d.duplicate_kind = "unique"
            else:
                # If exact key shared by >1 mark exact, else near.
                if d.sha256 and len(exact_key_to_idx.get("sha256:" + d.sha256, [])) > 1:
                    d.duplicate_kind = "exact"
                else:
                    d.duplicate_kind = "near"
            d.duplicate_cluster_id = cid
            d.duplicate_cluster_size = size
            kinds.append(d.duplicate_kind)

        out_clusters.append(
            {
                "cluster_id": cid,
                "size": size,
                "kinds": sorted(set(kinds)),
                "representative_path": docs[min(idxs)].path,
                "paths": [docs[i].path for i in sorted(idxs, key=lambda x: docs[x].path.lower())[:50]],
            }
        )
    return out_clusters


def build_bucket_cards(docs: Sequence["DocRecord"], *, samples_per_bucket: int = 8) -> List[Dict[str, Any]]:
    by_sub: Dict[Tuple[str, str], List[DocRecord]] = defaultdict(list)
    for d in docs:
        by_sub[(d.first_bucket, d.sub_bucket)].append(d)

    cards: List[Dict[str, Any]] = []
    for (fb, sb) in sorted(by_sub.keys(), key=lambda k: (k[0], k[1])):
        items = sorted(by_sub[(fb, sb)], key=lambda d: d.path.lower())
        sample_docs = items[: int(max(1, samples_per_bucket))]
        dom_counts = Counter(d.semantic_domain for d in items if d.semantic_domain)
        art_counts = Counter(d.artifact_type for d in items if d.artifact_type)

        ent = {"systems": [], "vendors": [], "projects": [], "locations": [], "people": []}
        for d in sample_docs:
            ent["systems"].extend(d.entities_systems)
            ent["vendors"].extend(d.entities_vendors)
            ent["projects"].extend(d.entities_projects)
            ent["locations"].extend(d.entities_locations)
            ent["people"].extend(d.entities_people)

        input_hash_src = "|".join(d.path + ":" + str(d.size_bytes) + ":" + d.sample_text[:200] for d in sample_docs)
        input_hash = hashlib.sha256(input_hash_src.encode("utf-8", errors="ignore")).hexdigest()

        hints = []
        top_artifacts = [k for k, _ in art_counts.most_common(3)]
        if "procedure" in top_artifacts:
            hints.append("How do I perform this process?")
        if "meeting_notes" in top_artifacts:
            hints.append("What decisions and action items were recorded?")
        if "report" in top_artifacts:
            hints.append("What were the key status or performance findings?")
        if not hints:
            hints.append("What documents are most relevant in this bucket?")

        cards.append(
            {
                "schema_version": SCHEMA_VERSION,
                "first_bucket": fb,
                "sub_bucket": sb,
                "bucket_description": f"Documents under structural bucket '{sb}' within '{fb}'.",
                "domain_hint": dom_counts.most_common(1)[0][0] if dom_counts else "general",
                "common_entities": {
                    "systems": _stable_unique(ent["systems"], max_items=10),
                    "vendors": _stable_unique(ent["vendors"], max_items=10),
                    "projects": _stable_unique(ent["projects"], max_items=10),
                    "locations": _stable_unique(ent["locations"], max_items=10),
                    "people": _stable_unique(ent["people"], max_items=10),
                },
                "typical_questions": hints,
                "allowed_artifact_types": [k for k, _ in art_counts.most_common(8)],
                "top_keywords": top_keywords(sample_docs, max_keywords=20, keywords_source="meta+sample"),
                "stats": {
                    "files": len(items),
                    "bytes": int(sum(d.size_bytes for d in items)),
                },
                "sample_paths": [d.path for d in sample_docs[:5]],
                "input_hash": input_hash,
            }
        )
    return cards


def maybe_unwrap_single_dir(p: Path) -> Path:
    """
    If `p` contains exactly one subdirectory and no files (excluding artifacts),
    treat that child directory as the effective root. This is common for archives
    that extract into a single top-level folder.
    """
    try:
        kids = list(p.iterdir())
    except Exception:
        return p

    subdirs = []
    files = 0
    for k in kids:
        if k.name in ARTIFACT_BASENAMES or k.name.startswith("._"):
            continue
        if k.is_dir():
            subdirs.append(k)
        elif k.is_file():
            files += 1

    if files == 0 and len(subdirs) == 1:
        return subdirs[0]
    return p


@dataclass(frozen=True)
class SourceRoot:
    id: str
    path: Path  # absolute path


@dataclass
class ArchiveExtraction:
    path: str
    kind: str
    extracted_to: str
    status: str
    error: str = ""


@dataclass
class DocRecord:
    schema_version: int
    path: str
    source_root: str
    first_bucket: str
    sub_bucket: str
    semantic_domain: str
    semantic_domain_score: int
    semantic_domain_margin: int
    semantic_decision: str
    semantic_abstain_reason: str
    semantic_top_candidates: List[Dict[str, Any]]
    ext: str
    size_bytes: int
    mtime_date: str
    sha256: str
    openxml_core: Dict[str, str]
    sample_text: str
    sample_truncated: bool
    sample_method: str
    extraction_notes: str
    # These prefixes are derived deterministically from the on-disk directory structure.
    # They are used to build router include rules without requiring reverse-parsing ids.
    _first_bucket_prefix: str
    _sub_bucket_prefix: str
    doc_type: str
    artifact_type: str
    mtime_year: str
    mtime_month: str
    mtime_decade: str
    openxml_created_year: str
    openxml_modified_year: str
    sensitivity_flags: List[str]
    sensitivity_level: str
    tags: List[str]
    entities_systems: List[str]
    entities_vendors: List[str]
    entities_projects: List[str]
    entities_locations: List[str]
    entities_people: List[str]
    duplicate_kind: str
    duplicate_cluster_id: str
    duplicate_cluster_size: int

    def to_json_obj(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "path": self.path,
            "source_root": self.source_root,
            "routing": {
                # Back-compat fields (schema_version 1)
                "first_bucket": self.first_bucket,
                "sub_bucket": self.sub_bucket,
                # Hybrid routing (schema_version 2)
                "structural": {
                    "first_bucket": self.first_bucket,
                    "sub_bucket": self.sub_bucket,
                },
                "semantic": {
                    "domain": self.semantic_domain,
                    "score": int(self.semantic_domain_score),
                    "margin": int(self.semantic_domain_margin),
                    "decision": self.semantic_decision,
                    "abstain_reason": self.semantic_abstain_reason,
                    "fallback_recommended": self.semantic_decision == "abstained",
                    "top_candidates": list(self.semantic_top_candidates),
                },
            },
            "file": {
                "ext": self.ext,
                "size_bytes": self.size_bytes,
                "mtime_date": self.mtime_date,
                "sha256": self.sha256,
            },
            "openxml_core": dict(self.openxml_core),
            "sample": {
                "text": self.sample_text,
                "chars": len(self.sample_text),
                "truncated": self.sample_truncated,
                "method": self.sample_method,
                "notes": self.extraction_notes,
            },
            "facets": {
                "semantic_domain": self.semantic_domain,
                "semantic_decision": self.semantic_decision,
                "doc_type": self.doc_type,
                "artifact_type": self.artifact_type,
                "mtime_year": self.mtime_year,
                "mtime_month": self.mtime_month,
                "mtime_decade": self.mtime_decade,
                "openxml_created_year": self.openxml_created_year,
                "openxml_modified_year": self.openxml_modified_year,
                "sensitivity_level": self.sensitivity_level,
                "sensitivity_flags": list(self.sensitivity_flags),
                "tags": list(self.tags),
                "entities_systems": list(self.entities_systems),
                "entities_vendors": list(self.entities_vendors),
                "entities_projects": list(self.entities_projects),
                "entities_locations": list(self.entities_locations),
                "entities_people": list(self.entities_people),
                "duplicate_kind": self.duplicate_kind,
                "duplicate_cluster_id": self.duplicate_cluster_id,
                "duplicate_cluster_size": int(self.duplicate_cluster_size),
            },
        }


def discover_source_roots(
    root: Path,
    out_dir: Path,
    *,
    container_dirs: set[str],
    include_hidden: bool,
    exclude_dir_names: set[str],
) -> List[SourceRoot]:
    """
    Partition the corpus into source roots (top-level dirs, container child dirs,
    extracted archives, plus root-level files).
    """
    roots: List[SourceRoot] = []
    exclude_dir_names_l = {str(x).strip().lower() for x in exclude_dir_names if str(x).strip()}

    # Root-level files only (not recursive)
    roots.append(SourceRoot(id="root", path=root))

    try:
        children = sorted(list(root.iterdir()), key=lambda p: p.name.lower())
    except Exception:
        children = []

    for ch in children:
        if ch == out_dir:
            continue
        if not include_hidden and ch.name.startswith("."):
            continue
        if ch.name in DEFAULT_EXCLUDE_DIRS:
            continue
        if is_routerkit_output_dir(ch):
            continue
        if ch.name.lower() in exclude_dir_names_l:
            continue
        if not ch.is_dir():
            continue

        name_l = ch.name.lower()
        if name_l in container_dirs:
            try:
                subdirs = [p for p in ch.iterdir() if p.is_dir() and (include_hidden or not p.name.startswith("."))]
            except Exception:
                subdirs = []
            subdirs = sorted(subdirs, key=lambda p: p.name.lower())
            for sub in subdirs:
                if sub.name.lower() in exclude_dir_names_l or sub.name in DEFAULT_EXCLUDE_DIRS:
                    continue
                if is_routerkit_output_dir(sub):
                    continue
                rid = f"{slug(name_l)}_{slug(sub.name)}"
                roots.append(SourceRoot(id=rid, path=maybe_unwrap_single_dir(sub.resolve())))
            continue

        rid = f"dir_{slug(ch.name)}"
        roots.append(SourceRoot(id=rid, path=maybe_unwrap_single_dir(ch.resolve())))

    # Extracted archives under out_dir/unarchived/<name>
    extracted_root = out_dir / "unarchived"
    if extracted_root.exists() and extracted_root.is_dir():
        try:
            ex_children = [p for p in extracted_root.iterdir() if p.is_dir()]
        except Exception:
            ex_children = []
        ex_children = sorted(ex_children, key=lambda p: p.name.lower())
        for ex in ex_children:
            rid = f"archive_{slug(ex.name)}"
            roots.append(SourceRoot(id=rid, path=maybe_unwrap_single_dir(ex.resolve())))

    # De-dupe by path (keep first id)
    seen = set()
    out_roots = []
    for r in roots:
        rp = str(r.path)
        if rp in seen:
            continue
        seen.add(rp)
        out_roots.append(r)

    # Ensure source_root ids are unique (slug collisions are common in real corpora).
    used_ids: set[str] = set()
    final: List[SourceRoot] = []
    for r in out_roots:
        rid = r.id
        if rid in used_ids:
            rid = f"{rid}_{_short_hash(str(r.path))}"
        used_ids.add(rid)
        final.append(SourceRoot(id=rid, path=r.path))
    return final


def iter_files_in_source(
    sr: SourceRoot,
    *,
    include_hidden: bool,
    exclude_names: set[str],
    exclude_dirs: set[str],
    exclude_basenames: Optional[set[str]] = None,
    exclude_path_regex: Optional[Sequence[re.Pattern]] = None,
    include_exts: Optional[set[str]] = None,
    exclude_exts: Optional[set[str]] = None,
) -> List[Path]:
    out: List[Path] = []
    ex_base = {x.lower() for x in (exclude_basenames or set())}
    ex_path_rx = list(exclude_path_regex or [])
    exclude_dirs_l = {str(x).strip().lower() for x in (exclude_dirs or set()) if str(x).strip()}

    if sr.id == "root":
        # Only root-level files
        try:
            kids = list(sr.path.iterdir())
        except Exception:
            return []
        for k in kids:
            if k.is_dir():
                continue
            if not include_hidden and k.name.startswith("."):
                continue
            if k.name in exclude_names:
                continue
            if ex_base and k.name.lower() in ex_base:
                continue
            if is_artifact_path(k):
                continue
            if ex_path_rx and path_matches_any_regex(norm_path(str(k)), ex_path_rx):
                continue
            e = ext_lower(k)
            if include_exts and e not in include_exts:
                continue
            if exclude_exts and e in exclude_exts:
                continue
            try:
                if int(k.stat().st_size) <= 0:
                    continue
            except Exception:
                continue
            out.append(k.resolve())
        return sorted(out, key=lambda p: norm_path(str(p)).lower())

    for dirpath, dirnames, filenames in os.walk(sr.path):
        dp = Path(dirpath)

        # prune hidden/system dirs
        pruned = []
        for d in dirnames:
            if d.lower() in exclude_dirs_l:
                continue
            if not include_hidden and d.startswith("."):
                continue
            # Avoid ingesting our own prior outputs if they live inside the scanned tree.
            cand = dp / d
            try:
                if (cand / "manifest.json").exists() and is_routerkit_output_dir(cand):
                    continue
            except Exception:
                pass
            pruned.append(d)
        dirnames[:] = pruned

        for fn in filenames:
            if fn in exclude_names:
                continue
            if ex_base and fn.lower() in ex_base:
                continue
            if not include_hidden and fn.startswith("."):
                continue
            p = dp / fn
            if is_artifact_path(p):
                continue
            if ex_path_rx and path_matches_any_regex(norm_path(str(p)), ex_path_rx):
                continue
            e = ext_lower(p)
            if include_exts and e not in include_exts:
                continue
            if exclude_exts and e in exclude_exts:
                continue
            try:
                if int(p.stat().st_size) <= 0:
                    continue
            except Exception:
                continue
            out.append(p.resolve())

    return sorted(out, key=lambda p: norm_path(str(p)).lower())


def compute_routing(rel_in_source: str, bucket_depth: int) -> Tuple[str, str]:
    # buckets are derived from directory path inside the source root (not filename)
    dir_rel = norm_path(os.path.dirname(rel_in_source))
    parts = [p for p in dir_rel.split("/") if p]

    if not parts:
        return "root_docs", "root_docs"

    first = slug(parts[0])

    if bucket_depth <= 1:
        return first, first

    depth = max(2, int(bucket_depth))
    chosen = parts[:depth]
    sub = "/".join(slug(x) for x in chosen)
    return first, sub


def compute_bucket_prefixes(source_root_rel: str, rel_in_source: str, bucket_depth: int) -> Tuple[str, str]:
    """
    Returns (first_bucket_prefix, sub_bucket_prefix) as workspace-relative path prefixes.
    These are based on the *actual* directory names inside the source root (not slugs).
    """
    src = norm_path(source_root_rel).strip("/")
    dir_rel = norm_path(os.path.dirname(rel_in_source)).strip("/")
    parts = [p for p in dir_rel.split("/") if p]

    if not parts:
        # File lives at source root itself.
        prefix = src
        return prefix, prefix

    first_parts = [parts[0]]
    depth = max(2, int(bucket_depth)) if bucket_depth > 1 else 1
    sub_parts = parts[:depth]

    first_prefix = "/".join([p for p in [src, "/".join(first_parts)] if p])
    sub_prefix = "/".join([p for p in [src, "/".join(sub_parts)] if p])
    return first_prefix, sub_prefix


def parse_openxml_dt_year(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    fmts = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in fmts:
        try:
            return str(datetime.strptime(s, fmt).year)
        except ValueError:
            pass
    if len(s) >= 6 and (s[-6] in "+-") and s[-3] == ":":
        s2 = s[:-6]
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]:
            try:
                return str(datetime.strptime(s2, fmt).year)
            except ValueError:
                pass
    return ""


def mtime_facets(mtime_date: str) -> Tuple[str, str, str]:
    s = (mtime_date or "").strip()
    if not s:
        return "", "", ""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return "", "", ""
    year = dt.year
    month = f"{dt.year:04d}-{dt.month:02d}"
    decade = f"{(year // 10) * 10}s"
    return str(year), month, decade


def extract_sample_and_core(
    p: Path,
    *,
    ext: str,
    limit_chars: int,
    pdf_max_pages: int,
    pptx_max_slides: int,
    xlsx_max_strings: int,
    xlsx_max_chars: int,
    zip_max_members: int,
    binary_max_bytes: int,
    sample_mode: str,
) -> Tuple[Dict[str, str], str, str, str]:
    """
    Returns: (core_props, sample_text, method, extraction_notes)
    """
    notes = ""
    core: Dict[str, str] = {}
    sample = ""
    method = "none"
    original_ext = (ext or "").strip().lower()
    ext = canonical_extract_ext(p, original_ext)
    if ext != original_ext:
        notes = f"ext_alias:{original_ext or '(none)'}->{ext}"

    sm = (sample_mode or "redacted").strip().lower()
    if sm == "none":
        # For safety, avoid reading file content. We still extract lightweight OpenXML core props
        # where present, because it is useful for provenance and typically lower-risk than body text.
        if ext in {"docx", "docm", "dotx", "dotm", "pptx", "pptm", "ppsx", "ppsm", "potx", "potm", "xlsx", "xlsm", "xltx", "xltm", "vsdx"}:
            try:
                with zipfile.ZipFile(p, "r") as z:
                    core = extract_openxml_core(z)
            except Exception:
                core = {}
        return core, "", "none", "sample_mode=none"

    # Emails
    if ext == "eml":
        method = "eml_stdlib"
        sample = extract_eml_sample(p, limit_chars, max_bytes=binary_max_bytes)
        return core, sample, method, notes
    if ext == "msg":
        method = "msg"
        sample = extract_msg_sample(p, limit_chars, max_bytes=binary_max_bytes)
        if sample:
            method = "msg_structured"
        return core, sample, method, notes

    # Text-ish
    if ext == "csv":
        csv_sample, csv_method, csv_notes = extract_csv_mail_sample(p, limit_chars)
        if csv_sample:
            method = csv_method
            sample = csv_sample
        else:
            method = "text"
            sample = read_text_file(p, limit_chars)
        if csv_notes:
            if notes:
                notes = f"{notes}; {csv_notes}"
            else:
                notes = csv_notes
        return core, sample, method, notes

    if ext in {
        "txt",
        "md",
        "jsonl",
        "tsv",
        "psv",
        "log",
        "sql",
        "toml",
        "properties",
        "env",
        "ini",
        "conf",
        "cfg",
        "json",
        "yaml",
        "yml",
        "xml",
        "html",
        "htm",
        "(noext)",
    }:
        method = "text"
        sample = read_text_file(p, limit_chars)
        return core, sample, method, notes
    if ext == "rtf":
        method = "rtf"
        sample = extract_rtf_text(p, limit_chars)
        if sample and _rtf_to_text is not None:
            method = "rtf_striprtf"
        else:
            method = "rtf_text"
        return core, sample, method, notes
    if ext == "boxnote":
        method = "boxnote"
        sample = extract_boxnote_text(p, limit_chars)
        return core, sample, method, notes

    # PDFs
    if ext == "pdf":
        method = "pdf"
        sample = extract_pdf_text(p, limit_chars, max_pages=pdf_max_pages)
        return core, sample, method, notes
    if ext == "xps":
        method = "xps"
        sample = extract_xps_sample(p, limit_chars)
        return core, sample, method, notes

    # OpenXML
    if ext in {"docx", "docm", "dotx", "dotm"}:
        method = "docx"
        core, sample = extract_docx_sample(p, limit_chars)
        return core, sample, method, notes
    if ext in {"pptx", "pptm", "ppsx", "ppsm", "potx", "potm"}:
        method = "pptx"
        core, sample = extract_pptx_sample(p, limit_chars, max_slides=pptx_max_slides)
        return core, sample, method, notes
    if ext in {"xlsx", "xlsm", "xltx", "xltm"}:
        method = "xlsx"
        core, sample = extract_xlsx_sample(p, limit_chars, max_strings=xlsx_max_strings, max_chars=xlsx_max_chars)
        return core, sample, method, notes
    if ext in {"xls", "xlsb"}:
        # Legacy/binary Excel formats are not OpenXML; best deterministic option is strings scan.
        method = "xls_binary_strings"
        sample = extract_binary_strings_sample(p, limit_chars, max_bytes=binary_max_bytes)
        return core, sample, method, notes
    if ext == "vsdx":
        method = "vsdx"
        core, sample = extract_vsdx_sample(p, limit_chars)
        return core, sample, method, notes
    if ext == "vsd":
        # Legacy Visio binary format fallback.
        method = "vsd_binary_strings"
        sample = extract_binary_strings_sample(p, limit_chars, max_bytes=binary_max_bytes)
        return core, sample, method, notes

    # Images
    if ext in {"jpg", "jpeg", "png", "gif", "tif", "tiff", "bmp", "heic"}:
        method = "image_path"
        sample = ascii_sanitize(f"image: {norm_path(str(p))}")[:limit_chars]
        return core, sample, method, notes

    # Archives (sample members)
    if ext == "zip":
        method = "zip_members"
        sample = extract_zip_members_sample(p, limit_chars, max_members=zip_max_members)
        return core, sample, method, notes
    if ext in {"tar", "tgz", "gz", "bz2", "xz"}:
        method = "tar_members"
        sample = extract_tar_members_sample(p, limit_chars, max_members=zip_max_members)
        return core, sample, method, notes

    # Fallback: binary strings
    # Heuristic: try decoding as text if the file looks mostly printable.
    try:
        with p.open("rb") as f:
            sniff = f.read(min(int(binary_max_bytes), 64 * 1024))
    except Exception:
        sniff = b""
    if sniff and is_probably_text(sniff):
        method = "text_guess"
        sample = ascii_sanitize(sniff.decode("utf-8", errors="replace"))[:limit_chars]
        return core, sample, method, notes

    method = "binary_strings"
    sample = extract_binary_strings_sample(p, limit_chars, max_bytes=binary_max_bytes)
    return core, sample, method, notes


def extract_archives(root: Path, out_dir: Path, *, recursive: bool, include_hidden: bool) -> List[ArchiveExtraction]:
    extracted: List[ArchiveExtraction] = []
    unarchived_dir = out_dir / "unarchived"
    safe_mkdir(unarchived_dir)

    def consider(p: Path) -> bool:
        if not p.is_file():
            return False
        if not include_hidden and p.name.startswith("."):
            return False
        ext = ext_lower(p)
        return ext in {"zip", "tar", "tgz", "gz", "bz2", "xz"}

    if recursive:
        candidates = []
        for dirpath, dirnames, filenames in os.walk(root):
            dp = Path(dirpath)
            # never descend into output dir
            dirnames[:] = [d for d in dirnames if (dp / d).resolve() != out_dir.resolve()]
            for fn in filenames:
                p = (dp / fn).resolve()
                if consider(p):
                    candidates.append(p)
    else:
        candidates = [p.resolve() for p in sorted(root.iterdir(), key=lambda x: x.name.lower()) if consider(p)]

    for arc in candidates:
        ext = ext_lower(arc)
        base = arc.name
        stem = arc.name
        for suf in [".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip", ".tar", ".gz", ".bz2", ".xz"]:
            if stem.lower().endswith(suf):
                stem = stem[: -len(suf)]
                break
        rel_arc = norm_path(str(arc.relative_to(root)))
        dest_name = f"{stem}__{_short_hash(rel_arc)}"
        dest = (unarchived_dir / dest_name).resolve()

        if dest.exists() and any(dest.iterdir()):
            extracted.append(
                ArchiveExtraction(
                    path=rel_arc,
                    kind=ext,
                    extracted_to=norm_path(str(dest.relative_to(root))),
                    status="skipped_exists",
                )
            )
            continue

        safe_mkdir(dest)
        try:
            if ext == "zip":
                with zipfile.ZipFile(arc, "r") as z:
                    _safe_extract_zip(z, dest)
            else:
                with tarfile.open(arc, "r:*") as t:
                    _safe_extract_tar(t, dest)
            extracted.append(
                ArchiveExtraction(
                    path=rel_arc,
                    kind=ext,
                    extracted_to=norm_path(str(dest.relative_to(root))),
                    status="extracted",
                )
            )
        except Exception as e:
            extracted.append(
                ArchiveExtraction(
                    path=rel_arc,
                    kind=ext,
                    extracted_to=norm_path(str(dest.relative_to(root))),
                    status="error",
                    error=str(e),
                )
            )

    return extracted


def _safe_extract_zip(z: zipfile.ZipFile, dest: Path) -> None:
    dest_abs = os.path.abspath(dest)
    for info in z.infolist():
        name = info.filename
        # zipfiles always use '/' internally
        if name.startswith("/") or name.startswith("\\"):
            continue
        target = os.path.abspath(os.path.join(dest_abs, name))
        if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
            continue
        z.extract(info, dest_abs)


def _safe_extract_tar(t: tarfile.TarFile, dest: Path) -> None:
    dest_abs = os.path.abspath(dest)
    for m in t.getmembers():
        name = m.name
        if not name:
            continue
        if name.startswith("/") or name.startswith("\\"):
            continue
        if m.issym() or m.islnk():
            continue
        target = os.path.abspath(os.path.join(dest_abs, name))
        if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
            continue
        t.extract(m, dest_abs)


def build_inventory(docs: Sequence[DocRecord], extractions: Sequence[ArchiveExtraction]) -> Dict[str, Any]:
    ext_counts = Counter()
    ext_bytes = Counter()
    src_counts = Counter()
    src_bytes = Counter()
    mtime_min = None
    mtime_max = None

    for d in docs:
        ext_counts[d.ext] += 1
        ext_bytes[d.ext] += d.size_bytes
        src_counts[d.source_root] += 1
        src_bytes[d.source_root] += d.size_bytes
        if d.mtime_date:
            if mtime_min is None or d.mtime_date < mtime_min:
                mtime_min = d.mtime_date
            if mtime_max is None or d.mtime_date > mtime_max:
                mtime_max = d.mtime_date

    extensions = []
    for ext, count in sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        extensions.append({"ext": ext, "files": count, "bytes": int(ext_bytes[ext])})

    sources = []
    for src, count in sorted(src_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        sources.append({"id": src, "files": count, "bytes": int(src_bytes[src])})

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "documents_total": len(docs),
        "bytes_total": int(sum(d.size_bytes for d in docs)),
        "mtime_min": mtime_min or "",
        "mtime_max": mtime_max or "",
        "extensions": extensions,
        "source_roots": sources,
        "archives": [e.__dict__ for e in extractions],
    }


def build_router(
    docs: Sequence[DocRecord],
    *,
    include_keywords: bool,
    keywords_source: str,
    rules: RulesContext,
    sample_mode: str,
    domain_score_threshold: int,
    domain_margin_threshold: int,
    semantic_threshold_overrides: Sequence[SemanticThresholdOverride],
) -> Dict[str, Any]:
    """
    Build a 2-layer router config from observed (first_bucket, sub_bucket) with
    include prefixes + keywords to help an agent classify queries.
    """
    by_first: Dict[str, List[DocRecord]] = defaultdict(list)
    by_sub: Dict[Tuple[str, str], List[DocRecord]] = defaultdict(list)
    for d in docs:
        by_first[d.first_bucket].append(d)
        by_sub[(d.first_bucket, d.sub_bucket)].append(d)

    def _mtime_span(rows: Sequence[DocRecord]) -> Tuple[str, str]:
        vals = sorted([str(d.mtime_date).strip() for d in rows if str(d.mtime_date).strip()])
        if not vals:
            return "", ""
        return vals[0], vals[-1]

    # Build first-bucket objects
    first_buckets = []
    for fb in sorted(by_first.keys()):
        items = by_first[fb]
        prefixes = sorted({d._first_bucket_prefix for d in items if d._first_bucket_prefix})
        # If we can't compute meaningful prefixes, omit; agent can still use keywords/examples.
        ex_paths = [d.path for d in sorted(items, key=lambda x: x.path.lower())[:5]]
        ext_counts = Counter(d.ext for d in items)
        kw = top_keywords(items, max_keywords=25, keywords_source=keywords_source) if include_keywords else []

        # Sub-buckets under this first bucket
        sub_buckets = []
        sub_keys = sorted({sb for (_fb, sb) in by_sub.keys() if _fb == fb})
        for sb in sub_keys:
            sb_items = by_sub[(fb, sb)]
            sb_kw = top_keywords(sb_items, max_keywords=20, keywords_source=keywords_source) if include_keywords else []
            sb_paths = [d.path for d in sorted(sb_items, key=lambda x: x.path.lower())[:5]]
            sb_ext_counts = Counter(d.ext for d in sb_items)
            sb_mtime_min, sb_mtime_max = _mtime_span(sb_items)
            sub_buckets.append(
                {
                    "id": sb,
                    "name": sb,
                    "include_path_prefixes": sorted({d._sub_bucket_prefix for d in sb_items if d._sub_bucket_prefix}),
                    "keywords": sb_kw,
                    "example_paths": sb_paths,
                    "stats": {
                        "files": len(sb_items),
                        "bytes": int(sum(d.size_bytes for d in sb_items)),
                        "mtime_min": sb_mtime_min,
                        "mtime_max": sb_mtime_max,
                        "ext_counts": dict(sorted(sb_ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                    },
                }
            )

        fb_mtime_min, fb_mtime_max = _mtime_span(items)
        first_buckets.append(
            {
                "id": fb,
                "name": fb,
                "include_path_prefixes": prefixes,
                "keywords": kw,
                "example_paths": ex_paths,
                "stats": {
                    "files": len(items),
                    "bytes": int(sum(d.size_bytes for d in items)),
                    "mtime_min": fb_mtime_min,
                    "mtime_max": fb_mtime_max,
                    "ext_counts": dict(sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                },
                "sub_buckets": sub_buckets,
            }
        )

    # Semantic domain stats
    dom_counts = Counter(d.semantic_domain for d in docs if getattr(d, "semantic_domain", ""))
    dom_bytes = Counter()
    dom_mtime_min: Dict[str, str] = {}
    dom_mtime_max: Dict[str, str] = {}
    for d in docs:
        dom = getattr(d, "semantic_domain", "")
        if not dom:
            continue
        dom_bytes[dom] += int(d.size_bytes)
        mtime_val = str(getattr(d, "mtime_date", "") or "").strip()
        if mtime_val:
            cur_min = str(dom_mtime_min.get(dom, "") or "")
            cur_max = str(dom_mtime_max.get(dom, "") or "")
            if not cur_min or mtime_val < cur_min:
                dom_mtime_min[dom] = mtime_val
            if not cur_max or mtime_val > cur_max:
                dom_mtime_max[dom] = mtime_val
    dom_stats = []
    for dom, cnt in sorted(dom_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        dom_stats.append(
            {
                "id": dom,
                "files": cnt,
                "bytes": int(dom_bytes.get(dom, 0)),
                "mtime_min": str(dom_mtime_min.get(dom, "") or ""),
                "mtime_max": str(dom_mtime_max.get(dom, "") or ""),
            }
        )
    decision_counts = Counter(getattr(d, "semantic_decision", "") for d in docs if getattr(d, "semantic_decision", ""))

    semantic_domains = []
    for dr in sorted(rules.domains, key=lambda r: r.id):
        semantic_domains.append(
            {
                "id": dr.id,
                "name": dr.name,
                "description": dr.description,
                "keywords": sorted(dr.keywords),
                "phrases": list(dr.phrases),
            }
        )

    obj = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "layers": ["first_bucket", "sub_bucket"],
        "first_buckets": first_buckets,
        "routing": {
            "structural": {"layers": ["first_bucket", "sub_bucket"], "first_buckets": first_buckets},
            "semantic": {
                "domain": {
                    "default": rules.default_domain,
                    "domains": semantic_domains,
                    "stats": dom_stats,
                    "decision_counts": dict(sorted(decision_counts.items(), key=lambda kv: kv[0])),
                    "thresholds": {
                        "score_threshold": int(domain_score_threshold),
                        "margin_threshold": int(domain_margin_threshold),
                        "overrides": [
                            {
                                "id": ov.id,
                                "first_bucket": ov.first_bucket,
                                "sub_bucket": ov.sub_bucket,
                                "first_bucket_regex": ov.first_bucket_regex.pattern if ov.first_bucket_regex is not None else "",
                                "path_prefix": ov.path_prefix,
                                "path_regex": ov.path_regex.pattern if ov.path_regex is not None else "",
                                "score_threshold": ov.score_threshold,
                                "margin_threshold": ov.margin_threshold,
                            }
                            for ov in semantic_threshold_overrides
                        ],
                    },
                }
            },
        },
        "safety": {
            "sample_mode": str(sample_mode),
            "router_keywords_included": bool(include_keywords),
            "keywords_source": str(keywords_source),
        },
        "rules": {
            "source": rules.source,
            "sha256": rules.sha256,
            "rules_version": int(rules.raw.get("rules_version", 0)) if isinstance(rules.raw, dict) else 0,
        },
    }
    return obj


def facet_definitions(*, rules: RulesContext) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "facets": [
            {"id": "source_root", "type": "string", "description": "Top-level source collection for provenance."},
            {
                "id": "semantic_domain",
                "type": "enum",
                "values": list(rules.domain_ids),
                "description": "Low-cardinality semantic domain for routing (rules-based).",
            },
            {
                "id": "semantic_decision",
                "type": "enum",
                "values": SEMANTIC_DECISION_ENUM,
                "description": "Whether semantic routing made a confident decision or abstained.",
            },
            {"id": "doc_type", "type": "enum", "values": DOC_TYPE_ENUM, "description": "Normalized file type."},
            {"id": "artifact_type", "type": "enum", "values": list(rules.artifact_type_ids), "description": "Coarse semantic-ish type."},
            {"id": "mtime_year", "type": "int", "description": "Filesystem modified year (YYYY)."},
            {"id": "mtime_month", "type": "string", "description": "Filesystem modified month (YYYY-MM)."},
            {"id": "mtime_decade", "type": "string", "description": "Filesystem modified decade (eg 2010s)."},
            {"id": "openxml_created_year", "type": "int", "description": "OpenXML core created year (if present)."},
            {"id": "openxml_modified_year", "type": "int", "description": "OpenXML core modified year (if present)."},
            {
                "id": "sensitivity_level",
                "type": "enum",
                "values": SENSITIVITY_LEVEL_ENUM,
                "description": "Coarse gating signal derived from sensitivity flags.",
            },
            {
                "id": "sensitivity_flags",
                "type": "set<enum>",
                "values": SENSITIVITY_FLAGS_ENUM,
                "description": "Heuristic sensitivity signals (comma-separated in TSV; array in JSON).",
            },
            {"id": "tags", "type": "set<string>", "description": "Lightweight topical tags (heuristic)."},
            {"id": "entities_systems", "type": "set<string>", "description": "Extracted system/entity hints (hostnames, infra IDs)."},
            {"id": "entities_vendors", "type": "set<string>", "description": "Extracted vendor/provider hints."},
            {"id": "entities_projects", "type": "set<string>", "description": "Extracted project/program hints."},
            {"id": "entities_locations", "type": "set<string>", "description": "Extracted location hints."},
            {"id": "entities_people", "type": "set<string>", "description": "Extracted people/name hints."},
            {
                "id": "duplicate_kind",
                "type": "enum",
                "values": DUPLICATE_KIND_ENUM,
                "description": "Duplicate signal: unique, exact duplicate, or near duplicate.",
            },
        ],
    }


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, objs: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=True) + "\n")


def write_tsv(path: Path, rows: Sequence[Dict[str, Any]], cols: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cols), delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def _doc_cache_obj(d: DocRecord) -> Dict[str, Any]:
    return {
        "schema_version": int(d.schema_version),
        "path": d.path,
        "source_root": d.source_root,
        "first_bucket": d.first_bucket,
        "sub_bucket": d.sub_bucket,
        "semantic_domain": d.semantic_domain,
        "semantic_domain_score": int(d.semantic_domain_score),
        "semantic_domain_margin": int(d.semantic_domain_margin),
        "semantic_decision": d.semantic_decision,
        "semantic_abstain_reason": d.semantic_abstain_reason,
        "semantic_top_candidates": list(d.semantic_top_candidates),
        "ext": d.ext,
        "size_bytes": int(d.size_bytes),
        "mtime_date": d.mtime_date,
        "sha256": d.sha256,
        "openxml_core": dict(d.openxml_core),
        "sample_text": d.sample_text,
        "sample_truncated": bool(d.sample_truncated),
        "sample_method": d.sample_method,
        "extraction_notes": d.extraction_notes,
        "_first_bucket_prefix": d._first_bucket_prefix,
        "_sub_bucket_prefix": d._sub_bucket_prefix,
        "doc_type": d.doc_type,
        "artifact_type": d.artifact_type,
        "mtime_year": d.mtime_year,
        "mtime_month": d.mtime_month,
        "mtime_decade": d.mtime_decade,
        "openxml_created_year": d.openxml_created_year,
        "openxml_modified_year": d.openxml_modified_year,
        "sensitivity_flags": list(d.sensitivity_flags),
        "sensitivity_level": d.sensitivity_level,
        "tags": list(d.tags),
        "entities_systems": list(d.entities_systems),
        "entities_vendors": list(d.entities_vendors),
        "entities_projects": list(d.entities_projects),
        "entities_locations": list(d.entities_locations),
        "entities_people": list(d.entities_people),
        "duplicate_kind": d.duplicate_kind,
        "duplicate_cluster_id": d.duplicate_cluster_id,
        "duplicate_cluster_size": int(d.duplicate_cluster_size),
    }


def _doc_from_cache_obj(obj: Dict[str, Any]) -> DocRecord:
    return DocRecord(
        schema_version=int(obj.get("schema_version", SCHEMA_VERSION)),
        path=str(obj.get("path", "")),
        source_root=str(obj.get("source_root", "")),
        first_bucket=str(obj.get("first_bucket", "")),
        sub_bucket=str(obj.get("sub_bucket", "")),
        semantic_domain=str(obj.get("semantic_domain", "general")),
        semantic_domain_score=int(obj.get("semantic_domain_score", 0)),
        semantic_domain_margin=int(obj.get("semantic_domain_margin", 0)),
        semantic_decision=str(obj.get("semantic_decision", "abstained")),
        semantic_abstain_reason=str(obj.get("semantic_abstain_reason", "")),
        semantic_top_candidates=list(obj.get("semantic_top_candidates", [])),
        ext=str(obj.get("ext", "")),
        size_bytes=int(obj.get("size_bytes", 0)),
        mtime_date=str(obj.get("mtime_date", "")),
        sha256=str(obj.get("sha256", "")),
        openxml_core=dict(obj.get("openxml_core", {})),
        sample_text=str(obj.get("sample_text", "")),
        sample_truncated=bool(obj.get("sample_truncated", False)),
        sample_method=str(obj.get("sample_method", "")),
        extraction_notes=str(obj.get("extraction_notes", "")),
        _first_bucket_prefix=str(obj.get("_first_bucket_prefix", "")),
        _sub_bucket_prefix=str(obj.get("_sub_bucket_prefix", "")),
        doc_type=str(obj.get("doc_type", "other")),
        artifact_type=str(obj.get("artifact_type", "other")),
        mtime_year=str(obj.get("mtime_year", "")),
        mtime_month=str(obj.get("mtime_month", "")),
        mtime_decade=str(obj.get("mtime_decade", "")),
        openxml_created_year=str(obj.get("openxml_created_year", "")),
        openxml_modified_year=str(obj.get("openxml_modified_year", "")),
        sensitivity_flags=list(obj.get("sensitivity_flags", [])),
        sensitivity_level=str(obj.get("sensitivity_level", "normal")),
        tags=list(obj.get("tags", [])),
        entities_systems=list(obj.get("entities_systems", [])),
        entities_vendors=list(obj.get("entities_vendors", [])),
        entities_projects=list(obj.get("entities_projects", [])),
        entities_locations=list(obj.get("entities_locations", [])),
        entities_people=list(obj.get("entities_people", [])),
        duplicate_kind=str(obj.get("duplicate_kind", "unique")),
        duplicate_cluster_id=str(obj.get("duplicate_cluster_id", "")),
        duplicate_cluster_size=int(obj.get("duplicate_cluster_size", 1)),
    )


def _state_init(db_path: Path) -> sqlite3.Connection:
    safe_mkdir(db_path.parent)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS doc_cache (
          path TEXT PRIMARY KEY,
          mtime_ns INTEGER NOT NULL,
          size_bytes INTEGER NOT NULL,
          run_sig TEXT NOT NULL,
          doc_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_cache_run_sig ON doc_cache(run_sig)")
    conn.commit()
    return conn


def _state_load_doc(
    conn: sqlite3.Connection,
    *,
    path: str,
    mtime_ns: int,
    size_bytes: int,
    run_sig: str,
) -> Optional[DocRecord]:
    try:
        cur = conn.execute(
            "SELECT doc_json FROM doc_cache WHERE path=? AND mtime_ns=? AND size_bytes=? AND run_sig=?",
            (path, int(mtime_ns), int(size_bytes), run_sig),
        )
        row = cur.fetchone()
        if not row:
            return None
        obj = json.loads(row[0])
        if not isinstance(obj, dict):
            return None
        return _doc_from_cache_obj(obj)
    except Exception:
        return None


def _state_store_doc(
    conn: sqlite3.Connection,
    *,
    path: str,
    mtime_ns: int,
    size_bytes: int,
    run_sig: str,
    doc: DocRecord,
) -> None:
    obj = _doc_cache_obj(doc)
    payload = json.dumps(obj, ensure_ascii=True, sort_keys=True)
    conn.execute(
        """
        INSERT INTO doc_cache(path, mtime_ns, size_bytes, run_sig, doc_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          mtime_ns=excluded.mtime_ns,
          size_bytes=excluded.size_bytes,
          run_sig=excluded.run_sig,
          doc_json=excluded.doc_json,
          updated_at=excluded.updated_at
        """,
        (path, int(mtime_ns), int(size_bytes), run_sig, payload, now_iso()),
    )


def build_facet_summary(docs: Sequence[DocRecord]) -> List[Dict[str, Any]]:
    counts: Dict[str, Counter] = defaultdict(Counter)
    for d in docs:
        # scalar facets
        for k, v in [
            ("source_root", d.source_root),
            ("semantic_domain", getattr(d, "semantic_domain", "")),
            ("semantic_decision", getattr(d, "semantic_decision", "")),
            ("doc_type", d.doc_type),
            ("artifact_type", d.artifact_type),
            ("mtime_year", d.mtime_year),
            ("mtime_month", d.mtime_month),
            ("mtime_decade", d.mtime_decade),
            ("openxml_created_year", d.openxml_created_year),
            ("openxml_modified_year", d.openxml_modified_year),
            ("sensitivity_level", d.sensitivity_level),
            ("duplicate_kind", getattr(d, "duplicate_kind", "")),
        ]:
            if v:
                counts[k][str(v)] += 1

        # set facets
        for flag in d.sensitivity_flags:
            counts["sensitivity_flags"][flag] += 1
        for tag in d.tags:
            counts["tags"][tag] += 1
        for x in d.entities_systems:
            counts["entities_systems"][x] += 1
        for x in d.entities_vendors:
            counts["entities_vendors"][x] += 1
        for x in d.entities_projects:
            counts["entities_projects"][x] += 1
        for x in d.entities_locations:
            counts["entities_locations"][x] += 1
        for x in d.entities_people:
            counts["entities_people"][x] += 1

    rows: List[Dict[str, Any]] = []
    for facet in sorted(counts.keys()):
        for value, count in counts[facet].most_common(50):
            rows.append({"facet": facet, "value": value, "count": count})
    return rows


def build_overview_md(root: Path, docs: Sequence[DocRecord], inventory_obj: Dict[str, Any], router_obj: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Corpus Overview (RouterKit)")
    lines.append("")
    lines.append(f"- Generated at: {inventory_obj.get('generated_at','')}")
    lines.append(f"- Root: `{norm_path(str(root))}`")
    lines.append(f"- Documents: **{inventory_obj.get('documents_total',0)}**")
    lines.append(f"- Bytes: **{inventory_obj.get('bytes_total',0)}**")
    lines.append(f"- Time range (mtime): **{inventory_obj.get('mtime_min','')}** to **{inventory_obj.get('mtime_max','')}**")
    lines.append("")

    lines.append("## Top File Types")
    lines.append("")
    lines.append("ext | files | bytes")
    lines.append("--- | ---: | ---:")
    for e in inventory_obj.get("extensions", [])[:15]:
        lines.append(f"{e['ext']} | {e['files']} | {e['bytes']}")
    lines.append("")

    lines.append("## Source Roots")
    lines.append("")
    lines.append("source_root | files | bytes")
    lines.append("--- | ---: | ---:")
    for s in inventory_obj.get("source_roots", [])[:20]:
        lines.append(f"{s['id']} | {s['files']} | {s['bytes']}")
    lines.append("")

    lines.append("## Semantic Domains")
    lines.append("")
    lines.append("domain | files | bytes")
    lines.append("--- | ---: | ---:")
    dom_stats = (
        router_obj.get("routing", {}).get("semantic", {}).get("domain", {}).get("stats", [])
        if isinstance(router_obj, dict)
        else []
    )
    for d in (dom_stats or [])[:20]:
        try:
            lines.append(f"{d.get('id','')} | {int(d.get('files',0))} | {int(d.get('bytes',0))}")
        except Exception:
            continue
    lines.append("")

    lines.append("## Semantic Decisions")
    lines.append("")
    lines.append("decision | count")
    lines.append("--- | ---:")
    dcounts = (
        router_obj.get("routing", {}).get("semantic", {}).get("domain", {}).get("decision_counts", {})
        if isinstance(router_obj, dict)
        else {}
    )
    for k in sorted(dcounts.keys()):
        lines.append(f"{k} | {int(dcounts.get(k, 0))}")
    lines.append("")

    lines.append("## Duplicates")
    lines.append("")
    lines.append("kind | count")
    lines.append("--- | ---:")
    dup_counts = Counter(getattr(d, "duplicate_kind", "unique") for d in docs)
    for k, v in sorted(dup_counts.items(), key=lambda kv: kv[0]):
        lines.append(f"{k} | {int(v)}")
    lines.append("")

    lines.append("## Router Buckets")
    lines.append("")
    lines.append("first_bucket | files | bytes")
    lines.append("--- | ---: | ---:")
    for fb in router_obj.get("first_buckets", [])[:25]:
        st = fb.get("stats", {})
        lines.append(f"{fb.get('id','')} | {st.get('files',0)} | {st.get('bytes',0)}")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_schemas(schema_dir: Path, *, rules: RulesContext) -> Dict[str, str]:
    safe_mkdir(schema_dir)

    tool_schema = {
        "type": "object",
        "required": ["name", "version"],
        "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
    }

    manifest_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Manifest",
        "type": "object",
        "required": ["schema_version", "tool", "generated_at", "input_root", "output_dir", "artifacts"],
        "properties": {
            "schema_version": {"type": "integer"},
            "tool": tool_schema,
            "generated_at": {"type": "string"},
            "input_root": {"type": "string"},
            "output_dir": {"type": "string"},
            "parameters": {"type": "object"},
            "counts": {"type": "object"},
            "artifacts": {"type": "object"},
        },
    }

    extension_schema = {
        "type": "object",
        "required": ["ext", "files", "bytes"],
        "properties": {"ext": {"type": "string"}, "files": {"type": "integer"}, "bytes": {"type": "integer"}},
    }
    source_root_schema = {
        "type": "object",
        "required": ["id", "files", "bytes"],
        "properties": {"id": {"type": "string"}, "files": {"type": "integer"}, "bytes": {"type": "integer"}},
    }
    archive_schema = {
        "type": "object",
        "required": ["path", "kind", "extracted_to", "status"],
        "properties": {
            "path": {"type": "string"},
            "kind": {"type": "string"},
            "extracted_to": {"type": "string"},
            "status": {"type": "string"},
            "error": {"type": "string"},
        },
    }

    inventory_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Inventory",
        "type": "object",
        "required": ["schema_version", "tool", "generated_at", "documents_total", "bytes_total", "extensions", "source_roots", "archives"],
        "properties": {
            "schema_version": {"type": "integer"},
            "tool": tool_schema,
            "generated_at": {"type": "string"},
            "documents_total": {"type": "integer"},
            "bytes_total": {"type": "integer"},
            "mtime_min": {"type": "string"},
            "mtime_max": {"type": "string"},
            "extensions": {"type": "array", "items": extension_schema},
            "source_roots": {"type": "array", "items": source_root_schema},
            "archives": {"type": "array", "items": archive_schema},
        },
    }

    ext_counts_schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    bucket_stats_schema = {
        "type": "object",
        "required": ["files", "bytes", "ext_counts"],
        "properties": {
            "files": {"type": "integer"},
            "bytes": {"type": "integer"},
            "mtime_min": {"type": "string"},
            "mtime_max": {"type": "string"},
            "ext_counts": ext_counts_schema,
        },
    }
    subbucket_schema = {
        "type": "object",
        "required": ["id", "name", "include_path_prefixes", "keywords", "example_paths", "stats"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "include_path_prefixes": {"type": "array", "items": {"type": "string"}},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "example_paths": {"type": "array", "items": {"type": "string"}},
            "stats": bucket_stats_schema,
        },
    }
    first_bucket_schema = {
        "type": "object",
        "required": ["id", "name", "include_path_prefixes", "keywords", "example_paths", "stats", "sub_buckets"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "include_path_prefixes": {"type": "array", "items": {"type": "string"}},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "example_paths": {"type": "array", "items": {"type": "string"}},
            "stats": bucket_stats_schema,
            "sub_buckets": {"type": "array", "items": subbucket_schema},
        },
    }

    router_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Router",
        "type": "object",
        "required": ["schema_version", "tool", "generated_at", "layers", "first_buckets"],
        "properties": {
            "schema_version": {"type": "integer"},
            "tool": tool_schema,
            "generated_at": {"type": "string"},
            "layers": {"type": "array", "items": {"type": "string"}},
            "first_buckets": {"type": "array", "items": first_bucket_schema},
            "routing": {"type": "object"},
            "safety": {"type": "object"},
            "rules": {"type": "object"},
        },
    }

    facet_item_schema = {
        "type": "object",
        "required": ["id", "type", "description"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string"},
            "description": {"type": "string"},
            "values": {"type": "array", "items": {"type": "string"}},
        },
    }

    facets_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Facet Definitions",
        "type": "object",
        "required": ["schema_version", "tool", "generated_at", "facets"],
        "properties": {
            "schema_version": {"type": "integer"},
            "tool": tool_schema,
            "generated_at": {"type": "string"},
            "facets": {"type": "array", "items": facet_item_schema},
        },
    }

    document_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Document Record (JSONL line)",
        "type": "object",
        "required": ["schema_version", "path", "source_root", "routing", "file", "sample", "facets"],
        "properties": {
            "schema_version": {"type": "integer"},
            "path": {"type": "string"},
            "source_root": {"type": "string"},
            "routing": {
                "type": "object",
                "required": ["first_bucket", "sub_bucket", "structural", "semantic"],
                "properties": {
                    "first_bucket": {"type": "string"},
                    "sub_bucket": {"type": "string"},
                    "structural": {
                        "type": "object",
                        "required": ["first_bucket", "sub_bucket"],
                        "properties": {"first_bucket": {"type": "string"}, "sub_bucket": {"type": "string"}},
                    },
                    "semantic": {
                        "type": "object",
                        "required": ["domain"],
                        "properties": {
                            "domain": {"type": "string", "enum": list(rules.domain_ids)},
                            "score": {"type": "integer"},
                            "margin": {"type": "integer"},
                            "decision": {"type": "string", "enum": SEMANTIC_DECISION_ENUM},
                            "abstain_reason": {"type": "string"},
                            "fallback_recommended": {"type": "boolean"},
                            "top_candidates": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["domain", "score"],
                                    "properties": {
                                        "domain": {"type": "string", "enum": list(rules.domain_ids)},
                                        "score": {"type": "integer"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "file": {
                "type": "object",
                "required": ["ext", "size_bytes", "mtime_date", "sha256"],
                "properties": {
                    "ext": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "mtime_date": {"type": "string"},
                    "sha256": {"type": "string"},
                },
            },
            "openxml_core": {"type": "object"},
            "sample": {
                "type": "object",
                "required": ["text", "chars", "truncated", "method", "notes"],
                "properties": {
                    "text": {"type": "string"},
                    "chars": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                    "method": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            "facets": {
                "type": "object",
                "required": [
                    "semantic_domain",
                    "semantic_decision",
                    "doc_type",
                    "artifact_type",
                    "mtime_year",
                    "mtime_month",
                    "mtime_decade",
                    "sensitivity_level",
                    "sensitivity_flags",
                    "tags",
                ],
                "properties": {
                    "semantic_domain": {"type": "string", "enum": list(rules.domain_ids)},
                    "semantic_decision": {"type": "string", "enum": SEMANTIC_DECISION_ENUM},
                    "doc_type": {"type": "string", "enum": DOC_TYPE_ENUM},
                    "artifact_type": {"type": "string", "enum": list(rules.artifact_type_ids)},
                    "mtime_year": {"type": "string"},
                    "mtime_month": {"type": "string"},
                    "mtime_decade": {"type": "string"},
                    "openxml_created_year": {"type": "string"},
                    "openxml_modified_year": {"type": "string"},
                    "sensitivity_level": {"type": "string", "enum": SENSITIVITY_LEVEL_ENUM},
                    "sensitivity_flags": {"type": "array", "items": {"type": "string", "enum": SENSITIVITY_FLAGS_ENUM}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "entities_systems": {"type": "array", "items": {"type": "string"}},
                    "entities_vendors": {"type": "array", "items": {"type": "string"}},
                    "entities_projects": {"type": "array", "items": {"type": "string"}},
                    "entities_locations": {"type": "array", "items": {"type": "string"}},
                    "entities_people": {"type": "array", "items": {"type": "string"}},
                    "duplicate_kind": {"type": "string", "enum": DUPLICATE_KIND_ENUM},
                    "duplicate_cluster_id": {"type": "string"},
                    "duplicate_cluster_size": {"type": "integer"},
                },
            },
        },
    }

    rules_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Rules (Ontology + Classifier Rules)",
        "type": "object",
        "required": ["rules_version", "domains", "artifact_types", "sensitivity"],
        "properties": {
            "rules_version": {"type": "integer"},
            "default_domain": {"type": "string"},
            "ingest": {
                "type": "object",
                "properties": {
                    "exclude_dirs": {"type": "array", "items": {"type": "string"}},
                    "exclude_exts": {"type": "array", "items": {"type": "string"}},
                    "exclude_basenames": {"type": "array", "items": {"type": "string"}},
                    "exclude_path_regex": {"type": "array"},
                },
            },
            "semantic_routing": {
                "type": "object",
                "properties": {
                    "default_thresholds": {
                        "type": "object",
                        "properties": {
                            "score_threshold": {"type": "integer"},
                            "margin_threshold": {"type": "integer"},
                        },
                    },
                    "threshold_overrides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "first_bucket": {"type": "string"},
                                "sub_bucket": {"type": "string"},
                                "first_bucket_regex": {},
                                "path_prefix": {"type": "string"},
                                "path_regex": {},
                                "score_threshold": {"type": "integer"},
                                "margin_threshold": {"type": "integer"},
                            },
                        },
                    },
                },
            },
            "domains": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "phrases": {"type": "array", "items": {"type": "string"}},
                        "regex": {"type": "array"},
                        "negative_keywords": {"type": "array", "items": {"type": "string"}},
                        "negative_phrases": {"type": "array", "items": {"type": "string"}},
                        "weights": {"type": "object"},
                    },
                },
            },
            "artifact_types": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "phrases": {"type": "array", "items": {"type": "string"}},
                        "regex": {"type": "array"},
                        "doc_types": {"type": "array", "items": {"type": "string"}},
                        "exts": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "sensitivity": {
                "type": "object",
                "required": ["flags", "level_map", "redactions"],
                "properties": {
                    "flags": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {
                                "id": {"type": "string"},
                                "path_keywords": {"type": "array", "items": {"type": "string"}},
                                "content_keywords": {"type": "array", "items": {"type": "string"}},
                                "regex": {"type": "array"},
                            },
                        },
                    },
                    "level_map": {"type": "object"},
                    "redactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "pattern", "replacement"],
                            "properties": {
                                "id": {"type": "string"},
                                "pattern": {"type": "string"},
                                "replacement": {"type": "string"},
                                "flags": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    }

    bucket_card_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Bucket Card (JSONL line)",
        "type": "object",
        "required": [
            "schema_version",
            "first_bucket",
            "sub_bucket",
            "bucket_description",
            "domain_hint",
            "common_entities",
            "typical_questions",
            "allowed_artifact_types",
            "top_keywords",
            "stats",
            "sample_paths",
            "input_hash",
        ],
        "properties": {
            "schema_version": {"type": "integer"},
            "first_bucket": {"type": "string"},
            "sub_bucket": {"type": "string"},
            "bucket_description": {"type": "string"},
            "domain_hint": {"type": "string"},
            "common_entities": {"type": "object"},
            "typical_questions": {"type": "array", "items": {"type": "string"}},
            "allowed_artifact_types": {"type": "array", "items": {"type": "string"}},
            "top_keywords": {"type": "array", "items": {"type": "string"}},
            "stats": {"type": "object"},
            "sample_paths": {"type": "array", "items": {"type": "string"}},
            "input_hash": {"type": "string"},
        },
    }

    dedupe_cluster_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Dedupe Clusters",
        "type": "object",
        "required": ["schema_version", "generated_at", "clusters"],
        "properties": {
            "schema_version": {"type": "integer"},
            "generated_at": {"type": "string"},
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["cluster_id", "size", "kinds", "representative_path", "paths"],
                    "properties": {
                        "cluster_id": {"type": "string"},
                        "size": {"type": "integer"},
                        "kinds": {"type": "array", "items": {"type": "string", "enum": DUPLICATE_KIND_ENUM}},
                        "representative_path": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }

    semantic_fallback_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Semantic Fallback Candidate (JSONL line)",
        "type": "object",
        "required": ["path", "semantic_domain_default", "semantic_score", "semantic_margin", "abstain_reason", "top_candidates"],
        "properties": {
            "path": {"type": "string"},
            "source_root": {"type": "string"},
            "semantic_domain_default": {"type": "string"},
            "semantic_score": {"type": "integer"},
            "semantic_margin": {"type": "integer"},
            "abstain_reason": {"type": "string"},
            "top_candidates": {"type": "array", "items": {"type": "object"}},
            "doc_type": {"type": "string"},
            "artifact_type": {"type": "string"},
        },
    }

    corpus_calibrator_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RouterKit Corpus Calibrator Report",
        "type": "object",
        "required": ["schema_version", "tool", "generated_at", "feature", "provider", "model", "status", "digest_sha256"],
        "properties": {
            "schema_version": {"type": "integer"},
            "tool": tool_schema,
            "generated_at": {"type": "string"},
            "feature": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "status": {"type": "string"},
            "digest_sha256": {"type": "string"},
            "digest": {"type": "object"},
            "patch": {"type": "object"},
            "merge_report": {"type": "object"},
            "candidate_rules": {"type": ["object", "null"]},
            "request": {"type": "object"},
            "response": {"type": "object"},
            "response_text_preview": {"type": "string"},
            "error": {"type": "string"},
        },
    }

    files = {
        "manifest.schema.json": manifest_schema,
        "inventory.schema.json": inventory_schema,
        "router.schema.json": router_schema,
        "facets.schema.json": facets_schema,
        "document.schema.json": document_schema,
        "rules.schema.json": rules_schema,
        "bucket_card.schema.json": bucket_card_schema,
        "dedupe_clusters.schema.json": dedupe_cluster_schema,
        "semantic_fallback_candidate.schema.json": semantic_fallback_schema,
        "corpus_calibrator.schema.json": corpus_calibrator_schema,
    }

    out_paths = {}
    for name, obj in files.items():
        p = schema_dir / name
        p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        out_paths[name] = norm_path(str(p))
    return out_paths



def _load_docs_index(path_or_dir: str) -> Dict[str, Dict[str, Any]]:
    p = Path(path_or_dir).expanduser().resolve()
    if p.is_dir():
        p = p / "documents.jsonl"
    rows: Dict[str, Dict[str, Any]] = {}
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        rp = str(obj.get("path", "")).strip()
        if not rp:
            continue
        sem = obj.get("routing", {}).get("semantic", {}) if isinstance(obj.get("routing"), dict) else {}
        rows[rp] = {
            "path": rp,
            "decision": str(sem.get("decision", obj.get("facets", {}).get("semantic_decision", "abstained"))),
            "domain": str(sem.get("domain", obj.get("facets", {}).get("semantic_domain", "general"))),
            "score": int(sem.get("score", 0) or 0),
            "margin": int(sem.get("margin", 0) or 0),
            "doc_type": str(obj.get("facets", {}).get("doc_type", "other")),
        }
    return rows


def _current_docs_index(docs: Sequence["DocRecord"]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for d in docs:
        rows[d.path] = {
            "path": d.path,
            "decision": str(d.semantic_decision),
            "domain": str(d.semantic_domain),
            "score": int(d.semantic_domain_score),
            "margin": int(d.semantic_domain_margin),
            "doc_type": str(d.doc_type),
        }
    return rows


def _load_benchmark_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _argv_has_flag(argv: Sequence[str], flag: str) -> bool:
    for tok in argv:
        if tok == flag or tok.startswith(flag + "="):
            return True
    return False


def _resolve_profile_path(profile_path: Path, raw: Any) -> str:
    txt = str(raw or "").strip()
    if not txt:
        return ""
    p = Path(txt).expanduser()
    if not p.is_absolute():
        p = (profile_path.parent / p).resolve()
    else:
        p = p.resolve()
    return str(p)


def _metric_float(metrics: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    if not isinstance(metrics, dict):
        return None
    val = metrics.get(key)
    try:
        return float(val)
    except Exception:
        return None


def _summary_from_docs_index(rows: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    routed = 0
    abstained = 0
    routed_domains: Counter[str] = Counter()
    for r in rows.values():
        if str(r.get("decision", "")) == "routed":
            routed += 1
            routed_domains[str(r.get("domain", "general"))] += 1
        else:
            abstained += 1
    it_ops_share = (100.0 * float(routed_domains.get("it_ops", 0)) / float(routed)) if routed > 0 else 0.0
    return {
        "documents": int(len(rows)),
        "routed": int(routed),
        "abstained": int(abstained),
        "abstain_rate": round((100.0 * float(abstained) / float(len(rows))) if rows else 0.0, 4),
        "routed_domains": dict(routed_domains),
        "it_ops_share_routed_pct": round(float(it_ops_share), 4),
    }


def compute_acceptance_report(
    *,
    current_rows: Dict[str, Dict[str, Any]],
    baseline_rows: Dict[str, Dict[str, Any]],
    min_routed_gain: int,
    max_routed_to_abstained: int,
    max_itops_share_drift_pp: float,
    benchmark_current: Optional[Dict[str, Any]] = None,
    benchmark_baseline: Optional[Dict[str, Any]] = None,
    min_route_precision: Optional[float] = None,
    min_domain_precision: Optional[float] = None,
    max_precision_drop_vs_baseline: Optional[float] = None,
) -> Dict[str, Any]:
    common = sorted(set(current_rows) & set(baseline_rows))

    base_common: Dict[str, Dict[str, Any]] = {k: baseline_rows[k] for k in common}
    cur_common: Dict[str, Dict[str, Any]] = {k: current_rows[k] for k in common}

    base_summary = _summary_from_docs_index(base_common)
    cur_summary = _summary_from_docs_index(cur_common)

    routed_to_abstained = 0
    abstained_to_routed = 0
    decision_changed = 0
    domain_changed_on_routed = 0

    for p in common:
        b = base_common[p]
        c = cur_common[p]
        bd = str(b.get("decision", "abstained"))
        cd = str(c.get("decision", "abstained"))
        if bd != cd:
            decision_changed += 1
            if bd == "routed" and cd != "routed":
                routed_to_abstained += 1
            elif bd != "routed" and cd == "routed":
                abstained_to_routed += 1
        if bd == "routed" and cd == "routed" and str(b.get("domain", "")) != str(c.get("domain", "")):
            domain_changed_on_routed += 1

    routed_gain = int(cur_summary["routed"] - base_summary["routed"])
    itops_share_drift_pp = float(cur_summary["it_ops_share_routed_pct"] - base_summary["it_ops_share_routed_pct"])
    cur_route_precision = _metric_float(benchmark_current, "route_precision")
    cur_domain_precision = _metric_float(benchmark_current, "domain_precision")
    base_route_precision = _metric_float(benchmark_baseline, "route_precision")
    route_precision_drop_vs_baseline: Optional[float] = None
    if cur_route_precision is not None and base_route_precision is not None:
        route_precision_drop_vs_baseline = float(base_route_precision - cur_route_precision)

    def _gate_min_metric(actual: Optional[float], configured: Optional[float]) -> Dict[str, Any]:
        if configured is None:
            return {
                "enabled": False,
                "configured": None,
                "actual": round(float(actual), 4) if actual is not None else None,
                "pass": True,
                "reason": "disabled",
            }
        conf = float(configured)
        if actual is None:
            return {
                "enabled": True,
                "configured": round(conf, 4),
                "actual": None,
                "pass": False,
                "reason": "missing_benchmark_metrics",
            }
        act = float(actual)
        return {
            "enabled": True,
            "configured": round(conf, 4),
            "actual": round(act, 4),
            "pass": bool(act >= conf),
        }

    def _gate_max_metric(actual: Optional[float], configured: Optional[float]) -> Dict[str, Any]:
        if configured is None:
            return {
                "enabled": False,
                "configured": None,
                "actual": round(float(actual), 4) if actual is not None else None,
                "pass": True,
                "reason": "disabled",
            }
        conf = float(configured)
        if actual is None:
            return {
                "enabled": True,
                "configured": round(conf, 4),
                "actual": None,
                "pass": False,
                "reason": "missing_benchmark_metrics",
            }
        act = float(actual)
        return {
            "enabled": True,
            "configured": round(conf, 4),
            "actual": round(act, 4),
            "pass": bool(act <= conf),
        }

    gates = {
        "min_routed_gain": {
            "configured": int(min_routed_gain),
            "actual": int(routed_gain),
            "pass": bool(routed_gain >= int(min_routed_gain)),
        },
        "max_routed_to_abstained": {
            "configured": int(max_routed_to_abstained),
            "actual": int(routed_to_abstained),
            "pass": bool(routed_to_abstained <= int(max_routed_to_abstained)),
        },
        "max_itops_share_drift_pp": {
            "configured": float(max_itops_share_drift_pp),
            "actual": round(float(itops_share_drift_pp), 4),
            "pass": bool(float(itops_share_drift_pp) <= float(max_itops_share_drift_pp)),
        },
        "min_route_precision": _gate_min_metric(cur_route_precision, min_route_precision),
        "min_domain_precision": _gate_min_metric(cur_domain_precision, min_domain_precision),
        "max_precision_drop_vs_baseline": _gate_max_metric(route_precision_drop_vs_baseline, max_precision_drop_vs_baseline),
    }

    passed = all(bool(v.get("pass")) for v in gates.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "common_documents": int(len(common)),
        "baseline": base_summary,
        "current": cur_summary,
        "diff": {
            "routed_gain": int(routed_gain),
            "abstained_delta": int(cur_summary["abstained"] - base_summary["abstained"]),
            "decision_changed": int(decision_changed),
            "abstained_to_routed": int(abstained_to_routed),
            "routed_to_abstained": int(routed_to_abstained),
            "domain_changed_on_routed": int(domain_changed_on_routed),
            "it_ops_share_drift_pp": round(float(itops_share_drift_pp), 4),
            "route_precision_current": round(float(cur_route_precision), 4) if cur_route_precision is not None else None,
            "domain_precision_current": round(float(cur_domain_precision), 4) if cur_domain_precision is not None else None,
            "route_precision_baseline": round(float(base_route_precision), 4) if base_route_precision is not None else None,
            "route_precision_drop_vs_baseline": (
                round(float(route_precision_drop_vs_baseline), 4)
                if route_precision_drop_vs_baseline is not None
                else None
            ),
        },
        "gates": gates,
        "benchmark": {
            "current": benchmark_current or {},
            "baseline": benchmark_baseline or {},
        },
        "pass": bool(passed),
    }


def _deterministic_take_even(items: Sequence[Any], n: int) -> List[Any]:
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return list(items)
    out: List[Any] = []
    for i in range(n):
        idx = int(round((i * (len(items) - 1)) / max(1, n - 1)))
        out.append(items[idx])
    return out


def create_fixed_benchmark(
    docs: Sequence["DocRecord"],
    *,
    size: int,
    min_score: int,
    min_margin: int,
) -> List[Dict[str, Any]]:
    target = max(200, min(500, int(size)))

    routed = [
        d
        for d in docs
        if d.semantic_decision == "routed"
        and int(d.semantic_domain_score) >= int(min_score)
        and int(d.semantic_domain_margin) >= int(min_margin)
    ]
    abstained = [
        d
        for d in docs
        if d.semantic_decision == "abstained"
        and int(d.semantic_domain_score) <= int(min_score)
        and int(d.semantic_domain_margin) <= int(min_margin)
    ]

    routed = sorted(routed, key=lambda d: (d.semantic_domain, d.path.lower()))
    abstained = sorted(abstained, key=lambda d: d.path.lower())

    want_routed = int(round(target * 0.7))
    want_abstained = target - want_routed

    routed_take = _deterministic_take_even(routed, min(len(routed), want_routed))
    abstained_take = _deterministic_take_even(abstained, min(len(abstained), want_abstained))

    selected: List[DocRecord] = list(routed_take) + list(abstained_take)
    if len(selected) < target:
        remainder = [d for d in sorted(docs, key=lambda x: x.path.lower()) if d.path not in {x.path for x in selected}]
        selected.extend(_deterministic_take_even(remainder, min(target - len(selected), len(remainder))))

    selected = sorted(selected[:target], key=lambda d: d.path.lower())
    rows: List[Dict[str, Any]] = []
    for i, d in enumerate(selected, start=1):
        expected_route = "routed" if d.semantic_decision == "routed" else "abstained"
        expected_domain = d.semantic_domain if expected_route == "routed" else ""
        rows.append(
            {
                "id": f"bm_{i:04d}",
                "path": d.path,
                "source_root": d.source_root,
                "expected_route": expected_route,
                "expected_domain": expected_domain,
                "label_source": "auto_high_confidence_seed",
                "label_confidence": "high" if expected_route == "routed" else "medium",
                "reference": {
                    "semantic_score": int(d.semantic_domain_score),
                    "semantic_margin": int(d.semantic_domain_margin),
                    "doc_type": d.doc_type,
                    "artifact_type": d.artifact_type,
                },
            }
        )
    return rows


def evaluate_labeled_benchmark(
    benchmark_rows: Sequence[Dict[str, Any]],
    current_rows: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    considered = 0
    missing = 0
    predicted_routed = 0
    expected_routed = 0
    true_positive_routed = 0
    domain_eval = 0
    domain_correct = 0
    predicted_abstained = 0

    for row in benchmark_rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path", "")).strip()
        if not path:
            continue
        cur = current_rows.get(path)
        if cur is None:
            missing += 1
            continue
        considered += 1
        exp_route = str(row.get("expected_route", "")).strip().lower()
        exp_domain = str(row.get("expected_domain", "")).strip().lower()
        pred_route = str(cur.get("decision", "abstained")).strip().lower()
        pred_domain = str(cur.get("domain", "general")).strip().lower()

        if exp_route == "routed":
            expected_routed += 1
        if pred_route == "routed":
            predicted_routed += 1
        else:
            predicted_abstained += 1

        if exp_route == "routed" and pred_route == "routed":
            true_positive_routed += 1
            if exp_domain:
                domain_eval += 1
                if pred_domain == exp_domain:
                    domain_correct += 1

    route_precision = float(true_positive_routed) / float(predicted_routed) if predicted_routed > 0 else 0.0
    domain_precision = float(domain_correct) / float(domain_eval) if domain_eval > 0 else 0.0
    abstain_rate = float(predicted_abstained) / float(considered) if considered > 0 else 0.0
    route_recall = float(true_positive_routed) / float(expected_routed) if expected_routed > 0 else 0.0

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "benchmark_total": int(len(benchmark_rows)),
        "considered": int(considered),
        "missing": int(missing),
        "route_precision": round(route_precision, 4),
        "route_recall": round(route_recall, 4),
        "domain_precision": round(domain_precision, 4),
        "abstain_rate": round(abstain_rate, 4),
        "counts": {
            "predicted_routed": int(predicted_routed),
            "expected_routed": int(expected_routed),
            "true_positive_routed": int(true_positive_routed),
            "domain_eval": int(domain_eval),
            "domain_correct": int(domain_correct),
            "predicted_abstained": int(predicted_abstained),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build deterministic routing + facet artifacts for a corpus directory.")
    ap.add_argument("--root", default=".", help="Corpus root directory (default: .)")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help=f"Output directory (default: {DEFAULT_OUT_DIR})")

    ap.add_argument("--extract-archives", action="store_true", help="Extract archives (.zip/.tar/...) into <out>/unarchived/")
    ap.add_argument("--archives-recursive", action="store_true", help="Search for archives recursively (default: top-level only)")

    ap.add_argument("--include-hidden", action="store_true", help="Include hidden files/dirs (dotfiles)")
    ap.add_argument("--container-dir", action="append", default=[], help="Treat this dir name as a container of source roots (repeatable)")
    ap.add_argument("--exclude-dir", action="append", default=[], help="Exclude this directory name (repeatable)")
    ap.add_argument(
        "--exclude-profile",
        choices=EXCLUDE_PROFILE_ENUM,
        default=DEFAULT_EXCLUDE_PROFILE,
        help="Built-in exclusion profile for non-corpus files (default: business_default)",
    )
    ap.add_argument(
        "--include-source-code",
        action="store_true",
        help="Include source code/script file types even when exclusion profile is enabled",
    )

    ap.add_argument("--bucket-depth", type=int, default=2, help="Directory depth used for routing (default: 2)")
    ap.add_argument("--max-files", type=int, default=0, help="If >0, process at most this many files (deterministic order)")

    ap.add_argument("--include-ext", action="append", default=[], help="If set, only include these extensions (repeatable, without dot)")
    ap.add_argument("--exclude-ext", action="append", default=[], help="Exclude these extensions (repeatable, without dot)")

    ap.add_argument("--rules", default="", help="Path to rules.json defining ontology + classifier rules (default: built-in)")
    ap.add_argument("--sample-mode", choices=SAMPLE_MODE_ENUM, default="redacted", help="Sample text mode: none|redacted|full (default: redacted)")
    ap.add_argument(
        "--keywords-source",
        choices=KEYWORDS_SOURCE_ENUM,
        default="meta_only",
        help="Source for router bucket keywords: path_only|meta_only|meta+sample (default: meta_only)",
    )
    ap.add_argument("--no-router-keywords", action="store_true", help="Do not include bucket keyword lists in router.json")
    ap.add_argument(
        "--domain-score-threshold",
        type=int,
        default=None,
        help="Minimum domain score before semantic routing is considered confident (default from rules or built-in)",
    )
    ap.add_argument(
        "--domain-margin-threshold",
        type=int,
        default=None,
        help="Minimum score margin vs second-best domain before semantic routing is considered confident (default from rules or built-in)",
    )
    ap.add_argument("--incremental", action="store_true", help="Enable incremental processing using a SQLite state cache")
    ap.add_argument("--state-db", default="", help="Path to incremental state SQLite DB (default: <out>/state.sqlite)")
    ap.add_argument("--near-dup-hamming", type=int, default=3, help="SimHash hamming distance threshold for near-duplicate clustering")
    ap.add_argument("--bucket-card-samples", type=int, default=8, help="Number of docs sampled per sub-bucket for bucket card generation")
    ap.add_argument("--no-bucket-cards", action="store_true", help="Do not emit bucket_cards.jsonl")
    ap.add_argument("--no-fallback-candidates", action="store_true", help="Do not emit semantic_fallback_candidates.jsonl")
    ap.add_argument("--corpus-calibrator", action="store_true", help="Enable LLM-assisted Corpus Calibrator to generate a candidate rules file")
    ap.add_argument(
        "--calibrator-provider",
        choices=CALIBRATOR_PROVIDER_ENUM,
        default=DEFAULT_CALIBRATOR_PROVIDER,
        help=f"LLM provider for Corpus Calibrator (default: {DEFAULT_CALIBRATOR_PROVIDER})",
    )
    ap.add_argument(
        "--calibrator-model",
        default=DEFAULT_CALIBRATOR_MODEL,
        help=f"Model id for Corpus Calibrator (default: {DEFAULT_CALIBRATOR_MODEL})",
    )
    ap.add_argument(
        "--calibrator-base-url",
        default=DEFAULT_CALIBRATOR_BASE_URL,
        help="Chat completions endpoint for calibrator provider",
    )
    ap.add_argument(
        "--calibrator-api-key",
        default="",
        help="API key for Corpus Calibrator (prefer env var instead of CLI)",
    )
    ap.add_argument(
        "--calibrator-api-key-env",
        default=DEFAULT_CALIBRATOR_API_KEY_ENV,
        help=f"Environment variable name to read calibrator API key (default: {DEFAULT_CALIBRATOR_API_KEY_ENV})",
    )
    ap.add_argument("--calibrator-timeout-sec", type=int, default=120, help="HTTP timeout seconds for calibrator calls")
    ap.add_argument(
        "--calibrator-insecure-tls",
        action="store_true",
        help="Disable TLS certificate verification for calibrator HTTP calls (diagnostic use only)",
    )
    ap.add_argument("--calibrator-temperature", type=float, default=0.0, help="Sampling temperature for calibrator model")
    ap.add_argument("--calibrator-max-output-tokens", type=int, default=3000, help="Max response tokens requested from calibrator model")
    ap.add_argument("--calibrator-max-buckets", type=int, default=24, help="Max first-buckets summarized for calibrator prompt")
    ap.add_argument("--calibrator-samples-per-bucket", type=int, default=4, help="Number of docs sampled per bucket in calibrator digest")
    ap.add_argument("--calibrator-max-snippet-chars", type=int, default=600, help="Per-sample snippet char limit in calibrator digest")
    ap.add_argument(
        "--calibrator-doc-selection",
        choices=CALIBRATOR_DOC_SELECTION_ENUM,
        default=DEFAULT_CALIBRATOR_DOC_SELECTION,
        help="Which docs to include in calibrator digest (default: low_confidence)",
    )
    ap.add_argument(
        "--calibrator-low-score-threshold",
        type=int,
        default=DEFAULT_DOMAIN_SCORE_THRESHOLD,
        help="Low-confidence score threshold for calibrator doc selection",
    )
    ap.add_argument(
        "--calibrator-low-margin-threshold",
        type=int,
        default=DEFAULT_DOMAIN_MARGIN_THRESHOLD,
        help="Low-confidence margin threshold for calibrator doc selection",
    )
    ap.add_argument("--calibrator-dry-run", action="store_true", help="Build calibrator digest and prompt hash without API call")

    ap.add_argument(
        "--benchmark-create",
        default="",
        help="Create/update fixed labeled benchmark JSONL at this path",
    )
    ap.add_argument(
        "--benchmark-file",
        default="",
        help="Evaluate current run against this benchmark JSONL",
    )
    ap.add_argument(
        "--benchmark-size",
        type=int,
        default=DEFAULT_BENCHMARK_SIZE,
        help="Benchmark size when creating benchmark (clamped to 200..500)",
    )
    ap.add_argument(
        "--benchmark-min-score",
        type=int,
        default=DEFAULT_BENCHMARK_MIN_SCORE,
        help="Minimum semantic score for auto-labeled routed benchmark seeds",
    )
    ap.add_argument(
        "--benchmark-min-margin",
        type=int,
        default=DEFAULT_BENCHMARK_MIN_MARGIN,
        help="Minimum semantic margin for auto-labeled routed benchmark seeds",
    )
    ap.add_argument(
        "--benchmark-report",
        default="",
        help="Optional path for benchmark metrics JSON (default: <out>/benchmark_metrics.json)",
    )

    ap.add_argument(
        "--acceptance-baseline",
        default="",
        help="Path to baseline documents.jsonl or artifacts dir for acceptance gating",
    )
    ap.add_argument(
        "--acceptance-min-routed-gain",
        type=int,
        default=0,
        help="Gate: minimum routed gain vs baseline",
    )
    ap.add_argument(
        "--acceptance-max-routed-to-abstained",
        type=int,
        default=999999,
        help="Gate: maximum routed->abstained docs vs baseline",
    )
    ap.add_argument(
        "--acceptance-max-itops-share-drift-pp",
        type=float,
        default=100.0,
        help="Gate: maximum allowed it_ops routed-share drift in percentage points",
    )
    ap.add_argument(
        "--acceptance-min-route-precision",
        type=float,
        default=None,
        help="Gate: minimum route precision on benchmark (requires --benchmark-file or --benchmark-create)",
    )
    ap.add_argument(
        "--acceptance-min-domain-precision",
        type=float,
        default=None,
        help="Gate: minimum domain precision on benchmark (requires --benchmark-file or --benchmark-create)",
    )
    ap.add_argument(
        "--acceptance-max-precision-drop-vs-baseline",
        type=float,
        default=None,
        help="Gate: max allowed drop in route precision vs baseline benchmark",
    )
    ap.add_argument(
        "--acceptance-report",
        default="",
        help="Optional path for acceptance report JSON (default: <out>/acceptance_report.json)",
    )
    ap.add_argument(
        "--acceptance-fail-on-gate",
        action="store_true",
        help="Exit non-zero if acceptance gate fails",
    )
    ap.add_argument(
        "--promote-rules-to",
        default="",
        help="If acceptance passes, copy current --rules file to this path",
    )

    ap.add_argument("--sample-chars", type=int, default=2000, help="Max chars of extracted sample text per doc")
    ap.add_argument("--pdf-max-pages", type=int, default=3, help="Max PDF pages to sample")
    ap.add_argument("--pptx-max-slides", type=int, default=5, help="Max PPTX slides to sample")
    ap.add_argument("--xlsx-max-strings", type=int, default=40, help="Max shared strings to sample from XLSX")
    ap.add_argument("--xlsx-max-chars", type=int, default=3000, help="Max chars worth of shared strings to scan in XLSX")
    ap.add_argument("--zip-max-members", type=int, default=30, help="Max archive members to list in sample")
    ap.add_argument("--binary-max-bytes", type=int, default=2 * 1024 * 1024, help="Max bytes to scan for binary strings fallback")

    ap.add_argument("--hash", action="store_true", help="Compute sha256 for each document (slow for large corpora)")

    args = ap.parse_args()

    root = Path(args.root).resolve()
    out_dir = (root / args.out).resolve()
    safe_mkdir(out_dir)

    rules = load_rules(str(args.rules or ""))
    sample_mode = str(args.sample_mode or "redacted").strip().lower()
    keywords_source = str(args.keywords_source or "meta_only").strip()
    include_router_keywords = not bool(args.no_router_keywords)
    exclude_profile = str(args.exclude_profile or DEFAULT_EXCLUDE_PROFILE).strip().lower()
    include_source_code = bool(args.include_source_code)
    calibrator_enabled = bool(args.corpus_calibrator)
    calibrator_provider = str(args.calibrator_provider or DEFAULT_CALIBRATOR_PROVIDER).strip().lower()
    calibrator_model = str(args.calibrator_model or DEFAULT_CALIBRATOR_MODEL).strip()
    calibrator_base_url = str(args.calibrator_base_url or DEFAULT_CALIBRATOR_BASE_URL).strip()
    calibrator_api_key = str(args.calibrator_api_key or "")
    calibrator_api_key_env = str(args.calibrator_api_key_env or DEFAULT_CALIBRATOR_API_KEY_ENV).strip()
    calibrator_timeout_sec = int(args.calibrator_timeout_sec)
    calibrator_insecure_tls = bool(args.calibrator_insecure_tls)
    calibrator_temperature = float(args.calibrator_temperature)
    calibrator_max_output_tokens = int(args.calibrator_max_output_tokens)
    calibrator_max_buckets = int(args.calibrator_max_buckets)
    calibrator_samples_per_bucket = int(args.calibrator_samples_per_bucket)
    calibrator_max_snippet_chars = int(args.calibrator_max_snippet_chars)
    calibrator_doc_selection = str(args.calibrator_doc_selection or DEFAULT_CALIBRATOR_DOC_SELECTION).strip().lower()
    calibrator_low_score_threshold = int(args.calibrator_low_score_threshold)
    calibrator_low_margin_threshold = int(args.calibrator_low_margin_threshold)
    calibrator_dry_run = bool(args.calibrator_dry_run)

    benchmark_create = str(args.benchmark_create or "").strip()
    benchmark_file = str(args.benchmark_file or "").strip()
    benchmark_size = int(args.benchmark_size)
    benchmark_min_score = int(args.benchmark_min_score)
    benchmark_min_margin = int(args.benchmark_min_margin)
    benchmark_report = str(args.benchmark_report or "").strip()

    acceptance_baseline = str(args.acceptance_baseline or "").strip()
    acceptance_min_routed_gain = int(args.acceptance_min_routed_gain)
    acceptance_max_routed_to_abstained = int(args.acceptance_max_routed_to_abstained)
    acceptance_max_itops_share_drift_pp = float(args.acceptance_max_itops_share_drift_pp)
    acceptance_min_route_precision = (
        float(args.acceptance_min_route_precision) if args.acceptance_min_route_precision is not None else None
    )
    acceptance_min_domain_precision = (
        float(args.acceptance_min_domain_precision) if args.acceptance_min_domain_precision is not None else None
    )
    acceptance_max_precision_drop_vs_baseline = (
        float(args.acceptance_max_precision_drop_vs_baseline)
        if args.acceptance_max_precision_drop_vs_baseline is not None
        else None
    )
    acceptance_report = str(args.acceptance_report or "").strip()
    acceptance_fail_on_gate = bool(args.acceptance_fail_on_gate)
    promote_rules_to = str(args.promote_rules_to or "").strip()

    backends = detected_backends()
    domain_score_threshold = (
        int(args.domain_score_threshold)
        if args.domain_score_threshold is not None
        else int(rules.semantic_default_score_threshold)
    )
    domain_margin_threshold = (
        int(args.domain_margin_threshold)
        if args.domain_margin_threshold is not None
        else int(rules.semantic_default_margin_threshold)
    )
    emit_bucket_cards = not bool(args.no_bucket_cards)
    emit_fallback_candidates = not bool(args.no_fallback_candidates)

    state_db = Path(args.state_db).expanduser().resolve() if args.state_db else (out_dir / "state.sqlite")
    state_conn: Optional[sqlite3.Connection] = None
    run_sig_obj = {
        "tool": TOOL_VERSION,
        "schema": SCHEMA_VERSION,
        "rules_sha256": rules.sha256,
        "sample_mode": sample_mode,
        "sample_chars": int(args.sample_chars),
        "pdf_max_pages": int(args.pdf_max_pages),
        "pptx_max_slides": int(args.pptx_max_slides),
        "xlsx_max_strings": int(args.xlsx_max_strings),
        "xlsx_max_chars": int(args.xlsx_max_chars),
        "zip_max_members": int(args.zip_max_members),
        "binary_max_bytes": int(args.binary_max_bytes),
        "bucket_depth": int(args.bucket_depth),
        "domain_score_threshold": domain_score_threshold,
        "domain_margin_threshold": domain_margin_threshold,
    }
    run_sig = hashlib.sha256(json.dumps(run_sig_obj, sort_keys=True).encode("utf-8")).hexdigest()
    cache_hits = 0
    cache_misses = 0
    if args.incremental:
        state_conn = _state_init(state_db)

    include_hidden = bool(args.include_hidden)
    container_dirs = set(DEFAULT_CONTAINER_DIRS)
    for cd in args.container_dir:
        container_dirs.add(cd.strip().lower())
    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
    exclude_dirs.update(rules.ingest_exclude_dirs)
    for ed in args.exclude_dir:
        exclude_dirs.add(ed.strip())
    exclude_dirs.add(out_dir.name)

    include_exts = {x.lower().lstrip(".") for x in args.include_ext if x and x.strip()}
    exclude_exts = {x.lower().lstrip(".") for x in args.exclude_ext if x and x.strip()}
    exclude_exts.update(rules.ingest_exclude_exts)

    exclude_basenames = set(rules.ingest_exclude_basenames)
    exclude_path_regex = list(rules.ingest_exclude_path_regex)

    if exclude_profile == "business_default":
        exclude_dirs.update(BUSINESS_NON_CORPUS_EXCLUDE_DIRS)
        if not include_source_code:
            exclude_exts.update(BUSINESS_NON_CORPUS_SOURCE_EXTS)
        exclude_basenames.update(x.lower() for x in BUSINESS_NON_CORPUS_EXCLUDE_BASENAMES)
        exclude_path_regex.extend(_compile_regex_list(BUSINESS_NON_CORPUS_EXCLUDE_PATH_REGEX))

    if not include_exts:
        include_exts = set()
    if not exclude_exts:
        exclude_exts = set()

    # 1) Optional extraction
    extractions: List[ArchiveExtraction] = []
    if args.extract_archives:
        extractions = extract_archives(root, out_dir, recursive=bool(args.archives_recursive), include_hidden=include_hidden)

    # 2) Discover source roots
    source_roots = discover_source_roots(
        root,
        out_dir,
        container_dirs=container_dirs,
        include_hidden=include_hidden,
        exclude_dir_names=exclude_dirs,
    )

    # 3) Collect files
    all_files: List[Tuple[SourceRoot, Path]] = []
    for sr in source_roots:
        files = iter_files_in_source(
            sr,
            include_hidden=include_hidden,
            exclude_names=set(),
            exclude_dirs=exclude_dirs,
            exclude_basenames=exclude_basenames,
            exclude_path_regex=exclude_path_regex,
            include_exts=include_exts or None,
            exclude_exts=exclude_exts or None,
        )
        for p in files:
            # Exclude generated artifacts, but allow extracted docs under <out>/unarchived.
            try:
                p.relative_to(out_dir)
            except Exception:
                all_files.append((sr, p))
            else:
                try:
                    p.relative_to(out_dir / "unarchived")
                except Exception:
                    continue
                else:
                    all_files.append((sr, p))

    # De-dupe on absolute path
    seen = set()
    unique: List[Tuple[SourceRoot, Path]] = []
    for sr, p in sorted(all_files, key=lambda t: norm_path(str(t[1])).lower()):
        sp = str(p)
        if sp in seen:
            continue
        seen.add(sp)
        unique.append((sr, p))

    if args.max_files and int(args.max_files) > 0:
        unique = unique[: int(args.max_files)]

    # 4) Build doc records (deterministic order)
    docs: List[DocRecord] = []
    for sr, p in unique:
        rel_path = norm_path(str(p.relative_to(root)))
        rel_in_source = norm_path(str(p.relative_to(sr.path)))
        source_root_rel = norm_path(str(sr.path.relative_to(root))) if sr.id != "root" else ""

        st = p.stat()
        ext = ext_lower(p)
        size_bytes = int(st.st_size)
        mdate = file_mtime_date(p)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))

        if state_conn is not None:
            cached = _state_load_doc(
                state_conn,
                path=rel_path,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                run_sig=run_sig,
            )
            if cached is not None:
                docs.append(cached)
                cache_hits += 1
                continue
            cache_misses += 1

        core, sample, method, notes = extract_sample_and_core(
            p,
            ext=ext,
            limit_chars=int(args.sample_chars),
            pdf_max_pages=int(args.pdf_max_pages),
            pptx_max_slides=int(args.pptx_max_slides),
            xlsx_max_strings=int(args.xlsx_max_strings),
            xlsx_max_chars=int(args.xlsx_max_chars),
            zip_max_members=int(args.zip_max_members),
            binary_max_bytes=int(args.binary_max_bytes),
            sample_mode=sample_mode,
        )

        fb, sb = compute_routing(rel_in_source, bucket_depth=int(args.bucket_depth))
        fb_prefix, sb_prefix = compute_bucket_prefixes(source_root_rel, rel_in_source, bucket_depth=int(args.bucket_depth))

        doc_type = doc_type_from_ext(ext)
        sample_before_redaction = sample
        if sample_mode == "redacted":
            sample = redact_text(sample, rules.redactions)
            core = {k: redact_text(v, rules.redactions) for k, v in core.items() if v}

        sem_score_threshold, sem_margin_threshold, _sem_override_id = resolve_semantic_thresholds(
            path=rel_path,
            first_bucket=fb,
            sub_bucket=sb,
            default_score_threshold=domain_score_threshold,
            default_margin_threshold=domain_margin_threshold,
            overrides=rules.semantic_threshold_overrides,
        )
        sem = classify_semantic_domain_detail(
            rel_path,
            core,
            sample,
            rules,
            score_threshold=sem_score_threshold,
            margin_threshold=sem_margin_threshold,
        )
        if _sem_override_id and _sem_override_id != "default":
            if notes:
                notes = f"{notes}; "
            notes = f"{notes}semantic_threshold_override={_sem_override_id}"
        sem_dom, sem_score = sem.domain, int(sem.score)
        art_type = classify_artifact_type(rel_path, doc_type=doc_type, ext=ext, core=core, sample=sample, rules=rules)

        myear, mmonth, mdecade = mtime_facets(mdate)
        ox_created_year = parse_openxml_dt_year(core.get("created", ""))
        ox_modified_year = parse_openxml_dt_year(core.get("modified", ""))

        flags = sensitivity_flags_with_rules(rel_path, sample, doc_type=doc_type, ext=ext, rules=rules)
        slevel = sensitivity_level_from_rules(flags, rules)
        tags = tagger(rel_path, sample, core.get("title", ""), core.get("subject", ""))
        ents = extract_entity_facets(rel_path, core, sample)

        sh = sha256_file(p) if args.hash else ""

        doc = DocRecord(
            schema_version=SCHEMA_VERSION,
            path=rel_path,
            source_root=sr.id,
            first_bucket=fb,
            sub_bucket=sb,
            semantic_domain=sem_dom,
            semantic_domain_score=int(sem_score),
            semantic_domain_margin=int(sem.margin),
            semantic_decision=sem.decision,
            semantic_abstain_reason=sem.abstain_reason,
            semantic_top_candidates=list(sem.top_candidates),
            ext=ext,
            size_bytes=size_bytes,
            mtime_date=mdate,
            sha256=sh,
            openxml_core={k: ascii_sanitize(v) for k, v in core.items() if v},
            sample_text=ascii_sanitize(sample),
            sample_truncated=len(sample_before_redaction) >= int(args.sample_chars),
            sample_method=method,
            extraction_notes=ascii_sanitize(notes),
            _first_bucket_prefix=ascii_sanitize(fb_prefix),
            _sub_bucket_prefix=ascii_sanitize(sb_prefix),
            doc_type=doc_type,
            artifact_type=art_type,
            mtime_year=myear,
            mtime_month=mmonth,
            mtime_decade=mdecade,
            openxml_created_year=ox_created_year,
            openxml_modified_year=ox_modified_year,
            sensitivity_flags=flags,
            sensitivity_level=slevel,
            tags=tags,
            entities_systems=list(ents.get("systems", [])),
            entities_vendors=list(ents.get("vendors", [])),
            entities_projects=list(ents.get("projects", [])),
            entities_locations=list(ents.get("locations", [])),
            entities_people=list(ents.get("people", [])),
            duplicate_kind="unique",
            duplicate_cluster_id="",
            duplicate_cluster_size=1,
        )
        docs.append(doc)
        if state_conn is not None:
            _state_store_doc(
                state_conn,
                path=rel_path,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                run_sig=run_sig,
                doc=doc,
            )

    if state_conn is not None:
        state_conn.commit()

    # 5) Derive cross-document signals
    dedupe_clusters = assign_duplicate_clusters(docs, near_hamming_threshold=int(args.near_dup_hamming))
    bucket_cards = build_bucket_cards(docs, samples_per_bucket=int(args.bucket_card_samples)) if emit_bucket_cards else []
    fallback_candidates = []
    if emit_fallback_candidates:
        for d in docs:
            if d.semantic_decision != "abstained":
                continue
            fallback_candidates.append(
                {
                    "path": d.path,
                    "source_root": d.source_root,
                    "semantic_domain_default": d.semantic_domain,
                    "semantic_score": int(d.semantic_domain_score),
                    "semantic_margin": int(d.semantic_domain_margin),
                    "abstain_reason": d.semantic_abstain_reason,
                    "top_candidates": list(d.semantic_top_candidates),
                    "doc_type": d.doc_type,
                    "artifact_type": d.artifact_type,
                }
            )

    calibrator_result: Dict[str, Any] = {}
    candidate_rules_obj: Optional[Dict[str, Any]] = None
    if calibrator_enabled:
        calibrator_result = run_corpus_calibrator(
            docs=docs,
            rules=rules,
            provider=calibrator_provider,
            model=calibrator_model,
            api_key=calibrator_api_key,
            base_url=calibrator_base_url,
            api_key_env=calibrator_api_key_env,
            dry_run=calibrator_dry_run,
            timeout_sec=calibrator_timeout_sec,
            temperature=calibrator_temperature,
            max_output_tokens=calibrator_max_output_tokens,
            insecure_tls=calibrator_insecure_tls,
            max_buckets=calibrator_max_buckets,
            samples_per_bucket=calibrator_samples_per_bucket,
            max_snippet_chars=calibrator_max_snippet_chars,
            doc_selection_mode=calibrator_doc_selection,
            low_score_threshold=calibrator_low_score_threshold,
            low_margin_threshold=calibrator_low_margin_threshold,
        )
        if isinstance(calibrator_result.get("candidate_rules"), dict):
            candidate_rules_obj = calibrator_result["candidate_rules"]

    # 6) Build derived artifacts
    inv = build_inventory(docs, extractions)
    router_obj = build_router(
        docs,
        include_keywords=include_router_keywords,
        keywords_source=keywords_source,
        rules=rules,
        sample_mode=sample_mode,
        domain_score_threshold=domain_score_threshold,
        domain_margin_threshold=domain_margin_threshold,
        semantic_threshold_overrides=rules.semantic_threshold_overrides,
    )
    facets_obj = facet_definitions(rules=rules)
    facet_summary = build_facet_summary(docs)

    schema_dir = out_dir / "schema"
    schema_paths = write_schemas(schema_dir, rules=rules)

    # 7) Write machine-readable artifacts
    write_json(out_dir / "inventory.json", inv)
    write_json(out_dir / "router.json", router_obj)
    write_json(out_dir / "facets.json", facets_obj)
    rules_used = dict(rules.raw)
    rules_used["_meta"] = {
        "source": rules.source,
        "sha256": rules.sha256,
        "generated_at": now_iso(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "schema_version": SCHEMA_VERSION,
    }
    write_json(out_dir / "rules_used.json", rules_used)
    write_json(out_dir / "backends.json", {"tool": {"name": TOOL_NAME, "version": TOOL_VERSION}, "generated_at": now_iso(), "backends": backends})
    write_json(out_dir / "dedupe_clusters.json", {"schema_version": SCHEMA_VERSION, "generated_at": now_iso(), "clusters": dedupe_clusters})
    if emit_bucket_cards:
        write_jsonl(out_dir / "bucket_cards.jsonl", bucket_cards)
    if emit_fallback_candidates:
        write_jsonl(out_dir / "semantic_fallback_candidates.jsonl", fallback_candidates)
    if calibrator_enabled:
        write_json(out_dir / "corpus_calibrator_report.json", calibrator_result)
        if candidate_rules_obj is not None:
            write_json(out_dir / "rules_candidate.json", candidate_rules_obj)
    write_jsonl(out_dir / "documents.jsonl", (d.to_json_obj() for d in docs))

    # 8) Write human-friendly TSVs
    profiles_rows: List[Dict[str, Any]] = []
    for d in docs:
        profiles_rows.append(
            {
                "path": d.path,
                "source_root": d.source_root,
                "first_bucket": d.first_bucket,
                "sub_bucket": d.sub_bucket,
                "semantic_domain": d.semantic_domain,
                "semantic_domain_score": int(d.semantic_domain_score),
                "semantic_domain_margin": int(d.semantic_domain_margin),
                "semantic_decision": d.semantic_decision,
                "semantic_abstain_reason": d.semantic_abstain_reason,
                "ext": d.ext,
                "size_bytes": d.size_bytes,
                "mtime_date": d.mtime_date,
                "doc_type": d.doc_type,
                "artifact_type": d.artifact_type,
                "openxml_title": d.openxml_core.get("title", ""),
                "openxml_subject": d.openxml_core.get("subject", ""),
                "openxml_creator": d.openxml_core.get("creator", ""),
                "openxml_created": d.openxml_core.get("created", ""),
                "openxml_modified": d.openxml_core.get("modified", ""),
                "sensitivity_level": d.sensitivity_level,
                "sensitivity_flags": ",".join(d.sensitivity_flags),
                "tags": ",".join(d.tags),
                "entities_systems": ",".join(d.entities_systems),
                "entities_vendors": ",".join(d.entities_vendors),
                "entities_projects": ",".join(d.entities_projects),
                "entities_locations": ",".join(d.entities_locations),
                "entities_people": ",".join(d.entities_people),
                "duplicate_kind": d.duplicate_kind,
                "duplicate_cluster_id": d.duplicate_cluster_id,
                "duplicate_cluster_size": int(d.duplicate_cluster_size),
                "sample": d.sample_text,
            }
        )

    write_tsv(
        out_dir / "document_profiles.tsv",
        profiles_rows,
        cols=[
            "path",
            "source_root",
            "first_bucket",
            "sub_bucket",
            "semantic_domain",
            "semantic_domain_score",
            "semantic_domain_margin",
            "semantic_decision",
            "semantic_abstain_reason",
            "ext",
            "size_bytes",
            "mtime_date",
            "doc_type",
            "artifact_type",
            "openxml_title",
            "openxml_subject",
            "openxml_creator",
            "openxml_created",
            "openxml_modified",
            "sensitivity_level",
            "sensitivity_flags",
            "tags",
            "entities_systems",
            "entities_vendors",
            "entities_projects",
            "entities_locations",
            "entities_people",
            "duplicate_kind",
            "duplicate_cluster_id",
            "duplicate_cluster_size",
            "sample",
        ],
    )

    write_tsv(out_dir / "facet_summary.tsv", facet_summary, cols=["facet", "value", "count"])

    # Bucket summary TSV
    sb_summary = Counter()
    sb_bytes = Counter()
    for d in docs:
        k = (d.first_bucket, d.sub_bucket)
        sb_summary[k] += 1
        sb_bytes[k] += d.size_bytes

    sb_rows = []
    for (fb, sb), cnt in sorted(sb_summary.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])):
        sb_rows.append(
            {
                "first_bucket": fb,
                "sub_bucket": sb,
                "count_files": cnt,
                "total_bytes": int(sb_bytes[(fb, sb)]),
            }
        )
    write_tsv(out_dir / "subbucket_summary.tsv", sb_rows, cols=["first_bucket", "sub_bucket", "count_files", "total_bytes"])

    # Overview markdown
    (out_dir / "overview.md").write_text(build_overview_md(root, docs, inv, router_obj), encoding="utf-8")

    benchmark_created_path = ""
    benchmark_metrics_path = ""
    benchmark_metrics: Dict[str, Any] = {}
    benchmark_rows_for_eval: List[Dict[str, Any]] = []
    benchmark_eval_path: Optional[Path] = None

    def _resolve_user_path(raw: str, default_path: Path) -> Path:
        txt = str(raw or "").strip()
        if not txt:
            return default_path
        up = Path(txt).expanduser()
        return up.resolve() if up.is_absolute() else (root / up).resolve()

    if benchmark_create:
        bench_create_path = _resolve_user_path(benchmark_create, out_dir / "benchmark_labeled.jsonl")
        bench_rows = create_fixed_benchmark(
            docs,
            size=benchmark_size,
            min_score=benchmark_min_score,
            min_margin=benchmark_min_margin,
        )
        write_jsonl(bench_create_path, bench_rows)
        benchmark_created_path = norm_path(str(bench_create_path))

    bench_eval_path_raw = benchmark_file or benchmark_create
    if bench_eval_path_raw:
        benchmark_eval_path = _resolve_user_path(bench_eval_path_raw, out_dir / "benchmark_labeled.jsonl")
        if benchmark_eval_path.exists():
            benchmark_rows_for_eval = _load_benchmark_rows(benchmark_eval_path)
            benchmark_metrics = evaluate_labeled_benchmark(benchmark_rows_for_eval, _current_docs_index(docs))
            benchmark_metrics["benchmark_file"] = norm_path(str(benchmark_eval_path))
            benchmark_metrics_out = _resolve_user_path(benchmark_report, out_dir / "benchmark_metrics.json")
            write_json(benchmark_metrics_out, benchmark_metrics)
            benchmark_metrics_path = norm_path(str(benchmark_metrics_out))

    acceptance_metrics: Dict[str, Any] = {}
    acceptance_report_path = ""
    acceptance_gate_failed = False
    if acceptance_baseline:
        baseline_rows = _load_docs_index(acceptance_baseline)
        baseline_benchmark_metrics: Dict[str, Any] = {}
        if benchmark_rows_for_eval:
            baseline_benchmark_metrics = evaluate_labeled_benchmark(benchmark_rows_for_eval, baseline_rows)
            if benchmark_eval_path is not None:
                baseline_benchmark_metrics["benchmark_file"] = norm_path(str(benchmark_eval_path))
        acceptance_metrics = compute_acceptance_report(
            current_rows=_current_docs_index(docs),
            baseline_rows=baseline_rows,
            min_routed_gain=acceptance_min_routed_gain,
            max_routed_to_abstained=acceptance_max_routed_to_abstained,
            max_itops_share_drift_pp=acceptance_max_itops_share_drift_pp,
            benchmark_current=benchmark_metrics,
            benchmark_baseline=baseline_benchmark_metrics,
            min_route_precision=acceptance_min_route_precision,
            min_domain_precision=acceptance_min_domain_precision,
            max_precision_drop_vs_baseline=acceptance_max_precision_drop_vs_baseline,
        )
        acceptance_out = _resolve_user_path(acceptance_report, out_dir / "acceptance_report.json")

        promotion: Dict[str, Any] = {"requested": bool(promote_rules_to), "promoted": False, "reason": ""}
        if promote_rules_to:
            if not acceptance_metrics.get("pass", False):
                promotion["reason"] = "gates_failed"
            elif not args.rules:
                promotion["reason"] = "no_rules_source_provided"
            else:
                src_rules = Path(str(args.rules)).expanduser()
                if not src_rules.is_absolute():
                    src_rules = (root / src_rules).resolve()
                dst_rules = _resolve_user_path(promote_rules_to, root / "rules_active.json")
                safe_mkdir(dst_rules.parent)
                shutil.copyfile(src_rules, dst_rules)
                promotion["promoted"] = True
                promotion["reason"] = "ok"
                promotion["target"] = norm_path(str(dst_rules))
                promotion["source"] = norm_path(str(src_rules))

        acceptance_metrics["promotion"] = promotion
        write_json(acceptance_out, acceptance_metrics)
        acceptance_report_path = norm_path(str(acceptance_out))
        if acceptance_fail_on_gate and not bool(acceptance_metrics.get("pass", False)):
            acceptance_gate_failed = True

    # Manifest
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "input_root": norm_path(str(root)),
        "output_dir": norm_path(str(out_dir.relative_to(root))),
        "parameters": {
            "extract_archives": bool(args.extract_archives),
            "archives_recursive": bool(args.archives_recursive),
            "include_hidden": include_hidden,
            "bucket_depth": int(args.bucket_depth),
            "sample_chars": int(args.sample_chars),
            "pdf_max_pages": int(args.pdf_max_pages),
            "pptx_max_slides": int(args.pptx_max_slides),
            "xlsx_max_strings": int(args.xlsx_max_strings),
            "xlsx_max_chars": int(args.xlsx_max_chars),
            "zip_max_members": int(args.zip_max_members),
            "binary_max_bytes": int(args.binary_max_bytes),
            "hash": bool(args.hash),
            "max_files": int(args.max_files or 0),
            "container_dirs": sorted(container_dirs),
            "exclude_dirs": sorted(exclude_dirs),
            "exclude_profile": exclude_profile,
            "include_source_code": include_source_code,
            "include_exts": sorted(include_exts),
            "exclude_exts": sorted(exclude_exts),
            "exclude_basenames": sorted(exclude_basenames),
            "exclude_path_regex_count": len(exclude_path_regex),
            "rules_source": rules.source,
            "rules_sha256": rules.sha256,
            "sample_mode": sample_mode,
            "keywords_source": keywords_source,
            "no_router_keywords": bool(args.no_router_keywords),
            "domain_score_threshold": int(domain_score_threshold),
            "domain_margin_threshold": int(domain_margin_threshold),
            "semantic_threshold_overrides": len(rules.semantic_threshold_overrides),
            "incremental": bool(args.incremental),
            "state_db": norm_path(str(state_db.relative_to(root))) if state_db.is_absolute() and str(state_db).startswith(str(root)) else norm_path(str(state_db)),
            "cache_hits": int(cache_hits),
            "cache_misses": int(cache_misses),
            "near_dup_hamming": int(args.near_dup_hamming),
            "bucket_card_samples": int(args.bucket_card_samples),
            "emit_bucket_cards": bool(emit_bucket_cards),
            "emit_fallback_candidates": bool(emit_fallback_candidates),
            "corpus_calibrator": bool(calibrator_enabled),
            "calibrator_provider": calibrator_provider,
            "calibrator_model": calibrator_model,
            "calibrator_base_url": calibrator_base_url,
            "calibrator_api_key_env": calibrator_api_key_env,
            "calibrator_timeout_sec": int(calibrator_timeout_sec),
            "calibrator_insecure_tls": bool(calibrator_insecure_tls),
            "calibrator_temperature": float(calibrator_temperature),
            "calibrator_max_output_tokens": int(calibrator_max_output_tokens),
            "calibrator_max_buckets": int(calibrator_max_buckets),
            "calibrator_samples_per_bucket": int(calibrator_samples_per_bucket),
            "calibrator_max_snippet_chars": int(calibrator_max_snippet_chars),
            "calibrator_doc_selection": str(calibrator_doc_selection),
            "calibrator_low_score_threshold": int(calibrator_low_score_threshold),
            "calibrator_low_margin_threshold": int(calibrator_low_margin_threshold),
            "calibrator_dry_run": bool(calibrator_dry_run),
            "calibrator_status": str(calibrator_result.get("status", "")) if calibrator_enabled else "",
            "benchmark_create": benchmark_create,
            "benchmark_file": benchmark_file,
            "benchmark_size": int(benchmark_size),
            "benchmark_min_score": int(benchmark_min_score),
            "benchmark_min_margin": int(benchmark_min_margin),
            "acceptance_baseline": acceptance_baseline,
            "acceptance_min_routed_gain": int(acceptance_min_routed_gain),
            "acceptance_max_routed_to_abstained": int(acceptance_max_routed_to_abstained),
            "acceptance_max_itops_share_drift_pp": float(acceptance_max_itops_share_drift_pp),
            "acceptance_min_route_precision": (
                float(acceptance_min_route_precision) if acceptance_min_route_precision is not None else None
            ),
            "acceptance_min_domain_precision": (
                float(acceptance_min_domain_precision) if acceptance_min_domain_precision is not None else None
            ),
            "acceptance_max_precision_drop_vs_baseline": (
                float(acceptance_max_precision_drop_vs_baseline)
                if acceptance_max_precision_drop_vs_baseline is not None
                else None
            ),
            "acceptance_fail_on_gate": bool(acceptance_fail_on_gate),
            "promote_rules_to": promote_rules_to,
            "backends": backends,
        },
        "counts": {"documents": len(docs), "bytes_total": int(inv.get("bytes_total", 0))},
        "artifacts": {
            "inventory_json": norm_path(str((out_dir / "inventory.json").relative_to(root))),
            "router_json": norm_path(str((out_dir / "router.json").relative_to(root))),
            "facets_json": norm_path(str((out_dir / "facets.json").relative_to(root))),
            "rules_used_json": norm_path(str((out_dir / "rules_used.json").relative_to(root))),
            "backends_json": norm_path(str((out_dir / "backends.json").relative_to(root))),
            "dedupe_clusters_json": norm_path(str((out_dir / "dedupe_clusters.json").relative_to(root))),
            "documents_jsonl": norm_path(str((out_dir / "documents.jsonl").relative_to(root))),
            "document_profiles_tsv": norm_path(str((out_dir / "document_profiles.tsv").relative_to(root))),
            "subbucket_summary_tsv": norm_path(str((out_dir / "subbucket_summary.tsv").relative_to(root))),
            "facet_summary_tsv": norm_path(str((out_dir / "facet_summary.tsv").relative_to(root))),
            "overview_md": norm_path(str((out_dir / "overview.md").relative_to(root))),
            "schema_dir": norm_path(str(schema_dir.relative_to(root))),
            "schemas": {k: norm_path(str(Path(v).relative_to(root))) for k, v in schema_paths.items()},
        },
    }
    if emit_bucket_cards:
        manifest["artifacts"]["bucket_cards_jsonl"] = norm_path(str((out_dir / "bucket_cards.jsonl").relative_to(root)))
    if emit_fallback_candidates:
        manifest["artifacts"]["semantic_fallback_candidates_jsonl"] = norm_path(
            str((out_dir / "semantic_fallback_candidates.jsonl").relative_to(root))
        )
    if calibrator_enabled:
        manifest["artifacts"]["corpus_calibrator_report_json"] = norm_path(
            str((out_dir / "corpus_calibrator_report.json").relative_to(root))
        )
        if candidate_rules_obj is not None:
            manifest["artifacts"]["rules_candidate_json"] = norm_path(str((out_dir / "rules_candidate.json").relative_to(root)))
    if benchmark_created_path:
        manifest["artifacts"]["benchmark_labeled_jsonl"] = benchmark_created_path
    if benchmark_metrics_path:
        manifest["artifacts"]["benchmark_metrics_json"] = benchmark_metrics_path
    if acceptance_report_path:
        manifest["artifacts"]["acceptance_report_json"] = acceptance_report_path
    write_json(out_dir / "manifest.json", manifest)

    if state_conn is not None:
        state_conn.commit()
        state_conn.close()

    if acceptance_gate_failed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
