/* Deadlines apply to response bodies too; document cancellation remains active. */
(function (root) {
  'use strict';
  async function request(url, options = {}, milliseconds = 150000) {
    const deadline = AbortSignal.timeout(milliseconds);
    const signal = options.signal ? AbortSignal.any([options.signal, deadline]) : deadline;
    return root.fetch(url, {...options, signal});
  }
  async function pdf(url, options) {
    const response = await request(url, options);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const max = 64 * 1024 * 1024;
    if (Number(response.headers.get('content-length')) > max) throw new Error('PDF exceeds the 64 MiB limit.');
    const reader = response.body.getReader();
    let bytes = 0;
    const chunks = [];
    try {
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > max) throw new Error('PDF exceeds the 64 MiB limit.');
        chunks.push(value);
      }
    } finally { await reader.cancel().catch(() => {}); }
    const output = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.byteLength; }
    return output.buffer;
  }
  root.FormulaRequests = {request, pdf};
  if (typeof module !== 'undefined') module.exports = root.FormulaRequests;
})(globalThis);
