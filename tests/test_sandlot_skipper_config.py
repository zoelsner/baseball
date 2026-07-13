import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sandlot_skipper
from sandlot_api import _web_search_evidence, sandlot_index, skipper_options


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

    def test_system_prompt_accepts_bounded_trade_advisor_handoffs(self):
        self.assertIn('Sandlot trade-analysis evidence:', sandlot_skipper.SYSTEM_PROMPT)
        self.assertIn('never invent weekly/ROS/dynasty numbers', sandlot_skipper.SYSTEM_PROMPT)
        self.assertIn('never tell the user to accept automatically', sandlot_skipper.SYSTEM_PROMPT)

    def test_web_search_evidence_infers_execution_from_citations_without_overstating_verification(self):
        self.assertEqual(_web_search_evidence([], 0), (0, False, False))
        self.assertEqual(
            _web_search_evidence([{"url": "https://www.mlb.com/example"}], 0),
            (1, True, True),
        )
        self.assertEqual(_web_search_evidence([], 2), (2, True, False))

    def test_trade_reply_enforces_withheld_horizons_after_model_generation(self):
        prompt = (
            "Sandlot trade-analysis evidence: exact offer. "
            "The blocked evidence is: Cole Ragans: Currently on IR; return timing is not modeled.. "
            "The do-nothing alternative is to keep Pete Alonso at a verified current snapshot package rate of 3.08 FP/G. "
            "Roster consequence: moves out 1B. Internal replacement evidence: "
            "Best reserve cover: Andrew Vaughn (-0.78 FP/G vs outgoing). "
            "Current counter direction: ask for healthy value."
        )
        raw = """## Verdict: REJECT

### Weekly Impact — Uncertainty: Low
You lose 3.08 FP/G and gain zero this week.

### Rest-of-Season — Uncertainty: Medium
Net ROS impact: lose 150+ remaining FPts.

### Dynasty — Uncertainty: High
The long-term case depends on health and prospect risk.

### Replacement Value — Uncertainty: Low
Over nine games, the downgrade is 5.8–7.4 points.

### Do Nothing — Uncertainty: Low
Alonso gives you 3.08 FP/G for the rest of 2026.
"""

        repaired = sandlot_skipper.repair_reply(raw, prompt, {})

        self.assertIn("Sandlot evidence guardrail applied", repaired)
        self.assertIn("withholds a weekly Fantrax point delta", repaired)
        self.assertIn("No rest-of-season point total is claimed", repaired)
        self.assertIn("Best reserve cover: Andrew Vaughn (-0.78 FP/G vs outgoing)", repaired)
        self.assertIn("keep Pete Alonso at a verified current snapshot package rate of 3.08 FP/G", repaired)
        self.assertIn("The long-term case depends on health and prospect risk", repaired)
        self.assertNotIn("lose 150+", repaired)
        self.assertNotIn("5.8–7.4", repaired)
        self.assertNotIn("for the rest of 2026", repaired)

    def test_trade_reply_accepts_common_exact_headings_and_drops_combined_sections(self):
        prompt = (
            "Sandlot trade-analysis evidence: exact offer. "
            "The blocked evidence is: health evidence is incomplete.. "
            "The do-nothing alternative is to keep the outgoing player at 2.0 FP/G. "
            "Roster consequence: moves out 2B and brings in OF. "
            "Internal replacement evidence: Reserve cover is -0.5 FP/G. "
            "Current counter direction: ask for healthy value. Run a deep, on-demand trade analysis"
        )
        raw = """**Verdict: COUNTER**

**Weekly Impact**
Invented 20-point swing.

2. Rest of Season
Invented 100 FPts.

Dynasty:
The young asset has upside, but surgery creates long-term risk.
**Health risk:** Two prior surgeries add uncertainty.
1. Jones still has starter upside if his recovery holds.

### Weekly Impact / Roster Fit
This combined section must not be treated as roster fit.

4. Roster Fit — Uncertainty: Medium
The package adds pitching depth without filling the open infield role.

### Replacement Value and Dynasty
This combined section must not replace either exact section.

Dynasty:
This exact section is allowed.
Weekly Impact / Roster Fit:
This plain combined section must fail closed too.
"""

        repaired = sandlot_skipper.repair_reply(raw, prompt, {})

        self.assertIn("## Verdict: **COUNTER**", repaired)
        self.assertIn("The young asset has upside, but surgery creates long-term risk", repaired)
        self.assertIn("**Health risk:** Two prior surgeries add uncertainty", repaired)
        self.assertIn("1. Jones still has starter upside if his recovery holds", repaired)
        self.assertIn("adds pitching depth without filling the open infield role", repaired)
        self.assertNotIn("Invented 20-point swing", repaired)
        self.assertNotIn("Invented 100 FPts", repaired)
        self.assertNotIn("combined section must not", repaired)
        self.assertNotIn("plain combined section must fail closed", repaired)

    def test_trade_reply_no_source_copy_is_explicitly_conditional(self):
        prompt = (
            "Sandlot trade-analysis evidence: exact offer. "
            "The blocked evidence is: health evidence is incomplete.. "
            "The do-nothing alternative is to keep the outgoing player. "
            "Roster consequence: roster stays balanced. "
            "Internal replacement evidence: No replacement is modeled. "
            "Current counter direction: ask for healthy value. Run a deep, on-demand trade analysis"
        )

        repaired = sandlot_skipper.repair_reply("Verdict: HOLD", prompt, {})

        self.assertIn("if none appear, keep HOLD", repaired)
        self.assertIn("if none appear, treat the research as unverified", repaired)
        self.assertNotIn("review the cited sources", repaired)

    def test_safe_trade_context_truncates_only_between_lines(self):
        line = "[Complete source label](https://example.com/complete-source)"
        value = "\n".join([line] * 80)

        cleaned = sandlot_skipper._safe_trade_context(value, "fallback")

        self.assertTrue(cleaned.endswith("\n…"))
        self.assertNotIn("https://example.com/complete-sour\n", cleaned)
        self.assertEqual(cleaned.removesuffix("\n…").splitlines()[-1], line)

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

    def test_closing_stream_closes_the_provider_response(self):
        class ClosableStream:
            def __init__(self):
                self.closed = False

            def __iter__(self):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="first token"))],
                )
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="second token"))],
                )

            def close(self):
                self.closed = True

        provider_stream = ClosableStream()
        completions = SimpleNamespace(create=lambda **kwargs: provider_stream)
        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        stream = client.stream(
            [{"role": "user", "content": "research this trade"}],
            model_order=("test/model",),
        )
        self.assertEqual(next(stream), ("token", "first token"))
        stream.close()

        self.assertTrue(provider_stream.closed)

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

        sources = [payload for kind, payload in chunks if kind == "source"]
        self.assertEqual(sources[0]["url"], "https://www.mlb.com/player/martin-perez-527048")
        self.assertIn(("web_search_requests", 1), chunks)
        self.assertIn(("token", "web-backed reply"), chunks)
        self.assertIn(("model", "test/model"), chunks)

    def test_stream_keeps_web_search_available_on_model_fallback(self):
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
        self.assertIn("tools", completions.calls[1])

    def test_stream_reports_cumulative_search_requests_across_fallbacks(self):
        usage = SimpleNamespace(server_tool_use=SimpleNamespace(web_search_requests=1))

        class RetryingCompletions:
            def create(self, **kwargs):
                if kwargs["model"] == "primary/model":
                    return [SimpleNamespace(choices=[], usage=usage)]
                return [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="fallback reply"))],
                    ),
                    SimpleNamespace(choices=[], usage=usage),
                ]

        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=RetryingCompletions()))

        chunks = list(
            client.stream(
                [{"role": "user", "content": "find missing player stats"}],
                model_order=("primary/model", "fallback/model"),
                web_search=True,
            )
        )

        self.assertIn(("web_search_requests", 1), chunks)
        self.assertIn(("web_search_requests", 2), chunks)
        self.assertEqual(chunks[-1], ("model", "fallback/model"))

    def test_stream_discards_citations_from_an_empty_failed_attempt(self):
        failed_citation = {
            "type": "url_citation",
            "url_citation": {
                "url": "https://failed.example/source",
                "title": "Failed attempt source",
            },
        }

        class CitationFallbackCompletions:
            def create(self, **kwargs):
                if kwargs["model"] == "primary/model":
                    return [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=None, annotations=[failed_citation]),
                                )
                            ],
                        )
                    ]
                return [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="uncited fallback"))],
                    )
                ]

        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=CitationFallbackCompletions()))

        chunks = list(
            client.stream(
                [{"role": "user", "content": "research this offer"}],
                model_order=("primary/model", "fallback/model"),
                web_search=True,
            )
        )

        self.assertFalse(any(kind == "source" for kind, _ in chunks))
        self.assertIn(("token", "uncited fallback"), chunks)
        self.assertEqual(chunks[-1], ("model", "fallback/model"))

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

    def test_cancel_active_stream_interrupts_a_blocked_provider_iterator(self):
        started = threading.Event()
        released = threading.Event()
        stream_closed = threading.Event()
        client_closed = threading.Event()

        class BlockingStream:
            def __iter__(self):
                return self

            def __next__(self):
                started.set()
                released.wait(timeout=2.0)
                raise StopIteration

            def close(self):
                stream_closed.set()
                released.set()

        class Provider:
            def __init__(self):
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=lambda **kwargs: BlockingStream()),
                )

            def close(self):
                client_closed.set()
                released.set()

        client = sandlot_skipper.SkipperClient(api_key="test-key")
        client.client = Provider()
        finished = threading.Event()

        def consume():
            try:
                list(client.stream([{"role": "user", "content": "research"}], model_order=("test/model",)))
            except RuntimeError:
                pass
            finally:
                finished.set()

        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        self.assertTrue(started.wait(timeout=1.0))

        client.cancel_active_stream()

        self.assertTrue(stream_closed.wait(timeout=1.0))
        self.assertTrue(client_closed.wait(timeout=1.0))
        self.assertTrue(finished.wait(timeout=1.0))

    def test_index_serves_content_hashed_app_bundle(self):
        response = sandlot_index()
        body = response.body.decode()

        self.assertIn("app.js?v=", body)
        self.assertNotIn("app.js?v=frontend-build", body)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")


if __name__ == "__main__":
    unittest.main()
