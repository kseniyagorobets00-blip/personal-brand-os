from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
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
    why_important: str
    category: str
    hype_level: str
    relevance_forecast: str
    reach_score: float
    brand_fit_score: float
    content_potential: float
    trend_score: float
    component_scores: dict[str, float]
    ai_reason: str
    matching_cases: tuple[str, ...]
    knowledge_materials: tuple[str, ...]
    best_formats: tuple[str, ...]
    best_rubrics: tuple[str, ...]
    sources: tuple[str, ...]
    detected_at: str
    why_trend: str
    author_brain_topics: tuple[str, ...]
    repeat_risk: str
    recommendation: str
    ai_explanation: dict[str, object]
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
        author_brain: dict[str, object] | None = None,
        graph_links: list[dict[str, str]] | None = None,
        ai_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        source_status: list[str] = []
        sources = self._collect_sources(source_status)
        if ai_context:
            content_plan = _dict_from(ai_context.get("content_plan")) or content_plan
            author_brain = _dict_from(ai_context.get("author_brain")) or author_brain
        topics = [
            self._build_topic(source, content_plan, documents, cases, ideas, graph_links or [])
            if author_brain is None
            else self._build_topic(source, content_plan, documents, cases, ideas, graph_links or [], author_brain)
            for source in sources
        ]
        grouped = self._group_similar_topics(topics)
        filtered = sorted(grouped, key=lambda item: item.trend_score, reverse=True)[:12]
        generated_at = datetime.now(timezone.utc)
        cache = {
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "expires_at": (generated_at + timedelta(minutes=self.ttl_minutes)).isoformat(timespec="seconds"),
            "sources": sorted({source for topic in filtered for source in topic.sources}),
            "source_status": " ".join(source_status) if source_status else "Используется локальный анализ.",
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
        author_brain: dict[str, object] | None = None,
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
        brain_bonus = _author_brain_bonus(content_text, author_brain or {})
        repeat_risk = _repeat_risk(content_text, content_plan, ideas)
        rubric_matches = _rubrics(source, content_plan)
        author_topics = _author_brain_topics(content_text, author_brain or {})
        reach = min(10.0, float(source.get("reach_base", 6.5)) + _controversy_bonus(source) + idea_bonus)
        repeat_penalty = 0.7 if repeat_risk == "высокий" else 0.25 if repeat_risk == "средний" else 0.0
        brand = min(10.0, float(source.get("brand_base", 6.5)) + plan_bonus + len(knowledge_matches) * 0.25 + len(case_matches) * 0.45 + graph_bonus + brain_bonus - repeat_penalty)
        editorial_fit = _editorial_strategy_score(source, content_plan, rubric_matches)
        content_potential = min(10.0, reach + len(case_matches) * 0.35 + len(knowledge_matches) * 0.2 + (0.4 if rubric_matches else 0.0))
        trend_relevance = _trend_relevance_score(source)
        repeat_score = {"низкий": 10.0, "средний": 6.0, "высокий": 2.0}.get(repeat_risk, 7.0)
        component_scores = {
            "trend_relevance": round(trend_relevance, 1),
            "brand_fit": round(brand, 1),
            "editorial_strategy_fit": round(editorial_fit, 1),
            "content_potential": round(content_potential, 1),
            "repeat_safety": round(repeat_score, 1),
        }
        trend_score = round(
            trend_relevance * 0.30
            + brand * 0.25
            + editorial_fit * 0.20
            + content_potential * 0.15
            + repeat_score * 0.10,
            1,
        )
        recommendation = _recommendation(trend_score, brand, repeat_risk)
        why_trend = str(source.get("why_trend") or source.get("why_now") or "Тема набирает плотность в нескольких источниках и пересекается с текущим контекстом автора.")
        why_important = str(source.get("why_important") or "Тема может дать авторский угол о связи тренда с операционной зрелостью, сервисом и управленческими системами.")
        explanation = {
            "trend": why_trend,
            "trend_score": trend_score,
            "content_potential": round(content_potential, 1),
            "month_focus": str(content_plan.get("month_focus", "")),
            "week_focus": str(content_plan.get("focus", "")),
            "documents": knowledge_matches,
            "cases": case_matches,
            "platform_fit": _platform_fit_reason(source, content_plan),
            "author_fit": _reason(title, plan_bonus, knowledge_matches, case_matches, brain_bonus),
            "repeat_risk": repeat_risk,
        }
        return TrendTopic(
            id=str(source.get("id") or _slug(title)),
            title=title,
            description=description,
            source=str(source.get("source", "Локальные редакторские источники")),
            why_now=str(source.get("why_now", "")),
            why_important=why_important,
            category=_category(source),
            hype_level=str(source.get("hype_level", "средний")),
            relevance_forecast=str(source.get("relevance_forecast", "1-2 недели")),
            reach_score=round(reach, 1),
            brand_fit_score=round(brand, 1),
            content_potential=round(content_potential, 1),
            trend_score=trend_score,
            component_scores=component_scores,
            ai_reason=_reason(title, plan_bonus, knowledge_matches, case_matches, brain_bonus),
            matching_cases=tuple(case_matches),
            knowledge_materials=tuple(knowledge_matches),
            best_formats=tuple(_formats(source, content_plan)),
            best_rubrics=tuple(rubric_matches),
            sources=tuple(_as_list(source.get("sources", [])) or [str(source.get("source", "Локальные редакторские источники"))]),
            detected_at=str(source.get("detected_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
            why_trend=why_trend,
            author_brain_topics=tuple(author_topics),
            repeat_risk=repeat_risk,
            recommendation=recommendation,
            ai_explanation=explanation,
            status="new",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _collect_sources(self, source_status: list[str]) -> list[dict[str, object]]:
        local_sources = self._load_sources()
        external_sources = ExternalFeedSourceProvider().fetch(source_status)
        if not external_sources:
            source_status.append("Внешние источники недоступны, используется локальный анализ.")
        return _dedupe_sources(external_sources + local_sources)

    def _group_similar_topics(self, topics: list[TrendTopic]) -> list[TrendTopic]:
        groups: list[TrendTopic] = []
        for topic in topics:
            existing_index = None
            topic_tokens = _tokens(" ".join((topic.title, topic.description)))
            for index, existing in enumerate(groups):
                existing_tokens = _tokens(" ".join((existing.title, existing.description)))
                if _jaccard(topic_tokens, existing_tokens) >= 0.42:
                    existing_index = index
                    break
            if existing_index is None:
                groups.append(topic)
                continue
            existing = groups[existing_index]
            merged_sources = tuple(sorted(set(existing.sources + topic.sources)))
            groups[existing_index] = TrendTopic(
                id=existing.id,
                title=existing.title,
                description=existing.description,
                source=", ".join(merged_sources),
                why_now=existing.why_now,
                why_important=existing.why_important,
                category=existing.category,
                hype_level=existing.hype_level,
                relevance_forecast=existing.relevance_forecast,
                reach_score=round(min(10.0, max(existing.reach_score, topic.reach_score) + 0.4), 1),
                brand_fit_score=round(max(existing.brand_fit_score, topic.brand_fit_score), 1),
                content_potential=round(max(existing.content_potential, topic.content_potential), 1),
                trend_score=round(min(10.0, max(existing.trend_score, topic.trend_score) + 0.3), 1),
                component_scores=existing.component_scores,
                ai_reason=existing.ai_reason,
                matching_cases=tuple(sorted(set(existing.matching_cases + topic.matching_cases))),
                knowledge_materials=tuple(sorted(set(existing.knowledge_materials + topic.knowledge_materials))),
                best_formats=tuple(sorted(set(existing.best_formats + topic.best_formats))),
                best_rubrics=tuple(sorted(set(existing.best_rubrics + topic.best_rubrics))),
                sources=merged_sources,
                detected_at=min(existing.detected_at, topic.detected_at),
                why_trend=f"{existing.why_trend} Похожие сигналы найдены в нескольких источниках.",
                author_brain_topics=tuple(sorted(set(existing.author_brain_topics + topic.author_brain_topics))),
                repeat_risk=max((existing.repeat_risk, topic.repeat_risk), key=_repeat_risk_rank),
                recommendation=existing.recommendation,
                ai_explanation=existing.ai_explanation,
                status=existing.status,
                created_at=existing.created_at,
            )
        return groups

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
            "why_important": topic.why_important,
            "category": topic.category,
            "hype_level": topic.hype_level,
            "relevance_forecast": topic.relevance_forecast,
            "reach_score": topic.reach_score,
            "brand_fit_score": topic.brand_fit_score,
            "content_potential": topic.content_potential,
            "trend_score": topic.trend_score,
            "component_scores": topic.component_scores,
            "ai_reason": topic.ai_reason,
            "matching_cases": list(topic.matching_cases),
            "knowledge_materials": list(topic.knowledge_materials),
            "best_formats": list(topic.best_formats),
            "best_rubrics": list(topic.best_rubrics),
            "sources": list(topic.sources),
            "detected_at": topic.detected_at,
            "why_trend": topic.why_trend,
            "author_brain_topics": list(topic.author_brain_topics),
            "repeat_risk": topic.repeat_risk,
            "recommendation": topic.recommendation,
            "ai_explanation": topic.ai_explanation,
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


def _rubrics(source: dict[str, object], content_plan: dict[str, object]) -> list[str]:
    configured = _as_list(source.get("best_rubrics", []))
    if configured:
        return configured
    text = " ".join(str(source.get(key, "")) for key in ("title", "description", "why_now", "source")).lower()
    if any(word in text for word in ("case", "кейс", "example", "project")):
        return ["Кейс", "Разбор ошибки"]
    if any(word in text for word in ("framework", "model", "sop", "process", "system")):
        return ["Framework", "Аналитика"]
    if any(word in text for word in ("myth", "миф", "risk", "ошибка")):
        return ["Миф", "Разбор ошибки"]
    pillars = _as_list(content_plan.get("content_pillars", []))
    return ["Аналитика", "Наблюдение"] if pillars else ["Наблюдение"]


def _author_brain_bonus(text: str, author_brain: dict[str, object]) -> float:
    profile = author_brain.get("profile", author_brain)
    if not isinstance(profile, dict):
        return 0.0
    tokens = _tokens(text)
    bonus = 0.0
    themes = profile.get("main_themes", [])
    if isinstance(themes, list):
        for theme in themes:
            if not isinstance(theme, dict):
                continue
            evidence = theme.get("evidence", [])
            evidence_text = " ".join(str(item) for item in evidence) if isinstance(evidence, list) else ""
            if tokens.intersection(_tokens(str(theme.get("name", "")) + " " + evidence_text)):
                bonus += 0.18
    cases = profile.get("cases", [])
    if isinstance(cases, list):
        for case in cases:
            if not isinstance(case, dict):
                continue
            case_text = " ".join(str(case.get(key, "")) for key in ("company", "project", "problem", "actions", "result"))
            if tokens.intersection(_tokens(case_text)):
                bonus += 0.22
    return min(1.2, bonus)


def _author_brain_topics(text: str, author_brain: dict[str, object]) -> list[str]:
    profile = author_brain.get("profile", author_brain)
    if not isinstance(profile, dict):
        return []
    tokens = _tokens(text)
    matches = []
    themes = profile.get("main_themes", [])
    if isinstance(themes, list):
        for theme in themes:
            if not isinstance(theme, dict):
                continue
            name = str(theme.get("name", ""))
            if tokens.intersection(_tokens(name)):
                matches.append(name)
    return matches[:5]


def _repeat_risk(text: str, content_plan: dict[str, object], ideas: list[object]) -> str:
    tokens = _tokens(text)
    recent_texts = []
    publications = content_plan.get("planned_publications", [])
    if isinstance(publications, list):
        for item in publications[:12]:
            if isinstance(item, dict):
                recent_texts.append(" ".join(str(item.get(key, "")) for key in ("topic", "summary", "note")))
    for idea in ideas[:12]:
        recent_texts.append(" ".join((getattr(idea, "title", ""), getattr(idea, "description", ""))))
    best = max((_jaccard(tokens, _tokens(item)) for item in recent_texts), default=0.0)
    if best >= 0.48:
        return "высокий"
    if best >= 0.28:
        return "средний"
    return "низкий"


def _trend_relevance_score(source: dict[str, object]) -> float:
    base = float(source.get("trend_base", source.get("reach_base", 6.5)))
    if str(source.get("detected_at", "")).strip():
        base += 0.4
    if _as_list(source.get("sources", [])):
        base += min(1.0, len(_as_list(source.get("sources", []))) * 0.2)
    return round(min(10.0, base), 1)


def _editorial_strategy_score(source: dict[str, object], content_plan: dict[str, object], rubrics: list[str]) -> float:
    tokens = _tokens(" ".join(str(source.get(key, "")) for key in ("title", "description", "why_now", "category")))
    plan_tokens = _tokens(
        " ".join(
            [
                str(content_plan.get("focus", "")),
                str(content_plan.get("month_focus", "")),
                " ".join(_as_list(content_plan.get("content_pillars", []))),
                " ".join(rubrics),
            ]
        )
    )
    return round(min(10.0, 5.8 + len(tokens & plan_tokens) * 0.45 + (0.8 if rubrics else 0.0)), 1)


def _category(source: dict[str, object]) -> str:
    category = str(source.get("category", "")).strip()
    if category:
        return category
    text = " ".join(str(source.get(key, "")) for key in ("title", "description", "source")).lower()
    if any(word in text for word in ("hotel", "hospitality", "travel", "skift", "phocus")):
        return "Hospitality"
    if any(word in text for word in ("customer", "cx", "service design", "ux", "nielsen")):
        return "Customer Experience"
    if any(word in text for word in ("operations", "lean", "process", "apqc")):
        return "Operations"
    if any(word in text for word in ("hbr", "mckinsey", "deloitte", "bcg", "management", "sloan")):
        return "Management"
    return "AI"


def _recommendation(trend_score: float, brand: float, repeat_risk: str) -> str:
    if repeat_risk == "высокий" or brand < 5.8:
        return "не брать"
    if trend_score >= 7.2 and brand >= 7.0:
        return "брать"
    return "отложить"


def _platform_fit_reason(source: dict[str, object], content_plan: dict[str, object]) -> str:
    formats = _formats(source, content_plan)
    if not formats:
        return "Площадка не задана, можно адаптировать после выбора формата."
    return f"Лучше всего подходит для: {', '.join(formats)}."


def _reason(title: str, plan_bonus: float, documents: list[str], cases: list[str], brain_bonus: float = 0.0) -> str:
    parts = [f"Тема «{title}» прошла фильтр Thinking Engine."]
    if plan_bonus > 0:
        parts.append("Она связана с текущим контент-планом.")
    if brain_bonus > 0:
        parts.append("Author Brain подтвердил связь с темами, кейсами и позицией автора.")
    if documents:
        parts.append("Есть материалы в Knowledge, которые можно использовать.")
    if cases:
        parts.append("Есть подходящие кейсы.")
    if len(parts) == 1:
        parts.append("Это можно держать как рыночный угол, но перед публикацией проверить соответствие бренду.")
    return " ".join(parts)


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE) if len(word) > 3}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _repeat_risk_rank(value: str) -> int:
    return {"низкий": 0, "средний": 1, "высокий": 2}.get(value, 0)


def _dict_from(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


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


def _dedupe_sources(sources: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: list[set[str]] = []
    result: list[dict[str, object]] = []
    for source in sources:
        tokens = _tokens(" ".join(str(source.get(key, "")) for key in ("title", "description")))
        if any(_jaccard(tokens, existing) >= 0.55 for existing in seen):
            continue
        seen.append(tokens)
        result.append(source)
    return result


class ExternalFeedSourceProvider:
    """Optional RSS/HTTP provider. It fails closed and lets the radar use local sources."""

    DEFAULT_FEEDS = (
        {"name": "OpenAI News", "category": "AI", "url": "https://openai.com/news/rss.xml"},
        {"name": "Anthropic News", "category": "AI", "url": "https://www.anthropic.com/news/rss.xml"},
        {"name": "Google DeepMind", "category": "AI", "url": "https://deepmind.google/blog/rss.xml"},
        {"name": "Google AI Blog", "category": "AI", "url": "https://blog.google/technology/ai/rss/"},
        {"name": "Microsoft AI Blog", "category": "AI", "url": "https://blogs.microsoft.com/ai/feed/"},
        {"name": "AWS ML Blog", "category": "AI", "url": "https://aws.amazon.com/blogs/machine-learning/feed/"},
        {"name": "Hugging Face Blog", "category": "AI", "url": "https://huggingface.co/blog/feed.xml"},
        {"name": "Harvard Business Review", "category": "Management", "url": "https://hbr.org/feed"},
        {"name": "MIT Sloan", "category": "Management", "url": "https://sloanreview.mit.edu/feed/"},
        {"name": "McKinsey Insights", "category": "Management", "url": "https://www.mckinsey.com/featured-insights/rss"},
        {"name": "Deloitte Insights", "category": "Management", "url": "https://www2.deloitte.com/us/en/insights/rss.xml"},
        {"name": "BCG", "category": "Management", "url": "https://www.bcg.com/publications/rss.aspx"},
        {"name": "Gartner", "category": "Management", "url": "", "stub": True},
        {"name": "HospitalityNet", "category": "Hospitality", "url": "https://www.hospitalitynet.org/rss/1.xml"},
        {"name": "Hotel Management", "category": "Hospitality", "url": "https://www.hotelmanagement.net/rss/xml"},
        {"name": "Hotel News Resource", "category": "Hospitality", "url": "https://www.hotelnewsresource.com/rss.xml"},
        {"name": "Skift", "category": "Hospitality", "url": "https://skift.com/feed/"},
        {"name": "PhocusWire", "category": "Hospitality", "url": "https://www.phocuswire.com/RSS/All-News"},
        {"name": "CX Network", "category": "Customer Experience", "url": "https://www.cxnetwork.com/rss"},
        {"name": "Nielsen Norman Group", "category": "Customer Experience", "url": "https://www.nngroup.com/feed/rss/"},
        {"name": "Service Design Network", "category": "Customer Experience", "url": "https://www.service-design-network.org/feed"},
        {"name": "APQC", "category": "Operations", "url": "https://www.apqc.org/blog/rss.xml"},
        {"name": "Lean Enterprise Institute", "category": "Operations", "url": "https://www.lean.org/feed/"},
    )

    def __init__(self, feeds: tuple[dict[str, object], ...] | None = None, timeout_seconds: float = 1.2) -> None:
        raw_feeds = os.environ.get("TREND_RADAR_RSS_FEEDS", "")
        configured = tuple({"name": item.strip(), "category": "External", "url": item.strip()} for item in raw_feeds.split(",") if item.strip())
        self.feeds = feeds or configured or self.DEFAULT_FEEDS
        self.timeout_seconds = timeout_seconds
        self.enabled = os.environ.get("TREND_RADAR_ENABLE_RSS", "").lower() in {"1", "true", "yes", "on"}

    def fetch(self, source_status: list[str]) -> list[dict[str, object]]:
        if not self.enabled:
            source_status.append("Внешние RSS-источники подключаемы, но сейчас выключены; используется локальный анализ.")
            return []
        items: list[dict[str, object]] = []
        for feed in self.feeds:
            if feed.get("stub"):
                source_status.append(f"{feed.get('name')}: provider-заглушка, RSS недоступен.")
                continue
            url = str(feed.get("url", "")).strip()
            if not url:
                continue
            try:
                request = Request(url, headers={"User-Agent": "PersonalBrandOS-TrendRadar/3.0"})
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read(500_000)
                items.extend(_parse_feed_items(payload, feed))
            except (OSError, URLError, ET.ParseError, TimeoutError) as exc:
                source_status.append(f"{feed.get('name')}: недоступен ({exc})")
        if items:
            source_status.append(f"Внешние RSS-источники: получено {len(items)} сигналов.")
        return items


def _parse_feed_items(payload: bytes, feed: dict[str, object]) -> list[dict[str, object]]:
    root = ET.fromstring(payload)
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    feed_url = str(feed.get("url", ""))
    feed_name = str(feed.get("name", feed_url))
    category = str(feed.get("category", "External"))
    result = []
    for item in items[:12]:
        title = _first_xml_text(item, ("title", "{http://www.w3.org/2005/Atom}title"))
        description = _first_xml_text(item, ("description", "summary", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"))
        published = _first_xml_text(item, ("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"))
        if not title:
            continue
        text = _strip_html(description)
        result.append(
            {
                "id": _slug(f"{feed_url}-{title}"),
                "title": title,
                "description": text[:600],
                "source": feed_name,
                "sources": [feed_name],
                "category": category,
                "detected_at": published or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "why_now": "Свежий сигнал из внешнего RSS/медиа-источника.",
                "why_trend": "Тема появилась во внешнем источнике и проверяется на соответствие Author Brain, Knowledge и редакционной стратегии.",
                "why_important": "Внешний сигнал помогает найти редакционный угол, который не является дословным переводом новости.",
                "hype_level": "проверить",
                "relevance_forecast": "1-2 недели",
                "reach_base": 6.8,
                "brand_base": 6.2,
                "tags": list(_tokens(" ".join((title, text))) & {"ai", "operations", "hospitality", "service", "customer", "experience", "management", "analytics"}),
            }
        )
    return result


def _first_xml_text(item: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        node = item.find(name)
        if node is not None and node.text:
            return unescape(node.text.strip())
    return ""


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()


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
