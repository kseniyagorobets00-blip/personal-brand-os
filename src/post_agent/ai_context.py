from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .author_brain import AuthorBrain, AuthorBrainRepository
from .author_profile import AuthorProfileRepository
from .daily_brief import DEFAULT_CONTENT_PLAN_PATH, SeedRepository
from .idea_vault import IdeaVault
from .knowledge import KnowledgeBase
from .knowledge_graph import KnowledgeGraph
from .learning import LearningCenter, lessons_for_prompt
from .memory import MemoryInbox
from .text_posts import TextPostRepository
from .trend_radar import DEFAULT_TREND_CACHE_PATH
from .writing_dna import WritingDNARepository


DEFAULT_EDITORIAL_STRATEGY_PATH = DEFAULT_CONTENT_PLAN_PATH.parents[1] / "seeds" / "editorial_strategy.json"


class AIContextEngine:
    """Builds the shared AI context used by generation, radar, drafting and recommendations."""

    def __init__(
        self,
        author_profile_repository: AuthorProfileRepository | None = None,
        writing_dna_repository: WritingDNARepository | None = None,
        author_brain_repository: AuthorBrainRepository | None = None,
        knowledge_base: KnowledgeBase | None = None,
        memory_inbox: MemoryInbox | None = None,
        knowledge_graph: KnowledgeGraph | None = None,
        learning_center: LearningCenter | None = None,
        idea_vault: IdeaVault | None = None,
        text_post_repository: TextPostRepository | None = None,
        seed_repository: SeedRepository | None = None,
        editorial_strategy_path: Path = DEFAULT_EDITORIAL_STRATEGY_PATH,
        trend_cache_path: Path = DEFAULT_TREND_CACHE_PATH,
    ) -> None:
        self.author_profile_repository = author_profile_repository or AuthorProfileRepository()
        self.writing_dna_repository = writing_dna_repository or WritingDNARepository()
        self.author_brain_repository = author_brain_repository or AuthorBrainRepository()
        self.knowledge_base = knowledge_base or KnowledgeBase()
        self.memory_inbox = memory_inbox or MemoryInbox()
        self.knowledge_graph = knowledge_graph or KnowledgeGraph()
        self.learning_center = learning_center or LearningCenter()
        self.idea_vault = idea_vault or IdeaVault()
        self.text_post_repository = text_post_repository or TextPostRepository()
        self.seed_repository = seed_repository or SeedRepository()
        self.editorial_strategy_path = editorial_strategy_path
        self.trend_cache_path = trend_cache_path

    def build(self, selected: dict[str, object] | None = None, include_local_sources: bool = False) -> dict[str, Any]:
        selected = selected or {}
        documents = self.knowledge_base.list_documents()
        cases = self.knowledge_base.list_cases()
        ideas = self.idea_vault.list_ideas()
        content_plan = self._load_content_plan()
        self.text_post_repository.sync_from_content_plan(content_plan)
        writing_dna = self.writing_dna_repository.load_raw()
        author_profile = self.author_profile_repository.load_raw()
        lessons = self.learning_center.list_lessons("accepted")
        author_brain = AuthorBrain(
            author_profile=author_profile,
            writing_dna=writing_dna,
            documents=documents,
            cases=cases,
            ideas=ideas,
            lessons=lessons,
        ).build(selected)
        if isinstance(author_brain.get("profile"), dict):
            author_brain["profile"] = self.author_brain_repository.apply_manual_overrides(author_brain["profile"])
        target_publication = selected or _target_publication(content_plan)
        query = " ".join(str(target_publication.get(key, "")) for key in ("platform", "rubric", "pillar", "format", "topic", "summary", "goal"))
        context: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "author_brain": author_brain,
            "writing_dna": writing_dna,
            "editorial_strategy": self._load_editorial_strategy(),
            "content_plan": content_plan,
            "trend_radar": self._load_trend_radar(),
            "knowledge_documents": self._knowledge_documents(documents),
            "semantic_chunks": self._semantic_chunks(documents),
            "memory": [item.__dict__ for item in self.memory_inbox.list_items()[:12]],
            "knowledge_graph_links": self.knowledge_graph.related_to(query),
            "lessons": lessons_for_prompt(lessons),
            "idea_vault": [idea.__dict__ for idea in ideas[:20]],
            "recent_drafts": self._recent_publications(content_plan, statuses={"in_progress", "drafted"}),
            "recent_topics": self._recent_publications(content_plan, statuses={"planned", "published", "in_progress", "drafted"}),
            "published_posts": self.text_post_repository.published_for_ai(),
            "month_focus": str(content_plan.get("month_focus", "")),
            "week_focus": str(content_plan.get("focus", "")),
            "selected": {
                "platform": str(target_publication.get("platform", selected.get("platform", ""))),
                "rubric": str(target_publication.get("rubric", target_publication.get("pillar", selected.get("rubric", "")))),
                "format": str(target_publication.get("format", selected.get("format", ""))),
                "topic": str(target_publication.get("topic", selected.get("topic", ""))),
            },
        }
        if include_local_sources:
            context["local_sources"] = self.seed_repository.load()
        return context

    def _load_content_plan(self) -> dict[str, Any]:
        try:
            return self.seed_repository.load_content_plan()
        except FileNotFoundError:
            return {}

    def _load_editorial_strategy(self) -> dict[str, object]:
        if not self.editorial_strategy_path.exists():
            return {"status": "default", "weekly_template": []}
        try:
            raw = json.loads(self.editorial_strategy_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"status": "error", "weekly_template": []}
        return raw if isinstance(raw, dict) else {"status": "error", "weekly_template": []}

    def _load_trend_radar(self) -> dict[str, object]:
        if not self.trend_cache_path.exists():
            return {"topics": [], "sources": [], "source_status": "not_started"}
        try:
            raw = json.loads(self.trend_cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"topics": [], "sources": [], "source_status": "error"}
        return raw if isinstance(raw, dict) else {"topics": [], "sources": [], "source_status": "error"}

    def _knowledge_documents(self, documents: list[object]) -> list[dict[str, object]]:
        return [
            {
                "title": getattr(document, "title", ""),
                "excerpt": getattr(document, "excerpt", ""),
                "metadata": getattr(document, "document_metadata", {}) or {},
                "analysis": getattr(document, "analysis", {}) or {},
            }
            for document in documents[:12]
        ]

    def _semantic_chunks(self, documents: list[object]) -> list[dict[str, object]]:
        chunks: list[dict[str, object]] = []
        for document in documents[:10]:
            title = str(getattr(document, "title", ""))
            for index, chunk in enumerate(getattr(document, "semantic_chunks", ())[:4]):
                chunks.append({"document": title, "index": index, "content": str(chunk)[:900]})
        return chunks[:24]

    def _recent_publications(self, content_plan: dict[str, Any], statuses: set[str]) -> list[dict[str, object]]:
        publications = content_plan.get("planned_publications", [])
        if not isinstance(publications, list):
            return []
        return [
            {
                "date": str(item.get("date", "")),
                "platform": str(item.get("platform", "")),
                "rubric": str(item.get("rubric", item.get("pillar", ""))),
                "format": str(item.get("format", "")),
                "topic": str(item.get("topic", "")),
                "summary": str(item.get("summary", "")),
                "status": str(item.get("status", "")),
            }
            for item in publications
            if isinstance(item, dict) and str(item.get("status", "")) in statuses
        ][:12]


def _target_publication(content_plan: dict[str, Any]) -> dict[str, object]:
    """Pick the publication the AI should work on: today's entry, else the first
    non-terminal one, else the first entry. Single source of truth shared by the
    context engine and the pipeline."""
    publications = content_plan.get("planned_publications", [])
    if not isinstance(publications, list) or not publications:
        return {}
    today = datetime.now().date().isoformat()
    for publication in publications:
        if isinstance(publication, dict) and str(publication.get("date", "")) == today:
            return publication
    for publication in publications:
        if isinstance(publication, dict) and str(publication.get("status", "")) not in {"published", "skipped", "archived"}:
            return publication
    return publications[0] if isinstance(publications[0], dict) else {}
