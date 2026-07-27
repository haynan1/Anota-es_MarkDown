/**
 * Alinhamento no editor: as transformações de texto da barra de ferramentas.
 *
 * This is the densest piece of logic on the front end - it edits ranges of the
 * writer's own text - so it is tested the same way the renderer is, and by the
 * same `pytest` command (see tests/test_editor_alignment.py).
 *
 * No test runner and no dependencies: the module under test is plain ESM, and
 * a textarea is small enough to stand in for. Run it directly with
 * `node tests/js/align.test.mjs`.
 */

import { applyAlign, currentAlignment, DEFAULT_ALIGNMENT } from '../../app/static/js/modules/toolbar.js';

// `insertText` prefers the browser's undo-preserving command and falls back to
// `setRangeText`; with no `document.execCommand` here, the fallback is what
// every case below exercises.
globalThis.document = { execCommand: () => false };
globalThis.Event = globalThis.Event || class { constructor(type) { this.type = type; } };

class FakeTextarea {
  constructor(value, start, end) {
    this.value = value;
    this.selectionStart = start;
    this.selectionEnd = end === undefined ? start : end;
  }

  focus() {}

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  setRangeText(text, start, end, mode) {
    this.value = this.value.slice(0, start) + text + this.value.slice(end);
    if (mode === 'end') this.selectionStart = this.selectionEnd = start + text.length;
  }

  dispatchEvent() {
    return true;
  }
}

/** `|` marks the caret; a second `|` marks the end of the selection. */
function field(marked) {
  const start = marked.indexOf('|');
  if (start === -1) throw new Error('faltou marcar o cursor com |');
  const rest = marked.slice(0, start) + marked.slice(start + 1);
  const end = rest.indexOf('|');
  if (end === -1) return new FakeTextarea(rest, start);
  return new FakeTextarea(rest.slice(0, end) + rest.slice(end + 1), start, end);
}

const selectionOf = (textarea) =>
  textarea.value.slice(textarea.selectionStart, textarea.selectionEnd);

let failures = 0;

function check(name, actual, expected) {
  if (actual === expected) {
    console.log(`ok   ${name}`);
    return;
  }
  failures += 1;
  console.log(`FAIL ${name}`);
  console.log(`     esperado: ${JSON.stringify(expected)}`);
  console.log(`     obtido:   ${JSON.stringify(actual)}`);
}

function align(marked, alignment) {
  const textarea = field(marked);
  applyAlign(textarea, alignment);
  return textarea;
}

/* ── Envolver o bloco ──────────────────────────────────────────────────── */

check(
  'o parágrafo inteiro é envolvido, não só a palavra sob o cursor',
  align('Um parágrafo |qualquer.', 'centro').value,
  '::: centro\nUm parágrafo qualquer.\n:::'
);

check(
  'o texto alinhado fica selecionado, e não a sintaxe',
  selectionOf(align('Um parágrafo |qualquer.', 'centro')),
  'Um parágrafo qualquer.'
);

check(
  'os parágrafos vizinhos não são tocados',
  align('Antes.\n\nAlvo |aqui.\n\nDepois.', 'direita').value,
  'Antes.\n\n::: direita\nAlvo aqui.\n:::\n\nDepois.'
);

check(
  'linhas contíguas contam como um bloco só',
  align('Linha um\nLinha |dois\nLinha três', 'centro').value,
  '::: centro\nLinha um\nLinha dois\nLinha três\n:::'
);

check(
  'uma seleção de vários parágrafos é envolvida inteira',
  align('|Um.\n\nDois.|\n\nTrês.', 'centro').value,
  '::: centro\nUm.\n\nDois.\n:::\n\nTrês.'
);

check(
  'em linha vazia entra um texto de exemplo',
  align('Antes.\n\n|', 'centro').value,
  'Antes.\n\n::: centro\ntexto centralizado\n:::'
);

