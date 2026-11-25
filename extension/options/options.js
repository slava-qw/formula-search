'use strict';

// === SETTINGS STORE (options) BEGIN ===
const SETTINGS_STORAGE_KEY = 'fs_settings';
const DEFAULT_SETTINGS = {
  backendUrl: 'http://localhost:7878',
  maxChatHistory: 500,
  defaultView: 'list',
  theme: 'auto',
  badgeEnabled: true,
};

function loadSettings() {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve(Object.assign({}, DEFAULT_SETTINGS));
  return chrome.storage.local.get(SETTINGS_STORAGE_KEY).then((result) => {
    return Object.assign({}, DEFAULT_SETTINGS, result[SETTINGS_STORAGE_KEY] || {});
  });
}

function saveSettings(settings) {
  if (typeof chrome === 'undefined' || !chrome.storage) return Promise.resolve();
  return chrome.storage.local.set({ [SETTINGS_STORAGE_KEY]: settings });
}

function applyTheme(theme) {
  if (theme === 'dark' || theme === 'light') {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
}
// === SETTINGS STORE (options) END ===

const backendUrlInput = document.getElementById('backend-url');
const maxChatHistoryInput = document.getElementById('max-chat-history');
const defaultViewSelect = document.getElementById('default-view');
const themeSelect = document.getElementById('theme');
const badgeEnabledInput = document.getElementById('badge-enabled');
const saveBtn = document.getElementById('save-btn');
const saveStatus = document.getElementById('save-status');

function fillForm(settings) {
  backendUrlInput.value = settings.backendUrl;
  maxChatHistoryInput.value = settings.maxChatHistory;
  defaultViewSelect.value = settings.defaultView;
  themeSelect.value = settings.theme;
  badgeEnabledInput.checked = settings.badgeEnabled;
  applyTheme(settings.theme);
}

function readForm() {
  const maxChatHistory = parseInt(maxChatHistoryInput.value, 10);
  return {
    backendUrl: backendUrlInput.value.trim() || DEFAULT_SETTINGS.backendUrl,
    maxChatHistory: Number.isFinite(maxChatHistory) && maxChatHistory > 0 ? maxChatHistory : DEFAULT_SETTINGS.maxChatHistory,
    defaultView: defaultViewSelect.value,
    theme: themeSelect.value,
    badgeEnabled: badgeEnabledInput.checked,
  };
}

saveBtn.addEventListener('click', () => {
  const settings = readForm();
  saveSettings(settings).then(() => {
    applyTheme(settings.theme);
    saveStatus.textContent = 'Saved.';
    setTimeout(() => { saveStatus.textContent = ''; }, 1500);
  }).catch(() => {
    saveStatus.textContent = 'Failed to save.';
  });
});

themeSelect.addEventListener('change', () => applyTheme(themeSelect.value));

loadSettings().then(fillForm);
