/* On-demand, literal source excerpts. All state belongs to the rendered card. */
(function (root) {
  'use strict';

  function create({match, url, revision, backend, signal, isCurrent,
    document = root.document, fetcher = root.fetch.bind(root),
    clipboard = root.navigator?.clipboard, timeoutMs = 8000}) {
    if (match.source !== 'tex' || !/^occ1:[a-f0-9]{64}$/.test(match.occurrence_id || '') ||
        !/^[a-f0-9]{32}$/.test(revision || '')) return null;
    const details = document.createElement('details');
    details.className = 'source-context';
    const summary = document.createElement('summary');
    summary.textContent = 'Source context';
    details.appendChild(summary);
    details.addEventListener('click', event => event.stopPropagation());
    let body = null;
    let active = null;
    let loaded = false;
    const current = () => details.isConnected && isCurrent() && !signal.aborted;

    function content() {
      if (!body) {
        body = document.createElement('div');
        body.className = 'source-context-body';
        details.appendChild(body);
      }
      return body;
    }

    function message(text, retry = false) {
      const target = content();
      target.replaceChildren();
      const status = document.createElement('p');
      status.className = 'source-context-status';
      status.setAttribute('role', 'status');
      status.textContent = text;
      target.appendChild(status);
      if (retry) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Retry';
        button.addEventListener('click', load);
        target.appendChild(button);
      }
    }

    function show(data) {
      const target = content();
      target.replaceChildren();
      const label = document.createElement('p');
      label.className = 'source-context-caption';
      label.textContent = data.before || data.after
        ? 'TeX excerpt · local macros expanded'
        : 'No surrounding text was retained for this formula.';
      const excerpt = document.createElement('pre');
      excerpt.className = 'source-context-excerpt';
      const formula = document.createElement('mark');
      formula.textContent = match.latex || '';
      excerpt.append(document.createTextNode(data.before), formula, document.createTextNode(data.after));
      target.append(label, excerpt);
      const copy = document.createElement('button');
      copy.type = 'button';
      copy.textContent = 'Copy excerpt';
      copy.addEventListener('click', async () => {
        copy.disabled = true;
        try {
          await clipboard.writeText(data.before + (match.latex || '') + data.after);
          if (current()) copy.textContent = 'Copied';
        } catch {
          if (current()) copy.textContent = 'Could not copy · retry';
        } finally {
          copy.disabled = false;
        }
      });
      target.appendChild(copy);
      root.requestAnimationFrame(() => {
        if (!current() || !details.open) return;
        // Long source prefixes should not hide the selected formula below the
        // excerpt's own scrollbar. Only move the surrounding result list while
        // focus still belongs to this disclosure, preserving a later user move.
        const formulaRect = formula.getBoundingClientRect();
        const excerptRect = excerpt.getBoundingClientRect();
        const inset = Math.max(0, (excerpt.clientHeight - Math.min(formulaRect.height, excerpt.clientHeight)) / 2);
        excerpt.scrollTop += formulaRect.top - excerptRect.top - inset;
        if (details.contains(document.activeElement)) target.scrollIntoView({block: 'nearest'});
      });
    }

    async function load() {
      if (!details.open || loaded || active || !current()) return;
      const controller = new AbortController();
      active = controller;
      let timedOut = false;
      const abort = () => controller.abort();
      signal.addEventListener('abort', abort, {once: true});
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      message('Loading source context…');
      try {
        const response = await fetcher(`${backend}/source-context`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({url, occurrence_id: match.occurrence_id, context_revision: revision}),
          signal: controller.signal,
        });
        if (!response.ok) {
          const error = new Error(response.status === 409
            ? 'The paper changed or expired. Search again to refresh its context.'
            : response.status === 404 ? 'Source context is unavailable for this formula.'
            : 'Could not load source context. Check the backend and retry.');
          error.retry = ![404, 409].includes(response.status);
          throw error;
        }
        const data = await response.json();
        if (!data || data.kind !== 'tex' || data.occurrence_id !== match.occurrence_id ||
            data.context_revision !== revision || typeof data.before !== 'string' ||
            typeof data.after !== 'string' || Array.from(data.before).length > 420 ||
            Array.from(data.after).length > 420) {
          // Count code points consistently with the backend's 420-character cap.
          throw new Error('The source excerpt did not match this result. Search again.');
        }
        if (!current() || !details.open || active !== controller || controller.signal.aborted) return;
        loaded = true;
        show(data);
      } catch (error) {
        if (!current() || !details.open || active !== controller) return;
        if (error.name === 'AbortError' && !timedOut) return;
        message(timedOut ? 'Source context took too long to load. Retry when the backend is ready.'
          : error.name === 'TypeError' ? 'Could not reach the backend. Check it and retry.'
          : error.name === 'SyntaxError' ? 'Source context could not be read. Retry when the backend is ready.' : error.message,
          timedOut || error.retry === true || ['TypeError', 'SyntaxError'].includes(error.name));
      } finally {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort);
        if (active === controller) active = null;
      }
    }

    details.addEventListener('toggle', () => {
      if (details.open) load();
      else if (active) {
        active.abort();
        active = null;
      }
    });
    return details;
  }

  root.FormulaSourceContext = {create};
  if (typeof module !== 'undefined') module.exports = root.FormulaSourceContext;
})(globalThis);
