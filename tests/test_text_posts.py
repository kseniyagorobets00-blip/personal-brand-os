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
        self.assertIn("Полный текст", detail_html)
        self.assertIn("Перенести в архив", detail_html)


if __name__ == "__main__":
    unittest.main()
