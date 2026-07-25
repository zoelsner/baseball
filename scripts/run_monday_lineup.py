"""Propose the optimal Monday lineup for the coming scoring week.

Thin CLI wrapper over sandlot_lineup_card.build_card() — the same compute
the Railway cron stores for the app's Today page. This entry point exists
for the weekly GitHub Actions run: it prints the card, writes it to the
step summary, and leaves monday_lineup.{json,md} for artifacts/email.

Usage: DATABASE_URL=postgres://... python scripts/run_monday_lineup.py
Read-only against the database.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sandlot_lineup_card as card_mod  # noqa: E402


def run():
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL is not set (is the repository secret configured?)")
    try:
        card = card_mod.build_card()
    except RuntimeError as exc:
        sys.exit(str(exc))

    summary = card_mod.render_markdown(card)
    print("\n" + summary)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    with open("monday_lineup.md", "w", encoding="utf-8") as fh:
        fh.write(summary + "\n")
    with open("monday_lineup.json", "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=1, default=str)
    print("\nfull detail written to monday_lineup.json / monday_lineup.md")


if __name__ == "__main__":
    run()
