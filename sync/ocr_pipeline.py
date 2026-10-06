#!/usr/bin/env python3
"""长图 PDF 处理：抽图 -> OCR -> 去水印 -> 输出 Markdown

这批资料的实际形态是每页一张超长截图，文字层只有推广水印，
所以正文必须走 OCR，不能直接信任 pdftotext。
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

KB = os.environ.get("KB_ROOT", "/opt/kb")
RAW = f"{KB}/raw"
DOCS = f"{KB}/docs"
STATE = f"{KB}/state"

# 超长截图需要切片后再 OCR，整图直接识别会大量丢字
SLICE_HEIGHT = 2200
SLICE_OVERLAP = 120

# 推广/水印噪声
NOISE_PATTERNS = [
    r"免费分享\s*https?://cunlove\.cn",
    r"cunlove\.cn",
    r"CunWorkNotess?",
    r"推广材料圈\s*VIP\s*群",
    r"推广材料圈",
    r"进群加微信",
    r"微信\s*[pP][eE][pP]854",
    r"微信\s*[a-zA-Z0-9]{6,}",
    r"公众号：?推广材料圈",
    r"原价\s*\d+\s*元\s*/?\s*年",
    r"^\s*微信pep854\s*$",
]


def clean_line(line):
    s = line.strip()
    if not s:
        return ""
    for p in NOISE_PATTERNS:
        if re.search(p, s, flags=re.I):
            return ""
    # 单字符碎片与纯符号噪声
    if len(re.sub(r"\W+", "", s)) <= 1:
        return ""
    return s


def extract_images(pdf, outdir):
    # -j(JPEG) 比 -png 快两个数量级（实测 49.3s -> 0.1s），PNG 无损对这批长截图收益可忽略
    try:
        subprocess.run(["pdfimages", "-j", pdf, os.path.join(outdir, "img")],
                       check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError:
        subprocess.run(["pdfimages", "-png", pdf, os.path.join(outdir, "img")],
                       check=True, capture_output=True, timeout=600)
    return sorted(glob.glob(os.path.join(outdir, "img-*.jpg")) +
                  glob.glob(os.path.join(outdir, "img-*.ppm")) +
                  glob.glob(os.path.join(outdir, "img-*.png")))


def ocr_image(engine, path):
    result, _ = engine(path)
    if not result:
        return ""
    return "\n".join(x[1] for x in result)


def slice_image(path, outdir):
    """把超长图切成带重叠的多片，返回切片路径列表"""
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        if h <= SLICE_HEIGHT:
            return [path]
        pieces = []
        y = 0
        idx = 0
        while y < h:
            box = (0, y, w, min(h, y + SLICE_HEIGHT))
            piece = os.path.join(outdir, f"slice-{idx:03d}.png")
            im.crop(box).save(piece)
            pieces.append(piece)
            idx += 1
            y += SLICE_HEIGHT - SLICE_OVERLAP
    return pieces


def main():
    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()

    report = []
    for pdf in sorted(glob.glob(os.path.join(RAW, "*.pdf"))):
        name = os.path.basename(pdf)
        with tempfile.TemporaryDirectory() as td:
            try:
                imgs = extract_images(pdf, td)
            except Exception as e:
                report.append({"file": name, "status": "pdfimages_failed", "error": str(e)})
                continue
            # 正文是长图，取面积最大的若干张，忽略二维码/推广小图
            scored = []
            for p in imgs:
                from PIL import Image
                with Image.open(p) as im:
                    w, h = im.size
                if w * h < 200 * 200:
                    continue
                scored.append((w * h, p))
            scored.sort(reverse=True)
            texts = []
            # 多页横向截图与单张长图都存在，正文图全部处理，只过滤二维码小图
            for _, p in scored:
                with tempfile.TemporaryDirectory() as sd:
                    for piece in slice_image(p, sd):
                        texts.append(ocr_image(engine, piece))
            # 去掉切片重叠带来的重复行，保留首次出现顺序
            seen = set()
            dedup = []
            for x in "\n".join(texts).splitlines():
                k = x.strip()
                if k and k not in seen:
                    seen.add(k)
                    dedup.append(k)
            body = "\n".join(dedup)
        lines = [clean_line(x) for x in body.splitlines()]
        lines = [x for x in lines if x]
        out_md = os.path.join(DOCS, os.path.splitext(name)[0] + ".md")
        with open(out_md, "w") as fh:
            fh.write(f"# {os.path.splitext(name)[0]}\n\n")
            fh.write("\n\n".join(lines))
        report.append({
            "file": name,
            "status": "ok",
            "images": len(scored),
            "lines": len(lines),
            "chars": sum(len(x) for x in lines),
            "markdown": os.path.basename(out_md),
        })
        print(f"  {name[:44]:<46} 图{len(scored):>2} 行{len(lines):>4} 字{sum(len(x) for x in lines):>6}")

    json.dump(report, open(os.path.join(STATE, "ocr_report.json"), "w"), ensure_ascii=False, indent=1)
    ok = [r for r in report if r["status"] == "ok"]
    print(f"\nOCR 完成: 成功 {len(ok)} / 失败 {len(report) - len(ok)}")
    if ok:
        avg = sum(r["chars"] for r in ok) / len(ok)
        print(f"平均正文字数: {avg:.0f}")
    print(f"Markdown 输出目录: {DOCS}")


if __name__ == "__main__":
    main()
