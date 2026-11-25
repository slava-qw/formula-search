const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const katex = require('../extension/popup/lib/katex.min.js');
const root = path.resolve(__dirname, '..');
const revision = process.env.FORMULA_PREVIEW_BASELINE_REF;
const source = revision ? execFileSync('git', ['-c', `safe.directory=${root.replaceAll('\\','/')}`,
  'show', `${revision}:extension/popup/popup.js`], {cwd:root, encoding:'utf8'})
  : fs.readFileSync(path.join(root,'extension/popup/popup.js'), 'utf8');
const start = source.indexOf('const KATEX_PREVIEW_MACROS =');
const block = source.slice(start, source.indexOf('function sourceReference(',start));
const context = vm.createContext({katex:{render:(text,target,options)=>{
  target.innerHTML=katex.renderToString(text,options);
}}});
vm.runInContext(block, context);

for (const [name, latex] of [
  ['upright words retain spaces', String.raw`\textnormal{sample mean}`],
  ['roman text retains spaces in a subscript', String.raw`p_{\textrm{pow law}}(x)`],
  ['bold text stays bold in a subscript', String.raw`a_{\textbf{M}}(x)`],
  ['nested roman and bold text preserve their scopes', String.raw`\textbf{bold \textrm{roman} bold}`],
  ['nested bold text remains inside an upright label', String.raw`\textrm{mean \textbf{value}}`],
  ['nested text normal reaches the bundled renderer unchanged', String.raw`\textit{italic \textnormal{normal} italic}`],
  ['escaped braces and text spacing are preserved', String.raw`\textbf{\{a b\}}`],
  ['a definition label retains its explicit text font', String.raw`x \mathrel{\overset{\raisebox{-0.25ex}{\textnormal{\tiny def}}}{=}} y`],
  ['display operator limits remain underneath', String.raw`\displaystyle\operatornamewithlimits{argmax}_{x} f(x)`],
  ['inline operator limits remain at the side', String.raw`\operatornamewithlimits{argmax}_{x} f(x)`],
]) {
  test(`preview typography: ${name}`, () => {
    context.raw=latex; context.target={innerHTML:'',textContent:''};
    vm.runInContext('renderLatexPreview(raw,target)',context);
    const expected=katex.renderToString(latex,{throwOnError:true,displayMode:false,trust:false,strict:false});
    assert.ok(context.target.innerHTML === expected, 'Preview must preserve the bundled renderer\'s supported typography');
  });
}
