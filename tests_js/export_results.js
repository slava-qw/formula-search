globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

const sampleMatches = [
  { score: 0.9, match_type: 'exact', latex: '\\alpha', label: '', section: 'Intro', source: 'text', page: 2, bbox: [1, 2, 3, 4], bbox_source: 'candidate', page_w: 600, page_h: 800 },
  { score: 0.5, match_type: 'fuzzy', latex: '\\beta', label: '', section: '', source: 'text', page: -1, bbox: null, bbox_source: 'candidate', page_w: 0, page_h: 0 },
];

const payload = buildExportPayload(sampleMatches);
assert(typeof payload === 'string', 'buildExportPayload returns a string, got ' + typeof payload);
const parsed = JSON.parse(payload);
assert(Array.isArray(parsed) && parsed.length === 2, 'the payload round-trips through JSON.parse as an array of the same length, got ' + JSON.stringify(parsed).slice(0, 100));
assert(parsed[0].latex === '\\alpha' && parsed[1].latex === '\\beta', 'field values are preserved, got ' + JSON.stringify(parsed.map((m) => m.latex)));
assert(parsed[0].page === 2 && parsed[1].page === -1, 'the page field (including the -1 "no page map" sentinel) is preserved, got ' + JSON.stringify(parsed.map((m) => m.page)));

const emptyPayload = buildExportPayload([]);
assert(JSON.parse(emptyPayload).length === 0, 'an empty matches array serializes to an empty JSON array, got ' + emptyPayload);
