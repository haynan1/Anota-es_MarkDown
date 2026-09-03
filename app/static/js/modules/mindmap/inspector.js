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
  const branchForm = $('[data-branch-form]', root);
  const mirrorForm = $('[data-mirror-form]', root);
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
    branchParent: $('[data-branch-parent]', root),
    mirrorName: $('[data-mirror-name]', root),
    branchChild: $('[data-branch-child]', root),
  };

  /* The child end of the selected parent-child line. The child names the
     line, because the line *is* the child's parent field - there is nothing
     else to point at. */
  let selectedBranch = null;

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

    const branch = selectedBranch ? store.get(selectedBranch) : null;
    if (branch && branch.parent) {
      branchForm.hidden = false;
      form.hidden = true;
      emptyMessage.hidden = true;
      fields.branchParent.textContent = label(store.get(branch.parent));
      fields.branchChild.textContent = label(branch);
      return;
    }
    branchForm.hidden = true;

    // Um espelho não tem campos próprios: eles são do original.
    if (node && node.mirror_of) {
      mirrorForm.hidden = false;
      form.hidden = true;
      emptyMessage.hidden = true;
      fields.mirrorName.textContent = label(store.original(node));
      return;
    }
    mirrorForm.hidden = true;

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

  function label(node) {
    return (node && node.text) || 'tópico sem título';
  }

  /** Remove the selected connection. Answers whether it did, so the Delete
   *  key can fall through to the selected topics when it did not. */
  function removeSelectedLink() {
    return selectedBranch ? detachSelectedBranch() : false;
  }

  function detachSelectedBranch() {
    const uuid = selectedBranch;
    if (!uuid || !actions.detach(uuid)) return false;
    selectedBranch = null;
    // The freed topic takes the selection: the line the pointer was on is
    // gone, and leaving nothing selected would read as "something happened,
    // somewhere".
    selection.only(uuid);
    notify('Desconectado. Agora é um tópico solto.', 'info');
    refresh();
    return true;
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
      case 'mm-attach':
        if (uuid) context.onAttachTo(uuid);
        break;
      case 'mm-share':
        if (uuid) context.onShare(uuid);
        break;
      case 'mm-goto-original': {
        const node = uuid ? store.get(uuid) : null;
        const original = node && store.original(node);
        if (original && original !== node) {
          selection.only(original.uuid);
          context.onReveal(original.uuid);
        }
        break;
      }
      case 'mm-delete':
        actions.removeSelection();
        break;
      case 'mm-detach': {
        // Reachable from the line and from the topic's own panel: a line is
        // not focusable, so without the button in the panel this is a change
        // only a pointer can make.
        const target = selectedBranch || uuid;
        if (target && actions.detach(target)) {
          selectedBranch = null;
          selection.only(target);
          notify('Desconectado. Agora é um tópico solto.', 'info');
          refresh();
        }
        break;
      }
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

  /**
   * The outline, and the one place the map's *shape* can be repaired.
   *
   * It used to be a mirror nobody could touch: it showed that a branch had
   * come loose and offered nothing to do about it. But structure is what this
   * panel is - a list of rows at depths - and moving a row between depths is
   * the oldest gesture an outline has. It is also the only structural editing
   * the map has that works without a pointer: the canvas is a figure, and a
   * figure is unreachable by keyboard.
   *
   * Alt and the arrows rather than Tab and Shift+Tab, familiar as those are
   * from every writing outliner: these rows are buttons in a panel, and taking
   * Tab from them would trap the focus in the list with no way out.
   */
  function renderOutline() {
    if (outlinePanel.hidden) return;

    // Which row the keyboard is on, so a redraw does not drop it. Every
    // structural change redraws, so without this the second Alt+Right of a
    // pair would land on nothing.
    const focused = document.activeElement;
    const keepFocus =
      focused && outlineHost.contains(focused) ? focused.dataset.outlineUuid : null;

    const list = document.createElement('ul');
    list.className = 'mm-outline-list';
    list.setAttribute('role', 'tree');

    const walk = (node, depth) => {
      const item = document.createElement('li');
      item.setAttribute('role', 'none');

      /* Um `div`, e não um `button`.
         Um botão engole o mousedown que iniciaria o arraste, e é por isso que
         arrastar uma linha desta lista não fazia nada: o `draggable` estava
         certo e nunca chegava a começar. O papel de item de árvore e o
         tabindex devolvem tudo que o botão dava - foco, teclado, leitor de
         tela - sem o comportamento nativo que estava no caminho. */
      const row = document.createElement('div');
      row.className = 'mm-outline-item';
      row.setAttribute('role', 'treeitem');
      row.setAttribute('aria-level', String(depth + 1));
      row.tabIndex = 0;
      row.dataset.outlineUuid = node.uuid;
      row.style.setProperty('--mm-outline-depth', String(depth));
      if (selection.primary === node.uuid) row.setAttribute('aria-current', 'true');

      const shown = store.original(node);
      const mirror = shown !== node;
      // O ramo é do original: aqui um espelho é sempre uma folha, e o que ele
      // mostra é o nome de lá.
      const kids = mirror ? [] : store.children(node.uuid);
      const folder = kids.length > 0;
      row.dataset.kind = mirror ? 'mirror' : folder ? 'folder' : 'leaf';
      if (folder) row.setAttribute('aria-expanded', node.collapsed ? 'false' : 'true');

      /* Um tópico que contém outros é uma pasta, e é dobrável.
         Uma lista de duzentos tópicos sempre aberta é uma lista que ninguém
         lê - e o painel que mostra a estrutura é justamente onde se quer
         fechar um nível para enxergar o de cima. */
      const twisty = document.createElement('span');
      twisty.className = 'mm-outline-twisty';
      twisty.dataset.outlineTwisty = '';
      twisty.setAttribute('aria-hidden', 'true');
      if (folder) twisty.appendChild(icon(node.collapsed ? 'chevron-right' : 'chevron-down'));

      const glyph = document.createElement('span');
      glyph.className = 'mm-outline-glyph';
      glyph.setAttribute('aria-hidden', 'true');
      glyph.appendChild(icon(mirror ? 'copy' : folder ? 'folder' : 'file'));
      if (shown.color) glyph.style.setProperty('--mm-node-color', shown.color);

      const text = document.createElement('span');
      text.className = 'mm-outline-text';
      text.textContent = shown.text || 'Sem título';

      row.append(twisty, glyph, text);

      if (folder) {
        const count = document.createElement('span');
        count.className = 'mm-outline-count tabular';
        count.textContent = String(kids.length);
        row.appendChild(count);
      }

      item.appendChild(row);
      list.appendChild(item);

      // Um ramo fechado esconde o que está dentro dele aqui também: a
      // Estrutura e a tela mostram o mesmo mapa, ou uma das duas está
      // mentindo sobre ele.
      if (depth < 24 && !node.collapsed) {
        kids.forEach((child) => walk(child, depth + 1));
      }
    };

    store.roots().forEach((root_) => walk(root_, 0));

    if (!list.childElementCount) {
      // Um painel em branco não diz se o mapa está vazio ou se o painel
      // quebrou. Todo estado é desenhado, inclusive este.
      const empty = document.createElement('p');
      empty.className = 'mm-outline-empty';
      empty.append(
        icon('mindmap'),
        document.createTextNode('Ainda não há tópicos. O primeiro nasce com um '),
      );
      const key = document.createElement('kbd');
      key.textContent = 'Tab';
      empty.append(key, document.createTextNode(' na tela.'));
      outlineHost.replaceChildren(empty);
      return;
    }

    outlineHost.replaceChildren(list);

    if (keepFocus) {
      const again = rowFor(keepFocus);
      if (again) again.focus();
    }
  }

  /** The row for a topic. Escaped, because a selector assembled from a value
   *  is a selector that can be broken by one - and "it cannot happen, the
   *  server validates the shape three layers away" is exactly the reasoning
   *  that stops being true after a refactor nobody connected to this line. */
  function rowFor(uuid) {
    return outlineHost.querySelector(`[data-outline-uuid="${CSS.escape(uuid)}"]`);
  }

  const NS_SVG = 'http://www.w3.org/2000/svg';

  /** Um ícone do sprite, como o resto da tela monta. */
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

  /** Every row of the outline, top to bottom, as it is drawn. */
  function outlineRows() {
    return [...outlineHost.querySelectorAll('[data-outline-uuid]')];
  }

  function focusRow(uuid) {
    const row = rowFor(uuid);
    if (row) row.focus();
  }

  outlineHost.addEventListener('click', (event) => {
    const row = event.target.closest('[data-outline-uuid]');
    if (!row) return;
    const uuid = row.dataset.outlineUuid;

    if (event.target.closest('[data-outline-twisty]')) {
      actions.toggleCollapse(uuid);
      renderOutline();
      focusRow(uuid);
      return;
    }

    selection.only(uuid);
    context.onReveal(uuid);
    // Explicitamente: um clique que deixa o foco para trás deixa o caminho do
    // teclado inalcançável para quem começou pelo ponteiro, que é todo mundo.
    row.focus();
  });

  outlineHost.addEventListener('keydown', (event) => {
    const row = event.target.closest('[data-outline-uuid]');
    if (!row) return;
    const uuid = row.dataset.outlineUuid;

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selection.only(uuid);
      context.onReveal(uuid);
      return;
    }

    // As horizontais abrem e fecham o ramo - o que uma árvore de arquivos faz,
    // e o que a seta ao lado do nome promete. Ler, não mudar.
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      const node = store.get(uuid);
      if (!node || !store.children(uuid).length) return;
      const shut = event.key === 'ArrowLeft';
      if (node.collapsed === shut) return;
      event.preventDefault();
      actions.toggleCollapse(uuid);
      renderOutline();
      focusRow(uuid);
      return;
    }

    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const rows = outlineRows();
      const index = rows.indexOf(row);
      const next = rows[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (!next) return;
      event.preventDefault();
      next.focus();
    }
  });

  return {
    refresh,
    renderOutline,
    openInspector,
    selectBranch(uuid) {
      selectedBranch = uuid;
      openInspector();
      refresh();
    },
    clearLink() {
      selectedBranch = null;
    },
    removeSelectedLink,
    get branch() { return selectedBranch; },
  };
}
