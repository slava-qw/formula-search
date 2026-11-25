globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

consumePendingQuery().then((handled) => {
  assert(handled === false, 'consumePendingQuery resolves false when nothing is pending, got ' + handled);
  assert(globalThis.__runSearchCalls === 0, 'no search is triggered when nothing is pending');

  return chrome.storage.local.set({ fs_pending_query: '\\gamma' });
}).then(() => consumePendingQuery())
  .then((handled) => {
    assert(handled === true, 'consumePendingQuery resolves true when a query is pending, got ' + handled);
    assert(queryInput.value === '\\gamma', 'the pending query fills the input, got ' + queryInput.value);
    assert(globalThis.__runSearchCalls === 1, 'a search is triggered exactly once, got ' + globalThis.__runSearchCalls);
    assert(globalThis.__showViewCalls[globalThis.__showViewCalls.length - 1] === 'list', 'a genuinely pending query switches back to the current (list) view so it is visible even if the default view is chat/bookmarks, got ' + JSON.stringify(globalThis.__showViewCalls));

    return chrome.storage.local.get('fs_pending_query');
  }).then((result) => {
    assert(!result.fs_pending_query, 'the pending query is cleared from storage after being consumed, got ' + JSON.stringify(result.fs_pending_query));
    return consumePendingQuery();
  }).then((handled) => {
    assert(handled === false, 'consuming a second time (already cleared) resolves false, got ' + handled);
    assert(globalThis.__runSearchCalls === 1, 'a second consume does not trigger a second search, got ' + globalThis.__runSearchCalls);

    // Finding I1 (final whole-branch review): a context-menu search fired
    // on a page where search never became available this load (e.g. a
    // non-PDF page -- init() disables #query/#search-btn) must not run
    // runSearch() against whatever document the backend happens to still
    // hold from an earlier session. queryInput.disabled mirrors that state.
    queryInput.disabled = true;
    const runSearchCallsBefore = globalThis.__runSearchCalls;
    const showViewCallsBefore = globalThis.__showViewCalls.length;
    return chrome.storage.local.set({ fs_pending_query: '\\delta' }).then(() => ({ runSearchCallsBefore, showViewCallsBefore }));
  }).then(({ runSearchCallsBefore, showViewCallsBefore }) => {
    return consumePendingQuery().then((handled) => ({ handled, runSearchCallsBefore, showViewCallsBefore }));
  }).then(({ handled, runSearchCallsBefore, showViewCallsBefore }) => {
    assert(handled === true, 'consumePendingQuery still resolves true when search is disabled, so the caller does not also fall back to restoreLastQuery() and clobber the input, got ' + handled);
    assert(queryInput.value === '\\delta', 'the input is still filled even when search is disabled, got ' + queryInput.value);
    assert(globalThis.__runSearchCalls === runSearchCallsBefore, 'runSearch is NOT triggered when search never became available this load, got ' + globalThis.__runSearchCalls + ' vs before ' + runSearchCallsBefore);
    assert(globalThis.__showViewCalls.length === showViewCallsBefore, 'no view switch happens either when search is disabled, got ' + JSON.stringify(globalThis.__showViewCalls));
    queryInput.disabled = false;

    return chrome.storage.local.get('fs_pending_query');
  }).then((result) => {
    assert(!result.fs_pending_query, 'the pending-query storage key is still cleared even when the disabled guard skips the search, got ' + JSON.stringify(result.fs_pending_query));
  }).then(() => consumePendingChatToggle()).then((handled) => {
    assert(handled === false, 'consumePendingChatToggle resolves false when nothing is pending, got ' + handled);
  }).then(() => {
    const showViewCallsBefore = globalThis.__showViewCalls.length;
    // Finding I3 (final whole-branch review): the flag now carries a
    // timestamp so a value that never got consumed (openPopup() rejected,
    // or the popup closed before init reached this point) can be told
    // apart from a genuinely fresh request, rather than surviving
    // indefinitely into some later, unrelated popup open.
    return chrome.storage.local.set({ fs_pending_toggle_chat: { ts: Date.now() } }).then(() => showViewCallsBefore);
  }).then((showViewCallsBefore) => consumePendingChatToggle().then((handled) => ({ handled, showViewCallsBefore })))
    .then(({ handled, showViewCallsBefore }) => {
      assert(handled === true, 'consumePendingChatToggle resolves true when a fresh toggle is pending, got ' + handled);
      assert(globalThis.__showViewCalls.length === showViewCallsBefore + 1, 'exactly one new view switch is triggered, got ' + JSON.stringify(globalThis.__showViewCalls));
      assert(globalThis.__showViewCalls[globalThis.__showViewCalls.length - 1] === 'chat', 'a fresh pending toggle lands on the chat view, got ' + JSON.stringify(globalThis.__showViewCalls));

      return chrome.storage.local.get('fs_pending_toggle_chat');
    }).then((result) => {
      assert(result.fs_pending_toggle_chat === false, 'the pending toggle is cleared from storage after being consumed, got ' + JSON.stringify(result.fs_pending_toggle_chat));
      const callsBefore = globalThis.__showViewCalls.length;
      return consumePendingChatToggle().then((handled) => ({ handled, callsBefore }));
    }).then(({ handled, callsBefore }) => {
      assert(handled === false, 'consuming the chat toggle a second time (already cleared) resolves false, got ' + handled);
      assert(globalThis.__showViewCalls.length === callsBefore, 'a second consume does not trigger a second view switch, got ' + JSON.stringify(globalThis.__showViewCalls));

      // Finding I3's stale case: a timestamp older than the 60s freshness
      // window must be treated as not-pending -- consumePendingChatToggle()
      // still clears the flag (so it doesn't linger forever), but does NOT
      // force chat view open.
      return chrome.storage.local.set({ fs_pending_toggle_chat: { ts: Date.now() - 120000 } });
    }).then(() => {
      const callsBefore = globalThis.__showViewCalls.length;
      return consumePendingChatToggle().then((handled) => ({ handled, callsBefore }));
    }).then(({ handled, callsBefore }) => {
      assert(handled === false, 'a stale (>60s old) pending toggle resolves false rather than forcing chat open, got ' + handled);
      assert(globalThis.__showViewCalls.length === callsBefore, 'a stale pending toggle triggers no view switch, got ' + JSON.stringify(globalThis.__showViewCalls));

      return chrome.storage.local.get('fs_pending_toggle_chat');
    }).then((result) => {
      assert(result.fs_pending_toggle_chat === false, 'a stale pending toggle is still cleared from storage so it cannot be re-evaluated later, got ' + JSON.stringify(result.fs_pending_toggle_chat));

      // Defense in depth: a bare `true` (the pre-fix shape, or any other
      // malformed value) must also be treated as not-fresh rather than
      // crashing or being mistaken for a valid timestamped flag.
      return chrome.storage.local.set({ fs_pending_toggle_chat: true });
    }).then(() => {
      const callsBefore = globalThis.__showViewCalls.length;
      return consumePendingChatToggle().then((handled) => ({ handled, callsBefore }));
    }).then(({ handled, callsBefore }) => {
      assert(handled === false, 'a bare boolean (non-timestamped) pending toggle is treated as not-fresh, got ' + handled);
      assert(globalThis.__showViewCalls.length === callsBefore, 'a bare-boolean pending toggle triggers no view switch, got ' + JSON.stringify(globalThis.__showViewCalls));
    });
