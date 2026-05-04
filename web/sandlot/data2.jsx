// v2 data — extends v1 with league teams, free agents, position-rank info,
// sync status, and trade grader paste flow.

const LEAGUE_TEAMS = [
  { id:'t1', name:'Curveball Cartel',     mgr:'Jordan',  rank:1, record:'24-12', pts:2841, streak:'W4', logo:'CC' },
  { id:'t2', name:'Sandlot Syndicate',    mgr:'You',     rank:2, record:'22-14', pts:2789, streak:'W2', logo:'SS', me:true },
  { id:'t3', name:'Bullpen Brigade',      mgr:'Marcus',  rank:3, record:'21-15', pts:2742, streak:'L1', logo:'BB' },
  { id:'t4', name:'Sliders Anonymous',    mgr:'Priya',   rank:4, record:'20-16', pts:2698, streak:'W1', logo:'SA' },
  { id:'t5', name:'Triple Threat',        mgr:'Devin',   rank:5, record:'18-18', pts:2611, streak:'L2', logo:'TT' },
  { id:'t6', name:'The Rally Caps',       mgr:'Ana',     rank:6, record:'17-19', pts:2588, streak:'W1', logo:'RC' },
  { id:'t7', name:'Fastball Fanatics',    mgr:'Owen',    rank:7, record:'15-21', pts:2502, streak:'L3', logo:'FF' },
  { id:'t8', name:'Dirt Dogs',            mgr:'Sam',     rank:8, record:'12-24', pts:2401, streak:'L4', logo:'DD' },
  { id:'t9', name:'Foul Pole Society',    mgr:'Tate',    rank:9, record:'11-25', pts:2356, streak:'L2', logo:'FP' },
  { id:'t10',name:'Bleacher Creatures',   mgr:'Jules',   rank:10,record:'10-26', pts:2298, streak:'W1', logo:'BC' },
  { id:'t11',name:'7th Inning Stretch',   mgr:'Kai',     rank:11,record:'9-27',  pts:2241, streak:'L1', logo:'7I' },
  { id:'t12',name:'The Designated Hitters',mgr:'Ren',    rank:12,record:'8-28',  pts:2189, streak:'L5', logo:'DH' },
];

// Position groups + my rank in the league for that position-group's points
const POSITION_GROUPS = [
  { pos:'C',  rankInLeague:6,  total:12, ptsRank:118.4, leagueAvg:124.2, bestPts:181.0 },
  { pos:'1B', rankInLeague:3,  total:12, ptsRank:212.6, leagueAvg:178.9, bestPts:241.4 },
  { pos:'2B', rankInLeague:9,  total:12, ptsRank:142.1, leagueAvg:166.5, bestPts:198.8 },
  { pos:'3B', rankInLeague:1,  total:12, ptsRank:248.3, leagueAvg:182.0, bestPts:248.3 },
  { pos:'SS', rankInLeague:4,  total:12, ptsRank:188.2, leagueAvg:180.4, bestPts:226.7 },
  { pos:'OF', rankInLeague:2,  total:12, ptsRank:592.0, leagueAvg:512.3, bestPts:618.4 },
  { pos:'UT', rankInLeague:7,  total:12, ptsRank:154.6, leagueAvg:162.0, bestPts:201.2 },
  { pos:'SP', rankInLeague:5,  total:12, ptsRank:412.8, leagueAvg:401.2, bestPts:498.6 },
  { pos:'RP', rankInLeague:8,  total:12, ptsRank:78.4,  leagueAvg:88.0,  bestPts:124.6 },
];

// Position card slot color states
// 'ok'    = in lineup, healthy (BLUE)
// 'bench' = on bench, healthy (GREEN)
// 'injured' = DTD/IL (RED)
// 'empty' = unfilled slot (GRAY)
function slotState(player) {
  if (!player) return 'empty';
  if (['il10','il60'].includes(player.status)) return 'injured';
  if (player.status === 'dtd') return 'injured';
  if (['BN'].includes(player.slot)) return 'bench';
  if (['IL'].includes(player.slot)) return 'injured';
  return 'ok';
}

