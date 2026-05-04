// Shared fake data for the fantasy baseball app
// Made-up players & realistic-looking stats. H2H Points league, no salaries.

const TEAM_NAME = "Sandlot Syndicate";
const LEAGUE_NAME = "Backyard Dynasty";
const OPP_NAME = "Curveball Cartel";
const TODAY = "Sun, May 3";

// status: ok | dtd | il10 | il60
// trend: hot | cold | steady
// vsExp: number — points above/below expectation (0.0 = on track)
// alert: null | { kind: 'benched-but-good' | 'opp-pitcher-tough' | 'cold-streak', msg, suggest }
const ROSTER = [
  // ── Hitters ─────────────────────────────────────────────
  { id:'p1',  name:'Ezra Cordero',     pos:'C',   team:'TEX', opp:'@SEA', mlbStarting:true,  status:'ok',  trend:'hot',    vsExp:+3.2,  proj:8.4,  last7:64,  l30avg:9.1, slot:'C',   alert:null },
  { id:'p2',  name:'Marcus Vela',      pos:'1B',  team:'CHC', opp:'vs MIL', mlbStarting:true, status:'ok',  trend:'steady', vsExp:+0.4, proj:11.2, last7:71,  l30avg:10.6, slot:'1B',  alert:null },
  { id:'p3',  name:'Kai Brennan',      pos:'2B',  team:'NYM', opp:'@PHI', mlbStarting:true,  status:'dtd', trend:'cold',   vsExp:-2.8, proj:6.1,  last7:38,  l30avg:7.8,  slot:'2B',
    alert:{ kind:'cold-streak', msg:'On a 0-for-15 stretch and listed day-to-day.', suggest:{ id:'p10', name:'Drew Halverson', why:'+3.5 proj vs lefty starter' } } },
  { id:'p4',  name:'Tobias Reyna',     pos:'3B',  team:'ATL', opp:'vs MIA', mlbStarting:true, status:'ok',  trend:'hot',    vsExp:+5.1, proj:12.8, last7:88,  l30avg:11.4, slot:'3B',  alert:null },
  { id:'p5',  name:'Soren Maddox',     pos:'SS',  team:'BAL', opp:'@TOR', mlbStarting:true,  status:'ok',  trend:'steady', vsExp:+0.9, proj:10.0, last7:62,  l30avg:9.6,  slot:'SS',  alert:null },
  { id:'p6',  name:'Renny Okafor',     pos:'OF',  team:'LAD', opp:'vs SF', mlbStarting:true,  status:'ok',  trend:'hot',    vsExp:+4.7, proj:13.5, last7:91,  l30avg:11.9, slot:'OF',  alert:null },
  { id:'p7',  name:'Joaquin Salas',    pos:'OF',  team:'HOU', opp:'@OAK', mlbStarting:true,  status:'ok',  trend:'steady', vsExp:+1.1, proj:10.8, last7:68,  l30avg:10.2, slot:'OF',  alert:null },
  { id:'p8',  name:'Wendell Park',     pos:'OF',  team:'BOS', opp:'vs TB',  mlbStarting:false, status:'ok',  trend:'cold',   vsExp:-1.4, proj:7.9,  last7:44,  l30avg:8.6,  slot:'OF',
    alert:{ kind:'opp-pitcher-tough', msg:'Sitting vs LHP today; not in MLB lineup.', suggest:{ id:'p11', name:'Hideo Tamura', why:'Confirmed leadoff vs RHP' } } },
  { id:'p9',  name:'Beau Lindgren',    pos:'UT',  team:'SD',  opp:'vs ARI', mlbStarting:true, status:'ok',  trend:'steady', vsExp:+0.2, proj:9.4,  last7:58,  l30avg:9.2,  slot:'UT',  alert:null },

  // Bench hitters
  { id:'p10', name:'Drew Halverson',   pos:'2B',  team:'KC',  opp:'vs DET', mlbStarting:true, status:'ok',  trend:'hot',    vsExp:+3.5, proj:9.6,  last7:74,  l30avg:8.9,  slot:'BN',  alert:null },
  { id:'p11', name:'Hideo Tamura',     pos:'OF',  team:'SEA', opp:'vs TEX', mlbStarting:true, status:'ok',  trend:'hot',    vsExp:+2.6, proj:11.4, last7:69,  l30avg:9.7,  slot:'BN',  alert:null },
  { id:'p12', name:'Quinn Mathers',    pos:'1B/3B', team:'STL', opp:'OFF',  mlbStarting:false, status:'ok',  trend:'steady', vsExp:+0.0, proj:0.0,  last7:42,  l30avg:8.1,  slot:'BN',  alert:null },
  { id:'p13', name:'Sully Pemberton',  pos:'SS',  team:'MIN', opp:'@CWS',  mlbStarting:false, status:'dtd', trend:'cold',   vsExp:-1.9, proj:5.4,  last7:31,  l30avg:7.3,  slot:'BN',  alert:null },

  // ── Pitchers ────────────────────────────────────────────
  { id:'p14', name:'Caleb Yost',       pos:'SP',  team:'PHI', opp:'vs NYM', mlbStarting:true, status:'ok',  trend:'hot',    vsExp:+6.1, proj:18.4, last7:42,  l30avg:16.8, slot:'SP',  alert:null,  probable:true },
  { id:'p15', name:'Luca Boscarino',   pos:'SP',  team:'TB',  opp:'@BOS',   mlbStarting:false, status:'ok',  trend:'steady', vsExp:+0.0, proj:0.0,  last7:0,   l30avg:14.2, slot:'SP',  alert:{ kind:'not-pitching', msg:'Not on the mound today.', suggest:{ id:'p18', name:'Pavel Krenz', why:'Probable starter, +14.2 proj' } }, probable:false },
  { id:'p16', name:'Marquez Daley',    pos:'SP',  team:'MIL', opp:'@CHC',   mlbStarting:true, status:'ok',  trend:'cold',   vsExp:-3.2, proj:11.8, last7:8,   l30avg:13.1, slot:'SP',  alert:null,  probable:true },
  { id:'p17', name:'Anders Vögel',     pos:'RP',  team:'CLE', opp:'vs DET', mlbStarting:true, status:'ok',  trend:'steady', vsExp:+1.0, proj:4.2,  last7:11,  l30avg:3.8,  slot:'RP',  alert:null,  probable:true },
  { id:'p18', name:'Pavel Krenz',      pos:'SP',  team:'AZ',  opp:'@SD',    mlbStarting:true, status:'ok',  trend:'hot',    vsExp:+2.4, proj:14.2, last7:31,  l30avg:12.4, slot:'BN',  alert:null,  probable:true },
  { id:'p19', name:'Tariq Bensalem',   pos:'RP',  team:'TOR', opp:'vs BAL', mlbStarting:false, status:'il10', trend:'cold',   vsExp:-0.8, proj:0,    last7:0,   l30avg:2.9,  slot:'IL',  alert:null },
  { id:'p20', name:'Jasper Whitlock',  pos:'SP',  team:'COL', opp:'OFF',    mlbStarting:false, status:'il60', trend:'cold',   vsExp:-2.0, proj:0,    last7:0,   l30avg:0,    slot:'IL',  alert:null },
];

