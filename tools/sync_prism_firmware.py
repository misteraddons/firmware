#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPO = "misteraddons/Reflex-Prism"
PUBLIC_REPO = "misteraddons/firmware"
ASSET_NAME = "prism_dac.uf2"
FLASH_NUKE_ASSET_NAME = "flash_nuke.uf2"
LATEST_DIRECTORY = "latest"
GITHUB_API = "https://api.github.com/repos"
USER_AGENT = "misteraddons-prism-firmware-sync/1.0"


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    release_title: str
    name: str
    api_url: str
    digest: str
    size: Optional[int]


def mirror_local_path(tag: str) -> str:
    return f"reflex-prism/{tag}/{ASSET_NAME}"


def mirror_asset_path(tag: str, asset_name: str) -> str:
    return f"reflex-prism/{tag}/{asset_name}"


def latest_asset_path(asset_name: str) -> str:
    return f"reflex-prism/{LATEST_DIRECTORY}/{asset_name}"


def public_latest_url(asset_name: str) -> str:
    return f"https://raw.githubusercontent.com/{PUBLIC_REPO}/main/{latest_asset_path(asset_name)}"


def prism_v1_hardware_check() -> dict:
    accepted_targets = [
        {
            "group": "prism-v11",
            "label": "Prism V1.05/V1.1",
            "markers": ["Hardware target: V1.05/V1.1 boards"],
        },
        {
            "group": "prism-v12",
            "label": "Prism V1.2",
            "markers": ["Hardware target: V1.2 boards"],
        },
        {
            "group": "prism-v13",
            "label": "Prism V1.3 Smart HD15",
            "markers": ["Hardware target: V1.3 Smart HD15 boards"],
        },
    ]
    mismatches = [
        {
            "group": "prism-pro",
            "label": "Prism Pro",
            "markers": ["Hardware target: Pro boards"],
        },
    ]
    return {
        "type": "serial_hardware_check",
        "label": "Reflex Prism hardware compatibility",
        "vid": "0x16D0",
        "pid": "0x14F6",
        "baud": 115200,
        "command": "dashboard config get",
        "timeout": 30,
        "open_settle": 0.5,
        "command_timeout": 8,
        "expected_group": "prism-v1",
        "expected_label": "Reflex Prism DAC V1",
        "accepted_targets": accepted_targets,
        "known_mismatches": mismatches,
    }


def prism_hardware_check_for_release(tag: str, release_title: str = "") -> dict:
    check = prism_v1_hardware_check()
    if re.fullmatch(r"v?1\.10(?:\.\d+)?", tag, flags=re.IGNORECASE) or re.fullmatch(
        r"prism-v1-r\d+", tag, flags=re.IGNORECASE
    ):
        targets = list(check["accepted_targets"]) + list(check["known_mismatches"])
        expected = next(target for target in targets if target["group"] == "prism-v11")
        check.pop("accepted_targets", None)
        check["expected_group"] = "prism-v11"
        check["expected_label"] = expected["label"]
        check["expect"] = list(expected["markers"])
        check["known_mismatches"] = [
            target for target in targets if target["group"] != "prism-v11"
        ]
    return check


def auth_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str) -> object:
    request = urllib.request.Request(url, headers=auth_headers())
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers=auth_headers("application/octet-stream"))
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def resolve_release_asset(repo: str, tag: Optional[str], asset_name: str = ASSET_NAME) -> ReleaseAsset:
    if tag:
        url = f"{GITHUB_API}/{repo}/releases/tags/{tag}"
    else:
        url = f"{GITHUB_API}/{repo}/releases/latest"

    release = request_json(url)
    if not isinstance(release, dict):
        raise RuntimeError("GitHub release response was not an object")

    release_tag = release.get("tag_name")
    if not isinstance(release_tag, str) or not release_tag:
        raise RuntimeError("GitHub release response did not include a tag")

    matches = [asset for asset in release.get("assets", []) if asset.get("name") == asset_name]
    if len(matches) != 1:
        names = ", ".join(asset.get("name", "") for asset in release.get("assets", []))
        raise RuntimeError(f"expected one {asset_name} asset in {repo} {release_tag}; got {len(matches)} in {names}")

    asset = matches[0]
    return ReleaseAsset(
        tag=release_tag,
        release_title=str(release.get("name") or ""),
        name=asset["name"],
        api_url=asset["url"],
        digest=asset.get("digest") or "",
        size=int(asset["size"]) if asset.get("size") is not None else None,
    )


