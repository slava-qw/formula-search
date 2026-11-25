function mkPendingQueryEl(tag, id) {
  const el = document.createElement(tag);
  el.id = id;
  document.body.appendChild(el);
  return el;
}
const queryInput = mkPendingQueryEl('input', 'query');
queryInput.value = '';
// Document activation stub; the Node race tests also exercise the real class.
const documentSession = {current: {}, isCurrent(context) { return context === this.current; }};
const resultFilter = {restorePage() {}, restoreSection() {}};

// Stand-in for popup.js's real runSearch (defined outside the extracted
// block) -- records calls so the test can assert a search was triggered
// without pulling in fetch/chrome.tabs machinery.
globalThis.__runSearchCalls = 0;
function runSearch() { globalThis.__runSearchCalls += 1; return Promise.resolve(); }

// Stand-in for popup.js's real showView (also defined outside the extracted
// block -- it drives chatView/bookmarksView/etc. DOM this fixture doesn't
// have) -- records the view names it was called with so
// consumePendingChatToggle's test can assert it lands on 'chat' without
// pulling in the rest of the popup DOM.
globalThis.__showViewCalls = [];
function showView(view) { globalThis.__showViewCalls.push(view); }

// Stand-in for popup.js's real _viewMode (also defined outside the
// extracted block) -- consumePendingQuery() now reads this to switch back
// to the "current" list/step view (never chat/bookmarks) before running a
// genuinely pending query.
let _viewMode = 'list';
