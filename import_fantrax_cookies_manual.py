"""Write Fantrax cookies from manually copied browser data.

This avoids macOS keychain access. Copy the request `Cookie:` header from a
logged-in Fantrax browser request, then run:

    pbpaste | python import_fantrax_cookies_manual.py --cookie-header -

The script writes `.cookies/fantrax.json` in the same format used by `auth.py`.
It never prints cookie values.

Avoid passing live cookie values directly on the command line. Use stdin (`-`)
or a local file so the values do not land in shell history or process listings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


COOKIE_PATH = Path(".cookies/fantrax.json")
DEFAULT_DOMAIN = ".fantrax.com"
DEFAULT_PATH = "/"


def _read_arg_or_stdin(value: str) -> tuple[str, str]:
    if value == "-":
        return sys.stdin.read(), "stdin"
    try:
        path = Path(value)
        if path.is_file():
            return path.read_text(), "file"
    except (OSError, ValueError):
        pass
    return value, "literal"


def parse_cookie_header(header: str) -> list[dict[str, str]]:
    text = header.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    cookies = []
    seen = set()
    for part in text.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value or name in seen:
            continue
        seen.add(name)
        cookies.append({
            "name": name,
            "value": value,
            "domain": DEFAULT_DOMAIN,
            "path": DEFAULT_PATH,
        })
    return cookies


def normalize_cookie_json(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [{"name": name, "value": value} for name, value in data.items()]
    if not isinstance(data, list):
        raise ValueError("Cookie JSON must be an array of cookie objects or an object of name/value pairs")
    cookies = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("value")
        if not name or value in (None, "") or name in seen:
            continue
        seen.add(name)
        cookie = {
            "name": name,
            "value": str(value),
            "domain": item.get("domain") or DEFAULT_DOMAIN,
            "path": item.get("path") or DEFAULT_PATH,
        }
        if "secure" in item:
            cookie["secure"] = bool(item.get("secure"))
        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item.get("httpOnly"))
        if "sameSite" in item:
            cookie["sameSite"] = item.get("sameSite")
        if "expiry" in item:
            cookie["expiry"] = item.get("expiry")
        if "expirationDate" in item:
            cookie["expiry"] = item.get("expirationDate")
        cookies.append(cookie)
    return cookies


def validate_cookies(cookies: list[dict[str, Any]]) -> None:
    if not cookies:
        raise ValueError("No cookies found in input")
    missing = [cookie for cookie in cookies if not cookie.get("name") or not cookie.get("value")]
    if missing:
        raise ValueError("Every cookie must have a name and value")


def write_cookies(cookies: list[dict[str, Any]], path: Path = COOKIE_PATH) -> None:
    validate_cookies(cookies)
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        path.parent.chmod(0o700)

    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(cookies, indent=2))
    tmp_path.chmod(0o600)
    tmp_path.replace(path)
    path.chmod(0o600)


def _interesting_names(cookies: list[dict[str, Any]]) -> list[str]:
    interesting = {"JSESSIONID", "FX_SESSION", "fxauth", "auth0", "user"}
    return [str(cookie.get("name")) for cookie in cookies if cookie.get("name") in interesting]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cookie-header", help="Cookie header text, file path, or '-' for stdin")
    source.add_argument("--json", help="Cookie JSON text, file path, or '-' for stdin")
    parser.add_argument("--output", default=str(COOKIE_PATH), help="Output cookie JSON path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cookie_header is not None:
        raw, source_kind = _read_arg_or_stdin(args.cookie_header)
        cookies = parse_cookie_header(raw)
    else:
        raw, source_kind = _read_arg_or_stdin(args.json)
        cookies = normalize_cookie_json(raw)
    if source_kind == "literal":
        print(
            "Warning: inline cookie input can appear in shell history and process listings; prefer '-' stdin or a file.",
            file=sys.stderr,
        )
    write_cookies(cookies, Path(args.output))
    print(f"Imported {len(cookies)} Fantrax cookie(s) to {args.output}")
    interesting = _interesting_names(cookies)
    if interesting:
        print(f"Auth-relevant cookie names present: {', '.join(interesting)}")
    else:
        print("No obvious auth/session cookie names found; run the slot diagnostic to verify the session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
