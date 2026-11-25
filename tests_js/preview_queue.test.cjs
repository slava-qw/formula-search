const test = require('node:test');
const assert = require('node:assert/strict');
const Queue = require('../extension/preview-queue.js');

function fixture(options = {}) {
  const calls = [], observers = [], listeners = {};
  const container = {cards: new Set(), contains(card) { return this.cards.has(card); },
    addEventListener(name, callback) { listeners[name] = callback; }};
  class Observer {
    constructor(callback, config) { this.callback = callback; this.config = config; this.observed = new Set(); observers.push(this); }
    observe(card) { this.observed.add(card); }
    unobserve(card) { this.observed.delete(card); }
    disconnect() { this.observed.clear(); this.disconnected = true; }
    deliver(card, visible = true) { this.callback([{target: card, isIntersecting: visible}]); }
  }
  const queue = new Queue({container, render(raw, target) { calls.push(raw); target.rendered = raw; }, Observer, ...options});
  function add(raw, config) {
    const classes = new Set();
    const target = {textContent: '', classList: {add: v => classes.add(v), remove: v => classes.delete(v)}, classes};
    const card = {isConnected: true, contains: el => el === target};
    target.closest = () => card;
    container.cards.add(card); queue.add(card, target, raw, config);
    return {card, target};
  }
  return {queue, container, calls, observers, listeners, add};
}

test('five initial previews render immediately; only approaching later cards are rendered once', () => {
  const f = fixture(), cards = Array.from({length: 9}, (_, i) => f.add(`x_${i}`));
  assert.deepEqual(f.calls, ['x_0', 'x_1', 'x_2', 'x_3', 'x_4']);
  assert.equal(f.queue.pending.size, 4); assert.equal(f.observers.length, 1);
  assert.equal(f.observers[0].config.root, f.container);
  f.observers[0].deliver(cards[7].card, false); assert.equal(f.calls.length, 5);
  f.observers[0].deliver(cards[7].card); f.observers[0].deliver(cards[7].card);
  assert.deepEqual(f.calls.slice(5), ['x_7']); assert.equal(f.queue.pending.size, 3);
  assert.equal(cards[7].target.classes.size, 0);
});

test('keyboard focus renders an offscreen formula synchronously without changing its source', () => {
  const f = fixture({eagerCount: 0}), raw = String.raw`\textbf{M}_{i} <img src=x>`, item = f.add(raw);
  assert.equal(item.target.textContent, raw); assert.equal(item.target.innerHTML, undefined);
  f.listeners.focusin({target: item.target});
  assert.equal(item.target.rendered, raw); assert.equal(f.queue.pending.size, 0);
  assert.equal(f.observers[0].observed.size, 0);
});

test('reset releases queued cards and ignores even a late callback for a reused card', () => {
  const f = fixture({eagerCount: 0}), old = f.add('old'), observer = f.observers[0];
  f.queue.reset(); assert.equal(observer.disconnected, true); assert.equal(f.queue.pending.size, 0);
  f.queue.add(old.card, old.target, 'new');
  observer.deliver(old.card); assert.deepEqual(f.calls, []);
  f.observers[1].deliver(old.card); assert.deepEqual(f.calls, ['new']);
});

test('detached, replaced and moved targets do not receive stale rendering', () => {
  for (const change of [
    (f, item) => { item.card.isConnected = false; },
    (f, item) => { f.container.cards.delete(item.card); },
    (f, item) => { item.card.contains = () => false; },
  ]) {
    const f = fixture({eagerCount: 0}), item = f.add('old'); change(f, item);
    f.observers[0].deliver(item.card);
    assert.deepEqual(f.calls, []); assert.equal(f.queue.pending.size, 0);
  }
});

test('step previews and missing or unusable observer APIs fall back to eager rendering', () => {
  const f = fixture({eagerCount: 0}); f.add('step', {defer: false});
  assert.deepEqual(f.calls, ['step']); assert.equal(f.observers.length, 0);
  for (const Observer of [null, class { constructor() { throw Error('unavailable'); } }]) {
    const g = fixture({Observer, eagerCount: 0}); g.add('a'); g.add('b');
    assert.deepEqual(g.calls, ['a', 'b']); assert.equal(g.queue.pending.size, 0);
  }
});

test('each new list starts with the eager allowance and no previous pending cards', () => {
  const f = fixture({eagerCount: 1}); f.add('old visible'); f.add('old distant'); f.queue.reset();
  f.add('new visible'); f.add('new distant');
  assert.deepEqual(f.calls, ['old visible', 'new visible']); assert.equal(f.queue.pending.size, 1);
});

test('out-of-order reveals retain real KaTeX typography and isolated per-formula definitions', () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const katex = require('../extension/popup/lib/katex.min.js');
  const source = fs.readFileSync(require.resolve('../extension/popup/popup.js'), 'utf8');
  const start = source.indexOf('const KATEX_PREVIEW_MACROS =');
  const context = vm.createContext({katex: {render(raw, target, options) {
    target.innerHTML = katex.renderToString(raw, options);
  }}});
  vm.runInContext(source.slice(start, source.indexOf('function sourceReference(', start)), context);
  const render = (raw, target) => {
    context.raw = raw; context.target = target;
    vm.runInContext('renderLatexPreview(raw,target)', context);
  };
  const rows = [String.raw`\gdef\mathbf#1{#1+1}\mathbf{x}`, String.raw`\mathbf{x}`,
    String.raw`\mbox{Bernoulli}(X_i)`, String.raw`a_{\textbf{M}}`, String.raw`\textnormal{sample mean}`];
  const expected = rows.map(raw => { const target = {}; render(raw, target); return target.innerHTML; });
  const f = fixture({eagerCount: 0, render}), cards = rows.map(raw => f.add(raw));
  for (const index of [4, 0, 2, 1, 3]) f.observers[0].deliver(cards[index].card);
  assert.deepEqual(cards.map(item => item.target.innerHTML), expected);
  assert.equal(f.queue.pending.size, 0);
});
