/**
 * Sending files from the computer into a document: toolbar picker,
 * drag-and-drop and paste.
 *
 * Three decisions shape this module:
 *
 *   1. **A placeholder is written immediately** and swapped for the real
 *      snippet when the server answers, so a 40 MB file never leaves the
 *      writer staring at nothing — and a failed send leaves no phantom
 *      markdown behind.
 *   2. **XMLHttpRequest, not fetch.** `fetch` still cannot report upload
 *      progress; a progress bar that only jumps from 0 to 100 is worse than
 *      no progress bar on a file that takes half a minute.
 *   3. **Client-side checks are a courtesy, never a control.** They exist to
 *      fail in a tenth of a second instead of after a 50 MB round trip. The
 *      server decides what an upload really is, from its bytes.
 */

import { csrfToken } from './dom.js';

const UPLOAD_URL = '/api/midia';

// Two at a time: enough to keep a local server busy, few enough that the
// progress of any single file stays meaningful.
const MAX_PARALLEL = 2;

// How long a finished row stays on screen before the tray tidies itself up.
// Failures never expire — they need a decision from the writer.
const SETTLED_TIMEOUT_MS = 6000;

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp']);
const VIDEO_EXTENSIONS = new Set(['mp4', 'webm']);

const DEFAULT_LIMITS = { image: 10485760, video: 104857600, file: 52428800 };

let counter = 0;

/* ── Helpers ─────────────────────────────────────────────────────────────── */

export function formatBytes(size) {
  const value = Number(size) || 0;
  if (value < 1024) return `${Math.round(value)} bytes`;

  const units = ['KB', 'MB', 'GB'];
  let scaled = value;
  for (let index = 0; index < units.length; index += 1) {
    scaled /= 1024;
    if (scaled < 1024 || index === units.length - 1) {
      const rounded = Math.round(scaled * 10) / 10;
      const text = Number.isInteger(rounded)
        ? String(rounded)
        : rounded.toFixed(1).replace('.', ',');
      return `${text} ${units[index]}`;
    }
  }
  return `${scaled.toFixed(1)} GB`;
}

function extensionOf(name) {
  const match = /\.([A-Za-z0-9]+)$/.exec(name || '');
  return match ? match[1].toLowerCase() : '';
}

function kindOf(file) {
  const extension = extensionOf(file.name);
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  return 'file';
}

const KIND_NOUNS = { image: 'imagens', video: 'vídeos', file: 'arquivos' };

/** Sprite icon, built as nodes: the app ships no innerHTML outside the preview. */
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon icon-sm');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

/* ── Textarea plumbing ───────────────────────────────────────────────────── */

function placeholderFor(item) {
  return `[enviando ${item.file.name}…](#envio-${item.id})`;
}

/**
 * Replace `needle` wherever it sits, keeping the caret where the writer left
 * it. 'preserve' matters: an upload finishing must never yank the cursor out
 * of the sentence being typed.
 */
function replaceInTextarea(textarea, needle, replacement) {
  const index = textarea.value.indexOf(needle);
  if (index === -1) return false;

  textarea.setRangeText(replacement, index, index + needle.length, 'preserve');
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
}

