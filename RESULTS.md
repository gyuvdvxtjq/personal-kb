# 结果报告

记录这套知识库从冷启动重建到批量资料处理的实测结果。所有数字来自实际运行，非估算。

---

## 1. 环境

| 项 | 值 |
|---|---|
| 部署形态 | Dify 源码原生模式（**无容器**，环境禁止 Docker） |
| CPU 配额 | 8 核（`cpu.max=800000/100000`），宿主机 steal time 约 30% |
| 内存 / 磁盘 | 28 GB / 持久 NAS（`/mnt/workspace`） |
| 数据库 | PostgreSQL 14，数据目录 `$KB_ROOT/runtime/pgdata`（**必须在持久盘**） |
| 向量库 | Chroma 0.5.20，`hnsw:space=cosine`、`search_ef=200` |
| Embedding | 本地 bge-m3（8100 端口，1024 维） |
| LLM | 魔搭 `Qwen/Qwen3.5-122B-A10B` |

**关键教训**：Dify 默认把 PG 数据放在 `/var/lib/postgresql`，容器重置即丢。
一次重置导致 20 篇文档元数据全失、而 Chroma 向量仍在（267 条孤儿向量），
必须删库重建。改为持久盘 + `start_stack.sh` 幂等重建后，问题根除。

---

## 2. 冷启动重建回归

PG 数据目录被清空后，从零恢复全栈并复测，指标与基线**完全一致**：

| 指标 | 基线 | 重建后 |
|---|---|---|
| recall@1 | 0.9524 | 0.9524 |
| recall@2 | 0.9524 | 0.9524 |
| recall@4 | 1.0 | 1.0 |
| recall@10 | 1.0 | 1.0 |
| MRR | 0.9643 | 0.9643 |

（21 道正文/易混题；简单题集全 1.0。hybrid_search 与 semantic_search 相同，
因 Chroma provider 的 `search_by_full_text` 直接返回空。）

重建耗时：全栈就绪约 2.5 分钟（其中 bge-m3 冷启动含模型加载+首帧，约 80 秒）。

---

## 3. 资料处理结果

### 3.1 下载

| 项 | 值 |
|---|---|
| 清单总量 | 1249 篇（keep 1249 / review 170 / drop 2268） |
| 成功下载 | 1246 篇，3.7 GB |
| 失败 | 3 篇（<你的网盘>返回 `HTTP 416 Range Not Satisfiable`，重试无效） |

### 3.2 OCR（长截图 PDF → Markdown）

这类资料每页是一张超长截图，`pdftotext` 只能取到推广水印，正文必须走 OCR。

| 项 | 值 |
|---|---|
| 已产出 Markdown | **1109 篇** |
| 待处理 | 137 篇 |
| 正文总字数 | **3,949,809** |
| 单篇中位 / 最大 / 最小 | 3,258 / 27,643 / 25 字 |

字数分布：

| 区间 | 篇数 | 占比 |
|---|---|---|
| < 1k 字 | 228 | 20.6% |
| 1–5k 字 | 624 | 56.3% |
| 5–10k 字 | 223 | 20.1% |
| ≥ 10k 字 | 34 | 3.1% |

### 3.3 性能：并行度不是越高越好

| 方案 | 速率 |
|---|---|
| 单进程 | 2 篇/分 |
| 16 分片 × OMP_NUM_THREADS=4 | **0.25 篇/分**（严重倒退） |
| 8 分片 × 单线程 | 3.2–3.5 篇/分 |

原因：容器只有 8 核配额，多线程 OpenMP 争抢 + busy-wait 把吞吐打崩。
正确做法是**分片数贴近核数、每进程单线程**
（`OMP_NUM_THREADS=1 OMP_WAIT_POLICY=PASSIVE`）。

### 3.4 关键优化：抽图格式

`pdfimages` 的输出格式对耗时影响是两个数量级：

| 命令 | 单文件（9.9 MB）耗时 | 产物大小 |
|---|---|---|
| `pdfimages -png` | 49.3 s | 67 MB |
| `pdfimages -j`（JPEG） | **0.1 s** | 9.9 MB |
| `pdftoppm -r110 -jpeg` | 2.8 s | 12 MB |

OCR 本身对 79 个切片约需 104 秒，是真正瓶颈；换 JPEG 省下的 49 秒约占单篇 30%。

### 3.5 已知质量问题

- **124 篇内容过短（< 300 字）**：多为推广页占位、正文是图片但分辨率不足，
  或本身就只有一两段。需要人工抽检，必要时走 MinerU 通道重解析。
- **3 篇下载失败**（<你的网盘> 416）。

---

## 4. MinerU 通道实测（未采纳为主力）

| 接口 | 结果 |
|---|---|
| `POST /api/v4/extract/task`（直接上传） | `HTTP 413 / -10006 file upload not allowed`——**文档明确不支持文件直传** |
| `POST /api/v4/file-urls/batch` + PUT 签名 URL | 申请与上传均成功（HTTP 200），但任务状态长期 `pending` |
| 官方 demo 文件（1 页） | 同样 `pending`，`pipeline` 与 `vlm` 均如此 → **服务端调度异常，非账号额度问题** |
| Agent 轻量 API（免 Token） | 秒级返回，但长截图被截断：990 字 vs 本地完整 6809 字，不适合做主力 |

结论：MinerU 质量上限更高，但当前不可用。`sync/mineru_batch.py` 保留为可选通道，
后台 `loop` 会持续轮询，服务恢复后自动下载 `full.md` 覆盖本地结果。

---

## 5. Chroma 检索调优（已内置）

两个根因级问题，都会静默劣化召回：

1. **未指定度量空间**：不传 metadata 时 Chroma 默认 `hnsw:space=l2`，
   而 provider 用 `score = 1 - distance`，实际算出来是 `2*cos - 1`，
   被 Dify 检索层的阈值逻辑过滤，导致 `top_k=4` 实际只返回 1 条。
   修法：所有 `get_collection` 统一带
   `{"hnsw:space":"cosine","hnsw:search_ef":200,"hnsw:construction_ef":200}`。
2. **默认 `search_ef=10`**：会丢失约 10% 的精确 top-10（含已知金标第 4 名）。
   调到 200 后，ANN 相对暴力精确检索的损失为 **0/40**。

附带：chromadb 0.5.20 只接受 `hnsw:construction_ef`，
写成 `hnsw:ef_construction` 会报 `Unknown HNSW parameter` 并让 upsert 全部 500。

---

## 6. 交付状态

- 密钥、原始资料、解析正文、私有路径均**未入库**（脱敏自检通过）。

## 7. 复现命令

```bash
source deploy/app.env
bash deploy/start_stack.sh                 # 幂等启动/恢复全栈
python3 sync/bootstrap.py                  # 账号/插件/模型/知识库/应用
bash deploy/run_ocr_shards.sh 8            # 并行 OCR
python3 sync/batch_run.py import           # 导入 Dify
python3 eval/eval_recall.py --cases eval/recall_eval_hard.jsonl   # 召回评测
```