'use strict';

let BACKEND = 'http://localhost:7878';
const IS_SIDE_PANEL = new URLSearchParams(location.search).get('surface') === 'sidepanel';
document.body.dataset.surface = IS_SIDE_PANEL ? 'panel' : 'popup';
const documentSession = new FormulaDocumentSession();
documentSession.begin();
let _panelWindowId = null;
let _initGeneration = 0;
let _handoffAppliedTo = null;
let _lastHandoffId = '';
let _pendingSavedNavigation = null;

// === SETTINGS STORE (popup outer) BEGIN ===
const SETTINGS_STORAGE_KEY = 'fs_settings';
const DEFAULT_SETTINGS = {
  backendUrl: 'http://localhost:7878',
  maxChatHistory: 500,
  defaultView: 'list',
  theme: 'auto',
  badgeEnabled: true,
};

function loadSettings() {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve(Object.assign({}, DEFAULT_SETTINGS));
  return chrome.storage.local.get(SETTINGS_STORAGE_KEY).then((result) => {
    return Object.assign({}, DEFAULT_SETTINGS, result[SETTINGS_STORAGE_KEY] || {});
  });
}

function saveSettings(settings) {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve();
  return chrome.storage.local.set({ [SETTINGS_STORAGE_KEY]: settings });
}

