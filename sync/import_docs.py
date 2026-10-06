#!/usr/bin/env python3
"""批量导入语料目录到 Dify 知识库（幂等：state 文件记录已导入，中断重跑自动续传）。

env:
  DIFY_API_BASE     Dify 控制台基址
  KB_STATE_DIR      状态目录，默认 ./state
  KB_CORPUS_DIR     语料 Markdown 目录，默认 ./corpus
  IMPORT_DATASET    目标 dataset id（或 KB_STATE_DIR/import_dataset.txt）
  IMPORT_STATE      进度文件名，默认 import_docs_done.txt
"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dify_client as D  # noqa: E402

STATE_DIR = Path(os.environ.get("KB_STATE_DIR", "state"))
CORPUS = Path(os.environ.get("KB_CORPUS_DIR", "corpus"))
DSID = os.environ.get("IMPORT_DATASET") or (STATE_DIR / "import_dataset.txt").read_text(encoding="utf-8").strip()
STATE = STATE_DIR / os.environ.get("IMPORT_STATE", "import_docs_done.txt")

done = set(STATE.read_text(encoding="utf-8").splitlines()) if STATE.exists() else set()
files = sorted(CORPUS.glob("*.md"))
print(f"total={len(files)} already_done={len(done)}", flush=True)

fail = 0
t0 = time.time()
for i, p in enumerate(files, 1):
    if p.name in done:
        continue
    for attempt in (1, 2, 3):
        try:
            code, res = D.upload_doc(None, DSID, str(p))
        except Exception as e:
            code, res = 599, str(e)[:80]
        if code == 401:
            D.login()
            continue
        if code in (200, 201):
            with STATE.open("a", encoding="utf-8") as f:
                f.write(p.name + "\n")
            break
        time.sleep(3 * attempt)
    else:
        fail += 1
        print(f"FAIL[{code}] {p.name} {str(res)[:80]}", flush=True)
    if i % 100 == 0:
        print(f"progress {i}/{len(files)} fail={fail} rate={i/(time.time()-t0):.1f}/s", flush=True)
print(f"IMPORT_DONE files={len(files)} fail={fail} elapsed={time.time()-t0:.0f}s", flush=True)
