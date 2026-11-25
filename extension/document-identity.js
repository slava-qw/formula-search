/* Keep this contract aligned with backend/document_identity.py. */
(function (root) {
  'use strict';
  function arxivId(url) {
    const value = String(url || '').trim();
    if (/[\s\\\x00-\x1f\x7f]/.test(value)) return '';
    const match = /^(https?):\/\/([^/?#]+)(\/[^?#]*)(?:[?#].*)?$/i.exec(value);
    if (!match) return '';
    const port = match[1].toLowerCase() === 'https' ? ':443' : ':80';
    let host = match[2].toLowerCase();
    if (host.endsWith(port)) host = host.slice(0, -port.length);
    if (!['arxiv.org', 'www.arxiv.org', 'export.arxiv.org'].includes(host)) return '';
    const id = /^\/(?:pdf|abs|src|html|e-print)\/([A-Za-z-]+(?:\.[A-Za-z]{2})?\/[0-9]{7}|[0-9]{4}\.[0-9]{4,5})(v[1-9][0-9]*)?(?:\.pdf)?$/.exec(match[3]);
    if (!id) return '';
    let base = id[1];
    if (base.includes('/')) {
      const [archive, number] = base.split('/');
      base = `${archive.split('.')[0].toLowerCase()}/${number}`;
    }
    return base + (id[2] || '');
  }
  function normalizeUrl(url) { return String(url || '').trim().split('#', 1)[0]; }
  function canonicalUrl(url) {
    const id = arxivId(url);
    return id ? `https://arxiv.org/pdf/${id}` : normalizeUrl(url);
  }
  function documentKey(url) {
    const id = arxivId(url);
    return id ? `arxiv:${id}` : normalizeUrl(url);
  }
  const identity = {arxivId, normalizeUrl, canonicalUrl, documentKey};
  root.FormulaDocumentIdentity = identity;
  if (typeof module !== 'undefined') module.exports = identity;
})(globalThis);
