from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .storage import data_path


DEFAULT_TEXT_POSTS_DIR = data_path("text_posts")
DEFAULT_TEXT_POSTS_PATH = DEFAULT_TEXT_POSTS_DIR / "posts.json"
TEXT_POST_TABS = ("planned", "archive")
TEXT_POST_STATUSES = ("draft", "approved", "published")


@dataclass(frozen=True)
class TextPost:
    id: str
    tab: str
    title: str
    platform: str
    publication_date: str
    text: str
    status: str
    source: str
    source_key: str
    created_at: str
    updated_at: str
    brief: str = ""


class TextPostRepository:
    def __init__(self, path: Path = DEFAULT_TEXT_POSTS_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def sync_from_content_plan(self, plan: dict[str, object]) -> None:
        publications = plan.get("planned_publications", [])
        if not isinstance(publications, list):
            return
        raw = self._read()
        by_key = {str(item.get("source_key", "")): item for item in raw if isinstance(item, dict)}
        by_title_platform = {
            _title_platform_key(str(item.get("title", "")), str(item.get("platform", ""))): item
            for item in raw
            if isinstance(item, dict)
        }
        current_keys: set[str] = set()
        changed = False
        for publication in publications:
            if not isinstance(publication, dict):
                continue
            source_key = source_key_for_publication(publication)
            if not source_key:
                continue
            current_keys.add(source_key)
            status = _normalize_plan_status(str(publication.get("status", "")))
            tab = "archive" if status == "published" else "planned"
            title = str(publication.get("topic", "")).strip() or "Без названия"
            platform = str(publication.get("platform", "")).strip()
            existing = by_key.get(source_key) or by_title_platform.get(_title_platform_key(title, platform))
            if existing:
                existing["source_key"] = source_key
                existing["title"] = title
                existing["platform"] = platform
                existing["publication_date"] = str(publication.get("date") or existing.get("publication_date", "")).strip()
                existing["brief"] = _publication_brief(publication)
                # Clean up posts whose text was auto-filled with the brief (summary/note)
                # before text and brief were separated — but never touch text the user typed.
                current_text = existing.get("text", "").strip()
                legacy_autofill = {
                    str(publication.get("summary", "")).strip(),
                    str(publication.get("note", "")).strip(),
                } - {""}
                if current_text and current_text in legacy_autofill:
                    existing["text"] = _publication_text(publication)
                if existing.get("tab") != "archive":
                    existing["tab"] = tab
                if tab == "archive" and existing.get("status") != "published":
                    existing["status"] = "published"
                existing["updated_at"] = _now()
                changed = True
                continue
            raw.append(
                {
                    "id": uuid4().hex,
                    "tab": tab,
                    "title": title,
                    "platform": platform,
                    "publication_date": str(publication.get("date", "")).strip(),
                    "text": _publication_text(publication),
                    "brief": _publication_brief(publication),
                    "status": "published" if tab == "archive" else "draft",
                    "source": "content_plan",
                    "source_key": source_key,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )
            changed = True
        # Drop orphaned drafts left over from previous plan versions — but only
        # untouched ones (came from the content plan, still a draft, empty body).
        # Anything the user wrote, approved or archived is always kept.
        pruned = [item for item in raw if not _is_orphan_plan_draft(item, current_keys)]
        if len(pruned) != len(raw):
            raw = pruned
            changed = True
        if changed:
            self._write(raw)

    def list_posts(
        self,
        tab: str = "planned",
        query: str = "",
        platform: str = "",
    ) -> list[TextPost]:
        tab = tab if tab in TEXT_POST_TABS else "planned"
        lowered_query = query.strip().lower()
        platform = platform.strip()
        posts = [self._from_raw(item) for item in self._read()]
        result = []
        for post in posts:
            if post.tab != tab:
                continue
            if platform and post.platform != platform:
                continue
            if lowered_query and lowered_query not in post.title.lower():
                continue
            result.append(post)
        return sorted(result, key=_sort_key, reverse=True)

    def get(self, post_id: str) -> TextPost | None:
        for item in self._read():
            if str(item.get("id", "")) == post_id:
                return self._from_raw(item)
        return None

    def update(
        self,
        post_id: str,
        title: str,
        platform: str,
        publication_date: str,
        text: str,
        status: str,
        tab: str | None = None,
    ) -> bool:
        raw = self._read()
        changed = False
        for item in raw:
            if str(item.get("id", "")) != post_id:
                continue
            item["title"] = title.strip() or "Без названия"
            item["platform"] = platform.strip()
            item["publication_date"] = publication_date.strip()
            item["text"] = text.strip()
            item["status"] = status if status in TEXT_POST_STATUSES else "draft"
            if tab in TEXT_POST_TABS:
                item["tab"] = tab
            item["updated_at"] = _now()
            changed = True
        if changed:
            self._write(raw)
        return changed

    def add_archive(self, title: str, platform: str, publication_date: str, text: str) -> TextPost:
        return self._add_manual("archive", "published", title, platform, publication_date, text)

    def add_planned(self, title: str, platform: str, publication_date: str, text: str) -> TextPost:
        return self._add_manual("planned", "draft", title, platform, publication_date, text)

    def _add_manual(
        self, tab: str, status: str, title: str, platform: str, publication_date: str, text: str
    ) -> TextPost:
        item = {
            "id": uuid4().hex,
            "tab": tab,
            "title": title.strip() or "Без названия",
            "platform": platform.strip(),
            "publication_date": publication_date.strip(),
            "text": text.strip(),
            "status": status,
            "source": "manual",
            "source_key": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        raw = self._read()
        raw.insert(0, item)
        self._write(raw)
        return self._from_raw(item)

    def move_to_archive(self, post_id: str) -> bool:
        post = self.get(post_id)
        if not post:
            return False
        return self.update(
            post_id=post.id,
            title=post.title,
            platform=post.platform,
            publication_date=post.publication_date,
            text=post.text,
            status="published",
            tab="archive",
        )

    def delete(self, post_id: str) -> bool:
        raw = self._read()
        kept = [item for item in raw if str(item.get("id", "")) != post_id]
        if len(kept) == len(raw):
            return False
        self._write(kept)
        return True

    def approved_for_publication(self, publication: object) -> TextPost | None:
        if not publication:
            return None
        source_key = source_key_for_publication(publication if isinstance(publication, dict) else _publication_to_dict(publication))
        for post in self.list_posts("planned"):
            if post.source_key == source_key and post.status == "approved" and post.text.strip():
                return post
        candidate = _publication_to_dict(publication)
        title_platform = _title_platform_key(str(candidate.get("topic", "")), str(candidate.get("platform", "")))
        for post in self.list_posts("planned"):
            if _title_platform_key(post.title, post.platform) == title_platform and post.status == "approved" and post.text.strip():
                return post
        return None

    def published_for_ai(self, limit: int = 30) -> list[dict[str, str]]:
        return [
            {
                "title": post.title,
                "platform": post.platform,
                "publication_date": post.publication_date,
                "text": post.text[:2400],
                "source": post.source,
            }
            for post in self.list_posts("archive")[:limit]
            if post.text.strip()
        ]

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write(self, items: list[dict[str, object]]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _from_raw(self, item: dict[str, object]) -> TextPost:
        return TextPost(
            id=str(item.get("id", "")),
            tab=str(item.get("tab", "planned")) if str(item.get("tab", "planned")) in TEXT_POST_TABS else "planned",
            title=str(item.get("title", "")),
            platform=str(item.get("platform", "")),
            publication_date=str(item.get("publication_date", "")),
            text=str(item.get("text", "")),
            status=str(item.get("status", "draft")),
            source=str(item.get("source", "")),
            source_key=str(item.get("source_key", "")),
            created_at=str(item.get("created_at", "")),
            updated_at=str(item.get("updated_at", "")),
            brief=str(item.get("brief", "")),
        )


def _is_orphan_plan_draft(item: dict[str, object], current_keys: set[str]) -> bool:
    if not isinstance(item, dict):
        return False
    if str(item.get("source", "")) != "content_plan":
        return False
    if str(item.get("tab", "planned")) != "planned":
        return False
    if str(item.get("status", "draft")) != "draft":
        return False
    if str(item.get("text", "")).strip():
        return False
    return str(item.get("source_key", "")) not in current_keys


def source_key_for_publication(publication: dict[str, object]) -> str:
    title = str(publication.get("topic") or publication.get("title", "")).strip()
    platform = str(publication.get("platform", "")).strip()
    if not title and not platform:
        return ""
    raw = "\n".join((platform, title))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"content-plan:{digest}"


def _publication_text(publication: dict[str, object]) -> str:
    # Only the real post body (the generated/written draft). Brief info lives separately.
    return str(publication.get("draft") or "").strip()


def _publication_brief(publication: dict[str, object]) -> str:
    """The generation context (task) — kept out of the post body, shown read-only."""
    fields = (
        ("Цель", "goal"),
        ("Угол", "angle"),
        ("Главная мысль", "main_thought"),
        ("Краткое содержание", "summary"),
        ("Заметка", "note"),
    )
    lines = []
    for label, key in fields:
        # Collapse any internal newlines so each field stays a single, cleanly labelled line.
        value = " ".join(str(publication.get(key, "")).split())
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _publication_to_dict(publication: object) -> dict[str, object]:
    return {
        "date": str(getattr(publication, "date", "")),
        "platform": str(getattr(publication, "platform", "")),
        "topic": str(getattr(publication, "topic", "")),
    }


def _normalize_plan_status(status: str) -> str:
    return "published" if status in {"published", "Published", "archived", "Archived"} else "planned"


def _sort_key(post: TextPost) -> tuple[str, str]:
    return (post.publication_date, post.updated_at)


def _title_platform_key(title: str, platform: str) -> str:
    return f"{platform.strip().lower()}::{title.strip().lower()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
