#!/usr/bin/env bash
# =============================================================================
# CodeTalks — 外网镜像构建脚本
# 在有公网访问的机器上执行，输出 7 个 .tar 文件供内网导入
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/image-export}"
VERSION="${VERSION:-v1.0.0}"

# 颜色
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[BUILD]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN ]${NC} $*"; }
fail() { echo -e "${RED}[FAIL ]${NC} $*"; exit 1; }

mkdir -p "$OUTPUT_DIR"
log "输出目录: $OUTPUT_DIR"
log "版本标签: $VERSION"
echo ""

# ─────────────────────────────────────────────
# 1. postgres:16  (直接拉官方镜像)
# ─────────────────────────────────────────────
log "Step 1/7 — 拉取 postgres:16"
docker pull postgres:16
docker tag postgres:16 "codetalk/postgres:$VERSION"
docker save "codetalk/postgres:$VERSION" -o "$OUTPUT_DIR/postgres-$VERSION.tar"
log "  -> 已保存: postgres-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/postgres-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 2. backend  (基于 python:3.12-slim，pip install)
# ─────────────────────────────────────────────
log "Step 2/7 — 构建 backend"
docker build \
  --platform linux/amd64 \
  --no-cache \
  -t "codetalk/backend:$VERSION" \
  "$PROJECT_ROOT/backend"
docker save "codetalk/backend:$VERSION" -o "$OUTPUT_DIR/backend-$VERSION.tar"
log "  -> 已保存: backend-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/backend-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 3. deepwiki  (从 ghcr.io 拉取)
# ─────────────────────────────────────────────
log "Step 3/7 — 拉取 deepwiki-open"
docker pull --platform linux/amd64 ghcr.io/asyncfuncai/deepwiki-open:latest
docker tag ghcr.io/asyncfuncai/deepwiki-open:latest "codetalk/deepwiki:$VERSION"
docker save "codetalk/deepwiki:$VERSION" -o "$OUTPUT_DIR/deepwiki-$VERSION.tar"
log "  -> 已保存: deepwiki-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/deepwiki-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 4. gitnexus  (node:20-slim + npm install gitnexus + Trixie libstdc++)
#    注意: 此步骤需要能访问 deb.debian.org 和 registry.npmjs.org
# ─────────────────────────────────────────────
log "Step 4/7 — 构建 gitnexus (需要 npm 和 Debian trixie 源)"
docker build \
  --platform linux/amd64 \
  --no-cache \
  -t "codetalk/gitnexus:$VERSION" \
  "$PROJECT_ROOT/docker/gitnexus"
docker save "codetalk/gitnexus:$VERSION" -o "$OUTPUT_DIR/gitnexus-$VERSION.tar"
log "  -> 已保存: gitnexus-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/gitnexus-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 5. frontend  (Next.js standalone)
# ─────────────────────────────────────────────
log "Step 5/7 — 构建 frontend (Next.js standalone)"
docker build \
  --platform linux/amd64 \
  --no-cache \
  -t "codetalk/frontend:$VERSION" \
  "$PROJECT_ROOT/frontend"
docker save "codetalk/frontend:$VERSION" -o "$OUTPUT_DIR/frontend-$VERSION.tar"
log "  -> 已保存: frontend-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/frontend-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 6. joern  (从 ghcr.io 拉取，JVM 重镜像约 1.5GB)
# ─────────────────────────────────────────────
log "Step 6/7 — 拉取 joern (CPG 分析引擎)"
docker pull --platform linux/amd64 ghcr.io/joernio/joern:nightly
docker tag ghcr.io/joernio/joern:nightly "codetalk/joern:$VERSION"
docker save "codetalk/joern:$VERSION" -o "$OUTPUT_DIR/joern-$VERSION.tar"
log "  -> 已保存: joern-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/joern-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 7. zoekt  (从 ghcr.io 拉取，代码搜索引擎)
# ─────────────────────────────────────────────
log "Step 7/7 — 拉取 zoekt (代码搜索引擎)"
docker pull --platform linux/amd64 ghcr.io/sourcegraph/zoekt:latest
docker tag ghcr.io/sourcegraph/zoekt:latest "codetalk/zoekt:$VERSION"
docker save "codetalk/zoekt:$VERSION" -o "$OUTPUT_DIR/zoekt-$VERSION.tar"
log "  -> 已保存: zoekt-$VERSION.tar  ($(du -sh "$OUTPUT_DIR/zoekt-$VERSION.tar" | cut -f1))"
echo ""

# ─────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────
echo "============================================"
log "全部完成！输出文件列表："
ls -lh "$OUTPUT_DIR/"*.tar
echo ""
warn "下一步: 通过合规通道将 $OUTPUT_DIR/ 目录传输到内网"
warn "内网操作: 执行 scripts/import-images.sh <HARBOR_HOST> <VERSION>"
echo "============================================"
