# ============================================================
# KRAKEN FUTURES 40 ASSET ICHIMOKU LIVE PAPER SCANNER
# VERSION 2.1.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
# PAPER_TRADING = True
#
# STRATEGY
# ------------------------------------------------------------
# 1H:
#   LONG  = price above Kumo + bullish cloud
#   SHORT = price below Kumo + bearish cloud
#
# 5M:
#   LONG:
#       Chikou crosses above price
#       + close above visible Kumo
#       + 1H LONG trend
#
#   SHORT:
#       Chikou crosses below price
#       + close below visible Kumo
#       + 1H SHORT trend
#
# SL / TP
# ------------------------------------------------------------
# TENKAN IS NOT USED FOR SL/TP
#
# Valid levels:
#   - Kijun
#   - Senkou A
#   - Senkou B
#   - confirmed Swing High / Swing Low
#
# LONG:
#   SL = nearest valid support below entry
#   TP = nearest valid resistance above entry
#        with RR >= MIN_RR
#
# SHORT:
#   SL = nearest valid resistance above entry
#   TP = nearest valid support below entry
#        with RR >= MIN_RR
#
# SWINGS:
#   confirmed only
#   left/right = 2
#   no lookahead
#
# DATABASE
# ------------------------------------------------------------
# SQLite history is preserved in:
#   kraken_ichimoku_40.db
#
# GitHub Actions should persist this file between runs.
#
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_ichimoku_40.db"

KRAKEN_BASE = "https://futures.kraken.com"
CHART_BASE = "https://futures.kraken.com/api/charts/v1"

TF_1H = "1h"
TF_5M = "5m"

COUNT_1H = 300
COUNT_5M = 500

REQUEST_TIMEOUT = 20

SCAN_INTERVAL_SECONDS = 60

ICH_TENKAN = 9
ICH_KIJUN = 26
ICH_SENKOU_B = 52
ICH_DISPLACEMENT = 26

SWING_LEFT = 2
SWING_RIGHT = 2

MIN_RR = 1.0

REPORT_INTERVAL_SECONDS = 300

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

CHART_DIR = "charts"

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

SYMBOL_MAP = {
    asset: f"PF_{asset}USD"
    for asset in ASSETS
}


# ============================================================
# GLOBAL HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Kraken-Ichimoku-40-Paper-Scanner/2.1.0"
    }
)


# ============================================================
# HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def fmt_price(value):
    if value is None:
        return "-"

    try:
        value = float(value)
    except Exception:
        return "-"

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:.3f}"

    if abs(value) >= 10:
        return f"{value:.4f}"

    if abs(value) >= 1:
        return f"{value:.5f}"

    if abs(value) >= 0.1:
        return f"{value:.6f}"

    if abs(value) >= 0.01:
        return f"{value:.7f}"

    return f"{value:.10f}".rstrip("0").rstrip(".")


def fmt_pct(value):
    try:
        value = float(value)
    except Exception:
        return "0.00%"

    return f"{value:+.2f}%"


def fmt_time(ts):
    if not ts:
        return "-"

    try:
        dt = datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc,
        )
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "-"


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_configured():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_request(method, data=None, files=None):
    if not telegram_configured():
        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    try:
        response = SESSION.post(
            url,
            data=data,
            files=files,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"Telegram HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return None

        return response.json()

    except Exception as exc:
        print(f"Telegram error: {exc}")
        return None


def send_telegram_message(text):
    if not telegram_configured():
        print("\n" + text + "\n")
        return False

    result = telegram_request(
        "sendMessage",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
    )

    return bool(result)


def send_telegram_photo(
    image_path,
    caption,
):
    if not telegram_configured():
        print("\n" + caption + "\n")
        return False

    try:
        with open(image_path, "rb") as photo:
            result = telegram_request(
                "sendPhoto",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                },
                files={
                    "photo": photo,
                },
            )

        return bool(result)

    except Exception as exc:
        print(f"Telegram photo error: {exc}")
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


