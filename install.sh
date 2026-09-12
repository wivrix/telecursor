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
    # Managed install dir: sync to remote main (local edits are discarded)
    git -C "$INSTALL_DIR" checkout -f "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
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
"$PYTHON" -m pip -q install -U pip setuptools wheel
"$PYTHON" -m pip -q install -r requirements.txt
"$PYTHON" -m pip -q install -e .

echo "==> Registering telecursor command"
"$PYTHON" "$ROOT/main.py" install --system

BIN="$ROOT/.venv/bin/telecursor"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
ln -sfn "$BIN" "$LOCAL_BIN/telecursor"
export PATH="$LOCAL_BIN:$ROOT/.venv/bin:$PATH"

# Persist PATH for new shells
for profile in "$HOME/.bashrc" "$HOME/.profile"; do
  if [[ -f "$profile" ]] || [[ "$profile" == "$HOME/.bashrc" ]]; then
    if ! grep -q 'Telecursor CLI' "$profile" 2>/dev/null; then
      printf '\n# Telecursor CLI\nexport PATH="%s:$PATH"\n' "$LOCAL_BIN" >> "$profile"
      echo "==> Added PATH to $profile"
    fi
    break
  fi
done

echo ""
echo "✅ Telecursor installed"
echo "   Folder:  $ROOT"
echo "   Command: $LOCAL_BIN/telecursor -> $BIN"
echo ""
echo "Next (new terminal, or: source ~/.bashrc):"
echo "  telecursor setup"
echo "  telecursor start -d"
echo "  telecursor status"
