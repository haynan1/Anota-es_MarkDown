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

import { branchPath, isVertical, routingFor } from './routing.js';

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

export function createRenderer({ store, page, nodesHost, linksHost, accent }) {
  const nodeElements = new Map();
  const linkElements = new Map();
  let selection = new Set();
  /* Which connection is selected, as the key the link map uses: `b:<uuid>`
     for a parent-child line, `e:<uuid>` for an association. One at a time,
     because a connection is selected by clicking exactly one of them. */
  let selectedLink = null;
  let draftLink = null;

  /* Which faces a connection uses, as one attribute - written on each node
     rather than on the board, because each branch can be arranged its own
     way. CSS reads it to put the connection ports on the right two sides:
     dragging a link out of the left edge of a node whose children hang
     underneath it is an invitation to draw the map the wrong way round.

     A node's own arrangement, not its parent's: what this attribute governs
     is where *its* children go. */
  function orientation(arrangement) {
    if (arrangement === 'radial') return 'radial';
    return isVertical(arrangement) ? 'vertical' : 'horizontal';
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
    // O que se lê num espelho é o tópico original: é o mesmo tópico, visto
    // noutro lugar da árvore. A posição e o tamanho continuam sendo deste nó,
    // porque é aqui que ele está desenhado.
    const shown = store.original(node);
    const mirror = shown !== node;

    element.style.setProperty('--mm-node-x', `${node.x}px`);
    element.style.setProperty('--mm-node-y', `${node.y}px`);
    element.style.setProperty('--mm-node-w', `${node.width}px`);

    // O centro do mapa é um só: o primeiro tópico sem pai, o que nasceu com
    // o mapa. Um tópico que ficou solto - desligado, ou criado à parte - é um
    // tópico, não um segundo centro. Pintar todos com o acento fazia um ramo
    // desligado se anunciar como o assunto principal da tela, que é
    // exatamente o susto de ver "vira blocos fortes" ao cortar uma linha.
    const roots = store.roots();
    const centre = !node.parent && roots.length > 0 && roots[0].uuid === node.uuid;
    const loose = !node.parent && !centre;

    const colour = shown.color || (centre ? accent : '');
    if (colour) {
      element.style.setProperty('--mm-node-color', colour);
      element.dataset.colored = 'true';
    } else {
      element.style.removeProperty('--mm-node-color');
      delete element.dataset.colored;
    }

    element.dataset.kind = shown.kind;
    element.dataset.shape = shown.shape;
    element.dataset.mirror = mirror ? 'true' : 'false';
    element.dataset.orientation = orientation(store.arrangementOf(node));
    if (node.layout) element.dataset.ownLayout = 'true';
    else delete element.dataset.ownLayout;
    element.dataset.root = centre ? 'true' : 'false';
    element.dataset.loose = loose ? 'true' : 'false';
    element.dataset.selected = selection.has(node.uuid) ? 'true' : 'false';
    element.dataset.collapsed = node.collapsed ? 'true' : 'false';

    const label = element.querySelector('[data-label]');
    // Never while the writer is typing into it: the caret would jump to the
    // start on every keystroke that triggers a render.
    if (label.textContent !== shown.text && element.dataset.editing !== 'true') {
      label.textContent = shown.text;
    }

    const media = element.querySelector('.mm-node-media');
    const image = media.querySelector('img');
    const source = shown.kind === 'image' ? shown.image : '';
    media.hidden = !source;
    if (source && image.getAttribute('src') !== source) {
      image.setAttribute('src', source);
      image.alt = shown.text || 'Imagem do tópico';
    }
    if (!source) image.removeAttribute('src');

    paintBadges(shown, element.querySelector('.mm-node-badges'));

    // O ramo é do original. Um espelho é sempre uma folha, e o número que
    // ele carrega diz quantos subtópicos existem lá - não aqui.
    const kids = mirror ? [] : store.children(node.uuid);
    const shared = mirror ? store.children(shown.uuid).length : 0;
    const toggle = element.querySelector('[data-toggle]');
    // Num espelho o botão vira um selo: diz quantos subtópicos o original
    // tem, e não dobra nada, porque não há ramo aqui para dobrar.
    toggle.hidden = mirror ? shared === 0 : kids.length === 0;
    if (mirror && shared) {
      toggle.textContent = String(shared);
      toggle.setAttribute('aria-label', `${shared} subtópicos, no tópico original`);
      toggle.title = 'O ramo é do tópico original';
    } else if (kids.length) {
      toggle.textContent = node.collapsed ? String(countBranch(node.uuid)) : '−';
      const label2 = node.collapsed ? 'Expandir ramo' : 'Recolher ramo';
      toggle.setAttribute('aria-label', label2);
      toggle.title = label2;
    }

    const name = shown.text || 'Tópico sem título';
    element.setAttribute(
      'aria-label',
      mirror
        ? `${name}, tópico compartilhado${shared ? `, ${shared} subtópicos no original` : ''}`
        : `${name}${kids.length ? `, ${kids.length} subtópicos` : ''}`
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

  function buildLink(key) {
    const group = document.createElementNS(NS_SVG, 'g');
    const hit = document.createElementNS(NS_SVG, 'path');
    hit.setAttribute('class', 'mm-link mm-link-hit');
    const path = document.createElementNS(NS_SVG, 'path');
    path.setAttribute('class', 'mm-link mm-link-branch');
    group.append(hit, path);
    linksHost.appendChild(group);
    linkElements.set(key, { group, path, hit });
    return linkElements.get(key);
  }

  function renderLinks() {
    const wanted = new Set();
    // Still on the board, for the arrangement a map has as a whole - the
    // minimap and anything else that asks the board rather than a node.
    if (page) page.dataset.orientation = orientation(store.layout);

    store.nodes.forEach((node) => {
      if (!node.parent) return;
      const parent = store.get(node.parent);
      if (!parent) return;

      // A linha de uma segunda aparição vai do pai dela até o tópico de
      // verdade, que está desenhado noutro lugar do quadro. É essa linha que
      // diz "isto também vale aqui", e ela é a mesma linha de sempre.
      const target = store.original(node);
      if (!target || !store.isVisible(target) || !store.isVisible(parent)) return;
      if (target !== node && !store.isVisible(node)) return;

      const key = `b:${node.uuid}`;
      wanted.add(key);
      const entry = linkElements.get(key) || buildLink(key);
      const path = branchPath(
        routingFor(store.arrangementOf(parent)), parent, target
      );
      entry.path.setAttribute('d', path);
      entry.hit.setAttribute('d', path);
      // On the paths themselves, not only on the group: what a pointer lands
      // on is the fat invisible hit path, and `closest` looks upwards from
      // there. With the mark only on the group this line drew a pointer
      // cursor and answered nothing - the worst of both, an affordance that
      // promises a click it does not take.
      entry.group.dataset.branch = node.uuid;
      entry.hit.dataset.branch = node.uuid;
      entry.path.dataset.branch = node.uuid;
      paintLinkSelection(key, entry);
      // A linha compartilhada se distingue: ela chega de longe, e quem a
      // segue precisa saber que não é a linha "normal" daquele tópico.
      entry.path.dataset.shared = target !== node ? 'true' : 'false';
      const colour = target.color || parent.color || accent;
      entry.path.style.setProperty('--mm-link-color', colour);
    });


    linkElements.forEach((entry, key) => {
      if (!wanted.has(key)) {
        entry.group.remove();
        linkElements.delete(key);
      }
    });
  }

  function paintLinkSelection(key, entry) {
    entry.path.classList.toggle('is-selected', key === selectedLink);
  }

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
      // Uma segunda aparição não ganha caixa: o que a desenha é a linha que
      // sai do pai dela e chega no tópico de verdade. Sete caixas com o mesmo
      // nome dizem menos do que uma caixa com sete linhas chegando nela.
      if (node.mirror_of || !store.isVisible(node)) return;
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

  /** Highlight one connection, or none. */
  function setLinkSelection(key) {
    if (selectedLink === key) return;
    selectedLink = key;
    linkElements.forEach((entry, current) => paintLinkSelection(current, entry));
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
    setLinkSelection,
    bounds,
    showDraft,
    hideDraft,
    elementFor(uuid) { return nodeElements.get(uuid); },
    get elements() { return nodeElements; },
  };
}
