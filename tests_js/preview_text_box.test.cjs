const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const katex = require('../extension/popup/lib/katex.min.js');
const root=path.resolve(__dirname,'..');
const revision=process.env.FORMULA_PREVIEW_BASELINE_REF;
const source=revision ? execFileSync('git',['-c',`safe.directory=${root.replaceAll('\\','/')}`,
  'show',`${revision}:extension/popup/popup.js`],{cwd:root,encoding:'utf8'})
  : fs.readFileSync(path.join(root,'extension/popup/popup.js'),'utf8');
const start=source.indexOf('const KATEX_PREVIEW_MACROS =');
const context=vm.createContext({katex:{render:(text,target,options)=>{
  target.innerHTML=katex.renderToString(text,options);
}}});
vm.runInContext(source.slice(start,source.indexOf('function sourceReference(',start)),context);
const withoutSource=markup=>markup.replace(/<annotation encoding="application\/x-tex">[\s\S]*?<\/annotation>/g,'');
for(const [name,input,reference] of [
  ['VAE distribution name is upright text', String.raw`X^{\prime}_i\sim \mbox{Bernoulli}(X_i)`,String.raw`X^{\prime}_i\sim \hbox{Bernoulli}(X_i)`],
  ['spaces are retained',String.raw`\mbox{sample mean}`,String.raw`\hbox{sample mean}`],
  ['nested boxes retain spaces and text scope',String.raw`\mbox{outer \mbox{inner words} end}`,String.raw`\hbox{outer \hbox{inner words} end}`],
  ['nested bold text remains bold',String.raw`\mbox{a \textbf{bold word}}`,String.raw`\hbox{a \textbf{bold word}}`],
  ['inline mathematics stays inside its text box',String.raw`\mbox{before $x^2$ after}`,String.raw`\hbox{before $x^2$ after}`],
  ['escaped braces and symbols remain literal text',String.raw`\mbox{\{a b\} \& c}`,String.raw`\hbox{\{a b\} \& c}`],
  ['definitions inside one formula can override the alias',String.raw`\def\mbox#1{#1+1}\mbox{x}`,String.raw`x+1`],
  ['escaped ampersand does not start a display alignment',String.raw`x\& y`,String.raw`x\& y`],
]) {
  test(`preview text box: ${name}`,()=>{
    context.raw=input;context.target={innerHTML:'',textContent:''};
    vm.runInContext('renderLatexPreview(raw,target)',context);
    const expected=katex.renderToString(reference,{throwOnError:true,displayMode:false,trust:false,strict:false});
    assert.ok(withoutSource(context.target.innerHTML)===withoutSource(expected),
      'Both visual HTML and accessible MathML must equal the supported text-box reference');
  });
}
test('preview text box: actual alignment still uses display layout',()=>{
  context.raw=String.raw`a&=b\\c&=d`;context.target={innerHTML:'',textContent:''};
  vm.runInContext('renderLatexPreview(raw,target)',context);
  const expected=katex.renderToString(String.raw`\begin{aligned}a&=b\\c&=d\end{aligned}`,
    {throwOnError:true,displayMode:true,trust:false,strict:false});
  assert.equal(withoutSource(context.target.innerHTML),withoutSource(expected));
});
