# ============================================================
# KRAKEN FUTURES 40-ASSET 1H TRENDLINE + 15M ICHIMOKU
# VERSION 3.0.0
# ============================================================
#
# PAPER ONLY
#
# STRATEGY
#
#   1H:
#     LONG  = fresh breakout above descending swing-high trendline
#     SHORT = fresh breakdown below ascending swing-low trendline
#
#   15M:
#     LONG  = latest closed 15M candle crosses above Ichimoku cloud
#             + Chikou is above price
#
#     SHORT = latest closed 15M candle crosses below Ichimoku cloud
#             + Chikou is below price
#
# IMPORTANT:
#   - NO 5M
#   - NO historical 15M signal replay
#   - Only the latest CLOSED 1H candle can create a new breakout
#   - 15M confirmation must happen AFTER the 1H breakout candle closes
#
# SL / TP:
#   - Kijun
#   - Senkou A
#   - Senkou B / visible cloud
#   - Confirmed swings
#   - Minimum SL distance
#   - Minimum TP distance
#   - Minimum RR
#
# ============================================================

import os
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
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

# Ichimoku
ICH_TENKAN = 9
ICH_KIJUN = 26
ICH_SENKOU_B = 52
ICH_DISPLACEMENT = 26

# Confirmed swings
SWING_LEFT = 2
SWING_RIGHT = 2

# Minimum risk/reward
MIN_RR = 1.50

# Minimum SL / TP distance from entry
MIN_SL_DISTANCE_PCT = 0.004   # 0.40%
MIN_TP_DISTANCE_PCT = 0.006   # 0.60%

# One 1H candle
BREAKOUT_TIMEFRAME_SECONDS = 60 * 60

TELEGRAM_TIMEOUT = 20

TELEGRAM_BOT_TOKEN_ENV_1 = "TELEGRAM_BOT_TOKEN"
TELEGRAM_BOT_TOKEN_ENV_2 = "TELEGRAM_TOKEN"

TELEGRAM_CHAT_ID_ENV_1 = "TELEGRAM_CHAT_ID"
TELEGRAM_CHAT_ID_ENV_2 = "TELEGRAM_CHAT"


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
    token = (
        os.getenv(TELEGRAM_BOT_TOKEN_ENV_1)
        or os.getenv(TELEGRAM_BOT_TOKEN_ENV_2)
    )

    chat_id = (
        os.getenv(TELEGRAM_CHAT_ID_ENV_1)
        or os.getenv(TELEGRAM_CHAT_ID_ENV_2)
    )

    return bool(token and chat_id)


def telegram_credentials():
    token = (
        os.getenv(TELEGRAM_BOT_TOKEN_ENV_1)
        or os.getenv(TELEGRAM_BOT_TOKEN_ENV_2)
    )

    chat_id = (
        os.getenv(TELEGRAM_CHAT_ID_ENV_1)
        or os.getenv(TELEGRAM_CHAT_ID_ENV_2)
    )

    return token, chat_id


def telegram_send_message(text):
    if not telegram_enabled():
        return False

    token, chat_id = telegram_credentials()

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=TELEGRAM_TIMEOUT,
        )

        return response.ok

    except Exception:
        return False


def telegram_send_photo(path, caption=None):
    if not telegram_enabled():
        return False

    token, chat_id = telegram_credentials()

    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"

        with open(path, "rb") as photo:

            response = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption or "",
                },
                files={
                    "photo": photo,
                },
                timeout=TELEGRAM_TIMEOUT,
            )

        return response.ok

    except Exception:
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    cur = conn.execute(
        f"PRAGMA table_info({table})"
    )

    columns = {
        row["name"]
        for row in cur.fetchall()
    }

    if column not in columns:

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
            rr REAL NOT NULL,
            sl_source TEXT,
            tp_source TEXT,
            status TEXT NOT NULL,
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
        ON trades(symbol, direction, signal_time)
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )


def format_time(ts):

    if not ts:
        return "-"

    try:

        return datetime.fromtimestamp(
            int(ts),
            timezone.utc,
        ).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:

        return "-"


# ============================================================
# KRAKEN DATA
# ============================================================

def normalize_timestamp(value):

    value = float(value)

    # milliseconds
    if value > 10_000_000_000:
        value /= 1000.0

    return int(value)


def normalize_candles(payload):

    data = payload

    if isinstance(payload, dict):

        if "candles" in payload:

            data = payload["candles"]

        elif "data" in payload:

            data = payload["data"]

        elif "result" in payload:

            result = payload["result"]

            if isinstance(result, dict):

                if "candles" in result:
                    data = result["candles"]

                elif "data" in result:
                    data = result["data"]

                else:
                    data = result

            else:

                data = result

    if not isinstance(data, list):
        return []

    output = []

    for item in data:

        try:

            if isinstance(item, dict):

                ts = (
                    item.get("timestamp")
                    or item.get("time")
                    or item.get("t")
                )

                o = item.get(
                    "open",
                    item.get("o"),
                )

                h = item.get(
                    "high",
                    item.get("h"),
                )

                l = item.get(
                    "low",
                    item.get("l"),
                )

                c = item.get(
                    "close",
                    item.get("c"),
                )

                v = item.get(
                    "volume",
                    item.get("v", 0),
                )

                if (
                    ts is None
                    or o is None
                    or h is None
                    or l is None
                    or c is None
                ):
                    continue

            elif (
                isinstance(item, (list, tuple))
                and len(item) >= 5
            ):

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

            output.append(
                {
                    "timestamp": normalize_timestamp(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v or 0),
                }
            )

        except Exception:

            continue

    output.sort(
        key=lambda x: x["timestamp"]
    )

    dedup = {}

    for candle in output:
        dedup[candle["timestamp"]] = candle

    return list(
        sorted(
            dedup.values(),
            key=lambda x: x["timestamp"],
        )
    )


