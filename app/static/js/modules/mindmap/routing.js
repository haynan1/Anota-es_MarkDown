/**
 * Where a connection goes - the browser's half of one piece of arithmetic.
 *
 * The server owns this geometry: `app/services/mind_map_layout.py` computes
 * the same paths for the SVG export, so a printed map and the board it was
 * printed from are the same drawing. The browser needs its own copy anyway,
 * because a link is redrawn on every frame of a drag and a round trip per
 * frame is not something anyone would ship.
 *
 * Two implementations of one truth is a drift risk, so it is pinned rather
 * than trusted: `tests/js/mindmap-routing.test.mjs` runs a table of cases
 * through this module and compares every string, character for character,
 * against what the Python produced for the same input.
 *
 * The shape of a connection is not decoration on top of the layout - it is
 * part of the same decision. A top-down map drawn with the sideways curve of
 * a horizontal one reads as a map that grew the wrong way: branches leaving
 * the left and right faces of a node whose children are underneath it.
 */

/** Which layouts run down the page rather than across it. */
export const VERTICAL_LAYOUTS = new Set(['down', 'tree']);

const BRANCH_ROUTING = {
  right: 'horizontal',
  down: 'vertical',
  tree: 'elbow',
  radial: 'spoke',
};

/** The radius of an elbow's shoulder. Below a whole pixel of run the corner
 *  is drawn square: an arc shorter than the line it rounds is a wobble. */
const ELBOW_RADIUS = 14;
/** How far a curve leans out of the face it leaves, at minimum. Without it a
 *  short link is a flat S that reads as a straight line arriving crooked. */
const CURVE_REACH = 24;

/**
 * How `layout` draws a parent-child connection. An unknown name - a map saved
 * before a layout existed - falls back to the horizontal curve rather than
 * throwing: a board that draws slightly wrong is recoverable, one that
 * refuses to draw is not.
 */
export function routingFor(layout) {
  return BRANCH_ROUTING[layout] || 'horizontal';
}

export function isVertical(layout) {
  return VERTICAL_LAYOUTS.has(layout);
}

/**
 * One decimal, rounded half away from zero, and never `-0.0`.
 *
 * `toFixed` rounds halves away from zero and Python rounds them to even, so
 * 0.25 would be "0.3" here and "0.2" there - one character of difference, on
 * a value nobody could see, that would break the test holding the two
 * implementations together. So the rounding happens here, not in the
 * formatter.
 */
function n(value) {
  let rounded = Math.floor(Math.abs(value) * 10 + 0.5) / 10;
  if (value < 0) rounded = -rounded;
  return (rounded + 0).toFixed(1);
}

function sign(value) {
  if (value > 0) return 1;
  return value < 0 ? -1 : 0;
}

const centreX = (box) => box.x + box.width / 2;
const centreY = (box) => box.y + box.height / 2;

/** The `d` of the line from `parent` to `child` under a given routing. */
export function branchPath(routing, parent, child) {
  if (routing === 'vertical') return verticalCurve(parent, child);
  if (routing === 'elbow') return elbow(parent, child);
  if (routing === 'spoke') return spoke(parent, child);
  return horizontalCurve(parent, child);
}

/** A cubic leaving the parent sideways and arriving the same way. */
function horizontalCurve(parent, child) {
  let x1 = parent.x + parent.width;
  let x2 = child.x;
  if (child.x + child.width < parent.x) {
    // The child sits to the left; leave from that face instead of looping the
    // line back across the parent.
    x1 = parent.x;
    x2 = child.x + child.width;
  }
  const y1 = centreY(parent);
  const y2 = centreY(child);

  const reach = Math.max(Math.abs(x2 - x1) * 0.5, CURVE_REACH);
  const lean = x2 >= x1 ? 1 : -1;
  return `M${n(x1)},${n(y1)} C${n(x1 + reach * lean)},${n(y1)} ${n(x2 - reach * lean)},${n(y2)} ${n(x2)},${n(y2)}`;
}

