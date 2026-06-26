from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEARNING_DIR = ROOT / "data" / "learning"
DEFAULT_LESSONS_PATH = DEFAULT_LEARNING_DIR / "lessons.json"


@dataclass(frozen=True)
class Lesson:
    id: str
    rule: str
    reason: str
    confidence: int
    status: str
    source: str
    created_at: str


class LearningCenter:
    """Human-approved learning. AI may suggest, user decides."""

    def __init__(self, path: Path = DEFAULT_LESSONS_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def list_lessons(self, status: str | None = None) -> list[Lesson]:
        lessons = [self._from_raw(item) for item in self._read()]
        if status:
            return [lesson for lesson in lessons if lesson.status == status]
        return lessons

    def create_candidate(self, rule: str, reason: str, confidence: int = 60, source: str = "feedback") -> Lesson:
        lesson = Lesson(
            id=uuid4().hex,
            rule=rule.strip(),
            reason=reason.strip(),
            confidence=max(0, min(100, int(confidence))),
            status="candidate",
            source=source,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        raw = self._read()
        raw.insert(0, self._to_raw(lesson))
        self._write(raw)
        return lesson

    def create_candidate_from_feedback(self, feedback: str, draft_title: str = "") -> Lesson:
        rule, confidence = infer_lesson_from_feedback(feedback)
        reason = f"Правило предложено из комментария к черновику «{draft_title}»: {feedback.strip()}"
        return self.create_candidate(rule=rule, reason=reason, confidence=confidence, source="writing_feedback")

    def update(self, lesson_id: str, status: str, rule: str | None = None) -> bool:
        allowed = {"candidate", "accepted", "rejected"}
        if status not in allowed:
            return False
        raw = self._read()
        changed = False
        for item in raw:
            if item.get("id") == lesson_id:
                item["status"] = status
                if rule is not None:
                    item["rule"] = rule.strip()
                changed = True
        if changed:
            self._write(raw)
        return changed

    def frequent_edit_patterns(self) -> list[str]:
        lessons = self.list_lessons()
        patterns: list[str] = []
        if any("нач" in lesson.rule.lower() and "наблюд" in lesson.rule.lower() for lesson in lessons):
            patterns.append("Автор предпочитает начинать не с определения, а с наблюдения.")
        if any("академ" in lesson.rule.lower() or "учебник" in lesson.rule.lower() for lesson in lessons):
            patterns.append("AI часто нужно уводить от академического тона.")
        if any("кейс" in lesson.rule.lower() for lesson in lessons):
            patterns.append("Автор часто усиливает текст реальными кейсами.")
        return patterns

    def learned_habits(self) -> list[str]:
        return [lesson.rule for lesson in self.list_lessons("accepted")]

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write(self, lessons: list[dict[str, object]]) -> None:
        self.path.write_text(json.dumps(lessons, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _from_raw(self, item: dict[str, object]) -> Lesson:
        return Lesson(
            id=str(item.get("id", "")),
            rule=str(item.get("rule", "")),
            reason=str(item.get("reason", "")),
            confidence=int(item.get("confidence", 0)),
            status=str(item.get("status", "candidate")),
            source=str(item.get("source", "")),
            created_at=str(item.get("created_at", "")),
        )

    def _to_raw(self, lesson: Lesson) -> dict[str, object]:
        return {
            "id": lesson.id,
            "rule": lesson.rule,
            "reason": lesson.reason,
            "confidence": lesson.confidence,
            "status": lesson.status,
            "source": lesson.source,
            "created_at": lesson.created_at,
        }


def infer_lesson_from_feedback(feedback: str) -> tuple[str, int]:
    text = feedback.strip()
    lowered = text.lower()
    if any(word in lowered for word in ("начало", "начала", "начать", "скучн")):
        return "Начинать публикацию с живого наблюдения, разговора или рабочей ситуации, а не с общего тезиса.", 78
    if any(word in lowered for word in ("академ", "учебник", "канцеляр", "не похоже")):
        return "Снижать академичность: писать как практик после рабочего разговора, без учебникового объяснения.", 82
    if any(word in lowered for word in ("mаyrveda", "mayrveda", "кейс", "гостиниц", "hospitality")):
        return "Если тема связана с сервисом или hospitality, сначала искать релевантный кейс или реалистичную рабочую ситуацию.", 75
    if any(word in lowered for word in ("вывод", "финал", "слаб")):
        return "Усиливать финальный вывод: завершать текст практическим управленческим наблюдением или вопросом.", 70
    if any(word in lowered for word in ("сарказм", "ирони")):
        return "Допускать легкую иронию, но без агрессии и язвительности.", 65
    return "Учитывать этот комментарий как индивидуальное правило редактуры автора.", 55


def lessons_for_prompt(lessons: list[Lesson]) -> list[str]:
    return [lesson.rule for lesson in lessons if lesson.status == "accepted"]
