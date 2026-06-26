from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IDEA_DIR = ROOT / "data" / "idea_vault"
DEFAULT_IDEA_INDEX_PATH = DEFAULT_IDEA_DIR / "ideas.json"
IDEA_STATUSES = ("New", "In Progress", "Drafted", "Published", "Archived")


@dataclass(frozen=True)
class Idea:
    id: str
    title: str
    description: str
    source: str
    status: str
    platforms: tuple[str, ...]
    created_at: str


class IdeaVault:
    def __init__(self, index_path: Path = DEFAULT_IDEA_INDEX_PATH) -> None:
        self.index_path = index_path
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index([])

    def list_ideas(self) -> list[Idea]:
        return [self._idea_from_raw(item) for item in self._read_index()]

    def get_idea(self, idea_id: str) -> Idea | None:
        for idea in self.list_ideas():
            if idea.id == idea_id:
                return idea
        return None

    def add_idea(
        self,
        title: str,
        description: str,
        source: str = "Manual",
        platforms: tuple[str, ...] | list[str] = (),
        status: str = "New",
    ) -> Idea:
        normalized_status = status if status in IDEA_STATUSES else "New"
        idea = Idea(
            id=uuid4().hex,
            title=title.strip(),
            description=description.strip(),
            source=source.strip() or "Manual",
            status=normalized_status,
            platforms=tuple(platform for platform in platforms if platform),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        items = self._read_index()
        items.insert(0, self._idea_to_raw(idea))
        self._write_index(items)
        return idea

    def update_status(self, idea_id: str, status: str) -> bool:
        if status not in IDEA_STATUSES:
            return False
        items = self._read_index()
        updated = False
        for item in items:
            if item.get("id") == idea_id:
                item["status"] = status
                updated = True
                break
        if updated:
            self._write_index(items)
        return updated

    def delete_idea(self, idea_id: str) -> bool:
        items = self._read_index()
        kept = [item for item in items if item.get("id") != idea_id]
        if len(kept) == len(items):
            return False
        self._write_index(kept)
        return True

    def _read_index(self) -> list[dict[str, object]]:
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _write_index(self, items: list[dict[str, object]]) -> None:
        self.index_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _idea_from_raw(self, item: dict[str, object]) -> Idea:
        return Idea(
            id=str(item["id"]),
            title=str(item.get("title", "")),
            description=str(item.get("description", "")),
            source=str(item.get("source", "Manual")),
            status=str(item.get("status", "New")),
            platforms=tuple(item.get("platforms", ())),
            created_at=str(item.get("created_at", "")),
        )

    def _idea_to_raw(self, idea: Idea) -> dict[str, object]:
        return {
            "id": idea.id,
            "title": idea.title,
            "description": idea.description,
            "source": idea.source,
            "status": idea.status,
            "platforms": list(idea.platforms),
            "created_at": idea.created_at,
        }
