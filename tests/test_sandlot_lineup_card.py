import unittest
from datetime import date
from unittest.mock import patch

import sandlot_lineup_card as card_mod


def _card(**overrides):
    card = {
        "week": ["2026-07-06", "2026-07-12"],
        "generated_at": "2026-07-06T04:00:00-04:00",
        "snapshot_id": 257,
        "team_name": "zohann",
        "projected_total": 280.2,
        "current_total": 255.4,
        "delta": 24.8,
        "lineup": [
            {"slot": "C", "name": "Salvador Perez", "proj": 9.7, "basis": "1.7/gm x 5.7 games"},
            {"slot": "SP", "name": "Tarik Skubal", "proj": 15.0,
             "basis": "15.0/gm x 1.0 starts (probable)"},
        ],
        "moves": {"start": ["Andrew Vaughn"], "bench": ["Kevin Gausman"]},
        "bench": [{"name": "Ian Happ", "proj": 11.4}],
        "unfilled": [],
        "excluded": [{"name": "Aaron Judge", "basis": "2.0/gm x 0.0 games"}],
    }
    card.update(overrides)
    return card


class ComingWeekTests(unittest.TestCase):
    def test_monday_keeps_current_week(self):
        monday, sunday = card_mod.coming_week(date(2026, 7, 6))
        self.assertEqual((monday, sunday), (date(2026, 7, 6), date(2026, 7, 12)))

    def test_midweek_targets_next_monday(self):
        monday, sunday = card_mod.coming_week(date(2026, 7, 8))
        self.assertEqual((monday, sunday), (date(2026, 7, 13), date(2026, 7, 19)))

    def test_sunday_targets_tomorrow(self):
        monday, _ = card_mod.coming_week(date(2026, 7, 12))
        self.assertEqual(monday, date(2026, 7, 13))


class RenderMarkdownTests(unittest.TestCase):
    def test_full_card(self):
        text = card_mod.render_markdown(_card())
        self.assertIn("week 2026-07-06 .. 2026-07-12", text)
        self.assertIn("**280.2**", text)
        self.assertIn("(**+24.8**)", text)
        self.assertIn("| SP | Tarik Skubal | 15.0 |", text)
        self.assertIn("Moves: start Andrew Vaughn; bench Kevin Gausman.", text)
        self.assertIn("Excluded (IL/out/minors): Aaron Judge", text)
        self.assertNotIn("No eligible player", text)

    def test_unfilled_warning_and_negative_delta(self):
        text = card_mod.render_markdown(_card(unfilled=["RP"], delta=-3.2))
        self.assertIn("No eligible player for: RP", text)
        self.assertIn("(**-3.2**)", text)

    def test_no_moves_renders_without_moves_line(self):
        text = card_mod.render_markdown(_card(moves={"start": [], "bench": []}))
        self.assertNotIn("Moves:", text)


class LineupCardEndpointTests(unittest.TestCase):
    def _get(self):
        from fastapi.testclient import TestClient

        import sandlot_api

        with TestClient(sandlot_api.app) as client:
            return client.get("/api/lineup/card")

    def test_serves_stored_payload(self):
        import sandlot_api

        stored = {
            "week_start": date(2026, 7, 6),
            "snapshot_id": 257,
            "payload": _card(),
            "generated_at": None,
        }
        with patch.object(sandlot_api.sandlot_db, "latest_lineup_proposal", return_value=stored):
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["delta"], 24.8)
        self.assertEqual(len(body["lineup"]), 2)

    def test_503_when_nothing_stored(self):
        import sandlot_api

        with patch.object(sandlot_api.sandlot_db, "latest_lineup_proposal", return_value=None):
            resp = self._get()
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
