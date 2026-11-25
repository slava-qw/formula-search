globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

const el = document.createElement('div');
el.id = 'probe';
el.className = 'a b';
document.body.appendChild(el);
assert(document.getElementById('probe') === el, 'getElementById finds an appended element by id');
assert(el.classList.contains('a') && el.classList.contains('b'), 'className populates classList');
assert(document.querySelector('.a') === el, 'querySelector matches a class selector');
el.textContent = 'hello';
assert(el.textContent === 'hello', 'textContent round-trips');

chrome.storage.local.set({ probeKey: 'v1' })
  .then(() => chrome.storage.local.get('probeKey'))
  .then((result) => {
    assert(result.probeKey === 'v1', 'chrome.storage.local set/get round-trips, got ' + JSON.stringify(result));
  });

let sawChange = null;
chrome.storage.onChanged.addListener((changes) => { sawChange = changes; });
chrome.storage.local.set({ probeKey2: 'v2' }).then(() => {
  assert(!!sawChange && sawChange.probeKey2 && sawChange.probeKey2.newValue === 'v2', 'onChanged fires with the new value, got ' + JSON.stringify(sawChange));
});
