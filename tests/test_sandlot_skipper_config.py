import os
import unittest
from unittest.mock import patch

import sandlot_skipper
from sandlot_api import sandlot_index, skipper_options


class SkipperModelConfigTests(unittest.TestCase):
    def test_default_primary_is_deepseek_flash_and_fallback_is_kimi(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sandlot_skipper.primary_model(), "deepseek/deepseek-v4-flash")
            self.assertEqual(sandlot_skipper.fallback_model(), "moonshotai/kimi-k2")
            self.assertNotIn("tencent/hy3-preview:free", sandlot_skipper.allowed_chat_models())
            self.assertFalse(any(m.startswith("tencent/") for m in sandlot_skipper.allowed_chat_models()))

    def test_env_fallback_override_is_still_allowed(self):
        with patch.dict(os.environ, {"SANDLOT_AI_MODEL_FALLBACK": "custom/model"}, clear=True):
            self.assertEqual(sandlot_skipper.fallback_model(), "custom/model")
            self.assertIn("custom/model", sandlot_skipper.allowed_chat_models())

    def test_options_do_not_advertise_retired_tencent_free_model(self):
        options = skipper_options()
        model_ids = [m["id"] for m in options["models"]]
        models_by_id = {m["id"]: m for m in options["models"]}
        self.assertEqual(options["default_model"], "deepseek/deepseek-v4-flash")
        self.assertIn("deepseek/deepseek-v4-flash", model_ids)
        self.assertIn("z-ai/glm-5.2", model_ids)
        self.assertEqual(models_by_id["z-ai/glm-5.2"]["label"], "GLM 5.2")
        self.assertEqual(models_by_id["z-ai/glm-5.2"]["short"], "GLM 5.2")
        self.assertNotIn("tencent/hy3-preview:free", model_ids)
        self.assertFalse(any(m.startswith("tencent/") for m in model_ids))

    def test_index_serves_content_hashed_app_bundle(self):
        response = sandlot_index()
        body = response.body.decode()

        self.assertIn("app.js?v=", body)
        self.assertNotIn("app.js?v=frontend-build", body)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")


if __name__ == "__main__":
    unittest.main()
