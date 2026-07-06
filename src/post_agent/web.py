from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ast
import json
import re
import socket
import threading
from urllib.parse import parse_qs, quote, urlparse

from .ai_gateway import AIGateway, AIGatewayError
from .ai_context import AIContextEngine
from .ai_pipeline import AIPipeline, ai_diagnostics, load_ai_result, load_ai_status
from .author_brain import THEME_WEIGHT_RULE, AuthorBrain, AuthorBrainRepository
from .author_profile import AuthorProfileRepository, list_to_text, text_to_list
from .daily_brief import (
    DEFAULT_CONTENT_PLAN_PATH,
    BriefItem,
    ContentPlan,
    DailyBrief,
    DailyBriefService,
    Draft,
    PlannedPublication,
    parse_plan_date,
    refresh_stale_content_plan,
    today_moscow,
    weekday_name_for_date,
)
from .idea_vault import IDEA_STATUSES, Idea, IdeaVault
from .knowledge import KnowledgeBase, KnowledgeSearchResult, SUPPORTED_EXTENSIONS
from .knowledge_graph import KnowledgeGraph
from .learning import LearningCenter, lessons_for_prompt
from .memory import MemoryInbox
from .memory_notes import MEMORY_NOTE_CATEGORIES, MEMORY_NOTE_LABELS, MemoryNote, MemoryNoteStore
from .text_posts import TEXT_POST_STATUSES, TextPost, TextPostRepository, source_key_for_publication
from .trend_radar import TrendRadar
from .writing_dna import WritingDNARepository, writing_dna_form_to_raw


REFINEMENT_ACTIONS = (
    "Обновить заголовок",
    "Другой вариант",
    "Сделать сильнее",
    "Сделать мягче",
)
UI_STATE_PATH = DEFAULT_CONTENT_PLAN_PATH.parents[1] / "ui_state.json"
AI_ACTION_DIAGNOSTICS_PATH = DEFAULT_CONTENT_PLAN_PATH.parents[1] / "ai" / "action_errors.json"
AI_TIMEOUT_MESSAGE = "AI не успел ответить. Попробуйте еще раз."


CONTENT_PLATFORMS = ("LinkedIn", "Telegram", "VC", "Сетка")
RUBRICS = (
    "Аналитика",
    "Кейс",
    "Framework",
    "Наблюдение",
    "Разбор ошибки",
    "Миф",
    "Storytelling",
    "Разговорный пост",
    "Инструменты",
    "Ответ на вопрос",
)
PUBLICATION_FORMATS = ("экспертный пост", "короткий пост", "статья", "карусель/пост", "мини-пост", "пост")
# One shared 3-stage lifecycle for both the content plan and Тексты.
PUBLICATION_STATUSES = ("draft", "approved", "published")
EDITORIAL_STRATEGY_PATH = DEFAULT_CONTENT_PLAN_PATH.parents[1] / "seeds" / "editorial_strategy.json"
RUBRIC_LIBRARY = {
    "Аналитика": ("проблема", "причина", "закономерность", "управленческий вывод"),
    "Кейс": ("проблема", "действия", "результат", "бизнес-эффект", "урок"),
    "Framework": ("модель", "3-5 элементов", "применение", "вывод"),
    "Наблюдение": ("рабочая ситуация", "вывод", "вопрос к аудитории"),
    "Разбор ошибки": ("ошибка", "почему возникает", "как исправить", "профилактика"),
    "Миф": ("миф", "почему он живет", "что происходит на практике", "новая формулировка"),
    "Storytelling": ("ситуация", "напряжение", "поворот", "смысл"),
    "Разговорный пост": ("живой тон", "личная мысль", "без академического стиля"),
    "Инструменты": ("задача", "инструмент", "как применять", "ограничение"),
    "Ответ на вопрос": ("вопрос", "короткий ответ", "логика", "пример"),
}
DEFAULT_EDITORIAL_STRATEGY = {
    "updated_at": "",
    "rubric_library": RUBRIC_LIBRARY,
    "weekly_template": [
        {"day": "Понедельник", "platform": "LinkedIn", "rubric": "Аналитика", "format": "экспертный пост", "active": True, "note": ""},
        {"day": "Вторник", "platform": "Telegram", "rubric": "Наблюдение", "format": "короткий пост", "active": True, "note": ""},
        {"day": "Среда", "platform": "VC", "rubric": "Кейс", "format": "статья", "active": True, "note": ""},
        {"day": "Четверг", "platform": "LinkedIn", "rubric": "Framework", "format": "карусель/пост", "active": True, "note": ""},
        {"day": "Пятница", "platform": "Telegram", "rubric": "Разговорный пост", "format": "короткий пост", "active": True, "note": ""},
        {"day": "Суббота", "platform": "Сетка", "rubric": "Наблюдение", "format": "мини-пост", "active": True, "note": ""},
        {"day": "Воскресенье", "platform": "Telegram", "rubric": "Наблюдение", "format": "мини-пост", "active": False, "note": "Выходной"},
    ],
}


class DailyBriefRequestHandler(BaseHTTPRequestHandler):
    # A friendly Russian page instead of the default English server error text.
    error_content_type = "text/html; charset=utf-8"
    error_message_format = (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Что-то пошло не так</title></head>"
        "<body style=\"font-family: system-ui, -apple-system, sans-serif; background:#121317;"
        " color:#ecebf0; margin:0; min-height:100vh; display:flex; align-items:center;"
        " justify-content:center;\">"
        "<div style=\"max-width:440px; padding:32px; text-align:center;\">"
        "<div style=\"font-size:44px; margin-bottom:14px;\" aria-hidden=\"true\">🙂</div>"
        "<h1 style=\"font-size:22px; margin:0 0 10px;\">Не получилось открыть страницу</h1>"
        "<p style=\"color:#9aa09a; margin:0 0 22px; line-height:1.5;\">Возможно, ссылка устарела или"
        " такой страницы больше нет. Ничего страшного — вернитесь на главную и продолжайте работу.</p>"
        "<a href=\"/daily-brief\" style=\"display:inline-block; padding:12px 22px; border-radius:999px;"
        " background:#f0604a; color:#fff; text-decoration:none; font-weight:700;\">На главную</a>"
        "<p style=\"color:#5a615c; margin:18px 0 0; font-size:12px;\">Код: %(code)d</p>"
        "</div></body></html>"
    )
    service = DailyBriefService()
    author_profile_repository = AuthorProfileRepository()
    writing_dna_repository = WritingDNARepository()
    memory_inbox = MemoryInbox()
    knowledge_graph = KnowledgeGraph()
    learning_center = LearningCenter()
    knowledge_base = KnowledgeBase()
    idea_vault = IdeaVault()
    memory_notes = MemoryNoteStore()
    text_posts = TextPostRepository()
    author_brain_repository = AuthorBrainRepository()
    trend_radar = TrendRadar(learning_center=learning_center)
    ai_context_engine = AIContextEngine(
        author_profile_repository=author_profile_repository,
        writing_dna_repository=writing_dna_repository,
        author_brain_repository=author_brain_repository,
        knowledge_base=knowledge_base,
        memory_inbox=memory_inbox,
        knowledge_graph=knowledge_graph,
        learning_center=learning_center,
        idea_vault=idea_vault,
        text_post_repository=text_posts,
    )
    ai_pipeline = AIPipeline(
        knowledge_base=knowledge_base,
        idea_vault=idea_vault,
        author_profile_repository=author_profile_repository,
        writing_dna_repository=writing_dna_repository,
        memory_inbox=memory_inbox,
        knowledge_graph=knowledge_graph,
        learning_center=learning_center,
    )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/daily-brief"):
            self._send_html(
                render_daily_brief(
                    self.service.build_today(),
                    pending_memory=len(self.memory_inbox.list_items("pending")),
                    pending_lessons=len(self.learning_center.list_lessons("candidate")),
                )
            )
            return
        if path == "/author-profile":
            query = parse_qs(urlparse(self.path).query)
            self._send_html(
                render_author_profile(
                    self.author_profile_repository.load_raw(),
                    saved=query.get("saved", ["0"])[0] == "1",
                    tab=query.get("tab", ["base"])[0],
                    dna=self.writing_dna_repository.load_raw(),
                    brain_profile=self.author_brain_repository.load_profile(),
                    brain_status=self.author_brain_repository.load_status(),
                    learning_center=self.learning_center,
                    memory_inbox=self.memory_inbox,
                    knowledge_graph=self.knowledge_graph,
                    refreshed=query.get("refreshed", ["0"])[0] == "1",
                    base_saved=query.get("base_saved", ["0"])[0] == "1",
                    dna_saved=query.get("dna_saved", ["0"])[0] == "1",
                    learning_saved=query.get("learning_saved", ["0"])[0] == "1",
                    rule_saved=query.get("rule_saved", ["0"])[0] == "1",
                    learning_settings_saved=query.get("learning_settings_saved", ["0"])[0] == "1",
                    strategy_saved=query.get("strategy_saved", ["0"])[0] == "1",
                )
            )
            return
        if path == "/bot-rules":
            from .bot_rules import load_bot_rules

            query = parse_qs(urlparse(self.path).query)
            self._send_html(render_bot_rules(load_bot_rules(), saved=query.get("saved", ["0"])[0] == "1"))
            return
        if path == "/how-it-works":
            self._send_html(render_how_it_works())
            return
        if path == "/author-brain":
            self.send_response(303)
            self.send_header("Location", "/author-profile?tab=base")
            self.end_headers()
            return
        if path == "/writing-dna":
            self.send_response(303)
            self.send_header("Location", "/author-profile?tab=dna")
            self.end_headers()
            return
        if path == "/learning":
            query = parse_qs(urlparse(self.path).query)
            self._send_html(
                render_learning_center(
                    self.learning_center,
                    self.memory_inbox,
                    self.knowledge_graph,
                    saved=query.get("saved", ["0"])[0] == "1",
                )
            )
            return
        if path == "/trend-radar":
            query = parse_qs(urlparse(self.path).query)
            cache = self.trend_radar.get_cached()
            has_data = bool(cache.get("topics"))
            stale = self.trend_radar.is_stale()
            refreshing = _trend_refresh_in_progress()
            if stale and not has_data:
                # Nothing to show yet — do a one-time synchronous build so the page isn't empty.
                _refresh_trend_radar_now()
                cache = self.trend_radar.get_cached()
                stale = self.trend_radar.is_stale()
            elif stale or query.get("refreshing", ["0"])[0] == "1":
                # Serve cached data immediately and refresh in the background.
                refreshing = _start_trend_radar_refresh_background() or _trend_refresh_in_progress()
            self._send_html(
                render_trend_radar(
                    cache,
                    saved=query.get("saved", ["0"])[0] == "1",
                    stale=stale,
                    refreshing=refreshing,
                )
            )
            return
        if path == "/content-plan":
            query = parse_qs(urlparse(self.path).query)
            saved = query.get("saved", ["0"])[0] == "1"
            view = query.get("view", ["list"])[0]
            action_status = query.get("status", [""])[0]
            plan = _content_plan_with_query_period(_load_content_plan_raw(), query)
            published_posts = _published_posts_for_calendar(plan) if view == "calendar" else None
            self._send_html(render_content_plan_page(plan, saved=saved, view=view, action_status=action_status, published_posts=published_posts))
            return
        if path == "/content-plan/open-text":
            query = parse_qs(urlparse(self.path).query)
            plan = _load_content_plan_raw()
            self.text_posts.sync_from_content_plan(plan)
            target = _text_post_for_publication(
                self.text_posts,
                query.get("platform", [""])[0],
                query.get("topic", [""])[0],
            )
            location = f"/texts/{target.id}" if target else "/texts?tab=planned"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()
            return
        if path == "/texts":
            query = parse_qs(urlparse(self.path).query)
            plan = _content_plan_with_query_period(_load_content_plan_raw(), query)
            self.text_posts.sync_from_content_plan(plan)
            self._send_html(render_text_posts_page(self.text_posts, query, plan))
            return
        if path.startswith("/texts/"):
            post_id = path.rsplit("/", 1)[-1]
            post = self.text_posts.get(post_id)
            if not post:
                self.send_error(404, "Not Found")
                return
            query_params = parse_qs(urlparse(self.path).query)
            self._send_html(
                render_text_post_detail(
                    post,
                    saved=query_params.get("saved", ["0"])[0] == "1",
                    generated=query_params.get("generated", ["0"])[0] == "1",
                    gen_error=query_params.get("gen_error", [""])[0],
                    revised=query_params.get("revised", ["0"])[0] == "1",
                    revise_error=query_params.get("revise_error", [""])[0],
                )
            )
            return
        if path == "/knowledge":
            query_params = parse_qs(urlparse(self.path).query)
            uploaded = query_params.get("uploaded", ["0"])[0] == "1"
            upload_error = query_params.get("upload_error", [""])[0]
            analysis = query_params.get("analysis", [""])[0]
            deleted = query_params.get("deleted", ["0"])[0] == "1"
            case_saved = query_params.get("case_saved", ["0"])[0] == "1"
            case_deleted = query_params.get("case_deleted", ["0"])[0] == "1"
            note_saved = query_params.get("note_saved", ["0"])[0] == "1"
            note_deleted = query_params.get("note_deleted", ["0"])[0] == "1"
            idea_saved = query_params.get("idea_saved", ["0"])[0] == "1"
            section = query_params.get("section", ["documents"])[0]
            self.knowledge_base.ensure_seed_documents()
            self._send_html(
                render_knowledge(
                    self.knowledge_base.list_documents(),
                    cases=self.knowledge_base.list_cases(),
                    ideas=self.idea_vault.list_ideas(),
                    notes=self.memory_notes.list_notes(),
                    uploaded=uploaded,
                    analysis=analysis,
                    upload_error=upload_error,
                    deleted=deleted,
                    case_saved=case_saved,
                    case_deleted=case_deleted,
                    note_saved=note_saved,
                    note_deleted=note_deleted,
                    idea_saved=idea_saved,
                    section=section,
                )
            )
            return
        if path == "/ideas":
            query = parse_qs(urlparse(self.path).query)
            self._send_html(
                render_idea_vault(
                    self.idea_vault.list_ideas(),
                    saved=query.get("saved", ["0"])[0] == "1",
                    deleted=query.get("deleted", ["0"])[0] == "1",
                    updated=query.get("updated", ["0"])[0] == "1",
                )
            )
            return
        if path.startswith("/ideas/"):
            idea_id = path.rsplit("/", 1)[-1]
            idea = self.idea_vault.get_idea(idea_id)
            if not idea:
                self.send_error(404, "Not Found")
                return
            query = parse_qs(urlparse(self.path).query)
            self._send_html(render_idea_detail(idea, planned=query.get("planned", [""])[0]))
            return
        if path.startswith("/knowledge/"):
            document_id = path.rsplit("/", 1)[-1]
            document = self.knowledge_base.get_document(document_id)
            if not document:
                self.send_error(404, "Not Found")
                return
            self._send_html(render_knowledge_document(document))
            return
        if path == "/health":
            self._send_text("ok")
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/author-profile":
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            data = parse_qs(payload)
            # The DNA tab is one form now: it carries both author-profile style
            # fields and Writing DNA generation rules, saved together.
            self.author_profile_repository.save_raw(_author_profile_form_to_raw(data))
            self.writing_dna_repository.save_raw(writing_dna_form_to_raw(data))
            self.send_response(303)
            self.send_header("Location", "/author-profile?tab=dna&saved=1")
            self.end_headers()
            return
        if path == "/writing-dna":
            # Backward compatibility: DNA is saved via /author-profile now.
            self.send_response(303)
            self.send_header("Location", "/author-profile?tab=dna")
            self.end_headers()
            return
        if path == "/bot-rules":
            from .bot_rules import save_bot_rules

            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            save_bot_rules(_bot_rules_form_to_raw(data))
            self.send_response(303)
            self.send_header("Location", "/bot-rules?saved=1")
            self.end_headers()
            return
        if path == "/author-profile/base":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            _save_author_base_form(data, self.author_brain_repository)
            self.send_response(303)
            self.send_header("Location", "/author-profile?tab=base&base_saved=1")
            self.end_headers()
            return
        if path == "/author-profile/strategy":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            redirect_target = _save_author_strategy_form(data)
            self.send_response(303)
            self.send_header("Location", redirect_target)
            self.end_headers()
            return
        if path == "/author-profile/learning-settings":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            _save_learning_settings_form(data, self.author_brain_repository)
            self.send_response(303)
            self.send_header("Location", "/bot-rules?saved=1")
            self.end_headers()
            return
        if path == "/author-profile/rules/add":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            rule = data.get("rule", [""])[0].strip()
            reason = data.get("reason", [""])[0].strip()
            if rule:
                self.learning_center.create_rule(rule=rule, reason=reason or "Правило добавлено вручную.", source="manual")
            self.send_response(303)
            self.send_header("Location", "/learning?saved=1")
            self.end_headers()
            return
        if path == "/content-plan/publish":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            _mark_plan_publication_published(data.get("platform", [""])[0], data.get("topic", [""])[0])
            self.send_response(303)
            self.send_header("Location", "/daily-brief")
            self.end_headers()
            return
        if path == "/content-plan":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            redirect_target = _save_content_plan_form(data)
            self.send_response(303)
            self.send_header("Location", redirect_target)
            self.end_headers()
            return
        if path in ("/texts/archive/add", "/texts/planned/add"):
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            adder = self.text_posts.add_archive if path.endswith("/archive/add") else self.text_posts.add_planned
            post = adder(
                title=data.get("title", [""])[0],
                platform=data.get("platform", [""])[0],
                publication_date=data.get("publication_date", [""])[0],
                text=data.get("text", [""])[0],
            )
            self.send_response(303)
            self.send_header("Location", f"/texts/{post.id}?saved=1")
            self.end_headers()
            return
        if path.startswith("/texts/"):
            post_id = path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            action = data.get("action", ["save"])[0]
            post = self.text_posts.get(post_id)
            if not post:
                self.send_error(404, "Not Found")
                return
            if action == "archive":
                # «Отметить опубликованным»: publish today unless it already has a real past date.
                parsed = parse_plan_date(post.publication_date)
                pub_date = post.publication_date if (parsed and parsed <= today_moscow()) else today_moscow().isoformat()
                self.text_posts.update(
                    post_id=post_id,
                    title=post.title,
                    platform=post.platform,
                    publication_date=pub_date,
                    text=post.text,
                    status="published",
                    tab="archive",
                )
                location = "/texts?tab=archive&saved=1"
            elif action == "delete":
                tab = post.tab
                self.text_posts.delete(post_id)
                location = f"/texts?tab={tab}&deleted=1"
            elif action == "generate":
                title = data.get("title", [""])[0]
                platform = data.get("platform", [""])[0]
                result = _generate_post_text(title or post.title, platform or post.platform, post.brief)
                new_text = str(result.get("text", "")).strip() or data.get("text", [""])[0]
                self.text_posts.update(
                    post_id=post_id,
                    title=title,
                    platform=platform,
                    publication_date=data.get("publication_date", [""])[0],
                    text=new_text,
                    status=data.get("status", ["draft"])[0],
                )
                if result.get("text"):
                    location = f"/texts/{post_id}?generated=1"
                else:
                    location = f"/texts/{post_id}?gen_error={quote(str(result.get('error', 'AI недоступен'))[:200])}"
            elif action == "revise":
                title = data.get("title", [""])[0]
                platform = data.get("platform", [""])[0]
                current_text = data.get("text", [""])[0]
                instruction = data.get("edit_instruction", [""])[0]
                result = _revise_post_text(current_text, instruction, title or post.title, platform or post.platform)
                new_text = str(result.get("text", "")).strip() or current_text
                self.text_posts.update(
                    post_id=post_id,
                    title=title,
                    platform=platform,
                    publication_date=data.get("publication_date", [""])[0],
                    text=new_text,
                    status=data.get("status", ["draft"])[0],
                )
                if result.get("text"):
                    location = f"/texts/{post_id}?revised=1"
                else:
                    location = f"/texts/{post_id}?revise_error={quote(str(result.get('error', 'AI недоступен'))[:200])}"
            else:
                self.text_posts.update(
                    post_id=post_id,
                    title=data.get("title", [""])[0],
                    platform=data.get("platform", [""])[0],
                    publication_date=data.get("publication_date", [""])[0],
                    text=data.get("text", [""])[0],
                    status=data.get("status", ["draft"])[0],
                )
                location = f"/texts/{post_id}?saved=1"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()
            return
        if path == "/daily-brief/refine":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            item_key = data.get("item_key", [""])[0]
            action = data.get("action", [""])[0]
            original_title = data.get("title", [""])[0]
            original_text = data.get("text", [""])[0]
            kind = data.get("kind", ["text"])[0]
            state = _load_ui_state()
            refinements = state.setdefault("refinements", {})
            if isinstance(refinements, dict):
                try:
                    refinements[item_key] = _refine_with_ai(action, original_title, original_text, kind)
                except Exception as exc:
                    refinements[item_key] = {
                        "action": action,
                        "status": "error",
                        "kind": kind,
                        "title": original_title,
                        "text": original_text,
                        "error": f"Не удалось выполнить действие: {exc}",
                    }
            _save_ui_state(state)
            self.send_response(303)
            self.send_header("Location", f"/daily-brief#{quote(item_key, safe='-')}")
            self.end_headers()
            return
        if path == "/daily-brief/feedback":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            item_key = data.get("item_key", [""])[0]
            title = data.get("title", [""])[0]
            text = data.get("text", [""])[0]
            feedback = data.get("feedback", [""])[0]
            intent = data.get("intent", ["draft"])[0]
            if intent == "lesson":
                self.learning_center.create_candidate_from_feedback(feedback, title)
                location = "/learning?saved=1"
            else:
                state = _load_ui_state()
                refinements = state.setdefault("refinements", {})
                if isinstance(refinements, dict):
                    refinements[item_key] = _apply_feedback_with_ai(title, text, feedback)
                _save_ui_state(state)
                location = f"/daily-brief#{quote(item_key, safe='-')}"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()
            return
        if path == "/daily-brief/ai-refresh":
            self._refresh_author_brain_background()
            started = self.ai_pipeline.start_background()
            if not started:
                pass
            self.send_response(303)
            self.send_header("Location", "/daily-brief")
            self.end_headers()
            return
        if path == "/trend-radar/refresh":
            _start_trend_radar_refresh_background()
            self.send_response(303)
            self.send_header("Location", "/trend-radar?refreshing=1")
            self.end_headers()
            return
        if path == "/trend-radar/action":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            topic_id = data.get("topic_id", [""])[0]
            action = data.get("action", [""])[0]
            topic = self.trend_radar.get_topic(topic_id)
            if topic and action == "saved":
                self.idea_vault.add_idea(
                    title=str(topic.get("title", "")),
                    description=str(topic.get("description", "")),
                    source="Trend Radar",
                    platforms=tuple(str(item) for item in topic.get("best_formats", []) if item),
                    status="New",
                )
            elif topic and action == "planned":
                _add_trend_to_content_plan(topic)
            elif topic and action == "drafted":
                self.idea_vault.add_idea(
                    title=str(topic.get("title", "")),
                    description=str(topic.get("description", "")),
                    source="Trend Radar: черновик",
                    platforms=tuple(str(item) for item in topic.get("best_formats", []) if item),
                    status="Drafted",
                )
            if action in {"approved", "rejected", "saved", "planned", "drafted"}:
                self.trend_radar.apply_decision(topic_id, action)
            self.send_response(303)
            self.send_header("Location", "/trend-radar?saved=1")
            self.end_headers()
            return
        if path.startswith("/learning/lesson/"):
            lesson_id = path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            self.learning_center.update(
                lesson_id,
                data.get("status", ["candidate"])[0],
                rule=data.get("rule", [None])[0],
            )
            self.send_response(303)
            self.send_header("Location", "/learning?saved=1")
            self.end_headers()
            return
        if path.startswith("/memory-inbox/"):
            item_id = path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            status = data.get("status", ["pending"])[0]
            if status == "accepted":
                self.memory_inbox.accept(item_id)
            elif status == "rejected":
                self.memory_inbox.reject(item_id)
            self.knowledge_base.rebuild_graph()
            self.send_response(303)
            self.send_header("Location", "/learning?saved=1")
            self.end_headers()
            return
        if path == "/knowledge/cases/add":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            self.knowledge_base.add_case(
                title=data.get("title", [""])[0],
                company=data.get("company", [""])[0],
                what_happened=data.get("what_happened", [""])[0],
                reason=data.get("reason", [""])[0],
                solution=data.get("solution", [""])[0],
                result=data.get("result", [""])[0],
                public_usage=data.get("public_usage", ["Не указано"])[0],
                key_topics=_csv_to_tuple(data.get("key_topics", [""])[0]),
                platforms=_csv_to_tuple(data.get("platforms", [""])[0]),
            )
            self.send_response(303)
            self.send_header("Location", "/knowledge?section=cases&case_saved=1")
            self.end_headers()
            return
        if path.startswith("/knowledge/cases/delete/"):
            case_id = path.rsplit("/", 1)[-1]
            self.knowledge_base.delete_case(case_id)
            self.send_response(303)
            self.send_header("Location", "/knowledge?section=cases&case_deleted=1")
            self.end_headers()
            return
        if path == "/knowledge/notes/add":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            category = data.get("category", [""])[0]
            self.memory_notes.add_note(
                category=category,
                text=data.get("text", [""])[0],
                title=data.get("title", [""])[0],
            )
            self._refresh_author_brain_background()
            section = _note_category_to_section(category)
            self.send_response(303)
            self.send_header("Location", f"/knowledge?section={section}&note_saved=1")
            self.end_headers()
            return
        if path.startswith("/knowledge/notes/delete/"):
            note_id = path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            section = _knowledge_section(data.get("section", ["observations"])[0])
            self.memory_notes.delete_note(note_id)
            self._refresh_author_brain_background()
            self.send_response(303)
            self.send_header("Location", f"/knowledge?section={section}&note_deleted=1")
            self.end_headers()
            return
        if path == "/knowledge/ideas/add":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            text = data.get("text", [""])[0].strip()
            if text:
                first_line = text.splitlines()[0].strip()
                title = first_line if len(first_line) <= 80 else first_line[:77] + "..."
                self.idea_vault.add_idea(
                    title=title or "Идея",
                    description=text,
                    source="Память",
                    platforms=(),
                    status="New",
                )
            self.send_response(303)
            self.send_header("Location", "/knowledge?section=ideas&idea_saved=1")
            self.end_headers()
            return
        if path.startswith("/knowledge/ideas/delete/"):
            idea_id = path.rsplit("/", 1)[-1]
            self.idea_vault.delete_idea(idea_id)
            self.send_response(303)
            self.send_header("Location", "/knowledge?section=ideas&note_deleted=1")
            self.end_headers()
            return
        if path == "/knowledge/upload":
            upload_error = ""
            max_upload = 15 * 1024 * 1024  # keep a big file from OOM-killing the small free instance
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > max_upload:
                # Do not read a huge body into memory; close the connection instead of draining it.
                self.close_connection = True
                upload_error = "Файл слишком большой. Максимум 15 МБ."
            else:
                try:
                    filename, content = self._read_multipart_file()
                    if filename and content is not None:
                        self.knowledge_base.add_document(filename, content)
                        self._refresh_author_brain_background()
                    else:
                        upload_error = "Файл не выбран."
                except ValueError:
                    upload_error = "Не удалось загрузить документ. Поддерживаются PDF, DOCX, Markdown и TXT."
                except Exception as exc:
                    _save_ai_action_error("knowledge_upload", exc)
                    upload_error = "Не удалось обработать документ. Попробуйте другой файл."
            self.send_response(303)
            location = "/knowledge?section=documents&uploaded=1&analysis=done" if not upload_error else f"/knowledge?section=documents&analysis=error&upload_error={quote(upload_error)}"
            self.send_header("Location", location)
            self.end_headers()
            return
        if path == "/author-brain/refresh":
            self._refresh_author_brain_background()
            self.send_response(303)
            self.send_header("Location", "/author-profile?tab=base&refreshed=1")
            self.end_headers()
            return
        if path.startswith("/knowledge/delete/"):
            document_id = path.rsplit("/", 1)[-1]
            self.knowledge_base.delete_document(document_id)
            self.send_response(303)
            self.send_header("Location", "/knowledge?section=documents&deleted=1")
            self.end_headers()
            return
        if path == "/ideas/add":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            self.idea_vault.add_idea(
                title=data.get("title", [""])[0],
                description=data.get("description", [""])[0],
                source=data.get("source", ["Вручную"])[0],
                platforms=_platforms_from_form(data),
                status=data.get("status", ["New"])[0],
            )
            self.send_response(303)
            self.send_header("Location", "/ideas?saved=1")
            self.end_headers()
            return
        if path == "/ideas/key-ideas":
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            _save_key_ideas_form(data, self.author_brain_repository)
            self.send_response(303)
            self.send_header("Location", "/ideas?updated=1#key-ideas")
            self.end_headers()
            return
        if path.startswith("/ideas/plan/"):
            idea_id = path.rsplit("/", 1)[-1]
            idea = self.idea_vault.get_idea(idea_id)
            added = _add_idea_to_content_plan(idea) if idea else False
            self.send_response(303)
            self.send_header("Location", f"/ideas/{idea_id}?planned={'1' if added else 'exists'}")
            self.end_headers()
            return
        if path.startswith("/ideas/status/"):
            idea_id = path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length", "0"))
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            self.idea_vault.update_status(idea_id, data.get("status", ["New"])[0])
            self.send_response(303)
            self.send_header("Location", f"/ideas/{idea_id}")
            self.end_headers()
            return
        if path.startswith("/ideas/delete/"):
            idea_id = path.rsplit("/", 1)[-1]
            self.idea_vault.delete_idea(idea_id)
            self.send_response(303)
            self.send_header("Location", "/ideas?deleted=1")
            self.end_headers()
            return
        self.send_error(404, "Not Found")

    def _current_author_brain(self) -> AuthorBrain:
        return AuthorBrain(
            author_profile=self.author_profile_repository.load_raw(),
            writing_dna=self.writing_dna_repository.load_raw(),
            documents=self.knowledge_base.list_documents(),
            cases=self.knowledge_base.list_cases(),
            ideas=self.idea_vault.list_ideas(),
            lessons=self.learning_center.list_lessons("accepted"),
        )

    def _refresh_author_brain_background(self) -> bool:
        self.knowledge_base.ensure_seed_documents()
        return self.author_brain_repository.start_background_refresh(self._current_author_brain())

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_html(self, body: str) -> None:
        if "</body>" in body:
            body = body.replace("</body>", _global_script() + "</body>", 1)
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_multipart_file(self) -> tuple[str | None, bytes | None]:
        content_type = self.headers.get("Content-Type", "")
        marker = "boundary="
        if marker not in content_type:
            return None, None
        boundary = content_type.split(marker, 1)[1].encode("utf-8")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        parts = body.split(b"--" + boundary)
        for part in parts:
            if b'name="document"' not in part or b"filename=" not in part:
                continue
            header, _, content = part.partition(b"\r\n\r\n")
            filename = _filename_from_multipart_header(header)
            if not filename:
                return None, None
            content = content.rsplit(b"\r\n", 1)[0]
            return filename, content
        return None, None


def _global_nav(active: str = "", extra: str = "") -> str:
    # Grouped by user intent: desktop = persistent left sidebar, mobile = burger menu.
    groups = (
        ("Сегодня", (("Дневной бриф", "/daily-brief", "daily"),)),
        (
            "Планирование",
            (
                ("Контент-план", "/content-plan", "content"),
                ("Тексты", "/texts", "texts"),
                ("Идеи", "/ideas", "ideas"),
            ),
        ),
        ("Память", (("Память", "/knowledge", "knowledge"),)),
        ("Сигналы", (("Радар трендов", "/trend-radar", "trends"),)),
        (
            "Настройки",
            (
                ("Профиль автора", "/author-profile", "profile"),
                ("Правила бота", "/bot-rules", "bot-rules"),
                ("Обучение", "/learning", "learning"),
            ),
        ),
    )
    sections = ""
    for group_label, links in groups:
        items = "".join(
            "<a class=\"nav-link{active}\" href=\"{href}\"{current}>{label}</a>".format(
                active=" active" if key == active else "",
                href=escape(href),
                current=' aria-current="page"' if key == active else "",
                label=escape(label),
            )
            for label, href, key in links
        )
        sections += f"<p class=\"nav-group\">{escape(group_label)}</p>{items}"
    extra_html = f"<p class=\"sidebar-extra\">{escape(extra)}</p>" if extra else ""
    how_link = (
        "<a class=\"sidebar-foot-link{active}\" href=\"/how-it-works\"{current}>Как это связано</a>".format(
            active=" active" if active == "how" else "",
            current=' aria-current="page"' if active == "how" else "",
        )
    )
    return (
        "<input type=\"checkbox\" id=\"nav-toggle\" class=\"nav-toggle\" hidden>"
        "<label for=\"nav-toggle\" class=\"burger\" aria-label=\"Открыть меню\">☰</label>"
        "<label for=\"nav-toggle\" class=\"nav-backdrop\" aria-hidden=\"true\"></label>"
        "<aside class=\"sidebar\">"
        "<a class=\"brand\" href=\"/daily-brief\">Personal Brand OS</a>"
        f"<nav class=\"sidebar-nav\" aria-label=\"Основная навигация\">{sections}</nav>"
        f"<div class=\"sidebar-foot\">{how_link}{extra_html}{_cloud_status_chip()}</div>"
        "</aside>"
    )


def _cloud_status_chip() -> str:
    """A small, reassuring indicator of whether data is saved to the cloud."""
    try:
        from .remote_sync import status as sync_status

        state = sync_status()
    except Exception:  # noqa: BLE001 - the indicator must never break a page
        return ""
    if not state.get("enabled"):
        return (
            '<p class="cloud-chip cloud-local" role="status">'
            '<span aria-hidden="true">💾</span> Сохраняется на этом устройстве</p>'
        )
    if state.get("ok"):
        return (
            '<p class="cloud-chip cloud-ok" role="status">'
            '<span aria-hidden="true">☁️</span> Данные сохраняются в облако</p>'
        )
    return (
        '<p class="cloud-chip cloud-wait" role="status">'
        '<span aria-hidden="true">☁️</span> Подключаюсь к облаку…</p>'
    )


def local_network_url(port: int = 8000) -> str:
    ip = "127.0.0.1"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "127.0.0.1"
    finally:
        sock.close()
    return f"http://{ip}:{port}/daily-brief"


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    from .remote_sync import bootstrap as bootstrap_remote_sync

    bootstrap_remote_sync()
    server = ThreadingHTTPServer((host, port), DailyBriefRequestHandler)
    try:
        print(f"Personal Brand OS is running at http://{host}:{port}/daily-brief")
        if host in {"0.0.0.0", "::"}:
            print(f"Open from phone or iPad: {local_network_url(port)}")
    except Exception:
        pass
    server.serve_forever()


