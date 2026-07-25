/**
 * Editor screen.
 *
 * Responsibilities: live preview, debounced autosave with optimistic
 * concurrency control, local draft recovery, keyboard shortcuts, view modes
 * and the metadata drawer.
 *
 * Two invariants drive the design:
 *   1. The preview is rendered by the server, so what you see is exactly what
 *      gets stored and printed - there is no second markdown implementation.
 *   2. A save response that arrives out of order is discarded, and a save
 *      whose base revision is stale is rejected rather than overwriting.
 */

import { $, $$, debounce, formatNumber, openDialog, closeDialog, postJSON } from './modules/dom.js';
import { toast } from './modules/toasts.js';
import { initToolbar, applyAction, handleTab } from './modules/toolbar.js';
import { initUploads } from './modules/uploads.js';
import { initRichCopy } from './modules/richcopy.js';
import { saveDraft, readDraft, clearDraft, pruneDrafts, diffLines } from './modules/drafts.js';

const MODE_KEY = 'markdown-studio:editor-mode';
const PREVIEW_DEBOUNCE_MS = 260;

const STATUS = {
  saved: { label: 'Salvo', state: 'saved' },
  dirty: { label: 'Alterações não salvas', state: 'dirty' },
  saving: { label: 'Salvando…', state: 'saving' },
  error: { label: 'Erro ao salvar', state: 'error' },
  conflict: { label: 'Conflito de edição', state: 'conflict' },
};

/** Per-kind upload ceilings published by the server, in bytes. */
function parseLimits(raw) {
  try {
    const parsed = JSON.parse(raw || '{}');
    return parsed && typeof parsed === 'object' ? parsed : undefined;
  } catch (error) {
    return undefined;
  }
}

