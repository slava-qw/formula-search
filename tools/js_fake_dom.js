// Minimal DOM + chrome.* shim for running extension-JS source (or extracted
// pieces of it) inside py_mini_racer's bare V8 context -- there is no real
// browser or Node/jsdom in this environment. Deliberately covers only what
// the info-card/chat code actually touches, not a general DOM
// implementation. Load this before any extension source and before a test
// script; see tools/run_js_test.py.

class FakeClassList {
  constructor(el) { this._el = el; this._set = new Set(); }
  add(...names) { names.forEach((n) => this._set.add(n)); this._sync(); }
  remove(...names) { names.forEach((n) => this._set.delete(n)); this._sync(); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    const has = this._set.has(name);
    const shouldHave = force === undefined ? !has : !!force;
    if (shouldHave) this._set.add(name); else this._set.delete(name);
    this._sync();
    return shouldHave;
  }
  _sync() { this._el._className = Array.from(this._set).join(' '); }
  _load(str) { this._set = new Set((str || '').split(/\s+/).filter(Boolean)); }
}

function matchesSimple(el, sel) {
  // Supports: tag, #id, .class, [attr], [attr="val"], and concatenations
  // like div.class[attr] -- enough for this codebase's own selectors.
  const re = /(#[\w-]+)|(\.[\w-]+)|(\[[\w-]+(="[^"]*")?\])|^[a-zA-Z][\w-]*/g;
  const tokens = sel.match(re) || [];
  return tokens.every((tok) => {
    if (tok[0] === '#') return el.id === tok.slice(1);
    if (tok[0] === '.') return el.classList.contains(tok.slice(1));
    if (tok[0] === '[') {
      const inner = tok.slice(1, -1);
      const eq = inner.indexOf('=');
      if (eq === -1) return el.getAttribute(inner) !== null;
      const name = inner.slice(0, eq);
      const val = inner.slice(eq + 2, -1);
      return el.getAttribute(name) === val;
    }
    return el.tagName === tok.toUpperCase();
  });
}
function matchesSelector(el, sel) {
  return sel.split(',').map((s) => s.trim()).some((part) => matchesSimple(el, part));
}

class FakeElement {
  constructor(tagName) {
    this.tagName = (tagName || 'div').toUpperCase();
    this._children = [];
    this.parentNode = null;
    this._attrs = {};
    this.dataset = {};
    this.style = {};
    this._text = '';
    this._listeners = {};
    this.classList = new FakeClassList(this);
    this._rect = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    this.disabled = false;
    this._value = '';
    this.type = '';
    this.rows = 1;
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }
  get className() { return this._className || ''; }
  set className(v) { this._className = v; this.classList._load(v); }
  // Real HTMLInputElement/HTMLSelectElement/HTMLTextAreaElement .value is
  // always a string (the IDL attribute is [DOMString]) -- assigning a number
  // like `el.value = 200` coerces to "200" in a real browser. Mirror that
  // here so numeric-field round-trips (e.g. settings forms) behave the same
  // as they would in Chrome.
  get value() { return this._value; }
  set value(v) { this._value = v === null || v === undefined ? '' : String(v); }
  get children() { return this._children; }
  get isConnected() { return !!this._connected; }
  get id() { return this._attrs.id || ''; }
  set id(v) { this._attrs.id = v; idIndex.set(v, this); }
  get clientWidth() { return this._rect.width; }
  get clientHeight() { return this._rect.height; }
  setAttribute(name, value) { this._attrs[name] = String(value); if (name === 'id') this.id = value; }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this._attrs, name) ? this._attrs[name] : null; }
  removeAttribute(name) { delete this._attrs[name]; }
  get textContent() {
    if (this._children.length === 0) return this._text;
    return this._children.map((c) => c.textContent || '').join('');
  }
  set textContent(v) { this._text = v; this._children.forEach((c) => { c.parentNode = null; }); this._children = []; }
  set innerHTML(v) {
    this._children.forEach((c) => { c.parentNode = null; });
    this._children = [];
    this._text = v === '' ? '' : this._text;
  }
  appendChild(child) { child.parentNode = this; this._children.push(child); this._markConnected(); return child; }
  insertBefore(child, ref) {
    child.parentNode = this;
    const i = ref ? this._children.indexOf(ref) : -1;
    if (i === -1) this._children.push(child); else this._children.splice(i, 0, child);
    this._markConnected();
    return child;
  }
  removeChild(child) { const i = this._children.indexOf(child); if (i !== -1) this._children.splice(i, 1); child.parentNode = null; return child; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  _markConnected() {
    let node = this;
    while (node) { if (node._isRoot) { this._propagateConnected(true); break; } node = node.parentNode; }
  }
  _propagateConnected(v) { this._connected = v; this._children.forEach((c) => c._propagateConnected(v)); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn); }
  dispatchEvent(evt) { (this._listeners[evt.type] || []).forEach((fn) => fn(evt)); return true; }
  click() { this.dispatchEvent({ type: 'click' }); }
  getBoundingClientRect() { return this._rect; }
  get offsetWidth() { return this._rect.width; }
  get offsetHeight() { return this._rect.height; }
  setPointerCapture() {}
  releasePointerCapture() {}
  focus() {}
  blur() {}
  querySelector(sel) { const r = this.querySelectorAll(sel); return r[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (el) => {
      el._children.forEach((c) => { if (matchesSelector(c, sel)) out.push(c); walk(c); });
    };
    walk(this);
    return out;
  }
}

const idIndex = new Map();

