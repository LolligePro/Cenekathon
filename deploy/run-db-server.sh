#!/usr/bin/env bash
set -eu

DB_SERVICE_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$DB_SERVICE_ROOT"
exec .venv/bin/python skillmarket_staging/server.py \
  --database "${SKILLMARKET_DB:-$DB_SERVICE_ROOT/skillmarket.duckdb}" \
  --sources "${SKILLMARKET_SOURCE_GLOB:-/home/team7/Cenekathon/data/vacancies_*.parquet}"
