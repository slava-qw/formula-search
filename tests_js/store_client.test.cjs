const test = require('node:test');
const assert = require('node:assert/strict');
const request = require('../extension/store-client.js');

test('store client accepts acknowledged values and supports external PDF calls', async () => {
  const calls = [];
  global.chrome = {runtime: {sendMessage: (...args) => {
    calls.push(args);
    args.at(-1)({ok: true, value: ['saved']});
  }}};
  assert.deepEqual(await request('notes.list'), ['saved']);
  assert.deepEqual(await request('notes.append', {text: 'hello'}, 'our-extension'), ['saved']);
  assert.equal(calls[0][0].type, 'storeRequest');
  assert.equal(calls[1][0], 'our-extension');
  assert.deepEqual(calls[1][1].data, {text: 'hello'});
});

test('missing acknowledgement, rejected save and disconnected worker are failures, never fabricated saved notes', async () => {
  global.chrome = {runtime: {sendMessage: (message, callback) => callback()}};
  await assert.rejects(request('notes.append', {text: 'keep me'}), /Could not save/);
  chrome.runtime.sendMessage = (message, callback) => callback({ok: false, error: 'disk full'});
  await assert.rejects(request('notes.append', {text: 'keep me'}), /disk full/);
  chrome.runtime.lastError = {message: 'context invalidated'};
  await assert.rejects(request('notes.list'), /context invalidated/);
  delete global.chrome;
  await assert.rejects(request('notes.append', {text: 'keep me'}), /not been saved/);
});
