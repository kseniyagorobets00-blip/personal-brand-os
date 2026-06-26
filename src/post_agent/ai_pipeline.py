from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import threading
from typing import Any
import re

from .ai_gateway import AIGateway, AIGatewayError, DEFAULT_ENV_PATH, load_ai_config
from .author_brain import FORBIDDEN_OPENINGS, AuthorBrain
from .author_profile import AuthorProfileRepository
from .daily_brief import ROOT, SeedRepository
from .idea_vault import IdeaVault
from .knowledge import KnowledgeBase
from .knowledge_graph import KnowledgeGraph
from .learning import LearningCenter, lessons_for_prompt
from .memory import MemoryInbox
from .thinking_engine import ThinkingEngine
from .writing_dna import WritingDNARepository


DEFAULT_AI_DIR = ROOT / "data" / "ai"
DEFAULT_AI_RESULT_PATH = DEFAULT_AI_DIR / "daily_brief_ai.json"
DEFAULT_AI_STATUS_PATH = DEFAULT_AI_DIR / "status.json"
DEFAULT_AI_ACTION_ERRORS_PATH = DEFAULT_AI_DIR / "action_errors.json"
_PIPELINE_LOCK = threading.Lock()
THINKING_ENGINE_PROMPT_RULE = (
    "Use the Thinking Engine result before writing. Do not jump directly from context to draft generation. "
    "Architecture: Knowledge -> Memory Inbox -> Knowledge Graph -> Author Brain -> Writing DNA -> Thinking Engine -> Prompt Builder -> AI Gateway -> AI. "
    "AI is not memory; it only reads structured memory objects and produces the requested result. "
)
THINKING_ENGINE_USER_RULE = (
    "Before writing, follow context.thinking_engine: selected mode, strongest angle, relevant case or realistic work situation, format recommendation, and transparency. "
    "Use context.lessons only if lessons were accepted by the user. Pending Memory Inbox items are not trusted memory yet. "
    "Return thinking_transparency as a list explaining why this draft was written this way. "
)


@dataclass(frozen=True)
class AIPipelineStatus:
    state: str
    message: str
    updated_at: str
    error: str = ""


