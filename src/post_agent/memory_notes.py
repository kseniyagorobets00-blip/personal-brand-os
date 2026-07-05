from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .storage import data_path


DEFAULT_MEMORY_DIR = data_path("memory")
DEFAULT_MEMORY_NOTES_PATH = DEFAULT_MEMORY_DIR / "notes.json"

# Free-text memory groups that the author fills in by hand. Documents, cases and
# ideas have their own stores; these three are the "just text" groups.
MEMORY_NOTE_CATEGORIES = ("observation", "principle", "story")
MEMORY_NOTE_LABELS = {
    "observation": "Наблюдения",
    "principle": "Принципы",
    "story": "Истории",
}


@dataclass(frozen=True)
class MemoryNote:
    id: str
    category: str
    title: str
    text: str
    created_at: str


class MemoryNoteStore:
    """Persistent store for hand-written memory notes (observations, principles, stories)."""

    def __init__(self, path: Path = DEFAULT_MEMORY_NOTES_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def list_notes(self, category: str | None = None) -> list[MemoryNote]:
        notes = [self._from_raw(item) for item in self._read()]
        if category:
            notes = [note for note in notes if note.category == category]
        return notes

    def add_note(self, category: str, text: str, title: str = "") -> MemoryNote | None:
        category = category if category in MEMORY_NOTE_CATEGORIES else ""
        text = text.strip()
        if not category or not text:
            return None
        note = MemoryNote(
            id=uuid4().hex,
            category=category,
            title=title.strip(),
            text=text,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        raw = self._read()
        raw.insert(0, self._to_raw(note))
        self._write(raw)
        return note

    def delete_note(self, note_id: str) -> bool:
        raw = self._read()
        kept = [item for item in raw if str(item.get("id", "")) != note_id]
        if len(kept) == len(raw):
            return False
        self._write(kept)
        return True

    def for_ai(self, category: str, limit: int = 20) -> list[str]:
        """Compact list of note strings for prompt/context building."""
        result: list[str] = []
        for note in self.list_notes(category)[:limit]:
            if note.title:
                result.append(f"{note.title}: {note.text}")
            else:
                result.append(note.text)
        return result

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write(self, items: list[dict[str, object]]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _from_raw(self, item: dict[str, object]) -> MemoryNote:
        return MemoryNote(
            id=str(item.get("id", "")),
            category=str(item.get("category", "")),
            title=str(item.get("title", "")),
            text=str(item.get("text", "")),
            created_at=str(item.get("created_at", "")),
        )

    def _to_raw(self, note: MemoryNote) -> dict[str, object]:
        return {
            "id": note.id,
            "category": note.category,
            "title": note.title,
            "text": note.text,
            "created_at": note.created_at,
        }
