// Stands in for popup.html's elements the popup-outer chat block's
// top-level getElementById calls need to find -- this harness only ever
// evaluates plain JS, it never parses popup.html itself.
function mkChatEl(tag, id) {
  const el = document.createElement(tag);
  el.id = id;
  document.body.appendChild(el);
  return el;
}
mkChatEl('button', 'chat-toggle-btn');
mkChatEl('span', 'chat-unread-badge').className = 'hidden';
mkChatEl('div', 'chat-view').className = 'hidden';
mkChatEl('div', 'chat-messages');
mkChatEl('div', 'chat-actions').className = 'hidden';
mkChatEl('button', 'chat-clear-btn');
mkChatEl('textarea', 'chat-input');
mkChatEl('button', 'chat-send-btn');

// Stand-ins for popup.js's own list/step-view globals (defined near the top
// of the real file, out of scope here -- only the new chat block's own
// behavior is under test).
const resultsList = mkChatEl('ul', 'results');
const stepNav = mkChatEl('div', 'step-nav');
const viewToggleBtn = mkChatEl('button', 'view-toggle-btn');
var _viewMode = 'list';

// Stand-in for popup.js's real showView() (also defined outside the CHAT
// STORE (popup outer) block this harness extracts) -- mirrors its actual
// list/step/chat DOM toggling exactly, since the block's chatToggleBtn
// handler now calls showView() instead of manipulating chatView/resultsList
// itself. refreshChatView/loadChatHistory/markChatSeen/updateUnreadBadge
// referenced below ARE defined inside the block, loaded right after this
// fixture and before the test script runs, so they exist by call time.
function showView(view) {
  const showingChat = view === 'chat';
  const stepping = !showingChat && _viewMode === 'step';
  chatView.classList.toggle('hidden', !showingChat);
  chatToggleBtn.classList.toggle('active', showingChat);
  resultsList.classList.toggle('hidden', showingChat);
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
}

// Stand-in for popup.js's real viewToggleBtn click handler (also outside the
// extracted block) -- needed because the new regression test clicks this
// button to verify it can switch away from an open chat view.
viewToggleBtn.addEventListener('click', () => {
  _viewMode = _viewMode === 'list' ? 'step' : 'list';
  showView(_viewMode);
});

// Stub for popup.js's real sendScrollToMatch (defined outside the CHAT
// STORE (popup outer) block this harness extracts, so it's never loaded
// here) -- click-to-jump tests need it callable and its argument
// inspectable without pulling in the real chrome.tabs/scripting machinery
// sendScrollToMatch itself uses.
globalThis.__sendScrollToMatchCalls = [];
function sendScrollToMatch(match) {
  globalThis.__sendScrollToMatchCalls.push(match);
  return Promise.resolve();
}
