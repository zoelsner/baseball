"""Pure-computation core for the "lineup efficiency autopsy".

Given a team's daily roster rows (from a snapshot's ``all_team_rosters``) plus a
map of the fantasy points each player actually scored that day, we answer:

    "How many points did this manager leave on the bench by not fielding the
     hindsight-optimal lineup, within the *same* slot structure they used?"

Everything here is stdlib-only and side-effect-free so it can be unit-tested
without a DB, a network, or Fantrax auth. The caller (a report/route) is
responsible for pulling snapshots, resolving ``points_by_fid`` per date, and
threading a ``date`` key onto each ``team_day`` result before aggregation.

Important caveats baked into the rules below:

  * HINDSIGHT-optimal, not decision-time optimal. We know each player's actual
    score, so "points left" is an upper bound on what perfect foresight could
    have captured — it is NOT a claim the manager could have known this.
  * SAME-DAY injury reality. A player sitting in an injury slot (or carrying a
    blocking injury flag) could not have been legally activated that day, so
    they are excluded from the optimal pool. A benched (BN/RES) healthy player
    could have started. Dynasty MIN/MINORS assets stay protected and excluded.
"""

# --- Canonical slot sets -----------------------------------------------------
# Reserve slots hold healthy, roster-able players who simply were not started;
# they ARE activatable in hindsight so they belong to the optimal pool.
RESERVE_SLOTS = {"BN", "RES"}
# Dynasty-minors assets are not ordinary bench options. Promoting them can have
# irreversible roster/contract consequences, so analytics must never claim
# they were freely startable in the weekly hindsight lineup.
PROTECTED_SLOTS = {"MIN", "MINORS"}
# Injured slots (and OUT/SUSP/IR injury flags) mark players who could not have
# been legally fielded that day; excluded from the optimal pool entirely.
INJURED_SLOTS = {"IL", "IR", "IL10", "IL60", "INJ", "INJ RES"}
# Any eligibility token in this set marks a player as usable in a pitcher slot.
PITCHER_TOKENS = {"P", "SP", "RP"}
# Injury flags that disqualify a player from same-day activation.
BLOCKED_INJURIES = {"OUT", "SUSP", "IR", "IL", "IL10", "IL60"}
# Outfield sub-position tokens Fantrax rarely emits but which are OF-eligible.
_OF_SUBTOKENS = {"LF", "CF", "RF"}

# Safety caps for the exact DP (see optimal_points). Realistic per-team,
# per-side pools are ~13 players and ~10 slots, well under these.
_MAX_POOL_PER_SIDE = 20


# --- Eligibility -------------------------------------------------------------

def eligibility_tokens(row):
    """Return the set of position tokens a player is eligible for.

    Union of the comma-separated ``positions`` string and the ``all_positions``
    list. We deliberately do NOT use ``slot`` (today's assignment): a player
    benched at slot "BN" is still eligible for his real positions. Only if no
    real eligibility is present do we fall back to the slot token — and only
    when that slot is itself a position (not BN/IL/UT/etc.).
    """
    tokens = set()
    for tok in (row.get("positions") or "").split(","):
        tok = tok.strip().upper()
        if tok:
            tokens.add(tok)
    for tok in (row.get("all_positions") or []):
        if isinstance(tok, str):
            tok = tok.strip().upper()
            if tok:
                tokens.add(tok)
    if not tokens:
        slot = (row.get("slot") or "").strip().upper()
        # Only fall back if the slot names a real fielding position.
        if slot and slot not in RESERVE_SLOTS and slot not in PROTECTED_SLOTS and slot not in INJURED_SLOTS \
                and slot not in ("UT", "DH"):
            tokens.add(slot)
    return tokens


def _is_active_slot(slot):
    slot = (slot or "").strip().upper()
    return (
        bool(slot)
        and slot not in RESERVE_SLOTS
        and slot not in PROTECTED_SLOTS
        and slot not in INJURED_SLOTS
    )


def slot_template(rows):
    """The multiset (list) of active slot codes this team fielded that day.

    This is the observed lineup structure we hold fixed when computing the
    optimal: we are not inventing extra slots, only re-filling the ones the
    manager actually used.
    """
    return [(r.get("slot") or "").strip().upper()
            for r in rows if _is_active_slot(r.get("slot"))]


