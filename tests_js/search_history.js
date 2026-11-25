globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

loadSearchHistory().then((history) => {
  assert(Array.isArray(history) && history.length === 0, 'search history starts empty, got ' + JSON.stringify(history));
  return saveSearchHistoryEntry('\\frac{d}{dx}');
}).then(() => saveSearchHistoryEntry('\\alpha'))
  .then(() => loadSearchHistory())
  .then((history) => {
    assert(history.length === 2, 'saveSearchHistoryEntry adds entries, got length ' + history.length);
    assert(history[0].query === '\\alpha', 'most recent query is first, got ' + history[0].query);
    return saveSearchHistoryEntry('\\frac{d}{dx}'); // re-run an existing query
  }).then(() => loadSearchHistory())
  .then((history) => {
    assert(history.length === 2, 're-running an existing query does not duplicate it, got length ' + history.length);
    assert(history[0].query === '\\frac{d}{dx}', 're-running an existing query moves it to the front, got ' + history[0].query);

    // Deferred as thunks (not invoked eagerly) so the reduce below runs them
    // strictly one at a time -- each save's internal read-modify-write cycle
    // must fully finish (including its own chrome.storage.local.set) before
    // the next one starts reading, or they race and clobber each other.
    const many = [];
    for (let i = 0; i < 25; i += 1) many.push(() => saveSearchHistoryEntry('q' + i));
    return many.reduce((p, fn) => p.then(fn), Promise.resolve());
  }).then(() => loadSearchHistory())
  .then((history) => {
    assert(history.length === 20, 'search history is capped at 20 entries, got length ' + history.length);
  });
