# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.4.8
# PAPER TRADING ONLY - NO REAL ORDERS
#
# H4:
#   - Detect confirmed NDS Hook
#   - Determine SHORT / LONG direction
#   - H4 direction authorizes M5 trade signals
#
# M5:
#   - Detect NDS Hooks independently
#   - Confirm Hook after PIVOT_RIGHT candles
#   - Send chart for confirmed/recent valid M5 Hook
#   - Trade signal only if M5 direction == H4 direction
#
# IMPORTANT NDS LOGIC
# -------------------
# POSITIVE HOOK / SHORT:
#   START(L) -> H1 -> L1 -> H2 -> L2 -> H3
#   H2 > H1
#   L2 < L1
#   H3 > H2
#   START is the LOWEST point of the whole hook
#
# NEGATIVE HOOK / LONG:
#   START(H) -> L1 -> H1 -> L2 -> H2 -> L3
#   L2 < L1
#   H2 > H1
#   L3 < L2
#   START is the HIGHEST point of the whole hook
#
# TP:
#   86.4% retracement from FINAL back toward START
#
# SHORT:
#   TP = FINAL - 86.4% * (FINAL - START)
#
# LONG:
#   TP = FINAL + 86.4% * (START - FINAL)
#
# IMPORTANT:
#   NDS detection uses ORIGINAL OHLC.
#   Chart display uses HEIKIN ASHI.
#
# CHART:
#   - Heikin Ashi candles
#   - NDS calculations remain based on real OHLC data
#   - Hook points/labels displayed separately
#
# PAPER TRADING ONLY
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
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.4.8"

REAL_TRADING = False

KRAKEN_FUTURES_URL = (
    "https://futures.kraken.com/derivatives/api/v3"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1"
)

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

M5_MAX_HOOK_AGE_SECONDS = (
    6 * 60 * 60
)

H4_MAX_HOOK_AGE_SECONDS = (
    24 * 60 * 60
)

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25

CHART_CANDLES = 240

DB_FILE = "nds_h4_m5_v546.db"

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
# DIAGNOSTIC
# ============================================================

DIAG = {
    "assets_scanned": 0,

    "h4_requests": 0,
    "h4_data_ok": 0,
    "h4_data_error": 0,
    "h4_empty": 0,
    "h4_short": 0,
    "h4_pivots": 0,
    "h4_hooks": 0,
    "h4_confirmed_hooks": 0,
    "h4_recent_hooks": 0,
    "h4_valid_hooks": 0,
    "h4_short_direction": 0,
    "h4_long_direction": 0,
    "h4_filtered": 0,

    "m5_requests": 0,
    "m5_data_ok": 0,
    "m5_data_error": 0,
    "m5_empty": 0,
    "m5_short": 0,
    "m5_pivots": 0,
    "m5_hooks": 0,
    "m5_confirmed_hooks": 0,
    "m5_recent_hooks": 0,
    "m5_range_valid_hooks": 0,
    "m5_valid_hooks": 0,

    "hook_charts_sent": 0,
    "hook_chart_errors": 0,

    "signals": 0,
    "duplicate_signals": 0,
    "max_open": 0,

    "api_errors": [],
}


H4_DIRECTION = {}
H4_DATA = {}

START_TIME = time.time()


# ============================================================
# TIME / FORMAT
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_ts():
    return int(
        utc_now().timestamp()
    )


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
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

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        r = requests.post(
            url,
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
                f"Telegram text HTTP "
                f"{r.status_code}"
            )

        return r.ok

    except Exception as e:

        DIAG["api_errors"].append(
            f"Telegram text: "
            f"{str(e)[:160]}"
        )

        return False


