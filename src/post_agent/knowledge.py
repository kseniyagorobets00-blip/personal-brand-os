from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import re
import zlib
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
    semantic_chunks: tuple[str, ...] = ()
    document_metadata: dict[str, object] | None = None
    chunk_metadata: tuple[dict[str, object], ...] = ()
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

        text = normalize_document_text(extract_text(stored_path, extension), extension)
        chunk_metadata = build_semantic_chunk_metadata(text)
        semantic_chunks = tuple(str(chunk.get("content", "")) for chunk in chunk_metadata if str(chunk.get("content", "")).strip())
        document_metadata = analyze_document_structure(Path(filename).stem.strip() or safe_name, text, chunk_metadata)
        analysis_text = _analysis_text(text, semantic_chunks, document_metadata=document_metadata, chunk_metadata=chunk_metadata)
        analysis = analyze_memory_text(Path(filename).stem.strip() or safe_name, analysis_text)
        analysis.update(
            {
                "document_metadata": document_metadata,
                "semantic_chunks": list(semantic_chunks[:12]),
                "chunks": list(chunk_metadata[:12]),
            }
        )
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
            semantic_chunks=semantic_chunks,
            document_metadata=document_metadata,
            chunk_metadata=chunk_metadata,
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
            semantic_chunks=tuple(str(chunk) for chunk in item.get("semantic_chunks", ()) if str(chunk).strip()) if isinstance(item.get("semantic_chunks", ()), list) else (),
            document_metadata=item.get("document_metadata", {}) if isinstance(item.get("document_metadata", {}), dict) else {},
            chunk_metadata=tuple(chunk for chunk in item.get("chunk_metadata", ()) if isinstance(chunk, dict)) if isinstance(item.get("chunk_metadata", ()), list) else (),
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
            body_tokens = _tokens(
                _analysis_text(
                    document.content_text or document.excerpt,
                    document.semantic_chunks,
                    document_metadata=document.document_metadata,
                    chunk_metadata=document.chunk_metadata,
                )
            )
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
            return "Документ сохранен и обработан. Текстовое содержание не найдено, но файл добавлен в память."
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


def normalize_document_text(text: str, extension: str) -> str:
    if extension == ".pdf":
        return _finalize_pdf_text(text)
    if extension == ".md":
        return _normalize_markdown_text(text)
    return _plain_text_to_markdown(text)


def analyze_document_structure(title: str, text: str, chunks: tuple[dict[str, object], ...]) -> dict[str, object]:
    combined = _analysis_text(text, tuple(str(chunk.get("content", "")) for chunk in chunks), chunk_metadata=chunks)
    companies = _known_entities(combined, ("MAYRVEDA", "Grand Marine Garden", "Mriya", "Красная Поляна", "Еврострой"))
    projects = _project_names(text, companies)
    topics = _known_entities(
        combined,
        (
            "Operations",
            "Customer Experience",
            "Service Design",
            "Hospitality",
            "Luxury Hospitality",
            "Guest Experience",
            "SOP",
            "Operational Excellence",
            "AI",
            "Project Management",
        ),
    )
    skills = _known_entities(
        combined,
        (
            "SOP",
            "service blueprint",
            "journey map",
            "audit",
            "analytics",
            "training",
            "process design",
            "CX audit",
            "регламент",
            "аудит",
            "обучение",
            "диагностика",
        ),
    )
    industries = _known_entities(combined, ("Hospitality", "Hotels", "Real Estate", "Development", "Service", "гостеприимство", "девелопмент", "сервис"))
    keywords = _keywords(combined, limit=18)
    return {
        "title": title,
        "document_type": _document_type(title, combined),
        "summary": _document_summary(text, chunks),
        "skills": skills,
        "competencies": sorted(set([*skills, *topics])),
        "topics": topics,
        "companies": companies,
        "projects": projects,
        "industries": industries,
        "entities": sorted(set([*companies, *projects, *topics])),
        "keywords": keywords,
        "language": _document_language(combined),
        "chunks": [_chunk_public_metadata(chunk) for chunk in chunks],
    }


