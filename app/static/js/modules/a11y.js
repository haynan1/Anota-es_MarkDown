/**
 * Reading-comfort panel: text size and bold emphasis.
 *
 * State lives in localStorage and is applied as a custom property plus a class
 * on <html> — the same shape as the theme. The anti-flash pass in
 * theme-init.js has already applied the saved values before first paint; this
 * module only renders the controls and reacts to changes.
 *
 * Written in plain JavaScript with no framework: the app ships no client-side
 * library, and this is state + storage + one apply() call.
 */

import { $, $$, openDialog, closeDialog } from './dom.js';

const SETTINGS = window.__markdownStudioA11y || {
  SCALE_KEY: 'markdown-studio:font-scale',
  BOLD_KEY: 'markdown-studio:bold-text',
  SCALES: [0.95, 1, 1.12, 1.25],
  EPSILON: 0.001,
  allowedScale: (value) => (Number(value) === 1 ? 1 : 1),
};

const LABELS = new Map([
  [0.95, 'Compacta'],
  [1, 'Padrão'],
  [1.12, 'Confortável'],
  [1.25, 'Grande'],
]);

const store = {
  scale: 1,
  bold: false,

  read() {
    try {
      this.scale = SETTINGS.allowedScale(window.localStorage.getItem(SETTINGS.SCALE_KEY));
      this.bold = window.localStorage.getItem(SETTINGS.BOLD_KEY) === 'true';
    } catch (error) {
      this.scale = 1;
      this.bold = false;
    }
  },

  persist(key, value) {
    try {
      window.localStorage.setItem(key, String(value));
    } catch (error) {
      /* the setting still applies for this page */
    }
  },

  isScale(value) {
    return Math.abs(this.scale - value) < SETTINGS.EPSILON;
  },

  get percent() {
    return Math.round(this.scale * 100);
  },

  get label() {
    for (const [value, name] of LABELS) {
      if (Math.abs(this.scale - value) < SETTINGS.EPSILON) return name;
    }
    return 'Padrão';
  },

  setScale(value) {
    this.scale = SETTINGS.allowedScale(value);
    this.persist(SETTINGS.SCALE_KEY, this.scale);
    this.apply();
  },

  setBold(value) {
    this.bold = Boolean(value);
    this.persist(SETTINGS.BOLD_KEY, this.bold);
    this.apply();
  },

  apply() {
    const root = document.documentElement;
    root.style.setProperty('--a11y-font-scale', String(this.scale));
    root.classList.toggle(
      'a11y-text-size',
      Math.abs(this.scale - 1) >= SETTINGS.EPSILON
    );
    root.classList.toggle('a11y-bold-text', this.bold);
    render();
  },
};

function render() {
  $$('[data-a11y-scale]').forEach((button) => {
    const value = parseFloat(button.dataset.a11yScale);
    const active = store.isScale(value);
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });

  const status = $('[data-a11y-status]');
  if (status) status.textContent = `${store.label} · ${store.percent}%`;

  const bold = $('[data-a11y-bold]');
  if (bold) bold.checked = store.bold;
}

export function initAccessibilityPanel() {
  const dialog = $('#a11y-dialog');
  if (!dialog) return;

  store.read();
  render();

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="open-a11y"]')) {
      openDialog(dialog);
      return;
    }

    const option = event.target.closest('[data-a11y-scale]');
    if (option) {
      store.setScale(parseFloat(option.dataset.a11yScale));
      return;
    }

    if (event.target.closest('[data-a11y-reset]')) {
      store.setScale(1);
      store.setBold(false);
    }
  });

  document.addEventListener('change', (event) => {
    if (event.target.matches('[data-a11y-bold]')) {
      store.setBold(event.target.checked);
    }
  });

  // Clicking the backdrop of a native <dialog> lands on the dialog itself.
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) closeDialog(dialog);
  });
}
