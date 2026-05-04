"""Railway cron entrypoint for Sandlot.

Railway cron services should do one unit of work and exit. This script shares
the exact same refresh path as POST /api/refresh.
"""

from __future__ import annotations

import logging
import sys

from sandlot_refresh import run_refresh


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_refresh(source="cron")
    if result.ok:
        logging.info("Sandlot cron refresh stored snapshot_id=%s in %sms", result.snapshot_id, result.duration_ms)
        return 0
    logging.error("Sandlot cron refresh failed: %s", "; ".join(result.errors))
    return 1


if __name__ == "__main__":
    sys.exit(main())
