/* Shared, read-only readiness probe. Keep this function self-contained: the
 * debugger serializes it into Chrome's PDF execution context. */
function FormulaNativeViewerState(page = 0, viewer = null, expectedUrl = '') {
  const fail = (code, error, retryable = true) => ({
    ok: false, code, error, retryable, viewerFound: !!viewer, hrefUnchanged: true,
  });
  if (!Number.isInteger(page) || page < 0) return fail('invalid-page', 'This match has no valid page.', false);
  if (!viewer && typeof document !== 'undefined') viewer = document.querySelector('pdf-viewer');
  if (!viewer) return fail('viewer-loading', 'Chrome’s PDF viewer is still opening.');
  try {
    if (expectedUrl) {
      if (!viewer.originalUrl) return fail('viewer-loading', 'Chrome’s PDF viewer is still opening.');
      if (String(viewer.originalUrl).split('#', 1)[0] !== String(expectedUrl).split('#', 1)[0]) {
        return fail('document-changed', 'The paper in this tab changed. Search the current paper again.', false);
      }
    }
    if (viewer.loadState_ === 'failed') return fail('pdf-load-failed', 'Chrome could not load this PDF.', false);
    if (viewer.loadState_ === 'loading') return fail('viewer-loading', 'The PDF is still loading.');
    let viewport;
    for (const key of ['viewport', 'viewport_']) {
      try { if (viewer[key]) { viewport = viewer[key]; break; } } catch { /* initial getter can assert */ }
    }
    if (!viewport) return fail('geometry-pending', 'The PDF page layout is not ready yet.');
    const pages = viewer.documentDimensions?.pageDimensions || viewport.pageDimensions || viewport.pageDimensions_;
    if (!Array.isArray(pages) || !pages.length) return fail('geometry-pending', 'The PDF page layout is not ready yet.');
    if (page >= pages.length) return fail('page-unavailable', `Page ${page+1} is not available in this PDF.`, false);
    if (typeof viewport.getPageScreenRect !== 'function') {
      return fail('unsupported-viewer', 'This PDF viewer does not expose usable page geometry.', false);
    }
    const rect = viewport.getPageScreenRect(page), size = viewport.size, position = viewport.position;
    if (!rect || !size || !position || ![rect.x, rect.y, rect.width, rect.height,
      size.width, size.height, position.x, position.y].every(Number.isFinite) ||
      rect.width <= 0 || rect.height <= 0 || size.width <= 0 || size.height <= 0) {
      return fail('geometry-pending', 'The PDF page layout is not ready yet.');
    }
    return {ok: true, viewerFound: true, pageCount: pages.length, hrefUnchanged: true};
  } catch {
    return fail('geometry-pending', 'The PDF page layout is not ready yet.');
  }
}
globalThis.FormulaNativeViewerState = FormulaNativeViewerState;
if (typeof module !== 'undefined') module.exports = FormulaNativeViewerState;
