import unittest
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from datetime import date, timedelta

from post_agent.ai_gateway import AIGatewayError
from post_agent.author_profile import AuthorProfileRepository
from post_agent.daily_brief import ContentPlan, DailyBriefService, PlannedPublication, SeedRepository, refresh_stale_content_plan, today_moscow, weekday_name_for_date
from post_agent.export import export_daily_brief
from post_agent.web import _author_profile_form_to_raw, _compact_content_plan_block, _content_plan_with_query_period, _refine_with_ai, _save_content_plan_form, _text_matches_platform, render_author_profile, render_content_plan_page, render_daily_brief, render_idea_vault
from post_agent.writing_dna import WritingDNARepository


def _first_planned_date() -> date:
    """A date that the committed seed content plan actually has a publication on.

    Keeps the happy-path daily-brief tests deterministic regardless of the real
    clock (otherwise they fail on any day the plan has no entry — a "free day").
    """
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seeds" / "content_plan.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    return date.fromisoformat(str(seed["planned_publications"][0]["date"]))


PLANNED_DATE = _first_planned_date()


class DailyBriefTests(unittest.TestCase):
    @patch("post_agent.daily_brief.today_moscow", return_value=PLANNED_DATE)
    def test_daily_brief_contains_required_sections(self, _today) -> None:
        brief = DailyBriefService().build_today()

        self.assertGreaterEqual(brief.source_count, 1)
        self.assertTrue(brief.executive_summary)
        self.assertTrue(brief.market_signals)
        self.assertTrue(brief.topics)
        self.assertTrue(brief.recommendations)
        self.assertTrue(brief.ideas)
        self.assertTrue(brief.drafts)
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

    @patch("post_agent.daily_brief.today_moscow", return_value=PLANNED_DATE)
    @patch("post_agent.web._load_ui_state", return_value={"approvals": {}, "refinements": {}})
    def test_daily_brief_html_renders_user_visible_blocks(self, _ui_state, _today) -> None:
        brief = DailyBriefService().build_today()
        html = render_daily_brief(brief)

        self.assertIn("Daily Brief", html)
        self.assertIn("Дневной бриф", html)
        self.assertIn("Радар трендов", html)
        self.assertIn("Профиль автора", html)
        self.assertNotIn("ДНК письма", html)
        self.assertNotIn("Правила, которые система уже учитывает", html)
        self.assertIn(brief.brief_date.strftime("%d.%m.%Y"), html)
        self.assertIn("Публикация дня", html)
        self.assertIn("Цель публикации", html)
        self.assertIn("Почему именно сегодня", html)
        self.assertIn("Почему агент рекомендует этот пост", html)
        self.assertIn("Создать черновик", html)
        self.assertIn("Главная идея", html)
        self.assertIn("Черновики к подготовке", html)
        self.assertIn("Почему актуально", html)
        self.assertIn("Краткая структура", html)
        self.assertIn("Первый черновик текста", html)
        self.assertNotIn("Тренды и сигналы", html)
        self.assertNotIn("Мои решения", html)
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
        self.assertIn("Профиль автора", html)
        # The standalone "Полезные материалы" block was removed from the main screen by request.
        self.assertNotIn("<h2>Полезные материалы</h2>", html)
        self.assertIn("Использовать", html)
        self.assertIn("/ideas", html)
        self.assertNotIn("Главный фокус дня", html)
        self.assertNotIn("Рыночные сигналы", html)
        self.assertNotIn("Найденные темы", html)
        self.assertNotIn("Темы для контента", html)
        self.assertNotIn("Подготовленные черновики", html)
        self.assertNotIn("Что предлагает AI", html)
        self.assertNotIn("Требует подтверждения", html)

    @patch("post_agent.daily_brief.today_moscow", return_value=PLANNED_DATE)
    def test_daily_brief_uses_content_plan_as_publication_source(self, _today) -> None:
        brief = DailyBriefService().build_today()

        self.assertTrue(brief.topics)
        self.assertTrue(all("из контент-плана" in item.tags for item in brief.topics))

    def test_global_script_confirms_deletes_and_guards_submits(self) -> None:
        from post_agent.web import _global_script

        script = _global_script()
        self.assertIn("Удалить безвозвратно", script)          # delete confirmation (#1)
        self.assertIn("/delete/", script)
        self.assertIn("dataset.submitting", script)            # double-submit + loading guard (#2)
        self.assertIn("btn.disabled=true", script)

    def test_auto_refresh_only_while_running_and_respects_activity(self) -> None:
        from post_agent.web import _auto_refresh_meta

        class Status:
            def __init__(self, state):
                self.state = state

        self.assertEqual(_auto_refresh_meta(Status("idle")), "")
        running = _auto_refresh_meta(Status("running"))
        self.assertIn("location.reload()", running)            # still refreshes while running (#5)
        self.assertIn("activeElement", running)                # but not while typing / active
        self.assertNotIn("http-equiv", running)                # no more hard full-page meta refresh

    def test_how_it_works_page_maps_the_pipeline(self) -> None:
        from post_agent.web import render_how_it_works

        html = render_how_it_works()
        for block in ("Как это связано", "Правила бота", "Author Brain", "Thinking Engine", "Входящие памяти"):
            self.assertIn(block, html)
        # legend explains the guard/gate markers
        self.assertIn("жёсткая проверка", html)
        self.assertIn("ручной шлюз", html)

    def test_flag_duplicate_cells_marks_near_duplicates(self) -> None:
        from post_agent.web import _flag_duplicate_cells

        plan = {
            "planned_publications": [
                {"topic": "Почему сервис ломается в точках передачи ответственности", "summary": "CX зависит от процессов и владельцев", "note": ""},
                {"topic": "Почему сервис ломается в точках передачи ответственности между ролями", "summary": "клиентский опыт зависит от процессов и владельцев участка", "note": ""},
                {"topic": "AI как зеркало операционной зрелости", "summary": "ИИ подсвечивает слабые процессы", "note": ""},
            ]
        }
        flagged = _flag_duplicate_cells(plan)
        self.assertEqual(flagged, 1)
        self.assertNotIn("repeat_warning", plan["planned_publications"][0])
        self.assertIn("repeat_warning", plan["planned_publications"][1])
        self.assertNotIn("repeat_warning", plan["planned_publications"][2])

    def test_gates_banner_surfaces_pending_gates(self) -> None:
        from post_agent.web import _gates_banner

        # Hidden when nothing is waiting.
        self.assertEqual(_gates_banner(0, 0), "")
        # Shown with counts and a link to where to resolve them.
        banner = _gates_banner(2, 3)
        self.assertIn("2 материалов памяти", banner)
        self.assertIn("3 правил обучения", banner)
        self.assertIn("AI их не использует", banner)
        self.assertIn("/learning", banner)
        # Only one gate present -> only that one is mentioned.
        only_memory = _gates_banner(1, 0)
        self.assertIn("материалов памяти", only_memory)
        self.assertNotIn("правил обучения", only_memory)

    def test_daily_brief_shows_free_day_when_no_publication_today(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sources.json").write_text('{"sources": [], "ideas": [], "approval_items": []}', encoding="utf-8")
            future = (today_moscow() + timedelta(days=5)).isoformat()
            (root / "content_plan.json").write_text(
                json.dumps(
                    {
                        "week": "test",
                        "focus": "f",
                        "month_focus": "m",
                        "content_pillars": ["AI"],
                        "platform_targets": ["LinkedIn"],
                        "today_recommendation": "r",
                        "planned_publications": [
                            {"date": future, "day": "x", "platform": "LinkedIn", "topic": "Будущая тема", "pillar": "AI", "status": "planned", "goal": "g", "summary": "s", "note": ""}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            brief = DailyBriefService(repository=SeedRepository(root / "sources.json", root / "content_plan.json")).build_today()

            self.assertFalse(brief.topics)
            html = render_daily_brief(brief)
            self.assertIn("Свободный день", html)
            # no misleading "create a draft" call to action on a day with no plan
            self.assertNotIn("Создать черновик", html)

    def test_daily_brief_shows_all_today_publications_from_content_plan(self) -> None:
        # Use the app's own notion of "today" (Moscow time), not the machine clock,
        # so the test doesn't flake around the UTC/Moscow date boundary.
        today = today_moscow().strftime("%d.%m.%Y")
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

    def test_stale_content_plan_is_moved_to_current_dates(self) -> None:
        plan = {
            "week": "25-30 июня 2026",
            "focus": "Focus",
            "planned_publications": [
                {"date": "2026-06-25", "topic": "First", "day": "Четверг"},
                {"date": "2026-06-26", "topic": "Second", "day": "Пятница"},
                {"date": "2026-06-30", "topic": "Last", "day": "Вторник"},
            ],
        }

        refreshed = refresh_stale_content_plan(plan, date(2026, 7, 4))

        self.assertEqual(refreshed["week_start"], "2026-07-04")
        self.assertEqual(refreshed["week_end"], "2026-07-09")
        self.assertEqual(refreshed["planned_publications"][0]["date"], "2026-07-04")
        self.assertEqual(refreshed["planned_publications"][0]["day"], "Суббота")
        self.assertEqual(refreshed["planned_publications"][2]["date"], "2026-07-09")
        self.assertIn("автоматически перенесен", refreshed["last_action"])

    def test_partially_stale_content_plan_is_shifted_when_first_publication_is_past(self) -> None:
        plan = {
            "week": "04-09 июля",
            "week_start": "2026-07-04",
            "week_end": "2026-07-09",
            "focus": "Focus",
            "planned_publications": [
                {"date": "2026-07-04", "topic": "Past but recent", "day": "Суббота"},
                {"date": "2026-07-06", "topic": "Still upcoming", "day": "Понедельник"},
            ],
        }

        refreshed = refresh_stale_content_plan(plan, date(2026, 7, 5))

        self.assertEqual(refreshed["planned_publications"][0]["date"], "2026-07-05")
        self.assertEqual(refreshed["planned_publications"][1]["date"], "2026-07-07")
        self.assertEqual(refreshed["week_start"], "2026-07-05")

    def test_compact_content_plan_shows_today_even_without_publication(self) -> None:
        today = today_moscow()
        future = (today + timedelta(days=2)).isoformat()
        plan = ContentPlan(
            week="test",
            focus="test",
            month_focus="test",
            content_pillars=(),
            platform_targets=(),
            today_recommendation="",
            planned_publications=(
                PlannedPublication(
                    date=future,
                    day="",
                    platform="LinkedIn",
                    topic="Future topic",
                    pillar="",
                    status="planned",
                    note="",
                ),
            ),
        )

        with patch("post_agent.web._load_content_plan_raw", return_value={"week_start": today.isoformat(), "week_end": future}):
            html = _compact_content_plan_block(plan)

        self.assertIn("Нет публикаций", html)
        self.assertIn("Future topic", html)

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

            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, str]:
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

            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, str]:
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

    @patch("post_agent.daily_brief.today_moscow", return_value=PLANNED_DATE)
    def test_daily_brief_explains_plan_fit(self, _today) -> None:
        brief = DailyBriefService().build_today()

        self.assertIn("план", brief.executive_summary.lower())
        self.assertTrue(any("контент-плана" in item.reason for item in brief.topics))
        self.assertTrue(any("статус" in item.action for item in brief.ideas))

    def test_russian_platform_blocks_long_english_ai_text(self) -> None:
        english = "Among the available angles, the strongest fit for today is the CX operations thesis."
        russian = "AI усиливает CX только там, где SOP и контроль качества уже работают."

        self.assertFalse(_text_matches_platform(english, "Сетка"))
        self.assertFalse(_text_matches_platform(english, "Telegram"))
        self.assertTrue(_text_matches_platform(russian, "Сетка"))

    @patch("post_agent.daily_brief.today_moscow", return_value=PLANNED_DATE)
    def test_daily_brief_exports_standalone_html(self, _today) -> None:
        with TemporaryDirectory() as directory:
            path = export_daily_brief(f"{directory}/daily-brief.html")

            self.assertTrue(path.exists())
            self.assertIn("Daily Brief", path.read_text(encoding="utf-8"))

    def test_author_profile_page_renders_editable_sections(self) -> None:
        profile = AuthorProfileRepository().load_raw()
        html = render_author_profile(profile)
        dna_html = render_author_profile(profile, tab="dna")
        strategy_html = render_author_profile(profile, tab="strategy")
        ideas_html = render_idea_vault([])

        self.assertIn("Профиль автора", html)
        self.assertIn("Авторская база", html)
        self.assertIn("ДНК письма", html)
        self.assertIn("Редакционная стратегия", html)
        self.assertIn("href=\"/author-profile?tab=dna\"", html)
        self.assertIn("href=\"/author-profile?tab=strategy\"", html)
        # The duplicate "Правила" tab was removed; rules now live only in "Правила бота"/"Обучение".
        self.assertNotIn("href=\"/author-profile?tab=rules\"", html)
        self.assertIn("Главные темы", html)
        self.assertNotIn("Ключевые идеи", html)
        self.assertNotIn("Кейсы автора", html)
        self.assertIn("Ключевые идеи", ideas_html)
        self.assertNotIn("правила стиля", html)
        self.assertIn("правила стиля", dna_html)
        self.assertIn("Тональность", dna_html)
        self.assertIn("Структура абзацев", dna_html)
        self.assertIn("Допустимые приемы", dna_html)
        self.assertIn("Запреты", dna_html)
        self.assertIn("Примеры хорошего текста", dna_html)
        self.assertIn("Сохранить профиль автора", dna_html)
        self.assertIn("Сохранить стратегию", strategy_html)
        self.assertIn("Создать план по стратегии", strategy_html)
        # The profile tab bar should only expose base/dna/strategy — removed tabs must stay out.
        tab_bar = html.split("class=\"view-switch\"", 1)[-1].split("</nav>", 1)[0]
        self.assertNotIn("/writing-dna", tab_bar)
        self.assertNotIn("tab=rules", tab_bar)
        self.assertNotIn("/author-brain", tab_bar)

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
        self.assertIn("Создать план по стратегии", html)
        self.assertIn("Настроить стратегию", html)
        self.assertIn("/author-profile?tab=strategy", html)
        self.assertNotIn("Сохранить стратегию", html)
        self.assertIn("Рубрика", html)
        self.assertIn("Сгенерировать тему/ТЗ", html)
        self.assertIn("Добавить публикацию", html)
        # The full post content ("Краткое содержание") is no longer editable on the plan — only the ТЗ.
        self.assertNotIn(">Краткое содержание<", html)
        self.assertIn("Заметка / ТЗ", html)
        self.assertIn("Цель", html)
        self.assertIn("Дата", html)

        self.assertIn("status-badge", html)
        self.assertIn("Следующий этап", html)
        self.assertNotIn("<select name=\"pub_0_status\"", html)

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
        self.assertIn('type="month" name="month"', html)

    def test_content_plan_period_query_can_open_month(self) -> None:
        plan = _content_plan_with_query_period({}, {"month": ["2026-06"]})

        self.assertEqual(plan["week_start"], "2026-06-01")
        self.assertEqual(plan["week_end"], "2026-06-30")
        self.assertEqual(plan["week"], "01.06.2026 - 30.06.2026")

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

        self.assertIn('<details class="calendar-publication">', html)

    def test_compact_content_plan_hides_past_publications(self) -> None:
        today = today_moscow()
        past = (today - timedelta(days=1)).isoformat()
        future = (today + timedelta(days=1)).isoformat()
        plan = ContentPlan(
            week="test",
            focus="test",
            month_focus="test",
            content_pillars=(),
            platform_targets=(),
            today_recommendation="",
            planned_publications=(
                PlannedPublication(date=past, day="", platform="LinkedIn", topic="Past topic", pillar="", status="planned", note=""),
                PlannedPublication(date=future, day="", platform="Telegram", topic="Future topic", pillar="", status="planned", note=""),
            ),
        )

        html = _compact_content_plan_block(plan)

        self.assertNotIn("Past topic", html)
        self.assertIn("Future topic", html)

    def test_content_plan_generate_publication_updates_only_selected_item(self) -> None:
        class Gateway:
            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, str]:
                return {
                    "publication": {
                        "topic": "Generated topic",
                        "goal": "Generated goal",
                        "summary": "Generated summary",
                        "status": "suggested",
                        "note": "Generated note",
                    },
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

        self.assertEqual(location, "/content-plan?saved=1&status=updated&view=list#publication-1")
        self.assertEqual(saved["planned_publications"][0]["topic"], "Keep me")
        self.assertEqual(saved["planned_publications"][1]["topic"], "Generated topic")
        self.assertEqual(saved["planned_publications"][1]["day"], "Суббота")
        self.assertEqual(saved["planned_publications"][1]["status"], "in_progress")
        self.assertTrue(saved["planned_publications"][1]["updated_at"])
        self.assertTrue(saved["updated_at"])

    def test_content_plan_next_stage_advances_publication_status(self) -> None:
        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            plan_path.write_text("{}", encoding="utf-8")
            form = {
                "view": ["list"],
                "plan_action": ["next_pub_0"],
                "week_start": ["2026-06-22"],
                "week_end": ["2026-06-28"],
                "focus": ["Focus"],
                "month_focus": ["Month"],
                "content_pillars": ["Operations"],
                "platform_targets": ["LinkedIn"],
                "today_recommendation": ["Today"],
                "pub_0_date": ["2026-06-26"],
                "pub_0_platform": ["LinkedIn"],
                "pub_0_topic": ["Post"],
                "pub_0_goal": [""],
                "pub_0_pillar": [""],
                "pub_0_status": ["planned"],
                "pub_0_summary": [""],
                "pub_0_note": [""],
            }

            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path):
                location = _save_content_plan_form(form)
                saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(location, "/content-plan?saved=1&status=updated&view=list#publication-0")
        self.assertEqual(saved["planned_publications"][0]["status"], "in_progress")

    def test_content_plan_publication_regenerates_when_ai_returns_similar_variant(self) -> None:
        class Gateway:
            def __init__(self) -> None:
                self.calls = 0
                self.prompts: list[str] = []

            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, str]:
                self.calls += 1
                self.prompts.append(user_prompt)
                if self.calls == 1:
                    return {
                        "topic": "Old topic",
                        "summary": "Old summary",
                        "note": "Old note",
                    }
                return {
                    "topic": "Completely new operations angle",
                    "summary": "A different idea about service ownership and operational accountability.",
                    "note": "Use a fresh observation, not the previous post.",
                }

        gateway = Gateway()
        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            plan_path.write_text("{}", encoding="utf-8")
            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path), patch("post_agent.web.AIGateway", return_value=gateway):
                result = _save_content_plan_form(
                    {
                        "view": ["list"],
                        "plan_action": ["generate_pub_0"],
                        "week_start": ["2026-06-22"],
                        "week_end": ["2026-06-28"],
                        "focus": ["Weekly focus"],
                        "month_focus": ["Month focus"],
                        "content_pillars": ["Operations"],
                        "platform_targets": ["LinkedIn"],
                        "today_recommendation": ["Today"],
                        "pub_0_date": ["2026-06-26"],
                        "pub_0_platform": ["LinkedIn"],
                        "pub_0_topic": ["Old topic"],
                        "pub_0_goal": ["Goal"],
                        "pub_0_pillar": ["Operations"],
                        "pub_0_status": ["planned"],
                        "pub_0_summary": ["Old summary"],
                        "pub_0_note": ["Old note"],
                    }
                )

        self.assertIn("status=updated", result)
        self.assertEqual(gateway.calls, 2)
        self.assertIn("Month focus", gateway.prompts[0])
        self.assertIn("Weekly focus", gateway.prompts[0])

    def test_content_plan_generate_full_plan_uses_editorial_strategy(self) -> None:
        class Gateway:
            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, object]:
                return {
                    "content_plan": {
                        "focus": "Generated focus",
                        "planned_publications": [
                            {"platform": "Telegram", "rubric": "Кейс", "format": "статья", "topic": "Generated Monday", "goal": "Goal", "summary": "Summary", "status": "planned", "note": "Note"},
                            {"platform": "VC", "rubric": "Миф", "format": "статья", "topic": "Generated Tuesday", "goal": "Goal", "summary": "Summary", "status": "planned", "note": "Note"},
                        ],
                    },
                }

        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            strategy_path = Path(directory) / "editorial_strategy.json"
            plan_path.write_text("{}", encoding="utf-8")
            form = {
                "view": ["calendar"],
                "plan_action": ["strategy_plan"],
                "week_start": ["2026-06-22"],
                "week_end": ["2026-06-28"],
                "focus": ["Old"],
                "month_focus": ["Month"],
                "content_pillars": ["Operations"],
                "platform_targets": ["LinkedIn"],
                "today_recommendation": ["Today"],
                "strategy_0_day": ["Понедельник"],
                "strategy_0_active": ["on"],
                "strategy_0_platform": ["LinkedIn"],
                "strategy_0_rubric": ["Аналитика"],
                "strategy_0_format": ["экспертный пост"],
                "strategy_0_note": [""],
                "strategy_1_day": ["Вторник"],
                "strategy_1_active": ["on"],
                "strategy_1_platform": ["Telegram"],
                "strategy_1_rubric": ["Наблюдение"],
                "strategy_1_format": ["короткий пост"],
                "strategy_1_note": [""],
                "strategy_2_day": ["Среда"],
                "strategy_2_platform": ["VC"],
                "strategy_2_rubric": ["Кейс"],
                "strategy_2_format": ["статья"],
                "strategy_2_note": [""],
                "strategy_3_day": ["Четверг"],
                "strategy_3_platform": ["LinkedIn"],
                "strategy_3_rubric": ["Framework"],
                "strategy_3_format": ["карусель/пост"],
                "strategy_3_note": [""],
                "strategy_4_day": ["Пятница"],
                "strategy_4_platform": ["Telegram"],
                "strategy_4_rubric": ["Разговорный пост"],
                "strategy_4_format": ["короткий пост"],
                "strategy_4_note": [""],
                "strategy_5_day": ["Суббота"],
                "strategy_5_platform": ["Сетка"],
                "strategy_5_rubric": ["Наблюдение"],
                "strategy_5_format": ["мини-пост"],
                "strategy_5_note": [""],
                "strategy_6_day": ["Воскресенье"],
                "strategy_6_platform": ["Telegram"],
                "strategy_6_rubric": ["Наблюдение"],
                "strategy_6_format": ["мини-пост"],
                "strategy_6_note": ["Выходной"],
            }

            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path), patch("post_agent.web.EDITORIAL_STRATEGY_PATH", strategy_path), patch("post_agent.web.AIGateway", return_value=Gateway()):
                location = _save_content_plan_form(form)
                saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(location, "/content-plan?saved=1&status=updated&view=calendar")
        # The user's manual week focus must be preserved, not overwritten by the AI's echo.
        self.assertEqual(saved["focus"], "Old")
        self.assertEqual(saved["planned_publications"][0]["date"], "2026-06-22")
        self.assertEqual(saved["planned_publications"][1]["date"], "2026-06-23")
        self.assertEqual(saved["planned_publications"][0]["platform"], "LinkedIn")
        self.assertEqual(saved["planned_publications"][0]["pillar"], "Аналитика")
        self.assertEqual(saved["planned_publications"][0]["format"], "экспертный пост")
        self.assertEqual(saved["planned_publications"][1]["platform"], "Telegram")
        self.assertEqual(saved["planned_publications"][1]["pillar"], "Наблюдение")
        self.assertEqual(saved["planned_publications"][0]["topic"], "Generated Monday")
        self.assertEqual(len(saved["planned_publications"]), 2)
        self.assertTrue(saved["last_action"].startswith("Создан план по редакционной стратегии."))
        self.assertTrue(saved["updated_at"])

    def test_content_plan_strategy_respects_calendar_weekdays(self) -> None:
        class Gateway:
            def complete_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict[str, object]:
                return {
                    "content_plan": {
                        "planned_publications": [
                            {"topic": "Generated Saturday", "goal": "Goal", "summary": "Summary", "status": "planned", "note": "Note"},
                            {"topic": "Generated Monday", "goal": "Goal", "summary": "Summary", "status": "planned", "note": "Note"},
                        ],
                    },
                }

        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "content_plan.json"
            strategy_path = Path(directory) / "editorial_strategy.json"
            plan_path.write_text("{}", encoding="utf-8")
            form = {
                "view": ["list"],
                "plan_action": ["strategy_plan"],
                "week_start": ["2026-07-04"],
                "week_end": ["2026-07-06"],
                "focus": ["Focus"],
                "month_focus": ["Month"],
                "content_pillars": ["Operations"],
                "platform_targets": ["LinkedIn"],
                "today_recommendation": ["Today"],
                "strategy_0_day": ["Понедельник"],
                "strategy_0_active": ["on"],
                "strategy_0_platform": ["LinkedIn"],
                "strategy_0_rubric": ["Аналитика"],
                "strategy_0_format": ["экспертный пост"],
                "strategy_0_note": [""],
                "strategy_1_day": ["Вторник"],
                "strategy_1_platform": ["Telegram"],
                "strategy_1_rubric": ["Наблюдение"],
                "strategy_1_format": ["короткий пост"],
                "strategy_1_note": [""],
                "strategy_2_day": ["Среда"],
                "strategy_2_platform": ["VC"],
                "strategy_2_rubric": ["Кейс"],
                "strategy_2_format": ["статья"],
                "strategy_2_note": [""],
                "strategy_3_day": ["Четверг"],
                "strategy_3_platform": ["LinkedIn"],
                "strategy_3_rubric": ["Framework"],
                "strategy_3_format": ["карусель/пост"],
                "strategy_3_note": [""],
                "strategy_4_day": ["Пятница"],
                "strategy_4_platform": ["Telegram"],
                "strategy_4_rubric": ["Разговорный пост"],
                "strategy_4_format": ["короткий пост"],
                "strategy_4_note": [""],
                "strategy_5_day": ["Суббота"],
                "strategy_5_active": ["on"],
                "strategy_5_platform": ["Сетка"],
                "strategy_5_rubric": ["Наблюдение"],
                "strategy_5_format": ["мини-пост"],
                "strategy_5_note": [""],
                "strategy_6_day": ["Воскресенье"],
                "strategy_6_platform": ["Telegram"],
                "strategy_6_rubric": ["Наблюдение"],
                "strategy_6_format": ["мини-пост"],
                "strategy_6_note": ["Выходной"],
            }

            with patch("post_agent.web.DEFAULT_CONTENT_PLAN_PATH", plan_path), patch("post_agent.web.EDITORIAL_STRATEGY_PATH", strategy_path), patch("post_agent.web.AIGateway", return_value=Gateway()):
                _save_content_plan_form(form)
                saved = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["planned_publications"][0]["date"], "2026-07-04")
        self.assertEqual(saved["planned_publications"][0]["day"], "Суббота")
        self.assertEqual(saved["planned_publications"][0]["platform"], "Сетка")
        self.assertEqual(saved["planned_publications"][0]["format"], "мини-пост")
        self.assertEqual(saved["planned_publications"][1]["date"], "2026-07-06")
        self.assertEqual(saved["planned_publications"][1]["day"], "Понедельник")
        self.assertEqual(saved["planned_publications"][1]["platform"], "LinkedIn")
        self.assertEqual(saved["planned_publications"][1]["format"], "экспертный пост")

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
