/**
 * Onde um tópico solto nasce.
 *
 * The bug this pins: a new loose topic was placed at the centre of the view,
 * and the centre does not move between two clicks. Clicking "novo tópico"
 * five times left five topics at one coordinate, perfectly stacked - the
 * board looked like it had one, and dragging it appeared to do nothing
 * because the four underneath stayed exactly where they were.
 *
 * No test runner and no dependencies, like tests/js/align.test.mjs: the module
 * takes four collaborators and none of them has to be real. Run it directly
 * with `node tests/js/mindmap-placement.test.mjs`, or through pytest.
 */

let counter = 0;
globalThis.window = {
  crypto: {
    randomUUID() {
      counter += 1;
      return `00000000-0000-4000-8000-${String(counter).padStart(12, '0')}`;
    },
  },
};

const { createActions } = await import('../../app/static/js/modules/mindmap/actions.js');

function harness({ nodes = [] } = {}) {
  const store = {
    nodes: new Map(nodes.map((node) => [node.uuid, node])),
    edges: new Map(),
    roots: () => [...store.nodes.values()].filter((node) => !node.parent),
    branch: (uuid) => [uuid],
    get: (uuid) => store.nodes.get(uuid),
    mutate: (apply) => apply(),
  };
  const actions = createActions({
    store,
    selection: { only() {}, list: () => [], has: () => false, prune() {} },
    limits: { nodes: 1000, edges: 2000 },
    notify() {},
  });
  return { store, actions };
}

let failures = 0;
function check(name, condition, detail = '') {
  if (condition) return;
  failures += 1;
  console.error(`FALHOU: ${name}${detail ? ` — ${detail}` : ''}`);
}

/* ── Um tópico solto nunca nasce em cima de outro ───────────────────────── */

{
  const { actions, store } = harness();
  for (let i = 0; i < 6; i += 1) actions.addLoose({ x: 148, y: 62 });

  const spots = [...store.nodes.values()].map((node) => `${node.x}|${node.y}`);
  check('seis tópicos no mesmo pedido ocupam seis lugares',
    new Set(spots).size === 6, `lugares distintos: ${new Set(spots).size}`);
  check('o primeiro fica onde foi pedido',
    store.nodes.values().next().value.x === 148);
}

{
  const { actions, store } = harness();
  actions.addLoose({ x: 0, y: 0 });
  actions.addLoose({ x: 900, y: 900 });
  const [a, b] = [...store.nodes.values()];
  check('um lugar livre é respeitado como está',
    b.x === 900 && b.y === 900, `ficou em ${b.x},${b.y}`);
  check('e não desloca quem já estava lá', a.x === 0 && a.y === 0);
}

{
  // O deslocamento é diagonal, então dois tópicos nunca compartilham uma linha
  // nem uma coluna - encostar num eixo só já basta para parecerem empilhados.
  const { actions, store } = harness();
  for (let i = 0; i < 4; i += 1) actions.addLoose({ x: 10, y: 10 });
  const nodes = [...store.nodes.values()];
  check('cada um sai da linha e da coluna do anterior',
    new Set(nodes.map((n) => n.x)).size === 4 && new Set(nodes.map((n) => n.y)).size === 4);
}

{
  // Um mapa cheio não pode transformar a colocação numa varredura.
  const many = Array.from({ length: 300 }, (_, i) => ({
    uuid: `no-${i}`, parent: null, x: 0, y: 0, width: 180, height: 48,
  }));
  const { actions } = harness({ nodes: many });
  const started = Date.now();
  actions.addLoose({ x: 0, y: 0 });
  check('a busca por um lugar livre é limitada',
    Date.now() - started < 250, `levou ${Date.now() - started}ms`);
}

if (failures) {
  console.error(`\n${failures} verificação(ões) falharam.`);
  process.exit(1);
}
console.log('colocação de tópicos soltos: ok');
