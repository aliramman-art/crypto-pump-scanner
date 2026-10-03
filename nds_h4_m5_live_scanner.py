# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.7.1
# PAPER TRADING ONLY - NO REAL ORDERS
#
# H4 confirmed H3/L3 activates same-direction M5 hook search
# Entry at confirmed M5 H3/L3
# TP = M5 hook 86.4%
# SL = nearest valid CONFIRMED H4 HA pivot
#
# IMPORTANT:
# - Hook nodes are built from Heikin Ashi candles
# - Trade monitoring uses REAL OHLC candles
# - SHORT => SL above nearest confirmed H4 HA High
# - LONG  => SL below nearest confirmed H4 HA Low
# - H4 activation H3/L3 is also eligible as a confirmed H4 pivot
# - No real exchange orders
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.7.1"
REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

TARGET_ASSETS = 100

H4_INTERVAL = "4h"
M5_INTERVAL = "5m"

H4_INTERVAL_MINUTES = 240
M5_INTERVAL_MINUTES = 5

H4_CANDLES = 320
M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

NDS_RETRACE = 0.864

M5_MIN_HOOK_RANGE_PCT = 0.20

H4_SL_BUFFER_PCT = 0.15

H4_MAX_HOOK_AGE_SECONDS = 24 * 3600
M5_MAX_HOOK_AGE_SECONDS = 6 * 3600

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25

CHART_CANDLES = 240

DB_FILE = "nds_h4_m5_v545.db"
CHART_DIR = "nds_h4_m5_charts"

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
    k: 0
    for k in [
        "assets_scanned",

        "h4_requests",
        "h4_data_ok",
        "h4_data_error",
        "h4_empty",
        "h4_short",
        "h4_pivots",
        "h4_hooks",
        "h4_confirmed",
        "h4_recent",
        "h4_valid_hooks",
        "h4_short_direction",
        "h4_long_direction",
        "h4_filtered",

        "m5_requests",
        "m5_data_ok",
        "m5_data_error",
        "m5_empty",
        "m5_short",
        "m5_pivots",
        "m5_hooks",
        "m5_confirmed",
        "m5_recent",
        "m5_range_valid",
        "m5_after_h4_activation",
        "m5_tp_touched",
        "m5_sl_found",
        "m5_geometry_valid",
        "m5_duplicate",
        "m5_max_open",

        "signal_ready",

        "hook_charts_sent",
        "hook_chart_errors",

        "signals",
        "duplicate_signals",
        "max_open",
    ]
}

DIAG["api_errors"] = []

# Store H4 state by symbol
H4_DIRECTION = {}
H4_DATA = {}

START_TIME = time.time()


# ============================================================
# BASIC HELPERS
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


def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not telegram_enabled():
        return False

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
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
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                files={"photo": photo},
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
# KRAKEN DATA
# ============================================================

