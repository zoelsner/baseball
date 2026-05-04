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

function v2FallbackModel() {
  return {
    source: 'mock',
    sync: { state:'fallback', label:'mock', ageMinutes:null, error:null },
    teamName: TEAM_NAME,
    leagueName: LEAGUE_NAME,
    roster: ROSTER,
    rosterMeta: {},
    leagueTeams: LEAGUE_TEAMS,
    snapshotId: null,
    takenAt: null,
  };
}

function v2NormalizeSnapshot(payload) {
  const freshness = payload?.freshness || {};
  const roster = (payload?.roster || []).filter(Boolean).map((p, idx) => {
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
  });
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
    },
    teamName: payload?.team_name || TEAM_NAME,
    leagueName: LEAGUE_NAME,
    roster: roster.length ? roster : ROSTER,
    rosterMeta: payload?.roster_meta || {},
    leagueTeams: leagueTeams.length ? leagueTeams : LEAGUE_TEAMS,
    snapshotId: payload?.snapshot_id || null,
    takenAt: payload?.taken_at || null,
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

// ── App shell ──────────────────────────────────────────────────
function V2App({ initial }) {
  const [page, setPage] = React.useState(initial?.page || 'today');
  const [detail, setDetail] = React.useState(initial?.detail || null);
  const [authed, setAuthed] = React.useState(initial?.auth ? false : true);
  const [model, setModel] = React.useState(v2FallbackModel);
  const [syncState, setSyncState] = React.useState({ state:'loading', label:'loading', error:null });

  const loadSnapshot = React.useCallback(async () => {
    if (window.location.protocol === 'file:') {
      setModel(v2FallbackModel());
      setSyncState({ state:'fallback', label:'mock', error:null });
      return;
    }
    setSyncState(s => ({ ...s, state:'loading', label:'loading', error:null }));
    try {
      const res = await fetch('/api/snapshot/latest');
      if (!res.ok) throw new Error(res.status === 404 ? 'No snapshot yet' : `Snapshot failed (${res.status})`);
      const payload = await res.json();
      const next = v2NormalizeSnapshot(payload);
      setModel(next);
      setSyncState(next.sync);
    } catch (err) {
      setModel(v2FallbackModel());
      setSyncState({ state:'fallback', label:'mock', error:err.message });
    }
  }, []);

  const refreshSnapshot = React.useCallback(async () => {
    if (window.location.protocol === 'file:') return;
    setSyncState(s => ({ ...s, state:'refreshing', label:'syncing', error:null }));
    try {
      const res = await fetch('/api/refresh', { method:'POST' });
      const payload = await res.json().catch(()=>({}));
      if (!res.ok) {
        const message = payload?.detail?.errors?.join('; ') || payload?.detail || `Refresh failed (${res.status})`;
        throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
      }
      const next = v2NormalizeSnapshot(payload.snapshot);
      setModel(next);
      setSyncState(next.sync);
    } catch (err) {
      setSyncState({ state:'failed', label:'failed', error:err.message });
    }
  }, []);

  React.useEffect(() => { loadSnapshot(); }, [loadSnapshot]);

  if (!authed) return <V2Auth onSignIn={()=>setAuthed(true)}/>;

  const pages = {
    today:   <V2Today model={model} sync={syncState} onRefresh={refreshSnapshot} onNav={setPage}/>,
    roster:  <V2Roster model={model} onPlayer={setDetail}/>,
    league:  <V2League model={model}/>,
    fa:      <V2FreeAgents/>,
    trade:   <V2TradeGrader/>,
    skipper: <V2Skipper model={model} sync={syncState}/>,
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
      {detail && <V2PlayerSheet player={detail} onClose={()=>setDetail(null)}/>}
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
    today:`Fantrax snapshot · ${model.source === 'api' ? 'live data' : 'mock fallback'}`,
    roster:`${model.teamName}`,
    league:`${model.leagueName} · ${model.leagueTeams.length} teams`,
    fa:`${FREE_AGENTS.length} picks · curated weekly`,
    trade:'Paste an offer for instant analysis',
    skipper:`Reading ${model.teamName}`,
    settings:`${model.leagueName}`,
  }[page];
  const syncColor = sync.state === 'failed' ? V2.bad : sync.state === 'refreshing' ? V2.warn : sync.state === 'fallback' ? V2.muted : V2.ok;
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
    { id:'trade',  label:'Trade',   icon:Icons.trade },
    { id:'league', label:'League',  icon:Icons.diamond },
    { id:'skipper',label:'Skipper', icon:Icons.sparkle },
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
    ? sync.error || 'Last refresh failed. Existing data stays visible.'
    : model.source === 'api'
      ? `Latest successful scrape is ${sync.label} old.`
      : 'Showing mock data until the first successful Fantrax scrape is stored.';
  return (
    <div style={{ padding:'4px 16px 28px', display:'flex', flexDirection:'column', gap:16 }}>
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
          <V2Eyebrow color={V2.accent}>{FREE_AGENTS.length} adds</V2Eyebrow>
          <div style={{ fontSize:14, fontWeight:700, marginTop:6, color:V2.accent, fontFamily:V2.fontDisplay }}>Best pickups →</div>
        </button>
      </div>

      <V2Primary variant="dark" onClick={onRefresh} sub={model.source === 'api' ? `Snapshot ${model.snapshotId || ''}` : 'First Railway scrape will replace mock data'}>
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
          <V2RosterSlot key={p.id} player={p} last={i===list.length-1} onClick={()=>onPlayer(p)}/>
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
        {players.map((p,i) => <V2RosterSlot key={p.id} player={p} last={i===players.length-1} onClick={()=>onPlayer(p)}/>)}
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
function V2League({ model }) {
  const [sort, setSort] = React.useState('rank');
  const [team, setTeam] = React.useState(null);
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
        {list.length ? list.map(t => <V2TeamRow key={t.id} team={t} expanded={team===t.id} onToggle={()=>setTeam(team===t.id?null:t.id)}/>) : (
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:14, padding:18, color:V2.muted, fontSize:13, fontWeight:700 }}>
            No standings in the latest snapshot.
          </div>
        )}
      </div>
    </div>
  );
}
function V2TeamRow({ team, expanded, onToggle }) {
  const tierColor = team.rank<=4 ? V2.ok : team.rank<=8 ? V2.accent : V2.warn;
  return (
    <div style={{ background: team.me?V2.accentSoft:V2.surface, border:`1px solid ${team.me?V2.accent:V2.hairline}`, borderRadius:14, overflow:'hidden' }}>
      <button onClick={onToggle} style={{
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
      {expanded && (
        <div style={{ borderTop:`1px solid ${V2.hairline2}`, padding:'12px 14px' }}>
          <V2Eyebrow>Position strength</V2Eyebrow>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:6, marginTop:8 }}>
            {POSITION_GROUPS.slice(0,9).map(g => {
              const r = ((g.rankInLeague + team.rank) % 12) + 1;
              const c = r<=3?V2.ok:r<=8?V2.accent:V2.warn;
              return (
                <div key={g.pos} style={{ background:V2.surface2, borderRadius:8, padding:'7px 9px', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
                  <span style={{ fontSize:11, fontWeight:700, color:V2.muted }}>{g.pos}</span>
                  <span style={{ fontSize:11.5, fontWeight:800, color:c, fontVariantNumeric:'tabular-nums' }}>#{r}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── /free-agents ───────────────────────────────────────────────
function V2FreeAgents() {
  const [filter, setFilter] = React.useState('ALL');
  const positions = ['ALL','OF','2B','SS','SP','RP','3B'];
  const list = FREE_AGENTS.filter(f => filter==='ALL' || f.pos.includes(filter));
  return (
    <div style={{ padding:'4px 16px 32px', display:'flex', flexDirection:'column', gap:14 }}>
      <V2Caution eyebrow="Skipper picks" tone="accent">
        Six worth-grabbing free agents this week, ranked by upside vs. your weakest slots.
      </V2Caution>
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
      {list.map(fa => <V2FACard key={fa.id} fa={fa}/>)}
    </div>
  );
}
function V2FACard({ fa }) {
  const expC = fa.vsExp>=1?V2.ok:fa.vsExp<=-1?V2.warn:V2.muted;
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:18, overflow:'hidden' }}>
      <div style={{ padding:'14px 16px', display:'flex', alignItems:'flex-start', gap:12 }}>
        <Avatar name={fa.name} size={40}/>
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <span style={{ fontSize:15, fontWeight:600, lineHeight:1.2, fontFamily:V2.fontDisplay }}>{fa.name}</span>
            {fa.trend==='hot' && <span style={{ background:V2.okSoft, color:V2.ok, fontSize:9.5, fontWeight:800, padding:'1px 6px', borderRadius:5 }}>HOT</span>}
          </div>
          <div style={{ fontSize:11.5, color:V2.muted, marginTop:3, display:'flex', gap:8 }}>
            <span>{fa.pos} · {fa.team} · age {fa.age}</span>
            <span>·</span>
            <span>{fa.rosteredPct}% rostered</span>
          </div>
        </div>
        <div style={{ textAlign:'right' }}>
          <div style={{ fontSize:9.5, color:V2.muted, fontWeight:800, letterSpacing:'0.06em', textTransform:'uppercase' }}>L30/G</div>
          <div style={{ fontSize:16, fontWeight:700, fontVariantNumeric:'tabular-nums', fontFamily:V2.fontMono }}>{fa.l30avg.toFixed(1)}</div>
          <div style={{ fontSize:10, fontWeight:700, color:expC, fontVariantNumeric:'tabular-nums', marginTop:1 }}>
            {fa.vsExp>=0?'+':''}{fa.vsExp.toFixed(1)}
          </div>
        </div>
      </div>
      <div style={{ padding:'2px 16px 14px' }}>
        <div style={{ display:'flex', gap:10, alignItems:'flex-start', marginBottom:10 }}>
          <div style={{ flexShrink:0, width:6, height:6, marginTop:7, background:V2.ok, borderRadius:'50%' }}/>
          <div style={{ fontSize:13, color:V2.body, lineHeight:1.55 }}>
            <span style={{ fontWeight:700, color:V2.ok }}>Why grab. </span>{fa.why}
          </div>
        </div>
        <div style={{ display:'flex', gap:10, alignItems:'flex-start' }}>
          <div style={{ flexShrink:0, width:6, height:6, marginTop:7, background:V2.warn, borderRadius:'50%' }}/>
          <div style={{ fontSize:13, color:V2.body, lineHeight:1.55 }}>
            <span style={{ fontWeight:700, color:V2.warn }}>Tradeoffs. </span>{fa.tradeoffs}
          </div>
        </div>
      </div>
      {fa.swap ? (
        <div style={{ padding:'12px 16px', display:'flex', alignItems:'center', gap:10, borderTop:`1px solid ${V2.hairline2}` }}>
          <div style={{ flex:1, minWidth:0 }}>
            <V2Eyebrow>Suggested swap</V2Eyebrow>
            <div style={{ fontSize:13, fontWeight:700, color:V2.ink, marginTop:4 }}>Drop {fa.swap.name}</div>
            <div style={{ fontSize:11.5, color:V2.muted, marginTop:1 }}>{fa.swap.why}</div>
          </div>
          <button style={{ background:V2.ink, color:'#fff', border:'none', padding:'10px 16px', borderRadius:999, fontSize:12.5, fontWeight:700, cursor:'pointer', fontFamily:'inherit', flexShrink:0 }}>Add</button>
        </div>
      ) : (
        <div style={{ padding:'12px 16px', display:'flex', justifyContent:'flex-end', borderTop:`1px solid ${V2.hairline2}` }}>
          <button style={{ background:'none', color:V2.body, border:`1px solid ${V2.hairline}`, padding:'9px 14px', borderRadius:999, fontSize:12, fontWeight:700, cursor:'pointer', fontFamily:'inherit' }}>Add to watchlist</button>
        </div>
      )}
    </div>
  );
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
function V2PlayerSheet({ player, onClose }) {
  const d = {
    ...PLAYER_DETAIL,
    pos: player.pos || PLAYER_DETAIL.pos,
    team: player.team || PLAYER_DETAIL.team,
    age: player.age || PLAYER_DETAIL.age,
    proj: player.proj || player.fppg || 0,
    l30avg: player.fppg || PLAYER_DETAIL.l30avg,
    vsExp: player.vsExp || 0,
    outlook: player.raw
      ? `${player.name} is currently in ${player.slot || 'BN'} with ${player.fppg ? player.fppg.toFixed(1) : 'no'} FP/G in the latest Fantrax snapshot.`
      : PLAYER_DETAIL.outlook,
  };
  const expColor = d.vsExp >= 1 ? V2.ok : d.vsExp <= -1 ? V2.warn : V2.muted;
  return (
    <div onClick={onClose} style={{ position:'absolute', inset:0, background:'rgba(15,23,42,0.32)', display:'flex', alignItems:'flex-end', zIndex:10 }}>
      <div onClick={e=>e.stopPropagation()} style={{
        background:V2.bg, borderTopLeftRadius:18, borderTopRightRadius:18, width:'100%', height:'88%', overflow:'auto',
      }}>
        <div style={{ height:5, width:42, background:V2.hairline, borderRadius:3, margin:'10px auto' }}/>
        <div style={{ padding:'8px 16px 16px' }}>
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <Avatar name={player.name} size={48}/>
            <div style={{ flex:1 }}>
              <div style={{ fontSize:18, fontWeight:700, letterSpacing:'-0.01em', fontFamily:V2.fontDisplay }}>{player.name}</div>
              <div style={{ fontSize:11.5, color:V2.muted, marginTop:2 }}>{d.pos} · {d.team} · age {d.age}</div>
            </div>
            <button onClick={onClose} style={{ background:'none', border:'none', cursor:'pointer', padding:6 }}>{Icons.close(V2.muted, 14)}</button>
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr 1fr', gap:6, marginTop:14 }}>
            <V2KPI label="Today" value={d.proj.toFixed(1)}/>
            <V2KPI label="L7" value={d.l7}/>
            <V2KPI label="L30/g" value={d.l30avg.toFixed(1)}/>
            <V2KPI label="vs Exp" value={`${d.vsExp>=0?'+':''}${d.vsExp.toFixed(1)}`} accent={expColor}/>
          </div>
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:12, padding:14, marginTop:14 }}>
            <V2Eyebrow>Outlook</V2Eyebrow>
            <div style={{ fontSize:13.5, lineHeight:1.55, color:V2.ink, marginTop:6 }}>{d.outlook}</div>
          </div>
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:12, padding:14, marginTop:10 }}>
            <V2Eyebrow>Last 14 games</V2Eyebrow>
            <div style={{ marginTop:8 }}>
              <Sparkline values={d.trend14} w={300} h={50} stroke={V2.accent} fill={V2.accentSoft} strokeWidth={2}/>
            </div>
          </div>
          <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:12, padding:'4px 0', marginTop:10 }}>
            {d.splits.map((s,i)=>(
              <div key={s.label} style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'10px 14px', borderBottom: i===d.splits.length-1?'none':`1px solid ${V2.hairline2}`, fontSize:13 }}>
                <span style={{ color:V2.body, fontWeight:600 }}>{s.label}</span>
                <div style={{ display:'flex', alignItems:'center', gap:10, minWidth:140 }}>
                  <div style={{ flex:1, height:5, background:V2.hairline2, borderRadius:3 }}>
                    <div style={{ width:`${(s.v/15)*100}%`, height:'100%', background:V2.accent, borderRadius:3 }}/>
                  </div>
                  <span style={{ fontVariantNumeric:'tabular-nums', fontWeight:700, fontFamily:V2.fontMono, minWidth:32, textAlign:'right' }}>{s.v.toFixed(1)}</span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop:14 }}>
            <V2Eyebrow>Latest</V2Eyebrow>
            <div style={{ display:'flex', flexDirection:'column', gap:8, paddingBottom:32, marginTop:8 }}>
              {d.news.map((n,i)=>(
                <div key={i} style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:10, padding:'10px 12px' }}>
                  <div style={{ fontSize:10.5, color:V2.muted, fontWeight:700 }}>{n.src.toUpperCase()} · {n.when}</div>
                  <div style={{ fontSize:13, marginTop:3, lineHeight:1.5 }}>{n.text}</div>
                </div>
              ))}
              {d.twitter.map((t,i)=>(
                <a key={`t${i}`} href="#" onClick={e=>e.preventDefault()} style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:10, padding:'10px 12px', textDecoration:'none', color:'inherit', display:'block' }}>
                  <div style={{ display:'flex', alignItems:'center', gap:5, fontSize:10.5, color:V2.muted, fontWeight:700 }}>
                    {Icons.twitter(V2.muted, 11)} <span>{t.handle.toUpperCase()} · {t.when}</span>
                  </div>
                  <div style={{ fontSize:13, marginTop:3, lineHeight:1.5 }}>{t.text}</div>
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
function V2KPI({ label, value, accent }) {
  return (
    <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, borderRadius:10, padding:'10px 8px', textAlign:'center' }}>
      <div style={{ fontSize:9.5, color:V2.muted, fontWeight:800, letterSpacing:'0.08em', textTransform:'uppercase' }}>{label}</div>
      <div style={{ fontSize:16, fontWeight:700, color:accent||V2.ink, fontVariantNumeric:'tabular-nums', fontFamily:V2.fontMono, marginTop:4 }}>{value}</div>
    </div>
  );
}

