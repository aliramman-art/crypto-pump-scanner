# ============================================================
# KRAKEN ICHIMOKU 40 LIVE SCANNER
# VERSION 2.0
# ============================================================
#
# PAPER ONLY
#
# IMPORTANT SIGNAL RULE:
#
# ONLY THE MOST RECENT CLOSED 5M CANDLE CAN CREATE A SIGNAL.
#
# LONG:
#   1H bullish Ichimoku trend
#   AND
#   latest closed 5M candle closes ABOVE cloud
#   AND
#   Chikou crosses ABOVE price ON THAT SAME 5M CANDLE
#
# SHORT:
#   1H bearish Ichimoku trend
#   AND
#   latest closed 5M candle closes BELOW cloud
#   AND
#   Chikou crosses BELOW price ON THAT SAME 5M CANDLE
#
# NO HISTORICAL SIGNAL REPLAY
# NO LOOKBACK SIGNAL SEARCH
#
# SL / TP:
#   Kijun
#   Senkou A
#   Senkou B
#   Confirmed swings
#
# TENKAN IS NOT USED FOR SL/TP
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


# ============================================================
# CONFIG
# ============================================================

PAPER_TRADING = True
REAL_TRADING = False

DB_FILE = "kraken_ichimoku_40.db"

CHART_BASE = "https://futures.kraken.com/api/charts/v1"

TF_1H = "1h"
TF_5M = "5m"

COUNT_1H = 300
COUNT_5M = 500

ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
ICHIMOKU_DISPLACEMENT = 26

SWING_LEFT = 2
SWING_RIGHT = 2

MIN_RR = 1.0

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REQUEST_TIMEOUT = 20


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
# SYMBOL
# ============================================================

def symbol_for(asset):
    return f"PF_{asset}USD"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send_message(text):
    if not telegram_enabled():
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
        }

        r = requests.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )

        return r.ok

    except Exception as e:
        print(f"Telegram message error: {e}")
        return False


def telegram_send_photo(path, caption):
    if not telegram_enabled():
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(path, "rb") as f:
            files = {
                "photo": f,
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
            }

            r = requests.post(
                url,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT,
            )

        return r.ok

    except Exception as e:
        print(f"Telegram photo error: {e}")
        return False


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    cur = conn.execute(f"PRAGMA table_info({table})")
    columns = {row["name"] for row in cur.fetchall()}

    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    conn = get_db()

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
        idx_trade_unique_signal
        ON trades(symbol, direction, signal_time)
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def format_time(ts):
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


# ============================================================
# KRAKEN DATA
# ============================================================

