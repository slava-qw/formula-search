// Exercise the production store behind a messaging boundary, including when a
// client realm has no chrome.storage. Storage failures still reach the client.
const __storeForRelay = createFormulaStore(chrome.storage.local);
chrome.runtime.sendMessage = (...args) => {
  const message = typeof args[0] === 'string' ? args[1] : args[0];
  const callback = typeof args[0] === 'string' ? args[2] : args[1];
  if (message?.type !== 'storeRequest') { callback?.(); return; }
  __storeForRelay.dispatch(message.action, message.data)
    .then(value => callback?.({ok: true, value}))
    .catch(error => callback?.({ok: false, error: error.message}));
};
