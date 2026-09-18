# Browser firmware updater

Proposed URL: `https://docs.misteraddons.com/tools/firmware/`.
Separate app from Adapt.html and Prism.html; source belongs in this repository.
Status: design reviewed. PICOBOOT command framing, erase/write planning and the
transport class are implemented in `core.js` and unit-tested against a
simulated USB device (`tests/picoboot.test.mjs`).

The **read** path is hardware validated (2026-09-18, one Classic2USB, one
Windows host, desktop Chrome; `build/picoboot-hardware-probe-2026-09-18.log`).
Serving `core.js` from localhost and driving it from the browser, the shipping
module discovered the interface, claimed it, ran both control transfers and
read boot ROM back over bulk. The **write** path — erase, write, and the
readback-compare loop in `performDirectFlash()` — has never run against
hardware and remains simulation-only.

`directFlash` stays disabled in `manifest.json`, and no UI control invokes it.
A validated read path is not permission to flash: the association defect below
still makes the approval step unreachable.

## Reuse

- Generate an approved browser manifest from `firmware_catalog.json` and
  checksums, not a second independently maintained product catalog.
- Retain the installer's hardware-revision gates, UF2 validation and post-flash
  checks. The current catalog mixes GitHub releases, repository-file mirrors
  and download-only AVR packages; not every product uses the same update method.
- The current installer copies UF2 to BOOTSEL. It does not read firmware back.
  Downloading an older release is not a backup of the attached device.

## Flow

1. **Connect:** user grants USB/HID/serial access. VID:PID narrows candidates;
   query product, hardware revision, firmware version and unique identity.
   Shared VID:PID cannot select a product. bcdDevice is not a firmware version.
2. **Check:** show installed build, latest compatible stable release, hardware
   target and release notes. Unknown versions stay unknown. Prerelease builds
   require an explicit tester channel.
3. **Backup:** offer settings separately from firmware/full-flash readback.
   Only advertise supported methods. Save locally; never upload dumps or keys.
4. **Confirm:** show identified board, installed/target versions, backup status
   and migration warnings. No automatic flashing on connect. Ambiguity blocks it.
5. **Flash:** fully download and validate first (size, hash, UF2 block structure,
   family, flash ranges and product/hardware association). A hash from the same
   manifest provides integrity, not independent release authenticity.
6. **Verify:** reconnect to the same device and check its firmware version and
   product-specific health. Copy completion alone is not verified success.

## Transport and safety

- Start with desktop Chrome/Edge over HTTPS and existing management WebHID/CDC
  for identity/settings and bootloader entry. Application MSC is not required.
- Investigate WebUSB PICOBOOT for RP2040 read/write/verify. picotool demonstrates
  real program/full-flash readback, but browser support is a separate transport
  implementation. Windows WinUSB binding is confirmed: on 2026-09-18 an RP2040 in
  BOOTSEL exposed its PICOBOOT interface as vendor class 0xff already bound to
  WINUSB, with no Zadig step and no driver replacement, and Chrome claimed it.
  The bootrom reports that interface as `[out/bulk, in/bulk]` while its mass
  storage interface reports `[in/bulk, out/bulk]`, so the OUT-then-IN ordering
  `findPicobootInterface()` requires genuinely discriminates between them.
  Linux permissions remain untested. Do not replace a controller's normal USB
  driver indiscriminately.
- Fallback: validated UF2 download plus manual BOOTSEL copy, or explicitly
  selected BOOTSEL folder where browser support permits. Folder access cannot
  read existing flash. Preserve the verified identity over USB mode changes.
- RPI-RP2 identifies a bootloader/chip, not the product. Keep Prism's existing
  refusal to catalog-flash an unidentified BOOTSEL device. Recovery must be a
  distinct, explicit flow; never choose whichever RP2040 appears first.
- Preserve settings/authentication sectors during updates. Full-flash backups
  may contain keys or bonds: label sensitive and restrict restoration to the
  original board. Do not promise backup on protected/unsupported targets.
- Test release-asset CORS or use a narrowly scoped same-origin approved mirror.
  No unrestricted download proxy, embedded GitHub credentials or third-party
  analytics/scripts on pages with device access.

## Known defect: bootloader association cannot succeed

Measured on a Classic2USB, 2026-09-18 (`build/picoboot-hardware-probe-2026-09-18.log`).

`associateBootloaderTransition()` requires the PICOBOOT device's USB serial
number to equal the verified application UID. On RP2040 it does not: the
bootrom serial is 12 hex characters, application UIDs are contractually 16, and
the two values are unrelated — not a prefix, suffix or transform of each other.
`connectBootloader()` therefore always raises `bootloader-unassociated`, so the
association step is unreachable no matter how the transport behaves.

picotool does not trust that serial either; for RP2040 it reads the flash ID
over PICOBOOT and compares that instead. Repairing this means issuing PICOBOOT
commands to the board, so the correlation rule and the transport have to be
brought up together rather than separately.

Until then, treat any UID-to-bootloader correlation as unimplemented. Do not
relax the check to make it pass — an unverified correlation is what the gate
exists to prevent.

## Hardware notes

A Classic2USB only exposes management identity in some modes. With a controller
attached it passes through that controller's USB identity (an attached N64 pad
made it enumerate as a Nintendo VID/PID named "N64 Controller"), and the
dashboard's catalog filters will not list it. Removing the controller and
selecting DInput brings up the documented VID/PID with the CDC management
interface. Any operator instructions need to say this, or users will open the
tool, see an empty device picker and conclude the tool is broken.

The board's USB serial string in that mode is a product name, not a unique ID.

## Acceptance gates

- Unit tests: shared/ambiguous IDs, wrong revision/image/family/ranges, malformed
  UF2, hash mismatch, unknown/prerelease versions, missing backup capability,
  disconnects, failed backups and wrong version/device after reboot.
- Browser tests: permission cancellation, unsupported APIs, failed downloads,
  no automatic writes, local-only backups and reconnect permissions.
- Hardware: one verified board at a time, backup/restore on a spare before
  making backup claims, Windows/Linux and multiple attached adapters.
- No public flashing support until that product's exact flow passes acceptance.

## Primary references

- [Raspberry Pi picotool: save/load/verify and driver requirements](https://github.com/raspberrypi/picotool)
- [Chrome WebUSB device and driver requirements](https://developer.chrome.com/docs/capabilities/build-for-webusb)
- [Chrome Web Serial](https://developer.chrome.com/docs/capabilities/serial)
