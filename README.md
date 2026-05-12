# Firmware Mirror

Firmware files and a small cross-platform installer/cache for MiSTer Addons products.

## Current Files

| Project | Local file | Source |
| --- | --- | --- |
| Reflex Prism | `reflex-prism/v1.10.3/prism_dac.uf2` | [`misteraddons/Reflex-Prism` v1.10.3](https://github.com/misteraddons/Reflex-Prism/releases/tag/v1.10.3) |
| Reflex Adapt | `reflex-adapt/v2.01/reflex_updater.sh` | [`misteraddons/Reflex-Adapt` v2.01](https://github.com/misteraddons/Reflex-Adapt/releases/tag/v2.01) |
| Reflex Adapt | `reflex-adapt/v2.01/reflex-v2.01.zip` | [`misteraddons/Reflex-Adapt` v2.01](https://github.com/misteraddons/Reflex-Adapt/releases/tag/v2.01) |
| Reflex Adapt | `reflex-adapt/v2.01/reflex-v2.01.tar.gz` | [`misteraddons/Reflex-Adapt` v2.01](https://github.com/misteraddons/Reflex-Adapt/releases/tag/v2.01) |
| MiSTercade V1 | `mistercade-v1/main-2025/_MiSTercade_V1-2025.zip` | [`misteraddons/MiSTercadeV1`](https://github.com/misteraddons/MiSTercadeV1/commit/6b9ab2dd289305a56be9a2f173248b50224aae19), commit `6b9ab2d` |
| MiSTercade V2 | `mistercade-v2/main/Scripts/_MiSTercade_V2_/` | [`misteraddons/MiSTercadeV2`](https://github.com/misteraddons/MiSTercadeV2/commit/0f0f1d67660a182854cd08b4336bfc963d46312d), commit `0f0f1d` |

## Install Notes

Cross-platform firmware installer: run `python firmware_installer.py`, choose a product from the dropdown, then connect each RP2040 board in BOOTSEL mode. The app uses cached firmware when available, downloads the selected firmware when missing, waits for `RPI-RP2`, copies the UF2, waits for the bootloader drive to detach, then waits for controller/gamepad enumeration for controller firmware before showing a green check and returning to the next-drive wait.

The catalog is in `firmware_catalog.json`. GP2040-CE products resolve from the latest [`OpenStickCommunity/GP2040-CE`](https://github.com/OpenStickCommunity/GP2040-CE/releases/latest) release by asset name, so Encode and Reflex CTRL firmware do not need to be manually mirrored first.

MiSTercade V1 and Reflex Adapt V1.x are 32u4 packages. The frontend can cache/download them, but this RPI-RP2 installer does not flash 32u4 firmware yet.

Headless mode is also available:

```sh
python firmware_installer.py --firmware path/to/firmware.uf2
python firmware_installer.py --list-catalog
python firmware_installer.py --product reflex-ctrl-nes --download
python firmware_installer.py --product reflex-ctrl-nes --once
```

Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. The upstream v1.10.3 release updates the Prism USB VID/PID and built-in 4:3 EDID modes.

Reflex Adapt: copy `reflex_updater.sh` to the `Scripts` folder on the MiSTer SD card, or use `reflex-v2.01.zip` for the desktop updater package.

MiSTercade V1: extract `_MiSTercade_V1-2025.zip` to the `Scripts` folder on the MiSTer SD card and run the included updater script.

MiSTercade V2: copy the `_MiSTercade_V2_` folder into the MiSTer SD card `Scripts` folder. Hold `JOY1 PROG`, `JOY2 PROG`, or both while powering on, then run `MiSTercade_V2_Program`.

## Verification

Checksums are in [`checksums.sha256`](checksums.sha256).

These files are mirrors of upstream firmware artifacts. Use upstream docs and release notes as the source of truth when flashing hardware.
