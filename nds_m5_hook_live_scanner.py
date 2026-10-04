# ============================================================
# NDS M5 LIVE SCANNER
# VERSION 5.5.0
# PAPER TRADING ONLY - NO REAL ORDERS
#
# M5 ONLY
#
# IMPORTANT:
#   - H4 FILTER COMPLETELY REMOVED
#   - No H4 data request
#   - No H4 Hook detection
#   - No H4 direction
#   - No H4 authorization
#   - No H4/M5 direction matching
#
# M5:
#   - Detect NDS Hooks
#   - Confirm Hook after PIVOT_RIGHT candles
#   - Send chart for confirmed/recent M5 Hook
#   - Range filter
#   - TP = 86.4% retracement
#   - SL = previous valid M5 pivot
#   - Create PAPER trade for valid M5 Hook
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
#   86.4% retracement from START toward FINAL
#
# SHORT:
#   TP = FINAL - 86.4% * (FINAL - START)
#
# LONG:
#   TP = FINAL + 86.4% * (START - FINAL)
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
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.5.0"

REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

TARGET_ASSETS = 100

M5_INTERVAL = "5m"
M5_INTERVAL_MINUTES = 5

M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# Minimum number of candles required between
# consecutive nodes of an NDS Hook.
#
# 10 candles or more -> allowed
# 9 candles or less  -> rejected
MIN_NODE_CANDLES = 10

NDS_RETRACE = 0.864

M5_MIN_HOOK_RANGE_PCT = 0.20

# M5 hooks are short-lived.
M5_MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25

CHART_CANDLES = 240

DB_FILE = "nds_m5_live_v550.db"
CHART_DIR = "nds_m5_live_charts"


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


START_TIME = time.time()


