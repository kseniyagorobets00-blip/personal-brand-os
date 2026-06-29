from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .author_brain import AuthorBrain
from .author_profile import AuthorProfile, AuthorProfileRepository
from .knowledge import KnowledgeBase, KnowledgeSearchResult
from .writing_dna import WritingDNARepository


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_PATH = ROOT / "data" / "seeds" / "daily_brief_sources.json"
DEFAULT_CONTENT_PLAN_PATH = ROOT / "data" / "seeds" / "content_plan.json"
PLAN_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d")
RU_WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def today_moscow() -> date:
    return datetime.now(MOSCOW_TZ).date()


def parse_plan_date(value: str) -> date | None:
    raw = value.strip()
    for fmt in PLAN_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def weekday_name_for_date(value: str) -> str:
    parsed = parse_plan_date(value)
    return RU_WEEKDAYS[parsed.weekday()] if parsed else ""


@dataclass(frozen=True)
class BriefItem:
    title: str
    summary: str
    reason: str
    action: str
    score: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    platform: str
    title: str
    angle: str
    text: str
    status: str = "ready_for_review"


@dataclass(frozen=True)
class ApprovalItem:
    title: str
    decision: str
    recommendation: str
    risk: str


@dataclass(frozen=True)
class RelatedKnowledge:
    document_id: str
    title: str
    reason: str
    excerpt: str
    score: int


@dataclass(frozen=True)
class PlannedPublication:
    date: str
    day: str
    platform: str
    topic: str
    pillar: str
    status: str
    note: str
    goal: str = ""
    summary: str = ""


@dataclass(frozen=True)
class ContentPlan:
    week: str
    focus: str
    month_focus: str
    content_pillars: tuple[str, ...]
    platform_targets: tuple[str, ...]
    planned_publications: tuple[PlannedPublication, ...]
    today_recommendation: str


@dataclass(frozen=True)
class DailyBrief:
    brief_date: date
    executive_summary: str
    market_signals: tuple[BriefItem, ...]
    topics: tuple[BriefItem, ...]
    recommendations: tuple[BriefItem, ...]
    ideas: tuple[BriefItem, ...]
    drafts: tuple[Draft, ...]
    approvals: tuple[ApprovalItem, ...]
    content_plan: ContentPlan
    related_knowledge: tuple[RelatedKnowledge, ...]
    memory_notes: tuple[str, ...] = field(default_factory=tuple)
    source_count: int = 0


class SeedRepository:
    def __init__(
        self,
        source_path: Path = DEFAULT_SEED_PATH,
        content_plan_path: Path = DEFAULT_CONTENT_PLAN_PATH,
    ) -> None:
        self.source_path = source_path
        self.content_plan_path = content_plan_path

    def load(self) -> dict[str, Any]:
        if not self.source_path.exists():
            raise FileNotFoundError(f"Daily Brief seed file not found: {self.source_path}")
        return json.loads(self.source_path.read_text(encoding="utf-8"))

    def load_content_plan(self) -> dict[str, Any]:
        if not self.content_plan_path.exists():
            raise FileNotFoundError(f"Content plan seed file not found: {self.content_plan_path}")
        return json.loads(self.content_plan_path.read_text(encoding="utf-8"))


