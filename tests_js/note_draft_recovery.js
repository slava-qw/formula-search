const RELAY_EXTENSION_ID = 'test-extension-id';
globalThis.__results = [];
function assert(cond, message) { __results.push((cond ? 'OK: ' : 'FAIL: ') + message); }
const input = document.createElement('textarea');
input._saveErrorElement = document.createElement('div');
const messages = document.createElement('div'), clear = document.createElement('button');
const noop = () => {};
const realSend = chrome.runtime.sendMessage;
let release;
chrome.runtime.sendMessage = (...args) => { release = args[args.length - 1]; };
input.value = 'original';
const failed = sendChatMessageWithRestore(input, null, messages, clear, noop, noop, noop);
input.value = 'typed while saving';
release({ok: false, error: 'offline'});
failed.then(() => {
  assert(input.value === 'original\n\ntyped while saving', 'a failed save preserves both the attempted note and newer typing');
  assert(input._saveErrorElement.textContent.includes('Not saved: offline'), 'save failure is visible beside the input');
  assert(messages.children.length === 0, 'a rejected save does not create a fake persisted bubble');
  input.value = 'second attempt';
  const saved = sendChatMessageWithRestore(input, null, messages, clear, noop, noop, noop);
  input.value = 'next draft';
  saveDraft('next draft');
  release({ok: true, value: [{id: 'saved', role: 'user', text: 'second attempt', timestamp: Date.now()}]});
  return saved;
}).then(() => {
  assert(input.value === 'next draft', 'a successful save does not clear newer typing');
  assert(input._saveErrorElement.textContent === '', 'retry clears the previous error');
  assert(messages.children.length === 1, 'acknowledged save renders the persisted note');
  return loadDraft();
}).then(draft => {
  assert(draft === 'next draft', 'newer draft survives an earlier successful save');
  chrome.runtime.sendMessage = realSend;
}).catch(error => assert(false, String(error)));
