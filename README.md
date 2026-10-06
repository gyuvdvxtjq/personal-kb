# personal-kb：从一堆长截图 PDF 到可评测的私有知识库

把 1200+ 篇只有「长截图」的 PDF，变成一个检索质量可量化、故障可恢复的个人问答系统。
同一个项目在两种受限环境下各跑了一套完整实现：

| | 阶段 1：受限容器环境 | 阶段 2：自有 VPS（当前线上） |
|---|---|---|
| 部署形态 | 禁止 Docker，Dify 源码原生运行（tmux） | Docker Compose（官方编排） |
| Embedding | 本地 bge-m3（:8100） | 云端 OpenAI 兼容 API（bge-m3，免费档） |
| LLM | 魔搭 Qwen（按量） | OpenAI 兼容中转（按量，可切换供应商） |
| 评测 | 21 道人工 hard 题，recall@K / MRR | 42 题合成考卷 + LLM 裁判 + 拒答测试 |

**两层评测口径不同、不可跨阶段对比**：阶段 1 测检索召回，阶段 2 测端到端答案质量。
两个环境的完整记录都保留：阶段 1 见 [RESULTS.md](RESULTS.md)，阶段 2 见下文评分卡。

这个仓库记录的不是「搭了一个 RAG」，而是四件更难的事：

1. **语料工程**：`pdftotext` 只能抽到水印的 PDF，如何在 8 核 CPU 上以 3.5 篇/分钟 OCR 出 395 万字正文
2. **可恢复性**：一次数据库丢失事故后，全栈幂等重建、召回指标逐项复现一致
3. **内容准入**：1109 篇语料按内容策略清理到 680 篇（-39%），并从一次失败的清洗实验中定位出「OCR 噪声承担区分信号」的反直觉根因
4. **评估体系**：暴力精确检索做无 rerank 基线 + 合成考卷端到端评分 + 留出集验证泛化 + 拒答测试

## 阶段 2 评分卡（当前线上配置）

端到端：LLM 基于语料合成考卷（每卷 42 行＝37 道可答题＋5 道库外拒答题）→ 走完整问答链路 → LLM 裁判评分。
评分卡只计成功获得裁判评分的可答题（标准卷 36/37、留出卷 34/37，其余为裁判调用失败）；拒答测试另计。
**留出考卷**（seed 777）：另抽 37 篇文档出全新题目（33 篇未出现在标准考卷），用于验证泛化——**分数与标准考卷一致，无过拟合**：

| 指标 | 标准考卷（seed 2026） | 留出考卷（seed 777） |
|---|---|---|
| 引用正确率 | 94.4% | **100%** |
| 基本可用率（≥部分正确） | 100% | **100%** |
| 答案完全正确率 | 58.3% | 55.9% |
| 忠实度（无编造） | 66.7% | 73.5% |
| 拒答诚实度（库外问题） | 100% | **100%** |

完全正确率 58% 的含义：41% 的回答为「部分正确」（判 1 分）——引用与方向正确，
但未覆盖出题人预设的全部要点；「完全错误」（0 分）极少。两个口径一起看：
下限（可用率 100%）说明不会答错，上限（58%）说明完整性还有空间。

### 检索层 A/B（同一组样本，固定种子）

| 实验 | 结论 |
|---|---|
| + 免费 rerank（bge-reranker-v2-m3） | recall@1 **+12.5pp**，采纳 |
| 混合检索 vs 语义检索（+rerank） | 实测无差异——Chroma provider 的全文检索返回空，**混合检索未真正生效**（与阶段 1 结论一致）；数据集已回退 semantic |
| 语料激进清洗 + 元数据头 | recall@1 **-27pp**，否决。根因：每日/每期系列文档内容雷同，OCR 噪声（时间戳、编号）意外是唯一区分信号，清洗后重排器无法区分 |
| 断行重排 + 清洗（单一系列验证） | 现有配置该系列 recall@1 已达 94.7%，实验方案无增益且引入重复导入混淆，不采纳 |

### 召回对照：同一批题，实际链路 vs 无 rerank 基线

留出考卷的 37 道可答题（库外拒答题不参与检索评测），同一 gold 口径（金标文档任一分段入选即命中）、同一库（5733 分段）：

| 检索方式 | recall@1 | recall@2 | MRR |
|---|---|---|---|
| Dify 实际链路（semantic + rerank，生产配置 top_k=6） | **91.9%** | **97.3%** | 94.6% |
| 暴力精确检索（纯 bge-m3 余弦全库扫描，无 ANN、无 rerank） | 75.7% | 94.6% | — |

