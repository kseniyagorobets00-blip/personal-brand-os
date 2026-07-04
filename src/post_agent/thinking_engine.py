from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


THINKING_MODES = ("Observation", "Story", "Case", "Provocation", "Framework", "Reflection")


@dataclass(frozen=True)
class ThinkingResult:
    mode: str
    today_context: str
    similar_materials: list[dict[str, str]]
    relevant_case: dict[str, object] | None
    work_situation: str
    strongest_angle: str
    format_recommendation: str
    transparency: list[str]
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "today_context": self.today_context,
            "similar_materials": self.similar_materials,
            "relevant_case": self.relevant_case,
            "work_situation": self.work_situation,
            "strongest_angle": self.strongest_angle,
            "format_recommendation": self.format_recommendation,
            "transparency": self.transparency,
            "generated_at": self.generated_at,
        }


class ThinkingEngine:
    """Decides what is worth saying before Prompt Builder asks AI to write."""

    def think(self, context: dict[str, Any]) -> dict[str, object]:
        publication = context.get("target_publication", {})
        author_brain = context.get("author_brain", {})
        graph_links = context.get("knowledge_graph_links", [])
        if not isinstance(publication, dict):
            publication = {}
        if not isinstance(author_brain, dict):
            author_brain = {}

        platform = str(publication.get("platform", ""))
        topic = str(publication.get("topic", ""))
        summary = str(publication.get("summary", ""))
        goal = str(publication.get("goal", ""))
        cases = author_brain.get("case_candidates", [])
        materials = author_brain.get("knowledge_observations", [])
        mode = self._mode(platform, topic, summary, cases, author_brain)
        relevant_case = cases[0] if isinstance(cases, list) and cases else None
        strongest_angle = self._angle(topic, summary, goal, mode, bool(relevant_case))
        result = ThinkingResult(
            mode=mode,
            today_context=self._today_context(platform, topic, goal),
            similar_materials=self._similar_materials(materials, graph_links),
            relevant_case=relevant_case if isinstance(relevant_case, dict) else None,
            work_situation=self._work_situation(platform, topic, bool(relevant_case)),
            strongest_angle=strongest_angle,
            format_recommendation=self._format(platform, mode),
            transparency=self._transparency(mode, relevant_case, graph_links, author_brain),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        return result.to_dict()

    def _mode(self, platform: str, topic: str, summary: str, cases: object, author_brain: dict[str, object]) -> str:
        if isinstance(cases, list) and cases:
            return "Case"
        text = f"{topic} {summary}".lower()
        if any(word in text for word in ("ошибка", "миф", "не работает", "хаос")):
            return "Provocation"
        if platform == "VC":
            return "Framework"
        if any(word in text for word in ("ситуация", "разговор", "наблюдение")):
            return "Observation"
        suggested = str(author_brain.get("thinking_mode", ""))
        return suggested if suggested in THINKING_MODES else "Observation"

    def _today_context(self, platform: str, topic: str, goal: str) -> str:
        return f"Сегодня в плане стоит {platform}: {topic}. Цель: {goal}".strip()

    def _similar_materials(self, materials: object, graph_links: object) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if isinstance(materials, list):
            for item in materials[:4]:
                if isinstance(item, dict):
                    result.append({"title": str(item.get("title", "")), "reason": str(item.get("excerpt", ""))[:220]})
        if isinstance(graph_links, list):
            for item in graph_links[:4]:
                if isinstance(item, dict):
                    result.append(
                        {
                            "title": str(item.get("label", "")),
                            "reason": f"Связь в Knowledge Graph: {item.get('type', '')} / {item.get('relation', '')}",
                        }
                    )
        return result[:6]

    def _work_situation(self, platform: str, topic: str, has_case: bool) -> str:
        if has_case:
            return "Использовать реальный кейс из памяти без выдуманных деталей."
        if "hospitality" in topic.lower() or "service" in topic.lower():
            return "Типичная рабочая ситуация: руководитель гостиницы или служба сервиса видит разрыв между обещанием и исполнением."
        if platform == "Telegram":
            return "Типичная рабочая ситуация: короткое наблюдение после разговора с командой или клиентом."
        return "Типичная рабочая ситуация: руководитель проекта замечает повторяющийся сбой в передаче ответственности."

    def _angle(self, topic: str, summary: str, goal: str, mode: str, has_case: bool) -> str:
        if has_case:
            return f"Показать тему через реальный кейс, затем вывести управленческую причину: {topic}."
        if mode == "Provocation":
            return f"Сформулировать спорный тезис и доказать его через рабочую ситуацию: {topic}."
        if mode == "Framework":
            return f"Собрать практическую рамку без академического тона: {summary or goal or topic}."
        return f"Начать с наблюдения и довести его до практического вывода: {summary or topic}."

    def _format(self, platform: str, mode: str) -> str:
        if platform == "VC":
            return "Развернутая статья с подзаголовками, но без учебникового тона."
        if platform == "Telegram":
            return "Живое наблюдение с одним сильным выводом и вопросом."
        if platform == "Сетка":
            return "Короткий профессиональный пост: ситуация, вывод, вопрос."
        return f"Публикация в режиме {mode}: наблюдение, разбор, практический вывод."

    def _transparency(
        self,
        mode: str,
        relevant_case: object,
        graph_links: object,
        author_brain: dict[str, object],
    ) -> list[str]:
        rows = [f"Выбран режим: {mode}."]
        if relevant_case:
            title = str(relevant_case.get("title", "")) if isinstance(relevant_case, dict) else ""
            rows.append(f"Использован релевантный кейс: {title}.")
        else:
            rows.append("Реальный кейс не найден, поэтому допустима типичная рабочая ситуация без выдуманных компаний и цифр.")
        if graph_links:
            rows.append("Угол усилен связями из Knowledge Graph.")
        if author_brain.get("writing_dna"):
            rows.append("Стиль выбран по Writing DNA: наблюдение раньше теории, живой практический голос, без AI-клише.")
        return rows
