const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture({ child = true, delay = 0, readyAfter = 0, never = false, failed = false, setupFailures = 0, onUnready = () => {} } = {}) {
  const listeners = new Set();
  const detached = new Set(), removed = new Set(), updated = new Set();
  const event = set => ({addListener: fn => set.add(fn), removeListener: fn => set.delete(fn)});
  const started = performance.now();
  let url = 'file:///paper.pdf';
  let attached = false, attaches = 0, detaches = 0;
  const calls = [];
  const emit = (source, method, params) => listeners.forEach(fn => fn(source, method, params));
  const context = (source, id, viewer) => emit(source, 'Runtime.executionContextCreated', {
    context: { id, origin: viewer ? 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai' : 'https://example.com', auxData: { isDefault: true } },
  });
  const api = {
    onEvent: event(listeners), onDetach: event(detached),
    async attach() { assert.equal(attached, false, 'overlapping debugger attachments'); attached = true; attaches++; },
    async detach() { attached = false; detaches++; },
    async sendCommand(source, method, params) {
      calls.push({source, method, params});
      if (method === 'Runtime.enable') {
        if ((source.sessionId || !child) && setupFailures > 0) {
          setupFailures--;
          throw Error('temporary target setup failure');
        }
        if (source.sessionId) context(source, 2, true);
        else { context(source, 1, false); if (!child) context(source, 2, true); }
      }
      if (method === 'Target.setAutoAttach' && !source.sessionId && child) {
        setTimeout(() => emit(source, 'Target.attachedToTarget', { sessionId: 'guest', targetInfo: {type: 'iframe'} }), delay);
      }
      if (method === 'Runtime.evaluate') {
        if (params.expression === 'perform navigation') return {result: {value: params.contextId === 2}};
        const viewport = {pageDimensions_: [{}, {}, {}], size: {width: 800, height: 600}, position: {x: 0, y: 0},
          getPageScreenRect() {
            if (never || performance.now()-started < readyAfter) { onUnready(); throw Error('geometry is not ready'); }
            return {x: 0, y: 0, width: 600, height: 800};
          }};
        const viewer = {viewport, originalUrl: url, loadState_: failed ? 'failed' : 'success'};
        return {result: {value: vm.runInNewContext(params.expression, {
          document: {querySelector: () => params.contextId === 2 ? viewer : null},
        })}};
      }
      return {};
    },
  };
  const tabs = {get: async () => ({url}), onRemoved: event(removed), onUpdated: event(updated)};
  const sandbox = {chrome: {debugger: api, tabs}, performance, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../extension/native-viewer-state.js'), 'utf8'), sandbox);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../extension/native-bridge.js'), 'utf8'), sandbox);
  return { bridge: sandbox.FormulaNativeBridge, calls,
    makeReady() { never = false; readyAfter = 0; },
    changeUrl(value) { url = value; updated.forEach(fn => fn(7, {url})); },
    close() { removed.forEach(fn => fn(7)); },
    disconnect(reason = 'canceled_by_user') { detached.forEach(fn => fn({tabId: 7}, reason)); },
    counts: () => ({attaches, detaches, listeners: listeners.size+detached.size+removed.size+updated.size, attached}) };
}

test('reaches a same-process PDF frame by its execution context', async () => {
  const f = fixture({child: false});
  const result = await f.bridge.withViewer(7, evaluate => evaluate('perform navigation'));
  assert.equal(result.result.value, true);
  assert.equal(f.calls.at(-1).params.contextId, 2);
  assert.equal(f.calls.at(-1).source.sessionId, undefined);
  assert.deepEqual(f.counts(), {attaches: 1, detaches: 1, listeners: 0, attached: false});
});

test('waits for a delayed guest instead of relying on a fixed 180 ms sleep', async () => {
  const f = fixture({delay: 220});
  await f.bridge.withViewer(7, evaluate => evaluate('perform navigation'));
  assert.equal(f.calls.at(-1).source.sessionId, 'guest');
  assert.equal(f.counts().detaches, 1);
});

test('serializes repeated navigation and detaches even when the task fails', async () => {
  const f = fixture({child: false});
  const first = f.bridge.withViewer(7, async () => { await new Promise(r => setTimeout(r, 10)); throw new Error('navigation failed'); });
  const second = f.bridge.withViewer(7, () => 'second result');
  await assert.rejects(first, /navigation failed/);
  assert.equal(await second, 'second result');
  assert.deepEqual(f.counts(), {attaches: 2, detaches: 2, listeners: 0, attached: false});
});

