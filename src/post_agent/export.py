from __future__ import annotations

from pathlib import Path

from .daily_brief import DailyBriefService
from .web import render_daily_brief


def export_daily_brief(output: str | Path = "build/daily-brief.html") -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    html = render_daily_brief(DailyBriefService().build_today())
    path.write_text(html, encoding="utf-8")
    return path
