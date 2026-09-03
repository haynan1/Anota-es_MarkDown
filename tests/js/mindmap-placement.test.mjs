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
    /* Como no store de verdade: raízes são os filhos de ninguém, e vêm na
       mesma ordem que qualquer lista de irmãos. Sem isto o dublê devolvia a
       ordem de inserção e escondia justamente o que os testes de reordenar
       precisam ver. */
    roots: () => store.children(null),
    branch: (uuid) => [uuid],
    children: (uuid) =>
      [...store.nodes.values()]
        .filter((node) => (node.parent || null) === (uuid || null))
        .sort((a, b) => a.position - b.position),
    get: (uuid) => store.nodes.get(uuid),
    /* Cópia fiel da regra do store: o tópico que este nó mostra é ele mesmo,
       ou o original que ele espelha. Um salto só - o serviço recusa espelho
       de espelho. */
    original: (node) => {
      if (!node || !node.mirror_of) return node;
      return store.nodes.get(node.mirror_of) || node;
    },
    /* Cópia fiel da regra do store: subindo pelos pais de `uuid`, algum
       deles é `candidate`? O próprio `uuid` não conta - um dublê que
       responde diferente do original é um dublê que mente mais tarde. */
    ancestorOf: (candidate, uuid) => {
      let cursor = store.nodes.get(uuid);
      let guard = 0;
      while (cursor && cursor.parent && guard < 64) {
        if (cursor.parent === candidate) return true;
        cursor = store.nodes.get(cursor.parent);
        guard += 1;
      }
      return false;
    },
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

/* ── Desconectar um tópico do de cima ───────────────────────────────────── */

/* O defeito: soltar um bloco em cima do outro pendura um no outro, e a linha
   que aparece desenhava um cursor de clique e não respondia a clique nenhum.
   O gesto tinha ida e não tinha volta. */

{
  const nodes = [
    { uuid: 'pai', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'filho', parent: 'pai', position: 0, x: 300, y: 120, width: 180, height: 48 },
    { uuid: 'neto', parent: 'filho', position: 0, x: 600, y: 120, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });

  check('desligar responde que desligou', actions.detach('filho') === true);
  check('o tópico deixa de estar pendurado', store.get('filho').parent === null);
  check('e não sai do lugar',
    store.get('filho').x === 300 && store.get('filho').y === 120);
  check('o ramo abaixo vem junto, ainda pendurado nele',
    store.get('neto').parent === 'filho');
  check('e o tópico de cima continua onde estava',
    store.get('pai').x === 0 && store.get('pai').parent === null);
}

{
  const nodes = [
    { uuid: 'solto', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });

  check('um tópico que já é solto não tem o que desligar',
    actions.detach('solto') === false);
  check('e nada acontece com ele', store.get('solto').parent === null);
  check('desligar algo que não existe não quebra',
    actions.detach('nao-existe') === false);
}

/* ── Desconectar tem volta ──────────────────────────────────────────────── */

/* O defeito: desligado, o tópico virava raiz - pintado como o centro do mapa -
   e o único jeito de pendurá-lo de novo era arrastar o ramo inteiro para cima
   de outro tópico. Quem procurava um botão achava "Conectar a…", que faz uma
   associação: uma linha tracejada que parece ter resolvido e não devolve
   hierarquia nenhuma. */

{
  const nodes = [
    { uuid: 'setup', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'analytics', parent: 'setup', position: 0, x: 300, y: 0, width: 180, height: 48 },
    { uuid: 'coleta', parent: 'analytics', position: 0, x: 600, y: 0, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });

  actions.detach('analytics');
  check('desligado, o tópico fica sem pai', store.get('analytics').parent === null);

  check('e conectar de volta responde que conectou',
    actions.reparent('analytics', 'setup') === true);
  check('o pai voltou a ser o de antes', store.get('analytics').parent === 'setup');
  check('o ramo abaixo nunca se soltou', store.get('coleta').parent === 'analytics');
}

{
  // Pendurar um tópico dentro do próprio ramo faria do mapa um anel.
  const nodes = [
    { uuid: 'pai', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'filho', parent: 'pai', position: 0, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });

  check('um tópico não pode ser pendurado no próprio ramo',
    actions.reparent('pai', 'filho') === false);
  check('e nada se move', store.get('pai').parent === null);
}

/* ── Reaninhar e reordenar ──────────────────────────────────────────────── */

