#!/usr/bin/env python3
"""文件分类：作者提取、保留判定（管线共用）"""
import json
import os
import re

KB = os.environ.get("KB_ROOT", "/opt/kb")
CONFIG = json.load(open(f"{KB}/config.json"))
KEEP = set(CONFIG["keep_authors"])
EXCLUDE = CONFIG["exclude_substrings"]
PROMO_NAMES = {"推广材料资料.png", "推广材料说明.png", "推广材料必看.png",
               "推广材料必看.zip", "推广材料文档.png", "推广材料资料"}

ALIAS = {
    "系列C-别名01": "系列C", "系列C-别名02": "系列C", "系列C-别名03": "系列C",
    "系列C-别名04": "系列C", "系列C-别名05": "系列C",
    "系列E-别名06": "系列E", "系列E-别名07": "系列E",
    "系列BC-别名08": "系列BC",
    "系列AD-别名09": "系列AD", "系列AD-别名10": "系列AD",
    "系列AD-别名11": "系列AD", "系列AD-别名12": "系列AD",
    "系列D-别名13": "系列D", "系列D-别名14": "系列D",
    "系列AC-别名15": "系列AC", "系列AC-别名16": "系列AC",
    "系列AE-别名17": "系列AE", "系列AE-别名18": "系列AE",
    "系列AE-别名19": "系列AE",
    "系列B-别名20": "系列B", "系列B-别名21": "系列B", "系列B-别名22": "系列B",
    "系列B-别名23": "系列B", "系列B-别名24": "系列B",
    "系列B-别名67": "系列B",
    "系列A-别名25": "系列A", "系列A-别名26": "系列A",
    "系列AF-别名27": "系列AF",
    "系列F-别名28": "系列F",
    "系列N-别名29": "系列N",
    "系列AG-别名68": "系列AG", "系列AG-别名30": "系列AG",
    "系列AH-别名31": "系列AH",
    "系列AI-别名32": "系列AI", "系列AI-别名69": "系列AI",
    "系列AI-别名33": "系列AI", "系列AI-别名34": "系列AI",
    "系列AJ-别名35": "系列AJ", "系列AJ-别名36": "系列AJ", "系列AJ-别名37": "系列AJ",
    "系列AJ-别名38": "系列AJ", "系列AJ-别名39": "系列AJ", "系列AJ-别名40": "系列AJ",
    "系列AJ-别名41": "系列AJ", "系列AJ-别名42": "系列AJ",
    "系列AK-别名70": "系列AK",
    "系列AL-别名43": "系列AL",
    "系列AM-别名44": "系列AM",
    "系列AN-别名71": "系列AN", "系列AN-别名45": "系列AN",
    "系列O-别名46": "系列O", "系列O-别名47": "系列O",
    "系列AO-别名48": "系列AO",
    "系列AP-别名49": "系列AP", "系列AQ-别名50": "系列AQ",
    "系列AR-别名51": "系列AR",
    "系列S-别名52": "系列S",
    "系列AS-别名53": "系列AS",
    "系列AT-别名54": "系列AT", "系列AU-别名55": "系列AU",
    "系列AV-别名56": "系列AV", "系列AV-别名57": "系列AV",
    "系列L-别名58": "系列L",
    "系列G-别名72": "系列G",
    "系列AW-别名59": "系列AW",
    "系列Z-别名73": "系列Z",
    "系列M-别名60": "系列M", "系列M-别名61": "系列M",
    "系列AX-别名62": "系列AX",
    "系列AY-别名63": "系列AY",
    "系列AZ-别名64": "系列AZ", "系列AZ-别名65": "系列AZ",
    "系列BA-别名74": "系列BA",
    "系列BB-别名66": "系列BB",
    "系列T-别名75": "系列T",
}


def extract_author(name):
    n = os.path.splitext(name)[0]
    n = re.sub(r"【[^】]*】", "", n)
    n = re.sub(r"[_\-]?\d{3,}.*$", "", n).strip()
    n = re.sub(r"^(20\d{2}|\d{6}|\d{8})\s*", "", n)
    n = n.strip("（）()")
    m = re.match(r"^([^：:0-9]{2,14}?)(?=\s*(?:20\d{2}|\d{4}|\d{6}|\d{8}|：|:|《|【|$))", n)
    if not m:
        m = re.match(r"^([^\d：:]{2,14})", n)
    a = (m.group(1) if m else "").strip(" -_·、")
    return ALIAS.get(a, a) if a else ""


def extract_date(name, month_dir=None):
    n = os.path.splitext(name)[0]
    m = re.search(r"(?<!\d)(2[5-9])(\d{2})(\d{2})(?!\d)", n)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"2[5-9]年(\d{1,2})月(\d{1,2})日", n)
    if m:
        return f"20--{int(m.group(1)):02d}-{int(m.group(2)):02d}".replace("20--", "20  ")
    m = re.search(r"(?<!\d)(2[5-9])\.(\d{2})\.(\d{2})(?!\d)", n)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if month_dir and month_dir.endswith("月"):
        try:
            return f"2026-{int(month_dir[:-1]):02d}"
        except ValueError:
            pass
    return ""


def is_kept(filename, size=None):
    base = os.path.basename(filename)
    if base in PROMO_NAMES:
        return False
    if any(s in base for s in EXCLUDE):
        return False
    ext = os.path.splitext(base)[1].lower()
    if ext == ".pdf":
        return extract_author(base) in KEEP
    if ext == ".zip" and size is not None and size < 10 * 2**20:
        return extract_author(base) in KEEP
    return False


def clean_title(name):
    n = os.path.splitext(name)[0]
    n = re.sub(r"【[^】]*】", "", n)
    n = re.sub(r"[_\-]?\d{3,}.*$", "", n).strip()
    n = re.sub(r"^(20\d{2}|\d{6}|\d{8})", "", n)
    return n.strip(" ：:")
