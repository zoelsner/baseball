// v2 — Card direction merged with Soft Cream tokens.
// Cream surface, generous padding, hairline-thin internal dividers,
// segmented pill controls, soft-tinted selection states, large rounded primaries.

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
  { id:'moonshotai/kimi-k2', label:'Kimi K2', short:'Kimi' },
  { id:'tencent/hy3-preview:free', label:'Tencent HY3 free', short:'Tencent' },
  { id:'deepseek/deepseek-v4-flash', label:'DeepSeek V4 Flash', short:'DS Flash' },
  { id:'deepseek/deepseek-v4-pro', label:'DeepSeek V4 Pro', short:'DS Pro' },
];
const V2_SKIPPER_DEFAULT_MODEL = 'moonshotai/kimi-k2';

function v2StoredValue(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value || fallback;
  } catch {
    return fallback;
  }
}

function v2StoreValue(key, value) {
  try { window.localStorage.setItem(key, value); } catch {}
}

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
  if (slot === 'BN') return 'bench';
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
  };
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

function V2Primary({ children, onClick, sub, variant='dark' }) {
  const bg = variant==='accent' ? V2.accent : V2.ink;
  return (
    <div>
      <button onClick={onClick} style={{
        width:'100%', padding:'15px 18px', borderRadius:999, border:'none',
        background:bg, color:'#fff', fontSize:15, fontWeight:700,
        cursor:'pointer', fontFamily:'inherit',
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
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail?.errors) && detail.errors.length) return detail.errors.join('; ');
  if (detail?.error) return detail.error;
  if (detail?.status) return `Refresh ${detail.status}`;
  return `Refresh failed (${status})`;
}

async function v2FetchRefresh() {
  const res = await fetch('/api/refresh', { method:'POST' });
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

  // Reset the in-page team-roster overlay whenever we leave the league tab.
  const setPage = React.useCallback((next) => {
    if (next !== 'league') setLeagueTeam(null);
    setPageRaw(next);
  }, []);

  const loadSnapshot = React.useCallback(async () => {
    if (window.location.protocol === 'file:') {
      setSyncState({ state:'failed', label:'no data', error:'Open over http(s) to load real data', notice:null });
      return;
    }
    setSyncState({ state:'loading', label:'loading', error:null, notice:null });

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

    // An empty roster from a "successful" snapshot is just as broken as a failed pull.
    const firstPullEmpty = snapshot && snapshot.roster.length === 0;
    if (snapshot && !firstPullEmpty) {
      setModel(snapshot);
      setSyncState({ ...snapshot.sync });
      return;
    }

    // Step 2: auto-trigger a fresh refresh and surface the first-pull failure.
    const reason = firstPullError || 'first snapshot was empty';
    setSyncState({ state:'refreshing', label:'retrying', error:null, notice:`First pull failed (${reason}); refreshing…` });
    try {
      const next = await v2FetchRefresh();
      setModel(next);
      setSyncState({ ...next.sync, notice:`First pull failed (${reason}); auto-refreshed.` });
    } catch (refreshErr) {
      const fallback = refreshErr.fallbackSnapshot || snapshot;
      if (fallback) setModel(fallback);
      setSyncState({
        state: 'failed',
        label: fallback?.sync?.label || 'failed',
        error: `First pull failed (${reason}); refresh also failed: ${refreshErr.message}`,
        notice: fallback ? (refreshErr.fallbackReason || 'Showing the last successful Fantrax pull.') : null,
      });
    }
  }, []);

  const refreshSnapshot = React.useCallback(async () => {
    if (window.location.protocol === 'file:') return;
    setSyncState(s => ({ ...s, state:'refreshing', label:'syncing', error:null, notice:null }));
    try {
      const next = await v2FetchRefresh();
      setModel(next);
      setSyncState({ ...next.sync });
    } catch (err) {
      if (err.fallbackSnapshot) setModel(err.fallbackSnapshot);
      setSyncState({
        state:'failed',
        label:err.fallbackSnapshot?.sync?.label || 'failed',
        error:err.message,
        notice:err.fallbackSnapshot ? (err.fallbackReason || 'Showing the last successful Fantrax pull.') : null,
      });
    }
  }, []);

  React.useEffect(() => { loadSnapshot(); }, [loadSnapshot]);

  const openPlayer = React.useCallback((id) => {
    if (!id) return;
    setPage('roster');
    setDetail(id);
  }, []);

  if (!authed) return <V2Auth onSignIn={()=>setAuthed(true)}/>;

  const pages = {
    today:   <V2Today model={model} sync={syncState} onRefresh={refreshSnapshot} onNav={setPage}/>,
    roster:  <V2Roster model={model} onPlayer={setDetail}/>,
    league:  leagueTeam
      ? <V2TeamRoster teamId={leagueTeam.id} teamMeta={leagueTeam} onBack={()=>setLeagueTeam(null)} onPlayer={setDetail}/>
      : <V2League model={model} onOpenTeam={setLeagueTeam}/>,
    fa:      <V2FreeAgents onOpenPlayer={openPlayer}/>,
    trade:   <V2TradeGrader/>,
    skipper: <V2Skipper model={model} sync={syncState} onOpenPlayer={openPlayer}/>,
    settings:<V2Settings model={model} sync={syncState} onRefresh={refreshSnapshot} onSignOut={()=>setAuthed(false)}/>,
  };

  return (
    <div style={{
      width:'100%', height:'100%', background:V2.bg, color:V2.ink, fontFamily:V2.font,
      display:'flex', flexDirection:'column', position:'relative',
    }}>
      <V2TopBar page={page} setPage={setPage} model={model} sync={syncState} onRefresh={refreshSnapshot}/>
      <div style={{ flex:1, overflow:'auto', WebkitOverflowScrolling:'touch' }}>{pages[page]}</div>
      <V2TabBar page={page} setPage={setPage}/>
      {detail && <V2PlayerSheet id={detail} onClose={()=>setDetail(null)}/>}
    </div>
  );
}

function V2TopBar({ page, setPage, model, sync, onRefresh }) {
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
  const syncColor = sync.state === 'failed' ? V2.bad : (sync.state === 'refreshing' || sync.state === 'loading') ? V2.warn : sync.notice ? V2.warn : V2.ok;
  return (
    <div style={{ padding: isHero?'18px 20px 16px':'16px 18px 12px', background:V2.bg }}>
      <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:10 }}>
        <div style={{ minWidth:0, flex:1 }}>
          <div style={{ fontSize:11.5, color:V2.warn, fontWeight:700, letterSpacing:'0.08em', textTransform:'uppercase' }}>
            {eyebrow}
          </div>
          <div style={{
            fontSize: isHero?28:22, fontWeight:600, letterSpacing:'-0.02em', marginTop:4,
            fontFamily:V2.fontDisplay, lineHeight:1.1,
          }}>{titles[page]}</div>
        </div>
        {page!=='settings' && (
          <button onClick={onRefresh} disabled={sync.state === 'refreshing' || window.location.protocol === 'file:'} title={sync.error || 'Refresh Fantrax data'} style={{
            background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:999,
            padding:'7px 11px', display:'flex', alignItems:'center', gap:7, cursor:'pointer',
            fontFamily:'inherit', flexShrink:0, marginTop:2,
            opacity: sync.state === 'refreshing' ? 0.7 : 1,
          }}>
            <div style={{ width:6, height:6, background:syncColor, borderRadius:'50%' }}/>
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
    { id:'fa',     label:'Adds',    icon:Icons.spark },
    { id:'skipper',label:'Skipper', icon:Icons.sparkle },
    { id:'trade',  label:'Trade',   icon:Icons.trade },
    { id:'league', label:'League',  icon:Icons.diamond },
  ];
  return (
    <div style={{ display:'flex', borderTop:`1px solid ${V2.hairline}`, background:V2.surface, paddingBottom:18, paddingTop:8 }}>
      {items.map(it => {
        const active = page===it.id;
        return (
          <button key={it.id} onClick={()=>setPage(it.id)} style={{
            flex:1, background:'none', border:'none', padding:'8px 4px',
            display:'flex', flexDirection:'column', alignItems:'center', gap:5,
            color: active ? V2.ink : V2.muted, cursor:'pointer', fontFamily:'inherit',
          }}>
            {it.icon(active ? V2.ink : V2.muted, 17)}
            <div style={{ fontSize:9.5, fontWeight: active ? 700 : 500 }}>{it.label}</div>
          </button>
        );
      })}
    </div>
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
function V2Today({ model, sync, onRefresh, onNav }) {
  const rosterCount = model.roster.length;
  const starters = model.roster.filter(p => !['BN','IL','IR'].includes(String(p.slot || '').toUpperCase())).length;
  const bench = model.roster.filter(p => String(p.slot || '').toUpperCase() === 'BN').length;
  const injured = model.roster.filter(p => v2PlayerState(p) === 'injured').length;
  const myTeam = model.leagueTeams.find(t => t.me);
  const topTeam = [...model.leagueTeams].sort((a,b)=>a.rank-b.rank)[0];
  const syncCopy = sync.state === 'failed'
    ? sync.error || 'Last refresh failed.'
    : sync.state === 'loading'
      ? 'Loading latest Fantrax snapshot…'
      : sync.state === 'refreshing'
        ? (sync.notice || 'Refreshing Fantrax data…')
        : model.source === 'api'
          ? `Latest successful scrape is ${sync.label} old.`
          : 'Waiting for the first successful Fantrax scrape.';
  return (
    <div style={{ padding:'4px 16px 28px', display:'flex', flexDirection:'column', gap:16 }}>
      {sync.notice && sync.state !== 'refreshing' && (
        <V2Caution eyebrow="Heads up" tone="warn">{sync.notice}</V2Caution>
      )}
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:22, padding:18 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:14 }}>
          <div>
            <div style={{ fontSize:13, color:V2.muted, fontWeight:700 }}>Latest sync</div>
            <div style={{ marginTop:5, fontSize:19, lineHeight:1.25, fontWeight:700, fontFamily:V2.fontDisplay }}>{syncCopy}</div>
          </div>
          <div style={{ textAlign:'center', minWidth:76 }}>
            <div style={{ fontSize:28, fontWeight:700, color:V2.ink, fontFamily:V2.fontMono, letterSpacing:'-0.04em' }}>{rosterCount}</div>
            <div style={{ fontSize:10.5, color:V2.muted, fontWeight:800, letterSpacing:'0.08em', textTransform:'uppercase' }}>players</div>
          </div>
        </div>
      </div>

      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>Roster shape</V2Eyebrow>
        <V2StatRow stats={[
          { value:starters, label:'Starting', color:V2.inLineup },
          { value:bench, label:'Bench', color:V2.bench },
          { value:injured, label:'Injured', color:injured ? V2.bad : V2.muted },
        ]}/>
      </div>

      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>League standing</V2Eyebrow>
        <div style={{ display:'flex', alignItems:'baseline', justifyContent:'space-between', gap:12, marginTop:10 }}>
          <div style={{ minWidth:0 }}>
            <div style={{ fontSize:20, fontWeight:700, fontFamily:V2.fontDisplay }}>{myTeam ? `#${myTeam.rank} ${myTeam.name}` : model.teamName}</div>
            <div style={{ color:V2.muted, fontSize:12, fontWeight:700, marginTop:3 }}>{myTeam?.record || 'Record unavailable'} {myTeam?.streak ? `· ${myTeam.streak}` : ''}</div>
          </div>
          <div style={{ fontSize:18, fontWeight:700, fontFamily:V2.fontMono }}>{myTeam ? Math.round(myTeam.pts).toLocaleString() : '—'}</div>
        </div>
        {topTeam && !topTeam.me && (
          <div style={{ marginTop:12, paddingTop:12, borderTop:`1px solid ${V2.hairline2}`, color:V2.body, fontSize:13, lineHeight:1.45 }}>
            Leader: <span style={{ fontWeight:700 }}>{topTeam.name}</span> with <span style={{ fontWeight:700, fontFamily:V2.fontMono }}>{Math.round(topTeam.pts).toLocaleString()}</span> points.
          </div>
        )}
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
        <button onClick={()=>onNav('roster')} style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, padding:'14px', cursor:'pointer', textAlign:'left', fontFamily:'inherit' }}>
          <V2Eyebrow>Your roster</V2Eyebrow>
          <div style={{ fontSize:14, fontWeight:700, marginTop:6, fontFamily:V2.fontDisplay }}>By position →</div>
        </button>
        <button onClick={()=>onNav('fa')} style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, padding:'14px', cursor:'pointer', textAlign:'left', fontFamily:'inherit' }}>
          <V2Eyebrow color={V2.accent}>Waiver board</V2Eyebrow>
          <div style={{ fontSize:14, fontWeight:700, marginTop:6, color:V2.accent, fontFamily:V2.fontDisplay }}>Review swaps →</div>
        </button>
      </div>

      <V2Primary variant="dark" onClick={onRefresh} sub={model.source === 'api' ? `Snapshot ${model.snapshotId || ''}` : 'Waiting for first scrape…'}>
        {sync.state === 'refreshing' ? 'Refreshing...' : 'Refresh Fantrax data'}
      </V2Primary>
    </div>
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
    if (view === 'starting') return !['BN','IL','IR'].includes(slot);
    if (view === 'bench') return slot === 'BN';
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
    if (view === 'starting') return !['BN','IL','IR'].includes(slot);
    if (view === 'bench') return slot === 'BN';
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
function V2League({ model, onOpenTeam }) {
  const [sort, setSort] = React.useState('rank');
  const sorters = {
    rank:(a,b)=>a.rank-b.rank,
    pts:(a,b)=>b.pts-a.pts,
    name:(a,b)=>a.name.localeCompare(b.name),
  };
  const list = [...(model.leagueTeams || [])].sort(sorters[sort]);
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:12 }}>
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
function V2FreeAgents({ onOpenPlayer }) {
  const [filter, setFilter] = React.useState('ALL');
  const [state, setState] = React.useState({ status:'loading', payload:null, error:null });

  React.useEffect(() => {
    let cancelled = false;
    if (window.location.protocol === 'file:') {
      setState({ status:'ready', payload:v2MockWaiverPayload(), error:null });
      return () => { cancelled = true; };
    }
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
  const isMock = state.payload?.source === 'mock';
  const briefState = state.payload?.brief?.state;

  if (state.status === 'loading') {
    return <V2WaiverState eyebrow="Waiver board" title="Loading waiver swaps" body="Reading the latest Fantrax snapshot." />;
  }
  if (state.status === 'error') {
    return <V2WaiverState eyebrow="Waiver board" title="Waiver swaps unavailable" body={state.error || 'The API did not return a waiver board.'} tone="warn" />;
  }

  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <V2Caution eyebrow={isMock ? 'Mock waiver board' : 'Deterministic board'} tone="accent">
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
        <V2WaiverSwapCard key={card.id} card={card} onOpenPlayer={onOpenPlayer}/>
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

function V2WaiverSwapCard({ card, onOpenPlayer }) {
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

      <div style={{ borderTop:`1px solid ${V2.hairline2}`, padding:'12px 16px', display:'flex', gap:8 }}>
        <button onClick={()=>onOpenPlayer?.(add.id)} disabled={!onOpenPlayer || !add.id} style={{
          flex:1, background:V2.ink, color:'#fff', border:'none', padding:'11px 14px',
          borderRadius:999, fontSize:12.5, fontWeight:800, cursor:onOpenPlayer && add.id ? 'pointer' : 'default',
          fontFamily:'inherit',
        }}>Review swap</button>
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

function v2MockWaiverPayload() {
  const cards = (FREE_AGENTS || []).filter(f => f.swap).slice(0, 8).map((fa, idx) => {
    const move = (ROSTER || []).find(p => p.id === fa.swap.id) || {};
    const addFpg = Number(fa.l30avg || fa.proj30 || 0);
    const moveFpg = Number(move.proj || move.l30avg || 0);
    const net = addFpg - moveFpg;
    return {
      id:`mock-waiver-${fa.id}`,
      rank:idx+1,
      add:{ id:fa.id, name:fa.name, team:fa.team, positions:fa.pos, age:fa.age, fpg:addFpg, score_source:'L30/G' },
      move_out:{ id:move.id || fa.swap.id, name:fa.swap.name, team:move.team || '', positions:move.pos || '', slot:move.slot || 'BN', age:move.age, fpg:moveFpg, injury:move.injury || move.status },
      net_delta:Number.isFinite(net) ? Math.round(net * 10) / 10 : fa.vsExp,
      sort_score:fa.vsExp || 0,
      fills_position:fa.pos,
      fit:'direct',
      confidence:idx < 2 ? 'High' : 'Medium',
      why:fa.why,
      risk:fa.tradeoffs,
      dynasty_note:'Mock card only; real dynasty note comes from Fantrax snapshot data.',
      evidence_chips:[`${v2Signed(Number.isFinite(net)?net:fa.vsExp, 1)} FP/G`, `${fa.pos} fit`, 'Mock fallback'],
      explanation:{ state:'deterministic' },
    };
  });
  return {
    source:'mock',
    snapshot_id:null,
    taken_at:null,
    freshness:{ state:'fallback', age_minutes:null },
    cards,
    brief:{ state:'missing', text:null },
    message:null,
  };
}

// ── /trade-grader ──────────────────────────────────────────────
function V2TradeGrader() {
  const [text, setText] = React.useState("You give: Tobias Reyna\nYou get: Niko Castellanos, Rell Brookings");
  const [graded, setGraded] = React.useState(true);
  const grade = () => { if (!text.trim()) return; setGraded(true); };
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>Paste an offer</V2Eyebrow>
        <textarea
          value={text}
          onChange={e=>setText(e.target.value)}
          placeholder={"e.g.\nYou give: Tobias Reyna\nYou get: Niko Castellanos, Rell Brookings"}
          style={{
            width:'100%', minHeight:110, marginTop:8,
            border:`1px solid ${V2.hairline}`, borderRadius:12,
            padding:'12px 14px', fontSize:13.5, fontFamily:'inherit', outline:'none', resize:'vertical',
            background:V2.surface2, color:V2.ink, lineHeight:1.5,
          }}/>
        <div style={{ marginTop:14 }}>
          <V2Primary onClick={grade} sub={graded?'Re-run with new context →':'Skipper analyzes vs. your roster shape & league context'}>
            {Icons.sparkle('#fff', 14)} {graded?'Re-grade with Claude':'Grade with Claude'}
          </V2Primary>
        </div>
      </div>

      {graded && (
        <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
            <div>
              <V2Eyebrow color={V2.ok}>Skipper grade</V2Eyebrow>
              <div style={{ fontSize:48, fontWeight:600, color:V2.ok, letterSpacing:'-0.03em', lineHeight:1, marginTop:6, fontFamily:V2.fontDisplay }}>A−</div>
              <div style={{ fontSize:11.5, color:V2.muted, marginTop:6 }}>Take it · solid value for both sides</div>
            </div>
            <div style={{ width:74, height:74, position:'relative', flexShrink:0 }}>
              <svg width="74" height="74" viewBox="0 0 74 74">
                <circle cx="37" cy="37" r="30" fill="none" stroke={V2.hairline} strokeWidth="6"/>
                <circle cx="37" cy="37" r="30" fill="none" stroke={V2.ok} strokeWidth="6"
                  strokeDasharray={`${TRADE.fairness * 188.5} 188.5`} transform="rotate(-90 37 37)" strokeLinecap="round"/>
              </svg>
              <div style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center' }}>
                <div style={{ fontSize:14, fontWeight:700, fontVariantNumeric:'tabular-nums', fontFamily:V2.fontMono }}>{(TRADE.fairness*100).toFixed(0)}</div>
                <div style={{ fontSize:8.5, color:V2.muted, fontWeight:700, letterSpacing:'0.06em' }}>FAIR</div>
              </div>
            </div>
          </div>

          <div style={{ marginTop:14, paddingTop:14, borderTop:`1px solid ${V2.hairline2}` }}>
            <V2StatRow stats={[
              { value:`+${TRADE.myDelta}`, label:'Your wkly Δ', color:V2.ok },
              { value:`${TRADE.oppDelta}`, label:'Their wkly Δ', color:V2.warn },
              { value:'-2 yr', label:'Avg age Δ', color:V2.ok },
            ]}/>
          </div>

          <div style={{ marginTop:14, background:V2.surface2, borderRadius:12, padding:12, display:'flex', gap:10 }}>
            <div style={{ flexShrink:0, marginTop:1 }}>{Icons.sparkle(V2.accent, 14)}</div>
            <div style={{ fontSize:13, color:V2.body, lineHeight:1.55 }}>{TRADE.ai}</div>
          </div>
        </div>
      )}

      <div>
        <div style={{ padding:'0 4px 8px' }}><V2Eyebrow>Recent</V2Eyebrow></div>
        <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, overflow:'hidden' }}>
          {RECENT_GRADES.map((g,i)=>(
            <div key={g.id} style={{
              padding:'12px 14px', borderBottom: i===RECENT_GRADES.length-1?'none':`1px solid ${V2.hairline2}`,
              display:'flex', alignItems:'center', gap:12,
            }}>
              <div style={{
                width:36, height:36, borderRadius:10, fontSize:13, fontWeight:800, color:'#fff',
                background: g.accent==='good'?V2.ok:V2.injured, display:'flex', alignItems:'center', justifyContent:'center',
                fontFamily:V2.fontMono,
              }}>{g.grade}</div>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:13.5, fontWeight:600, lineHeight:1.2, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', fontFamily:V2.fontDisplay }}>{g.summary}</div>
                <div style={{ fontSize:11, color:V2.muted, marginTop:3 }}>{g.when} · {g.taken?'accepted':'declined'}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ background:V2.surface2, border:`1px dashed ${V2.hairline}`, borderRadius:14, padding:16, textAlign:'center' }}>
        <V2Eyebrow>Coming · Phase 4</V2Eyebrow>
        <div style={{ fontSize:13.5, color:V2.body, marginTop:8, lineHeight:1.55 }}>
          <span style={{ fontWeight:700, color:V2.ink, fontFamily:V2.fontDisplay }}>Trade Scout</span> will run every 4 days and surface trade ideas tailored to your weakest positions.
        </div>
      </div>
    </div>
  );
}

// ── /settings ──────────────────────────────────────────────────
function V2Settings({ model, sync, onRefresh, onSignOut }) {
  const healthy = sync.state !== 'failed';
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, padding:16 }}>
        <V2Eyebrow>Fantrax sync</V2Eyebrow>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginTop:8 }}>
          <div>
            <div style={{ fontSize:18, fontWeight:600, fontFamily:V2.fontDisplay }}>{healthy ? `Synced ${sync.label}` : 'Refresh failed'}</div>
            <div style={{ fontSize:11.5, color:V2.muted, marginTop:3 }}>Railway Postgres · manual refresh + daily cron</div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:6, background:healthy?V2.okSoft:V2.badSoft, color:healthy?V2.ok:V2.bad, padding:'6px 11px', borderRadius:999 }}>
            <div style={{ width:6, height:6, background:healthy?V2.ok:V2.bad, borderRadius:'50%' }}/>
            <span style={{ fontSize:11.5, fontWeight:700 }}>{healthy ? 'Healthy' : 'Failed'}</span>
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
          <button onClick={onRefresh} style={{ background:V2.ink, color:'#fff', border:'none', padding:'10px 14px', borderRadius:999, fontSize:12.5, fontWeight:700, cursor:'pointer', fontFamily:'inherit' }}>Refresh now</button>
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
function V2PlayerSheet({ id, onClose }) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(null);
  const [syncing, setSyncing] = React.useState(false);
  const [syncCooldown, setSyncCooldown] = React.useState(false);
  const [activeClip, setActiveClip] = React.useState(null);
  const requestSeqRef = React.useRef(0);
  const cooldownTimerRef = React.useRef(null);

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
      <div onClick={e=>e.stopPropagation()} style={{
        background:V2.bg, borderTopLeftRadius:18, borderTopRightRadius:18, width:'100%', height:'88%', overflow:'auto',
        display:'flex', flexDirection:'column',
      }}>
        <div style={{ height:5, width:42, background:V2.hairline, borderRadius:3, margin:'10px auto', flexShrink:0 }}/>
        <div style={{
          padding:'4px 14px 10px', display:'flex', alignItems:'center', justifyContent:'space-between', gap:10, flexShrink:0,
        }}>
          <button onClick={onClose} style={{
            background:'none', border:'none', padding:6, cursor:'pointer',
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
              style={{ position:'absolute', inset:0, width:'100%', height:'100%', objectFit:'cover' }}
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
  return (
    <div
      onClick={(e) => { e.stopPropagation(); onClose(); }}
      style={{ position:'absolute', inset:0, zIndex:30, background:'rgba(15,23,42,0.68)', display:'flex', alignItems:'flex-end' }}
    >
      <div
        onClick={e=>e.stopPropagation()}
        style={{
          width:'100%', height:'74%', background:V2.bg, borderTopLeftRadius:20, borderTopRightRadius:20,
          display:'flex', flexDirection:'column', overflow:'hidden', boxShadow:'0 -18px 50px rgba(15,23,42,0.28)',
        }}
      >
        <div style={{ padding:'12px 14px', display:'flex', alignItems:'center', gap:10, borderBottom:`1px solid ${V2.hairline}`, flexShrink:0 }}>
          <button onClick={onClose} aria-label="Close clip" style={{
            width:34, height:34, borderRadius:999, border:`1px solid ${V2.hairline}`,
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
              flexShrink:0, padding:'8px 10px', borderRadius:999, border:`1px solid ${V2.hairline}`,
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

// Donut ring: my-score vs opponent-score for the current weekly matchup.
// Sized to ~3 lines of bubble text (62px). Color scale tracks the margin —
// green when comfortably ahead, amber when within ±10, red when trailing.
function V2MatchupDonut({ matchup }) {
  if (!matchup) return null;
  const my  = Number(matchup.my_score) || 0;
  const opp = Number(matchup.opponent_score) || 0;
  const total = my + opp;
  const myPct = total > 0 ? Math.max(0, Math.min(1, my / total)) : 0.5;
  const margin = my - opp;
  const fillColor = margin >= 10 ? '#16a34a' : margin <= -10 ? '#dc2626' : '#d97706';

  const size = 62;
  const stroke = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = circumference * myPct;

  const myName  = matchup.my_team_name || 'You';
  const oppName = matchup.opponent_team_name || 'Opponent';

  return (
    <div style={{
      display:'flex', alignItems:'center', gap:12, marginBottom:8,
      padding:'8px 10px', background:V2.surface2, border:`1px solid ${V2.hairline}`, borderRadius:12,
    }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink:0 }}>
        <circle cx={size/2} cy={size/2} r={radius} fill="none" stroke={V2.hairline} strokeWidth={stroke}/>
        <circle
          cx={size/2} cy={size/2} r={radius}
          fill="none" stroke={fillColor} strokeWidth={stroke}
          strokeDasharray={`${filled} ${circumference}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${size/2} ${size/2})`}
        />
      </svg>
      <div style={{ display:'flex', flexDirection:'column', gap:1, fontSize:12, color:V2.body, minWidth:0, flex:1 }}>
        <div style={{ display:'flex', justifyContent:'space-between', gap:8 }}>
          <span style={{ color:V2.ink, fontWeight:700, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{myName}</span>
          <span style={{ color:V2.ink, fontVariantNumeric:'tabular-nums', fontWeight:700 }}>{my.toFixed(1)}</span>
        </div>
        <div style={{ display:'flex', justifyContent:'space-between', gap:8 }}>
          <span style={{ color:V2.muted, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{oppName}</span>
          <span style={{ color:V2.muted, fontVariantNumeric:'tabular-nums' }}>{opp.toFixed(1)}</span>
        </div>
        <div style={{ fontSize:11, fontWeight:800, color:fillColor, letterSpacing:'0.02em' }}>
          {margin > 0 ? `+${margin.toFixed(1)} lead` : margin < 0 ? `${margin.toFixed(1)} behind` : 'tied'}
        </div>
      </div>
    </div>
  );
}

// ── /skipper ───────────────────────────────────────────────────
function V2Skipper({ model, sync, onOpenPlayer }) {
  const prompts = ['Weekly matchup assessment', 'Deep matchup analysis', 'Best waiver swap to review?', 'Where am I weakest?', 'Who is my best 2B?'];
  const [msgs, setMsgs] = React.useState([]);
  const [input, setInput] = React.useState('');
  const [streaming, setStreaming] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [brief, setBrief] = React.useState({ status:'loading', data:null, error:null });
  const [chatModel, setChatModel] = React.useState(() => {
    const stored = v2StoredValue('sandlot.skipper.model', V2_SKIPPER_DEFAULT_MODEL);
    return V2_SKIPPER_MODELS.some(m => m.id === stored) ? stored : V2_SKIPPER_DEFAULT_MODEL;
  });
  const [reasoning, setReasoning] = React.useState(() => v2StoredValue('sandlot.skipper.reasoning', 'off') === 'on');
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
  const activeModel = V2_SKIPPER_MODELS.find(m => m.id === chatModel) || V2_SKIPPER_MODELS[0];

  const updateChatModel = React.useCallback((next) => {
    setChatModel(next);
    v2StoreValue('sandlot.skipper.model', next);
  }, []);

  const updateReasoning = React.useCallback((next) => {
    setReasoning(next);
    v2StoreValue('sandlot.skipper.reasoning', next ? 'on' : 'off');
  }, []);

  React.useEffect(() => {
    let cancelled = false;
    if (window.location.protocol === 'file:') {
      setBrief({ status:'missing', data:null, error:null });
      return () => { cancelled = true; };
    }
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
          .map(m => ({
            role: m.role === 'assistant' ? 'ai' : m.role,
            text: m.content,
          }));
        setMsgs(loaded);
      })
      .catch(e => { if (!cancelled) setError(`Couldn't load history: ${e.message}`); });
    return () => { cancelled = true; };
  }, []);

  // Auto-scroll to bottom when messages or streaming text changes
  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [msgs]);

  const send = async (text) => {
    const t = (text ?? input).trim();
    if (!t || streaming) return;
    setError(null);
    setInput('');
    // Tag the upcoming AI bubble with chart:'matchup' when the prompt asks for
    // a deep matchup read so V2Bubble can render the donut alongside the text.
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
          reasoning,
          reasoning_effort: reasoning ? 'medium' : null,
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
          } else if (evt.type === 'error') {
            setError(evt.message || 'Skipper failed');
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
    }
  };

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
        <div style={{ display:'flex', alignItems:'center', gap:8, margin:'0 0 12px', overflowX:'auto', paddingBottom:1 }}>
          <label style={{
            display:'flex', alignItems:'center', gap:7, flexShrink:0,
            background:V2.surface, border:`1px solid ${V2.hairline}`,
            borderRadius:999, padding:'6px 10px', color:V2.body,
          }}>
            <span style={{ fontSize:10.5, fontWeight:800, color:V2.muted, letterSpacing:'0.06em', textTransform:'uppercase' }}>Model</span>
            <select value={chatModel} onChange={e=>updateChatModel(e.target.value)} disabled={streaming} style={{
              border:'none', background:'transparent', color:V2.ink, outline:'none',
              fontFamily:'inherit', fontSize:12, fontWeight:800, maxWidth:120,
              opacity: streaming ? 0.65 : 1,
            }}>
              {V2_SKIPPER_MODELS.map(m => <option key={m.id} value={m.id}>{m.short}</option>)}
            </select>
          </label>
          <button type="button" onClick={()=>updateReasoning(!reasoning)} disabled={streaming} title="Use OpenRouter reasoning for this model" style={{
            flexShrink:0, display:'flex', alignItems:'center', gap:7,
            border:`1px solid ${reasoning ? V2.accent : V2.hairline}`,
            background:reasoning ? V2.accentSoft : V2.surface,
            color:reasoning ? V2.accent : V2.body,
            borderRadius:999, padding:'7px 11px', cursor:streaming ? 'not-allowed' : 'pointer',
            opacity:streaming ? 0.65 : 1, fontFamily:'inherit',
          }}>
            <span style={{ width:7, height:7, borderRadius:'50%', background:reasoning ? V2.accent : V2.muted }}/>
            <span style={{ fontSize:11.5, fontWeight:800 }}>Reasoning {reasoning ? 'on' : 'off'}</span>
          </button>
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
        {msgs.map((m,i)=> <V2Bubble key={i} m={m} renderText={renderText} matchup={model?.matchup}/>)}
        {streaming && msgs.length > 0 && msgs[msgs.length-1].role === 'ai' && !msgs[msgs.length-1].text && (
          <div style={{ color:V2.muted, fontSize:12, padding:'2px 4px 8px' }}>Thinking…</div>
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
          <button onClick={()=>send()} disabled={streaming} style={{
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
function V2Bubble({ m, renderText, matchup }) {
  if (m.role==='user') return (
    <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:8 }}>
      <div style={{ background:V2.accent, color:'#fff', padding:'9px 13px', borderRadius:'14px 14px 4px 14px', fontSize:13.5, maxWidth:'82%', lineHeight:1.4 }}>{m.text}</div>
    </div>
  );
  if (v2IsBrokenSkipperReply(m.text)) return null;
  const body = renderText ? renderText(m.text) : m.text;
  const showDonut = m.chart === 'matchup' && matchup && (matchup.my_score != null || matchup.opponent_score != null);
  return (
    <div style={{ display:'flex', marginBottom:10 }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, color:V2.ink, padding:'10px 13px', borderRadius:'14px 14px 14px 4px', fontSize:13.5, maxWidth:'92%', lineHeight:1.5 }}>
        {showDonut && <V2MatchupDonut matchup={matchup}/>}
        {body}
      </div>
    </div>
  );
}

function V2ChatSheet({ context, onClose }) {
  const ctxPrompts = {
    today:   ['Should I start Brennan today?', 'Who is my weakest slot?'],
    roster:  ['Where am I weakest?', 'Which position should I trade for?'],
    league:  ['Who is my biggest threat?', 'How do I match up vs Bullpen Brigade?'],
    fa:      ['Best add for OF?', 'Compare Talavera vs Hayes'],
    trade:   ['Suggest a trade I should pitch', 'Roast my last trade'],
    settings:['Is my sync healthy?'],
  }[context] || SUGGESTED_PROMPTS;
  return (
    <div onClick={onClose} style={{ position:'absolute', inset:0, background:'rgba(26,26,26,0.36)', display:'flex', alignItems:'flex-end', zIndex:10 }}>
      <div onClick={e=>e.stopPropagation()} style={{
        background:V2.bg, borderTopLeftRadius:22, borderTopRightRadius:22, width:'100%', height:'82%', display:'flex', flexDirection:'column',
      }}>
        <div style={{ padding:'14px 16px', display:'flex', alignItems:'center', justifyContent:'space-between', borderBottom:`1px solid ${V2.hairline}` }}>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            {Icons.sparkle(V2.accent, 16)}
            <div style={{ fontWeight:600, fontFamily:V2.fontDisplay, fontSize:16 }}>Skipper</div>
            <div style={{ fontSize:10.5, background:V2.accentSoft, color:V2.accent, fontWeight:700, padding:'2px 8px', borderRadius:999, letterSpacing:'0.04em', textTransform:'uppercase' }}>
              context: {context}
            </div>
          </div>
          <button onClick={onClose} style={{ background:'none', border:'none', cursor:'pointer', padding:6 }}>{Icons.close(V2.muted, 14)}</button>
        </div>
        <div style={{ flex:1, overflow:'hidden' }}><V2ChatInner ctxPrompts={ctxPrompts}/></div>
      </div>
    </div>
  );
}
function V2ChatInner({ ctxPrompts }) {
  const [msgs, setMsgs] = React.useState(AI_SEED);
  const [input, setInput] = React.useState('');
  const send = (t) => {
    const text = (t ?? input).trim();
    if (!text) return;
    setMsgs(m => [...m, { role:'user', text }, { role:'ai', text:'…' }]);
    setInput('');
    setTimeout(()=>{
      setMsgs(m => { const next=[...m]; next[next.length-1]={role:'ai',text:'(Skipper would respond using Kimi with full league context loaded.)'}; return next; });
    }, 700);
  };
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>
      <div style={{ flex:1, overflow:'auto', padding:'14px 16px' }}>
        {msgs.map((m,i)=> <V2Bubble key={i} m={m}/>)}
      </div>
      <div style={{ borderTop:`1px solid ${V2.hairline}`, padding:'12px 14px 16px', background:V2.surface }}>
        <div style={{ display:'flex', gap:6, overflowX:'auto', paddingBottom:10 }}>
          {ctxPrompts.map(p => (
            <button key={p} onClick={()=>send(p)} style={{
              flexShrink:0, padding:'8px 12px', borderRadius:999, border:`1px solid ${V2.hairline}`,
              background:V2.surface2, color:V2.body, fontSize:11.5, cursor:'pointer', fontFamily:'inherit', fontWeight:600,
            }}>{p}</button>
          ))}
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')send();}}
            placeholder="Ask Skipper..."
            style={{ flex:1, border:`1px solid ${V2.hairline}`, background:V2.surface2, borderRadius:999, padding:'12px 16px', outline:'none', fontSize:14, color:V2.ink, fontFamily:'inherit' }}/>
          <button onClick={()=>send()} style={{
            width:42, height:42, borderRadius:'50%', background:V2.ink, border:'none', cursor:'pointer',
            display:'flex', alignItems:'center', justifyContent:'center',
          }}>{Icons.send('#fff', 14)}</button>
        </div>
      </div>
    </div>
  );
}

window.V2App = V2App;
