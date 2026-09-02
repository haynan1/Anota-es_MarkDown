/**
 * The side panels: the inspector, the outline and the pieces that hang off
 * them - picture uploads, document links and connection properties.
 *
 * The outline is the part worth defending. A canvas is a picture, and a
 * picture is unreachable with a screen reader and awkward to scan once a map
 * outgrows the viewport. The outline is the same map rendered as a list:
 * always in sync, keyboard-navigable, and the fastest way to jump across a
 * board without hunting for a node with the pointer.
 */

import { $, $$, debounce } from '../dom.js';

const SEARCH_DEBOUNCE_MS = 220;
const TEXT_DEBOUNCE_MS = 260;

export function createInspector(context) {
  const { root, store, selection, actions, notify, uploader, searchUrl } = context;

  const panel = $('[data-inspector-panel]', root);
  const outlinePanel = $('[data-outline-panel]', root);
  const outlineHost = $('[data-outline]', root);
  const form = $('[data-inspector-form]', root);
  const edgeForm = $('[data-edge-form]', root);
  const emptyMessage = $('[data-inspector-empty]', root);

  const fields = {
    text: $('[data-inspector-text]', root),
    note: $('[data-inspector-note]', root),
    url: $('[data-inspector-url]', root),
    shape: $('[data-inspector-shape]', root),
    layout: $('[data-inspector-layout]', root),
    layoutHint: $('[data-branch-layout-hint]', root),
    imagePreview: $('[data-image-preview]', root),
    imageEmpty: $('[data-image-empty]', root),
    imageUrlRow: $('[data-image-url-row]', root),
    imageUrl: $('[data-image-url]', root),
    imageClear: $('[data-action="mm-image-clear"]', root),
    docLink: $('[data-doc-link]', root),
    docTitle: $('[data-doc-title]', root),
    docEmpty: $('[data-doc-empty]', root),
    docClear: $('[data-action="mm-doc-clear"]', root),
    edgeLabel: $('[data-edge-label]', root),
    edgeStyle: $('[data-edge-style]', root),
  };

  let selectedEdge = null;

  /* ── Panels ─────────────────────────────────────────────────────────── */

  function togglePanel(element, trigger) {
    const open = element.hidden;
    element.hidden = !open;
    $$(`[data-action="${trigger}"]`, root).forEach((button) => {
      if (button.getAttribute('aria-expanded') !== null) {
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
      }
    });
    return open;
  }

  function openInspector() {
    if (panel.hidden) togglePanel(panel, 'mm-inspector');
  }

  /* ── Inspector ──────────────────────────────────────────────────────── */

  function refresh() {
    const uuid = selection.primary;
    const node = uuid ? store.get(uuid) : null;

    if (selectedEdge && store.edges.has(selectedEdge)) {
      const edge = store.edges.get(selectedEdge);
      edgeForm.hidden = false;
      form.hidden = true;
      emptyMessage.hidden = true;
      if (document.activeElement !== fields.edgeLabel) {
        fields.edgeLabel.value = edge.label;
      }
      fields.edgeStyle.value = edge.style;
      return;
    }

    edgeForm.hidden = true;
    if (!node) {
      form.hidden = true;
      emptyMessage.hidden = false;
      return;
    }

    form.hidden = false;
    emptyMessage.hidden = true;

    // Never overwrite the field somebody is typing into; the model and the
    // input are the same value from two directions.
    if (document.activeElement !== fields.text) fields.text.value = node.text;
    if (document.activeElement !== fields.note) fields.note.value = node.note;
    if (document.activeElement !== fields.url) fields.url.value = node.url;
    fields.shape.value = node.shape;
    paintBranchLayout(node);

    $$('[data-swatch]', root).forEach((button) => {
      button.setAttribute(
        'aria-pressed',
        button.dataset.swatch === node.color ? 'true' : 'false'
      );
    });

    const image = node.kind === 'image' ? node.image : '';
    fields.imagePreview.hidden = !image;
    if (image) fields.imagePreview.src = image;
    else fields.imagePreview.removeAttribute('src');
    fields.imageEmpty.hidden = Boolean(image);
    fields.imageClear.hidden = !image;

    const linked = node.document;
    fields.docLink.hidden = !linked;
    fields.docEmpty.hidden = Boolean(linked);
    fields.docClear.hidden = !linked;
    if (linked) {
      fields.docLink.href = linked.url;
      fields.docTitle.textContent = linked.title;
    }
  }

  /**
   * The branch's own arrangement, and what it actually resolves to.
   *
   * The hint is not decoration: with "Como o mapa" selected the control shows
   * an empty answer, and on a board that mixes arrangements the empty answer
   * is ambiguous - inherited from the map, or from a branch three levels up
   * that was given one? So it says which, by name.
   */
  function paintBranchLayout(node) {
    fields.layout.value = node.layout || '';
    const resolved = store.arrangementOf(node);
    // Read off the control rather than kept in a second list here: the names
    // are the server's, and one copy of them is enough.
    const option = [...fields.layout.options].find((item) => item.value === resolved);
    const name = option ? option.textContent.trim() : resolved;
    fields.layoutHint.textContent = node.layout
      ? `Só este ramo usa ${name}.`
      : `Segue o mapa: ${name}.`;
  }

  function patch(fieldsToApply) {
    const uuid = selection.primary;
    if (!uuid) return;
    actions.update(uuid, fieldsToApply);
  }

  const patchText = debounce((value) => patch({ text: value }), TEXT_DEBOUNCE_MS);
  const patchNote = debounce((value) => patch({ note: value }), TEXT_DEBOUNCE_MS);

  fields.text.addEventListener('input', () => patchText(fields.text.value));
  fields.note.addEventListener('input', () => patchNote(fields.note.value));
  fields.shape.addEventListener('change', () => patch({ shape: fields.shape.value }));
  fields.layout.addEventListener('change', () => {
    patch({ layout: fields.layout.value });
    // The whole branch moves, and so does every line inside it. Refreshing
    // here is what puts the hint on the value that was just chosen instead of
    // on the one before it.
    refresh();
  });

  fields.url.addEventListener('change', () => {
    const value = fields.url.value.trim();
    if (value && !/^(https?:|mailto:)/i.test(value)) {
      notify('Use um endereço começando por https://, http:// ou mailto:.', 'error');
      return;
    }
    patch({ url: value });
  });

  fields.edgeLabel.addEventListener('input', debounce(() => {
    if (!selectedEdge) return;
    const edge = store.edges.get(selectedEdge);
    if (!edge) return;
    store.mutate(() => {
      edge.label = fields.edgeLabel.value;
    });
  }, TEXT_DEBOUNCE_MS));

  fields.edgeStyle.addEventListener('change', () => {
    if (!selectedEdge) return;
    const edge = store.edges.get(selectedEdge);
    if (!edge) return;
    store.mutate(() => {
      edge.style = fields.edgeStyle.value;
    });
  });

  /* ── Buttons inside the panel ───────────────────────────────────────── */

  root.addEventListener('click', (event) => {
    const swatch = event.target.closest('[data-swatch]');
    if (swatch) {
      actions.updateSelection({ color: swatch.dataset.swatch });
      refresh();
      return;
    }

    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    const uuid = selection.primary;

    switch (action) {
      case 'mm-inspector':
        togglePanel(panel, 'mm-inspector');
        break;
      case 'mm-outline':
        if (togglePanel(outlinePanel, 'mm-outline')) renderOutline();
        break;
      case 'mm-open-url': {
        const node = uuid ? store.get(uuid) : null;
        if (node && node.url) window.open(node.url, '_blank', 'noopener,noreferrer');
        break;
      }
      case 'mm-image-url':
        fields.imageUrlRow.hidden = !fields.imageUrlRow.hidden;
        if (!fields.imageUrlRow.hidden) {
          const node = uuid ? store.get(uuid) : null;
          fields.imageUrl.value = node ? node.image_url : '';
          fields.imageUrl.focus();
        }
        break;
      case 'mm-image-url-apply': {
        if (!uuid) break;
        const value = fields.imageUrl.value.trim();
        if (value && !/^https?:\/\//i.test(value)) {
          notify('O endereço da imagem precisa começar por http:// ou https://.', 'error');
          break;
        }
        actions.update(uuid, {
          image_url: value,
          image: value,
          media_uuid: '',
          kind: value ? 'image' : 'topic',
        });
        fields.imageUrlRow.hidden = true;
        refresh();
        break;
      }
      case 'mm-image-clear':
        if (!uuid) break;
        actions.update(uuid, {
          image: '', image_url: '', media_uuid: '', kind: 'topic',
        });
        refresh();
        break;
      case 'mm-upload':
        uploader.pick(uuid);
        break;
      case 'mm-doc-clear':
        if (!uuid) break;
        actions.update(uuid, { document: null, document_uuid: '' });
        refresh();
        break;
      case 'mm-add-child':
        if (uuid) context.onAddChild(uuid);
        break;
      case 'mm-connect-from':
        if (uuid) context.onConnectFrom(uuid);
        break;
      case 'mm-delete':
        actions.removeSelection();
        break;
      case 'mm-edge-delete':
        if (selectedEdge) {
          actions.disconnect(selectedEdge);
          selectedEdge = null;
          refresh();
        }
        break;
      default:
        break;
    }
  });

  /* ── Document picker ────────────────────────────────────────────────── */

  const searchInput = $('[data-doc-search]', root);
  const resultsHost = $('[data-doc-results]', root);

  const runSearch = debounce(async (term) => {
    if (term.length < 2) {
      resultsHost.replaceChildren();
      return;
    }
    const response = await fetch(`${searchUrl}?q=${encodeURIComponent(term)}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return;
    const data = await response.json();
    renderResults((data && data.results) || []);
  }, SEARCH_DEBOUNCE_MS);

  function renderResults(results) {
    resultsHost.replaceChildren();
    if (!results.length) {
      const empty = document.createElement('li');
      empty.className = 'mm-doc-result-excerpt';
      empty.textContent = 'Nenhum documento encontrado.';
      resultsHost.appendChild(empty);
      return;
    }
    results.forEach((item) => {
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mm-doc-result';

      const title = document.createElement('span');
      title.className = 'mm-doc-result-title';
      title.textContent = item.title;

      const excerpt = document.createElement('span');
      excerpt.className = 'mm-doc-result-excerpt';
      excerpt.textContent = item.excerpt || '';

      button.append(title, excerpt);
      // The result carries its own identity; nothing is looked up by index,
      // so a list that changes under the pointer cannot link the wrong file.
      button.addEventListener('click', () => {
        const uuid = selection.primary;
        if (!uuid) {
          notify('Selecione um tópico antes de vincular um documento.', 'error');
          return;
        }
        actions.update(uuid, {
          document_uuid: item.uuid,
          // The search endpoint already answers with an editor URL, so the
          // picker uses it directly rather than rebuilding one.
          document: { uuid: item.uuid, title: item.title, url: item.url },
        });
        // A topic with no words takes the name of what it points at.
        const node = store.get(uuid);
        if (node && !node.text.trim()) actions.update(uuid, { text: item.title });
        refresh();
        const dialog = document.getElementById('map-doc-picker');
        if (dialog && typeof dialog.close === 'function') dialog.close();
      });

      row.appendChild(button);
      resultsHost.appendChild(row);
    });
  }

  if (searchInput) {
    searchInput.addEventListener('input', () => runSearch(searchInput.value.trim()));
  }

  /* ── Outline ────────────────────────────────────────────────────────── */

  function renderOutline() {
    if (outlinePanel.hidden) return;

    const list = document.createElement('ul');
    list.className = 'mm-outline-list';
    list.setAttribute('role', 'tree');

    const walk = (node, depth) => {
      const row = document.createElement('li');
      row.setAttribute('role', 'none');

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mm-outline-item';
      button.setAttribute('role', 'treeitem');
      button.setAttribute('aria-level', String(depth + 1));
      button.style.setProperty('--mm-outline-depth', String(depth));
      if (selection.primary === node.uuid) button.setAttribute('aria-current', 'true');

      const dot = document.createElement('span');
      dot.className = 'mm-outline-dot';
      if (node.color) dot.style.setProperty('--mm-node-color', node.color);

      const text = document.createElement('span');
      text.className = 'mm-outline-text';
      text.textContent = node.text || 'Sem título';

      button.append(dot, text);
      button.addEventListener('click', () => {
        selection.only(node.uuid);
        context.onReveal(node.uuid);
      });

      row.appendChild(button);
      list.appendChild(row);

      if (depth < 24) {
        store.children(node.uuid).forEach((child) => walk(child, depth + 1));
      }
    };

    store.roots().forEach((root_) => walk(root_, 0));
    outlineHost.replaceChildren(list);
  }

  return {
    refresh,
    renderOutline,
    openInspector,
    selectEdge(uuid) {
      selectedEdge = uuid;
      openInspector();
      refresh();
    },
    clearEdge() {
      selectedEdge = null;
    },
    get edge() { return selectedEdge; },
  };
}
