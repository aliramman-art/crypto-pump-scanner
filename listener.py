# ============================================================
# KRAKEN FUTURES ICHIMOKU TELEGRAM LIVE LISTENER v1.0
# ============================================================
#
# PURPOSE:
#   Real-time Telegram command listener for scanner.py
#
# COMMANDS:
#   /check ETH
#   /check BTC
#   /check SOL
#   /check BTC ETH SOL XRP
#   /check@BotName ETH
#
# IMPORTANT:
#   - Does NOT open trades
#   - Does NOT modify ichimoku_state.json
#   - Does NOT run TOP 100 scan
#   - Uses the exact analysis functions from scanner.py
#   - BTC -> PF_XBTUSD
#   - XBT -> PF_XBTUSD
#
# FILE STRUCTURE:
#
#   scanner.py
#   listener.py
#
# RUN:
#   python listener.py
#
# ============================================================

import os
import sys
import time
import json
import traceback
import requests

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

LISTENER_VERSION = "v1.0"

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE_URL
    + "/derivatives/api/v3/instruments"
)

TELEGRAM_API = (
    "https://api.telegram.org/bot"
)

TELEGRAM_POLL_TIMEOUT = 20

TELEGRAM_HTTP_TIMEOUT = 30

TELEGRAM_RETRIES = 3

TELEGRAM_RETRY_SLEEP = 2

TELEGRAM_MAX_MESSAGE_LENGTH = 4000

RECONNECT_SLEEP = 5

MAX_SYMBOLS_PER_COMMAND = 5


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Kraken-Ichi-Live-Listener/1.0"
})


# ============================================================
# TELEGRAM TOKEN
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# OFFSET FILE
# ============================================================

OFFSET_FILE = (
    "telegram_listener_offset.json"
)


# ============================================================
# TIME
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def now_iso():

    return now_utc().isoformat()


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)

    except Exception:
        return default


# ============================================================
# JSON LOAD
# ============================================================

def load_json(
    path,
    default=None
):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[WARN] "
            f"Cannot load {path}: {e}"
        )

        return default


# ============================================================
# JSON SAVE
# ============================================================

def save_json(
    path,
    data
):

    temp = path + ".tmp"

    with open(
        temp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp,
        path
    )


# ============================================================
# OFFSET
# ============================================================

def load_offset():

    data = load_json(
        OFFSET_FILE,
        {}
    )

    if not isinstance(
        data,
        dict
    ):
        return 0

    return int(
        safe_float(
            data.get(
                "offset",
                0
            )
        )
    )


def save_offset(
    offset
):

    save_json(
        OFFSET_FILE,
        {
            "offset": int(offset),
            "updated_at": now_iso(),
        }
    )

    print(
        f"[OFFSET] Saved "
        f"{int(offset)}"
    )


# ============================================================
# TELEGRAM API URL
# ============================================================

def telegram_url(
    method
):

    return (
        TELEGRAM_API
        + TELEGRAM_BOT_TOKEN
        + "/"
        + method
    )


# ============================================================
# TELEGRAM GET ME
# ============================================================

def telegram_get_me():

    if not TELEGRAM_BOT_TOKEN:
        return None

    try:

        response = SESSION.get(
            telegram_url("getMe"),
            timeout=TELEGRAM_HTTP_TIMEOUT
        )

        data = response.json()

        if data.get("ok"):

            return data.get(
                "result",
                {}
            )

        print(
            "[TELEGRAM ERROR] "
            f"getMe: {data}"
        )

    except Exception as e:

        print(
            "[TELEGRAM ERROR] "
            f"getMe exception: {e}"
        )

    return None


# ============================================================
# WEBHOOK INFO
# ============================================================

def telegram_get_webhook_info():

    try:

        response = SESSION.get(
            telegram_url(
                "getWebhookInfo"
            ),
            timeout=TELEGRAM_HTTP_TIMEOUT
        )

        data = response.json()

        if data.get("ok"):

            return data.get(
                "result",
                {}
            )

        print(
            "[TELEGRAM ERROR] "
            f"getWebhookInfo: {data}"
        )

    except Exception as e:

        print(
            "[TELEGRAM ERROR] "
            f"Webhook check: {e}"
        )

    return None


# ============================================================
# REMOVE WEBHOOK
# ============================================================