def fetch_release_asset(
    repo: str,
    tag: Optional[str],
    attempts: int,
    delay: float,
    asset_name: str = ASSET_NAME,
) -> tuple[ReleaseAsset, bytes]:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            asset = resolve_release_asset(repo, tag, asset_name)
            data = request_bytes(asset.api_url)
            return asset, data
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            print(f"Release asset is not ready yet; retry {attempt}/{attempts}: {error}", file=sys.stderr)
            time.sleep(delay)

    assert last_error is not None
    raise last_error


def expected_sha256(asset: ReleaseAsset) -> Optional[str]:
    if asset.digest.startswith("sha256:"):
        return asset.digest.split(":", 1)[1].lower()
    return None


def validate_prism_uf2(data: bytes, asset_name: str = ASSET_NAME) -> None:
    sys.path.insert(0, str(ROOT))
    from firmware_installer import RP2040_UF2_FAMILY_ID, validate_uf2_stream

    validate_uf2_stream(BytesIO(data), asset_name, RP2040_UF2_FAMILY_ID)


def update_catalog_text(text: str, tag: str, release_title: str = "") -> str:
    catalog = json.loads(text)
    for item in catalog.get("items", []):
        if item.get("id") == "reflex-prism":
            item["local_paths"] = [mirror_local_path(tag)]
            item["hardware_check"] = prism_hardware_check_for_release(tag, release_title)
            item["sources"] = [
                {
                    "type": "github_repo_file",
                    "repo": "misteraddons/firmware",
                    "ref": "main",
                    "path": latest_asset_path(ASSET_NAME),
                }
            ]
            return json.dumps(catalog, indent=2) + "\n"
    raise RuntimeError("reflex-prism entry not found in firmware_catalog.json")


def update_checksums_text(text: str, tag: str, sha256: str, flash_nuke_sha256: Optional[str] = None) -> str:
    entries = {
        mirror_local_path(tag): sha256,
        latest_asset_path(ASSET_NAME): sha256,
    }
    if flash_nuke_sha256:
        entries[mirror_asset_path(tag, FLASH_NUKE_ASSET_NAME)] = flash_nuke_sha256
        entries[latest_asset_path(FLASH_NUKE_ASSET_NAME)] = flash_nuke_sha256
    suffixes = tuple(f"  {path}" for path in entries)
    lines = [line for line in text.splitlines() if not line.endswith(suffixes)]
    lines.extend(f"{digest}  {path}" for path, digest in entries.items())
    return "\n".join(lines) + "\n"


def update_readme_text(text: str, tag: str) -> str:
    rel_path = latest_asset_path(ASSET_NAME)
    row = (
        f"| Reflex Prism | [`{rel_path}`]({rel_path}) | "
        f"[stable download]({public_latest_url(ASSET_NAME)}) |"
    )
    text, row_count = re.subn(r"^\| Reflex Prism \| .* \| .* \|$", row, text, count=1, flags=re.MULTILINE)
    if row_count != 1:
        raise RuntimeError("Reflex Prism README table row not found")

    nuke_row = (
        f"| Reflex Prism Flash Nuke | "
        f"[`{latest_asset_path(FLASH_NUKE_ASSET_NAME)}`]({latest_asset_path(FLASH_NUKE_ASSET_NAME)}) | "
        f"[stable download]({public_latest_url(FLASH_NUKE_ASSET_NAME)}) |"
    )
    if re.search(r"^\| Reflex Prism Flash Nuke \|", text, flags=re.MULTILINE):
        text = re.sub(r"^\| Reflex Prism Flash Nuke \| .* \| .* \|$", nuke_row, text,
                      count=1, flags=re.MULTILINE)
    else:
        text = text.replace(row, row + "\n" + nuke_row, 1)

    note = (
        "Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. "
        f"The latest mirrored release is `{tag}`. Use `flash_nuke.uf2` only as a last-resort "
        "full erase; it removes settings and Custom EDID and must be followed by `prism_dac.uf2`. "
        f"Permanent downloads: {public_latest_url(ASSET_NAME)} and "
        f"{public_latest_url(FLASH_NUKE_ASSET_NAME)}."
    )
    text, note_count = re.subn(r"^-?\s*Reflex Prism: .*$", note, text, count=1, flags=re.MULTILINE)
    if note_count != 1:
        raise RuntimeError("Reflex Prism README note not found")
    return text


