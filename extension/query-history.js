/* Editable history combobox. The query keeps DOM focus during list navigation. */
(function (root) {
  'use strict';
  class FormulaQueryHistory {
    constructor({input, list, load, choose, capture, isCurrent, document = root.document}) {
      Object.assign(this, {input, list, load, choose, capture, isCurrent, document});
      this.revision = 0;
      this.active = -1;
      this.entries = [];
      this.pending = null;
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-autocomplete', 'none');
      input.setAttribute('aria-controls', list.id);
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', 'Recent formula searches');
      this.hide();
    }
    get open() { return !this.list.classList.contains('hidden'); }
    hide() {
      this.revision++;
      this.pending = null;
      this.active = -1;
      this.list.classList.add('hidden');
      this.input.setAttribute('aria-expanded', 'false');
      this.input.removeAttribute('aria-activedescendant');
    }
    render(history) {
      this.list.replaceChildren();
      this.active = -1;
      this.input.removeAttribute('aria-activedescendant');
      this.entries = Array.isArray(history) ? history.filter(entry =>
        typeof entry?.query === 'string' && entry.query.trim() && entry.query.length <= 2048).slice(0, 20) : [];
      if (!this.entries.length) { this.hide(); return false; }
      this.shownState = this.capture();
      this.entries.forEach((entry, index) => {
        const option = this.document.createElement('li');
        option.id = `${this.list.id}-option-${index}`;
        option.textContent = entry.query;
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        option.addEventListener('mousedown', event => {
          if (event.button !== 0 || !this.list.contains(option)) return;
          event.preventDefault(); // Choose before blur dismisses the list.
          this.pick(index);
        });
        // Assistive activation may dispatch a click without a mouse-down.
        option.addEventListener('click', () => {
          if (this.list.contains(option)) this.pick(index);
        });
        this.list.appendChild(option);
      });
      this.list.classList.remove('hidden');
      this.input.setAttribute('aria-expanded', 'true');
      return true;
    }
    show(edge = null) {
      if (this.input.disabled || this.document.activeElement !== this.input) return Promise.resolve(false);
      if (this.pending && this.isCurrent(this.pending.state)) {
        if (edge !== null) this.pending.edge = edge;
        return this.pending.promise;
      }
      const request = {revision: ++this.revision, state: this.capture(), edge};
      this.pending = request;
      request.promise = Promise.resolve().then(() => this.load()).then(history => {
        if (request.revision !== this.revision || !this.isCurrent(request.state) ||
            this.input.disabled || this.document.activeElement !== this.input) return false;
        if (!this.render(history)) return false;
        if (request.edge !== null) this.select(request.edge === 'last' ? this.entries.length - 1 : 0);
        return true;
      }).catch(() => {
        // History is optional; storage failure must not interrupt formula search.
        if (request.revision === this.revision) this.hide();
        return false;
      }).finally(() => {
        if (this.pending === request) this.pending = null;
      });
      return request.promise;
    }
    select(index) {
      if (!this.open || !this.entries.length) return;
      this.active = (index + this.entries.length) % this.entries.length;
      [...this.list.children].forEach((option, i) => option.setAttribute('aria-selected', String(i === this.active)));
      const selected = this.list.children[this.active];
      this.input.setAttribute('aria-activedescendant', selected.id);
      selected.scrollIntoView({block: 'nearest'});
    }
    pick(index) {
      const entry = this.entries[index];
      if (!this.open || !entry || this.input.disabled || !this.isCurrent(this.shownState)) return;
      this.hide();
      this.choose(entry.query);
    }
    handleKeydown(event) {
      if (event.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
      if (event.key === 'Escape' && (this.open || this.pending)) {
        this.hide();
      } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        const up = event.key === 'ArrowUp';
        if (this.open) this.select(this.active < 0 ? (up ? this.entries.length - 1 : 0) : this.active + (up ? -1 : 1));
        else this.show(up ? 'last' : 'first');
      } else if (event.key === 'Enter' && this.open && this.active >= 0) {
        this.pick(this.active);
      } else {
        return false;
      }
      event.preventDefault();
      event.stopPropagation();
      return true;
    }
  }
  root.FormulaQueryHistory = FormulaQueryHistory;
  if (typeof module !== 'undefined') module.exports = FormulaQueryHistory;
})(globalThis);