- 暴力精确检索是**无 rerank 的纯向量排序基线**：金标段 75.7% 排第 1、94.6% 在前 2，top-10 无漏召——候选池质量够，瓶颈在排序而非召回
- rerank（cross-encoder）在余弦排序之上把排第 2 的金标大量提为第 1：R@1 **+16.2pp**，与标准考卷 A/B（+12.5pp）方向一致
- 结论：**检索不是端到端完全正确率（55.9%）的瓶颈**——36/37 题金标在前 2，丢分主要在答案生成环节（要点覆盖、忠实度）

## 架构（阶段 2）

```
用户 ──HTTPS──> Nginx(Dify)
                 ├─ Dify API/Worker（问答编排、切片、rerank 调度）
                 ├─ PostgreSQL（元数据）── Redis（队列）
                 ├─ plugin_daemon（openai_api_compatible 插件）
                 ├─ Chroma 0.5.20（向量库，semantic + rerank 后 top-k）
                 └─ 模型面（全部 OpenAI 兼容接口，可随时替换供应商）
                     ├─ Embedding: BAAI/bge-m3（免费档）
                     ├─ Rerank:    BAAI/bge-reranker-v2-m3（免费档）
                     └─ LLM:      qwen3.8-27b（按量，切换供应商只改凭证）
```

选型逻辑：**无 GPU、成本趋近零、组件全部可替换**。

## 资料流水线（阶段 1 建立，两阶段共用）

```
你的 PDF 目录
  └─ sync/inventory.py      盘点
      └─ sync/keep_rules.py + classify.py   按规则筛选（保留/复核/排除）
          └─ sync/batch_run.py download     批量下载（断点续传，3 次重试）
              └─ sync/batch_run.py ocr      pdfimages -j → 2200px 切片(重叠 120px) → RapidOCR → 去水印/去重叠行 → Markdown
                  └─ sync/import_docs.py    导入 Dify（幂等断点续传）
```

本批实测：1246 篇 / 3.7 GB，1109 篇 OCR 产出（正文中位数 3258 字），导入幂等。
细节与已知坑见 [docs/DATA_ACQUISITION.md](docs/DATA_ACQUISITION.md)。仓库不含任何资料与正文。

## 评估体系

```bash
# 端到端评分卡：LLM 出题 → 完整问答 → LLM 裁判 + 拒答测试
python3 eval/qa_eval.py                                            # 标准考卷（回归用）
EVAL_SUFFIX=holdout EVAL_SEED=777 python3 eval/qa_eval.py          # 留出考卷（泛化验证）

# 检索单项：recall@K / MRR（固定种子可复现）
python3 eval/synthetic_recall.py

# 召回对照：同一批考题，生产链路（hit-testing）vs 无 rerank 基线
python3 eval/pipeline_recall_holdout.py         # CASES_FILE=考题 JSONL（含金标文档前缀）
python3 eval/exact_baseline.py eval/cases.jsonl  # 需直连向量库/DB，按脚本注释配置
```

设计要点：

- **双考卷**：标准考卷做回归（每次改动重跑防退化），留出考卷用全新文章验证泛化。实测两卷一致
- **重复评估只删 `qa_eval_results<SUFFIX>.jsonl`，保留 golden 考卷**，考题才可复现
- **裁判偏差**：裁判与答题同模型有自我偏好风险；实测 glm-5.3-flash 裁判超时率 >50% 不可用，故用同模型裁判并在此注明
- **逐题明细脱敏落盘**：考题由语料衍生，仓库只提交「题号哈希 + 分数 + 命中」，不含题目与引文

## 快速开始（阶段 2 路线）

```bash
# 1. Dify 官方 compose
git clone --depth 1 https://github.com/langgenius/dify.git /opt/kb/dify
cd /opt/kb/dify/docker && cp .env.example .env
# 编辑 .env：SECRET_KEY / VECTOR_STORE=chroma / COMPOSE_PROFILES=chroma,postgresql,collaboration
cp deploy/compose/docker-compose.override.yaml /opt/kb/dify/docker/   # fd 调优 + DB 回环端口
docker compose up -d

# 2. Chroma 连接（host 用服务名，token 两端配对——模板里有说明）
cp deploy/compose/chroma.env.template /opt/kb/dify/docker/envs/vectorstores/chroma.env

# 3. 模型供应商：openai_api_compatible 插件，添加 Embedding/Rerank/LLM 三类凭证

# 4. 导入语料 + 评估
export DIFY_API_BASE=https://kb.example.com KB_STATE_DIR=/opt/kb/state KB_CORPUS_DIR=/opt/kb/corpus
python3 sync/import_docs.py
python3 eval/qa_eval.py
```

