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
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


RPI_RP2_LABEL = "RPI-RP2"
COPY_BUFFER_SIZE = 1024 * 1024
CHECK_MARK = "[OK]"
UF2_TARGET_NAME = "firmware.uf2"

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
    status: str = "custom"


@dataclass(frozen=True)
class CatalogItem:
    item_id: str
    label: str
    install_method: str
    file_type: str
    controller_check: bool
    sources: Sequence[dict]
    local_paths: Sequence[str]
    notes: str = ""


@dataclass(frozen=True)
class DownloadPlan:
    url: str
    file_name: str
    source_label: str


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
        return FirmwareSource(path)

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
        return FirmwareSource(path, entries[0])

    raise FirmwareError("Select a .uf2 file or a .zip file containing a .uf2 file.")


PROJECT_LABELS = {
    "mistercade-v2": "MiSTercade V2",
    "reflex-prism": "Reflex Prism",
}

SKIP_DISCOVERY_DIRS = {".git", "__pycache__", "__macosx"}
CATALOG_FILE = "firmware_catalog.json"
USER_AGENT = "MiSTerAddonsFirmwareInstaller/1.0"
GITHUB_API = "https://api.github.com/repos"


def default_firmware_root() -> Path:
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
        return []

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
                notes=raw.get("notes", ""),
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
                status=status,
            )
        )
    return choices


def get_catalog_item(item_id: str, root: Optional[Path] = None) -> CatalogItem:
    for item in load_catalog(root):
        if item.item_id == item_id:
            return item
    raise FirmwareError(f"Unknown catalog firmware: {item_id}")


def find_catalog_source(item: CatalogItem, root: Optional[Path] = None) -> Tuple[Optional[FirmwareSource], str]:
    root = (root or default_firmware_root()).resolve()
    cached = find_cached_catalog_file(item, root)
    if cached:
        return FirmwareSource(cached), "cached"

    for local_path in item.local_paths:
        candidate = (root / local_path).resolve()
        if candidate.exists():
            return FirmwareSource(candidate), "bundled"

    if item.install_method == "coming_soon":
        return None, "coming soon"
    if item.sources:
        return None, "download required"
    return None, "missing"


