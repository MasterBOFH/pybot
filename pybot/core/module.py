"""Module base class and loader."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pybot.core.api import BotAPI

log = logging.getLogger("pybot.core.module")


def on(event: str):
    """Decorator to mark a module method as an event handler."""

    def deco(fn):
        fn._pybot_event = event
        return fn

    return deco


class Module:
    name: str = "unnamed"

    def __init__(self) -> None:
        self.api: BotAPI | None = None
        self.config: dict[str, Any] = {}

    async def setup(self, api: BotAPI) -> None:
        self.api = api
        self.config = api.get_config()

    async def teardown(self) -> None:
        pass

    async def reload_config(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_state(self) -> dict[str, Any]:
        """Optional hot-reload state (override in modules that need it)."""
        return {}

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore state from get_state() before/around setup."""
        return


def discover_module_names() -> list[str]:
    import pybot.modules as modules_pkg

    names = []
    for info in pkgutil.iter_modules(modules_pkg.__path__):
        if info.name.startswith("_"):
            continue
        names.append(info.name)
    return names


def load_module_class(name: str) -> type[Module]:
    mod = importlib.import_module(f"pybot.modules.{name}")
    cls = getattr(mod, "Module", None)
    if cls is None:
        # try common pattern: GithubWebhookModule etc.
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, Module)
                and obj is not Module
            ):
                cls = obj
                break
    if cls is None:
        raise ImportError(f"No Module class in pybot.modules.{name}")
    return cls


def collect_handlers(instance: Module) -> dict[str, Any]:
    handlers = {}
    for attr_name in dir(instance):
        if attr_name.startswith("_"):
            continue
        attr = getattr(instance, attr_name)
        event = getattr(attr, "_pybot_event", None)
        if event:
            handlers[event] = attr
    # also allow handlers dict on class
    class_handlers = getattr(instance, "handlers", None)
    if isinstance(class_handlers, dict):
        handlers.update(class_handlers)
    return handlers
