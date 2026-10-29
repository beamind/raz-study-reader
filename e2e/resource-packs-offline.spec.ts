import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

test('生产空壳缓存后可断网重新打开，资源包仍能读图与播放原声', async ({ page, context, baseURL }, info) => {
  test.skip(process.env.PLAYWRIGHT_PREVIEW !== '1', 'Offline startup requires the production shell build.');
  const fixtures = JSON.parse(readFileSync('artifacts/pack-fixtures/fixtures.json', 'utf8'));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '导入第一个级别，开始阅读' })).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise<void>(resolve =>
      navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true }));
  });
  await page.getByRole('button', { name: '管理资源包', exact: true }).click();
  await page.getByLabel('选择资源包', { exact: true }).setInputFiles(fixtures.Q);
  await expect(page.locator('.book-card')).toHaveCount(1, { timeout: 30000 });
  await context.setOffline(true);
  await page.close();
  const offline = await context.newPage();
  await offline.goto(baseURL! + '/');
  await expect(offline.locator('.book-card')).toHaveCount(1);
  await offline.getByRole('button', { name: '打开 The Garden Robot', exact: true }).click();
  await offline.locator('.page-indicator').click();
  await offline.getByRole('button', { name: '跳转到第 5 页', exact: true }).click();
  await expect(offline.locator('.sentence-hotspots g').first()).toBeVisible();
  await offline.locator('.sentence-hotspots g').first().click();
  await offline.locator('.read-aloud').click();
  await expect(offline.locator('.read-aloud')).toHaveAttribute('aria-pressed', 'true');
  await offline.screenshot({ path: `artifacts/resource-packs/${info.project.name}-offline.png` });
  await context.setOffline(false);
});
