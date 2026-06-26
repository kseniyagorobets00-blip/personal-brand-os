from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WRITING_DNA_PATH = ROOT / "data" / "seeds" / "writing_dna.json"


DEFAULT_WRITING_DNA: dict[str, object] = {
    "main_goal": "Писать так, чтобы читатель чувствовал: это написал живой практик, а не AI, учебник или консультант ради консультирования.",
    "origin_of_posts": (
        "Публикация почти никогда не начинается с темы. Она начинается с наблюдения: разговор с клиентом, рабочая встреча, "
        "ситуация на проекте, ошибка бизнеса, повторяющаяся закономерность, интересный вопрос или противоречие."
    ),
    "story_rule": (
        "Если есть подходящий реальный кейс в памяти, использовать его естественно. Если кейса нет, можно создать типичную рабочую ситуацию "
        "без реальных компаний, цифр, фактов и несуществующих проектов."
    ),
    "tone": "Разговорный, профессиональный, живой. Иногда легкая ирония или сарказм, но без язвительности, агрессии и академического языка.",
    "paragraphs": "Естественные абзацы по 2-5 предложений. Не типичный LinkedIn, где каждое предложение с новой строки.",
    "allowed_phrases": [
        "Мне кажется...",
        "Все чаще замечаю...",
        "Последнее время вижу...",
        "Иногда складывается ощущение...",
        "Каждый раз удивляюсь...",
        "Недавно поймала себя на мысли...",
        "Если честно...",
    ],
    "argumentation_patterns": [
        "наблюдение -> что происходит -> почему это происходит -> типичная ошибка -> пример -> практический вывод -> вопрос читателю",
        "история -> разбор -> причина -> новый взгляд -> вывод",
    ],
    "forbidden_openings": [
        "В современном мире",
        "Сегодня многие компании",
        "В бизнесе часто",
        "Не секрет",
        "Многие считают",
        "Customer Experience — это",
        "Service Design — это",
        "Искусственный интеллект сегодня",
        "В эпоху цифровизации",
    ],
    "memory_usage": (
        "Если есть релевантный кейс пользователя, использовать его как живую часть рассуждения, например: "
        "'В одном из проектов в MAYRVEDA я особенно хорошо увидела...'. Не писать 'Использован кейс...'."
    ),
    "draft_rule": "Первый черновик — только готовый текст публикации. Не писать цель, основную мысль, структуру, инструкции или объяснение логики.",
    "self_check": [
        "ощущается ли живой автор",
        "есть ли наблюдение",
        "есть ли история или рабочая ситуация",
        "нет ли ощущения учебника",
        "нет ли AI-клише",
        "есть ли естественный разговорный язык",
        "похож ли текст на предыдущие публикации автора",
    ],
    "anti_template_rule": "Writing DNA задает вероятности, а не шаблон. Каждая публикация должна быть новой, но узнаваемо написанной одним автором.",
}


class WritingDNARepository:
    def __init__(self, path: Path = DEFAULT_WRITING_DNA_PATH) -> None:
        self.path = path

    def load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            self.save_raw(DEFAULT_WRITING_DNA)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(DEFAULT_WRITING_DNA)
        return raw

    def save_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def writing_dna_form_to_raw(data: dict[str, list[str]]) -> dict[str, object]:
    def value(name: str) -> str:
        return data.get(name, [""])[0].strip()

    def lines(name: str) -> list[str]:
        return [line.strip() for line in value(name).splitlines() if line.strip()]

    return {
        "main_goal": value("main_goal"),
        "origin_of_posts": value("origin_of_posts"),
        "story_rule": value("story_rule"),
        "tone": value("tone"),
        "paragraphs": value("paragraphs"),
        "allowed_phrases": lines("allowed_phrases"),
        "argumentation_patterns": lines("argumentation_patterns"),
        "forbidden_openings": lines("forbidden_openings"),
        "memory_usage": value("memory_usage"),
        "draft_rule": value("draft_rule"),
        "self_check": lines("self_check"),
        "anti_template_rule": value("anti_template_rule"),
    }
