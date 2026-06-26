import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.idea_vault import IdeaVault
from post_agent.knowledge import KnowledgeBase
from post_agent.learning import LearningCenter
from post_agent.production import run_production_check
from post_agent.trend_radar import TrendRadar
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

    def test_trend_decisions_can_create_candidate_lesson(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            learning = LearningCenter(root / "learning.json")
            radar = TrendRadar(root / "cache.json", root / "decisions.json", root / "sources.json", learning_center=learning)
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
            cache = radar.refresh({}, [], [], [])

            html = render_trend_radar(cache)

        self.assertIn("Trend Radar", html)
        self.assertIn("Потенциал охвата", html)
        self.assertIn("Соответствие бренду", html)
        self.assertIn("Сохранить в Idea Vault", html)
        self.assertIn("Добавить в Content Plan", html)

    def test_trend_can_be_saved_to_idea_vault(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = IdeaVault(root / "ideas.json")
            radar = TrendRadar(root / "cache.json", root / "decisions.json", root / "sources.json")
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
