import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.author_brain import AuthorBrain
from post_agent.memory_notes import MemoryNoteStore
from post_agent.web import render_knowledge


class MemoryNoteStoreTests(unittest.TestCase):
    def test_add_list_and_delete_notes_by_category(self) -> None:
        with TemporaryDirectory() as directory:
            store = MemoryNoteStore(Path(directory) / "notes.json")
            obs = store.add_note("observation", "Гости реагируют на разрыв, а не на сервис")
            store.add_note("principle", "Начинать с наблюдения")
            self.assertIsNotNone(obs)

            self.assertEqual(len(store.list_notes("observation")), 1)
            self.assertEqual(len(store.list_notes("principle")), 1)
            self.assertEqual(len(store.list_notes()), 2)

            self.assertTrue(store.delete_note(obs.id))
            self.assertEqual(len(store.list_notes("observation")), 0)

    def test_empty_or_bad_category_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = MemoryNoteStore(Path(directory) / "notes.json")
            self.assertIsNone(store.add_note("observation", "   "))
            self.assertIsNone(store.add_note("bogus", "text"))
            self.assertEqual(store.list_notes(), [])


class MemoryNotesFeedAiTests(unittest.TestCase):
    def test_notes_flow_into_author_brain(self) -> None:
        brain = AuthorBrain(
            author_profile={},
            writing_dna={},
            documents=[],
            cases=[],
            ideas=[],
            memory_notes={
                "principle": ["Начинать с наблюдения"],
                "observation": ["Гости реагируют на разрыв"],
                "story": ["Ресторан: однажды на открытии"],
            },
        )
        profile = brain.build_profile()
        self.assertIn("Начинать с наблюдения", profile["author_principles"])
        self.assertIn("Гости реагируют на разрыв", profile["author_observations"])

        full = brain.build({"platform": "Telegram", "topic": "сервис"})
        self.assertIn("Начинать с наблюдения", full["voice_principles"])
        self.assertTrue(any("Гости реагируют" in o["excerpt"] for o in full["knowledge_observations"]))
        self.assertTrue(any("Ресторан" in s.get("text", "") for s in full["examples_and_stories"]))


class MemoryPageTests(unittest.TestCase):
    def test_each_section_shows_only_its_group_and_a_form(self) -> None:
        for section in ("documents", "cases", "ideas", "observations", "principles", "stories"):
            html = render_knowledge([], cases=[], ideas=[], notes=[], section=section)
            self.assertIn("memory-tab active", html)
            self.assertIn("<form", html)

    def test_documents_upload_only_on_documents_section(self) -> None:
        docs_html = render_knowledge([], cases=[], ideas=[], notes=[], section="documents")
        principles_html = render_knowledge([], cases=[], ideas=[], notes=[], section="principles")
        self.assertIn("/knowledge/upload", docs_html)
        self.assertNotIn("/knowledge/upload", principles_html)
        self.assertIn("/knowledge/notes/add", principles_html)


if __name__ == "__main__":
    unittest.main()
