import { DashboardError, UpdateSession, associateBootloaderTransition, compareVersions, declareProduct, identifyAdaptManagement, identifyClassicSerialProduct, identifyProduct, identifyWebHidProduct, matchCandidates, selectRelease, validateDownload } from './core.js';

const state = { manifest: null, port: null, reader: null, hidDevice: null, bootloader: null, identity: null, release: null, image: null, session: new UpdateSession() };
const $ = selector => document.querySelector(selector);
const log = (message, tone = '') => {
  const item = document.createElement('li'); item.textContent = message; if (tone) item.dataset.tone = tone;
  $('#activity').prepend(item);
};
const setStatus = (text, tone = '') => { $('#status').textContent = text; $('#status').dataset.tone = tone; };

async function beginConnection() {
  // Do not leave a previously verified unit active after a cancelled/bad probe.
  if (state.reader) await state.reader.cancel().catch(() => {});
  if (state.port) await state.port.close().catch(() => {});
  if (state.hidDevice?.opened) await state.hidDevice.close().catch(() => {});
  state.port = null; state.hidDevice = null; state.identity = null; state.bootloader = null;
  if (state.session.phase !== 'flashing') {
    state.session.reset(); state.release = null; state.image = null;
  }
  for (const id of ['download', 'confirm', 'verify']) $('#' + id).disabled = true;
  $('#product-picker').value = '';
  renderIdentity();
}

function acceptIdentity(identity) {
  if (state.session.phase === 'flashing') {
    const approved = state.session.identity;
    if (identity.uniqueId !== approved.uniqueId || identity.product.id !== approved.product.id ||
        (approved.target && approved.target !== identity.target)) throw new DashboardError('wrong-device', 'Reconnect the approved physical unit and hardware target.');
    $('#verify').disabled = false;
  } else state.session.connected(identity);
  identity.product.connectedHardwareGroup = identity.hardware.group;
  state.identity = identity;
}

function renderCapabilities() {
  const hasApprovedRelease = !!(state.identity?.product.releases || []).length;
  const caps = [
    ['Settings backup', 'Implemented and simulated; disabled until hardware validation', false],
    ['Firmware backup', 'Disabled — application firmware readback is not implemented', false],
    ['Full-flash backup', 'Disabled — PICOBOOT readback requires hardware/driver validation', false],
    ['Direct browser flashing', 'PICOBOOT read/write/verify implemented and simulated; disabled until hardware validation', false],
    ['Validated UF2 download', hasApprovedRelease ? 'Available for an approved compatible release' : 'Unavailable until an approved compatible release exists', hasApprovedRelease],
  ];
  $('#capabilities').innerHTML = caps.map(([name, detail, enabled]) => `<div class="cap"><span>${name}</span><b class="${enabled ? 'yes' : 'no'}">${enabled ? 'Available' : 'Unavailable'}</b><small>${detail}</small></div>`).join('');
}

