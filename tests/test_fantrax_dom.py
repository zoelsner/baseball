import unittest

import fantrax_dom


class FantraxDomSlotTests(unittest.TestCase):
    def test_capture_roster_html_installs_cookies_and_reads_page_source(self):
        class FakeDriver:
            page_source = '<div class="player-row" data-player-id="p1"><button class="lineup-btn">OF</button></div>'

            def __init__(self):
                self.urls = []
                self.cookies = []
                self.quit_called = False

            def get(self, url):
                self.urls.append(url)

            def add_cookie(self, cookie):
                self.cookies.append(cookie)

            def execute_script(self, _script):
                return "complete"

            def quit(self):
                self.quit_called = True

        driver = FakeDriver()

        html = fantrax_dom.capture_roster_html(
            [
                {"name": "JSESSIONID", "value": "secret", "domain": ".fantrax.com", "path": "/"},
                {"name": "", "value": "ignored"},
            ],
            league_id="league-1",
            team_id="team-1",
            driver_factory=lambda **_kwargs: driver,
            wait_seconds=0,
        )

        self.assertEqual(html, driver.page_source)
        self.assertEqual(driver.urls[0], "https://www.fantrax.com/fantasy")
        self.assertEqual(driver.urls[1], "https://www.fantrax.com/fantasy/league/league-1/team/roster;teamId=team-1")
        self.assertEqual(driver.cookies, [{"name": "JSESSIONID", "value": "secret", "domain": "fantrax.com", "path": "/"}])
        self.assertTrue(driver.quit_called)

    def test_capture_roster_html_can_use_override_url(self):
        class FakeDriver:
            page_source = "<html></html>"

            def __init__(self):
                self.urls = []

            def get(self, url):
                self.urls.append(url)

            def add_cookie(self, _cookie):
                pass

            def execute_script(self, _script):
                return "complete"

            def quit(self):
                pass

        driver = FakeDriver()

        fantrax_dom.capture_roster_html(
            [{"name": "JSESSIONID", "value": "secret"}],
            league_id="league-1",
            team_id="team-1",
            url="https://example.test/roster",
            driver_factory=lambda **_kwargs: driver,
            wait_seconds=0,
        )

        self.assertEqual(driver.urls[-1], "https://example.test/roster")

    def test_extracts_lineup_button_slots_from_table_and_div_rows(self):
        html = """
        <table>
          <tr>
            <td><img src="https://www.fantrax.com/hsjudge_50.png" /></td>
            <td><button class="lineup-btn">OF</button></td>
          </tr>
        </table>
        <div class="player-row">
          <span style="background-image: url('https://img.fantrax.com/hsbench-1_large.png')"></span>
          <div class="lineup-btn"><span>Reserve</span></div>
        </div>
        """

        slots = fantrax_dom.lineup_slots_from_html(html)

        self.assertEqual(slots["judge"]["slot"], "OF")
        self.assertEqual(slots["judge"]["slot_source"], "dom.lineup-btn")
        self.assertEqual(slots["bench-1"]["slot"], "RES")

    def test_ignores_active_label_without_real_lineup_slot(self):
        html = """
        <div class="player-row" data-player-id="active-only">
          <button class="lineup-btn">Active</button>
        </div>
        """

        slots = fantrax_dom.lineup_slots_from_html(html)

        self.assertEqual(slots, {})

    def test_ignores_broad_row_lineup_label_without_button_control(self):
        html = """
        <div class="player-row" data-player-id="row-label" aria-label="lineup row">
          <span>Row Label OF Bench</span>
        </div>
        """

        slots = fantrax_dom.lineup_slots_from_html(html)

        self.assertEqual(slots, {})

    def test_records_conflicting_duplicate_player_slots(self):
        html = """
        <div class="player-row" data-scorer-id="same-player">
          <button class="lineup-btn">SS</button>
        </div>
        <div class="player-row" data-scorer-id="same-player">
          <button class="lineup-btn">UT</button>
        </div>
        """

        slots = fantrax_dom.lineup_slots_from_html(html)

        self.assertEqual(slots["same-player"]["slot"], "SS")
        self.assertEqual(slots["same-player"]["conflicts"], [{"slot": "UT", "text": "UT"}])


if __name__ == "__main__":
    unittest.main()
