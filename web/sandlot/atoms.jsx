// Shared atoms used across all three directions

// ── Status / trend / vs-expectation tokens ────────────────────
// Keep semantic, each direction maps these to its own palette.
const STATUS_LABEL = { ok:'Active', dtd:'Day-to-day', il10:'IL-10', il60:'IL-60' };

function vsExpTier(v){
  if (v >= 3)  return 'great';
  if (v >= 1)  return 'good';
  if (v > -1)  return 'meh';
  if (v > -3)  return 'bad';
  return 'awful';
}

// Sparkline (used in dashboard + detail)
function Sparkline({ values, w=120, h=28, stroke='#0a0a0a', fill='none', strokeWidth=1.5 }) {
  if (!values || !values.length) return null;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  const pts = values.map((v,i) => `${i*step},${h - ((v-min)/range)*h}`).join(' ');
  const area = `0,${h} ${pts} ${w},${h}`;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{display:'block'}}>
      {fill !== 'none' && <polyline points={area} fill={fill} stroke="none"/>}
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

// Tiny icons (no emoji, no libraries)
const Icons = {
  bell: (c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M8 2v1M3.5 7a4.5 4.5 0 1 1 9 0c0 3 1 4 1 4h-11s1-1 1-4Z" stroke={c} strokeWidth="1.4" strokeLinejoin="round"/><path d="M6.5 13a1.5 1.5 0 0 0 3 0" stroke={c} strokeWidth="1.4" strokeLinecap="round"/></svg>,
  warn: (c='#b45309',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M8 2.5 14.5 13.5h-13L8 2.5Z" stroke={c} strokeWidth="1.4" strokeLinejoin="round"/><path d="M8 7v3" stroke={c} strokeWidth="1.4" strokeLinecap="round"/><circle cx="8" cy="11.8" r=".8" fill={c}/></svg>,
  spark: (c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M8 2 9.5 6.5 14 8l-4.5 1.5L8 14l-1.5-4.5L2 8l4.5-1.5L8 2Z" stroke={c} strokeWidth="1.3" strokeLinejoin="round"/></svg>,
  arrow:(c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" stroke={c} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  search:(c='#737373',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4.5" stroke={c} strokeWidth="1.4"/><path d="M10.5 10.5 14 14" stroke={c} strokeWidth="1.4" strokeLinecap="round"/></svg>,
  swap: (c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M3 5h9m-2-2 2 2-2 2M13 11H4m2 2-2-2 2-2" stroke={c} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  up:   (c='#0a0a0a',s=12)=> <svg width={s} height={s} viewBox="0 0 12 12" fill="none"><path d="M6 2v8M3 5l3-3 3 3" stroke={c} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  down: (c='#0a0a0a',s=12)=> <svg width={s} height={s} viewBox="0 0 12 12" fill="none"><path d="M6 10V2M3 7l3 3 3-3" stroke={c} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  flat: (c='#0a0a0a',s=12)=> <svg width={s} height={s} viewBox="0 0 12 12" fill="none"><path d="M2 6h8" stroke={c} strokeWidth="1.5" strokeLinecap="round"/></svg>,
  send: (c='#fff',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="m2 14 12-6L2 2l2 6-2 6Z" fill={c}/></svg>,
  twitter:(c='#0a0a0a',s=12)=> <svg width={s} height={s} viewBox="0 0 12 12" fill="none"><path d="M9.5 1.5h1.7L7.5 5.7 12 11h-3.4L6 7.6 3 11H1.3l4-4.5L1 1.5h3.4L7 4.6l2.5-3.1Z" fill={c}/></svg>,
  diamond:(c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M8 2 14 8 8 14 2 8 8 2Z" stroke={c} strokeWidth="1.4" strokeLinejoin="round"/></svg>,
  ball: (c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke={c} strokeWidth="1.3"/><path d="M3.5 4.5C5 6 5 10 3.5 11.5M12.5 4.5C11 6 11 10 12.5 11.5" stroke={c} strokeWidth="1.1" strokeLinecap="round"/></svg>,
  bat:  (c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M3 13 13 3M2.5 13.5l1.4-1.4M11 3l2 2" stroke={c} strokeWidth="1.4" strokeLinecap="round"/><circle cx="12" cy="4" r="1.4" stroke={c} strokeWidth="1.2"/></svg>,
  home: (c='#0a0a0a',s=16)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M2 7 8 2l6 5v6.5a.5.5 0 0 1-.5.5h-3v-4h-3v4h-3a.5.5 0 0 1-.5-.5V7Z" stroke={c} strokeWidth="1.4" strokeLinejoin="round"/></svg>,
  list: (c='#0a0a0a',s=16)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M3 4h10M3 8h10M3 12h10" stroke={c} strokeWidth="1.4" strokeLinecap="round"/></svg>,
  trade:(c='#0a0a0a',s=16)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M3 5h9l-2-2M13 11H4l2 2" stroke={c} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  chat: (c='#0a0a0a',s=16)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M3 4.5A1.5 1.5 0 0 1 4.5 3h7A1.5 1.5 0 0 1 13 4.5v5A1.5 1.5 0 0 1 11.5 11H7l-3 2.5V11h-.5A.5.5 0 0 1 3 10.5v-6Z" stroke={c} strokeWidth="1.4" strokeLinejoin="round"/></svg>,
  sparkle:(c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M8 1.5 9.2 6 13.5 8 9.2 10 8 14.5 6.8 10 2.5 8 6.8 6 8 1.5Z" fill={c}/><path d="M13 1.5 13.6 3.4 15.5 4l-1.9.6L13 6.5l-.6-1.9L10.5 4l1.9-.6L13 1.5Z" fill={c} opacity=".6"/></svg>,
  filter:(c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="M2 4h12l-4.5 5v4l-3-1.5V9L2 4Z" stroke={c} strokeWidth="1.4" strokeLinejoin="round"/></svg>,
  close:(c='#0a0a0a',s=14)=> <svg width={s} height={s} viewBox="0 0 16 16" fill="none"><path d="m4 4 8 8M12 4l-8 8" stroke={c} strokeWidth="1.5" strokeLinecap="round"/></svg>,
};

// Trend dot
function TrendIcon({ trend, size=12, color='#0a0a0a' }) {
  if (trend === 'hot')  return Icons.up(color, size);
  if (trend === 'cold') return Icons.down(color, size);
  return Icons.flat(color, size);
}

// Avatar — initials, deterministic muted bg
function Avatar({ name, size=32, palette='neutral' }) {
  const initials = name.split(' ').map(s=>s[0]).join('').slice(0,2).toUpperCase();
  // hash → hue
  let h = 0; for (const ch of name) h = (h*31 + ch.charCodeAt(0)) >>> 0;
  const hue = h % 360;
  const bg = palette==='warm' ? `oklch(0.92 0.04 ${hue})` : `oklch(0.94 0.02 ${hue})`;
  const fg = palette==='warm' ? `oklch(0.42 0.06 ${hue})` : `oklch(0.38 0.04 ${hue})`;
  return (
    <div style={{
      width:size, height:size, borderRadius:'50%', background:bg, color:fg,
      display:'flex', alignItems:'center', justifyContent:'center',
      fontWeight:600, fontSize: size*0.38, letterSpacing:'-0.01em', flexShrink:0,
    }}>{initials}</div>
  );
}

function PlayerPhoto({ mlbId, name, size=56 }) {
  const id = mlbId ? String(mlbId) : '';
  const [failedFor, setFailedFor] = React.useState(null);
  const showPhoto = id && failedFor !== id;
  if (!showPhoto) return <Avatar name={name || '?'} size={size} palette="warm"/>;
  const src = `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${encodeURIComponent(id)}/headshot/67/current`;
  return (
    <img
      src={src}
      alt={name ? `${name} headshot` : 'Player headshot'}
      onError={() => setFailedFor(id)}
      style={{
        width:size, height:size, borderRadius:'50%', objectFit:'cover',
        display:'block', flexShrink:0, background:'#f4efe6',
        border:'1px solid rgba(15,23,42,0.08)',
      }}
    />
  );
}

// Build a lowercase-full-name -> fantrax_id map from a snapshot's player_index.
// Used by Skipper to wrap player mentions as tappable links.
function buildPlayerNameIndex(playerIndex) {
  const out = new Map();
  for (const p of playerIndex || []) {
    if (!p || !p.id || !p.name) continue;
    const norm = String(p.name).trim().toLowerCase();
    if (norm && !out.has(norm)) out.set(norm, p.id);
  }
  return out;
}

Object.assign(window, {
  STATUS_LABEL, vsExpTier,
  Sparkline, Icons, TrendIcon, Avatar, PlayerPhoto,
  buildPlayerNameIndex,
});
