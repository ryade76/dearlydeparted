#!/usr/bin/env python3
"""
Regression trace capture runner for Dearly Departed v2.

Runs regression questions against the live v2 agent and captures:
  - The question
  - The full agent response (answer, citations, timing, diagnostics)
  - The complete server-side trace (tool calls, evidence, verification)
  - Evaluation scores against expected criteria

Output is append-mode JSONL — one record per question, crash-safe.
Supports --resume to skip already-completed questions.

Usage:
    python scripts/run_regression_traces.py \\
      --tasks evals/v2/regression/set_1.jsonl \\
      --url http://localhost:8082 \\
      --output reports/regression/set_1_traces.jsonl \\
      --concurrency 1 \\
      --timeout 300 \\
      --resume
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import re

import requests

# Import scoring config if available
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from evals.v2.eval_config import SCORING, GATES, CITATION_VERIFICATION_GATE
except ImportError:
    SCORING = {
        "must_include_terms": {"weight": 0.3},
        "must_not_include_terms": {"weight": 0.1},
        "must_cite_paths": {"weight": 0.3},
        "min_citations": {"weight": 0.2},
        "expected_intent": {"weight": 0.1},
    }
    GATES = {"overall": 0.75}
    CITATION_VERIFICATION_GATE = 0.90


def load_tasks(path: str) -> List[Dict[str, Any]]:
    """Load tasks from JSONL file."""
    tasks = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def load_completed_ids(output_path: str) -> Set[str]:
    """Load task IDs already in the output file (for resume)."""
    completed = set()
    if not os.path.exists(output_path):
        return completed
    with open(output_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    record = json.loads(line)
                    completed.add(record.get("task_id", ""))
                except json.JSONDecodeError:
                    continue
    return completed


def _path_stem(path: str) -> str:
    """Extract normalized filename stem for fuzzy path matching.
    Strips version numbers and date suffixes to match document revisions.
    """
    name = path.rsplit("/", 1)[-1].lower()
    name = re.sub(r'\.[^.]+$', '', name)  # strip extension
    # Strip date suffixes FIRST (before version numbers)
    name = re.sub(r'[-_]\d{1,2}[a-z]{3}\d{2,4}$', '', name)  # _22Dec2016
    name = re.sub(r'[-_]\d{4}[a-z]{3}\d{1,2}$', '', name)    # _2017Jun30
    # Strip trailing 1-2 digit version only after a letter (prevents IMG_0685 → IMG)
    name = re.sub(r'(?<=[a-z])[-_]?\d{1,2}$', '', name)
    return name.strip('_- ')


def score_response(
    task: Dict[str, Any],
    answer: str,
    citations: List[Dict],
    diagnostics: Dict[str, Any],
    trace: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Score the response against expected criteria."""
    expected = task.get("expected", {})
    scores: Dict[str, float] = {}

    # must_include_terms
    must_include = expected.get("must_include_terms", [])
    if must_include:
        found = sum(1 for t in must_include if t.lower() in answer.lower())
        scores["must_include_terms"] = found / len(must_include)
    else:
        scores["must_include_terms"] = 1.0

    # must_not_include_terms
    must_not = expected.get("must_not_include_terms", [])
    if must_not:
        found = sum(1 for t in must_not if t.lower() in answer.lower())
        scores["must_not_include_terms"] = 1.0 - (found / len(must_not))
    else:
        scores["must_not_include_terms"] = 1.0

    # must_cite_paths
    must_cite = expected.get("must_cite_paths", [])
    if must_cite:
        cited_paths = {c.get("path", "") for c in citations}
        path_scores = []
        for p in must_cite:
            # Exact substring match (existing behavior) → 1.0
            if any(p.lower() in cp.lower() for cp in cited_paths):
                path_scores.append(1.0)
                continue
            # Filename-stem fallback → 0.5 partial credit
            exp_stem = _path_stem(p)
            if exp_stem and any(_path_stem(cp) == exp_stem for cp in cited_paths):
                path_scores.append(0.5)
                continue
            path_scores.append(0.0)
        scores["must_cite_paths"] = sum(path_scores) / len(path_scores)
    else:
        scores["must_cite_paths"] = 1.0

    # min_citations
    min_cit = expected.get("min_citations", 0)
    if min_cit > 0:
        verified_count = sum(1 for c in citations if c.get("verified", False))
        scores["min_citations"] = min(1.0, verified_count / min_cit)
    else:
        scores["min_citations"] = 1.0

    # expected_intent
    exp_intent = expected.get("expected_intent", "")
    if exp_intent:
        # Intent is in the trace, not in diagnostics
        actual_intent = (trace or {}).get("intent", "") or diagnostics.get("intent", "")
        scores["expected_intent"] = 1.0 if actual_intent == exp_intent else 0.0
    else:
        scores["expected_intent"] = 1.0

    # Composite
    composite = sum(scores.get(k, 0) * v["weight"] for k, v in SCORING.items() if k in scores)
    passed = composite >= 0.5

    return {
        "must_include_terms": round(scores.get("must_include_terms", 0), 3),
        "must_not_include_terms": round(scores.get("must_not_include_terms", 0), 3),
        "must_cite_paths": round(scores.get("must_cite_paths", 0), 3),
        "min_citations": round(scores.get("min_citations", 0), 3),
        "expected_intent": round(scores.get("expected_intent", 0), 3),
        "composite": round(composite, 3),
        "passed": passed,
    }


