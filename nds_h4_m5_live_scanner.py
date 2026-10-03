# ============================================================
# NDS M5 LIVE CHANNEL-HOOK SCANNER
# VERSION 7.0.0
# PAPER TRADING ONLY - NO REAL ORDERS
#
# Strategy timeframe: M5 ONLY
#
# Positive Hook / SHORT:
#   START(L) -> ascending channel -> break down -> H1
#   -> descending channel -> break up -> L1
#   -> ascending channel -> break down -> H2
#   -> descending channel -> break up -> L2
#   -> ascending channel -> break down -> H3 -> SHORT
#
# Negative Hook / LONG = exact mirror:
#   START(H) -> descending channel -> break up -> L1
#   -> ascending channel -> break down -> H1
#   -> descending channel -> break up -> L2
#   -> ascending channel -> break down -> H2
#   -> descending channel -> break up -> L3 -> LONG
#
# Nodes are built from HEIKIN ASHI candles.
# Channel breaks are confirmed from CLOSED REAL M5 candles.
# TP = 86.4% retracement from START to H3/L3.
# SHORT SL = slightly above nearest valid confirmed HA high before entry.
# LONG  SL = slightly below nearest valid confirmed HA low before entry.
#
# Telegram:
# - signal text
# - clear price + HA chart with all hook nodes and channel lines
# - open trades with current PnL / TP% / SL%
# - cumulative paper performance
#
# Performance is persistent.
# A one-time import from the previous nds_m15_v600.db is performed
# when this M5 database is first created, so the performance memory
# does not restart from zero.
# ============================================================

import json
import os
import sqlite3
import time
import traceback
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "7.0.0"
REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

TARGET_ASSETS = 100

# ============================================================
# STRATEGY TIMEFRAME
# ============================================================

STRATEGY_INTERVAL = "5m"
STRATEGY_INTERVAL_MINUTES = 5
STRATEGY_CANDLES = 2400


# ============================================================
# HEIKIN ASHI NODE ENGINE
# ============================================================

NODE_DIRECTION_BARS = 2

PIVOT_LEFT = 2
PIVOT_RIGHT = 2


# ============================================================
# CHANNEL ENGINE
#
# A valid channel needs:
# - 2 lows
# - 2 highs
# - alternating structure
# - same-direction movement of both sides
# - reasonably parallel slopes
# - actual closed-candle break
# ============================================================

CHANNEL_MIN_SAME_SIDE_TOUCHES = 2

CHANNEL_MAX_ANCHOR_NODE_SPAN = 12

CHANNEL_MAX_LOOKAHEAD_BARS = 420

CHANNEL_SLOPE_TOLERANCE = 0.40

CHANNEL_WIDTH_TOLERANCE_PCT = 0.50

CHANNEL_BREAK_BUFFER_PCT = 0.03

CHANNEL_MIN_RANGE_PCT = 0.15

MAX_CHANNELS_PER_SEARCH = 80


# ============================================================
# HOOK / TRADE
# ============================================================

NDS_RETRACE = 0.864

MIN_HOOK_RANGE_PCT = 0.20

SL_BUFFER_PCT = 0.15

MAX_OPEN_TRADES = 3

# Live scanner should not revive very old historical setups.
SIGNAL_LOOKBACK_HOURS = 12

REQUEST_TIMEOUT = 20

SCAN_SLEEP_SECONDS = 0.20

CHART_CANDLES = 260


# ============================================================
# DATABASE
# ============================================================

# New M5 database.
DB_FILE = "nds_m5_channel_v700.db"

# Previous M15 database used only for one-time performance import.
LEGACY_DB_FILE = "nds_m15_v600.db"

CHART_DIR = "nds_m5_channel_charts"


# ============================================================
# INSTRUMENT FILTERS
# ============================================================

XAUT_PREFERRED_SYMBOLS = (
    "PF_XAUTUSDT",
    "PF_XAUTUSD",
)

EXCLUDED_BASES = {
    "NEAR",
}


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or ""
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
    or ""
)


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "assets_scanned": 0,

    "instrument_requests": 0,
    "instrument_errors": 0,

    "xaut_found": 0,
    "near_excluded": 0,

    "m5_requests": 0,
    "m5_data_ok": 0,
    "m5_data_error": 0,
    "m5_empty": 0,
    "m5_short": 0,

    "candidate_turns": 0,

    "valid_high_nodes": 0,
    "valid_low_nodes": 0,

    "rejected_non_monotonic": 0,
    "rejected_non_extreme": 0,

    "nodes_collapsed": 0,

    "m5_nodes": 0,

    "channel_candidates": 0,
    "valid_up_channels": 0,
    "valid_down_channels": 0,
    "channel_breaks": 0,

    "positive_hooks": 0,
    "negative_hooks": 0,

    "confirmed_hooks": 0,
    "recent_hooks": 0,
    "range_valid_hooks": 0,

    "tp_touched": 0,

    "sl_found": 0,
    "sl_missing": 0,

    "geometry_valid": 0,

    "duplicates": 0,
    "max_open": 0,

    "signal_ready": 0,
    "signals": 0,

    "chart_sent": 0,
    "chart_errors": 0,

    "performance_imported": 0,

    "api_errors": [],
}


START_TIME = time.time()


# ============================================================
# TIME / FORMAT HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_ts():
    return int(utc_now().timestamp())


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "-"


def fmt_price(value):
    try:
        value = float(value)

        if value >= 1000:
            return f"{value:.2f}"

        if value >= 1:
            return f"{value:.5f}"

        if value >= 0.01:
            return f"{value:.7f}"

        return f"{value:.10f}"

    except Exception:
        return "-"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_send(text):
    if not telegram_enabled():
        return False

    try:
        r = requests.post(
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendMessage",

            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },

            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:
            DIAG["api_errors"].append(
                f"Telegram text HTTP {r.status_code}"
            )

        return r.ok

    except Exception as e:

        DIAG["api_errors"].append(
            f"Telegram text: {str(e)[:160]}"
        )

        return False


def telegram_send_photo(photo_path, caption):

    if (
        not telegram_enabled()
        or not photo_path
        or not os.path.exists(photo_path)
    ):
        return False

    try:

        with open(photo_path, "rb") as photo:

            r = requests.post(

                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto",

                files={
                    "photo": photo
                },

                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },

                timeout=REQUEST_TIMEOUT,
            )

        if not r.ok:

            DIAG["api_errors"].append(
                f"Telegram photo HTTP {r.status_code}"
            )

        return r.ok

    except Exception as e:

        DIAG["api_errors"].append(
            f"Telegram photo: {str(e)[:160]}"
        )

        return False


# ============================================================
# INSTRUMENT HELPERS
# ============================================================

def _instrument_base(symbol):

    s = str(symbol or "").upper()

    if s.startswith("PF_"):
        s = s[3:]

    if s.startswith("PI_"):
        s = s[3:]

    for quote in ("USDT", "USD"):

        if s.endswith(quote):

            return s[:-len(quote)]

    return s


