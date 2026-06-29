from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUTHOR_BRAIN_DIR = ROOT / "data" / "author_brain"
DEFAULT_AUTHOR_BRAIN_PROFILE_PATH = DEFAULT_AUTHOR_BRAIN_DIR / "profile.json"
DEFAULT_AUTHOR_BRAIN_STATUS_PATH = DEFAULT_AUTHOR_BRAIN_DIR / "status.json"
AUTHOR_BRAIN_VERSION = "2.0"
_REFRESH_LOCK = threading.Lock()

THINKING_MODES = (
    "Observation",
    "Story",
    "Case",
    "Provocation",
    "Framework",
    "Reflection",
)

FORBIDDEN_OPENINGS = (
    "РІ СЃРѕРІСЂРµРјРµРЅРЅРѕРј РјРёСЂРµ",
    "СЃРµРіРѕРґРЅСЏ РјРЅРѕРіРёРµ РєРѕРјРїР°РЅРёРё",
    "РІ Р±РёР·РЅРµСЃРµ С‡Р°СЃС‚Рѕ",
    "РЅРµ СЃРµРєСЂРµС‚",
    "РјРЅРѕРіРёРµ СЃС‡РёС‚Р°СЋС‚",
    "customer experience вЂ” СЌС‚Рѕ",
    "customer experience - СЌС‚Рѕ",
    "service design вЂ” СЌС‚Рѕ",
    "service design - СЌС‚Рѕ",
    "РёСЃРєСѓСЃСЃС‚РІРµРЅРЅС‹Р№ РёРЅС‚РµР»Р»РµРєС‚ СЃРµРіРѕРґРЅСЏ",
    "РІ СЌРїРѕС…Сѓ С†РёС„СЂРѕРІРёР·Р°С†РёРё",
)

DEFAULT_AUTHOR_MOVES = (
    "РЅР°С‡РёРЅР°С‚СЊ СЃ СЂР°Р±РѕС‡РµРіРѕ РЅР°Р±Р»СЋРґРµРЅРёСЏ, Р° РЅРµ СЃ РѕРїСЂРµРґРµР»РµРЅРёСЏ С‚РµРјС‹",
    "Р±С‹СЃС‚СЂРѕ РїРµСЂРµС…РѕРґРёС‚СЊ РѕС‚ СЃРёРјРїС‚РѕРјР° Рє СѓРїСЂР°РІР»РµРЅС‡РµСЃРєРѕР№ РїСЂРёС‡РёРЅРµ",
    "РѕР±СЉСЏСЃРЅСЏС‚СЊ С‡РµСЂРµР· РѕС‚РІРµС‚СЃС‚РІРµРЅРЅРѕСЃС‚СЊ, С‚РѕС‡РєРё РїРµСЂРµРґР°С‡Рё Рё РѕРїРµСЂР°С†РёРѕРЅРЅСѓСЋ Р·СЂРµР»РѕСЃС‚СЊ",
    "РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ РїСЂР°РєС‚РёС‡РµСЃРєРёР№ РІС‹РІРѕРґ РІРјРµСЃС‚Рѕ Р°РєР°РґРµРјРёС‡РµСЃРєРѕРіРѕ СЂРµР·СЋРјРµ",
    "Р·Р°РґР°РІР°С‚СЊ С‡РёС‚Р°С‚РµР»СЋ РІРѕРїСЂРѕСЃ, РєРѕС‚РѕСЂС‹Р№ С…РѕС‡РµС‚СЃСЏ РїСЂРёРјРµРЅРёС‚СЊ Рє СЃРІРѕРµР№ СЃРёСЃС‚РµРјРµ",
)

