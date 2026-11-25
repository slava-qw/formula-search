/* Versioned bookmark files. Validation and merge also run in the single writer. */
(function (root) {
  'use strict';
  const identity = root.FormulaBookmarkIdentity || (typeof require === 'function' ? require('./bookmark-identity.js') : null);
  const FORMAT = 'formula-search-bookmarks';
  const VERSION = 1;
  const MAX_ITEMS = 200;
  const MAX_BYTES = 8 * 1024 * 1024;
  function checkSize(text) {
    if (typeof text !== 'string' || text.length > MAX_BYTES || new TextEncoder().encode(text).length > MAX_BYTES) {
      throw new Error('Choose a bookmark backup smaller than 8 MiB.');
    }
  }
  function validate(items) {
    if (!Array.isArray(items) || items.length > MAX_ITEMS) throw new Error('A backup can contain at most 200 bookmarks.');
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const invalid = field => { throw new Error(`Bookmark ${i + 1} has an invalid ${field}.`); };
      if (!item || typeof item !== 'object' || Array.isArray(item)) invalid('record');
      for (const [field, max] of Object.entries({id: 16000, latex: 100000, label: 16000, section: 16000,
        source_url: 16000, resolved_source_url: 16000, source_file: 16000,
        location_status: 100, bbox_source: 100, source_location_kind: 100, occurrence_id: 69, occurrence_anchor: 69, mapping_revision:100})) {
        const value = item[field];
        if ((field === 'id' || field === 'latex') && (typeof value !== 'string' || !value.trim())) invalid(field);
        if (value !== undefined && (typeof value !== 'string' || value.length > max)) invalid(field);
      }
      for (const field of ['source_url', 'resolved_source_url']) {
        if (!item[field]) continue; // Older saved formulas can lack a paper URL.
        let url;
        try { url = new URL(item[field]); } catch { invalid(field); }
        if (!['https:', 'http:', 'file:'].includes(url.protocol)) invalid(field);
      }
      if (item.occurrence_id && !/^occ1:[a-f0-9]{64}$/.test(item.occurrence_id)) invalid('occurrence_id');
      if (item.occurrence_anchor && !/^anc1:[a-f0-9]{64}$/.test(item.occurrence_anchor)) invalid('occurrence_anchor');
      for (const [field, min] of [['page', -1], ['source_line', 0], ['ts', 0]]) {
        if (item[field] !== undefined && (!Number.isSafeInteger(item[field]) || item[field] < min)) invalid(field);
      }
      for (const field of ['page_w', 'page_h']) {
        if (item[field] !== undefined && (!Number.isFinite(item[field]) || item[field] < 0)) invalid(field);
      }
      if (item.bbox !== undefined && item.bbox !== null) {
        const b = item.bbox;
        if (!Array.isArray(b) || b.length !== 4 || !b.every(Number.isFinite) ||
            b[2] <= b[0] || b[3] <= b[1] || !(item.page >= 0) || !(item.page_w > 0) || !(item.page_h > 0)) invalid('bbox');
        if (['estimated', 'ambiguous'].includes(item.location_status)) invalid('location status for a precise box');
      }
    }
    return items;
  }
  function parse(text) {
    checkSize(text);
    let file;
    try { file = JSON.parse(text.replace(/^\uFEFF/, '')); }
    catch { throw new Error('This file is not valid JSON. Choose a Formula Search bookmark backup.'); }
    if (!file || file.format !== FORMAT) throw new Error('Choose a Formula Search bookmark backup, not a search-results export.');
    if (file.version !== VERSION) throw new Error('This bookmark backup version is not supported.');
    return validate(file.bookmarks);
  }
  function serialize(items, date = new Date()) {
    validate(items);
    const text = JSON.stringify({format: FORMAT, version: VERSION, created_at: date.toISOString(), bookmarks: items}, null, 2);
    checkSize(text);
    return text;
  }
  function merge(current, incoming) {
    validate(incoming);
    const next = current.slice();
    let added = 0, duplicates = 0;
    for (const entry of incoming) {
      const exists = next.some(saved => identity.same(saved, entry) ||
        (saved.occurrence_id && entry.occurrence_id && identity.key(saved) === identity.key(entry)));
      if (exists) { duplicates++; continue; }
      next.push(entry);
      added++;
    }
    if (next.length > MAX_ITEMS) {
      throw new Error(`Restoring would exceed 200 bookmarks (${current.length} saved, ${added} new). Remove some bookmarks and try again. Nothing was imported.`);
    }
    return {items: next, added, duplicates, total: next.length};
  }
  root.FormulaBookmarkBackup = {parse, serialize, merge, MAX_BYTES, MAX_ITEMS};
  if (typeof module !== 'undefined') module.exports = root.FormulaBookmarkBackup;
})(globalThis);
