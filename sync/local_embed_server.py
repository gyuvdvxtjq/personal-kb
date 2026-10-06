#!/usr/bin/env python3
"""本地 bge-m3 Embedding 服务：OpenAI 兼容接口，供 Dify 调用

启动:
  python3 sync/local_embed_server.py
接口:
  POST /v1/embeddings   {"model":"bge-m3","input":["文本1","文本2"]}
  GET  /v1/models
  GET  /health
"""
import os
import sys
import threading
import time

MODEL_PATH = os.environ.get(
    "LOCAL_EMBED_MODEL",
    os.path.join(os.environ.get("KB_ROOT","/opt/kb"),"runtime/models/models/BAAI--bge-m3/snapshots/master"),
)
PORT = int(os.environ.get("LOCAL_EMBED_PORT", "8100"))
MODEL_NAME = os.environ.get("LOCAL_EMBED_NAME", "bge-m3")

sys.path.insert(0, os.path.join(os.environ.get("KB_ROOT","/opt/kb"),"runtime/chroma/lib/python3.12/site-packages"))

from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import uvicorn  # noqa: E402

app = FastAPI(title="Local bge-m3 Embedding")
_model = None
_load_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _load_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                t0 = time.time()
                _model = SentenceTransformer(MODEL_PATH, device="cpu")
                print(f"model loaded in {time.time()-t0:.1f}s", flush=True)
    return _model


class EmbedRequest(BaseModel):
    model: str | None = MODEL_NAME
    input: list[str] | str
    encoding_format: str | None = "float"


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}


@app.post("/v1/embeddings")
@app.post("/embeddings")
def embed(req: EmbedRequest):
    texts = req.input if isinstance(req.input, list) else [req.input]
    texts = [t if t.strip() else " " for t in texts]
    m = get_model()
    vecs = m.encode(
        texts,
        batch_size=8,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    data = [
        {"object": "embedding", "index": i, "embedding": [float(x) for x in v]}
        for i, v in enumerate(vecs)
    ]
    return {
        "object": "list",
        "data": data,
        "model": req.model or MODEL_NAME,
        "usage": {"prompt_tokens": sum(len(t) for t in texts), "total_tokens": sum(len(t) for t in texts)},
    }


if __name__ == "__main__":
    print("preloading embedding model...", flush=True)
    t0 = time.time()
    m = get_model()
    print(f"weights loaded in {time.time()-t0:.1f}s", flush=True)
    t1 = time.time()
    m.encode(["预热文本"], normalize_embeddings=True, show_progress_bar=False)
    print(f"warmup encode done in {time.time()-t1:.1f}s", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
