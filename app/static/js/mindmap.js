/**
 * Mapa mental: a montagem.
 *
 * Nothing in this file decides anything - it wires the pieces together and
 * owns the small amount of glue that genuinely belongs between them: the
 * render loop, the save indicator, the viewport that is remembered between
 * visits, and the toolbar buttons that call into the modules.
 *
 * The board still degrades honestly. With JavaScript unavailable the canvas
 * cannot draw, but the map is not lost: the gallery, the settings form, every
 * export - Markdown, PDF, PNG, JPEG, SVG - "salvar como documento", duplicate,
 * o cadeado e delete are all plain server-rendered pages and form posts.
 */

import { $, $$, closeDialog, debounce, openDialog, postJSON } from './modules/dom.js';
import { toast } from './modules/toasts.js';
import { createStore } from './modules/mindmap/store.js';
import { createCamera, createRenderer } from './modules/mindmap/canvas.js';
import { createActions, createSelection } from './modules/mindmap/actions.js';
import { createInteractions } from './modules/mindmap/interactions.js';
import { createInspector } from './modules/mindmap/inspector.js';
import { createUploader } from './modules/mindmap/media.js';
import { createMinimap } from './modules/mindmap/minimap.js';

const VIEWPORT_SAVE_MS = 900;
const ZOOM_STEP = 1.2;

