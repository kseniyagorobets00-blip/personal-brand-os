import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from post_agent.text_posts import TextPostRepository
from post_agent.web import render_text_post_detail, render_text_posts_page


class TextPostRepositoryTests(unittest.TestCase):
    def test_syncs_planned_posts_without_overwriting_manual_text(self) -> None:
        plan = {
            "week_start": "2026-07-06",
            "week_end": "2026-07-12",
            "planned_publications": [
                {
                    "date": "2026-07-06",
                    "platform": "LinkedIn",
                    "topic": "AI exposes CX gaps",
                    "summary": "Original summary",
                    "status": "planned",
                }
            ],
        }
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            repository.sync_from_content_plan(plan)
            post = repository.list_posts("planned")[0]
            repository.update(post.id, post.title, post.platform, post.publication_date, "Edited text", "approved")
            repository.sync_from_content_plan(plan)
            loaded = repository.list_posts("planned")[0]
            approved = repository.approved_for_publication({"platform": "LinkedIn", "topic": "AI exposes CX gaps"})

        self.assertEqual(loaded.text, "Edited text")
        self.assertEqual(loaded.status, "approved")
        self.assertIsNotNone(approved)

    def test_manual_archive_posts_are_available_for_ai(self) -> None:
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            repository.add_archive("Published post", "Telegram", "2026-07-05", "Archive text")

            archive = repository.list_posts("archive")
            ai_posts = repository.published_for_ai()

        self.assertEqual(len(archive), 1)
        self.assertEqual(ai_posts[0]["title"], "Published post")
        self.assertEqual(ai_posts[0]["text"], "Archive text")

    def test_add_planned_creates_editable_draft_in_planned_tab(self) -> None:
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            post = repository.add_planned("Мой пост", "LinkedIn", "2026-07-10", "Черновик текста")
            planned = repository.list_posts("planned")

        self.assertEqual(post.tab, "planned")
        self.assertEqual(post.status, "draft")
        self.assertEqual(post.source, "manual")
        self.assertEqual([p.id for p in planned], [post.id])

    def test_planned_tab_offers_manual_creation_and_detail_has_editor_tools(self) -> None:
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            post = repository.add_planned("Мой пост", "LinkedIn", "2026-07-10", "Текст")
            list_html = render_text_posts_page(repository, {"tab": ["planned"]}, {})
            detail_html = render_text_post_detail(post)

        self.assertIn("/texts/planned/add", list_html)
        self.assertIn("Скопировать текст", detail_html)
        self.assertIn("char-count", detail_html)
        self.assertIn("confirm(", detail_html)

    def test_editor_offers_generate_and_generation_degrades_without_ai(self) -> None:
        from post_agent.web import _generate_post_text

        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            post = repository.add_planned("Тема", "LinkedIn", "2026-07-10", "")
            detail_html = render_text_post_detail(post)

        self.assertIn("Сгенерировать текст", detail_html)
        self.assertIn("value=\"generate\"", detail_html)
        # Without a configured AI the call must return a friendly error, never raise.
        result = _generate_post_text("Тема", "LinkedIn", "Цель: показать экспертизу")
        self.assertIn("error", result)
        self.assertNotIn("text", result)

    def test_text_posts_ui_renders_list_and_detail(self) -> None:
        plan = {
            "week_start": "2026-07-06",
            "week_end": "2026-07-12",
            "planned_publications": [
                {
                    "date": "2026-07-06",
                    "platform": "LinkedIn",
                    "topic": "AI exposes CX gaps",
                    "summary": "Original summary",
                    "status": "planned",
                }
            ],
        }
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            repository.sync_from_content_plan(plan)
            post = repository.list_posts("planned")[0]
            list_html = render_text_posts_page(repository, {"tab": ["planned"]}, plan)
            detail_html = render_text_post_detail(post)

        self.assertIn("Тексты", list_html)
        self.assertIn("Запланировано", list_html)
        self.assertIn("AI exposes CX gaps", list_html)
        self.assertIn("Текст публикации", detail_html)
        self.assertIn("Перенести в архив", detail_html)

    def test_text_field_holds_only_the_draft_and_brief_is_separate(self) -> None:
        plan = {
            "planned_publications": [
                {
                    "date": "2026-07-06",
                    "platform": "LinkedIn",
                    "topic": "CX operations",
                    "summary": "Разобрать, почему CX ломается на передаче ответственности",
                    "goal": "Показать экспертизу",
                    "status": "planned",
                }
            ]
        }
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            repository.sync_from_content_plan(plan)
            post = repository.list_posts("planned")[0]
            detail_html = render_text_post_detail(post)

        # No draft yet -> the post body stays empty; the brief carries the task.
        self.assertEqual(post.text, "")
        self.assertIn("Разобрать, почему CX ломается", post.brief)
        self.assertIn("Показать экспертизу", post.brief)
        self.assertIn("задание из контент-плана", detail_html)
        # The brief text must not leak into the editable post body textarea.
        body = detail_html.split("name=\"text\"", 1)[-1].split("</textarea>", 1)[0]
        self.assertNotIn("Разобрать, почему CX ломается", body)

    def test_sync_replaces_legacy_autofilled_text_but_keeps_real_draft(self) -> None:
        plan = {
            "planned_publications": [
                {
                    "date": "2026-07-06",
                    "platform": "LinkedIn",
                    "topic": "CX operations",
                    "summary": "Краткое содержание",
                    "draft": "Настоящий текст поста.",
                    "status": "planned",
                }
            ]
        }
        with TemporaryDirectory() as directory:
            repository = TextPostRepository(Path(directory) / "posts.json")
            # Simulate a post left over from the old logic where text = summary.
            repository.add_planned("CX operations", "LinkedIn", "2026-07-06", "Краткое содержание")
            repository.sync_from_content_plan(plan)
            post = repository.list_posts("planned")[0]

        self.assertEqual(post.text, "Настоящий текст поста.")


if __name__ == "__main__":
    unittest.main()
