import unittest

from post_agent.author_brain import AuthorBrain, FORBIDDEN_OPENINGS
from post_agent.author_profile import AuthorProfileRepository
from post_agent.knowledge import KnowledgeBase
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
                "topic": "Customer Experience как следствие операционной дисциплины",
                "summary": "CX зависит от operations, SOP и hospitality.",
            }
        )

        self.assertEqual(brain["thinking_mode"], "Case")
        self.assertTrue(any("MAYRVEDA" in str(item.get("title", "")) for item in brain["case_candidates"]))
        self.assertIn("в современном мире", FORBIDDEN_OPENINGS)
        self.assertIn("Похоже ли это на Ксению?", brain["self_check"]["question"])


if __name__ == "__main__":
    unittest.main()
