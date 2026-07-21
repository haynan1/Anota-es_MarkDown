/**
 * Markdown toolbar and text-insertion helpers.
 *
 * Insertions go through `document.execCommand('insertText')` when available so
 * the browser's native undo stack keeps working; `setRangeText` is the
 * fallback.
 */

const WRAP = 'wrap';
const LINE = 'line';
const BLOCK = 'block';

const ACTIONS = {
  heading: { kind: LINE, prefix: '## ' },
  quote: { kind: LINE, prefix: '> ' },
  list: { kind: LINE, prefix: '- ' },
  checklist: { kind: LINE, prefix: '- [ ] ' },

  bold: { kind: WRAP, before: '**', after: '**', placeholder: 'texto em negrito' },
  italic: { kind: WRAP, before: '*', after: '*', placeholder: 'texto em itálico' },
  link: { kind: WRAP, before: '[', after: '](https://)', placeholder: 'texto do link' },
  image: { kind: WRAP, before: '![', after: '](https://)', placeholder: 'descrição da imagem' },

  code: {
    kind: BLOCK,
    template: '```\n{selection}\n```',
    placeholder: 'seu código aqui',
  },
  table: {
    kind: BLOCK,
    template:
      '| Coluna A | Coluna B |\n' +
      '|:---------|:---------|\n' +
      '| valor    | valor    |\n',
  },
  rule: { kind: BLOCK, template: '\n---\n' },
};

/** Replace the current selection, preserving undo history where possible. */
function insertText(textarea, text) {
  textarea.focus();
  let inserted = false;
  try {
    inserted = document.execCommand('insertText', false, text);
  } catch (error) {
    inserted = false;
  }

  if (!inserted) {
    const { selectionStart, selectionEnd } = textarea;
    textarea.setRangeText(text, selectionStart, selectionEnd, 'end');
  }
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

function selectedText(textarea) {
  return textarea.value.slice(textarea.selectionStart, textarea.selectionEnd);
}

function applyWrap(textarea, action) {
  const selection = selectedText(textarea);
  const body = selection || action.placeholder || '';
  const start = textarea.selectionStart;

  insertText(textarea, `${action.before}${body}${action.after}`);

  if (!selection) {
    // Put the caret on the placeholder so the user can just start typing.
    const from = start + action.before.length;
    textarea.setSelectionRange(from, from + body.length);
  }
}

function applyLine(textarea, action) {
  const value = textarea.value;
  const lineStart = value.lastIndexOf('\n', textarea.selectionStart - 1) + 1;
  let lineEnd = value.indexOf('\n', textarea.selectionEnd);
  if (lineEnd === -1) lineEnd = value.length;

  const block = value.slice(lineStart, lineEnd);
  const lines = block.split('\n');
  const alreadyApplied = lines.every((line) => line.startsWith(action.prefix));

  const updated = lines
    .map((line) => (alreadyApplied ? line.slice(action.prefix.length) : action.prefix + line))
    .join('\n');

  textarea.setSelectionRange(lineStart, lineEnd);
  insertText(textarea, updated);
  textarea.setSelectionRange(lineStart, lineStart + updated.length);
}

function applyBlock(textarea, action) {
  const selection = selectedText(textarea);
  const value = textarea.value;
  const atLineStart = textarea.selectionStart === 0 || value[textarea.selectionStart - 1] === '\n';

  const body = action.template.replace('{selection}', selection || action.placeholder || '');
  insertText(textarea, (atLineStart ? '' : '\n') + body);
}

export function applyAction(textarea, name) {
  const action = ACTIONS[name];
  if (!action || !textarea) return;

  if (action.kind === WRAP) applyWrap(textarea, action);
  else if (action.kind === LINE) applyLine(textarea, action);
  else applyBlock(textarea, action);
}

/** Tab indents (or outdents with Shift) instead of leaving the editor. */
export function handleTab(textarea, event) {
  const value = textarea.value;
  const { selectionStart, selectionEnd } = textarea;
  const multiline = value.slice(selectionStart, selectionEnd).includes('\n');

  if (!multiline && !event.shiftKey) {
    event.preventDefault();
    insertText(textarea, '    ');
    return;
  }

  event.preventDefault();
  const lineStart = value.lastIndexOf('\n', selectionStart - 1) + 1;
  let lineEnd = value.indexOf('\n', selectionEnd);
  if (lineEnd === -1) lineEnd = value.length;

  const updated = value
    .slice(lineStart, lineEnd)
    .split('\n')
    .map((line) => {
      if (event.shiftKey) {
        return line.replace(/^ {1,4}/, '');
      }
      return `    ${line}`;
    })
    .join('\n');

  textarea.setSelectionRange(lineStart, lineEnd);
  insertText(textarea, updated);
  textarea.setSelectionRange(lineStart, lineStart + updated.length);
}

export function initToolbar(textarea, root) {
  root.addEventListener('click', (event) => {
    const button = event.target.closest('[data-md]');
    if (!button) return;
    event.preventDefault();
    applyAction(textarea, button.getAttribute('data-md'));
  });
}
