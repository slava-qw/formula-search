globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

const messagesEl = document.createElement('div');
const clearBtnEl = document.createElement('button');
renderChatMessages(messagesEl, clearBtnEl, []);
assert(messagesEl.style.display === 'none', 'renderChatMessages hides the thread when empty');
assert(clearBtnEl.style.display === 'none', 'renderChatMessages hides the clear button when empty');

appendChatMessage('hello', { latex: '\\psi', section: 'Intro', page: 2, bbox: [1, 2, 3, 4], page_w: 600, page_h: 800, source: 'tex', match_type: 'exact', bbox_source: 'query-subspan' })
  .then((messages) => {
    assert(messages.length === 1, 'appendChatMessage returns the updated array, got length ' + messages.length);
    assert(messages[0].text === 'hello', 'stores the message text');
    assert(messages[0].role === 'user', 'stores role=user');
    assert(messages[0].context && messages[0].context.latex === '\\psi', 'stores the context, got ' + JSON.stringify(messages[0].context));
    assert(messages[0].context.bbox && messages[0].context.bbox.length === 4, 'stores the bbox in context, got ' + JSON.stringify(messages[0].context.bbox));
    assert(messages[0].context.page_w === 600 && messages[0].context.page_h === 800, 'stores page_w/page_h in context, got ' + messages[0].context.page_w + '/' + messages[0].context.page_h);
    return appendChatMessage('world', null);
  })
  .then((messages) => {
    assert(messages.length === 2, 'a second append grows the array to 2, got ' + messages.length);
    assert(messages[0].id !== messages[1].id, 'each message gets a distinct id');
    assert(messages[1].timestamp > messages[0].timestamp, 'timestamps are strictly increasing even when appended back-to-back, got ' + messages[0].timestamp + ' -> ' + messages[1].timestamp);
    renderChatMessages(messagesEl, clearBtnEl, messages);
    assert(messagesEl.children.length === 2, 'renderChatMessages renders one bubble per message, got ' + messagesEl.children.length);
    assert(messagesEl.children[1].children[0].textContent === 'world', 'bubble text matches the message text');
    assert(messagesEl.children[1].className === 'fs-chat-msg fs-chat-msg-user', 'bubble gets the role modifier class, got ' + messagesEl.children[1].className);
    assert(messagesEl.children[1].querySelector('.fs-chat-msg-time') !== null, 'renderChatMessages includes a timestamp element per bubble');
    assert(messagesEl.children[1].querySelector('.fs-chat-msg-copy') !== null, 'renderChatMessages includes a per-message copy control on every bubble');
    assert(formatRelativeTime(Date.now()) === 'just now', 'formatRelativeTime formats a fresh timestamp as "just now", got ' + formatRelativeTime(Date.now()));
    assert(formatRelativeTime(Date.now() - 5 * 60000) === '5m ago', 'formatRelativeTime formats 5 minutes ago correctly, got ' + formatRelativeTime(Date.now() - 5 * 60000));
    assert(formatRelativeTime(Date.now() - 2 * 3600000) === '2h ago', 'formatRelativeTime formats 2 hours ago correctly, got ' + formatRelativeTime(Date.now() - 2 * 3600000));
    assert(messagesEl.style.display === 'flex', 'thread becomes visible once there are messages');
    assert(clearBtnEl.style.display === 'block', 'clear button becomes visible once there are messages');
    // A second, independent "card" instance (a fresh pair of DOM nodes, as
    // a real close+reopen would create) must see the SAME history -- this
    // is the bug being fixed: history used to live only in the first
    // card's own DOM and vanish when that card was destroyed.
    const messagesEl2 = document.createElement('div');
    const clearBtnEl2 = document.createElement('button');
    return loadChatHistory().then((messages2) => {
      renderChatMessages(messagesEl2, clearBtnEl2, messages2);
      assert(messagesEl2.children.length === 2, 'a fresh card element reads the same persisted history, got ' + messagesEl2.children.length);
      assert(messagesEl2.children[1].children[0].textContent === 'world', 'fresh card shows the same message text');
    });
  })
  .then(() => clearChatHistory())
  .then(() => loadChatHistory())
  .then((messages) => {
    assert(messages.length === 0, 'clearChatHistory empties the stored history, got length ' + messages.length);
    // CHAT_MAX_HISTORY is gone from this block now that trimming reads
    // fs_settings.maxChatHistory (falling back to 500) -- use the literal
    // default here since no fs_settings key is seeded for this block.
    const seeded = Array.from({ length: 500 }, (_, i) => ({
      id: 'seed' + i, role: 'user', text: 'm' + i, timestamp: i, context: null,
    }));
    return chrome.storage.local.set({ fs_chat_history: seeded });
  })
  .then(() => appendChatMessage('overflow', null))
  .then((messages) => {
    assert(messages.length === 500, 'history is capped at the default 500 when no fs_settings.maxChatHistory is set, got ' + messages.length);
    assert(messages[0].text === 'm1', 'the oldest message (m0) was dropped to make room, got ' + messages[0].text);
    assert(messages[messages.length - 1].text === 'overflow', 'the newest message is kept, got ' + messages[messages.length - 1].text);
  })
  .then(() => {
    // Regression test for the real bug this guards against: chrome.debugger's
    // Runtime.evaluate executes injected code in the target page's own raw JS
    // realm, which has `chrome` defined but NOT `chrome.storage` -- confirmed
    // live in real Chrome. The worker still persists notes through messaging.
    const savedStorage = chrome.storage;
    delete chrome.storage;
    return appendChatMessage('should not throw', null)
      .then((messages) => {
        // The worker retains storage access independently of the client realm.
        assert(messages.length === 500 && messages[messages.length - 1].text === 'should not throw', 'the worker persists and returns the full capped history even when the client has no storage API');
      })
      .catch((err) => {
        assert(false, 'appendChatMessage must not throw when chrome.storage is unavailable, but threw: ' + err);
      })
      .then(() => {
        chrome.storage = savedStorage;
      });
  })
  .then(() => {
    // Regression test for the missing .catch() finding: chrome.storage.local
    // can reject (not just be undefined) if the extension context is
    // invalidated mid-write (a routine occurrence on extension reload/
    // update, not exotic) -- the send path must restore the typed text
    // instead of losing it silently. sendChatMessageWithRestore is the
    // exact function showInfoPanel's per-card sendChatMessage delegates
    // to, so this exercises the real send+restore logic, not a copy of it.
    const realSet = chrome.storage.local.set;
    chrome.storage.local.set = () => Promise.reject(new Error('Extension context invalidated.'));
    const chatInput = document.createElement('textarea');
    chatInput.value = 'this should come back';
    const chatMessages2 = document.createElement('div');
    const chatClearBtn2 = document.createElement('button');
    const noop = () => {};
    // Returning sendChatMessageWithRestore's own promise (rather than
    // polling or an arbitrary extra tick) is what lets this await the
    // catch's restore deterministically instead of racing it.
    return sendChatMessageWithRestore(chatInput, { latex: '', section: '', page: 0 }, chatMessages2, chatClearBtn2, noop, noop, noop)
      .then(() => {
        assert(chatInput.value === 'this should come back', 'sendChatMessageWithRestore restores the typed text when the write rejects, got ' + JSON.stringify(chatInput.value));
      })
      .catch((err) => {
        assert(false, 'sendChatMessageWithRestore must not throw when the write rejects, but threw: ' + err);
      })
      .then(() => {
        chrome.storage.local.set = realSet;
      });
  })
  .then(() => {
    // Draft persistence: save/load/clear round-trip, and a successful send
    // clears the draft (nothing left to restore next time).
    return saveDraftPromiseForTest('typing a question')
      .then(() => loadDraft())
      .then((draft) => {
        assert(draft === 'typing a question', 'loadDraft reads back a saved draft, got ' + JSON.stringify(draft));
        return clearDraft();
      })
      .then(() => loadDraft())
      .then((draft) => {
        assert(draft === '', 'clearDraft empties the draft, got ' + JSON.stringify(draft));
      });
  })
  .then(() => {
    // Regression test: a rejected clipboard write on a per-message copy
    // click must not throw, and must show the '✕' failure glyph rather than
    // silently leaving the button unchanged with no sign anything failed --
    // then revert to the resting '⧉' glyph via its per-button timer. Each
    // `new Promise((resolve) => setTimeout(resolve, 0))` hop below flushes
    // one more step of the click handler's own promise chain (mirrors the
    // same pattern in tests_js/chat_store_popup_view.js).
    const copyBtn = messagesEl.children[messagesEl.children.length - 1].querySelector('.fs-chat-msg-copy');
    const realWriteText = navigator.clipboard.writeText;
    navigator.clipboard.writeText = () => Promise.reject(new Error('document not focused'));
    copyBtn.click();
    return new Promise((resolve) => setTimeout(resolve, 0)).then(() => {
      navigator.clipboard.writeText = realWriteText;
      assert(copyBtn.textContent === '✕', 'a rejected clipboard write on a per-message copy click shows the failure glyph, got ' + JSON.stringify(copyBtn.textContent));
      return new Promise((resolve) => setTimeout(resolve, 0));
    }).then(() => {
      assert(copyBtn.textContent === '⧉', 'the copy button reverts to its resting glyph after the failure timer, got ' + JSON.stringify(copyBtn.textContent));
    });
  }).then(() => {
    // maxChatHistory setting controls trimming (content.js has direct
    // chrome.storage access, so it reads fs_settings directly -- no relay
    // needed here).
    return chrome.storage.local.set({ fs_settings: { maxChatHistory: 3 } });
  }).then(() => clearChatHistory())
    .then(() => appendChatMessage('m1', null))
    .then(() => appendChatMessage('m2', null))
    .then(() => appendChatMessage('m3', null))
    .then(() => appendChatMessage('m4', null))
    .then((messages) => {
      assert(messages.length === 3, 'appendChatMessage trims to fs_settings.maxChatHistory when set, got length ' + messages.length);
      assert(messages[0].text === 'm2' && messages[2].text === 'm4', 'trimming keeps the most recent messages, got ' + JSON.stringify(messages.map((m) => m.text)));
    });

// saveDraft() itself is fire-and-forget (matches the production call sites,
// which save on every keystroke and don't await it) -- this test-only
// wrapper lets the test await the write actually landing before reading it
// back, without changing saveDraft's real signature.
function saveDraftPromiseForTest(text) {
  saveDraft(text);
  return chrome.storage.local.get('fs_chat_draft');
}
