const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const katex = require('../extension/popup/lib/katex.min.js');

function sourceFile(name) {
  const root = path.resolve(__dirname, '..');
  const revision = process.env.FORMULA_PREVIEW_BASELINE_REF;
  return revision ? execFileSync('git', ['-c', `safe.directory=${root.replaceAll('\\','/')}`,
    'show', `${revision}:extension/${name}`], {cwd:root, encoding:'utf8'})
    : fs.readFileSync(path.join(root, 'extension', name), 'utf8');
}
const popup = sourceFile('popup/popup.js');
function block(source, first, last) {
  const a = source.indexOf(first), b = source.indexOf(last, a);
  assert.ok(a >= 0 && b > a, `Missing production boundary: ${first}`);
  return source.slice(a, b);
}
const preview = block(popup, 'const KATEX_PREVIEW_MACROS =', 'function sourceReference(');
function fixture(surface) {
  // Real bundled KaTeX parsing/expansion/markup, with a minimal DOM sink.
  const render = (text, target, options) => { target.innerHTML = katex.renderToString(text, options); };
  if (surface === 'popup') {
    const context = vm.createContext({katex: {render}});
    vm.runInContext(preview, context);
    return {
      macros: vm.runInContext('KATEX_PREVIEW_MACROS', context),
      async render(text) {
        context.raw = text; context.target = {innerHTML: '', textContent: ''};
        vm.runInContext('renderLatexPreview(raw,target)', context);
        assert.ok(context.target.innerHTML.includes('katex'), 'Must exercise KaTeX, not raw fallback');
        return context.target.innerHTML;
      },
    };
  }
  const source = surface === 'native-content'
    ? sourceFile('content.js') : popup;
  const code = block(source, '    if (info.previewLatex && info.katexJsUrl) {', '    return true;');
  const targets = [], macros = {'\\mb': '\\mathbf'};
  const context = vm.createContext({
    info: {previewLatex: '', katexJsUrl: 'bundled-katex', katexMacros: macros},
    katex: {render}, card: {isConnected: true}, rawPre: {style: {}}, reposition() {},
    document: {createElement: () => ({innerHTML: ''})},
    formulaBox: {insertBefore: target => targets.push(target)},
    loadKatexIntoPage: async () => true, window: {requestAnimationFrame() {}},
  });
  return {macros, async render(text) {
    context.info.previewLatex = text;
    const before = targets.length;
    vm.runInContext(code, context);
    await Promise.resolve();
    assert.equal(targets.length, before + 1, 'Must insert the rendered preview');
    return targets.at(-1).innerHTML;
  }};
}
const describe = macros => JSON.stringify(Object.fromEntries(Object.entries(macros).map(
  ([key, value]) => [key, typeof value === 'string' ? value : 'non-string expansion'])));
const html = markup => markup.slice(markup.indexOf('<span class="katex-html"'));

for (const surface of ['popup', 'native-injected', 'native-content']) {
  for (const [name, probe, definition] of [
    ['custom command', String.raw`\samplemacro`, String.raw`\gdef\samplemacro{z}\samplemacro`],
    ['builtin command', String.raw`\mathbf{x}`, String.raw`\gdef\mathbf#1{#1+1}\mathbf{x}`],
    ['preset alias', String.raw`\mb{x}`, String.raw`\gdef\mb#1{#1+1}\mb{x}`],
  ]) {
    test(`${surface}: a formula cannot redefine a later ${name}`, async () => {
      const f = fixture(surface), initial = describe(f.macros);
      const before = await f.render(probe);
      await f.render(definition);
      const after = await f.render(probe);
      assert.equal(describe(f.macros), initial, 'Shared presets/payload must remain unchanged');
      assert.ok(after === before, 'The same later formula must render independently of preceding definitions');
    });
  }
  test(`${surface}: a failed formula cannot leave a macro definition behind`, async () => {
    const f = fixture(surface), initial = describe(f.macros);
    const before = await f.render(String.raw`\samplemacro`);
    await f.render(String.raw`\gdef\samplemacro{z}\frac{`);
    const after = await f.render(String.raw`\samplemacro`);
    assert.equal(describe(f.macros), initial);
    assert.ok(before === after);
  });
  test(`${surface}: definitions still work within their own formula`, async () => {
    const f = fixture(surface);
    const expected = html(await f.render('x+1'));
    for (const command of ['def', 'gdef']) {
      const actual = html(await f.render(`\\${command}\\local#1{#1+1}\\local{x}`));
      assert.equal(actual, expected);
    }
  });
}
