#!/usr/bin/env python3
"""
Evaluate RouterKit routing/retrieval outputs against labeled queries.

Usage:
  python3 eval_routerkit.py --artifacts kb_artifacts --queries eval_queries.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


def norm(s: str) -> str:
    return (s or "").strip().replace("\\", "/")


def toks(s: str) -> List[str]:
    out = []
    for t in re.split(r"[^a-z0-9]+", (s or "").lower()):
        if not t or len(t) < 2:
            continue
        out.append(t)
    return out


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
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


def _score_domain(query: str, domain_obj: Dict[str, Any]) -> int:
    q = " ".join(toks(query))
    qset = set(toks(query))
    score = 0
    for kw in domain_obj.get("keywords", []) or []:
        if str(kw).lower() in qset:
            score += 3
    for ph in domain_obj.get("phrases", []) or []:
        phs = " ".join(toks(str(ph)))
        if phs and phs in q:
            score += 4
    # slight prior for domain name mention
    did = str(domain_obj.get("id", "")).lower()
    if did and did in qset:
        score += 2
    return score


def predict_route(query: str, router: Dict[str, Any], *, top_first: int = 3, top_sub: int = 5) -> Dict[str, Any]:
    qtok = set(toks(query))
    qblob = " ".join(toks(query))

    sem = router.get("routing", {}).get("semantic", {}).get("domain", {})
    domains = sem.get("domains", []) or []
    dscored: List[Tuple[str, int]] = []
    for d in domains:
        dscored.append((str(d.get("id", "")), _score_domain(query, d if isinstance(d, dict) else {})))
    dscored = sorted(dscored, key=lambda x: (-x[1], x[0]))
    domain_default = str(sem.get("default", "general"))
    pred_domain = dscored[0][0] if dscored and dscored[0][1] > 0 else domain_default

    fb_scored: List[Tuple[str, int]] = []
    sb_scored: List[Tuple[str, str, int]] = []
    for fb in router.get("first_buckets", []) or []:
        fbid = str(fb.get("id", ""))
        score = 0
        for kw in fb.get("keywords", []) or []:
            if str(kw).lower() in qtok:
                score += 2
        if fbid and any(p in qblob for p in toks(fbid)):
            score += 2
        fb_scored.append((fbid, score))

        for sb in fb.get("sub_buckets", []) or []:
            sbid = str(sb.get("id", ""))
            sscore = score
            for kw in sb.get("keywords", []) or []:
                if str(kw).lower() in qtok:
                    sscore += 2
            if sbid and any(p in qblob for p in toks(sbid)):
                sscore += 2
            sb_scored.append((fbid, sbid, sscore))

    fb_top = [fb for fb, _ in sorted(fb_scored, key=lambda x: (-x[1], x[0]))[:top_first] if fb]
    sb_top = [(fb, sb) for fb, sb, _ in sorted(sb_scored, key=lambda x: (-x[2], x[0], x[1]))[:top_sub] if sb]

    return {
        "semantic_domain": pred_domain,
        "domain_scores": [{"id": d, "score": int(s)} for d, s in dscored[:5]],
        "first_buckets": fb_top,
        "sub_buckets": [{"first_bucket": fb, "sub_bucket": sb} for fb, sb in sb_top],
    }


def rank_docs(query: str, docs: Sequence[Dict[str, Any]], route: Dict[str, Any], *, top_k: int = 20) -> List[str]:
    qtok = set(toks(query))
    dom = route.get("semantic_domain", "")
    sub_allow = {x.get("sub_bucket", "") for x in route.get("sub_buckets", []) if isinstance(x, dict)}
    fb_allow = set(route.get("first_buckets", []) or [])

    scored: List[Tuple[int, str]] = []
    for d in docs:
        path = str(d.get("path", ""))
        if not path:
            continue
        routing = d.get("routing", {})
        facets = d.get("facets", {})
        rb = routing.get("structural", {})
        fb = str(rb.get("first_bucket", routing.get("first_bucket", "")))
        sb = str(rb.get("sub_bucket", routing.get("sub_bucket", "")))
        dd = str(routing.get("semantic", {}).get("domain", facets.get("semantic_domain", "")))

        score = 0
        if dom and dd == dom:
            score += 8
        if sub_allow and sb in sub_allow:
            score += 6
        if fb_allow and fb in fb_allow:
            score += 4

        blob = " ".join(
            [
                path,
                str(d.get("sample", {}).get("text", ""))[:2500],
                str(d.get("openxml_core", {}).get("title", "")),
                str(d.get("openxml_core", {}).get("subject", "")),
                " ".join(d.get("facets", {}).get("tags", []) or []),
                " ".join(d.get("facets", {}).get("entities_projects", []) or []),
                " ".join(d.get("facets", {}).get("entities_systems", []) or []),
            ]
        )
        btok = Counter(toks(blob))
        overlap = sum(btok.get(t, 0) for t in qtok)
        score += overlap
        if score > 0:
            scored.append((score, path))

    scored = sorted(scored, key=lambda x: (-x[0], x[1]))
    return [p for _, p in scored[:top_k]]


def ensure_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [norm(str(v)) for v in x if str(v).strip()]
    s = norm(str(x))
    return [s] if s else []


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate RouterKit artifacts using labeled query set.")
    ap.add_argument("--artifacts", required=True, help="Path to RouterKit output directory (contains router.json/documents.jsonl)")
    ap.add_argument("--queries", required=True, help="JSONL labeled query file")
    ap.add_argument("--out", default="", help="Output report JSON path (default: <artifacts>/eval_report.json)")
    ap.add_argument("--top-k", type=int, default=20, help="Retrieval cutoff k for recall metrics")
    args = ap.parse_args()

    art = Path(args.artifacts).resolve()
    queries_path = Path(args.queries).resolve()
    out_path = Path(args.out).resolve() if args.out else (art / "eval_report.json")

    router = load_json(art / "router.json")
    docs = load_jsonl(art / "documents.jsonl")
    qrows = load_jsonl(queries_path)

    per_query: List[Dict[str, Any]] = []
    agg = {
        "queries": 0,
        "semantic_domain_correct": 0,
        "first_bucket_hit_top3": 0,
        "sub_bucket_hit_top5": 0,
        "any_path_hit_topk": 0,
        "must_have_recall_sum": 0.0,
    }

    for i, q in enumerate(qrows):
        query = str(q.get("query", "")).strip()
        exp = q.get("expected", {}) if isinstance(q.get("expected"), dict) else {}
        if not query:
            continue
        agg["queries"] += 1

        route = predict_route(query, router, top_first=3, top_sub=5)
        top_paths = rank_docs(query, docs, route, top_k=int(args.top_k))

        exp_dom = norm(str(exp.get("semantic_domain", ""))).lower()
        exp_fb = set(ensure_list(exp.get("first_bucket")) + ensure_list(exp.get("first_buckets")))
        exp_sb = set(ensure_list(exp.get("sub_bucket")) + ensure_list(exp.get("sub_buckets")))
        must_paths = set(ensure_list(exp.get("must_have_paths")))
        any_paths = set(ensure_list(exp.get("any_of_paths")))

        sem_ok = bool(exp_dom) and route.get("semantic_domain", "").lower() == exp_dom
        fb_ok = bool(exp_fb) and any(norm(x) in exp_fb for x in route.get("first_buckets", [])[:3])
        sb_preds = [norm(x.get("sub_bucket", "")) for x in route.get("sub_buckets", [])[:5] if isinstance(x, dict)]
        sb_ok = bool(exp_sb) and any(x in exp_sb for x in sb_preds)

        got = set(norm(p) for p in top_paths)
        if any_paths:
            any_ok = any(p in got for p in any_paths)
        elif must_paths:
            any_ok = any(p in got for p in must_paths)
        else:
            any_ok = False

        if must_paths:
            recall = len([p for p in must_paths if p in got]) / max(1, len(must_paths))
        else:
            recall = 0.0

        agg["semantic_domain_correct"] += 1 if sem_ok else 0
        agg["first_bucket_hit_top3"] += 1 if fb_ok else 0
        agg["sub_bucket_hit_top5"] += 1 if sb_ok else 0
        agg["any_path_hit_topk"] += 1 if any_ok else 0
        agg["must_have_recall_sum"] += float(recall)

        per_query.append(
            {
                "id": str(q.get("id", i + 1)),
                "query": query,
                "predicted": route,
                "expected": exp,
                "retrieved_topk": top_paths,
                "metrics": {
                    "semantic_domain_correct": sem_ok,
                    "first_bucket_hit_top3": fb_ok,
                    "sub_bucket_hit_top5": sb_ok,
                    "any_path_hit_topk": any_ok,
                    "must_have_recall_topk": round(recall, 4),
                },
            }
        )

    qn = max(1, agg["queries"])
    summary = {
        "queries": agg["queries"],
        "semantic_domain_accuracy": round(agg["semantic_domain_correct"] / qn, 4),
        "first_bucket_hit_rate_top3": round(agg["first_bucket_hit_top3"] / qn, 4),
        "sub_bucket_hit_rate_top5": round(agg["sub_bucket_hit_top5"] / qn, 4),
        "any_path_hit_rate_topk": round(agg["any_path_hit_topk"] / qn, 4),
        "must_have_recall_mean_topk": round(agg["must_have_recall_sum"] / qn, 4),
    }

    report = {
        "tool": "eval_routerkit",
        "artifacts_dir": norm(str(art)),
        "queries_file": norm(str(queries_path)),
        "top_k": int(args.top_k),
        "summary": summary,
        "per_query": per_query,
    }
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(norm(str(out_path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

