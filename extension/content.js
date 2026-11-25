'use strict';

(() => {
  const LOAD_GUARD = '__formulaSearchNativeInstantContentLoaded';
  if (globalThis[LOAD_GUARD]) return;
  globalThis[LOAD_GUARD] = true;

  const OVERLAY_ATTR = 'data-formula-search-content-overlay';
  const HIGHLIGHT_VISIBLE_MS = 650;
  const HIGHLIGHT_REMOVE_MS = 900;
  const HIGHLIGHT_PAD_PX = 1.0;
  const JUMP_MARGIN_PDF_PT = 4;
  const JUMP_X_MARGIN_PDF_PT = 4;
  // Below this fraction of the formula's box actually on screen, treat it as
  // "scrolled away" -- hide the highlight/info-card/connector rather than
  // pin them to whatever sliver (or clamped, off-screen) position computeOverlayRect
  // would otherwise report.
  const VISIBILITY_THRESHOLD = 0.1;
  // Applied to the highlight's left/top/width/height on every reflow update
  // after the first (see the overlay controller's `everShown` gate below).
  // Short enough to track real scrolling closely, long enough to smooth out
  // a single stale geometry read into an imperceptible wobble instead of a
  // visible teleport.
  const POSITION_TRANSITION = 'left 90ms ease-out, top 90ms ease-out, width 90ms ease-out, height 90ms ease-out';

  // Captured from the jump payload for the back button. This isolated content
  // script uses same-extension messaging for its shared storage operations.
  let relayExtensionId = '';

  function finiteNumber(value, fallback = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function pageIndex(payload) {
    const p = Number(payload && payload.page);
    return Number.isInteger(p) && p >= 0 ? p : -1;
  }

  function bboxFromPayload(payload) {
    if (!Array.isArray(payload && payload.bbox) || payload.bbox.length < 4) return null;
    const box = payload.bbox.slice(0, 4).map(Number);
    if (!box.every(Number.isFinite)) return null;
    const x0 = Math.min(box[0], box[2]);
    const y0 = Math.min(box[1], box[3]);
    const x1 = Math.max(box[0], box[2]);
    const y1 = Math.max(box[1], box[3]);
    return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
  }

  function getOpenOrClosedShadowRoot(element) {
    if (!element) return null;
    try {
      if (element.shadowRoot) return element.shadowRoot;
    } catch {
      // Continue to extension-only API.
    }
    try {
      if (element.renderRoot) return element.renderRoot;
    } catch {
      // Continue to extension-only API.
    }
    try {
      if (typeof chrome !== 'undefined' && chrome.dom && typeof chrome.dom.openOrClosedShadowRoot === 'function') {
        return chrome.dom.openOrClosedShadowRoot(element);
      }
    } catch {
      // Not available in this execution world.
    }
    return null;
  }

  function safeQuery(root, selector) {
    try {
      return root && typeof root.querySelector === 'function' ? root.querySelector(selector) : null;
    } catch {
      return null;
    }
  }

  function childNodes(root) {
    try {
      return root && root.children ? Array.from(root.children) : [];
    } catch {
      return [];
    }
  }

  function findDeep(root, selector, maxDepth = 8, seen = new Set()) {
    if (!root || maxDepth < 0 || seen.has(root)) return null;
    seen.add(root);

    const direct = safeQuery(root, selector);
    if (direct) return direct;

    const roots = [];
    const rootShadow = getOpenOrClosedShadowRoot(root);
    if (rootShadow) roots.push(rootShadow);

    for (const node of childNodes(root)) {
      const shadow = getOpenOrClosedShadowRoot(node);
      if (shadow) roots.push(shadow);
    }

    for (const nextRoot of roots) {
      const found = findDeep(nextRoot, selector, maxDepth - 1, seen);
      if (found) return found;
    }

    for (const node of childNodes(root)) {
      const found = findDeep(node, selector, maxDepth - 1, seen);
      if (found) return found;
    }

    return null;
  }

  function getPdfViewerElement() {
    return safeQuery(document, 'pdf-viewer') || findDeep(document, 'pdf-viewer') || null;
  }

  function getViewerRoot(viewer) {
    return getOpenOrClosedShadowRoot(viewer) || viewer || null;
  }

  function getScroller(viewer) {
    const root = getViewerRoot(viewer);
    return safeQuery(root, '#scroller') ||
      findDeep(root, '#scroller') ||
      safeQuery(document, '#scroller') ||
      document.scrollingElement ||
      document.documentElement ||
      document.body ||
      null;
  }

  function targetPagePoint(payload) {
    const box = bboxFromPayload(payload);
    if (!box) return { x: 0, y: 0 };
    const pageHeight = finiteNumber(payload.page_h, 0);
    const margin = pageHeight > 0 ? Math.min(JUMP_MARGIN_PDF_PT, pageHeight * 0.008) : JUMP_MARGIN_PDF_PT;
    const topWithMargin = Math.max(0, box[1] - margin);
    return {
      x: Math.max(0, box[0] - JUMP_X_MARGIN_PDF_PT),
      y: pageHeight > 0 ? Math.max(0, pageHeight - topWithMargin) : topWithMargin,
    };
  }

  function getNativeViewport(viewer) {
    if (!viewer) return null;
    for (const key of ['viewport', 'viewport_']) {
      try {
        if (viewer[key]) return viewer[key];
      } catch {
        // Isolated content scripts usually cannot read page-world custom fields.
      }
    }
    return null;
  }

  function clearOverlay() {
    try {
      const controller = globalThis.__formulaSearchOverlayController;
      if (controller && typeof controller.destroy === 'function') controller.destroy();
      globalThis.__formulaSearchOverlayController = null;
      document.querySelectorAll(`[${OVERLAY_ATTR}]`).forEach((node) => node.remove());
    } catch {
      // Cleanup only.
    }
  }

  function validPoint(point) {
    return point && Number.isFinite(point.x) && Number.isFinite(point.y);
  }

  function rectFromDom(domRect) {
    if (!domRect || !Number.isFinite(domRect.left + domRect.top + domRect.right + domRect.bottom)) return null;
    return { left: domRect.left, top: domRect.top, right: domRect.right, bottom: domRect.bottom, width: domRect.width, height: domRect.height };
  }

  function viewportClipRect(viewer) {
    const scroller = getScroller(viewer);
    if (!scroller || typeof scroller.getBoundingClientRect !== 'function') return null;
    try {
      const rect = rectFromDom(scroller.getBoundingClientRect());
      if (!rect) return null;
      return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
    } catch {
      return null;
    }
  }

  function intersectRects(a, b) {
    if (!a) return b;
    if (!b) return a;
    const left = Math.max(a.left, b.left);
    const top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    if (right <= left || bottom <= top) return null;
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }

  function rectArea(rect) {
    return rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
  }

  function pointInside(rect, x, y) {
    return !!rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  }

  function pageScreenRectCandidates(page, viewport, viewer) {
    const clip = viewportClipRect(viewer);
    const candidates = [];

    function addFixedPageRect(x, y, width, height, mode, priority = 0) {
      if (!Number.isFinite(x + y + width + height) || width <= 0 || height <= 0) return;
      candidates.push({ x, y, width, height, clip, mode, priority });
    }

    function addNativePageScreenRect(rect) {
      if (!rect) return;
      const x = Number(rect.x);
      const y = Number(rect.y);
      const width = Number(rect.width);
      const height = Number(rect.height);
      if (!Number.isFinite(x + y + width + height) || width <= 0 || height <= 0) return;

      // Chromium's getPageScreenRect() is computed from the PDF viewer scroller
      // state. In the standard full-page viewer that coordinate space is the
      // scroller viewport, not the browser window. Our overlay is fixed to the
      // browser window, so the scroller-adjusted candidate is preferred.
      //
      // A low-priority "raw" fallback candidate (unadjusted x/y, in case some
      // embedded/OOPIF build already reports window coordinates) used to be
      // added here too whenever a clip was found -- but it still carried that
      // SAME window-space `clip` for computeOverlayRect's visibility test,
      // even though its own x/y are in a completely different (scroller-
      // local) coordinate space. That mismatch is normally harmless (the
      // scroller-adjusted candidate wins by a wide priority margin whenever
      // both are genuinely on-screen), but once the page scrolls far enough
      // that the scroller-adjusted candidate is correctly excluded as
      // off-screen, the raw candidate's coordinates can numerically
      // (coincidentally) still overlap that window-space clip -- becoming
      // the only surviving candidate and rendering the highlight at a
      // nonsensical position for a few more scroll ticks before it finally
      // scrolls out too. That's the exact "highlight jumps left/up a bit,
      // then needs another scroll to disappear" bug: reproduced live by
      // scrolling a pinned highlight until its page's mode flipped from
      // native-page-screen-rect-scroller to native-page-screen-rect-raw.
      // Since it never legitimately wins over a valid scroller-adjusted
      // candidate anyway, dropping it when a clip exists costs nothing in
      // the working case and removes the bug in the broken one.
      if (clip) {
        addFixedPageRect(x + clip.left, y + clip.top, width, height, 'native-page-screen-rect-scroller', 6);
      } else {
        addFixedPageRect(x, y, width, height, 'native-page-screen-rect-raw', 0);
      }
    }

    try {
      if (viewport && typeof viewport.getPageScreenRect === 'function') {
        addNativePageScreenRect(viewport.getPageScreenRect(page));
      }
    } catch {
      // Try fallback below.
    }
    // A valid native rectangle is authoritative even when the target is off
    // screen. A differently centered fallback must not resurrect it elsewhere.
    if (candidates.length) return candidates;

    try {
      if (viewport && typeof viewport.getPageInsetDimensions === 'function') {
        const dims = viewport.getPageInsetDimensions(page);
        const zoom = typeof viewport.getZoom === 'function' ? viewport.getZoom() : 1;
        const pos = viewport.position || { x: 0, y: 0 };
        const originX = clip ? clip.left : 0;
        const originY = clip ? clip.top : 0;

        // getPageInsetDimensions() returns document/page-layout coordinates.
        // Convert it directly to fixed-window coordinates once. Do not add a
        // second scroller offset; that was the source of out-of-paper highlights
        // after zoom changes in some Chrome builds.
        addFixedPageRect(
          originX + Number(dims.x || 0) * zoom - Number(pos.x || 0),
          originY + Number(dims.y || 0) * zoom - Number(pos.y || 0),
          Number(dims.width || 0) * zoom,
          Number(dims.height || 0) * zoom,
          'native-page-inset-dimensions-fixed',
          4
        );
      }
    } catch {
      // No native geometry available.
    }
    return candidates;
  }

  function pageBoxFractions(payload, viewport) {
    const box = bboxFromPayload(payload);
    const width = finiteNumber(payload.page_w, 0), height = finiteNumber(payload.page_h, 0);
    if (!box || !viewport || width <= 0 || height <= 0) return null;
    try {
      const turns = typeof viewport.getClockwiseRotations === 'function' ? viewport.getClockwiseRotations() : 0;
      if (!Number.isInteger(turns)) return null;
      const [left, top, right, bottom] = [box[0]/width, box[1]/height, box[2]/width, box[3]/height];
      if (![left, top, right, bottom].every(Number.isFinite)) return null;
      // Backend boxes are relative to the PDF's initial displayed orientation.
      // Chrome's rotation is additional; page rectangles already have the
      // resulting width/height, so rotate unit-square coordinates only once.
      switch ((turns % 4 + 4) % 4) {
        case 1: return [1-bottom, left, 1-top, right];
        case 2: return [1-right, 1-bottom, 1-left, 1-top];
        case 3: return [top, 1-right, bottom, 1-left];
        default: return [left, top, right, bottom];
      }
    } catch { return null; }
  }

  function computeOverlayRect(payload, viewport, viewer) {
    const page = pageIndex(payload);
    const box = pageBoxFractions(payload, viewport);
    if (page < 0 || !box) return null;

    let best = null;
    let bestScore = -Infinity;
    for (const pageRect of pageScreenRectCandidates(page, viewport, viewer)) {
      const x1 = pageRect.x + box[0] * pageRect.width;
      const x2 = pageRect.x + box[2] * pageRect.width;
      const y1 = pageRect.y + box[1] * pageRect.height;
      const y2 = pageRect.y + box[3] * pageRect.height;
      const pad = Math.min(HIGHLIGHT_PAD_PX, Math.max(0.35, Math.min(Math.abs(x2 - x1), Math.abs(y2 - y1)) * 0.10));
      const raw = {
        left: Math.min(x1, x2) - pad,
        top: Math.min(y1, y2) - pad,
        right: Math.max(x1, x2) + pad,
        bottom: Math.max(y1, y2) + pad,
      };
      raw.width = raw.right - raw.left;
      raw.height = raw.bottom - raw.top;
      if (!Number.isFinite(raw.left + raw.top + raw.width + raw.height) || raw.width < 1 || raw.height < 1) continue;

      const pageClip = { left: pageRect.x, top: pageRect.y, right: pageRect.x + pageRect.width, bottom: pageRect.y + pageRect.height, width: pageRect.width, height: pageRect.height };
      // pageRect.clip is the scroller's visible-viewport rect. When it's
      // present but does NOT overlap the page's own rect, the page has
      // scrolled entirely out of view -- there is no visible region for
      // this candidate at all, and it must be skipped rather than falling
      // back to the unclipped page rect. That fallback used to make an
      // off-screen formula's box "clip" against the whole (off-screen) page
      // instead of nothing, producing a technically-valid but wildly
      // off-screen rect that the caller then clamped back into view,
      // pinning the highlight/info-card near the viewport edge forever
      // instead of hiding them. Only fall back to the page rect alone when
      // there is genuinely no viewport clip to test against.
      const clip = pageRect.clip ? intersectRects(pageClip, pageRect.clip) : pageClip;
      if (pageRect.clip && !clip) continue;
      const clipped = intersectRects(raw, clip || pageClip);
      if (!clipped || clipped.width < 1 || clipped.height < 1) continue;

      const cx = clipped.left + clipped.width / 2;
      const cy = clipped.top + clipped.height / 2;
      const visibleFraction = rectArea(clipped) / Math.max(1, rectArea(raw));
      let score = visibleFraction * 10 + Number(pageRect.priority || 0);
      if (pointInside(pageRect.clip, cx, cy)) score += 6;
      if (pointInside(pageClip, cx, cy)) score += 4;
      if (clipped.width <= pageRect.width * 0.92 && clipped.height <= pageRect.height * 0.22) score += 1;
      if (payload.bbox_source === 'query-subspan' && clipped.width > pageRect.width * 0.50) score -= 3;
      if (score > bestScore) {
        bestScore = score;
        best = { ...clipped, mode: pageRect.mode, visibleFraction };
      }
    }
    return best;
  }

  // Shared by installDynamicOverlay and showInfoPanel so both react to
  // exactly the same set of triggers (scroller/window scroll, zoom via the
  // change-zoom event PDF viewers dispatch plus a MutationObserver safety
  // net, resize, fullscreen). Previously showInfoPanel only listened for
  // plain window resize/scroll and tried to piggyback on the highlight's own
  // update cycle via a controller reference grabbed at call time -- but
  // installDynamicOverlay runs a frame later (see scheduleOverlay's
  // requestAnimationFrame), so that reference was usually still null and the
  // hook silently never attached. Giving both call sites their own identical,
  // self-contained listener set removes that race entirely.
  function attachReflowListeners(viewer, onReflow) {
    let updateQueued = false;
    let settleTimer = null;
    const scheduleUpdate = () => {
      if (!updateQueued) {
        updateQueued = true;
        window.requestAnimationFrame(() => {
          updateQueued = false;
          onReflow();
        });
      }
      // Chromium's PDF plugin reports getPageScreenRect()/getPageInsetDimensions()
      // asynchronously relative to the scroll position -- during a fast, sustained
      // scroll the rAF tick above can read geometry that hasn't caught up yet,
      // which used to show the highlight/card settled in a plausible-looking but
      // stale spot (often a corner) for several more scroll events until the
      // real position happened to drift off-screen on its own. Re-checking once
      // ~120ms after the last scroll/resize event in a burst re-reads geometry
      // once Chromium has settled, correcting (or hiding) it promptly instead of
      // waiting on scroll momentum to carry it out of view.
      if (settleTimer) window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(() => {
        settleTimer = null;
        onReflow();
      }, 120);
    };
    const cleanups = [];
    cleanups.push(() => { if (settleTimer) window.clearTimeout(settleTimer); });
    const addListener = (target, event, options) => {
      if (!target || typeof target.addEventListener !== 'function') return;
      target.addEventListener(event, scheduleUpdate, options || { passive: true });
      cleanups.push(() => target.removeEventListener(event, scheduleUpdate, options || { passive: true }));
    };

    const scroller = getScroller(viewer);
    addListener(scroller, 'scroll', { passive: true });
    addListener(window, 'scroll', { passive: true });
    addListener(window, 'resize', { passive: true });
    addListener(window.visualViewport, 'resize', { passive: true });
    addListener(window.visualViewport, 'scroll', { passive: true });
    addListener(document.body, 'change-zoom', { passive: true });
    addListener(document, 'fullscreenchange', { passive: true });
    addListener(document, 'wheel', { passive: true, capture: true });
    addListener(document, 'keydown', { passive: true, capture: true });

    try {
      const resizeObserver = new ResizeObserver(scheduleUpdate);
      if (scroller) resizeObserver.observe(scroller);
      const root = getViewerRoot(viewer);
      if (root && root.host) resizeObserver.observe(root.host);
      cleanups.push(() => resizeObserver.disconnect());
    } catch {
      // ResizeObserver may not be available in unusual frames.
    }

    try {
      const mutationObserver = new MutationObserver(scheduleUpdate);
      mutationObserver.observe(document.body || document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['style', 'class', 'aria-valuenow'],
      });
      cleanups.push(() => mutationObserver.disconnect());
    } catch {
      // MutationObserver is only a zoom/layout-change safety net.
    }

    return {
      scheduleUpdate,
      destroy() {
        cleanups.forEach((fn) => { try { fn(); } catch { /* noop */ } });
        cleanups.length = 0;
      },
    };
  }

  function installDynamicOverlay(payload, viewport, viewer) {
    if (!bboxFromPayload(payload) || !viewport) return false;
    const pinned = !!payload.showInfoPanel;
    clearOverlay();

    const overlay = document.createElement('div');
    overlay.setAttribute(OVERLAY_ATTR, 'true');
    Object.assign(overlay.style, {
      position: 'fixed',
      left: '-9999px',
      top: '-9999px',
      width: '1px',
      height: '1px',
      zIndex: '2147483647',
      pointerEvents: 'none',
      boxSizing: 'border-box',
      background: 'rgba(255, 220, 0, 0.20)',
      border: '1px solid rgba(245, 158, 11, 0.98)',
      borderRadius: '2px',
      boxShadow: '0 0 5px rgba(245, 158, 11, 0.48)',
      transition: 'opacity 120ms ease',
      opacity: '1',
    });
    (document.body || document.documentElement).appendChild(overlay);

    const controller = {
      overlay,
      pinned,
      timers: [],
      cleanups: [],
      dead: false,
      currentRect: null,
      fadedOut: false,
      // Set by showInfoPanel's reposition() whenever the info card's own
      // bounding box currently overlaps this highlight on screen -- with the
      // card drawn on top (or, since the overlay's z-index actually wins,
      // showing a stray yellow patch bleeding through the white card), the
      // mark under it is confusing rather than useful, so it hides until the
      // card moves (by scroll, drag, or resize) off of it again.
      coveredByCard: false,
      // Set once the overlay has been positioned for the first time. Gates
      // POSITION_TRANSITION: the very first placement (right after a jump)
      // must snap there instantly, but every reflow update after that eases
      // toward its target instead of snapping. Chromium's PDF plugin can
      // report getPageScreenRect()/getPageInsetDimensions() a frame or two
      // behind the actual on-screen scroll position under fast/continuous
      // scrolling -- with an instant snap, a single stale read in that
      // window showed up as the highlight visibly teleporting to a wrong
      // (often corner-clipped) spot for a frame before either the geometry
      // caught up or the visibility check hid it. Easing the position
      // absorbs that kind of single-frame glitch into an imperceptible
      // wobble instead of a visible jump.
      everShown: false,
      update() {
        if (controller.dead || !overlay.isConnected) return false;
        const rect = computeOverlayRect(payload, viewport, viewer);
        const visible = !!rect && !(rect.visibleFraction < VISIBILITY_THRESHOLD);
        if (!visible) {
          // Scrolled/zoomed away from the formula: hide rather than pin to
          // a clamped, off-screen (or barely-clipped) position. currentRect
          // stays null so showInfoPanel's own reposition (which reads this
          // via its own computeOverlayRect call) hides the card/connector
          // the same way, and reappears the same way once back in view.
          controller.currentRect = null;
          overlay.style.opacity = '0';
          return false;
        }
        controller.currentRect = rect;
        if (controller.everShown) {
          overlay.style.transition = `${POSITION_TRANSITION}, opacity 120ms ease`;
        }
        controller.everShown = true;
        Object.assign(overlay.style, {
          left: `${Math.max(-20, rect.left)}px`,
          top: `${Math.max(-20, rect.top)}px`,
          width: `${Math.max(3, rect.width)}px`,
          height: `${Math.max(3, rect.height)}px`,
          opacity: (controller.fadedOut || controller.coveredByCard) ? '0' : '1',
        });
        overlay.dataset.placementMode = rect.mode || '';
        return true;
      },
      destroy() {
        if (controller.dead) return;
        controller.dead = true;
        controller.timers.forEach((id) => window.clearTimeout(id));
        controller.cleanups.forEach((fn) => { try { fn(); } catch { /* noop */ } });
        controller.cleanups.length = 0;
        if (overlay.isConnected) overlay.remove();
      },
    };
    globalThis.__formulaSearchOverlayController = controller;

    const reflow = attachReflowListeners(viewer, () => controller.update());
    const scheduleUpdate = reflow.scheduleUpdate;
    controller.cleanups.push(() => reflow.destroy());

    for (const delay of [0, 25, 70, 140, 260, 420]) {
      controller.timers.push(window.setTimeout(scheduleUpdate, delay));
    }
    if (!pinned) {
      // A plain jump/highlight (no info panel) is a transient "there it is"
      // flash. A highlight opened alongside an info panel stays lit -- and
      // connected to it via showInfoPanel's connector line -- until the
      // panel is closed or another formula is selected.
      controller.timers.push(window.setTimeout(() => { controller.fadedOut = true; overlay.style.opacity = '0'; }, HIGHLIGHT_VISIBLE_MS));
      controller.timers.push(window.setTimeout(() => controller.destroy(), HIGHLIGHT_REMOVE_MS));
    }
    return controller.update();
  }

  function scheduleOverlay(payload, viewport, viewer) {
    if (!bboxFromPayload(payload) || !viewport) return false;
    window.requestAnimationFrame(() => installDynamicOverlay(payload, viewport, viewer));
    return true;
  }

  function centerViewportTarget(data, viewport) {
    const box = pageBoxFractions(data, viewport);
    const page = pageIndex(data);
    if (!box || page < 0) return false;
    if (typeof viewport.getPageScreenRect !== 'function' ||
        (typeof viewport.scrollTo !== 'function' && typeof viewport.setPosition !== 'function')) return false;
    try {
      const rect = viewport.getPageScreenRect(page), size = viewport.size, pos = viewport.position;
      if (!rect || !size || !pos || !Number.isFinite(rect.x + rect.y + rect.width + rect.height + size.width + size.height + pos.x + pos.y) ||
          rect.width <= 0 || rect.height <= 0 || size.width <= 0 || size.height <= 0) return false;
      // getPageScreenRect is already zoomed and relative to the PDF viewport.
      // scrollTo drives Chromium's remote PDF scroller as well as its DOM one.
      const left = rect.x + box[0] * rect.width;
      const right = rect.x + box[2] * rect.width;
      const top = rect.y + box[1] * rect.height;
      const bottom = rect.y + box[3] * rect.height;
      const marginX = Math.min(24, size.width * .08), marginY = Math.min(24, size.height * .08);
      let dx = 0;
      if (left < marginX || right > size.width - marginX) {
        dx = right - left > size.width - 2 * marginX ? left - marginX : (left + right - size.width) / 2;
      }
      const dy = bottom - top > size.height - 2 * marginY ? top - marginY : (top + bottom - size.height) / 2;
      const position = {x: Math.max(0, pos.x + dx), y: Math.max(0, pos.y + dy)};
      if (typeof viewport.scrollTo === 'function') viewport.scrollTo({...position, forceSmoothScroll: false});
      else viewport.setPosition(position, false);
      return true;
    } catch {
      return false;
    }
  }

  function tryViewportNavigation(payload, viewer) {
    const page = pageIndex(payload);
    const viewport = getNativeViewport(viewer);
    if (!viewport || page < 0) return { ok: false, reason: 'native viewport API unavailable in content world' };

    try {
      if (!bboxFromPayload(payload) && typeof viewport.goToPage === 'function') {
        viewport.goToPage(page);
        return {ok: true, mode: 'content-native-viewport-page', highlighted: false};
      }
      if (centerViewportTarget(payload, viewport)) {
        return {ok: true, mode: 'content-native-viewport-context', highlighted: scheduleOverlay(payload, viewport, viewer)};
      }
      const goToXy = viewport.goToPageAndXy || viewport.goToPageAndXY;
      if (bboxFromPayload(payload) && typeof viewport.convertPageToScreen === 'function' && typeof goToXy === 'function') {
        const point = viewport.convertPageToScreen(page, targetPagePoint(payload));
        if (validPoint(point)) {
          goToXy.call(viewport, page, point.x, point.y);
          return { ok: true, mode: 'content-native-viewport-xy', highlighted: scheduleOverlay(payload, viewport, viewer) };
        }
      }
      if (typeof viewport.goToPage === 'function') {
        viewport.goToPage(page);
        return { ok: true, mode: 'content-native-viewport-page', highlighted: scheduleOverlay(payload, viewport, viewer) };
      }
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }

    return { ok: false, reason: 'viewport jump methods unavailable' };
  }

  function dispatchChangePageEvent(target, payload, origin) {
    const page = pageIndex(payload);
    const box = bboxFromPayload(payload);
    const point = targetPagePoint(payload);
    const eventName = box ? 'change-page-and-xy' : 'change-page';
    const detail = box
      ? { page, x: point.x, y: point.y, origin }
      : { page, origin };
    target.dispatchEvent(new CustomEvent(eventName, { bubbles: true, composed: true, detail }));
    return eventName;
  }

  function tryChangePageEvent(payload, viewer) {
    const page = pageIndex(payload);
    if (page < 0 || !viewer) return { ok: false, reason: 'viewer unavailable' };
    const root = getViewerRoot(viewer);
    const targets = [
      safeQuery(root, '#sidenav'),
      safeQuery(root, 'viewer-pdf-sidenav'),
      findDeep(root, 'viewer-pdf-sidenav'),
      document.body,
    ].filter(Boolean);
    if (!targets.length) return { ok: false, reason: 'native event target unavailable' };

    try {
      let eventName = '';
      for (const target of targets) {
        eventName = dispatchChangePageEvent(target, payload, 'formula-search');
      }
      const viewport = getNativeViewport(viewer);
      return { ok: true, mode: `content-native-event-${eventName}`, highlighted: scheduleOverlay(payload, viewport, viewer) };
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }
  }

  function approximateScroll(payload, viewer) {
    const page = pageIndex(payload);
    if (page < 0 || !viewer) return { ok: false, reason: 'viewer unavailable' };
    const scroller = getScroller(viewer);
    if (!scroller) return { ok: false, reason: 'no native scroller found' };

    let docLength = 0;
    const viewport = getNativeViewport(viewer);
    for (const candidate of [
      viewer.docLength,
      viewer.docLength_,
      viewport && viewport.documentDimensions && viewport.documentDimensions.length,
      viewport && viewport.pageDimensions && viewport.pageDimensions.length,
    ]) {
      const n = Number(candidate);
      if (Number.isInteger(n) && n > 0) {
        docLength = n;
        break;
      }
    }
    if (!docLength) docLength = Math.max(page + 1, 1);

    const box = bboxFromPayload(payload);
    const pageHeight = finiteNumber(payload.page_h, 0);
    const ratio = box && pageHeight > 0 ? Math.max(0, Math.min(1, box[1] / pageHeight)) : 0;
    const totalHeight = scroller.scrollHeight || scroller.offsetHeight || 0;
    const pageHeightCss = docLength ? totalHeight / docLength : 0;
    const targetTop = Math.max(0, pageHeightCss * page + pageHeightCss * ratio - 8);

    try {
      if (typeof scroller.scrollTo === 'function') {
        scroller.scrollTo({ top: targetTop, left: 0, behavior: 'auto' });
      } else {
        scroller.scrollTop = targetTop;
        scroller.scrollLeft = 0;
      }
      return { ok: true, mode: 'content-native-scroller-approximation', highlighted: false };
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }
  }

  const INFO_PANEL_ATTR = 'data-formula-search-content-info-panel';
  const INFO_PANEL_STYLE_ID = 'formula-search-content-info-panel-style';
  const CONNECTOR_ATTR = 'data-formula-search-content-connector';
  const INFO_ACCENT = '#f59e0b'; // same amber as the highlight box, for a consistent look

  // === FORMULA COPY (native) BEGIN ===
  function appendLatexCopyControl(parent, rawLatex) {
    if (typeof rawLatex !== 'string' || !rawLatex) return;
    const controls = document.createElement('div');
    controls.className = 'fs-info-card-copy-controls';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'fs-info-card-copy';
    button.textContent = 'Copy LaTeX';
    button.title = 'Copy the original formula without preview colors or a reference';
    const status = document.createElement('span');
    status.className = 'fs-info-card-copy-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    controls.append(button, status);
    parent.appendChild(controls);
    let busy = false;
    let source = null;
    button.addEventListener('click', async event => {
      event.stopPropagation();
      if (busy) return;
      busy = true;
      button.setAttribute('aria-busy', 'true');
      status.textContent = 'Copying…';
      try {
        if (typeof navigator === 'undefined' || typeof navigator.clipboard?.writeText !== 'function') {
          throw new Error('Clipboard unavailable');
        }
        await navigator.clipboard.writeText(rawLatex);
        if (controls.isConnected) status.textContent = 'Copied';
      } catch {
        if (!controls.isConnected) return;
        status.textContent = 'Could not copy. Copy the text below instead.';
        if (!source) {
          source = document.createElement('textarea');
          source.className = 'fs-info-card-copy-source';
          source.readOnly = true;
          source.spellcheck = false;
          source.setAttribute('aria-label', 'Original LaTeX to copy');
          source.value = rawLatex;
          controls.appendChild(source);
        }
        source.focus({preventScroll: true});
        source.select();
      } finally {
        busy = false;
        button.removeAttribute('aria-busy');
      }
    });
  }
  // === FORMULA COPY (native) END ===

  function ensureInfoPanelStyles() {
    // Reuses an existing tag rather than skipping when one is already
    // present: the tag lives in the PDF page's own DOM, not the
    // extension's, so it survives popup reloads, content-script reinjection,
    // and even `chrome://extensions` reload -- none of those touch a page
    // that's already open. Without overwriting textContent here, a tab left
    // open across an extension code update keeps rendering whatever CSS was
    // current the first time its info panel was ever opened, forever.
    let style = document.getElementById(INFO_PANEL_STYLE_ID);
    if (!style) {
      style = document.createElement('style');
      style.id = INFO_PANEL_STYLE_ID;
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent = `
      .fs-info-card { position: fixed; z-index: 2147483646; display: flex; flex-direction: column; width: min(340px, calc(100vw - 24px)); height: auto; max-height: min(70vh, 520px); min-width: 220px; min-height: 140px; max-width: 92vw; overflow: hidden; resize: none; background: linear-gradient(180deg, #ffffff, #fffdf8); border: 1px solid rgba(15,23,42,0.12); border-left: 4px solid ${INFO_ACCENT}; border-radius: 16px; box-shadow: 0 16px 32px rgba(15,23,42,0.18), 0 2px 8px rgba(15,23,42,0.08); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1e293b; pointer-events: auto; }
      .fs-info-card-header { flex: 0 0 auto; display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; padding: 12px 14px 10px 16px; background: linear-gradient(135deg, #fffaf0, #fff3e0); border-bottom: 1px solid rgba(245,158,11,0.22); cursor: move; user-select: none; touch-action: none; }
      .fs-info-card-eyebrow { margin: 0; color: #b45309; font-size: 10px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
      .fs-info-card-title { margin: 3px 0 0; font-size: 12.5px; font-weight: 700; color: #1e293b; word-break: break-word; }
      .fs-info-card-close { flex: 0 0 auto; width: 24px; height: 24px; padding: 0; border: 1px solid rgba(15,23,42,0.14); border-radius: 999px; background: #ffffff; color: #64748b; font-size: 13px; font-weight: 700; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center; }
      .fs-info-card-close:hover { background: #f1f5f9; color: #1e293b; }
      .fs-info-card-back { flex: 0 0 auto; width: 24px; height: 24px; padding: 0; border: 1px solid rgba(15,23,42,0.14); border-radius: 999px; background: #ffffff; color: #64748b; font-size: 13px; font-weight: 700; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center; }
      .fs-info-card-back:hover { background: #f1f5f9; color: #1e293b; }
      .fs-info-card-scroll { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
      .fs-info-card-body { padding: 12px 14px 14px 16px; font-size: 12px; line-height: 1.5; }
      .fs-info-card-formula { padding: 9px 11px; margin-bottom: 8px; border-radius: 10px; background: linear-gradient(135deg, #f8fafc, #f1f5f9); border: 1px solid rgba(15,23,42,0.08); overflow-x: auto; }
      .fs-info-card-formula .katex { font-size: 1.05em; }
      .fs-info-card-copy-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin: 6px 0 8px; }
      .fs-info-card-copy { padding: 4px 9px; border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; color: #334155; font: inherit; font-size: 11px; cursor: pointer; }
      .fs-info-card-copy:hover { background: #f1f5f9; }
      .fs-info-card-copy:focus-visible { outline: 2px solid #b45309; outline-offset: 2px; }
      .fs-info-card-copy-status { flex: 1; min-width: 0; font-size: 11px; color: #475569; overflow-wrap: anywhere; }
      .fs-info-card-copy-source { flex-basis: 100%; width: 100%; min-height: 64px; max-height: 140px; box-sizing: border-box; resize: vertical; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px; font: 11px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; color: #334155; background: #f8fafc; }
      .fs-info-card-raw { margin: 0; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; color: #475569; white-space: pre-wrap; word-break: break-word; }
      .fs-info-card-meta { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
      .fs-info-card-meta span { padding: 2px 8px; border-radius: 999px; background: #f1f5f9; color: #475569; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
      .fs-info-card-note { margin-top: 10px; padding-top: 8px; border-top: 1px dashed rgba(15,23,42,0.12); color: #94a3b8; font-size: 10.5px; font-style: italic; }
      .fs-chat-messages { display: none; flex-direction: column; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(15,23,42,0.08); }
      .fs-chat-msg { max-width: 85%; padding: 8px 12px; border-radius: 16px; font-size: 11.5px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; box-shadow: 0 1px 2px rgba(15,23,42,0.06); }
      .fs-chat-msg-user { align-self: flex-end; background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #ffffff; border-radius: 16px 16px 4px 16px; box-shadow: 0 3px 8px rgba(245,158,11,0.32); }
      .fs-chat-msg-text { white-space: pre-wrap; word-break: break-word; }
      .fs-chat-msg-copy { display: block; margin-top: 3px; border: none; background: transparent; padding: 0; color: inherit; opacity: 0.7; font-size: 11px; cursor: pointer; }
      .fs-chat-msg-copy:hover { opacity: 1; }
      .fs-chat-msg-time { font-size: 9.5px; color: rgba(255,255,255,0.75); margin-top: 3px; }
      .fs-chat-input-bar { flex: 0 0 auto; display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; background: linear-gradient(0deg, #fffaf0, #ffffff); border-top: 1px solid rgba(15,23,42,0.08); }
      .fs-chat-input-pill { flex: 1 1 auto; min-width: 0; display: flex; align-items: flex-end; background: #f8fafc; border: 1.5px solid rgba(15,23,42,0.10); border-radius: 20px; padding: 6px 6px 6px 14px; transition: border-color 120ms ease, box-shadow 120ms ease; }
      .fs-chat-input-pill:focus-within { border-color: rgba(245,158,11,0.55); box-shadow: 0 0 0 3px rgba(245,158,11,0.14); }
      .fs-chat-input { flex: 1 1 auto; min-width: 0; resize: none; border: none; background: transparent; font: inherit; font-size: 11.5px; line-height: 1.4; padding: 5px 4px; max-height: 72px; min-height: 18px; box-sizing: border-box; }
      .fs-chat-input:focus { outline: none; }
      .fs-chat-input::placeholder { color: #94a3b8; }
      .fs-chat-send { flex: 0 0 auto; width: 30px; height: 30px; border: none; border-radius: 999px; background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #ffffff; font-size: 13px; cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 3px 8px rgba(245,158,11,0.38); transition: transform 100ms ease, box-shadow 100ms ease, background 100ms ease; }
      .fs-chat-send:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 5px 12px rgba(245,158,11,0.48); }
      .fs-chat-send:disabled { background: #e2e8f0; color: #94a3b8; box-shadow: none; cursor: default; transform: none; }
      .fs-chat-clear { display: block; margin: 10px 0 0 auto; padding: 3px 8px; border: 1px solid rgba(15,23,42,0.12); border-radius: 999px; background: #ffffff; color: #94a3b8; font-size: 10px; cursor: pointer; }
    `;
  }

  // A dashed curved line from the pinned highlight to its info card, redrawn
  // whenever either one moves. Uses a fixed-position SVG overlay rather than
  // a CSS border trick so it can point in any direction as the card
  // repositions itself around scroll/zoom/viewport edges. A <path> (not a
  // straight <line>) lets the connector leave each rectangle perpendicular to
  // its edge and curve toward the other shape, like a diagram-tool connector,
  // instead of cutting a straight diagonal across the page at an arbitrary
  // angle.
  function ensureConnectorLine() {
    let svg = document.querySelector(`[${CONNECTOR_ATTR}]`);
    if (svg) return svg;
    svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute(CONNECTOR_ATTR, 'true');
    Object.assign(svg.style, {
      position: 'fixed', left: '0', top: '0', width: '100vw', height: '100vh',
      pointerEvents: 'none', zIndex: '2147483646',
    });
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', INFO_ACCENT);
    path.setAttribute('stroke-width', '1.5');
    path.setAttribute('stroke-dasharray', '4 4');
    path.setAttribute('stroke-linecap', 'round');
    svg.appendChild(path);
    for (const attr of ['data-fs-dot-a', 'data-fs-dot-b']) {
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute(attr, 'true');
      dot.setAttribute('r', '2.75');
      dot.setAttribute('fill', INFO_ACCENT);
      svg.appendChild(dot);
    }
    (document.body || document.documentElement).appendChild(svg);
    return svg;
  }

  // Picks the point where the connector should leave `rect` toward
  // `otherCenter`: the edge (left/right/top/bottom) facing the other shape,
  // at the coordinate along that edge closest to it -- not always the edge's
  // midpoint -- so the line meets the rectangle at a sensible corner/side
  // instead of always radiating from dead-center. `normal`/`axis` describe
  // the outward direction at that point, used to build the curve's control
  // point so it leaves perpendicular to the edge.
  function edgeAnchorPoint(rect, otherCenter) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = otherCenter.x - cx;
    const dy = otherCenter.y - cy;
    if (Math.abs(dx) * Math.max(1, rect.height) >= Math.abs(dy) * Math.max(1, rect.width)) {
      return {
        x: dx >= 0 ? rect.right : rect.left,
        y: Math.max(rect.top, Math.min(otherCenter.y, rect.bottom)),
        axis: 'x',
        normal: dx >= 0 ? 1 : -1,
      };
    }
    return {
      x: Math.max(rect.left, Math.min(otherCenter.x, rect.right)),
      y: dy >= 0 ? rect.bottom : rect.top,
      axis: 'y',
      normal: dy >= 0 ? 1 : -1,
    };
  }

  function connectorCurvePath(p1, p2) {
    const reach = Math.max(24, Math.hypot(p2.x - p1.x, p2.y - p1.y) * 0.4);
    const c1x = p1.axis === 'x' ? p1.x + p1.normal * reach : p1.x;
    const c1y = p1.axis === 'y' ? p1.y + p1.normal * reach : p1.y;
    const c2x = p2.axis === 'x' ? p2.x + p2.normal * reach : p2.x;
    const c2y = p2.axis === 'y' ? p2.y + p2.normal * reach : p2.y;
    return `M ${p1.x} ${p1.y} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2.x} ${p2.y}`;
  }

  function updateConnectorLine(highlightRect, cardEl) {
    if (!highlightRect || !cardEl || !cardEl.isConnected) return;
    const cardRect = cardEl.getBoundingClientRect();
    const hCenter = { x: highlightRect.left + highlightRect.width / 2, y: highlightRect.top + highlightRect.height / 2 };
    const cCenter = { x: cardRect.left + cardRect.width / 2, y: cardRect.top + cardRect.height / 2 };
    const p1 = edgeAnchorPoint(highlightRect, cCenter);
    const p2 = edgeAnchorPoint(cardRect, hCenter);
    const svg = ensureConnectorLine();
    svg.querySelector('path').setAttribute('d', connectorCurvePath(p1, p2));
    svg.querySelector('[data-fs-dot-a]').setAttribute('cx', String(p1.x));
    svg.querySelector('[data-fs-dot-a]').setAttribute('cy', String(p1.y));
    svg.querySelector('[data-fs-dot-b]').setAttribute('cx', String(p2.x));
    svg.querySelector('[data-fs-dot-b]').setAttribute('cy', String(p2.y));
  }

  function removeConnectorLine() {
    document.querySelectorAll(`[${CONNECTOR_ATTR}]`).forEach((node) => node.remove());
  }

  function loadKatexIntoPage(jsUrl, cssUrl) {
    if (globalThis.katex) return Promise.resolve(true);
    if (globalThis.__formulaSearchKatexLoading) return globalThis.__formulaSearchKatexLoading;
    if (!jsUrl) return Promise.resolve(false);
    globalThis.__formulaSearchKatexLoading = new Promise((resolve) => {
      try {
        if (cssUrl && !document.querySelector(`link[href="${cssUrl}"]`)) {
          const link = document.createElement('link');
          link.rel = 'stylesheet';
          link.href = cssUrl;
          (document.head || document.documentElement).appendChild(link);
        }
        const script = document.createElement('script');
        script.src = jsUrl;
        script.onload = () => resolve(!!globalThis.katex);
        script.onerror = () => resolve(false);
        (document.head || document.documentElement).appendChild(script);
      } catch {
        resolve(false);
      }
    });
    return globalThis.__formulaSearchKatexLoading;
  }

  function closeInfoPanel() {
    try {
      const controller = globalThis.__formulaSearchContentInfoPanelController;
      if (controller && typeof controller.destroy === 'function') controller.destroy();
      globalThis.__formulaSearchContentInfoPanelController = null;
      document.querySelectorAll(`[${INFO_PANEL_ATTR}]`).forEach((node) => node.remove());
      removeConnectorLine();
      // The highlight tied to this info panel was left un-faded (see
      // installDynamicOverlay's `pinned` branch) specifically so it would
      // stay lit exactly until the panel closes -- do that now.
      const highlightController = globalThis.__formulaSearchOverlayController;
      if (highlightController && highlightController.pinned) {
        highlightController.destroy();
        globalThis.__formulaSearchOverlayController = null;
      }
    } catch {
      // Cleanup only.
    }
  }

  function placeInfoCard(card, anchorRect) {
    const margin = 12;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    const cardW = card.offsetWidth || 360;
    const cardH = card.offsetHeight || 200;

    let left = anchorRect.right + margin;
    let top = anchorRect.top;
    if (left + cardW > vw - margin) {
      left = anchorRect.left - margin - cardW;
    }
    if (left < margin) {
      left = Math.min(Math.max(margin, anchorRect.left), Math.max(margin, vw - cardW - margin));
      top = anchorRect.bottom + margin;
    }
    if (top + cardH > vh - margin) top = Math.max(margin, vh - cardH - margin);
    if (top < margin) top = margin;

    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }

  // Used when there is no bbox to anchor next to (e.g. the backend could
  // only confirm a page, not an exact rendered location -- a real, fairly
  // common state for TeX-sourced matches). Without this the card stayed at
  // its off-screen creation position forever, so it looked like the info
  // button silently did nothing.
  function placeInfoCardFallback(card) {
    const margin = 12;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const cardW = card.offsetWidth || 360;
    card.style.left = `${Math.max(margin, vw - cardW - margin)}px`;
    card.style.top = `${margin}px`;
  }

  // === CHAT STORE (content.js) BEGIN ===
  const CHAT_STORAGE_KEY = 'fs_chat_history';
  const CHAT_COPY_GLYPH = '⧉';

  function loadChatHistory() { return FormulaStoreClient('notes.list'); }
  function appendChatMessage(text, context) {
    return FormulaStoreClient('notes.append', {text, context: context || null});
  }
  function clearChatHistory() { return FormulaStoreClient('notes.clear'); }

  const CHAT_DRAFT_KEY = 'fs_chat_draft';
  // In-memory shadow of the last known draft, alongside the storage-backed
  // copy -- saveDraft's write is fire-and-forget (matches every call site,
  // which saves on every keystroke and can't block on a round-trip), so a
  // write that rejects (e.g. extension context invalidated mid-send) would
  // otherwise leave a later loadDraft() reading stale/empty storage even
  // though the caller already re-typed the text into cachedDraft's source
  // of truth.
  //
  // This is the one of the three CHAT STORE copies where that protection
  // actually fires in production: LOAD_GUARD (top of this file) keeps one
  // script instance -- and this closure's cachedDraft -- resident across
  // every card opened in this tab, so a rejected write's cachedDraft update
  // from an earlier card survives to be read back by a later card's
  // loadDraft() call within the same tab. The popup-injected
  // (nativePdfInstantJump) and popup-outer copies of this same code carry
  // the identical logic for consistency and because it's cheap and
  // correct, but it's currently inert there in real usage -- see their own
  // comments for why.
  //
  // Because cachedDraft outlives any single card (LOAD_GUARD keeps this
  // whole closure resident for the tab's lifetime), the onChanged listener
  // that keeps it synced with writes from OTHER surfaces must be just as
  // long-lived -- registered once below, at this same module scope, rather
  // than per-card. A per-card listener would stop updating cachedDraft the
  // moment that card's controller.destroy() runs, leaving it silently stale
  // (and able to shadow a genuinely newer cross-surface draft) for every
  // later card opened in this tab.
  let cachedDraft = null;

  function loadDraft() {
    if (cachedDraft !== null) return Promise.resolve(cachedDraft);
    if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve('');
    return chrome.storage.local.get(CHAT_DRAFT_KEY).then((result) => result[CHAT_DRAFT_KEY] || '');
  }

  function saveDraft(text) {
    cachedDraft = text;
    if (typeof chrome === 'undefined' || !chrome.storage) return;
    chrome.storage.local.set({ [CHAT_DRAFT_KEY]: text }).catch(() => {});
  }

  function clearDraft() {
    cachedDraft = '';
    if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve();
    return chrome.storage.local.set({ [CHAT_DRAFT_KEY]: '' });
  }

  // Module-level (not per-card) sync of cachedDraft with writes from other
  // surfaces -- another open card in this tab, the popup's outer Chat view,
  // or a write that finally lands after an earlier one from here rejected.
  // Registered once, alongside cachedDraft itself, so it stays alive for as
  // long as cachedDraft does (the tab's whole lifetime, per LOAD_GUARD)
  // instead of being torn down with whichever card happened to create it.
  // CHAT_STORAGE_KEY (chat history) sync stays a separate, per-card listener
  // added/removed in showInfoPanel/controller.destroy(), since it drives
  // that specific card's own message list DOM.
  const onChatDraftStorageChanged = (changes, area) => {
    if (area !== 'local') return;
    if (changes[CHAT_DRAFT_KEY]) {
      cachedDraft = changes[CHAT_DRAFT_KEY].newValue || '';
    }
  };
  if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener(onChatDraftStorageChanged);
  }

  function renderChatMessages(messagesEl, clearBtnEl, messages) {
    messagesEl.innerHTML = '';
    for (const m of messages) {
      const bubble = document.createElement('div');
      bubble.className = `fs-chat-msg fs-chat-msg-${m.role}`;
      const text = document.createElement('div');
      text.className = 'fs-chat-msg-text';
      text.textContent = m.text;
      bubble.appendChild(text);
      const time = document.createElement('div');
      time.className = 'fs-chat-msg-time';
      time.textContent = formatRelativeTime(m.timestamp);
      bubble.appendChild(time);
      const copyBtn = document.createElement('button');
      copyBtn.type = 'button';
      copyBtn.className = 'fs-chat-msg-copy';
      copyBtn.setAttribute('aria-label', 'Copy message');
      copyBtn.textContent = CHAT_COPY_GLYPH;
      copyBtn.addEventListener('click', () => {
        if (typeof navigator === 'undefined' || !navigator.clipboard) return;
        // Cancel a pending revert from a rapid re-click, same pattern the
        // old whole-thread copyChatThread() used -- but per-button (kept
        // on the element itself via _copyRevertTimer) since there's one
        // button per message here, not a single shared control.
        if (copyBtn._copyRevertTimer) {
          clearTimeout(copyBtn._copyRevertTimer);
          copyBtn._copyRevertTimer = null;
        }
        navigator.clipboard.writeText(m.text).then(() => {
          copyBtn.textContent = '✓';
        }, () => {
          // writeText genuinely rejects under normal conditions (document
          // not focused, permissions policy) -- show that instead of
          // leaving the button silently unchanged with no sign the copy
          // failed.
          copyBtn.textContent = '✕';
        }).then(() => {
          copyBtn._copyRevertTimer = setTimeout(() => {
            copyBtn.textContent = CHAT_COPY_GLYPH;
            copyBtn._copyRevertTimer = null;
          }, 1200);
        });
      });
      bubble.appendChild(copyBtn);
      messagesEl.appendChild(bubble);
    }
    messagesEl.style.display = messages.length ? 'flex' : 'none';
    clearBtnEl.style.display = messages.length ? 'block' : 'none';
  }

  function formatRelativeTime(ts) {
    const diff = Date.now() - ts;
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  // showInfoPanel's per-card sendChatMessage just delegates to this (kept
  // here, parameterized, rather than inline in showInfoPanel) so it stays
  // reachable by tests_js/chat_store_content.js's block extraction, which
  // only pulls in this CHAT STORE region -- not the surrounding
  // card-building code that showInfoPanel's closure vars live in. Returns
  // its promise so callers/tests can await the write+render, or the
  // catch's restore, directly instead of racing an independent check.
  function sendChatMessageWithRestore(chatInput, chatContext, chatMessages, chatClearBtn, autoGrowChatInput, updateChatSendEnabled, scrollChatIntoView) {
    const text = chatInput.value.trim();
    if (!text) return Promise.resolve();
    chatInput.value = '';
    if (chatInput._saveErrorElement) chatInput._saveErrorElement.textContent = '';
    autoGrowChatInput();
    updateChatSendEnabled();
    const sent = appendChatMessage(text, chatContext).then((messages) => {
      if (chatInput._refreshNotes) chatInput._refreshNotes();
      else renderChatMessages(chatMessages, chatClearBtn, messages);
      window.requestAnimationFrame(scrollChatIntoView);
      if (!chatInput.value.trim()) clearDraft().catch(() => {});
    }).catch(error => {
      // A failed worker save must preserve both the attempted note and
      // anything typed while it was pending. Do not claim it was saved.
      chatInput.value = chatInput.value ? `${text}\n\n${chatInput.value}` : text;
      if (chatInput._saveErrorElement) chatInput._saveErrorElement.textContent = `Not saved: ${error.message}`;
      autoGrowChatInput();
      updateChatSendEnabled();
      saveDraft(chatInput.value);
    });
    chatInput.focus();
    return sent;
  }
  // === CHAT STORE (content.js) END ===

  // Base structure for the per-result "info panel" feature: anchors a
  // persistent styled card next to the highlighted formula in the actual
  // native PDF viewer (not a re-rendered page). The body currently shows the
  // formula's own LaTeX (KaTeX-rendered once the bundled renderer loads into
  // this page); a future iteration can swap in backend-provided explanations
  // without touching the anchoring/transport plumbing.
  function showInfoPanel(payload, viewport, viewer) {
    const info = payload.infoPanel;
    if (!info) return false;
    // Any previous info panel/pinned highlight was already retired by the
    // closeInfoPanel() call at the top of scrollToFormula, before the jump
    // attempts (and this jump's own new highlight) ran -- calling it again
    // here would race that highlight's creation, since
    // requestAnimationFrame's timing relative to this synchronous call is
    // not guaranteed.
    ensureInfoPanelStyles();

    const card = document.createElement('div');
    card.setAttribute(INFO_PANEL_ATTR, 'true');
    card.className = 'fs-info-card';
    card.style.left = '-9999px';
    card.style.top = '-9999px';

    const header = document.createElement('div');
    header.className = 'fs-info-card-header';
    const headerText = document.createElement('div');
    const eyebrow = document.createElement('p');
    eyebrow.className = 'fs-info-card-eyebrow';
    eyebrow.textContent = info.sourceType ? `Formula · ${info.sourceType}` : 'Formula';
    const title = document.createElement('p');
    title.className = 'fs-info-card-title';
    title.textContent = info.label || info.section || `Page ${Number(payload.page) + 1}`;
    headerText.appendChild(eyebrow);
    headerText.appendChild(title);
    const backBtn = document.createElement('button');
    backBtn.type = 'button';
    backBtn.className = 'fs-info-card-back';
    backBtn.textContent = '←';
    backBtn.setAttribute('aria-label', 'Back to results');
    backBtn.title = 'Close this card and try to reopen the extension popup';
    backBtn.addEventListener('click', () => {
      closeInfoPanel();
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        try { chrome.runtime.sendMessage({ type: 'openPopup' }, () => {}); } catch { /* best effort */ }
      }
    });
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'fs-info-card-close';
    closeBtn.textContent = '×';
    closeBtn.setAttribute('aria-label', 'Close formula info');
    closeBtn.addEventListener('click', closeInfoPanel);
    header.appendChild(backBtn);
    header.appendChild(headerText);
    header.appendChild(closeBtn);

    const body = document.createElement('div');
    body.className = 'fs-info-card-body';

    const formulaBox = document.createElement('div');
    formulaBox.className = 'fs-info-card-formula';
    const rawPre = document.createElement('div');
    rawPre.className = 'fs-info-card-raw';
    rawPre.textContent = info.rawLatex || '(no LaTeX source available)';
    formulaBox.appendChild(rawPre);

    const meta = document.createElement('div');
    meta.className = 'fs-info-card-meta';
    const pagePrefix = ['estimated', 'ambiguous'].includes(payload.location_status) ? 'near page' : 'page';
    [info.matchType, info.section, `${pagePrefix} ${Number(payload.page) + 1}`].filter(Boolean).forEach((text) => {
      const tag = document.createElement('span');
      tag.textContent = text;
      meta.appendChild(tag);
    });

    const hasBbox = !!bboxFromPayload(payload);
    // The "more context... future update" filler note is dropped once
    // there's a bbox: the chat input below is that future update now. The
    // no-bbox warning is still genuinely useful information, so it stays.
    let note = null;
    if (!hasBbox) {
      note = document.createElement('div');
      note.className = 'fs-info-card-note';
      note.textContent = payload.location_status === 'ambiguous'
        ? 'Several source occurrences share this estimated PDF location. No exact highlight is shown.'
        : payload.location_status === 'estimated'
          ? "This is the section's starting page. The formula's location has not been verified."
          : 'No exact highlight is available for this formula on this page.';
    }

    // Chat thread: appears directly below the formula info once there's at
    // least one message (display:none while empty -- see sendChatMessage),
    // so an unused card looks exactly like before. No backend to answer yet,
    // so sending just appends the user's own message; built as a real
    // message list with a modifier class per role (.fs-chat-msg-user) so a
    // future assistant reply is just another bubble, not a rework.
    const chatMessages = document.createElement('div');
    chatMessages.className = 'fs-chat-messages';

    const chatClearBtn = document.createElement('button');
    chatClearBtn.type = 'button';
    chatClearBtn.className = 'fs-chat-clear';
    chatClearBtn.textContent = 'Clear notes';
    chatClearBtn.style.display = 'none';

    const scroll = document.createElement('div');
    scroll.className = 'fs-info-card-scroll';
    scroll.appendChild(body);
    body.appendChild(formulaBox);
    appendLatexCopyControl(body, info.rawLatex);
    body.appendChild(meta);
    if (note) body.appendChild(note);
    body.appendChild(chatClearBtn);
    body.appendChild(chatMessages);

    // The input bar is a flex sibling of the scrollable region (like the
    // header), not part of it, so it stays put -- an always-visible bottom
    // bar, not something you have to scroll down to reach, matching the
    // "already have the message row" ask.
    const chatInputBar = document.createElement('div');
    chatInputBar.className = 'fs-chat-input-bar';
    const chatInputPill = document.createElement('div');
    chatInputPill.className = 'fs-chat-input-pill';
    const chatInput = document.createElement('textarea');
    chatInput.className = 'fs-chat-input';
    chatInput.rows = 1;
    chatInput.placeholder = 'Write a note about this formula…';
    chatInput.setAttribute('aria-label', 'Write a note about this formula');
    chatInputPill.appendChild(chatInput);
    const chatSend = document.createElement('button');
    chatSend.type = 'button';
    chatSend.className = 'fs-chat-send';
    chatSend.setAttribute('aria-label', 'Save note');
    chatSend.textContent = '➤';
    chatSend.disabled = true;
    chatInputBar.appendChild(chatInputPill);
    chatInputBar.appendChild(chatSend);

    const noteSaveError = document.createElement('div');
    noteSaveError.className = 'fs-note-save-error';
    noteSaveError.setAttribute('role', 'alert');
    noteSaveError.style.cssText = 'color:#b91c1c;font-size:12px;padding:0 12px;white-space:normal';
    chatInput._saveErrorElement = noteSaveError;

    const CHAT_INPUT_MAX_HEIGHT = 72;
    const autoGrowChatInput = () => {
      chatInput.style.height = 'auto';
      const next = Math.min(CHAT_INPUT_MAX_HEIGHT, Math.max(chatInput.scrollHeight || 0, 18));
      chatInput.style.height = `${next}px`;
    };
    const updateChatSendEnabled = () => {
      chatSend.disabled = !chatInput.value.trim();
    };
    const scrollChatIntoView = () => {
      // Scrolls the shared scroll region (formula info + chat thread share
      // it) down to the newest message, not the whole card -- the input bar
      // lives outside this region and needs no scrolling to stay visible.
      scroll.scrollTop = scroll.scrollHeight;
    };
    const chatContext = {
      latex: info.rawLatex || '', section: info.section || '', page: Number(payload.page),
      bbox: Array.isArray(payload.bbox) ? payload.bbox : null,
      page_w: Number(payload.page_w || 0), page_h: Number(payload.page_h || 0),
      source: payload.source || '', source_url: payload.source_url || '', match_type: payload.match_type || '', bbox_source: payload.bbox_source || '',
      location_status: payload.location_status || '',
    };
    const sendChatMessage = () => sendChatMessageWithRestore(
      chatInput, chatContext, chatMessages, chatClearBtn,
      autoGrowChatInput, updateChatSendEnabled, scrollChatIntoView
    );
    let draftSaveTimer = null;
    const onChatInput = () => {
      autoGrowChatInput();
      updateChatSendEnabled();
      if (draftSaveTimer) clearTimeout(draftSaveTimer);
      draftSaveTimer = setTimeout(() => { saveDraft(chatInput.value); }, 300);
    };
    const onChatKeydown = (event) => {
      // isComposing guards IME text composition (e.g. typing accented
      // characters or CJK input): the Enter that commits a composed
      // character must not also send the message.
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendChatMessage();
      }
    };
    chatInput.addEventListener('input', onChatInput);
    chatInput.addEventListener('keydown', onChatKeydown);
    chatSend.addEventListener('click', sendChatMessage);

    const onChatClearClick = () => {
      clearChatHistory().then(() => {
        renderChatMessages(chatMessages, chatClearBtn, []);
        noteSaveError.textContent = '';
      }).catch(error => { noteSaveError.textContent = `Could not clear notes: ${error.message}`; });
    };
    chatClearBtn.addEventListener('click', onChatClearClick);

    // Every surface (this card, any other open card, the popup's own Chat
    // view) reads/writes the same chrome.storage.local key -- onChanged is
    // how they all stay in sync without polling. Per-card (added here,
    // removed in controller.destroy()) since it drives this card's own
    // message list DOM. CHAT_DRAFT_KEY sync lives separately, at module
    // scope alongside cachedDraft itself -- see that listener's comment.
    let noteViewRevision = 0;
    const publishNotes = messages => {
      noteViewRevision++;
      renderChatMessages(chatMessages, chatClearBtn, messages);
      window.requestAnimationFrame(scrollChatIntoView);
    };
    chatInput._refreshNotes = () => {
      const revision = noteViewRevision;
      return loadChatHistory().then(messages => {
        if (revision === noteViewRevision) publishNotes(messages);
      }).catch(error => { noteSaveError.textContent = `Could not load notes: ${error.message}`; });
    };
    const onChatStorageChanged = (changes, area) => {
      if (area !== 'local') return;
      if (changes[CHAT_STORAGE_KEY]) {
        publishNotes(changes[CHAT_STORAGE_KEY].newValue || []);
      }
    };
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener(onChatStorageChanged);
    }
    const stopNoteSubscription = (typeof chrome === 'undefined' || !chrome.storage)
      ? FormulaStoreSubscribe(publishNotes, '') : () => {};
    chatInput._refreshNotes();
    loadDraft().then((text) => {
      if (!text) return;
      chatInput.value = text;
      autoGrowChatInput();
      updateChatSendEnabled();
    }).catch(() => {});

    card.appendChild(header);
    card.appendChild(scroll);
    card.appendChild(noteSaveError);
    card.appendChild(chatInputBar);
    (document.body || document.documentElement).appendChild(card);

    let lastRect = null;

    // Tells the highlight overlay (if any) whether the card currently sits
    // on top of it, so update() there can hide it -- see coveredByCard's
    // doc comment. Reads the controller live each call rather than caching
    // it once, since it's the same global the highlight itself keeps up to
    // date.
    const syncHighlightCoverage = (covered) => {
      const hc = globalThis.__formulaSearchOverlayController;
      if (!hc || hc.dead) return;
      if (hc.coveredByCard === covered) return;
      hc.coveredByCard = covered;
      hc.update();
    };

    // Single place that decides the connector's fate whenever the card or
    // highlight might have moved: draws it to `rect` normally, but tears it
    // down (instead of drawing a dangling line to a highlight that's hidden
    // underneath the card) once the card covers the highlight -- covering
    // and connecting are mutually exclusive, so both are decided from the
    // same overlap test in one place rather than two call sites drifting out
    // of sync.
    const syncConnectorAndCoverage = (rect) => {
      const covered = !!rect && !!intersectRects(rect, card.getBoundingClientRect());
      syncHighlightCoverage(covered);
      if (covered) {
        removeConnectorLine();
      } else {
        updateConnectorLine(rect, card);
      }
    };

    // Mirrors installDynamicOverlay's own visibility check so the card and
    // its connector hide together with the highlight when the formula
    // scrolls/zooms out of view, and reappear together once it's back --
    // rather than staying clamped at whatever screen edge computeOverlayRect
    // used to (incorrectly) report as "visible".
    const reposition = () => {
      if (!card.isConnected) return;
      if (!hasBbox) {
        // No bbox to anchor to at all (page-only match) -- this card was
        // never connected to a highlight, so it just stays put in its
        // fallback corner regardless of scroll/zoom.
        card.style.display = '';
        if (!card.dataset.userPositioned) placeInfoCardFallback(card);
        removeConnectorLine();
        syncHighlightCoverage(false);
        return;
      }
      const rect = computeOverlayRect(payload, viewport, viewer);
      const visible = !!rect && !(rect.visibleFraction < VISIBILITY_THRESHOLD);
      if (!visible) {
        lastRect = null;
        card.style.display = 'none';
        removeConnectorLine();
        syncHighlightCoverage(false);
        return;
      }
      lastRect = rect;
      card.style.display = '';
      // Once the user has dragged the card themselves, respect that
      // placement instead of snapping it back next to the highlight --
      // the connector line still keeps it visually tethered.
      if (!card.dataset.userPositioned) placeInfoCard(card, rect);
      syncConnectorAndCoverage(rect);
    };
    reposition();

    const reflow = attachReflowListeners(viewer, reposition);

    // Dragging: pointerdown on the header (but not the close button) picks
    // the card up; pointermove repositions it; the userPositioned flag set
    // here tells reposition() above to stop auto-placing the card so the
    // drag sticks.
    let dragState = null;
    const onHeaderPointerDown = (event) => {
      if (event.target === closeBtn || event.target === backBtn || event.button !== 0) return;
      const rect = card.getBoundingClientRect();
      dragState = { startX: event.clientX, startY: event.clientY, startLeft: rect.left, startTop: rect.top };
      card.dataset.userPositioned = 'true';
      header.setPointerCapture(event.pointerId);
      event.preventDefault();
    };
    const onHeaderPointerMove = (event) => {
      if (!dragState) return;
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      const cardW = card.offsetWidth;
      const cardH = card.offsetHeight;
      let left = dragState.startLeft + (event.clientX - dragState.startX);
      let top = dragState.startTop + (event.clientY - dragState.startY);
      left = Math.min(Math.max(0, left), Math.max(0, vw - cardW));
      top = Math.min(Math.max(0, top), Math.max(0, vh - cardH));
      card.style.left = `${Math.round(left)}px`;
      card.style.top = `${Math.round(top)}px`;
      syncConnectorAndCoverage(lastRect);
    };
    const onHeaderPointerUp = (event) => {
      dragState = null;
      try { header.releasePointerCapture(event.pointerId); } catch { /* noop */ }
    };
    header.addEventListener('pointerdown', onHeaderPointerDown);
    header.addEventListener('pointermove', onHeaderPointerMove);
    header.addEventListener('pointerup', onHeaderPointerUp);
    header.addEventListener('pointercancel', onHeaderPointerUp);

    // Resizing: 8 thin invisible strips/corners layered on top of the card's
    // edges (inset, not protruding past them, so the card's own
    // `overflow: auto` doesn't clip them) let the user resize from any side
    // or corner, like a normal OS window, rather than only the single
    // bottom-right grip a plain CSS `resize: both` would give -- see
    // ensureInfoPanelStyles, which sets `resize: none` now that this
    // replaces it.
    const RESIZE_HANDLES = [
      { dir: 'n', cursor: 'ns-resize', style: { top: '0', left: '10px', right: '10px', height: '7px' } },
      { dir: 's', cursor: 'ns-resize', style: { bottom: '0', left: '10px', right: '10px', height: '7px' } },
      { dir: 'w', cursor: 'ew-resize', style: { left: '0', top: '10px', bottom: '10px', width: '7px' } },
      { dir: 'e', cursor: 'ew-resize', style: { right: '0', top: '10px', bottom: '10px', width: '7px' } },
      { dir: 'nw', cursor: 'nwse-resize', style: { top: '0', left: '0', width: '12px', height: '12px' } },
      { dir: 'se', cursor: 'nwse-resize', style: { bottom: '0', right: '0', width: '12px', height: '12px' } },
      { dir: 'ne', cursor: 'nesw-resize', style: { top: '0', right: '0', width: '12px', height: '12px' } },
      { dir: 'sw', cursor: 'nesw-resize', style: { bottom: '0', left: '0', width: '12px', height: '12px' } },
    ];
    const MIN_CARD_W = 220;
    const MIN_CARD_H = 140;
    let resizeState = null;
    const resizeHandleEls = [];
    const onResizeHandlePointerMove = (event) => {
      if (!resizeState) return;
      const { dir } = resizeState;
      const dx = event.clientX - resizeState.startX;
      const dy = event.clientY - resizeState.startY;
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;

      let width = resizeState.startW;
      let height = resizeState.startH;
      if (dir.includes('e')) width = resizeState.startW + dx;
      if (dir.includes('w')) width = resizeState.startW - dx;
      if (dir.includes('s')) height = resizeState.startH + dy;
      if (dir.includes('n')) height = resizeState.startH - dy;
      width = Math.min(Math.max(MIN_CARD_W, width), Math.max(MIN_CARD_W, vw - 16));
      height = Math.min(Math.max(MIN_CARD_H, height), Math.max(MIN_CARD_H, vh - 16));

      // The edge opposite the one being dragged stays put: for the west/north
      // handles that means re-deriving left/top from the fixed right/bottom
      // edge and the (possibly clamped) new width/height, instead of the raw
      // pointer delta, so hitting the min/max size doesn't yank the card.
      let left = resizeState.startLeft;
      let top = resizeState.startTop;
      if (dir.includes('w')) left = resizeState.startLeft + resizeState.startW - width;
      if (dir.includes('n')) top = resizeState.startTop + resizeState.startH - height;
      left = Math.min(Math.max(0, left), Math.max(0, vw - width));
      top = Math.min(Math.max(0, top), Math.max(0, vh - height));

      card.style.width = `${Math.round(width)}px`;
      card.style.height = `${Math.round(height)}px`;
      card.style.left = `${Math.round(left)}px`;
      card.style.top = `${Math.round(top)}px`;
      syncConnectorAndCoverage(lastRect);
    };
    const onResizeHandlePointerUp = (event, handleEl) => {
      resizeState = null;
      try { handleEl.releasePointerCapture(event.pointerId); } catch { /* noop */ }
    };
    for (const { dir, cursor, style } of RESIZE_HANDLES) {
      const handle = document.createElement('div');
      handle.setAttribute(`data-fs-resize-${dir}`, 'true');
      Object.assign(handle.style, { position: 'absolute', cursor, zIndex: '2', ...style });
      const onDown = (event) => {
        if (event.button !== 0) return;
        const rect = card.getBoundingClientRect();
        resizeState = { dir, startX: event.clientX, startY: event.clientY, startLeft: rect.left, startTop: rect.top, startW: rect.width, startH: rect.height };
        card.dataset.userPositioned = 'true';
        handle.setPointerCapture(event.pointerId);
        event.preventDefault();
        event.stopPropagation();
      };
      const onUp = (event) => onResizeHandlePointerUp(event, handle);
      handle.addEventListener('pointerdown', onDown);
      handle.addEventListener('pointermove', onResizeHandlePointerMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
      card.appendChild(handle);
      resizeHandleEls.push({ handle, onDown, onUp });
    }

    let cardResizeObserver = null;
    try {
      cardResizeObserver = new ResizeObserver(() => syncConnectorAndCoverage(lastRect));
      cardResizeObserver.observe(card);
    } catch {
      // ResizeObserver may not be available in unusual frames; dragging/
      // resizing still works, the connector just won't track it live.
    }

    const controller = {
      destroy() {
        reflow.destroy();
        header.removeEventListener('pointerdown', onHeaderPointerDown);
        header.removeEventListener('pointermove', onHeaderPointerMove);
        header.removeEventListener('pointerup', onHeaderPointerUp);
        header.removeEventListener('pointercancel', onHeaderPointerUp);
        for (const { handle, onDown, onUp } of resizeHandleEls) {
          handle.removeEventListener('pointerdown', onDown);
          handle.removeEventListener('pointermove', onResizeHandlePointerMove);
          handle.removeEventListener('pointerup', onUp);
          handle.removeEventListener('pointercancel', onUp);
        }
        if (cardResizeObserver) cardResizeObserver.disconnect();
        stopNoteSubscription();
        noteViewRevision++;
        chatInput._refreshNotes = null;
        chatInput.removeEventListener('input', onChatInput);
        chatInput.removeEventListener('keydown', onChatKeydown);
        chatSend.removeEventListener('click', sendChatMessage);
        chatClearBtn.removeEventListener('click', onChatClearClick);
        if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
          chrome.storage.onChanged.removeListener(onChatStorageChanged);
        }
        syncHighlightCoverage(false);
        removeConnectorLine();
        if (card.isConnected) card.remove();
      },
    };
    globalThis.__formulaSearchContentInfoPanelController = controller;

    if (info.previewLatex && info.katexJsUrl) {
      loadKatexIntoPage(info.katexJsUrl, info.katexCssUrl).then((loaded) => {
        if (!loaded || !card.isConnected || !globalThis.katex) return;
        try {
          const katexTarget = document.createElement('div');
          globalThis.katex.render(info.previewLatex, katexTarget, {
            throwOnError: false,
            displayMode: true,
            trust: false,
            strict: false,
            macros: {...(info.katexMacros || {})},
          });
          formulaBox.insertBefore(katexTarget, rawPre);
          rawPre.style.display = 'none';
          window.requestAnimationFrame(reposition);
        } catch {
          // Keep the raw-text fallback that is already visible.
        }
      });
    }

    return true;
  }

  async function scrollToFormula(payload) {
    if (payload.extensionId) relayExtensionId = payload.extensionId;
    const page = pageIndex(payload);
    if (page < 0) return { ok: false, error: 'Match has no page mapping.' };

    // Jumping to any formula (plain click or the info arrow) retires
    // whatever was previously pinned open -- see the matching comment in
    // popup.js's nativePdfInstantJump for why this runs synchronously here
    // rather than from inside installDynamicOverlay's clearOverlay().
    const viewer = getPdfViewerElement();
    if (!viewer) {
      return { ok: false, error: 'Chrome PDF viewer element was not found in this frame.', hrefUnchanged: true };
    }

    const readiness = globalThis.FormulaNativeViewerState(page, viewer, payload.navigationUrl);
    if (!readiness.ok) return readiness;
    closeInfoPanel();

    const attempts = [];
    for (const attempt of [
      () => tryViewportNavigation(payload, viewer),
      () => tryChangePageEvent(payload, viewer),
      () => approximateScroll(payload, viewer),
    ]) {
      const result = attempt();
      attempts.push(result);
      if (result && result.ok) {
        if (payload.showInfoPanel) {
          try { showInfoPanel(payload, getNativeViewport(viewer), viewer); } catch { /* info panel is best-effort */ }
        }
        return {
          ok: true,
          page,
          mode: result.mode,
          highlighted: !!result.highlighted,
          hrefUnchanged: true,
          viewerFound: true,
          attempts,
        };
      }
    }

    return {
      ok: false,
      error: 'Could not drive Chrome native PDF viewer from content bridge.',
      hrefUnchanged: true,
      viewerFound: true,
      attempts,
    };
  }

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (message && message.type === 'formulaSearchPing') {
        const viewer = getPdfViewerElement();
        sendResponse({
          ok: true,
          href: window.location.href,
          hasPdfViewer: !!viewer,
          hasShadowRoot: !!getViewerRoot(viewer),
          nativeReadinessVersion: 1,
          noUrlFallback: true,
        });
        return false;
      }
      if (!message || message.type !== 'scrollToFormula') return false;

      Promise.resolve(scrollToFormula(message))
        .then((response) => sendResponse(response))
        .catch((err) => sendResponse({ ok: false, error: err && err.message ? err.message : String(err), hrefUnchanged: true }));
      return true;
    });
  }
})();
