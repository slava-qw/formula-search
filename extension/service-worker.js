'use strict';

// One writer owns shared read/modify/write state across all extension surfaces.
// The native PDF page uses the same queue through cross-extension messaging.
if (typeof importScripts === 'function') importScripts('document-identity.js', 'bookmark-identity.js', 'bookmark-backup.js', 'shared-store.js');
const sharedStore = createFormulaStore(chrome.storage.local);
const noteSubscribers = new Set();
function subscribeNotes(port) {
  if (port.name !== 'formula-notes') return;
  noteSubscribers.add(port);
  port.onDisconnect.addListener(() => noteSubscribers.delete(port));
  sharedStore.dispatch('notes.list').then(messages => {
    if (noteSubscribers.has(port)) port.postMessage({type: 'notesChanged', messages});
  }).catch(() => {});
}
chrome.runtime.onConnect?.addListener(subscribeNotes);
chrome.runtime.onConnectExternal?.addListener(port => {
  if (port.sender?.id === 'mhjfbmdgcfjbbpaeojofohoefgiehjai') subscribeNotes(port);
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local' || !changes.fs_chat_history) return;
  for (const port of noteSubscribers) {
    try { port.postMessage({type: 'notesChanged', messages: changes.fs_chat_history.newValue || []}); }
    catch { noteSubscribers.delete(port); }
  }
});

chrome.action.setBadgeBackgroundColor({ color: '#1a73e8' });

const badgeUpdates = new Map();

function updateBadge(matchCount, tabId) {
  const scope = Number.isInteger(tabId) && tabId >= 0 ? {tabId} : {};
  const key = scope.tabId ?? 'global';
  const generation = (badgeUpdates.get(key) || 0) + 1;
  badgeUpdates.set(key, generation);
  return chrome.storage.local.get('fs_settings').then((result) => {
    if (badgeUpdates.get(key) !== generation) return;
    const settings = result.fs_settings || {};
    const enabled = settings.badgeEnabled !== false; // default true when unset
    if (!enabled || !matchCount) {
      return chrome.action.setBadgeText({ ...scope, text: '' });
    }
    return chrome.action.setBadgeText({ ...scope, text: String(matchCount) });
  });
}

chrome.tabs?.onRemoved?.addListener(tabId => badgeUpdates.delete(tabId));

function handleMessage(message, sendResponse) {
  if (!message) return false;
  if (message.type === 'storeRequest') {
    sharedStore.dispatch(message.action, message.data)
      .then(value => sendResponse({ok: true, value}))
      .catch(error => sendResponse({ok: false, error: error.message}));
    return true;
  }
  // Retain the old protocol for PDF cards that were already open at reload.
  if (message.type === 'chatRelay') {
    const actions = {load: 'notes.list', append: 'notes.append', clear: 'notes.clear'};
    if (!actions[message.action]) return false;
    sharedStore.dispatch(actions[message.action], {text: message.text, context: message.context})
      .then(messages => sendResponse({ok: true, messages}))
      .catch(error => sendResponse({ok: false, error: error.message}));
    return true;
  }
  if (message.type === 'searchComplete') {
    updateBadge(message.matchCount, message.tabId).then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (message.type === 'openPopup') {
    Promise.resolve()
      .then(() => chrome.action.openPopup())
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
  return false;
}

// Same-extension callers (content.js, for Task 6's back button) use
// onMessage; the debugger-realm copy, which executes as a different
// extension, must use onMessageExternal instead.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => handleMessage(message, sendResponse));
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => handleMessage(message, sendResponse));

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'fs-search-selection',
    title: 'Search formula: "%s"',
    contexts: ['selection'],
  });
});

chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId !== 'fs-search-selection' || !info.selectionText) return;
  const text = info.selectionText;
  chrome.storage.local.set({ fs_pending_query: text }).then(() => {
    // Best-effort: open the popup and try to relay the text live so an
    // already-open (or freshly-opened) popup updates immediately. The
    // storage write above is the guaranteed path -- if this fails or the
    // popup's listener isn't registered in time, the popup's own init
    // (consumePendingQuery, popup.js) picks the value up on its next open
    // regardless.
    Promise.resolve().then(() => chrome.action.openPopup()).catch(() => {});
    try {
      chrome.runtime.sendMessage({ type: 'contextMenuQuery', text }, () => { void chrome.runtime.lastError; });
    } catch { /* best effort */ }
  });
});

chrome.commands.onCommand.addListener((command) => {
  if (command === 'next-result') {
    try { chrome.runtime.sendMessage({ type: 'stepCommand', direction: 'next' }, () => { void chrome.runtime.lastError; }); } catch { /* no open popup to receive it */ }
  } else if (command === 'prev-result') {
    try { chrome.runtime.sendMessage({ type: 'stepCommand', direction: 'prev' }, () => { void chrome.runtime.lastError; }); } catch { /* no open popup to receive it */ }
  } else if (command === 'toggle-chat') {
    // Same guaranteed-storage-write-then-best-effort-live-relay pattern as
    // the contextMenuQuery handler above: opening a popup that wasn't
    // already open means loading a whole new document and re-running
    // popup.js's full script, which takes far longer than the microtask
    // this openPopup()/sendMessage() pair resolves on -- a fresh popup's
    // onMessage listener is very unlikely to be registered in time to
    // receive the live toggleChatCommand message. fs_pending_toggle_chat
    // is the durable fallback the popup's own init (consumePendingChatToggle,
    // popup.js) picks up on its next open regardless of that race.
    //
    // The stored value carries a timestamp (not a bare boolean) so
    // consumePendingChatToggle() can tell a genuinely fresh request apart
    // from one that never got consumed at all -- e.g. openPopup() below
    // rejects, or the user dismisses the freshly-opened popup before its
    // init chain reaches consumePendingChatToggle() -- which would
    // otherwise leave the flag stale and wrongly force chat view open on a
    // LATER, unrelated popup open (toolbar click, a different shortcut, a
    // context-menu search).
    chrome.storage.local.set({ fs_pending_toggle_chat: { ts: Date.now() } }).then(() => {
      Promise.resolve().then(() => chrome.action.openPopup()).catch(() => {});
      try { chrome.runtime.sendMessage({ type: 'toggleChatCommand' }, () => { void chrome.runtime.lastError; }); } catch { /* best effort */ }
    });
  }
});