// Free agents — with rationale + tradeoffs
const FREE_AGENTS = [
  {
    id:'fa1', name:'Roman Talavera', pos:'OF', team:'WSH', age:25,
    proj30:11.2, l30avg:10.4, vsExp:+3.8, trend:'hot', status:'ok',
    rosteredPct:54,
    why:'Hot bat (.328 over 14g) playing every day vs a soft week of RHPs. Plus matchups Mon/Wed/Fri.',
    tradeoffs:'Strikeout-prone; production may dip vs LHP. He blocks a future OF prospect call-up.',
    swap:{ id:'p8', name:'Wendell Park', why:'Park is sitting vs LHP today and cold over L14.' },
  },
  {
    id:'fa2', name:'Quintrell Hayes', pos:'2B', team:'PIT', age:23,
    proj30:8.9, l30avg:9.3, vsExp:+1.6, trend:'steady', status:'ok',
    rosteredPct:41,
    why:'Quietly hitting .295 with multi-hit games in 5 of last 7. Locked into 2-hole.',
    tradeoffs:'Low power ceiling. Brennan still has more upside if he snaps out of his slump.',
    swap:{ id:'p3', name:'Kai Brennan', why:'Brennan is 0-for-15 and DTD.' },
  },
  {
    id:'fa3', name:'Idris Foulkes', pos:'SP', team:'TEX', age:27,
    proj30:14.6, l30avg:13.2, vsExp:+2.9, trend:'hot', status:'ok',
    rosteredPct:38,
    why:'Two starts this week (KC, OAK). Both are bottom-5 offenses vs RHP. Easy +27 floor.',
    tradeoffs:'Walk rate creeping up. If you start him over Boscarino long-term you risk losing ratios.',
    swap:{ id:'p15', name:'Luca Boscarino', why:'Boscarino has a single tough start at BOS.' },
  },
  {
    id:'fa4', name:'Coen Albright', pos:'RP', team:'SF', age:29,
    proj30:5.1, l30avg:4.6, vsExp:+1.3, trend:'hot', status:'ok',
    rosteredPct:22,
    why:'Took over closer duties this week. 4 saves in 7 days possible with SF on a soft schedule.',
    tradeoffs:'Holds-only role could disappear if Doval returns from IL next Tuesday.',
    swap:{ id:'p19', name:'Tariq Bensalem', why:'Bensalem on IL-10, no return date.' },
  },
  {
    id:'fa5', name:'Bastian Werle', pos:'3B/SS', team:'CIN', age:26,
    proj30:9.8, l30avg:8.1, vsExp:+2.1, trend:'hot', status:'ok',
    rosteredPct:31,
    why:'Multi-position eligibility plus a .310/.380/.510 line over 30 days. Cincy bandbox helps.',
    tradeoffs:'Flexibility is the real value here — bench him on the road in pitcher-friendly parks.',
    swap:null,
  },
  {
    id:'fa6', name:'Mateo Vinhas', pos:'SP', team:'CLE', age:24,
    proj30:12.8, l30avg:11.4, vsExp:+1.9, trend:'steady', status:'ok',
    rosteredPct:28,
    why:'Rookie with a 2.94 ERA over last 4 starts. Cleveland defense pads his floor.',
    tradeoffs:'Innings limit looms — likely shut down mid-September. Dynasty buy, redraft caution.',
    swap:null,
  },
];

// Trade Grader — last 5 graded
const RECENT_GRADES = [
  { id:'g1', when:'2d ago', summary:'Reyna for Castellanos + Brookings', grade:'A−', accent:'good', taken:false },
  { id:'g2', when:'5d ago', summary:'Salas for Hartwell',                grade:'B',  accent:'good', taken:true },
  { id:'g3', when:'11d ago',summary:'Yost + Park for Niemann',           grade:'D',  accent:'bad',  taken:false },
];

// Sync status
const SYNC = {
  lastSyncMins: 12,
  cookieExpiresInDays: 22,
  cookieStatus: 'ok', // ok | warning | expired
  cronStatus: 'ok',
};

// Lineup deadline (UTC simulated)
const LINEUP_DEADLINE = { hrs:0, mins:47 }; // until first game today

Object.assign(window, {
  LEAGUE_TEAMS, POSITION_GROUPS, FREE_AGENTS, RECENT_GRADES, SYNC, LINEUP_DEADLINE, slotState,
});