test('viewer existence is insufficient: waits for target geometry and runs the task once', async () => {
  let firstUnready;
  const unready = new Promise(resolve => { firstUnready = resolve; });
  const f = fixture({child: false, never: true, onUnready: firstUnready});
  let calls = 0;
  const work = f.bridge.withViewer(7, () => calls++, {page: 2});
  await unready;
  assert.equal(calls, 0, 'viewer presence alone must not run the task');
  f.makeReady();
  await work;
  assert.equal(calls, 1);
  assert.equal(f.counts().listeners, 0);
});

test('unready geometry times out without running the task or leaking attachment', async () => {
  const f = fixture({child: false, never: true});
  let calls = 0;
  await assert.rejects(f.bridge.withViewer(7, () => calls++, {timeoutMs: 80}), {code: 'viewer-timeout'});
  assert.equal(calls, 0);
  assert.deepEqual(f.counts(), {attaches: 1, detaches: 1, listeners: 0, attached: false});
});

test('out-of-range page and failed PDF load fail before any task', async () => {
  for (const [settings, page, code] of [[{}, 3, 'page-unavailable'], [{failed: true}, 0, 'pdf-load-failed']]) {
    const f = fixture({child: false, ...settings});
    await assert.rejects(f.bridge.withViewer(7, () => assert.fail('must not mutate viewer'), {page}), {code});
    assert.equal(f.counts().listeners, 0);
  }
});

test('a document change, closed tab or detached target cancels a readiness wait', async () => {
  for (const [action, code] of [['changeUrl', 'document-changed'], ['close', 'tab-closed'], ['disconnect', 'viewer-detached']]) {
    const f = fixture({child: false, never: true});
    const work = f.bridge.withViewer(7, () => assert.fail('must not mutate viewer'), {url: 'file:///paper.pdf'});
    setTimeout(() => f[action]('file:///different.pdf'), 25);
    await assert.rejects(work, {code});
    assert.equal(f.counts().listeners, 0);
  }
});

test('an aborted queued request never attaches after the earlier task', async () => {
  const f = fixture({child: false}), controller = new AbortController();
  const first = f.bridge.withViewer(7, async () => new Promise(resolve => setTimeout(resolve, 40)));
  const second = f.bridge.withViewer(7, () => assert.fail('stale task'), {signal: controller.signal});
  controller.abort();
  await first;
  await assert.rejects(second, {code: 'cancelled'});
  assert.equal(f.counts().attaches, 1);
});

test('superseding a task during preparation prevents its later evaluation', async () => {
  const f = fixture({child: false});
  let current = true;
  const work = f.bridge.withViewer(7, async evaluate => {
    current = false;
    return evaluate('perform navigation');
  }, {isCurrent: () => current});
  await assert.rejects(work, {code: 'cancelled'});
  assert.equal(f.calls.filter(c => c.params?.expression === 'perform navigation').length, 0);
  assert.equal(f.counts().listeners, 0);
});

test('wrong initial document is rejected before attachment; hash navigation is allowed', async () => {
  const f = fixture({child: false});
  await assert.rejects(f.bridge.withViewer(7, () => assert.fail(), {url: 'file:///different.pdf'}), {code: 'document-changed'});
  assert.equal(f.counts().attaches, 0);
  assert.equal(await f.bridge.withViewer(7, () => 'ok', {url: 'file:///paper.pdf#page=2'}), 'ok');
});

test('transient root or guest discovery setup errors can recover without reattaching or replaying a task', async () => {
  for (const child of [false, true]) {
    const f = fixture({child, setupFailures: 2});
    let calls = 0;
    await f.bridge.withViewer(7, () => calls++, {timeoutMs: 650});
    assert.equal(calls, 1);
    assert.deepEqual(f.counts(), {attaches: 1, detaches: 1, listeners: 0, attached: false});
  }
});

test('persistent discovery failure remains bounded and user detach is never retried', async () => {
  const f = fixture({child:false, setupFailures:100});
  await assert.rejects(f.bridge.withViewer(7, () => assert.fail(), {timeoutMs:150}), {code:'viewer-timeout'});
  assert.ok(f.calls.filter(c=>c.method==='Runtime.enable').length <= 3);
  assert.equal(f.counts().listeners,0);
  const g = fixture({child:false, setupFailures:100});
  const work = g.bridge.withViewer(7, () => assert.fail());
  setTimeout(() => g.disconnect(),25);
  await assert.rejects(work, {code:'viewer-detached',detachReason:'canceled_by_user'});
  assert.deepEqual(g.counts(), {attaches:1,detaches:1,listeners:0,attached:false});
});