def actual_points(rows, points_by_fid):
    """Points scored by players whose slot was active that day."""
    total = 0.0
    for r in rows:
        if _is_active_slot(r.get("slot")):
            total += float(points_by_fid.get(r.get("id"), 0.0))
    return total


# --- Slot-fit logic ----------------------------------------------------------

def _has_hitter_token(tokens):
    """True if the player has any non-pitcher eligibility (i.e. is a hitter)."""
    return bool(tokens - PITCHER_TOKENS)


def _can_pitch(tokens):
    return bool(tokens & PITCHER_TOKENS)


def _fits(slot, tokens):
    """Does a player with ``tokens`` fit lineup slot ``slot``?"""
    slot = slot.strip().upper()
    if slot == "UT":
        # Utility hitter slot: any hitter qualifies; pure pitchers do not.
        return _has_hitter_token(tokens)
    if slot == "P":
        # Generic pitcher slot: any pitcher qualifies.
        return _can_pitch(tokens)
    if slot in ("SP", "RP"):
        # Fantrax requires the specific eligibility for SP/RP slots — a
        # closer can't be started in an SP slot.
        return slot in tokens
    if slot == "OF":
        return "OF" in tokens or bool(tokens & _OF_SUBTOKENS)
    return slot in tokens


def _is_pitcher_slot(slot):
    return slot.strip().upper() in PITCHER_TOKENS


# --- Exact max-weight assignment (bitmask DP) --------------------------------

def _max_assign(slots, players):
    """Exact maximum-weight assignment of players to a fixed list of slots.

    ``slots``   : list of slot code strings (order defines the bitmask bits).
    ``players`` : list of (value, name, tokens) tuples.

    Each slot takes at most one player; each player fills at most one slot.
    Returns (best_total, [(slot_code, name), ...]). Slots may be left unfilled
    (a 0-point filler never beats leaving it open, so this is harmless).

    DP state: dp[mask] = (best_value, assignment_list) where ``mask`` is the set
    of filled slot indices. We process players one at a time; for each we may
    skip them or drop them into one compatible open slot. Processing players in
    the outer loop (reading last round's dp) guarantees each player is used at
    most once. This is exact for the pool considered — no greedy heuristics.
    """
    n = len(slots)
    if n == 0 or not players:
        return 0.0, []
    # Cap the pool for safety; take the highest-scoring players. In practice a
    # team side has fewer players than this cap, so nothing meaningful is lost.
    players = sorted(players, key=lambda p: p[0], reverse=True)[:_MAX_POOL_PER_SIDE]

    # Precompute each player's bitmask of compatible slots.
    compat = []
    for value, name, tokens in players:
        mask = 0
        for i, slot in enumerate(slots):
            if _fits(slot, tokens):
                mask |= (1 << i)
        compat.append((value, name, mask))

    dp = {0: (0.0, [])}
    for value, name, cmask in compat:
        if cmask == 0:
            continue  # player fits nothing on this side; skip entirely.
        new_dp = dict(dp)
        for mask, (val, asg) in dp.items():
            open_slots = cmask & ~mask
            s = open_slots
            while s:
                bit = s & (-s)          # lowest open compatible slot bit
                s ^= bit
                i = bit.bit_length() - 1
                cand = val + value
                nmask = mask | bit
                if cand > new_dp.get(nmask, (float("-inf"), None))[0]:
                    new_dp[nmask] = (cand, asg + [(slots[i], name)])
        dp = new_dp

    best_val, best_asg = 0.0, []
    for val, asg in dp.values():
        if val > best_val:
            best_val, best_asg = val, asg
    return best_val, best_asg


def _candidate_pool(rows):
    """Rows eligible to be fielded in the hindsight-optimal lineup.

    Excludes players in IL/IR slots and players flagged OUT/SUSP/IR — they
    could not have been legally activated that day. Benched (BN/RES/MIN) and
    DTD players ARE included: DTD players frequently still play.
    """
    pool = []
    for r in rows:
        slot = (r.get("slot") or "").strip().upper()
        if slot in INJURED_SLOTS or slot in PROTECTED_SLOTS:
            continue
        injury = (r.get("injury") or "").strip().upper()
        if injury in BLOCKED_INJURIES:
            continue
        pool.append(r)
    return pool


