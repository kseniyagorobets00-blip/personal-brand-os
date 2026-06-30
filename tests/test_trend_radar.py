import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from post_agent.ai_context import AIContextEngine
from post_agent.idea_vault import IdeaVault
from post_agent.knowledge import KnowledgeBase, KnowledgeCase, KnowledgeDocument
from post_agent.learning import LearningCenter
from post_agent.production import run_production_check
from post_agent.trend_radar import ExternalFeedSourceProvider, TrendRadar
from post_agent.web import _add_trend_to_content_plan, render_trend_radar


class TrendRadarTests(unittest.TestCase):
    def test_trend_radar_refresh_scores_and_caches_topics(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            learning = LearningCenter(root / "learning.json")
            radar = TrendRadar(
                cache_path=root / "cache.json",
                decisions_path=root / "decisions.json",
                seed_path=root / "sources.json",
                learning_center=learning,
            )
            knowledge = KnowledgeBase(root / "documents", root / "index.json")
            knowledge.add_document("MAYRVEDA.md", "MAYRVEDA Customer Experience SOP.".encode("utf-8"))

            with patch.dict("os.environ", {"TREND_RADAR_ENABLE_RSS": "0"}):
                cache = radar.refresh(
                    content_plan={"focus": "Customer Experience and Operations", "content_pillars": ["Customer Experience", "AI"]},
                    documents=knowledge.list_documents(),
                    cases=knowledge.list_cases(),
                    ideas=[],
                )

        self.assertTrue(cache["topics"])
        first = cache["topics"][0]
        self.assertIn("reach_score", first)
        self.assertIn("brand_fit_score", first)
        self.assertIn("best_formats", first)
        self.assertIn("best_rubrics", first)
        self.assertIn("repeat_risk", first)
        self.assertIn("recommendation", first)
        self.assertIn("ai_explanation", first)
        self.assertIn("Внешние", cache["source_status"])
        self.assertIn("source_diagnostics", cache)

    def test_external_feed_provider_is_enabled_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            provider = ExternalFeedSourceProvider(feeds=())

        self.assertTrue(provider.enabled)

    def test_trend_decisions_can_create_candidate_lesson(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            learning = LearningCenter(root / "learning.json")
            radar = TrendRadar(root / "cache.json", root / "decisions.json", root / "sources.json", learning_center=learning)
            with patch.dict("os.environ", {"TREND_RADAR_ENABLE_RSS": "0"}):
                cache = radar.refresh({}, [], [], [])
            topic_id = cache["topics"][0]["id"]

            radar.apply_decision(topic_id, "approved")
            radar.apply_decision(topic_id, "approved")
            radar.apply_decision(topic_id, "approved")
            lessons = learning.list_lessons("candidate")

        self.assertTrue(lessons)

    def test_trend_radar_page_renders_actions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            radar = TrendRadar(root / "cache.json", root / "decisions.json", root / "sources.json")
            with patch.dict("os.environ", {"TREND_RADAR_ENABLE_RSS": "0"}):
                cache = radar.refresh({}, [], [], [])

        html = render_trend_radar(cache)

        self.assertIn("Trend Radar", html)
        self.assertIn("Trend Score", html)
        self.assertIn("Content Potential", html)
        self.assertIn("Соответствие бренду", html)
        self.assertIn("Сохранить в Idea Vault", html)
        self.assertIn("Добавить в Content Plan", html)
        self.assertIn("Почему AI выбрал эту тему?", html)
        self.assertIn("Риск повтора", html)
        self.assertIn("Рекомендация", html)
        self.assertIn("Техническая информация", html)

    def test_trend_radar_page_renders_actions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            radar = TrendRadar(root / "cache.json", root / "decisions.json", root / "sources.json")
            with patch.dict("os.environ", {"TREND_RADAR_ENABLE_RSS": "0"}):
                cache = radar.refresh({}, [], [], [])

        html = render_trend_radar(cache)

        self.assertIn("Радар трендов", html)
        self.assertIn("Оценка тренда", html)
        self.assertIn("Контентный потенциал", html)
        self.assertIn("Соответствие бренду", html)
        self.assertIn("Добавить в идеи", html)
        self.assertIn("Добавить в контент-план", html)
        self.assertIn("О чем на самом деле этот тренд?", html)
        self.assertIn("Какой авторский угол предлагает AI", html)
        self.assertIn("Какие публикации можно сделать", html)
        self.assertIn("Техническая информация", html)

    def test_trend_radar_uses_semantic_documents_cases_and_dedupes_group_reason(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_path = root / "sources.json"
            seed_path.write_text(
                __import__("json").dumps(
                    {
                        "sources": [
                            {
                                "id": "guest-app-1",
                                "title": "Hotel mobile guest app and digital key",
                                "description": "Guest app changes service workflow and staff operations.",
                                "source": "Test source A",
                                "category": "Hospitality",
                                "sources": ["Test source A"],
                                "brand_base": 5.8,
                            },
                            {
                                "id": "guest-app-2",
                                "title": "Hotel mobile guest app and digital key update",
                                "description": "Guest app changes service workflow and staff operations.",
                                "source": "Test source B",
                                "category": "Hospitality",
                                "sources": ["Test source B"],
                                "brand_base": 5.8,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            radar = TrendRadar(root / "cache.json", root / "decisions.json", seed_path)
            documents = [
                KnowledgeDocument(
                    id="doc-1",
                    title="Service operations playbook",
                    original_filename="ops.md",
                    extension=".md",
                    stored_path="ops.md",
                    excerpt="",
                    content_text="",
                    word_count=12,
                    uploaded_at="",
                    semantic_chunks=("Guest journey depends on hotel staff workflow, SOP and operational ownership.",),
                )
            ]
            cases = [
                KnowledgeCase(
                    id="case-1",
                    title="Guest service workflow",
                    company="Hotel Project",
                    what_happened="Digital check-in created pressure on service teams.",
                    reason="The workflow did not have clear ownership.",
                    solution="Mapped service steps and SOP.",
                    result="More predictable guest experience.",
                    public_usage="Use as hospitality operations case.",
                    key_topics=("hospitality", "operations", "customer experience"),
                    platforms=("LinkedIn", "Telegram"),
                    created_at="",
                    key_takeaways=("Mobile service needs internal ownership", "SOP protects guest experience"),
                )
            ]

            with patch.dict("os.environ", {"TREND_RADAR_ENABLE_RSS": "0"}):
                cache = radar.refresh({}, documents, cases, [])

        first = cache["topics"][0]
        self.assertIn("Service operations playbook", first["knowledge_materials"])
        self.assertTrue(first["case_insights"])
        self.assertGreaterEqual(first["brand_fit_score"], 7.0)
        self.assertLessEqual(str(first["why_trend"]).count("Похожие сигналы найдены"), 1)

    def test_ai_context_engine_collects_shared_context(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge = KnowledgeBase(root / "documents", root / "index.json")
            knowledge.add_document("ops.md", "Operations and Customer Experience SOP.".encode("utf-8"))
            vault = IdeaVault(root / "ideas.json")
            vault.add_idea("AI in operations", "Use AI only when process ownership is clear.")
            content_plan_path = root / "content_plan.json"
            content_plan_path.write_text(
                '{"week":"test","focus":"CX","month_focus":"Operations","planned_publications":[{"platform":"LinkedIn","pillar":"Аналитика","format":"экспертный пост","topic":"CX ops","status":"planned"}]}',
                encoding="utf-8",
            )
            engine = AIContextEngine(
                knowledge_base=knowledge,
                idea_vault=vault,
                seed_repository=type("Repo", (), {"load_content_plan": lambda self: __import__("json").loads(content_plan_path.read_text(encoding="utf-8")), "load": lambda self: []})(),
                editorial_strategy_path=root / "editorial_strategy.json",
                trend_cache_path=root / "trend_cache.json",
            )

            context = engine.build({"platform": "LinkedIn", "rubric": "Аналитика", "format": "экспертный пост"})

        self.assertIn("author_brain", context)
        self.assertIn("writing_dna", context)
        self.assertIn("editorial_strategy", context)
        self.assertIn("semantic_chunks", context)
        self.assertEqual(context["month_focus"], "Operations")
        self.assertEqual(context["selected"]["platform"], "LinkedIn")

    def test_trend_can_be_saved_to_idea_vault(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = IdeaVault(root / "ideas.json")
            radar = TrendRadar(root / "cache.json", root / "decisions.json", root / "sources.json")
            with patch.dict("os.environ", {"TREND_RADAR_ENABLE_RSS": "0"}):
                cache = radar.refresh({}, [], [], [])
            topic = cache["topics"][0]

            vault.add_idea(topic["title"], topic["description"], source="Trend Radar", platforms=tuple(topic["best_formats"]))
            ideas = vault.list_ideas()

        self.assertEqual(ideas[0].source, "Trend Radar")

    def test_production_check_returns_rows(self) -> None:
        result = run_production_check(Path.cwd())

        self.assertTrue(result.rows)


if __name__ == "__main__":
    unittest.main()