def fetch_candles(
    symbol,
    timeframe,
    count,
):

    url = (
        f"{CHART_BASE}/trade/"
        f"{symbol}/{timeframe}"
    )

    response = requests.get(
        url,
        params={
            "count": count,
        },
        timeout=TELEGRAM_TIMEOUT,
    )

    response.raise_for_status()

    payload = response.json()

    return normalize_candles(
        payload
    )


def filter_closed_candles(
    candles,
    timeframe_minutes,
):

    if not candles:
        return []

    current = now_ts()

    interval = (
        timeframe_minutes * 60
    )

    closed = []

    for candle in candles:

        if (
            candle["timestamp"]
            + interval
            <= current
        ):
            closed.append(candle)

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

    tenkan = [None] * n
    kijun = [None] * n
    senkou_a = [None] * n
    senkou_b = [None] * n

    for i in range(n):

        if i >= ICH_TENKAN - 1:

            highs = [
                candles[j]["high"]
                for j in range(
                    i - ICH_TENKAN + 1,
                    i + 1,
                )
            ]

            lows = [
                candles[j]["low"]
                for j in range(
                    i - ICH_TENKAN + 1,
                    i + 1,
                )
            ]

            tenkan[i] = midpoint(
                highs + lows
            )

        if i >= ICH_KIJUN - 1:

            highs = [
                candles[j]["high"]
                for j in range(
                    i - ICH_KIJUN + 1,
                    i + 1,
                )
            ]

            lows = [
                candles[j]["low"]
                for j in range(
                    i - ICH_KIJUN + 1,
                    i + 1,
                )
            ]

            kijun[i] = midpoint(
                highs + lows
            )

        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):

            senkou_a[i] = (
                tenkan[i]
                + kijun[i]
            ) / 2.0

        if i >= ICH_SENKOU_B - 1:

            highs = [
                candles[j]["high"]
                for j in range(
                    i - ICH_SENKOU_B + 1,
                    i + 1,
                )
            ]

            lows = [
                candles[j]["low"]
                for j in range(
                    i - ICH_SENKOU_B + 1,
                    i + 1,
                )
            ]

            senkou_b[i] = midpoint(
                highs + lows
            )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
    }


def visible_cloud_at(
    ichi,
    index,
):

    shifted = (
        index
        - ICH_DISPLACEMENT
    )

    if shifted < 0:
        return None, None

    a = ichi["senkou_a"][shifted]
    b = ichi["senkou_b"][shifted]

    if a is None or b is None:
        return None, None

    return (
        max(a, b),
        min(a, b),
    )


def cloud_values(
    ichi,
    index,
):

    top, bottom = visible_cloud_at(
        ichi,
        index,
    )

    if (
        top is None
        or bottom is None
    ):
        return None, None, None

    shifted = (
        index
        - ICH_DISPLACEMENT
    )

    a = ichi["senkou_a"][shifted]
    b = ichi["senkou_b"][shifted]

    if a > b:
        direction = "BULLISH"

    elif a < b:
        direction = "BEARISH"

    else:
        direction = "FLAT"

    return (
        top,
        bottom,
        direction,
    )


# ============================================================
# CONFIRMED SWINGS
# ============================================================

def confirmed_swings(candles):

    highs = []
    lows = []

    n = len(candles)

    for i in range(
        SWING_LEFT,
        n - SWING_RIGHT,
    ):

        current_high = candles[i]["high"]
        current_low = candles[i]["low"]

        is_high = True
        is_low = True

        for j in range(
            i - SWING_LEFT,
            i,
        ):

            if (
                candles[j]["high"]
                >= current_high
            ):
                is_high = False

            if (
                candles[j]["low"]
                <= current_low
            ):
                is_low = False

        for j in range(
            i + 1,
            i + SWING_RIGHT + 1,
        ):

            if (
                candles[j]["high"]
                >= current_high
            ):
                is_high = False

            if (
                candles[j]["low"]
                <= current_low
            ):
                is_low = False

        if is_high:

            highs.append(
                {
                    "index": i,
                    "timestamp": candles[i]["timestamp"],
                    "price": current_high,
                }
            )

        if is_low:

            lows.append(
                {
                    "index": i,
                    "timestamp": candles[i]["timestamp"],
                    "price": current_low,
                }
            )

    return highs, lows


# ============================================================
# TRENDLINE
# ============================================================

def line_value(
    x1,
    y1,
    x2,
    y2,
    x,
):

    if x1 == x2:
        return None

    slope = (
        y2 - y1
    ) / float(
        x2 - x1
    )

    return (
        y1
        + slope * (x - x1)
    )


