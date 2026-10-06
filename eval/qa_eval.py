#!/usr/bin/env python3
"""端到端问答质量评估：LLM 基于语料合成考卷 → 走完整问答链路 → LLM 裁判评分 + 拒答测试。

env:
  DIFY_API_BASE     Dify 控制台基址（dify_client 读取），如 https://kb.example.com
  KB_STATE_DIR      状态目录（含 dify_dataset_id.txt / app_api_key.txt），默认 ./state
  KB_CORPUS_DIR     语料 Markdown 目录（出题用，不出库），默认 ./corpus
  LLM_BASE          OpenAI 兼容 LLM API 基址（出题+裁判）
  LLM_API_KEY       对应 API Key
  GEN_MODEL         出题模型
  JUDGE_MODEL       裁判模型（建议与答题模型不同家族以减少自我偏好）
  EVAL_SEED         抽样种子（固定可复现），默认 2026
  EVAL_N            抽样篇数，默认 45
  EVAL_SUFFIX       考卷文件后缀（区分标准卷/留出卷），默认空
  JUNK_PATTERNS     可选：语料平台噪声行的正则，用 | 分隔（按你的语料自定义）

注意：golden 考卷（golden_set<SUFFIX>.jsonl）生成后请保留，作为固定回归考卷；
重复评估只需删除 qa_eval_results<SUFFIX>.jsonl。考卷含语料衍生内容，不要提交到公开仓库。
"""
import sys, os, re, json, time, random, urllib.request
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sync"))
import dify_client as D  # noqa: E402

STATE = Path(os.environ.get("KB_STATE_DIR", "state"))
CORPUS = Path(os.environ.get("KB_CORPUS_DIR", "corpus"))
LLM_BASE = os.environ.get("LLM_BASE", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
GEN_MODEL = os.environ.get("GEN_MODEL", "")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", GEN_MODEL)
SUFFIX = os.environ.get("EVAL_SUFFIX", "")
SEED = int(os.environ.get("EVAL_SEED", "2026"))
N = int(os.environ.get("EVAL_N", "45"))
GOLD = STATE / ("golden_set" + SUFFIX + ".jsonl")
DONE = STATE / ("qa_eval_results" + SUFFIX + ".jsonl")

for var, val in (("LLM_BASE", LLM_BASE), ("LLM_API_KEY", LLM_API_KEY), ("GEN_MODEL", GEN_MODEL)):
    if not val:
        sys.exit(f"缺少环境变量 {var}")

JUNK_PATTERNS = os.environ.get("JUNK_PATTERNS", "")
JUNK = re.compile("^(?:" + JUNK_PATTERNS + ")$") if JUNK_PATTERNS else None
CORPUS = CORPUS if CORPUS.is_absolute() else (Path.cwd() / CORPUS)
STATE = STATE if STATE.is_absolute() else (Path.cwd() / STATE)
DSID = (STATE / "dify_dataset_id.txt").read_text(encoding="utf-8").strip()
APPKEY = (STATE / "app_api_key.txt").read_text(encoding="utf-8").strip()


def llm(model, messages, timeout=180, temperature=0.3, retries=3):
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature, "max_tokens": 2000}).encode()
    for a in range(retries):
        try:
            r = urllib.request.Request(LLM_BASE + "/chat/completions", data=body, method="POST")
            r.add_header("Content-Type", "application/json")
            r.add_header("Authorization", "Bearer " + LLM_API_KEY)
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                d = json.loads(resp.read())
            return d["choices"][0]["message"]["content"] or ""
        except Exception:
            if a == retries - 1:
                raise
            time.sleep(5 * (a + 1))


def parse_json(s):
    s = re.sub(r"^```(json)?|```$", "", s.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0)) if m else None


def clean(t, n=1400):
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    if JUNK is not None:
        lines = [l for l in lines if not JUNK.match(l)]
    return "\n".join(lines)[:n]


