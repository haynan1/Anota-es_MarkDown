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
globalThis.window = {
  addEventListener() {},
  requestAnimationFrame() {},
  // O store agenda o salvamento; aqui o relógio não avança, e é só isso que
  // se quer - que ele possa agendar sem estourar.
  setTimeout() { return 0; },
  clearTimeout() {},
  getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
  crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(Date.now()).slice(-12)}` },
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

if (failures) {
  console.error(`\n${failures} verificação(ões) falharam.`);
  process.exit(1);
}
console.log('a tela sobe: ok');
