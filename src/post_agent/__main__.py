from __future__ import annotations

import argparse
import os

from .author_profile import DEFAULT_AUTHOR_PROFILE_PATH
from .ai_pipeline import AIPipeline, load_ai_status
from .daily_brief import DailyBriefService
from .export import export_daily_brief
from .production import run_production_check
from .web import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="post-agent",
        description="Run Personal Brand OS tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("daily-brief", help="Print today's Daily Brief summary.")
    subparsers.add_parser("ai-refresh", help="Run AI Pipeline once and save the result.")
    subparsers.add_parser("ai-status", help="Print current AI Pipeline status.")
    subparsers.add_parser("author-profile", help="Print Author Profile file path.")
    subparsers.add_parser("production-check", help="Check production readiness.")

    export = subparsers.add_parser("export-daily-brief", help="Rebuild Daily Brief from seed sources and export HTML.")
    export.add_argument("--output", default="build/daily-brief.html")

    rebuild = subparsers.add_parser("rebuild-daily-brief", help="Alias for export-daily-brief.")
    rebuild.add_argument("--output", default="build/daily-brief.html")

    serve = subparsers.add_parser("serve", help="Run the Daily Brief web app.")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve.add_argument(
        "--lan",
        action="store_true",
        help="Make the app available to phones and tablets on the same Wi-Fi network.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        host = "0.0.0.0" if args.lan and args.host == "127.0.0.1" else args.host
        run_server(host=host, port=args.port)
        return

    if args.command == "daily-brief":
        brief = DailyBriefService().build_today()
        print(f"Daily Brief: {brief.brief_date:%d.%m.%Y}")
        print(brief.executive_summary)
        print("\nTopics:")
        for item in brief.topics:
            print(f"- {item.title} ({item.score})")
        return

    if args.command == "ai-refresh":
        result = AIPipeline().run()
        status = load_ai_status()
        print(status.message)
        if status.error:
            print(status.error)
        if result:
            print("AI result saved.")
        return

    if args.command == "ai-status":
        status = load_ai_status()
        print(status.message)
        if status.error:
            print(status.error)
        return

    if args.command == "author-profile":
        print(DEFAULT_AUTHOR_PROFILE_PATH.resolve())
        return

    if args.command == "production-check":
        result = run_production_check()
        for row in result.rows:
            print(row)
        if not result.ok:
            raise SystemExit(1)
        return

    if args.command in ("export-daily-brief", "rebuild-daily-brief"):
        path = export_daily_brief(args.output)
        print(path.resolve())
        return

    parser.print_help()


if __name__ == "__main__":
    main()
