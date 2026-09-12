#!/usr/bin/env bash
# One-line install (Linux / macOS):
#   curl -fsSL https://raw.githubusercontent.com/wivrix/telecursor/main/install.sh | bash
#
# Or from a clone:
#   bash install.sh
set -euo pipefail

REPO_URL="${TELECURSOR_REPO:-https://github.com/wivrix/telecursor.git}"
INSTALL_DIR="${TELECURSOR_DIR:-$HOME/telecursor}"
BRANCH="${TELECURSOR_BRANCH:-main}"

resolve_root() {
  local script_path="${BASH_SOURCE[0]:-}"
  if [[ -n "$script_path" && -f "$script_path" ]]; then
    local candidate
    candidate="$(cd "$(dirname "$script_path")" && pwd)"
    if [[ -f "$candidate/pyproject.toml" && -f "$candidate/main.py" ]]; then
      echo "$candidate"
      return 0
    fi
  fi
  return 1
}

echo "==> Telecursor installer"

ROOT="$(resolve_root || true)"
if [[ -z "${ROOT}" ]]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required. Install git, then re-run." >&2
    exit 1
  fi
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "==> Updating existing clone at $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
  else
    echo "==> Cloning $REPO_URL → $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  ROOT="$INSTALL_DIR"
fi

cd "$ROOT"
echo "==> Project: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (3.10+)." >&2
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "==> Creating virtualenv (.venv)"
  python3 -m venv .venv
fi
PYTHON="$ROOT/.venv/bin/python"

echo "==> Installing dependencies"
"$PYTHON" -m pip install -U pip setuptools wheel
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip install -e .

echo "==> Registering telecursor command"
"$PYTHON" "$ROOT/main.py" install --system || "$PYTHON" "$ROOT/main.py" install || true

# Prefer venv binary on PATH for this shell session
export PATH="$ROOT/.venv/bin:$PATH"

echo ""
echo "✅ Telecursor installed"
echo "   Folder: $ROOT"
if command -v telecursor >/dev/null 2>&1; then
  echo "   Command: $(command -v telecursor)"
else
  echo "   Command: $ROOT/.venv/bin/telecursor"
  echo "   Tip: add this to your shell profile:"
  echo "     export PATH=\"$ROOT/.venv/bin:\$PATH\""
fi
echo ""
echo "Next:"
echo "  telecursor setup"
echo "  telecursor start -d"
echo "  telecursor status"
