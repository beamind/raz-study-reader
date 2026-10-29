// Capture the real application with original demo material, using a disposable profile.
import { chromium, expect } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { setTimeout } from 'node:timers/promises';
import path from 'node:path';

const port = 4183;
const baseURL = `http://127.0.0.1:${port}`;
const pack = JSON.parse(readFileSync('artifacts/readme-demo/pack.json', 'utf8')).path;
const output = path.resolve('docs/screenshots');
mkdirSync(output, { recursive: true });
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
let serverLog = '';
for (const pipe of [server.stdout, server.stderr]) pipe.on('data', data => { serverLog = (serverLog + data).slice(-2000); });
let browser;
try {
  for (let i = 0; ; i++) {
    if (server.exitCode !== null) throw new Error(`Preview failed: ${serverLog}`);
    try { if ((await fetch(baseURL)).ok) break; } catch { /* server is starting */ }
    if (i >= 100) throw new Error(`Preview did not start: ${serverLog}`);
    await setTimeout(100);
  }
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  async function open(viewport, mobile = false) {
    const context = await browser.newContext({ viewport, hasTouch: true, isMobile: mobile, serviceWorkers: 'block' });
    const page = await context.newPage();
    await page.goto(baseURL);
    await page.getByRole('button', { name: '管理资源包', exact: true }).click();
    await page.getByLabel('选择资源包', { exact: true }).setInputFiles(pack);
    await expect(page.locator('.book-card')).toHaveCount(3);
    await expect(page.locator('dialog')).not.toBeVisible();
    return page;
  }
  async function capture(page, name) {
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map(image => image.complete ? undefined : new Promise(resolve => {
        image.addEventListener('load', resolve, { once: true });
        image.addEventListener('error', resolve, { once: true });
      })));
    });
    await page.screenshot({ path: path.join(output, name), animations: 'disabled' });
  }
  async function read(page) {
    await page.getByRole('button', { name: '打开 The Garden Robot', exact: true }).click();
    await page.locator('.page-indicator').click();
    await page.getByRole('button', { name: '跳转到第 5 页', exact: true }).click();
    await expect(page.locator('.sentence-hotspots g')).toBeVisible();
  }
  const tablet = await open({ width: 1280, height: 800 });
  await capture(tablet, 'bookshelf.png');
  await read(tablet);
  await capture(tablet, 'reading.png');
  const phone = await open({ width: 412, height: 915 }, true);
  await read(phone);
  await phone.locator('.sentence-hotspots g').click();
  await expect(phone.locator('.sentence-lesson')).toBeVisible();
  await capture(phone, 'sentence-lesson.png');
  await phone.getByRole('button', { name: '关闭讲解', exact: true }).click();
  await phone.locator('.page-indicator').click();
  await phone.getByRole('button', { name: '读后练习', exact: true }).click();
  await phone.getByRole('button', { name: '开始句子还原', exact: true }).click();
  await expect(phone.getByRole('radio', { name: 'bright', exact: true })).toBeVisible();
  await capture(phone, 'practice.png');
  console.log(`Captured 4 real application screenshots in ${output}`);
} finally {
  await browser?.close();
  server.kill();
}
