const test = require('node:test');
const assert = require('node:assert/strict');
const probe = require('../extension/native-viewer-state.js');
function viewer() {
  return {originalUrl: 'file:///paper.pdf', loadState_: 'success',
    documentDimensions: {pageDimensions: [{}, {}]},
    viewport: {size: {width: 800, height: 600}, position: {x: 0, y: 0},
      getPageScreenRect: () => ({x: -50, y: -1000, width: 1200, height: 1600})}};
}
test('loaded offscreen pages are ready; they do not have to be visible yet', () => {
  assert.equal(probe(1, viewer(), 'file:///paper.pdf#page=1').ok, true);
});
test('loading and failed documents are not made ready by retained dimensions', () => {
  const v = viewer();
  v.loadState_ = 'loading'; assert.equal(probe(0, v).retryable, true); assert.equal(probe(0, v).ok, false);
  v.loadState_ = 'failed'; assert.equal(probe(0, v).code, 'pdf-load-failed'); assert.equal(probe(0, v).retryable, false);
});
test('initial viewport getter assertions and incomplete page geometry stay pending', () => {
  const v = viewer(); Object.defineProperty(v, 'viewport', {get() {throw Error('not initialized');}});
  assert.equal(probe(0, v).code, 'geometry-pending');
  for (const geometry of [{x: 0, y: 0, width: 0, height: 100}, {x: NaN, y: 0, width: 100, height: 100}]) {
    const v = viewer(); v.viewport.getPageScreenRect = () => geometry;
    assert.equal(probe(0, v).ok, false);
  }
});
test('invalid pages and document changes fail without reading or mutating geometry', () => {
  const v = viewer(); v.viewport.getPageScreenRect = () => assert.fail('must not read geometry');
  assert.equal(probe(-1, v).code, 'invalid-page');
  assert.equal(probe(2, v).code, 'page-unavailable');
  assert.equal(probe(0, v, 'file:///other.pdf').code, 'document-changed');
});
