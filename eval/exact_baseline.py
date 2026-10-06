#!/usr/bin/env python3
"""暴力精确检索基线：绕开 ANN 与 Dify 过滤，给出无 rerank 的纯向量排序基线。

用法：
  1. 准备考卷 JSONL，每行：{"id": "...", "query": "...", "expect": ["文档id前8位", ...]}
     （文档 id 可从控制台 API 列出后取前 8 位）
  2. 配置环境变量后运行：
     export PG_HOST=127.0.0.1 PG_PORT=5432 PG_USER=dify PG_PASSWORD=... PG_DB=dify
     export EMBED_BASE=https://api.siliconflow.cn/v1   # 任意 OpenAI 兼容 /v1/embeddings
     export EMBED_MODEL=BAAI/bge-m3 EMBED_API_KEY=sk-...
     python3 eval/exact_baseline.py eval/my_cases.jsonl

说明：直接从 PG 拉全量有效分段 → 全部重新 embedding → 暴力余弦排序。
得到的是「无 ANN 损失、无阈值过滤、无 rerank」的基线排序：top-K 无漏召说明
候选池质量够；与 hit-testing 实测对照，差值反映 ANN 近似、rerank 与切片/过滤
策略的综合影响。
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

import psycopg2

STATE_DIR = Path(os.environ.get("KB_STATE_DIR", "state"))
DSID = (STATE_DIR / "dify_dataset_id.txt").read_text(encoding="utf-8").strip()
ROOT = Path(__file__).resolve().parent
KS = (1, 2, 4, 5, 10, 20)

PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_USER = os.environ.get("PG_USER", "dify")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")
PG_DB = os.environ.get("PG_DB", "dify")

EMBED_BASE = os.environ.get("EMBED_BASE", "http://127.0.0.1:8100/v1")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "")


def embed(texts, batch=16):
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        req = urllib.request.Request(
            EMBED_BASE + "/embeddings",
            data=json.dumps({"model": EMBED_MODEL, "input": chunk}).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + EMBED_API_KEY} if EMBED_API_KEY else {})},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
    return out


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def load_segments():
    conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD,
                            dbname=PG_DB, port=PG_PORT)
    cur = conn.cursor()
    cur.execute(
        "select s.id, s.document_id, s.content from document_segments s "
        "where s.dataset_id=%s and s.status='completed' order by s.position",
        (DSID,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def main():
    cases_file = sys.argv[1] if len(sys.argv) > 1 else "eval/recall_cases.jsonl"
    cases = [json.loads(l) for l in Path(cases_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    segs = load_segments()
    print(f"有效分段数 {len(segs)}，考卷 {len(cases)} 题")
    seg_vectors = embed([s[2] for s in segs])

    hits = {k: 0 for k in KS}
    ranks = []
    rows = []
    for case in cases:
        qv = embed([case["query"]])[0]
        scored = sorted(
            ((cos(qv, v), s[0], s[1]) for s, v in zip(segs, seg_vectors)),
            key=lambda t: -t[0],
        )
        expect = set(case["expect"])
        gold_positions = [i for i, (_, _, doc) in enumerate(scored, 1)
                          if doc[:8] in expect]
        first = gold_positions[0] if gold_positions else None
        for k in KS:
            if first and first <= k:
                hits[k] += 1
        ranks.append(first or 999)
        rows.append({"id": case["id"], "query": case["query"],
                     "gold_rank": first,
                     "gold_score": round(scored[first - 1][0], 4) if first else None,
                     "top1_score": round(scored[0][0], 4),
                     "top1_doc": scored[0][2][:8]})
    total = len(cases)
    print("\n暴力精确检索 (无 ANN 损失、无阈值过滤):")
    for k in KS:
        print(f"  recall@{k} = {hits[k]/total:.4f}")
    print("  未召回:", [c["id"] for c, r in zip(cases, ranks) if r == 999])
    print("\n金标最佳排名分布:", sorted(ranks))
    print("\n逐题细节（gold_rank=999 表示 top20 内仍无金标）:")
    for r in rows:
        print(f"  {r['id']} gold_rank={r['gold_rank']} gold_score={r['gold_score']} "
              f"top1={r['top1_doc']}@{r['top1_score']}")
    (ROOT / "exact_baseline.json").write_text(
        json.dumps({"n": total, "segments": len(segs),
                    "recall": {f"recall@{k}": hits[k] / total for k in KS},
                    "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
