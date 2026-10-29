import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
const { version } = JSON.parse(readFileSync('package.json', 'utf8'));

test('正式阅读器显示发行版本，许可证可在断网后离线查看', async ({ page, context }) => {
  test.skip(process.env.PLAYWRIGHT_PREVIEW !== '1', 'Release shell requires a production build.');
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '导入第一个级别，开始阅读' })).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise<void>(resolve =>
      navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true }));
  });
  await context.setOffline(true);
  await page.reload();
  await page.getByRole('button', { name: '阅读设置', exact: true }).click();
  await page.getByText('关于这个版本', { exact: true }).click();
  await expect(page.getByText(`RAZ 小小精读 · ${version}`, { exact: true })).toBeVisible();
  await expect(page.getByText('阅读器代码采用 PolyForm Noncommercial 1.0.0 非商业许可，允许范围以协议为准。与 Learning A-Z 无隶属关系，书籍与音频的权利归原权利人所有。', { exact: true })).toBeVisible();
  await page.getByText('软件与组件许可证', { exact: true }).click();
  const notices = page.locator('.about pre');
  await expect(notices).toContainText('Copyright (c) 2026 beamind');
  await expect(notices).toContainText('node_modules/react @');
  await expect(notices).toContainText('Apache License');
  await expect(notices).toContainText('PolyForm Noncommercial License 1.0.0');
  await expect(notices).toContainText('## Noncommercial Purposes');
  await expect(notices).toContainText('Required Notice: Copyright (c) 2026 beamind');
  await expect(notices).toContainText('License: MIT');
  const appLicense = await page.evaluate(async () => {
    const response = await fetch('./legal/APP-LICENSE.txt');
    return { ok: response.ok, text: await response.text() };
  });
  expect(appLicense.ok).toBe(true);
  expect(appLicense.text).toContain('PolyForm Noncommercial License 1.0.0');
  expect(appLicense.text).not.toContain('MIT License');
  await context.setOffline(false);
});
