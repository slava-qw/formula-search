globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

initChatView();

const toggleBtn = document.getElementById('chat-toggle-btn');
const chatViewEl = document.getElementById('chat-view');
const resultsEl = document.getElementById('results');
const inputEl = document.getElementById('chat-input');
const sendBtn = document.getElementById('chat-send-btn');
const messagesEl = document.getElementById('chat-messages');

toggleBtn.click();
assert(!chatViewEl.classList.contains('hidden'), 'clicking the chat toggle shows the chat view');
assert(resultsEl.classList.contains('hidden'), 'clicking the chat toggle hides the results list');
assert(toggleBtn.classList.contains('active'), 'the toggle button marks itself active while showing');

inputEl.value = 'what is this term';
inputEl.dispatchEvent({ type: 'input' });
assert(sendBtn.disabled === false, 'send becomes enabled once the input has text');

sendPopupChatMessage().then(() => loadChatHistory()).then((messages) => {
  assert(messages.length === 1, 'sending from the popup chat view appends to the shared history, got length ' + messages.length);
  assert(messages[0].text === 'what is this term', 'stores the typed text');
  assert(messagesEl.children.length === 1, 'the popup chat view renders the sent message, got ' + messagesEl.children.length);
  assert(messagesEl.children[0].className === 'chat-msg chat-msg-user', 'popup bubble gets the role modifier class, got ' + messagesEl.children[0].className);
  assert(messagesEl.children[0].querySelector('.chat-msg-time') !== null, 'the popup chat view renders a timestamp per bubble');

  // Live sync: a message appended from ANOTHER surface (e.g. an info card)
  // must show up here too via chrome.storage.onChanged, without needing to
  // click the toggle again.
  return appendChatMessage('from an info card', { latex: '\\alpha', section: '', page: 0 });
}).then(() => {
  assert(messagesEl.children.length === 2, 'a message appended elsewhere appears live via onChanged, got ' + messagesEl.children.length);
  assert(messagesEl.children[1].textContent.indexOf('from an info card') !== -1, 'shows the externally-added message text, got ' + messagesEl.children[1].textContent);
  assert(messagesEl.children[1].textContent.indexOf('\\alpha') !== -1, 'shows the context provenance line for that message, got ' + messagesEl.children[1].textContent);
  assert(messagesEl.children[1].querySelector('.chat-msg-context').tagName === 'BUTTON', 'the context provenance line is a clickable button, got ' + messagesEl.children[1].querySelector('.chat-msg-context').tagName);

  const clearBtn = document.getElementById('chat-clear-btn');
  const chatActionsEl = document.getElementById('chat-actions');
  // Visibility lives on the #chat-actions wrapper (toggled by
  // renderChatMessages), not on #chat-clear-btn itself -- nothing toggles
  // .hidden on the clear button directly since Task 6's markup change.
  assert(!chatActionsEl.classList.contains('hidden'), 'the chat actions bar (clear) is visible once there is history');
  clearBtn.click();
  return loadChatHistory();
}).then((messages) => {
  assert(messages.length === 0, 'the clear button empties the shared history, got length ' + messages.length);
}).then(() => {
  // Regression test for the missing .catch() finding: chrome.storage.local
  // can reject (not just be undefined) if the extension context is
  // invalidated mid-write (a routine occurrence on extension reload/
  // update, not exotic) -- the send path must restore the typed text
  // instead of losing it silently.
  const realSet = chrome.storage.local.set;
  chrome.storage.local.set = () => Promise.reject(new Error('Extension context invalidated.'));
  inputEl.value = 'this should come back';
  inputEl.dispatchEvent({ type: 'input' });
  // Returning sendPopupChatMessage's own promise (rather than polling or an
  // arbitrary extra tick) is what lets this await the catch's restore
  // deterministically instead of racing it.
  return sendPopupChatMessage()
    .then(() => {
      assert(inputEl.value === 'this should come back', 'sendPopupChatMessage restores the typed text when the write rejects, got ' + JSON.stringify(inputEl.value));
      assert(sendBtn.disabled === false, 'sendPopupChatMessage re-enables the send button when the write rejects');
    })
    .catch((err) => {
      assert(false, 'sendPopupChatMessage must not throw when the write rejects, but threw: ' + err);
    })
    .then(() => {
      chrome.storage.local.set = realSet;
    });
}).then(() => {
  return chrome.storage.local.set({ fs_chat_draft: 'restore me' });
}).then(() => loadDraft()).then((draft) => {
  assert(draft === 'restore me', 'the popup view can read a draft saved elsewhere, got ' + JSON.stringify(draft));
}).then(() => clearChatHistory())
  .then(() => {
    const badge = document.getElementById('chat-unread-badge');
    assert(badge !== null, 'the fixture/markup provides a #chat-unread-badge element');
    assert(badge.classList.contains('hidden'), 'the badge starts hidden with no history');
    return appendChatMessage('a new message', null);
  })
  .then(() => {
    // The toggle is currently showing (opened earlier in this test), so a
    // message arriving now should NOT show as unread -- it's already visible.
    const badge = document.getElementById('chat-unread-badge');
    assert(badge.classList.contains('hidden'), 'no unread badge while the chat view is already open, got hidden=' + badge.classList.contains('hidden'));
    toggleBtn.click(); // close the chat view
    return appendChatMessage('missed while closed', null);
  })
  .then(() => updateUnreadBadge())
  .then(() => {
    const badge = document.getElementById('chat-unread-badge');
    assert(!badge.classList.contains('hidden'), 'a message arriving while the chat view is closed shows the unread badge');
    assert(badge.textContent === '1', 'the badge shows the correct unread count, got ' + badge.textContent);
    toggleBtn.click(); // reopen -- should mark seen and clear the badge
    return new Promise((resolve) => setTimeout(resolve, 0));
  })
  .then(() => {
    const badge = document.getElementById('chat-unread-badge');
    assert(badge.classList.contains('hidden'), 'reopening the chat view marks messages seen and hides the badge');
  }).then(() => {
    const bubble = messagesEl.children[messagesEl.children.length - 1];
    const copyBtn = bubble.querySelector('.chat-msg-copy');
    assert(copyBtn !== null, 'each message bubble has its own copy control');
    copyBtn.click();
    return new Promise((resolve) => setTimeout(resolve, 0));
  }).then(() => {
    assert(navigator.clipboard._lastWrite === messagesEl.children[messagesEl.children.length - 1].querySelector('.chat-msg-text').textContent, "clicking a message's own copy control writes ONLY that message's text, not the whole thread");
    assert(document.getElementById('chat-copy-btn') === null, 'the old whole-thread Copy button no longer exists');
  }).then(() => {
    // Click-to-jump (Task 1) payload construction + disabled-guard: this is
    // the only realm that renders it (popup-outer's renderChatMessages),
    // so this is the correct file for real coverage of it, rather than the
    // wrong-surface corner-case files that exercise the injected/content
    // copies which never read m.context for this at all.
    const fullContext = {
      latex: '\\beta', section: 'Sec 2', page: 3,
      bbox: [1, 2, 3, 4], page_w: 600, page_h: 800,
      source: 'ocr', source_url: 'https://arxiv.org/pdf/1706.03762v7', match_type: 'exact', bbox_source: 'pdf',
    };
    return appendChatMessage('with full context', fullContext);
  }).then(() => {
    const bubble = messagesEl.children[messagesEl.children.length - 1];
    const ctxBtn = bubble.querySelector('.chat-msg-context');
    assert(ctxBtn !== null, 'a message with a full valid context renders a provenance button');
    assert(ctxBtn.disabled !== true, 'a full, page-valid context renders an enabled provenance button, got disabled=' + ctxBtn.disabled);
    globalThis.__sendScrollToMatchCalls.length = 0;
    ctxBtn.click();
    assert(globalThis.__sendScrollToMatchCalls.length === 1, 'clicking an enabled provenance button calls sendScrollToMatch exactly once, got ' + globalThis.__sendScrollToMatchCalls.length);
    const call = globalThis.__sendScrollToMatchCalls[0] || {};
    assert(call.page === 3, 'forwards context.page, got ' + JSON.stringify(call.page));
    assert(JSON.stringify(call.bbox) === JSON.stringify([1, 2, 3, 4]), 'forwards context.bbox, got ' + JSON.stringify(call.bbox));
    assert(call.page_w === 600, 'forwards context.page_w, got ' + JSON.stringify(call.page_w));
    assert(call.page_h === 800, 'forwards context.page_h, got ' + JSON.stringify(call.page_h));
    assert(call.latex === '\\beta', 'forwards context.latex, got ' + JSON.stringify(call.latex));
    assert(call.source === 'ocr', 'forwards context.source, got ' + JSON.stringify(call.source));
    assert(call.match_type === 'exact', 'forwards context.match_type, got ' + JSON.stringify(call.match_type));
    assert(call.bbox_source === 'pdf', 'forwards context.bbox_source, got ' + JSON.stringify(call.bbox_source));

    return appendChatMessage('old-shape context', { latex: '\\gamma', section: 'Sec 3' });
  }).then(() => {
    const bubble = messagesEl.children[messagesEl.children.length - 1];
    const ctxBtn = bubble.querySelector('.chat-msg-context');
    assert(ctxBtn !== null, 'an old-shape context (latex/section only, no page) still renders a provenance button');
    assert(ctxBtn.disabled === true, 'an old-shape context with no page renders a disabled provenance button, got disabled=' + ctxBtn.disabled);
    globalThis.__sendScrollToMatchCalls.length = 0;
    ctxBtn.click();
    assert(globalThis.__sendScrollToMatchCalls.length === 0, 'clicking a disabled (old-shape) provenance button does not call sendScrollToMatch');

    return appendChatMessage('negative page context', { latex: '\\delta', section: '', page: -1 });
  }).then(() => {
    const bubble = messagesEl.children[messagesEl.children.length - 1];
    const ctxBtn = bubble.querySelector('.chat-msg-context');
    assert(ctxBtn !== null, 'a context with page === -1 still renders a provenance button');
    assert(ctxBtn.disabled === true, 'a context with page === -1 renders a disabled provenance button, got disabled=' + ctxBtn.disabled);
    globalThis.__sendScrollToMatchCalls.length = 0;
    ctxBtn.click();
    assert(globalThis.__sendScrollToMatchCalls.length === 0, 'clicking a page===-1 disabled provenance button does not call sendScrollToMatch');
  }).then(() => {
    // Regression test: a rejected clipboard write on a per-message copy
    // click must not throw, and must show the '✕' failure glyph rather than
    // silently leaving the button unchanged with no sign anything failed --
    // then revert to the resting '⧉' glyph via its per-button timer. Each
    // `new Promise((resolve) => setTimeout(resolve, 0))` hop below flushes
    // one more step of the click handler's own promise chain (this harness's
    // setTimeout stub in tools/js_fake_dom.js runs its callback synchronously,
    // but resolving a promise still only settles already-queued microtasks
    // in FIFO order, so it takes two separate hops to observe first the '✕'
    // and then the reverted '⧉').
    const bubble = messagesEl.children[messagesEl.children.length - 1];
    const copyBtn = bubble.querySelector('.chat-msg-copy');
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
    // View switching must never disable a control -- every button stays
    // clickable at all times, and clicking one always cleanly switches away
    // from whatever view was previously active.
    // The chat view was left open by an earlier block (the badge reopen
    // above) -- normalize to a known closed starting state so this block's
    // clicks below mean what their comments say.
    if (!chatView.classList.contains('hidden')) toggleBtn.click();
    assert(chatView.classList.contains('hidden'), 'chat view starts closed for this block');
    assert(viewToggleBtn.disabled === false, 'view-toggle-btn starts enabled');
    toggleBtn.click(); // open chat
    assert(!chatView.classList.contains('hidden'), 'chat view is now showing');
    assert(viewToggleBtn.disabled === false, 'view-toggle-btn is NOT disabled while chat is open, got disabled=' + viewToggleBtn.disabled);
    viewToggleBtn.click(); // switch to step-through while chat is open
    assert(chatView.classList.contains('hidden'), 'clicking the list/step toggle while chat is open switches away from chat');
    assert(!resultsList.classList.contains('hidden'), 'the results list is visible again after leaving chat');
    assert(chatToggleBtn.disabled === false, 'chat-toggle-btn is never disabled either, got disabled=' + chatToggleBtn.disabled);
    toggleBtn.click(); // back to chat
    assert(!chatView.classList.contains('hidden'), 'chat view can be reopened after switching away and back');
    toggleBtn.click(); // close chat, should return to whatever list/step mode was active before
    assert(chatView.classList.contains('hidden'), 'chat view closes on a second click');
    assert(!resultsList.classList.contains('hidden'), 'closing chat restores the results list');
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
