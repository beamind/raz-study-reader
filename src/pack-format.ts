import type { Book, Library, LibraryLevel } from './types';
import { validBookPractice } from './practice';

export const PACK_SCHEMA = 'raz-reader-pack/1.0';
export const MAX_PACK_BYTES = 3 * 1024 ** 3;
export const MAX_ASSET_BYTES = 64 * 1024 ** 2;
export const MAX_JSON_BYTES = 4 * 1024 ** 2;
export interface PackFile { path: string; size: number; sha256: string }
export interface ResourcePack {
  schemaVersion: typeof PACK_SCHEMA; readerVersion: number; level: LibraryLevel;
  version: string; totalBytes: number; files: PackFile[]; library: Library;
}
export interface InstalledPack { id: string; installedAt: string; manifest: ResourcePack }
export interface PackRegistry { schemaVersion: 1; packs: InstalledPack[] }
export const emptyRegistry = (): PackRegistry => ({ schemaVersion: 1, packs: [] });
export const packError = () => new Error('资源包格式不完整或不兼容，请选择阅读器生成的 .razpack 文件。');
export const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
export const validId = (value: unknown): value is string => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9-]{0,119}$/.test(value);
const canonical = (value: unknown): unknown => Array.isArray(value) ? value.map(canonical) : object(value) ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const sameJson = (a: unknown, b: unknown) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
export function validAssetPath(path: unknown, level: string): path is string {
  return typeof path === 'string' && path.length < 300 &&
    new RegExp(`^content/levels/${level}/books/[a-z0-9-]+/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\\.(?:json|webp|mp3)$`).test(path) &&
    !path.split('/').includes('_source');
}

export function validatePack(value: unknown): ResourcePack {
  if (!object(value) || value.schemaVersion !== PACK_SCHEMA || value.readerVersion !== 1 ||
      !object(value.level) || typeof value.level.id !== 'string' || !/^[A-Z]$/.test(value.level.id) ||
      typeof value.level.label !== 'string' || !Array.isArray(value.level.bookIds) ||
      typeof value.version !== 'string' || !/^[0-9a-f]{16}$/.test(value.version) ||
      !Array.isArray(value.files) || !value.files.length || value.files.length > 60000 ||
      !object(value.library) || value.library.schemaVersion !== 'raz-reader-content/2.0' ||
      value.library.bundleId !== 'raz-local-preview' || value.library.contentVersion !== value.version ||
      !Array.isArray(value.library.books) || !value.library.books.length || value.library.books.length > 1000 ||
      !Array.isArray(value.library.levels) || value.library.levels.length !== 1 ||
      !sameJson(value.library.levels[0], value.level)) throw packError();
  const files = new Set<string>();
  let total = 0;
  for (const file of value.files) {
    if (!object(file) || !validAssetPath(file.path, value.level.id) || files.has(file.path) ||
        !Number.isSafeInteger(file.size) || (file.size as number) <= 0 ||
        (file.size as number) > (file.path.endsWith('.json') ? MAX_JSON_BYTES : MAX_ASSET_BYTES) ||
        typeof file.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(file.sha256)) throw packError();
    files.add(file.path); total += file.size as number;
  }
  if (total !== value.totalBytes || total > MAX_PACK_BYTES) throw packError();
  const ids = new Set<string>(), pageIds = new Set<string>();
  for (const book of value.library.books) {
    if (!object(book) || !validId(book.id) || !book.id.startsWith(`raz-${value.level.id.toLowerCase()}-`) || ids.has(book.id) || book.level !== value.level.id ||
        typeof book.title !== 'string' || typeof book.subtitle !== 'string' || typeof book.category !== 'string' ||
        book.contentVersion !== value.version || !validAssetPath(book.contentPath, value.level.id) ||
        !book.contentPath.endsWith('/book.json') || !files.has(book.contentPath) ||
        !validAssetPath(book.cover, value.level.id) || !book.cover.endsWith('.webp') || !files.has(book.cover) ||
        !Array.isArray(book.pageIds) || !book.pageIds.length || book.pageIds.length > 1000 ||
        new Set(book.pageIds).size !== book.pageIds.length ||
        book.pageIds.some(id => !validId(id) || !id.startsWith(`${book.id}-`) || pageIds.has(id)) ||
        !validBookPractice(book as unknown as Book)) throw packError();
    ids.add(book.id); book.pageIds.forEach(id => pageIds.add(id as string));
  }
  if (ids.size !== value.level.bookIds.length || value.level.bookIds.some(id => !ids.has(id as string)) ||
      value.library.pageCount !== pageIds.size) throw packError();
  return value as unknown as ResourcePack;
}

