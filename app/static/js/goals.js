/**
 * Metas — o realce progressivo das telas da jornada.
 *
 * Nada aqui é necessário para usar o app. A esteira já move cartões por
 * formulário, o formulário de meta já mostra todos os campos e o servidor já
 * ignora o que não se aplica. O que este arquivo acrescenta é a diferença
 * entre funcionar e ser bom de usar: arrastar em vez de clicar, ver só os
 * campos que importam, e a frase trocando sozinha enquanto a página fica
 * aberta.
 */

import { $, $$, csrfToken } from './modules/dom.js';
import { phraseIndex } from './modules/phrase-rotation.js';
import { toast } from './modules/toasts.js';

/* ── Conversa com o servidor ───────────────────────────────────────────── */

/**
 * PATCH com CSRF. Devolve `{ ok, data }` e nunca lança: quem chama decide o
 * que fazer com uma falha, e aqui a decisão é sempre "desfaz o que a tela já
 * tinha desenhado".
 */
async function patchJSON(url, payload) {
  try {
    const response = await fetch(url, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
        Accept: 'application/json',
      },
      body: JSON.stringify(payload),
    });
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    return { ok: response.ok, data };
  } catch (error) {
    return { ok: false, data: null };
  }
}

/* ── A esteira ─────────────────────────────────────────────────────────── */

function refreshColumn(list) {
  const column = list.closest('.board-column');
  const count = list.querySelectorAll('.board-card').length;

  const counter = column && column.querySelector('[data-count]');
  if (counter) counter.textContent = String(count);

  const placeholder = list.querySelector('[data-empty]');
  if (count > 0) {
    if (placeholder) placeholder.remove();
  } else if (!placeholder) {
    const empty = document.createElement('p');
    empty.className = 'board-empty';
    empty.setAttribute('data-empty', '');
    empty.textContent = 'Nada nesta coluna.';
    list.appendChild(empty);
  }
}

function initBoard(board) {
  let dragged = null;

  board.addEventListener('dragstart', (event) => {
    const card = event.target.closest('.board-card');
    if (!card) return;
    dragged = card;
    card.classList.add('is-dragging');
    // `move` é o que troca o cursor do ponteiro para a mão fechada.
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
  });

  board.addEventListener('dragend', () => {
    if (dragged) dragged.classList.remove('is-dragging');
    dragged = null;
    $$('.board-drop', board).forEach((list) => list.classList.remove('is-over'));
  });

  $$('.board-drop', board).forEach((list) => {
    list.addEventListener('dragover', (event) => {
      if (!dragged) return;
      event.preventDefault();
      list.classList.add('is-over');
    });
    list.addEventListener('dragleave', () => list.classList.remove('is-over'));

    list.addEventListener('drop', async (event) => {
      event.preventDefault();
      list.classList.remove('is-over');
      if (!dragged) return;

      const card = dragged;
      const origin = card.closest('.board-drop');
      if (!origin || origin === list) return;

      const status = list.dataset.status;
      const previousStatus = card.dataset.status;

      // Movido primeiro, confirmado depois: a tela responde ao gesto no mesmo
      // quadro, e o servidor tem a palavra final logo abaixo.
      list.appendChild(card);
      card.dataset.status = status;
      card.classList.add('is-settling');
      window.setTimeout(() => card.classList.remove('is-settling'), 340);
      refreshColumn(origin);
      refreshColumn(list);

      const payload = { status };
      if (card.dataset.day) payload.dia = card.dataset.day;

      const { ok, data } = await patchJSON(`/api/metas/${card.dataset.uuid}`, payload);
      if (!ok) {
        origin.appendChild(card);
        card.dataset.status = previousStatus;
        refreshColumn(origin);
        refreshColumn(list);
        toast(
          (data && data.error) || 'Não foi possível mover esta meta.',
          'error'
        );
        return;
      }

      (data.achievements || []).forEach((item) => {
        toast(`Conquista desbloqueada: ${item.title}`, 'success');
      });
    });
  });
}

/* ── O formulário ──────────────────────────────────────────────────────── */

/**
 * Mostra apenas o que se aplica.
 *
 * Sem prazo, não há data, horário nem repetição para escolher. Sem repetição,
 * não há "por quantos dias" nem "até quando". E uma série não tem situação
 * própria: quem conclui é o dia, então o campo some — o servidor já toma a
 * mesma decisão, e deixar a caixa na tela prometeria um efeito que ela não tem.
 */
function initGoalForm(form) {
  const deadline = form.querySelector('#has_deadline');
  const recurrence = form.querySelector('#recurrence_type');
  if (!deadline || !recurrence) return;

  const show = (element, visible) => {
    if (element) element.hidden = !visible;
  };

  const apply = () => {
    const hasDeadline = deadline.checked;
    const kind = recurrence.value;

    show(form.querySelector('[data-when="deadline"]'), hasDeadline);
    show(form.querySelector('[data-when="count"]'), hasDeadline && kind === 'count');
    show(
      form.querySelector('[data-when="until"]'),
      hasDeadline && (kind === 'weekdays' || kind === 'weekends' || kind === 'count')
    );
    show(
      form.querySelector('[data-when="single"]'),
      !hasDeadline || kind === 'none'
    );
  };

  apply();
  deadline.addEventListener('change', apply);
  recurrence.addEventListener('change', apply);
}

/* ── A frase do dia ────────────────────────────────────────────────────── */

/**
 * A rotação combina com a do servidor: as duas dividem o mesmo relógio pelo
 * mesmo intervalo, então a frase que aparece aqui é a que o servidor
 * escolheria agora. Recarregar não sorteia outra — ela troca quando o
 * intervalo vira, que é o que a pessoa configurou.
 */
function initPhrases(element) {
  let phrases = [];
  try {
    phrases = JSON.parse(element.dataset.phrases || '[]');
  } catch (error) {
    return;
  }
  const minutes = Number(element.dataset.interval) || 30;
  if (phrases.length < 2 || minutes < 1) return;

  const target = element.querySelector('[data-phrase-text]') || element;

  const paint = () => {
    const next = phrases[phraseIndex(Date.now(), minutes, phrases.length)];
    if (target.textContent === next) return;
    target.textContent = next;
  };

  paint();
  // Um pulso por minuto, e não um alarme no instante exato da virada: uma aba
  // deixada aberta por horas não pode depender de um único setTimeout longo,
  // que o navegador atrasa ou descarta ao suspender a aba.
  window.setInterval(paint, 60 * 1000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) paint();
  });
}

/* ── Início ────────────────────────────────────────────────────────────── */

$$('[data-board]').forEach(initBoard);

const goalForm = $('[data-goal-form]');
if (goalForm) initGoalForm(goalForm);

$$('[data-phrases]').forEach(initPhrases);
