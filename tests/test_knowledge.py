import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from post_agent.knowledge import KnowledgeBase, extract_pdf_text
from post_agent.web import render_knowledge, render_knowledge_document


class KnowledgeBaseTests(unittest.TestCase):
    def test_ai_enrichment_adds_document_specific_facts(self) -> None:
        class MockGateway:
            def is_configured(self):
                return True

            def complete_json(self, system, user):
                return {
                    "summary": "Резюме операционного директора",
                    "companies": ["Мой Отель"],
                    "roles": ["Операционный директор"],
                    "skills": ["Operations", "CX"],
                    "cases": [{"title": "Отток", "problem": "высокий отток", "action": "внедрила SOP", "result": "-30%"}],
                    "achievements": ["выручка +25%"],
                    "themes": ["Hospitality"],
                    "content_angles": ["как SOP спасает сервис"],
                    "quotes": ["сервис держится на стандартах"],
                }

        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("resume.md", "# Резюме\nОперационный директор, компания Мой Отель.".encode("utf-8"))

            self.assertNotIn("ai", document.analysis or {})  # not enriched synchronously
            self.assertTrue(base.enrich_document_with_ai(document.id, gateway=MockGateway()))

            enriched = base.get_document(document.id)
            ai = (enriched.analysis or {}).get("ai", {})
            self.assertEqual(ai["companies"], ["Мой Отель"])
            self.assertEqual(ai["cases"][0]["result"], "-30%")
            self.assertIn("выручка +25%", ai["achievements"])
            # and it reaches the memory item that feeds the AI
            self.assertIn("ai", base.memory_inbox.list_items()[0].extracted)

    def test_ai_enrichment_no_op_without_ai(self) -> None:
        class Off:
            def is_configured(self):
                return False

            def complete_json(self, system, user):
                raise AssertionError("must not be called when AI is off")

        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document("note.txt", "Просто заметка.".encode("utf-8"))
            self.assertFalse(base.enrich_document_with_ai(document.id, gateway=Off()))

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
        self.assertEqual(document.document_metadata["document_type"], "note")
        self.assertTrue(document.chunk_metadata)
        self.assertIn("summary", document.document_metadata)

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

    def test_pdf_binary_garbage_falls_back_to_readable_extractor(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "portfolio.pdf"
            pdf_path.write_bytes(b"%PDF-1.7 binary placeholder")

            with (
                patch("post_agent.knowledge.extract_pdf_text_with_pymupdf", return_value="A5=80\n@>BD\n1 0 obj\nstream\n/FlateDecode"),
                patch("post_agent.knowledge.extract_pdf_text_with_pdfplumber", return_value="Portfolio\n\nCustomer Experience Operations\nMAYRVEDA case"),
                patch("post_agent.knowledge.extract_pdf_text_with_pypdf", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_from_streams", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_ocr", return_value=""),
            ):
                text = extract_pdf_text(pdf_path)

        self.assertIn("Portfolio", text)
        self.assertIn("Customer Experience Operations", text)
        self.assertNotIn("@>BD", text)
        self.assertNotIn("FlateDecode", text)

    def test_pdf_stream_garbage_uses_ocr_instead_of_indexing_objects(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "cv.pdf"
            pdf_path.write_bytes(b"%PDF-1.7 binary placeholder")

            with (
                patch("post_agent.knowledge.extract_pdf_text_with_pymupdf", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_pdfplumber", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_pypdf", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_from_streams", return_value="Page 1\nA5=80\n@>BD\n12 0 obj\nendstream"),
                patch("post_agent.knowledge.extract_pdf_text_with_ocr", return_value="CV\n\nCustomer Experience leader\nOperations portfolio"),
            ):
                text = extract_pdf_text(pdf_path)

        self.assertIn("Customer Experience leader", text)
        self.assertNotIn("A5=80", text)
        self.assertNotIn("endstream", text)

    def test_pdf_text_is_saved_as_clean_markdown(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "portfolio.pdf"
            pdf_path.write_bytes(b"%PDF-1.7 binary placeholder")

            raw_text = (
                "--- Page 1 ---\n"
                "Ksenia GorobetsConsultant\n\n"
                "Rea\n\n"
                "Experience\n"
                "Customer Experience\n"
                "Operations leader across teams\n"
                "--- Page 2 ---\n"
                "Projects\n"
                "MAYRVEDA service design."
            )
            with (
                patch("post_agent.knowledge.extract_pdf_text_with_pymupdf", return_value=raw_text),
                patch("post_agent.knowledge.extract_pdf_text_with_pdfplumber", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_pypdf", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_from_streams", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_ocr", return_value=""),
            ):
                text = extract_pdf_text(pdf_path)

        self.assertNotIn("--- Page", text)
        self.assertNotIn("\nRea\n", text)
        self.assertIn("# Ksenia Gorobets Consultant", text)
        self.assertIn("## Experience", text)
        self.assertIn("## Customer Experience", text)
        self.assertIn("## Projects", text)
        self.assertIn("Operations leader across teams", text)

    def test_pdf_portfolio_keeps_sections_lists_cases_and_chunks(self) -> None:
        raw_text = (
            "--- Page 1 ---\n"
            "Портфолио\n\n"
            "Профиль\n"
            "Опыт\n"
            "Customer Experience consultant\n\n"
            "Экспертиза\n"
            "- Service Design\n"
            "- Operations\n\n"
            "Ключевые результаты\n"
            "- Reduced handoff errors\n"
            "- Improved guest response time\n\n"
            "Кейс 1. Оптимизация работы службы приема и размещения\n"
            "Проблема\n"
            "Команда теряла заявки между сменами.\n"
            "Действия\n"
            "- Описала SOP\n"
            "- Настроила контроль передачи смены\n"
            "Результат\n"
            "- Ошибок стало меньше\n"
            "Бизнес-эффект\n"
            "Сервис стал стабильнее.\n\n"
            "Кейс 2. Service recovery\n"
            "Проблема\n"
            "Гости долго ждали ответа.\n"
            "Результат\n"
            "- Response time improved\n\n"
            "Подход к работе\n"
            "Диагностика, структура, внедрение.\n"
            "Направления работы\n"
            "- CX audit\n"
            "- SOP design\n"
            "Контакты\n"
            "email@example.com"
        )
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            with (
                patch("post_agent.knowledge.extract_pdf_text_with_pymupdf", return_value=raw_text),
                patch("post_agent.knowledge.extract_pdf_text_with_pdfplumber", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_pypdf", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_from_streams", return_value=""),
                patch("post_agent.knowledge.extract_pdf_text_with_ocr", return_value=""),
            ):
                document = base.add_document("Портфолио.pdf", b"%PDF-1.7 placeholder")

            memory_item = base.memory_inbox.list_items()[0]

        self.assertIn("# Портфолио", document.content_text)
        self.assertIn("## Профиль", document.content_text)
        self.assertIn("### Опыт", document.content_text)
        self.assertIn("## Ключевые результаты", document.content_text)
        self.assertIn("- Reduced handoff errors", document.content_text)
        self.assertIn("## Кейс 1. Оптимизация работы службы приема и размещения", document.content_text)
        self.assertIn("### Проблема", document.content_text)
        self.assertIn("### Действия", document.content_text)
        self.assertIn("### Результат", document.content_text)
        self.assertIn("### Бизнес-эффект", document.content_text)
        self.assertIn("## Кейс 2. Service recovery", document.content_text)
        self.assertIn("## Подход к работе", document.content_text)
        self.assertIn("## Направления работы", document.content_text)
        self.assertIn("## Контакты", document.content_text)
        self.assertNotIn("--- Page", document.content_text)
        self.assertGreaterEqual(len(document.semantic_chunks), 3)
        self.assertTrue(any("Кейс 1" in chunk for chunk in document.semantic_chunks))
        self.assertTrue(any("Кейс 2" in chunk for chunk in document.semantic_chunks))
        self.assertIn("semantic_chunks", document.analysis)
        self.assertIn("semantic_chunks", memory_item.extracted)

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
        # The document card is now concise; full details (type, AI analysis, chunks)
        # live on the document detail page, reachable via "Открыть".
        self.assertIn("Открыть", library_html)
        self.assertIn("Service Design note", document_html)
        self.assertIn("Markdown", document_html)
        self.assertIn("document_type", document_html)
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

    def test_document_metadata_and_chunks_are_indexed_for_search(self) -> None:
        with TemporaryDirectory() as directory:
            base = KnowledgeBase(Path(directory) / "documents", Path(directory) / "index.json")
            document = base.add_document(
                "portfolio.md",
                (
                    "# Portfolio\n\n"
                    "## Case MAYRVEDA\n\n"
                    "### Problem\n\nGuest handoffs were unstable.\n\n"
                    "### Actions\n\n- SOP redesign\n- Service blueprint\n\n"
                    "### Business effect\n\nOperational Excellence improved."
                ).encode("utf-8"),
            )

            results = base.search("service blueprint")

        self.assertEqual(document.document_metadata["document_type"], "portfolio")
        self.assertTrue(any(chunk.get("type") == "case" for chunk in document.chunk_metadata))
        self.assertIn("chunks", document.analysis)
        self.assertEqual(results[0].document.id, document.id)

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