def update_repository(
    root: Path,
    tag: str,
    data: bytes,
    flash_nuke_data: bytes,
    release_title: str = "",
) -> Path:
    validate_prism_uf2(data, ASSET_NAME)
    validate_prism_uf2(flash_nuke_data, FLASH_NUKE_ASSET_NAME)
    sha256 = hashlib.sha256(data).hexdigest()
    flash_nuke_sha256 = hashlib.sha256(flash_nuke_data).hexdigest()
    rel_path = mirror_local_path(tag)
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    versioned_nuke = root / mirror_asset_path(tag, FLASH_NUKE_ASSET_NAME)
    versioned_nuke.write_bytes(flash_nuke_data)
    latest_firmware = root / latest_asset_path(ASSET_NAME)
    latest_firmware.parent.mkdir(parents=True, exist_ok=True)
    latest_firmware.write_bytes(data)
    (root / latest_asset_path(FLASH_NUKE_ASSET_NAME)).write_bytes(flash_nuke_data)

    catalog_path = root / "firmware_catalog.json"
    catalog_path.write_text(
        update_catalog_text(catalog_path.read_text(encoding="utf-8"), tag, release_title),
        encoding="utf-8",
    )

    checksums_path = root / "checksums.sha256"
    checksums_path.write_text(
        update_checksums_text(
            checksums_path.read_text(encoding="utf-8"), tag, sha256, flash_nuke_sha256
        ),
        encoding="utf-8",
    )

    readme_path = root / "README.md"
    readme_path.write_text(update_readme_text(readme_path.read_text(encoding="utf-8"), tag), encoding="utf-8")
    return target


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror the latest Reflex Prism firmware release into this repo.")
    parser.add_argument("--tag", help="Release tag to mirror. Defaults to the latest Reflex-Prism release.")
    parser.add_argument("--repo", default=SOURCE_REPO, help=f"Source GitHub repo. Defaults to {SOURCE_REPO}.")
    parser.add_argument("--asset-path", type=Path, help="Use an already downloaded prism_dac.uf2 instead of GitHub.")
    parser.add_argument("--flash-nuke-path", type=Path,
                        help="Use an already downloaded flash_nuke.uf2 instead of GitHub.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Firmware mirror repo root.")
    parser.add_argument("--attempts", type=int, default=12, help="Download retry attempts for release-event races.")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="Seconds between download retry attempts.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    root = args.root.resolve()

    if args.asset_path:
        if not args.tag:
            raise SystemExit("--tag is required with --asset-path")
        if not args.flash_nuke_path:
            raise SystemExit("--flash-nuke-path is required with --asset-path")
        tag = args.tag
        data = args.asset_path.read_bytes()
        flash_nuke_data = args.flash_nuke_path.read_bytes()
    else:
        asset, data = fetch_release_asset(args.repo, args.tag, args.attempts, args.retry_delay, ASSET_NAME)
        try:
            flash_asset, flash_nuke_data = fetch_release_asset(
                args.repo, asset.tag, args.attempts, args.retry_delay, FLASH_NUKE_ASSET_NAME
            )
        except Exception:
            legacy_nuke = root / latest_asset_path(FLASH_NUKE_ASSET_NAME)
            if not re.fullmatch(r"prism-v1-r\d+", asset.tag.lower()) or not legacy_nuke.is_file():
                raise
            flash_asset = None
            flash_nuke_data = legacy_nuke.read_bytes()
            print(f"Using existing {legacy_nuke.relative_to(root).as_posix()} for legacy {asset.tag}")
        if flash_asset is not None and flash_asset.tag != asset.tag:
            raise RuntimeError("Prism firmware and Flash Nuke resolved from different releases")
        tag = asset.tag
        release_title = asset.release_title
        digest = expected_sha256(asset)
        actual = hashlib.sha256(data).hexdigest()
        if digest and digest != actual:
            raise RuntimeError(f"{asset.name} sha256 {actual}; GitHub release digest is {digest}")
        if asset.size is not None and asset.size != len(data):
            raise RuntimeError(f"{asset.name} size {len(data)}; GitHub release size is {asset.size}")
        if flash_asset is not None:
            flash_digest = expected_sha256(flash_asset)
            flash_actual = hashlib.sha256(flash_nuke_data).hexdigest()
            if flash_digest and flash_digest != flash_actual:
                raise RuntimeError(
                    f"{flash_asset.name} sha256 {flash_actual}; GitHub release digest is {flash_digest}"
                )
            if flash_asset.size is not None and flash_asset.size != len(flash_nuke_data):
                raise RuntimeError(
                    f"{flash_asset.name} size {len(flash_nuke_data)}; GitHub release size is {flash_asset.size}"
                )

    target = update_repository(
        root, tag, data, flash_nuke_data, release_title if not args.asset_path else ""
    )
    print(f"Mirrored Reflex Prism {tag}: {target.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
