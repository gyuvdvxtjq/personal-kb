#!/usr/bin/env python3
"""盘点 文集/{月}/{日}/推广材料：统计正文文件数、类型、体积，音视频单列（排除项）"""
import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openlist_client import login, list_all

ROOT = os.environ.get("KB_REMOTE_ROOT", "/")  # 网盘资料根目录，按自己的存储填写
MEDIA = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".mp4", ".mkv",
         ".mov", ".avi", ".webm", ".m4v", ".ts", ".srt"}
KB = os.environ.get("KB_ROOT", "/opt/kb")
OUT = f"{KB}/state/inventory.json"


def ext(name):
    return os.path.splitext(name)[1].lower()


def main():
    t0 = time.time()
    token = login()
    months = sorted(e["name"] for e in list_all(token, ROOT)
                    if e.get("is_dir") and e["name"].endswith("月"))
    report = {"months": {}, "files": []}
    for m in months:
        st = {"days": 0, "days_with_dwj": 0, "total": 0, "bytes": 0,
              "by_ext": defaultdict(lambda: [0, 0]), "media": 0, "media_bytes": 0}
        days = [e["name"] for e in list_all(token, f"{ROOT}/{m}") if e.get("is_dir")]
        st["days"] = len(days)
        for d in days:
            dpath = f"{ROOT}/{m}/{d}"
            if not any(e.get("is_dir") and e["name"] == "推广材料"
                       for e in list_all(token, dpath)):
                continue
            st["days_with_dwj"] += 1
            for f in list_all(token, f"{dpath}/推广材料"):
                e, sz = ext(f["name"]), f.get("size", 0)
                if e in MEDIA:
                    st["media"] += 1
                    st["media_bytes"] += sz
                else:
                    st["total"] += 1
                    st["bytes"] += sz
                    st["by_ext"][e][0] += 1
                    st["by_ext"][e][1] += sz
                    report["files"].append({"path": f"{dpath}/推广材料/{f['name']}",
                                            "size": sz})
        st["by_ext"] = dict(st["by_ext"])
        report["months"][m] = st
        print(f"[{m}] {st['days']}天/推广材料{st['days_with_dwj']}天 | "
              f"正文 {st['total']}个 {st['bytes']/2**30:.2f}GB | "
              f"音视频(排除) {st['media']}个 {st['media_bytes']/2**20:.0f}MB | "
              f"累计 {time.time()-t0:.0f}s", flush=True)
    json.dump(report, open(OUT, "w"), ensure_ascii=False, indent=1)
    names = Counter(os.path.basename(f["path"]) for f in report["files"])
    dup = sum(c - 1 for c in names.values() if c > 1)
    gt = sum(s["total"] for s in report["months"].values())
    gb = sum(s["bytes"] for s in report["months"].values())
    gm = sum(s["media"] for s in report["months"].values())
    gmb = sum(s["media_bytes"] for s in report["months"].values())
    print(f"\n合计: 正文 {gt} 个 {gb/2**30:.2f}GB | 音视频(排除) {gm} 个 {gmb/2**20:.0f}MB | 疑似重名 {dup} 个")
    print("明细:", OUT)


if __name__ == "__main__":
    main()
