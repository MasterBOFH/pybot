"""Channel and membership mode parsing using ISUPPORT CHANMODES/PREFIX."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pybot.irc.isupport import ISupport


@dataclass
class ModeChange:
    add: bool
    mode: str
    param: str | None = None
    category: str | None = None  # A/B/C/D/prefix


def parse_mode_string(
    isupport: ISupport,
    mode_string: str,
    params: list[str],
) -> list[ModeChange]:
    """Parse `+m-b foo` style mode string with trailing params."""
    changes: list[ModeChange] = []
    adding = True
    param_i = 0
    for ch in mode_string:
        if ch == "+":
            adding = True
            continue
        if ch == "-":
            adding = False
            continue
        cat = isupport.mode_category(ch)
        needs_param = False
        if cat == "prefix" or cat in ("A", "B"):
            needs_param = True
        elif cat == "C":
            needs_param = adding  # param only when setting
        elif cat == "D":
            needs_param = False
        else:
            # Unknown: consume param if available (safer for membership-like)
            needs_param = param_i < len(params)

        param: str | None = None
        if needs_param and param_i < len(params):
            param = params[param_i]
            param_i += 1
        changes.append(ModeChange(add=adding, mode=ch, param=param, category=cat))
    return changes
