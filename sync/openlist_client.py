#!/usr/bin/env python3
"""OpenList API 客户端：登录 / 存储管理 / 文件列表 / 下载链接

用法:
  python3 openlist_client.py drivers          # 列出驱动（过滤 <your-cloud>）
  python3 openlist_client.py setup            # 挂载<你的网盘>网盘到 /<your-cloud>
  python3 openlist_client.py ls <路径>         # 列目录
  python3 openlist_client.py tree <路径> <深度> # 树状浏览
  python3 openlist_client.py link <路径>       # 取直链(下载用)
  python3 openlist_client.py mkdir <路径>      # 建目录(回推markdown用)
"""
import json
import os
import sys
import urllib.request

KB = os.environ.get("KB_ROOT", "/opt/kb")
BASE = os.environ.get("OPENLIST_URL", "http://127.0.0.1:5244")


def req(method, path, token=None, body=None, timeout=120):
    url = BASE + "/api" + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", token)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def login():
    pw = open(f"{KB}/.secrets/openlist_admin.txt").read().strip()
    d = req("POST", "/auth/login", body={"username": "admin", "password": pw})
    if d.get("code") != 200:
        raise SystemExit(f"登录失败: {d.get('message')}")
    return d["data"]["token"]


def api(method, path, token, body=None):
    d = req(method, path, token, body)
    if d.get("code") != 200:
        raise RuntimeError(f"{method} {path}: {d.get('message')}")
    return d.get("data")


def list_all(token, path, refresh=False):
    page, out, total = 1, [], None
    while True:
        d = api("POST", "/fs/list", token, {"path": path, "page": page,
                                            "per_page": 200, "refresh": refresh})
        c = d.get("content") or []
        out += c
        total = d.get("total", 0)
        if not c or len(out) >= total:
            break
        page += 1
    return out


def ensure_<your-cloud>_storage(token):
    cookie = open(f"{KB}/.secrets/<your-cloud>_cookie.txt").read().strip()
    addition = {"cookie": cookie, "root_folder_id": "0",
                "order_by": "none", "order_direction": "asc"}
    body = {
        "mount_path": "/<your-cloud>",
        "order": 0,
        "driver": "<YourCloud>",
        "cache_expiration": 30,
        "addition": json.dumps(addition, ensure_ascii=False),
        "remark": "<你的网盘>网盘-知识库管线",
        "enable_sign": False,
    }
    d = req("POST", "/admin/storage/create", token, body)
    print("setup:", d.get("code"), d.get("message"))


def tree(token, root, depth=2):
    def walk(path, level, prefix=""):
        entries = list_all(token, path)
        dirs = [e for e in entries if e.get("is_dir")]
        files = [e for e in entries if not e.get("is_dir")]
        size = sum(e.get("size", 0) for e in files)
        print(f"{prefix}{path}  [{len(dirs)}个文件夹, {len(files)}个文件, {size/1048576:.1f}MB]")
        if level < depth:
            for e in dirs:
                walk(f"{path}/{e['name']}".replace("//", "/"), level + 1,
                     prefix + "  ")
    walk(root, 0)


def main():
    token = login()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    if cmd == "drivers":
        names = api("GET", "/admin/driver/names", token, None)
        print([n for n in names if "uark" in n.lower()])
    elif cmd == "setup":
        ensure_<your-cloud>_storage(token)
    elif cmd == "ls":
        for e in list_all(token, sys.argv[2]):
            kind = "D" if e.get("is_dir") else "F"
            print(f"{kind}\t{e.get('size', 0)}\t{e['name']}")
    elif cmd == "tree":
        tree(token, sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2)
    elif cmd == "link":
        d = api("POST", "/fs/get", token, {"path": sys.argv[2]})
        print(d.get("raw_url"))
    elif cmd == "mkdir":
        api("POST", "/fs/mkdir", token, {"path": sys.argv[2]})
        print("mkdir OK:", sys.argv[2])


if __name__ == "__main__":
    main()
