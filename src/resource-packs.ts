import { Capacitor, registerPlugin } from '@capacitor/core';
import type { Library } from './types';
import { MAX_JSON_BYTES, object, packError, StoredZip, validatePack, validatePackBook, validId,
  type InstalledPack, type PackRegistry } from './pack-format';

const native = registerPlugin<{
  list(): Promise<{ registry: PackRegistry; root: string }>;
  open(): Promise<{ cancelled?: boolean; manifest?: unknown; id?: string; root?: string }>;
  commit(options: { id: string }): Promise<void>;
  discard(options: { id: string }): Promise<void>;
  cleanup(): Promise<void>;
  remove(options: { level: string }): Promise<void>;
  addListener(event: 'progress', listener: (event: { message: string }) => void): Promise<{ remove(): Promise<void> }>;
}>('ReaderPacks');
const isNative = () => Capacitor.getPlatform() === 'android';
const PREFIX = 'razpack:';
let nativeRoot = '';
const VERSION_LOCK = 'raz-reader-pack-in-use-';
const heldVersions = new Set<string>();
function retainVersions(packs: InstalledPack[]): void {
  if (isNative() || !navigator.locks) return;
  for (const pack of packs) {
    if (heldVersions.has(pack.id)) continue;
    heldVersions.add(pack.id);
    // The browser releases shared version leases when this page closes. Other
    // tabs may replace the registry while this reader keeps using its version.
    void navigator.locks.request(VERSION_LOCK + pack.id, { mode: 'shared' }, () => new Promise<void>(() => {}))
      .catch(() => heldVersions.delete(pack.id));
  }
}

async function root(): Promise<FileSystemDirectoryHandle> {
  if (!navigator.storage?.getDirectory) throw new Error('此浏览器不支持本地资源包，请使用新版 Chrome 或 Edge，通过 HTTPS 或 localhost 打开阅读器。');
  return (await navigator.storage.getDirectory()).getDirectoryHandle('raz-reader-packs', { create: true });
}
async function directory(path: string[], create = false): Promise<FileSystemDirectoryHandle> {
  let dir = await root();
  for (const part of path) dir = await dir.getDirectoryHandle(part, { create });
  return dir;
}
async function storedFile(path: string): Promise<File> {
  const parts = path.split('/'), name = parts.pop()!;
  return (await (await directory(parts)).getFileHandle(name)).getFile();
}
async function writeFile(path: string, blob: Blob): Promise<void> {
  const parts = path.split('/'), name = parts.pop()!;
  const file = await (await directory(parts, true)).getFileHandle(name, { create: true });
  const writer = await file.createWritable();
  await blob.stream().pipeTo(writer);
}
function checkRegistry(value: unknown): PackRegistry {
  if (!object(value) || value.schemaVersion !== 1 || !Array.isArray(value.packs) || value.packs.length > 26) throw new Error('资源包目录损坏，原文件已保留，请重新导入资源包。');
  const levels = new Set<string>();
  for (const entry of value.packs) {
    if (!object(entry) || !validId(entry.id) || typeof entry.installedAt !== 'string') throw packError();
    const pack = validatePack(entry.manifest);
    if (levels.has(pack.level.id)) throw packError();
    levels.add(pack.level.id);
  }
  return value as unknown as PackRegistry;
}
export async function listResourcePacks(): Promise<InstalledPack[]> {
  if (isNative()) {
    const result = await native.list();
    nativeRoot = result.root;
    return checkRegistry(result.registry).packs;
  }
  if (!navigator.storage?.getDirectory) return [];
  let file: File;
  try { file = await storedFile('installed.json'); }
  catch (error) { if (error instanceof DOMException && error.name === 'NotFoundError') return []; throw error; }
  // Creating a handle for the first registry can leave an empty target if the
  // tab closes before its atomic writable stream commits. No pack was enabled.
  if (!file.size) return [];
  if (file.size > 32 * 1024 ** 2) throw packError();
  return checkRegistry(JSON.parse(await file.text())).packs;
}
async function saveRegistry(packs: InstalledPack[]): Promise<void> {
  await writeFile('installed.json', new Blob([JSON.stringify({ schemaVersion: 1, packs })]));
}
async function removeDirectory(id: string): Promise<void> {
  try { await (await root()).removeEntry(id, { recursive: true }); }
  catch (error) { if (!(error instanceof DOMException && error.name === 'NotFoundError')) throw error; }
}
// Serialize imports, replacement and removal across tabs as well as this page.
let operations: Promise<unknown> = Promise.resolve();
async function exclusive<T>(task: () => Promise<T>): Promise<T> {
  const run = async (): Promise<T> => navigator.locks ? await navigator.locks.request('raz-reader-resource-packs', task) : await task();
  const result = operations.then(run, run);
  operations = result.catch(() => undefined);
  return result;
}
export async function cleanupResourcePacks(): Promise<void> {
  if (isNative()) { await native.cleanup(); return; }
  if (!navigator.storage?.getDirectory) return;
  await exclusive(async () => {
    const active = new Set((await listResourcePacks()).map(pack => pack.id));
    if (navigator.locks) {
      const locks = await navigator.locks.query();
      for (const lock of [...locks.held ?? [], ...locks.pending ?? []]) {
        if (lock.name?.startsWith(VERSION_LOCK)) active.add(lock.name.slice(VERSION_LOCK.length));
      }
    }
    const dir = await root();
    // Version directories are immutable. Recover abandoned staging files after
    // a closed tab or failed write; never remove an installed version.
    for await (const [name, handle] of (dir as FileSystemDirectoryHandle & {
      entries(): AsyncIterableIterator<[string, FileSystemHandle]>;
    }).entries()) {
      if (handle.kind === 'directory' && validId(name) && !active.has(name)) await dir.removeEntry(name, { recursive: true });
    }
  });
}