class DailyBriefService:
    """Builds Daily Brief from local seed sources and idea records."""

    def __init__(
        self,
        repository: SeedRepository | None = None,
        author_profile_repository: AuthorProfileRepository | None = None,
        knowledge_base: KnowledgeBase | None = None,
        writing_dna_repository: WritingDNARepository | None = None,
    ) -> None:
        self.repository = repository or SeedRepository()
        self.author_profile_repository = author_profile_repository or AuthorProfileRepository()
        self.knowledge_base = knowledge_base or KnowledgeBase()
        self.writing_dna_repository = writing_dna_repository or WritingDNARepository()

    def build_today(self) -> DailyBrief:
        seed = self.repository.load()
        content_plan = self._content_plan_from_seed(self.repository.load_content_plan())
        author_profile = self.author_profile_repository.load()
        self.knowledge_base.ensure_seed_documents()
        author_brain = AuthorBrain(
            author_profile=self.author_profile_repository.load_raw(),
            writing_dna=self.writing_dna_repository.load_raw(),
            documents=self.knowledge_base.list_documents(),
            cases=self.knowledge_base.list_cases(),
            ideas=[],
        )
        sources = tuple(seed.get("sources", ()))
        seed_ideas = tuple(seed.get("ideas", ()))
        selected_publications = self._selected_publications(content_plan)
        topics = self._topics_from_content_plan(selected_publications, content_plan)
        ideas = self._ideas_from_seed(seed_ideas, topics, content_plan)
        related_knowledge = self._related_knowledge(topics, ideas)

        return DailyBrief(
            brief_date=today_moscow(),
            executive_summary=self._executive_summary(topics, ideas, len(sources), content_plan),
            market_signals=self._signals_from_sources(sources),
            topics=topics,
            recommendations=self._recommendations(topics, ideas, content_plan),
            ideas=ideas,
            drafts=self._drafts(topics, ideas, author_profile, author_brain),
            approvals=self._approvals(seed, topics),
            content_plan=content_plan,
            related_knowledge=related_knowledge,
            memory_notes=tuple(seed.get("memory_notes", ())),
            source_count=len(sources),
        )

    def _signals_from_sources(self, sources: tuple[dict[str, Any], ...]) -> tuple[BriefItem, ...]:
        ranked = sorted(sources, key=lambda item: int(item.get("strength", 0)), reverse=True)
        return tuple(
            BriefItem(
                title=str(item["title"]),
                summary=str(item["signal"]),
                reason=str(item["why_it_matters"]),
                action=str(item["suggested_action"]),
                score=self._score(item),
                tags=tuple(item.get("tags", ())),
            )
            for item in ranked[:4]
        )

    def _topics_from_sources(
        self,
        sources: tuple[dict[str, Any], ...],
        content_plan: ContentPlan,
    ) -> tuple[BriefItem, ...]:
        topic_map: dict[str, dict[str, Any]] = {}
        for source in sources:
            for topic in source.get("topics", ()):
                current = topic_map.setdefault(
                    topic,
                    {
                        "score": 0,
                        "sources": 0,
                        "tags": set(),
                        "best_reason": source.get("why_it_matters", ""),
                    },
                )
                plan_bonus = self._topic_plan_bonus(topic, content_plan)
                current["score"] += self._score(source) + plan_bonus
                current["sources"] += 1
                current["tags"].update(source.get("tags", ()))
                current["tags"].update(self._topic_plan_tags(topic, content_plan))
                if self._score(source) > current.get("best_score", 0):
                    current["best_score"] = self._score(source)
                    current["best_reason"] = source.get("why_it_matters", "")

        ranked_topics = sorted(
            topic_map.items(),
            key=lambda item: (item[1]["score"], item[1]["sources"]),
            reverse=True,
        )
        return tuple(
            BriefItem(
                title=topic,
                summary=self._topic_summary(topic),
                reason=self._topic_reason(topic, str(meta["best_reason"]), content_plan),
                action=self._topic_action(topic, content_plan),
                score=min(98, round(meta["score"] / max(1, meta["sources"]))),
                tags=tuple(sorted(meta["tags"]))[:4],
            )
            for topic, meta in ranked_topics[:4]
        )

    def _selected_publications(self, content_plan: ContentPlan) -> tuple[PlannedPublication, ...]:
        today = today_moscow()
        todays = tuple(
            publication
            for publication in content_plan.planned_publications
            if self._publication_date(publication) == today
        )
        return tuple(sorted(todays, key=self._publication_priority, reverse=True))

    def _topics_from_content_plan(
        self,
        publications: tuple[PlannedPublication, ...],
        content_plan: ContentPlan,
    ) -> tuple[BriefItem, ...]:
        return tuple(
            BriefItem(
                title=publication.topic,
                summary=publication.summary or publication.note or self._topic_summary(publication.topic),
                reason=self._publication_reason(publication, content_plan),
                action=f"Готовить для {publication.platform}: {publication.goal or publication.note or content_plan.today_recommendation}",
                score=self._publication_priority(publication),
                tags=tuple(
                    item
                    for item in (publication.platform, publication.status, publication.pillar, "из контент-плана")
                    if item
                ),
            )
            for publication in publications
        )

    def _ideas_from_seed(
        self,
        seed_ideas: tuple[dict[str, Any], ...],
        topics: tuple[BriefItem, ...],
        content_plan: ContentPlan,
    ) -> tuple[BriefItem, ...]:
        topic_names = {topic.title for topic in topics}
        ranked = sorted(
            seed_ideas,
            key=lambda item: int(item.get("potential", 0)) + self._idea_plan_bonus(item, content_plan),
            reverse=True,
        )
        ideas: list[BriefItem] = []
        for item in ranked:
            tags = tuple(item.get("tags", ())) + tuple(self._idea_plan_tags(item, content_plan))
            topic_bonus = 5 if topic_names.intersection(item.get("related_topics", ())) else 0
            plan_bonus = self._idea_plan_bonus(item, content_plan)
            ideas.append(
                BriefItem(
                    title=str(item["title"]),
                    summary=str(item["raw_idea"]),
                    reason=self._idea_reason(item, content_plan),
                    action=self._idea_action(item, content_plan),
                    score=min(99, int(item.get("potential", 70)) + topic_bonus + plan_bonus),
                    tags=tags,
                )
            )
        return tuple(ideas[:4])

    def _recommendations(
        self,
        topics: tuple[BriefItem, ...],
        ideas: tuple[BriefItem, ...],
        content_plan: ContentPlan,
    ) -> tuple[BriefItem, ...]:
        primary_topic = topics[0] if topics else None
        primary_idea = ideas[0] if ideas else None
        recommendations = [
            BriefItem(
                title="Выбрать один главный фокус дня",
                summary=(
                    f"Самая сильная линия сегодня: {primary_topic.title}."
                    if primary_topic
                    else "Сегодня недостаточно сигналов для сильной публичной позиции."
                ),
                reason=f"Это связано с недельным фокусом: {content_plan.focus}",
                action=content_plan.today_recommendation,
                score=94,
                tags=("Strategy", "Focus"),
            )
        ]
        if primary_idea:
            recommendations.append(
                BriefItem(
                    title="Развить идею в авторскую позицию",
                    summary=primary_idea.title,
                    reason="Идея связана с текущими сигналами и недельным планом, поэтому подходит именно сегодня.",
                    action="Доработать угол и подготовить LinkedIn-версию.",
                    score=primary_idea.score,
                    tags=("Идеи",),
                )
            )
        return tuple(recommendations)

    def _drafts(
        self,
        topics: tuple[BriefItem, ...],
        ideas: tuple[BriefItem, ...],
        author_profile: AuthorProfile,
        author_brain: AuthorBrain,
    ) -> tuple[Draft, ...]:
        if not topics:
            return ()
        drafts = []
        for index, topic in enumerate(topics):
            platform = self._platform_from_topic(topic)
            idea = ideas[index] if index < len(ideas) else (ideas[0] if ideas else None)
            brain = author_brain.build({"platform": platform, "topic": topic.title, "summary": topic.summary, "goal": topic.action})
            drafts.append(
                Draft(
                    platform=platform,
                    title=topic.title,
                    angle=self._platform_angle(platform, author_profile),
                    text=self._draft_text(platform, topic, idea, author_profile, brain),
                )
            )
        return tuple(drafts)

    def _draft_text(
        self,
        platform: str,
        topic: BriefItem,
        idea: BriefItem | None,
        author_profile: AuthorProfile,
        author_brain: dict[str, object],
    ) -> str:
        if platform == "Telegram":
            return self._telegram_draft(topic, idea, author_profile, author_brain)
        if platform == "LinkedIn":
            return self._linkedin_draft(topic, idea, author_profile, author_brain)
        if platform == "VC":
            return self._vc_draft(topic, idea, author_profile, author_brain)
        if platform == "Сетка":
            return self._setka_draft(topic, idea, author_profile, author_brain)
        return (
            f"{topic.title}\n\n"
            f"{topic.summary}\n\n"
            "Если смотреть на эту тему как на управленческую ситуацию, становится видно: проблема редко живет на поверхности. "
            "Она проявляется в коммуникации, в сервисе, в скорости реакции, но начинается раньше — в том, как устроена операционная система.\n\n"
            "Когда нет ясной ответственности, стандартов и точек контроля, команда каждый раз заново договаривается о том, что должно произойти. "
            "Клиент в этот момент чувствует не отдельную ошибку, а отсутствие предсказуемости.\n\n"
            "Именно поэтому сильный экспертный бренд в Operations и Customer Experience строится не на общих словах про сервис, а на способности показывать причину: где система теряет управляемость и что нужно изменить, чтобы результат стал повторяемым."
        )

    def _legacy_drafts(
        self,
        topics: tuple[BriefItem, ...],
        ideas: tuple[BriefItem, ...],
        author_profile: AuthorProfile,
    ) -> tuple[Draft, ...]:
        primary = topics[0]
        idea = ideas[0] if ideas else None
        empty_brain: dict[str, object] = {}
        return (
            Draft(
                platform="LinkedIn",
                title=primary.title,
                angle=self._platform_angle("LinkedIn", author_profile),
                text=self._linkedin_draft(primary, idea, author_profile, empty_brain),
            ),
            Draft(
                platform="Telegram",
                title=idea.title if idea else primary.title,
                angle=self._platform_angle("Telegram", author_profile),
                text=self._telegram_draft(primary, idea, author_profile, empty_brain),
            ),
        )

    def _approvals(self, seed: dict[str, Any], topics: tuple[BriefItem, ...]) -> tuple[ApprovalItem, ...]:
        approvals = [
            ApprovalItem(
                title="Подтвердить главный фокус Daily Brief",
                decision=f"Использовать тему «{topics[0].title}» как главный фокус дня." if topics else "Пропустить публикационную реакцию сегодня.",
                recommendation="Рекомендую подтвердить: тема хорошо связывает рынок с вашей экспертной территорией.",
                risk="Если выбрать слишком широкий угол, текст может стать похож на общий комментарий о трендах.",
            )
        ]
        for item in seed.get("approval_items", ()):
            approvals.append(
                ApprovalItem(
                    title=str(item["title"]),
                    decision=str(item["decision"]),
                    recommendation=str(item["recommendation"]),
                    risk=str(item["risk"]),
                )
            )
        return tuple(approvals[:3])

    def _related_knowledge(
        self,
        topics: tuple[BriefItem, ...],
        ideas: tuple[BriefItem, ...],
    ) -> tuple[RelatedKnowledge, ...]:
        queries = [item.title for item in topics[:3]] + [item.title for item in ideas[:2]]
        results = self.knowledge_base.recommend_for_topics(queries, limit=4)
        return tuple(self._related_knowledge_from_result(result) for result in results)

    def _related_knowledge_from_result(self, result: KnowledgeSearchResult) -> RelatedKnowledge:
        return RelatedKnowledge(
            document_id=result.document.id,
            title=result.document.title,
            reason=result.reason,
            excerpt=result.document.excerpt,
            score=result.score,
        )

    def _executive_summary(
        self,
        topics: tuple[BriefItem, ...],
        ideas: tuple[BriefItem, ...],
        source_count: int,
        content_plan: ContentPlan,
    ) -> str:
        topic = topics[0].title if topics else "безопасная пауза без публичной реакции"
        idea = ideas[0].title if ideas else "накопление наблюдений"
        return (
            f"Brief пересобран из {source_count} локальных источников. "
            f"План недели: {content_plan.focus}. "
            f"Главный фокус дня: {topic}. "
            f"Самая перспективная идея: {idea}. "
            "Рекомендация: выпускать не обзор, а управленческую позицию с привязкой к Operations, CX и сервисной дисциплине."
        )

    def _content_plan_from_seed(self, seed: dict[str, Any]) -> ContentPlan:
        return ContentPlan(
            week=str(seed["week"]),
            focus=str(seed["focus"]),
            month_focus=str(seed.get("month_focus", "")),
            content_pillars=tuple(seed.get("content_pillars", ())),
            platform_targets=tuple(seed.get("platform_targets", ())),
            today_recommendation=str(seed.get("today_recommendation", "")),
            planned_publications=tuple(
                PlannedPublication(
                    date=str(item.get("date", "")),
                    day=weekday_name_for_date(str(item.get("date", ""))) or str(item.get("day", "")),
                    platform=str(item.get("platform", "")),
                    topic=str(item.get("topic", "")),
                    pillar=str(item.get("pillar", "")),
                    status=str(item.get("status", "planned")),
                    note=str(item.get("note", "")),
                    goal=str(item.get("goal", "")),
                    summary=str(item.get("summary", "")),
                )
                for item in seed.get("planned_publications", ())
            ),
        )

    def _publication_date(self, publication: PlannedPublication) -> date | None:
        return parse_plan_date(publication.date)

    def _publication_priority(self, publication: PlannedPublication) -> int:
        status_score = {
            "drafted": 98,
            "in_progress": 94,
            "suggested": 90,
            "planned": 86,
            "approved": 84,
            "needs_ai_plan": 70,
        }.get(publication.status, 76)
        platform_score = {
            "LinkedIn": 6,
            "VC": 5,
            "Telegram": 4,
            "Сетка": 3,
        }.get(publication.platform, 0)
        completeness = sum(1 for value in (publication.topic, publication.goal, publication.summary) if value.strip())
        return min(99, status_score + platform_score + completeness)

    def _publication_reason(self, publication: PlannedPublication, content_plan: ContentPlan) -> str:
        date_part = f" на {publication.date}" if publication.date else ""
        goal_part = f" Цель: {publication.goal}" if publication.goal else ""
        return (
            f"Публикация выбрана из контент-плана{date_part}: {publication.platform}, "
            f"статус {publication.status}. Она поддерживает фокус недели: {content_plan.focus}."
            f"{goal_part}"
        )

    def _platform_from_topic(self, topic: BriefItem) -> str:
        for tag in topic.tags:
            if tag in {"LinkedIn", "Telegram", "VC", "Сетка"}:
                return tag
        return "LinkedIn"

    def _topic_plan_bonus(self, topic: str, content_plan: ContentPlan) -> int:
        publication = self._planned_publication_for_topic(topic, content_plan)
        if publication and publication.status in {"planned", "suggested", "drafted", "in_progress", "idea"}:
            return 12
        if self._topic_matches_pillar(topic, content_plan):
            return 6
        return -8

    def _idea_plan_bonus(self, idea: dict[str, Any], content_plan: ContentPlan) -> int:
        related_topics = idea.get("related_topics", ())
        if any(self._planned_publication_for_topic(topic, content_plan) for topic in related_topics):
            return 8
        if any(self._topic_matches_pillar(topic, content_plan) for topic in related_topics):
            return 4
        return -4

    def _topic_plan_tags(self, topic: str, content_plan: ContentPlan) -> tuple[str, ...]:
        publication = self._planned_publication_for_topic(topic, content_plan)
        if publication:
            return (publication.platform, publication.status)
        if self._topic_matches_pillar(topic, content_plan):
            return ("plan-adjacent",)
        return ("skipped",)

    def _idea_plan_tags(self, idea: dict[str, Any], content_plan: ContentPlan) -> tuple[str, ...]:
        for topic in idea.get("related_topics", ()):
            publication = self._planned_publication_for_topic(topic, content_plan)
            if publication:
                return (publication.platform, publication.status)
        return ("plan-adjacent",) if self._idea_matches_pillar(idea, content_plan) else ("skipped",)

    def _topic_reason(self, topic: str, base_reason: str, content_plan: ContentPlan) -> str:
        publication = self._planned_publication_for_topic(topic, content_plan)
        if publication:
            return (
                f"{base_reason} Тема предложена сегодня, потому что стоит в плане недели "
                f"для {publication.platform} со статусом {publication.status}."
            )
        if self._topic_matches_pillar(topic, content_plan):
            return f"{base_reason} Тема не стоит в расписании напрямую, но поддерживает один из pillars недели."
        return f"{base_reason} Тема не входит в текущий план, поэтому ее лучше отложить, если не нужен быстрый рыночный комментарий."

    def _idea_reason(self, idea: dict[str, Any], content_plan: ContentPlan) -> str:
        base = str(idea["why_now"])
        for topic in idea.get("related_topics", ()):
            publication = self._planned_publication_for_topic(topic, content_plan)
            if publication:
                return f"{base} Идея связана с плановой публикацией для {publication.platform}: «{publication.topic}»."
        if self._idea_matches_pillar(idea, content_plan):
            return f"{base} Идея поддерживает недельный focus и может усилить план."
        return f"{base} Идея вне текущего плана, поэтому агент предлагает держать ее в резерве."

    def _idea_action(self, idea: dict[str, Any], content_plan: ContentPlan) -> str:
        for topic in idea.get("related_topics", ()):
            publication = self._planned_publication_for_topic(topic, content_plan)
            if publication:
                return f"{idea['next_step']} Приоритет: {publication.platform}, статус {publication.status}."
        return f"{idea['next_step']} Если не связано с планом недели, оставить в хранилище идей."

    def _topic_action(self, topic: str, content_plan: ContentPlan) -> str:
        publication = self._planned_publication_for_topic(topic, content_plan)
        if publication:
            return f"Готовить для {publication.platform}: {publication.note}"
        if "AI" in topic:
            return "Подготовить пост с сильным тезисом, если нужен рыночный комментарий; иначе отложить."
        if "SOP" in topic:
            return "Развить в короткую серию о сервисной дисциплине."
        return "Использовать как основу для главного черновика дня."

    def _planned_publication_for_topic(self, topic: str, content_plan: ContentPlan) -> PlannedPublication | None:
        for publication in content_plan.planned_publications:
            if publication.topic == topic:
                return publication
        return None

    def _topic_matches_pillar(self, topic: str, content_plan: ContentPlan) -> bool:
        lowered = topic.lower()
        return any(pillar.lower() in lowered for pillar in content_plan.content_pillars)

    def _idea_matches_pillar(self, idea: dict[str, Any], content_plan: ContentPlan) -> bool:
        text = " ".join([str(idea.get("title", "")), str(idea.get("raw_idea", "")), *idea.get("related_topics", ())]).lower()
        return any(pillar.lower() in text for pillar in content_plan.content_pillars)

    def _score(self, item: dict[str, Any]) -> int:
        strength = int(item.get("strength", 70))
        brand_fit = int(item.get("brand_fit", 70))
        freshness = int(item.get("freshness", 70))
        return round((strength * 0.4) + (brand_fit * 0.4) + (freshness * 0.2))

    def _topic_summary(self, topic: str) -> str:
        summaries = {
            "Customer Experience как следствие операционной дисциплины": "CX стоит раскрывать как результат процессов, ответственности и управленческой зрелости.",
            "AI как зеркало операционной зрелости": "AI показывает, насколько компания готова описывать, повторять и улучшать свои процессы.",
            "SOP как язык заботы о клиенте": "Стандарты можно показать не как бюрократию, а как способ сделать сервис надежным.",
            "Service Design без внедрения": "Карты и воркшопы не создают ценность, если не меняют поведение команды.",
        }
        return summaries.get(topic, "Тема связана с текущими сигналами и подходит для экспертной позиции автора.")

    def _platform_angle(self, platform: str, author_profile: AuthorProfile) -> str:
        rule = author_profile.rule_for_platform(platform)
        if not rule:
            return "Черновик учитывает общий Author Profile."
        return f"Черновик учитывает правило платформы: {rule.rule}"

    def _style_note(self, author_profile: AuthorProfile, platform: str) -> str:
        favorite = ", ".join(author_profile.vocabulary.favorite_words[:3])
        rule = author_profile.rule_for_platform(platform)
        platform_rule = f" Правило платформы: {rule.rule}" if rule else ""
        return (
            f"Стиль: {author_profile.structure.post_structure}; "
            f"вступление — {author_profile.structure.intro_length}; "
            f"тон — {author_profile.tone.directness}; "
            f"опорная лексика: {favorite}."
            f"{platform_rule}"
        )

    def _linkedin_draft(self, topic: BriefItem, idea: BriefItem | None, author_profile: AuthorProfile, author_brain: dict[str, object]) -> str:
        idea_line = f"\n\nЭта мысль особенно хорошо раскрывается через идею: {idea.title}." if idea else ""
        case_line = self._case_line(author_brain)
        return (
            "На проектах я все чаще ловлю себя на одном и том же наблюдении: клиентский опыт начинает ломаться раньше, чем клиент успевает пожаловаться.\n\n"
            "Снаружи это выглядит как обычная сервисная проблема: кто-то не так ответил, команда не успела, клиент не почувствовал внимания. "
            "Но внутри чаще лежит другое — неясная ответственность, слабая передача между ролями и процесс без владельца.\n\n"
            f"{topic.summary}\n\n"
            "Сильный сервис начинается раньше точки контакта. Он начинается там, где компания проектирует, кто принимает решения, как передается контекст и что считается нормой исполнения.\n\n"
            "Например, в сервисной цепочке может быть идеально прописан один этап, но клиент все равно почувствует разрыв, если следующий участник процесса не понимает, что именно уже обещано, какие ожидания сформированы и где начинается его зона ответственности. "
            "На бумаге задача передана. В реальности клиентский опыт уже просел.\n\n"
            f"{case_line}"
            "Для меня это один из самых важных управленческих симптомов: CX ломается не только там, где клиент что-то видит, а там, где внутри компании исчезает владение результатом. "
            "И если эту точку не увидеть, команда будет снова и снова улучшать коммуникацию, когда на самом деле нужно чинить операционную архитектуру."
            f"{idea_line}\n\n"
            "Поэтому вопрос не в том, как сделать сервис более «дружелюбным». Вопрос в том, какая система делает хороший сервис неизбежным: роли, SOP, точки контроля, обратная связь, данные и управленческая привычка разбирать сбои как системные, а не персональные.\n\n"
            "Если мы хотим сильный CX, нужно смотреть не только на улыбку в финальной точке контакта. Нужно смотреть на операционную систему, которая эту улыбку делает возможной."
        )

    def _telegram_draft(self, topic: BriefItem, idea: BriefItem | None, author_profile: AuthorProfile, author_brain: dict[str, object]) -> str:
        title = idea.title if idea else topic.title
        case_line = self._case_line(author_brain)
        return (
            "Был бы у меня сегодня один короткий тезис после рабочих разговоров, я бы сформулировала его так: AI очень честно показывает, насколько зрелая у компании операционная система.\n\n"
            f"{title}\n\n"
            f"{topic.summary}\n\n"
            "Если внутри процесса нет ясности, любой новый инструмент просто ускоряет неопределенность. "
            "Если ясность есть, даже простой инструмент начинает работать сильнее.\n\n"
            f"{case_line}"
            "Поэтому главный вопрос не «что внедрить?». Главный вопрос: что в операционной системе уже готово к усилению?\n\n"
            "Мне кажется, это особенно важно в сервисных бизнесах. Там очень легко принять симптом за причину: клиент недоволен, значит, нужно лучше обучить сотрудников, переписать скрипт, добавить автоматизацию или внедрить AI. Иногда это правда помогает. Но только если внутри уже есть понятная логика процесса.\n\n"
            "Если ее нет, новый инструмент просто делает хаос быстрее. Быстрее отправляются сообщения. Быстрее принимаются решения без контекста. Быстрее масштабируются ошибки в данных. И команда получает не зрелость, а ускоренную неопределенность.\n\n"
            "Я бы начинала не с вопроса «какой AI нам нужен?», а с другого: какие процессы у нас уже достаточно зрелые, чтобы их можно было усиливать?\n\n"
            "Потому что AI хорошо усиливает то, что уже работает. Но он очень честно показывает и то, что держалось только на ручном героизме людей."
        )

    def _vc_draft(self, topic: BriefItem, idea: BriefItem | None, author_profile: AuthorProfile, author_brain: dict[str, object]) -> str:
        idea_line = f"\n\nДополнительный угол для раскрытия: {idea.title}." if idea else ""
        case_line = self._case_line(author_brain)
        return (
            f"{topic.title}\n\n"
            "Недавно я снова подумала о том, как легко перепутать карту клиентского пути с реальным изменением сервиса.\n\n"
            "На встречах это часто выглядит убедительно: есть схема, этапы, эмоции клиента, точки контакта, список инициатив. "
            "Но потом команда возвращается в операционную реальность, и становится видно, что сама карта ничего не изменила. "
            "Клиентский опыт продолжает зависеть от того, кто сегодня на смене, кто вспомнил передать контекст и кто взял ответственность за следующий шаг.\n\n"
            "Проблема в том, что Customer Experience редко ломается в одной точке. Клиент видит задержку, противоречивый ответ или неуверенность сотрудника, но причина часто находится глубже: в неясных ролях, слабой передаче ответственности, отсутствии владельца процесса или стандарта, который должен был защитить клиента от случайности.\n\n"
            "Где именно возникает разрыв\n\n"
            f"{topic.summary}\n\n"
            f"{case_line}"
            "Если journey map остается только визуализацией, она не меняет сервис. Она показывает желаемый путь, но не отвечает на вопросы, от которых зависит исполнение: кто владеет переходом между этапами, что считается качественным результатом, где находится точка контроля, какие данные нужны команде, чтобы принять решение, и как разбирается сбой.\n\n"
            "В этом месте Service Design должен соединяться с Operations. Не как красивая схема, а как проектирование поведения системы: какие решения принимает команда, как передается контекст, где фиксируются ожидания клиента и кто отвечает за результат.\n\n"
            "Почему AI не решает это автоматически\n\n"
            "AI может ускорить коммуникацию, подсказать следующий шаг, помочь с персонализацией и снять часть рутины. Но он не заменяет операционную зрелость. Если процесс не описан, данные неточны, ответственность размыта, а исключения обрабатываются вручную, AI начинает масштабировать эту неопределенность.\n\n"
            "Поэтому зрелый подход начинается не с вопроса о технологии. Он начинается с управленческого диагноза: что именно в сервисной системе уже работает повторяемо, а где результат держится только на опыте отдельных людей.\n\n"
            f"{idea_line}\n\n"
            "Практический вывод простой: сильный Customer Experience появляется там, где забота превращена в управляемую систему. В ней есть стандарты, но они не ради бюрократии. Есть контроль, но не ради наказания. Есть процессы, но не ради процесса. Все это нужно для одного: чтобы клиент получал предсказуемый результат не случайно, а потому что система спроектирована именно так."
        )

    def _setka_draft(self, topic: BriefItem, idea: BriefItem | None, author_profile: AuthorProfile, author_brain: dict[str, object]) -> str:
        case_line = self._case_line(author_brain)
        return (
            "Есть одна мысль, которая постоянно возвращается, когда обсуждаешь сервисные стандарты.\n\n"
            f"{topic.title}\n\n"
            "Заботу о клиенте часто представляют как что-то очень мягкое: эмпатия, внимание, тон общения, умение почувствовать ситуацию.\n\n"
            "Все это важно. Но в реальном сервисе забота часто начинается гораздо раньше — в том, насколько предсказуемо работает система.\n\n"
            f"{topic.summary}\n\n"
            f"{case_line}"
            "Если сотрудник каждый раз сам решает, как действовать, сервис зависит от настроения, опыта и нагрузки конкретного человека. Сегодня клиенту повезло, завтра нет. Вроде бы команда старается, но результат получается нестабильным.\n\n"
            "SOP в этом смысле не про сухую инструкцию. Хороший стандарт защищает клиента от случайности. Он помогает команде быстро понять, что уже обещано, какой следующий шаг правильный, где нужно согласование, а где можно действовать самостоятельно.\n\n"
            "В hospitality это особенно заметно. Клиент может не знать, как устроен процесс внутри, но он мгновенно чувствует, когда система держит обещание: запрос не потерялся, контекст передан, ожидания понятны, исключение обработано спокойно.\n\n"
            "Для меня это и есть взрослая версия сервиса: не героизм отдельных сотрудников, а система, в которой забота становится повторяемой."
        )

    def _case_line(self, author_brain: dict[str, object]) -> str:
        cases = author_brain.get("case_candidates", [])
        if not isinstance(cases, list) or not cases:
            return ""
        case = cases[0]
        if not isinstance(case, dict):
            return ""
        title = str(case.get("title", "")).strip()
        excerpt = str(case.get("excerpt", case.get("what_happened", ""))).strip()
        if not title and not excerpt:
            return ""
        if excerpt:
            return f"Мне здесь полезно вспоминать {title}: {excerpt}\n\n"
        return f"Мне здесь полезно вспоминать кейс {title}, где тема проявлялась не как теория, а как рабочая ситуация.\n\n"
