#!/usr/bin/env bash
# Dify 个人知识库 —— 全栈启动（幂等，可反复执行）
#
# 用法：
#   export KB_ROOT=/opt/kb            # 代码根目录（默认 /opt/kb）
#   source deploy/app.env              # 端口/密码/路径等配置
#   bash deploy/start_stack.sh
#
# 设计要点：
#  - PostgreSQL 数据目录放在 $KB_ROOT/runtime/pgdata（持久盘），不放 /var/lib/postgresql
#  - 缺依赖自动安装（postgresql / redis-server / poppler-utils / rapidocr）
#  - 插件 daemon、PG 角色与数据库均幂等重建，容器/机器重启后可直接重跑
set -u

KB_ROOT="${KB_ROOT:-/opt/kb}"
RT="$KB_ROOT/runtime"
DIFY="$KB_ROOT/dify"
PG_BIN="${PG_BIN:-/usr/lib/postgresql/14/bin}"
PG_USER="${PG_USER:-dify}"
PG_PASSWORD="${PG_PASSWORD:-}"
PGDATA="${PGDATA:-$RT/pgdata}"
TMUX="${TMUX_BIN:-tmux}"
CHROMA_PATH="${CHROMA_PATH:-$RT/chroma-data}"
REDIS_DIR="${REDIS_DIR:-$DIFY/docker/volumes/redis/data}"
EMBED_PY="${EMBED_PY:-$RT/chroma/bin/python}"
WEB_PNPM="${WEB_PNPM:-pnpm}"
OPENLIST_DIR="${OPENLIST_DIR:-$KB_ROOT/tools/openlist}"

[ -f "$(dirname "$0")/app.env" ] && source "$(dirname "$0")/app.env"

log() { echo "[start_stack] $*"; }
die() { echo "[start_stack] 错误: $*" >&2; exit 1; }

# 受限容器可能把 tmux 放在非标路径：用 TMUX_BIN 指定（见 app.env.example）
[ -x "$TMUX" ] || command -v tmux >/dev/null 2>&1 || die "找不到 tmux，请先安装"
[ -n "$PG_PASSWORD" ] || die "请在 deploy/app.env 里设置 PG_PASSWORD"

# ---------- 清理旧会话 ----------
for s in dify-api dify-worker dify-web dify-plugin kb-embed kb-chroma openlist; do
  "$TMUX" kill-session -t "$s" 2>/dev/null || true
done
sleep 2

# ---------- 依赖自愈 ----------
NEED_APT=""
command -v redis-server >/dev/null 2>&1 || NEED_APT="$NEED_APT redis-server"
[ -x "$PG_BIN/pg_ctl" ] || NEED_APT="$NEED_APT postgresql"
command -v pdfimages >/dev/null 2>&1 || NEED_APT="$NEED_APT poppler-utils"
if [ -n "$NEED_APT" ]; then
  log "安装缺失依赖:$NEED_APT"
  apt-get update -qq >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $NEED_APT >/dev/null 2>&1 || \
    log "WARN: 依赖安装失败，请手动安装:$NEED_APT"
fi
python3 -c "import rapidocr_onnxruntime" >/dev/null 2>&1 || {
  log "安装 rapidocr_onnxruntime（OCR 用）"
  python3 -m pip install --quiet rapidocr_onnxruntime >/dev/null 2>&1 || log "WARN: rapidocr 安装失败"
}

# ---------- PostgreSQL（数据目录必须在持久盘） ----------
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  log "初始化 PG 数据目录 $PGDATA"
  mkdir -p "$PGDATA"; chown postgres:postgres "$PGDATA"
  su -s /bin/bash postgres -c "$PG_BIN/initdb -D $PGDATA -U postgres --auth-local=trust --auth-host=md5" >/dev/null
  grep -q "^listen_addresses" "$PGDATA/postgresql.conf" || cat >> "$PGDATA/postgresql.conf" <<EOF
listen_addresses = '127.0.0.1'
port = 5432
max_connections = 200
EOF
  grep -q "^host $PG_USER " "$PGDATA/pg_hba.conf" || cat >> "$PGDATA/pg_hba.conf" <<EOF
host $PG_USER $PG_USER 127.0.0.1/32 md5
EOF
fi
# pg_ctl 以 postgres 用户运行，日志文件必须先交给 postgres，否则 Permission denied
touch "$RT/pg.log" 2>/dev/null || true
chown postgres:postgres "$RT/pg.log" 2>/dev/null || true
su -s /bin/bash postgres -c "$PG_BIN/pg_ctl -D $PGDATA -l $RT/pg.log start" >/dev/null 2>&1 || \
  su -s /bin/bash postgres -c "$PG_BIN/pg_ctl -D $PGDATA -l $RT/pg.log restart" >/dev/null 2>&1 || true
sleep 2