def get_futures_instruments():
    try:
        r = requests.get(
            f"{KRAKEN_FUTURES_URL}/instruments",
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        symbols = []

        for item in r.json().get("instruments", []):
            s = str(
                item.get("symbol")
                or item.get("instrument")
                or ""
            )

            if (
                s.startswith("PF_")
                and "USD" in s
                and item.get("tradeable", True) is not False
            ):
                symbols.append(s)

        return list(dict.fromkeys(symbols))[:TARGET_ASSETS]

    except Exception as e:
        DIAG["api_errors"].append(
            f"Instruments: {str(e)[:180]}"
        )
        return []


def get_candles(symbol, interval, count):
    try:
        r = requests.get(
            f"{KRAKEN_CHART_URL}/trade/{symbol}/{interval}",
            params={"count": int(count)},
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:
            DIAG["api_errors"].append(
                f"{symbol} {interval} HTTP {r.status_code}"
            )
            return None

        rows = []

        for c in r.json().get("candles", []):

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

            elif (
                isinstance(c, (list, tuple))
                and len(c) >= 5
            ):
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
                errors="coerce",
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

        return (
            df
            .sort_values("time")
            .drop_duplicates("time")
            .reset_index(drop=True)
        )

    except Exception as e:
        DIAG["api_errors"].append(
            f"{symbol} {interval}: {str(e)[:180]}"
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
        ha["open"]
        + ha["high"]
        + ha["low"]
        + ha["close"]
    ) / 4.0

    opens = []

    for i in range(len(ha)):

        if i == 0:
            value = (
                float(ha.iloc[i].open)
                + float(ha.iloc[i].close)
            ) / 2.0
        else:
            value = (
                opens[i - 1]
                + float(ha.iloc[i - 1].ha_close)
            ) / 2.0

        opens.append(value)

    ha["ha_open"] = opens

    ha["ha_high"] = ha[
        ["high", "ha_open", "ha_close"]
    ].max(axis=1)

    ha["ha_low"] = ha[
        ["low", "ha_open", "ha_close"]
    ].min(axis=1)

    return ha


def heikin_ashi_ohlc(df):
    ha = calculate_heikin_ashi(df)

    out = df.copy().reset_index(drop=True)

    out["open"] = ha["ha_open"]
    out["high"] = ha["ha_high"]
    out["low"] = ha["ha_low"]
    out["close"] = ha["ha_close"]

    return out


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):
    highs = []
    lows = []

    if (
        df is None
        or df.empty
        or len(df) < PIVOT_LEFT + PIVOT_RIGHT + 1
    ):
        return highs, lows

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT,
    ):

        hi = float(df.iloc[i].high)
        lo = float(df.iloc[i].low)

        left_highs = [
            float(df.iloc[j].high)
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_highs = [
            float(df.iloc[j].high)
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        left_lows = [
            float(df.iloc[j].low)
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_lows = [
            float(df.iloc[j].low)
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        if (
            all(hi > x for x in left_highs)
            and all(hi >= x for x in right_highs)
        ):
            highs.append(
                {
                    "type": "H",
                    "index": i,
                    "time": float(df.iloc[i].time),
                    "price": hi,
                }
            )

        if (
            all(lo < x for x in left_lows)
            and all(lo <= x for x in right_lows)
        ):
            lows.append(
                {
                    "type": "L",
                    "index": i,
                    "time": float(df.iloc[i].time),
                    "price": lo,
                }
            )

    return highs, lows


def build_ordered_pivots(highs, lows):
    result = []

    for p in sorted(
        highs + lows,
        key=lambda x: (
            x["time"],
            x["index"]
        )
    ):

        if (
            not result
            or p["type"] != result[-1]["type"]
        ):
            result.append(p)

        elif (
            p["type"] == "H"
            and p["price"] >= result[-1]["price"]
        ):
            result[-1] = p

        elif (
            p["type"] == "L"
            and p["price"] <= result[-1]["price"]
        ):
            result[-1] = p

    return result


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks(df, interval_minutes):
    if df is None or df.empty:
        return []

    highs, lows = find_pivots(df)
    ordered = build_ordered_pivots(
        highs,
        lows,
    )

    hooks = []

    confirm_secs = (
        interval_minutes
        * 60
        * PIVOT_RIGHT
    )

    for i in range(
        max(0, len(ordered) - 5)
    ):

        p = ordered[i:i + 6]

        if len(p) < 6:
            continue

        types = [
            x["type"]
            for x in p
        ]

        # ----------------------------------------------------
        # POSITIVE HOOK / SHORT
        # START(L) -> H1 -> L1 -> H2 -> L2 -> H3
        # ----------------------------------------------------

        if types == [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:

            (
                start,
                h1,
                l1,
                h2,
                l2,
                h3,
            ) = p

            if not (
                h2["price"] > h1["price"]
                and l2["price"] < l1["price"]
                and h3["price"] > h2["price"]
            ):
                continue

            if not (
                start["price"] < l1["price"]
                and start["price"] < l2["price"]
            ):
                continue

            rng = (
                h3["price"]
                - start["price"]
            )

            if (
                rng <= 0
                or start["price"] <= 0
            ):
                continue

            hooks.append(
                {
                    "direction": "SHORT",
                    "start": start,
                    "h1": h1,
                    "l1": l1,
                    "h2": h2,
                    "l2": l2,
                    "h3": h3,
                    "final": h3,
                    "entry": h3["price"],
                    "tp": (
                        h3["price"]
                        - NDS_RETRACE * rng
                    ),
                    "range_pct": (
                        rng
                        / start["price"]
                        * 100
                    ),
                    "confirmation_time": (
                        h3["time"]
                        + confirm_secs
                    ),
                    "created_time": h3["time"],
                }
            )

        # ----------------------------------------------------
        # NEGATIVE HOOK / LONG
        # START(H) -> L1 -> H1 -> L2 -> H2 -> L3
        # ----------------------------------------------------

        elif types == [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:

            (
                start,
                l1,
                h1,
                l2,
                h2,
                l3,
            ) = p

            if not (
                l2["price"] < l1["price"]
                and h2["price"] > h1["price"]
                and l3["price"] < l2["price"]
            ):
                continue

            if not (
                start["price"] > h1["price"]
                and start["price"] > h2["price"]
            ):
                continue

            rng = (
                start["price"]
                - l3["price"]
            )

            if (
                rng <= 0
                or start["price"] <= 0
            ):
                continue

            hooks.append(
                {
                    "direction": "LONG",
                    "start": start,
                    "l1": l1,
                    "h1": h1,
                    "l2": l2,
                    "h2": h2,
                    "l3": l3,
                    "final": l3,
                    "entry": l3["price"],
                    "tp": (
                        l3["price"]
                        + NDS_RETRACE * rng
                    ),
                    "range_pct": (
                        rng
                        / start["price"]
                        * 100
                    ),
                    "confirmation_time": (
                        l3["time"]
                        + confirm_secs
                    ),
                    "created_time": l3["time"],
                }
            )

    return hooks


def hook_id(symbol, hook):
    return (
        f"{symbol}|"
        f"{hook['direction']}|"
        f"{int(hook['final']['time'])}|"
        f"{hook['final']['price']:.12f}"
    )


def hook_is_confirmed(hook, now_ts=None):
    if now_ts is None:
        now_ts = utc_now_ts()

    return (
        hook["confirmation_time"]
        <= now_ts
    )


def hook_is_recent(
    hook,
    now_ts=None,
    max_age_seconds=None,
):

    if now_ts is None:
        now_ts = utc_now_ts()

    if max_age_seconds is None:
        max_age_seconds = M5_MAX_HOOK_AGE_SECONDS

    age = (
        now_ts
        - hook["confirmation_time"]
    )

    return (
        0 <= age <= max_age_seconds
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    conn = db_connect()

    conn.executescript(
        """
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
            chart_path TEXT
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

        CREATE TABLE IF NOT EXISTS hook_chart_notifications(
            hook_key TEXT PRIMARY KEY,
            sent_at INTEGER,
            chart_path TEXT
        );
        """
    )

    conn.commit()
    conn.close()


def hook_exists(key):
    conn = db_connect()

    r = conn.execute(
        "SELECT 1 FROM hooks WHERE hook_key=?",
        (key,),
    ).fetchone()

    conn.close()

    return r is not None


def trade_exists(key):
    conn = db_connect()

    r = conn.execute(
        "SELECT 1 FROM trades WHERE hook_key=?",
        (key,),
    ).fetchone()

    conn.close()

    return r is not None


def open_trade_count():
    conn = db_connect()

    r = conn.execute(
        """
        SELECT COUNT(*) c
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()

    conn.close()

    return int(r["c"])


def save_hook(
    symbol,
    hook,
    sl,
    chart_path=None,
):

    key = hook_id(
        symbol,
        hook,
    )

    conn = db_connect()

    conn.execute(
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
            chart_path
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            key,
            symbol,
            hook["direction"],
            hook["start"]["price"],
            hook.get("h1", {}).get("price"),
            hook.get("l1", {}).get("price"),
            hook.get("h2", {}).get("price"),
            hook.get("l2", {}).get("price"),
            hook["final"]["price"],
            hook["entry"],
            hook["tp"],
            sl,
            hook["range_pct"],
            int(hook["confirmation_time"]),
            int(hook["created_time"]),
            utc_now_ts(),
            chart_path,
        ),
    )

    conn.commit()
    conn.close()

    return key


def save_trade(
    key,
    symbol,
    hook,
    sl,
    chart_path=None,
):

    conn = db_connect()

    conn.execute(
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
            ?,?,?,?,?,?,?,?
        )
        """,
        (
            key,
            symbol,
            hook["direction"],
            hook["entry"],
            sl,
            hook["tp"],
            utc_now_ts(),
            "OPEN",
            chart_path,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# H4 SL
# ============================================================

def pivot_confirmation_time(
    pivot,
    interval_minutes,
):
    """
    The pivot candle is only considered confirmed after the
    required right-side candles have completed.

    The scanner's existing pivot convention uses:
        pivot_time + PIVOT_RIGHT * interval
    """

    return (
        float(pivot["time"])
        + PIVOT_RIGHT
        * interval_minutes
        * 60
    )


def calculate_h4_sl(
    h4_df,
    m5_hook,
    h4_activation_hook=None,
):
    """
    Find the nearest CONFIRMED H4 Heikin Ashi protective pivot.

    SHORT:
        nearest confirmed H4 HA HIGH above Entry
        SL = High * (1 + buffer)

    LONG:
        nearest confirmed H4 HA LOW below Entry
        SL = Low * (1 - buffer)

    Important:
    - The pivot itself must be confirmed BEFORE the M5 entry.
    - The H4 activation H3/L3 is a valid fallback pivot because
      that H4 hook is already confirmed when the M5 search starts.
    """

    if (
        h4_df is None
        or h4_df.empty
        or m5_hook is None
    ):
        return None, None

    try:

        ha = heikin_ashi_ohlc(h4_df)

        highs, lows = find_pivots(ha)

        entry = float(
            m5_hook["entry"]
        )

        signal_time = float(
            m5_hook["confirmation_time"]
        )

        direction = m5_hook["direction"]

        # ----------------------------------------------------
        # Confirmed H4 pivots available before M5 entry
        # ----------------------------------------------------

        confirmed_highs = [
            p for p in highs
            if pivot_confirmation_time(
                p,
                H4_INTERVAL_MINUTES,
            ) <= signal_time
        ]

        confirmed_lows = [
            p for p in lows
            if pivot_confirmation_time(
                p,
                H4_INTERVAL_MINUTES,
            ) <= signal_time
        ]

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        if direction == "SHORT":

            candidates = [
                p for p in confirmed_highs
                if float(p["price"]) > entry
            ]

            # H4 activation H3 is explicitly valid.
            if (
                h4_activation_hook is not None
                and h4_activation_hook.get("direction") == "SHORT"
            ):

                activation_pivot = h4_activation_hook.get("h3")

                if activation_pivot:

                    activation_confirm_time = float(
                        h4_activation_hook["confirmation_time"]
                    )

                    if (
                        activation_confirm_time <= signal_time
                        and float(activation_pivot["price"]) > entry
                    ):
                        candidates.append(
                            {
                                **activation_pivot,
                                "_source": "H4_ACTIVATION_H3",
                            }
                        )

            if not candidates:
                return None, None

            # Deduplicate by time/price
            unique = {}

            for p in candidates:
                key = (
                    int(float(p["time"])),
                    round(float(p["price"]), 12),
                )
                unique[key] = p

            candidates = list(unique.values())

            # Nearest HIGH above entry
            selected = min(
                candidates,
                key=lambda p: (
                    float(p["price"]) - entry
                ),
            )

            pivot_price = float(
                selected["price"]
            )

            sl = (
                pivot_price
                * (1 + H4_SL_BUFFER_PCT / 100.0)
            )

            source = selected.get(
                "_source",
                "H4_HA_PIVOT_HIGH",
            )

            return float(sl), source

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            candidates = [
                p for p in confirmed_lows
                if float(p["price"]) < entry
            ]

            # H4 activation L3 is explicitly valid.
            if (
                h4_activation_hook is not None
                and h4_activation_hook.get("direction") == "LONG"
            ):

                activation_pivot = h4_activation_hook.get("l3")

                if activation_pivot:

                    activation_confirm_time = float(
                        h4_activation_hook["confirmation_time"]
                    )

                    if (
                        activation_confirm_time <= signal_time
                        and float(activation_pivot["price"]) < entry
                    ):
                        candidates.append(
                            {
                                **activation_pivot,
                                "_source": "H4_ACTIVATION_L3",
                            }
                        )

            if not candidates:
                return None, None

            # Deduplicate by time/price
            unique = {}

            for p in candidates:
                key = (
                    int(float(p["time"])),
                    round(float(p["price"]), 12),
                )
                unique[key] = p

            candidates = list(unique.values())

            # Nearest LOW below entry
            selected = min(
                candidates,
                key=lambda p: (
                    entry - float(p["price"])
                ),
            )

            pivot_price = float(
                selected["price"]
            )

            sl = (
                pivot_price
                * (1 - H4_SL_BUFFER_PCT / 100.0)
            )

            source = selected.get(
                "_source",
                "H4_HA_PIVOT_LOW",
            )

            return float(sl), source

        return None, None

    except Exception as e:
        DIAG["api_errors"].append(
            f"H4 SL: {str(e)[:180]}"
        )
        return None, None


# ============================================================
# TP CHECK
# ============================================================

def tp_already_touched(
    df,
    hook,
):
    """
    Only inspect candles AFTER the M5 final pivot.

    If TP was already reached before the entry confirmation,
    do not enter.
    """

    future = df[
        df.time > hook["final"]["time"]
    ]

    if future.empty:
        return False

    if hook["direction"] == "SHORT":
        return bool(
            (future.low <= hook["tp"]).any()
        )

    return bool(
        (future.high >= hook["tp"]).any()
    )


# ============================================================
# CHART
# ============================================================

def create_hook_chart(
    symbol,
    df,
    hook,
    sl,
    path_prefix="signal_m5",
    timeframe="M5",
    h4_activation=None,
):

    try:

        chart_df = (
            df.tail(CHART_CANDLES)
            .copy()
            .reset_index(drop=True)
        )

        if chart_df.empty:
            return None

        ha = calculate_heikin_ashi(
            chart_df
        )

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        x = mdates.date2num(
            pd.to_datetime(
                ha.time,
                unit="s",
                utc=True,
            ).dt.to_pydatetime()
        )

        minutes = (
            H4_INTERVAL_MINUTES
            if timeframe == "H4"
            else M5_INTERVAL_MINUTES
        )

        width = max(
            minutes / 1440 * 0.72,
            0.0008,
        )

        for i, row in ha.iterrows():

            xo = x[i]

            o = float(row.ha_open)
            c = float(row.ha_close)
            hi = float(row.ha_high)
            lo = float(row.ha_low)

            color = (
                "#26a69a"
                if c >= o
                else "#ef5350"
            )

            ax.vlines(
                xo,
                lo,
                hi,
                color="black",
                linewidth=0.8,
                zorder=2,
            )

            ax.add_patch(
                plt.Rectangle(
                    (
                        xo - width / 2,
                        min(o, c),
                    ),
                    width,
                    max(
                        abs(c - o),
                        max(abs(c), 1) * 1e-7,
                    ),
                    facecolor=color,
                    edgecolor="black",
                    linewidth=0.5,
                    zorder=3,
                )
            )

        if hook["direction"] == "SHORT":

            points = [
                hook[k]
                for k in (
                    "start",
                    "h1",
                    "l1",
                    "h2",
                    "l2",
                    "h3",
                )
            ]

            labels = [
                "START",
                "H1",
                "L1",
                "H2",
                "L2",
                "H3",
            ]

        else:

            points = [
                hook[k]
                for k in (
                    "start",
                    "l1",
                    "h1",
                    "l2",
                    "h2",
                    "l3",
                )
            ]

            labels = [
                "START",
                "L1",
                "H1",
                "L2",
                "H2",
                "L3",
            ]

        px = [
            datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc,
            )
            for p in points
        ]

        py = [
            p["price"]
            for p in points
        ]

        ax.plot(
            px,
            py,
            marker="o",
            linewidth=2,
            color="royalblue",
            label="Confirmed HA Hook",
            zorder=5,
        )

        if hook["direction"] == "SHORT":
            offsets = [
                (0, -28),
                (0, 18),
                (0, -30),
                (0, 18),
                (0, -30),
                (0, 22),
            ]
        else:
            offsets = [
                (0, 24),
                (0, -30),
                (0, 18),
                (0, -30),
                (0, 18),
                (0, -32),
            ]

        for p, label, off in zip(
            points,
            labels,
            offsets,
        ):

            dt = datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc,
            )

            emph = label in (
                "START",
                "H3",
                "L3",
            )

            ax.annotate(
                f"{label}\n{fmt_price(p['price'])}",
                (dt, p["price"]),
                xytext=off,
                textcoords="offset points",
                ha="center",
                fontsize=9 if emph else 8,
                fontweight=(
                    "bold" if emph else "normal"
                ),
                bbox=dict(
                    boxstyle="round,pad=.22",
                    fc="white",
                    ec=(
                        "black"
                        if emph
                        else "gray"
                    ),
                    alpha=0.9,
                ),
                arrowprops=dict(
                    arrowstyle="-",
                    color="gray",
                    linewidth=0.7,
                ),
                zorder=10,
            )

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        confirm = datetime.fromtimestamp(
            hook["confirmation_time"],
            tz=timezone.utc,
        )

        left = (
            pd.to_datetime(
                ha.time.iloc[0],
                unit="s",
                utc=True,
            )
            .to_pydatetime()
        )

        right = max(
            pd.to_datetime(
                ha.time.iloc[-1],
                unit="s",
                utc=True,
            )
            .to_pydatetime(),
            confirm,
        )

        ax.hlines(
            entry,
            left,
            right,
            linestyles="--",
            linewidth=1.4,
            label=f"ENTRY {fmt_price(entry)}",
            color="darkorange",
        )

        ax.hlines(
            tp,
            left,
            right,
            linestyles="--",
            linewidth=1.5,
            label=f"TP 86.4% {fmt_price(tp)}",
            color="seagreen",
        )

        if sl is not None:

            ax.hlines(
                float(sl),
                left,
                right,
                linestyles="--",
                linewidth=1.5,
                label=f"H4 SL {fmt_price(sl)}",
                color="crimson",
            )

        ax.axvline(
            confirm,
            linestyle=":",
            color="purple",
            label="M5 CONFIRMED",
        )

        if h4_activation:

            ax.axvline(
                datetime.fromtimestamp(
                    h4_activation,
                    tz=timezone.utc,
                ),
                linestyle="-.",
                color="black",
                alpha=0.8,
                label="H4 ACTIVATION",
            )

        ax.set_title(
            f"NDS {timeframe} | "
            f"{symbol} | "
            f"{hook['direction']} | "
            f"HEIKIN ASHI"
        )

        ax.set_xlabel("Time UTC")
        ax.set_ylabel("Price")

        ax.grid(alpha=0.22)

        ax.legend(
            loc="best",
            fontsize=8,
        )

        fig.autofmt_xdate()

        path = os.path.join(
            CHART_DIR,
            f"{path_prefix}_"
            f"{symbol.replace('/', '_').replace(':', '_')}_"
            f"{hook['direction']}_"
            f"{int(hook['final']['time'])}.png",
        )

        plt.tight_layout()

        plt.savefig(
            path,
            dpi=140,
        )

        plt.close(fig)

        return path

    except Exception as e:

        DIAG["hook_chart_errors"] += 1

        DIAG["api_errors"].append(
            f"Chart {symbol}: {str(e)[:180]}"
        )

        plt.close("all")

        return None


# ============================================================
# H4 DIRECTION
# ============================================================

def get_h4_direction(symbol):

    DIAG["h4_requests"] += 1

    df = get_candles(
        symbol,
        H4_INTERVAL,
        H4_CANDLES,
    )

    if df is None:
        DIAG["h4_data_error"] += 1
        return None

    if df.empty:
        DIAG["h4_empty"] += 1
        return None

    if len(df) < 50:
        DIAG["h4_short"] += 1
        return None

    DIAG["h4_data_ok"] += 1

    H4_DATA[symbol] = df.copy()

    ha = heikin_ashi_ohlc(df)

    hi, lo = find_pivots(ha)

    DIAG["h4_pivots"] += (
        len(hi) + len(lo)
    )

    hooks = detect_hooks(
        ha,
        H4_INTERVAL_MINUTES,
    )

    DIAG["h4_hooks"] += len(hooks)

    now = utc_now_ts()

    confirmed = [
        h for h in hooks
        if hook_is_confirmed(
            h,
            now,
        )
    ]

    recent = [
        h for h in confirmed
        if hook_is_recent(
            h,
            now,
            H4_MAX_HOOK_AGE_SECONDS,
        )
    ]

    DIAG["h4_confirmed"] += len(
        confirmed
    )

    DIAG["h4_recent"] += len(
        recent
    )

    DIAG["h4_valid_hooks"] += len(
        recent
    )

    if not recent:
        return None

    recent.sort(
        key=lambda h: (
            h["confirmation_time"],
            h["final"]["time"],
        ),
        reverse=True,
    )

    latest = recent[0]

    H4_DIRECTION[symbol] = {
        "direction": latest["direction"],
        "hook": latest,
        "activation_time": int(
            latest["confirmation_time"]
        ),
    }

    if latest["direction"] == "SHORT":
        DIAG["h4_short_direction"] += 1
    else:
        DIAG["h4_long_direction"] += 1

    return latest["direction"]


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook,
    sl,
    h4_hook,
    sl_source,
):

    d = hook["direction"]

    emoji = (
        "🔴"
        if d == "SHORT"
        else "🟢"
    )

    entry = float(
        hook["entry"]
    )

    tp = float(
        hook["tp"]
    )

    sl = float(sl)

    if d == "SHORT":
        risk = (
            (sl - entry)
            / entry
            * 100
        )

        reward = (
            (entry - tp)
            / entry
            * 100
        )

        h4_label = "H3"

    else:
        risk = (
            (entry - sl)
            / entry
            * 100
        )

        reward = (
            (tp - entry)
            / entry
            * 100
        )

        h4_label = "L3"

    return (
        f"{emoji} "
        f"<b>NDS {d} SIGNAL | H4 → M5</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"Entry M5 {h4_label}: "
        f"<b>{fmt_price(entry)}</b>\n"

        f"SL nearest confirmed H4 HA pivot: "
        f"<b>{fmt_price(sl)}</b> "
        f"({risk:+.2f}%)\n"

        f"TP 86.4% M5: "
        f"<b>{fmt_price(tp)}</b> "
        f"({reward:+.2f}%)\n\n"

        f"H4 SL source: "
        f"<b>{sl_source}</b>\n"

        f"H4 activation {h4_label}: "
        f"{fmt_ts(h4_hook['confirmation_time'])}\n"

        f"M5 hook confirmed: "
        f"{fmt_ts(hook['confirmation_time'])}\n"

        f"Hook range: "
        f"{hook['range_pct']:.2f}%\n"

        f"HA hook nodes; "
        f"trade monitoring uses real OHLC.\n"

        f"<b>PAPER TRADING ONLY</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    direction = get_h4_direction(
        symbol
    )

    h4info = H4_DIRECTION.get(
        symbol
    )

    if direction is None:
        DIAG["h4_filtered"] += 1
        return

    activation = int(
        h4info["activation_time"]
    )

    h4hook = h4info["hook"]

    DIAG["m5_requests"] += 1

    df = get_candles(
        symbol,
        M5_INTERVAL,
        M5_CANDLES,
    )

    if df is None:
        DIAG["m5_data_error"] += 1
        return

    if df.empty:
        DIAG["m5_empty"] += 1
        return

    if len(df) < 100:
        DIAG["m5_short"] += 1
        return

    DIAG["m5_data_ok"] += 1

    ha = heikin_ashi_ohlc(df)

    hi, lo = find_pivots(ha)

    DIAG["m5_pivots"] += (
        len(hi) + len(lo)
    )

    hooks = detect_hooks(
        ha,
        M5_INTERVAL_MINUTES,
    )

    DIAG["m5_hooks"] += len(hooks)

    now = utc_now_ts()

    for hook in hooks:

        # ----------------------------------------------------
        # CONFIRMED
        # ----------------------------------------------------

        if not hook_is_confirmed(
            hook,
            now,
        ):
            continue

        DIAG["m5_confirmed"] += 1

        # ----------------------------------------------------
        # RECENT
        # ----------------------------------------------------

        if not hook_is_recent(
            hook,
            now,
            M5_MAX_HOOK_AGE_SECONDS,
        ):
            continue

        DIAG["m5_recent"] += 1

        # ----------------------------------------------------
        # RANGE
        # ----------------------------------------------------

        if (
            hook["range_pct"]
            < M5_MIN_HOOK_RANGE_PCT
        ):
            continue

        DIAG["m5_range_valid"] += 1

        # ----------------------------------------------------
        # MUST BE AFTER H4 ACTIVATION
        # ----------------------------------------------------

        if (
            hook["confirmation_time"]
            <= activation
        ):
            continue

        DIAG["m5_after_h4_activation"] += 1

        # ----------------------------------------------------
        # SAME DIRECTION
        # ----------------------------------------------------

        if (
            hook["direction"]
            != direction
        ):
            continue

        # ----------------------------------------------------
        # TP MUST NOT HAVE ALREADY BEEN TOUCHED
        # ----------------------------------------------------

        if tp_already_touched(
            df,
            hook,
        ):

            DIAG["m5_tp_touched"] += 1
            continue

        # ----------------------------------------------------
        # H4 SL
        # ----------------------------------------------------

        sl, sl_source = calculate_h4_sl(
            H4_DATA.get(symbol),
            hook,
            h4hook,
        )

        if sl is None:

            # Diagnostic detail so the next run clearly tells
            # us which symbol/direction failed SL selection.
            DIAG["api_errors"].append(
                f"SL NOT FOUND | "
                f"{symbol} | "
                f"{hook['direction']} | "
                f"Entry={fmt_price(hook['entry'])}"
            )

            continue

        DIAG["m5_sl_found"] += 1

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        sl = float(sl)

        # ----------------------------------------------------
        # GEOMETRY
        # ----------------------------------------------------

        if direction == "SHORT":

            geometry_ok = (
                sl > entry > tp
            )

        else:

            geometry_ok = (
                sl < entry < tp
            )

        if not geometry_ok:

            DIAG["api_errors"].append(
                f"INVALID GEOMETRY | "
                f"{symbol} | "
                f"{direction} | "
                f"Entry={fmt_price(entry)} | "
                f"SL={fmt_price(sl)} | "
                f"TP={fmt_price(tp)}"
            )

            continue

        DIAG["m5_geometry_valid"] += 1

        # ----------------------------------------------------
        # DUPLICATE
        # ----------------------------------------------------

        key = hook_id(
            symbol,
            hook,
        )

        if (
            hook_exists(key)
            or trade_exists(key)
        ):

            DIAG["m5_duplicate"] += 1
            DIAG["duplicate_signals"] += 1
            continue

        # ----------------------------------------------------
        # MAX OPEN
        # ----------------------------------------------------

        if (
            open_trade_count()
            >= MAX_OPEN_TRADES
        ):

            DIAG["m5_max_open"] += 1
            DIAG["max_open"] += 1
            return

        # ----------------------------------------------------
        # SIGNAL READY
        # ----------------------------------------------------

        DIAG["signal_ready"] += 1

        # ----------------------------------------------------
        # M5 CHART
        # ----------------------------------------------------

        m5_chart = create_hook_chart(
            symbol,
            df,
            hook,
            sl,
            "signal_m5",
            "M5",
            activation,
        )

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        save_hook(
            symbol,
            hook,
            sl,
            m5_chart,
        )

        save_trade(
            key,
            symbol,
            hook,
            sl,
            m5_chart,
        )

        DIAG["signals"] += 1

        # ----------------------------------------------------
        # TELEGRAM TEXT
        # ----------------------------------------------------

        telegram_send(
            build_signal_message(
                symbol,
                hook,
                sl,
                h4hook,
                sl_source,
            )
        )

        # ----------------------------------------------------
        # TELEGRAM M5 CHART
        # ----------------------------------------------------

        if m5_chart:

            sent = telegram_send_photo(
                m5_chart,
                (
                    f"📍 "
                    f"<b>NDS {direction} SIGNAL | M5</b>\n"
                    f"<b>{symbol}</b>\n"
                    f"Entry: {fmt_price(entry)}\n"
                    f"TP 86.4%: {fmt_price(tp)}\n"
                    f"H4 SL: {fmt_price(sl)}\n"
                    f"SL Source: {sl_source}\n"
                    f"H4 activation: {fmt_ts(activation)}\n"
                    f"<b>PAPER TRADING ONLY</b>"
                ),
            )

            if sent:
                DIAG["hook_charts_sent"] += 1

        # ----------------------------------------------------
        # H4 CHART
        # ----------------------------------------------------

        h4chart = None

        if H4_DATA.get(symbol) is not None:

            h4chart = create_hook_chart(
                symbol,
                H4_DATA.get(symbol),
                h4hook,
                sl,
                "signal_h4",
                "H4",
            )

        if h4chart:

            sent = telegram_send_photo(
                h4chart,
                (
                    f"🧭 "
                    f"<b>H4 ACTIVATION HOOK</b>\n"
                    f"<b>{symbol}</b>\n"
                    f"Direction: {direction}\n"
                    f"Activation: {fmt_ts(activation)}\n"
                    f"SL Source: {sl_source}\n"
                    f"This H4 hook activated the later "
                    f"M5 hook search.\n"
                    f"<b>PAPER TRADING ONLY</b>"
                ),
            )

            if sent:
                DIAG["hook_charts_sent"] += 1

        return


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY opened_at ASC
        """
    ).fetchall()

    conn.close()

    return rows


def close_trade(
    trade,
    price,
    pnl_pct,
    pnl_price,
    reason,
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE trades
        SET
            closed_at=?,
            close_price=?,
            status=?,
            pnl_pct=?,
            pnl_price=?
        WHERE id=?
        """,
        (
            utc_now_ts(),
            price,
            reason,
            pnl_pct,
            pnl_price,
            trade["id"],
        ),
    )

    conn.commit()
    conn.close()


def monitor_open_trades():

    for trade in get_open_trades():

        try:

            symbol = trade["symbol"]
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

            df = get_candles(
                symbol,
                M5_INTERVAL,
                30,
            )

            if (
                df is None
                or df.empty
            ):
                continue

            future = df[
                df.time >= trade["opened_at"]
            ]

            if future.empty:
                continue

            reason = None
            price = None

            for _, candle in future.iterrows():

                hi = float(candle.high)
                lo = float(candle.low)

                hit_sl = (
                    hi >= sl
                    if direction == "SHORT"
                    else lo <= sl
                )

                hit_tp = (
                    lo <= tp
                    if direction == "SHORT"
                    else hi >= tp
                )

                # Conservative assumption if both are touched
                if hit_sl:
                    price = sl
                    reason = "SL"
                    break

                if hit_tp:
                    price = tp
                    reason = "TP"
                    break

            if reason is None:
                continue

            pnl_price = (
                entry - price
                if direction == "SHORT"
                else price - entry
            )

            pnl_pct = (
                pnl_price
                / entry
                * 100
                if entry
                else 0
            )

            close_trade(
                trade,
                price,
                pnl_pct,
                pnl_price,
                reason,
            )

            emoji = (
                "✅"
                if pnl_pct >= 0
                else "❌"
            )

            telegram_send(
                f"{emoji} "
                f"<b>NDS {direction} CLOSED</b>\n"
                f"<b>{symbol}</b>\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Close: {fmt_price(price)}\n"
                f"Result: <b>{reason}</b>\n"
                f"PnL: <b>{pnl_pct:+.2f}%</b>"
            )

        except Exception as e:

            DIAG["api_errors"].append(
                f"Monitor {trade['symbol']}: "
                f"{str(e)[:180]}"
            )


# ============================================================
# LIVE PRICE / TRADE REPORT
# ============================================================

def trade_metrics(
    entry,
    sl,
    tp,
    direction,
    current,
):

    if direction == "LONG":

        return (
            (current - entry)
            / entry
            * 100,

            (tp - entry)
            / entry
            * 100,

            (sl - entry)
            / entry
            * 100,
        )

    return (
        (entry - current)
        / entry
        * 100,

        (entry - tp)
        / entry
        * 100,

        (entry - sl)
        / entry
        * 100,
    )


def get_current_price(symbol):

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

                if (
                    isinstance(t, dict)
                    and str(
                        t.get("symbol")
                        or t.get("pair")
                        or t.get("instrument")
                        or ""
                    ) == symbol
                ):

                    for k in (
                        "last",
                        "lastPrice",
                        "markPrice",
                        "price",
                    ):

                        if t.get(k) is not None:
                            return float(
                                t[k]
                            )

    except Exception as e:

        DIAG["api_errors"].append(
            f"Ticker {symbol}: "
            f"{str(e)[:160]}"
        )

    df = get_candles(
        symbol,
        M5_INTERVAL,
        2,
    )

    if (
        df is None
        or df.empty
    ):
        return None

    return float(
        df.iloc[-1].close
    )


def open_trades_report_lines():

    rows = get_open_trades()

    lines = [
        "━━━ <b>OPEN TRADES</b> ━━━"
    ]

    if not rows:
        return lines + ["None"]

    for trade in rows:

        direction = trade["direction"]
        symbol = trade["symbol"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        current = get_current_price(
            symbol
        )

        if current is None:

            lines.extend(
                [
                    (
                        "🟢"
                        if direction == "LONG"
                        else "🔴"
                    )
                    + f" <b>{symbol} {direction}</b>",

                    f"Entry: {fmt_price(entry)}",

                    "Current: -",

                    f"TP: {fmt_price(tp)}",

                    f"SL: {fmt_price(sl)}",

                    "",
                ]
            )

            continue

        pnl_pct, tp_pct, sl_pct = trade_metrics(
            entry,
            sl,
            tp,
            direction,
            current,
        )

        lines.extend(
            [
                (
                    "🟢"
                    if direction == "LONG"
                    else "🔴"
                )
                + f" <b>{symbol} {direction}</b>",

                f"Entry: {fmt_price(entry)}",

                f"Current: "
                f"<b>{fmt_price(current)} "
                f"({pnl_pct:+.2f}%)</b>",

                f"TP: "
                f"<b>{fmt_price(tp)} "
                f"({tp_pct:+.2f}%)</b>",

                f"SL: "
                f"<b>{fmt_price(sl)} "
                f"({sl_pct:+.2f}%)</b>",

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

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*) c
        FROM trades
        WHERE status!='OPEN'
        """
    ).fetchone()["c"]

    wins = conn.execute(
        """
        SELECT COUNT(*) c
        FROM trades
        WHERE status='TP'
        """
    ).fetchone()["c"]

    losses = conn.execute(
        """
        SELECT COUNT(*) c
        FROM trades
        WHERE status='SL'
        """
    ).fetchone()["c"]

    pnl = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        ) p
        FROM trades
        WHERE status!='OPEN'
        """
    ).fetchone()["p"]

    conn.close()

    return {
        "total": int(total or 0),
        "wins": int(wins or 0),
        "losses": int(losses or 0),
        "pnl": float(pnl or 0),
    }


# ============================================================
# DIAGNOSTIC
# ============================================================

def diagnostic_text():

    p = performance_summary()

    lines = [
        "🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>",
        f"Version: <b>{VERSION}</b>",
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
        f"Scanned: <b>{DIAG['assets_scanned']}</b>",
        "",

        "━━━ <b>H4</b> ━━━",
    ]

    for label, key in [
        ("Requests", "h4_requests"),
        ("Data OK", "h4_data_ok"),
        ("Data Error", "h4_data_error"),
        ("Empty", "h4_empty"),
        ("Short", "h4_short"),
        ("Pivot Points", "h4_pivots"),
        ("Hooks", "h4_hooks"),
        ("Confirmed", "h4_confirmed"),
        ("Recent ≤24h", "h4_recent"),
        (
            "Valid Activation Hooks",
            "h4_valid_hooks",
        ),
        (
            "SHORT H3 Activations",
            "h4_short_direction",
        ),
        (
            "LONG L3 Activations",
            "h4_long_direction",
        ),
        ("Filtered", "h4_filtered"),
    ]:

        lines.append(
            f"{label}: {DIAG[key]}"
        )

    lines += [
        "",
        "━━━ <b>M5</b> ━━━",
    ]

    for label, key in [
        ("Requests", "m5_requests"),
        ("Data OK", "m5_data_ok"),
        ("Data Error", "m5_data_error"),
        ("Empty", "m5_empty"),
        ("Short", "m5_short"),
        ("Pivot Points", "m5_pivots"),
        ("Hooks", "m5_hooks"),
        ("Confirmed", "m5_confirmed"),
        ("Recent ≤6h", "m5_recent"),
        (
            f"Range Valid ≥{M5_MIN_HOOK_RANGE_PCT:.2f}%",
            "m5_range_valid",
        ),
        (
            "Confirmed After H4 Activation",
            "m5_after_h4_activation",
        ),
        (
            "TP Already Touched",
            "m5_tp_touched",
        ),
        (
            "SL Found",
            "m5_sl_found",
        ),
        (
            "Geometry Valid",
            "m5_geometry_valid",
        ),
        (
            "Duplicate",
            "m5_duplicate",
        ),
        (
            "Max Open",
            "m5_max_open",
        ),
        (
            "Signal Ready",
            "signal_ready",
        ),
    ]:

        lines.append(
            f"{label}: {DIAG[key]}"
        )

    lines += [
        "",
        "━━━ <b>SIGNALS</b> ━━━",
        f"Signals: {DIAG['signals']}",
        (
            f"Duplicates: "
            f"{DIAG['duplicate_signals']}"
        ),
        (
            f"Max Open Limit: "
            f"{DIAG['max_open']}"
        ),
        (
            f"Open Trades: "
            f"<b>{open_trade_count()}</b>"
        ),
        "",
    ]

    lines.extend(
        open_trades_report_lines()
    )

    lines += [
        "",
        "━━━ <b>PAPER PERFORMANCE</b> ━━━",
        f"Closed Trades: {p['total']}",
        f"TP: {p['wins']}",
        f"SL: {p['losses']}",
        f"PnL: <b>{p['pnl']:+.2f}%</b>",
    ]

    if DIAG["api_errors"]:

        lines += [
            "",
            "━━━ <b>DIAGNOSTIC DETAILS</b> ━━━",
        ]

        lines += [
            "• " + e
            for e in DIAG["api_errors"][-8:]
        ]

    lines += [
        "",
        "<b>PAPER TRADING ONLY - NO REAL ORDERS</b>",
    ]

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        init_db()

        print(
            f"NDS H4 -> M5 Scanner {VERSION}"
        )

        print(
            "H4 confirmed H3/L3 activates "
            "same-direction M5 hook search"
        )

        print(
            "Entry M5 H3/L3 | "
            "TP 86.4% M5 | "
            "SL nearest confirmed H4 HA pivot"
        )

        print(
            "PAPER TRADING ONLY - NO REAL ORDERS"
        )

        symbols = get_futures_instruments()

        if not symbols:

            msg = (
                "❌ <b>NDS Scanner</b>\n\n"
                "No futures instruments found."
            )

            print(msg)
            telegram_send(msg)

            return

        DIAG["assets_scanned"] = len(
            symbols
        )

        # Check existing open trades
        monitor_open_trades()

        # Scan all assets
        for symbol in symbols:

            try:

                process_symbol(
                    symbol
                )

            except Exception as e:

                DIAG["api_errors"].append(
                    f"Process {symbol}: "
                    f"{str(e)[:180]}"
                )

                traceback.print_exc()

            time.sleep(
                SCAN_SLEEP_SECONDS
            )

        # Check open trades again after new scan
        monitor_open_trades()

        report = diagnostic_text()

        print(
            "\n"
            + report
            + "\n"
        )

        telegram_send(
            report
        )

    except Exception as e:

        traceback.print_exc()

        telegram_send(
            "❌ <b>NDS Scanner Error</b>\n\n"
            f"{type(e).__name__}: "
            f"{str(e)[:500]}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
