/** Small DOM helpers shared by the app modules. */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/** CSRF token issued by Flask-WTF, sent as a header on every JSON request. */
export function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

/**
 * POST JSON with CSRF, returning `{ ok, status, data }`.
 * Never throws on an HTTP error - callers decide how to react.
 *
 * `keepalive` lets a save survive the page it was fired from: the browser
 * finishes the request after the tab is gone. It is the only way to persist a
 * last edit on `pagehide` while still sending the CSRF header, which
 * `navigator.sendBeacon` cannot do.
 */
export async function postJSON(url, payload, { signal, keepalive = false } = {}) {
  try {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      signal,
      keepalive,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
        Accept: 'application/json',
      },
      body: JSON.stringify(payload),
    });

    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }

    return { ok: response.ok, status: response.status, data };
  } catch (error) {
    if (error.name === 'AbortError') {
      throw error;
    }
    return { ok: false, status: 0, data: null };
  }
}

/** Trailing-edge debounce. */
export function debounce(fn, wait) {
  let timer = null;
  const wrapped = (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
  wrapped.cancel = () => window.clearTimeout(timer);
  wrapped.flush = (...args) => {
    window.clearTimeout(timer);
    fn(...args);
  };
  return wrapped;
}

export function formatNumber(value) {
  return new Intl.NumberFormat('pt-BR').format(value || 0);
}

/** Keeps focus inside an open dialog and restores it on close. */
export function openDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.showModal === 'function') {
    dialog.showModal();
  } else {
    dialog.setAttribute('open', '');
  }
  const focusable = dialog.querySelector(
    'input:not([type="hidden"]), select, textarea, button:not([data-action="close-dialog"])'
  );
  if (focusable) focusable.focus();
}

export function closeDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.close === 'function') {
    dialog.close();
  } else {
    dialog.removeAttribute('open');
  }
}
