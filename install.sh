#!/usr/bin/env bash
# Install the `telecursor` command onto your PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" -m pip install -U pip setuptools wheel
exec "$PYTHON" "$ROOT/main.py" install