function boot() {
  const page = $('[data-mind-map]');
  if (!page) return;

  let graph = null;
  try {
    graph = JSON.parse(page.dataset.graph || '{}');
  } catch (error) {
    graph = null;
  }
  if (!graph) {
    toast('Não foi possível ler este mapa. Recarregue a página.', 'error');
    return;
  }

  const stage = $('[data-stage]', page);
  const world = $('[data-world]', page);
  const nodesHost = $('[data-nodes]', page);
  const linksHost = $('[data-links]', page);
  const marquee = $('[data-marquee]', page);
  const hint = $('[data-hint]', page);
  const statusOutput = $('[data-save-status]', page);
  const statusLabel = $('[data-save-label]', page);
  const zoomLevel = $('[data-zoom-level]', page);
  const lostButton = $('[data-lost]', page);
  const minimapHost = $('[data-minimap]', page);

  const accent = page.dataset.accent || '#4F46E5';

  /* ── Model ────────────────────────────────────────────────────────── */

  const store = createStore({
    opsUrl: page.dataset.opsUrl,
    graphUrl: page.dataset.graphUrl,
    layoutUrl: page.dataset.layoutUrl,
    mediaUrl: page.dataset.mediaUrl,
    documentUrl: page.dataset.documentUrl,
    revision: Number(page.dataset.revision) || 1,
    locked: page.hasAttribute('data-locked'),
  });

  const camera = createCamera(page, world, stage);
  const renderer = createRenderer({ store, page, nodesHost, linksHost, accent });

  const selection = createSelection((members) => {
    renderer.setSelection(members);
    if (members.size) inspector.clearLink();
    renderer.setLinkSelection(selectedLinkKey());
    inspector.refresh();
    scheduleOutline();
  });

  const notify = (message, kind = 'info', options = {}) => toast(message, kind, options);

  const actions = createActions({
    store,
    selection,
    limits: graph.limits || { nodes: 1000, edges: 2000 },
    notify,
  });

  const uploader = createUploader({
    url: page.dataset.uploadUrl,
    limit: Number(page.dataset.uploadLimit) || 0,
    input: $('[data-file-input]', page),
    store,
    actions,
    selection,
    notify,
    onDone: () => inspector.refresh(),
  });

  const inspector = createInspector({
    root: page,
    store,
    selection,
    actions,
    notify,
    uploader,
    searchUrl: page.dataset.searchUrl,
    onAddChild(uuid) {
      const created = actions.addChild(uuid);
      if (created) interactions.beginEdit(created.uuid, { fresh: true });
    },
    onConnectFrom(uuid) {
      interactions.setTool('connect');
      selection.only(uuid);
      interactions.showHint('Clique no tópico que vai ficar dentro deste.');
    },
    onAttachTo(uuid) {
      selection.only(uuid);
      interactions.beginAttach(uuid);
    },
    onShare(uuid) {
      selection.only(uuid);
      interactions.beginAttach(uuid, 'share');
    },
    onReveal: reveal,
  });

  const minimap = createMinimap({ host: minimapHost, canvas: $('[data-minimap-canvas]', page), store, camera, stage });

  const interactions = createInteractions({
    page,
    stage,
    store,
    camera,
    renderer,
    selection,
    actions,
    marquee,
    hint,
    uploader,
    // Which line is lit is the inspector's answer and the renderer's job to
    // paint, so every path that changes the answer says so straight after.
    onSelectBranch: (uuid) => {
      inspector.selectBranch(uuid);
      renderer.setLinkSelection(selectedLinkKey());
    },
    onRemoveLink: () => {
      const removed = inspector.removeSelectedLink();
      renderer.setLinkSelection(selectedLinkKey());
      return removed;
    },
    onEditEnd: () => inspector.refresh(),
    undo: () => {
      if (!store.undo()) notify('Nada para desfazer.', 'info', { timeout: 1600 });
    },
    redo: () => {
      if (!store.redo()) notify('Nada para refazer.', 'info', { timeout: 1600 });
    },
    // The shortcut asks the same question the button does: Ctrl+Shift+O is
    // easy to hit while reaching for Ctrl+Shift+Z.
    organize: confirmOrganize,
    fit,
    reveal,
    centrePlacement,
  });

  /* ── Render loop ──────────────────────────────────────────────────── */

  let frame = null;
  function scheduleRender() {
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = null;
      renderer.render();
      // Measuring after painting is what keeps a node as tall as its text:
      // the browser is the only thing that knows, and the model has to learn
      // it for layout and export to place the box correctly.
      renderer.measure();
      minimap.draw();
      refreshHistoryButtons();
      checkIfLost();
    });
  }

  const scheduleOutline = debounce(() => inspector.renderOutline(), 120);

  /** The selected connection as the renderer names it, or null. */
  function selectedLinkKey() {
    return inspector.branch ? `b:${inspector.branch}` : null;
  }

  store.on('change', () => {
    selection.prune((uuid) => store.nodes.has(uuid));
    // Which line is lit is the inspector's answer; the renderer only paints
    // it. Read on every change so a line that was cut, or whose topic was
    // deleted, stops being highlighted.
    renderer.setLinkSelection(selectedLinkKey());
    scheduleRender();
    scheduleOutline();
    inspector.refresh();
  });

  store.on('status', (status) => {
    // Travado, o indicador nem existe na página: um "Salvo" permanente ao
    // lado de um cadeado diria a coisa certa pelo motivo errado.
    if (!statusOutput || !statusLabel) return;
    statusOutput.dataset.state = status === 'dirty' ? 'saving' : status;
    statusLabel.textContent = {
      saved: 'Salvo',
      saving: 'Salvando…',
      dirty: 'Salvando…',
      error: 'Não salvo',
    }[status] || 'Salvo';
  });

  /* Uma recusa acontece por dois motivos e a diferença importa: ou o mapa já
     estava travado quando esta aba abriu - e a pessoa acabou de tentar algo
     que a tela já mostrava desabilitado - ou ele foi travado em outro lugar
     enquanto se editava aqui, e nesse caso o que estava por salvar não foi
     salvo. O segundo caso merece a frase inteira. */
  store.on('refused', ({ remote } = {}) => {
    if (remote) {
      notify(
        'Este mapa foi travado em outro lugar. As alterações não salvas foram ' +
          'descartadas e recarregamos a versão do servidor.',
        'warning',
        { timeout: 9000 }
      );
      window.setTimeout(() => window.location.reload(), 1200);
      return;
    }
    notify(
      'Mapa travado. Use o cadeado no topo para poder editar.',
      'info',
      { timeout: 3200 }
    );
  });

  /* O servidor recusou o lote. Isto não é "não deu para salvar agora" - é
     "isto não pode ser salvo", e a diferença é a única coisa que a pessoa
     precisa saber para não ficar tentando. O motivo vem do servidor, escrito
     para ser lido, e a tela já voltou ao que existe de verdade. */
  store.on('rejected', ({ reason }) => {
    notify(
      `${reason} A última alteração não foi salva e o mapa voltou à versão do servidor.`,
      'error',
      { timeout: 9000 }
    );
  });

  store.on('conflict', () => {
    notify(
      'Este mapa foi alterado em outra aba. Carregamos a versão mais recente.',
      'warning',
      { timeout: 7000 }
    );
  });

  /* ── Camera ───────────────────────────────────────────────────────── */

  const saveViewport = debounce(() => {
    postJSON(page.dataset.viewportUrl, {
      x: camera.x,
      y: camera.y,
      zoom: camera.zoom,
    });
  }, VIEWPORT_SAVE_MS);

  camera.onChange(({ zoom }) => {
    zoomLevel.textContent = `${Math.round(zoom * 100)}%`;
    minimap.draw();
    saveViewport();
    checkIfLost();
  });

  /* Arrumar moves every topic at once. Undo covers it, but undo only helps
     someone who noticed - and on a large board the difference between "tidied"
     and "lost my arrangement" takes a moment to surface. So it is asked. */
  function confirmOrganize() {
    if (store.locked) {
      notify('Mapa travado. Use o cadeado no topo para poder editar.', 'info');
      return;
    }
    const dialog = $('#map-organize');
    if (!dialog) {
      organize(store.layout);
      return;
    }
    // The board is the authority on its own arrangement: "arrumar" can have
    // changed it since the page was rendered, and the dialog that offers to
    // change it again must open on what is actually true now.
    $$('[data-layout-picker] input', dialog).forEach((input) => {
      input.checked = input.value === store.layout;
    });
    paintMixedNote(dialog);
    openDialog(dialog);
  }

  /* A branch that was given its own arrangement does not follow the map, and
     "arrumar" moving most of the board while two branches stay put is the
     kind of thing that reads as a bug. So it is said, with the way out next
     to it. */
  function paintMixedNote(dialog) {
    const note = $('[data-mixed-note]', dialog);
    if (!note) return;
    const own = actions.ownArrangements();
    note.hidden = own === 0;
    const count = $('[data-mixed-count]', note);
    if (count) {
      count.textContent =
        own === 1
          ? '1 ramo tem disposição própria e não vai seguir esta escolha.'
          : `${own} ramos têm disposição própria e não vão seguir esta escolha.`;
    }
  }

  function chosenLayout() {
    const picked = $('[data-layout-picker] input:checked');
    return picked ? picked.value : null;
  }

  function fit() {
    const box = renderer.bounds();
    if (box) camera.fit(box);
    checkIfLost();
  }

  /* ── Losing the map ───────────────────────────────────────────────── */

  // How much of the board has to remain on screen before it counts as still
  // being there. A tenth is enough to steer back by; below that the surface
  // reads as empty and the way home has to be offered.
  const LOST_RATIO = 0.1;

  function checkIfLost() {
    if (!lostButton) return;
    const box = renderer.bounds();
    if (!box) {
      lostButton.hidden = true;
      return;
    }
    const view = stage.getBoundingClientRect();
    const left = box.x * camera.zoom + camera.x;
    const top = box.y * camera.zoom + camera.y;
    const width = box.width * camera.zoom;
    const height = box.height * camera.zoom;

    const overlapX = Math.max(0, Math.min(left + width, view.width) - Math.max(left, 0));
    const overlapY = Math.max(0, Math.min(top + height, view.height) - Math.max(top, 0));
    // Against the visible part of the board, not against its whole area: on a
    // map far larger than the screen, a tenth of it would never be on screen
    // and the button would never go away.
    const reachable = Math.min(width, view.width) * Math.min(height, view.height);
    const visible = overlapX * overlapY;

    lostButton.hidden = reachable > 0 && visible / reachable > LOST_RATIO;
  }

  /** Bring a node into view, moving the camera only if it is off screen. */
  function reveal(uuid) {
    const node = store.get(uuid);
    if (!node) return;
    const view = stage.getBoundingClientRect();
    const screenX = node.x * camera.zoom + camera.x;
    const screenY = node.y * camera.zoom + camera.y;
    const margin = 80;
    if (
      screenX > margin && screenY > margin &&
      screenX + node.width * camera.zoom < view.width - margin &&
      screenY + node.height * camera.zoom < view.height - margin
    ) {
      return;
    }
    camera.moveTo(
      view.width / 2 - (node.x + node.width / 2) * camera.zoom,
      view.height / 2 - (node.y + node.height / 2) * camera.zoom
    );
  }

  /** The middle of the board, in world coordinates - where a loose node goes
   *  when it was asked for with the keyboard rather than the pointer. */
  function centrePlacement() {
    const view = stage.getBoundingClientRect();
    const centre = camera.toWorld(view.left + view.width / 2, view.top + view.height / 2);
    return { x: centre.x - 90, y: centre.y - 24 };
  }

  async function organize(layout) {
    notify('Arrumando o mapa…', 'info', { timeout: 1500 });
    const ok = await store.organize(layout || null);
    if (!ok) {
      notify('Não foi possível arrumar o mapa agora.', 'error');
      return;
    }
    // The settings dialog holds the same choice. Left on the old value it
    // would offer to change the map back to what it no longer is - and a
    // Salvar there would actually do it.
    const select = $('#map-settings select[name="layout"]');
    if (select && layout) select.value = layout;
    // The graph came back with new coordinates; frame it so the change is
    // visible rather than happening somewhere off screen.
    window.requestAnimationFrame(() => {
      renderer.render();
      renderer.measure();
      fit();
    });
  }

  function refreshHistoryButtons() {
    const undoButton = $('[data-action="mm-undo"]', page);
    const redoButton = $('[data-action="mm-redo"]', page);
    // O `||` e não só a pilha: travado, a pilha está vazia de qualquer jeito,
    // mas ela ficaria com conteúdo se alguém travasse o mapa noutro lugar
    // durante esta sessão - e aí os botões voltariam a acender sozinhos.
    if (undoButton) undoButton.disabled = store.locked || !store.canUndo;
    if (redoButton) redoButton.disabled = store.locked || !store.canRedo;
  }

  /* ── Nome e cor do mapa ───────────────────────────────────────────── */

  /*
   * A paleta do diálogo e o seletor de cor são um controle só, e o seletor é
   * quem guarda o valor: ele é o campo do formulário, ele é o que chega ao
   * servidor, e ele é o que continua funcionando com o JavaScript fora do ar.
   * As oito cores são um atalho para ele - nunca uma segunda fonte da verdade,
   * que é como um formulário acaba enviando uma cor diferente da que está
   * marcada na tela.
   */
  const mapColor = $('#map-settings .color-input', page);

  function paintMapSwatches() {
    if (!mapColor) return;
    const current = (mapColor.value || '').toLowerCase();
    $$('[data-map-swatch]', page).forEach((swatch) => {
      const mine = swatch.dataset.mapSwatch.toLowerCase() === current;
      swatch.setAttribute('aria-pressed', mine ? 'true' : 'false');
    });
  }

  if (mapColor) {
    mapColor.addEventListener('input', paintMapSwatches);
    paintMapSwatches();
  }

  /* ── Toolbar ──────────────────────────────────────────────────────── */

  page.addEventListener('click', (event) => {
    const swatch = event.target.closest('[data-map-swatch]');
    if (swatch && mapColor) {
      mapColor.value = swatch.dataset.mapSwatch;
      paintMapSwatches();
      return;
    }

    const toolButton = event.target.closest('[data-tool-button]');
    if (toolButton) {
      interactions.setTool(toolButton.dataset.toolButton);
      return;
    }

    const button = event.target.closest('[data-action]');
    if (!button) return;

    switch (button.dataset.action) {
      case 'mm-add-topic': {
        const created = actions.addLoose(centrePlacement());
        if (created) interactions.beginEdit(created.uuid, { fresh: true });
        break;
      }
      case 'mm-add-note': {
        const created = actions.addNote(centrePlacement());
        if (created) interactions.beginEdit(created.uuid, { fresh: true });
        break;
      }
      case 'mm-organize':
        confirmOrganize();
        break;
      case 'mm-clear-branch-layouts': {
        const cleared = actions.clearBranchLayouts();
        notify(
          cleared === 1
            ? '1 ramo voltou a seguir o mapa.'
            : `${cleared} ramos voltaram a seguir o mapa.`,
          'info'
        );
        paintMixedNote($('#map-organize'));
        break;
      }
      case 'mm-organize-confirm': {
        // Read before closing: a closed dialog's inputs are still in the DOM,
        // but reading first is what keeps that from being something anyone
        // has to know.
        const layout = chosenLayout();
        closeDialog($('#map-organize'));
        organize(layout);
        break;
      }
      case 'mm-undo':
        store.undo();
        break;
      case 'mm-redo':
        store.redo();
        break;
      case 'mm-zoom-in':
        camera.zoomTo(camera.zoom * ZOOM_STEP);
        break;
      case 'mm-zoom-out':
        camera.zoomTo(camera.zoom / ZOOM_STEP);
        break;
      case 'mm-fit':
        fit();
        break;
      default:
        break;
    }
  });

  if (zoomLevel) zoomLevel.addEventListener('click', () => camera.zoomTo(1));

  /* ── Leaving the page ─────────────────────────────────────────────── */

  // `pagehide` rather than `beforeunload`: it fires when a tab is discarded or
  // put into the back/forward cache, which `beforeunload` does not, and it
  // does not block the navigation. `keepalive` is what lets the request
  // outlive the page.
  window.addEventListener('pagehide', () => {
    if (store.hasPendingChanges()) store.flush({ keepalive: true });
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && store.hasPendingChanges()) {
      store.flush({ keepalive: true });
    }
  });

  window.addEventListener('resize', debounce(() => minimap.draw(), 150));

  /* ── Start ────────────────────────────────────────────────────────── */

  store.adopt(graph);
  renderer.render();
  renderer.measure();

  const saved = graph.viewport || {};
  if (saved.zoom && (saved.x || saved.y)) {
    camera.moveTo(saved.x, saved.y, saved.zoom);
  } else {
    fit();
  }
  camera.write();

  const first = store.roots()[0];
  if (first) selection.only(first.uuid);
  inspector.refresh();
  minimap.draw();
  refreshHistoryButtons();
  checkIfLost();
  stage.focus({ preventScroll: true });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
