# ============================================================
# KRAKEN FUTURES 40-ASSET ICHIMOKU + CHIKOU LIVE SCANNER
# VERSION 1.0.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
# PAPER_TRADING = True
#
# STRATEGY
#
# 1H:
#   Determine the main trend using Ichimoku.
#
#   LONG:
#       Price above Kumo
#       Bullish Kumo
#
#   SHORT:
#       Price below Kumo
#       Bearish Kumo
#
# 5M:
#   LONG:
#       Chikou crosses ABOVE price
#       Price closes ABOVE Kumo
#       1H trend = LONG
#
#   SHORT:
#       Chikou crosses BELOW price
#       Price closes BELOW Kumo
#       1H trend = SHORT
#
# SL / TP:
#   Valid confirmed 5M swing levels only.
#
#   LONG:
#       SL = latest confirmed swing low
#       TP = nearest confirmed swing high giving RR >= 1
#
#   SHORT:
#       SL = latest confirmed swing high
#       TP = nearest confirmed swing low giving RR >= 1
#
#   A swing is valid only after the required candles on BOTH sides
#   have closed. No lookahead.
#
# EXIT:
#   LONG:
#       Chikou bearish cross
#       OR price closes below Kumo
#       OR SL
#       OR TP
#
#   SHORT:
#       Chikou bullish cross
#       OR price closes above Kumo
#       OR SL
#       OR TP
#
# DATA:
#   Kraken Futures public Charts API
#   tick_type = trade
#   1H + 5M
#
# TELEGRAM:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# DATABASE:
#   kraken_ichimoku_40.db
#
# ============================================================

import os
import time
import json
import math
import sqlite3
import logging
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.0.0"

REAL_TRADING = False
PAPER_TRADING = True

KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1"

TICK_TYPE = "trade"

TIMEFRAME_1H = "1h"
TIMEFRAME_5M = "5m"

# ------------------------------------------------------------
# Ichimoku
# ------------------------------------------------------------

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
ICHIMOKU_DISPLACEMENT = 26

# ------------------------------------------------------------
# Swing
# ------------------------------------------------------------

SWING_LEFT = 2
SWING_RIGHT = 2

# ------------------------------------------------------------
# RR
# ------------------------------------------------------------

MIN_RR = 1.0

# ------------------------------------------------------------
# Candle history
# ------------------------------------------------------------

HISTORY_1H = 220
HISTORY_5M = 500

# ------------------------------------------------------------
# Polling
# ------------------------------------------------------------

LOOP_SECONDS = 60

# ------------------------------------------------------------
# Database
# ------------------------------------------------------------

DB_FILE = "kraken_ichimoku_40.db"

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("ichimoku40")


# ============================================================
# 40 ASSETS
# ============================================================

ASSETS = [
    "XBT",
    "ETH",
    "SOL",
    "XRP",
    "LTC",
    "DOGE",
    "ADA",
    "LINK",
    "AVAX",
    "DOT",

    "BNB",
    "TRX",
    "UNI",
    "AAVE",
    "SUI",
    "NEAR",
    "ATOM",
    "FIL",
    "ARB",
    "OP",

    "INJ",
    "PEPE",
    "SHIB",
    "BONK",
    "WIF",
    "SEI",
    "TIA",
    "JUP",
    "JTO",
    "RUNE",

    "ICP",
    "APT",
    "FET",
    "ALGO",
    "XLM",
    "BCH",
    "ETC",
    "EOS",
    "MKR",
    "COMP",
]


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "Kraken-Ichi40-Scanner/1.0",
    }
)


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            signal_time INTEGER NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            rr REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_open_signal
        ON signals(asset, direction, status)
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials are not configured.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.status_code != 200:
            log.error(
                "Telegram error: %s %s",
                response.status_code,
                response.text[:300],
            )
            return False

        return True

    except Exception as exc:

        log.error(
            "Telegram exception: %s",
            exc,
        )

        return False


# ============================================================
# TIME
# ============================================================

def now_ms():
    return int(time.time() * 1000)


def utc_text(ms=None):

    if ms is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc,
        )

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value):

    try:
        return float(value)
    except Exception:
        return None


def finite(value):

    return (
        value is not None
        and math.isfinite(value)
    )


# ============================================================
# KRAKEN API
# ============================================================

def fetch_candles(symbol, resolution, count):

    """
    Current Kraken Futures Charts API:

    /api/charts/v1/{tick_type}/{symbol}/{resolution}

    Example:

    /api/charts/v1/trade/PF_XBTUSD/5m
    """

    url = (
        f"{KRAKEN_CHARTS_BASE}/"
        f"{TICK_TYPE}/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "count": int(count),
    }

    last_error = None

    for attempt in range(4):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=20,
            )

            if response.status_code == 200:

                data = response.json()

                candles = data.get(
                    "candles",
                    [],
                )

                result = []

                for c in candles:

                    try:

                        t = int(c["time"])
                        o = float(c["open"])
                        h = float(c["high"])
                        l = float(c["low"])
                        cl = float(c["close"])
                        v = float(c.get("volume", 0))

                        result.append(
                            {
                                "time": t,
                                "open": o,
                                "high": h,
                                "low": l,
                                "close": cl,
                                "volume": v,
                            }
                        )

                    except Exception:
                        continue

                result.sort(
                    key=lambda x: x["time"]
                )

                return result

            last_error = (
                f"HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        except Exception as exc:

            last_error = str(exc)

        time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(
        f"Kraken candle request failed: "
        f"{url} | {last_error}"
    )


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def remove_current_candle(candles, timeframe):

    if not candles:
        return candles

    if timeframe == "5m":
        seconds = 5 * 60

    elif timeframe == "1h":
        seconds = 60 * 60

    else:
        return candles

    current_ms = now_ms()

    last_time = candles[-1]["time"]

    candle_end = last_time + seconds * 1000

    if candle_end > current_ms:

        return candles[:-1]

    return candles


# ============================================================
# ICHIMOKU
# ============================================================

def midpoint(highs, lows, end_index, period):

    start = end_index - period + 1

    if start < 0:
        return None

    hi = max(
        highs[start:end_index + 1]
    )

    lo = min(
        lows[start:end_index + 1]
    )

    return (hi + lo) / 2.0


def build_ichimoku(candles):

    n = len(candles)

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    closes = [
        x["close"]
        for x in candles
    ]

    tenkan = [None] * n
    kijun = [None] * n

    senkou_a_raw = [None] * n
    senkou_b_raw = [None] * n

    for i in range(n):

        tenkan[i] = midpoint(
            highs,
            lows,
            i,
            TENKAN_PERIOD,
        )

        kijun[i] = midpoint(
            highs,
            lows,
            i,
            KIJUN_PERIOD,
        )

        senkou_b_raw[i] = midpoint(
            highs,
            lows,
            i,
            SENKOU_B_PERIOD,
        )

        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):

            senkou_a_raw[i] = (
                tenkan[i]
                + kijun[i]
            ) / 2.0

    # --------------------------------------------------------
    # Align Senkou values to what is actually visible at
    # current candle index.
    #
    # Raw Senkou calculated at t-26 is plotted at t.
    # Therefore visible cloud at t = raw[t-26].
    # --------------------------------------------------------

    cloud_a = [None] * n
    cloud_b = [None] * n

    for i in range(n):

        source = (
            i - ICHIMOKU_DISPLACEMENT
        )

        if source >= 0:

            cloud_a[i] = (
                senkou_a_raw[source]
            )

            cloud_b[i] = (
                senkou_b_raw[source]
            )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": cloud_a,
        "senkou_b": cloud_b,
        "close": closes,
    }


# ============================================================
# CLOUD STATE
# ============================================================

def cloud_state(ichi, index):

    if index < 0:
        return None

    if index >= len(ichi["close"]):
        return None

    price = ichi["close"][index]

    a = ichi["senkou_a"][index]
    b = ichi["senkou_b"][index]

    if not finite(price):
        return None

    if not finite(a) or not finite(b):
        return None

    upper = max(a, b)
    lower = min(a, b)

    if price > upper:

        if a > b:
            return "LONG"

        return "ABOVE_BEARISH_CLOUD"

    if price < lower:

        if a < b:
            return "SHORT"

        return "BELOW_BULLISH_CLOUD"

    return "INSIDE"


# ============================================================
# 1H TREND
# ============================================================

def get_1h_trend(candles):

    if len(candles) < 100:
        return None

    ichi = build_ichimoku(candles)

    i = len(candles) - 1

    state = cloud_state(
        ichi,
        i,
    )

    if state == "LONG":
        return "LONG"

    if state == "SHORT":
        return "SHORT"

    return None


# ============================================================
# CHIKOU CROSS
# ============================================================

def chikou_cross(candles, index):

    """
    Standard Chikou interpretation:

    Current Chikou = current close plotted 26 candles back.

    Therefore, at decision candle t:

        bullish:
            close[t] > close[t-26]
            AND
            close[t-1] <= close[t-27]

        bearish:
            close[t] < close[t-26]
            AND
            close[t-1] >= close[t-27]

    All values are already closed.
    No future data.
    """

    if index < ICHIMOKU_DISPLACEMENT + 2:
        return None

    current_close = candles[index]["close"]

    previous_close = candles[index - 1]["close"]

    price_26 = candles[
        index - ICHIMOKU_DISPLACEMENT
    ]["close"]

    price_27 = candles[
        index - ICHIMOKU_DISPLACEMENT - 1
    ]["close"]

    bullish = (
        current_close > price_26
        and previous_close <= price_27
    )

    bearish = (
        current_close < price_26
        and previous_close >= price_27
    )

    if bullish:
        return "LONG"

    if bearish:
        return "SHORT"

    return None


# ============================================================
# 5M ENTRY CONDITION
# ============================================================

def get_5m_signal(candles):

    if len(candles) < 100:
        return None

    ichi = build_ichimoku(candles)

    i = len(candles) - 1

    cross = chikou_cross(
        candles,
        i,
    )

    if cross is None:
        return None

    state = cloud_state(
        ichi,
        i,
    )

    if cross == "LONG" and state == "LONG":

        return "LONG"

    if cross == "SHORT" and state == "SHORT":

        return "SHORT"

    return None


# ============================================================
# CONFIRMED SWING
# ============================================================

def is_swing_low(candles, index):

    left_start = index - SWING_LEFT
    right_end = index + SWING_RIGHT

    if left_start < 0:
        return False

    if right_end >= len(candles):
        return False

    value = candles[index]["low"]

    for j in range(
        left_start,
        right_end + 1,
    ):

        if j == index:
            continue

        if candles[j]["low"] <= value:
            return False

    return True


def is_swing_high(candles, index):

    left_start = index - SWING_LEFT
    right_end = index + SWING_RIGHT

    if left_start < 0:
        return False

    if right_end >= len(candles):
        return False

    value = candles[index]["high"]

    for j in range(
        left_start,
        right_end + 1,
    ):

        if j == index:
            continue

        if candles[j]["high"] >= value:
            return False

    return True


# ============================================================
# FIND VALID SL / TP
# ============================================================

