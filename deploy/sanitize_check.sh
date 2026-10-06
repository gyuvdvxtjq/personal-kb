#!/usr/bin/env bash
# 提交前脱敏检查：扫描将要入库的文件，发现密钥/正文/私有路径即失败
# 用法：bash deploy/sanitize_check.sh [目录，默认当前目录]
set -u
ROOT="${1:-$(pwd)}"
cd "$ROOT" || exit 1
FAIL=0

say() { printf '%s\n' "$*"; }
hit() { FAIL=1; say "  ✗ $1"; }

say "== 1. 密钥类文件是否被排除 =="
for f in state/secrets.env state/app_api_key.txt deploy/app.env deploy/plugin-daemon.env; do
  [ -e "$f" ] && say "  ! 存在 $f（务必确认已被 .gitignore 排除）"
done
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  tracked=$(git ls-files 2>/dev/null | grep -E '(^|/)(app|plugin-daemon)\.env$|secrets\.env|api_key' || true)
  [ -n "$tracked" ] && hit "以下密钥文件已被 git 跟踪: $tracked"
else
  say "  （非 git 仓库，跳过跟踪检查）"
fi

say "== 2. 扫描密钥特征 =="
PAT='sk-[A-Za-z0-9]{20,}|MODELSCOPE_ACCESS_TOKEN=[A-Za-z0-9]{8,}|MINERU_API_KEY=[A-Za-z0-9]{8,}|DIFY_PASSWORD=[A-Za-z0-9#!@%^&*.-]{6,}|SERVER_KEY=[A-Za-z0-9+/]{20,}|DIFY_INNER_API_KEY=[A-Za-z0-9+/]{20,}|password\s*=\s*["'"'"'][^"'"'"']{6,}["'"'"']|PASSWORD=[A-Za-z0-9]{6,}'
# 只扫将要入库的文本文件
FILES=$(find . -type f \( -name "*.py" -o -name "*.sh" -o -name "*.md" -o -name "*.example" -o -name "*.txt" -o -name "*.yaml" -o -name "*.yml" \) \
        -not -path "./.git/*" -not -path "*/runtime/*" -not -path "*/dify/*" -not -path "*/node_modules/*" -not -name "sanitize_check.sh" 2>/dev/null)
MATCH=$(echo "$FILES" | xargs grep -InE "$PAT" 2>/dev/null | grep -vE '\.example:|CHANGE_ME|__|=\.\.\.|DIFY_PASSWORD=\.\.\.' || true)
if [ -n "$MATCH" ]; then
  echo "$MATCH" | head -20 | while read -r l; do say "  ✗ $l"; done
  FAIL=1
else
  say "  ✓ 未发现硬编码密钥"
fi

say "== 3. 是否混入正文/原始资料 =="
BAD=$(find . -type f \( -name "*.pdf" -o -name "*.zip" \) -not -path "./.git/*" 2>/dev/null | head -5)
[ -n "$BAD" ] && hit "存在 PDF/ZIP: $BAD"
BIGMD=$(find . -type f -name "*.md" -size +200k -not -path "./.git/*" 2>/dev/null | head -3)
[ -n "$BIGMD" ] && hit "存在超大 md（可能是正文）: $BIGMD"

say "== 4. 是否混入私有绝对路径 =="
P=$(echo "$FILES" | xargs grep -In "/mnt/workspace/kb\|/mnt/user\|/etc/dsw" 2>/dev/null | grep -v sanitize_check || true)
[ -n "$P" ] && { echo "$P" | while read -r l; do say "  ✗ $l"; done; FAIL=1; } || say "  ✓ 无私有绝对路径"

say "== 4.5 是否混入真实来源/供应商指纹 =="
FPFILE=deploy/.fingerprints.local
if [ -f "$FPFILE" ]; then
  FPRINT=$(paste -sd'|' "$FPFILE")
  FP=$(echo "$FILES" | xargs grep -InE "$FPRINT" 2>/dev/null | grep -v sanitize_check | grep -vE "示例|代号" || true)
  [ -n "$FP" ] && { echo "$FP" | while read -r l; do say "  ✗ $l"; done; FAIL=1; } || say "  ✓ 无来源指纹"
else
  say "  ! 跳过（无 $FPFILE：把你的真实供应商域名/IP/系列名一行一个写入该文件，已 gitignore）"
fi

say "== 5. 目录体积（排除应被忽略的大目录）==="
CNT=$(find . -type f -not -path "./.git/*" -not -path "./runtime/*" -not -path "./dify/*" 2>/dev/null | wc -l)
say "  待入库文件数: $CNT"

if [ "$FAIL" = 0 ]; then say "✅ 脱敏检查通过"; else say "❌ 发现问题，请修正后再提交"; fi
exit $FAIL