/** Transient notifications, mirroring the server-rendered flash markup. */

import { $ } from './dom.js';

const ICONS = {
  success: 'check',
  error: 'alert',
  warning: 'alert',
  info: 'info',
};

const AUTO_DISMISS_MS = 5000;

function container() {
  return $('#toasts');
}

function dismiss(toast) {
  if (!toast || !toast.isConnected) return;
  toast.style.opacity = '0';
  toast.style.transform = 'translateY(8px)';
  window.setTimeout(() => toast.remove(), 180);
}

export function toast(message, kind = 'info', { timeout = AUTO_DISMISS_MS } = {}) {
  const host = container();
  if (!host) return null;

  const element = document.createElement('div');
  element.className = `toast toast-${kind}`;
  element.setAttribute('data-toast', '');

  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('class', 'icon');
  icon.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${ICONS[kind] || 'info'}`);
  icon.appendChild(use);

  const iconWrap = document.createElement('span');
  iconWrap.className = 'toast-icon';
  iconWrap.appendChild(icon);

  const body = document.createElement('p');
  body.className = 'toast-body';
  body.textContent = message;

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn btn-ghost btn-icon btn-sm toast-close';
  close.setAttribute('data-action', 'dismiss-toast');
  close.setAttribute('aria-label', 'Fechar aviso');
  close.textContent = '✕';

  element.append(iconWrap, body, close);
  host.appendChild(element);

  if (timeout > 0) {
    window.setTimeout(() => dismiss(element), timeout);
  }
  return element;
}

export function initToasts() {
  const host = container();
  if (!host) return;

  host.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action="dismiss-toast"]');
    if (button) dismiss(button.closest('[data-toast]'));
  });

  // Server-rendered flashes fade out on the same schedule as client toasts.
  host.querySelectorAll('[data-toast]').forEach((element) => {
    window.setTimeout(() => dismiss(element), AUTO_DISMISS_MS);
  });
}
