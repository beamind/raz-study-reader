import { defineConfig } from '@playwright/test';

const preview = process.env.PLAYWRIGHT_PREVIEW !== '0';
if (preview) process.env.PLAYWRIGHT_PREVIEW = '1';
const baseURL = preview ? 'http://127.0.0.1:4173' : 'http://127.0.0.1:5173';

export default defineConfig({
  testDir: './e2e',
  timeout: 60000,
  expect: { timeout: 10000 },
  workers: 2,
  fullyParallel: true,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL, channel: process.env.PLAYWRIGHT_CHANNEL || undefined, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'tablet', use: { browserName: 'chromium', viewport: { width: 1280, height: 800 }, hasTouch: true } },
    { name: 'phone', use: { browserName: 'chromium', viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true } },
  ],
  webServer: { command: preview ? 'npm run preview' : 'npm run dev', url: baseURL, reuseExistingServer: false, timeout: 60000 },
});
