import './management-identity.js';

export function identifyAdaptManagement(products, management, usbInfo) {
  const candidates = matchCandidates(products, usbInfo).filter(product =>
    product.identity?.product === management.product);
  if (candidates.length !== 1) throw new DashboardError('ambiguous-device', 'Identity does not match exactly one approved product.');
  const product = candidates[0], rules = product.identity;
  if (management.mcu !== rules.mcu || management.vid !== rules.baseVid || management.pid !== rules.basePid ||
      !(rules.hardwareTargets || []).includes(management.target)) {
    throw new DashboardError('incompatible-hardware', 'Reported build target is not approved for this product.');
  }
  const targets = product.hardwareCheck?.acceptedTargets || [];
  if (targets.length !== 1) throw new DashboardError('unknown-hardware', 'Ambiguous hardware compatibility group.');
  return { product, hardware: targets[0], uniqueId: management.uid, version: management.version,
    identitySupport: 'verified', management, build: management.build, target: management.target };
}

export const UF2 = Object.freeze({
  blockSize: 512,
  magicStart0: 0x0a324655,
  magicStart1: 0x9e5d5157,
  magicEnd: 0x0ab16f30,
  familyFlag: 0x00002000,
});

export class DashboardError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'DashboardError';
    this.code = code;
  }
}

export function parseInteger(value) {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return Number.parseInt(value, 0);
  return NaN;
}

export function compareVersions(left, right) {
  const prerelease = value => String(value).replace(/^v/i, '').split('-', 2);
  const [la, lp = ''] = prerelease(left);
  const [ra, rp = ''] = prerelease(right);
  const ln = la.split('.').map(n => Number.parseInt(n, 10) || 0);
  const rn = ra.split('.').map(n => Number.parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(ln.length, rn.length); i += 1) {
    const delta = (ln[i] || 0) - (rn[i] || 0);
    if (delta) return Math.sign(delta);
  }
  if (lp === rp) return 0;
  if (!lp) return 1;
  if (!rp) return -1;
  return lp.localeCompare(rp, undefined, { numeric: true, sensitivity: 'base' });
}

export function matchCandidates(products, usbInfo) {
  return products.filter(product => (product.usbFilters || []).some(filter =>
    parseInteger(filter.vendorId) === usbInfo.vendorId &&
    parseInteger(filter.productId) === usbInfo.productId));
}

export function identifyProduct(products, probe) {
  const candidates = matchCandidates(products, probe.usbInfo || {});
  if (!candidates.length) throw new DashboardError('unknown-device', 'No approved product matches this USB filter.');
  const matches = candidates.filter(product => {
    const identity = product.identity || {};
    if (!identity.markers?.length) return false;
    return identity.markers.every(marker => probe.response.includes(marker));
  });
  if (matches.length !== 1) {
    throw new DashboardError('ambiguous-device', 'USB IDs are only a filter. The device query did not identify exactly one approved product.');
  }
  const product = matches[0];
  const hardware = identifyHardware(product, probe.response);
  const rawUniqueId = extractField(probe.response, product.identity.uniqueIdPatterns || []).toUpperCase();
  if (rawUniqueId && isPlaceholderUid(rawUniqueId)) throw new DashboardError('placeholder-identity', 'The serial device returned a placeholder unique ID.');
  const version = extractField(probe.response, product.identity.versionPatterns || []);
  return { product, hardware, uniqueId: rawUniqueId || null, version: version || null, identitySupport: rawUniqueId ? 'verified' : 'unsupported' };
}

export function parseWebHidDeviceInfo(input) {
  let data = input instanceof Uint8Array ? input : new Uint8Array(input.buffer, input.byteOffset || 0, input.byteLength);
  if (data[0] !== 0xad && (data[0] === 0xe0 || data[1] === 0xad)) data = data.slice(1);
  let shift = 0;
  if (data[0] !== 0xad) {
    if (data[0] === 1 && data.length >= 62) shift = -1;
    else throw new DashboardError('invalid-identity', 'The HID device did not return a valid RFLX device-info report.');
  }
  const at = index => data[index + shift] || 0;
  const text = (start, end) => String.fromCharCode(...data.slice(Math.max(0, start + shift), Math.max(0, end + shift))).replace(/\0/g, '').trim();
  const productId = text(30, 50);
  if (!productId) throw new DashboardError('invalid-identity', 'The HID device-info report did not contain a product identity.');
  return { protocolVersion: at(1), version: `${at(2)}.${at(3)}.${at(4)}`, controllerName: text(10, 30), productId };
}

