#!/usr/bin/env python3
"""Dify 控制台 API 客户端：登录、创建知识库、上传文档、查询索引状态"""
import json
import mimetypes
import os
import base64
import http.cookiejar
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("DIFY_API_BASE", "http://127.0.0.1:5001")
EMAIL = os.environ.get("DIFY_EMAIL", "")
PASSWORD = os.environ.get("DIFY_PASSWORD", "")

# Dify 控制台登录使用 Cookie 会话，不是 Bearer token
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))


def csrf_token():
    for c in _JAR:
        if c.name.endswith("csrf_token"):
            return c.value
    return ""


def _urlopen(req, timeout):
    return _OPENER.open(req, timeout=timeout)


def _req(method, path, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    # 控制台接口统一要求 CSRF 头
    token = csrf_token()
    if token:
        r.add_header("X-CSRF-Token", token)
    try:
        with _urlopen(r, timeout) as resp:
            raw = resp.read() or b"{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]


def login():
    if not (EMAIL and PASSWORD):
        raise SystemExit("请先设置环境变量 DIFY_EMAIL / DIFY_PASSWORD")
    # 前端对登录密码做 UTF-8 Base64 编码后提交，后端按相同方式解码
    encoded_password = base64.b64encode(PASSWORD.encode("utf-8")).decode()
    code, res = _req("POST", "/console/api/login",
                     {"email": EMAIL, "password": encoded_password, "remember_me": True})
    if code != 200 or (isinstance(res, dict) and res.get("result") != "success"):
        raise SystemExit(f"登录失败 {code}: {res}")
    return "cookie-session"


def create_dataset(token, name):
    code, res = _req("POST", "/console/api/datasets",
                     {"name": name, "indexing_technique": "high_quality"})
    return code, res


def upload_file(path):
    """上传文件到控制台文件区，返回 upload_file id（新版两阶段建文档的第一步）"""
    boundary = f"----kb{uuid.uuid4().hex}"
    filename = os.path.basename(path)
    ctype = mimetypes.guess_type(filename)[0] or "text/markdown"
    with open(path, "rb") as fh:
        content = fh.read()
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n').encode()
    body += f"Content-Type: {ctype}\r\n\r\n".encode()
    body += content
    body += f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(
        f"{BASE}/console/api/files/upload",
        data=bytes(body), method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    token = csrf_token()
    if token:
        r.add_header("X-CSRF-Token", token)
    try:
        with _urlopen(r, 180) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]


def upload_doc(token, dataset_id, path, doc_language="Chinese"):
    """新版协议：先上传文件，再以 upload_file id 建文档"""
    code, res = upload_file(path)
    if code not in (200, 201) or not isinstance(res, dict) or not res.get("id"):
        return code, res
    file_id = res["id"]
    body = {
        "indexing_technique": "high_quality",
        "data_source": {
            "info_list": {
                "data_source_type": "upload_file",
                "file_info_list": {"file_ids": [file_id]},
            }
        },
        "process_rule": {"mode": "automatic"},
        "doc_form": "text_model",
        "doc_language": doc_language,
    }
    return _req("POST", f"/console/api/datasets/{dataset_id}/documents", body)


def list_docs(token, dataset_id, limit=100):
    return _req("GET", f"/console/api/datasets/{dataset_id}/documents?limit={limit}",
                headers=None)


def delete_doc(token, dataset_id, doc_id):
    return _req("DELETE", f"/console/api/datasets/{dataset_id}/documents/{doc_id}")


def reindex_docs(token, dataset_id, doc_dir):
    """删除知识库内全部文档并按目录内容重新导入（用于重建向量索引配置）。"""
    code, res = list_docs(token, dataset_id, limit=100)
    if code != 200:
        return code, res
    docs = res.get("data", [])
    for doc in docs:
        delete_doc(token, dataset_id, doc["id"])
    paths = sorted(Path(doc_dir).glob("*.md"))
    for path in paths:
        upload_doc(token, dataset_id, str(path))
    return 200, {"deleted": len(docs), "reuploaded": len(paths)}