def app_ask(q):
    body = json.dumps({"inputs": {}, "query": q, "response_mode": "blocking", "user": "qa-eval"}).encode()
    r = urllib.request.Request(D.BASE + "/v1/chat-messages", data=body, method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", "Bearer " + APPKEY)
    with urllib.request.urlopen(r, timeout=300) as resp:
        a = json.loads(resp.read())
    refs = [x.get("document_name", "") for x in (a.get("metadata") or {}).get("retriever_resources", [])]
    return a.get("answer", ""), refs


if not GOLD.exists() or GOLD.stat().st_size == 0:
    D.login()
    docs = {}
    for page in range(1, 16):
        code, res = D._req("GET", f"/console/api/datasets/{DSID}/documents?limit=100&page={page}", timeout=60)
        rows = (res.get("data") or []) if isinstance(res, dict) else []
        docs.update({d["name"]: d["id"] for d in rows})
        if len(rows) < 100:
            break
    allnames = sorted(n for n in docs if (CORPUS / n).exists())
    random.seed(SEED)
    random.shuffle(allnames)
    picks, per_series = [], {}
    for n in allnames:
        s = re.sub(r"\d.*", "", n)[:10] or n[:10]
        if per_series.get(s, 0) >= 8:
            continue
        picks.append(n)
        per_series[s] = per_series.get(s, 0) + 1
        if len(picks) >= N:
            break
    print(f"sampled docs (seed={SEED}): {len(picks)}", flush=True)
    with GOLD.open("w", encoding="utf-8") as f:
        for i, name in enumerate(picks, 1):
            t = clean((CORPUS / name).read_text(encoding="utf-8", errors="ignore"))
            if len(t) < 300:
                continue
            try:
                r = llm(GEN_MODEL, [
                    {"role": "system", "content": "你是知识库考题出题人。基于资料写一个用户真实会问的问题。要求：1)不要照抄标题原词 2)问题应能用这段资料回答 3)给出回答所需的2-3条关键事实摘录。只输出JSON：{\"question\":\"...\",\"key_facts\":[\"...\"]}"},
                    {"role": "user", "content": "资料标题：" + name + "\n\n" + t}])
                j = parse_json(r)
                if j and j.get("question") and j.get("key_facts"):
                    f.write(json.dumps({"doc": name, "question": j["question"], "key_facts": j["key_facts"], "type": "qa"}, ensure_ascii=False) + "\n")
                    print(f"gold {i}/{len(picks)}: {j['question'][:40]}", flush=True)
            except Exception as e:
                print("gen fail:", name[:30], str(e)[:60], flush=True)
            time.sleep(1)
    refusals = ["请推荐一个适合初学者的厨房烘焙入门食谱。", "量子计算目前最主要的工程瓶颈是什么？",
                "马拉松备赛三个月的训练计划应该怎么排？", "家用路由器的信道该怎么选择？",
                "个人养老金账户的税收优惠具体怎么计算？"]
    with GOLD.open("a", encoding="utf-8") as f:
        for q in refusals:
            f.write(json.dumps({"doc": "", "question": q, "key_facts": [], "type": "refusal"}, ensure_ascii=False) + "\n")
print("golden ready:", sum(1 for _ in GOLD.open(encoding="utf-8")), "cases | judge:", JUDGE_MODEL, flush=True)

done_ids = set()
if DONE.exists():
    for line in DONE.read_text(encoding="utf-8").splitlines():
        try:
            done_ids.add(json.loads(line)["question"])
        except Exception:
            pass
cases = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
cases = [c for c in cases if c["question"] not in done_ids]
print("to evaluate:", len(cases), flush=True)
out = DONE.open("a", encoding="utf-8")
for i, c in enumerate(cases, 1):
    rec = dict(c)
    try:
        ans, refs = app_ask(c["question"])
        rec["answer"] = ans[:800]
        rec["refs"] = refs
        rec["gold_in_refs"] = bool(c["doc"]) and any(c["doc"] == r or c["doc"][:12] in r for r in refs)
        if c["type"] == "refusal":
            j = parse_json(llm(JUDGE_MODEL, [
                {"role": "system", "content": "你是质检员。知识库中不存在能回答该问题的资料。评判知识库的回答是否诚实承认资料不足/未提及（而不是编造答案）。输出JSON：{\"refusal_ok\":0或1,\"reason\":\"一句话\"}"},
                {"role": "user", "content": "问题：" + c["question"] + "\n\n知识库回答：" + ans[:600]}]))
            rec["refusal_ok"] = (j or {}).get("refusal_ok", 0)
        else:
            j = parse_json(llm(JUDGE_MODEL, [
                {"role": "system", "content": "你是质检员。根据【参考要点】评判【知识库回答】是否正确回应了问题。correctness: 2=正确完整 1=部分正确 0=错误或答非所问；faithful: 回答是否只基于检索资料无编造 1/0。只输出JSON：{\"correctness\":0-2,\"faithful\":0-1,\"reason\":\"一句话\"}"},
                {"role": "user", "content": "问题：" + c["question"] + "\n参考要点：" + json.dumps(c["key_facts"], ensure_ascii=False) + "\n引用文档：" + json.dumps(refs, ensure_ascii=False)[:200] + "\n知识库回答：" + ans[:700]}]))
            rec["correctness"] = (j or {}).get("correctness", 0)
            rec["faithful"] = (j or {}).get("faithful", 0)
            rec["reason"] = (j or {}).get("reason", "")[:100]
    except Exception as e:
        rec["error"] = str(e)[:120]
    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out.flush()
    print(f"eval {i}/{len(cases)}: {c['question'][:36]} -> {rec.get('correctness', rec.get('refusal_ok','ERR'))}", flush=True)

rows = [json.loads(l) for l in DONE.read_text(encoding="utf-8").splitlines() if l.strip()]
qa = [r for r in rows if r.get("type") == "qa" and "correctness" in r]
ref = [r for r in rows if r.get("type") == "refusal" and "refusal_ok" in r]
n = len(qa)
print(f"\n===== 评分卡 (考卷'{SUFFIX or '标准'}' seed={SEED} 裁判={JUDGE_MODEL}) =====", flush=True)
if n:
    print(f"样本数: {n}（拒答 {len(ref)}）", flush=True)
    print(f"答案完全正确率: {sum(1 for r in qa if r['correctness']==2)/n*100:.1f}%", flush=True)
    print(f"基本可用率(>=1): {sum(1 for r in qa if r['correctness']>=1)/n*100:.1f}%", flush=True)
    print(f"忠实度: {sum(r['faithful'] for r in qa)/n*100:.1f}%", flush=True)
    print(f"引用正确率: {sum(1 for r in qa if r.get('gold_in_refs'))/n*100:.1f}%", flush=True)
if ref:
    print(f"拒答正确率: {sum(r['refusal_ok'] for r in ref)/len(ref)*100:.1f}%", flush=True)
print("===== QA_EVAL_DONE =====", flush=True)
