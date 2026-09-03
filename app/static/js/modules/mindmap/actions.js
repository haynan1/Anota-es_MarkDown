/**
 * Selection, and every change a person can ask the board to make.
 *
 * Both the pointer and the keyboard end up here, and so does the inspector
 * panel: "add a subtopic" is one function, whether it was reached by pressing
 * Tab, by clicking a port, or by pressing a button in a side panel. Three
 * implementations of one verb is how a canvas ends up with a gesture that
 * quietly does something slightly different from its menu item.
 *
 * Nada aqui pergunta se o mapa está travado. `store.mutate` responde isso, e
 * responde para todo mundo de uma vez. O que estas funções fazem é *acreditar*
 * na resposta: uma criação recusada devolve `null` em vez do nó que não
 * existe, para que quem chamou não saia editando um tópico que o modelo nunca
 * recebeu.
 */

import { isVertical } from './routing.js';

/** Layout used when a node is born under the pointer rather than by tidying.
 *  Two sets, because a map that grows downwards has to grow downwards from
 *  the first Tab and not only after someone presses "arrumar": a subtopic
 *  that appears to the right of its parent on a tree has already told the
 *  writer the wrong thing about the map they are building. */
const CHILD_GAP_X = 90;
const SIBLING_GAP_Y = 24;
const CHILD_GAP_Y = 72;
const SIBLING_GAP_X = 32;
const NEW_WIDTH = 180;
const NEW_HEIGHT = 48;
const NOTE_WIDTH = 200;

export function newId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  // A fallback that still produces the exact shape the server validates.
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createSelection(onChange) {
  let members = new Set();
  let primary = null;

  function emit() {
    onChange(new Set(members), primary);
  }

  return {
    get size() { return members.size; },
    get primary() { return primary; },
    list() { return [...members]; },
    has(uuid) { return members.has(uuid); },
    only(uuid) {
      members = new Set(uuid ? [uuid] : []);
      primary = uuid || null;
      emit();
    },
    add(uuid) {
      members.add(uuid);
      primary = uuid;
      emit();
    },
    toggle(uuid) {
      if (members.has(uuid)) {
        members.delete(uuid);
        if (primary === uuid) primary = members.values().next().value || null;
      } else {
        members.add(uuid);
        primary = uuid;
      }
      emit();
    },
    replace(list) {
      members = new Set(list);
      primary = list.length ? list[list.length - 1] : null;
      emit();
    },
    clear() {
      members = new Set();
      primary = null;
      emit();
    },
    prune(exists) {
      const before = members.size;
      members = new Set([...members].filter(exists));
      if (primary && !exists(primary)) primary = members.values().next().value || null;
      if (members.size !== before) emit();
    },
  };
}

