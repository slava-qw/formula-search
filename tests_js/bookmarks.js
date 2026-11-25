globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

// bookmarkKey() (loaded below from the "BOOKMARKS (popup outer)" marker
// block) now incorporates document identity via documentKey(_sourceUrl) --
// see Finding #1, batch-A final review: bookmarks used to be document-blind
// (`${page}::${latex}`), so two different papers with the same formula on
// the same page collided on one bookmark id. Both documentKey and
// _sourceUrl are ordinary top-level declarations elsewhere in popup.js
// (outside the BOOKMARKS marker block this test loads via --block), so this
// test provides minimal stand-ins here, matching production semantics for
// arXiv URLs (the only case exercised below).
function documentKey(url) {
  const m = /arxiv\.org\/(?:pdf|abs|src)\/([A-Za-z-]+(?:\.[A-Z]{2})?\/\d{7}(?:v\d+)?|[0-9]{4}\.[0-9]{4,5}(?:v\d+)?)(?:\.pdf)?/i.exec(url || '');
  if (m) return `arxiv:${m[1]}`;
  return (url || '').split('#', 1)[0].trim();
}
globalThis._sourceUrl = 'https://arxiv.org/abs/1706.03762';

const sampleMatch = { page: 4, latex: '\\frac{d}{dx}', label: 'eq:1', section: 'Intro', bbox: [1, 2, 3, 4], page_w: 600, page_h: 800 };

loadBookmarks().then((bookmarks) => {
  assert(Array.isArray(bookmarks) && bookmarks.length === 0, 'bookmarks start empty, got ' + JSON.stringify(bookmarks));
  return toggleBookmark(sampleMatch);
}).then((bookmarked) => {
  assert(bookmarked === true, 'toggleBookmark returns true when adding a new bookmark, got ' + bookmarked);
  return loadBookmarks();
}).then((bookmarks) => {
  assert(bookmarks.length === 1, 'bookmark was persisted, got length ' + bookmarks.length);
  assert(bookmarks[0].id === bookmarkKey(sampleMatch), 'bookmark id matches bookmarkKey(match), got ' + bookmarks[0].id);
  assert(bookmarks[0].id.startsWith('arxiv:1706.03762::location1:'), 'legacy backend fallback key incorporates document identity and location, got ' + bookmarks[0].id);
  assert(bookmarks[0].latex === sampleMatch.latex, 'bookmark stores the latex field');
  assert(bookmarks[0].page === 4, 'bookmark stores the page field');
  assert(bookmarks[0].source_url === _sourceUrl, 'bookmark stores source_url, got ' + bookmarks[0].source_url);

  // Same page/latex, but from a different paper -- must NOT collide with
  // the bookmark above (this is the exact bug Finding #1 describes).
  globalThis._sourceUrl = 'https://arxiv.org/abs/2506.01320';
  return toggleBookmark(sampleMatch);
}).then((bookmarked) => {
  assert(bookmarked === true, 'the same page/latex bookmarked from a different paper adds a new entry rather than colliding, got ' + bookmarked);
  return loadBookmarks();
}).then((bookmarks) => {
  assert(bookmarks.length === 2, 'bookmarks from two different papers do not collide, got length ' + bookmarks.length);
  const ids = bookmarks.map((b) => b.id);
  assert(new Set(ids).size === 2, 'bookmark ids differ across documents, got ' + JSON.stringify(ids));

  // Clean up both entries (toggle each off) before the cap test below.
  return toggleBookmark(sampleMatch); // removes the paper-B entry (_sourceUrl still paper B)
}).then((bookmarked) => {
  assert(bookmarked === false, 'toggling the paper-B bookmark again removes it, got ' + bookmarked);
  globalThis._sourceUrl = 'https://arxiv.org/abs/1706.03762';
  return toggleBookmark(sampleMatch); // removes the paper-A entry
}).then((bookmarked) => {
  assert(bookmarked === false, 'toggling the paper-A bookmark again removes it, got ' + bookmarked);
  return loadBookmarks();
}).then((bookmarks) => {
  assert(bookmarks.length === 0, 'bookmarks were removed, got length ' + bookmarks.length);

  const many = [];
  for (let i = 0; i < 205; i += 1) many.push(() => toggleBookmark({ page: i, latex: 'x' + i, label: '', section: '', bbox: null, page_w: 0, page_h: 0 }));
  return many.reduce((p, fn) => p.then(fn), Promise.resolve());
}).then(() => loadBookmarks())
  .then((bookmarks) => {
    assert(bookmarks.length === 200, 'bookmarks are capped at 200, got length ' + bookmarks.length);
  });
