#!/usr/bin/env python3
"""幂等初始化 Dify：管理员账号 → 模型插件 → 模型凭证 → 默认模型 → 知识库 → 应用 + API Key

容器重置后 PG 变空库，全部步骤可重复执行（已存在则跳过）。
状态写入 state/（含密钥，不得进 Git）：
  dify_dataset_id.txt / app_id.txt / app_api_key.txt
用法：python3 sync/bootstrap.py
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dify_client as D

STATE = ROOT / "state"
PKGS = ROOT / "runtime" / "plugin-storage" / "plugin_packages"
PROVIDER = "openai_api_compatible"
MS_BASE = "https://api-inference.modelscope.cn/v1"
EMBED_BASE = "http://127.0.0.1:8100/v1"
LLM_MODEL = os.environ.get("KB_LLM_MODEL", "Qwen/Qwen3.5-122B-A10B")
EMBED_MODEL = os.environ.get("KB_EMBED_MODEL", "bge-m3")
APP_NAME = os.environ.get("KB_APP_NAME", "kb-verify-app")
DATASET_NAME = os.environ.get("KB_DATASET_NAME", "sample20-verify")


def log(*a):
    print(*a, flush=True)


def ms_token():
    tok = os.environ.get("MODELSCOPE_ACCESS_TOKEN", "")
    if tok:
        secret = STATE / "secrets.env"
        if not secret.exists():
            secret.write_text(f"MODELSCOPE_ACCESS_TOKEN={tok}\n")
            os.chmod(secret, 0o600)
        return tok
    secret = STATE / "secrets.env"
    if secret.exists():
        for line in secret.read_text().splitlines():
            if line.startswith("MODELSCOPE_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("缺少 MODELSCOPE_ACCESS_TOKEN（环境变量或 state/secrets.env）")


def ensure_setup():
    code, res = D._req("GET", "/console/api/setup", timeout=30)
    if isinstance(res, dict) and res.get("step") == "finished":
        log("[setup] 已初始化")
        return
    code, res = D._req("POST", "/console/api/setup", {
        "email": D.EMAIL, "name": "Admin", "password": D.PASSWORD, "language": "zh-Hans",
    }, timeout=120)
    log("[setup]", code, str(res)[:200])
    if code not in (200, 201):
        raise SystemExit("初始化失败")


def ensure_plugin():
    code, res = D._req("GET", "/console/api/workspaces/current/plugin/installed-ids?category=model", timeout=60)
    ids = (res or {}).get("plugin_ids") or [] if isinstance(res, dict) else []
    if any(PROVIDER in str(i) for i in ids):
        log("[plugin] 已安装:", [i for i in ids if PROVIDER in str(i)][0])
        return
    pkgs = sorted(PKGS.glob(f"langgenius/{PROVIDER}:*"))
    if not pkgs:
        raise SystemExit(f"未找到本地插件包 {PKGS}/langgenius/{PROVIDER}:*")
    pkg = pkgs[-1]

    boundary = f"----pkg{uuid.uuid4().hex}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"pkg\"; "
            f"filename=\"{pkg.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
        + pkg.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(D.BASE + "/console/api/workspaces/current/plugin/upload/pkg",
                               data=body, method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if D.csrf_token():
        r.add_header("X-CSRF-Token", D.csrf_token())
    with D._urlopen(r, timeout=300) as resp:
        up = json.loads(resp.read() or b"{}")
    ident = up.get("unique_identifier")
    log("[plugin] 上传:", code, ident)
    if not ident:
        raise SystemExit(f"上传插件包失败: {str(up)[:300]}")

    code, res = D._req("POST", "/console/api/workspaces/current/plugin/install/pkg",
                       {"plugin_unique_identifiers": [ident]}, timeout=300)
    log("[plugin] 安装:", code, str(res)[:300])
    if code >= 400:
        raise SystemExit("安装插件失败")
    for i in range(60):
        code, res = D._req("GET", "/console/api/workspaces/current/plugin/installed-ids?category=model", timeout=60)
        ids = (res or {}).get("plugin_ids") or [] if isinstance(res, dict) else []
        if any(ident == i2 or PROVIDER in str(i2) for i2 in ids):
            log("[plugin] 安装完成")
            return
        time.sleep(2)
    raise SystemExit("插件安装超时")


def existing_models():
    code, res = D._req("GET", f"/console/api/workspaces/current/model-providers/{PROVIDER}/models", timeout=60)
    data = (res or {}).get("data") or [] if isinstance(res, dict) else []
    return {m.get("model") for m in data if isinstance(m, dict)}


def ensure_model_credentials():
    have = existing_models()
    base = f"/console/api/workspaces/current/model-providers/{PROVIDER}/models/credentials"
    specs = [
        (LLM_MODEL, "llm", {
            "display_name": "ModelScope " + LLM_MODEL.split("/")[-1],
            "api_key": ms_token(),
            "endpoint_url": MS_BASE,
            "mode": "chat",
            "context_size": "32768",
        }),
        (EMBED_MODEL, "text-embedding", {
            "display_name": "local bge-m3",
            "api_key": "local-embed",
            "endpoint_url": EMBED_BASE,
            "mode": "embedding",
            "context_size": "8192",
            "max_chunks": "16",
        }),
    ]
    for model, mtype, creds in specs:
        if model in have:
            log(f"[model] 已存在: {model}")
            continue
        code, res = D._req("POST", base, {"model": model, "model_type": mtype, "credentials": creds}, timeout=120)
        log(f"[model] 新建 {model}:", code, str(res)[:200])
        if code >= 400 and "already" not in str(res).lower():
            raise SystemExit(f"创建模型失败 {model}: {res}")


def ensure_defaults():
    code, res = D._req("POST", "/console/api/workspaces/current/default-model", {
        "model_settings": [
            {"model_type": "llm", "provider": PROVIDER, "model": LLM_MODEL},
            {"model_type": "text-embedding", "provider": PROVIDER, "model": EMBED_MODEL},
        ],
    }, timeout=60)
    log("[default-model]", code, str(res)[:200])
    if code >= 400:
        raise SystemExit("设置默认模型失败")


def ensure_dataset():
    f = STATE / "dify_dataset_id.txt"
    dsid = f.read_text().strip() if f.exists() else ""
    if dsid:
        code, res = D._req("GET", f"/console/api/datasets?page=1&limit=100", timeout=60)
        rows = (res or {}).get("data") or [] if isinstance(res, dict) else []
        if any(r.get("id") == dsid for r in rows):
            log("[dataset] 已存在:", dsid)
            return dsid
    code, res = D.create_dataset(None, DATASET_NAME)
    log("[dataset] 新建:", code, str(res)[:200])
    if code >= 400 or not isinstance(res, dict) or not res.get("id"):
        raise SystemExit("创建知识库失败")
    dsid = res["id"]
    f.write_text(dsid + "\n")
    return dsid


def _model_cfg(dsid):
    return {
        "model": {"provider": PROVIDER, "name": LLM_MODEL, "mode": "chat", "completion_params": {}},
        "prompt_type": "simple",
        "pre_prompt": "你是个人知识库问答助手，基于检索到的资料回答，引用来源，不确定时明确说明。",
        "dataset_configs": {
            "retrieval_model": "single",
            "datasets": {"strategy": "single", "datasets": [{"dataset": {"id": dsid, "enabled": True}}]},
            "top_k": 4,
            "score_threshold_enabled": False,
            "reranking_enabled": False,
        },
        "retriever_resource": {"enabled": True},
    }


def ensure_app(dsid):
    f_id, f_key = STATE / "app_id.txt", STATE / "app_api_key.txt"
    app_id = f_id.read_text().strip() if f_id.exists() else ""
    if app_id:
        code, res = D._req("GET", f"/console/api/apps?page=1&limit=100", timeout=60)
        rows = (res or {}).get("data") or [] if isinstance(res, dict) else []
        if any(a.get("id") == app_id for a in rows):
            log("[app] 已存在:", app_id)
            if f_key.exists() and f_key.read_text().strip():
                return app_id, f_key.read_text().strip()
    if not app_id:
        code, res = D._req("POST", "/console/api/apps",
                           {"name": APP_NAME, "mode": "chat", "icon": "bot",
                            "icon_background": "#FFEAD5"}, timeout=120)
        log("[app] 新建:", code, str(res)[:200])
        if code >= 400 or not isinstance(res, dict) or not res.get("id"):
            raise SystemExit("创建应用失败")
        app_id = res["id"]
        f_id.write_text(app_id + "\n")

    code, res = D._req("POST", f"/console/api/apps/{app_id}/model-config", _model_cfg(dsid), timeout=120)
    log("[app] model-config:", code, str(res)[:300])
    if code >= 400:
        raise SystemExit("应用模型配置失败")

    code, res = D._req("POST", f"/console/api/apps/{app_id}/api-keys", {"name": "kb-key"}, timeout=60)
    log("[app] api-key:", code, str(res)[:200])
    key = ""
    if isinstance(res, dict):
        key = res.get("token") or (res.get("data") or {}).get("token") or ""
    if key:
        f_key.write_text(key + "\n")
        os.chmod(f_key, 0o600)
    elif f_key.exists():
        key = f_key.read_text().strip()
    if not key:
        raise SystemExit("生成 API Key 失败")
    return app_id, key


def main():
    ensure_setup()
    D.login()
    ensure_plugin()
    ensure_model_credentials()
    ensure_defaults()
    dsid = ensure_dataset()
    app_id, key = ensure_app(dsid)
    log("\n=== bootstrap 完成 ===")
    log("dataset:", dsid)
    log("app:", app_id)
    log("api_key:", key)


if __name__ == "__main__":
    main()
