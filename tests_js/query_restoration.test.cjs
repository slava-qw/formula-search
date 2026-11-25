const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const DocumentSession = require('../extension/document-session.js');
const ResultFilter = require('../extension/result-filter.js');
const source = fs.readFileSync(require.resolve('../extension/popup/popup.js'), 'utf8');
function between(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing source boundary: ${start}`);
  return source.slice(a, b);
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
function setup() {
  const listeners = {}, calls = [], local = {}, session = {};
  const queryInput = {value: '', disabled: false, addEventListener(name, fn) { listeners[name] = fn; }};
  const mode = {value: 'contains', addEventListener(name, fn) { listeners[`mode-${name}`] = fn; }};
  const resultPageSelect = {value: 'all', addEventListener(name, fn) { listeners[`page-${name}`] = fn; }};
  const resultSectionSelect = {value: 'all', addEventListener(name, fn) { listeners[`section-${name}`] = fn; }};
  const documentSession = new DocumentSession();
  documentSession.begin();
  const context = vm.createContext({
    queryInput, documentSession, resultFilter: new ResultFilter(), IS_SIDE_PANEL: true, currentSessionUrl: 'paper-a',
    _sourceUrl: 'paper-a', _panelWindowId: 1, _lastHandoffId: '', _handoffAppliedTo: null, _viewMode: 'list',
    resultPageSelect, resultSectionSelect, resultsList: {scrollTop: 0}, _matches: [], _stepIndex: 0,
    updateResultFilterControls() {}, renderResults() {}, sendScrollToMatch() {},
    documentKey: value => value, document: {getElementById: () => mode},
    updateQueryPreview() {}, showQueryHistoryDropdown() {}, hideQueryHistoryDropdown() {}, showView() {},
    runSearch: async () => { calls.push({query: queryInput.value, mode: mode.value}); },
    chrome: {storage: {
      local: {get: async key => ({[key]: local[key]}), set: async values => Object.assign(local, values)},
      session: {get: async key => ({[key]: session[key]}), remove: async key => { delete session[key]; }},
    }},
    loadLastQuery: async () => 'saved-old-query',
  });
  vm.runInContext([
    between('// === QUERY INTENT (popup outer) BEGIN ===', '// === QUERY INTENT (popup outer) END ==='),
    between('// === PENDING QUERY (popup outer) BEGIN ===', '// === PENDING QUERY (popup outer) END ==='),
    between('async function restoreLastQuery()', 'function updateQueryPreview()'),
    between("queryInput.addEventListener('input'", 'async function runSearch('),
    between('async function consumePanelHandoff()', 'chrome.storage.onChanged.addListener'),
    between("resultPageSelect.addEventListener('change'", 'let _searchGeneration'),
  ].join('\n'), context);
  const evaluate = code => vm.runInContext(code, context);
  const edit = value => { queryInput.value = value; listeners.input(); };
  return {context, queryInput, mode, calls, local, session, evaluate, edit, listeners};
}

test('an untouched enabled surface restores and searches its saved query once', async () => {
  const s = setup();
  await s.evaluate('restoreLastQuery()');
  await s.evaluate('restoreLastQuery()');
  assert.deepEqual(s.calls, [{query: 'saved-old-query', mode: 'contains'}]);
});

for (const [name, change, expected] of [
  ['typing', s => s.edit('fresh'), 'fresh'],
  ['type then clear', s => { s.edit('fresh'); s.edit(''); }, ''],
  ['history selection', s => s.evaluate("setQueryValue('history')"), 'history'],
  ['mode choice', s => { s.mode.value = 'exact'; s.listeners['mode-change'](); }, ''],
  ['document switch and return', s => { s.context.documentSession.begin(); s.context.documentSession.begin(); }, ''],
  ['disabled search', s => { s.queryInput.disabled = true; }, ''],
]) {
  test(`late last-query restoration respects ${name}`, async () => {
    const s = setup(), read = deferred();
    s.context.loadLastQuery = () => read.promise;
    const pending = s.evaluate('restoreLastQuery()');
    change(s);
    read.resolve('saved-old-query');
    await pending;
    assert.equal(s.queryInput.value, expected);
    assert.deepEqual(s.calls, []);
  });
}

test('typing and clearing before restoration starts also prevents an automatic search', async () => {
  const s = setup();
  s.edit('fresh'); s.edit('');
  await s.evaluate('restoreLastQuery()');
  assert.equal(s.queryInput.value, '');
  assert.deepEqual(s.calls, []);
});

test('concurrent saved-query reads apply only the first result', async () => {
  const s = setup(), first = deferred(), second = deferred();
  let count = 0;
  s.context.loadLastQuery = () => (++count === 1 ? first : second).promise;
  const a = s.evaluate('restoreLastQuery()'), b = s.evaluate('restoreLastQuery()');
  second.resolve('newer'); await b;
  first.resolve('older'); await a;
  assert.deepEqual(s.calls, [{query: 'newer', mode: 'contains'}]);
});

test('malformed stored query cannot fill or submit the input', async () => {
  const s = setup();
  s.context.loadLastQuery = async () => ({query: 'bad'});
  await s.evaluate('restoreLastQuery()');
  assert.equal(s.queryInput.value, '');
  assert.deepEqual(s.calls, []);
});

for (const boundary of ['get', 'set']) {
  test(`pending context-menu query cannot overwrite typing during storage ${boundary}`, async () => {
    const s = setup(), gate = deferred(), reached = deferred();
    s.local.fs_pending_query = 'selected';
    s.context.chrome.storage.local[boundary] = async () => {
      reached.resolve(); await gate.promise;
      if (boundary === 'get') return {fs_pending_query: 'selected'};
      s.local.fs_pending_query = '';
    };
    const pending = s.evaluate('consumePendingQuery()');
    await reached.promise;
    s.edit('fresh'); gate.resolve();
    assert.equal(await pending, true);
    assert.equal(s.queryInput.value, 'fresh');
    assert.deepEqual(s.calls, []);
  });
}

test('startup consumes an obsolete pending query without replacing an early edit', async () => {
  const s = setup();
  s.local.fs_pending_query = 'selected';
  s.edit('fresh'); s.edit('');
  assert.equal(await s.evaluate('consumePendingQuery({startup: true})'), true);
  assert.equal(s.local.fs_pending_query, '');
  assert.equal(s.queryInput.value, '');
  assert.deepEqual(s.calls, []);
});

test('a later explicit context-menu query still replaces an existing draft', async () => {
  const s = setup();
  s.edit('existing');
  s.context.resultFilter.restorePage('page:9');
  s.context.resultFilter.restoreSection('section:Methods');
  s.local.fs_pending_query = 'selected';
  assert.equal(await s.evaluate('consumePendingQuery()'), true);
  assert.deepEqual(s.calls, [{query: 'selected', mode: 'contains'}]);
  assert.equal(s.context.resultFilter.page, 'all');
  assert.equal(s.context.resultFilter.section, 'all');
});

test('pending query cannot cross document activations', async () => {
  const s = setup(), gate = deferred();
  s.context.chrome.storage.local.get = () => gate.promise;
  const pending = s.evaluate('consumePendingQuery()');
  s.context.documentSession.begin();
  gate.resolve({fs_pending_query: 'selected'});
  assert.equal(await pending, true);
  assert.equal(s.queryInput.value, '');
  assert.deepEqual(s.calls, []);
});

function handoff(s) {
  s.session.fs_panel_handoff_1 = {id: 'one', ts: Date.now(), document: 'paper-a', query: 'transferred', mode: 'exact', pageFilter: 'page:9', sectionFilter:'section:Methods'};
}
test('ordinary panel transfer preserves the query and search mode, then consumes itself', async () => {
  const s = setup(); handoff(s);
  assert.equal(await s.evaluate('consumePanelHandoff()'), true);
  assert.equal(await s.evaluate('consumePanelHandoff()'), false);
  assert.deepEqual(s.calls, [{query: 'transferred', mode: 'exact'}]);
  assert.equal(s.session.fs_panel_handoff_1, undefined);
  assert.equal(s.context.resultFilter.page, 'page:9');
  assert.equal(s.context.resultFilter.section, 'section:Methods');
});

for (const boundary of ['get', 'remove']) {
  test(`panel transfer respects typing during storage ${boundary}`, async () => {
    const s = setup(), gate = deferred(), reached = deferred(); handoff(s);
    s.context.chrome.storage.session[boundary] = async () => {
      reached.resolve(); await gate.promise;
      return {fs_panel_handoff_1: s.session.fs_panel_handoff_1};
    };
    const pending = s.evaluate('consumePanelHandoff()');
    await reached.promise;
    s.edit('fresh'); gate.resolve(); await pending;
    assert.equal(s.queryInput.value, 'fresh');
    assert.equal(s.mode.value, 'contains', 'A superseded transfer must not leave its mode behind');
    assert.equal(s.context.resultFilter.section, 'all', 'A superseded transfer must not leave its section behind');
    assert.deepEqual(s.calls, []);
  });
}

test('panel transfer cannot cross document activations', async () => {
  const s = setup(), gate = deferred(); handoff(s);
  s.context.chrome.storage.session.get = () => gate.promise;
  const pending = s.evaluate('consumePanelHandoff()');
  s.context.documentSession.begin();
  gate.resolve({fs_panel_handoff_1: s.session.fs_panel_handoff_1});
  assert.equal(await pending, false);
  assert.deepEqual(s.calls, []);
});

for (const boundary of ['get', 'remove']) {
  test(`a page choice takes precedence over delayed panel transfer during ${boundary}`, async () => {
    const s = setup(), gate = deferred(), reached = deferred(); handoff(s);
    s.context.resultFilter.set([{page: 2}, {page: 9}]);
    s.context._matches = s.context.resultFilter.matches;
    s.context.chrome.storage.session[boundary] = async () => {
      reached.resolve(); await gate.promise;
      return {fs_panel_handoff_1: s.session.fs_panel_handoff_1};
    };
    const pending = s.evaluate('consumePanelHandoff()');
    await reached.promise;
    s.context.resultPageSelect.value = 'page:2'; s.listeners['page-change']();
    gate.resolve(); await pending;
    assert.equal(s.context.resultFilter.page, 'page:2');
    assert.deepEqual(s.calls, []);
  });
}

for (const boundary of ['get','remove']) {
  test(`a section choice takes precedence over delayed panel transfer during ${boundary}`,async()=>{
    const s=setup(),gate=deferred(),reached=deferred();handoff(s);
    s.context.resultFilter.set([{section:'Intro',page:2},{section:'Methods',page:9}]);
    s.context._matches=s.context.resultFilter.matches;
    s.context.chrome.storage.session[boundary]=async()=>{
      reached.resolve();await gate.promise;return {fs_panel_handoff_1:s.session.fs_panel_handoff_1};
    };
    const pending=s.evaluate('consumePanelHandoff()');await reached.promise;
    s.context.resultSectionSelect.value='section:Intro';s.listeners['section-change']();
    gate.resolve();await pending;
    assert.equal(s.context.resultFilter.section,'section:Intro');
    assert.equal(s.context.resultFilter.page,'all');assert.deepEqual(s.calls,[]);
  });
}

test('legacy panel handoffs without a section clear an older section choice',async()=>{
  const s=setup();handoff(s);delete s.session.fs_panel_handoff_1.sectionFilter;
  s.context.resultFilter.restoreSection('section:Old');
  await s.evaluate('consumePanelHandoff()');assert.equal(s.context.resultFilter.section,'all');
});