def normalize_candles(raw):
    """
    Kraken chart endpoint can return candle data in
    different structures depending on endpoint/version.
    """

    if raw is None:
        return []

    data = raw

    if isinstance(raw, dict):

        for key in (
            "candles",
            "data",
            "result",
            "results",
        ):
            if key in raw:
                data = raw[key]
                break

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "results",
        ):
            if key in data:
                data = data[key]
                break

    if not isinstance(data, list):
        return []

    output = []

    for item in data:

        try:

            if isinstance(item, dict):

                ts = (
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("ts")
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

            if ts is None:
                continue

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000.0

            row = {
                "timestamp": int(ts),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }

            output.append(row)

        except Exception:
            continue

    output.sort(key=lambda x: x["timestamp"])

    return output


def fetch_candles(symbol, timeframe, count):
    url = (
        f"{CHART_BASE}/trade/"
        f"{symbol}/"
        f"{timeframe}"
    )

    try:

        r = requests.get(
            url,
            params={"count": count},
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        raw = r.json()

        candles = normalize_candles(raw)

        return candles

    except Exception as e:

        print(
            f"ERROR fetching {symbol} {timeframe}: {e}"
        )

        return []


# ============================================================
# REMOVE CURRENT UNFINISHED CANDLE
# ============================================================

def filter_closed_candles(candles, timeframe_minutes):
    if not candles:
        return []

    interval = timeframe_minutes * 60
    current = now_ts()

    closed = []

    for candle in candles:

        ts = int(candle["timestamp"])

        if ts + interval <= current:
            closed.append(candle)

    return closed


# ============================================================
# ICHIMOKU
# ============================================================

def midpoint(values):
    if not values:
        return None

    return (
        max(values) + min(values)
    ) / 2.0


def calculate_ichimoku(candles):
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

        if i + 1 >= ICHIMOKU_TENKAN:
            tenkan[i] = midpoint(
                highs[
                    i + 1 - ICHIMOKU_TENKAN:
                    i + 1
                ]
            )

        if i + 1 >= ICHIMOKU_KIJUN:
            kijun[i] = midpoint(
                highs[
                    i + 1 - ICHIMOKU_KIJUN:
                    i + 1
                ]
            )

        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):
            senkou_a_raw[i] = (
                tenkan[i] + kijun[i]
            ) / 2.0

        if i + 1 >= ICHIMOKU_SENKOU_B:
            senkou_b_raw[i] = midpoint(
                highs[
                    i + 1 - ICHIMOKU_SENKOU_B:
                    i + 1
                ]
            )

    result = []

    for i in range(n):

        result.append(
            {
                "tenkan": tenkan[i],
                "kijun": kijun[i],
                "senkou_a_raw": senkou_a_raw[i],
                "senkou_b_raw": senkou_b_raw[i],
                "close": closes[i],
            }
        )

    return result


def visible_cloud_at(ichi, index):
    """
    At candle index t, the visible cloud comes from
    raw Senkou values calculated at t - 26.
    """

    source_index = (
        index - ICHIMOKU_DISPLACEMENT
    )

    if source_index < 0:
        return None, None

    if source_index >= len(ichi):
        return None, None

    a = ichi[source_index]["senkou_a_raw"]
    b = ichi[source_index]["senkou_b_raw"]

    if a is None or b is None:
        return None, None

    return a, b


def cloud_values(ichi, index):
    a, b = visible_cloud_at(
        ichi,
        index,
    )

    if a is None or b is None:
        return None, None, None

    top = max(a, b)
    bottom = min(a, b)

    return top, bottom, a - b


# ============================================================
# 1H TREND
# ============================================================

def get_1h_trend(candles):
    if len(candles) < 100:
        return None

    ichi = calculate_ichimoku(candles)

    i = len(candles) - 1

    close = candles[i]["close"]

    top, bottom, cloud_direction = cloud_values(
        ichi,
        i,
    )

    if top is None or bottom is None:
        return None

    if (
        close > top
        and cloud_direction > 0
    ):
        return "LONG"

    if (
        close < bottom
        and cloud_direction < 0
    ):
        return "SHORT"

    return None


# ============================================================
# CRITICAL NEW SIGNAL LOGIC
# ============================================================

def latest_5m_signal(
    candles,
    ichi,
    trend_1h,
):
    """
    IMPORTANT:

    ONLY candles[-1] is allowed to generate a signal.

    We DO NOT scan historical candles.

    The latest closed candle itself must:
        1. Close outside cloud.
        2. Have Chikou crossing price on SAME candle.

    Chikou at candle t:
        close[t]

    Compared with price at:
        t - 26

    Previous Chikou:
        close[t-1]

    Previous comparison price:
        close[t-27]
    """

    if trend_1h not in ("LONG", "SHORT"):
        return None

    n = len(candles)

    if n < 60:
        return None

    # ========================================================
    # ONLY THE LAST CLOSED CANDLE
    # ========================================================

    i = n - 1

    # --------------------------------------------------------
    # Never inspect an older candle for signal generation.
    # --------------------------------------------------------

    current = candles[i]

    current_ts = int(
        current["timestamp"]
    )

    # --------------------------------------------------------
    # Cloud of latest candle
    # --------------------------------------------------------

    cloud_top, cloud_bottom, cloud_dir = cloud_values(
        ichi,
        i,
    )

    if (
        cloud_top is None
        or cloud_bottom is None
    ):
        return None

    close_now = float(
        candles[i]["close"]
    )

    close_prev = float(
        candles[i - 1]["close"]
    )

    # ========================================================
    # CHIKOU CROSS ON THE SAME CANDLE
    # ========================================================

    chikou_source = i - ICHIMOKU_DISPLACEMENT
    previous_chikou_source = (
        i - 1 - ICHIMOKU_DISPLACEMENT
    )

    if chikou_source < 0:
        return None

    if previous_chikou_source < 0:
        return None

    price_now = float(
        candles[chikou_source]["close"]
    )

    price_previous = float(
        candles[previous_chikou_source]["close"]
    )

    bullish_chikou_cross = (
        close_now > price_now
        and close_prev <= price_previous
    )

    bearish_chikou_cross = (
        close_now < price_now
        and close_prev >= price_previous
    )

    # ========================================================
    # LONG
    # ========================================================

    if trend_1h == "LONG":

        close_outside_cloud = (
            close_now > cloud_top
        )

        if (
            close_outside_cloud
            and cloud_dir > 0
            and bullish_chikou_cross
        ):

            return {
                "direction": "LONG",
                "signal_time": current_ts,
                "entry": close_now,
                "index": i,
                "cloud_top": cloud_top,
                "cloud_bottom": cloud_bottom,
                "cloud_direction": cloud_dir,
            }

    # ========================================================
    # SHORT
    # ========================================================

    if trend_1h == "SHORT":

        close_outside_cloud = (
            close_now < cloud_bottom
        )

        if (
            close_outside_cloud
            and cloud_dir < 0
            and bearish_chikou_cross
        ):

            return {
                "direction": "SHORT",
                "signal_time": current_ts,
                "entry": close_now,
                "index": i,
                "cloud_top": cloud_top,
                "cloud_bottom": cloud_bottom,
                "cloud_direction": cloud_dir,
            }

    return None


# ============================================================
# SWINGS
# ============================================================

def confirmed_swings(candles):
    highs = []
    lows = []

    n = len(candles)

    left = SWING_LEFT
    right = SWING_RIGHT

    for i in range(
        left,
        n - right,
    ):

        h = candles[i]["high"]
        l = candles[i]["low"]

        left_highs = [
            candles[j]["high"]
            for j in range(
                i - left,
                i,
            )
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + right + 1,
            )
        ]

        left_lows = [
            candles[j]["low"]
            for j in range(
                i - left,
                i,
            )
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + right + 1,
            )
        ]

        if (
            all(h > x for x in left_highs)
            and all(h >= x for x in right_highs)
        ):
            highs.append(
                {
                    "index": i,
                    "price": h,
                    "source": "Swing High",
                }
            )

        if (
            all(l < x for x in left_lows)
            and all(l <= x for x in right_lows)
        ):
            lows.append(
                {
                    "index": i,
                    "price": l,
                    "source": "Swing Low",
                }
            )

    return highs, lows


