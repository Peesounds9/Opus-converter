# 💱 Opus Currency Converter — Telegram Bot

A Telegram bot that converts between every world currency, with rates that
auto-refresh on a schedule. Powered by
[exchangerate-api.com](https://www.exchangerate-api.com) (with a no-key
fallback).

## Features

- **~170 currencies** — ISO 4217 active codes plus precious metals (XAU, XAG,
  XPT, XPD)
- **Auto-refresh** — rates update on a configurable interval (default: every
  hour) using the Telegram bot's job queue; falls back to a public endpoint
  if the API key is missing or fails
- **Offline cache** — keeps serving conversions even when the upstream API is
  flaky
- **Inline keyboards** — popular currencies plus paginated full list
- **Friendly UX** — currency names, symbols, and country flag emojis
- **Multiple input styles** — `/convert 100 USD EUR` or just `100 USD`

## Project layout

```
.
├── bot.py                # Telegram bot logic, handlers, keyboards
├── config.py             # Settings loaded from .env
├── rates.py              # Rate fetching, caching, conversion math
├── main.py               # Entry point (`python main.py`)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── Procfile              # For Heroku-style deploys
├── runtime.txt           # Python version pin
├── app.json              # One-click deploy manifest
├── .env.example
└── data/                 # Local rate cache (gitignored)
```

## Quick start

### 1. Get the secrets you need

- **Telegram bot token** — talk to [@BotFather](https://t.me/BotFather),
  create a bot, copy the token.
- **Exchange rate API key** *(optional)* — sign up free at
  [exchangerate-api.com](https://www.exchangerate-api.com). The bot still
  works without it via the public fallback endpoint, but the key gives you
  higher rate limits.

### 2. Configure

```bash
cp .env.example .env
# then edit .env with your tokens
```

Required env vars:

| Variable                | Required | Default | Notes |
|-------------------------|----------|---------|-------|
| `TELEGRAM_BOT_TOKEN`    | Yes      | —       | From @BotFather |
| `EXCHANGE_API_KEY`      | No       | (none)  | Free key for higher rate limits |
| `RATES_REFRESH_MINUTES` | No       | `60`    | How often to refresh rates |
| `BASE_CURRENCY`         | No       | `USD`   | ISO 4217 base for storage |

### 3. Run

**Local Python:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**Docker:**

```bash
docker compose up -d --build
```

**Heroku-style platforms:**

```bash
heroku create
heroku config:set TELEGRAM_BOT_TOKEN=... EXCHANGE_API_KEY=...
git push heroku main
```

**Railway:**

1. New Project → Deploy from GitHub → pick `Peesounds9/Opus-converter`.
2. Add environment variables (`TELEGRAM_BOT_TOKEN`, optional
   `EXCHANGE_API_KEY`, optional `RATES_REFRESH_MINUTES`, optional
   `BASE_CURRENCY`).
3. Railway auto-detects Python via Nixpacks; no extra config required.
   `railway.toml` is included as a fallback.
4. The bot starts a tiny stdlib HTTP healthcheck listener on `$PORT` (Railway
   sets this automatically). It answers `/`, `/health`, `/healthz`, and
   `/status` with a JSON snapshot of the rate cache. This keeps Railway
   happy and gives you a quick way to verify the deploy without opening
   Telegram.
5. Watch logs; you should see `Starting Opus currency bot...`,
   `Healthcheck server listening on 0.0.0.0:<port>`, and the initial rate
   fetch.

## Usage inside Telegram

Once the bot is running, send it `/start`.

| Command | What it does |
|---------|--------------|
| `/start` | Welcome + quick help |
| `/help`  | Detailed help |
| `/rates` | Show last refresh time and provider |
| `/list`  | All supported currencies (paginated) |
| `/convert 100 USD EUR` | Convert 100 USD to EUR |
| `100 USD` | Plain-text style; bot asks where to convert |

Tap a currency in the inline menu to convert, or `🌐 More...` to browse all
~170 codes.

## How the refresh works

The bot uses Telegram's built-in `JobQueue` to refresh rates every
`RATES_REFRESH_MINUTES`. It tries the authenticated endpoint first, then the
public fallback, then keeps serving from the on-disk cache if both fail —
so a transient API outage doesn't take the bot offline. Cache files live in
`data/rates.json` and `data/rates_meta.json` and are persisted across
restarts.

> **Railway / ephemeral hosts:** the local `data/` cache is wiped on
> redeploy. To keep it, add a Railway Volume mounted at `/app/data`. Otherwise
> the bot just re-fetches on first request, which is fine — it just costs
> one extra API call.

## Development notes

- Python 3.12+
- `python-telegram-bot` v21 (async)
- No database needed — per-user state is held in memory for the duration of
  a pending conversion (10 minutes)
- Add a new currency by appending to `rates.py:CURRENCY_NAMES` (and
  optionally `CURRENCY_SYMBOLS` / `flag_emoji`)

## License

MIT
