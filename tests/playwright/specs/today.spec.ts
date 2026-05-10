import { test, expect } from '@playwright/test';
import { waitForAppMount, captureSnapshot } from '../fixtures/sandlot';

test.describe('Today page', () => {
  test('renders matchup card with shape-correct scores and margin', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;

    // If the deployed snapshot has a matchup with scores, the matchup card renders.
    // Otherwise the empty state takes over — assert that path explicitly.
    const m = snapshot?.matchup;
    const hasScores = m && (m.my_score !== undefined || m.myScore !== undefined);

    if (!hasScores) {
      await expect(page.getByText(/no matchup|off week|tbd|fantrax snapshot/i)).toBeVisible();
      test.info().annotations.push({
        type: 'note',
        description: 'Deployed snapshot has no matchup scores; only empty-state asserted.',
      });
      return;
    }

    // X.X · X.X score format renders for both sides somewhere on the page.
    const bodyText = await page.locator('body').innerText();
    expect(bodyText).toMatch(/\b\d{1,3}\.\d\b\s*·\s*\d{1,3}\.\d\b/);

    // Margin label is always present alongside any rendered matchup.
    await expect(page.getByText(/^margin$/i)).toBeVisible();
  });

  test('shows opponent label from snapshot', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;

    const oppName = snapshot?.matchup?.opponent_team_name;
    test.skip(!oppName, 'No opponent in current snapshot; nothing to assert.');

    // Escape regex metacharacters in opponent name (team names sometimes
    // contain `.` or `+`).
    const escaped = oppName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    await expect(page.getByText(new RegExp(`vs\\s+${escaped}`, 'i'))).toBeVisible();
  });
});
