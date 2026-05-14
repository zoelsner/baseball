"""Admin CLI for reviewing Sandlot matchup projection calibration."""

from __future__ import annotations

import argparse
import json
from typing import Any

import sandlot_db
import sandlot_matchup


def build_report(*, limit: int | None = None) -> dict[str, Any]:
    sandlot_db.init_schema()
    rows = sandlot_db.list_projection_logs_for_evaluation(limit=limit)
    return sandlot_matchup.calibration_report(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print projection calibration metrics from projection_logs.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum evaluated log rows to include.")
    args = parser.parse_args(argv)
    print(json.dumps(build_report(limit=args.limit), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
