"""IRC line parsing and formatting (tags, prefix, command, params)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Prefix:
    raw: str
    nick: str | None = None
    user: str | None = None
    host: str | None = None
    is_server: bool = False

    @classmethod
    def parse(cls, raw: str) -> Prefix:
        if "!" in raw or "@" in raw:
            nick = raw
            user = None
            host = None
            if "!" in nick:
                nick, rest = nick.split("!", 1)
                if "@" in rest:
                    user, host = rest.split("@", 1)
                else:
                    user = rest
            elif "@" in nick:
                nick, host = nick.split("@", 1)
            return cls(raw=raw, nick=nick, user=user, host=host, is_server=False)
        return cls(raw=raw, nick=None, user=None, host=raw, is_server=True)


@dataclass
class Message:
    tags: dict[str, str | None] = field(default_factory=dict)
    prefix: Prefix | None = None
    command: str = ""
    params: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def source_nick(self) -> str | None:
        if self.prefix and not self.prefix.is_server:
            return self.prefix.nick
        return None

    @property
    def trailing(self) -> str:
        return self.params[-1] if self.params else ""

    def tag(self, key: str, default: str | None = None) -> str | None:
        if key not in self.tags:
            return default
        val = self.tags[key]
        return default if val is None else val


def _unescape_tag_value(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            mapping = {":": ";", "s": " ", "r": "\r", "n": "\n", "\\": "\\"}
            out.append(mapping.get(nxt, nxt))
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_tags(tag_str: str) -> dict[str, str | None]:
    tags: dict[str, str | None] = {}
    for item in tag_str.split(";"):
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            tags[key] = _unescape_tag_value(value)
        else:
            tags[item] = None
    return tags


def parse_message(line: str) -> Message:
    """Parse a single IRC protocol line (without CR/LF)."""
    raw = line
    tags: dict[str, str | None] = {}
    prefix: Prefix | None = None

    if line.startswith("@"):
        tag_part, line = line[1:].split(" ", 1)
        tags = parse_tags(tag_part)

    if line.startswith(":"):
        prefix_part, line = line[1:].split(" ", 1)
        prefix = Prefix.parse(prefix_part)

    parts = line.split(" ")
    command = parts[0].upper()
    params: list[str] = []
    i = 1
    while i < len(parts):
        if parts[i].startswith(":"):
            params.append(" ".join(parts[i:])[1:])
            break
        if parts[i]:
            params.append(parts[i])
        i += 1

    return Message(tags=tags, prefix=prefix, command=command, params=params, raw=raw)


def format_line(command: str, *params: str, tags: dict[str, Any] | None = None) -> str:
    """Format an outbound IRC line (no CR/LF)."""
    parts: list[str] = []
    if tags:
        tag_items = []
        for k, v in tags.items():
            if v is None:
                tag_items.append(str(k))
            else:
                tag_items.append(f"{k}={v}")
        parts.append("@" + ";".join(tag_items))
    parts.append(command.upper())
    if params:
        *middle, last = params
        for p in middle:
            parts.append(p)
        if last == "" or " " in last or last.startswith(":"):
            parts.append(":" + last)
        else:
            parts.append(last)
    return " ".join(parts)
