#!/usr/bin/env bash
# Start the ircd matrix (Docker) and run parser interop tests against it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker/ircd-matrix/docker-compose.yml)
if ! docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f docker/ircd-matrix/docker-compose.yml)
fi

export PYBOT_IRCD_MATRIX_HOST="${PYBOT_IRCD_MATRIX_HOST:-127.0.0.1}"

echo "==> Starting ircd matrix on ${PYBOT_IRCD_MATRIX_HOST}"
"${COMPOSE[@]}" up -d --pull missing

cleanup() {
  if [[ "${PYBOT_KEEP_IRCD_MATRIX:-}" == "1" ]]; then
    echo "==> Leaving ircd matrix running (PYBOT_KEEP_IRCD_MATRIX=1)"
  else
    echo "==> Stopping ircd matrix"
    "${COMPOSE[@]}" down
  fi
}
trap cleanup EXIT

echo "==> Waiting for ircd matrix ports..."
python3 - <<PY
import socket, time, sys
host = "${PYBOT_IRCD_MATRIX_HOST}"
ports = {"ircd-irc2": 4440, "unreal4": 4441, "hybrid": 4442, "ircu2": 4443,
         "bahamut": 4444, "ngircd": 4445, "charybdis": 4447, "inspircd": 4448}
deadline = time.time() + 180
pending = dict(ports)
while pending and time.time() < deadline:
    for name, port in list(pending.items()):
        try:
            with socket.create_connection((host, port), timeout=1):
                print(f"{name} ready at {host}:{port}")
                del pending[name]
        except OSError:
            pass
    if pending:
        time.sleep(0.5)
if pending:
    print(f"timeout waiting for: {pending}", file=sys.stderr)
    sys.exit(1)
PY

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

echo "==> Running ircd matrix interop tests"
"$PYTHON" -m pytest tests/integration/test_ircd_matrix.py --ircd-matrix -v