export function identifyWebHidProduct(products, probe) {
  const info = parseWebHidDeviceInfo(probe.report);
  const candidates = matchCandidates(products, probe.usbInfo || {});
  const matches = candidates.filter(product =>
    (product.identity?.hidProductIds || []).includes(info.productId));
  if (matches.length !== 1) throw new DashboardError('ambiguous-device', 'The HID identity query did not identify exactly one approved product.');
  const product = matches[0];
  const targets = product.hardwareCheck?.acceptedTargets || [];
  if (targets.length !== 1) throw new DashboardError('unknown-hardware', 'The product hardware compatibility group could not be verified.');
  return { product, hardware: targets[0], uniqueId: null, version: info.version, controllerName: info.controllerName };
}

const PLACEHOLDER_UIDS = new Set(['0000000000000000', 'FFFFFFFFFFFFFFFF', 'DEADBEEFDEADBEEF', '0123456789ABCDEF']);
export function isPlaceholderUid(uid) {
  return PLACEHOLDER_UIDS.has(uid) || /^(..)(?:\1){7}$/.test(uid);
}

function identityLines(response) {
  return String(response || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
}

export function parseManagementIdentity(response, expected) {
  const responseLines = identityLines(response);
  const lines = responseLines.filter(line => /^IDENTITY\b/i.test(line));
  if (!lines.length && responseLines.some(line => /^ERR:UNKNOWN_CMD$/i.test(line))) throw new DashboardError('identity-unsupported', 'Installed firmware does not support the IDENTITY command.');
  if (!lines.length) throw new DashboardError('missing-identity', 'The serial device did not return an IDENTITY record.');
  if (lines.length !== 1) throw new DashboardError('ambiguous-identity', 'The serial device returned multiple identity records.');
  const match = lines[0].match(/^IDENTITY SCHEMA=(\d+) PRODUCT=([A-Z0-9_]+) MCU=([A-Z0-9]+) BASE_VID=([0-9A-F]{4}) BASE_PID=([0-9A-F]{4}) UID=([0-9A-F]{16})$/i);
  if (!match) throw new DashboardError('malformed-identity', 'The serial device returned a malformed IDENTITY record.');
  const identity = { schema: Number(match[1]), product: match[2].toUpperCase(), mcu: match[3].toUpperCase(), vid: match[4].toUpperCase(), pid: match[5].toUpperCase(), uid: match[6].toUpperCase() };
  const required = { schema: Number(expected.schema), product: String(expected.product).toUpperCase(), mcu: String(expected.mcu).toUpperCase(), vid: String(expected.baseVid).toUpperCase(), pid: String(expected.basePid).toUpperCase() };
  for (const field of ['schema', 'product', 'mcu', 'vid', 'pid']) {
    if (identity[field] !== required[field]) throw new DashboardError(field === 'product' ? 'wrong-product' : 'incompatible-hardware', `IDENTITY ${field} does not match the approved product.`);
  }
  if (isPlaceholderUid(identity.uid)) throw new DashboardError('placeholder-identity', 'The serial device returned a placeholder UID.');
  return identity;
}

export function parseManagementInfo(response, expected) {
  const lines = identityLines(response).filter(line => /^INFO\b/i.test(line));
  if (lines.length !== 1) throw new DashboardError(lines.length ? 'ambiguous-identity' : 'missing-product', 'The serial device did not return exactly one INFO record.');
  const match = lines[0].match(/^INFO PRODUCT=([A-Z0-9_]+) VERSION=([^\s]+) TAG=([^\s]*) HARDWARE="([^"]+)"$/i);
  if (!match) throw new DashboardError('malformed-identity', 'The serial device returned a malformed INFO record.');
  const product = match[1].toUpperCase();
  if (product !== String(expected.product).toUpperCase()) throw new DashboardError('wrong-product', 'INFO product does not match Classic2USB.');
  const hardware = match[4].trim();
  if (!(expected.hardwareStrings || []).includes(hardware)) throw new DashboardError('incompatible-hardware', `Unapproved Classic2USB hardware: ${hardware}.`);
  return { product, version: match[2], tag: match[3], hardware };
}

