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

import { $, $$, postJSON, openDialog, closeDialog } from './dom.js';
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

  // A `display: none` node cannot be selected, and the whole-document preview
  // is hidden while the checklist is on screen. Show it for the length of the
  // copy — the alternative is a button that silently does nothing on the one
  // path that exists because the Clipboard API was unavailable.
  const wasHidden = node.hidden;
  node.hidden = false;

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
  node.hidden = wasHidden;
  return copied;
}

async function copyPlain(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  return false;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function icon(name, cls = 'icon icon-sm') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

export function initRichCopy({ textarea, dialog }) {
  if (!textarea || !dialog) return null;

  const preview = $('[data-rich-preview]', dialog);
  const notesList = $('[data-rich-notes]', dialog);
  const notesBox = $('[data-rich-notes-box]', dialog);
  const empty = $('[data-rich-empty]', dialog);
  const actions = $('[data-rich-actions]', dialog);
  const plan = $('[data-rich-plan]', dialog);
  const planText = $('[data-rich-plan-text]', dialog);
  const progress = $('[data-rich-progress]', dialog);
  const partsHost = $('[data-rich-parts]', dialog);
  const copyLabel = $('[data-rich-copy-label]', dialog);

  let current = { html: '', text: '' };
  let parts = [];
  const copied = new Set();

  function renderNotes(notes) {
    notesList.textContent = '';
    (notes || []).forEach((note) => {
      const item = document.createElement('li');
      item.textContent = note;
      notesList.appendChild(item);
    });
    notesBox.hidden = !(notes && notes.length);
  }

  /* ── Copiar em partes ─────────────────────────────────────────────────
     Splitting only earns its complexity when there is something to split
     around: with no pictures the dialog stays exactly as it was. */

  /** Parts with text in them - the only ones there is anything to copy from. */
  function copyable() {
    return parts.filter((part) => part.html);
  }

  function updateProgress() {
    const total = copyable().length;
    const done = copied.size;

    progress.textContent =
      done === total
        ? 'Todas as partes foram copiadas.'
        : `${done} de ${total} partes copiadas.`;

    // The first part not yet copied is "where I am"; it is the only one shown
    // as current, so the eye lands on it when the writer comes back from Wix.
    const cards = $$('[data-part]', partsHost);
    const next = cards.find((card) => !copied.has(Number(card.dataset.part)));
    cards.forEach((card) => {
      const index = Number(card.dataset.part);
      card.dataset.state = copied.has(index) ? 'done' : card === next ? 'current' : 'todo';
    });
  }

  function markCopied(index) {
    copied.add(index);
    const button = $(`[data-copy-part="${index}"]`, partsHost);
    if (button) {
      button.textContent = '';
      button.append(icon('check'), document.createTextNode('Copiado'));
      button.classList.remove('btn-primary');
    }
    updateProgress();
  }

  function buildCard(part, index, position, total) {
    const card = document.createElement('section');
    card.className = 'rich-part';
    card.dataset.part = String(index);

    const head = document.createElement('header');
    head.className = 'rich-part-head';

    const title = document.createElement('h3');
    title.className = 'rich-part-title';
    title.textContent = `Parte ${position} de ${total}`;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-primary rich-part-copy';
    button.dataset.copyPart = String(index);
    button.setAttribute('aria-label', `Copiar a parte ${position} de ${total}`);
    button.append(icon('copy'), document.createTextNode('Copiar'));

    head.append(title, button);

    const body = document.createElement('div');
    body.className = 'rich-part-body markdown-body';
    // Safe to assign: built and sanitized by the server against a list
    // strictly smaller than the one used for the document itself.
    body.innerHTML = part.html;

    card.append(head, body);
    partsHost.appendChild(card);
  }

  function buildStep(media) {
    const step = document.createElement('p');
    step.className = 'rich-part-media';
    step.append(
      icon(media.kind === 'vídeo' ? 'upload' : 'image'),
      document.createTextNode(`Agora suba na Wix: ${media.description}`)
    );
    partsHost.appendChild(step);
  }

  function renderParts(list) {
    const next = list || [];
    // Reopening the dialog after a trip to Wix must not erase the checklist -
    // remembering where you were is the whole point of splitting. The marks
    // are kept only while the parts are byte-for-byte the ones already
    // copied; the moment the document changes they would be lying.
    const unchanged =
      next.length === parts.length && next.every((part, index) => part.html === parts[index].html);

    parts = next;
    if (!unchanged) copied.clear();
    partsHost.textContent = '';

    const withText = copyable();
    const uploads = parts.filter((part) => part.media).length;
    // One stretch of text is one paste, however many pictures surround it -
    // there is no sequence to follow, so there is no checklist to show.
    const split = withText.length > 1;

    plan.hidden = !split;
    partsHost.hidden = !split;
    preview.hidden = split;
    if (copyLabel) {
      copyLabel.textContent = split ? 'Copiar tudo de uma vez' : 'Copiar texto formatado';
    }

    if (!split) return;

    const media = uploads === 1 ? '1 imagem ou vídeo' : `${uploads} imagens e vídeos`;
    planText.textContent =
      `Este documento tem ${media} no meio do texto, e a Wix aceita um de cada vez. ` +
      'Copie uma parte, cole na descrição, suba a mídia indicada e volte aqui para a próxima.';

    let position = 0;
    parts.forEach((part, index) => {
      if (part.html) {
        position += 1;
        buildCard(part, index, position, withText.length);
        if (copied.has(index)) markCopied(index);
      }
      if (part.media) buildStep(part.media);
    });

    updateProgress();
  }

  async function copyPart(index) {
    const part = parts[index];
    if (!part) return;

    try {
      if (await writeClipboard(part.html, part.text)) {
        markCopied(index);
        toast(`Parte ${index + 1} copiada. Cole na Wix e volte.`, 'success');
        return;
      }
    } catch (error) {
      /* falls through to the selection-based copy */
    }

    const body = $(`[data-part="${index}"] .rich-part-body`, partsHost);
    if (body && copySelection(body)) {
      markCopied(index);
      toast(`Parte ${index + 1} copiada. Cole na Wix e volte.`, 'success');
      return;
    }
    toast('Não foi possível copiar. Selecione o texto da parte e use Ctrl+C.', 'error');
  }

  async function open() {
    preview.textContent = 'Preparando…';
    preview.hidden = false;
    plan.hidden = true;
    partsHost.hidden = true;
    partsHost.textContent = '';
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
    renderParts(data.parts);
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
    const partButton = event.target.closest('[data-copy-part]');
    if (partButton) {
      const index = Number(partButton.dataset.copyPart);
      run(() => copyPart(index), 'Não foi possível copiar esta parte.');
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
