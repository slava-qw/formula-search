const test = require('node:test');
const assert = require('node:assert/strict');
const {waitForCommittedUrl} = require('../extension/pdf-navigation.js');

test('PDF reload barrier waits through a pending URL and incomplete viewer load', async () => {
  const target='file:///paper.pdf#page=2';
  const states=[
    {url:'file:///paper.pdf#page=1',pendingUrl:target,status:'complete'},
    {url:target,status:'loading'},
    {url:target,status:'complete'},
  ];
  let reads=0;
  assert.equal(await waitForCommittedUrl(7,target,{tabs:{get:async id=>{
    assert.equal(id,7);return states[reads++];
  }}}),true);
  assert.equal(reads,3,'must not allow reload against the old committed URL');
});

test('PDF commit barrier stops when the document request becomes stale', async () => {
  let current=true,reads=0;
  const result=await waitForCommittedUrl(7,'file:///paper.pdf#page=2',{
    isCurrent:()=>current,tabs:{get:async()=>{reads++;current=false;return {url:'file:///paper.pdf#page=2',status:'complete'};}},
  });
  assert.equal(result,false);assert.equal(reads,1);
});

test('PDF commit barrier preserves caller abort and has a bounded deadline', async () => {
  const controller=new AbortController();controller.abort();
  const tabs={get:async()=>assert.fail('must not read after cancellation')};
  await assert.rejects(waitForCommittedUrl(7,'file:///paper.pdf',{signal:controller.signal,tabs}),{name:'AbortError'});
  await assert.rejects(waitForCommittedUrl(7,'file:///paper.pdf',{timeoutMs:0,tabs}),/did not finish opening/);
});
