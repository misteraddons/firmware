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


def generated_manifest_text() -> str:
    return json.dumps(build_manifest(ROOT), indent=2) + "\n"


def check(destination_repo: Path) -> list[str]:
    """Report how the deployed copy differs from this repository.

    The export is one-directional, so without this the live site can lag the
    source with nothing to notice. Reports rather than repairs: deciding to
    republish is a deployment decision, not a tooling one.

    Compares decoded text, not raw bytes. Every published file is text, and the
    two repositories need not agree on line endings for the served content to be
    identical; byte comparison would report drift on every Windows checkout.
    """
    drift: list[str] = []
    manifest = generated_manifest_text()
    if (WEB_ROOT / "manifest.json").read_text(encoding="utf-8") != manifest:
        drift.append("manifest.json in this repository is stale; re-run export_firmware_dashboard.py")
    destination = destination_repo / "docs" / "tools" / "firmware"
    for name in FILES:
        target = destination / name
        expected = manifest if name == "manifest.json" else (WEB_ROOT / name).read_text(encoding="utf-8")
        if not target.is_file():
            drift.append(f"absent from the docs repository: {name}")
        elif target.read_text(encoding="utf-8") != expected:
            drift.append(f"deployed copy differs: {name}")
    headers_path = destination_repo / "docs" / "_headers"
    if not headers_path.is_file() or "/tools/firmware/*" not in headers_path.read_text(encoding="utf-8"):
        drift.append("absent from the docs repository: _headers block for /tools/firmware/*")
    return drift


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
    parser.add_argument("--check", action="store_true", help="Report drift against the docs repository and write nothing.")
    args = parser.parse_args()
    destination = args.destination.resolve()
    if args.check:
        drift = check(destination)
        for entry in drift:
            print(entry)
        print(f"{len(drift)} difference(s) against {destination / 'docs/tools/firmware'}")
        return 1 if drift else 0
    export(destination)
    print(f"Exported firmware dashboard to {destination / 'docs/tools/firmware'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
