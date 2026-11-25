const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

for (const [file, start, end, name] of [
  ['../extension/popup/popup.js', 'function tryViewportJump(', 'function tryViewerPrivateMethod(', 'tryViewportJump'],
  ['../extension/content.js', 'function tryViewportNavigation(', 'function dispatchChangePageEvent(', 'tryViewportNavigation'],
]) {
  const source = fs.readFileSync(require.resolve(file), 'utf8');
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `Missing source boundary in ${file}`);
  const block = source.slice(a, b);
  function run(bbox, pageApi = true) {
    const calls = [];
    const viewport = {
      convertPageToScreen: (page, point) => { calls.push(['convert', page]); return {x: point.x, y: 792-point.y}; },
      goToPageAndXy: (page, x, y) => calls.push(['xy', page, x, y]),
      goToPage: page => calls.push(['page', page]),
    };
    if (!pageApi) delete viewport.goToPage;
    const context = vm.createContext({
      pageIndex: data => data.page, bboxFromPayload: data => data.bbox,
      getViewport: () => viewport, getNativeViewport: () => viewport,
      centerViewportTarget: () => false,
      scheduleViewportOverlay: () => !!bbox, scheduleOverlay: () => !!bbox,
      targetPagePoint: () => bbox ? {x: 10, y: 700} : {x: 0, y: 0},
      validPoint: () => true, payload: {page: 2, bbox},
    });
    vm.runInContext(block, context);
    return {calls, result: vm.runInContext(`${name}(payload, {})`, context)};
  }
  test(`${name}: page-only match uses page navigation, avoiding bottom-origin coordinates`, () => {
    const {calls, result} = run(null);
    assert.deepEqual(calls, [['page', 2]]);
    assert.equal(result.ok, true);
    assert.equal(result.highlighted, false);
  });
  test(`${name}: a precise target keeps its coordinate fallback`, () => {
    const {calls, result} = run([10, 92, 30, 110]);
    assert.deepEqual(calls, [['convert', 2], ['xy', 2, 10, 92]]);
    assert.equal(result.ok, true);
    assert.equal(result.highlighted, true);
  });
  test(`${name}: missing page API defers to later page transports without inventing coordinates`, () => {
    const {calls, result} = run(null, false);
    assert.deepEqual(calls, []);
    assert.equal(result.ok, false);
  });
}
