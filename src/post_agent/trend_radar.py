from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from .daily_brief import ROOT
from .learning import LearningCenter


DEFAULT_TREND_DIR = ROOT / "data" / "trend_radar"
DEFAULT_TREND_CACHE_PATH = DEFAULT_TREND_DIR / "cache.json"
DEFAULT_TREND_DECISIONS_PATH = DEFAULT_TREND_DIR / "decisions.json"
DEFAULT_TREND_SEED_PATH = ROOT / "data" / "seeds" / "trend_sources.json"
TREND_CACHE_TTL_MINUTES = 30


@dataclass(frozen=True)
class TrendTopic:
    id: str
    title: str
    description: str
    source: str
    why_now: str
    hype_level: str
    relevance_forecast: str
    reach_score: float
    brand_fit_score: float
    ai_reason: str
    matching_cases: tuple[str, ...]
    knowledge_materials: tuple[str, ...]
    best_formats: tuple[str, ...]
    status: str
    created_at: str


class TrendRadar:
    """Cached editorial radar. Providers can later be replaced with real external sources."""

    def __init__(
        self,
        cache_path: Path = DEFAULT_TREND_CACHE_PATH,
        decisions_path: Path = DEFAULT_TREND_DECISIONS_PATH,
        seed_path: Path = DEFAULT_TREND_SEED_PATH,
        ttl_minutes: int = TREND_CACHE_TTL_MINUTES,
        learning_center: LearningCenter | None = None,
    ) -> None:
        self.cache_path = cache_path
        self.decisions_path = decisions_path
        self.seed_path = seed_path
        self.ttl_minutes = _env_int("TREND_RADAR_CACHE_TTL_MINUTES", ttl_minutes)
        self.learning_center = learning_center or LearningCenter()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache_path.exists():
            self._write_cache({"generated_at": "", "expires_at": "", "topics": [], "sources": []})
        if not self.decisions_path.exists():
            self._write_decisions([])
        if not self.seed_path.exists():
            self.seed_path.parent.mkdir(parents=True, exist_ok=True)
            self.seed_path.write_text(json.dumps(DEFAULT_TREND_SOURCES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def get_cached(self) -> dict[str, object]:
        return self._read_cache()

    def is_stale(self) -> bool:
        raw = self._read_cache()
        expires_at = _parse_iso(str(raw.get("expires_at", "")))
        return expires_at is None or datetime.now(timezone.utc) >= expires_at

    def refresh(
        self,
        content_plan: dict[str, object],
        documents: list[object],
        cases: list[object],
        ideas: list[object],
        graph_links: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        sources = self._load_sources()
        topics = [
            self._build_topic(source, content_plan, documents, cases, ideas, graph_links or [])
            for source in sources
        ]
        filtered = sorted(topics, key=lambda item: (item.reach_score * 0.55 + item.brand_fit_score * 0.45), reverse=True)[:8]
        generated_at = datetime.now(timezone.utc)
        cache = {
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "expires_at": (generated_at + timedelta(minutes=self.ttl_minutes)).isoformat(timespec="seconds"),
            "sources": sorted({topic.source for topic in filtered}),
            "topics": [self._to_raw(topic) for topic in filtered],
        }
        self._write_cache(cache)
        return cache

    def apply_decision(self, topic_id: str, action: str) -> bool:
        cache = self._read_cache()
        topics = cache.get("topics", [])
        if not isinstance(topics, list):
            return False
        changed = False
        target: dict[str, object] | None = None
        for topic in topics:
            if isinstance(topic, dict) and topic.get("id") == topic_id:
                topic["status"] = action
                target = topic
                changed = True
                break
        if not changed or not target:
            return False
        self._write_cache(cache)
        decision = {
            "id": uuid4().hex,
            "topic_id": topic_id,
            "title": str(target.get("title", "")),
            "action": action,
            "reach_score": target.get("reach_score", 0),
            "brand_fit_score": target.get("brand_fit_score", 0),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        raw = self._read_decisions()
        raw.insert(0, decision)
        self._write_decisions(raw)
        self._maybe_create_lesson(raw, decision)
        return True

    def get_topic(self, topic_id: str) -> dict[str, object] | None:
        topics = self._read_cache().get("topics", [])
        if not isinstance(topics, list):
            return None
        for topic in topics:
            if isinstance(topic, dict) and topic.get("id") == topic_id:
                return topic
        return None

    def decisions(self) -> list[dict[str, object]]:
        return self._read_decisions()

    def _build_topic(
        self,
        source: dict[str, object],
        content_plan: dict[str, object],
        documents: list[object],
        cases: list[object],
        ideas: list[object],
        graph_links: list[dict[str, str]],
    ) -> TrendTopic:
        title = str(source.get("title", ""))
        description = str(source.get("description", ""))
        tags = _as_list(source.get("tags", []))
        content_text = " ".join((title, description, " ".join(tags)))
        plan_bonus = _plan_fit_bonus(content_text, content_plan)
        knowledge_matches = _matching_documents(content_text, documents)
        case_matches = _matching_cases(content_text, cases)
        idea_bonus = _idea_bonus(content_text, ideas)
        graph_bonus = min(0.6, len(graph_links) * 0.1)
        reach = min(10.0, float(source.get("reach_base", 6.5)) + _controversy_bonus(source) + idea_bonus)
        brand = min(10.0, float(source.get("brand_base", 6.5)) + plan_bonus + len(knowledge_matches) * 0.25 + len(case_matches) * 0.45 + graph_bonus)
        return TrendTopic(
            id=str(source.get("id") or _slug(title)),
            title=title,
            description=description,
            source=str(source.get("source", "Локальные редакторские источники")),
            why_now=str(source.get("why_now", "")),
            hype_level=str(source.get("hype_level", "средний")),
            relevance_forecast=str(source.get("relevance_forecast", "1-2 недели")),
            reach_score=round(reach, 1),
            brand_fit_score=round(brand, 1),
            ai_reason=_reason(title, plan_bonus, knowledge_matches, case_matches),
            matching_cases=tuple(case_matches),
            knowledge_materials=tuple(knowledge_matches),
            best_formats=tuple(_formats(source, content_plan)),
            status="new",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _load_sources(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.seed_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            raw = DEFAULT_TREND_SOURCES
        sources = raw.get("sources", raw) if isinstance(raw, dict) else raw
        return [item for item in sources if isinstance(item, dict)]

    def _maybe_create_lesson(self, decisions: list[dict[str, object]], latest: dict[str, object]) -> None:
        same_action = [item for item in decisions[:8] if item.get("action") == latest.get("action")]
        if len(same_action) < 3:
            return
        action = str(latest.get("action", ""))
        if action == "approved":
            rule = "Пользователь часто одобряет темы, где рыночный интерес соединяется с операционной зрелостью и Customer Experience."
        elif action == "rejected":
            rule = "Пользователь часто отклоняет темы с высоким хайпом, если они слабо связаны с позиционированием бренда."
        elif action == "saved":
            rule = "Пользователь сохраняет темы, которые можно развить позже как экспертные наблюдения, даже если они не подходят для публикации сегодня."
        else:
            return
        existing = [lesson.rule for lesson in self.learning_center.list_lessons()]
        if rule not in existing:
            self.learning_center.create_candidate(
                rule=rule,
                reason="Trend Radar заметил повторяющийся паттерн решений по темам.",
                confidence=68,
                source="trend_radar",
            )

    def _read_cache(self) -> dict[str, object]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"generated_at": "", "expires_at": "", "topics": [], "sources": []}
        return raw if isinstance(raw, dict) else {"generated_at": "", "expires_at": "", "topics": [], "sources": []}

    def _write_cache(self, raw: dict[str, object]) -> None:
        self.cache_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_decisions(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write_decisions(self, raw: list[dict[str, object]]) -> None:
        self.decisions_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _to_raw(self, topic: TrendTopic) -> dict[str, object]:
        return {
            "id": topic.id,
            "title": topic.title,
            "description": topic.description,
            "source": topic.source,
            "why_now": topic.why_now,
            "hype_level": topic.hype_level,
            "relevance_forecast": topic.relevance_forecast,
            "reach_score": topic.reach_score,
            "brand_fit_score": topic.brand_fit_score,
            "ai_reason": topic.ai_reason,
            "matching_cases": list(topic.matching_cases),
            "knowledge_materials": list(topic.knowledge_materials),
            "best_formats": list(topic.best_formats),
            "status": topic.status,
            "created_at": topic.created_at,
        }


def _matching_documents(text: str, documents: list[object]) -> list[str]:
    tokens = _tokens(text)
    matches = []
    for document in documents:
        haystack = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", "")))
        if tokens.intersection(_tokens(haystack)):
            matches.append(getattr(document, "title", ""))
    return matches[:3]


def _matching_cases(text: str, cases: list[object]) -> list[str]:
    tokens = _tokens(text)
    matches = []
    for case in cases:
        haystack = " ".join((getattr(case, "title", ""), getattr(case, "company", ""), " ".join(getattr(case, "key_topics", ()))))
        if tokens.intersection(_tokens(haystack)):
            matches.append(getattr(case, "title", ""))
    return matches[:3]


def _plan_fit_bonus(text: str, content_plan: dict[str, object]) -> float:
    tokens = _tokens(text)
    plan_text = " ".join(
        [
            str(content_plan.get("focus", "")),
            str(content_plan.get("month_focus", "")),
            " ".join(_as_list(content_plan.get("content_pillars", []))),
        ]
    )
    return min(1.4, len(tokens.intersection(_tokens(plan_text))) * 0.25)


def _idea_bonus(text: str, ideas: list[object]) -> float:
    tokens = _tokens(text)
    count = 0
    for idea in ideas:
        haystack = " ".join((getattr(idea, "title", ""), getattr(idea, "description", "")))
        if tokens.intersection(_tokens(haystack)):
            count += 1
    return min(0.8, count * 0.2)


def _controversy_bonus(source: dict[str, object]) -> float:
    text = " ".join(str(source.get(key, "")) for key in ("title", "description", "why_now", "hype_level")).lower()
    return 0.6 if any(word in text for word in ("спор", "конфликт", "миф", "ошибка", "хайп")) else 0.0


def _formats(source: dict[str, object], content_plan: dict[str, object]) -> list[str]:
    configured = _as_list(source.get("best_formats", []))
    if configured:
        return configured
    platforms = _as_list(content_plan.get("platform_targets", []))
    return platforms[:3] or ["LinkedIn", "Telegram", "VC"]


def _reason(title: str, plan_bonus: float, documents: list[str], cases: list[str]) -> str:
    parts = [f"Тема «{title}» прошла фильтр Thinking Engine."]
    if plan_bonus > 0:
        parts.append("Она связана с текущим контент-планом.")
    if documents:
        parts.append("Есть материалы в Knowledge, которые можно использовать.")
    if cases:
        parts.append("Есть подходящие кейсы.")
    if len(parts) == 1:
        parts.append("Это можно держать как рыночный угол, но перед публикацией проверить соответствие бренду.")
    return " ".join(parts)


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE) if len(word) > 3}


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE))[:80] or uuid4().hex


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


DEFAULT_TREND_SOURCES = {
    "sources": [
        {
            "id": "ai-operational-maturity",
            "title": "AI как зеркало операционной зрелости",
            "description": "Компании активнее обсуждают AI, но результаты зависят от качества процессов, данных и ответственности.",
            "source": "Локальные редакторские источники: AI / Operations",
            "why_now": "AI остается горячей темой, но аудитория устала от общих обещаний и ищет практический управленческий взгляд.",
            "hype_level": "высокий",
            "relevance_forecast": "2-4 недели",
            "reach_base": 9.0,
            "brand_base": 9.2,
            "tags": ["AI", "Operations", "Customer Experience", "Operational Excellence"],
            "best_formats": ["LinkedIn", "Telegram"],
        },
        {
            "id": "service-design-implementation-gap",
            "title": "Service Design без внедрения",
            "description": "Journey map и service blueprint не меняют сервис, если не связаны с ролями, SOP и контролем исполнения.",
            "source": "Локальные редакторские источники: Service Design / CX",
            "why_now": "Команды продолжают делать красивые карты пути, но бизнес ждет операционного результата.",
            "hype_level": "средний",
            "relevance_forecast": "1-2 месяца",
            "reach_base": 7.6,
            "brand_base": 9.4,
            "tags": ["Service Design", "Customer Experience", "SOP", "Operations"],
            "best_formats": ["VC", "LinkedIn"],
        },
        {
            "id": "hospitality-personalization-vs-standards",
            "title": "Персонализация в hospitality против стандартов",
            "description": "Luxury Hospitality требует живого сервиса, но без стандартов персонализация превращается в случайность.",
            "source": "Локальные редакторские источники: Hospitality",
            "why_now": "Гостиничный рынок ищет баланс между AI-персонализацией, сервисной дисциплиной и человеческим вниманием.",
            "hype_level": "средний",
            "relevance_forecast": "1 месяц",
            "reach_base": 7.2,
            "brand_base": 9.1,
            "tags": ["Luxury Hospitality", "Hospitality", "Customer Experience", "SOP"],
            "best_formats": ["LinkedIn", "Telegram"],
        },
        {
            "id": "process-ownership-handoffs",
            "title": "Сервис ломается в точках передачи ответственности",
            "description": "Клиентский опыт часто рушится не в контакте, а между ролями, когда никто не владеет переходом.",
            "source": "Локальные редакторские источники: Operations / CX",
            "why_now": "Тема хорошо соединяет Customer Experience, Operations и управленческую диагностику.",
            "hype_level": "умеренный",
            "relevance_forecast": "долгоиграющая тема",
            "reach_base": 7.8,
            "brand_base": 9.7,
            "tags": ["Operations", "Customer Experience", "SOP"],
            "best_formats": ["LinkedIn", "Сетка", "Telegram"],
        },
    ]
}
