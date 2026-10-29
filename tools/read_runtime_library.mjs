import { readFileSync } from 'node:fs';
import path from 'node:path';

/** Test/inspection tools may explicitly load all books; the application loads on demand. */
export function readRuntimeLibrary(contentRoot = path.resolve('content')) {
  const library = JSON.parse(readFileSync(path.join(contentRoot, 'manifest.json'), 'utf8'));
  if (library.schemaVersion === 'raz-reader-content/1.0') return library;
  const pages = {};
  for (const book of library.books) {
    const relative = book.contentPath.replace(/^content\//, '');
    const file = path.resolve(contentRoot, relative);
    if (!file.startsWith(path.resolve(contentRoot) + path.sep)) throw new Error('Book outside content root');
    const data = JSON.parse(readFileSync(file, 'utf8'));
    if (data.id !== book.id || data.contentVersion !== library.contentVersion) throw new Error('Stale book content');
    Object.assign(pages, data.pages);
  }
  return { ...library, pages };
}
