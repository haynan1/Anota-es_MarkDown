/**
 * The map, in the browser: one source of truth, one way to change it, and one
 * way it reaches the server.
 *
 * Why a diff and not a log of commands
 * ------------------------------------
 * A canvas produces a torrent of tiny changes, and the obvious design - record
 * every gesture as a command and replay the list at the server - turns out to
 * be the fragile one. Undo needs an inverse for every command; a dropped
 * response leaves the log and the database disagreeing; and "drag fifty nodes"
 * becomes fifty commands describing a single thought.
 *
 * So nothing is recorded. Mutations edit a plain local model, and when it is
 * time to save, the model is *compared* against the last state the server
 * acknowledged. The difference is the batch. That gives three things for free:
 *
 * * **Undo is a snapshot swap.** Restore an earlier model and the next diff
 *   automatically describes how to get the server there. No inverse operations
 *   to write, and none to get wrong.
 * * **Coalescing is automatic.** A node dragged across the screen over two
 *   seconds is one `node.update` carrying its final position.
 * * **A failed save is harmless.** The baseline only moves when the server
 *   says yes, so the next attempt re-sends everything that has not landed.
 *
 * The cost is holding two copies of the graph in memory. For a board capped at
 * a thousand nodes that is a few hundred kilobytes - a price worth paying to
 * never have to reason about a half-applied command log.
 */

import { postJSON } from '../dom.js';

/** Fields that travel to the server unchanged, and are compared one by one. */
const NODE_FIELDS = [
  'text', 'note', 'url', 'image_url', 'media_uuid', 'document_uuid',
  'kind', 'shape', 'color', 'collapsed', 'layout', 'x', 'y', 'width', 'height',
];
const EDGE_FIELDS = ['label', 'style', 'color'];

const SAVE_DEBOUNCE_MS = 700;
const UNDO_DEPTH = 80;
/** Heights measured by the browser jitter by fractions of a pixel; only a real
 *  change is worth a round trip. */
const SIZE_EPSILON = 1.5;

