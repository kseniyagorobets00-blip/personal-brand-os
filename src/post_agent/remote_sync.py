from __future__ import annotations

"""Mirror the local data directory to Supabase so memory survives restarts and devices.

The app keeps reading and writing plain JSON files under the data directory.
This module adds a thin replication layer on top:

- on startup, `bootstrap()` downloads every stored file from Supabase into the
  local data directory (remote state wins over the bundled seed files);
- a background thread then watches the data directory and pushes every change
  (create, update, delete) back to Supabase over its REST API.

Everything is stored in one table created by the user:

    create table if not exists app_data (
      path text primary key,
      content jsonb not null,
      updated_at timestamptz not null default now()
    );

Text files are stored as {"kind": "text", "text": ...}; binary uploads
(PDF/DOCX documents) as {"kind": "binary", "b64": ...}.
"""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .ai_gateway import DEFAULT_ENV_PATH, _read_env_file
from .storage import data_root

SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_SERVICE_KEY"
TABLE = "app_data"
TEXT_EXTENSIONS = {".json", ".md", ".txt"}
PULL_PAGE_SIZE = 500
_IGNORED_PARTS = {"__pycache__", ".DS_Store"}

_watcher_started = threading.Event()
_last_error_log = 0.0
_last_success_at: float = 0.0
_last_error_message: str = ""
_last_error_at: float = 0.0


def status() -> dict[str, object]:
    """A small, UI-friendly snapshot of the cloud-sync state."""
    if not is_enabled():
        return {"enabled": False, "ok": False, "last_success_at": None, "error": ""}
    ok = _last_success_at > 0 and (_last_error_at <= _last_success_at)
    return {
        "enabled": True,
        "ok": ok,
        "last_success_at": _last_success_at or None,
        "error": _last_error_message if not ok else "",
    }


def sync_config() -> tuple[str, str] | None:
    env_file = _read_env_file(DEFAULT_ENV_PATH)
    url = (os.environ.get(SUPABASE_URL_ENV) or env_file.get(SUPABASE_URL_ENV, "")).strip().rstrip("/")
    key = (os.environ.get(SUPABASE_KEY_ENV) or env_file.get(SUPABASE_KEY_ENV, "")).strip()
    if url and key:
        return url, key
    return None


def is_enabled() -> bool:
    return sync_config() is not None


def bootstrap() -> bool:
    """Pull remote state, then start watching local changes. Safe to call twice."""
    config = sync_config()
    if not config:
        return False
    print(f"Supabase sync: папка данных = {data_root()}")
    try:
        remote_paths = pull_all()
        print(f"Supabase sync: восстановлено файлов из облака: {len(remote_paths)}.")
        pushed = push_missing(remote_paths)
        if pushed:
            print(f"Supabase sync: загружено новых локальных файлов в облако: {pushed}.")
    except Exception as exc:  # noqa: BLE001 - the app must start even if sync is down
        print(f"Supabase sync: не удалось скачать данные ({exc}). Работаем с локальными файлами.")
    start_background_sync()
    return True


def pull_all() -> set[str]:
    """Download every remote file into the local data directory. Remote wins."""
    root = data_root()
    remote_paths: set[str] = set()
    offset = 0
    while True:
        rows = _request(
            "GET",
            f"?select=path,content&order=path.asc&limit={PULL_PAGE_SIZE}&offset={offset}",
        )
        if not isinstance(rows, list):
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            rel_path = str(row.get("path", "")).strip()
            content = row.get("content")
            if not rel_path or not isinstance(content, dict):
                continue
            target = root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if content.get("kind") == "binary":
                target.write_bytes(base64.b64decode(str(content.get("b64", ""))))
            else:
                target.write_text(str(content.get("text", "")), encoding="utf-8")
            remote_paths.add(rel_path)
        if len(rows) < PULL_PAGE_SIZE:
            break
        offset += PULL_PAGE_SIZE
    return remote_paths


def push_missing(remote_paths: set[str]) -> int:
    """Upload local files that the remote store does not know about yet."""
    pushed = 0
    for rel_path in sorted(_scan()):
        if rel_path in remote_paths:
            continue
        push_file(rel_path)
        pushed += 1
    return pushed


def push_file(rel_path: str) -> None:
    """Upsert one local file to Supabase."""
    source = data_root() / rel_path
    payload = _encode_file(source)
    if payload is None:
        return
    _request(
        "POST",
        "",
        body=[
            {
                "path": rel_path,
                "content": payload,
                # updated_at only defaults on insert, so set it on every upsert to keep it truthful.
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        prefer="resolution=merge-duplicates,return=minimal",
    )
    global _last_success_at
    _last_success_at = time.time()


def delete_remote(rel_path: str) -> None:
    _request("DELETE", f"?path=eq.{urllib.parse.quote(rel_path, safe='')}", prefer="return=minimal")


def start_background_sync() -> bool:
    if _watcher_started.is_set():
        return False
    _watcher_started.set()
    thread = threading.Thread(target=_watch_loop, name="supabase-sync", daemon=True)
    thread.start()
    return True


def _watch_loop() -> None:
    interval = _env_float("SUPABASE_SYNC_INTERVAL_SECONDS", 3.0)
    snapshot = _scan()
    print(f"Supabase sync: фоновое слежение запущено, файлов под наблюдением: {len(snapshot)}, интервал {interval}с.")
    while True:
        time.sleep(interval)
        try:
            current = _scan()
        except OSError as exc:
            _log_sync_error(exc)
            continue
        except Exception as exc:  # noqa: BLE001 - never let the watcher thread die silently
            _log_sync_error(exc)
            continue
        changed = [path for path, stamp in current.items() if snapshot.get(path) != stamp]
        removed = [path for path in snapshot if path not in current]
        for rel_path in changed:
            try:
                push_file(rel_path)
                snapshot[rel_path] = current[rel_path]
                print(f"Supabase sync: отправлено в облако -> {rel_path}")
            except Exception as exc:  # noqa: BLE001 - keep the loop alive on network errors
                _log_sync_error(exc)
        for rel_path in removed:
            try:
                delete_remote(rel_path)
                snapshot.pop(rel_path, None)
                print(f"Supabase sync: удалено из облака -> {rel_path}")
            except Exception as exc:  # noqa: BLE001
                _log_sync_error(exc)
        for rel_path in current:
            snapshot.setdefault(rel_path, current[rel_path])


def _scan() -> dict[str, tuple[int, int]]:
    root = data_root()
    snapshot: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snapshot
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _IGNORED_PARTS or part.startswith(".") for part in path.parts):
            continue
        stat = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _encode_file(path: Path) -> dict[str, str] | None:
    try:
        if path.suffix.lower() in TEXT_EXTENSIONS:
            return {"kind": "text", "text": path.read_text(encoding="utf-8")}
        return {"kind": "binary", "b64": base64.b64encode(path.read_bytes()).decode("ascii")}
    except (OSError, UnicodeDecodeError):
        return None


def _request(method: str, query: str, body: object | None = None, prefer: str = "") -> object:
    config = sync_config()
    if not config:
        raise RuntimeError("Supabase sync is not configured.")
    url, key = config
    request = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}{query}",
        method=method,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            **({"Prefer": prefer} if prefer else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _log_sync_error(exc: Exception) -> None:
    global _last_error_log, _last_error_message, _last_error_at
    _last_error_message = str(exc)
    _last_error_at = time.time()
    now = time.monotonic()
    if now - _last_error_log > 60:
        _last_error_log = now
        print(f"Supabase sync: ошибка синхронизации ({exc}). Повторю автоматически.")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default