function applyTheme(theme) {
  if (theme === 'dark' || theme === 'light') {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
}
// === SETTINGS STORE (popup outer) END ===

// === SEARCH HISTORY (popup outer) BEGIN ===
const SEARCH_HISTORY_KEY = 'fs_search_history';
const SEARCH_HISTORY_MAX = 20;

function loadSearchHistory() {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve([]);
  return chrome.storage.local.get(SEARCH_HISTORY_KEY).then((result) => result[SEARCH_HISTORY_KEY] || []);
}

function saveSearchHistoryEntry(query) {
  if (!query) return Promise.resolve();
  return FormulaStoreClient('history.record', {query});
}
// === SEARCH HISTORY (popup outer) END ===

// === BOOKMARKS (popup outer) BEGIN ===
const BOOKMARKS_STORAGE_KEY = 'fs_bookmarks';
const BOOKMARKS_MAX = 200;

function bookmarkKey(match) {
  return FormulaBookmarkIdentity.key(match, _sourceUrl);
}

function loadBookmarks() {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve([]);
  return chrome.storage.local.get(BOOKMARKS_STORAGE_KEY).then((result) => {
    const items = result[BOOKMARKS_STORAGE_KEY];
    if (items !== undefined && !Array.isArray(items)) throw new Error('Saved bookmarks have an unexpected format.');
    return items || [];
  });
}

function bookmarkEntry(match) {
  return {
    id: bookmarkKey(match), latex: match.latex || '', label: match.label || '',
    section: match.section || '', page: match.page, bbox: match.bbox || null,
    page_w: match.page_w || 0, page_h: match.page_h || 0,
    source_url: match.resolved_source_url || match.source_url || _sourceUrl,
    location_status: match.location_status || '', bbox_source: match.bbox_source || '',
    source_file: match.source_file || '', source_line: match.source_line || 0,
    source_location_kind: match.source_location_kind || '',
    occurrence_id: match.occurrence_id || '',
    occurrence_anchor: match.occurrence_anchor || '',
    mapping_revision: match.mapping_revision || '',
  };
}

function toggleBookmark(match) {
  return FormulaStoreClient('bookmarks.toggle', {entry: bookmarkEntry(match)});
}
// === BOOKMARKS (popup outer) END ===

let bookmarkRefreshRevision = 0;
let bookmarkListRevision = 0;
async function refreshBookmarkStars(items) {
  const revision = ++bookmarkRefreshRevision;
  try {
    const bookmarks = items === undefined ? await loadBookmarks() : items;
    if (revision !== bookmarkRefreshRevision) return;
    if (!Array.isArray(bookmarks)) throw new Error('Saved bookmarks have an unexpected format.');
    for (const button of resultsList.querySelectorAll('.bookmark-star-btn')) {
      if (!button._bookmarkEntry) continue;
      const saved = bookmarks.some(item => FormulaBookmarkIdentity.same(item, button._bookmarkEntry));
      button.textContent = saved ? '★' : '☆';
      button.title = saved ? 'Remove bookmark' : 'Bookmark this formula';
      button.setAttribute('aria-pressed', String(saved));
    }
  } catch (error) {
    if (revision === bookmarkRefreshRevision) statusEl.textContent = `Could not load bookmarks: ${error.message}`;
  }
}

// === PENDING QUERY (popup outer) BEGIN ===
const PENDING_QUERY_KEY = 'fs_pending_query';
const PENDING_TOGGLE_CHAT_KEY = 'fs_pending_toggle_chat';
// A real user gesture (keyboard shortcut) that leads here opens the popup
// within a second or two. Anything older than this was never consumed --
// openPopup() rejected, or the popup closed before init reached
// consumePendingChatToggle() below -- and must not be allowed to force chat
// view open on a LATER, unrelated popup open (toolbar click, a different
// shortcut, a context-menu search). See Finding I3, final whole-branch review.
const PENDING_TOGGLE_CHAT_MAX_AGE_MS = 60000;

function consumePendingQuery({startup = false} = {}) {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve(false);
  // Startup restoration is older than any interaction already made here.
  // A later explicit context-menu action may replace the current query.
  const startupSuperseded = startup && _queryRevision !== 0;
  const queryState = captureQueryState();
  return chrome.storage.local.get(PENDING_QUERY_KEY).then((result) => {
    const text = result[PENDING_QUERY_KEY];
    if (typeof text !== 'string' || !text) return false;
    return chrome.storage.local.set({ [PENDING_QUERY_KEY]: '' }).then(() => {
      if (startupSuperseded || !isQueryStateCurrent(queryState)) return true;
      setQueryValue(text);
      resultFilter.restorePage('all');
      resultFilter.restoreSection('all');
      // search never became available this load (e.g. a context-menu search
      // fired on a non-PDF page, so init() disabled #query/#search-btn) --
      // same guard restoreLastQuery() uses. Still return true so the
      // caller's fallback-to-restoreLastQuery() doesn't also run and
      // clobber the input we just (harmlessly) filled.
      if (queryInput.disabled) return true;
      // The init chain applies the configured default view (possibly
      // 'chat' or 'bookmarks', both of which hide #results) before this
      // runs -- switch back to the list view so a genuinely pending query's
      // results are actually visible instead of silently rendering behind
      // a hidden #results.
      showView(_viewMode);
      runSearch();
      return true;
    });
  });
}

// Durable fallback for the toggle-chat command's best-effort live relay
// (service-worker.js) -- when the popup wasn't already open, opening it and
// sending toggleChatCommand races the popup's own script load/init, so the
// service worker also writes this flag before that race, and this consumes
// it on every popup open regardless of whether the live message won or lost
// that race. Landing on chat is unambiguous here (unlike the already-open
// case in handleIncomingMessage, which toggles), since a freshly opened
// popup has no "current" chat state to toggle away from.
function consumePendingChatToggle() {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve(false);
  return chrome.storage.local.get(PENDING_TOGGLE_CHAT_KEY).then((result) => {
    const stored = result[PENDING_TOGGLE_CHAT_KEY];
    if (!stored) return false;
    // Carries a timestamp (written by service-worker.js's commands
    // listener) rather than a bare boolean so a flag that never got
    // consumed -- openPopup() rejected, or this popup closed before init
    // reached this point -- can be told apart from a genuinely fresh
    // request instead of surviving indefinitely into some later, unrelated
    // popup open.
    const isFresh = typeof stored === 'object' && typeof stored.ts === 'number' &&
      (Date.now() - stored.ts) < PENDING_TOGGLE_CHAT_MAX_AGE_MS;
    return chrome.storage.local.set({ [PENDING_TOGGLE_CHAT_KEY]: false }).then(() => {
      if (!isFresh) return false;
      showView('chat');
      return true;
    });
  });
}
// === PENDING QUERY (popup outer) END ===

// === RECENT PAPERS (popup outer) BEGIN ===
const RECENT_PAPERS_KEY = 'fs_recent_papers';
const RECENT_PAPERS_MAX = 15;

function loadRecentPapers() {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve([]);
  return chrome.storage.local.get(RECENT_PAPERS_KEY).then((result) => result[RECENT_PAPERS_KEY] || []);
}

function recordRecentPaper(url, formulaCount) {
  if (!url) return Promise.resolve();
  return FormulaStoreClient('recent.record', {entry: {
    url, title: (typeof _activeTab !== 'undefined' && _activeTab?.title) || url, formulaCount,
  }});
}
// === RECENT PAPERS (popup outer) END ===

const queryInput = document.getElementById('query');

// === QUERY INTENT (popup outer) BEGIN ===
// Revisions also detect type-then-clear: value equality alone loses that intent.
let _queryRevision = 0;
function setQueryValue(value) {
  _queryRevision += 1;
  queryInput.value = value;
}
function captureQueryState() {
  return {context: documentSession.current, revision: _queryRevision,
    value: queryInput.value, mode: document.getElementById('search-mode')?.value};
}
function isQueryStateCurrent(state) {
  return documentSession.isCurrent(state.context) && state.revision === _queryRevision &&
    state.value === queryInput.value && state.mode === document.getElementById('search-mode')?.value;
}
// === QUERY INTENT (popup outer) END ===
const searchBtn = document.getElementById('search-btn');
const queryHistoryDropdown = document.getElementById('query-history-dropdown');
const statusEl = document.getElementById('status');
const recentPapersEl = document.getElementById('recent-papers');
const resultsList = document.getElementById('results');
const resultPreviewQueue = new FormulaPreviewQueue({container: resultsList, render: renderLatexPreview});
const viewToggleBtn = document.getElementById('view-toggle-btn');
const stepNav = document.getElementById('step-nav');
const stepUpBtn = document.getElementById('step-up-btn');
const stepDownBtn = document.getElementById('step-down-btn');
const stepIndexLabel = document.getElementById('step-index-label');
const stepOcrBtn = document.getElementById('step-ocr-btn');
const ocrPageInput = document.getElementById('ocr-page-input');
const ocrRunBtn = document.getElementById('ocr-run-btn');

let _activeTab = null;
let _sourceUrl = '';
// Set only on a *successful* /process response (not merely attempted --
// _sourceUrl above tracks the active tab's URL regardless of whether
// processing it ever worked). runSearch() and the OCR handler both send
// this back to the backend so /search and /ocr-page hit the right
// per-document session; if /process never succeeded this stays '', which
// correctly looks like "no session" to the backend (empty matches / 400)
// rather than silently querying a document that was never actually indexed.
let currentSessionUrl = '';
let _matches = [];
const resultFilter = new FormulaResultFilter();
const resultFilterEl = document.getElementById('result-filter');
const resultPageSelect = document.getElementById('result-page');
const resultSectionSelect = document.getElementById('result-section');
const resultPageRow = document.getElementById('result-page-row');
const resultSectionRow = document.getElementById('result-section-row');
const resultFilterSummary = document.getElementById('result-filter-summary');
const exportFilteredBtn = document.getElementById('export-filtered-btn');
const exportStatus = document.getElementById('export-status');

function updateResultFilterControls() {
  exportStatus.textContent = '';
  resultPageSelect.replaceChildren();
  resultSectionSelect.replaceChildren();
  const option = (select, value, label) => {
    const item = document.createElement('option');
    item.value = value; item.textContent = label; item.title = label;
    select.appendChild(item);
  };
  option(resultSectionSelect, 'all', `All sections (${resultFilter.all.length})`);
  for (const group of resultFilter.sectionGroups) {
    option(resultSectionSelect, group.key, `${group.label} (${group.count})`);
  }
  resultSectionSelect.value = resultFilter.section;
  option(resultPageSelect, 'all', `All pages (${resultFilter.sectionMatches.length})`);
  for (const group of resultFilter.groups) {
    const label = group.key === 'unknown' ? 'No page location' : `Page ${Number(group.key.slice(5))+1}`;
    option(resultPageSelect, group.key, `${label} (${group.count})`);
  }
  resultPageSelect.value = resultFilter.page;
  const filtered = resultFilter.page !== 'all' || resultFilter.section !== 'all';
  resultFilterSummary.textContent = filtered && resultFilter.all.length
    ? `Showing ${_matches.length} of ${resultFilter.all.length} matches.` : '';
  exportFilteredBtn.textContent = `Export filtered results (${_matches.length})`;
  exportFilteredBtn.disabled = !_matches.length;
  exportFilteredBtn.classList.toggle('hidden', !filtered || !resultFilter.all.length);
  const showPage = resultFilter.groups.length > 1 || resultFilter.page !== 'all';
  const showSection = resultFilter.sectionGroups.length > 1 || resultFilter.section !== 'all';
  resultPageRow.classList.toggle('hidden', !showPage);
  resultSectionRow.classList.toggle('hidden', !showSection);
  resultFilterEl.classList.toggle('hidden', resultsList.classList.contains('hidden') ||
    !resultFilter.all.length || (!showPage && !showSection));
}

function clearSearchResults({keepPage = false} = {}) {
  hideQueryHistoryDropdown();
  resultPreviewQueue.reset();
  _sourceContextRevision = '';
  resetSourceContextRequests();
  resultFilter.clear({keepPage});
  _matches = [];
  _stepIndex = 0;
  updateResultFilterControls();
}

resultPageSelect.addEventListener('change', () => {
  applyResultScope(() => resultFilter.select(resultPageSelect.value));
});
resultSectionSelect.addEventListener('change', () => {
  applyResultScope(() => resultFilter.selectSection(resultSectionSelect.value));
});
function applyResultScope(select) {
  _queryRevision += 1;
  const previous = _matches[_stepIndex];
  _matches = select();
  _stepIndex = Math.max(0, _matches.indexOf(previous));
  updateResultFilterControls();
  renderResults();
  resultsList.scrollTop = 0;
  if (_viewMode === 'step' && _matches.length && _matches[_stepIndex] !== previous) {
    sendScrollToMatch(_matches[_stepIndex]);
  }
}
let _searchGeneration = 0;
let _searchAbort = null;
let _navigationAbort = null;
let _sourceContextRevision = '';
let _sourceContextAbort = new AbortController();

function resetSourceContextRequests() {
  _sourceContextAbort.abort();
  _sourceContextAbort = new AbortController();
}

function cancelPendingSearch() {
  _navigationAbort?.abort();
  _searchGeneration += 1;
  if (_searchAbort) _searchAbort.abort();
  _searchAbort = null;
}
// 'list' shows every result as a scrollable card list (default). 'step' is a
// Ctrl+F-style view: exactly one result row is shown at a time, and the
// up/down buttons replace it in place while jumping to that occurrence.
let _viewMode = 'list';
let _stepIndex = 0;
// Set true just before enableSearch()'s own programmatic queryInput.focus()
// call; the focus listener clears it on the very next 'focus' event so that
// one event doesn't open the search-history dropdown. See Finding #4,
// batch-A final review.
let _suppressNextFocusDropdown = false;

searchBtn.disabled = true;
queryInput.disabled = true;
ocrPageInput.disabled = true;
ocrRunBtn.disabled = true;

const LEGACY_VIEWER_PATH_RE = /\/viewer\/viewer\.html$/i;
const CHROME_NATIVE_PDF_VIEWER_ID = 'mhjfbmdgcfjbbpaeojofohoefgiehjai';
const DEBUGGER_PROTOCOL_VERSION = '1.3';

// Absolute chrome-extension:// URLs for the bundled KaTeX assets so the
// injected in-PDF info panel (which runs in the *target tab*, not the popup)
// can load the same renderer the popup preview uses. Requires these paths to
// be listed under web_accessible_resources in manifest.json.
const KATEX_JS_URL = (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL)
  ? chrome.runtime.getURL('popup/lib/katex.min.js')
  : '';
const KATEX_CSS_URL = (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL)
  ? chrome.runtime.getURL('popup/lib/katex.min.css')
  : '';

function stripFragment(url) {
  return (url || '').split('#', 1)[0];
}

function normalizeUrl(url) {
  return stripFragment(url).trim();
}

function decodeMaybe(value) {
  if (!value) return '';
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function plausiblePdfSource(url) {
  if (!url) return '';
  const decoded = decodeMaybe(String(url).trim());
  return /^(https?|file):/i.test(decoded) ? decoded : '';
}

function extractArxivId(url) {
  return FormulaDocumentIdentity.arxivId(url);
}

function isArxivUrl(url) {
  return !!extractArxivId(url);
}

function arxivPdfUrl(url) {
  const id = extractArxivId(url);
  return id ? `https://arxiv.org/pdf/${id}` : '';
}

function isPdfUrl(url) {
  if (!url) return false;
  const u = stripFragment(url).toLowerCase();
  return /\.pdf([?]|$)/.test(u) ||
    isArxivUrl(url) ||
    u.includes('openreview.net/pdf') ||
    u.includes('semanticscholar.org/reader/') ||
    u.startsWith('chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/');
}

function isLegacyViewerUrl(url) {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'chrome-extension:' && LEGACY_VIEWER_PATH_RE.test(parsed.pathname);
  } catch {
    return false;
  }
}

function sourceUrlFromChromeNativeViewerUrl(tabUrl) {
  try {
    const parsed = new URL(tabUrl || '');
    if (parsed.protocol !== 'chrome-extension:' || parsed.host !== CHROME_NATIVE_PDF_VIEWER_ID) {
      return '';
    }

    const explicit = ['src', 'url', 'file']
      .map((key) => plausiblePdfSource(parsed.searchParams.get(key)))
      .find(Boolean);
    if (explicit) return explicit;

    const rawSearch = parsed.search ? parsed.search.slice(1) : '';
    const rawHash = parsed.hash ? parsed.hash.slice(1) : '';
    for (const candidate of [rawSearch, rawHash]) {
      const decoded = plausiblePdfSource(candidate);
      if (decoded) return decoded;
      const nested = candidate.match(/(?:^|[?&])(https?%3A%2F%2F[^&]+|file%3A%2F%2F[^&]+)/i);
      if (nested) {
        const nestedDecoded = plausiblePdfSource(nested[1]);
        if (nestedDecoded) return nestedDecoded;
      }
    }
  } catch {
    return '';
  }
  return '';
}

function sourceUrlFromTabUrl(tabUrl) {
  if (!tabUrl) return '';
  if (isLegacyViewerUrl(tabUrl)) {
    try {
      return new URL(tabUrl).searchParams.get('src') || '';
    } catch {
      return '';
    }
  }
  const nativeSource = sourceUrlFromChromeNativeViewerUrl(tabUrl);
  return nativeSource || tabUrl;
}

function nativePdfUrlForSource(url) {
  const arxivPdf = arxivPdfUrl(url);
  return arxivPdf || normalizeUrl(url);
}

function documentKey(url) {
  return FormulaDocumentIdentity.documentKey(url);
}

function desiredSourceType(url) {
  return isArxivUrl(url) ? 'tex' : 'text';
}

function enableSearch(n, emptyMsg) {
  statusEl.textContent = n
    ? `${n} formula${n !== 1 ? 's' : ''} indexed — type a query to search`
    : emptyMsg;
  searchBtn.disabled = false;
  queryInput.disabled = false;
  ocrPageInput.disabled = false;
  ocrRunBtn.disabled = false;
  // This .focus() is a programmatic side effect of search becoming ready,
  // not a genuine user interaction -- suppress the one 'focus' event it
  // fires so the history dropdown doesn't pop up over the idle
  // recent-papers list on the very first popup open of a browser session
  // (see Finding #4, batch-A final review). Cleared by the focus listener
  // itself the moment that event fires, so it never suppresses a later,
  // real user focus.
  _suppressNextFocusDropdown = true;
  if (!IS_SIDE_PANEL) queryInput.focus();
}

function resetDocumentSearch() {
  document.getElementById('document-warning')?.replaceChildren();
  cancelPendingSearch();
  currentSessionUrl = '';
  clearSearchResults();
  resultsList.innerHTML = '';
  exportBtn.disabled = true;
  searchBtn.disabled = queryInput.disabled = true;
  ocrPageInput.disabled = ocrRunBtn.disabled = true;
  updateStepControls();
  hideQueryHistoryDropdown();
  if (_activeTab?.id) {
    chrome.runtime.sendMessage({type: 'searchComplete', matchCount: 0, tabId: _activeTab.id}).catch(() => {});
  }
}

function recordLoadedPaper(url, count, context) {
  recordRecentPaper(url, count).then(loadRecentPapers).then(papers => {
    if (documentSession.isCurrent(context)) renderRecentPapers(papers);
  }).catch(() => {});
}

function showDocumentWarning(data, url, context) {
  let warning = document.getElementById('document-warning');
  if (!warning) {
    warning = document.createElement('div'); warning.id = 'document-warning'; warning.setAttribute('role', 'status');
    statusEl.after(warning);
  }
  warning.replaceChildren();
  if (!data.pdf_warning) return;
  warning.append(document.createTextNode(data.pdf_warning + ' '));
  const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry PDF loading';
  retry.onclick = () => {
    if (documentSession.isCurrent(context)) processArxiv(url, context);
  };
  warning.append(retry);
  if (!data.pdf_cached) ocrRunBtn.disabled = ocrPageInput.disabled = true;
}

async function processArxiv(tabUrl, context = documentSession.current) {
  if (!documentSession.isCurrent(context)) return;
  resetDocumentSearch();
  showDocumentWarning({}, tabUrl, context);
  statusEl.textContent = 'Loading formulas from TeX source…';
  const formData = new FormData();
  formData.append('url', tabUrl);
  try {
    const res = await FormulaRequests.request(`${BACKEND}/process`, {method: 'POST', body: formData, signal: context.signal});
    const data = await res.json();
    if (!documentSession.isCurrent(context)) return;
    if (!res.ok) {
      if (isPdfUrl(tabUrl)) await processTab(tabUrl, context);
      else statusEl.textContent = `Could not load source: ${data.detail || res.statusText}`;
      return;
    }
    currentSessionUrl = tabUrl;
    enableSearch(data.formula_count, 'No formulas found in the source. Try another query or page OCR.');
    recordLoadedPaper(tabUrl, data.formula_count, context);
    if (data.cache_hit) statusEl.textContent += ' Loaded from cache.';
    showDocumentWarning(data, tabUrl, context);
  } catch (err) {
    if (documentSession.isCurrent(context) && err.name !== 'AbortError') {
      statusEl.textContent = `Could not load this paper: ${err.message}. Check that the local backend is running.`;
    }
  }
}

async function processTab(tabUrl, context = documentSession.current) {
  if (!documentSession.isCurrent(context)) return;
  resetDocumentSearch();
  showDocumentWarning({}, tabUrl, context);
  const pdfUrl = nativePdfUrlForSource(tabUrl);
  statusEl.textContent = 'Loading the PDF…';
  let pdfBytes;
  try {
    pdfBytes = await FormulaRequests.pdf(pdfUrl, {signal: context.signal});
    if (!documentSession.isCurrent(context)) return;
  } catch (err) {
    if (documentSession.isCurrent(context) && err.name !== 'AbortError') {
      statusEl.textContent = `Could not read the PDF: ${err.message}. For a local file, enable file URL access for Formula Search in chrome://extensions.`;
    }
    return;
  }
  statusEl.textContent = 'Extracting formulas…';
  const formData = new FormData();
  formData.append('file', new Blob([pdfBytes], {type: 'application/pdf'}), 'document.pdf');
  formData.append('url', pdfUrl);
  try {
    const res = await FormulaRequests.request(`${BACKEND}/process`, {method: 'POST', body: formData, signal: context.signal});
    const data = await res.json();
    if (!documentSession.isCurrent(context)) return;
    if (!res.ok) {
      statusEl.textContent = `Could not index the PDF: ${data.detail || res.statusText}`;
      return;
    }
    currentSessionUrl = pdfUrl;
    enableSearch(data.formula_count, 'No text formulas found. Try page OCR if the paper is scanned.');
    recordLoadedPaper(tabUrl, data.formula_count, context);
    if (data.cache_hit) statusEl.textContent += ' Loaded from cache.';
    showDocumentWarning(data, tabUrl, context);
  } catch (err) {
    if (documentSession.isCurrent(context) && err.name !== 'AbortError') {
      statusEl.textContent = `Could not index the PDF: ${err.message}. Check that the local backend is running.`;
    }
  }
}

function renderRecentPapers(papers) {
  // Guard against a stale render landing on top of search results. This is
  // reached via multiple un-awaited promise chains (init()'s direct
  // loadRecentPapers().then(renderRecentPapers), and
  // processArxiv/processTab's recordRecentPaper(...).then(...).then(
  // renderRecentPapers) chains) that can resolve *after* runSearch() has
  // already hidden #recent-papers -- runSearch's hide is synchronous and
  // needs only one storage hop (restoreLastQuery's loadLastQuery) to reach
  // it, while these chains need three (read/write/read). If a search is in
  // flight or has results, or the query field already holds a value that's
  // about to auto-search, skip the visible render. The underlying data was
  // already persisted by recordRecentPaper regardless.
  if (_matches.length > 0 || queryInput.value.trim()) return;
  recentPapersEl.innerHTML = '';
  if (!papers.length) { recentPapersEl.classList.add('hidden'); return; }
  const heading = document.createElement('div');
  heading.className = 'recent-papers-heading';
  heading.textContent = 'Recently opened';
  recentPapersEl.appendChild(heading);
  const list = document.createElement('ul');
  list.className = 'recent-papers-list';
  papers.forEach((p) => {
    const li = document.createElement('li');
    const title = document.createElement('div');
    title.className = 'recent-paper-title';
    title.textContent = p.title || p.url;
    const meta = document.createElement('div');
    meta.className = 'recent-paper-meta';
    meta.textContent = `${p.formulaCount} formula${p.formulaCount === 1 ? '' : 's'} · ${formatRelativeTime(p.lastOpened)}`;
    li.appendChild(title);
    li.appendChild(meta);
    list.appendChild(li);
  });
  recentPapersEl.appendChild(list);
  recentPapersEl.classList.remove('hidden');
}

function matchMessage(match, opts = {}) {
  const payload = {
    type: 'scrollToFormula',
    page: Number(match.page),
    bbox: Array.isArray(match.bbox) ? match.bbox : null,
    page_w: Number(match.page_w || 0),
    page_h: Number(match.page_h || 0),
    latex: match.latex || '',
    source: match.source || '',
    source_url: match.resolved_source_url || match.source_url || _sourceUrl,
    match_type: match.match_type || '',
    bbox_source: match.bbox_source || '',
    location_status: match.location_status || '',
    extensionId: (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) || '',
  };
  if (opts.showInfoPanel) {
    payload.showInfoPanel = true;
    payload.infoPanel = {
      rawLatex: match.latex || '',
      previewLatex: sanitizeLatexForPreview(match.highlighted_latex || match.latex || ''),
      label: match.label || '',
      section: match.section || '',
      sourceType: match.source || '',
      matchType: match.match_type || '',
      katexJsUrl: KATEX_JS_URL,
      katexCssUrl: KATEX_CSS_URL,
      katexMacros: KATEX_PREVIEW_MACROS,
    };
  }
  return payload;
}

function nativePdfInstantJump(payload) {
  const OVERLAY_ATTR = 'data-formula-search-overlay';
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

  function finiteNumber(value, fallback = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function pageIndex(data) {
    const page = Number(data && data.page);
    return Number.isInteger(page) && page >= 0 ? page : -1;
  }

  function bboxFromPayload(data) {
    if (!Array.isArray(data && data.bbox) || data.bbox.length < 4) return null;
    const box = data.bbox.slice(0, 4).map(Number);
    if (!box.every(Number.isFinite)) return null;
    const x0 = Math.min(box[0], box[2]);
    const y0 = Math.min(box[1], box[3]);
    const x1 = Math.max(box[0], box[2]);
    const y1 = Math.max(box[1], box[3]);
    return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
  }

  function getOpenOrClosedShadowRoot(element) {
    if (!element) return null;
    try { if (element.shadowRoot) return element.shadowRoot; } catch { /* noop */ }
    try { if (element.renderRoot) return element.renderRoot; } catch { /* noop */ }
    try {
      if (typeof chrome !== 'undefined' && chrome.dom && typeof chrome.dom.openOrClosedShadowRoot === 'function') {
        return chrome.dom.openOrClosedShadowRoot(element);
      }
    } catch { /* noop */ }
    return null;
  }

  function safeQuery(root, selector) {
    try { return root && typeof root.querySelector === 'function' ? root.querySelector(selector) : null; } catch { return null; }
  }

  function childNodes(root) {
    try { return root && root.children ? Array.from(root.children) : []; } catch { return []; }
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

  function getViewer() {
    return safeQuery(document, 'pdf-viewer') || findDeep(document, 'pdf-viewer') || null;
  }

  function getViewerRoot(viewer) {
    return getOpenOrClosedShadowRoot(viewer) || viewer || null;
  }

  function getViewport(viewer) {
    if (!viewer) return null;
    for (const key of ['viewport', 'viewport_']) {
      try { if (viewer[key]) return viewer[key]; } catch { /* try next */ }
    }
    return null;
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

  function validPoint(point) {
    return point && Number.isFinite(point.x) && Number.isFinite(point.y);
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
      // POSITION_TRANSITION below: the very first placement (right after a
      // jump) must snap there instantly, but every reflow update after that
      // eases toward its target instead of snapping. Chromium's PDF plugin
      // can report getPageScreenRect()/getPageInsetDimensions() a frame or
      // two behind the actual on-screen scroll position under fast/
      // continuous scrolling -- with an instant snap, a single stale read
      // in that window showed up as the highlight visibly teleporting to a
      // wrong (often corner-clipped) spot for a frame before either the
      // geometry caught up or the visibility check hid it. Easing the
      // position absorbs that kind of single-frame glitch into an
      // imperceptible wobble instead of a visible jump, regardless of
      // exactly which frame or two was stale -- it doesn't depend on
      // guessing the right "implausible distance" cutoff the way rejecting
      // suspicious reads outright would.
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
      // panel is closed or another formula is selected (see closeInfoPanel).
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

  function scheduleViewportOverlay(data, viewport, viewer) {
    return scheduleOverlay(data, viewport, viewer);
  }

  function targetPagePoint(data) {
    const box = bboxFromPayload(data);
    if (!box) return { x: 0, y: 0 };
    const pageHeight = finiteNumber(data.page_h, 0);
    const margin = pageHeight > 0 ? Math.min(JUMP_MARGIN_PDF_PT, pageHeight * 0.008) : JUMP_MARGIN_PDF_PT;
    const topWithMargin = Math.max(0, box[1] - margin);
    return {
      x: Math.max(0, box[0] - JUMP_X_MARGIN_PDF_PT),
      y: pageHeight > 0 ? Math.max(0, pageHeight - topWithMargin) : topWithMargin,
    };
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

  function tryViewportJump(data, viewer) {
    const page = pageIndex(data);
    const viewport = getViewport(viewer);
    if (page < 0 || !viewport) return { ok: false, reason: 'native viewport object unavailable' };
    try {
      if (!bboxFromPayload(data) && typeof viewport.goToPage === 'function') {
        viewport.goToPage(page);
        return {ok: true, mode: 'native-viewport-page', highlighted: false};
      }
      if (centerViewportTarget(data, viewport)) {
        return {ok: true, mode: 'native-viewport-context', highlighted: scheduleViewportOverlay(data, viewport, viewer)};
      }
      const goToXy = viewport.goToPageAndXy || viewport.goToPageAndXY;
      if (bboxFromPayload(data) && typeof viewport.convertPageToScreen === 'function' && typeof goToXy === 'function') {
        const point = viewport.convertPageToScreen(page, targetPagePoint(data));
        if (validPoint(point)) {
          goToXy.call(viewport, page, point.x, point.y);
          const highlighted = scheduleViewportOverlay(data, viewport, viewer);
          return { ok: true, mode: 'native-viewport-xy', highlighted };
        }
      }
      if (typeof viewport.goToPage === 'function') {
        viewport.goToPage(page);
        const highlighted = scheduleViewportOverlay(data, viewport, viewer);
        return { ok: true, mode: 'native-viewport-page', highlighted };
      }
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }
    return { ok: false, reason: 'native viewport jump methods unavailable' };
  }

  function tryViewerPrivateMethod(data, viewer) {
    const page = pageIndex(data);
    const viewport = getViewport(viewer);
    if (page < 0 || !viewer) return { ok: false, reason: 'viewer unavailable' };
    if (!bboxFromPayload(data)) return {ok: false, reason: 'page-only navigation requires a page event'};
    try {
      if (viewport && typeof viewport.convertPageToScreen === 'function' && typeof viewer.goToPageAndXy_ === 'function') {
        const point = viewport.convertPageToScreen(page, targetPagePoint(data));
        if (validPoint(point)) {
          viewer.goToPageAndXy_('formula-search', page, point);
          const highlighted = scheduleViewportOverlay(data, viewport, viewer);
          return { ok: true, mode: 'native-viewer-private-xy', highlighted };
        }
      }
      if (typeof viewer.onChangePageAndXy_ === 'function') {
        const point = targetPagePoint(data);
        const event = new CustomEvent('change-page-and-xy', {
          bubbles: true,
          composed: true,
          detail: { page, x: point.x, y: point.y, origin: 'formula-search' },
        });
        viewer.onChangePageAndXy_(event);
        const highlighted = scheduleViewportOverlay(data, viewport, viewer);
        return { ok: true, mode: 'native-viewer-private-event', highlighted };
      }
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }
    return { ok: false, reason: 'private viewer jump methods unavailable' };
  }

  function dispatchChangePageEvent(target, data, origin) {
    const page = pageIndex(data);
    const box = bboxFromPayload(data);
    const point = targetPagePoint(data);
    const eventName = box ? 'change-page-and-xy' : 'change-page';
    const detail = box ? { page, x: point.x, y: point.y, origin } : { page, origin };
    target.dispatchEvent(new CustomEvent(eventName, { bubbles: true, composed: true, detail }));
    return eventName;
  }

  function tryChangePageEvent(data, viewer) {
    const page = pageIndex(data);
    if (page < 0) return { ok: false, reason: 'invalid page' };
    if (!viewer) return { ok: false, reason: 'viewer unavailable' };
    const root = getViewerRoot(viewer);
    const targets = [
      safeQuery(root, '#sidenav'),
      safeQuery(root, 'viewer-pdf-sidenav'),
      findDeep(root, 'viewer-pdf-sidenav'),
      document.body,
    ].filter(Boolean);
    if (!targets.length) return { ok: false, reason: 'native PDF event target unavailable' };
    try {
      let eventName = '';
      for (const target of targets) eventName = dispatchChangePageEvent(target, data, 'formula-search');
      const viewport = getViewport(viewer);
      const highlighted = scheduleViewportOverlay(data, viewport, viewer);
      return { ok: true, mode: `native-event-${eventName}`, highlighted };
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }
  }

  function approximateScroll(data, viewer) {
    const page = pageIndex(data);
    if (page < 0) return { ok: false, reason: 'invalid page' };
    const scroller = getScroller(viewer);
    if (!scroller) return { ok: false, reason: 'no native scroller found' };
    let docLength = 0;
    const viewport = getViewport(viewer);
    for (const candidate of [
      viewer && viewer.docLength,
      viewer && viewer.docLength_,
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
    const box = bboxFromPayload(data);
    const pageHeight = finiteNumber(data.page_h, 0);
    const pageRatio = box && pageHeight > 0 ? Math.max(0, Math.min(1, box[1] / pageHeight)) : 0;
    const totalHeight = scroller.scrollHeight || scroller.offsetHeight || 0;
    const pageHeightCss = docLength > 0 ? totalHeight / docLength : 0;
    const targetTop = Math.max(0, pageHeightCss * page + pageHeightCss * pageRatio - 8);
    try {
      if (typeof scroller.scrollTo === 'function') scroller.scrollTo({ top: targetTop, left: 0, behavior: 'auto' });
      else { scroller.scrollTop = targetTop; scroller.scrollLeft = 0; }
      return { ok: true, mode: 'native-scroller-approximation', highlighted: false };
    } catch (err) {
      return { ok: false, reason: err && err.message ? err.message : String(err) };
    }
  }

  const INFO_PANEL_ATTR = 'data-formula-search-info-panel';
  const INFO_PANEL_STYLE_ID = 'formula-search-info-panel-style';
  const CONNECTOR_ATTR = 'data-formula-search-connector';
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
      const controller = globalThis.__formulaSearchInfoPanelController;
      if (controller && typeof controller.destroy === 'function') controller.destroy();
      globalThis.__formulaSearchInfoPanelController = null;
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

    // Prefer placing the card beside the highlighted formula (right, then
    // left) so the two stay visually connected; fall back to below it when
    // there is no horizontal room, similar to the annotation-card layout
    // this info panel is modeled after.
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
  // only confirm a page, not an exact rendered location -- a real,
  // fairly common state for TeX-sourced matches). Without this the card
  // stayed at its off-screen creation position forever, so it looked like
  // the info button silently did nothing.
  function placeInfoCardFallback(card) {
    const margin = 12;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const cardW = card.offsetWidth || 360;
    card.style.left = `${Math.max(margin, vw - cardW - margin)}px`;
    card.style.top = `${margin}px`;
  }

  // The debugger transport's Runtime.evaluate runs this function inside
  // Chrome's built-in PDF Viewer extension's own page realm, a different
  // extension context than ours -- chrome.runtime.id there would be THAT
  // extension's id, not ours, so it can't be read locally. payload.extensionId
  // is captured by matchMessage() in the privileged popup/outer script (the
  // only context that reliably knows our own correct id) and threaded through
  // the jump payload for exactly this reason.
  const RELAY_EXTENSION_ID = payload.extensionId || '';

  // === CHAT STORE (popup-injected) BEGIN ===
  const CHAT_STORAGE_KEY = 'fs_chat_history';
  const CHAT_MAX_HISTORY = 500;
  const CHAT_COPY_GLYPH = '⧉';

  function loadChatHistory() {
    return FormulaStoreClient('notes.list', {}, RELAY_EXTENSION_ID);
  }
  function appendChatMessage(text, context) {
    return FormulaStoreClient('notes.append', {text, context: context || null}, RELAY_EXTENSION_ID);
  }
  function clearChatHistory() {
    return FormulaStoreClient('notes.clear', {}, RELAY_EXTENSION_ID);
  }

  const CHAT_DRAFT_KEY = 'fs_chat_draft';
  // In-memory shadow of the last known draft, alongside the storage-backed
  // copy -- saveDraft's write is fire-and-forget (matches every call site,
  // which saves on every keystroke and can't block on a round-trip), so a
  // write that rejects (e.g. extension context invalidated mid-send) would
  // otherwise leave a later loadDraft() reading stale/empty storage even
  // though the caller already re-typed the text into cachedDraft's source
  // of truth.
  //
  // That protection is currently INERT for this copy in real usage, though:
  // this whole function (nativePdfInstantJump) is re-injected fresh via
  // chrome.scripting.executeScript/Runtime.evaluate on every single jump --
  // no LOAD_GUARD keeps a script instance resident the way content.js's
  // does -- so cachedDraft starts null on every execution, and showInfoPanel
  // calls loadDraft() exactly once per execution, before the user has typed
  // anything. There is no second read within one execution left to ever
  // observe a cache hit. Kept anyway for consistency with the other two
  // copies of this code and because it's cheap and correct -- it would
  // start protecting drafts here for real the moment any future code adds a
  // second loadDraft() call site within one execution.
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
  // reachable by tests_js/chat_store_popup_injected.js's block extraction,
  // which only pulls in this CHAT STORE region -- not the surrounding
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
  // === CHAT STORE (popup-injected) END ===

  function showInfoPanel(payload, viewport, viewer) {
    const info = payload.infoPanel;
    if (!info) return false;
    // Any previous info panel/pinned highlight was already retired by the
    // closeInfoPanel() call at the top of nativePdfInstantJump, before the
    // jump attempts (and this jump's own new highlight) ran -- calling it
    // again here would race that highlight's creation, since
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
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage && RELAY_EXTENSION_ID) {
        // Best-effort: chrome.action.openPopup() may not succeed depending
        // on whether Chrome still considers this click a live user gesture
        // by the time the relay reaches the service worker. Closing the
        // card (above) always works regardless; reopening the popup is a
        // bonus, not a guarantee.
        try { chrome.runtime.sendMessage(RELAY_EXTENSION_ID, { type: 'openPopup' }, () => {}); } catch { /* best effort */ }
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

    // Base structure only: shows the raw LaTeX (progressively upgraded to a
    // KaTeX render below) plus match metadata. A future iteration can swap
    // this block for backend-provided explanations / related formulas
    // without touching the anchoring or transport plumbing above.
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
    // how they all stay in sync without polling.
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
      if (changes[CHAT_DRAFT_KEY]) {
        // Keeps the in-memory draft shadow (see cachedDraft above) from
        // shadowing a newer value written elsewhere -- same caveat as
        // cachedDraft itself applies here: since this execution never calls
        // loadDraft() a second time, there is nothing left in THIS
        // execution to actually read cachedDraft again after this update.
        // Harmless, and here for the same forward-compatibility reason.
        cachedDraft = changes[CHAT_DRAFT_KEY].newValue || '';
      }
    };
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener(onChatStorageChanged);
    }
    const stopNoteSubscription = (typeof chrome === 'undefined' || !chrome.storage)
      ? FormulaStoreSubscribe(publishNotes, RELAY_EXTENSION_ID) : () => {};
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
    globalThis.__formulaSearchInfoPanelController = controller;

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

  const page = pageIndex(payload);
  if (page < 0) return { ok: false, error: 'Match has no page mapping.' };

  // Jumping to any formula (via a plain row click or the info arrow) retires
  // whatever was previously pinned open -- there is only ever one active
  // highlight/info panel/connector set at a time. Done once, up front,
  // rather than from inside installDynamicOverlay's clearOverlay(): that
  // call is deferred a frame via requestAnimationFrame (see
  // scheduleOverlay), which would otherwise run *after* showInfoPanel below
  // and destroy the very info panel this same jump just opened.
  const viewer = getViewer();
  if (!viewer) {
    return {
      ok: false,
      error: 'Chrome PDF viewer element was not found in this execution context.',
      href: location.href,
      title: document.title,
      hrefUnchanged: true,
      viewerFound: false,
    };
  }

  const readiness = globalThis.FormulaNativeViewerState(page, viewer, payload.navigationUrl);
  if (!readiness.ok) return readiness;
  closeInfoPanel();

  const attempts = [];
  for (const attempt of [
    () => tryViewportJump(payload, viewer),
    () => tryViewerPrivateMethod(payload, viewer),
    () => tryChangePageEvent(payload, viewer),
    () => approximateScroll(payload, viewer),
  ]) {
    const result = attempt();
    attempts.push(result);
    if (result && result.ok) {
      if (payload.showInfoPanel) {
        try { showInfoPanel(payload, getViewport(viewer), viewer); } catch { /* info panel is best-effort */ }
      }
      return {
        ok: true,
        page,
        mode: result.mode,
        highlighted: !!result.highlighted,
        bbox: !!bboxFromPayload(payload),
        hrefUnchanged: true,
        viewerFound: true,
        attempts,
      };
    }
  }

  return {
    ok: false,
    error: 'Could not drive Chrome native PDF viewer without URL navigation.',
    hrefUnchanged: true,
    viewerFound: true,
    attempts,
  };
}

function normalizeInjectionResult(entry) {
  const result = entry && entry.result;
  const value = result && typeof result === 'object'
    ? result
    : { ok: false, error: result === undefined ? 'empty injection result' : String(result) };
  return {
    ...value,
    frameId: entry && entry.frameId,
    documentId: entry && entry.documentId,
  };
}

function pickBestNativeResult(results) {
  const normalized = (results || []).map(normalizeInjectionResult);
  return normalized.find((result) => result.ok && result.highlighted) ||
    normalized.find((result) => result.ok && result.mode && !/approximation/.test(result.mode)) ||
    normalized.find((result) => result.ok) ||
    normalized.find((result) => result.viewerFound) ||
    normalized[0] ||
    { ok: false, error: 'No execution result returned.' };
}

async function nativeRequestFailure(tabId, options) {
  const stale = () => options.signal?.aborted || options.isCurrent && !options.isCurrent();
  const cancelled = {ok: false, code: 'cancelled', error: 'This formula jump was cancelled.'};
  if (stale()) return cancelled;
  if (options.url) {
    let tab;
    try { tab = await chrome.tabs.get(tabId); }
    catch { return {ok: false, code: 'tab-closed', error: 'This paper tab was closed.'}; }
    if (String(tab.url || '').split('#', 1)[0] !== String(options.url).split('#', 1)[0]) {
      return {ok: false, code: 'document-changed', error: 'The paper in this tab changed. Search the current paper again.'};
    }
  }
  return stale() ? cancelled : null;
}

async function executeNativeJumpViaScripting(tabId, payload, options = {}) {
  if (!chrome.scripting || typeof chrome.scripting.executeScript !== 'function') {
    return { ok: false, error: 'chrome.scripting.executeScript is unavailable.' };
  }

  const targets = [
    { tabId, allFrames: true },
    { tabId },
  ];
  let lastError = '';

  for (const target of targets) {
    for (const world of ['MAIN', 'ISOLATED']) {
      try {
        const before = await nativeRequestFailure(tabId, options);
        if (before) return before;
        await chrome.scripting.executeScript({target, world, files: ['native-viewer-state.js']});
        if (payload.showInfoPanel) await chrome.scripting.executeScript({target, world, files: ['store-client.js']});
        const after = await nativeRequestFailure(tabId, options);
        if (after) return after;
        const results = await chrome.scripting.executeScript({
          target,
          world,
          func: nativePdfInstantJump,
          args: [payload],
          injectImmediately: true,
        });
        const best = pickBestNativeResult(results);
        if (best && best.ok) {
          return { ...best, transport: `scripting-${world.toLowerCase()}${target.allFrames ? '-all-frames' : '-main-frame'}` };
        }
        if (best && (best.error || best.reason)) lastError = best.error || best.reason;
      } catch (err) {
        lastError = err && err.message ? err.message : String(err);
      }
    }
  }

  return { ok: false, error: lastError || 'scripting injection did not reach the native PDF viewer', transport: 'scripting' };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runtimeResultFromEvaluation(raw) {
  if (!raw) return { ok: false, error: 'Runtime.evaluate returned no response.' };
  if (raw.exceptionDetails) {
    const text = raw.exceptionDetails.text || 'Runtime exception';
    const description = raw.exceptionDetails.exception && raw.exceptionDetails.exception.description;
    return { ok: false, error: description || text };
  }
  const remote = raw.result || {};
  if (remote.value && typeof remote.value === 'object') return remote.value;
  if (remote.type === 'undefined') return { ok: false, error: 'Evaluation returned undefined.' };
  return { ok: false, error: remote.description || String(remote.value || 'Unexpected evaluation result.') };
}

// Chrome's own PDF viewer is itself a bundled extension page
// (chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/...), and its
// script-src CSP ("script-src 'self' ...", no 'unsafe-eval') blocks BOTH a
// <script src="chrome-extension://<us>/..."> tag (cross-extension resource
// loads for scripts are refused outright) AND eval()/Function() called from
// code already running inside that page -- which is exactly how
// showInfoPanel's loadKatexIntoPage tried to load KaTeX, so it always
// silently failed there and fell back to raw-text (verified live: Chrome
// logs "violates ... script-src" and "Denying load ... web_accessible_resources"
// for every preview, the katex.min.js <script> tag never actually runs).
// A fresh chrome.debugger Runtime.evaluate call, in contrast, is NOT
// subject to the target page's CSP (the same reason executeNativeJumpViaDebugger
// exists at all, to reach the guest view chrome.scripting itself can't
// inject into) -- so instead of asking the ALREADY-INJECTED code to load
// KaTeX, the debugger transport pre-seeds window.katex itself, in a
// separate Runtime.evaluate call, before running the main jump. Once that
// succeeds, loadKatexIntoPage's own `if (globalThis.katex) return
// Promise.resolve(true);` check short-circuits immediately and it never
// needs to touch the CSP-blocked path at all.
let _katexBundlePromise = null;
function getKatexBundleSource() {
  if (_katexBundlePromise) return _katexBundlePromise;
  if (!KATEX_JS_URL) return Promise.resolve(null);
  _katexBundlePromise = (async () => {
    try {
      const [jsRes, cssRes] = await Promise.all([
        fetch(KATEX_JS_URL).then((r) => (r.ok ? r.text() : null)),
        KATEX_CSS_URL ? fetch(KATEX_CSS_URL).then((r) => (r.ok ? r.text() : '')) : Promise.resolve(''),
      ]);
      if (!jsRes) return null;
      // katex.min.css references its @font-face files with relative
      // `url(fonts/...)` paths, resolved against wherever the stylesheet
      // itself lives. That works for a normal <link> in our own popup, but
      // once injected as inline CSS text into a completely different page
      // (the PDF viewer's own extension page), the browser resolves those
      // relative paths against THAT page's URL instead and 404s on every
      // glyph -- rewrite them to absolute chrome-extension://.../fonts/
      // URLs first so they resolve correctly no matter where this CSS ends
      // up.
      const fontsBase = chrome.runtime.getURL('popup/lib/fonts/');
      const css = (cssRes || '').replace(/url\(fonts\//g, `url(${fontsBase}`);
      return { js: jsRes, css };
    } catch {
      return null;
    }
  })();
  return _katexBundlePromise;
}

function buildKatexInstallExpression(bundle) {
  // Deliberately NOT wrapped in a function -- katex.min.js is itself a
  // single self-invoking UMD expression that assigns to `self`/`this` at
  // the top level (`e.katex=t()` inside `!function(e,t){...}("undefined"!=
  // typeof self?self:this, ...)`); calling it from inside an extra function
  // scope risks changing what `this` resolves to. A plain `if` block does
  // not affect `this`, so the katex source runs exactly as it would from a
  // normal top-level <script> tag.
  const cssBlock = bundle.css
    ? `if (!document.getElementById('formula-search-katex-style')) {
        var __fsKatexStyle = document.createElement('style');
        __fsKatexStyle.id = 'formula-search-katex-style';
        __fsKatexStyle.textContent = ${JSON.stringify(bundle.css)};
        (document.head || document.documentElement).appendChild(__fsKatexStyle);
      }`
    : '';
  return `if (!window.katex) {\n${bundle.js}\n}\n${cssBlock}`;
}

async function executeNativeJumpViaDebugger(tabId, payload, options = {}) {
  try {
    return await FormulaNativeBridge.withViewer(tabId, async (evaluate) => {
      if (payload.showInfoPanel && payload.infoPanel && payload.infoPanel.previewLatex) {
        const bundle = await getKatexBundleSource().catch(() => null);
        if (bundle) await evaluate(buildKatexInstallExpression(bundle)).catch(() => {});
      }
      const expression = `globalThis.FormulaNativeViewerState = (${FormulaNativeViewerState.toString()}); globalThis.FormulaStoreClient = (${FormulaStoreClient.toString()}); globalThis.FormulaStoreSubscribe = (${FormulaStoreSubscribe.toString()}); (${nativePdfInstantJump.toString()})(${JSON.stringify(payload)})`;
      const raw = await evaluate(expression, { userGesture: true });
      return { ...runtimeResultFromEvaluation(raw), transport: 'debugger' };
    }, {...options, page: payload.page});
  } catch (err) {
    return { ok: false, code: err?.code, detachReason: err?.detachReason, discovery: err?.discovery,
      error: err && err.message ? err.message : String(err), transport: 'debugger' };
  }
}

async function ensureNativeContentScript(tabId) {
  try {
    const ping = await chrome.tabs.sendMessage(tabId, { type: 'formulaSearchPing' });
    if (ping?.ok && ping.nativeReadinessVersion === 1) return true;
  } catch {
    // The bridge is injected on demand; many PDF tabs will not have it yet.
  }

  if (!chrome.scripting || typeof chrome.scripting.executeScript !== 'function') return false;
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['native-viewer-state.js', 'store-client.js', 'content.js'] });
    const ping = await chrome.tabs.sendMessage(tabId, { type: 'formulaSearchPing' });
    return !!(ping?.ok && ping.nativeReadinessVersion === 1);
  } catch {
    return false;
  }
}

async function executeNativeJumpViaContentBridge(tabId, payload, options = {}) {
  const before = await nativeRequestFailure(tabId, options);
  if (before) return before;
  const canMessageTab = await ensureNativeContentScript(tabId);
  if (!canMessageTab) return { ok: false, error: 'content bridge unavailable', transport: 'content-script' };

  try {
    const after = await nativeRequestFailure(tabId, options);
    if (after) return after;
    const response = await chrome.tabs.sendMessage(tabId, payload);
    return { ...(response || { ok: false, error: 'empty content bridge response' }), transport: 'content-script' };
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err), transport: 'content-script' };
  }
}

function statusForNativeResponse(match, response) {
  const page = Number(match.page) + 1;
  if (response?.highlighted) return `Highlighted the formula on page ${page}.`;
  if (response?.ok && match.location_status === 'ambiguous') return `Opened page ${page}. Several source occurrences share this estimated location; no highlight is shown.`;
  if (response?.ok) return `Opened page ${page}. An exact highlight is unavailable for this match.`;
  if (response?.code && response.error) return response.error;
  return `Could not jump to page ${page}. Make sure the paper is open in Chrome’s PDF viewer.`;
}

async function openSavedFormula(match, opts = {}) {
  if (!plausiblePdfSource(match.source_url)) {
    statusEl.textContent = 'This saved formula has no usable paper URL.';
    return;
  }
  const request = {match, opts, key: documentKey(match.source_url), activated: false};
  _pendingSavedNavigation = request;
  statusEl.textContent = 'Opening the saved paper…';
  try {
    const tabs = await chrome.tabs.query({windowId: _panelWindowId});
    if (_pendingSavedNavigation !== request) return;
    const existing = tabs.find(tab => documentKey(sourceUrlFromTabUrl(tab.url)) === request.key);
    const pdfUrl = nativePdfUrlForSource(match.source_url);
    const update = {active: true};
    if (existing && /arxiv\.org\/abs\//i.test(existing.url)) update.url = pdfUrl;
    const tab = existing ? await chrome.tabs.update(existing.id, update)
      : await chrome.tabs.create({windowId: _panelWindowId, url: pdfUrl, active: true});
    if (_pendingSavedNavigation === request) await init(tab, {restoreQuery: true});
  } catch (error) {
    if (_pendingSavedNavigation === request) {
      _pendingSavedNavigation = null;
      statusEl.textContent = `Could not open the saved paper: ${error.message}`;
    }
  }
}

async function recheckSavedFormula(match, opts) {
  const context = documentSession.current;
  setQueryValue(match.latex || '');
  document.getElementById('search-mode').value = 'exact';
  showView('list');
  await runSearch();
  if (!documentSession.isCurrent(context) || queryInput.value.trim() !== (match.latex || '').trim()) return;
  const current = resultFilter.all.filter(item => item.mapping_revision === 'source-mapping2' &&
    FormulaBookmarkIdentity.same(match,bookmarkEntry(item)));
  if (current.length === 1) return sendScrollToMatch(current[0],opts);
  statusEl.textContent = 'This saved location needs rechecking after a parser update. Select a current result to locate it; the original bookmark is retained.';
}

let nativeNavigationRevision = 0;
async function sendScrollToMatch(match, opts = {}) {
  const revision = ++nativeNavigationRevision;
  if (match.source_url && documentKey(match.source_url) !== documentKey(_sourceUrl)) return openSavedFormula(match, opts);
  if (match.id && match.source_file && match.mapping_revision !== 'source-mapping2') return recheckSavedFormula(match,opts);
  if (!_activeTab || match.page === -1 || match.page === undefined || match.page === null) {
    statusEl.textContent = 'This match has no page mapping yet.';
    return;
  }

  const context = documentSession.current;
  _navigationAbort?.abort();
  _navigationAbort = new AbortController();
  const navigationSignal = AbortSignal.any([context.signal,_navigationAbort.signal]);
  const tabId = _activeTab.id;
  const isCurrent = () => !navigationSignal.aborted && revision === nativeNavigationRevision && documentSession.isCurrent(context);
  const navigation = {signal: navigationSignal, isCurrent, url: _activeTab.url};
  const payload = matchMessage(match, opts);
  payload.navigationUrl = navigation.url;
  statusEl.textContent = opts.showInfoPanel
    ? `Jumping to page ${Number(match.page) + 1} and opening the formula info panel...`
    : `Jumping to page ${Number(match.page) + 1}…`;

  const supportedJump = async () => {
    const stale = await nativeRequestFailure(tabId,navigation);
    if (stale || !isCurrent()) return;
    const target = nativePdfUrlForSource(match.resolved_source_url || _sourceUrl);
    const tab = await chrome.tabs.get(tabId);
    if (!isCurrent() || tab.url.split('#',1)[0] !== navigation.url.split('#',1)[0]) return;
    const samePdf = tab.url.split('#',1)[0] === target.split('#',1)[0];
    const pageUrl = FormulaPdfNavigation.pageUrl(samePdf ? tab.url : target,match.page);
    await chrome.tabs.update(tabId,{url:pageUrl});
    // Current Chrome does not apply new PDF open parameters on a hash-only
    // navigation. A normal cached reload applies page= reliably.
    if (samePdf) {
      if (!await FormulaPdfNavigation.waitForCommittedUrl(tabId,pageUrl,{signal:navigationSignal,isCurrent})) return;
      await chrome.tabs.reload(tabId);
    }
    if (!isCurrent()) return;
    statusEl.textContent = `Opened page ${Number(match.page)+1}. Exact highlighting inside Chrome’s PDF viewer is unavailable; use the formula excerpt.`;
    if (opts.showInfoPanel) {
      try { await FormulaPdfNavigation.excerpt({backend:BACKEND,url:currentSessionUrl,match,signal:navigationSignal}); }
      catch (error) { if (isCurrent()) statusEl.textContent = `Opened page ${Number(match.page)+1}. PDF excerpt unavailable: ${error.message}`; }
    }
  };
  if (FormulaPdfNavigation.restrictedViewer()) {
    try { await supportedJump(); }
    catch (error) { if (isCurrent()) statusEl.textContent = `Could not open the formula: ${error.message}`; }
    return;
  }

  const attempts = [];
  for (const runner of [
    () => executeNativeJumpViaScripting(tabId, payload, navigation),
    () => executeNativeJumpViaContentBridge(tabId, payload, navigation),
    () => executeNativeJumpViaDebugger(tabId, payload, navigation),
  ]) {
    if (!isCurrent()) return;
    const response = await runner();
    if (!isCurrent()) return;
    attempts.push(response);
    if (response && response.ok) {
      statusEl.textContent = statusForNativeResponse(match, response);
      if (!response.highlighted) {
        console.info('[FormulaSearch] Native instant jump succeeded without exact highlight:', response, attempts);
      }
      // Popup intentionally stays open after a jump so results stay browsable
      // and repeat clicks (e.g. the info-panel arrow) keep working.
      return;
    }
  }

  const bestFailure = attempts.findLast((attempt) => attempt?.code) || attempts.find((attempt) => attempt?.viewerFound) || attempts[0] || { error: 'unknown error' };
  try { await supportedJump(); }
  catch (error) { if (isCurrent()) statusEl.textContent = statusForNativeResponse(match,bestFailure)+` ${error.message}`; }
}


const KATEX_PREVIEW_MACROS = {
  '\\dmodel': 'd_{\\text{model}}',
  '\\dff': 'd_{\\text{ff}}',
  '\\dffn': 'd_{\\text{ff}}',
  '\\ESS': '\\mathrm{ESS}',
  '\\Weight': 'w',
  '\\pathvar': '\\lambda',
  '\\pnorm': '\\pi',
  '\\probIS': 'q',
  '\\dif': 'd',
  '\\mc': '\\mathcal',
  '\\mb': '\\mathbf',
  '\\norm': '\\lVert #1 \\rVert',
  '\\abs': '\\lvert #1 \\rvert',
  '\\del': '(#1)',
  '\\cbr': '\\{#1\\}',
  '\\parenthesis': '(#1)',
  '\\squarebracket': '[#1]',
  '\\curlybracket': '\\{#1\\}',
  '\\absolutevalue': '\\lvert #1 \\rvert',
  '\\doublebar': '\\lVert #1 \\rVert',
  '\\mymacro': '#1',
  '\\defn': '\\textbf{#1}',
  // LaTeX's text box is absent from this KaTeX build; use its supported box
  // primitive so text, spaces and nested math survive without regex unwrapping.
  '\\mbox': '\\hbox{#1}',
};

function sanitizeLatexForPreview(raw) {
  let s = String(raw || '');
  s = s.replace(/\\(?:label|ref|eqref|tag)\s*\{[^{}]*\}/g, ' ');
  s = s.replace(/\\(?:nonumber|notag|xspace)\b/g, '');
  s = s.replace(/\\color\s*\{[^{}]*\}/g, '');

  // Remove unsupported presentation wrappers. Supported text fonts and
  // operator limits carry meaning and must reach KaTeX with their scopes intact.
  for (let i = 0; i < 8; i += 1) {
    const next = s
      .replace(/\\(?:mathcolor|textcolor|mcol)\s*\{[^{}]*\}\s*\{([^{}]*)\}/g, ' $1 ')
      .replace(/\\(?:ensuremath|emph)\s*\{([^{}]*)\}/g, ' $1 ')
      .replace(/\\(?:parenthesis|del)\*?(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g, '($1)')
      .replace(/\\(?:squarebracket)\*?(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g, '[$1]')
      .replace(/\\(?:curlybracket|cbr|set)\*?(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g, '\\{$1\\}')
      .replace(/\\(?:absolutevalue|abs)\*?(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g, '\\lvert $1 \\rvert')
      .replace(/\\(?:doublebar|norm)\*?(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g, '\\lVert $1 \\rVert')
      .replace(/\\(?:bm|boldsymbol|mathbf|mathrm|mathit|mathsf|mathtt|mathbb|mathbbm|mathcal|mathscr|mathfrak)\s*\{([^{}]*)\}/g, (m, inner) => {
        const command = (m.match(/^\\[A-Za-z]+/) || [''])[0].replace('\\mathbbm', '\\mathbb');
        return `${command}{${inner}}`;
      });
    if (next === s) break;
    s = next;
  }
  return s.trim();
}

function renderLatexPreview(raw, target) {
  const latex = sanitizeLatexForPreview(raw);
  const useDisplay = /(?:^|[^\\])(?:\\\\|&)/.test(latex);
  const candidate = useDisplay && !/\\begin\s*\{(?:aligned|split|array|matrix)/.test(latex)
    ? `\\begin{aligned}${latex}\\end{aligned}`
    : latex;
  try {
    katex.render(candidate, target, {
      throwOnError: false,
      displayMode: useDisplay,
      trust: false,
      strict: false,
      // KaTeX writes global definitions into this object. Each formula owns
      // its expansion scope so a query cannot redefine later result previews.
      macros: {...KATEX_PREVIEW_MACROS},
    });
  } catch {
    target.textContent = raw || '';
  }
}

function sourceReference(match) {
  if (!Number.isSafeInteger(match.source_line) || match.source_line < 1 ||
      !['literal', 'macro'].includes(match.source_location_kind)) return '';
  const file = typeof match.source_file === 'string' ? match.source_file : '';
  return `${file ? `${file}:${match.source_line}` : `TeX source, line ${match.source_line}`}${match.source_location_kind === 'macro' ? ' (macro call)' : ''}`;
}

function appendSourceReference(parent, match) {
  const reference = sourceReference(match);
  if (!reference) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'source-reference';
  button.textContent = reference;
  button.title = `Copy TeX source reference: ${reference}. ${match.source_location_kind === 'macro' ? 'Line of the macro invocation that produces this formula.' : 'Line where the formula starts.'}`;
  button.setAttribute('aria-label', `Copy source reference: ${reference}`);
  button.addEventListener('click', async event => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(reference);
      button.textContent = `Copied · ${reference}`;
    } catch {
      button.textContent = `Could not copy · ${reference}`;
    }
    clearTimeout(button._copyRevertTimer);
    button._copyRevertTimer = setTimeout(() => { button.textContent = reference; }, 1600);
  });
  parent.appendChild(button);
}

function appendResultLatexCopy(parent, raw) {
  if (typeof raw !== 'string' || !raw) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'result-latex-copy-btn';
  button.textContent = 'Copy LaTeX';
  button.title = 'Copy the original formula without page or section details';
  FormulaCardCopy.bind(button, {container: parent, text: raw, label: 'LaTeX'});
  parent.appendChild(button);
}

function renderMatch(match, index, {deferPreview = false} = {}) {
  const li = document.createElement('li');
  li.className = `result-item ${match.match_type || ''}`;
  if (match.page === -1) li.classList.add('unmapped');

  const header = document.createElement('div');
  header.className = 'result-header';

  const locationSpan = document.createElement('span');
  locationSpan.className = 'page-label';
  const pageLabel = match.page >= 0
    ? `${['estimated', 'ambiguous'].includes(match.location_status) ? 'Near page' : 'Page'} ${match.page + 1}`
    : 'PDF location unavailable';
  locationSpan.textContent = match.section ? `${pageLabel} · ${match.section}` : pageLabel;
  locationSpan.title = locationSpan.textContent;

  const badge = document.createElement('span');
  badge.className = 'badge';
  badge.textContent = match.match_type || 'match';

  const actions = document.createElement('span');
  actions.className = 'header-actions';

  const infoBtn = document.createElement('button');
  infoBtn.type = 'button';
  infoBtn.className = 'info-arrow-btn';
  infoBtn.title = 'Jump to formula and show info panel';
  infoBtn.setAttribute('aria-label', 'Show formula info');
  infoBtn.textContent = '▸';
  infoBtn.addEventListener('click', async (evt) => {
    evt.stopPropagation();
    // Await the jump before closing: it dispatches chrome.scripting/
    // chrome.debugger calls from this popup's own script, and closing the
    // popup destroys that script's context. Closing too early can abort the
    // in-flight call before it ever reaches the PDF tab.
    await sendScrollToMatch(match, { showInfoPanel: true });
    if (!IS_SIDE_PANEL) { try { window.close(); } catch { /* noop */ } }
  });

  actions.appendChild(badge);
  actions.appendChild(infoBtn);

  const starBtn = document.createElement('button');
  starBtn.type = 'button';
  starBtn.className = 'bookmark-star-btn';
  starBtn.title = 'Bookmark this formula';
  starBtn.textContent = '☆';
  starBtn.setAttribute('aria-pressed', 'false');
  starBtn._bookmarkEntry = bookmarkEntry(match);
  starBtn.addEventListener('click', async (evt) => {
    evt.stopPropagation();
    starBtn.disabled = true;
    try {
      await toggleBookmark(match);
      await refreshBookmarkStars();
    } catch (error) {
      statusEl.textContent = `Could not save the bookmark: ${error.message}`;
    } finally {
      starBtn.disabled = false;
    }
  });
  actions.appendChild(starBtn);

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'result-copy-btn';
  copyBtn.title = 'Copy as markdown';
  copyBtn.setAttribute('aria-label', 'Copy formula and reference as Markdown');
  copyBtn.textContent = '⧉';
  const pageText = match.page >= 0
    ? `${['estimated', 'ambiguous'].includes(match.location_status) ? 'near page' : 'page'} ${match.page + 1}` : 'no page map';
  const reference = sourceReference(match);
  FormulaCardCopy.bind(copyBtn, {container: li, label: 'Markdown', successText: '✓', errorText: '✕',
    text: `$${match.latex || ''}$ — ${pageText}, ${match.section || 'untitled section'}${reference ? ` · ${reference}` : ''}`});
  actions.appendChild(copyBtn);

  header.appendChild(locationSpan);
  header.appendChild(actions);

  const formulaDiv = document.createElement('div');
  formulaDiv.className = 'formula-preview';
  formulaDiv.tabIndex = 0;
  formulaDiv.setAttribute('role', 'button');
  formulaDiv.setAttribute('aria-label', `Open formula on ${pageLabel}: ${match.latex || ''}`);
  formulaDiv.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      formulaDiv.click();
    }
  });
  li.appendChild(header);
  li.appendChild(formulaDiv);
  resultPreviewQueue.add(li, formulaDiv, match.highlighted_latex || match.latex || '', {defer: deferPreview});
  appendResultLatexCopy(li, match.latex);

  const metadata = [];
  if (match.label) metadata.push(match.label);
  if (match.source === 'text' && match.location_method === 'text-math-fragment') metadata.push('PDF text fragment');

  if (match.bbox && match.page >= 0) metadata.push(match.bbox_source === 'query-subspan' ? 'Matched expression located' : 'Formula located');
  if (!match.bbox && match.page >= 0) metadata.push(match.location_status === 'ambiguous'
    ? 'PDF location ambiguous · highlight unavailable'
    : match.location_status === 'estimated' ? 'Section start · formula location not verified' : 'Page only · highlight unavailable');
  if (match.page < 0) metadata.push('Found in source · location not verified');
  if (metadata.length) {
    const labelDiv = document.createElement('div');
    labelDiv.className = 'formula-label';
    labelDiv.textContent = metadata.join(' · ');
    li.appendChild(labelDiv);
  }
  appendSourceReference(li, match);
  const documentContext = documentSession.current;
  const sessionUrl = currentSessionUrl;
  const searchGeneration = _searchGeneration;
  const sourceContext = FormulaSourceContext.create({
    match, url: sessionUrl, revision: _sourceContextRevision, backend: BACKEND,
    signal: _sourceContextAbort.signal,
    isCurrent: () => documentSession.isCurrent(documentContext) &&
      currentSessionUrl === sessionUrl && _searchGeneration === searchGeneration,
  });
  if (sourceContext) li.appendChild(sourceContext);

  li.addEventListener('click', () => {
    if (typeof index === 'number') _stepIndex = index;
    sendScrollToMatch(match);
  });
  return li;
}

const bookmarksView = document.getElementById('bookmarks-view');
const bookmarksList = document.getElementById('bookmarks-list');
const bookmarksToggleBtn = document.getElementById('bookmarks-toggle-btn');
const bookmarkTransfer = FormulaBookmarkTransfer.bind({
  exportButton: document.getElementById('bookmark-export-btn'),
  importButton: document.getElementById('bookmark-import-btn'),
  input: document.getElementById('bookmark-import-file'),
  status: document.getElementById('bookmark-transfer-status'),
  count: document.getElementById('bookmark-count'),
  store: FormulaStoreClient,
});

function formatBookmarkSourceLabel(sourceUrl) {
  if (!sourceUrl) return '';
  const arxivId = extractArxivId(sourceUrl);
  if (arxivId) return `arXiv:${arxivId}`;
  try {
    const parsed = new URL(sourceUrl);
    return parsed.protocol === 'file:' ? decodeMaybe(parsed.pathname.split('/').pop()) : parsed.hostname.replace(/^www\./, '');
  } catch {
    return sourceUrl;
  }
}

function renderBookmarkItem(bm) {
  const li = document.createElement('li');
  li.className = 'result-item';
  const header = document.createElement('div');
  header.className = 'result-header';
  const locationSpan = document.createElement('span');
  locationSpan.className = 'page-label';
  const pageLabel = bm.page >= 0
    ? `${['estimated', 'ambiguous'].includes(bm.location_status) ? 'Near page' : 'Page'} ${bm.page + 1}` : 'No page map';
  locationSpan.textContent = bm.section ? `${pageLabel} · ${bm.section}` : pageLabel;
  const actions = document.createElement('span');
  actions.className = 'header-actions';
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'bookmark-star-btn';
  removeBtn.title = 'Remove bookmark';
  removeBtn.textContent = '★';
  removeBtn.addEventListener('click', (evt) => {
    evt.stopPropagation();
    FormulaStoreClient('bookmarks.remove', {id: bm.id})
      .then(() => renderBookmarksView())
      .catch(error => { statusEl.textContent = `Could not remove the bookmark: ${error.message}`; });
  });
  actions.appendChild(removeBtn);
  header.appendChild(locationSpan);
  header.appendChild(actions);
  const formulaDiv = document.createElement('div');
  formulaDiv.className = 'formula-preview';
  renderLatexPreview(bm.latex || '', formulaDiv);
  li.appendChild(header);
  li.appendChild(formulaDiv);
  appendResultLatexCopy(li, bm.latex);
  // Bookmarks from multiple papers all show in one flat list -- label which
  // paper this one came from (compact, reuses .formula-label) so starring a
  // formula in paper A and later opening paper B doesn't read as ambiguous.
  const sourceLabel = formatBookmarkSourceLabel(bm.source_url);
  const locationNote = bm.location_status === 'ambiguous' ? 'PDF location ambiguous · highlight unavailable'
    : bm.location_status === 'estimated' ? 'Section start · formula location not verified' : '';
  if (sourceLabel || locationNote) {
    const sourceDiv = document.createElement('div');
    sourceDiv.className = 'formula-label';
    sourceDiv.textContent = [sourceLabel, locationNote].filter(Boolean).join(' · ');
    li.appendChild(sourceDiv);
  }
  appendSourceReference(li, bm);
  li.addEventListener('click', () => {
    if (!bm.source_url) {
      statusEl.textContent = 'This older bookmark has no paper URL. Open its paper and search the saved formula again.';
      return;
    }
    sendScrollToMatch({...bm,source:'',match_type:''});
  });
  return li;
}

function renderBookmarksView(items) {
  const revision = ++bookmarkListRevision;
  (items === undefined ? loadBookmarks() : Promise.resolve(items)).then((bookmarks) => {
    if (revision !== bookmarkListRevision) return;
    if (!Array.isArray(bookmarks)) throw new Error('Saved bookmarks have an unexpected format.');
    bookmarksList.innerHTML = '';
    bookmarkTransfer.updateCount(bookmarks.length);
    if (!bookmarks.length) {
      // <li>, not <div> -- this element is appended directly into
      // #bookmarks-list, which is a <ul>; a <div> child there outside any
      // <li> is invalid HTML nesting.
      const empty = document.createElement('li');
      empty.id = 'bookmarks-empty';
      empty.textContent = 'No bookmarks yet — click the star on any result to save it here.';
      bookmarksList.appendChild(empty);
      return;
    }
    bookmarks.forEach((bm) => bookmarksList.appendChild(renderBookmarkItem(bm)));
  }).catch(error => {
    if (revision === bookmarkListRevision) statusEl.textContent = `Could not load bookmarks: ${error.message}`;
  });
}

bookmarksToggleBtn.addEventListener('click', () => {
  const showingBookmarks = bookmarksView.classList.contains('hidden'); // about to flip
  showView(showingBookmarks ? 'bookmarks' : _viewMode);
});

function updateStepControls() {
  const total = _matches.length;
  stepIndexLabel.textContent = total ? `${_stepIndex + 1} / ${total}` : '0 / 0';
  stepUpBtn.disabled = total === 0;
  stepDownBtn.disabled = total === 0;
}

// Single place that decides which of the four views (list / step / chat /
// bookmarks) is visible, and updates every toggle button's active state to match --
// no button is ever disabled to prevent switching; clicking any toggle
// always cleanly switches away from whatever was previously showing.
function showView(view) {
  const showingChat = view === 'chat';
  const showingBookmarks = view === 'bookmarks';
  const stepping = !showingChat && !showingBookmarks && _viewMode === 'step';
  chatView.classList.toggle('hidden', !showingChat);
  chatToggleBtn.classList.toggle('active', showingChat);
  bookmarksView.classList.toggle('hidden', !showingBookmarks);
  bookmarksToggleBtn.classList.toggle('active', showingBookmarks);
  resultsList.classList.toggle('hidden', showingChat || showingBookmarks);
  updateResultFilterControls();
  stepNav.classList.toggle('hidden', !stepping);
  viewToggleBtn.classList.toggle('active', stepping);
  viewToggleBtn.textContent = _viewMode === 'step' ? '☰ List view' : '⇅ Step through';
  viewToggleBtn.title = _viewMode === 'step'
    ? 'Switch back to the scrollable results list'
    : 'Switch to a Ctrl+F-style view: one result at a time, stepped with ▲/▼';
  if (showingChat) {
    updateUnreadBadge();
    refreshChatView();
    loadChatHistory().then((messages) => {
      if (messages.length) markChatSeen(messages[messages.length - 1].timestamp);
      updateUnreadBadge();
    }).catch(() => {});
  }
  if (showingBookmarks) renderBookmarksView();
}

// === EXPORT RESULTS (popup outer) BEGIN ===
function buildExportPayload(matches) {
  return JSON.stringify(matches, null, 2);
}
let _exportRevision = 0;
function requestResultsExport(matches, {filtered = false} = {}) {
  if (!matches.length) return;
  exportStatus.textContent = '';
  const revision = ++_exportRevision;
  const queryState = captureQueryState();
  const generation = _searchGeneration;
  const showFailure = () => {
    if (revision === _exportRevision && generation === _searchGeneration && isQueryStateCurrent(queryState)) {
      exportStatus.textContent = 'Export was not started. Try again.';
    }
  };
  let url;
  try {
    // Serialize now: changing filters while Save As is open must not change the file.
    const blob = new Blob([buildExportPayload(matches)], {type: 'application/json'});
    url = URL.createObjectURL(blob);
    const filename = `formula-search-${filtered ? 'filtered' : 'results'}-${Date.now()}.json`;
    chrome.downloads.download({url, filename, saveAs: true}, () => {
      const error = chrome.runtime.lastError;
      URL.revokeObjectURL(url);
      if (error) showFailure();
    });
  } catch {
    if (url) URL.revokeObjectURL(url);
    showFailure();
  }
}
// === EXPORT RESULTS (popup outer) END ===

const exportBtn = document.getElementById('export-btn');

exportBtn.addEventListener('click', () => requestResultsExport(resultFilter.all));
exportFilteredBtn.addEventListener('click', () => requestResultsExport(_matches, {filtered: true}));

// Renders exactly one result at a time (like Ctrl+F's "n of N"): #results
// holds a single <li>, swapped out on every up/down step instead of a
// scrollable list of every match.
function renderResults() {
  resultPreviewQueue.reset();
  resetSourceContextRequests();
  resultsList.innerHTML = '';
  if (_viewMode === 'step') {
    if (_matches.length) {
      resultsList.appendChild(renderMatch(_matches[_stepIndex], _stepIndex));
    }
    updateStepControls();
    refreshBookmarkStars();
    return;
  }
  _matches.forEach((match, i) => resultsList.appendChild(renderMatch(match, i, {deferPreview: true})));
  updateStepControls();
  refreshBookmarkStars();
}

function stepToIndex(nextIndex) {
  if (!_matches.length) return;
  _stepIndex = ((nextIndex % _matches.length) + _matches.length) % _matches.length;
  renderResults();
  sendScrollToMatch(_matches[_stepIndex]);
}

viewToggleBtn.addEventListener('click', () => {
  _viewMode = _viewMode === 'list' ? 'step' : 'list';
  showView(_viewMode); // never leaves chat or bookmarks showing, regardless of what was active before
  renderResults();
  if (_viewMode === 'step' && _matches.length) sendScrollToMatch(_matches[_stepIndex]);
});
stepUpBtn.addEventListener('click', () => stepToIndex(_stepIndex - 1));
stepDownBtn.addEventListener('click', () => stepToIndex(_stepIndex + 1));

// Chrome discards the whole popup (and all its JS state) every time it
// closes, whether by outside click or window.close(). chrome.storage.session
// survives that -- it's scoped to the browser session, not persisted to
// disk -- so reopening the popup (icon click or Ctrl+Shift+F) can pick the
// query back up instead of starting blank every time.
const QUERY_STORAGE_KEY = 'formulaSearchLastQuery';

function queryStorageArea() {
  if (typeof chrome === 'undefined' || !chrome.storage) return null;
  return chrome.storage.session || chrome.storage.local || null;
}

function saveLastQuery(query) {
  const area = queryStorageArea();
  if (!area) return;
  try { area.set({ [QUERY_STORAGE_KEY]: query }); } catch { /* best effort */ }
}

function loadLastQuery() {
  const area = queryStorageArea();
  if (!area) return Promise.resolve('');
  return new Promise((resolve) => {
    try {
      area.get([QUERY_STORAGE_KEY], (result) => {
        resolve((result && result[QUERY_STORAGE_KEY]) || '');
      });
    } catch {
      resolve('');
    }
  });
}

async function restoreLastQuery() {
  if (queryInput.disabled || _queryRevision !== 0 || queryInput.value) return;
  const queryState = captureQueryState();
  const saved = await loadLastQuery();
  if (typeof saved !== 'string' || !saved || queryInput.disabled || !isQueryStateCurrent(queryState)) return;
  setQueryValue(saved);
  await runSearch();
}

function updateQueryPreview() {
  const preview = document.getElementById('query-preview');
  const query = queryInput.value.trim();
  preview.classList.toggle('hidden', !query);
  if (query) renderLatexPreview(query, preview);
  else preview.textContent = '';
}

const queryHistory = new FormulaQueryHistory({input: queryInput, list: queryHistoryDropdown,
  load: loadSearchHistory, capture: captureQueryState, isCurrent: isQueryStateCurrent,
  choose: query => { setQueryValue(query); runSearch(); }});

function renderQueryHistoryDropdown(history) { return queryHistory.render(history); }

function showQueryHistoryDropdown() {
  return queryHistory.show();
}

function hideQueryHistoryDropdown() {
  queryHistory.hide();
}

queryInput.addEventListener('focus', () => {
  if (_suppressNextFocusDropdown) {
    _suppressNextFocusDropdown = false;
    return;
  }
  if (!queryInput.value.trim()) showQueryHistoryDropdown();
});
queryInput.addEventListener('input', () => {
  _queryRevision += 1;
  updateQueryPreview();
  if (!queryInput.value.trim()) showQueryHistoryDropdown();
  else hideQueryHistoryDropdown();
});
queryInput.addEventListener('blur', () => hideQueryHistoryDropdown());
document.getElementById('search-mode').addEventListener('change', () => { _queryRevision += 1; });

async function runSearch(allowReload = true) {
  _queryRevision += 1;
  hideQueryHistoryDropdown();
  cancelPendingSearch();
  const generation = _searchGeneration;
  const sessionUrl = currentSessionUrl;
  const documentContext = documentSession.current;
  const sourceUrl = _sourceUrl;
  const mode = document.getElementById('search-mode')?.value || 'contains';
  recentPapersEl.classList.add('hidden');
  const query = queryInput.value.trim();
  if (!sessionUrl || queryInput.disabled) return;
  if (!query) {
    clearSearchResults();
    renderResults();
    exportBtn.disabled = true;
    statusEl.textContent = 'Type a formula to search this paper.';
    chrome.runtime.sendMessage({type: 'searchComplete', matchCount: 0, tabId: _activeTab?.id}).catch(() => {});
    updateQueryPreview();
    return;
  }
  updateQueryPreview();
  saveLastQuery(query);
  saveSearchHistoryEntry(query).catch(error => console.warn('[FormulaSearch] Could not save search history:', error));
  hideQueryHistoryDropdown();
  statusEl.textContent = 'Searching...';
  resultsList.innerHTML = '';
  clearSearchResults({keepPage: true});
  updateStepControls();
  exportBtn.disabled = true;
  const controller = new AbortController();
  _searchAbort = controller;
  const isCurrent = () => generation === _searchGeneration && sessionUrl === currentSessionUrl;
  let data;
  try {
    const res = await FormulaRequests.request(`${BACKEND}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, url: sessionUrl, mode }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    data = await res.json();
  } catch (err) {
    if (!isCurrent() || err.name === 'AbortError') return;
    statusEl.textContent = `Error: ${err.message}. Is the backend running?`;
    // A failed search must not leave a PREVIOUS successful search's badge
    // count or exported payload looking current -- clear the same
    // matches/export/badge state the success path below sets for a
    // genuine 0-match result. See Finding I4, final whole-branch review.
    clearSearchResults({keepPage: true});
    exportBtn.disabled = true;
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        chrome.runtime.sendMessage({ type: 'searchComplete', matchCount: 0, tabId: _activeTab?.id }, () => { void chrome.runtime.lastError; });
      } catch { /* best effort */ }
    }
    return;
  } finally {
    if (_searchAbort === controller) _searchAbort = null;
  }

  if (!isCurrent()) return;
  if (data.loaded === false) {
    if (allowReload === false) {
      statusEl.textContent = 'This paper could not stay loaded. Reopen Formula Search to try again.';
      return;
    }
    // An expired/evicted session is different from a valid search with no hits.
    // Reload once, preserving this query/mode, and retain document cancellation.
    if (isArxivUrl(sourceUrl)) await processArxiv(sourceUrl, documentContext);
    else await processTab(sourceUrl, documentContext);
    if (documentSession.isCurrent(documentContext) && currentSessionUrl &&
        queryInput.value.trim() === query && (document.getElementById('search-mode')?.value || 'contains') === mode) {
      await runSearch(false);
    }
    return;
  }
  _matches = resultFilter.set((data.matches || []).map(match => ({...match, source_url: _sourceUrl,
    resolved_source_url: data.resolved_source_url || _sourceUrl})));
  _sourceContextRevision = typeof data.source_context_revision === 'string' ? data.source_context_revision : '';
  FormulaStoreClient('bookmarks.reconcile', {entries: resultFilter.all.map(bookmarkEntry)})
    .then(refreshBookmarkStars).catch(error => console.warn('[FormulaSearch] Bookmark refresh failed:', error));
  updateResultFilterControls();
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
    try {
      chrome.runtime.sendMessage({ type: 'searchComplete', matchCount: resultFilter.all.length, tabId: _activeTab?.id }, () => { void chrome.runtime.lastError; });
    } catch { /* best effort */ }
  }
  _stepIndex = 0;
  statusEl.textContent = resultFilter.all.length
    ? `${resultFilter.all.length} match${resultFilter.all.length > 1 ? 'es' : ''} found — ${_viewMode === 'step' ? 'use ▲/▼ to step through results' : 'select a formula to find it in the paper'}`
    : 'No matches found. Try a shorter expression or Contains mode.';
  if (data.semantic_status && data.semantic_status !== 'complete') {
    statusEl.textContent += ' Algebraic checks are incomplete; notation matches are included.';
  }
  if (Number.isFinite(data.search_ms)) statusEl.textContent += ` (${Math.round(data.search_ms)} ms)`;

  renderResults();
  exportBtn.disabled = resultFilter.all.length === 0;
  if (_viewMode === 'step' && _matches.length) sendScrollToMatch(_matches[_stepIndex]);
}

async function runOcr(pageNumberOneIndexed) {
  const context = documentSession.current;
  const sessionUrl = currentSessionUrl;
  if (!sessionUrl) return;
  const pageNum = parseInt(pageNumberOneIndexed, 10);
  if (!Number.isInteger(pageNum) || pageNum < 1) {
    statusEl.textContent = 'Enter a valid page number to OCR.';
    return;
  }
  ocrRunBtn.disabled = true;
  statusEl.textContent = `Running OCR on page ${pageNum}...`;
  try {
    statusEl.textContent = `Preparing page ${pageNum} preview...`;
    const selection = await FormulaOcrSelection.selectEquation({backend: BACKEND, url: sessionUrl, page: pageNum - 1, signal: context.signal});
    if (!selection || !documentSession.isCurrent(context)) {
      if (documentSession.isCurrent(context)) statusEl.textContent = 'Equation recognition cancelled.';
      return;
    }
    statusEl.textContent = 'Recognizing the selected equation...';
    const res = await FormulaRequests.request(`${BACKEND}/ocr-page`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({page:pageNum-1, url:sessionUrl, region:selection.region,
                            context_revision:selection.context_revision, preview_only:true}),
      signal: context.signal,
    });
    if (!res.ok) {
      // Surfaced verbatim -- the backend's own validation (e.g. "No PDF
      // bytes available...") already covers why OCR isn't possible yet; no
      // client-side pre-check duplicates that logic here.
      const err = await res.json().catch(() => ({ detail: `Server error ${res.status}` }));
      if (documentSession.isCurrent(context)) statusEl.textContent = err.detail;
      return;
    }
    let data = await res.json();
    if (!documentSession.isCurrent(context)) return;
    if (data.proposal_id) {
      let corrected = data.latex, saveError = '';
      const proposal = data.proposal_id;
      for (;;) {
        const latex = await FormulaOcrSelection.reviewEquation(corrected, context.signal, selection.preview_image, saveError);
        if (latex === null || !documentSession.isCurrent(context)) {
          if (documentSession.isCurrent(context)) statusEl.textContent = 'Recognized equation was not added.';
          return;
        }
        corrected = latex;
        try {
          const saved = await FormulaRequests.request(`${BACKEND}/ocr-save`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({url:sessionUrl, proposal_id:proposal, latex}), signal:context.signal,
          });
          const result = await saved.json();
          if (!saved.ok) throw new Error(result.detail || 'Could not save the equation.');
          if (!documentSession.isCurrent(context)) return;
          data = result; break;
        } catch (error) {
          if (!documentSession.isCurrent(context) || error.name === 'AbortError') return;
          saveError = error.message;
        }
      }
    }
    // The newly-OCR'd formulas are indexed on the backend now, but this
    // popup's own _matches/rendered results list is unaffected until the
    // user re-searches -- nudge them to do that, but only when there's
    // actually something new to find.
    const foundSuffix = data.formulas_found > 0 ? ' — search again to include them' : '';
    statusEl.textContent = `OCR'd page ${pageNum} — found ${data.formulas_found} new formula${data.formulas_found === 1 ? '' : 's'}${foundSuffix}`;
  } catch (err) {
    if (documentSession.isCurrent(context) && err.name !== 'AbortError') statusEl.textContent = `OCR failed: ${err.message}`;
  } finally {
    if (documentSession.isCurrent(context)) ocrRunBtn.disabled = false;
  }
}

ocrRunBtn.addEventListener('click', () => runOcr(ocrPageInput.value));
document.getElementById('retry-paper-btn').addEventListener('click', () => {
  if (!_sourceUrl || !documentSession.current) return;
  documentSession.begin();
  if (isArxivUrl(_sourceUrl)) processArxiv(_sourceUrl, documentSession.current);
  else if (isPdfUrl(_sourceUrl)) processTab(_sourceUrl, documentSession.current);
});

stepOcrBtn.addEventListener('click', () => {
  if (!_matches.length || !_matches[_stepIndex]) return;
  const page = _matches[_stepIndex].page;
  if (typeof page !== 'number' || page < 0) return;
  ocrPageInput.value = String(page + 1);
  runOcr(page + 1);
});

async function init(tabPromise, {restoreQuery = false} = {}) {
  const generation = ++_initGeneration;
  if (_sourceUrl) documentSession.remember(documentKey(_sourceUrl), {
    query: queryInput.value, mode: document.getElementById('search-mode').value,
    pageFilter: resultFilter.page,
    sectionFilter: resultFilter.section,
  });
  const context = documentSession.begin();
  resetDocumentSearch();
  statusEl.textContent = 'Finding the active paper…';
  const tab = tabPromise ? await tabPromise : (await chrome.tabs.query({active: true, windowId: _panelWindowId}))[0];
  if (!documentSession.isCurrent(context)) return false;
  _activeTab = tab || null;
  _sourceUrl = sourceUrlFromTabUrl(tab?.url || '');
  if (_pendingSavedNavigation) {
    if (_pendingSavedNavigation.key === documentKey(_sourceUrl)) _pendingSavedNavigation.activated = true;
    else if (_pendingSavedNavigation.activated) _pendingSavedNavigation = null;
  }
  if (tab?.id) chrome.runtime.sendMessage({type: 'searchComplete', matchCount: 0, tabId: tab.id}).catch(() => {});
  const paperContext = document.getElementById('paper-context');
  paperContext.textContent = tab?.title || 'No paper selected';
  paperContext.title = _sourceUrl;
  const saved = documentSession.recall(documentKey(_sourceUrl));
  if (restoreQuery) {
    setQueryValue(saved?.query || '');
    resultFilter.restorePage(saved?.pageFilter);
    resultFilter.restoreSection(saved?.sectionFilter);
    document.getElementById('search-mode').value = saved?.mode || 'contains';
  }
  updateQueryPreview();
  loadRecentPapers().then(papers => {
    if (documentSession.isCurrent(context)) renderRecentPapers(papers);
  }).catch(() => {});
  if (!tab || (!isArxivUrl(_sourceUrl) && !isPdfUrl(_sourceUrl))) {
    statusEl.textContent = IS_SIDE_PANEL
      ? 'Open a PDF or an arXiv paper in this window to search its formulas.'
      : 'Open a PDF or an arXiv paper, then open Formula Search.';
    return false;
  }
  const sourceUrl = _sourceUrl;
  const wantedSource = desiredSourceType(sourceUrl);
  try {
    const res = await FormulaRequests.request(`${BACKEND}/status?url=${encodeURIComponent(sourceUrl)}`, {signal: context.signal}, 10000);
    if (res.ok) {
      const s = await res.json();
      if (!documentSession.isCurrent(context)) return false;
      const sameDocument = s.loaded && documentKey(s.url) === documentKey(sourceUrl);
      if (sameDocument && (s.source_type === wantedSource || (wantedSource === 'text' && s.source_type === 'tex'))) {
        currentSessionUrl = s.url || sourceUrl;
        enableSearch(s.formula_count, 'No formulas indexed for this paper yet.');
        showDocumentWarning(s, sourceUrl, context);
      }
    }
  } catch (err) {
    if (!documentSession.isCurrent(context) || err.name === 'AbortError') return false;
  }
  if (!documentSession.isCurrent(context)) return false;
  if (!currentSessionUrl) {
    if (isArxivUrl(sourceUrl)) await processArxiv(sourceUrl, context);
    else await processTab(sourceUrl, context);
  }
  if (!documentSession.isCurrent(context)) return false;
  const handedOff = await consumePanelHandoff();
  if (!documentSession.isCurrent(context)) return false;
  if (!handedOff && restoreQuery && queryInput.value.trim() && currentSessionUrl) await runSearch();
  if (documentSession.isCurrent(context) && currentSessionUrl &&
      _pendingSavedNavigation?.key === documentKey(_sourceUrl)) {
    const request = _pendingSavedNavigation;
    _pendingSavedNavigation = null;
    await sendScrollToMatch(request.match, request.opts);
  }
  return generation === _initGeneration && !!currentSessionUrl;
}

function watchPanelTabs() {
  if (!IS_SIDE_PANEL) return;
  const activate = tab => init(tab, {restoreQuery: true}).catch(err => {
    console.warn('[FormulaSearch] Could not activate tab:', err);
  });
  chrome.tabs.onActivated.addListener(info => {
    // A previous tabs.get may still be pending. Even returning immediately to
    // _activeTab must supersede that in-flight activation.
    if (info.windowId === _panelWindowId) activate(chrome.tabs.get(info.tabId));
  });
  chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
    if (tab.windowId !== _panelWindowId || !tab.active) return;
    const changedDocument = change.url && documentKey(sourceUrlFromTabUrl(change.url)) !== documentKey(_sourceUrl);
    if (changedDocument) activate(tab);
    else if (tabId === _activeTab?.id && change.title) document.getElementById('paper-context').textContent = change.title;
  });
}

async function consumePanelHandoff() {
  if (!IS_SIDE_PANEL || !currentSessionUrl || queryInput.disabled) return false;
  const queryState = captureQueryState();
  const context = queryState.context;
  const key = `fs_panel_handoff_${_panelWindowId}`;
  const stored = await chrome.storage.session.get(key);
  const handoff = stored[key];
  if (!isQueryStateCurrent(queryState) || !handoff || handoff.id === _lastHandoffId ||
      Date.now() - handoff.ts > 60000 || handoff.document !== documentKey(_sourceUrl)) return false;
  _lastHandoffId = handoff.id;
  _handoffAppliedTo = context;
  await chrome.storage.session.remove(key);
  // Apply query, mode and both filters together after the asynchronous consume.
  // A gesture during remove must not leave half of an obsolete handoff visible.
  if (!isQueryStateCurrent(queryState)) return true;
  setQueryValue(handoff.query || '');
  resultFilter.restorePage(handoff.pageFilter);
  resultFilter.restoreSection(handoff.sectionFilter);
  document.getElementById('search-mode').value = ['contains', 'exact', 'algebraic'].includes(handoff.mode) ? handoff.mode : 'contains';
  showView(_viewMode);
  await runSearch();
  return true;
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes[BOOKMARKS_STORAGE_KEY]) {
    const items = changes[BOOKMARKS_STORAGE_KEY].newValue ?? [];
    refreshBookmarkStars(items);
    if (!bookmarksView.classList.contains('hidden')) renderBookmarksView(items);
  }
  if (area === 'session' && changes[`fs_panel_handoff_${_panelWindowId}`]?.newValue) {
    consumePanelHandoff().catch(err => console.warn('[FormulaSearch] Could not restore the popup query:', err));
  }
});

document.getElementById('open-panel-btn').addEventListener('click', () => {
  // Call directly in the click handler: Chrome requires the user gesture.
  if (!chrome.sidePanel || !_activeTab) return;
  const key = `fs_panel_handoff_${_activeTab.windowId}`;
  const handoff = chrome.storage.session.set({[key]: {
    id: crypto.randomUUID(), ts: Date.now(), document: documentKey(_sourceUrl),
    query: queryInput.value, mode: document.getElementById('search-mode').value,
    pageFilter: resultFilter.page,
    sectionFilter: resultFilter.section,
  }});
  const opened = chrome.sidePanel.open({windowId: _activeTab.windowId});
  Promise.all([handoff, opened]).then(() => {
    if (!IS_SIDE_PANEL) window.close();
  }).catch(err => { statusEl.textContent = `Could not open the side panel: ${err.message}`; });
});

// === MESSAGE ROUTER (popup outer) BEGIN ===
function handleIncomingMessage(message) {
  if (!message) return;
  if (message.type === 'OCR_COMPLETE' && queryInput.value.trim()) {
    runSearch();
  } else if (message.type === 'contextMenuQuery' && message.text) {
    consumePendingQuery();
  } else if (message.type === 'stepCommand') {
    if (message.direction === 'next') stepToIndex(_stepIndex + 1);
    else if (message.direction === 'prev') stepToIndex(_stepIndex - 1);
  } else if (message.type === 'toggleChatCommand') {
    const showingChat = chatView.classList.contains('hidden'); // about to flip
    showView(showingChat ? 'chat' : _viewMode);
    // The service worker always writes fs_pending_toggle_chat before this
    // live relay attempt (see service-worker.js), since it can't know in
    // advance whether a listener is already here to receive it -- when one
    // is (this branch running proves that), the flag must still be cleared
    // here too, independently of consumePendingChatToggle() (which only
    // clears it at init), or it goes stale: it would otherwise wrongly
    // force chat open on some later, completely unrelated popup open. Same
    // pairing consumePendingQuery() gets "for free" by being the one
    // function both the live-relay and init paths share; toggleChatCommand
    // has no shared function to inherit that from since its live-relay
    // behavior (toggle) and init-time behavior (unconditional show) are
    // deliberately different, so the clear has to be duplicated here
    // instead. Best-effort, fire-and-forget -- matches this file's existing
    // style for this kind of secondary cleanup (see saveLastQuery, e.g.).
    if (typeof chrome !== 'undefined' && chrome.storage) {
      chrome.storage.local.set({ fs_pending_toggle_chat: false }).catch(() => {});
    }
  }
}
// === MESSAGE ROUTER (popup outer) END ===

if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((message) => handleIncomingMessage(message));
}

// === CHAT STORE (popup outer) BEGIN ===
// Same chrome.storage.local key/shape as the info-card chat widgets
// injected into the PDF page (popup.js's nativePdfInstantJump,
// content.js) -- one unified, chronological conversation, not a separate
// thread per surface. See
// docs/superpowers/specs/2026-08-09-unified-chat-architecture-design.md.
const CHAT_STORAGE_KEY = 'fs_chat_history';
// Literal constant, not read from the button's current textContent at
// click time: on a rapid double-click within the revert window, capturing
// "current" text after the first click's write already resolved would
// capture '✓'/'✕' as the "original" to revert to, corrupting the second
// click's eventual revert.
const CHAT_COPY_GLYPH = '⧉';

const chatToggleBtn = document.getElementById('chat-toggle-btn');
const chatView = document.getElementById('chat-view');
const chatMessagesEl = document.getElementById('chat-messages');
const chatActions = document.getElementById('chat-actions');
const chatClearBtn = document.getElementById('chat-clear-btn');
const chatInputEl = document.getElementById('chat-input');
const chatSendBtn = document.getElementById('chat-send-btn');
const chatUnreadBadge = document.getElementById('chat-unread-badge');

function loadChatHistory() {
  return FormulaStoreClient('notes.list');
}

function appendChatMessage(text, context) {
  return FormulaStoreClient('notes.append', {text, context: context || null});
}

function clearChatHistory() {
  return FormulaStoreClient('notes.clear');
}

const CHAT_LAST_SEEN_KEY = 'fs_chat_last_seen';

function loadLastSeen() {
  return FormulaStoreClient('seen.read');
}

function markChatSeen(ts) {
  if (typeof chrome === 'undefined' || !chrome.storage) return;
  FormulaStoreClient('seen.advance', {timestamp: ts}).catch(() => {});
}

// Bumped by every updateUnreadBadge() call so an earlier in-flight (hidden
// path) computation can tell it's been superseded and self-cancel, even if
// it doesn't resolve until *after* a later call already changed things
// (e.g. close -> message arrives -> async fetch starts -> reopen (marks
// seen, settles badge to 0) -> close again, all before that fetch
// resolves). A plain "is the view hidden right now" check at resolve time
// only catches "resolved after a reopen, still open" -- it misses this
// closed-reopened-closed-again race, where the view is hidden again by the
// time the stale computation finishes but the lastSeen it read is stale.
let unreadBadgeGeneration = 0;

function updateUnreadBadge() {
  unreadBadgeGeneration++;
  // While the view is open the count is always 0 -- settle that
  // synchronously rather than via the async fetch below.
  if (!chatView.classList.contains('hidden')) {
    chatUnreadBadge.textContent = '0';
    chatUnreadBadge.classList.add('hidden');
    return;
  }
  const myGeneration = unreadBadgeGeneration;
  return Promise.all([loadChatHistory(), loadLastSeen()]).then(([messages, lastSeen]) => {
    // A newer call (from a subsequent open/close/onChanged) has since
    // started -- it either already settled the badge or has its own
    // fresher fetch in flight. Either way, this stale result must not
    // overwrite whatever's current.
    if (myGeneration !== unreadBadgeGeneration) return;
    const unread = messages.filter((m) => m.timestamp > lastSeen).length;
    chatUnreadBadge.textContent = String(unread);
    chatUnreadBadge.classList.toggle('hidden', unread === 0);
  }).catch(() => {});
}

const CHAT_DRAFT_KEY = 'fs_chat_draft';
// In-memory shadow of the last known draft, alongside the storage-backed
// copy -- saveDraft's write is fire-and-forget (matches every call site,
// which saves on every keystroke and can't block on a round-trip), so a
// write that rejects (e.g. extension context invalidated mid-send) would
// otherwise leave a later loadDraft() reading stale/empty storage even
// though the caller already re-typed the text into cachedDraft's source of
// truth.
//
// That protection is currently INERT for this copy in real usage, though:
// Chrome tears down this whole popup script when the popup closes, so
// cachedDraft resets to null on every popup open, and initChatView() calls
// loadDraft() exactly once per popup session, before the user has typed
// anything. There is no second read within one session left to ever
// observe a cache hit. Kept anyway for consistency with the other two
// copies of this code and because it's cheap and correct -- it would start
// protecting drafts here for real the moment any future code adds a second
// loadDraft() call site within one popup session.
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

// The popup is the one surface a conversation can be read from without ever
// having opened an info card on the formula that started it, so each
// bubble gets a small provenance line when the message carries one -- the
// info cards themselves skip this since you're already looking at that
// formula.
function renderChatMessages(messages) {
  chatMessagesEl.innerHTML = '';
  for (const m of messages) {
    const bubble = document.createElement('div');
    bubble.className = `chat-msg chat-msg-${m.role}`;
    if (m.context && m.context.latex) {
      const ctx = document.createElement('button');
      ctx.type = 'button';
      ctx.className = 'chat-msg-context';
      ctx.textContent = `re: ${m.context.latex}`;
      if (m.context.source_url && Number.isInteger(m.context.page) && m.context.page >= 0) {
        ctx.title = 'Jump to this formula';
        ctx.addEventListener('click', () => sendScrollToMatch({
          page: m.context.page,
          bbox: m.context.bbox || null,
          page_w: m.context.page_w || 0,
          page_h: m.context.page_h || 0,
          latex: m.context.latex,
          source: m.context.source || '',
          source_url: m.context.source_url,
          match_type: m.context.match_type || '',
          bbox_source: m.context.bbox_source || '',
          location_status: m.context.location_status || '',
        }));
      } else {
        ctx.disabled = true;
      }
      bubble.appendChild(ctx);
    }
    const text = document.createElement('div');
    text.className = 'chat-msg-text';
    text.textContent = m.text;
    bubble.appendChild(text);
    const time = document.createElement('div');
    time.className = 'chat-msg-time';
    time.textContent = formatRelativeTime(m.timestamp);
    bubble.appendChild(time);
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'chat-msg-copy';
    copyBtn.setAttribute('aria-label', 'Copy message');
    copyBtn.textContent = CHAT_COPY_GLYPH;
    copyBtn.addEventListener('click', () => {
      if (typeof navigator === 'undefined' || !navigator.clipboard) return;
      // Cancel a pending revert from a rapid re-click, same pattern the
      // old whole-thread copyChatThread() used -- but per-button (kept on
      // the element itself via _copyRevertTimer) since there's one button
      // per message here, not a single shared control.
      if (copyBtn._copyRevertTimer) {
        clearTimeout(copyBtn._copyRevertTimer);
        copyBtn._copyRevertTimer = null;
      }
      navigator.clipboard.writeText(m.text).then(() => {
        copyBtn.textContent = '✓';
      }, () => {
        // writeText genuinely rejects under normal conditions (document not
        // focused, permissions policy) -- show that instead of leaving the
        // button silently unchanged with no sign the copy failed.
        copyBtn.textContent = '✕';
      }).then(() => {
        copyBtn._copyRevertTimer = setTimeout(() => {
          copyBtn.textContent = CHAT_COPY_GLYPH;
          copyBtn._copyRevertTimer = null;
        }, 1200);
      });
    });
    bubble.appendChild(copyBtn);
    chatMessagesEl.appendChild(bubble);
  }
  chatActions.classList.toggle('hidden', messages.length === 0);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function formatRelativeTime(ts) {
  const diff = Date.now() - ts;
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function refreshChatView() {
  loadChatHistory().then(renderChatMessages).catch(error => {
    const status = document.getElementById('note-save-status');
    if (status) status.textContent = `Could not load notes: ${error.message}`;
  });
}

// Returns its promise (a click handler's return value is otherwise
// ignored) so tests can await the write+render, or the catch's restore,
// directly instead of racing an independent read against it.
function sendPopupChatMessage() {
  const text = chatInputEl.value.trim();
  if (!text) return;
  chatInputEl.value = '';
  const saveStatus = document.getElementById('note-save-status');
  if (saveStatus) saveStatus.textContent = '';
  chatSendBtn.disabled = true;
  return appendChatMessage(text, null).then((messages) => {
    renderChatMessages(messages);
    if (!chatInputEl.value.trim()) clearDraft().catch(() => {});
  }).catch(error => {
    // appendChatMessage's own set() call can still reject (e.g. the
    // extension context was invalidated by a reload/update mid-write)
    // even past its !chrome.storage guard -- restore what the user typed
    // instead of silently losing it. Re-save the draft too, since the
    // restore doesn't fire a synthetic 'input' event.
    chatInputEl.value = chatInputEl.value ? `${text}\n\n${chatInputEl.value}` : text;
    if (saveStatus) saveStatus.textContent = `Not saved: ${error.message}`;
    chatSendBtn.disabled = false;
    saveDraft(chatInputEl.value);
  });
}

function initChatView() {
  chatToggleBtn.addEventListener('click', () => {
    const showingChat = chatView.classList.contains('hidden'); // about to flip
    showView(showingChat ? 'chat' : _viewMode);
  });
  let draftSaveTimer = null;
  chatInputEl.addEventListener('input', () => {
    chatSendBtn.disabled = !chatInputEl.value.trim();
    if (draftSaveTimer) clearTimeout(draftSaveTimer);
    draftSaveTimer = setTimeout(() => { saveDraft(chatInputEl.value); }, 300);
  });
  chatInputEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendPopupChatMessage();
    }
  });
  chatSendBtn.addEventListener('click', sendPopupChatMessage);
  chatClearBtn.addEventListener('click', () => {
    clearChatHistory().catch(error => {
      const status = document.getElementById('note-save-status');
      if (status) status.textContent = `Could not clear notes: ${error.message}`;
    });
  });
  if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes[CHAT_STORAGE_KEY]) {
        if (!chatView.classList.contains('hidden')) {
          const messages = changes[CHAT_STORAGE_KEY].newValue || [];
          renderChatMessages(messages);
          // The view is open and this message just rendered live -- it's
          // seen the moment it's on screen, so advance last-seen now rather
          // than waiting for a close+reopen cycle (which would otherwise
          // wrongly count everything seen-while-open as unread later).
          if (messages.length) markChatSeen(messages[messages.length - 1].timestamp);
        } else {
          updateUnreadBadge();
        }
      }
      if (changes[CHAT_DRAFT_KEY]) {
        // Keeps the in-memory draft shadow (see cachedDraft above) from
        // shadowing a newer value written elsewhere -- same caveat as
        // cachedDraft itself applies here: since this popup session never
        // calls loadDraft() a second time, there is nothing left in THIS
        // session to actually read cachedDraft again after this update.
        // Harmless, and here for the same forward-compatibility reason.
        cachedDraft = changes[CHAT_DRAFT_KEY].newValue || '';
      }
    });
  }
  loadDraft().then((text) => {
    if (!text) return;
    chatInputEl.value = text;
    chatSendBtn.disabled = !text.trim();
  }).catch(() => {});
  updateUnreadBadge();
}
// === CHAT STORE (popup outer) END ===