export function validatePackBook(value: unknown, book: Book, pack: ResourcePack): void {
  if (!object(value) || value.schemaVersion !== 'raz-reader-book/1.0' || value.id !== book.id ||
      value.contentVersion !== pack.version || !object(value.pages) ||
      Object.keys(value.pages).length !== book.pageIds.length) throw packError();
  const files = new Set(pack.files.map(file => file.path));
  for (const id of book.pageIds) {
    const page = value.pages[id];
    if (!object(page) || page.id !== id || page.bookId !== book.id || !object(page.paper) ||
        !files.has(page.paper.image as string) || !String(page.paper.image).endsWith('.webp') ||
        !Number.isFinite(page.paper.width) || !Number.isFinite(page.paper.height) ||
        (page.paper.width as number) <= 0 || (page.paper.height as number) <= 0 ||
        !Array.isArray(page.paper.hotspots) || !Array.isArray(page.sourceUnits) ||
        !Array.isArray(page.blocks) || typeof page.label !== 'string' || typeof page.bookTitle !== 'string' ||
        typeof page.sha256 !== 'string' || !Array.isArray(page.audio) ||
        page.audio.some(track => !object(track) || !files.has(track.src as string) ||
          !String(track.src).endsWith('.mp3') || !Array.isArray(track.clips))) throw packError();
    const units = new Set<string>();
    for (const unit of page.sourceUnits) {
      if (!object(unit) || !validId(unit.id) || units.has(unit.id) || typeof unit.text !== 'string' || typeof unit.kind !== 'string') throw packError();
      units.add(unit.id);
    }
    if (page.paper.hotspots.some(spot => !object(spot) || !units.has(spot.unitId as string) || !Array.isArray(spot.rects) ||
      spot.rects.some(rect => !object(rect) || ['x', 'y', 'width', 'height'].some(key => !Number.isFinite(rect[key]))))) throw packError();
    if (page.blocks.some(block => !object(block) || !validId(block.id) || typeof block.kind !== 'string' ||
      typeof block.title !== 'string' || typeof block.markdown !== 'string' || !Array.isArray(block.sourceRefs) ||
      block.sourceRefs.some(id => !units.has(id as string)))) throw packError();
    if (page.audio.some(track => (track as { clips: unknown[] }).clips.some(clip => !object(clip) ||
      !units.has(clip.unitId as string) || !Number.isFinite(clip.startMs) || !Number.isFinite(clip.endMs) ||
      (clip.startMs as number) < 0 || (clip.endMs as number) <= (clip.startMs as number)))) throw packError();
  }
  // Use the catalogue's practice data for shelf/review and the same data in the
  // book for offline reading. Prevent a pack from presenting two different sets.
  if (!sameJson(value.practice, book.practice)) throw packError();
}

/** Read only the central directory and requested file slices; never buffer a ZIP. */
export class StoredZip {
  private constructor(private blob: Blob, private entries: Map<string, { offset: number; size: number }>, private start: number) {}
  get names() { return [...this.entries.keys()]; }
  static async open(blob: Blob): Promise<StoredZip> {
    if (blob.size < 22 || blob.size > MAX_PACK_BYTES + 16 * 1024 ** 2) throw packError();
    const offset = Math.max(0, blob.size - 65557);
    const tail = new DataView(await blob.slice(offset).arrayBuffer());
    let end = tail.byteLength - 22;
    while (end >= 0 && (tail.getUint32(end, true) !== 0x06054b50 || end + 22 + tail.getUint16(end + 20, true) !== tail.byteLength)) end--;
    if (end < 0 || tail.getUint16(end + 4, true) || tail.getUint16(end + 6, true)) throw packError();
    const count = tail.getUint16(end + 10, true), size = tail.getUint32(end + 12, true), start = tail.getUint32(end + 16, true);
    if (!count || count > 60001 || count !== tail.getUint16(end + 8, true) || size > 16 * 1024 ** 2 || start + size !== offset + end) throw packError();
    const bytes = new Uint8Array(await blob.slice(start, start + size).arrayBuffer()), view = new DataView(bytes.buffer);
    const entries = new Map<string, { offset: number; size: number }>();
    let cursor = 0;
    for (let index = 0; index < count; index++) {
      if (cursor + 46 > size || view.getUint32(cursor, true) !== 0x02014b50) throw packError();
      const flags = view.getUint16(cursor + 8, true), method = view.getUint16(cursor + 10, true);
      const length = view.getUint16(cursor + 28, true), extra = view.getUint16(cursor + 30, true), comment = view.getUint16(cursor + 32, true);
      const fileSize = view.getUint32(cursor + 24, true), compressed = view.getUint32(cursor + 20, true);
      const mode = view.getUint32(cursor + 38, true) >>> 16;
      if (cursor + 46 + length + extra + comment > size || flags & ~0x800 || method || fileSize !== compressed ||
          fileSize > MAX_ASSET_BYTES || view.getUint16(cursor + 34, true) || (mode & 0xf000) === 0xa000) throw packError();
      const name = new TextDecoder('utf-8', { fatal: true }).decode(bytes.slice(cursor + 46, cursor + 46 + length));
      if (entries.has(name) || (name !== 'pack.json' && !/^content\/levels\/[A-Z]\/books\/[a-zA-Z0-9_/-]+\.(json|mp3|webp)$/.test(name)) || name.includes('/_source/')) throw packError();
      entries.set(name, { offset: view.getUint32(cursor + 42, true), size: fileSize });
      cursor += 46 + length + extra + comment;
    }
    if (cursor !== size) throw packError();
    return new StoredZip(blob, entries, start);
  }

  async file(name: string): Promise<Blob> {
    const entry = this.entries.get(name);
    if (!entry || entry.offset + 30 > this.start) throw packError();
    const view = new DataView(await this.blob.slice(entry.offset, entry.offset + 30).arrayBuffer());
    if (view.getUint32(0, true) !== 0x04034b50 || view.getUint16(6, true) & ~0x800 || view.getUint16(8, true) ||
        view.getUint32(18, true) !== entry.size || view.getUint32(22, true) !== entry.size) throw packError();
    const nameLength = view.getUint16(26, true), start = entry.offset + 30 + nameLength + view.getUint16(28, true);
    if (start + entry.size > this.start || await this.blob.slice(entry.offset + 30, entry.offset + 30 + nameLength).text() !== name) throw packError();
    return this.blob.slice(start, start + entry.size);
  }
}
