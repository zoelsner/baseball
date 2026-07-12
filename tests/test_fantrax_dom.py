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

    def test_live_headshot_url_does_not_fold_size_into_player_id(self):
        html = """
        <div itablerow class="i-table__row ng-star-inserted">
          <div itablecell="player">
            <button class="lineup-btn">OF</button>
            <scorer>
              <figure class="scorer__image"
                style="background-image: url(&quot;https://fantraximg.com/si/headshots/MLB/hs0423c_96_1.png&quot;);">
              </figure>
              <div class="scorer__info__name"><a>C. Cortes</a></div>
            </scorer>
          </div>
        </div>
        """

        slots = fantrax_dom.lineup_slots_from_html(html)

        self.assertEqual(set(slots), {"0423c"})
        self.assertEqual(slots["0423c"]["slot"], "OF")

    def test_visible_live_rows_parse_slot_identity_and_headshotless_players(self):
        html = """
        <div itablerow class="i-table__row ng-star-inserted">
          <div itablecell="player"><button class="lineup-btn">OF</button><scorer>
            <figure class="scorer__image"
              style="background-image: url(&quot;https://fantraximg.com/si/headshots/MLB/hs0423c_96_1.png&quot;);"></figure>
            <div class="scorer__info__name"><a>C. Cortes</a></div>
            <div class="scorer__info__positions"><span>OF</span><span> - ATH </span></div>
          </scorer></div>
          <button>Drop</button><button>Trade</button>
        </div>
        <div itablerow class="i-table__row ng-star-inserted">
          <div itablecell="player"><button class="lineup-btn">Min</button><scorer>
            <figure class="scorer__image"></figure>
            <div class="scorer__info__name"><a>C. Condon</a></div>
            <div class="scorer__info__positions"><span>1B,OF</span><span> - COL </span></div>
          </scorer></div>
          <button>Drop</button><button>Trade</button>
        </div>
        """

        rows = fantrax_dom.visible_roster_rows_from_html(html)

        self.assertEqual(rows, [
            {
                "player_id": "0423c",
                "name": "C. Cortes",
                "team": "ATH",
                "slot": "OF",
                "lineup_control_enabled": True,
                "identity_source": "visible_headshot_or_attribute",
                "identity_conflict": [],
            },
            {
                "player_id": None,
                "name": "C. Condon",
                "team": "COL",
                "slot": "MIN",
                "lineup_control_enabled": True,
                "identity_source": "visible_identity_missing",
                "identity_conflict": [],
            },
        ])

    def test_position_chip_before_real_lineup_control_uses_marked_control(self):
        html = """
        <div class="i-table__row" data-player-id="p1">
          <div itablecell="player"><button>OF</button><button class="lineup-btn">RES</button><scorer></scorer></div>
        </div>
        """
        self.assertEqual(fantrax_dom.lineup_slots_from_html(html)["p1"]["slot"], "RES")

    def test_ambiguous_unmarked_slot_controls_fail_closed(self):
        html = """
        <div class="i-table__row" data-player-id="p1">
          <div itablecell="player"><button>OF</button><button>RES</button><scorer></scorer></div>
        </div>
        """
        self.assertEqual(fantrax_dom.lineup_slots_from_html(html), {})

    def test_disabled_lineup_control_is_reported(self):
        html = '<div class="player-row" data-player-id="p1"><button class="lineup-btn" disabled>OF</button></div>'
        self.assertFalse(fantrax_dom.lineup_slots_from_html(html)["p1"]["lineup_control_enabled"])

    def test_framework_class_and_ancestor_state_disable_lineup_control(self):
        for disabled_markup in (
            '<button class="lineup-btn mat-mdc-button-disabled">OF</button>',
            '<button class="lineup-btn p-disabled">OF</button>',
            '<button class="lineup-btn lineup-btn--disabled">OF</button>',
            '<div class="locked-control"><button class="lineup-btn">OF</button></div>',
            '<div data-locked="true"><button class="lineup-btn">OF</button></div>',
        ):
            with self.subTest(markup=disabled_markup):
                html = f'<div class="player-row" data-player-id="p1">{disabled_markup}</div>'
                self.assertFalse(fantrax_dom.lineup_slots_from_html(html)["p1"]["lineup_control_enabled"])

    def test_reconciles_headshotless_prospect_only_with_unique_safe_identity(self):
        visible = [
            {"player_id": "0423c", "name": "C. Cortes", "team": "ATH", "slot": "OF", "identity_conflict": []},
            {"player_id": None, "name": "C. Condon", "team": "COL", "slot": "MIN", "identity_conflict": []},
            {"player_id": None, "name": "L. Montes", "team": "SEA", "slot": "MIN", "identity_conflict": []},
        ]
        expected = [
            {"id": "0423c", "name": "Carlos Cortes", "team": "ATH"},
            {"id": "06d2k", "name": "Charlie Condon", "team": "COL"},
            {"id": "05abc", "name": "Lazaro Montes", "team": "SEA"},
        ]

        rows = fantrax_dom.reconcile_visible_roster_rows(visible, expected)

        by_id = {row["player_id"]: row for row in rows}
        self.assertEqual(by_id["0423c"]["identity_source"], "visible_player_id")
        self.assertEqual(by_id["06d2k"]["identity_source"], "visible_initial_surname_team_unique")
        self.assertEqual(by_id["05abc"]["identity_source"], "visible_initial_surname_team_unique")

    def test_headshotless_identity_join_fails_on_ambiguity_or_missing_row(self):
        ambiguous_visible = [
            {"player_id": None, "name": "C. Condon", "team": "COL", "slot": "MIN", "identity_conflict": []},
        ]
        ambiguous_expected = [
            {"id": "one", "name": "Charlie Condon", "team": "COL"},
            {"id": "two", "name": "Chris Condon", "team": "COL"},
        ]
        with self.assertRaisesRegex(fantrax_dom.VisibleRosterIdentityError, "count"):
            fantrax_dom.reconcile_visible_roster_rows(ambiguous_visible, ambiguous_expected)

        with self.assertRaisesRegex(fantrax_dom.VisibleRosterIdentityError, "safe matches"):
            fantrax_dom.reconcile_visible_roster_rows(
                ambiguous_visible,
                [{"id": "other", "name": "Charlie Condon", "team": "SEA"}],
            )

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
