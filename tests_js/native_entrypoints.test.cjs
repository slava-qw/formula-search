const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture(content, viewer) {
  let listener, mutations = 0;
  const controller = {destroy() {mutations++;}};
  const context = vm.createContext({
    document: {querySelector: selector => selector === 'pdf-viewer' ? viewer : null,
      querySelectorAll: () => [], body: {dispatchEvent() {mutations++;}}},
    chrome: {runtime: {onMessage: {addListener(fn) {listener = fn;}}}},
    __formulaSearchInfoPanelController: controller,
    __formulaSearchContentInfoPanelController: controller,
  });
  const read = name => fs.readFileSync(path.join(__dirname, '../extension', name), 'utf8');
  vm.runInContext(read('native-viewer-state.js'), context);
  if (content) vm.runInContext(read('content.js'), context);
  else {
    const source = read('popup/popup.js'), start = source.indexOf('function nativePdfInstantJump(');
    vm.runInContext(source.slice(start, source.indexOf('\nfunction normalizeInjectionResult', start)), context);
  }
  return {
    mutations: () => mutations,
    jump: payload => content ? new Promise(resolve => listener({type:'scrollToFormula', ...payload}, {}, resolve))
      : context.nativePdfInstantJump(payload),
  };
}
for (const content of [false, true]) {
  test(`${content ? 'content' : 'scripting/debugger'} entrypoint leaves existing UI and navigation untouched while loading`, async () => {
    const f = fixture(content, {loadState_:'loading'});
    const response = await f.jump({page:1,showInfoPanel:true});
    assert.equal(response.ok,false); assert.equal(response.code,'viewer-loading');
    assert.equal(f.mutations(),0);
  });
  test(`${content ? 'content' : 'scripting/debugger'} entrypoint rejects a changed document and unavailable page before fallback events`, async () => {
    const f = fixture(content, {originalUrl:'file:///paper.pdf',loadState_:'success',
      viewport:{},documentDimensions:{pageDimensions:[{}]}});
    assert.equal((await f.jump({page:0,navigationUrl:'file:///other.pdf'})).code,'document-changed');
    assert.equal((await f.jump({page:9,navigationUrl:'file:///paper.pdf'})).code,'page-unavailable');
    assert.equal(f.mutations(),0);
  });
}

test('an older resident content bridge without readiness checks cannot claim a navigation', async () => {
  const source=fs.readFileSync(path.join(__dirname,'../extension/popup/popup.js'),'utf8');
  const start=source.indexOf('async function ensureNativeContentScript(');
  const code=source.slice(start,source.indexOf('\nasync function executeNativeJumpViaContentBridge',start));
  for (const current of [false,true]) {
    let injections=0;
    const context=vm.createContext({chrome:{
      tabs:{sendMessage:async()=>({ok:true,...(current?{nativeReadinessVersion:1}:{})})},
      scripting:{executeScript:async()=>{injections++;}},
    }});
    vm.runInContext(code,context);
    assert.equal(await context.ensureNativeContentScript(7),current);
    assert.equal(injections,current?0:1);
  }
});