/** The same curve stood on end: out of the bottom face, into the top. */
function verticalCurve(parent, child) {
  let y1 = parent.y + parent.height;
  let y2 = child.y;
  if (child.y + child.height < parent.y) {
    y1 = parent.y;
    y2 = child.y + child.height;
  }
  const x1 = centreX(parent);
  const x2 = centreX(child);

  const reach = Math.max(Math.abs(y2 - y1) * 0.5, CURVE_REACH);
  const lean = y2 >= y1 ? 1 : -1;
  return `M${n(x1)},${n(y1)} C${n(x1)},${n(y1 + reach * lean)} ${n(x2)},${n(y2 - reach * lean)} ${n(x2)},${n(y2)}`;
}

/**
 * Down, across, down again - the org-chart connector.
 *
 * The turn happens exactly halfway between the two rows, and that is what
 * makes every child of one parent share a single horizontal run: a tidied
 * tree puts a whole level on one line, so the midpoints coincide and the
 * shoulders draw one bus rather than a fan of near-misses. Dragged out of
 * alignment by hand, each connector turns at its own midpoint - the shape
 * degrades into an honest elbow instead of into a knot.
 */
function elbow(parent, child) {
  let y1 = parent.y + parent.height;
  let y2 = child.y;
  if (child.y + child.height < parent.y) {
    y1 = parent.y;
    y2 = child.y + child.height;
  }
  const x1 = centreX(parent);
  const x2 = centreX(child);
  const middle = (y1 + y2) / 2;

  const across = sign(x2 - x1);
  const radius = Math.min(
    ELBOW_RADIUS,
    Math.abs(x2 - x1) / 2,
    Math.abs(middle - y1),
    Math.abs(y2 - middle)
  );
  if (across === 0 || radius < 1) {
    // Straight under the parent, or too little room to round the corner
    // honestly. Routing through the midpoint collapses to a plain vertical
    // line when the two centres agree - the common case for an only child,
    // and the one place a curve would look like a mistake.
    return `M${n(x1)},${n(y1)} L${n(x1)},${n(middle)} L${n(x2)},${n(middle)} L${n(x2)},${n(y2)}`;
  }

  const first = sign(middle - y1);
  const second = sign(y2 - middle);
  return (
    `M${n(x1)},${n(y1)} ` +
    `L${n(x1)},${n(middle - radius * first)} ` +
    `Q${n(x1)},${n(middle)} ${n(x1 + radius * across)},${n(middle)} ` +
    `L${n(x2 - radius * across)},${n(middle)} ` +
    `Q${n(x2)},${n(middle)} ${n(x2)},${n(middle + radius * second)} ` +
    `L${n(x2)},${n(y2)}`
  );
}

/**
 * A straight run between two boxes, cut at the faces it crosses.
 *
 * Radial is the one layout where a child can sit in any direction at all from
 * its parent, so the only honest answer is the line between the two centres.
 * Straight on purpose: in a fan the angle *is* the information, and a bowed
 * spoke would put a curve between a branch and the direction it means.
 */
function spoke(parent, child) {
  const start = edgePoint(parent, centreX(child), centreY(child));
  const end = edgePoint(child, centreX(parent), centreY(parent));
  return `M${n(start.x)},${n(start.y)} L${n(end.x)},${n(end.y)}`;
}

/** Where the ray from a box's centre towards a point leaves the box. */
function edgePoint(box, towardX, towardY) {
  const cx = centreX(box);
  const cy = centreY(box);
  const dx = towardX - cx;
  const dy = towardY - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };

  const scaleX = dx ? (box.width / 2) / Math.abs(dx) : Infinity;
  const scaleY = dy ? (box.height / 2) / Math.abs(dy) : Infinity;
  const reach = Math.min(scaleX, scaleY);
  return { x: cx + dx * reach, y: cy + dy * reach };
}

/**
 * The `d` of an association - the edge that is not part of the tree.
 *
 * Centre to centre whatever the layout is: a free edge says "this also has to
 * do with that", and it has no orientation of its own to respect.
 */
export function freePath(style, source, target) {
  const x1 = centreX(source);
  const y1 = centreY(source);
  const x2 = centreX(target);
  const y2 = centreY(target);
  if (style === 'line') return `M${n(x1)},${n(y1)} L${n(x2)},${n(y2)}`;

  const midX = (x1 + x2) / 2;
  const midY = (y1 + y2) / 2 - Math.abs(x2 - x1) * 0.12;
  return `M${n(x1)},${n(y1)} Q${n(midX)},${n(midY)} ${n(x2)},${n(y2)}`;
}
