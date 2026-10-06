#!/usr/bin/env bash
# 并行 OCR：N 路分片，每片一个 tmux 会话
# 用法：bash deploy/run_ocr_shards.sh 8
set -u
KB="${KB_ROOT:-/opt/kb}"
T="${TMUX_BIN:-tmux}"
[ -x "$T" ] || T=$(command -v tmux 2>/dev/null || true)
N="${1:-8}"
mkdir -p "$KB/runtime"

for i in $(seq 0 $((N-1))); do
  "$T" kill-session -t "ocr-$i" 2>/dev/null || true
done

# 每个进程单线程：容器 CPU 配额有限，多线程反而因争抢降低吞吐
export OMP_NUM_THREADS=1 OMP_WAIT_POLICY=PASSIVE KMP_BLOCKTIME=0 PYTHONUNBUFFERED=1
for i in $(seq 0 $((N-1))); do
  "$T" new-session -d -s "ocr-$i" \
    "cd '$KB' && python3 sync/batch_run.py ocr --shard $i --nshards $N >> '$KB/runtime/ocr_$i.log' 2>&1" \
    >/dev/null 2>&1 &
  sleep 0.3
done
echo "已启动 $N 路 OCR 分片（KB_ROOT=$KB）"