function mkOptEl(tag, id, type) {
  const el = document.createElement(tag);
  el.id = id;
  if (type) el.type = type;
  document.body.appendChild(el);
  return el;
}
mkOptEl('input', 'backend-url', 'text');
mkOptEl('input', 'max-chat-history', 'number');
// NB: these fixture-local handles are prefixed with `_fixture` (rather than
// named `defaultViewSelect`/`themeSelect` as in the brief) because
// run_js_test.py evals each --whole file as a separate top-level script in
// one persistent V8 context, and options.js declares its own top-level
// `const defaultViewSelect`/`const themeSelect` -- an identical name here
// would throw "Identifier ... has already been declared" on load.
const _fixtureDefaultViewSelect = mkOptEl('select', 'default-view');
['list', 'step', 'chat'].forEach((v) => {
  const opt = document.createElement('option');
  opt.value = v;
  _fixtureDefaultViewSelect.appendChild(opt);
});
const _fixtureThemeSelect = mkOptEl('select', 'theme');
['auto', 'light', 'dark'].forEach((v) => {
  const opt = document.createElement('option');
  opt.value = v;
  _fixtureThemeSelect.appendChild(opt);
});
mkOptEl('input', 'badge-enabled', 'checkbox');
mkOptEl('button', 'save-btn');
mkOptEl('span', 'save-status');
