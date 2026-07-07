import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.ai_gateway import AIGatewayConfig, load_ai_config, resolve_model_for_action
from post_agent.ai_pipeline import AIPipeline, _needs_revision, load_ai_result, load_ai_status
from post_agent.daily_brief import DailyBriefService
from post_agent.web import render_daily_brief


class AIPipelineTests(unittest.TestCase):
    def test_draft_language_triggers_revision(self) -> None:
        english = "Among the available angles, the strongest fit today is the CX operations thesis about ownership and handoffs."
        russian = "Сегодня заметила: сервис ломается не в контакте с гостем, а на стыке ролей, где никто не владеет переходом."
        good = {"draft": russian, "author_fit_score": "9"}
        bad = {"draft": english, "author_fit_score": "9"}
        # Russian platform must reject an English draft, accept a Russian one.
        self.assertTrue(_needs_revision(bad, "VC"))
        self.assertFalse(_needs_revision(good, "VC"))
        # LinkedIn expects English, so a Russian draft must be revised.
        self.assertTrue(_needs_revision(good, "LinkedIn"))
        self.assertFalse(_needs_revision(bad, "LinkedIn"))

    def test_env_config_loads_from_file(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "PROXY_API_KEY=test-key\nPROXY_API_BASE_URL=https://example.test/v1\nAI_MODEL=test-model\n",
                encoding="utf-8",
            )

            config = load_ai_config(env_path)

        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertEqual(config.model, "test-model")
        self.assertTrue(config.is_configured)

    def test_legacy_premium_only_env_splits_into_mini_and_premium(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "PROXY_API_KEY=test-key\nPROXY_API_BASE_URL=https://example.test/v1\nAI_MODEL=gpt-5.4-nano\n",
                encoding="utf-8",
            )
            config = load_ai_config(env_path)
        self.assertEqual(config.model, "gpt-4o-mini")
        self.assertEqual(config.premium_model, "gpt-5.4-nano")

    def test_premium_model_used_only_for_deep_actions(self) -> None:
        config = AIGatewayConfig("k", "https://example.test/v1", "gpt-4o-mini", "gpt-5.4-nano")
        self.assertEqual(resolve_model_for_action(config, "text_post_generate"), "gpt-4o-mini")
        self.assertEqual(resolve_model_for_action(config, "trend_radar_synthesize"), "gpt-4o-mini")
        self.assertEqual(resolve_model_for_action(config, "ai_pipeline_draft"), "gpt-5.4-nano")
        self.assertEqual(resolve_model_for_action(config, "content_plan_full"), "gpt-5.4-nano")

    def test_pipeline_without_proxyapi_does_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            result_path = Path(directory) / "daily_brief_ai.json"
            status_path = Path(directory) / "status.json"
            pipeline = AIPipeline(
                result_path=result_path,
                status_path=status_path,
            )
            pipeline.gateway.config = AIGatewayConfig("", "", "", "")

            result = pipeline.run()
            status = load_ai_status(status_path)

        self.assertIsNone(result)
        self.assertEqual(status.state, "not_configured")
        self.assertIn("ProxyAPI не настроен", status.message)

    def test_pipeline_saves_real_ai_shape_to_cache(self) -> None:
        class FakeGateway:
            def is_configured(self) -> bool:
                return True

            def model_for(self, action=None):
                return "gpt-5.4-nano"

            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, object]:
                self.user_prompt = user_prompt
                return {
                    "main_topic": "AI тема дня",
                    "daily_recommendation": "Опубликовать AI-позицию",
                    "choice_reason": "Тема связана с Content Plan и памятью.",
                    "publication_goal": "Показать экспертность.",
                    "main_idea": "AI усиливает зрелые процессы.",
                    "why_today": "Это стоит в недельном плане.",
                    "recommended_materials": [{"title": "MAYRVEDA", "type": "Кейс", "reason": "Поддерживает CX-угол."}],
                    "ideas": ["Сравнить процесс и сервис"],
                    "thinking_mode": "Observation",
                    "author_fit_score": "9",
                    "author_fit_notes": "Звучит живо.",
                    "draft": (
                        "Yesterday I noticed how quickly AI exposes weak handoffs in service operations. "
                        "When ownership is fuzzy, customer experience breaks before the model ever does.\n\n"
                        "Final line"
                    ),
                }

        gateway = FakeGateway()
        with TemporaryDirectory() as directory:
            result_path = Path(directory) / "daily_brief_ai.json"
            status_path = Path(directory) / "status.json"
            pipeline = AIPipeline(
                gateway=gateway,
                result_path=result_path,
                status_path=status_path,
            )

            result = pipeline.run()
            cached = load_ai_result(result_path)
            status = load_ai_status(status_path)

        self.assertEqual(result["main_topic"], "AI тема дня")
        self.assertIn("AI exposes weak handoffs", cached["draft"])
        self.assertIn("author_brain", gateway.user_prompt)
        self.assertIn("thinking_engine", gateway.user_prompt)
        self.assertIn("knowledge_graph", gateway.user_prompt)
        self.assertIn("lessons", gateway.user_prompt)
        self.assertIn("thinking_engine", cached)
        self.assertIn("thinking_transparency", cached)
        self.assertNotIn('"author_profile"', gateway.user_prompt)
        self.assertNotIn('"knowledge"', gateway.user_prompt)
        self.assertEqual(status.state, "completed")

    def test_daily_brief_drafts_do_not_expose_author_profile_rules(self) -> None:
        forbidden = (
            "Стиль:",
            "Структура:",
            "Правило платформы:",
            "Без определения темы",
            "Можно:",
            "Цель публикации:",
            "Основная мысль:",
            "Краткая структура:",
        )

        brief = DailyBriefService().build_today()
        draft_text = "\n\n".join(draft.text for draft in brief.drafts)

        for marker in forbidden:
            self.assertNotIn(marker, draft_text)

    def test_daily_brief_renders_ai_controls_and_local_trends(self) -> None:
        html = render_daily_brief(DailyBriefService().build_today())

        self.assertIn("AI-анализ", html)
        self.assertIn("Обновить AI-анализ", html)
        self.assertIn("Диагностика AI", html)
        self.assertIn("Python", html)
        self.assertIn("Рабочая папка", html)
        self.assertIn(".env загружен", html)
        self.assertIn("ProxyAPI настроен", html)
        self.assertIn("Последняя ошибка AI-анализа", html)
        self.assertNotIn("Тренды и сигналы", html)
        self.assertNotIn("Локальные данные", html)


if __name__ == "__main__":
    unittest.main()
