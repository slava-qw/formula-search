const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const identity = require('../extension/document-identity.js');
const source = fs.readFileSync(require.resolve('../extension/popup/popup.js'), 'utf8');

test('bookmarks and native note context retain the searched version while current navigation retains its tab URL', async () => {
  let saved;
  const context = vm.createContext({
    documentKey: identity.documentKey,
    FormulaBookmarkIdentity: require('../extension/bookmark-identity.js'),
    _sourceUrl: 'https://arxiv.org/pdf/1706.03762',
    FormulaStoreClient: async (action, data) => { saved = data.entry; return true; },
  });
  const bookmarks = source.slice(source.indexOf('const BOOKMARKS_STORAGE_KEY'), source.indexOf('// === BOOKMARKS (popup outer) END ==='));
  const message = source.slice(source.indexOf('function matchMessage('), source.indexOf('function nativePdfInstantJump('));
  vm.runInContext(bookmarks + '\n' + message, context);
  context.match = {source_url: context._sourceUrl, resolved_source_url: 'https://arxiv.org/pdf/1706.03762v7',
    page: 3, latex: 'QK^T', source: 'tex', occurrence_id: 'occ1:' + 'a'.repeat(64)};
  await vm.runInContext('toggleBookmark(match)', context);
  assert.equal(saved.source_url, 'https://arxiv.org/pdf/1706.03762v7');
  assert.equal(saved.id, 'arxiv:1706.03762v7::' + context.match.occurrence_id);
  const payload = vm.runInContext('matchMessage(match)', context);
  assert.equal(payload.source_url, saved.source_url);
  assert.equal(context.match.source_url, 'https://arxiv.org/pdf/1706.03762');
  context.match.resolved_source_url = 'https://arxiv.org/pdf/1706.03762v8';
  await vm.runInContext('toggleBookmark(match)', context);
  assert.equal(saved.id, 'arxiv:1706.03762v8::' + context.match.occurrence_id);
});