def detect_1h_trendline_breakout(
    candles,
):

    """
    ONLY the latest CLOSED 1H candle
    can be the breakout candle.

    LONG:
      latest two confirmed swing highs
      are descending.

      previous close <= trendline
      current close > trendline

    SHORT:
      latest two confirmed swing lows
      are ascending.

      previous close >= trendline
      current close < trendline
    """

    n = len(candles)

    if n < 20:
        return None

    highs, lows = confirmed_swings(
        candles
    )

    # --------------------------------------------------------
    # LONG
    # Descending resistance
    # --------------------------------------------------------

    if len(highs) >= 2:

        p1 = highs[-2]
        p2 = highs[-1]

        if (
            p2["price"]
            < p1["price"]
        ):

            previous_index = n - 2
            current_index = n - 1

            previous_line = line_value(
                p1["index"],
                p1["price"],
                p2["index"],
                p2["price"],
                previous_index,
            )

            current_line = line_value(
                p1["index"],
                p1["price"],
                p2["index"],
                p2["price"],
                current_index,
            )

            if (
                previous_line is not None
                and current_line is not None
                and candles[
                    previous_index
                ]["close"] <= previous_line
                and candles[
                    current_index
                ]["close"] > current_line
            ):

                return {
                    "direction": "LONG",
                    "breakout_time": candles[
                        current_index
                    ]["timestamp"],
                    "breakout_price": candles[
                        current_index
                    ]["close"],
                    "line_type": (
                        "DESCENDING RESISTANCE"
                    ),
                    "p1_time": p1["timestamp"],
                    "p1_price": p1["price"],
                    "p2_time": p2["timestamp"],
                    "p2_price": p2["price"],
                }

    # --------------------------------------------------------
    # SHORT
    # Ascending support
    # --------------------------------------------------------

    if len(lows) >= 2:

        p1 = lows[-2]
        p2 = lows[-1]

        if (
            p2["price"]
            > p1["price"]
        ):

            previous_index = n - 2
            current_index = n - 1

            previous_line = line_value(
                p1["index"],
                p1["price"],
                p2["index"],
                p2["price"],
                previous_index,
            )

            current_line = line_value(
                p1["index"],
                p1["price"],
                p2["index"],
                p2["price"],
                current_index,
            )

            if (
                previous_line is not None
                and current_line is not None
                and candles[
                    previous_index
                ]["close"] >= previous_line
                and candles[
                    current_index
                ]["close"] < current_line
            ):

                return {
                    "direction": "SHORT",
                    "breakout_time": candles[
                        current_index
                    ]["timestamp"],
                    "breakout_price": candles[
                        current_index
                    ]["close"],
                    "line_type": (
                        "ASCENDING SUPPORT"
                    ),
                    "p1_time": p1["timestamp"],
                    "p1_price": p1["price"],
                    "p2_time": p2["timestamp"],
                    "p2_price": p2["price"],
                }

    return None


# ============================================================
# 15M SIGNAL
# ============================================================

