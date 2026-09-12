import { DashboardError, UpdateSession, compareVersions, identifyProduct, selectRelease, validateDownload } from './core.js';

const state = { manifest: null, port: null, reader: null, identity: null, release: null, image: null, session: new UpdateSession() };
const $ = selector => document.querySelector(selector);
const log = (message, tone = '') => {
  const item = document.createElement('li'); item.textContent = message; if (tone) item.dataset.tone = tone;
  $('#activity').prepend(item);
};
const setStatus = (text, tone = '') => { $('#status').textContent = text; $('#status').dataset.tone = tone; };

function renderCapabilities() {
  const caps = [
    ['Settings backup', 'Supported after verified Prism serial identification', true],
    ['Firmware backup', 'Disabled — application firmware readback is not implemented', false],
    ['Full-flash backup', 'Disabled — PICOBOOT readback requires hardware/driver validation', false],
    ['Direct browser flashing', 'Disabled — PICOBOOT write/verify is not hardware validated', false],
    ['Validated UF2 download', 'Supported; manual BOOTSEL copy required', true],
  ];
  $('#capabilities').innerHTML = caps.map(([name, detail, enabled]) => `<div class="cap"><span>${name}</span><b class="${enabled ? 'yes' : 'no'}">${enabled ? 'Available' : 'Unavailable'}</b><small>${detail}</small></div>`).join('');
}

