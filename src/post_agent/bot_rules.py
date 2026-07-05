from __future__ import annotations

"""Editable bot rules.

Historically the author's built-in rules (thinking moves, forbidden openings,
platform rules, anti-repeat rules, theme weight rule, thinking modes) were
hardcoded constants that the user could not see or change. This module turns
them into a single JSON file under the synced data directory so they can be
viewed and edited from the UI ("Правила бота") and reconfigured later.

The code constants remain the defaults: if the JSON file is missing or a field
is empty, the corresponding default is used, so behaviour never breaks.
"""

import json

from .author_brain import (
    DEFAULT_AUTHOR_MOVES,
    FORBIDDEN_OPENINGS,
    PLATFORM_FIT,
    THEME_WEIGHT_RULE,
    THINKING_MODES,
)
from .storage import data_path

BOT_RULES_PATH = data_path("seeds", "bot_rules.json")

DEFAULT_ANTI_REPEAT_RULES = (
    "Не предлагать тему, если она слишком похожа на недавние идеи.",
    "Не использовать один и тот же кейс в соседних черновиках без явного запроса.",
    "Если новая идея похожа на старую идею или кейс, показывать предупреждение перед черновиком.",
)

# The rubric "recipe": which steps each content rubric should follow. Editable in "Правила бота".
DEFAULT_RUBRIC_RULES = {
    "Аналитика": ["проблема", "причина", "закономерность", "управленческий вывод"],
    "Кейс": ["проблема", "действия", "результат", "бизнес-эффект", "урок"],
    "Framework": ["модель", "3-5 элементов", "применение", "вывод"],
    "Наблюдение": ["рабочая ситуация", "вывод", "вопрос к аудитории"],
    "Разбор ошибки": ["ошибка", "почему возникает", "как исправить", "профилактика"],
    "Миф": ["миф", "почему он живет", "что происходит на практике", "новая формулировка"],
    "Storytelling": ["ситуация", "напряжение", "поворот", "смысл"],
    "Разговорный пост": ["живой тон", "личная мысль", "без академического стиля"],
    "Инструменты": ["задача", "инструмент", "как применять", "ограничение"],
    "Ответ на вопрос": ["вопрос", "короткий ответ", "логика", "пример"],
}

# The keys used everywhere. list-of-lines fields vs. single-text vs. platform map vs. rubric map-of-lists.
LIST_FIELDS = ("thinking_rules", "forbidden_openings", "anti_repeat_rules", "thinking_modes")
TEXT_FIELDS = ("theme_weight_rule",)
MAP_FIELDS = ("platform_rules",)
RUBRIC_FIELD = "rubric_rules"


def default_bot_rules() -> dict[str, object]:
    return {
        "thinking_rules": list(DEFAULT_AUTHOR_MOVES),
        "forbidden_openings": list(FORBIDDEN_OPENINGS),
        "platform_rules": dict(PLATFORM_FIT),
        "anti_repeat_rules": list(DEFAULT_ANTI_REPEAT_RULES),
        "theme_weight_rule": THEME_WEIGHT_RULE,
        "thinking_modes": list(THINKING_MODES),
        "rubric_rules": {rubric: list(steps) for rubric, steps in DEFAULT_RUBRIC_RULES.items()},
    }


def load_bot_rules() -> dict[str, object]:
    """Effective rules: user file merged over the code defaults."""
    defaults = default_bot_rules()
    if not BOT_RULES_PATH.exists():
        return defaults
    try:
        raw = json.loads(BOT_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    merged = dict(defaults)
    for key in defaults:
        value = raw.get(key)
        if not value:
            continue
        if key == RUBRIC_FIELD and isinstance(value, dict):
            rubric_merged = dict(defaults[key])
            for rubric, steps in value.items():
                if isinstance(steps, str):
                    steps = steps.splitlines()
                cleaned = [str(step).strip() for step in steps if str(step).strip()] if isinstance(steps, list) else []
                if cleaned:
                    rubric_merged[str(rubric)] = cleaned
            merged[key] = rubric_merged
        elif key in MAP_FIELDS and isinstance(value, dict):
            merged[key] = {**defaults[key], **{str(k): str(v) for k, v in value.items() if str(v).strip()}}
        elif key in LIST_FIELDS and isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            if cleaned:
                merged[key] = cleaned
        elif key in TEXT_FIELDS and str(value).strip():
            merged[key] = str(value).strip()
    return merged


def save_bot_rules(data: dict[str, object]) -> dict[str, object]:
    """Validate against defaults, keep only known keys, and persist."""
    defaults = default_bot_rules()
    clean: dict[str, object] = {}
    for key in LIST_FIELDS:
        value = data.get(key, defaults[key])
        if isinstance(value, str):
            value = [line.strip() for line in value.splitlines()]
        clean[key] = [str(item).strip() for item in value if str(item).strip()] or list(defaults[key])  # type: ignore[arg-type]
    for key in TEXT_FIELDS:
        text = str(data.get(key, "")).strip()
        clean[key] = text or defaults[key]
    for key in MAP_FIELDS:
        value = data.get(key, {})
        if isinstance(value, dict):
            merged = {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}
        else:
            merged = {}
        clean[key] = {**defaults[key], **merged}  # type: ignore[dict-item]
    rubric_value = data.get(RUBRIC_FIELD, {})
    rubric_clean = dict(defaults[RUBRIC_FIELD])  # type: ignore[arg-type]
    if isinstance(rubric_value, dict):
        for rubric, steps in rubric_value.items():
            if isinstance(steps, str):
                steps = steps.splitlines()
            cleaned = [str(step).strip() for step in steps if str(step).strip()] if isinstance(steps, list) else []
            if cleaned:
                rubric_clean[str(rubric)] = cleaned
    clean[RUBRIC_FIELD] = rubric_clean
    BOT_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOT_RULES_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return clean
