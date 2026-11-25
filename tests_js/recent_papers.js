globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

// recordRecentPaper() (loaded below from the "RECENT PAPERS (popup outer)"
// marker block) now dedupes on documentKey(url), not raw URL equality --
// see Finding #5, batch-A final review: /abs/X and /pdf/X for the same
// arXiv paper used to count as two separate recent-papers entries.
// documentKey is an ordinary top-level declaration elsewhere in popup.js
// (outside the RECENT PAPERS marker block this test loads via --block), so
// this test provides a minimal stand-in matching production semantics for
// arXiv URLs (the only case exercised below).
function documentKey(url) {
  const m = /arxiv\.org\/(?:pdf|abs|src)\/([A-Za-z-]+(?:\.[A-Z]{2})?\/\d{7}(?:v\d+)?|[0-9]{4}\.[0-9]{4,5}(?:v\d+)?)(?:\.pdf)?/i.exec(url || '');
  if (m) return `arxiv:${m[1]}`;
  return (url || '').split('#', 1)[0].trim();
}

loadRecentPapers().then((papers) => {
  assert(Array.isArray(papers) && papers.length === 0, 'recent papers start empty, got ' + JSON.stringify(papers));
  return recordRecentPaper('https://arxiv.org/abs/1706.03762', 42);
}).then(() => loadRecentPapers())
  .then((papers) => {
    assert(papers.length === 1, 'recordRecentPaper adds an entry, got length ' + papers.length);
    assert(papers[0].url === 'https://arxiv.org/abs/1706.03762', 'stores the url, got ' + papers[0].url);
    assert(papers[0].formulaCount === 42, 'stores the formula count, got ' + papers[0].formulaCount);
    return recordRecentPaper('https://arxiv.org/abs/2506.01320', 10);
  }).then(() => loadRecentPapers())
  .then((papers) => {
    assert(papers.length === 2, 'a second distinct paper adds a second entry, got length ' + papers.length);
    assert(papers[0].url === 'https://arxiv.org/abs/2506.01320', 'most recently opened paper is first, got ' + papers[0].url);
    return recordRecentPaper('https://arxiv.org/abs/1706.03762', 45); // re-open the first paper
  }).then(() => loadRecentPapers())
  .then((papers) => {
    assert(papers.length === 2, 're-opening an existing paper updates it in place rather than duplicating, got length ' + papers.length);
    assert(papers[0].url === 'https://arxiv.org/abs/1706.03762', 're-opening moves the paper back to the front, got ' + papers[0].url);
    assert(papers[0].formulaCount === 45, 're-opening updates the stored formula count, got ' + papers[0].formulaCount);
    return recordRecentPaper('https://arxiv.org/pdf/1706.03762', 45); // same paper, opened via the PDF URL form instead of /abs/
  }).then(() => loadRecentPapers())
  .then((papers) => {
    assert(papers.length === 2, '/abs/X and /pdf/X for the same arXiv paper dedupe to one entry, got length ' + papers.length);
    assert(papers[0].url === 'https://arxiv.org/pdf/1706.03762', 'dedupe keeps the most recently recorded url form, got ' + papers[0].url);

    const many = [];
    for (let i = 0; i < 20; i += 1) many.push(() => recordRecentPaper('https://example.com/paper' + i, i));
    return many.reduce((p, fn) => p.then(fn), Promise.resolve());
  }).then(() => loadRecentPapers())
  .then((papers) => {
    assert(papers.length === 15, 'recent papers are capped at 15, got length ' + papers.length);
  });
