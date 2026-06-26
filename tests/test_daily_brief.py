import unittest
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from datetime import date

from post_agent.ai_gateway import AIGatewayError
from post_agent.author_profile import AuthorProfileRepository
from post_agent.daily_brief import DailyBriefService, SeedRepository, weekday_name_for_date
from post_agent.export import export_daily_brief
from post_agent.web import _author_profile_form_to_raw, _refine_with_ai, _save_content_plan_form, render_author_profile, render_content_plan_page, render_daily_brief, render_writing_dna
from post_agent.writing_dna import WritingDNARepository


class DailyBriefTests(unittest.TestCase):
    def test_daily_brief_contains_required_sections(self) -> None:
        brief = DailyBriefService().build_today()

        self.assertGreaterEqual(brief.source_count, 1)
        self.assertTrue(brief.executive_summary)
        self.assertTrue(brief.market_signals)
        self.assertTrue(brief.topics)
        self.assertTrue(brief.recommendations)
        self.assertTrue(brief.ideas)
        self.assertTrue(brief.drafts)
        self.assertTrue(brief.approvals)
        self.assertTrue(brief.content_plan.week)
        self.assertTrue(brief.content_plan.planned_publications)
        self.assertTrue(brief.related_knowledge)

    def test_drafts_do_not_render_author_profile_instructions(self) -> None:
        brief = DailyBriefService().build_today()
        draft_text = "\n".join(draft.text for draft in brief.drafts)

        self.assertNotIn("Стиль:", draft_text)
        self.assertNotIn("Правило платформы:", draft_text)
        self.assertNotIn("Без определения темы", draft_text)
        self.assertNotIn("Цель публикации:", draft_text)
        self.assertNotIn("Основная мысль:", draft_text)
        self.assertNotIn("Краткая структура:", draft_text)

    def test_daily_brief_html_renders_user_visible_blocks(self) -> None:
        html = render_daily_brief(DailyBriefService().build_today())

        self.assertIn("Daily Brief", html)
        self.assertIn("Публикация дня", html)
        self.assertIn("Цель публикации", html)
        self.assertIn("Почему именно сегодня", html)
        self.assertIn("Почему агент рекомендует этот пост", html)
        self.assertIn("Создать черновик", html)
        self.assertIn("Пропустить сегодня", html)
        self.assertIn("Главная идея", html)
        self.assertIn("Черновики к подготовке", html)
        self.assertIn("Почему актуально", html)
        self.assertIn("Краткая структура", html)
        self.assertIn("Первый черновик текста", html)
        self.assertIn("Мои решения", html)
        self.assertIn("Контент-план", html)
        self.assertIn("План недели", html)
        self.assertIn("Открыть полный контент-план", html)
        self.assertIn("Обновить заголовок", html)
        self.assertIn("Другой вариант", html)
        self.assertIn("Сделать сильнее", html)
        self.assertIn("Сделать мягче", html)
        self.assertIn("/daily-brief/refine", html)
        self.assertIn('name="title"', html)
        self.assertIn('name="text"', html)
        self.assertIn('name="kind"', html)
        self.assertIn("Обновляем...", html)
        self.assertIn("Author Profile", html)
        self.assertIn("Полезные материалы", html)
        self.assertIn("Открыть", html)
        self.assertIn("Использовать", html)
        self.assertIn("/daily-brief/approval", html)
        self.assertIn("decision-status", html)
        self.assertIn("/ideas", html)
        self.assertNotIn("Главный фокус дня", html)
        self.assertNotIn("Рыночные сигналы", html)
        self.assertNotIn("Найденные темы", html)
        self.assertNotIn("Темы для контента", html)
        self.assertNotIn("Подготовленные черновики", html)
        self.assertNotIn("Что предлагает AI", html)
        self.assertNotIn("Требует подтверждения", html)

    def test_daily_brief_uses_content_plan_as_publication_source(self) -> None:
        brief = DailyBriefService().build_today()
        titles = {item.title for item in brief.topics}

        self.assertIn("AI как зеркало операционной зрелости", titles)
        self.assertTrue(all("из контент-плана" in item.tags for item in brief.topics))

    def test_daily_brief_shows_all_today_publications_from_content_plan(self) -> None:
        today = date.today().strftime("%d.%m.%Y")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources_path = root / "sources.json"
            plan_path = root / "content_plan.json"
            sources_path.write_text('{"sources": [], "ideas": [], "approval_items": []}', encoding="utf-8")
            plan_path.write_text(
                f"""{{
                  "week": "test",
                  "focus": "test focus",
                  "month_focus": "test month",
                  "content_pillars": ["AI"],
                  "platform_targets": ["LinkedIn", "Telegram"],
                  "today_recommendation": "prepare",
                  "planned_publications": [
                    {{"date": "{today}", "day": "Сегодня", "platform": "LinkedIn", "topic": "Первая тема", "pillar": "AI", "status": "planned", "goal": "goal 1", "summary": "summary 1", "note": ""}},
                    {{"date": "{today}", "day": "Сегодня", "platform": "Telegram", "topic": "Вторая тема", "pillar": "AI", "status": "planned", "goal": "goal 2", "summary": "summary 2", "note": ""}}
                  ]
                }}""",
                encoding="utf-8",
            )

            brief = DailyBriefService(repository=SeedRepository(sources_path, plan_path)).build_today()

        self.assertEqual({item.title for item in brief.topics}, {"Первая тема", "Вторая тема"})
        self.assertEqual(len(brief.drafts), 2)

    def test_2026_06_26_is_friday(self) -> None:
        self.assertEqual(weekday_name_for_date("2026-06-26"), "Пятница")
        self.assertEqual(weekday_name_for_date("26.06.2026"), "Пятница")

    def test_content_plan_day_is_computed_from_date(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources_path = root / "sources.json"
            plan_path = root / "content_plan.json"
            sources_path.write_text('{"sources": [], "ideas": [], "approval_items": []}', encoding="utf-8")
            plan_path.write_text(
                """{
                  "week": "test",
                  "focus": "test focus",
                  "month_focus": "test month",
                  "content_pillars": ["AI"],
                  "platform_targets": ["LinkedIn"],
                  "today_recommendation": "prepare",
                  "planned_publications": [
                    {"date": "26.06.2026", "day": "Вторник", "platform": "LinkedIn", "topic": "Тема", "pillar": "AI", "status": "planned", "goal": "goal", "summary": "summary", "note": ""}
                  ]
                }""",
                encoding="utf-8",
            )

            brief = DailyBriefService(repository=SeedRepository(sources_path, plan_path)).build_today()

        self.assertEqual(brief.content_plan.planned_publications[0].day, "Пятница")

    def test_refinement_uses_ai_gateway_result(self) -> None:
        class FakeGateway:
            def is_configured(self) -> bool:
                return True

            def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
                return {"title": "AI-заголовок", "text": "AI-версия текста"}

        with patch("post_agent.web.AIGateway", return_value=FakeGateway()):
            result = _refine_with_ai("Сделать сильнее", "Старый заголовок", "Старый текст", "draft")

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["title"], "AI-заголовок")
        self.assertEqual(result["text"], "AI-версия текста")

    def test_refinement_retries_timeout_and_keeps_current_draft(self) -> None:
        class TimeoutGateway:
            def __init__(self) -> None:
                self.calls = 0

            def is_configured(self) -> bool:
                return True

            def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
                self.calls += 1
                raise AIGatewayError("timed out")

        gateway = TimeoutGateway()
        with patch("post_agent.web.AIGateway", return_value=gateway):
            result = _refine_with_ai("Сделать сильнее", "Старый заголовок", "Старый текст", "draft")

        self.assertEqual(gateway.calls, 2)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["title"], "Старый заголовок")
        self.assertEqual(result["text"], "Старый текст")
        self.assertEqual(result["error"], "AI не успел ответить. Попробуйте еще раз.")

    def test_daily_brief_explains_plan_fit(self) -> None:
        brief = DailyBriefService().build_today()

        self.assertIn("план", brief.executive_summary.lower())
        self.assertTrue(any("контент-плана" in item.reason for item in brief.topics))
        self.assertTrue(any("статус" in item.action for item in brief.ideas))

    def test_daily_brief_exports_standalone_html(self) -> None:
        with TemporaryDirectory() as directory:
            path = export_daily_brief(f"{directory}/daily-brief.html")

            self.assertTrue(path.exists())
            self.assertIn("Daily Brief", path.read_text(encoding="utf-8"))

    def test_author_profile_page_renders_editable_sections(self) -> None:
        profile = AuthorProfileRepository().load_raw()
        html = render_author_profile(profile)

        self.assertIn("Author Profile", html)
        self.assertIn("тон", html)
        self.assertIn("структура", html)
        self.assertIn("лексика", html)
        self.assertIn("правила площадок", html)
        self.assertIn("цели площадок", html)
        self.assertIn("чего не писать", html)
        self.assertIn("примеры и истории", html)
        self.assertIn("Сохранить Author Profile", html)

    def test_writing_dna_page_renders_editable_sections(self) -> None:
        dna = WritingDNARepository().load_raw()
        html = render_writing_dna(dna)

        self.assertIn("Writing DNA", html)
        self.assertIn("Как рождаются публикации", html)
        self.assertIn("Запрещенные AI-вступления", html)
        self.assertIn("Сохранить Writing DNA", html)

    def test_content_plan_page_renders_editable_plan(self) -> None:
        html = render_content_plan_page(
            {
                "week": "25-30 июня",
                "focus": "CX через Operations",
                "month_focus": "Operations, CX, AI",
                "content_pillars": ["Operations", "Customer Experience"],
                "platform_targets": ["LinkedIn", "Telegram"],
                "today_recommendation": "Подготовить пост",
                "planned_publications": [
                    {
                        "day": "Понедельник",
                        "platform": "LinkedIn",
                        "topic": "CX",
                        "pillar": "Customer Experience",
                        "status": "planned",
                        "note": "Главный пост",
                    }
                ],
            }
        )

        self.assertIn("Контент-план", html)
        self.assertIn("Сохранить план", html)
        self.assertIn("Утвердить план", html)
        self.assertIn("Создать новый план", html)
        self.assertIn("Сгенерировать тему/содержание", html)
        self.assertIn("Добавить публикацию", html)
        self.assertIn("Краткое содержание", html)
        self.assertIn("Цель", html)
        self.assertIn("Дата", html)

    def test_content_plan_page_uses_browser_date_inputs(self) -> None:
        html = render_content_plan_page(
            {
                "week_start": "2026-06-22",
                "week_end": "2026-06-28",
                "planned_publications": [{"date": "26.06.2026", "platform": "LinkedIn", "topic": "CX"}],
            }
        )

        self.assertIn('type="date" name="week_start"', html)
        self.assertIn('type="date" name="week_end"', html)
        self.assertIn('type="date" name="pub_0_date"', html)
        self.assertIn('value="2026-06-26"', html)

    def test_content_plan_calendar_view_groups_same_day_publications(self) -> None:
        html = render_content_plan_page(
            {
                "week": "25-30 июня",
                "focus": "CX через Operations",
                "month_focus": "Operations, CX, AI",
                "content_pillars": ["Operations", "Customer Experience"],
                "platform_targets": ["LinkedIn", "Telegram"],
                "today_recommendation": "Подготовить пост",
                "planned_publications": [
                    {"date": "26.06.2026", "platform": "LinkedIn", "topic": "Первая тема", "status": "planned", "goal": "", "summary": "", "note": ""},
                    {"date": "26.06.2026", "platform": "Telegram", "topic": "Вторая тема", "status": "drafted", "goal": "", "summary": "", "note": ""},
                ],
            },
            view="calendar",
        )

        self.assertIn("Календарь", html)
        self.assertIn("#publication-0", html)
        self.assertIn("#publication-1", html)
        self.assertIn("Первая тема", html)
        self.assertIn("Вторая тема", html)

    def test_content_plan_generate_publication_updates_only_selected_item(self) -> None:
        class Gateway:
            def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
                return {
                    "topic": "Generated topic",
                    "goal": "Generated goal",
                    "summary": "Generated summary",
                    "status": "suggested",
                    "note": "Generated note",
                }

        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            plan_path.write_text("{}", encoding="utf-8")
            form = {
                "view": ["list"],
                "plan_action": ["generate_pub_1"],
                "week_start": ["2026-06-22"],
                "week_end": ["2026-06-28"],
                "focus": ["Focus"],
                "month_focus": ["Month"],
                "content_pillars": ["Operations"],
                "platform_targets": ["LinkedIn"],
                "today_recommendation": ["Today"],
                "pub_0_date": ["2026-06-26"],
                "pub_0_platform": ["LinkedIn"],
                "pub_0_topic": ["Keep me"],
                "pub_0_goal": [""],
                "pub_0_pillar": [""],
                "pub_0_status": ["planned"],
                "pub_0_summary": [""],
                "pub_0_note": [""],
                "pub_1_date": ["2026-06-27"],
                "pub_1_platform": ["Telegram"],
                "pub_1_topic": ["Old"],
                "pub_1_goal": [""],
                "pub_1_pillar": [""],
                "pub_1_status": ["planned"],
                "pub_1_summary": [""],
                "pub_1_note": [""],
            }

            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path), patch("post_agent.web.AIGateway", return_value=Gateway()):
                location = _save_content_plan_form(form)
                saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(location, "/content-plan?saved=1&view=list#publication-1")
        self.assertEqual(saved["planned_publications"][0]["topic"], "Keep me")
        self.assertEqual(saved["planned_publications"][1]["topic"], "Generated topic")
        self.assertEqual(saved["planned_publications"][1]["day"], "Суббота")

    def test_content_plan_generate_full_plan_replaces_week_plan(self) -> None:
        class Gateway:
            def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
                return {
                    "focus": "Generated focus",
                    "planned_publications": [
                        {"platform": "LinkedIn", "topic": "Generated Monday", "goal": "Goal", "summary": "Summary", "status": "planned", "note": "Note"},
                        {"platform": "Telegram", "topic": "Generated Tuesday", "goal": "Goal", "summary": "Summary", "status": "planned", "note": "Note"},
                    ],
                }

        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            plan_path.write_text("{}", encoding="utf-8")
            form = {
                "view": ["calendar"],
                "plan_action": ["request_ai"],
                "week_start": ["2026-06-22"],
                "week_end": ["2026-06-28"],
                "focus": ["Old"],
                "month_focus": ["Month"],
                "content_pillars": ["Operations"],
                "platform_targets": ["LinkedIn"],
                "today_recommendation": ["Today"],
            }

            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path), patch("post_agent.web.AIGateway", return_value=Gateway()):
                location = _save_content_plan_form(form)
                saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(location, "/content-plan?saved=1&view=calendar")
        self.assertEqual(saved["focus"], "Generated focus")
        self.assertEqual(saved["planned_publications"][0]["date"], "2026-06-22")
        self.assertEqual(saved["planned_publications"][1]["date"], "2026-06-23")
        self.assertEqual(saved["planned_publications"][0]["topic"], "Generated Monday")

    def test_author_profile_form_can_be_saved_as_json(self) -> None:
        raw = _author_profile_form_to_raw(
            {
                "formality": ["professional"],
                "directness": ["direct"],
                "provocation": ["moderate"],
                "emotionality": ["restrained"],
                "post_structure": ["thesis -> diagnosis -> conclusion"],
                "intro_length": ["short"],
                "narrative_logic": ["symptom to cause"],
                "conclusion": ["clear takeaway"],
                "favorite_words": ["операционная дисциплина\nзрелость"],
                "unwanted_words": ["магия"],
                "banned_cliches": ["в современном мире"],
                "professional_terms": ["CX\nSOP"],
                "platform_linkedin": ["business"],
                "platform_telegram": ["short"],
                "platform_vc": ["structured"],
                "platform_setka": ["live"],
                "goal_linkedin": ["expertise"],
                "goal_telegram": ["dialogue"],
                "goal_vc": ["deep articles"],
                "goal_setka": ["presence"],
                "what_not_to_write": ["не звучать как AI"],
                "examples_and_stories": ["Передача ответственности\nСитуация: Команда теряла клиента между этапами.\nВывод: Нужен владелец перехода.\nТемы: Customer Experience, SOP"],
            }
        )

        with TemporaryDirectory() as directory:
            repo = AuthorProfileRepository(Path(directory) / "author_profile.json")
            repo.save_raw(raw)
            loaded = repo.load()

        self.assertEqual(loaded.tone.directness, "direct")
        self.assertIn("операционная дисциплина", loaded.vocabulary.favorite_words)
        self.assertEqual(loaded.rule_for_platform("Telegram").rule, "short")
        self.assertEqual(raw["platform_goals"]["LinkedIn"], "expertise")
        self.assertIn("не звучать как AI", raw["what_not_to_write"])
        self.assertEqual(raw["examples_and_stories"][0]["title"], "Передача ответственности")


if __name__ == "__main__":
    unittest.main()