function boot() {
  const page = $('#editor-page');
  if (!page) return;

  const textarea = $('[data-editor-content]');
  const titleInput = $('[data-editor-title]');
  const preview = $('[data-preview]');
  const previewPane = $('[data-preview-pane]');
  const editor = $('[data-editor]');
  const statusEl = $('[data-save-status]');
  const statusLabel = $('[data-save-label]');
  if (!textarea || !titleInput) return;

  const uuid = page.dataset.uuid;
  const autosaveUrl = page.dataset.autosaveUrl;
  const previewUrl = page.dataset.previewUrl;
  const autosaveMs = Math.max(1, parseInt(page.dataset.autosaveSeconds, 10) || 3) * 1000;

  const state = {
    revision: parseInt(page.dataset.revision, 10) || 1,
    savedTitle: titleInput.value,
    savedContent: textarea.value,
    dirty: false,
    saving: false,
    // Monotonic ticket: only the newest save response is allowed to win.
    sequence: 0,
    settled: 0,
  };

  /* ── Status ─────────────────────────────────────────────────────────── */

  function setStatus(kind) {
    const config = STATUS[kind] || STATUS.saved;
    if (statusEl) statusEl.dataset.state = config.state;
    if (statusLabel) statusLabel.textContent = config.label;
  }

  /* ── Preview ────────────────────────────────────────────────────────── */

  let previewController = null;

  const renderPreview = debounce(async () => {
    if (!preview) return;

    if (previewController) previewController.abort();
    previewController = new AbortController();
    if (previewPane) previewPane.dataset.loading = 'true';

    try {
      const { ok, data } = await postJSON(
        previewUrl,
        { content_markdown: textarea.value },
        { signal: previewController.signal }
      );

      if (ok && data && data.ok) {
        // Safe to assign: the server sanitized this HTML with the same
        // allowlist used for stored content.
        preview.innerHTML = data.html;
        updateMetrics(data);
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        setStatus('error');
      }
    } finally {
      if (previewPane) delete previewPane.dataset.loading;
    }
  }, PREVIEW_DEBOUNCE_MS);

  function updateMetrics(data) {
    const words = $('[data-metric-words]');
    const chars = $('[data-metric-chars]');
    const time = $('[data-metric-time]');
    if (words) words.textContent = formatNumber(data.word_count);
    if (chars) chars.textContent = formatNumber(data.character_count);
    if (time) time.textContent = data.reading_time;
  }

  /* ── Mirrors for the no-JS metadata form ────────────────────────────── */

  function syncMirrors() {
    const mirrorTitle = $('[data-mirror-title]');
    const mirrorContent = $('[data-mirror-content]');
    const mirrorRevision = $('[data-mirror-revision]');
    if (mirrorTitle) mirrorTitle.value = titleInput.value;
    if (mirrorContent) mirrorContent.value = textarea.value;
    if (mirrorRevision) mirrorRevision.value = String(state.revision);
  }

  /* ── Autosave ───────────────────────────────────────────────────────── */

  async function save({ silent = true } = {}) {
    if (state.saving && silent) return;

    const title = titleInput.value;
    const content = textarea.value;

    if (title === state.savedTitle && content === state.savedContent) {
      state.dirty = false;
      setStatus('saved');
      return;
    }

    const ticket = ++state.sequence;
    state.saving = true;
    setStatus('saving');

    const { ok, status, data } = await postJSON(autosaveUrl, {
      title,
      content_markdown: content,
      revision: state.revision,
      refresh_slug: !silent,
    });

    state.saving = false;

    // A newer save already landed - this response is stale, ignore it.
    if (ticket <= state.settled) return;
    state.settled = ticket;

    if (ok && data && data.ok) {
      state.revision = data.revision;
      state.savedTitle = data.title;
      state.savedContent = data.content_markdown;
      state.dirty = titleInput.value !== state.savedTitle || textarea.value !== state.savedContent;
      page.dataset.revision = String(state.revision);
      syncMirrors();
      clearDraft(uuid);
      setStatus(state.dirty ? 'dirty' : 'saved');
      if (!silent) toast('Documento salvo.', 'success');
      return;
    }

    if (status === 409) {
      setStatus('conflict');
      toast(
        (data && data.error) ||
          'Este documento foi alterado em outro lugar. Recarregue a página para ver a versão atual.',
        'error',
        { timeout: 12000 }
      );
      return;
    }

    setStatus('error');
    toast(
      (data && data.error) || 'Não foi possível salvar. Seu texto continua aqui e no rascunho local.',
      'error'
    );
  }

  const autosave = debounce(() => save({ silent: true }), autosaveMs);

  /* ── Input handling ─────────────────────────────────────────────────── */

  function onEdit() {
    state.dirty =
      titleInput.value !== state.savedTitle || textarea.value !== state.savedContent;

    if (state.dirty) {
      setStatus('dirty');
      saveDraft(uuid, {
        title: titleInput.value,
        content: textarea.value,
        revision: state.revision,
      });
      autosave();
    } else {
      autosave.cancel();
      setStatus('saved');
    }
    syncMirrors();
  }

  textarea.addEventListener('input', () => {
    onEdit();
    renderPreview();
  });
  titleInput.addEventListener('input', onEdit);

  /* ── View modes ─────────────────────────────────────────────────────── */

  function setMode(mode) {
    if (!editor) return;
    editor.dataset.mode = mode;
    $$('[data-mode]').forEach((button) =>
      button.setAttribute('aria-pressed', button.dataset.mode === mode ? 'true' : 'false')
    );
    try {
      window.localStorage.setItem(MODE_KEY, mode);
    } catch (error) {
      /* ignore */
    }
  }

  $$('[data-mode]').forEach((button) =>
    button.addEventListener('click', () => setMode(button.dataset.mode))
  );

  try {
    const storedMode = window.localStorage.getItem(MODE_KEY);
    if (['split', 'write', 'read'].includes(storedMode)) setMode(storedMode);
  } catch (error) {
    /* ignore */
  }

  /* ── Fullscreen & drawer ────────────────────────────────────────────── */

  function toggleFullscreen(force) {
    const next = force !== undefined ? force : page.dataset.fullscreen !== 'true';
    page.dataset.fullscreen = next ? 'true' : 'false';
    const button = $('[data-action="toggle-fullscreen"]');
    if (button) button.setAttribute('aria-pressed', next ? 'true' : 'false');
  }

  function toggleDrawer(force) {
    const drawer = $('#meta-drawer');
    if (!drawer) return;

    const next = force !== undefined ? force : drawer.dataset.open !== 'true';
    drawer.dataset.open = next ? 'true' : 'false';

    const backdrop = $('.drawer-backdrop');
    if (backdrop) backdrop.dataset.visible = next ? 'true' : 'false';

    // The header trigger is underneath the open panel, so its state has to be
    // announced even while it cannot be reached.
    const trigger = $('.editor-header [data-action="toggle-meta"]');
    if (trigger) trigger.setAttribute('aria-expanded', next ? 'true' : 'false');

    // Focus follows the panel, and returns to the trigger when it closes.
    if (next) {
      const first = drawer.querySelector('select, input, button');
      if (first) first.focus();
    } else if (trigger) {
      trigger.focus();
    }
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="toggle-fullscreen"]')) toggleFullscreen();
    if (event.target.closest('[data-action="toggle-meta"]')) toggleDrawer();
    if (event.target.closest('[data-action="close-meta"]')) toggleDrawer(false);
    if (event.target.closest('[data-action="save-now"]')) {
      autosave.cancel();
      save({ silent: false });
    }
  });

  /* ── Draft recovery ─────────────────────────────────────────────────── */

  function initDraftRecovery() {
    const banner = $('[data-draft-banner]');
    const draft = readDraft(uuid);
    if (!banner || !draft) return;

    const differs = draft.content !== state.savedContent || draft.title !== state.savedTitle;
    if (!differs) {
      clearDraft(uuid);
      return;
    }

    const message = $('[data-draft-message]');
    if (message) {
      const when = new Date(draft.at || Date.now()).toLocaleString('pt-BR');
      message.textContent =
        `Encontramos um rascunho local não salvo (${when}) diferente da versão no servidor.`;
    }
    banner.hidden = false;

    document.addEventListener('click', (event) => {
      const action = event.target.closest('[data-draft-action]');
      if (!action) return;

      const kind = action.getAttribute('data-draft-action');

      if (kind === 'discard') {
        clearDraft(uuid);
        banner.hidden = true;
        toast('Rascunho local descartado.', 'info');
      }

      if (kind === 'recover') {
        titleInput.value = draft.title || '';
        textarea.value = draft.content || '';
        banner.hidden = true;
        closeDialog($('#draft-dialog'));
        onEdit();
        renderPreview.flush();
        toast('Rascunho recuperado. Revise e salve.', 'success');
      }

      if (kind === 'compare') {
        renderDraftDiff(draft);
        openDialog($('#draft-dialog'));
      }
    });
  }

  function renderDraftDiff(draft) {
    const host = $('[data-draft-diff]');
    if (!host) return;
    host.textContent = '';

    diffLines(state.savedContent, draft.content).forEach((row) => {
      if (row.tag === 'equal') return;
      const line = document.createElement('div');
      line.className = 'diff-row';
      line.dataset.tag = row.tag;

      const gutter = document.createElement('span');
      gutter.className = 'diff-gutter';
      gutter.textContent = row.tag === 'insert' ? 'rascunho' : 'salvo';

      const text = document.createElement('span');
      text.className = 'diff-text';
      text.textContent = row.text || ' ';

      line.append(gutter, text);
      host.appendChild(line);
    });

    if (!host.childElementCount) {
      const note = document.createElement('p');
      note.className = 'muted small';
      note.textContent = 'Nenhuma diferença de conteúdo entre o rascunho e a versão salva.';
      host.appendChild(note);
    }
  }

  /* ── Keyboard shortcuts ─────────────────────────────────────────────── */

  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Tab') handleTab(textarea, event);
  });

  document.addEventListener('keydown', (event) => {
    const mod = event.ctrlKey || event.metaKey;

    if (event.key === 'Escape') {
      const drawer = $('#meta-drawer');
      if (drawer && drawer.dataset.open === 'true') {
        toggleDrawer(false);
        return;
      }
      if (page.dataset.fullscreen === 'true') {
        toggleFullscreen(false);
      }
      return;
    }

    if (!mod) return;

    const key = event.key.toLowerCase();
    const inEditor = document.activeElement === textarea;

    if (key === 's') {
      event.preventDefault();
      autosave.cancel();
      save({ silent: false });
      return;
    }

    if (key === 'p') {
      // Opens the export dialog instead of the browser print sheet.
      event.preventDefault();
      openDialog($('#pdf-dialog'));
      return;
    }

    if (!inEditor) return;

    if (key === 'b') {
      event.preventDefault();
      applyAction(textarea, 'bold');
    } else if (key === 'i') {
      event.preventDefault();
      applyAction(textarea, 'italic');
    } else if (key === 'k') {
      event.preventDefault();
      applyAction(textarea, 'link');
    }
  });

  /* ── Leaving the page ───────────────────────────────────────────────── */

  window.addEventListener('beforeunload', (event) => {
    if (!state.dirty) return undefined;
    // The draft is already in localStorage; this is the last-chance warning.
    event.preventDefault();
    event.returnValue = '';
    return '';
  });

  // A best-effort flush when the tab is hidden or closed.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && state.dirty && !state.saving) {
      autosave.flush();
    }
  });

  /* ── Start ──────────────────────────────────────────────────────────── */

  initToolbar(textarea, document);
  initUploads({
    textarea,
    pane: $('.editor-source'),
    documentUuid: uuid,
    input: $('[data-media-input]'),
    trayHost: page,
    accept: page.dataset.uploadAccept || '',
    limits: parseLimits(page.dataset.uploadLimits),
    // No toasts here: the tray occupies the same corner of the screen and
    // tells the whole story - name, progress, outcome, retry. A toast would
    // land on top of the thing the writer is already watching.
    onStatus: () => {},
  });
  initRichCopy({ textarea, dialog: $('#rich-copy-dialog') });
  initDraftRecovery();
  pruneDrafts();
  syncMirrors();
  setStatus('saved');
  textarea.focus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
