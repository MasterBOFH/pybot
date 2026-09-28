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

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

echo "==> Waiting for ircd matrix ports..."
"$PYTHON" - <<'PY'
import os, socket, sys, time
from tests.conftest import IRCD_MATRIX_SERVERS
host = os.environ["PYBOT_IRCD_MATRIX_HOST"]
pending = {
    (name, port)
    for name, ports in IRCD_MATRIX_SERVERS.items()
    for port in ports
    if port is not None
}
deadline = time.time() + 120
while pending and time.time() < deadline:
    for name, port in sorted(pending):
        try:
            with socket.create_connection((host, port), timeout=1):
                print(f"{name} ready at {host}:{port}")
                pending.discard((name, port))
        except OSError:
            pass
    if pending:
        time.sleep(0.5)
if pending:
    print(f"timeout waiting for: {sorted(pending)}", file=sys.stderr)
    sys.exit(1)
PY

echo "==> Running ircd matrix interop tests"
"$PYTHON" -m pytest tests/integration/test_ircd_matrix.py --ircd-matrix -v