# ---------- 角色与数据库（幂等） ----------
# 注意：整条 SQL 必须作为 "$1" 单个参数传入；写成 $* 会被词分割拆坏
PGSQL() { su -s /bin/bash postgres -c "$PG_BIN/psql -h /var/run/postgresql -U postgres -d postgres -c \"$1\""; }
PGSQL "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" | grep -q 1 || \
  PGSQL "CREATE USER $PG_USER WITH PASSWORD '$PG_PASSWORD';" >/dev/null
PGSQL "ALTER USER $PG_USER WITH PASSWORD '$PG_PASSWORD';" >/dev/null 2>&1 || true
for db in dify dify_plugin; do
  PGSQL "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || \
    PGSQL "CREATE DATABASE $db OWNER $PG_USER;" >/dev/null
done

# ---------- Redis ----------
command -v redis-server >/dev/null 2>&1 || die "缺少 redis-server"
pgrep -x redis-server >/dev/null || {
  mkdir -p "$REDIS_DIR"
  redis-server --daemonize yes --bind 127.0.0.1 --port 6379 \
    --dir "$REDIS_DIR" --logfile "$RT/redis.log"
}

# ---------- OpenList（网盘挂载，可选） ----------
if [ -x "$OPENLIST_DIR/openlist" ]; then
  "$TMUX" new-session -d -s openlist \
    "cd $OPENLIST_DIR && exec ./openlist server --data $OPENLIST_DIR/data --config $OPENLIST_DIR/data/config.json"
fi

# ---------- Dify 数据库 schema ----------
cd "$DIFY/api" || die "找不到 $DIFY/api"
.venv/bin/python -c 'import pyarrow' >/dev/null 2>&1 || \
  UV_CACHE_DIR="$RT/uv-cache" uv pip install --python .venv/bin/python pyarrow \
    -i https://mirrors.aliyun.com/pypi/simple/ >/dev/null 2>&1 || true
.venv/bin/flask --app app.py db upgrade >/dev/null 2>&1 || log "flask db upgrade 跳过（可能已迁移）"
[ -f "$RT/patch_chroma_client.py" ] && .venv/bin/python "$RT/patch_chroma_client.py"

# ---------- plugin daemon ----------
ENV_FILE="$RT/plugin-daemon.env"
[ -f "$ENV_FILE" ] || die "缺少 $ENV_FILE（可从 deploy/plugin-daemon.env.example 复制并填密钥）"
cd "$RT/plugin-daemon-root/app" || die "缺少 plugin-daemon-root/app"
./commandline migrate >/dev/null 2>&1 || true
"$TMUX" new-session -d -s dify-plugin \
  "cd $RT/plugin-daemon-root/app && exec env \$(grep -v '^#' $ENV_FILE | xargs) ./main"

# ---------- Dify API / Worker / Web ----------
"$TMUX" new-session -d -s dify-api "cd $DIFY/api && exec .venv/bin/python -m app"
"$TMUX" new-session -d -s dify-worker \
  "cd $DIFY/api && exec .venv/bin/celery -A celery_entrypoint.celery worker -P gevent -c 2 \
   -Q api_token,dataset,dataset_summary,priority_dataset,priority_pipeline,pipeline,mail,ops_trace,app_deletion,app_rbac,plugin,workflow_storage,conversation,workflow,schedule_poller,schedule_executor,triggered_workflow_dispatcher,trigger_refresh_publisher,trigger_refresh_executor,retention,workflow_based_app_execution \
   --loglevel INFO --prefetch-multiplier=1"
"$TMUX" new-session -d -s dify-web "$WEB_PNPM --dir $DIFY/web dev --hostname 0.0.0.0"

# ---------- Embedding (bge-m3) 与 Chroma ----------
[ -x "$EMBED_PY" ] || die "缺少 $EMBED_PY（Embedding 虚拟环境）"
"$TMUX" new-session -d -s kb-embed "cd $KB_ROOT && exec $EMBED_PY sync/local_embed_server.py"
command -v chroma >/dev/null 2>&1 && CHROMA_BIN=chroma || CHROMA_BIN="$RT/chroma/bin/chroma"
"$TMUX" new-session -d -s kb-chroma "cd $KB_ROOT && exec $CHROMA_BIN run --path $CHROMA_PATH --host 127.0.0.1 --port 8000"

# ---------- 就绪等待（Embedding 冷启动含模型加载+首帧，实测约 80s） ----------
log "等待服务就绪..."
for _ in $(seq 1 150); do
  ok=1
  curl -fsS -m 2 http://127.0.0.1:5001/health >/dev/null 2>&1 || ok=0
  curl -fsS -m 2 http://127.0.0.1:8100/health >/dev/null 2>&1 || ok=0
  curl -fsS -m 2 http://127.0.0.1:8000/api/v2/heartbeat >/dev/null 2>&1 || ok=0
  ss -lnt 2>/dev/null | grep -q ':5002 ' || ok=0
  ss -lnt 2>/dev/null | grep -q ':3000 ' || ok=0
  if [ "$ok" = 1 ]; then
    log "ALL SERVICES READY"
    "$TMUX" ls
    exit 0
  fi
  sleep 2
done
log "部分服务未就绪:"
"$TMUX" ls 2>&1
exit 1