export function identifyClassicSerialProduct(products, probe) {
  const candidates = matchCandidates(products, probe.usbInfo || {}).filter(product => product.identity?.protocol === 'classic2usb-management-v1');
  if (candidates.length !== 1) throw new DashboardError('ambiguous-device', 'The selected serial USB ID does not identify exactly one Classic2USB catalog entry.');
  const product = candidates[0];
  const info = parseManagementInfo(probe.infoResponse, product.identity);
  let management = null; let identitySupport = 'verified';
  try { management = parseManagementIdentity(probe.identityResponse, product.identity); }
  catch (error) { if (error.code !== 'identity-unsupported') throw error; identitySupport = 'unsupported'; }
  const targets = product.hardwareCheck?.acceptedTargets || [];
  if (targets.length !== 1) throw new DashboardError('unknown-hardware', 'Classic2USB hardware compatibility is ambiguous.');
  return { product, hardware: targets[0], uniqueId: management?.uid || null, version: info.version, identitySupport, management, info };
}

function bootKey(device) {
  return `${Number(device.vendorId).toString(16)}:${Number(device.productId).toString(16)}:${String(device.serialNumber || '').replace(/[^0-9a-f]/gi, '').toUpperCase()}`;
}

export function associateBootloaderTransition({ identity, transitionRequested, sourceDisconnected, beforeDevices = [], afterDevices = [] }) {
  if (!identity?.uniqueId) throw new DashboardError('bootloader-unassociated', 'A verified application UID is required before bootloader association.');
  if (!transitionRequested || !sourceDisconnected) throw new DashboardError('bootloader-unassociated', 'The verified serial device did not perform the observed bootloader transition.');
  const before = new Set(beforeDevices.map(bootKey));
  const candidates = afterDevices.filter(device => Number(device.vendorId) === 0x2e8a && Number(device.productId) === 0x0003 && !before.has(bootKey(device)));
  if (candidates.length !== 1) throw new DashboardError('ambiguous-bootloader', 'Exactly one newly connected PICOBOOT device is required.');
  const serial = String(candidates[0].serialNumber || '').replace(/[^0-9a-f]/gi, '').toUpperCase();
  if (!serial || serial !== identity.uniqueId.toUpperCase()) throw new DashboardError('bootloader-unassociated', 'PICOBOOT did not expose the verified application UID; no browser write is allowed.');
  return candidates[0];
}

export function identifyHardware(product, response) {
  const rules = product.hardwareCheck?.acceptedTargets || [];
  const mismatches = product.hardwareCheck?.knownMismatches || [];
  const rejected = mismatches.find(rule => (rule.markers || []).some(marker => response.includes(marker)));
  if (rejected) throw new DashboardError('incompatible-hardware', `Connected hardware is ${rejected.label}; no compatible approved image is offered.`);
  const matches = rules.filter(rule => (rule.markers || []).some(marker => response.includes(marker)));
  if (matches.length !== 1) throw new DashboardError('unknown-hardware', 'The hardware revision could not be verified.');
  return matches[0];
}

function extractField(text, patterns) {
  for (const source of patterns) {
    const match = String(text).match(new RegExp(source, 'im'));
    if (match?.[1]) return match[1].trim();
  }
  return '';
}

export function selectRelease(product, hardwareGroup, channel = 'stable') {
  const releases = (product.releases || []).filter(release =>
    release.channel === channel && (release.hardwareGroups || [release.hardwareGroup]).includes(hardwareGroup));
  releases.sort((a, b) => compareVersions(b.version, a.version));
  return releases[0] || null;
}

function inRanges(start, end, ranges) {
  return ranges.some(range => start >= parseInteger(range.start) && end <= parseInteger(range.end));
}

function overlaps(start, end, ranges) {
  return ranges.some(range => start < parseInteger(range.end) && end > parseInteger(range.start));
}

