/**
 * The camera and the renderer: everything that turns the model into pixels.
 *
 * Nodes are DOM elements and connections are SVG paths, both inside one
 * transformed world. That combination is deliberate:
 *
 * * **DOM for nodes**, because a node is text. Text in a `<canvas>` cannot be
 *   selected, cannot be edited in place, cannot be found with Ctrl+F, and is
 *   invisible to a screen reader. Every one of those is a real capability this
 *   board keeps by paying for elements instead.
 * * **SVG for links**, because a curve is a curve and one `d` attribute beats
 *   any arrangement of divs.
 * * **One transform for the camera.** Panning and zooming move a single
 *   element, so the browser composites the whole board on the GPU instead of
 *   relaying out a thousand children.
 *
 * The renderer reconciles rather than rebuilds: elements are kept in a map by
 * UUID and updated in place. Throwing the board away on every change would
 * lose focus, lose a text selection mid-edit, and drop the caret out of the
 * node being typed into.
 *
 * No inline styles anywhere - positions ride on custom properties written
 * through the CSSOM, which the strict CSP allows.
 */

import { branchPath, freePath, isVertical, routingFor } from './routing.js';

const NS_SVG = 'http://www.w3.org/2000/svg';
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 4;
const GRID_BASE = 28;

/* ── Camera ───────────────────────────────────────────────────────────── */

export function createCamera(page, world, stage) {
  let x = 0;
  let y = 0;
  let zoom = 1;
  const listeners = [];

  function write() {
    world.style.setProperty('--mm-x', `${x}px`);
    world.style.setProperty('--mm-y', `${y}px`);
    world.style.setProperty('--mm-zoom', String(zoom));
    // The dot grid belongs to the paper, not to the content: it scales with
    // the zoom and slides with the pan so the board feels like a surface
    // rather than a texture painted on the viewport.
    page.style.setProperty('--mm-grid-size', `${GRID_BASE * zoom}px`);
    page.style.setProperty('--mm-grid-offset-x', `${x % (GRID_BASE * zoom)}px`);
    page.style.setProperty('--mm-grid-offset-y', `${y % (GRID_BASE * zoom)}px`);
    listeners.forEach((handler) => handler({ x, y, zoom }));
  }

  function toWorld(clientX, clientY) {
    const box = stage.getBoundingClientRect();
    return {
      x: (clientX - box.left - x) / zoom,
      y: (clientY - box.top - y) / zoom,
    };
  }

  /** Zoom around a fixed point, so the board grows under the pointer. */
  function zoomTo(nextZoom, anchorX, anchorY) {
    const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
    if (clamped === zoom) return;
    const box = stage.getBoundingClientRect();
    const px = anchorX === undefined ? box.width / 2 : anchorX - box.left;
    const py = anchorY === undefined ? box.height / 2 : anchorY - box.top;

    x = px - ((px - x) / zoom) * clamped;
    y = py - ((py - y) / zoom) * clamped;
    zoom = clamped;
    write();
  }

  function panBy(dx, dy) {
    x += dx;
    y += dy;
    write();
  }

  function moveTo(nextX, nextY, nextZoom) {
    x = nextX;
    y = nextY;
    if (nextZoom) zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
    write();
  }

  /** Frame a rectangle of world space inside the stage, with breathing room. */
  function fit(box, padding = 80) {
    const view = stage.getBoundingClientRect();
    if (!box || box.width <= 0 || box.height <= 0) return;
    const scale = Math.min(
      (view.width - padding * 2) / box.width,
      (view.height - padding * 2) / box.height,
      1.6
    );
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
    x = view.width / 2 - (box.x + box.width / 2) * zoom;
    y = view.height / 2 - (box.y + box.height / 2) * zoom;
    write();
  }

  return {
    get x() { return x; },
    get y() { return y; },
    get zoom() { return zoom; },
    toWorld,
    zoomTo,
    panBy,
    moveTo,
    fit,
    write,
    onChange(handler) { listeners.push(handler); },
  };
}