export function createStore(config) {
  const listeners = { change: [], status: [], conflict: [] };

  let nodes = new Map();
  let edges = new Map();
  let revision = config.revision || 1;
  /* How this map arranges itself. Held here rather than read off the page,
     because "arrumar" can change it without a reload and everything that
     draws - the links, the ports, where a new subtopic is born - has to move
     with it in the same frame. */
  let layout = config.layout || 'right';

  /** The last graph the server confirmed. Every diff is measured from here. */
  let baseline = { nodes: new Map(), edges: new Map() };
  let inFlight = null;

  const undoStack = [];
  const redoStack = [];

  let timer = null;
  let status = 'saved';

  /* ── Events ─────────────────────────────────────────────────────────── */

  function on(event, handler) {
    (listeners[event] || []).push(handler);
  }
  function emit(event, payload) {
    (listeners[event] || []).forEach((handler) => handler(payload));
  }
  function setStatus(next) {
    if (status === next) return;
    status = next;
    emit('status', status);
  }

  /* ── Loading ────────────────────────────────────────────────────────── */

  function adopt(graph) {
    nodes = new Map();
    edges = new Map();
    (graph.nodes || []).forEach((node) => nodes.set(node.uuid, normalizeNode(node)));
    (graph.edges || []).forEach((edge) => edges.set(edge.uuid, normalizeEdge(edge)));
    revision = graph.revision || revision;
    if (graph.layout) layout = graph.layout;
    childIndex = null;
    arrangementIndex = null;
    baseline = cloneGraph(nodes, edges);
    undoStack.length = 0;
    redoStack.length = 0;
    setStatus('saved');
    emit('change', { structural: true });
  }

  /**
   * The graph carries identities; the page carries the URL shapes. Resolving
   * them here means every consumer - the renderer, the inspector, the outline
   * - reads one already-usable address, and the server never had to know how
   * this application routes.
   */
  function resolve(template, uuid) {
    if (!template || !uuid) return '';
    return template.replace('00000000-0000-0000-0000-000000000000', uuid);
  }

  function normalizeNode(raw) {
    const document_ = raw.document
      ? { ...raw.document, url: resolve(config.documentUrl, raw.document.uuid) }
      : null;
    const image = raw.media_uuid
      ? resolve(config.mediaUrl, raw.media_uuid)
      : raw.image_url || '';
    return {
      uuid: raw.uuid,
      parent: raw.parent || null,
      position: Number(raw.position) || 0,
      kind: raw.kind || 'topic',
      text: raw.text || '',
      note: raw.note || '',
      url: raw.url || '',
      image,
      image_url: raw.image_url || '',
      media_uuid: raw.media_uuid || '',
      document: document_,
      document_uuid: raw.document ? raw.document.uuid : '',
      x: Number(raw.x) || 0,
      y: Number(raw.y) || 0,
      width: Number(raw.width) || 180,
      height: Number(raw.height) || 48,
      color: raw.color || '',
      shape: raw.shape || 'rounded',
      // '' means "the same as whatever this branch hangs from" - a real
      // answer, and the one almost every node gives.
      layout: raw.layout || '',
      collapsed: Boolean(raw.collapsed),
    };
  }

  function normalizeEdge(raw) {
    return {
      uuid: raw.uuid,
      source: raw.source,
      target: raw.target,
      label: raw.label || '',
      style: raw.style || 'curve',
      color: raw.color || '',
    };
  }

  /* ── Reading ────────────────────────────────────────────────────────── */

  /**
   * Children by parent, rebuilt only when the shape of the tree changed.
   * Every render walks this; recomputing it per lookup was the difference
   * between a smooth drag and a stuttering one on a large board.
   */
  let childIndex = null;
  let arrangementIndex = null;
  function children(uuid) {
    if (childIndex === null) {
      childIndex = new Map();
      const ordered = [...nodes.values()].sort(
        (a, b) => a.position - b.position || a.uuid.localeCompare(b.uuid)
      );
      ordered.forEach((node) => {
        const key = node.parent || '';
        if (!childIndex.has(key)) childIndex.set(key, []);
        childIndex.get(key).push(node);
      });
    }
    return childIndex.get(uuid || '') || [];
  }

  function roots() {
    return children(null);
  }

  /**
   * What a node is actually arranged by, once inheritance is applied.
   *
   * A node that names nothing is arranged by whatever its parent is arranged
   * by, and a root that names nothing is arranged by the map. That is what
   * keeps "arrumar" meaning the whole map: only the branches given an opinion
   * of their own keep it.
   *
   * The same rule the server applies in `effective_layouts`, because both
   * have to answer it - the server to place the nodes, the canvas to draw the
   * lines between them on every frame of a drag.
   */
  function arrangements() {
    if (arrangementIndex !== null) return arrangementIndex;
    arrangementIndex = new Map();
    const walk = (list, inherited) => {
      list.forEach((node) => {
        const mine = node.layout || inherited;
        arrangementIndex.set(node.uuid, mine);
        walk(children(node.uuid), mine);
      });
    };
    walk(roots(), layout);
    return arrangementIndex;
  }

  /** How `node`'s own branch is arranged - what decides where its children
   *  go, which face they leave from, and which arrow reaches them. */
  function arrangementOf(node) {
    if (!node) return layout;
    return arrangements().get(node.uuid) || layout;
  }

  /** A node and everything hanging off it, parents first. */
  function branch(uuid) {
    const found = [];
    const frontier = [uuid];
    const seen = new Set();
    while (frontier.length) {
      const current = frontier.shift();
      if (seen.has(current) || !nodes.has(current)) continue;
      seen.add(current);
      found.push(current);
      children(current).forEach((child) => frontier.push(child.uuid));
    }
    return found;
  }

  /** Whether every ancestor of a node is expanded - i.e. it is on screen. */
  function isVisible(node) {
    let cursor = node.parent;
    let guard = 0;
    while (cursor && guard < 64) {
      const parent = nodes.get(cursor);
      if (!parent) return true;
      if (parent.collapsed) return false;
      cursor = parent.parent;
      guard += 1;
    }
    return true;
  }

  function ancestorOf(candidate, uuid) {
    let cursor = nodes.get(uuid);
    let guard = 0;
    while (cursor && cursor.parent && guard < 64) {
      if (cursor.parent === candidate) return true;
      cursor = nodes.get(cursor.parent);
      guard += 1;
    }
    return false;
  }

  /* ── Writing ────────────────────────────────────────────────────────── */

  /**
   * The only way to change the map. Everything else in the canvas calls this.
   *
   * `structural` says the parent/child shape may have moved, which is the one
   * thing the child index cannot detect on its own.
   */
  function mutate(apply, { structural = false, record = true } = {}) {
    if (record) {
      undoStack.push(snapshot());
      if (undoStack.length > UNDO_DEPTH) undoStack.shift();
      redoStack.length = 0;
    }
    apply();
    if (structural) childIndex = null;
    arrangementIndex = null;
    setStatus('dirty');
    schedule();
    emit('change', { structural });
  }

  /**
   * A change the user did not make and must not have to undo: the browser
   * measuring how tall a node turned out to be. It still has to reach the
   * server - layout and the SVG export both place nodes by their real size.
   */
  function measured(uuid, width, height) {
    const node = nodes.get(uuid);
    if (!node) return false;
    if (
      Math.abs(node.width - width) < SIZE_EPSILON &&
      Math.abs(node.height - height) < SIZE_EPSILON
    ) {
      return false;
    }
    node.width = width;
    node.height = height;
    setStatus('dirty');
    schedule();
    return true;
  }

  function snapshot() {
    return {
      nodes: [...nodes.values()].map((node) => ({ ...node })),
      edges: [...edges.values()].map((edge) => ({ ...edge })),
    };
  }

  function restore(state) {
    nodes = new Map(state.nodes.map((node) => [node.uuid, { ...node }]));
    edges = new Map(state.edges.map((edge) => [edge.uuid, { ...edge }]));
    childIndex = null;
    arrangementIndex = null;
    setStatus('dirty');
    schedule();
    emit('change', { structural: true });
  }

  function undo() {
    const previous = undoStack.pop();
    if (!previous) return false;
    redoStack.push(snapshot());
    restore(previous);
    return true;
  }

  function redo() {
    const next = redoStack.pop();
    if (!next) return false;
    undoStack.push(snapshot());
    restore(next);
    return true;
  }

  /* ── Diffing ────────────────────────────────────────────────────────── */

  function cloneGraph(nodeMap, edgeMap) {
    return {
      nodes: new Map([...nodeMap].map(([key, value]) => [key, { ...value }])),
      edges: new Map([...edgeMap].map(([key, value]) => [key, { ...value }])),
    };
  }

  /**
   * Everything the server would have to do to look like the local model.
   *
   * Deletes go last on purpose: a node re-parented onto a sibling that is
   * being removed in the same batch has to move before the ground opens.
   */
  function diff(from, to) {
    const operations = [];
    const removals = [];

    to.nodes.forEach((node, uuid) => {
      const before = from.nodes.get(uuid);
      if (!before) {
        operations.push({
          type: 'node.create',
          uuid,
          parent: node.parent,
          fields: fieldsOf(node, NODE_FIELDS),
        });
        return;
      }
      if (before.parent !== node.parent || before.position !== node.position) {
        operations.push({
          type: 'node.move',
          uuid,
          parent: node.parent,
          position: node.position,
          x: node.x,
          y: node.y,
        });
      }
      const changed = changedFields(before, node, NODE_FIELDS);
      if (Object.keys(changed).length) {
        operations.push({ type: 'node.update', uuid, fields: changed });
      }
    });

    from.nodes.forEach((_node, uuid) => {
      if (!to.nodes.has(uuid)) removals.push({ type: 'node.delete', uuid });
    });

    to.edges.forEach((edge, uuid) => {
      const before = from.edges.get(uuid);
      if (!before) {
        operations.push({
          type: 'edge.create',
          uuid,
          source: edge.source,
          target: edge.target,
          fields: fieldsOf(edge, EDGE_FIELDS),
        });
        return;
      }
      const changed = changedFields(before, edge, EDGE_FIELDS);
      if (Object.keys(changed).length) {
        operations.push({ type: 'edge.update', uuid, fields: changed });
      }
    });

    from.edges.forEach((_edge, uuid) => {
      if (!to.edges.has(uuid)) removals.push({ type: 'edge.delete', uuid });
    });

    return [...operations, ...removals];
  }

  function fieldsOf(source, keys) {
    const result = {};
    keys.forEach((key) => {
      result[key] = source[key];
    });
    return result;
  }

  function changedFields(before, after, keys) {
    const result = {};
    keys.forEach((key) => {
      if (before[key] !== after[key]) result[key] = after[key];
    });
    return result;
  }

  /* ── Saving ─────────────────────────────────────────────────────────── */

  function schedule() {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      flush().catch(() => setStatus('error'));
    }, SAVE_DEBOUNCE_MS);
  }

  async function flush({ keepalive = false } = {}) {
    window.clearTimeout(timer);
    // One request at a time. A second batch composed while the first is in
    // flight would be measured from a baseline the server has not adopted yet.
    if (inFlight) return inFlight;

    const pending = cloneGraph(nodes, edges);
    const operations = diff(baseline, pending);
    if (!operations.length) {
      setStatus('saved');
      return { ok: true, applied: 0 };
    }

    setStatus('saving');
    inFlight = send(operations, pending, keepalive);
    try {
      return await inFlight;
    } finally {
      inFlight = null;
    }
  }

  async function send(operations, pending, keepalive) {
    const { ok, status: code, data } = await postJSON(
      config.opsUrl,
      { revision, operations },
      { keepalive }
    );

    if (ok && data && data.ok) {
      revision = data.revision;
      baseline = pending;
      // Anything typed while the request was open is still unsaved, and the
      // next diff will carry it: the baseline moved, the model did not.
      const remaining = diff(baseline, cloneGraph(nodes, edges));
      setStatus(remaining.length ? 'dirty' : 'saved');
      if (remaining.length) schedule();
      return { ok: true, applied: data.applied };
    }

    if (code === 409 && data && data.server_state) {
      emit('conflict', data.server_state);
      adopt(data.server_state);
      return { ok: false, conflict: true };
    }

    setStatus('error');
    return { ok: false, error: (data && data.error) || 'Não foi possível salvar.' };
  }

  /* ── Server-side operations ─────────────────────────────────────────── */

  /**
   * Ask the server to tidy the board. Local edits are flushed first, so the
   * layout is computed over the map as it looks on screen and not over the
   * version the server happened to have.
   */
  async function organize(layout) {
    await flush();
    setStatus('saving');
    const { ok, data } = await postJSON(config.layoutUrl, { layout });
    if (ok && data && data.ok) {
      adopt(data.graph);
      return true;
    }
    setStatus('error');
    return false;
  }

  async function reload() {
    const response = await fetch(config.graphUrl, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return false;
    const data = await response.json();
    if (!data || !data.ok) return false;
    adopt(data.graph);
    return true;
  }

  return {
    on,
    adopt,
    reload,
    organize,
    mutate,
    measured,
    flush,
    undo,
    redo,
    get nodes() { return nodes; },
    get edges() { return edges; },
    get revision() { return revision; },
    get layout() { return layout; },
    arrangementOf,
    get status() { return status; },
    get canUndo() { return undoStack.length > 0; },
    get canRedo() { return redoStack.length > 0; },
    get(uuid) { return nodes.get(uuid); },
    children,
    roots,
    branch,
    isVisible,
    ancestorOf,
    hasPendingChanges() {
      return diff(baseline, cloneGraph(nodes, edges)).length > 0;
    },
  };
}
