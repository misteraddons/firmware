import test from 'node:test';
import assert from 'node:assert/strict';
import { DashboardError, PICOBOOT, PicobootTransport, decodePicobootStatus, encodePicobootCommand, findPicobootInterface, planFlashWrites } from '../web/firmware-dashboard/core.js';

function expectCode(fn, code) { return assert.rejects(fn, error => error instanceof DashboardError && error.code === code); }

test('encodePicobootCommand produces a 32-byte little-endian frame with args in place', () => {
  const { bytes, token } = encodePicobootCommand({ cmdId: PICOBOOT.cmd.write, args: Uint8Array.of(1, 2, 3, 4), transferLength: 256, token: 7 });
  const view = new DataView(bytes.buffer);
  assert.equal(bytes.length, 32);
  assert.equal(view.getUint32(0, true), PICOBOOT.magic);
  assert.equal(view.getUint32(4, true), 7); assert.equal(token, 7);
  assert.equal(bytes[8], PICOBOOT.cmd.write); assert.equal(bytes[9], 4);
  assert.equal(view.getUint32(12, true), 256);
  assert.deepEqual([...bytes.slice(16, 20)], [1, 2, 3, 4]);
  assert.deepEqual([...bytes.slice(20, 32)], new Array(12).fill(0));
});

test('decodePicobootStatus parses a 16-byte response and rejects the wrong length', () => {
  const bytes = new Uint8Array(16); const view = new DataView(bytes.buffer);
  view.setUint32(0, 9, true); view.setUint32(4, PICOBOOT.status.ok, true); view.setUint8(8, PICOBOOT.cmd.write); view.setUint8(9, 1);
  const status = decodePicobootStatus(view);
  assert.deepEqual(status, { token: 9, statusCode: 0, cmdId: PICOBOOT.cmd.write, inProgress: true });
  assert.throws(() => decodePicobootStatus(new Uint8Array(8)), error => error.code === 'picoboot-status');
});

test('findPicobootInterface picks the vendor interface with one bulk OUT and one bulk IN endpoint', () => {
  const massStorage = { interfaceNumber: 0, alternate: { interfaceClass: 0x08, endpoints: [] } };
  const picoboot = { interfaceNumber: 1, alternate: { interfaceClass: 0xff, endpoints: [
    { endpointNumber: 3, direction: 'out', type: 'bulk' }, { endpointNumber: 3, direction: 'in', type: 'bulk' },
  ] } };
  const found = findPicobootInterface({ interfaces: [massStorage, picoboot] });
  assert.deepEqual(found, { interfaceNumber: 1, outEndpoint: 3, inEndpoint: 3 });
  assert.throws(() => findPicobootInterface({ interfaces: [massStorage] }), error => error.code === 'picoboot-interface');
});

function uf2Block({ target, data }) {
  const bytes = new Uint8Array(512); const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x0a324655, true); view.setUint32(4, 0x9e5d5157, true); view.setUint32(8, 0x2000, true);
  view.setUint32(12, target, true); view.setUint32(16, data.length, true); view.setUint32(20, 0, true); view.setUint32(24, 1, true);
  view.setUint32(28, 0xe48bff56, true); view.setUint32(508, 0x0ab16f30, true); bytes.set(data, 32);
  return bytes;
}

test('planFlashWrites merges touched sectors into contiguous erase runs and preserves write payloads', () => {
  const first = uf2Block({ target: 0x10000000, data: Uint8Array.of(1, 2, 3) });
  const second = uf2Block({ target: 0x10001000, data: Uint8Array.of(4, 5, 6) });
  const image = new Uint8Array(first.length + second.length); image.set(first, 0); image.set(second, first.length);
  const plan = planFlashWrites(image);
  assert.deepEqual(plan.erases, [{ addr: 0x10000000, size: 0x2000 }]);
  assert.equal(plan.writes.length, 2);
  assert.equal(plan.writes[0].addr, 0x10000000); assert.deepEqual([...plan.writes[0].bytes], [1, 2, 3]);
  assert.equal(plan.writes[1].addr, 0x10001000); assert.deepEqual([...plan.writes[1].bytes], [4, 5, 6]);
});

test('planFlashWrites keeps sectors separate when the image does not touch what lies between them', () => {
  const first = uf2Block({ target: 0x10000000, data: Uint8Array.of(1) });
  const second = uf2Block({ target: 0x10010000, data: Uint8Array.of(2) });
  const image = new Uint8Array(first.length + second.length); image.set(first, 0); image.set(second, first.length);
  const plan = planFlashWrites(image);
  assert.deepEqual(plan.erases, [{ addr: 0x10000000, size: 0x1000 }, { addr: 0x10010000, size: 0x1000 }]);
});

