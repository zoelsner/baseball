import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

async function mockTradeAdvisor(page: import('@playwright/test').Page) {
  await page.route('**/api/trades/incoming', route => route.fulfill({
    status:200, contentType:'application/json',
    body:JSON.stringify({ snapshot_id:321, offers:[], read_only:true, fantrax_changed:false, writes_enabled:false }),
  }));
  await page.route('http://127.0.0.1:8765/health', route => route.fulfill({
    status:200, contentType:'application/json', body:JSON.stringify({ ok:false }),
  }));
  await page.route('**/api/recommendation-receipts/latest', route => route.fulfill({ status:204 }));
  await page.route('**/api/recommendation-learning', route => route.fulfill({
    status:200,
    contentType:'application/json',
    body:JSON.stringify({ summary:{ scored:0, accepted_and_observed:0 }, evidence_checkpoint:{ requirements:[] }, autopilot_eligible:false }),
  }));
  await page.route('**/api/skipper/options', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) }));
  await page.route('**/api/waiver-swaps/latest', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ brief: { state: 'missing' } }) }));
  await page.route('**/api/skipper/messages', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ messages: [] }) }));
  await page.route('**/api/snapshot/latest', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        snapshot_id: 321,
        team_id: 'me',
        team_name: 'Sandlot',
        freshness: { state: 'fresh', age_minutes: 1 },
        roster: [{ id: 'm1', name: 'My Second Baseman', slot: '2B', positions: '2B', fppg: 2.0 }],
        standings: [{ team_id: 'me', team_name: 'Sandlot', rank: 1, fantasy_points: 100 }],
        player_index: [
          { id: 'm1', name: 'My Second Baseman', source: 'mine', slot: '2B', positions: '2B', team: 'ME', fppg: 2.0 },
          { id: 'o1', name: 'Their Outfielder', source: 'league', slot: 'OF', positions: 'OF', team: 'OPP', fppg: 1.5 },
        ],
      }),
    });
  });
  await page.route('**/api/trades/grade', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        snapshot_id: 321,
        letter_grade: 'C',
        headline: 'Current-rate deficit · lower FP/G',
        fairness: 0.9,
        my_delta: -0.5,
        their_delta: 0.5,
        age_delta: 1,
        my_give: [{ id:'m1', name:'My Second Baseman' }],
        my_get: [{ id:'o1', name:'Their Outfielder' }],
        my_weakest_position: '2B',
        receipt: {
          receipt_id: `trade-assessment:${'b'.repeat(64)}`,
          input_hash: 'b'.repeat(64), source: 'trade_cockpit', action_type: 'trade_assessment',
          lifecycle_state: 'active', decision_state: 'pending', read_only: true,
          expires_at: '2099-07-13T00:00:00Z',
          trade: {
            give:[{ player_id:'m1', player_name:'My Second Baseman' }],
            get:[{ player_id:'o1', player_name:'Their Outfielder' }],
            guardrails:{ manual_execution_only:true, fantrax_write_authorized:false },
          },
          fantrax_changed: false, writes_enabled: false,
        },
        rationale: 'The current snapshot rate favors the other roster.',
        counters: [{
          tier: 'balanced', acceptance_band: 'balanced', my_delta: 0.5,
          give: [{ id: 'm1', name: 'My Second Baseman' }],
          get: [{ id: 'o1', name: 'Their Outfielder' }, { id: 'o2', name: 'Their Second Baseman' }],
          rationale: 'Adds 2B help while preserving a fair current-rate package.',
        }],
        analysis: {
          recommendation: { action: 'counter', title: 'Counter before accepting', detail: 'Adds 2B help while preserving a fair current-rate package.' },
          horizons: [
            { key: 'current_rate', label: 'Current rate', status: 'modeled', value: -0.5, unit: 'FP/G', detail: 'Net change from current snapshot scoring rates.' },
            { key: 'this_week', label: 'This week', status: 'unavailable', value: null, unit: null, detail: 'Weekly games and lineup usage are not modeled yet.' },
            { key: 'rest_of_season', label: 'Rest of season', status: 'unavailable', value: null, unit: null, detail: 'Rest-of-season playing time is not modeled yet.' },
            { key: 'dynasty', label: 'Dynasty', status: 'limited', value: 1, unit: 'yr avg age', detail: 'Average age is only a directional signal.' },
          ],
          roster_fit: { weakest_position: '2B', acquired_positions: ['OF'], fills_weakest_position: false, label: 'Does not directly fill 2B', detail: 'The get side covers OF, not your weakest current-rate position.' },
          recommended_counter: { tier: 'balanced' },
          skipper_prompt: 'Analyze this proposed trade: I give My Second Baseman; I get Their Outfielder. Do not claim unsupported certainty.',
          manual_only: true,
        },
      }),
    });
  });
}

