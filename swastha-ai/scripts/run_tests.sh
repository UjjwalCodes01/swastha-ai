#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  python3 "$ROOT/scripts/bootstrap_test_env.py"
fi

if [ "$#" -eq 0 ]; then
  exec "$PYTHON" -m pytest tests/ai_core tests/compliance
fi

exec "$PYTHON" -m pytest "$@"
