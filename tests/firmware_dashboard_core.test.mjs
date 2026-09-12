import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { DashboardError, SimulatedTransport, UpdateSession, compareVersions, identifyProduct, identifyWebHidProduct, parseWebHidDeviceInfo, validateUf2 } from '../web/firmware-dashboard/core.js';

const prism = {
  id: 'reflex-prism', label: 'Reflex Prism', usbFilters: [{ vendorId: 0x16d0, productId: 0x14f6 }],
  identity: { markers: ['=== Status ==='], versionPatterns: ['Firmware Version:\\s*v?([^\\s]+)'], uniqueIdPatterns: ['Board ID:\\s*([0-9A-F]{16})'] },
  hardwareCheck: { acceptedTargets: [{ group: 'prism-v11', label: 'V1.1', markers: ['Hardware target: V1.05/V1.1 boards'] }], knownMismatches: [{ group: 'prism-pro', label: 'Prism Pro', markers: ['Hardware target: Pro boards'] }] },
};

function expectCode(fn, code) { assert.throws(fn, error => error instanceof DashboardError && error.code === code); }
function uf2({ family = 0xe48bff56, target = 0x10000000, corrupt = false } = {}) {
  const bytes = new Uint8Array(512); const view = new DataView(bytes.buffer);
  view.setUint32(0, corrupt ? 0 : 0x0a324655, true); view.setUint32(4, 0x9e5d5157, true); view.setUint32(8, 0x2000, true);
  view.setUint32(12, target, true); view.setUint32(16, 256, true); view.setUint32(20, 0, true); view.setUint32(24, 1, true);
  view.setUint32(28, family, true); view.setUint32(508, 0x0ab16f30, true); return bytes;
}
const policy = { expectedFamily: 0xe48bff56, allowedFlashRanges: [{ start: 0x10000000, end: 0x10200000 }], protectedFlashRanges: [{ start: 0x101f0000, end: 0x10200000 }] };

