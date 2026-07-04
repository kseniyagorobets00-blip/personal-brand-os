from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .storage import data_path

DEFAULT_AUTHOR_PROFILE_PATH = data_path("seeds", "author_profile.json")


@dataclass(frozen=True)
class ToneProfile:
    formality: str
    directness: str
    provocation: str
    emotionality: str


@dataclass(frozen=True)
class StructureProfile:
    post_structure: str
    intro_length: str
    narrative_logic: str
    conclusion: str


@dataclass(frozen=True)
class VocabularyProfile:
    favorite_words: tuple[str, ...]
    unwanted_words: tuple[str, ...]
    banned_cliches: tuple[str, ...]
    professional_terms: tuple[str, ...]


@dataclass(frozen=True)
class PlatformRule:
    platform: str
    rule: str


@dataclass(frozen=True)
class AuthorProfile:
    tone: ToneProfile
    structure: StructureProfile
    vocabulary: VocabularyProfile
    platform_rules: tuple[PlatformRule, ...]

    def rule_for_platform(self, platform: str) -> PlatformRule | None:
        normalized = platform.lower()
        for rule in self.platform_rules:
            if rule.platform.lower() == normalized:
                return rule
        return None


class AuthorProfileRepository:
    def __init__(self, path: Path = DEFAULT_AUTHOR_PROFILE_PATH) -> None:
        self.path = path

    def load(self) -> AuthorProfile:
        if not self.path.exists():
            raise FileNotFoundError(f"Author Profile file not found: {self.path}")
        return author_profile_from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"Author Profile file not found: {self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def author_profile_from_dict(data: dict[str, Any]) -> AuthorProfile:
    tone = data.get("tone", {})
    structure = data.get("structure", {})
    vocabulary = data.get("vocabulary", {})
    platform_rules = data.get("platform_rules", {})
    return AuthorProfile(
        tone=ToneProfile(
            formality=str(tone.get("formality", "professional")),
            directness=str(tone.get("directness", "direct")),
            provocation=str(tone.get("provocation", "moderate")),
            emotionality=str(tone.get("emotionality", "restrained")),
        ),
        structure=StructureProfile(
            post_structure=str(structure.get("post_structure", "")),
            intro_length=str(structure.get("intro_length", "")),
            narrative_logic=str(structure.get("narrative_logic", "")),
            conclusion=str(structure.get("conclusion", "")),
        ),
        vocabulary=VocabularyProfile(
            favorite_words=tuple(vocabulary.get("favorite_words", ())),
            unwanted_words=tuple(vocabulary.get("unwanted_words", ())),
            banned_cliches=tuple(vocabulary.get("banned_cliches", ())),
            professional_terms=tuple(vocabulary.get("professional_terms", ())),
        ),
        platform_rules=tuple(
            PlatformRule(platform=str(platform), rule=str(rule))
            for platform, rule in platform_rules.items()
        ),
    )


def list_to_text(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(items)


def text_to_list(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]
