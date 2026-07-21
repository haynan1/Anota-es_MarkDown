/**
 * Applies the saved theme before first paint.
 *
 * Loaded synchronously in <head> (not a module) so the page never renders in
 * the wrong theme and then flips.
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'markdown-studio:theme';

  function preferredTheme() {
    var stored = null;
    try {
      stored = window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      // Private mode or storage disabled - fall back to the system setting.
    }

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

  document.documentElement.setAttribute('data-theme', preferredTheme());
})();
