/**
 * A tela sobe, com os módulos de verdade.
 *
 * Dois defeitos desta mesma classe chegaram ao produto na mesma sessão, e os
 * dois deixavam a área de trabalho em branco ao criar um mapa: uma remoção
 * grande levou junto código vizinho - primeiro uma função de ícone, depois as
 * declarações do índice de filhos - e o primeiro desenho estourava. O laço de
 * render morre junto, e um mapa novo abre como uma tela vazia.
 *
 * A primeira versão desta suíte não pegou o segundo porque construía a tela
 * contra um *store* de mentira. A regra que ficou, e que este arquivo aplica:
 * **dublê só para o DOM**. Todo módulo da aplicação que não precisa de um
 * navegador entra aqui de verdade - o store, as ações, o renderizador, o
 * painel, os gestos - e é o servidor quem entrega o grafo, pelo pytest, para
 * que nem o formato do payload possa divergir.
 *
 * O DOM abaixo é burro de propósito: o que se quer saber é se o código roda de
 * ponta a ponta, não se o pixel saiu certo. Isso é assunto dos testes de CSS.
 *
 * Sem runner e sem dependências, como as outras suítes em tests/js.
 * Uso: `node tests/js/mindmap-boot.test.mjs <caminho-do-grafo.json>`
 */

import { readFileSync } from 'node:fs';

/* ── Um DOM que responde o suficiente ───────────────────────────────────── */

function element(tag = 'div') {
  const node = {
    tagName: tag,
    dataset: {},
    children: [],
    hidden: false,
    tabIndex: 0,
    draggable: false,
    textContent: '',
    value: '',
    offsetHeight: 48,
    style: {
      setProperty() {},
      removeProperty() {},
      getPropertyValue() { return ''; },
    },
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute() {},
    getAttribute() { return null; },
    removeAttribute() {},
    appendChild(child) { node.children.push(child); return child; },
    insertBefore(child) { node.children.unshift(child); return child; },
    append(...items) { node.children.push(...items); },
    replaceChildren(...items) { node.children = items; },
    remove() {},
    focus() {},
    contains() { return false; },
    addEventListener() {},
    querySelector() { return element(); },
    querySelectorAll() { return []; },
    closest() { return null; },
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 800, height: 600, bottom: 600, right: 800 };
    },
    get childElementCount() { return node.children.length; },
  };
  return node;
}

globalThis.document = {
  createElement: (tag) => element(tag),
  createElementNS: (_ns, tag) => element(tag),
  createTextNode: (text) => ({ text }),
  createDocumentFragment: () => element('fragment'),
  createRange: () => ({ selectNodeContents() {} }),
  querySelector: () => element(),
  querySelectorAll: () => [],
  addEventListener() {},
  activeElement: null,
};
let minted = 0;
globalThis.window = {
  addEventListener() {},
  requestAnimationFrame() {},
  // O store agenda o salvamento; aqui o relógio não avança, e é só isso que
  // se quer - que ele possa agendar sem estourar.
  setTimeout() { return 0; },
  clearTimeout() {},
  getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
  /* Um contador, e não o relógio.
     `Date.now()` devolvia o mesmo identificador para duas chamadas no mesmo
     milissegundo, e um nó criado logo depois do outro sobrescrevia o irmão -
     ou o próprio pai - dentro do modelo. Um dublê capaz de cunhar
     identificadores repetidos torna duvidoso todo teste construído sobre ele,
     e o servidor recusa o segundo `create` do mesmo uuid justamente porque
     duplicata é erro. Mesmo contador da suíte de colocação. */
  crypto: {
    randomUUID: () =>
      `00000000-0000-4000-8000-${String((minted += 1)).padStart(12, '0')}`,
  },
};
globalThis.CSS = { escape: (value) => value };
globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true }) });

const base = new URL('../../app/static/js/modules/mindmap/', import.meta.url);
const { createCamera, createRenderer } = await import(new URL('canvas.js', base));
const { createActions, createSelection } = await import(new URL('actions.js', base));
const { createInspector } = await import(new URL('inspector.js', base));
const { createInteractions } = await import(new URL('interactions.js', base));
const { createStore } = await import(new URL('store.js', base));
const { createMinimap } = await import(new URL('minimap.js', base));

let failures = 0;
function check(name, run) {
  try {
    run();
  } catch (error) {
    failures += 1;
    console.error(`FALHOU: ${name} — ${error && error.message}`);
    if (error && error.stack) console.error(error.stack.split('\n')[1]);
  }
}

