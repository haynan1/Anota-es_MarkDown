/**
 * Media uploads from the editor: drag-and-drop, paste, and the toolbar button.
 *
 * A placeholder is inserted immediately and swapped for the real snippet when
 * the server answers, so a large video never leaves the writer staring at
 * nothing — and a failed upload leaves no phantom markdown behind.
 */

import { csrfToken } from './dom.js';

const UPLOAD_URL = '/api/midia';

function placeholderFor(name) {
  return `![enviando ${name}…]()`;
}

/** Replace the first occurrence of `needle`, or append at the caret. */
function replaceInTextarea(textarea, needle, replacement) {
  const index = textarea.value.indexOf(needle);
  if (index === -1) return;

  textarea.setRangeText(replacement, index, index + needle.length, 'end');
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

function insertAtCaret(textarea, text) {
  textarea.focus();
  const { selectionStart, selectionEnd } = textarea;
  textarea.setRangeText(text, selectionStart, selectionEnd, 'end');
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

async function uploadOne(file, { textarea, documentUuid, onStatus }) {
  const marker = placeholderFor(file.name);
  insertAtCaret(textarea, `\n${marker}\n`);
  onStatus(`Enviando ${file.name}…`, 'info');

  const body = new FormData();
  body.append('file', file);
  if (documentUuid) body.append('document_uuid', documentUuid);

  try {
    const response = await fetch(UPLOAD_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken(), Accept: 'application/json' },
      body,
    });

    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }

    if (response.ok && data && data.ok) {
      replaceInTextarea(textarea, marker, data.markdown);
      onStatus(`${file.name} adicionado.`, 'success');
      return true;
    }

    // Remove the placeholder: a failed upload must not leave broken markdown.
    replaceInTextarea(textarea, marker, '');
    onStatus((data && data.error) || `Não foi possível enviar ${file.name}.`, 'error');
    return false;
  } catch (error) {
    replaceInTextarea(textarea, marker, '');
    onStatus(`Não foi possível enviar ${file.name}.`, 'error');
    return false;
  }
}

/** Uploads run one at a time so a batch cannot saturate the local server. */
async function uploadAll(files, context) {
  for (const file of files) {
    // eslint-disable-next-line no-await-in-loop
    await uploadOne(file, context);
  }
}

export function initUploads({ textarea, pane, documentUuid, input, onStatus }) {
  if (!textarea) return;

  const context = { textarea, documentUuid, onStatus };

  if (input) {
    input.addEventListener('change', () => {
      if (input.files && input.files.length) {
        uploadAll(Array.from(input.files), context);
        input.value = '';
      }
    });
  }

  const dropTarget = pane || textarea;

  ['dragenter', 'dragover'].forEach((type) =>
    dropTarget.addEventListener(type, (event) => {
      if (!event.dataTransfer || !Array.from(event.dataTransfer.types).includes('Files')) {
        return;
      }
      event.preventDefault();
      dropTarget.setAttribute('data-dropping', 'true');
    })
  );

  ['dragleave', 'dragend'].forEach((type) =>
    dropTarget.addEventListener(type, () => dropTarget.removeAttribute('data-dropping'))
  );

  dropTarget.addEventListener('drop', (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    dropTarget.removeAttribute('data-dropping');
    if (!files || !files.length) return;

    event.preventDefault();
    uploadAll(Array.from(files), context);
  });

  textarea.addEventListener('paste', (event) => {
    const items = event.clipboardData && event.clipboardData.files;
    if (!items || !items.length) return;

    // Only intercept when the clipboard actually carries a file; pasting
    // text that happens to sit next to an image must still paste the text.
    event.preventDefault();
    uploadAll(Array.from(items), context);
  });
}
