#!/usr/bin/env python3
"""批量管线：下载 → OCR → 导入 Dify（可断点续跑）

用法：
  python3 sync/batch_run.py download              # 按 keep.jsonl 下载到 raw/
  python3 sync/batch_run.py ocr                   # raw/*.pdf -> ocr_docs/*.md（跳过已有）
  python3 sync/batch_run.py import                # ocr_docs/*.md -> Dify 知识库（跳过已导入）
  python3 sync/batch_run.py all                   # 依次执行三步

断点：下载看文件是否存在；OCR 看 .md 是否存在；导入看 state/batch_imported.json。
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dify_client as D
import ocr_pipeline as O
from openlist_client import api, login

KB = Path(os.environ.get("KB_ROOT", "/opt/kb"))
RAW = KB / "raw"
OCR_DOCS = KB / "ocr_docs"
STATE = KB / "state"
KEEP = STATE / "keep.jsonl"
DATASET_ID_FILE = STATE / "full_dataset_id.txt"
PROD_DATASET = os.environ.get("KB_FULL_DATASET", "kb-full")


def load_keep():
    return [json.loads(l) for l in KEEP.open() if l.strip()]


def safe_name(path):
    return os.path.basename(path).replace("/", "_")


# ---------------- download ----------------
def cmd_download(limit=None):
    RAW.mkdir(parents=True, exist_ok=True)
    token = login()
    items = load_keep()
    if limit:
        items = items[:limit]
    fails = []
    t0 = time.time()
    for i, item in enumerate(items, 1):
        remote = item["path"]
        out = RAW / safe_name(remote)
        if out.exists() and out.stat().st_size > 1024:
            if i % 50 == 0:
                print(f"[{i}/{len(items)}] 已有 {out.name[:40]}")
            continue
        err = None
        for attempt in range(3):  # <你的网盘>偶发 416/超时，重试
            try:
                d = api("POST", "/fs/get", token, {"path": remote})
                url = d.get("raw_url") or d.get("url")
                if not url:
                    raise RuntimeError(f"无下载链接: {d}")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=300) as resp, open(out, "wb") as fh:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                err = None
                break
            except Exception as e:
                err = str(e)
                time.sleep(2 * (attempt + 1))
        if err:
            fails.append({"path": remote, "error": err})
            print(f"[{i}/{len(items)}] 失败 {out.name[:40]}: {err}")
            continue
        if i % 20 == 0 or i == len(items):
            rate = (i / max(1e-9, time.time() - t0))
            print(f"[{i}/{len(items)}] {out.stat().st_size/2**20:.1f}MB {out.name[:40]} ({rate:.2f}/s)")
    json.dump(fails, (STATE / "batch_download_failures.json").open("w"), ensure_ascii=False, indent=1)
    print(f"下载完成，失败 {len(fails)}")


# ---------------- ocr ----------------
def cmd_ocr(limit=None, shard=0, nshards=1):
    import tempfile, glob
    from rapidocr_onnxruntime import RapidOCR
    OCR_DOCS.mkdir(parents=True, exist_ok=True)
    engine = RapidOCR()
    pdfs = sorted(RAW.glob("*.pdf"))
    if limit:
        pdfs = pdfs[:limit]
    if nshards > 1:
        pdfs = [p for i, p in enumerate(pdfs) if i % nshards == shard]
    report = []
    t0 = time.time()
    for idx, pdf in enumerate(pdfs, 1):
        out_md = OCR_DOCS / (pdf.stem + ".md")
        if out_md.exists() and out_md.stat().st_size > 200:
            report.append({"file": pdf.name, "status": "skipped"})
            continue
        with tempfile.TemporaryDirectory() as td:
            try:
                imgs = O.extract_images(str(pdf), td)
            except Exception as e:
                report.append({"file": pdf.name, "status": "pdfimages_failed", "error": str(e)})
                print(f"[{idx}/{len(pdfs)}] pdfimages 失败 {pdf.name[:40]}")
                continue
            scored = []
            for p in imgs:
                from PIL import Image
                with Image.open(p) as im:
                    w, h = im.size
                if w * h < 200 * 200:
                    continue
                scored.append((w * h, p))
            scored.sort(reverse=True)
            texts = []
            for _, p in scored:
                with tempfile.TemporaryDirectory() as sd:
                    for piece in O.slice_image(p, sd):
                        texts.append(O.ocr_image(engine, piece))
        seen, dedup = set(), []
        for x in "\n".join(texts).splitlines():
            k = x.strip()
            if k and k not in seen:
                seen.add(k)
                dedup.append(k)
        lines = [x for x in (O.clean_line(x) for x in dedup) if x]
        out_md.write_text(f"# {pdf.stem}\n\n" + "\n\n".join(lines))
        chars = sum(len(x) for x in lines)
        report.append({"file": pdf.name, "status": "ok", "images": len(scored),
                       "lines": len(lines), "chars": chars})
        done = sum(1 for r in report if r["status"] == "ok")
        rate = done / max(1e-9, time.time() - t0)
        eta = (len(pdfs) - idx) / max(1e-9, rate) / 60
        print(f"[{idx}/{len(pdfs)}] {pdf.name[:38]:<40} 图{len(scored):>2} 字{chars:>6} "
              f"({rate*60:.1f}篇/分 ETA{eta:.0f}分)")
        if idx % 25 == 0:
            json.dump(report, (STATE / f"batch_ocr_report_{shard}.json").open("w"), ensure_ascii=False, indent=1)
    json.dump(report, (STATE / f"batch_ocr_report_{shard}.json").open("w"), ensure_ascii=False, indent=1)
    ok = [r for r in report if r["status"] == "ok"]
    print(f"OCR 完成：成功 {len(ok)} / 跳过 {sum(1 for r in report if r['status']=='skipped')} / 失败 {sum(1 for r in report if r['status'] not in ('ok','skipped'))}")


# ---------------- import ----------------
def ensure_full_dataset():
    if DATASET_ID_FILE.exists():
        dsid = DATASET_ID_FILE.read_text().strip()
        code, res = D._req("GET", f"/console/api/datasets/{dsid}", timeout=60)
        if code == 200:
            return dsid
    code, res = D.create_dataset(None, PROD_DATASET)
    if code >= 400 or not isinstance(res, dict) or not res.get("id"):
        raise SystemExit(f"创建知识库失败 {code} {str(res)[:200]}")
    dsid = res["id"]
    DATASET_ID_FILE.write_text(dsid + "\n")
    return dsid


def cmd_import(limit=None, batch=4):
    D.login()
    dsid = ensure_full_dataset()
    print("知识库:", dsid)
    imported = set()
    if (STATE / "batch_imported.json").exists():
        imported = set(json.load((STATE / "batch_imported.json").open()))
    mds = sorted(OCR_DOCS.glob("*.md"))
    if limit:
        mds = mds[:limit]
    todo = [m for m in mds if m.name not in imported]
    print(f"待导入 {len(todo)} / 共 {len(mds)}")
    for i, md in enumerate(todo, 1):
        code, res = D.upload_doc(None, dsid, str(md))
        if code >= 400:
            print(f"[{i}/{len(todo)}] 上传失败 {md.name[:40]}: {str(res)[:120]}")
            continue
        imported.add(md.name)
        if i % 20 == 0 or i == len(todo):
            json.dump(sorted(imported), (STATE / "batch_imported.json").open("w"), ensure_ascii=False)
            print(f"[{i}/{len(todo)}] 已上传 {len(imported)}")
    json.dump(sorted(imported), (STATE / "batch_imported.json").open("w"), ensure_ascii=False)
    # 等待索引
    print("等待索引完成...")
    for _ in range(240):
        code, res = D.list_docs(None, dsid, limit=1000)
        docs = res.get("data", []) if isinstance(res, dict) else []
        pend = [d for d in docs if d.get("indexing_status") not in ("completed", None)]
        if not pend:
            print(f"索引完成，共 {len(docs)} 篇")
            return
        time.sleep(10)
    print("索引等待超时，稍后可用 batch_run.py import 继续")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "ocr", "import", "all"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    a = ap.parse_args()
    if a.cmd in ("download", "all"):
        cmd_download(a.limit)
    if a.cmd in ("ocr", "all"):
        cmd_ocr(a.limit, a.shard, a.nshards)
    if a.cmd in ("import", "all"):
        cmd_import(a.limit)


if __name__ == "__main__":
    main()
