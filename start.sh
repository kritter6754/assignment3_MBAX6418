#!/usr/bin/env bash
# AI Jukebox launcher for macOS and Linux. Run it from anywhere:   bash start.sh
# It creates .env on the first run (asking for the class key), installs what is needed,
# starts the app and opens http://localhost:8000. Stop the app with Ctrl+C.
set -e
cd "$(dirname "$0")"
echo "== AI Jukebox launcher ($(pwd))"

# 1. Settings: .env holds the key (never uploaded to GitHub).
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi
if grep -qE '^CLASS_API_KEY=(replace-me)?[[:space:]]*$' .env; then
  printf "Paste the class API key (it will not be shown), then press Enter: "
  read -rs KEY
  echo
  if [ -z "$KEY" ]; then
    echo "No key entered. Open .env, set CLASS_API_KEY=..., then run this again."
    exit 1
  fi
  tmp="$(mktemp)"
  grep -v '^CLASS_API_KEY=' .env > "$tmp" || true
  { printf 'CLASS_API_KEY=%s\n' "$KEY"; cat "$tmp"; } > .env
  rm -f "$tmp"
  echo "Saved the key in .env."
fi
if [ ! -f class-endpoints.txt ] && grep -q 'example.com' .env; then
  echo "NOTE: there is no class-endpoints.txt here and the URLs in .env are still placeholders."
  echo "      Copy class-endpoints.txt into $(pwd), or fill in the four *_BASE_URL lines in .env."
fi

# 2. Install and start.
export OPEN_BROWSER="${OPEN_BROWSER:-1}"
if command -v uv >/dev/null 2>&1; then
  echo "Installing dependencies with uv (first run takes a minute)..."
  exec uv run --no-dev ai-jukebox
fi
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
  echo "Python 3.12 or newer is needed (or install uv: https://docs.astral.sh/uv/). Found: ${PY:-none}"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Creating a virtual environment in .venv ..."
  "$PY" -m venv .venv
fi
echo "Installing dependencies with pip (first run takes a minute)..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec .venv/bin/python -m jukebox
