#!/usr/bin/env python3
"""自采样召回评测：从语料正文中段抽取特征句作查询、原文档为标准答案，
走 Dify hit-testing（semantic + 可选 rerank）报 recall@K / MRR。

env:
  KB_STATE_DIR      状态目录（含 dify_dataset_id.txt），默认 ./state
  KB_CORPUS_DIR     语料 Markdown 目录，默认 ./corpus
  EVAL_N            抽样篇数，默认 30
  EVAL_SEED         随机种子（固定可复现），默认 42
  RERANK_PROVIDER   rerank 供应商（openai_api_compatible），留空则不启用 rerank
  RERANK_MODEL      rerank 模型名
  AD_PATTERNS       可选：语料推广行的正则过滤，用 | 分隔（按你的语料自定义）
  DIFY_API_BASE     Dify 控制台基址（dify_client 读取）
"""
import os, sys, random, re
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sync"))
import dify_client as D  # noqa: E402

N = int(os.environ.get("EVAL_N", "30"))
SEED = int(os.environ.get("EVAL_SEED", "42"))
STATE = Path(os.environ.get("KB_STATE_DIR", "state"))
CORPUS = Path(os.environ.get("KB_CORPUS_DIR", "corpus"))
AD_PATTERNS = [p for p in os.environ.get("AD_PATTERNS", "").split("|") if p]
AD = re.compile("|".join(AD_PATTERNS)) if AD_PATTERNS else None
CJK = re.compile(r"[一-鿿]")


def cjk_len(x):
    return len(CJK.findall(x))


def pick_query(text):
    """取正文中段一条完整长句作查询。AD_PATTERNS 可选过滤推广行。"""
    if len(text) < 800:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if AD is not None:
        lines = [l for l in lines if not AD.search(l)]
    t = re.sub(r"[#>*`\[\]()!|【】]", " ", " ".join(lines))
    sents = [s.strip() for s in re.split(r"[。！？]", t) if 35 <= len(s.strip()) <= 90]
    cands = [x for x in sents if cjk_len(x) >= 12]
    if not cands:
        return None
    mid = cands[len(cands)//4: len(cands)*3//4] or cands
    random.shuffle(mid)
    return mid[0]


D.login()
dsid = (STATE / "dify_dataset_id.txt").read_text(encoding="utf-8").strip()
docs = {}
for page in range(1, 16):
    code, res = D._req("GET", f"/console/api/datasets/{dsid}/documents?limit=100&page={page}", timeout=90)
    rows = (res.get("data") or []) if isinstance(res, dict) else []
    docs.update({d["name"]: d["id"] for d in rows})
    if len(rows) < 100:
        break
files = sorted(CORPUS.glob("*.md"))
random.seed(SEED)
sample = random.sample(files, min(N, len(files)))
rerank = bool(os.environ.get("RERANK_MODEL"))
ret = {"search_method": "semantic_search",
       "reranking_enable": rerank,
       "top_k": 10, "score_threshold_enabled": False}
if rerank:
    ret["reranking_model"] = {"reranking_provider_name": os.environ["RERANK_PROVIDER"],
                              "reranking_model_name": os.environ["RERANK_MODEL"]}

hits = {1: 0, 2: 0, 4: 0, 10: 0}
rr = []
used = 0
for i, p in enumerate(sample, 1):
    q = pick_query(p.read_text(encoding="utf-8", errors="ignore"))
    if not q:
        print(f"[{i}] SKIP(too short/noisy) {p.name[:40]}", flush=True)
        continue
    payload = {"query": q, "retrieval_model": ret}
    code, res = D._req("POST", f"/console/api/datasets/{dsid}/hit-testing", payload, timeout=300)
    ranked = [((rec.get("segment") or {}).get("document") or {}).get("id", "") for rec in (res.get("records") or [])]
    gold = docs.get(p.name, "")
    pos = next((i2 for i2, d in enumerate(ranked, 1) if d and d == gold), None)
    for k in hits:
        if pos and pos <= k:
            hits[k] += 1
    rr.append(1.0/pos if pos else 0.0)
    used += 1
    print(f"[{i}/{len(sample)}] rank={pos} {p.name[:40]}", flush=True)

n = len(rr)
print(f"SAMPLES={n}")
print(f"recall@1={hits[1]/n:.4f} recall@2={hits[2]/n:.4f} recall@4={hits[4]/n:.4f} "
      f"recall@10={hits[10]/n:.4f} MRR={sum(rr)/n:.4f}")