# ============================================================
# LEVEL CANDIDATES
# ============================================================

def build_level_candidates(
    candles,
    ichi,
    entry_index,
):
    supports = []
    resistances = []

    # --------------------------------------------------------
    # Ichimoku levels
    #
    # TENKAN IS INTENTIONALLY EXCLUDED.
    # --------------------------------------------------------

    for idx in range(
        max(0, entry_index - 100),
        entry_index + 1,
    ):

        k = ichi[idx]["kijun"]

        if k is not None:

            supports.append(
                (
                    float(k),
                    "Kijun",
                )
            )

            resistances.append(
                (
                    float(k),
                    "Kijun",
                )
            )

        a, b = visible_cloud_at(
            ichi,
            idx,
        )

        if a is not None:
            supports.append(
                (
                    float(a),
                    "Senkou A",
                )
            )

            resistances.append(
                (
                    float(a),
                    "Senkou A",
                )
            )

        if b is not None:
            supports.append(
                (
                    float(b),
                    "Senkou B",
                )
            )

            resistances.append(
                (
                    float(b),
                    "Senkou B",
                )
            )

    # --------------------------------------------------------
    # Confirmed swings only
    # --------------------------------------------------------

    highs, lows = confirmed_swings(candles)

    for swing in highs:

        if swing["index"] <= entry_index:
            resistances.append(
                (
                    float(swing["price"]),
                    swing["source"],
                )
            )

    for swing in lows:

        if swing["index"] <= entry_index:
            supports.append(
                (
                    float(swing["price"]),
                    swing["source"],
                )
            )

    return supports, resistances


# ============================================================
# SL / TP
# ============================================================

