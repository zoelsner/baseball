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

export async function gotoTab(page: Page, label: 'Today' | 'Roster' | 'Adds' | 'Skipper' | 'League') {
  await page.getByRole('button', { name: label, exact: true }).click();
}

/** Validate the scored or snapshot-only Today card against the API response. */
export async function expectTodayMatchup(page: Page, snapshot: any) {
  expect(snapshot?.roster, 'Today smoke requires a populated successful snapshot').toEqual(expect.any(Array));
  expect(snapshot.roster.length).toBeGreaterThan(0);
  const matchup = snapshot.matchup;
  const hasScores = matchup && ['my_score', 'myScore', 'opponent_score', 'oppScore']
    .some(key => matchup[key] !== undefined);
  const scoredLabel = page.getByText(/Matchup · (Leading|Trailing|Tied)/i).first();
  if (hasScores) {
    await expect(scoredLabel).toBeVisible();
    await expect(page.getByText(/^margin$/i)).toBeVisible();
    await expect(page.locator('body')).toContainText(/\b\d+\.\d\b\s*·\s*\d+\.\d\b/, { useInnerText: true });
    return scoredLabel;
  }
  const snapshotLabel = page.getByText('Latest snapshot', { exact: true });
  await expect(snapshotLabel).toBeVisible();
  await expect(scoredLabel).toHaveCount(0);
  await expect(page.getByText(/^margin$/i)).toHaveCount(0);
  const quality = snapshot.data_quality;
  if (quality?.lineup_slots?.state !== 'ok' || quality?.lineup_recommendations_ready !== true) {
    await expect(page.getByText('Hot swaps paused', { exact: true })).toBeVisible();
    await expect(page.getByText('Advice paused', { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/^Lineup and replacement advice is paused: .+/).first()).toBeVisible();
    await expect(page.getByText('Review lineup move', { exact: true })).toHaveCount(0);
  }
  return snapshotLabel;
}
