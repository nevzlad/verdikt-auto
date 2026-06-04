# VERDIKT-AUTO

AI-powered Telegram channel automation for news analytics. Uses a smart AI router that selects the best free LLM provider for each task (summarization, rewriting, SEO, headlines) with automatic fallback.

## Architecture

```
src/verdikt_auto/
├── core/          # Config, logging, AI router, models
├── scanner/       # Content sources (YouTube, Telegram, VK, RSS, Trends)
├── ranker/        # Topic prioritization
├── prompt/        # Prompt templates
├── generator/     # Content generation via AI router
├── parser/        # Post parsing & validation
├── formatter/     # Image, TTS, text formatting
├── seo/           # Headline optimization, A/B testing
├── publisher/     # Telegram publishing, scheduling
├── analytics/     # Metrics, chat analysis
├── growth/        # Content repurposing, gamification
├── monetization/  # Ads, affiliate, premium
├── dashboard/     # HTTP server & reports
└── anti_ban/      # Rate limiting, protection
```

## API Keys Setup

### AI Providers (free tiers)
| Provider | Sign Up | Free Tier |
|----------|---------|-----------|
| **OpenRouter** | https://openrouter.ai/keys | $1 free credit |
| **Groq** | https://console.groq.com/keys | 30 req/min free |
| **Together** | https://api.together.ai/settings/api-keys | $25 free credit |
| **Cerebras** | https://cloud.cerebras.ai/ | Free tier available |
| **Gemini** | https://aistudio.google.com/app/apikey | 60 req/min free |
| **DeepSeek** | https://platform.deepseek.com/api_keys | ¥5M tokens free |
| **Replicate** | https://replicate.com/account/api-tokens | Free tier with limits |

### Scanner APIs
| Service | Sign Up | Notes |
|---------|---------|-------|
| **YouTube Data API** | https://console.cloud.google.com/ | Enable YouTube Data API v3 |
| **VK API** | https://dev.vk.com/ | Create standalone app, get token |

### Telegram
1. Create bot via [@BotFather](https://t.me/BotFather) — get `TELEGRAM_BOT_TOKEN`
2. Create channel, add bot as admin — get `TELEGRAM_CHANNEL_ID`
3. Get your chat ID via [@userinfobot](https://t.me/userinfobot) — set `ADMIN_CHAT_ID`

## Quick Start

```bash
# Clone & enter
git clone <repo> && cd verdikt-auto

# Copy env & fill keys
cp .env.example .env

# Install
pip install -e ".[dev]"

# Run pipeline
verdikt-auto pipeline

# Run dashboard
verdikt-auto dashboard

# Run tests
pytest -v --cov=src/verdikt_auto
```

## Configuration

Edit `config.yaml` to control:
- `posts_per_day` — number of daily posts
- `schedule_hours` — posting times (24h format)
- `ai_routing.priority` — provider order per task type
- `topic_weights` — ranking formula coefficients
