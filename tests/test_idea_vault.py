import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from post_agent.idea_vault import IdeaVault
from post_agent.web import _add_idea_to_content_plan, render_idea_detail, render_idea_vault


class IdeaVaultTests(unittest.TestCase):
    def test_idea_can_be_saved_listed_and_opened(self) -> None:
        with TemporaryDirectory() as directory:
            vault = IdeaVault(Path(directory) / "ideas.json")
            idea = vault.add_idea(
                title="CX as operational discipline",
                description="Explain CX through processes and responsibility.",
                source="Daily Brief",
                platforms=("LinkedIn", "Telegram"),
            )

            ideas = vault.list_ideas()
            loaded = vault.get_idea(idea.id)

        self.assertEqual(len(ideas), 1)
        self.assertEqual(loaded.title, "CX as operational discipline")
        self.assertEqual(loaded.status, "New")
        self.assertIn("LinkedIn", loaded.platforms)

    def test_status_can_be_changed_and_idea_deleted(self) -> None:
        with TemporaryDirectory() as directory:
            vault = IdeaVault(Path(directory) / "ideas.json")
            idea = vault.add_idea("SOP as care", "Use SOP as hospitality angle.")

            updated = vault.update_status(idea.id, "Drafted")
            loaded = vault.get_idea(idea.id)
            deleted = vault.delete_idea(idea.id)

            self.assertTrue(updated)
            self.assertEqual(loaded.status, "Drafted")
            self.assertTrue(deleted)
            self.assertEqual(vault.list_ideas(), [])

    def test_idea_vault_ui_renders_list_and_detail_actions(self) -> None:
        with TemporaryDirectory() as directory:
            vault = IdeaVault(Path(directory) / "ideas.json")
            idea = vault.add_idea("AI accelerates chaos", "A strong operations angle.")

            list_html = render_idea_vault(vault.list_ideas())
            detail_html = render_idea_detail(idea)

        self.assertIn("Идеи", list_html)
        self.assertIn("Добавить идею вручную", list_html)
        self.assertIn("AI accelerates chaos", list_html)
        self.assertIn("Новая", list_html)
        self.assertIn("Обновить статус", detail_html)
        self.assertIn("Удалить идею", detail_html)
        self.assertIn("Добавить в контент-план", detail_html)
        self.assertIn(f"/ideas/plan/{idea.id}", detail_html)

    def test_idea_is_added_to_content_plan_once(self) -> None:
        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "week_start": "2026-07-06",
                        "week_end": "2026-07-12",
                        "planned_publications": [],
                    }
                ),
                encoding="utf-8",
            )
            vault = IdeaVault(Path(directory) / "ideas.json")
            idea = vault.add_idea("CX держится на операциях", "Развить через SOP.", platforms=("LinkedIn",))

            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path):
                first = _add_idea_to_content_plan(idea)
                second = _add_idea_to_content_plan(idea)
                saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertTrue(first)
        self.assertFalse(second)
        publications = saved["planned_publications"]
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["topic"], "CX держится на операциях")
        self.assertEqual(publications[0]["platform"], "LinkedIn")
        self.assertEqual(publications[0]["status"], "idea")


if __name__ == "__main__":
    unittest.main()
