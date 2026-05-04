# Fantrax Session Notes — 2026-05-03

> **After compact, read this file first.** This is a 12-team **dynasty/keeper** league. Optimize for 5-yr asset value, not 2026 wins.

---

## 🔴 CURRENT STATE — frame for everything below

- **Standings: 0-5-0, dead LAST, 4 GB, 848 PF.** Every other team is 1,400-1,800 PF.
- **Mode: REBUILD for 2027+.** Season is cooked.
- **Operating principle (Codex):** Yordan is NOT a liquidation asset. He's a 28yo cornerstone (1.123 OPS in 2026). **Sell only into a real overpay. Don't dump. User has wavered emotionally on this multiple times — anchor against panic-selling.**
- ⚠️ **Old notes said "6th place, 1 GB" — that was wrong/stale. Trust the standings above.**

---

## 🔥 ACTIVE NEGOTIATIONS

### 1. Jimmybromley (LIVE — he's typing his counter RIGHT NOW)
*Counterparty = combined Jimmy + Peter Bromley account.*
- **State:** User asked for "hitter + 1 current pitcher + another young one." Jimmy: "I definitely do" + "Easily." Mid-typing.
- **Floor to accept:** Yordan straight up for **Skubal + Walker Jenkins + young SP** (Sasaki, Cade Horton, or similar)
- **Do NOT include Alonso** in your side — 38 HR / .871 OPS in 2025 = real value, not a throw-in
- **History:** Original offer was Skubal + Lazaro Montes for Yordan + Alonso. User almost accepted; Codex pushed back. **Hard pass on that original offer.**
- **→ NEXT:** Wait for counter. Grade against floor. Reject anything that drops Walker Jenkins or includes Alonso.

### 2. MLB's Top 100 (LIVE — counter pending)
- **Their offer:** PCA + Jonah Tong + Bubba Chandler for Yordan
- **Their concession on table:** "I'd do a Hitting Prospect instead of Tong or Chandler if you prefer"
- **→ NEXT:** Counter — swap Tong for **Jackson Holliday**. If no Holliday, fall back to Walcott (top-15 SS) → Jesus Made (top-25 SS) → Chourio (IR, buy-low)
- If Holliday lands → A-tier deal, equal to PerpetualRob

### 3. PerpetualRob (NOT YET SENT — Codex's #1 ranked deal)
- **Send:** Yordan + Christian Walker for Roman Anthony + Andrew Painter + Eury Perez
- Roman Anthony = top-3 prospect in baseball, MLB-imminent. Highest ceiling of any package.
- ⚠️ **Research first (60 sec):** Google "Roman Anthony injury status." Healthy → fire DM. Injured → skip to ssnider21.
- **→ NEXT:** Research Anthony, then send.

### 4. ssnider21 (NOT YET SENT — backup if PerpetualRob dies)
- **Send:** Yordan + Christian Walker for Leo De Vries (top-5 prospect) + Joe Ryan + Bryce Eldridge
- **→ NEXT:** Send only if PerpetualRob falls through.

### 5. House of Hades (NOT YET SENT — last resort)
- **Send:** Yordan + Reid Detmers for George Kirby + Eli Willits + Owen Caissie
- Worst 5-yr value of the 5. Use only if all others die.

---

## 🚫 OFF-LIMITS (never include in any package)

Brooks Lee (25yo dynasty), Daylen Lile (23yo OF), Davis Martin, Kevin Gausman, Spencer Strider (IR ace), Aaron Judge.

*(Christian Walker IS tradeable — he's in two of the packages above as the secondary piece.)*

---

## 📋 KEY ROSTER ANCHORS

**Hitters:** Yordan (4.62, 28yo — TRADE BAIT), Judge (3.95), Walker (3.59, in 2 packages), Buxton (3.07), Brooks Lee (2.43, 25yo)
**Pitchers:** Davis Martin (17.67), Gausman (14.86), Ritchie (14.50), McGreevy (13.71), Burke (13.29), Strider (IR)
**Hold:** Daylen Lile (23yo OF)

*(For full rosters of all 12 teams, see cached JSON below or re-pull via fantrax_data.py.)*

---

## 💡 CODEX CORRECTIONS (these overrode user's initial instincts)

- **Sasaki = name value, not current value.** 5.97 ERA, shoulder history. Risky throw-in, not a pillar.
- **Skubal is the best single piece in any deal**, but he's a win-now anchor, not a classic rebuild centerpiece (Anthony/De Vries fit a rebuild better).
- "More pieces" can be fake depth — one volatile arm + one 2027 prospect ≠ value.

---

## 🎯 ORDER OF OPERATIONS

1. **NOW:** Wait for Jimmy counter, send MLB Top 100 Holliday counter
2. **In next 10 min:** Google Roman Anthony health, fire PerpetualRob DM if clean
3. **Today:** ssnider21 if PerpetualRob dies
4. **Last resort:** House of Hades

---

## 🛠️ TECH NOTES

**Cached data this session (regenerate via fantrax_data.py if needed):**
- `/tmp/fantrax-trade-analysis/rosters.json` — full league (5.9MB)
- `/tmp/fantrax-trade-analysis/rosters-slim.json` — me + Jimmy + Bani + MLB Top 100
- `/tmp/fantrax-trade-analysis/contenders-slim.json` — 5 contender rosters

JSON schema: `standings.records[]` (rank, win, loss, points_for, games_back, streak); per-team `players[]` (name, team, positions, slot, fpts, fppg, injury, age).

**Repo:** `/Users/zach/Projects/fantrax-daily-audit` — see README.md for script invocation.
