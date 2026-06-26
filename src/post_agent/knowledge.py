from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import re
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from .knowledge_graph import KnowledgeGraph
from .memory import MemoryInbox, analyze_memory_text


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
DEFAULT_DOCUMENT_DIR = DEFAULT_KNOWLEDGE_DIR / "documents"
DEFAULT_INDEX_PATH = DEFAULT_KNOWLEDGE_DIR / "index.json"
DEFAULT_CASES_PATH = DEFAULT_KNOWLEDGE_DIR / "cases.json"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    original_filename: str
    extension: str
    stored_path: str
    excerpt: str
    content_text: str
    word_count: int
    uploaded_at: str
    analysis: dict[str, object] | None = None


@dataclass(frozen=True)
class KnowledgeSearchResult:
    document: KnowledgeDocument
    reason: str
    score: int


@dataclass(frozen=True)
class KnowledgeCase:
    id: str
    title: str
    company: str
    what_happened: str
    reason: str
    solution: str
    result: str
    public_usage: str
    key_topics: tuple[str, ...]
    platforms: tuple[str, ...]
    created_at: str
    period: str = ""
    context: str = ""
    related_articles: tuple[str, ...] = ()
    key_takeaways: tuple[str, ...] = ()


class KnowledgeBase:
    def __init__(
        self,
        document_dir: Path = DEFAULT_DOCUMENT_DIR,
        index_path: Path = DEFAULT_INDEX_PATH,
        cases_path: Path = DEFAULT_CASES_PATH,
        memory_inbox: MemoryInbox | None = None,
        knowledge_graph: KnowledgeGraph | None = None,
    ) -> None:
        self.document_dir = document_dir
        self.index_path = index_path
        self.cases_path = index_path.with_name("cases.json") if cases_path == DEFAULT_CASES_PATH and index_path != DEFAULT_INDEX_PATH else cases_path
        memory_path = DEFAULT_KNOWLEDGE_DIR.parent / "memory" / "inbox.json" if self.index_path == DEFAULT_INDEX_PATH else self.index_path.parent / "memory" / "inbox.json"
        self.memory_inbox = memory_inbox or MemoryInbox(memory_path)
        self.knowledge_graph = knowledge_graph or KnowledgeGraph(self.index_path.parent / "graph.json")
        self.document_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index([])
        if not self.cases_path.exists():
            self._write_cases([])

    def list_documents(self) -> list[KnowledgeDocument]:
        return [self._document_from_raw(item) for item in self._read_index()]

    def get_document(self, document_id: str) -> KnowledgeDocument | None:
        for document in self.list_documents():
            if document.id == document_id:
                return document
        return None

    def add_document(self, filename: str, content: bytes) -> KnowledgeDocument:
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Unsupported document type.")

        document_id = uuid4().hex
        safe_name = self._safe_filename(filename)
        stored_name = f"{document_id}{extension}"
        stored_path = self.document_dir / stored_name
        stored_path.write_bytes(content)

        text = extract_text(stored_path, extension)
        analysis = analyze_memory_text(Path(filename).stem.strip() or safe_name, text)
        document = KnowledgeDocument(
            id=document_id,
            title=Path(filename).stem.strip() or safe_name,
            original_filename=safe_name,
            extension=extension,
            stored_path=self._stored_path_for_index(stored_path),
            excerpt=self._excerpt(text),
            content_text=text,
            word_count=len(re.findall(r"\w+", text, flags=re.UNICODE)),
            uploaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            analysis=analysis,
        )
        items = self._read_index()
        items.insert(0, document.__dict__)
        self._write_index(items)
        self.memory_inbox.add_item(
            source_type="document",
            source_id=document.id,
            title=f"Анализ документа: {document.title}",
            summary=document.excerpt,
            extracted=analysis,
        )
        self.rebuild_graph()
        return document

    def delete_document(self, document_id: str) -> bool:
        items = self._read_index()
        kept: list[dict[str, object]] = []
        removed: dict[str, object] | None = None
        for item in items:
            if item.get("id") == document_id:
                removed = item
            else:
                kept.append(item)
        if not removed:
            return False

        stored = ROOT / str(removed.get("stored_path", ""))
        if not stored.exists():
            stored = Path(str(removed.get("stored_path", "")))
        if stored.exists() and stored.is_file():
            stored.unlink()
        self._write_index(kept)
        self.rebuild_graph()
        return True

    def list_cases(self) -> list[KnowledgeCase]:
        return [self._case_from_raw(item) for item in self._read_cases()]

    def add_case(
        self,
        title: str,
        company: str,
        what_happened: str,
        reason: str,
        solution: str,
        result: str,
        public_usage: str,
        key_topics: tuple[str, ...] | list[str],
        platforms: tuple[str, ...] | list[str],
        period: str = "",
        context: str = "",
        related_articles: tuple[str, ...] | list[str] = (),
        key_takeaways: tuple[str, ...] | list[str] = (),
    ) -> KnowledgeCase:
        case = KnowledgeCase(
            id=uuid4().hex,
            title=title.strip(),
            company=company.strip(),
            what_happened=what_happened.strip(),
            reason=reason.strip(),
            solution=solution.strip(),
            result=result.strip(),
            public_usage=public_usage.strip(),
            key_topics=tuple(item for item in key_topics if item),
            platforms=tuple(item for item in platforms if item),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            period=period.strip(),
            context=context.strip(),
            related_articles=tuple(item for item in related_articles if item),
            key_takeaways=tuple(item for item in key_takeaways if item),
        )
        items = self._read_cases()
        items.insert(0, self._case_to_raw(case))
        self._write_cases(items)
        self.rebuild_graph()
        return case

    def delete_case(self, case_id: str) -> bool:
        items = self._read_cases()
        kept = [item for item in items if item.get("id") != case_id]
        if len(kept) == len(items):
            return False
        self._write_cases(kept)
        self.rebuild_graph()
        return True

    def rebuild_graph(self) -> dict[str, object]:
        return self.knowledge_graph.rebuild(
            documents=self.list_documents(),
            cases=self.list_cases(),
            memory_items=self.memory_inbox.list_items(),
        )

    def _read_index(self) -> list[dict[str, object]]:
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _write_index(self, items: list[dict[str, object]]) -> None:
        self.index_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_cases(self) -> list[dict[str, object]]:
        return json.loads(self.cases_path.read_text(encoding="utf-8"))

    def _write_cases(self, items: list[dict[str, object]]) -> None:
        self.cases_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _document_from_raw(self, item: dict[str, object]) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=str(item["id"]),
            title=str(item["title"]),
            original_filename=str(item["original_filename"]),
            extension=str(item["extension"]),
            stored_path=str(item["stored_path"]),
            excerpt=str(item.get("excerpt", "")),
            content_text=str(item.get("content_text", item.get("excerpt", ""))),
            word_count=int(item.get("word_count", 0)),
            uploaded_at=str(item.get("uploaded_at", "")),
            analysis=item.get("analysis", {}) if isinstance(item.get("analysis", {}), dict) else {},
        )

    def _case_from_raw(self, item: dict[str, object]) -> KnowledgeCase:
        return KnowledgeCase(
            id=str(item["id"]),
            title=str(item.get("title", "")),
            company=str(item.get("company", "")),
            what_happened=str(item.get("what_happened", "")),
            reason=str(item.get("reason", "")),
            solution=str(item.get("solution", "")),
            result=str(item.get("result", "")),
            public_usage=str(item.get("public_usage", "")),
            key_topics=tuple(item.get("key_topics", ())),
            platforms=tuple(item.get("platforms", ())),
            created_at=str(item.get("created_at", "")),
            period=str(item.get("period", "")),
            context=str(item.get("context", "")),
            related_articles=tuple(item.get("related_articles", ())),
            key_takeaways=tuple(item.get("key_takeaways", ())),
        )

    def _case_to_raw(self, case: KnowledgeCase) -> dict[str, object]:
        return {
            "id": case.id,
            "title": case.title,
            "company": case.company,
            "what_happened": case.what_happened,
            "reason": case.reason,
            "solution": case.solution,
            "result": case.result,
            "public_usage": case.public_usage,
            "key_topics": list(case.key_topics),
            "platforms": list(case.platforms),
            "created_at": case.created_at,
            "period": case.period,
            "context": case.context,
            "related_articles": list(case.related_articles),
            "key_takeaways": list(case.key_takeaways),
        }

    def search(self, query: str, limit: int = 8) -> list[KnowledgeSearchResult]:
        tokens = _tokens(query)
        if not tokens:
            return []
        results: list[KnowledgeSearchResult] = []
        for document in self.list_documents():
            title_tokens = _tokens(document.title)
            body_tokens = _tokens(document.content_text or document.excerpt)
            title_matches = sorted(tokens.intersection(title_tokens))
            body_matches = sorted(tokens.intersection(body_tokens))
            if not title_matches and not body_matches:
                continue
            score = len(title_matches) * 4 + len(body_matches)
            reason = self._search_reason(document, title_matches, body_matches)
            results.append(KnowledgeSearchResult(document=document, reason=reason, score=score))
        return sorted(results, key=lambda result: result.score, reverse=True)[:limit]

    def recommend_for_topics(self, topics: list[str] | tuple[str, ...], limit: int = 4) -> list[KnowledgeSearchResult]:
        seen: set[str] = set()
        results: list[KnowledgeSearchResult] = []
        for topic in topics:
            for result in self.search(topic, limit=limit):
                if result.document.id in seen:
                    continue
                seen.add(result.document.id)
                results.append(
                    KnowledgeSearchResult(
                        document=result.document,
                        reason=self._recommendation_reason(result.document, topic, result.reason),
                        score=result.score,
                    )
                )
        return sorted(results, key=lambda result: result.score, reverse=True)[:limit]

    def ensure_seed_documents(self) -> None:
        if self.list_documents():
            return
        self.add_document(
            "MAYRVEDA-cx-operations-note.md",
            (
                "# MAYRVEDA: Customer Experience and Operations\n\n"
                "Кейс MAYRVEDA показывает, что Customer Experience зависит от операционной дисциплины, "
                "точек передачи ответственности и ясных SOP. Этот материал полезен для тем про сервис, "
                "hospitality, Service Design и управленческий диагноз."
            ).encode("utf-8"),
        )
        self.add_document(
            "SOP-as-service-care.md",
            (
                "# SOP as care\n\n"
                "SOP может быть языком заботы о клиенте, если стандарт защищает сервис от случайности. "
                "Материал поддерживает идеи про hospitality, predictable service и Operational Excellence."
            ).encode("utf-8"),
        )

    def _search_reason(
        self,
        document: KnowledgeDocument,
        title_matches: list[str],
        body_matches: list[str],
    ) -> str:
        if title_matches:
            return f"Документ найден по названию: {', '.join(title_matches)}."
        return f"Документ найден по содержимому: {', '.join(body_matches[:4])}."

    def _recommendation_reason(self, document: KnowledgeDocument, topic: str, search_reason: str) -> str:
        if "mayrveda" in document.title.lower() or "mayrveda" in document.content_text.lower():
            return f"Использован кейс MAYRVEDA, потому что тема связана с {topic}. {search_reason}"
        if "sop" in document.title.lower() or "sop" in document.content_text.lower():
            return f"Найдена статья о SOP, которая поддерживает сегодняшнюю тему: {topic}. {search_reason}"
        return f"Документ рекомендован, потому что поддерживает тему: {topic}. {search_reason}"

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename).name.strip()
        return re.sub(r"[^A-Za-zА-Яа-я0-9._ -]+", "_", name) or "document"

    def _stored_path_for_index(self, path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    def _excerpt(self, text: str) -> str:
        compact = " ".join(text.split())
        if not compact:
            return "Текст пока не извлечен. Документ сохранен и готов для будущей AI-индексации."
        return compact[:900]


def extract_text(path: Path, extension: str) -> str:
    if extension in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if extension == ".docx":
        return extract_docx_text(path)
    if extension == ".pdf":
        return extract_pdf_text(path)
    return ""


def extract_docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    text = re.sub(r"<w:tab\s*/>", "\t", xml)
    text = re.sub(r"</w:p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)


def extract_pdf_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        raw = data.decode("latin-1", errors="ignore")
    except Exception:
        return ""
    candidates = re.findall(r"\(([^()]{3,})\)\s*Tj", raw)
    return " ".join(unescape(item) for item in candidates)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE)
    stop_words = {
        "как",
        "что",
        "это",
        "для",
        "или",
        "the",
        "and",
        "with",
        "через",
        "связана",
        "следствие",
    }
    return {word for word in words if len(word) > 2 and word not in stop_words}
