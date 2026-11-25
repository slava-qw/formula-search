const test = require('node:test');
const assert = require('node:assert/strict');
const createStore = require('../extension/shared-store.js');

function fixture(seed = {}) {
  const data = structuredClone(seed);
  let failNext = false;
  const storage = {
    async get(key) { await new Promise(resolve => setTimeout(resolve, 1)); return structuredClone({[key]: data[key]}); },
    async set(values) {
      await new Promise(resolve => setTimeout(resolve, 1));
      if (failNext) { failNext = false; throw new Error('disk full'); }
      Object.assign(data, structuredClone(values));
    },
  };
  return {store: createStore(storage), data, fail: () => {failNext = true;}};
}

test('100 simultaneous note saves preserve all texts and strict timestamps', async () => {
  const {store} = fixture();
  await Promise.all(Array.from({length: 100}, (_, i) => store.dispatch('notes.append', {text: `note-${i}`, context: {source_url: `paper-${i}`}})));
  const notes = await store.dispatch('notes.list');
  assert.equal(notes.length, 100);
  assert.equal(new Set(notes.map(n => n.id)).size, 100);
  notes.forEach((note, i) => {
    assert.equal(note.text, `note-${i}`);
    assert.equal(note.context.source_url, `paper-${i}`);
    if (i) assert.ok(note.timestamp > notes[i - 1].timestamp);
  });
});

test('distinct concurrent bookmarks survive; repeated toggles and removals are ordered', async () => {
  const {store} = fixture();
  await Promise.all(Array.from({length: 40}, (_, i) => store.dispatch('bookmarks.toggle', {entry: {id: `b-${i}`, latex: `x_${i}`, source_url: 'paper-a'}})));
  assert.equal((await store.dispatch('bookmarks.list')).length, 40);
  const toggled = await Promise.all([1, 2].map(() => store.dispatch('bookmarks.toggle', {entry: {id: 'same', latex: 'x'}})));
  assert.deepEqual(toggled, [true, false]);
  await Promise.all([store.dispatch('bookmarks.remove', {id: 'b-0'}), store.dispatch('bookmarks.toggle', {entry: {id: 'new', latex: 'z'}})]);
  const items = await store.dispatch('bookmarks.list');
  assert.equal(items.length, 40);
  assert.equal(items.some(item => item.id === 'b-0'), false);
  assert.equal(items.some(item => item.id === 'new'), true);
});

test('append / clear / append order cannot resurrect deleted notes', async () => {
  const {store} = fixture();
  await Promise.all([
    store.dispatch('notes.append', {text: 'before'}), store.dispatch('notes.clear'),
    store.dispatch('notes.append', {text: 'after'}),
  ]);
  assert.deepEqual((await store.dispatch('notes.list')).map(n => n.text), ['after']);
});

test('a rejected write is reported, leaves persisted data intact and does not poison the queue', async () => {
  const {store, fail} = fixture({fs_chat_history: [{id: 'old', text: 'existing', timestamp: 10}]});
  fail();
  const results = await Promise.allSettled([
    store.dispatch('notes.append', {text: 'failed'}), store.dispatch('notes.append', {text: 'saved'}),
  ]);
  assert.equal(results[0].status, 'rejected');
  assert.equal(results[1].status, 'fulfilled');
  assert.deepEqual((await store.dispatch('notes.list')).map(n => n.text), ['existing', 'saved']);
});

test('caps apply to newest entries, last-seen never moves backward, arXiv URLs deduplicate', async () => {
  const {store, data} = fixture({fs_settings: {maxChatHistory: 3}});
  await Promise.all(Array.from({length: 10}, (_, i) => store.dispatch('notes.append', {text: `n${i}`})));
  assert.deepEqual(data.fs_chat_history.map(n => n.text), ['n7', 'n8', 'n9']);
  await Promise.all([100, 20, 150, 30].map(timestamp => store.dispatch('seen.advance', {timestamp})));
  assert.equal(data.fs_chat_last_seen, 150);
  await Promise.all(['abs', 'pdf'].map(kind => store.dispatch('recent.record', {entry: {url: `https://arxiv.org/${kind}/1706.03762v7`}})));
  assert.equal(data.fs_recent_papers.length, 1);
  await Promise.all(Array.from({length: 30}, (_, i) => store.dispatch('history.record', {query: `x_${i}`})));
  assert.equal(data.fs_search_history.length, 20);
  assert.equal(data.fs_search_history[0].query, 'x_29');
});

test('malformed existing data is not silently overwritten and invalid actions cannot write', async () => {
  const {store, data} = fixture({fs_chat_history: {unexpected: 'preserve'}});
  await assert.rejects(store.dispatch('notes.append', {text: 'new'}), /unexpected format/);
  assert.deepEqual(data.fs_chat_history, {unexpected: 'preserve'});
  await assert.rejects(store.dispatch('arbitrary.clear'), /Unknown/);
  await assert.rejects(store.dispatch('bookmarks.clear'), /Unknown/);
});
