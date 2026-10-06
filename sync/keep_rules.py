#!/usr/bin/env python3
"""保留/排除规则集中配置（管线共用）

三类结果：
1) keep      : 明确进入知识库
2) drop      : 明确排除（保留清单，不删除源文件）
3) review    : 规则无法判断，需人工复核
"""
import os
import re

KB = os.environ.get("KB_ROOT", "/opt/kb")

# 目录级规则：只处理 文集/{月}/{日}/推广材料/**
ALLOWED_DIR_MARKER = "/推广材料/"

KEEP_AUTHORS = [
    "系列A", "系列B", "系列C", "系列D", "系列E", "系列F",
    "系列G", "系列H", "系列I", "系列J", "系列K",
    "系列L", "系列M", "系列N", "系列O", "系列P",
    "系列Q", "系列R", "系列S", "系列T", "系列U",
    "系列V", "系列W",
]

# 系列V之后默认保留，但显式排除这些
DROP_AUTHORS_DEFAULT_ON = ["系列X", "系列Y", "系列Z", "系列AA", "系列AB"]

# 完全不看
DROP_AUTHORS_HARD = ["系列AC"]

# 作者归一化的额外别名
AUTHOR_ALIAS = {
    "系列W-别名01": "系列W",
    "系列W-别名02": "系列W",
}

# 用于日期前缀场景的作者回退匹配（长名优先）
KNOWN_AUTHORS = sorted(
    set(KEEP_AUTHORS + DROP_AUTHORS_DEFAULT_ON + DROP_AUTHORS_HARD + list(AUTHOR_ALIAS.keys())),
    key=len,
    reverse=True,
)

# 文件名包含即排除（作者名之外）
DROP_SUBSTRINGS = ["示例排除栏目A", "示例排除栏目B"]

# 具体炒股：个股、买卖点、短线、技术指标、盘面操作
# 注意：只用于标题判断，且对系列A豁免
STOCK_TRADE_PATTERNS = [
    r"个[骨股]|个股",
    r"买卖点|买点|卖点",
    r"短线|波段|做[TD]|T\+0",
    r"技术指标|均线|MACD|KDJ|布林|量价|筹码|浪形|顶背离|底背离",
    r"选[骨股]|牛[骨股]|黑马|妖[骨股]",
    r"仓位|加仓|减仓|空仓|满仓|止盈|止损|抄底|逃顶",
    r"盘前|盘后|复盘|收盘|开盘|盘中|操盘|交易日",
]

# 宏观信号：命中则不因市场词直接排除
MACRO_PATTERNS = [
    r"宏观|经济|政策|货币|财政|利率|汇率|通胀|通缩|周期",
    r"地缘|战争|冲突|制裁|关税|产业链|人口|房地产|债务",
    r"美联储|央行|降息|加息|流动性|资产配置|大类资产",
    r"格局|秩序|改革|制度|历史|社会|认知",
]


def normalize_author(author: str) -> str:
    a = (author or "").strip()
    return AUTHOR_ALIAS.get(a, a)


def is_stock_trading(title: str) -> bool:
    t = title or ""
    return any(re.search(p, t) for p in STOCK_TRADE_PATTERNS)


def is_macro(title: str) -> bool:
    t = title or ""
    return any(re.search(p, t) for p in MACRO_PATTERNS)


def classify_item(filename: str, author: str, size: int | None = None) -> tuple[str, str]:
    """返回 (decision, reason)"""
    base = os.path.basename(filename)
    ext = os.path.splitext(base)[1].lower()
    if ext != ".pdf" and not (ext == ".zip" and size is not None and size < 10 * 2**20):
        return "drop", "非目标文件类型"

    a = normalize_author(author)
    title = os.path.splitext(base)[0]

    if any(s in base for s in DROP_SUBSTRINGS):
        return "drop", f"命中排除词: {[s for s in DROP_SUBSTRINGS if s in base]}"
    if a in DROP_AUTHORS_HARD:
        return "drop", "作者完全不看"
    if a in DROP_AUTHORS_DEFAULT_ON:
        return "drop", "默认保留区间内的显式排除"

    if a in KEEP_AUTHORS:
        # 系列A豁免；其他作者若标题偏具体炒股且无宏观信号，则转入人工复核
        if a != "系列A" and is_stock_trading(title) and not is_macro(title):
            return "review", "疑似具体炒股，待确认"
        return "keep", "命中保留作者"

    # 未列入保留名单的作者，先看是否像宏观内容
    if is_macro(title) and not is_stock_trading(title):
        return "review", "未列入作者清单但标题偏宏观，待确认"
    return "drop", "作者不在保留清单"
