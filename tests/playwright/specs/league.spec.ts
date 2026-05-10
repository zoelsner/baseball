import { test, expect } from '@playwright/test';
import { waitForAppMount, captureSnapshot, gotoTab } from '../fixtures/sandlot';

test.describe('League page', () => {
  test('renders all teams from snapshot standings, sortable', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;
    await gotoTab(page, 'League');

    const teams = (snapshot?.standings || []).filter((t: any) => t?.team_name);
    test.skip(teams.length === 0, 'Snapshot has no standings; nothing to assert.');

    // Every team name should appear somewhere in the league list.
    for (const t of teams) {
      await expect(page.getByText(t.team_name, { exact: false }).first()).toBeVisible();
    }

    // Sort segment is present and clickable. Switching to "Points" should not
    // throw — a regression here would surface as a console error or empty list.
    await page.getByRole('button', { name: 'Points', exact: true }).click();
    // After re-sort, the first team in the snapshot still renders.
    await expect(page.getByText(teams[0].team_name, { exact: false }).first()).toBeVisible();
  });

  test('opens the team-roster overlay when a team row is tapped', async ({ page }) => {
    const snapshotPromise = captureSnapshot(page);
    await page.goto('/');
    await waitForAppMount(page);
    const snapshot = await snapshotPromise;
    await gotoTab(page, 'League');

    // Pick an opponent (not your own team) to open. The "YOU" badge is rendered
    // for the user's own row; pick a team without the badge so the overlay is
    // unambiguous.
    const myId = snapshot?.team_id;
    const opponent = (snapshot?.standings || []).find(
      (t: any) => t?.team_id && t.team_id !== myId && t.team_name,
    );
    test.skip(!opponent, 'No non-self team in standings.');

    const rosterPromise = page.waitForResponse(
      r => /\/api\/team\/[^/]+\/roster$/.test(r.url()),
      { timeout: 10_000 },
    );

    // The team row is itself a button (the rank tile + name + points). Click
    // by name match — Playwright resolves to the enclosing button.
    await page.getByText(opponent.team_name, { exact: false }).first().click();

    const res = await rosterPromise;
    // 200 (roster present) or 503 (DB transient) are both acceptable signals
    // that the click triggered the right network call. We assert request shape
    // not response payload, since opponent rosters are noisy real data.
    expect([200, 503]).toContain(res.status());
  });
});
