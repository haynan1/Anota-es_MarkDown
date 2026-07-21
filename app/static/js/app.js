/**
 * Application shell behaviour: theme, navigation, dialogs, confirmations,
 * filters and category colours.
 *
 * Everything degrades gracefully - with JavaScript disabled the forms still
 * submit, the filters still apply and destructive actions still ask for
 * confirmation server-side.
 */

import { $, $$, debounce, openDialog, closeDialog } from './modules/dom.js';
import { initAccessibilityPanel } from './modules/a11y.js';
import { initToasts } from './modules/toasts.js';
import { initBulkSelect } from './modules/bulk-select.js';

const THEME_KEY = 'markdown-studio:theme';
const SIDEBAR_KEY = 'markdown-studio:sidebar';

/* ── Theme ────────────────────────────────────────────────────────────── */

function toggleTheme() {
  const root = document.documentElement;
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try {
    window.localStorage.setItem(THEME_KEY, next);
  } catch (error) {
    /* storage unavailable - the theme still applies for this page */
  }
}

function watchSystemTheme() {
  const meta = document.querySelector('meta[name="theme-preference"]');
  if (!meta || meta.getAttribute('content') !== 'auto') return;

  const query = window.matchMedia('(prefers-color-scheme: dark)');
  query.addEventListener('change', (event) => {
    let stored = null;
    try {
      stored = window.localStorage.getItem(THEME_KEY);
    } catch (error) {
      stored = null;
    }
    // An explicit user choice always wins over the system preference.
    if (!stored) {
      document.documentElement.setAttribute('data-theme', event.matches ? 'dark' : 'light');
    }
  });
}

/* ── Sidebar ──────────────────────────────────────────────────────────── */

/** Applies the state and keeps the toggle's accessible name truthful. */
function setSidebar(shell, state) {
  const collapsed = state === 'collapsed';
  shell.setAttribute('data-sidebar', state);

  const toggle = $('[data-action="toggle-sidebar"]');
  if (!toggle) return;

  const label = collapsed ? 'Expandir barra lateral' : 'Recolher barra lateral';
  toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  // Name only through aria-label/title — a rendered tooltip does not fit the
  // collapsed rail.
  toggle.setAttribute('aria-label', label);
  toggle.setAttribute('title', label);
}

function initSidebar() {
  const shell = $('#app-shell');
  if (!shell) return;

  try {
    if (window.localStorage.getItem(SIDEBAR_KEY) === 'collapsed') {
      setSidebar(shell, 'collapsed');
    }
  } catch (error) {
    /* ignore */
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="toggle-sidebar"]')) {
      const collapsed = shell.getAttribute('data-sidebar') === 'collapsed';
      setSidebar(shell, collapsed ? 'expanded' : 'collapsed');
      try {
        window.localStorage.setItem(SIDEBAR_KEY, collapsed ? 'expanded' : 'collapsed');
      } catch (error) {
        /* ignore */
      }
    }

    if (event.target.closest('[data-action="open-mobile-nav"]')) {
      shell.setAttribute('data-mobile-nav', 'open');
      const trigger = $('[data-action="open-mobile-nav"]');
      if (trigger) trigger.setAttribute('aria-expanded', 'true');
    }

    if (event.target.closest('[data-action="close-mobile-nav"]')) {
      closeMobileNav(shell);
    }
  });
}

function closeMobileNav(shell) {
  shell.setAttribute('data-mobile-nav', 'closed');
  const trigger = $('[data-action="open-mobile-nav"]');
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

/* ── Category colours ─────────────────────────────────────────────────── */

/**
 * Colours arrive as data attributes and are applied from JavaScript.
 * A `style` attribute in the HTML would force 'unsafe-inline' into the CSP;
 * styles set via the CSSOM are not subject to that restriction.
 */
function applyCategoryColors(root = document) {
  $$('[data-color]', root).forEach((element) => {
    const color = element.getAttribute('data-color');
    if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(color || '')) {
      element.style.setProperty('--category-color', color);
    }
  });
}

/* ── Confirmation dialogs ─────────────────────────────────────────────── */

/**
 * Forms carrying `data-confirm` are routed through one shared dialog instead
 * of window.confirm, so destructive actions look and behave consistently.
 */
function initConfirmations() {
  const dialog = $('#confirm-dialog');
  if (!dialog) return;

  const message = $('#confirm-dialog-message');
  let pendingForm = null;

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    const prompt = form.getAttribute('data-confirm');
    if (!prompt || form.dataset.confirmed === 'true') return;

    event.preventDefault();
    pendingForm = form;
    if (message) message.textContent = prompt;
    openDialog(dialog);
  });

  dialog.addEventListener('close', () => {
    if (dialog.returnValue === 'confirm' && pendingForm) {
      pendingForm.dataset.confirmed = 'true';
      pendingForm.submit();
    }
    pendingForm = null;
  });
}

