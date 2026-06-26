from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent


@dataclass(frozen=True)
class PostVariant:
    name: str
    text: str


class PostGenerator:
    """Generates post drafts in the author's editorial style."""

    def generate(self, topic: str, platform: str = "linkedin") -> list[PostVariant]:
        cleaned_topic = self._normalize_topic(topic)
        platform_note = self._platform_note(platform)

        return [
            PostVariant(
                name="neutral",
                text=self._neutral_post(cleaned_topic, platform_note),
            ),
            PostVariant(
                name="strong",
                text=self._strong_post(cleaned_topic, platform_note),
            ),
            PostVariant(
                name="provocative",
                text=self._provocative_post(cleaned_topic, platform_note),
            ),
        ]

    def _normalize_topic(self, topic: str) -> str:
        cleaned = " ".join(topic.strip().split())
        if not cleaned:
            raise ValueError("Post topic cannot be empty.")
        return cleaned

    def _platform_note(self, platform: str) -> str:
        notes = {
            "linkedin": "Формат LinkedIn: деловой контекст, ясная мысль, без лишней драматизации.",
            "setka": "Формат Сетки: живее, короче, с более прямым заходом и личной интонацией.",
        }
        return notes.get(platform.lower(), notes["linkedin"])

    def _neutral_post(self, topic: str, platform_note: str) -> str:
        return dedent(
            f"""
            Тема: {topic}

            Есть ситуации, где проблема выглядит проще, чем она есть на самом деле.
            Снаружи кажется: нужно просто принять решение, поменять процесс или добавить еще один инструмент.
            Но внутри обычно лежит другой слой: привычки команды, качество коммуникации и то, насколько честно люди называют реальность.

            Я часто вижу это в теме «{topic}».

            Если разбирать спокойно, без героизма, важны три вопроса:
            1. Что именно сейчас не работает?
            2. Почему это стало нормой?
            3. Какое одно действие изменит поведение, а не только презентацию?

            Хорошее решение редко начинается с красивой формулировки.
            Оно начинается с точного диагноза.

            Вывод: если тема важная, сначала стоит разобрать ситуацию, а уже потом выбирать инструмент, процесс или публичную позицию.

            {platform_note}
            """
        ).strip()

    def _strong_post(self, topic: str, platform_note: str) -> str:
        return dedent(
            f"""
            Тема: {topic}

            В теме «{topic}» чаще всего ломается не стратегия.
            Ломается честность разговора.

            Команды могут долго обсуждать цели, метрики и планы, но обходить главный вопрос:
            что мы на самом деле пытаемся не замечать?

            Из-за этого появляются странные компромиссы.
            Решения принимаются как будто рационально, но по факту защищают старые привычки.
            Люди называют это осторожностью, хотя иногда это просто страх увидеть проблему целиком.

            Сильный ход здесь не в том, чтобы говорить громче.
            Сильный ход в том, чтобы назвать ситуацию точнее:
            где мы теряем фокус, где подменяем работу активностью, где делаем вид, что процесс сам по себе создает результат.

            Вывод простой: пока команда не может честно описать проблему, она будет производить не решения, а версии самоуспокоения.

            {platform_note}
            """
        ).strip()

    def _provocative_post(self, topic: str, platform_note: str) -> str:
        return dedent(
            f"""
            Тема: {topic}

            Непопулярная мысль про «{topic}»: многим не нужно решение.
            Им нужно ощущение, что они уже что-то делают.

            Поэтому появляются встречи, документы, дорожные карты, обсуждения формата и новые правила.
            Все выглядит занято, разумно и профессионально.
            Но если спросить, какое поведение реально изменилось, часто наступает пауза.

            И вот в этой паузе обычно вся правда.

            Проблема не в том, что люди недостаточно умные.
            Проблема в том, что система научилась имитировать движение лучше, чем признавать тупик.

            В теме «{topic}» я бы начинал не с вопроса «что нам сделать?».
            Я бы начинал с более неприятного:
            что мы продолжаем делать, хотя уже знаем, что это не работает?

            Вывод: прогресс начинается не там, где команда добавляет новый план, а там, где она прекращает защищать старую иллюзию.

            {platform_note}
            """
        ).strip()

