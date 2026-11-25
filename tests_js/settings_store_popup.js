globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

loadSettings().then((settings) => {
  assert(settings.backendUrl === 'http://localhost:7878', 'popup outer loadSettings returns defaults when nothing stored, got ' + settings.backendUrl);
  return saveSettings(Object.assign({}, DEFAULT_SETTINGS, { theme: 'dark' }));
}).then(() => loadSettings()).then((settings) => {
  assert(settings.theme === 'dark', 'popup outer saveSettings/loadSettings round-trip works, got ' + settings.theme);
  applyTheme('dark');
  assert(document.documentElement.getAttribute('data-theme') === 'dark', 'popup outer applyTheme sets data-theme');
  applyTheme('auto');
  assert(document.documentElement.getAttribute('data-theme') === null, 'popup outer applyTheme(auto) clears data-theme');
});
