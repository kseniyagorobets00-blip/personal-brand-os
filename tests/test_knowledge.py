import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from post_agent.knowledge import KnowledgeBase
from post_agent.web import render_knowledge, render_knowledge_document


class KnowledgeBaseTests(unittest.TestCase):
    def test_txt_document_uploads_and_indexes(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document(
                "operations-note.txt",
                "Customer Experience starts with operational discipline.".encode("utf-8"),
            )

            documents = base.list_documents()

        self.assertEqual(len(documents), 1)
        self.assertEqual(document.extension, ".txt")
        self.assertIn("operational discipline", document.excerpt)
        self.assertGreater(document.word_count, 0)

    def test_markdown_document_uploads_and_can_be_deleted(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("framework.md", b"# Framework\n\nSOP as care.")

            deleted = base.delete_document(document.id)

            self.assertTrue(deleted)
            self.assertEqual(base.list_documents(), [])

    def test_docx_text_is_indexed(self) -> None:
        with TemporaryDirectory() as directory:
            docx_path = Path(directory) / "case.docx"
            with ZipFile(docx_path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    "<w:document><w:body><w:p><w:r><w:t>MAYRVEDA service case</w:t></w:r></w:p></w:body></w:document>",
                )
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("case.docx", docx_path.read_bytes())

        self.assertIn("MAYRVEDA service case", document.excerpt)

    def test_pdf_text_stream_is_extracted_and_indexed(self) -> None:
        pdf_stream = zlib.compress(b"BT /F1 12 Tf 72 720 Td (CV Gorobets Customer Experience Operations) Tj ET")
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Length "
            + str(len(pdf_stream)).encode("ascii")
            + b" /Filter /FlateDecode >>\nstream\n"
            + pdf_stream
            + b"\nendstream\nendobj\n%%EOF"
        )
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("cv.pdf", pdf_bytes)

        self.assertIn("CV Gorobets", document.content_text)
        self.assertGreater(document.word_count, 0)
        self.assertNotIn("Текстовое содержание не найдено", document.excerpt)

    def test_knowledge_ui_renders_library_and_document(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("note.txt", "Service Design note".encode("utf-8"))

            library_html = render_knowledge(base.list_documents(), cases=base.list_cases())
            document_html = render_knowledge_document(document)

        self.assertIn("Память", library_html)
        self.assertIn("Загрузить документ", library_html)
        self.assertIn("Кейсы", library_html)
        self.assertIn("Наблюдения", library_html)
        self.assertIn("Принципы", library_html)
        self.assertIn("Истории", library_html)
        self.assertIn("note", library_html)
        self.assertIn("Service Design note", document_html)
        self.assertIn("Удалить документ", document_html)

    def test_knowledge_ui_renders_upload_error(self) -> None:
        html = render_knowledge([], cases=[], upload_error="Не удалось загрузить документ.")

        self.assertIn("Не удалось загрузить документ.", html)
        self.assertIn("error-note", html)

    def test_search_finds_documents_by_title_and_content_with_reason(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            base.add_document(
                "MAYRVEDA-case.md",
                "Customer Experience and SOP handoff in hospitality operations.".encode("utf-8"),
            )

            title_results = base.search("MAYRVEDA")
            content_results = base.search("hospitality")

        self.assertEqual(title_results[0].document.title, "MAYRVEDA-case")
        self.assertIn("названию", title_results[0].reason)
        self.assertIn("содержимому", content_results[0].reason)

    def test_recommendations_explain_why_document_is_used(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            base.add_document(
                "MAYRVEDA-cx-note.md",
                "Customer Experience depends on operational discipline.".encode("utf-8"),
            )

            results = base.recommend_for_topics(["Customer Experience как следствие операционной дисциплины"])

        self.assertTrue(results)
        self.assertIn("MAYRVEDA", results[0].reason)
        self.assertIn("Customer Experience", results[0].reason)

    def test_knowledge_ui_renders_documents_folder_without_search_block(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            base.add_document("sop.md", "SOP protects Customer Experience.".encode("utf-8"))

            html = render_knowledge(base.list_documents(), cases=base.list_cases(), section="documents")

        self.assertNotIn("Поиск по памяти", html)
        self.assertIn("sop", html)

    def test_cases_can_be_saved_and_rendered(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(
                Path(directory) / "documents",
                Path(directory) / "index.json",
                Path(directory) / "cases.json",
            )
            case = base.add_case(
                title="MAYRVEDA handoff",
                company="MAYRVEDA",
                what_happened="Сервис ломался на передаче ответственности.",
                reason="Не было владельца перехода.",
                solution="Уточнили SOP и роли.",
                result="Сервис стал предсказуемее.",
                public_usage="Только обезличенно",
                key_topics=("Customer Experience", "SOP"),
                platforms=("LinkedIn", "Telegram"),
            )
            html = render_knowledge(base.list_documents(), cases=base.list_cases(), section="cases")
            saved_title = base.list_cases()[0].title

        self.assertEqual(saved_title, case.title)
        self.assertIn("Кейсы", html)
        self.assertIn("MAYRVEDA handoff", html)
        self.assertIn("Сервис ломался", html)


if __name__ == "__main__":
    unittest.main()
