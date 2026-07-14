import { test, expect, Page } from '@playwright/test';
import { waitForAppMount } from '../fixtures/sandlot';

const HASH = 'a'.repeat(64);
const RECEIPT_ID = `monday-lineup:${HASH}`;

const snapshot = {
  snapshot_id: 277,
  taken_at: '2026-07-12T14:40:58Z',
  freshness: { state:'fresh', age_minutes:0 },
  league_name: 'Test League',
  team_name: 'My Team',
  roster: [
    { id:'starter', name:'Current Starter', slot:'OF', positions:'OF', team:'NYY', fppg:4.1, slot_source:'raw.statusId' },
    { id:'bench', name:'Bench Bat', slot:'RES', positions:'OF', team:'LAD', fppg:5.2, slot_source:'raw.statusId' },
  ],
  matchup: null,
};

function receipt(decisionState = 'pending', overrides: Record<string, any> = {}) {
  return {
    receipt_id: RECEIPT_ID,
    input_hash: HASH,
    source: 'monday_lineup',
    action_type: 'lineup_plan',
    period: { start:'2026-07-13', end:'2026-07-19' },
    evaluation: { metric_name:'projected_points', metric_unit:'points', baseline_value:179.4, projected_value:201.2, projected_gain:21.8 },
    baseline_assignment: [{ slot:'OF', player_id:'starter', player_name:'Current Starter', projected_points:4.1 }],
    proposed_assignment: [{ slot:'OF', player_id:'bench', player_name:'Bench Bat', projected_points:5.2 }],
    unfilled_slots: [],
    evidence: { snapshot_id:277, snapshot_taken_at:'2026-07-12T14:40:58Z' },
    lifecycle_state: 'active',
    decision_state: decisionState,
    reconciliation: {
      state:decisionState === 'rejected' ? 'skipped' : 'awaiting',
      snapshot_id:291,
      snapshot_taken_at:'2026-07-14T13:02:52Z',
      applied_count:0,
      total_changes:2,
      applied_changes:[],
      remaining_changes:[],
      fantrax_changed_by_sandlot:false,
    },
    outcome_state: 'pending',
    generated_at: '2026-07-12T14:45:00Z',
    expires_at: '2026-07-13T23:59:00Z',
    read_only: true,
    fantrax_changed: false,
    writes_enabled: false,
    ...overrides,
  };
}

const emptyLearningReport = {
  scoring_version:'counterfactual_lineup_v1',
  measurement_scope:'retrospective_static_lineup_counterfactual',
  counterfactual_gain_available:false,
  sample_state:'collecting',
  summary:{
    evaluated:0, scored:0, unavailable:0, accepted_and_observed:0,
    actual_assignment_matches:{ proposed:0, baseline:0, other:0 },
    average_counterfactual_gain:null,
    positive_counterfactual_gain_rate:null,
  },
  evidence_checkpoint:{
    state:'collecting', minimum_sample_reached:false,
    requirements:[
      { key:'scored_evaluations', current:0, required:8, passed:false },
      { key:'accepted_and_observed', current:0, required:4, passed:false },
    ],
  },
  items:[], read_only:true, fantrax_changed:false, autopilot_eligible:false,
};

async function mockBase(page: Page) {
  await page.route('**/api/snapshot/latest', route => route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(snapshot) }));
  await page.route('**/api/recommendation-receipts/latest', route => route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(receipt()) }));
  await page.route('**/api/recommendation-learning', route => route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(emptyLearningReport) }));
}

