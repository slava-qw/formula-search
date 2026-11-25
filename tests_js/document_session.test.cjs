const test = require('node:test');
const assert = require('node:assert/strict');
const DocumentSession = require('../extension/document-session.js');

test('late work from a previous activation is stale, including return to same document', () => {
  const session = new DocumentSession();
  const first = session.begin();
  session.remember('paper-a', {query: 'x', mode: 'exact'});
  const second = session.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(session.isCurrent(first), false);
  const third = session.begin();
  assert.equal(session.isCurrent(second), false);
  assert.equal(session.isCurrent(third), true);
  assert.deepEqual(session.recall('paper-a'), {query: 'x', mode: 'exact'});
});

test('per-document drafts are bounded and remembering refreshes recency', () => {
  const session = new DocumentSession();
  for (let i = 0; i < 50; i++) session.remember(String(i), {query: String(i)});
  session.remember('0', {query: 'new'});
  session.remember('50', {query: 'last'});
  assert.equal(session.recall('1'), null);
  assert.equal(session.recall('0').query, 'new');
  assert.equal(session.queries.size, 50);
});
