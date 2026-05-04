"""Import Fantrax cookies from your default Chrome profile.

This sidesteps the selenium login flow entirely — if you're already logged in
to Fantrax in Chrome (the regular browser, not a selenium-driven one), this
reads those cookies straight out of Chrome's local SQLite store, decrypts the
values via your macOS keychain, and writes them in the same format auth.py
expects (.cookies/fantrax.json).

macOS may prompt for keychain access the first time — approve it. After that
it's silent.

Usage: python import_chrome_cookies.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

COOKIE_PATH = Path(".cookies/fantrax.json")


def main() -> int:
    try:
        from pycookiecheat import chrome_cookies
    except ImportError:
        print("pycookiecheat not installed. Run: pip install pycookiecheat")
        return 1

    print("Reading Fantrax cookies from your default Chrome profile...")
    print("(macOS may prompt you to allow keychain access.)\n")

    try:
        # pycookiecheat returns {name: value} for cookies matching the URL
        # AND its parent domains (so .fantrax.com cookies are included).
        cookies_dict = chrome_cookies("https://www.fantrax.com/fantasy")
    except Exception as e:
        print(f"Failed to read Chrome cookies: {e}")
        print("\nFallbacks:")
        print("  - Make sure you're logged in to Fantrax in your default Chrome profile")
        print("  - If using a non-default profile, set CHROME_COOKIE_FILE env var to that profile's Cookies file")
        print("  - Or fall back to selenium: just re-run the audit/intel script directly")
        return 2

    if not cookies_dict:
        print("No Fantrax cookies found in default Chrome profile.")
        print("Are you logged in there? Try visiting fantrax.com in Chrome and signing in.")
        return 3

    # Convert {name: value} to the selenium-style list the rest of the code expects.
    cookie_list = []
    for name, value in cookies_dict.items():
        cookie_list.append({
            "name": name,
            "value": value,
            "domain": ".fantrax.com",
            "path": "/",
        })

    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(cookie_list, indent=2))

    print(f"Imported {len(cookie_list)} cookie(s) to {COOKIE_PATH}")
    interesting = [c["name"] for c in cookie_list if c["name"] in ("JSESSIONID", "FX_SESSION", "fxauth", "auth0", "user")]
    if interesting:
        print(f"Auth-relevant cookie names present: {interesting}")
    else:
        print("Note: didn't see an obvious auth/session cookie name. The pull may still work,")
        print("or you may need to log in manually — try running the script and we'll see.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
