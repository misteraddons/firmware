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
ASSET_NAME = "prism_dac.uf2"
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


def prism_hardware_check(
    *,
    expected_group: str,
    expected_label: str,
    expected_marker: str,
) -> dict:
    mismatches = [
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
        "expected_group": expected_group,
        "expected_label": expected_label,
        "expect": [expected_marker],
        "known_mismatches": [
            mismatch for mismatch in mismatches if mismatch["group"] != expected_group
        ],
    }


def prism_hardware_check_for_release(tag: str, release_title: str = "") -> dict:
    text = f"{release_title} {tag}".lower()
    if re.search(r"\bv1\.2(?:0)?\b", text) or re.match(r"^v?1\.20(?:\.|$)", tag.lower()):
        return prism_hardware_check(
            expected_group="prism-v12",
            expected_label="Prism V1.2",
            expected_marker="Hardware target: V1.2 boards",
        )
    if "pro" in text:
        return prism_hardware_check(
            expected_group="prism-pro",
            expected_label="Prism Pro",
            expected_marker="Hardware target: Pro boards",
        )
    return prism_hardware_check(
        expected_group="prism-v11",
        expected_label="Prism V1.05/V1.1",
        expected_marker="Hardware target: V1.05/V1.1 boards",
    )


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


def resolve_release_asset(repo: str, tag: Optional[str]) -> ReleaseAsset:
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

    matches = [asset for asset in release.get("assets", []) if asset.get("name") == ASSET_NAME]
    if len(matches) != 1:
        names = ", ".join(asset.get("name", "") for asset in release.get("assets", []))
        raise RuntimeError(f"expected one {ASSET_NAME} asset in {repo} {release_tag}; got {len(matches)} in {names}")

    asset = matches[0]
    return ReleaseAsset(
        tag=release_tag,
        release_title=str(release.get("name") or ""),
        name=asset["name"],
        api_url=asset["url"],
        digest=asset.get("digest") or "",
        size=int(asset["size"]) if asset.get("size") is not None else None,
    )


def fetch_release_asset(repo: str, tag: Optional[str], attempts: int, delay: float) -> tuple[ReleaseAsset, bytes]:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            asset = resolve_release_asset(repo, tag)
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


def validate_prism_uf2(data: bytes) -> None:
    sys.path.insert(0, str(ROOT))
    from firmware_installer import RP2040_UF2_FAMILY_ID, validate_uf2_stream

    validate_uf2_stream(BytesIO(data), ASSET_NAME, RP2040_UF2_FAMILY_ID)


def update_catalog_text(text: str, tag: str, release_title: str = "") -> str:
    catalog = json.loads(text)
    for item in catalog.get("items", []):
        if item.get("id") == "reflex-prism":
            item["local_paths"] = [mirror_local_path(tag)]
            item["hardware_check"] = prism_hardware_check_for_release(tag, release_title)
            return json.dumps(catalog, indent=2) + "\n"
    raise RuntimeError("reflex-prism entry not found in firmware_catalog.json")


def update_checksums_text(text: str, tag: str, sha256: str) -> str:
    rel_path = mirror_local_path(tag)
    new_line = f"{sha256}  {rel_path}"
    lines = [line for line in text.splitlines() if not line.endswith(f"  {rel_path}")]
    lines.append(new_line)
    return "\n".join(lines) + "\n"


def update_readme_text(text: str, tag: str) -> str:
    rel_path = mirror_local_path(tag)
    release_url = f"https://github.com/{SOURCE_REPO}/releases/tag/{tag}"
    row = (
        f"| Reflex Prism | `{rel_path}` | "
        f"[`{SOURCE_REPO}` {tag}]({release_url}) |"
    )
    text, row_count = re.subn(r"^\| Reflex Prism \| .* \| .* \|$", row, text, count=1, flags=re.MULTILINE)
    if row_count != 1:
        raise RuntimeError("Reflex Prism README table row not found")

    note = (
        "Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. "
        f"The latest mirrored release is `{tag}`."
    )
    text, note_count = re.subn(r"^-?\s*Reflex Prism: .*$", note, text, count=1, flags=re.MULTILINE)
    if note_count != 1:
        raise RuntimeError("Reflex Prism README note not found")
    return text


def update_repository(root: Path, tag: str, data: bytes, release_title: str = "") -> Path:
    validate_prism_uf2(data)
    sha256 = hashlib.sha256(data).hexdigest()
    rel_path = mirror_local_path(tag)
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    catalog_path = root / "firmware_catalog.json"
    catalog_path.write_text(
        update_catalog_text(catalog_path.read_text(encoding="utf-8"), tag, release_title),
        encoding="utf-8",
    )

    checksums_path = root / "checksums.sha256"
    checksums_path.write_text(
        update_checksums_text(checksums_path.read_text(encoding="utf-8"), tag, sha256),
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
        tag = args.tag
        data = args.asset_path.read_bytes()
    else:
        asset, data = fetch_release_asset(args.repo, args.tag, args.attempts, args.retry_delay)
        tag = asset.tag
        release_title = asset.release_title
        digest = expected_sha256(asset)
        actual = hashlib.sha256(data).hexdigest()
        if digest and digest != actual:
            raise RuntimeError(f"{asset.name} sha256 {actual}; GitHub release digest is {digest}")
        if asset.size is not None and asset.size != len(data):
            raise RuntimeError(f"{asset.name} size {len(data)}; GitHub release size is {asset.size}")

    target = update_repository(root, tag, data, release_title if not args.asset_path else "")
    print(f"Mirrored Reflex Prism {tag}: {target.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
