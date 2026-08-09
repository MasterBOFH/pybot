"""ISUPPORT (005) parsing: CHANMODES, CASEMAPPING, WHOX, PREFIX, etc."""

from __future__ import annotations

from dataclasses import dataclass, field


# Defaults before 005 arrives
_DEFAULT_CHANMODES = ("eIbq", "k", "flj", "CFLMPQScgimnprstz")
_DEFAULT_PREFIX = ("ov", "@+")


@dataclass
class ISupport:
    raw: dict[str, str | None] = field(default_factory=dict)
    casemapping: str = "rfc1459"
    chanmodes: tuple[str, str, str, str] = _DEFAULT_CHANMODES
    prefix_modes: str = "ov"
    prefix_symbols: str = "@+"
    whox: bool = False
    chantypes: str = "#&"
    network: str | None = None
    statusmsg: str = ""
    nicklen: int = 30  # before 005; networks often advertise 9–30
    targmax: dict[str, int] = field(default_factory=dict)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.raw.get(key.upper(), default)

    def has(self, key: str) -> bool:
        return key.upper() in self.raw

    def casefold(self, name: str) -> str:
        mapping = self.casemapping.lower()
        if mapping == "ascii":
            return name.lower()
        # rfc1459 / strict-rfc1459
        table = str.maketrans(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\~",
            "abcdefghijklmnopqrstuvwxyz{}|^",
        )
        if mapping == "strict-rfc1459":
            table = str.maketrans(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\",
                "abcdefghijklmnopqrstuvwxyz{}|",
            )
        return name.translate(table)

    def equal(self, a: str, b: str) -> bool:
        return self.casefold(a) == self.casefold(b)

    def parse_tokens(self, params: list[str]) -> None:
        """Parse 005 middle params (exclude trailing 'are supported by this server')."""
        for token in params:
            if token.lower().endswith("are supported by this server"):
                continue
            if "=" in token:
                key, value = token.split("=", 1)
            else:
                key, value = token, None
            key = key.upper()
            self.raw[key] = value
            self._apply(key, value)

    def _apply(self, key: str, value: str | None) -> None:
        if key == "CASEMAPPING" and value:
            self.casemapping = value.lower()
        elif key == "CHANMODES" and value:
            parts = value.split(",")
            while len(parts) < 4:
                parts.append("")
            self.chanmodes = (parts[0], parts[1], parts[2], parts[3])
        elif key == "PREFIX" and value:
            # (ov)@+
            if value.startswith("(") and ")" in value:
                modes, symbols = value[1:].split(")", 1)
                self.prefix_modes = modes
                self.prefix_symbols = symbols
        elif key == "WHOX":
            self.whox = True
        elif key == "CHANTYPES" and value is not None:
            self.chantypes = value
        elif key == "NETWORK" and value is not None:
            self.network = value
        elif key == "STATUSMSG" and value is not None:
            self.statusmsg = value
        elif key in {"NICKLEN", "MAXNICKLEN"} and value:
            try:
                self.nicklen = max(1, int(value))
            except ValueError:
                pass
        elif key == "TARGMAX" and value:
            for item in value.split(","):
                if ":" in item:
                    cmd, num = item.split(":", 1)
                    try:
                        self.targmax[cmd.upper()] = int(num)
                    except ValueError:
                        pass

    def mode_category(self, letter: str) -> str | None:
        """Return 'A'|'B'|'C'|'D'|'prefix' or None."""
        if letter in self.prefix_modes:
            return "prefix"
        a, b, c, d = self.chanmodes
        if letter in a:
            return "A"
        if letter in b:
            return "B"
        if letter in c:
            return "C"
        if letter in d:
            return "D"
        return None

    def symbol_for_mode(self, mode: str) -> str | None:
        if mode in self.prefix_modes:
            return self.prefix_symbols[self.prefix_modes.index(mode)]
        return None

    def mode_for_symbol(self, symbol: str) -> str | None:
        if symbol in self.prefix_symbols:
            return self.prefix_modes[self.prefix_symbols.index(symbol)]
        return None