async function loadManifest() {
  const response = await fetch('./manifest.json', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Manifest request failed (${response.status})`);
  state.manifest = await response.json();
  const withReleases = state.manifest.products.filter(product => (product.releases || []).length);
  $('#catalog-count').textContent = `${state.manifest.products.length} approved catalog entries, ${withReleases.length} with a published release`;
  const picker = $('#product-picker');
  for (const product of withReleases) {
    const option = document.createElement('option');
    option.value = product.id;
    option.textContent = product.label;
    picker.append(option);
  }
}

// Reaching a release without a device: the operator names the product. Everything
// that needs a verified unit stays locked, because nothing here checked anything.
async function chooseProduct(productId) {
  await beginConnection();
  $('#product-picker').value = productId;
  if (!productId) { setStatus('Selection cleared. Nothing is selected.', ''); return; }
  const identity = declareProduct(state.manifest.products, productId);
  acceptIdentity(identity);
  renderIdentity(); renderUpdateAvailability();
  setStatus('Product set from your selection. No attached hardware was identified or checked.', 'warn');
  log(`Selected ${identity.product.label} by hand; no device identity was verified.`, 'warn');
}

async function readSerialResponse(port, command, timeoutMs = 8000) {
  const writer = port.writable.getWriter();
  await writer.write(new TextEncoder().encode(`${command}\r\n`)); writer.releaseLock();
  const reader = port.readable.getReader(); state.reader = reader;
  const decoder = new TextDecoder(); let output = ''; let idleTimer; let totalTimer;
  try {
    await new Promise((resolve, reject) => {
      totalTimer = setTimeout(resolve, timeoutMs);
      (async () => {
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) { resolve(); break; }
            output += decoder.decode(value, { stream: true });
            clearTimeout(idleTimer); idleTimer = setTimeout(resolve, 250);
          }
        } catch (error) { if (!output) reject(error); else resolve(); }
      })();
    });
  } finally { clearTimeout(idleTimer); clearTimeout(totalTimer); await reader.cancel().catch(() => {}); reader.releaseLock(); state.reader = null; }
  return output;
}

async function waitForSerialDisconnect(port, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!port.readable && !port.writable) return true;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  return false;
}

async function connectSerial() {
  if (!navigator.serial) throw new DashboardError('unsupported', 'Web Serial requires desktop Chrome or Edge over HTTPS.');
  await beginConnection();
  const filters = state.manifest.products.flatMap(product => product.usbFilters || []).map(filter => ({ usbVendorId: Number(filter.vendorId), usbProductId: Number(filter.productId) }));
  let port;
  try { port = await navigator.serial.requestPort({ filters }); }
  catch (error) { throw new DashboardError('permission-cancelled', error.name === 'NotFoundError' ? 'Device permission was cancelled.' : error.message); }
  const info = port.getInfo();
  await port.open({ baudRate: 115200, bufferSize: 65536 }); state.port = port;
  const candidates = matchCandidates(state.manifest.products, { vendorId: info.usbVendorId, productId: info.usbProductId });
  const modernIdentity = candidates.find(item => item.identity?.identityV2Command === 'IDENTITY2');
  if (modernIdentity) {
    const responseV2 = await readSerialResponse(port, modernIdentity.identity.identityV2Command, 2500);
    if (!/^ERR:UNKNOWN_CMD\s*$/i.test(responseV2.trim())) {
      const management = ReflexIdentity.parseSerial(responseV2);
      const identity = identifyAdaptManagement(state.manifest.products, management,
        { vendorId: info.usbVendorId, productId: info.usbProductId });
      acceptIdentity(identity);
      renderIdentity(); renderUpdateAvailability();
      setStatus('Product, build target and unit ID verified in one serial response.', 'ok');
      return;
    }
  }
  const classic = candidates.find(item => item.identity?.protocol === 'classic2usb-management-v1');
  if (classic) {
    const infoResponse = await readSerialResponse(port, classic.identity.infoCommand, 2500);
    const identityResponse = await readSerialResponse(port, classic.identity.identityCommand, 2500);
    const identity = identifyClassicSerialProduct(state.manifest.products, { usbInfo: { vendorId: info.usbVendorId, productId: info.usbProductId }, infoResponse, identityResponse });
    acceptIdentity(identity);
    renderIdentity(); renderUpdateAvailability();
    if (identity.identitySupport === 'verified') {
      setStatus('Product recognized and unique identity verified over one serial session.', 'ok');
      log(`Verified ${identity.product.label} UID ${identity.uniqueId}; firmware ${identity.version}.`, 'ok');
    } else {
      setStatus('Product recognized, but installed firmware lacks IDENTITY support. Manual updates are not identity-verified.', 'warn');
      log(`Recognized ${identity.product.label} firmware ${identity.version}; IDENTITY unsupported.`, 'warn');
    }
    return;
  }
  const product = candidates.find(item => item.identity?.command);
  if (!product?.identity?.command) throw new DashboardError('ambiguous-device', 'The selected USB ID has no approved browser identity query.');
  const response = await readSerialResponse(port, product.identity.command, product.identity.timeoutMs || 8000);
  const identity = identifyProduct(state.manifest.products, { usbInfo: { vendorId: info.usbVendorId, productId: info.usbProductId }, response });
  acceptIdentity(identity);
  renderIdentity(); renderUpdateAvailability();
  if (identity.identitySupport === 'verified') {
    setStatus('Product recognized and unique identity verified from one serial query.', 'ok');
    log(`Verified ${identity.product.label} UID ${identity.uniqueId}; firmware ${identity.version}.`, 'ok');
  } else {
    setStatus('Product recognized, but no unique identity was returned. Manual updates are not identity-verified.', 'warn');
    log(`Recognized ${identity.product.label} / ${identity.hardware.label}; unique identity unavailable.`, 'warn');
  }
}

async function connectHid() {
  if (!navigator.hid) throw new DashboardError('unsupported', 'WebHID requires desktop Chrome or Edge over HTTPS.');
  await beginConnection();
  const filters = state.manifest.products
    .filter(product => product.identity?.hidProductIds?.length)
    .flatMap(product => product.usbFilters || [])
    .map(filter => ({ vendorId: Number(filter.vendorId), productId: Number(filter.productId) }));
  let selected;
  try { [selected] = await navigator.hid.requestDevice({ filters }); }
  catch (error) { throw new DashboardError('permission-cancelled', error.name === 'NotFoundError' ? 'Device permission was cancelled.' : error.message); }
  if (!selected) throw new DashboardError('permission-cancelled', 'Device permission was cancelled.');
  try {
    if (!selected.opened) await selected.open();
    const management = await ReflexIdentity.readHid(selected);
    if (management) {
      const identity = identifyAdaptManagement(state.manifest.products, management,
        { vendorId: selected.vendorId, productId: selected.productId });
      acceptIdentity(identity); state.hidDevice = selected;
      renderIdentity(); renderUpdateAvailability();
      setStatus('Product, build target and unit ID verified over WebHID.', 'ok');
      return;
    }
    const report = await selected.receiveFeatureReport(0xe0);
    const identity = identifyWebHidProduct(state.manifest.products, {
      usbInfo: { vendorId: selected.vendorId, productId: selected.productId }, report: new Uint8Array(report.buffer),
    });
    acceptIdentity(identity); state.hidDevice = selected;
    renderIdentity(); renderUpdateAvailability(); setStatus('Product recognized over WebHID. Select its serial interface to verify unique identity.', 'warn');
    log(`Recognized ${identity.product.label} / ${identity.hardware.label}; WebHID identity is product-family only.`, 'warn');
  } catch (error) {
    if (selected.opened) await selected.close().catch(() => {});
    throw error;
  }
}

async function connectBootloader() {
  if (!navigator.usb) throw new DashboardError('unsupported', 'WebUSB requires desktop Chrome or Edge over HTTPS.');
  if (!state.identity?.uniqueId || !state.port || state.identity.identitySupport !== 'verified') throw new DashboardError('bootloader-unassociated', 'Verify this device through serial IDENTITY before associating PICOBOOT.');
  if (!window.confirm(`Put verified device ${state.identity.uniqueId} into PICOBOOT? This does not flash firmware.`)) throw new DashboardError('permission-cancelled', 'Bootloader association was cancelled.');
  const beforeDevices = await navigator.usb.getDevices();
  const response = await readSerialResponse(state.port, 'BOOTLOADER', 2500).catch(() => '');
  const sourceDisconnected = await waitForSerialDisconnect(state.port);
  const acknowledged = /(?:^|\n)OK:BOOTLOADER(?:\r?$|\n)/m.test(response) || sourceDisconnected;
  if (!acknowledged) throw new DashboardError('bootloader-unassociated', 'The verified serial device did not acknowledge the bootloader transition.');
  let selected;
  try { selected = await navigator.usb.requestDevice({ filters: [{ vendorId: 0x2e8a, productId: 0x0003 }] }); }
  catch (error) { throw new DashboardError('permission-cancelled', error.name === 'NotFoundError' ? 'Device permission was cancelled.' : error.message); }
  const device = associateBootloaderTransition({ identity: state.identity, transitionRequested: true, sourceDisconnected, beforeDevices, afterDevices: [selected] });
  state.bootloader = device; state.session.associateBootloader();
  setStatus('PICOBOOT is associated with the verified application UID. Browser flashing remains unavailable.', 'ok');
}

function renderIdentity() {
  const identity = state.identity;
  const declared = identity?.identitySupport === 'declared';
  $('#product').textContent = identity?.product.label || 'Not connected';
  $('#hardware').textContent = identity?.target || identity?.hardware.label || 'Unknown';
  $('#product-status').textContent = !identity ? 'Not recognized' : declared ? 'Stated by you, not detected' : 'Recognized';
  $('#identity-status').textContent = identity?.uniqueId ? 'Verified' : declared ? 'Not verified — no device was queried' : identity?.identitySupport === 'unsupported' ? 'Installed firmware lacks IDENTITY support' : identity ? 'Not verified' : 'Not checked';
  $('#unique-id').textContent = identity?.uniqueId || 'Unavailable';
  $('#installed').textContent = !identity ? 'Unknown (bcdDevice ignored)' : declared ? 'Unknown — nothing was read from a device' : `${identity.version || 'Unknown'}${identity.build ? ' / ' + identity.build : ''}`;
  $('#backup-settings').disabled = !identity?.product.backups?.settings?.supported;
  $('#check-update').disabled = !identity;
  $('#show-manual').disabled = !identity;
  $('#connect-usb').disabled = !identity?.uniqueId || !state.port;
}

function renderUpdateAvailability() {
  const hasRelease = !!(state.identity?.product.releases || []).length;
  renderCapabilities();
  $('#release-status').textContent = hasRelease ? 'Approved release available' : 'No approved firmware release available';
  $('#flash-status').textContent = state.identity?.product.directFlash?.supported ? 'Available' : 'Browser flashing transport unavailable';
  $('#latest').textContent = hasRelease ? 'Not checked' : 'None approved';
  $('#release-notes').textContent = hasRelease
    ? 'Check compatibility to select an approved release.'
    : `This catalog publishes no approved release for ${state.identity?.product.label || 'this product'}.`;
 }

function checkUpdate() {
  const release = selectRelease(state.identity.product, state.identity.hardware.group, 'stable');
  if (!release) { renderUpdateAvailability(); setStatus('Product recognized. No approved firmware release is available.', 'warn'); log('No approved compatible firmware release is available.', 'warn'); return; }
  state.release = release; state.session.checked(release);
  $('#latest').textContent = release.version;
  $('#release-notes').textContent = release.releaseNotes || 'No release notes supplied.';
  $('#download').disabled = false;
  $('#confirm').disabled = !state.identity.uniqueId;
  const comparison = state.identity.version ? compareVersions(state.identity.version, release.version) : null;
  setStatus(comparison === 0 ? 'Firmware is current. You may still download the validated recovery UF2.' : 'Compatible approved firmware is ready for validation.', 'ok');
}

function showManualFallback() {
  $('#manual').hidden = false;
  $('#manual-mode').textContent = state.identity?.uniqueId
    ? 'Identity was verified in application mode, but a manual mass-storage copy cannot preserve that association.'
    : state.identity?.identitySupport === 'declared'
      ? 'You selected this product yourself. Nothing checked what is actually attached, so confirm the board matches before copying.'
      : 'Installed firmware lacks unique identity support; this manual path is not identity-verified.';
  setStatus('Manual instructions shown. Nothing has been written to a device.', 'warn');
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
  if (!state.identity) throw new DashboardError('disconnect', 'Reconnect the same device first.');
  let identity;
  if (state.identity.management?.schema === 2) {
    let record, usbInfo;
    if (state.hidDevice) {
      record = await ReflexIdentity.readHid(state.hidDevice);
      usbInfo = {vendorId:state.hidDevice.vendorId, productId:state.hidDevice.productId};
    } else if (state.port) {
      record = ReflexIdentity.parseSerial(await readSerialResponse(state.port, 'IDENTITY2', 2500));
      const info = state.port.getInfo(); usbInfo = {vendorId:info.usbVendorId, productId:info.usbProductId};
    }
    if (!record) throw new DashboardError('missing-identity', 'Identity support disappeared after update.');
    identity = identifyAdaptManagement(state.manifest.products, record, usbInfo);
  } else {
    if (!state.port) throw new DashboardError('disconnect', 'Reconnect over serial first.');
    const info = state.port.getInfo(); const usbInfo = {vendorId:info.usbVendorId, productId:info.usbProductId};
    const rules = state.identity.product.identity;
    if (rules.protocol === 'classic2usb-management-v1') {
      const infoResponse = await readSerialResponse(state.port, rules.infoCommand, 2500);
      const identityResponse = await readSerialResponse(state.port, rules.identityCommand, 2500);
      identity = identifyClassicSerialProduct(state.manifest.products, {usbInfo, infoResponse, identityResponse});
    } else {
      const response = await readSerialResponse(state.port, rules.command, 8000);
      identity = identifyProduct(state.manifest.products, {usbInfo, response});
    }
  }
  const health = state.identity.product.postFlashCheck;
  for (const command of health?.commands || []) {
    if (!state.port) throw new DashboardError('health-check', 'This product requires a serial health check.');
    const output = await readSerialResponse(state.port, command.command, health.commandTimeoutMs || 8000);
    if (!(command.expect || []).every(marker => output.includes(marker))) throw new DashboardError('health-check', `Health command failed: ${command.command}`);
  }
  state.session.verify(identity, true); setStatus('Verified: same device and target version; configured health checks passed.', 'ok');
}

function safeName(value) { return String(value || 'unknown').replace(/[^a-z0-9._-]/gi, '_'); }
function downloadBlob(blob, name) { const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); }
function handle(action) { return async () => { try { await action(); } catch (error) { setStatus(error.message, 'error'); log(`${error.code || error.name || 'error'}: ${error.message}`, 'error'); } }; }

window.addEventListener('DOMContentLoaded', handle(async () => {
  renderCapabilities(); await loadManifest(); renderIdentity();
  $('#product-picker').addEventListener('change', handle(() => chooseProduct($('#product-picker').value)));
  $('#connect-serial').addEventListener('click', handle(connectSerial));
  $('#connect-hid').addEventListener('click', handle(connectHid));
  $('#connect-usb').addEventListener('click', handle(connectBootloader));
  $('#check-update').addEventListener('click', handle(checkUpdate));
  $('#backup-settings').addEventListener('click', handle(backupSettings));
  $('#download').addEventListener('click', handle(downloadFirmware));
  $('#confirm').addEventListener('click', handle(confirmUpdate));
  $('#show-manual').addEventListener('click', handle(showManualFallback));
  $('#verify').addEventListener('click', handle(verifyAfterFlash));
  setStatus('Ready. Connection never starts an update.', 'ok');
}));
