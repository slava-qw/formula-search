const test = require('node:test');
const assert = require('node:assert/strict');
const backup = require('../extension/bookmark-backup.js');
const identity = require('../extension/bookmark-identity.js');
const createStore = require('../extension/shared-store.js');

function entry(n = 1, more = {}) {
  const item = {latex: `x_${n}`, page: 4, bbox: [1.125, 2.25, 3.375, 4.5], page_w: 612, page_h: 792,
    section: 'Derivatives', source_url: 'https://arxiv.org/pdf/1802.01528v3',
    source_file: 'matrix-calculus.tex', source_line: 97, source_location_kind: 'literal',
    location_status: 'located', bbox_source: 'query-subspan', occurrence_id: 'occ1:'+n.toString(16).padStart(64, '0'), ts: 123, ...more};
  return {...item, id: identity.key(item)};
}
const file = bookmarks => JSON.stringify({format: 'formula-search-bookmarks', version: 1, bookmarks});
function fixture(items = []) {
  let saved = structuredClone(items), writes = 0, fail = false;
  const store = createStore({async get() { await Promise.resolve(); return {fs_bookmarks: structuredClone(saved)}; },
    async set(value) { if (fail) { fail = false; throw Error('Quota exceeded'); } writes++; saved = structuredClone(value.fs_bookmarks); }});
  return {store, get saved() { return saved; }, get writes() { return writes; }, fail() { fail = true; }};
}

test('backup round trip preserves raw TeX, provenance, exact geometry, timestamps, uncertainty and legacy data', () => {
  const items = [entry(1, {latex: '\\frac{α}{β} − \\text{<img src=x>}\n'}),
    entry(2, {bbox: null, location_status: 'ambiguous'}), entry(3, {page: -1, bbox: null, location_status: 'unmapped'}),
    {id: 'legacy', latex: 'x'}];
  const text = backup.serialize(items, new Date('2026-11-22T00:00:00Z'));
  assert.equal(JSON.parse(text).created_at, '2026-11-22T00:00:00.000Z');
  assert.deepEqual(backup.parse('\uFEFF'+text), items);
});

test('wrong format/version and malformed JSON reject instead of interpreting a result export as bookmarks', () => {
  for (const text of ['{', 'null', '[]', JSON.stringify([entry()]), JSON.stringify({format: 'formula-search-bookmarks', version: 2, bookmarks: []})]) {
    assert.throws(() => backup.parse(text), /JSON|backup|version/);
  }
});

test('every known field has type checks; URLs, invalid geometry, false precise ambiguity and malformed occurrence IDs reject', () => {
  const changes = [{latex: ''}, {id: 2}, {section: {}}, {source_line: -1}, {page: 1.5}, {ts: -1},
    {source_url: 'javascript:alert(1)'}, {resolved_source_url: 'data:text/html,x'}, {source_url: 'not a URL'},
    {bbox: [1, 2, 0, 4]}, {bbox: [1, 2, 3]}, {bbox: [1, 2, 3, Infinity]}, {page_w: 0},
    {page_h: '792'}, {page: -1}, {location_status: 'ambiguous'}, {occurrence_id: 'wrong'}, {latex: 'x'.repeat(100001)}];
  for (const change of changes) assert.throws(() => backup.parse(file([{...entry(), ...change}])), /invalid/, JSON.stringify(change));
  assert.throws(() => backup.parse(file([null])), /invalid record/);
  assert.throws(() => backup.parse(file(Array.from({length: 201}, (_, i) => entry(i)))), /at most 200/);
});

test('byte budget rejects multibyte JSON before parsing or accepting nested extra data', () => {
  const text = JSON.stringify({format: 'formula-search-bookmarks', version: 1, bookmarks: [], extra: 'α'.repeat(backup.MAX_BYTES/2)});
  assert.ok(text.length < backup.MAX_BYTES);
  assert.throws(() => backup.parse(text), /8 MiB/);
  assert.throws(() => backup.serialize([entry(1, {extra: 'x'.repeat(backup.MAX_BYTES)})]), /8 MiB/);
});