def build_semantic_chunk_metadata(text: str) -> tuple[dict[str, object], ...]:
    chunks = build_semantic_chunks(text)
    result: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        title = _chunk_title(chunk, index)
        keywords = _keywords(chunk, limit=8)
        result.append(
            {
                "title": title,
                "type": _chunk_type(title, chunk),
                "summary": _chunk_summary(chunk),
                "keywords": keywords,
                "embedding": _keyword_embedding(keywords),
                "content": chunk,
            }
        )
    return tuple(result)


def build_semantic_chunks(text: str) -> tuple[str, ...]:
    blocks = re.split(r"\n(?=#{1,3}\s+)", text.strip())
    chunks: list[str] = []
    current = ""
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if _is_case_heading(block) and current:
            chunks.append(current.strip())
            current = block
            continue
        if current and len((current + "\n\n" + block).split()) > 260:
            chunks.append(current.strip())
            current = block
        else:
            current = block if not current else current + "\n\n" + block
    if current:
        chunks.append(current.strip())
    if not chunks and text.strip():
        chunks = [text.strip()]
    return tuple(chunks)


def _analysis_text(
    text: str,
    semantic_chunks: tuple[str, ...] | list[str],
    document_metadata: dict[str, object] | None = None,
    chunk_metadata: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
) -> str:
    chunks = "\n\n".join(str(chunk).strip() for chunk in semantic_chunks if str(chunk).strip())
    metadata_text = ""
    if document_metadata:
        metadata_text = json.dumps(document_metadata, ensure_ascii=False)
    chunk_text = "\n\n".join(
        " ".join(
            (
                str(chunk.get("title", "")),
                str(chunk.get("type", "")),
                str(chunk.get("summary", "")),
                " ".join(str(item) for item in chunk.get("keywords", []) if str(item).strip()) if isinstance(chunk.get("keywords", []), list) else "",
            )
        )
        for chunk in chunk_metadata
        if isinstance(chunk, dict)
    )
    return f"{metadata_text}\n\n{chunk_text}\n\n{chunks}\n\n{text.strip()}".strip()


def extract_pdf_text(path: Path) -> str:
    extractors = (
        extract_pdf_text_with_pymupdf,
        extract_pdf_text_with_pdfplumber,
        extract_pdf_text_with_pypdf,
        extract_pdf_text_from_streams,
    )
    for extractor in extractors:
        text = _clean_extracted_pdf_text(extractor(path))
        if _is_readable_pdf_text(text):
            return _finalize_pdf_text(text)
    return _finalize_pdf_text(extract_pdf_text_with_ocr(path))


def extract_pdf_text_with_pymupdf(path: Path) -> str:
    try:
        import fitz
    except Exception:
        return ""
    try:
        document = fitz.open(str(path))
    except Exception:
        return ""
    pages: list[str] = []
    try:
        for page_number, page in enumerate(document, start=1):
            blocks = page.get_text("blocks", sort=True)
            page_lines: list[str] = []
            for block in blocks:
                text = str(block[4] if len(block) > 4 else "").strip()
                if text:
                    page_lines.append(_normalize_pdf_block(text))
            if page_lines:
                pages.append(f"--- Page {page_number} ---\n" + "\n\n".join(page_lines))
    finally:
        document.close()
    return "\n\n".join(pages).strip()


def extract_pdf_text_with_pdfplumber(path: Path) -> str:
    try:
        import pdfplumber
    except Exception:
        return ""
    pages: list[str] = []
    try:
        with pdfplumber.open(str(path)) as document:
            for page_number, page in enumerate(document.pages, start=1):
                text = page.extract_text(x_tolerance=1, y_tolerance=3, layout=True) or ""
                text = _normalize_pdf_block(text)
                if text:
                    pages.append(f"--- Page {page_number} ---\n{text}")
    except Exception:
        return ""
    return "\n\n".join(pages).strip()


