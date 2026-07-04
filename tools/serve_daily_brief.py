from __future__ import annotations

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from post_agent.web import run_server  # noqa: E402


if __name__ == "__main__":
    lan_mode = os.environ.get("LAN", "").lower() in {"1", "true", "yes", "on"}
    run_server(
        host=os.environ.get("HOST", "0.0.0.0" if lan_mode else "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