async function loadManifest() {
  const response = await fetch('./manifest.json', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Manifest request failed (${response.status})`);
  state.manifest = await response.json();
  $('#catalog-count').textContent = `${state.manifest.products.length} approved catalog entries`;
}

async function readSerialResponse(port, command, timeoutMs = 8000) {
  const writer = port.writable.getWriter();
  await writer.write(new TextEncoder().encode(`${command}\r\n`)); writer.releaseLock();
  const reader = port.readable.getReader(); state.reader = reader;
  const decoder = new TextDecoder(); let output = ''; let idle;
  try {
    await Promise.race([
      (async () => { while (true) { const { value, done } = await reader.read(); if (done) break; output += decoder.decode(value, { stream: true }); clearTimeout(idle); } })(),
      new Promise(resolve => { idle = setTimeout(resolve, timeoutMs); }),
    ]);
  } finally { clearTimeout(idle); reader.releaseLock(); state.reader = null; }
  return output;
}

async function connectSerial() {
  if (!navigator.serial) throw new DashboardError('unsupported', 'Web Serial requires desktop Chrome or Edge over HTTPS.');
  const filters = state.manifest.products.flatMap(product => product.usbFilters || []).map(filter => ({ usbVendorId: Number(filter.vendorId), usbProductId: Number(filter.productId) }));
  let port;
  try { port = await navigator.serial.requestPort({ filters }); }
  catch (error) { throw new DashboardError('permission-cancelled', error.name === 'NotFoundError' ? 'Device permission was cancelled.' : error.message); }
  const info = port.getInfo();
  await port.open({ baudRate: 115200, bufferSize: 65536 }); state.port = port;
  const product = state.manifest.products.find(item => (item.usbFilters || []).some(filter => Number(filter.vendorId) === info.usbVendorId && Number(filter.productId) === info.usbProductId));
  if (!product?.identity?.command) throw new DashboardError('ambiguous-device', 'The selected USB ID has no approved browser identity query.');
  const response = await readSerialResponse(port, product.identity.command, product.identity.timeoutMs || 8000);
  const identity = identifyProduct(state.manifest.products, { usbInfo: { vendorId: info.usbVendorId, productId: info.usbProductId }, response });
  identity.product.connectedHardwareGroup = identity.hardware.group;
  state.identity = identity; state.session.connected(identity);
  renderIdentity(); setStatus('Device identified. Check for a compatible release.', 'ok');
  log(`Identified ${identity.product.label} / ${identity.hardware.label}.`, 'ok');
}

async function connectHid() {
  if (!navigator.hid) throw new DashboardError('unsupported', 'WebHID requires desktop Chrome or Edge over HTTPS.');
  try { await navigator.hid.requestDevice({ filters: [] }); }
  catch (error) { throw new DashboardError('permission-cancelled', error.name === 'NotFoundError' ? 'Device permission was cancelled.' : error.message); }
  throw new DashboardError('ambiguous-device', 'HID permission alone cannot identify a product. Use its approved query transport.');
}

async function connectBootloader() {
  if (!navigator.usb) throw new DashboardError('unsupported', 'WebUSB requires desktop Chrome or Edge over HTTPS.');
  try { await navigator.usb.requestDevice({ filters: [{ vendorId: 0x2e8a, productId: 0x0003 }] }); }
  catch (error) { throw new DashboardError('permission-cancelled', error.name === 'NotFoundError' ? 'Device permission was cancelled.' : error.message); }
  throw new DashboardError('bootloader-only', 'RPI-RP2/PICOBOOT identifies an RP2040, not the product. Connect the running device first.');
}

function renderIdentity() {
  const identity = state.identity;
  $('#product').textContent = identity?.product.label || 'Not connected';
  $('#hardware').textContent = identity?.hardware.label || 'Unknown';
  $('#unique-id').textContent = identity?.uniqueId || 'Unavailable — updating blocked';
  $('#installed').textContent = identity?.version || 'Unknown (bcdDevice ignored)';
  $('#backup-settings').disabled = !identity?.product.backups?.settings?.command;
  $('#check-update').disabled = !identity;
}

function checkUpdate() {
  const release = selectRelease(state.identity.product, state.identity.hardware.group, 'stable');
  if (!release) throw new DashboardError('no-release', 'No compatible approved stable release is available.');
  state.release = release; state.session.checked(release);
  $('#latest').textContent = release.version;
  $('#release-notes').textContent = release.releaseNotes || 'No release notes supplied.';
  $('#download').disabled = false;
  $('#confirm').disabled = !state.identity.uniqueId;
  const comparison = state.identity.version ? compareVersions(state.identity.version, release.version) : null;
  setStatus(comparison === 0 ? 'Firmware is current. You may still download the validated recovery UF2.' : 'Compatible approved firmware is ready for validation.', 'ok');
}

async function backupSettings() {
  const backup = state.identity.product.backups.settings;
  try {
    const content = await readSerialResponse(state.port, backup.command, backup.timeoutMs || 8000);
    if (!(backup.expect || []).every(marker => content.includes(marker))) throw new Error('Settings response was incomplete.');
    downloadBlob(new Blob([content], { type: 'text/plain' }), `${state.identity.product.id}-${safeName(state.identity.uniqueId)}-settings.txt`);
    state.session.backupResult('settings', true); log('Settings backup downloaded locally.', 'ok');
  } catch (error) { state.session.backupResult('settings', false); throw error; }
}

async function downloadFirmware() {
  const response = await fetch(state.release.url, { mode: 'cors', credentials: 'omit', cache: 'no-store' });
  if (!response.ok) throw new DashboardError('download', `Firmware download failed (${response.status}).`);
  const bytes = await response.arrayBuffer();
  await validateDownload(bytes, state.release, state.identity.product);
  state.image = bytes; downloadBlob(new Blob([bytes], { type: 'application/octet-stream' }), state.release.fileName);
  $('#confirm').disabled = !state.identity.uniqueId; log('UF2 checksum, structure, family, ranges, protected sectors, and hardware target passed.', 'ok');
}

function confirmUpdate() {
  if (!state.image) throw new DashboardError('state', 'Download and validate the UF2 first.');
  state.session.confirm();
  const approved = window.confirm(`Prepare manual update for ${state.identity.product.label}\n\nDevice: ${state.identity.uniqueId}\nHardware: ${state.identity.hardware.label}\nTarget: ${state.release.version}\n\nNo browser write will occur. Continue to manual BOOTSEL instructions?`);
  if (!approved) throw new DashboardError('permission-cancelled', 'Update confirmation was cancelled.');
  state.session.beginFlash();
  $('#manual').hidden = false; $('#verify').disabled = false;
  setStatus('Manual copy pending. The dashboard has not written to the device.', 'warn');
}

async function verifyAfterFlash() {
  if (!state.port) throw new DashboardError('disconnect', 'Reconnect the same device over serial first.');
  const response = await readSerialResponse(state.port, state.identity.product.identity.command, 8000);
  const info = state.port.getInfo();
  const identity = identifyProduct(state.manifest.products, { usbInfo: { vendorId: info.usbVendorId, productId: info.usbProductId }, response });
  const health = state.identity.product.postFlashCheck;
  for (const command of health.commands || []) {
    const output = await readSerialResponse(state.port, command.command, health.commandTimeoutMs || 8000);
    if (!(command.expect || []).every(marker => output.includes(marker))) throw new DashboardError('health-check', `Health command failed: ${command.command}`);
  }
  state.session.verify(identity, true); setStatus('Verified: same device, target version, and health checks passed.', 'ok');
}

function safeName(value) { return String(value || 'unknown').replace(/[^a-z0-9._-]/gi, '_'); }
function downloadBlob(blob, name) { const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); }
function handle(action) { return async () => { try { await action(); } catch (error) { setStatus(error.message, 'error'); log(`${error.code || error.name || 'error'}: ${error.message}`, 'error'); } }; }

window.addEventListener('DOMContentLoaded', handle(async () => {
  renderCapabilities(); await loadManifest(); renderIdentity();
  $('#connect-serial').addEventListener('click', handle(connectSerial));
  $('#connect-hid').addEventListener('click', handle(connectHid));
  $('#connect-usb').addEventListener('click', handle(connectBootloader));
  $('#check-update').addEventListener('click', handle(checkUpdate));
  $('#backup-settings').addEventListener('click', handle(backupSettings));
  $('#download').addEventListener('click', handle(downloadFirmware));
  $('#confirm').addEventListener('click', handle(confirmUpdate));
  $('#verify').addEventListener('click', handle(verifyAfterFlash));
  setStatus('Ready. Connection never starts an update.', 'ok');
}));
