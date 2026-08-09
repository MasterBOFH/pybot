"""Entry point: python -m pybot [config.yaml]"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pybot.core.bot import Bot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pybot IRC bot")
    parser.add_argument(
        "config",
        nargs="?",
        default="config.yaml",
        help="Path to YAML config (default: config.yaml)",
    )
    args = parser.parse_args(argv)
    path = Path(args.config)
    if not path.is_file():
        print(f"Config not found: {path}", file=sys.stderr)
        return 1
    bot = Bot(path)
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