CORE_THEMES = (
    ("operations", ("operations", "operational", "process", "handoff", "ownership", "операц", "процесс")),
    ("customer experience", ("customer experience", "cx", "guest experience", "клиент", "опыт")),
    ("service systems", ("service system", "service design", "blueprint", "сервис")),
    ("hospitality", ("hospitality", "hotel", "guest", "гост", "отел")),
    ("premium service", ("premium", "luxury", "personalization", "преми", "luxury hospitality")),
    ("process improvement", ("improvement", "diagnostics", "audit", "maturity", "улучш", "диагност")),
    ("BI / analytics", ("bi", "analytics", "dashboard", "data", "metric", "аналит", "данн")),
    ("SOP", ("sop", "standard", "regulation", "регламент", "стандарт")),
    ("управленческие системы", ("management system", "управлен", "ответствен", "контрол")),
)

CONTENT_ANGLES = (
    "personal observation",
    "case",
    "analytics",
    "provocation",
    "framework",
    "practical teardown",
)

PLATFORM_FIT = {
    "LinkedIn": "English, executive/consulting tone, clear business effect.",
    "VC": "Russian, expert but alive, practical teardown or case logic.",
    "Telegram": "Short, conversational, one thought with a working observation.",
    "Сетка": "Observation-first, short thoughts, low ceremony.",
    "РЎРµС‚РєР°": "Observation-first, short thoughts, low ceremony.",
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
            "key_ideas": key_ideas,
            "cases": cases,
            "content_angles": self._content_angles(corpus),
            "platform_fit": PLATFORM_FIT,
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
            "author_identity": "РљСЃРµРЅРёСЏ, РїСЂР°РєС‚РёРє Рё РєРѕРЅСЃСѓР»СЊС‚Р°РЅС‚ РЅР° РїРµСЂРµСЃРµС‡РµРЅРёРё Operations, Customer Experience, Service Design, Hospitality Рё AI.",
            "generation_intent": (
                "РџРёСЃР°С‚СЊ С‚Р°Рє, РєР°Рє РµСЃР»Рё Р±С‹ РљСЃРµРЅРёСЏ СѓС‚СЂРѕРј СЃРµР»Р° Р·Р° РїСѓР±Р»РёРєР°С†РёСЋ РїРѕСЃР»Рµ СЂР°Р±РѕС‡РµРіРѕ СЂР°Р·РіРѕРІРѕСЂР°, Р°СѓРґРёС‚Р°, РїСЂРѕРµРєС‚Р° "
                "РёР»Рё СЃРІРµР¶РµРіРѕ РЅР°Р±Р»СЋРґРµРЅРёСЏ. Р­С‚Рѕ РЅРµ СѓРЅРёРІРµСЂСЃР°Р»СЊРЅР°СЏ СЌРєСЃРїРµСЂС‚РЅР°СЏ СЃС‚Р°С‚СЊСЏ, Р° С…РѕРґ РјС‹СЃР»Рё РєРѕРЅРєСЂРµС‚РЅРѕРіРѕ Р°РІС‚РѕСЂР°."
            ),
            "writing_dna": self.writing_dna,
            "thinking_mode": self._select_mode(platform, query),
            "allowed_thinking_modes": list(THINKING_MODES),
            "voice_principles": self._voice_principles(platform),
            "author_moves": list(DEFAULT_AUTHOR_MOVES),
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
            "similarity_report": self.similarity_report(topic or summary or goal, profile),
            "self_check": {
                "question": "РџРѕС…РѕР¶Рµ Р»Рё СЌС‚Рѕ РЅР° РљСЃРµРЅРёСЋ?",
                "criteria": [
                    "РµСЃС‚СЊ Р¶РёРІРѕРµ СЂР°Р±РѕС‡РµРµ РЅР°Р±Р»СЋРґРµРЅРёРµ РёР»Рё СЃРёС‚СѓР°С†РёСЏ",
                    "РЅРµС‚ РѕС‰СѓС‰РµРЅРёСЏ AI РёР»Рё СѓС‡РµР±РЅРёРєР°",
                    "РјС‹СЃР»СЊ РёРґРµС‚ РѕС‚ СЃРёРјРїС‚РѕРјР° Рє РїСЂРёС‡РёРЅРµ Рё РїСЂР°РєС‚РёС‡РµСЃРєРѕРјСѓ РІС‹РІРѕРґСѓ",
                    "РёСЃРїРѕР»СЊР·РѕРІР°РЅ РїРѕРґС…РѕРґСЏС‰РёР№ СЂРµР°Р»СЊРЅС‹Р№ РєРµР№СЃ РёР»Рё С‚РµРєСЃС‚ С‡РµСЃС‚РЅРѕ РѕР±С…РѕРґРёСЃСЏ Р±РµР· РІС‹РґСѓРјР°РЅРЅРѕРіРѕ РєРµР№СЃР°",
                    "РЅРµС‚ Р·Р°РїСЂРµС‰РµРЅРЅС‹С… РІСЃС‚СѓРїР»РµРЅРёР№ Рё СЃР»СѓР¶РµР±РЅС‹С… РёРЅСЃС‚СЂСѓРєС†РёР№",
                ],
                "if_weak": "Р’С‹РїРѕР»РЅРёС‚СЊ РѕРґРЅСѓ РІРЅСѓС‚СЂРµРЅРЅСЋСЋ РёС‚РµСЂР°С†РёСЋ СѓР»СѓС‡С€РµРЅРёСЏ РґРѕ С„РёРЅР°Р»СЊРЅРѕРіРѕ JSON.",
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
            principles.append("РґРѕРїСѓСЃС‚РёРјР° СЂР°Р·РіРѕРІРѕСЂРЅР°СЏ РёРЅС‚РѕРЅР°С†РёСЏ, РєР°Рє РЅР°Р±Р»СЋРґРµРЅРёРµ РєРѕР»Р»РµРіРµ")
        if platform == "VC":
            principles.append("РјРѕР¶РЅРѕ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ РїРѕРґР·Р°РіРѕР»РѕРІРєРё, РЅРѕ С‚РµРєСЃС‚ РґРѕР»Р¶РµРЅ РѕСЃС‚Р°РІР°С‚СЊСЃСЏ РѕРїС‹С‚РѕРј РїСЂР°РєС‚РёРєР°")
        if platform in {"РЎРµС‚РєР°", "Сетка"}:
            principles.append("РєРѕСЂРѕС‚РєРѕ: СЃРёС‚СѓР°С†РёСЏ, РІС‹РІРѕРґ, РІРѕРїСЂРѕСЃ")
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
        rules = self.author_profile.get("platform_rules", {})
        return str(rules.get(platform, "")) if isinstance(rules, dict) else ""

    def _what_not_to_write(self) -> list[str]:
        rules = _as_str_list(self.author_profile.get("what_not_to_write", []))
        dna_rules = [str(self.writing_dna.get("draft_rule", "")), str(self.writing_dna.get("anti_template_rule", ""))]
        return [*rules, *[item for item in dna_rules if item], "РЅРµ РЅР°С‡РёРЅР°С‚СЊ СЃ РѕРїСЂРµРґРµР»РµРЅРёСЏ РїРѕРЅСЏС‚РёСЏ", "РЅРµ Р·РІСѓС‡Р°С‚СЊ РєР°Рє СѓРЅРёРІРµСЂСЃР°Р»СЊРЅС‹Р№ РєРѕРЅСЃСѓР»СЊС‚Р°РЅС‚ РёР»Рё ChatGPT"]

    def _forbidden_openings(self) -> list[str]:
        configured = _as_str_list(self.writing_dna.get("forbidden_openings", []))
        return configured or list(FORBIDDEN_OPENINGS)

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
                        "usage_rule": "РњРѕР¶РЅРѕ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ С‚РѕР»СЊРєРѕ С„Р°РєС‚С‹ РёР· excerpt/content. РќРµ РїСЂРёРґСѓРјС‹РІР°С‚СЊ РґРµС‚Р°Р»Рё РєРµР№СЃР°.",
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
        lowered = query.lower()
        if self._case_candidates(query):
            return "Case"
        if platform == "VC":
            return "Framework"
        if any(word in lowered for word in ("РѕС€РёР±РєР°", "С…Р°РѕСЃ", "РјРёС„", "РЅРµ СЂР°Р±РѕС‚Р°РµС‚", "mistake", "chaos", "myth")):
            return "Provocation"
        if any(word in lowered for word in ("РЅР°Р±Р»СЋРґРµРЅРёРµ", "СЃРёС‚СѓР°С†РёСЏ", "РїСЂРёРјРµСЂ", "observation", "example")):
            return "Observation"
        if platform in {"Telegram", "РЎРµС‚РєР°", "Сетка"}:
            return "Reflection"
        return "Observation"

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
            if evidence or theme in {"operations", "customer experience", "service systems", "hospitality", "SOP"}:
                themes.append(
                    {
                        "name": theme,
                        "score": min(100, 42 + count * 8 + len(evidence) * 6),
                        "evidence": evidence[:5],
                        "risk": "rotate with adjacent angles" if count > 5 else "",
                    }
                )
        return sorted(themes, key=lambda item: int(item["score"]), reverse=True)

    def _key_ideas(self, corpus: str) -> list[dict[str, object]]:
        sentences = _sentences(corpus)
        defaults = [
            "Customer experience is an operational outcome, not only a communication layer.",
            "SOP and standards protect service from randomness when connected to responsibility.",
            "AI amplifies process maturity and exposes gaps in data, ownership, and handoffs.",
            "Premium service needs both human attention and repeatable systems.",
            "Service design becomes useful when it reaches implementation, roles, control points, and metrics.",
            "Business symptoms usually point to deeper management-system causes.",
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
                    "usage_risk": "avoid using this case too often",
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
                    "usage_risk": "use only facts present in Knowledge",
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
                "Starts from a working observation before moving to a management cause.",
                "Connects service quality with operations, ownership, standards, and data.",
                "Prefers practical conclusions over abstract definitions.",
            ]
        )
        return result[:8]

    def _strengths(self, themes: list[dict[str, object]], cases: list[dict[str, object]]) -> list[str]:
        result = [
            "Operational diagnosis of service problems.",
            "Translation of customer experience into process, roles, SOP, and control points.",
            "Ability to turn cases and observations into executive content.",
        ]
        if cases:
            result.append("Real case base for hospitality and service-system content.")
        if any(str(theme.get("name", "")).lower() == "bi / analytics" for theme in themes):
            result.append("Connects service and operations with BI / analytics logic.")
        return result

    def _anti_repetition(self, key_ideas: list[dict[str, object]], cases: list[dict[str, object]]) -> dict[str, object]:
        recent_ideas = [{"title": getattr(idea, "title", ""), "description": getattr(idea, "description", "")} for idea in self.ideas[:12]]
        return {
            "recent_ideas": recent_ideas,
            "overused_theme_candidates": [str(item["idea"]) for item in key_ideas if str(item.get("repeat_risk", "")) == "high"][:4],
            "case_rotation": [str(item.get("project") or item.get("company", "")) for item in cases[:6] if item.get("project") or item.get("company")],
            "rules": [
                "Do not propose a topic if it is strongly similar to recent ideas.",
                "Do not reuse the same case in consecutive drafts unless the user explicitly asks.",
                "If an idea matches an old idea or case, show the similarity warning before drafting.",
            ],
        }

    def _recent_updates(self) -> list[dict[str, str]]:
        updates = [
            {"type": "document", "title": getattr(document, "title", ""), "updated_at": getattr(document, "uploaded_at", "")}
            for document in self.documents[:5]
        ]
        updates.extend(
            {"type": "case", "title": getattr(case, "title", ""), "updated_at": getattr(case, "created_at", "")}
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
            profile = brain.build_profile()
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

    def empty_profile(self) -> dict[str, object]:
        return {
            "version": AUTHOR_BRAIN_VERSION,
            "updated_at": "",
            "status": "empty",
            "main_themes": [],
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
    return any(name in lowered for name in ("mayrveda", "mriya", "РєСЂР°СЃРЅР°СЏ РїРѕР»СЏРЅР°", "РµРІСЂРѕСЃС‚СЂРѕР№", "РєРµР№СЃ", "case"))


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
