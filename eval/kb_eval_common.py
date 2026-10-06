"""评测共用的文档标识解析：把稳定的标签解析成当前的 document_id。

重建索引会改变 document_id，因此评测标签不能写死 id，改用文档名子串解析。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "sync"))
import dify_client as D  # noqa: E402


def resolve_dataset_id() -> str:
    return (ROOT.parent / "state" / "dify_dataset_id.txt").read_text(encoding="utf-8").strip()


def load_keys():
    return json.loads((ROOT / "doc_keys.json").read_text(encoding="utf-8"))


def current_doc_ids(dataset_id: str):
    """返回 {文档名: 当前 document_id}。"""
    D.login()
    code, res = D.list_docs(None, dataset_id, limit=100)
    if code != 200:
        raise RuntimeError(f"list_docs failed {code}: {res}")
    return {doc["name"]: doc["id"] for doc in res.get("data", [])}


def resolve_labels(dataset_id: str | None = None) -> dict[str, str]:
    """{标签: 当前 document_id}；缺失标签直接报错，避免静默算成未召回。"""
    dataset_id = dataset_id or resolve_dataset_id()
    keys = load_keys()
    docs = current_doc_ids(dataset_id)
    resolved = {}
    missing = []
    for label, needle in keys.items():
        hits = [doc_id for name, doc_id in docs.items() if needle in name]
        if len(hits) != 1:
            missing.append((label, needle, len(hits)))
        else:
            resolved[label] = hits[0]
    if missing:
        raise RuntimeError(f"标签解析失败: {missing}")
    return resolved


def load_cases(path=None):
    cases = []
    target = Path(path) if path else ROOT / "recall_eval.jsonl"
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases