import { createContext, useCallback, useContext, useRef, useState } from 'react';
import type { PropsWithChildren } from 'react';
import type { Book, Library, LessonPage } from './types';
import { BookPageCache } from './book-cache';
import { cleanupResourcePacks, listResourcePacks, packageLibrary, resourceJson } from './resource-packs';

export async function fetchJson(path: string): Promise<unknown> {
  if (path.startsWith('razpack:')) return resourceJson(path);
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(new URL(path, document.baseURI), { signal: controller.signal });
    if (!response.ok) throw new Error(`Content unavailable: ${response.status}`);
    return await response.json();
  } finally { window.clearTimeout(timeout); }
}

export async function loadLibrary(): Promise<Library> {
  const data = await fetchJson('content/manifest.json') as Library;
  if (!['raz-reader-content/1.0', 'raz-reader-content/2.0'].includes(data.schemaVersion) ||
      !Array.isArray(data.levels) || !Array.isArray(data.books) || data.books.some(b => !b.pageIds?.length)) {
    throw new Error('Unsupported reader content');
  }
  await cleanupResourcePacks();
  const base = { ...data, pages: data.pages ?? {} };
  const packs = await listResourcePacks();
  return packs.length ? packageLibrary(base, packs) : base;
}

interface ContentContextValue {
  library: Library;
  loadBook: (bookId: string) => Promise<void>;
  retainBooks: (bookIds: string[]) => () => void;
}
const ContentContext = createContext<ContentContextValue | null>(null);

export function ContentProvider({ initial, children }: PropsWithChildren<{ initial: Library }>) {
  const [cache] = useState(() => new BookPageCache(initial.books, initial.pages));
  const [library, setLibrary] = useState(() => ({ ...initial, pages: cache.pages }));
  const pending = useRef(new Map<string, Promise<void>>());
  const retainBooks = useCallback((bookIds: string[]) => {
    const release = cache.retain(bookIds);
    return () => {
      if (release()) setLibrary(current => ({ ...current, pages: cache.pages }));
    };
  }, [cache]);
  const loadBook = useCallback((bookId: string): Promise<void> => {
    const book = initial.books.find(b => b.id === bookId);
    if (!book) return Promise.reject(new Error('Unknown book'));
    if (cache.has(book)) return Promise.resolve();
    const existing = pending.current.get(bookId);
    if (existing) return existing;
    const task = (async () => {
      if (!book.contentPath) throw new Error('Missing book content path');
      const data = await fetchJson(book.contentPath) as Book & {
        schemaVersion: string; contentVersion: string; pages: Record<string, LessonPage>;
      };
      if (data.schemaVersion !== 'raz-reader-book/1.0' || data.id !== book.id ||
          data.contentVersion !== (book.contentVersion ?? initial.contentVersion.split('.')[0]) || !data.pages ||
          Object.keys(data.pages).length !== book.pageIds.length || book.pageIds.some(id => {
            const page = data.pages[id];
            return !page || page.id !== id || page.bookId !== book.id || !page.paper ||
              !Array.isArray(page.sourceUnits) || !Array.isArray(page.blocks) || !Array.isArray(page.audio);
          })) throw new Error('Book content does not match shelf');
      if (book.resourceRoot) {
        for (const page of Object.values(data.pages)) {
          page.paper = { ...page.paper, image: book.resourceRoot + page.paper.image };
          page.audio = page.audio.map(track => ({ ...track, src: book.resourceRoot + track.src }));
        }
      }
      cache.add(book, data.pages);
      setLibrary(current => ({ ...current, pages: cache.pages }));
    })().finally(() => pending.current.delete(bookId));
    pending.current.set(bookId, task);
    return task;
  }, [initial, cache]);
  return <ContentContext.Provider value={{ library, loadBook, retainBooks }}>{children}</ContentContext.Provider>;
}

export function useContent() {
  const value = useContext(ContentContext);
  if (!value) throw new Error('Missing content provider');
  return value;
}