searchBtn.addEventListener('click', runSearch);
queryInput.addEventListener('keydown', (event) => {
  if (queryHistory.handleKeydown(event)) return;
  if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) runSearch();
});

// Popup-local fallback for next-result/prev-result (manifest.json's
// suggested_key: Alt+Down / Alt+Up). chrome.commands.onCommand's delivery to
// a real, focused popup bubble (as opposed to popup.html opened as a plain
// tab, which is all Task 6's live verification could actually exercise) is
// unverified and, per that task's live run, can be unreliable once the
// browser session has done enough else. This listens for the same key combo
// directly within the popup's own script and calls stepToIndex() straight
// away -- no service-worker round trip, no chrome.commands dependency -- so
// next/prev work by construction whenever the popup itself has keyboard
// focus. The chrome.commands path (service-worker.js -> stepCommand message
// -> handleIncomingMessage) remains as a bonus/fallback for configurations
// where this listener isn't attached (e.g. a devtools-detached popup tab
// that lost focus). Deliberately NOT extended to toggle-chat: that command's
// openPopup()-based cross-context behavior is different in kind (it must be
// able to open a popup that isn't open yet) and out of scope here.
document.addEventListener('keydown', (e) => {
  if (!e.altKey || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
  // Scope this the same way the existing Enter-key handler above is scoped
  // to queryInput specifically: skip while a text-entry element has focus so
  // this never competes with normal typing/editing.
  const target = e.target;
  const isTextEntry = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
  if (isTextEntry) return;
  e.preventDefault();
  stepToIndex(_stepIndex + (e.key === 'ArrowDown' ? 1 : -1));
});

updateStepControls();
initChatView();

loadSettings()
  .then(async (settings) => {
    BACKEND = settings.backendUrl || DEFAULT_SETTINGS.backendUrl;
    applyTheme(settings.theme);
    if (settings.defaultView === 'chat') {
      showView('chat');
    } else if (settings.defaultView === 'bookmarks') {
      showView('bookmarks');
    } else {
      _viewMode = settings.defaultView === 'step' ? 'step' : 'list';
      showView(_viewMode);
    }
    _panelWindowId = (await chrome.windows.getCurrent()).id;
    watchPanelTabs();
    return init();
  })
  .then(() => consumePendingQuery({startup: true}))
  .then((handled) => { if (!handled && _handoffAppliedTo !== documentSession.current) return restoreLastQuery(); })
  // Applied last and independent of the query/search outcome above: a
  // pending chat-toggle only decides which view ends up showing, it never
  // suppresses a pending query's search (that still runs either way), so
  // it can't contradict consumePendingQuery/restoreLastQuery -- it just
  // has the final say on the view once whichever of those has settled.
  .then(() => consumePendingChatToggle())
  .catch(err => { statusEl.textContent = `Unexpected error: ${err.message}`; });
