#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "misteraddons-firmware-audit/1.0"
RP2040_UF2_FAMILY_ID = 0xE48BFF56


@dataclass(frozen=True)
class SourceCheck:
    label: str
    local_path: str
    source_type: str
    repo: str
    release: str = "latest"
    asset_regex: str = ""
    ref: str = "main"
    repo_path: str = ""
    expected_family: Optional[int] = None


@dataclass(frozen=True)
class SourceDigest:
    label: str
    sha256: str
    size: Optional[int]


CHECKS = [
    SourceCheck(
        "MiSTercade V1 package",
        "mistercade-v1/main-2025/_MiSTercade_V1-2025.zip",
        "github_repo_file",
        "misteraddons/MiSTercade",
        repo_path="mistercade_v1_2025/firmware/_MiSTercade_V1-2025.zip",
    ),
    SourceCheck(
        "MiSTercade V2 firmware",
        "mistercade-v2/main/Scripts/_MiSTercade_V2_/GP2040-CE_latest_MiSTercadeV2.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_MiSTercadeV2\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "MiSTercade V2 flash_nuke",
        "mistercade-v2/main/Scripts/_MiSTercade_V2_/flash_nuke.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^flash_nuke\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "MiSTercade V2 erase script",
        "mistercade-v2/main/Scripts/_MiSTercade_V2_/MiSTercade_V2_Erase.sh",
        "github_repo_file",
        "misteraddons/MiSTercadeV2",
        repo_path="Scripts/_MiSTercade_V2_/MiSTercade_V2_Erase.sh",
    ),
    SourceCheck(
        "MiSTercade V2 program script",
        "mistercade-v2/main/Scripts/_MiSTercade_V2_/MiSTercade_V2_Program.sh",
        "github_repo_file",
        "misteraddons/MiSTercadeV2",
        repo_path="Scripts/_MiSTercade_V2_/MiSTercade_V2_Program.sh",
    ),
    SourceCheck(
        "Reflex Adapt updater",
        "reflex-adapt/v2.01/reflex_updater.sh",
        "github_release_asset",
        "misteraddons/Reflex-Adapt",
        asset_regex=r"^reflex_updater\.sh$",
    ),
    SourceCheck(
        "Reflex Adapt zip",
        "reflex-adapt/v2.01/reflex-v2.01.zip",
        "github_release_asset",
        "misteraddons/Reflex-Adapt",
        asset_regex=r"^reflex-v.*\.zip$",
    ),
    SourceCheck(
        "Reflex Adapt tarball",
        "reflex-adapt/v2.01/reflex-v2.01.tar.gz",
        "github_release_asset",
        "misteraddons/Reflex-Adapt",
        asset_regex=r"^reflex-v.*\.tar\.gz$",
    ),
    SourceCheck(
        "Reflex CTRL Genesis 6",
        "reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlGenesis6.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexCtrlGenesis6\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex CTRL NES",
        "reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlNES.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexCtrlNES\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex CTRL SNES",
        "reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSNES.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexCtrlSNES\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex CTRL Saturn",
        "reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSaturn.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexCtrlSaturn\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex CTRL Virtual Boy",
        "reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlVB.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexCtrlVB\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex Encode V1.2",
        "reflex-encode/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV1.2.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexEncodeV1\.2\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex Encode V2.0",
        "reflex-encode/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV2.0.uf2",
        "github_release_asset",
        "OpenStickCommunity/GP2040-CE",
        asset_regex=r"^GP2040-CE_.*_ReflexEncodeV2\.0\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
    SourceCheck(
        "Reflex Prism",
        "reflex-prism/v1.10.4/prism_dac.uf2",
        "github_release_asset",
        "misteraddons/Reflex-Prism",
        asset_regex=r"^prism_dac\.uf2$",
        expected_family=RP2040_UF2_FAMILY_ID,
    ),
]


def request_json(url: str) -> object:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def hash_bytes(data: bytes) -> tuple[str, int]:
    return hashlib.sha256(data).hexdigest(), len(data)


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def hash_url(url: str) -> tuple[str, int]:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)

    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def resolve_release_asset(check: SourceCheck) -> SourceDigest:
    if check.release == "latest":
        url = f"https://api.github.com/repos/{check.repo}/releases/latest"
    else:
        url = f"https://api.github.com/repos/{check.repo}/releases/tags/{check.release}"

    release = request_json(url)
    assert isinstance(release, dict)
    pattern = re.compile(check.asset_regex, re.IGNORECASE)
    matches = [asset for asset in release.get("assets", []) if pattern.match(asset.get("name", ""))]
    if len(matches) != 1:
        names = ", ".join(asset.get("name", "") for asset in release.get("assets", []))
        raise RuntimeError(f"{check.label}: expected one asset matching {check.asset_regex}; got {len(matches)} in {names}")

    asset = matches[0]
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        sha256 = digest.split(":", 1)[1].lower()
        size = int(asset["size"]) if asset.get("size") is not None else None
    else:
        sha256, size = hash_url(asset["browser_download_url"])
    return SourceDigest(f"{check.repo} {release.get('tag_name', check.release)} / {asset['name']}", sha256, size)