def telegram_delete_webhook():

    try:

        response = SESSION.get(
            telegram_url(
                "deleteWebhook"
            ),
            params={
                "drop_pending_updates": False
            },
            timeout=TELEGRAM_HTTP_TIMEOUT
        )

        data = response.json()

        if data.get("ok"):

            print(
                "[TELEGRAM] "
                "Webhook removed."
            )

            return True

        print(
            "[TELEGRAM ERROR] "
            f"deleteWebhook: {data}"
        )

    except Exception as e:

        print(
            "[TELEGRAM ERROR] "
            f"deleteWebhook: {e}"
        )

    return False


# ============================================================
# TELEGRAM SEND
# ============================================================

def split_message(
    message,
    max_length=TELEGRAM_MAX_MESSAGE_LENGTH
):

    if not message:
        return []

    if len(message) <= max_length:
        return [message]

    chunks = []

    remaining = message

    while len(remaining) > max_length:

        cut = remaining.rfind(
            "\n",
            0,
            max_length
        )

        if cut <= 0:
            cut = max_length

        chunks.append(
            remaining[:cut]
        )

        remaining = remaining[cut:]

        remaining = remaining.lstrip(
            "\n"
        )

    if remaining:
        chunks.append(
            remaining
        )

    return chunks


def send_telegram(
    message,
    chat_id=None
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN missing."
        )

        return False

    target_chat = (
        chat_id
        or TELEGRAM_CHAT_ID
    )

    if not target_chat:

        print(
            "[TELEGRAM ERROR] "
            "Chat ID missing."
        )

        return False

    chunks = split_message(
        message
    )

    success = True

    for chunk in chunks:

        sent = False

        for attempt in range(
            1,
            TELEGRAM_RETRIES + 1
        ):

            try:

                response = SESSION.post(
                    telegram_url(
                        "sendMessage"
                    ),
                    json={
                        "chat_id": target_chat,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                    timeout=TELEGRAM_HTTP_TIMEOUT
                )

                try:

                    data = response.json()

                except Exception:

                    data = {
                        "ok": False,
                        "description":
                            response.text
                    }

                if (
                    response.ok
                    and
                    data.get("ok") is True
                ):

                    sent = True

                    break

                print(
                    "[TELEGRAM ERROR] "
                    f"{data}"
                )

                if (
                    data.get("error_code")
                    == 429
                ):

                    retry_after = safe_float(
                        data.get(
                            "parameters",
                            {}
                        ).get(
                            "retry_after",
                            3
                        ),
                        3
                    )

                    time.sleep(
                        retry_after
                    )

                elif attempt < TELEGRAM_RETRIES:

                    time.sleep(
                        TELEGRAM_RETRY_SLEEP
                    )

            except Exception as e:

                print(
                    "[TELEGRAM ERROR] "
                    f"sendMessage: {e}"
                )

                if attempt < TELEGRAM_RETRIES:

                    time.sleep(
                        TELEGRAM_RETRY_SLEEP
                    )

        if not sent:

            success = False

    return success


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def get_updates():

    offset = load_offset()

    print(
        f"[TELEGRAM] Polling "
        f"offset={offset}"
    )

    try:

        response = SESSION.get(
            telegram_url(
                "getUpdates"
            ),
            params={
                "offset": offset,
                "timeout":
                    TELEGRAM_POLL_TIMEOUT,
                "allowed_updates":
                    json.dumps(
                        ["message"]
                    ),
            },
            timeout=(
                TELEGRAM_POLL_TIMEOUT
                + 10
            )
        )

        try:

            data = response.json()

        except Exception:

            print(
                "[TELEGRAM ERROR] "
                "Invalid JSON response."
            )

            return []

        if not data.get("ok"):

            error_code = data.get(
                "error_code"
            )

            if error_code == 409:

                print(
                    "================================================"
                )

                print(
                    "[TELEGRAM ERROR] "
                    "409 CONFLICT"
                )

                print(
                    "Another Telegram listener "
                    "is already polling this bot."
                )

                print(
                    "Stop the other listener "
                    "before starting this one."
                )

                print(
                    "================================================"
                )

            else:

                print(
                    "[TELEGRAM ERROR] "
                    f"getUpdates: {data}"
                )

            return []

        updates = data.get(
            "result",
            []
        )

        if updates:

            print(
                f"[TELEGRAM] "
                f"{len(updates)} update(s)"
            )

        return updates

    except requests.exceptions.Timeout:

        return []

    except Exception as e:

        print(
            "[TELEGRAM ERROR] "
            f"getUpdates: {e}"
        )

        return []


# ============================================================
# SYMBOL NORMALIZATION
# ============================================================

def normalize_symbol(
    raw
):

    q = str(
        raw or ""
    ).strip().upper()

    q = (
        q.replace(
            "$",
            ""
        )
        .replace(
            ",",
            ""
        )
        .strip()
    )

    if not q:
        return ""

    # --------------------------------------------------------
    # BTC / XBT
    # --------------------------------------------------------

    if q in (
        "BTC",
        "XBT",
        "BTCUSD",
        "XBTUSD",
        "PF_BTCUSD",
        "PF_XBTUSD",
    ):

        return "PF_XBTUSD"

    # --------------------------------------------------------
    # Already Kraken futures symbol
    # --------------------------------------------------------

    if q.startswith("PF_"):

        return q

    # --------------------------------------------------------
    # USD suffix
    # --------------------------------------------------------

    if q.endswith("USD"):

        return "PF_" + q

    # --------------------------------------------------------
    # Normal coin
    # --------------------------------------------------------

    return (
        "PF_"
        + q
        + "USD"
    )


# ============================================================
# DISPLAY SYMBOL
# ============================================================

def display_symbol(
    symbol
):

    symbol = str(
        symbol or ""
    ).upper()

    if symbol == "PF_XBTUSD":
        return "BTC"

    if symbol.startswith("PF_"):

        value = symbol[3:]

        if value.endswith("USD"):

            value = value[:-3]

        return value

    return symbol


# ============================================================
# FETCH INSTRUMENTS
# ============================================================

def get_instruments():

    try:

        response = SESSION.get(
            INSTRUMENTS_URL,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(
            data,
            dict
        ):

            return (
                data.get(
                    "instruments"
                )
                or data.get(
                    "data"
                )
                or []
            )

        if isinstance(
            data,
            list
        ):

            return data

    except Exception as e:

        print(
            "[KRAKEN ERROR] "
            f"Instruments: {e}"
        )

    return []


# ============================================================
# FIND MARKET
# ============================================================

def find_market(
    raw_symbol
):

    normalized = normalize_symbol(
        raw_symbol
    )

    if not normalized:
        return None

    instruments = get_instruments()

    for item in instruments:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = str(
            item.get("symbol")
            or item.get("instrument")
            or ""
        )

        if (
            symbol.upper()
            != normalized.upper()
        ):
            continue

        if not symbol.upper().startswith(
            "PF_"
        ):
            continue

        return {
            "symbol": symbol,
            "tick_size": safe_float(
                item.get("tickSize")
                or item.get("tick_size")
                or item.get(
                    "priceIncrement"
                ),
                0.0
            ),
            "volume": 0.0,
            "spread_pct": 0.0,
        }

    return None


# ============================================================
# IMPORT SCANNER
# ============================================================

def import_scanner():

    try:

        import scanner

        print(
            "[SCANNER] "
            "scanner.py imported successfully."
        )

        return scanner

    except Exception as e:

        print(
            "================================================"
        )

        print(
            "[FATAL] Cannot import scanner.py"
        )

        print(
            f"Reason: {e}"
        )

        print(
            "Make sure listener.py and scanner.py "
            "are in the same directory."
        )

        print(
            "================================================"
        )

        traceback.print_exc()

        return None


# ============================================================
# CHECK USING SCANNER
# ============================================================

def check_symbol(
    scanner,
    market
):

    # --------------------------------------------------------
    # IMPORTANT:
    # These functions come directly from scanner.py
    # --------------------------------------------------------

    return scanner.check_symbol(
        market
    )


# ============================================================
# GENERATE CHECK MESSAGE
# ============================================================

def generate_check_message(
    scanner,
    result
):

    return scanner.generate_check_message(
        result
    )


# ============================================================
# COMMAND PARSER
# ============================================================

def parse_command(
    text,
    bot_username=""
):

    if not text:
        return []

    text = text.strip()

    parts = text.split()

    if not parts:
        return []

    command = parts[0].strip()

    command_lower = command.lower()

    # --------------------------------------------------------
    # /check
    # --------------------------------------------------------

    valid = False

    if command_lower == "/check":

        valid = True

    # --------------------------------------------------------
    # /check@BotName
    # --------------------------------------------------------

    elif command_lower.startswith(
        "/check@"
    ):

        if bot_username:

            expected = (
                "/check@"
                + bot_username.lower()
            )

            if command_lower == expected:

                valid = True

        else:

            # If username is unavailable,
            # accept Telegram-style /check@...
            valid = True

    if not valid:

        return []

    symbols = []

    for raw in parts[1:]:

        cleaned = (
            raw
            .replace(
                ",",
                ""
            )
            .replace(
                "$",
                ""
            )
            .strip()
        )

        if cleaned:

            symbols.append(
                cleaned
            )

    return symbols[
        :MAX_SYMBOLS_PER_COMMAND
    ]


# ============================================================
# HELP
# ============================================================

def help_message():

    return (
        "🤖 KRAKEN ICHIMOKU LIVE CHECK\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Commands:\n\n"
        "/check ETH\n"
        "/check BTC\n"
        "/check SOL\n"
        "/check BTC ETH SOL XRP\n\n"
        "BTC is automatically mapped to "
        "Kraken XBT.\n\n"
        "ℹ️ Analysis only.\n"
        "No trade will be opened."
    )


# ============================================================
# PROCESS ONE UPDATE
# ============================================================

def process_update(
    scanner,
    update,
    bot_username
):

    update_id = update.get(
        "update_id"
    )

    try:

        message = update.get(
            "message"
        )

        if not isinstance(
            message,
            dict
        ):

            if update_id is not None:

                save_offset(
                    int(update_id) + 1
                )

            return

        chat = message.get(
            "chat",
            {}
        )

        chat_id = chat.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        )

        username = (
            message.get(
                "from",
                {}
            ).get(
                "username",
                ""
            )
        )

        print(
            "------------------------------------------------"
        )

        print(
            "[MESSAGE]"
        )

        print(
            f"Chat ID: {chat_id}"
        )

        print(
            f"User: @{username}"
        )

        print(
            f"Text: {text!r}"
        )

        # ----------------------------------------------------
        # Security
        # ----------------------------------------------------

        if (
            TELEGRAM_CHAT_ID
            and
            str(chat_id)
            != str(TELEGRAM_CHAT_ID)
        ):

            print(
                f"[SECURITY] "
                f"Unauthorized chat: {chat_id}"
            )

            # Advance offset so the message
            # is not processed forever.

            if update_id is not None:

                save_offset(
                    int(update_id) + 1
                )

            return

        # ----------------------------------------------------
        # Help
        # ----------------------------------------------------

        text_lower = (
            text.strip().lower()
            if text
            else ""
        )

        if text_lower in (
            "/start",
            "/help",
        ):

            send_telegram(
                help_message(),
                chat_id=chat_id
            )

            if update_id is not None:

                save_offset(
                    int(update_id) + 1
                )

            return

        # ----------------------------------------------------
        # Parse /check
        # ----------------------------------------------------

        symbols = parse_command(
            text,
            bot_username
        )

        if not symbols:

            if update_id is not None:

                save_offset(
                    int(update_id) + 1
                )

            return

        # ----------------------------------------------------
        # Immediate acknowledgement
        # ----------------------------------------------------

        display_names = []

        for raw in symbols:

            normalized = normalize_symbol(
                raw
            )

            if normalized:

                display_names.append(
                    display_symbol(
                        normalized
                    )
                )

        send_telegram(
            "⏳ LIVE CHECK RECEIVED\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🪙 "
            f"{', '.join(display_names)}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔄 Reading Kraken data...\n"
            "📊 Running MTF Ichimoku...\n"
            "Please wait...",
            chat_id=chat_id
        )

        # ----------------------------------------------------
        # Analyze every symbol
        # ----------------------------------------------------

        for raw_symbol in symbols:

            try:

                normalized = normalize_symbol(
                    raw_symbol
                )

                if not normalized:

                    send_telegram(
                        "❌ INVALID SYMBOL\n"
                        f"{raw_symbol}",
                        chat_id=chat_id
                    )

                    continue

                print(
                    f"[CHECK] "
                    f"{raw_symbol.upper()} "
                    f"-> "
                    f"{normalized}"
                )

                market = find_market(
                    normalized
                )

                if not market:

                    send_telegram(
                        "❌ CHECK ERROR\n"
                        f"🪙 "
                        f"{raw_symbol.upper()}\n"
                        "Kraken USD perpetual "
                        "market not found.",
                        chat_id=chat_id
                    )

                    continue

                print(
                    f"[CHECK] "
                    f"Analyzing "
                    f"{market['symbol']}"
                )

                # ------------------------------------------------
                # THIS CALL DOES NOT OPEN A TRADE.
                # It only performs check_symbol().
                # ------------------------------------------------

                result = check_symbol(
                    scanner,
                    market
                )

                message_text = (
                    generate_check_message(
                        scanner,
                        result
                    )
                )

                send_telegram(
                    message_text,
                    chat_id=chat_id
                )

                print(
                    f"[CHECK] "
                    f"{display_symbol(market['symbol'])} "
                    f"completed."
                )

            except Exception as e:

                print(
                    "[CHECK ERROR] "
                    f"{raw_symbol}: {e}"
                )

                traceback.print_exc()

                send_telegram(
                    "❌ CHECK ERROR\n"
                    f"🪙 "
                    f"{raw_symbol.upper()}\n"
                    f"Reason: {e}",
                    chat_id=chat_id
                )

        # ----------------------------------------------------
        # Save offset
        # ----------------------------------------------------

        if update_id is not None:

            save_offset(
                int(update_id) + 1
            )

        print(
            "[MESSAGE] Processing complete."
        )

    except Exception as e:

        print(
            "[UPDATE ERROR] "
            f"{e}"
        )

        traceback.print_exc()

        if update_id is not None:

            save_offset(
                int(update_id) + 1
            )


# ============================================================
# STARTUP CHECK
# ============================================================

def startup():

    print(
        "============================================================"
    )

    print(
        f"KRAKEN FUTURES "
        f"ICHIMOKU LIVE LISTENER "
        f"{LISTENER_VERSION}"
    )

    print(
        "============================================================"
    )

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[FATAL] "
            "TELEGRAM_BOT_TOKEN is missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] "
            "TELEGRAM_CHAT_ID is not set."
        )

        print(
            "[WARN] "
            "Listener will accept messages "
            "from any chat."
        )

    bot = telegram_get_me()

    if not bot:

        print(
            "[FATAL] "
            "Telegram bot authentication failed."
        )

        return False

    bot_username = (
        bot.get(
            "username",
            ""
        )
    )

    bot_name = (
        bot.get(
            "first_name",
            ""
        )
    )

    print(
        f"[TELEGRAM] "
        f"Bot: {bot_name} "
        f"@{bot_username}"
    )

    # --------------------------------------------------------
    # Webhook check
    # --------------------------------------------------------

    webhook = telegram_get_webhook_info()

    if webhook:

        webhook_url = webhook.get(
            "url",
            ""
        )

        if webhook_url:

            print(
                "================================================"
            )

            print(
                "[WARNING] "
                "Telegram webhook is active."
            )

            print(
                f"[WEBHOOK] "
                f"{webhook_url}"
            )

            print(
                "[ACTION] "
                "Removing webhook..."
            )

            telegram_delete_webhook()

            print(
                "[TELEGRAM] "
                "getUpdates mode enabled."
            )

            print(
                "================================================"
            )

    print(
        f"[OFFSET] "
        f"Starting from "
        f"{load_offset()}"
    )

    return True


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    if not startup():

        sys.exit(1)

    # --------------------------------------------------------
    # Import scanner AFTER Telegram startup
    # --------------------------------------------------------

    scanner = import_scanner()

    if scanner is None:

        sys.exit(1)

    # --------------------------------------------------------
    # Get bot username
    # --------------------------------------------------------

    bot = telegram_get_me()

    bot_username = ""

    if bot:

        bot_username = (
            bot.get(
                "username",
                ""
            )
        )

    print(
        "============================================================"
    )

    print(
        "🟢 LIVE LISTENER IS RUNNING"
    )

    print(
        "Commands:"
    )

    print(
        "  /check ETH"
    )

    print(
        "  /check BTC"
    )

    print(
        "  /check BTC ETH SOL XRP"
    )

    print(
        "============================================================"
    )

    while True:

        try:

            updates = get_updates()

            if not updates:

                continue

            for update in updates:

                process_update(
                    scanner,
                    update,
                    bot_username
                )

        except KeyboardInterrupt:

            print(
                "\n[STOP] "
                "Listener stopped."
            )

            break

        except Exception as e:

            print(
                "================================================"
            )

            print(
                "[LOOP ERROR]"
            )

            print(
                f"{e}"
            )

            traceback.print_exc()

            print(
                f"[RECONNECT] "
                f"Waiting {RECONNECT_SLEEP}s..."
            )

            time.sleep(
                RECONNECT_SLEEP
            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