def ensure_column(
    conn,
    table,
    column,
    definition,
):
    columns = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    names = {
        row["name"]
        for row in columns
    }

    if column not in names:
        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {column} {definition}
            """
        )


def init_db():
    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            asset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,

            signal_time INTEGER NOT NULL,

            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,

            rr REAL,

            sl_source TEXT,
            tp_source TEXT,

            status TEXT NOT NULL DEFAULT 'OPEN',

            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,

            pnl_pct REAL
        )
        """
    )

    ensure_column(
        conn,
        "trades",
        "rr",
        "REAL",
    )

    ensure_column(
        conn,
        "trades",
        "sl_source",
        "TEXT",
    )

    ensure_column(
        conn,
        "trades",
        "tp_source",
        "TEXT",
    )

    ensure_column(
        conn,
        "trades",
        "exit_price",
        "REAL",
    )

    ensure_column(
        conn,
        "trades",
        "exit_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "trades",
        "exit_reason",
        "TEXT",
    )

    ensure_column(
        conn,
        "trades",
        "pnl_pct",
        "REAL",
    )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_trades_symbol_direction_signal
        ON trades (
            symbol,
            direction,
            signal_time
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_trades_status
        ON trades(status)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_trades_symbol_direction
        ON trades(symbol, direction)
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# DATABASE TRADE OPERATIONS
# ============================================================

def has_open_trade(
    symbol,
    direction,
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (
            symbol,
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
    sl_source,
    tp_source,
):
    conn = db_connect()

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO trades (
            asset,
            symbol,
            direction,
            signal_time,
            entry,
            sl,
            tp,
            rr,
            sl_source,
            tp_source,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            asset,
            symbol,
            direction,
            int(signal_time),
            float(entry),
            float(sl),
            float(tp),
            float(rr),
            sl_source,
            tp_source,
        ),
    )

    conn.commit()

    inserted = cursor.rowcount == 1

    conn.close()

    return inserted


def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
        """
    ).fetchall()

    conn.close()

    return rows


def close_trade(
    trade_id,
    exit_price,
    exit_reason,
    exit_time=None,
):
    if exit_time is None:
        exit_time = now_ts()

    conn = db_connect()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (trade_id,),
    ).fetchone()

    if row is None:
        conn.close()
        return None

    entry = float(row["entry"])
    direction = row["direction"]

    exit_price = float(exit_price)

    if direction == "LONG":
        pnl_pct = (
            (exit_price - entry)
            / entry
            * 100.0
        )
    else:
        pnl_pct = (
            (entry - exit_price)
            / entry
            * 100.0
        )

    conn.execute(
        """
        UPDATE trades
        SET
            status = 'CLOSED',
            exit_price = ?,
            exit_time = ?,
            exit_reason = ?,
            pnl_pct = ?
        WHERE id = ?
        """,
        (
            exit_price,
            int(exit_time),
            exit_reason,
            float(pnl_pct),
            trade_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "id": trade_id,
        "asset": row["asset"],
        "symbol": row["symbol"],
        "direction": direction,
        "entry": entry,
        "exit": exit_price,
        "reason": exit_reason,
        "pnl_pct": pnl_pct,
        "exit_time": exit_time,
    }


# ============================================================
# KRAKEN OHLC
# ============================================================

def normalize_candles(payload):
    """
    Kraken chart responses have changed format over time.
    This parser accepts common response layouts.
    """

    if not isinstance(payload, dict):
        return []

    data = None

    for key in (
        "candles",
        "data",
        "result",
        "ohlc",
    ):
        if key in payload:
            data = payload[key]
            break

    if isinstance(data, dict):
        for key in (
            "candles",
            "data",
            "result",
            "ohlc",
        ):
            if key in data:
                data = data[key]
                break

    if not isinstance(data, list):
        return []

    candles = []

    for item in data:
        try:
            if isinstance(item, dict):
                ts = (
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("t")
                )

                o = (
                    item.get("open")
                    or item.get("o")
                )

                h = (
                    item.get("high")
                    or item.get("h")
                )

                l = (
                    item.get("low")
                    or item.get("l")
                )

                c = (
                    item.get("close")
                    or item.get("c")
                )

                v = (
                    item.get("volume")
                    or item.get("v")
                    or 0
                )

            elif isinstance(item, (list, tuple)):
                if len(item) < 5:
                    continue

                ts = item[0]
                o = item[1]
                h = item[2]
                l = item[3]
                c = item[4]
                v = item[5] if len(item) > 5 else 0

            else:
                continue

            ts = float(ts)

            # Some APIs return milliseconds.
            if ts > 10_000_000_000:
                ts /= 1000.0

            o = float(o)
            h = float(h)
            l = float(l)
            c = float(c)
            v = float(v)

            candles.append(
                {
                    "time": int(ts),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                }
            )

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


def fetch_candles(
    symbol,
    timeframe,
    count,
):
    url = (
        f"{CHART_BASE}/trade/"
        f"{symbol}/"
        f"{timeframe}"
    )

    try:
        response = SESSION.get(
            url,
            params={
                "count": count,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"{symbol} {timeframe}: "
                f"HTTP {response.status_code}"
            )
            return []

        payload = response.json()

        candles = normalize_candles(
            payload
        )

        if not candles:
            print(
                f"{symbol} {timeframe}: "
                f"no candle data"
            )

        return candles

    except Exception as exc:
        print(
            f"{symbol} {timeframe} "
            f"request error: {exc}"
        )

        return []


# ============================================================
# CLOSED CANDLE FILTER
# ============================================================

def filter_closed_candles(
    candles,
    timeframe_minutes,
):
    if not candles:
        return []

    current = int(
        time.time()
    )

    interval = (
        timeframe_minutes * 60
    )

    result = []

    for candle in candles:
        candle_time = int(
            candle["time"]
        )

        if candle_time + interval <= current:
            result.append(candle)

    return result


# ============================================================
# SERIES
# ============================================================

def candle_series(candles):
    return {
        "time": [
            x["time"]
            for x in candles
        ],
        "open": [
            x["open"]
            for x in candles
        ],
        "high": [
            x["high"]
            for x in candles
        ],
        "low": [
            x["low"]
            for x in candles
        ],
        "close": [
            x["close"]
            for x in candles
        ],
        "volume": [
            x["volume"]
            for x in candles
        ],
    }


# ============================================================
# ICHIMOKU
# ============================================================

def rolling_midpoint(
    highs,
    lows,
    period,
):
    result = [
        None
        for _ in highs
    ]

    for i in range(
        period - 1,
        len(highs),
    ):
        window_high = max(
            highs[
                i - period + 1:
                i + 1
            ]
        )

        window_low = min(
            lows[
                i - period + 1:
                i + 1
            ]
        )

        result[i] = (
            window_high
            + window_low
        ) / 2.0

    return result


def calculate_ichimoku(candles):
    s = candle_series(candles)

    highs = s["high"]
    lows = s["low"]
    closes = s["close"]

    n = len(candles)

    tenkan = rolling_midpoint(
        highs,
        lows,
        ICH_TENKAN,
    )

    kijun = rolling_midpoint(
        highs,
        lows,
        ICH_KIJUN,
    )

    senkou_b_raw = rolling_midpoint(
        highs,
        lows,
        ICH_SENKOU_B,
    )

    senkou_a_raw = [
        None
        for _ in range(n)
    ]

    for i in range(n):
        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):
            senkou_a_raw[i] = (
                tenkan[i]
                + kijun[i]
            ) / 2.0

    # Visible cloud at candle i comes from
    # the raw cloud calculated 26 candles earlier.
    visible_a = [
        None
        for _ in range(n)
    ]

    visible_b = [
        None
        for _ in range(n)
    ]

    for i in range(n):
        source = (
            i - ICH_DISPLACEMENT
        )

        if source >= 0:
            visible_a[i] = (
                senkou_a_raw[source]
            )

            visible_b[i] = (
                senkou_b_raw[source]
            )

    # Chikou value aligned with current candle.
    # Chikou[t] = close[t]
    # compared with price 26 candles earlier.
    chikou = [
        None
        for _ in range(n)
    ]

    for i in range(n):
        chikou[i] = closes[i]

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a_raw": senkou_a_raw,
        "senkou_b_raw": senkou_b_raw,
        "visible_a": visible_a,
        "visible_b": visible_b,
        "chikou": chikou,
        "close": closes,
        "time": s["time"],
        "high": highs,
        "low": lows,
    }


# ============================================================
# 1H TREND
# ============================================================

def get_1h_trend(
    ichi,
    index,
):
    if index < 0:
        return None

    close = ichi["close"][index]

    a = ichi["visible_a"][index]
    b = ichi["visible_b"][index]

    if (
        close is None
        or a is None
        or b is None
    ):
        return None

    cloud_top = max(a, b)
    cloud_bottom = min(a, b)

    bullish_cloud = a > b
    bearish_cloud = a < b

    if (
        close > cloud_top
        and bullish_cloud
    ):
        return "LONG"

    if (
        close < cloud_bottom
        and bearish_cloud
    ):
        return "SHORT"

    return None


# ============================================================
# 5M CHIKOU CROSS
# ============================================================

def chikou_cross(
    closes,
    index,
    direction,
):
    """
    Bullish:
        close[t] > close[t-26]
        previous:
        close[t-1] <= close[t-27]

    Bearish:
        close[t] < close[t-26]
        previous:
        close[t-1] >= close[t-27]
    """

    if index < ICH_DISPLACEMENT + 1:
        return False

    current_price = closes[index]

    current_reference = closes[
        index - ICH_DISPLACEMENT
    ]

    previous_price = closes[
        index - 1
    ]

    previous_reference = closes[
        index - ICH_DISPLACEMENT - 1
    ]

    if direction == "LONG":
        return (
            current_price
            > current_reference
            and
            previous_price
            <= previous_reference
        )

    if direction == "SHORT":
        return (
            current_price
            < current_reference
            and
            previous_price
            >= previous_reference
        )

    return False


# ============================================================
# CONFIRMED SWINGS
# ============================================================

def confirmed_swing_highs(
    highs,
    left=SWING_LEFT,
    right=SWING_RIGHT,
):
    result = []

    n = len(highs)

    for i in range(
        left,
        n - right,
    ):
        center = highs[i]

        left_values = highs[
            i - left:i
        ]

        right_values = highs[
            i + 1:i + right + 1
        ]

        if (
            all(center > x for x in left_values)
            and
            all(center >= x for x in right_values)
        ):
            confirmation_index = (
                i + right
            )

            result.append(
                {
                    "index": i,
                    "confirmation_index":
                        confirmation_index,
                    "price": center,
                    "type": "Swing High",
                }
            )

    return result


def confirmed_swing_lows(
    lows,
    left=SWING_LEFT,
    right=SWING_RIGHT,
):
    result = []

    n = len(lows)

    for i in range(
        left,
        n - right,
    ):
        center = lows[i]

        left_values = lows[
            i - left:i
        ]

        right_values = lows[
            i + 1:i + right + 1
        ]

        if (
            all(center < x for x in left_values)
            and
            all(center <= x for x in right_values)
        ):
            confirmation_index = (
                i + right
            )

            result.append(
                {
                    "index": i,
                    "confirmation_index":
                        confirmation_index,
                    "price": center,
                    "type": "Swing Low",
                }
            )

    return result


def valid_swings_at_entry(
    candles,
    entry_index,
):
    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    swing_highs = confirmed_swing_highs(
        highs
    )

    swing_lows = confirmed_swing_lows(
        lows
    )

    valid_highs = [
        x
        for x in swing_highs
        if x["confirmation_index"]
        <= entry_index
    ]

    valid_lows = [
        x
        for x in swing_lows
        if x["confirmation_index"]
        <= entry_index
    ]

    return (
        valid_highs,
        valid_lows,
    )


# ============================================================
# LEVEL CANDIDATES
# ============================================================

def build_level_candidates(
    candles,
    ichi,
    entry_index,
    direction,
    entry,
):
    """
    IMPORTANT:
    Tenkan is deliberately NOT included.

    Sources:
        Kijun
        Senkou A
        Senkou B
        confirmed swings
    """

    candidates = []

    kijun = ichi["kijun"][entry_index]
    senkou_a = ichi["visible_a"][entry_index]
    senkou_b = ichi["visible_b"][entry_index]

    components = [
        (kijun, "Kijun"),
        (senkou_a, "Senkou A"),
        (senkou_b, "Senkou B"),
    ]

    if direction == "LONG":
        for value, source in components:
            if value is None:
                continue

            value = float(value)

            if value < entry:
                candidates.append(
                    {
                        "price": value,
                        "source": source,
                        "kind": "SUPPORT",
                    }
                )

    elif direction == "SHORT":
        for value, source in components:
            if value is None:
                continue

            value = float(value)

            if value > entry:
                candidates.append(
                    {
                        "price": value,
                        "source": source,
                        "kind": "RESISTANCE",
                    }
                )

    swing_highs, swing_lows = valid_swings_at_entry(
        candles,
        entry_index,
    )

    if direction == "LONG":
        for swing in swing_lows:
            value = float(
                swing["price"]
            )

            if value < entry:
                candidates.append(
                    {
                        "price": value,
                        "source": "Swing Low",
                        "kind": "SUPPORT",
                    }
                )

        for swing in swing_highs:
            value = float(
                swing["price"]
            )

            if value > entry:
                candidates.append(
                    {
                        "price": value,
                        "source": "Swing High",
                        "kind": "RESISTANCE",
                    }
                )

    else:
        for swing in swing_highs:
            value = float(
                swing["price"]
            )

            if value > entry:
                candidates.append(
                    {
                        "price": value,
                        "source": "Swing High",
                        "kind": "RESISTANCE",
                    }
                )

        for swing in swing_lows:
            value = float(
                swing["price"]
            )

            if value < entry:
                candidates.append(
                    {
                        "price": value,
                        "source": "Swing Low",
                        "kind": "SUPPORT",
                    }
                )

    return candidates


# ============================================================
# SL / TP SELECTION
# ============================================================

def select_sl_tp(
    candles,
    ichi,
    entry_index,
    direction,
):
    entry = float(
        candles[entry_index]["close"]
    )

    candidates = build_level_candidates(
        candles=candles,
        ichi=ichi,
        entry_index=entry_index,
        direction=direction,
        entry=entry,
    )

    supports = [
        x
        for x in candidates
        if x["kind"] == "SUPPORT"
    ]

    resistances = [
        x
        for x in candidates
        if x["kind"] == "RESISTANCE"
    ]

    if direction == "LONG":

        if not supports:
            return None

        if not resistances:
            return None

        # Nearest support below entry.
        sl_candidate = max(
            supports,
            key=lambda x: x["price"],
        )

        sl = float(
            sl_candidate["price"]
        )

        risk = entry - sl

        if risk <= 0:
            return None

        # Only TP levels with RR >= 1.
        valid_tp = []

        for candidate in resistances:
            tp = float(
                candidate["price"]
            )

            reward = tp - entry

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:
                valid_tp.append(
                    {
                        **candidate,
                        "rr": rr,
                    }
                )

        if not valid_tp:
            return None

        # Nearest resistance satisfying RR.
        tp_candidate = min(
            valid_tp,
            key=lambda x: x["price"],
        )

        tp = float(
            tp_candidate["price"]
        )

        rr = (
            tp - entry
        ) / risk

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "sl_source": sl_candidate["source"],
            "tp_source": tp_candidate["source"],
        }

    if direction == "SHORT":

        if not supports:
            return None

        if not resistances:
            return None

        # Nearest resistance above entry.
        sl_candidate = min(
            resistances,
            key=lambda x: x["price"],
        )

        sl = float(
            sl_candidate["price"]
        )

        risk = sl - entry

        if risk <= 0:
            return None

        # Only TP levels with RR >= 1.
        valid_tp = []

        for candidate in supports:
            tp = float(
                candidate["price"]
            )

            reward = entry - tp

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:
                valid_tp.append(
                    {
                        **candidate,
                        "rr": rr,
                    }
                )

        if not valid_tp:
            return None

        # Nearest support satisfying RR.
        tp_candidate = max(
            valid_tp,
            key=lambda x: x["price"],
        )

        tp = float(
            tp_candidate["price"]
        )

        rr = (
            entry - tp
        ) / risk

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "sl_source": sl_candidate["source"],
            "tp_source": tp_candidate["source"],
        }

    return None


# ============================================================
# SIGNAL DETECTION
# ============================================================

def detect_signal(
    candles_1h,
    candles_5m,
):
    if len(candles_1h) < 100:
        return None

    if len(candles_5m) < 100:
        return None

    ichi_1h = calculate_ichimoku(
        candles_1h
    )

    ichi_5m = calculate_ichimoku(
        candles_5m
    )

    index_1h = len(candles_1h) - 1
    index_5m = len(candles_5m) - 1

    trend = get_1h_trend(
        ichi_1h,
        index_1h,
    )

    if trend not in (
        "LONG",
        "SHORT",
    ):
        return None

    close_5m = candles_5m[
        index_5m
    ]["close"]

    cloud_a = ichi_5m[
        "visible_a"
    ][index_5m]

    cloud_b = ichi_5m[
        "visible_b"
    ][index_5m]

    if (
        cloud_a is None
        or cloud_b is None
    ):
        return None

    cloud_top = max(
        cloud_a,
        cloud_b,
    )

    cloud_bottom = min(
        cloud_a,
        cloud_b,
    )

    if trend == "LONG":

        if close_5m <= cloud_top:
            return None

        if not chikou_cross(
            ichi_5m["close"],
            index_5m,
            "LONG",
        ):
            return None

        direction = "LONG"

    else:

        if close_5m >= cloud_bottom:
            return None

        if not chikou_cross(
            ichi_5m["close"],
            index_5m,
            "SHORT",
        ):
            return None

        direction = "SHORT"

    levels = select_sl_tp(
        candles=candles_5m,
        ichi=ichi_5m,
        entry_index=index_5m,
        direction=direction,
    )

    if levels is None:
        return None

    return {
        "direction": direction,
        "signal_time": candles_5m[
            index_5m
        ]["time"],
        "entry_index": index_5m,
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "rr": levels["rr"],
        "sl_source": levels["sl_source"],
        "tp_source": levels["tp_source"],
        "trend": trend,
        "ichi_1h": ichi_1h,
        "ichi_5m": ichi_5m,
    }


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    direction,
    entry,
    current,
):
    entry = float(entry)
    current = float(current)

    if direction == "LONG":
        return (
            (current - entry)
            / entry
            * 100.0
        )

    return (
        (entry - current)
        / entry
        * 100.0
    )


def sl_distance_pct(
    direction,
    entry,
    sl,
):
    entry = float(entry)
    sl = float(sl)

    if direction == "LONG":
        return (
            (entry - sl)
            / entry
            * 100.0
        )

    return (
        (sl - entry)
        / entry
        * 100.0
    )


def tp_distance_pct(
    direction,
    entry,
    tp,
):
    entry = float(entry)
    tp = float(tp)

    if direction == "LONG":
        return (
            (tp - entry)
            / entry
            * 100.0
        )

    return (
        (entry - tp)
        / entry
        * 100.0
    )


# ============================================================
# OPEN TRADE PROCESSING
# ============================================================

def process_open_trades(
    market_data,
):
    rows = get_open_trades()

    closed = []

    for trade in rows:

        symbol = trade["symbol"]

        data = market_data.get(symbol)

        if not data:
            continue

        candles = data.get(
            "candles_5m",
            [],
        )

        if not candles:
            continue

        candle = candles[-1]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        close = float(
            candle["close"]
        )

        direction = trade["direction"]

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        exit_price = None
        reason = None

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            # Same candle TP + SL:
            # SL first, conservative assumption.
            if hit_sl:
                exit_price = sl
                reason = "SL"

            elif hit_tp:
                exit_price = tp
                reason = "TP"

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            # Same candle TP + SL:
            # SL first.
            if hit_sl:
                exit_price = sl
                reason = "SL"

            elif hit_tp:
                exit_price = tp
                reason = "TP"

        # Opposite Ichimoku / Chikou exit.
        if exit_price is None:
            ichi = data.get(
                "ichi_5m"
            )

            if ichi:
                idx = len(candles) - 1

                a = ichi["visible_a"][idx]
                b = ichi["visible_b"][idx]

                if (
                    a is not None
                    and b is not None
                ):
                    cloud_top = max(a, b)
                    cloud_bottom = min(a, b)

                    if direction == "LONG":
                        opposite_cross = chikou_cross(
                            ichi["close"],
                            idx,
                            "SHORT",
                        )

                        if (
                            opposite_cross
                            and close < cloud_bottom
                        ):
                            exit_price = close
                            reason = "ICHIMOKU_EXIT"

                    else:
                        opposite_cross = chikou_cross(
                            ichi["close"],
                            idx,
                            "LONG",
                        )

                        if (
                            opposite_cross
                            and close > cloud_top
                        ):
                            exit_price = close
                            reason = "ICHIMOKU_EXIT"

        if exit_price is not None:
            result = close_trade(
                trade_id=trade["id"],
                exit_price=exit_price,
                exit_reason=reason,
                exit_time=candle["time"],
            )

            if result:
                closed.append(result)

                send_close_notification(
                    result
                )

    return closed


# ============================================================
# CLOSE NOTIFICATION
# ============================================================

def send_close_notification(
    result,
):
    direction = result["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    pnl = float(
        result["pnl_pct"]
    )

    text = (
        "✅ TRADE CLOSED\n\n"
        f"{emoji} {result['symbol']} "
        f"{direction}\n"
        f"Reason: {result['reason']}\n"
        f"Entry: {fmt_price(result['entry'])}\n"
        f"Exit: {fmt_price(result['exit'])}\n"
        f"PnL: {fmt_pct(pnl)}\n"
        f"Time: {fmt_time(result['exit_time'])}\n\n"
        "📌 PAPER ONLY"
    )

    send_telegram_message(
        text
    )


# ============================================================
# CHART
# ============================================================

def plot_candles(
    ax,
    candles,
):
    if not candles:
        return

    times = [
        datetime.fromtimestamp(
            c["time"],
            tz=timezone.utc,
        )
        for c in candles
    ]

    x = mdates.date2num(times)

    if len(x) >= 2:
        candle_width = (
            x[1] - x[0]
        ) * 0.70
    else:
        candle_width = 0.01

    for i, candle in enumerate(candles):
        open_price = candle["open"]
        close_price = candle["close"]
        high = candle["high"]
        low = candle["low"]

        color = (
            "green"
            if close_price >= open_price
            else "red"
        )

        ax.plot(
            [x[i], x[i]],
            [low, high],
            color=color,
            linewidth=0.8,
        )

        body_low = min(
            open_price,
            close_price,
        )

        body_height = abs(
            close_price
            - open_price
        )

        if body_height == 0:
            body_height = max(
                abs(close_price) * 0.00001,
                1e-12,
            )

        rect = Rectangle(
            (
                x[i] - candle_width / 2,
                body_low,
            ),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            alpha=0.75,
        )

        ax.add_patch(rect)


def create_signal_chart(
    asset,
    candles,
    ichi,
    signal,
):
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    # Last 120 candles for readability.
    display_count = min(
        120,
        len(candles),
    )

    start = len(candles) - display_count

    display_candles = candles[
        start:
    ]

    times = [
        datetime.fromtimestamp(
            c["time"],
            tz=timezone.utc,
        )
        for c in display_candles
    ]

    x = mdates.date2num(times)

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    plot_candles(
        ax,
        display_candles,
    )

    tenkan = ichi["tenkan"][start:]
    kijun = ichi["kijun"][start:]
    senkou_a = ichi["visible_a"][start:]
    senkou_b = ichi["visible_b"][start:]

    ax.plot(
        x,
        [
            value if value is not None else math.nan
            for value in tenkan
        ],
        label="Tenkan",
        linewidth=1.0,
    )

    ax.plot(
        x,
        [
            value if value is not None else math.nan
            for value in kijun
        ],
        label="Kijun",
        linewidth=1.2,
    )

    ax.plot(
        x,
        [
            value if value is not None else math.nan
            for value in senkou_a
        ],
        label="Senkou A",
        linewidth=1.0,
    )

    ax.plot(
        x,
        [
            value if value is not None else math.nan
            for value in senkou_b
        ],
        label="Senkou B",
        linewidth=1.0,
    )

    a_values = [
        value if value is not None else math.nan
        for value in senkou_a
    ]

    b_values = [
        value if value is not None else math.nan
        for value in senkou_b
    ]

    ax.fill_between(
        x,
        a_values,
        b_values,
        alpha=0.15,
    )

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.2,
        label=f"Entry {fmt_price(entry)}",
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.2,
        label=f"SL {fmt_price(sl)}",
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.2,
        label=f"TP {fmt_price(tp)}",
    )

    # Mark latest entry candle.
    entry_idx = len(
        display_candles
    ) - 1

    if 0 <= entry_idx < len(x):
        ax.scatter(
            [x[entry_idx]],
            [entry],
            s=70,
            marker="o",
            zorder=10,
        )

    # Mark confirmed swing levels used by SL/TP.
    entry_index_global = (
        len(candles) - 1
    )

    swing_highs, swing_lows = valid_swings_at_entry(
        candles,
        entry_index_global,
    )

    for swing in swing_highs[-10:]:
        idx = swing["index"] - start

        if 0 <= idx < len(x):
            ax.scatter(
                [x[idx]],
                [swing["price"]],
                marker="^",
                s=35,
                alpha=0.6,
            )

    for swing in swing_lows[-10:]:
        idx = swing["index"] - start

        if 0 <= idx < len(x):
            ax.scatter(
                [x[idx]],
                [swing["price"]],
                marker="v",
                s=35,
                alpha=0.6,
            )

    direction = signal["direction"]

    ax.set_title(
        f"{asset} 5M Ichimoku | {direction}"
    )

    ax.grid(
        True,
        alpha=0.2,
    )

    ax.legend(
        loc="upper left",
        fontsize=8,
    )

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M"
        )
    )

    fig.autofmt_xdate()

    fig.tight_layout()

    filename = (
        f"{asset}_"
        f"{direction}_"
        f"{signal['signal_time']}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename,
    )

    fig.savefig(
        path,
        dpi=130,
        bbox_inches="tight",
    )

    plt.close(fig)

    return path


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def send_new_signal(
    asset,
    signal,
    candles,
):
    direction = signal["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]
    rr = signal["rr"]

    sl_pct = sl_distance_pct(
        direction,
        entry,
        sl,
    )

    tp_pct = tp_distance_pct(
        direction,
        entry,
        tp,
    )

    caption = (
        f"{emoji} NEW {direction}\n\n"
        f"PF_{asset}USD\n"
        f"Entry: {fmt_price(entry)}\n"
        f"SL: {fmt_price(sl)} "
        f"(-{sl_pct:.2f}%)\n"
        f"TP: {fmt_price(tp)} "
        f"(+{tp_pct:.2f}%)\n"
        f"RR: {rr:.2f}\n\n"
        f"SL Source: {signal['sl_source']}\n"
        f"TP Source: {signal['tp_source']}\n\n"
        f"1H Trend: {direction}\n"
        f"5M Chikou: Confirmed\n\n"
        f"Signal: {fmt_time(signal['signal_time'])}\n\n"
        "📌 PAPER ONLY"
    )

    path = create_signal_chart(
        asset=asset,
        candles=candles,
        ichi=signal["ichi_5m"],
        signal=signal,
    )

    send_telegram_photo(
        path,
        caption,
    )


# ============================================================
# OPEN TRADE DETAILS
# ============================================================

def build_open_details(
    market_data,
):
    rows = get_open_trades()

    details = []

    for trade in rows:
        symbol = trade["symbol"]

        data = market_data.get(symbol)

        if not data:
            continue

        candles = data.get(
            "candles_5m",
            [],
        )

        if not candles:
            continue

        current = float(
            candles[-1]["close"]
        )

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        direction = trade[
            "direction"
        ]

        pnl = calculate_pnl(
            direction,
            entry,
            current,
        )

        sl_pct = sl_distance_pct(
            direction,
            entry,
            sl,
        )

        tp_pct = tp_distance_pct(
            direction,
            entry,
            tp,
        )

        details.append(
            {
                "trade": trade,
                "current": current,
                "pnl": pnl,
                "sl_pct": sl_pct,
                "tp_pct": tp_pct,
            }
        )

    return details


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats(
    open_details=None,
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN pnl_pct > 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN pnl_pct < 0
                    THEN 1
                    ELSE 0
                END
            ) AS losses,
            COALESCE(
                SUM(pnl_pct),
                0
            ) AS closed_pnl
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    conn.close()

    total = int(
        row["total"] or 0
    )

    wins = int(
        row["wins"] or 0
    )

    losses = int(
        row["losses"] or 0
    )

    closed_pnl = float(
        row["closed_pnl"] or 0
    )

    decided = (
        wins + losses
    )

    win_rate = (
        wins / decided * 100
        if decided > 0
        else 0.0
    )

    open_pnl = 0.0

    if open_details:
        for item in open_details:
            open_pnl += float(
                item["pnl"]
            )

    total_pnl = (
        closed_pnl
        + open_pnl
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "closed_pnl": closed_pnl,
        "open_pnl": open_pnl,
        "total_pnl": total_pnl,
    }


# ============================================================
# REPORT
# ============================================================

def build_report(
    trend_counts,
    new_signals,
    open_details,
):
    stats = performance_stats(
        open_details
    )

    lines = []

    lines.append(
        "📊 1H ICHIMOKU REPORT"
    )

    lines.append(
        f"Assets scanned: {len(ASSETS)}"
    )

    lines.append(
        f"🟢 New LONG: "
        f"{trend_counts.get('LONG', 0)}"
    )

    lines.append(
        f"🔴 New SHORT: "
        f"{trend_counts.get('SHORT', 0)}"
    )

    lines.append("")

    if new_signals:
        lines.append(
            "🆕 NEW SIGNALS"
        )

        for item in new_signals:
            direction = item[
                "direction"
            ]

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            entry = item["entry"]
            sl = item["sl"]
            tp = item["tp"]

            sl_pct = sl_distance_pct(
                direction,
                entry,
                sl,
            )

            tp_pct = tp_distance_pct(
                direction,
                entry,
                tp,
            )

            lines.append(
                f"{emoji} {item['asset']} "
                f"{direction} | "
                f"Entry {fmt_price(entry)} | "
                f"SL {fmt_price(sl)} "
                f"(-{sl_pct:.2f}%) | "
                f"TP {fmt_price(tp)} "
                f"(+{tp_pct:.2f}%) | "
                f"RR {item['rr']:.2f}"
            )

        lines.append("")

    lines.append(
        f"📌 OPEN TRADES: "
        f"{len(open_details)}"
    )

    if open_details:

        for item in open_details:

            trade = item["trade"]

            direction = trade[
                "direction"
            ]

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            lines.append("")

            lines.append(
                f"{emoji} "
                f"{trade['symbol']} "
                f"{direction}"
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(trade['entry'])}"
            )

            lines.append(
                f"Current: "
                f"{fmt_price(item['current'])}"
            )

            lines.append(
                f"P/L: "
                f"{fmt_pct(item['pnl'])}"
            )

            lines.append(
                f"SL: "
                f"{fmt_price(trade['sl'])} "
                f"(-{item['sl_pct']:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{fmt_price(trade['tp'])} "
                f"(+{item['tp_pct']:.2f}%)"
            )

            lines.append(
                f"RR: "
                f"{float(trade['rr'] or 0):.2f}"
            )

            lines.append(
                f"SL Source: "
                f"{trade['sl_source'] or '-'}"
            )

            lines.append(
                f"TP Source: "
                f"{trade['tp_source'] or '-'}"
            )

            lines.append(
                f"Opened: "
                f"{fmt_time(trade['signal_time'])}"
            )

    else:
        lines.append(
            "No open trades."
        )

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Closed Trades: "
        f"{stats['total']}"
    )

    lines.append(
        f"Wins: "
        f"{stats['wins']}"
    )

    lines.append(
        f"Losses: "
        f"{stats['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['win_rate']:.2f}%"
    )

    lines.append(
        f"Closed PnL: "
        f"{fmt_pct(stats['closed_pnl'])}"
    )

    lines.append(
        f"Open PnL: "
        f"{fmt_pct(stats['open_pnl'])}"
    )

    lines.append(
        f"TOTAL PnL: "
        f"{fmt_pct(stats['total_pnl'])}"
    )

    lines.append("")

    lines.append(
        "📌 PAPER ONLY"
    )

    return "\n".join(lines)


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(
    asset,
):
    symbol = SYMBOL_MAP[
        asset
    ]

    candles_1h_raw = fetch_candles(
        symbol,
        TF_1H,
        COUNT_1H,
    )

    candles_5m_raw = fetch_candles(
        symbol,
        TF_5M,
        COUNT_5M,
    )

    candles_1h = filter_closed_candles(
        candles_1h_raw,
        60,
    )

    candles_5m = filter_closed_candles(
        candles_5m_raw,
        5,
    )

    if (
        len(candles_1h) < 100
        or len(candles_5m) < 100
    ):
        print(
            f"{asset}: insufficient candles"
        )

        return {
            "asset": asset,
            "symbol": symbol,
            "candles_1h": candles_1h,
            "candles_5m": candles_5m,
            "signal": None,
            "ichi_5m": None,
        }

    signal = detect_signal(
        candles_1h,
        candles_5m,
    )

    ichi_5m = calculate_ichimoku(
        candles_5m
    )

    return {
        "asset": asset,
        "symbol": symbol,
        "candles_1h": candles_1h,
        "candles_5m": candles_5m,
        "signal": signal,
        "ichi_5m": ichi_5m,
    }


# ============================================================
# RUN ONE COMPLETE SCAN
# ============================================================

def run_scan():
    init_db()

    market_data = {}

    trend_counts = {
        "LONG": 0,
        "SHORT": 0,
    }

    new_signals = []

    print("=" * 70)
    print(
        "KRAKEN ICHIMOKU 40 "
        "PAPER SCANNER"
    )
    print("=" * 70)

    for asset in ASSETS:

        print(
            f"\nScanning {asset}..."
        )

        try:
            result = scan_asset(
                asset
            )

            symbol = result[
                "symbol"
            ]

            market_data[
                symbol
            ] = {
                "candles_1h":
                    result["candles_1h"],
                "candles_5m":
                    result["candles_5m"],
                "ichi_5m":
                    result["ichi_5m"],
            }

            signal = result[
                "signal"
            ]

            if signal is None:
                continue

            direction = signal[
                "direction"
            ]

            trend_counts[
                direction
            ] += 1

            # One open trade per
            # asset + direction.
            if has_open_trade(
                symbol,
                direction,
            ):
                print(
                    f"{asset}: "
                    f"{direction} already open"
                )

                continue

            inserted = insert_trade(
                asset=asset,
                symbol=symbol,
                direction=direction,
                signal_time=signal[
                    "signal_time"
                ],
                entry=signal[
                    "entry"
                ],
                sl=signal[
                    "sl"
                ],
                tp=signal[
                    "tp"
                ],
                rr=signal[
                    "rr"
                ],
                sl_source=signal[
                    "sl_source"
                ],
                tp_source=signal[
                    "tp_source"
                ],
            )

            if not inserted:
                print(
                    f"{asset}: "
                    f"signal already in DB"
                )

                continue

            print(
                f"{asset}: NEW "
                f"{direction} | "
                f"Entry "
                f"{fmt_price(signal['entry'])} | "
                f"SL "
                f"{fmt_price(signal['sl'])} | "
                f"TP "
                f"{fmt_price(signal['tp'])} | "
                f"RR "
                f"{signal['rr']:.2f}"
            )

            new_item = {
                "asset": asset,
                "direction": direction,
                "entry": signal[
                    "entry"
                ],
                "sl": signal[
                    "sl"
                ],
                "tp": signal[
                    "tp"
                ],
                "rr": signal[
                    "rr"
                ],
                "sl_source":
                    signal["sl_source"],
                "tp_source":
                    signal["tp_source"],
            }

            new_signals.append(
                new_item
            )

            # Send immediately.
            send_new_signal(
                asset=asset,
                signal=signal,
                candles=result[
                    "candles_5m"
                ],
            )

        except Exception as exc:
            print(
                f"{asset}: ERROR: "
                f"{exc}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Process existing open trades
    # --------------------------------------------------------

    closed = process_open_trades(
        market_data
    )

    print(
        f"\nClosed trades this run: "
        f"{len(closed)}"
    )

    # --------------------------------------------------------
    # Build current open details
    # --------------------------------------------------------

    open_details = build_open_details(
        market_data
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report = build_report(
        trend_counts=trend_counts,
        new_signals=new_signals,
        open_details=open_details,
    )

    print("\n" + report)

    send_telegram_message(
        report
    )

    print(
        "\nScan completed."
    )


# ============================================================
# MAIN
# ============================================================

def main():
    try:
        run_scan()

    except Exception as exc:
        print(
            f"FATAL ERROR: {exc}"
        )

        traceback.print_exc()

        send_telegram_message(
            "❌ ICHIMOKU SCANNER ERROR\n\n"
            f"{type(exc).__name__}: {exc}"
        )

        raise


if __name__ == "__main__":
    main()
