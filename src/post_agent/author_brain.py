from __future__ import annotations

import re
from typing import Any


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


class AuthorBrain:
    """Builds a compact thinking profile for generation.

    Prompt Builder should consume this object instead of raw author memory files.
    """

    def __init__(
        self,
        author_profile: dict[str, Any],
        writing_dna: dict[str, Any] | None,
        documents: list[object],
        cases: list[object],
        ideas: list[object],
    ) -> None:
        self.author_profile = author_profile
        self.writing_dna = writing_dna or {}
        self.documents = documents
        self.cases = cases
        self.ideas = ideas

    def build(self, publication: dict[str, object] | None = None) -> dict[str, object]:
        publication = publication or {}
        platform = str(publication.get("platform", ""))
        topic = str(publication.get("topic", ""))
        summary = str(publication.get("summary", ""))
        goal = str(publication.get("goal", ""))
        query = " ".join((platform, topic, summary, goal))
        return {
            "author_identity": "Ксения, практик и консультант на пересечении Operations, Customer Experience, Service Design, Hospitality и AI.",
            "generation_intent": (
                "Писать так, как если бы Ксения утром села за публикацию после рабочего разговора, аудита, проекта "
                "или свежего наблюдения. Это не универсальная экспертная статья, а ход мысли конкретного автора."
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
            "self_check": {
                "question": "Похоже ли это на Ксению?",
                "criteria": [
                    "есть живое рабочее наблюдение или ситуация",
                    "нет ощущения AI или учебника",
                    "мысль идет от симптома к причине и практическому выводу",
                    "использован подходящий реальный кейс или текст честно обходится без выдуманного кейса",
                    "нет запрещенных вступлений и служебных инструкций",
                ],
                "if_weak": "Выполнить одну внутреннюю итерацию улучшения до финального JSON.",
            },
        }

    def _voice_principles(self, platform: str) -> list[str]:
        tone = self.author_profile.get("tone", {})
        structure = self.author_profile.get("structure", {})
        principles = [
            str(tone.get("formality", "")),
            str(tone.get("directness", "")),
            str(tone.get("provocation", "")),
            str(tone.get("emotionality", "")),
            str(structure.get("narrative_logic", "")),
            str(structure.get("conclusion", "")),
        ]
        if platform == "Telegram":
            principles.append("допустима разговорная интонация, как наблюдение коллеге")
        if platform == "VC":
            principles.append("можно использовать подзаголовки, но текст должен оставаться опытом практика")
        if platform == "Сетка":
            principles.append("коротко: ситуация, вывод, вопрос")
        return [item for item in principles if item]

    def _vocabulary(self) -> dict[str, list[str]]:
        vocabulary = self.author_profile.get("vocabulary", {})
        return {
            "favorite_words": _as_str_list(vocabulary.get("favorite_words", []))[:12],
            "unwanted_words": _as_str_list(vocabulary.get("unwanted_words", []))[:12],
            "professional_terms": _as_str_list(vocabulary.get("professional_terms", [])),
        }

    def _platform_goal(self, platform: str) -> str:
        goals = self.author_profile.get("platform_goals", {})
        return str(goals.get(platform, ""))

    def _platform_rule(self, platform: str) -> str:
        rules = self.author_profile.get("platform_rules", {})
        return str(rules.get(platform, ""))

    def _what_not_to_write(self) -> list[str]:
        rules = _as_str_list(self.author_profile.get("what_not_to_write", []))
        dna_rules = [str(self.writing_dna.get("draft_rule", "")), str(self.writing_dna.get("anti_template_rule", ""))]
        return [*rules, *[item for item in dna_rules if item], "не начинать с определения понятия", "не звучать как универсальный консультант или ChatGPT"]

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
            text = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", ""), getattr(document, "content_text", ""), " ".join(chunks)))
            if _looks_like_case(text) and _matches(query, text):
                candidates.append(
                    {
                        "type": "knowledge_case_note",
                        "title": getattr(document, "title", ""),
                        "excerpt": getattr(document, "excerpt", ""),
                        "chunks": list(chunks[:3]),
                        "usage_rule": "Можно использовать только факты из excerpt/content. Не придумывать детали кейса.",
                    }
                )
        return candidates[:4]

    def _knowledge_observations(self, query: str) -> list[dict[str, str]]:
        observations = []
        for document in self.documents:
            chunks = tuple(getattr(document, "semantic_chunks", ()))
            text = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", ""), " ".join(chunks)))
            if _matches(query, text):
                observations.append({"title": getattr(document, "title", ""), "excerpt": getattr(document, "excerpt", ""), "chunks": "\n\n".join(chunks[:3])})
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
        if any(word in lowered for word in ("ошибка", "хаос", "миф", "не работает")):
            return "Provocation"
        if any(word in lowered for word in ("наблюдение", "ситуация", "пример")):
            return "Observation"
        if platform in {"Telegram", "Сетка"}:
            return "Reflection"
        return "Observation"


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _matches(query: str, text: str) -> bool:
    query_words = set(re.findall(r"[A-Za-zА-Яа-я]{4,}", query.lower()))
    text_words = set(re.findall(r"[A-Za-zА-Яа-я]{4,}", text.lower()))
    return bool(query_words.intersection(text_words))


def _looks_like_case(text: str) -> bool:
    lowered = text.lower()
    return any(name in lowered for name in ("mayrveda", "mriya", "красная поляна", "еврострой", "кейс"))
