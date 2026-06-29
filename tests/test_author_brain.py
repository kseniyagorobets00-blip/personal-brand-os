import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.author_brain import AuthorBrain, AuthorBrainRepository, FORBIDDEN_OPENINGS
from post_agent.author_profile import AuthorProfileRepository
from post_agent.knowledge import KnowledgeBase
from post_agent.web import render_author_brain
from post_agent.writing_dna import WritingDNARepository


class AuthorBrainTests(unittest.TestCase):
    def test_author_brain_uses_knowledge_case_notes(self) -> None:
        knowledge = KnowledgeBase()
        knowledge.ensure_seed_documents()
        brain = AuthorBrain(
            author_profile=AuthorProfileRepository().load_raw(),
            writing_dna=WritingDNARepository().load_raw(),
            documents=knowledge.list_documents(),
            cases=knowledge.list_cases(),
            ideas=[],
        ).build(
            {
                "platform": "LinkedIn",
                "topic": "Customer Experience РєР°Рє СЃР»РµРґСЃС‚РІРёРµ РѕРїРµСЂР°С†РёРѕРЅРЅРѕР№ РґРёСЃС†РёРїР»РёРЅС‹",
                "summary": "CX Р·Р°РІРёСЃРёС‚ РѕС‚ operations, SOP Рё hospitality.",
            }
        )

        self.assertEqual(brain["thinking_mode"], "Case")
        self.assertTrue(any("MAYRVEDA" in str(item.get("title", "")) for item in brain["case_candidates"]))
        self.assertIn("РІ СЃРѕРІСЂРµРјРµРЅРЅРѕРј РјРёСЂРµ", FORBIDDEN_OPENINGS)
        self.assertIn("РџРѕС…РѕР¶Рµ Р»Рё СЌС‚Рѕ РЅР° РљСЃРµРЅРёСЋ?", brain["self_check"]["question"])

    def test_author_brain_20_builds_structured_profile_and_similarity(self) -> None:
        knowledge = KnowledgeBase()
        knowledge.ensure_seed_documents()
        builder = AuthorBrain(
            author_profile=AuthorProfileRepository().load_raw(),
            writing_dna=WritingDNARepository().load_raw(),
            documents=knowledge.list_documents(),
            cases=knowledge.list_cases(),
            ideas=[],
        )

        profile = builder.build_profile()
        brain = builder.build({"topic": "SOP protects customer experience in hospitality operations"})

        self.assertEqual(profile["version"], "2.0")
        self.assertTrue(any(item["name"] == "operations" for item in profile["main_themes"]))
        self.assertIn("key_ideas", profile)
        self.assertIn("anti_repetition", brain)
        self.assertIn("similarity_report", brain)

    def test_author_brain_repository_keeps_saved_profile_and_status(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge = KnowledgeBase(root / "documents", root / "index.json")
            knowledge.add_document("case.md", b"MAYRVEDA Customer Experience SOP operations result.")
            repository = AuthorBrainRepository(root / "profile.json", root / "status.json")
            profile = repository.refresh(
                AuthorBrain(
                    author_profile={},
                    writing_dna={},
                    documents=knowledge.list_documents(),
                    cases=knowledge.list_cases(),
                    ideas=[],
                )
            )

            self.assertEqual(repository.load_status().state, "completed")
            self.assertEqual(repository.load_profile()["version"], "2.0")
            self.assertEqual(profile["source_counts"]["documents"], 1)

    def test_author_brain_page_renders_refresh_button(self) -> None:
        with TemporaryDirectory() as directory:
            repository = AuthorBrainRepository(Path(directory) / "profile.json", Path(directory) / "status.json")
            html = render_author_brain(repository.empty_profile(), repository.load_status())

            self.assertIn("Author Brain", html)
            self.assertIn("/author-brain/refresh", html)
            self.assertIn("Обновить Author Brain", html)


if __name__ == "__main__":
    unittest.main()