def select_sl_tp(
    candles,
    ichi,
    entry,
    direction,
    entry_index,
):
    supports, resistances = build_level_candidates(
        candles,
        ichi,
        entry_index,
    )

    if direction == "LONG":

        support_candidates = [
            x for x in supports
            if x[0] < entry
        ]

        if not support_candidates:
            return None

        support_candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        sl, sl_source = support_candidates[0]

        resistance_candidates = [
            x for x in resistances
            if x[0] > entry
        ]

        if not resistance_candidates:
            return None

        resistance_candidates.sort(
            key=lambda x: x[0]
        )

        selected_tp = None

        for tp, tp_source in resistance_candidates:

            risk = entry - sl
            reward = tp - entry

            if risk <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                selected_tp = (
                    tp,
                    tp_source,
                    rr,
                )

                break

        if selected_tp is None:
            return None

        tp, tp_source, rr = selected_tp

        return {
            "sl": float(sl),
            "tp": float(tp),
            "rr": float(rr),
            "sl_source": sl_source,
            "tp_source": tp_source,
        }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        resistance_candidates = [
            x for x in resistances
            if x[0] > entry
        ]

        if not resistance_candidates:
            return None

        resistance_candidates.sort(
            key=lambda x: x[0]
        )

        sl, sl_source = resistance_candidates[0]

        support_candidates = [
            x for x in supports
            if x[0] < entry
        ]

        if not support_candidates:
            return None

        support_candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        selected_tp = None

        for tp, tp_source in support_candidates:

            risk = sl - entry
            reward = entry - tp

            if risk <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                selected_tp = (
                    tp,
                    tp_source,
                    rr,
                )

                break

        if selected_tp is None:
            return None

        tp, tp_source, rr = selected_tp

        return {
            "sl": float(sl),
            "tp": float(tp),
            "rr": float(rr),
            "sl_source": sl_source,
            "tp_source": tp_source,
        }

    return None


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(
    conn,
    symbol,
    direction,
):
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

    return row is not None


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_trade(
    conn,
    asset,
    symbol,
    signal,
    levels,
):
    try:

        cur = conn.execute(
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
                signal["direction"],
                signal["signal_time"],
                signal["entry"],
                levels["sl"],
                levels["tp"],
                levels["rr"],
                levels["sl_source"],
                levels["tp_source"],
            ),
        )

        conn.commit()

        return cur.rowcount == 1

    except Exception as e:

        print(
            f"Insert trade error {symbol}: {e}"
        )

        return False


# ============================================================
# CLOSE OPEN TRADES
# ============================================================

