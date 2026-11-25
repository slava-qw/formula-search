const test = require('node:test');
const assert = require('node:assert/strict');
const identity = require('../extension/bookmark-identity.js');
const createStore = require('../extension/shared-store.js');
const entry = (id, overrides = {}) => {
  const item = {source_url: 'https://arxiv.org/pdf/1706.03762v7', page: 3, latex: 'x+y',
    bbox: [10, 20, 50, 40], occurrence_id: 'occ1:' + id.repeat(64), ...overrides};
  return {...item, id: identity.key(item)};
};

test('same-page formulas have distinct keys; query subspans keep the same key', () => {
  const first = entry('a'), second = entry('b', {bbox: [10, 120, 50, 140]});
  assert.notEqual(first.id, second.id);
  assert.equal(identity.key({...first, bbox: [20, 20, 25, 40]}), first.id);
  assert.equal(identity.key({...first, page: -1, bbox: null}), first.id);
  assert.equal(identity.same(first, second), false);
});

test('arXiv URL aliases agree but versions and documents stay separate', () => {
  const first = entry('a');
  assert.equal(identity.key({...first, source_url: 'https://arxiv.org/abs/1706.03762v7'}), first.id);
  for (const source_url of ['https://arxiv.org/pdf/1706.03762v6', 'https://arxiv.org/pdf/1802.01528v3']) {
    assert.notEqual(identity.key({...first, source_url}), first.id);
  }
  assert.equal(identity.key({...first, source_url: 'https://arxiv.org/pdf/1706.03762',
    resolved_source_url: 'https://arxiv.org/pdf/1706.03762v7'}), first.id);
});

test('legacy matching needs an exact rectangle and compatible source; ambiguous saves stay separate', () => {
  const current = entry('a', {source_file: 'main.tex', source_line: 5});
  const legacy = {...current, id: 'old-page-latex-key'};
  delete legacy.occurrence_id;
  assert.equal(identity.same(legacy, current), true);
  for (const changes of [{bbox: null}, {bbox: [10, 120, 50, 140]}, {source_line: 6}, {source_file: 'other.tex'}]) {
    assert.equal(identity.same({...legacy, ...changes}, current), false);
  }
  assert.notEqual(identity.key({...legacy, bbox: [10, 120, 50, 140]}), identity.key(legacy));
});

test('serialized toggles preserve distinct occurrences and remove matching legacy bookmarks only', async () => {
  const first = entry('a'), second = entry('b', {bbox: [10, 120, 50, 140]});
  const legacy = {...first, id: 'legacy', occurrence_id: ''};
  const ambiguous = {...first, id: 'ambiguous', occurrence_id: '', bbox: null};
  const data = {fs_bookmarks: [legacy, ambiguous]};
  const store = createStore({get: async key => structuredClone({[key]: data[key]}),
    set: async values => Object.assign(data, structuredClone(values))});
  assert.equal(await store.dispatch('bookmarks.toggle', {entry: first}), false);
  assert.deepEqual(data.fs_bookmarks.map(item => item.id), ['ambiguous']);
  assert.deepEqual(await Promise.all([first, second].map(item => store.dispatch('bookmarks.toggle', {entry: item}))), [true, true]);
  assert.equal(data.fs_bookmarks.length, 3);
  assert.equal(await store.dispatch('bookmarks.toggle', {entry: {...first, bbox: [15, 20, 25, 40]}}), false);
  assert.equal(data.fs_bookmarks.some(item => item.id === second.id), true);
  assert.equal(data.fs_bookmarks.some(item => item.id === ambiguous.id), true);
});

test('long formula identities stay compact and failed bookmark saves do not erase existing entries', async () => {
  const first = entry('a', {latex: 'x+'.repeat(20000)+'y'});
  assert.ok(first.id.length < 150);
  const data = {fs_bookmarks: [first]};
  const store = createStore({get: async key => structuredClone({[key]: data[key]}),
    set: async () => {throw new Error('disk full');}});
  await assert.rejects(store.dispatch('bookmarks.toggle', {entry: entry('b')}), /disk full/);
  assert.deepEqual(data.fs_bookmarks, [first]);
});
