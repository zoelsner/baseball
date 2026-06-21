import os
import unittest
from types import SimpleNamespace
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

    def test_web_search_defaults_on_and_can_be_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(sandlot_skipper.web_search_available())
            self.assertTrue(sandlot_skipper.web_search_default_enabled())
            self.assertTrue(sandlot_skipper.web_search_allowed(True))
            self.assertFalse(sandlot_skipper.web_search_allowed(False))

        with patch.dict(os.environ, {"SANDLOT_SKIPPER_WEB_SEARCH_DISABLED": "1"}, clear=True):
            self.assertFalse(sandlot_skipper.web_search_available())
            self.assertFalse(sandlot_skipper.web_search_default_enabled())
            self.assertFalse(sandlot_skipper.web_search_allowed(True))

        with patch.dict(os.environ, {"SANDLOT_SKIPPER_WEB_SEARCH_DEFAULT_ENABLED": "0"}, clear=True):
            self.assertTrue(sandlot_skipper.web_search_available())
            self.assertFalse(sandlot_skipper.web_search_default_enabled())
            self.assertTrue(sandlot_skipper.web_search_allowed(True))

    def test_options_advertise_web_search_controls(self):
        with patch.dict(os.environ, {}, clear=True):
            options = skipper_options()

        self.assertEqual(options["web_search"]["tool"], "openrouter:web_search")
        self.assertTrue(options["web_search"]["available"])
        self.assertTrue(options["web_search"]["default_enabled"])

        with patch.dict(os.environ, {"SANDLOT_SKIPPER_WEB_SEARCH_DEFAULT_ENABLED": "0"}, clear=True):
            options = skipper_options()
        self.assertTrue(options["web_search"]["available"])
        self.assertFalse(options["web_search"]["default_enabled"])

        with patch.dict(os.environ, {"SANDLOT_SKIPPER_WEB_SEARCH_DISABLED": "1"}, clear=True):
            options = skipper_options()
        self.assertFalse(options["web_search"]["available"])
        self.assertFalse(options["web_search"]["default_enabled"])

    def test_build_messages_adds_web_fallback_prompt_only_when_enabled(self):
        base = sandlot_skipper.build_messages([], "compare this free agent", "SNAPSHOT", web_search=False)
        with_web = sandlot_skipper.build_messages([], "compare this free agent", "SNAPSHOT", web_search=True)

        self.assertFalse(any("Web fallback is enabled" in m["content"] for m in base))
        self.assertTrue(any("Web fallback is enabled" in m["content"] for m in with_web))

    def test_stream_adds_capped_openrouter_web_search_tool_when_enabled(self):
        class CapturingCompletions:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="searched"))],
                    )
                ]

        completions = CapturingCompletions()
        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        chunks = list(
            client.stream(
                [{"role": "user", "content": "find missing player stats"}],
                model_order=("test/model",),
                web_search=True,
            )
        )

        self.assertEqual(chunks, [("token", "searched"), ("model", "test/model")])
        self.assertEqual(completions.kwargs["tools"][0]["type"], "openrouter:web_search")
        self.assertEqual(completions.kwargs["tools"][0]["parameters"]["max_results"], 4)
        self.assertEqual(completions.kwargs["tools"][0]["parameters"]["max_total_results"], 8)

    def test_stream_extracts_url_citation_sources_and_usage(self):
        class CapturingCompletions:
            def create(self, **kwargs):
                return [
                    SimpleNamespace(
                        usage=SimpleNamespace(
                            server_tool_use=SimpleNamespace(web_search_requests=1),
                        ),
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    annotations=[
                                        {
                                            "type": "url_citation",
                                            "url_citation": {
                                                "url": "https://www.mlb.com/player/martin-perez-527048",
                                                "title": "Martin Perez Stats",
                                                "content": "Pitcher profile excerpt",
                                                "start_index": 10,
                                                "end_index": 20,
                                            },
                                        }
                                    ],
                                    content="web-backed reply",
                                ),
                            )
                        ],
                    )
                ]

        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=CapturingCompletions()))

        chunks = list(
            client.stream(
                [{"role": "user", "content": "find missing player stats"}],
                model_order=("test/model",),
                web_search=True,
            )
        )

        self.assertEqual(chunks[0][0], "source")
        self.assertEqual(chunks[0][1]["url"], "https://www.mlb.com/player/martin-perez-527048")
        self.assertIn(("web_search_requests", 1), chunks)
        self.assertIn(("token", "web-backed reply"), chunks)
        self.assertIn(("model", "test/model"), chunks)

    def test_stream_only_attaches_web_search_tool_to_first_model_attempt(self):
        class FallbackCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs["model"] == "primary/model":
                    raise RuntimeError("primary failed")
                return [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="fallback reply"))],
                    )
                ]

        completions = FallbackCompletions()
        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        chunks = list(
            client.stream(
                [{"role": "user", "content": "find missing player stats"}],
                model_order=("primary/model", "fallback/model"),
                web_search=True,
            )
        )

        self.assertEqual(chunks, [("token", "fallback reply"), ("model", "fallback/model")])
        self.assertIn("tools", completions.calls[0])
        self.assertNotIn("tools", completions.calls[1])

    def test_stream_omits_web_search_tool_when_disabled(self):
        class CapturingCompletions:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="snapshot only"))],
                    )
                ]

        completions = CapturingCompletions()
        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        list(
            client.stream(
                [{"role": "user", "content": "use snapshot"}],
                model_order=("test/model",),
                web_search=False,
            )
        )

        self.assertNotIn("tools", completions.kwargs)

    def test_index_serves_content_hashed_app_bundle(self):
        response = sandlot_index()
        body = response.body.decode()

        self.assertIn("app.js?v=", body)
        self.assertNotIn("app.js?v=frontend-build", body)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")


if __name__ == "__main__":
    unittest.main()