/* ── Generic dialog triggers ──────────────────────────────────────────── */

function initDialogs() {
  document.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-action="open-dialog"]');
    if (opener) {
      openDialog($(`#${opener.getAttribute('data-target')}`));
    }

    const closer = event.target.closest('[data-action="close-dialog"]');
    if (closer) {
      closeDialog(closer.closest('dialog'));
    }
  });
}

/* ── Rename dialog ────────────────────────────────────────────────────── */

function initRename() {
  const dialog = $('#rename-dialog');
  const form = $('#rename-form');
  const input = $('#rename-title');
  if (!dialog || !form || !input) return;

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action="rename"]');
    if (!button) return;

    form.action = `/documentos/${encodeURIComponent(button.dataset.uuid)}/renomear`;
    input.value = button.dataset.title || '';
    openDialog(dialog);
    input.select();
  });
}

/* ── Filters ──────────────────────────────────────────────────────────── */

function initFilters() {
  const form = $('#filters-form');
  if (!form) return;

  const submit = debounce(() => {
    // Any filter change resets pagination.
    const page = form.querySelector('input[name="pagina"]');
    if (page) page.remove();
    form.submit();
  }, 400);

  $$('[data-autosubmit]', form).forEach((field) => {
    const immediate = field.tagName === 'SELECT';
    field.addEventListener(immediate ? 'change' : 'input', () => {
      if (immediate) {
        submit.flush();
      } else {
        submit();
      }
    });
  });
}

/* ── Import dropzone ──────────────────────────────────────────────────── */

function initDropzone() {
  const zone = $('[data-dropzone]');
  if (!zone) return;

  const input = zone.querySelector('input[type="file"]');
  const nameLabel = zone.querySelector('[data-dropzone-name]');
  if (!input) return;

  const showName = () => {
    if (!nameLabel) return;
    const file = input.files && input.files[0];
    nameLabel.textContent = file ? file.name : '';
    nameLabel.hidden = !file;
  };

  // No click/keydown handlers: the element is a <label for="…">, so opening
  // the picker by mouse or keyboard is native browser behaviour.
  input.addEventListener('change', showName);

  ['dragenter', 'dragover'].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.setAttribute('data-dragover', 'true');
    })
  );

  ['dragleave', 'drop'].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.removeAttribute('data-dragover');
    })
  );

  zone.addEventListener('drop', (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length) {
      input.files = files;
      showName();
    }
  });
}

/* ── Backup restore confirmation field ────────────────────────────────── */

function initRestoreForm() {
  const form = $('[data-restore-form]');
  if (!form) return;

  const mode = form.querySelector('select[name="mode"]');
  const confirmation = form.querySelector('[data-replace-confirmation]');
  if (!mode || !confirmation) return;

  const sync = () => {
    const replacing = mode.value === 'replace';
    confirmation.hidden = !replacing;
    const input = confirmation.querySelector('input');
    if (input) input.required = replacing;
  };

  mode.addEventListener('change', sync);
  sync();
}

/* ── Escape handling ──────────────────────────────────────────────────── */

function initEscape() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;

    const openMenu = $('details.menu[open]');
    if (openMenu) {
      openMenu.removeAttribute('open');
      const summary = openMenu.querySelector('summary');
      if (summary) summary.focus();
      return;
    }

    const shell = $('#app-shell');
    if (shell && shell.getAttribute('data-mobile-nav') === 'open') {
      closeMobileNav(shell);
    }
  });
}

/** Only one overflow menu open at a time, and clicking away closes it. */
function initMenus() {
  document.addEventListener('click', (event) => {
    const clicked = event.target.closest('details.menu');
    $$('details.menu[open]').forEach((menu) => {
      if (menu !== clicked) menu.removeAttribute('open');
    });
  });
}

/* ── Boot ─────────────────────────────────────────────────────────────── */

function boot() {
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="toggle-theme"]')) toggleTheme();
  });

  watchSystemTheme();
  initSidebar();
  initToasts();
  initConfirmations();
  initDialogs();
  initRename();
  initFilters();
  initDropzone();
  initRestoreForm();
  initEscape();
  initMenus();
  initAccessibilityPanel();
  initBulkSelect();
  applyCategoryColors();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

export { applyCategoryColors };
