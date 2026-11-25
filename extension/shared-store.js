/* All read/modify/write operations live in the service worker. Queues are per
 * storage key, so an unrelated bookmark save needn't wait for note history. */
(function (root) {
  'use strict';
  const identity = root.FormulaDocumentIdentity || (typeof require === 'function' ? require('./document-identity.js') : null);
  const bookmarks = root.FormulaBookmarkIdentity || (typeof require === 'function' ? require('./bookmark-identity.js') : null);
  const backup = root.FormulaBookmarkBackup || (typeof require === 'function' ? require('./bookmark-backup.js') : null);
  function createFormulaStore(storage) {
    const queues = new Map();
    const keys = {
      notes: 'fs_chat_history', bookmarks: 'fs_bookmarks', history: 'fs_search_history',
      recent: 'fs_recent_papers', seen: 'fs_chat_last_seen',
    };
    function ordered(key, task) {
      const result = (queues.get(key) || Promise.resolve()).then(task);
      const tail = result.then(() => {}, () => {});
      queues.set(key, tail);
      tail.then(() => { if (queues.get(key) === tail) queues.delete(key); });
      return result;
    }
    async function list(key) {
      const stored = await storage.get(key);
      if (stored[key] === undefined) return [];
      if (!Array.isArray(stored[key])) throw new Error('Saved data has an unexpected format; it was left unchanged.');
      return stored[key];
    }
    function text(value, name, max) {
      if (typeof value !== 'string' || !value.trim() || value.length > max) {
        throw new Error(`${name} must contain 1–${max} characters.`);
      }
      return value;
    }
    function documentId(url) {
      return identity.documentKey(url);
    }
    async function dispatch(action, data = {}) {
      const [kind, operation] = String(action).split('.');
      const key = keys[kind];
      if (!key) throw new Error('Unknown storage request.');
      return ordered(key, async () => {
        if (kind === 'seen' && operation === 'read') return Number((await storage.get(key))[key]) || 0;
        if (kind === 'seen' && operation === 'advance') {
          if (!Number.isFinite(data.timestamp) || data.timestamp < 0) throw new Error('Invalid timestamp.');
          const stored = await storage.get(key);
          const value = Math.max(Number(stored[key]) || 0, data.timestamp);
          await storage.set({[key]: value});
          return value;
        }
        const items = await list(key);
        if (operation === 'list') return items;
        let next, result;
        if (kind === 'notes') {
          if (operation === 'clear') next = [];
          else if (operation === 'append') {
            const message = text(data.text, 'Note', 20000);
            const settings = (await storage.get('fs_settings')).fs_settings || {};
            const limit = Number.isFinite(settings.maxChatHistory) && settings.maxChatHistory > 0
              ? Math.max(1, Math.min(5000, Math.floor(settings.maxChatHistory))) : 500;
            const previous = items.reduce((max, item) => Math.max(max, Number.isFinite(item.timestamp) ? item.timestamp : 0), 0);
            const timestamp = Math.max(Date.now(), previous + 1);
            next = items.concat({id: `${timestamp}-${Math.random().toString(36).slice(2)}`,
              role: 'user', text: message, timestamp, context: data.context || null}).slice(-limit);
          }
        } else if (kind === 'bookmarks') {
          if (operation === 'reconcile') {
            if (!Array.isArray(data.entries) || data.entries.length > 10000) throw new Error('Invalid bookmark refresh.');
            const entries = data.entries.filter(entry => /^anc1:[a-f0-9]{64}$/.test(entry.occurrence_anchor || ''));
            const byKey = new Map(entries.map(entry => [bookmarks.key(entry),entry]));
            const byAnchor = new Map();
            const anchorKey = entry => `${bookmarks.documentKey(entry)}::${entry.occurrence_anchor}`;
            for (const entry of entries) {
              const key = anchorKey(entry);
              byAnchor.set(key,byAnchor.has(key) ? null : entry);
            }
            let changed = false;
            next = items.map(item => {
              const entry = byKey.get(bookmarks.key(item)) || (item.occurrence_anchor && byAnchor.get(anchorKey(item)));
              if (!entry) return item;
              const update = {occurrence_anchor:entry.occurrence_anchor};
              // Refresh location once after a mapper upgrade. Repeated queries
              // must not replace a saved rectangle with each query's subspan.
              if (entry.mapping_revision && entry.mapping_revision !== item.mapping_revision) {
                for (const field of ['mapping_revision','occurrence_id','page','bbox','page_w','page_h','location_status','bbox_source']) {
                  if (entry[field] !== undefined) update[field] = entry[field];
                }
              }
              if (Object.keys(update).every(key => JSON.stringify(item[key]) === JSON.stringify(update[key]))) return item;
              changed = true; return {...item,...update};
            });
            if (!changed) return items;
          } else if (operation === 'toggle') {
            const entry = data.entry;
            text(entry?.id, 'Bookmark ID', 16000);
            text(entry?.latex, 'Formula', 100000);
            const exists = items.some(item => bookmarks.same(item, entry));
            next = exists ? items.filter(item => !bookmarks.same(item, entry)) : [{...entry, ts: Date.now()}, ...items].slice(0, 200);
            result = !exists;
          } else if (operation === 'remove') next = items.filter(item => item.id !== data.id);
          else if (operation === 'import') {
            const merged = backup.merge(items, backup.parse(data.text));
            result = {added: merged.added, duplicates: merged.duplicates, total: merged.total};
            if (!merged.added) return result;
            next = merged.items;
          }
        } else if (kind === 'history' && operation === 'record') {
          const query = text(data.query, 'Query', 2048);
          next = [{query, ts: Date.now()}, ...items.filter(item => item.query !== query)].slice(0, 20);
        } else if (kind === 'recent' && operation === 'record') {
          const entry = data.entry;
          text(entry?.url, 'Paper URL', 16000);
          next = [{...entry, lastOpened: Date.now()}, ...items.filter(item => documentId(item.url) !== documentId(entry.url))].slice(0, 15);
        }
        if (!next) throw new Error('Unknown storage operation.');
        await storage.set({[key]: next});
        return result === undefined ? next : result;
      });
    }
    return {dispatch};
  }
  root.createFormulaStore = createFormulaStore;
  if (typeof module !== 'undefined') module.exports = createFormulaStore;
})(globalThis);