export function packageLibrary(base: Library, packs: InstalledPack[]): Library {
  retainVersions(packs);
  let books = [...base.books], levels = [...base.levels];
  let pageCount = base.pageCount ?? Object.keys(base.pages).length;
  for (const { id, manifest: pack } of packs) {
    const levelId = pack.level.id;
    pageCount -= books.filter(book => book.level === levelId).reduce((sum, book) => sum + book.pageIds.length, 0);
    books = books.filter(book => book.level !== levelId);
    levels = levels.filter(level => level.id !== levelId);
    const prefix = `${PREFIX}${id}/`;
    books.push(...pack.library.books.map(book => ({ ...book,
      cover: prefix + book.cover, contentPath: prefix + book.contentPath,
      contentVersion: pack.version, resourceRoot: prefix,
    })));
    levels.push(pack.level); pageCount += pack.library.pageCount!;
  }
  books.sort((a, b) => a.id.localeCompare(b.id)); levels.sort((a, b) => a.id.localeCompare(b.id));
  return { ...base, books, levels, pageCount, contentVersion: [base.contentVersion, ...packs.map(pack => pack.manifest.version)].join('.') };
}

export async function importWebResourcePack(file: File, progress: (message: string) => void): Promise<void> {
  return exclusive(async () => {
    progress('正在检查资源包…');
    const archive = await StoredZip.open(file);
    const metadata = await archive.file('pack.json');
    if (metadata.size > MAX_JSON_BYTES) throw packError();
    const pack = validatePack(JSON.parse(await metadata.text()));
    const names = new Set(archive.names);
    if (names.size !== pack.files.length + 1 || pack.files.some(item => !names.has(item.path))) throw packError();
    const packs = await listResourcePacks();
    const estimate = await navigator.storage.estimate();
    if (estimate.quota && estimate.quota - (estimate.usage ?? 0) < pack.totalBytes + metadata.size + 8 * 1024 ** 2) {
      throw new Error('浏览器可用空间不足，请先释放空间或移除不用的资源包。');
    }
    const id = `${pack.level.id}-${pack.version}-${crypto.randomUUID()}`;
    let committed = false;
    try {
      let completed = 0;
      for (const entry of pack.files) {
        const blob = await archive.file(entry.path);
        if (blob.size !== entry.size) throw packError();
        const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), b => b.toString(16).padStart(2, '0')).join('');
        if (hash !== entry.sha256) throw new Error('资源包文件校验失败，原有资源未改变，请重新复制资源包。');
        const book = pack.library.books.find(book => book.contentPath === entry.path);
        if (book) validatePackBook(JSON.parse(await blob.text()), book, pack);
        await writeFile(`${id}/${entry.path}`, blob);
        completed += entry.size;
        progress(`正在导入 ${pack.level.id} 级：${Math.floor(completed / pack.totalBytes * 100)}%`);
      }
      const next = packs.filter(item => item.manifest.level.id !== pack.level.id);
      next.push({ id, installedAt: new Date().toISOString(), manifest: pack });
      await saveRegistry(next);
      committed = true;
      // Old versions are collected on the next startup, after the reader reloads.
    } finally {
      if (!committed) await removeDirectory(id).catch(() => undefined);
    }
  });
}
export async function importNativeResourcePack(progress: (message: string) => void): Promise<boolean> {
  const listener = await native.addListener('progress', event => progress(event.message));
  let id: string | undefined;
  try {
    const result = await native.open();
    if (result.cancelled) return false;
    id = result.id;
    if (!validId(id) || typeof result.root !== 'string') throw packError();
    nativeRoot = result.root;
    const pack = validatePack(result.manifest);
    for (const book of pack.library.books) {
      validatePackBook(await resourceJson(`${PREFIX}${id}/${book.contentPath}`), book, pack);
    }
    await native.commit({ id });
    id = undefined;
    return true;
  } catch (error) {
    if (id) await native.discard({ id }).catch(() => undefined);
    throw error;
  }
  finally { await listener.remove(); }
}
export async function removeResourcePack(level: string): Promise<void> {
  if (isNative()) { await native.remove({ level }); return; }
  await exclusive(async () => {
    const packs = await listResourcePacks();
    await saveRegistry(packs.filter(pack => pack.manifest.level.id !== level));
  });
}

