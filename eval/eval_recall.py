import json
import sys
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))
import dify_client as D
from kb_eval_common import load_cases as _load_cases, resolve_dataset_id, resolve_labels

ROOT = Path(__file__).resolve().parent
TOP_K = 10


def load_cases(path=None):
    return _load_cases(path)


def hit_test(dsid, query, search_method, top_k=TOP_K):
    payload = {
        "query": query,
        "retrieval_model": {
            "search_method": search_method,
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": False,
        },
    }
    code, resp = D._req("POST", f"/console/api/datasets/{dsid}/hit-testing", payload, timeout=300)
    if code != 200:
        raise RuntimeError(f"hit-testing failed {code}: {resp}")
    return resp["records"]


def evaluate(dsid, cases, search_method, labels, ks=(1, 2, 4, 5, 10)):
    hits = {k: 0 for k in ks}
    reciprocal = []
    rows = []
    for case in cases:
        expect = {labels[label][:8] for label in case["expect"]}
        records = hit_test(dsid, case["query"], search_method)
        ranked = []
        for rec in records:
            seg = rec.get("segment") or {}
            did = (seg.get("document") or {}).get("id", "")
            ranked.append((did[:8], float(rec.get("score") or 0.0)))
        gold_positions = [i for i, (did, _) in enumerate(ranked, 1) if did in expect]
        for k in hits:
            if any(p <= k for p in gold_positions):
                hits[k] += 1
        first = gold_positions[0] if gold_positions else None
        reciprocal.append(1.0 / first if first else 0.0)
        rows.append({
            "id": case["id"],
            "query": case["query"],
            "note": case.get("note", ""),
            "expect": sorted(expect),
            "top5": ranked[:5],
            "first_gold_rank": first,
        })
    total = len(cases)
    return {
        "search_method": search_method,
        "n": total,
        **{f"recall@{k}": round(v / total, 4) for k, v in hits.items()},
        "mrr": round(statistics.fmean(reciprocal), 4),
        "unretrieved": [r["id"] for r in rows if r["first_gold_rank"] is None],
        "missed_at_4": [r["id"] for r in rows
                        if r["first_gold_rank"] is None or r["first_gold_rank"] > 4],
        "rows": rows,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases")
    parser.add_argument("--out")
    parser.add_argument("methods", nargs="*", default=None)
    args = parser.parse_args()

    D.login()
    dsid = resolve_dataset_id()
    labels = resolve_labels(dsid)
    cases = load_cases(args.cases)
    methods = args.methods or ["semantic_search", "hybrid_search"]
    out = []
    for method in methods:
        report = evaluate(dsid, cases, method, labels)
        summary = " ".join(f"{k}={report[k]}" for k in
                           ("recall@1", "recall@2", "recall@4", "recall@5", "recall@10", "mrr"))
        print(f"[{method}] n={report['n']} {summary}")
        if report["unretrieved"]:
            print(f"  完全未召回: {report['unretrieved']}")
        if report["missed_at_4"]:
            print(f"  top4内缺失: {report['missed_at_4']}")
        out.append(report)
    name = args.out or f"recall_report_{'-'.join(methods)}.json"
    (ROOT / name).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()