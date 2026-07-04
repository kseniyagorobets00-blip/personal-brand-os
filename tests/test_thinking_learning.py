import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.knowledge import KnowledgeBase
from post_agent.knowledge_graph import KnowledgeGraph
from post_agent.learning import LearningCenter
from post_agent.memory import MemoryInbox
from post_agent.thinking_engine import ThinkingEngine
from post_agent.web import render_learning_center


class ThinkingLearningTests(unittest.TestCase):
    def test_document_upload_creates_memory_inbox_item_and_graph(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = MemoryInbox(root / "memory" / "inbox.json")
            graph = KnowledgeGraph(root / "knowledge" / "graph.json")
            base = KnowledgeBase(root / "documents", root / "index.json", memory_inbox=inbox, knowledge_graph=graph)

            document = base.add_document(
                "MAYRVEDA-note.md",
                "MAYRVEDA Customer Experience SOP hospitality case.".encode("utf-8"),
            )
            inbox_items = inbox.list_items("pending")
            graph_data = graph.read_graph()

        self.assertEqual(document.analysis["companies"], ["MAYRVEDA"])
        self.assertEqual(len(inbox_items), 1)
        self.assertIn("Customer Experience", inbox_items[0].extracted["themes"])
        self.assertGreaterEqual(len(graph_data["nodes"]), 1)

    def test_thinking_engine_uses_case_before_generation(self) -> None:
        context = {
            "target_publication": {
                "platform": "LinkedIn",
                "topic": "Customer Experience and SOP",
                "summary": "service handoff",
                "goal": "show operational maturity",
            },
            "author_brain": {
                "thinking_mode": "Observation",
                "writing_dna": {"main_goal": "living practitioner"},
                "case_candidates": [{"title": "MAYRVEDA handoff"}],
                "knowledge_observations": [{"title": "SOP note", "excerpt": "SOP protects service."}],
            },
            "knowledge_graph_links": [{"label": "Luxury Hospitality", "type": "theme", "relation": "mentions"}],
        }

        result = ThinkingEngine().think(context)

        self.assertEqual(result["mode"], "Case")
        self.assertIn("MAYRVEDA", result["relevant_case"]["title"])
        self.assertTrue(any("Writing DNA" in item for item in result["transparency"]))

    def test_learning_center_creates_candidate_lessons(self) -> None:
        with TemporaryDirectory() as directory:
            center = LearningCenter(Path(directory) / "lessons.json")
            lesson = center.create_candidate_from_feedback("Начало скучное. Я бы начала с разговора.", "Draft")

            center.update(lesson.id, "accepted")
            accepted = center.list_lessons("accepted")

        self.assertTrue(lesson.rule)
        self.assertEqual(accepted[0].id, lesson.id)

    def test_learning_center_page_renders_candidates_and_memory_inbox(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            center = LearningCenter(root / "lessons.json")
            inbox = MemoryInbox(root / "inbox.json")
            graph = KnowledgeGraph(root / "graph.json")
            center.create_candidate("Писать живее.", "Пользователь попросил меньше академичности.", 80)
            inbox.add_item("document", "1", "Анализ документа", "summary", {"themes": ["SOP"]})

            html = render_learning_center(center, inbox, graph)

        self.assertIn("Центр обучения", html)
        self.assertIn("Предложенные правила", html)
        self.assertIn("Входящие памяти", html)
        self.assertIn("Писать живее.", html)


if __name__ == "__main__":
    unittest.main()
