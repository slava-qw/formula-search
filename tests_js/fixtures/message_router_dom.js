// Minimal stand-ins for handleIncomingMessage()'s free variables (all
// defined elsewhere in the real popup.js, outside the extracted
// MESSAGE ROUTER (popup outer) block) -- just enough to drive and observe
// each branch without pulling in the rest of the 3000+ line outer script
// and its full DOM.
function mkMessageRouterEl(tag, id) {
  const el = document.createElement(tag);
  el.id = id;
  document.body.appendChild(el);
  return el;
}

const queryInput = mkMessageRouterEl('input', 'query');
queryInput.value = '';

// Starts hidden, like a freshly-opened popup's chat view.
const chatView = mkMessageRouterEl('div', 'chat-view');
chatView.classList.add('hidden');

globalThis.__runSearchCalls = 0;
function runSearch() { globalThis.__runSearchCalls += 1; return Promise.resolve(); }

globalThis.__consumePendingQueryCalls = 0;
function consumePendingQuery() { globalThis.__consumePendingQueryCalls += 1; return Promise.resolve(false); }

globalThis.__stepToIndexCalls = [];
let _stepIndex = 0;
function stepToIndex(i) { globalThis.__stepToIndexCalls.push(i); }

globalThis.__showViewCalls = [];
let _viewMode = 'list';
function showView(view) {
  globalThis.__showViewCalls.push(view);
  // Mirror the real showView()'s one relevant side effect (toggling
  // chatView's hidden class) so a second toggleChatCommand dispatch in the
  // same test sees a consistent showingChat flip, same as the real popup.
  chatView.classList.toggle('hidden', view !== 'chat');
}
