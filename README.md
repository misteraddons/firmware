# Firmware Mirror

Firmware files and a small cross-platform installer/cache for MiSTer Addons products.

## Current Files

| Project | Local file | Source |
| --- | --- | --- |
| Reflex Prism | [`reflex-prism/latest/prism_dac.uf2`](reflex-prism/latest/prism_dac.uf2) | [release candidate download](https://raw.githubusercontent.com/misteraddons/firmware/main/reflex-prism/latest/prism_dac.uf2) |
| Reflex Prism Flash Nuke | [`reflex-prism/latest/flash_nuke.uf2`](reflex-prism/latest/flash_nuke.uf2) | [stable download](https://raw.githubusercontent.com/misteraddons/firmware/main/reflex-prism/latest/flash_nuke.uf2) |
| Reflex Adapt Legacy / V1 | [`reflex-adapt-legacy/v2.01/`](reflex-adapt-legacy/v2.01/) | [`misteraddons/Reflex-Adapt-Legacy` v2.01](https://github.com/misteraddons/Reflex-Adapt-Legacy/releases/tag/v2.01) |
| Reflex CTRL Genesis 6 | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlGenesis6.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlGenesis6.uf2) |
| Reflex CTRL NES | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlNES.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlNES.uf2) |
| Reflex CTRL SNES | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSNES.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSNES.uf2) |
| Reflex CTRL Saturn | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSaturn.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSaturn.uf2) |
| Reflex CTRL Virtual Boy | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlVB.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlVB.uf2) |
| Reflex Encode | `reflex-encode/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV1.2.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV1.2.uf2) |
| Reflex Encode V2.0 | `reflex-encode/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV2.0.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV2.0.uf2) |
| MiSTercade V1 | `mistercade-v1/main-2025/_MiSTercade_V1-2025.zip` | [`misteraddons/MiSTercade` `_MiSTercade_V1-2025.zip`](https://github.com/misteraddons/MiSTercade/blob/main/mistercade_v1_2025/firmware/_MiSTercade_V1-2025.zip) |
| MiSTercade V2 firmware | `mistercade-v2/main/Scripts/_MiSTercade_V2_/GP2040-CE_latest_MiSTercadeV2.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_MiSTercadeV2.uf2) |
| MiSTercade V2 flash nuke | `mistercade-v2/main/Scripts/_MiSTercade_V2_/flash_nuke.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/flash_nuke.uf2) |
| MiSTercade V2 scripts | `mistercade-v2/main/Scripts/_MiSTercade_V2_/MiSTercade_V2_*.sh` | [`misteraddons/MiSTercadeV2` scripts folder](https://github.com/misteraddons/MiSTercadeV2/tree/main/Scripts/_MiSTercade_V2_) |

## Install Notes

### Prerequisites

Windows:

- Use the bundled `FirmwareInstaller.exe` from `FirmwareInstaller-windows.zip` when available; it includes Python, the catalog, the Windows GUI wrapper, and `pyserial`.
- To run from source, install Python 3 with Tkinter support, then install serial support:

```powershell
python -m pip install pyserial
python firmware_installer.py
```

macOS:

- Install Python 3.
- Install `pyserial`; Prism firmware updates require it for the USB CDC bootloader command and post-flash sanity check:

```sh
python3 -m pip install pyserial
python3 firmware_cli.py
```

On macOS, the RP2040 bootloader should appear as `/Volumes/RPI-RP2`. There is currently no signed/notarized macOS app or DMG; use the Terminal CLI from source.

Cross-platform firmware installer: run `python firmware_installer.py`, choose a product and firmware version, then connect each RP2040 board. On startup, the app checks every catalog item for updates in the background and caches anything newer while keeping bundled firmware available offline. It waits for `RPI-RP2`, copies the exact selected UF2, waits for the bootloader drive to detach, then waits for controller/gamepad enumeration or product-specific post-flash checks before showing a green check and returning to the next-device wait. Products with a `pre_flash_bootloader` catalog entry can also enter bootloader mode from a matching USB serial VID:PID before flashing.

The catalog is in `firmware_catalog.json`. GP2040-CE products ship local mirrors and can still resolve from the latest [`OpenStickCommunity/GP2040-CE`](https://github.com/OpenStickCommunity/GP2040-CE/releases/latest) release by asset name.

MiSTercade V1 and Reflex Adapt V1.x are 32u4 packages. The frontend can cache/download them, but this RPI-RP2 installer does not flash 32u4 firmware yet.

Terminal CLI mode is also available. With no arguments, `firmware_cli.py` lists available products:

```sh
python3 firmware_cli.py
python3 firmware_cli.py --list-catalog
python3 firmware_cli.py --product reflex-prism
python3 firmware_cli.py --product reflex-prism --version v1.10.9
python3 firmware_cli.py --refresh-all
python3 firmware_cli.py --firmware path/to/firmware.uf2
```

Flashing is continuous by default: after one device completes, the CLI waits for the next `RPI-RP2` bootloader drive or configured serial bootloader device. Add `--once` only when you want to flash one device and exit.

When `reflex-prism` is selected, the updater exposes every bundled or cached Prism version. The current `v1.11` release supports the listed Prism V1 hardware targets; legacy `v1.10.x` files are restricted to V1.05/V1.1 hardware. The updater watches for Prism USB CDC VID:PID `16D0:14F6`, verifies the connected hardware against the selected version, sends `bootloader`, flashes it, runs the Prism USB CDC sanity check, then waits for disconnect before arming for the next Prism. Catalog Prism firmware cannot start from an already-mounted BOOTSEL/manual `RPI-RP2` drive because the hardware target cannot be verified there; manually browsed custom UF2 files still work for recovery.

The original installer script also accepts the same headless arguments:

```sh
python firmware_installer.py --firmware path/to/firmware.uf2
python firmware_installer.py --list-catalog
python firmware_installer.py --product reflex-ctrl-nes --download
python firmware_installer.py --product reflex-ctrl-nes
```

Build a Windows executable:

```powershell
.\build_windows_exe.ps1
.\dist\FirmwareInstaller\FirmwareInstaller.exe
```

The executable is a PyInstaller onedir build. It bundles the catalog, checksums, complete mirrored firmware history, and Windows GUI script, then uses `FirmwareInstaller.exe` itself for catalog, download, and flash subprocesses. Downloads are stored by product and version and validated before replacing a cached file. No system Python install is required for the built app.
Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. The mirrored release candidate is `v1.11` and remains pending hardware testing. Use `flash_nuke.uf2` only as a last-resort full erase; it removes settings and Custom EDID and must be followed by `prism_dac.uf2`. Permanent downloads: https://raw.githubusercontent.com/misteraddons/firmware/main/reflex-prism/latest/prism_dac.uf2 and https://raw.githubusercontent.com/misteraddons/firmware/main/reflex-prism/latest/flash_nuke.uf2.

Reflex Prism update checks use the static `reflex-prism/latest/prism_dac.uf2` mirror. Versioned directories remain available for rollback, and `tools/sync_prism_firmware.py` updates the versioned copy, static aliases, catalog, README, and checksums together. Downloads and local UF2 files are validated as structurally valid UF2 images and, for RP2040 entries, must carry UF2 family ID `0xE48BFF56`. Before flashing Prism catalog firmware, the installer opens the normal USB CDC VID:PID `16D0:14F6`, runs `dashboard config get`, and blocks the update unless `Hardware target` is one of the supported Reflex Prism DAC V1 targets; after flashing, it opens the same serial console and sanity-checks `status` plus `dashboard config get`. Source runs need `pyserial`; the Windows bundled installer includes it.

Reflex Adapt: copy `reflex_updater.sh` to the `Scripts` folder on the MiSTer SD card, or use `reflex-v2.01.zip` for the desktop updater package.

MiSTercade V1: extract `_MiSTercade_V1-2025.zip` to the `Scripts` folder on the MiSTer SD card and run the included updater script.

MiSTercade V2: copy the `_MiSTercade_V2_` folder into the MiSTer SD card `Scripts` folder. Hold `JOY1 PROG`, `JOY2 PROG`, or both while powering on, then run `MiSTercade_V2_Program`.

## Verification

Checksums are in [`checksums.sha256`](checksums.sha256).

Run the source audit locally:

```sh
python tools/audit_firmware_sources.py
```

The `Audit Firmware Sources` GitHub Action runs the same check daily and on demand. It compares mirrored files against source release digests or source repo file hashes, verifies `checksums.sha256`, and validates RP2040 UF2 family IDs. If any source repo is private, set a `SOURCE_REPO_TOKEN` or `FIRMWARE_REPO_TOKEN` secret with read access to those repos.

The `Sync Prism Firmware` GitHub Action runs daily, on demand, and from `repository_dispatch` event type `reflex-prism-release`. It mirrors the latest or requested Reflex-Prism release, runs the source audit, and commits only when the mirror changes.

The `Build Windows Installer` GitHub Action builds `FirmwareInstaller-windows.zip` on demand. Pushing a tag named `installer-v*` or `firmware-v*` also creates a GitHub release with the installer zip, catalog, and checksums.

These files are mirrors of upstream firmware artifacts. Use upstream docs and release notes as the source of truth when flashing hardware.