check(
  'o texto de exemplo já vem selecionado, pronto para ser digitado por cima',
  selectionOf(align('Antes.\n\n|', 'centro')),
  'texto centralizado'
);

check(
  'títulos e listas entram no bloco como qualquer outra linha',
  align('- item um\n- item |dois', 'centro').value,
  '::: centro\n- item um\n- item dois\n:::'
);

check(
  'um bloco vizinho nunca é absorvido',
  align('::: centro\na\n:::\n\nAlvo |aqui.', 'direita').value,
  '::: centro\na\n:::\n\n::: direita\nAlvo aqui.\n:::'
);

/* ── Ler o estado ──────────────────────────────────────────────────────── */

check('fora de qualquer bloco, o padrão', currentAlignment(field('Texto |normal')), DEFAULT_ALIGNMENT);
check('dentro do bloco', currentAlignment(field('::: centro\nTexto |aqui\n:::')), 'centro');
check('na própria cerca de abertura', currentAlignment(field('::: cen|tro\nTexto\n:::')), 'centro');
check('na própria cerca de fechamento', currentAlignment(field('::: centro\nTexto\n:|::')), 'centro');
check('depois do bloco fechado', currentAlignment(field('::: centro\nTexto\n:::\n\nDe|pois')), DEFAULT_ALIGNMENT);
check('antes do bloco', currentAlignment(field('An|tes\n\n::: centro\nTexto\n:::')), DEFAULT_ALIGNMENT);
check('sinônimo em inglês é reconhecido', currentAlignment(field('::: center\nTex|to\n:::')), 'centro');

// O renderizador deixa estes dois como texto; o editor tem de concordar com ele.
check('palavra-chave desconhecida não é alinhamento', currentAlignment(field('::: aviso\nTex|to\n:::')), DEFAULT_ALIGNMENT);
check('cerca sem fechamento não é alinhamento', currentAlignment(field('::: centro\nTex|to')), DEFAULT_ALIGNMENT);

check('no primeiro caractere do documento', currentAlignment(field('|Texto')), DEFAULT_ALIGNMENT);
check('em documento vazio', currentAlignment(field('|')), DEFAULT_ALIGNMENT);

/* ── Alternar ──────────────────────────────────────────────────────────── */

check(
  'pedir o mesmo alinhamento desfaz',
  align('::: centro\nTexto |aqui\n:::', 'centro').value,
  'Texto aqui'
);

check(
  '"esquerda" desfaz, porque é o que o Markdown já faz sozinho',
  align('::: centro\nTexto |aqui\n:::', 'esquerda').value,
  'Texto aqui'
);

check(
  'trocar de alinhamento troca só a palavra-chave',
  align('::: centro\nTexto |aqui\n:::', 'direita').value,
  '::: direita\nTexto aqui\n:::'
);

check(
  'o cursor acompanha a troca em vez de saltar',
  (() => {
    const textarea = align('::: centro\nTexto |aqui\n:::', 'direita');
    return textarea.value.slice(0, textarea.selectionStart);
  })(),
  '::: direita\nTexto '
);

check(
  'trocar por uma palavra mais curta também mantém o cursor',
  align('::: justificado\nTexto |aqui\n:::', 'centro').value,
  '::: centro\nTexto aqui\n:::'
);

check(
  'desfazer devolve todos os parágrafos ao documento',
  align('Antes.\n\n::: centro\nUm.\n\nDois |aqui.\n:::\n\nDepois.', 'esquerda').value,
  'Antes.\n\nUm.\n\nDois aqui.\n\nDepois.'
);

check(
  '"esquerda" fora de um bloco não escreve nada',
  align('Texto |normal', 'esquerda').value,
  'Texto normal'
);

check(
  'uma palavra-chave inventada é ignorada em vez de escrita',
  align('Texto |normal', 'diagonal').value,
  'Texto normal'
);

console.log(failures ? `\n${failures} caso(s) falharam.` : '\nTodos os casos passaram.');
process.exit(failures ? 1 : 0);
