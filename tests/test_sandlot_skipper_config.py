import os
import unittest
from unittest.mock import patch

import sandlot_skipper
from sandlot_api import skipper_options


class SkipperModelConfigTests(unittest.TestCase):
    def test_default_fallback_is_current_non_tencent_model(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sandlot_skipper.fallback_model(), "deepseek/deepseek-v4-flash")
            self.assertNotIn("tencent/hy3-preview:free", sandlot_skipper.allowed_chat_models())
            self.assertFalse(any(m.startswith("tencent/") for m in sandlot_skipper.allowed_chat_models()))

    def test_env_fallback_override_is_still_allowed(self):
        with patch.dict(os.environ, {"SANDLOT_AI_MODEL_FALLBACK": "custom/model"}, clear=True):
            self.assertEqual(sandlot_skipper.fallback_model(), "custom/model")
            self.assertIn("custom/model", sandlot_skipper.allowed_chat_models())

    def test_options_do_not_advertise_retired_tencent_free_model(self):
        model_ids = [m["id"] for m in skipper_options()["models"]]
        self.assertIn("deepseek/deepseek-v4-flash", model_ids)
        self.assertNotIn("tencent/hy3-preview:free", model_ids)
        self.assertFalse(any(m.startswith("tencent/") for m in model_ids))


if __name__ == "__main__":
    unittest.main()
