"""Hot-reload helpers via importlib."""

from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType

log = logging.getLogger("pybot.core.reload")

# Sticky modules — never importlib.reload these (hold sockets / state)
STICKY_PREFIXES = (
    "pybot.irc.connection",
    "pybot.irc.state",
    "pybot.irc.flood",
    "pybot.core.timers",
    "pybot.core.http",
)


def is_sticky(modname: str) -> bool:
    return any(modname == p or modname.startswith(p + ".") for p in STICKY_PREFIXES)


def reload_package_modules(prefix: str) -> list[str]:
    """Reload all loaded modules under prefix (except sticky). Returns names reloaded."""
    names = sorted(
        (
            name
            for name in list(sys.modules)
            if name == prefix or name.startswith(prefix + ".")
        ),
        key=lambda n: n.count("."),
        reverse=True,
    )
    reloaded = []
    for name in names:
        if is_sticky(name):
            log.debug("Skipping sticky module %s", name)
            continue
        mod = sys.modules.get(name)
        if mod is None or not isinstance(mod, ModuleType):
            continue
        try:
            importlib.reload(mod)
            reloaded.append(name)
            log.info("Reloaded %s", name)
        except Exception:
            log.exception("Failed to reload %s", name)
    return reloaded


def reload_module_by_name(fullname_name: str) -> ModuleType:
    """Reload a bot module package pybot.modules.<name>."""
    full = f"pybot.modules.{fullname_name}"
    # reload submodules first
    reload_package_modules(full)
    if full in sys.modules:
        return importlib.reload(sys.modules[full])
    return importlib.import_module(full)
