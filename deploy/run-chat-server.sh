#!/usr/bin/env bash
set -eu
APP_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$APP_ROOT"
export SKILLMARKET_DB="${SKILLMARKET_DB:-$APP_ROOT/data/skillmarket.duckdb}"
export SKILLMARKET_SOURCE=duckdb
if [ -f "$APP_ROOT/RELEASE" ]; then
  SKILLMARKET_RELEASE="$(cat "$APP_ROOT/RELEASE")"
else
  SKILLMARKET_RELEASE="$(git rev-parse HEAD)"
fi
export SKILLMARKET_RELEASE
exec "${SKILLMARKET_PYTHON:-$APP_ROOT/.venv/bin/python}" -m uvicorn server:app \
  --host 127.0.0.1 --port "${SKILLMARKET_PORT:-8000}"