/* ── O grafo que o servidor entrega ─────────────────────────────────────── */

const payload = process.argv[2]
  ? JSON.parse(readFileSync(process.argv[2], 'utf-8'))
  : {
      // Rodando à mão, sem o pytest: a forma mínima de um mapa novo.
      uuid: 'x', title: 'Mapa', description: '', color: '#4F46E5',
      layout: 'right', revision: 1, viewport: { x: 0, y: 0, zoom: 1 },
      nodes: [{
        uuid: 'raiz', parent: null, position: 0, kind: 'topic', text: 'Mapa',
        note: '', url: '', image_url: '', media_uuid: '', document: null,
        x: 0, y: 0, width: 200, height: 56, color: '#4F46E5', shape: 'pill',
        collapsed: false, layout: '',
      }],
      limits: { nodes: 1000, text: 500, note: 4000, url: 500 },
    };

function boot(graph) {
  const store = createStore({
    opsUrl: '/ops', graphUrl: '/graph', layoutUrl: '/layout',
    mediaUrl: '/media/00000000-0000-0000-0000-000000000000',
    documentUrl: '/doc/00000000-0000-0000-0000-000000000000',
    revision: graph.revision, layout: graph.layout,
  });
  store.adopt(graph);

  const page = element();
  const stage = element();
  const camera = createCamera(page, element(), stage);
  const renderer = createRenderer({
    store, page, nodesHost: element(), linksHost: element(), accent: graph.color,
  });
  const selection = createSelection(() => {});
  const actions = createActions({
    store, selection, limits: graph.limits, notify() {},
  });
  const inspector = createInspector({
    root: element(), store, selection, actions,
    notify() {}, uploader: { pick() {} }, searchUrl: '/search',
    onAddChild() {}, onConnectFrom() {}, onAttachTo() {}, onReveal() {},
  });
  const minimap = createMinimap({
    host: element(), canvas: element(), store, camera, stage,
  });
  createInteractions({
    store, selection, page, stage, camera, renderer, actions,
    marquee: element(), hint: element(), context: {},
    onEditStart() {}, onEditEnd() {}, onSelectBranch() {}, onRemoveLink() {},
    uploader: { pick() {} },
  });

  camera.write();
  renderer.render();
  renderer.renderLinks();
  renderer.measure();
  renderer.bounds();
  inspector.refresh();
  inspector.renderOutline();
  minimap.draw();
  return { store, actions, selection };
}

/* ── Um mapa recém-criado abre ──────────────────────────────────────────── */

check('a tela sobe com o grafo que o servidor entregou', () => {
  const { store } = boot(payload);
  if (!store.nodes.size) throw new Error('o grafo chegou vazio ao store');
});

const shapes = {
  'um mapa vazio': [],
  'um ramo': [
    { uuid: 'raiz', parent: null }, { uuid: 'filho', parent: 'raiz' },
  ],
  'um ramo fechado': [
    { uuid: 'raiz', parent: null, collapsed: true }, { uuid: 'filho', parent: 'raiz' },
  ],
  'um ramo com disposição própria': [
    { uuid: 'raiz', parent: null },
    { uuid: 'a', parent: 'raiz', layout: 'tree' },
    { uuid: 'a1', parent: 'a' },
  ],
  'vários tópicos soltos': [
    { uuid: 'a', parent: null }, { uuid: 'b', parent: null }, { uuid: 'c', parent: null },
  ],
};

for (const [name, nodes] of Object.entries(shapes)) {
  check(`${name}: a tela sobe`, () => {
    boot({
      ...payload,
      nodes: nodes.map((item, index) => ({
        position: index, kind: 'topic', text: item.uuid, note: '', url: '',
        image_url: '', media_uuid: '', document: null, x: 0, y: 0,
        width: 180, height: 48, color: '', shape: 'rounded',
        collapsed: false, layout: '', ...item,
      })),
    });
  });
}

/* ── E os gestos que o mapa oferece rodam sobre o store de verdade ──────── */

check('criar, conectar, desconectar e reaninhar rodam de ponta a ponta', () => {
  const { store, actions } = boot(payload);
  const root = store.roots()[0];

  const child = actions.addChild(root.uuid);
  const sibling = actions.addSibling(child.uuid);
  const loose = actions.addLoose({ x: 0, y: 0 });

  actions.connect(child.uuid, loose.uuid);
  actions.indent(sibling.uuid);
  actions.outdent(sibling.uuid);
  actions.shiftSibling(child.uuid, 1);
  actions.detach(child.uuid);
  actions.toggleCollapse(root.uuid);
  actions.duplicate(root.uuid);
  actions.removeSelection();

  if (!store.nodes.size) throw new Error('o mapa ficou vazio depois dos gestos');
});

