#!/usr/bin/env python3
"""
Simple retrieval calibration sweep over top-k values.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

LOGGER = logging.getLogger("calibrate_retrieval")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Calibrate retrieval settings via automated sweep")
    ap.add_argument("--eval-file", default="evals/retrieval/queries.sample.jsonl")
    ap.add_argument("--top-k-values", default="5,10,20,30")
    ap.add_argument("--backend", choices=["query-api", "hybrid"], default="query-api")
    ap.add_argument("--query-api-url", default="http://127.0.0.1:8080/retrieve")
    ap.add_argument("--mode", choices=["basic", "local", "global", "drift"], default="basic")
    ap.add_argument(
        "--modes",
        default="",
        help="Optional comma-separated override for mode sweep, e.g. local,global,drift",
    )
    ap.add_argument("--es-url", default="http://127.0.0.1:9200")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--embed-url", default="http://127.0.0.1:8001/v1/embeddings")
    ap.add_argument("--embed-model", default="Qwen/Qwen3-Embedding-8B")
    ap.add_argument("--embed-dim", type=int, default=4096)
    ap.add_argument(
        "--embed-query-instruction",
        default="Given a web search query, retrieve relevant passages that answer the query",
    )
    ap.add_argument("--embed-timeout-sec", type=int, default=600)
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args()


def parse_topk_values(raw: str) -> List[int]:
    out: List[int] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value > 0:
            out.append(value)
    if not out:
        raise ValueError("No valid top-k values provided")
    return out


def parse_modes(args: argparse.Namespace) -> List[str]:
    raw = str(args.modes or "").strip()
    if not raw:
        return [str(args.mode)]
    out: List[str] = []
    for part in raw.split(","):
        mode = str(part).strip().lower()
        if mode in {"basic", "local", "global", "drift"} and mode not in out:
            out.append(mode)
    if not out:
        out = [str(args.mode)]
    return out


def run_eval(top_k: int, mode: str, args: argparse.Namespace) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "scripts/eval_retrieval.py",
        "--eval-file",
        args.eval_file,
        "--backend",
        args.backend,
        "--query-api-url",
        args.query_api_url,
        "--mode",
        str(mode),
        "--top-k",
        str(top_k),
        "--es-url",
        args.es_url,
        "--qdrant-url",
        args.qdrant_url,
        "--embed-url",
        args.embed_url,
        "--embed-model",
        args.embed_model,
        "--embed-dim",
        str(args.embed_dim),
        "--embed-query-instruction",
        args.embed_query_instruction,
        "--embed-timeout-sec",
        str(args.embed_timeout_sec),
        "--log-level",
        args.log_level,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if not stdout:
        raise RuntimeError(f"eval_retrieval produced no stdout for top_k={top_k}. stderr={stderr}")

    report = json.loads(stdout)
    report["_exit_code"] = int(completed.returncode)
    report["_stderr"] = stderr
    report["_top_k"] = int(top_k)
    report["_mode"] = str(mode)
    return report


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    top_k_values = parse_topk_values(args.top_k_values)
    mode_values = parse_modes(args)

    runs: List[Dict[str, Any]] = []
    for mode in mode_values:
        for top_k in top_k_values:
            LOGGER.info("Running retrieval eval sweep for mode=%s top_k=%d", mode, top_k)
            try:
                report = run_eval(top_k, mode, args)
                runs.append(report)
            except Exception as exc:
                runs.append(
                    {
                        "_mode": str(mode),
                        "_top_k": int(top_k),
                        "_exit_code": 2,
                        "summary": {"pass_rate": 0.0, "total": 0, "passed": 0},
                        "error": str(exc),
                    }
                )

    scored_runs: List[Dict[str, Any]] = []
    for run in runs:
        summary = run.get("summary", {}) if isinstance(run.get("summary"), dict) else {}
        pass_rate = float(summary.get("pass_rate", 0.0) or 0.0)
        total = int(summary.get("total", 0) or 0)
        passed = int(summary.get("passed", 0) or 0)
        scored_runs.append(
            {
                "mode": str(run.get("_mode", args.mode)),
                "top_k": safe_int(run.get("_top_k", 0), 0),
                "exit_code": safe_int(run.get("_exit_code", 2), 2),
                "pass_rate": pass_rate,
                "passed": passed,
                "total": total,
                "report": run,
            }
        )

    # Prefer highest pass_rate, then highest passed count, then smallest top_k.
    best = sorted(
        scored_runs,
        key=lambda r: (-r["pass_rate"], -r["passed"], r["top_k"], r["mode"]),
    )[0]

    output = {
        "eval_file": str(Path(args.eval_file).expanduser().resolve()),
        "candidates": [
            {
                "mode": r["mode"],
                "top_k": r["top_k"],
                "pass_rate": r["pass_rate"],
                "passed": r["passed"],
                "total": r["total"],
            }
            for r in scored_runs
        ],
        "best": {
            "mode": best["mode"],
            "top_k": best["top_k"],
            "pass_rate": best["pass_rate"],
            "passed": best["passed"],
            "total": best["total"],
            "exit_code": best["exit_code"],
        },
    }
    print(json.dumps(output, indent=2))

    # Write a local artifact for repeatability.
    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "retrieval_calibration.json"
    out_file.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Wrote calibration report: %s", out_file)

    return 0 if int(best["exit_code"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
