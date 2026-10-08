#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

for arg in "$@"; do
  case "$arg" in
    --with-pdf)
      # Backward-compatible no-op: PDF support is installed by default.
      ;;
    *)
      echo "unknown option: $arg" >&2
      echo "usage: scripts/bootstrap_python_env.sh [--with-pdf]" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

if .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install beautifulsoup4 liteparse lxml mlx-vlm pillow pymupdf requests 'pyjwt[crypto]'
elif command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python beautifulsoup4 liteparse lxml mlx-vlm pillow pymupdf requests 'pyjwt[crypto]'
else
  echo "pip is unavailable in .venv and uv was not found" >&2
  exit 1
fi

.venv/bin/python scripts/kpaper.py doctor --json
