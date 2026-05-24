#!/usr/bin/env python3
"""Terminal-only firmware updater.

Default behavior is the common macOS Prism path:

    python3 firmware_cli.py

That flashes the Reflex Prism catalog item once and exits. Pass any
firmware_installer.py CLI arguments to override the default.
"""

from __future__ import annotations

import sys
from typing import Sequence

from firmware_installer import main as installer_main


DEFAULT_ARGS = ("--product", "reflex-prism", "--once")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = list(DEFAULT_ARGS)
    return installer_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
