from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_DIR = ROOT / "data" / "memory"
DEFAULT_MEMORY_INBOX_PATH = DEFAULT_MEMORY_DIR / "inbox.json"


@dataclass(frozen=True)
class MemoryInboxItem:
    id: str
    source_type: str
    source_id: str
    title: str
    summary: str
    extracted: dict[str, object]
    status: str
    created_at: str


class MemoryInbox:
    """Staging area for new knowledge before it becomes trusted memory."""

    def __init__(self, path: Path = DEFAULT_MEMORY_INBOX_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def list_items(self, status: str | None = None) -> list[MemoryInboxItem]:
        items = [self._from_raw(item) for item in self._read()]
        if status:
            return [item for item in items if item.status == status]
        return items

    def add_item(
        self,
        source_type: str,
        source_id: str,
        title: str,
        summary: str,
        extracted: dict[str, object],
        status: str = "pending",
    ) -> MemoryInboxItem:
        item = MemoryInboxItem(
            id=uuid4().hex,
            source_type=source_type,
            source_id=source_id,
            title=title.strip(),
            summary=summary.strip(),
            extracted=extracted,
            status=status,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        raw = self._read()
        raw.insert(0, self._to_raw(item))
        self._write(raw)
        return item

    def accept(self, item_id: str) -> bool:
        return self._set_status(item_id, "accepted")

    def reject(self, item_id: str) -> bool:
        return self._set_status(item_id, "rejected")

    def _set_status(self, item_id: str, status: str) -> bool:
        items = self._read()
        changed = False
        for item in items:
            if item.get("id") == item_id:
                item["status"] = status
                changed = True
        if changed:
            self._write(items)
        return changed

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write(self, items: list[dict[str, object]]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _from_raw(self, item: dict[str, object]) -> MemoryInboxItem:
        extracted = item.get("extracted", {})
        return MemoryInboxItem(
            id=str(item.get("id", "")),
            source_type=str(item.get("source_type", "")),
            source_id=str(item.get("source_id", "")),
            title=str(item.get("title", "")),
            summary=str(item.get("summary", "")),
            extracted=extracted if isinstance(extracted, dict) else {},
            status=str(item.get("status", "pending")),
            created_at=str(item.get("created_at", "")),
        )

    def _to_raw(self, item: MemoryInboxItem) -> dict[str, object]:
        return {
            "id": item.id,
            "source_type": item.source_type,
            "source_id": item.source_id,
            "title": item.title,
            "summary": item.summary,
            "extracted": item.extracted,
            "status": item.status,
            "created_at": item.created_at,
        }


def analyze_memory_text(title: str, text: str) -> dict[str, object]:
    """Cheap local extraction for MVP. AI enrichment can replace this later."""

    combined = f"{title}\n{text}"
    companies = _known_matches(combined, ("MAYRVEDA", "Mriya", "Красная Поляна", "Еврострой"))
    themes = _known_matches(
        combined,
        (
            "Customer Experience",
            "Operations",
            "Service Design",
            "Luxury Hospitality",
            "Hospitality",
            "SOP",
            "AI",
            "Project Management",
            "Operational Excellence",
        ),
    )
    ideas = _sentences_with(combined, ("идея", "вывод", "важно", "замечаю", "кажется"))[:5]
    quotes = _quote_like_sentences(combined)[:5]
    results = _sentences_with(combined, ("результат", "эффект", "стало", "сниз", "рост", "%"))[:5]
    cases = []
    if companies or "кейс" in combined.lower() or "case" in combined.lower():
        cases.append(
            {
                "title": title,
                "companies": companies,
                "context": _compact(text, 500),
                "themes": themes,
                "public_usage": "requires_confirmation",
            }
        )
    return {
        "projects": companies,
        "companies": companies,
        "roles": _known_matches(combined, ("руководитель", "операционный менеджер", "администратор", "front office", "служба сервиса")),
        "tools": _known_matches(combined, ("SOP", "service blueprint", "journey map", "SLA", "AI", "регламент")),
        "cases": cases,
        "results": results,
        "numbers": re.findall(r"\b\d+(?:[.,]\d+)?\s?%?\b", combined)[:10],
        "conclusions": ideas,
        "ideas": ideas,
        "quotes": quotes,
        "themes": themes,
        "favorite_phrases": _sentences_with(combined, ("мне кажется", "замечаю", "если честно", "поймала себя"))[:5],
    }


def _known_matches(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _sentences_with(text: str, needles: tuple[str, ...]) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", _compact(text, 4000))
    result = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(needle.lower() in lowered for needle in needles):
            result.append(sentence.strip())
    return [item for item in result if item]


def _quote_like_sentences(text: str) -> list[str]:
    quoted = re.findall(r"[«\"]([^»\"]{20,220})[»\"]", text)
    return [item.strip() for item in quoted if item.strip()]


def _compact(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]
