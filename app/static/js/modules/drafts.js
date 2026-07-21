/**
 * Local draft storage.
 *
 * A copy of the in-progress document lives in localStorage so an unexpected
 * close (crash, accidental tab close, power loss) never loses work. The copy
 * is cleared as soon as the server confirms a save.
 */

const PREFIX = 'markdown-studio:draft:';
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

function key(uuid) {
  return `${PREFIX}${uuid}`;
}

export function saveDraft(uuid, draft) {
  try {
    window.localStorage.setItem(
      key(uuid),
      JSON.stringify({ ...draft, at: Date.now() })
    );
  } catch (error) {
    // Quota exceeded or storage disabled - autosave to the server still runs.
  }
}

export function readDraft(uuid) {
  try {
    const raw = window.localStorage.getItem(key(uuid));
    if (!raw) return null;

    const draft = JSON.parse(raw);
    if (!draft || typeof draft.content !== 'string') return null;
    if (Date.now() - (draft.at || 0) > MAX_AGE_MS) {
      clearDraft(uuid);
      return null;
    }
    return draft;
  } catch (error) {
    return null;
  }
}

export function clearDraft(uuid) {
  try {
    window.localStorage.removeItem(key(uuid));
  } catch (error) {
    /* ignore */
  }
}

/** Remove drafts left behind by documents that no longer exist. */
export function pruneDrafts() {
  try {
    const stale = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const name = window.localStorage.key(index);
      if (!name || !name.startsWith(PREFIX)) continue;
      try {
        const draft = JSON.parse(window.localStorage.getItem(name));
        if (!draft || Date.now() - (draft.at || 0) > MAX_AGE_MS) stale.push(name);
      } catch (error) {
        stale.push(name);
      }
    }
    stale.forEach((name) => window.localStorage.removeItem(name));
  } catch (error) {
    /* ignore */
  }
}

/**
 * Minimal line diff for the "recover draft?" comparison.
 * Longest-common-subsequence over lines, capped so a huge document cannot
 * lock up the UI.
 */
export function diffLines(oldText, newText, limit = 400) {
  const a = (oldText || '').split('\n').slice(0, limit);
  const b = (newText || '').split('\n').slice(0, limit);

  const table = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const rows = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      rows.push({ tag: 'equal', text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      rows.push({ tag: 'delete', text: a[i] });
      i += 1;
    } else {
      rows.push({ tag: 'insert', text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    rows.push({ tag: 'delete', text: a[i] });
    i += 1;
  }
  while (j < b.length) {
    rows.push({ tag: 'insert', text: b[j] });
    j += 1;
  }

  return rows;
}
