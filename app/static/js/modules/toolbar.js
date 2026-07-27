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

/* ── Alignment ──────────────────────────────────────────────────────────────
   Alignment is a property of a block, not of a word, so these helpers always
   work on whole blocks: the fence pair the caret is inside, or the paragraphs
   the selection touches. What gets written is `::: centro` … `:::` — the
   syntax rendered by app/services/align_service.py.
   ------------------------------------------------------------------------- */

/** Every keyword the renderer accepts, mapped to the one we write. */
const ALIGN_KEYWORDS = {
  esquerda: 'esquerda', left: 'esquerda',
  centro: 'centro', centralizado: 'centro', center: 'centro',
  direita: 'direita', right: 'direita',
  justificado: 'justificado', justificar: 'justificado', justify: 'justificado',
};

/** Left is what Markdown already does, so it is spelled as "no fence at all". */
export const DEFAULT_ALIGNMENT = 'esquerda';

const ALIGN_PLACEHOLDERS = {
  centro: 'texto centralizado',
  direita: 'texto à direita',
  justificado: 'texto justificado',
};

const FENCE_OPEN_RE = /^ {0,3}:{3,}[ \t]*([A-Za-z]+)[ \t]*$/;
const FENCE_CLOSE_RE = /^ {0,3}:{3,}[ \t]*$/;

// The scan runs on every cursor move, so it is bounded: a pathological
// document must not be able to turn a keystroke into a full-file walk.
const MAX_SCAN_LINES = 4000;

function lineStartAt(value, index) {
  return value.lastIndexOf('\n', Math.max(index - 1, 0)) + 1;
}

function lineEndAt(value, index) {
  const end = value.indexOf('\n', index);
  return end === -1 ? value.length : end;
}

/** Start of the line above the one beginning at `start`, or -1 at the top. */
function lineAbove(value, start) {
  if (start <= 0) return -1;
  const from = start - 2;
  return (from < 0 ? -1 : value.lastIndexOf('\n', from)) + 1;
}

/** Start of the line after the one ending at `end`, or -1 at the bottom. */
function lineBelow(value, end) {
  return end >= value.length ? -1 : end + 1;
}

function lineFrom(value, start) {
  return value.slice(start, lineEndAt(value, start));
}

/**
 * The aligned block containing the selection, or null.
 *
 * Read upwards first: an opening fence before any closing one means we are
 * inside it. Then downwards for the fence that shuts it.
 */
function alignedRegion(value, selectionStart, selectionEnd) {
  const caretLine = lineStartAt(value, selectionStart);

  let openStart = -1;
  let keyword = '';
  for (let start = caretLine, steps = 0; start !== -1 && steps < MAX_SCAN_LINES; steps += 1) {
    const line = lineFrom(value, start);
    const open = FENCE_OPEN_RE.exec(line);
    if (open) {
      // An unknown keyword is not ours: the renderer leaves it as text.
      if (!ALIGN_KEYWORDS[open[1].toLowerCase()]) return null;
      openStart = start;
      keyword = ALIGN_KEYWORDS[open[1].toLowerCase()];
      break;
    }
    // A closing fence *above* the caret belongs to a block already shut.
    if (start < caretLine && FENCE_CLOSE_RE.test(line)) return null;
    start = lineAbove(value, start);
  }
  if (openStart === -1) return null;

  let closeStart = -1;
  const from = Math.max(lineStartAt(value, selectionEnd), openStart);
  for (let start = from, steps = 0; start !== -1 && steps < MAX_SCAN_LINES; steps += 1) {
    if (start > openStart) {
      const line = lineFrom(value, start);
      if (FENCE_CLOSE_RE.test(line)) {
        closeStart = start;
        break;
      }
      // A second opening fence means ours was never closed.
      if (FENCE_OPEN_RE.test(line)) return null;
    }
    start = lineBelow(value, lineEndAt(value, start));
  }
  if (closeStart === -1) return null;

  const contentStart = Math.min(lineEndAt(value, openStart) + 1, value.length);
  return {
    keyword,
    start: openStart,
    end: lineEndAt(value, closeStart),
    contentStart,
    // Stops before the newline that precedes the closing fence.
    contentEnd: Math.max(contentStart, closeStart - 1),
  };
}

