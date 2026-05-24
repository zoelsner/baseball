"""Railway cron entrypoint for Sandlot.

Railway cron services should do one unit of work and exit. This script shares
the exact same refresh path as POST /api/refresh.
"""

from __future__ import annotations

import logging
import os
import sys

import player_service
import sandlot_config
import sandlot_waivers
from sandlot_refresh import run_refresh


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_refresh(source="cron")
    if result.ok:
        logging.info("Sandlot cron refresh stored snapshot_id=%s in %sms", result.snapshot_id, result.duration_ms)
        if sandlot_config.profile_warm_enabled():
            warm_result = player_service.warm_roster_profiles(
                snapshot_id=result.snapshot_id,
                generate_takes=os.environ.get("SANDLOT_PROFILE_WARM_TAKES") == "1",
            )
            logging.info("Sandlot profile warm result: %s", warm_result)
        else:
            logging.info("Sandlot profile warm skipped; set SANDLOT_PROFILE_WARM_ENABLED=1 to enable")
        if sandlot_config.waiver_ai_warm_enabled():
            waiver_result = sandlot_waivers.warm_latest_waiver_ai(snapshot_id=result.snapshot_id)
            logging.info("Sandlot waiver AI warm result: %s", waiver_result)
        else:
            logging.info("Sandlot waiver AI warm skipped; set SANDLOT_WAIVER_AI_WARM_ENABLED=1 to enable")
        return 0
    if result.status == "skipped":
        logging.info("Sandlot cron refresh skipped: %s", "; ".join(result.errors))
        return 0
    logging.error("Sandlot cron refresh failed: %s", "; ".join(result.errors))
    return 1


if __name__ == "__main__":
    sys.exit(main())
