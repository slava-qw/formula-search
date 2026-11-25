/* The UI only reads a user-selected file; the worker validates and merges it. */
(function (root) {
  'use strict';
  function bind({exportButton, importButton, input, status, count, store,
    backup = root.FormulaBookmarkBackup, downloads = root.chrome?.downloads,
    runtime = root.chrome?.runtime, urls = root.URL}) {
    let busy = false, savedCount = 0;
    function update() {
      exportButton.disabled = busy || !savedCount;
      importButton.disabled = busy;
      input.disabled = busy;
      count.textContent = `${savedCount} saved`;
      exportButton.setAttribute('aria-busy', String(busy));
      importButton.setAttribute('aria-busy', String(busy));
    }
    async function run(task) {
      if (busy) return;
      busy = true;
      status.textContent = '';
      status.removeAttribute('data-error');
      update();
      try { await task(); }
      catch (error) {
        status.textContent = error.message || 'The bookmark operation failed. Try again.';
        status.setAttribute('data-error', 'true');
      } finally { busy = false; update(); }
    }
    exportButton.addEventListener('click', () => run(async () => {
      const items = await store('bookmarks.list');
      const text = backup.serialize(items);
      let url;
      try {
        url = urls.createObjectURL(new Blob([text], {type: 'application/json'}));
        await new Promise((resolve, reject) => {
          downloads.download({url, filename: 'formula-search-bookmarks.json', saveAs: true}, () => {
            const error = runtime.lastError;
            if (error) reject(new Error('Backup download was not started. Try again.'));
            else resolve();
          });
        });
        status.textContent = `Backup download requested for ${items.length} bookmark${items.length === 1 ? '' : 's'}.`;
      } finally { if (url) urls.revokeObjectURL(url); }
    }));
    importButton.addEventListener('click', () => { if (!busy) input.click(); });
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      input.value = ''; // Permit retrying the same file after an error.
      if (!file) return;
      run(async () => {
        if (file.size > backup.MAX_BYTES) throw new Error('Choose a bookmark backup smaller than 8 MiB.');
        let text;
        try { text = await file.text(); }
        catch { throw new Error('The backup file could not be read. Choose it again.'); }
        const result = await store('bookmarks.import', {text});
        status.textContent = `Added ${result.added}; already saved ${result.duplicates}. ${result.total} bookmarks saved.`;
      });
    });
    update();
    return {updateCount(value) { savedCount = value; update(); }};
  }
  root.FormulaBookmarkTransfer = {bind};
  if (typeof module !== 'undefined') module.exports = root.FormulaBookmarkTransfer;
})(globalThis);
