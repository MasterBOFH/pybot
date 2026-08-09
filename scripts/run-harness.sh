#!/usr/bin/env bash
# Start ircu2 (Docker) and run unit + integration tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker/docker-compose.yml)
if ! docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f docker/docker-compose.yml)
fi

export PYBOT_IRC_HOST="${PYBOT_IRC_HOST:-127.0.0.1}"
export PYBOT_IRC_PORT="${PYBOT_IRC_PORT:-6667}"

echo "==> Building/starting ircu2 harness on ${PYBOT_IRC_HOST}:${PYBOT_IRC_PORT}"
"${COMPOSE[@]}" up -d --build

cleanup() {
  if [[ "${PYBOT_KEEP_IRCU:-}" == "1" ]]; then
    echo "==> Leaving ircu2 running (PYBOT_KEEP_IRCU=1)"
  else
    echo "==> Stopping ircu2 harness"
    "${COMPOSE[@]}" down
  fi
}
trap cleanup EXIT

echo "==> Waiting for IRC port..."
python3 - <<'PY'
import os, socket, time, sys
host = os.environ["PYBOT_IRC_HOST"]
port = int(os.environ["PYBOT_IRC_PORT"])
deadline = time.time() + 120
last = None
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=1):
            print(f"ircu2 ready at {host}:{port}")
            sys.exit(0)
    except OSError as e:
        last = e
        time.sleep(0.5)
print(f"timeout waiting for {host}:{port}: {last}", file=sys.stderr)
sys.exit(1)
PY

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

echo "==> Running unit + integration tests"
"$PYTHON" -m pytest tests/ --integration -v
