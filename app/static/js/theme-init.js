/**
 * Applies the saved theme and text size before first paint.
 *
 * Loaded synchronously in <head> (not a module). It has to be an external
 * file rather than an inline block: the Content-Security-Policy allows no
 * inline scripts and issues no nonce, and a same-origin file satisfies
 * `script-src 'self'` while still running before the first paint — so the
 * page never renders at the wrong theme or size and then jumps.
 */
(function () {
  'use strict';

  var THEME_KEY = 'markdown-studio:theme';
  var SCALE_KEY = 'markdown-studio:font-scale';
  var BOLD_KEY = 'markdown-studio:bold-text';

  // Discrete steps, not a slider: every value is validated against this list,
  // so nothing arbitrary from storage can reach the stylesheet.
  var SCALES = [0.95, 1, 1.12, 1.25];
  var EPSILON = 0.001;

  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      // Private mode or storage disabled — fall back to the defaults.
      return null;
    }
  }

  function preferredTheme() {
    var stored = read(THEME_KEY);
    if (stored === 'light' || stored === 'dark') {
      return stored;
    }

    var meta = document.querySelector('meta[name="theme-preference"]');
    var configured = meta ? meta.getAttribute('content') : 'auto';
    if (configured === 'light' || configured === 'dark') {
      return configured;
    }

    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function allowedScale(value) {
    var scale = parseFloat(value);
    for (var index = 0; index < SCALES.length; index += 1) {
      // Float comparison always with a tolerance, never ===.
      if (Math.abs(SCALES[index] - scale) < EPSILON) {
        return SCALES[index];
      }
    }
    return 1;
  }

  var root = document.documentElement;
  root.setAttribute('data-theme', preferredTheme());

  var scale = allowedScale(read(SCALE_KEY));
  root.style.setProperty('--a11y-font-scale', String(scale));
  root.classList.toggle('a11y-text-size', Math.abs(scale - 1) >= EPSILON);

  root.classList.toggle('a11y-bold-text', read(BOLD_KEY) === 'true');

  // Shared with the module that renders the panel, so the allowlist and the
  // storage keys are defined exactly once.
  window.__markdownStudioA11y = {
    THEME_KEY: THEME_KEY,
    SCALE_KEY: SCALE_KEY,
    BOLD_KEY: BOLD_KEY,
    SCALES: SCALES,
    EPSILON: EPSILON,
    allowedScale: allowedScale,
  };
})();
