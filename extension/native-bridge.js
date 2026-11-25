/* Reach Chrome's PDF viewer in either a same-process frame or a guest target.
 * Keep attachment lifecycle and discovery separate from the injected UI.
 */
(() => {
  const queues = new Map();
  const PDF_ORIGIN = 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai';

  async function attachToViewer(tabId, task, options) {
    const api = chrome.debugger;
    if (!api?.attach) throw new Error('Native PDF navigation is unavailable in this browser.');
    const root = { tabId };
    const contexts = new Map();
    const initialized = new Set();
    const initializing = new Set();
    const sources = new Map();
    const parents = new Map();
    const setupFailures = new Map();
    const pending = new Set();
    let attached = false;
    let closing = false;
    let wake = null;
    let stopped = null;
    const failure = (code, message) => Object.assign(new Error(message), {code});
    const checkCurrent = () => {
      if (stopped) throw stopped;
      if (options.signal?.aborted || options.isCurrent && !options.isCurrent()) {
        throw failure('cancelled', 'This formula jump was cancelled.');
      }
    };
    const signal = () => { if (wake) { wake(); wake = null; } };
    const normalizeUrl = value => String(value || '').split('#', 1)[0];
    const verifyTab = async () => {
      checkCurrent();
      if (options.url) {
        let tab;
        try { tab = await chrome.tabs.get(tabId); }
        catch { throw failure('tab-closed', 'This paper tab was closed.'); }
        if (normalizeUrl(tab.url) !== normalizeUrl(options.url)) {
          throw failure('document-changed', 'The paper in this tab changed. Search the current paper again.');
        }
      }
      checkCurrent();
    };
    const onDetach = (source, reason) => {
      if (source.tabId === tabId && !source.sessionId && !closing) {
        stopped = failure('viewer-detached', 'The PDF viewer connection was closed. Try the result again.');
        stopped.detachReason = reason;
        signal();
      }
    };
    const onRemoved = id => {
      if (id === tabId) { stopped = failure('tab-closed', 'This paper tab was closed.'); signal(); }
    };
    const onUpdated = (id, change) => {
      if (id === tabId && options.url && change.url && normalizeUrl(change.url) !== normalizeUrl(options.url)) {
        stopped = failure('document-changed', 'The paper in this tab changed. Search the current paper again.');
        signal();
      }
    };
    const contextKey = (source, id) => `${source.sessionId || 'root'}:${id}`;
    const track = (promise) => {
      pending.add(promise);
      promise.catch(() => {}).finally(() => { pending.delete(promise); signal(); });
    };
    const setup = async (source) => {
      const key = source.sessionId || 'root';
      sources.set(key, source);
      if (initialized.has(key) || initializing.has(key) || closing || stopped) return;
      if ((setupFailures.get(key)?.retryAt || 0) > performance.now()) return;
      initializing.add(key);
      try {
        await api.sendCommand(source, 'Runtime.enable');
        if (closing || stopped || !sources.has(key)) return;
        // Discover related targets, then probe their default execution contexts
        // before injecting UI. Same-process frames have contexts but no target.
        await api.sendCommand(source, 'Target.setAutoAttach', {
          autoAttach: true, waitForDebuggerOnStart: false, flatten: true,
        });
        if (sources.has(key)) { initialized.add(key); setupFailures.delete(key); }
      } catch (error) {
        if (!closing && sources.has(key)) {
          const count = (setupFailures.get(key)?.count || 0)+1;
          setupFailures.set(key, {count, error: String(error),
            retryAt: performance.now()+Math.min(500, 60*2**Math.min(count-1, 4))});
        }
      } finally { initializing.delete(key); }
    };
    const onEvent = (source, method, params) => {
      if (source.tabId !== tabId || closing) return;
      if (method === 'Target.attachedToTarget' && params.sessionId) {
        if (['iframe', 'page', 'webview'].includes(params.targetInfo?.type)) {
          parents.set(params.sessionId, source.sessionId || 'root');
          track(setup({ tabId, sessionId: params.sessionId }));
        }
      } else if (method === 'Target.detachedFromTarget' && params.sessionId) {
        const removed = new Set([params.sessionId]);
        for (const key of removed) {
          for (const [child, parent] of parents) if (parent === key) removed.add(child);
          sources.delete(key); initialized.delete(key); setupFailures.delete(key); parents.delete(key);
        }
        for (const [key, entry] of contexts) if (removed.has(entry.source.sessionId)) contexts.delete(key);
      } else if (method === 'Runtime.executionContextCreated') {
        const context = params.context;
        if (context.auxData?.isDefault) {
          contexts.set(contextKey(source, context.id), { source, context });
          signal();
        }
      } else if (method === 'Runtime.executionContextDestroyed') {
        contexts.delete(contextKey(source, params.executionContextId));
      } else if (method === 'Runtime.executionContextsCleared') {
        for (const [key, entry] of contexts) {
          if (entry.source.sessionId === source.sessionId) contexts.delete(key);
        }
      }
    };
    await verifyTab();
    api.onEvent.addListener(onEvent);
    api.onDetach?.addListener(onDetach);
    chrome.tabs?.onRemoved?.addListener(onRemoved);
    chrome.tabs?.onUpdated?.addListener(onUpdated);
    options.signal?.addEventListener('abort', signal);
    try {
      await api.attach(root, '1.3');
      attached = true;
      await setup(root);
      const timeout = Number.isFinite(options.timeoutMs) ? Math.max(0, Math.min(15000, options.timeoutMs)) : 6000;
      const deadline = performance.now() + timeout;
      const checked = new Set();
      let lastState = null;
      const probeExpression = `(${FormulaNativeViewerState.toString()})(${JSON.stringify(options.page ?? 0)}, null, ${JSON.stringify(options.url || '')})`;
      while (performance.now() < deadline) {
        checkCurrent();
        for (const source of sources.values()) {
          const key = source.sessionId || 'root';
          if (!initialized.has(key) && !initializing.has(key) &&
              (setupFailures.get(key)?.retryAt || 0) <= performance.now()) track(setup(source));
        }
        const ordered = [...contexts.entries()].sort((a, b) =>
          Number(b[1].context.origin.startsWith(PDF_ORIGIN)) - Number(a[1].context.origin.startsWith(PDF_ORIGIN)));
        for (const [key, { source, context }] of ordered) {
          if (checked.has(key) && !context.origin.startsWith(PDF_ORIGIN)) continue;
          checked.add(key);
          const evaluate = async (expression, evaluationOptions = {}) => {
            await verifyTab();
            return api.sendCommand(source, 'Runtime.evaluate', {
              expression, contextId: context.id, returnByValue: true, awaitPromise: false, ...evaluationOptions,
            });
          };
          let state = null;
          try {
            const probe = await evaluate(probeExpression);
            state = probe.result?.value;
          } catch (error) {
            if (error.code) throw error;
            // A frame may have navigated while we were discovering it.
          }
          checkCurrent();
          if (state?.viewerFound) {
            checked.delete(key);
            lastState = state;
          }
          if (state && !state.ok && state.retryable === false) throw failure(state.code, state.error);
          if (state?.ok) {
            await verifyTab();
            return await task(evaluate);
          }
        }
        await new Promise(resolve => {
          const timer = setTimeout(resolve, 60);
          wake = () => { clearTimeout(timer); resolve(); };
        });
      }
      const timeoutError = failure('viewer-timeout', lastState?.error || 'The native PDF viewer is not ready. Wait for the PDF to load and try again.');
      timeoutError.discovery = {contexts: contexts.size,
        setupFailures: [...setupFailures.values()].map(({count, error}) => ({count, error}))};
      throw timeoutError;
    } finally {
      closing = true;
      wake = null;
      api.onEvent.removeListener(onEvent);
      api.onDetach?.removeListener(onDetach);
      chrome.tabs?.onRemoved?.removeListener(onRemoved);
      chrome.tabs?.onUpdated?.removeListener(onUpdated);
      options.signal?.removeEventListener('abort', signal);
      if (attached) await api.detach(root).catch(() => {});
    }
  }

  function withViewer(tabId, task, options = {}) {
    // Repeated result clicks share a tab. Finish and detach one session before
    // attaching the next, rather than failing with "already attached".
    const previous = queues.get(tabId) || Promise.resolve();
    const result = previous.catch(() => {}).then(() => attachToViewer(tabId, task, options));
    queues.set(tabId, result);
    const cleanup = () => { if (queues.get(tabId) === result) queues.delete(tabId); };
    result.then(cleanup, cleanup);
    return result;
  }

  globalThis.FormulaNativeBridge = { withViewer };
})();
