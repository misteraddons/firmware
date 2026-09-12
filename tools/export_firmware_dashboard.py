#!/usr/bin/env python3
"""Generate the browser dashboard manifest from the canonical firmware catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import firmware_installer as installer

WEB_ROOT = ROOT / "web" / "firmware-dashboard"
PUBLIC_REPO_RAW = "https://raw.githubusercontent.com/misteraddons/firmware/"


def checksum_map(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split(maxsplit=1)
        result[relative.strip().replace("\\", "/")] = digest.lower()
    return result


def relative_source(source: installer.FirmwareSource, root: Path) -> str:
    return source.path.resolve().relative_to(root.resolve()).as_posix()


def committed_revision(root: Path, relative: str) -> str:
    revision = subprocess.check_output(
        ["git", "-C", str(root), "log", "-1", "--format=%H", "--", relative],
        text=True,
    ).strip()
    if len(revision) != 40:
        raise ValueError(f"No immutable committed revision for {relative}")
    return revision


def browser_product(item: installer.CatalogItem, root: Path, checksums: dict[str, str]) -> dict:
    product = {
        "id": item.item_id,
        "label": item.label,
        "installMethod": item.install_method,
        "notes": item.notes,
        "usbFilters": [],
        "identity": {"supported": False},
        "releases": [],
        "backups": {
            "settings": {"supported": False, "reason": "No approved browser settings protocol."},
            "firmware": {"supported": False, "reason": "Application firmware readback is unavailable."},
            "fullFlash": {"supported": False, "reason": "PICOBOOT readback is not hardware validated."},
        },
        "directFlash": {"supported": False, "reason": "Browser PICOBOOT write/verify is not hardware validated."},
    }
    if item.item_id != "reflex-prism":
        return product

    hardware = item.hardware_check or {}
    serial = item.pre_flash_bootloader or item.post_flash_check or {}
    product.update({
        "usbFilters": [{"vendorId": installer.parse_optional_int(serial.get("vid")), "productId": installer.parse_optional_int(serial.get("pid"))}],
        "identity": {
            "supported": True,
            "transport": "serial",
            "command": "status\r\ndashboard config get",
            "timeoutMs": int(float(hardware.get("command_timeout", 8)) * 1000),
            "markers": ["=== Status ===", "[DASHBOARD] CONFIG BEGIN", "[DASHBOARD] CONFIG END"],
            "versionPatterns": [r"Firmware Version:\s*v?([^\s]+)", r"Firmware:\s*v?([^\s]+)"],
            "uniqueIdPatterns": [r"Board ID:\s*([0-9A-F]{16})"],
        },
        "hardwareCheck": {
            "acceptedTargets": hardware.get("accepted_targets", []),
            "knownMismatches": hardware.get("known_mismatches", []),
        },
        "backups": {
            "settings": {
                "supported": False,
                "implemented": True,
                "transport": "serial",
                "command": "dashboard config get",
                "expect": ["[DASHBOARD] CONFIG BEGIN", "[DASHBOARD] CONFIG END"],
                "timeoutMs": int(float(hardware.get("command_timeout", 8)) * 1000),
                "sensitivity": "local-settings",
                "reason": "Implemented and simulated, but disabled until backup output is validated on hardware.",
            },
            "firmware": {"supported": False, "reason": "No application firmware readback command."},
            "fullFlash": {"supported": False, "reason": "PICOBOOT readback and original-board restore are not hardware validated."},
        },
        "postFlashCheck": {
            "transport": "serial",
            "commands": (item.post_flash_check or {}).get("commands", []),
            "commandTimeoutMs": int(float((item.post_flash_check or {}).get("command_timeout", 8)) * 1000),
        },
        "flashPolicy": {
            "expectedFamily": item.expected_uf2_family,
            "allowedFlashRanges": [{"start": 0x10000000, "end": 0x10200000}],
            # Normal firmware UF2s must not write the final 64 KiB used for persistent data.
            "protectedFlashRanges": [{"start": 0x101F0000, "end": 0x10200000}],
        },
    })

    for version in installer.catalog_firmware_versions(item, root):
        if version.version.casefold() in {"latest", "main", "master", "current"}:
            continue
        relative = relative_source(version.source, root)
        if relative not in checksums or version.source.copy_name.casefold() == "flash_nuke.uf2":
            continue
        check = version.hardware_check or {}
        accepted_groups = [str(entry.get("group")) for entry in check.get("accepted_targets", []) if entry.get("group")]
        group = str(check.get("expected_group") or "")
        hardware_groups = accepted_groups if group == "prism-v1" else [group]
        if not hardware_groups:
            continue
        installer.validate_firmware_source(version.source, item.expected_uf2_family)
        product["releases"].append({
            "version": version.version.lstrip("v"),
            "channel": "stable",
            "hardwareGroups": hardware_groups,
            "fileName": version.source.copy_name,
            "sha256": checksums[relative],
            "bytes": version.source.path.stat().st_size,
            "url": f"{PUBLIC_REPO_RAW}{committed_revision(root, relative)}/{relative}",
            "releaseNotes": "Approved Reflex Prism stable firmware from the canonical firmware catalog.",
        })
    return product


def build_manifest(root: Path = ROOT) -> dict:
    checksums = checksum_map(root)
    products = [browser_product(item, root, checksums) for item in installer.load_catalog(root)]
    return {
        "schema": 1,
        "generatedFrom": "firmware_catalog.json + checksums.sha256 + firmware_installer.py",
        "privacy": {"uploads": False, "analytics": False, "storage": "local-only"},
        "transports": {"serial": "supported", "hid": "permission-only", "picoboot": "investigated-disabled"},
        "products": products,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=WEB_ROOT / "manifest.json")
    args = parser.parse_args()
    manifest = build_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({hashlib.sha256(args.output.read_bytes()).hexdigest()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
