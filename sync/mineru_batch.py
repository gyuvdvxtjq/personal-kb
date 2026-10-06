#!/usr/bin/env python3
"""MinerU 批量解析：申请签名URL → curl PUT 上传 → 轮询 → 下载 zip 里的 full.md

用法：
  python3 sync/mineru_batch.py submit [每批文件数]   # 提交一批（默认50，受单批上限）
  python3 sync/mineru_batch.py poll                # 轮询在途任务，落盘 full.md
  python3 sync/mineru_batch.py loop                # 持续 submit+poll（每天额度用完自动停）
断点：state/mineru_done.json 记录已完成的文件名；上传中的 batch 记录在 state/mineru_batches.json
"""
import glob
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

KB = Path(os.environ.get("KB_ROOT", "/opt/kb"))
RAW = KB / "raw"
OUT = KB / "ocr_docs"
STATE = KB / "state"
SECRETS = STATE / "secrets.env"
DONE = STATE / "mineru_done.json"
BATCHES = STATE / "mineru_batches.json"
BATCH_SIZE = 50


def key():
    for l in SECRETS.read_text().splitlines():
        if l.startswith("MINERU_API_KEY="):
            return l.split("=", 1)[1].strip()
    raise SystemExit("state/secrets.env 缺 MINERU_API_KEY")


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def save(p, o):
    p.write_text(json.dumps(o, ensure_ascii=False, indent=1))


def done_set():
    return set(load(DONE, []))


def api(method, path, data=None):
    """调 MinerU JSON 接口（curl 避免签名/编码问题）"""
    cmd = ["curl", "-s", "-m", "60", "-X", method, f"https://mineru.net{path}",
           "-H", f"Authorization: Bearer {key()}"]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"code": -1, "msg": out[:200]}


def pending_files():
    done = done_set()
    out = []
    for f in sorted(RAW.glob("*.pdf")):
        if f.name in done:
            continue
        md = OUT / (f.stem + ".md")
        if md.exists() and md.stat().st_size > 200:
            continue  # 本地 OCR 已产出，跳过
        out.append(f)
    return out


def submit(n=BATCH_SIZE):
    files = pending_files()[:n]
    if not files:
        print("没有待提交文件（本地 OCR 已覆盖或 MinerU 已完成）")
        return None
    payload = {"files": [{"name": f.name, "is_ocr": True} for f in files],
               "model_version": "vlm", "enable_formula": False, "enable_table": False}
    resp = api("POST", "/api/v4/file-urls/batch", payload)
    if resp.get("code") != 0:
        print("申请上传URL失败:", resp.get("code"), resp.get("msg"))
        return None
    data = resp["data"]
    bid, urls = data["batch_id"], data["file_urls"]
    # curl PUT 上传（urllib 会因签名里的 +/= 破坏签名）
    ok = 0
    for f, u in zip(files, urls):
        r = subprocess.run(["curl", "-s", "-m", "600", "-X", "PUT", "-T", str(f), u,
                            "-o", "/dev/null", "-w", "%{http_code}"], capture_output=True, text=True)
        if r.stdout.strip() == "200":
            ok += 1
        else:
            print(f"  上传失败 {f.name[:30]} HTTP:{r.stdout.strip()}")
    batches = load(BATCHES, {})
    batches[bid] = {"files": [f.name for f in files], "ts": time.time()}
    save(BATCHES, batches)
    print(f"提交 batch={bid} 文件 {len(files)} 上传成功 {ok}")
    return bid


def poll_once():
    batches = load(BATCHES, {})
    if not batches:
        return 0, 0
    done = done_set()
    finished = 0
    for bid, info in list(batches.items()):
        resp = api("GET", f"/api/v4/extract-results/batch/{bid}")
        if resp.get("code") != 0:
            continue
        results = resp["data"].get("extract_result", [])
        n_done = n_run = 0
        for r in results:
            st = r.get("state")
            if st == "done" and r.get("full_zip_url"):
                name = r.get("file_name")
                dest = OUT / (Path(name).stem + ".md")
                # 下载 zip 里的 full.md
                zp = "/tmp/_mz.zip"
                subprocess.run(["curl", "-s", "-m", "300", "-o", zp, r["full_zip_url"]], check=False)
                try:
                    with zipfile.ZipFile(zp) as z:
                        md = next(n for n in z.namelist() if n.endswith("full.md"))
                        dest.write_bytes(z.read(md))
                    done.add(name)
                    finished += 1
                    n_done += 1
                except Exception as e:
                    print(f"  解压失败 {name[:30]}: {e}")
            elif st in ("running", "converting"):
                n_run += 1
            elif st == "failed":
                print(f"  解析失败 {r.get('file_name','')[:30]}: {r.get('err_msg','')}")
                done.add(r.get("file_name"))
                n_done += 1
        save(DONE, sorted(done))
        if all(r.get("state") in ("done", "failed") for r in results) and results:
            del batches[bid]
        print(f"batch {bid[:8]} 完成{n_done}/{len(results)} 运行中{n_run}")
    save(BATCHES, batches)
    return finished, len(batches)


def loop(minutes=0):
    t0 = time.time()
    while True:
        if minutes and (time.time() - t0) / 60 >= minutes:
            print("达到运行时限，停止")
            break
        finished, inflight = poll_once()
        if finished:
            print(f"本轮落盘 {finished} 个；剩余待提交 {len(pending_files())}")
        # 没有在途任务就补提交
        if not load(BATCHES, {}):
            if not pending_files():
                print("全部完成")
                break
            if submit() is None:
                print("提交受限（可能额度用尽），等待下一轮")
                time.sleep(120)
        time.sleep(20)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if cmd == "submit":
        submit(int(sys.argv[2]) if len(sys.argv) > 2 else BATCH_SIZE)
    elif cmd == "poll":
        poll_once()
    elif cmd == "loop":
        loop(int(sys.argv[2]) if len(sys.argv) > 2 else 0)