export function validateUf2(bytes, policy) {
  const view = bytes instanceof DataView ? bytes : new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (!view.byteLength || view.byteLength % UF2.blockSize) throw new DashboardError('corrupt-uf2', 'UF2 length is empty or not a multiple of 512 bytes.');
  const seen = new Set();
  const families = new Set();
  let expectedBlocks = null;
  let minAddress = Number.MAX_SAFE_INTEGER;
  let maxAddress = 0;
  for (let offset = 0; offset < view.byteLength; offset += UF2.blockSize) {
    const number = offset / UF2.blockSize;
    if (view.getUint32(offset, true) !== UF2.magicStart0 ||
        view.getUint32(offset + 4, true) !== UF2.magicStart1 ||
        view.getUint32(offset + 508, true) !== UF2.magicEnd) {
      throw new DashboardError('corrupt-uf2', `Bad UF2 magic in block ${number + 1}.`);
    }
    const flags = view.getUint32(offset + 8, true);
    const target = view.getUint32(offset + 12, true);
    const payload = view.getUint32(offset + 16, true);
    const blockNo = view.getUint32(offset + 20, true);
    const total = view.getUint32(offset + 24, true);
    if (!payload || payload > 476 || !total || blockNo >= total) throw new DashboardError('corrupt-uf2', `Invalid UF2 fields in block ${number + 1}.`);
    if (expectedBlocks === null) expectedBlocks = total;
    if (expectedBlocks !== total || seen.has(blockNo)) throw new DashboardError('corrupt-uf2', 'UF2 block numbering is inconsistent or duplicated.');
    seen.add(blockNo);
    if (flags & UF2.familyFlag) families.add(view.getUint32(offset + 28, true));
    const end = target + payload;
    if (!inRanges(target, end, policy.allowedFlashRanges || [])) throw new DashboardError('flash-range', `UF2 writes outside the approved flash range at 0x${target.toString(16)}.`);
    if (overlaps(target, end, policy.protectedFlashRanges || [])) throw new DashboardError('protected-range', `UF2 overlaps a protected settings/authentication range at 0x${target.toString(16)}.`);
    minAddress = Math.min(minAddress, target);
    maxAddress = Math.max(maxAddress, end);
  }
  if (seen.size !== expectedBlocks) throw new DashboardError('corrupt-uf2', `UF2 is missing blocks (${seen.size}/${expectedBlocks}).`);
  const expectedFamily = parseInteger(policy.expectedFamily);
  if (!families.has(expectedFamily) || families.size !== 1) throw new DashboardError('wrong-family', 'UF2 family does not match the approved RP2040 family.');
  return { blocks: seen.size, minAddress, maxAddress, family: expectedFamily };
}

export async function sha256Hex(bytes) {
  const buffer = bytes instanceof ArrayBuffer ? bytes : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
}

export async function validateDownload(bytes, release, product) {
  const digest = await sha256Hex(bytes);
  if (digest.toLowerCase() !== release.sha256.toLowerCase()) throw new DashboardError('checksum', 'Downloaded firmware checksum does not match the approved manifest.');
  const inspected = validateUf2(new Uint8Array(bytes), product.flashPolicy);
  if (!(release.hardwareGroups || [release.hardwareGroup]).includes(product.connectedHardwareGroup)) throw new DashboardError('incompatible-image', 'Firmware image is not approved for the connected hardware revision.');
  return { digest, ...inspected };
}