class AIPipeline:
    def __init__(
        self,
        gateway: AIGateway | None = None,
        seed_repository: SeedRepository | None = None,
        knowledge_base: KnowledgeBase | None = None,
        idea_vault: IdeaVault | None = None,
        author_profile_repository: AuthorProfileRepository | None = None,
        writing_dna_repository: WritingDNARepository | None = None,
        memory_inbox: MemoryInbox | None = None,
        knowledge_graph: KnowledgeGraph | None = None,
        learning_center: LearningCenter | None = None,
        thinking_engine: ThinkingEngine | None = None,
        result_path: Path = DEFAULT_AI_RESULT_PATH,
        status_path: Path = DEFAULT_AI_STATUS_PATH,
    ) -> None:
        self.gateway = gateway or AIGateway()
        self.seed_repository = seed_repository or SeedRepository()
        self.knowledge_base = knowledge_base or KnowledgeBase()
        self.idea_vault = idea_vault or IdeaVault()
        self.author_profile_repository = author_profile_repository or AuthorProfileRepository()
        self.writing_dna_repository = writing_dna_repository or WritingDNARepository()
        self.memory_inbox = memory_inbox or MemoryInbox()
        self.knowledge_graph = knowledge_graph or KnowledgeGraph()
        self.learning_center = learning_center or LearningCenter()
        self.thinking_engine = thinking_engine or ThinkingEngine()
        self.result_path = result_path
        self.status_path = status_path
        self.cache = AICache(result_path=result_path, status_path=status_path)
        self.result_path.parent.mkdir(parents=True, exist_ok=True)

    def start_background(self) -> bool:
        if _PIPELINE_LOCK.locked():
            return False
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return True

    def run(self) -> dict[str, Any] | None:
        if not _PIPELINE_LOCK.acquire(blocking=False):
            self._write_status("running", "AI анализ уже выполняется.")
            return None
        try:
            self._write_status("running", "AI анализирует данные...")
            if not self.gateway.is_configured():
                self._write_status("not_configured", "ProxyAPI не настроен.", "Заполните .env и запустите анализ повторно.")
                return None

            context = self._build_context()
            response = self.gateway.complete_json(_system_prompt(), _user_prompt(context))
            result = self._normalize_response(response)
            if _needs_revision(result):
                revised = self.gateway.complete_json(_revision_system_prompt(), _revision_prompt(context, result))
                result = self._normalize_response({**response, **revised})
            result["thinking_engine"] = context.get("thinking_engine", {})
            result["thinking_transparency"] = (context.get("thinking_engine", {}) or {}).get("transparency", [])
            self.cache.write_result(result)
            self.cache.write_status("completed", "AI-анализ обновлен.")
            return result
        except AIGatewayError as exc:
            self._write_status("error", "ProxyAPI недоступен. Оставлен предыдущий Daily Brief.", str(exc))
            return None
        finally:
            _PIPELINE_LOCK.release()

    def _build_context(self) -> dict[str, Any]:
        self.knowledge_base.ensure_seed_documents()
        documents = self.knowledge_base.list_documents()
        cases = self.knowledge_base.list_cases()
        ideas = self.idea_vault.list_ideas()
        content_plan = self.seed_repository.load_content_plan()
        publication = _target_publication(content_plan)
        graph = self.knowledge_graph.rebuild(
            documents=documents,
            cases=cases,
            memory_items=self.memory_inbox.list_items(),
        )
        query = " ".join(str(publication.get(key, "")) for key in ("platform", "topic", "summary", "goal")) if publication else ""
        author_brain = AuthorBrain(
            author_profile=self.author_profile_repository.load_raw(),
            writing_dna=self.writing_dna_repository.load_raw(),
            documents=documents,
            cases=cases,
            ideas=ideas,
        ).build(publication)
        context = {
            "content_plan": content_plan,
            "target_publication": publication,
            "local_sources": self.seed_repository.load(),
            "memory_inbox": [item.__dict__ for item in self.memory_inbox.list_items("pending")[:8]],
            "knowledge_graph": graph,
            "knowledge_graph_links": self.knowledge_graph.related_to(query),
            "author_brain": author_brain,
            "lessons": lessons_for_prompt(self.learning_center.list_lessons("accepted")),
        }
        context["thinking_engine"] = self.thinking_engine.think(context)
        return context

    def _normalize_response(self, response: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {
            "generated_at": now,
            "model": load_ai_config().model,
            "main_topic": str(response.get("main_topic", response.get("daily_recommendation", ""))),
            "publication_goal": str(response.get("publication_goal", "")),
            "main_idea": str(response.get("main_idea", "")),
            "daily_recommendation": str(response.get("daily_recommendation", "")),
            "choice_reason": str(response.get("choice_reason", "")),
            "why_today": str(response.get("why_today", response.get("choice_reason", ""))),
            "recommended_materials": _as_list(response.get("recommended_materials", [])),
            "ideas": _as_list(response.get("ideas", [])),
            "draft": _clean_draft_text(str(response.get("draft", ""))),
            "thinking_mode": str(response.get("thinking_mode", "")),
            "author_fit_score": str(response.get("author_fit_score", "")),
            "author_fit_notes": str(response.get("author_fit_notes", "")),
            "thinking_engine": response.get("thinking_engine", {}),
            "thinking_transparency": _as_list(response.get("thinking_transparency", [])),
            "raw_response": response,
        }

    def _write_status(self, state: str, message: str, error: str = "") -> None:
        self.cache.write_status(state, message, error)


class AICache:
    """Reusable cache contract for AI modules. Implemented for Daily Brief first."""

    def __init__(self, result_path: Path = DEFAULT_AI_RESULT_PATH, status_path: Path = DEFAULT_AI_STATUS_PATH) -> None:
        self.result_path = result_path
        self.status_path = status_path
        self.result_path.parent.mkdir(parents=True, exist_ok=True)

    def read_result(self) -> dict[str, Any] | None:
        return load_ai_result(self.result_path)

    def read_status(self) -> AIPipelineStatus:
        return load_ai_status(self.status_path)

    def write_result(self, result: dict[str, Any]) -> None:
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_status(self, state: str, message: str, error: str = "") -> None:
        status = {
            "state": state,
            "message": message,
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def force_refresh(self, pipeline: AIPipeline) -> dict[str, Any] | None:
        return pipeline.run()


def load_ai_result(path: Path = DEFAULT_AI_RESULT_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def load_ai_status(path: Path = DEFAULT_AI_STATUS_PATH) -> AIPipelineStatus:
    if not path.exists():
        config = load_ai_config()
        if not config.is_configured:
            return AIPipelineStatus("not_configured", "ProxyAPI не настроен.", "")
        return AIPipelineStatus("idle", "AI-анализ еще не запускался.", "")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return AIPipelineStatus("error", "Статус AI-анализа поврежден.", "")
    return AIPipelineStatus(
        state=str(raw.get("state", "idle")),
        message=str(raw.get("message", "")),
        updated_at=str(raw.get("updated_at", "")),
        error=str(raw.get("error", "")),
    )


def ai_diagnostics(status_path: Path = DEFAULT_AI_STATUS_PATH) -> dict[str, object]:
    config = load_ai_config()
    status = load_ai_status(status_path)
    return {
        "python_executable": sys.executable,
        "cwd": str(Path.cwd().resolve()),
        "env_path": str(DEFAULT_ENV_PATH.resolve()),
        "env_loaded": DEFAULT_ENV_PATH.exists(),
        "proxy_configured": config.is_configured,
        "model": config.model,
        "last_error": status.error,
        "last_action_error": _last_action_error(),
    }


def _last_action_error(path: Path = DEFAULT_AI_ACTION_ERRORS_PATH) -> str:
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    if not isinstance(raw, list) or not raw:
        return ""
    first = raw[0] if isinstance(raw[0], dict) else {}
    return str(first.get("error", ""))


def _system_prompt() -> str:
    return (
        THINKING_ENGINE_PROMPT_RULE
        +
        "Ты AI Chief Content Officer для Personal Brand OS. "
        "Ты пишешь не универсальный экспертный текст, а публикацию конкретного автора: Ксении. "
        "Сначала используй Author Brain как модель мышления автора, затем Writing DNA внутри Author Brain как стиль рассуждения, потом пиши. "
        "Не работай напрямую с сырой памятью как с набором фактов: используй author_brain из контекста. "
        "Ответь строго JSON-объектом."
    )


def _user_prompt(context: dict[str, Any]) -> str:
    return (
        THINKING_ENGINE_USER_RULE
        +
        "Проанализируй данные Personal Brand OS и верни JSON с полями: "
        "main_topic, daily_recommendation, choice_reason, publication_goal, main_idea, why_today, recommended_materials, ideas, "
        "thinking_mode, author_fit_score, author_fit_notes, draft. "
        "recommended_materials должен быть массивом объектов с title, type, reason. "
        "ideas должен быть массивом коротких идей. "
        "Выбери thinking_mode из author_brain.allowed_thinking_modes. "
        "author_brain.writing_dna отвечает за КАК мыслит и пишет автор: наблюдение рождает тему, абзацы естественные, голос живой, без шаблонности. "
        "Если author_brain.case_candidates содержит подходящий кейс, сначала попробуй использовать его. "
        "Если подходящего кейса нет, не придумывай искусственный кейс. "
        "draft должен быть готовым постом на русском языке, написанным через Author Brain. "
        "Представь, что Ксения утром решила написать наблюдение после рабочего разговора, аудита или проекта. "
        "Пиши так, как если бы она действительно села писать сама. "
        "Пиши draft как естественный авторский текст, 400-800 слов для LinkedIn/VC "
        "или уместный для платформы объем для Telegram/Сетка. "
        "draft не должен быть планом, техническим заданием, описанием статьи, брифом или списком того, что нужно написать. "
        "Поля вроде publication_goal, main_idea, choice_reason используй только для отдельных JSON-полей, не вставляй их в draft. "
        "Текст должен ощущаться живым: наблюдение, рабочая ситуация, маленькая история, личное размышление, сомнение, вопрос читателю или неожиданное сравнение. "
        "Если кейса нет, можно использовать типичную жизненную рабочую ситуацию без реальных компаний, цифр, вымышленных фактов и несуществующих проектов. "
        "Не превращай текст в учебник. "
        "В draft запрещены технические комментарии, объяснение структуры, перечисление правил, служебные формулировки, "
        "маркеры 'Стиль:', 'Структура:', 'Правило платформы:', 'Без определения темы', 'Можно:', "
        "'Цель публикации:', 'Основная мысль:', 'Краткая структура:'. "
        "Нельзя начинать draft с запрещенных вступлений из author_brain.forbidden_openings. "
        "Перед финальным JSON выполни внутреннюю самопроверку 'Похоже ли это на Ксению?'. "
        "Проверь критерии из author_brain.writing_dna.self_check. "
        "Если author_fit_score ниже 8 из 10 или текст звучит как AI, выполни одну внутреннюю итерацию улучшения и верни улучшенную версию. "
        "Для LinkedIn, Telegram и Сетка не используй заголовки внутри текста; для VC подзаголовки допустимы. "
        "Не придумывай внешние новости: работай только с внутренними данными продукта.\n\n"
        f"Данные:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _revision_system_prompt() -> str:
    return (
        "Ты редактор Author Brain. Перепиши только draft так, чтобы он звучал как живая публикация Ксении, "
        "без AI-клише, служебных блоков и учебникового тона. Ответь строго JSON."
    )


def _revision_prompt(context: dict[str, Any], result: dict[str, Any]) -> str:
    return (
        "Текущий draft слабый или содержит запрещенное вступление/служебные маркеры. "
        "Перепиши draft одной улучшенной версией. Сохрани смысл и используй author_brain. "
        "Верни JSON с полями draft, thinking_mode, author_fit_score, author_fit_notes.\n\n"
        f"Author Brain:\n{json.dumps(context.get('author_brain', {}), ensure_ascii=False, indent=2)}\n\n"
        f"Текущий результат:\n{json.dumps(result, ensure_ascii=False, indent=2)}"
    )


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _target_publication(content_plan: dict[str, Any]) -> dict[str, object]:
    publications = content_plan.get("planned_publications", [])
    if not isinstance(publications, list) or not publications:
        return {}
    today = datetime.now().strftime("%d.%m.%Y")
    for publication in publications:
        if isinstance(publication, dict) and str(publication.get("date", "")) == today:
            return publication
    for publication in publications:
        if isinstance(publication, dict) and str(publication.get("status", "")) not in {"published", "skipped", "archived"}:
            return publication
    return publications[0] if isinstance(publications[0], dict) else {}


def _needs_revision(result: dict[str, Any]) -> bool:
    draft = str(result.get("draft", ""))
    if not draft:
        return False
    if _starts_with_forbidden_opening(draft):
        return True
    if _contains_forbidden_marker(draft):
        return True
    score = _score_as_int(result.get("author_fit_score", ""))
    return score is not None and score < 8


def _starts_with_forbidden_opening(text: str) -> bool:
    first = re.sub(r"\s+", " ", text.strip().split("\n", 1)[0]).lower()
    return any(first.startswith(opening) for opening in FORBIDDEN_OPENINGS)


def _score_as_int(value: object) -> int | None:
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))


FORBIDDEN_DRAFT_MARKERS = (
    "Стиль:",
    "Структура:",
    "Правило платформы:",
    "Правило:",
    "Без определения темы",
    "Можно:",
    "Нельзя:",
    "Author Profile",
    "Platform Rules",
    "Цель публикации:",
    "Основная мысль:",
    "Краткая структура:",
    "Техническое задание:",
    "Описание статьи:",
)


def _clean_draft_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    lines = cleaned.splitlines()
    safe_lines: list[str] = []
    skipping_service_block = False
    for line in lines:
        stripped = line.strip()
        if _contains_forbidden_marker(stripped):
            skipping_service_block = True
            continue
        if skipping_service_block:
            if not stripped or re.match(r"^[-*\d.)\s]+", stripped):
                continue
            skipping_service_block = False
        safe_lines.append(line)
    cleaned = "\n".join(safe_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _contains_forbidden_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in FORBIDDEN_DRAFT_MARKERS)
