import { Preferences } from '@capacitor/preferences';
import type { Library, ReaderState, Progress } from './types';

export const STORAGE_KEY = 'raz-reader-v1';
export const initialState: ReaderState = { schemaVersion: 1, activeBookId: null, progress: {}, fontSize: 'standard' };

export function restoreState(value: string | null, library: Library): ReaderState {
  if (!value) return { ...initialState, progress: {} };
  try {
    const data = JSON.parse(value);
    if (!data || data.schemaVersion !== 1) return { ...initialState, progress: {} };
    const progress: Record<string, Progress> = {};
    for (const [id, raw] of Object.entries(data.progress ?? {})) {
      const item = raw as Progress | null;
      if (/^[a-zA-Z0-9][a-zA-Z0-9-]{0,119}$/.test(id) && item && typeof item.pageId === 'string' && item.pageId.length <= 200) {
        const book = library.books.find(book => book.id === id);
        progress[id] = {
          pageId: !book || book.pageIds.includes(item.pageId) ? item.pageId : book.pageIds[0],
          updatedAt: Number.isFinite(item.updatedAt) ? item.updatedAt : 0,
        };
      }
    }
    return {
      schemaVersion: 1, progress,
      activeBookId: library.books.some(b => b.id === data.activeBookId) ? data.activeBookId : null,
      fontSize: ['standard', 'large', 'larger'].includes(data.fontSize) ? data.fontSize : 'standard',
    };
  } catch { return { ...initialState, progress: {} }; }
}

export async function readState(library: Library) {
  const { value } = await Preferences.get({ key: STORAGE_KEY });
  return restoreState(value, library);
}

// Preserve write order during rapid page changes, including native asynchronous writes.
let writes = Promise.resolve();
export function writeState(state: ReaderState): Promise<void> {
  const value = JSON.stringify(state);
  writes = writes.catch(() => undefined).then(() => Preferences.set({ key: STORAGE_KEY, value }));
  return writes;
}
