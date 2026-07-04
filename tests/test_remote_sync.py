from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_agent import remote_sync  # noqa: E402


class FakeSupabase:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def request(self, method: str, query: str, body: object | None = None, prefer: str = "") -> object:
        if method == "GET":
            return [{"path": path, "content": content} for path, content in sorted(self.rows.items())]
        if method == "POST":
            assert isinstance(body, list)
            for row in body:
                self.rows[row["path"]] = row["content"]
            return None
        if method == "DELETE":
            key = query.split("path=eq.", 1)[1]
            from urllib.parse import unquote

            self.rows.pop(unquote(key), None)
            return None
        raise AssertionError(f"unexpected method {method}")


@pytest.fixture()
def fake_remote(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PERSONAL_BRAND_OS_DATA_DIR", str(data_dir))
    # Trigger the one-time seed bootstrap now so it cannot overwrite files the tests write below.
    from post_agent.storage import data_root

    data_root()
    fake = FakeSupabase()
    monkeypatch.setattr(remote_sync, "_request", fake.request)
    monkeypatch.setattr(remote_sync, "sync_config", lambda: ("https://example.supabase.co", "key"))
    return fake


def test_pull_all_writes_text_and_binary_files(fake_remote, tmp_path):
    fake_remote.rows["memory/inbox.json"] = {"kind": "text", "text": "[{\"id\": \"1\"}]"}
    fake_remote.rows["knowledge/documents/a.pdf"] = {
        "kind": "binary",
        "b64": base64.b64encode(b"%PDF-fake").decode("ascii"),
    }

    remote_paths = remote_sync.pull_all()

    root = tmp_path / "data"
    assert remote_paths == {"memory/inbox.json", "knowledge/documents/a.pdf"}
    assert json.loads((root / "memory" / "inbox.json").read_text(encoding="utf-8")) == [{"id": "1"}]
    assert (root / "knowledge" / "documents" / "a.pdf").read_bytes() == b"%PDF-fake"


def test_push_file_round_trips_text_and_binary(fake_remote, tmp_path):
    root = tmp_path / "data"
    (root / "learning").mkdir(parents=True, exist_ok=True)
    (root / "learning" / "lessons.json").write_text("[]", encoding="utf-8")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "file.bin").write_bytes(b"\x00\x01binary")

    remote_sync.push_file("learning/lessons.json")
    remote_sync.push_file("docs/file.bin")

    assert fake_remote.rows["learning/lessons.json"] == {"kind": "text", "text": "[]"}
    stored = fake_remote.rows["docs/file.bin"]
    assert stored["kind"] == "binary"
    assert base64.b64decode(stored["b64"]) == b"\x00\x01binary"


def test_push_missing_uploads_only_new_local_files(fake_remote, tmp_path):
    root = tmp_path / "data"
    (root / "seeds").mkdir(parents=True, exist_ok=True)
    (root / "seeds" / "known.json").write_text("{}", encoding="utf-8")
    (root / "seeds" / "new.json").write_text("{\"fresh\": true}", encoding="utf-8")

    pushed = remote_sync.push_missing({"seeds/known.json"})

    # data_root() bootstraps default seed files too, so more than one file is pushed;
    # the important contract: new files are uploaded, known remote paths are skipped.
    assert pushed >= 1
    assert "seeds/new.json" in fake_remote.rows
    assert "seeds/known.json" not in fake_remote.rows


def test_delete_remote_removes_row(fake_remote):
    fake_remote.rows["memory/inbox.json"] = {"kind": "text", "text": "[]"}

    remote_sync.delete_remote("memory/inbox.json")

    assert fake_remote.rows == {}


def test_sync_disabled_without_config(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(remote_sync, "_read_env_file", lambda path: {})
    assert remote_sync.is_enabled() is False
    assert remote_sync.bootstrap() is False
