#!/usr/bin/env python3
"""按清单经 OpenList 下载样本文件到本地 raw 目录"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openlist_client import api, login

KB = os.environ.get("KB_ROOT", "/opt/kb")
SAMPLE = f"{KB}/state/sample20.json"
RAW = f"{KB}/raw"


def safe_name(path):
    name = os.path.basename(path)
    # 去掉<你的网盘>推广尾巴与期号噪音，保留可追溯原名
    return name.replace("/", "_")


def main():
    os.makedirs(RAW, exist_ok=True)
    token = login()
    items = json.load(open(SAMPLE))
    ok, fail = 0, []
    for i, item in enumerate(items, 1):
        remote = item["path"]
        out = os.path.join(RAW, safe_name(remote))
        if os.path.exists(out) and os.path.getsize(out) > 1024:
            ok += 1
            continue
        try:
            d = api("POST", "/fs/get", token, {"path": remote})
            url = d.get("raw_url") or d.get("url")
            if not url:
                raise RuntimeError(f"无下载链接: {d}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=180) as resp, open(out, "wb") as fh:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    fh.write(chunk)
            ok += 1
            print(f"[{i}/{len(items)}] {os.path.getsize(out)/2**20:.1f}MB {os.path.basename(out)[:50]}")
        except Exception as e:
            fail.append((remote, str(e)))
            print(f"[{i}/{len(items)}] 失败 {os.path.basename(remote)[:40]}: {e}")
    print(f"\n下载完成: 成功 {ok} / 失败 {len(fail)}")
    json.dump(fail, open(f"{KB}/state/sample_download_failures.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
