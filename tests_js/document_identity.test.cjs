const test = require('node:test');
const assert = require('node:assert/strict');
const identity = require('../extension/document-identity.js');
const cases = require('../testdata/document_identity.json');

test('JavaScript follows the shared backend document identity contract', () => {
  for (const entry of cases) {
    assert.equal(identity.arxivId(entry.url), entry.id, entry.url);
    assert.equal(identity.canonicalUrl(entry.url), entry.canonical || entry.url.trim().split('#', 1)[0], entry.url);
    assert.equal(identity.documentKey(entry.url), entry.id ? `arxiv:${entry.id}` : entry.url.trim().split('#', 1)[0]);
  }
});
