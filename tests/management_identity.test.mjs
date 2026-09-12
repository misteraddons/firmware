import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { identifyAdaptManagement, UpdateSession } from '../web/firmware-dashboard/core.js';

const classic = {
  id: 'classic', usbFilters: [{vendorId: 0x16d0, productId: 0x1460}],
  identity: {product:'CLASSIC2USB', mcu:'RP2040', baseVid:'16D0', basePid:'1460', hardwareTargets:['CLASSIC2USB_RP2040']},
  hardwareCheck: {acceptedTargets:[{group:'classic'}]}, releases:[],
};
const usb = {vendorId:0x16d0, productId:0x1460};
const response = 'IDENTITY SCHEMA=2 PRODUCT=CLASSIC2USB TARGET=CLASSIC2USB_RP2040 MCU=RP2040 UID=A1B2C3D4E5F60718 BASE_VID=16D0 BASE_PID=1460 CAPS=002F VERSION=1.0.0 BUILD=src-12345678901234567890';
const parsed = () => ReflexIdentity.parseSerial(response);

test('schema 2 identifies exact hardware and firmware in one response', () => {
  const value = identifyAdaptManagement([classic], parsed(), usb);
  assert.equal(value.uniqueId, 'A1B2C3D4E5F60718');
  assert.equal(value.target, 'CLASSIC2USB_RP2040');
  assert.equal(value.version, '1.0.0');
  assert.equal(value.build, 'src-12345678901234567890');
});
test('shared USB IDs never authorize Nova, alternate boards, or ambiguous products', () => {
  for (const patch of [{product:'REFLEX_NOVA'}, {target:'CLASSIC2USB_SONIK'}, {mcu:'RP2350'}, {pid:'14F6'}]) {
    assert.throws(() => identifyAdaptManagement([classic], {...parsed(), ...patch}, usb));
  }
  assert.throws(() => identifyAdaptManagement([classic, classic], parsed(), usb));
});
test('invalid or incomplete schema-2 identity never falls back to family detection', () => {
  for (const invalid of ['', 'ERR:UNKNOWN_CMD', response + '\n' + response,
    response.replace('SCHEMA=2', 'SCHEMA=1'), response.replace('A1B2C3D4E5F60718', 'FFFFFFFFFFFFFFFF'),
    response.replace('TARGET=CLASSIC2USB_RP2040 ', ''), response + ' UID=A1B2C3D4E5F60718']) {
    assert.throws(() => ReflexIdentity.parseSerial(invalid));
  }
});
test('HID builds use the same board ID as their identity report', () => {
  const a = new Uint8Array(63), b = new Uint8Array(63);
  a.set([0xAD,2,47,0]); b.set([0xAD,2]);
  const uid = Uint8Array.from(Buffer.from('A1B2C3D4E5F60718','hex'));
  a.set(uid,4); b.set(uid,2);
  const put = (bytes, offset, value) => bytes.set(new TextEncoder().encode(value), offset);
  put(a,12,'CLASSIC2USB'); put(a,28,'CLASSIC2USB_RP2040'); a[52]=1;
  a.set([0xd0,0x16,0x60,0x14],53);
  put(b,10,'1.0.0'); put(b,34,'src-12345678901234567890');
  assert.deepEqual(ReflexIdentity.parseReports(a,b),parsed());
  b[2]^=1;
  assert.throws(() => ReflexIdentity.parseReports(a,b), /mismatch/);
});
test('post-update verification rejects same UID with changed product or target', () => {
  const original=identifyAdaptManagement([classic],parsed(),usb);
  for (const patch of [{target:'CLASSIC2USB_SONIK'},{product:'MODERN2USB'}]) {
    const session=new UpdateSession(); session.connected(original); session.checked({version:'1.0.0'});
    assert.throws(() => session.verify({...original, management:{...original.management,...patch}},true), /target changed/);
  }
});
test('schema-2 transport is integrated without changing legacy firmware approval', () => {
  const app=fs.readFileSync(new URL('../web/firmware-dashboard/app.js',import.meta.url),'utf8');
  assert.match(app,/ReflexIdentity\.readHid\(selected\)/);
  assert.match(app,/'IDENTITY2'/);
  assert.match(app,/identifyClassicSerialProduct/);
  const catalog=JSON.parse(fs.readFileSync(new URL('../firmware_catalog.json',import.meta.url)));
  const entry=catalog.items.find(p=>p.id==='reflex-adapt-classic2usb');
  assert.deepEqual(entry.browser_identity.hardwareTargets,['CLASSIC2USB_RP2040']);
  assert.equal(entry.install_method,'coming_soon');
});
