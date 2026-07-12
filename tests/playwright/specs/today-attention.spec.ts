import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { gotoTab, waitForAppMount } from '../fixtures/sandlot';

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
          confidence_basis: 'projected_points_magnitude',
          probability_calibrated: false,
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
                { key: 'fantrax_movability', label: 'Fantrax movability', state: 'blocked', detail: 'Fantrax currently marks Cold Corner unavailable for lineup changes.' },
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
            movability: {
              state: 'locked',
              label: 'Locked',
              reason: 'Fantrax currently marks Cold Corner unavailable for lineup changes.',
              source: 'fantrax.raw.scorer.disableLineupChange',
              participants: {
                move_in: { id: 'bench-bat', name: 'Bench Bat', state: 'movable' },
                move_out: { id: 'corner', name: 'Cold Corner', state: 'locked' },
              },
            },
            projected_benefit: { points: 2.4, win_probability_delta: null, probability_calibrated: false },
            reason: 'Move Bench Bat into UT and Cold Corner to BN because the lineup-only simulation sees bench upgrade.',
            short_term_outlook: 'Bench Bat has 2 remaining games at 4.2 FP/G; Cold Corner has 1 remaining game at 0.8 FP/G.',
            risk: 'Risk unknown: win probability is not calibrated. Confirm Fantrax lock status before acting.',
            confidence: 'high',
            confidence_basis: 'projected_points_magnitude',
            risk_label: 'unknown',
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
    win_this_week: {
      model_version: 'win_this_week_v1',
      state: 'ready',
      snapshot_id: 'snapshot-attention-test',
      read_only: true,
      writes_enabled: false,
      handoffs: {
        lineup: {
          label: 'Open Fantrax lineup',
          url: 'https://www.fantrax.com/fantasy/league/league-test/team/roster;teamId=team-test',
          method: 'GET',
          read_only: true,
          writes_enabled: false,
        },
      },
      primary_action_id: 'waiver:test:add:drop',
      summary: {
        headline: 'Up 6.1; the best current path adds about 5.8 projected points to protect the lead.',
        outlook: 'After this move, the remaining-week estimate puts you 9.8 points ahead.',
        projected_margin_before_action: 4.0,
        projected_margin_after_action: 9.8,
        win_probability_excluded_reason: 'Win probability is not calibrated; actions are ranked by projected remaining-week points.',
        projection_caveat: 'Known-opportunity lower bound: 3 pitcher(s) have no posted probable start and contribute zero until that changes.',
      },
      actions: [{
        id: 'waiver:test:add:drop',
        rank: 1,
        kind: 'waiver',
        state: 'review_now',
        title: 'Add Impact Streamer and move out Cold Corner',
        steps: [
          { action: 'add', player_id: 'impact-streamer', player_name: 'Impact Streamer' },
          { action: 'move_out', player_id: 'corner', player_name: 'Cold Corner' },
          { action: 'start', player_id: 'impact-streamer', player_name: 'Impact Streamer', to_slot: 'UT' },
          { player_id: 'bridge-one', player_name: 'Bridge One', from_slot: 'UT', to_slot: 'OF' },
          { player_id: 'bridge-two', player_name: 'Bridge Two', from_slot: 'OF', to_slot: 'BN' },
        ],
        expected_points: { estimate: 5.8, comparable: true },
        win_probability_delta: null,
        probability_calibrated: false,
        deadline: { state: 'known', at: '2099-06-07T22:40:00Z' },
        confidence: 'medium',
        dynasty_cost: { level: 'low', reason: 'No major dynasty concern from age alone.' },
        legality: { state: 'provisionally_legal', requires_live_preflight: true },
        writes_enabled: false,
      }],
      monitoring_actions: [{
        id: 'monitor:waiver:test:add:drop',
        kind: 'monitor',
        state: 'scheduled_check',
        title: 'Recheck Fantrax and MLB status before the action deadline',
        reason: 'Availability, lineup confirmation, and Fantrax locks can change after the snapshot.',
      }],
      diagnostics: { probability_calibrated: false },
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

const isLocalBundle = process.env.SANDLOT_EXPECT_SLOT_GATE === '1';

test.describe('Today — Attention Queue', () => {
  test('orders roster issues by consequence', async ({ page }) => {
    test.skip(
      !isLocalBundle,
      'Mocked branch-only Today ordering is verified against the rebuilt local bundle; Railway PR E2E remains a live production smoke.',
    );

    await mockSnapshot(page, baseSnapshot());

    await page.goto('/');
    await waitForAppMount(page);
    await skipIfAttentionQueueNotDeployed(page);

    await expect(page.getByText('Matchup · Leading')).toBeVisible();
    await expect(page.getByText('snapshot 12m old')).toBeVisible();
    await expect(page.getByText('Win This Week', { exact: true })).toBeVisible();
    await expect(page.getByText('Review now', { exact: true })).toBeVisible();
    await expect(page.getByText('Add Impact Streamer and move out Cold Corner', { exact: true })).toBeVisible();
    await expect(page.getByText('After this move, the remaining-week estimate puts you 9.8 points ahead.', { exact: true })).toBeVisible();
    await expect(page.getByText('+5.8', { exact: true })).toBeVisible();
    await expect(page.getByText('Live preflight required', { exact: true })).toBeVisible();
    await expect(page.getByText('Read-only', { exact: true })).toBeVisible();
    await expect(page.getByText('Complete order · 5 steps', { exact: true })).toBeVisible();
    await expect(page.getByText('Bridge Two: OF → BN', { exact: true })).toBeVisible();
    await expect(page.getByText(/Projection note: Known-opportunity lower bound: 3 pitcher/)).toBeVisible();
    await expect(page.getByText('1 hot swap')).toBeVisible();
    await expect(page.getByText('Leading by 6.1 · 2d left; this swap adds +2.4 projected points to protect the edge.')).toBeVisible();
    await expect(page.getByText('1 urgent · 1 check · 1 review')).toBeVisible();
    await expect(page.getByText('Day-to-day on OF. Inspect replacement risk before lock.')).toBeVisible();
    await expect(page.getByText('No projected output. Confirm the active slot before leaving this player in.')).toBeVisible();

    const yOf = async (locator: ReturnType<Page['locator']>) => {
      const box = await locator.boundingBox();
      expect(box).not.toBeNull();
      return box!.y;
    };
    const matchupTop = await yOf(page.getByText('Matchup · Leading'));
    const winThisWeekTop = await yOf(page.getByText('Win This Week', { exact: true }));
    const hotSwapsTop = await yOf(page.getByText('1 hot swap'));
    const judge = await yOf(page.getByRole('button', { name: /Aaron Judge/ }));
    const webb = await yOf(page.getByRole('button', { name: /Logan Webb/ }));
    const cold = await yOf(page.getByRole('button', { name: /Cold Corner Review Output/ }));
    const replacement = await yOf(page.getByText('Bench Bat for Cold Corner'));

    expect(matchupTop).toBeLessThan(hotSwapsTop);
    expect(matchupTop).toBeLessThan(winThisWeekTop);
    expect(winThisWeekTop).toBeLessThan(hotSwapsTop);
    expect(replacement).toBeLessThan(judge);
    expect(webb).toBeGreaterThan(judge);
    expect(cold).toBeGreaterThan(webb);

    const queueSection = page.locator('section').filter({ hasText: 'Bench Bat for Cold Corner' });
    await expect(page.getByText('Bench Bat for Cold Corner')).toBeVisible();
    await expect(page.getByText('OUT', { exact: true })).toBeVisible();
    await expect(page.getByText('IN', { exact: true })).toBeVisible();
    await expect(queueSection.getByText('+2.4', { exact: true })).toBeVisible();
    await expect(page.getByText('high point edge', { exact: true })).toBeVisible();
    await expect(page.getByText('unknown risk', { exact: true })).toBeVisible();
    await expect(page.getByText('latest Fantrax snapshot', { exact: true })).toBeVisible();
    if (process.env.SANDLOT_EXPECT_SLOT_GATE === '1') {
      await expect(page.getByText('Locked', { exact: true })).toBeVisible();
      await expect(queueSection.getByText('Movability. Fantrax currently marks Cold Corner unavailable for lineup changes.')).toBeVisible();
    }
    await expect(queueSection.getByText('Proposal safety', { exact: true })).toBeVisible();
    await expect(queueSection.getByText('Trusted slot data', { exact: true })).toBeVisible();
    await expect(queueSection.getByText('Lineup-only move', { exact: true })).toBeVisible();
    await expect(queueSection.getByText('Protected players excluded', { exact: true })).toBeVisible();
    await expect(queueSection.getByText('Fantrax movability', { exact: true })).toBeVisible();
    await expect(queueSection.getByText('Execution safety', { exact: true })).toBeVisible();
    await expect(queueSection.getByRole('button', { name: /Propose swap blocked/i })).toBeDisabled();
    await expect(queueSection.getByRole('button', { name: /Ask Skipper/i })).toBeVisible();
    await expect(queueSection.getByRole('button', { name: /Deep research/i })).toBeVisible();

    const winPanel = page.getByRole('region', { name: 'Win This Week' });
    await expect(winPanel.getByRole('button', { name: 'Pressure-test with Skipper' })).toBeVisible();
    await expect(winPanel.getByRole('button', { name: 'Open waiver board' })).toBeVisible();
    await winPanel.getByRole('button', { name: 'Pressure-test with Skipper' }).click();
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/top Win This Week action/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Expected remaining-week impact: \+5.8 points/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/remaining-week estimate puts you 9.8 points ahead/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Add Impact Streamer/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Move out Cold Corner/);

    await gotoTab(page, 'Today');

    await queueSection.getByRole('button', { name: /Ask Skipper/i }).click();
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Pressure-test this lineup-only hot swap/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Move IN: Bench Bat/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/Move OUT: Cold Corner/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/lineup-swap:corner:bench-bat:UT/);
    await expect(page.getByPlaceholder(/Ask about your roster/)).toHaveValue(/writes enabled: no/);
  });

  test('shows a clear empty state when the snapshot has no queue items', async ({ page }) => {
    test.skip(
      !isLocalBundle,
      'Mocked branch-only Today empty state is verified against the rebuilt local bundle; Railway PR E2E remains a live production smoke.',
    );

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

    await expect(page.getByText('No hot swaps')).toBeVisible();
    await expect(page.getByText('No lineup-only move clears the meaningful-gain threshold right now.')).toBeVisible();
    await expect(page.getByText('No current issues')).toBeVisible();
    await expect(page.getByText('No injury, lineup, output, or replacement issue needs action in the current snapshot.')).toBeVisible();
  });

  test('explains why the best no-action alternatives were rejected', async ({ page }) => {
    test.skip(!isLocalBundle, 'No-action alternatives are verified against the rebuilt local bundle.');
    await mockSnapshot(page, baseSnapshot({
      win_this_week: {
        model_version: 'win_this_week_v1',
        state: 'no_action',
        read_only: true,
        writes_enabled: false,
        summary: {
          headline: 'No lineup move clears the meaningful-gain threshold from this snapshot.',
          outlook: 'The current remaining-week estimate leaves you 12.0 points behind.',
          projected_margin_before_action: -12.0,
          projected_margin_after_action: null,
        },
        actions: [],
        monitoring_actions: [],
        no_action: {
          reason: 'No lineup move clears the meaningful-gain threshold from this snapshot.',
          alternatives: [{
            id: 'rejected-lineup:bench:starter',
            kind: 'lineup',
            title: 'Start Small Upgrade over Current Starter',
            expected_points: { estimate: 0.4, comparable: true },
            status: 'below_threshold',
            reason: "The estimated +0.4-point gain is below Sandlot's 1.0-point meaningful-gain threshold.",
          }],
        },
      },
    }));

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    await expect(panel.getByText('No worthwhile move', { exact: true })).toBeVisible();
    await expect(panel.getByText('Best alternatives checked', { exact: true })).toBeVisible();
    await expect(panel.getByText('The current remaining-week estimate leaves you 12.0 points behind.', { exact: true })).toBeVisible();
    await expect(panel.getByText('Start Small Upgrade over Current Starter', { exact: true })).toBeVisible();
    await expect(panel.getByText('+0.4 pts', { exact: true })).toBeVisible();
    await expect(panel.getByText(/below Sandlot's 1.0-point meaningful-gain threshold/)).toBeVisible();
  });

  test('opens lineup plans on the verified read-only Fantrax roster route', async ({ page }) => {
    test.skip(!isLocalBundle, 'Fantrax lineup handoff is verified against the rebuilt local bundle.');
    const snapshot = baseSnapshot();
    snapshot.win_this_week.actions[0].kind = 'lineup';
    snapshot.win_this_week.actions[0].state = 'act_now';
    snapshot.win_this_week.actions[0].title = 'Start Bench Bat over Current Starter';
    await mockSnapshot(page, snapshot);

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    const handoff = panel.getByRole('link', { name: 'Open Fantrax lineup' });
    await expect(handoff).toHaveAttribute(
      'href',
      'https://www.fantrax.com/fantasy/league/league-test/team/roster;teamId=team-test',
    );
    await expect(handoff).toHaveAttribute('target', '_blank');
    await expect(panel.getByRole('button', { name: 'Open waiver board' })).toHaveCount(0);
  });

  test('blocks an expired primary action until the plan is refreshed', async ({ page }) => {
    test.skip(!isLocalBundle, 'Deadline-expiry safety is verified against the rebuilt local bundle.');
    const expired = baseSnapshot();
    expired.win_this_week.actions[0].deadline = { state: 'known', at: '2020-01-01T00:00:00Z' };
    await mockSnapshot(page, expired);

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    await expect(panel.getByText('Refresh required', { exact: true })).toBeVisible();
    await expect(panel.getByText('Deadline passed · refresh required', { exact: true })).toBeVisible();
    await expect(panel.getByText('expired estimate', { exact: true })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Refresh plan' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Pressure-test with Skipper' })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: 'Open waiver board' })).toHaveCount(0);
  });

  test('blocks waiver controls when the snapshot is stale', async ({ page }) => {
    test.skip(!isLocalBundle, 'Stale-plan safety is verified against the rebuilt local bundle.');
    await mockSnapshot(page, baseSnapshot({
      freshness: { state: 'stale', age_minutes: 48 },
    }));

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    await expect(panel.getByText('Refresh required', { exact: true })).toBeVisible();
    await expect(panel.getByText('This plan comes from a stale snapshot. Refresh before making any Fantrax change.', { exact: true })).toBeVisible();
    await expect(panel.getByText('stale estimate', { exact: true })).toBeVisible();
    await expect(panel.getByText('Snapshot stale', { exact: true })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Refresh plan' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Pressure-test with Skipper' })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: 'Open waiver board' })).toHaveCount(0);
  });

  test('blocks Fantrax lineup handoff when the snapshot is old', async ({ page }) => {
    test.skip(!isLocalBundle, 'Old-plan safety is verified against the rebuilt local bundle.');
    const old = baseSnapshot({ freshness: { state: 'old', age_minutes: 180 } });
    old.win_this_week.actions[0].kind = 'lineup';
    old.win_this_week.actions[0].title = 'Start Bench Bat over Current Starter';
    await mockSnapshot(page, old);

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    await expect(panel.getByText('Refresh required', { exact: true })).toBeVisible();
    await expect(panel.getByText('This plan comes from an old snapshot. Refresh before making any Fantrax change.', { exact: true })).toBeVisible();
    await expect(panel.getByText('Snapshot old', { exact: true })).toBeVisible();
    await expect(panel.getByRole('link', { name: 'Open Fantrax lineup' })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: 'Pressure-test with Skipper' })).toHaveCount(0);
  });

  test('pauses matchup actions when Fantrax is editing a different period', async ({ page }) => {
    test.skip(!isLocalBundle, 'Editable-period safety is verified against the rebuilt local bundle.');
    const mismatch = baseSnapshot();
    mismatch.win_this_week = {
      model_version: 'win_this_week_v1',
      state: 'paused',
      read_only: true,
      writes_enabled: false,
      current_period: {
        state: 'mismatch',
        actionable: false,
        matchup: { number: 16, start: '2026-07-06', end: '2026-07-12' },
        fantrax: { number: 17, start: '2026-07-13', end: '2026-07-26' },
      },
      summary: {
        headline: 'Fantrax is editing Period 17 (Jul 13 through Jul 26), which cannot affect projected Period 16 (Jul 6 through Jul 12).',
        outlook: 'The current remaining-week projection remains available for context.',
      },
      actions: [],
      handoffs: {},
      monitoring_actions: [{
        id: 'monitor:period-alignment',
        kind: 'monitor',
        state: 'blocked',
        title: 'Wait for the projected matchup to become editable',
        reason: 'Fantrax and the matchup projection refer to different scoring periods.',
      }],
      no_action: {
        reason: 'Fantrax is editing Period 17 (Jul 13 through Jul 26), which cannot affect projected Period 16 (Jul 6 through Jul 12).',
        alternatives: [],
      },
    };
    await mockSnapshot(page, mismatch);

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    await expect(panel.getByText('Plan paused', { exact: true })).toBeVisible();
    await expect(panel.getByText('0 options', { exact: true })).toBeVisible();
    await expect(panel.getByText(/Fantrax is editing Period 17 .* cannot affect projected Period 16/)).toBeVisible();
    await expect(panel.getByText(/Monitor: Wait for the projected matchup to become editable/)).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Pressure-test with Skipper' })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: 'Open waiver board' })).toHaveCount(0);
    await expect(panel.getByRole('link', { name: 'Open Fantrax lineup' })).toHaveCount(0);
  });

  test('labels a safe future-period lineup plan separately from the live matchup', async ({ page }) => {
    test.skip(!isLocalBundle, 'Future-period planning UI is verified against the rebuilt local bundle.');
    const future = baseSnapshot();
    future.win_this_week.planning_horizon = {
      mode: 'editable_period',
      period_number: 17,
      start: '2026-07-13',
      end: '2026-07-26',
      matchup_key: 'period-17',
      lineup_actions_enabled: true,
      waiver_actions_enabled: false,
    };
    future.win_this_week.actions[0].kind = 'lineup';
    future.win_this_week.actions[0].title = 'Start Bench Bat over Cold Corner';
    future.win_this_week.actions[0].target_period = {
      period_number: 17,
      start: '2026-07-13',
      end: '2026-07-26',
      matchup_key: 'period-17',
    };
    future.win_this_week.actions[0].review = {
      state: 'reviewable',
      proposal_id: 'lineup-swap:corner:bench-bat:UT',
      snapshot_id: 'snapshot-attention-test',
      input_hash: 'a'.repeat(64),
      target_period: future.win_this_week.actions[0].target_period,
      slot_moves: [
        { order: 1, player_id: 'bench-bat', player_name: 'Bench Bat', from_slot: 'BN', to_slot: 'UT' },
        { order: 2, player_id: 'corner', player_name: 'Cold Corner', from_slot: 'UT', to_slot: 'BN' },
      ],
      executor: { state: 'offline' },
      requires_local_visible_approval: true,
      requires_live_preflight: true,
      writes_enabled: false,
    };
    future.win_this_week.summary.headline = 'Planning Period 17: the best legal lineup path adds about 5.8 projected points.';
    future.win_this_week.handoffs.lineup.label = 'Open Fantrax Period 17 lineup';
    future.win_this_week.handoffs.lineup.target_period = future.win_this_week.actions[0].target_period;
    future.win_this_week.monitoring_actions = [{
      id: 'monitor:future-period-waiver-boundary',
      kind: 'monitor',
      state: 'blocked',
      title: 'Future-period waivers remain research-only',
      reason: 'Period 17 add/drop timing is not proven.',
    }];
    await mockSnapshot(page, future);

    await page.goto('/');
    await waitForAppMount(page);

    const panel = page.getByRole('region', { name: 'Win This Week' });
    await expect(panel.getByText('Plan Period 17', { exact: true })).toBeVisible();
    await expect(panel.getByText('Best next-period lineup', { exact: true })).toBeVisible();
    await expect(panel.getByText(/Planning Period 17: the best legal lineup path/)).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Review exact action' })).toBeVisible();
    await expect(panel.getByRole('link', { name: 'Open Fantrax Period 17 lineup' })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Open waiver board' })).toHaveCount(0);
    await expect(panel.getByText(/Monitor: Future-period waivers remain research-only/)).toBeVisible();

    await panel.getByRole('button', { name: 'Review exact action' }).click();
    const review = page.getByRole('dialog', { name: 'Review exact lineup action' });
    await expect(review.getByText('Exact action review', { exact: true })).toBeVisible();
    await expect(review.getByText('Start Bench Bat over Cold Corner', { exact: true })).toBeVisible();
    await expect(review.getByText('Period 17', { exact: true })).toBeVisible();
    await expect(review.getByText('BN → UT', { exact: true })).toBeVisible();
    await expect(review.getByText('UT → BN', { exact: true })).toBeVisible();
    await expect(review.getByText(/Local executor offline\. Nothing will change from this screen/)).toBeVisible();
    await expect(review.getByRole('button', { name: 'Ask Skipper' })).toBeVisible();
    await expect(review.getByRole('link', { name: 'Open Fantrax Period 17 lineup' })).toBeVisible();
    await expect(review.getByRole('button', { name: /confirm|execute|apply/i })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(review).toHaveCount(0);
    await expect(panel.getByRole('button', { name: 'Review exact action' })).toBeFocused();
    await panel.getByRole('button', { name: 'Review exact action' }).click();
    await page.getByRole('dialog', { name: 'Review exact lineup action' }).getByRole('button', { name: 'Ask Skipper' }).click();
    const skipperPrompt = page.getByPlaceholder(/Ask about your roster/);
    await expect(skipperPrompt).toHaveValue(/Immutable proposal: snapshot snapshot-attention-test; proposal lineup-swap:corner:bench-bat:UT; input hash a{64}/);
    await expect(skipperPrompt).toHaveValue(/Exact target: Period 17; matchup period-17; 2026-07-13 through 2026-07-26/);
    await expect(skipperPrompt).toHaveValue(/Bench Bat: BN → UT/);
    await expect(skipperPrompt).toHaveValue(/This is a read-only review/);
  });

  test('silently refetches when the primary action deadline arrives', async ({ page }) => {
    test.skip(!isLocalBundle, 'Deadline-triggered refetch is verified against the rebuilt local bundle.');
    const expiring = baseSnapshot();
    expiring.win_this_week.actions[0].deadline = {
      state: 'known',
      at: new Date(Date.now() + 500).toISOString(),
    };
    const refreshed = baseSnapshot({
      win_this_week: {
        model_version: 'win_this_week_v1',
        state: 'no_action',
        read_only: true,
        writes_enabled: false,
        summary: { headline: 'No legal move remains after the deadline.' },
        actions: [],
        monitoring_actions: [],
        no_action: { reason: 'No legal move remains after the deadline.' },
      },
    });
    let requests = 0;
    await page.route('**/api/snapshot/latest', async route => {
      requests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(requests === 1 ? expiring : refreshed),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await expect(page.getByText('Add Impact Streamer and move out Cold Corner', { exact: true })).toBeVisible();
    await expect(page.getByText('No worthwhile move', { exact: true })).toBeVisible();
    expect(requests).toBeGreaterThanOrEqual(2);
  });

  test('pauses swap guidance when lineup slot provenance is untrusted', async ({ page }) => {
    test.skip(
      !isLocalBundle,
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
    await expect(page.getByText('Lineup and replacement advice is paused: Lineup-slot source trusted for 17/37 roster players.')).toHaveCount(2);
    await expect(page.getByText('Review lineup move')).toHaveCount(0);
    await expect(page.getByText('Low FP/G for active slot')).toHaveCount(0);
  });

  test('pauses swap guidance when explicit lineup readiness is missing', async ({ page }) => {
    test.skip(
      !isLocalBundle,
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

  test('production Today smoke keeps matchup and advice visible', async ({ page }) => {
    test.skip(isLocalBundle, 'Railway smoke runs only against the deployed production app.');

    await page.goto('/');
    await waitForAppMount(page);
    await skipIfAttentionQueueNotDeployed(page);

    const matchup = page.getByText(/Matchup · (Leading|Trailing|Tied)/i).first();
    const hotSwaps = page.getByText('Hot Swaps', { exact: true }).first();
    const attention = page.getByText('Attention Queue', { exact: true }).first();

    await expect(matchup).toBeVisible();
    await expect(hotSwaps).toBeVisible();
    await expect(attention).toBeVisible();
    await expect(page.getByText(/first snapshot was empty/i)).toHaveCount(0);

    if (process.env.GITHUB_EVENT_NAME === 'push') {
      const yOf = async (locator: ReturnType<Page['locator']>) => {
        const box = await locator.boundingBox();
        expect(box).not.toBeNull();
        return box!.y;
      };
      expect(await yOf(matchup)).toBeLessThan(await yOf(hotSwaps));
    }
  });
});
