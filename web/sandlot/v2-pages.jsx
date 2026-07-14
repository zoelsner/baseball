// v2 — Card direction merged with Soft Cream tokens.
// Cream surface, generous padding, hairline-thin internal dividers,
// segmented pill controls, soft-tinted selection states, large rounded primaries.
import React from 'react';
import {
  STATUS_LABEL,
  Icons,
  Avatar,
  PlayerPhoto,
  buildPlayerNameIndex,
} from './atoms.jsx';

// ── Tokens ─────────────────────────────────────────────────────
const V2 = {
  bg:       '#efe8dc',
  surface:  '#fffaf2',
  surface2: '#f1e8da',

  ink:      '#0f172a',
  body:     '#334155',
  muted:    '#64748b',

  hairline: '#e2d7c6',
  hairline2:'#eadfce',

  accent:    '#df7042',
  accentSoft:'#f8dfce',

  inLineup:    '#2563eb', inLineupSoft:'#dbe7fe',
  bench:       '#0f9d58', benchSoft:'#dcf2e3',
  injured:     '#dc2626', injuredSoft:'#fde2e1',
  empty:       '#94a3b8', emptySoft:'#eef1f5',

  ok:'#0f9d58', okSoft:'#dcf2e3',
  warn:'#df7042', warnSoft:'#f8dfce',
  bad:'#dc2626', badSoft:'#fde2e1',

  font: '"Inter",system-ui,-apple-system,sans-serif',
  fontDisplay: '"Source Serif 4","Inter",serif',
  fontMono: '"JetBrains Mono","Roboto Mono",ui-monospace,monospace',
};

const V2_SKIPPER_MODELS = [
  { id:'deepseek/deepseek-v4-flash', label:'DeepSeek V4 Flash', short:'DS Flash' },
  { id:'moonshotai/kimi-k2', label:'Kimi K2', short:'Kimi' },
  { id:'deepseek/deepseek-v4-pro', label:'DeepSeek V4 Pro', short:'DS Pro' },
  { id:'z-ai/glm-5.2', label:'GLM 5.2', short:'GLM 5.2' },
];
const V2_SKIPPER_DEFAULT_MODEL = 'deepseek/deepseek-v4-flash';
const V2_INACTIVE_SLOTS = ['BN', 'BE', 'BENCH', 'IL', 'IR', 'RES', 'RESERVE', 'MIN', 'MINORS'];
const V2_BENCH_SLOTS = ['BN', 'BE', 'BENCH', 'RES', 'RESERVE', 'MIN', 'MINORS'];
const V2_OWNER_BRIDGE_URL = 'http://127.0.0.1:8765';
const V2_EXECUTION_TERMINAL_STATES = ['preflight_passed', 'preflight_failed', 'expired', 'cancelled'];

// Preference persistence helpers removed in #36 — Sandlot does not persist UI
// state to `window.localStorage` (global rule in CLAUDE.md). Skipper model +
// reasoning toggle now reset to defaults on each page load. The refresh-token
// read at v2RefreshHeaders is intentionally retained: that's reading a
// manually-set config value (per the 401 error copy in v2RefreshErrorMessage),
// not persisting app state.

function v2StateColor(state){
  if (state==='ok')      return { fg:V2.inLineup, bg:V2.inLineupSoft, label:'In lineup' };
  if (state==='bench')   return { fg:V2.bench,    bg:V2.benchSoft,    label:'Bench' };
  if (state==='injured') return { fg:V2.injured,  bg:V2.injuredSoft,  label:'Injured' };
  return { fg:V2.empty, bg:V2.emptySoft, label:'Empty' };
}
function v2PlayerState(p) {
  if (!p) return 'empty';
  const status = String(p.status || p.injury || '').toLowerCase();
  const slot = String(p.slot || '').toUpperCase();
  if (['il10','il60','ir','out','dtd','susp'].includes(status)) return 'injured';
  if (['IL','IR'].includes(slot)) return 'injured';
  if (V2_BENCH_SLOTS.includes(slot)) return 'bench';
  return 'ok';
}

function v2EmptyModel() {
  return {
    source: 'empty',
    sync: { state:'loading', label:'loading', ageMinutes:null, error:null, notice:null },
    teamName: 'Your team',
    leagueName: '',
    roster: [],
    rosterMeta: {},
    leagueTeams: [],
    snapshotId: null,
    takenAt: null,
    playerIndex: [],
    matchup: null,
    winThisWeek: null,
    dataQuality: null,
  };
}

function v2NormalizeSkipperOptions(payload) {
  const models = Array.isArray(payload?.models)
    ? payload.models
        .filter(m => m?.id)
        .map(m => ({
          id: String(m.id),
          label: String(m.label || m.id),
          short: String(m.short || m.label || m.id),
        }))
    : [];
  const webSearch = payload?.web_search || {};
  return {
    defaultModel: payload?.default_model || V2_SKIPPER_DEFAULT_MODEL,
    models: models.length ? models : V2_SKIPPER_MODELS,
    webSearch: {
      defaultEnabled: webSearch.default_enabled !== false,
      available: webSearch.available !== false,
    },
  };
}

function v2BuildSwapSkipperPrompt(card) {
  const add = card?.add || {};
  const out = card?.move_out || {};
  const chips = (card?.evidence_chips || []).join(', ') || 'none';
  const net = v2Signed(card?.net_delta, 1);
  return [
    'Help me pressure-test this waiver swap before I touch Fantrax.',
    '',
    `Proposed swap: add ${add.name || 'Unknown free agent'} (${add.positions || 'UT'}${add.team ? `, ${add.team}` : ''}) and move out ${out.name || 'Unknown roster player'} (${out.positions || 'UT'}${out.team ? `, ${out.team}` : ''}).`,
    `Estimated delta: ${net} FP/G. Confidence: ${card?.confidence || 'Medium'}.`,
    `Evidence chips: ${chips}.`,
    card?.why ? `Why Sandlot likes it: ${card.why}` : null,
    card?.risk ? `Risk: ${card.risk}` : null,
    card?.dynasty_note ? `Dynasty note: ${card.dynasty_note}` : null,
    '',
    'Use the latest roster snapshot. Tell me what data you trust, what you do not trust, what I should manually verify in Fantrax, and whether this is still worth doing.',
  ].filter(Boolean).join('\n');
}

function v2BuildLineupSwapSkipperPrompt(card, mode='quick') {
  const moveIn = card?.move_in || {};
  const moveOut = card?.move_out || {};
  const benefit = card?.projected_benefit || {};
  const proposal = card?.proposal || {};
  const evidenceLabel = benefit.probability_calibrated === true
    ? `Confidence: ${card?.confidence || 'unknown'}`
    : `Point-edge strength: ${card?.confidence || 'unknown'}`;
  const deep = mode === 'deep';
  return [
    deep
      ? 'Run a deep research review before I touch this lineup swap.'
      : 'Pressure-test this lineup-only hot swap before I touch Fantrax.',
    '',
    `Move IN: ${moveIn.name || 'Unknown player'} (${moveIn.positions || 'UT'}${moveIn.team ? `, ${moveIn.team}` : ''}) from ${moveIn.from_slot || '?'} to ${moveIn.to_slot || '?'}.`,
    `Move OUT: ${moveOut.name || 'Unknown player'} (${moveOut.positions || 'UT'}${moveOut.team ? `, ${moveOut.team}` : ''}) from ${moveOut.from_slot || '?'} to ${moveOut.to_slot || '?'}.`,
    proposal.id ? `Proposal: ${proposal.id} (${proposal.status || 'blocked'}; writes enabled: ${proposal.writes_enabled === true ? 'yes' : 'no'}).` : null,
    `Projected benefit: ${v2Signed(benefit.points, 1)} points. ${evidenceLabel}. Risk: ${card?.risk_label || 'unknown'}.`,
    card?.reason ? `Sandlot reason: ${card.reason}` : null,
    card?.short_term_outlook ? `Short-term outlook: ${card.short_term_outlook}` : null,
    card?.risk ? `Risk note: ${card.risk}` : null,
    '',
    deep
      ? 'Use roster context plus web search if needed. Verify probable starts, schedule/games this week, injuries, role changes, Fantrax scoring relevance, and whether this is worth proposing. Separate snapshot-verified facts from web/contextual assumptions.'
      : 'Use the latest roster snapshot. Tell me what data you trust, what is uncertain, and whether the proposed lineup-only swap is worth considering.',
  ].filter(Boolean).join('\n');
}

// Shared mapper so user roster + per-team roster stay byte-identical.
function v2NormalizeRosterRow(p, idx) {
  const positions = Array.isArray(p.all_positions) && p.all_positions.length
    ? p.all_positions.filter(Boolean).join('/')
    : (p.positions || p.pos || 'UT');
  const slot = p.slot || p.slot_full || 'BN';
  const status = (p.injury || p.status || '').toString().toLowerCase();
  const fppg = v2Number(p.fppg);
  const fpts = v2Number(p.fpts);
  return {
    id: p.id || `${p.name || 'player'}-${idx}`,
    name: p.name || 'Unknown player',
    pos: positions,
    team: p.team || '',
    slot,
    slotSource: p.slot_source || p.slotSource || null,
    fppg,
    fpts,
    proj: fppg || 0,
    vsExp: 0,
    status,
    injury: p.injury || null,
    age: p.age,
    opp: '',
    trend: 'steady',
    alert: null,
    raw: p,
  };
}

function v2NormalizeSnapshot(payload) {
  const freshness = payload?.freshness || {};
  const roster = (payload?.roster || []).filter(Boolean).map(v2NormalizeRosterRow);
  const leagueTeams = (payload?.standings || []).map((t, idx) => ({
    id: t.team_id || `${t.team_name || 'team'}-${idx}`,
    name: t.team_name || 'Unknown team',
    mgr: t.owner || t.manager || '',
    rank: v2Number(t.rank) || idx + 1,
    pts: v2Number(t.fantasy_points) || v2Number(t.points_for) || 0,
    record: [t.win, t.loss, t.tie].filter(v => v !== undefined && v !== null).join('-') || '—',
    streak: t.streak || '',
    me: t.team_id && payload?.team_id ? t.team_id === payload.team_id : t.team_name === payload?.team_name,
    raw: t,
  }));
  return {
    source: 'api',
    sync: {
      state: freshness.state || 'fresh',
      label: v2SyncLabel(freshness),
      ageMinutes: freshness.age_minutes ?? null,
      error: null,
      notice: null,
    },
    teamName: payload?.team_name || 'Your team',
    leagueName: '',
    roster,
    rosterMeta: payload?.roster_meta || {},
    leagueTeams,
    snapshotId: payload?.snapshot_id || null,
    takenAt: payload?.taken_at || null,
    playerIndex: payload?.player_index || [],
    matchup: payload?.matchup || null,
    winThisWeek: payload?.win_this_week || null,
    dataQuality: payload?.data_quality || null,
  };
}

function v2Number(value) {
  if (value === null || value === undefined || value === '') return 0;
  const n = Number(String(value).replace(/,/g, ''));
  return Number.isFinite(n) ? n : 0;
}

