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
 * Two selections, not one
 * -----------------------
 * A page of checkboxes answers "these documents". A library does not fit on a
 * page, so once every box on it is ticked the bar offers the other question:
 * *every* result of the current filters. That mode sends no identifiers at
 * all — only a flag — and the server re-runs the same query the page was
 * rendered from. Nothing here has to know how many documents that is, and no
 * request has to carry them.
 *
 * Progressive enhancement: the selection column and the bar are inert in
 * markup and activated here. With scripting off there is simply no bulk UI,
 * and every single-document action still works.
 */

import { $, $$, openDialog } from './dom.js';

/** Value of the `selecao` field that asks the server to resolve the filters. */
const MODE_FILTER = 'filtro';

export function initBulkSelect() {
  const bar = $('[data-bulk-bar]');
  if (!bar) return;

  // A programmatic submit() ignores the submitter, and with it any formaction
  // the button carried. Captured once so every submit can restore it: without
  // this, exporting a selection would leave the bar permanently pointed at the
  // export route and the next "archive" would download a ZIP.
  const defaultAction = bar.getAttribute('action');

  // How many documents "todos os resultados" actually reaches — the server's
  // ceiling, not this page's arithmetic.
  const filterTotal = Number(bar.dataset.bulkTotal || 0);

  const boxes = () => $$('.doc-select');
  const selected = () => boxes().filter((box) => box.checked);

  const countLabel = $('[data-bulk-count]', bar);
  const pluralMark = $('[data-bulk-plural]', bar);
  const scopeMark = $('[data-bulk-bar-scope]', bar);
  const selectAll = $('[data-bulk-select-all]');

  const scope = $('[data-bulk-scope]');
  const scopeOffer = $('[data-bulk-scope-page]');
  const scopeActive = $('[data-bulk-scope-all]');

  // True once the user asked for every result rather than for these boxes.
  let everything = false;

  // Turn on the selection column and reveal the "select all" control now that
  // JS is running.
  $$('[data-doc-list]').forEach((list) => list.classList.add('is-selectable'));
  $$('[data-bulk-only]').forEach((el) => { el.hidden = false; });

  /** How many documents the next action would touch. */
  function count() {
    return everything ? filterTotal : selected().length;
  }

  function sync() {
    const chosen = selected();
    const total = boxes().length;
    const pageIsFull = total > 0 && chosen.length === total;

    // Ticking or unticking anything is a statement about *these* documents,
    // so it always drops back out of "every result".
    if (everything && !pageIsFull) everything = false;

    const active = count();
    if (countLabel) countLabel.textContent = String(active);
    if (pluralMark) pluralMark.hidden = active === 1;
    if (scopeMark) scopeMark.hidden = !everything;
    bar.hidden = active === 0;

    if (selectAll) {
      selectAll.checked = pageIsFull;
      selectAll.indeterminate = chosen.length > 0 && !pageIsFull;
    }

    // The offer to widen the selection is only meaningful once the page it
    // would widen is itself fully chosen.
    if (scope) scope.hidden = !pageIsFull;
    if (scopeOffer) scopeOffer.hidden = everything;
    if (scopeActive) scopeActive.hidden = !everything;
  }

  document.addEventListener('change', (event) => {
    if (event.target instanceof HTMLElement && event.target.classList.contains('doc-select')) {
      everything = false;
      sync();
    }
  });

  if (selectAll) {
    selectAll.addEventListener('change', () => {
      if (!selectAll.checked) everything = false;
      boxes().forEach((box) => { box.checked = selectAll.checked; });
      sync();
    });
  }

  // Each of these two buttons hides itself and reveals the other, so focus has
  // to be handed over deliberately — otherwise the keyboard lands back on
  // <body> and the change is announced to nobody.
  const selectEverything = $('[data-bulk-select-everything]');
  const selectPage = $('[data-bulk-select-page]');

  if (selectEverything) {
    selectEverything.addEventListener('click', () => {
      everything = true;
      boxes().forEach((box) => { box.checked = true; });
      sync();
      if (selectPage) selectPage.focus();
    });
  }

  if (selectPage) {
    selectPage.addEventListener('click', () => {
      everything = false;
      sync();
      if (selectEverything) selectEverything.focus();
    });
  }

  const clear = $('[data-bulk-clear]', bar);
  if (clear) {
    clear.addEventListener('click', () => {
      everything = false;
      boxes().forEach((box) => { box.checked = false; });
      sync();
    });
  }

  bar.addEventListener('click', (event) => {
    const button = event.target.closest('button[name="acao"]');
    if (!button) return;

    event.preventDefault();
    if (!count()) return;

    // "Mover" and "Agrupar" each need a destination. Asking for one here — on
    // the control the user is looking at — beats a round trip that comes back
    // with the selection gone and an error at the top of the page.
    const required = requiredField(button);
    if (required) {
      required.focus();
      return;
    }

    // Only the destructive action asks first; the rest are reversible.
    if (button.value === 'trash') {
      confirmThen(trashPrompt(count()), () => submitWith(button));
    } else {
      submitWith(button);
    }
  });

  /** The empty <select> this button depends on, if it has one. */
  function requiredField(button) {
    const id = button.dataset.bulkRequires;
    if (!id) return null;
    const field = document.getElementById(id);
    return field && !field.value ? field : null;
  }

  function submitWith(button) {
    // Drop anything a previous, cancelled attempt may have injected.
    $$('input[data-bulk-injected]', bar).forEach((el) => el.remove());
    // getAttribute, not .formAction: the property falls back to the form's own
    // action, so every button would look like it had one.
    bar.setAttribute('action', button.getAttribute('formaction') || defaultAction);
    appendHidden(bar, 'acao', button.value);

    if (everything) {
      // No identifiers: the `filtros` field is already in the markup, and the
      // server resolves the set from it.
      appendHidden(bar, 'selecao', MODE_FILTER);
    } else {
      selected().forEach((box) => appendHidden(bar, 'uuids', box.dataset.uuid));
    }
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
