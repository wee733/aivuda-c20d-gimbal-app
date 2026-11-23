import en from './locales/en-US.mjs';
import zh from './locales/zh-CN.mjs';

let messages = en;

export function t(key, values = {}) {
  return messages[key].replace(/\{(\w+)\}/g, (placeholder, name) => values[name] ?? placeholder);
}

export function bindLocale(onChange) {
  const apply = () => {
    let setting;
    try { setting = localStorage.getItem('aivuda_ui_locale'); } catch {}
    const lang = !setting || setting === 'system' ? navigator.language : setting;
    messages = /^zh(?:-|_|$)/i.test(lang) ? zh : en;
    document.documentElement.lang = messages === zh ? 'zh-CN' : 'en-US';
    for (const element of document.querySelectorAll('[data-i18n]')) {
      element.textContent = t(element.dataset.i18n);
    }
    for (const element of document.querySelectorAll('[data-i18n-label]')) {
      element.setAttribute('aria-label', t(element.dataset.i18nLabel));
    }
    onChange();
  };
  window.addEventListener('storage', event => {
    if (!event.key || event.key === 'aivuda_ui_locale') apply();
  });
  window.addEventListener('languagechange', apply);
  apply();
}