# ============================================================
# TIME / FORMAT
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

    url = (
        f"https://api.telegram.org/"
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
                f"Telegram text HTTP {r.status_code}"
            )

        return r.ok

    except Exception as e:

        DIAG["api_errors"].append(
            f"Telegram text: {str(e)[:160]}"
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
        f"https://api.telegram.org/"
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
            f"Instruments: {str(e)[:180]}"
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
                isinstance(c, (list, tuple))
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
            df["time"].astype(float)
        )

        df = (
            df
            .sort_values("time")
            .drop_duplicates("time")
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
        < PIVOT_LEFT
        + PIVOT_RIGHT
        + 1
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

    """
    Detect NDS Hooks from explicit pivot lists.

    Returns:
        hooks
        ordered_pivots
    """

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

        # ====================================================
        # MINIMUM NODE DISTANCE
        # ====================================================

        if any(
            (
                p[j]["index"]
                - p[j - 1]["index"]
            ) < MIN_NODE_CANDLES
            for j in range(
                1,
                len(p)
            )
        ):

            continue

        types = [
            x["type"]
            for x in p
        ]

        # ====================================================
        # POSITIVE HOOK / SHORT
        #
        # START(L) -> H1 -> L1 -> H2 -> L2 -> H3
        # ====================================================

        if types == [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:

            start, h1, l1, h2, l2, h3 = p

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

            # START MUST BE THE LOWEST
            # POINT OF THE WHOLE HOOK.

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

                "entry": h3["price"],
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
        #
        # START(H) -> L1 -> H1 -> L2 -> H2 -> L3
        # ====================================================

        if types == [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:

            start, l1, h1, l2, h2, l3 = p

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

            # START MUST BE THE HIGHEST
            # POINT OF THE WHOLE HOOK.

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

                "entry": l3["price"],
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

    if (
        df is None
        or df.empty
    ):

        return [], [], []

    highs, lows = find_pivots(
        df
    )

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

    CREATE TABLE IF NOT EXISTS hook_chart_notifications (
        hook_key TEXT PRIMARY KEY,
        sent_at INTEGER,
        chart_path TEXT
    );

    """)

    conn.commit()
    conn.close()


def notification_exists(
    key
):

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
        (hook_key,sent_at,chart_path)
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


def hook_exists(
    key
):

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


def trade_exists(
    key
):

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
            ).get("price"),
            hook.get(
                "l1",
                {}
            ).get("price"),
            hook.get(
                "h2",
                {}
            ).get("price"),
            hook.get(
                "l2",
                {}
            ).get("price"),
            hook["final"]["price"],
            hook["entry"],
            hook["tp"],
            sl,
            hook["range_pct"],
            int(
                hook["confirmation_time"]
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

    highs, lows = find_pivots(
        df
    )

    final_time = hook[
        "final"
    ]["time"]

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

        chart_df = (
            df.tail(
                CHART_CANDLES
            )
            .copy()
        )

        if chart_df.empty:

            return None

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        # ====================================================
        # HEIKIN ASHI
        #
        # IMPORTANT:
        # Heikin Ashi is ONLY for visualization.
        #
        # NDS detection, Hook prices, Entry, TP and SL
        # continue to use ORIGINAL OHLC data.
        # ====================================================

        ha_df = chart_df.copy()

        ha_df["ha_close"] = (
            ha_df["open"]
            + ha_df["high"]
            + ha_df["low"]
            + ha_df["close"]
        ) / 4.0

        ha_open_values = []

        for i in range(
            len(ha_df)
        ):

            raw_open = float(
                ha_df.iloc[i]["open"]
            )

            raw_close = float(
                ha_df.iloc[i]["close"]
            )

            if i == 0:

                ha_open = (
                    raw_open
                    + raw_close
                ) / 2.0

            else:

                prev_ha_open = (
                    ha_open_values[
                        i - 1
                    ]
                )

                prev_ha_close = float(
                    ha_df.iloc[
                        i - 1
                    ]["ha_close"]
                )

                ha_open = (
                    prev_ha_open
                    + prev_ha_close
                ) / 2.0

            ha_open_values.append(
                ha_open
            )

        ha_df["ha_open"] = (
            ha_open_values
        )

        ha_df["ha_high"] = ha_df[
            [
                "high",
                "ha_open",
                "ha_close",
            ]
        ].max(axis=1)

        ha_df["ha_low"] = ha_df[
            [
                "low",
                "ha_open",
                "ha_close",
            ]
        ].min(axis=1)

        time_deltas = (
            ha_df["time"]
            .diff()
            .dropna()
        )

        if not time_deltas.empty:

            candle_width = (
                float(
                    time_deltas.median()
                )
                / 86400.0
                * 0.70
            )

        else:

            candle_width = (
                M5_INTERVAL_MINUTES
                / 1440.0
                * 0.70
            )

        # ====================================================
        # DRAW HEIKIN ASHI CANDLES
        # ====================================================

        for _, candle in ha_df.iterrows():

            dt = datetime.fromtimestamp(
                float(
                    candle["time"]
                ),
                tz=timezone.utc
            )

            x_pos = (
                dt.timestamp()
                / 86400.0
            )

            ha_open = float(
                candle["ha_open"]
            )

            ha_high = float(
                candle["ha_high"]
            )

            ha_low = float(
                candle["ha_low"]
            )

            ha_close = float(
                candle["ha_close"]
            )

            ax.plot(
                [dt, dt],
                [ha_low, ha_high],
                linewidth=0.8,
                zorder=1
            )

            body_low = min(
                ha_open,
                ha_close
            )

            body_high = max(
                ha_open,
                ha_close
            )

            body_height = (
                body_high
                - body_low
            )

            if body_height <= 0:

                body_height = (
                    max(
                        abs(ha_close),
                        1.0
                    )
                    * 0.00001
                )

            if ha_close >= ha_open:

                face_color = "green"

            else:

                face_color = "red"

            rect = Rectangle(
                (
                    x_pos
                    - candle_width / 2.0,
                    body_low,
                ),
                candle_width,
                body_height,
                facecolor=face_color,
                edgecolor=face_color,
                linewidth=0.8,
                alpha=0.75,
                zorder=2
            )

            ax.add_patch(rect)

        ax.plot(
            [],
            [],
            linewidth=6,
            label=f"{timeframe} Heikin Ashi"
        )

        # ====================================================
        # HOOK NODES
        # ====================================================

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

        ax.plot(
            px,
            py,
            marker="o",
            linewidth=2.0,
            label="Confirmed NDS Hook"
        )

        offsets = [
            (0, 18),
            (0, 24),
            (0, -28),
            (0, 24),
            (0, -28),
            (0, 24),
        ]

        for p, label, offset in zip(
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
                (
                    dt,
                    p["price"]
                ),
                xytext=offset,
                textcoords="offset points",
                ha="center",
                fontsize=8,
                bbox=dict(
                    boxstyle="round,pad=0.2",
                    fc="white",
                    alpha=0.80
                ),
            )

        # ====================================================
        # ENTRY / TP / SL
        # ====================================================

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        start_x = min(
            px[0],
            datetime.fromtimestamp(
                float(
                    chart_df[
                        "time"
                    ].iloc[0]
                ),
                tz=timezone.utc
            )
        )

        confirmation_dt = (
            datetime.fromtimestamp(
                hook[
                    "confirmation_time"
                ],
                tz=timezone.utc
            )
        )

        end_x = max(
            px[-1],
            datetime.fromtimestamp(
                float(
                    chart_df[
                        "time"
                    ].iloc[-1]
                ),
                tz=timezone.utc
            ),
            confirmation_dt
        )

        ax.hlines(
            entry,
            start_x,
            end_x,
            linestyles="--",
            linewidth=1.4,
            label=(
                f"ENTRY "
                f"{fmt_price(entry)}"
            ),
        )

        ax.hlines(
            tp,
            start_x,
            end_x,
            linestyles="--",
            linewidth=1.6,
            label=(
                f"TP 86.4% "
                f"{fmt_price(tp)}"
            ),
        )

        if sl is not None:

            ax.hlines(
                float(sl),
                start_x,
                end_x,
                linestyles="--",
                linewidth=1.4,
                label=(
                    f"SL "
                    f"{fmt_price(sl)}"
                ),
            )

        ax.axvline(
            confirmation_dt,
            linestyle=":",
            linewidth=1.2,
            label="CONFIRMED"
        )

        ax.set_title(
            f"NDS {timeframe} | "
            f"{symbol} | "
            f"{hook['direction']} | "
            f"CONFIRMED HOOK"
        )

        ax.set_xlabel(
            "Time UTC"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.25
        )

        ax.legend(
            loc="best",
            fontsize=8
        )

        fig.autofmt_xdate()

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
            dpi=140
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
    df,
    hooks=None
):

    """
    Send confirmed/recent M5 Hook charts.

    This is now the MAIN Hook reporting path.
    There is NO H4 dependency.
    """

    if hooks is None:

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

        if notification_exists(
            key
        ):

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

        sl_text = (
            fmt_price(sl)
            if sl is not None
            else "-"
        )

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
            f"{sl_text}\n"
            f"Hook range: "
            f"{hook['range_pct']:.2f}%\n"
            f"Confirmed: "
            f"{fmt_ts(hook['confirmation_time'])}\n\n"
            f"<b>M5 HOOK CHART</b>\n"
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

            DIAG[
                "api_errors"
            ].append(
                f"Hook chart not sent: "
                f"{symbol} "
                f"{hook['direction']}"
            )


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

        f"<b>M5 NDS SIGNAL</b>\n"
        f"<b>Paper Trading</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol
):

    # --------------------------------------------------------
    # 1. M5 DATA ONLY
    #
    # H4 FILTER COMPLETELY REMOVED.
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

        return

    if df.empty:

        DIAG[
            "m5_empty"
        ] += 1

        return

    if len(df) < 100:

        DIAG[
            "m5_short"
        ] += 1

        return

    DIAG[
        "m5_data_ok"
    ] += 1

    # --------------------------------------------------------
    # 2. M5 PIVOTS + HOOKS
    # --------------------------------------------------------

    hooks, highs, lows = detect_hooks(
        df,
        M5_INTERVAL_MINUTES
    )

    DIAG[
        "m5_pivots"
    ] += len(highs) + len(lows)

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
    ] += len(
        confirmed_m5
    )

    DIAG[
        "m5_recent_hooks"
    ] += len(
        recent_m5
    )

    # --------------------------------------------------------
    # 3. SEND M5 HOOK CHARTS
    #
    # NO H4 DEPENDENCY.
    # --------------------------------------------------------

    send_detected_m5_hook_charts(
        symbol,
        df,
        hooks
    )

    # --------------------------------------------------------
    # 4. M5 VALID CANDIDATES FOR PAPER SIGNAL
    # --------------------------------------------------------

    candidates = []

    for hook in recent_m5:

        # ----------------------------------------------------
        # Range filter
        # ----------------------------------------------------

        if (
            hook["range_pct"]
            < M5_MIN_HOOK_RANGE_PCT
        ):

            continue

        # ----------------------------------------------------
        # TP already touched
        # ----------------------------------------------------

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
    ] += len(
        candidates
    )

    if not candidates:

        return

    # --------------------------------------------------------
    # 5. MOST RECENT VALID M5 HOOK
    # --------------------------------------------------------

    candidates.sort(
        key=lambda h: (
            h["confirmation_time"],
            h["final"]["time"]
        ),
        reverse=True
    )

    hook = candidates[0]

    # --------------------------------------------------------
    # 6. SL
    # --------------------------------------------------------

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
    # 7. PRICE GEOMETRY SAFETY CHECK
    # --------------------------------------------------------

    if hook["direction"] == "SHORT":

        if not (
            sl > entry > tp
        ):

            return

    else:

        if not (
            sl < entry < tp
        ):

            return

    # --------------------------------------------------------
    # 8. DUPLICATE PROTECTION
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
    # 9. MAXIMUM OPEN TRADES
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
    # 10. M5 SIGNAL CHART
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
    # 11. SIGNAL MESSAGE
    # --------------------------------------------------------

    telegram_send(
        build_signal_message(
            symbol,
            hook,
            sl
        )
    )

    # --------------------------------------------------------
    # 12. SIGNAL CHART
    # --------------------------------------------------------

    if chart_path:

        telegram_send_photo(
            chart_path,
            (
                f"📍 "
                f"<b>NDS "
                f"{hook['direction']} "
                f"SIGNAL | M5</b>\n"
                f"<b>{symbol}</b>\n"
                f"Entry: "
                f"{fmt_price(hook['entry'])}\n"
                f"TP 86.4%: "
                f"{fmt_price(hook['tp'])}\n"
                f"SL: "
                f"{fmt_price(sl)}\n"
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

            for _, candle in future.iterrows():

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

                # Conservative:
                # if both hit in same candle,
                # SL is assumed first.

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

            DIAG[
                "api_errors"
            ].append(
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
        "<b>NDS M5 DIAGNOSTIC</b>",

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
        "<b>M5 ONLY</b>",
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
            f"NDS M5 Scanner "
            f"{VERSION}"
        )

        print(
            "M5 ONLY"
        )

        print(
            "H4 FILTER COMPLETELY REMOVED"
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
            f"M5 pivot confirmation: "
            f"{M5_INTERVAL_MINUTES}m x "
            f"{PIVOT_RIGHT}"
        )

        print(
            f"M5 hook max age: "
            f"{M5_MAX_HOOK_AGE_SECONDS // 3600}h"
        )

        print(
            f"Minimum node distance: "
            f"{MIN_NODE_CANDLES} candles"
        )

        print(
            f"M5 minimum hook range: "
            f"{M5_MIN_HOOK_RANGE_PCT:.2f}%"
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

            telegram_send(
                report
            )

            return

        DIAG[
            "assets_scanned"
        ] = len(symbols)

        # ----------------------------------------------------
        # Monitor existing paper trades
        # ----------------------------------------------------

        monitor_open_trades()

        # ----------------------------------------------------
        # Scan all M5 symbols
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Monitor again after scan
        # ----------------------------------------------------

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
