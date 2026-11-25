const test = require('node:test');
const assert = require('node:assert/strict');
const Copy = require('../extension/card-copy.js');

function fixture(clipboard, text = String.raw`\textbf{M}_i <img src=x> & \alpha` + '\n' + String.raw`\frac{a}{b}`) {
  const document = {activeElement: null, createElement(tag) {
    return {tag, children: [], attrs: {}, listeners: {}, parent: null, textContent: '',
      get isConnected() { return this.parent?.isConnected || false; },
      appendChild(node) { node.parent = this; this.children.push(node); },
      remove() { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; },
      contains(node) { return this.children.includes(node) || this.children.some(child => child.contains(node)); },
      setAttribute(key, value) { this.attrs[key] = value; }, removeAttribute(key) { delete this.attrs[key]; },
      addEventListener(name, handler) { this.listeners[name] = handler; },
      focus(options) { document.activeElement = this; this.focusOptions = options; },
      select() { this.selected = this.value; }, scrollIntoView(options) { this.scrollOptions = options; },
    };
  }};
  const container = document.createElement('li');
  container.parent = {isConnected: true};
  const button = document.createElement('button'); button.textContent = 'Copy LaTeX';
  container.appendChild(button);
  Copy.bind(button, {container, document, text, label: 'LaTeX', clipboard: () => clipboard});
  return {document, container, button, text,
    click() { return button.listeners.click({stopPropagation() {}}); },
    status() { return container.children.find(el => el.className === 'formula-copy-status'); },
    source() { return container.children.find(el => el.className === 'formula-copy-source'); },
  };
}

test('only the button exists before interaction; successful copy preserves raw multiline TeX', async () => {
  const writes = [], f = fixture({writeText: async text => writes.push(text)});
  assert.equal(f.container.children.length, 1);
  await f.click();
  assert.deepEqual(writes, [f.text]); assert.equal(f.status().textContent, 'LaTeX copied');
  assert.equal(f.status().attrs.role, 'status'); assert.equal(f.button.textContent, 'Copied');
  assert.equal(f.source(), undefined); assert.equal(f.button.attrs['aria-busy'], undefined);
});

for (const [name, clipboard] of [['absent', undefined], ['missing method', {}],
  ['synchronous rejection', {writeText() { throw Error('denied'); }}],
  ['async rejection', {writeText: async () => { throw Error('denied'); }}]]) {
  test(`${name} clipboard provides literal readonly selected text without HTML interpretation`, async () => {
    const f = fixture(clipboard); f.button.focus(); await f.click();
    const source = f.source();
    assert.equal(source.value, f.text); assert.equal(source.selected, f.text);
    assert.equal(source.readOnly, true); assert.equal(source.spellcheck, false);
    assert.equal(source.attrs['aria-label'], 'LaTeX to copy'); assert.equal(source.innerHTML, undefined);
    assert.equal(f.document.activeElement, source); assert.equal(source.focusOptions.preventScroll, true);
    assert.match(f.status().textContent, /Could not copy/);
    let stopped = false; source.listeners.click({stopPropagation() { stopped = true; }});
    assert.equal(stopped, true);
  });
}

test('pending copies suppress duplicate clicks; successful retry removes the old fallback', async () => {
  let reject, calls = 0;
  const f = fixture({writeText: () => { calls++; return calls === 1 ? new Promise((_, no) => reject = no) : Promise.resolve(); }});
  const pending = f.click(); await f.click();
  assert.equal(calls, 1); assert.equal(f.button.attrs['aria-busy'], 'true');
  reject(Error('denied')); await pending; assert.ok(f.source());
  await f.click(); assert.equal(calls, 2); assert.equal(f.status().textContent, 'LaTeX copied');
  assert.equal(f.source(), undefined); assert.equal(f.container.children.length, 2);
});

test('a late failure leaves focus on the newer user destination', async () => {
  let reject;
  const f = fixture({writeText: () => new Promise((_, no) => reject = no)});
  f.button.focus(); const pending = f.click();
  const query = f.document.createElement('input'); query.focus();
  reject(Error('denied')); await pending;
  assert.equal(f.document.activeElement, query); assert.equal(f.source().selected, undefined);
  assert.equal(f.source().scrollOptions, undefined);
});

for (const outcome of ['success', 'failure']) {
  test(`detached card receives no late ${outcome} feedback or focus change`, async () => {
    let resolve, reject;
    const f = fixture({writeText: () => new Promise((yes, no) => { resolve = yes; reject = no; })});
    const pending = f.click(); f.container.parent = null;
    if (outcome === 'success') resolve(); else reject(Error('denied'));
    await pending;
    assert.equal(f.source(), undefined); assert.equal(f.status().textContent, 'Copying LaTeX…');
    assert.equal(f.document.activeElement, null); assert.equal(f.button.attrs['aria-busy'], undefined);
  });
}

test('the same recovery retains complete Markdown text and the icon feedback', async () => {
  const f = fixture(undefined), button = f.document.createElement('button'); button.textContent = '⧉';
  f.container.appendChild(button);
  const text = '$x$ — near page 3, Introduction · main.tex:28';
  Copy.bind(button, {container: f.container, document: f.document, text, label: 'Markdown',
    successText: '✓', errorText: '✕', clipboard: () => undefined});
  await button.listeners.click({stopPropagation() {}});
  assert.equal(f.source().value, text); assert.equal(f.source().attrs['aria-label'], 'Markdown to copy');
  assert.equal(button.textContent, '✕');
});

test('different formats share one feedback area and replace its fallback with the requested text', async () => {
  const f = fixture(undefined); await f.click();
  const button = f.document.createElement('button'); button.textContent = '⧉'; f.container.appendChild(button);
  Copy.bind(button, {container: f.container, document: f.document, text: '$x$ — page 3', label: 'Markdown', clipboard: () => undefined});
  await button.listeners.click({stopPropagation() {}});
  assert.equal(f.container.children.filter(el => el.className === 'formula-copy-status').length, 1);
  assert.equal(f.container.children.filter(el => el.className === 'formula-copy-source').length, 1);
  assert.equal(f.source().value, '$x$ — page 3'); assert.match(f.status().textContent, /Could not copy Markdown/);
});

test('a late failure for one format cannot replace the newer format success or steal focus', async () => {
  let reject;
  const f = fixture({writeText: () => new Promise((_, no) => reject = no)});
  const pending = f.click();
  const button = f.document.createElement('button'); button.textContent = '⧉'; f.container.appendChild(button);
  Copy.bind(button, {container: f.container, document: f.document, text: '$x$', label: 'Markdown', clipboard: () => ({writeText: async () => {}})});
  button.focus(); await button.listeners.click({stopPropagation() {}});
  reject(Error('old failure')); await pending;
  assert.equal(f.status().textContent, 'Markdown copied'); assert.equal(f.source(), undefined);
  assert.equal(f.document.activeElement, button);
});
