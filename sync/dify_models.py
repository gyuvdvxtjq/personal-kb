#!/usr/bin/env python3
"""配置 Dify 模型供应商：魔搭 Qwen 对话模型 + 本地 bge-m3 Embedding"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dify_client as D

MS_TOKEN = os.environ.get("MODELSCOPE_ACCESS_TOKEN", "")
MS_BASE = "https://api-inference.modelscope.cn/v1"
EMBED_BASE = "http://127.0.0.1:8100/v1"


def _post(path, payload):
    return D._req("POST", path, payload)


def main():
    D.login()

    base = "/console/api/workspaces/current/model-providers"

    # 1) 对话模型：魔搭 OpenAI 兼容（LLM）
    code, res = _post(f"{base}/openai_api_compatible/credentials", {
        "name": "modelscope-qwen",
        "credentials": {
            "api_key": MS_TOKEN,
            "endpoint_url": MS_BASE,
            "extra_headers": "{}",
            "mode": "chat",
        },
    })
    print("魔搭 LLM:", code, str(res)[:300])

    # 2) Embedding：本地 bge-m3
    code2, res2 = _post(f"{base}/openai_api_compatible/credentials", {
        "name": "local-bge-m3",
        "credentials": {
            "api_key": "local-embed",
            "endpoint_url": EMBED_BASE,
            "extra_headers": "{}",
            "mode": "embedding",
        },
    })
    print("本地 Embedding:", code2, str(res2)[:300])


if __name__ == "__main__":
    main()
