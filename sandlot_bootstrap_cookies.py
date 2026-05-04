"""Seed Railway Postgres with the locally captured Fantrax cookies.

Run after a successful local Selenium login has created .cookies/fantrax.json,
with DATABASE_URL pointed at the Railway Postgres database.
"""

from __future__ import annotations

import json
import logging

from dotenv import load_dotenv

import auth
import sandlot_db


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not auth.COOKIE_PATH.exists():
        raise SystemExit(f"No cookie file found at {auth.COOKIE_PATH}. Run the local audit once first.")

    cookies = json.loads(auth.COOKIE_PATH.read_text())
    if not isinstance(cookies, list) or not cookies:
        raise SystemExit(f"{auth.COOKIE_PATH} does not contain a non-empty cookie array.")

    sandlot_db.init_schema()
    sandlot_db.upsert_fantrax_cookies(cookies, source="local-bootstrap")
    logging.info("Stored %d Fantrax cookies in Postgres fantrax_sessions.", len(cookies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