def resolve_repo_file(check: SourceCheck) -> SourceDigest:
    path = check.repo_path.strip("/")
    url = f"https://api.github.com/repos/{check.repo}/contents/{path}?ref={check.ref}"
    item = request_json(url)
    assert isinstance(item, dict)
    if item.get("type") != "file":
        raise RuntimeError(f"{check.label}: source is not a file: {check.repo}/{path}")

    content = item.get("content")
    if content and item.get("encoding") == "base64":
        sha256, size = hash_bytes(base64.b64decode(content))
    else:
        sha256, size = hash_url(item["download_url"])
    return SourceDigest(f"{check.repo}@{check.ref} / {path}", sha256, size)


def resolve_source(check: SourceCheck) -> SourceDigest:
    if check.source_type == "github_release_asset":
        return resolve_release_asset(check)
    if check.source_type == "github_repo_file":
        return resolve_repo_file(check)
    raise RuntimeError(f"{check.label}: unsupported source type {check.source_type}")


def load_checksums() -> dict[str, str]:
    checksums: dict[str, str] = {}
    path = ROOT / "checksums.sha256"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, local_path = line.split(None, 1)
        checksums[local_path.strip().replace("\\", "/")] = digest.lower()
    return checksums


def validate_local_uf2(path: Path, expected_family: Optional[int]) -> None:
    if path.suffix.lower() != ".uf2":
        return

    sys.path.insert(0, str(ROOT))
    from firmware_installer import FirmwareSource, validate_firmware_source

    validate_firmware_source(FirmwareSource(path), expected_family)


def catalog_local_paths() -> set[str]:
    catalog = json.loads((ROOT / "firmware_catalog.json").read_text(encoding="utf-8"))
    paths: set[str] = set()
    for item in catalog.get("items", []):
        for local_path in item.get("local_paths", []):
            paths.add(local_path.replace("\\", "/"))
    return paths


def main() -> int:
    checksums = load_checksums()
    failures: list[str] = []
    checked_paths = {check.local_path for check in CHECKS}

    for local_path in sorted(catalog_local_paths() - checked_paths):
        failures.append(f"{local_path}: catalog local path has no source audit check")

    for check in CHECKS:
        path = ROOT / check.local_path
        if not path.exists():
            failures.append(f"{check.label}: missing {check.local_path}")
            continue

        try:
            validate_local_uf2(path, check.expected_family)
            local_sha, local_size = hash_file(path)
            recorded_sha = checksums.get(check.local_path)
            source = resolve_source(check)
        except Exception as error:
            failures.append(f"{check.label}: {error}")
            continue

        if recorded_sha != local_sha:
            failures.append(f"{check.label}: checksums.sha256 has {recorded_sha}; local file is {local_sha}")
        if source.size is not None and source.size != local_size:
            failures.append(f"{check.label}: local size {local_size}; source size {source.size} ({source.label})")
        if source.sha256 != local_sha:
            failures.append(f"{check.label}: local sha256 {local_sha}; source sha256 {source.sha256} ({source.label})")

        if not any(failure.startswith(f"{check.label}:") for failure in failures):
            print(f"[OK] {check.label}: {check.local_path} matches {source.label}")

    if failures:
        print()
        print("Firmware source audit failed:")
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1

    print()
    print(f"[OK] {len(CHECKS)} firmware source check(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