// ── /skipper ───────────────────────────────────────────────────
function V2Skipper() {
  const prompts = ['Who is my best 2B?', 'Compare my pitching to the league', 'Where am I weakest?'];
  const [msgs, setMsgs] = React.useState([]);
  const [input, setInput] = React.useState('');
  const [streaming, setStreaming] = React.useState(false);
  const [error, setError] = React.useState(null);
  const scrollRef = React.useRef(null);

  // Load history on mount
  React.useEffect(() => {
    let cancelled = false;
    fetch('/api/skipper/messages')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`history ${r.status}`)))
      .then(data => {
        if (cancelled) return;
        const loaded = (data.messages || []).map(m => ({
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
    // Optimistically append user message + empty AI bubble we'll fill via stream.
    setMsgs(m => [...m, { role:'user', text:t }, { role:'ai', text:'' }]);
    setStreaming(true);

    try {
      const resp = await fetch('/api/skipper/messages', {
        method: 'POST',
        headers: { 'Content-Type':'application/json' },
        body: JSON.stringify({ content: t }),
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

  return (
    <div style={{ height:'100%', display:'flex', flexDirection:'column' }}>
      <div ref={scrollRef} style={{ flex:1, overflow:'auto', padding:'4px 16px 18px' }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, margin:'4px 0 10px' }}>
          <div style={{ width:28, height:28, borderRadius:'50%', background:V2.warn, color:'#fff', display:'flex', alignItems:'center', justifyContent:'center' }}>{Icons.sparkle('#fff', 14)}</div>
          <V2Eyebrow color={V2.muted}>Skipper</V2Eyebrow>
        </div>
        {msgs.length === 0 && !streaming && (
          <div style={{ color:V2.muted, fontSize:13, padding:'18px 4px', lineHeight:1.5 }}>
            Ask anything about your roster. Skipper reads the latest snapshot and answers from real data only.
          </div>
        )}
        {msgs.map((m,i)=> <V2Bubble key={i} m={m}/>)}
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
            placeholder={streaming ? 'Skipper is responding…' : 'Ask about your roster, trades, matchups...'}
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

// ── Skipper chat (inlined V2Bubble) ────────────────────────────
function V2Bubble({ m }) {
  if (m.role==='user') return (
    <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:8 }}>
      <div style={{ background:V2.accent, color:'#fff', padding:'9px 13px', borderRadius:'14px 14px 4px 14px', fontSize:13.5, maxWidth:'82%', lineHeight:1.4 }}>{m.text}</div>
    </div>
  );
  return (
    <div style={{ display:'flex', marginBottom:10 }}>
      <div style={{ background:V2.surface, border:`1px solid ${V2.hairline}`, color:V2.ink, padding:'10px 13px', borderRadius:'14px 14px 14px 4px', fontSize:13.5, maxWidth:'92%', lineHeight:1.5 }}>{m.text}</div>
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
