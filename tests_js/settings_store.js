globalThis.__results = [];
function assert(cond, msg) { globalThis.__results.push((cond ? 'OK: ' : 'FAIL: ') + msg); }

loadSettings().then((settings) => {
  assert(settings.backendUrl === 'http://localhost:7878', 'loadSettings returns the default backendUrl when nothing is stored, got ' + settings.backendUrl);
  assert(settings.maxChatHistory === 500, 'default maxChatHistory is 500, got ' + settings.maxChatHistory);
  assert(settings.defaultView === 'list', 'default defaultView is list, got ' + settings.defaultView);
  assert(settings.theme === 'auto', 'default theme is auto, got ' + settings.theme);
  assert(settings.badgeEnabled === true, 'default badgeEnabled is true, got ' + settings.badgeEnabled);

  return saveSettings({ backendUrl: 'http://localhost:9999', maxChatHistory: 200, defaultView: 'step', theme: 'dark', badgeEnabled: false });
}).then(() => loadSettings()).then((settings) => {
  assert(settings.backendUrl === 'http://localhost:9999', 'saveSettings persists backendUrl, got ' + settings.backendUrl);
  assert(settings.maxChatHistory === 200, 'saveSettings persists maxChatHistory, got ' + settings.maxChatHistory);
  assert(settings.theme === 'dark', 'saveSettings persists theme, got ' + settings.theme);

  fillForm(settings);
  assert(document.getElementById('backend-url').value === 'http://localhost:9999', 'fillForm populates the backend URL field');
  assert(document.getElementById('max-chat-history').value === '200', 'fillForm populates the max history field, got ' + document.getElementById('max-chat-history').value);
  assert(document.documentElement.getAttribute('data-theme') === 'dark', 'fillForm applies the theme attribute to <html>, got ' + document.documentElement.getAttribute('data-theme'));

  document.getElementById('backend-url').value = 'http://localhost:1234';
  document.getElementById('max-chat-history').value = '300';
  const read = readForm();
  assert(read.backendUrl === 'http://localhost:1234', 'readForm reads the backend URL field, got ' + read.backendUrl);
  assert(read.maxChatHistory === 300, 'readForm reads and coerces the max history field to a number, got ' + read.maxChatHistory + ' (' + typeof read.maxChatHistory + ')');

  applyTheme('auto');
  assert(document.documentElement.getAttribute('data-theme') === null, 'applyTheme(auto) removes the data-theme attribute, got ' + document.documentElement.getAttribute('data-theme'));
  applyTheme('light');
  assert(document.documentElement.getAttribute('data-theme') === 'light', 'applyTheme(light) sets data-theme=light');
});