def find_cached_catalog_file(item: CatalogItem, root: Optional[Path] = None) -> Optional[Path]:
    cache_dir = catalog_item_cache_dir(item, root)
    if not cache_dir.is_dir():
        return None

    candidates = [path for path in cache_dir.iterdir() if path.is_file()]
    if item.file_type == "uf2":
        candidates = [path for path in candidates if path.suffix.lower() == ".uf2"]
    elif item.file_type == "package":
        candidates = [path for path in candidates if path.suffix.lower() in {".zip", ".gz", ".tgz", ".bin", ".hex"}]

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


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
            return existing

    if not item.sources:
        raise FirmwareError(f"No download source configured for {item.label}.")

    last_error: Optional[Exception] = None
    for source in item.sources:
        try:
            plan = resolve_download_plan(source)
            target = catalog_item_cache_dir(item, root) / safe_file_name(plan.file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if force_download or not target.exists():
                if log:
                    log(f"Downloading {item.label} from {plan.source_label}: {plan.file_name}")
                download_file(plan.url, target)
            return FirmwareSource(target)
        except Exception as error:
            last_error = error
            if log:
                log(f"Download source failed for {item.label}: {error}")

    local, status = find_catalog_source(item, root)
    if local:
        if log:
            log(f"Using {status} firmware because download failed.")
        return local

    if last_error:
        raise FirmwareError(f"Could not download {item.label}: {last_error}")
    raise FirmwareError(f"Could not find firmware for {item.label}.")


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
        return DownloadPlan(url=url, file_name=source.get("file_name") or Path(url).name, source_label=url)
    raise FirmwareError(f"Unsupported source type: {source_type}")


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


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temp = target.with_suffix(target.suffix + ".tmp")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with temp.open("wb") as stream:
                shutil.copyfileobj(response, stream, COPY_BUFFER_SIZE)
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
    numbers = [int(part) for part in re.findall(r"\d+", value)]
    return tuple(numbers or [0])


def safe_file_name(value: str) -> str:
    name = Path(value).name
    return re.sub(r"[^A-Za-z0-9._+ -]", "_", name)


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
        if re.match(r"^v?\d", lower) or re.match(r"^main-\d{4}$", lower):
            return part
    return ""


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


def run_install_loop(
    firmware: FirmwareSource,
    verify_controller: bool,
    stop_event: threading.Event,
    log: Callable[[str], None],
    status: Callable[[str, str], None],
    once: bool = False,
) -> None:
    log(f"Firmware selected: {firmware.display_name}")
    log("Waiting for an RPI-RP2 bootloader drive.")

    while True:
        _raise_if_stopped(stop_event)
        status("Waiting for RPI-RP2 drive...", "blue")
        baseline = list_game_controllers() if verify_controller else set()
        mounts = wait_for_rpi_rp2(stop_event)

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
            status(f"{CHECK_MARK} Flash complete; controller detected. Waiting for next RPI-RP2...", "green")
        else:
            status(f"{CHECK_MARK} Flash complete. Waiting for next RPI-RP2...", "green")

        if once:
            return

        stop_event.wait(1.5)


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        if platform.system().lower() == "windows":
            script = default_firmware_root() / "firmware_installer_windows.ps1"
            if script.exists():
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
                )
                return 0
        print("Tkinter is not available. Use --firmware for CLI mode.", file=sys.stderr)
        return 2

    class App:
        def __init__(self, root: "tk.Tk") -> None:
            self.root = root
            self.firmware: Optional[FirmwareSource] = None
            self.selected_choice: Optional[FirmwareChoice] = None
            self.choice_by_label: Dict[str, FirmwareChoice] = {}
            self.worker: Optional[threading.Thread] = None
            self.stop_event = threading.Event()

            root.title("UF2 Firmware Installer")
            root.geometry("760x520")
            root.minsize(680, 440)

            self.choice_var = tk.StringVar(value="")
            self.verify_var = tk.BooleanVar(value=True)
            self.status_var = tk.StringVar(value="Select firmware, then connect an RPI-RP2 drive.")

            frame = ttk.Frame(root, padding=16)
            frame.pack(fill="both", expand=True)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(5, weight=1)

            title = ttk.Label(frame, text="RP2040 UF2 Firmware Installer", font=("TkDefaultFont", 16, "bold"))
            title.grid(row=0, column=0, columnspan=4, sticky="w")

            ttk.Label(frame, text="Firmware").grid(row=1, column=0, sticky="w", pady=(14, 2))

            self.firmware_combo = ttk.Combobox(frame, textvariable=self.choice_var, state="readonly")
            self.firmware_combo.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            self.firmware_combo.bind("<<ComboboxSelected>>", self.select_dropdown_firmware)

            self.download_button = ttk.Button(frame, text="Download/Refresh", command=self.download_selected)
            self.download_button.grid(row=2, column=2, sticky="e", padx=(8, 0), pady=(0, 8))

            self.select_button = ttk.Button(frame, text="Browse...", command=self.select_firmware)
            self.select_button.grid(row=2, column=3, sticky="e", padx=(8, 0), pady=(0, 8))

            self.verify_check = ttk.Checkbutton(
                frame,
                text="Wait for controller/gamepad after flashing",
                variable=self.verify_var,
            )
            self.verify_check.grid(row=3, column=0, columnspan=4, sticky="w")

            self.status_label = tk.Label(
                frame,
                textvariable=self.status_var,
                anchor="w",
                fg="gray25",
                font=("TkDefaultFont", 12, "bold"),
            )
            self.status_label.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(14, 8))

            self.log_text = tk.Text(frame, height=14, wrap="word", state="disabled")
            self.log_text.grid(row=5, column=0, columnspan=4, sticky="nsew")

            scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
            scrollbar.grid(row=5, column=4, sticky="ns")
            self.log_text.configure(yscrollcommand=scrollbar.set)

            self.start_button = ttk.Button(frame, text="Start", command=self.start)
            self.start_button.grid(row=6, column=2, sticky="e", pady=(12, 0))

            self.stop_button = ttk.Button(frame, text="Stop", command=self.stop, state="disabled")
            self.stop_button.grid(row=6, column=3, sticky="e", padx=(8, 0), pady=(12, 0))

            self.load_firmware_choices()
            self.log("Select firmware, then connect a board in BOOTSEL/RPI-RP2 mode.")

        def load_firmware_choices(self) -> None:
            choices = discover_firmware_choices()
            self.choice_by_label = {choice.label: choice for choice in choices}
            labels = list(self.choice_by_label)
            self.firmware_combo.configure(values=labels)

            if labels:
                self.choice_var.set(labels[0])
                self.select_dropdown_firmware(log_selected=False)
                self.log(f"Found {len(labels)} catalog firmware option(s).")
                return

            self.firmware_combo.configure(state="disabled")
            self.set_status("No repo UF2 firmware found. Use Browse for a custom UF2.", "orange")

        def select_dropdown_firmware(self, _event: object = None, log_selected: bool = True) -> None:
            label = self.choice_var.get()
            choice = self.choice_by_label.get(label)
            if choice is None:
                return
            self.apply_choice(choice, log_selected=log_selected)

        def apply_choice(self, choice: FirmwareChoice, log_selected: bool = True) -> None:
            self.selected_choice = choice
            self.firmware = choice.source
            self.choice_var.set(choice.label)
            if choice.controller_check is None:
                self.verify_var.set(is_controller_firmware(choice.source) if choice.source else False)
            else:
                self.verify_var.set(choice.controller_check)

            if choice.install_method == "coming_soon":
                self.set_status("Coming soon; no firmware source configured yet.", "orange")
            elif choice.install_method != "rp2040":
                self.set_status(f"{choice.status.title()}; download/cache only for this 32u4 package.", "orange")
            elif choice.source:
                self.set_status(f"Ready ({choice.status}).", "gray25")
            else:
                self.set_status("Download required; Start will download first.", "orange")

            if log_selected:
                if choice.source:
                    self.log(f"Firmware selected: {choice.source.display_name}")
                else:
                    self.log(f"Firmware selected: {choice.label} ({choice.status})")

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
            if label not in self.choice_by_label:
                self.choice_by_label[label] = FirmwareChoice(label=label, source=firmware)
                self.firmware_combo.configure(values=list(self.choice_by_label), state="readonly")
            self.apply_choice(self.choice_by_label[label])

        def download_selected(self) -> None:
            choice = self.selected_choice
            if choice is None or choice.item_id is None:
                self.set_status("Select a catalog firmware item to download.", "orange")
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
            self.worker = threading.Thread(target=self.worker_main, args=(choice, firmware, verify), daemon=True)
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
                self.root.after(0, lambda: self.refresh_choice_after_download(choice.label))
            except Exception as error:
                self.thread_log(f"Error: {error}")
                self.thread_status(f"Error: {error}", "red")
            finally:
                self.root.after(0, self.worker_done)

        def refresh_choice_after_download(self, label: str) -> None:
            choices = discover_firmware_choices()
            self.choice_by_label = {choice.label: choice for choice in choices}
            self.firmware_combo.configure(values=list(self.choice_by_label))
            choice = self.choice_by_label.get(label)
            if choice:
                self.apply_choice(choice, log_selected=False)

        def worker_main(
            self,
            choice: Optional[FirmwareChoice],
            firmware: Optional[FirmwareSource],
            verify: bool,
        ) -> None:
            try:
                if choice and choice.item_id:
                    if choice.install_method == "coming_soon":
                        raise FirmwareError(f"{choice.label} firmware is coming soon.")
                    firmware = ensure_catalog_firmware(
                        choice.item_id,
                        force_download=False,
                        log=self.thread_log,
                    )
                    if choice.install_method != "rp2040":
                        self.thread_log(f"Cached firmware package: {firmware.display_name}")
                        self.thread_status("Cached. 32u4 flashing is not implemented in this RPI-RP2 installer.", "green")
                        return

                if firmware is None:
                    raise FirmwareError("No firmware selected.")

                run_install_loop(
                    firmware=firmware,
                    verify_controller=verify,
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
            self.download_button.configure(state=state)
            if busy:
                self.firmware_combo.configure(state="disabled")
                self.start_button.configure(state="disabled")
                self.stop_button.configure(state="normal")
                self.verify_check.configure(state="disabled")
            else:
                if self.choice_by_label:
                    self.firmware_combo.configure(state="readonly")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.verify_check.configure(state="normal")

        def worker_done(self) -> None:
            self.set_busy(False)

        def thread_log(self, message: str) -> None:
            self.root.after(0, lambda: self.log(message))

        def thread_status(self, message: str, color: str) -> None:
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

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flash UF2 firmware to RPI-RP2 bootloader drives.")
    parser.add_argument("--firmware", type=Path, help="Path to a .uf2 file or .zip containing a .uf2 file.")
    parser.add_argument("--product", help="Catalog product id to download/use.")
    parser.add_argument("--download", action="store_true", help="Download/cache the selected --product and exit.")
    parser.add_argument("--refresh", action="store_true", help="Force re-download when used with --download or --product.")
    parser.add_argument("--list-catalog", action="store_true", help="List catalog firmware options and exit.")
    parser.add_argument("--catalog-json", action="store_true", help="Print catalog firmware options as JSON and exit.")
    parser.add_argument("--zip-member", help="UF2 path inside a .zip package when multiple are present.")
    parser.add_argument("--once", action="store_true", help="Flash one attach/detach cycle, then exit.")

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
        records.append(
            {
                "id": choice.item_id or "",
                "label": choice.label,
                "install_method": choice.install_method,
                "controller_check": bool(choice.controller_check),
                "status": choice.status,
                "source": source,
            }
        )
    return records


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
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
            firmware = ensure_catalog_firmware(
                args.product,
                force_download=args.refresh,
                log=_print_log,
            )
            item = get_catalog_item(args.product)
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

