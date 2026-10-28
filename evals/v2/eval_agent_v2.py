#!/usr/bin/env python3
"""Dearly Departed v2 Agent Evaluation Runner.

Usage:
    python evals/v2/eval_agent_v2.py --tasks evals/v2/tasks.sample.jsonl [--url http://localhost:8082] [--output reports/eval_v2.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List

import requests

from evals.v2.eval_config import GATES, CITATION_VERIFICATION_GATE, SCORING


def load_tasks(path: str) -> List[Dict[str, Any]]:
    tasks = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def run_single_task(url: str, task: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
    """Run a single task against the v2 agent and return result + scores."""
    query = task["question"]
    payload = {
        "query": query,
        "domain": task.get("domain_hint", ""),
        "top_k": 20,
        "strategy": "auto",
    }

    t0 = time.time()
    try:
        resp = requests.post(f"{url}/v2/ask", json=payload, timeout=timeout)
        elapsed = time.time() - t0
        if resp.status_code >= 300:
            return {"task_id": task["id"], "error": f"HTTP {resp.status_code}", "elapsed": elapsed, "scores": {}, "passed": False}
        data = resp.json()
    except Exception as exc:
        return {"task_id": task["id"], "error": str(exc), "elapsed": time.time() - t0, "scores": {}, "passed": False}

    answer = data.get("answer", "")
    citations = data.get("citations", [])
    retrieval = data.get("retrieval", {})
    diagnostics = data.get("diagnostics", {})

    # Score
    expected = task.get("expected", {})
    scores = {}

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
        found = sum(1 for p in must_cite if any(p.lower() in cp.lower() for cp in cited_paths))
        scores["must_cite_paths"] = found / len(must_cite)
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
        actual_intent = retrieval.get("intent", "") or diagnostics.get("intent", "")
        scores["expected_intent"] = 1.0 if actual_intent == exp_intent else 0.0
    else:
        scores["expected_intent"] = 1.0

    # Weighted composite
    composite = sum(scores[k] * SCORING[k]["weight"] for k in scores if k in SCORING)
    passed = composite >= 0.5

    return {
        "task_id": task["id"],
        "category": task.get("category", ""),
        "question": query,
        "answer_length": len(answer),
        "citation_count": len(citations),
        "verified_citations": sum(1 for c in citations if c.get("verified", False)),
        "scores": scores,
        "composite": round(composite, 3),
        "passed": passed,
        "elapsed": round(elapsed, 2),
        "intent": retrieval.get("intent", ""),
        "strategy": retrieval.get("strategy", ""),
        "context_tokens": retrieval.get("context_tokens", 0),
        "error": "",
    }


def run_eval(url: str, tasks: List[Dict[str, Any]], output_path: str) -> None:
    results = []
    category_scores = defaultdict(list)

    print(f"Running {len(tasks)} tasks against {url}...")
    for i, task in enumerate(tasks):
        result = run_single_task(url, task)
        results.append(result)
        category_scores[task.get("category", "unknown")].append(result)

        status = "PASS" if result["passed"] else "FAIL"
        print(f"  [{i+1}/{len(tasks)}] {status} {task['id']}: composite={result.get('composite', 0):.3f} ({result['elapsed']:.1f}s)")

    # Aggregate
    total_passed = sum(1 for r in results if r["passed"])
    total = len(results)
    overall_rate = total_passed / total if total > 0 else 0

    category_summary = {}
    for cat, cat_results in category_scores.items():
        cat_passed = sum(1 for r in cat_results if r["passed"])
        cat_total = len(cat_results)
        cat_rate = cat_passed / cat_total if cat_total > 0 else 0
        gate = GATES.get(cat, 0.60)
        category_summary[cat] = {
            "passed": cat_passed,
            "total": cat_total,
            "pass_rate": round(cat_rate, 3),
            "gate": gate,
            "gate_passed": cat_rate >= gate,
        }

    # Citation verification rate
    total_citations = sum(r.get("citation_count", 0) for r in results)
    total_verified = sum(r.get("verified_citations", 0) for r in results)
    citation_rate = total_verified / total_citations if total_citations > 0 else 1.0

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": url,
        "total_tasks": total,
        "total_passed": total_passed,
        "overall_pass_rate": round(overall_rate, 3),
        "overall_gate": GATES["overall"],
        "overall_gate_passed": overall_rate >= GATES["overall"],
        "citation_verification_rate": round(citation_rate, 3),
        "citation_gate": CITATION_VERIFICATION_GATE,
        "citation_gate_passed": citation_rate >= CITATION_VERIFICATION_GATE,
        "category_summary": category_summary,
        "results": results,
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print(f"\n{'='*60}")
    print(f"EVALUATION SUMMARY")
    print(f"{'='*60}")
    print(f"Overall: {total_passed}/{total} ({overall_rate:.1%}) — Gate: {GATES['overall']:.0%} — {'PASS' if report['overall_gate_passed'] else 'FAIL'}")
    print(f"Citations: {total_verified}/{total_citations} ({citation_rate:.1%}) — Gate: {CITATION_VERIFICATION_GATE:.0%} — {'PASS' if report['citation_gate_passed'] else 'FAIL'}")
    print()
    for cat, summary in sorted(category_summary.items()):
        status = "PASS" if summary["gate_passed"] else "FAIL"
        print(f"  {cat:30s} {summary['passed']:3d}/{summary['total']:3d} ({summary['pass_rate']:.1%}) gate={summary['gate']:.0%} {status}")
    print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dearly Departed v2 Agent Evaluation")
    parser.add_argument("--tasks", required=True, help="Path to tasks JSONL file")
    parser.add_argument("--url", default="http://localhost:8082", help="v2 agent API URL")
    parser.add_argument("--output", default="reports/eval_v2.json", help="Output report path")
    args = parser.parse_args()

    tasks = load_tasks(args.tasks)
    run_eval(args.url, tasks, args.output)