/* O caso de verdade, tirado da tela de quem reportou: "Analytics" e "Tags"
   foram desconectados de "SETUP INICIAL" e ficaram no nível de cima, irmãos
   da raiz do mapa. Duas descidas de nível por ramo devolvem o mapa - com o
   ponteiro, arrastando um tópico sobre outro na tela; pelo teclado, Alt e as
   setas sobre o tópico selecionado. */

function board() {
  return [
    { uuid: 'ads', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'setup', parent: 'ads', position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'analytics', parent: null, position: 1, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'coleta', parent: 'analytics', position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'tags', parent: null, position: 2, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'ga4', parent: 'tags', position: 0, x: 0, y: 0, width: 180, height: 48 },
  ];
}

{
  const { actions, store } = harness({ nodes: board(), layout: 'right' });

  check('pôr dentro do de cima leva ao irmão anterior',
    actions.indent('analytics') === true && store.get('analytics').parent === 'ads');
  check('de novo, e chega em SETUP INICIAL',
    actions.indent('analytics') === true && store.get('analytics').parent === 'setup');
  check('o ramo abaixo veio junto', store.get('coleta').parent === 'analytics');

  actions.indent('tags');
  actions.indent('tags');
  check('o segundo ramo faz o mesmo caminho', store.get('tags').parent === 'setup');
  check('e os dois viraram irmãos sob SETUP INICIAL',
    store.children('setup').map((n) => n.uuid).join(',') === 'analytics,tags');
  check('sem sobrar nada no nível de cima além da raiz',
    store.roots().map((n) => n.uuid).join(',') === 'ads');
}

{
  const { actions, store } = harness({ nodes: board(), layout: 'right' });

  check('o primeiro da lista não tem o que ficar embaixo',
    actions.indent('ads') === false);
  check('e um tópico no nível de cima não tem de onde sair',
    actions.outdent('ads') === false);

  check('tirar de dentro sobe um nível',
    actions.outdent('setup') === true && store.get('setup').parent === null);
  check('e aterrissa logo depois de quem era o seu pai',
    store.roots().map((n) => n.uuid).join(',') === 'ads,setup,analytics,tags');
}

{
  const { actions, store } = harness({ nodes: board(), layout: 'right' });

  check('descer entre os irmãos', actions.shiftSibling('analytics', 1) === true);
  check('a ordem mudou',
    store.roots().map((n) => n.uuid).join(',') === 'ads,tags,analytics');
  check('o último não desce mais', actions.shiftSibling('analytics', 1) === false);
  check('o primeiro não sobe mais', actions.shiftSibling('ads', -1) === false);
}

{
  // As posições ficam densas: empates só são resolvidos pelo uuid, e um mapa
  // cuja ordem depende disso é um mapa que se reordena sozinho.
  const { actions, store } = harness({ nodes: board(), layout: 'right' });
  actions.shiftSibling('tags', -1);
  const roots = store.roots();
  check('as posições continuam 0, 1, 2…',
    roots.every((node, index) => node.position === index),
    roots.map((n) => `${n.uuid}:${n.position}`).join(' '));
}

{
  // Um ramo não pode ser posto dentro de si mesmo.
  const { actions, store } = harness({ nodes: board(), layout: 'right' });
  check('pôr um tópico dentro do próprio ramo é recusado',
    actions.moveInto('ads', 'setup', 0) === false);
  check('e nada se move', store.get('ads').parent === null);
}

{
  // Mover para dentro de um ramo fechado o abre - senão o tópico some no
  // instante em que chega, e a tecla parece não ter funcionado.
  const nodes = board();
  nodes.find((n) => n.uuid === 'ads').collapsed = true;
  const { actions, store } = harness({ nodes, layout: 'right' });
  actions.indent('analytics');
  check('o ramo de destino abre para receber',
    store.get('ads').collapsed === false);
}

/* ── Onde um tópico aterrissa ───────────────────────────────────────────── */

/* Onde um tópico pode aterrissar numa lista de irmãos: antes, depois, ou
   dentro de outro. `moveInto` é onde os três chegam. */

