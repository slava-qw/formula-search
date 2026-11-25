const test = require('node:test');
const assert = require('node:assert/strict');
const History = require('../extension/query-history.js');

function fixture(load = async () => [{query: 'x'}, {query: '\\rho'}, {query: 'QK^T'}]) {
  const document = {activeElement: null, createElement(tag) {
    const classes = new Set();
    return {tag, id: '', value: '', disabled: false, attrs: {}, listeners: {}, children: [], textContent: '',
      classList: {add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value)},
      setAttribute(key, value) { this.attrs[key] = value; }, removeAttribute(key) { delete this.attrs[key]; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      appendChild(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
      contains(child) { return this.children.includes(child); }, scrollIntoView(options) { this.scrolled = options; },
      focus() { document.activeElement = this; },
    };
  }};
  const input = document.createElement('input'), list = document.createElement('ul'), choices = [];
  list.id = 'history'; input.focus();
  let revision = 0;
  const history = new History({input, list, load, document, choose: value => choices.push(value),
    capture: () => ({revision, value: input.value}), isCurrent: state => state.revision === revision && state.value === input.value});
  function key(key, modifiers = {}) {
    const event = {key, prevented: false, stopped: false, preventDefault() { this.prevented = true; },
      stopPropagation() { this.stopped = true; }, ...modifiers};
    const handled = history.handleKeydown(event);
    return {event, handled};
  }
  return {input, list, document, choices, history, key, revise() { revision++; }};
}

test('history exposes an editable combobox with literal listbox options, bounded valid entries and no initial selection', async () => {
  const entries = [{query: '<img src=x onerror=alert(1)>'}, null, {}, {query: ''}, {query: 'x'.repeat(2049)},
    ...Array.from({length: 30}, (_, i) => ({query: 'q'+i}))];
  const f = fixture(async () => entries);
  assert.equal(f.input.attrs.role, 'combobox'); assert.equal(f.input.attrs['aria-expanded'], 'false');
  await f.history.show();
  assert.equal(f.list.attrs.role, 'listbox'); assert.equal(f.input.attrs['aria-controls'], 'history');
  assert.equal(f.input.attrs['aria-autocomplete'], 'none'); assert.equal(f.input.attrs['aria-expanded'], 'true');
  assert.equal(f.list.children.length, 20); assert.equal(f.list.children[0].textContent, entries[0].query);
  assert.equal(f.list.children[0].innerHTML, undefined);
  assert.equal(f.input.attrs['aria-activedescendant'], undefined);
  assert.ok(f.list.children.every(option => option.attrs.role === 'option' && option.attrs['aria-selected'] === 'false'));
});

test('arrow navigation wraps without changing the draft or DOM focus, and Enter chooses once', async () => {
  const f = fixture(); f.input.value = 'draft'; await f.history.show();
  f.key('ArrowDown'); assert.equal(f.history.active, 0); f.key('ArrowUp'); assert.equal(f.history.active, 2);
  assert.equal(f.input.attrs['aria-activedescendant'], f.list.children[2].id);
  assert.equal(f.list.children[2].attrs['aria-selected'], 'true');
  assert.equal(f.input.value, 'draft'); assert.equal(f.document.activeElement, f.input);
  assert.equal(f.key('Enter').handled, true); assert.deepEqual(f.choices, ['QK^T']);
  assert.equal(f.history.open, false); assert.equal(f.input.attrs['aria-expanded'], 'false');
  assert.equal(f.input.attrs['aria-activedescendant'], undefined);
  assert.equal(f.key('Enter').handled, false); assert.deepEqual(f.choices, ['QK^T']);
});

test('arrow keys open history with the requested edge; a pending read is shared', async () => {
  let release, reads = 0;
  const f = fixture(() => { reads++; return new Promise(resolve => release = resolve); });
  assert.equal(f.key('ArrowDown').handled, true);
  const pending = f.history.pending.promise;
  f.key('ArrowUp'); await Promise.resolve(); assert.equal(reads, 1);
  release([{query: 'first'}, {query: 'last'}]); await pending;
  assert.equal(f.history.active, 1); assert.equal(f.input.value, '');
});

for (const action of ['typing', 'blur', 'dismissal', 'document', 'disabled']) {
  test(`a delayed read cannot reopen history after ${action}`, async () => {
    let release;
    const f = fixture(() => new Promise(resolve => release = resolve));
    const pending = f.history.show(); await Promise.resolve();
    if (action === 'typing') { f.input.value = 'new draft'; f.revise(); f.history.hide(); }
    if (action === 'blur') { f.document.activeElement = null; f.history.hide(); }
    if (action === 'dismissal') assert.equal(f.key('Escape').handled, true);
    if (action === 'document') f.revise();
    if (action === 'disabled') f.input.disabled = true;
    release([{query: 'obsolete'}]); await pending;
    assert.equal(f.history.open, false); assert.equal(f.input.attrs['aria-expanded'], 'false');
    assert.equal(f.list.children.length, 0); assert.deepEqual(f.choices, []);
  });
}

test('newer show intent supersedes an old pending read even when the input value is unchanged', async () => {
  const releases = [];
  const f = fixture(() => new Promise(resolve => releases.push(resolve)));
  const old = f.history.show(); await Promise.resolve(); f.revise();
  const current = f.history.show(); await Promise.resolve();
  releases[1]([{query: 'new'}]); await current;
  releases[0]([{query: 'old'}]); await old;
  assert.equal(f.list.children[0].textContent, 'new');
});

test('Escape keeps the draft, Tab remains browser-managed, and IME/modifier keys are left alone', async () => {
  const f = fixture(); f.input.value = 'my draft'; await f.history.show(); f.key('ArrowDown');
  for (const event of [{key: 'Enter', isComposing: true}, {key: 'Enter', keyCode: 229},
    {key: 'ArrowDown', altKey: true}, {key: 'ArrowUp', ctrlKey: true}, {key: 'ArrowDown', shiftKey: true}, {key: 'Tab'}]) {
    assert.equal(f.key(event.key, event).handled, false);
  }
  assert.deepEqual(f.choices, []); assert.equal(f.key('Escape').handled, true);
  assert.equal(f.input.value, 'my draft'); assert.equal(f.document.activeElement, f.input);
});

test('mouse and assistive click selection choose once; an obsolete rendered option cannot choose', async () => {
  const f = fixture(); await f.history.show();
  const option = f.list.children[0];
  option.listeners.mousedown({button: 2, preventDefault() { assert.fail('Right-click must be unchanged'); }});
  assert.deepEqual(f.choices, []);
  option.listeners.mousedown({button: 0, preventDefault() {}}); option.listeners.click();
  assert.deepEqual(f.choices, ['x']);
  await f.history.show(); const old = f.list.children[1];
  f.history.render([{query: 'replacement'}]);
  old.listeners.mousedown({button: 0, preventDefault() { assert.fail('Removed option must be ignored'); }});
  old.listeners.click();
  assert.deepEqual(f.choices, ['x']);
  f.list.children[0].listeners.click(); assert.deepEqual(f.choices, ['x', 'replacement']);
});

test('empty, malformed or rejected history stays closed; a stale failure leaves the new list intact', async () => {
  for (const value of [[], null, {}, [null, {query: 12}]]) {
    const f = fixture(async () => value); assert.equal(await f.history.show(), false); assert.equal(f.history.open, false);
  }
  const f = fixture(async () => { throw Error('storage unavailable'); });
  assert.equal(await f.history.show(), false); assert.equal(f.history.open, false);
  let reject;
  f.history.load = () => new Promise((_, no) => reject = no);
  const old = f.history.show(); await Promise.resolve(); f.history.hide();
  f.history.load = async () => [{query: 'new'}]; await f.history.show();
  reject(Error('old failure')); await old; assert.equal(f.list.children[0].textContent, 'new');
});
