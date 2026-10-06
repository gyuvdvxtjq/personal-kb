#!/usr/bin/env python3
"""同题对照：与 exact_baseline 用同一份考题 JSONL（{id, query, expect:[docid[:8]]}，
可由 qa_eval 生成的 golden 考卷导出），走 Dify hit-testing——省略 retrieval_model 即用
数据集生产检索配置，报实际链路 recall@K / MRR，与无 rerank 基线直接对比。

env:
  KB_STATE_DIR   状态目录（含 dify_dataset_id.txt），默认 ./state
  CASES_FILE     考题 JSONL，默认 eval/cases.jsonl
  DIFY_API_BASE  Dify 控制台基址（dify_client 读取）
"""
import os, sys, json
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sync"))
import dify_client as D  # noqa: E402

STATE = Path(os.environ.get("KB_STATE_DIR", "state"))
CASES = os.environ.get("CASES_FILE", "eval/cases.jsonl")

D.login()
dsid = (STATE / "dify_dataset_id.txt").read_text(encoding="utf-8").strip()
cases = [json.loads(l) for l in open(CASES, encoding="utf-8") if l.strip()]
print(f"cases={len(cases)} dataset={dsid}")

hits = {1: 0, 2: 0, 4: 0, 10: 0}
rr = []
for i, c in enumerate(cases, 1):
    golds = c["expect"]
    code, res = D._req("POST", f"/console/api/datasets/{dsid}/hit-testing",
                       {"query": c["query"]}, timeout=300)
    ranked = [((rec.get("segment") or {}).get("document") or {}).get("id", "")
              for rec in (res.get("records") or [])]
    pos = next((k for k, d in enumerate(ranked, 1)
                if any(d.startswith(g) for g in golds)), None)
    for k in hits:
        if pos and pos <= k:
            hits[k] += 1
    rr.append(1.0 / pos if pos else 0.0)
    print(f"[{i}/{len(cases)}] {c['id']} rank={pos}", flush=True)

n = len(rr)
print(f"SAMPLES={n}")
print(f"recall@1={hits[1]/n:.4f} recall@2={hits[2]/n:.4f} recall@4={hits[4]/n:.4f} "
      f"recall@10={hits[10]/n:.4f} MRR={sum(rr)/n:.4f}")