def latest_15m_signal(
    candles,
    ichi,
    breakout,
):

    """
    ONLY the latest CLOSED 15M candle
    is allowed to create a signal.

    The 15M confirmation must occur AFTER
    the 1H breakout candle has closed.
    """

    if (
        not candles
        or not breakout
    ):
        return None

    n = len(candles)

    if (
        n
        < ICH_DISPLACEMENT + 3
    ):
        return None

    i = n - 1
    previous_i = i - 1

    signal_time = candles[i]["timestamp"]

    breakout_end = (
        breakout["breakout_time"]
        + BREAKOUT_TIMEFRAME_SECONDS
    )

    # Do not use 15M candles that existed
    # before the 1H breakout candle closed.
    if signal_time < breakout_end:
        return None

    current_top, current_bottom, current_cloud_direction = cloud_values(
        ichi,
        i,
    )

    previous_top, previous_bottom, _ = cloud_values(
        ichi,
        previous_i,
    )

    if (
        current_top is None
        or current_bottom is None
        or previous_top is None
        or previous_bottom is None
    ):
        return None

    current_close = candles[i]["close"]
    previous_close = candles[
        previous_i
    ]["close"]

    chikou_reference_index = (
        i
        - ICH_DISPLACEMENT
    )

    if chikou_reference_index < 0:
        return None

    chikou_reference_price = candles[
        chikou_reference_index
    ]["close"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if breakout["direction"] == "LONG":

        crossed_above_cloud = (
            previous_close
            <= previous_top
            and current_close
            > current_top
        )

        chikou_above = (
            current_close
            > chikou_reference_price
        )

        if (
            crossed_above_cloud
            and chikou_above
        ):

            return {
                "direction": "LONG",
                "signal_time": signal_time,
                "entry": current_close,
                "cloud_top": current_top,
                "cloud_bottom": current_bottom,
                "cloud_direction": current_cloud_direction,
                "chikou_reference_price": (
                    chikou_reference_price
                ),
                "breakout": breakout,
            }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if breakout["direction"] == "SHORT":

        crossed_below_cloud = (
            previous_close
            >= previous_bottom
            and current_close
            < current_bottom
        )

        chikou_below = (
            current_close
            < chikou_reference_price
        )

        if (
            crossed_below_cloud
            and chikou_below
        ):

            return {
                "direction": "SHORT",
                "signal_time": signal_time,
                "entry": current_close,
                "cloud_top": current_top,
                "cloud_bottom": current_bottom,
                "cloud_direction": current_cloud_direction,
                "chikou_reference_price": (
                    chikou_reference_price
                ),
                "breakout": breakout,
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

    start = max(
        0,
        entry_index - 150,
    )

    # --------------------------------------------------------
    # Ichimoku
    # --------------------------------------------------------

    for i in range(
        start,
        entry_index + 1,
    ):

        kijun = ichi[
            "kijun"
        ][i]

        if kijun is not None:

            supports.append(
                (
                    kijun,
                    (
                        "Kijun 15M @ "
                        f"{format_time(candles[i]['timestamp'])}"
                    ),
                )
            )

            resistances.append(
                (
                    kijun,
                    (
                        "Kijun 15M @ "
                        f"{format_time(candles[i]['timestamp'])}"
                    ),
                )
            )

        top, bottom = visible_cloud_at(
            ichi,
            i,
        )

        if (
            top is not None
            and bottom is not None
        ):

            supports.append(
                (
                    bottom,
                    (
                        "Cloud Bottom @ "
                        f"{format_time(candles[i]['timestamp'])}"
                    ),
                )
            )

            resistances.append(
                (
                    top,
                    (
                        "Cloud Top @ "
                        f"{format_time(candles[i]['timestamp'])}"
                    ),
                )
            )

    # --------------------------------------------------------
    # Confirmed swings
    # --------------------------------------------------------

    highs, lows = confirmed_swings(
        candles
    )

    for swing in highs:

        if swing["index"] <= entry_index:

            resistances.append(
                (
                    swing["price"],
                    (
                        "Confirmed Swing High @ "
                        f"{format_time(swing['timestamp'])}"
                    ),
                )
            )

    for swing in lows:

        if swing["index"] <= entry_index:

            supports.append(
                (
                    swing["price"],
                    (
                        "Confirmed Swing Low @ "
                        f"{format_time(swing['timestamp'])}"
                    ),
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
    entry_index,
    direction,
    entry,
):

    """
    LONG:
      SL >= 0.40% below entry
      TP >= 0.60% above entry
      RR >= 1.50

    SHORT:
      SL >= 0.40% above entry
      TP >= 0.60% below entry
      RR >= 1.50
    """

    supports, resistances = (
        build_level_candidates(
            candles,
            ichi,
            entry_index,
        )
    )

    min_sl_distance = (
        entry
        * MIN_SL_DISTANCE_PCT
    )

    min_tp_distance = (
        entry
        * MIN_TP_DISTANCE_PCT
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        sl_candidates = [
            item
            for item in supports
            if item[0]
            <= entry - min_sl_distance
        ]

        if not sl_candidates:
            return None

        # Closest valid support.
        sl_price, sl_source = max(
            sl_candidates,
            key=lambda x: x[0],
        )

        tp_candidates = [
            item
            for item in resistances
            if item[0]
            >= entry + min_tp_distance
        ]

        if not tp_candidates:
            return None

        tp_candidates.sort(
            key=lambda x: x[0]
        )

        risk = (
            entry
            - sl_price
        )

        if risk <= 0:
            return None

        selected_tp = None

        for tp_price, tp_source in tp_candidates:

            reward = (
                tp_price
                - entry
            )

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                selected_tp = (
                    tp_price,
                    tp_source,
                    rr,
                )

                break

        if selected_tp is None:
            return None

        tp_price, tp_source, rr = (
            selected_tp
        )

        return {
            "sl": sl_price,
            "tp": tp_price,
            "rr": rr,
            "sl_source": sl_source,
            "tp_source": tp_source,
        }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        sl_candidates = [
            item
            for item in resistances
            if item[0]
            >= entry + min_sl_distance
        ]

        if not sl_candidates:
            return None

        # Closest valid resistance.
        sl_price, sl_source = min(
            sl_candidates,
            key=lambda x: x[0],
        )

        tp_candidates = [
            item
            for item in supports
            if item[0]
            <= entry - min_tp_distance
        ]

        if not tp_candidates:
            return None

        tp_candidates.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        risk = (
            sl_price
            - entry
        )

        if risk <= 0:
            return None

        selected_tp = None

        for tp_price, tp_source in tp_candidates:

            reward = (
                entry
                - tp_price
            )

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                selected_tp = (
                    tp_price,
                    tp_source,
                    rr,
                )

                break

        if selected_tp is None:
            return None

        tp_price, tp_source, rr = (
            selected_tp
        )

        return {
            "sl": sl_price,
            "tp": tp_price,
            "rr": rr,
            "sl_source": sl_source,
            "tp_source": tp_source,
        }

    return None


# ============================================================
# OPEN TRADE
# ============================================================

def has_open_trade(
    conn,
    symbol,
):

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    return row is not None


def insert_trade(
    conn,
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

    try:

        conn.execute(
            """
            INSERT INTO trades (
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
                signal_time,
                entry,
                sl,
                tp,
                rr,
                sl_source,
                tp_source,
            ),
        )

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        conn.rollback()

        return False


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
        ORDER BY id
        """,
        (symbol,),
    ).fetchall()

    closed = []

    if not rows:
        return closed

    high = latest_candle["high"]
    low = latest_candle["low"]

    candle_time = (
        latest_candle["timestamp"]
    )

    for trade in rows:

        direction = trade[
            "direction"
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

        exit_price = None
        reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if low <= sl:

                exit_price = sl
                reason = "SL"

            elif high >= tp:

                exit_price = tp
                reason = "TP"

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        elif direction == "SHORT":

            if high >= sl:

                exit_price = sl
                reason = "SL"

            elif low <= tp:

                exit_price = tp
                reason = "TP"

        if exit_price is None:
            continue

        if direction == "LONG":

            pnl_pct = (
                (
                    exit_price
                    - entry
                )
                / entry
            ) * 100.0

        else:

            pnl_pct = (
                (
                    entry
                    - exit_price
                )
                / entry
            ) * 100.0

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
                candle_time,
                reason,
                pnl_pct,
                trade["id"],
            ),
        )

        closed.append(
            {
                "asset": trade["asset"],
                "symbol": trade["symbol"],
                "direction": direction,
                "entry": entry,
                "exit": exit_price,
                "reason": reason,
                "pnl_pct": pnl_pct,
            }
        )

    conn.commit()

    return closed


# ============================================================
# TELEGRAM TRADE MESSAGES
# ============================================================

def send_closed_trade_message(
    trade,
):

    direction_emoji = (
        "🟢"
        if trade["direction"] == "LONG"
        else "🔴"
    )

    pnl = trade["pnl_pct"]

    if pnl >= 0:
        pnl_text = f"+{pnl:.2f}%"
    else:
        pnl_text = f"{pnl:.2f}%"

    text = (
        f"{direction_emoji} TRADE CLOSED\n\n"
        f"{trade['symbol']} "
        f"{trade['direction']}\n"
        f"Reason: {trade['reason']}\n"
        f"Entry: {trade['entry']:.8g}\n"
        f"Exit: {trade['exit']:.8g}\n"
        f"PnL: {pnl_text}\n\n"
        f"📌 PAPER ONLY"
    )

    telegram_send_message(
        text
    )


def send_new_signal(
    asset,
    symbol,
    signal,
    levels,
):

    direction = signal[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = signal["entry"]
    sl = levels["sl"]
    tp = levels["tp"]
    rr = levels["rr"]

    breakout_time = signal[
        "breakout"
    ]["breakout_time"]

    text = (
        f"{emoji} NEW {direction}\n\n"
        f"{symbol} {direction}\n"
        f"Entry: {entry:.8g}\n"
        f"SL: {sl:.8g}\n"
        f"TP: {tp:.8g}\n"
        f"RR: {rr:.2f}\n"
        f"1H Breakout: "
        f"{format_time(breakout_time)}\n"
        f"15M Confirmation: "
        f"{format_time(signal['signal_time'])}\n"
        f"SL Source: "
        f"{levels['sl_source']}\n"
        f"TP Source: "
        f"{levels['tp_source']}\n\n"
        f"📌 PAPER ONLY"
    )

    telegram_send_message(
        text
    )


# ============================================================
# CHART
# ============================================================

def make_signal_chart(
    asset,
    candles,
    ichi,
    direction,
    entry,
    sl,
    tp,
    breakout,
    signal_time,
    output_path,
):

    if not candles:
        return False

    chart_count = min(
        160,
        len(candles),
    )

    visible = candles[
        -chart_count:
    ]

    start_index = (
        len(candles)
        - chart_count
    )

    end_index = (
        len(candles)
        - 1
    )

    start_timestamp = (
        visible[0]["timestamp"]
    )

    fig, ax = plt.subplots(
        figsize=(16, 8),
        dpi=120,
    )

    x_dates = [
        datetime.fromtimestamp(
            c["timestamp"],
            timezone.utc,
        )
        for c in visible
    ]

    x_num = mdates.date2num(
        x_dates
    )

    # ========================================================
    # CANDLE WIDTH
    # ========================================================

    if len(x_num) >= 2:

        candle_width = (
            x_num[1]
            - x_num[0]
        ) * 0.72

    else:

        candle_width = 0.008

    # ========================================================
    # CANDLESTICKS
    # ========================================================

    for local_i, candle in enumerate(
        visible
    ):

        x = x_num[local_i]

        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        ax.vlines(
            x,
            l,
            h,
            linewidth=0.7,
            color="black",
            alpha=0.85,
        )

        body_low = min(
            o,
            c,
        )

        body_height = abs(
            c - o
        )

        if body_height == 0:

            body_height = max(
                abs(c) * 0.00001,
                1e-12,
            )

        if c >= o:
            face = "white"
        else:
            face = "black"

        rect = Rectangle(
            (
                x
                - candle_width / 2.0,
                body_low,
            ),
            candle_width,
            body_height,
            facecolor=face,
            edgecolor="black",
            linewidth=0.7,
        )

        ax.add_patch(
            rect
        )

    # ========================================================
    # ICHIMOKU ARRAYS
    # ========================================================

    tenkan_x = []
    tenkan_y = []

    kijun_x = []
    kijun_y = []

    senkou_a_x = []
    senkou_a_y = []

    senkou_b_x = []
    senkou_b_y = []

    cloud_x = []
    cloud_top = []
    cloud_bottom = []

    for local_i, candle in enumerate(
        visible
    ):

        absolute_i = (
            start_index
            + local_i
        )

        x = x_num[local_i]

        tenkan = ichi[
            "tenkan"
        ][absolute_i]

        kijun = ichi[
            "kijun"
        ][absolute_i]

        if tenkan is not None:

            tenkan_x.append(x)
            tenkan_y.append(
                tenkan
            )

        if kijun is not None:

            kijun_x.append(x)
            kijun_y.append(
                kijun
            )

        shifted = (
            absolute_i
            - ICH_DISPLACEMENT
        )

        if shifted >= 0:

            a = ichi[
                "senkou_a"
            ][shifted]

            b = ichi[
                "senkou_b"
            ][shifted]

            if (
                a is not None
                and b is not None
            ):

                senkou_a_x.append(x)
                senkou_a_y.append(a)

                senkou_b_x.append(x)
                senkou_b_y.append(b)

                cloud_x.append(x)

                cloud_top.append(
                    max(a, b)
                )

                cloud_bottom.append(
                    min(a, b)
                )

            else:

                cloud_x.append(x)
                cloud_top.append(
                    math.nan
                )
                cloud_bottom.append(
                    math.nan
                )

        else:

            cloud_x.append(x)
            cloud_top.append(
                math.nan
            )
            cloud_bottom.append(
                math.nan
            )

    # ========================================================
    # ICHIMOKU LINES
    # ========================================================

    ax.plot(
        tenkan_x,
        tenkan_y,
        color="blue",
        linewidth=1.2,
        label="Tenkan",
    )

    ax.plot(
        kijun_x,
        kijun_y,
        color="red",
        linewidth=1.2,
        label="Kijun",
    )

    ax.plot(
        senkou_a_x,
        senkou_a_y,
        color="green",
        linewidth=1.0,
        label="Senkou A",
    )

    ax.plot(
        senkou_b_x,
        senkou_b_y,
        color="red",
        linewidth=1.0,
        label="Senkou B",
    )

    # ========================================================
    # BULLISH / BEARISH CLOUD
    # ========================================================

    for i in range(
        len(cloud_x) - 1
    ):

        x1 = cloud_x[i]
        x2 = cloud_x[i + 1]

        a1 = cloud_top[i]
        b1 = cloud_bottom[i]

        a2 = cloud_top[i + 1]
        b2 = cloud_bottom[i + 1]

        if any(
            math.isnan(v)
            for v in (
                a1,
                b1,
                a2,
                b2,
            )
        ):
            continue

        abs_i = (
            start_index
            + i
        )

        shifted = (
            abs_i
            - ICH_DISPLACEMENT
        )

        if shifted < 0:
            continue

        sa = ichi[
            "senkou_a"
        ][shifted]

        sb = ichi[
            "senkou_b"
        ][shifted]

        if (
            sa is None
            or sb is None
        ):
            continue

        if sa >= sb:
            cloud_color = "green"
        else:
            cloud_color = "red"

        ax.fill_between(
            [x1, x2],
            [a1, a2],
            [b1, b2],
            color=cloud_color,
            alpha=0.16,
            linewidth=0,
        )

    # ========================================================
    # CHIKOU SPAN
    #
    # Current close plotted 26 candles BACK
    # ========================================================

    chikou_x = []
    chikou_y = []

    for absolute_i in range(
        start_index
        + ICH_DISPLACEMENT,
        end_index + 1,
    ):

        target_index = (
            absolute_i
            - ICH_DISPLACEMENT
        )

        local_target = (
            target_index
            - start_index
        )

        if (
            local_target < 0
            or local_target
            >= len(x_num)
        ):
            continue

        chikou_x.append(
            x_num[local_target]
        )

        chikou_y.append(
            candles[
                absolute_i
            ]["close"]
        )

    ax.plot(
        chikou_x,
        chikou_y,
        color="green",
        linewidth=1.0,
        linestyle="--",
        label="Chikou Span",
    )

    # ========================================================
    # CONFIRMED SWINGS
    # ========================================================

    highs, lows = confirmed_swings(
        candles
    )

    for swing in highs:

        if (
            start_timestamp
            <= swing["timestamp"]
            <= visible[-1]["timestamp"]
        ):

            dt = datetime.fromtimestamp(
                swing["timestamp"],
                timezone.utc,
            )

            ax.scatter(
                [mdates.date2num(dt)],
                [swing["price"]],
                marker="^",
                s=25,
                alpha=0.65,
            )

    for swing in lows:

        if (
            start_timestamp
            <= swing["timestamp"]
            <= visible[-1]["timestamp"]
        ):

            dt = datetime.fromtimestamp(
                swing["timestamp"],
                timezone.utc,
            )

            ax.scatter(
                [mdates.date2num(dt)],
                [swing["price"]],
                marker="v",
                s=25,
                alpha=0.65,
            )

    # ========================================================
    # 1H TRENDLINE + BREAKOUT
    # ========================================================

    if breakout:

        trend_x = []
        trend_y = []

        first_visible_ts = (
            visible[0]["timestamp"]
        )

        last_visible_ts = (
            visible[-1]["timestamp"]
        )

        trend_start_ts = max(
            first_visible_ts,
            breakout["p1_time"],
        )

        trend_end_ts = max(
            trend_start_ts,
            last_visible_ts,
        )

        step_seconds = (
            15 * 60
        )

        t = trend_start_ts

        while t <= trend_end_ts:

            y = line_value(
                breakout["p1_time"],
                breakout["p1_price"],
                breakout["p2_time"],
                breakout["p2_price"],
                t,
            )

            if y is not None:

                dt = datetime.fromtimestamp(
                    t,
                    timezone.utc,
                )

                trend_x.append(
                    mdates.date2num(dt)
                )

                trend_y.append(y)

            t += step_seconds

        if trend_x:

            ax.plot(
                trend_x,
                trend_y,
                color="purple",
                linewidth=1.8,
                linestyle="-.",
                label=(
                    "1H "
                    f"{breakout['line_type']}"
                ),
            )

        breakout_dt = (
            datetime.fromtimestamp(
                breakout[
                    "breakout_time"
                ],
                timezone.utc,
            )
        )

        breakout_x = (
            mdates.date2num(
                breakout_dt
            )
        )

        if (
            breakout[
                "breakout_time"
            ]
            >= first_visible_ts
            and
            breakout[
                "breakout_time"
            ]
            <= last_visible_ts
        ):

            ax.axvline(
                breakout_x,
                color="purple",
                linestyle=":",
                linewidth=1.4,
                alpha=0.9,
            )

            offset = (
                max(
                    abs(
                        visible[-1]["high"]
                        - visible[-1]["low"]
                    ),
                    abs(entry) * 0.002,
                )
                * 2.0
            )

            label_y = (
                breakout[
                    "breakout_price"
                ]
                + offset
                if direction == "LONG"
                else
                breakout[
                    "breakout_price"
                ]
                - offset
            )

            ax.annotate(
                (
                    "1H BREAKOUT → "
                    f"{direction}"
                ),
                xy=(
                    breakout_x,
                    breakout[
                        "breakout_price"
                    ],
                ),
                xytext=(
                    breakout_x,
                    label_y,
                ),
                arrowprops={
                    "arrowstyle": "->",
                    "linewidth": 1.0,
                },
                fontsize=9,
                ha="center",
            )

    # ========================================================
    # ENTRY / SL / TP
    # ========================================================

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.1,
        label=f"Entry {entry:.8g}",
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.1,
        label=f"SL {sl:.8g}",
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.1,
        label=f"TP {tp:.8g}",
    )

    # ========================================================
    # 15M CONFIRMATION
    # ========================================================

    signal_dt = (
        datetime.fromtimestamp(
            signal_time,
            timezone.utc,
        )
    )

    signal_x = (
        mdates.date2num(
            signal_dt
        )
    )

    if (
        visible[0]["timestamp"]
        <= signal_time
        <= visible[-1]["timestamp"]
    ):

        ax.axvline(
            signal_x,
            color="black",
            linestyle=":",
            linewidth=1.0,
            alpha=0.55,
        )

        ax.annotate(
            "15M CONFIRMATION",
            xy=(
                signal_x,
                entry,
            ),
            xytext=(
                signal_x,
                entry,
            ),
            fontsize=8,
            rotation=90,
            va="bottom",
            ha="right",
        )

    # ========================================================
    # FORMAT
    # ========================================================

    ax.set_title(
        f"{asset} 15M Ichimoku Signal - "
        f"{direction}"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.xaxis_date()

    ax.xaxis.set_major_locator(
        mdates.AutoDateLocator()
    )

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M",
            tz=timezone.utc,
        )
    )

    plt.xticks(
        rotation=35
    )

    ax.grid(
        alpha=0.20
    )

    ax.legend(
        loc="upper left",
        fontsize=8,
    )

    fig.tight_layout()

    try:

        fig.savefig(
            output_path,
            bbox_inches="tight",
        )

        return True

    finally:

        plt.close(fig)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats(conn):

    closed = conn.execute(
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
            ) AS pnl
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    open_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()

    total = int(
        closed["total"] or 0
    )

    wins = int(
        closed["wins"] or 0
    )

    losses = int(
        closed["losses"] or 0
    )

    closed_pnl = float(
        closed["pnl"] or 0.0
    )

    open_count = int(
        open_row["count"] or 0
    )

    if total > 0:

        win_rate = (
            wins / total
        ) * 100.0

    else:

        win_rate = 0.0

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "closed_pnl": closed_pnl,
        "open_count": open_count,
    }


def calculate_open_pnl(
    conn,
    price_map,
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchall()

    total = 0.0

    for row in rows:

        symbol = row[
            "symbol"
        ]

        if symbol not in price_map:
            continue

        current = price_map[
            symbol
        ]

        entry = float(
            row["entry"]
        )

        if row[
            "direction"
        ] == "LONG":

            pnl = (
                (
                    current
                    - entry
                )
                / entry
            ) * 100.0

        else:

            pnl = (
                (
                    entry
                    - current
                )
                / entry
            ) * 100.0

        total += pnl

    return total


# ============================================================
# OPEN TRADE DETAILS
# ============================================================

def build_open_details(
    conn,
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id
        """
    ).fetchall()

    if not rows:

        return (
            "📌 OPEN TRADES: 0",
            {},
        )

    lines = [
        f"📌 OPEN TRADES: {len(rows)}",
        "",
    ]

    price_map = {}

    for row in rows:

        symbol = row[
            "symbol"
        ]

        try:

            candles = fetch_candles(
                symbol,
                TF_15M,
                10,
            )

            candles = filter_closed_candles(
                candles,
                15,
            )

            if candles:

                current = candles[
                    -1
                ]["close"]

            else:

                current = float(
                    row["entry"]
                )

        except Exception:

            current = float(
                row["entry"]
            )

        price_map[
            symbol
        ] = current

        entry = float(
            row["entry"]
        )

        if row[
            "direction"
        ] == "LONG":

            pnl = (
                (
                    current
                    - entry
                )
                / entry
            ) * 100.0

        else:

            pnl = (
                (
                    entry
                    - current
                )
                / entry
            ) * 100.0

        emoji = (
            "🟢"
            if row[
                "direction"
            ] == "LONG"
            else "🔴"
        )

        if pnl >= 0:
            pnl_text = (
                f"+{pnl:.2f}%"
            )
        else:
            pnl_text = (
                f"{pnl:.2f}%"
            )

        lines.extend(
            [
                (
                    f"{emoji} "
                    f"{symbol} "
                    f"{row['direction']}"
                ),
                f"Entry: {entry:.8g}",
                f"Current: {current:.8g}",
                f"P/L: {pnl_text}",
                (
                    f"TP: "
                    f"{float(row['tp']):.8g}"
                ),
                (
                    f"SL: "
                    f"{float(row['sl']):.8g}"
                ),
                "",
            ]
        )

    return (
        "\n".join(lines).strip(),
        price_map,
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    conn,
    new_long,
    new_short,
    price_map,
):

    stats = performance_stats(
        conn
    )

    open_pnl = calculate_open_pnl(
        conn,
        price_map,
    )

    total_pnl = (
        stats["closed_pnl"]
        + open_pnl
    )

    open_details, _ = (
        build_open_details(
            conn
        )
    )

    lines = [
        "📊 1H ICHIMOKU REPORT",
        "",
        (
            f"Assets scanned: "
            f"{len(ASSETS)}"
        ),
        (
            f"🟢 New LONG: "
            f"{new_long}"
        ),
        (
            f"🔴 New SHORT: "
            f"{new_short}"
        ),
        "",
        open_details,
        "",
        "📈 PERFORMANCE",
        (
            f"Closed trades: "
            f"{stats['closed']}"
        ),
        (
            f"Wins: "
            f"{stats['wins']}"
        ),
        (
            f"Losses: "
            f"{stats['losses']}"
        ),
        (
            f"Win rate: "
            f"{stats['win_rate']:.2f}%"
        ),
        (
            f"Closed PnL: "
            f"{stats['closed_pnl']:+.2f}%"
        ),
        (
            f"Open PnL: "
            f"{open_pnl:+.2f}%"
        ),
        (
            f"Total PnL: "
            f"{total_pnl:+.2f}%"
        ),
        "",
        "📌 PAPER ONLY",
    ]

    return "\n".join(
        lines
    )


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

    result = {
        "new_long": 0,
        "new_short": 0,
        "price": None,
    }

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

    if len(candles_1h) < 80:
        return result

    # ONLY the latest closed 1H candle.
    breakout = (
        detect_1h_trendline_breakout(
            candles_1h
        )
    )

    # ========================================================
    # 15M
    # ========================================================

    candles_15m = fetch_candles(
        symbol,
        TF_15M,
        COUNT_15M,
    )

    candles_15m = filter_closed_candles(
        candles_15m,
        15,
    )

    if len(candles_15m) < 120:
        return result

    if candles_15m:

        result["price"] = (
            candles_15m[-1]["close"]
        )

    # ========================================================
    # CLOSE EXISTING TRADE
    # Latest CLOSED 15M candle
    # ========================================================

    closed_trades = (
        process_open_trades(
            conn,
            symbol,
            candles_15m[-1],
        )
    )

    for closed_trade in (
        closed_trades
    ):

        send_closed_trade_message(
            closed_trade
        )

    # ========================================================
    # NO FRESH 1H BREAKOUT
    # ========================================================

    if breakout is None:
        return result

    # ========================================================
    # 15M ICHIMOKU
    # ========================================================

    ichi = calculate_ichimoku(
        candles_15m
    )

    signal = latest_15m_signal(
        candles_15m,
        ichi,
        breakout,
    )

    if signal is None:
        return result

    # One open trade per symbol.
    if has_open_trade(
        conn,
        symbol,
    ):
        return result

    entry_index = (
        len(candles_15m)
        - 1
    )

    entry = signal[
        "entry"
    ]

    # ========================================================
    # SL / TP
    # ========================================================

    levels = select_sl_tp(
        candles_15m,
        ichi,
        entry_index,
        signal["direction"],
        entry,
    )

    if levels is None:
        return result

    # ========================================================
    # INSERT
    # ========================================================

    inserted = insert_trade(
        conn,
        asset,
        symbol,
        signal["direction"],
        signal["signal_time"],
        entry,
        levels["sl"],
        levels["tp"],
        levels["rr"],
        levels["sl_source"],
        levels["tp_source"],
    )

    if not inserted:
        return result

    # ========================================================
    # CHART
    # ========================================================

    chart_path = (
        f"/tmp/"
        f"{asset}_15m_ichimoku_signal.png"
    )

    chart_ok = make_signal_chart(
        asset=asset,
        candles=candles_15m,
        ichi=ichi,
        direction=signal[
            "direction"
        ],
        entry=entry,
        sl=levels["sl"],
        tp=levels["tp"],
        breakout=breakout,
        signal_time=signal[
            "signal_time"
        ],
        output_path=chart_path,
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_new_signal(
        asset,
        symbol,
        signal,
        levels,
    )

    if chart_ok:

        telegram_send_photo(
            chart_path,
            caption=(
                f"{asset} 15M Ichimoku "
                f"{signal['direction']} "
                f"| PAPER ONLY"
            ),
        )

    if signal[
        "direction"
    ] == "LONG":

        result[
            "new_long"
        ] = 1

    else:

        result[
            "new_short"
        ] = 1

    return result


# ============================================================
# MAIN
# ============================================================

def main():

    if (
        not PAPER_TRADING
        or REAL_TRADING
    ):

        raise RuntimeError(
            "Safety check failed: "
            "PAPER_TRADING must be True "
            "and REAL_TRADING must be False."
        )

    init_db()

    conn = db_connect()

    total_long = 0
    total_short = 0

    price_map = {}

    try:

        for asset in ASSETS:

            try:

                result = scan_asset(
                    conn,
                    asset,
                )

                total_long += (
                    result["new_long"]
                )

                total_short += (
                    result["new_short"]
                )

                if (
                    result["price"]
                    is not None
                ):

                    price_map[
                        symbol_for(asset)
                    ] = result[
                        "price"
                    ]

            except Exception as exc:

                print(
                    f"[ERROR] "
                    f"{asset}: {exc}"
                )

                traceback.print_exc()

        # ====================================================
        # FINAL REPORT
        # ====================================================

        report = build_report(
            conn,
            total_long,
            total_short,
            price_map,
        )

        print(report)

        telegram_send_message(
            report
        )

    finally:

        conn.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
