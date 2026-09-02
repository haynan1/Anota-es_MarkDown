/**
 * A geometria das ligações, e o contrato entre os dois idiomas que a desenham.
 *
 * The canvas draws a link on every frame of a drag, so the browser has its own
 * copy of the arithmetic the SVG export uses on the server. Two
 * implementations of one truth drift - unless something reads both.
 *
 * So this file does two jobs. Run on its own (`node tests/js/mindmap-routing
 * .test.mjs`) it checks the properties a connection must have whatever the
 * layout: it leaves the face the layout says it leaves, it arrives at the
 * opposite one, it never emits `-0.0`. And it prints the whole case table as
 * JSON, which is what pytest reads: `test_mindmaps.py` recomputes every one of
 * those paths in Python and compares the strings character for character. A
 * change to either implementation that the other did not receive fails there.
 *
 * No test runner and no dependencies, like the other suites under tests/js.
 */

import {
  branchPath,
  freePath,
  isVertical,
  routingFor,
} from '../../app/static/js/modules/mindmap/routing.js';

const box = (x, y, width = 180, height = 48) => ({ x, y, width, height });

/* Every shape a connection can be asked for: the ordinary case, the child
   dragged back past its parent on each axis, boxes of different sizes, the
   only child sitting exactly under its parent, and coordinates chosen to land
   on a rounding tie (x.x5) in both languages. */
const CASES = [
  ['horizontal', box(0, 0), box(276, 0)],
  ['horizontal', box(0, 0), box(276, 300)],
  ['horizontal', box(400, 0), box(0, 120)],
  ['horizontal', box(0, 0), box(190, 12, 60, 32)],
  ['horizontal', box(0, 0), box(180, 0)],

  ['vertical', box(0, 0), box(0, 124)],
  ['vertical', box(0, 0), box(240, 124)],
  ['vertical', box(0, 300), box(120, 0)],
  ['vertical', box(0, 0), box(35, 49, 60, 32)],

  ['elbow', box(0, 0), box(0, 124)],
  ['elbow', box(0, 0), box(240, 124)],
  ['elbow', box(0, 0), box(-240, 124)],
  ['elbow', box(0, 300), box(240, 0)],
  ['elbow', box(0, 0), box(3, 52)],
  ['elbow', box(0, 0), box(240, 49)],
  ['elbow', box(0, 0), box(245, 125, 61, 33)],

  ['spoke', box(0, 0), box(240, 124)],
  ['spoke', box(0, 0), box(-240, -124)],
  ['spoke', box(0, 0), box(0, 200)],
  ['spoke', box(0, 0), box(200, 0)],
  ['spoke', box(0, 0), box(0, 0)],
  ['spoke', box(0, 0), box(45, 25, 61, 33)],
];

const FREE_CASES = [
  ['curve', box(0, 0), box(240, 124)],
  ['curve', box(240, 124), box(0, 0)],
  ['line', box(0, 0), box(240, 124)],
  ['dashed', box(0, 0), box(-241, 125, 61, 33)],
];

const table = {
  branches: CASES.map(([routing, parent, child]) => ({
    routing,
    parent,
    child,
    d: branchPath(routing, parent, child),
  })),
  free: FREE_CASES.map(([style, source, target]) => ({
    style,
    source,
    target,
    d: freePath(style, source, target),
  })),
  routings: Object.fromEntries(
    ['right', 'down', 'tree', 'radial', 'desconhecido'].map((name) => [
      name,
      routingFor(name),
    ])
  ),
};

/* ── Propriedades que valem em qualquer idioma ──────────────────────────── */

let failures = 0;
function check(name, condition, detail = '') {
  if (condition) return;
  failures += 1;
  console.error(`FALHOU: ${name}${detail ? ` — ${detail}` : ''}`);
}

/** The `M` of a path, and its last coordinate pair. */
function ends(d) {
  const numbers = d.match(/-?\d+\.\d+/g).map(Number);
  return {
    start: { x: numbers[0], y: numbers[1] },
    end: { x: numbers[numbers.length - 2], y: numbers[numbers.length - 1] },
  };
}

check('nenhum caminho carrega menos-zero',
  ![...table.branches, ...table.free].some((entry) => entry.d.includes('-0.0')));

check('uma disposição desconhecida ainda desenha',
  table.routings.desconhecido === 'horizontal');
check('árvore e vertical descem a página',
  isVertical('tree') && isVertical('down') && !isVertical('right')
    && !isVertical('radial'));

{
  const parent = box(0, 0);
  const child = box(240, 124);

  const horizontal = ends(branchPath('horizontal', parent, child));
  check('horizontal sai pela face direita',
    horizontal.start.x === 180 && horizontal.start.y === 24,
    JSON.stringify(horizontal.start));
  check('e chega pela esquerda do filho',
    horizontal.end.x === 240 && horizontal.end.y === 148);

  for (const routing of ['vertical', 'elbow']) {
    const link = ends(branchPath(routing, parent, child));
    check(`${routing} sai pela face de baixo`,
      link.start.x === 90 && link.start.y === 48, JSON.stringify(link.start));
    check(`${routing} chega pelo topo do filho`,
      link.end.x === 330 && link.end.y === 124, JSON.stringify(link.end));
  }
}

{
  // Um filho arrastado para cima do pai não faz a linha atravessar a caixa.
  const above = ends(branchPath('elbow', box(0, 300), box(0, 0)));
  check('o cotovelo se inverte quando o filho está acima',
    above.start.y === 300 && above.end.y === 48,
    `${above.start.y} → ${above.end.y}`);
}

{
  // Filho único exatamente sob o pai: uma reta, não uma curva envergonhada.
  const straight = branchPath('elbow', box(0, 0), box(0, 124));
  check('um filho único cai em linha reta',
    !straight.includes('Q') && straight.startsWith('M90.0,48.0'), straight);
}

{
  /* O barramento: dois filhos do mesmo pai, na mesma linha, dobram na mesma
     altura - é isso que faz um organograma parecer um organograma em vez de
     um leque de curvas quase paralelas. */
  const parent = box(0, 0);
  const left = branchPath('elbow', parent, box(-300, 124));
  const right = branchPath('elbow', parent, box(300, 124));
  const middle = (d) => d.match(/-?\d+\.\d+/g).map(Number)[3];
  check('irmãos de um mesmo nível dobram na mesma altura',
    middle(left) === middle(right), `${middle(left)} contra ${middle(right)}`);
}

{
  // O raio começa e termina nas bordas, não nos centros.
  const spoke = ends(branchPath('spoke', box(0, 0), box(0, 400)));
  check('o raio sai da borda de baixo', spoke.start.x === 90 && spoke.start.y === 48);
  check('e chega na borda de cima', spoke.end.x === 90 && spoke.end.y === 400);
}

if (failures) {
  console.error(`\n${failures} verificação(ões) falharam.`);
  process.exit(1);
}

// A tabela, para o pytest conferir contra o Python.
console.log(JSON.stringify(table));
