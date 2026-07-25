"""Email the Monday lineup card (monday_lineup.md) via Gmail SMTP.

Runs as the last step of the monday-lineup workflow. Skips quietly when the
email secrets are not configured, so the workflow stays green either way.

Env: GMAIL_USER (sender), GMAIL_APP_PASSWORD (app password), EMAIL_TO
(recipient, defaults to GMAIL_USER).
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from email.mime.text import MIMEText


def main() -> int:
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("EMAIL_TO") or user
    if not (user and password):
        print("email skipped: GMAIL_USER / GMAIL_APP_PASSWORD secrets not set")
        return 0
    try:
        body = open("monday_lineup.md", encoding="utf-8").read()
    except FileNotFoundError:
        print("email skipped: monday_lineup.md not found (card step failed?)")
        return 0

    subject = "Sandlot Monday lineup"
    try:
        card = json.load(open("monday_lineup.json", encoding="utf-8"))
        subject = (f"Sandlot Monday lineup — week of {card['week'][0]} "
                   f"({card['delta']:+.1f} pts available)")
    except Exception:  # noqa: BLE001 — subject enrichment only
        pass

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"emailed lineup card to {to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
