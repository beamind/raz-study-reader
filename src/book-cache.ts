import type { Book, LessonPage } from './types';

export const BOOK_CACHE_LIMIT = 8;

/** LRU cache of reloadable books. Active readers and practice rounds hold leases. */
export class BookPageCache {
  private pageData: Record<string, LessonPage>;
  private entries = new Map<string, readonly string[]>();
  private leases = new Map<string, number>();

  constructor(books: Book[], pages: Record<string, LessonPage>, private limit = BOOK_CACHE_LIMIT) {
    this.pageData = { ...pages };
    for (const book of books) {
      // Older bundles can embed pages without a path from which to reload them.
      if (book.contentPath && book.pageIds.every(id => pages[id])) this.entries.set(book.id, book.pageIds);
    }
    this.trim();
  }

  get pages() { return this.pageData; }

  has(book: Book): boolean {
    if (!book.pageIds.every(id => this.pageData[id])) return false;
    this.touch(book.id);
    return true;
  }

  add(book: Book, pages: Record<string, LessonPage>): void {
    this.pageData = { ...this.pageData, ...pages };
    this.entries.delete(book.id);
    this.entries.set(book.id, book.pageIds);
    this.trim();
  }

  retain(bookIds: string[]): () => boolean {
    const ids = [...new Set(bookIds)];
    for (const id of ids) {
      this.leases.set(id, (this.leases.get(id) ?? 0) + 1);
      this.touch(id);
    }
    let released = false;
    return () => {
      if (released) return false;
      released = true;
      for (const id of ids) {
        const count = this.leases.get(id)! - 1;
        if (count) this.leases.set(id, count);
        else this.leases.delete(id);
      }
      return this.trim();
    };
  }

  private touch(id: string): void {
    const pages = this.entries.get(id);
    if (!pages) return;
    this.entries.delete(id);
    this.entries.set(id, pages);
  }

  private trim(): boolean {
    let pages: Record<string, LessonPage> | undefined;
    for (const [id, pageIds] of this.entries) {
      if (this.entries.size <= this.limit) break;
      if (this.leases.has(id)) continue;
      pages ??= { ...this.pageData };
      for (const pageId of pageIds) delete pages[pageId];
      this.entries.delete(id);
    }
    // Do not mutate snapshots already used by React. If every excess book is in
    // use, allow it temporarily; releasing its lease trims the cache again.
    if (pages) this.pageData = pages;
    return !!pages;
  }
}
