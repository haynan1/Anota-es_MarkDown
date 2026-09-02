/**
 * The minimap: where you are, on a board bigger than the window.
 *
 * Drawn from the model rather than scaled down from the DOM - a second render
 * of two hundred boxes at 1/20th scale costs a handful of rectangles, while
 * cloning the board would cost a second copy of every node.
 *
 * It appears only once the map is genuinely larger than the viewport. A
 * minimap of a map that already fits on screen is decoration.
 */

const WIDTH = 200;
const HEIGHT = 130;
const NS_SVG = 'http://www.w3.org/2000/svg';

export function createMinimap({ host, canvas, store, camera, stage }) {
  let projection = null;

  function draw() {
    const bounds = worldBounds();
    if (!bounds) {
      host.hidden = true;
      return;
    }

    const view = stage.getBoundingClientRect();
    const visibleWorld = {
      width: view.width / camera.zoom,
      height: view.height / camera.zoom,
    };
    // Nothing to navigate: the board is already entirely on screen.
    if (bounds.width <= visibleWorld.width && bounds.height <= visibleWorld.height) {
      host.hidden = true;
      return;
    }
    host.hidden = false;

    const padding = 10;
    const scale = Math.min(
      (WIDTH - padding * 2) / bounds.width,
      (HEIGHT - padding * 2) / bounds.height
    );
    const offsetX = padding + (WIDTH - padding * 2 - bounds.width * scale) / 2;
    const offsetY = padding + (HEIGHT - padding * 2 - bounds.height * scale) / 2;
    projection = { scale, offsetX, offsetY, bounds };

    const fragment = document.createDocumentFragment();
    store.nodes.forEach((node) => {
      if (!store.isVisible(node)) return;
      const rect = document.createElementNS(NS_SVG, 'rect');
      rect.setAttribute('class', 'mm-minimap-node');
      rect.setAttribute('x', String(offsetX + (node.x - bounds.x) * scale));
      rect.setAttribute('y', String(offsetY + (node.y - bounds.y) * scale));
      rect.setAttribute('width', String(Math.max(node.width * scale, 2)));
      rect.setAttribute('height', String(Math.max(node.height * scale, 2)));
      rect.setAttribute('rx', '1.5');
      if (!node.parent) rect.dataset.root = 'true';
      fragment.appendChild(rect);
    });

    const viewport = document.createElementNS(NS_SVG, 'rect');
    viewport.setAttribute('class', 'mm-minimap-view');
    viewport.setAttribute('x', String(offsetX + (-camera.x / camera.zoom - bounds.x) * scale));
    viewport.setAttribute('y', String(offsetY + (-camera.y / camera.zoom - bounds.y) * scale));
    viewport.setAttribute('width', String(visibleWorld.width * scale));
    viewport.setAttribute('height', String(visibleWorld.height * scale));
    viewport.setAttribute('rx', '2');
    fragment.appendChild(viewport);

    canvas.replaceChildren(fragment);
  }

  function worldBounds() {
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

  // Clicking the minimap centres the camera there: the fastest way across a
  // large board, and the reason a minimap is worth having at all.
  host.addEventListener('click', (event) => {
    if (!projection) return;
    const box = canvas.getBoundingClientRect();
    const local = {
      x: ((event.clientX - box.left) / box.width) * WIDTH,
      y: ((event.clientY - box.top) / box.height) * HEIGHT,
    };
    const world = {
      x: projection.bounds.x + (local.x - projection.offsetX) / projection.scale,
      y: projection.bounds.y + (local.y - projection.offsetY) / projection.scale,
    };
    const view = stage.getBoundingClientRect();
    camera.moveTo(
      view.width / 2 - world.x * camera.zoom,
      view.height / 2 - world.y * camera.zoom
    );
  });

  return { draw };
}
