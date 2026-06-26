import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.idea_vault import IdeaVault
from post_agent.web import render_idea_detail, render_idea_vault


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


if __name__ == "__main__":
    unittest.main()
