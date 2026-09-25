# ============================================================
# KRAKEN ICHIMOKU 40 LIVE SCANNER
# VERSION 3.0
# ============================================================
#
# PAPER ONLY
#
# STRATEGY
#
# 1H:
#   LONG:
#       Descending swing-high trendline
#       Latest closed 1H candle breaks ABOVE it
#
#   SHORT:
#       Ascending swing-low trendline
#       Latest closed 1H candle breaks BELOW it
#
# 15M:
#   LONG:
#       1H direction = LONG
#       Latest closed 15M candle crosses ABOVE cloud
#       Chikou Span is ABOVE price
#
#   SHORT:
#       1H direction = SHORT
#       Latest closed 15M candle crosses BELOW cloud
#       Chikou Span is BELOW price
#
# ONLY THE MOST RECENT CLOSED 15M CANDLE
# CAN CREATE A NEW SIGNAL.
#
# NO 5M TIMEFRAME.
#
# NO HISTORICAL SIGNAL REPLAY.
#
# SL / TP:
#   Kijun
#   Senkou A
#   Senkou B
#   Confirmed swings
#
# TENKAN IS NOT USED FOR SL / TP.
#
# CHART:
#   Real candles
#   Tenkan = BLUE
#   Kijun = RED
#   Senkou A = GREEN
#   Senkou B = RED
#   Chikou Span = GREEN
#   Bullish cloud = GREEN
#   Bearish cloud = RED
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

PAPER_TRADING = True
REAL_TRADING = False

DB_FILE = "kraken_ichimoku_40.db"

CHART_BASE = "https://futures.kraken.com/api/charts/v1"

TF_1H = "1h"
TF_15M = "15m"

COUNT_1H = 300
COUNT_15M = 500

ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
ICHIMOKU_DISPLACEMENT = 26

SWING_LEFT = 2
SWING_RIGHT = 2

MIN_RR = 1.0

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
)

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
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


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

        print(
            f"Telegram message error: {e}"
        )

        return False


def telegram_send_photo(
    path,
    caption,
):
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

        print(
            f"Telegram photo error: {e}"
        )

        return False


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def ensure_column(
    conn,
    table,
    column,
    definition,
):

    cur = conn.execute(
        f"PRAGMA table_info({table})"
    )

    columns = {
        row["name"]
        for row in cur.fetchall()
    }

    if column not in columns:

        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} "
            f"{definition}"
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
        ON trades(
            symbol,
            direction,
            signal_time
        )
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

        return dt.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:

        return "-"


# ============================================================
# KRAKEN DATA
# ============================================================