export class UpdateSession {
  constructor() { this.reset(); }
  reset() {
    this.phase = 'idle'; this.identity = null; this.release = null; this.confirmed = false; this.bootloaderAssociated = false;
    this.backup = { settings: 'not-run', firmware: 'unsupported', fullFlash: 'unsupported' };
  }
  connected(identity) { this.identity = identity; this.phase = 'identified'; this.confirmed = false; }
  checked(release) { if (!this.identity) throw new DashboardError('state', 'Identify a device first.'); this.release = release; this.phase = 'ready'; }
  backupResult(kind, ok) { this.backup[kind] = ok ? 'complete' : 'failed'; if (!ok) throw new DashboardError('backup-failed', `${kind} backup failed.`); }
  confirm() { if (!this.release || !this.identity?.uniqueId) throw new DashboardError('state', 'A release and unique device identity are required.'); this.confirmed = true; }
  associateBootloader() { if (!this.identity?.uniqueId) throw new DashboardError('bootloader-unassociated', 'Verified identity is required.'); this.bootloaderAssociated = true; }
  beginFlash() { if (!this.confirmed) throw new DashboardError('approval-required', 'Explicit update approval is required.'); this.phase = 'flashing'; }
  beginDirectFlash() { if (!this.confirmed) throw new DashboardError('approval-required', 'Explicit update approval is required.'); if (!this.bootloaderAssociated) throw new DashboardError('bootloader-unassociated', 'The selected bootloader is not associated with the approved device.'); this.phase = 'flashing'; }
  disconnected() { if (this.phase === 'flashing') throw new DashboardError('disconnect', 'Device disconnected while updating.'); this.phase = 'disconnected'; }
  verify(identity, healthOk) {
    if (!this.identity || identity.uniqueId !== this.identity.uniqueId) throw new DashboardError('wrong-device', 'Reconnected device is not the device that was approved.');
    if (this.identity.management?.schema === 2 && ['product', 'target', 'mcu', 'vid', 'pid'].some(key =>
        identity.management?.[key] !== this.identity.management[key])) throw new DashboardError('wrong-device', 'Reconnected product/hardware target changed.');
    if (compareVersions(identity.version || '', this.release.version) !== 0) throw new DashboardError('wrong-version', 'Post-flash firmware version does not match the approved target.');
    if (!healthOk) throw new DashboardError('health-check', 'Post-flash health check failed.');
    this.phase = 'verified'; return true;
  }
}

export class SimulatedTransport {
  constructor(script = {}) { this.script = script; this.connected = false; this.writes = []; }
  async requestPermission() {
    if (this.script.permission === 'cancel') throw new DashboardError('permission-cancelled', 'Device permission was cancelled.');
    this.connected = true; return this.script.usbInfo || { vendorId: 0x16d0, productId: 0x14f6 };
  }
  async query(command) {
    if (!this.connected || this.script.disconnectOnQuery) throw new DashboardError('disconnect', 'Device disconnected during query.');
    if (this.script.backupFailure && command.includes('config')) throw new DashboardError('backup-failed', 'Settings backup failed.');
    return this.script.responses?.[command] || this.script.response || '';
  }
  async write(bytes) {
    if (!this.script.allowWrite) throw new DashboardError('unsupported', 'Simulated transport write is disabled.');
    this.writes.push(bytes); return bytes.byteLength;
  }
}

// PICOBOOT USB interface exposed by the RP2040/RP2350 bootrom (VID 0x2e8a PID 0x0003).
// Wire format from raspberrypi/pico-sdk src/common/boot_picoboot_headers/include/boot/picoboot.h;
// control/bulk sequencing from raspberrypi/picotool picoboot_connection/picoboot_connection.c.
// Implemented and exercised only against a fake USB device double (see tests/picoboot.test.mjs).
// Not hardware validated: directFlash stays disabled in manifest.json until a physical bring-up passes.
export const PICOBOOT = Object.freeze({
  magic: 0x431fd10b,
  ifReset: 0x41,
  ifCmdStatus: 0x42,
  cmd: Object.freeze({ exclusiveAccess: 0x1, reboot: 0x2, flashErase: 0x3, read: 0x84, write: 0x5, exitXip: 0x6, enterCmdXip: 0x7 }),
  status: Object.freeze({ ok: 0, unknownCmd: 1, invalidCmdLength: 2, invalidTransferLength: 3, invalidAddress: 4, badAlignment: 5, interleavedWrite: 6, rebooting: 7, unknownError: 8, invalidState: 9, notPermitted: 10, invalidArg: 11, bufferTooSmall: 12, preconditionNotMet: 13, modifiedData: 14, invalidData: 15, notFound: 16, unsupportedModification: 17 }),
  pageSize: 256,
  sectorSize: 4096,
});

let picobootToken = 1;

export function encodePicobootCommand({ cmdId, args = new Uint8Array(0), transferLength = 0, token = picobootToken++ }) {
  const bytes = new Uint8Array(32);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, PICOBOOT.magic, true);
  view.setUint32(4, token >>> 0, true);
  bytes[8] = cmdId;
  bytes[9] = args.length;
  view.setUint32(12, transferLength >>> 0, true);
  bytes.set(args.subarray(0, 16), 16);
  return { bytes, token };
}

