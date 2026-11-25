const test = require('node:test');
const assert = require('node:assert/strict');
const Transfer = require('../extension/bookmark-transfer.js');
const Backup = require('../extension/bookmark-backup.js');
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture(store = async action => action === 'bookmarks.list' ? [{id: 'a', latex: 'α'}] : {added: 1, duplicates: 2, total: 3}) {
  const el = () => ({listeners: {}, attrs: {}, textContent: '', value: '', files: [], clicks: 0,
    addEventListener(name, handler) { this.listeners[name] = handler; },
    setAttribute(key, value) { this.attrs[key] = value; }, removeAttribute(key) { delete this.attrs[key]; },
    click() { this.clicks++; return this.listeners.click?.(); }});
  const exportButton = el(), importButton = el(), input = el(), status = el(), count = el();
  const requests = [], revoked = [], blobs = [], calls = [], runtime = {};
  const downloads = {download(request, done) { requests.push({request, done}); }};
  const controller = Transfer.bind({exportButton, importButton, input, status, count, backup: Backup,
    store: (...args) => { calls.push(args); return store(...args); }, runtime, downloads,
    urls: {createObjectURL(blob) { blobs.push(blob); return 'blob:backup'; }, revokeObjectURL(url) { revoked.push(url); }}});
  controller.updateCount(1);
  return {exportButton, importButton, input, status, count, requests, revoked, blobs, calls, runtime, controller, downloads,
    async import(file) { input.value = 'selected'; input.files = file ? [file] : []; input.listeners.change(); await settle(); }};
}

test('export snapshots serialized store data, requests Save As once, and cleans its URL after callback', async () => {
  const f = fixture(); const pending = f.exportButton.click(); await settle();
  assert.equal(f.exportButton.disabled, true); assert.equal(f.importButton.disabled, true);
  await f.exportButton.click(); assert.equal(f.calls.length, 1); assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].request.saveAs, true);
  assert.equal(f.requests[0].request.filename, 'formula-search-bookmarks.json');
  assert.deepEqual(Backup.parse(await f.blobs[0].text()), [{id: 'a', latex: 'α'}]);
  assert.deepEqual(f.revoked, []);
  f.requests[0].done(5); await pending;
  assert.deepEqual(f.revoked, ['blob:backup']); assert.match(f.status.textContent, /requested for 1 bookmark\./);
  assert.equal(f.importButton.disabled, false);
});

for (const sync of [false, true]) test(`download ${sync ? 'throw' : 'callback error'} releases URL and permits retry`, async () => {
  const f = fixture();
  if (sync) f.downloads.download = () => { throw Error('download unavailable'); };
  const pending = f.exportButton.click(); await settle();
  if (!sync) { f.runtime.lastError = {message: 'cancelled'}; f.requests[0].done(); delete f.runtime.lastError; }
  await pending;
  assert.equal(f.status.attrs['data-error'], 'true'); assert.equal(f.exportButton.disabled, false);
  assert.equal(f.status.textContent.includes('requested'), false); assert.deepEqual(f.revoked, ['blob:backup']);
});

test('export storage errors create no URL or download and keep the file controls usable', async () => {
  const f = fixture(async () => { throw Error('storage unavailable'); }); await f.exportButton.click();
  assert.equal(f.requests.length, 0); assert.equal(f.blobs.length, 0);
  assert.match(f.status.textContent, /storage unavailable/); assert.equal(f.importButton.disabled, false);
});

test('file chooser cancellation leaves state untouched; oversize files reject before reading', async () => {
  const f = fixture(); f.importButton.click(); assert.equal(f.input.clicks, 1);
  f.status.textContent = 'Previous success'; await f.import(null);
  assert.equal(f.status.textContent, 'Previous success'); assert.equal(f.calls.length, 0);
  await f.import({size: Backup.MAX_BYTES+1, text() { throw Error('must not read'); }});
  assert.match(f.status.textContent, /8 MiB/); assert.equal(f.calls.length, 0); assert.equal(f.input.value, '');
});

test('file read and worker validation errors keep saved data on the worker and allow same-file retry', async () => {
  const f = fixture(async () => { throw Error('Bookmark 2 has an invalid bbox.'); });
  await f.import({size: 5, async text() { throw Error('read failure'); }});
  assert.match(f.status.textContent, /could not be read/); assert.equal(f.calls.length, 0);
  await f.import({size: 5, async text() { return '{bad}'; }});
  assert.deepEqual(f.calls, [['bookmarks.import', {text: '{bad}'}]]);
  assert.match(f.status.textContent, /Bookmark 2/); assert.equal(f.input.value, ''); assert.equal(f.importButton.disabled, false);
});

test('one import runs while busy and late response cannot overwrite a newer displayed storage count', async () => {
  let release;
  const f = fixture(() => new Promise(resolve => release = resolve));
  const file = {size: 2, async text() { return '{}'; }};
  await f.import(file); assert.equal(f.input.disabled, true);
  await f.import(file); assert.equal(f.calls.length, 1);
  f.controller.updateCount(4);
  release({added: 1, duplicates: 2, total: 3}); await settle();
  assert.equal(f.count.textContent, '4 saved'); assert.equal(f.status.textContent, 'Added 1; already saved 2. 3 bookmarks saved.');
  assert.equal(f.input.disabled, false);
});