def normalize_candles(raw):

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

            elif isinstance(
                item,
                (list, tuple),
            ):

                if len(item) < 5:
                    continue

                ts = item[0]
                o = item[1]
                h = item[2]
                l = item[3]
                c = item[4]

                v = (
                    item[5]
                    if len(item) > 5
                    else 0
                )

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

    output.sort(
        key=lambda x: x["timestamp"]
    )

    return output


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

        r = requests.get(
            url,
            params={
                "count": count
            },
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        raw = r.json()

        return normalize_candles(
            raw
        )

    except Exception as e:

        print(
            f"ERROR fetching "
            f"{symbol} {timeframe}: {e}"
        )

        return []


# ============================================================
# REMOVE CURRENT UNFINISHED CANDLE
# ============================================================

def filter_closed_candles(
    candles,
    timeframe_minutes,
):

    if not candles:
        return []

    interval = (
        timeframe_minutes * 60
    )

    current = now_ts()

    closed = []

    for candle in candles:

        ts = int(
            candle["timestamp"]
        )

        if ts + interval <= current:

            closed.append(
                candle
            )

    return closed


# ============================================================
# ICHIMOKU
# ============================================================

def midpoint(values):

    if not values:
        return None

    return (
        max(values)
        + min(values)
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

        if (
            i + 1
            >= ICHIMOKU_TENKAN
        ):

            tenkan[i] = midpoint(
                highs[
                    i + 1
                    - ICHIMOKU_TENKAN:
                    i + 1
                ]
                +
                lows[
                    i + 1
                    - ICHIMOKU_TENKAN:
                    i + 1
                ]
            )

        if (
            i + 1
            >= ICHIMOKU_KIJUN
        ):

            kijun[i] = midpoint(
                highs[
                    i + 1
                    - ICHIMOKU_KIJUN:
                    i + 1
                ]
                +
                lows[
                    i + 1
                    - ICHIMOKU_KIJUN:
                    i + 1
                ]
            )

        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):

            senkou_a_raw[i] = (
                tenkan[i]
                + kijun[i]
            ) / 2.0

        if (
            i + 1
            >= ICHIMOKU_SENKOU_B
        ):

            senkou_b_raw[i] = midpoint(
                highs[
                    i + 1
                    - ICHIMOKU_SENKOU_B:
                    i + 1
                ]
                +
                lows[
                    i + 1
                    - ICHIMOKU_SENKOU_B:
                    i + 1
                ]
            )

    result = []

    for i in range(n):

        result.append(
            {
                "tenkan": tenkan[i],
                "kijun": kijun[i],
                "senkou_a_raw":
                    senkou_a_raw[i],
                "senkou_b_raw":
                    senkou_b_raw[i],
                "close": closes[i],
            }
        )

    return result


def visible_cloud_at(
    ichi,
    index,
):

    source_index = (
        index
        - ICHIMOKU_DISPLACEMENT
    )

    if source_index < 0:
        return None, None

    if source_index >= len(ichi):
        return None, None

    a = ichi[
        source_index
    ]["senkou_a_raw"]

    b = ichi[
        source_index
    ]["senkou_b_raw"]

    if a is None or b is None:
        return None, None

    return a, b


def cloud_values(
    ichi,
    index,
):

    a, b = visible_cloud_at(
        ichi,
        index,
    )

    if a is None or b is None:
        return (
            None,
            None,
            None,
        )

    top = max(a, b)
    bottom = min(a, b)

    return (
        top,
        bottom,
        a - b,
    )


# ============================================================
# CONFIRMED SWINGS
# ============================================================

def confirmed_swings(
    candles,
):

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
            all(
                h > x
                for x in left_highs
            )
            and
            all(
                h >= x
                for x in right_highs
            )
        ):

            highs.append(
                {
                    "index": i,
                    "price": h,
                    "source": "Swing High",
                }
            )

        if (
            all(
                l < x
                for x in left_lows
            )
            and
            all(
                l <= x
                for x in right_lows
            )
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
# 1H TRENDLINE
# ============================================================

def line_value(
    x1,
    y1,
    x2,
    y2,
    x,
):

    if x2 == x1:
        return None

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return (
        y1
        + slope * (
            x - x1
        )
    )


def get_1h_trendline_direction(
    candles,
):

    if len(candles) < 100:
        return None

    highs, lows = confirmed_swings(
        candles
    )

    current_index = (
        len(candles) - 1
    )

    previous_index = (
        current_index - 1
    )

    # ========================================================
    # LONG
    #
    # Need at least two confirmed lower highs.
    # Latest closed 1H candle must break above
    # the descending resistance line.
    # ========================================================

    if len(highs) >= 2:

        high_candidates = [
            x for x in highs
            if x["index"]
            <= previous_index
        ]

        if len(high_candidates) >= 2:

            h2 = high_candidates[-1]
            h1 = high_candidates[-2]

            if (
                h2["price"]
                < h1["price"]
            ):

                resistance_now = line_value(
                    h1["index"],
                    h1["price"],
                    h2["index"],
                    h2["price"],
                    current_index,
                )

                resistance_previous = line_value(
                    h1["index"],
                    h1["price"],
                    h2["index"],
                    h2["price"],
                    previous_index,
                )

                if (
                    resistance_now
                    is not None
                    and resistance_previous
                    is not None
                ):

                    current_close = (
                        candles[
                            current_index
                        ]["close"]
                    )

                    previous_close = (
                        candles[
                            previous_index
                        ]["close"]
                    )

                    fresh_break_long = (
                        previous_close
                        <= resistance_previous
                        and
                        current_close
                        > resistance_now
                    )

                    if fresh_break_long:

                        return {
                            "direction": "LONG",
                            "line_type":
                                "Descending Resistance",
                            "x1":
                                h1["index"],
                            "y1":
                                h1["price"],
                            "x2":
                                h2["index"],
                            "y2":
                                h2["price"],
                            "value":
                                resistance_now,
                        }

    # ========================================================
    # SHORT
    #
    # Need at least two confirmed higher lows.
    # Latest closed 1H candle must break below
    # the ascending support line.
    # ========================================================

    if len(lows) >= 2:

        low_candidates = [
            x for x in lows
            if x["index"]
            <= previous_index
        ]

        if len(low_candidates) >= 2:

            l2 = low_candidates[-1]
            l1 = low_candidates[-2]

            if (
                l2["price"]
                > l1["price"]
            ):

                support_now = line_value(
                    l1["index"],
                    l1["price"],
                    l2["index"],
                    l2["price"],
                    current_index,
                )

                support_previous = line_value(
                    l1["index"],
                    l1["price"],
                    l2["index"],
                    l2["price"],
                    previous_index,
                )

                if (
                    support_now
                    is not None
                    and support_previous
                    is not None
                ):

                    current_close = (
                        candles[
                            current_index
                        ]["close"]
                    )

                    previous_close = (
                        candles[
                            previous_index
                        ]["close"]
                    )

                    fresh_break_short = (
                        previous_close
                        >= support_previous
                        and
                        current_close
                        < support_now
                    )

                    if fresh_break_short:

                        return {
                            "direction": "SHORT",
                            "line_type":
                                "Ascending Support",
                            "x1":
                                l1["index"],
                            "y1":
                                l1["price"],
                            "x2":
                                l2["index"],
                            "y2":
                                l2["price"],
                            "value":
                                support_now,
                        }

    return None


# ============================================================
# 15M SIGNAL
# ============================================================

def latest_15m_signal(
    candles,
    ichi,
    trend_1h,
):

    if trend_1h not in (
        "LONG",
        "SHORT",
    ):
        return None

    n = len(candles)

    if n < 100:
        return None

    # ========================================================
    # ONLY THE LATEST CLOSED 15M CANDLE
    # ========================================================

    i = n - 1

    previous_i = i - 1

    current = candles[i]
    previous = candles[previous_i]

    signal_time = int(
        current["timestamp"]
    )

    close_now = float(
        current["close"]
    )

    close_previous = float(
        previous["close"]
    )

    # ========================================================
    # CURRENT CLOUD
    # ========================================================

    cloud_top_now, cloud_bottom_now, _ = cloud_values(
        ichi,
        i,
    )

    cloud_top_previous, cloud_bottom_previous, _ = cloud_values(
        ichi,
        previous_i,
    )

    if (
        cloud_top_now is None
        or cloud_bottom_now is None
        or cloud_top_previous is None
        or cloud_bottom_previous is None
    ):

        return None

    # ========================================================
    # CHIKOU
    #
    # Chikou Span is current close plotted 26 candles back.
    #
    # Therefore:
    #
    # Chikou ABOVE price =
    # current close > close 26 candles ago
    #
    # Chikou BELOW price =
    # current close < close 26 candles ago
    # ========================================================

    chikou_index = (
        i
        - ICHIMOKU_DISPLACEMENT
    )

    previous_chikou_index = (
        previous_i
        - ICHIMOKU_DISPLACEMENT
    )

    if chikou_index < 0:
        return None

    if previous_chikou_index < 0:
        return None

    price_at_chikou = float(
        candles[
            chikou_index
        ]["close"]
    )

    previous_price_at_chikou = float(
        candles[
            previous_chikou_index
        ]["close"]
    )

    chikou_above = (
        close_now
        > price_at_chikou
    )

    chikou_below = (
        close_now
        < price_at_chikou
    )

    previous_chikou_above = (
        close_previous
        > previous_price_at_chikou
    )

    previous_chikou_below = (
        close_previous
        < previous_price_at_chikou
    )

    # ========================================================
    # LONG
    #
    # Price must actually cross from inside/below cloud
    # to ABOVE cloud on the latest 15M candle.
    #
    # Chikou must be above price.
    # ========================================================

    if trend_1h == "LONG":

        price_breaks_above_cloud = (
            close_previous
            <= cloud_top_previous
            and
            close_now
            > cloud_top_now
        )

        if (
            price_breaks_above_cloud
            and chikou_above
        ):

            return {
                "direction": "LONG",
                "signal_time":
                    signal_time,
                "entry":
                    close_now,
                "index":
                    i,
                "cloud_top":
                    cloud_top_now,
                "cloud_bottom":
                    cloud_bottom_now,
                "chikou":
                    close_now,
                "chikou_price":
                    price_at_chikou,
            }

    # ========================================================
    # SHORT
    #
    # Price must actually cross from inside/above cloud
    # to BELOW cloud on the latest 15M candle.
    #
    # Chikou must be below price.
    # ========================================================

    if trend_1h == "SHORT":

        price_breaks_below_cloud = (
            close_previous
            >= cloud_bottom_previous
            and
            close_now
            < cloud_bottom_now
        )

        if (
            price_breaks_below_cloud
            and chikou_below
        ):

            return {
                "direction": "SHORT",
                "signal_time":
                    signal_time,
                "entry":
                    close_now,
                "index":
                    i,
                "cloud_top":
                    cloud_top_now,
                "cloud_bottom":
                    cloud_bottom_now,
                "chikou":
                    close_now,
                "chikou_price":
                    price_at_chikou,
            }

    return None


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
    # Ichimoku
    #
    # Tenkan intentionally excluded from SL / TP.
    # --------------------------------------------------------

    for idx in range(
        max(
            0,
            entry_index - 100,
        ),
        entry_index + 1,
    ):

        kijun = ichi[idx]["kijun"]

        if kijun is not None:

            supports.append(
                (
                    float(kijun),
                    "Kijun",
                )
            )

            resistances.append(
                (
                    float(kijun),
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
    # Confirmed swings
    # --------------------------------------------------------

    highs, lows = confirmed_swings(
        candles
    )

    for swing in highs:

        if (
            swing["index"]
            <= entry_index
        ):

            resistances.append(
                (
                    float(
                        swing["price"]
                    ),
                    swing["source"],
                )
            )

    for swing in lows:

        if (
            swing["index"]
            <= entry_index
        ):

            supports.append(
                (
                    float(
                        swing["price"]
                    ),
                    swing["source"],
                )
            )

    return (
        supports,
        resistances,
    )


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

    supports, resistances = (
        build_level_candidates(
            candles,
            ichi,
            entry_index,
        )
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        support_candidates = [
            x
            for x in supports
            if x[0] < entry
        ]

        if not support_candidates:
            return None

        support_candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        sl, sl_source = (
            support_candidates[0]
        )

        resistance_candidates = [
            x
            for x in resistances
            if x[0] > entry
        ]

        if not resistance_candidates:
            return None

        resistance_candidates.sort(
            key=lambda x: x[0]
        )

        selected_tp = None

        for tp, tp_source in (
            resistance_candidates
        ):

            risk = (
                entry - sl
            )

            reward = (
                tp - entry
            )

            if risk <= 0:
                continue

            rr = (
                reward / risk
            )

            if rr >= MIN_RR:

                selected_tp = (
                    tp,
                    tp_source,
                    rr,
                )

                break

        if selected_tp is None:
            return None

        tp, tp_source, rr = (
            selected_tp
        )

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
            x
            for x in resistances
            if x[0] > entry
        ]

        if not resistance_candidates:
            return None

        resistance_candidates.sort(
            key=lambda x: x[0]
        )

        sl, sl_source = (
            resistance_candidates[0]
        )

        support_candidates = [
            x
            for x in supports
            if x[0] < entry
        ]

        if not support_candidates:
            return None

        support_candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        selected_tp = None

        for tp, tp_source in (
            support_candidates
        ):

            risk = (
                sl - entry
            )

            reward = (
                entry - tp
            )

            if risk <= 0:
                continue

            rr = (
                reward / risk
            )

            if rr >= MIN_RR:

                selected_tp = (
                    tp,
                    tp_source,
                    rr,
                )

                break

        if selected_tp is None:
            return None

        tp, tp_source, rr = (
            selected_tp
        )

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

        return (
            cur.rowcount == 1
        )

    except Exception as e:

        print(
            f"Insert trade error "
            f"{symbol}: {e}"
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

        direction = (
            trade["direction"]
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

        high = float(
            latest_candle["high"]
        )

        low = float(
            latest_candle["low"]
        )

        exit_price = None
        reason = None
        pnl = 0.0

        if direction == "LONG":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            if hit_sl:

                exit_price = sl
                reason = "SL"

            elif hit_tp:

                exit_price = tp
                reason = "TP"

            if exit_price is not None:

                pnl = (
                    (
                        exit_price
                        - entry
                    )
                    / entry
                    * 100.0
                )

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            if hit_sl:

                exit_price = sl
                reason = "SL"

            elif hit_tp:

                exit_price = tp
                reason = "TP"

            if exit_price is not None:

                pnl = (
                    (
                        entry
                        - exit_price
                    )
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
# CANDLE CHART
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

        fig, ax = plt.subplots(
            figsize=(16, 9)
        )

        # ----------------------------------------------------
        # Candle width
        # ----------------------------------------------------

        candle_width = (
            15 / (24 * 60)
        )

        # ----------------------------------------------------
        # Real candles
        # ----------------------------------------------------

        for local_i, candle in enumerate(
            data
        ):

            absolute_i = (
                start + local_i
            )

            x = mdates.date2num(
                datetime.fromtimestamp(
                    candle["timestamp"],
                    tz=timezone.utc,
                )
            )

            o = candle["open"]
            h = candle["high"]
            l = candle["low"]
            c = candle["close"]

            if c >= o:

                body_color = "green"

            else:

                body_color = "red"

            # Wick

            ax.vlines(
                x,
                l,
                h,
                color="black",
                linewidth=0.8,
            )

            # Body

            body_bottom = min(
                o,
                c,
            )

            body_height = abs(
                c - o
            )

            if body_height == 0:

                body_height = (
                    max(
                        abs(h - l),
                        1e-12,
                    )
                    * 0.01
                )

            rect = Rectangle(
                (
                    x
                    - candle_width / 2,
                    body_bottom,
                ),
                candle_width,
                body_height,
                facecolor=body_color,
                edgecolor=body_color,
                alpha=0.75,
            )

            ax.add_patch(
                rect
            )

        # ----------------------------------------------------
        # Ichimoku arrays
        # ----------------------------------------------------

        times = []

        tenkan_values = []
        kijun_values = []
        senkou_a_values = []
        senkou_b_values = []

        chikou_times = []
        chikou_values = []

        for absolute_i in range(
            start,
            len(candles),
        ):

            dt = datetime.fromtimestamp(
                candles[
                    absolute_i
                ]["timestamp"],
                tz=timezone.utc,
            )

            times.append(dt)

            row = ichi[
                absolute_i
            ]

            tenkan_values.append(
                row["tenkan"]
            )

            kijun_values.append(
                row["kijun"]
            )

            a, b = visible_cloud_at(
                ichi,
                absolute_i,
            )

            senkou_a_values.append(
                a
            )

            senkou_b_values.append(
                b
            )

            # ------------------------------------------------
            # Chikou
            #
            # Current close is plotted 26 candles backward.
            # ------------------------------------------------

            chikou_index = (
                absolute_i
                - ICHIMOKU_DISPLACEMENT
            )

            if chikou_index >= 0:

                chikou_times.append(
                    datetime.fromtimestamp(
                        candles[
                            chikou_index
                        ]["timestamp"],
                        tz=timezone.utc,
                    )
                )

                chikou_values.append(
                    candles[
                        absolute_i
                    ]["close"]
                )

        # ----------------------------------------------------
        # Ichimoku lines
        # ----------------------------------------------------

        ax.plot(
            times,
            tenkan_values,
            color="blue",
            linewidth=1.1,
            label="Tenkan",
        )

        ax.plot(
            times,
            kijun_values,
            color="red",
            linewidth=1.1,
            label="Kijun",
        )

        ax.plot(
            times,
            senkou_a_values,
            color="green",
            linewidth=1.1,
            label="Senkou A",
        )

        ax.plot(
            times,
            senkou_b_values,
            color="red",
            linewidth=1.1,
            label="Senkou B",
        )

        # ----------------------------------------------------
        # Chikou Span
        # ----------------------------------------------------

        if chikou_times:

            ax.plot(
                chikou_times,
                chikou_values,
                color="green",
                linewidth=1.0,
                label="Chikou Span",
            )

        # ----------------------------------------------------
        # Cloud
        #
        # Green when A > B
        # Red when A < B
        # ----------------------------------------------------

        for j in range(
            len(times) - 1
        ):

            a1 = (
                senkou_a_values[j]
            )

            b1 = (
                senkou_b_values[j]
            )

            a2 = (
                senkou_a_values[j + 1]
            )

            b2 = (
                senkou_b_values[j + 1]
            )

            if (
                a1 is None
                or b1 is None
                or a2 is None
                or b2 is None
            ):

                continue

            segment_times = [
                times[j],
                times[j + 1],
            ]

            if (
                a1 >= b1
                and a2 >= b2
            ):

                ax.fill_between(
                    segment_times,
                    [a1, a2],
                    [b1, b2],
                    color="green",
                    alpha=0.18,
                )

            elif (
                a1 <= b1
                and a2 <= b2
            ):

                ax.fill_between(
                    segment_times,
                    [a1, a2],
                    [b1, b2],
                    color="red",
                    alpha=0.18,
                )

        # ----------------------------------------------------
        # Entry / SL / TP
        # ----------------------------------------------------

        entry = signal["entry"]
        sl = levels["sl"]
        tp = levels["tp"]

        ax.axhline(
            entry,
            linestyle="--",
            linewidth=1.0,
            label=f"Entry {entry:g}",
        )

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1.0,
            label=f"SL {sl:g}",
        )

        ax.axhline(
            tp,
            linestyle="--",
            linewidth=1.0,
            label=f"TP {tp:g}",
        )

        # ----------------------------------------------------
        # Signal candle
        # ----------------------------------------------------

        signal_dt = datetime.fromtimestamp(
            signal["signal_time"],
            tz=timezone.utc,
        )

        ax.axvline(
            signal_dt,
            linestyle=":",
            linewidth=1.0,
        )

        # ----------------------------------------------------
        # Confirmed swings
        # ----------------------------------------------------

        highs_swings, lows_swings = (
            confirmed_swings(
                candles
            )
        )

        for swing in highs_swings:

            if (
                start
                <= swing["index"]
                < len(candles)
            ):

                dt = datetime.fromtimestamp(
                    candles[
                        swing["index"]
                    ]["timestamp"],
                    tz=timezone.utc,
                )

                ax.scatter(
                    dt,
                    swing["price"],
                    marker="^",
                    s=35,
                )

        for swing in lows_swings:

            if (
                start
                <= swing["index"]
                < len(candles)
            ):

                dt = datetime.fromtimestamp(
                    candles[
                        swing["index"]
                    ]["timestamp"],
                    tz=timezone.utc,
                )

                ax.scatter(
                    dt,
                    swing["price"],
                    marker="v",
                    s=35,
                )

        # ----------------------------------------------------
        # Formatting
        # ----------------------------------------------------

        ax.set_title(
            f"{asset} 15M Ichimoku "
            f"Signal - "
            f"{signal['direction']}"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.20
        )

        ax.legend(
            loc="best",
            fontsize=8,
        )

        ax.xaxis.set_major_formatter(
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
            f"ichimoku_signal_"
            f"{asset}.png"
        )

        plt.savefig(
            path,
            dpi=140,
            bbox_inches="tight",
        )

        plt.close()

        return path

    except Exception as e:

        print(
            f"Chart error "
            f"{asset}: {e}"
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

    direction = signal[
        "direction"
    ]

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
            (
                entry - sl
            )
            / entry
            * 100
        )

        tp_pct = (
            (
                tp - entry
            )
            / entry
            * 100
        )

    else:

        sl_pct = (
            (
                sl - entry
            )
            / entry
            * 100
        )

        tp_pct = (
            (
                entry - tp
            )
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
        f"15M candle: "
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

        return (
            "📌 OPEN TRADES: 0"
        )

    lines = [
        f"📌 OPEN TRADES: "
        f"{len(rows)}",
        "",
    ]

    current_cache = {}

    for trade in rows:

        symbol = trade[
            "symbol"
        ]

        if symbol not in current_cache:

            candles = fetch_candles(
                symbol,
                TF_15M,
                10,
            )

            candles = (
                filter_closed_candles(
                    candles,
                    15,
                )
            )

            if candles:

                current_cache[
                    symbol
                ] = candles[-1]["close"]

            else:

                current_cache[
                    symbol
                ] = None

        current = current_cache[
            symbol
        ]

        if current is None:

            current = trade[
                "entry"
            ]

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

        if direction == "LONG":

            pnl = (
                (
                    current
                    - entry
                )
                / entry
                * 100
            )

            sl_distance = (
                (
                    entry
                    - sl
                )
                / entry
                * 100
            )

            tp_distance = (
                (
                    tp
                    - entry
                )
                / entry
                * 100
            )

            emoji = "🟢"

        else:

            pnl = (
                (
                    entry
                    - current
                )
                / entry
                * 100
            )

            sl_distance = (
                (
                    sl
                    - entry
                )
                / entry
                * 100
            )

            tp_distance = (
                (
                    entry
                    - tp
                )
                / entry
                * 100
            )

            emoji = "🔴"

        lines.append(
            f"{emoji} "
            f"{symbol} "
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

    return "\n".join(
        lines
    )


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

    decided = (
        wins + losses
    )

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
        open_row[
            "open_pnl"
        ] or 0
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

    open_details = (
        build_open_details(
            conn
        )
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

    candles_1h = (
        filter_closed_candles(
            candles_1h,
            60,
        )
    )

    if len(candles_1h) < 100:

        print(
            f"{asset}: "
            f"insufficient 1H data"
        )

        return {
            "new": None,
            "closed": 0,
        }

    # ========================================================
    # 1H TRENDLINE BREAK
    # ========================================================

    trendline = (
        get_1h_trendline_direction(
            candles_1h
        )
    )

    if trendline is None:

        print(
            f"{asset}: "
            f"no fresh 1H trendline break"
        )

        return {
            "new": None,
            "closed": 0,
        }

    trend = trendline[
        "direction"
    ]

    # ========================================================
    # 15M
    # ========================================================

    candles_15m = fetch_candles(
        symbol,
        TF_15M,
        COUNT_15M,
    )

    candles_15m = (
        filter_closed_candles(
            candles_15m,
            15,
        )
    )

    if len(candles_15m) < 100:

        print(
            f"{asset}: "
            f"insufficient 15M data"
        )

        return {
            "new": None,
            "closed": 0,
        }

    ichi_15m = (
        calculate_ichimoku(
            candles_15m
        )
    )

    # ========================================================
    # CLOSE EXISTING TRADES
    #
    # Latest CLOSED 15M candle.
    # ========================================================

    closed = process_open_trades(
        conn,
        symbol,
        candles_15m[-1],
    )

    # ========================================================
    # NEW 15M SIGNAL
    #
    # ONLY candles_15m[-1].
    # ========================================================

    signal = latest_15m_signal(
        candles_15m,
        ichi_15m,
        trend,
    )

    if signal is None:

        print(
            f"{asset}: "
            f"no new 15M signal"
        )

        return {
            "new": None,
            "closed": closed,
        }

    direction = signal[
        "direction"
    ]

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
        candles_15m,
        ichi_15m,
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
    # TELEGRAM
    # ========================================================

    send_new_signal(
        asset,
        signal,
        levels,
        candles_15m,
        ichi_15m,
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

                if (
                    result["new"]
                    == "LONG"
                ):

                    new_long += 1

                elif (
                    result["new"]
                    == "SHORT"
                ):

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

        print(
            report
        )

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
        "1H TRENDLINE BREAK"
    )

    print(
        "15M ICHIMOKU SIGNAL"
    )

    print(
        "NO 5M TIMEFRAME"
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
