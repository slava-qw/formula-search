const test = require('node:test');
const assert = require('node:assert/strict');
const ResultFilter = require('../extension/result-filter.js');

test('page selection preserves complete exports, ordering and occurrence objects', () => {
  const all = [{page: 12, occurrence_id: 'a'}, {page: 2, occurrence_id: 'b'},
    {page: 12, occurrence_id: 'c'}, {page: 0, occurrence_id: 'd'}];
  const filter = new ResultFilter();
  filter.set(all);
  assert.deepEqual(filter.groups, [{key: 'page:0', count: 1}, {key: 'page:2', count: 1}, {key: 'page:12', count: 2}]);
  assert.deepEqual(filter.select('page:12').map(m => m.occurrence_id), ['a', 'c']);
  assert.equal(filter.matches[1], all[2]);
  assert.equal(filter.all, all);
  assert.equal(filter.select('all'), all);
});

test('unknown locations remain accessible without assigning invented pages', () => {
  const filter = new ResultFilter();
  const unknown = [{page: null}, {}, {page: -1}, {page: 1.5}, {page: '2'}, {page: Infinity}];
  filter.set([...unknown, {page: 2}]);
  assert.deepEqual(filter.select('unknown'), unknown);
  assert.deepEqual(filter.groups, [{key: 'page:2', count: 1}, {key: 'unknown', count: 6}]);
});

test('a new query retains a populated page and falls back to all when it has no matches', () => {
  const filter = new ResultFilter();
  filter.set([{page: 1}, {page: 2}]); filter.select('page:2');
  filter.clear({keepPage: true});
  assert.deepEqual(filter.all, []);
  filter.set([{page: 2}, {page: 3}]);
  assert.equal(filter.page, 'page:2');
  assert.equal(filter.matches.length, 1);
  filter.set([{page: 3}]);
  assert.equal(filter.page, 'all');
  assert.equal(filter.matches, filter.all);
});

test('document reset clears scope while an explicit restore survives loading', () => {
  const filter = new ResultFilter();
  filter.restorePage('page:20');
  filter.clear({keepPage: true});
  filter.set([{page: 20}, {page: 0}]);
  assert.equal(filter.matches[0].page, 20);
  filter.clear();
  filter.set([{page: 20}, {page: 0}]);
  assert.equal(filter.page, 'all');
  assert.equal(filter.matches.length, 2);
});

test('empty responses cannot leave old matches or page groups behind', () => {
  const filter = new ResultFilter();
  filter.set([{page: 4}]); filter.select('page:4'); filter.set([]);
  assert.deepEqual([filter.all, filter.matches, filter.groups], [[], [], []]);
  assert.equal(filter.page, 'all');
});

test('malformed stored page choices safely show all pages', () => {
  const filter = new ResultFilter();
  for (const value of [undefined, null, {}, -1, 'page:-1', 'page:01', 'page:1.2', 'page:9007199254740992', 'page:1<script>']) {
    filter.restorePage(value);
    assert.equal(filter.page, 'all');
  }
  for (const value of ['unknown', 'page:0', 'page:15']) {
    filter.restorePage(value);
    assert.equal(filter.page, value);
  }
});