def process_open_trades(
    conn,
    symbol,
    latest_candle,
):
    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE symbol = ?
          AND status = 'OPEN'
        ORDER BY id ASC
        """,
        (symbol,),
    ).fetchall()

    closed = 0

    for trade in rows:

        direction = trade["direction"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        high = float(
            latest_candle["high"]
        )

        low = float(
            latest_candle["low"]
        )

        exit_price = None
        reason = None

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl:
                exit_price = sl
                reason = "SL"

            elif hit_tp:
                exit_price = tp
                reason = "TP"

            if exit_price is not None:
                pnl = (
                    (exit_price - entry)
                    / entry
                    * 100.0
                )

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl:
                exit_price = sl
                reason = "SL"

            elif hit_tp:
                exit_price = tp
                reason = "TP"

            if exit_price is not None:
                pnl = (
                    (entry - exit_price)
                    / entry
                    * 100.0
                )

        if exit_price is not None:

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
                    int(
                        latest_candle[
                            "timestamp"
                        ]
                    ),
                    reason,
                    pnl,
                    trade["id"],
                ),
            )

            closed += 1

            emoji = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            telegram_send_message(
                f"{emoji} TRADE CLOSED\n\n"
                f"{symbol} "
                f"{direction}\n"
                f"Reason: {reason}\n"
                f"Entry: {entry:g}\n"
                f"Exit: {exit_price:g}\n"
                f"PnL: {pnl:+.2f}%\n\n"
                f"📌 PAPER ONLY"
            )

    conn.commit()

    return closed


# ============================================================
# CHART
# ============================================================

def make_signal_chart(
    asset,
    candles,
    ichi,
    signal,
    levels,
):
    try:

        last_count = min(
            120,
            len(candles),
        )

        start = (
            len(candles)
            - last_count
        )

        data = candles[start:]

        times = [
            datetime.fromtimestamp(
                c["timestamp"],
                tz=timezone.utc,
            )
            for c in data
        ]

        closes = [
            c["close"]
            for c in data
        ]

        highs = [
            c["high"]
            for c in data
        ]

        lows = [
            c["low"]
            for c in data
        ]

        plt.figure(
            figsize=(14, 8)
        )

        plt.plot(
            times,
            closes,
            label="Close",
        )

        tenkan = []
        kijun = []
        senkou_a = []
        senkou_b = []

        for absolute_index in range(
            start,
            len(candles),
        ):

            row = ichi[absolute_index]

            tenkan.append(
                row["tenkan"]
            )

            kijun.append(
                row["kijun"]
            )

            a, b = visible_cloud_at(
                ichi,
                absolute_index,
            )

            senkou_a.append(a)
            senkou_b.append(b)

        plt.plot(
            times,
            tenkan,
            label="Tenkan",
        )

        plt.plot(
            times,
            kijun,
            label="Kijun",
        )

        plt.plot(
            times,
            senkou_a,
            label="Senkou A",
        )

        plt.plot(
            times,
            senkou_b,
            label="Senkou B",
        )

        try:

            plt.fill_between(
                times,
                senkou_a,
                senkou_b,
                where=[
                    (
                        a is not None
                        and b is not None
                    )
                    for a, b in zip(
                        senkou_a,
                        senkou_b,
                    )
                ],
                alpha=0.15,
            )

        except Exception:
            pass

        entry = signal["entry"]
        sl = levels["sl"]
        tp = levels["tp"]

        plt.axhline(
            entry,
            linestyle="--",
            label=f"Entry {entry:g}",
        )

        plt.axhline(
            sl,
            linestyle="--",
            label=f"SL {sl:g}",
        )

        plt.axhline(
            tp,
            linestyle="--",
            label=f"TP {tp:g}",
        )

        # ----------------------------------------------------
        # Confirmed swings
        # ----------------------------------------------------

        highs_swings, lows_swings = confirmed_swings(
            candles
        )

        for swing in highs_swings:

            if start <= swing["index"] < len(candles):

                plt.scatter(
                    times[
                        swing["index"] - start
                    ],
                    swing["price"],
                    marker="^",
                    s=35,
                )

        for swing in lows_swings:

            if start <= swing["index"] < len(candles):

                plt.scatter(
                    times[
                        swing["index"] - start
                    ],
                    swing["price"],
                    marker="v",
                    s=35,
                )

        plt.title(
            f"{asset} 5M Ichimoku Signal - "
            f"{signal['direction']}"
        )

        plt.legend(
            loc="best"
        )

        plt.grid(
            alpha=0.2
        )

        plt.gca().xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%m-%d %H:%M",
                tz=timezone.utc,
            )
        )

        plt.xticks(
            rotation=30
        )

        plt.tight_layout()

        path = (
            f"ichimoku_signal_{asset}.png"
        )

        plt.savefig(
            path,
            dpi=140,
        )

        plt.close()

        return path

    except Exception as e:

        print(
            f"Chart error {asset}: {e}"
        )

        try:
            plt.close()
        except Exception:
            pass

        return None


# ============================================================
# NEW SIGNAL TELEGRAM
# ============================================================

def send_new_signal(
    asset,
    signal,
    levels,
    candles,
    ichi,
):
    direction = signal["direction"]

    emoji = (
        "🟢 LONG"
        if direction == "LONG"
        else "🔴 SHORT"
    )

    entry = signal["entry"]
    sl = levels["sl"]
    tp = levels["tp"]
    rr = levels["rr"]

    if direction == "LONG":

        sl_pct = (
            (entry - sl)
            / entry
            * 100
        )

        tp_pct = (
            (tp - entry)
            / entry
            * 100
        )

    else:

        sl_pct = (
            (sl - entry)
            / entry
            * 100
        )

        tp_pct = (
            (entry - tp)
            / entry
            * 100
        )

    text = (
        f"{emoji} NEW ICHIMOKU SIGNAL\n\n"
        f"PF_{asset}USD\n\n"
        f"Entry: {entry:g}\n"
        f"SL: {sl:g} "
        f"(-{sl_pct:.2f}%)\n"
        f"TP: {tp:g} "
        f"(+{tp_pct:.2f}%)\n"
        f"RR: {rr:.2f}\n\n"
        f"SL Source: "
        f"{levels['sl_source']}\n"
        f"TP Source: "
        f"{levels['tp_source']}\n\n"
        f"5M candle: "
        f"{format_time(signal['signal_time'])}\n\n"
        f"📌 PAPER ONLY"
    )

    chart = make_signal_chart(
        asset,
        candles,
        ichi,
        signal,
        levels,
    )

    if chart:

        sent = telegram_send_photo(
            chart,
            text,
        )

        try:
            os.remove(chart)
        except Exception:
            pass

        if sent:
            return

    telegram_send_message(
        text
    )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def build_open_details(conn):
    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
        """
    ).fetchall()

    if not rows:
        return "📌 OPEN TRADES: 0"

    lines = [
        f"📌 OPEN TRADES: {len(rows)}",
        "",
    ]

    current_cache = {}

    for trade in rows:

        symbol = trade["symbol"]

        if symbol not in current_cache:

            candles = fetch_candles(
                symbol,
                TF_5M,
                10,
            )

            candles = filter_closed_candles(
                candles,
                5,
            )

            if candles:
                current_cache[symbol] = candles[-1]["close"]
            else:
                current_cache[symbol] = None

        current = current_cache[symbol]

        if current is None:
            current = trade["entry"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        direction = trade["direction"]

        if direction == "LONG":

            pnl = (
                (current - entry)
                / entry
                * 100
            )

            sl_distance = (
                (entry - sl)
                / entry
                * 100
            )

            tp_distance = (
                (tp - entry)
                / entry
                * 100
            )

            emoji = "🟢"

        else:

            pnl = (
                (entry - current)
                / entry
                * 100
            )

            sl_distance = (
                (sl - entry)
                / entry
                * 100
            )

            tp_distance = (
                (entry - tp)
                / entry
                * 100
            )

            emoji = "🔴"

        lines.append(
            f"{emoji} {symbol} "
            f"{direction}"
        )

        lines.append(
            f"Entry: {entry:g}"
        )

        lines.append(
            f"Current: {current:g}"
        )

        lines.append(
            f"P/L: {pnl:+.2f}%"
        )

        lines.append(
            f"SL: {sl:g} "
            f"({sl_distance:.2f}%)"
        )

        lines.append(
            f"TP: {tp:g} "
            f"({tp_distance:.2f}%)"
        )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats(conn):
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
                    WHEN pnl_pct <= 0
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

    decided = wins + losses

    if decided > 0:
        win_rate = (
            wins
            / decided
            * 100
        )
    else:
        win_rate = 0.0

    open_row = conn.execute(
        """
        SELECT
            COALESCE(
                SUM(pnl_pct),
                0
            ) AS open_pnl
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()

    open_pnl = float(
        open_row["open_pnl"] or 0
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
    conn,
    new_long,
    new_short,
):
    stats = performance_stats(
        conn
    )

    open_details = build_open_details(
        conn
    )

    text = (
        "📊 1H ICHIMOKU REPORT\n\n"
        f"Assets scanned: "
        f"{len(ASSETS)}\n"
        f"🟢 New LONG: "
        f"{new_long}\n"
        f"🔴 New SHORT: "
        f"{new_short}\n\n"
        f"{open_details}\n\n"
        f"📈 PERFORMANCE\n"
        f"Closed trades: "
        f"{stats['total']}\n"
        f"Wins: "
        f"{stats['wins']}\n"
        f"Losses: "
        f"{stats['losses']}\n"
        f"Win rate: "
        f"{stats['win_rate']:.2f}%\n"
        f"Closed PnL: "
        f"{stats['closed_pnl']:+.2f}%\n"
        f"Open PnL: "
        f"{stats['open_pnl']:+.2f}%\n"
        f"Total PnL: "
        f"{stats['total_pnl']:+.2f}%\n\n"
        f"📌 PAPER ONLY"
    )

    return text


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(
    conn,
    asset,
):
    symbol = symbol_for(
        asset
    )

    print(
        f"Scanning {asset}..."
    )

    # ========================================================
    # 1H
    # ========================================================

    candles_1h = fetch_candles(
        symbol,
        TF_1H,
        COUNT_1H,
    )

    candles_1h = filter_closed_candles(
        candles_1h,
        60,
    )

    if len(candles_1h) < 100:

        print(
            f"{asset}: insufficient 1H data"
        )

        return {
            "new": None,
            "closed": 0,
        }

    trend = get_1h_trend(
        candles_1h
    )

    # ========================================================
    # 5M
    # ========================================================

    candles_5m = fetch_candles(
        symbol,
        TF_5M,
        COUNT_5M,
    )

    candles_5m = filter_closed_candles(
        candles_5m,
        5,
    )

    if len(candles_5m) < 100:

        print(
            f"{asset}: insufficient 5M data"
        )

        return {
            "new": None,
            "closed": 0,
        }

    ichi_5m = calculate_ichimoku(
        candles_5m
    )

    # ========================================================
    # CLOSE EXISTING TRADES
    #
    # Only latest CLOSED candle is used.
    # ========================================================

    closed = process_open_trades(
        conn,
        symbol,
        candles_5m[-1],
    )

    # ========================================================
    # NEW SIGNAL
    #
    # CRITICAL:
    #
    # latest_5m_signal() checks ONLY candles_5m[-1].
    #
    # No historical signal replay.
    # ========================================================

    signal = latest_5m_signal(
        candles_5m,
        ichi_5m,
        trend,
    )

    if signal is None:

        print(
            f"{asset}: no new signal"
        )

        return {
            "new": None,
            "closed": closed,
        }

    direction = signal["direction"]

    # ========================================================
    # OPEN TRADE CHECK
    # ========================================================

    if has_open_trade(
        conn,
        symbol,
        direction,
    ):

        print(
            f"{asset}: "
            f"{direction} already open"
        )

        return {
            "new": None,
            "closed": closed,
        }

    # ========================================================
    # SL / TP
    # ========================================================

    levels = select_sl_tp(
        candles_5m,
        ichi_5m,
        signal["entry"],
        direction,
        signal["index"],
    )

    if levels is None:

        print(
            f"{asset}: "
            f"{direction} signal found "
            f"but valid SL/TP not found"
        )

        return {
            "new": None,
            "closed": closed,
        }

    # ========================================================
    # INSERT
    # ========================================================

    inserted = insert_trade(
        conn,
        asset,
        symbol,
        signal,
        levels,
    )

    if not inserted:

        print(
            f"{asset}: "
            f"signal already exists"
        )

        return {
            "new": None,
            "closed": closed,
        }

    # ========================================================
    # TELEGRAM IMMEDIATELY
    # ========================================================

    send_new_signal(
        asset,
        signal,
        levels,
        candles_5m,
        ichi_5m,
    )

    print(
        f"{asset}: "
        f"NEW {direction} "
        f"Entry={signal['entry']}"
    )

    return {
        "new": direction,
        "closed": closed,
    }


# ============================================================
# RUN ONE SCAN
# ============================================================

def run_scan():

    init_db()

    conn = get_db()

    new_long = 0
    new_short = 0

    total_closed = 0

    try:

        for asset in ASSETS:

            try:

                result = scan_asset(
                    conn,
                    asset,
                )

                if result["new"] == "LONG":
                    new_long += 1

                elif result["new"] == "SHORT":
                    new_short += 1

                total_closed += (
                    result["closed"]
                )

            except Exception as e:

                print(
                    f"ERROR scanning "
                    f"{asset}: {e}"
                )

                traceback.print_exc()

        conn.commit()

        print(
            f"Closed trades this run: "
            f"{total_closed}"
        )

        report = build_report(
            conn,
            new_long,
            new_short,
        )

        telegram_send_message(
            report
        )

        print(report)

    finally:

        conn.close()


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        "KRAKEN ICHIMOKU 40 LIVE SCANNER"
    )

    print(
        "PAPER ONLY"
    )

    print(
        "LATEST CLOSED 5M CANDLE ONLY"
    )

    print(
        "NO HISTORICAL SIGNAL REPLAY"
    )

    print(
        "=================================================="
    )

    run_scan()


if __name__ == "__main__":
    main()
