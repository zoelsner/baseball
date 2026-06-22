import unittest

import fantrax_dom


class FantraxDomSlotTests(unittest.TestCase):
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