export function createActions({ store, selection, limits, notify }) {
  /**
   * Um espelho nunca ganha ramo próprio: o ramo é do original.
   *
   * O servidor sempre recusou isto, e o cliente checava em *um* dos quatro
   * caminhos que levam até lá. Pelos outros três - Tab com um espelho
   * selecionado, arrastar um tópico para cima dele, movê-lo para dentro pelo
   * painel Estrutura - o modelo local aceitava alegremente, e aí acontecia o
   * pior desfecho possível: o lote ia para o servidor, voltava 400, e como a
   * baseline só anda quando o servidor diz sim, *toda* gravação seguinte
   * reenviava a mesma operação impossível. O mapa parava de salvar em
   * silêncio e ficava assim até alguém recarregar a página.
   *
   * Uma regra dita em quatro lugares e cumprida em um é a forma mais cara de
   * não ter regra nenhuma. Agora ela mora aqui, e os quatro perguntam.
   */
  function refusesChildren(parentUuid) {
    const parent = parentUuid ? store.get(parentUuid) : null;
    if (!parent || !parent.mirror_of) return false;
    notify(
      'Este é um tópico compartilhado. Adicione o subtópico no original — ' +
        'ele aparece aqui junto.',
      'error'
    );
    return true;
  }

  /**
   * Um ramo não passa do teto de níveis do mapa.
   *
   * A mesma conta do servidor, e pelo mesmo motivo do espelho acima: um lote
   * que ele vai recusar não pode sair daqui. `descendo` é quanta árvore vem
   * junto - zero para um tópico novo, a altura do ramo para um que está sendo
   * movido - porque mover um galho de cinco níveis para o fundo do mapa
   * estoura o teto tanto quanto criar cinco tópicos lá.
   */
  function tooDeep(parentUuid, descendo = 0) {
    if (!parentUuid) return false;
    const ceiling = limits.depth;
    // Um mapa antigo, aberto por um servidor que ainda não mandava este
    // número, não pode ficar sem criar subtópico nenhum: sem teto conhecido,
    // quem decide continua sendo o servidor.
    if (!ceiling) return false;

    let depth = 0;
    let cursor = store.get(parentUuid);
    while (cursor && cursor.parent && depth <= ceiling * 2) {
      depth += 1;
      cursor = store.get(cursor.parent);
    }
    if (depth + 1 + descendo < ceiling) return false;

    notify(
      `Este mapa vai até ${ceiling} níveis de profundidade. ` +
        'Continue o assunto num tópico ao lado, ou num mapa próprio.',
      'error'
    );
    return true;
  }

  /** Quantos níveis pendem de um nó - a altura do ramo que ele carrega. */
  function heightOf(uuid) {
    let height = 0;
    let frontier = store.children(uuid);
    while (frontier.length && height <= (limits.depth || 64) * 2) {
      height += 1;
      frontier = frontier.flatMap((node) => store.children(node.uuid));
    }
    return height;
  }

  /* ── Creating ───────────────────────────────────────────────────────── */

  function blankNode(overrides) {
    return {
      uuid: newId(),
      parent: null,
      position: 0,
      kind: 'topic',
      text: '',
      note: '',
      url: '',
      image: '',
      image_url: '',
      media_uuid: '',
      document: null,
      document_uuid: '',
      x: 0,
      y: 0,
      width: NEW_WIDTH,
      height: NEW_HEIGHT,
      color: '',
      shape: 'rounded',
      collapsed: false,
      mirror_of: '',
      // Explicitly empty, not absent: `normalizeNode` gives every node the
      // server sends an empty string here, and a locally created node with
      // the key missing altogether would be a second shape for one model -
      // the kind of asymmetry a diff eventually trips over.
      layout: '',
      ...overrides,
    };
  }

  function atCapacity() {
    if (store.nodes.size < limits.nodes) return false;
    notify(
      `Este mapa chegou ao limite de ${limits.nodes} tópicos. ` +
        'Divida o assunto em outro mapa.',
      'error'
    );
    return true;
  }

  /** Where a new child of `parent` should land: one step along the axis the
   *  map grows on, and past the last sibling on the other one - so a branch
   *  spreads instead of stacking on itself. */
  function childPlacement(parent) {
    const siblings = store.children(parent.uuid);

    // The parent's own arrangement, not the map's: on a board that mixes
    // them, Tab inside the organogram has to grow the organogram.
    if (isVertical(store.arrangementOf(parent))) {
      const y = parent.y + parent.height + CHILD_GAP_Y;
      if (!siblings.length) {
        return { x: parent.x + parent.width / 2 - NEW_WIDTH / 2, y };
      }
      const rightmost = siblings.reduce(
        (edge, node) => Math.max(edge, node.x + node.width),
        -Infinity
      );
      return { x: rightmost + SIBLING_GAP_X, y };
    }

    const x = parent.x + parent.width + CHILD_GAP_X;
    if (!siblings.length) {
      return { x, y: parent.y + parent.height / 2 - NEW_HEIGHT / 2 };
    }
    const lowest = siblings.reduce(
      (bottom, node) => Math.max(bottom, node.y + node.height),
      -Infinity
    );
    return { x, y: lowest + SIBLING_GAP_Y };
  }

  function addChild(parentUuid, overrides = {}) {
    const parent = store.get(parentUuid);
    if (!parent || refusesChildren(parentUuid) || tooDeep(parentUuid)) return null;
    if (atCapacity()) return null;

    const placement = childPlacement(parent);
    const node = blankNode({
      parent: parent.uuid,
      position: store.children(parent.uuid).length,
      ...placement,
      ...overrides,
    });

    const applied = store.mutate(() => {
      store.nodes.set(node.uuid, node);
      // A branch that gains a child while folded would hide it the instant it
      // was created, which reads as the key not having worked.
      if (parent.collapsed) parent.collapsed = false;
    }, { structural: true });
    if (!applied) return null;

    selection.only(node.uuid);
    return node;
  }

  function addSibling(uuid, overrides = {}) {
    const node = store.get(uuid);
    if (!node) return null;
    if (node.parent) return addChild(node.parent, overrides);
    // A second root goes beside the first on a map that grows downwards, and
    // below it on one that grows across - the free direction, either way.
    // The map's arrangement, because roots are the map's to arrange: what a
    // root says about its own branch has no bearing on where its siblings go.
    const beside = isVertical(store.layout)
      ? { x: node.x + node.width + SIBLING_GAP_X, y: node.y }
      : { x: node.x, y: node.y + node.height + SIBLING_GAP_Y };
    return addLoose({ ...beside, ...overrides });
  }

  /** A node with no parent: an idea that has not found its branch yet. */
  /* A new loose topic is placed at the centre of the view, which is the right
     answer once and the wrong answer every time after: the centre does not
     move between two clicks, so the second topic lands exactly on the first
     and the board looks like it has one. Nudge down-right until the spot is
     free, the way a window manager cascades. */
  const CASCADE = 28;

  function freeSpot(x, y) {
    const taken = (px, py) =>
      [...store.nodes.values()].some(
        (other) => Math.abs(other.x - px) < CASCADE && Math.abs(other.y - py) < CASCADE
      );
    let spot = { x, y };
    // Bounded: with a map at its ceiling this must not become a search.
    for (let step = 0; step < 40 && taken(spot.x, spot.y); step += 1) {
      spot = { x: x + (step + 1) * CASCADE, y: y + (step + 1) * CASCADE };
    }
    return spot;
  }

  function addLoose(overrides = {}) {
    if (atCapacity()) return null;
    const placement = freeSpot(
      typeof overrides.x === 'number' ? overrides.x : 0,
      typeof overrides.y === 'number' ? overrides.y : 0
    );
    const node = blankNode({
      parent: null,
      position: store.roots().length,
      ...overrides,
      ...placement,
    });
    if (!store.mutate(() => store.nodes.set(node.uuid, node), { structural: true })) {
      return null;
    }
    selection.only(node.uuid);
    return node;
  }

  function addNote(overrides = {}) {
    return addLoose({
      kind: 'note',
      shape: 'rect',
      width: NOTE_WIDTH,
      text: '',
      ...overrides,
    });
  }

  function duplicate(uuid) {
    const node = store.get(uuid);
    if (!node || atCapacity()) return null;
    const copy = blankNode({
      ...node,
      uuid: newId(),
      x: node.x + 28,
      y: node.y + 28,
      position: store.children(node.parent).length,
    });
    if (!store.mutate(() => store.nodes.set(copy.uuid, copy), { structural: true })) {
      return null;
    }
    selection.only(copy.uuid);
    return copy;
  }

  /* ── Changing ───────────────────────────────────────────────────────── */

  function update(uuid, fields) {
    const node = store.get(uuid);
    if (!node) return;
    store.mutate(() => Object.assign(node, fields));
  }

  /** Apply the same change to every selected node - colour, shape, a nudge. */
  function updateSelection(fields) {
    const targets = selection.list().filter((uuid) => store.get(uuid));
    if (!targets.length) return;
    store.mutate(() => {
      targets.forEach((uuid) => Object.assign(store.get(uuid), fields));
    });
  }

  function moveBy(uuids, dx, dy, { record = true } = {}) {
    if (!uuids.length) return;
    store.mutate(
      () => {
        uuids.forEach((uuid) => {
          const node = store.get(uuid);
          if (!node) return;
          node.x += dx;
          node.y += dy;
        });
      },
      { record }
    );
  }

  function moveTo(uuid, x, y, { record = true } = {}) {
    const node = store.get(uuid);
    if (!node) return;
    store.mutate(() => {
      node.x = x;
      node.y = y;
    }, { record });
  }

  /**
   * Hang a node off a new parent.
   *
   * Refused when the target is inside the branch being moved: that would cut
   * the branch loose from the map entirely. The server checks the same thing -
   * this check exists so the answer is immediate and visible, not so the rule
   * is enforced here.
   */
  function reparent(uuid, parentUuid) {
    const node = store.get(uuid);
    if (!node || uuid === parentUuid) return false;
    if (parentUuid && store.ancestorOf(uuid, parentUuid)) {
      notify('Um tópico não pode virar filho do próprio ramo.', 'error');
      return false;
    }
    if (refusesChildren(parentUuid)) return false;
    if (tooDeep(parentUuid, heightOf(uuid))) return false;
    if (node.parent === parentUuid) return false;

    return store.mutate(() => {
      node.parent = parentUuid || null;
      node.position = store.children(parentUuid).length;
      const parent = parentUuid ? store.get(parentUuid) : null;
      if (parent && parent.collapsed) parent.collapsed = false;
    }, { structural: true });
  }

  /**
   * Cut the line to the topic above: the node stops being a child and becomes
   * a loose topic, standing exactly where it stood.
   *
   * Nothing is deleted and nothing moves - the branch underneath comes along,
   * still hanging off the node that was cut free.
   */
  function detach(uuid) {
    const node = store.get(uuid);
    if (!node || !node.parent) return false;
    return reparent(uuid, null);
  }

  /**
   * Põe o mesmo tópico também embaixo de `parentUuid`.
   *
   * Uma árvore não sabe dizer "isto vale para todas as etapas": um tópico tem
   * um pai. O espelho é como isso é dito sem que o mapa deixe de ser uma
   * árvore - uma linha como qualquer outra, e na ponta dela um tópico que
   * mora noutro lugar. Renomear ali renomeia aqui, porque é o mesmo tópico.
   */
  function shareInto(uuid, parentUuid) {
    const original = store.original(store.get(uuid));
    const parent = store.get(parentUuid);
    if (!original || !parent || original.uuid === parent.uuid) return null;
    if (refusesChildren(parent.uuid) || tooDeep(parent.uuid)) return null;
    // O mesmo tópico duas vezes no mesmo lugar seriam duas linhas dizendo a
    // mesma coisa.
    const already = store.children(parent.uuid).some(
      (child) => store.original(child).uuid === original.uuid
    );
    if (already) {
      notify('Este tópico já aparece aqui.', 'info');
      return null;
    }
    if (atCapacity()) return null;

    const node = blankNode({
      parent: parent.uuid,
      position: store.children(parent.uuid).length,
      mirror_of: original.uuid,
      ...childPlacement(parent),
    });
    const applied = store.mutate(() => {
      store.nodes.set(node.uuid, node);
      if (parent.collapsed) parent.collapsed = false;
    }, { structural: true });
    if (!applied) return null;
    selection.only(node.uuid);
    return node;
  }

  /* ── Reordenar e reaninhar ──────────────────────────────────────────── */

  /**
   * Move a topic under `parentUuid` at a given place among its siblings, and
   * renumber that whole list.
   *
   * `reparent` appends, which is right for a drop on the board - the pointer
   * said where, and "where" was a place on the canvas, not a place in a list.
   * The outline is the opposite: it *is* the list, so a move there has to land
   * on an index and leave the numbering dense behind it. Renumbering the whole
   * sibling list is a handful of small writes and it is what stops positions
   * from drifting into ties that only the uuid tiebreak resolves.
   */
  function moveInto(uuid, parentUuid, index) {
    const node = store.get(uuid);
    if (!node) return false;
    if (parentUuid && (uuid === parentUuid || store.ancestorOf(uuid, parentUuid))) {
      notify('Um tópico não pode virar filho do próprio ramo.', 'error');
      return false;
    }
    if (refusesChildren(parentUuid)) return false;
    if (tooDeep(parentUuid, heightOf(uuid))) return false;

    const target = parentUuid || null;
    const siblings = store.children(target).filter((item) => item.uuid !== uuid);
    const at = Math.max(0, Math.min(index, siblings.length));
    const ordered = [...siblings.slice(0, at), node, ...siblings.slice(at)];

    return store.mutate(() => {
      node.parent = target;
      ordered.forEach((item, position) => {
        item.position = position;
      });
      const parent = target ? store.get(target) : null;
      // A branch that gains a child while folded would hide it the instant it
      // arrived, which reads as the move not having happened.
      if (parent && parent.collapsed) parent.collapsed = false;
    }, { structural: true });
  }

  /** Hang the topic under the sibling above it - the outliner's Tab. */
  function indent(uuid) {
    const node = store.get(uuid);
    if (!node) return false;
    const siblings = store.children(node.parent);
    const index = siblings.findIndex((item) => item.uuid === uuid);
    // The first of its list has nothing above it to go under.
    if (index <= 0) return false;

    const above = siblings[index - 1];
    return moveInto(uuid, above.uuid, store.children(above.uuid).length);
  }

  /** Lift the topic out to its parent's level, right after it. */
  function outdent(uuid) {
    const node = store.get(uuid);
    if (!node || !node.parent) return false;
    const parent = store.get(node.parent);
    if (!parent) return false;

    const uncles = store.children(parent.parent);
    const at = uncles.findIndex((item) => item.uuid === parent.uuid);
    return moveInto(uuid, parent.parent || null, at + 1);
  }

  /** Swap places with the sibling above (-1) or below (+1). */
  function shiftSibling(uuid, delta) {
    const node = store.get(uuid);
    if (!node) return false;
    const siblings = store.children(node.parent);
    const index = siblings.findIndex((item) => item.uuid === uuid);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= siblings.length) return false;

    return moveInto(uuid, node.parent, target);
  }

  function toggleCollapse(uuid) {
    const node = store.get(uuid);
    if (!node || !store.children(uuid).length) return;
    store.mutate(() => {
      node.collapsed = !node.collapsed;
    }, { structural: true });
  }

  /* ── Removing ───────────────────────────────────────────────────────── */

  /** Delete the selection, branches and all. */
  function remove(uuids) {
    const doomed = new Set();
    uuids.forEach((uuid) => store.branch(uuid).forEach((key) => doomed.add(key)));
    if (!doomed.size) return 0;

    const applied = store.mutate(() => {
      doomed.forEach((uuid) => store.nodes.delete(uuid));
    }, { structural: true });
    if (!applied) return 0;

    selection.prune((uuid) => store.nodes.has(uuid));
    return doomed.size;
  }

  function removeSelection() {
    return remove(selection.list());
  }

  /* ── Conexões ───────────────────────────────────────────────────────── */

  /*
   * Conectar é pendurar, e é a única coisa que uma linha quer dizer.
   *
   * The board used to draw two kinds of line: the parent-child spine, and a
   * free association that crossed the map without changing anything. Nothing
   * on screen said which was which, and the gesture that produced each was
   * near enough a coin toss - so "conectar" could leave the structure exactly
   * as it had been. There is one line now, and connecting two topics puts one
   * inside the other, which is what a mind map means by a connection.
   */

  /** Conecta: `targetUuid` passa a ficar dentro de `sourceUuid`.
   *  Devolve o uuid do que se moveu, ou null quando foi recusado. */
  function connect(sourceUuid, targetUuid) {
    if (!sourceUuid || !targetUuid || sourceUuid === targetUuid) return null;
    if (!store.get(sourceUuid) || !store.get(targetUuid)) return null;
    return reparent(targetUuid, sourceUuid) ? targetUuid : null;
  }

  /** How many branches carry an arrangement of their own. */
  function ownArrangements() {
    return [...store.nodes.values()].filter((node) => node.layout).length;
  }

  /** Hand every branch back to the map.
   *
   *  One mutation, so one Ctrl+Z takes it back: clearing them one at a time
   *  would leave someone pressing undo once per branch to recover a decision
   *  they made once.
   */
  function clearBranchLayouts() {
    const own = [...store.nodes.values()].filter((node) => node.layout);
    if (!own.length) return 0;
    const applied = store.mutate(() => {
      own.forEach((node) => {
        node.layout = '';
      });
    });
    return applied ? own.length : 0;
  }

  /* ── Moving around ──────────────────────────────────────────────────── */

  /**
   * The node in a given direction, following the hierarchy rather than the
   * geometry: one arrow goes into the branch, its opposite goes back to the
   * parent, and the other two walk the siblings. That is how the map is read
   * out loud, and it is what makes the arrow keys navigate an outline rather
   * than a plane.
   *
   * Which arrow does which turns with the map, and on a mixed board it turns
   * twice over: where a node's *children* sit is decided by the node's own
   * arrangement, and where the node itself sits among its siblings is decided
   * by its parent's. An organogram hanging off a horizontal spine reaches its
   * children downwards and its parent leftwards, and both are right.
   *
   * That is also the one place two meanings can land on one key: with the
   * branch growing down and the siblings stacked down, Down is both "into the
   * branch" and "next sibling". The branch wins while there is a branch, and
   * the key falls through to the sibling when there is not - so no arrow ever
   * becomes a key that does nothing.
   */
  function neighbour(uuid, direction) {
    const node = store.get(uuid);
    if (!node) return null;

    const inside = isVertical(store.arrangementOf(node));
    const parent = node.parent ? store.get(node.parent) : null;
    const around = isVertical(
      parent ? store.arrangementOf(parent) : store.layout
    );

    const intoBranch = inside ? 'down' : 'right';
    const towardsRoot = around ? 'up' : 'left';
    const previousSibling = around ? 'left' : 'up';
    const nextSibling = around ? 'right' : 'down';

    if (direction === intoBranch && !node.collapsed) {
      const kids = store.children(uuid);
      if (kids.length) return kids[0].uuid;
    }
    if (direction === towardsRoot) return node.parent;
    if (direction !== previousSibling && direction !== nextSibling) return null;

    const siblings = store.children(node.parent);
    const index = siblings.findIndex((item) => item.uuid === uuid);
    if (index < 0) return null;
    const target = direction === previousSibling ? index - 1 : index + 1;
    return siblings[target] ? siblings[target].uuid : null;
  }

  return {
    addChild,
    addSibling,
    addLoose,
    addNote,
    duplicate,
    update,
    updateSelection,
    moveBy,
    moveTo,
    reparent,
    detach,
    moveInto,
    indent,
    outdent,
    shiftSibling,
    ownArrangements,
    clearBranchLayouts,
    toggleCollapse,
    remove,
    removeSelection,
    connect,
    shareInto,
    neighbour,
  };
}
