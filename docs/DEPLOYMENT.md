# Deployment Guide

## Prerequisites

- Python 3.11+
- Telegram Bot Token (from @BotFather)
- At least one AI provider API key (OpenRouter, Groq, etc.)

## Option 1: GitHub Actions (Recommended)

1. Fork/clone the repository to GitHub.

2. Add repository secrets (`Settings → Secrets and variables → Actions`):

   | Secret | Description |
   |--------|-------------|
   | `BOT_TOKEN` | Telegram bot token from @BotFather |
   | `CHANNEL_ID` | Target channel ID (`@username` or numeric ID) |
   | `OPENROUTER_API_KEY` | OpenRouter API key |
   | `GROQ_API_KEY` | Groq API key (optional) |

3. The `daily_pipeline.yml` workflow runs automatically at 06:00 MSK.
   The `weekly_digest.yml` runs every Sunday at 20:00 MSK.

4. To trigger manually: `Actions → VERDIKT Daily Pipeline → Run workflow`.

## Option 2: VPS / Dedicated Server

### Installation

```bash
# System dependencies
sudo apt update && sudo apt install -y python3.11 python3.11-venv ffmpeg git

# Clone
git clone https://github.com/your-org/verdikt-auto.git
cd verdikt-auto
```

### Environment

```bash
cp .env.example .env
nano .env   # fill in BOT_TOKEN, CHANNEL_ID, API keys
```

### Install

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### Run

```bash
# Full pipeline
python -m verdikt_auto.pipeline

# Or component by component:
python -m verdikt_auto.scanner --run
python -m verdikt_auto.generator --generate 7
python -m verdikt_auto.publisher --publish

# Dashboard (background):
python -m verdikt_auto.dashboard &
```

### systemd Service

```ini
[Unit]
Description=VERDIKT-AUTO Pipeline
After=network.target

[Service]
Type=simple
User=verdikt
WorkingDirectory=/opt/verdikt-auto
ExecStart=/opt/verdikt-auto/venv/bin/python -m verdikt_auto.pipeline
Restart=on-failure
RestartSec=30
EnvironmentFile=/opt/verdikt-auto/.env

[Install]
WantedBy=multi-user.target
```

## Option 3: Docker

```bash
# Build
docker compose build

# Run
docker compose up -d

# Logs
docker compose logs -f

# Stop
docker compose down
```

## Configuration

All settings via `.env` file:

```ini
BOT_TOKEN=123456:ABC-DEF1234...
CHANNEL_ID=@channel_username
OPENROUTER_API_KEY=sk-or-...
GROQ_API_KEY=gsk_...
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=secure_password_here
TZ=Europe/Moscow
```

## Monitoring

- Dashboard: `http://your-server:5000` (Basic Auth)
- Health check: `GET /api/health` → `{"status": "ok"}`
- GitHub Actions: Check workflow run logs

## Backup

```bash
# SQLite databases
tar -czf backup-$(date +%Y%m%d).tar.gz data/
```
