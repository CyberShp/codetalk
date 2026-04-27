#!/usr/bin/env bash
# =============================================================================
# CodeTalks — 内网镜像导入脚本
# 在内网有 Docker 权限的机器上执行（不是 PC 安全机）
# =============================================================================
set -euo pipefail

HARBOR_HOST="${1:?用法: $0 <HARBOR_HOST> <VERSION>  例: $0 harbor.company.com v1.0.0}"
VERSION="${2:?用法: $0 <HARBOR_HOST> <VERSION>}"
PROJECT="codetalk"
IMPORT_DIR="${IMPORT_DIR:-./image-export}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[IMPORT]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN  ]${NC} $*"; }

REGISTRY="$HARBOR_HOST/$PROJECT"

log "Harbor: $REGISTRY"
log "版本:   $VERSION"
log "导入目录: $IMPORT_DIR"
echo ""

# Harbor 登录（如未登录）
if ! docker info 2>/dev/null | grep -q "$HARBOR_HOST"; then
  warn "请确认已登录 Harbor: docker login $HARBOR_HOST"
fi

for IMAGE in postgres backend deepwiki gitnexus frontend joern zoekt; do
  TAR="$IMPORT_DIR/$IMAGE-$VERSION.tar"
  if [[ ! -f "$TAR" ]]; then
    echo -e "\033[0;31m[SKIP]\033[0m $TAR 不存在，跳过"
    continue
  fi

  log "加载: $TAR"
  docker load -i "$TAR"

  SRC="codetalk/$IMAGE:$VERSION"
  DST="$REGISTRY/$IMAGE:$VERSION"
  log "标记: $SRC -> $DST"
  docker tag "$SRC" "$DST"

  log "推送: $DST"
  docker push "$DST"
  echo ""
done

echo "============================================"
log "镜像已全部推送到 Harbor"
log "下一步: 修改 k8s/*.yaml 中的 HARBOR_HOST 占位符，然后 kubectl apply"
echo "============================================"
