"""Selenium-based login + cookie persistence for Fantrax.

Fantrax has no public API, so we log in through the browser once, capture the
session cookies, and reuse them on subsequent runs via a requests.Session.
On 401/403 the caller should call get_session(force_login=True).

Cookies are persisted as JSON (cookies are plain dicts of strings, so JSON is
equivalent to pickle here without the deserialization risk).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

log = logging.getLogger(__name__)

COOKIE_PATH = Path(".cookies/fantrax.json")
LOGIN_URL = "https://www.fantrax.com/login"
HOME_URL = "https://www.fantrax.com/fantasy"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _build_driver(headful: bool) -> webdriver.Chrome:
    options = Options()
    if not headful:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


MANUAL_LOGIN_TIMEOUT_SECS = 300  # 5 min for the user to log in by hand


def _try_auto_fill(driver, username: str, password: str) -> bool:
    """Best-effort attempt at auto-filling the login form. Returns True if we
    submitted a form, False if we couldn't find one (caller falls back to
    manual login)."""
    try:
        wait = WebDriverWait(driver, 8)
        email_input = None
        for selector in [(By.NAME, "email"), (By.ID, "email"),
                         (By.CSS_SELECTOR, "input[type='email']"),
                         (By.CSS_SELECTOR, "input[formcontrolname='email']")]:
            try:
                email_input = wait.until(EC.presence_of_element_located(selector))
                break
            except Exception:
                continue
        if not email_input:
            return False

        email_input.clear()
        email_input.send_keys(username)

        pwd_input = None
        for selector in [(By.NAME, "password"), (By.ID, "password"),
                         (By.CSS_SELECTOR, "input[type='password']"),
                         (By.CSS_SELECTOR, "input[formcontrolname='password']")]:
            try:
                pwd_input = driver.find_element(*selector)
                break
            except Exception:
                continue
        if not pwd_input:
            return False

        pwd_input.clear()
        pwd_input.send_keys(password)

        submitted = False
        for selector in [
            (By.XPATH, "//button[@type='submit']"),
            (By.XPATH, "//button[contains(translate(., 'SIGN', 'sign'), 'sign in')]"),
            (By.XPATH, "//button[contains(translate(., 'LOGIN', 'login'), 'login')]"),
        ]:
            try:
                driver.find_element(*selector).click()
                submitted = True
                break
            except Exception:
                continue
        if not submitted:
            pwd_input.submit()
        return True
    except Exception as e:
        log.debug("auto-fill attempt failed: %s", e)
        return False


def _wait_for_login(driver, timeout: int = MANUAL_LOGIN_TIMEOUT_SECS) -> bool:
    """Poll the driver until we detect a logged-in session. Logged-in markers:
    URL no longer on /login, or any cookie named 'JSESSIONID' is present, or
    we navigated to fantrax.com/fantasy."""
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        try:
            url = driver.current_url or ""
        except Exception:
            time.sleep(2)
            continue
        cookies = driver.get_cookies()
        cookie_names = {c.get("name", "") for c in cookies}
        in_fantasy = "/fantasy" in url
        no_login = "login" not in url.lower()
        has_session_cookie = "JSESSIONID" in cookie_names or "FX_SESSION" in cookie_names
        on_fantrax = "fantrax.com" in url
        if on_fantrax and (in_fantasy or (no_login and has_session_cookie)):
            return True
        status = f"url={url} cookies={len(cookies)}"
        if status != last_status:
            log.info("waiting for login... %s", status)
            last_status = status
        time.sleep(2)
    return False


def _selenium_login(username: str, password: str, headful: bool) -> list[dict]:
    """Drive Chrome to capture a logged-in Fantrax session.

    Strategy:
      1. Open Chrome, navigate to login URL.
      2. Try to auto-fill the form (covers happy path / unchanged DOM).
      3. Whether or not auto-fill worked, wait up to MANUAL_LOGIN_TIMEOUT_SECS
         for a logged-in state. If you hit MFA, captcha, an OAuth flow, or the
         form moved entirely, just complete the login manually in the open
         Chrome window — the script will detect it and capture cookies.
    """
    if not headful:
        log.warning("headless selenium for Fantrax login is fragile; switching to headful")
    log.info("Opening Chrome for Fantrax login. If you don't see auto-fill, "
             "log in by hand in the window — script will wait up to %ds.",
             MANUAL_LOGIN_TIMEOUT_SECS)
    driver = _build_driver(headful=True)  # always visible for login
    try:
        driver.get(LOGIN_URL)
        time.sleep(2)
        auto_submitted = _try_auto_fill(driver, username, password)
        if auto_submitted:
            log.info("Auto-fill submitted — verifying login completes...")
        else:
            log.info("Auto-fill failed; please log in manually in the Chrome window.")

        if not _wait_for_login(driver):
            try:
                final_url = driver.current_url
            except Exception:
                final_url = "?"
            raise RuntimeError(
                f"Manual login window timed out after {MANUAL_LOGIN_TIMEOUT_SECS}s. "
                f"Last URL: {final_url}. Re-run to retry."
            )

        # Visit a known logged-in page so the full cookie set is established.
        try:
            driver.get(HOME_URL)
            time.sleep(2)
        except Exception:
            pass

        cookies = driver.get_cookies()
        log.info("Captured %d cookies from logged-in session", len(cookies))
        return cookies
    finally:
        driver.quit()


def _save_cookies(cookies: list[dict]) -> None:
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(cookies, indent=2))
    log.info("Saved cookies to %s", COOKIE_PATH)


def _load_cookies() -> list[dict] | None:
    if not COOKIE_PATH.exists():
        return None
    try:
        return json.loads(COOKIE_PATH.read_text())
    except Exception as e:
        log.warning("Failed to load cookies (%s); will re-login", e)
        return None


def _build_session(cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    for c in cookies:
        kwargs = {"name": c["name"], "value": c["value"]}
        if c.get("domain"):
            kwargs["domain"] = c["domain"]
        if c.get("path"):
            kwargs["path"] = c["path"]
        session.cookies.set(**kwargs)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.fantrax.com",
        "Referer": "https://www.fantrax.com/fantasy",
    })
    return session


def get_session(force_login: bool = False) -> requests.Session:
    """Return a requests.Session authenticated to Fantrax.

    If a cached cookie file exists and force_login is False, those cookies are
    reused. Otherwise we drive selenium to log in and persist a new set.
    """
    cookies = None if force_login else _load_cookies()
    if not cookies:
        username = os.environ.get("FANTRAX_USER")
        password = os.environ.get("FANTRAX_PASS")
        if not username or not password:
            raise RuntimeError(
                "FANTRAX_USER and FANTRAX_PASS must be set (used for selenium login)"
            )
        # Default to headful for first login (or any forced re-login) so the
        # user can complete MFA/captcha. After cookies cache, runs are headless
        # because we never re-enter selenium unless cookies expire.
        is_first_login = force_login or not COOKIE_PATH.exists()
        headful_default = "1" if is_first_login else "0"
        headful = os.environ.get("FANTRAX_HEADFUL", headful_default) == "1"
        cookies = _selenium_login(username, password, headful=headful)
        _save_cookies(cookies)
    return _build_session(cookies)
