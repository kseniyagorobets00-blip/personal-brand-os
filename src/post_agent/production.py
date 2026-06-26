from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .ai_gateway import DEFAULT_ENV_PATH, load_ai_config


@dataclass(frozen=True)
class ProductionCheck:
    ok: bool
    rows: tuple[str, ...]


def run_production_check(root: Path | None = None) -> ProductionCheck:
    root = root or Path(__file__).resolve().parents[2]
    config = load_ai_config()
    rows: list[str] = []
    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        rows.append(("OK " if condition else "FAIL ") + message)
        if not condition:
            ok = False

    check(DEFAULT_ENV_PATH.exists() or bool(os.environ.get("PROXY_API_KEY")), ".env or environment variables are available")
    check(config.is_configured, "ProxyAPI is configured")
    check((root / "data").exists(), "data directory exists")
    check((root / "src" / "post_agent").exists(), "application package exists")
    check((root / "tools" / "serve_daily_brief.py").exists(), "server entrypoint exists")
    check(bool(os.environ.get("PORT")) or True, "PORT can be provided by Railway or defaults locally")
    return ProductionCheck(ok=ok, rows=tuple(rows))
