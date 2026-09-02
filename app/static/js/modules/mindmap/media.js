/**
 * Pictures on the board.
 *
 * The upload goes through the same endpoint and the same server-side checks as
 * an image dropped into the editor: the type is decided from the file's bytes,
 * not from its name, and the stored path is generated. Nothing here is a
 * second, weaker door into the media pipeline - it is the same door.
 *
 * The size check below is a courtesy, not a defence. It exists so a 40 MB
 * photo is refused in a tenth of a second instead of after the upload; the
 * server enforces the real ceiling regardless.
 */

import { csrfToken } from '../dom.js';

export function createUploader({ url, limit, input, store, actions, selection, notify, onDone }) {
  let pending = null;

  function humanLimit() {
    return `${Math.round(limit / (1024 * 1024))} MB`;
  }

  function validate(file) {
    if (!file) return 'Nenhum arquivo selecionado.';
    if (!file.type.startsWith('image/')) {
      return 'Um tópico aceita imagens: PNG, JPG, GIF ou WebP.';
    }
    if (limit && file.size > limit) {
      return `A imagem excede o limite de ${humanLimit()}.`;
    }
    if (file.size === 0) return 'O arquivo está vazio.';
    return null;
  }

  async function send(file) {
    const body = new FormData();
    body.append('file', file);

    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken(), Accept: 'application/json' },
      body,
    });

    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok || !data || !data.ok) {
      throw new Error((data && data.error) || 'Não foi possível enviar a imagem.');
    }
    return data;
  }

  /**
   * Put a picture on a node - the selected one, an existing one under the
   * pointer, or a brand new node created where the file was dropped.
   */
  async function place(file, targetUuid, point) {
    const problem = validate(file);
    if (problem) {
      notify(problem, 'error');
      return null;
    }

    notify('Enviando imagem…', 'info', { timeout: 2000 });
    let asset;
    try {
      asset = await send(file);
    } catch (error) {
      notify(error.message, 'error');
      return null;
    }

    let uuid = targetUuid;
    if (!uuid) {
      const created = actions.addLoose({
        kind: 'image',
        width: 240,
        x: point ? point.x - 120 : 0,
        y: point ? point.y - 80 : 0,
        text: '',
      });
      if (!created) return null;
      uuid = created.uuid;
    }

    actions.update(uuid, {
      kind: 'image',
      media_uuid: asset.uuid,
      image: asset.url,
      image_url: '',
    });
    selection.only(uuid);
    if (onDone) onDone(uuid);
    notify('Imagem adicionada ao tópico.', 'success', { timeout: 2500 });
    return uuid;
  }

  if (input) {
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      const target = pending;
      pending = null;
      // Cleared before the upload so picking the same file twice in a row
      // still fires a change event the second time.
      input.value = '';
      if (file) place(file, target, null);
    });
  }

  return {
    place,
    /** Open the file picker, remembering which node the result belongs to. */
    pick(uuid) {
      if (!input) return;
      pending = uuid || selection.primary || null;
      if (!pending && !store.nodes.size) {
        notify('Crie um tópico antes de enviar uma imagem.', 'error');
        return;
      }
      input.click();
    },
  };
}
