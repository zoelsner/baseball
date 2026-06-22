import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path

import import_fantrax_cookies_manual as manual


class ManualFantraxCookieImportTests(unittest.TestCase):
    def test_parse_cookie_header_strips_prefix_and_preserves_equals_in_values(self):
        cookies = manual.parse_cookie_header("Cookie: JSESSIONID=abc123; token=a=b=c; empty=; JSESSIONID=dup")

        self.assertEqual([cookie["name"] for cookie in cookies], ["JSESSIONID", "token"])
        self.assertEqual(cookies[1]["value"], "a=b=c")
        self.assertEqual(cookies[0]["domain"], ".fantrax.com")
        self.assertEqual(cookies[0]["path"], "/")

    def test_normalize_cookie_json_accepts_name_value_object(self):
        cookies = manual.normalize_cookie_json(json.dumps({"JSESSIONID": "abc", "FX_SESSION": "def"}))

        self.assertEqual([cookie["name"] for cookie in cookies], ["JSESSIONID", "FX_SESSION"])
        self.assertEqual(cookies[1]["value"], "def")

    def test_normalize_cookie_json_preserves_browser_cookie_metadata(self):
        cookies = manual.normalize_cookie_json(json.dumps([
            {
                "name": "JSESSIONID",
                "value": "abc",
                "domain": "www.fantrax.com",
                "path": "/fantasy",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expirationDate": 12345,
            }
        ]))

        self.assertEqual(cookies[0]["domain"], "www.fantrax.com")
        self.assertEqual(cookies[0]["path"], "/fantasy")
        self.assertTrue(cookies[0]["secure"])
        self.assertTrue(cookies[0]["httpOnly"])
        self.assertEqual(cookies[0]["sameSite"], "Lax")
        self.assertEqual(cookies[0]["expiry"], 12345)

    def test_normalize_cookie_json_skips_empty_and_duplicate_names(self):
        cookies = manual.normalize_cookie_json(json.dumps([
            {"name": "JSESSIONID", "value": "abc", "secure": False, "httpOnly": False},
            {"name": "skip", "value": ""},
            {"name": "JSESSIONID", "value": "duplicate"},
        ]))

        self.assertEqual([cookie["name"] for cookie in cookies], ["JSESSIONID"])
        self.assertFalse(cookies[0]["secure"])
        self.assertFalse(cookies[0]["httpOnly"])

    def test_write_cookies_writes_auth_compatible_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fantrax.json"
            manual.write_cookies([
                {"name": "JSESSIONID", "value": "abc", "domain": ".fantrax.com", "path": "/"}
            ], path)

            stored = json.loads(path.read_text())
            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(stored[0]["name"], "JSESSIONID")
        self.assertEqual(stored[0]["value"], "abc")
        self.assertEqual(mode, 0o600)

    def test_main_does_not_print_cookie_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "header.txt"
            output_path = Path(tmp) / "fantrax.json"
            input_path.write_text("Cookie: JSESSIONID=secret-session-value; token=other-secret")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = manual.main(["--cookie-header", str(input_path), "--output", str(output_path)])

        self.assertEqual(exit_code, 0)
        combined_output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("secret-session-value", combined_output)
        self.assertNotIn("other-secret", combined_output)

    def test_main_warns_when_cookie_values_are_passed_inline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "fantrax.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = manual.main([
                    "--cookie-header",
                    "Cookie: JSESSIONID=secret-session-value",
                    "--output",
                    str(output_path),
                ])

        self.assertEqual(exit_code, 0)
        self.assertIn("shell history", stderr.getvalue())
        self.assertNotIn("secret-session-value", stdout.getvalue() + stderr.getvalue())

    def test_validate_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            manual.validate_cookies([])


if __name__ == "__main__":
    unittest.main()
