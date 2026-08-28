#!/usr/bin/env python3
"""Cross-platform RP2040 UF2 firmware installer.

Run without arguments for the GUI:

    python firmware_installer.py

Or run headless:

    python firmware_installer.py --firmware path/to/firmware.uf2
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


RPI_RP2_LABEL = "RPI-RP2"
COPY_BUFFER_SIZE = 1024 * 1024
CHECK_MARK = "[OK]"
UF2_TARGET_NAME = "firmware.uf2"
UF2_BLOCK_SIZE = 512
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID_PRESENT = 0x00002000
RP2040_UF2_FAMILY_ID = 0xE48BFF56

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

CONTROLLER_FIRMWARE_KEYWORDS = (
    "mistercade",
    "classic2usb",
    "gp2040",
    "gamepad",
    "joystick",
    "controller",
    "reflex-adapt",
    "reflex_adapt",
    "adapt",
)

CONTROLLER_DEVICE_KEYWORDS = (
    "mistercade",
    "classic2usb",
    "gp2040",
    "joystick",
    "hid-compliant game controller",
    "xinput",
    "reflex adapt",
    "reflex-adapt",
)

CONTROLLER_DEVICE_ID_KEYWORDS = (
    "VID_320F&PID_5044",
)


class FirmwareError(Exception):
    """Base installer error."""


class MultipleFirmwareFound(FirmwareError):
    """Raised when a zip contains more than one UF2 member."""

    def __init__(self, entries: Sequence[str]) -> None:
        self.entries = list(entries)
        super().__init__("Archive contains multiple UF2 files.")


class InstallerStopped(Exception):
    """Raised when the user stops the installer."""


@dataclass(frozen=True)
class FirmwareSource:
    path: Path
    zip_member: Optional[str] = None

    @property
    def display_name(self) -> str:
        if self.zip_member:
            return f"{self.path.name} -> {self.zip_member}"
        return str(self.path)

    @property
    def copy_name(self) -> str:
        if self.zip_member:
            return Path(self.zip_member).name
        return self.path.name

    @contextlib.contextmanager
    def open_binary(self) -> Iterator[BinaryIO]:
        if self.zip_member:
            with zipfile.ZipFile(self.path) as archive:
                with archive.open(self.zip_member, "r") as stream:
                    yield stream
            return

        with self.path.open("rb") as stream:
            yield stream


@dataclass(frozen=True)
class FirmwareChoice:
    label: str
    source: Optional[FirmwareSource]
    item_id: Optional[str] = None
    install_method: str = "rp2040"
    controller_check: Optional[bool] = None
    hardware_check: Optional[dict] = None
    pre_flash_bootloader: Optional[dict] = None
    post_flash_check: Optional[dict] = None
    status: str = "custom"


@dataclass(frozen=True)
class FirmwareVersion:
    version: str
    source: FirmwareSource
    status: str
    hardware_check: Optional[dict] = None


@dataclass(frozen=True)
class CatalogItem:
    item_id: str
    label: str
    install_method: str
    file_type: str
    controller_check: bool
    sources: Sequence[dict]
    local_paths: Sequence[str]
    hardware_check: Optional[dict] = None
    pre_flash_bootloader: Optional[dict] = None
    post_flash_check: Optional[dict] = None
    notes: str = ""
    expected_uf2_family: Optional[int] = None


@dataclass(frozen=True)
class DownloadPlan:
    url: str
    file_name: str
    source_label: str
    version: str = ""
    immutable_version: bool = False


@dataclass(frozen=True)
class Mount:
    path: Path
    label: str


@dataclass(frozen=True)
class Controller:
    identity: str
    name: str


def list_uf2_entries(zip_path: Path) -> List[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return sorted(
            entry.filename
            for entry in archive.infolist()
            if not entry.is_dir()
            and entry.filename.lower().endswith(".uf2")
            and "__macosx/" not in entry.filename.lower()
        )


def resolve_firmware(path: Path, zip_member: Optional[str] = None) -> FirmwareSource:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FirmwareError(f"Firmware does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix == ".uf2":
        if zip_member:
            raise FirmwareError("--zip-member is only valid for .zip firmware packages.")
        source = FirmwareSource(path)
        validate_firmware_source(source)
        return source

    if suffix == ".zip":
        entries = list_uf2_entries(path)
        if zip_member:
            if zip_member not in entries:
                raise FirmwareError(f"UF2 member not found in archive: {zip_member}")
            return FirmwareSource(path, zip_member)
        if not entries:
            raise FirmwareError("No UF2 files found in this archive.")
        if len(entries) > 1:
            raise MultipleFirmwareFound(entries)
        source = FirmwareSource(path, entries[0])
        validate_firmware_source(source)
        return source

    raise FirmwareError("Select a .uf2 file or a .zip file containing a .uf2 file.")


PROJECT_LABELS = {
    "mistercade-v2": "MiSTercade V2",
    "reflex-prism": "Reflex Prism",
}

SKIP_DISCOVERY_DIRS = {".git", "__pycache__", "__macosx"}
CATALOG_FILE = "firmware_catalog.json"
USER_AGENT = "MiSTerAddonsFirmwareInstaller/1.0"
GITHUB_API = "https://api.github.com/repos"
PRISM_PRE_FLASH_BOOTLOADER = {
    "type": "serial_bootloader",
    "label": "Reflex Prism CDC bootloader command",
    "vid": "0x16D0",
    "pid": "0x14F6",
    "baud": 115200,
    "command": "bootloader",
    "timeout": 30,
    "open_settle": 0.5,
    "command_timeout": 2,
}
PRISM_HARDWARE_CHECK = {
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
    "accepted_targets": [
        {
            "group": "prism-v11",
            "label": "Prism V1.05/V1.1",
            "markers": [
                "Hardware target: V1.05/V1.1 boards",
            ],
        },
        {
            "group": "prism-v12",
            "label": "Prism V1.2",
            "markers": [
                "Hardware target: V1.2 boards",
            ],
        },
        {
            "group": "prism-v13",
            "label": "Prism V1.3 Smart HD15",
            "markers": [
                "Hardware target: V1.3 Smart HD15 boards",
            ],
        },
    ],
    "known_mismatches": [
        {
            "group": "prism-pro",
            "label": "Prism Pro",
            "markers": [
                "Hardware target: Pro boards",
            ],
        },
    ],
}
PRISM_POST_FLASH_CHECK = {
    "type": "serial_console",
    "label": "Reflex Prism CDC sanity check",
    "vid": "0x16D0",
    "pid": "0x14F6",
    "baud": 115200,
    "timeout": 30,
    "open_settle": 2.5,
    "command_timeout": 8,
    "commands": [
        {
            "command": "status",
            "expect": [
                "=== Status ===",
                "EDID Route",
                "Sync Mode",
            ],
        },
        {
            "command": "dashboard config get",
            "expect": [
                "[DASHBOARD] CONFIG BEGIN",
                "[DASHBOARD] CONFIG END",
            ],
        },
    ],
}


def default_firmware_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    return Path(__file__).resolve().parent


def discover_firmware_choices(root: Optional[Path] = None) -> List[FirmwareChoice]:
    root = (root or default_firmware_root()).resolve()
    catalog = load_catalog(root)
    if catalog:
        return catalog_firmware_choices(catalog, root)

    return discover_local_firmware_choices(root)


def discover_local_firmware_choices(root: Optional[Path] = None) -> List[FirmwareChoice]:
    root = (root or default_firmware_root()).resolve()
    sources: List[FirmwareSource] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or _should_skip_discovered_path(path):
            continue

        suffix = path.suffix.lower()
        if suffix == ".uf2":
            sources.append(FirmwareSource(path.resolve()))
            continue

        if suffix == ".zip":
            try:
                entries = list_uf2_entries(path)
            except zipfile.BadZipFile:
                continue
            for entry in entries:
                sources.append(FirmwareSource(path.resolve(), entry))

    choices = [FirmwareChoice(_firmware_choice_label(source, root), source) for source in sources]
    return _dedupe_firmware_choices(sorted(choices, key=_firmware_choice_sort_key))


def catalog_path(root: Optional[Path] = None) -> Path:
    return (root or default_firmware_root()).resolve() / CATALOG_FILE


def load_catalog(root: Optional[Path] = None) -> List[CatalogItem]:
    path = catalog_path(root)
    if not path.exists():
        return builtin_catalog_items()

    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    items: List[CatalogItem] = []
    for raw in data.get("items", []):
        items.append(
            CatalogItem(
                item_id=raw["id"],
                label=raw["label"],
                install_method=raw.get("install_method", "rp2040"),
                file_type=raw.get("file_type", "uf2"),
                controller_check=bool(raw.get("controller_check", False)),
                sources=tuple(raw.get("sources", [])),
                local_paths=tuple(raw.get("local_paths", [])),
                hardware_check=raw.get("hardware_check"),
                pre_flash_bootloader=raw.get("pre_flash_bootloader"),
                post_flash_check=raw.get("post_flash_check"),
                notes=raw.get("notes", ""),
                expected_uf2_family=parse_optional_int(raw.get("expected_uf2_family")),
            )
        )
    return items


def catalog_cache_root(root: Optional[Path] = None) -> Path:
    root = (root or default_firmware_root()).resolve()
    try:
        with catalog_path(root).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        cache_dir = data.get("cache_dir", "firmware-cache")
    except OSError:
        cache_dir = "firmware-cache"
    return root / cache_dir


def catalog_item_cache_dir(item: CatalogItem, root: Optional[Path] = None) -> Path:
    return catalog_cache_root(root) / item.item_id


def catalog_firmware_choices(items: Sequence[CatalogItem], root: Optional[Path] = None) -> List[FirmwareChoice]:
    root = (root or default_firmware_root()).resolve()
    choices: List[FirmwareChoice] = []
    for item in items:
        source, status = find_catalog_source(item, root)
        choices.append(
            FirmwareChoice(
                label=item.label,
                source=source,
                item_id=item.item_id,
                install_method=item.install_method,
                controller_check=item.controller_check,
                hardware_check=item.hardware_check,
                pre_flash_bootloader=item.pre_flash_bootloader,
                post_flash_check=item.post_flash_check,
                status=status,
            )
        )
    return choices


def get_catalog_item(item_id: str, root: Optional[Path] = None) -> CatalogItem:
    for item in load_catalog(root):
        if item.item_id == item_id:
            return item
    for item in builtin_catalog_items():
        if item.item_id == item_id:
            return item
    raise FirmwareError(f"Unknown catalog firmware: {item_id}")


def builtin_catalog_items() -> List[CatalogItem]:
    return [
        CatalogItem(
            item_id="reflex-prism",
            label="Reflex Prism",
            install_method="rp2040",
            file_type="uf2",
            controller_check=False,
            sources=(
                {
                    "type": "github_repo_latest_semver_file",
                    "repo": "misteraddons/firmware",
                    "ref": "main",
                    "directory": "reflex-prism",
                    "file_name": "prism_dac.uf2",
                },
            ),
            local_paths=("reflex-prism/v1.10.10/prism_dac.uf2",),
            hardware_check=PRISM_HARDWARE_CHECK,
            pre_flash_bootloader=PRISM_PRE_FLASH_BOOTLOADER,
            post_flash_check=PRISM_POST_FLASH_CHECK,
            expected_uf2_family=RP2040_UF2_FAMILY_ID,
        )
    ]


def find_catalog_source(item: CatalogItem, root: Optional[Path] = None) -> Tuple[Optional[FirmwareSource], str]:
    root = (root or default_firmware_root()).resolve()
    versions = catalog_firmware_versions(item, root)
    if versions:
        selected = versions[0]
        return selected.source, selected.status

    if item.install_method == "coming_soon":
        return None, "coming soon"
    if item.sources:
        return None, "download required"
    return None, "missing"


def find_cached_catalog_file(item: CatalogItem, root: Optional[Path] = None) -> Optional[Path]:
    cache_dir = catalog_item_cache_dir(item, root)
    if not cache_dir.is_dir():
        return None

    candidates = [path for path in cache_dir.rglob("*") if path.is_file()]
    if item.file_type == "uf2":
        candidates = [path for path in candidates if path.suffix.lower() == ".uf2"]
    elif item.file_type == "package":
        candidates = [path for path in candidates if path.suffix.lower() in {".zip", ".gz", ".tgz", ".bin", ".hex"}]

    if not candidates:
        return None
    firmware_root = (root or default_firmware_root()).resolve()
    return max(
        candidates,
        key=lambda path: (
            semver_key(_catalog_version_from_path(item, path, firmware_root)),
            path.stat().st_mtime,
        ),
    )


def _download_catalog_plan(
    item: CatalogItem,
    plan: DownloadPlan,
    root: Path,
    *,
    force_download: bool,
    log: Optional[Callable[[str], None]],
) -> FirmwareSource:
    version_dir = safe_file_name(_cache_version_for_plan(item, plan))
    target = catalog_item_cache_dir(item, root) / version_dir / safe_file_name(plan.file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if force_download or not target.exists():
        if log:
            log(f"Downloading {item.label} from {plan.source_label}: {plan.file_name}")
        download_file(
            plan.url,
            target,
            validate=lambda path: validate_catalog_firmware(item, FirmwareSource(path)),
        )
    firmware = FirmwareSource(target)
    validate_catalog_firmware(item, firmware)
    return firmware


def ensure_catalog_firmware(
    item_id: str,
    root: Optional[Path] = None,
    force_download: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> FirmwareSource:
    root = (root or default_firmware_root()).resolve()
    item = get_catalog_item(item_id, root)

    if item.install_method == "coming_soon":
        raise FirmwareError(f"{item.label} firmware is coming soon.")

    if not force_download:
        existing, _status = find_catalog_source(item, root)
        if existing:
            validate_catalog_firmware(item, existing)
            return existing

    if not item.sources:
        raise FirmwareError(f"No download source configured for {item.label}.")

    last_error: Optional[Exception] = None
    for source in item.sources:
        try:
            plan = resolve_download_plan(source)
            return _download_catalog_plan(
                item,
                plan,
                root,
                force_download=force_download,
                log=log,
            )
        except Exception as error:
            last_error = error
            if log:
                log(f"Download source failed for {item.label}: {error}")

    local, status = find_catalog_source(item, root)
    if local:
        validate_catalog_firmware(item, local)
        if log:
            log(f"Using {status} firmware because download failed.")
        return local

    if last_error:
        raise FirmwareError(f"Could not download {item.label}: {last_error}")
    raise FirmwareError(f"Could not find firmware for {item.label}.")


def refresh_catalog_firmware(
    item_id: str,
    root: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> FirmwareSource:
    root = (root or default_firmware_root()).resolve()
    item = get_catalog_item(item_id, root)
    if item.install_method == "coming_soon":
        raise FirmwareError(f"{item.label} firmware is coming soon.")

    last_error: Optional[Exception] = None
    for source in item.sources:
        try:
            plan = resolve_download_plan(source)
            if plan.immutable_version:
                for version in catalog_firmware_versions(item, root):
                    if version.version.casefold() == plan.version.casefold():
                        validate_catalog_firmware(item, version.source)
                        if log:
                            log(f"{item.label} is current ({version.version}).")
                        return version.source
            return _download_catalog_plan(
                item,
                plan,
                root,
                force_download=not plan.immutable_version,
                log=log,
            )
        except Exception as error:
            last_error = error
            if log:
                log(f"Update check failed for {item.label}: {error}")

    local, status = find_catalog_source(item, root)
    if local:
        validate_catalog_firmware(item, local)
        if log:
            log(f"Using {status} firmware for {item.label}.")
        return local
    if last_error:
        raise FirmwareError(f"Could not refresh {item.label}: {last_error}")
    raise FirmwareError(f"No update source configured for {item.label}.")


def refresh_all_catalog_firmware(
    root: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
    max_workers: int = 4,
) -> Tuple[Dict[str, FirmwareSource], Dict[str, str]]:
    root = (root or default_firmware_root()).resolve()
    items = [item for item in load_catalog(root) if item.install_method != "coming_soon"]
    refreshed: Dict[str, FirmwareSource] = {}
    errors: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items) or 1))) as executor:
        futures = {
            executor.submit(refresh_catalog_firmware, item.item_id, root, log): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                refreshed[item.item_id] = future.result()
            except Exception as error:
                errors[item.item_id] = str(error)
                if log:
                    log(f"Could not refresh {item.label}: {error}")
    return refreshed, errors


def resolve_download_plan(source: dict) -> DownloadPlan:
    source_type = source.get("type")
    if source_type == "github_release_asset":
        return resolve_github_release_asset(source)
    if source_type == "github_repo_file":
        return resolve_github_repo_file(source)
    if source_type == "github_repo_latest_semver_file":
        return resolve_github_latest_semver_file(source)
    if source_type == "url":
        url = source["url"]
        return DownloadPlan(
            url=url,
            file_name=source.get("file_name") or Path(url).name,
            source_label=url,
            version=str(source.get("version") or "latest"),
        )
    raise FirmwareError(f"Unsupported source type: {source_type}")


def validate_catalog_firmware(item: CatalogItem, source: FirmwareSource) -> None:
    if item.file_type == "uf2":
        validate_firmware_source(source, item.expected_uf2_family)
        return
    if source.path.stat().st_size <= 0:
        raise FirmwareError(f"{source.display_name} is empty.")
    if source.path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source.path) as archive:
                bad_entry = archive.testzip()
        except zipfile.BadZipFile as error:
            raise FirmwareError(f"{source.display_name} is not a valid ZIP package.") from error
        if bad_entry:
            raise FirmwareError(f"{source.display_name} contains a corrupt ZIP entry: {bad_entry}")


def resolve_github_release_asset(source: dict) -> DownloadPlan:
    repo = source["repo"]
    release = source.get("release", "latest")
    if release == "latest":
        release_url = f"{GITHUB_API}/{repo}/releases/latest"
    else:
        release_url = f"{GITHUB_API}/{repo}/releases/tags/{release}"

    release_data = request_json(release_url)
    asset_regex = re.compile(source["asset_regex"], re.IGNORECASE)
    matches = [asset for asset in release_data.get("assets", []) if asset_regex.match(asset.get("name", ""))]
    if not matches:
        raise FirmwareError(f"No release asset matched {source['asset_regex']} in {repo} {release_data.get('tag_name', release)}")

    asset = sorted(matches, key=lambda item: item.get("name", ""))[0]
    return DownloadPlan(
        url=asset["browser_download_url"],
        file_name=asset["name"],
        source_label=f"{repo} {release_data.get('tag_name', release)}",
        version=str(release_data.get("tag_name") or release),
        immutable_version=True,
    )


def resolve_github_repo_file(source: dict) -> DownloadPlan:
    repo = source["repo"]
    ref = source.get("ref", "main")
    file_path = source["path"].strip("/")
    api_url = f"{GITHUB_API}/{repo}/contents/{file_path}?ref={ref}"
    data = request_json(api_url)
    if data.get("type") != "file" or not data.get("download_url"):
        raise FirmwareError(f"GitHub path is not a downloadable file: {repo}/{file_path}")
    return DownloadPlan(
        url=data["download_url"],
        file_name=data["name"],
        source_label=f"{repo}/{file_path}",
        version=str(ref),
    )


def resolve_github_latest_semver_file(source: dict) -> DownloadPlan:
    repo = source["repo"]
    ref = source.get("ref", "main")
    directory = source["directory"].strip("/")
    file_name = source["file_name"]
    api_url = f"{GITHUB_API}/{repo}/contents/{directory}?ref={ref}"
    entries = ensure_list(request_json(api_url))
    dirs = [entry for entry in entries if entry.get("type") == "dir"]
    if not dirs:
        raise FirmwareError(f"No version directories found in {repo}/{directory}")

    latest = sorted(dirs, key=lambda entry: semver_key(entry.get("name", "")), reverse=True)[0]
    file_api_url = f"{GITHUB_API}/{repo}/contents/{latest['path'].strip('/')}/{file_name}?ref={ref}"
    data = request_json(file_api_url)
    if data.get("type") != "file" or not data.get("download_url"):
        raise FirmwareError(f"Latest version does not contain {file_name}: {latest.get('name')}")
    return DownloadPlan(
        url=data["download_url"],
        file_name=data["name"],
        source_label=f"{repo}/{latest.get('path')}",
        version=str(latest.get("name") or "latest"),
        immutable_version=True,
    )


def request_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(
    url: str,
    target: Path,
    validate: Optional[Callable[[Path], None]] = None,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temp = target.with_name(f"{target.stem}.tmp{target.suffix}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with temp.open("wb") as stream:
                shutil.copyfileobj(response, stream, COPY_BUFFER_SIZE)
        if validate:
            validate(temp)
        temp.replace(target)
    except Exception:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def ensure_list(value: object) -> List[dict]:
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def semver_key(value: str) -> Tuple[int, ...]:
    match = re.fullmatch(r"prism-v1-r(\d+)", value.lower())
    if match:
        # Legacy release-series tags were not firmware versions. Keep their
        # historical order, but rank them below the replacement v1.11+
        # semantic firmware tags.
        return (0, 1, 10, max(0, int(match.group(1)) - 1), 1)
    numbers = [int(part) for part in re.findall(r"\d+", value)]
    return (0, *(numbers or [0]))


def safe_file_name(value: str) -> str:
    name = Path(value).name
    return re.sub(r"[^A-Za-z0-9._+ -]", "_", name)


def parse_optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise FirmwareError(f"Invalid integer value in catalog: {value!r}")


def validate_firmware_source(source: FirmwareSource, expected_family: Optional[int] = None) -> None:
    if source.copy_name.lower().endswith(".uf2"):
        with source.open_binary() as stream:
            validate_uf2_stream(stream, source.display_name, expected_family)


def validate_uf2_stream(stream: BinaryIO, label: str, expected_family: Optional[int] = None) -> None:
    expected_blocks: Optional[int] = None
    seen_blocks: Set[int] = set()
    family_ids: Set[int] = set()
    block_count = 0

    while True:
        block = stream.read(UF2_BLOCK_SIZE)
        if not block:
            break
        block_count += 1
        if len(block) != UF2_BLOCK_SIZE:
            raise FirmwareError(f"{label} is not a valid UF2: partial block at #{block_count}.")

        start0 = int.from_bytes(block[0:4], "little")
        start1 = int.from_bytes(block[4:8], "little")
        flags = int.from_bytes(block[8:12], "little")
        payload_size = int.from_bytes(block[16:20], "little")
        block_no = int.from_bytes(block[20:24], "little")
        num_blocks = int.from_bytes(block[24:28], "little")
        family_or_size = int.from_bytes(block[28:32], "little")
        end_magic = int.from_bytes(block[508:512], "little")

        if start0 != UF2_MAGIC_START0 or start1 != UF2_MAGIC_START1 or end_magic != UF2_MAGIC_END:
            raise FirmwareError(f"{label} is not a valid UF2: bad magic in block #{block_count}.")
        if payload_size <= 0 or payload_size > 476:
            raise FirmwareError(f"{label} is not a valid UF2: invalid payload size {payload_size}.")
        if num_blocks <= 0 or block_no >= num_blocks:
            raise FirmwareError(f"{label} is not a valid UF2: invalid block numbering.")
        if expected_blocks is None:
            expected_blocks = num_blocks
        elif expected_blocks != num_blocks:
            raise FirmwareError(f"{label} is not a valid UF2: inconsistent block count.")

        seen_blocks.add(block_no)
        if flags & UF2_FLAG_FAMILY_ID_PRESENT:
            family_ids.add(family_or_size)

    if block_count == 0:
        raise FirmwareError(f"{label} is empty; expected UF2 firmware.")
    if expected_blocks is not None and len(seen_blocks) != expected_blocks:
        raise FirmwareError(
            f"{label} is not a valid UF2: expected {expected_blocks} blocks, found {len(seen_blocks)}."
        )
    if expected_family is not None:
        if not family_ids:
            raise FirmwareError(f"{label} is missing a UF2 family ID; expected 0x{expected_family:08X}.")
        if expected_family not in family_ids:
            actual = ", ".join(f"0x{family_id:08X}" for family_id in sorted(family_ids))
            raise FirmwareError(
                f"{label} has UF2 family {actual}; expected 0x{expected_family:08X}."
            )


def _should_skip_discovered_path(path: Path) -> bool:
    return any(part.lower() in SKIP_DISCOVERY_DIRS for part in path.parts)


def _firmware_choice_label(source: FirmwareSource, root: Path) -> str:
    try:
        relative = source.path.relative_to(root)
        parts = relative.parts
    except ValueError:
        return source.display_name

    project = PROJECT_LABELS.get(parts[0].lower(), _title_from_slug(parts[0])) if parts else "Firmware"
    version = _find_version_label(parts[1:-1])
    firmware_name = source.copy_name
    if firmware_name.lower() == "flash_nuke.uf2":
        firmware_name = "flash_nuke.uf2 (erase/reset)"

    prefix = f"{project} {version}" if version else project
    return f"{prefix} - {firmware_name}"


def _find_version_label(parts: Sequence[str]) -> str:
    for part in parts:
        lower = part.lower()
        if (
            re.match(r"^v?\d", lower)
            or re.match(r"^main-\d{4}$", lower)
            or re.match(r"^prism-v\d+-r\d+$", lower)
            or lower in {"main", "latest"}
        ):
            return part
    return ""


def firmware_source_key(source: FirmwareSource) -> str:
    return f"{source.path.resolve()}::{source.zip_member or ''}"


def firmware_source_digest(source: FirmwareSource) -> str:
    digest = hashlib.sha256()
    with source.open_binary() as stream:
        while True:
            chunk = stream.read(COPY_BUFFER_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_local_version(item: CatalogItem) -> str:
    for local_path in item.local_paths:
        local_version = _find_version_label(Path(local_path).parts[1:-1])
        if local_version:
            return local_version
    return ""


def _cache_version_for_plan(item: CatalogItem, plan: DownloadPlan) -> str:
    version = plan.version or "latest"
    if plan.immutable_version or version.casefold() not in {"main", "master", "latest"}:
        return version
    return _catalog_local_version(item) or version


def _catalog_candidate_matches(item: CatalogItem, path: Path) -> bool:
    if not path.is_file() or path.name.lower().endswith(".tmp"):
        return False
    if item.file_type == "uf2":
        return path.suffix.lower() == ".uf2"
    if item.file_type == "package":
        return path.suffix.lower() in {".zip", ".gz", ".tgz", ".bin", ".hex"}
    return True


def _catalog_candidate_paths(item: CatalogItem, root: Path) -> List[Tuple[Path, str]]:
    candidates: Dict[Path, str] = {}
    for local_path in item.local_paths:
        relative = Path(local_path)
        explicit = (root / relative).resolve()
        if _catalog_candidate_matches(item, explicit):
            candidates.setdefault(explicit, "bundled")

        if not relative.parts:
            continue
        project_root = (root / relative.parts[0]).resolve()
        if not project_root.is_dir():
            continue
        for candidate in project_root.rglob(relative.name):
            resolved = candidate.resolve()
            if _catalog_candidate_matches(item, resolved):
                candidates.setdefault(resolved, "bundled")

    cache_dir = catalog_item_cache_dir(item, root)
    if cache_dir.is_dir():
        for candidate in cache_dir.rglob("*"):
            resolved = candidate.resolve()
            if _catalog_candidate_matches(item, resolved):
                candidates.setdefault(resolved, "cached")
    return list(candidates.items())


def _catalog_version_from_path(item: CatalogItem, path: Path, root: Path) -> str:
    cache_dir = catalog_item_cache_dir(item, root).resolve()
    try:
        cache_relative = path.resolve().relative_to(cache_dir)
        if len(cache_relative.parts) > 1:
            return cache_relative.parts[0]
    except ValueError:
        pass

    try:
        relative = path.resolve().relative_to(root.resolve())
        version = _find_version_label(relative.parts[1:-1])
        if version:
            return version
    except ValueError:
        pass

    match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)*)(?!\d)", path.name)
    if match:
        return f"v{match.group(1)}"
    return "current"


def _hardware_check_for_group(check: dict, expected_group: str) -> dict:
    targets = list(check.get("accepted_targets", []) or []) + list(check.get("known_mismatches", []) or [])
    expected = next((target for target in targets if target.get("group") == expected_group), None)
    if expected is None:
        return check

    narrowed = dict(check)
    narrowed.pop("accepted_targets", None)
    narrowed["expected_group"] = expected_group
    narrowed["expected_label"] = str(expected.get("label") or expected_group)
    narrowed["expect"] = list(expected.get("markers", []) or [])
    narrowed["known_mismatches"] = [
        target for target in targets if target.get("group") != expected_group
    ]
    return narrowed


def catalog_hardware_check_for_version(item: CatalogItem, version: str) -> Optional[dict]:
    check = item.hardware_check
    if not check or item.item_id != "reflex-prism":
        return check

    lower = version.casefold()
    if re.match(r"^v?1\.10(?:\.|$)", lower) or re.match(r"^v?1\.1(?:\.|$)", lower):
        return _hardware_check_for_group(check, "prism-v11")
    if re.match(r"^v?1\.20(?:\.|$)", lower) or re.match(r"^v?1\.2(?:\.|$)", lower):
        return _hardware_check_for_group(check, "prism-v12")
    if re.match(r"^v?1\.30(?:\.|$)", lower) or re.match(r"^v?1\.3(?:\.|$)", lower):
        return _hardware_check_for_group(check, "prism-v13")
    return check


def catalog_firmware_versions(item: CatalogItem, root: Optional[Path] = None) -> List[FirmwareVersion]:
    root = (root or default_firmware_root()).resolve()
    by_digest: Dict[str, FirmwareVersion] = {}
    candidates = sorted(
        _catalog_candidate_paths(item, root),
        key=lambda entry: (
            0 if entry[1] == "bundled" else 1,
            0 if entry[1] == "bundled" else -entry[0].stat().st_mtime,
        ),
    )
    for path, status in candidates:
        source = FirmwareSource(path)
        version = _catalog_version_from_path(item, path, root)
        if status == "cached" and version.casefold() in {"main", "master", "latest"}:
            version = _catalog_local_version(item) or version
        candidate = FirmwareVersion(
            version=version,
            source=source,
            status=status,
            hardware_check=catalog_hardware_check_for_version(item, version),
        )
        if version == "current" and status == "cached" and item.hardware_check:
            continue
        digest_key = f"{firmware_source_digest(source)}:{version.casefold()}"
        existing_digest = by_digest.get(digest_key)
        if existing_digest:
            best_version = max(
                (existing_digest.version, candidate.version),
                key=lambda value: (semver_key(value), value.casefold()),
            )
            preferred = (
                candidate
                if existing_digest.status == "cached" and candidate.status == "bundled"
                else existing_digest
            )
            by_digest[digest_key] = FirmwareVersion(
                version=best_version,
                source=preferred.source,
                status=preferred.status,
                hardware_check=catalog_hardware_check_for_version(item, best_version),
            )
            continue
        by_digest[digest_key] = candidate

    versions: Dict[str, FirmwareVersion] = {}
    for candidate in by_digest.values():
        version = candidate.version
        key = version.casefold()
        existing = versions.get(key)
        if existing is None or (existing.status == "bundled" and candidate.status == "cached"):
            versions[key] = candidate

    return sorted(
        versions.values(),
        key=lambda entry: (semver_key(entry.version), entry.version.casefold()),
        reverse=True,
    )


def select_catalog_firmware_version(
    item: CatalogItem,
    requested_version: Optional[str] = None,
    root: Optional[Path] = None,
) -> FirmwareVersion:
    versions = catalog_firmware_versions(item, root)
    if not versions:
        raise FirmwareError(f"No firmware versions are available for {item.label}.")
    if not requested_version or requested_version.casefold() == "latest":
        return versions[0]
    for version in versions:
        if version.version.casefold() == requested_version.casefold():
            return version
    available = ", ".join(version.version for version in versions)
    raise FirmwareError(f"Unknown {item.label} firmware version {requested_version!r}. Available: {available}")


def _title_from_slug(value: str) -> str:
    words = re.split(r"[-_]+", value)
    return " ".join(word.capitalize() for word in words if word) or value


def _firmware_choice_sort_key(choice: FirmwareChoice) -> Tuple[str, int, str]:
    label = choice.label.lower()
    erase = 1 if "flash_nuke" in label else 0
    return (label.replace("flash_nuke", "zz_flash_nuke"), erase, label)


def _dedupe_firmware_choices(choices: Sequence[FirmwareChoice]) -> List[FirmwareChoice]:
    seen_labels: Set[str] = set()
    deduped: List[FirmwareChoice] = []
    for choice in choices:
        label = choice.label
        if label in seen_labels:
            label = f"{choice.label} ({choice.source.display_name})"
        seen_labels.add(label)
        deduped.append(FirmwareChoice(label, choice.source))
    return deduped


def is_controller_firmware(source: FirmwareSource) -> bool:
    if source.copy_name.lower() == "flash_nuke.uf2":
        return False
    text = f"{source.path} {source.zip_member or ''}".lower()
    return any(keyword in text for keyword in CONTROLLER_FIRMWARE_KEYWORDS)


def _normalize_mount_path(path: Path) -> str:
    text = str(path)
    if os.name == "nt":
        return text.rstrip("\\/").lower()
    return text.rstrip("/")


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _looks_like_rpi_rp2(path: Path, label: str = "", trust_name: bool = False) -> bool:
    label_upper = label.upper()
    if label_upper == RPI_RP2_LABEL or label_upper.startswith(f"{RPI_RP2_LABEL} "):
        return True

    name_upper = path.name.upper()
    if trust_name and (name_upper == RPI_RP2_LABEL or name_upper.startswith(f"{RPI_RP2_LABEL} ")):
        return True

    # RP2040 bootloader volumes normally expose both files.
    has_info = _safe_is_file(path / "INFO_UF2.TXT")
    has_index = _safe_is_file(path / "INDEX.HTM") or _safe_is_file(path / "INDEX.HTML")
    return has_info and has_index


def _windows_rpi_rp2_mounts() -> List[Mount]:
    mounts: List[Mount] = []
    kernel32 = ctypes.windll.kernel32
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    bitmask = kernel32.GetLogicalDrives()

    for index in range(26):
        if not bitmask & (1 << index):
            continue

        root = f"{chr(ord('A') + index)}:\\"
        drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if drive_type not in (DRIVE_REMOVABLE, DRIVE_FIXED):
            continue

        label_buffer = ctypes.create_unicode_buffer(261)
        filesystem_buffer = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong()
        max_component = ctypes.c_ulong()
        flags = ctypes.c_ulong()

        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            label_buffer,
            len(label_buffer),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            filesystem_buffer,
            len(filesystem_buffer),
        )
        label = label_buffer.value if ok else ""
        path = Path(root)
        if _looks_like_rpi_rp2(path, label=label, trust_name=False):
            mounts.append(Mount(path, label or RPI_RP2_LABEL))

    return mounts


def _unescape_proc_mount(path: str) -> str:
    return (
        path.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _posix_mount_candidates() -> Iterator[Tuple[Path, bool]]:
    proc_mounts = Path("/proc/mounts")
    if proc_mounts.exists():
        try:
            with proc_mounts.open("r", encoding="utf-8", errors="replace") as mounts_file:
                for line in mounts_file:
                    fields = line.split()
                    if len(fields) >= 2:
                        yield Path(_unescape_proc_mount(fields[1])), True
        except OSError:
            pass

    volumes = Path("/Volumes")
    if volumes.is_dir():
        try:
            for child in volumes.iterdir():
                yield child, True
        except OSError:
            pass

    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    roots = [Path("/media"), Path("/mnt")]
    if user:
        roots.insert(0, Path("/media") / user)
        roots.insert(1, Path("/run/media") / user)

    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                yield child, False
        except OSError:
            continue


def find_rpi_rp2_mounts() -> List[Mount]:
    if os.name == "nt":
        return _dedupe_mounts(_windows_rpi_rp2_mounts())

    mounts: List[Mount] = []
    for path, trust_name in _posix_mount_candidates():
        if _looks_like_rpi_rp2(path, trust_name=trust_name):
            mounts.append(Mount(path, path.name or RPI_RP2_LABEL))
    return _dedupe_mounts(mounts)


def _dedupe_mounts(mounts: Iterable[Mount]) -> List[Mount]:
    seen: Set[str] = set()
    deduped: List[Mount] = []
    for mount in mounts:
        key = _normalize_mount_path(mount.path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mount)
    return deduped


def copy_firmware_to_mount(source: FirmwareSource, mount: Mount) -> Path:
    # The RP2040 bootloader's tiny FAT volume can reject long filenames on
    # Windows. The filename is irrelevant to flashing; the UF2 payload matters.
    target = mount.path / UF2_TARGET_NAME
    try:
        with source.open_binary() as src:
            with target.open("wb") as dst:
                shutil.copyfileobj(src, dst, COPY_BUFFER_SIZE)
                if os.name != "nt":
                    dst.flush()
                    os.fsync(dst.fileno())
    except OSError:
        if os.name == "nt" and not _looks_like_rpi_rp2(mount.path, label=mount.label):
            return target
        raise

    if os.name != "nt":
        try:
            dir_fd = os.open(str(mount.path), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    return target


def _raise_if_stopped(stop_event: threading.Event) -> None:
    if stop_event.is_set():
        raise InstallerStopped()


def wait_for_rpi_rp2(stop_event: threading.Event, poll_seconds: float = 1.0) -> List[Mount]:
    while True:
        _raise_if_stopped(stop_event)
        mounts = find_rpi_rp2_mounts()
        if mounts:
            return mounts
        stop_event.wait(poll_seconds)


def wait_for_rpi_rp2_timeout(
    stop_event: threading.Event,
    *,
    timeout_s: float,
    poll_seconds: float = 0.25,
) -> List[Mount]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _raise_if_stopped(stop_event)
        mounts = find_rpi_rp2_mounts()
        if mounts:
            return mounts
        stop_event.wait(poll_seconds)
    raise FirmwareError("Timed out waiting for RPI-RP2 after serial bootloader command.")


def wait_for_detach(
    mounts: Sequence[Mount],
    stop_event: threading.Event,
    poll_seconds: float = 1.0,
) -> None:
    wanted = {_normalize_mount_path(mount.path) for mount in mounts}
    while True:
        _raise_if_stopped(stop_event)
        current = {_normalize_mount_path(mount.path) for mount in find_rpi_rp2_mounts()}
        if not wanted.intersection(current):
            return
        stop_event.wait(poll_seconds)


def list_game_controllers() -> Set[Controller]:
    system = platform.system().lower()
    if system == "windows":
        return _windows_game_controllers()
    if system == "linux":
        return _linux_game_controllers()
    if system == "darwin":
        return _mac_game_controllers()
    return set()


def _windows_game_controllers() -> Set[Controller]:
    controllers: Set[Controller] = set()
    controllers.update(_windows_winmm_controllers())
    controllers.update(_windows_xinput_controllers())
    controllers.update(_windows_pnp_controller_candidates())
    return controllers


def _windows_winmm_controllers() -> Set[Controller]:
    controllers: Set[Controller] = set()

    try:
        winmm = ctypes.WinDLL("winmm")
    except OSError:
        return controllers

    class JOYCAPSW(ctypes.Structure):
        _fields_ = [
            ("wMid", ctypes.c_ushort),
            ("wPid", ctypes.c_ushort),
            ("szPname", ctypes.c_wchar * 32),
            ("wXmin", ctypes.c_uint),
            ("wXmax", ctypes.c_uint),
            ("wYmin", ctypes.c_uint),
            ("wYmax", ctypes.c_uint),
            ("wZmin", ctypes.c_uint),
            ("wZmax", ctypes.c_uint),
            ("wNumButtons", ctypes.c_uint),
            ("wPeriodMin", ctypes.c_uint),
            ("wPeriodMax", ctypes.c_uint),
            ("wRmin", ctypes.c_uint),
            ("wRmax", ctypes.c_uint),
            ("wUmin", ctypes.c_uint),
            ("wUmax", ctypes.c_uint),
            ("wVmin", ctypes.c_uint),
            ("wVmax", ctypes.c_uint),
            ("wCaps", ctypes.c_uint),
            ("wMaxAxes", ctypes.c_uint),
            ("wNumAxes", ctypes.c_uint),
            ("wMaxButtons", ctypes.c_uint),
            ("szRegKey", ctypes.c_wchar * 32),
            ("szOEMVxD", ctypes.c_wchar * 260),
        ]

    winmm.joyGetNumDevs.restype = ctypes.c_uint
    winmm.joyGetDevCapsW.argtypes = [ctypes.c_uint, ctypes.POINTER(JOYCAPSW), ctypes.c_uint]
    winmm.joyGetDevCapsW.restype = ctypes.c_uint

    try:
        count = int(winmm.joyGetNumDevs())
    except OSError:
        return controllers

    for index in range(count):
        caps = JOYCAPSW()
        result = winmm.joyGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps))
        if result != 0:
            continue

        name = caps.szPname or f"Joystick {index + 1}"
        identity = f"winmm:{index}:{caps.wMid}:{caps.wPid}:{name}"
        controllers.add(Controller(identity, name))

    return controllers


def _windows_xinput_controllers() -> Set[Controller]:
    controllers: Set[Controller] = set()

    dll = None
    for dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = ctypes.WinDLL(dll_name)
            break
        except OSError:
            continue
    if dll is None:
        return controllers

    class XINPUT_GAMEPAD(ctypes.Structure):
        _fields_ = [
            ("wButtons", ctypes.c_ushort),
            ("bLeftTrigger", ctypes.c_ubyte),
            ("bRightTrigger", ctypes.c_ubyte),
            ("sThumbLX", ctypes.c_short),
            ("sThumbLY", ctypes.c_short),
            ("sThumbRX", ctypes.c_short),
            ("sThumbRY", ctypes.c_short),
        ]

    class XINPUT_STATE(ctypes.Structure):
        _fields_ = [
            ("dwPacketNumber", ctypes.c_ulong),
            ("Gamepad", XINPUT_GAMEPAD),
        ]

    dll.XInputGetState.argtypes = [ctypes.c_uint, ctypes.POINTER(XINPUT_STATE)]
    dll.XInputGetState.restype = ctypes.c_uint

    for index in range(4):
        state = XINPUT_STATE()
        if dll.XInputGetState(index, ctypes.byref(state)) == 0:
            name = f"XInput controller {index + 1}"
            controllers.add(Controller(f"xinput:{index}", name))

    return controllers


def _windows_pnp_controller_candidates() -> Set[Controller]:
    controllers: Set[Controller] = set()
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "$nameRx='(?i)(hid-compliant game controller|joystick|gp2040|mistercade|classic2usb|reflex[- ]adapt|d-input|xinput)'; "
            "$idRx='(?i)(VID_320F&PID_5044)'; "
            "Get-CimInstance Win32_PnPEntity | "
            "Where-Object { $_.Name -match $nameRx -or $_.DeviceID -match $idRx } | "
            "ForEach-Object { \"$($_.DeviceID)`t$($_.Name)\" }"
        ),
    ]

    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return controllers

    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        identity, name = line.split("\t", 1)
        name = name.strip()
        if name:
            controllers.add(Controller(f"pnp:{identity.strip()}", name))

    return controllers


def _linux_game_controllers() -> Set[Controller]:
    controllers: Set[Controller] = set()

    sys_class_input = Path("/sys/class/input")
    if sys_class_input.is_dir():
        for js_device in sorted(sys_class_input.glob("js*")):
            name_path = js_device / "device" / "name"
            try:
                name = name_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                name = js_device.name
            controllers.add(Controller(f"linux-js:{js_device.name}:{name}", name))

    proc_devices = Path("/proc/bus/input/devices")
    if proc_devices.exists():
        try:
            text = proc_devices.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for block in re.split(r"\n\s*\n", text):
            name_match = re.search(r'N:\s+Name="([^"]+)"', block)
            handlers_match = re.search(r"H:\s+Handlers=(.+)", block)
            name = name_match.group(1) if name_match else "Input device"
            handlers = handlers_match.group(1) if handlers_match else ""
            is_js = re.search(r"\bjs\d+\b", handlers) is not None
            is_named = _has_controller_keyword(name)
            if is_js or is_named:
                identity = f"linux-input:{handlers}:{name}"
                controllers.add(Controller(identity, name))

    return controllers


def _mac_game_controllers() -> Set[Controller]:
    controllers: Set[Controller] = set()
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-c", "IOHIDDevice", "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return controllers

    blocks = re.split(r"\n(?=\s*[| ]*\+-o\s)", result.stdout)
    for block in blocks:
        product_match = re.search(r'"Product"\s*=\s*"([^"]+)"', block)
        product = product_match.group(1) if product_match else "HID game controller"

        usage_pages = _ioreg_numbers(block, ("PrimaryUsagePage", "DeviceUsagePage"))
        usages = _ioreg_numbers(block, ("PrimaryUsage", "DeviceUsage"))
        usage_says_game_controller = bool(set(usage_pages).intersection({1}) and set(usages).intersection({4, 5}))

        if usage_says_game_controller or _has_controller_keyword(product):
            location_match = re.search(r'"LocationID"\s*=\s*(0x[0-9a-fA-F]+|\d+)', block)
            location = location_match.group(1) if location_match else product
            controllers.add(Controller(f"mac-hid:{location}:{product}", product))

    return controllers


def _ioreg_numbers(block: str, keys: Sequence[str]) -> List[int]:
    numbers: List[int] = []
    for key in keys:
        for match in re.finditer(rf'"{re.escape(key)}"\s*=\s*(0x[0-9a-fA-F]+|\d+)', block):
            raw = match.group(1)
            try:
                numbers.append(int(raw, 0))
            except ValueError:
                continue
    return numbers


def _has_controller_keyword(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in CONTROLLER_DEVICE_KEYWORDS)


def _has_controller_device_id_keyword(text: str) -> bool:
    upper = text.upper()
    return any(keyword.upper() in upper for keyword in CONTROLLER_DEVICE_ID_KEYWORDS)


def is_known_controller(controller: Controller) -> bool:
    lower_name = controller.name.lower()
    explicit_name = any(
        keyword in lower_name
        for keyword in ("gp2040", "mistercade", "classic2usb", "reflex", "d-input")
    )
    return explicit_name or _has_controller_device_id_keyword(controller.identity)


def wait_for_new_controller(
    before: Set[Controller],
    stop_event: threading.Event,
    log: Callable[[str], None],
    poll_seconds: float = 1.0,
) -> Set[Controller]:
    last_notice = time.monotonic()
    while True:
        _raise_if_stopped(stop_event)
        current = list_game_controllers()
        added = current - before
        if added:
            return added

        known = {controller for controller in current if is_known_controller(controller)}
        if known:
            return known

        now = time.monotonic()
        if now - last_notice >= 10:
            log("Still waiting for a controller/gamepad to enumerate...")
            last_notice = now
        stop_event.wait(poll_seconds)


def format_mounts(mounts: Sequence[Mount]) -> str:
    return ", ".join(str(mount.path) for mount in mounts)


def format_controllers(controllers: Iterable[Controller]) -> str:
    names: Set[str] = set()
    for controller in controllers:
        if _has_controller_device_id_keyword(controller.identity):
            names.add("GP2040-CE device")
        else:
            names.add(controller.name)
    names = sorted(names)
    return ", ".join(names) if names else "unknown controller"


def _parse_config_int(value: object, field: str, context: str) -> int:
    parsed = parse_optional_int(value)
    if parsed is None:
        raise FirmwareError(f"{context} is missing {field}.")
    return parsed


def _parse_post_flash_int(value: object, field: str) -> int:
    return _parse_config_int(value, field, "Post-flash check")


def _parse_pre_flash_int(value: object, field: str) -> int:
    return _parse_config_int(value, field, "Pre-flash bootloader command")


def _require_pyserial():
    try:
        import serial  # type: ignore
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise FirmwareError(
            "Serial bootloader and post-flash checks require pyserial. "
            "Install it with: python -m pip install pyserial"
        ) from exc
    return serial, list_ports


def find_serial_vid_pid(vid: int, pid: int) -> Optional[str]:
    _serial, list_ports = _require_pyserial()
    for port in list_ports.comports():
        if port.vid == vid and port.pid == pid:
            return str(port.device)
    return None


def list_connected_serial_vid_pids() -> Set[Tuple[int, int]]:
    _serial, list_ports = _require_pyserial()
    devices: Set[Tuple[int, int]] = set()
    for port in list_ports.comports():
        if port.vid is None or port.pid is None:
            continue
        devices.add((int(port.vid), int(port.pid)))
    return devices


def firmware_choice_key(choice: FirmwareChoice) -> str:
    return choice.item_id or choice.label


def firmware_choice_vid_pid(choice: FirmwareChoice) -> Optional[Tuple[int, int]]:
    for config in (choice.pre_flash_bootloader, choice.post_flash_check):
        if not config:
            continue
        vid = parse_optional_int(config.get("vid"))
        pid = parse_optional_int(config.get("pid"))
        if vid is not None and pid is not None:
            return vid, pid
    return None


def connected_firmware_choice_keys(
    choices: Sequence[FirmwareChoice],
    connected_vid_pids: Set[Tuple[int, int]],
) -> Set[str]:
    connected: Set[str] = set()
    for choice in choices:
        vid_pid = firmware_choice_vid_pid(choice)
        if vid_pid and vid_pid in connected_vid_pids:
            connected.add(firmware_choice_key(choice))
    return connected


def wait_for_serial_vid_pid(
    vid: int,
    pid: int,
    *,
    timeout_s: float,
    stop_event: threading.Event,
    log: Callable[[str], None],
    poll_seconds: float = 0.5,
) -> str:
    deadline = time.time() + timeout_s
    last_log = 0.0

    while time.time() < deadline:
        _raise_if_stopped(stop_event)
        port_name = find_serial_vid_pid(vid, pid)
        if port_name:
            return port_name

        now = time.time()
        if now - last_log >= 5.0:
            log(f"Waiting for USB serial VID:PID {vid:04X}:{pid:04X}...")
            last_log = now
        stop_event.wait(poll_seconds)

    raise FirmwareError(f"Timed out waiting for USB serial VID:PID {vid:04X}:{pid:04X}.")


def wait_for_serial_vid_pid_detach(
    vid: int,
    pid: int,
    *,
    stop_event: threading.Event,
    log: Callable[[str], None],
    poll_seconds: float = 0.5,
) -> None:
    last_log = 0.0
    while True:
        _raise_if_stopped(stop_event)
        port_name = find_serial_vid_pid(vid, pid)
        if not port_name:
            return

        now = time.time()
        if now - last_log >= 5.0:
            log(f"Waiting for USB serial VID:PID {vid:04X}:{pid:04X} to disconnect from {port_name}...")
            last_log = now
        stop_event.wait(poll_seconds)


def wait_for_flash_mounts(
    pre_flash_bootloader: Optional[dict],
    hardware_check: Optional[dict],
    stop_event: threading.Event,
    log: Callable[[str], None],
    poll_seconds: float = 1.0,
) -> List[Mount]:
    if not pre_flash_bootloader:
        return wait_for_rpi_rp2(stop_event, poll_seconds=poll_seconds)

    vid = _parse_pre_flash_int(pre_flash_bootloader.get("vid"), "vid")
    pid = _parse_pre_flash_int(pre_flash_bootloader.get("pid"), "pid")
    label = str(pre_flash_bootloader.get("label") or "serial bootloader command")
    timeout_s = float(pre_flash_bootloader.get("timeout", 30.0))
    last_log = 0.0

    while True:
        _raise_if_stopped(stop_event)
        mounts = find_rpi_rp2_mounts()
        if mounts:
            if hardware_check:
                raise FirmwareError(
                    "Hardware compatibility cannot be verified from an RPI-RP2 bootloader drive. "
                    "Connect the device normally so the installer can verify the hardware revision "
                    "before entering bootloader."
                )
            return mounts

        port_name = find_serial_vid_pid(vid, pid)
        if port_name:
            log(f"Found {label} on {port_name}.")
            if hardware_check:
                run_serial_hardware_check(hardware_check, port_name, stop_event=stop_event, log=log)
            log(f"Entering RP2040 bootloader on {port_name}.")
            run_serial_bootloader_command(pre_flash_bootloader, port_name, stop_event=stop_event, log=log)
            return wait_for_rpi_rp2_timeout(
                stop_event,
                timeout_s=timeout_s,
                poll_seconds=min(poll_seconds, 0.25),
            )

        now = time.time()
        if now - last_log >= 5.0:
            log(f"Waiting for RPI-RP2 drive or USB serial VID:PID {vid:04X}:{pid:04X}...")
            last_log = now
        stop_event.wait(poll_seconds)


def serial_read_until_idle(port, *, idle_s: float, timeout_s: float) -> str:
    start = time.time()
    last_rx = start
    chunks: List[str] = []

    while time.time() - start < timeout_s:
        waiting = port.in_waiting
        if waiting:
            chunks.append(port.read(waiting).decode("utf-8", errors="ignore"))
            last_rx = time.time()
        elif chunks and time.time() - last_rx >= idle_s:
            break
        time.sleep(0.05)

    return "".join(chunks)


def serial_send_command(port, command: str, *, timeout_s: float) -> str:
    serial_read_until_idle(port, idle_s=0.1, timeout_s=0.4)
    port.write((command + "\r\n").encode("utf-8"))
    port.flush()
    time.sleep(0.05)
    return serial_read_until_idle(port, idle_s=1.0, timeout_s=timeout_s)


def validate_serial_hardware_response(check: dict, response: str) -> None:
    expected_label = str(check.get("expected_label") or check.get("expected_group") or "selected firmware")
    accepted_targets = check.get("accepted_targets", []) or []
    for target in accepted_targets:
        markers = [str(marker) for marker in target.get("markers", []) if str(marker)]
        if markers and all(marker in response for marker in markers):
            return

    expected_markers = [str(marker) for marker in check.get("expect", []) if str(marker)]
    missing = [marker for marker in expected_markers if marker not in response]
    if not accepted_targets and not missing:
        return

    for mismatch in check.get("known_mismatches", []) or []:
        markers = [str(marker) for marker in mismatch.get("markers", []) if str(marker)]
        if markers and all(marker in response for marker in markers):
            connected_label = str(mismatch.get("label") or mismatch.get("group") or "different hardware")
            raise FirmwareError(
                f"Hardware mismatch: selected firmware targets {expected_label}, "
                f"but connected {connected_label}."
            )

    raise FirmwareError(
        f"Hardware compatibility check did not confirm {expected_label}; "
        f"missing expected text: {', '.join(missing or ['one supported hardware target'])}"
    )


def run_serial_hardware_check(
    check: dict,
    port_name: str,
    *,
    stop_event: threading.Event,
    log: Callable[[str], None],
) -> None:
    serial, _list_ports = _require_pyserial()
    check_type = str(check.get("type") or "serial_hardware_check").lower()
    if check_type != "serial_hardware_check":
        raise FirmwareError(f"Unsupported hardware check type: {check_type}")

    label = str(check.get("label") or "hardware compatibility check")
    baud = int(check.get("baud", 115200))
    command = str(check.get("command") or "").strip()
    open_settle_s = float(check.get("open_settle", 0.5))
    command_timeout_s = float(check.get("command_timeout", 8.0))
    if not command:
        raise FirmwareError("Hardware compatibility check is missing command.")

    log(f"Running {label} on {port_name}.")
    with serial.Serial(port_name, baudrate=baud, timeout=0.1, write_timeout=2) as port:
        time.sleep(open_settle_s)
        _raise_if_stopped(stop_event)
        serial_read_until_idle(port, idle_s=0.2, timeout_s=1.0)
        response = serial_send_command(port, command, timeout_s=command_timeout_s)

    validate_serial_hardware_response(check, response)
    expected_label = str(check.get("expected_label") or check.get("expected_group") or "selected firmware")
    log(f"Hardware compatibility passed: {expected_label} on {port_name}.")


def run_serial_bootloader_command(
    check: dict,
    port_name: str,
    *,
    stop_event: threading.Event,
    log: Callable[[str], None],
) -> None:
    serial, _list_ports = _require_pyserial()
    check_type = str(check.get("type") or "serial_bootloader").lower()
    if check_type != "serial_bootloader":
        raise FirmwareError(f"Unsupported pre-flash bootloader type: {check_type}")

    label = str(check.get("label") or "serial bootloader command")
    baud = int(check.get("baud", 115200))
    command = str(check.get("command") or "").strip()
    open_settle_s = float(check.get("open_settle", 0.5))
    command_timeout_s = float(check.get("command_timeout", 2.0))
    if not command:
        raise FirmwareError("Pre-flash bootloader command is missing command.")

    wrote_command = False
    try:
        port = serial.Serial(port_name, baudrate=baud, timeout=0.1, write_timeout=command_timeout_s)
        try:
            time.sleep(open_settle_s)
            _raise_if_stopped(stop_event)
            try:
                serial_read_until_idle(port, idle_s=0.1, timeout_s=0.3)
            except Exception:
                pass
            port.write((command + "\r\n").encode("utf-8"))
            wrote_command = True
            try:
                port.flush()
            except Exception:
                pass
        finally:
            try:
                port.close()
            except Exception:
                if not wrote_command:
                    raise
    except InstallerStopped:
        raise
    except Exception as exc:
        if wrote_command:
            log(f"Sent {label}; serial port disconnected during bootloader entry.")
            return
        raise FirmwareError(f"Failed to send {label} on {port_name}: {exc}") from exc

    log(f"Sent {label} on {port_name}.")


def run_serial_console_post_flash_check(
    check: dict,
    *,
    stop_event: threading.Event,
    log: Callable[[str], None],
) -> None:
    serial, _list_ports = _require_pyserial()
    label = str(check.get("label") or "USB serial console")
    vid = _parse_post_flash_int(check.get("vid"), "vid")
    pid = _parse_post_flash_int(check.get("pid"), "pid")
    baud = int(check.get("baud", 115200))
    timeout_s = float(check.get("timeout", 30.0))
    open_settle_s = float(check.get("open_settle", 2.0))
    command_timeout_s = float(check.get("command_timeout", 8.0))
    commands = check.get("commands") or []

    port_name = wait_for_serial_vid_pid(vid, pid, timeout_s=timeout_s, stop_event=stop_event, log=log)
    log(f"Post-flash check found {label} on {port_name}.")

    with serial.Serial(port_name, baudrate=baud, timeout=0.1, write_timeout=2) as port:
        time.sleep(open_settle_s)
        serial_read_until_idle(port, idle_s=0.2, timeout_s=1.0)
        for entry in commands:
            _raise_if_stopped(stop_event)
            command = str(entry.get("command", "")).strip()
            if not command:
                continue
            response = serial_send_command(port, command, timeout_s=command_timeout_s)
            missing = [marker for marker in entry.get("expect", []) if str(marker) not in response]
            if missing:
                raise FirmwareError(
                    f"{label} command {command!r} did not return expected text: {', '.join(missing)}"
                )
            log(f"Post-flash command passed: {command}")


def run_post_flash_check(
    check: Optional[dict],
    *,
    stop_event: threading.Event,
    log: Callable[[str], None],
) -> None:
    if not check:
        return

    check_type = str(check.get("type", "")).lower()
    if check_type == "serial_console":
        run_serial_console_post_flash_check(check, stop_event=stop_event, log=log)
        return

    raise FirmwareError(f"Unsupported post-flash check type: {check_type or '<missing>'}")


def run_install_loop(
    firmware: FirmwareSource,
    verify_controller: bool,
    post_flash_check: Optional[dict],
    pre_flash_bootloader: Optional[dict],
    hardware_check: Optional[dict],
    stop_event: threading.Event,
    log: Callable[[str], None],
    status: Callable[[str, str], None],
    once: bool = False,
) -> None:
    log(f"Firmware selected: {firmware.display_name}")
    if pre_flash_bootloader:
        log("Waiting for an RPI-RP2 bootloader drive or a matching USB serial device.")
    else:
        log("Waiting for an RPI-RP2 bootloader drive.")

    while True:
        _raise_if_stopped(stop_event)
        if pre_flash_bootloader:
            status("Waiting for RPI-RP2 drive or matching serial device...", "blue")
        else:
            status("Waiting for RPI-RP2 drive...", "blue")
        baseline = list_game_controllers() if verify_controller else set()
        mounts = wait_for_flash_mounts(pre_flash_bootloader, hardware_check, stop_event, log)

        status(f"Copying {firmware.copy_name}...", "orange")
        log(f"Found RPI-RP2 drive(s): {format_mounts(mounts)}")
        for mount in mounts:
            target = copy_firmware_to_mount(firmware, mount)
            log(f"Copied firmware to {target}")

        status("Waiting for RPI-RP2 drive(s) to detach...", "orange")
        wait_for_detach(mounts, stop_event)
        log("Bootloader drive(s) detached.")

        if verify_controller:
            status("Waiting for controller/gamepad enumeration...", "orange")
            detected = wait_for_new_controller(baseline, stop_event, log)
            log(f"Controller detected: {format_controllers(detected)}")

        if post_flash_check:
            label = str(post_flash_check.get("label") or "post-flash sanity check")
            status(f"Running {label}...", "orange")
            run_post_flash_check(post_flash_check, stop_event=stop_event, log=log)
            log(f"Post-flash check passed: {label}")

        next_target = "next device" if pre_flash_bootloader else "next RPI-RP2"
        if verify_controller and post_flash_check:
            status(f"{CHECK_MARK} Flash complete; controller and post-flash check passed. Waiting for {next_target}...", "green")
        elif verify_controller:
            status(f"{CHECK_MARK} Flash complete; controller detected. Waiting for {next_target}...", "green")
        elif post_flash_check:
            status(f"{CHECK_MARK} Flash complete; post-flash check passed. Waiting for {next_target}...", "green")
        else:
            status(f"{CHECK_MARK} Flash complete. Waiting for {next_target}...", "green")

        if once:
            return

        if pre_flash_bootloader:
            try:
                vid = _parse_pre_flash_int(pre_flash_bootloader.get("vid"), "vid")
                pid = _parse_pre_flash_int(pre_flash_bootloader.get("pid"), "pid")
                status("Flash complete. Disconnect device to arm the next unit...", "green")
                wait_for_serial_vid_pid_detach(vid, pid, stop_event=stop_event, log=log)
                log("Flashed serial device disconnected.")
            except FirmwareError as error:
                log(f"Skipping serial disconnect wait: {error}")

        stop_event.wait(1.5)


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        if platform.system().lower() == "windows":
            script = default_firmware_root() / "firmware_installer_windows.ps1"
            if script.exists():
                env = os.environ.copy()
                if getattr(sys, "frozen", False):
                    env["FIRMWARE_INSTALLER_EXE"] = str(Path(sys.executable).resolve())
                subprocess.Popen(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-STA",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                    ],
                    cwd=str(default_firmware_root()),
                    env=env,
                )
                return 0
        print("Tkinter is not available. Use --firmware for CLI mode.", file=sys.stderr)
        return 2

    class App:
        def __init__(self, root: "tk.Tk") -> None:
            self.root = root
            self.firmware: Optional[FirmwareSource] = None
            self.selected_choice: Optional[FirmwareChoice] = None
            self.selected_version: Optional[FirmwareVersion] = None
            self.choice_by_label: Dict[str, FirmwareChoice] = {}
            self.version_by_label: Dict[str, FirmwareVersion] = {}
            self.catalog_choices: List[FirmwareChoice] = []
            self.connected_choice_keys: Set[str] = set()
            self.device_detection_after_id: Optional[str] = None
            self.device_detection_error_logged = False
            self.worker: Optional[threading.Thread] = None
            self.startup_refresh_worker: Optional[threading.Thread] = None
            self.startup_refresh_running = False
            self.version_user_selected = False
            self.closing = False
            self.stop_event = threading.Event()

            root.title("UF2 Firmware Installer")
            root.geometry("780x570")
            root.minsize(700, 500)
            root.protocol("WM_DELETE_WINDOW", self.close)

            self.choice_var = tk.StringVar(value="")
            self.version_var = tk.StringVar(value="")
            self.verify_var = tk.BooleanVar(value=True)
            self.status_var = tk.StringVar(value="Select firmware, then connect a device.")

            frame = ttk.Frame(root, padding=16)
            frame.pack(fill="both", expand=True)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(7, weight=1)

            title = ttk.Label(frame, text="MiSTer Addons Firmware Installer", font=("TkDefaultFont", 16, "bold"))
            title.grid(row=0, column=0, columnspan=4, sticky="w")

            ttk.Label(frame, text="Firmware").grid(row=1, column=0, sticky="w", pady=(14, 2))

            self.firmware_combo = ttk.Combobox(frame, textvariable=self.choice_var, state="readonly")
            self.firmware_combo.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            self.firmware_combo.bind("<<ComboboxSelected>>", self.select_dropdown_firmware)

            self.download_button = ttk.Button(frame, text="Download/Refresh", command=self.download_selected)
            self.download_button.grid(row=2, column=2, sticky="e", padx=(8, 0), pady=(0, 8))

            self.select_button = ttk.Button(frame, text="Browse...", command=self.select_firmware)
            self.select_button.grid(row=2, column=3, sticky="e", padx=(8, 0), pady=(0, 8))

            ttk.Label(frame, text="Version").grid(row=3, column=0, sticky="w", pady=(4, 2))

            self.version_combo = ttk.Combobox(frame, textvariable=self.version_var, state="readonly")
            self.version_combo.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 8))
            self.version_combo.bind("<<ComboboxSelected>>", self.select_dropdown_version)

            self.verify_check = ttk.Checkbutton(
                frame,
                text="Wait for controller/gamepad after flashing",
                variable=self.verify_var,
            )
            self.verify_check.grid(row=5, column=0, columnspan=4, sticky="w")

            self.status_label = tk.Label(
                frame,
                textvariable=self.status_var,
                anchor="w",
                fg="gray25",
                font=("TkDefaultFont", 12, "bold"),
            )
            self.status_label.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 8))

            self.log_text = tk.Text(frame, height=14, wrap="word", state="disabled")
            self.log_text.grid(row=7, column=0, columnspan=4, sticky="nsew")

            scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
            scrollbar.grid(row=7, column=4, sticky="ns")
            self.log_text.configure(yscrollcommand=scrollbar.set)

            self.start_button = ttk.Button(frame, text="Start", command=self.start)
            self.start_button.grid(row=8, column=2, sticky="e", pady=(12, 0))

            self.stop_button = ttk.Button(frame, text="Stop", command=self.stop, state="disabled")
            self.stop_button.grid(row=8, column=3, sticky="e", padx=(8, 0), pady=(12, 0))

            self.load_firmware_choices()
            self.schedule_device_detection_refresh()
            self.log("Select firmware, then connect a board normally or in BOOTSEL/RPI-RP2 mode.")
            self.start_startup_refresh()

        def load_firmware_choices(self) -> None:
            self.catalog_choices = discover_firmware_choices()
            self.refresh_connected_choice_keys(log_errors=False)
            labels = self.populate_firmware_combo()

            if labels:
                self.log(f"Found {len(labels)} catalog firmware option(s).")
                return

            self.firmware_combo.configure(state="disabled")
            self.set_status("No repo UF2 firmware found. Use Browse for a custom UF2.", "orange")

        def refresh_connected_choice_keys(self, log_errors: bool = True) -> bool:
            if not any(firmware_choice_vid_pid(choice) for choice in self.catalog_choices):
                connected_keys = set()
            else:
                try:
                    connected_vid_pids = list_connected_serial_vid_pids()
                    connected_keys = connected_firmware_choice_keys(self.catalog_choices, connected_vid_pids)
                    self.device_detection_error_logged = False
                except FirmwareError as error:
                    connected_keys = set()
                    if log_errors and not self.device_detection_error_logged:
                        self.log(f"USB serial device detection unavailable: {error}")
                        self.device_detection_error_logged = True

            if connected_keys == self.connected_choice_keys:
                return False
            self.connected_choice_keys = connected_keys
            return True

        def schedule_device_detection_refresh(self) -> None:
            if not self.closing:
                self.device_detection_after_id = self.root.after(2000, self.refresh_connected_devices)

        def refresh_connected_devices(self) -> None:
            self.device_detection_after_id = None
            try:
                if not (self.worker and self.worker.is_alive()) and self.catalog_choices:
                    selected_key = firmware_choice_key(self.selected_choice) if self.selected_choice else None
                    if self.refresh_connected_choice_keys():
                        self.populate_firmware_combo(selected_key=selected_key)
            finally:
                self.schedule_device_detection_refresh()

        def choice_display_label(self, choice: FirmwareChoice) -> str:
            if firmware_choice_key(choice) in self.connected_choice_keys:
                return f"{choice.label} [connected]"
            return choice.label

        def ordered_firmware_choices(self) -> List[FirmwareChoice]:
            indexed = list(enumerate(self.catalog_choices))
            indexed.sort(
                key=lambda item: (
                    0 if firmware_choice_key(item[1]) in self.connected_choice_keys else 1,
                    item[0],
                )
            )
            return [choice for _index, choice in indexed]

        def populate_firmware_combo(
            self,
            selected_key: Optional[str] = None,
            selected_source_key: Optional[str] = None,
        ) -> List[str]:
            ordered_choices = self.ordered_firmware_choices()
            self.choice_by_label = {self.choice_display_label(choice): choice for choice in ordered_choices}
            labels = list(self.choice_by_label)
            self.firmware_combo.configure(values=labels)

            if not labels:
                return labels

            if selected_key is None and self.selected_choice:
                selected_key = firmware_choice_key(self.selected_choice)
            if selected_source_key is None and self.selected_version:
                selected_source_key = firmware_source_key(self.selected_version.source)

            selected = self.choice_for_key(selected_key) if selected_key else None
            if selected is None:
                selected = ordered_choices[0]
            self.apply_choice(
                selected,
                log_selected=False,
                selected_source_key=selected_source_key,
            )
            return labels

        def choice_for_key(self, key: Optional[str]) -> Optional[FirmwareChoice]:
            if not key:
                return None
            for choice in self.catalog_choices:
                if firmware_choice_key(choice) == key or choice.label == key:
                    return choice
            return None

        def select_dropdown_firmware(self, _event: object = None, log_selected: bool = True) -> None:
            label = self.choice_var.get()
            choice = self.choice_by_label.get(label)
            if choice is None:
                return
            self.version_user_selected = False
            self.apply_choice(choice, log_selected=log_selected)

        def select_dropdown_version(self, _event: object = None) -> None:
            version = self.version_by_label.get(self.version_var.get())
            if version:
                self.version_user_selected = True
                self.apply_version(version)

        def populate_version_combo(
            self,
            choice: FirmwareChoice,
            selected_source_key: Optional[str] = None,
        ) -> None:
            if choice.item_id:
                item = get_catalog_item(choice.item_id)
                versions = catalog_firmware_versions(item)
            elif choice.source:
                versions = [FirmwareVersion("custom", choice.source, "custom", choice.hardware_check)]
            else:
                versions = []

            self.version_by_label = {}
            labels: List[str] = []
            for index, version in enumerate(versions):
                latest = " [latest]" if index == 0 else ""
                label = f"{version.version} ({version.status}){latest}"
                self.version_by_label[label] = version
                labels.append(label)
            self.version_combo.configure(values=labels)

            if not versions:
                self.selected_version = None
                self.firmware = None
                self.version_var.set("Download required")
                self.version_combo.configure(state="disabled")
                return

            selected = None
            if selected_source_key:
                selected = next(
                    (
                        version
                        for version in versions
                        if firmware_source_key(version.source) == selected_source_key
                    ),
                    None,
                )
            if selected is None:
                selected = versions[0]
            self.version_combo.configure(state="readonly" if len(versions) > 1 else "disabled")
            self.apply_version(selected, log_selected=False)

        def apply_version(self, version: FirmwareVersion, log_selected: bool = True) -> None:
            self.selected_version = version
            self.firmware = version.source
            for label, candidate in self.version_by_label.items():
                if firmware_source_key(candidate.source) == firmware_source_key(version.source):
                    self.version_var.set(label)
                    break
            self.update_selection_status()
            if log_selected:
                self.log(f"Firmware version selected: {version.version} - {version.source.display_name}")

        def apply_choice(
            self,
            choice: FirmwareChoice,
            log_selected: bool = True,
            selected_source_key: Optional[str] = None,
        ) -> None:
            self.selected_choice = choice
            self.choice_var.set(self.choice_display_label(choice))
            if choice.controller_check is None:
                self.verify_var.set(is_controller_firmware(choice.source) if choice.source else False)
            else:
                self.verify_var.set(choice.controller_check)
            self.populate_version_combo(choice, selected_source_key=selected_source_key)
            self.update_selection_status()

            if log_selected:
                self.log(f"Firmware selected: {choice.label}")

        def selected_hardware_check(self) -> Optional[dict]:
            if self.selected_version:
                return self.selected_version.hardware_check
            if self.selected_choice:
                return self.selected_choice.hardware_check
            return None

        def update_selection_status(self) -> None:
            choice = self.selected_choice
            if choice is None:
                self.set_status("Select firmware, then connect a device.", "gray25")
                return
            firmware = self.firmware
            status = self.selected_version.status if self.selected_version else choice.status
            hardware_check = self.selected_hardware_check()

            if choice.install_method == "coming_soon":
                self.set_status("Coming soon; no firmware source configured yet.", "orange")
            elif choice.install_method != "rp2040":
                self.set_status(f"{status.title()}; download/cache only for this package.", "orange")
            elif firmware and firmware_choice_key(choice) in self.connected_choice_keys:
                self.set_status(f"Ready ({status}); connected device detected.", "green")
            elif firmware and hardware_check:
                target = str(hardware_check.get("expected_label") or "verified hardware")
                self.set_status(f"Ready ({status}); connect normally for {target} verification.", "gray25")
            elif firmware and choice.pre_flash_bootloader:
                self.set_status(f"Ready ({status}); normal serial or BOOTSEL/RPI-RP2 supported.", "gray25")
            elif firmware:
                self.set_status(f"Ready ({status}).", "gray25")
            else:
                self.set_status("Download required; Start will download first.", "orange")

        def select_firmware(self) -> None:
            file_name = filedialog.askopenfilename(
                title="Select UF2 firmware",
                filetypes=[
                    ("UF2 firmware", "*.uf2"),
                    ("ZIP packages", "*.zip"),
                    ("All files", "*.*"),
                ],
            )
            if not file_name:
                return

            path = Path(file_name)
            try:
                firmware = resolve_firmware(path)
            except MultipleFirmwareFound as error:
                member = self.choose_zip_member(path, error.entries)
                if not member:
                    return
                try:
                    firmware = resolve_firmware(path, member)
                except FirmwareError as nested_error:
                    messagebox.showerror("Firmware error", str(nested_error))
                    return
            except FirmwareError as error:
                messagebox.showerror("Firmware error", str(error))
                return

            label = _firmware_choice_label(firmware, default_firmware_root())
            choice = self.choice_for_key(label)
            if choice is None:
                choice = FirmwareChoice(label=label, source=firmware)
                self.catalog_choices.append(choice)
                self.firmware_combo.configure(state="readonly")
            self.populate_firmware_combo(selected_key=firmware_choice_key(choice))

        def start_startup_refresh(self) -> None:
            if self.startup_refresh_running:
                return
            self.startup_refresh_running = True
            self.download_button.configure(state="disabled")
            self.log("Checking all catalog firmware for updates...")
            self.startup_refresh_worker = threading.Thread(
                target=self.startup_refresh_main,
                daemon=True,
            )
            self.startup_refresh_worker.start()

        def startup_refresh_main(self) -> None:
            _refreshed, errors = refresh_all_catalog_firmware(log=self.thread_log)
            if not self.closing:
                self.root.after(0, lambda: self.finish_startup_refresh(errors))

        def finish_startup_refresh(self, errors: Dict[str, str]) -> None:
            if self.closing:
                return
            if self.worker and self.worker.is_alive():
                self.root.after(500, lambda: self.finish_startup_refresh(errors))
                return

            selected_key = firmware_choice_key(self.selected_choice) if self.selected_choice else None
            selected_source_key = (
                firmware_source_key(self.selected_version.source)
                if self.selected_version and self.version_user_selected
                else ""
            )
            custom_choice = self.selected_choice if self.selected_choice and not self.selected_choice.item_id else None
            self.catalog_choices = discover_firmware_choices()
            if custom_choice:
                self.catalog_choices.append(custom_choice)
            self.refresh_connected_choice_keys(log_errors=False)
            self.populate_firmware_combo(
                selected_key=selected_key,
                selected_source_key=selected_source_key,
            )
            self.startup_refresh_running = False
            self.download_button.configure(state="normal")
            if errors:
                self.log(f"Update check completed with {len(errors)} source error(s); bundled firmware remains available.")
            else:
                self.log("Startup update check complete; all catalog firmware is ready.")

        def download_selected(self) -> None:
            choice = self.selected_choice
            if choice is None or choice.item_id is None:
                self.set_status("Select a catalog firmware item to download.", "orange")
                return
            if self.startup_refresh_running:
                self.set_status("The startup firmware update check is already running.", "orange")
                return
            if self.worker and self.worker.is_alive():
                return
            self.stop_event.clear()
            self.set_busy(True)
            self.worker = threading.Thread(target=self.download_worker, args=(choice, True), daemon=True)
            self.worker.start()

        def choose_zip_member(self, path: Path, entries: Sequence[str]) -> Optional[str]:
            dialog = tk.Toplevel(self.root)
            dialog.title("Choose UF2 from archive")
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.geometry("620x320")
            dialog.columnconfigure(0, weight=1)
            dialog.rowconfigure(1, weight=1)

            ttk.Label(dialog, text=f"{path.name} contains multiple UF2 files. Choose one:").grid(
                row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 6)
            )
            listbox = tk.Listbox(dialog, exportselection=False)
            listbox.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=12)
            for entry in entries:
                listbox.insert("end", entry)
            if entries:
                listbox.selection_set(0)

            selected: List[Optional[str]] = [None]

            def accept() -> None:
                selection = listbox.curselection()
                if selection:
                    selected[0] = entries[int(selection[0])]
                dialog.destroy()

            def cancel() -> None:
                dialog.destroy()

            ttk.Button(dialog, text="Use Selected", command=accept).grid(row=2, column=0, sticky="e", pady=12)
            ttk.Button(dialog, text="Cancel", command=cancel).grid(row=2, column=1, sticky="w", padx=8, pady=12)
            listbox.bind("<Double-Button-1>", lambda _event: accept())

            self.root.wait_window(dialog)
            return selected[0]

        def start(self) -> None:
            if self.selected_choice is None and self.firmware is None:
                self.select_firmware()
                if self.selected_choice is None and self.firmware is None:
                    return

            self.stop_event.clear()
            self.set_busy(True)
            choice = self.selected_choice
            firmware = self.firmware
            verify = bool(self.verify_var.get())
            hardware_check = self.selected_hardware_check()
            self.worker = threading.Thread(
                target=self.worker_main,
                args=(choice, firmware, verify, hardware_check),
                daemon=True,
            )
            self.worker.start()

        def stop(self) -> None:
            self.stop_event.set()
            self.set_status("Stopping...", "orange")

        def download_worker(self, choice: FirmwareChoice, force: bool) -> None:
            try:
                firmware = ensure_catalog_firmware(
                    choice.item_id or "",
                    force_download=force,
                    log=self.thread_log,
                )
                self.thread_log(f"Cached firmware: {firmware.display_name}")
                self.thread_status("Cached. Select Start to flash RP2040 firmware.", "green")
                self.root.after(0, lambda: self.refresh_choice_after_download(firmware_choice_key(choice)))
            except Exception as error:
                self.thread_log(f"Error: {error}")
                self.thread_status(f"Error: {error}", "red")
            finally:
                self.root.after(0, self.worker_done)

        def refresh_choice_after_download(self, label: str) -> None:
            self.catalog_choices = discover_firmware_choices()
            self.refresh_connected_choice_keys(log_errors=False)
            self.version_user_selected = False
            self.populate_firmware_combo(selected_key=label, selected_source_key="")

        def worker_main(
            self,
            choice: Optional[FirmwareChoice],
            firmware: Optional[FirmwareSource],
            verify: bool,
            hardware_check: Optional[dict],
        ) -> None:
            try:
                if choice and choice.item_id:
                    if choice.install_method == "coming_soon":
                        raise FirmwareError(f"{choice.label} firmware is coming soon.")
                    item = get_catalog_item(choice.item_id)
                    if firmware is None:
                        firmware = ensure_catalog_firmware(
                            choice.item_id,
                            force_download=False,
                            log=self.thread_log,
                        )
                        hardware_check = item.hardware_check
                    else:
                        validate_catalog_firmware(item, firmware)
                    if choice.install_method != "rp2040":
                        self.thread_log(f"Cached firmware package: {firmware.display_name}")
                        self.thread_status("Cached. 32u4 flashing is not implemented in this RPI-RP2 installer.", "green")
                        return

                if firmware is None:
                    raise FirmwareError("No firmware selected.")

                run_install_loop(
                    firmware=firmware,
                    verify_controller=verify,
                    post_flash_check=choice.post_flash_check if choice else None,
                    pre_flash_bootloader=choice.pre_flash_bootloader if choice else None,
                    hardware_check=hardware_check,
                    stop_event=self.stop_event,
                    log=self.thread_log,
                    status=self.thread_status,
                )
            except InstallerStopped:
                self.thread_log("Stopped.")
                self.thread_status("Stopped.", "gray25")
            except Exception as error:
                self.thread_log(f"Error: {error}")
                self.thread_status(f"Error: {error}", "red")
            finally:
                self.root.after(0, self.worker_done)

        def set_busy(self, busy: bool) -> None:
            state = "disabled" if busy else "normal"
            self.select_button.configure(state=state)
            self.download_button.configure(state="disabled" if busy or self.startup_refresh_running else "normal")
            if busy:
                self.firmware_combo.configure(state="disabled")
                self.version_combo.configure(state="disabled")
                self.start_button.configure(state="disabled")
                self.stop_button.configure(state="normal")
                self.verify_check.configure(state="disabled")
            else:
                if self.choice_by_label:
                    self.firmware_combo.configure(state="readonly")
                if len(self.version_by_label) > 1:
                    self.version_combo.configure(state="readonly")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.verify_check.configure(state="normal")

        def worker_done(self) -> None:
            self.set_busy(False)

        def thread_log(self, message: str) -> None:
            if not self.closing:
                with contextlib.suppress(RuntimeError, tk.TclError):
                    self.root.after(0, lambda: self.log(message))

        def thread_status(self, message: str, color: str) -> None:
            if not self.closing:
                with contextlib.suppress(RuntimeError, tk.TclError):
                    self.root.after(0, lambda: self.set_status(message, color))

        def set_status(self, message: str, color: str) -> None:
            self.status_var.set(message)
            self.status_label.configure(fg=color)

        def log(self, message: str) -> None:
            timestamp = time.strftime("%H:%M:%S")
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def close(self) -> None:
            self.closing = True
            self.stop_event.set()
            if self.device_detection_after_id:
                with contextlib.suppress(tk.TclError):
                    self.root.after_cancel(self.device_detection_after_id)
            self.root.destroy()

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flash UF2 firmware to RPI-RP2 bootloader drives.")
    parser.add_argument("--firmware", type=Path, help="Path to a .uf2 file or .zip containing a .uf2 file.")
    parser.add_argument("--product", help="Catalog product id to download/use.")
    parser.add_argument("--version", help="Catalog firmware version to use with --product; defaults to latest.")
    parser.add_argument("--download", action="store_true", help="Download/cache the selected --product and exit.")
    parser.add_argument("--refresh", action="store_true", help="Force re-download when used with --download or --product.")
    parser.add_argument("--refresh-all", action="store_true", help="Check/cache all catalog firmware and exit.")
    parser.add_argument("--list-catalog", action="store_true", help="List catalog firmware options and exit.")
    parser.add_argument("--catalog-json", action="store_true", help="Print catalog firmware options as JSON and exit.")
    parser.add_argument("--zip-member", help="UF2 path inside a .zip package when multiple are present.")
    parser.add_argument("--once", action="store_true", help="Flash one attach/detach cycle, then exit. Without this, flashing is continuous.")

    verify_group = parser.add_mutually_exclusive_group()
    verify_group.add_argument(
        "--controller-check",
        action="store_true",
        help="Wait for a new controller/gamepad after flashing.",
    )
    verify_group.add_argument(
        "--no-controller-check",
        action="store_true",
        help="Do not wait for controller/gamepad enumeration.",
    )

    return parser.parse_args(argv)


def _print_log(message: str) -> None:
    print(message, flush=True)


def _print_status(message: str, _color: str) -> None:
    print(message, flush=True)


def catalog_choice_records(root: Optional[Path] = None) -> List[dict]:
    records: List[dict] = []
    for choice in discover_firmware_choices(root):
        source = choice.source.display_name if choice.source else ""
        versions: List[dict] = []
        if choice.item_id:
            item = get_catalog_item(choice.item_id, root)
            for version in catalog_firmware_versions(item, root):
                hardware_target = ""
                if version.hardware_check:
                    hardware_target = str(
                        version.hardware_check.get("expected_label")
                        or version.hardware_check.get("expected_group")
                        or ""
                    )
                versions.append(
                    {
                        "version": version.version,
                        "status": version.status,
                        "source": version.source.display_name,
                        "hardware_target": hardware_target,
                    }
                )
        records.append(
            {
                "id": choice.item_id or "",
                "label": choice.label,
                "install_method": choice.install_method,
                "controller_check": bool(choice.controller_check),
                "hardware_check": bool(choice.hardware_check),
                "pre_flash_bootloader": bool(choice.pre_flash_bootloader),
                "post_flash_check": bool(choice.post_flash_check),
                "status": choice.status,
                "source": source,
                "versions": versions,
            }
        )
    return records


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    post_flash_check: Optional[dict] = None
    pre_flash_bootloader: Optional[dict] = None
    hardware_check: Optional[dict] = None
    if args.version and not args.product:
        print("Firmware error: --version requires --product.", file=sys.stderr)
        return 2

    if args.refresh_all:
        refreshed, errors = refresh_all_catalog_firmware(log=_print_log)
        print(f"Checked {len(refreshed) + len(errors)} catalog item(s); {len(refreshed)} ready, {len(errors)} failed.")
        for item_id, error in errors.items():
            print(f"{item_id}: {error}", file=sys.stderr)
        return 1 if errors else 0

    if args.catalog_json:
        print(json.dumps(catalog_choice_records(), indent=2))
        return 0

    if args.list_catalog:
        for record in catalog_choice_records():
            print(
                f"{record['id']}\t{record['label']}\t{record['install_method']}\t"
                f"{record['status']}\t{record['source']}"
            )
        return 0

    if args.product:
        try:
            item = get_catalog_item(args.product)
            if args.version:
                if args.refresh:
                    raise FirmwareError("--refresh cannot be combined with --version; use --refresh-all first.")
                selected_version = select_catalog_firmware_version(item, args.version)
                firmware = selected_version.source
                validate_catalog_firmware(item, firmware)
                hardware_check = selected_version.hardware_check
            else:
                firmware = ensure_catalog_firmware(
                    args.product,
                    force_download=args.refresh,
                    log=_print_log,
                )
                available_versions = catalog_firmware_versions(item)
                selected_version = next(
                    (
                        version
                        for version in available_versions
                        if firmware_source_key(version.source) == firmware_source_key(firmware)
                    ),
                    None,
                )
                hardware_check = selected_version.hardware_check if selected_version else item.hardware_check
        except FirmwareError as error:
            print(f"Firmware error: {error}", file=sys.stderr)
            return 2

        if args.download or item.install_method != "rp2040":
            print(firmware.display_name)
            if item.install_method != "rp2040":
                print("Cached only: this item is not flashable by the RPI-RP2 installer.", file=sys.stderr)
            return 0

        if args.controller_check:
            verify_controller = True
        elif args.no_controller_check:
            verify_controller = False
        else:
            verify_controller = item.controller_check
        pre_flash_bootloader = item.pre_flash_bootloader
        post_flash_check = item.post_flash_check
    elif not args.firmware:
        return launch_gui()
    else:
        try:
            firmware = resolve_firmware(args.firmware, args.zip_member)
        except MultipleFirmwareFound as error:
            print("Archive contains multiple UF2 files. Re-run with --zip-member:", file=sys.stderr)
            for entry in error.entries:
                print(f"  {entry}", file=sys.stderr)
            return 2
        except FirmwareError as error:
            print(f"Firmware error: {error}", file=sys.stderr)
            return 2

        if args.controller_check:
            verify_controller = True
        elif args.no_controller_check:
            verify_controller = False
        else:
            verify_controller = is_controller_firmware(firmware)

    stop_event = threading.Event()
    try:
        run_install_loop(
            firmware=firmware,
            verify_controller=verify_controller,
            post_flash_check=post_flash_check,
            pre_flash_bootloader=pre_flash_bootloader,
            hardware_check=hardware_check,
            stop_event=stop_event,
            log=_print_log,
            status=_print_status,
            once=args.once,
        )
    except KeyboardInterrupt:
        stop_event.set()
        print("Stopped.", file=sys.stderr)
        return 130
    except InstallerStopped:
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
