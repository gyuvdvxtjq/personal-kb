# 如何把自有资料变成知识库语料

本文说明从「你自己的 PDF 归档」到「可导入 Dify 的 Markdown 语料」的完整路径。
**代码仓库不含任何资料与正文**，你需要从自己的资料出发按本流程构建。

---

## 数据从哪来

来源自定：本地 PDF 目录、网盘同步目录均可。本项目的数据形态是
**长截图型 PDF**（每页一张超长图，`pdftotext` 只能取到水印），所以正文必须走 OCR。
如果你的资料是文本型 PDF，可以直接用 `pdftotext`，跳过 OCR 步骤。

本批实测规模（供参考）：1246 个 PDF / 3.7 GB → 1109 篇 Markdown、约 395 万字，
正文中位数 3258 字。

## 完整链路

```
你的 PDF 来源（网盘经 OpenList 挂载 / 本地目录）
  └─ sync/inventory.py       盘点全量目录 → state/inventory.json
      └─ sync/keep_rules.py + classify.py   按规则筛选（保留/复核/排除）
          └─ sync/batch_run.py download      批量下载（断点续传，3 次重试）
              └─ sync/batch_run.py ocr       长截图 PDF → Markdown
                  └─ sync/batch_run.py import（或 sync/import_docs.py）  导入 Dify
```

## 1. 数据源接入

OpenList 是网盘聚合工具，可以把各类网盘挂成类本地目录再通过 API 取直链：

```bash
"$KB_ROOT/tools/openlist/openlist" server \
  --data "$KB_ROOT/tools/openlist/data" \
  --config "$KB_ROOT/tools/openlist/data/config.json"
# 默认端口 5244
```

凭据（管理员密码、网盘凭据）放在 `.secrets/` 目录，**不要提交到 Git**：

```bash
mkdir -p "$KB_ROOT/.secrets"
# 按 OpenList 文档添加你的存储驱动；凭据文件自行管理
python3 sync/openlist_client.py drivers    # 确认存储驱动可用
python3 sync/openlist_client.py setup      # 挂载
```

> 如果资料已在本地目录，跳过 OpenList，直接把 `KB_REMOTE_ROOT` 指向该目录。

## 2. 盘点与筛选

```bash
export KB_REMOTE_ROOT=<你的资料根目录>
python3 sync/inventory.py          # → state/inventory.json
python3 sync/classify.py           # → state/{keep,review,drop}.jsonl
```

筛选规则在 `sync/keep_rules.py`，本项目用到的两层判定（示例）：

- **内容主题层**：排除个股/短线/买卖点/技术指标等交易类标题，
  保留宏观、政策、地缘、货币财政、周期、大类资产类内容
- **来源层**：按系列白名单保留，显式排除不看的系列（示例中已用代号，
  按你自己的语料填真实名称）

## 3. 下载原始文件

```bash
python3 sync/batch_run.py download
```

实现：登录 OpenList 拿 token → `POST /api/fs/get {path}` 取 `raw_url` → 下载。
已存在且大于 1 KB 的文件跳过，**中断后重跑即可续传**。网盘偶发 `HTTP 416`，内置 3 次重试。

## 4. 解析为 Markdown

这批资料每页是一张超长截图，`pdftotext` 只能取到推广水印，正文必须走 OCR：

```bash
bash deploy/run_ocr_shards.sh 8     # 8 路并行，约 3.5 篇/分（8 核）
```

单篇流程：`pdfimages -j` 抽图（比 `-png` 快两个数量级）→ 长图按 2200 px 切片
（重叠 120 px）→ RapidOCR 逐片识别 → 去水印行 → 去重叠行 → 输出 Markdown。

已存在的 `.md` 会跳过，**同样支持断点续跑**。

> 想换 MinerU 云端解析：`python3 sync/mineru_batch.py loop`
> （需在 `deploy/app.env` 配 `MINERU_API_KEY`；见 RESULTS.md 第 4 节的实测状态）

## 5. 导入 Dify

```bash
python3 sync/batch_run.py import          # 原生模式管线
# 或（Docker Compose 路线）
python3 sync/import_docs.py               # 读 KB_STATE_DIR/import_dataset.txt
```

---

## 已知坑

| 问题 | 处理 |
|---|---|
| 网盘返回 `HTTP 416` | 重试 3 次；仍失败则跳过（本批 3 篇） |
| 124 篇正文 < 300 字 | 多为推广页或图片分辨率不足，建议抽样人工核验，必要时走 MinerU |
| OCR 并行反而更慢 | 分片数贴近 CPU 配额（8），每进程 `OMP_NUM_THREADS=1` |
| 网盘上 `stat` 很慢 | 避免 `ls -S` 遍历千级文件，用 Python `os.path.getsize` |
| 网盘凭据过期 | 直链取不到时重新抓凭据，重挂存储 |

---

## 合规提醒

资料版权归原作者，仓库与本流程仅供个人学习检索使用。
**不要把原始 PDF 或解析正文提交到任何代码仓库**；若需在多台机器间同步，
用私有存储。本仓库的 `.gitignore` 已强制排除 `corpus*/`，提交前再跑一次
`bash deploy/sanitize_check.sh`。