阶段 1（受限环境）路线：`source deploy/app.env && bash deploy/start_stack.sh`，
完整记录见 [RESULTS.md](RESULTS.md)。

## 项目结构

```
deploy/           start_stack.sh（阶段1 幂等启动）；run_ocr_shards.sh（并行 OCR）；
                  compose/（阶段2 的 chroma.env 模板与 override）；sanitize_check.sh（提交前自检）
sync/             batch_run.py 流水线入口；ocr_pipeline.py；import_docs.py（幂等导入）；
                  dify_client.py（含 Dify 1.17 __Host-csrf_token 兼容）；keep_rules.py / classify.py（筛选规则）
eval/             qa_eval.py（端到端评分卡）；synthetic_recall.py（recall@K/MRR）；
                  pipeline_recall_holdout.py（同题对照：生产链路）；exact_baseline.py（无 rerank 暴力基线）；
                  eval_recall.py / kb_eval_common.py（阶段1 工具）
docs/             DATA_ACQUISITION.md（资料流水线与已知坑）
RESULTS.md        阶段1 全部实测数字与事故复盘
```

## 已知限制

- 合成考卷由语料生成、题目保证可答，测的是**链路质量上限**，不是真实用户问题的分布
- 裁判与答题同模型（同家族偏差）；计分样本 34-37 题，单项指标噪声 ±7pp，用于回归对比而非绝对水平
- 忠实度 67-74% 的主要成因是回答混入常识性衔接表述，LLM 裁判对此偏严
- Chroma provider 全文检索为空，混合检索在该组合下未真正生效；换 Weaviate/pgvector 才能验证
- 137 篇 PDF 尚未 OCR；3 篇因源站 HTTP 416 无法下载
- 实际链路 recall@2 = 97.3% 仍剩 1 题未命中（rerank 候选池截断）；调大 top_k 或换 pgvector/Weaviate 可再验

## 踩坑记录

1. **语料清洗反而降召回（-27pp）**：每日/每期系列文档内容高度雷同，OCR 噪声（时间戳、编号）意外是唯一区分特征；清洗后文档更相似，重排器无法区分。教训：**清洗前先验证「垃圾行是否承担区分功能」**
2. **Dify 1.17 cookie 改名**：控制台 API 的 CSRF cookie 从 `csrf_token` 变为 `__Host-csrf_token`，脚本端需用 `endswith` 兼容匹配（`sync/dify_client.py` 已修）
3. **Worker 容器文件描述符默认 1024**：批量索引 1000+ 文档时 `Too many open files` 全批报错。用 `docker-compose.override.yaml` 把 worker/api 的 nofile 提到 65536
4. **certbot 官方镜像没有 bash**：`docker compose exec certbot /update-cert.sh` 报 `no such file`，要用 `sh /update-cert.sh`
5. **Celery 任务不落盘**：机器重启丢在途索引任务，文档永远停在 waiting。重启后需对 waiting/error 文档调 `POST /datasets/{id}/retry` 批量重触发
6. **compose 的 env_file 是 optional**：`envs/vectorstores/chroma.env` 模板里客户端凭证默认为空，而服务端要求 token——照抄 example 会 401，两端必须显式配对，且 `CHROMA_HOST` 用服务名不能用 127.0.0.1
7. **容器重建使控制台会话失效**：`.env` 变更触发 api 容器重建后，脚本要重新登录
8. **nginx HTTPS 重定向丢 Cookie**：以 `http://127.0.0.1` 为基址的脚本在启用 HTTPS 后被跨域重定向丢会话，改用域名基址
9. **pkill 自匹配**：`pkill -f <脚本名>` 会匹配到执行它的 shell 自身，用 `kill <pid>` 或正则锚定
10. **git 历史无法靠 force-push 洗净**：公开化仓库用全新历史重建，而不是在旧历史上删文件

## 成本

| 项 | 费用 |
|---|---|
| Embedding（bge-m3） | ¥0（免费档） |
| Rerank（bge-reranker-v2-m3） | ¥0（免费档） |
| LLM 问答 | 按量，代金券可支撑数千次问答 |
| 评估跑分（约 150 次 LLM 调用） | 个位数人民币 |
| VPS | 2C4G 即可（+4G swap） |

## License

MIT。仓库不含任何语料正文、密钥与个人数据（`.gitignore` 强制排除 `corpus*/ state/ runtime/`，提交前跑 `bash deploy/sanitize_check.sh`）。
