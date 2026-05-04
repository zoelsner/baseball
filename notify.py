"""Compose and send the daily audit email via Gmail SMTP.

Sends a multipart message: plaintext (markdown) + HTML (markdown rendered).
Uses Gmail App Passwords (regular Gmail passwords don't work with SMTP since
2022). On uncaught exceptions in the main script, send_failure() is called
with the traceback.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _markdown_to_html(md_text: str) -> str:
    try:
        import markdown as md_lib

        html_body = md_lib.markdown(md_text, extensions=["fenced_code", "tables"])
    except ImportError:
        log.warning("python markdown package not installed; sending pre-formatted HTML")
        escaped = md_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_body = f"<pre style='font-family: ui-monospace, Menlo, monospace; font-size: 13px;'>{escaped}</pre>"

    style = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             font-size: 14px; line-height: 1.5; color: #1f2937; max-width: 760px;
             margin: 0 auto; padding: 16px; }
      h1 { font-size: 22px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
      h2 { font-size: 17px; margin-top: 24px; color: #111827; }
      table { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
      th, td { border: 1px solid #e5e7eb; padding: 4px 8px; text-align: left; }
      th { background: #f9fafb; }
      code, pre { font-family: ui-monospace, Menlo, monospace; }
      pre { background: #f3f4f6; padding: 10px; border-radius: 4px; overflow-x: auto; }
    </style>
    """
    return f"<!doctype html><html><head>{style}</head><body>{html_body}</body></html>"


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set in the environment")
    return val


def send_email(subject: str, markdown_body: str) -> None:
    user = _require("EMAIL_FROM")
    pwd = _require("GMAIL_APP_PASSWORD")
    to = _require("EMAIL_TO")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(markdown_body, "plain", "utf-8"))
    msg.attach(MIMEText(_markdown_to_html(markdown_body), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(user, pwd)
        server.sendmail(user, [to], msg.as_string())
    log.info("Email sent: %r -> %s", subject, to)


def send_failure(traceback_text: str, log_path: str | None = None) -> None:
    body_lines = ["# Fantrax Audit FAILED", "", "```", traceback_text.strip(), "```"]
    if log_path:
        body_lines += ["", f"Full log: `{log_path}`"]
    body = "\n".join(body_lines)
    try:
        send_email("Fantrax Audit FAILED", body)
    except Exception as e:
        log.error("Failure-notification email also failed: %s", e)