{
  const nodes = [
    { uuid: 'a', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'b', parent: null, position: 1, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'c', parent: null, position: 2, x: 0, y: 0, width: 180, height: 48 },
  ];
  const order = (store) => store.roots().map((n) => n.uuid).join(',');

  const before = harness({ nodes: nodes.map((n) => ({ ...n })), layout: 'right' });
  before.actions.moveInto('c', null, 0);
  check('entrar antes', order(before.store) === 'c,a,b');

  const after = harness({ nodes: nodes.map((n) => ({ ...n })), layout: 'right' });
  after.actions.moveInto('a', null, 2);
  check('entrar depois', order(after.store) === 'b,c,a');

  const inside = harness({ nodes: nodes.map((n) => ({ ...n })), layout: 'right' });
  inside.actions.moveInto('c', 'a', 0);
  check('entrar dentro', inside.store.get('c').parent === 'a');
  check('e sai do nível de cima', order(inside.store) === 'a,b');
}

{
  // Arrastar para o mesmo lugar não pode reordenar nada por conta própria.
  const nodes = [
    { uuid: 'a', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'b', parent: null, position: 1, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });
  actions.moveInto('a', null, 0);
  check('aterrissar onde já estava deixa tudo como estava',
    store.roots().map((n) => n.uuid).join(',') === 'a,b');
}

/* ── Conectar é pendurar ────────────────────────────────────────────────── */

/* O quadro desenhava dois tipos de linha e nada dizia qual era qual, então
   "conectar" podia deixar a estrutura exatamente como estava. Sobrou uma
   linha, e conectar dois tópicos põe um dentro do outro. */

{
  const nodes = [
    { uuid: 'setup', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'analytics', parent: null, position: 1, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'coleta', parent: 'analytics', position: 0, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'right' });

  check('conectar devolve quem se moveu',
    actions.connect('setup', 'analytics') === 'analytics');
  check('e o alvo passa a ficar dentro da origem',
    store.get('analytics').parent === 'setup');
  check('o ramo abaixo veio junto', store.get('coleta').parent === 'analytics');

  check('conectar um tópico a si mesmo não faz nada',
    actions.connect('setup', 'setup') === null);
  check('nem a um tópico que não existe',
    actions.connect('setup', 'nao-existe') === null);
  check('nem para dentro do próprio ramo',
    actions.connect('coleta', 'analytics') === null);
  check('e a estrutura fica de pé', store.get('analytics').parent === 'setup');
}

/* ── Um bloco comum em várias etapas ────────────────────────────────────── */

/* O caso de verdade: "Modelo de alcance", com os seis modelos de campanha
   dentro, vale para as seis etapas de "Objetivo de campanha" - e não para uma
   só. Uma árvore não sabe dizer isso; o tópico compartilhado sabe. */

{
  const etapas = ['Vendas', 'Leads', 'Trafego', 'App', 'Alcance', 'Visitas'];
  const nodes = [
    { uuid: 'obj', parent: null, position: 0, x: 0, y: 0, width: 180, height: 48 },
    ...etapas.map((nome, i) => ({
      uuid: nome, parent: 'obj', position: i, x: 0, y: 0, width: 180, height: 48,
    })),
    { uuid: 'modelo', parent: 'Vendas', position: 0, x: 0, y: 0, width: 180, height: 48 },
    { uuid: 'shopping', parent: 'modelo', position: 0, x: 0, y: 0, width: 180, height: 48 },
  ];
  const { actions, store } = harness({ nodes, layout: 'tree' });

  const criados = etapas
    .filter((nome) => nome !== 'Vendas')
    .map((nome) => actions.shareInto('modelo', nome))
    .filter(Boolean);

  check('o bloco entra nas outras cinco etapas', criados.length === 5,
    `entrou em ${criados.length}`);
  check('e cada um é o mesmo tópico, não uma cópia',
    criados.every((n) => n.mirror_of === 'modelo'));
  check('o ramo continua sendo só do original',
    store.children('shopping').length === 0 &&
    criados.every((n) => store.children(n.uuid).length === 0));

  check('repetir na mesma etapa não duplica',
    actions.shareInto('modelo', 'Leads') === null);
  check('o próprio bloco não entra em si mesmo',
    actions.shareInto('modelo', 'modelo') === null);

  // Compartilhar o espelho compartilha o original: é o mesmo tópico.
  const doEspelho = actions.shareInto(criados[0].uuid, 'Vendas');
  check('compartilhar um espelho aponta para o original',
    doEspelho === null || doEspelho.mirror_of === 'modelo');
}

if (failures) {
  console.error(`\n${failures} verificação(ões) falharam.`);
  process.exit(1);
}
console.log('colocação de tópicos soltos: ok');
