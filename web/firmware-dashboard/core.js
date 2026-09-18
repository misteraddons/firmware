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
