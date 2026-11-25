const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(surface, clipboard) {
  const filename=surface==='injected'?'popup/popup.js':'content.js';
  const code=fs.readFileSync(path.join(__dirname,'../extension',filename),'utf8');
  const start=code.indexOf('// === FORMULA COPY (native) BEGIN ===');
  const end=code.indexOf('// === FORMULA COPY (native) END ===',start);
  assert.ok(start>=0 && end>start);
  const document={activeElement:null,createElement(tag) {
    return {tag,children:[],attrs:{},listeners:{},textContent:'',parent:null,
      get isConnected(){return this.parent?.isConnected || false;},
      append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}},
      appendChild(node){this.append(node);},
      setAttribute(name,value){this.attrs[name]=value;},removeAttribute(name){delete this.attrs[name];},
      addEventListener(name,handler){this.listeners[name]=handler;},
      focus(options){document.activeElement=this;this.focusOptions=options;},select(){this.selected=this.value;}};
  }};
  const context=vm.createContext({document,navigator:{clipboard}});
  vm.runInContext(code.slice(start,end),context);
  const root={isConnected:true,children:[],appendChild(node){node.parent=this;this.children.push(node);}};
  const add=raw=>{
    context.parent=root;context.raw=raw;
    vm.runInContext('appendLatexCopyControl(parent,raw)',context);
    const controls=root.children.at(-1);
    return controls && {controls,button:controls.children[0],status:controls.children[1],
      click(){return controls.children[0].listeners.click({stopPropagation(){}});},
      source(){return controls.children.find(el=>el.tag==='textarea');}};
  };
  return {add,root,document};
}

for(const surface of ['injected','content']) {
  test(`${surface} formula copy: exact raw notation is copied without reference or preview rewriting`,async()=>{
    const copied=[];
    const f=fixture(surface,{writeText:async text=>copied.push(text)});
    const raw=String.raw`\textbf{M}_{i} & <tag>`+'\n'+String.raw`\frac{a}{b} \\ c`;
    const card=f.add(raw);
    assert.equal(card.button.type,'button');
    assert.equal(card.button.textContent,'Copy LaTeX');
    assert.equal(card.status.attrs.role,'status');
    await card.click();
    assert.deepEqual(copied,[raw]);
    assert.equal(card.status.textContent,'Copied');
    assert.equal(card.source(),undefined);
    assert.equal(card.button.attrs['aria-busy'],undefined);
  });
  test(`${surface} formula copy: unavailable clipboard exposes exact selectable text`,async()=>{
    const f=fixture(surface,undefined),raw=String.raw`<img src=x>\{a\} & b`,card=f.add(raw);
    await card.click();
    const source=card.source();
    assert.equal(source.value,raw);assert.equal(source.selected,raw);
    assert.equal(source.readOnly,true);assert.equal(source.spellcheck,false);
    assert.equal(source.focusOptions.preventScroll,true);assert.equal(f.document.activeElement,source);
    assert.match(card.status.textContent,/Could not copy/);
    assert.equal(source.innerHTML,undefined);
  });
  test(`${surface} formula copy: rejected write reports failure and retry can succeed`,async()=>{
    let attempts=0;
    const f=fixture(surface,{writeText:()=>{if(++attempts===1) throw Error('denied');return Promise.resolve();}});
    const card=f.add('x+y');await card.click();
    assert.match(card.status.textContent,/Could not copy/);const source=card.source();
    await card.click();assert.equal(attempts,2);assert.equal(card.status.textContent,'Copied');
    assert.equal(card.source(),source);assert.equal(source.value,'x+y');
  });
  test(`${surface} formula copy: a pending write suppresses duplicate clicks`,async()=>{
    let finish,attempts=0;
    const f=fixture(surface,{writeText:()=>{attempts++;return new Promise(resolve=>finish=resolve);}});
    const card=f.add('a'),pending=card.click();await card.click();
    assert.equal(attempts,1);assert.equal(card.button.attrs['aria-busy'],'true');
    finish();await pending;assert.equal(card.status.textContent,'Copied');
  });
  test(`${surface} formula copy: a late rejection cannot focus a removed card or change a replacement`,async()=>{
    let reject;
    const f=fixture(surface,{writeText:()=>new Promise((resolve,no)=>reject=no)});
    const old=f.add('old'),pending=old.click();old.controls.parent=null;
    const replacement=f.add('new');reject(Error('denied'));await pending;
    assert.equal(f.document.activeElement,null);assert.equal(old.source(),undefined);
    assert.equal(replacement.status.textContent,'');assert.equal(replacement.source(),undefined);
  });
  test(`${surface} formula copy: missing raw source does not offer a misleading copy button`,()=>{
    const f=fixture(surface,{writeText:()=>assert.fail('No copy expected')});
    for(const value of ['',null,undefined,{},17]) f.add(value);
    assert.equal(f.root.children.length,0);
  });
}
