/* Shared by result stars and the service worker's serialized bookmark writes. */
(function (root) {
  'use strict';
  const documentIdentity = root.FormulaDocumentIdentity || (typeof require === 'function' ? require('./document-identity.js') : null);
  const occurrence = item => typeof item?.occurrence_id === 'string' && /^occ1:[a-f0-9]{64}$/.test(item.occurrence_id) ? item.occurrence_id : '';
  const documentKey = (item, fallback = '') => documentIdentity.documentKey(item.resolved_source_url || item.source_url || fallback);
  const rectangle = item => Array.isArray(item?.bbox) && item.bbox.length === 4 &&
    item.bbox.every(Number.isFinite) && item.bbox[2] > item.bbox[0] && item.bbox[3] > item.bbox[1]
    ? item.bbox : null;
  function key(item, fallback = '') {
    const doc = documentKey(item, fallback);
    const id = occurrence(item);
    if (id) return `${doc}::${id}`;
    // Older backends lack an occurrence ID. Distinguish known locations, but
    // cannot promise stable identity across their query-specific rectangles.
    return `${doc}::location1:${JSON.stringify([item.page, item.latex || '', item.source_file || '',
      item.source_line || 0, rectangle(item)])}`;
  }
  function same(saved, entry) {
    const savedDocument = documentKey(saved), entryDocument = documentKey(entry);
    if (saved.id === entry.id && (!savedDocument || !entryDocument || savedDocument === entryDocument)) return true;
    if (savedDocument && savedDocument === entryDocument && occurrence(saved) && occurrence(saved) === occurrence(entry)) return true;
    if (documentKey(saved) && documentKey(saved) === documentKey(entry) &&
        /^anc1:[a-f0-9]{64}$/.test(saved.occurrence_anchor || '') &&
        saved.occurrence_anchor === entry.occurrence_anchor) return true;
    if (occurrence(saved) && occurrence(entry)) return false;
    if (!documentKey(saved) || documentKey(saved) !== documentKey(entry) ||
        saved.page !== entry.page || saved.latex !== entry.latex) return false;
    // Only associate a legacy record when its stored rectangle agrees exactly.
    // Source lines alone can contain repeated identical formulas/macro calls.
    // Unknown/ambiguous bookmarks stay visible in the saved list, untouched.
    const a = rectangle(saved), b = rectangle(entry);
    if (!a || !b || !a.every((value, i) => value === b[i])) return false;
    if (saved.source_file && entry.source_file && saved.source_file !== entry.source_file) return false;
    if (saved.source_line && entry.source_line && saved.source_line !== entry.source_line) return false;
    return true;
  }
  root.FormulaBookmarkIdentity = {key, same, documentKey};
  if (typeof module !== 'undefined') module.exports = root.FormulaBookmarkIdentity;
})(globalThis);
