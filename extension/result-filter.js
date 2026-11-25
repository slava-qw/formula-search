/* Keep every result while selecting a section and a page within that section. */
(function (root) {
  'use strict';
  class FormulaResultFilter {
    constructor() { this.clear(); }
    static pageKey(match) {
      return Number.isSafeInteger(match.page) && match.page >= 0 ? `page:${match.page}` : 'unknown';
    }
    static sectionKey(match) {
      const title = typeof match.section === 'string' ? match.section.trim() : '';
      return title ? `section:${title}` : 'unknown';
    }
    restorePage(key) {
      this.page = key === 'unknown' || (typeof key === 'string' && /^page:(0|[1-9]\d*)$/.test(key) &&
        Number.isSafeInteger(Number(key.slice(5)))) ? key : 'all';
    }
    restoreSection(key) {
      this.section = key === 'unknown' ? key : typeof key === 'string' && key.startsWith('section:') &&
        key.slice(8).trim() ? `section:${key.slice(8).trim()}` : 'all';
    }
    clear({keepPage = false} = {}) {
      // Retain both scope choices while loading a repeated search. Document
      // resets clear both; set() validates restored choices against new data.
      if (!keepPage) { this.page = 'all'; this.section = 'all'; }
      this.all = [];
      this.matches = [];
      this.sectionMatches = [];
      this.groups = [];
      this.sectionGroups = [];
    }
    set(matches) {
      this.all = matches;
      const sections = new Map();
      for (const match of matches) {
        const key = FormulaResultFilter.sectionKey(match);
        sections.set(key, (sections.get(key) || 0)+1);
      }
      this.sectionGroups = [...sections].map(([key, count]) => ({key, count,
        label: key === 'unknown' ? 'Unlabelled results' : key.slice(8)})).sort((a, b) =>
        a.key === 'unknown' ? 1 : b.key === 'unknown' ? -1 : a.label.localeCompare(b.label));
      if (this.section !== 'all' && !this.sectionGroups.some(group => group.key === this.section)) {
        this.section = 'all'; this.page = 'all';
      }
      return this.rebuildPages();
    }
    selectSection(key) {
      this.section = this.sectionGroups.some(group => group.key === key) ? key : 'all';
      // A page from another section must never make the newly chosen section
      // appear empty. Page options are then rebuilt from this section only.
      this.page = 'all';
      return this.rebuildPages();
    }
    rebuildPages() {
      this.sectionMatches = this.section === 'all' ? this.all :
        this.all.filter(match => FormulaResultFilter.sectionKey(match) === this.section);
      const counts = new Map();
      for (const match of this.sectionMatches) {
        const key = FormulaResultFilter.pageKey(match);
        counts.set(key, (counts.get(key) || 0)+1);
      }
      this.groups = [...counts].map(([key, count]) => ({key, count})).sort((a, b) =>
        a.key === 'unknown' ? 1 : b.key === 'unknown' ? -1 : Number(a.key.slice(5))-Number(b.key.slice(5)));
      return this.select(this.page);
    }
    select(key) {
      this.page = this.groups.some(group => group.key === key) ? key : 'all';
      this.matches = this.page === 'all' ? this.sectionMatches :
        this.sectionMatches.filter(match => FormulaResultFilter.pageKey(match) === this.page);
      return this.matches;
    }
  }
  root.FormulaResultFilter = FormulaResultFilter;
  if (typeof module !== 'undefined') module.exports = FormulaResultFilter;
})(globalThis);
