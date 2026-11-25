/* Build distant math previews only when their result card approaches the list. */
(function (root) {
  'use strict';

  class FormulaPreviewQueue {
    constructor({container, render, Observer = root.IntersectionObserver, eagerCount = 5}) {
      this.container = container;
      this.render = render;
      this.Observer = Observer;
      this.eagerCount = eagerCount;
      this.pending = new Map();
      this.generation = 0;
      this.reset();
      container.addEventListener('focusin', event => this.reveal(event.target.closest('.result-item')));
    }

    reset() {
      this.generation += 1;
      this.observer?.disconnect();
      this.observer = null;
      this.pending.clear();
      this.ordinal = 0;
    }

    add(card, target, raw, {defer = true} = {}) {
      if (!defer || this.ordinal++ < this.eagerCount || typeof this.Observer !== 'function') {
        this.render(raw, target);
        return;
      }
      if (!this.observer) {
        const generation = this.generation;
        try {
          this.observer = new this.Observer(entries => {
            if (generation !== this.generation) return;
            for (const entry of entries) if (entry.isIntersecting) this.reveal(entry.target);
          }, {root: this.container, rootMargin: '640px 0px'});
        } catch {
          // A browser without a usable observer still gets complete previews.
          this.Observer = null;
          this.render(raw, target);
          return;
        }
      }
      target.classList.add('formula-preview-pending');
      target.textContent = raw;
      this.pending.set(card, {target, raw});
      this.observer.observe(card);
    }

    reveal(card) {
      const item = this.pending.get(card);
      if (!item) return;
      this.pending.delete(card);
      this.observer?.unobserve(card);
      if (!card.isConnected || !this.container.contains(card) || !card.contains(item.target)) return;
      item.target.classList.remove('formula-preview-pending');
      this.render(item.raw, item.target);
    }
  }

  root.FormulaPreviewQueue = FormulaPreviewQueue;
  if (typeof module !== 'undefined') module.exports = FormulaPreviewQueue;
})(globalThis);