def _stored_content_plan() -> dict[str, object]:
    """The plan as actually saved on disk, with real dates (no rolling refresh).

    The reminder and the «mark published» flow need true past dates, which the
    display-time refresh would otherwise shift onto today."""
    try:
        plan = json.loads(DEFAULT_CONTENT_PLAN_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return plan if isinstance(plan, dict) else {}


def _publish_reminder_block() -> str:
    """Nudge to mark past-due publications as published, so the archive and the
    calendar stay accurate. Closes the loop between «I posted this» and the tool."""
    raw = _stored_content_plan()
    today = today_moscow()
    pending = []
    for item in raw.get("planned_publications", []):
        if not isinstance(item, dict):
            continue
        parsed = parse_plan_date(str(item.get("date", "")))
        if parsed and parsed < today and _normalize_publication_status(str(item.get("status", ""))) != "published":
            pending.append((parsed, item))
    if not pending:
        return ""
    pending.sort(key=lambda pair: pair[0], reverse=True)
    rows = ""
    for parsed, item in pending[:5]:
        platform = str(item.get("platform", ""))
        topic = str(item.get("topic", "")) or "Без темы"
        rows += f"""
        <form class="reminder-row" method="post" action="/content-plan/publish">
          <input type="hidden" name="platform" value="{escape(platform)}">
          <input type="hidden" name="topic" value="{escape(topic)}">
          <div class="reminder-info">
            <span class="reminder-date">{escape(parsed.strftime('%d.%m'))}</span>
            <span class="reminder-topic">{escape(platform)} · {escape(_short_text(topic, 56))}</span>
          </div>
          <button type="submit">Отметить опубликованным</button>
        </form>
        """
    more = f'<p class="reminder-more">…и ещё {len(pending) - 5}</p>' if len(pending) > 5 else ""
    return f"""
    <section class="publish-reminder">
      <div class="reminder-head">
        <strong>Опубликовала эти посты? Отметь — они уйдут в архив и календарь.</strong>
        <span>Прошлые публикации без статуса «Опубликовано»</span>
      </div>
      <div class="reminder-list">{rows}{more}</div>
    </section>
    """


def _mark_plan_publication_published(platform: str, topic: str) -> bool:
    """Mark the matching content-plan publication as published and sync it to the archive."""
    platform = str(platform).strip()
    topic = str(topic).strip()
    raw = _stored_content_plan()
    today = today_moscow()
    changed = False
    for item in raw.get("planned_publications", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("platform", "")).strip() == platform and str(item.get("topic", "")).strip() == topic:
            item["status"] = "published"
            parsed = parse_plan_date(str(item.get("date", "")))
            if not parsed or parsed > today:
                item["date"] = today.isoformat()
                item["day"] = weekday_name_for_date(item["date"])
            changed = True
    if changed:
        raw["updated_at"] = _now_iso()
        raw["last_action"] = "Публикация отмечена опубликованной."
        DEFAULT_CONTENT_PLAN_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        TextPostRepository().sync_from_content_plan(raw)
    return changed


def _gates_banner(pending_memory: int, pending_lessons: int) -> str:
    if pending_memory <= 0 and pending_lessons <= 0:
        return ""
    parts: list[str] = []
    if pending_memory > 0:
        parts.append(f"{pending_memory} материалов памяти")
    if pending_lessons > 0:
        parts.append(f"{pending_lessons} правил обучения")
    what = " и ".join(parts)
    return f"""
    <div class="gates-banner">
      <div class="gates-text">
        <strong>{escape(what)} ждут вашего решения.</strong>
        <span>Пока вы их не подтвердите, AI их не использует.</span>
      </div>
      <a class="gates-action" href="/learning">Открыть и решить →</a>
    </div>"""


def _page_shell(
    *,
    title: str,
    eyebrow: str,
    heading: str,
    hint: str,
    active: str,
    content: str,
    nav_extra: str = "",
    head_extra: str = "",
) -> str:
    """Single source of truth for the page chrome: <head>, sidebar nav and topbar.
    `content` is the page body inside <main class="shell"> after the header."""
    hint_html = f'\n        <p class="page-hint">{escape(hint)}</p>' if hint else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {head_extra}<title>{escape(title)} - Personal Brand OS</title>
  <style>{_styles()}</style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">{escape(eyebrow)}</p>
        <h1>{escape(heading)}</h1>{hint_html}
      </div>
      {_global_nav(active, nav_extra)}
    </header>
    {content}
  </main>
</body>
</html>"""


def render_daily_brief(brief: DailyBrief, pending_memory: int = 0, pending_lessons: int = 0) -> str:
    primary_topic = brief.topics[0] if brief.topics else None
    primary_idea = brief.ideas[0] if brief.ideas else None
    primary_recommendation = brief.recommendations[0] if brief.recommendations else None
    ai_status = load_ai_status()
    ai_result = load_ai_result()
    content = f"""
    {_gates_banner(pending_memory, pending_lessons)}

    {_publish_reminder_block()}

    {_ai_status_block(ai_status, ai_result)}

    {_today_card(brief, primary_topic, primary_idea, primary_recommendation, ai_result)}

    {_drafts_to_prepare_section(brief, ai_result)}

    {_compact_content_plan_block(brief.content_plan)}
"""
    return _page_shell(
        title="Дневной бриф",
        eyebrow="AI-директор контента",
        heading="Дневной бриф",
        hint="Что публиковать сегодня и почему — главный экран на каждый день.",
        active="daily",
        nav_extra=brief.brief_date.strftime("%d.%m.%Y"),
        head_extra=_auto_refresh_meta(ai_status) + "\n  ",
        content=content,
    )


def render_author_profile(
    profile: dict[str, object],
    saved: bool = False,
    tab: str = "base",
    dna: dict[str, object] | None = None,
    brain_profile: dict[str, object] | None = None,
    brain_status: object | None = None,
    learning_center: LearningCenter | None = None,
    memory_inbox: MemoryInbox | None = None,
    knowledge_graph: KnowledgeGraph | None = None,
    refreshed: bool = False,
    base_saved: bool = False,
    dna_saved: bool = False,
    learning_saved: bool = False,
    rule_saved: bool = False,
    learning_settings_saved: bool = False,
    strategy_saved: bool = False,
) -> str:
    dna = dna if dna is not None else WritingDNARepository().load_raw()
    brain_repository = AuthorBrainRepository()
    brain_profile = brain_profile if brain_profile is not None else brain_repository.load_profile()
    brain_status = brain_status if brain_status is not None else brain_repository.load_status()
    learning_center = learning_center or LearningCenter()
    memory_inbox = memory_inbox or MemoryInbox()
    knowledge_graph = knowledge_graph or KnowledgeGraph()
    notices = []
    if base_saved:
        notices.append("Авторская база сохранена. AI будет учитывать эти темы и идеи.")
    if saved:
        notices.append("Профиль автора сохранен.")
    if dna_saved:
        notices.append("ДНК письма сохранена.")
    if learning_saved:
        notices.append("Правила обновлены.")
    if rule_saved:
        notices.append("Правило добавлено.")
    if learning_settings_saved:
        notices.append("Настройки обучения сохранены.")
    if strategy_saved:
        notices.append("Редакционная стратегия сохранена.")
    if refreshed:
        notices.append("Обновление профиля автора запущено. Пока оно идет, используется последняя сохраненная версия.")
    notice_html = "".join(f"<div class=\"notice\">{escape(item)}</div>" for item in notices)
    active_tab = _normalize_author_tab(tab)
    tabs = (
        ("base", "Авторская база"),
        ("dna", "ДНК письма"),
        ("strategy", "Редакционная стратегия"),
    )
    tab_links = "".join(
        f"<a class=\"{'active' if key == active_tab else ''}\" href=\"/author-profile?tab={key}\">{label}</a>"
        for key, label in tabs
    )
    panels = {
        "base": _author_base_panel(brain_profile, brain_status),
        "dna": _writing_dna_panel(dna, profile),
        "strategy": _editorial_strategy_panel(_load_editorial_strategy()),
    }
    content = f"""
    {notice_html}
    <nav class="view-switch">
      {tab_links}
    </nav>
    {panels[active_tab]}
"""
    return _page_shell(
        title="Профиль автора",
        eyebrow="профиль, стиль и обучение",
        heading="Профиль автора",
        hint="Кто вы как автор: темы, стиль и стратегия, на которые опирается AI.",
        active="profile",
        content=content,
    )


def _normalize_author_tab(tab: str) -> str:
    aliases = {
        "author-base": "base",
        "writing-dna": "dna",
        "editorial-strategy": "strategy",
    }
    value = aliases.get((tab or "").strip(), (tab or "").strip())
    return value if value in {"base", "dna", "strategy"} else "base"


def _author_base_panel(profile: dict[str, object], status: object) -> str:
    state = _status_label(str(getattr(status, "state", "")))
    message = _status_message(str(getattr(status, "message", "")))
    updated = _format_moscow_time(str(getattr(status, "updated_at", "")) or str(profile.get("updated_at", "")))
    error = str(getattr(status, "error", ""))
    source_counts = profile.get("source_counts", {})
    if not isinstance(source_counts, dict):
        source_counts = {}
    return f"""
    <section class="block" id="author-base">
      <div class="section-title">
        <div>
          <p class="eyebrow">профиль автора</p>
          <h2>Авторская база</h2>
        </div>
        <span>{escape(state)}</span>
      </div>
      <section class="ai-panel ai-{escape(str(getattr(status, "state", "")))}">
        <div>
          <strong>{escape(message)}</strong>
          <p>Обновлено: {escape(updated or "пока нет данных")}</p>
          {_small_error(escape(error))}
        </div>
        <form method="post" action="/author-brain/refresh">
          <button type="submit">Обновить профиль автора</button>
        </form>
      </section>
      <section class="stat-row">
        <div class="stat-card"><span>Документы</span><strong>{escape(str(source_counts.get("documents", 0)))}</strong></div>
        <div class="stat-card"><span>Кейсы</span><strong>{escape(str(source_counts.get("cases", 0)))}</strong></div>
        <div class="stat-card"><span>Идеи</span><strong>{escape(str(source_counts.get("ideas", 0)))}</strong></div>
      </section>
      {_author_base_editor(profile)}
      <section class="grid two">
        {_simple_list_section("Стиль мышления", profile.get("thinking_style", []))}
        {_simple_list_section("Сильные стороны", profile.get("strengths", []))}
      </section>
    </section>
    """


def _author_base_editor(profile: dict[str, object]) -> str:
    themes = profile.get("main_themes", [])
    theme_rows = "".join(
        _editable_theme_row(item, index)
        for index, item in enumerate(themes if isinstance(themes, list) else [])
        if isinstance(item, dict)
    ) or '<div class="empty">Пока нет главных тем</div>'
    return f"""
      <form class="profile-form" method="post" action="/author-profile/base">
        <section class="profile-section">
          <div class="section-title"><div><p class="eyebrow">редактируется вручную</p><h2>Главные темы</h2></div></div>
          <div class="help-note">Вес темы: 90-100 — основной фокус AI, 70-89 — использовать регулярно, 40-69 — вспомогательный угол, ниже 40 — только если явно подходит к задаче.</div>
          <div class="card-list">{theme_rows}</div>
          <article class="plan-item edit-row">
            <h3>Добавить тему</h3>
            {_input("new_theme_name", "Название", "")}
            {_input("new_theme_score", "Вес", "80")}
            {_textarea("new_theme_evidence", "Маркеры через строки", "")}
            {_input("new_theme_risk", "Ограничение/риск", "")}
          </article>
        </section>
        <section class="profile-section">
          <p class="eyebrow">сохранение</p>
          <div class="form-actions">
            <button name="author_base_action" value="save" type="submit">Сохранить авторскую базу</button>
            <button class="ghost" name="author_base_action" value="add" type="submit">Добавить новые строки</button>
          </div>
        </section>
      </form>
    """


def _editable_theme_row(item: dict[str, object], index: int) -> str:
    evidence = item.get("evidence", [])
    chips = _chips(evidence if isinstance(evidence, list) else [])
    risk = str(item.get("risk", "")).strip()
    risk_html = f"<p class=\"risk\">{escape(_display_ru(risk))}</p>" if risk else ""
    return f"""
      <article class="card">
        <div class="card-head"><h3>{escape(_display_ru(str(item.get("name", ""))))}</h3><strong>{escape(str(item.get("score", "")))}</strong></div>
        <div class="tags">{chips}</div>
        {risk_html}
        <details class="inline-editor">
          <summary>Редактировать</summary>
          <div class="edit-row">
            {_input(f"theme_{index}_name", "Тема", _display_ru(str(item.get("name", ""))))}
            {_input(f"theme_{index}_score", "Вес", str(item.get("score", "")))}
            {_textarea(f"theme_{index}_evidence", "Маркеры", list_to_text(item.get("evidence", [])))}
            {_input(f"theme_{index}_risk", "Ограничение/риск", _display_ru(str(item.get("risk", ""))))}
            <button class="ghost" name="author_base_action" value="delete_theme_{index}" type="submit">Удалить тему</button>
          </div>
        </details>
      </article>
    """


def _writing_dna_panel(dna: dict[str, object], profile: dict[str, object]) -> str:
    tone = profile.get("tone", {})
    structure = profile.get("structure", {})
    vocabulary = profile.get("vocabulary", {})
    platform_rules = profile.get("platform_rules", {})
    what_not_to_write = profile.get("what_not_to_write", [])
    examples_and_stories = profile.get("examples_and_stories", [])
    if not isinstance(tone, dict):
        tone = {}
    if not isinstance(structure, dict):
        structure = {}
    if not isinstance(vocabulary, dict):
        vocabulary = {}
    if not isinstance(platform_rules, dict):
        platform_rules = {}
    return f"""
    <section class="block" id="writing-dna">
      <div class="section-title"><div><p class="eyebrow">стиль автора</p><h2>ДНК письма</h2></div></div>
      <form class="profile-form" method="post" action="/author-profile">
        <section class="profile-section">
          <p class="eyebrow">правила стиля</p>
          <div class="form-grid">
            {_input("formality", "Формальность", tone.get("formality", ""))}
            {_input("directness", "Прямота", tone.get("directness", ""))}
            {_input("provocation", "Провокационность", tone.get("provocation", ""))}
            {_input("emotionality", "Эмоциональность", tone.get("emotionality", ""))}
          </div>
          {_textarea("post_structure", "Структура текста", structure.get("post_structure", ""))}
          {_textarea("intro_length", "Вступление", structure.get("intro_length", ""))}
          {_textarea("narrative_logic", "Логика рассуждения", structure.get("narrative_logic", ""))}
          {_textarea("conclusion", "Вывод", structure.get("conclusion", ""))}
          {_textarea("favorite_words", "Допустимые приемы и любимая лексика", list_to_text(vocabulary.get("favorite_words", [])))}
          {_textarea("unwanted_words", "Нежелательные слова", list_to_text(vocabulary.get("unwanted_words", [])))}
          {_textarea("banned_cliches", "Запреты и клише", list_to_text(vocabulary.get("banned_cliches", [])))}
          {_textarea("professional_terms", "Профессиональная терминология", list_to_text(vocabulary.get("professional_terms", [])))}
          {_textarea("what_not_to_write", "Чего не писать", list_to_text(what_not_to_write))}
          {_textarea("examples_and_stories", "Примеры хорошего текста", _stories_to_text(examples_and_stories))}
        </section>
        <section class="profile-section">
          <p class="eyebrow">правила генерации</p>
          {_textarea("main_goal", "Главная цель письма", dna.get("main_goal", ""))}
          {_textarea("origin_of_posts", "Как рождаются публикации", dna.get("origin_of_posts", ""))}
          {_textarea("story_rule", "Правило историй", dna.get("story_rule", ""))}
          {_textarea("memory_usage", "Использование памяти", dna.get("memory_usage", ""))}
          {_textarea("tone", "Тональность", dna.get("tone", ""))}
          {_textarea("paragraphs", "Структура абзацев", dna.get("paragraphs", ""))}
          {_textarea("allowed_phrases", "Допустимые приемы", list_to_text(dna.get("allowed_phrases", [])))}
          {_textarea("argumentation_patterns", "Паттерны аргументации", list_to_text(dna.get("argumentation_patterns", [])))}
          <p class="pointer-note">Запрещённые начала текста теперь редактируются на вкладке <a href="/bot-rules">«Правила бота»</a> — единый источник для AI.</p>
          {_textarea("draft_rule", "Правило первого черновика", dna.get("draft_rule", ""))}
          {_textarea("self_check", "Самопроверка", list_to_text(dna.get("self_check", [])))}
          {_textarea("anti_template_rule", "Не превращать в шаблон", dna.get("anti_template_rule", ""))}
        </section>
        <section class="profile-section">
          <p class="eyebrow">сохранение</p>
          <div class="form-actions"><button type="submit">Сохранить профиль автора</button></div>
        </section>
      </form>
    </section>
    """


def _status_label(state: str) -> str:
    return {
        "idle": "Ожидание",
        "running": "Обновляется",
        "completed": "Готово",
        "ready": "Готово",
        "error": "Ошибка",
    }.get(state, state or "Ожидание")


def _status_message(message: str) -> str:
    return {
        "Author Brain has not been refreshed yet.": "Авторская база еще не обновлялась",
        "Author Brain profile updated.": "Профиль автора обновлен",
        "Author Brain is updating from Knowledge, Writing DNA, and Lessons.": "Профиль автора обновляется на основе памяти, ДНК письма и обучения",
        "Author Brain refresh is already running.": "Обновление профиля автора уже выполняется",
        "Author Brain refresh failed. Last saved profile is still available.": "Обновление авторской базы не удалось. Используется последняя сохраненная версия.",
    }.get(message, message or "Авторская база еще не обновлялась")


def _small_error(value: str) -> str:
    return f"<p class=\"risk\">{value}</p>" if value else ""


def _simple_list_section(title: str, items: object) -> str:
    entries = [str(item) for item in items if str(item).strip()] if isinstance(items, list) else []
    body = "".join(f"<li>{escape(item)}</li>" for item in entries) if entries else "<li>Пока недостаточно данных</li>"
    return f"""<section class="profile-section">
      <p class="eyebrow">Авторская база</p>
      <h2>{escape(title)}</h2>
      <ul class="ai-list">{body}</ul>
    </section>"""


def _profile_items(items: object, renderer: object) -> str:
    if not isinstance(items, list) or not items:
        return "<div class=\"empty\">Пока недостаточно данных</div>"
    return "".join(renderer(item) for item in items if isinstance(item, dict))


def _chips(items: object) -> str:
    values = items if isinstance(items, list) else []
    return "".join(f"<span>{escape(_display_ru(str(item)))}</span>" for item in values if str(item).strip())


def _anti_rule_label(value: str) -> str:
    return {
        "Do not propose a topic if it is strongly similar to recent ideas.": "Не предлагать тему, если она слишком похожа на недавние идеи.",
        "Do not reuse the same case in consecutive drafts unless the user explicitly asks.": "Не использовать один и тот же кейс в соседних черновиках без явного запроса.",
        "If an idea matches an old idea or case, show the similarity warning before drafting.": "Если новая идея похожа на старую идею или кейс, показать предупреждение перед черновиком.",
    }.get(value, value)


def _display_ru(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    replacements = {
        "operations": "операции и процессы",
        "operational": "операционный",
        "customer experience": "клиентский опыт (CX)",
        "service systems": "сервисные системы",
        "hospitality": "гостеприимство",
        "premium service": "премиальный сервис",
        "process improvement": "улучшение процессов",
        "BI / analytics": "BI / аналитика",
        "analytics": "аналитика",
        "personal observation": "личное наблюдение",
        "case": "кейс",
        "provocation": "провокационный угол",
        "framework": "фреймворк",
        "practical teardown": "практический разбор",
        "strong": "сильное соответствие",
        "medium": "среднее соответствие",
        "low": "низкое соответствие",
        "document": "документ",
        "ready": "готово",
        "timed out": "превышено время ожидания",
        "timeout": "превышено время ожидания",
        "AI request failed.": "AI-запрос не выполнен.",
        "Author Brain refresh failed. Last saved profile is still available.": "Обновление авторской базы не удалось. Используется последняя сохраненная версия.",
        "rotate with adjacent angles": "чередовать со смежными углами",
        "use only facts present in Knowledge": "использовать только факты из памяти",
        "English, executive/consulting tone, clear business effect.": "английский язык; управленческий, консультационный тон; понятный бизнес-эффект.",
        "Russian, expert but alive, practical teardown or case logic.": "русский язык; экспертно, но живо; практический разбор или логика кейса.",
        "Short, conversational, one thought with a working observation.": "коротко и разговорно: одна мысль через рабочее наблюдение.",
        "Observation-first, short thoughts, low ceremony.": "сначала наблюдение; короткая мысль; без лишней официальности.",
        "Customer experience is an operational outcome, not only a communication layer.": "Клиентский опыт — это операционный результат, а не только слой коммуникации.",
        "SOP and standards protect service from randomness when connected to responsibility.": "SOP и стандарты защищают сервис от случайности, если связаны с ответственностью.",
        "AI amplifies process maturity and exposes gaps in data, ownership, and handoffs.": "AI усиливает зрелость процессов и показывает разрывы в данных, ответственности и передачах между ролями.",
        "Premium service needs both human attention and repeatable systems.": "Премиальному сервису нужны и человеческое внимание, и повторяемые системы.",
        "Service design becomes useful when it reaches implementation, roles, control points, and metrics.": "Service Design становится полезным, когда доходит до внедрения, ролей, точек контроля и метрик.",
        "Business symptoms usually point to deeper management-system causes.": "Бизнес-симптомы обычно указывают на более глубокие причины в управленческой системе.",
        "Operational diagnosis of service problems.": "Операционная диагностика сервисных проблем.",
        "Translation of customer experience into process, roles, SOP, and control points.": "Перевод клиентского опыта в процессы, роли, SOP и точки контроля.",
        "Ability to turn cases and observations into executive content.": "Умение превращать кейсы и наблюдения в управленческий контент.",
        "Real case base for hospitality and service-system content.": "Реальная база кейсов для тем про гостеприимство и сервисные системы.",
        "Connects service and operations with BI / analytics logic.": "Связывает сервис и операции с логикой BI / аналитики.",
        "Starts from a working observation before moving to a management cause.": "Начинает с рабочего наблюдения, а потом переходит к управленческой причине.",
        "Connects service quality with operations, ownership, standards, and data.": "Связывает качество сервиса с операциями, ответственностью, стандартами и данными.",
        "Prefers practical conclusions over abstract definitions.": "Предпочитает практические выводы абстрактным определениям.",
        "РЎРµС‚РєР°": "Сетка",
    }
    if text in replacements:
        return replacements[text]
    result = text
    phrase_replacements = {
        "Customer Experience and Operations": "клиентский опыт и операции",
        "Customer Experience": "клиентский опыт (CX)",
        "Operations": "операции",
        "Service Design": "Service Design",
        "Hospitality": "гостеприимство",
        "responsibility handoffs": "передачи ответственности",
        "handoffs": "передачи ответственности",
        "ownership": "ответственность",
        "standards": "стандарты",
        "control points": "точки контроля",
        "service": "сервис",
        "randomness": "случайность",
    }
    for source, target in phrase_replacements.items():
        result = re.sub(re.escape(source), target, result, flags=re.IGNORECASE)
    return result


def render_learning_center(
    learning_center: LearningCenter,
    memory_inbox: MemoryInbox,
    knowledge_graph: KnowledgeGraph,
    saved: bool = False,
) -> str:
    candidates = learning_center.list_lessons("candidate")
    accepted = learning_center.list_lessons("accepted")
    rejected = learning_center.list_lessons("rejected")
    pending_memory = memory_inbox.list_items("pending")
    graph = knowledge_graph.read_graph()
    saved_notice = "<div class=\"notice\">Центр обучения обновлен.</div>" if saved else ""
    candidate_cards = "".join(_lesson_card(lesson) for lesson in candidates) or '<div class="empty">Новых предложенных правил пока нет.</div>'
    memory_cards = "".join(_memory_inbox_card(item) for item in pending_memory) or '<div class="empty">Входящие памяти пусты.</div>'
    accepted_cards = "".join(_lesson_summary_card(lesson) for lesson in accepted) or '<div class="empty">Подтвержденных правил пока нет.</div>'
    patterns = learning_center.frequent_edit_patterns()
    pattern_cards = "".join(f'<article class="card"><p>{escape(pattern)}</p></article>' for pattern in patterns) or '<div class="empty">Паттерны появятся после нескольких комментариев и решений.</div>'
    content = f"""
    {saved_notice}
    <section class="block">
      <div class="section-title"><div><p class="eyebrow">требует решения</p><h2>Предложенные правила</h2></div><span>{len(candidates)} ожидают решения</span></div>
      <div class="card-list">{candidate_cards}</div>
    </section>
    <section class="block">
      <div class="section-title"><div><p class="eyebrow">память</p><h2>Входящие памяти</h2></div><span>{len(pending_memory)} на подтверждение</span></div>
      <div class="card-list">{memory_cards}</div>
    </section>
    <section class="block">
      <div class="section-title"><div><p class="eyebrow">что уже изучено</p><h2>Подтвержденные правила</h2></div><span>{len(accepted)} активных правил</span></div>
      <details class="knowledge-upload embedded-form">
        <summary>Добавить правило</summary>
        <p class="brief-hint">Единственное место, где правила добавляются вручную. Обычно правила рождаются из ваших комментариев к черновикам, но можно записать и напрямую.</p>
        <form method="post" action="/author-profile/rules/add">
          <textarea name="rule" rows="4" placeholder="Например: начинать пост с рабочей ситуации, а не с общего тезиса" required></textarea>
          <textarea name="reason" rows="3" placeholder="Почему это важно для моего стиля"></textarea>
          <button type="submit">Сохранить правило</button>
        </form>
      </details>
      <div class="card-list">{accepted_cards}</div>
    </section>
    <section class="block">
      <div class="section-title"><div><p class="eyebrow">системная память</p><h2>Граф знаний</h2></div><span>{len(graph.get('nodes', []))} узлов / {len(graph.get('edges', []))} связей</span></div>
      <div class="card"><p>Граф сейчас локальный и файловый. Он связывает документы, кейсы, темы, компании, идеи и подтвержденные элементы памяти. Позже его можно заменить графовой БД без изменения поведения агента.</p></div>
    </section>
    <section class="block">
      <div class="section-title"><div><p class="eyebrow">паттерны</p><h2>Частые правки и привычки автора</h2></div><span>{len(rejected)} отклонено</span></div>
      <div class="card-list">{pattern_cards}</div>
    </section>
"""
    return _page_shell(
        title="Центр обучения",
        eyebrow="обучение памяти",
        heading="Центр обучения",
        hint="Здесь вы подтверждаете, что AI запомнит и чему научится.",
        active="learning",
        content=content,
    )


def _ai_synthesize_trends(
    signals: list[dict[str, object]],
    author_brain: dict[str, object],
    content_plan: dict[str, object],
) -> list[dict[str, object]]:
    """Cluster fresh real-world media signals into global editorial trends via AI.

    Returns a list of trend dicts, or an empty list on any failure so the radar
    falls back to the deterministic rule-based path."""
    gateway = AIGateway()
    if not gateway.is_configured() or not signals:
        return []
    profile = author_brain.get("profile", author_brain) if isinstance(author_brain, dict) else {}
    profile = profile if isinstance(profile, dict) else {}

    def _names(value: object, limit: int) -> list[str]:
        items: list[str] = []
        if isinstance(value, list):
            for entry in value[:limit]:
                if isinstance(entry, dict):
                    items.append(str(entry.get("name") or entry.get("idea") or entry.get("title") or ""))
                else:
                    items.append(str(entry))
        return [item for item in items if item.strip()]

    themes = _names(profile.get("main_themes"), 12)
    key_ideas = _names(profile.get("key_ideas"), 12)
    author_cases = [
        str(case.get("title") or case.get("project") or case.get("company") or "")
        for case in (profile.get("cases", []) if isinstance(profile.get("cases"), list) else [])
        if isinstance(case, dict)
    ]
    author_cases = [c for c in author_cases if c.strip()][:8]
    principles = _names(profile.get("author_principles"), 8)
    observations = _names(profile.get("author_observations"), 8)
    pillars = content_plan.get("content_pillars", [])
    expertise = ", ".join(themes[:6]) or "Operations, Customer Experience, Hospitality, AI в бизнесе, управленческие системы"
    author_context = {
        "main_themes": themes,
        "key_ideas": key_ideas,
        "author_cases": author_cases,
        "author_principles": principles,
        "author_observations": observations,
        "week_focus": str(content_plan.get("focus", "")),
        "content_pillars": [str(p) for p in pillars] if isinstance(pillars, list) else [],
        "expertise": expertise,
    }
    trimmed_signals = [
        {
            "title": str(s.get("title", ""))[:200],
            "summary": str(s.get("summary", ""))[:300],
            "source": str(s.get("source", "")),
            "url": str(s.get("url", "")),
        }
        for s in signals[:40]
    ]
    try:
        response = _complete_json_with_retry(
            gateway,
            system_prompt=(
                "Ты редакционный стратег КОНКРЕТНОГО автора, а не универсальный AI-аналитик новостей. "
                "Свежие сигналы СМИ — это только сырьё. Главное — темы, ключевые идеи, кейсы, принципы и "
                "наблюдения автора из его КОНТЕКСТА. Отбирай и формулируй тренды через призму автора: "
                "бери только то, что реально пересекается с его экспертной территорией и взглядами, и "
                "отбрасывай сигналы, к которым автору нечего добавить от себя. "
                "Не пересказывай новости и не выдавай общие отраслевые обзоры — синтезируй тренды, которые "
                "автор мог бы раскрыть своим голосом, опираясь на свои идеи и кейсы. Отвечай строго JSON."
            ),
            user_prompt=(
                "КОНТЕКСТ АВТОРА — ГЛАВНЫЙ ОРИЕНТИР (JSON):\n"
                f"{json.dumps(author_context, ensure_ascii=False)}\n\n"
                "СВЕЖИЕ СИГНАЛЫ ИЗ СМИ — ТОЛЬКО СЫРЬЁ ДЛЯ ПРИВЯЗКИ (JSON):\n"
                f"{json.dumps(trimmed_signals, ensure_ascii=False)}\n\n"
                "ПРАВИЛА ОТБОРА:\n"
                "1. Каждый тренд ОБЯЗАН пересекаться хотя бы с одной main_theme, key_idea или экспертизой автора.\n"
                "2. Если сигнал не связан с территорией автора — не включай его, даже если он громкий.\n"
                "3. author_angle и expertise_connection формулируй через конкретные идеи/кейсы автора, а не общими словами.\n"
                "4. Лучше 5-8 по-настоящему релевантных автору трендов, чем 10 общих. Сортируй по релевантности автору.\n\n"
                "Верни строго JSON вида: {\"trends\": [ {\n"
                "  \"title\": \"редакционная формулировка глобального тренда (не заголовок новости)\",\n"
                "  \"category\": \"AI|Operations|Customer Experience|Hospitality|Management\",\n"
                "  \"trend_essence\": \"о чём на самом деле этот тренд, 1-2 предложения\",\n"
                "  \"main_idea\": \"главная мысль\",\n"
                "  \"audience_importance\": \"почему это важно аудитории автора\",\n"
                "  \"author_angle\": \"уникальный авторский угол подачи\",\n"
                "  \"expertise_connection\": \"связь с экспертизой автора\",\n"
                "  \"why_now\": \"почему тренд актуален сейчас\",\n"
                "  \"why_trend\": \"почему это именно тренд, а не разовая новость\",\n"
                "  \"why_important\": \"чем ценен для бренда автора\",\n"
                "  \"hype_level\": \"высокий|средний|низкий\",\n"
                "  \"relevance_forecast\": \"насколько долго тема будет актуальна\",\n"
                "  \"trend_relevance\": число 0-10,\n"
                "  \"supporting_sources\": [{\"name\": \"источник\", \"url\": \"ссылка\"}],\n"
                "  \"publication_ideas\": {\"LinkedIn\": \"...\", \"Telegram\": \"...\", \"VC\": \"...\", \"Сетка\": \"...\"}\n"
                "} ] }. Каждый тренд должен опираться на реальные сигналы из списка."
            ),
            action="trend_radar_synthesize",
        )
    except AIGatewayError:
        return []
    trends = response.get("trends") if isinstance(response, dict) else None
    if not isinstance(trends, list):
        return []
    return [item for item in trends if isinstance(item, dict)]


_TREND_REFRESH_LOCK = threading.Lock()


def _trend_refresh_in_progress() -> bool:
    return _TREND_REFRESH_LOCK.locked()


def _start_trend_radar_refresh_background() -> bool:
    """Kick off a trend-radar refresh in a daemon thread so the page never blocks.

    Returns True if a new refresh was started, False if one is already running."""
    if _TREND_REFRESH_LOCK.locked():
        return False

    def run() -> None:
        if not _TREND_REFRESH_LOCK.acquire(blocking=False):
            return
        try:
            _refresh_trend_radar_now()
        except Exception:  # noqa: BLE001 - background refresh must never crash the server
            pass
        finally:
            _TREND_REFRESH_LOCK.release()

    threading.Thread(target=run, name="trend-radar-refresh", daemon=True).start()
    return True


def _refresh_trend_radar_now() -> dict[str, object]:
    DailyBriefRequestHandler.knowledge_base.ensure_seed_documents()
    author_brain = AuthorBrain(
        author_profile=DailyBriefRequestHandler.author_profile_repository.load_raw(),
        writing_dna=DailyBriefRequestHandler.writing_dna_repository.load_raw(),
        documents=DailyBriefRequestHandler.knowledge_base.list_documents()[:8],
        cases=DailyBriefRequestHandler.knowledge_base.list_cases()[:8],
        ideas=DailyBriefRequestHandler.idea_vault.list_ideas()[:12],
        lessons=DailyBriefRequestHandler.learning_center.list_lessons("accepted"),
    ).build()
    plan = _load_content_plan_raw()
    pillars = plan.get("content_pillars", [])
    pillar_query = " ".join(str(item) for item in pillars) if isinstance(pillars, list) else str(pillars)
    ai_context = DailyBriefRequestHandler.ai_context_engine.build({"topic": pillar_query}, include_local_sources=True)
    return DailyBriefRequestHandler.trend_radar.refresh(
        content_plan=plan,
        documents=DailyBriefRequestHandler.knowledge_base.list_documents(),
        cases=DailyBriefRequestHandler.knowledge_base.list_cases(),
        ideas=DailyBriefRequestHandler.idea_vault.list_ideas(),
        author_brain=author_brain,
        graph_links=DailyBriefRequestHandler.knowledge_graph.related_to(pillar_query),
        ai_context=ai_context,
        synthesizer=_ai_synthesize_trends,
    )


def render_trend_radar(
    cache: dict[str, object],
    saved: bool = False,
    stale: bool = False,
    refreshing: bool = False,
) -> str:
    topics = cache.get("topics", [])
    if not isinstance(topics, list):
        topics = []
    generated_at = str(cache.get("generated_at", ""))
    expires_at = str(cache.get("expires_at", ""))
    sources = cache.get("sources", [])
    source_text = ", ".join(str(item) for item in sources) if isinstance(sources, list) else ""
    source_status = str(cache.get("source_status", ""))
    diagnostics = _source_diagnostics_table(cache.get("source_diagnostics", []))
    saved_notice = '<div class="notice">Радар трендов обновлен.</div>' if saved else ""
    refreshing_notice = (
        '<div class="notice trend-refreshing">'
        '<span class="spinner" aria-hidden="true"></span>'
        'AI анализирует свежие сигналы мировых СМИ в фоне (~40 сек). '
        'Пока показаны данные из последнего кэша — страница обновится сама.'
        '</div>'
        if refreshing
        else ""
    )
    # Auto-reload while a background refresh is running so fresh trends appear without a manual click.
    head_extra = '<meta http-equiv="refresh" content="15">\n  ' if refreshing else ""
    status = "Обновляется в фоне" if refreshing else ("Нужно обновить" if stale else "Готов к редакционному выбору")
    empty = '<div class="empty">Радар трендов еще не запускался. Нажмите «Обновить радар».</div>'
    main_card = _main_trend_recommendation(topics[0]) if topics else empty
    cards = "".join(_trend_card(topic) for topic in topics) or empty
    analysis_mode = str(cache.get("analysis_mode", "local"))
    signal_count = int(cache.get("external_signal_count", 0) or 0)
    if analysis_mode == "ai":
        mode_text = (
            f"AI-анализ мировых СМИ: обработано {signal_count} свежих сигналов и выделены глобальные тренды."
            if signal_count > 0
            else "AI-анализ: глобальные тренды выделены (внешние СМИ сейчас недоступны)."
        )
        mode_badge = f'<div class="trend-mode trend-mode-ai">{escape(mode_text)}</div>'
    else:
        mode_badge = '<div class="trend-mode trend-mode-local">Локальный анализ: внешние СМИ или AI недоступны, показаны редакционные заготовки.</div>'
    refresh_button = (
        '<button type="submit" disabled>Обновляется…</button>'
        if refreshing
        else '<button type="submit">Обновить радар</button>'
    )
    content = f"""
    {saved_notice}
    {refreshing_notice}
    {mode_badge}
    <section class="block">
      <div class="section-title">
        <div>
          <p class="eyebrow">сегодня AI рекомендует</p>
          <h2>Лучшая тема для редакционного решения</h2>
        </div>
        <form method="post" action="/trend-radar/refresh">
          {refresh_button}
        </form>
      </div>
      {main_card}
    </section>
    <section class="block">
      <div class="section-title">
        <div>
          <p class="eyebrow">темы с потенциалом</p>
          <h2>Редакционные рекомендации</h2>
        </div>
        <span>{len(topics)} тем</span>
      </div>
      <div class="card-list trend-radar-list">{cards}</div>
    </section>
    <details class="block strategy-rules">
      <summary>Техническая информация</summary>
      <div class="draft-context-grid">
        <div><p class="label">Статус</p><p>{escape(status)}</p></div>
        <div><p class="label">Последнее обновление</p><p>{escape(_format_moscow_time(generated_at) or "еще не запускался")}</p></div>
        <div><p class="label">Кэш до</p><p>{escape(expires_at or "не задан")}</p></div>
        <div><p class="label">Источники</p><p>{escape(source_text or "локальные источники продукта")}</p></div>
        <div><p class="label">Доступ внешних источников</p><p>{escape(source_status or "Внешние источники недоступны, используется локальный анализ.")}</p></div>
      </div>
      {diagnostics}
    </details>
"""
    return _page_shell(
        title="Радар трендов",
        eyebrow="сигналы и тренды",
        heading="Радар трендов",
        hint="Свежие темы и сигналы, отобранные под ваши площадки.",
        active="trends",
        content=content,
        head_extra=head_extra,
    )


def _source_diagnostics_table(value: object) -> str:
    if not isinstance(value, list) or not value:
        return '<p class="state-note">Диагностика источников пока не сохранена.</p>'
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item.get("name", "")))}</td>
              <td>{escape(str(item.get("status", "")))}</td>
              <td>{escape(str(item.get("http_status", "")))}</td>
              <td>{escape(str(item.get("fetched_count", 0)))}</td>
              <td>{escape(str(item.get("trend_count", 0)))}</td>
              <td>{escape(str(item.get("error", "")))}</td>
            </tr>
            """
        )
    return f"""
      <div class="table-wrap">
        <table>
          <thead><tr><th>Источник</th><th>Статус</th><th>HTTP</th><th>Получено</th><th>В трендах</th><th>Ошибка</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    """


