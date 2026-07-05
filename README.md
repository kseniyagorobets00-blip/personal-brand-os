# Personal Brand OS

Personal AI Operator for building an expert brand. Personal Brand OS is a full
editorial operating system: it plans what to publish, drafts it in the author's
voice, remembers everything it learns, and keeps a single source of truth for
every AI decision.

The whole app is a self-contained Python web server (`http.server`) rendering
server-side HTML with a shared page shell — no build step, no external UI
framework.

## Quick start

macOS / Linux:

```bash
export PYTHONPATH=src
python3 -m post_agent serve
```

Windows (PowerShell):

```powershell
$env:PYTHONPATH = "src"
python -m post_agent serve
```

Then open the Daily Brief:

```text
http://127.0.0.1:8000/daily-brief
```

To test from a phone or tablet on the same Wi-Fi network:

```bash
export PYTHONPATH=src
python3 -m post_agent serve --lan
```

## Main screens

Navigation is grouped by intent (see `docs/redesign-plan.md`):

- **Today → Daily Brief** (`/daily-brief`) — the morning work screen: what to
  publish today and why, what needs approval, what text is ready, and the draft
  chain `Шаблон → AI-черновик → Утверждённый текст`.
- **Planning**
  - **Content Plan** (`/content-plan`) — weekly/monthly plan, list or calendar
    view, the single source of truth that drives the brief and text generation.
  - **Texts** (`/texts`) — editorial workspace with a clean editor and Focus
    Mode; planned posts and archive.
  - **Ideas** (`/ideas`) — author key ideas (from the profile) and free ideas
    (from the Trend Radar); any idea can be promoted into the content plan.
- **Memory → Knowledge** (`/knowledge`) — documents, cases, and notes the AI
  learns from, with per-document AI analysis.
- **Signals → Trend Radar** (`/trend-radar`) — fresh topics matched to the
  author's platforms and Author Brain.
- **Settings**
  - **Author Profile** (`/author-profile`) — author base, Writing DNA, and
    editorial strategy in one place, saved through a single form.
  - **Bot Rules** (`/bot-rules`) — thinking rules, forbidden openings, platform
    and rubric rules; the single source AI generation reads from.
  - **Learning Center** (`/learning`) — the only manual gate where you confirm
    what the AI remembers and which rules it learns.
- **How it works** (`/how-it-works`) — a map of how knowledge and rules flow
  into a finished publication.

## AI configuration

AI generation runs through ProxyAPI. Create a `.env` file in the project root:

```env
PROXY_API_KEY=your_key
PROXY_API_BASE_URL=https://api.proxyapi.ru/openai/v1
AI_MODEL=gpt-4o-mini
AI_PREMIUM_MODEL=gpt-5.4-nano
```

`AI_MODEL` (default `gpt-4o-mini`) is used for most tasks: trend radar, text edits,
document parsing, brief refinements. `AI_PREMIUM_MODEL` is reserved for deep work only:
Daily Brief draft generation and full content-plan rebuild. If you still have a single
`AI_MODEL=gpt-5.4-nano`, the app auto-splits it into mini + premium on load.

Without these values the app still works and degrades gracefully: templates and
local analysis are used instead of live generation.

## Persistent memory (optional)

For access from any network and cross-device memory, deploy to hosting and
connect free persistent memory via Supabase:

```env
SUPABASE_URL=https://YOUR-PROJECT.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
```

See `docs/RENDER_DEPLOY.md`.

## CLI

```bash
export PYTHONPATH=src
python3 -m post_agent <command>
```

| Command | What it does |
| --- | --- |
| `serve [--host --port --lan]` | Run the web app. |
| `daily-brief` | Print today's Daily Brief summary to the terminal. |
| `ai-refresh` | Run the AI Pipeline once and save the result. |
| `ai-status` | Print the current AI Pipeline status. |
| `author-profile` | Print the Author Profile file path. |
| `production-check` | Check production readiness. |
| `export-daily-brief [--output]` | Rebuild the brief from seed sources and export HTML. |
| `rebuild-daily-brief [--output]` | Alias for `export-daily-brief`. |

## Data

- Seed sources and content plan live in `data/seeds/`.
- Runtime state (author brain, AI status/result, UI decisions) lives in `data/`.
- Do not commit runtime data from `data/` unless explicitly requested.

## Development

Run the test suite:

```bash
export PYTHONPATH=src
python3 -m pytest -q
```

Architecture notes:

- `src/post_agent/web.py` — routing and all server-rendered pages. Every page is
  built through the shared `_page_shell(...)` helper, so `<head>`, styles,
  topbar, and navigation live in one place.
- `src/post_agent/ai_context.py` — builds AI context; owns the canonical
  `_target_publication` selection used across the app.
- `src/post_agent/ai_pipeline.py`, `ai_gateway.py` — AI orchestration and the
  ProxyAPI client.
