/**
 * A rotação das frases, e o contrato entre os dois idiomas que a calculam.
 *
 * O servidor escolhe a frase que vai no HTML; a página escolhe a frase que
 * entra no lugar dela quando o intervalo vira. Duas implementações de uma
 * verdade só divergem - a não ser que algo leia as duas.
 *
 * Este arquivo faz dois trabalhos. Rodado sozinho (`node
 * tests/js/phrase-rotation.test.mjs`) ele checa as propriedades que a conta
 * precisa ter: o índice está sempre dentro da lista, ele é estável dentro de
 * uma janela, ele avança quando a janela vira e uma lista vazia devolve -1. E
 * imprime a tabela inteira de casos como JSON, que é o que o pytest lê:
 * `test_phrases.py` recalcula cada um deles em Python e compara.
 *
 * Sem runner e sem dependências, como as outras suítes em tests/js.
 */

import { phraseIndex } from '../../app/static/js/modules/phrase-rotation.js';

const MINUTE = 60 * 1000;

/* Um instante conhecido (2026-03-01T12:00:00Z) e os arredores das viradas de
   janela, que é onde um `floor` errado se esconde. */
const BASE = Date.UTC(2026, 2, 1, 12, 0, 0);

const CASES = [
  { epochMs: 0, interval: 30, count: 6 },
  { epochMs: BASE, interval: 1, count: 6 },
  { epochMs: BASE, interval: 5, count: 6 },
  { epochMs: BASE, interval: 15, count: 8 },
  { epochMs: BASE, interval: 30, count: 8 },
  { epochMs: BASE, interval: 60, count: 8 },
  { epochMs: BASE - 1, interval: 30, count: 8 },
  { epochMs: BASE + 1, interval: 30, count: 8 },
  { epochMs: BASE + 30 * MINUTE - 1, interval: 30, count: 8 },
  { epochMs: BASE + 30 * MINUTE, interval: 30, count: 8 },
  { epochMs: BASE, interval: 30, count: 1 },
  { epochMs: BASE, interval: 30, count: 0 },
  { epochMs: BASE, interval: 0, count: 6 },
  // Uma lista longa e um instante bem à frente: o índice tem de continuar
  // caindo dentro dela.
  { epochMs: Date.UTC(2099, 11, 31, 23, 59, 59), interval: 60, count: 207 },
];

const rows = CASES.map((item) => ({
  ...item,
  index: phraseIndex(item.epochMs, item.interval, item.count),
}));

function check(condition, message) {
  if (!condition) {
    console.error(`FALHOU: ${message}`);
    process.exitCode = 1;
  }
}

for (const row of rows) {
  if (row.count < 1) {
    check(row.index === -1, `lista vazia deveria devolver -1, deu ${row.index}`);
    continue;
  }
  check(
    row.index >= 0 && row.index < row.count,
    `índice ${row.index} fora de uma lista de ${row.count}`
  );
}

// Estável dentro da janela, e diferente na janela seguinte.
check(
  phraseIndex(BASE, 30, 8) === phraseIndex(BASE + 30 * MINUTE - 1, 30, 8),
  'a frase mudou no meio da janela'
);
check(
  phraseIndex(BASE, 30, 8) !== phraseIndex(BASE + 30 * MINUTE, 30, 8),
  'a frase não mudou quando a janela virou'
);

console.log(JSON.stringify(rows, null, 2));