/* ── E um mapa travado não se move ──────────────────────────────────────── */

/*
 * O cadeado tem um único ponto de verdade no navegador - `store.mutate` - e é
 * exatamente por isso que ele precisa deste teste: um teste por gesto seria um
 * teste esquecido no dia em que alguém acrescentar o gesto seguinte. Aqui os
 * mesmos gestos do bloco acima rodam contra um mapa travado, e o que se afirma
 * é sobre o modelo inteiro: nem um nó a mais, nem um a menos, nem um campo
 * diferente.
 */

function fingerprint(store) {
  return JSON.stringify(
    [...store.nodes.values()]
      .map((node) => ({ ...node }))
      .sort((a, b) => a.uuid.localeCompare(b.uuid))
  );
}

check('um mapa travado recusa todos os gestos, e não muda nada', () => {
  const { store, actions } = boot({ ...payload, locked: true });
  const root = store.roots()[0];
  const before = fingerprint(store);

  if (!store.locked) throw new Error('o store não adotou o cadeado do grafo');

  if (actions.addChild(root.uuid) !== null) {
    throw new Error('addChild devolveu um nó que o modelo não recebeu');
  }
  if (actions.addLoose({ x: 0, y: 0 }) !== null) throw new Error('addLoose criou');
  if (actions.addNote({ x: 0, y: 0 }) !== null) throw new Error('addNote criou');
  if (actions.duplicate(root.uuid) !== null) throw new Error('duplicate copiou');
  if (actions.remove([root.uuid]) !== 0) throw new Error('remove apagou');
  if (actions.clearBranchLayouts() !== 0) throw new Error('clearBranchLayouts mexeu');

  actions.update(root.uuid, { text: 'outro texto' });
  actions.moveBy([root.uuid], 50, 50);
  actions.moveTo(root.uuid, 999, 999);
  actions.toggleCollapse(root.uuid);
  actions.detach(root.uuid);

  if (fingerprint(store) !== before) throw new Error('o mapa travado mudou');
  if (store.hasPendingChanges()) throw new Error('sobrou algo para salvar');
});

check('um mapa travado não guarda a medida que o navegador tirou', () => {
  const { store } = boot({ ...payload, locked: true });
  const root = store.roots()[0];
  if (store.measured(root.uuid, 400, 400)) {
    throw new Error('a medida entrou num mapa travado');
  }
});

check('destravado, os mesmos gestos continuam funcionando', () => {
  const { store, actions } = boot(payload);
  const root = store.roots()[0];
  // Contado como diferença e não contra um número: o grafo aqui pode vir do
  // servidor, pelo pytest, e aí ele já chega com mais de um tópico.
  const before = store.nodes.size;
  if (!actions.addChild(root.uuid)) throw new Error('addChild parou de funcionar');
  if (store.nodes.size !== before + 1) throw new Error('o nó não entrou no modelo');
});

/* ── Um espelho não ganha ramo, por nenhum dos caminhos ─────────────────── */

/*
 * O defeito que isto prende chegou ao produto e ficou visível só no log de
 * acesso: cinco `POST /operacoes` iguais devolvendo 400 em quinze segundos.
 *
 * A causa era uma regra dita em quatro lugares e cumprida em um. O servidor
 * sempre recusou um subtópico dentro de um espelho - o ramo é do original -
 * e o cliente só checava isso em `shareInto`. Por Tab, por arrastar e pelo
 * painel Estrutura o modelo local aceitava, o lote ia, voltava 400, e como a
 * baseline só anda quando o servidor diz sim, toda gravação seguinte
 * reenviava a mesma operação impossível: o mapa parava de salvar em silêncio.
 */

const COM_ESPELHO = {
  ...payload,
  nodes: [
    { uuid: 'raiz', parent: null },
    { uuid: 'original', parent: 'raiz' },
    { uuid: 'espelho', parent: 'raiz', mirror_of: 'original' },
    { uuid: 'solto', parent: null },
  ].map((item, index) => ({
    position: index, kind: 'topic', text: item.uuid, note: '', url: '',
    image_url: '', media_uuid: '', document: null, x: 0, y: index * 80,
    width: 180, height: 48, color: '', shape: 'rounded',
    collapsed: false, layout: '', ...item,
  })),
};