def telegram_send_photo(
    photo_path,
    caption
):

    if (
        not telegram_enabled()
        or not photo_path
        or not os.path.exists(photo_path)
    ):
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            photo_path,
            "rb"
        ) as photo:

            r = requests.post(
                url,
                files={
                    "photo": photo
                },
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "caption":
                        caption[:1024],
                    "parse_mode":
                        "HTML",
                },
                timeout=REQUEST_TIMEOUT,
            )

        if not r.ok:

            DIAG["api_errors"].append(
                f"Telegram photo HTTP "
                f"{r.status_code}"
            )

        return r.ok

    except Exception as e:

        DIAG["api_errors"].append(
            f"Telegram photo: "
            f"{str(e)[:160]}"
        )

        return False


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_futures_instruments():

    try:

        r = requests.get(
            f"{KRAKEN_FUTURES_URL}/instruments",
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        instruments = r.json().get(
            "instruments",
            []
        )

        symbols = []

        for item in instruments:

            symbol = str(
                item.get("symbol")
                or item.get("instrument")
                or ""
            )

            if not symbol.startswith("PF_"):
                continue

            if "USD" not in symbol:
                continue

            if item.get(
                "tradeable",
                True
            ) is False:
                continue

            symbols.append(symbol)

        return list(
            dict.fromkeys(symbols)
        )[:TARGET_ASSETS]

    except Exception as e:

        DIAG["api_errors"].append(
            f"Instruments: "
            f"{str(e)[:180]}"
        )

        return []


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(
    symbol,
    interval,
    count
):

    url = (
        f"{KRAKEN_CHART_URL}/trade/"
        f"{symbol}/{interval}"
    )

    try:

        r = requests.get(
            url,
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

        candles = r.json().get(
            "candles",
            []
        )

        rows = []

        for c in candles:

            if isinstance(c, dict):

                ts = c.get(
                    "time",
                    c.get(
                        "timestamp",
                        c.get("t")
                    )
                )

                op = c.get(
                    "open",
                    c.get("o")
                )

                hi = c.get(
                    "high",
                    c.get("h")
                )

                lo = c.get(
                    "low",
                    c.get("l")
                )

                cl = c.get(
                    "close",
                    c.get("c")
                )

                vol = c.get(
                    "volume",
                    c.get("v", 0)
                )

            elif (
                isinstance(
                    c,
                    (list, tuple)
                )
                and len(c) >= 5
            ):

                ts, op, hi, lo, cl = c[:5]

                vol = (
                    c[5]
                    if len(c) > 5
                    else 0
                )

            else:
                continue

            rows.append([
                ts,
                op,
                hi,
                lo,
                cl,
                vol
            ])

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

        for col in [
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]:

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

        if (
            df["time"].max()
            > 10_000_000_000
        ):

            df["time"] = (
                df["time"] / 1000.0
            )

        df["time"] = (
            df["time"]
            .astype(float)
        )

        df = (
            df
            .sort_values("time")
            .drop_duplicates(
                "time"
            )
            .reset_index(drop=True)
        )

        return df

    except Exception as e:

        DIAG["api_errors"].append(
            f"{symbol} {interval}: "
            f"{str(e)[:180]}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    if (
        df is None
        or df.empty
        or len(df)
        < (
            PIVOT_LEFT
            + PIVOT_RIGHT
            + 1
        )
    ):
        return highs, lows

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        hi = float(
            df.iloc[i]["high"]
        )

        lo = float(
            df.iloc[i]["low"]
        )

        left_hi = [
            float(
                df.iloc[j]["high"]
            )
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_hi = [
            float(
                df.iloc[j]["high"]
            )
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        left_lo = [
            float(
                df.iloc[j]["low"]
            )
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_lo = [
            float(
                df.iloc[j]["low"]
            )
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        if (
            all(
                hi > x
                for x in left_hi
            )
            and
            all(
                hi >= x
                for x in right_hi
            )
        ):

            highs.append({

                "type": "H",

                "index": i,

                "time": float(
                    df.iloc[i]["time"]
                ),

                "price": hi,
            })

        if (
            all(
                lo < x
                for x in left_lo
            )
            and
            all(
                lo <= x
                for x in right_lo
            )
        ):

            lows.append({

                "type": "L",

                "index": i,

                "time": float(
                    df.iloc[i]["time"]
                ),

                "price": lo,
            })

    return highs, lows


def build_ordered_pivots(
    highs,
    lows
):

    pivots = sorted(
        highs + lows,
        key=lambda p: (
            p["time"],
            p["index"]
        )
    )

    result = []

    for p in pivots:

        if not result:

            result.append(p)
            continue

        last = result[-1]

        if p["type"] != last["type"]:

            result.append(p)
            continue

        if p["type"] == "H":

            if (
                p["price"]
                >= last["price"]
            ):

                result[-1] = p

        else:

            if (
                p["price"]
                <= last["price"]
            ):

                result[-1] = p

    return result


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks_from_pivots(
    highs,
    lows,
    interval_minutes
):

    ordered = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    confirmation_seconds = (
        interval_minutes
        * 60
        * PIVOT_RIGHT
    )

    if len(ordered) < 6:
        return hooks, ordered

    for i in range(
        len(ordered) - 5
    ):

        p = ordered[
            i:i + 6
        ]

        types = [
            x["type"]
            for x in p
        ]

        # ====================================================
        # POSITIVE HOOK / SHORT
        # ====================================================

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
                h3
            ) = p

            if not (
                h2["price"]
                > h1["price"]
                and
                l2["price"]
                < l1["price"]
                and
                h3["price"]
                > h2["price"]
            ):
                continue

            if not (
                start["price"]
                < l1["price"]
                and
                start["price"]
                < l2["price"]
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

            # ------------------------------------------------
            # 86.4% retracement from FINAL back toward START
            # ------------------------------------------------

            tp = (
                h3["price"]
                - NDS_RETRACE * rng
            )

            hooks.append({

                "direction": "SHORT",

                "start": start,

                "h1": h1,
                "l1": l1,
                "h2": h2,
                "l2": l2,

                "h3": h3,

                "final": h3,

                "entry":
                    h3["price"],

                "tp": tp,

                "range_pct":
                    rng
                    / start["price"]
                    * 100.0,

                "confirmation_time":
                    h3["time"]
                    + confirmation_seconds,

                "created_time":
                    h3["time"],
            })

        # ====================================================
        # NEGATIVE HOOK / LONG
        # ====================================================

        if types == [
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
                l3
            ) = p

            if not (
                l2["price"]
                < l1["price"]
                and
                h2["price"]
                > h1["price"]
                and
                l3["price"]
                < l2["price"]
            ):
                continue

            if not (
                start["price"]
                > h1["price"]
                and
                start["price"]
                > h2["price"]
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

            # ------------------------------------------------
            # 86.4% retracement from FINAL back toward START
            # ------------------------------------------------

            tp = (
                l3["price"]
                + NDS_RETRACE * rng
            )

            hooks.append({

                "direction": "LONG",

                "start": start,

                "l1": l1,
                "h1": h1,
                "l2": l2,
                "h2": h2,

                "l3": l3,

                "final": l3,

                "entry":
                    l3["price"],

                "tp": tp,

                "range_pct":
                    rng
                    / start["price"]
                    * 100.0,

                "confirmation_time":
                    l3["time"]
                    + confirmation_seconds,

                "created_time":
                    l3["time"],
            })

    return hooks, ordered


def detect_hooks(
    df,
    interval_minutes
):

    if df is None or df.empty:
        return [], [], []

    highs, lows = find_pivots(df)

    hooks, ordered = (
        detect_hooks_from_pivots(
            highs,
            lows,
            interval_minutes
        )
    )

    return hooks, highs, lows


# ============================================================
# HOOK STATUS
# ============================================================

def hook_id(
    symbol,
    hook
):

    final = hook["final"]

    return (
        f"{symbol}|"
        f"{hook['direction']}|"
        f"{int(final['time'])}|"
        f"{final['price']:.12f}"
    )


def hook_is_confirmed(
    hook,
    now_ts=None
):

    if now_ts is None:
        now_ts = utc_now_ts()

    return (
        hook["confirmation_time"]
        <= now_ts
    )


def hook_is_recent(
    hook,
    max_age_seconds,
    now_ts=None
):

    if now_ts is None:
        now_ts = utc_now_ts()

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

    conn.executescript("""

    CREATE TABLE IF NOT EXISTS hooks (
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

    CREATE TABLE IF NOT EXISTS trades (
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

    CREATE TABLE IF NOT EXISTS
    hook_chart_notifications (
        hook_key TEXT PRIMARY KEY,
        sent_at INTEGER,
        chart_path TEXT
    );

    """)

    conn.commit()
    conn.close()


def notification_exists(key):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM hook_chart_notifications
        WHERE hook_key=?
        """,
        (key,)
    ).fetchone()

    conn.close()

    return row is not None


def mark_notification_sent(
    key,
    path
):

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO
        hook_chart_notifications
        (
            hook_key,
            sent_at,
            chart_path
        )
        VALUES (?,?,?)
        """,
        (
            key,
            utc_now_ts(),
            path,
        )
    )

    conn.commit()
    conn.close()


def hook_exists(key):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM hooks
        WHERE hook_key=?
        LIMIT 1
        """,
        (key,)
    ).fetchone()

    conn.close()

    return row is not None


def trade_exists(key):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM trades
        WHERE hook_key=?
        LIMIT 1
        """,
        (key,)
    ).fetchone()

    conn.close()

    return row is not None


def open_trade_count():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()

    conn.close()

    return int(
        row["c"]
    )


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

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO hooks (
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
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?
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
                hook["created_time"]
            ),
            utc_now_ts(),
            chart_path,
        )
    )

    conn.commit()
    conn.close()

    return key


def save_trade(
    key,
    symbol,
    hook,
    sl,
    chart_path=None
):

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO trades (
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
        VALUES (?,?,?,?,?,?,?,?,?)
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
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# STOP LOSS
# ============================================================

def calculate_sl(
    df,
    hook
):

    highs, lows = find_pivots(df)

    final_time = (
        hook["final"]["time"]
    )

    if hook["direction"] == "SHORT":

        candidates = [
            x
            for x in highs
            if x["time"] < final_time
        ]

    else:

        candidates = [
            x
            for x in lows
            if x["time"] < final_time
        ]

    candidates.sort(
        key=lambda x: x["time"],
        reverse=True
    )

    if not candidates:
        return None

    return candidates[0]["price"]


# ============================================================
# TP TEST
# ============================================================

def tp_already_touched(
    df,
    hook
):

    future = df[
        df["time"]
        > hook["final"]["time"]
    ]

    if future.empty:
        return False

    if hook["direction"] == "SHORT":

        return bool(
            (
                future["low"]
                <= hook["tp"]
            ).any()
        )

    return bool(
        (
            future["high"]
            >= hook["tp"]
        ).any()
    )


# ============================================================
# HEIKIN ASHI
# ============================================================

def calculate_heikin_ashi(df):

    """
    ONLY for chart display.

    NDS Hook detection, Entry, SL and TP use
    original Kraken OHLC values.

    HA Close =
        (O + H + L + C) / 4

    HA Open =
        (previous HA Open + previous HA Close) / 2

    HA High =
        max(H, HA Open, HA Close)

    HA Low =
        min(L, HA Open, HA Close)
    """

    ha = df.copy()

    if ha.empty:
        return ha

    ha_open = []
    ha_close = []
    ha_high = []
    ha_low = []

    for i in range(
        len(ha)
    ):

        op = float(
            ha.iloc[i]["open"]
        )

        hi = float(
            ha.iloc[i]["high"]
        )

        lo = float(
            ha.iloc[i]["low"]
        )

        cl = float(
            ha.iloc[i]["close"]
        )

        current_ha_close = (
            op + hi + lo + cl
        ) / 4.0

        if i == 0:

            current_ha_open = (
                op + cl
            ) / 2.0

        else:

            current_ha_open = (
                ha_open[i - 1]
                + ha_close[i - 1]
            ) / 2.0

        current_ha_high = max(
            hi,
            current_ha_open,
            current_ha_close
        )

        current_ha_low = min(
            lo,
            current_ha_open,
            current_ha_close
        )

        ha_open.append(
            current_ha_open
        )

        ha_close.append(
            current_ha_close
        )

        ha_high.append(
            current_ha_high
        )

        ha_low.append(
            current_ha_low
        )

    ha["ha_open"] = ha_open
    ha["ha_high"] = ha_high
    ha["ha_low"] = ha_low
    ha["ha_close"] = ha_close

    return ha


# ============================================================
# CHART HELPERS
# ============================================================

def get_hook_points(hook):

    if hook["direction"] == "SHORT":

        points = [
            hook["start"],
            hook["h1"],
            hook["l1"],
            hook["h2"],
            hook["l2"],
            hook["h3"],
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
            hook["start"],
            hook["l1"],
            hook["h1"],
            hook["l2"],
            hook["h2"],
            hook["l3"],
        ]

        labels = [
            "START",
            "L1",
            "H1",
            "L2",
            "H2",
            "L3",
        ]

    return points, labels


# ============================================================
# CHART
# ============================================================

def create_hook_chart(
    symbol,
    df,
    hook,
    sl,
    path_prefix="hook",
    timeframe="M5"
):

    try:

        # ----------------------------------------------------
        # IMPORTANT:
        # Calculate HA from the FULL dataframe first.
        # ----------------------------------------------------

        ha_df = calculate_heikin_ashi(
            df
        )

        chart_df = (
            ha_df
            .tail(CHART_CANDLES)
            .copy()
        )

        if chart_df.empty:
            return None

        # ----------------------------------------------------
        # Convert candle times to timezone-aware datetime.
        # ----------------------------------------------------

        chart_df["dt"] = pd.to_datetime(
            chart_df["time"],
            unit="s",
            utc=True
        )

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        # ----------------------------------------------------
        # Heikin Ashi candle width
        #
        # Matplotlib datetime axes use DAYS.
        # ----------------------------------------------------

        if len(chart_df) > 1:

            time_diffs_days = (
                chart_df["dt"]
                .diff()
                .dropna()
                .dt.total_seconds()
                / 86400.0
            )

            median_diff = (
                float(
                    time_diffs_days.median()
                )
                if not time_diffs_days.empty
                else (
                    M5_INTERVAL_MINUTES
                    / 1440.0
                )
            )

            candle_width = (
                median_diff * 0.72
            )

        else:

            if timeframe == "H4":

                candle_width = (
                    H4_INTERVAL_MINUTES
                    / 1440.0
                    * 0.72
                )

            else:

                candle_width = (
                    M5_INTERVAL_MINUTES
                    / 1440.0
                    * 0.72
                )

        # ----------------------------------------------------
        # Heikin Ashi candles
        #
        # IMPORTANT:
        # x is datetime, NOT Unix seconds.
        # This fixes the original chart-axis bug.
        # ----------------------------------------------------

        for _, candle in chart_df.iterrows():

            x = candle["dt"]

            op = float(
                candle["ha_open"]
            )

            hi = float(
                candle["ha_high"]
            )

            lo = float(
                candle["ha_low"]
            )

            cl = float(
                candle["ha_close"]
            )

            if cl >= op:

                body_color = "green"

            else:

                body_color = "red"

            # Wick
            ax.vlines(
                x,
                lo,
                hi,
                linewidth=0.8,
                color=body_color,
                alpha=0.9,
                zorder=2
            )

            # Body
            body_bottom = min(
                op,
                cl
            )

            body_height = abs(
                cl - op
            )

            min_body = (
                (hi - lo) * 0.002
                if hi != lo
                else 0.00000001
            )

            body_height = max(
                body_height,
                min_body
            )

            rect = Rectangle(
                (
                    mdates.date2num(x)
                    - candle_width / 2,
                    body_bottom
                ),
                candle_width,
                body_height,
                facecolor=body_color,
                edgecolor=body_color,
                linewidth=0.7,
                alpha=0.85,
                zorder=3
            )

            ax.add_patch(rect)

        # ----------------------------------------------------
        # Hook points
        # ----------------------------------------------------

        points, labels = get_hook_points(
            hook
        )

        px = [
            datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc
            )
            for p in points
        ]

        py = [
            p["price"]
            for p in points
        ]

        # ----------------------------------------------------
        # Hook connecting line
        # ----------------------------------------------------

        ax.plot(
            px,
            py,
            marker="o",
            linewidth=2.0,
            label="Confirmed NDS Hook",
            zorder=5
        )

        # ----------------------------------------------------
        # Improved labels
        #
        # Alternate/staggered offsets so labels do not
        # pile onto each other.
        # ----------------------------------------------------

        if hook["direction"] == "SHORT":

            offsets = [
                (0, -34),   # START
                (0, 30),    # H1
                (0, -32),   # L1
                (0, 32),    # H2
                (0, -32),   # L2
                (0, 34),    # H3
            ]

        else:

            offsets = [
                (0, 36),    # START
                (0, -32),   # L1
                (0, 30),    # H1
                (0, -32),   # L2
                (0, 30),    # H2
                (0, -36),   # L3
            ]

        for (
            p,
            label,
            offset
        ) in zip(
            points,
            labels,
            offsets
        ):

            dt = datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc
            )

            ax.annotate(
                (
                    f"{label}\n"
                    f"{fmt_price(p['price'])}"
                ),
                (dt, p["price"]),
                xytext=offset,
                textcoords="offset points",
                ha="center",
                va="center",
                fontsize=8,
                bbox=dict(
                    boxstyle="round,pad=0.25",
                    fc="white",
                    alpha=0.86
                ),
                zorder=10
            )

        # ----------------------------------------------------
        # Entry / TP / SL
        # ----------------------------------------------------

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        sl_value = (
            float(sl)
            if sl is not None
            else None
        )

        chart_start_dt = (
            chart_df["dt"].iloc[0]
        )

        chart_end_dt = (
            chart_df["dt"].iloc[-1]
        )

        hook_min_dt = min(
            px
        )

        hook_max_dt = max(
            px
        )

        start_x = min(
            chart_start_dt,
            hook_min_dt
        )

        confirmation_dt = datetime.fromtimestamp(
            hook["confirmation_time"],
            tz=timezone.utc
        )

        end_x = max(
            chart_end_dt,
            hook_max_dt,
            confirmation_dt
        )

        # Add a small horizontal margin.
        total_days = (
            end_x - start_x
        ).total_seconds() / 86400.0

        if total_days <= 0:
            total_days = (
                M5_INTERVAL_MINUTES
                / 1440.0
            )

        x_margin = (
            total_days * 0.03
        )

        start_x_plot = (
            start_x
            - pd.Timedelta(
                days=x_margin
            )
        )

        end_x_plot = (
            end_x
            + pd.Timedelta(
                days=x_margin
            )
        )

        ax.hlines(
            entry,
            start_x_plot,
            end_x_plot,
            linestyles="--",
            linewidth=1.4,
            label=(
                f"ENTRY "
                f"{fmt_price(entry)}"
            ),
            zorder=4
        )

        ax.hlines(
            tp,
            start_x_plot,
            end_x_plot,
            linestyles="--",
            linewidth=1.6,
            label=(
                f"TP 86.4% "
                f"{fmt_price(tp)}"
            ),
            zorder=4
        )

        if sl_value is not None:

            ax.hlines(
                sl_value,
                start_x_plot,
                end_x_plot,
                linestyles="--",
                linewidth=1.4,
                label=(
                    f"SL "
                    f"{fmt_price(sl_value)}"
                ),
                zorder=4
            )

        # ----------------------------------------------------
        # Confirmation line
        # ----------------------------------------------------

        ax.axvline(
            confirmation_dt,
            linestyle=":",
            linewidth=1.2,
            label="CONFIRMED",
            zorder=4
        )

        # ----------------------------------------------------
        # Highlight FINAL point
        # ----------------------------------------------------

        ax.scatter(
            [px[-1]],
            [py[-1]],
            s=70,
            zorder=11
        )

        # ----------------------------------------------------
        # Title
        # ----------------------------------------------------

        ax.set_title(
            (
                f"NDS {timeframe} | "
                f"{symbol} | "
                f"{hook['direction']} | "
                f"CONFIRMED HOOK | "
                f"HEIKIN ASHI"
            ),
            fontsize=12,
            fontweight="bold"
        )

        ax.set_xlabel(
            "Time UTC"
        )

        ax.set_ylabel(
            "Price"
        )

        # ----------------------------------------------------
        # Datetime axis
        # ----------------------------------------------------

        if timeframe == "H4":

            locator = mdates.AutoDateLocator(
                minticks=6,
                maxticks=10
            )

        else:

            locator = mdates.AutoDateLocator(
                minticks=8,
                maxticks=12
            )

        formatter = mdates.ConciseDateFormatter(
            locator
        )

        ax.xaxis.set_major_locator(
            locator
        )

        ax.xaxis.set_major_formatter(
            formatter
        )

        ax.set_xlim(
            start_x_plot,
            end_x_plot
        )

        ax.grid(
            alpha=0.25
        )

        ax.legend(
            loc="best",
            fontsize=8
        )

        fig.autofmt_xdate()

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        safe_symbol = (
            symbol
            .replace("/", "_")
            .replace(":", "_")
        )

        path = os.path.join(
            CHART_DIR,
            (
                f"{path_prefix}_"
                f"{safe_symbol}_"
                f"{hook['direction']}_"
                f"{int(hook['final']['time'])}.png"
            )
        )

        plt.tight_layout()

        plt.savefig(
            path,
            dpi=140,
            bbox_inches="tight"
        )

        plt.close(fig)

        return path

    except Exception as e:

        DIAG["api_errors"].append(
            f"Chart {symbol}: "
            f"{str(e)[:180]}"
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# M5 HOOK CHART REPORTING
# ============================================================

def send_detected_m5_hook_charts(
    symbol,
    df
):

    hooks, highs, lows = detect_hooks(
        df,
        M5_INTERVAL_MINUTES
    )

    now_ts = utc_now_ts()

    for hook in hooks:

        if not hook_is_confirmed(
            hook,
            now_ts
        ):
            continue

        if not hook_is_recent(
            hook,
            M5_MAX_HOOK_AGE_SECONDS,
            now_ts
        ):
            continue

        if (
            hook["range_pct"]
            < M5_MIN_HOOK_RANGE_PCT
        ):
            continue

        DIAG[
            "m5_range_valid_hooks"
        ] += 1

        key = hook_id(
            symbol,
            hook
        )

        if notification_exists(key):
            continue

        sl = calculate_sl(
            df,
            hook
        )

        chart_path = create_hook_chart(
            symbol,
            df,
            hook,
            sl,
            "detected_hook",
            "M5"
        )

        if not chart_path:

            DIAG[
                "hook_chart_errors"
            ] += 1

            continue

        caption = (
            f"🔎 <b>NDS M5 HOOK</b>\n"
            f"<b>{symbol}</b>\n"
            f"Direction: "
            f"<b>{hook['direction']}</b>\n\n"
            f"START: "
            f"{fmt_price(hook['start']['price'])}\n"
            f"FINAL: "
            f"{fmt_price(hook['final']['price'])}\n"
            f"Entry reference: "
            f"{fmt_price(hook['entry'])}\n"
            f"TP 86.4%: "
            f"<b>{fmt_price(hook['tp'])}</b>\n"
            f"SL reference: "
            f"{fmt_price(sl)}\n"
            f"Hook range: "
            f"{hook['range_pct']:.2f}%\n"
            f"Confirmed: "
            f"{fmt_ts(hook['confirmation_time'])}\n\n"
            f"<b>M5 HEIKIN ASHI HOOK CHART</b>\n"
            f"<b>PAPER TRADING ONLY</b>"
        )

        if telegram_send_photo(
            chart_path,
            caption
        ):

            mark_notification_sent(
                key,
                chart_path
            )

            DIAG[
                "hook_charts_sent"
            ] += 1

        else:

            DIAG[
                "hook_chart_errors"
            ] += 1

            DIAG["api_errors"].append(
                f"Hook chart not sent: "
                f"{symbol} "
                f"{hook['direction']}"
            )


# ============================================================
# H4 DIRECTION
# ============================================================

def get_h4_direction(symbol):

    DIAG[
        "h4_requests"
    ] += 1

    df = get_candles(
        symbol,
        H4_INTERVAL,
        H4_CANDLES
    )

    if df is None:

        DIAG[
            "h4_data_error"
        ] += 1

        return None

    if df.empty:

        DIAG[
            "h4_empty"
        ] += 1

        return None

    if len(df) < 50:

        DIAG[
            "h4_short"
        ] += 1

        return None

    DIAG[
        "h4_data_ok"
    ] += 1

    H4_DATA[
        symbol
    ] = df.copy()

    hooks, highs, lows = detect_hooks(
        df,
        H4_INTERVAL_MINUTES
    )

    DIAG[
        "h4_pivots"
    ] += (
        len(highs)
        + len(lows)
    )

    DIAG[
        "h4_hooks"
    ] += len(hooks)

    now_ts = utc_now_ts()

    confirmed = [
        h
        for h in hooks
        if hook_is_confirmed(
            h,
            now_ts
        )
    ]

    recent = [
        h
        for h in confirmed
        if hook_is_recent(
            h,
            H4_MAX_HOOK_AGE_SECONDS,
            now_ts
        )
    ]

    DIAG[
        "h4_confirmed_hooks"
    ] += len(confirmed)

    DIAG[
        "h4_recent_hooks"
    ] += len(recent)

    DIAG[
        "h4_valid_hooks"
    ] += len(recent)

    if not recent:
        return None

    recent.sort(
        key=lambda h: (
            h["confirmation_time"],
            h["final"]["time"]
        ),
        reverse=True
    )

    latest = recent[0]

    H4_DIRECTION[
        symbol
    ] = {
        "direction":
            latest["direction"],
        "hook":
            latest,
    }

    if latest["direction"] == "SHORT":

        DIAG[
            "h4_short_direction"
        ] += 1

    else:

        DIAG[
            "h4_long_direction"
        ] += 1

    return latest["direction"]


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook,
    sl
):

    direction = hook[
        "direction"
    ]

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    entry = float(
        hook["entry"]
    )

    tp = float(
        hook["tp"]
    )

    sl = float(sl)

    if direction == "SHORT":

        risk_pct = (
            (sl - entry)
            / entry
            * 100
        )

        reward_pct = (
            (entry - tp)
            / entry
            * 100
        )

    else:

        risk_pct = (
            (entry - sl)
            / entry
            * 100
        )

        reward_pct = (
            (tp - entry)
            / entry
            * 100
        )

    return (
        f"{emoji} "
        f"<b>NDS {direction}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"Entry: "
        f"<b>{fmt_price(entry)}</b>\n"

        f"SL: "
        f"<b>{fmt_price(sl)}</b> "
        f"({risk_pct:.2f}%)\n"

        f"TP 86.4%: "
        f"<b>{fmt_price(tp)}</b> "
        f"({reward_pct:.2f}%)\n\n"

        f"Hook Range: "
        f"{hook['range_pct']:.2f}%\n"

        f"Confirmed: "
        f"{fmt_ts(hook['confirmation_time'])}\n"

        f"<b>H4 direction confirmed</b>\n"
        f"<b>Paper Trading</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    # --------------------------------------------------------
    # 1. H4 direction
    # --------------------------------------------------------

    direction = get_h4_direction(
        symbol
    )

    # --------------------------------------------------------
    # 2. M5 data
    # --------------------------------------------------------

    DIAG[
        "m5_requests"
    ] += 1

    df = get_candles(
        symbol,
        M5_INTERVAL,
        M5_CANDLES
    )

    if df is None:

        DIAG[
            "m5_data_error"
        ] += 1

        if direction is None:

            DIAG[
                "h4_filtered"
            ] += 1

        return

    if df.empty:

        DIAG[
            "m5_empty"
        ] += 1

        if direction is None:

            DIAG[
                "h4_filtered"
            ] += 1

        return

    if len(df) < 100:

        DIAG[
            "m5_short"
        ] += 1

        if direction is None:

            DIAG[
                "h4_filtered"
            ] += 1

        return

    DIAG[
        "m5_data_ok"
    ] += 1

    # --------------------------------------------------------
    # 3. M5 pivots / hooks
    # --------------------------------------------------------

    hooks, highs, lows = detect_hooks(
        df,
        M5_INTERVAL_MINUTES
    )

    DIAG[
        "m5_pivots"
    ] += (
        len(highs)
        + len(lows)
    )

    DIAG[
        "m5_hooks"
    ] += len(hooks)

    now_ts = utc_now_ts()

    confirmed_m5 = [
        h
        for h in hooks
        if hook_is_confirmed(
            h,
            now_ts
        )
    ]

    recent_m5 = [
        h
        for h in confirmed_m5
        if hook_is_recent(
            h,
            M5_MAX_HOOK_AGE_SECONDS,
            now_ts
        )
    ]

    DIAG[
        "m5_confirmed_hooks"
    ] += len(confirmed_m5)

    DIAG[
        "m5_recent_hooks"
    ] += len(recent_m5)

    # --------------------------------------------------------
    # 4. M5 confirmed/recent valid Hook charts
    #
    # Independent of H4 direction.
    # --------------------------------------------------------

    send_detected_m5_hook_charts(
        symbol,
        df
    )

    # --------------------------------------------------------
    # 5. H4 filter applies ONLY to trading
    # --------------------------------------------------------

    if direction is None:

        DIAG[
            "h4_filtered"
        ] += 1

        return

    # --------------------------------------------------------
    # 6. M5 candidates for actual PAPER signal
    # --------------------------------------------------------

    candidates = []

    for hook in recent_m5:

        if (
            hook["direction"]
            != direction
        ):
            continue

        if (
            hook["range_pct"]
            < M5_MIN_HOOK_RANGE_PCT
        ):
            continue

        if tp_already_touched(
            df,
            hook
        ):
            continue

        candidates.append(
            hook
        )

    DIAG[
        "m5_valid_hooks"
    ] += len(candidates)

    if not candidates:
        return

    candidates.sort(
        key=lambda h: (
            h["confirmation_time"],
            h["final"]["time"]
        ),
        reverse=True
    )

    hook = candidates[0]

    sl = calculate_sl(
        df,
        hook
    )

    if sl is None:
        return

    entry = float(
        hook["entry"]
    )

    tp = float(
        hook["tp"]
    )

    sl = float(sl)

    # --------------------------------------------------------
    # Price geometry safety check
    # --------------------------------------------------------

    if direction == "SHORT":

        if not (
            sl
            > entry
            > tp
        ):
            return

    else:

        if not (
            sl
            < entry
            < tp
        ):
            return

    # --------------------------------------------------------
    # Duplicate protection
    # --------------------------------------------------------

    key = hook_id(
        symbol,
        hook
    )

    if (
        hook_exists(key)
        or trade_exists(key)
    ):

        DIAG[
            "duplicate_signals"
        ] += 1

        return

    # --------------------------------------------------------
    # Maximum open trades
    # --------------------------------------------------------

    if (
        open_trade_count()
        >= MAX_OPEN_TRADES
    ):

        DIAG[
            "max_open"
        ] += 1

        return

    # --------------------------------------------------------
    # M5 SIGNAL CHART
    # --------------------------------------------------------

    chart_path = create_hook_chart(
        symbol,
        df,
        hook,
        sl,
        "signal_m5",
        "M5"
    )

    save_hook(
        symbol,
        hook,
        sl,
        chart_path
    )

    save_trade(
        key,
        symbol,
        hook,
        sl,
        chart_path
    )

    DIAG[
        "signals"
    ] += 1

    # --------------------------------------------------------
    # SIGNAL MESSAGE
    # --------------------------------------------------------

    telegram_send(
        build_signal_message(
            symbol,
            hook,
            sl
        )
    )

    if chart_path:

        telegram_send_photo(
            chart_path,
            (
                f"📍 "
                f"<b>NDS {direction} SIGNAL | M5</b>\n"
                f"<b>{symbol}</b>\n"
                f"Entry: "
                f"{fmt_price(hook['entry'])}\n"
                f"TP 86.4%: "
                f"{fmt_price(hook['tp'])}\n"
                f"SL: "
                f"{fmt_price(sl)}\n"
                f"<b>HEIKIN ASHI CHART</b>\n"
                f"<b>PAPER TRADING ONLY</b>"
            )
        )

    # --------------------------------------------------------
    # H4 CONFIRMATION CHART
    # --------------------------------------------------------

    h4_info = H4_DIRECTION.get(
        symbol
    )

    h4_df = H4_DATA.get(
        symbol
    )

    if (
        h4_info
        and h4_df is not None
    ):

        h4_hook = h4_info.get(
            "hook"
        )

        if h4_hook:

            h4_sl = calculate_sl(
                h4_df,
                h4_hook
            )

            h4_chart_path = (
                create_hook_chart(
                    symbol,
                    h4_df,
                    h4_hook,
                    h4_sl,
                    "signal_h4",
                    "H4"
                )
            )

            if h4_chart_path:

                telegram_send_photo(
                    h4_chart_path,
                    (
                        f"🧭 "
                        f"<b>H4 DIRECTION "
                        f"CONFIRMATION</b>\n"
                        f"<b>{symbol}</b>\n"
                        f"Direction: "
                        f"<b>{direction}</b>\n"
                        f"This H4 Hook "
                        f"authorized "
                        f"the M5 signal.\n"
                        f"<b>HEIKIN ASHI CHART</b>\n"
                        f"<b>PAPER TRADING ONLY</b>"
                    )
                )


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
    close_price,
    pnl_pct,
    pnl_price,
    reason
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
            close_price,
            reason,
            pnl_pct,
            pnl_price,
            trade["id"],
        )
    )

    conn.commit()
    conn.close()


def monitor_open_trades():

    for trade in get_open_trades():

        symbol = trade[
            "symbol"
        ]

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

        try:

            df = get_candles(
                symbol,
                M5_INTERVAL,
                30
            )

            if (
                df is None
                or df.empty
            ):
                continue

            future = df[
                df["time"]
                >= trade["opened_at"]
            ]

            if future.empty:
                continue

            close_price = None
            reason = None

            for _, candle in (
                future.iterrows()
            ):

                high = float(
                    candle["high"]
                )

                low = float(
                    candle["low"]
                )

                if direction == "SHORT":

                    hit_sl = (
                        high >= sl
                    )

                    hit_tp = (
                        low <= tp
                    )

                else:

                    hit_sl = (
                        low <= sl
                    )

                    hit_tp = (
                        high >= tp
                    )

                if hit_sl:

                    close_price = sl
                    reason = "SL"

                    break

                if hit_tp:

                    close_price = tp
                    reason = "TP"

                    break

            if reason is None:
                continue

            if direction == "SHORT":

                pnl_price = (
                    entry
                    - close_price
                )

            else:

                pnl_price = (
                    close_price
                    - entry
                )

            pnl_pct = (
                pnl_price
                / entry
                * 100
                if entry
                else 0.0
            )

            close_trade(
                trade,
                close_price,
                pnl_pct,
                pnl_price,
                reason
            )

            emoji = (
                "✅"
                if pnl_pct >= 0
                else "❌"
            )

            telegram_send(
                (
                    f"{emoji} "
                    f"<b>NDS {direction} "
                    f"CLOSED</b>\n"
                    f"<b>{symbol}</b>\n\n"
                    f"Entry: "
                    f"<b>{fmt_price(entry)}</b>\n"
                    f"Close: "
                    f"<b>{fmt_price(close_price)}</b>\n"
                    f"Result: "
                    f"<b>{reason}</b>\n"
                    f"PnL: "
                    f"<b>{pnl_pct:+.2f}%</b>"
                )
            )

        except Exception as e:

            DIAG["api_errors"].append(
                f"Monitor {symbol}: "
                f"{str(e)[:180]}"
            )


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
        "total": int(
            total or 0
        ),
        "wins": int(
            wins or 0
        ),
        "losses": int(
            losses or 0
        ),
        "pnl": float(
            pnl or 0
        ),
    }


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_text():

    perf = performance_summary()

    lines = [

        "🔎 "
        "<b>NDS H4 → M5 DIAGNOSTIC</b>",

        f"Version: "
        f"<b>{VERSION}</b>",

        f"Time: "
        f"{utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}",

        f"Runtime: "
        f"<b>{time.time() - START_TIME:.1f}s</b>",

        "",

        "━━━ <b>ASSETS</b> ━━━",

        f"Scanned: "
        f"<b>{DIAG['assets_scanned']}</b>",

        "",

        "━━━ <b>H4</b> ━━━",

        f"Requests: "
        f"{DIAG['h4_requests']}",

        f"Data OK: "
        f"{DIAG['h4_data_ok']}",

        f"Data Error: "
        f"{DIAG['h4_data_error']}",

        f"Empty: "
        f"{DIAG['h4_empty']}",

        f"Short: "
        f"{DIAG['h4_short']}",

        f"Pivot Points: "
        f"{DIAG['h4_pivots']}",

        f"Hooks: "
        f"{DIAG['h4_hooks']}",

        f"Confirmed Hooks: "
        f"{DIAG['h4_confirmed_hooks']}",

        f"Recent Hooks: "
        f"{DIAG['h4_recent_hooks']}",

        f"Valid Hooks: "
        f"{DIAG['h4_valid_hooks']}",

        f"SHORT Direction: "
        f"{DIAG['h4_short_direction']}",

        f"LONG Direction: "
        f"{DIAG['h4_long_direction']}",

        f"Filtered: "
        f"{DIAG['h4_filtered']}",

        "",

        "━━━ <b>M5</b> ━━━",

        f"Requests: "
        f"{DIAG['m5_requests']}",

        f"Data OK: "
        f"{DIAG['m5_data_ok']}",

        f"Data Error: "
        f"{DIAG['m5_data_error']}",

        f"Empty: "
        f"{DIAG['m5_empty']}",

        f"Short: "
        f"{DIAG['m5_short']}",

        f"Pivot Points: "
        f"{DIAG['m5_pivots']}",

        f"Hooks: "
        f"{DIAG['m5_hooks']}",

        f"Confirmed Hooks: "
        f"{DIAG['m5_confirmed_hooks']}",

        f"Recent Hooks: "
        f"{DIAG['m5_recent_hooks']}",

        f"Range Valid Hooks: "
        f"{DIAG['m5_range_valid_hooks']}",

        f"Valid Hooks: "
        f"{DIAG['m5_valid_hooks']}",

        f"Hook Charts Sent: "
        f"{DIAG['hook_charts_sent']}",

        f"Hook Chart Errors: "
        f"{DIAG['hook_chart_errors']}",

        "",

        "━━━ <b>SIGNALS</b> ━━━",

        f"Signals: "
        f"{DIAG['signals']}",

        f"Duplicates: "
        f"{DIAG['duplicate_signals']}",

        f"Max Open Limit: "
        f"{DIAG['max_open']}",

        f"Open Trades: "
        f"<b>{open_trade_count()}</b>",

        "",

        "━━━ <b>PAPER PERFORMANCE</b> ━━━",

        f"Closed Trades: "
        f"{perf['total']}",

        f"TP: "
        f"{perf['wins']}",

        f"SL: "
        f"{perf['losses']}",

        f"PnL: "
        f"<b>{perf['pnl']:+.2f}%</b>",
    ]

    if DIAG["api_errors"]:

        lines += [
            "",
            "━━━ <b>ERRORS</b> ━━━"
        ]

        lines += [
            f"• {e}"
            for e in DIAG[
                "api_errors"
            ][-5:]
        ]

    lines += [
        "",
        "<b>PAPER TRADING ONLY</b>"
    ]

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        init_db()

        print(
            f"NDS H4 -> M5 Scanner "
            f"{VERSION}"
        )

        print(
            "H4 direction -> M5 signal"
        )

        print(
            "M5 confirmed hooks are "
            "reported independently."
        )

        print(
            "PAPER TRADING ONLY - "
            "NO REAL ORDERS"
        )

        print(
            "Charts: HEIKIN ASHI"
        )

        print(
            f"H4 pivot confirmation: "
            f"{H4_INTERVAL_MINUTES}m x "
            f"{PIVOT_RIGHT}"
        )

        print(
            f"M5 pivot confirmation: "
            f"{M5_INTERVAL_MINUTES}m x "
            f"{PIVOT_RIGHT}"
        )

        print(
            f"H4 hook max age: "
            f"{H4_MAX_HOOK_AGE_SECONDS // 3600}h"
        )

        print(
            f"M5 hook max age: "
            f"{M5_MAX_HOOK_AGE_SECONDS // 3600}h"
        )

        symbols = (
            get_futures_instruments()
        )

        if not symbols:

            report = (
                "❌ "
                "<b>NDS Scanner</b>\n\n"
                "No futures instruments found."
            )

            print(report)

            telegram_send(report)

            return

        DIAG[
            "assets_scanned"
        ] = len(symbols)

        monitor_open_trades()

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
            (
                "❌ "
                "<b>NDS Scanner Error</b>\n\n"
                f"{type(e).__name__}: "
                f"{str(e)[:500]}"
            )
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
