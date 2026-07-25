/**
 * Group page: drag a document to change its place in the sequence.
 *
 * This is an enhancement, never the only way. Every row carries "move up" and
 * "move down" buttons that post a form, so the order can be changed with a
 * keyboard, with a screen reader, and with JavaScript switched off entirely.
 * What this file adds is the pointer gesture — and it persists through the
 * same service the buttons use.
 */

import { $, $$, postJSON } from './modules/dom.js';
import { toast } from './modules/toasts.js';

function boot() {
  const list = $('[data-group-list]');
  const host = list && list.closest('[data-group-order-url]');
  if (!list || !host) return;

  const url = host.dataset.groupOrderUrl;
  let dragged = null;

  function renumber() {
    $$('.group-item-position', list).forEach((label, index) => {
      label.textContent = String(index + 1);
    });
    // The first row cannot move up and the last cannot move down; without this
    // the arrows would keep the state of the order the page was rendered with.
    const items = $$('.group-item', list);
    items.forEach((item, index) => {
      const [up, down] = $$('.group-item-actions button', item);
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === items.length - 1;
    });
  }

  async function persist() {
    const uuids = $$('.group-item', list).map((item) => item.dataset.uuid);
    const { ok, data } = await postJSON(url, { uuids });

    if (!ok || !data || !data.ok) {
      toast('Não foi possível salvar a nova ordem. Recarregue a página.', 'error');
      return;
    }
    toast('Ordem atualizada.', 'success', { timeout: 2500 });
  }

  list.addEventListener('dragstart', (event) => {
    const item = event.target.closest('.group-item');
    if (!item) return;
    dragged = item;
    item.dataset.dragging = 'true';
    // Firefox refuses to start a drag without payload on the transfer object.
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', item.dataset.uuid || '');
    }
  });

  list.addEventListener('dragend', () => {
    if (dragged) delete dragged.dataset.dragging;
    dragged = null;
    renumber();
  });

  list.addEventListener('dragover', (event) => {
    if (!dragged) return;
    event.preventDefault();

    const target = event.target.closest('.group-item');
    if (!target || target === dragged) return;

    // Insert before or after depending on which half of the row is hovered,
    // so the drop lands where the pointer visually is.
    const box = target.getBoundingClientRect();
    const after = event.clientY > box.top + box.height / 2;
    target.parentNode.insertBefore(dragged, after ? target.nextSibling : target);
  });

  list.addEventListener('drop', (event) => {
    if (!dragged) return;
    event.preventDefault();
    delete dragged.dataset.dragging;
    dragged = null;
    renumber();
    // The order is already on screen; if saving it fails the writer has to be
    // told, so the rejection is never left unhandled.
    persist().catch(() =>
      toast('Não foi possível salvar a nova ordem. Recarregue a página.', 'error')
    );
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
