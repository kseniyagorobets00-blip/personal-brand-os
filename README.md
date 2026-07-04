# Personal Brand OS

Personal AI Operator for expert brand development. The product currently delivers a working Daily Brief: an executive content briefing with market signals, topics, recommendations, ideas, drafts, and approval items.

## Quick start

```powershell
$env:PYTHONPATH = "src"
python -m post_agent serve
```

If Python is not on PATH in the Codex desktop runtime, use the bundled Python executable with the development runner:

```powershell
& "C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" tools\serve_daily_brief.py
```

Open:

```text
http://127.0.0.1:8000/daily-brief
```

To test from a phone or iPad on the same Wi-Fi network:

```powershell
$env:PYTHONPATH = "src"
python -m post_agent serve --lan
```

For access from any network or mobile internet, deploy the app to hosting and use a persistent data directory:

```env
PERSONAL_BRAND_OS_DATA_DIR=/var/data/personal-brand-os
```

See `docs/RENDER_DEPLOY.md`.

Author Profile:

```text
http://127.0.0.1:8000/author-profile
```

Or export a standalone browser page:

```powershell
$env:PYTHONPATH = "src"
python -m post_agent export-daily-brief
```

Open `build/daily-brief.html`.

Rebuild the Daily Brief from local seed sources:

```powershell
$env:PYTHONPATH = "src"
python -m post_agent rebuild-daily-brief
```

## What it does now

- shows a daily executive brief;
- builds the brief from local seed sources in `data/seeds/daily_brief_sources.json`;
- surfaces market signals and topics;
- recommends what to do next;
- stores idea candidates in the brief experience;
- prepares drafts for LinkedIn and Telegram;
- highlights decisions that require approval;
- shows refinement actions for topics and drafts: "Не заходит", "Дай другой угол", "Сделай сильнее", "Сделай мягче", "Перепиши в моем стиле".
- stores Author Profile separately in `data/seeds/author_profile.json`;
- uses Author Profile tone, structure, vocabulary, and platform rules when preparing drafts.

The previous post generator is still available:

```powershell
$env:PYTHONPATH = "src"
python -m post_agent generate "Why teams lose product focus"
```
