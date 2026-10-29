import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { StoredZip, validatePack, MAX_JSON_BYTES } from '../src/pack-format';
import { restoreState } from '../src/storage';
import { restorePracticeState, newSession, emptyPracticeRecord, emptyPracticeState } from '../src/practice';
import { library } from './pack-fixture';

const fixtures: Record<string, string> = JSON.parse(readFileSync('artifacts/pack-fixtures/fixtures.json', 'utf8'));
const book = library.books.find(book => book.id === 'raz-q-003')!;
const shell = { schemaVersion: 'raz-reader-content/2.0', bundleId: library.bundleId,
  contentVersion: 'shell-v1', contentStatus: 'draft-preview', levels: [], books: [], pages: {}, pageCount: 0 };
const readerKey = 'CapacitorStorage.raz-reader-v1';
const practiceKey = 'CapacitorStorage.raz-restoration-v1';

test('资源包只读文件切片，校验元数据并拒绝不兼容或越界条目', async () => {
  const archive = await StoredZip.open(new Blob([new Uint8Array(readFileSync(fixtures.Q))]));
  const metadata = await archive.file('pack.json');
  expect(metadata.size).toBeLessThan(MAX_JSON_BYTES);
  const pack = validatePack(JSON.parse(await metadata.text()));
  expect(pack.library.books).toHaveLength(1);
  expect(archive.names).toHaveLength(pack.files.length + 1);
  const incompatible = structuredClone(pack); incompatible.readerVersion = 99;
  expect(() => validatePack(incompatible)).toThrow();
  const traversal = structuredClone(pack); traversal.files[0].path = '../escape.json';
  expect(() => validatePack(traversal)).toThrow();
  const duplicate = structuredClone(pack); duplicate.files[1].path = duplicate.files[0].path;
  expect(() => validatePack(duplicate)).toThrow();
  await expect(StoredZip.open(new Blob(['not a zip']))).rejects.toThrow();
});

test('移除级别后保留阅读位置与未完成练习，重新导入后可恢复', () => {
  const progress = { [book.id]: { pageId: book.pageIds[4], updatedAt: 100 } };
  const reader = JSON.stringify({ schemaVersion: 1, activeBookId: book.id, progress, fontSize: 'large' });
  const missing = restoreState(reader, shell);
  expect(missing.activeBookId).toBeNull();
  expect(missing.progress).toEqual(progress);
  const session = newSession(book, 100), state = emptyPracticeState();
  state.activeBookId = book.id;
  state.books[book.id] = { ...emptyPracticeRecord(), sessions: [session], currentSessionId: session.id };
  const paused = restorePracticeState(JSON.stringify(state), shell);
  expect(paused.activeBookId).toBeNull();
  expect(paused.books[book.id].currentSessionId).toBe(session.id);
  expect(restorePracticeState(JSON.stringify(paused), library).books[book.id].sessions).toEqual([session]);
  expect(restoreState(JSON.stringify(missing), library).progress).toEqual(progress);
});