def extract_pdf_text_with_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path))
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = _normalize_pdf_block(page.extract_text() or "")
            if text:
                pages.append(f"--- Page {page_number} ---\n{text}")
        return "\n\n".join(pages).strip()
    except Exception:
        return ""


def extract_pdf_text_with_ocr(path: Path) -> str:
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except Exception:
        return ""
    try:
        document = fitz.open(str(path))
    except Exception:
        return ""
    pages: list[str] = []
    try:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            text = pytesseract.image_to_string(image, lang="eng+rus")
            text = _normalize_pdf_block(text)
            if text:
                pages.append(f"--- Page {page_number} ---\n{text}")
    except Exception:
        return ""
    finally:
        document.close()
    return "\n\n".join(pages).strip()


def extract_pdf_text_from_streams(path: Path) -> str:
    data = path.read_bytes()
    try:
        raw = data.decode("latin-1", errors="ignore")
    except Exception:
        return ""
    chunks = [raw]
    for match in re.finditer(rb"(<<.*?>>)\s*stream\r?\n(.*?)\r?\nendstream", data, flags=re.DOTALL):
        header = match.group(1)
        stream = match.group(2)
        if b"FlateDecode" in header:
            try:
                stream = zlib.decompress(stream)
            except zlib.error:
                continue
        try:
            chunks.append(stream.decode("latin-1", errors="ignore"))
        except Exception:
            continue
    candidates: list[str] = []
    for chunk in chunks:
        candidates.extend(_pdf_text_candidates(chunk))
    return _normalize_pdf_block("\n".join(candidates))


def _pdf_text_candidates(raw: str) -> list[str]:
    texts: list[str] = []
    texts.extend(_decode_pdf_literal(item) for item in re.findall(r"\(([^()]*)\)\s*Tj", raw))
    for array in re.findall(r"\[(.*?)\]\s*TJ", raw, flags=re.DOTALL):
        texts.append(" ".join(_decode_pdf_literal(item) for item in re.findall(r"\(([^()]*)\)", array)))
        texts.extend(_decode_pdf_hex(item) for item in re.findall(r"<([0-9A-Fa-f]+)>", array))
    texts.extend(_decode_pdf_hex(item) for item in re.findall(r"<([0-9A-Fa-f]{4,})>\s*Tj", raw))
    return [item for item in texts if item.strip()]


def _decode_pdf_literal(value: str) -> str:
    value = value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    value = value.replace(r"\n", "\n").replace(r"\r", "\n").replace(r"\t", "\t")
    return unescape(value)


def _decode_pdf_hex(value: str) -> str:
    try:
        data = bytes.fromhex(value)
    except ValueError:
        return ""
    for encoding in ("utf-16-be", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding).strip("\ufeff\x00")
        except UnicodeDecodeError:
            continue
        if text:
            return text
    return ""


