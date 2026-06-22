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
          action: {
            chain: [
              { player_id: 'bench-bat', player_name: 'Bench Bat', from_slot: 'BN', to_slot: 'UT' },
              { player_id: 'corner', player_name: 'Cold Corner', from_slot: 'UT', to_slot: 'BN' },
            ],
          },
          replacement_card: {
            type: 'lineup_hot_swap',
            proposal: {
              id: 'lineup-swap:corner:bench-bat:UT',
              type: 'lineup_swap',
              status: 'blocked',
              writes_enabled: false,
              confirmation_required: true,
              summary: 'Move Cold Corner out and Bench Bat in.',
              safety_checks: [
                { key: 'trusted_slots', label: 'Trusted slot data', state: 'passed', detail: 'Recommendation is only emitted after lineup slot provenance is trusted.' },
                { key: 'lineup_only', label: 'Lineup-only move', state: 'passed', detail: 'No add, drop, trade, or roster-pool mutation is attached to this proposal.' },
                { key: 'protected_players', label: 'Protected players excluded', state: 'passed', detail: 'Minors, IL/IR, and other protected rows are not eligible swap targets.' },
                { key: 'executor_ready', label: 'Execution safety', state: 'blocked', detail: 'Fantrax write execution still needs a separate confirmed executor contract.' },
              ],
            },
            move_in: {
              id: 'bench-bat',
              name: 'Bench Bat',
              team: 'LAD',
              positions: '1B',
              from_slot: 'BN',
              to_slot: 'UT',
              fppg: 4.2,
              remaining_games: 2,
              slot_source: 'raw.statusId',
            },
            move_out: {
              id: 'corner',
              name: 'Cold Corner',
              team: 'SEA',
              positions: '1B',
              from_slot: 'UT',
              to_slot: 'BN',
              fppg: 0.8,
              remaining_games: 1,
              slot_source: 'raw.lineupSlot',
            },
            projected_benefit: { points: 2.4, win_probability_delta: 0.02 },
            reason: 'Move Bench Bat into UT and Cold Corner to BN because the lineup-only simulation sees bench upgrade.',
            short_term_outlook: 'Bench Bat has 2 remaining games at 4.2 FP/G; Cold Corner has 1 remaining game at 0.8 FP/G.',
            risk: 'Medium risk: this is a lineup-only projection. Confirm Fantrax lock status before acting.',
            confidence: 'high',
            risk_label: 'medium',
            provenance: {
              source: 'latest Fantrax snapshot',
              slot_provenance: 'trusted',
              move_in_slot_source: 'raw.statusId',
              move_out_slot_source: 'raw.lineupSlot',
            },
            safety: { lineup_only: true, add_drop: false, live_writes: false },
            execution: {
              state: 'blocked',
              label: 'Propose swap',
              reason: 'Lineup execution is disabled until safety is ready.',
            },
            blocked_reason: 'Propose swap is disabled until execution safety is ready.',
          },
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

    const expectBranchHotSwap = process.env.SANDLOT_EXPECT_SLOT_GATE === '1';
    if (expectBranchHotSwap) {
      await expect(page.getByText('1 hot swap')).toBeVisible();
      await expect(page.getByText('Best lineup-only move from the latest matchup simulation.')).toBeVisible();
      await expect(page.getByText('1 urgent · 1 check · 1 review')).toBeVisible();
    } else {
      await expect(page.getByText('1 urgent · 1 check · 2 review')).toBeVisible();
    }
    await expect(page.getByText('Day-to-day on OF. Inspect replacement risk before lock.')).toBeVisible();
    await expect(page.getByText('No projected output. Confirm the active slot before leaving this player in.')).toBeVisible();

    const yOf = async (locator: ReturnType<Page['locator']>) => {
      const box = await locator.boundingBox();
      expect(box).not.toBeNull();
      return box!.y;
    };
    const judge = await yOf(page.getByRole('button', { name: /Aaron Judge/ }));
    const webb = await yOf(page.getByRole('button', { name: /Logan Webb/ }));
    const cold = await yOf(page.getByRole('button', { name: /Cold Corner Review Output/ }));
    const replacement = await yOf(expectBranchHotSwap
      ? page.getByText('Bench Bat for Cold Corner')
      : page.getByText(/Review lineup move|Bench Bat for Cold Corner/)
    );

    if (expectBranchHotSwap) {
      expect(replacement).toBeLessThan(judge);
    } else {
      expect(replacement).toBeGreaterThan(cold);
    }
    expect(webb).toBeGreaterThan(judge);
    expect(cold).toBeGreaterThan(webb);

    if (expectBranchHotSwap) {
      await expect(page.getByText('Bench Bat for Cold Corner')).toBeVisible();
      await expect(page.getByText('OUT', { exact: true })).toBeVisible();
      await expect(page.getByText('IN', { exact: true })).toBeVisible();
      await expect(page.getByText('+2.4')).toBeVisible();
      await expect(page.getByText('high confidence', { exact: true })).toBeVisible();
      await expect(page.getByText('medium risk', { exact: true })).toBeVisible();
      await expect(page.getByText('latest Fantrax snapshot', { exact: true })).toBeVisible();
      const queueSection = page.locator('section').filter({ hasText: 'Bench Bat for Cold Corner' });
      await expect(queueSection.getByText('Proposal safety', { exact: true })).toBeVisible();
      await expect(queueSection.getByText('Trusted slot data', { exact: true })).toBeVisible();
      await expect(queueSection.getByText('Lineup-only move', { exact: true })).toBeVisible();
      await expect(queueSection.getByText('Protected players excluded', { exact: true })).toBeVisible();
      await expect(queueSection.getByText('Execution safety', { exact: true })).toBeVisible();
      await expect(queueSection.getByRole('button', { name: /Propose swap blocked/i })).toBeDisabled();
      await expect(queueSection.getByRole('button', { name: /Ask Skipper/i })).toBeVisible();
      await expect(queueSection.getByRole('button', { name: /Deep research/i })).toBeVisible();

      await queueSection.getByRole('button', { name: /Ask Skipper/i }).click();
      await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Pressure-test this lineup-only hot swap/);
      await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Move IN: Bench Bat/);
      await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Move OUT: Cold Corner/);
      await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/lineup-swap:corner:bench-bat:UT/);
      await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/writes enabled: no/);
    }
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

    if (process.env.SANDLOT_EXPECT_SLOT_GATE === '1') {
      await expect(page.getByText('No hot swaps')).toBeVisible();
      await expect(page.getByText('No lineup-only move clears the meaningful-gain threshold right now.')).toBeVisible();
    }
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

    await expect(page.getByText('Hot swaps paused')).toBeVisible();
    await expect(page.getByText('Lineup swap advice is paused: Lineup-slot source trusted for 17/37 roster players.')).toBeVisible();
    await expect(page.getByText('Advice paused')).toHaveCount(2);
    await expect(page.getByText('Showing only status-safe items until lineup slots are verified.')).toBeVisible();
    await expect(page.getByText('Lineup and replacement advice is paused: Lineup-slot source trusted for 17/37 roster players.')).toBeVisible();
    await expect(page.getByText('Review lineup move')).toHaveCount(0);
    await expect(page.getByText('Low FP/G for active slot')).toHaveCount(0);
  });

  test('pauses swap guidance when explicit lineup readiness is missing', async ({ page }) => {
    test.skip(
      process.env.SANDLOT_EXPECT_SLOT_GATE !== '1',
      'Slot-provenance pause UI is verified against the rebuilt local bundle, not the current Railway deploy.',
    );

    const dataQuality = {
      projection_ready: true,
      recommendations_ready: true,
      add_drop_recommendations_ready: true,
      lineup_slots: { state: 'ok', trusted: 3, total: 3, reason: 'Lineup slots trusted from Fantrax statusId' },
    };

    await mockSnapshot(page, baseSnapshot({ data_quality: dataQuality }));

    await page.goto('/');
    await waitForAppMount(page);
    await skipIfAttentionQueueNotDeployed(page);

    await expect(page.getByText('Advice paused')).toBeVisible();
    await expect(page.getByText('Lineup and replacement advice is paused: Lineup recommendation readiness is not explicitly trusted.')).toBeVisible();
    await expect(page.getByText('Review lineup move')).toHaveCount(0);
  });
});