def find_long_levels(candles, entry_index):

    entry = candles[entry_index]["close"]

    # --------------------------------------------------------
    # A swing becomes confirmed only after SWING_RIGHT candles.
    #
    # Therefore the newest usable swing is:
    #
    # entry_index - SWING_RIGHT
    # --------------------------------------------------------

    latest_confirmed = (
        entry_index - SWING_RIGHT
    )

    if latest_confirmed <= SWING_LEFT:
        return None

    swing_lows = []
    swing_highs = []

    for i in range(
        SWING_LEFT,
        latest_confirmed + 1,
    ):

        if is_swing_low(candles, i):

            level = candles[i]["low"]

            if level < entry:
                swing_lows.append(
                    (i, level)
                )

        if is_swing_high(candles, i):

            level = candles[i]["high"]

            if level > entry:
                swing_highs.append(
                    (i, level)
                )

    if not swing_lows or not swing_highs:
        return None

    # Latest confirmed support
    sl_index, sl = max(
        swing_lows,
        key=lambda x: x[0],
    )

    risk = entry - sl

    if risk <= 0:
        return None

    # --------------------------------------------------------
    # Choose the nearest confirmed resistance which gives
    # RR >= 1.
    # --------------------------------------------------------

    candidates = []

    for tp_index, tp in swing_highs:

        reward = tp - entry

        if reward <= 0:
            continue

        rr = reward / risk

        if rr >= MIN_RR:

            candidates.append(
                (
                    tp_index,
                    tp,
                    rr,
                )
            )

    if not candidates:
        return None

    # Closest valid TP
    tp_index, tp, rr = min(
        candidates,
        key=lambda x: x[1],
    )

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "sl_index": sl_index,
        "tp_index": tp_index,
    }


