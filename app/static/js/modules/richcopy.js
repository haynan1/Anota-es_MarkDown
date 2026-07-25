/**
 * "Copiar para a Wix": puts the document on the clipboard as formatted text.
 *
 * The clipboard carries two flavours of the same content — `text/html` for
 * editors that understand rich text (the Wix description field, Google Docs,
 * Word) and `text/plain` for everything else. That pair is exactly what a
 * copy from a word processor looks like, which is why it pastes as formatting
 * instead of as literal markup.
 *
 * `navigator.clipboard.write` needs a secure context. localhost is one, so
 * the normal case is covered; when the app is reached by IP over plain HTTP
 * the API is missing entirely, and the fallback selects an offscreen node and
 * lets the browser's own copy command do the work.
 */

import { $, postJSON, openDialog, closeDialog } from './dom.js';
import { toast } from './toasts.js';

const RICH_TEXT_URL = '/api/texto-rico';

/** Copy `html` (with its plain-text twin) using the Clipboard API. */
async function writeClipboard(html, text) {
  if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
    const item = new window.ClipboardItem({
      'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([text], { type: 'text/plain' }),
    });
    await navigator.clipboard.write([item]);
    return true;
  }
  return false;
}

/**
 * Fallback for non-secure contexts: select the rendered preview and copy the
 * selection. The browser builds both flavours from the live DOM, so the paste
 * keeps its formatting exactly as the Clipboard API would.
 */
function copySelection(node) {
  const selection = window.getSelection();
  if (!selection || !node) return false;

  const range = document.createRange();
  range.selectNodeContents(node);
  selection.removeAllRanges();
  selection.addRange(range);

  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch (error) {
    copied = false;
  }
  selection.removeAllRanges();
  return copied;
}

async function copyPlain(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  return false;
}

export function initRichCopy({ textarea, dialog }) {
  if (!textarea || !dialog) return null;

  const preview = $('[data-rich-preview]', dialog);
  const notesList = $('[data-rich-notes]', dialog);
  const notesBox = $('[data-rich-notes-box]', dialog);
  const empty = $('[data-rich-empty]', dialog);
  const actions = $('[data-rich-actions]', dialog);

  let current = { html: '', text: '' };

  function renderNotes(notes) {
    notesList.textContent = '';
    (notes || []).forEach((note) => {
      const item = document.createElement('li');
      item.textContent = note;
      notesList.appendChild(item);
    });
    notesBox.hidden = !(notes && notes.length);
  }

  async function open() {
    preview.textContent = 'Preparando…';
    notesBox.hidden = true;
    empty.hidden = true;
    actions.hidden = true;
    openDialog(dialog);

    const { ok, data } = await postJSON(RICH_TEXT_URL, {
      content_markdown: textarea.value,
    });

    if (!ok || !data || !data.ok) {
      preview.textContent = '';
      empty.hidden = false;
      empty.textContent =
        (data && data.error) || 'Não foi possível preparar o texto para colar.';
      return;
    }

    current = { html: data.html || '', text: data.text || '' };

    if (!current.html) {
      preview.textContent = '';
      empty.hidden = false;
      empty.textContent = 'Este documento ainda está vazio.';
      return;
    }

    // Safe to assign: this HTML was built and sanitized by the server against
    // a list strictly smaller than the one used for the document itself.
    preview.innerHTML = current.html;
    renderNotes(data.notes);
    actions.hidden = false;
  }

  async function copyRich() {
    try {
      if (await writeClipboard(current.html, current.text)) {
        toast('Texto formatado copiado. Cole na descrição do produto.', 'success');
        return;
      }
    } catch (error) {
      /* falls through to the selection-based copy */
    }

    if (copySelection(preview)) {
      toast('Texto formatado copiado. Cole na descrição do produto.', 'success');
      return;
    }
    toast('Não foi possível copiar. Selecione o texto acima e use Ctrl+C.', 'error');
  }

  async function copyText() {
    try {
      if (await copyPlain(current.text)) {
        toast('Texto simples copiado.', 'success');
        return;
      }
    } catch (error) {
      /* falls through */
    }
    toast('Não foi possível copiar. Selecione o texto acima e use Ctrl+C.', 'error');
  }

  /** No async handler leaves the page without a report: a silent rejection
      here would look like a button that simply does nothing. */
  function run(task, message) {
    task().catch(() => toast(message, 'error'));
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="open-rich-copy"]')) {
      event.preventDefault();
      run(open, 'Não foi possível preparar o texto para colar.');
    }
    if (event.target.closest('[data-action="copy-rich"]')) {
      run(copyRich, 'Não foi possível copiar. Selecione o texto e use Ctrl+C.');
    }
    if (event.target.closest('[data-action="copy-plain"]')) {
      run(copyText, 'Não foi possível copiar. Selecione o texto e use Ctrl+C.');
    }
    if (event.target.closest('[data-action="close-rich-copy"]')) closeDialog(dialog);
  });

  return { open };
}
