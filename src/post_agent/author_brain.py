from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any

from .storage import data_path

DEFAULT_AUTHOR_BRAIN_DIR = data_path("author_brain")
DEFAULT_AUTHOR_BRAIN_PROFILE_PATH = DEFAULT_AUTHOR_BRAIN_DIR / "profile.json"
DEFAULT_AUTHOR_BRAIN_STATUS_PATH = DEFAULT_AUTHOR_BRAIN_DIR / "status.json"
AUTHOR_BRAIN_VERSION = "2.0"
_REFRESH_LOCK = threading.Lock()
THEME_WEIGHT_RULE = (
    "Вес главной темы управляет приоритетом AI: 90-100 — основной фокус и предпочтительный угол; "
    "70-89 — регулярный рабочий угол; 40-69 — вспомогательный контекст; ниже 40 — использовать только при явном совпадении с задачей."
)

THINKING_MODES = (
    "Observation",
    "Story",
    "Case",
    "Provocation",
    "Framework",
    "Reflection",
)

FORBIDDEN_OPENINGS = (
    "в современном мире",
    "сегодня многие компании",
    "в бизнесе часто",
    "не секрет",
    "многие считают",
    "customer experience — это",
    "customer experience - это",
    "service design — это",
    "service design - это",
    "искусственный интеллект сегодня",
    "в эпоху цифровизации",
)

DEFAULT_AUTHOR_MOVES = (
    "начинать с рабочего наблюдения, а не с определения темы",
    "быстро переходить от симптома к управленческой причине",
    "объяснять через ответственность, точки передачи и операционную зрелость",
    "использовать практический вывод вместо академического резюме",
    "задавать читателю вопрос, который хочется применить к своей системе",
)

CORE_THEMES = (
    ("операции и процессы", ("operations", "operational", "process", "handoff", "ownership", "операц", "процесс")),
    ("клиентский опыт (CX)", ("customer experience", "cx", "guest experience", "клиент", "опыт")),
    ("сервисные системы", ("service system", "service design", "blueprint", "сервис")),
    ("гостеприимство", ("hospitality", "hotel", "guest", "гост", "отел")),
    ("премиальный сервис", ("premium", "luxury", "personalization", "преми", "luxury hospitality")),
    ("улучшение процессов", ("improvement", "diagnostics", "audit", "maturity", "улучш", "диагност")),
    ("BI / analytics", ("bi", "analytics", "dashboard", "data", "metric", "аналит", "данн")),
    ("SOP", ("sop", "standard", "regulation", "регламент", "стандарт")),
    ("управленческие системы", ("management system", "управлен", "ответствен", "контрол")),
)

CONTENT_ANGLES = (
    "личное наблюдение",
    "кейс",
    "аналитика",
    "провокационный угол",
    "фреймворк",
    "практический разбор",
)

PLATFORM_FIT = {
    "LinkedIn": "Английский язык только для самого поста; управленческий, консультационный тон; понятный бизнес-эффект.",
    "VC": "Русский язык; экспертно, но живо; практический разбор или логика кейса.",
    "Telegram": "Русский язык; коротко и разговорно: одна мысль через рабочее наблюдение.",
    "Сетка": "Русский язык; сначала наблюдение, короткая мысль, без лишней официальности.",
}


@dataclass(frozen=True)
class AuthorBrainStatus:
    state: str
    message: str
    updated_at: str
    error: str = ""


