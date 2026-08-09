"""Pytest fixtures for unit and integration tests."""

from __future__ import annotations

import os

import pytest

from tests.harness.wait import wait_port


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: tests that need a live ircu2 (docker harness)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--integration", default=False):
        return
    skip = pytest.mark.skip(reason="need --integration (and running ircu2 harness)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests against live ircu2",
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