def optimal_points(rows, template, points_by_fid):
    """Max points achievable by reassigning this team's players to ``template``.

    Returns (best_total, assignment) where assignment is [(slot, player_name)].

    We split the slot template into a hitter side and a pitcher side, since
    eligibility never crosses the two — EXCEPT two-way players (e.g. eligibility
    "OF,SP"), who could serve either side. We handle those by enumerating, for
    each two-way player, which side to place them on, running the exact DP per
    side for each combination, and keeping the best combined total. A two-way
    player is therefore never double-counted on both sides.
    """
    pool = _candidate_pool(rows)

    hit_slots = [s for s in template if not _is_pitcher_slot(s)]
    pit_slots = [s for s in template if _is_pitcher_slot(s)]

    hit_only, pit_only, two_way = [], [], []
    for r in pool:
        tokens = eligibility_tokens(r)
        value = float(points_by_fid.get(r.get("id"), 0.0))
        name = r.get("name") or r.get("id") or "?"
        entry = (value, name, tokens)
        can_hit = _has_hitter_token(tokens)
        can_pit = _can_pitch(tokens)
        if can_hit and can_pit:
            two_way.append(entry)
        elif can_pit:
            pit_only.append(entry)
        else:
            hit_only.append(entry)

    # Enumerate side assignments for two-way players: bit i -> hitter side.
    # (Placing a player on a side is not forcing them to start; the DP's skip
    # option means "unused" is always available.) Realistically two_way is tiny.
    best_total, best_asg = float("-inf"), []
    for combo in range(1 << len(two_way)):
        hside = list(hit_only)
        pside = list(pit_only)
        for i, entry in enumerate(two_way):
            if combo & (1 << i):
                hside.append(entry)
            else:
                pside.append(entry)
        hv, ha = _max_assign(hit_slots, hside)
        pv, pa = _max_assign(pit_slots, pside)
        if hv + pv > best_total:
            best_total, best_asg = hv + pv, ha + pa
    if best_total == float("-inf"):
        best_total = 0.0
    return best_total, best_asg


# --- Per-day and aggregate ---------------------------------------------------

def team_day(rows, points_by_fid):
    """One team's efficiency autopsy for a single day."""
    template = slot_template(rows)
    actual = actual_points(rows, points_by_fid)
    optimal, assignment = optimal_points(rows, template, points_by_fid)
    efficiency = (actual / optimal) if optimal > 0 else None
    return {
        "actual": actual,
        "optimal": optimal,
        "efficiency": efficiency,
        "points_left": optimal - actual,
        "assignment": assignment,
    }


def autopsy(team_days):
    """Aggregate a list of per-day results for one team.

    Each dict in ``team_days`` is a ``team_day`` result the caller has tagged
    with a "date" key. We sum actual/optimal, recompute a blended efficiency,
    and surface the three days that leaked the most points.
    """
    days = len(team_days)
    actual_total = sum(d["actual"] for d in team_days)
    optimal_total = sum(d["optimal"] for d in team_days)
    points_left_total = sum(d["points_left"] for d in team_days)
    efficiency = (actual_total / optimal_total) if optimal_total > 0 else None
    worst = sorted(team_days, key=lambda d: d["points_left"], reverse=True)[:3]
    worst_days = [{"date": d.get("date"), "points_left": d["points_left"],
                   "actual": d["actual"], "optimal": d["optimal"]}
                  for d in worst]
    return {
        "days": days,
        "actual_total": actual_total,
        "optimal_total": optimal_total,
        "efficiency": efficiency,
        "points_left_total": points_left_total,
        "points_left_per_day": (points_left_total / days) if days else 0.0,
        "worst_days": worst_days,
    }


def coverage(rows, points_by_fid, id_map_hit):
    """Data-quality signal for one team's day.

    Of this team's non-injured players (the pool the numbers actually depend
    on), what fraction have a daily-points entry, and what fraction have an id
    mapping (``id_map_hit`` is the set of fantrax ids we resolved)? Low coverage
    means the day's actual/optimal figures should be trusted less.
    """
    pool = _candidate_pool(rows)
    n = len(pool)
    with_points = sum(1 for r in pool if r.get("id") in points_by_fid)
    with_id = sum(1 for r in pool if r.get("id") in id_map_hit)
    return {
        "n_players": n,
        "with_points": with_points,
        "with_id": with_id,
        "points_coverage": (with_points / n) if n else None,
        "id_coverage": (with_id / n) if n else None,
    }
