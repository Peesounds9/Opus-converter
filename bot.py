"""Telegram bot for currency conversion.

Conversation flow (kept simple so it works in any Telegram client):
  1. User sends `/convert <amount> <FROM> [to]` or just a numeric message.
  2. If `to` is missing, the bot asks for it via a one-tap inline menu of
     popular currencies plus a "More..." button that paginates through the
     full list.
  3. Conversions use the latest cached rates, refreshed periodically by the
     background scheduler and on /rates.

Supported commands:
  /start     Welcome message + quick help
  /help      Detailed usage
  /list      List all supported currencies (paginated)
  /rates     Show last refresh time / provider
  /convert   Perform a conversion: /convert 100 USD EUR
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import SETTINGS
import rates as rates_mod

log = logging.getLogger(__name__)


# Numbered group helpers -----------------------------------------------------

# Match integers and decimals with optional thousands separators and a sign.
NUM_RE = re.compile(r"^-?\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")


def parse_amount(text: str) -> float | None:
    cleaned = text.strip().replace(",", "").replace("\u00a0", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_amount(text: str) -> tuple[float | None, str]:
    """Pull the first number out of `text`. Returns (number, remaining_text)."""
    tokens = text.strip().split()
    if not tokens:
        return None, ""
    if NUM_RE.match(tokens[0]):
        amount = parse_amount(tokens[0])
        return amount, " ".join(tokens[1:])
    # Bare number like "100"
    return None, text.strip()


# Per-user session state -----------------------------------------------------

@dataclass
class PendingState:
    amount: float
    from_ccy: str
    requested_at: float

    def age_seconds(self) -> float:
        return time.time() - self.requested_at


# Simple in-memory store keyed by user id. Fine for single-instance bots;
# state is short-lived (a few minutes) so process restarts are not painful.
PENDING: dict[int, PendingState] = {}


# Keyboards ------------------------------------------------------------------

POPULAR = ["USD", "EUR", "GBP", "JPY", "CNY", "CHF", "CAD", "AUD",
           "INR", "BRL", "ZAR", "NGN", "TRY", "MXN", "RUB", "KRW",
           "SGD", "HKD", "NZD", "SEK"]

PAGE_SIZE = 8


def popular_keyboard(amount: float, from_ccy: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code in POPULAR:
        if code == from_ccy.upper():
            continue
        flag = rates_mod.flag_emoji(code) or ""
        label = f"{flag} {code}".strip()
        row.append(InlineKeyboardButton(label, callback_data=f"pick:{code}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🌐 More...", callback_data="more:0")])
    return InlineKeyboardMarkup(rows)


def page_keyboard(amount: float, from_ccy: str, page: int,
                  total_pages: int) -> InlineKeyboardMarkup:
    supported = [c for c in rates_mod.list_supported() if c != from_ccy.upper()]
    start = page * PAGE_SIZE
    chunk = supported[start:start + PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code in chunk:
        flag = rates_mod.flag_emoji(code) or ""
        label = f"{flag} {code}".strip()
        row.append(InlineKeyboardButton(label, callback_data=f"pick:{code}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"more:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"more:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Back to popular", callback_data="back:popular")])
    return InlineKeyboardMarkup(rows)


# Conversion result formatting ----------------------------------------------

def format_result(amount: float, from_ccy: str, to_ccy: str,
                  result: float, fetched_at: float | None) -> str:
    age = ""
    if fetched_at:
        minutes = max(0, int((time.time() - fetched_at) / 60))
        if minutes < 1:
            age = "just now"
        elif minutes < 60:
            age = f"{minutes} min ago"
        elif minutes < 60 * 24:
            age = f"{minutes // 60}h {minutes % 60}m ago"
        else:
            age = f"{minutes // 1440}d ago"

    sym_from = rates_mod.symbol_of(from_ccy)
    sym_to = rates_mod.symbol_of(to_ccy)
    name_from = rates_mod.name_of(from_ccy)
    name_to = rates_mod.name_of(to_ccy)
    rate = result / amount if amount else 0
    return (
        f"💱 *Currency Conversion*\n\n"
        f"`{amount:,.2f} {from_ccy}`  →  "
        f"`{result:,.4f} {to_ccy}`\n\n"
        f"_{name_from}_  →  _{name_to}_\n"
        f"Rate: 1 {from_ccy} = `{rate:,.6f} {to_ccy}`\n"
        f"🕒 Rates last updated: *{age or 'never'}*"
    )


def perform_conversion(amount: float, from_ccy: str, to_ccy: str) -> tuple[str, bool]:
    rate_map, meta = rates_mod.load_cached_rates()
    if not rate_map:
        # Try a one-shot fetch in case the scheduler hasn't run yet.
        try:
            rate_map = rates_mod.refresh_rates(force=True)
            meta = rates_mod._read_json(SETTINGS.meta_file, {})
        except Exception as exc:  # noqa: BLE001
            return f"⚠️ Couldn't fetch rates: {exc}", False

    converted = rates_mod.convert(amount, from_ccy, to_ccy, rate_map)
    if converted is None:
        return (
            f"❌ Unsupported currency `{from_ccy}` or `{to_ccy}`.\n"
            f"Try /list to see all supported codes."
        ), False

    fetched_at = float(meta.get("fetched_at") or 0) or None
    return format_result(amount, from_ccy.upper(), to_ccy.upper(),
                         converted, fetched_at), True


# Handlers -------------------------------------------------------------------

HELP_TEXT = (
    "💱 *Opus Currency Converter*\n\n"
    "*How to convert:*\n"
    "• `/convert 100 USD EUR` — quick conversion\n"
    "• Send just a number, e.g. `100 USD` — bot asks where to convert\n"
    "• Tap a currency in the inline menu\n\n"
    "*Commands:*\n"
    "/start — Welcome\n"
    "/help — This help\n"
    "/list — All supported currencies\n"
    "/rates — Show last refresh\n"
    "/convert \\<amount> \\<FROM> \\[to] — Convert\n\n"
    "Rates auto-refresh every "
    f"{SETTINGS.refresh_minutes} minutes and come from "
    "exchangerate-api.com."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"👋 Hey! {HELP_TEXT}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_rates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, meta = rates_mod.load_cached_rates()
    if not meta:
        try:
            rates_mod.refresh_rates(force=True)
            _, meta = rates_mod.load_cached_rates()
        except Exception as exc:  # noqa: BLE001
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return

    fetched = float(meta.get("fetched_at", 0))
    age = ""
    if fetched:
        delta = int((time.time() - fetched) / 60)
        age = f"{delta} min ago" if delta < 60 else f"{delta // 60}h {delta % 60}m ago"
    text = (
        f"📊 *Rate cache status*\n\n"
        f"Base currency: `{meta.get('base', SETTINGS.base_currency)}`\n"
        f"Provider: `{meta.get('provider', 'unknown')}`\n"
        f"Currencies tracked: *{meta.get('count', '?')}*\n"
        f"Last refresh: *{age or 'just now'}*"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    codes = rates_mod.list_supported()
    page = 0
    if context.args and context.args[0].isdigit():
        page = max(0, min(int(context.args[0]), len(codes) // PAGE_SIZE))
    total = (len(codes) + PAGE_SIZE - 1) // PAGE_SIZE

    start = page * PAGE_SIZE
    chunk = codes[start:start + PAGE_SIZE]
    lines = [f"`{c}` — {rates_mod.name_of(c)}" for c in chunk]
    await update.effective_message.reply_text(
        "🌍 *Supported currencies* (page {}/{}):\n\n{}".format(page + 1, total, "\n".join(lines)),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: `/convert <amount> <FROM> [to]` — e.g. `/convert 100 USD EUR`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    amount = parse_amount(context.args[0])
    from_ccy = context.args[1].upper()
    if amount is None or len(from_ccy) != 3 or not from_ccy.isalpha():
        await update.effective_message.reply_text(
            "❌ Format: `/convert <amount> <FROM> [to]`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    user_id = update.effective_user.id
    PENDING[user_id] = PendingState(amount=amount, from_ccy=from_ccy, requested_at=time.time())

    to_ccy = context.args[2].upper() if len(context.args) >= 3 else None
    if to_ccy:
        text, _ = perform_conversion(amount, from_ccy, to_ccy)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        PENDING.pop(user_id, None)
    else:
        await update.effective_message.reply_text(
            f"💱 Convert *{amount:,.2f} {from_ccy}* to which currency?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=popular_keyboard(amount, from_ccy),
        )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return

    amount, rest = extract_amount(text)
    if amount is None:
        await update.effective_message.reply_text(
            "Send a number with a currency, e.g. `100 USD` or `/convert 100 USD EUR`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    parts = rest.split()
    from_ccy = parts[0].upper() if parts else SETTINGS.base_currency
    if len(from_ccy) != 3 or not from_ccy.isalpha():
        # Treat the first token as currency even if it's not 3 letters
        from_ccy = SETTINGS.base_currency

    user_id = update.effective_user.id
    PENDING[user_id] = PendingState(amount=amount, from_ccy=from_ccy, requested_at=time.time())

    to_ccy = parts[1].upper() if len(parts) >= 2 else None
    if to_ccy and len(to_ccy) == 3 and to_ccy.isalpha():
        text_out, _ = perform_conversion(amount, from_ccy, to_ccy)
        await update.effective_message.reply_text(text_out, parse_mode=ParseMode.MARKDOWN)
        PENDING.pop(user_id, None)
    else:
        await update.effective_message.reply_text(
            f"💱 Convert *{amount:,.2f} {from_ccy}* to which currency?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=popular_keyboard(amount, from_ccy),
        )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id

    if data == "noop":
        return

    if data.startswith("more:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        state = PENDING.get(user_id)
        if not state or state.age_seconds() > 600:
            await query.edit_message_text("⏳ That request expired — please resend the amount.")
            return
        supported = [c for c in rates_mod.list_supported() if c != state.from_ccy.upper()]
        total = (len(supported) + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total - 1))
        await query.edit_message_reply_markup(
            page_keyboard(state.amount, state.from_ccy, page, total)
        )
        return

    if data == "back:popular":
        state = PENDING.get(user_id)
        if not state:
            await query.edit_message_text("⏳ That request expired — please resend the amount.")
            return
        await query.edit_message_reply_markup(
            popular_keyboard(state.amount, state.from_ccy)
        )
        return

    if data.startswith("pick:"):
        to_ccy = data.split(":", 1)[1].upper()
        state = PENDING.get(user_id)
        if not state:
            await query.edit_message_text("⏳ That request expired — please resend the amount.")
            return
        text_out, _ = perform_conversion(state.amount, state.from_ccy, to_ccy)
        PENDING.pop(user_id, None)
        await query.edit_message_text(text_out, parse_mode=ParseMode.MARKDOWN)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Telegram error: %s", context.error)


# Bot bootstrap --------------------------------------------------------------

def build_application() -> Application:
    if not SETTINGS.has_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and fill it in."
        )
    app = ApplicationBuilder().token(SETTINGS.telegram_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("rates", cmd_rates))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("convert", cmd_convert))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(error_handler)
    return app


async def post_init(app: Application) -> None:
    """Refresh rates on startup so the first /convert isn't slow."""
    try:
        rates_mod.refresh_rates(force=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("Initial rate fetch failed: %s", exc)


def install_scheduler(app: Application) -> None:
    """Periodic refresh using the job queue (no extra dependency)."""
    job_queue = app.job_queue
    interval_seconds = max(60, SETTINGS.refresh_minutes * 60)

    async def _refresh_job(_: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            rates_mod.refresh_rates()
        except Exception as exc:  # noqa: BLE001
            log.warning("Scheduled refresh failed: %s", exc)

    job_queue.run_repeating(_refresh_job, interval=interval_seconds, first=interval_seconds,
                            name="rates-refresh")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("Starting Opus currency bot. Refresh every %d min.", SETTINGS.refresh_minutes)
    app = build_application()
    install_scheduler(app)
    app.post_init = post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
