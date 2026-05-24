#!/usr/bin/env python3
"""Terminal-only firmware updater.

Default behavior lists available products:

    python3 firmware_cli.py

Pass firmware_installer.py CLI arguments to download or flash firmware.
"""

from __future__ import annotations

import sys
from typing import Sequence

from firmware_installer import main as installer_main


DEFAULT_ARGS = ("--list-catalog",)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = list(DEFAULT_ARGS)
    return installer_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
