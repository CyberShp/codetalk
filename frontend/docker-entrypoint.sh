#!/bin/sh
# Replace compile-time placeholder URLs with runtime values.
# NEXT_PUBLIC_* vars are baked into .js at build time;
# this script patches them at container startup so the same image
# works across environments without rebuilding.

set -e

if [ -n "$NEXT_PUBLIC_API_URL" ] && [ "$NEXT_PUBLIC_API_URL" != "__RUNTIME_API_URL__" ]; then
  echo "[entrypoint] Patching API URL -> $NEXT_PUBLIC_API_URL"
  find /app/.next -type f -name '*.js' -exec \
    sed -i "s|__RUNTIME_API_URL__|$NEXT_PUBLIC_API_URL|g" {} +
fi

if [ -n "$NEXT_PUBLIC_WS_URL" ] && [ "$NEXT_PUBLIC_WS_URL" != "__RUNTIME_WS_URL__" ]; then
  echo "[entrypoint] Patching WS URL -> $NEXT_PUBLIC_WS_URL"
  find /app/.next -type f -name '*.js' -exec \
    sed -i "s|__RUNTIME_WS_URL__|$NEXT_PUBLIC_WS_URL|g" {} +
fi

exec "$@"