function fakePicobootDevice() {
  const calls = []; let lastCmd = null; let forcedStatus = null;
  const iface = { interfaceNumber: 1, alternate: { interfaceClass: 0xff, endpoints: [
    { endpointNumber: 3, direction: 'out', type: 'bulk' }, { endpointNumber: 3, direction: 'in', type: 'bulk' },
  ] } };
  return {
    opened: false, configuration: null, calls,
    async open() { this.opened = true; },
    async selectConfiguration() { this.configuration = { interfaces: [iface] }; },
    async claimInterface(n) { calls.push(['claim', n]); },
    async controlTransferOut(setup) { calls.push(['controlOut', setup]); return { status: 'ok' }; },
    async controlTransferIn(setup, length) {
      calls.push(['controlIn', setup, length]);
      const bytes = new Uint8Array(16); const view = new DataView(bytes.buffer);
      const status = forcedStatus || { token: lastCmd.token, statusCode: PICOBOOT.status.ok, cmdId: lastCmd.cmdId, inProgress: 0 };
      view.setUint32(0, status.token, true); view.setUint32(4, status.statusCode, true); view.setUint8(8, status.cmdId); view.setUint8(9, status.inProgress);
      return { data: view };
    },
    async transferOut(endpointNumber, data) {
      calls.push(['out', endpointNumber, data.byteLength]);
      if (data.byteLength === 32) lastCmd = { token: new DataView(data.buffer, data.byteOffset, 32).getUint32(4, true), cmdId: data[8] };
      return { status: 'ok', bytesWritten: data.byteLength };
    },
    async transferIn(endpointNumber, length) {
      calls.push(['in', endpointNumber, length]);
      if (length === 1) return { status: 'ok', data: new DataView(new ArrayBuffer(0)) };
      const bytes = new Uint8Array(length).fill(0xab);
      return { status: 'ok', data: new DataView(bytes.buffer) };
    },
    forceStatus(status) { forcedStatus = status; },
  };
}

test('open() claims the discovered PICOBOOT interface', async () => {
  const device = fakePicobootDevice();
  const transport = new PicobootTransport(device);
  await transport.open();
  assert.equal(device.opened, true);
  assert.deepEqual(device.calls.find(call => call[0] === 'claim'), ['claim', 1]);
});

test('exclusiveAccess sends the 1-byte level argument and waits for the opposite-direction ACK', async () => {
  const device = fakePicobootDevice();
  const transport = new PicobootTransport(device);
  await transport.open();
  await transport.exclusiveAccess(2);
  const [, , sentLength] = device.calls.find(call => call[0] === 'out');
  assert.equal(sentLength, 32);
  const ack = device.calls.filter(call => call[0] === 'in');
  assert.deepEqual(ack[0], ['in', 3, 1]);
});

test('writeRange writes the command frame then the payload and validates status', async () => {
  const device = fakePicobootDevice();
  const transport = new PicobootTransport(device);
  await transport.open();
  const payload = Uint8Array.of(1, 2, 3, 4);
  await transport.writeRange(0x10000000, payload);
  const outCalls = device.calls.filter(call => call[0] === 'out');
  assert.equal(outCalls.length, 2);
  assert.equal(outCalls[0][2], 32); assert.equal(outCalls[1][2], payload.length);
});

test('readRange reads the payload via the IN endpoint after the command frame', async () => {
  const device = fakePicobootDevice();
  const transport = new PicobootTransport(device);
  await transport.open();
  const data = await transport.readRange(0x10000000, 8);
  assert.equal(data.length, 8); assert.equal(data[0], 0xab);
});

test('a non-OK PICOBOOT status is surfaced as picoboot-status, not swallowed', async () => {
  const device = fakePicobootDevice();
  const transport = new PicobootTransport(device);
  await transport.open();
  device.forceStatus({ token: 1, statusCode: PICOBOOT.status.badAlignment, cmdId: PICOBOOT.cmd.flashErase, inProgress: 0 });
  await expectCode(() => transport.eraseRange(0x10000000, 0x1000), 'picoboot-status');
});

test('a status token mismatch is rejected rather than attributed to the wrong command', async () => {
  const device = fakePicobootDevice();
  const transport = new PicobootTransport(device);
  await transport.open();
  device.forceStatus({ token: 999, statusCode: PICOBOOT.status.ok, cmdId: PICOBOOT.cmd.flashErase, inProgress: 0 });
  await expectCode(() => transport.eraseRange(0x10000000, 0x1000), 'picoboot-status');
});
