from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_agent import bot_rules  # noqa: E402
from post_agent.author_brain import AuthorBrain  # noqa: E402


@pytest.fixture()
def temp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_BRAND_OS_DATA_DIR", str(tmp_path / "data"))
    # storage caches nothing important, but recompute the path for this data dir.
    monkeypatch.setattr(bot_rules, "BOT_RULES_PATH", (tmp_path / "data" / "seeds" / "bot_rules.json"))
    return tmp_path


def test_defaults_are_readable_russian():
    rules = bot_rules.default_bot_rules()
    assert "в современном мире" in rules["forbidden_openings"]
    assert rules["thinking_modes"][0] == "Observation"
    assert set(rules["platform_rules"]) == {"LinkedIn", "VC", "Telegram", "Сетка"}


def test_load_returns_defaults_when_file_missing(temp_data):
    assert bot_rules.load_bot_rules() == bot_rules.default_bot_rules()


def test_save_then_load_round_trips_edits(temp_data):
    bot_rules.save_bot_rules(
        {
            "thinking_rules": "новое правило\nвторое правило",
            "forbidden_openings": "нельзя так начинать",
            "anti_repeat_rules": "не повторяться",
            "theme_weight_rule": "мой вес тем",
            "thinking_modes": "Observation\nCase",
            "platform_rules": {"LinkedIn": "мой линкедин тон"},
        }
    )
    rules = bot_rules.load_bot_rules()
    assert rules["thinking_rules"] == ["новое правило", "второе правило"]
    assert rules["forbidden_openings"] == ["нельзя так начинать"]
    assert rules["theme_weight_rule"] == "мой вес тем"
    assert rules["thinking_modes"] == ["Observation", "Case"]
    # platform edit merges over defaults, other platforms kept.
    assert rules["platform_rules"]["LinkedIn"] == "мой линкедин тон"
    assert "VC" in rules["platform_rules"]


def test_empty_fields_fall_back_to_defaults(temp_data):
    bot_rules.save_bot_rules({"thinking_rules": "", "theme_weight_rule": ""})
    rules = bot_rules.load_bot_rules()
    assert rules["thinking_rules"] == bot_rules.default_bot_rules()["thinking_rules"]
    assert rules["theme_weight_rule"] == bot_rules.default_bot_rules()["theme_weight_rule"]


def test_author_brain_uses_edited_rules(temp_data):
    bot_rules.save_bot_rules(
        {
            "thinking_rules": "думать иначе",
            "forbidden_openings": "запретное начало",
            "thinking_modes": "Observation\nStory",
            "theme_weight_rule": "вес",
            "anti_repeat_rules": "без повторов",
            "platform_rules": {},
        }
    )
    brain = AuthorBrain(
        author_profile={},
        writing_dna={},
        documents=[],
        cases=[],
        ideas=[],
    ).build({"platform": "LinkedIn", "topic": "тест"})
    assert brain["author_moves"] == ["думать иначе"]
    assert brain["allowed_thinking_modes"] == ["Observation", "Story"]
    assert brain["forbidden_openings"] == ["запретное начало"]
    assert brain["anti_repeat_rules"] == ["без повторов"]