test.describe('Today recommendation receipt', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(process.env.SANDLOT_EXPECT_RECEIPT !== '1', 'Receipt UI is verified against the rebuilt local bundle.');
    await mockBase(page);
  });

  test('shows measurable plan and honest desktop-owner-only state when bridge is offline', async ({ page }) => {
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await expect(card.getByText(/^\+21\.8/)).toBeVisible();
    await expect(card.getByText('Bench Bat (OF)', { exact:true })).toBeVisible();
    await expect(card.getByText('Current Starter', { exact:true })).toBeVisible();
    await expect(card.getByText(/First start the local owner bridge on this Mac/)).toBeVisible();
    await expect(card.getByRole('button', { name:'I’ll use this lineup' })).toHaveCount(0);
    await expect(card.getByRole('link', { name:'Review on this Mac · bridge required' })).toHaveAttribute(
      'href',
      `http://127.0.0.1:8765/recommendation-receipts/${encodeURIComponent(RECEIPT_ID)}/review?input_hash=${HASH}`,
    );
    await expect(card.getByRole('button', { name:'Ask Skipper about this plan' })).toBeVisible();
  });

  test('shows exact period and partial live completion without stale decision controls', async ({ page }) => {
    await page.unroute('**/api/recommendation-receipts/latest');
    await page.route('**/api/recommendation-receipts/latest', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify(receipt('pending', {
        baseline_assignment:[
          { slot:'OF', player_id:'cortes', player_name:'Carlos Cortes', projected_points:3 },
          { slot:'UT', player_id:'other', player_name:'Other Starter', projected_points:4 },
        ],
        proposed_assignment:[
          { slot:'OF', player_id:'lile', player_name:'Daylen Lile', projected_points:7 },
          { slot:'UT', player_id:'upgrade', player_name:'Other Upgrade', projected_points:6 },
        ],
        reconciliation:{
          state:'partially_applied', snapshot_id:291, snapshot_taken_at:'2026-07-14T13:02:52Z',
          applied_count:2, total_changes:4,
          applied_changes:[
            { player_id:'lile', player_name:'Daylen Lile', proposed_slot:'OF', observed_slot:'OF', matches_proposed:true },
            { player_id:'cortes', player_name:'Carlos Cortes', proposed_slot:'RES', observed_slot:'RES', matches_proposed:true },
          ],
          remaining_changes:[], fantrax_changed_by_sandlot:false,
        },
      })),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await expect(card.getByText('7-day lineup receipt', { exact:true })).toBeVisible();
    await expect(card.getByText('Partially applied · 2/4', { exact:true })).toBeVisible();
    await expect(card.getByText(/confirms 2 of 4 planned assignment changes: Daylen Lile → OF; Carlos Cortes → RES/)).toBeVisible();
    await expect(card.getByRole('button', { name:'I’ll use this lineup' })).toHaveCount(0);
    await expect(card.getByRole('button', { name:'Pass' })).toHaveCount(0);
  });

  test('shows the empty learning gate without implying that autopilot is available', async ({ page }) => {
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Learning from completed weeks' });
    await expect(card.getByText('Autopilot locked', { exact:true })).toBeVisible();
    await expect(card.getByText('No eligible completed lineup receipts yet.')).toBeVisible();
    await expect(card.getByText('0/8', { exact:true })).toBeVisible();
    await expect(card.getByText('0/4', { exact:true })).toBeVisible();
    await expect(card.getByRole('progressbar', { name:'Scored weeks evidence progress' })).toHaveAttribute('aria-valuenow', '0');
    await expect(card.getByRole('progressbar', { name:'Accepted + observed evidence progress' })).toHaveAttribute('aria-valuemax', '4');
    await expect(card.getByText(/does not prove causality, execute Fantrax moves, or grant write authority/)).toBeVisible();
  });

  test('shows sanitized early evidence as hindsight rather than realized lift', async ({ page }) => {
    await page.unroute('**/api/recommendation-learning');
    await page.route('**/api/recommendation-learning', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({
        ...emptyLearningReport,
        counterfactual_gain_available:true,
        summary:{
          ...emptyLearningReport.summary,
          evaluated:3, scored:2, unavailable:1, accepted_and_observed:1,
          actual_assignment_matches:{ proposed:1, baseline:1, other:0 },
          average_counterfactual_gain:5.5,
          positive_counterfactual_gain_rate:0.5,
        },
        evidence_checkpoint:{
          ...emptyLearningReport.evidence_checkpoint,
          requirements:[
            { key:'scored_evaluations', current:2, required:8, passed:false },
            { key:'accepted_and_observed', current:1, required:4, passed:false },
          ],
        },
      }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Early lineup evidence' });
    await expect(card.getByText('+5.5', { exact:true })).toBeVisible();
    await expect(card.getByText('avg hindsight edge', { exact:true })).toBeVisible();
    await expect(card.getByText('2/8', { exact:true })).toBeVisible();
    await expect(card.getByText('1/4', { exact:true })).toBeVisible();
    await expect(card.getByText('Autopilot locked', { exact:true })).toBeVisible();
  });

  test('carries only sanitized learning evidence into an unsent Skipper draft', async ({ page }) => {
    await page.unroute('**/api/recommendation-learning');
    await page.route('**/api/recommendation-learning', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({
        ...emptyLearningReport,
        summary:{
          ...emptyLearningReport.summary,
          evaluated:3, scored:2, unavailable:1, accepted_and_observed:1,
          average_counterfactual_gain:5.5,
        },
        evidence_checkpoint:{
          ...emptyLearningReport.evidence_checkpoint,
          requirements:[
            { key:'scored_evaluations', current:2, required:8, passed:false },
            { key:'accepted_and_observed', current:1, required:4, passed:false },
          ],
        },
        autopilot:{ state:'locked', eligible:false },
      }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    await page.getByRole('button', { name:'Ask Skipper what Sandlot learned' }).click();
    const draft = page.getByPlaceholder(/Ask about your roster/i);
    await expect(draft).toHaveValue(/2 of 8 scored weeks/);
    await expect(draft).toHaveValue(/1 of 4 accepted-and-observed plans/);
    await expect(draft).toHaveValue(/Average retrospective static-lineup edge: \+5\.5 points/);
    await expect(draft).toHaveValue(/Autopilot state: locked; eligible: no/);
    await expect(draft).toHaveValue(/Do not propose or perform a Fantrax write/);
    await expect(draft).not.toHaveValue(/Bench Bat|Current Starter|Aaron Judge/);
  });

  test('announces a learning-report failure without changing automation state', async ({ page }) => {
    await page.unroute('**/api/recommendation-learning');
    await page.route('**/api/recommendation-learning', route => route.fulfill({
      status:503, contentType:'application/json', body:JSON.stringify({ detail:'temporarily unavailable' }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    const alert = page.getByRole('alert');
    await expect(alert).toContainText('Learning report unavailable');
    await expect(alert).toContainText('No automation state changed');
  });

  test('retains the newest learning report when snapshot refresh responses overlap', async ({ page }) => {
    let reads = 0;
    await page.unroute('**/api/recommendation-learning');
    await page.route('**/api/recommendation-learning', async route => {
      reads += 1;
      const average = reads === 1 ? 1.0 : 9.0;
      if (reads === 1) await new Promise(resolve => setTimeout(resolve, 450));
      await route.fulfill({
        status:200,
        contentType:'application/json',
        body:JSON.stringify({
          ...emptyLearningReport,
          summary:{ ...emptyLearningReport.summary, scored:2, accepted_and_observed:1, average_counterfactual_gain:average },
          evidence_checkpoint:{
            ...emptyLearningReport.evidence_checkpoint,
            requirements:[
              { key:'scored_evaluations', current:2, required:8, passed:false },
              { key:'accepted_and_observed', current:1, required:4, passed:false },
            ],
          },
        }),
      });
    });
    await page.route('**/api/refresh', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ snapshot:{ ...snapshot, snapshot_id:278, freshness:{ state:'fresh', age_minutes:1 } } }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);
    await page.getByRole('button', { name:'Refresh Fantrax data' }).click();

    const card = page.getByRole('region', { name:'Early lineup evidence' });
    await expect(card.getByText('+9.0', { exact:true })).toBeVisible();
    await page.waitForTimeout(550);
    await expect(card.getByText('+9.0', { exact:true })).toBeVisible();
    expect(reads).toBe(2);
  });

  test('does not refetch learning evidence when only the freshness label changes', async ({ page }) => {
    let reads = 0;
    await page.unroute('**/api/recommendation-learning');
    await page.route('**/api/recommendation-learning', route => {
      reads += 1;
      return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(emptyLearningReport) });
    });
    await page.route('**/api/refresh', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ snapshot:{ ...snapshot, freshness:{ state:'fresh', age_minutes:1 } } }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);
    await expect(page.getByRole('region', { name:'Learning from completed weeks' })).toBeVisible();
    await page.getByRole('button', { name:'Refresh Fantrax data' }).click();
    await expect(page.getByRole('region', { name:'Learning from completed weeks' })).toBeVisible();
    expect(reads).toBe(1);
  });

  test('keeps prior evidence visible and labels a failed snapshot revalidation', async ({ page }) => {
    let reads = 0;
    await page.unroute('**/api/recommendation-learning');
    await page.route('**/api/recommendation-learning', route => {
      reads += 1;
      if (reads > 1) {
        return route.fulfill({ status:503, contentType:'application/json', body:JSON.stringify({ detail:'temporarily unavailable' }) });
      }
      return route.fulfill({
        status:200,
        contentType:'application/json',
        body:JSON.stringify({
          ...emptyLearningReport,
          summary:{ ...emptyLearningReport.summary, scored:2, accepted_and_observed:1, average_counterfactual_gain:5.5 },
          evidence_checkpoint:{
            ...emptyLearningReport.evidence_checkpoint,
            requirements:[
              { key:'scored_evaluations', current:2, required:8, passed:false },
              { key:'accepted_and_observed', current:1, required:4, passed:false },
            ],
          },
        }),
      });
    });
    await page.route('**/api/refresh', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ snapshot:{ ...snapshot, snapshot_id:278, freshness:{ state:'fresh', age_minutes:1 } } }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);
    const card = page.getByRole('region', { name:'Early lineup evidence' });
    await expect(card.getByText('+5.5', { exact:true })).toBeVisible();
    await page.getByRole('button', { name:'Refresh Fantrax data' }).click();

    await expect(card.getByText('+5.5', { exact:true })).toBeVisible();
    await expect(card.getByRole('status')).toHaveText(/Couldn’t update — showing previous evidence/);
    expect(reads).toBe(2);
  });

  test('records the exact accepted decision through the local bridge without a Fantrax write', async ({ page }) => {
    let posted: any = null;
    await page.route('http://127.0.0.1:8765/health', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ ok:true, mode:'dry_run', writes_enabled:false, recommendation_decisions_enabled:true, nonce:'local-nonce' }),
    }));
    await page.route('http://127.0.0.1:8765/recommendation-receipts/**/decision', async route => {
      posted = { headers:route.request().headers(), body:route.request().postDataJSON() };
      await route.fulfill({
        status:200,
        contentType:'application/json',
        body:JSON.stringify({ ...receipt('accepted'), changed:true }),
      });
    });
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await card.getByRole('button', { name:'I’ll use this lineup' }).click();
    await expect(card.getByText('Intent recorded, but the latest Fantrax snapshot does not yet confirm any planned assignment change.')).toBeVisible();
    expect(posted.body).toEqual({ decision:'accepted', input_hash:HASH });
    expect(posted.headers['x-sandlot-bridge-nonce']).toBe('local-nonce');
    await expect(card.getByText('Accepted · not yet confirmed')).toBeVisible();
  });

  test('keeps active-to-active moves out of the bench list', async ({ page }) => {
    await page.unroute('**/api/recommendation-receipts/latest');
    await page.route('**/api/recommendation-receipts/latest', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify(receipt('pending', {
        baseline_assignment:[{ slot:'OF', player_id:'judge', player_name:'Aaron Judge', projected_points:30 }],
        proposed_assignment:[{ slot:'UT', player_id:'judge', player_name:'Aaron Judge', projected_points:30 }],
      })),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await expect(card.getByText('Aaron Judge (OF → UT)', { exact:true })).toBeVisible();
    await expect(card.getByText('Keep the current starters', { exact:true })).toBeVisible();
    await expect(card.getByText('No additional bench moves', { exact:true })).toBeVisible();
    await expect(card.getByText('Aaron Judge', { exact:true })).toHaveCount(0);
  });

  test('refetches and warns instead of applying a stale decision', async ({ page }) => {
    let latestReads = 0;
    await page.unroute('**/api/recommendation-receipts/latest');
    await page.route('**/api/recommendation-receipts/latest', route => {
      latestReads += 1;
      return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(receipt()) });
    });
    await page.route('http://127.0.0.1:8765/health', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ ok:true, mode:'dry_run', writes_enabled:false, recommendation_decisions_enabled:true, nonce:'local-nonce' }),
    }));
    await page.route('http://127.0.0.1:8765/recommendation-receipts/**/decision', route => route.fulfill({
      status:409,
      contentType:'application/json',
      body:JSON.stringify({ detail:'Recommendation receipt has been superseded' }),
    }));
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await card.getByRole('button', { name:'Pass' }).click();
    await expect(card.getByRole('alert')).toContainText('A newer recommendation is available');
    expect(latestReads).toBeGreaterThanOrEqual(2);
    await expect(card.getByText('Pass recorded. Sandlot will retain this decision for outcome analysis.')).toHaveCount(0);
  });

  test('does not let an older receipt response overwrite a newer refresh', async ({ page }) => {
    const newerHash = 'b'.repeat(64);
    const newer = receipt('pending', {
      receipt_id:`monday-lineup:${newerHash}`,
      input_hash:newerHash,
      evaluation:{ metric_name:'projected_points', metric_unit:'points', baseline_value:180, projected_value:224.4, projected_gain:44.4 },
    });
    let reads = 0;
    await page.unroute('**/api/recommendation-receipts/latest');
    await page.route('**/api/recommendation-receipts/latest', async route => {
      reads += 1;
      if (reads === 2) {
        await new Promise(resolve => setTimeout(resolve, 450));
        return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(receipt()) });
      }
      return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(reads >= 3 ? newer : receipt()) });
    });
    await page.route('**/api/refresh', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ snapshot:{ ...snapshot, freshness:{ state:'fresh', age_minutes:1 } } }),
    }));
    await page.route('http://127.0.0.1:8765/health', route => route.abort());
    await page.goto('/');
    await waitForAppMount(page);

    await page.getByRole('button', { name:'Refresh Fantrax data' }).click();
    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await expect(card.getByText(/^\+44\.4/)).toBeVisible();
    await page.waitForTimeout(550);
    await expect(card.getByText(/^\+44\.4/)).toBeVisible();
    expect(reads).toBeGreaterThanOrEqual(3);
  });

  test('does not let an in-flight decision read revert a committed acceptance', async ({ page }) => {
    let reads = 0;
    let serverDecision = 'pending';
    let decisionStarted = false;
    let finishRefresh: (() => void) | null = null;
    await page.unroute('**/api/recommendation-receipts/latest');
    await page.route('**/api/recommendation-receipts/latest', async route => {
      reads += 1;
      const responseBody = receipt(serverDecision);
      if (reads === 2) await new Promise(resolve => setTimeout(resolve, 450));
      await route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(responseBody) });
    });
    await page.route('**/api/refresh', async route => {
      await new Promise<void>(resolve => {
        finishRefresh = () => {
          route.fulfill({
            status:200,
            contentType:'application/json',
            body:JSON.stringify({ snapshot:{ ...snapshot, freshness:{ state:'fresh', age_minutes:1 } } }),
          }).finally(resolve);
        };
      });
    });
    await page.route('http://127.0.0.1:8765/health', route => route.fulfill({
      status:200,
      contentType:'application/json',
      body:JSON.stringify({ ok:true, mode:'dry_run', writes_enabled:false, recommendation_decisions_enabled:true, nonce:'local-nonce' }),
    }));
    await page.route('http://127.0.0.1:8765/recommendation-receipts/**/decision', route => {
      decisionStarted = true;
      return new Promise(resolve => setTimeout(resolve, 180)).then(() => {
        serverDecision = 'accepted';
        return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify({ ...receipt('accepted'), changed:true }) });
      });
    });
    await page.goto('/');
    await waitForAppMount(page);

    const card = page.getByRole('region', { name:'Lineup plan · Jul 13–Jul 19' });
    await card.getByRole('button', { name:'I’ll use this lineup' }).click();
    await expect.poll(() => decisionStarted).toBe(true);
    await page.getByRole('button', { name:'Refresh Fantrax data' }).click();
    await expect.poll(() => reads).toBeGreaterThanOrEqual(2);
    await expect(card.getByText('Intent recorded, but the latest Fantrax snapshot does not yet confirm any planned assignment change.')).toBeVisible();
    await page.waitForTimeout(550);
    await expect(card.getByText('Accepted · not yet confirmed')).toBeVisible();
    await expect(card.getByRole('button', { name:'I’ll use this lineup' })).toHaveCount(0);
    finishRefresh?.();
  });
});
