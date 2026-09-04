/**
 * A tela de configurações: a conversa entre a paleta e a cor de destaque.
 *
 * As duas são coisas separadas de propósito. A paleta é o papel e a tinta —
 * o fundo, os painéis, o texto, as bordas. O destaque é a única cor por cima
 * disso, e ela é do usuário. Uma paleta apenas *nasce* com uma cor.
 *
 * O que este módulo resolve é o momento em que as duas se cruzam: alguém
 * troca de paleta e a cor de destaque continua sendo a da paleta anterior.
 *
 * A regra é uma só, e ela é conservadora: a cor acompanha a paleta enquanto
 * ainda for a cor de *alguma* paleta — ou seja, enquanto o usuário nunca
 * tiver escolhido a dele. No instante em que ele escolhe um tom próprio, a
 * troca de paleta passa a deixá-lo em paz, e o botão "Usar a cor da paleta"
 * é o caminho explícito de volta. Nada é decidido em silêncio: o campo muda
 * na tela, antes de salvar, e dá para desfazer escolhendo outra cor.
 */

const PICKER = '[data-palette-picker]';
const ACCENT = '#accent_color';

/** As cores com que as paletas nascem, lidas do próprio HTML. */
function paletteAccents(picker) {
  return Array.from(picker.querySelectorAll('[data-accent]')).map((radio) =>
    (radio.getAttribute('data-accent') || '').toLowerCase(),
  );
}

/** O rádio marcado agora. */
function selected(picker) {
  return picker.querySelector('[data-accent]:checked');
}

/**
 * `<input type="color">` normaliza tudo para #rrggbb minúsculo, e o valor
 * gravado pode ter vindo em maiúsculas ou na forma de três dígitos — então a
 * comparação é feita sobre o valor normalizado, nunca sobre o texto cru.
 */
function sameColour(a, b) {
  return (a || '').toLowerCase() === (b || '').toLowerCase();
}

export function initSettings(root = document) {
  const picker = root.querySelector(PICKER);
  const accent = root.querySelector(ACCENT);
  if (!picker || !accent) return;

  const known = paletteAccents(picker);

  const adopt = () => {
    const radio = selected(picker);
    if (!radio) return;
    accent.value = radio.getAttribute('data-accent') || accent.value;
  };

  picker.addEventListener('change', (event) => {
    if (!event.target.matches('[data-accent]')) return;
    // Só enquanto a cor ainda for a de alguma paleta. Um tom escolhido à mão
    // não é sobrescrito por uma troca de paleta.
    if (known.some((colour) => sameColour(colour, accent.value))) adopt();
  });

  // O caminho explícito de volta, para quem já tinha escolhido a sua.
  const button = root.querySelector('[data-action="adopt-palette-accent"]');
  if (button) button.addEventListener('click', adopt);
}

// Ponto de entrada, como em goals.js: a folha é carregada só por esta tela,
// e initSettings devolve na hora se os elementos não estiverem lá.
initSettings();
