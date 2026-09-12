#!/usr/bin/env python3
"""Export the static firmware dashboard into the isolated MkDocs repository."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:
    from .export_firmware_dashboard import ROOT, WEB_ROOT, build_manifest
except ImportError:  # Direct script execution.
    from export_firmware_dashboard import ROOT, WEB_ROOT, build_manifest

FILES = ("index.html", "styles.css", "core.js", "app.js", "management-identity.js", "manifest.json")
HEADER_BLOCK = """

/tools/firmware/*
  Cache-Control: no-cache, no-transform
  X-Robots-Tag: noindex, nofollow, noarchive
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: no-referrer
  Permissions-Policy: hid=(self), serial=(self), usb=(self)
"""


def export(destination_repo: Path) -> None:
    manifest = build_manifest(ROOT)
    (WEB_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    destination = destination_repo / "docs" / "tools" / "firmware"
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.iterdir():
        if stale.is_file() and stale.name not in FILES:
            raise ValueError(f"Unexpected existing firmware-dashboard file: {stale.name}")
    for name in FILES:
        shutil.copy2(WEB_ROOT / name, destination / name)
    headers_path = destination_repo / "docs" / "_headers"
    headers = headers_path.read_text(encoding="utf-8")
    if "/tools/firmware/*" not in headers:
        headers_path.write_text(headers.rstrip() + HEADER_BLOCK + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    export(args.destination.resolve())
    print(f"Exported firmware dashboard to {args.destination.resolve() / 'docs/tools/firmware'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