/* ── Renderer ─────────────────────────────────────────────────────────── */

export function createRenderer({ store, page, nodesHost, linksHost, labelsHost, accent }) {
  const nodeElements = new Map();
  const linkElements = new Map();
  let selection = new Set();
  let draftLink = null;

  /* Which faces of a node a connection uses, as one attribute on the board.
     CSS reads it to put the connection ports on the right two sides: dragging
     a link out of the left edge of a node whose children hang underneath it
     is an invitation to draw the map the wrong way round. */
  function orientation() {
    if (store.layout === 'radial') return 'radial';
    return isVertical(store.layout) ? 'vertical' : 'horizontal';
  }

  /* -- Nodes -- */

  function buildNode(uuid) {
    const element = document.createElement('article');
    element.className = 'mm-node';
    element.dataset.uuid = uuid;
    element.tabIndex = -1;
    element.setAttribute('role', 'treeitem');

    const media = document.createElement('div');
    media.className = 'mm-node-media';
    media.hidden = true;
    const image = document.createElement('img');
    image.alt = '';
    image.loading = 'lazy';
    image.decoding = 'async';
    image.draggable = false;
    media.appendChild(image);

    const label = document.createElement('div');
    label.className = 'mm-node-label';
    label.dataset.label = '';

    const badges = document.createElement('div');
    badges.className = 'mm-node-badges';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'mm-node-toggle';
    toggle.dataset.toggle = '';
    toggle.hidden = true;

    element.append(media, label, badges, toggle);
    // All four are built and CSS shows the pair the layout uses. Building the
    // two that are wanted would mean rebuilding every node when the layout
    // changes, which is the one moment the board must not flicker.
    ['left', 'right', 'top', 'bottom'].forEach((side) => {
      const port = document.createElement('span');
      port.className = 'mm-port';
      port.dataset.port = side;
      port.dataset.side = side;
      element.appendChild(port);
    });

    const handle = document.createElement('span');
    handle.className = 'mm-handle';
    handle.dataset.handle = '';
    element.appendChild(handle);

    nodesHost.appendChild(element);
    nodeElements.set(uuid, element);
    return element;
  }

  function paintNode(node, element) {
    element.style.setProperty('--mm-node-x', `${node.x}px`);
    element.style.setProperty('--mm-node-y', `${node.y}px`);
    element.style.setProperty('--mm-node-w', `${node.width}px`);

    const colour = node.color || (node.parent ? '' : accent);
    if (colour) {
      element.style.setProperty('--mm-node-color', colour);
      element.dataset.colored = 'true';
    } else {
      element.style.removeProperty('--mm-node-color');
      delete element.dataset.colored;
    }

    element.dataset.kind = node.kind;
    element.dataset.shape = node.shape;
    element.dataset.root = node.parent ? 'false' : 'true';
    element.dataset.selected = selection.has(node.uuid) ? 'true' : 'false';
    element.dataset.collapsed = node.collapsed ? 'true' : 'false';

    const label = element.querySelector('[data-label]');
    // Never while the writer is typing into it: the caret would jump to the
    // start on every keystroke that triggers a render.
    if (label.textContent !== node.text && element.dataset.editing !== 'true') {
      label.textContent = node.text;
    }

    const media = element.querySelector('.mm-node-media');
    const image = media.querySelector('img');
    const source = node.kind === 'image' ? node.image : '';
    media.hidden = !source;
    if (source && image.getAttribute('src') !== source) {
      image.setAttribute('src', source);
      image.alt = node.text || 'Imagem do tópico';
    }
    if (!source) image.removeAttribute('src');

    paintBadges(node, element.querySelector('.mm-node-badges'));

    const kids = store.children(node.uuid);
    const toggle = element.querySelector('[data-toggle]');
    toggle.hidden = kids.length === 0;
    if (kids.length) {
      toggle.textContent = node.collapsed ? String(countBranch(node.uuid)) : '−';
      const label2 = node.collapsed ? 'Expandir ramo' : 'Recolher ramo';
      toggle.setAttribute('aria-label', label2);
      toggle.title = label2;
    }

    element.setAttribute(
      'aria-label',
      `${node.text || 'Tópico sem título'}${kids.length ? `, ${kids.length} subtópicos` : ''}`
    );
  }

  function paintBadges(node, host) {
    const wanted = [];
    if (node.url) wanted.push(['link', node.url, 'Abrir link']);
    if (node.document) wanted.push(['file', node.document.url, 'Abrir documento']);
    if (node.note) wanted.push(['quote', '', 'Tem anotação']);

    // Rebuilt rather than reconciled: a node carries at most three of these,
    // and the diff would cost more than the three elements it saves.
    host.replaceChildren();
    wanted.forEach(([name, href, title]) => {
      const element = href
        ? document.createElement('a')
        : document.createElement('span');
      element.className = 'mm-node-badge';
      element.title = title;
      if (href) {
        element.href = href;
        element.target = '_blank';
        // noopener is what stops the opened page from reaching back into this
        // one through window.opener.
        element.rel = 'noopener noreferrer';
        element.dataset.badgeLink = '';
      }
      element.appendChild(icon(name));
      host.appendChild(element);
    });
  }

  function icon(name) {
    const svg = document.createElementNS(NS_SVG, 'svg');
    svg.setAttribute('class', 'icon icon-sm');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const use = document.createElementNS(NS_SVG, 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.appendChild(use);
    return svg;
  }

  function countBranch(uuid) {
    return Math.max(store.branch(uuid).length - 1, 0);
  }

  /* -- Links -- */

  function buildLink(key, kind) {
    const group = document.createElementNS(NS_SVG, 'g');
    const hit = document.createElementNS(NS_SVG, 'path');
    hit.setAttribute('class', 'mm-link mm-link-hit');
    const path = document.createElementNS(NS_SVG, 'path');
    path.setAttribute('class', `mm-link mm-link-${kind}`);
    group.append(hit, path);
    linksHost.appendChild(group);
    linkElements.set(key, { group, path, hit, label: null, plate: null });
    return linkElements.get(key);
  }

  function renderLinks() {
    const wanted = new Set();
    const routing = routingFor(store.layout);
    if (page) page.dataset.orientation = orientation();

    store.nodes.forEach((node) => {
      if (!node.parent) return;
      const parent = store.get(node.parent);
      if (!parent) return;
      if (!store.isVisible(node) || !store.isVisible(parent)) return;

      const key = `b:${node.uuid}`;
      wanted.add(key);
      const entry = linkElements.get(key) || buildLink(key, 'branch');
      const path = branchPath(routing, parent, node);
      entry.path.setAttribute('d', path);
      entry.hit.setAttribute('d', path);
      entry.group.dataset.branch = node.uuid;
      const colour = node.color || parent.color || accent;
      entry.path.style.setProperty('--mm-link-color', colour);
    });

    store.edges.forEach((edge) => {
      const source = store.get(edge.source);
      const target = store.get(edge.target);
      if (!source || !target) return;
      if (!store.isVisible(source) || !store.isVisible(target)) return;

      const key = `e:${edge.uuid}`;
      wanted.add(key);
      const entry = linkElements.get(key) || buildLink(key, 'free');
      const path = freePath(edge.style, source, target);
      entry.path.setAttribute('d', path);
      entry.hit.setAttribute('d', path);
      entry.path.dataset.style = edge.style;
      entry.group.dataset.edge = edge.uuid;
      entry.hit.dataset.edge = edge.uuid;
      if (edge.color) entry.path.style.setProperty('--mm-link-color', edge.color);
      else entry.path.style.removeProperty('--mm-link-color');
      paintEdgeLabel(entry, edge, source, target);
    });

    linkElements.forEach((entry, key) => {
      if (!wanted.has(key)) {
        entry.group.remove();
        // The label lives in its own layer, so removing the group no longer
        // takes it along - it would sit on the board with nothing under it.
        if (entry.label) entry.label.remove();
        if (entry.plate) entry.plate.remove();
        linkElements.delete(key);
      }
    });
  }

  function paintEdgeLabel(entry, edge, source, target) {
    if (!edge.label) {
      if (entry.label) {
        entry.label.remove();
        entry.plate.remove();
        entry.label = null;
        entry.plate = null;
      }
      return;
    }
    if (!entry.label) {
      entry.plate = document.createElementNS(NS_SVG, 'rect');
      entry.plate.setAttribute('class', 'mm-link-label-plate');
      entry.plate.setAttribute('rx', '6');
      entry.plate.setAttribute('height', '20');
      entry.label = document.createElementNS(NS_SVG, 'text');
      entry.label.setAttribute('class', 'mm-link-label');
      // Into the layer above the nodes, not into the link's own group.
      labelsHost.append(entry.plate, entry.label);
    }
    const x = (source.x + source.width / 2 + target.x + target.width / 2) / 2;
    const y = (source.y + source.height / 2 + target.y + target.height / 2) / 2;
    const width = edge.label.length * 7 + 14;
    entry.label.textContent = edge.label;
    entry.label.setAttribute('x', String(x));
    entry.label.setAttribute('y', String(y + 4));
    entry.plate.setAttribute('x', String(x - width / 2));
    entry.plate.setAttribute('y', String(y - 11));
    entry.plate.setAttribute('width', String(width));
  }

  /** The dashed line that follows the pointer while a connection is drawn. */
  function showDraft(from, to) {
    if (!draftLink) {
      draftLink = document.createElementNS(NS_SVG, 'path');
      draftLink.setAttribute('class', 'mm-draft-link');
      linksHost.appendChild(draftLink);
    }
    draftLink.setAttribute('d', `M${from.x},${from.y} L${to.x},${to.y}`);
  }

  function hideDraft() {
    if (draftLink) {
      draftLink.remove();
      draftLink = null;
    }
  }

  /* -- Full pass -- */

  function render() {
    const wanted = new Set();

    store.nodes.forEach((node) => {
      if (!store.isVisible(node)) return;
      wanted.add(node.uuid);
      const element = nodeElements.get(node.uuid) || buildNode(node.uuid);
      paintNode(node, element);
    });

    nodeElements.forEach((element, uuid) => {
      if (!wanted.has(uuid)) {
        element.remove();
        nodeElements.delete(uuid);
      }
    });

    renderLinks();
  }

  /**
   * Read back what the browser actually laid out.
   *
   * A node is as tall as its text, and only the browser knows that. The value
   * is pushed into the model so the server can lay the board out and draw the
   * SVG export with real sizes instead of estimates.
   */
  function measure() {
    let changed = false;
    nodeElements.forEach((element, uuid) => {
      const node = store.get(uuid);
      if (!node) return;
      const height = element.offsetHeight;
      if (height && store.measured(uuid, node.width, height)) changed = true;
    });
    if (changed) renderLinks();
  }

  function setSelection(next) {
    selection = next;
    nodeElements.forEach((element, uuid) => {
      element.dataset.selected = selection.has(uuid) ? 'true' : 'false';
    });
  }

  /** The rectangle every visible node fits inside, in world coordinates. */
  function bounds() {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    store.nodes.forEach((node) => {
      if (!store.isVisible(node)) return;
      minX = Math.min(minX, node.x);
      minY = Math.min(minY, node.y);
      maxX = Math.max(maxX, node.x + node.width);
      maxY = Math.max(maxY, node.y + node.height);
    });
    if (minX === Infinity) return null;
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
  }

  return {
    render,
    renderLinks,
    measure,
    setSelection,
    bounds,
    showDraft,
    hideDraft,
    elementFor(uuid) { return nodeElements.get(uuid); },
    get elements() { return nodeElements; },
  };
}