/** The paragraphs the selection touches, grown to blank-line boundaries. */
function paragraphBounds(value, selectionStart, selectionEnd) {
  const stops = (line) => !line.trim() || FENCE_OPEN_RE.test(line) || FENCE_CLOSE_RE.test(line);

  let start = lineStartAt(value, selectionStart);
  for (let steps = 0; steps < MAX_SCAN_LINES; steps += 1) {
    const above = lineAbove(value, start);
    if (above === -1 || stops(lineFrom(value, above))) break;
    start = above;
  }

  let end = lineEndAt(value, selectionEnd);
  for (let steps = 0; steps < MAX_SCAN_LINES; steps += 1) {
    const below = lineBelow(value, end);
    if (below === -1) break;
    const line = lineFrom(value, below);
    if (stops(line)) break;
    end = below + line.length;
  }

  // A selection that ends on the newline itself must not drag the blank line
  // after it into the block.
  while (end > start && value[end - 1] === '\n') end -= 1;

  return { start, end };
}

function replaceRange(textarea, start, end, text) {
  textarea.setSelectionRange(start, end);
  insertText(textarea, text);
}

/** The alignment in force where the caret is. */
export function currentAlignment(textarea) {
  if (!textarea) return DEFAULT_ALIGNMENT;
  const region = alignedRegion(textarea.value, textarea.selectionStart, textarea.selectionEnd);
  return region ? region.keyword : DEFAULT_ALIGNMENT;
}

/**
 * Align the current block. Asking for the alignment it already has - or for
 * "esquerda", which is the default - removes the fences instead of nesting
 * another pair.
 */
export function applyAlign(textarea, name) {
  if (!textarea) return;

  const target = ALIGN_KEYWORDS[name];
  if (!target) return;

  const value = textarea.value;
  const region = alignedRegion(value, textarea.selectionStart, textarea.selectionEnd);

  if (region) {
    if (target === region.keyword || target === DEFAULT_ALIGNMENT) {
      const inner = value.slice(region.contentStart, region.contentEnd);
      replaceRange(textarea, region.start, region.end, inner);
      textarea.setSelectionRange(region.start, region.start + inner.length);
      return;
    }

    // Only the keyword changes: the text does not move, so neither should the
    // caret - beyond the handful of characters the word itself grew or shrank.
    const openEnd = lineEndAt(value, region.start);
    const opening = `::: ${target}`;
    const shift = opening.length - (openEnd - region.start);
    const { selectionStart, selectionEnd } = textarea;

    replaceRange(textarea, region.start, openEnd, opening);
    textarea.setSelectionRange(selectionStart + shift, selectionEnd + shift);
    return;
  }

  // Nothing to remove and nothing to add: left is what plain Markdown does.
  if (target === DEFAULT_ALIGNMENT) return;

  const bounds = paragraphBounds(value, textarea.selectionStart, textarea.selectionEnd);
  const selected = value.slice(bounds.start, bounds.end);
  const body = selected.trim() ? selected : ALIGN_PLACEHOLDERS[target];
  const opening = `::: ${target}`;

  // The fences have to start a block of their own, so a blank line is added
  // wherever the surrounding text does not already provide one.
  const before = value.slice(0, bounds.start);
  const after = value.slice(bounds.end);
  let prefix = '';
  if (before && !before.endsWith('\n\n')) prefix = before.endsWith('\n') ? '\n' : '\n\n';
  let suffix = '';
  if (after && !after.startsWith('\n\n')) suffix = after.startsWith('\n') ? '\n' : '\n\n';

  replaceRange(textarea, bounds.start, bounds.end, `${prefix}${opening}\n${body}\n:::${suffix}`);

  // Land on the text, not on the syntax: with no selection the placeholder is
  // selected, so the next keystroke replaces it.
  const from = bounds.start + prefix.length + opening.length + 1;
  textarea.setSelectionRange(from, from + body.length);
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