function insertAtCaret(textarea, text) {
  const { selectionStart, selectionEnd } = textarea;
  textarea.setRangeText(text, selectionStart, selectionEnd, 'end');
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

/** Drop a placeholder and the blank line it introduced. */
function removePlaceholder(textarea, item) {
  const marker = placeholderFor(item);
  if (!replaceInTextarea(textarea, `\n${marker}\n`, '')) {
    replaceInTextarea(textarea, marker, '');
  }
}

/* ── The tray ────────────────────────────────────────────────────────────── */

const STATE_LABELS = {
  queued: 'Na fila',
  uploading: 'Enviando',
  done: 'Concluído',
  error: 'Falhou',
  canceled: 'Cancelado',
};

function createTray(host) {
  const panel = document.createElement('section');
  panel.className = 'upload-tray';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Envios do documento');

  const head = document.createElement('header');
  head.className = 'upload-tray-head';

  const title = document.createElement('h2');
  title.className = 'upload-tray-title';
  title.textContent = 'Enviando arquivos';

  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'btn btn-ghost btn-icon btn-sm';
  dismiss.setAttribute('aria-label', 'Fechar lista de envios');
  dismiss.appendChild(icon('close'));

  head.append(title, dismiss);

  const list = document.createElement('ul');
  list.className = 'upload-tray-list';
  // Progress is announced as it settles, not on every percent tick.
  list.setAttribute('aria-live', 'polite');

  panel.append(head, list);
  (host || document.body).appendChild(panel);

  return { panel, title, list, dismiss };
}

/* ── Manager ─────────────────────────────────────────────────────────────── */

export function initUploads(options) {
  const {
    textarea,
    pane,
    documentUuid,
    input,
    trayHost,
    accept = '',
    limits = DEFAULT_LIMITS,
    onStatus = () => {},
  } = options;

  if (!textarea) return null;

  const accepted = new Set(
    accept
      .split(',')
      .map((entry) => entry.trim().replace(/^\./, '').toLowerCase())
      .filter(Boolean)
  );

  const tray = createTray(trayHost);
  const items = [];
  let running = 0;

  /* ── Rendering ─────────────────────────────────────────────────────── */

  function renderRow(item) {
    const row = item.row;
    row.dataset.state = item.state;

    item.nameEl.textContent = item.file.name;
    item.stateEl.textContent =
      item.state === 'error'
        ? item.message || 'Falhou'
        : `${STATE_LABELS[item.state]} · ${formatBytes(item.file.size)}`;

    const percent = item.state === 'done' ? 100 : Math.round(item.progress * 100);
    item.barEl.value = percent;
    item.barEl.setAttribute(
      'aria-label',
      `${item.file.name}: ${STATE_LABELS[item.state].toLowerCase()}, ${percent}%`
    );

    const finished = item.state === 'done' || item.state === 'canceled';
    item.cancelEl.hidden = finished || item.state === 'error';
    item.retryEl.hidden = item.state !== 'error';
  }

  function refreshTray() {
    const active = items.filter(
      (item) => item.state === 'queued' || item.state === 'uploading'
    ).length;
    const failed = items.filter((item) => item.state === 'error').length;

    if (!items.length) {
      tray.panel.hidden = true;
      return;
    }

    tray.panel.hidden = false;
    if (active) {
      tray.title.textContent =
        active === 1 ? 'Enviando 1 arquivo' : `Enviando ${active} arquivos`;
    } else if (failed) {
      tray.title.textContent = failed === 1 ? '1 envio falhou' : `${failed} envios falharam`;
    } else {
      tray.title.textContent = 'Envios concluídos';
    }
  }

  function removeItem(item) {
    const index = items.indexOf(item);
    if (index !== -1) items.splice(index, 1);
    item.row.remove();
    refreshTray();
  }

  function scheduleCleanup(item) {
    window.clearTimeout(item.timer);
    item.timer = window.setTimeout(() => {
      // Only tidy up while nothing needs attention: a tray that vanishes
      // mid-upload looks like the upload vanished with it.
      if (items.some((other) => other.state === 'error')) return;
      removeItem(item);
    }, SETTLED_TIMEOUT_MS);
  }

  function buildRow(item) {
    const row = document.createElement('li');
    row.className = 'upload-item';

    // A native <progress>: it carries the progressbar role and its value
    // without a single inline style, which the CSP forbids anyway.
    const bar = document.createElement('progress');
    bar.className = 'upload-progress';
    bar.max = 100;
    bar.value = 0;

    const badge = document.createElement('span');
    badge.className = 'upload-badge';
    badge.setAttribute('aria-hidden', 'true');
    badge.textContent = extensionOf(item.file.name).toUpperCase().slice(0, 4) || '?';

    const text = document.createElement('span');
    text.className = 'upload-text';

    const name = document.createElement('span');
    name.className = 'upload-name';
    const state = document.createElement('span');
    state.className = 'upload-state';
    text.append(name, state);

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-ghost btn-icon btn-sm';
    cancel.setAttribute('aria-label', `Cancelar envio de ${item.file.name}`);
    cancel.appendChild(icon('close'));
    cancel.addEventListener('click', () => cancelItem(item));

    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-ghost btn-icon btn-sm';
    retry.hidden = true;
    retry.setAttribute('aria-label', `Tentar enviar ${item.file.name} novamente`);
    retry.appendChild(icon('restore'));
    retry.addEventListener('click', () => retryItem(item));

    row.append(badge, text, cancel, retry, bar);

    Object.assign(item, {
      row,
      nameEl: name,
      stateEl: state,
      barEl: bar,
      cancelEl: cancel,
      retryEl: retry,
    });

    tray.list.appendChild(row);
    return row;
  }

  /* ── Lifecycle ─────────────────────────────────────────────────────── */

  function reject(item, message) {
    item.state = 'error';
    item.message = message;
    renderRow(item);
    refreshTray();
    onStatus(message, 'error');
  }

  function settle(item, state, message) {
    item.state = state;
    item.message = message || '';
    renderRow(item);
    refreshTray();
    if (state === 'done' || state === 'canceled') scheduleCleanup(item);
  }

  function cancelItem(item) {
    if (item.request) item.request.abort();
    window.clearTimeout(item.timer);
    removePlaceholder(textarea, item);
    settle(item, 'canceled');
  }

  function retryItem(item) {
    const problem = validate(item);
    if (problem) {
      reject(item, problem);
      return;
    }

    item.state = 'queued';
    item.progress = 0;
    item.message = '';
    // The failed attempt took its placeholder with it, so a new one goes in
    // at the caret - otherwise the retry would land wherever the writer
    // happens to be when the server answers.
    insertAtCaret(textarea, `\n${placeholderFor(item)}\n`);
    renderRow(item);
    refreshTray();
    pump();
  }

  function validate(item) {
    const extension = extensionOf(item.file.name);
    // No extension at all: let the server look at the bytes and decide.
    if (extension && accepted.size && !accepted.has(extension)) {
      return `.${extension} não é um formato aceito.`;
    }
    const limit = limits[item.kind] || limits.file;
    if (limit && item.file.size > limit) {
      return `Excede o limite de ${formatBytes(limit)} para ${KIND_NOUNS[item.kind]}.`;
    }
    if (item.file.size === 0) return 'O arquivo está vazio.';
    return null;
  }

  function send(item) {
    running += 1;
    item.state = 'uploading';
    renderRow(item);
    refreshTray();

    const body = new FormData();
    body.append('file', item.file);
    if (documentUuid) body.append('document_uuid', documentUuid);

    const request = new XMLHttpRequest();
    item.request = request;
    request.open('POST', UPLOAD_URL, true);
    request.withCredentials = true;
    request.setRequestHeader('X-CSRFToken', csrfToken());
    request.setRequestHeader('Accept', 'application/json');

    request.upload.addEventListener('progress', (event) => {
      if (!event.lengthComputable) return;
      item.progress = event.loaded / event.total;
      renderRow(item);
    });

    const finish = () => {
      running -= 1;
      item.request = null;
      pump();
    };

    request.addEventListener('load', () => {
      let data = null;
      try {
        data = JSON.parse(request.responseText);
      } catch (error) {
        data = null;
      }

      if (request.status >= 200 && request.status < 300 && data && data.ok) {
        item.progress = 1;
        if (!replaceInTextarea(textarea, placeholderFor(item), data.markdown)) {
          // The writer deleted the placeholder mid-flight; the file is stored,
          // so put it where the caret is rather than losing it silently.
          insertAtCaret(textarea, data.markdown);
        }
        settle(item, 'done');
        onStatus(`${item.file.name} adicionado ao documento.`, 'success');
      } else {
        removePlaceholder(textarea, item);
        settle(item, 'error', (data && data.error) || 'Não foi possível enviar.');
        onStatus((data && data.error) || `Não foi possível enviar ${item.file.name}.`, 'error');
      }
      finish();
    });

    request.addEventListener('error', () => {
      removePlaceholder(textarea, item);
      settle(item, 'error', 'Falha de conexão com o servidor.');
      finish();
    });

    request.addEventListener('abort', finish);

    request.send(body);
  }

  function pump() {
    while (running < MAX_PARALLEL) {
      const next = items.find((item) => item.state === 'queued');
      if (!next) return;

      try {
        send(next);
      } catch (error) {
        // `running` is incremented inside send(); a throw before the request
        // is dispatched would leave the counter high and stall every upload
        // queued behind it.
        running = Math.max(0, running - 1);
        removePlaceholder(textarea, next);
        settle(next, 'error', 'Não foi possível iniciar o envio.');
      }
    }
  }

  function enqueue(files) {
    const list = Array.from(files || []);
    if (!list.length) return;

    list.forEach((file) => {
      counter += 1;
      const item = {
        id: `${Date.now().toString(36)}-${counter}`,
        file,
        kind: kindOf(file),
        state: 'queued',
        progress: 0,
        message: '',
        request: null,
        timer: 0,
      };
      items.push(item);
      buildRow(item);

      const problem = validate(item);
      if (problem) {
        reject(item, problem);
        return;
      }

      insertAtCaret(textarea, `\n${placeholderFor(item)}\n`);
      renderRow(item);
    });

    refreshTray();
    pump();
  }

  tray.dismiss.addEventListener('click', () => {
    items.slice().forEach((item) => {
      if (item.state === 'uploading' || item.state === 'queued') return;
      removeItem(item);
    });
    if (!items.length) tray.panel.hidden = true;
  });

  /* ── Entry points ──────────────────────────────────────────────────── */

  if (input) {
    input.addEventListener('change', () => {
      if (input.files && input.files.length) enqueue(input.files);
      input.value = '';
    });
  }

  const dropTarget = pane || textarea;
  let dragDepth = 0;

  const carriesFiles = (event) =>
    Boolean(event.dataTransfer) &&
    Array.from(event.dataTransfer.types || []).includes('Files');

  dropTarget.addEventListener('dragenter', (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    dropTarget.setAttribute('data-dropping', 'true');
  });

  dropTarget.addEventListener('dragover', (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    // Without this the browser may show a "move" cursor and refuse the drop.
    event.dataTransfer.dropEffect = 'copy';
  });

  // dragleave fires when crossing into a child element too; the counter keeps
  // the overlay from flickering as the pointer moves over the text.
  dropTarget.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) dropTarget.removeAttribute('data-dropping');
  });

  dropTarget.addEventListener('dragend', () => {
    dragDepth = 0;
    dropTarget.removeAttribute('data-dropping');
  });

  dropTarget.addEventListener('drop', (event) => {
    dragDepth = 0;
    dropTarget.removeAttribute('data-dropping');
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;

    event.preventDefault();
    // A drop lands where the writer aimed, not where the caret happened to be.
    if (typeof document.caretPositionFromPoint === 'function') {
      const position = document.caretPositionFromPoint(event.clientX, event.clientY);
      if (position && position.offsetNode === textarea) {
        textarea.setSelectionRange(position.offset, position.offset);
      }
    }
    enqueue(files);
  });

  textarea.addEventListener('paste', (event) => {
    const files = event.clipboardData && event.clipboardData.files;
    if (!files || !files.length) return;

    // Only intercept when the clipboard actually carries a file; pasting text
    // that happens to sit next to an image must still paste the text.
    event.preventDefault();
    enqueue(files);
  });

  return { enqueue };
}
