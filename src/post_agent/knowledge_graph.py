from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

from .memory import MemoryInboxItem


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH_PATH = ROOT / "data" / "knowledge" / "graph.json"


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    type: str


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str


class KnowledgeGraph:
    """Local relationship map. It is intentionally replaceable by a graph DB later."""

    def __init__(self, path: Path = DEFAULT_GRAPH_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.write_graph([], [])

    def rebuild(
        self,
        documents: list[object],
        cases: list[object],
        memory_items: list[MemoryInboxItem],
    ) -> dict[str, object]:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        def add_node(label: str, node_type: str) -> str:
            clean = label.strip()
            node_id = _node_id(node_type, clean)
            if clean and node_id not in nodes:
                nodes[node_id] = GraphNode(id=node_id, label=clean, type=node_type)
            return node_id

        def link(source: str, target: str, relation: str) -> None:
            if source and target and source != target:
                edges.append(GraphEdge(source=source, target=target, relation=relation))

        for document in documents:
            document_id = add_node(getattr(document, "title", ""), "document")
            text = " ".join((getattr(document, "title", ""), getattr(document, "excerpt", ""), getattr(document, "content_text", "")))
            for theme in _themes(text):
                link(document_id, add_node(theme, "theme"), "mentions")
            for company in _companies(text):
                link(document_id, add_node(company, "company"), "mentions")

        for case in cases:
            case_id = add_node(getattr(case, "title", ""), "case")
            company = getattr(case, "company", "")
            link(case_id, add_node(company, "company"), "belongs_to")
            for topic in getattr(case, "key_topics", ()):
                link(case_id, add_node(str(topic), "theme"), "supports")
            for platform in getattr(case, "platforms", ()):
                link(case_id, add_node(str(platform), "platform"), "fits")

        for item in memory_items:
            if item.status != "accepted":
                continue
            memory_id = add_node(item.title, f"memory_{item.source_type}")
            extracted = item.extracted
            for theme in _as_list(extracted.get("themes")):
                link(memory_id, add_node(theme, "theme"), "extracts")
            for company in _as_list(extracted.get("companies")):
                link(memory_id, add_node(company, "company"), "extracts")
            for idea in _as_list(extracted.get("ideas"))[:5]:
                link(memory_id, add_node(idea, "idea"), "suggests")

        self.write_graph(list(nodes.values()), _dedupe_edges(edges))
        return self.read_graph()

    def read_graph(self) -> dict[str, object]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"nodes": [], "edges": [], "updated_at": ""}
        return raw if isinstance(raw, dict) else {"nodes": [], "edges": [], "updated_at": ""}

    def related_to(self, query: str, limit: int = 8) -> list[dict[str, str]]:
        graph = self.read_graph()
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return []
        query_tokens = _tokens(query)
        matched_ids = {
            str(node.get("id", ""))
            for node in nodes
            if query_tokens.intersection(_tokens(str(node.get("label", ""))))
        }
        related: list[dict[str, str]] = []
        for edge in edges:
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            if source in matched_ids or target in matched_ids:
                other = target if source in matched_ids else source
                node = next((item for item in nodes if str(item.get("id", "")) == other), None)
                if node:
                    related.append(
                        {
                            "label": str(node.get("label", "")),
                            "type": str(node.get("type", "")),
                            "relation": str(edge.get("relation", "")),
                        }
                    )
        return related[:limit]

    def write_graph(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        raw = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "nodes": [node.__dict__ for node in nodes],
            "edges": [edge.__dict__ for edge in edges],
        }
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _node_id(node_type: str, label: str) -> str:
    slug = "-".join(re.findall(r"[A-Za-zА-Яа-я0-9]+", label.lower(), flags=re.UNICODE))[:80]
    return f"{node_type}:{slug or 'unknown'}"


def _themes(text: str) -> list[str]:
    terms = (
        "Customer Experience",
        "Operations",
        "Service Design",
        "Luxury Hospitality",
        "Hospitality",
        "Guest Experience",
        "SOP",
        "AI",
        "Project Management",
        "Operational Excellence",
    )
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _companies(text: str) -> list[str]:
    terms = ("MAYRVEDA", "Mriya", "Красная Поляна", "Еврострой")
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower(), flags=re.UNICODE) if len(word) > 2}


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dedupe_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    seen: set[tuple[str, str, str]] = set()
    result: list[GraphEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.relation)
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result
