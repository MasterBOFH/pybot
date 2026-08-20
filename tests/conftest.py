"""Pytest fixtures for unit and integration tests."""

from __future__ import annotations

import os

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


# name -> port, matching docker/ircd-matrix/docker-compose.yml
IRCD_MATRIX_SERVERS = {
    "ircd-irc2": 4440,
    "unreal4": 4441,
    "hybrid": 4442,
    "ircu2": 4443,
    "bahamut": 4444,
    "ngircd": 4445,
    "charybdis": 4447,
    "inspircd": 4448,
}


@pytest.fixture(scope="session")
def ircd_matrix_host() -> str:
    return os.environ.get("PYBOT_IRCD_MATRIX_HOST", "127.0.0.1")
