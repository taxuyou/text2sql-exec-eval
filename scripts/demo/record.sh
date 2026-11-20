#!/usr/bin/env bash
# Re-record docs/demo.gif from the real web app (python -m t2s_eval.web).
#
#   NODE_MODULES=/path/to/node_modules scripts/demo/record.sh
#
# NODE_MODULES   a node_modules directory containing playwright 1.65.0 (required)
# PRETENDARD_DIR directory with Pretendard *.ttf files (optional, mounted as a system font)
# PY             Python with t2s_eval installed (default: .venv/bin/python)
# GIF_PY         Python with Pillow (default: $PY)
# FRAMES         keep the PNG frames in this directory (default: a temporary directory)
set -euo pipefail
cd "$(dirname "$0")/../.."

PY=${PY:-.venv/bin/python}
GIF_PY=${GIF_PY:-$PY}
NODE_MODULES=${NODE_MODULES:?set NODE_MODULES to a node_modules with playwright 1.65.0}
PORT=${PORT:-8765}
IMAGE=mcr.microsoft.com/playwright:v1.65.0-noble

if [ -n "${FRAMES:-}" ]; then
  rm -rf "$FRAMES" && mkdir -p "$FRAMES"
else
  FRAMES=$(mktemp -d)
  CLEAN_FRAMES=1
fi
FRAMES=$(cd "$FRAMES" && pwd)

[ -f demo/shop.sqlite ] || "$PY" demo/build_shop_db.py
"$PY" -m t2s_eval.web --db demo/shop.sqlite --pairs demo/pairs.jsonl --port "$PORT" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; [ -n "${CLEAN_FRAMES:-}" ] && rm -rf "$FRAMES"' EXIT
for _ in $(seq 50); do
  "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/pairs')" \
    2>/dev/null && break
  sleep 0.1
done

mounts=(-v "$NODE_MODULES:/opt/node_modules:ro" -v "$PWD/scripts/demo:/scripts:ro" -v "$FRAMES:/frames")
if [ -n "${PRETENDARD_DIR:-}" ]; then
  mounts+=(-v "$PRETENDARD_DIR:/usr/share/fonts/truetype/pretendard:ro")
fi
if [ -d /usr/share/fonts/truetype/dejavu ]; then
  mounts+=(-v /usr/share/fonts/truetype/dejavu:/usr/share/fonts/truetype/dejavu:ro)
fi

docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -e NODE_PATH=/opt/node_modules "${mounts[@]}" \
  "$IMAGE" node /scripts/record.cjs "http://127.0.0.1:$PORT" /frames

"$GIF_PY" scripts/demo/make_gif.py "$FRAMES" docs/demo.gif