const documentElement = new FakeElement('html');
documentElement._isRoot = true;
documentElement._connected = true;
const bodyEl = new FakeElement('body');
const headEl = new FakeElement('head');
documentElement.appendChild(headEl);
documentElement.appendChild(bodyEl);

globalThis.document = {
  documentElement,
  body: bodyEl,
  head: headEl,
  createElement: (tag) => new FakeElement(tag),
  createElementNS: (_ns, tag) => new FakeElement(tag),
  getElementById: (id) => idIndex.get(id) || null,
  querySelector: (sel) => documentElement.querySelector(sel),
  querySelectorAll: (sel) => documentElement.querySelectorAll(sel),
  addEventListener() {},
  removeEventListener() {},
};

globalThis.window = {
  innerWidth: 1400,
  innerHeight: 900,
  requestAnimationFrame: (fn) => fn(),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.requestAnimationFrame = globalThis.window.requestAnimationFrame;

globalThis.ResizeObserver = class { observe() {} disconnect() {} };

// setTimeout/clearTimeout: synchronous, same shortcut as
// requestAnimationFrame above -- MiniRacer is a bare V8 context with no
// event loop, so there's no real timer queue to defer into. Debounced code
// under test (draft-save timers) only needs its callback to eventually run
// with the latest value; nothing here asserts on real elapsed time.
globalThis.setTimeout = (fn) => { fn(); return 0; };
globalThis.clearTimeout = () => {};

// chrome.storage.local: in-memory, Promise-based (matches the real
// chrome.storage API's promise form, supported since Chrome 88 -- this
// extension's manifest already requires minimum_chrome_version 125).
const _storageData = {};
const _storageListeners = [];
globalThis.chrome = {
  runtime: {
    getURL: (path) => `chrome-extension://fake-ext-id/${path}`,
    // Real cross-extension chrome.runtime.sendMessage relaying can't be
    // simulated here (no second extension/process exists in this bare V8
    // context) -- this default just simulates "no relay available" by
    // invoking the callback with undefined, same as a real sendMessage call
    // to an unreachable/nonexistent extension id would. Tests that need to
    // assert a relay was actually attempted override this per-test.
    sendMessage: (targetId, msg, callback) => { if (typeof callback === 'function') callback(undefined); },
  },
  storage: {
    local: {
      get(keys) {
        return Promise.resolve().then(() => {
          const out = {};
          const list = keys == null ? Object.keys(_storageData) : (Array.isArray(keys) ? keys : [keys]);
          list.forEach((k) => { if (k in _storageData) out[k] = _storageData[k]; });
          return out;
        });
      },
      set(obj) {
        return Promise.resolve().then(() => {
          const changes = {};
          Object.keys(obj).forEach((k) => {
            changes[k] = { oldValue: _storageData[k], newValue: obj[k] };
            _storageData[k] = obj[k];
          });
          _storageListeners.forEach((fn) => fn(changes, 'local'));
        });
      },
    },
    onChanged: {
      addListener: (fn) => _storageListeners.push(fn),
      removeListener: (fn) => {
        const i = _storageListeners.indexOf(fn);
        if (i !== -1) _storageListeners.splice(i, 1);
      },
    },
  },
};

// service-worker.js registers these at top level (chrome.runtime.onMessage,
// onMessageExternal, chrome.action, chrome.contextMenus, chrome.commands) --
// none existed in this fake DOM before, so service-worker.js could never be
// loaded here at all. These are minimal recording/registration stubs: they
// don't simulate real message routing (individual tests invoke a captured
// listener directly, which is this codebase's established pattern -- see
// e.g. how existing tests override chrome.runtime.sendMessage locally),
// they just let the file load without throwing and expose what was
// registered/called so tests can drive it.
const _messageListeners = [];
const _externalMessageListeners = [];
const _installedListeners = [];
const _commandListeners = [];
const _contextMenuClickListeners = [];
let _lastBadgeText = '';
let _lastBadgeColor = '';
let _openPopupCalls = 0;

chrome.runtime.onMessage = { addListener: (fn) => _messageListeners.push(fn) };
chrome.runtime.onMessageExternal = { addListener: (fn) => _externalMessageListeners.push(fn) };
chrome.runtime.onInstalled = { addListener: (fn) => _installedListeners.push(fn) };
chrome.action = {
  openPopup: () => { _openPopupCalls += 1; return Promise.resolve(); },
  setBadgeText: ({ text }) => { _lastBadgeText = text; return Promise.resolve(); },
  setBadgeBackgroundColor: () => Promise.resolve(),
};
chrome.contextMenus = {
  create: () => {},
  onClicked: { addListener: (fn) => _contextMenuClickListeners.push(fn) },
};
chrome.commands = {
  onCommand: { addListener: (fn) => _commandListeners.push(fn) },
};

globalThis.__fakeChromeInternals = {
  messageListeners: _messageListeners,
  externalMessageListeners: _externalMessageListeners,
  installedListeners: _installedListeners,
  commandListeners: _commandListeners,
  contextMenuClickListeners: _contextMenuClickListeners,
  getLastBadgeText: () => _lastBadgeText,
  getLastBadgeColor: () => _lastBadgeColor,
  getOpenPopupCalls: () => _openPopupCalls,
};

// navigator.clipboard: minimal, in-memory stub -- no test needs it to be
// backed by a real OS clipboard, just to resolve and record the last write.
globalThis.navigator = {
  clipboard: {
    _lastWrite: '',
    writeText(text) {
      this._lastWrite = text;
      return Promise.resolve();
    },
  },
};