class AuthorBrain:
    """Builds the structured author-thinking profile used by generation."""

    def __init__(
        self,
        author_profile: dict[str, Any],
        writing_dna: dict[str, Any] | None,
        documents: list[object],
        cases: list[object],
        ideas: list[object],
        lessons: list[object] | None = None,
    ) -> None:
        self.author_profile = author_profile
        self.writing_dna = writing_dna or {}
        self.documents = documents
        self.cases = cases
        self.ideas = ideas
        self.lessons = lessons or []
        self._rules_cache: dict[str, object] | None = None

    def _rules(self) -> dict[str, object]:
        if self._rules_cache is None:
            from .bot_rules import load_bot_rules  # lazy import avoids a circular dependency

            self._rules_cache = load_bot_rules()
        return self._rules_cache

    def build_profile(self) -> dict[str, object]:
        corpus = self._corpus()
        themes = self._main_themes(corpus)
        cases = self._structured_cases()
        key_ideas = self._key_ideas(corpus)
        return {
            "version": AUTHOR_BRAIN_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "ready",
            "main_themes": themes,
            "theme_weight_rule": str(self._rules().get("theme_weight_rule", THEME_WEIGHT_RULE)),
            "key_ideas": key_ideas,
            "cases": cases,
            "content_angles": self._content_angles(corpus),
            "platform_fit": self._rules().get("platform_rules", PLATFORM_FIT) if isinstance(self._rules().get("platform_rules"), dict) else PLATFORM_FIT,
            "thinking_style": self._thinking_style(),
            "strengths": self._strengths(themes, cases),
            "anti_repetition": self._anti_repetition(key_ideas, cases),
            "recent_updates": self._recent_updates(),
            "source_counts": {
                "documents": len(self.documents),
                "cases": len(self.cases),
                "ideas": len(self.ideas),
                "lessons": len(self.lessons),
            },
        }

    def build(self, publication: dict[str, object] | None = None) -> dict[str, object]:
        publication = publication or {}
        platform = str(publication.get("platform", ""))
        topic = str(publication.get("topic", ""))
        summary = str(publication.get("summary", ""))
        goal = str(publication.get("goal", ""))
        query = " ".join((platform, topic, summary, goal))
        profile = self.build_profile()
        return {
            "version": AUTHOR_BRAIN_VERSION,
            "profile": profile,
            "author_identity": "Ксения, практик и консультант на пересечении Operations, Customer Experience, Service Design, Hospitality и AI.",
            "generation_intent": (
                "Писать так, как если бы Ксения утром села за публикацию после рабочего разговора, аудита, проекта "
                "или свежего наблюдения. Это не универсальная экспертная статья, а ход мысли конкретного автора."
            ),
            "writing_dna": self.writing_dna,
            "thinking_mode": self._select_mode(platform, query),
            "allowed_thinking_modes": _as_str_list(self._rules().get("thinking_modes", THINKING_MODES)) or list(THINKING_MODES),
            "voice_principles": self._voice_principles(platform),
            "author_moves": _as_str_list(self._rules().get("thinking_rules", DEFAULT_AUTHOR_MOVES)) or list(DEFAULT_AUTHOR_MOVES),
            "vocabulary": self._vocabulary(),
            "platform_goal": self._platform_goal(platform),
            "platform_rule": self._platform_rule(platform),
            "what_not_to_write": self._what_not_to_write(),
            "forbidden_openings": self._forbidden_openings(),
            "examples_and_stories": self._examples_and_stories(query),
            "case_candidates": self._case_candidates(query),
            "knowledge_observations": self._knowledge_observations(query),
            "idea_patterns": self._idea_patterns(query),
            "anti_repetition": profile["anti_repetition"],
            "anti_repeat_rules": _as_str_list(self._rules().get("anti_repeat_rules", [])),
            "platform_rules": self._rules().get("platform_rules", {}) if isinstance(self._rules().get("platform_rules"), dict) else {},
            "similarity_report": self.similarity_report(topic or summary or goal, profile),
            "self_check": {
                "question": "Похоже ли это на Ксению?",
                "criteria": [
                    "есть живое рабочее наблюдение или ситуация",
                    "нет ощущения AI или учебника",
                    "мысль идет от симптома к причине и практическому выводу",
                    "использован подходящий реальный кейс или текст честно обходися без выдуманного кейса",
                    "нет запрещенных вступлений и служебных инструкций",
                ],
                "if_weak": "Выполнить одну внутреннюю итерацию улучшения до финального JSON.",
            },
        }

    def similarity_report(self, idea: str, profile: dict[str, object] | None = None) -> dict[str, object]:
        profile = profile or self.build_profile()
        idea_tokens = _tokens(idea)
        if not idea_tokens:
            return {"too_similar": False, "matches": []}
        matches: list[dict[str, object]] = []
        for item in _as_dict_list(profile.get("key_ideas", [])):
            score = _similarity(idea_tokens, _tokens(str(item.get("idea", ""))))
            if score >= 0.35:
                matches.append({"type": "idea", "title": str(item.get("idea", "")), "score": round(score, 2)})
        for item in _as_dict_list(profile.get("cases", [])):
            text = " ".join(str(item.get(key, "")) for key in ("company", "project", "problem", "actions", "result", "business_effect"))
            score = _similarity(idea_tokens, _tokens(text))
            if score >= 0.35:
                matches.append({"type": "case", "title": str(item.get("project") or item.get("company", "")), "score": round(score, 2)})
        matches = sorted(matches, key=lambda item: float(item.get("score", 0)), reverse=True)[:5]
        return {"too_similar": bool(matches and float(matches[0].get("score", 0)) >= 0.58), "matches": matches}

    def _voice_principles(self, platform: str) -> list[str]:
        tone = self.author_profile.get("tone", {})
        structure = self.author_profile.get("structure", {})
        principles = [
            str(tone.get("formality", "")) if isinstance(tone, dict) else "",
            str(tone.get("directness", "")) if isinstance(tone, dict) else "",
            str(tone.get("provocation", "")) if isinstance(tone, dict) else "",
            str(tone.get("emotionality", "")) if isinstance(tone, dict) else "",
            str(structure.get("narrative_logic", "")) if isinstance(structure, dict) else "",
            str(structure.get("conclusion", "")) if isinstance(structure, dict) else "",
        ]
        if platform == "Telegram":
            principles.append("допустима разговорная интонация, как наблюдение коллеге")
        if platform == "VC":
            principles.append("можно использовать подзаголовки, но текст должен оставаться опытом практика")
        if platform in {"РЎРµС‚РєР°", "Сетка"}:
            principles.append("коротко: ситуация, вывод, вопрос")
        return [item for item in principles if item]

    def _vocabulary(self) -> dict[str, list[str]]:
        vocabulary = self.author_profile.get("vocabulary", {})
        if not isinstance(vocabulary, dict):
            vocabulary = {}
        return {
            "favorite_words": _as_str_list(vocabulary.get("favorite_words", []))[:12],
            "unwanted_words": _as_str_list(vocabulary.get("unwanted_words", []))[:12],
            "professional_terms": _as_str_list(vocabulary.get("professional_terms", [])),
        }

    def _platform_goal(self, platform: str) -> str:
        goals = self.author_profile.get("platform_goals", {})
        return str(goals.get(platform, "")) if isinstance(goals, dict) else ""

    def _platform_rule(self, platform: str) -> str:
        # Single source of truth for platform rules: the editable "Правила бота" store.
        rules = self._rules().get("platform_rules", {})
        return str(rules.get(platform, "")) if isinstance(rules, dict) else ""

    def _what_not_to_write(self) -> list[str]:
        rules = _as_str_list(self.author_profile.get("what_not_to_write", []))
        dna_rules = [str(self.writing_dna.get("draft_rule", "")), str(self.writing_dna.get("anti_template_rule", ""))]
        return [*rules, *[item for item in dna_rules if item], "не начинать с определения понятия", "не звучать как универсальный консультант или ChatGPT"]

    def _forbidden_openings(self) -> list[str]:
        # Single source of truth: the editable "Правила бота" store.
        rules = _as_str_list(self._rules().get("forbidden_openings", []))
        return rules or list(FORBIDDEN_OPENINGS)

    def _examples_and_stories(self, query: str) -> list[dict[str, object]]:
        stories = self.author_profile.get("examples_and_stories", [])
        if not isinstance(stories, list):
            return []
        return [
            story
            for story in stories
            if isinstance(story, dict) and _matches(query, " ".join(str(value) for value in story.values()))
        ][:4]

    def _case_candidates(self, query: str) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for case in self.cases:
            text = " ".join(
                (
                    getattr(case, "title", ""),
                    getattr(case, "company", ""),
                    getattr(case, "what_happened", ""),
                    getattr(case, "solution", ""),
                    getattr(case, "result", ""),
                    " ".join(getattr(case, "key_topics", ())),
                )
            )
            if _matches(query, text):
                candidates.append(
                    {
                        "type": "case",
                        "title": getattr(case, "title", ""),
                        "company": getattr(case, "company", ""),
                        "what_happened": getattr(case, "what_happened", ""),
                        "solution": getattr(case, "solution", ""),
                        "result": getattr(case, "result", ""),
                        "public_usage": getattr(case, "public_usage", ""),
                    }
                )
        for document in self.documents:
            chunks = tuple(getattr(document, "semantic_chunks", ()))
            metadata = getattr(document, "document_metadata", {}) or {}
            chunk_metadata = tuple(getattr(document, "chunk_metadata", ()))
            metadata_text = " ".join(str(value) for value in metadata.values()) if isinstance(metadata, dict) else ""
            chunk_text = " ".join(str(chunk.get("summary", "")) for chunk in chunk_metadata if isinstance(chunk, dict))
            text = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", ""), metadata_text, chunk_text, " ".join(chunks)))
            if _looks_like_case(text) and _matches(query, text):
                candidates.append(
                    {
                        "type": "knowledge_case_note",
                        "title": getattr(document, "title", ""),
                        "excerpt": getattr(document, "excerpt", ""),
                        "metadata": metadata,
                        "chunks": list(chunk_metadata[:3]) or list(chunks[:3]),
                        "usage_rule": "Можно использовать только факты из excerpt/content. Не придумывать детали кейса.",
                    }
                )
        return candidates[:4]

    def _knowledge_observations(self, query: str) -> list[dict[str, str]]:
        observations = []
        for document in self.documents:
            chunks = tuple(getattr(document, "semantic_chunks", ()))
            metadata = getattr(document, "document_metadata", {}) or {}
            chunk_metadata = tuple(getattr(document, "chunk_metadata", ()))
            metadata_text = " ".join(str(value) for value in metadata.values()) if isinstance(metadata, dict) else ""
            chunk_text = " ".join(str(chunk.get("summary", "")) for chunk in chunk_metadata if isinstance(chunk, dict))
            text = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", ""), metadata_text, chunk_text, " ".join(chunks)))
            if _matches(query, text):
                observations.append({"title": getattr(document, "title", ""), "excerpt": getattr(document, "excerpt", ""), "metadata": str(metadata), "chunks": "\n\n".join(chunks[:3])})
        return observations[:4]

    def _idea_patterns(self, query: str) -> list[dict[str, str]]:
        patterns = []
        for idea in self.ideas:
            text = " ".join((getattr(idea, "title", ""), getattr(idea, "description", "")))
            if _matches(query, text):
                patterns.append({"title": getattr(idea, "title", ""), "description": getattr(idea, "description", "")})
        return patterns[:4]

    def _select_mode(self, platform: str, query: str) -> str:
        # Respect the editable thinking modes ("Правила бота"): never return a mode
        # the user removed, and fall back within their list rather than a hardcoded default.
        allowed = _as_str_list(self._rules().get("thinking_modes", [])) or list(THINKING_MODES)
        lowered = query.lower()
        candidates: list[tuple[bool, str]] = [
            (bool(self._case_candidates(query)), "Case"),
            (platform == "VC", "Framework"),
            (any(word in lowered for word in ("ошибка", "хаос", "миф", "не работает", "mistake", "chaos", "myth")), "Provocation"),
            (any(word in lowered for word in ("наблюдение", "ситуация", "пример", "observation", "example")), "Observation"),
            (platform in {"Telegram", "Сетка"}, "Reflection"),
        ]
        for matched, mode in candidates:
            if matched and mode in allowed:
                return mode
        return allowed[0]

    def _corpus(self) -> str:
        parts: list[str] = []
        for document in self.documents:
            metadata = getattr(document, "document_metadata", {}) or {}
            chunks = getattr(document, "chunk_metadata", ()) or ()
            parts.extend([getattr(document, "title", ""), getattr(document, "excerpt", ""), getattr(document, "content_text", "")])
            if isinstance(metadata, dict):
                parts.append(json.dumps(metadata, ensure_ascii=False))
            for chunk in chunks:
                if isinstance(chunk, dict):
                    parts.append(" ".join(str(chunk.get(key, "")) for key in ("title", "type", "summary")))
                    keywords = chunk.get("keywords", [])
                    if isinstance(keywords, list):
                        parts.append(" ".join(str(item) for item in keywords))
        for case in self.cases:
            parts.extend(
                [
                    getattr(case, "title", ""),
                    getattr(case, "company", ""),
                    getattr(case, "what_happened", ""),
                    getattr(case, "reason", ""),
                    getattr(case, "solution", ""),
                    getattr(case, "result", ""),
                    " ".join(getattr(case, "key_topics", ())),
                ]
            )
        for idea in self.ideas:
            parts.extend([getattr(idea, "title", ""), getattr(idea, "description", "")])
        for lesson in self.lessons:
            parts.append(getattr(lesson, "rule", str(lesson)))
        parts.append(json.dumps(self.author_profile, ensure_ascii=False))
        parts.append(json.dumps(self.writing_dna, ensure_ascii=False))
        return "\n".join(str(part) for part in parts if str(part).strip())

    def _main_themes(self, corpus: str) -> list[dict[str, object]]:
        lowered = corpus.lower()
        themes = []
        for theme, markers in CORE_THEMES:
            evidence = [marker for marker in markers if marker.lower() in lowered]
            count = sum(lowered.count(marker.lower()) for marker in markers)
            if evidence or theme in {"операции и процессы", "клиентский опыт (CX)", "сервисные системы", "гостеприимство", "SOP"}:
                themes.append(
                    {
                        "name": theme,
                        "score": min(100, 42 + count * 8 + len(evidence) * 6),
                        "evidence": evidence[:5],
                        "risk": "чередовать со смежными углами" if count > 5 else "",
                    }
                )
        return sorted(themes, key=lambda item: int(item["score"]), reverse=True)

    def _key_ideas(self, corpus: str) -> list[dict[str, object]]:
        sentences = _sentences(corpus)
        defaults = [
            "Клиентский опыт — это операционный результат, а не только слой коммуникации.",
            "SOP и стандарты защищают сервис от случайности, если связаны с ответственностью.",
            "AI усиливает зрелость процессов и показывает разрывы в данных, ответственности и передачах между ролями.",
            "Премиальному сервису нужны и человеческое внимание, и повторяемые системы.",
            "Service Design становится полезным, когда доходит до внедрения, ролей, точек контроля и метрик.",
            "Бизнес-симптомы обычно указывают на более глубокие причины в управленческой системе.",
        ]
        ideas = [
            {
                "idea": candidate,
                "evidence_count": sum(1 for sentence in sentences if _similarity(_tokens(candidate), _tokens(sentence)) >= 0.2),
                "belief": candidate,
                "repeat_risk": "high" if _similarity(_tokens(candidate), _tokens(corpus)) >= 0.2 else "medium",
            }
            for candidate in defaults
        ]
        for sentence in sentences[:80]:
            lowered = sentence.lower()
            if any(marker in lowered for marker in ("conclusion", "important", "вывод", "важно", "кажется", "замеч")):
                ideas.append({"idea": sentence[:220], "evidence_count": 1, "belief": sentence[:220], "repeat_risk": "medium"})
            if len(ideas) >= 8:
                break
        return ideas[:8]

    def _structured_cases(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for case in self.cases:
            result.append(
                {
                    "company": getattr(case, "company", ""),
                    "project": getattr(case, "title", ""),
                    "problem": getattr(case, "what_happened", ""),
                    "actions": getattr(case, "solution", ""),
                    "result": getattr(case, "result", ""),
                    "business_effect": getattr(case, "reason", "") or getattr(case, "result", ""),
                    "themes": list(getattr(case, "key_topics", ())),
                    "platform_fit": list(getattr(case, "platforms", ())),
                    "usage_risk": "не использовать этот кейс слишком часто",
                }
            )
        for document in self.documents:
            text = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", ""), getattr(document, "content_text", "")))
            if not _looks_like_case(text):
                continue
            metadata = getattr(document, "document_metadata", {}) or {}
            themes = metadata.get("topics", []) if isinstance(metadata, dict) else []
            result.append(
                {
                    "company": _first_known_company(text),
                    "project": getattr(document, "title", ""),
                    "problem": _sentence_with_any(text, ("problem", "проблем", "хаос", "break", "gap")),
                    "actions": _sentence_with_any(text, ("action", "действ", "solution", "решен", "sop", "process")),
                    "result": _sentence_with_any(text, ("result", "эффект", "сниз", "рост", "%")),
                    "business_effect": _sentence_with_any(text, ("business effect", "эффект", "result", "%")),
                    "themes": themes if isinstance(themes, list) else [],
                    "platform_fit": ["LinkedIn", "VC", "Telegram"],
                    "usage_risk": "использовать только факты из памяти",
                }
            )
        return result[:12]

    def _content_angles(self, corpus: str) -> list[dict[str, object]]:
        lowered = corpus.lower()
        return [{"name": angle, "fit": _angle_fit(angle, lowered)} for angle in CONTENT_ANGLES]

    def _thinking_style(self) -> list[str]:
        result = _as_str_list(self.writing_dna.get("argumentation_patterns", []))
        result.extend(
            [
                "Начинает с рабочего наблюдения, а потом переходит к управленческой причине.",
                "Связывает качество сервиса с операциями, ответственностью, стандартами и данными.",
                "Предпочитает практические выводы абстрактным определениям.",
            ]
        )
        return result[:8]

    def _strengths(self, themes: list[dict[str, object]], cases: list[dict[str, object]]) -> list[str]:
        result = [
            "Операционная диагностика сервисных проблем.",
            "Перевод клиентского опыта в процессы, роли, SOP и точки контроля.",
            "Умение превращать кейсы и наблюдения в управленческий контент.",
        ]
        if cases:
            result.append("Реальная база кейсов для тем про гостеприимство и сервисные системы.")
        if any(str(theme.get("name", "")).lower() == "bi / analytics" for theme in themes):
            result.append("Связывает сервис и операции с логикой BI / аналитики.")
        return result

    def _anti_repetition(self, key_ideas: list[dict[str, object]], cases: list[dict[str, object]]) -> dict[str, object]:
        recent_ideas = [{"title": getattr(idea, "title", ""), "description": getattr(idea, "description", "")} for idea in self.ideas[:12]]
        return {
            "recent_ideas": recent_ideas,
            "overused_theme_candidates": [str(item["idea"]) for item in key_ideas if str(item.get("repeat_risk", "")) == "high"][:4],
            "case_rotation": [str(item.get("project") or item.get("company", "")) for item in cases[:6] if item.get("project") or item.get("company")],
            "rules": [
                "Не предлагать тему, если она слишком похожа на недавние идеи.",
                "Не использовать один и тот же кейс в соседних черновиках без явного запроса.",
                "Если новая идея похожа на старую идею или кейс, показать предупреждение перед черновиком.",
            ],
        }

    def _recent_updates(self) -> list[dict[str, str]]:
        updates = [
            {"type": "документ", "title": getattr(document, "title", ""), "updated_at": getattr(document, "uploaded_at", "")}
            for document in self.documents[:5]
        ]
        updates.extend(
            {"type": "кейс", "title": getattr(case, "title", ""), "updated_at": getattr(case, "created_at", "")}
            for case in self.cases[:3]
        )
        return updates[:8]


class AuthorBrainRepository:
    def __init__(
        self,
        profile_path: Path = DEFAULT_AUTHOR_BRAIN_PROFILE_PATH,
        status_path: Path = DEFAULT_AUTHOR_BRAIN_STATUS_PATH,
    ) -> None:
        self.profile_path = profile_path
        self.status_path = status_path
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    def load_profile(self) -> dict[str, object]:
        try:
            raw = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return self.empty_profile()
        return raw if isinstance(raw, dict) else self.empty_profile()

    def load_status(self) -> AuthorBrainStatus:
        try:
            raw = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return AuthorBrainStatus("idle", "Author Brain has not been refreshed yet.", "")
        return AuthorBrainStatus(
            state=str(raw.get("state", "idle")),
            message=str(raw.get("message", "")),
            updated_at=str(raw.get("updated_at", "")),
            error=str(raw.get("error", "")),
        )

    def refresh(self, brain: AuthorBrain) -> dict[str, object]:
        self.write_status("running", "Author Brain is updating from Knowledge, Writing DNA, and Lessons.")
        try:
            profile = self.apply_manual_overrides(brain.build_profile())
            self.profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.write_status("completed", "Author Brain profile updated.")
            return profile
        except Exception as exc:
            self.write_status("error", "Author Brain refresh failed. Last saved profile is still available.", str(exc))
            raise

    def start_background_refresh(self, brain: AuthorBrain) -> bool:
        if _REFRESH_LOCK.locked():
            self.write_status("running", "Author Brain refresh is already running.")
            return False

        def run() -> None:
            if not _REFRESH_LOCK.acquire(blocking=False):
                return
            try:
                self.refresh(brain)
            finally:
                _REFRESH_LOCK.release()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return True

    def write_status(self, state: str, message: str, error: str = "") -> None:
        raw = {
            "state": state,
            "message": message,
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.status_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def save_profile(self, profile: dict[str, object]) -> None:
        self.profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def apply_manual_overrides(self, profile: dict[str, object]) -> dict[str, object]:
        current = self.load_profile()
        controls = current.get("manual_author_base", {})
        if not isinstance(controls, dict):
            return profile
        merged = dict(profile)
        if controls.get("main_themes") and isinstance(current.get("main_themes"), list):
            merged["main_themes"] = current["main_themes"]
        if controls.get("key_ideas") and isinstance(current.get("key_ideas"), list):
            merged["key_ideas"] = current["key_ideas"]
        if controls.get("platform_fit") and isinstance(current.get("platform_fit"), dict):
            merged["platform_fit"] = current["platform_fit"]
        if controls.get("anti_repetition") and isinstance(current.get("anti_repetition"), dict):
            merged["anti_repetition"] = current["anti_repetition"]
        if controls:
            merged["manual_author_base"] = controls
        return merged

    def empty_profile(self) -> dict[str, object]:
        return {
            "version": AUTHOR_BRAIN_VERSION,
            "updated_at": "",
            "status": "empty",
            "main_themes": [],
            "theme_weight_rule": THEME_WEIGHT_RULE,
            "key_ideas": [],
            "cases": [],
            "content_angles": [],
            "platform_fit": PLATFORM_FIT,
            "thinking_style": [],
            "strengths": [],
            "anti_repetition": {"recent_ideas": [], "overused_theme_candidates": [], "case_rotation": [], "rules": []},
            "recent_updates": [],
            "source_counts": {"documents": 0, "cases": 0, "ideas": 0, "lessons": 0},
        }


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _as_dict_list(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _matches(query: str, text: str) -> bool:
    query_words = {word for word in re.findall(r"\w+", query.lower(), flags=re.UNICODE) if len(word) >= 4}
    text_words = {word for word in re.findall(r"\w+", text.lower(), flags=re.UNICODE) if len(word) >= 4}
    return bool(query_words.intersection(text_words))


def _looks_like_case(text: str) -> bool:
    lowered = text.lower()
    return any(name in lowered for name in ("mayrveda", "mriya", "красная поляна", "еврострой", "кейс", "case"))


def _tokens(text: str) -> set[str]:
    stop_words = {"and", "the", "for", "with", "это", "как", "что", "для"}
    return {
        word
        for word in re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        if len(word) > 2 and word not in stop_words
    }


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / max(1, len(left.union(right)))


def _sentences(text: str) -> list[str]:
    compact = " ".join(text.split())
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", compact) if 24 <= len(sentence.strip()) <= 260]


def _sentence_with_any(text: str, markers: tuple[str, ...]) -> str:
    for sentence in _sentences(text):
        lowered = sentence.lower()
        if any(marker.lower() in lowered for marker in markers):
            return sentence[:240]
    return ""


def _first_known_company(text: str) -> str:
    for company in ("MAYRVEDA", "Grand Marine Garden", "Mriya", "Красная Поляна", "Еврострой"):
        if company.lower() in text.lower():
            return company
    return ""


def _angle_fit(angle: str, corpus: str) -> str:
    if angle == "case" and any(marker in corpus for marker in ("case", "кейс", "mayrveda", "mriya")):
        return "strong"
    if angle == "analytics" and any(marker in corpus for marker in ("analytics", "bi", "data", "metric", "аналит")):
        return "strong"
    if angle == "framework" and any(marker in corpus for marker in ("sop", "framework", "standard", "process")):
        return "strong"
    if angle == "personal observation":
        return "strong"
    return "medium"
