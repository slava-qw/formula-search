/* This function is also installed in Chrome's PDF viewer via Runtime.evaluate.
 * Keep it self-contained: that realm has runtime messaging but no storage API. */
function FormulaStoreClient(action, data = {}, extensionId = '') {
  return new Promise((resolve, reject) => {
    if (typeof chrome === 'undefined' || !chrome.runtime?.sendMessage) {
      reject(new Error('Formula Search is unavailable. Your text has not been saved.'));
      return;
    }
    const callback = response => {
      const error = chrome.runtime.lastError;
      if (error || !response || response.ok !== true) {
        reject(new Error(error?.message || response?.error || 'Could not save. Reload Formula Search and try again.'));
      } else resolve(response.value);
    };
    try {
      const message = {type: 'storeRequest', action, data};
      if (extensionId) chrome.runtime.sendMessage(extensionId, message, callback);
      else chrome.runtime.sendMessage(message, callback);
    } catch (error) { reject(error); }
  });
}
if (typeof module !== 'undefined') module.exports = FormulaStoreClient;

// The PDF viewer cannot subscribe to our storage.onChanged event directly.
// Reconnect on user focus after worker suspension; no heartbeat keeps it alive.
function FormulaStoreSubscribe(onChange, extensionId = '') {
  if (typeof chrome === 'undefined' || !chrome.runtime?.connect) return () => {};
  let port = null, closed = false;
  const connect = () => {
    if (closed || port || document.visibilityState === 'hidden') return;
    try {
      port = extensionId ? chrome.runtime.connect(extensionId, {name: 'formula-notes'})
        : chrome.runtime.connect({name: 'formula-notes'});
      const active = port;
      port.onMessage.addListener(message => {
        if (!closed && active === port && message.type === 'notesChanged') onChange(message.messages);
      });
      port.onDisconnect.addListener(() => {
        void chrome.runtime.lastError;
        if (port === active) port = null;
      });
    } catch { port = null; }
  };
  window.addEventListener('focus', connect, true);
  document.addEventListener('visibilitychange', connect);
  connect();
  return () => {
    closed = true;
    window.removeEventListener('focus', connect, true);
    document.removeEventListener('visibilitychange', connect);
    try { port?.disconnect(); } catch { /* Already disconnected. */ }
    port = null;
  };
}