test('shared VID PID remains ambiguous without exact query match', () => {
  const twin = structuredClone(prism); twin.id = 'other';
  expectCode(() => identifyProduct([prism, twin], { usbInfo: { vendorId: 0x16d0, productId: 0x14f6 }, response: '=== Status ===\nHardware target: V1.05/V1.1 boards' }), 'ambiguous-device');
});
test('query identifies product, hardware, version and unique identity', () => {
  const found = identifyProduct([prism], { usbInfo: { vendorId: 0x16d0, productId: 0x14f6 }, response: '=== Status ===\nFirmware Version: v1.11\nBoard ID: 0123456789ABCDEF\nHardware target: V1.05/V1.1 boards' });
  assert.equal(found.product.id, 'reflex-prism'); assert.equal(found.version, '1.11'); assert.equal(found.uniqueId, '0123456789ABCDEF');
});
test('Classic2USB WebHID device-info report identifies the product without trusting VID PID alone', () => {
  const report = new Uint8Array(63); report[0] = 0xad; report[1] = 3; report[2] = 2; report[3] = 4; report[4] = 1;
  report.set(new TextEncoder().encode('Classic2USB'), 30);
  const product = { id: 'reflex-adapt-classic2usb', usbFilters: [{ vendorId: 0x16d0, productId: 0x1460 }], identity: { transport: 'hid', webhidProductIds: ['Classic2USB'] }, hardwareCheck: { acceptedTargets: [{ group: 'classic2usb-published', label: 'All published Classic2USB revisions' }] } };
  const found = identifyWebHidProduct([product], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, report });
  assert.equal(found.product.id, product.id); assert.equal(found.version, '2.4.1'); assert.equal(found.uniqueId, null);
  assert.equal(parseWebHidDeviceInfo(report).productId, 'Classic2USB');
});
test('Classic2USB WebHID query rejects a shared VID PID with the wrong reported product', () => {
  const report = new Uint8Array(63); report[0] = 0xad; report.set(new TextEncoder().encode('DifferentProduct'), 30);
  const product = { usbFilters: [{ vendorId: 0x16d0, productId: 0x1460 }], identity: { transport: 'hid', webhidProductIds: ['Classic2USB'] } };
  expectCode(() => identifyWebHidProduct([product], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, report }), 'ambiguous-device');
});
test('incompatible hardware is rejected', () => expectCode(() => identifyProduct([prism], { usbInfo: { vendorId: 0x16d0, productId: 0x14f6 }, response: '=== Status ===\nHardware target: Pro boards' }), 'incompatible-hardware'));
test('corrupt UF2 is rejected', () => expectCode(() => validateUf2(uf2({ corrupt: true }), policy), 'corrupt-uf2'));
test('wrong family is rejected', () => expectCode(() => validateUf2(uf2({ family: 1 }), policy), 'wrong-family'));
test('out of range and protected writes are rejected', () => {
  expectCode(() => validateUf2(uf2({ target: 0x20000000 }), policy), 'flash-range');
  expectCode(() => validateUf2(uf2({ target: 0x101f0000 }), policy), 'protected-range');
});
test('semantic version comparison handles prereleases', () => {
  assert.equal(compareVersions('1.11.0', '1.10.10'), 1); assert.equal(compareVersions('1.11.0-beta.1', '1.11.0'), -1); assert.equal(compareVersions('v1.11', '1.11.0'), 0);
});
test('permission cancellation is represented without changing session state', () => {
  const session = new UpdateSession(); const error = new DashboardError('permission-cancelled', 'cancelled');
  assert.equal(error.code, 'permission-cancelled'); assert.equal(session.phase, 'idle');
});
test('disconnect while flashing fails', () => {
  const session = new UpdateSession(); session.connected({ uniqueId: 'A' }); session.checked({ version: '1.11' }); session.confirm(); session.beginFlash();
  expectCode(() => session.disconnected(), 'disconnect');
});
test('backup failures are blocking', () => { const session = new UpdateSession(); expectCode(() => session.backupResult('settings', false), 'backup-failed'); });
test('post-flash verification rejects wrong device, version, and health', () => {
  const ready = () => { const session = new UpdateSession(); session.connected({ uniqueId: 'A' }); session.checked({ version: '1.11' }); return session; };
  expectCode(() => ready().verify({ uniqueId: 'B', version: '1.11' }, true), 'wrong-device');
  expectCode(() => ready().verify({ uniqueId: 'A', version: '1.10' }, true), 'wrong-version');
  expectCode(() => ready().verify({ uniqueId: 'A', version: '1.11' }, false), 'health-check');
});
test('no flash can begin before explicit confirmation', () => { const session = new UpdateSession(); expectCode(() => session.beginFlash(), 'approval-required'); });
test('simulated transport covers permission cancellation, disconnects, backups and no implicit writes', async () => {
  await assert.rejects(() => new SimulatedTransport({ permission: 'cancel' }).requestPermission(), error => error.code === 'permission-cancelled');
  const disconnected = new SimulatedTransport({ disconnectOnQuery: true }); await disconnected.requestPermission();
  await assert.rejects(() => disconnected.query('status'), error => error.code === 'disconnect');
  const backup = new SimulatedTransport({ backupFailure: true }); await backup.requestPermission();
  await assert.rejects(() => backup.query('dashboard config get'), error => error.code === 'backup-failed');
  const safe = new SimulatedTransport(); await safe.requestPermission(); assert.equal(safe.writes.length, 0);
});
test('the exported approved Prism UF2 passes browser structural and range validation', () => {
  const manifest = JSON.parse(fs.readFileSync(new URL('../web/firmware-dashboard/manifest.json', import.meta.url)));
  const product = manifest.products.find(item => item.id === 'reflex-prism');
  const bytes = fs.readFileSync(new URL('../reflex-prism/v1.11/prism_dac.uf2', import.meta.url));
  const result = validateUf2(bytes, product.flashPolicy); assert.ok(result.blocks > 0); assert.ok(result.maxAddress < 0x101f0000);
});