// Lineup positions for "starting" view (H2H points, daily setup)
const LINEUP_SLOTS = ['C','1B','2B','3B','SS','OF','OF','OF','UT','SP','SP','SP','RP','RP'];

// Aggregate alerts on bench/IL too (e.g. better option exists)
const BENCH_INSIGHTS = [
  { playerId:'p10', msg:'Hot bench bat with a great matchup. Consider starting at 2B.', vs:'p3' },
  { playerId:'p11', msg:'Confirmed in lineup vs RHP — better than your current OF3.', vs:'p8' },
  { playerId:'p18', msg:'Probable starter today. Boscarino is not pitching.', vs:'p15' },
];

// Standings / matchup
const MATCHUP = {
  week: 6,
  myScore: 412.6,
  oppScore: 388.1,
  myRemaining: 11,
  oppRemaining: 14,
  myProj: 612.4,
  oppProj: 598.7,
  daysLeft: 3,
};

const STANDINGS = [
  { rank:1, team:'Curveball Cartel', record:'24-12', pts:2841 },
  { rank:2, team:'Sandlot Syndicate', record:'22-14', pts:2789, me:true },
  { rank:3, team:'Bullpen Brigade', record:'21-15', pts:2742 },
  { rank:4, team:'Sliders Anonymous', record:'20-16', pts:2698 },
  { rank:5, team:'Triple Threat', record:'18-18', pts:2611 },
  { rank:6, team:'The Rally Caps', record:'17-19', pts:2588 },
  { rank:7, team:'Fastball Fanatics', record:'15-21', pts:2502 },
  { rank:8, team:'Dirt Dogs', record:'12-24', pts:2401 },
];

// Trade scenario
const TRADE = {
  give: [{ id:'p4', name:'Tobias Reyna', pos:'3B', proj30:11.4, age:28 }],
  get:  [{ id:'x1', name:'Niko Castellanos', pos:'OF', proj30:12.6, age:24 },
         { id:'x2', name:'Rell Brookings', pos:'SP', proj30:13.9, age:26 }],
  myDelta: +2.1,    // weekly proj points
  oppDelta: -0.4,
  fairness: 0.74,   // 0..1 — model's fairness score
  ai: "You give up an in-prime 3B for a younger OF and a mid-rotation SP. Net weekly +2.1 pts and you get younger at two spots. Worth it for dynasty if you trust Castellanos' contact rate.",
};

// AI chat seed messages
const AI_SEED = [
  { role:'user', text:'Should I start Kai Brennan today?' },
  { role:'ai', text:"I'd bench him. He's 0-for-15, listed day-to-day, and faces a tough RHP in Philly. Drew Halverson is hot and has a plus matchup vs a lefty in Detroit. Swap saves you ~3.5 projected points." },
  { role:'user', text:'And my pitching?' },
  { role:'ai', text:"Caleb Yost is your stud — keep starting. Boscarino isn't pitching today, so move Pavel Krenz into the SP slot (he's probable in San Diego, +14.2 proj). Marquez Daley has been cold but his ratios are still good — I'd ride it out." },
];

const SUGGESTED_PROMPTS = [
  "Who should I start at SS today?",
  "Why is my team underperforming?",
  "Compare Tobias Reyna vs Niko Castellanos",
  "Who's the best add off waivers right now?",
  "Roast my lineup",
];

// ── exports ────────────────────────────────────────────────
Object.assign(window, {
  TEAM_NAME, LEAGUE_NAME, OPP_NAME, TODAY,
  ROSTER, LINEUP_SLOTS, BENCH_INSIGHTS,
  MATCHUP, STANDINGS, TRADE,
  AI_SEED, SUGGESTED_PROMPTS,
});
