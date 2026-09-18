import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { DashboardError, SimulatedTransport, UpdateSession, associateBootloaderTransition, compareVersions, identifyClassicSerialProduct, identifyProduct, identifyWebHidProduct, parseManagementIdentity, parseWebHidDeviceInfo, selectRelease, validateUf2 } from '../web/firmware-dashboard/core.js';

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
  const found = identifyProduct([prism], { usbInfo: { vendorId: 0x16d0, productId: 0x14f6 }, response: '=== Status ===\nFirmware Version: v1.11\nBoard ID: FEDCBA9876543210\nHardware target: V1.05/V1.1 boards' });
  assert.equal(found.product.id, 'reflex-prism'); assert.equal(found.version, '1.11'); assert.equal(found.uniqueId, 'FEDCBA9876543210'); assert.equal(found.identitySupport, 'verified');
});
test('placeholder Prism unique ID is rejected, not silently accepted', () => {
  expectCode(() => identifyProduct([prism], { usbInfo: { vendorId: 0x16d0, productId: 0x14f6 }, response: '=== Status ===\nFirmware Version: v1.11\nBoard ID: 0000000000000000\nHardware target: V1.05/V1.1 boards' }), 'placeholder-identity');
});
test('missing Prism unique ID leaves identity unsupported rather than implicitly verified', () => {
  const found = identifyProduct([prism], { usbInfo: { vendorId: 0x16d0, productId: 0x14f6 }, response: '=== Status ===\nFirmware Version: v1.11\nHardware target: V1.05/V1.1 boards' });
  assert.equal(found.uniqueId, null); assert.equal(found.identitySupport, 'unsupported');
});
test('Classic2USB WebHID device-info report identifies the product without trusting VID PID alone', () => {
  const report = new Uint8Array(63); report[0] = 0xad; report[1] = 3; report[2] = 2; report[3] = 4; report[4] = 1;
  report.set(new TextEncoder().encode('Classic2USB'), 30);
  const product = { id: 'reflex-adapt-classic2usb', usbFilters: [{ vendorId: 0x16d0, productId: 0x1460 }], identity: { hidProductIds: ['Classic2USB'] }, hardwareCheck: { acceptedTargets: [{ group: 'classic2usb-published', label: 'All published Classic2USB revisions' }] } };
  const found = identifyWebHidProduct([product], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, report });
  assert.equal(found.product.id, product.id); assert.equal(found.version, '2.4.1'); assert.equal(found.uniqueId, null);
  assert.equal(parseWebHidDeviceInfo(report).productId, 'Classic2USB');
});
test('Classic2USB WebHID query rejects a shared VID PID with the wrong reported product', () => {
  const report = new Uint8Array(63); report[0] = 0xad; report.set(new TextEncoder().encode('DifferentProduct'), 30);
  const product = { usbFilters: [{ vendorId: 0x16d0, productId: 0x1460 }], identity: { hidProductIds: ['Classic2USB'] } };
  expectCode(() => identifyWebHidProduct([product], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, report }), 'ambiguous-device');
});
const classicSerial = { id: 'reflex-adapt-classic2usb', usbFilters: [{ vendorId: 0x16d0, productId: 0x1460 }], releases: [], identity: { protocol: 'classic2usb-management-v1', schema: 1, product: 'CLASSIC2USB', mcu: 'RP2040', baseVid: '16D0', basePid: '1460', hardwareStrings: ['RP2040 2MB'] }, hardwareCheck: { acceptedTargets: [{ group: 'classic2usb-published', label: 'All published Classic2USB revisions' }] } };
const classicInfo = 'INFO PRODUCT=Classic2USB VERSION=2.4.1 TAG=stable HARDWARE="RP2040 2MB"';
const classicIdentity = 'IDENTITY SCHEMA=1 PRODUCT=Classic2USB MCU=RP2040 BASE_VID=16D0 BASE_PID=1460 UID=A1B2C3D4E5F60718';
test('valid Classic2USB identity and version are accepted from one serial probe', () => {
  const found = identifyClassicSerialProduct([classicSerial], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, infoResponse: classicInfo, identityResponse: classicIdentity });
  assert.equal(found.uniqueId, 'A1B2C3D4E5F60718'); assert.equal(found.version, '2.4.1'); assert.equal(found.identitySupport, 'verified');
});
test('unsupported IDENTITY retains product recognition but not unique identity', () => {
  const found = identifyClassicSerialProduct([classicSerial], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, infoResponse: classicInfo, identityResponse: 'ERR:UNKNOWN_CMD' });
  assert.equal(found.product.id, classicSerial.id); assert.equal(found.uniqueId, null); assert.equal(found.identitySupport, 'unsupported');
});
test('malformed, placeholder, wrong-product and ambiguous identities are rejected', () => {
  const expected = classicSerial.identity;
  expectCode(() => parseManagementIdentity('', expected), 'missing-identity');
  expectCode(() => parseManagementIdentity('IDENTITY broken', expected), 'malformed-identity');
  expectCode(() => parseManagementIdentity(classicIdentity.replace('A1B2C3D4E5F60718', '0000000000000000'), expected), 'placeholder-identity');
  expectCode(() => parseManagementIdentity(classicIdentity.replace('Classic2USB', 'MODERN2USB'), expected), 'wrong-product');
  expectCode(() => parseManagementIdentity(`${classicIdentity}\n${classicIdentity}`, expected), 'ambiguous-identity');
});
test('wrong Classic2USB hardware and multiple catalog matches are rejected', () => {
  expectCode(() => identifyClassicSerialProduct([classicSerial], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, infoResponse: classicInfo.replace('RP2040 2MB', 'RP2350'), identityResponse: classicIdentity }), 'incompatible-hardware');
  expectCode(() => identifyClassicSerialProduct([classicSerial, structuredClone(classicSerial)], { usbInfo: { vendorId: 0x16d0, productId: 0x1460 }, infoResponse: classicInfo, identityResponse: classicIdentity }), 'ambiguous-device');
});
test('bootloader association requires one new device exposing the approved UID', () => {
  const identity = { uniqueId: 'A1B2C3D4E5F60718' };
  const target = { vendorId: 0x2e8a, productId: 3, serialNumber: identity.uniqueId };
  assert.equal(associateBootloaderTransition({ identity, transitionRequested: true, sourceDisconnected: true, afterDevices: [target] }), target);
  expectCode(() => associateBootloaderTransition({ identity, transitionRequested: true, sourceDisconnected: true, afterDevices: [target, { ...target, serialNumber: '1111222233334444' }] }), 'ambiguous-bootloader');
  expectCode(() => associateBootloaderTransition({ identity, transitionRequested: true, sourceDisconnected: true, afterDevices: [{ ...target, serialNumber: '1111222233334444' }] }), 'bootloader-unassociated');
  expectCode(() => associateBootloaderTransition({ identity, transitionRequested: true, sourceDisconnected: false, afterDevices: [target] }), 'bootloader-unassociated');
});
test('Classic2USB has no approved release', () => assert.equal(selectRelease(classicSerial, 'classic2usb-published'), null));
test('selectRelease picks the highest stable version for the matching hardware group', () => {
  const product = { releases: [
    { version: '1.10.0', channel: 'stable', hardwareGroups: ['prism-v11'] },
    { version: '1.11.0', channel: 'stable', hardwareGroups: ['prism-v11'] },
    { version: '1.9.0', channel: 'stable', hardwareGroups: ['prism-v11'] },
  ] };
  assert.equal(selectRelease(product, 'prism-v11').version, '1.11.0');
});
test('selectRelease excludes a newer prerelease unless the tester channel is explicitly requested', () => {
  const product = { releases: [
    { version: '1.11.0', channel: 'stable', hardwareGroups: ['prism-v11'] },
    { version: '1.12.0-beta.1', channel: 'prerelease', hardwareGroups: ['prism-v11'] },
  ] };
  assert.equal(selectRelease(product, 'prism-v11').version, '1.11.0');
  assert.equal(selectRelease(product, 'prism-v11', 'prerelease').version, '1.12.0-beta.1');
});
test('selectRelease excludes a release not approved for the connected hardware group', () => {
  const product = { releases: [{ version: '1.11.0', channel: 'stable', hardwareGroups: ['prism-v12'] }] };
  assert.equal(selectRelease(product, 'prism-v11'), null);
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
  const session = new UpdateSession(); session.connected({ uniqueId: 'A' }); session.checked({ version: '1.11' }); session.confirm(); session.associateBootloader(); session.beginFlash();
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
test('confirmed direct flash remains blocked until bootloader association', () => { const session = new UpdateSession(); session.connected({ uniqueId: 'A' }); session.checked({ version: '1.11' }); session.confirm(); expectCode(() => session.beginDirectFlash(), 'bootloader-unassociated'); });
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
