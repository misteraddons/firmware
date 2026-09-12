import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as core from '../web/firmware-dashboard/core.js';

const catalog = JSON.parse(fs.readFileSync(new URL('../web/firmware-dashboard/manifest.json',import.meta.url)));
const serialV2 = 'IDENTITY SCHEMA=2 PRODUCT=CLASSIC2USB TARGET=CLASSIC2USB_RP2040 MCU=RP2040 UID=A1B2C3D4E5F60718 BASE_VID=16D0 BASE_PID=1460 CAPS=002F VERSION=1.0.0 BUILD=src-12345678901234567890';
function harness() {
  const elements = new Map(), commands = [];
  const port = {getInfo:()=>({usbVendorId:0x16d0,usbProductId:0x1460}), open:async()=>{},close:async()=>{},responses:{IDENTITY2:serialV2}};
  const fake = () => ({textContent:'',disabled:false,dataset:{},prepend(){},addEventListener(){}});
  const context = vm.createContext({...core, ReflexIdentity:globalThis.ReflexIdentity, console,
    document:{querySelector(id){if(!elements.has(id))elements.set(id,fake());return elements.get(id);},createElement:fake},
    window:{addEventListener(){}},navigator:{serial:{requestPort:async()=>port}},
  });
  const app = fs.readFileSync(new URL('../web/firmware-dashboard/app.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/, '');
  vm.runInContext(app + '\nglobalThis.hooks={state,connectSerial,connectHid,verifyAfterFlash};',context);
  context.hooks.state.manifest = structuredClone(catalog);
  context.readSerialResponse = async (device, command) => {assert.equal(device,port);commands.push(command);return port.responses[command] || '';};
  return {context,port,commands,elements,...context.hooks};
}
function hidDevice() {
  const a = new Uint8Array(63), b = new Uint8Array(63), calls = [];
  a.set([0xAD,2,47,0]);b.set([0xAD,2]);
  const uid=Uint8Array.from(Buffer.from('A1B2C3D4E5F60718','hex'));a.set(uid,4);b.set(uid,2);
  const put=(bytes,offset,text)=>bytes.set(new TextEncoder().encode(text),offset);
  put(a,12,'CLASSIC2USB');put(a,28,'CLASSIC2USB_RP2040');a[52]=1;a.set([0xd0,0x16,0x60,0x14],53);
  put(b,10,'1.0.0');put(b,34,'src-12345678901234567890');
  return {vendorId:0x16d0,productId:0x1460,opened:false,
    collections:[{featureReports:[{reportId:0xE1},{reportId:0xE2}]}],
    async open(){this.opened=true;},async close(){this.opened=false;},
    async receiveFeatureReport(id){calls.push(id);return id===0xE1?a:b;},calls,a,b};
}
test('actual serial UI uses one complete identity record without a HID/version merge',async()=>{
  const h=harness();await h.connectSerial();
  assert.deepEqual(h.commands,['IDENTITY2']);
  assert.equal(h.state.identity.uniqueId,'A1B2C3D4E5F60718');
  assert.equal(h.elements.get('#hardware').textContent,'CLASSIC2USB_RP2040');
  assert.match(h.elements.get('#installed').textContent,/src-/);
});
test('actual serial UI retains the legacy command path',async()=>{
  const h=harness();h.port.responses={IDENTITY2:'ERR:UNKNOWN_CMD',
    INFO:'INFO PRODUCT=Classic2USB VERSION=1.0.0 TAG=v1.0.0 HARDWARE="RP2040 2MB"',
    IDENTITY:'IDENTITY SCHEMA=1 PRODUCT=Classic2USB MCU=RP2040 BASE_VID=16D0 BASE_PID=1460 UID=A1B2C3D4E5F60718'};
  await h.connectSerial();assert.deepEqual(h.commands,['IDENTITY2','INFO','IDENTITY']);
  assert.equal(h.state.identity.uniqueId,'A1B2C3D4E5F60718');
});
test('bad second device clears the old verified identity and does not downgrade',async()=>{
  const h=harness();await h.connectSerial();h.port.responses.IDENTITY2=serialV2.replace('TARGET=CLASSIC2USB_RP2040','TARGET=CLASSIC2USB_SONIK');
  await assert.rejects(()=>h.connectSerial(),/not approved/);
  assert.equal(h.state.identity,null);assert.equal(h.elements.get('#confirm').disabled,true);
  assert.deepEqual(h.commands,['IDENTITY2','IDENTITY2']);
});
test('actual HID UI reads the same handle and preserves approved identity during verification',async()=>{
  const h=harness(),device=hidDevice();h.context.navigator.hid={requestDevice:async()=>[device]};
  await h.connectHid();assert.deepEqual(device.calls,[0xE1,0xE2]);assert.equal(h.commands.length,0);
  const approved=h.state.session.identity;h.state.session.checked({version:'1.0.0'});h.state.session.confirm();h.state.session.beginFlash();
  await h.connectHid();assert.equal(h.state.session.identity,approved);
  await h.verifyAfterFlash();assert.equal(h.state.session.phase,'verified');
});
test('same-product different HID unit is rejected when reconnecting an approved update',async()=>{
  const h=harness(),device=hidDevice();h.context.navigator.hid={requestDevice:async()=>[device]};
  await h.connectHid();h.state.session.checked({version:'1.0.0'});h.state.session.confirm();h.state.session.beginFlash();
  device.a[4]^=1;device.b[2]^=1;
  await assert.rejects(()=>h.connectHid(),/approved physical unit/);
  assert.equal(h.state.identity,null);assert.equal(h.state.session.identity.uniqueId,'A1B2C3D4E5F60718');
});