def fetch_trace(url: str, trace_id: str, max_retries: int = 3,
                backoff: float = 2.0) -> Optional[Dict[str, Any]]:
    """Fetch the persisted trace with retries (trace persistence is async)."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(f"{url}/v2/trace/{trace_id}", timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404 and attempt < max_retries:
                # Trace may not be persisted yet
                time.sleep(backoff * attempt)
                continue
            return None
        except Exception:
            if attempt < max_retries:
                time.sleep(backoff * attempt)
                continue
            return None
    return None


def run_single_task(
    url: str,
    task: Dict[str, Any],
    set_index: int,
    timeout: int = 300,
    fetch_traces: bool = True,
) -> Dict[str, Any]:
    """Run a single task, capture response + trace, score it."""
    task_id = task.get("id", "unknown")
    question = task.get("question", "")
    category = task.get("category", "")

    payload = {
        "query": question,
        "domain": task.get("domain_hint", ""),
        "top_k": 40,
        "strategy": "auto",
    }
    # Add source_path_hint if present
    if task.get("source_path_hint"):
        payload["source_path_hint"] = task["source_path_hint"]

    t0 = time.time()
    error_msg = ""
    response_data: Dict[str, Any] = {}
    trace_data: Dict[str, Any] = {}

    try:
        resp = requests.post(f"{url}/v2/ask", json=payload, timeout=timeout)
        elapsed = time.time() - t0

        if resp.status_code >= 300:
            error_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
        else:
            response_data = resp.json()
    except requests.exceptions.Timeout:
        elapsed = time.time() - t0
        error_msg = f"Timeout after {timeout}s"
    except Exception as exc:
        elapsed = time.time() - t0
        error_msg = str(exc)

    # Extract response fields
    answer = response_data.get("answer", "")
    citations = response_data.get("citations", [])
    trace_id = response_data.get("trace_id", "")
    timing = response_data.get("timing", {})
    diagnostics = response_data.get("diagnostics", {})
    retrieval = response_data.get("retrieval", {})
    model = response_data.get("model", "")

    # Fetch full trace from server
    if trace_id and fetch_traces:
        trace_data = fetch_trace(url, trace_id) or {}

    # Score
    eval_scores = score_response(task, answer, citations, diagnostics, trace_data)

    # Count tool calls from trace
    tool_calls = trace_data.get("tool_call_log", [])
    tool_count = len(tool_calls)

    record = {
        "task_id": task_id,
        "set_index": set_index,
        "category": category,
        "question": question,
        "domain_hint": task.get("domain_hint", ""),
        "expected": task.get("expected", {}),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_sec": round(elapsed, 2),
        "response": {
            "answer": answer,
            "citations": citations,
            "model": model,
            "trace_id": trace_id,
            "timing": timing,
            "diagnostics": diagnostics,
            "retrieval": retrieval,
        },
        "trace": {
            "trace_id": trace_data.get("trace_id", trace_id),
            "intent": trace_data.get("intent", diagnostics.get("intent", "")),
            "complexity": trace_data.get("complexity", diagnostics.get("complexity", "")),
            "strategy": trace_data.get("strategy", diagnostics.get("strategy", "")),
            "taxonomy_hints": trace_data.get("taxonomy_hints", {}),
            "tool_call_log": tool_calls,
            "citations": trace_data.get("citations", citations),
            "state_timings": trace_data.get("state_timings", timing),
            "context_token_count": trace_data.get("context_token_count", 0),
            "evidence_count": trace_data.get("evidence_count", 0),
            "verification_passed": trace_data.get("verification_passed", False),
            "retry_count": trace_data.get("retry_count", 0),
            "error_message": trace_data.get("error_message", ""),
            "plan_steps": trace_data.get("plan_steps", []),
            "graph_traversal_count": trace_data.get("graph_traversal_count", 0),
            "entity_profile_count": trace_data.get("entity_profile_count", 0),
        },
        "eval_scores": eval_scores,
        "error": error_msg,
    }

    return record


def write_summary(
    results: List[Dict[str, Any]],
    output_path: str,
    set_index: int,
    url: str,
    total_elapsed: float,
) -> None:
    """Write aggregate summary JSON."""
    total = len(results)
    passed = sum(1 for r in results if r["eval_scores"].get("passed", False))
    errors = sum(1 for r in results if r["error"])

    # Category breakdown
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    cat_summary = {}
    for cat, cat_results in sorted(by_cat.items()):
        cat_passed = sum(1 for r in cat_results if r["eval_scores"].get("passed", False))
        cat_total = len(cat_results)
        cat_rate = cat_passed / cat_total if cat_total > 0 else 0
        cat_summary[cat] = {
            "passed": cat_passed,
            "total": cat_total,
            "pass_rate": round(cat_rate, 3),
            "avg_elapsed_sec": round(
                sum(r["elapsed_sec"] for r in cat_results) / cat_total, 2) if cat_total else 0,
            "error_count": sum(1 for r in cat_results if r["error"]),
        }

    # Tool usage stats
    tool_counts: Dict[str, int] = defaultdict(int)
    tool_durations: Dict[str, List[float]] = defaultdict(list)
    for r in results:
        for tc in r.get("trace", {}).get("tool_call_log", []):
            name = tc.get("tool_name", "unknown")
            tool_counts[name] += 1
            tool_durations[name].append(tc.get("duration_ms", 0))

    tool_summary = {}
    for name in sorted(tool_counts.keys()):
        durations = tool_durations[name]
        tool_summary[name] = {
            "call_count": tool_counts[name],
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0,
            "max_duration_ms": round(max(durations), 1) if durations else 0,
        }

    # Citation stats
    total_citations = sum(len(r["response"].get("citations", [])) for r in results)
    total_verified = sum(
        sum(1 for c in r["response"].get("citations", []) if c.get("verified", False))
        for r in results
    )

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "set_index": set_index,
        "url": url,
        "total_questions": total,
        "total_passed": passed,
        "total_errors": errors,
        "overall_pass_rate": round(passed / total, 3) if total else 0,
        "total_elapsed_sec": round(total_elapsed, 1),
        "avg_elapsed_sec": round(total_elapsed / total, 2) if total else 0,
        "citation_total": total_citations,
        "citation_verified": total_verified,
        "citation_verification_rate": round(
            total_verified / total_citations, 3) if total_citations else 1.0,
        "category_summary": cat_summary,
        "tool_summary": tool_summary,
    }

    summary_path = output_path.replace("_traces.jsonl", "_summary.json")
    os.makedirs(os.path.dirname(summary_path) or ".", exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to: {summary_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Run regression questions and capture full traces")
    parser.add_argument("--tasks", required=True,
                        help="Path to regression JSONL file")
    parser.add_argument("--url", default="http://localhost:8082",
                        help="v2 agent API URL")
    parser.add_argument("--output", default="",
                        help="Output JSONL path (default: auto from tasks path)")
    parser.add_argument("--set-index", type=int, default=0,
                        help="Set index for metadata (auto-detected from filename if 0)")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Parallel requests (default 1)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Per-question timeout in seconds")
    parser.add_argument("--resume", action="store_true",
                        help="Skip questions already in output file")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N questions (0 = all)")
    parser.add_argument("--no-traces", action="store_true",
                        help="Skip fetching server-side traces (faster)")
    parser.add_argument("--delay", type=float, default=0,
                        help="Delay between requests in seconds")
    args = parser.parse_args()

    # Auto-detect set index from filename
    set_index = args.set_index
    if set_index == 0:
        import re as _re
        m = _re.search(r'set_(\d+)', args.tasks)
        set_index = int(m.group(1)) if m else 1

    # Auto-generate output path
    output_path = args.output
    if not output_path:
        output_path = f"reports/regression/set_{set_index}_traces.jsonl"

    # Load tasks
    tasks = load_tasks(args.tasks)
    if args.limit > 0:
        tasks = tasks[:args.limit]
    total = len(tasks)
    print(f"Loaded {total} tasks from {args.tasks}")

    # Resume support
    completed_ids: Set[str] = set()
    if args.resume:
        completed_ids = load_completed_ids(output_path)
        if completed_ids:
            tasks = [t for t in tasks if t.get("id", "") not in completed_ids]
            print(f"Resuming: {len(completed_ids)} already completed, {len(tasks)} remaining")

    if not tasks:
        print("All tasks already completed.")
        return

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Run tasks
    results: List[Dict[str, Any]] = []
    t_start = time.time()

    print(f"\nRunning {len(tasks)} tasks against {args.url} (concurrency={args.concurrency})...")
    print(f"Output: {output_path}")
    print(f"{'='*80}")

    if args.concurrency <= 1:
        # Sequential execution
        for i, task in enumerate(tasks):
            record = run_single_task(
                args.url, task, set_index,
                timeout=args.timeout,
                fetch_traces=not args.no_traces,
            )
            results.append(record)

            # Append to output file immediately
            with open(output_path, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

            # Progress
            status = "PASS" if record["eval_scores"].get("passed") else "FAIL"
            if record["error"]:
                status = "ERR "
            tool_count = len(record.get("trace", {}).get("tool_call_log", []))
            intent = record.get("trace", {}).get("intent", "?")
            completed = len(completed_ids) + i + 1
            print(f"  [{completed}/{total}] {status} {record['task_id']} "
                  f"({record['elapsed_sec']:.1f}s) intent={intent} tools={tool_count} "
                  f"composite={record['eval_scores'].get('composite', 0):.3f}")

            if args.delay > 0:
                time.sleep(args.delay)
    else:
        # Concurrent execution
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {}
            for i, task in enumerate(tasks):
                future = executor.submit(
                    run_single_task, args.url, task, set_index,
                    args.timeout, not args.no_traces,
                )
                futures[future] = (i, task)

            for future in as_completed(futures):
                i, task = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    record = {
                        "task_id": task.get("id", "unknown"),
                        "set_index": set_index,
                        "category": task.get("category", ""),
                        "question": task.get("question", ""),
                        "domain_hint": task.get("domain_hint", ""),
                        "expected": task.get("expected", {}),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "elapsed_sec": 0,
                        "response": {},
                        "trace": {},
                        "eval_scores": {"composite": 0, "passed": False},
                        "error": str(exc),
                    }

                results.append(record)

                # Append to output file immediately
                with open(output_path, "a") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

                status = "PASS" if record.get("eval_scores", {}).get("passed") else "FAIL"
                if record.get("error"):
                    status = "ERR "
                tool_count = len(record.get("trace", {}).get("tool_call_log", []))
                completed = len(completed_ids) + len(results)
                print(f"  [{completed}/{total}] {status} {record['task_id']} "
                      f"({record.get('elapsed_sec', 0):.1f}s) "
                      f"composite={record.get('eval_scores', {}).get('composite', 0):.3f}")

    total_elapsed = time.time() - t_start

    # Print summary
    passed = sum(1 for r in results if r["eval_scores"].get("passed", False))
    errors = sum(1 for r in results if r["error"])
    print(f"\n{'='*80}")
    print(f"REGRESSION RUN COMPLETE — Set {set_index}")
    print(f"{'='*80}")
    print(f"  Total:   {len(results)}")
    print(f"  Passed:  {passed} ({passed/len(results)*100:.1f}%)" if results else "")
    print(f"  Errors:  {errors}")
    print(f"  Time:    {total_elapsed:.1f}s ({total_elapsed/len(results):.1f}s avg)" if results else "")
    print(f"  Output:  {output_path}")

    # Category breakdown
    by_cat: Dict[str, List] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    print(f"\n  Category Breakdown:")
    for cat in sorted(by_cat.keys()):
        cat_results = by_cat[cat]
        cat_passed = sum(1 for r in cat_results if r["eval_scores"].get("passed", False))
        print(f"    {cat:30s} {cat_passed:4d}/{len(cat_results):4d} "
              f"({cat_passed/len(cat_results)*100:.0f}%)")

    # Write summary
    write_summary(results, output_path, set_index, args.url, total_elapsed)


if __name__ == "__main__":
    main()