check('nenhum caminho põe um subtópico dentro de um espelho', () => {
  const { store, actions } = boot(COM_ESPELHO);
  const antes = store.nodes.size;

  if (actions.addChild('espelho') !== null) throw new Error('Tab criou filho no espelho');
  if (actions.reparent('solto', 'espelho')) throw new Error('arrastar pendurou no espelho');
  if (actions.moveInto('solto', 'espelho', 0)) throw new Error('a Estrutura pendurou no espelho');
  if (actions.shareInto('original', 'espelho')) throw new Error('shareInto pendurou no espelho');

  if (store.nodes.size !== antes) throw new Error('o modelo mudou mesmo assim');
  if (store.get('solto').parent !== null) throw new Error('o tópico solto foi movido');
  if (store.hasPendingChanges()) throw new Error('sobrou uma operação impossível para enviar');
});

check('e o caminho legítimo continua aberto', () => {
  const { store, actions } = boot(COM_ESPELHO);
  if (!actions.addChild('original')) throw new Error('o original parou de aceitar subtópicos');
  if (!actions.reparent('solto', 'original')) throw new Error('arrastar parou de funcionar');
  if (!store.get('espelho')) throw new Error('o espelho sumiu');
});

/* ── E um lote recusado não volta para a fila ───────────────────────────── */

/*
 * A segunda metade do mesmo defeito, e a que vale independentemente da causa:
 * um lote que o servidor chamou de inválido nunca fica válido por ser
 * reenviado. Sem esta regra, *qualquer* operação impossível - a de amanhã,
 * que ainda não conhecemos - tranca o mapa do mesmo jeito.
 *
 * O transporte do store é um parâmetro justamente para isto poder ser
 * exercitado sem um servidor.
 */

function servidorQue(resposta) {
  const enviados = [];
  const store = createStore({
    opsUrl: '/ops', graphUrl: '/graph', layoutUrl: '/layout',
    mediaUrl: '/media/00000000-0000-0000-0000-000000000000',
    documentUrl: '/doc/00000000-0000-0000-0000-000000000000',
    revision: payload.revision, layout: payload.layout,
    post: (url, body) => {
      enviados.push(body.operations);
      return Promise.resolve(resposta);
    },
    load: () => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, graph: payload }),
    }),
  });
  store.adopt(payload);
  return { store, enviados };
}

const RECUSA = {
  ok: false, status: 400,
  data: { ok: false, error: 'Este é um tópico compartilhado.' },
};
const QUEDA = { ok: false, status: 0, data: null };
const ERRO_DO_SERVIDOR = { ok: false, status: 500, data: null };

await (async () => {
  const { store, enviados } = servidorQue(RECUSA);
  const motivos = [];
  store.on('rejected', ({ reason }) => motivos.push(reason));

  store.mutate(() => store.nodes.set('novo', { ...store.roots()[0], uuid: 'novo' }),
    { structural: true });
  await store.flush();

  check('uma recusa é dita à pessoa, com o motivo do servidor', () => {
    if (motivos.length !== 1) throw new Error(`avisos: ${motivos.length}`);
    if (!motivos[0].includes('compartilhado')) throw new Error(motivos[0]);
  });

  check('e o lote impossível não fica na fila', () => {
    if (store.hasPendingChanges()) {
      throw new Error('a operação recusada continuou pendente e voltaria a ser enviada');
    }
  });

  await store.flush();
  check('uma segunda gravação não reenvia nada', () => {
    if (enviados.length !== 1) throw new Error(`o cliente enviou ${enviados.length} lotes`);
  });
})();

for (const [nome, resposta] of [['a rede caiu', QUEDA], ['o servidor errou', ERRO_DO_SERVIDOR]]) {
  await (async () => {
    const { store } = servidorQue(resposta);
    let recusas = 0;
    store.on('rejected', () => { recusas += 1; });

    store.mutate(() => store.nodes.set('novo', { ...store.roots()[0], uuid: 'novo' }),
      { structural: true });
    await store.flush();

    check(`${nome}: continua sendo transitório, e a alteração espera`, () => {
      if (recusas) throw new Error('uma falha passageira foi tratada como recusa');
      if (!store.hasPendingChanges()) throw new Error('a alteração foi descartada');
      if (store.status !== 'error') throw new Error(`status ${store.status}`);
    });
  })();
}

/* ── E o teto de níveis é o mesmo dos dois lados ────────────────────────── */

