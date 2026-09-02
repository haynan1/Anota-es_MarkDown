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

function harness({ nodes = [], layout = 'right' } = {}) {
  const store = {
    nodes: new Map(nodes.map((node) => [node.uuid, node])),
    edges: new Map(),
    layout,
    /* The same rule the real store applies: a node that names nothing takes
       what is above it, and a root that names nothing takes the map's. */
    arrangementOf: (node) => {
      let current = node;
      while (current) {
        if (current.layout) return current.layout;
        current = current.parent ? store.nodes.get(current.parent) : null;
      }
      return layout;
    },
    roots: () => [...store.nodes.values()].filter((node) => !node.parent),
    branch: (uuid) => [uuid],
    children: (uuid) =>
      [...store.nodes.values()]
        .filter((node) => (node.parent || null) === (uuid || null))
        .sort((a, b) => a.position - b.position),
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

/* ── Um subtópico nasce na direção em que o mapa cresce ─────────────────── */

/* The bug this pins: pressing Tab on a map arranged as a tree put the new
   subtopic to the *right* of its parent, where nothing else on that board
   lives. The topic was correct - the right parent, the right position - and
   the board said something false about the map being built until someone
   pressed "arrumar". Growing a branch and tidying it are not supposed to
   disagree about which way is down. */

const root = () => ({
  uuid: 'raiz', parent: null, position: 0, x: 100, y: 100, width: 180, height: 48,
});

{
  const { actions, store } = harness({ nodes: [root()], layout: 'right' });
  const first = actions.addChild('raiz');
  check('num mapa horizontal o filho nasce à direita',
    first.x > 100 + 180, `x = ${first.x}`);
  check('e alinhado com o pai',
    first.y + first.height / 2 === 100 + 48 / 2, `y = ${first.y}`);

  store.nodes.set(first.uuid, first);
  const second = actions.addChild('raiz');
  check('o irmão desce, não se empilha',
    second.y >= first.y + first.height && second.x === first.x,
    `${second.x},${second.y} contra ${first.x},${first.y}`);
}

for (const layout of ['down', 'tree']) {
  const { actions, store } = harness({ nodes: [root()], layout });
  const first = actions.addChild('raiz');
  check(`num mapa ${layout} o filho nasce abaixo`,
    first.y > 100 + 48, `y = ${first.y}`);
  check(`e centrado sob o pai (${layout})`,
    first.x + first.width / 2 === 100 + 180 / 2, `x = ${first.x}`);

  store.nodes.set(first.uuid, first);
  const second = actions.addChild('raiz');
  check(`o irmão vai para o lado (${layout})`,
    second.x >= first.x + first.width && second.y === first.y,
    `${second.x},${second.y} contra ${first.x},${first.y}`);
  check(`e nenhum dos dois cobre o outro (${layout})`,
    second.x >= first.x + first.width || first.x >= second.x + second.width);
}

/* ── As setas apontam para o que selecionam ─────────────────────────────── */

/* Right selecting a child that is drawn underneath the node is an arrow
   pointing away from the thing it moves to. The keys turn with the map. */

{
  const nodes = [
    root(),
    { uuid: 'a', parent: 'raiz', position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'b', parent: 'raiz', position: 1, x: 0, y: 0, width: 180, height: 48 },
  ];

  const across = harness({ nodes: nodes.map((n) => ({ ...n })), layout: 'right' }).actions;
  check('horizontal: direita entra no ramo', across.neighbour('raiz', 'right') === 'a');
  check('horizontal: esquerda volta ao pai', across.neighbour('a', 'left') === 'raiz');
  check('horizontal: baixo é o próximo irmão', across.neighbour('a', 'down') === 'b');
  check('horizontal: cima é o irmão anterior', across.neighbour('b', 'up') === 'a');

  const down = harness({ nodes: nodes.map((n) => ({ ...n })), layout: 'tree' }).actions;
  check('árvore: baixo entra no ramo', down.neighbour('raiz', 'down') === 'a');
  check('árvore: cima volta ao pai', down.neighbour('a', 'up') === 'raiz');
  check('árvore: direita é o próximo irmão', down.neighbour('a', 'right') === 'b');
  check('árvore: esquerda é o irmão anterior', down.neighbour('b', 'left') === 'a');
}

{
  // Um ramo recolhido não entrega seus filhos por tecla nenhuma.
  const { actions } = harness({
    nodes: [
      { ...root(), collapsed: true },
      { uuid: 'a', parent: 'raiz', position: 0, x: 0, y: 0, width: 180, height: 48 },
    ],
    layout: 'tree',
  });
  check('árvore: um ramo fechado não é atravessado',
    actions.neighbour('raiz', 'down') === null);
}

/* ── Um mapa com todos os tipos ao mesmo tempo ──────────────────────────── */

/* The bug this pins: with the arrangement read off the *map*, a tree branch
   hanging inside a horizontal map grew its subtopics to the right and its
   arrow keys reached them with Right - both describing a map the person was
   not looking at. What decides is the branch, not the board. */

{
  const nodes = [
    { uuid: 'raiz', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'arv', parent: 'raiz', position: 0, x: 300, y: 0, width: 180, height: 48,
      layout: 'tree' },
    { uuid: 'com', parent: 'raiz', position: 1, x: 300, y: 200, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });

  const underTree = actions.addChild('arv');
  check('dentro do ramo árvore, o subtópico nasce abaixo',
    underTree.y > 48, `y = ${underTree.y}`);
  check('e centrado sob o pai',
    underTree.x + underTree.width / 2 === 300 + 180 / 2, `x = ${underTree.x}`);

  store.nodes.set(underTree.uuid, underTree);
  const besideTree = actions.addChild('arv');
  check('e o irmão dentro da árvore vai para o lado',
    besideTree.x >= underTree.x + underTree.width && besideTree.y === underTree.y);

  const besidePlain = actions.addChild('com');
  check('no mesmo mapa, o ramo comum continua crescendo para a direita',
    besidePlain.x > 300 + 180, `x = ${besidePlain.x}`);
}

{
  /* As duas metades da pergunta: onde ficam meus filhos é a minha disposição,
     onde eu fico entre meus irmãos é a do meu pai. Num ramo árvore pendurado
     num mapa horizontal, as duas respostas são diferentes - e ambas certas. */
  const nodes = [
    { uuid: 'raiz', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'arv', parent: 'raiz', position: 0, x: 0, y: 0, width: 180, height: 48,
      layout: 'tree' },
    { uuid: 'com', parent: 'raiz', position: 1, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'f1', parent: 'arv', position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'f2', parent: 'arv', position: 1, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions } = harness({ nodes, layout: 'right' });

  check('baixo entra no ramo árvore', actions.neighbour('arv', 'down') === 'f1');
  check('esquerda volta ao pai, que é horizontal',
    actions.neighbour('arv', 'left') === 'raiz');
  check('e cima continua andando entre os irmãos do mapa',
    actions.neighbour('com', 'up') === 'arv');

  // Dentro da árvore as duas respostas voltam a coincidir.
  check('dentro da árvore, cima volta ao pai', actions.neighbour('f1', 'up') === 'arv');
  check('e direita anda entre os irmãos', actions.neighbour('f1', 'right') === 'f2');
}

{
  /* Uma tecla, dois sentidos: com o ramo descendo e os irmãos empilhados
     para baixo, Baixo quer dizer as duas coisas. O ramo ganha enquanto há
     ramo, e a tecla volta para o irmão quando não há - nenhuma seta pode
     virar uma tecla que não faz nada. */
  const nodes = [
    { uuid: 'raiz', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'a', parent: 'raiz', position: 0, x: 0, y: 0, width: 180, height: 48,
      layout: 'tree' },
    { uuid: 'b', parent: 'raiz', position: 1, x: 0, y: 0, width: 180, height: 48,
      layout: 'tree' },
    { uuid: 'a1', parent: 'a', position: 0, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions } = harness({ nodes, layout: 'right' });

  check('com ramo, baixo entra nele', actions.neighbour('a', 'down') === 'a1');
  check('sem ramo, a mesma tecla vai para o irmão',
    actions.neighbour('b', 'down') === null, 'b é o último irmão');

  const folded = harness({
    nodes: nodes.map((node) => (node.uuid === 'a' ? { ...node, collapsed: true } : { ...node })),
    layout: 'right',
  }).actions;
  check('com o ramo fechado, baixo devolve a tecla ao irmão',
    folded.neighbour('a', 'down') === 'b');
}

{
  /* Um nó novo tem exatamente a mesma forma que um nó vindo do servidor. Uma
     chave ausente de um lado e vazia do outro é um modelo com duas formas, e
     o diff acaba tropeçando nela. */
  const { actions } = harness({ layout: 'right' });
  const fresh = actions.addLoose({ x: 0, y: 0 });
  check('um tópico novo declara a disposição do ramo, mesmo vazia',
    Object.prototype.hasOwnProperty.call(fresh, 'layout') && fresh.layout === '',
    JSON.stringify(fresh.layout));
}

if (failures) {
  console.error(`\n${failures} verificação(ões) falharam.`);
  process.exit(1);
}
console.log('colocação de tópicos soltos: ok');