function v2SyncLabel(freshness) {
  const mins = freshness?.age_minutes;
  if (mins === null || mins === undefined) return 'fresh';
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function v2FreshnessStateForAge(ageMinutes, fallback='fresh') {
  if (!Number.isFinite(ageMinutes)) return fallback;
  const derived = ageMinutes < 18 * 60 ? 'fresh' : ageMinutes < 36 * 60 ? 'stale' : 'old';
  const rank = { fresh:0, stale:1, old:2 };
  return (rank[fallback] ?? 0) > rank[derived] ? fallback : derived;
}

function v2SyncTone(sync={}) {
  if (sync.state === 'failed' || sync.state === 'missing' || sync.state === 'old') {
    return {
      color:V2.bad,
      bg:V2.badSoft,
      label:sync.state === 'old' ? 'Old' : sync.state === 'missing' ? 'Missing' : 'Failed',
    };
  }
  if (sync.state === 'stale' || sync.state === 'refreshing' || sync.state === 'loading' || sync.notice) {
    return {
      color:V2.warn,
      bg:V2.warnSoft,
      label:sync.state === 'stale' ? 'Stale' : sync.state === 'refreshing' ? 'Refreshing' : sync.state === 'loading' ? 'Loading' : 'Needs attention',
    };
  }
  return { color:V2.ok, bg:V2.okSoft, label:'Healthy' };
}

function v2QualityReason(dataQuality, purpose='projection') {
  if (!dataQuality) return 'Data quality is unavailable';
  const reasonKeys = {
    projection: ['projection_reasons'],
    recommendation: ['recommendation_reasons'],
    lineup: ['lineup_recommendation_reasons', 'recommendation_reasons'],
    add_drop: ['add_drop_recommendation_reasons', 'recommendation_reasons'],
  }[purpose] || ['projection_reasons'];
  const reasons = reasonKeys
    .flatMap(key => dataQuality[key] || [])
    .concat(dataQuality.reasons || [])
    .filter(Boolean);
  if (!reasons.length) return 'Required snapshot data is available';
  const first = String(reasons[0]).replace(/\.$/, '');
  return reasons.length === 1 ? first : `${first}, plus ${reasons.length - 1} more`;
}

function v2LineupAdviceReady(dataQuality) {
  if (!dataQuality) return false;
  return dataQuality.lineup_slots?.state === 'ok' && dataQuality.lineup_recommendations_ready === true;
}

function v2LineupQualityReason(dataQuality) {
  if (!dataQuality) return 'Data quality is unavailable';
  const reason = v2QualityReason(dataQuality, 'lineup');
  if (reason !== 'Required snapshot data is available') return reason;
  if (dataQuality.lineup_recommendations_ready !== true) {
    return 'Lineup recommendation readiness is not explicitly trusted';
  }
  return dataQuality.lineup_slots?.reason || 'Lineup-slot provenance is unavailable';
}

// ── Reusable controls ──────────────────────────────────────────

function V2Segment({ items, value, onChange, full=true }) {
  return (
    <div style={{
      display:'flex', background:V2.surface2, borderRadius:999, padding:4,
      border:`1px solid ${V2.hairline2}`,
      width: full ? '100%' : 'auto',
    }}>
      {items.map(it => {
        const v = typeof it==='string'?it:it.value;
        const l = typeof it==='string'?it:it.label;
        const active = v === value;
        return (
          <button key={v} onClick={()=>onChange(v)} style={{
            flex: full?1:'0 0 auto',
            padding:'7px 14px', borderRadius:999, border:'none',
            background: active ? V2.surface : 'transparent',
            color: active ? V2.ink : V2.muted,
            fontSize:12.5, fontWeight: active?700:600, cursor:'pointer',
            fontFamily:'inherit',
            boxShadow: active ? '0 1px 2px rgba(26,26,26,0.06)' : 'none',
          }}>{l}</button>
        );
      })}
    </div>
  );
}

function V2Primary({ children, onClick, sub, variant='dark', disabled=false }) {
  const bg = variant==='accent' ? V2.accent : V2.ink;
  return (
    <div>
      <button onClick={onClick} disabled={disabled} style={{
        width:'100%', padding:'15px 18px', borderRadius:999, border:'none',
        background:bg, color:'#fff', fontSize:15, fontWeight:700,
        cursor:disabled?'not-allowed':'pointer', fontFamily:'inherit',
        opacity:disabled?0.55:1,
        display:'flex', alignItems:'center', justifyContent:'center', gap:8,
      }}>{children}</button>
      {sub && <div style={{ textAlign:'center', fontSize:12, color:V2.muted, marginTop:10, fontWeight:500 }}>{sub}</div>}
    </div>
  );
}

function V2Caution({ eyebrow='Caution', children, tone='warn' }) {
  const fg = tone==='warn'?V2.warn:tone==='ok'?V2.ok:V2.accent;
  const bg = tone==='warn'?V2.warnSoft:tone==='ok'?V2.okSoft:V2.accentSoft;
  return (
    <div style={{ background:bg, borderLeft:`2px solid ${fg}`, borderRadius:'4px 12px 12px 4px', padding:'12px 14px' }}>
      <div style={{ fontSize:10.5, color:fg, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase', marginBottom:4 }}>{eyebrow}</div>
      <div style={{ fontSize:12.5, color:V2.body, lineHeight:1.55 }}>{children}</div>
    </div>
  );
}

function V2StatRow({ stats }) {
  return (
    <div style={{ display:'grid', gridTemplateColumns:`repeat(${stats.length}, 1fr)`, paddingTop:6 }}>
      {stats.map((s,i)=>(
        <div key={i} style={{
          padding:'4px 8px', textAlign:'center',
          borderLeft: i===0?'none':`1px solid ${V2.hairline2}`,
        }}>
          <div style={{ fontSize:18, fontWeight:700, color:s.color||V2.ink, fontVariantNumeric:'tabular-nums', letterSpacing:'-0.01em', fontFamily:V2.fontDisplay }}>{s.value}</div>
          <div style={{ fontSize:10.5, color:V2.muted, fontWeight:600, marginTop:2 }}>{s.label}</div>
        </div>
      ))}
    </div>
  );
}

function V2Eyebrow({ children, color }) {
  return (
    <div style={{ fontSize:10.5, color:color||V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase' }}>
      {children}
    </div>
  );
}

// Shared by initial-load auto-retry and the manual refresh button.
function v2RefreshErrorMessage(payload, status) {
  const detail = payload?.detail ?? payload ?? {};
  if (status === 401) return 'Refresh token missing or invalid. Set localStorage.sandlot_refresh_token and try again.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail?.errors) && detail.errors.length) return detail.errors.join('; ');
  if (detail?.error) return detail.error;
  if (detail?.status) return `Refresh ${detail.status}`;
  return `Refresh failed (${status})`;
}

function v2RefreshHeaders() {
  let token = '';
  try { token = window.localStorage.getItem('sandlot_refresh_token') || ''; } catch {}
  token = token.trim();
  return token ? { 'x-refresh-token': token } : undefined;
}

async function v2FetchRefresh() {
  const headers = v2RefreshHeaders();
  const res = await fetch('/api/refresh', headers ? { method:'POST', headers } : { method:'POST' });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload?.detail || {};
    const fallbackPayload = detail?.snapshot || payload?.snapshot || null;
    const error = new Error(v2RefreshErrorMessage(payload, res.status));
    if (fallbackPayload) error.fallbackSnapshot = v2NormalizeSnapshot(fallbackPayload);
    error.fallbackReason = detail?.fallback_reason || null;
    throw error;
  }
  return v2NormalizeSnapshot(payload.snapshot);
}

// ── App shell ──────────────────────────────────────────────────
function V2App({ initial }) {
  const [page, setPageRaw] = React.useState(initial?.page || 'today');
  const [detail, setDetail] = React.useState(initial?.detail || null); // player id or null
  const [leagueTeam, setLeagueTeam] = React.useState(null); // { id, name, mgr, ... } or null
  const [authed, setAuthed] = React.useState(initial?.auth ? false : true);
  const [model, setModel] = React.useState(v2EmptyModel);
  const [syncState, setSyncState] = React.useState({ state:'loading', label:'loading', error:null, notice:null });
  const [skipperDraft, setSkipperDraft] = React.useState(null);
  const mainScrollRef = React.useRef(null);
  const snapshotReadInFlightRef = React.useRef(false);
  const refreshInFlightRef = React.useRef(false);
  const snapshotRequestSeqRef = React.useRef(0);
  const syncAgeAnchorRef = React.useRef(null);

  const setPage = React.useCallback((next) => {
    if (next !== 'league') setLeagueTeam(null);
    setPageRaw(next);
  }, []);

  const acceptSnapshot = React.useCallback((snapshot) => {
    const rawAgeMinutes = snapshot?.sync?.ageMinutes;
    const ageMinutes = rawAgeMinutes === null || rawAgeMinutes === undefined ? NaN : Number(rawAgeMinutes);
    syncAgeAnchorRef.current = Number.isFinite(ageMinutes)
      ? { ageMinutes, observedAt:Date.now() }
      : null;
    setModel(snapshot);
    setSyncState({ ...snapshot.sync });
  }, []);

  const loadSnapshot = React.useCallback(async ({ silent=false }={}) => {
    if (snapshotReadInFlightRef.current || (silent && refreshInFlightRef.current)) return;
    snapshotReadInFlightRef.current = true;
    const requestSeq = ++snapshotRequestSeqRef.current;
    if (!silent) setSyncState({ state:'loading', label:'loading', error:null, notice:null });

    // Step 1: read the latest stored snapshot.
    let snapshot = null;
    let firstPullError = null;
    try {
      const res = await fetch('/api/snapshot/latest');
      if (!res.ok) throw new Error(res.status === 404 ? 'No snapshot yet' : `Snapshot failed (${res.status})`);
      snapshot = v2NormalizeSnapshot(await res.json());
    } catch (err) {
      firstPullError = err.message;
    }

    if (requestSeq !== snapshotRequestSeqRef.current) {
      snapshotReadInFlightRef.current = false;
      return;
    }

    // An empty roster from a "successful" snapshot is just as broken as a failed pull.
    const firstPullEmpty = snapshot && snapshot.roster.length === 0;
    if (snapshot && !firstPullEmpty) {
      acceptSnapshot(snapshot);
      snapshotReadInFlightRef.current = false;
      return;
    }

    snapshotReadInFlightRef.current = false;
    if (silent) return;

    // Step 2: surface the broken/missing snapshot without auto-running the scraper.
    const reason = firstPullError || 'first snapshot was empty';
    if (snapshot) setModel(snapshot);
    setSyncState({
      state:'failed',
      label:snapshot?.sync?.label || 'no data',
      ageMinutes:snapshot?.sync?.ageMinutes ?? null,
      error:reason,
      notice:snapshot ? 'Showing the latest stored Fantrax pull.' : null,
    });
  }, [acceptSnapshot]);

  const refreshSnapshot = React.useCallback(async () => {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    const requestSeq = ++snapshotRequestSeqRef.current;
    setSyncState(s => ({ ...s, state:'refreshing', label:'syncing', error:null, notice:null }));
    try {
      const next = await v2FetchRefresh();
      if (requestSeq !== snapshotRequestSeqRef.current) return;
      acceptSnapshot(next);
    } catch (err) {
      if (requestSeq !== snapshotRequestSeqRef.current) return;
      if (err.fallbackSnapshot) {
        const rawAgeMinutes = err.fallbackSnapshot?.sync?.ageMinutes;
        const ageMinutes = rawAgeMinutes === null || rawAgeMinutes === undefined ? NaN : Number(rawAgeMinutes);
        syncAgeAnchorRef.current = Number.isFinite(ageMinutes)
          ? { ageMinutes, observedAt:Date.now() }
          : syncAgeAnchorRef.current;
        setModel(err.fallbackSnapshot);
      }
      setSyncState(current => ({
        state:'failed',
        label:err.fallbackSnapshot?.sync?.label || current.label || 'failed',
        ageMinutes:err.fallbackSnapshot?.sync?.ageMinutes ?? current.ageMinutes ?? null,
        error:err.message,
        notice:err.fallbackSnapshot ? (err.fallbackReason || 'Showing the last successful Fantrax pull.') : null,
      }));
    } finally {
      refreshInFlightRef.current = false;
    }
  }, [acceptSnapshot]);

  React.useEffect(() => { loadSnapshot(); }, [loadSnapshot]);

  const tickSyncAge = React.useCallback(() => {
    const anchor = syncAgeAnchorRef.current;
    if (!anchor) return;
    const elapsedMinutes = Math.max(0, Math.floor((Date.now() - anchor.observedAt) / 60000));
    const ageMinutes = anchor.ageMinutes + elapsedMinutes;
    setSyncState(current => {
      if (current.state === 'loading' || current.state === 'refreshing') return current;
      const state = ['fresh','stale','old'].includes(current.state)
        ? v2FreshnessStateForAge(ageMinutes, current.state)
        : current.state;
      const label = v2SyncLabel({ age_minutes:ageMinutes });
      if (current.ageMinutes === ageMinutes && current.state === state && current.label === label) return current;
      return { ...current, state, label, ageMinutes };
    });
  }, []);

  React.useEffect(() => {
    const timer = window.setInterval(tickSyncAge, 60000);
    return () => window.clearInterval(timer);
  }, [tickSyncAge]);

  React.useEffect(() => {
    const refreshStoredSnapshot = () => {
      if (document.visibilityState === 'hidden') return;
      tickSyncAge();
      loadSnapshot({ silent:true });
    };
    window.addEventListener('focus', refreshStoredSnapshot);
    document.addEventListener('visibilitychange', refreshStoredSnapshot);
    return () => {
      window.removeEventListener('focus', refreshStoredSnapshot);
      document.removeEventListener('visibilitychange', refreshStoredSnapshot);
    };
  }, [loadSnapshot, tickSyncAge]);

  const primaryActionDeadline = model?.winThisWeek?.actions?.[0]?.deadline?.at || null;
  React.useEffect(() => {
    if (!primaryActionDeadline) return undefined;
    const deadlineMs = new Date(primaryActionDeadline).getTime();
    if (!Number.isFinite(deadlineMs)) return undefined;
    const refreshInMs = Math.max(0, deadlineMs - Date.now() + 1000);
    const timer = window.setTimeout(
      () => loadSnapshot({ silent:true }),
      Math.min(refreshInMs, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [loadSnapshot, primaryActionDeadline]);

  React.useLayoutEffect(() => {
    if (mainScrollRef.current) mainScrollRef.current.scrollTop = 0;
  }, [page]);

  const openPlayer = React.useCallback((id) => {
    if (!id) return;
    setPage('roster');
    setDetail(id);
  }, []);

  const continueInSkipper = React.useCallback((prompt, options={}) => {
    if (!prompt) return;
    setSkipperDraft({
      id:Date.now(), text:prompt,
      autoSend:options.autoSend === true,
      reasoning:options.reasoning === true,
      reasoningEffort:options.reasoningEffort || null,
      webSearch:options.webSearch === true,
    });
    setPage('skipper');
  }, []);

  if (!authed) return <V2Auth onSignIn={()=>setAuthed(true)}/>;

  const pages = {
    today:   <V2Today model={model} sync={syncState} onRefresh={refreshSnapshot} onNav={setPage} onPlayer={setDetail} onAskSkipper={continueInSkipper}/>,
    roster:  <V2Roster model={model} onPlayer={setDetail}/>,
    league:  leagueTeam
      ? <V2TeamRoster teamId={leagueTeam.id} teamMeta={leagueTeam} onBack={()=>setLeagueTeam(null)} onPlayer={setDetail}/>
      : <V2League model={model} onOpenTeam={setLeagueTeam} onOpenTrade={()=>setPage('trade')}/>,
    fa:      <V2FreeAgents onOpenPlayer={openPlayer} onAskSkipper={continueInSkipper}/>,
    trade:   <V2TradeGrader model={model} onAskSkipper={continueInSkipper}/>,
    skipper: <V2Skipper model={model} sync={syncState} onOpenPlayer={openPlayer} draft={skipperDraft} onDraftConsumed={()=>setSkipperDraft(null)}/>,
    settings:<V2Settings model={model} sync={syncState} onRefresh={refreshSnapshot} onSignOut={()=>setAuthed(false)}/>,
  };

  return (
    <div style={{
      width:'100%', height:'100%', background:V2.bg, color:V2.ink, fontFamily:V2.font,
      display:'flex', flexDirection:'column', position:'relative',
    }}>
      <V2TopBar page={page} setPage={setPage} model={model} sync={syncState} onRefresh={refreshSnapshot}/>
      <div ref={mainScrollRef} data-testid="main-scroll" style={{ flex:1, overflow:'auto', WebkitOverflowScrolling:'touch' }}>{pages[page]}</div>
      <V2TabBar page={page} setPage={setPage}/>
      {detail && <V2PlayerSheet id={detail} onClose={()=>setDetail(null)}/>}
    </div>
  );
}

function V2TopBar({ page, setPage, model, sync, onRefresh }) {
  if (page === 'today') return null;

  const titles = {
    today:'Sandlot',
    roster:'Your roster',
    league:'The league',
    fa:'Best adds',
    trade:'Grade an offer',
    skipper:'Skipper',
    settings:'Settings',
  };
  const isHero = page==='today';
  const eyebrow = {
    today:`Fantrax snapshot · ${model.source === 'api' ? 'live data' : 'no data yet'}`,
    roster:`${model.teamName}`,
    league:`${model.leagueName} · ${model.leagueTeams.length} teams`,
    fa:'Waiver swaps · ranked from snapshot',
    trade:'Paste an offer for instant analysis',
    skipper:`Reading ${model.teamName}`,
    settings:`${model.leagueName}`,
  }[page];
  const syncTone = v2SyncTone(sync);
  return (
    <div style={{ padding: isHero?'18px 20px 16px':'16px 18px 12px', background:V2.bg }}>
      <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:10 }}>
        <div style={{ minWidth:0, flex:1 }}>
          <div style={{ fontSize:11.5, color:V2.warn, fontWeight:700, letterSpacing:'0.08em', textTransform:'uppercase' }}>
            {eyebrow}
          </div>
          <h1 style={{
            fontSize: isHero?28:22, fontWeight:600, letterSpacing:'-0.02em', marginTop:4,
            marginBottom:0, fontFamily:V2.fontDisplay, lineHeight:1.1, textWrap:'balance',
          }}>{titles[page]}</h1>
        </div>
        {page!=='settings' && (
          <button onClick={onRefresh} disabled={sync.state === 'refreshing'} aria-label={sync.state === 'refreshing' ? 'Refreshing Fantrax data' : 'Refresh Fantrax data'} title={sync.error || 'Refresh Fantrax data'} style={{
            background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:999,
            padding:'7px 11px', display:'flex', alignItems:'center', gap:7, cursor:sync.state === 'refreshing' ? 'not-allowed' : 'pointer',
            fontFamily:'inherit', flexShrink:0, marginTop:2,
            opacity: sync.state === 'refreshing' ? 0.7 : 1,
          }}>
            <div style={{ width:6, height:6, background:syncTone.color, borderRadius:'50%' }}/>
            <div style={{ fontSize:11, color:V2.body, fontWeight:600, whiteSpace:'nowrap' }}>{sync.label}</div>
          </button>
        )}
      </div>
    </div>
  );
}

function V2TabBar({ page, setPage }) {
  const items = [
    { id:'today',  label:'Today',   icon:Icons.home },
    { id:'roster', label:'Roster',  icon:Icons.list },
    { id:'skipper',label:'Skipper', icon:Icons.sparkle },
    { id:'fa',     label:'Adds',    icon:Icons.spark },
    { id:'league', label:'League',  icon:Icons.diamond },
  ];
  return (
    <nav aria-label="Primary navigation" style={{ display:'flex', borderTop:`1px solid ${V2.hairline}`, background:V2.surface, paddingBottom:18, paddingTop:8 }}>
      {items.map(it => {
        const active = page===it.id;
        return (
          <button key={it.id} onClick={()=>setPage(it.id)} aria-current={active ? 'page' : undefined} style={{
            flex:1, background:'none', border:'none', padding:'8px 4px',
            display:'flex', flexDirection:'column', alignItems:'center', gap:5,
            color: active ? V2.ink : V2.muted, cursor:'pointer', fontFamily:'inherit',
          }}>
            {it.icon(active ? V2.ink : V2.muted, 17)}
            <div style={{ fontSize:9.5, fontWeight: active ? 700 : 500 }}>{it.label}</div>
          </button>
        );
      })}
    </nav>
  );
}

// ── /auth ───────────────────────────────────────────────────────
function V2Auth({ onSignIn }) {
  const [email, setEmail] = React.useState('');
  const [sent, setSent] = React.useState(false);
  return (
    <div style={{ width:'100%', height:'100%', background:V2.bg, color:V2.ink, fontFamily:V2.font, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'40px 28px' }}>
      <div style={{ width:64, height:64, borderRadius:18, background:V2.ink, color:'#fff', display:'flex', alignItems:'center', justifyContent:'center', marginBottom:22 }}>
        {Icons.diamond('#fff', 32)}
      </div>
      <div style={{ fontSize:30, fontWeight:600, letterSpacing:'-0.02em', fontFamily:V2.fontDisplay }}>Sandlot</div>
      <div style={{ fontSize:13.5, color:V2.muted, marginTop:8, textAlign:'center', lineHeight:1.55 }}>
        Your Fantrax dynasty league,<br/>finally readable.
      </div>
      <div style={{ width:'100%', marginTop:40 }}>
        {!sent ? (
          <>
            <V2Eyebrow>Email</V2Eyebrow>
            <input
              value={email}
              onChange={e=>setEmail(e.target.value)}
              placeholder="you@example.com"
              style={{
                width:'100%', marginTop:8, padding:'14px 16px', fontSize:15,
                border:`1px solid ${V2.hairline}`, borderRadius:14, background:V2.surface,
                fontFamily:'inherit', outline:'none', color:V2.ink,
              }}/>
            <div style={{ marginTop:14 }}>
              <V2Primary onClick={()=>setSent(true)} sub="Single-user app · Supabase magic link · no password">Send magic link</V2Primary>
            </div>
          </>
        ) : (
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, padding:18, textAlign:'center' }}>
            <div style={{ fontSize:15, fontWeight:700 }}>Check your inbox</div>
            <div style={{ fontSize:12.5, color:V2.muted, marginTop:6, lineHeight:1.5 }}>
              Sent a sign-in link to <span style={{ color:V2.ink, fontWeight:600 }}>{email||'your email'}</span>.
            </div>
            <button onClick={onSignIn} style={{
              marginTop:14, padding:'10px 16px', background:V2.accent, color:'#fff',
              border:'none', borderRadius:999, fontWeight:700, fontSize:13, cursor:'pointer', fontFamily:'inherit',
            }}>(demo) Continue →</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── /today ─────────────────────────────────────────────────────
function v2StarterRows(roster) {
  return (roster || []).filter(p => {
    const slot = String(p.slot || '').toUpperCase();
    return !V2_INACTIVE_SLOTS.includes(slot);
  });
}

function v2StatusText(p) {
  if (!p) return '';
  const raw = String(p.injury || p.status || '').trim();
  const key = raw.toLowerCase();
  if (!raw || key === 'ok' || key === 'active') return 'Active';
  return STATUS_LABEL[key] || raw;
}

function v2PlayerMetric(p) {
  return v2Number(p?.proj) || v2Number(p?.fppg) || v2Number(p?.fpts) || 0;
}

function v2FormatMetric(value, digits=1) {
  const n = v2Number(value);
  if (!n) return '—';
  return n.toFixed(digits);
}

function v2MatchupInfo(matchup) {
  if (!matchup) return null;
  const hasScore = matchup.my_score !== undefined || matchup.myScore !== undefined
    || matchup.opponent_score !== undefined || matchup.oppScore !== undefined;
  if (!hasScore) return null;
  const my = v2Number(matchup.my_score ?? matchup.myScore);
  const opp = v2Number(matchup.opponent_score ?? matchup.oppScore);
  const margin = matchup.margin !== undefined && matchup.margin !== null
    ? v2Number(matchup.margin)
    : my - opp;
  const opponent = matchup.opponent_team_name || matchup.opponent || matchup.oppName || 'Opponent';
  const week = matchup.period_number || matchup.week || '';
  let daysLeft = null;
  if (matchup.end) {
    const end = new Date(`${matchup.end}T23:59:59`);
    if (!Number.isNaN(end.getTime())) {
      daysLeft = Math.max(0, Math.ceil((end.getTime() - Date.now()) / 86400000));
    }
  }
  if (daysLeft === null && matchup.daysLeft !== undefined) daysLeft = v2Number(matchup.daysLeft);
  if (daysLeft === null && matchup.days_left !== undefined) daysLeft = v2Number(matchup.days_left);
  const leading = margin > 0;
  const periodDays = (() => {
    const explicit = Number(matchup.days);
    if (Number.isFinite(explicit) && explicit > 0) return Math.round(explicit);
    if (!matchup.start || !matchup.end) return null;
    const start = new Date(`${matchup.start}T00:00:00Z`);
    const end = new Date(`${matchup.end}T00:00:00Z`);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) return null;
    return Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
  })();
  return {
    my,
    opp,
    margin,
    opponent,
    week,
    daysLeft,
    leading,
    projection:matchup.projection || null,
    periodDays,
    periodStart:matchup.start || null,
    periodEnd:matchup.end || null,
    latestCompleted:matchup.latest_completed || null,
  };
}

function v2ProjectionInfo(projection) {
  if (!projection || projection.projected_my === null || projection.projected_my === undefined
      || projection.projected_opp === null || projection.projected_opp === undefined) return null;
  const projectedMy = v2Number(projection.projected_my);
  const projectedOpp = v2Number(projection.projected_opp);
  const projectedMargin = projectedMy - projectedOpp;
  const hasProbability = projection.win_probability !== null && projection.win_probability !== undefined;
  const probabilityCalibrated = projection.probability_calibrated === true && hasProbability;
  const probability = probabilityCalibrated
    ? Math.max(0, Math.min(1, v2Number(projection.win_probability)))
    : null;
  const pct = probability === null ? null : Math.round(probability * 100);
  const color = probabilityCalibrated
    ? (pct >= 60 ? V2.ok : pct >= 45 ? V2.warn : V2.bad)
    : (projectedMargin >= 5 ? V2.ok : projectedMargin > -5 ? V2.warn : V2.bad);
  const band = probabilityCalibrated
    ? (pct >= 70 ? 'COMFORTABLE' : pct >= 55 ? 'SLIGHT EDGE' : pct > 45 ? 'TOSS-UP' : pct > 30 ? 'UPHILL' : 'STEEP UPHILL')
    : (projectedMargin >= 15 ? 'PROJECTED EDGE' : projectedMargin >= 5 ? 'PROJECTED LEAN' : projectedMargin > -5 ? 'PROJECTED TOSS-UP' : projectedMargin > -15 ? 'PROJECTED UPHILL' : 'PROJECTED DEFICIT');
  const shortBand = probabilityCalibrated
    ? (pct >= 70 ? 'EDGE' : pct >= 55 ? 'LEAN' : pct > 45 ? 'TOSS' : pct > 30 ? 'RISK' : 'LONG')
    : 'MODEL';
  return {
    band,
    shortBand,
    color,
    dash: probability === null ? null : (probability * 188.5).toFixed(1),
    projectedMyValue: projectedMy,
    projectedOppValue: projectedOpp,
    projectedMy: projectedMy.toFixed(1),
    projectedOpp: projectedOpp.toFixed(1),
    projectedMargin,
    probabilityCalibrated,
    probabilityLabel: projection.probability_calibrated === false ? 'not calibrated' : 'probability unavailable',
    complete: Boolean(projection.complete),
  };
}

function v2ProjectionContext(matchup, projectionInfo) {
  const periodDays = Number(matchup?.periodDays);
  if (!projectionInfo || !Number.isFinite(periodDays) || periodDays <= 0) return null;
  const periodDates = matchup?.periodStart && matchup?.periodEnd
    ? `${v2ShortDate(matchup.periodStart)}–${v2ShortDate(matchup.periodEnd)}`
    : null;
  const periodLabel = `${periodDays}-day scoring period${periodDates ? ` · ${periodDates}` : ''}`;
  const projectedMyDaily = projectionInfo.projectedMyValue / periodDays;
  const projectedOppDaily = projectionInfo.projectedOppValue / periodDays;
  const latest = matchup?.latestCompleted;
  const latestDays = Number(latest?.days);
  const latestMy = Number(latest?.my_score);
  const latestOpp = Number(latest?.opponent_score);
  const hasLatest = Number.isFinite(latestDays) && latestDays > 0
    && Number.isFinite(latestMy) && Number.isFinite(latestOpp);
  const paceLabel = hasLatest
    ? `≈ ${Math.round(projectedMyDaily)}–${Math.round(projectedOppDaily)} FP/day · last: ${Math.round(latestMy / latestDays)}–${Math.round(latestOpp / latestDays)}/day (${Math.round(latestMy)}–${Math.round(latestOpp)}, ${Math.round(latestDays)}d)`
    : `≈ ${Math.round(projectedMyDaily)}–${Math.round(projectedOppDaily)} FP/day`;
  const estimatedPitchers = Math.max(0, Math.round(v2Number(matchup?.projection?.pitchers_with_cadence_estimate)));
  const unmodeledPitchers = Math.max(0, Math.round(v2Number(matchup?.projection?.pitchers_without_opportunity_model)));
  const coverageParts = [
    estimatedPitchers > 0 ? `${estimatedPitchers} cadence-estimated` : null,
    unmodeledPitchers > 0 ? `${unmodeledPitchers} unmodeled` : null,
  ].filter(Boolean);
  return {
    periodLabel,
    paceLabel,
    coverageLabel: coverageParts.length
      ? `Both rosters: ${coverageParts.join(' · ')} pitcher${estimatedPitchers + unmodeledPitchers === 1 ? '' : 's'}${unmodeledPitchers > 0 ? ' · totals are partial' : ''}`
      : null,
  };
}

function V2WinProbabilityRing({ projection }) {
  const info = v2ProjectionInfo(projection);
  if (!info || !info.probabilityCalibrated) return null;
  return (
    <div style={{ width:66, height:66, position:'relative', flexShrink:0 }}>
      <svg width="66" height="66" viewBox="0 0 74 74">
        <circle cx="37" cy="37" r="30" fill="none" stroke={V2.hairline} strokeWidth="6"/>
        <circle cx="37" cy="37" r="30" fill="none" stroke={info.color} strokeWidth="6"
          strokeDasharray={`${info.dash} 188.5`} transform="rotate(-90 37 37)" strokeLinecap="round"/>
      </svg>
      <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
        <div style={{ color:info.color, fontSize:10.5, lineHeight:1, fontWeight:900, fontFamily:V2.fontMono }}>{info.shortBand}</div>
        <div style={{ marginTop:4, color:V2.muted, fontSize:7.5, lineHeight:1, fontWeight:800, letterSpacing:'0.04em' }}>EDGE</div>
      </div>
    </div>
  );
}

function v2MoveChainText(chain) {
  if (!Array.isArray(chain) || !chain.length) return 'No move detail';
  return chain.map(step => {
    const name = step.player_name || step.player_id || 'Player';
    return `${name} ${step.from_slot || '?'} -> ${step.to_slot || '?'}`;
  }).join('; ');
}

function v2MatchupEvidenceLabel(confidence, probabilityCalibrated) {
  return probabilityCalibrated === true
    ? `${confidence} confidence`
    : `${confidence} point edge`;
}

function V2MatchupRecommendationCard({ recommendations }) {
  if (!recommendations) return null;
  const top = recommendations.recommendations?.[0] || null;
  const noAction = recommendations.no_action || null;
  const chipStyle = {
    display:'inline-flex',
    alignItems:'center',
    padding:'4px 7px',
    borderRadius:999,
    background:V2.surface2,
    color:V2.muted,
    fontSize:11,
    fontWeight:800,
    whiteSpace:'nowrap',
  };
  if (top) {
    const points = v2Number(top.points_delta);
    const confidence = top.confidence || 'medium';
    const evidenceLabel = v2MatchupEvidenceLabel(confidence, top.probability_calibrated);
    const chain = top.action?.chain || [];
    return (
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'14px 15px', display:'flex', flexDirection:'column', gap:9 }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
          <V2Eyebrow color={V2.accent}>Best lineup action</V2Eyebrow>
          <span style={{ color:V2.accent, fontWeight:900, fontFamily:V2.fontMono, fontSize:12 }}>
            +{points.toFixed(1)}
          </span>
        </div>
        <div style={{ color:V2.ink, fontSize:14, lineHeight:1.35, fontWeight:800 }}>
          {v2MoveChainText(chain)}
        </div>
        <div style={{ display:'flex', gap:6, flexWrap:'wrap' }}>
          <span style={chipStyle}>{evidenceLabel}</span>
          {(top.reason_chips || []).slice(0,3).map(chip => <span key={chip} style={chipStyle}>{chip}</span>)}
        </div>
      </div>
    );
  }
  if (noAction) {
    const bestRejected = v2Number(noAction.best_rejected_delta);
    return (
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'14px 15px', display:'flex', flexDirection:'column', gap:8 }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
          <V2Eyebrow color={V2.muted}>Stable lineup</V2Eyebrow>
          {noAction.best_rejected_delta !== null && noAction.best_rejected_delta !== undefined ? (
            <span style={{ color:V2.muted, fontWeight:900, fontFamily:V2.fontMono, fontSize:12 }}>
              best {bestRejected >= 0 ? '+' : ''}{bestRejected.toFixed(1)}
            </span>
          ) : null}
        </div>
        <div style={{ color:V2.body, fontSize:13, lineHeight:1.4, fontWeight:700 }}>
          {noAction.reason || 'No compelling lineup move from this snapshot.'}
        </div>
      </div>
    );
  }
  return null;
}

function v2LowOutputCutoff(starters) {
  const values = starters.map(v2PlayerMetric).filter(n => n > 0).sort((a,b)=>a-b);
  if (!values.length) return 0;
  const median = values[Math.floor(values.length / 2)];
  return Math.max(1, median * 0.55);
}

function v2RosterHealth(model) {
  const roster = model.roster || [];
  const starters = v2StarterRows(roster);
  const cutoff = v2LowOutputCutoff(starters);
  const seen = new Set();
  const addRow = (list, p, reason, chips=[]) => {
    if (!p || seen.has(p.id)) return;
    seen.add(p.id);
    list.push({ player:p, reason, chips });
  };

  const injuryRows = [];
  const coldRows = [];
  const lineupRows = [];

  starters.forEach(p => {
    const state = v2PlayerState(p);
    const metric = v2PlayerMetric(p);
    const rawStatus = String(p.injury || p.status || '').toLowerCase();
    const isCold = p.trend === 'cold' || p.vsExp <= -1.5 || (cutoff > 0 && metric > 0 && metric <= cutoff);
    const lineupFlag = p.alert?.kind === 'not-pitching' || p.alert?.kind === 'opp-pitcher-tough' || p.mlbStarting === false || String(p.opp || '').toUpperCase() === 'OFF' || metric === 0;

    if (state === 'injured' || ['dtd', 'out', 'susp'].includes(rawStatus)) {
      addRow(injuryRows, p, v2StatusText(p), [
        v2StatusText(p),
        p.vsExp ? `${p.vsExp > 0 ? '+' : ''}${p.vsExp.toFixed(1)} vs exp` : null,
      ].filter(Boolean));
      return;
    }
    if (lineupFlag) {
      const lineupReason = p.alert?.msg || (
        String(p.opp || '').toUpperCase() === 'OFF' ? 'Off today' :
        metric === 0 ? 'No projected output' :
        'Lineup check'
      );
      addRow(lineupRows, p, lineupReason, [
        p.alert?.kind ? p.alert.kind.replace(/-/g, ' ') : null,
        metric ? `${metric.toFixed(1)} FP/G` : null,
      ].filter(Boolean));
      return;
    }
    if (isCold) {
      addRow(coldRows, p, p.trend === 'cold' ? 'Cold streak' : 'Low FP/G for active slot', [
        metric ? `${metric.toFixed(1)} FP/G` : null,
        p.vsExp ? `${p.vsExp > 0 ? '+' : ''}${p.vsExp.toFixed(1)} vs exp` : null,
      ].filter(Boolean));
    }
  });

  const flagged = injuryRows.length + coldRows.length + lineupRows.length;
  const healthy = Math.max(0, starters.length - flagged);
  const score = starters.length ? Math.max(0, Math.round((healthy / starters.length) * 100)) : 0;

  return {
    roster,
    starters,
    bench: roster.filter(p => String(p.slot || '').toUpperCase() === 'BN'),
    injured: roster.filter(p => v2PlayerState(p) === 'injured'),
    injuryRows,
    coldRows,
    lineupRows,
    flagged,
    healthy,
    score,
    softOnly:false,
  };
}

function v2PlayerContext(p) {
  return [p?.slot, p?.pos, p?.team].filter(Boolean).join(' · ') || 'Roster';
}

function v2QueueMetricChip(p) {
  const metric = v2PlayerMetric(p);
  return metric ? `${metric.toFixed(1)} FP/G` : null;
}

function v2AttentionSeverity(severity) {
  if (severity === 'urgent') return { label:'Urgent', color:V2.bad, bg:V2.badSoft };
  if (severity === 'check') return { label:'Check', color:V2.warn, bg:V2.warnSoft };
  if (severity === 'review') return { label:'Review', color:V2.accent, bg:V2.accentSoft };
  return { label:'Watch', color:V2.muted, bg:V2.surface2 };
}

function v2AttentionReason(kind, row) {
  const p = row.player;
  if (kind === 'status') {
    return `${row.reason} on ${p.slot || 'active roster'}. Inspect replacement risk before lock.`;
  }
  if (kind === 'lineup') {
    return `${row.reason}. Confirm the active slot before leaving this player in.`;
  }
  return `${row.reason}. Check whether this active spot needs a replacement.`;
}

function v2AttentionQueue(health, matchupRecommendations, options={}) {
  const items = [];
  const allowLineupHealth = options.allowLineupHealth !== false;
  const allowReplacement = options.allowReplacement !== false;
  const addPlayerItem = (kind, row, index) => {
    const p = row.player;
    const metric = v2PlayerMetric(p);
    const meta = {
      status:{ priority:300, severity:'urgent', label:'Status', action:'Inspect' },
      lineup:{ priority:200, severity:'check', label:'Role', action:'Inspect' },
      output:{ priority:100, severity:'review', label:'Output', action:'Inspect' },
    }[kind];
    items.push({
      id:`${kind}-${p.id || p.name || index}`,
      kind,
      priority:meta.priority + metric,
      severity:meta.severity,
      label:meta.label,
      player:p,
      title:p.name,
      context:v2PlayerContext(p),
      reason:v2AttentionReason(kind, row),
      chips:[v2StatusText(p) !== 'Active' ? v2StatusText(p) : null, ...(row.chips || []), v2QueueMetricChip(p)]
        .filter(Boolean)
        .filter((chip, chipIndex, all) => all.indexOf(chip) === chipIndex)
        .slice(0, 3),
      action:meta.action,
    });
  };

  health.injuryRows.forEach((row, index) => addPlayerItem('status', row, index));
  if (allowLineupHealth) {
    health.lineupRows.forEach((row, index) => addPlayerItem('lineup', row, index));
    health.coldRows.forEach((row, index) => addPlayerItem('output', row, index));
  }

  const top = allowReplacement ? matchupRecommendations?.recommendations?.[0] || null : null;
  if (top) {
    const points = v2Number(top.points_delta);
    const confidence = top.confidence || 'medium';
    const evidenceLabel = v2MatchupEvidenceLabel(confidence, top.probability_calibrated);
    const replacementCard = top.replacement_card || null;
    const moveIn = replacementCard?.move_in || {};
    const moveOut = replacementCard?.move_out || {};
    items.push({
      id:`replacement-${top.id || v2MoveChainText(top.action?.chain || [])}`,
      kind:'replacement',
      priority:50 + Math.max(0, points),
      severity:'review',
      label:'Replacement',
      title:'Lineup hot swap',
      context:moveIn.name && moveOut.name ? `${moveIn.name} for ${moveOut.name}` : 'Roster decision',
      reason:replacementCard?.reason || `${v2MoveChainText(top.action?.chain || [])}. Projected gain ${points >= 0 ? '+' : ''}${points.toFixed(1)} points.`,
      chips:[evidenceLabel, ...(top.reason_chips || [])].slice(0, 3),
      action:'Blocked',
      nav:'roster',
      replacement:replacementCard,
      proposal:replacementCard?.proposal || null,
      blockedAction:replacementCard?.execution || {
        state:'blocked',
        label:'Propose swap',
        reason:'Lineup execution safety is not enabled.',
      },
    });
  }

  return items.sort((a,b)=>b.priority-a.priority).slice(0, 6);
}

function v2WinWeekDeadline(deadline) {
  if (!deadline || deadline.state !== 'known' || !deadline.at) return 'Deadline needs a fresh check';
  const parsed = new Date(deadline.at);
  if (Number.isNaN(parsed.getTime())) return 'Deadline needs a fresh check';
  if (parsed.getTime() <= Date.now()) return 'Deadline passed · refresh required';
  return `Before ${parsed.toLocaleTimeString([], { weekday:'short', hour:'numeric', minute:'2-digit' })}`;
}

function v2InclusivePeriodDays(period) {
  const start = period?.start ? new Date(`${period.start}T00:00:00Z`) : null;
  const end = period?.end ? new Date(`${period.end}T00:00:00Z`) : null;
  if (!start || !end || Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) return null;
  return Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
}

function v2WinPeriodLanguage(plan) {
  const horizon = plan?.planning_horizon || plan?.matchup || {};
  const days = v2InclusivePeriodDays(horizon);
  const weekly = days === null || days === 7;
  return {
    days,
    surfaceLabel:weekly ? 'Win This Week' : 'Win This Matchup',
    remainingLabel:weekly ? 'remaining-week' : 'remaining-period',
  };
}

function v2WinWeekDeadlineExpired(deadline) {
  if (!deadline || deadline.state !== 'known' || !deadline.at) return false;
  const parsed = new Date(deadline.at);
  return !Number.isNaN(parsed.getTime()) && parsed.getTime() <= Date.now();
}

function v2ProjectedMarginPosition(value) {
  const margin = v2Number(value);
  if (margin === null) return null;
  if (Math.abs(margin) < 0.05) return 'Tied';
  return margin > 0 ? `${margin.toFixed(1)} ahead` : `${Math.abs(margin).toFixed(1)} behind`;
}

function v2WinWeekPrompt(action, plan) {
  const periodLanguage = v2WinPeriodLanguage(plan);
  const points = v2Number(action?.expected_points?.estimate);
  const deadline = v2WinWeekDeadline(action?.deadline);
  const review = action?.review?.state === 'reviewable' ? action.review : null;
  const promptMoves = review?.slot_moves || action?.steps || [];
  const steps = promptMoves.map((step, index) => {
    if (review) return `${index + 1}. ${step.player_name || step.player_id}: ${step.from_slot || '?'} → ${step.to_slot || '?'}.`;
    if (step.action === 'add') return `${index + 1}. Add ${step.player_name || step.player_id}${step.to_slot ? ` for ${step.to_slot}` : ''}.`;
    if (step.action === 'move_out') return `${index + 1}. Move out ${step.player_name || step.player_id}.`;
    if (step.action === 'start') return `${index + 1}. Start ${step.player_name || step.player_id} in ${step.to_slot || 'the open slot'}.`;
    return `${index + 1}. Move ${step.player_name || step.player_id} from ${step.from_slot || '?'} to ${step.to_slot || '?'}.`;
  });
  return [
    `Pressure-test Sandlot’s top ${periodLanguage.surfaceLabel} action before I touch Fantrax.`,
    '',
    `Plan: ${action?.title || 'Unknown action'}.`,
    `Expected ${periodLanguage.remainingLabel} impact: ${v2Signed(points, 1)} points. ${deadline}.`,
    review ? `Immutable proposal: snapshot ${review.snapshot_id}; proposal ${review.proposal_id}; input hash ${review.input_hash}.` : null,
    review ? `Exact target: Period ${review.target_period?.period_number || '?'}; matchup ${review.target_period?.matchup_key || '?'}; ${review.target_period?.start || '?'} through ${review.target_period?.end || '?'}.` : null,
    plan?.summary?.outlook || null,
    `Confidence: ${action?.confidence || 'unknown'}. Dynasty cost: ${action?.dynasty_cost?.level || 'unknown'}.`,
    action?.dynasty_cost?.reason ? `Dynasty note: ${action.dynasty_cost.reason}` : null,
    ...steps,
    '',
    plan?.summary?.win_probability_excluded_reason || null,
    review ? 'This is a read-only review. Do not claim it was executed or alter the exact target/mapping.' : null,
    'Verify current Fantrax availability, transaction or lineup locks, MLB lineup status, and the exact deadline. Do not invent a win-probability change if it is not calibrated.',
  ].filter(Boolean).join('\n');
}

function V2WinThisWeekPanel({ plan, sync={}, onNav, onAskSkipper, onRefresh }) {
  const [reviewAction, setReviewAction] = React.useState(null);
  React.useEffect(() => { setReviewAction(null); }, [plan?.snapshot_id]);
  if (!plan) return null;
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  const primary = actions[0] || null;
  const alternatives = Array.isArray(plan.no_action?.alternatives) ? plan.no_action.alternatives : [];
  const lineupHandoff = plan.handoffs?.lineup?.read_only === true && plan.handoffs?.lineup?.method === 'GET'
    ? plan.handoffs.lineup
    : null;
  const deadlineExpired = v2WinWeekDeadlineExpired(primary?.deadline);
  const staleSnapshot = ['stale', 'old', 'failed', 'loading', 'refreshing', 'missing'].includes(sync.state);
  const refreshRequired = deadlineExpired || staleSnapshot;
  const snapshotStateLabel = sync.state === 'refreshing' ? 'refreshing' : sync.state || 'unknown';
  const snapshotArticle = snapshotStateLabel === 'old' ? 'an' : 'a';
  const monitor = (plan.monitoring_actions || [])[0] || null;
  const points = v2Number(primary?.expected_points?.estimate);
  const kindLabel = primary?.kind === 'waiver' ? 'Waiver move' : 'Lineup move';
  const stateLabel = refreshRequired ? 'Refresh required' : primary?.state === 'review_now' ? 'Review now' : 'Best move now';
  const dynastyLevel = primary?.dynasty_cost?.level || 'unknown';
  const planningNextPeriod = plan.planning_horizon?.mode === 'editable_period';
  const targetPeriod = plan.planning_horizon?.period_number;
  const periodLanguage = v2WinPeriodLanguage(plan);
  const panelLabel = planningNextPeriod && targetPeriod ? `Plan Period ${targetPeriod}` : periodLanguage.surfaceLabel;
  const tone = refreshRequired
    ? { fg:V2.warn, bg:V2.warnSoft }
    : plan.state === 'ready'
    ? { fg:V2.accent, bg:V2.accentSoft }
    : plan.state === 'paused'
      ? { fg:V2.warn, bg:V2.warnSoft }
      : { fg:V2.ok, bg:V2.okSoft };

  return (
    <>
    <section aria-label={panelLabel} style={{
      background:`linear-gradient(145deg, ${V2.surface} 0%, #fff7ed 100%)`,
      borderRadius:26,
      padding:18,
      boxShadow:'0 0 0 1px rgba(0,0,0,0.06), 0 1px 2px -1px rgba(0,0,0,0.08), 0 8px 24px rgba(76,38,16,0.07)',
      overflow:'hidden',
    }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:12 }}>
        <div>
          <V2Eyebrow color={tone.fg}>{panelLabel}</V2Eyebrow>
          <div style={{ marginTop:7, color:V2.ink, fontSize:25, lineHeight:1.03, fontWeight:850, fontFamily:V2.fontDisplay, textWrap:'balance' }}>
            {primary
              ? planningNextPeriod ? 'Best next-period lineup' : stateLabel
              : plan.state === 'paused' ? 'Plan paused' : 'No worthwhile move'}
          </div>
        </div>
        <span style={{ flexShrink:0, background:tone.bg, color:tone.fg, borderRadius:999, padding:'6px 10px', fontSize:11, fontWeight:900 }}>
          {actions.length} option{actions.length === 1 ? '' : 's'}
        </span>
      </div>

      <div style={{ marginTop:9, color:V2.body, fontSize:13.5, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
        {deadlineExpired
          ? 'The stored primary action has passed its deadline. Refresh before making any Fantrax change.'
          : staleSnapshot
            ? `This plan comes from ${snapshotArticle} ${snapshotStateLabel} snapshot. Refresh before making any Fantrax change.`
          : plan.summary?.headline || plan.no_action?.reason || 'Waiting for a matchup plan.'}
      </div>

      {!refreshRequired && plan.summary?.outlook ? (
        <div style={{ marginTop:7, color:V2.ink, fontSize:12.5, lineHeight:1.4, fontWeight:850, textWrap:'pretty' }}>
          {plan.summary.outlook}
        </div>
      ) : null}

      {plan.summary?.projection_caveat ? (
        <div style={{ marginTop:10, background:V2.warnSoft, color:V2.warn, borderRadius:12, padding:'9px 10px', fontSize:11.5, lineHeight:1.4, fontWeight:800, textWrap:'pretty' }}>
          Projection note: {plan.summary.projection_caveat}
        </div>
      ) : null}

      {primary ? (
        <div style={{ marginTop:15, background:'rgba(255,255,255,0.78)', borderRadius:16, padding:'15px 15px 14px', boxShadow:'0 0 0 1px rgba(0,0,0,0.055)' }}>
          <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:14 }}>
            <div style={{ minWidth:0 }}>
              <div style={{ display:'flex', alignItems:'center', flexWrap:'wrap', gap:7 }}>
                <span style={{ color:V2.accent, fontSize:10.5, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>{kindLabel}</span>
                <span style={{ color:V2.muted, fontSize:10.5, fontWeight:850 }}>· {primary.confidence || 'unknown'} confidence</span>
              </div>
              <div style={{ marginTop:7, color:V2.ink, fontSize:20, lineHeight:1.1, fontWeight:850, fontFamily:V2.fontDisplay, textWrap:'balance' }}>
                {primary.title}
              </div>
              <div style={{ marginTop:7, color:V2.accent, fontSize:12.5, lineHeight:1.35, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>
                {v2WinWeekDeadline(primary.deadline)}
              </div>
            </div>
            <div style={{ flexShrink:0, textAlign:'right' }}>
              <div style={{ color:V2.accent, fontSize:29, lineHeight:0.95, fontWeight:900, fontFamily:V2.fontDisplay, fontVariantNumeric:'tabular-nums' }}>
                {v2Signed(points, 1)}
              </div>
              <div style={{ marginTop:5, color:V2.muted, fontSize:10, fontWeight:900, letterSpacing:'0.06em', textTransform:'uppercase' }}>{deadlineExpired ? 'expired estimate' : staleSnapshot ? 'stale estimate' : 'proj. points'}</div>
            </div>
          </div>

          <div style={{ marginTop:12, display:'flex', flexWrap:'wrap', gap:7 }}>
            <span style={{ background:V2.surface2, color:V2.body, borderRadius:999, padding:'5px 8px', fontSize:10.5, fontWeight:850 }}>
              {dynastyLevel === 'none' ? 'No dynasty cost' : `${dynastyLevel} dynasty cost`}
            </span>
            <span style={{ background:V2.surface2, color:V2.body, borderRadius:999, padding:'5px 8px', fontSize:10.5, fontWeight:850 }}>
              {deadlineExpired ? 'Deadline passed' : staleSnapshot ? `Snapshot ${snapshotStateLabel}` : primary.legality?.state === 'snapshot_verified' ? 'Snapshot legal' : 'Live preflight required'}
            </span>
            <span style={{ background:V2.surface2, color:V2.body, borderRadius:999, padding:'5px 8px', fontSize:10.5, fontWeight:850 }}>
              Read-only
            </span>
          </div>

          {(primary.steps || []).length ? (
            <div style={{ marginTop:12, display:'flex', flexDirection:'column', gap:6 }}>
              <div style={{ color:V2.muted, fontSize:10, lineHeight:1.3, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>
                Complete order · {(primary.steps || []).length} step{(primary.steps || []).length === 1 ? '' : 's'}
              </div>
              {(primary.steps || []).map((step, index) => {
                const text = step.action === 'add'
                  ? `Add ${step.player_name || step.player_id}${step.to_slot ? ` for ${step.to_slot}` : ''}`
                  : step.action === 'move_out'
                    ? `Move out ${step.player_name || step.player_id}`
                    : step.action === 'start'
                      ? `Start ${step.player_name || step.player_id} in ${step.to_slot || 'the open slot'}`
                    : `${step.player_name || step.player_id}: ${step.from_slot || '?'} → ${step.to_slot || '?'}`;
                return (
                  <div key={`${primary.id}-step-${index}`} style={{ display:'flex', gap:9, alignItems:'baseline', color:V2.body, fontSize:12.5, lineHeight:1.35, fontWeight:750 }}>
                    <span style={{ color:V2.accent, fontFamily:V2.fontMono, fontVariantNumeric:'tabular-nums', fontWeight:900 }}>{index + 1}</span>
                    <span style={{ minWidth:0, textWrap:'pretty' }}>{text}</span>
                  </div>
                );
              })}
            </div>
          ) : null}

          {primary.dynasty_cost?.reason ? (
            <div style={{ marginTop:11, color:V2.muted, fontSize:11.5, lineHeight:1.4, fontWeight:700, textWrap:'pretty' }}>
              Dynasty: {primary.dynasty_cost.reason}
            </div>
          ) : null}

          <div style={{
            marginTop:14,
            display:'grid',
            gridTemplateColumns:refreshRequired || (!lineupHandoff && primary.kind !== 'waiver') ? '1fr' : '1fr 1fr',
            gap:9,
          }}>
            {refreshRequired ? (
              <button onClick={onRefresh} disabled={sync.state === 'refreshing'} style={{ minHeight:44, background:V2.warn, color:'#fff', border:'none', borderRadius:999, padding:'11px 14px', cursor:sync.state === 'refreshing' ? 'not-allowed' : 'pointer', opacity:sync.state === 'refreshing' ? 0.7 : 1, fontFamily:'inherit', fontSize:12.5, fontWeight:850 }}>
                {sync.state === 'refreshing' ? 'Refreshing…' : 'Refresh plan'}
              </button>
            ) : primary.review?.state === 'reviewable' ? (
              <button onClick={()=>setReviewAction(primary)} style={{ minHeight:44, background:V2.ink, color:'#fff', border:'none', borderRadius:999, padding:'11px 14px', cursor:'pointer', fontFamily:'inherit', fontSize:12.5, fontWeight:850 }}>
                Review exact action
              </button>
            ) : (
              <button onClick={()=>onAskSkipper(v2WinWeekPrompt(primary, plan))} style={{ minHeight:44, background:V2.ink, color:'#fff', border:'none', borderRadius:999, padding:'11px 14px', cursor:'pointer', fontFamily:'inherit', fontSize:12.5, fontWeight:850 }}>
                Pressure-test with Skipper
              </button>
            )}
            {!refreshRequired && primary.kind === 'waiver' ? (
              <button onClick={()=>onNav('fa')} style={{ minHeight:44, background:V2.surface, color:V2.body, border:`1px solid ${V2.hairline}`, borderRadius:999, padding:'11px 14px', cursor:'pointer', fontFamily:'inherit', fontSize:12.5, fontWeight:850 }}>
                Open waiver board
              </button>
            ) : null}
            {!refreshRequired && primary.kind !== 'waiver' && lineupHandoff ? (
              <a href={lineupHandoff.url} target="_blank" rel="noopener noreferrer" style={{ minHeight:44, background:V2.surface, color:V2.body, border:`1px solid ${V2.hairline}`, borderRadius:999, padding:'11px 14px', display:'flex', alignItems:'center', justifyContent:'center', textDecoration:'none', fontSize:12.5, fontWeight:850 }}>
                {lineupHandoff.label || 'Open Fantrax lineup'}
              </a>
            ) : null}
          </div>
        </div>
      ) : null}

      {!primary && alternatives.length ? (
        <div style={{ marginTop:15, background:'rgba(255,255,255,0.78)', borderRadius:16, padding:'14px 15px', boxShadow:'0 0 0 1px rgba(0,0,0,0.055)' }}>
          <div style={{ color:V2.muted, fontSize:10, lineHeight:1.3, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>
            Best alternatives checked
          </div>
          <div style={{ marginTop:8, display:'flex', flexDirection:'column', gap:10 }}>
            {alternatives.map((alternative, index) => {
              const estimate = v2Number(alternative?.expected_points?.estimate);
              const kind = alternative?.kind === 'waiver' ? 'Waiver' : alternative?.kind === 'lineup' ? 'Lineup' : 'Option';
              return (
                <div key={alternative.id || `alternative-${index}`} style={{ paddingTop:index ? 10 : 0, borderTop:index ? `1px solid ${V2.hairline2}` : 'none' }}>
                  <div style={{ display:'flex', alignItems:'baseline', justifyContent:'space-between', gap:10 }}>
                    <div style={{ color:V2.ink, fontSize:13, lineHeight:1.35, fontWeight:850, textWrap:'pretty' }}>
                      {alternative.title || 'Considered alternative'}
                    </div>
                    <div style={{ flexShrink:0, color:estimate === null ? V2.muted : V2.accent, fontFamily:V2.fontMono, fontSize:11.5, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>
                      {estimate === null ? kind : `${v2Signed(estimate, 1)} pts`}
                    </div>
                  </div>
                  <div style={{ marginTop:4, color:V2.muted, fontSize:11.5, lineHeight:1.4, fontWeight:700, textWrap:'pretty' }}>
                    {alternative.reason || 'This option did not clear Sandlot’s legal and value gates.'}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {monitor ? (
        <div style={{ marginTop:12, display:'flex', alignItems:'flex-start', gap:9, color:V2.muted, fontSize:11.5, lineHeight:1.4, fontWeight:750 }}>
          <span aria-hidden="true" style={{ marginTop:4, width:7, height:7, borderRadius:'50%', flexShrink:0, background:V2.warn }}/>
          <span style={{ textWrap:'pretty' }}><strong style={{ color:V2.body }}>Monitor:</strong> {monitor.title}. {monitor.reason}</span>
        </div>
      ) : null}
    </section>
    {reviewAction ? (
      <V2ActionReviewSheet
        action={reviewAction}
        handoff={lineupHandoff}
        plan={plan}
        onAskSkipper={onAskSkipper}
        onClose={()=>setReviewAction(null)}
      />
    ) : null}
    </>
  );
}

function useV2OwnerBridge(identity, { enabled=true }={}) {
  const [bridge, setBridge] = React.useState({ state:'connecting', nonce:null, decisionsEnabled:false, error:null });
  React.useEffect(() => {
    let cancelled = false;
    if (!enabled) {
      setBridge({ state:'disabled', nonce:null, decisionsEnabled:false, error:null });
      return () => { cancelled = true; };
    }
    setBridge({ state:'connecting', nonce:null, decisionsEnabled:false, error:null });
    fetch(`${V2_OWNER_BRIDGE_URL}/health`, { cache:'no-store', targetAddressSpace:'local' })
      .then(async response => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body?.ok !== true || body?.mode !== 'dry_run' || !body?.nonce || body?.writes_enabled !== false) {
          throw new Error('Local owner bridge did not return a safe dry-run handshake.');
        }
        return body;
      })
      .then(body => {
        if (!cancelled) setBridge({
          state:'ready',
          nonce:body.nonce,
          decisionsEnabled:body.recommendation_decisions_enabled === true,
          error:null,
        });
      })
      .catch(() => {
        if (!cancelled) setBridge({
          state:'offline',
          nonce:null,
          decisionsEnabled:false,
          error:'Start the local owner bridge on your Mac to use owner-only controls.',
        });
      });
    return () => { cancelled = true; };
  }, [enabled, identity]);
  return bridge;
}

function V2ActionReviewSheet({ action, handoff, plan, onAskSkipper, onClose }) {
  const { dialogRef, closeButtonRef } = useV2DialogFocus(onClose);
  const review = action?.review || {};
  const target = review.target_period || action?.target_period || {};
  const moves = Array.isArray(review.slot_moves) ? review.slot_moves : [];
  const hash = String(review.input_hash || '');
  const points = v2Number(action?.expected_points?.estimate);
  const marginBefore = v2ProjectedMarginPosition(plan?.summary?.projected_margin_before_action);
  const marginAfter = v2ProjectedMarginPosition(plan?.summary?.projected_margin_after_action);
  const probabilityReason = plan?.summary?.win_probability_excluded_reason || null;
  const projectionCaveat = plan?.summary?.projection_caveat || null;
  const confirmation = review?.contract?.confirmation?.expected || null;
  const bridge = useV2OwnerBridge(`${review.proposal_id || ''}:${review.input_hash || ''}`);
  const [requestState, setRequestState] = React.useState({ state:'idle', requestId:null, error:null });
  const exactRequestReady = review.state === 'reviewable'
    && confirmation
    && String(confirmation.proposal_id || '') === String(review.proposal_id || '')
    && String(confirmation.input_hash || '') === String(review.input_hash || '')
    && Number(confirmation.snapshot_id) === Number(review.snapshot_id);

  React.useEffect(() => {
    if (!requestState.requestId || !bridge.nonce) return undefined;
    let cancelled = false;
    let timer = null;
    const requestId = requestState.requestId;
    const poll = async () => {
      try {
        const response = await fetch(`${V2_OWNER_BRIDGE_URL}/execution-requests/${encodeURIComponent(requestId)}`, {
          cache:'no-store',
          targetAddressSpace:'local',
          headers:{ 'X-Sandlot-Bridge-Nonce':bridge.nonce },
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(v2ExecutionError(body, response.status));
        v2ValidateExecutionResponse(body, { requestId, review, requireZeroWriteProof:body?.state === 'preflight_passed' });
        if (cancelled) return;
        const nextState = body.state || 'pending';
        setRequestState({ state:nextState, requestId, error:null });
        if (!V2_EXECUTION_TERMINAL_STATES.includes(nextState)) timer = window.setTimeout(poll, 1500);
      } catch (error) {
        if (!cancelled) setRequestState(current => ({ ...current, state:'status_error', error:error?.message || 'Could not read live safety-check status.' }));
      }
    };
    timer = window.setTimeout(poll, 1500);
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [bridge.nonce, requestState.requestId, review.proposal_id, review.snapshot_id, review.input_hash]);

  const requestPreflight = async () => {
    if (bridge.state !== 'ready' || !bridge.nonce || !exactRequestReady || requestState.state === 'requesting') return;
    setRequestState({ state:'requesting', requestId:null, error:null });
    try {
      const response = await fetch(`${V2_OWNER_BRIDGE_URL}/execution-requests`, {
        method:'POST',
        targetAddressSpace:'local',
        headers:{ 'content-type':'application/json', 'X-Sandlot-Bridge-Nonce':bridge.nonce },
        body:JSON.stringify({
          mode:'dry_run',
          proposal_id:review.proposal_id,
          snapshot_id:Number(review.snapshot_id),
          input_hash:review.input_hash,
          confirmation,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(v2ExecutionError(body, response.status));
      v2ValidateExecutionResponse(body, { review });
      setRequestState({ state:body.state || 'pending', requestId:body.request_id, error:null });
    } catch (error) {
      setRequestState({ state:'error', requestId:null, error:error?.message || 'Live safety check could not be requested.' });
    }
  };

  const requestTone = requestState.state === 'preflight_passed'
    ? { fg:V2.ok, bg:V2.okSoft, label:'Safety check passed' }
    : ['preflight_failed','expired','cancelled','error','status_error'].includes(requestState.state)
      ? { fg:V2.warn, bg:V2.warnSoft, label:requestState.state === 'expired' ? 'Request expired' : 'Safety check stopped' }
      : { fg:V2.body, bg:V2.surface2, label:['pending','claimed'].includes(requestState.state) ? 'Checking live Fantrax…' : 'Dry-run only' };
  const confirmationDisabled = bridge.state !== 'ready'
    || !exactRequestReady
    || requestState.state === 'requesting'
    || Boolean(requestState.requestId);
  return (
    <div onClick={onClose} style={{ position:'fixed', inset:0, zIndex:70, background:'rgba(31,20,12,0.62)', display:'flex', alignItems:'flex-end', justifyContent:'center' }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Review exact lineup action"
        onClick={event=>event.stopPropagation()}
        style={{ width:'min(100%, 520px)', maxHeight:'88vh', overflowY:'auto', background:V2.bg, borderTopLeftRadius:28, borderTopRightRadius:28, padding:'18px 18px 24px', boxShadow:'0 -1px 0 rgba(255,255,255,0.5), 0 -18px 52px rgba(31,20,12,0.24)' }}
      >
        <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:14 }}>
          <div style={{ minWidth:0 }}>
            <V2Eyebrow color={V2.accent}>Exact action review</V2Eyebrow>
            <h2 style={{ margin:'7px 0 0', color:V2.ink, fontFamily:V2.fontDisplay, fontSize:27, lineHeight:1.02, letterSpacing:'-0.025em', textWrap:'balance' }}>{action.title}</h2>
          </div>
          <button ref={closeButtonRef} onClick={onClose} aria-label="Close action review" style={{ width:44, height:44, flexShrink:0, border:'none', borderRadius:999, background:V2.surface, color:V2.body, boxShadow:'0 0 0 1px rgba(0,0,0,0.07), 0 2px 6px rgba(31,20,12,0.08)', cursor:'pointer', fontFamily:'inherit', fontSize:20 }}>×</button>
        </div>

        <div style={{ marginTop:15, display:'grid', gridTemplateColumns:'1fr 1fr', gap:9 }}>
          <div style={{ background:V2.surface, borderRadius:16, padding:'12px 13px', boxShadow:'0 0 0 1px rgba(0,0,0,0.055)' }}>
            <div style={{ color:V2.muted, fontSize:10, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>Target</div>
            <div style={{ marginTop:5, color:V2.ink, fontSize:15, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>Period {target.period_number || '?'}</div>
            <div style={{ marginTop:3, color:V2.muted, fontSize:11.5, fontWeight:700 }}>{target.start && target.end ? `${target.start} → ${target.end}` : 'Dates require preflight'}</div>
          </div>
          <div style={{ background:V2.surface, borderRadius:16, padding:'12px 13px', boxShadow:'0 0 0 1px rgba(0,0,0,0.055)' }}>
            <div style={{ color:V2.muted, fontSize:10, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>Expected impact</div>
            <div style={{ marginTop:5, color:V2.accent, fontFamily:V2.fontDisplay, fontSize:22, lineHeight:1, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>{v2Signed(points, 1)} pts</div>
            <div style={{ marginTop:3, color:V2.muted, fontSize:11.5, fontWeight:700 }}>{action.confidence || 'unknown'} confidence</div>
          </div>
        </div>

        {(marginBefore && marginAfter) || probabilityReason ? (
          <div role="group" aria-label="Projected matchup leverage" style={{ marginTop:12, background:V2.surface, borderRadius:18, padding:'14px', boxShadow:'0 0 0 1px rgba(0,0,0,0.055), 0 1px 2px -1px rgba(31,20,12,0.06)' }}>
            <div style={{ color:V2.muted, fontSize:10, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>Projected matchup leverage</div>
            {marginBefore && marginAfter ? (
              <div style={{ marginTop:10, display:'grid', gridTemplateColumns:'1fr auto 1fr', alignItems:'center', gap:10 }}>
                <div style={{ minWidth:0 }}>
                  <div style={{ color:V2.muted, fontSize:10.5, lineHeight:1, fontWeight:800 }}>Do nothing</div>
                  <div style={{ marginTop:5, color:V2.body, fontFamily:V2.fontMono, fontSize:14, lineHeight:1.15, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>{marginBefore}</div>
                </div>
                <span aria-hidden="true" style={{ color:V2.accent, fontSize:18, lineHeight:1, fontWeight:900 }}>→</span>
                <div style={{ minWidth:0, textAlign:'right' }}>
                  <div style={{ color:V2.muted, fontSize:10.5, lineHeight:1, fontWeight:800 }}>Make move</div>
                  <div style={{ marginTop:5, color:V2.accent, fontFamily:V2.fontMono, fontSize:14, lineHeight:1.15, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>{marginAfter}</div>
                </div>
              </div>
            ) : null}
            {probabilityReason ? (
              <div style={{ marginTop:12, background:V2.warnSoft, color:V2.warn, borderRadius:12, padding:'10px 11px', textWrap:'pretty' }}>
                <div style={{ fontSize:11.5, lineHeight:1.2, fontWeight:900 }}>Win odds withheld</div>
                <div style={{ marginTop:4, fontSize:11, lineHeight:1.4, fontWeight:750 }}>{probabilityReason}</div>
                {projectionCaveat ? <div style={{ marginTop:4, fontSize:10.5, lineHeight:1.4, fontWeight:700 }}>{projectionCaveat}</div> : null}
              </div>
            ) : null}
          </div>
        ) : null}

        <div style={{ marginTop:12, background:V2.surface, borderRadius:18, padding:'14px 14px 13px', boxShadow:'0 0 0 1px rgba(0,0,0,0.055)' }}>
          <div style={{ color:V2.muted, fontSize:10, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>Exact final slot mapping</div>
          <div style={{ marginTop:9, display:'flex', flexDirection:'column', gap:8 }}>
            {moves.map((move, index)=>(
              <div key={`${review.proposal_id}-review-${index}`} style={{ display:'grid', gridTemplateColumns:'22px 1fr auto', alignItems:'baseline', gap:8, color:V2.body, fontSize:12.5, lineHeight:1.35, fontWeight:750 }}>
                <span style={{ color:V2.accent, fontFamily:V2.fontMono, fontWeight:900 }}>{move.order || index + 1}</span>
                <span style={{ minWidth:0, textWrap:'pretty' }}>{move.player_name || move.player_id}</span>
                <span style={{ color:V2.ink, fontFamily:V2.fontMono, fontWeight:900 }}>{move.from_slot || '?'} → {move.to_slot || '?'}</span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ marginTop:12, background:bridge.state === 'ready' ? V2.okSoft : V2.warnSoft, color:bridge.state === 'ready' ? V2.ok : V2.warn, borderRadius:16, padding:'12px 13px', fontSize:12, lineHeight:1.45, fontWeight:800, textWrap:'pretty' }}>
          {bridge.state === 'ready'
            ? 'Local owner bridge connected. Confirming below requests a visible, zero-click live safety check; it still cannot change Fantrax.'
            : bridge.state === 'connecting'
              ? 'Checking for the trusted local owner bridge. Nothing can change while this check runs.'
              : 'Local owner bridge offline. Start it on your Mac to enable the exact dry-run confirmation below; nothing will change from this screen.'}
        </div>
        <div style={{ marginTop:9, color:V2.muted, fontSize:10.5, lineHeight:1.4, fontFamily:V2.fontMono, overflowWrap:'anywhere' }}>
          Snapshot #{review.snapshot_id || plan.snapshot_id} · proposal {review.proposal_id || action.id} · contract {hash ? `${hash.slice(0, 12)}…` : 'unavailable'}
        </div>

        <div aria-live="polite" style={{ marginTop:12, background:requestTone.bg, color:requestTone.fg, borderRadius:16, padding:'11px 13px', fontSize:12, lineHeight:1.4, fontWeight:800 }}>
          <div>{requestTone.label}</div>
          {requestState.state === 'preflight_passed' ? <div style={{ marginTop:3 }}>The exact proposal still matched live Fantrax. Zero clicks and zero writes were made.</div> : null}
          {requestState.error ? <div role="alert" style={{ marginTop:3 }}>{requestState.error}</div> : null}
          {requestState.requestId ? <div style={{ marginTop:4, fontFamily:V2.fontMono, fontSize:10, overflowWrap:'anywhere' }}>{requestState.requestId}</div> : null}
        </div>

        <button
          onClick={requestPreflight}
          disabled={confirmationDisabled}
          aria-label="Confirm exact action and request live safety check"
          style={{
            marginTop:14, width:'100%', minHeight:48, border:'none', borderRadius:999,
            background:!confirmationDisabled ? V2.ink : V2.surface2,
            color:!confirmationDisabled ? '#fff' : V2.muted,
            padding:'12px 15px', cursor:!confirmationDisabled ? 'pointer' : 'not-allowed',
            opacity:['requesting','pending','claimed'].includes(requestState.state) ? 0.72 : 1,
            fontFamily:'inherit', fontSize:13, fontWeight:900,
            transitionProperty:'transform, opacity', transitionDuration:'160ms',
          }}
          onPointerDown={event=>{ if (!event.currentTarget.disabled) event.currentTarget.style.transform='scale(0.96)'; }}
          onPointerUp={event=>{ event.currentTarget.style.transform='scale(1)'; }}
          onPointerCancel={event=>{ event.currentTarget.style.transform='scale(1)'; }}
          onPointerLeave={event=>{ event.currentTarget.style.transform='scale(1)'; }}
        >
          {requestState.state === 'requesting'
            ? 'Requesting safety check…'
            : ['pending','claimed'].includes(requestState.state)
              ? 'Live safety check running…'
              : requestState.state === 'preflight_passed'
                ? 'Live safety check passed'
                : requestState.state === 'preflight_failed'
                  ? 'Live safety check failed'
                  : requestState.state === 'expired'
                    ? 'Safety-check request expired'
                    : requestState.requestId
                      ? 'Safety-check request stopped'
                      : 'Confirm exact action · run safety check'}
        </button>
        {!exactRequestReady ? <div style={{ marginTop:7, color:V2.warn, fontSize:11.5, lineHeight:1.4, fontWeight:750 }}>Exact confirmation data is incomplete. Refresh instead of attempting this proposal.</div> : null}

        <div style={{ marginTop:10, display:'grid', gridTemplateColumns:handoff ? '1fr 1fr' : '1fr', gap:9 }}>
          <button onClick={()=>{ onClose(); onAskSkipper(v2WinWeekPrompt(action, plan)); }} style={{ minHeight:46, border:'none', borderRadius:999, background:V2.ink, color:'#fff', padding:'11px 14px', cursor:'pointer', fontFamily:'inherit', fontSize:12.5, fontWeight:850 }}>Ask Skipper</button>
          {handoff ? <a href={handoff.url} target="_blank" rel="noopener noreferrer" style={{ minHeight:46, borderRadius:999, background:V2.surface, color:V2.body, padding:'11px 14px', display:'flex', alignItems:'center', justifyContent:'center', textDecoration:'none', boxShadow:'0 0 0 1px rgba(0,0,0,0.08)', fontSize:12.5, fontWeight:850 }}>{handoff.label || 'Open Fantrax lineup'}</a> : null}
        </div>
      </div>
    </div>
  );
}

function v2ExecutionError(body, status) {
  const detail = body?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object') {
    if (typeof detail.reason === 'string') return detail.reason;
    if (typeof detail.error === 'string') return detail.error;
  }
  return `Live safety check unavailable (${status}).`;
}

function v2ValidateExecutionResponse(body, { review, requestId=null, requireZeroWriteProof=false }) {
  if (body?.mode !== 'dry_run' || body?.writes_enabled !== false) {
    throw new Error('Safety-check response did not remain dry-run and write-disabled.');
  }
  if (!/^xreq_[A-Za-z0-9_-]{20,80}$/.test(String(body?.request_id || '')) || (requestId && body.request_id !== requestId)) {
    throw new Error('Safety-check response returned a mismatched request id.');
  }
  if (
    String(body?.proposal_id || '') !== String(review?.proposal_id || '')
    || Number(body?.snapshot_id) !== Number(review?.snapshot_id)
    || String(body?.input_hash || '') !== String(review?.input_hash || '')
  ) {
    throw new Error('Safety-check response no longer matches the exact proposal.');
  }
  if (requireZeroWriteProof) {
    const report = body?.evidence || {};
    const proof = report?.evidence || {};
    if (report?.writes_attempted !== false || proof?.fantrax_click_count !== 0 || proof?.fantrax_write_count !== 0) {
      throw new Error('Passing safety check did not prove zero Fantrax clicks and writes.');
    }
  }
}

function v2ReceiptMoves(receipt) {
  const baseline = Array.isArray(receipt?.baseline_assignment) ? receipt.baseline_assignment : [];
  const proposed = Array.isArray(receipt?.proposed_assignment) ? receipt.proposed_assignment : [];
  const baselineByPlayer = new Map(baseline.map(item => [String(item.player_id || ''), item]));
  const proposedByPlayer = new Map(proposed.map(item => [String(item.player_id || ''), item]));
  const starts = proposed.filter(item => !baselineByPlayer.has(String(item.player_id || '')));
  const benches = baseline.filter(item => !proposedByPlayer.has(String(item.player_id || '')));
  const slotChanges = proposed.filter(item => {
    const prior = baselineByPlayer.get(String(item.player_id || ''));
    return prior && prior.slot !== item.slot;
  }).map(item => ({ ...item, from_slot:baselineByPlayer.get(String(item.player_id || ''))?.slot }));
  return { starts, benches, slotChanges };
}

function v2ReceiptPeriodMeta(receipt) {
  const period = receipt?.period || {};
  const days = v2InclusivePeriodDays(period);
  const format = value => {
    const parsed = value ? new Date(`${value}T00:00:00Z`) : null;
    return parsed && !Number.isNaN(parsed.getTime())
      ? parsed.toLocaleDateString([], { month:'short', day:'numeric', timeZone:'UTC' })
      : value || 'unknown';
  };
  const range = period.start && period.end ? `${format(period.start)}–${format(period.end)}` : 'dates unavailable';
  return {
    days,
    range,
    eyebrow:days ? `${days}-day lineup receipt` : 'Lineup receipt',
    title:`Lineup plan · ${range}`,
  };
}

function V2RecommendationReceipt({ sync, onAskSkipper }) {
  const [receipt, setReceipt] = React.useState(null);
  const [readState, setReadState] = React.useState('loading');
  const [decisionState, setDecisionState] = React.useState({ state:'idle', error:null });
  const receiptReadSeqRef = React.useRef(0);
  const bridge = useV2OwnerBridge(
    receipt ? `${receipt.receipt_id}:${receipt.input_hash}` : 'receipt-none',
    { enabled:Boolean(receipt) },
  );

  const loadReceipt = React.useCallback(async () => {
    const requestSeq = ++receiptReadSeqRef.current;
    try {
      const response = await fetch('/api/recommendation-receipts/latest', { cache:'no-store' });
      if (requestSeq !== receiptReadSeqRef.current) return null;
      if (response.status === 204 || response.status === 404) {
        setReceipt(null);
        setReadState('empty');
        return null;
      }
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || `Receipt failed (${response.status})`);
      if (requestSeq !== receiptReadSeqRef.current) return null;
      setReceipt(body);
      setReadState('ready');
      return body;
    } catch (error) {
      if (requestSeq !== receiptReadSeqRef.current) return null;
      setReadState('error');
      setDecisionState({ state:'idle', error:error?.message || 'Lineup receipt is unavailable.' });
      return null;
    }
  }, []);

  React.useEffect(() => {
    loadReceipt();
    return () => { receiptReadSeqRef.current += 1; };
  }, [loadReceipt, sync?.label]);

  const decide = async decision => {
    if (!receipt || bridge.state !== 'ready' || !bridge.decisionsEnabled || !bridge.nonce) return;
    receiptReadSeqRef.current += 1;
    setDecisionState({ state:'saving', error:null });
    try {
      const response = await fetch(
        `${V2_OWNER_BRIDGE_URL}/recommendation-receipts/${encodeURIComponent(receipt.receipt_id)}/decision`,
        {
          method:'POST',
          targetAddressSpace:'local',
          headers:{ 'content-type':'application/json', 'X-Sandlot-Bridge-Nonce':bridge.nonce },
          body:JSON.stringify({ decision, input_hash:receipt.input_hash }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (response.status === 409) {
        await loadReceipt();
        throw new Error('A newer recommendation is available. Review the refreshed lineup before deciding.');
      }
      if (!response.ok) throw new Error(body?.detail || 'Could not record this decision.');
      if (
        body?.receipt_id !== receipt.receipt_id
        || String(body?.input_hash || '').toLowerCase() !== String(receipt.input_hash || '').toLowerCase()
        || body?.decision_state !== decision
        || body?.fantrax_changed !== false
        || body?.writes_enabled !== false
      ) {
        throw new Error('Decision response did not preserve the exact no-write receipt boundary.');
      }
      receiptReadSeqRef.current += 1;
      setReceipt(body);
      setDecisionState({ state:'saved', error:null });
    } catch (error) {
      setDecisionState({ state:'error', error:error?.message || 'Could not record this decision.' });
    }
  };

  if (readState === 'loading' || readState === 'empty') return null;
  if (readState === 'error') {
    return <V2Caution eyebrow="Lineup receipt unavailable" tone="warn">{decisionState.error}</V2Caution>;
  }
  const rawGain = receipt?.evaluation?.projected_gain;
  const numericGain = rawGain === null || rawGain === undefined || rawGain === '' ? NaN : Number(rawGain);
  const gain = Number.isFinite(numericGain) ? numericGain : null;
  const moves = v2ReceiptMoves(receipt);
  const periodMeta = v2ReceiptPeriodMeta(receipt);
  const reconciliation = receipt?.reconciliation || {
    state:receipt?.decision_state === 'rejected' ? 'skipped' : 'awaiting',
    applied_count:0,
    total_changes:0,
    applied_changes:[],
    remaining_changes:[],
  };
  const pending = receipt?.decision_state === 'pending' && reconciliation.state === 'awaiting';
  const canDecide = pending && bridge.state === 'ready' && bridge.decisionsEnabled;
  const localReviewHref = receipt?.receipt_id && receipt?.input_hash
    ? `${V2_OWNER_BRIDGE_URL}/recommendation-receipts/${encodeURIComponent(receipt.receipt_id)}/review?input_hash=${encodeURIComponent(receipt.input_hash)}`
    : null;
  const decisionLabel = reconciliation.state === 'applied'
    ? 'Applied in Fantrax'
    : reconciliation.state === 'partially_applied'
      ? `Partially applied · ${reconciliation.applied_count || 0}/${reconciliation.total_changes || 0}`
      : reconciliation.state === 'skipped'
        ? 'Skipped'
        : reconciliation.state === 'unavailable'
          ? 'Fantrax state unavailable'
          : receipt?.decision_state === 'accepted' ? 'Accepted · not yet confirmed' : 'Awaiting your call';
  const prompt = `Pressure-test this ${periodMeta.days || 'current'}-day lineup receipt for ${periodMeta.range}. It projects ${gain === null ? 'an unscored change' : `${v2Signed(gain, 1)} points`}. Explain the assumptions, downside, and whether I should use it. Receipt ${receipt?.receipt_id}.`;
  const appliedChangeText = (reconciliation.applied_changes || [])
    .slice(0, 3)
    .map(change => `${change.player_name} → ${change.proposed_slot}`)
    .join('; ');
  return (
    <section aria-labelledby="lineup-receipt-title" style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:24, padding:'16px 18px', boxShadow:'0 8px 24px rgba(31,20,12,0.045)' }}>
      <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:12 }}>
        <div style={{ minWidth:0 }}>
          <V2Eyebrow color={reconciliation.state === 'applied' ? V2.ok : reconciliation.state === 'partially_applied' ? V2.warn : reconciliation.state === 'skipped' ? V2.muted : V2.accent}>{periodMeta.eyebrow}</V2Eyebrow>
          <h2 id="lineup-receipt-title" style={{ margin:'7px 0 0', color:V2.ink, fontFamily:V2.fontDisplay, fontSize:22, lineHeight:1.08, letterSpacing:'-0.025em', textWrap:'balance' }}>
            {periodMeta.title}
          </h2>
        </div>
        <div style={{ flexShrink:0, color:gain !== null && gain >= 0 ? V2.accent : V2.warn, fontFamily:V2.fontMono, fontSize:20, lineHeight:1, fontWeight:900, fontVariantNumeric:'tabular-nums', textAlign:'right' }}>
          {gain === null ? '—' : v2Signed(gain, 1)}
          <div style={{ marginTop:5, color:V2.muted, fontFamily:V2.font, fontSize:9.5, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>projected pts</div>
        </div>
      </div>

      <div style={{ marginTop:13, background:V2.surface2, borderRadius:16, padding:'12px 13px' }}>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
          <div>
            <V2Eyebrow color={V2.ok}>Start</V2Eyebrow>
            <div style={{ marginTop:6, color:V2.body, fontSize:12, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
              {moves.starts.length ? moves.starts.map(item => `${item.player_name} (${item.slot})`).join(', ') : 'Keep the current starters'}
            </div>
          </div>
          <div>
            <V2Eyebrow color={V2.warn}>Bench</V2Eyebrow>
            <div style={{ marginTop:6, color:V2.body, fontSize:12, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
              {moves.benches.length ? moves.benches.map(item => item.player_name).join(', ') : 'No additional bench moves'}
            </div>
          </div>
        </div>
        {moves.slotChanges.length ? (
          <div style={{ marginTop:11, paddingTop:10, borderTop:`1px solid ${V2.hairline}`, color:V2.body, fontSize:11.5, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
            <V2Eyebrow color={V2.inLineup}>Slot changes</V2Eyebrow>
            <div style={{ marginTop:6 }}>
              {moves.slotChanges.map(item => `${item.player_name} (${item.from_slot} → ${item.slot})`).join(', ')}
            </div>
          </div>
        ) : null}
      </div>

      <div style={{ marginTop:11, display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, color:V2.muted, fontSize:10.5, lineHeight:1.35, fontWeight:750 }}>
        <span style={{ fontVariantNumeric:'tabular-nums' }}>{receipt?.period?.start} → {receipt?.period?.end}</span>
        <span>{decisionLabel}</span>
      </div>

      {reconciliation.state === 'applied' ? (
        <div role="status" style={{ marginTop:12, background:V2.okSoft, color:V2.body, borderRadius:14, padding:'11px 12px', fontSize:11.5, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
          Latest Fantrax snapshot confirms all {reconciliation.total_changes} planned assignment changes. Sandlot observed this; it did not execute it.
        </div>
      ) : null}
      {reconciliation.state === 'partially_applied' ? (
        <div role="status" style={{ marginTop:12, background:V2.warnSoft, color:V2.body, borderRadius:14, padding:'11px 12px', fontSize:11.5, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
          Latest Fantrax snapshot confirms {reconciliation.applied_count} of {reconciliation.total_changes} planned assignment changes{appliedChangeText ? `: ${appliedChangeText}` : ''}. The remaining {Math.max(0, (reconciliation.total_changes || 0) - (reconciliation.applied_count || 0))} are not assumed.
        </div>
      ) : null}
      {reconciliation.state === 'skipped' ? (
        <div role="status" style={{ marginTop:12, background:V2.surface2, color:V2.body, borderRadius:14, padding:'11px 12px', fontSize:11.5, lineHeight:1.45, fontWeight:750 }}>
          Pass recorded. Sandlot will retain this decision for outcome analysis.
        </div>
      ) : null}
      {reconciliation.state === 'awaiting' && receipt?.decision_state === 'accepted' ? (
        <div role="status" style={{ marginTop:12, background:V2.warnSoft, color:V2.body, borderRadius:14, padding:'11px 12px', fontSize:11.5, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
          Intent recorded, but the latest Fantrax snapshot does not yet confirm any planned assignment change.
        </div>
      ) : null}
      {reconciliation.state === 'unavailable' ? (
        <div role="status" style={{ marginTop:12, background:V2.warnSoft, color:V2.body, borderRadius:14, padding:'11px 12px', fontSize:11.5, lineHeight:1.45, fontWeight:750, textWrap:'pretty' }}>
          Sandlot cannot verify the current Fantrax assignment from trusted snapshot evidence. Refresh before relying on this receipt.
        </div>
      ) : null}
      {pending && !canDecide ? (
        <div style={{ marginTop:12, color:V2.muted, fontSize:11.5, lineHeight:1.45, fontWeight:700, textWrap:'pretty' }}>
          {bridge.state === 'connecting'
            ? 'Checking for owner controls on this Mac…'
            : 'Owner controls could not connect inside this page. First start the local owner bridge on this Mac. Then use the local review below; if the bridge is already running, the link bypasses the blocked in-page check.'}
        </div>
      ) : null}
      {decisionState.error ? <div role="alert" style={{ marginTop:10, color:V2.bad, fontSize:11.5, lineHeight:1.4, fontWeight:750 }}>{decisionState.error}</div> : null}

      <div style={{ marginTop:13, display:'grid', gridTemplateColumns:canDecide || (pending && bridge.state === 'offline' && localReviewHref) ? '1fr 1fr' : '1fr', gap:8 }}>
        {canDecide ? (
          <>
            <button onClick={()=>decide('accepted')} disabled={decisionState.state === 'saving'} style={{ minHeight:44, border:'none', borderRadius:999, background:V2.ink, color:'#fff', fontFamily:'inherit', fontSize:12, fontWeight:850, cursor:decisionState.state === 'saving' ? 'wait' : 'pointer', padding:'10px 12px', opacity:decisionState.state === 'saving' ? 0.65 : 1 }}>
              {decisionState.state === 'saving' ? 'Recording…' : 'I’ll use this lineup'}
            </button>
            <button onClick={()=>decide('rejected')} disabled={decisionState.state === 'saving'} style={{ minHeight:44, border:`1px solid ${V2.hairline}`, borderRadius:999, background:V2.surface, color:V2.body, fontFamily:'inherit', fontSize:12, fontWeight:850, cursor:decisionState.state === 'saving' ? 'wait' : 'pointer', padding:'10px 12px', opacity:decisionState.state === 'saving' ? 0.65 : 1 }}>
              Pass
            </button>
          </>
        ) : (
          <>
            {pending && bridge.state === 'offline' && localReviewHref ? (
              <a href={localReviewHref} target="_blank" rel="noopener noreferrer" style={{ minHeight:44, border:'none', borderRadius:999, background:V2.ink, color:'#fff', fontFamily:'inherit', fontSize:12, fontWeight:850, cursor:'pointer', padding:'10px 12px', display:'flex', alignItems:'center', justifyContent:'center', textAlign:'center', textDecoration:'none' }}>
                Review on this Mac · bridge required
              </a>
            ) : null}
            <button onClick={()=>onAskSkipper(prompt)} style={{ minHeight:44, border:`1px solid ${V2.hairline}`, borderRadius:999, background:V2.surface, color:V2.body, fontFamily:'inherit', fontSize:12, fontWeight:850, cursor:'pointer', padding:'10px 12px' }}>
              Ask Skipper about this plan
            </button>
          </>
        )}
      </div>
    </section>
  );
}

function v2RecommendationLearningPrompt(report) {
  const summary = report?.summary || {};
  const checkpoint = report?.evidence_checkpoint || {};
  const requirements = Array.isArray(checkpoint.requirements) ? checkpoint.requirements : [];
  const scored = Number(summary.scored || 0);
  const aligned = Number(summary.accepted_and_observed || 0);
  const scoredRequired = Number(requirements.find(item => item?.key === 'scored_evaluations')?.required || 8);
  const alignedRequired = Number(requirements.find(item => item?.key === 'accepted_and_observed')?.required || 4);
  const rawAverage = summary.average_counterfactual_gain;
  const average = typeof rawAverage === 'number' && Number.isFinite(rawAverage)
    ? `${v2Signed(rawAverage, 1)} points`
    : 'not available yet';
  return [
    'Help me understand Sandlot’s recommendation learning report.',
    '',
    `Evidence checkpoint: ${scored} of ${scoredRequired} scored weeks; ${aligned} of ${alignedRequired} accepted-and-observed plans.`,
    `Average retrospective static-lineup edge: ${average}.`,
    `Autopilot state: ${report?.autopilot?.state || 'locked'}; eligible: ${report?.autopilot_eligible === true ? 'yes' : 'no'}.`,
    '',
    'Explain what this evidence does and does not prove, what the next completed weeks should teach us, and what would still need separate safety review before any automation. Treat the counterfactual as hindsight, not causal lift or proof that a Fantrax action executed. Do not propose or perform a Fantrax write from this report.',
  ].join('\n');
}

function V2RecommendationLearning({ snapshotId, onAskSkipper }) {
  const [report, setReport] = React.useState(null);
  const [state, setState] = React.useState('loading');

  React.useEffect(() => {
    if (!snapshotId) return undefined;
    let cancelled = false;
    if (!report) setState('loading');
    fetch('/api/recommendation-learning', { cache:'no-store' })
      .then(async response => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body?.detail || `Learning report failed (${response.status})`);
        return body;
      })
      .then(body => {
        if (cancelled) return;
        setReport(body);
        setState('ready');
      })
      .catch(() => {
        if (!cancelled) setState(report ? 'stale' : 'error');
      });
    return () => { cancelled = true; };
  }, [snapshotId]);

  if (state === 'loading') return null;
  if (state === 'error') {
    return <div role="alert"><V2Caution eyebrow="Learning report unavailable" tone="warn">The lineup evidence ledger could not be read. No automation state changed.</V2Caution></div>;
  }

  const summary = report?.summary || {};
  const requirements = Array.isArray(report?.evidence_checkpoint?.requirements)
    ? report.evidence_checkpoint.requirements
    : [];
  const scored = Number(summary.scored || 0);
  const aligned = Number(summary.accepted_and_observed || 0);
  const rawAverage = summary.average_counterfactual_gain;
  const hasAverage = typeof rawAverage === 'number' && Number.isFinite(rawAverage);
  const average = hasAverage ? rawAverage : null;
  const scoredRequirement = requirements.find(item => item?.key === 'scored_evaluations') || { current:scored, required:8 };
  const alignedRequirement = requirements.find(item => item?.key === 'accepted_and_observed') || { current:aligned, required:4 };
  const progressRows = [
    { label:'Scored weeks', ...scoredRequirement },
    { label:'Accepted + observed', ...alignedRequirement },
  ];
  const cardShadow = '0 0 0 1px rgba(15,23,42,0.055), 0 1px 2px -1px rgba(15,23,42,0.07), 0 8px 22px rgba(31,20,12,0.045)';
  const skipperLabel = scored ? 'Ask Skipper what Sandlot learned' : 'Ask Skipper why this is locked';
  const askSkipper = event => {
    event.currentTarget.style.transform = 'scale(1)';
    onAskSkipper(v2RecommendationLearningPrompt(report));
  };

  return (
    <section aria-labelledby="recommendation-learning-title" style={{ background:V2.surface, borderRadius:24, padding:'16px 18px', boxShadow:cardShadow }}>
      <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:12 }}>
        <div style={{ minWidth:0 }}>
          <V2Eyebrow color={V2.inLineup}>Learning loop</V2Eyebrow>
          <h2 id="recommendation-learning-title" style={{ margin:'7px 0 0', color:V2.ink, fontFamily:V2.fontDisplay, fontSize:21, lineHeight:1.08, letterSpacing:'-0.025em', textWrap:'balance' }}>
            {scored ? 'Early lineup evidence' : 'Learning from completed weeks'}
          </h2>
        </div>
        <span style={{ flexShrink:0, background:V2.warnSoft, color:V2.warn, borderRadius:999, padding:'6px 9px', fontSize:9.5, lineHeight:1, fontWeight:900, letterSpacing:'0.06em', textTransform:'uppercase', whiteSpace:'nowrap' }}>
          Autopilot locked
        </span>
      </div>

      {state === 'stale' ? (
        <div role="status" style={{ marginTop:11, background:V2.warnSoft, color:V2.warn, borderRadius:12, padding:'9px 10px', fontSize:10.5, lineHeight:1.4, fontWeight:800, textWrap:'pretty' }}>
          Couldn’t update — showing previous evidence.
        </div>
      ) : null}

      {scored ? (
        <div style={{ marginTop:13, display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:7 }}>
          {[
            { value:scored, label:'scored weeks' },
            { value:hasAverage ? v2Signed(average, 1) : '—', label:'avg hindsight edge' },
            { value:aligned, label:'accepted + observed' },
          ].map(item => (
            <div key={item.label} style={{ minWidth:0, background:V2.surface2, borderRadius:12, padding:'10px 7px', textAlign:'center' }}>
              <div style={{ color:V2.ink, fontFamily:V2.fontMono, fontSize:17, lineHeight:1, fontWeight:900, fontVariantNumeric:'tabular-nums' }}>{item.value}</div>
              <div style={{ marginTop:5, color:V2.muted, fontSize:9.5, lineHeight:1.2, fontWeight:800, textWrap:'balance' }}>{item.label}</div>
            </div>
          ))}
        </div>
      ) : (
        <p style={{ margin:'12px 0 0', color:V2.body, fontSize:12.2, lineHeight:1.5, fontWeight:700, textWrap:'pretty' }}>
          No eligible completed lineup receipts yet. Sandlot is collecting evidence before automation can even be reviewed.
        </p>
      )}

      <div style={{ marginTop:13, display:'flex', flexDirection:'column', gap:9 }}>
        {progressRows.map(item => {
          const current = Math.max(0, Number(item.current || 0));
          const required = Math.max(1, Number(item.required || 1));
          const percent = Math.min(100, current / required * 100);
          return (
            <div key={item.label}>
              <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:12, color:V2.muted, fontSize:10.5, lineHeight:1.2, fontWeight:800 }}>
                <span>{item.label}</span>
                <span style={{ fontFamily:V2.fontMono, fontVariantNumeric:'tabular-nums' }}>{current}/{required}</span>
              </div>
              <div style={{ marginTop:5, height:5, borderRadius:999, background:V2.hairline2, overflow:'hidden' }}>
                <div
                  role="progressbar"
                  aria-label={`${item.label} evidence progress`}
                  aria-valuemin={0}
                  aria-valuemax={required}
                  aria-valuenow={Math.min(current, required)}
                  style={{ width:`${percent}%`, height:'100%', borderRadius:999, background:item.passed ? V2.ok : V2.inLineup }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ marginTop:12, paddingTop:10, borderTop:`1px solid ${V2.hairline2}`, color:V2.muted, fontSize:10.5, lineHeight:1.45, fontWeight:700, textWrap:'pretty' }}>
        Counterfactual only — this measures a static lineup in hindsight. It does not prove causality, execute Fantrax moves, or grant write authority.
      </div>

      <button
        type="button"
        onClick={askSkipper}
        onPointerDown={event => { event.currentTarget.style.transform = 'scale(0.96)'; }}
        onPointerUp={event => { event.currentTarget.style.transform = 'scale(1)'; }}
        onPointerCancel={event => { event.currentTarget.style.transform = 'scale(1)'; }}
        onPointerLeave={event => { event.currentTarget.style.transform = 'scale(1)'; }}
        style={{ marginTop:12, width:'100%', minHeight:44, border:'none', borderRadius:999, background:V2.ink, color:'#fff', padding:'10px 14px', fontFamily:'inherit', fontSize:12, fontWeight:850, cursor:'pointer', transform:'scale(1)', transitionProperty:'transform, opacity', transitionDuration:'150ms', transitionTimingFunction:'cubic-bezier(0.2, 0, 0, 1)' }}
      >
        {skipperLabel}
      </button>
    </section>
  );
}

function V2Today({ model, sync, onRefresh, onNav, onPlayer, onAskSkipper }) {
  const health = v2RosterHealth(model);
  const dataQuality = model.dataQuality || null;
  const hasRealData = model.source === 'api' && health.roster.length > 0;
  const lineupAdviceReady = model.source === 'api' ? v2LineupAdviceReady(dataQuality) : true;
  const matchupRecommendations = lineupAdviceReady ? model.matchup?.recommendations || null : null;
  const queue = v2AttentionQueue(health, matchupRecommendations, {
    allowLineupHealth: lineupAdviceReady,
    allowReplacement: lineupAdviceReady,
  });
  const hotSwapItems = queue.filter(item => item.kind === 'replacement' && item.replacement);
  const attentionItems = queue.filter(item => !(item.kind === 'replacement' && item.replacement));
  const matchup = v2MatchupInfo(model.matchup);
  const projection = matchup?.projection || null;
  const projectionInfo = v2ProjectionInfo(projection);
  const showProjection = projectionInfo && !projectionInfo.complete;
  const showProjectionFallback = matchup && !showProjection && dataQuality?.projection_ready === false;
  const lineupPausedReason = model.source === 'api' && hasRealData && !lineupAdviceReady
    ? v2LineupQualityReason(dataQuality)
    : null;
  const showRecommendationFallback = model.source === 'api' && !lineupPausedReason && dataQuality?.recommendations_ready === false && !matchupRecommendations;
  const weekLabel = matchup?.week ? `Week ${matchup.week}` : 'Today';
  const staleCopy = sync.state === 'failed'
    ? (sync.error || 'Last refresh failed.')
    : sync.state === 'refreshing'
      ? (sync.notice || 'Refreshing Fantrax data...')
      : model.source === 'api'
        ? `Snapshot ${sync.label} old.`
        : 'Waiting for first successful Fantrax scrape.';
  const syncTone = v2SyncTone(sync);

  return (
    <div style={{ padding:'18px 16px 28px', display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:12, paddingTop:2 }}>
        <div>
          <V2Eyebrow color={V2.accent}>Today · {weekLabel}</V2Eyebrow>
          <h1 style={{ marginTop:8, marginBottom:0, fontSize:34, lineHeight:0.96, fontWeight:700, letterSpacing:'-0.035em', fontFamily:V2.fontDisplay, textWrap:'balance' }}>
            Today
          </h1>
        </div>
        <button onClick={onRefresh} disabled={sync.state === 'refreshing'} aria-label={sync.state === 'refreshing' ? 'Refreshing Fantrax data' : 'Refresh Fantrax data'} style={{
          display:'flex', alignItems:'center', gap:7, border:`1px solid ${V2.hairline}`, background:V2.surface,
          borderRadius:999, padding:'9px 13px', color:V2.body, fontSize:13, fontWeight:800,
          cursor:sync.state === 'refreshing' ? 'not-allowed' : 'pointer', fontFamily:'inherit', whiteSpace:'nowrap',
          opacity:sync.state === 'refreshing' ? 0.7 : 1,
        }}>
          <span style={{ width:7, height:7, borderRadius:'50%', background:syncTone.color }}/>
          {sync.state === 'refreshing' ? 'syncing' : sync.label || 'now'}
        </button>
      </div>

      <V2MatchupStatusCard
        matchup={matchup}
        projectionInfo={projectionInfo}
        showProjection={showProjection}
        showProjectionFallback={showProjectionFallback}
        dataQuality={dataQuality}
        hasRealData={hasRealData}
        sync={sync}
        staleCopy={staleCopy}
        rosterCount={health.roster.length}
      />

      <V2WinThisWeekPanel
        plan={model.winThisWeek}
        sync={sync}
        onNav={onNav}
        onAskSkipper={onAskSkipper}
        onRefresh={onRefresh}
      />

      <V2RecommendationReceipt sync={sync} onAskSkipper={onAskSkipper}/>

      <V2RecommendationLearning snapshotId={model.snapshotId} onAskSkipper={onAskSkipper}/>

      <V2HotSwapsPanel
        items={hotSwapItems}
        hasRealData={hasRealData}
        sync={sync}
        pausedReason={lineupPausedReason}
        matchup={matchup}
        onAskSkipper={onAskSkipper}
      />

      <V2AttentionQueue items={attentionItems} hasRealData={hasRealData} sync={sync} pausedReason={lineupPausedReason} onPlayer={onPlayer} onNav={onNav} onAskSkipper={onAskSkipper}/>

      {lineupPausedReason ? (
        <V2Caution eyebrow="Advice paused" tone="warn">
          Lineup and replacement advice is paused: {lineupPausedReason}.
        </V2Caution>
      ) : null}

      {sync.notice && sync.state !== 'refreshing' && (
        <V2Caution eyebrow="Heads up" tone="warn">{sync.notice}</V2Caution>
      )}

      {showRecommendationFallback ? (
        <V2Caution eyebrow="Advice paused" tone="warn">
          Recommendation data is incomplete: {v2QualityReason(dataQuality, 'recommendation')}.
        </V2Caution>
      ) : null}

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
        <button onClick={()=>onNav('fa')} style={{ background:V2.ink, color:'#fff', border:'none', borderRadius:999, padding:'13px 14px', cursor:'pointer', fontFamily:'inherit', fontSize:13, fontWeight:800 }}>
          Review waiver board
        </button>
        <button onClick={()=>onNav('skipper')} style={{ background:V2.surface, color:V2.body, border:`1px solid ${V2.hairline}`, borderRadius:999, padding:'13px 14px', cursor:'pointer', fontFamily:'inherit', fontSize:13, fontWeight:800 }}>
          Ask Skipper
        </button>
      </div>
    </div>
  );
}

function V2MatchupStatusCard({
  matchup,
  projectionInfo,
  showProjection,
  showProjectionFallback,
  dataQuality,
  hasRealData,
  sync,
  staleCopy,
  rosterCount,
}) {
  const freshness = v2SnapshotFreshnessText(sync);
  const projectionContext = showProjection ? v2ProjectionContext(matchup, projectionInfo) : null;
  return (
    <section style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:24, padding:'16px 18px', overflow:'hidden' }}>
      {matchup ? (
        <>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:12 }}>
          <div style={{ minWidth:0 }}>
            <div style={{ display:'flex', alignItems:'center', flexWrap:'wrap', gap:7 }}>
              <V2Eyebrow color={matchup.leading ? V2.accent : V2.bad}>Matchup · {matchup.leading ? 'Leading' : matchup.margin < 0 ? 'Trailing' : 'Tied'}</V2Eyebrow>
              <span style={{ color:V2.muted, fontSize:10.5, fontWeight:800, letterSpacing:'0.06em', textTransform:'uppercase' }}>
                {freshness}
              </span>
            </div>
            <div style={{ marginTop:8, fontSize:27, lineHeight:1, fontWeight:850, letterSpacing:0, fontFamily:V2.fontMono, fontVariantNumeric:'tabular-nums' }}>
              {matchup.my.toFixed(1)} <span style={{ color:'#b8afa0', fontFamily:V2.fontDisplay, fontWeight:700 }}>·</span> <span style={{ color:'#b8afa0' }}>{matchup.opp.toFixed(1)}</span>
            </div>
            {showProjection ? (
              <div style={{ marginTop:7, color:projectionInfo.color, fontSize:12.5, fontWeight:850, fontFamily:V2.fontMono, fontVariantNumeric:'tabular-nums', whiteSpace:'nowrap' }}>
                Projected {Math.round(projectionInfo.projectedMyValue)} - {Math.round(projectionInfo.projectedOppValue)}
              </div>
            ) : null}
            {showProjection && !projectionInfo.probabilityCalibrated ? (
              <div style={{ marginTop:4, color:V2.muted, fontSize:10.5, fontWeight:800 }}>
                FP/G estimate · {projectionInfo.probabilityLabel}
              </div>
            ) : null}
            {showProjectionFallback ? (
              <div style={{ marginTop:7, color:V2.warn, fontSize:12, fontWeight:800, lineHeight:1.35, textWrap:'pretty' }}>
                Projection paused: {v2QualityReason(dataQuality, 'projection')}.
              </div>
            ) : null}
            <div style={{ marginTop:8, color:V2.muted, fontSize:13, fontWeight:800, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
              vs {matchup.opponent}{matchup.daysLeft !== null ? ` · ${matchup.daysLeft}d left` : ''}
            </div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:11, flexShrink:0 }}>
            {showProjection ? <V2WinProbabilityRing projection={matchup.projection}/> : null}
            <div style={{ textAlign:'right', flexShrink:0 }}>
              <div style={{ color:matchup.margin >= 0 ? V2.accent : V2.bad, fontSize:30, lineHeight:1, fontWeight:850, letterSpacing:0, fontFamily:V2.fontDisplay, fontVariantNumeric:'tabular-nums' }}>
                {v2Signed(matchup.margin, 1)}
              </div>
              <div style={{ marginTop:6, color:V2.muted, fontSize:12, fontWeight:800 }}>margin</div>
            </div>
          </div>
        </div>
        {projectionContext ? (
          <div role="note" aria-label="Projection scale and evidence" style={{ marginTop:12, padding:'10px 11px', borderRadius:14, background:V2.surface2, border:`1px solid ${V2.hairline2}`, display:'flex', flexDirection:'column', gap:4 }}>
            <div style={{ color:V2.ink, fontSize:11.5, fontWeight:850 }}>{projectionContext.periodLabel}</div>
            <div style={{ color:V2.body, fontSize:11.5, fontWeight:750, lineHeight:1.35 }}>{projectionContext.paceLabel}</div>
            {projectionContext.coverageLabel ? (
              <div style={{ color:V2.warn, fontSize:10.5, fontWeight:800, lineHeight:1.35 }}>{projectionContext.coverageLabel}</div>
            ) : null}
          </div>
        ) : null}
        </>
      ) : (
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:12 }}>
          <div>
            <V2Eyebrow>{hasRealData ? 'Latest snapshot' : 'Fantrax snapshot'}</V2Eyebrow>
            <div style={{ marginTop:8, fontSize:19, lineHeight:1.15, fontWeight:700, fontFamily:V2.fontDisplay, textWrap:'balance' }}>{staleCopy}</div>
          </div>
          <div style={{ textAlign:'right' }}>
            <div style={{ fontSize:25, fontWeight:800, fontFamily:V2.fontMono, fontVariantNumeric:'tabular-nums' }}>{rosterCount || '—'}</div>
            <div style={{ color:V2.muted, fontSize:11, fontWeight:800, textTransform:'uppercase', letterSpacing:'0.08em' }}>players</div>
          </div>
        </div>
      )}
    </section>
  );
}

function v2SnapshotFreshnessText(sync) {
  if (sync.state === 'refreshing') return 'syncing';
  if (sync.state === 'failed') return 'last scrape failed';
  const label = sync.label || 'fresh';
  if (label === 'now') return 'snapshot now';
  return `snapshot ${label} old`;
}

function v2HotSwapContextLine(matchup, item) {
  const card = item?.replacement || {};
  const benefit = v2Number(card.projected_benefit?.points);
  const benefitText = benefit ? `${v2Signed(benefit, 1)} projected points` : 'the top projected gain';
  if (!matchup) return `Best lineup-only move from the latest matchup simulation, worth ${benefitText}.`;
  const days = matchup.daysLeft !== null ? ` · ${matchup.daysLeft}d left` : '';
  if (matchup.margin < 0) {
    return `Trailing by ${Math.abs(matchup.margin).toFixed(1)}${days}; this swap adds ${benefitText} before execution gates.`;
  }
  if (matchup.margin > 0) {
    return `Leading by ${matchup.margin.toFixed(1)}${days}; this swap adds ${benefitText} to protect the edge.`;
  }
  return `Tied${days}; this swap adds ${benefitText} from the latest lineup simulation.`;
}

function V2HotSwapsPanel({ items, hasRealData, sync, pausedReason, matchup, onAskSkipper }) {
  const paused = Boolean(pausedReason);
  const tone = paused
    ? { color:V2.warn, bg:V2.warnSoft }
    : items.length
      ? { color:V2.accent, bg:V2.accentSoft }
      : { color:V2.ok, bg:V2.okSoft };
  const headline = items.length
    ? `${items.length} hot swap${items.length === 1 ? '' : 's'}`
    : paused
      ? 'Hot swaps paused'
      : hasRealData
        ? 'No hot swaps'
        : 'Waiting for roster data';
  const detail = items.length
    ? v2HotSwapContextLine(matchup, items[0])
    : paused
      ? `Lineup swap advice is paused: ${pausedReason}.`
      : hasRealData
        ? 'No lineup-only move clears the meaningful-gain threshold right now.'
        : sync.state === 'failed'
          ? (sync.error || 'Last refresh failed.')
          : 'Waiting for the first successful Fantrax snapshot.';
  return (
    <section style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:24, overflow:'hidden' }}>
      <div style={{ padding:'17px 18px 14px', borderBottom:items.length ? `1px solid ${V2.hairline2}` : 'none' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
          <V2Eyebrow color={tone.color}>Hot Swaps</V2Eyebrow>
          <span style={{
            background:tone.bg,
            color:tone.color,
            borderRadius:999,
            padding:'5px 9px',
            fontSize:11,
            fontWeight:900,
          }}>{items.length || 0}</span>
        </div>
        <div style={{ marginTop:9, fontSize:24, lineHeight:1.05, fontWeight:800, fontFamily:V2.fontDisplay, textWrap:'balance' }}>
          {headline}
        </div>
        <div style={{ marginTop:7, color:V2.muted, fontSize:12.5, lineHeight:1.4, fontWeight:700, textWrap:'pretty' }}>
          {detail}
        </div>
      </div>
      {items.length ? (
        <div>
          {items.map((item, index) => (
            <V2LineupHotSwapCard
              key={item.id}
              item={item}
              last={index === items.length - 1}
              onAskSkipper={onAskSkipper}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function V2AttentionQueue({ items, hasRealData, sync, pausedReason, onPlayer, onNav, onAskSkipper }) {
  const urgentCount = items.filter(item => item.severity === 'urgent').length;
  const checkCount = items.filter(item => item.severity === 'check').length;
  const reviewCount = items.length - urgentCount - checkCount;
  const paused = Boolean(pausedReason);
  const staleSnapshot = sync.state === 'stale';
  const oldSnapshot = sync.state === 'old';
  const queueTone = paused
    ? { color:V2.warn, bg:V2.warnSoft }
    : !hasRealData
      ? { color:V2.warn, bg:V2.warnSoft }
    : items.length
      ? { color:V2.accent, bg:V2.accentSoft }
      : oldSnapshot
        ? { color:V2.bad, bg:V2.badSoft }
        : staleSnapshot
          ? { color:V2.warn, bg:V2.warnSoft }
          : { color:V2.ok, bg:V2.okSoft };
  const headline = items.length
    ? [
        urgentCount ? `${urgentCount} urgent` : null,
        checkCount ? `${checkCount} check` : null,
        reviewCount ? `${reviewCount} review` : null,
      ].filter(Boolean).join(' · ')
    : paused
      ? 'Advice paused'
      : !hasRealData
        ? 'No snapshot to check'
      : oldSnapshot
        ? 'Snapshot too old to call clear'
        : staleSnapshot
          ? 'No issues in the stale snapshot'
          : 'No current issues';
  return (
    <section style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:24, overflow:'hidden' }}>
      <div style={{ padding:'17px 18px 14px', borderBottom:items.length ? `1px solid ${V2.hairline2}` : 'none' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
          <V2Eyebrow color={queueTone.color}>Attention Queue</V2Eyebrow>
          <span style={{
            background:queueTone.bg,
            color:queueTone.color,
            borderRadius:999,
            padding:'5px 9px',
            fontSize:11,
            fontWeight:900,
          }}>{items.length || 0}</span>
        </div>
        <div style={{ marginTop:9, fontSize:24, lineHeight:1.05, fontWeight:800, fontFamily:V2.fontDisplay }}>
          {headline}
        </div>
        <div style={{ marginTop:7, color:V2.muted, fontSize:12.5, lineHeight:1.4, fontWeight:700 }}>
          {paused
            ? 'Showing only status-safe items until lineup slots are verified.'
            : !hasRealData
              ? 'Waiting for the first successful Fantrax snapshot before calling the roster clear.'
            : oldSnapshot
              ? 'Refresh before relying on this queue for a roster decision.'
              : staleSnapshot
                ? `Snapshot ${sync.label} old. Refresh before treating the roster as clear.`
                : 'Ordered by roster consequence from the latest Fantrax snapshot.'}
        </div>
      </div>
      {items.length ? (
        <div>
          {items.map((item, index) => (
            item.kind === 'replacement' && item.replacement ? (
              <V2LineupHotSwapCard
                key={item.id}
                item={item}
                last={index === items.length - 1}
                onAskSkipper={onAskSkipper}
              />
            ) : (
              <V2AttentionQueueRow
                key={item.id}
                item={item}
                last={index === items.length - 1}
                onPlayer={onPlayer}
                onNav={onNav}
              />
            )
          ))}
        </div>
      ) : (
        <V2AttentionEmptyState hasRealData={hasRealData} sync={sync} pausedReason={pausedReason}/>
      )}
    </section>
  );
}

function V2LineupHotSwapCard({ item, last, onAskSkipper }) {
  const card = item.replacement || {};
  const moveIn = card.move_in || {};
  const moveOut = card.move_out || {};
  const benefit = card.projected_benefit || {};
  const confidence = card.confidence || 'medium';
  const evidenceLabel = v2MatchupEvidenceLabel(confidence, benefit.probability_calibrated);
  const risk = card.risk_label || 'unknown';
  const execution = card.execution || item.blockedAction || {};
  const proposal = item.proposal || card.proposal || {};
  const safetyChecks = Array.isArray(proposal.safety_checks) ? proposal.safety_checks : [];
  const movability = card.movability || {};
  const benefitText = v2Signed(benefit.points, 1);
  const confidenceTone = String(confidence).toLowerCase() === 'high'
    ? { fg:V2.ok, bg:V2.okSoft }
    : String(confidence).toLowerCase() === 'light' || String(confidence).toLowerCase() === 'low'
      ? { fg:V2.warn, bg:V2.warnSoft }
      : { fg:V2.body, bg:V2.surface2 };
  const movabilityTone = movability.state === 'movable'
    ? { fg:V2.ok, bg:V2.okSoft }
    : movability.state === 'locked'
      ? { fg:V2.warn, bg:V2.warnSoft }
      : { fg:V2.body, bg:V2.surface2 };
  return (
    <div style={{
      padding:'15px 16px 16px',
      borderBottom:last?'none':`1px solid ${V2.hairline2}`,
      display:'flex',
      flexDirection:'column',
      gap:12,
    }}>
      <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:12 }}>
        <div style={{ minWidth:0 }}>
          <V2Eyebrow color={V2.accent}>Lineup hot swap</V2Eyebrow>
          <div style={{ marginTop:6, color:V2.ink, fontSize:19, lineHeight:1.08, fontWeight:800, fontFamily:V2.fontDisplay, textWrap:'balance' }}>
            {moveIn.name || 'Move-in candidate'} for {moveOut.name || 'current starter'}
          </div>
        </div>
        <div style={{ flexShrink:0, textAlign:'right' }}>
          <div style={{ color:V2.accent, fontSize:22, lineHeight:1, fontWeight:900, fontFamily:V2.fontDisplay, fontVariantNumeric:'tabular-nums' }}>
            {benefitText}
          </div>
          <div style={{ marginTop:3, color:V2.muted, fontSize:10.5, fontWeight:900, letterSpacing:'0.05em', textTransform:'uppercase' }}>
            points
          </div>
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 26px 1fr', alignItems:'stretch', gap:8 }}>
        <V2LineupSwapPlayer label="OUT" player={moveOut} tone="out"/>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'center', color:V2.muted }}>
          {Icons.swap(V2.muted, 18)}
        </div>
        <V2LineupSwapPlayer label="IN" player={moveIn} tone="in"/>
      </div>

      <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
        <span style={{ background:confidenceTone.bg, color:confidenceTone.fg, borderRadius:999, padding:'4px 8px', fontSize:10.5, fontWeight:900 }}>
          {evidenceLabel}
        </span>
        <span style={{ background:V2.surface2, color:V2.body, borderRadius:999, padding:'4px 8px', fontSize:10.5, fontWeight:900 }}>
          {risk} risk
        </span>
        <span style={{ background:V2.surface2, color:V2.body, borderRadius:999, padding:'4px 8px', fontSize:10.5, fontWeight:900 }}>
          {card.provenance?.source || 'latest Fantrax snapshot'}
        </span>
        {movability.label ? (
          <span style={{ background:movabilityTone.bg, color:movabilityTone.fg, borderRadius:999, padding:'4px 8px', fontSize:10.5, fontWeight:900 }}>
            {movability.label}
          </span>
        ) : null}
      </div>

      <V2LineupHotSwapActions execution={execution} card={card} onAskSkipper={onAskSkipper}/>

      <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
        <V2ReasonLine color={V2.ok} label="Why" text={card.reason || item.reason}/>
        <V2ReasonLine color={V2.accent} label="Outlook" text={card.short_term_outlook}/>
        <V2ReasonLine color={V2.warn} label="Risk" text={card.risk}/>
        <V2ReasonLine color={movabilityTone.fg} label="Movability" text={movability.reason}/>
        <V2ReasonLine
          color={V2.muted}
          label="Source"
          text={[
            card.provenance?.slot_provenance ? `slot provenance ${card.provenance.slot_provenance}` : null,
            moveIn.slot_source ? `IN ${moveIn.slot_source}` : null,
            moveOut.slot_source ? `OUT ${moveOut.slot_source}` : null,
          ].filter(Boolean).join(' · ')}
        />
      </div>

      {safetyChecks.length ? <V2ProposalSafetyChecklist proposal={proposal} checks={safetyChecks}/> : null}
    </div>
  );
}

function V2LineupHotSwapActions({ execution, card, onAskSkipper }) {
  return (
    <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
      <button disabled title={execution.reason || card.blocked_reason || 'Execution safety is not ready'} style={{
        flex:'1 1 135px',
        minHeight:40,
        border:'none',
        borderRadius:999,
        background:V2.surface2,
        color:V2.muted,
        fontFamily:'inherit',
        fontSize:12.5,
        fontWeight:900,
        cursor:'not-allowed',
      }}>
        {execution.label || 'Propose swap'} blocked
      </button>
      <button onClick={()=>onAskSkipper?.(v2BuildLineupSwapSkipperPrompt(card, 'quick'))} style={{
        flex:'1 1 120px',
        minHeight:40,
        display:'inline-flex',
        alignItems:'center',
        justifyContent:'center',
        gap:7,
        border:'none',
        borderRadius:999,
        background:V2.accentSoft,
        color:V2.accent,
        fontFamily:'inherit',
        fontSize:12.5,
        fontWeight:900,
        cursor:'pointer',
      }}>
        {Icons.chat(V2.accent, 14)} Ask Skipper
      </button>
      <button onClick={()=>onAskSkipper?.(v2BuildLineupSwapSkipperPrompt(card, 'deep'))} style={{
        flex:'1 1 125px',
        minHeight:40,
        display:'inline-flex',
        alignItems:'center',
        justifyContent:'center',
        gap:7,
        border:'none',
        borderRadius:999,
        background:V2.ink,
        color:'#fff',
        fontFamily:'inherit',
        fontSize:12.5,
        fontWeight:900,
        cursor:'pointer',
      }}>
        {Icons.search('#fff', 14)} Deep research
      </button>
    </div>
  );
}

function V2ProposalSafetyChecklist({ proposal, checks }) {
  return (
    <div aria-label="Proposal safety" style={{
      display:'flex',
      flexDirection:'column',
      gap:8,
      padding:'2px 0 1px',
    }}>
      <div style={{ display:'flex', justifyContent:'space-between', gap:10, alignItems:'baseline' }}>
        <div style={{ color:V2.ink, fontSize:12, fontWeight:900 }}>Proposal safety</div>
        <div style={{ color:V2.muted, fontSize:10.5, fontWeight:900, textTransform:'uppercase', letterSpacing:'0.05em' }}>
          {proposal.status || 'blocked'}
        </div>
      </div>
      <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
        {checks.map((check, index) => {
          const tone = check.state === 'blocked'
            ? { fg:V2.warn, bg:V2.warnSoft }
            : check.state === 'warning'
              ? { fg:V2.accent, bg:V2.accentSoft }
              : { fg:V2.ok, bg:V2.okSoft };
          return (
            <div key={check.key || `${check.label}-${index}`} style={{
              display:'inline-flex',
              alignItems:'center',
              gap:6,
              maxWidth:'100%',
              borderRadius:999,
              background:tone.bg,
              color:tone.fg,
              padding:'5px 8px',
              fontSize:10.5,
              lineHeight:1.15,
              fontWeight:900,
            }}>
              <span aria-hidden="true" style={{
                width:6,
                height:6,
                borderRadius:999,
                background:tone.fg,
                flexShrink:0,
              }}/>
              <span style={{ minWidth:0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{check.label}</span>
            </div>
          );
        })}
      </div>
      <details style={{ marginTop:1 }}>
        <summary style={{
          minHeight:40,
          display:'flex',
          alignItems:'center',
          color:V2.muted,
          fontSize:11,
          fontWeight:850,
          cursor:'pointer',
          textWrap:'pretty',
        }}>
          Show safety details
        </summary>
        <div style={{ display:'grid', gridTemplateColumns:'1fr', gap:6, paddingTop:1 }}>
          {checks.map((check, index) => (
            <div key={check.key || `${check.label}-${index}`} style={{ color:V2.body, fontSize:11.5, lineHeight:1.35, fontWeight:750, textWrap:'pretty' }}>
              <span style={{ color:V2.ink, fontWeight:900 }}>Safety detail: {check.label}</span>
              {check.detail ? <span style={{ color:V2.muted }}> - {check.detail}</span> : null}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

function V2LineupSwapPlayer({ label, player, tone }) {
  const color = tone === 'in' ? V2.ok : V2.warn;
  const bg = tone === 'in' ? V2.okSoft : V2.warnSoft;
  const fppg = Number(player?.fppg);
  const games = player?.remaining_games;
  return (
    <div style={{ minWidth:0, display:'flex', gap:9, alignItems:'center', padding:'8px 0' }}>
      <div style={{
        width:38,
        height:38,
        borderRadius:10,
        background:bg,
        color,
        display:'flex',
        alignItems:'center',
        justifyContent:'center',
        flexShrink:0,
        fontSize:11,
        fontWeight:900,
        fontFamily:V2.fontMono,
      }}>
        {label}
      </div>
      <div style={{ minWidth:0 }}>
        <div style={{
          color:V2.ink,
          fontSize:15,
          lineHeight:1.1,
          fontWeight:800,
          fontFamily:V2.fontDisplay,
          overflow:'hidden',
          display:'-webkit-box',
          WebkitLineClamp:2,
          WebkitBoxOrient:'vertical',
          textWrap:'balance',
        }}>
          {player?.name || 'Unknown player'}
        </div>
        <div style={{ marginTop:3, color:V2.muted, fontSize:11, lineHeight:1.25, fontWeight:750, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
          {[player?.from_slot && player?.to_slot ? `${player.from_slot} -> ${player.to_slot}` : null, player?.positions, player?.team].filter(Boolean).join(' · ')}
        </div>
        <div style={{ marginTop:4, color:V2.body, fontSize:11, fontWeight:850, fontFamily:V2.fontMono, fontVariantNumeric:'tabular-nums' }}>
          {Number.isFinite(fppg) ? `${fppg.toFixed(1)} FP/G` : 'FP/G —'}{games !== null && games !== undefined ? ` · ${games}g` : ''}
        </div>
      </div>
    </div>
  );
}

function V2AttentionQueueRow({ item, last, onPlayer, onNav }) {
  const sev = v2AttentionSeverity(item.severity);
  const open = () => {
    if (item.player?.id) onPlayer?.(item.player.id);
    else if (item.nav) onNav?.(item.nav);
  };
  return (
    <button onClick={open} style={{
      width:'100%', display:'grid', gridTemplateColumns:'42px 1fr auto', alignItems:'center', gap:12,
      padding:'15px 16px', border:'none', borderBottom:last?'none':`1px solid ${V2.hairline2}`,
      background:'transparent', textAlign:'left', cursor:'pointer', fontFamily:'inherit', color:V2.ink,
    }}>
      {item.player ? (
        <Avatar name={item.player.name} size={40}/>
      ) : (
        <div style={{
          width:40, height:40, borderRadius:12, background:sev.bg, color:sev.color,
          display:'flex', alignItems:'center', justifyContent:'center',
        }}>{Icons.swap(sev.color, 18)}</div>
      )}
      <div style={{ minWidth:0 }}>
        <div style={{ display:'flex', alignItems:'center', gap:7, flexWrap:'wrap' }}>
          <span style={{ fontSize:16, lineHeight:1.15, fontWeight:700, fontFamily:V2.fontDisplay }}>{item.title}</span>
          <span style={{ background:sev.bg, color:sev.color, borderRadius:999, padding:'4px 7px', fontSize:10.5, fontWeight:900 }}>{sev.label}</span>
        </div>
        <div style={{ marginTop:4, color:V2.muted, fontSize:11.5, fontWeight:800, lineHeight:1.25 }}>
          {item.label} · {item.context}
        </div>
        <div style={{ marginTop:7, color:V2.body, fontSize:12.5, fontWeight:650, lineHeight:1.42 }}>
          {item.reason}
        </div>
        {item.chips?.length ? (
          <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:8 }}>
            {item.chips.map(chip => (
              <span key={chip} style={{ background:V2.surface2, color:V2.muted, borderRadius:999, padding:'4px 8px', fontSize:10.5, fontWeight:800 }}>
                {chip}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <div style={{ color:sev.color, fontSize:12, fontWeight:900, whiteSpace:'nowrap', alignSelf:'center' }}>
        {item.action} ›
      </div>
    </button>
  );
}

function V2AttentionEmptyState({ hasRealData, sync, pausedReason }) {
  const paused = Boolean(pausedReason);
  const staleSnapshot = sync.state === 'stale';
  const oldSnapshot = sync.state === 'old';
  const label = paused ? 'Paused' : oldSnapshot ? 'Old data' : staleSnapshot ? 'Stale data' : hasRealData ? 'Clear' : 'No data';
  const labelColor = paused || staleSnapshot ? V2.warn : oldSnapshot ? V2.bad : hasRealData ? V2.ok : V2.warn;
  const copy = paused
    ? `Lineup and replacement advice is paused: ${pausedReason}.`
    : oldSnapshot
      ? `This snapshot is ${sync.label || 'more than a day'} old, so Sandlot cannot call the roster clear. Refresh before relying on it.`
    : staleSnapshot
      ? `No issue is flagged in this ${sync.label || 'stale'}-old snapshot. Refresh before treating the roster as clear.`
    : hasRealData
    ? 'No injury, lineup, output, or replacement issue needs action in the current snapshot.'
    : sync.state === 'failed'
      ? (sync.error || 'Snapshot data is unavailable right now.')
      : 'Waiting for the first successful Fantrax snapshot.';
  return (
    <div style={{ padding:'16px 18px 18px' }}>
      <div style={{ background:V2.surface2, border:`1px solid ${V2.hairline2}`, borderRadius:18, padding:'15px 16px' }}>
        <div style={{ color:labelColor, fontSize:12, fontWeight:900, textTransform:'uppercase', letterSpacing:'0.08em' }}>
          {label}
        </div>
        <div style={{ marginTop:7, color:V2.body, fontSize:13, lineHeight:1.45, fontWeight:700 }}>
          {copy}
        </div>
      </div>
    </div>
  );
}

function V2HealthSummary({ health }) {
  const active = health.starters.length || 0;
  const segments = Math.max(8, Math.min(14, active || 14));
  const flagged = Math.min(segments, health.flagged);
  const warn = Math.min(flagged, Math.max(0, health.coldRows.length + health.lineupRows.length));
  const bad = Math.min(flagged, health.injuryRows.length);
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:24, padding:'18px 18px 16px' }}>
      <V2Eyebrow color={V2.accent}>Lineup health</V2Eyebrow>
      <div style={{ display:'grid', gridTemplateColumns:'1fr auto', alignItems:'center', gap:16, marginTop:12 }}>
        <div>
          <div style={{ display:'flex', alignItems:'baseline', gap:3 }}>
            <span style={{ fontSize:56, lineHeight:0.9, fontWeight:800, color:V2.ink, letterSpacing:'-0.07em', fontFamily:V2.fontDisplay }}>{health.score || 0}</span>
            <span style={{ color:V2.muted, fontSize:28, fontWeight:700, fontFamily:V2.fontDisplay }}>%</span>
          </div>
          <div style={{ marginTop:8, fontSize:15, color:V2.body, lineHeight:1.35, fontWeight:700, fontFamily:V2.fontDisplay }}>
            {health.softOnly ? `${health.flagged} active starters worth a look.` : `${health.flagged} of ${active || 0} active starters flagged.`}
          </div>
        </div>
        <div style={{ width:80, display:'grid', gridTemplateColumns:'repeat(7, 8px)', gap:5, justifyContent:'end' }}>
          {Array.from({ length:segments }).map((_, i) => {
            const bg = i < bad ? V2.bad : i < bad + warn ? '#c9872e' : V2.okSoft;
            return <div key={i} style={{ width:8, height:20, borderRadius:4, background:bg, opacity:i < health.flagged ? 1 : 0.8 }}/>;
          })}
        </div>
      </div>
      <div style={{ marginTop:16, height:5, background:V2.okSoft, borderRadius:999, overflow:'hidden', display:'flex' }}>
        <div style={{ width:`${active ? (health.injuryRows.length / active) * 100 : 0}%`, background:V2.bad }}/>
        <div style={{ width:`${active ? ((health.coldRows.length + health.lineupRows.length) / active) * 100 : 0}%`, background:'#c9872e' }}/>
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:12, marginTop:12, color:V2.muted, fontSize:12, fontWeight:800, flexWrap:'wrap' }}>
        <V2HealthLegend color={V2.bad} label={`${health.injuryRows.length} injured`}/>
        <V2HealthLegend color="#c9872e" label={`${health.coldRows.length + health.lineupRows.length} check`}/>
        <V2HealthLegend color={V2.ok} label={`${health.healthy} healthy`}/>
      </div>
    </div>
  );
}

function V2HealthLegend({ color, label }) {
  return (
    <span style={{ display:'inline-flex', alignItems:'center', gap:6 }}>
      <span style={{ width:7, height:7, borderRadius:'50%', background:color }}/>
      {label}
    </span>
  );
}

function V2HealthSection({ title, count, color, rows, empty, onPlayer }) {
  return (
    <section>
      <div style={{ display:'flex', alignItems:'center', gap:9, margin:'2px 6px 10px' }}>
        <span style={{ width:8, height:8, borderRadius:'50%', background:color }}/>
        <div style={{ color:color, fontSize:13, fontWeight:900, textTransform:'uppercase', letterSpacing:'0.13em' }}>{title}</div>
        <div style={{ color:V2.muted, fontSize:13, fontWeight:800 }}>{count}</div>
      </div>
      <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
        {rows.length ? rows.slice(0, 3).map(row => (
          <V2HealthPlayerRow key={row.player.id} row={row} color={color} onPlayer={onPlayer}/>
        )) : (
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'14px 16px', color:V2.muted, fontSize:13, fontWeight:700, lineHeight:1.4 }}>
            {empty}
          </div>
        )}
      </div>
    </section>
  );
}

function V2HealthPlayerRow({ row, color, onPlayer }) {
  const p = row.player;
  const metric = v2PlayerMetric(p);
  const detail = [p.pos, p.team, p.opp].filter(Boolean).join(' · ');
  return (
    <button onClick={()=>onPlayer?.(p.id)} style={{
      width:'100%', display:'grid', gridTemplateColumns:'48px 1fr auto', alignItems:'center', gap:12,
      background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:20, padding:'15px 16px',
      textAlign:'left', cursor:'pointer', fontFamily:'inherit', color:V2.ink,
    }}>
      <Avatar name={p.name} size={42}/>
      <div style={{ minWidth:0 }}>
        <div style={{ fontSize:16, lineHeight:1.15, fontWeight:700, fontFamily:V2.fontDisplay, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{p.name}</div>
        <div style={{ marginTop:4, color:V2.muted, fontSize:12, lineHeight:1.2, fontWeight:800, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{detail || p.slot || 'Roster'}</div>
        <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:7 }}>
          {(row.chips || []).slice(0, 2).map(chip => (
            <span key={chip} style={{ background:V2.surface2, color:color, borderRadius:999, padding:'4px 8px', fontSize:11, fontWeight:800 }}>{chip}</span>
          ))}
          {!row.chips?.length && metric > 0 && (
            <span style={{ background:V2.surface2, color:V2.muted, borderRadius:999, padding:'4px 8px', fontSize:11, fontWeight:800 }}>{v2FormatMetric(metric)} FP/G</span>
          )}
        </div>
      </div>
      <div style={{ color:V2.accent, fontSize:12.5, fontWeight:900, whiteSpace:'nowrap' }}>Inspect ›</div>
    </button>
  );
}

function V2DecisionCard({ swap, onPlayer }) {
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:22, padding:18 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:10 }}>
        <div style={{ display:'flex', alignItems:'baseline', gap:9 }}>
          <div style={{ color:V2.warn, fontSize:28, fontWeight:700, fontFamily:V2.fontDisplay, letterSpacing:'-0.03em' }}>{swap.gain}</div>
          <div style={{ color:V2.muted, fontSize:12, fontWeight:800 }}>proj pts today</div>
        </div>
        <div style={{ background:V2.okSoft, color:V2.ok, borderRadius:999, padding:'6px 11px', fontSize:12, fontWeight:800 }}>{swap.confidence}</div>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 30px 1fr', alignItems:'center', gap:8, marginTop:16 }}>
        <button onClick={()=>onPlayer(swap.from)} style={{ display:'flex', alignItems:'center', gap:10, background:'none', border:'none', padding:0, textAlign:'left', cursor:'pointer', fontFamily:'inherit', minWidth:0 }}>
          <Avatar name={swap.from.name} size={38}/>
          <div style={{ minWidth:0 }}>
            <div style={{ color:V2.muted, textDecoration:'line-through', fontSize:14, fontWeight:700, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{swap.from.name}</div>
            <div style={{ color:V2.muted, fontSize:11.5, fontWeight:700 }}>{swap.from.pos} · {swap.from.team}</div>
          </div>
        </button>
        <div style={{ color:V2.muted, display:'flex', justifyContent:'center' }}>{Icons.swap(V2.muted, 20)}</div>
        <button onClick={()=>onPlayer(swap.to)} style={{ display:'flex', alignItems:'center', gap:10, background:'none', border:'none', padding:0, textAlign:'left', cursor:'pointer', fontFamily:'inherit', minWidth:0 }}>
          <Avatar name={swap.to.name} size={38}/>
          <div style={{ minWidth:0 }}>
            <div style={{ color:V2.ink, fontSize:14, fontWeight:700, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{swap.to.name}</div>
            <div style={{ color:V2.muted, fontSize:11.5, fontWeight:700 }}>{swap.to.pos} · {swap.to.team} · bench</div>
          </div>
        </button>
      </div>
      <div style={{ marginTop:14, color:V2.body, fontSize:14, lineHeight:1.45 }}>{swap.reason}</div>
      <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:12 }}>
        {swap.tags.map(t => <span key={t} style={{ background:V2.surface2, color:V2.muted, borderRadius:999, padding:'5px 9px', fontSize:11, fontWeight:800 }}>{t}</span>)}
      </div>
      <div style={{ marginTop:14, paddingTop:14, borderTop:`1px solid ${V2.hairline2}`, display:'grid', gridTemplateColumns:'1fr auto', gap:10 }}>
        <button style={{ background:V2.ink, color:'#fff', border:'none', borderRadius:999, padding:'12px 16px', fontSize:13, fontWeight:800, cursor:'pointer', fontFamily:'inherit' }}>Review in Fantrax</button>
        <button style={{ background:V2.surface2, color:V2.body, border:'none', borderRadius:999, padding:'12px 16px', fontSize:13, fontWeight:800, cursor:'pointer', fontFamily:'inherit' }}>Dismiss</button>
      </div>
    </div>
  );
}

// ── /roster — FLAGSHIP ─────────────────────────────────────────
function V2Roster({ model, onPlayer }) {
  const [view, setView] = React.useState('starting');
  const roster = model.roster || [];
  const list = roster.filter(p => {
    const slot = String(p.slot || '').toUpperCase();
    if (view === 'starting') return !V2_INACTIVE_SLOTS.includes(slot);
    if (view === 'bench') return V2_BENCH_SLOTS.includes(slot);
    return true;
  });
  const best = [...roster].sort((a,b)=>(b.fppg || b.proj || 0) - (a.fppg || a.proj || 0))[0];
  const weakestSlot = roster.find(p => v2PlayerState(p) === 'injured') || roster.find(p => String(p.slot || '').toUpperCase() === 'BN');
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <V2Segment items={[{value:'starting',label:'Starting'},{value:'bench',label:'Bench'},{value:'all',label:'All'}]} value={view} onChange={setView}/>

      <div style={{ display:'flex', gap:14, padding:'2px 4px' }}>
        <Legend color={V2.inLineup} label="In lineup"/>
        <Legend color={V2.bench} label="Bench"/>
        <Legend color={V2.injured} label="Injured"/>
      </div>

      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:22, overflow:'hidden' }}>
        {list.length ? list.map((p,i)=>(
          <V2RosterSlot key={p.id} player={p} last={i===list.length-1} onClick={()=>onPlayer(p.id)}/>
        )) : (
          <div style={{ padding:18, color:V2.muted, fontSize:13, fontWeight:700 }}>No players in this view.</div>
        )}
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
        <V2MiniInsight title="Top FP/G" value={best ? best.name : '—'} tone="ok" note={best ? `${best.pos || 'UT'} · ${(best.fppg || best.proj || 0).toFixed(1)} FP/G` : 'Waiting for roster data.'}/>
        <V2MiniInsight title="Watch" value={weakestSlot ? weakestSlot.name : '—'} tone="warn" note={weakestSlot ? `${weakestSlot.slot || 'BN'} · ${weakestSlot.injury || weakestSlot.status || 'bench depth'}` : 'No obvious issue from the scrape.'}/>
      </div>
    </div>
  );
}

function V2TeamRoster({ teamId, teamMeta, onBack, onPlayer }) {
  const [view, setView] = React.useState('starting');
  const [state, setState] = React.useState({ status:'loading', payload:null, error:null });

  React.useEffect(() => {
    let cancelled = false;
    if (!teamId) return;
    setState({ status:'loading', payload:null, error:null });
    fetch(`/api/team/${encodeURIComponent(teamId)}/roster`)
      .then(async r => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data?.detail || `Team roster ${r.status}`);
        return data;
      })
      .then(data => { if (!cancelled) setState({ status:'ready', payload:data, error:null }); })
      .catch(err => { if (!cancelled) setState({ status:'error', payload:null, error:err.message }); });
    return () => { cancelled = true; };
  }, [teamId]);

  const roster = React.useMemo(() => {
    return ((state.payload?.rows) || []).filter(Boolean).map(v2NormalizeRosterRow);
  }, [state.payload]);

  const list = roster.filter(p => {
    const slot = String(p.slot || '').toUpperCase();
    if (view === 'starting') return !V2_INACTIVE_SLOTS.includes(slot);
    if (view === 'bench') return V2_BENCH_SLOTS.includes(slot);
    return true;
  });

  const teamName = state.payload?.team_name || teamMeta?.name || 'Team';
  const subline = [teamMeta?.mgr, teamMeta?.record, teamMeta?.streak].filter(Boolean).join(' · ');

  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <button onClick={onBack} style={{
        alignSelf:'flex-start', background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:999,
        padding:'6px 12px', fontSize:12, fontWeight:700, color:V2.body, cursor:'pointer', fontFamily:'inherit',
      }}>← Standings</button>

      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'14px 16px' }}>
        <V2Eyebrow>Team roster</V2Eyebrow>
        <div style={{ marginTop:6, fontSize:20, fontWeight:700, fontFamily:V2.fontDisplay, lineHeight:1.2 }}>{teamName}</div>
        {subline && <div style={{ marginTop:4, fontSize:12, color:V2.muted, fontWeight:600 }}>{subline}</div>}
      </div>

      {state.status === 'loading' && (
        <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:18, color:V2.muted, fontSize:13, fontWeight:700 }}>
          Loading roster from latest snapshot…
        </div>
      )}

      {state.status === 'error' && (
        <V2Caution eyebrow="Roster unavailable" tone="warn">{state.error || 'Could not load this team’s roster.'}</V2Caution>
      )}

      {state.status === 'ready' && (
        <>
          <V2Segment items={[{value:'starting',label:'Starting'},{value:'bench',label:'Bench'},{value:'all',label:'All'}]} value={view} onChange={setView}/>

          <div style={{ display:'flex', gap:14, padding:'2px 4px' }}>
            <Legend color={V2.inLineup} label="In lineup"/>
            <Legend color={V2.bench} label="Bench"/>
            <Legend color={V2.injured} label="Injured"/>
          </div>

          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:22, overflow:'hidden' }}>
            {list.length ? list.map((p,i)=>(
              <V2RosterSlot key={p.id} player={p} last={i===list.length-1} onClick={()=>onPlayer(p.id)}/>
            )) : (
              <div style={{ padding:18, color:V2.muted, fontSize:13, fontWeight:700 }}>No players in this view.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function V2MiniInsight({ title, value, note, tone }) {
  const c = tone==='ok' ? V2.ok : V2.warn;
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:16, padding:14 }}>
      <V2Eyebrow color={c}>{title}</V2Eyebrow>
      <div style={{ marginTop:7, fontSize:19, fontWeight:700, color:c, fontFamily:V2.fontDisplay }}>{value}</div>
      <div style={{ marginTop:5, color:V2.muted, fontSize:11.5, lineHeight:1.4, fontWeight:600 }}>{note}</div>
    </div>
  );
}

function Legend({ color, label }) {
  return (
    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
      <span style={{ width:8, height:8, background:color, borderRadius:2 }}/>
      <span style={{ fontSize:11, color:V2.body, fontWeight:600 }}>{label}</span>
    </div>
  );
}

function V2PositionCard({ group, players, onPlayer }) {
  const pct = Math.min(1, group.ptsRank / group.bestPts);
  const rankTier =
    group.rankInLeague <= 3 ? V2.ok :
    group.rankInLeague <= 8 ? V2.accent : V2.warn;
  const rankLabel =
    group.rankInLeague === 1 ? '1st' :
    group.rankInLeague === 2 ? '2nd' :
    group.rankInLeague === 3 ? '3rd' :
    `${group.rankInLeague}th`;
  const longName = { OF:'Outfield', SP:'Starting Pitchers', RP:'Relievers', UT:'Utility' }[group.pos] || group.pos;

  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, overflow:'hidden' }}>
      <div style={{ padding:'16px 16px 14px' }}>
        <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:10 }}>
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <div style={{
              minWidth:38, height:38, borderRadius:10, background:V2.ink, color:'#fff', padding:'0 10px',
              display:'flex', alignItems:'center', justifyContent:'center',
              fontSize:13, fontWeight:800, letterSpacing:'-0.01em', fontFamily:V2.fontMono,
            }}>{group.pos}</div>
            <div>
              <div style={{ fontSize:15, fontWeight:600, lineHeight:1.2, fontFamily:V2.fontDisplay }}>{longName}</div>
              <div style={{ fontSize:11.5, color:V2.muted, marginTop:2 }}>{players.length} {players.length===1?'player':'players'}</div>
            </div>
          </div>
          <div style={{ textAlign:'right' }}>
            <div style={{ fontSize:9.5, color:V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase' }}>League rank</div>
            <div style={{ fontSize:22, fontWeight:600, color:rankTier, fontVariantNumeric:'tabular-nums', letterSpacing:'-0.01em', fontFamily:V2.fontDisplay, lineHeight:1.1 }}>
              {rankLabel}<span style={{ fontSize:11, color:V2.muted, fontWeight:600 }}>/{group.total}</span>
            </div>
          </div>
        </div>
        <div style={{ marginTop:12 }}>
          <div style={{ display:'flex', justifyContent:'space-between', fontSize:11, color:V2.muted, fontWeight:600, marginBottom:6 }}>
            <span><span style={{ color:V2.ink, fontWeight:700, fontFamily:V2.fontMono }}>{group.ptsRank.toFixed(1)}</span> pts</span>
            <span>avg <span style={{ fontFamily:V2.fontMono }}>{group.leagueAvg.toFixed(1)}</span> · best <span style={{ fontFamily:V2.fontMono }}>{group.bestPts.toFixed(1)}</span></span>
          </div>
          <div style={{ height:6, background:V2.surface2, borderRadius:3, position:'relative' }}>
            <div style={{ width:`${pct*100}%`, height:'100%', background:rankTier, borderRadius:3 }}/>
            <div style={{
              position:'absolute', top:-2, bottom:-2, left:`${(group.leagueAvg/group.bestPts)*100}%`,
              width:1.5, background:V2.muted,
            }}/>
          </div>
        </div>
      </div>
      <div style={{ borderTop:`1px solid ${V2.hairline2}` }}>
        {players.map((p,i) => <V2RosterSlot key={p.id} player={p} last={i===players.length-1} onClick={()=>onPlayer(p.id)}/>)}
      </div>
    </div>
  );
}

function V2RosterSlot({ player, last, onClick }) {
  const state = v2PlayerState(player);
  const sc = v2StateColor(state);
  const metric = player.proj || player.fppg || 0;
  const showExp = metric > 0;
  const expC = state==='injured' ? V2.injured : (player.vsExp >= 1 ? V2.ok : player.vsExp <= -1 ? V2.warn : V2.muted);
  const statusLabel = player.injury || STATUS_LABEL[player.status] || 'IL';

  return (
    <button onClick={onClick} style={{
      width:'100%', display:'flex', alignItems:'center', gap:11, padding:'12px 14px 12px 12px',
      background:'none', cursor:'pointer', textAlign:'left',
      borderTop:'none',
      borderBottom: last?'none':`1px solid ${V2.hairline2}`,
      borderLeft:`3px solid ${sc.fg}`,
      borderRight:'none',
      fontFamily:'inherit',
    }}>
      <div style={{
        width:30, fontSize:10, color:sc.fg, fontWeight:800, letterSpacing:'0.04em',
        textAlign:'center', flexShrink:0, fontFamily:V2.fontMono,
        background:sc.bg, padding:'4px 0', borderRadius:6,
      }}>{player.slot}</div>
      <Avatar name={player.name} size={32}/>
      <div style={{ flex:1, minWidth:0 }}>
        <div style={{ fontSize:13.5, fontWeight:600, lineHeight:1.2, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{player.name}</div>
        <div style={{ display:'flex', alignItems:'center', gap:6, marginTop:3, flexWrap:'wrap' }}>
          <span style={{ fontSize:10.5, color:V2.muted, fontWeight:600 }}>{player.pos} · {player.team}</span>
          <span style={{ fontSize:10.5, color:V2.muted }}>{player.opp}</span>
          {state==='injured' && <span style={{ background:V2.injuredSoft, color:V2.injured, fontSize:10, fontWeight:700, padding:'1px 6px', borderRadius:5 }}>{statusLabel}</span>}
          {player.trend === 'hot' && state !== 'injured' && <span style={{ background:V2.okSoft, color:V2.ok, fontSize:10, fontWeight:700, padding:'1px 6px', borderRadius:5 }}>HOT</span>}
        </div>
      </div>
      <div style={{ textAlign:'right', flexShrink:0 }}>
        <div style={{ fontSize:14, fontWeight:700, fontVariantNumeric:'tabular-nums', fontFamily:V2.fontMono }}>
          {showExp ? metric.toFixed(1) : '—'}
        </div>
        {showExp && (
          <div style={{ fontSize:10, fontWeight:700, color:expC, fontVariantNumeric:'tabular-nums', marginTop:1 }}>
            {player.vsExp>=0?'+':''}{player.vsExp.toFixed(1)}
          </div>
        )}
      </div>
    </button>
  );
}

// ── /league ────────────────────────────────────────────────────
function V2League({ model, onOpenTeam, onOpenTrade }) {
  const [sort, setSort] = React.useState('rank');
  const sorters = {
    rank:(a,b)=>a.rank-b.rank,
    pts:(a,b)=>b.pts-a.pts,
    name:(a,b)=>a.name.localeCompare(b.name),
  };
  const list = [...(model.leagueTeams || [])].sort(sorters[sort]);
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:12 }}>
      <V2LeagueTradeDesk onOpenTrade={onOpenTrade}/>
      <V2Segment items={[{value:'rank',label:'Rank'},{value:'pts',label:'Points'},{value:'name',label:'Name'}]} value={sort} onChange={setSort}/>
      <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
        {list.length ? list.map(t => <V2TeamRow key={t.id} team={t} onOpen={()=>onOpenTeam && onOpenTeam(t)}/>) : (
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, padding:18, color:V2.muted, fontSize:13, fontWeight:700 }}>
            No standings in the latest snapshot.
          </div>
        )}
      </div>
    </div>
  );
}

function V2LeagueTradeDesk({ onOpenTrade }) {
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'14px 16px' }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:14 }}>
        <div style={{ minWidth:0 }}>
          <V2Eyebrow color={V2.accent}>Trade desk</V2Eyebrow>
          <div style={{ marginTop:6, fontSize:19, lineHeight:1.1, fontWeight:700, fontFamily:V2.fontDisplay }}>
            Grade offers against the whole league.
          </div>
          <div style={{ marginTop:5, color:V2.muted, fontSize:12.5, lineHeight:1.4, fontWeight:600 }}>
            Compare your roster with any opponent player before you answer.
          </div>
        </div>
        <button onClick={onOpenTrade} style={{
          flexShrink:0, border:'none', borderRadius:999, background:V2.ink, color:'#fff',
          padding:'10px 13px', fontSize:12.5, fontWeight:800, cursor:'pointer', fontFamily:'inherit',
          display:'inline-flex', alignItems:'center', gap:7,
        }}>
          {Icons.trade('#fff', 15)}
          Grade an offer
        </button>
      </div>
    </div>
  );
}
function V2TeamRow({ team, onOpen }) {
  const tierColor = team.rank<=4 ? V2.ok : team.rank<=8 ? V2.accent : V2.warn;
  return (
    <div style={{ background: team.me?V2.accentSoft:V2.surface, border:`1px solid ${team.me?V2.accent:V2.hairline}`, borderRadius:14, overflow:'hidden' }}>
      <button onClick={onOpen} style={{
        width:'100%', display:'flex', alignItems:'center', gap:12, padding:'12px 14px',
        background:'none', cursor:'pointer', textAlign:'left', border:'none', fontFamily:'inherit',
      }}>
        <div style={{
          width:36, height:36, borderRadius:10, background:tierColor, color:'#fff',
          display:'flex', alignItems:'center', justifyContent:'center',
          fontSize:11, fontWeight:800, fontFamily:V2.fontMono,
        }}>{team.rank<10?`0${team.rank}`:team.rank}</div>
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <span style={{ fontSize:14, fontWeight:700, lineHeight:1.2, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', fontFamily:V2.fontDisplay }}>{team.name}</span>
            {team.me && <span style={{ background:V2.accent, color:'#fff', fontSize:9.5, fontWeight:800, padding:'1px 6px', borderRadius:5 }}>YOU</span>}
          </div>
          <div style={{ fontSize:11.5, color:V2.muted, marginTop:2 }}>{[team.mgr, team.record, team.streak].filter(Boolean).join(' · ')}</div>
        </div>
        <div style={{ fontSize:14, fontWeight:700, fontVariantNumeric:'tabular-nums', fontFamily:V2.fontMono }}>
          {team.pts.toLocaleString()}
        </div>
      </button>
    </div>
  );
}

// ── /free-agents ───────────────────────────────────────────────
function V2FreeAgents({ onOpenPlayer, onAskSkipper }) {
  const [filter, setFilter] = React.useState('ALL');
  const [state, setState] = React.useState({ status:'loading', payload:null, error:null });

  React.useEffect(() => {
    let cancelled = false;
    setState({ status:'loading', payload:null, error:null });
    fetch('/api/waiver-swaps/latest')
      .then(async r => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || `waiver swaps ${r.status}`);
        return data;
      })
      .then(data => { if (!cancelled) setState({ status:'ready', payload:data, error:null }); })
      .catch(err => { if (!cancelled) setState({ status:'error', payload:null, error:err.message }); });
    return () => { cancelled = true; };
  }, []);

  const cards = state.payload?.cards || [];
  const positions = React.useMemo(() => v2WaiverPositions(cards), [cards]);
  const list = cards.filter(card => {
    if (filter === 'ALL') return true;
    const positions = `${card?.add?.positions || ''} ${card?.fills_position || ''}`.toUpperCase();
    return positions.includes(filter);
  });
  const briefState = state.payload?.brief?.state;

  if (state.status === 'loading') {
    return <V2WaiverState eyebrow="Waiver board" title="Loading waiver swaps" body="Reading the latest Fantrax snapshot." />;
  }
  if (state.status === 'error') {
    return <V2WaiverState eyebrow="Waiver board" title="Waiver swaps unavailable" body={state.error || 'The API did not return a waiver board.'} tone="warn" />;
  }

  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <V2Caution eyebrow="Deterministic board" tone="accent">
        {cards.length
          ? `${cards.length} ranked swap${cards.length===1?'':'s'} from the latest snapshot. Review in Fantrax before making any move.`
          : (state.payload?.message || 'No positive waiver swaps found.')}
      </V2Caution>

      {briefState === 'ready' && (
        <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:16, padding:14 }}>
          <V2Eyebrow color={V2.accent}>Skipper brief</V2Eyebrow>
          <div style={{ marginTop:8, fontSize:12.5, color:V2.body, lineHeight:1.55 }}>
            {v2BriefLines(state.payload?.brief?.text).slice(0,2).map((line,i)=>(
              <div key={i} style={{ display:'flex', gap:8, marginTop:i?6:0 }}>
                <span style={{ color:V2.accent, fontWeight:800 }}>•</span>
                <span>{line}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {positions.length > 1 && (
        <div style={{ display:'flex', gap:6, overflowX:'auto', paddingBottom:4, margin:'0 -16px', padding:'0 16px 4px' }}>
          {positions.map(p=>(
            <button key={p} onClick={()=>setFilter(p)} style={{
              padding:'7px 13px', borderRadius:999,
              border:`1px solid ${filter===p?V2.ink:V2.hairline}`,
              background: filter===p?V2.ink:V2.surface, color: filter===p?'#fff':V2.body,
              fontSize:12, fontWeight:700, cursor:'pointer', flexShrink:0, fontFamily:'inherit',
            }}>{p}</button>
          ))}
        </div>
      )}

      {!list.length && (
        <V2WaiverState eyebrow="No cards" title="Nothing in this filter" body="Try All, or refresh after Fantrax updates the free-agent pool." compact />
      )}
      {list.map(card => (
        <V2WaiverSwapCard key={card.id} card={card} onOpenPlayer={onOpenPlayer} onAskSkipper={onAskSkipper}/>
      ))}
    </div>
  );
}

function V2WaiverState({ eyebrow, title, body, tone='accent', compact=false }) {
  const color = tone === 'warn' ? V2.warn : V2.accent;
  return (
    <div style={{ padding:compact?'0':'4px 16px 32px' }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:compact?14:18 }}>
        <V2Eyebrow color={color}>{eyebrow}</V2Eyebrow>
        <div style={{ fontSize:compact?16:20, fontWeight:700, fontFamily:V2.fontDisplay, marginTop:8 }}>{title}</div>
        <div style={{ fontSize:13, color:V2.muted, lineHeight:1.5, marginTop:6 }}>{body}</div>
      </div>
    </div>
  );
}

function V2WaiverSwapCard({ card, onOpenPlayer, onAskSkipper }) {
  const add = card.add || {};
  const out = card.move_out || {};
  const conf = v2ConfidenceStyle(card.confidence);
  const net = v2Signed(card.net_delta, 1);
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, overflow:'hidden' }}>
      <div style={{ padding:'14px 16px 12px', display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:12 }}>
        <div style={{ minWidth:0 }}>
          <V2Eyebrow color={V2.accent}>Rank {card.rank || '—'}</V2Eyebrow>
          <div style={{ display:'flex', alignItems:'baseline', gap:8, marginTop:5 }}>
            <span style={{ fontSize:26, fontWeight:700, color:V2.accent, fontFamily:V2.fontDisplay, fontVariantNumeric:'tabular-nums' }}>{net}</span>
            <span style={{ fontSize:11, color:V2.muted, fontWeight:800, letterSpacing:'0.04em', textTransform:'uppercase' }}>FP/G delta</span>
          </div>
        </div>
        <div style={{ background:conf.bg, color:conf.fg, borderRadius:999, padding:'5px 9px', fontSize:11, fontWeight:800, whiteSpace:'nowrap' }}>
          {card.confidence || 'Medium'}
        </div>
      </div>

      <div style={{ padding:'0 16px 14px', display:'grid', gridTemplateColumns:'1fr 26px 1fr', alignItems:'center', gap:8 }}>
        <V2WaiverPlayer player={add} label="Inspect add"/>
        <div style={{ display:'flex', justifyContent:'center', color:V2.muted }}>{Icons.swap(V2.muted, 20)}</div>
        <V2WaiverPlayer player={out} label="Move out" muted/>
      </div>

      <div style={{ padding:'0 16px 12px', display:'flex', flexWrap:'wrap', gap:6 }}>
        {(card.evidence_chips || []).map(chip => (
          <span key={chip} style={{ background:V2.surface2, color:V2.body, borderRadius:999, padding:'4px 8px', fontSize:10.5, fontWeight:800 }}>
            {chip}
          </span>
        ))}
      </div>

      <div style={{ borderTop:`1px solid ${V2.hairline2}`, padding:'12px 16px', display:'flex', flexDirection:'column', gap:10 }}>
        <V2ReasonLine color={V2.ok} label="Why" text={card.why}/>
        <V2ReasonLine color={V2.warn} label="Risk" text={card.risk}/>
        {card.dynasty_note && <V2ReasonLine color={V2.muted} label="Dynasty" text={card.dynasty_note}/>}
      </div>

      <div style={{ borderTop:`1px solid ${V2.hairline2}`, padding:'12px 16px', display:'flex', gap:8, flexWrap:'wrap' }}>
        <button onClick={()=>onOpenPlayer?.(add.id)} disabled={!onOpenPlayer || !add.id} style={{
          flex:'1 1 130px', background:V2.ink, color:'#fff', border:'none', padding:'11px 14px',
          borderRadius:999, fontSize:12.5, fontWeight:800, cursor:onOpenPlayer && add.id ? 'pointer' : 'default',
          fontFamily:'inherit',
        }}>Review swap</button>
        <button onClick={()=>onAskSkipper?.(v2BuildSwapSkipperPrompt(card))} style={{
          flex:'1 1 150px', display:'flex', alignItems:'center', justifyContent:'center', gap:7,
          background:V2.accentSoft, color:V2.accent, border:'none', padding:'11px 14px',
          borderRadius:999, fontSize:12.5, fontWeight:800, cursor:'pointer', fontFamily:'inherit',
        }}>{Icons.chat(V2.accent, 14)} Continue in Skipper</button>
        <button onClick={()=>onOpenPlayer?.(out.id)} disabled={!onOpenPlayer || !out.id} style={{
          flex:'0 0 auto', background:V2.surface2, color:V2.body, border:'none', padding:'11px 13px',
          borderRadius:999, fontSize:12, fontWeight:800, cursor:onOpenPlayer && out.id ? 'pointer' : 'default',
          fontFamily:'inherit',
        }}>Roster player</button>
      </div>
    </div>
  );
}

function V2WaiverPlayer({ player, label, muted=false }) {
  return (
    <div style={{ minWidth:0, display:'flex', gap:9, alignItems:'center', opacity:muted?0.82:1 }}>
      <Avatar name={player.name || '?'} size={34}/>
      <div style={{ minWidth:0 }}>
        <div style={{ fontSize:10.5, color:V2.muted, fontWeight:800, letterSpacing:'0.06em', textTransform:'uppercase' }}>{label}</div>
        <div style={{ marginTop:2, fontSize:14.5, fontWeight:700, color:V2.ink, fontFamily:V2.fontDisplay, lineHeight:1.12, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{player.name || 'Unknown'}</div>
        <div style={{ marginTop:2, fontSize:11, color:V2.muted, fontWeight:650, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
          {player.positions || 'UT'}{player.team ? ` · ${player.team}` : ''}{Number.isFinite(player.fpg) ? ` · ${Number(player.fpg).toFixed(1)}` : ''}
        </div>
      </div>
    </div>
  );
}

function V2ReasonLine({ color, label, text }) {
  if (!text) return null;
  return (
    <div style={{ display:'flex', gap:9, alignItems:'flex-start' }}>
      <div style={{ width:6, height:6, borderRadius:'50%', background:color, marginTop:7, flexShrink:0 }}/>
      <div style={{ fontSize:12.8, color:V2.body, lineHeight:1.5 }}>
        <span style={{ color, fontWeight:800 }}>{label}. </span>{text}
      </div>
    </div>
  );
}

function v2ConfidenceStyle(confidence) {
  if (confidence === 'High') return { fg:V2.ok, bg:V2.okSoft };
  if (confidence === 'Low') return { fg:V2.warn, bg:V2.warnSoft };
  return { fg:V2.body, bg:V2.surface2 };
}

function v2WaiverPositions(cards) {
  const out = ['ALL'];
  for (const card of cards || []) {
    const text = `${card?.add?.positions || ''} ${card?.fills_position || ''}`;
    for (const token of text.toUpperCase().split(/[^A-Z0-9]+/)) {
      if (['C','1B','2B','3B','SS','OF','SP','RP','UT'].includes(token) && !out.includes(token)) out.push(token);
    }
  }
  return out;
}

function v2Signed(value, digits=1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${n>=0?'+':''}${n.toFixed(digits)}`;
}

function v2BriefLines(text) {
  return String(text || '')
    .split(/\n+/)
    .map(line => line.replace(/^\s*[-*•]\s*/, '').trim())
    .filter(Boolean);
}

// ── /trade-grader ──────────────────────────────────────────────
const TRADE_PICK_MAX = 5;

function v2GradeColor(letter) {
  if (!letter) return V2.body;
  const head = String(letter).charAt(0);
  if (head === 'A') return V2.ok;
  if (head === 'B') return V2.body;
  if (head === 'C') return V2.warn;
  return V2.injured;
}

function v2TradeManualReviewCopy(reason) {
  const text = String(reason || '').trim();
  const age = text.match(/^(?:give|get) player (.+) is age ([0-9.]+) and requires manual dynasty review$/i);
  if (age) return `${age[1]} is ${age[2]}, so Sandlot can’t grade this dynasty trade reliably yet. Review it manually in Fantrax.`;
  const protectedAsset = text.match(/^(?:give|get) player (.+) is protected as a keeper\/minors asset/i);
  if (protectedAsset) return `${protectedAsset[1]} is a protected keeper or minors asset, so Sandlot won’t grade this offer automatically. Review it manually in Fantrax.`;
  return text || 'Sandlot cannot grade this offer reliably yet. Review it manually in Fantrax.';
}

function V2ManualTradeReview({ offer, onAskSkipper }) {
  const review = offer && offer.manual_review;
  if (!review) return null;
  const recommendation = review.recommendation || {};
  const deadline = review.deadline || {};
  const uncertainty = review.uncertainty || {};
  const doNothing = review.do_nothing || {};
  const roster = review.roster_consequences || {};
  const replacement = review.replacement_value || {};
  const counter = review.counteroffer || {};
  const blockers = Array.isArray(review.blockers) ? review.blockers : [];
  const horizons = Array.isArray(review.horizons) ? review.horizons : [];
  const rawPreserved = doNothing.current_rate_preserved;
  const preserved = rawPreserved === null || rawPreserved === undefined || rawPreserved === '' ? NaN : Number(rawPreserved);
  const researchWithSkipper = () => onAskSkipper && onAskSkipper(review.skipper_prompt, {
    autoSend:true, reasoning:true, reasoningEffort:'high', webSearch:true,
  });

  return (
    <section aria-label="Manual trade review" style={{
      marginTop:10, background:V2.surface, border:`1px solid ${V2.hairline}`,
      borderRadius:16, padding:12, display:'flex', flexDirection:'column', gap:10,
    }}>
      <div>
        <V2Eyebrow color={V2.warn}>Decision brief · evidence incomplete</V2Eyebrow>
        <h3 style={{
          margin:'6px 0 0', color:V2.ink, fontFamily:V2.fontDisplay,
          fontSize:20, lineHeight:1.06, letterSpacing:'-0.02em', textWrap:'balance',
        }}>{recommendation.title || 'Hold this offer for now'}</h3>
        <div style={{ marginTop:6, color:V2.body, fontSize:11.5, lineHeight:1.45, fontWeight:650, textWrap:'pretty' }}>
          {recommendation.detail || 'The full package does not have enough verified evidence for a safe grade.'}
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(2, minmax(0, 1fr))', gap:8 }}>
        <div style={{ minWidth:0, background:V2.warnSoft, borderRadius:12, padding:'9px 10px' }}>
          <div style={{ color:V2.warn, fontSize:9, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>Uncertainty</div>
          <div style={{ marginTop:4, color:V2.ink, fontSize:12, lineHeight:1.25, fontWeight:850 }}>{uncertainty.label || 'Value withheld'}</div>
        </div>
        <div style={{ minWidth:0, background:V2.surface2, borderRadius:12, padding:'9px 10px' }}>
          <div style={{ color:V2.muted, fontSize:9, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>Deadline</div>
          <div style={{ marginTop:4, color:V2.ink, fontSize:12, lineHeight:1.25, fontWeight:850 }}>{deadline.label || 'Not provided'}</div>
          <div style={{ marginTop:2, color:V2.muted, fontSize:8.5, lineHeight:1.25 }}>Fantrax: {deadline.fantrax_schedule_label || 'Pending'}</div>
        </div>
      </div>

      <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
        {horizons.map(item => (
          <div key={item.key} style={{ minWidth:0, background:V2.surface2, borderRadius:11, padding:'9px 10px' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'baseline', gap:8 }}>
              <div style={{ color:V2.muted, fontSize:8.5, lineHeight:1.2, fontWeight:900, letterSpacing:'0.055em', textTransform:'uppercase' }}>{item.label}</div>
              <div style={{ flexShrink:0, color:item.status === 'manual_review' ? V2.warn : V2.muted, fontSize:9.5, lineHeight:1.2, fontWeight:850 }}>
                {item.status === 'manual_review' ? 'Manual review' : 'Withheld'}
              </div>
            </div>
            <div style={{ marginTop:4, color:V2.body, fontSize:10.5, lineHeight:1.4, textWrap:'pretty' }}>{item.detail}</div>
          </div>
        ))}
      </div>

      <div style={{ background:V2.okSoft, borderRadius:12, padding:'10px 11px' }}>
        <V2Eyebrow color={V2.ok}>Do nothing</V2Eyebrow>
        <div style={{ marginTop:5, color:V2.ink, fontSize:13, lineHeight:1.3, fontWeight:850 }}>{doNothing.title || 'Keep your current roster'}</div>
        <div style={{ marginTop:3, color:V2.body, fontSize:10.5, lineHeight:1.4 }}>
          {doNothing.detail || 'The roster stays unchanged and the offer remains unanswered.'}{Number.isFinite(preserved) ? <> Current package rate: <span style={{ fontFamily:V2.fontMono, fontWeight:850 }}>{preserved.toFixed(2)} {doNothing.unit || 'FP/G'}</span>.</> : ''}
        </div>
      </div>

      <div style={{ display:'flex', flexDirection:'column', gap:7 }}>
        <div style={{ padding:'0 2px' }}>
          <div style={{ color:V2.ink, fontSize:11.5, lineHeight:1.35, fontWeight:850 }}>{roster.label || 'Roster shape requires review'}</div>
          <div style={{ marginTop:2, color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>{roster.detail}</div>
        </div>
        <div style={{ padding:'0 2px' }}>
          <div style={{ color:V2.ink, fontSize:11.5, lineHeight:1.35, fontWeight:850 }}>{replacement.label || 'Replacement value unavailable'}</div>
          <div style={{ marginTop:2, color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>{replacement.detail}</div>
        </div>
        <div style={{ padding:'0 2px' }}>
          <div style={{ color:V2.ink, fontSize:11.5, lineHeight:1.35, fontWeight:850 }}>{counter.title || 'Counter direction needs review'}</div>
          <div style={{ marginTop:2, color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>{counter.detail}</div>
        </div>
      </div>

      {blockers.length ? (
        <details style={{ borderTop:`1px solid ${V2.hairline2}`, paddingTop:8 }}>
          <summary style={{ color:V2.body, fontSize:10.5, lineHeight:1.4, fontWeight:850, cursor:'pointer' }}>
            Why Sandlot withheld the grade ({blockers.length})
          </summary>
          <div style={{ marginTop:7, display:'flex', flexDirection:'column', gap:6 }}>
            {blockers.map((item, index) => (
              <div key={`${item.player_id || 'player'}-${item.kind || 'reason'}-${index}`} style={{ color:V2.muted, fontSize:10.5, lineHeight:1.4, textWrap:'pretty' }}>
                <strong style={{ color:V2.body }}>{item.player_name || 'Player'}:</strong> {item.reason}
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <button onClick={researchWithSkipper} disabled={!onAskSkipper || !review.skipper_prompt} style={{
        minHeight:44, width:'100%', border:'none', borderRadius:999,
        background:V2.ink, color:'#fff', fontFamily:'inherit', fontSize:11.5, fontWeight:850,
        cursor:onAskSkipper && review.skipper_prompt ? 'pointer' : 'not-allowed',
        opacity:onAskSkipper && review.skipper_prompt ? 1 : 0.6,
      }}>Research this trade in Skipper</button>
      <div style={{ color:V2.muted, fontSize:9.5, lineHeight:1.35, textAlign:'center' }}>
        On demand · web sources + high reasoning · no trade is answered or sent
      </div>
    </section>
  );
}

function V2PlayerPicker({ label, source, model, value, onChange }) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');

  const candidates = React.useMemo(() => {
    const idx = (model && model.playerIndex) || [];
    return idx.filter(p => p && p.source === source);
  }, [model, source]);

  const valueIds = React.useMemo(() => new Set(value.map(p => p.id)), [value]);
  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = candidates.filter(p => !valueIds.has(p.id));
    if (!q) return list.slice(0, 80);
    return list
      .filter(p => (p.name || '').toLowerCase().includes(q) || (p.team || '').toLowerCase().includes(q))
      .slice(0, 60);
  }, [candidates, valueIds, query]);

  const add = (p) => {
    if (value.length >= TRADE_PICK_MAX) return;
    onChange([...value, p]);
    setQuery('');
    setOpen(false);
  };
  const remove = (id) => onChange(value.filter(p => p.id !== id));
  const pickerId = `trade-picker-${source}`;

  return (
    <section aria-labelledby={`${pickerId}-label`} style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:14 }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
        <div id={`${pickerId}-label`}><V2Eyebrow color={source === 'mine' ? V2.injured : V2.ok}>{label}</V2Eyebrow></div>
        <span style={{ fontFamily:V2.fontMono, fontSize:10.5, fontWeight:700, color:V2.muted, letterSpacing:'0.04em' }}>
          {value.length}/{TRADE_PICK_MAX}
        </span>
      </div>

      <div style={{ marginTop:10, display:'flex', flexDirection:'column', gap:6 }}>
        {value.map(p => (
          <div key={p.id} style={{
            background:V2.surface2, borderRadius:10, padding:'8px 10px',
            display:'flex', alignItems:'center', gap:10,
          }}>
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{
                fontSize:13.5, fontWeight:600, color:V2.ink, lineHeight:1.25,
                whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis',
              }}>{p.name}</div>
              <div style={{ fontSize:11, color:V2.muted, marginTop:2, fontFamily:V2.fontMono }}>
                {p.slot || p.positions || '—'} · {p.team || '—'}
                {p.fppg != null ? ` · ${Number(p.fppg).toFixed(1)} FP/G` : ''}
              </div>
            </div>
            <button onClick={()=>remove(p.id)} aria-label={`Remove ${p.name} from ${label}`} style={{
              flexShrink:0, width:40, height:40, border:'none', borderRadius:10,
              background:'transparent', color:V2.muted, cursor:'pointer', fontSize:18, lineHeight:1,
            }}>×</button>
          </div>
        ))}

        {value.length < TRADE_PICK_MAX && (
          !open ? (
            <button onClick={()=>setOpen(true)} aria-expanded="false" aria-controls={`${pickerId}-options`} aria-label={`Add player to ${label}`} style={{
              background:'transparent', border:`1px dashed ${V2.hairline}`, borderRadius:10,
              padding:'10px 12px', color:V2.body, fontSize:12.5, fontWeight:600, cursor:'pointer',
              fontFamily:'inherit', textAlign:'left',
            }}>+ Add player</button>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
              <input
                autoFocus
                value={query}
                onChange={e=>setQuery(e.target.value)}
                aria-label={`Search players for ${label}`}
                placeholder={`Search ${source === 'mine' ? 'your roster' : 'other teams'}…`}
                style={{
                  width:'100%', border:`1px solid ${V2.hairline}`, borderRadius:10,
                  padding:'9px 12px', fontSize:13, fontFamily:'inherit', outline:'none',
                  background:V2.surface2, color:V2.ink,
                }}/>
              <div id={`${pickerId}-options`} role="group" aria-label={`${label} player options`} style={{
                maxHeight:240, overflowY:'auto',
                border:`1px solid ${V2.hairline2}`, borderRadius:10, background:V2.surface2,
              }}>
                {filtered.length === 0 ? (
                  <div style={{ padding:'12px 14px', fontSize:12, color:V2.muted }}>
                    No matches{candidates.length === 0 ? ' — snapshot has no players from this side yet' : ''}.
                  </div>
                ) : filtered.map((p, i) => (
                  <button key={p.id} onClick={()=>add(p)} aria-label={`Add ${p.name} to ${label}`} style={{
                    width:'100%', textAlign:'left', display:'flex', flexDirection:'column', gap:2,
                    padding:'9px 12px', border:'none', cursor:'pointer',
                    background:'transparent', borderBottom: i === filtered.length - 1 ? 'none' : `1px solid ${V2.hairline2}`,
                    fontFamily:'inherit',
                  }}>
                    <div style={{ fontSize:13, fontWeight:600, color:V2.ink }}>{p.name}</div>
                    <div style={{ fontSize:10.5, color:V2.muted, fontFamily:V2.fontMono }}>
                      {p.slot || p.positions || '—'} · {p.team || '—'}
                      {p.fppg != null ? ` · ${Number(p.fppg).toFixed(1)} FP/G` : ''}
                    </div>
                  </button>
                ))}
              </div>
              <button onClick={()=>{ setOpen(false); setQuery(''); }} style={{
                background:'transparent', border:'none', color:V2.muted, fontSize:11.5, fontWeight:600,
                cursor:'pointer', padding:'4px 0', textAlign:'left', fontFamily:'inherit',
              }}>Cancel</button>
            </div>
          )
        )}
      </div>
    </section>
  );
}

function v2TradeHorizonValue(item) {
  if (!item || item.value == null) return item && item.status === 'limited' ? 'Limited' : 'Not modeled';
  const value = Number(item.value);
  if (!Number.isFinite(value)) return String(item.value);
  const formatted = `${value >= 0 ? '+' : ''}${value.toFixed(item.key === 'current_rate' ? 2 : 1)}`;
  return item.unit ? `${formatted} ${item.unit}` : formatted;
}

function V2TradeAnalysisSummary({ result, onAskSkipper }) {
  const analysis = result && result.analysis;
  if (!analysis) return null;
  const recommendation = analysis.recommendation || {};
  const counter = analysis.recommended_counter;
  const fit = analysis.roster_fit || {};
  const horizons = analysis.horizons || [];
  const scrollToCounters = () => {
    const target = document.getElementById('trade-counters');
    if (target) target.scrollIntoView({ behavior:'smooth', block:'start' });
  };
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div>
        <V2Eyebrow color={recommendation.action === 'counter' ? V2.accent : V2.warn}>Trade analysis</V2Eyebrow>
        <h2 style={{ margin:'7px 0 0', fontFamily:V2.fontDisplay, fontSize:22, lineHeight:1.08, letterSpacing:'-0.025em' }}>
          {recommendation.title || 'Review this offer'}
        </h2>
        <div style={{ marginTop:7, color:V2.body, fontSize:12.5, lineHeight:1.5, fontWeight:600 }}>
          {recommendation.detail || 'Compare the evidence below before answering.'}
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(2, minmax(0, 1fr))', gap:8 }}>
        {horizons.map(item => {
          const modeled = item.status === 'modeled';
          const limited = item.status === 'limited';
          const value = Number(item.value);
          const tone = modeled && Number.isFinite(value) ? (value >= 0 ? V2.ok : V2.injured) : limited ? V2.warn : V2.muted;
          return (
            <div key={item.key} style={{
              minWidth:0, background:V2.surface2, border:`1px solid ${V2.hairline2}`,
              borderRadius:13, padding:'11px 12px',
            }}>
              <div style={{ color:V2.muted, fontSize:9.5, fontWeight:900, letterSpacing:'0.07em', textTransform:'uppercase' }}>{item.label}</div>
              <div style={{ marginTop:6, color:tone, fontSize:modeled || limited ? 17 : 13, fontWeight:850, lineHeight:1.1, fontFamily:modeled || limited ? V2.fontMono : V2.font }}>
                {v2TradeHorizonValue(item)}
              </div>
              <div style={{ marginTop:6, color:V2.muted, fontSize:10.5, lineHeight:1.35 }}>{item.detail}</div>
            </div>
          );
        })}
      </div>

      <div style={{ background:fit.fills_weakest_position ? V2.okSoft : V2.accentSoft, borderRadius:13, padding:'11px 12px' }}>
        <V2Eyebrow color={fit.fills_weakest_position ? V2.ok : V2.accent}>Roster fit</V2Eyebrow>
        <div style={{ marginTop:6, color:V2.ink, fontSize:13.5, fontWeight:800 }}>{fit.label || 'Manual roster-fit review'}</div>
        <div style={{ marginTop:4, color:V2.body, fontSize:11.5, lineHeight:1.45 }}>{fit.detail}</div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns:counter ? '1fr 1fr' : '1fr', gap:8 }}>
        {counter ? (
          <button onClick={scrollToCounters} style={{
            minHeight:44, border:'none', borderRadius:999, background:V2.ink, color:'#fff',
            fontFamily:'inherit', fontSize:12, fontWeight:850, cursor:'pointer', padding:'10px 12px',
          }}>Review recommended counter</button>
        ) : null}
        <button onClick={()=>onAskSkipper && onAskSkipper(analysis.skipper_prompt)} disabled={!onAskSkipper} style={{
          minHeight:44, border:`1px solid ${V2.hairline}`, borderRadius:999, background:V2.surface,
          color:V2.body, fontFamily:'inherit', fontSize:12, fontWeight:850,
          cursor:onAskSkipper ? 'pointer' : 'not-allowed', padding:'10px 12px', opacity:onAskSkipper ? 1 : 0.6,
        }}>Ask Skipper</button>
      </div>

      <div style={{ textAlign:'center', color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>
        Analysis only · never auto-accepts · complete any trade manually in Fantrax
      </div>
    </div>
  );
}

function V2TradeDecisionReceipt({ initialReceipt, expectedGive, expectedGet }) {
  const [receipt, setReceipt] = React.useState(initialReceipt || null);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState(null);
  React.useEffect(() => { setReceipt(initialReceipt || null); setError(null); }, [initialReceipt?.receipt_id]);
  const bridge = useV2OwnerBridge(
    receipt ? `${receipt.receipt_id}:${receipt.input_hash}` : 'trade-receipt-none',
    { enabled:Boolean(receipt) },
  );
  if (!receipt) return null;
  const pending = receipt.decision_state === 'pending';
  const boundGive = Array.isArray(receipt?.trade?.give) ? receipt.trade.give : [];
  const boundGet = Array.isArray(receipt?.trade?.get) ? receipt.trade.get : [];
  const ids = rows => (rows || []).map(item => String(item?.player_id || item?.id || '')).filter(Boolean).sort();
  const sameIds = (left, right) => left.length === right.length && left.every((value, index) => value === right[index]);
  const identityMatches = sameIds(ids(boundGive), ids(expectedGive)) && sameIds(ids(boundGet), ids(expectedGet));
  const expiresAt = Date.parse(String(receipt.expires_at || ''));
  const expired = !Number.isFinite(expiresAt) || expiresAt <= Date.now();
  const canDecide = pending && identityMatches && !expired && bridge.state === 'ready' && bridge.decisionsEnabled && bridge.nonce;
  const decide = async decision => {
    if (!canDecide || saving) return;
    const deadline = Date.parse(String(receipt.expires_at || ''));
    if (!Number.isFinite(deadline) || deadline <= Date.now()) {
      setError('This assessment expired. Refresh Fantrax data and re-grade before deciding.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(
        `${V2_OWNER_BRIDGE_URL}/recommendation-receipts/${encodeURIComponent(receipt.receipt_id)}/decision`,
        {
          method:'POST', targetAddressSpace:'local',
          headers:{ 'content-type':'application/json', 'X-Sandlot-Bridge-Nonce':bridge.nonce },
          body:JSON.stringify({ decision, input_hash:receipt.input_hash }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || 'Could not record this trade decision.');
      if (
        body?.receipt_id !== receipt.receipt_id
        || String(body?.input_hash || '').toLowerCase() !== String(receipt.input_hash || '').toLowerCase()
        || body?.decision_state !== decision
        || body?.action_type !== 'trade_assessment'
        || body?.fantrax_changed !== false
        || body?.writes_enabled !== false
      ) throw new Error('Decision response did not preserve the exact manual-trade boundary.');
      setReceipt(body);
    } catch (reason) {
      setError(reason?.message || 'Could not record this trade decision.');
    } finally {
      setSaving(false);
    }
  };
  const recorded = receipt.decision_state === 'accepted' ? 'Intent to accept recorded' : receipt.decision_state === 'rejected' ? 'Pass recorded' : null;
  const names = rows => rows.map(item => item.player_name).filter(Boolean).join(', ') || 'Unavailable';
  return (
    <section aria-label="Exact trade decision" style={{ background:V2.surface2, border:`1px solid ${V2.hairline2}`, borderRadius:15, padding:'12px 13px' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:10 }}>
        <V2Eyebrow color={recorded ? (receipt.decision_state === 'accepted' ? V2.ok : V2.muted) : V2.accent}>Exact offer receipt</V2Eyebrow>
        <span style={{ color:V2.muted, fontFamily:V2.fontMono, fontSize:9.5, fontWeight:800 }}>#{String(receipt.receipt_id || '').slice(-8)}</span>
      </div>
      <div style={{ marginTop:7, color:V2.body, fontSize:11.5, lineHeight:1.45, fontWeight:700, textWrap:'pretty' }}>
        {recorded || 'Record your current intent against this exact snapshot.'} This never accepts, rejects, or counters in Fantrax.
      </div>
      <div style={{ marginTop:8, paddingTop:8, borderTop:`1px solid ${V2.hairline2}`, color:V2.muted, fontSize:10.5, lineHeight:1.45 }}>
        <strong style={{ color:V2.body }}>Give:</strong> {names(boundGive)}<br/>
        <strong style={{ color:V2.body }}>Get:</strong> {names(boundGet)}
      </div>
      {pending && !identityMatches ? (
        <div role="alert" style={{ marginTop:8, color:V2.bad, fontSize:10.5, lineHeight:1.4, fontWeight:750 }}>Receipt does not match the displayed offer. Edit and re-grade before deciding.</div>
      ) : pending && expired ? (
        <div role="status" style={{ marginTop:8, color:V2.warn, fontSize:10.5, lineHeight:1.4, fontWeight:750 }}>This assessment expired. Refresh Fantrax data and re-grade before deciding.</div>
      ) : pending && !canDecide ? (
        <div style={{ marginTop:8, color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>
          {bridge.state === 'connecting' ? 'Checking owner controls on this Mac…' : 'Start the local owner bridge to record your intent.'}
        </div>
      ) : null}
      {error ? <div role="alert" style={{ marginTop:8, color:V2.bad, fontSize:11, fontWeight:750 }}>{error}</div> : null}
      {canDecide ? (
        <div style={{ marginTop:10, display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
          <button onClick={()=>decide('accepted')} disabled={saving} style={{ minHeight:44, border:'none', borderRadius:999, background:V2.ink, color:'#fff', fontFamily:'inherit', fontSize:11.5, fontWeight:850, cursor:saving ? 'wait' : 'pointer', opacity:saving ? 0.65 : 1 }}>Record intent to accept</button>
          <button onClick={()=>decide('rejected')} disabled={saving} style={{ minHeight:44, border:`1px solid ${V2.hairline}`, borderRadius:999, background:V2.surface, color:V2.body, fontFamily:'inherit', fontSize:11.5, fontWeight:850, cursor:saving ? 'wait' : 'pointer', opacity:saving ? 0.65 : 1 }}>Pass</button>
        </div>
      ) : null}
    </section>
  );
}

function V2TradeGradeCard({ result, onAskSkipper }) {
  const fairness = Math.max(0, Math.min(1, Number(result.fairness) || 0));
  const fairnessPct = (fairness * 100).toFixed(0);
  const dash = (fairness * 188.5).toFixed(1);
  const grade = result.letter_grade || '—';
  const color = v2GradeColor(grade);
  const myDelta = Number(result.my_delta) || 0;
  const theirDelta = Number(result.their_delta) || 0;
  const ageDelta = result.age_delta;
  const fmt = (n) => `${n >= 0 ? '+' : ''}${Number(n).toFixed(1)}`;
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
      <V2TradeAnalysisSummary result={result} onAskSkipper={onAskSkipper}/>
      <div style={{ marginTop:12 }}><V2TradeDecisionReceipt initialReceipt={result.receipt} expectedGive={result.my_give} expectedGet={result.my_get}/></div>
      <div style={{ margin:'16px 0', borderTop:`1px solid ${V2.hairline2}` }}/>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:14 }}>
        <div style={{ flex:1, minWidth:0 }}>
          <V2Eyebrow color={color}>Current-rate grade</V2Eyebrow>
          <div style={{
            fontSize:48, fontWeight:600, color, letterSpacing:'-0.03em', lineHeight:1,
            marginTop:6, fontFamily:V2.fontDisplay,
          }}>{grade}</div>
          <div style={{ fontSize:12, color:V2.body, marginTop:8, fontWeight:600 }}>{result.headline || ''}</div>
          {result.my_weakest_position ? (
            <div style={{ marginTop:10, display:'inline-flex', alignItems:'center', gap:6, background:V2.warnSoft, color:V2.warn, borderRadius:999, padding:'5px 9px', fontSize:10.5, fontWeight:900, letterSpacing:'0.04em', textTransform:'uppercase' }}>
              Weakest: {result.my_weakest_position}
            </div>
          ) : null}
        </div>
        <div style={{ width:74, height:74, position:'relative', flexShrink:0 }}>
          <svg width="74" height="74" viewBox="0 0 74 74">
            <circle cx="37" cy="37" r="30" fill="none" stroke={V2.hairline} strokeWidth="6"/>
            <circle cx="37" cy="37" r="30" fill="none" stroke={color} strokeWidth="6"
              strokeDasharray={`${dash} 188.5`} transform="rotate(-90 37 37)" strokeLinecap="round"/>
          </svg>
          <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
            <div style={{ fontSize:14, fontWeight:700, fontFamily:V2.fontMono }}>{fairnessPct}</div>
            <div style={{ fontSize:8.5, color:V2.muted, fontWeight:700, letterSpacing:'0.06em' }}>FAIR</div>
          </div>
        </div>
      </div>

      <div style={{ marginTop:14, paddingTop:14, borderTop:`1px solid ${V2.hairline2}` }}>
        <V2StatRow stats={[
          { value: fmt(myDelta), label:'Your FP/G Δ', color: myDelta >= 0 ? V2.ok : V2.injured },
          { value: fmt(theirDelta), label:'Their FP/G Δ', color: theirDelta >= 0 ? V2.ok : V2.warn },
          {
            value: ageDelta == null ? '—' : `${ageDelta > 0 ? '+' : ''}${Number(ageDelta).toFixed(1)} yr`,
            label:'Avg age Δ',
            color: ageDelta == null ? V2.muted : (ageDelta <= 0 ? V2.ok : V2.warn),
          },
        ]}/>
      </div>

      <div style={{ marginTop:10, color:V2.muted, fontSize:11.5, lineHeight:1.45 }}>
        Rate-only comparison from the current snapshot. It excludes prospects and age 24-or-younger players and is not a complete dynasty valuation.
      </div>

      {result.rationale ? (
        <div style={{ marginTop:14, background:V2.surface2, borderRadius:12, padding:12, display:'flex', gap:10 }}>
          <div style={{ flexShrink:0, marginTop:1 }}>{Icons.sparkle(V2.accent, 14)}</div>
          <div style={{ fontSize:13, color:V2.body, lineHeight:1.55 }}>{result.rationale}</div>
        </div>
      ) : null}

      {(result.model || result.cached) ? (
        <div style={{ marginTop:10, display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <div style={{ fontSize:10, color:V2.muted, fontFamily:V2.fontMono, letterSpacing:'0.04em' }}>
            {result.cached ? 'CACHED' : 'FRESH'}{result.model ? ` · ${result.model}` : ''}
          </div>
        </div>
      ) : null}

      {result.no_counter_reason ? (
        <div style={{ marginTop:14, background:V2.warnSoft, borderLeft:`2px solid ${V2.warn}`, borderRadius:'4px 12px 12px 4px', padding:'10px 12px', color:V2.body, fontSize:12.5, lineHeight:1.45 }}>
          {result.no_counter_reason}
        </div>
      ) : null}

      {(result.counters || []).length ? (
        <div id="trade-counters" style={{ marginTop:14, paddingTop:14, borderTop:`1px solid ${V2.hairline2}`, display:'flex', flexDirection:'column', gap:8, scrollMarginTop:12 }}>
          <V2Eyebrow color={V2.accent}>Counters</V2Eyebrow>
          {(result.counters || []).map(counter => <V2TradeCounterCard key={counter.tier} counter={counter}/>)}
        </div>
      ) : null}
    </div>
  );
}

function V2TradeCounterCard({ counter }) {
  const [open, setOpen] = React.useState(false);
  const tier = String(counter.tier || '').toUpperCase();
  const band = counter.acceptance_band || counter.counter_strength || 'review';
  const myDelta = Number(counter.my_delta) || 0;
  const names = (rows) => (rows || []).map(p => p.name).filter(Boolean).join(', ') || '—';
  return (
    <div style={{ border:`1px solid ${V2.hairline2}`, borderRadius:12, overflow:'hidden', background:V2.surface2 }}>
      <button onClick={()=>setOpen(!open)} style={{
        width:'100%', border:'none', background:'transparent', padding:'11px 12px',
        display:'grid', gridTemplateColumns:'1fr auto', gap:10, textAlign:'left', cursor:'pointer', fontFamily:'inherit',
      }}>
        <div style={{ minWidth:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:7, flexWrap:'wrap' }}>
            <span style={{ color:V2.accent, fontSize:10.5, fontWeight:900, letterSpacing:'0.08em' }}>{tier}</span>
            <span style={{ background:V2.surface, color:V2.muted, borderRadius:999, padding:'3px 7px', fontSize:10, fontWeight:800, textTransform:'uppercase' }}>{band}</span>
          </div>
          <div style={{ marginTop:6, color:V2.ink, fontSize:12.5, fontWeight:750, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
            Get {names(counter.get)}
          </div>
          <div style={{ marginTop:3, color:V2.muted, fontSize:11.2, fontWeight:650, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
            Give {names(counter.give)}
          </div>
        </div>
        <div style={{ textAlign:'right', color:myDelta >= 0 ? V2.ok : V2.warn, fontFamily:V2.fontMono, fontSize:12, fontWeight:900, whiteSpace:'nowrap' }}>
          {myDelta >= 0 ? '+' : ''}{myDelta.toFixed(1)}
          <div style={{ color:V2.muted, fontSize:9, marginTop:3 }}>FP/G</div>
        </div>
      </button>
      {open ? (
        <div style={{ borderTop:`1px solid ${V2.hairline2}`, padding:'10px 12px', color:V2.body, fontSize:12.5, lineHeight:1.5 }}>
          {counter.rationale || 'No rationale returned for this counter.'}
        </div>
      ) : null}
    </div>
  );
}

function V2TradeGrader({ model, onAskSkipper }) {
  const [give, setGive] = React.useState([]);
  const [get, setGet] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [result, setResult] = React.useState(null);
  const [editing, setEditing] = React.useState(true);
  const [incoming, setIncoming] = React.useState({ state:'loading', snapshotId:null, freshness:null, offers:[], error:null });
  const [reviewingTradeId, setReviewingTradeId] = React.useState(null);
  const gradeRequestRef = React.useRef(0);
  const gradeSnapshotRef = React.useRef(model?.snapshotId ?? null);

  React.useEffect(() => {
    const nextSnapshotId = model?.snapshotId ?? null;
    const priorSnapshotId = gradeSnapshotRef.current;
    gradeSnapshotRef.current = nextSnapshotId;
    if (priorSnapshotId === null || nextSnapshotId === priorSnapshotId) return;
    gradeRequestRef.current += 1;
    setLoading(false);
    setReviewingTradeId(null);
    setResult(null);
    setGive([]);
    setGet([]);
    setEditing(true);
    setError('Fantrax data refreshed. Review the offer again against the new snapshot.');
    setIncoming({ state:'loading', snapshotId:null, freshness:null, offers:[], error:null });
  }, [model?.snapshotId]);

  React.useEffect(() => {
    let cancelled = false;
    fetch('/api/trades/incoming', { cache:'no-store' })
      .then(async response => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body?.detail || 'Incoming offers are unavailable.');
        if (body?.read_only !== true || body?.fantrax_changed !== false || body?.writes_enabled !== false) {
          throw new Error('Incoming offers did not preserve the read-only boundary.');
        }
        if (!cancelled) setIncoming({ state:'ready', snapshotId:body.snapshot_id, freshness:body.freshness || null, offers:Array.isArray(body.offers) ? body.offers : [], error:null });
      })
      .catch(reason => { if (!cancelled) setIncoming({ state:'error', snapshotId:null, freshness:null, offers:[], error:reason?.message || 'Incoming offers are unavailable.' }); });
    return () => { cancelled = true; };
  }, [model?.snapshotId]);

  const ready = give.length > 0 && get.length > 0 && !loading;

  const gradeExact = async (giveRows, getRows, incomingOffer=null) => {
    if (!giveRows.length || !getRows.length || loading) return;
    const requestId = ++gradeRequestRef.current;
    setLoading(true);
    setReviewingTradeId(incomingOffer?.trade_id || null);
    setError(null);
    try {
      const res = await fetch('/api/trades/grade', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          give:giveRows.map(p => p.id), get:getRows.map(p => p.id),
          ...(incomingOffer ? { incoming_trade_id:incomingOffer.trade_id, incoming_snapshot_id:incoming.snapshotId } : {}),
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = body && body.detail ? String(body.detail) : `Grade failed (${res.status})`;
        throw new Error(detail);
      }
      if (requestId !== gradeRequestRef.current) return;
      setResult(body);
      setEditing(false);
    } catch (e) {
      if (requestId === gradeRequestRef.current) setError(e && e.message ? e.message : String(e));
    } finally {
      if (requestId === gradeRequestRef.current) {
        setLoading(false);
        setReviewingTradeId(null);
      }
    }
  };
  const grade = () => gradeExact(give, get);
  const reviewIncoming = offer => {
    const index = new Map(((model && model.playerIndex) || []).map(player => [String(player.id || ''), player]));
    const giveRows = (offer.give || []).map(item => index.get(String(item.player_id || ''))).filter(Boolean);
    const getRows = (offer.get || []).map(item => index.get(String(item.player_id || ''))).filter(Boolean);
    if (giveRows.length !== (offer.give || []).length || getRows.length !== (offer.get || []).length) {
      setError('This offer references a player missing from the current snapshot. Refresh before reviewing it.');
      return;
    }
    setGive(giveRows);
    setGet(getRows);
    setEditing(false);
    setResult(null);
    gradeExact(giveRows, getRows, offer);
  };

  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      {incoming.state === 'ready' && incoming.offers.length ? (
        <section aria-labelledby="incoming-trades-title" style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:20, padding:'14px 15px' }}>
          <V2Eyebrow color={V2.accent}>From Fantrax</V2Eyebrow>
          <h2 id="incoming-trades-title" style={{ margin:'6px 0 0', color:V2.ink, fontFamily:V2.fontDisplay, fontSize:21, lineHeight:1.08, letterSpacing:'-0.02em' }}>Incoming offers</h2>
          <div style={{ marginTop:5, color:V2.muted, fontSize:11.5, lineHeight:1.4 }}>Read-only {incoming.freshness?.state || 'stored'} snapshot · reviewing never answers the offer</div>
          <div style={{ marginTop:11, display:'flex', flexDirection:'column', gap:8 }}>
            {incoming.offers.map((offer, index) => (
              <div key={offer.trade_id || index} style={{ background:V2.surface2, borderRadius:14, padding:'11px 12px' }}>
                <div style={{ color:V2.body, fontSize:11.5, fontWeight:800 }}>{offer.proposed_by || 'Another team'}</div>
                <div style={{ marginTop:6, color:V2.ink, fontSize:12.5, lineHeight:1.4, fontWeight:750 }}>You get {(offer.get || []).map(item=>item.player_name).filter(Boolean).join(', ') || '—'}</div>
                <div style={{ marginTop:2, color:V2.muted, fontSize:11.5, lineHeight:1.4, fontWeight:650 }}>You give {(offer.give || []).map(item=>item.player_name).filter(Boolean).join(', ') || '—'}</div>
                {offer.includes_draft_pick ? <div style={{ marginTop:5, color:V2.warn, fontSize:10.5, lineHeight:1.35, fontWeight:750 }}>Includes a draft pick · picks are not modeled yet</div> : null}
                {offer.manual_review_reason && !offer.manual_review ? <div style={{ marginTop:5, color:V2.warn, fontSize:10.5, lineHeight:1.35, fontWeight:750 }}>{v2TradeManualReviewCopy(offer.manual_review_reason)}</div> : null}
                {offer.manual_review ? (
                  <V2ManualTradeReview offer={offer} onAskSkipper={onAskSkipper}/>
                ) : (
                  <button onClick={()=>reviewIncoming(offer)} disabled={!offer.gradeable || loading} style={{ marginTop:9, minHeight:44, width:'100%', border:'none', borderRadius:999, background:offer.gradeable ? V2.ink : V2.hairline, color:offer.gradeable ? '#fff' : V2.muted, fontFamily:'inherit', fontSize:11.5, fontWeight:850, cursor:offer.gradeable && !loading ? 'pointer' : 'not-allowed' }}>
                    {offer.gradeable ? (reviewingTradeId === offer.trade_id ? 'Reviewing…' : 'Review exact offer') : offer.status === 'awaiting_execution' ? 'Already accepted in Fantrax' : 'Manual review required'}
                  </button>
                )}
                {!offer.gradeable && !offer.includes_draft_pick && !offer.manual_review_reason && offer.status !== 'awaiting_execution' ? <div style={{ marginTop:6, color:V2.muted, fontSize:10.5, lineHeight:1.35 }}>This offer is incomplete, stale, or includes terms Sandlot cannot model exactly. Open Fantrax to inspect it.</div> : null}
              </div>
            ))}
          </div>
        </section>
      ) : incoming.state === 'error' ? (
        <div role="status" aria-live="polite" style={{ color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>Incoming Fantrax offers could not be checked. You can still build an offer below.</div>
      ) : incoming.state === 'loading' ? (
        <div role="status" aria-live="polite" style={{ color:V2.muted, fontSize:10.5, lineHeight:1.4 }}>Checking incoming Fantrax offers…</div>
      ) : null}
      {loading && !result ? (
        <div role="status" aria-live="polite" style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16, color:V2.body, fontSize:12.5, fontWeight:750 }}>Reviewing the exact offer against snapshot {incoming.snapshotId || model?.snapshotId || '—'}…</div>
      ) : editing || !result ? (
        <>
          <V2PlayerPicker label="You give" source="mine" model={model} value={give} onChange={setGive}/>
          <V2PlayerPicker label="You get" source="league" model={model} value={get} onChange={setGet}/>
          <div>
            <V2Primary onClick={grade} disabled={!ready} sub={ready ? 'Snapshot · cached on repeat' : 'Pick at least one player on each side'}>
              {Icons.sparkle('#fff', 14)} {loading ? 'Grading…' : (result ? 'Re-grade' : 'Grade')}
            </V2Primary>
          </div>
        </>
      ) : (
        <div style={{ background:V2.surface2, border:`1px solid ${V2.hairline}`, borderRadius:15, padding:'11px 12px', display:'flex', alignItems:'center', gap:10 }}>
          <div style={{ flex:1, minWidth:0 }}>
            <V2Eyebrow color={V2.accent}>Offer reviewed</V2Eyebrow>
            <div style={{ marginTop:5, color:V2.ink, fontSize:12.5, fontWeight:750, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
              Give {give.map(p=>p.name).join(', ')}
            </div>
            <div style={{ marginTop:2, color:V2.muted, fontSize:11.5, fontWeight:650, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
              Get {get.map(p=>p.name).join(', ')}
            </div>
          </div>
          <button onClick={()=>{ setEditing(true); setResult(null); setError(null); }} style={{
            flexShrink:0, minHeight:40, border:`1px solid ${V2.hairline}`, borderRadius:999,
            background:V2.surface, color:V2.body, fontFamily:'inherit', fontSize:11.5,
            fontWeight:800, cursor:'pointer', padding:'8px 11px',
          }}>Edit offer</button>
        </div>
      )}

      {error ? (
        <div role="alert" style={{
          background:V2.badSoft, border:`1px solid ${V2.bad}`, borderRadius:12, padding:'10px 12px',
          fontSize:12.5, color:V2.bad,
        }}>{error}</div>
      ) : null}

      <div aria-live="polite">
        {result ? <V2TradeGradeCard result={result} onAskSkipper={onAskSkipper}/> : null}
      </div>
    </div>
  );
}

// ── /settings ──────────────────────────────────────────────────
function V2Settings({ model, sync, onRefresh, onSignOut }) {
  const syncTone = v2SyncTone(sync);
  const syncHeadline = sync.state === 'failed'
    ? 'Refresh failed'
    : sync.state === 'refreshing'
      ? 'Refreshing…'
      : sync.state === 'loading'
        ? 'Loading snapshot…'
        : `Synced ${sync.label}`;
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>Fantrax sync</V2Eyebrow>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginTop:8 }}>
          <div>
            <div style={{ fontSize:18, fontWeight:600, fontFamily:V2.fontDisplay }}>{syncHeadline}</div>
            <div style={{ fontSize:11.5, color:V2.muted, marginTop:3 }}>Railway Postgres · manual refresh + scheduled refresh</div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:6, background:syncTone.bg, color:syncTone.color, padding:'6px 11px', borderRadius:999 }}>
            <div style={{ width:6, height:6, background:syncTone.color, borderRadius:'50%' }}/>
            <span style={{ fontSize:11.5, fontWeight:700 }}>{syncTone.label}</span>
          </div>
        </div>
      </div>

      <V2Caution eyebrow="Cookie-backed scraping">
        Railway uses the stored Fantrax cookie from Postgres. If it expires, refresh locally and run the cookie bootstrap script again.
      </V2Caution>

      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>Snapshot</V2Eyebrow>
        <div style={{ fontSize:18, fontWeight:600, marginTop:8, fontFamily:V2.fontDisplay }}>{model.teamName}</div>
        <div style={{ fontSize:11.5, color:V2.muted, marginTop:3 }}>{model.roster.length} players · {model.leagueTeams.length} teams</div>
        <div style={{ display:'flex', gap:8, marginTop:14 }}>
          <button onClick={onRefresh} disabled={sync.state === 'refreshing'} aria-label={sync.state === 'refreshing' ? 'Refreshing Fantrax data' : 'Refresh Fantrax data'} style={{ background:V2.ink, color:'#fff', border:'none', padding:'10px 14px', borderRadius:999, fontSize:12.5, fontWeight:700, cursor:sync.state === 'refreshing' ? 'not-allowed' : 'pointer', opacity:sync.state === 'refreshing' ? 0.7 : 1, fontFamily:'inherit' }}>{sync.state === 'refreshing' ? 'Refreshing…' : 'Refresh now'}</button>
          <button onClick={onSignOut} style={{ background:'none', color:V2.injured, border:`1px solid ${V2.injured}33`, padding:'10px 14px', borderRadius:999, fontSize:12.5, fontWeight:700, cursor:'pointer', fontFamily:'inherit' }}>Sign out</button>
        </div>
      </div>

      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>About</V2Eyebrow>
        <div style={{ fontSize:13, color:V2.body, marginTop:8, lineHeight:1.55 }}>
          FastAPI · Railway Postgres · Railway cron. Built for {model.leagueName}.
        </div>
      </div>
    </div>
  );
}

// ── Player sheet (inlined from D2 + V2 tokens) ─────────────────
function useV2DialogFocus(onClose) {
  const dialogRef = React.useRef(null);
  const closeButtonRef = React.useRef(null);
  const onCloseRef = React.useRef(onClose);
  onCloseRef.current = onClose;

  React.useEffect(() => {
    const previousFocus = document.activeElement;
    closeButtonRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
      )].filter(node => node.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };
  }, []);

  return { dialogRef, closeButtonRef };
}

function V2PlayerSheet({ id, onClose }) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [syncing, setSyncing] = React.useState(false);
  const [syncCooldown, setSyncCooldown] = React.useState(false);
  const [activeClip, setActiveClip] = React.useState(null);
  const requestSeqRef = React.useRef(0);
  const cooldownTimerRef = React.useRef(null);
  const { dialogRef, closeButtonRef } = useV2DialogFocus(onClose);

  React.useEffect(() => () => {
    if (cooldownTimerRef.current) clearTimeout(cooldownTimerRef.current);
  }, []);

  React.useEffect(() => { setActiveClip(null); }, [id]);

  const load = React.useCallback(async () => {
    const requestSeq = ++requestSeqRef.current;
    setSyncing(false);
    setLoading(true); setError(null); setData(null);
    try {
      const r = await fetch(`/api/player/${encodeURIComponent(id)}`);
      if (!r.ok) {
        const text = await r.text().catch(()=>'');
        throw new Error(text.slice(0, 300) || `Failed (${r.status})`);
      }
      const nextData = await r.json();
      if (requestSeq !== requestSeqRef.current) return;
      setData(nextData);
    } catch (e) {
      if (requestSeq !== requestSeqRef.current) return;
      setError(e.message || 'Failed to load player');
    } finally {
      if (requestSeq === requestSeqRef.current) setLoading(false);
    }
  }, [id]);

  React.useEffect(() => { load(); }, [load]);

  // Poll for the Skipper take if it's still being generated server-side.
  React.useEffect(() => {
    if (!data) return;
    const take = data.take || {};
    const takeState = ((data.profile_cache || {}).take || {}).state;
    const takePending = ((data.profile_cache || {}).take || {}).pending === true;
    const stillGenerating = !take.text && !take.error && (takePending || takeState === 'missing');
    if (!stillGenerating) return;

    let attempts = 0;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const r = await fetch(`/api/player/${encodeURIComponent(id)}`);
        if (!r.ok || cancelled) return;
        const next = await r.json();
        if (cancelled) return;
        setData(next);
        const nextTake = next.take || {};
        const nextPending = ((next.profile_cache || {}).take || {}).pending === true;
        const nextState = ((next.profile_cache || {}).take || {}).state;
        const done = nextTake.text || nextTake.error || (!nextPending && nextState !== 'missing');
        if (done || attempts >= 12) return;
        timer = setTimeout(tick, 3500);
      } catch (_) {
        if (attempts < 12 && !cancelled) timer = setTimeout(tick, 5000);
      }
    };
    let timer = setTimeout(tick, 2500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id, data]);

  const sync = async () => {
    if (syncing || syncCooldown) return;
    const requestSeq = ++requestSeqRef.current;
    setSyncing(true); setError(null);
    try {
      const r = await fetch(`/api/player/${encodeURIComponent(id)}/refresh`, { method:'POST' });
      if (!r.ok) {
        const text = await r.text().catch(()=>'');
        throw new Error(text.slice(0, 300) || `Sync failed (${r.status})`);
      }
      const nextData = await r.json();
      if (requestSeq !== requestSeqRef.current) return;
      setData(nextData);
    } catch (e) {
      if (requestSeq !== requestSeqRef.current) return;
      setError(e.message || 'Sync failed');
    } finally {
      if (requestSeq !== requestSeqRef.current) return;
      setLoading(false);
      setSyncing(false);
      setSyncCooldown(true);
      if (cooldownTimerRef.current) clearTimeout(cooldownTimerRef.current);
      cooldownTimerRef.current = setTimeout(() => {
        cooldownTimerRef.current = null;
        setSyncCooldown(false);
      }, 5000);
    }
  };

  const freshness = data?.snapshot_freshness;
  const fState = freshness?.state;
  const fAge = freshness?.age_minutes;
  const fColor = fState === 'fresh' ? V2.ok : fState === 'stale' ? V2.warn : fState === 'old' ? V2.bad : V2.muted;
  const fLabel = fState ? v2SyncLabel(freshness) : null;

  return (
    <div onClick={onClose} style={{ position:'absolute', inset:0, background:'rgba(15,23,42,0.32)', display:'flex', alignItems:'flex-end', zIndex:10 }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-label="Player details" onClick={e=>e.stopPropagation()} style={{
        background:V2.bg, borderTopLeftRadius:18, borderTopRightRadius:18, width:'100%', height:'88%', overflow:'auto',
        display:'flex', flexDirection:'column',
      }}>
        <div style={{ height:5, width:42, background:V2.hairline, borderRadius:3, margin:'10px auto', flexShrink:0 }}/>
        <div style={{
          padding:'4px 14px 10px', display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, flexShrink:0,
        }}>
          <button ref={closeButtonRef} onClick={onClose} style={{
            background:'none', border:'none', padding:6, cursor:'pointer', width:40, height:40,
            display:'flex', alignItems:'center', justifyContent:'center',
          }} aria-label="Close">
            {Icons.close(V2.muted, 14)}
          </button>
          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
            {fLabel && (
              <div title={`Snapshot ${fAge != null ? `${fAge}m old` : 'age unknown'}`} style={{
                display:'flex', alignItems:'center', gap:6,
                padding:'5px 10px', borderRadius:999,
                background:V2.surface, border:`1px solid ${V2.hairline}`,
              }}>
                <span style={{ width:6, height:6, background:fColor, borderRadius:'50%' }}/>
                <span style={{ fontSize:11, color:V2.body, fontWeight:700 }}>{fLabel}</span>
              </div>
            )}
            <button onClick={sync} disabled={syncing || syncCooldown} title={syncCooldown ? 'Hold on a sec…' : 'Force fresh MLB pull'} style={{
              background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:999,
              padding:'7px 12px', display:'flex', alignItems:'center', gap:7,
              cursor: (syncing || syncCooldown) ? 'not-allowed' : 'pointer',
              opacity: (syncing || syncCooldown) ? 0.6 : 1,
              fontFamily:'inherit',
            }}>
              <span style={{ fontSize:13, color:V2.body, fontWeight:700 }}>{syncing ? 'Syncing…' : 'Sync'}</span>
            </button>
          </div>
        </div>
        <div style={{ flex:1, overflow:'auto', padding:'4px 16px 28px', display:'flex', flexDirection:'column', gap:14 }}>
          {loading && !data && <V2ProfileSkeleton/>}
          {error && !data && (
            <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, padding:14, color:V2.bad, fontSize:13 }}>
              <div style={{ fontWeight:700, marginBottom:6 }}>Couldn't load player</div>
              <div style={{ color:V2.body, lineHeight:1.4 }}>{error}</div>
              <button onClick={load} style={{
                marginTop:10, padding:'8px 14px', borderRadius:999, border:`1px solid ${V2.hairline}`,
                background:V2.surface2, color:V2.ink, fontSize:12, fontWeight:700, cursor:'pointer', fontFamily:'inherit',
              }}>Retry</button>
            </div>
          )}
          {data && <V2ProfileBody data={data} onOpenClip={setActiveClip}/>}
          {data && error && (
            <div style={{ color:V2.bad, fontSize:12.5, padding:'2px 4px' }}>{error}</div>
          )}
        </div>
      </div>
      {activeClip && <V2ClipViewer clip={activeClip} onClose={() => setActiveClip(null)}/>}
    </div>
  );
}

function V2ProfileSkeleton() {
  const bar = (h, w='100%', mt=10) => (
    <div style={{ height:h, width:w, marginTop:mt, background:V2.surface2, borderRadius:8 }}/>
  );
  return (
    <>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        {bar(20, '60%', 0)}
        {bar(12, '40%')}
      </div>
      {bar(80)}
      {bar(80)}
      {bar(60)}
      {bar(160)}
    </>
  );
}

function V2ProfileBody({ data, onOpenClip }) {
  const p = data.player || {};
  const trend = data.trend;
  const games = data.games || [];
  const sparkline = data.sparkline || [];
  const isPitcher = data.group === 'pitching';
  const status = String(p.injury || '').toLowerCase();
  const statusLabel = status ? (STATUS_LABEL[status] || p.injury) : 'Active';
  const statusOk = !status || status === 'ok' || status === 'active';

  return (
    <>
      <V2ProfileHero player={p} mlb={data.mlb} take={data.take} statusLabel={statusLabel} statusOk={statusOk}/>
      {data.mlb?.available === false && (
        <div style={{ background:V2.warnSoft, color:V2.warn, border:`1px solid ${V2.warn}33`, borderRadius:14, padding:'12px 14px', fontSize:12.5, fontWeight:600 }}>
          {data.mlb.reason || 'MLB stats not available for this player.'}
        </div>
      )}
      <V2ProfileStats trend={trend} games={games} sparkline={sparkline} isPitcher={isPitcher} season={data.season}/>
      <V2ProfileClips clips={data.media?.items || data.clips} player={p} onOpenClip={onOpenClip}/>
    </>
  );
}

function V2ProfileHero({ player, mlb, take, statusLabel, statusOk }) {
  const ageBit = player.age ? ` · Age ${player.age}` : '';
  const teamBit = player.team ? `${player.team}` : '';
  const posBit = player.positions || player.slot || '';
  const meta = [teamBit, posBit].filter(Boolean).join(' · ') + ageBit;
  const ownerLabel = player.source === 'my_roster'
    ? 'On your roster'
    : player.source === 'league_roster'
      ? `On ${player.owner_team_name || 'another team'}`
      : player.source === 'free_agent'
        ? 'Free agent'
        : null;
  const takeText = take?.text;
  const takeError = take?.error;
  return (
    <div style={{
      background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18,
      padding:18, display:'flex', flexDirection:'column', gap:16,
    }}>
      <div style={{ display:'flex', alignItems:'flex-start', gap:14 }}>
        <PlayerPhoto mlbId={mlb?.mlb_id} name={player.name || '?'} size={84}/>
        <div style={{ flex:1, minWidth:0, display:'flex', flexDirection:'column', gap:8, paddingTop:4 }}>
          <div style={{
            fontSize:24, fontWeight:600, fontFamily:V2.fontDisplay,
            letterSpacing:'-0.02em', lineHeight:1.05, color:V2.ink,
            overflow:'hidden', display:'-webkit-box', WebkitLineClamp:2, WebkitBoxOrient:'vertical',
          }}>{player.name || '?'}</div>
          <div style={{ fontSize:11, color:V2.muted, fontWeight:800, letterSpacing:'0.06em', textTransform:'uppercase' }}>{meta || 'Player'}</div>
          <div style={{ display:'flex', flexWrap:'wrap', gap:6, paddingTop:2 }}>
            {ownerLabel && (
              <span style={{ background:V2.accentSoft, color:V2.accent, fontSize:10, fontWeight:800, padding:'4px 9px', borderRadius:999, letterSpacing:'0.06em', textTransform:'uppercase' }}>{ownerLabel}</span>
            )}
            <span style={{
              background: statusOk ? V2.benchSoft : V2.injuredSoft,
              color: statusOk ? V2.bench : V2.injured,
              fontSize:10, fontWeight:800, padding:'4px 9px', borderRadius:999, letterSpacing:'0.06em', textTransform:'uppercase',
            }}>{statusLabel}</span>
          </div>
        </div>
      </div>
      <div style={{ height:1, background:V2.hairline2 }}/>
      <div style={{ display:'flex', flexDirection:'column', gap:9 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <span style={{ display:'inline-flex', alignItems:'center', justifyContent:'center', width:18, height:18, borderRadius:'50%', background:V2.accent }}>
            <svg width="10" height="10" viewBox="0 0 16 16" fill="none">
              <path d="M8 1.5 9.2 6 13.5 8 9.2 10 8 14.5 6.8 10 2.5 8 6.8 6 8 1.5Z" fill={V2.surface}/>
            </svg>
          </span>
          <span style={{ fontSize:10.5, color:V2.accent, fontWeight:800, letterSpacing:'0.12em', textTransform:'uppercase' }}>Skipper take</span>
        </div>
        {takeText ? (
          <div style={{ fontSize:13.5, lineHeight:1.55, color:V2.ink, fontWeight:500 }}>{takeText}</div>
        ) : takeError ? (
          <div style={{ fontSize:13, color:V2.muted, lineHeight:1.5, fontWeight:500 }}>Skipper unavailable. Stats are still current.</div>
        ) : (
          <div>
            <div style={{ height:9, width:'95%', background:V2.surface2, borderRadius:8 }}/>
            <div style={{ height:9, width:'86%', background:V2.surface2, borderRadius:8, marginTop:7 }}/>
            <div style={{ height:9, width:'72%', background:V2.surface2, borderRadius:8, marginTop:7 }}/>
          </div>
        )}
      </div>
    </div>
  );
}

function V2ProfileStats({ trend, games, sparkline, isPitcher, season }) {
  const [expanded, setExpanded] = React.useState(false);
  React.useEffect(() => setExpanded(false), [games]);

  const seasonStats = isPitcher ? v2ComputePitchingSeason(games) : v2ComputeHittingSeason(games);
  const l7 = v2AverageFpts(games, 7);
  const l30 = v2AverageFpts(games, 30);
  const headlineThird = isPitcher
    ? { label:'ERA', value: seasonStats.ip ? seasonStats.era.toFixed(2) : '—' }
    : { label:'AVG', value: seasonStats.ab ? seasonStats.avg.toFixed(3).replace(/^0/, '') : '—' };

  const headline = [
    { label:'L7',  value: v2FormatFpts(l7) },
    { label:'L30', value: v2FormatFpts(l30) },
    headlineThird,
  ];

  const moreCells = isPitcher
    ? [
        { label:'IP',   value: seasonStats.ip.toFixed(1) },
        { label:'K',    value: seasonStats.k },
        { label:'WHIP', value: seasonStats.ip ? seasonStats.whip.toFixed(2) : '—' },
        { label:'BB',   value: seasonStats.bb },
        { label:'W',    value: seasonStats.wins },
        { label:'SV',   value: seasonStats.saves },
      ]
    : [
        { label:'H',   value: seasonStats.h },
        { label:'HR',  value: seasonStats.hr },
        { label:'RBI', value: seasonStats.rbi },
        { label:'BB',  value: seasonStats.bb },
        { label:'K',   value: seasonStats.k },
        { label:'SB',  value: seasonStats.sb },
      ];

  const trendDir = trend?.direction || 'flat';
  const trendTone = trendDir === 'up' ? V2.ok : trendDir === 'down' ? V2.bad : V2.muted;
  const trendArrow = trendDir === 'up' ? '↑' : trendDir === 'down' ? '↓' : '→';
  const points = sparkline || [];
  const reversedGames = games.slice().reverse();

  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'18px 18px 4px' }}>
      <div style={{ display:'flex', alignItems:'stretch', paddingBottom:14 }}>
        {headline.map((s, i) => (
          <React.Fragment key={s.label}>
            {i > 0 && <div style={{ width:1, background:V2.hairline2 }}/>}
            <div style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
              <div style={{ fontFamily:V2.fontMono, fontSize:26, fontWeight:700, color:V2.ink, letterSpacing:'-0.02em' }}>{s.value}</div>
              <div style={{ fontSize:10, color:V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase' }}>{s.label}</div>
            </div>
          </React.Fragment>
        ))}
      </div>
      <button
        onClick={() => setExpanded(v => !v)}
        aria-label={expanded ? 'Hide more stats' : 'Show more stats'}
        style={{
          background:'none', border:'none', borderTop:`1px solid ${V2.hairline2}`,
          width:'100%', padding:'14px 0', display:'flex', alignItems:'center', justifyContent:'space-between',
          cursor:'pointer', fontFamily:'inherit', textAlign:'left',
        }}
      >
        <span style={{ fontSize:11, color:V2.ink, fontWeight:800, letterSpacing:'0.12em', textTransform:'uppercase' }}>
          {expanded ? 'Hide stats' : 'More stats'}
        </span>
        <span style={{
          width:26, height:26, borderRadius:999, background:V2.surface2, border:`1px solid ${V2.hairline2}`,
          display:'flex', alignItems:'center', justifyContent:'center',
        }}>
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition:'transform .15s ease' }}>
            <path d="M3 4.5 6 7.5l3-3" stroke={V2.body} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </span>
      </button>
      {expanded && (
        <div style={{ paddingBottom:14, display:'flex', flexDirection:'column', gap:14 }}>
          <div>
            <div style={{ fontSize:10.5, color:V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase', paddingTop:4, paddingBottom:8 }}>
              Season {season ? `· ${season}` : ''} · {isPitcher ? 'Pitching' : 'Hitting'}
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'repeat(3, 1fr)', border:`1px solid ${V2.hairline2}`, borderRadius:12, overflow:'hidden' }}>
              {moreCells.map((s, i) => (
                <div key={s.label} style={{
                  padding:'10px 8px', display:'flex', flexDirection:'column', alignItems:'center', gap:3,
                  borderRight: (i % 3 !== 2) ? `1px solid ${V2.hairline2}` : 'none',
                  borderTop:   (i >= 3) ? `1px solid ${V2.hairline2}` : 'none',
                  background:V2.surface,
                }}>
                  <div style={{ fontFamily:V2.fontMono, fontSize:18, fontWeight:700, color:V2.ink, letterSpacing:'-0.02em' }}>{s.value}</div>
                  <div style={{ fontSize:9.5, color:V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase' }}>{s.label}</div>
                </div>
              ))}
            </div>
          </div>
          {points.length > 0 && (
            <div>
              <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
                <span style={{ fontSize:10.5, color:V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase' }}>Last {points.length} games · FPTS</span>
                <span style={{ color:trendTone, fontSize:16, lineHeight:1, fontWeight:900 }}>{trendArrow}</span>
              </div>
              <div style={{ marginTop:10 }}>
                <V2BarSparkline values={points.map(p => Number(p.fpts) || 0)}/>
              </div>
            </div>
          )}
          {reversedGames.length > 0 && (
            <div style={{ display:'flex', flexDirection:'column' }}>
              <div style={{ fontSize:10.5, color:V2.muted, fontWeight:800, letterSpacing:'0.1em', textTransform:'uppercase', paddingBottom:4 }}>Game log</div>
              {reversedGames.slice(0, 14).map((g, i) => (
                <div key={i} style={{
                  padding:'10px 0', borderTop:`1px solid ${V2.hairline2}`,
                  display:'grid', gridTemplateColumns:'52px 1fr auto', alignItems:'center', gap:10, fontSize:12.5,
                }}>
                  <div style={{ color:V2.muted, fontWeight:700, fontFamily:V2.fontMono }}>{v2ShortDate(g.date)}</div>
                  <div style={{ minWidth:0 }}>
                    <div style={{ fontWeight:700, fontSize:12 }}>{g.home ? 'vs ' : '@ '}{g.opponent || '?'}</div>
                    <div style={{ color:V2.body, marginTop:2, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{g.line || '—'}</div>
                  </div>
                  <div style={{ textAlign:'right', fontFamily:V2.fontMono }}>
                    {!isPitcher && (
                      <div style={{ fontSize:11, color:V2.muted, fontWeight:700 }}>
                        {g.avg_game === null || g.avg_game === undefined ? '—' : g.avg_game.toFixed(3).replace(/^0/, '')}
                      </div>
                    )}
                    <div style={{
                      fontWeight:800, fontSize:13.5,
                      color: (g.fpts_estimated || 0) < 0 ? V2.bad : V2.ink,
                    }}>{(g.fpts_estimated ?? 0).toFixed(1)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function v2AverageFpts(games, count) {
  const vals = (games || [])
    .slice(-count)
    .map(g => Number(g.fpts_estimated))
    .filter(Number.isFinite);
  if (!vals.length) return null;
  return vals.reduce((sum, v) => sum + v, 0) / vals.length;
}

function v2FormatFpts(value) {
  return value === null || value === undefined ? '—' : value.toFixed(1);
}

const V2_PROFILE_PLACEHOLDER_CLIPS = [
  { id:'p1', date:'May 3',  title:'RBI double vs SF',                caption:'See swing path, not just box score.', kind:'video', tone:'orange' },
  { id:'p2', date:'May 1',  title:'Chase strikeout',                 caption:'Useful negative clip for approach.',  kind:'video', tone:'dark'   },
  { id:'p3', date:'Apr 30', title:'Dodgers note · Normal workload',  caption:'Roberts: "He\'s our guy back there."', kind:'note',  tone:'blue'   },
];

function V2ProfileClips({ clips, onOpenClip }) {
  const list = Array.isArray(clips) ? clips : V2_PROFILE_PLACEHOLDER_CLIPS;
  if (!list.length) return null;
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:'16px 4px 4px', display:'flex', flexDirection:'column' }}>
      <div style={{ fontSize:11, color:V2.accent, fontWeight:800, letterSpacing:'0.14em', textTransform:'uppercase', padding:'0 14px 12px' }}>
        MLB clips + news
      </div>
      {list.map((c, i) => <V2ClipRow key={c.id || i} clip={c} onOpen={onOpenClip}/>)}
    </div>
  );
}

function V2BarSparkline({ values, w=320, h=56 }) {
  if (!values || !values.length) return null;
  const max = Math.max(0, ...values);
  const min = Math.min(0, ...values);
  const span = (max - min) || 1;
  const slot = w / values.length;
  const barW = Math.max(4, slot - 4);
  const zeroY = h * (max / span);
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display:'block' }}>
      {values.map((v, i) => {
        const x = i * slot + (slot - barW) / 2;
        const isZero = !v;
        const barH = isZero ? 2 : Math.max(2, Math.abs(v) / span * h);
        const y = v >= 0 ? zeroY - barH : zeroY;
        const fill = isZero ? V2.hairline : v < 0 ? V2.bad : V2.accent;
        return <rect key={i} x={x} y={y} width={barW} height={barH} rx={1.5} fill={fill}/>;
      })}
    </svg>
  );
}

function V2ClipRow({ clip, onOpen }) {
  const isNote = clip.kind === 'note';
  const tone = clip.tone || (isNote ? 'blue' : 'orange');
  const hasThumb = !isNote && !!clip.thumbnail;
  const thumbBg = tone === 'orange'
    ? 'linear-gradient(135deg, #df7042 0%, #a04a23 100%)'
    : tone === 'dark'
      ? 'linear-gradient(135deg, #3a2418 0%, #1a0f08 100%)'
      : 'linear-gradient(135deg, #dbe7fe 0%, #7da0d8 100%)';
  const clickable = !!clip.url && typeof onOpen === 'function';
  const Wrap = clickable ? 'button' : 'div';
  const wrapProps = clickable
    ? { type:'button', onClick:() => onOpen(clip), title:`Open ${clip.title || 'clip'}` }
    : {};
  return (
    <Wrap
      {...wrapProps}
      style={{
        appearance:'none', WebkitAppearance:'none', width:'100%',
        background:'none', border:'none', fontFamily:'inherit', textAlign:'left',
        padding:'10px 14px', borderTop:`1px solid ${V2.hairline2}`,
        display:'flex', alignItems:'center', gap:12,
        textDecoration:'none', color:'inherit', cursor:clickable ? 'pointer' : 'default',
      }}
    >
      <div style={{
        width:54, height:54, borderRadius:12,
        background: hasThumb ? '#1a0f08' : thumbBg,
        display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0,
        position:'relative', overflow:'hidden',
      }}>
        {hasThumb ? (
          <React.Fragment>
            <img
              src={clip.thumbnail}
              alt=""
              loading="lazy"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
              style={{
                position:'absolute', inset:0, width:'100%', height:'100%', objectFit:'cover',
                outline:'1px solid rgba(0,0,0,0.1)', outlineOffset:'-1px',
              }}
            />
            <div style={{
              position:'absolute', inset:0,
              background:'linear-gradient(135deg, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.35) 100%)',
            }}/>
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none" style={{ position:'relative', filter:'drop-shadow(0 1px 2px rgba(0,0,0,0.5))' }}>
              <path d="M5 3.5v9l8-4.5L5 3.5Z" fill={V2.surface}/>
            </svg>
          </React.Fragment>
        ) : isNote ? (
          <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
            <path d="M3 5h10M3 8h10M3 11h7" stroke="#1e3a5f" strokeWidth="1.6" strokeLinecap="round"/>
          </svg>
        ) : (
          <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
            <path d="M5 3.5v9l8-4.5L5 3.5Z" fill={V2.surface}/>
          </svg>
        )}
      </div>
      <div style={{ flex:1, minWidth:0, display:'flex', flexDirection:'column', gap:3 }}>
        <div style={{ fontSize:13.5, color:V2.ink, fontWeight:700, letterSpacing:'-0.005em' }}>{clip.title}</div>
        <div style={{ fontSize:11.5, color:V2.muted, fontWeight:500, lineHeight:1.4 }}>
          {clip.date ? <span style={{ fontFamily:V2.fontMono, fontWeight:700, color:V2.body }}>{v2ShortDate(clip.date)}</span> : null}
          {clip.date && clip.caption && clip.caption !== clip.title ? ' · ' : null}
          {clip.caption && clip.caption !== clip.title ? clip.caption : null}
        </div>
      </div>
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none" style={{ flexShrink:0 }}>
        <path d="M4 3l3 3-3 3" stroke="#94a3b8" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    </Wrap>
  );
}

function V2ClipViewer({ clip, onClose }) {
  const url = clip?.url || '';
  const directVideo = /\.(mp4|mov|m4v)(\?|#|$)/i.test(url);
  const { dialogRef, closeButtonRef } = useV2DialogFocus(onClose);
  return (
    <div
      onClick={(e) => { e.stopPropagation(); onClose(); }}
      style={{ position:'absolute', inset:0, zIndex:30, background:'rgba(15,23,42,0.68)', display:'flex', alignItems:'flex-end' }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={clip?.title ? `Clip: ${clip.title}` : 'MLB clip'}
        onClick={e=>e.stopPropagation()}
        style={{
          width:'100%', height:'74%', background:V2.bg, borderTopLeftRadius:20, borderTopRightRadius:20,
          display:'flex', flexDirection:'column', overflow:'hidden', boxShadow:'0 -18px 50px rgba(15,23,42,0.28)',
        }}
      >
        <div style={{ padding:'12px 14px', display:'flex', alignItems:'center', gap:10, borderBottom:`1px solid ${V2.hairline}`, flexShrink:0 }}>
          <button ref={closeButtonRef} onClick={onClose} aria-label="Close clip" style={{
            width:40, height:40, borderRadius:999, border:`1px solid ${V2.hairline}`,
            background:V2.surface, display:'flex', alignItems:'center', justifyContent:'center',
            cursor:'pointer',
          }}>{Icons.close(V2.muted, 14)}</button>
          <div style={{ minWidth:0, flex:1 }}>
            <div style={{ fontSize:13.5, color:V2.ink, fontWeight:800, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
              {clip?.title || 'MLB clip'}
            </div>
            <div style={{ marginTop:2, fontSize:11.5, color:V2.muted, fontWeight:600, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
              {[clip?.source || 'MLB', clip?.date ? v2ShortDate(clip.date) : null, clip?.duration].filter(Boolean).join(' · ')}
            </div>
          </div>
          {url && (
            <a href={url} target="_blank" rel="noopener noreferrer" style={{
              flexShrink:0, minHeight:40, padding:'8px 10px', borderRadius:999, border:`1px solid ${V2.hairline}`,
              background:V2.surface2, color:V2.body, textDecoration:'none', fontSize:11.5, fontWeight:800,
            }}>Open</a>
          )}
        </div>
        <div style={{ flex:1, background:'#070a12', display:'flex', alignItems:'center', justifyContent:'center' }}>
          {url ? (
            directVideo ? (
              <video src={url} controls autoPlay playsInline style={{ width:'100%', height:'100%', objectFit:'contain', background:'#000' }}/>
            ) : (
              <iframe
                src={url}
                title={clip?.title || 'MLB clip'}
                allow="autoplay; encrypted-media; fullscreen; picture-in-picture"
                allowFullScreen
                style={{ width:'100%', height:'100%', border:0, background:'#000' }}
              />
            )
          ) : (
            <div style={{ padding:24, color:'#fff', textAlign:'center', fontSize:13, lineHeight:1.45 }}>
              This item does not include a playable MLB URL yet.
            </div>
          )}
        </div>
        {clip?.caption && clip.caption !== clip.title && (
          <div style={{ padding:'12px 14px', background:V2.surface, borderTop:`1px solid ${V2.hairline}`, color:V2.body, fontSize:12.5, lineHeight:1.45 }}>
            {clip.caption}
          </div>
        )}
      </div>
    </div>
  );
}

function v2ShortDate(iso) {
  if (!iso) return '—';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}`;
}

function v2ComputeHittingSeason(games) {
  let ab=0, h=0, hr=0, rbi=0, bb=0, k=0, sb=0;
  for (const g of games) {
    ab += g.ab || 0; h += g.h || 0; hr += g.hr || 0; rbi += g.rbi || 0;
    bb += g.bb || 0; k += g.k || 0; sb += g.sb || 0;
  }
  return { ab, h, hr, rbi, bb, k, sb, avg: ab ? h / ab : 0 };
}

function v2ComputePitchingSeason(games) {
  let ip=0, h=0, er=0, bb=0, k=0, wins=0, saves=0;
  for (const g of games) {
    ip += g.ip || 0; h += g.h || 0; er += g.er || 0;
    bb += g.bb || 0; k += g.k || 0;
    if (g.win) wins += 1; if (g.save) saves += 1;
  }
  const era = ip ? (er * 9) / ip : 0;
  const whip = ip ? (bb + h) / ip : 0;
  return { ip, h, er, bb, k, wins, saves, era, whip };
}

// ── Skipper player-link rendering ──────────────────────────────
function V2PlayerLink({ id, name, onOpen }) {
  return (
    <span
      onClick={() => onOpen && onOpen(id)}
      style={{
        cursor:'pointer',
        fontWeight:700,
        color:V2.accent,
        borderBottom:`1px dashed ${V2.accent}`,
        paddingBottom:1,
      }}
    >{name}</span>
  );
}

const V2_TAG_RE = /\[\[([^\]|]+)\|([^\]]+)\]\]/g;
const V2_REGEX_ESCAPE_RE = /[.*+?^${}()|[\]\\]/g;

// Build a regex matching any full-name in the snapshot (case-insensitive,
// longest first). Returns null when the index has no full names yet.
function v2BuildFallbackRegex(index) {
  if (!index || index.size === 0) return null;
  const fullNames = [...index.keys()].filter(n => n && n.includes(' '));
  if (!fullNames.length) return null;
  fullNames.sort((a, b) => b.length - a.length);
  const escaped = fullNames.map(n => n.replace(V2_REGEX_ESCAPE_RE, '\\$&'));
  return new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi');
}

function v2SplitTagsAndText(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(V2_TAG_RE)) {
    if (m.index > last) out.push({ kind:'text', text:text.slice(last, m.index) });
    out.push({ kind:'link', name:m[1].trim(), id:m[2].trim() });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind:'text', text:text.slice(last) });
  return out;
}

function v2ApplyFallback(parts, index, fallbackRe) {
  if (!fallbackRe || !index) return parts;
  const expanded = [];
  for (const part of parts) {
    if (part.kind !== 'text') { expanded.push(part); continue; }
    // matchAll on a global regex resets internal iteration per call, so the
    // memoized `fallbackRe` is safe to share across renders.
    const matches = [...part.text.matchAll(fallbackRe)];
    let lastFb = 0;
    for (const mm of matches) {
      if (mm.index > lastFb) expanded.push({ kind:'text', text:part.text.slice(lastFb, mm.index) });
      const matched = mm[1];
      const id = index.get(matched.toLowerCase());
      if (id) expanded.push({ kind:'link', name:matched, id });
      else expanded.push({ kind:'text', text:matched });
      lastFb = mm.index + mm[0].length;
    }
    if (lastFb < part.text.length) expanded.push({ kind:'text', text:part.text.slice(lastFb) });
  }
  return expanded;
}

function v2RenderSkipperText(text, index, fallbackRe, onOpen) {
  if (!text) return text;
  const parts = v2ApplyFallback(v2SplitTagsAndText(text), index, fallbackRe);
  let key = 0;
  return parts.map(p => p.kind === 'link'
    ? <V2PlayerLink key={key++} id={p.id} name={p.name} onOpen={onOpen}/>
    : <React.Fragment key={key++}>{p.text}</React.Fragment>);
}

// Render Skipper output as React: paragraphs, bullet lists, and inline
// **bold** — with player tags / fuzzy player-name matches still becoming
// V2PlayerLink. Skipper's prompt allows markdown but the model often glues
// list items onto one line with " - **Label** —" separators; we normalize
// that into real bullets before splitting into blocks.
function v2RenderSkipperMarkdown(text, index, fallbackRe, onOpen) {
  if (!text) return text;
  const normalized = String(text).replace(/(^|[^\n])\s+-\s+(?=\*\*)/g, '$1\n- ');
  const lines = normalized.split('\n');

  const blocks = [];
  let para = [];
  let list = null;
  const flushPara = () => {
    if (para.length) { blocks.push({ type:'p', text:para.join(' ') }); para = []; }
  };
  const flushList = () => {
    if (list) { blocks.push({ type:'ul', items:list }); list = null; }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    const li = line.match(/^[-*]\s+(.*)$/);
    if (li) {
      flushPara();
      if (!list) list = [];
      list.push(li[1]);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();

  if (!blocks.length) return null;

  const renderInline = (s) => {
    const out = [];
    const re = /\*\*([^*\n]+)\*\*/g;
    let last = 0;
    let key = 0;
    let m;
    while ((m = re.exec(s)) !== null) {
      if (m.index > last) {
        out.push(<React.Fragment key={key++}>{v2RenderSkipperText(s.slice(last, m.index), index, fallbackRe, onOpen)}</React.Fragment>);
      }
      out.push(<strong key={key++} style={{ fontWeight:700 }}>{v2RenderSkipperText(m[1], index, fallbackRe, onOpen)}</strong>);
      last = m.index + m[0].length;
    }
    if (last < s.length) {
      out.push(<React.Fragment key={key++}>{v2RenderSkipperText(s.slice(last), index, fallbackRe, onOpen)}</React.Fragment>);
    }
    return out;
  };

  let bk = 0;
  return blocks.map(b => {
    if (b.type === 'ul') {
      return (
        <ul key={bk++} style={{ margin:'4px 0', paddingLeft:18 }}>
          {b.items.map((item, i) => (
            <li key={i} style={{ margin:'3px 0' }}>{renderInline(item)}</li>
          ))}
        </ul>
      );
    }
    return (
      <p key={bk++} style={{ margin:'4px 0' }}>{renderInline(b.text)}</p>
    );
  });
}

function v2IsBrokenSkipperReply(text) {
  const normalized = String(text || '').trim().toLowerCase().replace(/[.]/g, ' ').replace(/\s+/g, ' ');
  return ['data', 'data unavailable', 'unavailable', 'no data'].includes(normalized);
}

const V2_DEEP_MATCHUP_RE = /\b(deep matchup|matchup analysis|matchup deep|thorough matchup)\b/i;
function v2IsDeepMatchupPrompt(text) {
  return V2_DEEP_MATCHUP_RE.test(String(text || ''));
}

function v2ProjectionPressure(projection) {
  const drivers = projection?.drivers || {};
  const restDelta = v2Number(drivers.rest_of_period_delta);
  const gameEdge = v2Number(drivers.game_volume_edge);
  const currentMargin = v2Number(drivers.current_margin);
  if (Math.abs(restDelta) >= 5) {
    return restDelta > 0 ? `Late swing toward you by ${Math.abs(restDelta).toFixed(1)}` : `Late swing against you by ${Math.abs(restDelta).toFixed(1)}`;
  }
  if (Math.abs(gameEdge) >= 2) {
    return gameEdge > 0 ? `Schedule edge +${Math.abs(gameEdge).toFixed(0)} games` : `Opponent schedule +${Math.abs(gameEdge).toFixed(0)} games`;
  }
  if (Math.abs(currentMargin) > 0) {
    return currentMargin > 0 ? `Current lead ${currentMargin.toFixed(1)}` : `Current gap ${Math.abs(currentMargin).toFixed(1)}`;
  }
  return 'No single driver dominates';
}

function v2ProjectionScheduleText(projection) {
  const edge = v2Number(projection?.drivers?.game_volume_edge);
  if (edge > 0) return `You +${edge.toFixed(0)} games`;
  if (edge < 0) return `Opponent +${Math.abs(edge).toFixed(0)} games`;
  return 'Even volume';
}

function v2ProjectionRiskText(projection) {
  if (projection?.probability_calibrated === false) return 'Probability uncalibrated';
  if (projection?.win_probability === null || projection?.win_probability === undefined) return 'Probability unavailable';
  const risk = String(projection?.drivers?.risk_level || '').toLowerCase();
  if (risk === 'high') return 'High swing risk';
  if (risk === 'medium') return 'Medium swing risk';
  if (risk === 'low') return 'Low swing risk';
  return 'Risk unknown';
}

function V2MatchupProjectionCard({ matchup, dataQuality }) {
  const info = v2MatchupInfo(matchup);
  if (!info) return null;
  const projection = info.projection || null;
  const projectionInfo = v2ProjectionInfo(projection);
  const incomplete = dataQuality?.projection_ready === false;
  const cardStyle = {
    marginBottom:9,
    padding:'10px 11px',
    background:V2.surface2,
    border:`1px solid ${V2.hairline}`,
    borderRadius:12,
    display:'flex',
    flexDirection:'column',
    gap:8,
  };
  const metricStyle = {
    minWidth:0,
    padding:'7px 8px',
    border:`1px solid ${V2.hairline}`,
    borderRadius:10,
    background:V2.surface,
  };
  if (incomplete) {
    return (
      <div style={cardStyle}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
          <V2Eyebrow color={V2.warn}>Data incomplete</V2Eyebrow>
          <span style={{ color:info.margin >= 0 ? V2.accent : V2.bad, fontWeight:900, fontFamily:V2.fontMono, fontSize:12 }}>
            {info.margin >= 0 ? '+' : ''}{info.margin.toFixed(1)}
          </span>
        </div>
        <div style={{ color:V2.ink, fontSize:14, fontWeight:800, fontFamily:V2.fontDisplay }}>
          Score-based view only
        </div>
        <div style={{ color:V2.muted, fontSize:12, lineHeight:1.35 }}>
          {v2QualityReason(dataQuality, 'projection')}
        </div>
      </div>
    );
  }
  if (!projectionInfo) {
    return (
      <div style={cardStyle}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
          <V2Eyebrow color={info.leading ? V2.accent : V2.bad}>{info.leading ? 'Leading' : info.margin < 0 ? 'Trailing' : 'Tied'}</V2Eyebrow>
          <span style={{ color:info.margin >= 0 ? V2.accent : V2.bad, fontWeight:900, fontFamily:V2.fontMono, fontSize:12 }}>
            {info.margin >= 0 ? '+' : ''}{info.margin.toFixed(1)}
          </span>
        </div>
        <div style={{ display:'flex', justifyContent:'space-between', gap:12, color:V2.ink, fontWeight:800, fontFamily:V2.fontMono, fontSize:14 }}>
          <span>You {info.my.toFixed(1)}</span>
          <span style={{ color:V2.muted }}>{info.opp.toFixed(1)} {info.opponent}</span>
        </div>
        <div style={{ color:V2.muted, fontSize:12, lineHeight:1.35 }}>
          Projection is unavailable, so Skipper is using the current score only.
        </div>
      </div>
    );
  }
  return (
    <div style={cardStyle}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
        <V2Eyebrow color={projectionInfo.color}>{projectionInfo.band}</V2Eyebrow>
        <span style={{ color:V2.muted, fontSize:11, fontWeight:800, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          vs {info.opponent}
        </span>
      </div>
      <div style={{ display:'flex', justifyContent:'space-between', gap:12, alignItems:'baseline' }}>
        <div style={{ minWidth:0 }}>
          <div style={{ color:V2.ink, fontSize:15, fontWeight:900, fontFamily:V2.fontMono, whiteSpace:'nowrap' }}>
            {projectionInfo.projectedMy} - {projectionInfo.projectedOpp}
          </div>
          <div style={{ marginTop:3, color:V2.muted, fontSize:11.5, fontWeight:800 }}>projected final</div>
        </div>
        <div style={{ color:projectionInfo.color, fontSize:18, lineHeight:1, fontWeight:900, fontFamily:V2.fontDisplay, textAlign:'right' }}>
          {projectionInfo.projectedMargin >= 0 ? '+' : ''}{projectionInfo.projectedMargin.toFixed(1)}
        </div>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:7 }}>
        <div style={metricStyle}>
          <div style={{ color:V2.muted, fontSize:10.5, fontWeight:800, textTransform:'uppercase' }}>Schedule</div>
          <div style={{ marginTop:3, color:V2.ink, fontSize:12, fontWeight:800 }}>{v2ProjectionScheduleText(projection)}</div>
        </div>
        <div style={metricStyle}>
          <div style={{ color:V2.muted, fontSize:10.5, fontWeight:800, textTransform:'uppercase' }}>Risk</div>
          <div style={{ marginTop:3, color:V2.ink, fontSize:12, fontWeight:800 }}>{v2ProjectionRiskText(projection)}</div>
        </div>
      </div>
      <div style={{ color:V2.body, fontSize:12.2, lineHeight:1.35, fontWeight:700 }}>
        {v2ProjectionPressure(projection)}
      </div>
    </div>
  );
}

// ── /skipper ───────────────────────────────────────────────────
function V2Skipper({ model, sync, onOpenPlayer, draft, onDraftConsumed }) {
  const prompts = ['Weekly matchup assessment', 'Deep matchup analysis', 'Best waiver swap to review?', 'Where am I weakest?', 'Who is my best 2B?'];
  const [msgs, setMsgs] = React.useState([]);
  const [input, setInput] = React.useState('');
  const [streaming, setStreaming] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [brief, setBrief] = React.useState({ status:'loading', data:null, error:null });
  const [modelOptions, setModelOptions] = React.useState({
    defaultModel: V2_SKIPPER_DEFAULT_MODEL,
    models: V2_SKIPPER_MODELS,
    webSearch: { defaultEnabled:true, available:true },
  });
  const [chatModel, setChatModel] = React.useState(V2_SKIPPER_DEFAULT_MODEL);
  const [reasoning, setReasoning] = React.useState(false);
  const [webFallback, setWebFallback] = React.useState(true);
  const [optionsReady, setOptionsReady] = React.useState(false);
  const [historyReady, setHistoryReady] = React.useState(false);
  const [pendingAutoDraft, setPendingAutoDraft] = React.useState(null);
  const [researchingTrade, setResearchingTrade] = React.useState(false);
  const scrollRef = React.useRef(null);

  const playerNameIndex = React.useMemo(
    () => buildPlayerNameIndex(model?.playerIndex || []),
    [model?.playerIndex],
  );
  const fallbackRe = React.useMemo(
    () => v2BuildFallbackRegex(playerNameIndex),
    [playerNameIndex],
  );
  const renderText = React.useCallback(
    (text) => v2RenderSkipperMarkdown(text, playerNameIndex, fallbackRe, onOpenPlayer),
    [playerNameIndex, fallbackRe, onOpenPlayer],
  );
  const activeModel = modelOptions.models.find(m => m.id === chatModel) || modelOptions.models[0] || V2_SKIPPER_MODELS[0];
  const webSearchAvailable = modelOptions.webSearch?.available !== false;
  const webSearchEnabled = webSearchAvailable && webFallback;

  const updateChatModel = React.useCallback((next) => {
    setChatModel(next);
  }, []);

  const updateReasoning = React.useCallback((next) => {
    setReasoning(next);
  }, []);

  React.useEffect(() => {
    let cancelled = false;
    fetch('/api/skipper/options')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`options ${r.status}`)))
      .then(data => {
        if (cancelled) return;
        const options = v2NormalizeSkipperOptions(data);
        setModelOptions(options);
        setChatModel(current => options.models.some(m => m.id === current) ? current : options.defaultModel);
        setWebFallback(options.webSearch.defaultEnabled && options.webSearch.available);
        setOptionsReady(true);
      })
      .catch(() => { if (!cancelled) setOptionsReady(true); });
    return () => { cancelled = true; };
  }, []);

  React.useEffect(() => {
    if (!draft?.text) return;
    if (draft.autoSend) setPendingAutoDraft(draft);
    else setInput(draft.text);
    onDraftConsumed?.();
  }, [draft?.id]);

  React.useEffect(() => {
    let cancelled = false;
    setBrief({ status:'loading', data:null, error:null });
    fetch('/api/waiver-swaps/latest')
      .then(async r => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || `brief ${r.status}`);
        return data;
      })
      .then(data => {
        if (cancelled) return;
        setBrief({ status:data?.brief?.state === 'ready' ? 'ready' : 'missing', data:data?.brief || null, error:null });
      })
      .catch(err => { if (!cancelled) setBrief({ status:'error', data:null, error:err.message }); });
    return () => { cancelled = true; };
  }, [model?.snapshotId]);

  // Load history on mount
  React.useEffect(() => {
    let cancelled = false;
    fetch('/api/skipper/messages')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`history ${r.status}`)))
      .then(data => {
        if (cancelled) return;
        const loaded = (data.messages || [])
          .filter(m => !(m.role === 'assistant' && v2IsBrokenSkipperReply(m.content)))
          .map(m => {
            const sources = Array.isArray(m.metadata?.sources) ? m.metadata.sources : [];
            const sourcesAvailable = m.metadata?.sources_available === true || sources.length > 0;
            return {
              role:m.role === 'assistant' ? 'ai' : m.role,
              text:m.content,
              sources,
              webSearchUnverified:m.metadata?.web_search_requested === true && !sourcesAvailable,
            };
          });
        setMsgs(loaded);
        setHistoryReady(true);
      })
      .catch(e => {
        if (!cancelled) {
          setError(`Couldn't load history: ${e.message}`);
          setHistoryReady(true);
        }
      });
    return () => { cancelled = true; };
  }, []);

  // Auto-scroll to bottom when messages or streaming text changes
  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [msgs]);

  const send = async (text, overrides={}) => {
    const t = (text ?? input).trim();
    if (!t || streaming) return;
    const isTradeResearch = t.startsWith('Sandlot trade-analysis evidence:');
    const useReasoning = overrides.reasoning === undefined ? reasoning : overrides.reasoning === true;
    const reasoningEffort = useReasoning ? (overrides.reasoningEffort || 'medium') : null;
    const useWebSearch = overrides.webSearch === undefined ? webSearchEnabled : overrides.webSearch === true;
    setError(null);
    setInput('');
    setResearchingTrade(isTradeResearch);
    // Tag the upcoming AI bubble with chart:'matchup' when the prompt asks for
    // a deep matchup read so V2Bubble can render the projection card with it.
    const aiSeed = v2IsDeepMatchupPrompt(t) ? { role:'ai', text:'', chart:'matchup' } : { role:'ai', text:'' };
    setMsgs(m => [...m, { role:'user', text:t }, aiSeed]);
    setStreaming(true);

    try {
      const resp = await fetch('/api/skipper/messages', {
        method: 'POST',
        headers: { 'Content-Type':'application/json' },
        body: JSON.stringify({
          content: t,
          model: chatModel,
          reasoning:useReasoning,
          reasoning_effort:reasoningEffort,
          web_search:useWebSearch,
        }),
      });
      if (!resp.ok || !resp.body) {
        const detail = await resp.text().catch(() => '');
        throw new Error(`stream ${resp.status} ${detail.slice(0,200)}`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let done = false;
      while (!done) {
        const { value, done: streamDone } = await reader.read();
        done = streamDone;
        if (value) buffer += decoder.decode(value, { stream: true });
        // SSE frames are split by \n\n
        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!frame.startsWith('data:')) continue;
          const json = frame.slice(5).trim();
          if (!json) continue;
          let evt;
          try { evt = JSON.parse(json); } catch { continue; }
          if (evt.type === 'token' && evt.text) {
            setMsgs(m => {
              const next = m.slice();
              const last = next[next.length - 1];
              next[next.length - 1] = { ...last, text: (last.text || '') + evt.text };
              return next;
            });
          } else if (evt.type === 'replace' && evt.text) {
            // Backend repaired a broken reply — swap the streamed text wholesale.
            setMsgs(m => {
              const next = m.slice();
              const last = next[next.length - 1];
              next[next.length - 1] = { ...last, text: evt.text };
              return next;
            });
          } else if (evt.type === 'sources' && Array.isArray(evt.sources)) {
            setMsgs(m => {
              if (!m.length) return m;
              const next = m.slice();
              const last = next[next.length - 1] || {};
              const seen = new Set((last.sources || []).map(s => s.url).filter(Boolean));
              const sources = [...(last.sources || [])];
              for (const source of evt.sources) {
                if (!source?.url || seen.has(source.url)) continue;
                seen.add(source.url);
                sources.push(source);
              }
              next[next.length - 1] = { ...last, sources };
              return next;
            });
          } else if (evt.type === 'done' && evt.web_search_requested === true && evt.sources_available !== true) {
            setMsgs(m => {
              if (!m.length) return m;
              const next = m.slice();
              const last = next[next.length - 1] || {};
              next[next.length - 1] = { ...last, webSearchUnverified:true };
              return next;
            });
          } else if (evt.type === 'error') {
            setError(evt.message || 'Skipper failed');
            // Drop the AI bubble if the stream errored before any tokens
            setMsgs(m => {
              if (m.length && m[m.length-1].role === 'ai' && !m[m.length-1].text) {
                return m.slice(0, -1);
              }
              return m;
            });
          }
        }
      }
    } catch (e) {
      setError(e.message);
      // Drop the empty AI bubble if we never got tokens
      setMsgs(m => {
        if (m.length && m[m.length-1].role === 'ai' && !m[m.length-1].text) {
          return m.slice(0, -1);
        }
        return m;
      });
    } finally {
      setStreaming(false);
      setResearchingTrade(false);
    }
  };

  React.useEffect(() => {
    if (!pendingAutoDraft?.text || !optionsReady || !historyReady) return;
    const queued = pendingAutoDraft;
    setPendingAutoDraft(null);
    setReasoning(queued.reasoning === true);
    setWebFallback(queued.webSearch === true);
    send(queued.text, {
      reasoning:queued.reasoning === true,
      reasoningEffort:queued.reasoningEffort || 'high',
      webSearch:queued.webSearch === true,
    });
  }, [pendingAutoDraft?.id, optionsReady, historyReady]);

  const clear = async () => {
    if (streaming) return;
    try {
      const r = await fetch('/api/skipper/messages', { method: 'DELETE' });
      if (!r.ok) throw new Error(`clear ${r.status}`);
      setMsgs([]);
      setError(null);
    } catch (e) {
      setError(`Couldn't clear history: ${e.message}`);
    }
  };

  return (
    <div style={{ height:'100%', display:'flex', flexDirection:'column' }}>
      <div ref={scrollRef} style={{ flex:1, overflow:'auto', padding:'4px 16px 18px' }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, margin:'4px 0 10px' }}>
          <div style={{ width:28, height:28, borderRadius:'50%', background:V2.warn, color:'#fff', display:'flex', alignItems:'center', justifyContent:'center' }}>{Icons.sparkle('#fff', 14)}</div>
          <V2Eyebrow color={V2.muted}>Skipper</V2Eyebrow>
          {msgs.length > 0 && (
            <button onClick={clear} disabled={streaming} title="Clear chat history" style={{
              marginLeft:'auto', background:'none', border:`1px solid ${V2.hairline}`,
              color:V2.muted, fontSize:11, fontWeight:700, letterSpacing:'0.04em',
              textTransform:'uppercase', padding:'5px 10px', borderRadius:999,
              cursor: streaming ? 'not-allowed' : 'pointer', opacity: streaming ? 0.5 : 1,
              fontFamily:'inherit',
            }}>Clear</button>
          )}
        </div>
        <div style={{ display:'flex', alignItems:'center', flexWrap:'wrap', gap:8, margin:'0 0 12px', paddingBottom:1 }}>
          <label style={{
            display:'flex', alignItems:'center', gap:7, flexShrink:0,
            background:V2.surface, border:`1px solid ${V2.hairline}`,
            borderRadius:999, padding:'6px 10px', color:V2.body, maxWidth:'100%',
          }}>
            <span style={{ fontSize:10.5, fontWeight:800, color:V2.muted, letterSpacing:'0.06em', textTransform:'uppercase' }}>Model</span>
            <select value={chatModel} onChange={e=>updateChatModel(e.target.value)} disabled={streaming} style={{
              border:'none', background:'transparent', color:V2.ink, outline:'none',
              fontFamily:'inherit', fontSize:12, fontWeight:800, minWidth:168, maxWidth:230,
              opacity: streaming ? 0.65 : 1,
            }}>
              {modelOptions.models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <button type="button" aria-pressed={reasoning} onClick={()=>updateReasoning(!reasoning)} disabled={streaming} title="Use OpenRouter reasoning for this model" style={{
            flexShrink:0, display:'flex', alignItems:'center', gap:7,
            border:`1px solid ${reasoning ? V2.accent : V2.hairline}`,
            background:reasoning ? V2.accentSoft : V2.surface,
            color:reasoning ? V2.accent : V2.body,
            borderRadius:999, minHeight:40, padding:'8px 12px', cursor:streaming ? 'not-allowed' : 'pointer',
            opacity:streaming ? 0.65 : 1, fontFamily:'inherit',
          }}>
            <span style={{ width:7, height:7, borderRadius:'50%', background:reasoning ? V2.accent : V2.muted }}/>
            <span style={{ fontSize:11.5, fontWeight:800 }}>Reasoning {reasoning ? 'on' : 'off'}</span>
          </button>
          {webSearchAvailable && <button type="button" aria-pressed={webSearchEnabled} onClick={()=>setWebFallback(v=>!v)} disabled={streaming} title="Allow Skipper to search public web sources when snapshot data is missing" style={{
            flexShrink:0, display:'flex', alignItems:'center', gap:7,
            border:`1px solid ${webSearchEnabled ? V2.accent : V2.hairline}`,
            background:webSearchEnabled ? V2.accentSoft : V2.surface,
            color:webSearchEnabled ? V2.accent : V2.body,
            borderRadius:999, minHeight:40, padding:'8px 12px', cursor:streaming ? 'not-allowed' : 'pointer',
            opacity:streaming ? 0.65 : 1, fontFamily:'inherit',
          }}>
            <span style={{ width:7, height:7, borderRadius:'50%', background:webSearchEnabled ? V2.accent : V2.muted }}/>
            <span style={{ fontSize:11.5, fontWeight:800 }}>Web fallback {webSearchEnabled ? 'on' : 'off'}</span>
          </button>}
          <div style={{ flexShrink:0, color:V2.muted, fontSize:10.5, fontWeight:700 }}>
            {activeModel.label}
          </div>
        </div>
        <V2SkipperRefreshBrief brief={brief} sync={sync}/>
        {msgs.length === 0 && !streaming && (
          <div style={{ color:V2.muted, fontSize:13, padding:'18px 4px', lineHeight:1.5 }}>
            Ask anything about your roster. Skipper reads the latest snapshot and answers from real data only.
          </div>
        )}
        {msgs.map((m,i)=> <V2Bubble key={i} m={m} renderText={renderText} matchup={model?.matchup} dataQuality={model?.dataQuality}/>)}
        {streaming && msgs.length > 0 && msgs[msgs.length-1].role === 'ai' && !msgs[msgs.length-1].text && (
          <div style={{ color:V2.muted, fontSize:12, padding:'2px 4px 8px' }}>
            {researchingTrade ? 'Researching cited evidence and applying Sandlot guardrails…' : 'Thinking…'}
          </div>
        )}
        {error && (
          <div style={{ color:V2.bad || '#c33', fontSize:12.5, padding:'8px 4px' }}>{error}</div>
        )}
      </div>
      <div style={{ borderTop:`1px solid ${V2.hairline}`, padding:'10px 14px 16px', background:V2.surface }}>
        <div style={{ display:'flex', gap:7, overflowX:'auto', paddingBottom:10 }}>
          {prompts.map(p => (
            <button key={p} onClick={()=>send(p)} disabled={streaming} style={{
              flexShrink:0, padding:'8px 12px', borderRadius:999, border:`1px solid ${V2.hairline}`,
              background:V2.surface2, color:V2.body, fontSize:11.5,
              cursor: streaming ? 'not-allowed' : 'pointer',
              opacity: streaming ? 0.5 : 1,
              fontFamily:'inherit', fontWeight:700,
            }}>{p}</button>
          ))}
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <input value={input} onChange={e=>setInput(e.target.value)} disabled={streaming}
            onKeyDown={e=>{if(e.key==='Enter')send();}}
            placeholder={streaming ? 'Skipper is responding…' : 'Ask about your roster, waivers, matchups...'}
            style={{ flex:1, border:`1px solid ${V2.hairline}`, background:V2.surface2, borderRadius:999, padding:'12px 15px', outline:'none', fontSize:13.5, color:V2.ink, fontFamily:'inherit' }}/>
          <button onClick={()=>send()} disabled={streaming} aria-label="Send message" style={{
            width:42, height:42, borderRadius:'50%', background:V2.warn, border:'none',
            cursor: streaming ? 'not-allowed' : 'pointer',
            opacity: streaming ? 0.6 : 1,
            display:'flex', alignItems:'center', justifyContent:'center',
          }}>{Icons.send('#fff', 14)}</button>
        </div>
      </div>
    </div>
  );
}

function V2SkipperRefreshBrief({ brief, sync }) {
  const lines = v2BriefLines(brief?.data?.text);
  const ready = brief?.status === 'ready' && lines.length;
  const loading = brief?.status === 'loading';
  const error = brief?.status === 'error';
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:15, marginBottom:14 }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:10 }}>
        <V2Eyebrow color={ready ? V2.accent : V2.muted}>Refresh brief</V2Eyebrow>
        <div style={{ fontSize:10.5, color:V2.muted, fontWeight:800, letterSpacing:'0.04em', textTransform:'uppercase' }}>
          {sync?.label || 'snapshot'}
        </div>
      </div>
      {ready ? (
        <div style={{ marginTop:10, display:'flex', flexDirection:'column', gap:8 }}>
          {lines.slice(0,5).map((line,i)=>(
            <div key={i} style={{ display:'flex', gap:9, alignItems:'flex-start', fontSize:13, color:V2.body, lineHeight:1.45 }}>
              <span style={{ color:V2.accent, fontWeight:800, marginTop:1 }}>•</span>
              <span>{line}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ marginTop:8, fontSize:12.5, color:error ? V2.warn : V2.muted, lineHeight:1.5 }}>
          {loading
            ? 'Checking for a cached refresh brief...'
            : error
              ? `Brief unavailable: ${brief.error}`
              : 'Brief will appear after the next refresh.'}
        </div>
      )}
    </div>
  );
}

// ── Skipper chat (inlined V2Bubble) ────────────────────────────
function V2Bubble({ m, renderText, matchup, dataQuality }) {
  if (m.role==='user') return (
    <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:8 }}>
      <div style={{ background:V2.accent, color:'#fff', padding:'9px 13px', borderRadius:'14px 14px 4px 14px', fontSize:13.5, maxWidth:'82%', lineHeight:1.4 }}>{m.text}</div>
    </div>
  );
  if (v2IsBrokenSkipperReply(m.text)) return null;
  const body = renderText ? renderText(m.text) : m.text;
  const showProjectionCard = m.chart === 'matchup' && matchup && (matchup.my_score != null || matchup.opponent_score != null);
  return (
    <div style={{ display:'flex', marginBottom:10 }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, color:V2.ink, padding:'10px 13px', borderRadius:'14px 14px 14px 4px', fontSize:13.5, maxWidth:'92%', lineHeight:1.5 }}>
        {showProjectionCard && <V2MatchupProjectionCard matchup={matchup} dataQuality={dataQuality}/>}
        {body}
        {m.webSearchUnverified ? (
          <div role="status" style={{ marginTop:9, color:V2.warn, fontSize:11.5, lineHeight:1.4, fontWeight:750 }}>
            Web verification was requested but unavailable. Treat this answer as unverified.
          </div>
        ) : null}
        <V2WebSources sources={m.sources}/>
      </div>
    </div>
  );
}

function V2WebSources({ sources }) {
  const list = (sources || []).filter(s => s?.url).slice(0, 4);
  if (!list.length) return null;
  return (
    <div style={{ marginTop:10, paddingTop:9, borderTop:`1px solid ${V2.hairline2}` }}>
      <div style={{ fontSize:10, color:V2.muted, fontWeight:900, letterSpacing:'0.08em', textTransform:'uppercase' }}>Web sources</div>
      <div style={{ marginTop:6, display:'flex', flexDirection:'column', gap:5 }}>
        {list.map((source, index) => (
          <a key={`${source.url}-${index}`} href={source.url} target="_blank" rel="noreferrer" style={{
            display:'block', color:V2.accent, fontSize:12, lineHeight:1.3,
            fontWeight:800, textDecoration:'none', overflow:'hidden', textOverflow:'ellipsis',
            whiteSpace:'nowrap',
          }}>
            {source.title || source.url}
          </a>
        ))}
      </div>
    </div>
  );
}

export { V2App };
