import { test, expect } from '@playwright/test';
import { waitForAppMount, gotoTab } from '../fixtures/sandlot';

const snapshot = {
  team_name: 'Zach Sandlot',
  team_id: 'team-zach',
  snapshot_id: 'snapshot-skipper-web-test',
  taken_at: '2026-06-21T12:00:00Z',
  freshness: { state: 'fresh', age_minutes: 0 },
  roster_meta: {},
  standings: [],
  roster: [
    { id: 'hudson', name: 'Bryan Hudson', positions: 'SP/RP', team: 'CHW', slot: 'RES', fppg: 2.89, fpts: 104 },
  ],
  player_index: [
    { id: 'hudson', name: 'Bryan Hudson', team: 'CHW', positions: 'SP/RP', source: 'mine' },
  ],
  matchup: null,
  data_quality: { projection_ready: false, recommendations_ready: false },
};

test.describe('Skipper web fallback', () => {
  test('sends the web_search setting with Skipper messages', async ({ page }) => {
    const posts: any[] = [];

    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshot) });
    });
    await page.route('**/api/skipper/options', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          default_model: 'deepseek/deepseek-v4-flash',
          models: [{ id: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', short: 'DS Flash' }],
          reasoning: { default_enabled: false, default_effort: 'medium', efforts: ['medium'] },
          web_search: { available: true, default_enabled: true, tool: 'openrouter:web_search' },
        }),
      });
    });
    await page.route('**/api/waiver-swaps/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cards: [], brief: { state: 'missing' }, message: 'No swaps.' }),
      });
    });
    await page.route('**/api/skipper/messages', async route => {
      const req = route.request();
      if (req.method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ session_id: 1, messages: [] }),
        });
        return;
      }
      posts.push(req.postDataJSON());
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'data: {"type":"token","text":"ack"}',
          '',
          'data: {"type":"sources","sources":[{"url":"https://www.mlb.com/player/martin-perez-527048","title":"Martin Perez Stats"}]}',
          '',
          'data: {"type":"done","model":"test","web_search_requested":true,"web_search":true,"web_search_requests":1}',
          '',
          '',
        ].join('\n'),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Skipper');

    const input = page.getByPlaceholder(/ask about your roster/i);
    const webFallback = page.getByRole('button', { name: /Web fallback on/i });
    await expect(webFallback).toBeVisible();
    await expect(webFallback).toBeInViewport();
    await expect(page.getByRole('button', { name: /Reasoning off/i })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible();

    await input.fill('Can web verify Martin Perez?');
    await input.press('Enter');
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].web_search).toBe(true);
    await expect(page.getByText('Web sources')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Martin Perez Stats' })).toHaveAttribute('href', 'https://www.mlb.com/player/martin-perez-527048');

    await webFallback.click();
    await expect(page.getByRole('button', { name: /Web fallback off/i })).toBeVisible();

    await input.fill('Snapshot only this time.');
    await input.press('Enter');
    await expect.poll(() => posts.length).toBe(2);
    expect(posts[1].web_search).toBe(false);
  });

  test('hides the web fallback toggle when the server disables web search', async ({ page }) => {
    await page.route('**/api/snapshot/latest', async route => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshot) });
    });
    await page.route('**/api/skipper/options', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          default_model: 'deepseek/deepseek-v4-flash',
          models: [{ id: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', short: 'DS Flash' }],
          reasoning: { default_enabled: false, default_effort: 'medium', efforts: ['medium'] },
          web_search: { available: false, default_enabled: false, tool: 'openrouter:web_search' },
        }),
      });
    });
    await page.route('**/api/waiver-swaps/latest', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cards: [], brief: { state: 'missing' }, message: 'No swaps.' }),
      });
    });
    await page.route('**/api/skipper/messages', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 1, messages: [] }),
      });
    });

    await page.goto('/');
    await waitForAppMount(page);
    await gotoTab(page, 'Skipper');

    await expect(page.getByRole('button', { name: /Web fallback/i })).toHaveCount(0);
  });
});