def _is_tradeable_pf(symbol):

    return str(symbol or "").upper().startswith("PF_")


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_futures_instruments():

    DIAG["instrument_requests"] += 1

    try:

        r = requests.get(
            f"{KRAKEN_FUTURES_URL}/instruments",
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        raw = r.json().get(
            "instruments",
            []
        )

        all_pf = []

        xaut = None

        for item in raw:

            symbol = str(
                item.get("symbol")
                or item.get("instrument")
                or ""
            )

            if not _is_tradeable_pf(symbol):
                continue

            if item.get("tradeable", True) is False:
                continue

            base = _instrument_base(symbol)

            if base == "XAUT":

                upper = symbol.upper()

                if (
                    xaut is None
                    or upper in XAUT_PREFERRED_SYMBOLS
                ):
                    xaut = symbol

                continue

            if base in EXCLUDED_BASES:

                DIAG["near_excluded"] += 1

                continue

            if (
                "USD" not in symbol.upper()
                and
                "USDT" not in symbol.upper()
            ):

                continue

            all_pf.append(symbol)

        all_pf = list(
            dict.fromkeys(all_pf)
        )

        if not xaut:

            DIAG["api_errors"].append(
                "XAUT futures instrument not found"
            )

            return all_pf[:TARGET_ASSETS]

        DIAG["xaut_found"] = 1

        selected = all_pf[
            :max(
                0,
                TARGET_ASSETS - 1
            )
        ]

        if xaut not in selected:
            selected.append(xaut)

        return selected[:TARGET_ASSETS]

    except Exception as e:

        DIAG["instrument_errors"] += 1

        DIAG["api_errors"].append(
            f"Instruments: {str(e)[:180]}"
        )

        return []


# ============================================================
# CANDLE DATA
# ============================================================

def get_candles(
    symbol,
    interval=STRATEGY_INTERVAL,
    count=STRATEGY_CANDLES
):

    try:

        r = requests.get(

            f"{KRAKEN_CHART_URL}/trade/"
            f"{symbol}/{interval}",

            params={
                "count": int(count)
            },

            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:

            DIAG["api_errors"].append(
                f"{symbol} {interval} "
                f"HTTP {r.status_code}"
            )

            return None

        rows = []

        for c in r.json().get(
            "candles",
            []
        ):

            if isinstance(c, dict):

                vals = [

                    c.get(
                        "time",
                        c.get(
                            "timestamp",
                            c.get("t")
                        )
                    ),

                    c.get(
                        "open",
                        c.get("o")
                    ),

                    c.get(
                        "high",
                        c.get("h")
                    ),

                    c.get(
                        "low",
                        c.get("l")
                    ),

                    c.get(
                        "close",
                        c.get("c")
                    ),

                    c.get(
                        "volume",
                        c.get("v", 0)
                    ),
                ]

            elif isinstance(
                c,
                (list, tuple)
            ) and len(c) >= 5:

                vals = list(c[:5]) + [
                    c[5] if len(c) > 5 else 0
                ]

            else:

                continue

            rows.append(vals)

        if not rows:

            return pd.DataFrame()

        df = pd.DataFrame(
            rows,
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        for col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna(
            subset=[
                "time",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        if df.empty:
            return df

        if df.time.max() > 10_000_000_000:

            df["time"] /= 1000.0

        df["time"] = df["time"].astype(float)

        df = (
            df
            .sort_values("time")
            .drop_duplicates("time")
            .reset_index(drop=True)
        )

        # Never use the current still-forming M5 candle.
        current_bucket = (
            int(
                time.time()
                // (
                    STRATEGY_INTERVAL_MINUTES
                    * 60
                )
            )
            *
            (
                STRATEGY_INTERVAL_MINUTES
                * 60
            )
        )

        df = df[
            df.time < current_bucket
        ].reset_index(drop=True)

        return df

    except Exception as e:

        DIAG["api_errors"].append(
            f"{symbol} {interval}: "
            f"{str(e)[:180]}"
        )

        return None


# ============================================================
# HEIKIN ASHI
# ============================================================

def calculate_heikin_ashi(df):

    ha = (
        df[
            [
                "time",
                "open",
                "high",
                "low",
                "close",
            ]
        ]
        .copy()
        .reset_index(drop=True)
    )

    ha["ha_close"] = (
        ha.open
        + ha.high
        + ha.low
        + ha.close
    ) / 4.0

    opens = []

    for i in range(len(ha)):

        if i == 0:

            opens.append(
                (
                    float(
                        ha.iloc[i].open
                    )
                    +
                    float(
                        ha.iloc[i].close
                    )
                ) / 2.0
            )

        else:

            opens.append(
                (
                    opens[i - 1]
                    +
                    float(
                        ha.iloc[i - 1].ha_close
                    )
                ) / 2.0
            )

    ha["ha_open"] = opens

    ha["ha_high"] = ha[
        [
            "high",
            "ha_open",
            "ha_close",
        ]
    ].max(axis=1)

    ha["ha_low"] = ha[
        [
            "low",
            "ha_open",
            "ha_close",
        ]
    ].min(axis=1)

    return ha


# ============================================================
# HA NODE ENGINE
# ============================================================

def strictly_monotonic(
    values,
    direction
):

    if len(values) < 2:
        return False

    vals = [
        float(v)
        for v in values
    ]

    if direction == "UP":

        return all(
            vals[i] < vals[i + 1]
            for i in range(
                len(vals) - 1
            )
        )

    return all(
        vals[i] > vals[i + 1]
        for i in range(
            len(vals) - 1
        )
    )


def direction_changed_around_node(
    df,
    index,
    node_type
):

    n = NODE_DIRECTION_BARS

    if (
        index - n < 0
        or
        index + n >= len(df)
    ):
        return False

    closes = (
        df["close"]
        .astype(float)
        .tolist()
    )

    before = closes[
        index - n:index + 1
    ]

    after = closes[
        index:index + n + 1
    ]

    if node_type == "H":

        return (
            strictly_monotonic(
                before,
                "UP"
            )
            and
            strictly_monotonic(
                after,
                "DOWN"
            )
        )

    return (
        strictly_monotonic(
            before,
            "DOWN"
        )
        and
        strictly_monotonic(
            after,
            "UP"
        )
    )


def _is_strict_local_extreme(
    df,
    index,
    node_type
):

    if (
        index - PIVOT_LEFT < 0
        or
        index + PIVOT_RIGHT >= len(df)
    ):
        return False

    if node_type == "H":

        value = float(
            df.iloc[index].high
        )

        left_vals = [
            float(
                df.iloc[j].high
            )
            for j in range(
                index - PIVOT_LEFT,
                index
            )
        ]

        right_vals = [
            float(
                df.iloc[j].high
            )
            for j in range(
                index + 1,
                index + PIVOT_RIGHT + 1
            )
        ]

        return (
            all(
                value > x
                for x in left_vals
            )
            and
            all(
                value > x
                for x in right_vals
            )
        )

    value = float(
        df.iloc[index].low
    )

    left_vals = [
        float(
            df.iloc[j].low
        )
        for j in range(
            index - PIVOT_LEFT,
            index
        )
    ]

    right_vals = [
        float(
            df.iloc[j].low
        )
        for j in range(
            index + 1,
            index + PIVOT_RIGHT + 1
        )
    ]

    return (
        all(
            value < x
            for x in left_vals
        )
        and
        all(
            value < x
            for x in right_vals
        )
    )


def _make_node(
    df,
    index,
    node_type
):

    if node_type == "H":

        price = float(
            df.iloc[index].high
        )

        raw_key = "high"

    else:

        price = float(
            df.iloc[index].low
        )

        raw_key = "low"

    return {

        "type": node_type,

        "index": int(index),

        "time": float(
            df.iloc[index].time
        ),

        "price": price,

        "raw_price": float(
            df.iloc[index][raw_key]
        ),

        "ha_open": float(
            df.iloc[index].open
        ),

        "ha_close": float(
            df.iloc[index].close
        ),

        "ha_high": float(
            df.iloc[index].high
        ),

        "ha_low": float(
            df.iloc[index].low
        ),
    }


def _collapse_overlapping_same_direction_nodes(
    nodes
):

    if not nodes:
        return []

    nodes = sorted(
        nodes,
        key=lambda p: (
            p["time"],
            p["index"]
        )
    )

    out = []

    max_gap = NODE_DIRECTION_BARS

    for node in nodes:

        if not out:

            out.append(node)

            continue

        prev = out[-1]

        if (
            node["type"] != prev["type"]
            or
            node["index"] - prev["index"]
            > max_gap
        ):

            out.append(node)

            continue

        DIAG[
            "nodes_collapsed"
        ] += 1

        if node["type"] == "H":

            if (
                node["price"]
                >
                prev["price"]
            ):

                out[-1] = node

        else:

            if (
                node["price"]
                <
                prev["price"]
            ):

                out[-1] = node

    return out


def find_pivots(
    df,
    count_diag=True
):

    high_nodes = []

    low_nodes = []

    if df is None or df.empty:

        return (
            high_nodes,
            low_nodes
        )

    n = NODE_DIRECTION_BARS

    start = max(
        PIVOT_LEFT,
        n
    )

    end = len(df) - max(
        PIVOT_RIGHT,
        n
    )

    if end <= start:

        return (
            high_nodes,
            low_nodes
        )

    for i in range(
        start,
        end
    ):

        DIAG[
            "candidate_turns"
        ] += 2

        high_turn = (
            direction_changed_around_node(
                df,
                i,
                "H"
            )
        )

        low_turn = (
            direction_changed_around_node(
                df,
                i,
                "L"
            )
        )

        if high_turn:

            if _is_strict_local_extreme(
                df,
                i,
                "H"
            ):

                high_nodes.append(
                    _make_node(
                        df,
                        i,
                        "H"
                    )
                )

            else:

                DIAG[
                    "rejected_non_extreme"
                ] += 1

        elif low_turn:

            if _is_strict_local_extreme(
                df,
                i,
                "L"
            ):

                low_nodes.append(
                    _make_node(
                        df,
                        i,
                        "L"
                    )
                )

            else:

                DIAG[
                    "rejected_non_extreme"
                ] += 1

    high_nodes = (
        _collapse_overlapping_same_direction_nodes(
            high_nodes
        )
    )

    low_nodes = (
        _collapse_overlapping_same_direction_nodes(
            low_nodes
        )
    )

    if count_diag:

        DIAG[
            "valid_high_nodes"
        ] += len(high_nodes)

        DIAG[
            "valid_low_nodes"
        ] += len(low_nodes)

    return (
        high_nodes,
        low_nodes
    )


def build_ordered_pivots(
    highs,
    lows
):

    all_nodes = sorted(

        highs + lows,

        key=lambda p: (
            p["index"],
            p["time"]
        )
    )

    result = []

    for node in all_nodes:

        if (
            result
            and
            node["index"]
            ==
            result[-1]["index"]
        ):

            continue

        result.append(node)

    return result


# ============================================================
# CHANNEL GEOMETRY
# ============================================================

def _line_from_points(
    p1,
    p2
):

    t1 = float(
        p1["time"]
    )

    t2 = float(
        p2["time"]
    )

    y1 = float(
        p1["price"]
    )

    y2 = float(
        p2["price"]
    )

    dt = t2 - t1

    if dt <= 0:
        return None

    slope = (
        y2 - y1
    ) / dt

    intercept = (
        y1 - slope * t1
    )

    return {

        "slope": slope,

        "intercept": intercept,

        "t1": t1,

        "t2": t2,
    }


def _line_value(
    line,
    ts
):

    return (
        line["slope"]
        * float(ts)
        +
        line["intercept"]
    )


def _relative_diff(
    a,
    b
):

    scale = max(
        abs(a),
        abs(b),
        1e-12
    )

    return (
        abs(a - b)
        / scale
    )


def _channel_candidate_valid(
    start,
    opposite1,
    same2,
    opposite2,
    direction
):

    if direction == "UP":

        if not (
            start["type"] == "L"
            and
            opposite1["type"] == "H"
            and
            same2["type"] == "L"
            and
            opposite2["type"] == "H"
        ):

            return None

        if not (
            same2["price"]
            >
            start["price"]
        ):

            return None

        if not (
            opposite2["price"]
            >
            opposite1["price"]
        ):

            return None

        lower = _line_from_points(
            start,
            same2
        )

        upper = _line_from_points(
            opposite1,
            opposite2
        )

        if (
            lower is None
            or
            upper is None
        ):

            return None

        if (
            lower["slope"] <= 0
            or
            upper["slope"] <= 0
        ):

            return None

    else:

        if not (
            start["type"] == "H"
            and
            opposite1["type"] == "L"
            and
            same2["type"] == "H"
            and
            opposite2["type"] == "L"
        ):

            return None

        if not (
            same2["price"]
            <
            start["price"]
        ):

            return None

        if not (
            opposite2["price"]
            <
            opposite1["price"]
        ):

            return None

        upper = _line_from_points(
            start,
            same2
        )

        lower = _line_from_points(
            opposite1,
            opposite2
        )

        if (
            upper is None
            or
            lower is None
        ):

            return None

        if (
            upper["slope"] >= 0
            or
            lower["slope"] >= 0
        ):

            return None

    # Slopes should be reasonably parallel.
    if (
        _relative_diff(
            lower["slope"],
            upper["slope"]
        )
        >
        CHANNEL_SLOPE_TOLERANCE
    ):

        return None

    # Channel must remain open.
    for node in (
        start,
        opposite1,
        same2,
        opposite2
    ):

        lo = _line_value(
            lower,
            node["time"]
        )

        hi = _line_value(
            upper,
            node["time"]
        )

        if hi <= lo:
            return None

    # Each boundary anchor should lie on its boundary.
    boundary_pairs = (

        (start, lower),

        (same2, lower),

        (opposite1, upper),

        (opposite2, upper),
    )

    for node, line in boundary_pairs:

        lv = _line_value(
            line,
            node["time"]
        )

        base = max(
            abs(node["price"]),
            1e-12
        )

        mismatch = (
            abs(
                node["price"]
                - lv
            )
            / base
            * 100
        )

        if (
            mismatch
            >
            CHANNEL_WIDTH_TOLERANCE_PCT
        ):

            return None

    return {

        "upper": upper,

        "lower": lower,
    }


def _node_price_extreme(
    nodes,
    node_type
):

    pool = [
        n
        for n in nodes
        if n["type"] == node_type
    ]

    if not pool:
        return None

    if node_type == "H":

        return max(
            pool,
            key=lambda x: (
                float(x["price"]),
                float(x["time"])
            )
        )

    return min(
        pool,
        key=lambda x: (
            float(x["price"]),
            float(x["time"])
        )
    )


# ============================================================
# FIND ONE CHANNEL AND ITS BREAK
# ============================================================

def find_channel_after(
    df,
    ordered_nodes,
    start_pos,
    direction
):

    if (
        start_pos < 0
        or
        start_pos >= len(ordered_nodes)
    ):

        return None

    start = ordered_nodes[
        start_pos
    ]

    needed = (
        ["L", "H", "L", "H"]
        if direction == "UP"
        else
        ["H", "L", "H", "L"]
    )

    if (
        start["type"]
        !=
        needed[0]
    ):

        return None

    max_j = min(
        len(ordered_nodes) - 1,
        start_pos
        +
        CHANNEL_MAX_ANCHOR_NODE_SPAN
    )

    found = 0

    # Search earliest viable 4-point channel.
    for i1 in range(
        start_pos + 1,
        max_j + 1
    ):

        if (
            ordered_nodes[i1]["type"]
            !=
            needed[1]
        ):

            continue

        for i2 in range(
            i1 + 1,
            max_j + 1
        ):

            if (
                ordered_nodes[i2]["type"]
                !=
                needed[2]
            ):

                continue

            for i3 in range(
                i2 + 1,
                max_j + 1
            ):

                if (
                    ordered_nodes[i3]["type"]
                    !=
                    needed[3]
                ):

                    continue

                o1 = ordered_nodes[
                    i1
                ]

                s2 = ordered_nodes[
                    i2
                ]

                o2 = ordered_nodes[
                    i3
                ]

                DIAG[
                    "channel_candidates"
                ] += 1

                channel_lines = (
                    _channel_candidate_valid(

                        start,

                        o1,

                        s2,

                        o2,

                        direction,
                    )
                )

                if not channel_lines:

                    DIAG[
                        "rejected_non_monotonic"
                    ] += 1

                    continue

                found += 1

                if direction == "UP":

                    DIAG[
                        "valid_up_channels"
                    ] += 1

                else:

                    DIAG[
                        "valid_down_channels"
                    ] += 1

                anchor_last_index = int(
                    o2["index"]
                )

                break_limit = min(

                    len(df) - 1,

                    anchor_last_index
                    +
                    CHANNEL_MAX_LOOKAHEAD_BARS
                )

                break_idx = None

                # Channel break uses REAL M5 CLOSE.
                for bi in range(
                    anchor_last_index + 1,
                    break_limit + 1
                ):

                    bar = df.iloc[bi]

                    close = float(
                        bar["close"]
                    )

                    lower_v = _line_value(
                        channel_lines["lower"],
                        bar["time"]
                    )

                    upper_v = _line_value(
                        channel_lines["upper"],
                        bar["time"]
                    )

                    if direction == "UP":

                        if (
                            close
                            <
                            lower_v
                            *
                            (
                                1
                                -
                                CHANNEL_BREAK_BUFFER_PCT
                                / 100.0
                            )
                        ):

                            break_idx = bi

                            break

                    else:

                        if (
                            close
                            >
                            upper_v
                            *
                            (
                                1
                                +
                                CHANNEL_BREAK_BUFFER_PCT
                                / 100.0
                            )
                        ):

                            break_idx = bi

                            break

                if break_idx is None:
                    continue

                DIAG[
                    "channel_breaks"
                ] += 1

                relevant_nodes = [

                    n

                    for n in ordered_nodes

                    if (
                        int(n["index"])
                        >=
                        int(start["index"])
                    )

                    and

                    (
                        int(n["index"])
                        <
                        int(break_idx)
                    )
                ]

                if direction == "UP":

                    final = _node_price_extreme(
                        relevant_nodes,
                        "H"
                    )

                    if final is None:
                        continue

                    same_nodes = [
                        n
                        for n in relevant_nodes
                        if n["type"] == "L"
                    ]

                    # START must really remain
                    # the lowest structural low of
                    # this ascending channel.
                    if any(
                        n["price"]
                        <
                        start["price"]
                        for n in same_nodes
                    ):

                        continue

                else:

                    final = _node_price_extreme(
                        relevant_nodes,
                        "L"
                    )

                    if final is None:
                        continue

                    same_nodes = [
                        n
                        for n in relevant_nodes
                        if n["type"] == "H"
                    ]

                    # START must really remain
                    # the highest structural high of
                    # this descending channel.
                    if any(
                        n["price"]
                        >
                        start["price"]
                        for n in same_nodes
                    ):

                        continue

                range_pct = (
                    abs(
                        final["price"]
                        -
                        start["price"]
                    )
                    /
                    max(
                        start["price"],
                        1e-12
                    )
                    * 100
                )

                if (
                    range_pct
                    <
                    CHANNEL_MIN_RANGE_PCT
                ):

                    continue

                return {

                    "direction": direction,

                    "start": start,

                    "break_index": int(
                        break_idx
                    ),

                    "break_time": float(
                        df.iloc[
                            break_idx
                        ].time
                    ),

                    "final": final,

                    "upper": channel_lines[
                        "upper"
                    ],

                    "lower": channel_lines[
                        "lower"
                    ],

                    "anchor_nodes": [
                        start,
                        o1,
                        s2,
                        o2,
                    ],

                    "channel_range_pct":
                        range_pct,
                }

                if (
                    found
                    >=
                    MAX_CHANNELS_PER_SEARCH
                ):

                    return None

    return None


def _find_node_pos(
    ordered_nodes,
    node
):

    for i, n in enumerate(
        ordered_nodes
    ):

        if (
            int(n["index"])
            ==
            int(node["index"])
            and
            n["type"]
            ==
            node["type"]
        ):

            return i

    return None


# ============================================================
# POSITIVE HOOK / SHORT
#
# START(L)
# -> UP CHANNEL BREAK
# -> H1
# -> DOWN CHANNEL BREAK
# -> L1
# -> UP CHANNEL BREAK
# -> H2
# -> DOWN CHANNEL BREAK
# -> L2
# -> UP CHANNEL BREAK
# -> H3
# ============================================================

def _channel_chain_for_short(
    df,
    ordered_nodes,
    start_pos
):

    up1 = find_channel_after(
        df,
        ordered_nodes,
        start_pos,
        "UP"
    )

    if not up1:
        return None

    h1 = up1["final"]

    h1_pos = _find_node_pos(
        ordered_nodes,
        h1
    )

    if h1_pos is None:
        return None

    down1 = find_channel_after(
        df,
        ordered_nodes,
        h1_pos,
        "DOWN"
    )

    if not down1:
        return None

    l1 = down1["final"]

    l1_pos = _find_node_pos(
        ordered_nodes,
        l1
    )

    if l1_pos is None:
        return None

    up2 = find_channel_after(
        df,
        ordered_nodes,
        l1_pos,
        "UP"
    )

    if not up2:
        return None

    h2 = up2["final"]

    h2_pos = _find_node_pos(
        ordered_nodes,
        h2
    )

    if (
        h2_pos is None
        or
        h2["price"]
        <=
        h1["price"]
    ):

        return None

    down2 = find_channel_after(
        df,
        ordered_nodes,
        h2_pos,
        "DOWN"
    )

    if not down2:
        return None

    l2 = down2["final"]

    l2_pos = _find_node_pos(
        ordered_nodes,
        l2
    )

    if (
        l2_pos is None
        or
        l2["price"]
        >=
        l1["price"]
    ):

        return None

    up3 = find_channel_after(
        df,
        ordered_nodes,
        l2_pos,
        "UP"
    )

    if not up3:
        return None

    h3 = up3["final"]

    if (
        h3["price"]
        <=
        h2["price"]
    ):

        return None

    start = ordered_nodes[
        start_pos
    ]

    rng = (
        h3["price"]
        -
        start["price"]
    )

    if (
        rng <= 0
        or
        start["price"] <= 0
    ):

        return None

    # H3 must itself be confirmed,
    # and the final channel break must be closed.
    confirmation_time = max(

        float(
            up3["break_time"]
        )
        +
        STRATEGY_INTERVAL_MINUTES * 60,

        pivot_confirmation_time(
            h3
        ),
    )

    # 86.4% retracement from H3 back toward START.
    tp = (
        h3["price"]
        -
        NDS_RETRACE * rng
    )

    return {

        "direction":
            "SHORT",

        "start":
            start,

        "h1":
            h1,

        "l1":
            l1,

        "h2":
            h2,

        "l2":
            l2,

        "h3":
            h3,

        "final":
            h3,

        "entry":
            h3["price"],

        "tp":
            tp,

        "range_pct":
            rng
            /
            start["price"]
            *
            100,

        "confirmation_time":
            confirmation_time,

        "created_time":
            float(
                h3["time"]
            ),

        "channels":
            [
                up1,
                down1,
                up2,
                down2,
                up3,
            ],
    }


# ============================================================
# NEGATIVE HOOK / LONG
#
# START(H)
# -> DOWN CHANNEL BREAK
# -> L1
# -> UP CHANNEL BREAK
# -> H1
# -> DOWN CHANNEL BREAK
# -> L2
# -> UP CHANNEL BREAK
# -> H2
# -> DOWN CHANNEL BREAK
# -> L3
# ============================================================

def _channel_chain_for_long(
    df,
    ordered_nodes,
    start_pos
):

    down1 = find_channel_after(
        df,
        ordered_nodes,
        start_pos,
        "DOWN"
    )

    if not down1:
        return None

    l1 = down1["final"]

    l1_pos = _find_node_pos(
        ordered_nodes,
        l1
    )

    if l1_pos is None:
        return None

    up1 = find_channel_after(
        df,
        ordered_nodes,
        l1_pos,
        "UP"
    )

    if not up1:
        return None

    h1 = up1["final"]

    h1_pos = _find_node_pos(
        ordered_nodes,
        h1
    )

    if h1_pos is None:
        return None

    down2 = find_channel_after(
        df,
        ordered_nodes,
        h1_pos,
        "DOWN"
    )

    if not down2:
        return None

    l2 = down2["final"]

    l2_pos = _find_node_pos(
        ordered_nodes,
        l2
    )

    if (
        l2_pos is None
        or
        l2["price"]
        >=
        l1["price"]
    ):

        return None

    up2 = find_channel_after(
        df,
        ordered_nodes,
        l2_pos,
        "UP"
    )

    if not up2:
        return None

    h2 = up2["final"]

    h2_pos = _find_node_pos(
        ordered_nodes,
        h2
    )

    if (
        h2_pos is None
        or
        h2["price"]
        <=
        h1["price"]
    ):

        return None

    down3 = find_channel_after(
        df,
        ordered_nodes,
        h2_pos,
        "DOWN"
    )

    if not down3:
        return None

    l3 = down3["final"]

    if (
        l3["price"]
        >=
        l2["price"]
    ):

        return None

    start = ordered_nodes[
        start_pos
    ]

    # START remains above H1/H2.
    if not (
        start["price"]
        >
        h1["price"]
        and
        start["price"]
        >
        h2["price"]
    ):

        return None

    rng = (
        start["price"]
        -
        l3["price"]
    )

    if (
        rng <= 0
        or
        start["price"] <= 0
    ):

        return None

    confirmation_time = max(

        float(
            down3["break_time"]
        )
        +
        STRATEGY_INTERVAL_MINUTES * 60,

        pivot_confirmation_time(
            l3
        ),
    )

    # 86.4% retracement from L3 back toward START.
    tp = (
        l3["price"]
        +
        NDS_RETRACE * rng
    )

    return {

        "direction":
            "LONG",

        "start":
            start,

        "l1":
            l1,

        "h1":
            h1,

        "l2":
            l2,

        "h2":
            h2,

        "l3":
            l3,

        "final":
            l3,

        "entry":
            l3["price"],

        "tp":
            tp,

        "range_pct":
            rng
            /
            start["price"]
            *
            100,

        "confirmation_time":
            confirmation_time,

        "created_time":
            float(
                l3["time"]
            ),

        "channels":
            [
                down1,
                up1,
                down2,
                up2,
                down3,
            ],
    }


# ============================================================
# DETECT COMPLETE CHANNEL HOOKS
# ============================================================

def detect_channel_hooks(
    df,
    highs,
    lows
):

    ordered = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    seen = set()

    for pos, node in enumerate(
        ordered
    ):

        if node["type"] == "L":

            short_hook = (
                _channel_chain_for_short(
                    df,
                    ordered,
                    pos
                )
            )

            if short_hook:

                key = (
                    short_hook[
                        "direction"
                    ],
                    int(
                        short_hook[
                            "final"
                        ]["index"]
                    ),
                )

                if key not in seen:

                    hooks.append(
                        short_hook
                    )

                    seen.add(key)

                    DIAG[
                        "positive_hooks"
                    ] += 1

        if node["type"] == "H":

            long_hook = (
                _channel_chain_for_long(
                    df,
                    ordered,
                    pos
                )
            )

            if long_hook:

                key = (
                    long_hook[
                        "direction"
                    ],
                    int(
                        long_hook[
                            "final"
                        ]["index"]
                    ),
                )

                if key not in seen:

                    hooks.append(
                        long_hook
                    )

                    seen.add(key)

                    DIAG[
                        "negative_hooks"
                    ] += 1

    hooks.sort(
        key=lambda h: (
            float(
                h["confirmation_time"]
            ),
            float(
                h["final"]["time"]
            ),
        )
    )

    return hooks


# ============================================================
# HOOK / CONFIRMATION HELPERS
# ============================================================

def hook_id(
    symbol,
    hook
):

    return (
        f"{symbol}|"
        f"{hook['direction']}|"
        f"{int(hook['final']['time'])}|"
        f"{hook['final']['price']:.12f}"
    )


def pivot_confirmation_time(
    pivot
):

    return (
        float(pivot["time"])
        +
        PIVOT_RIGHT
        *
        STRATEGY_INTERVAL_MINUTES
        *
        60
    )


def hook_is_confirmed(
    hook,
    now_ts=None
):

    now_ts = (
        utc_now_ts()
        if now_ts is None
        else now_ts
    )

    return (
        float(
            hook["confirmation_time"]
        )
        <=
        float(now_ts)
    )


def hook_is_recent(
    hook,
    now_ts=None
):

    now_ts = (
        utc_now_ts()
        if now_ts is None
        else now_ts
    )

    age = (
        float(now_ts)
        -
        float(
            hook["confirmation_time"]
        )
    )

    return (
        0 <= age
        <=
        SIGNAL_LOOKBACK_HOURS * 3600
    )


# ============================================================
# STOP LOSS
#
# SHORT:
# nearest valid previous confirmed HA HIGH above entry
#
# LONG:
# nearest valid previous confirmed HA LOW below entry
# ============================================================

def calculate_m5_sl(
    m5_ha_df,
    hook
):

    if (
        m5_ha_df is None
        or
        m5_ha_df.empty
        or
        hook is None
    ):

        DIAG["sl_missing"] += 1

        return None

    try:

        highs, lows = find_pivots(
            m5_ha_df,
            count_diag=False
        )

        signal_time = float(
            hook["confirmation_time"]
        )

        entry = float(
            hook["entry"]
        )

        confirmed_highs = [

            p

            for p in highs

            if (
                pivot_confirmation_time(p)
                <=
                signal_time
            )

            and
            (
                float(p["time"])
                <
                signal_time
            )
        ]

        confirmed_lows = [

            p

            for p in lows

            if (
                pivot_confirmation_time(p)
                <=
                signal_time
            )

            and
            (
                float(p["time"])
                <
                signal_time
            )
        ]

        if hook["direction"] == "SHORT":

            candidates = [

                p

                for p in confirmed_highs

                if float(p["price"])
                >
                entry
            ]

            if not candidates:

                DIAG["sl_missing"] += 1

                return None

            # Nearest previous valid high in time.
            latest = max(
                candidates,
                key=lambda p:
                    float(p["time"])
            )

            return (
                float(
                    latest["price"]
                )
                *
                (
                    1
                    +
                    SL_BUFFER_PCT
                    / 100.0
                )
            )

        candidates = [

            p

            for p in confirmed_lows

            if float(p["price"])
            <
            entry
        ]

        if not candidates:

            DIAG["sl_missing"] += 1

            return None

        latest = max(
            candidates,
            key=lambda p:
                float(p["time"])
        )

        return (
            float(
                latest["price"]
            )
            *
            (
                1
                -
                SL_BUFFER_PCT
                / 100.0
            )
        )

    except Exception as e:

        DIAG["sl_missing"] += 1

        DIAG["api_errors"].append(
            f"M5 SL: {str(e)[:180]}"
        )

        return None


# ============================================================
# TP TOUCH CHECK
# ============================================================

def tp_already_touched(
    df,
    hook
):

    future = df[
        df.time
        >
        hook["final"]["time"]
    ]

    if future.empty:
        return False

    if hook["direction"] == "SHORT":

        return bool(
            (
                future.low
                <=
                hook["tp"]
            ).any()
        )

    return bool(
        (
            future.high
            >=
            hook["tp"]
        ).any()
    )


# ============================================================
# CHART SERIALIZATION
# ============================================================

def _serialize_line(
    line
):

    return {
        k: float(v)
        for k, v in line.items()
    }


def _serialize_channel(
    ch
):

    return {

        "direction":
            ch["direction"],

        "start_time":
            float(
                ch["start"]["time"]
            ),

        "break_time":
            float(
                ch["break_time"]
            ),

        "upper":
            _serialize_line(
                ch["upper"]
            ),

        "lower":
            _serialize_line(
                ch["lower"]
            ),
    }


# ============================================================
# CHART
#
# Panel 1:
#   Real M5 candles
#   channel lines
#   all hook nodes
#   entry / TP / SL
#
# Panel 2:
#   Heikin Ashi candles
#   same nodes
#   same channels
# ============================================================

def create_hook_chart(
    symbol,
    df,
    hook,
    sl,
    path_prefix="signal_m5"
):

    try:

        os.makedirs(
            CHART_DIR,
            exist_ok=True
        )

        if df is None or df.empty:
            return None

        if hook["direction"] == "SHORT":

            labels = [
                "START",
                "H1",
                "L1",
                "H2",
                "L2",
                "H3",
            ]

            keys = [
                "start",
                "h1",
                "l1",
                "h2",
                "l2",
                "h3",
            ]

        else:

            labels = [
                "START",
                "L1",
                "H1",
                "L2",
                "H2",
                "L3",
            ]

            keys = [
                "start",
                "l1",
                "h1",
                "l2",
                "h2",
                "l3",
            ]

        points = [
            hook[k]
            for k in keys
        ]

        node_times = [
            float(p["time"])
            for p in points
        ]

        work_df = (
            df
            .copy()
            .reset_index(drop=True)
        )

        times = (
            work_df["time"]
            .astype(float)
            .tolist()
        )

        if not times:
            return None

        first_idx = min(
            range(len(times)),
            key=lambda i:
                abs(
                    times[i]
                    -
                    min(node_times)
                )
        )

        last_idx = max(
            range(len(times)),
            key=lambda i:
                abs(
                    times[i]
                    -
                    max(node_times)
                )
        )

        pad_left = 35
        pad_right = 55

        start_idx = max(
            0,
            first_idx - pad_left
        )

        end_idx = min(
            len(work_df),
            last_idx + pad_right + 1
        )

        if (
            end_idx - start_idx
            <
            180
        ):

            center = (
                first_idx
                +
                last_idx
            ) // 2

            start_idx = max(
                0,
                center - 90
            )

            end_idx = min(
                len(work_df),
                start_idx + 180
            )

            start_idx = max(
                0,
                end_idx - 180
            )

        chart_df = (
            work_df
            .iloc[
                start_idx:end_idx
            ]
            .copy()
            .reset_index(drop=True)
        )

        ha = calculate_heikin_ashi(
            chart_df
        )

        x = mdates.date2num(
            pd.to_datetime(
                chart_df.time,
                unit="s",
                utc=True
            ).dt.to_pydatetime()
        )

        width = max(
            (
                STRATEGY_INTERVAL_MINUTES
                /
                1440.0
            )
            * 0.70,
            0.0008
        )

        fig, (
            ax_price,
            ax_ha
        ) = plt.subplots(
            2,
            1,
            figsize=(17, 13),
            sharex=True
        )

        def draw_candles(
            ax,
            data,
            use_ha=False
        ):

            for i, row in data.iterrows():

                xo = x[i]

                if use_ha:

                    o, c, hi, lo = map(
                        float,
                        [
                            row.ha_open,
                            row.ha_close,
                            row.ha_high,
                            row.ha_low,
                        ]
                    )

                else:

                    o, c, hi, lo = map(
                        float,
                        [
                            row.open,
                            row.close,
                            row.high,
                            row.low,
                        ]
                    )

                candle_color = (
                    "#26a69a"
                    if c >= o
                    else
                    "#ef5350"
                )

                ax.vlines(
                    xo,
                    lo,
                    hi,
                    color="black",
                    linewidth=0.7,
                    zorder=2
                )

                ax.add_patch(
                    plt.Rectangle(

                        (
                            xo - width / 2,
                            min(o, c)
                        ),

                        width,

                        max(
                            abs(c - o),
                            max(
                                abs(c),
                                1.0
                            )
                            * 1e-7
                        ),

                        facecolor=candle_color,

                        edgecolor="black",

                        linewidth=0.45,

                        zorder=3,
                    )
                )

        draw_candles(
            ax_price,
            chart_df,
            use_ha=False
        )

        draw_candles(
            ax_ha,
            ha,
            use_ha=True
        )

        chart_left = (
            datetime.fromtimestamp(
                float(
                    chart_df.time.iloc[0]
                ),
                tz=timezone.utc
            )
        )

        chart_right = (
            datetime.fromtimestamp(
                float(
                    chart_df.time.iloc[-1]
                ),
                tz=timezone.utc
            )
        )

        channel_styles = {

            "UP": {
                "upper":
                    "darkgreen",
                "lower":
                    "seagreen",
            },

            "DOWN": {
                "upper":
                    "firebrick",
                "lower":
                    "indianred",
            },
        }

        # Draw every channel segment.
        for ch in hook[
            "channels"
        ]:

            start_dt = (
                datetime.fromtimestamp(
                    float(
                        ch["start"]["time"]
                    ),
                    tz=timezone.utc
                )
            )

            end_dt = (
                datetime.fromtimestamp(
                    float(
                        ch["break_time"]
                    ),
                    tz=timezone.utc
                )
            )

            s_dt = max(
                chart_left,
                start_dt
            )

            e_dt = min(
                chart_right,
                end_dt
            )

            if e_dt <= s_dt:
                continue

            c = channel_styles[
                ch["direction"]
            ]

            for ax in (
                ax_price,
                ax_ha
            ):

                ax.plot(

                    [s_dt, e_dt],

                    [
                        _line_value(
                            ch["lower"],
                            s_dt.timestamp()
                        ),

                        _line_value(
                            ch["lower"],
                            e_dt.timestamp()
                        ),
                    ],

                    linestyle="--",

                    linewidth=1.8,

                    color=c["lower"],

                    alpha=0.85,
                )

                ax.plot(

                    [s_dt, e_dt],

                    [
                        _line_value(
                            ch["upper"],
                            s_dt.timestamp()
                        ),

                        _line_value(
                            ch["upper"],
                            e_dt.timestamp()
                        ),
                    ],

                    linestyle="--",

                    linewidth=1.8,

                    color=c["upper"],

                    alpha=0.85,
                )

                # Channel break marker.
                ax.axvline(
                    e_dt,
                    color="purple",
                    linestyle=":",
                    linewidth=1.0,
                    alpha=0.65
                )

        px = [

            datetime.fromtimestamp(
                float(p["time"]),
                tz=timezone.utc
            )

            for p in points
        ]

        py = [
            float(p["price"])
            for p in points
        ]

        # Main hook path.
        for ax in (
            ax_price,
            ax_ha
        ):

            ax.plot(
                px,
                py,
                marker="o",
                linewidth=2.7,
                color="royalblue",
                zorder=7,
                label="VALID CHANNEL HOOK"
            )

        offsets = [

            (0, -38),
            (0, 32),
            (0, -38),
            (0, 32),
            (0, -38),
            (0, 34),
        ]

        for p, label, off in zip(
            points,
            labels,
            offsets
        ):

            dt = (
                datetime.fromtimestamp(
                    float(p["time"]),
                    tz=timezone.utc
                )
            )

            is_final = (
                label in
                ("H3", "L3")
            )

            is_start = (
                label == "START"
            )

            price = float(
                p["price"]
            )

            for ax in (
                ax_price,
                ax_ha
            ):

                ax.scatter(

                    [dt],
                    [price],

                    s=(
                        145
                        if is_final
                        else
                        (
                            110
                            if is_start
                            else
                            88
                        )
                    ),

                    facecolors="white",

                    edgecolors=(
                        "purple"
                        if is_final
                        else
                        "royalblue"
                    ),

                    linewidths=(
                        2.2
                        if is_final
                        else
                        1.6
                    ),

                    zorder=10,
                )

                ax.annotate(

                    f"{label}\n"
                    f"{fmt_price(price)}",

                    (dt, price),

                    xytext=off,

                    textcoords="offset points",

                    ha="center",

                    va="center",

                    fontsize=(
                        10
                        if is_final
                        else
                        9
                    ),

                    fontweight="bold",

                    bbox=dict(

                        boxstyle="round,pad=.30",

                        fc=(
                            "#f7efff"
                            if is_final
                            else
                            "#fffdf2"
                        ),

                        ec=(
                            "purple"
                            if is_final
                            else
                            "royalblue"
                        ),

                        linewidth=1.5,

                        alpha=0.97,
                    ),

                    arrowprops=dict(
                        arrowstyle="-",
                        color="gray",
                        linewidth=0.8
                    ),

                    zorder=12,
                )

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        confirm_dt = (
            datetime.fromtimestamp(
                float(
                    hook[
                        "confirmation_time"
                    ]
                ),
                tz=timezone.utc
            )
        )

        right_dt = max(
            chart_right,
            confirm_dt
        )

        for ax in (
            ax_price,
            ax_ha
        ):

            ax.hlines(

                entry,

                chart_left,
                right_dt,

                linestyles="-.",

                linewidth=1.5,

                color="darkorange",

                label=(
                    f"ENTRY "
                    f"{fmt_price(entry)}"
                ),
            )

            ax.hlines(

                tp,

                chart_left,
                right_dt,

                linestyles="-.",

                linewidth=1.7,

                color="seagreen",

                label=(
                    f"TP 86.4% "
                    f"{fmt_price(tp)}"
                ),
            )

            if sl is not None:

                ax.hlines(

                    float(sl),

                    chart_left,
                    right_dt,

                    linestyles="-.",

                    linewidth=1.7,

                    color="crimson",

                    label=(
                        f"SL "
                        f"{fmt_price(sl)}"
                    ),
                )

            ax.axvline(

                confirm_dt,

                linestyle=":",

                linewidth=1.1,

                color="purple",

                label="ENTRY CONFIRMED"
            )

            ax.grid(
                alpha=0.22
            )

            ax.legend(
                loc="best",
                fontsize=8,
                ncol=2
            )

            ax.set_xlim(
                chart_left,
                right_dt
            )

        if hook["direction"] == "SHORT":

            title = (
                "SHORT: "
                "START→H1→L1→H2→L2→H3"
            )

        else:

            title = (
                "LONG: "
                "START→L1→H1→L2→H2→L3"
            )

        ax_price.set_title(

            f"NDS M5 CHANNEL HOOK | "
            f"{symbol} | "
            f"{hook['direction']}\n"

            f"{title}\n"

            "REAL PRICE CANDLES + "
            "CHANNEL BREAKS"
        )

        ax_price.set_ylabel(
            "Price"
        )

        ax_ha.set_title(
            "HEIKIN ASHI | "
            "STRICT HA NODES + "
            "SAME CHANNEL STRUCTURE"
        )

        ax_ha.set_ylabel(
            "HA Price"
        )

        ax_ha.set_xlabel(
            "Time UTC"
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        path = os.path.join(

            CHART_DIR,

            (
                f"{path_prefix}_"
                f"{symbol.replace('/', '_').replace(':', '_')}_"
                f"{hook['direction']}_"
                f"{int(hook['final']['time'])}.png"
            )
        )

        plt.savefig(
            path,
            dpi=155
        )

        plt.close(fig)

        return path

    except Exception as e:

        DIAG["chart_errors"] += 1

        DIAG["api_errors"].append(
            f"Chart {symbol}: "
            f"{str(e)[:180]}"
        )

        plt.close("all")

        return None


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    conn = db_connect()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS hooks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_key TEXT UNIQUE,
            symbol TEXT,
            direction TEXT,
            start_price REAL,
            h1_price REAL,
            l1_price REAL,
            h2_price REAL,
            l2_price REAL,
            final_price REAL,
            entry REAL,
            tp REAL,
            sl REAL,
            range_pct REAL,
            confirmation_time INTEGER,
            created_time INTEGER,
            detected_time INTEGER,
            chart_path TEXT,
            channels_json TEXT
        );

        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_key TEXT UNIQUE,
            symbol TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            opened_at INTEGER,
            closed_at INTEGER,
            close_price REAL,
            status TEXT,
            pnl_pct REAL,
            pnl_price REAL,
            chart_path TEXT
        );

        CREATE TABLE IF NOT EXISTS performance_ledger(
            id INTEGER PRIMARY KEY CHECK(id=1),
            total_closed INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            cumulative_pnl_pct REAL NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS performance_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            total_closed INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            cumulative_pnl_pct REAL NOT NULL
        );
        """
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO
        performance_ledger(
            id,
            updated_at
        )
        VALUES(1,?)
        """,
        (
            utc_now_ts(),
        )
    )

    conn.commit()

    conn.close()

    migrate_legacy_performance()


# ============================================================
# META
# ============================================================

def _meta_get(
    key
):

    c = db_connect()

    row = c.execute(
        "SELECT value FROM meta WHERE key=?",
        (key,)
    ).fetchone()

    c.close()

    if row is None:
        return None

    return row["value"]


def _meta_set(
    key,
    value
):

    c = db_connect()

    c.execute(

        "INSERT OR REPLACE INTO "
        "meta(key,value) VALUES(?,?)",

        (
            key,
            str(value)
        )
    )

    c.commit()

    c.close()


# ============================================================
# LEGACY PERFORMANCE IMPORT
# ============================================================

def migrate_legacy_performance():

    if (
        _meta_get(
            "legacy_performance_imported"
        )
        ==
        "1"
    ):

        return

    if not os.path.exists(
        LEGACY_DB_FILE
    ):

        _meta_set(
            "legacy_performance_imported",
            "1"
        )

        return

    try:

        old = sqlite3.connect(
            LEGACY_DB_FILE
        )

        old.row_factory = sqlite3.Row

        cols = [
            r[1]
            for r in old.execute(
                "PRAGMA table_info(trades)"
            ).fetchall()
        ]

        if not cols:

            old.close()

            _meta_set(
                "legacy_performance_imported",
                "1"
            )

            return

        rows = old.execute(

            """
            SELECT
                status,
                pnl_pct
            FROM trades
            WHERE status IN ('TP','SL')
            """
        ).fetchall()

        old.close()

        if not rows:

            _meta_set(
                "legacy_performance_imported",
                "1"
            )

            return

        wins = sum(
            1
            for r in rows
            if str(
                r["status"]
            ).upper()
            ==
            "TP"
        )

        losses = sum(
            1
            for r in rows
            if str(
                r["status"]
            ).upper()
            ==
            "SL"
        )

        pnl = sum(

            float(
                r["pnl_pct"]
                or 0.0
            )

            for r in rows
        )

        total = len(rows)

        conn = db_connect()

        current = conn.execute(
            """
            SELECT *
            FROM performance_ledger
            WHERE id=1
            """
        ).fetchone()

        if (
            current
            and
            int(
                current[
                    "total_closed"
                ]
            )
            ==
            0
        ):

            conn.execute(

                """
                UPDATE performance_ledger
                SET
                    total_closed=?,
                    wins=?,
                    losses=?,
                    cumulative_pnl_pct=?,
                    updated_at=?
                WHERE id=1
                """,

                (
                    total,
                    wins,
                    losses,
                    pnl,
                    utc_now_ts()
                )
            )

            conn.execute(

                """
                INSERT INTO
                performance_snapshots(
                    created_at,
                    total_closed,
                    wins,
                    losses,
                    cumulative_pnl_pct
                )
                VALUES(?,?,?,?,?)
                """,

                (
                    utc_now_ts(),
                    total,
                    wins,
                    losses,
                    pnl,
                )
            )

            DIAG[
                "performance_imported"
            ] = total

        conn.commit()

        conn.close()

        _meta_set(
            "legacy_performance_imported",
            "1"
        )

    except Exception as e:

        DIAG[
            "api_errors"
        ].append(
            "Legacy performance import: "
            f"{str(e)[:180]}"
        )


# ============================================================
# DB HELPERS
# ============================================================

def hook_exists(
    key
):

    c = db_connect()

    r = c.execute(
        """
        SELECT 1
        FROM hooks
        WHERE hook_key=?
        """,
        (key,)
    ).fetchone()

    c.close()

    return r is not None


def trade_exists(
    key
):

    c = db_connect()

    r = c.execute(
        """
        SELECT 1
        FROM trades
        WHERE hook_key=?
        """,
        (key,)
    ).fetchone()

    c.close()

    return r is not None


def open_trade_count():

    c = db_connect()

    r = c.execute(
        """
        SELECT COUNT(*) c
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()

    c.close()

    return int(
        r["c"]
    )


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(
    symbol,
    hook,
    sl,
    chart_path=None
):

    key = hook_id(
        symbol,
        hook
    )

    channels_json = json.dumps(

        [
            _serialize_channel(ch)
            for ch in hook.get(
                "channels",
                []
            )
        ],

        separators=(
            ",",
            ":"
        )
    )

    c = db_connect()

    c.execute(

        """
        INSERT OR IGNORE INTO hooks(
            hook_key,
            symbol,
            direction,
            start_price,
            h1_price,
            l1_price,
            h2_price,
            l2_price,
            final_price,
            entry,
            tp,
            sl,
            range_pct,
            confirmation_time,
            created_time,
            detected_time,
            chart_path,
            channels_json
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,

        (

            key,

            symbol,

            hook["direction"],

            hook["start"]["price"],

            hook.get(
                "h1",
                {}
            ).get(
                "price"
            ),

            hook.get(
                "l1",
                {}
            ).get(
                "price"
            ),

            hook.get(
                "h2",
                {}
            ).get(
                "price"
            ),

            hook.get(
                "l2",
                {}
            ).get(
                "price"
            ),

            hook["final"]["price"],

            hook["entry"],

            hook["tp"],

            sl,

            hook["range_pct"],

            int(
                hook[
                    "confirmation_time"
                ]
            ),

            int(
                hook[
                    "created_time"
                ]
            ),

            utc_now_ts(),

            chart_path,

            channels_json,
        )
    )

    c.commit()

    c.close()

    return key


# ============================================================
# SAVE TRADE
# ============================================================

def save_trade(
    key,
    symbol,
    hook,
    sl,
    chart_path=None
):

    c = db_connect()

    c.execute(

        """
        INSERT OR IGNORE INTO trades(
            hook_key,
            symbol,
            direction,
            entry,
            sl,
            tp,
            opened_at,
            status,
            chart_path
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?
        )
        """,

        (

            key,

            symbol,

            hook["direction"],

            hook["entry"],

            sl,

            hook["tp"],

            int(
                hook[
                    "confirmation_time"
                ]
            ),

            "OPEN",

            chart_path,
        )
    )

    c.commit()

    c.close()


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook,
    sl
):

    d = hook["direction"]

    emoji = (
        "🔴"
        if d == "SHORT"
        else
        "🟢"
    )

    entry, tp, sl = map(
        float,
        [
            hook["entry"],
            hook["tp"],
            sl
        ]
    )

    risk = (
        (sl - entry) / entry * 100
        if d == "SHORT"
        else
        (sl - entry) / entry * 100
    )

    reward = (
        (tp - entry)
        /
        entry
        *
        100
    )

    if d == "SHORT":

        node_line = (
            "START → H1 → L1 → "
            "H2 → L2 → H3"
        )

    else:

        node_line = (
            "START → L1 → H1 → "
            "L2 → H2 → L3"
        )

    final_label = (
        "H3"
        if d == "SHORT"
        else
        "L3"
    )

    return (

        f"{emoji} "
        f"<b>NDS {d} SIGNAL | "
        f"M5 CHANNEL</b>\n"

        f"<b>{symbol}</b>\n\n"

        f"Hook: "
        f"<b>{node_line}</b>\n"

        f"Entry {final_label}: "
        f"<b>{fmt_price(entry)}</b>\n"

        f"SL nearest valid previous node: "
        f"<b>{fmt_price(sl)}</b> "
        f"({risk:+.2f}%)\n"

        f"TP 86.4%: "
        f"<b>{fmt_price(tp)}</b> "
        f"({reward:+.2f}%)\n"

        f"Start → Final range: "
        f"<b>{hook['range_pct']:.2f}%</b>\n"

        f"Confirmed: "
        f"{fmt_ts(hook['confirmation_time'])}\n"

        f"<b>PAPER TRADING ONLY</b>"
    )


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    c = db_connect()

    rows = c.execute(

        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY opened_at ASC
        """
    ).fetchall()

    c.close()

    return rows


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    price,
    reason
):

    entry = float(
        trade["entry"]
    )

    direction = (
        trade["direction"]
    )

    if direction == "SHORT":

        pnl_price = (
            entry
            -
            price
        )

    else:

        pnl_price = (
            price
            -
            entry
        )

    pnl_pct = (
        pnl_price
        /
        entry
        *
        100
        if entry
        else
        0.0
    )

    c = db_connect()

    try:

        c.execute(
            "BEGIN IMMEDIATE"
        )

        c.execute(

            """
            UPDATE trades
            SET
                closed_at=?,
                close_price=?,
                status=?,
                pnl_pct=?,
                pnl_price=?
            WHERE
                id=?
                AND
                status='OPEN'
            """,

            (
                utc_now_ts(),
                price,
                reason,
                pnl_pct,
                pnl_price,
                trade["id"],
            )
        )

        changed = c.execute(
            "SELECT changes() n"
        ).fetchone()["n"]

        if changed:

            ledger = c.execute(

                """
                SELECT *
                FROM performance_ledger
                WHERE id=1
                """
            ).fetchone()

            total = (
                int(
                    ledger[
                        "total_closed"
                    ]
                )
                + 1
            )

            wins = (
                int(
                    ledger[
                        "wins"
                    ]
                )
                +
                (
                    1
                    if reason == "TP"
                    else
                    0
                )
            )

            losses = (
                int(
                    ledger[
                        "losses"
                    ]
                )
                +
                (
                    1
                    if reason == "SL"
                    else
                    0
                )
            )

            cumulative = (
                float(
                    ledger[
                        "cumulative_pnl_pct"
                    ]
                )
                +
                pnl_pct
            )

            c.execute(

                """
                UPDATE performance_ledger
                SET
                    total_closed=?,
                    wins=?,
                    losses=?,
                    cumulative_pnl_pct=?,
                    updated_at=?
                WHERE id=1
                """,

                (
                    total,
                    wins,
                    losses,
                    cumulative,
                    utc_now_ts(),
                )
            )

        c.commit()

        return (
            pnl_pct,
            pnl_price,
            bool(changed)
        )

    except Exception:

        c.rollback()

        raise

    finally:

        c.close()


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol
):

    try:

        r = requests.get(
            f"{KRAKEN_FUTURES_URL}/tickers",
            timeout=REQUEST_TIMEOUT,
        )

        if r.ok:

            for t in r.json().get(
                "tickers",
                []
            ):

                if not isinstance(
                    t,
                    dict
                ):

                    continue

                ts = str(

                    t.get("symbol")
                    or
                    t.get("pair")
                    or
                    t.get("instrument")
                    or
                    ""
                )

                if ts != symbol:
                    continue

                for key in (
                    "last",
                    "lastPrice",
                    "markPrice",
                    "price"
                ):

                    if t.get(key) is not None:

                        return float(
                            t[key]
                        )

    except Exception as e:

        DIAG[
            "api_errors"
        ].append(
            f"Ticker {symbol}: "
            f"{str(e)[:160]}"
        )

    df = get_candles(
        symbol,
        STRATEGY_INTERVAL,
        2
    )

    if (
        df is None
        or
        df.empty
    ):

        return None

    return float(
        df.iloc[-1].close
    )


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():

    for trade in get_open_trades():

        try:

            symbol = trade["symbol"]

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

            opened_at = int(
                trade["opened_at"]
                or 0
            )

            df = get_candles(
                symbol,
                STRATEGY_INTERVAL,
                800
            )

            reason = None

            price = None

            if (
                df is not None
                and
                not df.empty
            ):

                future = df[
                    df.time >= opened_at
                ]

                for _, candle in future.iterrows():

                    hi = float(
                        candle.high
                    )

                    lo = float(
                        candle.low
                    )

                    if direction == "SHORT":

                        hit_sl = (
                            hi >= sl
                        )

                        hit_tp = (
                            lo <= tp
                        )

                    else:

                        hit_sl = (
                            lo <= sl
                        )

                        hit_tp = (
                            hi >= tp
                        )

                    # Conservative:
                    # if both hit in one candle,
                    # SL is assumed first.
                    if hit_sl:

                        price = sl

                        reason = "SL"

                        break

                    if hit_tp:

                        price = tp

                        reason = "TP"

                        break

            if reason is None:

                current = get_current_price(
                    symbol
                )

                if current is not None:

                    if direction == "SHORT":

                        if current >= sl:

                            price = sl

                            reason = "SL"

                        elif current <= tp:

                            price = tp

                            reason = "TP"

                    else:

                        if current <= sl:

                            price = sl

                            reason = "SL"

                        elif current >= tp:

                            price = tp

                            reason = "TP"

            if reason is None:
                continue

            (
                pnl_pct,
                pnl_price,
                changed
            ) = close_trade(
                trade,
                price,
                reason
            )

            if not changed:
                continue

            emoji = (
                "✅"
                if pnl_pct >= 0
                else
                "❌"
            )

            telegram_send(

                f"{emoji} "
                f"<b>NDS {direction} "
                f"CLOSED | M5</b>\n"

                f"<b>{symbol}</b>\n"

                f"Entry: "
                f"{fmt_price(entry)}\n"

                f"Close: "
                f"{fmt_price(price)}\n"

                f"Result: "
                f"<b>{reason}</b>\n"

                f"PnL: "
                f"<b>{pnl_pct:+.2f}%</b>\n"

                f"Cumulative Performance "
                f"updated in memory.\n"

                f"<b>PAPER TRADING ONLY</b>"
            )

        except Exception as e:

            DIAG[
                "api_errors"
            ].append(
                f"Monitor "
                f"{trade['symbol']}: "
                f"{str(e)[:180]}"
            )


# ============================================================
# OPEN TRADE METRICS
# ============================================================

def trade_metrics(
    entry,
    sl,
    tp,
    direction,
    current
):

    if direction == "LONG":

        pnl = (
            current
            -
            entry
        ) / entry * 100

        tp_pct = (
            tp
            -
            entry
        ) / entry * 100

        sl_pct = (
            sl
            -
            entry
        ) / entry * 100

    else:

        pnl = (
            entry
            -
            current
        ) / entry * 100

        tp_pct = (
            entry
            -
            tp
        ) / entry * 100

        sl_pct = (
            entry
            -
            sl
        ) / entry * 100

    return (
        pnl,
        tp_pct,
        sl_pct
    )


def open_trades_report_lines():

    rows = get_open_trades()

    lines = [
        "━━━ <b>OPEN TRADES</b> ━━━"
    ]

    if not rows:

        return lines + [
            "None"
        ]

    for t in rows:

        direction = (
            t["direction"]
        )

        symbol = (
            t["symbol"]
        )

        entry, sl, tp = map(
            float,
            [
                t["entry"],
                t["sl"],
                t["tp"],
            ]
        )

        current = get_current_price(
            symbol
        )

        icon = (
            "🟢"
            if direction == "LONG"
            else
            "🔴"
        )

        if current is None:

            lines.extend(
                [
                    (
                        f"{icon} "
                        f"<b>{symbol} "
                        f"{direction}</b>"
                    ),

                    (
                        f"Entry: "
                        f"{fmt_price(entry)}"
                    ),

                    "Current: -",

                    (
                        f"TP: "
                        f"{fmt_price(tp)}"
                    ),

                    (
                        f"SL: "
                        f"{fmt_price(sl)}"
                    ),

                    "",
                ]
            )

            continue

        (
            pnl,
            tp_pct,
            sl_pct
        ) = trade_metrics(

            entry,
            sl,
            tp,
            direction,
            current
        )

        lines.extend(
            [

                (
                    f"{icon} "
                    f"<b>{symbol} "
                    f"{direction}</b>"
                ),

                (
                    f"Entry: "
                    f"{fmt_price(entry)}"
                ),

                (
                    f"Current: "
                    f"<b>"
                    f"{fmt_price(current)} "
                    f"({pnl:+.2f}%)"
                    f"</b>"
                ),

                (
                    f"TP 86.4%: "
                    f"<b>"
                    f"{fmt_price(tp)} "
                    f"({tp_pct:+.2f}%)"
                    f"</b>"
                ),

                (
                    f"SL: "
                    f"<b>"
                    f"{fmt_price(sl)} "
                    f"({sl_pct:+.2f}%)"
                    f"</b>"
                ),

                "",
            ]
        )

    if lines[-1] == "":
        lines.pop()

    return lines


# ============================================================
# PERFORMANCE
# ============================================================

def performance_summary():

    c = db_connect()

    row = c.execute(
        """
        SELECT *
        FROM performance_ledger
        WHERE id=1
        """
    ).fetchone()

    c.close()

    if not row:

        return {

            "total": 0,

            "wins": 0,

            "losses": 0,

            "pnl": 0.0,

            "winrate": 0.0,
        }

    total = int(
        row["total_closed"]
    )

    wins = int(
        row["wins"]
    )

    losses = int(
        row["losses"]
    )

    pnl = float(
        row["cumulative_pnl_pct"]
    )

    winrate = (
        wins
        /
        total
        *
        100
        if total
        else
        0.0
    )

    return {

        "total":
            total,

        "wins":
            wins,

        "losses":
            losses,

        "pnl":
            pnl,

        "winrate":
            winrate,
    }


def save_performance_snapshot():

    p = performance_summary()

    c = db_connect()

    c.execute(

        """
        INSERT INTO performance_snapshots(
            created_at,
            total_closed,
            wins,
            losses,
            cumulative_pnl_pct
        )
        VALUES(?,?,?,?,?)
        """,

        (

            utc_now_ts(),

            p["total"],

            p["wins"],

            p["losses"],

            p["pnl"],
        )
    )

    c.commit()

    c.close()


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_text():

    p = performance_summary()

    lines = [

        "🔎 "
        "<b>NDS M5 "
        "CHANNEL-HOOK "
        "DIAGNOSTIC</b>",

        (
            f"Version: "
            f"<b>{VERSION}</b>"
        ),

        (
            f"Time: "
            f"{utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        ),

        (
            f"Runtime: "
            f"<b>{time.time() - START_TIME:.1f}s</b>"
        ),

        "",

        "━━━ <b>ASSETS</b> ━━━",

        (
            f"Scanned: "
            f"<b>{DIAG['assets_scanned']}</b>"
        ),

        (
            f"XAUT Included: "
            f"<b>{DIAG['xaut_found']}</b>"
        ),

        (
            f"NEAR Excluded: "
            f"<b>{DIAG['near_excluded']}</b>"
        ),

        "",

        "━━━ "
        "<b>M5 / HA NODE ENGINE</b> "
        "━━━",

        (
            f"Requests: "
            f"{DIAG['m5_requests']}"
        ),

        (
            f"Data OK: "
            f"{DIAG['m5_data_ok']}"
        ),

        (
            f"Data Error: "
            f"{DIAG['m5_data_error']}"
        ),

        (
            f"Empty: "
            f"{DIAG['m5_empty']}"
        ),

        (
            f"Short: "
            f"{DIAG['m5_short']}"
        ),

        (
            f"Valid High Nodes: "
            f"{DIAG['valid_high_nodes']}"
        ),

        (
            f"Valid Low Nodes: "
            f"{DIAG['valid_low_nodes']}"
        ),

        (
            f"Rejected Non-Extreme: "
            f"{DIAG['rejected_non_extreme']}"
        ),

        (
            f"Same-Turn Collapsed: "
            f"{DIAG['nodes_collapsed']}"
        ),

        (
            f"Total Valid HA Nodes: "
            f"{DIAG['m5_nodes']}"
        ),

        "",

        "━━━ "
        "<b>CHANNEL ENGINE</b> "
        "━━━",

        (
            f"Channel Candidates: "
            f"{DIAG['channel_candidates']}"
        ),

        (
            f"Valid Ascending Channels: "
            f"{DIAG['valid_up_channels']}"
        ),

        (
            f"Valid Descending Channels: "
            f"{DIAG['valid_down_channels']}"
        ),

        (
            f"Confirmed Channel Breaks: "
            f"{DIAG['channel_breaks']}"
        ),

        (
            f"Positive / SHORT Hooks: "
            f"{DIAG['positive_hooks']}"
        ),

        (
            f"Negative / LONG Hooks: "
            f"{DIAG['negative_hooks']}"
        ),

        (
            f"Confirmed Hooks: "
            f"{DIAG['confirmed_hooks']}"
        ),

        (
            f"Recent Hooks ≤ "
            f"{SIGNAL_LOOKBACK_HOURS}h: "
            f"{DIAG['recent_hooks']}"
        ),

        (
            f"Range Valid ≥ "
            f"{MIN_HOOK_RANGE_PCT:.2f}%: "
            f"{DIAG['range_valid_hooks']}"
        ),

        (
            f"TP Already Touched: "
            f"{DIAG['tp_touched']}"
        ),

        (
            f"SL Found: "
            f"{DIAG['sl_found']}"
        ),

        (
            f"SL Missing: "
            f"{DIAG['sl_missing']}"
        ),

        (
            f"Geometry Valid: "
            f"{DIAG['geometry_valid']}"
        ),

        (
            f"Duplicates: "
            f"{DIAG['duplicates']}"
        ),

        (
            f"Max Open: "
            f"{DIAG['max_open']}"
        ),

        (
            f"Signal Ready: "
            f"{DIAG['signal_ready']}"
        ),

        "",

        "━━━ <b>SIGNALS</b> ━━━",

        (
            f"Signals: "
            f"<b>{DIAG['signals']}</b>"
        ),

        (
            f"Charts Sent: "
            f"{DIAG['chart_sent']}"
        ),

        (
            f"Chart Errors: "
            f"{DIAG['chart_errors']}"
        ),

        (
            f"Open Trades: "
            f"<b>{open_trade_count()}</b>"
        ),

        "",

        *open_trades_report_lines(),

        "",

        "━━━ "
        "<b>CUMULATIVE "
        "PAPER PERFORMANCE</b> "
        "━━━",

        (
            f"Closed Trades: "
            f"{p['total']}"
        ),

        (
            f"TP: "
            f"{p['wins']}"
        ),

        (
            f"SL: "
            f"{p['losses']}"
        ),

        (
            f"Win Rate: "
            f"{p['winrate']:.2f}%"
        ),

        (
            f"Cumulative PnL: "
            f"<b>{p['pnl']:+.2f}%</b>"
        ),

        (
            f"Legacy Imported This Run: "
            f"{DIAG['performance_imported']}"
        ),
    ]

    if DIAG[
        "api_errors"
    ]:

        lines += [

            "",

            "━━━ "
            "<b>ERRORS</b> "
            "━━━"

        ] + [

            "• " + e
            for e in
            DIAG[
                "api_errors"
            ][-6:]
        ]

    lines += [

        "",

        "<b>"
        "PAPER TRADING ONLY - "
        "NO REAL ORDERS"
        "</b>",
    ]

    return "\n".join(
        lines
    )


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(
    symbol
):

    DIAG[
        "m5_requests"
    ] += 1

    df = get_candles(

        symbol,

        STRATEGY_INTERVAL,

        STRATEGY_CANDLES
    )

    if df is None:

        DIAG[
            "m5_data_error"
        ] += 1

        return

    if df.empty:

        DIAG[
            "m5_empty"
        ] += 1

        return

    if len(df) < 250:

        DIAG[
            "m5_short"
        ] += 1

        return

    DIAG[
        "m5_data_ok"
    ] += 1

    # HA data is the node source.
    ha = calculate_heikin_ashi(
        df
    )

    highs, lows = find_pivots(
        ha
    )

    DIAG[
        "m5_nodes"
    ] += (
        len(highs)
        +
        len(lows)
    )

    hooks = detect_channel_hooks(
        ha,
        highs,
        lows
    )

    now = utc_now_ts()

    # Only the latest eligible hook
    # per direction is considered per symbol.
    latest_by_direction = {}

    for hook in hooks:

        latest_by_direction[
            hook["direction"]
        ] = hook

    for direction, hook in sorted(

        latest_by_direction.items(),

        key=lambda x:
            float(
                x[1][
                    "confirmation_time"
                ]
            )
    ):

        if not hook_is_confirmed(
            hook,
            now
        ):

            continue

        DIAG[
            "confirmed_hooks"
        ] += 1

        if not hook_is_recent(
            hook,
            now
        ):

            continue

        DIAG[
            "recent_hooks"
        ] += 1

        if (
            hook["range_pct"]
            <
            MIN_HOOK_RANGE_PCT
        ):

            continue

        DIAG[
            "range_valid_hooks"
        ] += 1

        if tp_already_touched(
            df,
            hook
        ):

            DIAG[
                "tp_touched"
            ] += 1

            continue

        sl = calculate_m5_sl(
            ha,
            hook
        )

        if sl is None:
            continue

        DIAG[
            "sl_found"
        ] += 1

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        sl = float(sl)

        geometry_ok = (

            (
                sl
                >
                entry
                >
                tp
            )

            if hook["direction"] == "SHORT"

            else

            (
                sl
                <
                entry
                <
                tp
            )
        )

        if not geometry_ok:
            continue

        DIAG[
            "geometry_valid"
        ] += 1

        key = hook_id(
            symbol,
            hook
        )

        if (
            hook_exists(key)
            or
            trade_exists(key)
        ):

            DIAG[
                "duplicates"
            ] += 1

            continue

        if (
            open_trade_count()
            >=
            MAX_OPEN_TRADES
        ):

            DIAG[
                "max_open"
            ] += 1

            return

        DIAG[
            "signal_ready"
        ] += 1

        chart = create_hook_chart(

            symbol,

            df,

            hook,

            sl,

            "signal_m5"
        )

        save_hook(
            symbol,
            hook,
            sl,
            chart
        )

        save_trade(
            key,
            symbol,
            hook,
            sl,
            chart
        )

        DIAG[
            "signals"
        ] += 1

        # Signal text.
        telegram_send(
            build_signal_message(
                symbol,
                hook,
                sl
            )
        )

        # Full chart.
        if chart:

            if hook["direction"] == "SHORT":

                hook_caption = (
                    "START→H1→L1→H2→L2→H3"
                )

            else:

                hook_caption = (
                    "START→L1→H1→L2→H2→L3"
                )

            sent = telegram_send_photo(

                chart,

                f"📍 "
                f"<b>NDS "
                f"{hook['direction']} "
                f"SIGNAL | "
                f"M5 CHANNEL</b>\n"

                f"<b>{symbol}</b>\n"

                f"Entry: "
                f"{fmt_price(entry)}\n"

                f"TP 86.4%: "
                f"{fmt_price(tp)}\n"

                f"SL: "
                f"{fmt_price(sl)}\n"

                f"Hook: "
                f"{hook_caption}\n"

                f"GREEN = ascending "
                f"channel\n"

                f"RED = descending "
                f"channel\n"

                f"PAPER TRADING ONLY"
            )

            if sent:

                DIAG[
                    "chart_sent"
                ] += 1

        # One new signal per symbol per scan.
        return


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        init_db()

        print(
            f"NDS M5 Channel Hook "
            f"Scanner {VERSION}"
        )

        print(
            "M5 ONLY"
        )

        print(
            "NODES = STRICT "
            "HEIKIN ASHI TURNS"
        )

        print(
            "CHANNELS = 2 LOW TOUCHES "
            "+ 2 HIGH TOUCHES "
            "+ PARALLEL SLOPES"
        )

        print(
            "SHORT = "
            "START(L) -> UP BREAK -> H1 "
            "-> DOWN BREAK -> L1 "
            "-> UP BREAK -> H2 "
            "-> DOWN BREAK -> L2 "
            "-> UP BREAK -> H3"
        )

        print(
            "LONG  = "
            "START(H) -> DOWN BREAK -> L1 "
            "-> UP BREAK -> H1 "
            "-> DOWN BREAK -> L2 "
            "-> UP BREAK -> H2 "
            "-> DOWN BREAK -> L3"
        )

        print(
            "TP = 86.4% RETRACEMENT "
            "| SL = NEAREST PREVIOUS "
            "VALID OPPOSITE HA NODE "
            "+/- BUFFER"
        )

        print(
            "PAPER TRADING ONLY - "
            "NO REAL ORDERS"
        )

        symbols = get_futures_instruments()

        if not symbols:

            msg = (

                "❌ "
                "<b>NDS M5 Channel Scanner</b>\n\n"
                "No futures instruments found."
            )

            print(msg)

            telegram_send(msg)

            return

        DIAG[
            "assets_scanned"
        ] = len(symbols)

        # First monitor existing positions.
        monitor_open_trades()

        # Scan all selected assets.
        for symbol in symbols:

            try:

                process_symbol(
                    symbol
                )

            except Exception as e:

                DIAG[
                    "api_errors"
                ].append(
                    f"Process {symbol}: "
                    f"{str(e)[:180]}"
                )

                traceback.print_exc()

            time.sleep(
                SCAN_SLEEP_SECONDS
            )

        # Re-check trades.
        monitor_open_trades()

        # Build final report.
        report = diagnostic_text()

        print(
            "\n"
            +
            report
            +
            "\n"
        )

        # Send report.
        telegram_send(
            report
        )

        # IMPORTANT:
        # persistent performance snapshot
        # is updated after the report.
        save_performance_snapshot()

    except Exception as e:

        traceback.print_exc()

        telegram_send(

            "❌ "
            "<b>NDS M5 Channel Scanner Error</b>\n\n"

            f"{type(e).__name__}: "
            f"{str(e)[:500]}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
