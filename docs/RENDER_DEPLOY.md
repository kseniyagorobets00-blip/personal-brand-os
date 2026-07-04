# Render Free Deploy

This project can be deployed as a free Render Web Service for testing.

## 1. GitHub

1. Create a GitHub repository.
2. Commit the project files.
3. Make sure `.env` is not committed. It is ignored by `.gitignore`.
4. Push the repository to GitHub.

## 2. Render Service

1. Open Render.
2. Create a new **Web Service**.
3. Connect your GitHub repository.
4. Choose the free instance type.
5. Use the commands below.

## Build Command

```bash
pip install -r requirements.txt
```

## Start Command

```bash
python -m post_agent serve --host 0.0.0.0 --port $PORT
```

The app also reads `PORT` from the environment, but passing it explicitly keeps the Render command clear.

## Environment Variables

Add these in Render dashboard:

```env
APP_ENV=production
PERSONAL_BRAND_OS_DATA_DIR=/var/data/personal-brand-os
PROXY_API_KEY=your_proxyapi_key
PROXY_API_BASE_URL=https://api.proxyapi.ru/openai/v1
AI_MODEL=gpt-5.4-nano
TREND_RADAR_CACHE_TTL_MINUTES=30
```

Do not add `.env` to GitHub.

## Persistent Memory

For real use from phone, iPad, and any network, connect a persistent disk to the Render service and mount it at:

```text
/var/data
```

The app will store memory, uploaded documents, content plan changes, author profile settings, ideas, rules, and AI cache under:

```text
/var/data/personal-brand-os
```

On first start, the app copies the default `data/` files from the repository into this persistent folder without overwriting existing files. After that, user changes are written to the persistent folder, not to the temporary app filesystem.

## Open From iPhone / iPad

After deploy, Render gives you a public HTTPS URL like:

```text
https://your-service-name.onrender.com
```

Open this URL in Safari or Chrome on iPhone/iPad:

```text
https://your-service-name.onrender.com/daily-brief
```

Useful pages:

```text
/daily-brief
/trend-radar
/content-plan
/knowledge
/learning
/ideas
/author-profile
/writing-dna
```

## Render Free Limitation

Render Free can sleep after inactivity, so the first request after a pause may be slow.

Render Free storage can be temporary and is not reliable for long-term memory. For real memory, use a paid service with persistent disk or move storage to an external database/object storage later.