def find_short_levels(candles, entry_index):

    entry = candles[entry_index]["close"]

    latest_confirmed = (
        entry_index - SWING_RIGHT
    )

    if latest_confirmed <= SWING_LEFT:
        return None

    swing_lows = []
    swing_highs = []

    for i in range(
        SWING_LEFT,
        latest_confirmed + 1,
    ):

        if is_swing_low(candles, i):

            level = candles[i]["low"]

            if level < entry:
                swing_lows.append(
                    (i, level)
                )

        if is_swing_high(candles, i):

            level = candles[i]["high"]

            if level > entry:
                swing_highs.append(
                    (i, level)
                )

    if not swing_lows or not swing_highs:
        return None

    # Latest confirmed resistance
    sl_index, sl = max(
        swing_highs,
        key=lambda x: x[0],
    )

    risk = sl - entry

    if risk <= 0:
        return None

    # --------------------------------------------------------
    # Nearest confirmed support which gives RR >= 1
    # --------------------------------------------------------

    candidates = []

    for tp_index, tp in swing_lows:

        reward = entry - tp

        if reward <= 0:
            continue

        rr = reward / risk

        if rr >= MIN_RR:

            candidates.append(
                (
                    tp_index,
                    tp,
                    rr,
                )
            )

    if not candidates:
        return None

    # Closest valid TP
    tp_index, tp, rr = max(
        candidates,
        key=lambda x: x[1],
    )

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "sl_index": sl_index,
        "tp_index": tp_index,
    }


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
        """
    ).fetchall()

    conn.close()

    return rows


def has_open_trade(asset, direction):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE asset = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (
            asset,
            direction,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def insert_trade(
    asset,
    symbol,
    direction,
    signal_time,
    entry,
    sl,
    tp,
    rr,
):

    conn = db_connect()

    try:

        cur = conn.execute(
            """
            INSERT INTO signals (
                asset,
                symbol,
                direction,
                signal_time,
                entry,
                sl,
                tp,
                rr,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """,
            (
                asset,
                symbol,
                direction,
                signal_time,
                entry,
                sl,
                tp,
                rr,
            ),
        )

        conn.commit()

        return cur.lastrowid

    except sqlite3.IntegrityError:

        conn.rollback()

        return None

    finally:

        conn.close()


def close_trade(
    trade_id,
    exit_price,
    exit_reason,
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE signals
        SET
            status = 'CLOSED',
            exit_price = ?,
            exit_time = ?,
            exit_reason = ?
        WHERE id = ?
          AND status = 'OPEN'
        """,
        (
            exit_price,
            now_ms(),
            exit_reason,
            trade_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# PNL
# ============================================================

def pnl_percent(direction, entry, exit_price):

    if entry == 0:
        return 0.0

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100.0

    return (
        (entry - exit_price)
        / entry
    ) * 100.0


# ============================================================
# CHECK OPEN TRADES
# ============================================================

def manage_open_trades(price_cache):

    trades = get_open_trades()

    if not trades:
        return

    for trade in trades:

        symbol = trade["symbol"]
        direction = trade["direction"]

        candles = price_cache.get(
            symbol
        )

        if not candles:
            continue

        current = candles[-1]["close"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        reason = None
        exit_price = None

        # ----------------------------------------------------
        # HARD SL / TP
        # ----------------------------------------------------

        if direction == "LONG":

            if current <= sl:

                reason = "SL"
                exit_price = current

            elif current >= tp:

                reason = "TP"
                exit_price = current

        else:

            if current >= sl:

                reason = "SL"
                exit_price = current

            elif current <= tp:

                reason = "TP"
                exit_price = current

        # ----------------------------------------------------
        # ICHIMOKU EXIT
        # ----------------------------------------------------

        if reason is None:

            ichi = build_ichimoku(
                candles
            )

            i = len(candles) - 1

            state = cloud_state(
                ichi,
                i,
            )

            cross = chikou_cross(
                candles,
                i,
            )

            if direction == "LONG":

                if cross == "SHORT":

                    reason = "CHIKOU_EXIT"
                    exit_price = current

                elif state != "LONG":

                    reason = "KUMO_EXIT"
                    exit_price = current

            else:

                if cross == "LONG":

                    reason = "CHIKOU_EXIT"
                    exit_price = current

                elif state != "SHORT":

                    reason = "KUMO_EXIT"
                    exit_price = current

        if reason is not None:

            close_trade(
                trade["id"],
                exit_price,
                reason,
            )

            pnl = pnl_percent(
                direction,
                entry,
                exit_price,
            )

            emoji = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            message = (
                f"{emoji} TRADE CLOSED\n\n"
                f"{symbol} {direction}\n"
                f"Reason: {reason}\n"
                f"Entry: {entry:g}\n"
                f"Exit: {exit_price:g}\n"
                f"PnL: {pnl:+.2f}%\n\n"
                f"📌 PAPER ONLY"
            )

            telegram_send(message)

            log.info(
                "CLOSED %s %s | %s | %.3f%%",
                symbol,
                direction,
                reason,
                pnl,
            )


# ============================================================
# NEW SIGNAL
# ============================================================

def create_signal(
    asset,
    symbol,
    candles_1h,
    candles_5m,
):

    trend = get_1h_trend(
        candles_1h
    )

    if trend is None:
        return None

    signal = get_5m_signal(
        candles_5m
    )

    if signal is None:
        return None

    if signal != trend:
        return None

    index = len(candles_5m) - 1

    if signal == "LONG":

        levels = find_long_levels(
            candles_5m,
            index,
        )

    else:

        levels = find_short_levels(
            candles_5m,
            index,
        )

    if levels is None:
        return None

    rr = levels["rr"]

    if rr < MIN_RR:
        return None

    entry = levels["entry"]
    sl = levels["sl"]
    tp = levels["tp"]

    if has_open_trade(
        asset,
        signal,
    ):
        return None

    signal_time = candles_5m[index]["time"]

    trade_id = insert_trade(
        asset=asset,
        symbol=symbol,
        direction=signal,
        signal_time=signal_time,
        entry=entry,
        sl=sl,
        tp=tp,
        rr=rr,
    )

    if trade_id is None:
        return None

    return {
        "id": trade_id,
        "asset": asset,
        "symbol": symbol,
        "direction": signal,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "time": signal_time,
    }


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def format_signal(signal):

    direction = signal["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    return (
        f"{emoji} NEW {direction}\n\n"
        f"{signal['symbol']}\n"
        f"Entry: {signal['entry']:g}\n"
        f"SL: {signal['sl']:g}\n"
        f"TP: {signal['tp']:g}\n"
        f"RR: 1:{signal['rr']:.2f}\n"
        f"Time: {utc_text(signal['time'])}\n\n"
        f"📌 PAPER ONLY"
    )


# ============================================================
# OPEN REPORT
# ============================================================

def send_open_report():

    trades = get_open_trades()

    if not trades:
        return

    lines = [
        "📌 OPEN TRADES",
        "",
    ]

    for trade in trades:

        symbol = trade["symbol"]
        direction = trade["direction"]

        lines.append(
            f"{'🟢' if direction == 'LONG' else '🔴'} "
            f"{symbol} {direction}"
        )

        lines.append(
            f"Entry: {float(trade['entry']):g}"
        )

        lines.append(
            f"SL: {float(trade['sl']):g}"
        )

        lines.append(
            f"TP: {float(trade['tp']):g}"
        )

        lines.append(
            f"RR: 1:{float(trade['rr']):.2f}"
        )

        lines.append("")

    lines.append("📌 PAPER ONLY")

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# SYMBOL
# ============================================================

def make_symbol(asset):

    return f"PF_{asset}USD"


# ============================================================
# PROCESS ONE ASSET
# ============================================================

def process_asset(asset):

    symbol = make_symbol(asset)

    log.info(
        "Scanning %s",
        symbol,
    )

    candles_1h = fetch_candles(
        symbol,
        TIMEFRAME_1H,
        HISTORY_1H,
    )

    candles_5m = fetch_candles(
        symbol,
        TIMEFRAME_5M,
        HISTORY_5M,
    )

    candles_1h = remove_current_candle(
        candles_1h,
        TIMEFRAME_1H,
    )

    candles_5m = remove_current_candle(
        candles_5m,
        TIMEFRAME_5M,
    )

    if len(candles_1h) < 100:
        log.warning(
            "%s insufficient 1H candles: %d",
            symbol,
            len(candles_1h),
        )
        return None, candles_5m

    if len(candles_5m) < 100:
        log.warning(
            "%s insufficient 5M candles: %d",
            symbol,
            len(candles_5m),
        )
        return None, candles_5m

    signal = create_signal(
        asset,
        symbol,
        candles_1h,
        candles_5m,
    )

    return signal, candles_5m


# ============================================================
# MAIN SCAN
# ============================================================

def scan_all():

    log.info(
        "=================================================="
    )

    log.info(
        "KRAKEN ICHIMOKU 40 SCANNER %s",
        VERSION,
    )

    log.info(
        "Assets: %d",
        len(ASSETS),
    )

    log.info(
        "REAL_TRADING=%s | PAPER_TRADING=%s",
        REAL_TRADING,
        PAPER_TRADING,
    )

    price_cache = {}

    new_signals = []

    errors = 0

    for asset in ASSETS:

        try:

            signal, candles_5m = process_asset(
                asset
            )

            symbol = make_symbol(asset)

            if candles_5m:
                price_cache[symbol] = candles_5m

            if signal:

                new_signals.append(
                    signal
                )

                telegram_send(
                    format_signal(signal)
                )

                log.info(
                    "NEW %s %s | Entry=%g SL=%g TP=%g RR=%.2f",
                    signal["symbol"],
                    signal["direction"],
                    signal["entry"],
                    signal["sl"],
                    signal["tp"],
                    signal["rr"],
                )

        except Exception as exc:

            errors += 1

            log.exception(
                "ERROR %s: %s",
                asset,
                exc,
            )

        time.sleep(0.15)

    # --------------------------------------------------------
    # Manage currently open trades after fresh prices loaded.
    # --------------------------------------------------------

    manage_open_trades(
        price_cache
    )

    log.info(
        "Scan completed | New=%d | Errors=%d",
        len(new_signals),
        errors,
    )

    return new_signals


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    init_db()

    log.info(
        "Starting Kraken 40 Asset Ichimoku Scanner"
    )

    log.info(
        "Database: %s",
        DB_FILE,
    )

    log.info(
        "1H trend + 5M Chikou/Kumo"
    )

    log.info(
        "Minimum RR: %.2f",
        MIN_RR,
    )

    while True:

        started = time.time()

        try:

            scan_all()

        except KeyboardInterrupt:

            log.info(
                "Stopped by user."
            )

            break

        except Exception as exc:

            log.exception(
                "MAIN LOOP ERROR: %s",
                exc,
            )

        elapsed = time.time() - started

        sleep_for = max(
            5,
            LOOP_SECONDS - elapsed,
        )

        log.info(
            "Next scan in %.1f seconds",
            sleep_for,
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
