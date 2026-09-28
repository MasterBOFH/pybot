"""Pytest fixtures for unit and integration tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

import pytest

from tests.harness.wait import wait_port


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: tests that need a live ircu2 (docker harness)"
    )
    config.addinivalue_line(
        "markers",
        "ircd_matrix: tests that need the multi-ircd docker matrix (docker/ircd-matrix)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_integration = config.getoption("--integration", default=False)
    run_matrix = config.getoption("--ircd-matrix", default=False)
    skip_integration = pytest.mark.skip(reason="need --integration (and running ircu2 harness)")
    skip_matrix = pytest.mark.skip(
        reason="need --ircd-matrix (and running docker/ircd-matrix harness)"
    )
    for item in items:
        if "ircd_matrix" in item.keywords:
            if not run_matrix:
                item.add_marker(skip_matrix)
        elif "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests against live ircu2",
    )
    parser.addoption(
        "--ircd-matrix",
        action="store_true",
        default=False,
        help="Run parser interop tests against the docker/ircd-matrix ircd matrix",
    )


@pytest.fixture(scope="session")
def irc_host() -> str:
    return os.environ.get("PYBOT_IRC_HOST", "127.0.0.1")


@pytest.fixture(scope="session")
def irc_port() -> int:
    return int(os.environ.get("PYBOT_IRC_PORT", "6667"))


@pytest.fixture
async def irc_server(irc_host: str, irc_port: int):
    """Ensure ircu2 is accepting connections."""
    await wait_port(irc_host, irc_port, timeout=90.0)
    return irc_host, irc_port


class MatrixServer(NamedTuple):
    plain: int
    tls: int | None  # None: this ircd version has no native TLS


# compose service name -> ports, matching docker/ircd-matrix/docker-compose.yml
# (also read by scripts/run-ircd-matrix.sh to wait for the ports).
IRCD_MATRIX_SERVERS = {
    "ircd-irc2": MatrixServer(4440, None),
    "unrealircd-6": MatrixServer(4441, 5551),
    "ircd-hybrid-8": MatrixServer(4442, 5552),
    "ircu2": MatrixServer(4443, None),
    "bahamut": MatrixServer(4444, 5554),
    "ngircd": MatrixServer(4445, 5555),
    "ircd-ratbox": MatrixServer(4446, 5556),
    "solanum": MatrixServer(4447, 5557),
    "inspircd-4": MatrixServer(4448, 5558),
    "ergo": MatrixServer(4449, 5559),
}

# Test CA that signs every matrix server's TLS certificate (valid for
# 127.0.0.1 and localhost), copied from MasterBOFH/test-servers certs/.
IRCD_MATRIX_CA = str(Path(__file__).resolve().parent.parent / "docker/ircd-matrix/certs/ca.crt")


@pytest.fixture(scope="session")
def ircd_matrix_host() -> str:
    return os.environ.get("PYBOT_IRCD_MATRIX_HOST", "127.0.0.1")
