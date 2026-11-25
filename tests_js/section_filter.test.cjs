const test=require('node:test');
const assert=require('node:assert/strict');
const ResultFilter=require('../extension/result-filter.js');

test('section selection narrows page choices and retains original occurrence objects for export',()=>{
  const all=[{page:1,section:'Intro',id:'a'},{page:2,section:'Methods',id:'b'},
    {page:1,section:'Methods',id:'c'},{page:9,section:'Intro',id:'d'}];
  const f=new ResultFilter();f.set(all);f.select('page:9');
  assert.deepEqual(f.selectSection('section:Methods'),[all[1],all[2]]);
  assert.equal(f.page,'all');assert.equal(f.all,all);
  assert.deepEqual(f.groups,[{key:'page:1',count:1},{key:'page:2',count:1}]);
  assert.equal(f.sectionMatches.length,2);
  assert.deepEqual(f.select('page:1'),[all[2]]);assert.equal(f.matches[0],all[2]);
  assert.deepEqual(f.selectSection('all'),all);assert.equal(f.matches,all);
});
test('unlabelled and unlocated results remain selectable without colliding with real heading names',()=>{
  const all=[{section:null},{section:''},{section:'   ',page:3},{section:'No section',page:3},
    {section:'all',page:1},{section:'unknown',page:4},{section:' Intro ',page:2}];
  const f=new ResultFilter();f.set(all);
  assert.deepEqual(f.selectSection('unknown'),all.slice(0,3));
  assert.deepEqual(f.select('unknown'),all.slice(0,2));
  assert.deepEqual(f.selectSection('section:No section'),[all[3]]);
  assert.deepEqual(f.selectSection('section:all'),[all[4]]);
  assert.deepEqual(f.selectSection('section:unknown'),[all[5]]);
  assert.deepEqual(f.selectSection('section:Intro'),[all[6]]);
});
test('a repeated query retains a valid section/page and missing sections reset both scopes',()=>{
  const f=new ResultFilter();f.set([{section:'A',page:2},{section:'B',page:3}]);
  f.selectSection('section:A');f.select('page:2');f.clear({keepPage:true});
  assert.deepEqual([f.all,f.sectionMatches,f.groups,f.sectionGroups],[[],[],[],[]]);
  f.set([{section:'A',page:2},{section:'A',page:4},{section:'B',page:3}]);
  assert.equal(f.section,'section:A');assert.equal(f.page,'page:2');assert.equal(f.matches.length,1);
  f.set([{section:'B',page:2},{section:'C',page:3}]);
  assert.equal(f.section,'all');assert.equal(f.page,'all');assert.equal(f.matches.length,2);
});
test('document clear and restored section/page survive loading without leaking previous records',()=>{
  const f=new ResultFilter();f.set([{section:'old',page:1}]);f.selectSection('section:old');f.clear();
  assert.equal(f.section,'all');assert.equal(f.page,'all');
  f.restoreSection('section:Methods');f.restorePage('page:4');f.clear({keepPage:true});
  f.set([{section:'Methods',page:2},{section:'Methods',page:4},{section:'Elsewhere',page:4}]);
  assert.deepEqual(f.matches,[f.all[1]]);
});
test('empty responses clear every group and both scopes',()=>{
  const f=new ResultFilter();f.set([{section:'A',page:3},{section:'B',page:4}]);
  f.selectSection('section:B');f.select('page:4');f.set([]);
  assert.deepEqual([f.all,f.matches,f.sectionMatches,f.groups,f.sectionGroups],[[],[],[],[],[]]);
  assert.equal(f.section,'all');assert.equal(f.page,'all');
});
test('malformed stored section values are ignored and Unicode headings stay distinct',()=>{
  const f=new ResultFilter();
  for(const key of [null,undefined,{},42,'','section:','page:3','section:   ']) {
    f.restoreSection(key);assert.equal(f.section,'all');
  }
  f.restoreSection('section:付録 α');assert.equal(f.section,'section:付録 α');
  f.set([{section:'付録 α',page:1},{section:'付録 Α',page:2}]);assert.equal(f.matches.length,1);
});
test('all 490 matrix-calculus x occurrences partition into sections and pages without losing order or data',()=>{
  // Keep only layout/identity metadata in the public fixture; no paper text.
  const all=require('../testdata/section-filter-occurrences.json').results;
  assert.equal(all.length,490);
  const original=JSON.stringify(all),f=new ResultFilter();f.set(all);
  assert.equal(f.sectionGroups.length,23);
  assert.equal(f.sectionGroups.reduce((sum,g)=>sum+g.count,0),490);
  const seen=[];
  for(const section of f.sectionGroups) {
    f.selectSection(section.key);assert.equal(f.sectionMatches.length,section.count);
    assert.equal(f.groups.reduce((sum,g)=>sum+g.count,0),section.count);
    const expected=all.filter(m=>ResultFilter.sectionKey(m)===section.key);
    assert.deepEqual(f.sectionMatches,expected);
    for(const page of f.groups) {f.select(page.key);seen.push(...f.matches.map(m=>m.occurrence_id));}
  }
  assert.deepEqual(seen.sort(),all.map(m=>m.occurrence_id).sort());
  assert.equal(JSON.stringify(f.all),original);
});