export function decodePicobootStatus(data) {
  const view = data instanceof DataView ? data : new DataView(data.buffer, data.byteOffset || 0, data.byteLength);
  if (view.byteLength !== 16) throw new DashboardError('picoboot-status', 'PICOBOOT status response was not 16 bytes.');
  return { token: view.getUint32(0, true), statusCode: view.getUint32(4, true), cmdId: view.getUint8(8), inProgress: !!view.getUint8(9) };
}

function rangeArgs(addr, size) {
  const args = new Uint8Array(8);
  new DataView(args.buffer).setUint32(0, addr >>> 0, true);
  new DataView(args.buffer).setUint32(4, size >>> 0, true);
  return args;
}

// Turns an already-validated UF2 image (see validateUf2) into an ordered erase/write plan:
// erase covers the union of flash sectors the image touches, merged into contiguous runs.
export function planFlashWrites(bytes) {
  const view = bytes instanceof DataView ? bytes : new DataView(bytes.buffer, bytes.byteOffset || 0, bytes.byteLength);
  const writes = [];
  for (let offset = 0; offset < view.byteLength; offset += UF2.blockSize) {
    const target = view.getUint32(offset + 12, true);
    const payload = view.getUint32(offset + 16, true);
    writes.push({ addr: target, bytes: new Uint8Array(view.buffer, view.byteOffset + offset + 32, payload) });
  }
  writes.sort((a, b) => a.addr - b.addr);
  const sectors = new Set();
  for (const write of writes) {
    const start = Math.floor(write.addr / PICOBOOT.sectorSize) * PICOBOOT.sectorSize;
    const end = Math.ceil((write.addr + write.bytes.length) / PICOBOOT.sectorSize) * PICOBOOT.sectorSize;
    for (let sector = start; sector < end; sector += PICOBOOT.sectorSize) sectors.add(sector);
  }
  const erases = [];
  for (const sector of [...sectors].sort((a, b) => a - b)) {
    const last = erases[erases.length - 1];
    if (last && last.addr + last.size === sector) last.size += PICOBOOT.sectorSize;
    else erases.push({ addr: sector, size: PICOBOOT.sectorSize });
  }
  return { erases, writes };
}

// Finds the vendor-specific (class 0xff) interface exposing exactly one bulk OUT and one bulk
// IN endpoint, matching picoboot_connection.c's discovery rule without depending on interface index.
export function findPicobootInterface(configuration) {
  for (const iface of configuration?.interfaces || []) {
    const alt = iface.alternate;
    const endpoints = alt?.endpoints || [];
    if (alt?.interfaceClass !== 0xff || endpoints.length !== 2) continue;
    const [first, second] = endpoints;
    if (first.type === 'bulk' && first.direction === 'out' && second.type === 'bulk' && second.direction === 'in') {
      return { interfaceNumber: iface.interfaceNumber, outEndpoint: first.endpointNumber, inEndpoint: second.endpointNumber };
    }
  }
  throw new DashboardError('picoboot-interface', 'No PICOBOOT vendor interface with one bulk OUT and one bulk IN endpoint was found.');
}

// Thin wrapper over a WebUSB USBDevice (or a duck-typed double in tests). No caller in app.js
// invokes this yet; direct browser flashing stays behind manifest.json's disabled directFlash flag.
export class PicobootTransport {
  constructor(device) { this.device = device; this.interfaceInfo = null; }

  async open() {
    if (!this.device.opened) await this.device.open();
    if (this.device.configuration == null) await this.device.selectConfiguration(1);
    this.interfaceInfo = findPicobootInterface(this.device.configuration);
    await this.device.claimInterface(this.interfaceInfo.interfaceNumber);
  }

  async reset() {
    await this.device.controlTransferOut({ requestType: 'vendor', recipient: 'interface', request: PICOBOOT.ifReset, value: 0, index: this.interfaceInfo.interfaceNumber });
  }

  async status() {
    const result = await this.device.controlTransferIn({ requestType: 'vendor', recipient: 'interface', request: PICOBOOT.ifCmdStatus, value: 0, index: this.interfaceInfo.interfaceNumber }, 16);
    return decodePicobootStatus(result.data);
  }

