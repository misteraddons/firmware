/* Shared read-only Adapt management identity codec. No device writes. */
(() => {
  'use strict';
  const fail = message => { throw new Error(`Invalid Adapt identity: ${message}`); };
  const token = value => /^[A-Z0-9_]+$/.test(value);
  function validate(value) {
    if (value.schema !== 2 || !token(value.product) || !token(value.target) || value.mcu !== 'RP2040') fail('schema/product/target/MCU');
    if (!/^[0-9A-F]{16}$/.test(value.uid) || /^(..)(?:\1){7}$/.test(value.uid) || ['DEADBEEFDEADBEEF', '0123456789ABCDEF'].includes(value.uid)) fail('unit ID');
    if (!/^[0-9A-F]{4}$/.test(value.vid) || !/^[0-9A-F]{4}$/.test(value.pid) || !Number.isInteger(value.capabilities) || value.capabilities < 0 || value.capabilities > 65535) fail('USB IDs/capabilities');
    if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$/.test(value.version) || !/^[0-9A-Za-z._+-]+$/.test(value.build)) fail('version/build');
    return Object.freeze(value);
  }
  function payload(input, id) {
    let bytes = input instanceof Uint8Array ? input : input instanceof ArrayBuffer ? new Uint8Array(input) : new Uint8Array(input.buffer, input.byteOffset || 0, input.byteLength);
    if (bytes.length === 64 && bytes[0] === id) bytes = bytes.subarray(1);
    if (bytes.length !== 63 || bytes[0] !== 0xAD || bytes[1] !== 2) fail('report length/header');
    return bytes;
  }
  const hex = bytes => Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('').toUpperCase();
  const word = (bytes, offset) => bytes[offset] | (bytes[offset + 1] << 8);
  function text(bytes) {
    const end = bytes.indexOf(0);
    if (end < 0 || bytes.slice(end).some(n => n !== 0)) fail('unterminated field');
    return String.fromCharCode(...bytes.slice(0, end));
  }
  function parseReports(identity, build) {
    const a = payload(identity, 0xE1), b = payload(build, 0xE2);
    if (hex(a.slice(4, 12)) !== hex(b.slice(2, 10))) fail('report unit mismatch');
    if (a.slice(57).some(n => n !== 0)) fail('reserved bytes');
    return validate({ schema: 2, product: text(a.slice(12, 28)), target: text(a.slice(28, 52)),
      uid: hex(a.slice(4, 12)), mcu: a[52] === 1 ? 'RP2040' : '',
      vid: word(a, 53).toString(16).padStart(4, '0').toUpperCase(), pid: word(a, 55).toString(16).padStart(4, '0').toUpperCase(),
      capabilities: word(a, 2), version: text(b.slice(10, 34)), build: text(b.slice(34, 63)) });
  }
  function parseSerial(response) {
    const lines = String(response).split(/\r?\n/).map(s => s.trim()).filter(s => /^IDENTITY\b/.test(s));
    if (lines.length !== 1) fail('missing/ambiguous serial record');
    const fields = {};
    for (const part of lines[0].split(' ').slice(1)) {
      const pair = part.match(/^([A-Z_]+)=([^\s=]+)$/);
      if (!pair || Object.hasOwn(fields, pair[1])) fail('duplicate/malformed field');
      fields[pair[1]] = pair[2];
    }
    if (!/^[0-9A-F]{4}$/.test(fields.CAPS || '')) fail('capabilities');
    return validate({ schema: Number(fields.SCHEMA), product: fields.PRODUCT || '', target: fields.TARGET || '',
      uid: fields.UID || '', mcu: fields.MCU || '', vid: fields.BASE_VID || '', pid: fields.BASE_PID || '',
      capabilities: parseInt(fields.CAPS, 16), version: fields.VERSION || '', build: fields.BUILD || '' });
  }
  function featureIds(collections) {
    const ids = new Set();
    for (const c of collections || []) {
      for (const r of c.featureReports || []) ids.add(r.reportId);
      for (const id of featureIds(c.children)) ids.add(id);
    }
    return ids;
  }
  async function readHid(device, receive = id => device.receiveFeatureReport(id)) {
    const ids = featureIds(device.collections);
    if (!ids.has(0xE1) && !ids.has(0xE2)) return null; // Legacy firmware, not a UID.
    if (!ids.has(0xE1) || !ids.has(0xE2)) fail('incomplete descriptor');
    const identity = await receive(0xE1);
    const build = await receive(0xE2);
    return parseReports(identity, build);
  }
  globalThis.ReflexIdentity = Object.freeze({ parseReports, parseSerial, readHid, featureIds });
})();
