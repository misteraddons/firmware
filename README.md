# Firmware Mirror

Firmware files and a small cross-platform installer/cache for MiSTer Addons products.

## Current Files

| Project | Local file | Source |
| --- | --- | --- |
| Reflex Prism | `reflex-prism/v1.10.7/prism_dac.uf2` | [`misteraddons/Reflex-Prism` v1.10.7](https://github.com/misteraddons/Reflex-Prism/releases/tag/v1.10.7) |
| Reflex Adapt | `reflex-adapt/v2.01/reflex_updater.sh` | [`misteraddons/Reflex-Adapt-Legacy` v2.01](https://github.com/misteraddons/Reflex-Adapt-Legacy/releases/tag/v2.01) |
| Reflex Adapt | `reflex-adapt/v2.01/reflex-v2.01.zip` | [`misteraddons/Reflex-Adapt-Legacy` v2.01](https://github.com/misteraddons/Reflex-Adapt-Legacy/releases/tag/v2.01) |
| Reflex Adapt | `reflex-adapt/v2.01/reflex-v2.01.tar.gz` | [`misteraddons/Reflex-Adapt-Legacy` v2.01](https://github.com/misteraddons/Reflex-Adapt-Legacy/releases/tag/v2.01) |
| Reflex CTRL Genesis 6 | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlGenesis6.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlGenesis6.uf2) |
| Reflex CTRL NES | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlNES.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlNES.uf2) |
| Reflex CTRL SNES | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSNES.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSNES.uf2) |
| Reflex CTRL Saturn | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSaturn.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlSaturn.uf2) |
| Reflex CTRL Virtual Boy | `reflex-ctrl/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlVB.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexCtrlVB.uf2) |
| Reflex Encode | `reflex-encode/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV1.2.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV1.2.uf2) |
| Reflex Encode V2.0 | `reflex-encode/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV2.0.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_ReflexEncodeV2.0.uf2) |
| MiSTercade V1 | `mistercade-v1/main-2025/_MiSTercade_V1-2025.zip` | [`misteraddons/MiSTercadeV1`](https://github.com/misteraddons/MiSTercadeV1/commit/6b9ab2dd289305a56be9a2f173248b50224aae19), commit `6b9ab2d` |
| MiSTercade V2 firmware | `mistercade-v2/main/Scripts/_MiSTercade_V2_/GP2040-CE_latest_MiSTercadeV2.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/GP2040-CE_0.7.12_MiSTercadeV2.uf2) |
| MiSTercade V2 flash nuke | `mistercade-v2/main/Scripts/_MiSTercade_V2_/flash_nuke.uf2` | [`OpenStickCommunity/GP2040-CE` v0.7.12](https://github.com/OpenStickCommunity/GP2040-CE/releases/download/v0.7.12/flash_nuke.uf2) |
| MiSTercade V2 scripts | `mistercade-v2/main/Scripts/_MiSTercade_V2_/MiSTercade_V2_*.sh` | [`misteraddons/MiSTercadeV2`](https://github.com/misteraddons/MiSTercadeV2/commit/0f0f1d67660a182854cd08b4336bfc963d46312d), commit `0f0f1d` |

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
- Install `pyserial`; Prism firmware updates require it for the post-flash USB CDC sanity check:

```sh
python3 -m pip install pyserial
python3 firmware_cli.py
```

On macOS, the RP2040 bootloader should appear as `/Volumes/RPI-RP2`. There is currently no signed/notarized macOS app or DMG; use the Terminal CLI from source.

Cross-platform firmware installer: run `python firmware_installer.py`, choose a product from the dropdown, then connect each RP2040 board in BOOTSEL mode. The app uses cached firmware when available, downloads the selected firmware when missing, waits for `RPI-RP2`, copies the UF2, waits for the bootloader drive to detach, then waits for controller/gamepad enumeration for controller firmware before showing a green check and returning to the next-drive wait.

The catalog is in `firmware_catalog.json`. GP2040-CE products ship local mirrors and can still resolve from the latest [`OpenStickCommunity/GP2040-CE`](https://github.com/OpenStickCommunity/GP2040-CE/releases/latest) release by asset name.

MiSTercade V1 and Reflex Adapt V1.x are 32u4 packages. The frontend can cache/download them, but this RPI-RP2 installer does not flash 32u4 firmware yet.

Terminal CLI mode is also available. With no arguments, `firmware_cli.py` lists available products:

```sh
python3 firmware_cli.py
python3 firmware_cli.py --list-catalog
python3 firmware_cli.py --product reflex-prism
python3 firmware_cli.py --firmware path/to/firmware.uf2
```

Flashing is continuous by default: after one device completes, the CLI waits for the next `RPI-RP2` bootloader drive. Add `--once` only when you want to flash one device and exit.

When `reflex-prism` is selected, the CLI downloads the latest Prism `prism_dac.uf2` from this GitHub repo when needed, flashes it, and runs the Prism USB CDC sanity check.

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

The executable is a PyInstaller onedir build. It bundles the catalog and Windows GUI script, then uses `FirmwareInstaller.exe` itself for catalog, download, and flash subprocesses. No system Python install is required for the built app.
Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. The latest mirrored release is `v1.10.7`.

Reflex Prism update checks use `firmware_catalog.json` source type `github_repo_latest_semver_file`. The installer lists `misteraddons/firmware/reflex-prism`, sorts version directories semantically, and downloads `prism_dac.uf2` from the highest version directory. `tools/sync_prism_firmware.py` mirrors the latest `misteraddons/Reflex-Prism` release into this repo and updates the catalog, README, and checksums together. Downloads and local UF2 files are validated as structurally valid UF2 images and, for RP2040 entries, must carry UF2 family ID `0xE48BFF56`. After flashing Prism, the installer waits for USB CDC VID:PID `16D0:14F6`, opens the serial console, and sanity-checks `status` plus `dashboard config get`. Source runs need `pyserial`; the Windows bundled installer includes it.

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