function localPath(path: string): string | null {
  if (!path.startsWith(PREFIX)) return null;
  const relative = path.slice(PREFIX.length), id = relative.split('/')[0];
  if (!validId(id) || !/^content\/levels\/[A-Z]\/books\/[a-zA-Z0-9_/-]+\.(json|webp|mp3)$/.test(relative.slice(id.length + 1))) throw packError();
  return relative;
}
export async function resourceJson(path: string): Promise<unknown | undefined> {
  const relative = localPath(path);
  if (!relative) return undefined;
  if (!isNative()) {
    const file = await storedFile(relative);
    if (file.size > MAX_JSON_BYTES) throw packError();
    return JSON.parse(await file.text());
  }
  const response = await fetch(Capacitor.convertFileSrc(`${nativeRoot}/${relative}`));
  if (!response.ok) throw new Error('本地资源无法打开，请重新导入对应级别。');
  return response.json();
}
export interface AssetLease { url: string; release(): void }
export async function openResourceAsset(path: string): Promise<AssetLease> {
  const relative = localPath(path);
  if (!relative) return { url: path, release() {} };
  if (isNative()) return { url: Capacitor.convertFileSrc(`${nativeRoot}/${relative}`), release() {} };
  const file = await storedFile(relative);
  const type = path.endsWith('.mp3') ? 'audio/mpeg' : 'image/webp';
  const url = URL.createObjectURL(file.slice(0, file.size, type));
  return { url, release: () => URL.revokeObjectURL(url) };
}