def _main_trend_recommendation(topic: object) -> str:
    item = topic if isinstance(topic, dict) else {}
    topic_id = str(item.get("id", ""))
    reasons = [
        f"оценка тренда {_score_value(item, 'trend_score')}/10",
        f"соответствие бренду {_score_value(item, 'brand_fit_score')}/10",
        f"контентный потенциал {_score_value(item, 'content_potential')}/10",
        f"риск повтора: {_repeat_risk_label(str(item.get('repeat_risk', '')))}",
        "основана на текущем рыночном сигнале",
    ]
    reason_items = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons if str(reason).strip())
    return f"""
      <article class="card trend-card main-trend-card">
        <p class="label">Главная рекомендация</p>
        <h3>{escape(str(item.get("title", "")))}</h3>
        <p class="score-stars">{_score_stars(item.get("trend_score", 0))}</p>
        <p>{escape(str(item.get("trend_essence") or item.get("description", "")))}</p>
        <ul>{reason_items}</ul>
        <div class="topic-actions">
          {_trend_action_form(topic_id, "drafted", "Создать пост")}
          {_trend_action_form(topic_id, "planned", "Добавить в контент-план", "secondary")}
          {_trend_action_form(topic_id, "saved", "Добавить в идеи", "secondary")}
          {_trend_action_form(topic_id, "rejected", "Отклонить", "ghost")}
        </div>
      </article>
    """


def _trend_card(topic: object) -> str:
    item = topic if isinstance(topic, dict) else {}
    topic_id = str(item.get("id", ""))
    cases = _inline_list(item.get("matching_cases", []), "Подходящих кейсов пока нет")
    case_insights = _case_insights_html(item.get("case_insights", []))
    materials = _inline_list(item.get("knowledge_materials", []), "Документы из памяти пока не найдены")
    sources = _inline_list(item.get("sources", []), str(item.get("source", "")))
    publication_ideas = _publication_ideas_html(item.get("publication_ideas", {}))
    source_url = str(item.get("source_url", "")).strip()
    source_link = (
        f'<a class="open-link" href="{escape(source_url)}" target="_blank" rel="noreferrer">Открыть оригинальную статью</a>'
        if source_url
        else '<span class="state-note">Оригинальная ссылка не найдена</span>'
    )
    status = str(item.get("status", "new"))
    explanation = item.get("ai_explanation", {})
    explanation = explanation if isinstance(explanation, dict) else {}
    return f"""
    <article class="card trend-card" id="{escape(topic_id)}">
      <div class="card-head">
        <h3>{escape(str(item.get("title", "")))}</h3>
        <strong>{escape(_trend_status_ru(status))}</strong>
      </div>
      <p class="score-stars">{_score_stars(item.get("trend_score", 0))}</p>
      <div class="score-grid">
        <div><p class="label">Оценка тренда</p><b>{escape(str(item.get("trend_score", "")))}/10</b></div>
        <div><p class="label">Соответствие бренду</p><b>{escape(str(item.get("brand_fit_score", "")))}/10</b></div>
        <div><p class="label">Контентный потенциал</p><b>{escape(str(item.get("content_potential", item.get("reach_score", ""))))}/10</b></div>
        <div><p class="label">Категория</p><b>{escape(_category_ru(str(item.get("category", ""))))}</b></div>
      </div>
      <div class="draft-materials">
        <p class="label">Краткая суть</p>
        <p>{escape(str(item.get("trend_essence") or item.get("description", "")))}</p>
        <p class="label">О чем на самом деле этот тренд?</p>
        <p><b>Суть тренда:</b> {escape(str(item.get("trend_essence", "")))}</p>
        <p><b>Главная идея:</b> {escape(str(item.get("main_idea", "")))}</p>
        <p><b>Почему это важно для моей аудитории:</b> {escape(str(item.get("audience_importance", "")))}</p>
        <p class="label">Почему это важно именно сейчас</p>
        <p>{escape(str(item.get("why_trend", item.get("why_now", ""))))}</p>
        <p class="label">Какой авторский угол предлагает AI</p>
        <p>{escape(str(item.get("author_angle", "")))}</p>
        <p class="label">Как это связано с моей экспертизой</p>
        <p>{escape(str(item.get("expertise_connection", "")))}</p>
      </div>
      <div class="draft-context-grid">
        <div><p class="label">Кейсы использовать</p><p>{cases}</p></div>
        <div><p class="label">Документы использованы</p><p>{materials}</p></div>
        <div><p class="label">Риск повтора</p><p>{escape(_repeat_risk_label(str(item.get("repeat_risk", ""))))}</p></div>
        <div><p class="label">Рекомендация</p><p>{escape(_recommendation_label(str(item.get("recommendation", ""))))}</p></div>
      </div>
      <div class="draft-materials">
        <p class="label">Как использовать кейсы</p>
        {case_insights}
      </div>
      <div class="draft-materials">
        <p class="label">Какие публикации можно сделать</p>
        {publication_ideas}
      </div>
      <details class="draft-materials">
        <summary>Почему AI предложил это?</summary>
        <p><b>Тренд:</b> {escape(str(explanation.get("trend", item.get("why_now", ""))))}</p>
        <p><b>Оценка тренда:</b> {escape(str(explanation.get("trend_score", item.get("trend_score", ""))))}/10</p>
        <p><b>Соответствие бренду:</b> {escape(str(item.get("brand_fit_score", "")))}/10</p>
        <p><b>Контентный потенциал:</b> {escape(str(explanation.get("content_potential", item.get("content_potential", ""))))}/10</p>
        <p><b>Фокус недели:</b> {escape(str(explanation.get("week_focus", "")))}</p>
        <p><b>Почему подходит автору:</b> {escape(str(item.get("expertise_connection", "")))}</p>
        <p><b>Авторский угол:</b> {escape(str(explanation.get("author_angle", item.get("author_angle", ""))))}</p>
        <p><b>Риск повтора:</b> {escape(_repeat_risk_label(str(explanation.get("repeat_risk", item.get("repeat_risk", "")))))}</p>
      </details>
      <div class="draft-materials">
        <p class="label">Источник новости</p>
        <p>{sources}</p>
        {source_link}
      </div>
      <div class="topic-actions">
        {_trend_action_form(topic_id, "drafted", "Создать пост")}
        {_trend_action_form(topic_id, "planned", "Добавить в контент-план", "secondary")}
        {_trend_action_form(topic_id, "saved", "Добавить в идеи", "secondary")}
        {_trend_action_form(topic_id, "rejected", "Отклонить", "ghost")}
      </div>
    </article>
    """


