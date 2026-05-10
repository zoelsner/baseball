import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.SANDLOT_URL || 'https://web-production-90664.up.railway.app';

export default defineConfig({
  testDir: './specs',
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      // Mobile-shaped chromium. Sandlot is built for mobile (iPhone-frame on
      // desktop), but no iOS-specific APIs; chromium is enough for behaviour.
      name: 'mobile',
      use: { ...devices['Pixel 7'] },
    },
  ],
});