def _normalize_pdf_block(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\x00", "").splitlines()]
    compact_lines: list[str] = []
    previous_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not previous_blank:
                compact_lines.append("")
            previous_blank = True
            continue
        compact_lines.append(stripped)
        previous_blank = False
    return "\n".join(compact_lines).strip()


def _clean_extracted_pdf_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\ufffd", "")
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _looks_like_pdf_service_line(stripped):
            continue
        lines.append(line)
    return _normalize_pdf_block("\n".join(lines))


def _finalize_pdf_text(text: str) -> str:
    clean = _clean_extracted_pdf_text(text)
    if not clean:
        return ""
    repaired_lines = _repair_pdf_line_flow(clean.splitlines())
    return _format_pdf_text_as_markdown(repaired_lines)


def _repair_pdf_line_flow(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current = ""
    for raw_line in lines:
        line = _repair_pdf_spacing(raw_line.strip())
        if not line or _looks_like_pdf_service_line(line) or _looks_like_truncated_pdf_fragment(line):
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue
        if not current:
            current = line
            continue
        if _should_keep_pdf_line_break(current, line):
            paragraphs.append(current.strip())
            current = line
            continue
        current = _join_pdf_line(current, line)
    if current:
        paragraphs.append(current.strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def _repair_pdf_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([a-zа-яё])([A-ZА-ЯЁ])", r"\1 \2", text)
    text = re.sub(r"([A-Za-zА-Яа-яЁё])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([A-Za-zА-Яа-яЁё])", r"\1 \2", text)
    text = re.sub(r"([.!?;:])([A-Za-zА-Яа-яЁё])", r"\1 \2", text)
    text = re.sub(r"([,])([A-Za-zА-Яа-яЁё])", r"\1 \2", text)
    return text


def _join_pdf_line(previous: str, current: str) -> str:
    if previous.endswith("-"):
        return previous[:-1] + current
    return previous + " " + current


def _should_keep_pdf_line_break(previous: str, current: str) -> bool:
    if _pdf_heading_level(previous) or _pdf_heading_level(current):
        return True
    if previous.endswith((".", "!", "?", ":", ";")):
        return True
    if _looks_like_list_item(current):
        return True
    if re.match(r"^\d+[\.)]\s+", current):
        return True
    return False


def _format_pdf_text_as_markdown(paragraphs: list[str]) -> str:
    formatted: list[str] = []
    last_was_heading = False
    for index, paragraph in enumerate(_merge_pdf_body_lines(paragraphs)):
        level = _pdf_heading_level(paragraph, is_first=index == 0)
        if level:
            heading = paragraph.lstrip("# ").strip()
            formatted.append("#" * level + " " + heading)
            last_was_heading = True
        elif _looks_like_list_item(paragraph):
            formatted.append(_normalize_list_item(paragraph))
            last_was_heading = False
        else:
            if last_was_heading and formatted and formatted[-1].startswith("# "):
                last_was_heading = False
            formatted.append(paragraph)
            last_was_heading = False
    return "\n\n".join(formatted).strip()


def _merge_pdf_body_lines(paragraphs: list[str]) -> list[str]:
    merged: list[str] = []
    for paragraph in paragraphs:
        if not merged:
            merged.append(paragraph)
            continue
        previous = merged[-1]
        if _pdf_heading_level(previous) or _pdf_heading_level(paragraph) or _looks_like_list_item(previous) or _looks_like_list_item(paragraph):
            merged.append(paragraph)
            continue
        if previous.endswith((".", "!", "?", ":", ";")):
            merged.append(paragraph)
            continue
        merged[-1] = _join_pdf_line(previous, paragraph)
    return merged


def _pdf_heading_level(text: str, is_first: bool = False) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    lowered = stripped.lower().strip("# ")
    if _is_case_heading(stripped):
        return 2
    if is_first and re.search(r"\bportfolio\b|портфолио", lowered):
        return 1
    if lowered in {"portfolio", "портфолио"}:
        return 1
    if _matches_pdf_heading(
        lowered,
        (
            "profile",
            "профиль",
            "ключевые результаты",
            "key results",
            "подход к работе",
            "work approach",
            "направления работы",
            "work directions",
            "контакты",
            "contacts",
        ),
    ):
        return 2
    if _matches_pdf_heading(
        lowered,
        (
            "опыт",
            "experience",
            "экспертиза",
            "expertise",
            "проблема",
            "problem",
            "действия",
            "actions",
            "результат",
            "result",
            "бизнес-эффект",
            "business effect",
        ),
    ):
        return 3
    if not _looks_like_document_heading(stripped):
        return 0
    return 2


def _matches_pdf_heading(text: str, variants: tuple[str, ...]) -> bool:
    return any(text == variant or text.startswith(f"{variant}:") for variant in variants)


def _is_case_heading(text: str) -> bool:
    stripped = text.strip().lstrip("# ").lower()
    return bool(re.match(r"^(case|кейс)\s*\d+", stripped))


def _looks_like_document_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 70:
        return False
    if stripped.endswith((".", ",", ";")):
        return False
    if _looks_like_list_item(stripped):
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9+&.-]+", stripped)
    if not words or len(words) > 8:
        return False
    if stripped.startswith("#"):
        return True
    uppercase_words = [word for word in words if any(char.isalpha() for char in word) and word.upper() == word]
    titlecase_words = [word for word in words if word[:1].isupper()]
    return len(uppercase_words) >= max(1, len(words) - 1) or (len(words) <= 5 and len(titlecase_words) == len(words))


def _looks_like_list_item(text: str) -> bool:
    return bool(re.match(r"^([-*•]|[0-9]+[\.)])\s+", text.strip()))


def _normalize_list_item(text: str) -> str:
    return re.sub(r"^([*•]|[0-9]+[\.)])\s+", "- ", text.strip())


def _looks_like_truncated_pdf_fragment(text: str) -> bool:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    if len(letters) > 3:
        return False
    return bool(letters) and text.isalpha() and not text.isupper()


def _normalize_markdown_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\x00", "").splitlines()]
    result: list[str] = []
    previous_blank = False
    for line in lines:
        stripped = _repair_pdf_spacing(line.strip())
        if not stripped:
            if not previous_blank:
                result.append("")
            previous_blank = True
            continue
        result.append(stripped)
        previous_blank = False
    return "\n".join(result).strip()


def _plain_text_to_markdown(text: str) -> str:
    clean = _normalize_markdown_text(text)
    if not clean:
        return ""
    lines = []
    for line in clean.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
        elif _pdf_heading_level(stripped):
            lines.append("#" * _pdf_heading_level(stripped) + " " + stripped.lstrip("# ").strip())
        elif _looks_like_list_item(stripped):
            lines.append(_normalize_list_item(stripped))
        else:
            lines.append(stripped)
    return _normalize_markdown_text("\n".join(lines))


def _chunk_public_metadata(chunk: dict[str, object]) -> dict[str, object]:
    return {
        "title": chunk.get("title", ""),
        "type": chunk.get("type", ""),
        "summary": chunk.get("summary", ""),
        "keywords": chunk.get("keywords", []),
        "embedding": chunk.get("embedding", []),
    }


def _chunk_title(chunk: str, index: int) -> str:
    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip()
    first = " ".join(chunk.split())[:80].strip()
    return first or f"Chunk {index}"


def _chunk_type(title: str, chunk: str) -> str:
    lowered = f"{title}\n{chunk}".lower()
    if _is_case_heading(title) or "### проблема" in lowered or "### problem" in lowered:
        return "case"
    if any(word in lowered for word in ("контакты", "contacts", "email", "@")):
        return "contacts"
    if any(word in lowered for word in ("ключевые результаты", "key results", "результат")):
        return "results"
    if any(word in lowered for word in ("экспертиза", "expertise", "skills")):
        return "expertise"
    if any(word in lowered for word in ("профиль", "profile", "опыт", "experience")):
        return "profile"
    if any(word in lowered for word in ("подход", "approach", "направления", "directions")):
        return "approach"
    return "section"


def _chunk_summary(chunk: str) -> str:
    compact = " ".join(line.strip("#- ").strip() for line in chunk.splitlines() if line.strip())
    sentences = re.split(r"(?<=[.!?])\s+", compact)
    summary = " ".join(sentence for sentence in sentences[:2] if sentence).strip()
    return summary[:420]


def _document_summary(text: str, chunks: tuple[dict[str, object], ...]) -> str:
    summaries = [str(chunk.get("summary", "")).strip() for chunk in chunks if str(chunk.get("summary", "")).strip()]
    if summaries:
        return " ".join(summaries[:3])[:700]
    return _chunk_summary(text)


def _document_type(title: str, text: str) -> str:
    lowered = f"{title}\n{text}".lower()
    checks = (
        ("portfolio", ("портфолио", "portfolio", "кейc", "кейс")),
        ("resume", ("резюме", "cv", "curriculum vitae")),
        ("sop", ("sop", "standard operating procedure", "регламент")),
        ("instruction", ("инструкция", "manual", "guide", "how to")),
        ("research", ("исследование", "research", "methodology")),
        ("article", ("статья", "article")),
        ("book", ("книга", "book", "chapter")),
        ("note", ("заметка", "note")),
    )
    for document_type, markers in checks:
        if any(marker in lowered for marker in markers):
            return document_type
    return "document"


def _document_language(text: str) -> str:
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cyrillic and latin and min(cyrillic, latin) / max(cyrillic, latin) > 0.2:
        return "RU/EN"
    if cyrillic >= latin:
        return "RU"
    return "EN"


def _known_entities(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _project_names(text: str, companies: list[str]) -> list[str]:
    projects = set(companies)
    for match in re.findall(r"^#{2,3}\s+(?:Кейс|Case)\s*\d+\.?\s+(.+)$", text, flags=re.MULTILINE):
        project = match.strip()
        if project:
            projects.add(project[:120])
    return sorted(projects)


def _keywords(text: str, limit: int = 12) -> list[str]:
    stop_words = {
        "and",
        "the",
        "for",
        "with",
        "this",
        "that",
        "или",
        "как",
        "что",
        "это",
        "для",
        "при",
        "над",
        "под",
        "кейс",
    }
    counts: dict[str, int] = {}
    for word in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9+-]{2,}", text):
        key = word.strip("-+").lower()
        if len(key) < 3 or key in stop_words:
            continue
        counts[key] = counts.get(key, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _keyword_embedding(keywords: list[str]) -> list[float]:
    if not keywords:
        return []
    vector = [0.0, 0.0, 0.0, 0.0]
    for keyword in keywords:
        bucket = sum(ord(char) for char in keyword) % len(vector)
        vector[bucket] += 1.0
    total = sum(vector) or 1.0
    return [round(value / total, 4) for value in vector]


def _looks_like_pdf_service_line(line: str) -> bool:
    if not line:
        return False
    lowered = line.lower()
    if re.fullmatch(r"-{2,}\s*page\s+\d+\s*-{2,}", lowered):
        return True
    if re.fullmatch(r"page\s+\d+(\s+of\s+\d+)?", lowered):
        return True
    service_tokens = (
        "%pdf",
        " obj",
        "endobj",
        "stream",
        "endstream",
        "xref",
        "trailer",
        "startxref",
        "/filter",
        "/flatedecode",
        "/length",
        "/type",
        "/font",
        "/xobject",
        "/contents",
        "/resources",
    )
    if any(token in lowered for token in service_tokens):
        return True
    if re.fullmatch(r"[<>{}\[\]/\\0-9A-Fa-f\s._:%-]{12,}", line):
        return True
    return False


def _is_readable_pdf_text(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 12:
        return False

    chars = [char for char in compact if not char.isspace()]
    if not chars:
        return False

    letters = sum(1 for char in chars if char.isalpha())
    digits = sum(1 for char in chars if char.isdigit())
    controls = sum(1 for char in chars if ord(char) < 32)
    suspicious = sum(1 for char in chars if char in "{}[]<>@=\\|~^")
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", compact, flags=re.UNICODE)
    pdf_markers = len(re.findall(r"\b(?:obj|endobj|stream|endstream|xref|trailer|FlateDecode|BT|ET|Tf|Tj|TJ)\b", compact))

    if controls:
        return False
    if pdf_markers >= 2:
        return False
    if letters / len(chars) < 0.45:
        return False
    if suspicious / len(chars) > 0.08:
        return False
    if len(words) < 2 and letters + digits < 24:
        return False
    return True


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
