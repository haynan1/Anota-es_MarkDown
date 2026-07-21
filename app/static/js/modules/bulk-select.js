/**
 * Multi-select on the documents listing.
 *
 * The per-document checkboxes are plain inputs with no <form> of their own —
 * nesting them inside the existing per-document action forms would be invalid
 * markup — so this module gathers the selected UUIDs into the bulk-action form
 * at submit time. It also injects the chosen action as a hidden field, because
 * a programmatic form.submit() drops the submitter button and its value would
 * otherwise be lost.
 *
 * Progressive enhancement: the selection column and the bar are inert in
 * markup and activated here. With scripting off there is simply no bulk UI,
 * and every single-document action still works.
 */

import { $, $$, openDialog } from './dom.js';

export function initBulkSelect() {
  const bar = $('[data-bulk-bar]');
  if (!bar) return;

  const boxes = () => $$('.doc-select');
  const selected = () => boxes().filter((box) => box.checked);

  const countLabel = $('[data-bulk-count]', bar);
  const pluralMark = $('[data-bulk-plural]', bar);
  const selectAll = $('[data-bulk-select-all]');

  // Turn on the selection column and reveal the "select all" control now that
  // JS is running.
  $$('[data-doc-list]').forEach((list) => list.classList.add('is-selectable'));
  $$('[data-bulk-only]').forEach((el) => { el.hidden = false; });

  function sync() {
    const chosen = selected();
    const total = boxes().length;

    if (countLabel) countLabel.textContent = String(chosen.length);
    if (pluralMark) pluralMark.hidden = chosen.length === 1;
    bar.hidden = chosen.length === 0;

    if (selectAll) {
      selectAll.checked = total > 0 && chosen.length === total;
      selectAll.indeterminate = chosen.length > 0 && chosen.length < total;
    }
  }

  document.addEventListener('change', (event) => {
    if (event.target instanceof HTMLElement && event.target.classList.contains('doc-select')) {
      sync();
    }
  });

  if (selectAll) {
    selectAll.addEventListener('change', () => {
      boxes().forEach((box) => { box.checked = selectAll.checked; });
      sync();
    });
  }

  const clear = $('[data-bulk-clear]', bar);
  if (clear) {
    clear.addEventListener('click', () => {
      boxes().forEach((box) => { box.checked = false; });
      sync();
    });
  }

  bar.addEventListener('click', (event) => {
    const button = event.target.closest('button[name="acao"]');
    if (!button) return;

    event.preventDefault();
    const chosen = selected();
    if (!chosen.length) return;

    // Only the destructive action asks first; the rest are reversible.
    if (button.value === 'trash') {
      confirmThen(trashPrompt(chosen.length), () => submitWith(button, chosen));
    } else {
      submitWith(button, chosen);
    }
  });

  function submitWith(button, chosen) {
    // Drop anything a previous, cancelled attempt may have injected.
    $$('input[data-bulk-injected]', bar).forEach((el) => el.remove());
    appendHidden(bar, 'acao', button.value);
    chosen.forEach((box) => appendHidden(bar, 'uuids', box.dataset.uuid));
    bar.submit();
  }

  sync();
}

function appendHidden(form, name, value) {
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = name;
  input.value = value;
  input.setAttribute('data-bulk-injected', '');
  form.appendChild(input);
}

function trashPrompt(count) {
  return count === 1
    ? 'Mover o documento selecionado para a lixeira? Você poderá restaurá-lo depois.'
    : `Mover os ${count} documentos selecionados para a lixeira? Você poderá restaurá-los depois.`;
}

/** Route a destructive confirmation through the shared dialog. */
function confirmThen(message, onConfirm) {
  const dialog = $('#confirm-dialog');
  if (!dialog) {
    if (window.confirm(message)) onConfirm();
    return;
  }

  const messageEl = $('#confirm-dialog-message');
  if (messageEl) messageEl.textContent = message;

  // returnValue persists across opens; reset it so a stale "confirm" from an
  // earlier dialog cannot leak through an Escape-cancel this time.
  dialog.returnValue = '';
  const handle = () => {
    dialog.removeEventListener('close', handle);
    if (dialog.returnValue === 'confirm') onConfirm();
  };
  dialog.addEventListener('close', handle);
  openDialog(dialog);
}
