const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../extension/popup/popup.js'), 'utf8')
  .split('// === EXPORT RESULTS (popup outer) BEGIN ===')[1]
  .split('// === EXPORT RESULTS (popup outer) END ===')[0];

function fixture() {
  const calls = [], urls = new Map(), revoked = [];
  let current = true, errorReads = 0, lastError;
  const runtime = {get lastError() { errorReads++; return lastError; }};
  const context = vm.createContext({Blob, Date, _searchGeneration: 1,
    captureQueryState: () => ({}), isQueryStateCurrent: () => current,
    statusEl: {textContent: 'Search ready'}, exportStatus: {textContent: ''},
    URL: {createObjectURL(blob) { const url = `blob:${urls.size}`; urls.set(url, blob); return url; },
      revokeObjectURL(url) { revoked.push(url); }},
    chrome: {runtime, downloads: {download(request, callback) { calls.push({request, callback}); }}},
  });
  vm.runInContext(source, context);
  return {context, calls, urls, revoked,
    stale() { current = false; },
    finish(index = 0, error) { lastError = error; calls[index].callback(error ? undefined : 42); lastError = undefined; },
    get errorReads() { return errorReads; },
  };
}

test('full and filtered exports snapshot complete records, preserve ordering and request Save As', async () => {
  const f = fixture();
  const rows = [{latex: '\\alpha', page: 2, occurrence_id: 'a', bbox: [1, 2, 3, 4]},
    {latex: '\\beta', page: -1, occurrence_id: 'b', bbox: null}];
  const original = structuredClone(rows);
  f.context.requestResultsExport(rows);
  f.context.requestResultsExport(rows.slice(1), {filtered: true});
  rows[1].latex = 'changed after click'; rows.length = 0;
  for (let i = 0; i < 2; i++) {
    const request = f.calls[i].request;
    assert.equal(request.saveAs, true);
    assert.match(request.filename, i ? /^formula-search-filtered-\d+\.json$/ : /^formula-search-results-\d+\.json$/);
    assert.equal(f.urls.get(request.url).type, 'application/json');
    assert.deepEqual(JSON.parse(await f.urls.get(request.url).text()), i ? original.slice(1) : original);
    f.finish(i);
  }
  assert.deepEqual(f.revoked, ['blob:0', 'blob:1']);
  assert.equal(f.errorReads, 2);
  assert.equal(f.context.statusEl.textContent, 'Search ready');
});

test('empty exports do not create an object URL or a download', () => {
  const f = fixture(); f.context.requestResultsExport([], {filtered: true});
  assert.equal(f.calls.length, 0); assert.equal(f.urls.size, 0);
});

test('download errors are consumed in the callback, revoke the URL and show feedback', () => {
  const f = fixture(); f.context.requestResultsExport([{latex: 'x'}]);
  f.finish(0, {message: 'arbitrary browser error'});
  assert.equal(f.errorReads, 1); assert.deepEqual(f.revoked, ['blob:0']);
  assert.match(f.context.exportStatus.textContent, /Export was not started/);
  assert.equal(f.context.statusEl.textContent, 'Search ready');
});

test('a synchronous download failure also releases the URL and shows feedback', () => {
  const f = fixture(); f.context.chrome.downloads.download = () => { throw new Error('API unavailable'); };
  f.context.requestResultsExport([{latex: 'x'}]);
  assert.deepEqual(f.revoked, ['blob:0']);
  assert.match(f.context.exportStatus.textContent, /Export was not started/);
});

test('serialization failure is contained without creating a URL', () => {
  const f = fixture(), row = {}; row.circular = row;
  assert.doesNotThrow(() => f.context.requestResultsExport([row]));
  assert.equal(f.calls.length, 0); assert.equal(f.urls.size, 0);
  assert.match(f.context.exportStatus.textContent, /Export was not started/);
});

test('late failure cannot replace feedback after a query, filter or document change', () => {
  const f = fixture(); f.context.requestResultsExport([{latex: 'x'}]);
  f.stale(); f.context.statusEl.textContent = 'New context'; f.finish(0, {message: 'old failure'});
  assert.equal(f.context.statusEl.textContent, 'New context');
  assert.equal(f.context.exportStatus.textContent, '');
  assert.equal(f.errorReads, 1); assert.deepEqual(f.revoked, ['blob:0']);
});

test('repeating the same search invalidates pending export failure feedback', () => {
  const f = fixture(); f.context.requestResultsExport([{latex: 'x'}]);
  f.context._searchGeneration++; f.context.statusEl.textContent = 'Searching';
  f.finish(0, {message: 'old failure'});
  assert.equal(f.context.statusEl.textContent, 'Searching');
  assert.equal(f.context.exportStatus.textContent, '');
});

test('an older export failure cannot overwrite feedback after a newer export request', () => {
  const f = fixture(); f.context.requestResultsExport([{latex: 'x'}]);
  f.context.requestResultsExport([{latex: 'y'}], {filtered: true});
  f.finish(1); f.finish(0, {message: 'older request failed'});
  assert.equal(f.context.statusEl.textContent, 'Search ready');
  assert.equal(f.context.exportStatus.textContent, '');
  assert.deepEqual(f.revoked, ['blob:1', 'blob:0']);
});

test('retry clears the earlier export error while retaining the search summary', () => {
  const f = fixture(); f.context.requestResultsExport([{latex: 'x'}]);
  f.finish(0, {message: 'failed'});
  assert.match(f.context.exportStatus.textContent, /Export was not started/);
  f.context.requestResultsExport([{latex: 'x'}]);
  assert.equal(f.context.exportStatus.textContent, '');
  f.finish(1);
  assert.equal(f.context.exportStatus.textContent, '');
  assert.equal(f.context.statusEl.textContent, 'Search ready');
});
