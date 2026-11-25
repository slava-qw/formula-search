/* Clipboard feedback and selectable fallback, created only after interaction. */
(function (root) {
  'use strict';
  const cardState = new WeakMap();
  function bind(button, {container, text, label, successText = 'Copied', errorText,
    document = root.document, clipboard = () => root.navigator?.clipboard}) {
    const idleText = button.textContent;
    let pending = false, timer;
    const connected = () => container.isConnected && button.isConnected && container.contains(button);
    button.addEventListener('click', async event => {
      event.stopPropagation();
      if (pending || !connected()) return;
      let state = cardState.get(container);
      if (!state) {
        const status = document.createElement('p');
        status.className = 'formula-copy-status';
        status.setAttribute('role', 'status');
        container.appendChild(status);
        state = {status, source: null, revision: 0};
        cardState.set(container, state);
      }
      const revision = ++state.revision;
      const current = () => connected() && state.revision === revision;
      state.source?.remove();
      state.source = null;
      pending = true;
      clearTimeout(timer);
      button.textContent = idleText;
      button.setAttribute('aria-busy', 'true');
      state.status.textContent = `Copying ${label}…`;
      try {
        const target = clipboard();
        if (typeof target?.writeText !== 'function') throw new Error('Clipboard unavailable');
        await target.writeText(text);
        if (!current()) return;
        state.status.textContent = `${label} copied`;
        button.textContent = successText;
      } catch {
        if (!current()) return;
        state.status.textContent = `Could not copy ${label}. Select the text below to copy it.`;
        button.textContent = errorText || idleText;
        const source = document.createElement('textarea');
        source.className = 'formula-copy-source';
        source.value = text;
        source.readOnly = true;
        source.spellcheck = false;
        source.rows = 3;
        source.setAttribute('aria-label', `${label} to copy`);
        source.addEventListener('click', event => event.stopPropagation());
        container.appendChild(source);
        state.source = source;
        // A late rejection must not take focus back from the query or another card.
        if (document.activeElement === button) {
          source.focus({preventScroll: true});
          source.select();
          source.scrollIntoView({block: 'nearest'});
        }
      } finally {
        pending = false;
        button.removeAttribute('aria-busy');
        if (connected()) timer = setTimeout(() => { button.textContent = idleText; }, 1400);
      }
    });
  }
  root.FormulaCardCopy = {bind};
  if (typeof module !== 'undefined') module.exports = root.FormulaCardCopy;
})(globalThis);
