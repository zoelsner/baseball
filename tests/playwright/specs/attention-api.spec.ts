import { test, expect } from '@playwright/test';

// GET /api/attention (#64): the machine surface Zo Computer polls. This spec
// validates the response contract against the live deploy — no UI involved.

const KINDS = ['status', 'lineup', 'output', 'replacement'];
const SEVERITIES = ['urgent', 'check', 'review'];
const SUPPORTED_ACTIONS = ['move_to_il', 'add_free_agent', 'drop_player', 'change_slot'];
const ACTION_REQUEST_FIELDS = ['action', 'player_id', 'to_slot', 'confirm_player_name', 'move_out_player_id'];

test.describe('GET /api/attention', () => {
  test('returns the machine-readable attention queue contract', async ({ request }) => {
    const res = await request.get('/api/attention');
    test.skip(res.status() === 404, 'Target deploy does not have /api/attention yet.');
    test.skip(res.status() === 503, 'Target deploy has no successful snapshot (or no DB).');

    expect(res.status()).toBe(200);
    const body = await res.json();

    expect(body.snapshot_id).toBeTruthy();
    expect(['fresh', 'stale', 'old', 'missing']).toContain(body.freshness?.state);
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.items.length).toBeLessThanOrEqual(6);

    let previousPriority = Infinity;
    for (const item of body.items) {
      expect(KINDS).toContain(item.kind);
      expect(SEVERITIES).toContain(item.severity);
      expect(typeof item.title).toBe('string');
      expect(typeof item.reason).toBe('string');
      expect(Array.isArray(item.chips)).toBe(true);
      expect(item.chips.length).toBeLessThanOrEqual(3);

      // Ordered queue: priority must be non-increasing.
      expect(item.priority).toBeLessThanOrEqual(previousPriority);
      previousPriority = item.priority;

      // Executable items must be ready to submit to POST /api/actions as-is.
      expect(Array.isArray(item.actions)).toBe(true);
      for (const payload of item.actions) {
        expect(SUPPORTED_ACTIONS).toContain(payload.action);
        expect(typeof payload.player_id).toBe('string');
        expect(payload.player_id.length).toBeGreaterThan(0);
        for (const key of Object.keys(payload)) {
          expect(ACTION_REQUEST_FIELDS).toContain(key);
        }
      }
      if (item.action !== null && item.action !== undefined) {
        expect(item.actions).toHaveLength(1);
        expect(item.action).toEqual(item.actions[0]);
      }
    }
  });
});

test.describe('GET /api/hot-swaps/latest', () => {
  test('returns the read-only hot-swap proposal contract', async ({ request }) => {
    const res = await request.get('/api/hot-swaps/latest');
    test.skip(res.status() === 404, 'Target deploy does not have /api/hot-swaps/latest yet.');
    test.skip(res.status() === 503, 'Target deploy has no successful snapshot (or no DB).');

    expect(res.status()).toBe(200);
    const body = await res.json();

    expect(body.snapshot_id).toBeTruthy();
    expect(['ready', 'paused', 'none']).toContain(body.state);
    expect(body.writes_enabled).toBe(false);
    expect(['fresh', 'stale', 'old', 'missing']).toContain(body.freshness?.state);
    expect(Array.isArray(body.proposals)).toBe(true);

    if (body.state === 'paused') {
      expect(typeof body.paused_reason).toBe('string');
      expect(body.proposals).toHaveLength(0);
    }

    for (const entry of body.proposals) {
      expect(entry.proposal?.status).toBe('blocked');
      expect(entry.proposal?.writes_enabled).toBe(false);
      expect(entry.proposal?.confirmation_required).toBe(true);
      expect(Array.isArray(entry.proposal?.safety_checks)).toBe(true);
      expect(entry.blocked_action?.state).toBe('blocked');
      expect(entry.source_item?.kind).toBe('replacement');
      expect(entry.replacement?.type).toBe('lineup_hot_swap');
    }
  });
});
