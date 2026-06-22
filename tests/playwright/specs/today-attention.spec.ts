import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { waitForAppMount } from '../fixtures/sandlot';

function baseSnapshot(overrides: Record<string, any> = {}) {
  return {
    team_name: 'Zach Sandlot',
    team_id: 'team-zach',
    snapshot_id: 'snapshot-attention-test',
    taken_at: '2026-06-07T13:00:00Z',
    freshness: { state: 'fresh', age_minutes: 12 },
    roster_meta: {},
    standings: [],
    roster: [
      { id: 'judge', name: 'Aaron Judge', positions: 'OF', team: 'NYY', slot: 'OF', slot_source: 'raw.statusId', fppg: 6.2, injury: 'DTD' },
      { id: 'webb', name: 'Logan Webb', positions: 'SP', team: 'SF', slot: 'SP', slot_source: 'raw.statusId', fppg: 0 },
      { id: 'corner', name: 'Cold Corner', positions: '1B', team: 'SEA', slot: 'UT', slot_source: 'raw.statusId', fppg: 0.8 },
    ],
    matchup: {
      week: 10,
      my_score: 114.2,
      opponent_score: 108.1,
      opponent_team_name: 'Test Opponent',
      days_left: 2,
      recommendations: {
        recommendations: [{
          points_delta: 2.4,
          confidence: 'high',
          reason_chips: ['bench upgrade'],
          action: { chain: [{ player_name: 'Bench Bat', from_slot: 'BN', to_slot: 'UT' }] },
        }],
      },
    },
    data_quality: {
      projection_ready: true,
      recommendations_ready: true,
      lineup_recommendations_ready: true,
      add_drop_recommendations_ready: true,
      lineup_slots: { state: 'ok', trusted: 3, total: 3, reason: 'Lineup slots trusted from Fantrax statusId' },
    },
    ...overrides,
  };
}

async function mockSnapshot(page: Page, payload: Record<string, any>) {
  await page.route('**/api/snapshot/latest', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

async function skipIfAttentionQueueNotDeployed(page: Page) {
  const count = await page.getByText('Attention Queue', { exact: true }).count();
  test.skip(count === 0, 'Target deploy does not have the #58 Attention Queue UI yet.');
}

test.describe('Today — Attention Queue', () => {
  test('orders roster issues by consequence', async ({ page }) => {
    await mockSnapshot(page, baseSnapshot());

    await page.goto('/');
    await waitForAppMount(page);
    await skipIfAttentionQueueNotDeployed(page);

    await expect(page.getByText('1 urgent · 1 check · 2 review')).toBeVisible();
    await expect(page.getByText('Day-to-day on OF. Inspect replacement risk before lock.')).toBeVisible();
    await expect(page.getByText('No projected output. Confirm the active slot before leaving this player in.')).toBeVisible();

    const body = await page.locator('body').innerText();
    const judge = body.indexOf('Aaron Judge');
    const webb = body.indexOf('Logan Webb');
    const cold = body.indexOf('Cold Corner');
    const replacement = body.indexOf('Review lineup move');

    expect(judge).toBeGreaterThanOrEqual(0);
    expect(webb).toBeGreaterThan(judge);
    expect(cold).toBeGreaterThan(webb);
    expect(replacement).toBeGreaterThan(cold);
  });

  test('shows a clear empty state when the snapshot has no queue items', async ({ page }) => {
    await mockSnapshot(page, baseSnapshot({
      roster: [
        { id: 'healthy-a', name: 'Healthy Bat', positions: 'OF', team: 'LAD', slot: 'OF', slot_source: 'raw.statusId', fppg: 5.8 },
        { id: 'healthy-b', name: 'Healthy Arm', positions: 'SP', team: 'ATL', slot: 'SP', slot_source: 'raw.statusId', fppg: 4.4 },
        { id: 'healthy-c', name: 'Healthy Corner', positions: '1B', team: 'NYM', slot: '1B', slot_source: 'raw.statusId', fppg: 3.9 },
      ],
      matchup: {
        week: 10,
        my_score: 114.2,
        opponent_score: 108.1,
        opponent_team_name: 'Test Opponent',
        days_left: 2,
        recommendations: { recommendations: [] },
      },
    }));

    await page.goto('/');
    await waitForAppMount(page);
    await skipIfAttentionQueueNotDeployed(page);

    await expect(page.getByText('No current issues')).toBeVisible();
    await expect(page.getByText('No injury, lineup, output, or replacement issue needs action in the current snapshot.')).toBeVisible();
  });

  test('pauses swap guidance when lineup slot provenance is untrusted', async ({ page }) => {
    test.skip(
      process.env.SANDLOT_EXPECT_SLOT_GATE !== '1',
      'Slot-provenance pause UI is verified against the rebuilt local bundle, not the current Railway deploy.',
    );

    await mockSnapshot(page, baseSnapshot({
      roster: [
        { id: 'friedl', name: 'TJ Friedl', positions: 'OF', team: 'CIN', slot: 'OF', slot_source: 'position_fallback', fppg: 1.4 },
      ],
      matchup: {
        week: 13,
        my_score: 172.5,
        opponent_score: 320.0,
        opponent_team_name: 'Kaman615',
        days_left: 1,
        recommendations: {
          recommendations: [{
            points_delta: 2.6,
            confidence: 'medium',
            reason_chips: ['active-slot upgrade'],
            action: { chain: [{ player_name: 'Bench Bat', from_slot: 'BN', to_slot: 'OF' }] },
          }],
        },
      },
      data_quality: {
        projection_ready: true,
        recommendations_ready: false,
        lineup_recommendations_ready: false,
        add_drop_recommendations_ready: false,
        lineup_recommendation_reasons: ['Lineup-slot source trusted for 17/37 roster players'],
        lineup_slots: { state: 'partial', trusted: 17, total: 37, reason: 'Lineup-slot source trusted for 17/37 roster players' },
      },
    }));

    await page.goto('/');
    await waitForAppMount(page);
    await skipIfAttentionQueueNotDeployed(page);

    await expect(page.getByText('Advice paused')).toBeVisible();
    await expect(page.getByText('Showing only status-safe items until lineup slots are verified.')).toBeVisible();
    await expect(page.getByText('Lineup and replacement advice is paused: Lineup-slot source trusted for 17/37 roster players.')).toBeVisible();
    await expect(page.getByText('Review lineup move')).toHaveCount(0);
    await expect(page.getByText('Low FP/G for active slot')).toHaveCount(0);
  });
});
