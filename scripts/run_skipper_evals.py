"""Skipper model evals: which cheap model is good enough?

Runs a fixed suite of Skipper tasks against candidate OpenRouter models,
using the PRODUCTION prompt pipeline (sandlot_skipper.build_context /
build_messages) with the latest real snapshot as context. Grading is
deterministic — the snapshot is ground truth, so no judge model is needed:

  matchup_read   — names the right opponent; quoted numbers are groundable
  start_sit      — picks the objectively better player (big FP/G gap pair)
  fake_player    — admits a made-up player isn't in the data (no fabrication)
  win_prob       — quotes the deterministic win probability correctly
  one_sentence   — follows a strict brevity instruction, names a real player
  bench_scan     — reads the roster table correctly (top bench hitters)

Each task runs twice per model at production temperature. Output: pass
rates, first-token + total latency, estimated cost per 1k replies, and the
cheapest-acceptable-model verdict.

Usage: DATABASE_URL=... OPENROUTER_API_KEY=... python scripts/run_skipper_evals.py
Optional: SANDLOT_EVAL_MODELS="a,b,c" to override the candidate list.
Candidates missing from OpenRouter's catalog are skipped with a note, so a
stale default id can't break the run. Read-only against the database.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402
import requests  # noqa: E402

import mlb_stats  # noqa: E402
import sandlot_skipper as sk  # noqa: E402

DEFAULT_MODELS = [
    "deepseek/deepseek-v4-flash",  # current primary
    "moonshotai/kimi-k2",          # current fallback
    "deepseek/deepseek-chat",
    "z-ai/glm-5.2",                # in-app deeper-analysis option
    "z-ai/glm-4.6",
    "qwen/qwen3-32b",
    "anthropic/claude-haiku-4.5",  # quality/cost anchor
]
REPEATS = 2
PASS_THRESHOLD = 0.85

_norm = mlb_stats._normalize


# --- fixture helpers ---------------------------------------------------------

def _rows(snapshot):
    return (snapshot.get("roster") or {}).get("rows") or []


def _is_hitter(r):
    toks = {t.strip().upper() for t in (r.get("positions") or "").split(",") if t.strip()}
    return bool(toks - {"P", "SP", "RP"})


def _fppg(r):
    try:
        return float(r.get("fppg"))
    except (TypeError, ValueError):
        return None


def start_sit_pair(snapshot):
    """(better, worse) same-side pair with an unambiguous FP/G gap."""
    active, bench = [], []
    for r in _rows(snapshot):
        if not _is_hitter(r) or _fppg(r) is None:
            continue
        slot = (r.get("slot") or "").upper()
        if slot in ("BN", "RES"):
            bench.append(r)
        elif slot not in ("IL", "IR", "MIN"):
            active.append(r)
    best = None
    for a in active:
        for b in bench:
            gap = abs(_fppg(a) - _fppg(b))
            if best is None or gap > best[0]:
                best = (gap, a, b)
    if not best or best[0] < 1.5:
        return None
    _, a, b = best
    return (a, b) if _fppg(a) >= _fppg(b) else (b, a)


def bench_hitter_truth(snapshot, n=3):
    bench = [r for r in _rows(snapshot)
             if (r.get("slot") or "").upper() in ("BN", "RES")
             and _is_hitter(r) and _fppg(r) is not None]
    bench.sort(key=_fppg, reverse=True)
    return [r["name"] for r in bench[:n]]


def grounded_numbers(snapshot, extras):
    """Every number a truthful reply could reasonably quote."""
    allowed = set()

    def add(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        for x in (f, round(f), round(f, 1), abs(f), round(abs(f), 1), round(abs(f))):
            allowed.add(round(float(x), 1))

    m = snapshot.get("matchup") or {}
    for k in ("my_score", "opponent_score", "margin", "period_number", "days"):
        add(m.get(k))
    try:
        add(abs(float(m.get("my_score")) - float(m.get("opponent_score"))))
    except (TypeError, ValueError):
        pass
    rec = (snapshot.get("standings") or {}).get("my_record") or {}
    for k in ("win", "loss", "tie", "rank", "games_back"):
        add(rec.get(k))
    wp = (extras or {}).get("win_probability") or {}
    for k, v in (wp.items() if isinstance(wp, dict) else []):
        add(v)
        try:
            add(float(v) * 100)
        except (TypeError, ValueError):
            pass
    for team in [snapshot.get("roster") or {}] + list((snapshot.get("all_team_rosters") or {}).values()):
        for r in team.get("rows") or []:
            add(r.get("fppg"))
            add(r.get("fpts"))
    return allowed


NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers_groundable(reply, allowed, min_frac=0.7):
    nums = [round(float(x), 1) for x in NUM_RE.findall(reply)]
    checked = [n for n in nums if n > 3]  # small counts ("2 starts") are noise
    if not checked:
        return True, "no substantive numbers"
    ok = sum(1 for n in checked
             if any(abs(n - a) <= 0.51 for a in allowed))
    frac = ok / len(checked)
    return frac >= min_frac, f"{ok}/{len(checked)} numbers grounded"


def mentions(reply, name):
    return _norm(name) in _norm(reply)


ADMIT_RE = re.compile(
    r"not (?:in|on|part of|seeing|find)|don'?t (?:have|see)|no (?:data|record|info)"
    r"|isn'?t (?:in|on)|couldn'?t find|not appear|unfamiliar|not a player"
    r"|doesn'?t (?:appear|show|exist|ring)", re.I)


# --- task suite ---------------------------------------------------------------

def build_tasks(snapshot, extras):
    tasks = []
    allowed = grounded_numbers(snapshot, extras)
    opp = (snapshot.get("matchup") or {}).get("opponent_team_name")

    def matchup_grade(reply):
        checks = [(bool(reply.strip()) and not sk.is_broken_reply(reply), "non-broken")]
        if opp:
            checks.append((mentions(reply, opp), f"names opponent {opp}"))
        ok, note = numbers_groundable(reply, allowed)
        checks.append((ok, note))
        return checks
    tasks.append(("matchup_read", "How's my matchup looking this week?", matchup_grade))

    pair = start_sit_pair(snapshot)
    if pair:
        better, worse = pair
        prompt = (f"Should I start {better['name']} or {worse['name']} this week? "
                  "First line: only the name you'd start. Then one short sentence why.")

        def start_sit_grade(reply, better=better, worse=worse):
            first = (reply.strip().splitlines() or [""])[0]
            picked_better = mentions(first, better["name"])
            picked_worse = mentions(first, worse["name"])
            return [(picked_better and not picked_worse,
                     f"picked {better['name']} (fp/g {better.get('fppg')} vs {worse.get('fppg')})")]
        tasks.append(("start_sit", prompt, start_sit_grade))

    def fake_grade(reply):
        return [(bool(ADMIT_RE.search(reply)), "admits player not in data")]
    tasks.append(("fake_player",
                  "What do you think about trading for Tobias Reyna? Is he worth it?",
                  fake_grade))

    wp = (extras or {}).get("win_probability")
    wp_val = None
    if isinstance(wp, dict):
        for k in ("win_probability", "probability", "p_win", "mine"):
            if isinstance(wp.get(k), (int, float)):
                wp_val = float(wp[k])
                break
    if wp_val is not None:
        pct = wp_val * 100 if wp_val <= 1 else wp_val

        def wp_grade(reply, pct=pct):
            nums = [float(x) for x in NUM_RE.findall(reply)]
            hit = any(abs(n - pct) <= 1.0 or abs(n * 100 - pct) <= 1.0 for n in nums)
            return [(hit, f"quotes win prob ~{pct:.0f}%"),
                    (len(reply.split()) <= 90, "stays brief")]
        tasks.append(("win_prob",
                      "What's my win probability this week? Keep it under 60 words.",
                      wp_grade))

    my_names = [r["name"] for r in _rows(snapshot) if r.get("name")]

    def one_sentence_grade(reply):
        return [(reply.count(".") <= 3 and len(reply.split()) <= 45, "one-ish sentence"),
                (any(mentions(reply, n) for n in my_names), "names a rostered player")]
    tasks.append(("one_sentence",
                  "In exactly one sentence: who is the best hitter on my roster right now?",
                  one_sentence_grade))

    truth = bench_hitter_truth(snapshot)
    if len(truth) == 3:
        def bench_grade(reply, truth=truth, my_names=my_names):
            hits = sum(1 for n in truth if mentions(reply, n))
            named = [n for n in my_names if mentions(reply, n)]
            only_mine = len(named) >= 1
            return [(hits >= 2, f"{hits}/3 of true top bench hitters"),
                    (only_mine, "names come from my roster")]
        tasks.append(("bench_scan",
                      "List my 3 highest FP/G bench hitters. Names only, comma separated.",
                      bench_grade))
    return tasks


# --- runner -------------------------------------------------------------------

def openrouter_catalog(key):
    resp = requests.get("https://openrouter.ai/api/v1/models",
                        headers={"Authorization": f"Bearer {key}"}, timeout=20)
    resp.raise_for_status()
    out = {}
    for m in resp.json().get("data") or []:
        pricing = m.get("pricing") or {}
        out[m.get("id")] = (float(pricing.get("prompt") or 0),
                            float(pricing.get("completion") or 0))
    return out


def run_case(client, model, messages):
    t0 = time.monotonic()
    first_tok = None
    buf = []
    for kind, text in client.stream(messages, model_order=(model,)):
        if kind == "token":
            if first_tok is None:
                first_tok = time.monotonic() - t0
            buf.append(text)
    return "".join(buf), first_tok or 0.0, time.monotonic() - t0


def run():
    key = os.environ.get("OPENROUTER_API_KEY")
    dsn = os.environ.get("DATABASE_URL")
    if not key or not dsn:
        sys.exit("OPENROUTER_API_KEY and DATABASE_URL are both required")

    with psycopg.connect(dsn, connect_timeout=20) as conn:
        conn.read_only = True
        row = conn.execute(
            "SELECT id, data FROM snapshots WHERE status='success' "
            "ORDER BY taken_at DESC LIMIT 1").fetchone()
    if not row:
        sys.exit("No successful snapshot")
    snap_id, snapshot = row
    # The deployed pipeline embeds its deterministic projection under
    # matchup.projection; expose it to the graders as the win-prob source.
    projection = (snapshot.get("matchup") or {}).get("projection")
    extras = {"win_probability": projection if isinstance(projection, dict) else None}

    tasks = build_tasks(snapshot, extras)
    print(f"snapshot {snap_id}: {len(tasks)} tasks x {REPEATS} repeats")

    # Prompts the deterministic layer answers for free never reach a model.
    live_tasks = []
    for name, prompt, grade in tasks:
        if sk.deterministic_reply(prompt, snapshot):
            print(f"  {name}: handled by deterministic_reply — $0, excluded from model eval")
        else:
            live_tasks.append((name, prompt, grade))

    catalog = openrouter_catalog(key)
    wanted = [m.strip() for m in
              os.environ.get("SANDLOT_EVAL_MODELS", ",".join(DEFAULT_MODELS)).split(",")
              if m.strip()]
    models = [m for m in wanted if m in catalog]
    for m in wanted:
        if m not in catalog:
            print(f"  skipping {m}: not in OpenRouter catalog")

    client = sk.SkipperClient()
    results = {}
    for model in models:
        rows_out, ftl, total_l, cost = [], [], [], 0.0
        p_in, p_out = catalog[model]
        for name, prompt, grade in live_tasks:
            tier = sk.detect_tier(prompt, snapshot)
            context = sk.build_context(tier, snapshot, prompt=prompt)
            messages = sk.build_messages([], prompt, context)
            prompt_chars = sum(len(m["content"]) for m in messages)
            for _ in range(REPEATS):
                try:
                    reply, ft, tt = run_case(client, model, messages)
                except Exception as exc:  # noqa: BLE001
                    rows_out.append((name, False, f"error: {exc}"))
                    continue
                checks = grade(reply)
                passed = all(ok for ok, _ in checks)
                note = "; ".join(n for ok, n in checks if not ok) or "ok"
                rows_out.append((name, passed, note))
                ftl.append(ft)
                total_l.append(tt)
                cost += (prompt_chars / 4) * p_in + (len(reply) / 4) * p_out
        n = len(rows_out)
        results[model] = {
            "cases": rows_out,
            "pass_rate": sum(1 for _, ok, _ in rows_out if ok) / n if n else 0.0,
            "by_task": {
                t: (sum(1 for nm, ok, _ in rows_out if nm == t and ok),
                    sum(1 for nm, _, _ in rows_out if nm == t))
                for t in {nm for nm, _, _ in rows_out}},
            "first_token_s": round(median(ftl), 2) if ftl else None,
            "total_s": round(median(total_l), 2) if total_l else None,
            "cost_per_1k": round(cost / max(1, n) * 1000, 2),
        }
        print(f"  {model}: {results[model]['pass_rate']:.0%} pass, "
              f"ft {results[model]['first_token_s']}s, ~${results[model]['cost_per_1k']}/1k")

    ranked = sorted(results.items(), key=lambda kv: (-kv[1]["pass_rate"], kv[1]["cost_per_1k"]))
    lines = [
        f"# Skipper model evals — snapshot {snap_id}, {len(live_tasks)} tasks x {REPEATS}",
        "",
        "| Model | Pass | First token | Total | ~$/1k replies | Tasks failed |",
        "|-------|-----:|------------:|------:|--------------:|--------------|",
    ]
    for model, r in ranked:
        fails = [f"{t} {ok}/{n}" for t, (ok, n) in sorted(r["by_task"].items()) if ok < n]
        lines.append(f"| {model} | {r['pass_rate']:.0%} | {r['first_token_s']}s "
                     f"| {r['total_s']}s | ${r['cost_per_1k']} | {', '.join(fails) or '—'} |")
    acceptable = [(m, r) for m, r in results.items() if r["pass_rate"] >= PASS_THRESHOLD]
    if acceptable:
        cheapest = min(acceptable, key=lambda kv: kv[1]["cost_per_1k"])
        lines += ["", f"**Verdict: cheapest model ≥{PASS_THRESHOLD:.0%} pass is "
                  f"`{cheapest[0]}` (~${cheapest[1]['cost_per_1k']}/1k replies).**"]
    else:
        lines += ["", f"**No model reached {PASS_THRESHOLD:.0%} — review per-case notes "
                  "in the artifact before trusting any of them further.**"]
    summary = "\n".join(lines)
    print("\n" + summary)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    with open("skipper_evals.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, default=str)
    print("\nfull per-case detail written to skipper_evals.json")


if __name__ == "__main__":
    run()