async function gradeMockOffer(page: import('@playwright/test').Page) {
  await gotoTab(page, 'League');
  await page.getByRole('button', { name: /Grade an offer/i }).click();
  await page.getByRole('button', { name: 'Add player to You give', exact: true }).click();
  await page.getByRole('group', { name: 'You give player options' }).getByRole('button').first().click();
  await page.getByRole('button', { name: 'Add player to You get', exact: true }).click();
  await page.getByRole('group', { name: 'You get player options' }).getByRole('button').first().click();
  await page.getByRole('button', { name: 'Grade', exact: true }).click();
}

test.describe('Trade page', () => {
  test('loads an exact incoming Fantrax offer into the grader in one click', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.route('**/api/trades/incoming', route => route.fulfill({
      status:200, contentType:'application/json',
      body:JSON.stringify({
        snapshot_id:321, read_only:true, fantrax_changed:false, writes_enabled:false,
        offers:[{
          trade_id:'tx1', proposed_by:'Other Team', gradeable:true, manual_only:true,
          give:[{ player_id:'m1', player_name:'My Second Baseman' }],
          get:[{ player_id:'o1', player_name:'Their Outfielder' }], blocked_reasons:[],
        }],
      }),
    }));
    let submitted: any = null;
    await page.route('**/api/trades/grade', async route => {
      submitted = route.request().postDataJSON();
      await route.fallback();
    });
    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'League');
    await page.getByRole('button', { name:/Grade an offer/i }).click();

    const incoming = page.getByRole('region', { name:'Incoming offers' });
    await expect(incoming.getByText('You get Their Outfielder')).toBeVisible();
    await expect(incoming.getByText('You give My Second Baseman')).toBeVisible();
    await incoming.getByRole('button', { name:'Review exact offer' }).click();
    await expect(page.getByRole('heading', { name:'Counter before accepting' })).toBeVisible();
    expect(submitted).toEqual({ give:['m1'], get:['o1'], incoming_trade_id:'tx1', incoming_snapshot_id:321 });
    await expect(page.getByText(/never auto-accepts/i)).toBeVisible();
  });

  test('explains why an incoming draft-pick offer needs manual review', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.route('**/api/trades/incoming', route => route.fulfill({
      status:200, contentType:'application/json',
      body:JSON.stringify({
        snapshot_id:321, read_only:true, fantrax_changed:false, writes_enabled:false,
        offers:[{
          trade_id:'tx-pick', proposed_by:'Other Team', gradeable:false, manual_only:true,
          give:[{ player_id:'m1', player_name:'My Second Baseman' }], get:[],
          blocked_reasons:['draft_pick','missing_get_side'], includes_draft_pick:true, status:'pending',
        }],
      }),
    }));
    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'League');
    await page.getByRole('button', { name:/Grade an offer/i }).click();

    const incoming = page.getByRole('region', { name:'Incoming offers' });
    await expect(incoming.getByText(/Includes a draft pick.*not modeled yet/i)).toBeVisible();
    await expect(incoming.getByRole('button', { name:'Manual review required' })).toBeDisabled();
  });

  test('explains a young-player dynasty policy block without generic stale copy', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.route('**/api/trades/incoming', route => route.fulfill({
      status:200, contentType:'application/json',
      body:JSON.stringify({
        snapshot_id:321, freshness:{ state:'fresh' }, read_only:true, fantrax_changed:false, writes_enabled:false,
        offers:[{
          trade_id:'tx-young', proposed_by:'Other Team', gradeable:false, manual_only:true, status:'pending',
          give:[{ player_id:'m1', player_name:'My Second Baseman' }],
          get:[{ player_id:'o1', player_name:'Young Player' }],
          blocked_reasons:['participant_policy'], includes_draft_pick:false,
          manual_review_reason:'get player Young Player is age 24 and requires manual dynasty review',
          manual_review:{
            state:'manual_review_required',
            recommendation:{ action:'hold', title:'Hold this offer for now', detail:'Sandlot cannot safely compare the package while Young Player has unresolved dynasty value.' },
            uncertainty:{ level:'high', label:'Value withheld', detail:'A current-rate grade would overstate certainty.' },
            deadline:{ state:'unknown', label:'Not provided', fantrax_schedule_label:'Pending', detail:'No verified deadline.' },
            do_nothing:{ title:'Keep My Second Baseman', current_rate_preserved:2.0, unit:'FP/G' },
            horizons:[
              { key:'current_matchup', label:'Current matchup', status:'withheld', detail:'Current-period value withheld.' },
              { key:'rest_of_season', label:'Rest of season', status:'withheld', detail:'Future role is not verified.' },
              { key:'dynasty', label:'Dynasty', status:'manual_review', detail:'Manual dynasty valuation required.' },
            ],
            roster_consequences:{ label:'Moves out 2B; brings in OF.', detail:'Roster shape only.' },
            replacement_value:{ status:'directional', label:'Best reserve cover: Bench Bat (-0.80 FP/G vs outgoing)', detail:'Reserve-only, same-position comparison.' },
            counteroffer:{ state:'direction_only', title:'Counter direction: value the long-term assets first', detail:'Do not name an exact counter yet.' },
            blockers:[{ player_id:'o1', player_name:'Young Player', kind:'young_asset', reason:'Age 24 requires manual dynasty valuation.' }],
            skipper_prompt:'Review this exact incoming Fantrax offer. I give My Second Baseman; I get Young Player. Clearly separate verified facts from assumptions.',
            manual_only:true, read_only:true, fantrax_changed:false, writes_enabled:false,
          },
        }],
      }),
    }));
    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'League');
    await page.getByRole('button', { name:/Grade an offer/i }).click();

    const incoming = page.getByRole('region', { name:'Incoming offers' });
    const review = incoming.getByRole('region', { name:'Manual trade review' });
    await expect(review.getByRole('heading', { name:'Hold this offer for now' })).toBeVisible();
    await expect(review.getByText('Current matchup')).toBeVisible();
    await expect(review.getByText('Rest of season')).toBeVisible();
    await expect(review.getByText('Dynasty', { exact:true })).toBeVisible();
    await expect(review.getByText('Current-period value withheld.')).toBeVisible();
    await expect(review.getByText('Future role is not verified.')).toBeVisible();
    await expect(review.getByText('Manual dynasty valuation required.')).toBeVisible();
    await expect(review.getByText(/Keep My Second Baseman/)).toBeVisible();
    await expect(review.getByText(/Best reserve cover: Bench Bat/)).toBeVisible();
    await expect(review.getByText(/Counter direction: value the long-term assets first/)).toBeVisible();
    await expect(review.getByText(/no trade was accepted, rejected, or sent/i)).toBeVisible();
    await expect(incoming.getByText(/incomplete, stale, or includes terms/i)).toHaveCount(0);
    await review.getByRole('button', { name:'Ask Skipper to pressure-test it' }).click();
    await expect(page.getByPlaceholder('Ask about your roster, waivers, matchups...')).toHaveValue(/I give My Second Baseman; I get Young Player/);
  });

  test('discards an in-flight incoming grade when the Fantrax snapshot refreshes', async ({ page }) => {
    await mockTradeAdvisor(page);
    let snapshotId = 321;
    await page.route('**/api/snapshot/latest', route => route.fulfill({
      status:200, contentType:'application/json', body:JSON.stringify({
        snapshot_id:snapshotId, team_id:'me', team_name:'Sandlot', freshness:{ state:'fresh', age_minutes:1 },
        roster:[{ id:'m1', name:'My Second Baseman', slot:'2B', positions:'2B', fppg:2.0 }],
        standings:[{ team_id:'me', team_name:'Sandlot', rank:1, fantasy_points:100 }],
        player_index:[
          { id:'m1', name:'My Second Baseman', source:'mine', slot:'2B', positions:'2B', team:'ME', fppg:2.0 },
          { id:'o1', name:'Their Outfielder', source:'league', slot:'OF', positions:'OF', team:'OPP', fppg:1.5 },
        ],
      }),
    }));
    await page.route('**/api/trades/incoming', route => route.fulfill({
      status:200, contentType:'application/json', body:JSON.stringify({
        snapshot_id:snapshotId, freshness:{ state:'fresh' }, read_only:true, fantrax_changed:false, writes_enabled:false,
        offers:[{ trade_id:'tx-race', proposed_by:'Other Team', gradeable:true, give:[{ player_id:'m1', player_name:'My Second Baseman' }], get:[{ player_id:'o1', player_name:'Their Outfielder' }] }],
      }),
    }));
    let releaseGrade!: () => void;
    const gradeGate = new Promise<void>(resolve => { releaseGrade = resolve; });
    await page.route('**/api/trades/grade', async route => {
      await gradeGate;
      await route.fulfill({
        status:200, contentType:'application/json', body:JSON.stringify({
          my_give:[{ id:'m1', name:'My Second Baseman' }], my_get:[{ id:'o1', name:'Their Outfielder' }],
          letter_grade:'A', fairness:1, my_delta:1, their_delta:-1,
          analysis:{ recommendation:{ title:'Old snapshot result' }, horizons:[] },
        }),
      });
    });
    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'League');
    await page.getByRole('button', { name:/Grade an offer/i }).click();
    await page.getByRole('button', { name:'Review exact offer' }).click();
    await expect(page.getByText(/Reviewing the exact offer against snapshot 321/i)).toBeVisible();

    snapshotId = 322;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page.getByRole('alert')).toContainText(/Fantrax data refreshed/i);
    releaseGrade();
    await page.waitForTimeout(100);
    await expect(page.getByRole('heading', { name:'Old snapshot result' })).toHaveCount(0);
  });



  test('renders both player pickers and a disabled Grade CTA', async ({ page }) => {
    await page.goto('/');
    await waitForAppMount(page);

    const tradeTab = page.getByRole('button', { name: 'Trade', exact: true });
    if (await tradeTab.count()) {
      await tradeTab.click();
    } else {
      await gotoTab(page, 'League');
      await page.getByRole('button', { name: /Grade an offer/i }).click();
    }

    // Both V2PlayerPicker labels.
    await expect(page.getByText(/^You give$/i)).toBeVisible();
    await expect(page.getByText(/^You get$/i)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Grade an offer', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add player to You give', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add player to You get', exact: true })).toBeVisible();

    // The Grade CTA exists. With no players picked it should not be ready —
    // V2Primary renders the helper subtitle "Pick at least one player on each side".
    await expect(page.getByText(/Pick at least one player on each side/i)).toBeVisible();

    // A trade is not gradeable until both sides have at least one player.
    // Preserve that as a real native disabled state, not only an in-handler guard.
    await expect(page.getByRole('button', { name: 'Grade', exact: true })).toBeDisabled();

    const addGive = page.getByRole('button', { name: 'Add player to You give', exact: true });
    const addGiveBox = await addGive.boundingBox();
    expect(addGiveBox).not.toBeNull();
    expect(addGiveBox!.height).toBeGreaterThanOrEqual(40);
    await addGive.click();
    await expect(page.getByRole('textbox', { name: 'Search players for You give', exact: true })).toBeFocused();
  });

  test('separates trade horizons and carries the exact offer into Skipper', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('pageerror', error => consoleErrors.push(error.message));
    await page.setViewportSize({ width: 390, height: 860 });
    await mockTradeAdvisor(page);
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    await expect(page.getByRole('heading', { name: 'Counter before accepting' })).toBeVisible();
    await expect(page.getByText('-0.50 FP/G', { exact: true })).toBeVisible();
    await expect(page.getByText('Not modeled', { exact: true })).toHaveCount(2);
    await expect(page.getByText('Does not directly fill 2B', { exact: true })).toBeVisible();
    await expect(page.getByText(/never auto-accepts/i)).toBeVisible();
    await page.getByRole('button', { name: 'Ask Skipper', exact: true }).click();
    await expect(page.getByPlaceholder(/Ask about your roster/i)).toHaveValue(/I give My Second Baseman; I get Their Outfielder/i);
    expect(consoleErrors).toEqual([]);
  });

  test('hides stale analysis as soon as the reviewed offer is edited', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    await expect(page.getByRole('heading', { name: 'Counter before accepting' })).toBeVisible();
    await page.getByRole('button', { name: 'Edit offer', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Counter before accepting' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Grade', exact: true })).toBeVisible();
  });

  test('records exact trade intent through the owner bridge without a Fantrax write', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.route('http://127.0.0.1:8765/health', route => route.fulfill({
      status:200, contentType:'application/json',
      body:JSON.stringify({ ok:true, mode:'dry_run', writes_enabled:false, recommendation_decisions_enabled:true, nonce:'trade-nonce' }),
    }));
    let submitted: any = null;
    await page.route('http://127.0.0.1:8765/recommendation-receipts/**/decision', async route => {
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status:200, contentType:'application/json',
        body:JSON.stringify({
          receipt_id:`trade-assessment:${'b'.repeat(64)}`, input_hash:'b'.repeat(64),
          source:'trade_cockpit', action_type:'trade_assessment', lifecycle_state:'active',
          decision_state:'accepted', fantrax_changed:false, writes_enabled:false, changed:true,
        }),
      });
    });
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    const receipt = page.getByRole('region', { name:'Exact trade decision' });
    await expect(receipt.getByText(/never accepts, rejects, or counters in Fantrax/i)).toBeVisible();
    await receipt.getByRole('button', { name:'Record intent to accept' }).click();
    await expect(receipt.getByText(/Intent to accept recorded.*never accepts/i)).toBeVisible();
    expect(submitted).toEqual({ decision:'accepted', input_hash:'b'.repeat(64) });
  });

  test('blocks an expired or mismatched exact trade receipt before owner intent', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.route('**/api/trades/grade', route => route.fulfill({
      status:200, contentType:'application/json', body:JSON.stringify({
        snapshot_id:321, letter_grade:'C', fairness:0.9, my_delta:-0.5, their_delta:0.5,
        my_give:[{ id:'m1', name:'My Second Baseman' }], my_get:[{ id:'o1', name:'Their Outfielder' }],
        analysis:{ recommendation:{ title:'Review this offer' }, horizons:[] },
        receipt:{
          receipt_id:`trade-assessment:${'c'.repeat(64)}`, input_hash:'c'.repeat(64),
          action_type:'trade_assessment', decision_state:'pending', expires_at:'2020-01-01T00:00:00Z',
          trade:{ give:[{ player_id:'wrong', player_name:'Wrong Player' }], get:[{ player_id:'o1', player_name:'Their Outfielder' }], guardrails:{ manual_execution_only:true, fantrax_write_authorized:false } },
        },
      }),
    }));
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    const receipt = page.getByRole('region', { name:'Exact trade decision' });
    await expect(receipt.getByText(/does not match the displayed offer/i)).toBeVisible();
    await expect(receipt.getByRole('button', { name:'Record intent to accept' })).toHaveCount(0);
  });

  test('blocks a matching trade receipt after its deadline', async ({ page }) => {
    await mockTradeAdvisor(page);
    await page.route('http://127.0.0.1:8765/health', route => route.fulfill({
      status:200, contentType:'application/json', body:JSON.stringify({ ok:true, mode:'dry_run', writes_enabled:false, recommendation_decisions_enabled:true, nonce:'expiry-nonce' }),
    }));
    let submissions = 0;
    await page.route('http://127.0.0.1:8765/recommendation-receipts/**/decision', route => {
      submissions += 1;
      return route.fulfill({ status:500, contentType:'application/json', body:'{}' });
    });
    const expiresAt = new Date(Date.now() + 1_500).toISOString();
    await page.route('**/api/trades/grade', route => route.fulfill({
      status:200, contentType:'application/json', body:JSON.stringify({
        snapshot_id:321, letter_grade:'C', fairness:0.9, my_delta:-0.5, their_delta:0.5,
        my_give:[{ id:'m1', name:'My Second Baseman' }], my_get:[{ id:'o1', name:'Their Outfielder' }],
        analysis:{ recommendation:{ title:'Review this offer' }, horizons:[] },
        receipt:{
          receipt_id:`trade-assessment:${'d'.repeat(64)}`, input_hash:'d'.repeat(64),
          action_type:'trade_assessment', decision_state:'pending', expires_at:expiresAt,
          trade:{ give:[{ player_id:'m1', player_name:'My Second Baseman' }], get:[{ player_id:'o1', player_name:'Their Outfielder' }], guardrails:{ manual_execution_only:true, fantrax_write_authorized:false } },
        },
      }),
    }));
    await page.goto('/');
    await waitForAppMount(page);
    await gradeMockOffer(page);

    const receipt = page.getByRole('region', { name:'Exact trade decision' });
    const accept = receipt.getByRole('button', { name:'Record intent to accept' });
    await expect(accept).toBeVisible();
    await page.waitForTimeout(1_600);
    await accept.click();
    await expect(receipt.getByRole('alert')).toContainText(/assessment expired/i);
    expect(submissions).toBe(0);
  });
});
