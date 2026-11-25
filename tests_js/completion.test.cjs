const test = require('node:test');
const assert = require('node:assert/strict');
const identity = require('../extension/bookmark-identity.js');
const createStore = require('../extension/shared-store.js');
const requests = require('../extension/requests.js');
const navigation = require('../extension/pdf-navigation.js');
const fs = require('node:fs');
const vm = require('node:vm');

test('old saved locations are rechecked by identity; unknown parser identities require a current selection', async () => {
  const source = fs.readFileSync(require.resolve('../extension/popup/popup.js'),'utf8');
  const saved = {id:'old',source_file:'main.tex',latex:'g_t=x',source_url:'file:///paper.pdf',occurrence_id:'occ1:'+'a'.repeat(64)};
  let matches = [], jumps=[];
  const queryInput={value:''}, statusEl={textContent:''};
  const context=vm.createContext({documentSession:{current:{},isCurrent:()=>true},queryInput,statusEl,
    setQueryValue:value=>{queryInput.value=value;},document:{getElementById:()=>({value:''})},showView:()=>{},
    runSearch:async()=>{},resultFilter:{get all(){return matches;}},FormulaBookmarkIdentity:identity,
    bookmarkEntry:item=>item,sendScrollToMatch:async item=>jumps.push(item)});
  vm.runInContext(source.slice(source.indexOf('async function recheckSavedFormula('),source.indexOf('let nativeNavigationRevision')),context);
  await context.recheckSavedFormula(saved,{});
  assert.equal(jumps.length,0); assert.match(statusEl.textContent,/needs rechecking/);
  matches=[{...saved,mapping_revision:'source-mapping2',page:27}];
  await context.recheckSavedFormula(saved,{});
  assert.equal(jumps.length,1); assert.equal(jumps[0].page,27);
  matches=[{...matches[0],id:'other',occurrence_id:'occ1:'+'b'.repeat(64)}];
  await context.recheckSavedFormula(saved,{});
  assert.equal(jumps.length,1);
});

test('supported navigation preserves the document, existing zoom, and rejects invalid pages', () => {
  const url = navigation.pageUrl('https://arxiv.org/pdf/2312.00752v2#zoom=125&nameddest=old',27);
  const parsed = new URL(url);
  assert.equal(parsed.pathname,'/pdf/2312.00752v2');
  assert.equal(new URLSearchParams(parsed.hash.slice(1)).get('page'),'28');
  assert.equal(new URLSearchParams(parsed.hash.slice(1)).get('zoom'),'125');
  assert.ok(!parsed.hash.includes('nameddest'));
  for (const page of [-1,NaN,1.5]) assert.throws(()=>navigation.pageUrl('file:///paper.pdf',page));
  assert.throws(()=>navigation.pageUrl('javascript:alert(1)',0));
  assert.equal(navigation.restrictedViewer('Chrome/158.0.8127.37'),true);
  assert.equal(navigation.restrictedViewer('Chromium/151.0.0.0'),false);
});

test('parser-independent anchors reconcile across order updates, with version separation', () => {
  const a = {id:'old', source_url:'https://arxiv.org/pdf/2312.00752v2', occurrence_id:'occ1:'+'a'.repeat(64), occurrence_anchor:'anc1:'+'c'.repeat(64)};
  const b = {...a,id:'new',occurrence_id:'occ1:'+'b'.repeat(64)};
  assert.equal(identity.same(a,b),true);
  assert.equal(identity.same(a,{...b,occurrence_anchor:'anc1:'+'d'.repeat(64)}),false);
  assert.equal(identity.same(a,{...b,source_url:'https://arxiv.org/pdf/2312.00752v1'}),false);
});

test('existing bookmarks acquire a stable anchor only from an identical occurrence', async () => {
  const saved = {id:'old',latex:'x',source_url:'file:///paper.pdf',occurrence_id:'occ1:'+'a'.repeat(64)};
  const data = {fs_bookmarks:[saved]}; let writes=0;
  const store = createStore({get:async key=>structuredClone({[key]:data[key]}),set:async values=>{writes++;Object.assign(data,values);}});
  const matching = {...saved,occurrence_anchor:'anc1:'+'c'.repeat(64)};
  await store.dispatch('bookmarks.reconcile',{entries:[{...matching,occurrence_id:'occ1:'+'b'.repeat(64)}]});
  assert.equal(writes,0);
  await store.dispatch('bookmarks.reconcile',{entries:[matching]});
  assert.equal(data.fs_bookmarks[0].occurrence_anchor,matching.occurrence_anchor);
  assert.equal(data.fs_bookmarks[0].id,'old');
  await store.dispatch('bookmarks.reconcile',{entries:[matching]});
  assert.equal(writes,1);
});

test('a mapper upgrade repairs a saved wrong page once, preserving user fields and later query geometry', async () => {
  const saved = {id:'saved',latex:'g_t=x',source_url:'file:///paper.pdf',occurrence_id:'occ1:'+'a'.repeat(64),page:26,bbox:[1,2,3,4],ts:123,custom:'keep'};
  const data = {fs_bookmarks:[saved]};
  const store = createStore({get:async key=>structuredClone({[key]:data[key]}),set:async values=>Object.assign(data,values)});
  const fresh = {...saved,page:27,bbox:[10,20,30,40],occurrence_anchor:'anc1:'+'c'.repeat(64),mapping_revision:'source-mapping2'};
  await store.dispatch('bookmarks.reconcile',{entries:[fresh]});
  assert.equal(data.fs_bookmarks[0].page,27); assert.equal(data.fs_bookmarks[0].custom,'keep'); assert.equal(data.fs_bookmarks[0].ts,123);
  await store.dispatch('bookmarks.reconcile',{entries:[{...fresh,bbox:[15,20,18,40]}]});
  assert.deepEqual(data.fs_bookmarks[0].bbox,[10,20,30,40]);
});

test('request deadline aborts a stalled connection; caller abort is preserved', async () => {
  const original = global.fetch;
  global.fetch = async (url,{signal})=>new Promise((resolve,reject)=>{
    signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
  });
  const keepAlive = setInterval(()=>{},100);
  try {
    await assert.rejects(requests.request('http://test',{},20),{name:'TimeoutError'});
    const controller = new AbortController();
    const result = requests.request('http://test',{signal:controller.signal},1000);
    controller.abort();
    await assert.rejects(result,{name:'AbortError'});
  } finally {clearInterval(keepAlive);global.fetch=original;}
});