/*
 * O mesmo defeito do espelho, no outro teto: o servidor recusa um subtópico
 * que passe de `MAX_DEPTH` níveis, e a tela não sabia disso. Vinte Tabs
 * seguidos produziam um lote que nunca seria aceito.
 *
 * A conta está escrita duas vezes - em Python e aqui - porque nenhuma das
 * duas pode esperar pela outra: o servidor decide, e a tela precisa recusar o
 * gesto onde ele acontece. Duas escritas de uma regra é risco de divergência,
 * então o ponto exato da recusa é fixado aqui e em
 * `test_depth_is_bounded`, que constrói a mesma corrente do lado de lá.
 */

const TETO = 20;

function corrente(comprimento) {
  const nodes = [];
  for (let i = 0; i < comprimento; i += 1) {
    nodes.push({
      uuid: `n${i}`, parent: i === 0 ? null : `n${i - 1}`, position: 0,
      kind: 'topic', text: `n${i}`, note: '', url: '', image_url: '',
      media_uuid: '', document: null, x: 0, y: i * 80, width: 180, height: 48,
      color: '', shape: 'rounded', collapsed: false, layout: '',
    });
  }
  return boot({ ...payload, nodes, limits: { ...payload.limits, depth: TETO } });
}

check('o último nível permitido ainda aceita um subtópico', () => {
  // A mesma corrente que `test_depth_is_bounded` monta: a raiz e mais
  // dezenove. Pendurar em n18 ainda cabe.
  const { actions } = corrente(TETO);
  if (!actions.addChild(`n${TETO - 2}`)) {
    throw new Error('a tela recusou um subtópico que o servidor aceitaria');
  }
});

check('e o seguinte é recusado aqui, e não pelo servidor', () => {
  const { store, actions } = corrente(TETO);
  const antes = store.nodes.size;

  if (actions.addChild(`n${TETO - 1}`) !== null) {
    throw new Error('a tela deixou passar um lote que o servidor recusaria');
  }
  if (store.nodes.size !== antes) throw new Error('o modelo cresceu mesmo assim');
  if (store.hasPendingChanges()) throw new Error('sobrou operação impossível na fila');
});

/* Não é só o tópico que desce: o ramo inteiro desce com ele. Sem contar a
   altura, mover um galho de três níveis para perto do teto passaria aqui e
   morreria no servidor.

   A conta é a do servidor, letra por letra - `profundidade do novo pai + 1 +
   altura do que se move >= teto` - então os dois lados da fronteira são
   fixados: em n15 dá 19 e cabe; em n16 dá 20 e não cabe. Afirmar só a recusa
   deixaria passar um guarda exagerado, que recusa o que o servidor aceita e é
   igualmente um defeito - só mais silencioso. */
function ramoDeTresNiveis(actions) {
  const ramo = actions.addLoose({ x: 900, y: 0 });
  let ponta = ramo.uuid;
  for (let i = 0; i < 3; i += 1) ponta = actions.addChild(ponta).uuid;
  return ramo.uuid;
}

check('um ramo alto ainda cabe no último nível que o comporta', () => {
  const { store, actions } = corrente(TETO - 3);
  const ramo = ramoDeTresNiveis(actions);

  if (!actions.reparent(ramo, `n${TETO - 5}`)) {
    throw new Error('a tela recusou um movimento que o servidor aceitaria');
  }
  if (store.get(ramo).parent !== `n${TETO - 5}`) throw new Error('o ramo não foi movido');
});

check('e um nível abaixo disso é recusado aqui, não pelo servidor', () => {
  const { store, actions } = corrente(TETO - 3);
  const ramo = ramoDeTresNiveis(actions);

  if (actions.reparent(ramo, `n${TETO - 4}`)) {
    throw new Error('a tela desceu um ramo alto para além do teto');
  }
  if (store.get(ramo).parent !== null) throw new Error('o ramo foi movido mesmo assim');
});

check('sem teto conhecido, quem decide continua sendo o servidor', () => {
  /* Um mapa aberto por um servidor que ainda não manda `limits.depth` não
     pode ficar sem criar subtópico nenhum. */
  const nodes = [{
    uuid: 'raiz', parent: null, position: 0, kind: 'topic', text: 'raiz',
    note: '', url: '', image_url: '', media_uuid: '', document: null,
    x: 0, y: 0, width: 180, height: 48, color: '', shape: 'rounded',
    collapsed: false, layout: '',
  }];
  const { actions } = boot({ ...payload, nodes, limits: { nodes: 1000 } });
  if (!actions.addChild('raiz')) throw new Error('sem teto, a tela travou sozinha');
});

if (failures) {
  console.error(`\n${failures} verificação(ões) falharam.`);
  process.exit(1);
}
console.log('a tela sobe: ok');
