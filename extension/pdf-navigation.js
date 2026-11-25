/* Supported page navigation and PDF excerpts when internal viewer access is unavailable. */
(function (root) {
  'use strict';
  function pageUrl(url, page) {
    if (!Number.isInteger(page) || page < 0) throw new Error('This formula has no known page.');
    const target = new URL(url);
    if (!['https:','http:','file:'].includes(target.protocol)) throw new Error('This paper URL cannot be opened.');
    const hash = new URLSearchParams(target.hash.slice(1));
    hash.delete('nameddest'); hash.set('page',String(page+1)); target.hash = hash.toString();
    return target.href;
  }
  function restrictedViewer(userAgent = navigator.userAgent) {
    // Chrome 158 is verified to block extension access to internal PDF frames.
    // Older/unknown engines still use capability probes and the same fallback.
    return Number(/(?:Chrome|Chromium)\/(\d+)/.exec(userAgent)?.[1]) >= 158;
  }
  async function waitForCommittedUrl(tabId, url, {signal, isCurrent = () => true, timeoutMs = 10000, tabs = root.chrome.tabs} = {}) {
    // tabs.update can resolve while pendingUrl still names the new navigation.
    // Reloading then can reload the previous page parameters instead.
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      signal?.throwIfAborted();
      if (!isCurrent()) return false;
      const tab = await tabs.get(tabId);
      signal?.throwIfAborted();
      if (!isCurrent()) return false;
      if (tab.url === url && !tab.pendingUrl && tab.status === 'complete') return true;
      await new Promise(resolve => setTimeout(resolve, 40));
    }
    throw new Error('The PDF page did not finish opening. Try again.');
  }
  async function excerpt({backend,url,match,signal}) {
    const response = await FormulaRequests.request(`${backend}/formula-preview`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url,page:match.page,region:match.bbox || null}),signal,
    },30000);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not show the PDF excerpt.');
    signal.throwIfAborted();
    const dialog = document.createElement('dialog'); dialog.className = 'ocr-selection'; dialog.dataset.formulaExcerpt = '';
    const title = document.createElement('h3'); title.textContent = match.bbox ? `Formula on page ${match.page+1}` : `Page ${match.page+1} — exact location unavailable`;
    const image = document.createElement('img'); image.src = data.image; image.alt = 'Original PDF excerpt'; image.style.cssText='max-width:100%;display:block;background:white';
    const latex = document.createElement('div'); latex.textContent = match.latex; latex.style.cssText='overflow:auto;max-height:120px;padding:8px 0;font-family:monospace;font-size:12px';
    const close = document.createElement('button'); close.type='button'; close.textContent='Close';
    const remove = () => {signal.removeEventListener('abort',remove);dialog.remove();};
    close.onclick=remove;dialog.oncancel=event=>{event.preventDefault();remove();};
    signal.addEventListener('abort',remove,{once:true});dialog.append(title,image,latex,close);document.body.append(dialog);dialog.showModal();close.focus();
  }
  root.FormulaPdfNavigation = {pageUrl,restrictedViewer,waitForCommittedUrl,excerpt};
  if (typeof module !== 'undefined') module.exports = root.FormulaPdfNavigation;
})(globalThis);
