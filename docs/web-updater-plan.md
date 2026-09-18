# Browser firmware updater

Proposed URL: `https://docs.misteraddons.com/tools/firmware/`.
Separate app from Adapt.html and Prism.html; source belongs in this repository.
Status: design reviewed. PICOBOOT command framing, erase/write planning and the
transport class are implemented in `core.js` and unit-tested against a
simulated USB device (`tests/picoboot.test.mjs`); no interface discovery,
control transfer or bulk transfer in that path has run against real hardware.
`directFlash` stays disabled in `manifest.json`, and no UI control invokes it.

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
  implementation. Validate Windows WinUSB binding and Linux permissions; do
  not replace a controller's normal USB driver indiscriminately.
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
