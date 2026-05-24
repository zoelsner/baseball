import os
import unittest
from unittest.mock import patch

import sandlot_config


class SandlotConfigTests(unittest.TestCase):
    def test_warmups_default_off(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("SANDLOT_")}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(sandlot_config.profile_warm_enabled())
            self.assertFalse(sandlot_config.waiver_ai_warm_enabled())

    def test_profile_warm_requires_explicit_enable(self):
        with patch.dict(os.environ, {"SANDLOT_PROFILE_WARM_ENABLED": "1"}, clear=False):
            self.assertTrue(sandlot_config.profile_warm_enabled())

    def test_legacy_disable_still_wins(self):
        with patch.dict(
            os.environ,
            {
                "SANDLOT_PROFILE_WARM_ENABLED": "1",
                "SANDLOT_PROFILE_WARM_DISABLED": "1",
                "SANDLOT_WAIVER_AI_WARM_ENABLED": "1",
                "SANDLOT_WAIVER_AI_WARM_DISABLED": "1",
            },
            clear=False,
        ):
            self.assertFalse(sandlot_config.profile_warm_enabled())
            self.assertFalse(sandlot_config.waiver_ai_warm_enabled())


if __name__ == "__main__":
    unittest.main()
