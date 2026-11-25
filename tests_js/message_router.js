globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

// Regression test for the toggle-chat "stale flag" bug (fix round 2): the
// service worker always writes fs_pending_toggle_chat=true before its
// best-effort live relay, since it can't know in advance whether a popup is
// already open to receive the message. When one IS open (this branch of
// handleIncomingMessage running proves that), the flag must be cleared here
// too -- otherwise it survives to a later, completely unrelated popup open
// and consumePendingChatToggle() wrongly forces that open onto the chat
// view.
chrome.storage.local.set({ fs_pending_toggle_chat: true }).then(() => {
  handleIncomingMessage({ type: 'toggleChatCommand' });
  // The storage clear inside the branch is fire-and-forget (not awaited by
  // handleIncomingMessage itself), so give it a settle tick before reading
  // it back.
  return new Promise((resolve) => setTimeout(resolve, 0));
}).then(() => {
  assert(globalThis.__showViewCalls[globalThis.__showViewCalls.length - 1] === 'chat', 'toggleChatCommand on an already-open popup with chat hidden switches to chat, got ' + JSON.stringify(globalThis.__showViewCalls));
  return chrome.storage.local.get('fs_pending_toggle_chat');
}).then((result) => {
  assert(!result.fs_pending_toggle_chat, 'handling toggleChatCommand live also clears fs_pending_toggle_chat, so it cannot go stale into a later unrelated popup open, got ' + JSON.stringify(result.fs_pending_toggle_chat));

  // Dispatch again: chat is now showing (previous call switched to it), so
  // this one should toggle back away from chat, and still leave the flag
  // clear (it already was, but the clear statement runs unconditionally on
  // every dispatch of this branch -- confirm it doesn't misbehave the
  // second time).
  return chrome.storage.local.set({ fs_pending_toggle_chat: true });
}).then(() => {
  handleIncomingMessage({ type: 'toggleChatCommand' });
  return new Promise((resolve) => setTimeout(resolve, 0));
}).then(() => {
  assert(globalThis.__showViewCalls[globalThis.__showViewCalls.length - 1] === 'list', 'toggleChatCommand on an already-open popup with chat showing toggles back to _viewMode, got ' + JSON.stringify(globalThis.__showViewCalls));
  return chrome.storage.local.get('fs_pending_toggle_chat');
}).then((result) => {
  assert(!result.fs_pending_toggle_chat, 'the flag stays cleared on a second toggleChatCommand dispatch, got ' + JSON.stringify(result.fs_pending_toggle_chat));

  // Finding M1 (final whole-branch review): this file previously only
  // exercised the toggleChatCommand branch of handleIncomingMessage() --
  // OCR_COMPLETE, contextMenuQuery, and stepCommand went unasserted here
  // (tests_js/service_worker.js only proves the *worker side* sends
  // stepCommand, not that the popup router handles it). The
  // __runSearchCalls/__consumePendingQueryCalls/__stepToIndexCalls spies in
  // fixtures/message_router_dom.js already existed for exactly this
  // purpose but were unused; wiring them up here.

  // OCR_COMPLETE only re-runs the search when the query box actually holds
  // something to search for.
  queryInput.value = '';
  const runSearchCallsBefore1 = globalThis.__runSearchCalls;
  handleIncomingMessage({ type: 'OCR_COMPLETE' });
  assert(globalThis.__runSearchCalls === runSearchCallsBefore1, 'OCR_COMPLETE with an empty query box does not trigger a search, got ' + globalThis.__runSearchCalls + ' vs before ' + runSearchCallsBefore1);

  queryInput.value = '\\alpha';
  const runSearchCallsBefore2 = globalThis.__runSearchCalls;
  handleIncomingMessage({ type: 'OCR_COMPLETE' });
  assert(globalThis.__runSearchCalls === runSearchCallsBefore2 + 1, 'OCR_COMPLETE with a non-empty query box triggers runSearch(), got ' + globalThis.__runSearchCalls + ' vs before ' + runSearchCallsBefore2);

  // contextMenuQuery only hands off to consumePendingQuery() when it
  // actually carries text (mirrors the real service worker, which never
  // sends this message without info.selectionText).
  const consumeCallsBefore1 = globalThis.__consumePendingQueryCalls;
  handleIncomingMessage({ type: 'contextMenuQuery', text: '' });
  assert(globalThis.__consumePendingQueryCalls === consumeCallsBefore1, 'contextMenuQuery without text does not call consumePendingQuery(), got ' + globalThis.__consumePendingQueryCalls + ' vs before ' + consumeCallsBefore1);

  const consumeCallsBefore2 = globalThis.__consumePendingQueryCalls;
  handleIncomingMessage({ type: 'contextMenuQuery', text: '\\beta' });
  assert(globalThis.__consumePendingQueryCalls === consumeCallsBefore2 + 1, 'contextMenuQuery with text calls consumePendingQuery(), got ' + globalThis.__consumePendingQueryCalls + ' vs before ' + consumeCallsBefore2);

  // stepCommand steps in the direction it names, relative to the current
  // _stepIndex (both branches, since B4's keyboard shortcuts rely on both).
  handleIncomingMessage({ type: 'stepCommand', direction: 'next' });
  assert(globalThis.__stepToIndexCalls[globalThis.__stepToIndexCalls.length - 1] === _stepIndex + 1, 'stepCommand/next calls stepToIndex(_stepIndex + 1), got ' + JSON.stringify(globalThis.__stepToIndexCalls));

  handleIncomingMessage({ type: 'stepCommand', direction: 'prev' });
  assert(globalThis.__stepToIndexCalls[globalThis.__stepToIndexCalls.length - 1] === _stepIndex - 1, 'stepCommand/prev calls stepToIndex(_stepIndex - 1), got ' + JSON.stringify(globalThis.__stepToIndexCalls));

  const stepCallsBefore = globalThis.__stepToIndexCalls.length;
  handleIncomingMessage({ type: 'stepCommand', direction: 'sideways' });
  assert(globalThis.__stepToIndexCalls.length === stepCallsBefore, 'an unrecognized stepCommand direction calls neither branch, got ' + JSON.stringify(globalThis.__stepToIndexCalls));
});
