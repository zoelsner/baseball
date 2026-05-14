import { Page, expect } from '@playwright/test';

/**
 * Sandlot is a no-bundler SPA: index.html pulls React + Babel from CDNs and
 * transpiles every .jsx file in-browser before mounting V2App. Tests need a
 * stable signal that the app has actually mounted (not just that the document
 * loaded).
 *
 * The bottom tab bar is the most reliable readiness probe: it renders only
 * after V2App's first commit, and its labels never change once the app is up.
 */
/**
 * Tab-bar buttons need `exact: true` because the "Ask Skipper" CTA on the
 * empty-state Today page shares the word with the "Skipper" tab.
 */
export async function waitForAppMount(page: Page) {
  await expect(page.getByRole('button', { name: 'Today', exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Roster', exact: true })).toBeVisible();
}

/**
 * Capture the snapshot response in-band. Returns the parsed body once the app
 * receives `/api/snapshot/latest`. Use this instead of `page.on('response')`,
 * which races with subsequent test code.
 *
 * Expected usage: kick off the wait BEFORE navigation, then await afterwards.
 *
 *   const snapshotPromise = captureSnapshot(page);
 *   await page.goto('/');
 *   await waitForAppMount(page);
 *   const snapshot = await snapshotPromise;
 */
export async function captureSnapshot(page: Page): Promise<any> {
  const res = await page.waitForResponse(
    r => r.url().includes('/api/snapshot/latest') && r.ok(),
    { timeout: 15_000 },
  );
  return res.json();
}

export async function gotoTab(page: Page, label: 'Today' | 'Roster' | 'Adds' | 'Skipper' | 'Trade' | 'League') {
  await page.getByRole('button', { name: label, exact: true }).click();
}