test('merge keeps saved records and ordering, deduplicates within the file, preserves different occurrences and versions', () => {
  const saved = entry(), updated = {...saved, latex: 'changed', ts: 999};
  const other = entry(2), version = entry(1, {source_url: 'https://arxiv.org/pdf/1802.01528v2'});
  const result = backup.merge([saved], [updated, other, other, version]);
  assert.deepEqual(result, {items: [saved, other, version], added: 2, duplicates: 2, total: 3});
  assert.equal(result.items[0], saved); assert.equal(saved.ts, 123);
});

test('canonical document and occurrence identities deduplicate despite a different stored ID; legacy boxes must agree', () => {
  const modern = entry();
  const alias = {...modern, id: 'alias', source_url: 'https://arxiv.org/abs/1802.01528v3'};
  assert.equal(backup.merge([modern], [alias]).duplicates, 1);
  const old = {...modern, id: 'old', occurrence_id: ''};
  assert.equal(backup.merge([old], [modern]).duplicates, 1);
  const elsewhere = {...old, id: 'elsewhere', bbox: [5, 6, 7, 8]};
  assert.equal(backup.merge([elsewhere], [modern]).added, 1);
  assert.equal(backup.merge([{...old, bbox: null}], [modern]).added, 1);
});

test('invalid later record and capacity overflow leave all saved records untouched with no partial write', async () => {
  const f = fixture([entry()]);
  await assert.rejects(f.store.dispatch('bookmarks.import', {text: file([entry(2), {...entry(3), bbox: 'bad'}])}), /Bookmark 2/);
  assert.deepEqual(f.saved, [entry()]); assert.equal(f.writes, 0);
  const full = fixture(Array.from({length: 200}, (_, i) => entry(i)));
  await assert.rejects(full.store.dispatch('bookmarks.import', {text: file([entry(201)])}), /Nothing was imported/);
  assert.equal(full.saved.length, 200); assert.equal(full.writes, 0);
  assert.deepEqual(await full.store.dispatch('bookmarks.import', {text: file([entry(1)])}), {added: 0, duplicates: 1, total: 200});
  assert.equal(full.writes, 0);
});

test('concurrent imports and toggles serialize in one worker without dropping or duplicating bookmarks', async () => {
  const f = fixture();
  const results = await Promise.all([
    f.store.dispatch('bookmarks.import', {text: file([entry(), entry(2)])}),
    f.store.dispatch('bookmarks.toggle', {entry: entry(3)}),
    f.store.dispatch('bookmarks.import', {text: file([entry(2), entry(4)])}),
  ]);
  assert.deepEqual(results[0], {added: 2, duplicates: 0, total: 2});
  assert.equal(results[1], true);
  assert.deepEqual(results[2], {added: 1, duplicates: 1, total: 4});
  assert.deepEqual(f.saved.map(b => b.occurrence_id), [entry(3), entry(), entry(2), entry(4)].map(b => b.occurrence_id));
});

test('quota failure is reported without erasing saved data and subsequent imports still work', async () => {
  const f = fixture([entry()]); f.fail();
  await assert.rejects(f.store.dispatch('bookmarks.import', {text: file([entry(2)])}), /Quota/);
  assert.deepEqual(f.saved, [entry()]);
  assert.deepEqual(await f.store.dispatch('bookmarks.import', {text: file([entry(2)])}), {added: 1, duplicates: 0, total: 2});
});

test('malformed existing storage stays untouched and import does not clear valid existing bookmarks', async () => {
  const f = fixture({keep: 'original'});
  await assert.rejects(f.store.dispatch('bookmarks.import', {text: file([])}), /unexpected format/);
  assert.deepEqual(f.saved, {keep: 'original'});
  const valid = fixture([entry()]);
  assert.deepEqual(await valid.store.dispatch('bookmarks.import', {text: file([])}), {added: 0, duplicates: 0, total: 1});
  assert.deepEqual(valid.saved, [entry()]); assert.equal(valid.writes, 0);
});