def _case_insights_html(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "<p>Подходящих кейсов пока нет. Можно использовать общий авторский опыт как наблюдение.</p>"
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = escape(str(item.get("title", "")).strip())
        why = escape(str(item.get("why", "")).strip())
        theses = escape(str(item.get("theses", "")).strip())
        if title:
            rows.append(f"<p><b>{title}</b><br>{why}<br><span>{theses}</span></p>")
    return "".join(rows) or "<p>Подходящих кейсов пока нет. Можно использовать общий авторский опыт как наблюдение.</p>"


def _score_value(item: dict[str, object], key: str) -> str:
    return str(item.get(key, "")).strip() or "0"


def _score_stars(value: object) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    filled = max(1, min(5, round(score / 2)))
    return "★" * filled + "☆" * (5 - filled)


def _publication_ideas_html(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "<p>Пока недостаточно данных для вариантов публикаций.</p>"
    rows = []
    for platform in ("LinkedIn", "Telegram", "VC", "Сетка"):
        title = str(value.get(platform, "")).strip()
        if title:
            rows.append(f"<p><b>{escape(platform)}:</b> {escape(title)}</p>")
    return "".join(rows) or "<p>Пока недостаточно данных для вариантов публикаций.</p>"


def _category_ru(value: str) -> str:
    return {
        "AI": "ИИ",
        "Hospitality": "Гостеприимство",
        "Customer Experience": "Клиентский опыт",
        "Operations": "Операции",
        "Management": "Управление",
        "External": "Внешний сигнал",
    }.get(value, value or "Не задана")


def _repeat_risk_label(value: str) -> str:
    value = value.strip().lower()
    labels = {
        "низкий": "низкий",
        "средний": "средний",
        "высокий": "высокий",
        "low": "низкий",
        "medium": "средний",
        "high": "высокий",
        "РЅРёР·РєРёР№": "низкий",
        "СЃСЂРµРґРЅРёР№": "средний",
        "РІС‹СЃРѕРєРёР№": "высокий",
    }
    return labels.get(value, value or "низкий")


def _recommendation_label(value: str) -> str:
    labels = {
        "брать": "брать",
        "отложить": "отложить",
        "не брать": "не брать",
        "Р±СЂР°С‚СЊ": "брать",
        "РѕС‚Р»РѕР¶РёС‚СЊ": "отложить",
        "РЅРµ Р±СЂР°С‚СЊ": "не брать",
    }
    return labels.get(value, value or "отложить")


def _trend_action_form(topic_id: str, action: str, label: str, button_class: str = "") -> str:
    class_attr = f' class="{button_class}"' if button_class else ""
    return f"""
    <form method="post" action="/trend-radar/action">
      <input type="hidden" name="topic_id" value="{escape(topic_id)}">
      <input type="hidden" name="action" value="{escape(action)}">
      <button{class_attr} type="submit">{escape(label)}</button>
    </form>
    """


def _inline_list(value: object, empty: str) -> str:
    if not isinstance(value, list) or not value:
        return escape(empty)
    return escape(", ".join(str(item) for item in value))


def _trend_status_ru(status: str) -> str:
    return {
        "new": "новая",
        "approved": "одобрено",
        "rejected": "отклонено",
        "saved": "сохранено",
        "planned": "в плане",
        "drafted": "черновик",
    }.get(status, status)


def _lesson_card(lesson: object) -> str:
    lesson_id = escape(str(getattr(lesson, "id", "")))
    rule = str(getattr(lesson, "rule", ""))
    return f"""
    <article class="card">
      <p class="label">Предлагаемое правило</p>
      <form method="post" action="/learning/lesson/{lesson_id}">
        <textarea name="rule" rows="4">{escape(rule)}</textarea>
        <p><b>Почему система так решила:</b> {escape(str(getattr(lesson, "reason", "")))}</p>
        <p><b>Уверенность:</b> {escape(str(getattr(lesson, "confidence", "")))}%</p>
        <div class="form-actions">
          <button type="submit" name="status" value="accepted">Принять</button>
          <button class="secondary" type="submit" name="status" value="candidate">Изменить</button>
          <button class="ghost" type="submit" name="status" value="rejected">Отклонить</button>
        </div>
      </form>
    </article>
    """


def _lesson_summary_card(lesson: object) -> str:
    return f"""
    <article class="card">
      <p>{escape(str(getattr(lesson, "rule", "")))}</p>
      <p class="why">{escape(str(getattr(lesson, "reason", "")))}</p>
    </article>
    """


def _lesson_source_label(source: str) -> str:
    return "добавлено мной" if source in {"manual", "user"} else "добавлен AI"


def _memory_inbox_card(item: object) -> str:
    item_id = escape(str(getattr(item, "id", "")))
    extracted = getattr(item, "extracted", {})
    themes = ", ".join(str(value) for value in extracted.get("themes", [])[:6]) if isinstance(extracted, dict) else ""
    return f"""
    <article class="card">
      <h3>{escape(str(getattr(item, "title", "")))}</h3>
      <p>{escape(str(getattr(item, "summary", "")))}</p>
      <p class="why">Темы: {escape(themes or "не определены")}</p>
      <form method="post" action="/memory-inbox/{item_id}" class="form-actions">
        <button type="submit" name="status" value="accepted">Добавить в память</button>
        <button class="ghost" type="submit" name="status" value="rejected">Отклонить</button>
      </form>
    </article>
    """


def _ai_status_block(status: object, result: dict[str, object] | None) -> str:
    status_class = f"ai-{escape(status.state)}"
    diagnostics = ai_diagnostics()
    details = ""
    if status.error:
        details = f"<p class=\"risk\">{escape(status.error)}</p>"
    elif result:
        details = f"<p>Последний AI-анализ: {escape(str(result.get('generated_at', '')))}</p>"
    return f"""
    <section class="ai-panel {status_class}">
      <div>
        <p class="eyebrow">AI-анализ</p>
        <h2>{escape(status.message or "AI-анализ еще не запускался.")}</h2>
        {details}
        <p class="ai-panel-hint">Пересоберёт профиль автора и AI-черновик для сегодняшней публикации. Обычно занимает 30–60 секунд.</p>
      </div>
      <form method="post" action="/daily-brief/ai-refresh">
        <button type="submit" data-busy="Обновляю AI-анализ…">Обновить AI-анализ</button>
      </form>
      {_ai_diagnostics_block(diagnostics)}
    </section>
    """


def _ai_diagnostics_block(diagnostics: dict[str, object]) -> str:
    rows = (
        ("Python", diagnostics.get("python_executable", "")),
        ("Рабочая папка", diagnostics.get("cwd", "")),
        (".env загружен", "да" if diagnostics.get("env_loaded") else "нет"),
        ("ProxyAPI настроен", "да" if diagnostics.get("proxy_configured") else "нет"),
        ("Модель (основная)", diagnostics.get("model", "")),
        ("Модель (глубокий анализ)", diagnostics.get("premium_model", "")),
        ("Последняя ошибка AI-анализа", diagnostics.get("last_error", "") or "нет"),
        ("Последняя техническая ошибка AI-действия", diagnostics.get("last_action_error", "") or "нет"),
    )
    return f"""
      <details class="ai-diagnostics">
        <summary>Диагностика AI</summary>
        <dl>
          {"".join(f"<div><dt>{escape(str(label))}</dt><dd>{escape(_display_ru(str(value)))}</dd></div>" for label, value in rows)}
        </dl>
      </details>
    """


def _auto_refresh_meta(status: object) -> str:
    # Refresh while the AI is running, but never interrupt the user while they read or type.
    if getattr(status, "state", "") == "running":
        return (
            "<script>(function(){"
            "var last=Date.now();"
            "['keydown','mousemove','scroll','input','focusin','touchstart'].forEach(function(ev){"
            "document.addEventListener(ev,function(){last=Date.now();},{passive:true});});"
            "setInterval(function(){"
            "var el=document.activeElement;"
            "if(el&&(el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.tagName==='SELECT'))return;"
            "if(Date.now()-last<8000)return;"
            "location.reload();"
            "},4000);})();</script>"
        )
    return ""


def _global_script() -> str:
    # Injected on every page (via _send_html): confirm destructive deletes, and give
    # every form's button a loading state + double-submit guard.
    return (
        "<script>(function(){"
        "document.addEventListener('submit',function(e){"
        "var f=e.target; if(!(f&&f.tagName==='FORM'))return;"
        "var action=f.getAttribute('action')||'';"
        "if(action.indexOf('/delete/')>=0||action.indexOf('/remove/')>=0){"
        "if(!window.confirm('Удалить безвозвратно? Это действие нельзя отменить.')){e.preventDefault();return;}}"
        "if(f.dataset.submitting==='1'){e.preventDefault();return;}"
        "f.dataset.submitting='1';"
        "var btn=e.submitter||f.querySelector('button[type=\"submit\"],button:not([type])');"
        "var hasInline=f.getAttribute('onsubmit');"
        "setTimeout(function(){if(!btn)return;"
        "if(btn.tagName==='BUTTON'&&!hasInline){btn.textContent=btn.dataset.busy||'Подождите…';}"
        "btn.disabled=true;},0);"
        "});})();</script>"
    )


def _ai_error_note(detail: object, action: str = "сгенерировать текст") -> str:
    """A calm, human explanation instead of a raw AI/network error string."""
    detail_text = str(detail or "").strip()
    extra = (
        f"<details class=\"error-detail\"><summary>Технические детали</summary>{escape(detail_text)}</details>"
        if detail_text
        else ""
    )
    return (
        "<div class=\"state-note error-note\">"
        f"Не удалось {escape(action)} — AI сейчас не ответил. Попробуйте ещё раз через минуту. "
        "Если повторяется несколько раз, проверьте баланс и ключ в настройках."
        f"{extra}</div>"
    )


def _free_day_card(brief: DailyBrief) -> str:
    return f"""
    <section class="today-card free-day">
      <div class="today-main">
        <p class="eyebrow">сегодня</p>
        <h2>Свободный день</h2>
        <h3>На сегодня в контент-плане нет запланированной публикации</h3>
      </div>
      <div class="today-details">
        <div>
          <p class="label">Что это значит</p>
          <p>Сегодня по плану публиковать ничего не нужно — это нормально, план идёт не каждый день.</p>
        </div>
        <div>
          <p class="label">Если хочется что-то сделать</p>
          <p>Можно открыть контент-план и добавить публикацию или заглянуть в «Идеи».</p>
        </div>
      </div>
      <div class="today-actions">
        <a class="primary-action" href="/content-plan">Открыть контент-план</a>
        <a class="secondary" href="/ideas">Идеи</a>
      </div>
    </section>"""


def _today_card(
    brief: DailyBrief,
    topic: BriefItem | None,
    idea: BriefItem | None,
    recommendation: BriefItem | None,
    ai_result: dict[str, object] | None = None,
) -> str:
    if not brief.topics:
        return _free_day_card(brief)
    title = _today_title(brief.topics)
    item_key = _item_key(title)
    platform = _today_platforms(brief.topics)
    goal = _today_goal(brief, topic)
    why_today = topic.reason if topic else "На сегодня нет активных публикаций в контент-плане."
    why_agent = str(ai_result.get("choice_reason", "")) if ai_result else ""
    if not _text_matches_platform(why_agent, platform):
        why_agent = ""
    why_agent = why_agent or (recommendation.reason if recommendation else (topic.reason if topic else "Агент не видит публикации, которую нужно форсировать."))
    time_estimate = _time_estimate(platform)
    idea_text = str(ai_result.get("main_idea", "")) if ai_result else ""
    if not _text_matches_platform(idea_text, platform):
        idea_text = ""
    idea_text = idea_text or (idea.title if idea else "Рабочая очередь берется из контент-плана.")
    refinement = _refinement_entry(_load_ui_state(), item_key)
    title = str(refinement.get("title") or title)
    idea_text = str(refinement.get("text") or idea_text)
    refinement_notice = _refinement_notice(refinement)
    publication_rows = _today_publication_rows(brief.topics, brief.content_plan)
    return f"""
    <section class="today-card" id="{escape(item_key)}">
      <div class="today-main">
        <p class="eyebrow">сегодня</p>
        <h2>Публикация дня</h2>
        <h3>{escape(title)}</h3>
        <div class="today-meta">
          <span>{escape(platform)}</span>
          <span>{escape(time_estimate)}</span>
        </div>
      </div>
      <div class="today-details">
        <div>
          <p class="label">Цель публикации</p>
          <p>{escape(goal)}</p>
        </div>
        <div>
          <p class="label">Главная идея</p>
          <p>{escape(idea_text)}</p>
        </div>
      </div>
      <details class="today-why secondary-details">
        <summary>
          <span class="s-eyebrow">объяснение</span>
          <span class="s-title">Почему AI это предлагает</span>
          <span class="s-hint">Логика выбора темы</span>
        </summary>
        <div class="secondary-body">
          <div class="today-details">
            <div>
              <p class="label">Почему именно сегодня</p>
              <p>{escape(why_today)}</p>
            </div>
            <div>
              <p class="label">Почему агент рекомендует этот пост</p>
              <p>{escape(why_agent)}</p>
            </div>
          </div>
        </div>
      </details>
      <div class="today-publications">{publication_rows}</div>
      {refinement_notice}
      <div class="today-actions">
        <a class="primary-action" href="#drafts">Создать черновик</a>
        <form method="post" action="/daily-brief/refine">
          <input type="hidden" name="item_key" value="{escape(item_key)}">
          <input type="hidden" name="action" value="Другой вариант">
          <input type="hidden" name="title" value="{escape(title)}">
          <input type="hidden" name="text" value="{escape(idea_text)}">
          <input type="hidden" name="kind" value="today">
          <button class="secondary" type="submit">Другой вариант</button>
        </form>
      </div>
    </section>
    """


def _today_title(topics: tuple[BriefItem, ...]) -> str:
    if not topics:
        return "Сегодня нет публикаций в контент-плане"
    if len(topics) == 1:
        return topics[0].title
    return f"Сегодня запланировано {len(topics)} публикации"


def _today_platforms(topics: tuple[BriefItem, ...]) -> str:
    platforms = []
    for item in topics:
        platform = _platform_for_item(item)
        if platform and platform not in platforms:
            platforms.append(platform)
    return ", ".join(platforms) if platforms else "Площадка не выбрана"


def _text_matches_platform(text: str, platform: str) -> bool:
    if not text.strip():
        return True
    if _normalize_platform(platform) == "LinkedIn":
        return True
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cyrillic:
        return latin <= cyrillic * 1.4
    return latin < 18


def _today_goal(brief: DailyBrief, topic: BriefItem | None) -> str:
    if not topic:
        return "Не планировать публикацию без записи в контент-плане."
    publication = _publication_for_topic(brief.content_plan, topic.title)
    if publication and getattr(publication, "goal", ""):
        return str(publication.goal)
    return topic.action


def _today_publication_rows(topics: tuple[BriefItem, ...], plan: ContentPlan) -> str:
    if not topics:
        return "<div class=\"empty\">Добавьте публикацию на сегодня в Контент-план.</div>"
    rows = []
    for item in topics:
        publication = _publication_for_topic(plan, item.title)
        platform = str(getattr(publication, "platform", "")) or _platform_for_item(item) or "Площадка не выбрана"
        status = str(getattr(publication, "status", "")) or ""
        goal = str(getattr(publication, "goal", "")) or item.action
        summary = str(getattr(publication, "summary", "")) or item.summary
        rows.append(
            f"""
            <article class="today-publication">
              <div>
                <span>{escape(platform)} · {escape(_status_ru(status))}</span>
                <h4>{escape(item.title)}</h4>
                <p><b>Цель:</b> {escape(goal)}</p>
                <p>{escape(summary)}</p>
              </div>
              <strong>AI-приоритет {escape(str(item.score))}</strong>
            </article>
            """
        )
    return "".join(rows)


def _compact_content_plan_block(plan: ContentPlan) -> str:
    day_groups = _week_plan_day_groups(plan)
    if not day_groups:
        body = '<div class="empty">В контент-плане пока нет будущих публикаций.</div>'
    else:
        body = "".join(_week_group_card(day, items) for day, items in day_groups)
    return f"""
    <section class="content-plan compact-plan">
      <div class="section-title">
        <div>
          <p class="eyebrow">контент-план</p>
          <h2>План недели</h2>
        </div>
        <span>{escape(plan.week)}</span>
      </div>
      <div class="week-list">{body}</div>
      <a class="open-link" href="/content-plan">Открыть полный контент-план</a>
    </section>
    """


def _week_plan_day_groups(plan: ContentPlan) -> list[tuple[date, list[PlannedPublication]]]:
    """Build day-by-day week view from today through plan end, including empty days."""
    today = today_moscow()
    raw_plan = _load_content_plan_raw()
    week_start, week_end = _content_plan_period(raw_plan)
    parsed_start = parse_plan_date(week_start) or today
    parsed_end = parse_plan_date(week_end) or parsed_start
    if parsed_end < today:
        parsed_end = today

    by_date: dict[date, list[PlannedPublication]] = {}
    for item in plan.planned_publications:
        parsed = parse_plan_date(str(item.date))
        if parsed is None or parsed < today or parsed > parsed_end:
            continue
        by_date.setdefault(parsed, []).append(item)
    for items in by_date.values():
        items.sort(key=lambda pub: (str(pub.platform), str(pub.topic)))

    days: list[tuple[date, list[PlannedPublication]]] = []
    cursor = max(today, parsed_start)
    while cursor <= parsed_end:
        days.append((cursor, by_date.get(cursor, [])))
        cursor += timedelta(days=1)
    return days


def _week_group_card(day: date, items: list[PlannedPublication]) -> str:
    today = today_moscow()
    day_label = weekday_name_for_date(day.isoformat())
    date_text = day.strftime("%d.%m")
    extra_class = " is-today" if day == today else (" is-empty" if not items else "")
    if items:
        rows = "".join(
            f"""
            <div class="week-publication">
              <strong>{escape(str(item.platform))}</strong>
              <span>{escape(str(item.topic))}</span>
            </div>
            """
            for item in items
        )
    else:
        rows = '<div class="week-publication muted"><span>Нет публикаций</span></div>'
    return f"""
    <article class="week-item{extra_class}">
      <span>{escape(day_label)} · {escape(date_text)}</span>
      {rows}
    </article>
    """


def render_content_plan_page(plan: dict[str, object], saved: bool = False, view: str = "list", action_status: str = "", published_posts: list | None = None) -> str:
    notice = "<div class=\"notice\">Контент-план сохранен.</div>" if saved else ""
    if action_status == "updated":
        notice += "<div class=\"notice\">Обновлено.</div>"
    if plan.get("updated_at"):
        notice += f"<div class=\"state-note\">Обновлено: {escape(_format_moscow_time(plan.get('updated_at')))} (МСК)</div>"
    if plan.get("last_action"):
        notice += f"<div class=\"state-note\">{escape(str(plan.get('last_action')))}</div>"
    if plan.get("ai_error"):
        notice += _ai_error_note(plan.get("ai_error"), "составить план")
    publications = plan.get("planned_publications", [])
    week_start, week_end = _content_plan_period(plan)
    # The list tab shows only the selected week; the calendar shows all published posts.
    week_publications, _ = _split_week_publications(publications, week_start, week_end)
    rows = "".join(_content_plan_edit_row(item, index) for index, item in enumerate(week_publications))
    new_index = len(week_publications)
    view = "calendar" if view == "calendar" else "list"
    calendar_block = _content_plan_published_calendar(published_posts or []) if view == "calendar" else ""
    content = f"""
    {notice}
    <div class="view-switch">
      <a class="{'active' if view == 'list' else ''}" href="/content-plan?view=list">Список</a>
      <a class="{'active' if view == 'calendar' else ''}" href="/content-plan?view=calendar">Календарь</a>
    </div>
    {calendar_block}
    <form class="profile-form" method="post" action="/content-plan" onsubmit="if (document.activeElement && document.activeElement.tagName === 'BUTTON') {{ document.activeElement.dataset.originalText = document.activeElement.textContent; document.activeElement.textContent = 'Генерируется...'; }}">
      <input type="hidden" name="view" value="{escape(view)}">
      <section class="profile-section">
        <p class="eyebrow">неделя</p>
        <div class="form-grid">
          {_date_input("week_start", "Дата начала недели", week_start)}
          {_date_input("week_end", "Дата конца недели", week_end)}
        </div>
        {_textarea("focus", "Фокус недели", _clean_focus_value(plan.get("focus", "")))}
        <input type="hidden" name="today_recommendation" value="{escape(str(plan.get("today_recommendation", "")))}">
        <input type="hidden" name="content_pillars" value="{escape(list_to_text(plan.get("content_pillars", [])))}">
        <input type="hidden" name="platform_targets" value="{escape(list_to_text(plan.get("platform_targets", [])))}">
        <div class="form-actions">
          <button name="plan_action" value="save_focus" type="submit">Сохранить фокус и даты</button>
        </div>
      </section>
      <section class="profile-section">
        <p class="eyebrow">публикации</p>
        <div class="plan-edit-list">{rows}</div>
        <article class="plan-item edit-row">
          <h3>Добавить публикацию</h3>
          {_date_input("new_pub_date", "Дата", week_start)}
          {_select("new_pub_platform", "Площадка", "", CONTENT_PLATFORMS)}
          {_input("new_pub_topic", "Тема", "")}
          {_input("new_pub_goal", "Цель", "")}
          {_select("new_pub_pillar", "Рубрика", "Наблюдение", RUBRICS)}
          {_select("new_pub_format", "Формат публикации", "пост", PUBLICATION_FORMATS)}
          {_input("new_pub_note", "Заметка / ТЗ", "")}
          <input type="hidden" name="new_pub_summary" value="">
          <input type="hidden" name="new_pub_status" value="draft">
          <button class="ghost" name="plan_action" value="add_publication" type="submit">Добавить публикацию</button>
        </article>
        <input type="hidden" name="new_pub_index" value="{new_index}">
      </section>
      <section class="profile-section">
        <p class="eyebrow">действия</p>
        <div class="form-actions">
          <button name="plan_action" value="approve" type="submit">Утвердить план</button>
          <button class="ghost" name="plan_action" value="strategy_plan" type="submit">Создать план по стратегии</button>
          <a href="/author-profile?tab=strategy">Настроить стратегию</a>
        </div>
      </section>
    </form>
"""
    return _page_shell(
        title="Контент-план",
        eyebrow="план публикаций",
        heading="Контент-план",
        hint="План публикаций по неделям — темы, площадки и форматы.",
        active="content",
        content=content,
    )


def render_text_posts_page(repository: TextPostRepository, query: dict[str, list[str]], plan: dict[str, object]) -> str:
    tab = query.get("tab", ["planned"])[0]
    tab = tab if tab in {"planned", "archive"} else "planned"
    search = query.get("q", [""])[0].strip()
    platform = query.get("platform", [""])[0].strip()
    page = _positive_int(query.get("page", ["1"])[0], 1)
    week_start, week_end = _content_plan_period(plan)
    period_active = any(query.get(key, [""])[0].strip() for key in ("month", "week_start", "week_end"))
    posts = repository.list_posts(tab=tab, query=search, platform=platform)
    if tab == "planned" and period_active:
        posts = _filter_text_posts_by_period(posts, week_start, week_end)
    per_page = 10
    page_count = max(1, (len(posts) + per_page - 1) // per_page)
    page = max(1, min(page, page_count))
    visible = posts[(page - 1) * per_page : page * per_page]
    empty_html = (
        "<div class=\"empty\">Здесь пока пусто. Тексты появляются автоматически из контент-плана "
        "или добавьте новый вручную ниже.</div>"
        if tab == "planned"
        else "<div class=\"empty\">В архиве пока пусто. Добавьте опубликованный пост вручную ниже.</div>"
    )
    rows = "".join(_text_post_row(post) for post in visible) or empty_html
    saved = query.get("saved", ["0"])[0] == "1"
    deleted = query.get("deleted", ["0"])[0] == "1"
    notice = "<div class=\"notice\">Сохранено.</div>" if saved else ""
    if deleted:
        notice += "<div class=\"notice\">Удалено.</div>"
    content = f"""
    {notice}
    <div class="view-switch">
      <a class="{'active' if tab == 'planned' else ''}" href="/texts?tab=planned">Запланировано</a>
      <a class="{'active' if tab == 'archive' else ''}" href="/texts?tab=archive">Архив</a>
    </div>
    <form class="period-picker text-filter" method="get" action="/texts">
      <input type="hidden" name="tab" value="{escape(tab)}">
      {'<label><span>Начало недели</span><input type="date" name="week_start" value="' + escape(_date_for_input(week_start)) + '" onchange="this.form.submit()"></label>' if tab == 'planned' else ''}
      {'<label><span>Конец недели</span><input type="date" name="week_end" value="' + escape(_date_for_input(week_end)) + '" onchange="this.form.submit()"></label>' if tab == 'planned' else ''}
      <label><span>Поиск по названию</span><input type="search" name="q" value="{escape(search)}" placeholder="Название поста"></label>
      <label><span>Площадка</span><select name="platform">
        <option value="">Все площадки</option>
        {_platform_options(platform)}
      </select></label>
      <button class="ghost" type="submit">Найти</button>
      <a class="secondary-link" href="/texts?tab={escape(tab)}">Сбросить</a>
    </form>
    <section class="text-list">
      <div class="section-title">
        <div>
          <p class="eyebrow">{'запланированные тексты' if tab == 'planned' else 'архив публикаций'}</p>
          <h2>{'Запланировано' if tab == 'planned' else 'Архив'}</h2>
          {('<p class="page-hint">Период: ' + escape(_format_week_range(week_start, week_end)) + '</p>' if period_active else '<p class="page-hint">Показаны все запланированные тексты из контент-плана. Выберите период выше, чтобы отфильтровать.</p>') if tab == 'planned' else ''}
        </div>
        <span>{len(posts)} всего</span>
      </div>
      <div class="knowledge-list">{rows}</div>
      {_pagination('/texts', tab, search, platform, page, page_count, week_start, week_end)}
    </section>
    {_manual_planned_form() if tab == 'planned' else _manual_archive_form()}
"""
    return _page_shell(
        title="Тексты",
        eyebrow="редакция",
        heading="Тексты",
        hint="Рабочее место для запланированных публикаций и архива опубликованных постов.",
        active="texts",
        content=content,
    )


def _generate_post_text(title: str, platform: str, brief: str) -> dict[str, object]:
    """Generate a ready-to-publish post body from the brief, in the author's style."""
    title = title.strip()
    try:
        gateway = AIGateway()
        if not gateway.is_configured():
            raise AIGatewayError("ProxyAPI не настроен.")
        norm_platform = _normalize_platform(platform)
        language = _language_for_platform(norm_platform)
        context = DailyBriefRequestHandler.ai_context_engine.build(
            {"topic": title, "platform": platform, "summary": brief},
            include_local_sources=True,
        )
        response = _complete_json_with_retry(
            gateway,
            system_prompt=(
                "Ты пишешь от лица КОНКРЕТНОГО автора, а не как универсальный AI-копирайтер. "
                "Опирайся прежде всего на его материал из контекста: ключевые идеи, кейсы, наблюдения, "
                "принципы, темы и Writing DNA. Это не общий экспертный пост из интернета, а ход мысли "
                "именно этого автора: его формулировки, его примеры, его взгляд. "
                "Не добавляй общих мест, банальностей и «универсальных советов», которых нет в его картине мира. "
                "Соблюдай правила площадки и запрещённые начала. Не добавляй пояснений от себя. "
                "Ответь строго JSON с единственным полем draft."
            ),
            user_prompt=(
                f"Площадка: {norm_platform}\n"
                f"Язык: {language}. {_language_policy_for_platform(norm_platform)}\n"
                f"Тема: {title}\n"
                f"Задание (бриф):\n{brief or '—'}\n\n"
                "МАТЕРИАЛ АВТОРА — ГЛАВНЫЙ ИСТОЧНИК (JSON). Строй пост на нём, а не на общих знаниях:\n"
                f"{json.dumps(context, ensure_ascii=False)}\n\n"
                "Напиши полный текст поста: сильное начало без запрещённых клише, структура по правилам "
                "рубрики, живой авторский тон, конкретика из материала автора вместо общих мест. "
                "Если по теме есть подходящие идеи/кейсы/наблюдения автора — используй именно их.\n"
                "Верни строго JSON: {\"draft\": \"полный текст поста\"}."
            ),
            action="text_post_generate",
        )
        draft = str(response.get("draft") or response.get("text") or "").strip()
        if not draft:
            return {"error": "AI вернул пустой ответ. Попробуйте ещё раз."}
        return {"text": draft}
    except AIGatewayError as exc:
        _save_ai_action_error("text_post_generate", exc)
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - surface any failure as a friendly message
        _save_ai_action_error("text_post_generate", exc)
        return {"error": f"Не удалось сгенерировать текст: {exc}"}


def _revise_post_text(current_text: str, instruction: str, title: str, platform: str) -> dict[str, object]:
    """Revise the author's existing post text according to precise edit comments.

    Unlike generation, this keeps the author's text and applies exactly what the
    instruction asks — it does not rewrite the post from scratch."""
    current_text = current_text.strip()
    instruction = instruction.strip()
    if not instruction:
        return {"error": "Напишите, что именно поменять в тексте."}
    if not current_text:
        return {"error": "Сначала напишите или сгенерируйте текст, затем попросите AI внести правки."}
    try:
        gateway = AIGateway()
        if not gateway.is_configured():
            raise AIGatewayError("ProxyAPI не настроен.")
        norm_platform = _normalize_platform(platform)
        language = _language_for_platform(norm_platform)
        context = DailyBriefRequestHandler.ai_context_engine.build(
            {"topic": title, "platform": platform, "summary": instruction},
            include_local_sources=True,
        )
        response = _complete_json_with_retry(
            gateway,
            system_prompt=(
                "Ты AI-редактор конкретного автора. Тебе дают ГОТОВЫЙ текст поста автора и "
                "точные правки от автора. Внеси ИМЕННО эти правки, сохрани авторский текст, тон и "
                "структуру там, где правки их не затрагивают. Не переписывай пост с нуля. "
                "Где нужно что-то добавить — бери материал автора из контекста (его идеи, кейсы, наблюдения), "
                "а не общие формулировки из интернета. Не добавляй пояснений от себя, соблюдай Writing DNA и "
                "правила площадки. Ответь строго JSON с полем draft."
            ),
            user_prompt=(
                f"Площадка: {norm_platform}\n"
                f"Язык: {language}. {_language_policy_for_platform(norm_platform)}\n"
                f"Тема: {title}\n\n"
                f"ТЕКУЩИЙ ТЕКСТ ПОСТА:\n{current_text}\n\n"
                f"ПРАВКИ АВТОРА (примени точно и только их):\n{instruction}\n\n"
                "МАТЕРИАЛ АВТОРА для любых добавлений (JSON) — опирайся на него, а не на общие знания:\n"
                f"{json.dumps(context, ensure_ascii=False)}\n\n"
                "Верни строго JSON: {\"draft\": \"полный обновлённый текст поста\"}."
            ),
            action="text_post_revise",
        )
        draft = str(response.get("draft") or response.get("text") or "").strip()
        if not draft:
            return {"error": "AI вернул пустой ответ. Попробуйте ещё раз."}
        return {"text": draft}
    except AIGatewayError as exc:
        _save_ai_action_error("text_post_revise", exc)
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - surface any failure as a friendly message
        _save_ai_action_error("text_post_revise", exc)
        return {"error": f"Не удалось внести правки: {exc}"}


def render_text_post_detail(
    post: TextPost,
    saved: bool = False,
    generated: bool = False,
    gen_error: str = "",
    revised: bool = False,
    revise_error: str = "",
) -> str:
    back_tab = post.tab
    notice = ""
    if saved:
        notice += "<div class=\"notice\">Сохранено.</div>"
    if generated:
        notice += "<div class=\"notice\">Текст сгенерирован. Проверьте и при необходимости отредактируйте.</div>"
    if revised:
        notice += "<div class=\"notice\">Правки внесены. Проверьте результат и при необходимости уточните комментарий.</div>"
    if gen_error:
        notice += _ai_error_note(gen_error, "сгенерировать текст")
    if revise_error:
        notice += _ai_error_note(revise_error, "внести правки")
    archive_action = (
        '<button class="ghost" name="action" value="archive" type="submit">Отметить опубликованным</button>'
        if post.tab == "planned"
        else ""
    )
    brief_html = ""
    if post.brief.strip():
        rows = ""
        for line in post.brief.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                label, _, value = line.partition(":")
                rows += f"<p><b>{escape(label)}:</b>{escape(value)}</p>"
            else:
                rows += f"<p>{escape(line)}</p>"
        brief_html = (
            "<section class=\"text-brief\">"
            "<p class=\"eyebrow\">задание из контент-плана</p>"
            "<p class=\"brief-hint\">Это подсказка, о чём писать. Сам пост пишите в поле ниже.</p>"
            f"<div class=\"brief-lines\">{rows}</div>"
            "</section>"
        )
    content = f"""
    {notice}
    <form class="profile-form" method="post" action="/texts/{escape(post.id)}">
      <div class="focus-bar">
        <button id="focus-exit" class="ghost" type="button">← Выйти из фокуса</button>
        <p class="editor-meta"><span id="char-count-2">0</span> симв. · <span id="word-count-2">0</span> сл.</p>
        <button name="action" value="save" type="submit">Сохранить</button>
      </div>
      <section class="profile-section">
        <div class="form-grid">
          {_input("title", "Название", post.title)}
          {_select("platform", "Площадка", post.platform, CONTENT_PLATFORMS)}
          {_date_input("publication_date", "Дата публикации", post.publication_date)}
          {_select("status", "Статус", post.status, list(TEXT_POST_STATUSES))}
        </div>
        <p class="mode-hint">Статус «Утверждено» означает, что этот текст готов — Дневной бриф будет предлагать именно его.</p>
        {brief_html}
        <label class="editor-label">
          <span>Текст публикации</span>
          <textarea id="post-text" name="text" rows="16" placeholder="Пишите пост здесь…">{escape(post.text)}</textarea>
        </label>
        <div class="editor-bar">
          <p class="editor-meta"><span id="char-count">0</span> символов · <span id="word-count">0</span> слов</p>
          <div class="editor-bar-actions">
            <button name="action" value="generate" type="submit" class="secondary"
              onclick="var t=document.getElementById('post-text'); if(t&&t.value.trim()&&!confirm('Заменить текущий текст сгенерированным AI?'))return false; this.textContent='Генерирую…';">✨ Сгенерировать текст</button>
            <button id="focus-enter" class="ghost" type="button">✎ Режим фокуса</button>
            <button id="copy-text" class="ghost" type="button">Скопировать текст</button>
          </div>
        </div>
        <div class="ai-revise">
          <label class="editor-label">
            <span>Правки для AI</span>
            <textarea id="edit-instruction" name="edit_instruction" rows="3" placeholder="Напишите точные правки: например «сократи вступление, убери клише, добавь пример из гостиничного сервиса, сделай финал сильнее»"></textarea>
          </label>
          <p class="brief-hint">AI перепишет текущий текст, применив именно эти комментарии, и сохранит ваш стиль.</p>
          <button name="action" value="revise" type="submit" class="secondary"
            onclick="var i=document.getElementById('edit-instruction'); if(!i||!i.value.trim()){{alert('Напишите, что поменять в тексте.');return false;}} this.textContent='Вношу правки…';">✍️ Внести правки по комментарию</button>
        </div>
        <div class="form-actions">
          <button name="action" value="save" type="submit">Сохранить</button>
          {archive_action}
          <button class="ghost danger-text" name="action" value="delete" type="submit"
            onclick="return confirm('Удалить этот текст безвозвратно? Действие нельзя отменить.');">Удалить</button>
          <a href="/texts?tab={escape(back_tab)}">Назад к списку</a>
        </div>
      </section>
    </form>
    <script>(function(){{
      var t=document.getElementById('post-text');
      var counts=[['char-count','word-count'],['char-count-2','word-count-2']];
      function upd(){{if(!t)return;var v=t.value;var c=v.length;var w=v.trim()?v.trim().split(/\\s+/).length:0;
        counts.forEach(function(p){{var ce=document.getElementById(p[0]),we=document.getElementById(p[1]);
          if(ce)ce.textContent=c; if(we)we.textContent=w;}});}}
      if(t){{t.addEventListener('input',upd);upd();}}
      var b=document.getElementById('copy-text');
      if(b&&t){{b.addEventListener('click',function(){{
        if(!navigator.clipboard){{t.select();document.execCommand('copy');}}
        else{{navigator.clipboard.writeText(t.value);}}
        b.textContent='Скопировано ✓';
        setTimeout(function(){{b.textContent='Скопировать текст';}},1500);
      }});}}
      var fe=document.getElementById('focus-enter');
      if(fe)fe.addEventListener('click',function(){{document.body.classList.add('focus-on'); if(t)t.focus();}});
      var fx=document.getElementById('focus-exit');
      if(fx)fx.addEventListener('click',function(){{document.body.classList.remove('focus-on');}});
    }})();</script>
"""
    return _page_shell(
        title=post.title,
        eyebrow="текст публикации",
        heading=post.title,
        hint=f"{post.platform} · {_format_text_date(post.publication_date)}",
        active="texts",
        content=content,
    )


def _text_post_row(post: TextPost) -> str:
    return f"""
    <article class="knowledge-card text-row">
      <a href="/texts/{escape(post.id)}">
        <h3>{escape(post.title)}</h3>
        <div class="doc-meta">
          <span>{escape(post.platform or "Без площадки")}</span>
          <span>{escape(_format_text_date(post.publication_date))}</span>
          <span>{escape(_status_ru(post.status))}</span>
        </div>
      </a>
      <a class="open-link" href="/texts/{escape(post.id)}">Открыть</a>
    </article>
    """


def _manual_archive_form() -> str:
    return f"""
    <section class="knowledge-upload">
      <p class="eyebrow">добавить вручную</p>
      <h2>Добавить пост в архив</h2>
      <form class="profile-form compact-editor" method="post" action="/texts/archive/add">
        <div class="form-grid">
          {_input("title", "Название", "")}
          {_select("platform", "Площадка", "", CONTENT_PLATFORMS)}
          {_date_input("publication_date", "Дата публикации", today_moscow().isoformat())}
        </div>
        {_textarea("text", "Полный текст", "")}
        <button type="submit">Добавить в архив</button>
      </form>
    </section>
    """


def _manual_planned_form() -> str:
    return f"""
    <section class="knowledge-upload">
      <p class="eyebrow">написать с нуля</p>
      <h2>Новый текст</h2>
      <p class="mode-hint">Создайте пост, которого нет в контент-плане. Он появится во вкладке «Запланировано».</p>
      <form class="profile-form compact-editor" method="post" action="/texts/planned/add">
        <div class="form-grid">
          {_input("title", "Название", "")}
          {_select("platform", "Площадка", "", CONTENT_PLATFORMS)}
          {_date_input("publication_date", "Дата публикации", today_moscow().isoformat())}
        </div>
        {_textarea("text", "Полный текст", "")}
        <button type="submit">Создать текст</button>
      </form>
    </section>
    """


def _platform_options(selected: str) -> str:
    return "".join(
        f"<option value=\"{escape(platform)}\" {'selected' if platform == selected else ''}>{escape(platform)}</option>"
        for platform in CONTENT_PLATFORMS
    )


def _pagination(base: str, tab: str, search: str, platform: str, page: int, page_count: int, week_start: str = "", week_end: str = "") -> str:
    if page_count <= 1:
        return ""
    params = f"tab={quote(tab)}&q={quote(search)}&platform={quote(platform)}"
    if tab == "planned":
        params += f"&week_start={quote(week_start)}&week_end={quote(week_end)}"
    prev_link = f"{base}?{params}&page={page - 1}" if page > 1 else ""
    next_link = f"{base}?{params}&page={page + 1}" if page < page_count else ""
    prev_html = f"<a href=\"{escape(prev_link)}\">← Назад</a>" if prev_link else "<span>← Назад</span>"
    next_html = f"<a href=\"{escape(next_link)}\">Вперед →</a>" if next_link else "<span>Вперед →</span>"
    return f"<nav class=\"pagination\">{prev_html}<strong>{page} / {page_count}</strong>{next_html}</nav>"


def _filter_text_posts_by_period(posts: list[TextPost], start: str, end: str) -> list[TextPost]:
    parsed_start = parse_plan_date(start)
    parsed_end = parse_plan_date(end)
    if not parsed_start and not parsed_end:
        return posts
    result = []
    for post in posts:
        parsed = parse_plan_date(post.publication_date)
        if not parsed:
            # Keep undated posts visible so plan items without a date are never lost.
            result.append(post)
            continue
        if parsed_start and parsed < parsed_start:
            continue
        if parsed_end and parsed > parsed_end:
            continue
        result.append(post)
    return result


def _format_text_date(value: str) -> str:
    parsed = parse_plan_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else (value or "без даты")


def _positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _editorial_strategy_panel(strategy: dict[str, object]) -> str:
    rows = "".join(
        _editorial_strategy_row(item, index)
        for index, item in enumerate(_normalize_strategy_entries(strategy.get("weekly_template", [])))
    )
    updated = str(strategy.get("updated_at", "")).strip() or "Используется дефолтная стратегия"
    return f"""
      <section class="block" id="editorial-strategy">
        <div class="section-title">
          <div>
            <p class="eyebrow">редакционная стратегия</p>
            <h2>Редакционная стратегия</h2>
          </div>
          <span>Обновлено: {escape(updated)}</span>
        </div>
        <form class="profile-form" method="post" action="/author-profile/strategy" onsubmit="if (document.activeElement && document.activeElement.tagName === 'BUTTON') {{ document.activeElement.dataset.originalText = document.activeElement.textContent; document.activeElement.textContent = 'Сохраняется...'; }}">
          <section class="profile-section">
            <p class="eyebrow">недельный шаблон</p>
            <div class="strategy-grid">{rows}</div>
            <p class="state-note">Правила рубрик редактируются на странице «Правила бота».</p>
            <div class="form-actions">
              <button class="ghost" name="plan_action" value="save_strategy" type="submit">Сохранить стратегию</button>
              <button name="plan_action" value="strategy_plan" type="submit">Создать план по стратегии</button>
            </div>
          </section>
        </form>
      </section>
    """


def _editorial_strategy_row(item: dict[str, object], index: int) -> str:
    active = "checked" if item.get("active") else ""
    day = str(item.get("day", ""))
    note = str(item.get("note", ""))
    return f"""
      <article class="plan-item edit-row strategy-row">
        <input type="hidden" name="strategy_{index}_day" value="{escape(day)}">
        <label class="check-field">
          <input type="checkbox" name="strategy_{index}_active" {active}>
          <span>{escape(day)}</span>
        </label>
        {_select(f"strategy_{index}_platform", "Площадка", _normalize_platform(str(item.get("platform", ""))), CONTENT_PLATFORMS)}
        {_select(f"strategy_{index}_rubric", "Рубрика", _normalize_rubric(str(item.get("rubric", ""))), RUBRICS)}
        {_select(f"strategy_{index}_format", "Формат публикации", _normalize_publication_format(str(item.get("format", ""))), PUBLICATION_FORMATS)}
        {_input(f"strategy_{index}_note", "Заметка/ограничение", note)}
      </article>
    """


def _open_text_query(item: object) -> str:
    item = item if isinstance(item, dict) else {}
    return f"platform={quote(str(item.get('platform', '')))}&topic={quote(str(item.get('topic', '')))}"


def _text_post_for_publication(repository: TextPostRepository, platform: str, topic: str) -> TextPost | None:
    """Find the Тексты post that corresponds to a content-plan publication, so the
    plan's «Открыть текст» link lands on the same synced item."""
    platform = str(platform).strip()
    topic = str(topic).strip()
    if not (platform or topic):
        return None
    source_key = source_key_for_publication({"topic": topic, "platform": platform})
    posts = repository.list_posts("planned") + repository.list_posts("archive")
    for post in posts:
        if post.source_key and post.source_key == source_key:
            return post
    for post in posts:
        if post.title.strip().lower() == topic.lower() and post.platform.strip().lower() == platform.lower():
            return post
    return None


def _content_plan_edit_row(item: object, index: int) -> str:
    item = item if isinstance(item, dict) else {}
    error = ""
    if item.get("ai_error"):
        error = _ai_error_note(item.get("ai_error"), "подобрать тему")
    if item.get("repeat_warning"):
        error += f"<div class=\"state-note repeat-note\">⚠ {escape(str(item.get('repeat_warning')))}</div>"
    updated = f"<div class=\"state-note\">Обновлено: {escape(_format_moscow_time(item.get('updated_at')))} (МСК)</div>" if item.get("updated_at") else ""
    day = weekday_name_for_date(str(item.get("date", ""))) or str(item.get("day", ""))
    status = _normalize_publication_status(str(item.get("status", "")))
    topic_text = str(item.get("topic", "")).strip() or "Без темы"
    platform_text = str(item.get("platform", "")).strip() or "Площадка не выбрана"
    date_text = _format_text_date(str(item.get("date", "")))
    day_label = escape(day) if day else date_text
    highlight = " has-alert" if (item.get("ai_error") or item.get("repeat_warning")) else ""
    return f"""
    <details class="plan-row{highlight}" id="publication-{index}">
      <summary class="plan-row-head">
        <span class="plan-row-date">{day_label} · {escape(date_text)}</span>
        <span class="plan-row-platform">{escape(platform_text)}</span>
        <span class="plan-row-topic">{escape(topic_text)}</span>
        {_status_badge(status)}
      </summary>
      <div class="plan-row-body">
        <div class="plan-fields">
          {_date_input(f"pub_{index}_date", "Дата", str(item.get("date", "")))}
          <label>День недели<span>{escape(day or "Будет определен по дате")}</span></label>
          {_select(f"pub_{index}_platform", "Площадка", str(item.get("platform", "")), CONTENT_PLATFORMS)}
          {_input(f"pub_{index}_topic", "Тема", item.get("topic", ""))}
          {_input(f"pub_{index}_goal", "Цель", item.get("goal", ""))}
          {_select(f"pub_{index}_pillar", "Рубрика", _publication_rubric(item), RUBRICS)}
          {_select(f"pub_{index}_format", "Формат публикации", _publication_format(item), PUBLICATION_FORMATS)}
          {_select(f"pub_{index}_status", "Статус", status, list(PUBLICATION_STATUSES))}
        </div>
        <input type="hidden" name="pub_{index}_summary" value="{escape(str(item.get("summary", item.get("note", ""))))}">
        {_textarea(f"pub_{index}_note", "Заметка / ТЗ", item.get("note", ""))}
        <p class="mode-hint">Полный текст поста готовится в разделе «Тексты» — здесь только тема и ТЗ.</p>
        {updated}
        {error}
        <div class="form-actions">
          <button class="ghost" name="plan_action" value="generate_pub_{index}" type="submit">Сгенерировать тему/ТЗ</button>
          <a class="ghost" href="/content-plan/open-text?{_open_text_query(item)}">Открыть текст →</a>
          {'' if status == 'published' else f'<button class="ghost" name="plan_action" value="publish_pub_{index}" type="submit">Отметить опубликованным</button>'}
          <button class="ghost danger-text" name="plan_action" value="delete_pub_{index}" type="submit">Удалить</button>
        </div>
      </div>
    </details>
    """


def _content_plan_published_calendar(published_posts: object) -> str:
    """The calendar is the full publishing history: every post ever published,
    grouped by date (newest first). It reads from the archive, so it keeps real
    past dates and never loses history when the weekly plan is regenerated."""
    posts = published_posts if isinstance(published_posts, list) else []
    dated = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        parsed = parse_plan_date(str(post.get("date", "")))
        if parsed:
            dated.append((parsed, post))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    weekday_labels = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    groups: dict[str, list[dict[str, object]]] = {}
    order: list[date] = []
    for parsed, post in dated:
        key = parsed.isoformat()
        if key not in groups:
            groups[key] = []
            order.append(parsed)
        groups[key].append(post)
    days = []
    for parsed in order:
        day_posts = "".join(_calendar_publication(post) for post in groups[parsed.isoformat()])
        days.append(
            f"""
            <div class="calendar-day">
              <strong>{weekday_labels[parsed.weekday()]}, {parsed.strftime('%d.%m.%Y')}</strong>
              {day_posts}
            </div>
            """
        )
    body = (
        f'<div class="calendar-grid week">{"".join(days)}</div>'
        if days
        else '<div class="empty">Пока нет опубликованных постов. Они появятся здесь, когда пост перейдёт в статус «Опубликовано».</div>'
    )
    return f"""
    <section class="block calendar-block">
      <div class="section-title">
        <div>
          <p class="eyebrow">опубликовано</p>
          <h2>Все публикации</h2>
        </div>
        <span>{len(dated)} опубликовано</span>
      </div>
      {body}
    </section>
    """


def _calendar_publication(post: dict[str, object]) -> str:
    platform = str(post.get("platform", ""))
    topic = str(post.get("topic", ""))
    summary = str(post.get("summary", ""))
    short_topic = _short_text(topic or "Без темы", 44)
    return f"""
    <details class="calendar-publication">
      <summary>
        <span>{escape(platform)}</span>
        <b>{escape(short_topic)}</b>
      </summary>
      <p>{escape(summary or topic)}</p>
    </details>
    """


def _short_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _clean_focus_value(value: object) -> str:
    """Return a clean week-focus string.

    Older generations could accidentally store the whole focus object
    ({'month_focus': ..., 'week_focus': ...}) as text in this field. If we
    detect that shape, pull out just the week focus so the field reads cleanly.
    """
    if isinstance(value, dict):
        return str(value.get("week_focus") or value.get("focus") or "").strip()
    text = str(value or "").strip()
    if text.startswith("{") and "week_focus" in text:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text
        if isinstance(parsed, dict):
            return str(parsed.get("week_focus") or parsed.get("focus") or "").strip()
    return text


def _load_content_plan_raw() -> dict[str, object]:
    # Present the plan on current dates in memory, but don't persist that back to
    # the committed seed on every read — that caused git churn and flaky tests.
    # Real edits still persist through the save handlers.
    plan = json.loads(DEFAULT_CONTENT_PLAN_PATH.read_text(encoding="utf-8"))
    # The plan is week-scoped only. Clean any legacy focus dump and drop the
    # retired month focus so nothing month-based leaks into the UI or the AI.
    if isinstance(plan, dict):
        plan["focus"] = _clean_focus_value(plan.get("focus", ""))
        plan.pop("month_focus", None)
    return refresh_stale_content_plan(plan, today_moscow())


def _content_plan_with_query_period(plan: dict[str, object], query: dict[str, list[str]]) -> dict[str, object]:
    month = query.get("month", [""])[0].strip()
    month_start, month_end = _month_range(month)
    start = _normalize_plan_date_value(query.get("week_start", [""])[0])
    end = _normalize_plan_date_value(query.get("week_end", [""])[0])
    if month_start and month_end:
        start, end = month_start, month_end
    if not start and not end:
        return plan
    updated = dict(plan)
    if start:
        updated["week_start"] = start
    if end:
        updated["week_end"] = end
    week_start, week_end = _content_plan_period(updated)
    updated["week_start"] = week_start
    updated["week_end"] = week_end
    updated["week"] = _format_week_range(week_start, week_end)
    updated["last_action"] = "Открыт выбранный период. Нажмите «Сохранить план» или «Создать новый план», чтобы закрепить изменения."
    return updated


def _content_plan_period(plan: dict[str, object]) -> tuple[str, str]:
    start = _normalize_plan_date_value(str(plan.get("week_start", "")))
    end = _normalize_plan_date_value(str(plan.get("week_end", "")))
    publications = plan.get("planned_publications", [])
    dates = [
        parsed
        for parsed in (
            parse_plan_date(str(item.get("date", "")))
            for item in publications
            if isinstance(item, dict)
        )
        if parsed is not None
    ]
    if not start and dates:
        start = min(dates).isoformat()
    if not end and dates:
        end = max(dates).isoformat()
    if not start:
        today = today_moscow()
        start = (today - timedelta(days=today.weekday())).isoformat()
    if not end:
        parsed_start = parse_plan_date(start)
        end = (parsed_start + timedelta(days=6)).isoformat() if parsed_start else start
    return start, end


def _split_week_publications(
    publications: object, week_start: str, week_end: str
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split the plan into the items shown in the week list vs. everything else.

    The list tab is scoped to the selected period [week_start, week_end]. Items
    dated outside that window (e.g. already-published posts from earlier weeks)
    stay in storage but are not shown in the editable list. Undated items stay in
    the list so a freshly added publication is never hidden."""
    items = publications if isinstance(publications, list) else []
    start = parse_plan_date(week_start)
    end = parse_plan_date(week_end)
    inside: list[dict[str, object]] = []
    outside: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = parse_plan_date(str(item.get("date", "")))
        if start and end and parsed and not (start <= parsed <= end):
            outside.append(item)
        else:
            inside.append(item)
    return inside, outside


def _published_posts_for_calendar(plan: dict[str, object]) -> list[dict[str, str]]:
    """Every post ever published, as simple dated cards for the calendar.

    Sourced from the Texts archive (which also absorbs plan items marked
    published), so history survives weekly plan regeneration and date refreshes."""
    repository = TextPostRepository()
    repository.sync_from_content_plan(plan)
    posts = []
    for post in repository.list_posts("archive"):
        summary = (post.text[:160].strip() if post.text.strip() else post.brief.strip())
        posts.append(
            {
                "date": post.publication_date,
                "platform": post.platform,
                "topic": post.title,
                "summary": summary,
            }
        )
    return posts


def _normalize_plan_date_value(value: str) -> str:
    parsed = parse_plan_date(value)
    return parsed.isoformat() if parsed else ""


def _format_week_range(start: str, end: str) -> str:
    parsed_start = parse_plan_date(start)
    parsed_end = parse_plan_date(end)
    if parsed_start and parsed_end:
        return f"{parsed_start.strftime('%d.%m.%Y')} - {parsed_end.strftime('%d.%m.%Y')}"
    return start or end


def _date_for_input(value: str) -> str:
    parsed = parse_plan_date(value)
    return parsed.isoformat() if parsed else ""


def _month_range(value: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        return "", ""
    year, month = (int(part) for part in value.split("-", 1))
    if month < 1 or month > 12:
        return "", ""
    return date(year, month, 1).isoformat(), date(year, month, monthrange(year, month)[1]).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _format_moscow_time(value: object) -> str:
    """Short human-readable Moscow time (dd.mm.yyyy HH:MM) from a stored ISO timestamp."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    moscow = parsed.astimezone(timezone(timedelta(hours=3)))
    return moscow.strftime("%d.%m.%Y %H:%M")


def _load_editorial_strategy() -> dict[str, object]:
    if not EDITORIAL_STRATEGY_PATH.exists():
        return _default_editorial_strategy()
    try:
        raw = json.loads(EDITORIAL_STRATEGY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_editorial_strategy()
    if not isinstance(raw, dict):
        return _default_editorial_strategy()
    strategy = _default_editorial_strategy()
    strategy.update({key: value for key, value in raw.items() if key != "weekly_template"})
    strategy["weekly_template"] = _normalize_strategy_entries(raw.get("weekly_template", []))
    strategy["rubric_library"] = RUBRIC_LIBRARY
    return strategy


def _save_editorial_strategy(strategy: dict[str, object]) -> None:
    EDITORIAL_STRATEGY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(strategy)
    payload["rubric_library"] = RUBRIC_LIBRARY
    payload["updated_at"] = _now_iso()
    EDITORIAL_STRATEGY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _default_editorial_strategy() -> dict[str, object]:
    return json.loads(json.dumps(DEFAULT_EDITORIAL_STRATEGY, ensure_ascii=False))


def _normalize_strategy_entries(entries: object) -> list[dict[str, object]]:
    source = entries if isinstance(entries, list) else []
    by_day = {
        str(item.get("day", "")): item
        for item in source
        if isinstance(item, dict)
    }
    normalized = []
    for default in DEFAULT_EDITORIAL_STRATEGY["weekly_template"]:
        item = by_day.get(str(default["day"]), {})
        active_value = item.get("active", default["active"]) if isinstance(item, dict) else default["active"]
        platform = _normalize_platform(str(item.get("platform", default["platform"])) if isinstance(item, dict) else str(default["platform"]))
        rubric = _normalize_rubric(str(item.get("rubric", item.get("pillar", default["rubric"])) if isinstance(item, dict) else str(default["rubric"])))
        publication_format = _normalize_strategy_format(
            str(item.get("format", default["format"])) if isinstance(item, dict) else str(default["format"]),
            platform,
            rubric,
        )
        normalized.append(
            {
                "day": str(default["day"]),
                "platform": platform,
                "rubric": rubric,
                "format": publication_format,
                "active": active_value in (True, "true", "1", "on", "yes", "active"),
                "note": str(item.get("note", default["note"])) if isinstance(item, dict) else str(default["note"]),
            }
        )
    return normalized


def _strategy_from_form(data: dict[str, list[str]]) -> dict[str, object]:
    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    entries = []
    for index, default in enumerate(DEFAULT_EDITORIAL_STRATEGY["weekly_template"]):
        day = value(f"strategy_{index}_day") or str(default["day"])
        platform = _normalize_platform(value(f"strategy_{index}_platform"))
        rubric = _normalize_rubric(value(f"strategy_{index}_rubric"))
        entries.append(
            {
                "day": day,
                "platform": platform,
                "rubric": rubric,
                "format": _normalize_strategy_format(value(f"strategy_{index}_format"), platform, rubric),
                "active": value(f"strategy_{index}_active") == "on",
                "note": value(f"strategy_{index}_note"),
            }
        )
    return {
        "updated_at": _now_iso(),
        "rubric_library": RUBRIC_LIBRARY,
        "weekly_template": _normalize_strategy_entries(entries),
    }


def _strategy_publications(strategy: dict[str, object], week_start: str, plan: dict[str, object]) -> list[dict[str, str]]:
    parsed_start = parse_plan_date(week_start) or today_moscow()
    week_end = str(plan.get("week_end", ""))
    parsed_end = parse_plan_date(week_end) or (parsed_start + timedelta(days=6))
    publications = []
    trend_topics = _trend_topics_for_plan()
    for index, entry in enumerate(_normalize_strategy_entries(strategy.get("weekly_template", []))):
        if not entry.get("active"):
            continue
        publication_day = _strategy_day_index(str(entry.get("day", "")))
        if publication_day is None:
            publication_date_obj = parsed_start + timedelta(days=index)
        else:
            days_until_publication = (publication_day - parsed_start.weekday()) % 7
            publication_date_obj = parsed_start + timedelta(days=days_until_publication)
        if publication_date_obj < parsed_start or publication_date_obj > parsed_end:
            continue
        publication_date = publication_date_obj.isoformat()
        rubric = _normalize_rubric(str(entry.get("rubric", "")))
        publication_format = _normalize_publication_format(str(entry.get("format", "")))
        platform = _normalize_platform(str(entry.get("platform", "")))
        trend = _best_trend_for_strategy(trend_topics, platform, rubric)
        evergreen = _evergreen_topic_for_strategy(platform, rubric)
        trend_title = _platform_publication_title(trend, platform) if trend else ""
        source_title = trend_title or evergreen
        publications.append(
            {
                "date": publication_date,
                "day": weekday_name_for_date(publication_date),
                "platform": platform,
                "topic": _editorial_topic_from_signal(source_title, platform, rubric),
                "goal": _localized_goal(platform, "Связать редакционную стратегию с самым сильным трендом недели."),
                "format": publication_format,
                "pillar": rubric,
                "rubric": rubric,
                "status": "draft",
                "summary": _localized_summary(platform, trend, evergreen),
                "note": _trend_selection_note(platform, rubric, trend, str(entry.get("note", ""))),
                "week_focus": str(plan.get("focus", "")),
                "strategy_locked": "true",
                "strategy_note": str(entry.get("note", "")),
                "used_trend": trend_title,
                "trend_score": str(trend.get("trend_score", "")) if trend else "",
                "brand_fit_score": str(trend.get("brand_fit_score", "")) if trend else "",
                "content_potential": str(trend.get("content_potential", "")) if trend else "",
                "repeat_risk": str(trend.get("repeat_risk", "")) if trend else "низкий",
                "why_ai_chose": _why_ai_chose_topic(platform, rubric, trend),
            }
        )
    return sorted(publications, key=lambda item: (item.get("date", ""), item.get("platform", "")))


def _strategy_day_index(day: str) -> int | None:
    try:
        return ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье").index(day)
    except ValueError:
        return None


def _trend_topics_for_plan() -> list[dict[str, object]]:
    if DailyBriefRequestHandler.trend_radar.is_stale():
        _refresh_trend_radar_now()
    topics = DailyBriefRequestHandler.trend_radar.get_cached().get("topics", [])
    if not isinstance(topics, list):
        return []
    return [item for item in topics if isinstance(item, dict)]


def _best_trend_for_strategy(topics: list[dict[str, object]], platform: str, rubric: str) -> dict[str, object] | None:
    candidates = []
    for topic in topics:
        formats = [str(item) for item in topic.get("best_formats", [])] if isinstance(topic.get("best_formats", []), list) else []
        rubrics = [str(item) for item in topic.get("best_rubrics", [])] if isinstance(topic.get("best_rubrics", []), list) else []
        if formats and platform not in formats:
            continue
        if rubrics and rubric not in rubrics:
            continue
        if str(topic.get("repeat_risk", "")) == "высокий":
            continue
        candidates.append(topic)
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("trend_score") or item.get("brand_fit_score") or 0))


def _platform_publication_title(trend: dict[str, object] | None, platform: str) -> str:
    if not trend:
        return ""
    ideas = trend.get("publication_ideas", {})
    if isinstance(ideas, dict):
        value = str(ideas.get(platform, "")).strip()
        if value:
            return value
    return str(trend.get("title", "")).strip()


def _evergreen_topic_for_strategy(platform: str, rubric: str) -> str:
    documents = DailyBriefRequestHandler.knowledge_base.list_documents()
    if documents:
        title = str(getattr(documents[0], "title", ""))
        if title:
            return title
    return "Customer Experience as an operational system" if platform == "LinkedIn" else "Customer Experience как операционная система"


def _english_rubric_prefix(rubric: str) -> str:
    return {
        "Аналитика": "Analysis",
        "Кейс": "Case",
        "Framework": "Framework",
        "Наблюдение": "Observation",
        "Разбор ошибки": "Mistake teardown",
        "Миф": "Myth",
        "Storytelling": "Story",
        "Разговорный пост": "Point of view",
        "Инструменты": "Tools",
        "Ответ на вопрос": "Answer",
    }.get(rubric, "Analysis")


def _localized_goal(platform: str, text: str) -> str:
    if platform == "LinkedIn":
        return "Connect the editorial strategy with the strongest weekly trend and show an executive operations point of view."
    return text


def _editorial_topic_from_signal(signal: str, platform: str, rubric: str) -> str:
    signal = signal.strip()
    if platform == "LinkedIn":
        safe_signal = signal if signal and not re.search(r"[А-Яа-я]", signal) else "This Market Signal"
        return f"{_english_rubric_prefix(rubric)}: {safe_signal}"
    if not signal:
        signal = "актуальный тренд"
    if rubric == "Кейс":
        return f"Как тренд «{signal}» проявляется в операционной реальности сервиса"
    if rubric == "Framework":
        return f"Фреймворк: как разложить тренд «{signal}» на процессы, роли и контроль"
    if rubric == "Миф":
        return f"Миф вокруг тренда «{signal}»: почему он не работает без операционной системы"
    return signal


def _localized_summary(platform: str, trend: dict[str, object] | None, evergreen: str) -> str:
    if trend:
        score = str(trend.get("trend_score", ""))
        if platform == "LinkedIn":
            return f"Use this trend signal with a trend score of {score}/10 as an editorial angle. Connect it to operations maturity, customer experience and service systems."
        return f"Использовать тренд с оценкой {score}/10 как смысловой сигнал: связать его с операционной зрелостью, клиентским опытом и сервисными системами."
    if platform == "LinkedIn":
        return f"No strong trend matched the strategy, so use an evergreen knowledge angle: {evergreen}."
    return f"Подходящий тренд не найден, поэтому используется evergreen-тема из памяти: {evergreen}."


def _trend_selection_note(platform: str, rubric: str, trend: dict[str, object] | None, strategy_note: str) -> str:
    if not trend:
        base = "Evergreen fallback: the radar did not find a strong enough match." if platform == "LinkedIn" else "Запасной вариант: радар не нашел достаточно сильное совпадение."
    elif platform == "LinkedIn":
        base = (
            f"The radar selected this signal: trend score {trend.get('trend_score', '')}/10, "
            f"brand fit {trend.get('brand_fit_score', '')}/10, content potential {trend.get('content_potential', '')}/10, "
            f"repeat risk {_repeat_risk_label(str(trend.get('repeat_risk', '')))}. Rubric: {rubric}."
        )
    else:
        base = (
            f"Радар выбрал этот сигнал: оценка тренда {trend.get('trend_score', '')}/10, "
            f"соответствие бренду {trend.get('brand_fit_score', '')}/10, контентный потенциал {trend.get('content_potential', '')}/10, "
            f"риск повтора {_repeat_risk_label(str(trend.get('repeat_risk', '')))}. Рубрика: {rubric}."
        )
    return " ".join(part for part in (base, strategy_note) if part)


def _why_ai_chose_topic(platform: str, rubric: str, trend: dict[str, object] | None) -> str:
    if not trend:
        return "Evergreen knowledge topic selected because no trend matched the strategy." if platform == "LinkedIn" else "Выбрана evergreen-тема из памяти, потому что подходящий тренд не найден."
    if platform == "LinkedIn":
        return f"Chosen because it fits {platform}, the {rubric} rubric, has brand fit {trend.get('brand_fit_score', '')}/10, content potential {trend.get('content_potential', '')}/10 and trend score {trend.get('trend_score', '')}/10."
    return f"Выбрано, потому что тема подходит площадке {platform}, рубрике «{rubric}», бренду автора и имеет оценку тренда {trend.get('trend_score', '')}/10."


def _merge_strategy_publication(base: dict[str, str], generated: dict[str, object]) -> dict[str, str]:
    merged = dict(base)
    merged["topic"] = str(generated.get("topic") or generated.get("title") or base.get("topic") or "Тема по стратегии").strip()
    merged["goal"] = str(generated.get("goal") or generated.get("purpose") or base.get("goal", "")).strip()
    summary_parts = [
        str(generated.get("angle", "")).strip(),
        str(generated.get("main_thought", "")).strip(),
        str(generated.get("summary") or generated.get("content") or generated.get("description") or "").strip(),
    ]
    merged["summary"] = "\n".join(part for part in summary_parts if part) or str(base.get("summary", "")).strip()
    generated_note = str(generated.get("note") or "").strip()
    merged["note"] = generated_note or str(base.get("note", "")).strip()
    merged["draft"] = str(generated.get("draft") or base.get("draft", "")).strip()
    merged["status"] = _normalize_publication_status(str(generated.get("status", base.get("status", "planned"))))
    return merged


def _match_trend_to_publication(publication: dict[str, object], trend_cache: dict[str, object]) -> dict[str, object] | None:
    """Deterministically pick the best trend for one plan cell (by platform, rubric, score).

    Gives the AI a concrete trend per cell instead of the whole undifferentiated list.
    """
    topics = trend_cache.get("topics", []) if isinstance(trend_cache, dict) else []
    if not isinstance(topics, list) or not topics:
        return None
    platform = _normalize_platform(str(publication.get("platform", "")))
    rubric = _normalize_rubric(str(publication.get("rubric") or publication.get("pillar") or publication.get("format") or ""))

    def score(topic: dict[str, object]) -> float:
        if not isinstance(topic, dict):
            return -100.0
        value = float(topic.get("trend_score", 0) or 0) + float(topic.get("brand_fit_score", 0) or 0) * 0.5
        best_formats = [str(item) for item in topic.get("best_formats", []) if isinstance(topic.get("best_formats"), list)]
        best_rubrics = [str(item) for item in topic.get("best_rubrics", []) if isinstance(topic.get("best_rubrics"), list)]
        if platform and platform in best_formats:
            value += 3.0
        if rubric and rubric in best_rubrics:
            value += 2.0
        if str(topic.get("recommendation", "")) == "не брать":
            value -= 5.0
        return value

    best = max((topic for topic in topics if isinstance(topic, dict)), key=score, default=None)
    if best is None or score(best) <= 0:
        return None
    ideas = best.get("publication_ideas", {})
    return {
        "title": str(best.get("title", "")),
        "essence": str(best.get("trend_essence", "")),
        "main_idea": str(best.get("main_idea", "")),
        "why_now": str(best.get("why_trend", "")),
        "platform_angle": str(ideas.get(platform, "")) if isinstance(ideas, dict) else "",
        "trend_score": best.get("trend_score", 0),
        "brand_fit_score": best.get("brand_fit_score", 0),
        "recommendation": str(best.get("recommendation", "")),
    }


def _content_plan_ai_context(target: dict[str, object] | None = None) -> dict[str, object]:
    context = DailyBriefRequestHandler.ai_context_engine.build(target or {}, include_local_sources=True)
    # editorial_strategy and lessons already come from AIContextEngine.build(); only add
    # what build() does not provide.
    context["rubric_library"] = _effective_rubric_rules()
    trend_cache = context.get("trend_radar", {})
    trend_cache = trend_cache if isinstance(trend_cache, dict) else {}
    if target:
        matched = _match_trend_to_publication(target, trend_cache)
        if matched:
            context["matched_trend"] = matched
    # Per-cell trend matches for the whole-week generator, so trend -> plan is wired in code.
    content_plan = context.get("content_plan", {})
    publications = content_plan.get("planned_publications", []) if isinstance(content_plan, dict) else []
    trend_matches = []
    if isinstance(publications, list):
        for item in publications:
            if not isinstance(item, dict):
                continue
            matched = _match_trend_to_publication(item, trend_cache)
            if matched:
                trend_matches.append(
                    {
                        "date": str(item.get("date", "")),
                        "platform": str(item.get("platform", "")),
                        "rubric": str(item.get("rubric", item.get("pillar", ""))),
                        "matched_trend": matched,
                    }
                )
    if trend_matches:
        context["trend_matches"] = trend_matches
    return context


def _save_content_plan_form(data: dict[str, list[str]]) -> str:
    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    action = value("plan_action")
    view = "calendar" if value("view") == "calendar" else "list"
    strategy = _strategy_from_form(data) if any(key.startswith("strategy_") for key in data) else _load_editorial_strategy()
    if action in {"save_strategy", "strategy_plan"}:
        _save_editorial_strategy(strategy)
    week_start = _normalize_plan_date_value(value("week_start")) or _content_plan_period(_load_content_plan_raw())[0]
    week_end = _normalize_plan_date_value(value("week_end"))
    if not week_end:
        parsed_start = parse_plan_date(week_start)
        week_end = (parsed_start + timedelta(days=6)).isoformat() if parsed_start else week_start
    # The list only shows the current week, so the form only carries this week's rows.
    # Keep every publication outside the window (published history, other weeks) so a
    # save never silently drops it.
    preserved_out_of_week = _split_week_publications(
        _load_content_plan_raw().get("planned_publications", []), week_start, week_end
    )[1]
    delete_index = _action_index(action, "delete_pub_")
    next_index = _action_index(action, "next_pub_")
    publish_index = _action_index(action, "publish_pub_")
    publications = []
    indexes = sorted(
        {
            int(match.group(1))
            for key in data
            for match in [re.match(r"pub_(\d+)_", key)]
            if match
        }
    )
    for index in indexes:
        if delete_index == index:
            continue
        topic = value(f"pub_{index}_topic")
        platform = _normalize_platform(value(f"pub_{index}_platform"))
        publication_date = _normalize_plan_date_value(value(f"pub_{index}_date"))
        if not (topic or platform or publication_date):
            continue
        status = _normalize_publication_status(value(f"pub_{index}_status"))
        if next_index == index:
            status = _next_publication_status(status)
        if publish_index == index:
            status = "published"
            # Mark it published today unless it already has a real (past) publish date.
            parsed_pub = parse_plan_date(publication_date)
            if not parsed_pub or parsed_pub > today_moscow():
                publication_date = today_moscow().isoformat()
        publication_rubric = _normalize_rubric(value(f"pub_{index}_pillar") or value(f"pub_{index}_format"))
        publication_format = _normalize_publication_format(value(f"pub_{index}_format"))
        publications.append(
            {
                "date": publication_date,
                "day": weekday_name_for_date(publication_date),
                "platform": platform,
                "topic": topic,
                "goal": value(f"pub_{index}_goal"),
                "format": publication_format,
                "pillar": publication_rubric,
                "rubric": publication_rubric,
                "status": status,
                "summary": value(f"pub_{index}_summary"),
                "note": value(f"pub_{index}_note"),
            }
        )
    if action == "save_focus":
        # Save only the strategic header (period dates + focus). Keep the stored
        # publications exactly as they are — the small button under «Фокус недели»
        # must not rewrite or clear the plan below it.
        stored = _load_content_plan_raw()
        stored_pubs = stored.get("planned_publications")
        publications = stored_pubs if isinstance(stored_pubs, list) else publications
    if action == "add_publication":
        new_pub_date = _normalize_plan_date_value(value("new_pub_date"))
        new_publication = {
            "date": new_pub_date,
            "day": weekday_name_for_date(new_pub_date),
            "platform": _normalize_platform(value("new_pub_platform")),
            "topic": value("new_pub_topic") or "Новая публикация",
            "goal": value("new_pub_goal"),
            "format": _normalize_publication_format(value("new_pub_format")),
            "pillar": _normalize_rubric(value("new_pub_pillar") or value("new_pub_format")),
            "rubric": _normalize_rubric(value("new_pub_pillar") or value("new_pub_format")),
            "status": "draft",
            "summary": value("new_pub_summary"),
            "note": value("new_pub_note"),
        }
        publications.append(new_publication)

    raw = {
        "week": _format_week_range(week_start, week_end),
        "week_start": week_start,
        "week_end": week_end,
        "focus": _clean_focus_value(value("focus")),
        "content_pillars": text_to_list(value("content_pillars")),
        "platform_targets": text_to_list(value("platform_targets")),
        "today_recommendation": value("today_recommendation"),
        "planned_publications": publications,
        "updated_at": _now_iso(),
        "last_action": "Сохранено вручную.",
    }
    if action in {"request_ai", "strategy_plan"}:
        raw["planned_publications"] = _strategy_publications(strategy, week_start, raw)
        raw = _generate_content_plan_with_ai(raw, strategy)
    else:
        generate_index = _action_index(action, "generate_pub_")
        if generate_index is not None and generate_index < len(publications):
            raw["planned_publications"][generate_index] = _generate_content_plan_publication_with_ai(raw, publications[generate_index])
            raw["updated_at"] = _now_iso()
            if raw["planned_publications"][generate_index].get("ai_error"):
                raw["last_action"] = f"AI не обновил публикацию #{generate_index + 1}."
            else:
                raw["last_action"] = f"Обновлена публикация #{generate_index + 1}."
        elif action == "approve":
            raw["last_action"] = "План утвержден."
        elif action == "add_publication":
            raw["last_action"] = "Публикация добавлена."
        elif action == "save_focus":
            raw["last_action"] = "Фокус и даты периода сохранены."
        elif action == "save_strategy":
            raw["last_action"] = "Редакционная стратегия сохранена."
        elif next_index is not None:
            raw["last_action"] = f"Публикация #{next_index + 1} переведена на следующий этап."
        elif publish_index is not None:
            raw["last_action"] = "Публикация отмечена опубликованной — она в архиве и календаре."
    # save_focus already keeps the full stored list; every other action rebuilds only
    # the week from the form, so re-attach the preserved out-of-week publications.
    if action != "save_focus" and preserved_out_of_week:
        raw["planned_publications"] = list(raw.get("planned_publications", [])) + preserved_out_of_week
    DEFAULT_CONTENT_PLAN_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if publish_index is not None:
        # Push the freshly published post into the archive so the calendar is instantly correct.
        TextPostRepository().sync_from_content_plan(raw)
    anchor = ""
    target_index = _action_index(action, "generate_pub_")
    if target_index is None:
        target_index = _action_index(action, "next_pub_")
    if target_index is None:
        target_index = publish_index
    if target_index is not None:
        anchor = f"#publication-{target_index}"
    return f"/content-plan?saved=1&status=updated&view={view}{anchor}"


def _save_author_strategy_form(data: dict[str, list[str]]) -> str:
    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    action = value("plan_action")
    strategy = _strategy_from_form(data)
    _save_editorial_strategy(strategy)
    if action == "strategy_plan":
        raw = _load_content_plan_raw()
        week_start, week_end = _content_plan_period(raw)
        raw["week"] = _format_week_range(week_start, week_end)
        raw["week_start"] = week_start
        raw["week_end"] = week_end
        raw["planned_publications"] = _strategy_publications(strategy, week_start, raw)
        raw = _generate_content_plan_with_ai(raw, strategy)
        raw["updated_at"] = _now_iso()
        if not raw.get("last_action"):
            raw["last_action"] = "План создан по редакционной стратегии."
        DEFAULT_CONTENT_PLAN_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return "/content-plan?saved=1&status=updated&view=list"
    return "/author-profile?tab=strategy&strategy_saved=1"


def _save_learning_settings_form(data: dict[str, list[str]], repository: AuthorBrainRepository | None = None) -> None:
    repository = repository or AuthorBrainRepository()

    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    platform_fit = {
        platform: value(f"platform_fit_{platform}")
        for platform in CONTENT_PLATFORMS
        if value(f"platform_fit_{platform}")
    }
    anti_repetition = {
        "recent_ideas": [],
        "overused_theme_candidates": text_to_list(value("anti_overused_themes")),
        "case_rotation": text_to_list(value("anti_case_rotation")),
        "rules": text_to_list(value("anti_rules")),
    }
    profile = repository.load_profile()
    if platform_fit:
        profile["platform_fit"] = platform_fit
    profile["anti_repetition"] = anti_repetition
    profile["updated_at"] = _now_iso()
    profile["status"] = "ready"
    controls = profile.get("manual_author_base", {})
    if not isinstance(controls, dict):
        controls = {}
    controls.update({"platform_fit": True, "anti_repetition": True, "updated_at": profile["updated_at"]})
    profile["manual_author_base"] = controls
    repository.save_profile(profile)


def _save_author_base_form(data: dict[str, list[str]], repository: AuthorBrainRepository | None = None) -> None:
    repository = repository or AuthorBrainRepository()

    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    action = value("author_base_action")
    delete_theme = _action_index(action, "delete_theme_")
    themes: list[dict[str, object]] = []
    theme_indexes = sorted(
        {
            int(match.group(1))
            for key in data
            for match in [re.match(r"theme_(\d+)_", key)]
            if match
        }
    )
    for index in theme_indexes:
        if delete_theme == index:
            continue
        name = value(f"theme_{index}_name")
        if not name:
            continue
        themes.append(
            {
                "name": name,
                "score": _int_value(value(f"theme_{index}_score"), 80),
                "evidence": text_to_list(value(f"theme_{index}_evidence")),
                "risk": value(f"theme_{index}_risk"),
                "source": "manual",
            }
        )
    new_theme_name = value("new_theme_name")
    if new_theme_name:
        themes.append(
            {
                "name": new_theme_name,
                "score": _int_value(value("new_theme_score"), 80),
                "evidence": text_to_list(value("new_theme_evidence")),
                "risk": value("new_theme_risk"),
                "source": "manual",
            }
        )

    profile = repository.load_profile()
    profile["main_themes"] = themes
    profile["theme_weight_rule"] = _theme_weight_rule()
    profile["updated_at"] = _now_iso()
    profile["status"] = "ready"
    controls = profile.get("manual_author_base", {})
    if not isinstance(controls, dict):
        controls = {}
    controls.update({"main_themes": True, "updated_at": profile["updated_at"]})
    profile["manual_author_base"] = controls
    repository.save_profile(profile)


def _save_key_ideas_form(data: dict[str, list[str]], repository: AuthorBrainRepository | None = None) -> None:
    repository = repository or AuthorBrainRepository()

    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    action = value("key_ideas_action")
    delete_idea = _action_index(action, "delete_idea_")
    ideas: list[dict[str, object]] = []
    idea_indexes = sorted(
        {
            int(match.group(1))
            for key in data
            for match in [re.match(r"idea_(\d+)_", key)]
            if match
        }
    )
    for index in idea_indexes:
        if delete_idea == index:
            continue
        idea = value(f"idea_{index}_text")
        if not idea:
            continue
        belief = value(f"idea_{index}_belief") or idea
        ideas.append(
            {
                "idea": idea,
                "belief": belief,
                "repeat_risk": _normalize_repeat_risk(value(f"idea_{index}_repeat_risk")),
                "source": "manual",
            }
        )
    new_idea = value("new_idea_text")
    if new_idea:
        ideas.append(
            {
                "idea": new_idea,
                "belief": value("new_idea_belief") or new_idea,
                "repeat_risk": _normalize_repeat_risk(value("new_idea_repeat_risk")),
                "source": "manual",
            }
        )
    profile = repository.load_profile()
    profile["key_ideas"] = ideas
    profile["updated_at"] = _now_iso()
    profile["status"] = "ready"
    controls = profile.get("manual_author_base", {})
    if not isinstance(controls, dict):
        controls = {}
    controls.update({"key_ideas": True, "updated_at": profile["updated_at"]})
    profile["manual_author_base"] = controls
    repository.save_profile(profile)


def _theme_weight_rule() -> str:
    return THEME_WEIGHT_RULE


def _int_value(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_repeat_risk(value: str) -> str:
    return value if value in {"low", "medium", "high"} else "medium"


def _add_trend_to_content_plan(topic: dict[str, object]) -> None:
    raw = _load_content_plan_raw()
    publications = raw.get("planned_publications", [])
    if not isinstance(publications, list):
        publications = []
    formats = topic.get("best_formats", [])
    platform = _normalize_platform(str(formats[0]) if isinstance(formats, list) and formats else "LinkedIn")
    title = _platform_publication_title(topic, platform)
    if not title:
        return
    if any(isinstance(item, dict) and str(item.get("topic", "")).strip() == title for item in publications):
        return
    rubrics = topic.get("best_rubrics", [])
    rubric = _normalize_rubric(str(rubrics[0])) if isinstance(rubrics, list) and rubrics else "Наблюдение"
    localized_title = _editorial_topic_from_signal(title, platform, rubric)
    today = today_moscow().strftime("%d.%m.%Y")
    publications.append(
        {
            "date": today,
            "day": weekday_name_for_date(today),
            "platform": platform,
            "topic": localized_title,
            "goal": _localized_goal(platform, "Проверить тренд как потенциально сильную публикацию дня."),
            "format": "пост",
            "pillar": rubric,
            "rubric": rubric,
            "status": "draft",
            "summary": _localized_summary(platform, topic, ""),
            "note": _trend_selection_note(platform, rubric, topic, str(topic.get("ai_reason", ""))),
            "used_trend": title,
            "trend_score": str(topic.get("trend_score", "")),
            "brand_fit_score": str(topic.get("brand_fit_score", "")),
            "content_potential": str(topic.get("content_potential", "")),
            "repeat_risk": str(topic.get("repeat_risk", "")),
            "why_ai_chose": _why_ai_chose_topic(platform, rubric, topic),
        }
    )
    raw["planned_publications"] = publications
    DEFAULT_CONTENT_PLAN_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _add_idea_to_content_plan(idea: Idea) -> bool:
    """Promote a vault idea into the content plan as a new 'idea' publication.
    Returns False if the idea is empty or its topic already exists in the plan."""
    raw = _load_content_plan_raw()
    publications = raw.get("planned_publications", [])
    if not isinstance(publications, list):
        publications = []
    title = idea.title.strip()
    if not title:
        return False
    if any(isinstance(item, dict) and str(item.get("topic", "")).strip() == title for item in publications):
        return False
    platform = _normalize_platform(idea.platforms[0]) if idea.platforms else "LinkedIn"
    rubric = "Наблюдение"
    today = today_moscow().isoformat()
    publications.append(
        {
            "date": today,
            "day": weekday_name_for_date(today),
            "platform": platform,
            "topic": title,
            "goal": _localized_goal(platform, "Развить идею из хранилища в полноценную публикацию."),
            "format": "пост",
            "pillar": rubric,
            "rubric": rubric,
            "status": "draft",
            "summary": idea.description.strip(),
            "note": f"Идея добавлена в план из хранилища идей (источник: {idea.source}).",
        }
    )
    raw["planned_publications"] = publications
    DEFAULT_CONTENT_PLAN_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _action_index(action: str, prefix: str) -> int | None:
    if not action.startswith(prefix):
        return None
    value = action.removeprefix(prefix)
    return int(value) if value.isdigit() else None


def _generate_content_plan_publication_with_ai(plan: dict[str, object], publication: dict[str, str]) -> dict[str, str]:
    updated = dict(publication)
    try:
        platform = _normalize_platform(str(publication.get("platform", "")))
        publication_format = _normalize_publication_format(str(publication.get("format") or publication.get("pillar") or ""))
        rubric = _normalize_rubric(str(publication.get("rubric") or publication.get("pillar") or publication.get("format") or ""))
        language = _language_for_platform(platform)
        context = _content_plan_ai_context(publication)
        previous = _publication_signature(publication)
        # Other cells of the plan, so the new topic does not repeat a sibling publication.
        own_signature = _publication_signature(publication)
        siblings = [
            item
            for item in plan.get("planned_publications", [])
            if isinstance(item, dict) and _publication_signature(item) and _publication_signature(item) != own_signature
        ]
        sibling_topics = "; ".join(str(item.get("topic", "")) for item in siblings if str(item.get("topic", "")).strip())
        response: dict[str, object] = {}
        best_language_ok: dict[str, object] | None = None
        for attempt in range(3):
            raw_response = _complete_json_with_retry(
                AIGateway(),
                system_prompt=(
                    "Ты AI Chief Content Officer. Создай совершенно новую идею для одной публикации. "
                    "Не переписывай существующую тему и не делай рерайт. Ответь строго JSON."
                ),
                user_prompt=(
                    "Строгая иерархия смысла:\n"
                    f"1. Фокус недели (фиксирован): {plan.get('focus', '')}\n"
                    "2. Из фокуса недели нужно придумать новую публикацию.\n\n"
                    "Сохрани только эти поля публикации:\n"
                    f"- date: {publication.get('date', '')}\n"
                    f"- platform: {platform}\n"
                    f"- goal: {publication.get('goal', '')}\n"
                    f"- rubric: {rubric}\n"
                    f"- format: {publication_format}\n"
                    f"- language: {language}\n\n"
                    f"Жесткое правило языка: {_language_policy_for_platform(platform)}\n"
                    f"ВАЖНО: тема (topic) должна быть на языке площадки ({language}). "
                    "Даже если материал в Knowledge, Trend Radar или кейс назван по-английски (например «SOP-as-service-care»), "
                    "нельзя выносить английское название в тему для русской площадки. Извлеки смысл и сформулируй тему по-русски. "
                    "Английские слова допустимы только как отдельные термины (SOP, CX), но не как английское предложение-заголовок.\n"
                    "Не делай дословный перевод тренда или источника. Извлеки смысл, свяжи с Author Brain, Editorial Strategy, Memory и сформулируй новую редакционную тему под площадку.\n\n"
                    "Для этой ячейки уже подобран подходящий тренд в поле context.matched_trend (если он есть). "
                    "Возьми его за отправную точку: используй его смысл (essence, main_idea, platform_angle), но переформулируй под площадку и на её языке. "
                    "Если matched_trend пустой — используй evergreen-тему из Knowledge.\n\n"
                    f"Правила рубрики: {_publication_format_instruction(rubric)}\n\n"
                    "Сначала внутри себя сформируй 3 варианта идеи. Проверь каждый по Author Brain, Editorial Strategy, площадке, "
                    "новизне, риску повтора, наличию сильного кейса/аргумента и актуальности Trend Radar. "
                    "Выбери лучший вариант и объясни выбор в note. "
                    "Заново придумай: topic, angle, main_thought, summary, note. "
                    "Тема должна быть заметно другой, не рерайтом старой.\n\n"
                    f"Предыдущий вариант, который нельзя повторять: {previous}\n"
                    f"Темы других публикаций плана, которые тоже нельзя повторять: {sibling_topics or 'нет'}\n"
                    f"Попытка: {attempt + 1}. Seed: {_now_iso()}\n"
                    f"Контекст автора и продукта: {json.dumps(context, ensure_ascii=False)}\n\n"
                    "Верни JSON с полями: topic, angle, main_thought, goal, summary, status, note, choice_reason, quality_scores. "
                    "Не меняй date, platform, rubric и format."
                ),
                action="content_plan_publication",
            )
            response = _extract_publication_response(raw_response)
            language_ok = _text_matches_platform(str(response.get("topic", "")), platform) and _text_matches_platform(str(response.get("summary", "")), platform)
            not_duplicate = not _publication_too_similar(publication, response) and not any(_publication_too_similar(sibling, response) for sibling in siblings)
            if language_ok and best_language_ok is None:
                best_language_ok = response
            if language_ok and not_duplicate:
                break
        # Prefer a response that respects the platform language, even if it took the last attempt.
        response = best_language_ok or response
        updated["topic"] = str(response.get("topic") or response.get("title") or updated.get("topic") or "Тема для публикации").strip()
        updated["goal"] = str(response.get("goal") or response.get("purpose") or updated.get("goal", "")).strip()
        summary_parts = [
            str(response.get("angle", "")).strip(),
            str(response.get("main_thought", "")).strip(),
            str(response.get("summary") or response.get("content") or response.get("description") or "").strip(),
        ]
        updated["summary"] = "\n".join(part for part in summary_parts if part) or str(updated.get("summary", "")).strip()
        updated["format"] = publication_format
        updated["pillar"] = rubric
        updated["rubric"] = rubric
        updated["status"] = "draft"
        updated["note"] = str(response.get("note") or updated.get("note", "")).strip()
        updated["date"] = _normalize_plan_date_value(str(updated.get("date", "")))
        updated["day"] = weekday_name_for_date(str(updated.get("date", "")))
        updated["week_focus"] = str(plan.get("focus", ""))
        updated["updated_at"] = _now_iso()
        updated.pop("ai_error", None)
    except AIGatewayError as exc:
        _save_ai_action_error("content_plan_publication", exc)
        updated["ai_error"] = str(exc)
    except Exception as exc:
        _save_ai_action_error("content_plan_publication", exc)
        updated["ai_error"] = f"Не удалось сгенерировать публикацию: {exc}"
    return updated


def _generate_content_plan_with_ai(plan: dict[str, object], strategy: dict[str, object] | None = None) -> dict[str, object]:
    updated = dict(plan)
    try:
        strategy = strategy or _load_editorial_strategy()
        context = _content_plan_ai_context()
        locked_publications = [
            item
            for item in plan.get("planned_publications", [])
            if isinstance(item, dict)
        ]
        previous_publications = [
            {
                "topic": item.get("topic", ""),
                "summary": item.get("summary", ""),
                "platform": item.get("platform", ""),
                "rubric": item.get("rubric", item.get("pillar", "")),
            }
            for item in locked_publications
        ]
        response: dict[str, object] = {}
        for attempt in range(2):
            raw_response = _complete_json_with_retry(
                AIGateway(),
                system_prompt=(
                    "Ты AI Chief Content Officer. Заполни недельный контент-план строго по редакционной стратегии. "
                    "Не меняй день, дату, площадку, рубрику и формат публикации. Ответь строго JSON."
                ),
                user_prompt=(
                    "Фокус недели задан пользователем и ЗАФИКСИРОВАН — его нельзя менять. "
                    "Все темы недели должны прямо раскрывать фокус недели.\n\n"
                    "Строгая иерархия:\n"
                    "1. Редакционная стратегия\n"
                    "2. Недельный шаблон\n"
                    f"3. Фокус недели (фиксирован, раскрывать в каждой теме): {plan.get('focus', '')}\n"
                    "4. Author Brain\n"
                    "5. Trend Radar\n"
                    "6. Контент-план\n\n"
                    f"Период: {plan.get('week_start', '')} - {plan.get('week_end', '')}\n"
                    f"Опорные направления: {plan.get('content_pillars', [])}\n\n"
                    "Жестко зафиксированный недельный шаблон. Его нельзя менять:\n"
                    f"{json.dumps(locked_publications, ensure_ascii=False)}\n\n"
                    "Жесткое правило языка: язык определяется только platform. LinkedIn — только английский. Telegram, VC и Сетка — только русский. "
                    "Для русских площадок тема (topic) тоже должна быть на русском: даже если материал в Knowledge или кейс назван по-английски (например «SOP-as-service-care»), нельзя выносить английское название в тему — извлеки смысл и сформулируй по-русски. "
                    "Нельзя копировать или дословно переводить заголовок тренда; нужно извлечь смысл и создать новую редакционную тему под площадку, рубрику, Author Brain и Writing DNA.\n\n"
                    "Алгоритм выбора для каждого дня: Editorial Strategy -> Trend Radar -> Author Brain -> Knowledge -> Memory -> Writing DNA -> Anti-Repetition -> AI Context -> Content Plan. "
                    "Для каждой ячейки в context.trend_matches уже подобран конкретный тренд (matched_trend) по её дате, площадке и рубрике. "
                    "Сопоставь ячейку с её matched_trend по date/platform и используй его смысл (essence, main_idea, platform_angle) как отправную точку, переформулировав под площадку и её язык. "
                    "Если для ячейки нет matched_trend — используй evergreen Knowledge fallback.\n\n"
                    "Можно генерировать только topic, angle, goal, main_thought, summary, note и draft. "
                    "date, day, platform, rubric, pillar и format должны остаться как в шаблоне.\n\n"
                    "Алгоритм качества: для каждой ячейки сформируй 2-3 варианта идеи; проверь Author Brain, Editorial Strategy, "
                    "platform fit, новизну, риск повтора, сильный кейс/аргумент и актуальность тренда; выбери лучший и объясни выбор в note. "
                    "Для draft оцени strategy_fit, author_voice_fit, originality, practical_value, headline_strength, first_paragraph_strength, platform_fit.\n\n"
                    "Предыдущие публикации запрещено использовать как основу; их нужно только избегать:\n"
                    f"{json.dumps(previous_publications, ensure_ascii=False)}\n\n"
                    "Каждый повторный запуск должен давать другой план: другие темы, идеи, углы и содержание.\n"
                    f"Попытка: {attempt + 1}. Seed: {_now_iso()}\n"
                    f"Редакционная стратегия и правила рубрик: {json.dumps(strategy, ensure_ascii=False)}\n"
                    f"Контекст автора, Knowledge, Trend Radar и Lessons: {json.dumps(context, ensure_ascii=False)}\n\n"
                    "Верни JSON с полем planned_publications (фокус недели менять нельзя). "
                    "У каждой публикации верни только: topic, angle, goal, main_thought, summary, status, note, draft, choice_reason, quality_scores. "
                    "Для LinkedIn генерируй тему, цель, summary и note на английском языке. Для остальных площадок — на русском. "
                    "Если идея похожа на предыдущую, предложи другой угол."
                ),
                action="content_plan_full",
            )
            response = _extract_plan_response(raw_response)
            if attempt == 1 or not _plan_too_similar(previous_publications, response):
                break
        # The user's strategic inputs (focus, month focus, period dates, pillars) are the
        # intent that GUIDES generation — never overwrite them with the AI's echo. The AI
        # only fills in the publications (topics/summaries/drafts).
        publications = response.get("planned_publications") or response.get("publications") or response.get("plan")
        if isinstance(publications, list) and publications:
            merged = []
            generated_items = [item for item in publications if isinstance(item, dict)]
            for index, base in enumerate(locked_publications):
                generated = generated_items[index] if index < len(generated_items) else {}
                merged.append(_merge_strategy_publication(_normalize_plan_publication(base), generated))
            updated["planned_publications"] = merged
        else:
            updated["planned_publications"] = [_normalize_plan_publication(item) for item in locked_publications]
        week_start, week_end = _content_plan_period(updated)
        updated["week_start"] = week_start
        updated["week_end"] = week_end
        updated["week"] = _format_week_range(week_start, week_end)
        for publication in updated.get("planned_publications", []):
            if isinstance(publication, dict):
                publication["week_focus"] = str(updated.get("focus", ""))
        duplicate_count = _flag_duplicate_cells(updated)
        updated["updated_at"] = _now_iso()
        updated["last_action"] = (
            f"Создан план по редакционной стратегии. Похожих публикаций: {duplicate_count} — отмечены, их можно перегенерировать."
            if duplicate_count
            else "Создан план по редакционной стратегии."
        )
        updated.pop("ai_error", None)
    except AIGatewayError as exc:
        _save_ai_action_error("content_plan_full", exc)
        updated["ai_error"] = str(exc)
        updated["updated_at"] = _now_iso()
        updated["last_action"] = "AI не обновил план."
    except Exception as exc:
        _save_ai_action_error("content_plan_full", exc)
        updated["ai_error"] = f"Не удалось сгенерировать контент-план: {exc}"
        updated["updated_at"] = _now_iso()
        updated["last_action"] = "AI не обновил план."
    return updated


def _extract_publication_response(response: dict[str, object]) -> dict[str, object]:
    for key in ("publication", "post", "item", "result"):
        value = response.get(key)
        if isinstance(value, dict):
            return value
    return response


def _extract_plan_response(response: dict[str, object]) -> dict[str, object]:
    for key in ("content_plan", "plan", "week_plan", "result"):
        value = response.get(key)
        if isinstance(value, dict):
            return value
    return response


def _publication_signature(item: dict[str, object]) -> str:
    return " ".join(
        str(item.get(field, ""))
        for field in ("topic", "summary", "note")
    ).strip()


def _publication_too_similar(previous: dict[str, object], response: dict[str, object]) -> bool:
    old_text = _publication_signature(previous)
    new_text = " ".join(
        str(response.get(field, ""))
        for field in ("topic", "title", "angle", "main_thought", "summary", "content", "description", "note")
    )
    return _text_similarity(old_text, new_text) >= 0.58


def _flag_duplicate_cells(plan: dict[str, object], threshold: float = 0.58) -> int:
    """Deterministically mark plan cells that are too similar to an earlier cell.

    No extra AI calls: it just detects repeats and sets repeat_warning so the UI
    can show them and the user can regenerate the affected cell (which then avoids siblings).
    """
    publications = plan.get("planned_publications", [])
    if not isinstance(publications, list):
        return 0
    flagged = 0
    for index, item in enumerate(publications):
        if not isinstance(item, dict):
            continue
        item.pop("repeat_warning", None)
        signature = _publication_signature(item)
        if not signature:
            continue
        for earlier in publications[:index]:
            if not isinstance(earlier, dict):
                continue
            if _text_similarity(signature, _publication_signature(earlier)) >= threshold:
                item["repeat_warning"] = f"Похожа на публикацию «{str(earlier.get('topic', '')).strip()}» — стоит перегенерировать."
                flagged += 1
                break
    return flagged


def _plan_too_similar(previous_publications: list[dict[str, object]], response: dict[str, object]) -> bool:
    publications = response.get("planned_publications") or response.get("publications") or response.get("plan")
    if not isinstance(publications, list) or not publications:
        return False
    old_text = " ".join(_publication_signature(item) for item in previous_publications)
    new_text = " ".join(
        _publication_signature(item)
        for item in publications
        if isinstance(item, dict)
    )
    return _text_similarity(old_text, new_text) >= 0.48


def _text_similarity(left: str, right: str) -> float:
    left_tokens = set(_similarity_tokens(left))
    right_tokens = set(_similarity_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _similarity_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE)
        if len(token) > 3
    ]


def _normalize_plan_publication(item: dict[str, object]) -> dict[str, str]:
    publication_date = _normalize_plan_date_value(str(item.get("date", "")).strip())
    platform = _normalize_platform(str(item.get("platform", "")).strip())
    rubric = _normalize_rubric(str(item.get("rubric") or item.get("pillar") or item.get("format") or ""))
    publication_format = _normalize_strategy_format(str(item.get("format") or ""), platform, rubric)
    return {
        "date": publication_date,
        "day": weekday_name_for_date(publication_date),
        "platform": platform,
        "topic": str(item.get("topic", "")).strip() or "Тема для публикации",
        "goal": str(item.get("goal", "")).strip(),
        "format": publication_format,
        "pillar": rubric,
        "rubric": rubric,
        "status": _normalize_publication_status(str(item.get("status", "planned")).strip() or "planned"),
        "summary": str(item.get("summary", "")).strip(),
        "note": str(item.get("note", "")).strip(),
        "draft": str(item.get("draft", "")).strip(),
        "strategy_locked": str(item.get("strategy_locked", "")).strip(),
        "strategy_note": str(item.get("strategy_note", "")).strip(),
        "used_trend": str(item.get("used_trend", "")).strip(),
        "trend_score": str(item.get("trend_score", "")).strip(),
        "brand_fit_score": str(item.get("brand_fit_score", "")).strip(),
        "content_potential": str(item.get("content_potential", "")).strip(),
        "repeat_risk": str(item.get("repeat_risk", "")).strip(),
        "why_ai_chose": str(item.get("why_ai_chose", "")).strip(),
    }


def render_knowledge(
    documents: list[object],
    cases: list[object] | None = None,
    ideas: list[object] | None = None,
    notes: list[object] | None = None,
    uploaded: bool = False,
    analysis: str = "",
    upload_error: str = "",
    deleted: bool = False,
    case_saved: bool = False,
    case_deleted: bool = False,
    note_saved: bool = False,
    note_deleted: bool = False,
    idea_saved: bool = False,
    section: str = "documents",
) -> str:
    notices = []
    if uploaded:
        notices.append("Документ добавлен в память.")
    if analysis == "done":
        notices.append("Анализ завершен.")
    if analysis == "error":
        notices.append("Не удалось разобрать документ автоматически — сам файл сохранён, попробуйте анализ ещё раз позже.")
    if upload_error:
        notices.append(upload_error)
    if deleted:
        notices.append("Документ удален из памяти.")
    if case_saved:
        notices.append("Кейс сохранен.")
    if case_deleted:
        notices.append("Кейс удален.")
    if note_saved:
        notices.append("Запись сохранена в память.")
    if note_deleted:
        notices.append("Запись удалена.")
    if idea_saved:
        notices.append("Идея сохранена в память.")
    notice_html = "".join(
        f"<div class=\"notice{' error-note' if item == upload_error and upload_error else ''}\">{escape(item)}</div>"
        for item in notices
    )
    cases = cases if cases is not None else DailyBriefRequestHandler.knowledge_base.list_cases()
    ideas = ideas if ideas is not None else DailyBriefRequestHandler.idea_vault.list_ideas()
    notes = notes if notes is not None else DailyBriefRequestHandler.memory_notes.list_notes()
    section = _knowledge_section(section)
    section_html = _knowledge_section_content(section, documents, cases, ideas, notes)
    content = f"""
    {notice_html}
    <nav class="memory-tabs">
      {_memory_tab("documents", "Документы", section)}
      {_memory_tab("cases", "Кейсы", section)}
      {_memory_tab("ideas", "Идеи", section)}
      {_memory_tab("observations", "Наблюдения", section)}
      {_memory_tab("principles", "Принципы", section)}
      {_memory_tab("stories", "Истории", section)}
    </nav>
    {section_html}
"""
    return _page_shell(
        title="Память",
        eyebrow="долгосрочная память",
        heading="Память",
        hint="Документы, кейсы, идеи, наблюдения, принципы и истории — материал, из которого учится AI.",
        active="knowledge",
        content=content,
    )


def render_how_it_works() -> str:
    stages = [
        ("🟢", "Знания", "Память", "/knowledge", "Документы и кейсы, которые вы загружаете.", "Питают Граф знаний и Author Brain."),
        ("⏳", "Входящие памяти", "Обучение", "/learning", "Новое знание сначала ждёт подтверждения.", "Влияет на AI только после того, как вы нажмёте «Принять»."),
        ("⚙️", "Граф знаний", "", "", "Связи между темами, компаниями и кейсами.", "Строится автоматически из принятой памяти."),
        ("🟢 ✅", "Правила бота", "Правила бота", "/bot-rules", "Правила мышления, запрещённые начала, правила площадок, режимы, анти-повтор, вес тем.", "Единый источник для AI. Есть жёсткие проверки: язык, режимы, площадки, повторы."),
        ("🟢", "ДНК письма", "Профиль автора → ДНК", "/author-profile?tab=dna", "Как автор пишет: тон, структура, лексика.", "Питает Author Brain."),
        ("⚙️", "Author Brain", "", "", "Модель мышления автора: темы, кейсы, идеи.", "Собирается из Знаний, ДНК письма, Уроков и Правил бота."),
        ("🟢 ⏳", "Уроки (обучение)", "Обучение", "/learning", "Правила, выученные из ваших правок и решений.", "Влияют на AI только принятые уроки."),
        ("🟢 ✅", "Стратегия · Радар трендов · Контент-план", "Контент-план", "/content-plan", "Что и когда публиковать.", "Тренды подбираются к ячейкам плана в коде; повторы проверяются автоматически."),
        ("⚙️ ✅", "Thinking Engine", "", "", "Выбирает режим мышления и угол публикации.", "Выбирает только из ваших режимов (Правила бота)."),
        ("⚙️ ✅", "Сборка подсказки → AI", "", "", "Всё, что выше, собирается в одну подсказку и уходит в AI.", "Черновик проверяется на язык и запрещённые начала."),
    ]
    cards = []
    for icon, title, where_label, where_href, what, feeds in stages:
        where = f'<a href="{escape(where_href)}">{escape(where_label)}</a>' if where_href else '<span class="hw-auto">считается автоматически</span>'
        cards.append(
            f"""
      <div class="hw-stage">
        <div class="hw-icon">{icon}</div>
        <div class="hw-body">
          <div class="hw-head"><h3>{escape(title)}</h3><span class="hw-where">{where}</span></div>
          <p class="hw-what">{escape(what)}</p>
          <p class="hw-feeds">{escape(feeds)}</p>
        </div>
      </div>"""
        )
    flow = '<div class="hw-arrow">↓</div>'.join(cards)
    content = f"""
    <section class="block">
      <p>Путь от ваших знаний и правил до готовой публикации. Каждый блок показывает, что это, где редактируется и на что влияет.</p>
      <div class="hw-legend">
        <span>🟢 редактируете вы</span>
        <span>⚙️ считается автоматически</span>
        <span>✅ есть жёсткая проверка в коде</span>
        <span>⏳ ручной шлюз — влияет на AI после подтверждения</span>
      </div>
      <div class="hw-flow">{flow}</div>
    </section>
"""
    return _page_shell(
        title="Как это связано",
        eyebrow="карта системы",
        heading="Как это связано",
        hint="",
        active="how",
        content=content,
    )


def _bot_rules_form_to_raw(data: dict[str, list[str]]) -> dict[str, object]:
    def first(key: str) -> str:
        return data.get(key, [""])[0]

    platform_rules: dict[str, str] = {}
    rubric_rules: dict[str, list[str]] = {}
    for key, values in data.items():
        if key.startswith("platform__"):
            platform_rules[key[len("platform__"):]] = values[0] if values else ""
        elif key.startswith("rubric__"):
            steps = [line.strip() for line in (values[0] if values else "").splitlines() if line.strip()]
            rubric_rules[key[len("rubric__"):]] = steps
    if "thinking_mode_item" in data:
        thinking_modes = [item.strip() for item in data.get("thinking_mode_item", []) if item.strip()]
    else:
        # The editing UI was removed; keep whatever modes are already stored so a save doesn't reset them.
        from .bot_rules import load_bot_rules

        stored = load_bot_rules().get("thinking_modes", [])
        thinking_modes = [str(item) for item in stored] if isinstance(stored, list) else []
    return {
        "thinking_rules": first("thinking_rules"),
        "forbidden_openings": first("forbidden_openings"),
        "anti_repeat_rules": first("anti_repeat_rules"),
        "theme_weight_rule": first("theme_weight_rule"),
        "thinking_modes": thinking_modes,
        "platform_rules": platform_rules,
        "rubric_rules": rubric_rules,
    }


def render_bot_rules(rules: dict[str, object], saved: bool = False) -> str:
    def _lines(key: str) -> str:
        value = rules.get(key, [])
        items = value if isinstance(value, list) else []
        return "\n".join(str(item) for item in items)

    platform_rules = rules.get("platform_rules", {})
    if not isinstance(platform_rules, dict):
        platform_rules = {}
    platform_blocks = "".join(
        _textarea(f"platform__{platform}", f"Площадка: {platform}", text)
        for platform, text in platform_rules.items()
    )
    rubric_rules = rules.get("rubric_rules", {})
    if not isinstance(rubric_rules, dict):
        rubric_rules = {}
    rubric_blocks = "".join(
        _textarea(f"rubric__{rubric}", f"Рубрика: {rubric}", "\n".join(str(step) for step in (steps if isinstance(steps, list) else [])))
        for rubric, steps in rubric_rules.items()
    )
    notice = '<div class="notice ok">Правила сохранены. AI будет использовать их при следующей генерации.</div>' if saved else ""
    content = f"""
    {notice}
    <section class="block">
      <p>Это внутренние правила, по которым AI думает и пишет от вашего имени. Раньше они были спрятаны в коде — теперь их можно менять здесь. Каждое правило пишите с новой строки. Пустое поле вернётся к значению по умолчанию.</p>
      <form method="post" action="/bot-rules" class="stack-form">
        <div class="section-title"><div><p class="eyebrow">как думает автор</p><h2>Правила мышления</h2></div></div>
        {_textarea("thinking_rules", "По одному правилу на строку", _lines("thinking_rules"))}

        <div class="section-title"><div><p class="eyebrow">чего избегать</p><h2>Запрещённые начала текста</h2></div></div>
        {_textarea("forbidden_openings", "Фразы, с которых нельзя начинать пост (по одной на строку)", _lines("forbidden_openings"))}

        <div class="section-title"><div><p class="eyebrow">площадки</p><h2>Правила площадок</h2></div></div>
        {platform_blocks}

        <div class="section-title"><div><p class="eyebrow">против повторов</p><h2>Правила против повторов</h2></div></div>
        {_textarea("anti_repeat_rules", "По одному правилу на строку", _lines("anti_repeat_rules"))}

        <div class="section-title"><div><p class="eyebrow">рубрики</p><h2>Правила рубрик</h2></div></div>
        <p class="mode-hint">Из каких шагов состоит публикация каждой рубрики. Один шаг — с новой строки. Пустое поле вернёт значение по умолчанию.</p>
        {rubric_blocks}

        <div class="section-title"><div><p class="eyebrow">приоритет тем</p><h2>Правило веса главных тем</h2></div></div>
        {_textarea("theme_weight_rule", "Как вес темы влияет на приоритет", str(rules.get("theme_weight_rule", "")))}

        <div class="form-actions"><button type="submit">Сохранить правила</button></div>
      </form>
    </section>
"""
    return _page_shell(
        title="Правила бота",
        eyebrow="настройки поведения",
        heading="Правила бота",
        hint="",
        active="bot-rules",
        content=content,
    )


def render_knowledge_document(document: object) -> str:
    metadata = _document_metadata(document)
    chunks = _document_chunks(document)
    content = f"""
    <section class="document-view">
      <div class="doc-meta">
        <span>{escape(document.original_filename)}</span>
        <span>{escape(document.extension)}</span>
        <span>{document.word_count} слов</span>
      </div>
      <div class="doc-actions">
        <a class="open-link" href="#markdown">Подробнее</a>
        <a class="open-link" href="#ai-analysis">AI-анализ</a>
        <a class="open-link" href="#chunks">Chunks</a>
      </div>
      <section id="markdown">
        <h2>Markdown</h2>
        <pre>{escape(getattr(document, "content_text", document.excerpt))}</pre>
      </section>
      <section id="ai-analysis">
        <h2>AI-анализ</h2>
        {_metadata_panel(metadata)}
      </section>
      <section id="chunks">
        <h2>Chunks</h2>
        {_chunks_panel(chunks)}
      </section>
      <form method="post" action="/knowledge/delete/{escape(document.id)}">
        <button class="danger" type="submit">Удалить документ</button>
      </form>
    </section>
"""
    return _page_shell(
        title=f"{document.title} - Память",
        eyebrow="документ",
        heading=document.title,
        hint="",
        active="knowledge",
        content=content,
    )


def _truncate_text(value: str, limit: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _knowledge_card(document: object) -> str:
    metadata = _document_metadata(document)
    summary = str(metadata.get("summary") or getattr(document, "excerpt", ""))
    summary_short = _truncate_text(summary, 140)
    return f"""
    <article class="knowledge-card">
      <div>
        <h3><a href="/knowledge/{escape(document.id)}">{escape(document.title)}</a></h3>
        {f'<p>{escape(summary_short)}</p>' if summary_short else ''}
        <div class="doc-meta">
          <span>{escape(document.extension)}</span>
          <span>{document.word_count} слов</span>
        </div>
      </div>
      <div class="card-actions">
        <a class="open-link" href="/knowledge/{escape(document.id)}">Открыть</a>
        <form method="post" action="/knowledge/delete/{escape(document.id)}">
          <button class="ghost danger-text" type="submit">Удалить</button>
        </form>
      </div>
    </article>
    """


def _document_metadata(document: object) -> dict[str, object]:
    metadata = getattr(document, "document_metadata", {}) or {}
    if isinstance(metadata, dict) and metadata:
        return metadata
    analysis = getattr(document, "analysis", {}) or {}
    if isinstance(analysis, dict) and isinstance(analysis.get("document_metadata"), dict):
        return analysis["document_metadata"]
    return {}


def _document_chunks(document: object) -> list[dict[str, object]]:
    chunks = getattr(document, "chunk_metadata", ()) or ()
    if chunks:
        return [chunk for chunk in chunks if isinstance(chunk, dict)]
    analysis = getattr(document, "analysis", {}) or {}
    if isinstance(analysis, dict) and isinstance(analysis.get("chunks"), list):
        return [chunk for chunk in analysis["chunks"] if isinstance(chunk, dict)]
    return []


def _metadata_panel(metadata: dict[str, object]) -> str:
    if not metadata:
        return '<div class="empty">AI-анализ пока не сохранен.</div>'
    rows = []
    for key in (
        "document_type",
        "summary",
        "topics",
        "competencies",
        "skills",
        "companies",
        "projects",
        "industries",
        "entities",
        "keywords",
        "language",
    ):
        if key not in metadata:
            continue
        value = metadata.get(key)
        if isinstance(value, list):
            rendered = " • ".join(str(item) for item in value if str(item).strip())
        else:
            rendered = str(value)
        rows.append(f"<p><b>{escape(key)}:</b> {escape(rendered)}</p>")
    return "\n".join(rows) or '<div class="empty">AI-анализ пока не сохранен.</div>'


def _chunks_panel(chunks: list[dict[str, object]]) -> str:
    if not chunks:
        return '<div class="empty">Semantic chunks пока не сохранены.</div>'
    cards = []
    for chunk in chunks:
        keywords = chunk.get("keywords", [])
        keyword_text = " • ".join(str(item) for item in keywords if str(item).strip()) if isinstance(keywords, list) else ""
        content = str(chunk.get("content", "")).strip()
        cards.append(
            f"""
            <article class="knowledge-card">
              <div>
                <h3>{escape(str(chunk.get("title", "Chunk")))}</h3>
                <p><b>type:</b> {escape(str(chunk.get("type", "")))}</p>
                <p>{escape(str(chunk.get("summary", "")))}</p>
                <p><b>keywords:</b> {escape(keyword_text or "—")}</p>
                {f'<pre>{escape(content)}</pre>' if content else ''}
              </div>
            </article>
            """
        )
    return '<div class="knowledge-list">' + "".join(cards) + "</div>"


SECTION_TO_NOTE_CATEGORY = {
    "observations": "observation",
    "principles": "principle",
    "stories": "story",
}
NOTE_CATEGORY_TO_SECTION = {value: key for key, value in SECTION_TO_NOTE_CATEGORY.items()}


def _note_category_to_section(category: str) -> str:
    return NOTE_CATEGORY_TO_SECTION.get(category, "observations")


def _memory_tab(section_key: str, title: str, active: str) -> str:
    active_class = " active" if section_key == active else ""
    return f'<a class="memory-tab{active_class}" href="/knowledge?section={escape(section_key)}">{escape(title)}</a>'


def _knowledge_section(value: str) -> str:
    return value if value in {"documents", "cases", "ideas", "observations", "principles", "stories"} else "documents"


def _knowledge_section_content(
    section: str,
    documents: list[object],
    cases: list[object],
    ideas: list[object],
    notes: list[object],
) -> str:
    labels = {
        "documents": ("Документы", "материалы, из которых AI берёт факты"),
        "cases": ("Кейсы", "рабочие ситуации для будущего контента"),
        "ideas": ("Идеи", "мысли и заготовки для постов"),
        "observations": ("Наблюдения", "закономерности и выводы из практики"),
        "principles": ("Принципы", "ваши правила и убеждения"),
        "stories": ("Истории", "жизненные примеры и ситуации"),
    }
    title, eyebrow = labels.get(section, labels["documents"])
    if section == "documents":
        form = _docs_upload_form()
        body = (
            "".join(_knowledge_card(document) for document in documents)
            if documents
            else "<div class=\"empty\">Пока нет документов. Загрузите PDF, DOCX, Markdown или TXT.</div>"
        )
        count = len(documents)
    elif section == "cases":
        form = _case_form()
        body = (
            "".join(_case_card(case) for case in cases)
            if cases
            else "<div class=\"empty\">Кейсов пока нет. Добавьте первый рабочий пример.</div>"
        )
        count = len(cases)
    elif section == "ideas":
        form = _ideas_memory_form()
        body = (
            "".join(_memory_idea_card(idea) for idea in ideas)
            if ideas
            else "<div class=\"empty\">Пока нет идей. Запишите первую мысль — она попадёт в память и в подсказку AI.</div>"
        )
        count = len(ideas)
    else:
        category = SECTION_TO_NOTE_CATEGORY.get(section, "observation")
        section_notes = [note for note in notes if getattr(note, "category", "") == category]
        form = _note_form(section, category)
        body = (
            "".join(_note_card(note, section) for note in section_notes)
            if section_notes
            else "<div class=\"empty\">Здесь пока пусто. Добавьте запись текстом — AI будет её использовать.</div>"
        )
        count = len(section_notes)
    return f"""
    {form}
    <section class="block">
      <div class="section-title">
        <div>
          <p class="eyebrow">{escape(eyebrow)}</p>
          <h2>{escape(title)}</h2>
        </div>
        <span>{count} записей</span>
      </div>
      <div class="knowledge-list">{body}</div>
    </section>
    """


def _docs_upload_form() -> str:
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    return f"""
    <section class="knowledge-upload embedded-form">
      <h2>Загрузить документ</h2>
      <p>Поддерживаются: {escape(supported)}. Документ сохранится локально и попадёт в базовый индекс.</p>
      <form method="post" action="/knowledge/upload" enctype="multipart/form-data" onsubmit="const s=this.querySelector('[data-upload-status]'); if (s) s.textContent='Анализируется...';">
        <input type="file" name="document" required>
        <button type="submit">Добавить в память</button>
        <span class="state-note" data-upload-status></span>
      </form>
    </section>
    """


def _ideas_memory_form() -> str:
    return """
    <section class="knowledge-upload embedded-form">
      <h2>Добавить идею</h2>
      <form method="post" action="/knowledge/ideas/add">
        <textarea name="text" rows="3" placeholder="Запишите идею одним текстом…" required></textarea>
        <button type="submit">Сохранить идею</button>
      </form>
    </section>
    """


NOTE_PLACEHOLDERS = {
    "observations": "Опишите наблюдение или закономерность из практики…",
    "principles": "Сформулируйте принцип или убеждение…",
    "stories": "Расскажите историю или ситуацию из опыта…",
}
NOTE_ADD_LABELS = {
    "observations": "Добавить наблюдение",
    "principles": "Добавить принцип",
    "stories": "Добавить историю",
}


def _note_form(section: str, category: str) -> str:
    placeholder = NOTE_PLACEHOLDERS.get(section, "Запишите текст…")
    add_label = NOTE_ADD_LABELS.get(section, "Добавить запись")
    title_field = (
        '<input name="title" placeholder="Название истории (необязательно)">'
        if section == "stories"
        else ""
    )
    return f"""
    <section class="knowledge-upload embedded-form">
      <h2>{escape(add_label)}</h2>
      <form method="post" action="/knowledge/notes/add">
        <input type="hidden" name="category" value="{escape(category)}">
        {title_field}
        <textarea name="text" rows="3" placeholder="{escape(placeholder)}" required></textarea>
        <button type="submit">Сохранить</button>
      </form>
    </section>
    """


def _note_card(note: object, section: str) -> str:
    note_id = escape(getattr(note, "id", ""))
    title = escape(getattr(note, "title", "") or "")
    text = escape(getattr(note, "text", ""))
    heading = f"<h3>{title}</h3>" if title else ""
    return f"""
    <article class="knowledge-card">
      <div>
        {heading}
        <p>{text}</p>
      </div>
      <form method="post" action="/knowledge/notes/delete/{note_id}">
        <input type="hidden" name="section" value="{escape(section)}">
        <button class="ghost danger-text" type="submit">Удалить</button>
      </form>
    </article>
    """


def _memory_idea_card(idea: object) -> str:
    idea_id = escape(getattr(idea, "id", ""))
    title = escape(getattr(idea, "title", "") or "Идея")
    description = escape(getattr(idea, "description", ""))
    source = escape(_source_ru(getattr(idea, "source", "")))
    return f"""
    <article class="knowledge-card">
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
        <div class="doc-meta"><span>{source}</span></div>
      </div>
      <div class="card-actions">
        <a class="open-link" href="/ideas/{idea_id}">Открыть</a>
        <form method="post" action="/knowledge/ideas/delete/{idea_id}">
          <button class="ghost danger-text" type="submit">Удалить</button>
        </form>
      </div>
    </article>
    """


def _case_form() -> str:
    return """
    <section class="knowledge-upload embedded-form">
      <h2>Добавить кейс</h2>
      <form method="post" action="/knowledge/cases/add">
        <input name="title" placeholder="Название кейса" required>
        <input name="company" placeholder="Компания / проект">
        <textarea name="what_happened" rows="3" placeholder="Что произошло" required></textarea>
        <textarea name="reason" rows="3" placeholder="Причина"></textarea>
        <textarea name="solution" rows="3" placeholder="Решение"></textarea>
        <textarea name="result" rows="3" placeholder="Результат"></textarea>
        <select name="public_usage">
          <option>Можно использовать публично</option>
          <option>Только обезличенно</option>
          <option>Нельзя использовать публично</option>
        </select>
        <input name="key_topics" placeholder="Ключевые темы: Customer Experience, SOP">
        <input name="platforms" placeholder="Площадки: LinkedIn, Telegram">
        <button type="submit">Сохранить кейс</button>
      </form>
    </section>
    """


def _case_card(case: object) -> str:
    topics = ", ".join(case.key_topics) or "темы не указаны"
    platforms = ", ".join(case.platforms) or "площадки не указаны"
    return f"""
    <article class="knowledge-card">
      <div>
        <h3>{escape(case.title)}</h3>
        <p><b>Компания:</b> {escape(case.company or "не указана")}</p>
        <p><b>Что произошло:</b> {escape(case.what_happened)}</p>
        <p><b>Решение:</b> {escape(case.solution)}</p>
        <p><b>Результат:</b> {escape(case.result)}</p>
        <div class="doc-meta">
          <span>{escape(case.public_usage)}</span>
          <span>{escape(topics)}</span>
          <span>{escape(platforms)}</span>
        </div>
      </div>
      <form method="post" action="/knowledge/cases/delete/{escape(case.id)}">
        <button class="ghost danger-text" type="submit">Удалить</button>
      </form>
    </article>
    """


def render_idea_vault(
    ideas: list[Idea],
    saved: bool = False,
    deleted: bool = False,
    updated: bool = False,
) -> str:
    notices = []
    if saved:
        notices.append("Идея сохранена.")
    if deleted:
        notices.append("Идея удалена.")
    if updated:
        notices.append("Статус идеи обновлен.")
    notice_html = "".join(f"<div class=\"notice\">{escape(item)}</div>" for item in notices)
    author_profile = AuthorBrainRepository().load_profile()
    content = f"""
    {notice_html}
    {_key_ideas_section(author_profile)}
    <section class="block">
      <p class="page-hint">Свободные идеи и заготовки теперь живут в разделе <a href="/knowledge?section=ideas">«Память → Идеи»</a> — там же появляются идеи из Дневного брифа и Радара трендов. Здесь остаются только ключевые идеи автора.</p>
    </section>
"""
    return _page_shell(
        title="Идеи",
        eyebrow="ключевые идеи автора",
        heading="Ключевые идеи",
        hint="Опорные убеждения автора, на которые AI опирается при генерации.",
        active="ideas",
        content=content,
    )


def _key_ideas_section(profile: dict[str, object]) -> str:
    ideas = profile.get("key_ideas", [])
    rows = "".join(
        _editable_key_idea_row(item, index)
        for index, item in enumerate(ideas if isinstance(ideas, list) else [])
        if isinstance(item, dict)
    ) or '<div class="empty">Пока нет ключевых идей</div>'
    return f"""
    <section class="block" id="key-ideas">
      <div class="section-title">
        <div>
          <p class="eyebrow">из профиля автора</p>
          <h2>Ключевые идеи автора</h2>
        </div>
      </div>
      <p class="page-hint">Это опорные убеждения из профиля автора — не то же самое, что свободные идеи в «Память → Идеи». AI опирается на них при генерации. Их также можно редактировать в разделе «Профиль автора».</p>
      <form class="profile-form" method="post" action="/ideas/key-ideas">
        <div class="card-list">{rows}</div>
        <section class="profile-section">
          <p class="eyebrow">новая ключевая идея</p>
          {_textarea("new_idea_text", "Идея", "")}
          {_textarea("new_idea_belief", "Как AI должен это понимать", "")}
          {_select("new_idea_repeat_risk", "Риск повтора", "medium", ("low", "medium", "high"))}
          <div class="form-actions">
            <button name="key_ideas_action" value="save" type="submit">Сохранить ключевые идеи</button>
            <button class="ghost" name="key_ideas_action" value="add" type="submit">Добавить идею</button>
          </div>
        </section>
      </form>
    </section>
    """


def _editable_key_idea_row(item: dict[str, object], index: int) -> str:
    markers = [f"риск повтора: {_repeat_risk_label(str(item.get('repeat_risk', 'medium')))}"]
    if str(item.get("source", "")).strip():
        markers.append("ручная база" if str(item.get("source")) == "manual" else str(item.get("source")))
    return f"""
      <article class="card">
        <h3>{escape(_display_ru(str(item.get("idea", ""))))}</h3>
        <div class="tags">{_chips(markers)}</div>
        <details class="inline-editor">
          <summary>Редактировать</summary>
          <div class="edit-row">
            {_textarea(f"idea_{index}_text", "Идея", _display_ru(str(item.get("idea", ""))))}
            {_textarea(f"idea_{index}_belief", "Как AI должен это понимать", _display_ru(str(item.get("belief", ""))))}
            {_select(f"idea_{index}_repeat_risk", "Риск повтора", str(item.get("repeat_risk", "medium")), ("low", "medium", "high"))}
            <button class="ghost" name="key_ideas_action" value="delete_idea_{index}" type="submit">Удалить идею</button>
          </div>
        </details>
      </article>
    """


def render_idea_detail(idea: Idea, planned: str = "") -> str:
    status_options = "".join(
        f"<option value=\"{escape(status)}\" {'selected' if status == idea.status else ''}>{escape(_status_ru(status))}</option>"
        for status in IDEA_STATUSES
    )
    platforms = ", ".join(idea.platforms)
    notice = ""
    if planned == "1":
        notice = '<div class="notice">Идея добавлена в контент-план (статус «Идея»). Откройте контент-план, чтобы назначить дату и площадку.</div>'
    elif planned == "exists":
        notice = '<div class="notice">Такая тема уже есть в контент-плане — новая строка не добавлена.</div>'
    content = f"""
    {notice}
    <section class="document-view">
      <div class="doc-meta">
        <span>{escape(_status_ru(idea.status))}</span>
        <span>{escape(_source_ru(idea.source))}</span>
        <span>{escape(platforms)}</span>
        <span>{escape(idea.created_at)}</span>
      </div>
      <pre>{escape(idea.description)}</pre>
      <div class="form-actions">
        <form method="post" action="/ideas/plan/{escape(idea.id)}">
          <button type="submit" data-busy="Добавляю…">Добавить в контент-план</button>
        </form>
        <a class="open-link" href="/content-plan">Открыть контент-план</a>
        <form method="post" action="/ideas/status/{escape(idea.id)}">
          <select name="status">{status_options}</select>
          <button class="secondary" type="submit">Обновить статус</button>
        </form>
        <form method="post" action="/ideas/delete/{escape(idea.id)}">
          <button class="danger" type="submit">Удалить идею</button>
        </form>
      </div>
    </section>
"""
    return _page_shell(
        title=f"{idea.title} - Идеи",
        eyebrow="идея",
        heading=idea.title,
        hint="",
        active="ideas",
        content=content,
    )


def _save_idea_form(title: str, description: str, source: str, platforms: tuple[str, ...], label: str = "Сохранить в Идеи") -> str:
    platform_text = ", ".join(platforms)
    return f"""
    <form class="inline-save" method="post" action="/ideas/add">
      <input type="hidden" name="title" value="{escape(title)}">
      <input type="hidden" name="description" value="{escape(description)}">
      <input type="hidden" name="source" value="{escape(source)}">
      <input type="hidden" name="platforms" value="{escape(platform_text)}">
      <button class="ghost" type="submit">{escape(label)}</button>
    </form>
    """


def _platforms_from_form(data: dict[str, list[str]]) -> tuple[str, ...]:
    value = data.get("platforms", [""])[0]
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _csv_to_tuple(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _source_ru(source: str) -> str:
    sources = {
        "Manual": "Вручную",
        "Daily Brief": "Дневной бриф",
        "Knowledge": "Память",
        "Content Plan": "Контент-план",
        "Trend Radar": "Радар трендов",
        "Trend Radar: черновик": "Радар трендов: черновик",
    }
    return sources.get(source, source)


def _publication_for_topic(plan: ContentPlan, title: str) -> object | None:
    normalized = title.lower()
    for publication in plan.planned_publications:
        if publication.topic.lower() == normalized:
            return publication
    return plan.planned_publications[0] if plan.planned_publications else None


def _platform_for_item(item: BriefItem | None) -> str:
    if not item:
        return ""
    known = ("LinkedIn", "Telegram", "VC", "Сетка")
    for tag in item.tags:
        if tag in known:
            return tag
    for platform in known:
        if platform.lower() in item.action.lower() or platform.lower() in item.reason.lower():
            return platform
    return ""


def _platform_goal(platform: str) -> str:
    goals = {
        "LinkedIn": "Усилить экспертное позиционирование и запустить профессиональное обсуждение.",
        "Telegram": "Сформулировать живое наблюдение и поддержать регулярный контакт с аудиторией.",
        "VC": "Развернуть тему в глубокий экспертный материал с практическим выводом.",
        "Сетка": "Сохранить регулярное профессиональное присутствие через короткое наблюдение.",
    }
    return goals.get(platform, "Поддержать экспертное присутствие без лишней нагрузки на день.")


def _time_estimate(platform: str) -> str:
    estimates = {
        "LinkedIn": "30-40 минут",
        "Telegram": "15-25 минут",
        "VC": "60-90 минут",
        "Сетка": "10-20 минут",
    }
    return estimates.get(platform, "20-30 минут")


def _status_ru(status: str) -> str:
    statuses = {
        "New": "Новая",
        "In Progress": "В работе",
        "Drafted": "Черновик готов",
        "Published": "Опубликована",
        "Archived": "В архиве",
        "planned": "запланировано",
        "suggested": "предложено",
        "drafted": "черновик",
        "approved": "утверждено",
        "needs_ai_plan": "запросить AI-план",
        "ready_for_review": "готово к просмотру",
    }
    statuses.update(
        {
            "New": "Новая",
            "In Progress": "В работе",
            "Drafted": "Черновик",
            "Published": "Опубликовано",
            "Archived": "Архив",
            "idea": "Идея",
            "planned": "Запланировано",
            "suggested": "Идея",
            "draft": "Черновик",
            "drafted": "В работе",
            "in_progress": "В работе",
            "review": "В работе",
            "approved": "Утверждено",
            "published": "Опубликовано",
            "archived": "Архив",
            "needs_ai_plan": "Идея",
            "ready_for_review": "В работе",
        }
    )
    return statuses.get(status, status)


def _stories_to_text(stories: object) -> str:
    if not isinstance(stories, list):
        return ""
    rows = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        title = str(story.get("title", "")).strip()
        situation = str(story.get("situation", "")).strip()
        lesson = str(story.get("lesson", "")).strip()
        topics = ", ".join(story.get("topics", ()))
        rows.append(f"{title}\nСитуация: {situation}\nВывод: {lesson}\nТемы: {topics}")
    return "\n\n---\n\n".join(rows)


def _stories_from_text(text: str) -> list[dict[str, object]]:
    stories = []
    for block in text.split("---"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        title = lines[0]
        situation = ""
        lesson = ""
        topics: list[str] = []
        for line in lines[1:]:
            if line.startswith("Ситуация:"):
                situation = line.replace("Ситуация:", "", 1).strip()
            elif line.startswith("Вывод:"):
                lesson = line.replace("Вывод:", "", 1).strip()
            elif line.startswith("Темы:"):
                topics = [item.strip() for item in line.replace("Темы:", "", 1).split(",") if item.strip()]
        stories.append({"title": title, "situation": situation, "lesson": lesson, "topics": topics})
    return stories


def _load_ui_state() -> dict[str, object]:
    if not UI_STATE_PATH.exists():
        return {"refinements": {}}
    try:
        state = json.loads(UI_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"refinements": {}}
    if not isinstance(state, dict):
        return {"refinements": {}}
    state.setdefault("refinements", {})
    return state


def _save_ui_state(state: dict[str, object]) -> None:
    UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _filename_from_multipart_header(header: bytes) -> str | None:
    match = re.search(rb'filename="([^"]*)"', header)
    if not match:
        return None
    return match.group(1).decode("utf-8", errors="ignore")


def _author_profile_form_to_raw(data: dict[str, list[str]]) -> dict[str, object]:
    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    return {
        "tone": {
            "formality": value("formality"),
            "directness": value("directness"),
            "provocation": value("provocation"),
            "emotionality": value("emotionality"),
        },
        "structure": {
            "post_structure": value("post_structure"),
            "intro_length": value("intro_length"),
            "narrative_logic": value("narrative_logic"),
            "conclusion": value("conclusion"),
        },
        "vocabulary": {
            "favorite_words": text_to_list(value("favorite_words")),
            "unwanted_words": text_to_list(value("unwanted_words")),
            "banned_cliches": text_to_list(value("banned_cliches")),
            "professional_terms": text_to_list(value("professional_terms")),
        },
        "platform_rules": {
            "LinkedIn": value("platform_linkedin"),
            "Telegram": value("platform_telegram"),
            "VC": value("platform_vc"),
            "Сетка": value("platform_setka"),
        },
        "platform_goals": {
            "LinkedIn": value("goal_linkedin"),
            "Telegram": value("goal_telegram"),
            "VC": value("goal_vc"),
            "Сетка": value("goal_setka"),
        },
        "what_not_to_write": text_to_list(value("what_not_to_write")),
        "examples_and_stories": _stories_from_text(value("examples_and_stories")),
    }


def _input(name: str, label: str, value: object) -> str:
    return f"""
    <label>
      <span>{escape(label)}</span>
      <input name="{escape(name)}" value="{escape(str(value))}">
    </label>
    """


def _date_input(name: str, label: str, value: object) -> str:
    return f"""
    <label>
      <span>{escape(label)}</span>
      <input type="date" name="{escape(name)}" value="{escape(_date_for_input(str(value)))}">
    </label>
    """


def _normalize_platform(value: str) -> str:
    value = value.strip()
    return value if value in CONTENT_PLATFORMS else (value or "LinkedIn")


def _normalize_publication_format(value: str) -> str:
    value = value.strip()
    if value in PUBLICATION_FORMATS:
        return value
    return {
        "Аналитика": "экспертный пост",
        "Кейс": "статья",
        "Framework": "карусель/пост",
        "Наблюдение": "короткий пост",
        "Разбор ошибки": "экспертный пост",
        "Миф": "экспертный пост",
        "Storytelling": "пост",
        "Разговорный пост": "короткий пост",
        "Инструменты": "карусель/пост",
        "Ответ на вопрос": "короткий пост",
    }.get(value, "пост")


def _normalize_strategy_format(value: str, platform: str, rubric: str) -> str:
    value = value.strip()
    if value in PUBLICATION_FORMATS:
        return value
    if value in RUBRICS:
        return _default_format_for_strategy(platform, rubric)
    return _default_format_for_strategy(platform, rubric)


def _default_format_for_strategy(platform: str, rubric: str) -> str:
    platform = _normalize_platform(platform)
    rubric = _normalize_rubric(rubric)
    if platform == "VC":
        return "статья"
    if platform == "Сетка":
        return "мини-пост"
    if rubric == "Framework":
        return "карусель/пост"
    if rubric in {"Аналитика", "Разбор ошибки", "Миф"}:
        return "экспертный пост"
    if rubric == "Кейс":
        return "статья"
    if rubric in {"Наблюдение", "Разговорный пост", "Ответ на вопрос"}:
        return "короткий пост"
    return "пост"


def _normalize_rubric(value: str) -> str:
    value = value.strip()
    return value if value in RUBRICS else "Наблюдение"


def _publication_format(item: object) -> str:
    if not isinstance(item, dict):
        return "пост"
    return _normalize_strategy_format(
        str(item.get("format") or ""),
        str(item.get("platform") or ""),
        str(item.get("rubric") or item.get("pillar") or ""),
    )


def _publication_rubric(item: object) -> str:
    if not isinstance(item, dict):
        return "Наблюдение"
    return _normalize_rubric(str(item.get("rubric") or item.get("pillar") or item.get("format") or ""))


def _normalize_publication_status(status: str) -> str:
    # Collapse every legacy status onto the shared 3-stage lifecycle.
    mapping = {
        "idea": "draft",
        "planned": "draft",
        "suggested": "draft",
        "needs_ai_plan": "draft",
        "New": "draft",
        "new": "draft",
        "in_progress": "draft",
        "In Progress": "draft",
        "ready_for_review": "draft",
        "review": "draft",
        "drafted": "draft",
        "Drafted": "draft",
        "Approved": "approved",
        "Published": "published",
        "Archived": "published",
        "archived": "published",
    }
    normalized = mapping.get(status, status)
    return normalized if normalized in PUBLICATION_STATUSES else "draft"


def _next_publication_status(status: str) -> str:
    current = _normalize_publication_status(status)
    index = PUBLICATION_STATUSES.index(current) if current in PUBLICATION_STATUSES else 0
    return PUBLICATION_STATUSES[min(index + 1, len(PUBLICATION_STATUSES) - 1)]


def _status_badge(status: str) -> str:
    normalized = _normalize_publication_status(status)
    return f'<div class="status-badge status-{escape(normalized)}">● {escape(_status_ru(normalized))}</div>'


def _language_for_platform(platform: str) -> str:
    return "English" if _normalize_platform(platform) == "LinkedIn" else "Russian"


def _language_policy_for_platform(platform: str) -> str:
    if _normalize_platform(platform) == "LinkedIn":
        return "LinkedIn means every topic, title, goal, summary, recommendation, explanation and draft must be in English. Do not output Russian for LinkedIn."
    return "Telegram, VC and Сетка mean every topic, title, goal, summary, recommendation, explanation and draft must be in Russian. Do not output English for this platform."


def _effective_rubric_rules() -> dict[str, object]:
    """Rubric recipes from the editable "Правила бота" store (falls back to defaults)."""
    from .bot_rules import load_bot_rules

    rules = load_bot_rules().get("rubric_rules", {})
    return rules if isinstance(rules, dict) and rules else RUBRIC_LIBRARY


def _publication_format_instruction(publication_format: str) -> str:
    rubric = _normalize_rubric(publication_format)
    library = _effective_rubric_rules()
    rules = library.get(rubric) or library.get("Наблюдение") or RUBRIC_LIBRARY["Наблюдение"]
    return f"{rubric}: " + "; ".join(str(step) for step in rules)


def _select(name: str, label: str, selected: str, options: tuple[str, ...] | list[str]) -> str:
    option_html = "".join(
        f"<option value=\"{escape(option)}\" {'selected' if option == selected else ''}>{escape(_status_ru(option) if name.endswith('_status') or name == 'status' else option)}</option>"
        for option in options
    )
    return f"""
    <label>
      <span>{escape(label)}</span>
      <select name="{escape(name)}">{option_html}</select>
    </label>
    """


def _textarea(name: str, label: str, value: object) -> str:
    return f"""
    <label>
      <span>{escape(label)}</span>
      <textarea name="{escape(name)}" rows="5">{escape(str(value))}</textarea>
    </label>
    """


def _drafts_to_prepare_section(brief: DailyBrief, ai_result: dict[str, object] | None = None) -> str:
    TextPostRepository().sync_from_content_plan(_load_content_plan_raw())
    cards = "".join(
        _draft_to_prepare_card(topic, draft, brief, ai_result if index == 0 else None)
        for index, (topic, draft) in enumerate(zip(brief.topics, brief.drafts))
    )
    if not cards:
        cards = "<div class=\"empty\">На сегодня нет публикаций в контент-плане.</div>"
    return f"""
    <section class="block" id="drafts">
      <div class="section-title">
        <div>
          <p class="eyebrow">работа на сегодня</p>
          <h2>Черновики к подготовке</h2>
        </div>
        <span>{len(brief.drafts)} к подготовке</span>
      </div>
      <div class="card-list draft-prep-list">
        {cards}
      </div>
    </section>
    """


def _draft_chain(stage: str, approved_post: object | None = None) -> str:
    """Show which text is currently 'the main one': Шаблон → AI-черновик → Утверждённый текст.
    The active step is the one whose text is shown in the card below."""
    order = ("template", "ai", "approved")
    labels = {"template": "Шаблон", "ai": "AI-черновик", "approved": "Утверждённый текст"}
    active_index = order.index(stage)
    steps = []
    for index, key in enumerate(order):
        state = "active" if index == active_index else ("done" if index < active_index else "todo")
        steps.append(f'<span class="chain-step is-{state}">{escape(labels[key])}</span>')
    chain = '<span class="chain-sep">→</span>'.join(steps)
    link = ""
    if stage == "approved" and approved_post is not None:
        post_id = escape(str(getattr(approved_post, "id", "")))
        if post_id:
            link = f'<a class="chain-link" href="/texts/{post_id}">Открыть в Текстах</a>'
    return f"""
      <div class="draft-chain">
        <div class="chain-steps">{chain}</div>
        {link}
      </div>
    """


def _draft_to_prepare_card(
    topic: BriefItem,
    draft: Draft,
    brief: DailyBrief,
    ai_result: dict[str, object] | None = None,
) -> str:
    ui_state = _load_ui_state()
    key = _item_key(f"{draft.platform}-{draft.title}")
    refinement = _refinement_entry(ui_state, key)
    action = _refinement_action(refinement)
    title = str(refinement.get("title") or _refined_title(draft.title, action))
    ai_draft = str((ai_result or {}).get("draft", ""))
    refinement_text = str(refinement.get("text") or "")
    if refinement_text and _looks_like_forbidden_draft(refinement_text):
        refinement_text = ""
    draft_text = str(refinement_text or _refined_text(ai_draft or draft.text, action))
    publication = _publication_for_topic(brief.content_plan, topic.title)
    platform = str(getattr(publication, "platform", "")) or draft.platform
    if not _text_matches_platform(ai_draft, platform):
        ai_draft = ""
    approved_post = TextPostRepository().approved_for_publication(publication)
    if approved_post:
        title = approved_post.title
        draft_text = approved_post.text
    if approved_post:
        stage = "approved"
    elif ai_draft:
        stage = "ai"
    else:
        stage = "template"
    goal = str(getattr(publication, "goal", "")) or topic.action
    summary = str(getattr(publication, "summary", "")) or topic.summary
    refinement_notice = _refinement_notice(refinement)
    tags = "".join(f"<span>{escape(_status_ru(tag))}</span>" for tag in topic.tags)
    text_label = {
        "approved": "Утверждённый текст из раздела «Тексты»",
        "ai": "AI-черновик",
        "template": "Первый черновик текста (шаблон)",
    }[stage]
    return f"""
    <article class="card draft-prep-card" id="{escape(key)}">
      <div class="card-head">
        <h3>{escape(title)}</h3>
        <strong>{escape(platform)}</strong>
      </div>
      {_draft_chain(stage, approved_post)}
      <div class="draft-context-grid">
        <div>
          <p class="label">Цель</p>
          <p>{escape(goal)}</p>
        </div>
        <div>
          <p class="label">Почему актуально</p>
          <p>{escape(topic.reason)}</p>
        </div>
      </div>
      <p class="label">Краткая структура</p>
      <p>{escape(_draft_structure(platform, summary))}</p>
      <p class="label">{text_label}</p>
      <pre>{escape(draft_text)}</pre>
      {_thinking_transparency_block(ai_result)}
      {_writing_feedback_block(key, title, draft_text)}
      {refinement_notice}
      <div class="topic-actions">
        {_save_idea_form(topic.title, summary, "Daily Brief", topic.tags, label="Использовать")}
        {_refinement_bar(key, draft.title, draft.text, "draft")}
      </div>
      <div class="tags">{tags}</div>
    </article>
    """


def _thinking_transparency_block(ai_result: dict[str, object] | None) -> str:
    if not ai_result:
        return ""
    rows = ai_result.get("thinking_transparency", [])
    if not isinstance(rows, list) or not rows:
        engine = ai_result.get("thinking_engine", {})
        if isinstance(engine, dict):
            rows = engine.get("transparency", [])
    if not isinstance(rows, list) or not rows:
        return ""
    items = "".join(f"<li>{escape(str(row))}</li>" for row in rows[:6])
    mode = ""
    engine = ai_result.get("thinking_engine", {})
    if isinstance(engine, dict):
        mode = str(engine.get("mode", ""))
    return f"""
    <details class="thinking-transparency">
      <summary>Почему AI написал именно так?</summary>
      <p>{escape(f"Режим: {mode}" if mode else "Внутреннее рассуждение AI")}</p>
      <ul>{items}</ul>
    </details>
    """


def _writing_feedback_block(item_key: str, title: str, text: str) -> str:
    return f"""
    <div class="writing-feedback">
      <p class="label">Мои комментарии для AI</p>
      <form method="post" action="/daily-brief/feedback">
        <input type="hidden" name="item_key" value="{escape(item_key)}">
        <input type="hidden" name="title" value="{escape(title)}">
        <input type="hidden" name="text" value="{escape(text)}">
        <textarea name="feedback" rows="7" placeholder="Например: начало скучное, добавь кейс MAYRVEDA, слишком академично, вывод слабый..."></textarea>
        <div class="form-actions">
          <button type="submit" name="intent" value="draft">Применить только к этому черновику</button>
          <button class="secondary" type="submit" name="intent" value="lesson">Предложить новое правило</button>
        </div>
      </form>
    </div>
    """


def _draft_structure(platform: str, summary: str) -> str:
    if platform == "Telegram":
        return f"Наблюдение -> управленческий вывод -> практический вопрос аудитории. Основа: {summary}"
    if platform == "VC":
        return f"Тезис -> разбор причины -> практическая модель -> вывод. Основа: {summary}"
    if platform == "Сетка":
        return f"Короткий тезис -> пример -> вывод для практики. Основа: {summary}"
    return f"Тезис -> диагноз -> 2-3 аргумента -> практический вывод. Основа: {summary}"


def _looks_like_forbidden_draft(text: str) -> bool:
    normalized = text.strip().lower()
    forbidden_openings = (
        "в современном мире",
        "сегодня многие компании",
        "в бизнесе часто",
        "не секрет",
        "многие считают",
        "customer experience — это",
        "service design — это",
        "искусственный интеллект сегодня",
        "в эпоху цифровизации",
    )
    forbidden_markers = (
        "стиль:",
        "структура:",
        "правило платформы:",
        "можно:",
        "цель публикации:",
        "основная мысль:",
    )
    return normalized.startswith(forbidden_openings) or any(marker in normalized for marker in forbidden_markers)


def _refinement_bar(item_key: str, title: str, text: str, kind: str) -> str:
    buttons = "".join(
        f"""
        <form method="post" action="/daily-brief/refine" onsubmit="this.querySelector('button').textContent='Обновляем...';">
          <input type="hidden" name="item_key" value="{escape(item_key)}">
          <input type="hidden" name="action" value="{escape(label)}">
          <input type="hidden" name="title" value="{escape(title)}">
          <input type="hidden" name="text" value="{escape(text)}">
          <input type="hidden" name="kind" value="{escape(kind)}">
          <button class="ghost" type="submit">{escape(label)}</button>
        </form>
        """
        for label in REFINEMENT_ACTIONS
    )
    return f"<div class=\"refine\">{buttons}</div>"


def _refine_with_ai(action: str, title: str, text: str, kind: str) -> dict[str, object]:
    action = action if action in REFINEMENT_ACTIONS else "Другой вариант"
    title = title.strip()
    text = text.strip()
    kind = kind if kind in {"today", "topic", "draft"} else "text"
    try:
        gateway = AIGateway()
        if not gateway.is_configured():
            raise AIGatewayError("ProxyAPI не настроен.")
        context = DailyBriefRequestHandler.ai_context_engine.build({"topic": title, "summary": text}, include_local_sources=True)
        response = _complete_json_with_retry(
            gateway,
            system_prompt=(
                "Ты редактор AI Chief Content Officer для Personal Brand OS. "
                "Доработай только переданный фрагмент. Не добавляй новые факты. "
                "Ответь строго JSON с полями title и text."
            ),
            user_prompt=(
                f"Тип фрагмента: {kind}\n"
                f"Действие: {action}\n"
                f"Текущий заголовок: {title}\n"
                f"Текущий текст: {text}\n\n"
                f"AI Context Engine:\n{json.dumps(context, ensure_ascii=False)}\n\n"
                "Правила:\n"
                "- 'Обновить заголовок' меняет прежде всего title.\n"
                "- 'Другой вариант' дает свежую формулировку без изменения смысла.\n"
                "- 'Сделать сильнее' делает мысль более точной и уверенной.\n"
                "- 'Сделать мягче' снижает категоричность и делает тон спокойнее.\n"
                "Верни JSON: {\"title\":\"...\", \"text\":\"...\"}."
            ),
            action="daily_brief_refine",
        )
        refined_title = str(response.get("title") or title).strip() or title
        refined_text = str(response.get("text") or text).strip() or text
        return {
            "action": action,
            "status": "updated",
            "kind": kind,
            "title": refined_title,
            "text": refined_text,
            "error": "",
        }
    except AIGatewayError as exc:
        _save_ai_action_error("daily_brief_refine", exc)
        error_text = AI_TIMEOUT_MESSAGE
    except Exception as exc:
        _save_ai_action_error("daily_brief_refine", exc)
        error_text = AI_TIMEOUT_MESSAGE
    return {
        "action": action,
        "status": "error",
        "kind": kind,
        "title": title,
        "text": text,
        "error": error_text,
    }


def _apply_feedback_with_ai(title: str, text: str, feedback: str) -> dict[str, object]:
    feedback = feedback.strip()
    if not feedback:
        return {
            "action": "Комментарий AI",
            "status": "error",
            "kind": "draft",
            "title": title,
            "text": text,
            "error": "Комментарий пустой.",
        }
    try:
        gateway = AIGateway()
        if not gateway.is_configured():
            raise AIGatewayError("ProxyAPI не настроен.")
        context = DailyBriefRequestHandler.ai_context_engine.build({"topic": title, "summary": text}, include_local_sources=True)
        response = gateway.complete_json(
            system_prompt=(
                "You are the Thinking Engine editor for a Personal Brand OS. "
                "Apply the user's feedback only to the current draft. Do not create a permanent rule. "
                "Do not invent facts, companies, numbers, or projects. Return strict JSON with title and text."
            ),
            user_prompt=(
                f"Title: {title}\n\n"
                f"Current draft:\n{text}\n\n"
                f"User feedback:\n{feedback}\n\n"
                f"AI Context Engine:\n{json.dumps(context, ensure_ascii=False)}\n\n"
                "Rewrite the draft in Russian as a complete publication. Return JSON: {\"title\":\"...\", \"text\":\"...\"}."
            ),
            action="draft_feedback",
        )
        return {
            "action": "Комментарий AI",
            "status": "updated",
            "kind": "draft",
            "title": str(response.get("title") or title).strip() or title,
            "text": str(response.get("text") or text).strip() or text,
            "error": "",
        }
    except Exception as exc:
        _save_ai_action_error("draft_feedback", exc)
        return {
            "action": "Комментарий AI",
            "status": "error",
            "kind": "draft",
            "title": title,
            "text": text,
            "error": str(exc),
        }


def _complete_json_with_retry(
    gateway: AIGateway,
    system_prompt: str,
    user_prompt: str,
    action: str,
) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return gateway.complete_json(system_prompt=system_prompt, user_prompt=user_prompt, action=action)
        except AIGatewayError as exc:
            last_error = exc
            if attempt == 1:
                break
            if not _looks_like_timeout(exc):
                break
        except (TimeoutError, socket.timeout) as exc:
            last_error = exc
            if attempt == 1:
                break
    if last_error:
        _save_ai_action_error(action, last_error)
        if isinstance(last_error, AIGatewayError):
            raise last_error
        raise AIGatewayError(str(last_error)) from last_error
    raise AIGatewayError("AI request failed.")


def _looks_like_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("timeout", "timed out", "превыш", "time-out"))


def _save_ai_action_error(action: str, exc: Exception) -> None:
    AI_ACTION_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(AI_ACTION_DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        items = []
    items.insert(
        0,
        {
            "action": action,
            "error": str(exc),
            "type": exc.__class__.__name__,
            "created_at": date.today().isoformat(),
        },
    )
    AI_ACTION_DIAGNOSTICS_PATH.write_text(
        json.dumps(items[:20], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _item_key(text: str) -> str:
    words = re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE)
    return "-".join(words[:8]) or "item"


def _refinement_entry(state: dict[str, object], item_key: str) -> dict[str, object]:
    refinements = state.get("refinements", {})
    if not isinstance(refinements, dict):
        return {}
    item = refinements.get(item_key, {})
    if isinstance(item, dict):
        return item
    return {}


def _refinement_action(refinement: dict[str, object]) -> str:
    return str(refinement.get("action", ""))


def _refined_title(title: str, refinement: str) -> str:
    if refinement == "Обновить заголовок":
        return f"Новый угол: {title}"
    if refinement in {"Другой вариант", "Дать другой вариант"}:
        return f"Альтернативно: {title}"
    if refinement in {"Сделать сильнее", "Сделай сильнее"}:
        return f"Сильнее: {title}"
    if refinement == "Сделать мягче":
        return f"Мягче: {title}"
    return title


def _refined_text(text: str, refinement: str) -> str:
    if refinement in {"Другой вариант", "Дать другой вариант"}:
        return f"Альтернативный вариант: {text}"
    if refinement in {"Сделать сильнее", "Сделай сильнее"}:
        return f"Более сильная формулировка: {text}"
    if refinement == "Сделать мягче":
        return f"Более мягкая формулировка: {text}"
    return text


def _refinement_notice(refinement: dict[str, object]) -> str:
    action = _refinement_action(refinement)
    if not action:
        return ""
    if refinement.get("status") == "error":
        return _ai_error_note(refinement.get("error", ""), "обновить черновик")
    return f"<div class=\"state-note\">Обновлено: {escape(action)}.</div>"


def _styles() -> str:
    return """
    :root {
      color-scheme: dark;
      /* Luxury Editorial OS — deep warm graphite with a coral accent. */
      --bg: #121317;
      --paper: #1b1d23;
      --paper-soft: #23252e;
      --paper-rgb: 27, 29, 35;
      --ink: #ecebf0;
      --muted: #989ba8;
      --line: #2f323d;
      --line-soft: #262933;
      --accent: #f0604a;
      --accent-strong: #ff7358;
      --accent-soft: rgba(240, 96, 74, .14);
      --risk: #e6a15f;
      --radius: 14px;
      --radius-sm: 10px;
      --shadow: 0 18px 46px rgba(0, 0, 0, .38);
    }
    * { box-sizing: border-box; }
    html { overflow-x: hidden; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
      text-rendering: optimizeLegibility;
      overflow-x: hidden;
    }
    .shell {
      width: min(1120px, calc(100% - 48px));
      margin: 0 auto;
      padding: 42px 0 72px;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 32px;
      align-items: flex-end;
      padding-bottom: 28px;
      border-bottom: 1px solid var(--line);
    }
    .eyebrow {
      margin: 0 0 8px;
      color: var(--muted);
      text-transform: uppercase;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .12em;
    }
    h1, h2, h3, p { margin: 0; }
    .page-hint {
      margin-top: 10px;
      max-width: 460px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }
    h1 {
      font-size: clamp(42px, 6vw, 78px);
      line-height: .95;
      font-weight: 720;
      letter-spacing: 0;
    }
    h2 {
      font-size: 20px;
      line-height: 1.2;
      font-weight: 680;
      letter-spacing: 0;
    }
    h3 {
      font-size: 17px;
      line-height: 1.3;
      font-weight: 680;
      letter-spacing: 0;
    }
    .meta {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
      color: var(--muted);
      font-size: 13px;
    }
    .meta a, .form-actions a, .open-link {
      color: var(--ink);
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 7px 12px;
      background: var(--paper-soft);
      font-weight: 680;
      transition: border-color .16s ease, background .16s ease, color .16s ease;
    }
    .meta a:hover, .form-actions a:hover, .open-link:hover {
      border-color: var(--accent);
      color: var(--ink);
    }
    /* Links styled as ghost buttons (e.g. «Открыть текст») match the muted buttons beside them. */
    .form-actions a.ghost {
      background: transparent;
      color: var(--muted);
      font-weight: 620;
    }
    .form-actions a.ghost:hover {
      background: var(--paper-soft);
      border-color: var(--accent);
      color: var(--ink);
    }
    /* ===== Sidebar navigation (desktop) / burger (mobile) ===== */
    .sidebar {
      position: fixed;
      top: 0;
      left: 0;
      bottom: 0;
      width: 240px;
      display: flex;
      flex-direction: column;
      padding: 26px 16px 20px;
      background: var(--paper);
      border-right: 1px solid var(--line-soft);
      overflow-y: auto;
      z-index: 50;
    }
    .brand {
      display: block;
      padding: 4px 12px 18px;
      font-size: 15px;
      font-weight: 720;
      letter-spacing: .01em;
      color: var(--ink);
      text-decoration: none;
    }
    .sidebar-nav { display: flex; flex-direction: column; gap: 2px; }
    .nav-group {
      margin: 18px 12px 8px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .14em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .nav-group:first-child { margin-top: 0; }
    .nav-link {
      display: block;
      padding: 9px 12px;
      border-radius: 10px;
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 640;
      transition: background .16s ease, color .16s ease;
    }
    .nav-link:hover { background: var(--paper-soft); color: var(--ink); }
    .nav-link.active { background: var(--accent-soft); color: var(--accent); }
    .sidebar-foot {
      margin-top: auto;
      padding: 16px 12px 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .sidebar-extra { margin: 0; color: var(--muted); font-size: 12px; font-weight: 640; }
    .sidebar-foot-link {
      color: var(--muted);
      font-size: 12px;
      font-weight: 640;
      text-decoration: none;
      padding: 2px 0;
    }
    .sidebar-foot-link:hover { color: var(--ink); }
    .sidebar-foot-link.active { color: var(--accent); }
    .burger {
      display: none;
      position: fixed;
      top: 14px;
      right: 14px;
      z-index: 60;
      width: 44px;
      height: 44px;
      align-items: center;
      justify-content: center;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 12px;
      color: var(--ink);
      font-size: 20px;
      cursor: pointer;
      box-shadow: var(--shadow);
    }
    .nav-backdrop { display: none; }
    @media (min-width: 900px) {
      body { padding-left: 240px; }
    }
    @media (max-width: 899px) {
      .burger { display: inline-flex; }
      .sidebar {
        width: min(280px, 82vw);
        transform: translateX(-100%);
        transition: transform .22s ease;
        box-shadow: var(--shadow);
      }
      .nav-toggle:checked ~ .sidebar { transform: none; }
      .nav-toggle:checked ~ .nav-backdrop {
        display: block;
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, .5);
        z-index: 40;
      }
    }
    .cloud-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin: 8px 0 0;
      font-size: 12px;
      font-weight: 640;
      color: var(--muted);
    }
    .cloud-chip.cloud-ok { color: var(--accent); }
    .cloud-chip.cloud-wait { color: var(--muted); }
    a:focus-visible, button:focus-visible, summary:focus-visible,
    input:focus-visible, textarea:focus-visible, select:focus-visible {
      outline: 3px solid var(--accent);
      outline-offset: 2px;
      border-radius: 6px;
    }
    .open-link {
      display: inline-flex;
      height: fit-content;
      white-space: nowrap;
    }
    .meta span {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 7px 11px;
      background: rgba(var(--paper-rgb),.45);
    }
    .summary {
      max-width: 880px;
      padding: 36px 0 18px;
    }
    .summary p {
      font-size: clamp(20px, 2.4vw, 30px);
      line-height: 1.28;
      font-weight: 560;
      color: #2b302b;
    }
    .hero-cards {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin: 34px 0 24px;
    }
    .stat-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin: 28px 0 24px;
    }
    .stat-card {
      display: flex;
      flex-direction: column;
      gap: 6px;
      background: var(--paper);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 18px 20px;
    }
    .stat-card span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .stat-card strong {
      font-size: 34px;
      line-height: 1;
      font-weight: 720;
      color: var(--accent);
    }
    .summary-card {
      min-height: 190px;
      background: rgba(var(--paper-rgb), .86);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 20px;
      box-shadow: 0 10px 30px rgba(45, 42, 35, .03);
      min-width: 0;
    }
    .summary-card h2 {
      font-size: 19px;
      margin-bottom: 12px;
      overflow-wrap: anywhere;
    }
    .summary-card p:last-child {
      color: var(--muted);
      font-size: 14px;
    }
    .summary-card summary {
      display: inline-flex;
      margin-top: 14px;
      color: var(--accent);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 680;
      list-style: none;
      cursor: pointer;
      min-height: 40px;
      align-items: center;
      touch-action: manipulation;
    }
    .summary-card summary::-webkit-details-marker { display: none; }
    .summary-card .summary-full {
      display: none;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line-soft);
    }
    .summary-card[open] .summary-full { display: block; }
    .summary-card[open] .summary-compact { display: none; }
    .expand-close { display: none; }
    .summary-card[open] .expand-open { display: none; }
    .summary-card[open] .expand-close { display: inline; }
    .today-card {
      display: grid;
      grid-template-columns: minmax(280px, .9fr) minmax(0, 1.35fr);
      gap: 28px;
      margin: 34px 0 26px;
      padding: 30px;
      background: var(--paper);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .today-main h2 {
      font-size: 18px;
      color: var(--muted);
      margin-bottom: 10px;
    }
    .today-main h3 {
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1.05;
      max-width: 620px;
    }
    .today-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 18px;
    }
    .today-meta span, .topic-platform {
      display: inline-flex;
      width: fit-content;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 5px 9px;
      background: var(--paper-soft);
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
    }
    .today-details {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }
    .today-details p:last-child {
      color: var(--ink);
    }
    .today-publications {
      grid-column: 1 / -1;
      display: grid;
      gap: 10px;
    }
    .today-publication {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: rgba(var(--paper-rgb), .68);
      padding: 14px;
    }
    .today-publication span {
      color: var(--accent);
      font-size: 12px;
      font-weight: 760;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .today-publication h4 {
      margin: 4px 0 6px;
      font-size: 17px;
    }
    .today-publication p {
      margin: 0;
      color: var(--muted);
    }
    .today-publication strong {
      white-space: nowrap;
      color: var(--accent);
      font-size: 13px;
    }
    .today-actions {
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding-top: 20px;
      border-top: 1px solid var(--line-soft);
    }
    .score-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin: 16px 0;
    }
    .score-grid div {
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 14px;
      background: var(--paper-soft);
    }
    .score-grid b {
      display: block;
      margin-top: 4px;
      font-size: 24px;
      color: var(--accent);
    }
    .trend-radar-list {
      gap: 18px;
    }
    .trend-mode {
      margin: 0 0 18px;
      padding: 12px 16px;
      border-radius: 12px;
      font-size: 14px;
      line-height: 1.4;
      border: 1px solid rgba(255,255,255,0.08);
    }
    .trend-mode-ai {
      background: rgba(94, 234, 168, 0.10);
      border-color: rgba(94, 234, 168, 0.35);
      color: #bff3d6;
    }
    .trend-mode-local {
      background: rgba(255, 196, 94, 0.08);
      border-color: rgba(255, 196, 94, 0.30);
      color: #f4d9a6;
    }
    .trend-refreshing {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .spinner {
      width: 15px;
      height: 15px;
      border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.25);
      border-top-color: currentColor;
      animation: spin 0.8s linear infinite;
      flex: 0 0 auto;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .trend-card .topic-actions form {
      margin: 0;
    }
    .primary-action {
      display: inline-flex;
      min-height: 42px;
      align-items: center;
      color: #fff;
      background: var(--accent);
      border-radius: 999px;
      padding: 10px 16px;
      text-decoration: none;
      font-weight: 680;
      font-size: 13px;
      transition: background .16s ease;
    }
    .primary-action:hover { background: var(--accent-strong); }
    .ai-panel {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
      margin: 28px 0 0;
      padding: 20px 22px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      background: var(--paper);
      flex-wrap: wrap;
    }
    .ai-panel h2 {
      font-size: 17px;
    }
    .ai-panel p:not(.eyebrow) {
      color: var(--muted);
      margin-top: 6px;
      font-size: 14px;
    }
    .ai-running {
      background: rgba(240, 190, 90, .10);
    }
    .ai-error, .ai-not_configured {
      background: rgba(240, 96, 74, .10);
    }
    .ai-diagnostics {
      flex-basis: 100%;
      border-top: 1px solid var(--line-soft);
      padding-top: 12px;
    }
    .ai-diagnostics summary {
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      cursor: pointer;
    }
    .ai-diagnostics dl {
      display: grid;
      gap: 8px;
      margin: 12px 0 0;
    }
    .ai-diagnostics dl div {
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: 12px;
    }
    .ai-diagnostics dt {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
    }
    .ai-diagnostics dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
    .ai-result-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .ai-list {
      margin: 12px 0 0;
      padding-left: 18px;
    }
    .ai-list li + li {
      margin-top: 12px;
    }
    .ai-list span {
      color: var(--muted);
      font-size: 12px;
    }
    .empty-inline {
      color: var(--muted);
      margin-top: 10px;
    }
    .trend-list {
      display: grid;
      gap: 10px;
    }
    .trend-item {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 16px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: rgba(var(--paper-rgb), .72);
    }
    .trend-item p {
      color: var(--muted);
      margin-top: 6px;
    }
    .trend-item span {
      flex: 0 0 auto;
      height: fit-content;
      color: var(--accent);
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 5px 9px;
      background: var(--accent-soft);
      font-size: 12px;
      font-weight: 680;
    }
    .workflow-note {
      background: transparent;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 18px 0;
      margin: 18px 0 30px;
    }
    .workflow-note p:last-child {
      max-width: 880px;
      color: #303630;
    }
    .grid {
      display: grid;
      gap: 28px;
      margin: 34px 0;
    }
    .two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .block { min-width: 0; margin-top: 34px; }
    .secondary-details {
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      background: var(--paper);
      overflow: hidden;
    }
    .secondary-details > summary {
      list-style: none;
      cursor: pointer;
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 4px 12px;
      padding: 20px 22px;
    }
    .secondary-details > summary::-webkit-details-marker { display: none; }
    .secondary-details > summary::after {
      content: "▾";
      margin-left: auto;
      color: var(--muted);
      transition: transform .2s ease;
    }
    .secondary-details[open] > summary::after { transform: rotate(180deg); }
    .secondary-details > summary .s-eyebrow {
      flex-basis: 100%;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
    }
    .secondary-details > summary .s-title { font-size: 18px; font-weight: 680; color: var(--ink); }
    .secondary-details > summary .s-hint { color: var(--muted); font-size: 13px; }
    .secondary-body { padding: 0 22px 22px; }
    .today-why { grid-column: 1 / -1; }
    .today-why > summary { padding: 16px 18px; }
    .today-why .secondary-body { padding: 0 18px 18px; }
    .section-title {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 18px;
      margin-bottom: 18px;
    }
    .section-title span, .why, .draft-meta, .tags {
      color: var(--muted);
    }
    .stack-form { display: grid; gap: 4px; }
    .stack-form .section-title { margin-top: 34px; margin-bottom: 14px; }
    .stack-form > p:first-child { margin-bottom: 4px; }
    .stack-form label + label { margin-top: 18px; }
    .section-title span {
      font-size: 13px;
      white-space: nowrap;
    }
    .card-list, .draft-grid, .approval-grid {
      display: grid;
      gap: 18px;
    }
    .card, .draft, .approval, .memory {
      background: var(--paper);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      padding: 22px;
      box-shadow: var(--shadow);
      min-width: 0;
      transition: border-color .18s ease, transform .18s ease;
    }
    .card:hover, .draft:hover, .approval:hover {
      border-color: var(--line);
      transform: translateY(-1px);
    }
    .content-plan {
      background: var(--paper);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      padding: 24px;
      margin: 18px 0 34px;
      box-shadow: var(--shadow);
    }
    .compact-plan {
      margin-top: 0;
    }
    .week-list {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .week-item {
      display: grid;
      align-content: start;
      gap: 6px;
      padding: 14px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
      min-width: 0;
    }
    .week-item span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 680;
    }
    .week-item strong {
      font-size: 16px;
      color: var(--accent);
    }
    .week-item em {
      color: var(--accent);
      font-style: normal;
      font-size: 13px;
    }
    .week-item.is-today {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .week-item.is-empty {
      opacity: .82;
    }
    .week-publication.muted span {
      color: var(--muted);
      font-size: 13px;
    }
    .week-publication {
      display: grid;
      gap: 3px;
      padding-top: 8px;
      border-top: 1px solid var(--line-soft);
    }
    .week-publication:first-of-type {
      border-top: 0;
      padding-top: 2px;
    }
    .week-publication span {
      font-size: 13px;
      font-weight: 520;
      color: var(--ink);
    }
    .plan-focus {
      max-width: 860px;
      color: #2d332f;
      font-size: 17px;
      margin-bottom: 18px;
    }
    .plan-meta-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      padding: 16px 0;
      border-top: 1px solid var(--line-soft);
      border-bottom: 1px solid var(--line-soft);
    }
    .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .1em;
      margin-bottom: 8px;
    }
    .plan-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }
    .plan-item {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      padding: 14px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
    }
    .plan-item p {
      color: var(--muted);
      margin-top: 6px;
      font-size: 13px;
    }
    .plan-day, .plan-status {
      color: var(--muted);
      font-size: 12px;
      font-weight: 680;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .plan-status {
      height: fit-content;
      white-space: nowrap;
      color: var(--accent);
      background: var(--accent-soft);
      border-radius: 999px;
      padding: 4px 8px;
      text-transform: none;
      letter-spacing: 0;
    }
    .status-badge {
      width: fit-content;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 7px 11px;
      font-size: 13px;
      font-weight: 760;
      align-self: end;
    }
    .status-draft {
      color: #e0a76a;
      background: rgba(224, 167, 106, .14);
    }
    .status-approved {
      color: #86b7e6;
      background: rgba(134, 183, 230, .14);
    }
    .status-published {
      color: #7bcf9a;
      background: rgba(123, 207, 154, .14);
    }
    .strategy-grid {
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }
    .strategy-row {
      grid-template-columns: minmax(150px, .8fr) repeat(4, minmax(150px, 1fr));
      align-items: end;
    }
    .check-field {
      align-self: stretch;
      display: flex;
      align-items: center;
      gap: 10px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 10px 12px;
      background: var(--paper-soft);
      font-weight: 700;
    }
    .check-field input {
      width: 18px;
      height: 18px;
      accent-color: var(--accent);
    }
    .strategy-rules {
      margin-top: 16px;
      color: var(--muted);
    }
    .strategy-rules ul {
      margin: 12px 0 0;
      padding-left: 20px;
      display: grid;
      gap: 7px;
    }
    .today-reco {
      margin-top: 16px;
      color: var(--ink);
    }
    .card-head, .draft-meta, .approval-actions {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: center;
      flex-wrap: wrap;
    }
    .card-head h3 {
      margin: 0;
      min-width: 0;
      flex: 1 1 auto;
      overflow-wrap: anywhere;
      line-height: 1.35;
    }
    .card-head strong {
      color: var(--accent);
      background: var(--accent-soft);
      border-radius: 999px;
      min-width: 42px;
      flex-shrink: 0;
      text-align: center;
      padding: 5px 10px;
      font-size: 13px;
      line-height: 1.3;
    }
    .card p, .approval p { margin-top: 12px; line-height: 1.6; }
    .approval-actions { margin-top: 22px; padding-top: 20px; border-top: 1px solid var(--line-soft); }
    .approval h3, .card-head { margin-bottom: 4px; }
    .topic-actions { margin-top: 18px; }
    .refine { margin-top: 16px; }
    .draft-prep-list {
      gap: 18px;
    }
    .draft-prep-card pre {
      margin-top: 8px;
    }
    .draft-chain {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      margin: 4px 0 6px;
    }
    .chain-steps {
      display: inline-flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .chain-step {
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 680;
      color: var(--muted);
      background: var(--paper-soft);
      white-space: nowrap;
    }
    .chain-step.is-active {
      color: #fff;
      background: var(--accent);
      border-color: var(--accent);
    }
    .chain-step.is-done {
      color: var(--accent);
      background: var(--accent-soft);
      border-color: var(--line-soft);
    }
    .chain-sep { color: var(--muted); font-size: 12px; }
    .chain-link {
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      text-decoration: none;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 6px 12px;
      white-space: nowrap;
    }
    .chain-link:hover { border-color: var(--accent); }
    .draft-context-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin: 14px 0;
    }
    .draft-materials {
      margin-top: 16px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
      padding: 14px;
    }
    .draft-materials ul {
      margin: 0;
      padding-left: 18px;
      display: grid;
      gap: 8px;
    }
    .draft-materials li span {
      display: block;
      color: var(--muted);
      margin-top: 2px;
    }
    .action {
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--line-soft);
      color: #333832;
      font-weight: 620;
    }
    .refine {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 16px;
    }
    .topic-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
      align-items: center;
    }
    .topic-actions .inline-save, .topic-actions .refine {
      margin-top: 0;
    }
    .refine form, .approval-actions form {
      margin: 0;
    }
    button {
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      padding: 10px 16px;
      font: inherit;
      font-size: 13px;
      font-weight: 680;
      cursor: pointer;
      min-height: 42px;
      touch-action: manipulation;
      transition: background .16s ease, border-color .16s ease, color .16s ease;
    }
    button:hover { background: var(--accent-strong); }
    button.secondary, button.ghost {
      background: transparent;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    button.secondary:hover, button.ghost:hover {
      background: var(--paper-soft);
      border-color: var(--accent);
      color: var(--ink);
    }
    button.ghost {
      color: var(--muted);
      padding: 7px 12px;
      font-weight: 620;
    }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 14px;
      font-size: 12px;
    }
    .tags span, .draft-meta span {
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 4px 8px;
      background: var(--paper-soft);
    }
    .draft-grid, .approval-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .draft h3 { margin-top: 14px; }
    pre {
      margin: 16px 0 0;
      white-space: pre-wrap;
      font: inherit;
      color: var(--ink);
      background: var(--paper-soft);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 18px;
      overflow-wrap: anywhere;
    }
    .help-note {
      margin: 12px 0 4px;
      padding: 12px 14px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 13px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .state-note {
      margin-top: 12px;
      display: inline-flex;
      max-width: 100%;
      width: fit-content;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 6px 10px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      overflow-wrap: anywhere;
    }
    .error-note {
      background: rgba(240, 96, 74, .12);
      border-color: rgba(240, 96, 74, .35);
      color: #f0917d;
    }
    .error-detail {
      margin-top: 8px;
      font-size: 12px;
      opacity: .75;
    }
    .error-detail summary {
      cursor: pointer;
      font-weight: 640;
    }
    .risk { color: var(--risk); }
    .memory {
      margin-top: 34px;
    }
    .memory ul {
      margin: 14px 0 0;
      padding-left: 20px;
      color: var(--muted);
    }
    .notice {
      margin: 28px 0 0;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 14px 16px;
      font-weight: 680;
    }
    .profile-form {
      margin-top: 32px;
      display: grid;
      gap: 24px;
    }
    .knowledge-upload {
      margin: 32px 0;
      background: var(--paper);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      padding: 24px;
      box-shadow: var(--shadow);
    }
    .memory-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 24px 0;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line-soft);
    }
    .memory-tab {
      background: rgba(var(--paper-rgb), .72);
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 8px 16px;
      color: var(--ink);
      text-decoration: none;
      font-size: 14px;
      font-weight: 600;
      white-space: nowrap;
      transition: background .16s ease, border-color .16s ease, color .16s ease;
    }
    .memory-tab:hover { background: var(--paper-soft); }
    .memory-tab.active {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .card-actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .plan-edit-list {
      display: grid;
      gap: 10px;
    }
    .plan-row {
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
      overflow: hidden;
    }
    .plan-row.has-alert { border-color: rgba(240, 96, 74, .45); }
    .plan-row:hover { border-color: var(--line); }
    .plan-row > summary {
      list-style: none;
      cursor: pointer;
      display: grid;
      grid-template-columns: minmax(0, 190px) minmax(0, 96px) minmax(0, 1fr) minmax(0, 160px) 14px;
      grid-template-areas: "date platform topic status chev";
      align-items: start;
      gap: 14px 16px;
      padding: 15px 16px;
      min-width: 0;
    }
    .plan-row > summary::-webkit-details-marker { display: none; }
    .plan-row-date {
      grid-area: date;
      color: var(--muted);
      font-size: 13px;
      font-weight: 680;
      white-space: normal;
      overflow-wrap: anywhere;
      min-width: 0;
      line-height: 1.4;
    }
    .plan-row-platform {
      grid-area: platform;
      color: var(--accent);
      font-size: 13px;
      font-weight: 700;
      white-space: normal;
      overflow-wrap: anywhere;
      min-width: 0;
      line-height: 1.4;
    }
    .plan-row-topic {
      grid-area: topic;
      min-width: 0;
      white-space: normal;
      overflow-wrap: anywhere;
      line-height: 1.4;
      color: var(--ink);
    }
    .plan-row .status-badge {
      grid-area: status;
      justify-self: start;
      align-self: start;
      max-width: 100%;
      white-space: normal;
      text-align: center;
      line-height: 1.35;
    }
    .plan-row > summary::after {
      content: "▾";
      grid-area: chev;
      color: var(--muted);
      line-height: 1.4;
      transition: transform .2s ease;
    }
    .plan-row[open] > summary::after { transform: rotate(180deg); }
    .plan-row-body {
      padding: 6px 16px 18px;
      border-top: 1px solid var(--line-soft);
    }
    .plan-fields {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin: 16px 0;
    }
    .view-switch {
      display: inline-flex;
      gap: 6px;
      margin: 28px 0 0;
      padding: 4px;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      background: rgba(var(--paper-rgb), .72);
    }
    .view-switch a {
      color: var(--muted);
      text-decoration: none;
      border-radius: 999px;
      padding: 8px 13px;
      font-size: 13px;
      font-weight: 680;
      min-height: 40px;
      display: inline-flex;
      align-items: center;
    }
    .view-switch a.active {
      color: white;
      background: var(--accent);
    }
    .period-picker {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      align-items: end;
      margin: 18px 0 0;
      padding: 16px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      background: rgba(var(--paper-rgb), .72);
    }
    .period-picker input,
    .period-picker select {
      min-width: 0;
    }
    .calendar-block {
      margin-top: 26px;
    }
    .calendar-weekdays, .calendar-grid {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 8px;
    }
    .calendar-weekdays {
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
    }
    .calendar-weekdays span {
      padding: 0 8px;
    }
    .calendar-day {
      min-height: 118px;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 10px;
      background: rgba(var(--paper-rgb), .72);
      min-width: 0;
    }
    .calendar-day.muted {
      background: transparent;
      border-style: dashed;
    }
    .calendar-day > strong {
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      letter-spacing: .02em;
    }
    .calendar-empty {
      color: var(--line);
      font-size: 13px;
    }
    .calendar-date {
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    .calendar-publication {
      display: grid;
      gap: 2px;
      margin-top: 6px;
      padding: 8px;
      border-radius: var(--radius-sm);
      background: var(--accent-soft);
      color: var(--ink);
      text-decoration: none;
      font-size: 12px;
      overflow-wrap: break-word;
      word-break: normal;
      hyphens: auto;
    }
    .calendar-publication summary {
      display: grid;
      gap: 3px;
      cursor: pointer;
      list-style: none;
    }
    .calendar-publication summary::-webkit-details-marker {
      display: none;
    }
    .calendar-publication span {
      color: var(--accent);
      font-weight: 760;
    }
    .calendar-publication b {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .calendar-publication p {
      margin: 8px 0 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .calendar-publication a {
      color: var(--accent);
      font-weight: 680;
      text-decoration: none;
    }
    .edit-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }
    .card-list + .plan-item,
    .card-list + .profile-section {
      margin-top: 18px;
    }
    .plan-item.edit-row {
      margin-top: 18px;
      align-content: start;
    }
    .plan-item.edit-row h3 {
      grid-column: 1 / -1;
      margin: 0 0 2px;
    }
    .inline-editor {
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--line-soft);
    }
    .inline-editor > summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      font-weight: 680;
      list-style: none;
    }
    .inline-editor > summary::-webkit-details-marker { display: none; }
    .inline-editor .edit-row,
    .inline-editor label,
    .inline-editor form {
      margin-top: 14px;
    }
    .mode-hint {
      color: var(--muted);
      font-size: 0.92rem;
      margin: 4px 0 12px;
    }
    .editor-label { display: block; }
    .editor-label span {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .editor-label textarea {
      width: 100%;
      min-height: 320px;
      resize: vertical;
      line-height: 1.6;
      font-size: 15px;
      font-weight: 400;
    }
    .editor-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin: 8px 0 4px;
    }
    .editor-meta {
      color: var(--muted);
      font-size: 0.85rem;
      margin: 0;
    }
    .editor-bar-actions { display: flex; gap: 10px; flex-wrap: wrap; }
    .ai-revise {
      border: 1px solid var(--line-soft);
      border-radius: 12px;
      padding: 14px 16px;
      margin: 14px 0 4px;
      background: var(--surface-soft, rgba(0,0,0,0.02));
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .ai-revise textarea { min-height: 68px; }
    .ai-revise .brief-hint { margin: 0; }
    .ai-revise button { align-self: flex-start; }
    .text-brief {
      border: 1px solid var(--line-soft);
      border-left: 3px solid var(--accent);
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
      padding: 14px 16px;
      margin-bottom: 16px;
    }
    .text-brief .eyebrow { margin-bottom: 4px; }
    .brief-hint { color: var(--muted); font-size: 13px; margin: 0 0 10px; }
    .brief-lines { display: grid; gap: 6px; }
    .brief-lines p { font-size: 14px; line-height: 1.5; color: var(--muted); }
    .brief-lines b { color: var(--ink); font-weight: 640; margin-right: 4px; }
    body.focus-on .text-brief { display: none; }
    /* ===== Focus Mode: distraction-free writing ===== */
    .focus-bar { display: none; }
    body.focus-on { padding-left: 0; }
    body.focus-on .sidebar,
    body.focus-on .burger,
    body.focus-on .topbar,
    body.focus-on .form-grid,
    body.focus-on .mode-hint,
    body.focus-on .editor-bar,
    body.focus-on .form-actions { display: none; }
    body.focus-on .shell { width: min(760px, calc(100% - 32px)); padding-top: 12px; }
    body.focus-on .profile-form { margin-top: 0; }
    body.focus-on .profile-section {
      background: transparent;
      border: 0;
      box-shadow: none;
      padding: 0;
    }
    body.focus-on .editor-label span { display: none; }
    body.focus-on .editor-label textarea {
      min-height: 82vh;
      font-size: 17px;
      line-height: 1.7;
      border: 0;
      background: transparent;
      padding: 8px 0;
    }
    body.focus-on .editor-label textarea:focus,
    body.focus-on .editor-label textarea:focus-visible { outline: none; border: 0; }
    body.focus-on .focus-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 30;
      padding: 12px 0;
      margin-bottom: 6px;
      background: var(--bg);
      border-bottom: 1px solid var(--line-soft);
    }
    .pointer-note {
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.5;
      padding: 12px 14px;
      border: 1px dashed rgba(120,120,120,0.35);
      border-radius: 12px;
      background: rgba(120,120,120,0.06);
    }
    .pointer-note a { font-weight: 680; }
    .repeat-note { color: #f0c975; background: rgba(240,190,90,0.12); border-radius: var(--radius-sm); padding: 6px 10px; }
    .hw-legend { display: flex; flex-wrap: wrap; gap: 10px 18px; margin: 14px 0 20px; color: var(--muted); font-size: 0.9rem; }
    .hw-flow { display: flex; flex-direction: column; align-items: stretch; }
    .hw-stage { display: flex; gap: 14px; padding: 16px; border: 1px solid rgba(120,120,120,0.2); border-radius: 14px; background: rgba(120,120,120,0.04); }
    .hw-icon { flex: 0 0 auto; font-size: 1.1rem; white-space: nowrap; }
    .hw-body { flex: 1 1 auto; }
    .hw-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; }
    .hw-head h3 { margin: 0; }
    .hw-where a { font-weight: 680; }
    .hw-where .hw-auto, .hw-auto { color: var(--muted); font-size: 0.88rem; }
    .hw-what { margin: 6px 0 4px; }
    .hw-feeds { color: var(--muted); font-size: 0.92rem; margin: 0; }
    .hw-arrow { text-align: center; color: var(--muted); font-size: 1.1rem; line-height: 1.6; }
    .gates-banner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      padding: 14px 18px;
      border-radius: var(--radius);
      background: rgba(240,190,90,0.12);
      border: 1px solid rgba(240,190,90,0.35);
    }
    .gates-text strong { display: block; color: #f0c975; }
    .gates-text span { color: #c9a960; font-size: 0.92rem; }
    .gates-action {
      flex: 0 0 auto;
      font-weight: 680;
      text-decoration: none;
      padding: 9px 16px;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
    }
    .publish-reminder {
      padding: 16px 18px;
      border-radius: var(--radius);
      background: var(--paper);
      border: 1px solid var(--line);
      margin-bottom: 4px;
    }
    .reminder-head strong { display: block; color: var(--ink); }
    .reminder-head span { color: var(--muted); font-size: 0.9rem; }
    .reminder-list { margin-top: 12px; display: grid; gap: 8px; }
    .reminder-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      padding: 10px 12px;
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
      border: 1px solid var(--line-soft);
    }
    .reminder-info { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
    .reminder-date { color: var(--accent); font-weight: 760; font-size: 0.85rem; }
    .reminder-topic { color: var(--ink); overflow-wrap: anywhere; }
    .reminder-row button {
      flex: 0 0 auto;
      font-weight: 680;
      padding: 8px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--accent-soft);
      color: var(--accent);
      cursor: pointer;
    }
    .reminder-more { margin-top: 6px; color: var(--muted); font-size: 0.88rem; }
    .mode-list {
      display: grid;
      gap: 10px;
      margin-bottom: 8px;
    }
    .mode-row {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .mode-index {
      flex: 0 0 auto;
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: var(--surface-2, rgba(120,120,120,0.12));
      color: var(--muted);
      font-size: 0.85rem;
      font-weight: 680;
    }
    .mode-row input {
      flex: 1 1 auto;
    }
    .knowledge-upload p {
      color: var(--muted);
      margin-top: 8px;
    }
    .knowledge-upload form {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 16px;
    }
    .inline-save {
      margin-top: 14px;
    }
    .knowledge-list {
      display: grid;
      gap: 16px;
    }
    .knowledge-card, .document-view {
      background: rgba(var(--paper-rgb), .86);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius-sm);
      padding: 18px;
    }
    .knowledge-card {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      min-width: 0;
    }
    .knowledge-card > div:first-child {
      min-width: 0;
      flex: 1 1 auto;
    }
    .knowledge-card > form,
    .knowledge-card .card-actions {
      flex-shrink: 0;
    }
    .knowledge-card a {
      color: var(--ink);
      text-decoration: none;
    }
    .knowledge-card p {
      color: var(--muted);
      margin-top: 8px;
    }
    .text-list {
      margin-top: 28px;
    }
    .text-filter {
      grid-template-columns: minmax(220px, 1fr) minmax(180px, max-content) max-content max-content;
    }
    .text-row {
      align-items: center;
    }
    .text-row h3 {
      margin: 0;
      font-size: 17px;
      line-height: 1.35;
    }
    .text-row .doc-meta {
      margin-bottom: 0;
    }
    .secondary-link {
      color: var(--muted);
      font-size: 13px;
      font-weight: 680;
      text-decoration: none;
      align-self: center;
      padding: 10px 0;
    }
    .pagination {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 14px;
      margin-top: 20px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 680;
    }
    .pagination a, .pagination span {
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 8px 12px;
      text-decoration: none;
      color: var(--ink);
      min-width: 78px;
      text-align: center;
    }
    .pagination span {
      color: var(--muted);
      background: var(--paper-soft);
    }
    .compact-editor {
      margin-top: 16px;
    }
    .doc-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin: 14px 0;
      color: var(--muted);
      font-size: 12px;
    }
    .doc-meta span {
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      padding: 4px 8px;
      background: var(--paper-soft);
    }
    .doc-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0 16px;
    }
    .document-view pre {
      max-height: 520px;
      overflow: auto;
    }
    .empty {
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: var(--radius-sm);
      padding: 24px;
      background: rgba(var(--paper-rgb),.35);
      text-align: center;
    }
    .danger, .danger-text {
      color: var(--risk);
      border-color: rgba(140, 70, 55, .3);
    }
    .profile-section {
      background: var(--paper);
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      padding: 24px;
      box-shadow: var(--shadow);
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }
    label {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 680;
      min-width: 0;
    }
    input, textarea {
      width: 100%;
      max-width: 100%;
      min-width: 0;
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      background: var(--paper-soft);
      color: var(--ink);
      padding: 11px 12px;
      font: inherit;
      resize: vertical;
      min-height: 44px;
      transition: border-color .16s ease;
    }
    /* iOS renders native date/month pickers with a large intrinsic width; keep them inside the card. */
    input[type="date"], input[type="month"], input[type="time"] {
      -webkit-appearance: none;
      appearance: none;
    }
    input::placeholder, textarea::placeholder { color: color-mix(in srgb, var(--muted) 70%, transparent); }
    input:focus, textarea:focus, select:focus { border-color: var(--accent); }
    select {
      width: 100%;
      max-width: 100%;
      min-width: 0;
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--paper-soft);
      color: var(--ink);
      padding: 9px 12px;
      font: inherit;
      min-height: 44px;
      transition: border-color .16s ease;
    }
    .form-actions {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }
    @media (min-width: 641px) and (max-width: 1100px) {
      .shell { width: min(100% - 40px, 960px); }
      .period-picker { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .plan-row > summary {
        grid-template-columns: 1fr auto;
        grid-template-areas:
          "topic topic"
          "date chev"
          "platform status";
        gap: 8px 12px;
        align-items: center;
      }
      .plan-row-topic { font-weight: 600; }
      .plan-row .status-badge { justify-self: end; }
      .hero-cards, .today-details, .week-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .calendar-weekdays { display: none; }
      .calendar-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .calendar-day.muted { display: none; }
      .today-card { grid-template-columns: 1fr; }
      .draft-grid, .approval-grid, .plan-list, .form-grid, .edit-row, .ai-result-grid, .draft-context-grid, .score-grid, .text-filter, .plan-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .plan-meta-grid { grid-template-columns: 1fr; }
      .knowledge-card {
        flex-direction: column;
        align-items: stretch;
      }
    }
    @media (max-width: 640px) {
      .shell { width: min(100% - 28px, 1120px); padding-top: 28px; }
      .topbar, .section-title { align-items: flex-start; }
      .topbar { display: grid; }
      .meta { justify-content: flex-start; }
      .two, .draft-grid, .approval-grid { grid-template-columns: 1fr; }
      .plan-meta-grid, .plan-list, .form-grid, .hero-cards, .edit-row, .today-card, .today-details, .week-list, .ai-result-grid, .draft-context-grid, .score-grid, .text-filter, .plan-fields { grid-template-columns: 1fr; }
      .plan-row > summary {
        grid-template-columns: 1fr auto;
        grid-template-areas:
          "topic topic"
          "date chev"
          "platform status";
        gap: 8px 12px;
        align-items: center;
      }
      .plan-row-topic { font-weight: 600; }
      .plan-row-date,
      .plan-row-platform { font-size: 12px; }
      .plan-row .status-badge { justify-self: end; font-size: 12px; padding: 6px 9px; }
      .knowledge-card {
        flex-direction: column;
        align-items: stretch;
      }
      .knowledge-card .card-actions,
      .knowledge-card > form {
        justify-content: flex-start;
      }
      .calendar-weekdays { display: none; }
      .calendar-grid { grid-template-columns: 1fr; }
      .calendar-day { min-height: auto; }
      .calendar-day.muted { display: none; }
      .calendar-publication { font-size: 13px; }
      .period-picker { grid-template-columns: 1fr; }
      .period-picker input { min-width: 0; }
      .summary { padding-top: 28px; }
      h1 { font-size: 42px; }
      .section-title { display: grid; }
      .section-title span { white-space: normal; }
      .ai-panel, .trend-item { display: grid; }
      .today-publication { display: grid; }
      .ai-diagnostics dl div { grid-template-columns: 1fr; gap: 2px; }
      .approval-actions, .refine, .form-actions, .today-actions, .topic-actions { display: grid; grid-template-columns: 1fr; }
      .approval-actions button, .refine button, .form-actions button, .form-actions a, .today-actions button, .today-actions a, .topic-actions button, .ai-panel button { width: 100%; justify-content: center; text-align: center; }
      .summary-card { min-height: auto; }
    }
    """
