/**
 * Qual frase está no ar agora.
 *
 * Esta conta existe duas vezes: aqui, para a página trocar a frase sem
 * recarregar, e em ``PhraseService`` no servidor, para o HTML já chegar com a
 * frase certa. Duas implementações de uma verdade só divergem - a não ser que
 * algo leia as duas, que é o que `tests/js/phrase-rotation.test.mjs` faz: ele
 * imprime a tabela de casos que o pytest recalcula em Python e compara.
 *
 * A escolha é uma função do relógio, e não um sorteio. É isso que faz o
 * servidor e a página concordarem, e é isso que faz recarregar a tela não
 * trocar a frase: ela troca quando o intervalo vira.
 */

/** O índice da frase no instante `epochMs`, ou -1 quando não há frases. */
export function phraseIndex(epochMs, intervalMinutes, count) {
  if (!count || count < 1) return -1;
  const window = Math.max(1, Math.trunc(intervalMinutes)) * 60 * 1000;
  return Math.floor(epochMs / window) % count;
}
