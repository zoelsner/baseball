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
    outcome_state: 'pending',
    generated_at: '2026-07-12T14:45:00Z',
    expires_at: '2026-07-13T23:59:00Z',
    read_only: true,
    fantrax_changed: false,
    writes_enabled: false,
    ...overrides,
  };
}

async function mockBase(page: Page) {
  await page.route('**/api/snapshot/latest', route => route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(snapshot) }));
  await page.route('**/api/recommendation-receipts/latest', route => route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(receipt()) }));
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

    const card = page.getByRole('region', { name:'A measurable plan for next week' });
    await expect(card.getByText(/^\+21\.8/)).toBeVisible();
    await expect(card.getByText('Bench Bat (OF)', { exact:true })).toBeVisible();
    await expect(card.getByText('Current Starter', { exact:true })).toBeVisible();
    await expect(card.getByText(/Open Sandlot on your Mac with the local owner bridge/)).toBeVisible();
    await expect(card.getByRole('button', { name:'I’ll use this lineup' })).toHaveCount(0);
    await expect(card.getByRole('button', { name:'Ask Skipper about this plan' })).toBeVisible();
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

    const card = page.getByRole('region', { name:'A measurable plan for next week' });
    await card.getByRole('button', { name:'I’ll use this lineup' }).click();
    await expect(card.getByText('Decision recorded. You still need to set this lineup in Fantrax yourself.')).toBeVisible();
    expect(posted.body).toEqual({ decision:'accepted', input_hash:HASH });
    expect(posted.headers['x-sandlot-bridge-nonce']).toBe('local-nonce');
    await expect(card.getByText('Using this plan')).toBeVisible();
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

    const card = page.getByRole('region', { name:'A measurable plan for next week' });
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

    const card = page.getByRole('region', { name:'A measurable plan for next week' });
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
    const card = page.getByRole('region', { name:'A measurable plan for next week' });
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

    const card = page.getByRole('region', { name:'A measurable plan for next week' });
    await card.getByRole('button', { name:'I’ll use this lineup' }).click();
    await expect.poll(() => decisionStarted).toBe(true);
    await page.getByRole('button', { name:'Refresh Fantrax data' }).click();
    await expect.poll(() => reads).toBeGreaterThanOrEqual(2);
    await expect(card.getByText('Decision recorded. You still need to set this lineup in Fantrax yourself.')).toBeVisible();
    await page.waitForTimeout(550);
    await expect(card.getByText('Using this plan')).toBeVisible();
    await expect(card.getByRole('button', { name:'I’ll use this lineup' })).toHaveCount(0);
    finishRefresh?.();
  });
});
