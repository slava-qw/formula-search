/* A UI request belongs to one document activation, even if a tab returns to
 * the same URL while an older response is still in flight. */
(function (root) {
  'use strict';
  class FormulaDocumentSession {
    constructor() {
      this.current = null;
      this.queries = new Map();
    }
    begin() {
      this.current?.controller.abort();
      const controller = new AbortController();
      this.current = { controller, signal: controller.signal };
      return this.current;
    }
    isCurrent(context) {
      return context === this.current && !context.signal.aborted;
    }
    remember(key, state) {
      if (!key) return;
      this.queries.delete(key);
      this.queries.set(key, { ...state });
      if (this.queries.size > 50) this.queries.delete(this.queries.keys().next().value);
    }
    recall(key) {
      return this.queries.get(key) || null;
    }
  }
  root.FormulaDocumentSession = FormulaDocumentSession;
  if (typeof module !== 'undefined') module.exports = FormulaDocumentSession;
})(globalThis);
