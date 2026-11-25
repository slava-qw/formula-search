globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

const internals = globalThis.__fakeChromeInternals;

function dispatchMessage(message) {
  let response;
  let responded = false;
  const sendResponse = (r) => { response = r; responded = true; };
  internals.messageListeners.forEach((fn) => fn(message, {}, sendResponse));
  return { response, responded };
}

Promise.resolve().then(() => {
  return chrome.storage.local.set({ fs_settings: { badgeEnabled: true } });
}).then(() => {
  const { response } = dispatchMessage({ type: 'searchComplete', matchCount: 5 });
  return new Promise((resolve) => setTimeout(resolve, 0)).then(() => response);
}).then(() => {
  assert(internals.getLastBadgeText() === '5', 'badge shows the match count when enabled, got ' + JSON.stringify(internals.getLastBadgeText()));

  dispatchMessage({ type: 'searchComplete', matchCount: 0 });
  return new Promise((resolve) => setTimeout(resolve, 0));
}).then(() => {
  assert(internals.getLastBadgeText() === '', 'badge clears on a zero-match result, got ' + JSON.stringify(internals.getLastBadgeText()));

  return chrome.storage.local.set({ fs_settings: { badgeEnabled: false } });
}).then(() => {
  dispatchMessage({ type: 'searchComplete', matchCount: 7 });
  return new Promise((resolve) => setTimeout(resolve, 0));
}).then(() => {
  assert(internals.getLastBadgeText() === '', 'badge stays cleared when badgeEnabled is false even with matches, got ' + JSON.stringify(internals.getLastBadgeText()));

  return chrome.storage.local.set({ fs_settings: {} }); // badgeEnabled unset entirely
}).then(() => {
  dispatchMessage({ type: 'searchComplete', matchCount: 3 });
  return new Promise((resolve) => setTimeout(resolve, 0));
}).then(() => {
  assert(internals.getLastBadgeText() === '3', 'badge defaults to enabled when badgeEnabled is unset, got ' + JSON.stringify(internals.getLastBadgeText()));
}).then(() => {
  const internals2 = globalThis.__fakeChromeInternals;
  assert(internals2.installedListeners.length > 0, 'an onInstalled listener is registered (for creating the context menu)');
  internals2.installedListeners.forEach((fn) => fn());
  assert(internals2.contextMenuClickListeners.length > 0, 'an onClicked listener is registered for the context menu');

  return chrome.storage.local.set({ fs_pending_query: '' }); // start clean
}).then(() => {
  const internals3 = globalThis.__fakeChromeInternals;
  internals3.contextMenuClickListeners.forEach((fn) => fn({ menuItemId: 'fs-search-selection', selectionText: '\\alpha + \\beta' }));
  return new Promise((resolve) => setTimeout(resolve, 0));
}).then(() => chrome.storage.local.get('fs_pending_query')).then((result) => {
  assert(result.fs_pending_query === '\\alpha + \\beta', 'clicking the context menu item writes the selection to fs_pending_query, got ' + JSON.stringify(result.fs_pending_query));
  const internals4 = globalThis.__fakeChromeInternals;
  assert(internals4.getOpenPopupCalls() >= 1, 'clicking the context menu item attempts chrome.action.openPopup(), got ' + internals4.getOpenPopupCalls() + ' calls');
}).then(() => {
  const internals5 = globalThis.__fakeChromeInternals;
  assert(internals5.commandListeners.length > 0, 'a commands.onCommand listener is registered');

  let lastSent = null;
  chrome.runtime.sendMessage = (msg, cb) => { lastSent = msg; if (cb) cb(undefined); };
  internals5.commandListeners.forEach((fn) => fn('next-result'));
  assert(lastSent && lastSent.type === 'stepCommand' && lastSent.direction === 'next', 'the next-result command sends a stepCommand/next message, got ' + JSON.stringify(lastSent));

  lastSent = null;
  internals5.commandListeners.forEach((fn) => fn('prev-result'));
  assert(lastSent && lastSent.type === 'stepCommand' && lastSent.direction === 'prev', 'the prev-result command sends a stepCommand/prev message, got ' + JSON.stringify(lastSent));

  lastSent = null;
  const popupCallsBefore = internals5.getOpenPopupCalls();
  internals5.commandListeners.forEach((fn) => fn('toggle-chat'));
  // toggle-chat first writes a durable fs_pending_toggle_chat flag to
  // chrome.storage.local (an async, Promise-based fake -- see js_fake_dom.js)
  // BEFORE its best-effort openPopup()/sendMessage() pair, so both of those
  // land a few microtask ticks after dispatch now, not synchronously/one-tick
  // out like the context-menu handler above. Settle through several ticks
  // (same setTimeout(resolve, 0) idiom used above, chained) before checking
  // either -- chosen generously rather than tuned to the exact minimum, so
  // it doesn't silently start failing again if that chain gains one more hop.
  const settleTick = () => new Promise((resolve) => setTimeout(resolve, 0));
  return settleTick().then(settleTick).then(settleTick).then(settleTick).then(() => {
    assert(lastSent && lastSent.type === 'toggleChatCommand', 'the toggle-chat command sends a toggleChatCommand message, got ' + JSON.stringify(lastSent));
    assert(internals5.getOpenPopupCalls() > popupCallsBefore, 'the toggle-chat command also attempts chrome.action.openPopup(), got ' + internals5.getOpenPopupCalls() + ' vs before ' + popupCallsBefore);
    return chrome.storage.local.get('fs_pending_toggle_chat');
  }).then((result) => {
    // Finding I3 (final whole-branch review): the flag is a timestamped
    // object, not a bare boolean, so consumePendingChatToggle() (popup.js)
    // can tell a genuinely fresh request apart from one that never got
    // consumed (openPopup() rejected, or the popup closed before init got
    // there) instead of it going stale forever.
    const stored = result.fs_pending_toggle_chat;
    assert(!!stored && typeof stored === 'object' && typeof stored.ts === 'number', 'the toggle-chat command writes a timestamped durable fs_pending_toggle_chat flag before the best-effort relay, got ' + JSON.stringify(stored));
    assert(stored && (Date.now() - stored.ts) < 5000, 'the written timestamp is current (within 5s of now), got ' + JSON.stringify(stored));
  });
});
