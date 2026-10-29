import { test, expect } from '@playwright/test';
import { BookPageCache, BOOK_CACHE_LIMIT } from '../src/book-cache';
import type { Book, LessonPage } from '../src/types';


const books: Book[] = Array.from({ length: BOOK_CACHE_LIMIT + 1 }, (_, i) => ({
  id: `fixture-${i}`, title: `Fixture ${i}`, subtitle: '', level: 'Q', category: 'Fixture',
  cover: '', contentPath: `fixture-${i}/book.json`, pageIds: [`fixture-${i}-p001`],
}));
// The cache owns page references, not lesson validation (which loadBook performs).
const pagesFor = (book: Book) => Object.fromEntries(book.pageIds.map(id => [id, { id, bookId: book.id } as LessonPage]));

test('淘汰整册页面，缓存命中刷新使用顺序且不修改旧快照', () => {
  const cache = new BookPageCache([], {}, 2);
  cache.add(books[0], pagesFor(books[0]));
  cache.add(books[1], pagesFor(books[1]));
  const snapshot = cache.pages;
  expect(cache.has(books[0])).toBe(true);
  cache.add(books[2], pagesFor(books[2]));
  expect(cache.has(books[0])).toBe(true);
  expect(cache.has(books[1])).toBe(false);
  expect(cache.has(books[2])).toBe(true);
  for (const id of books[1].pageIds) {
    expect(cache.pages[id]).toBeUndefined();
    expect(snapshot[id]).toBeDefined();
  }
  expect(new Set(Object.values(cache.pages).map(page => page.bookId)).size).toBe(2);
});

test('当前阅读与练习在异步加载前保留，多处使用同一本书需全部释放', () => {
  const cache = new BookPageCache([], {}, 1);
  const releaseReader = cache.retain([books[0].id]);
  const releasePractice = cache.retain([books[0].id, books[0].id, books[1].id]);
  // Requests may finish in either order, including after the reader exits.
  releaseReader();
  cache.add(books[1], pagesFor(books[1]));
  cache.add(books[0], pagesFor(books[0]));
  expect(cache.has(books[0])).toBe(true);
  expect(cache.has(books[1])).toBe(true);
  cache.add(books[2], pagesFor(books[2]));
  expect(cache.has(books[2])).toBe(false);
  expect(releasePractice()).toBe(true);
  expect(new Set(Object.values(cache.pages).map(page => page.bookId)).size).toBe(1);
  expect(releasePractice()).toBe(false);
  // A repeated cleanup must not release a later reader's lease.
  const releaseNext = cache.retain([books[1].id]);
  releasePractice();
  cache.add(books[1], pagesFor(books[1]));
  cache.add(books[2], pagesFor(books[2]));
  expect(cache.has(books[1])).toBe(true);
  releaseNext();
});

test('迟到的请求不能挤掉当前书，已淘汰的讲义可重新加入', () => {
  const cache = new BookPageCache([], {}, 1);
  const release = cache.retain([books[0].id]);
  cache.add(books[0], pagesFor(books[0]));
  cache.add(books[1], pagesFor(books[1]));
  expect(cache.has(books[0])).toBe(true);
  expect(cache.has(books[1])).toBe(false);
  release();
  cache.add(books[1], pagesFor(books[1]));
  expect(cache.has(books[0])).toBe(false);
  expect(cache.has(books[1])).toBe(true);
});

test('初始可重载讲义也受上限约束，兼容无法重载的旧内嵌讲义', () => {
  const embedded = { ...books[0], contentPath: undefined };
  const pages = { ...pagesFor(embedded), ...pagesFor(books[1]), ...pagesFor(books[2]) };
  const cache = new BookPageCache([embedded, books[1], books[2]], pages, 1);
  expect(cache.has(embedded)).toBe(true);
  expect(cache.has(books[1])).toBe(false);
  expect(cache.has(books[2])).toBe(true);
  expect(pages[books[1].pageIds[0]]).toBeDefined();
});
