#!/usr/bin/env python3
"""PDF 文字层检测与提取：有文字层直接抽文本，无文字层标记为待 OCR"""
import glob
import json
import os
import subprocess

KB = os.environ.get("KB_ROOT", "/opt/kb")
RAW = f"{KB}/raw"
OUT = f"{KB}/state/pdf_text_report.json"


def char_count(text):
    return sum(1 for c in text if not c.isspace())


def main():
    rows = []
    for pdf in sorted(glob.glob(os.path.join(RAW, "*.pdf"))):
        r = subprocess.run(
            ["pdftotext", "-layout", pdf, "-"],
            capture_output=True, text=True, timeout=120,
        )
        text = r.stdout or ""
        n = char_count(text)
        rows.append({
            "file": os.path.basename(pdf),
            "size_mb": round(os.path.getsize(pdf) / 2 ** 20, 2),
            "text_chars": n,
            "mode": "text" if n >= 300 else "ocr",
            "preview": text.strip()[:180],
        })
    json.dump(rows, open(OUT, "w"), ensure_ascii=False, indent=1)
    text_n = sum(1 for x in rows if x["mode"] == "text")
    ocr_n = len(rows) - text_n
    print(f"样本 {len(rows)} 个 | 文字层可直接提取 {text_n} | 需要 OCR {ocr_n}")
    for x in rows:
        print(f"  {x['mode']:<5} {x['text_chars']:>6}字 {x['size_mb']:>5}MB {x['file'][:46]}")
    print(f"\n报告: {OUT}")


if __name__ == "__main__":
    main()
