/**
 * Pointer and keyboard: everything that turns a gesture into a command.
 *
 * The rules the whole surface is built on:
 *
 * * **The keyboard is never the second-class path.** Tab makes a subtopic,
 *   Enter makes a sibling, the arrows walk the hierarchy. A map can be built
 *   end to end without touching the pointer, which is also what makes it
 *   usable with a screen reader.
 * * **A gesture is one undo step.** Dragging twelve nodes across the board is
 *   one entry in the history, not one per animation frame.
 * * **Nothing destructive happens by accident.** Re-parenting only fires when
 *   the pointer is genuinely over another node and the drop target is lit;
 *   overlapping two boxes is not a request to restructure the map.
 */

const DRAG_THRESHOLD = 4;
const NUDGE = 16;
const NUDGE_FINE = 2;
const ZOOM_STEP = 1.2;

export function createInteractions(context) {
  const {
    page, stage, store, camera, renderer, selection, actions, hint,
    onEditStart, onEditEnd, onSelectEdge, uploader,
  } = context;

  let tool = 'select';
  let spaceHeld = false;
  let gesture = null;
  let editing = null;
  let connectSource = null;

  /* ── Small helpers ──────────────────────────────────────────────────── */

  const isTyping = () => {
    const active = document.activeElement;
    if (!active) return false;
    return (
      active.isContentEditable ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName)
    );
  };

  function setTool(next) {
    tool = next;
    stage.dataset.tool = next;
    document.querySelectorAll('[data-tool-button]').forEach((button) => {
      const on = button.dataset.toolButton === next;
      button.classList.toggle('is-on', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (next !== 'connect') clearConnect();
  }

  function showHint(message) {
    if (!hint) return;
    hint.textContent = message || '';
    hint.hidden = !message;
  }

  /** The topmost visible node under a world point, skipping a set of nodes. */
  function nodeAt(point, skip = new Set()) {
    let found = null;
    store.nodes.forEach((node) => {
      if (skip.has(node.uuid) || !store.isVisible(node)) return;
      if (
        point.x >= node.x && point.x <= node.x + node.width &&
        point.y >= node.y && point.y <= node.y + node.height
      ) {
        found = node;
      }
    });
    return found;
  }

  function anchorOf(node, side) {
    if (side === 'top' || side === 'bottom') {
      return {
        x: node.x + node.width / 2,
        y: side === 'top' ? node.y : node.y + node.height,
      };
    }
    return {
      x: side === 'left' ? node.x : node.x + node.width,
      y: node.y + node.height / 2,
    };
  }

  /* ── Text editing ───────────────────────────────────────────────────── */

  function beginEdit(uuid, { fresh = false, retry = true } = {}) {
    const element = renderer.elementFor(uuid);
    const node = store.get(uuid);
    if (!node) return;
    if (!element) {
      // A topic that was created a moment ago has no element yet: the render
      // is scheduled on the next frame. Every "create it and let them type"
      // path lands here, so waiting one frame is what makes the caret appear
      // in a brand new topic instead of nowhere.
      if (retry) window.requestAnimationFrame(() => beginEdit(uuid, { fresh, retry: false }));
      return;
    }

    commitEdit();
    const label = element.querySelector('[data-label]');
    editing = { uuid, original: node.text, label, element, fresh };

    element.dataset.editing = 'true';
    label.contentEditable = 'true';
    label.spellcheck = true;
    label.focus();

    const range = document.createRange();
    range.selectNodeContents(label);
    const cursor = window.getSelection();
    cursor.removeAllRanges();
    cursor.addRange(range);

    if (onEditStart) onEditStart(uuid);
  }

  /**
   * End the edit and keep what was typed.
   *
   * Almost every way an edit ends is a way of walking away from it - clicking
   * elsewhere, pressing Escape, opening another topic. A topic created for
   * that edit and still blank goes with it: this is where a board's blank
   * boxes come from, one abandoned gesture at a time. Enter and Tab are the
   * exception and opt out, because there the writer is continuing rather than
   * leaving, and the chain needs the topic it just made.
   */
  function commitEdit({ abandonEmpty = true } = {}) {
    if (!editing) return null;
    const { uuid, label, element, fresh } = editing;
    const value = (label.innerText || '').replace(/\s+$/, '');
    editing = null;

    label.contentEditable = 'false';
    delete element.dataset.editing;

    if (abandonEmpty && fresh && !value.trim()) {
      actions.remove([uuid]);
      if (onEditEnd) onEditEnd(uuid);
      return null;
    }

    const node = store.get(uuid);
    if (node && node.text !== value) actions.update(uuid, { text: value });
    if (onEditEnd) onEditEnd(uuid);
    return uuid;
  }

  function cancelEdit() {
    if (!editing) return;
    const { uuid, original, label, element, fresh } = editing;
    editing = null;
    label.contentEditable = 'false';
    delete element.dataset.editing;
    label.textContent = original;

    // Escaping out of a topic that was created for this very edit takes the
    // topic with it. Leaving it behind is how a board fills up with blank
    // boxes: the gesture that made it was abandoned, so the thing it made
    // should go too. An existing topic is only ever restored, never removed.
    if (fresh && !original.trim()) {
      actions.remove([uuid]);
    }

    if (onEditEnd) onEditEnd(uuid);
  }

  /* ── Connecting ─────────────────────────────────────────────────────── */

  function clearConnect() {
    if (connectSource) {
      const element = renderer.elementFor(connectSource);
      if (element) delete element.dataset.connect;
    }
    connectSource = null;
    showHint('');
  }

  function connectStep(uuid) {
    if (!connectSource) {
      connectSource = uuid;
      const element = renderer.elementFor(uuid);
      if (element) element.dataset.connect = 'true';
      showHint('Agora clique no tópico que se conecta a este.');
      return;
    }
    const source = connectSource;
    clearConnect();
    if (source !== uuid) actions.connect(source, uuid);
  }

  /* ── Pointer ────────────────────────────────────────────────────────── */

  stage.addEventListener('pointerdown', (event) => {
    if (event.button === 2) return;

    // The toolbar, the zoom and the minimap sit inside the stage so they
    // follow the board when a panel opens. They are chrome, not canvas: a
    // press on one is never a gesture on the board underneath, and without
    // this it would clear the selection and start a marquee behind them.
    if (event.target.closest('[data-chrome]')) return;

    const nodeElement = event.target.closest('.mm-node');
    const port = event.target.closest('[data-port]');
    const handle = event.target.closest('[data-handle]');
    const toggle = event.target.closest('[data-toggle]');
    const badge = event.target.closest('[data-badge-link]');
    const edgeHit = event.target.closest('[data-edge]');

    if (badge) return; // a link inside a node is a link, not a drag handle

    if (toggle && nodeElement) {
      event.preventDefault();
      actions.toggleCollapse(nodeElement.dataset.uuid);
      return;
    }

    // Panning wins over everything: it is the gesture people fall back on when
    // they are lost, and it must never be swallowed by whatever is underneath.
    if (event.button === 1 || spaceHeld || tool === 'pan') {
      startPan(event);
      return;
    }

    if (port && nodeElement) {
      startConnection(event, nodeElement.dataset.uuid, port.dataset.port);
      return;
    }

    if (handle && nodeElement) {
      startResize(event, nodeElement.dataset.uuid);
      return;
    }

    if (nodeElement) {
      const uuid = nodeElement.dataset.uuid;
      if (editing && editing.uuid === uuid) return; // clicking inside the text

      if (tool === 'connect') {
        event.preventDefault();
        connectStep(uuid);
        return;
      }

      commitEdit();
      if (event.shiftKey) {
        selection.toggle(uuid);
      } else if (!selection.has(uuid)) {
        selection.only(uuid);
      }
      startDrag(event, uuid);
      return;
    }

    if (edgeHit) {
      commitEdit();
      selection.clear();
      if (onSelectEdge) onSelectEdge(edgeHit.dataset.edge);
      return;
    }

    // Empty canvas.
    commitEdit();
    clearConnect();
    if (!event.shiftKey) selection.clear();
    startMarquee(event);
  });

  function startPan(event) {
    event.preventDefault();
    stage.setPointerCapture(event.pointerId);
    stage.dataset.panning = 'true';
    gesture = {
      kind: 'pan',
      pointerId: event.pointerId,
      lastX: event.clientX,
      lastY: event.clientY,
    };
  }

  function startDrag(event, uuid) {
    event.preventDefault();
    // No pointer capture here, deliberately. Capturing on the press retargets
    // every later event to the stage - including the `click` and `dblclick`
    // the browser synthesises - so a double click on a topic arrived as a
    // double click on the board: it never opened the editor, it created a new
    // topic underneath. The capture is taken below, once the pointer has
    // actually travelled far enough to be a drag rather than a click.
    const moving = selection.has(uuid) ? selection.list() : [uuid];
    // Dragging a parent drags its branch. Anything else would tear a map apart
    // every time someone tidied one corner of it.
    const carried = new Set();
    moving.forEach((key) => store.branch(key).forEach((child) => carried.add(child)));

    gesture = {
      kind: 'drag',
      pointerId: event.pointerId,
      uuid,
      moving: [...carried],
      skip: carried,
      origin: camera.toWorld(event.clientX, event.clientY),
      moved: false,
      first: true,
      dropTarget: null,
    };
    page.dataset.interacting = 'true';
  }

  function startResize(event, uuid) {
    event.preventDefault();
    event.stopPropagation();
    stage.setPointerCapture(event.pointerId);
    const node = store.get(uuid);
    gesture = {
      kind: 'resize',
      pointerId: event.pointerId,
      uuid,
      startWidth: node.width,
      origin: camera.toWorld(event.clientX, event.clientY),
      first: true,
    };
    page.dataset.interacting = 'true';
  }

  function startConnection(event, uuid, side) {
    event.preventDefault();
    event.stopPropagation();
    stage.setPointerCapture(event.pointerId);
    gesture = {
      kind: 'connect',
      pointerId: event.pointerId,
      uuid,
      side,
      point: camera.toWorld(event.clientX, event.clientY),
    };
    showHint('Solte sobre um tópico para conectar, ou no vazio para criar um novo.');
  }

  function startMarquee(event) {
    if (tool !== 'select') return;
    stage.setPointerCapture(event.pointerId);
    gesture = {
      kind: 'marquee',
      pointerId: event.pointerId,
      origin: camera.toWorld(event.clientX, event.clientY),
      additive: event.shiftKey,
    };
  }

  stage.addEventListener('pointermove', (event) => {
    if (!gesture || event.pointerId !== gesture.pointerId) return;

    if (gesture.kind === 'pan') {
      camera.panBy(event.clientX - gesture.lastX, event.clientY - gesture.lastY);
      gesture.lastX = event.clientX;
      gesture.lastY = event.clientY;
      return;
    }

    const point = camera.toWorld(event.clientX, event.clientY);

    if (gesture.kind === 'drag') {
      const dx = point.x - gesture.origin.x;
      const dy = point.y - gesture.origin.y;
      if (!gesture.moved && Math.hypot(dx, dy) * camera.zoom < DRAG_THRESHOLD) return;

      if (!gesture.moved) {
        // From here the gesture owns the pointer: it has to keep receiving
        // moves when it leaves the board, and there is no click left to lose.
        try {
          stage.setPointerCapture(gesture.pointerId);
        } catch (error) {
          // The pointer is already gone; the gesture ends on its own.
        }
      }
      gesture.moved = true;
      actions.moveBy(gesture.moving, dx, dy, { record: gesture.first });
      gesture.first = false;
      gesture.origin = point;

      const target = nodeAt(point, gesture.skip);
      setDropTarget(target ? target.uuid : null);
      return;
    }

    if (gesture.kind === 'resize') {
      const width = gesture.startWidth + (point.x - gesture.origin.x);
      actions.update(gesture.uuid, { width: Math.max(80, Math.min(640, width)) });
      gesture.first = false;
      return;
    }

    if (gesture.kind === 'connect') {
      const source = store.get(gesture.uuid);
      renderer.showDraft(anchorOf(source, gesture.side), point);
      const target = nodeAt(point, new Set([gesture.uuid]));
      setDropTarget(target ? target.uuid : null);
      return;
    }

    if (gesture.kind === 'marquee') {
      drawMarquee(gesture.origin, point);
    }
  });

  function setDropTarget(uuid) {
    if (gesture.dropTarget === uuid) return;
    if (gesture.dropTarget) {
      const previous = renderer.elementFor(gesture.dropTarget);
      if (previous) delete previous.dataset.drop;
    }
    gesture.dropTarget = uuid;
    if (uuid) {
      const element = renderer.elementFor(uuid);
      if (element) element.dataset.drop = 'true';
      const target = store.get(uuid);
      showHint(
        gesture.kind === 'connect'
          ? `Conectar a “${target.text || 'tópico sem título'}”.`
          : `Soltar aqui torna o tópico filho de “${target.text || 'sem título'}”.`
      );
    } else {
      showHint(gesture.kind === 'connect' ? 'Solte no vazio para criar um tópico.' : '');
    }
  }

  function drawMarquee(from, to) {
    const marquee = context.marquee;
    if (!marquee) return;
    const x1 = Math.min(from.x, to.x) * camera.zoom + camera.x;
    const y1 = Math.min(from.y, to.y) * camera.zoom + camera.y;
    marquee.hidden = false;
    marquee.style.setProperty('--mm-marquee-x', `${x1}px`);
    marquee.style.setProperty('--mm-marquee-y', `${y1}px`);
    marquee.style.setProperty('--mm-marquee-w', `${Math.abs(to.x - from.x) * camera.zoom}px`);
    marquee.style.setProperty('--mm-marquee-h', `${Math.abs(to.y - from.y) * camera.zoom}px`);
  }

  function finishGesture(event) {
    if (!gesture || (event && event.pointerId !== gesture.pointerId)) return;
    const finished = gesture;
    gesture = null;
    delete page.dataset.interacting;
    delete stage.dataset.panning;
    showHint('');

    if (finished.dropTarget) {
      const element = renderer.elementFor(finished.dropTarget);
      if (element) delete element.dataset.drop;
    }

    if (finished.kind === 'drag' && finished.moved && finished.dropTarget) {
      actions.reparent(finished.uuid, finished.dropTarget);
    }

    if (finished.kind === 'connect') {
      renderer.hideDraft();
      const point = event ? camera.toWorld(event.clientX, event.clientY) : null;
      if (finished.dropTarget) {
        actions.connect(finished.uuid, finished.dropTarget);
      } else if (point) {
        // Dragged out into open space: the gesture said "there is another
        // thought over here", so make it rather than doing nothing.
        const created = actions.addChild(finished.uuid, { x: point.x, y: point.y - 24 });
        if (created) beginEdit(created.uuid, { fresh: true });
      }
    }

    if (finished.kind === 'marquee') {
      const marquee = context.marquee;
      if (marquee && !marquee.hidden) {
        selectWithin(finished.origin, event ? camera.toWorld(event.clientX, event.clientY) : finished.origin, finished.additive);
        marquee.hidden = true;
      }
    }
  }

  function selectWithin(from, to, additive) {
    const left = Math.min(from.x, to.x);
    const right = Math.max(from.x, to.x);
    const top = Math.min(from.y, to.y);
    const bottom = Math.max(from.y, to.y);
    if (right - left < 4 && bottom - top < 4) return;

    const inside = [];
    store.nodes.forEach((node) => {
      if (!store.isVisible(node)) return;
      if (
        node.x + node.width >= left && node.x <= right &&
        node.y + node.height >= top && node.y <= bottom
      ) {
        inside.push(node.uuid);
      }
    });
    selection.replace(additive ? [...new Set([...selection.list(), ...inside])] : inside);
  }

  stage.addEventListener('pointerup', finishGesture);
  stage.addEventListener('pointercancel', finishGesture);

  stage.addEventListener('dblclick', (event) => {
    const nodeElement = event.target.closest('.mm-node');
    if (nodeElement) beginEdit(nodeElement.dataset.uuid);
    // On the board itself, two clicks do nothing: see the handler below.
  });

  // Three clicks on empty board is "a new thought, right here". Two used to be
  // enough, and two is also how a word is selected and how a topic is opened
  // for editing - so a double click that landed just off a topic left a blank
  // one behind, over and over. `dblclick` cannot carry this: it fires on the
  // second click and never again, so the third is read from `click` itself.
  stage.addEventListener('click', (event) => {
    if (event.detail !== 3) return;
    if (event.target.closest('.mm-node')) return;
    if (event.target.closest('[data-chrome]')) return;

    const point = camera.toWorld(event.clientX, event.clientY);
    const created = actions.addLoose({ x: point.x - 90, y: point.y - 24 });
    if (created) beginEdit(created.uuid, { fresh: true });
  });

  /* ── Wheel ──────────────────────────────────────────────────────────── */

  stage.addEventListener(
    'wheel',
    (event) => {
      // Ctrl+wheel is the pinch gesture on a trackpad, and the universal zoom
      // shortcut with a mouse. Plain wheel scrolls the board.
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        const factor = Math.exp(-event.deltaY * 0.002);
        camera.zoomTo(camera.zoom * factor, event.clientX, event.clientY);
        return;
      }
      event.preventDefault();
      camera.panBy(-event.deltaX, -event.deltaY);
    },
    { passive: false }
  );

  /* ── Text input inside a node ───────────────────────────────────────── */

  function handleEditingKey(event) {
    if (event.key === 'Escape') {
      cancelEdit();
      stage.focus();
      return true;
    }
    // Enter finishes the thought and starts the next one at the same level;
    // Shift+Enter is a line break inside this one. That is the rhythm every
    // outliner has, and it is what makes a map typeable at speed.
    if (event.key === 'Enter' && !event.shiftKey) {
      const uuid = commitEdit({ abandonEmpty: false });
      const created = actions.addSibling(uuid);
      if (created) beginEdit(created.uuid, { fresh: true });
      return true;
    }
    if (event.key === 'Tab') {
      const uuid = commitEdit({ abandonEmpty: false });
      const created = actions.addChild(uuid);
      if (created) beginEdit(created.uuid, { fresh: true });
      return true;
    }
    return false;
  }

  stage.addEventListener('focusout', (event) => {
    if (editing && event.target === editing.label) commitEdit();
  });

  // A node label is plain text. Pasting rich content would otherwise drop
  // markup straight into a contenteditable, which the server would strip on
  // the next save - after the writer had already seen it styled.
  stage.addEventListener('paste', (event) => {
    if (!editing) return;
    event.preventDefault();
    const text = (event.clipboardData || window.clipboardData).getData('text/plain');
    document.execCommand('insertText', false, text.replace(/\r?\n/g, '\n'));
  });

  /* ── Files dropped on the board ─────────────────────────────────────── */

  ['dragenter', 'dragover'].forEach((type) =>
    stage.addEventListener(type, (event) => {
      if (!event.dataTransfer || !event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      showHint('Solte a imagem para criar um tópico com ela.');
    })
  );

  stage.addEventListener('dragleave', () => showHint(''));

  stage.addEventListener('drop', (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    event.preventDefault();
    showHint('');
    const point = camera.toWorld(event.clientX, event.clientY);
    const target = nodeAt(point);
    uploader.place(files[0], target ? target.uuid : null, point);
  });

  /* ── Keyboard ───────────────────────────────────────────────────────── */

  /**
   * Board keys listen on the stage, not on the document.
   *
   * Tab means "new subtopic" here and "next control" everywhere else, and the
   * only honest way to have both is to scope it. The stage is focusable and
   * every node lives inside it, so this fires whether the board or one of its
   * nodes holds focus - and never while the inspector does.
   */
  stage.addEventListener('keydown', (event) => {
    if (editing) {
      if (handleEditingKey(event)) {
        event.preventDefault();
        event.stopPropagation();
      }
      return;
    }
    if (isTyping()) return;

    const primary = selection.primary;
    const control = event.ctrlKey || event.metaKey;

    if (control && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) context.redo();
      else context.undo();
      return;
    }
    if (control && event.key.toLowerCase() === 'y') {
      event.preventDefault();
      context.redo();
      return;
    }
    if (control && event.key.toLowerCase() === 's') {
      event.preventDefault();
      store.flush();
      return;
    }
    if (control && event.key.toLowerCase() === 'a') {
      event.preventDefault();
      selection.replace(
        [...store.nodes.values()].filter((node) => store.isVisible(node)).map((n) => n.uuid)
      );
      return;
    }
    if (control && event.key.toLowerCase() === 'd') {
      event.preventDefault();
      if (primary) actions.duplicate(primary);
      return;
    }
    if (control && event.shiftKey && event.key.toLowerCase() === 'o') {
      event.preventDefault();
      context.organize();
      return;
    }
    if (control) return;

    switch (event.key) {
      case 'Tab': {
        event.preventDefault();
        const parent = primary || firstRoot();
        const created = actions.addChild(parent);
        if (created) beginEdit(created.uuid, { fresh: true });
        break;
      }
      case 'Enter':
      case 'F2': {
        event.preventDefault();
        if (!primary) break;
        if (event.key === 'F2') {
          beginEdit(primary);
        } else {
          const created = actions.addSibling(primary);
          if (created) beginEdit(created.uuid, { fresh: true });
        }
        break;
      }
      case 'Delete':
      case 'Backspace':
        event.preventDefault();
        actions.removeSelection();
        break;
      case 'Escape':
        clearConnect();
        selection.clear();
        break;
      case 'ArrowUp':
      case 'ArrowDown':
      case 'ArrowLeft':
      case 'ArrowRight': {
        if (!primary) break;
        event.preventDefault();
        const direction = event.key.replace('Arrow', '').toLowerCase();
        if (event.shiftKey) {
          const step = event.altKey ? NUDGE_FINE : NUDGE;
          const dx = direction === 'left' ? -step : direction === 'right' ? step : 0;
          const dy = direction === 'up' ? -step : direction === 'down' ? step : 0;
          actions.moveBy(selection.list(), dx, dy);
        } else {
          const next = actions.neighbour(primary, direction);
          if (next) {
            selection.only(next);
            context.reveal(next);
          }
        }
        break;
      }
      case '+':
      case '=':
        camera.zoomTo(camera.zoom * ZOOM_STEP);
        break;
      case '-':
      case '_':
        camera.zoomTo(camera.zoom / ZOOM_STEP);
        break;
      case '0':
        camera.zoomTo(1);
        break;
      case 'f':
      case 'F':
        context.fit();
        break;
      case 'v':
      case 'V':
        setTool('select');
        break;
      case 'h':
      case 'H':
        setTool('pan');
        break;
      case 'c':
      case 'C':
        setTool('connect');
        break;
      case 'n':
      case 'N': {
        const created = actions.addLoose(context.centrePlacement());
        if (created) beginEdit(created.uuid, { fresh: true });
        break;
      }
      default:
        break;
    }
  });

  // Held space turns the pointer into a hand, wherever focus happens to be.
  document.addEventListener('keydown', (event) => {
    if (event.key !== ' ' || isTyping()) return;
    spaceHeld = true;
    stage.dataset.space = 'true';
    if (stage.contains(event.target)) event.preventDefault();
  });

  document.addEventListener('keyup', (event) => {
    if (event.key === ' ') {
      spaceHeld = false;
      delete stage.dataset.space;
    }
  });

  function firstRoot() {
    const roots = store.roots();
    return roots.length ? roots[0].uuid : null;
  }

  return {
    setTool,
    get tool() { return tool; },
    beginEdit,
    commitEdit,
    showHint,
    isEditing: () => Boolean(editing),
  };
}
