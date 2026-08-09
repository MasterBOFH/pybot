"""Async wait helpers for integration tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable


async def wait_port(host: str, port: int, timeout: float = 60.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last_err: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_err = exc
            await asyncio.sleep(0.25)
    raise TimeoutError(f"Port {host}:{port} not open within {timeout}s: {last_err}")


async def wait_until(
    predicate: Callable[[], bool] | Callable[[], Awaitable[bool]],
    timeout: float = 10.0,
    interval: float = 0.05,
    desc: str = "condition",
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Timed out waiting for {desc}")
