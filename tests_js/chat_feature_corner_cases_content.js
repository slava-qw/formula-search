globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

const messagesEl = document.createElement('div');
const clearBtnEl = document.createElement('button');

// 1. A message with NO context (context: null, e.g. sent from the popup
// with no formula behind it) must not render a broken/clickable context
// line, and must still render fine end-to-end with a timestamp.
appendChatMessage('no context here', null)
  .then((messages) => {
    renderChatMessages(messagesEl, clearBtnEl, messages);
    assert(messagesEl.children.length === 1, 'a context-less message still renders, got ' + messagesEl.children.length);
    assert(messagesEl.querySelector('.fs-chat-msg-time') !== null, 'a context-less message still gets a timestamp');

    // 2. A message with a context that has NO page (an old-shape message
    // from before this feature set, or a page-only match with page === -1)
    // must not throw when rendered.
    return appendChatMessage('old-shape context', { latex: '\\beta', section: '' });
  })
  .then((messages) => {
    renderChatMessages(messagesEl, clearBtnEl, messages);
    assert(messagesEl.children.length === 2, 'a message with an old, narrower context shape still renders, got ' + messagesEl.children.length);

    // 3. Draft persistence must survive a failed send (chrome.storage.local
    // rejecting), not just a missing chrome.storage.
    const chatInput = document.createElement('textarea');
    chatInput.value = 'draft should survive a rejected send';
    const noop = () => {};
    const realSet = chrome.storage.local.set;
    chrome.storage.local.set = () => Promise.reject(new Error('Extension context invalidated.'));
    const textSent = chatInput.value;
    return sendChatMessageWithRestore(chatInput, null, messagesEl, clearBtnEl, noop, noop, noop).then(() => {
      chrome.storage.local.set = realSet;
      assert(chatInput.value === textSent, 'the input still shows the restored text after a rejected send');
      return loadDraft();
    }).then((draft) => {
      assert(draft === textSent, 'the draft was re-saved after a rejected send, so it is not lost on next open, got ' + JSON.stringify(draft));
    });
  })
  .then(() => {
    // 4. formatRelativeTime must not throw or return something nonsensical
    // for an edge timestamp (exactly at a boundary, or a future timestamp
    // -- e.g. from slight clock skew between the tab and the extension
    // process).
    assert(typeof formatRelativeTime(Date.now() + 5000) === 'string', 'formatRelativeTime does not throw on a future timestamp');
    assert(formatRelativeTime(0) !== '', 'formatRelativeTime does not throw or return empty for a very old timestamp (epoch 0)');
  });
