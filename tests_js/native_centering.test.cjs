const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function extract(file, start, end) {
  const text = fs.readFileSync(require.resolve(file), 'utf8');
  return text.slice(text.indexOf(start), text.indexOf(end, text.indexOf(start)));
}
const sources = [
  extract('../extension/popup/popup.js', '  function centerViewportTarget(', '  function tryViewportJump('),
  extract('../extension/content.js', '  function centerViewportTarget(', '  function tryViewportNavigation('),
];
const helpers = extract('../extension/content.js', '  function finiteNumber(', '  function getOpenOrClosedShadowRoot(');
const fractions = extract('../extension/content.js', '  function pageBoxFractions(', '  function computeOverlayRect(');
const center = vm.runInNewContext(helpers + fractions + sources[0] + '\ncenterViewportTarget');
const data = {page: 2, page_w: 600, page_h: 800, bbox: [400, 650, 460, 670]};

function viewport(overrides = {}) {
  return {
    size: {width: 900, height: 800}, position: {x: 0, y: 3000},
    getPageScreenRect: () => ({x: 50, y: -900, width: 800, height: 3200/3}),
    scrollTo(p) { this.sent = p; }, ...overrides,
  };
}

test('both injection paths use the same centering calculation', () => assert.equal(sources[0], sources[1]));

test('bottom-of-page jump centers in screen pixels without moving a visible horizontal target', () => {
  const vp = viewport();
  assert.equal(center(data, vp), true);
  assert.equal(vp.sent.x, 0);
  assert.equal(vp.sent.y, 2580);
  assert.equal(vp.sent.forceSmoothScroll, false);
});

test('zoomed offscreen formula is revealed horizontally and vertically without a second zoom multiplier', () => {
  const vp = viewport({position: {x: 300, y: 5000},
    getPageScreenRect: () => ({x: -300, y: -1800, width: 1600, height: 6400/3})});
  assert.equal(center(data, vp), true);
  assert.ok(Math.abs(vp.sent.x - (430*8/3-450)) < 1e-8);
  assert.equal(vp.sent.y, 4560);
});

test('target already within horizontal margins retains the reader horizontal position', () => {
  const vp = viewport({position: {x: 123, y: 3000}});
  center(data, vp);
  assert.equal(vp.sent.x, 123);
});

test('oversized formula starts within readable margins instead of cutting off both ends', () => {
  const vp = viewport({position: {x: 500, y: 5000},
    getPageScreenRect: () => ({x: -200, y: -2000, width: 1800, height: 2400})});
  center({...data, bbox: [50, 100, 550, 700]}, vp);
  assert.equal(vp.sent.x, 426);
  assert.equal(vp.sent.y, 3276);
});

test('first-page target clamps the scroll request at document start', () => {
  const vp = viewport({position: {x: 0, y: 0},
    getPageScreenRect: () => ({x: 50, y: 3, width: 600, height: 800})});
  center({...data, page: 0, bbox: [10, 10, 20, 20]}, vp);
  assert.equal(vp.sent.y, 0);
  assert.equal(vp.sent.x, 0);
});

test('setPosition fallback preserves the same screen coordinate calculation', () => {
  const vp = viewport({scrollTo: undefined, setPosition(p, smooth) {this.sent = p; this.smooth = smooth;}});
  assert.equal(center(data, vp), true);
  assert.equal(vp.sent.y, 2580);
  assert.equal(vp.smooth, false);
});

test('unavailable or invalid geometry falls through without guessing or scrolling', () => {
  for (const vp of [null, {}, viewport({size: {width: 0, height: 800}}),
    viewport({position: {x: NaN, y: 0}}), viewport({getClockwiseRotations: () => .5}),
    viewport({getPageScreenRect: () => {throw Error('not ready');}}),
    viewport({scrollTo: undefined}), viewport({getPageScreenRect: undefined})]) {
    assert.equal(center(data, vp), false);
    assert.equal(vp?.sent, undefined);
  }
  for (const invalid of [{...data, bbox: null}, {...data, bbox: [1,2,1,2]},
    {...data, page: -1}, {...data, page_h: 0}, {...data, bbox: [1,2,NaN,4]}]) {
    const vp = viewport();
    assert.equal(center(invalid, vp), false);
    assert.equal(vp.sent, undefined);
  }
});

test('quarter-turns rotate the target once in normalized page space', () => {
  assert.equal(fractions, extract('../extension/popup/popup.js', '  function pageBoxFractions(', '  function computeOverlayRect('));
  const transform = vm.runInNewContext(helpers + fractions + '\npageBoxFractions');
  const expected = [[2/3,13/16,23/30,67/80], [13/80,2/3,3/16,23/30],
    [7/30,13/80,1/3,3/16], [13/16,7/30,67/80,1/3]];
  for (let turn = 0; turn < 4; turn++) {
    const actual = transform(data, {getClockwiseRotations: () => turn});
    expected[turn].forEach((n, i) => assert.ok(Math.abs(actual[i]-n) < 1e-12));
  }
  const vp = viewport({getClockwiseRotations: () => 1,
    getPageScreenRect: () => ({x: 100, y: 50, width: 800, height: 600})});
  assert.equal(center(data, vp), true);
  assert.equal(vp.sent.y, 3080);
});

const geometry = extract('../extension/content.js', '  function rectFromDom(', '  // Shared by installDynamicOverlay');
function overlay() {
  return vm.runInNewContext(helpers+geometry+'\ncomputeOverlayRect', {HIGHLIGHT_PAD_PX: 1,
    getScroller: () => ({getBoundingClientRect: () => ({left: 0, top: 0, right: 900, bottom: 800, width: 900, height: 800})})});
}
test('a rotated highlight uses the same target as navigation', () => {
  const rect = overlay()(data, viewport({getClockwiseRotations: () => 1,
    getPageScreenRect: () => ({x: 100, y: 50, width: 800, height: 600})}), {});
  for (const [key, value] of Object.entries({left: 229, top: 449, right: 251, bottom: 511})) {
    assert.ok(Math.abs(rect[key]-value) < 1e-9, key);
  }
});
test('an offscreen native target cannot reappear through differently centered fallback geometry', () => {
  assert.equal(overlay()(data, viewport({
    getPageScreenRect: () => ({x: 100, y: -1000, width: 600, height: 800}),
    getPageInsetDimensions: () => ({x: 0, y: 0, width: 600, height: 800}),
    position: {x: 0, y: 0},
  }), {}), null);
});