  async runCommand({ cmdId, args, transferLength = 0, outData = null }) {
    const { bytes, token } = encodePicobootCommand({ cmdId, args, transferLength });
    const sent = await this.device.transferOut(this.interfaceInfo.outEndpoint, bytes);
    if (sent.status !== 'ok' || sent.bytesWritten !== bytes.byteLength) throw new DashboardError('picoboot-transfer', 'PICOBOOT command transfer failed.');
    let inData = null;
    if (transferLength) {
      if (cmdId & 0x80) {
        const result = await this.device.transferIn(this.interfaceInfo.inEndpoint, transferLength);
        if (result.status !== 'ok') throw new DashboardError('picoboot-transfer', 'PICOBOOT data read failed.');
        inData = new Uint8Array(result.data.buffer, result.data.byteOffset, result.data.byteLength);
      } else {
        const result = await this.device.transferOut(this.interfaceInfo.outEndpoint, outData);
        if (result.status !== 'ok' || result.bytesWritten !== outData.byteLength) throw new DashboardError('picoboot-transfer', 'PICOBOOT data write failed.');
      }
    }
    // The zero-length ACK travels opposite the data phase (or opposite the command itself when there is no data phase).
    if (cmdId & 0x80) await this.device.transferOut(this.interfaceInfo.outEndpoint, new Uint8Array(0));
    else await this.device.transferIn(this.interfaceInfo.inEndpoint, 1);
    const status = await this.status();
    if (status.token !== token || status.cmdId !== cmdId) throw new DashboardError('picoboot-status', 'PICOBOOT status did not match the issued command.');
    if (status.statusCode !== PICOBOOT.status.ok) throw new DashboardError('picoboot-status', `PICOBOOT command failed with status ${status.statusCode}.`);
    return inData;
  }

  exclusiveAccess(level = 2) { return this.runCommand({ cmdId: PICOBOOT.cmd.exclusiveAccess, args: Uint8Array.of(level) }); }
  eraseRange(addr, size) { return this.runCommand({ cmdId: PICOBOOT.cmd.flashErase, args: rangeArgs(addr, size) }); }
  writeRange(addr, bytes) { return this.runCommand({ cmdId: PICOBOOT.cmd.write, args: rangeArgs(addr, bytes.byteLength), transferLength: bytes.byteLength, outData: bytes }); }
  readRange(addr, size) { return this.runCommand({ cmdId: PICOBOOT.cmd.read, args: rangeArgs(addr, size), transferLength: size }); }
  reboot(pc = 0, sp = 0, delayMs = 500) {
    const args = new Uint8Array(12);
    new DataView(args.buffer).setUint32(0, pc >>> 0, true);
    new DataView(args.buffer).setUint32(4, sp >>> 0, true);
    new DataView(args.buffer).setUint32(8, delayMs >>> 0, true);
    return this.runCommand({ cmdId: PICOBOOT.cmd.reboot, args });
  }
}

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

// Erases, writes and reads back an already-validated UF2 image (see validateDownload) over an
// open PicobootTransport. Every written range is read back and compared before returning, so a
// write that silently did not take is reported rather than assumed. Does not reboot the device;
// callers decide when to call transport.reboot() once they are ready to hand control back.
export async function performDirectFlash(transport, image, { onProgress } = {}) {
  const plan = planFlashWrites(image);
  await transport.exclusiveAccess(2);
  for (const erase of plan.erases) {
    await transport.eraseRange(erase.addr, erase.size);
    onProgress?.({ phase: 'erase', addr: erase.addr, size: erase.size });
  }
  for (const write of plan.writes) {
    await transport.writeRange(write.addr, write.bytes);
    onProgress?.({ phase: 'write', addr: write.addr, size: write.bytes.length });
  }
  for (const write of plan.writes) {
    const readback = await transport.readRange(write.addr, write.bytes.length);
    if (!bytesEqual(readback, write.bytes)) throw new DashboardError('verify-mismatch', `Flash readback did not match the written image at 0x${write.addr.toString(16)}.`);
    onProgress?.({ phase: 'verify', addr: write.addr, size: write.bytes.length });
  }
  return plan;
}
