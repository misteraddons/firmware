# Adapt management identity integration

The shared schema-2 codec is maintained in the development firmware repository:
`Reflex-Adapt-Dev/web/management-identity.js`. Do not hand-edit the updater copy.
Its wire contract is in `Reflex-Adapt-Dev/docs/internal/management-identity.md`.

Use the dev `tools/sync_management_identity_codec.py --updater <firmware-repo>`
generator, and `--check` for parity. Offline Adapt.html embeds the same codec.
`tools/publish_firmware_dashboard.py` includes the codec in its export file list.

- Serial prefers the additive read-only `IDENTITY2` command. All fields come
  from that one device/response. Only an explicit unknown-command reply falls
  back to the older Classic2USB `INFO` and schema-1 `IDENTITY` flow.
- HID reads advertised E1/E2 feature reports from one selected handle and
  requires the UID in both to match. Neither report present means legacy E0;
  malformed/incomplete schema-2 data never downgrades to family-only approval.
- The catalog owns the exact `hardwareTargets` allowlist. Classic2USB currently
  accepts only `CLASSIC2USB_RP2040`; alternate pinouts and Nova are rejected.
  Do not infer board identity from shared VID/PID or accept arbitrary targets.
- The hardware target describes the installed firmware, not independently
  detected PCB wiring. UID is a stable unit correlation mechanism, not proof
  against tampering or a previously incorrect board flash.
- Failed/cancelled connection attempts clear the old selected identity.
  Reconnection during an approved manual update preserves the original approval
  and rejects another unit or changed hardware target. Verification uses the
  appropriate schema/transport rather than a Prism-only status command.
- Other products can use the same codec by adding explicitly reviewed catalog
  identity rules. This change approves no additional products or firmware files.

Tests: `node --test tests/*.test.mjs` and
`python -m unittest discover -s tests`. The application-handler tests simulate
device selection, legacy fallback, invalid targets and post-update reconnection;
they do not claim physical hardware acceptance.

Classic2USB still has no approved release in the catalog. Direct browser writes,
firmware readback and physical bootloader correlation remain separately gated.
Changes are local only; the live docs dashboard has not been redeployed.

## Classic2USB hardware check (2026-09-11)

The operator confirmed matching UID and build between local Adapt.html and
this dashboard, with firmware `1.0.0`, target `CLASSIC2USB_RP2040`, and build
`src-d5905494a50b50d54c77`. The UID remained stable after unplug/replug and
controller inputs worked normally. This validates the agreed new-firmware
identity smoke test only; legacy firmware and second-unit tests were excluded.
It does not enable browser flashing/backups or approve a firmware release.