async function manager(page: Page) {
  await page.getByRole('button', { name: '管理资源包', exact: true }).click();
  await expect(page.getByRole('heading', { name: '资源包管理' })).toBeVisible();
}
async function install(page: Page, name: string) {
  await page.getByLabel('选择资源包', { exact: true }).setInputFiles(fixtures[name]);
  await expect(page.getByRole('button', { name: '阅读设置', exact: true })).toBeVisible();
  await expect(page.locator('dialog')).not.toBeVisible({ timeout: 30000 });
}
test('首次导入中断留下的空索引和临时版本可在下次启动恢复', async ({ page }) => {
  await page.route('**/content/manifest.json', route => route.fulfill({ json: shell }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '导入第一个级别，开始阅读' })).toBeVisible();
  await page.evaluate(async () => {
    const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('raz-reader-packs', { create: true });
    await dir.getFileHandle('installed.json', { create: true });
    await dir.getDirectoryHandle('Q-abandoned', { create: true });
  });
  await page.reload();
  await expect(page.getByRole('heading', { name: '导入第一个级别，开始阅读' })).toBeVisible();
  expect(await page.evaluate(async () => {
    const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('raz-reader-packs');
    try { await dir.getDirectoryHandle('Q-abandoned'); return true; }
    catch { return false; }
  })).toBe(false);
  await manager(page); await install(page, 'Q');
  await expect(page.locator('.book-card')).toHaveCount(1);
});
test('空壳导入后离线读图、讲解、音频与练习；移除重装保留学习记录', async ({ page, context }, info) => {
  await page.route('**/content/manifest.json', route => route.fulfill({ json: shell }));
  await page.addInitScript(() => {
    const AudioClass = window.Audio;
    (window as any).__packAudio = [];
    window.Audio = class extends AudioClass {
      constructor() { super(); (window as any).__packAudio.push(this); }
    };
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '导入第一个级别，开始阅读' })).toBeVisible();
  await manager(page);
  await install(page, 'Q');
  await expect(page.locator('.book-card')).toHaveCount(1);
  await expect(page.locator('.book-cover img')).toHaveAttribute('src', /^blob:/);
  await page.getByRole('button', { name: `打开 ${book.title}`, exact: true }).click();
  await page.locator('.page-indicator').click();
  await page.getByRole('button', { name: '跳转到第 5 页', exact: true }).click();
  await expect(page.locator('.original-reader')).toHaveAttribute('data-page-id', book.pageIds[4]);
  await expect(page.locator('.paper-image > img')).toHaveAttribute('src', /^blob:/);
  await expect(page.locator('.sentence-hotspots g').first()).toBeVisible();
  await page.screenshot({ path: `artifacts/resource-packs/${info.project.name}-reader.png` });
  await context.setOffline(true);
  // All lesson/media reads now come from local storage, including new object URLs.
  await page.locator('.sentence-hotspots g').first().click();
  await expect(page.locator('.sentence-lesson')).toBeVisible();
  await page.locator('.read-aloud').click();
  await expect.poll(() => page.evaluate(() => {
    const audio = (window as any).__packAudio.at(-1) as HTMLAudioElement;
    return !!audio && audio.currentSrc.startsWith('blob:') && audio.readyState >= 2 && !audio.paused;
  })).toBe(true);
  await page.getByRole('button', { name: '关闭讲解', exact: true }).click();
  await page.locator('.page-indicator').click();
  await page.getByRole('button', { name: '读后练习', exact: true }).click();
  await page.getByRole('button', { name: '开始句子还原', exact: true }).click();
  await page.getByRole('radio', { name: book.practice!.questions[0].blank.text, exact: true }).check();
  await page.getByRole('button', { name: '确认答案', exact: true }).click();
  await page.getByRole('button', { name: '回看书中讲解', exact: true }).click();
  await expect(page.locator('.sentence-lesson')).toBeVisible();
  await context.setOffline(false);
  // Preserve the in-progress round and reading position across removal/reinstall.
  await page.locator('.practice-header button').click();
  await page.locator('.practice-header button').click();
  await page.locator('.practice-header button').click();
  await page.getByRole('button', { name: '返回书架', exact: true }).click();
  const saved = await page.evaluate(({ readerKey, practiceKey }) => ({
    reader: JSON.parse(localStorage.getItem(readerKey)!), practice: JSON.parse(localStorage.getItem(practiceKey)!),
  }), { readerKey, practiceKey });
  await manager(page);
  await page.getByRole('button', { name: '移除 Q 级资源', exact: true }).click();
  await page.getByRole('button', { name: '确认移除 Q 级资源', exact: true }).click();
  await expect(page.getByRole('heading', { name: '导入第一个级别，开始阅读' })).toBeVisible();
  const missing = await page.evaluate(({ readerKey, practiceKey }) => ({
    reader: JSON.parse(localStorage.getItem(readerKey)!), practice: JSON.parse(localStorage.getItem(practiceKey)!),
  }), { readerKey, practiceKey });
  expect(missing.reader.progress).toEqual(saved.reader.progress);
  expect(missing.practice.books).toEqual(saved.practice.books);
  await manager(page); await install(page, 'Q');
  await page.getByRole('button', { name: `打开 ${book.title}`, exact: true }).click();
  await expect(page.locator('.original-reader')).toHaveAttribute('data-page-id', book.pageIds[4]);
  await page.locator('.page-indicator').click();
  await page.getByRole('button', { name: '继续读后练习', exact: true }).click();
  await expect(page.getByRole('heading', { name: '还原正确', exact: true })).toBeVisible();
});

test('资源包损坏或不兼容不影响旧包；更新 Q 包保持 P 包独立', async ({ page }) => {
  await page.route('**/content/manifest.json', route => route.fulfill({ json: shell }));
  await page.goto('/'); await manager(page); await install(page, 'Q');
  await manager(page); await install(page, 'P');
  await manager(page);
  for (const name of ['damaged', 'incompatible']) {
    await page.getByLabel('选择资源包', { exact: true }).setInputFiles(fixtures[name]);
    await expect(page.locator('.resource-settings [role="alert"]')).toBeVisible({ timeout: 30000 });
    await expect(page.locator('.resource-pack')).toHaveCount(2);
  }
  await install(page, 'update');
  await page.getByRole('button', { name: 'Q 级', exact: true }).click();
  await expect(page.locator('.book-card')).toContainText('更新测试');
  await page.getByRole('button', { name: `打开 ${book.title}`, exact: true }).click();
  await expect(page.locator('.original-reader')).toBeVisible();
  await page.getByRole('button', { name: '返回书架', exact: true }).click();
  await page.getByRole('button', { name: 'P 级', exact: true }).click();
  await page.getByRole('button', { name: '打开 A Lantern for Milo', exact: true }).click();
  await expect(page.locator('.original-reader')).toBeVisible();
});

test('其他网页窗口仍使用旧版本时不回收，关闭后释放旧资源', async ({ page, context, baseURL }) => {
  await context.route('**/content/manifest.json', route => route.fulfill({ json: shell }));
  await page.goto('/'); await manager(page); await install(page, 'Q');
  const oldId = await page.evaluate(async () => {
    const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('raz-reader-packs');
    return JSON.parse(await (await (await dir.getFileHandle('installed.json')).getFile()).text()).packs[0].id;
  });
  const second = await context.newPage();
  await second.goto(baseURL! + '/'); await manager(second); await install(second, 'update');
  await expect(second.locator('.book-card')).toContainText('更新测试');
  await page.getByRole('button', { name: `打开 ${book.title}`, exact: true }).click();
  await expect(page.locator('.sentence-hotspots g').first()).toBeVisible();
  await page.close();
  await second.reload();
  // Reading position is shared by these tabs, so startup restores the book
  // opened above rather than always showing the shelf.
  await expect(second.locator('.original-reader')).toBeVisible();
  await second.getByRole('button', { name: '返回书架', exact: true }).click();
  await expect(second.locator('.book-card')).toHaveCount(1);
  expect(await second.evaluate(async id => {
    const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('raz-reader-packs');
    try { await dir.getDirectoryHandle(id); return true; } catch { return false; }
  }, oldId)).toBe(false);
});
