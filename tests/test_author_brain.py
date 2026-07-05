import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.author_brain import AuthorBrain, AuthorBrainRepository, FORBIDDEN_OPENINGS
from post_agent.author_profile import AuthorProfileRepository
from post_agent.knowledge import KnowledgeBase
from post_agent.writing_dna import WritingDNARepository


class AuthorBrainTests(unittest.TestCase):
    def test_ai_document_facts_feed_the_author_brain(self) -> None:
        class MockGateway:
            def is_configured(self):
                return True

            def complete_json(self, system, user, **kwargs):
                return {
                    "companies": ["Мой Отель"],
                    "roles": ["Операционный директор"],
                    "skills": ["Operations", "CX"],
                    "cases": [{"title": "Отток", "problem": "высокий отток", "action": "SOP", "result": "-30%"}],
                    "achievements": ["выручка +25%"],
                    "themes": ["Luxury Hospitality"],
                }

        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("resume.md", "# Резюме\nОперационный директор.".encode("utf-8"))
            base.enrich_document_with_ai(document.id, gateway=MockGateway())

            brain = AuthorBrain(
                author_profile={},
                writing_dna={},
                documents=base.list_documents(),
                cases=[],
                ideas=[],
            ).build_profile()

        self.assertIn("Мой Отель", brain["background"]["companies"])
        self.assertIn("выручка +25%", brain["background"]["achievements"])
        self.assertTrue(any(theme["name"] == "Luxury Hospitality" for theme in brain["main_themes"]))
        self.assertTrue(any(case.get("result") == "-30%" for case in brain["cases"]))

    def test_author_brain_uses_knowledge_case_notes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge = KnowledgeBase(root / "documents", root / "index.json")
            knowledge.add_document(
                "MAYRVEDA-cx-operations-note.md",
                (
                    "# MAYRVEDA: Customer Experience and Operations\n\n"
                    "Кейс MAYRVEDA показывает, что Customer Experience зависит от операционной дисциплины, "
                    "точек передачи ответственности и ясных SOP."
                ).encode("utf-8"),
            )
            brain = AuthorBrain(
                author_profile=AuthorProfileRepository().load_raw(),
                writing_dna=WritingDNARepository().load_raw(),
                documents=knowledge.list_documents(),
                cases=knowledge.list_cases(),
                ideas=[],
            ).build(
                {
                    "platform": "LinkedIn",
                    "topic": "Customer Experience как следствие операционной дисциплины",
                    "summary": "CX зависит от operations, SOP и hospitality.",
                }
            )

        self.assertEqual(brain["thinking_mode"], "Case")
        self.assertTrue(any("MAYRVEDA" in str(item.get("title", "")) for item in brain["case_candidates"]))
        self.assertIn("в современном мире", FORBIDDEN_OPENINGS)
        self.assertIn("Похоже ли это на Ксению?", brain["self_check"]["question"])

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
        self.assertTrue(any(item["name"] == "операции и процессы" for item in profile["main_themes"]))
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

    def test_author_brain_repository_preserves_manual_author_base(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = AuthorBrainRepository(root / "profile.json", root / "status.json")
            repository.save_profile(
                {
                    "main_themes": [{"name": "ручная тема", "score": 90, "evidence": [], "risk": "", "source": "manual"}],
                    "key_ideas": [{"idea": "ручная идея", "belief": "ручная идея", "evidence_count": 1, "repeat_risk": "low", "source": "manual"}],
                    "manual_author_base": {"main_themes": True, "key_ideas": True},
                }
            )

            merged = repository.apply_manual_overrides(
                {
                    "main_themes": [{"name": "авто тема", "score": 80}],
                    "key_ideas": [{"idea": "авто идея"}],
                }
            )

            self.assertEqual(merged["main_themes"][0]["name"], "ручная тема")
            self.assertEqual(merged["key_ideas"][0]["idea"], "ручная идея")

if __name__ == "__main__":
    unittest.main()
