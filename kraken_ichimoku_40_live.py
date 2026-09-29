# ============================================================
# NDS M30 LIVE SCANNER
# VERSION 4.7.0
# ============================================================
#
# PAPER ONLY
# M30 ONLY
# NO M1
# NO 123F
#
# POSITIVE / SHORT:
# START -> H1 -> L1 -> H2 -> L2 -> H3
#
# NEGATIVE / LONG:
# START -> L1 -> H1 -> L2 -> H2 -> L3
#
# ENTRY = H3 / L3
# TP    = 86.4% retracement
# SL    = 50% of TP distance
#
# EXIT FIX:
#   Uses LAST M30 candle HIGH/LOW + CLOSE.
#   If TP/SL was crossed between scans,
#   EXIT PRICE = EXACT TP/SL.
#
# REPORT:
#   WIN RATE
#   REALIZED PNL
#   OPEN/LIVE PNL
#   TOTAL PNL
#
# ============================================================

import os
import time
import sqlite3
import hashlib
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.7.0"

DB_FILE = "nds_m30_m1_v44.db"
CHART_DIR = "nds_charts"

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

INTERVAL = "30m"
M30_COUNT = 320

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_SWING_PCT = 0.0005

TP_RETRACE = 0.864

MIN_TP_DISTANCE_PCT = 0.30
MAX_TP_DISTANCE_PCT = 5.00

SL_TP_MULTIPLIER = 0.50

REQUEST_TIMEOUT = 20

CHART_CANDLES = 180

SCAN_SECONDS = 300
REPORT_SECONDS = 900

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
# ASSETS
# ============================================================

ASSETS = [
    "PI_XBTUSD",
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_AVAXUSD",
    "PF_LINKUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",
    "PF_BCHUSD",
    "PF_TRXUSD",
    "PF_ATOMUSD",
    "PF_NEARUSD",
    "PF_FILUSD",
    "PF_ALGOUSD",
    "PF_APTUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_SUIUSD",
    "PF_INJUSD",
    "PF_SEIUSD",
    "PF_TIAUSD",
    "PF_PEPEUSD",
    "PF_WIFUSD",
    "PF_FLOKIUSD",
    "PF_BONKUSD",
    "PF_SHIBUSD",
    "PF_LDOUSD",
    "PF_AAVEUSD",
    "PF_UNIUSD",
    "PF_MKRUSD",
    "PF_ETCUSD",
    "PF_XLMUSD",
    "PF_XTZUSD",
    "PF_HBARUSD",
    "PF_EOSUSD",
    "PF_RUNEUSD",
]


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {}


def reset_diag():
    global DIAG

    DIAG = {
        "requests": 0,
        "data_ok": 0,
        "empty": 0,
        "short_data": 0,
        "errors": 0,

        "pivot_highs": 0,
        "pivot_lows": 0,
        "alternating_pivots": 0,
        "sequences": 0,

        "short_candidates": 0,
        "short_structure_valid": 0,
        "short_start_rejected": 0,
        "short_h2_rejected": 0,
        "short_l2_rejected": 0,
        "short_h3_rejected": 0,

        "long_candidates": 0,
        "long_structure_valid": 0,
        "long_start_rejected": 0,
        "long_l2_rejected": 0,
        "long_h2_rejected": 0,
        "long_l3_rejected": 0,

        "tp_valid_distance": 0,
        "tp_distance_rejected": 0,
        "tp_already_touched": 0,

        "confirmed_hooks": 0,
        "new_signals": 0,
        "charts": 0,
        "opened_trades": 0,

        "open_trades": 0,
        "tp_hits": 0,
        "sl_hits": 0,
    }


reset_diag()


# ============================================================
# TIME / FORMAT
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def fmt_price(value):
    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 100:
        return f"{value:,.3f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    if value >= 0.0001:
        return f"{value:.8f}"

    return f"{value:.10f}"


def pct_change(entry, price, direction):
    entry = float(entry)
    price = float(price)

    if entry == 0:
        return 0.0

    if direction == "LONG":
        return ((price - entry) / entry) * 100.0

    return ((entry - price) / entry) * 100.0


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hooks (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_price REAL NOT NULL,
            entry REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            confirmation_time TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            entry_time TEXT NOT NULL,
            current_price REAL,
            current_pct REAL,
            tp_pct REAL,
            sl_pct REAL,
            status TEXT NOT NULL,
            exit_price REAL,
            exit_time TEXT,
            pnl_pct REAL,
            last_update TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def hook_exists(hook_id_value):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM hooks
        WHERE id = ?
        """,
        (hook_id_value,),
    ).fetchone()

    conn.close()

    return row is not None


def save_hook(hook):
    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO hooks (
            id,
            symbol,
            direction,
            start_price,
            entry,
            tp,
            sl,
            confirmation_time,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hook["id"],
            hook["symbol"],
            hook["direction"],
            hook["start_price"],
            hook["entry"],
            hook["tp"],
            hook["sl"],
            hook["confirmation_time"],
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()


def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def get_closed_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE status = 'CLOSED'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def has_open_trade(symbol):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM paper_trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    conn.close()

    return row is not None


def create_paper_trade(hook, current_price):
    conn = db_connect()

    entry = float(hook["entry"])
    tp = float(hook["tp"])
    sl = float(hook["sl"])
    direction = hook["direction"]

    tp_pct = abs(
        pct_change(
            entry,
            tp,
            direction,
        )
    )

    sl_pct = abs(
        pct_change(
            entry,
            sl,
            direction,
        )
    )

    current_pct = pct_change(
        entry,
        current_price,
        direction,
    )

    cursor = conn.execute(
        """
        INSERT INTO paper_trades (
            hook_id,
            symbol,
            direction,
            entry,
            tp,
            sl,
            entry_time,
            current_price,
            current_pct,
            tp_pct,
            sl_pct,
            status,
            exit_price,
            exit_time,
            pnl_pct,
            last_update
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            'OPEN',
            NULL, NULL, NULL, ?
        )
        """,
        (
            hook["id"],
            hook["symbol"],
            direction,
            entry,
            tp,
            sl,
            hook["confirmation_time"],
            current_price,
            current_pct,
            tp_pct,
            sl_pct,
            now_iso(),
        ),
    )

    trade_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return trade_id


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats():
    closed = get_closed_trades()
    opened = get_open_trades()

    total_closed = len(closed)

    tp_wins = sum(
        1
        for t in closed
        if float(t["pnl_pct"] or 0) > 0
    )

    sl_losses = sum(
        1
        for t in closed
        if float(t["pnl_pct"] or 0) <= 0
    )

    if total_closed > 0:
        win_rate = (
            tp_wins / total_closed
        ) * 100.0
    else:
        win_rate = 0.0

    realized_pnl = sum(
        float(t["pnl_pct"] or 0)
        for t in closed
    )

    live_pnl = sum(
        float(t["current_pct"] or 0)
        for t in opened
    )

    total_pnl = (
        realized_pnl +
        live_pnl
    )

    return {
        "closed": total_closed,
        "wins": tp_wins,
        "losses": sl_losses,
        "win_rate": win_rate,
        "realized_pnl": realized_pnl,
        "live_pnl": live_pnl,
        "total_pnl": total_pnl,
        "open_count": len(opened),
    }


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
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception as exc:
        print(
            "Telegram text error:",
            exc,
        )
        return False


def telegram_send_photo(
    photo_path,
    caption="",
):
    if not telegram_enabled():
        return False

    if not os.path.exists(photo_path):
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(
            photo_path,
            "rb",
        ) as photo:

            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={
                    "photo": photo,
                },
                timeout=REQUEST_TIMEOUT,
            )

        return response.ok

    except Exception as exc:
        print(
            "Telegram photo error:",
            exc,
        )
        return False


# ============================================================
# KRAKEN M30
# ============================================================

def fetch_m30(symbol):
    DIAG["requests"] += 1

    url = (
        f"{BASE_URL}/"
        f"{symbol}/"
        f"{INTERVAL}"
    )

    try:
        response = requests.get(
            url,
            params={
                "since": 0,
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        candles = payload.get(
            "candles",
            [],
        )

        if not candles:
            DIAG["empty"] += 1
            return None

        rows = []

        for candle in candles:
            try:
                timestamp = float(
                    candle["time"]
                )

                if timestamp > 10_000_000_000:
                    timestamp /= 1000.0

                rows.append(
                    {
                        "time": pd.to_datetime(
                            timestamp,
                            unit="s",
                            utc=True,
                        ),
                        "open": float(
                            candle["open"]
                        ),
                        "high": float(
                            candle["high"]
                        ),
                        "low": float(
                            candle["low"]
                        ),
                        "close": float(
                            candle["close"]
                        ),
                        "volume": float(
                            candle.get(
                                "volume",
                                0,
                            )
                        ),
                    }
                )

            except Exception:
                continue

        if len(rows) < 50:
            DIAG["short_data"] += 1
            return None

        df = pd.DataFrame(rows)

        df = (
            df
            .drop_duplicates(
                subset=["time"]
            )
            .sort_values("time")
            .tail(M30_COUNT)
            .reset_index(drop=True)
        )

        DIAG["data_ok"] += 1

        return df

    except Exception as exc:
        DIAG["errors"] += 1

        print(
            f"{symbol} M30 error:",
            exc,
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):
    highs = []
    lows = []

    high_values = (
        df["high"]
        .to_numpy(dtype=float)
    )

    low_values = (
        df["low"]
        .to_numpy(dtype=float)
    )

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        left_highs = high_values[
            i - PIVOT_LEFT:i
        ]

        right_highs = high_values[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        left_lows = low_values[
            i - PIVOT_LEFT:i
        ]

        right_lows = low_values[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            high_values[i]
            >= np.max(left_highs)
            and
            high_values[i]
            >= np.max(right_highs)
        ):
            highs.append(
                {
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": float(
                        high_values[i]
                    ),
                    "kind": "H",
                }
            )

        if (
            low_values[i]
            <= np.min(left_lows)
            and
            low_values[i]
            <= np.min(right_lows)
        ):
            lows.append(
                {
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": float(
                        low_values[i]
                    ),
                    "kind": "L",
                }
            )

    DIAG["pivot_highs"] += len(highs)
    DIAG["pivot_lows"] += len(lows)

    return highs, lows


def alternating_pivots(
    highs,
    lows,
):
    pivots = highs + lows

    pivots.sort(
        key=lambda p: (
            p["index"],
            0 if p["kind"] == "H" else 1,
        )
    )

    result = []

    for pivot in pivots:

        if not result:
            result.append(pivot)
            continue

        last = result[-1]

        if pivot["kind"] == last["kind"]:

            if pivot["kind"] == "H":

                if (
                    pivot["price"]
                    >= last["price"]
                ):
                    result[-1] = pivot

            else:

                if (
                    pivot["price"]
                    <= last["price"]
                ):
                    result[-1] = pivot

        else:
            result.append(pivot)

    DIAG["alternating_pivots"] += len(result)

    return result


# ============================================================
# TP / SL
# ============================================================

def calculate_short_levels(
    start_price,
    entry,
):
    move = (
        entry -
        start_price
    )

    if move <= 0:
        return None

    tp = (
        entry -
        move * TP_RETRACE
    )

    distance_pct = (
        abs(entry - tp)
        / entry
    ) * 100.0

    sl = (
        entry +
        abs(entry - tp)
        * SL_TP_MULTIPLIER
    )

    return {
        "tp": tp,
        "sl": sl,
        "tp_distance_pct": distance_pct,
    }


def calculate_long_levels(
    start_price,
    entry,
):
    move = (
        start_price -
        entry
    )

    if move <= 0:
        return None

    tp = (
        entry +
        move * TP_RETRACE
    )

    distance_pct = (
        abs(tp - entry)
        / entry
    ) * 100.0

    sl = (
        entry -
        abs(tp - entry)
        * SL_TP_MULTIPLIER
    )

    return {
        "tp": tp,
        "sl": sl,
        "tp_distance_pct": distance_pct,
    }


# ============================================================
# HISTORICAL TP FILTER
# ============================================================

def tp_already_touched(
    df,
    confirmation_index,
    direction,
    tp,
):
    future = df.iloc[
        confirmation_index + 1:
    ]

    if future.empty:
        return False

    if direction == "SHORT":
        return bool(
            (
                future["low"]
                <= tp
            ).any()
        )

    return bool(
        (
            future["high"]
            >= tp
        ).any()
    )


# ============================================================
# HOOK ID
# ============================================================

def make_hook_id(
    symbol,
    direction,
    points,
):
    raw = (
        f"{symbol}|"
        f"{direction}|"
        +
        "|".join(
            (
                f"{p['kind']}:"
                f"{p['time']}:"
                f"{p['price']:.12f}"
            )
            for p in points
        )
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:24]


# ============================================================
# SHORT HOOK
# ============================================================

def build_short_hook(
    symbol,
    df,
    sequence,
):
    if len(sequence) != 6:
        return None

    if [
        p["kind"]
        for p in sequence
    ] != [
        "L",
        "H",
        "L",
        "H",
        "L",
        "H",
    ]:
        return None

    DIAG["short_candidates"] += 1

    start, h1, l1, h2, l2, h3 = (
        sequence
    )

    # START = LOWEST POINT
    if start["price"] != min(
        p["price"]
        for p in sequence
    ):
        DIAG["short_start_rejected"] += 1
        return None

    # H2 > H1
    if h2["price"] <= h1["price"]:
        DIAG["short_h2_rejected"] += 1
        return None

    # L2 < L1
    if l2["price"] >= l1["price"]:
        DIAG["short_l2_rejected"] += 1
        return None

    # H3 > H2
    if h3["price"] <= h2["price"]:
        DIAG["short_h3_rejected"] += 1
        return None

    DIAG["short_structure_valid"] += 1

    if start["price"] <= 0:
        return None

    range_pct = (
        (
            h3["price"]
            -
            start["price"]
        )
        /
        start["price"]
    )

    if range_pct < MIN_SWING_PCT:
        return None

    levels = calculate_short_levels(
        start["price"],
        h3["price"],
    )

    if not levels:
        return None

    tp = levels["tp"]
    sl = levels["sl"]
    tp_distance_pct = (
        levels["tp_distance_pct"]
    )

    if not (
        MIN_TP_DISTANCE_PCT
        <= tp_distance_pct
        <= MAX_TP_DISTANCE_PCT
    ):
        DIAG["tp_distance_rejected"] += 1
        return None

    DIAG["tp_valid_distance"] += 1

    if tp_already_touched(
        df,
        h3["index"],
        "SHORT",
        tp,
    ):
        DIAG["tp_already_touched"] += 1
        return None

    points = [
        start,
        h1,
        l1,
        h2,
        l2,
        h3,
    ]

    return {
        "id": make_hook_id(
            symbol,
            "SHORT",
            points,
        ),
        "symbol": symbol,
        "direction": "SHORT",
        "start_price": float(
            start["price"]
        ),
        "entry": float(
            h3["price"]
        ),
        "tp": float(tp),
        "sl": float(sl),
        "tp_distance_pct": float(
            tp_distance_pct
        ),
        "confirmation_time": (
            h3["time"].isoformat()
        ),
        "confirmation_index": (
            h3["index"]
        ),
        "points": points,
    }


# ============================================================
# LONG HOOK
# ============================================================

def build_long_hook(
    symbol,
    df,
    sequence,
):
    if len(sequence) != 6:
        return None

    if [
        p["kind"]
        for p in sequence
    ] != [
        "H",
        "L",
        "H",
        "L",
        "H",
        "L",
    ]:
        return None

    DIAG["long_candidates"] += 1

    start, l1, h1, l2, h2, l3 = (
        sequence
    )

    # START = HIGHEST POINT
    if start["price"] != max(
        p["price"]
        for p in sequence
    ):
        DIAG["long_start_rejected"] += 1
        return None

    # L2 < L1
    if l2["price"] >= l1["price"]:
        DIAG["long_l2_rejected"] += 1
        return None

    # H2 > H1
    if h2["price"] <= h1["price"]:
        DIAG["long_h2_rejected"] += 1
        return None

    # L3 < L2
    if l3["price"] >= l2["price"]:
        DIAG["long_l3_rejected"] += 1
        return None

    DIAG["long_structure_valid"] += 1

    if l3["price"] <= 0:
        return None

    range_pct = (
        (
            start["price"]
            -
            l3["price"]
        )
        /
        l3["price"]
    )

    if range_pct < MIN_SWING_PCT:
        return None

    levels = calculate_long_levels(
        start["price"],
        l3["price"],
    )

    if not levels:
        return None

    tp = levels["tp"]
    sl = levels["sl"]
    tp_distance_pct = (
        levels["tp_distance_pct"]
    )

    if not (
        MIN_TP_DISTANCE_PCT
        <= tp_distance_pct
        <= MAX_TP_DISTANCE_PCT
    ):
        DIAG["tp_distance_rejected"] += 1
        return None

    DIAG["tp_valid_distance"] += 1

    if tp_already_touched(
        df,
        l3["index"],
        "LONG",
        tp,
    ):
        DIAG["tp_already_touched"] += 1
        return None

    points = [
        start,
        l1,
        h1,
        l2,
        h2,
        l3,
    ]

    return {
        "id": make_hook_id(
            symbol,
            "LONG",
            points,
        ),
        "symbol": symbol,
        "direction": "LONG",
        "start_price": float(
            start["price"]
        ),
        "entry": float(
            l3["price"]
        ),
        "tp": float(tp),
        "sl": float(sl),
        "tp_distance_pct": float(
            tp_distance_pct
        ),
        "confirmation_time": (
            l3["time"].isoformat()
        ),
        "confirmation_index": (
            l3["index"]
        ),
        "points": points,
    }


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks(
    symbol,
    df,
):
    highs, lows = detect_pivots(df)

    pivots = alternating_pivots(
        highs,
        lows,
    )

    DIAG["sequences"] += max(
        0,
        len(pivots) - 5,
    )

    candidates = []

    for i in range(
        len(pivots) - 5
    ):
        sequence = pivots[
            i:i + 6
        ]

        short_hook = build_short_hook(
            symbol,
            df,
            sequence,
        )

        if short_hook:
            candidates.append(
                short_hook
            )

        long_hook = build_long_hook(
            symbol,
            df,
            sequence,
        )

        if long_hook:
            candidates.append(
                long_hook
            )

    if not candidates:
        return []

    candidates.sort(
        key=lambda x: (
            x["confirmation_time"]
        ),
        reverse=True,
    )

    return [
        candidates[0]
    ]


# ============================================================
# CHART
# ============================================================

def make_chart(
    df,
    hook,
    current_price,
):
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    chart_df = df.tail(
        180
    ).copy()

    fig, ax = plt.subplots(
        figsize=(16, 9)
    )

    x_values = mdates.date2num(
        chart_df["time"]
        .dt
        .to_pydatetime()
    )

    candle_width = 0.018

    for x, row in zip(
        x_values,
        chart_df.itertuples(),
    ):
        o = float(row.open)
        h = float(row.high)
        l = float(row.low)
        c = float(row.close)

        ax.plot(
            [x, x],
            [l, h],
            linewidth=1,
        )

        bottom = min(o, c)
        height = abs(c - o)

        if height == 0:
            height = max(
                abs(h - l) * 0.002,
                1e-12,
            )

        rect = plt.Rectangle(
            (
                x - candle_width / 2,
                bottom,
            ),
            candle_width,
            height,
            fill=False,
            linewidth=1,
        )

        ax.add_patch(rect)

    points = hook["points"]

    px = [
        mdates.date2num(
            pd.to_datetime(
                p["time"],
                utc=True,
            ).to_pydatetime()
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
        linewidth=2,
    )

    if hook["direction"] == "SHORT":
        labels = [
            "START",
            "H1",
            "L1",
            "H2",
            "L2",
            "H3 CONFIRMED",
        ]

        offsets = [
            (0, -28),
            (0, 22),
            (0, -28),
            (0, 22),
            (0, -28),
            (0, 24),
        ]

    else:
        labels = [
            "START",
            "L1",
            "H1",
            "L2",
            "H2",
            "L3 CONFIRMED",
        ]

        offsets = [
            (0, 28),
            (0, -28),
            (0, 22),
            (0, -28),
            (0, 22),
            (0, -28),
        ]

    for (
        x,
        y,
        label,
        offset,
    ) in zip(
        px,
        py,
        labels,
        offsets,
    ):
        ax.scatter(
            [x],
            [y],
            s=70,
            zorder=5,
        )

        ax.annotate(
            (
                f"{label}\n"
                f"{fmt_price(y)}"
            ),
            (x, y),
            xytext=offset,
            textcoords="offset points",
            ha="center",
            va="center",
            fontsize=9,
            bbox={
                "boxstyle":
                    "round,pad=0.3",
                "alpha": 0.8,
            },
        )

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

    tp_pct = abs(
        pct_change(
            entry,
            tp,
            hook["direction"],
        )
    )

    sl_pct = abs(
        pct_change(
            entry,
            sl,
            hook["direction"],
        )
    )

    current_pct = pct_change(
        entry,
        current_price,
        hook["direction"],
    )

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.2,
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.5,
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.5,
    )

    ax.axhline(
        current_price,
        linestyle=":",
        linewidth=1.2,
    )

    ax.text(
        1.005,
        entry,
        f"ENTRY {fmt_price(entry)}",
        transform=ax.get_yaxis_transform(),
        va="center",
        fontsize=9,
    )

    ax.text(
        1.005,
        tp,
        (
            f"TP 86.4% "
            f"{fmt_price(tp)} "
            f"({tp_pct:+.2f}%)"
        ),
        transform=ax.get_yaxis_transform(),
        va="center",
        fontsize=9,
    )

    ax.text(
        1.005,
        sl,
        (
            f"SL "
            f"{fmt_price(sl)} "
            f"({-sl_pct:+.2f}%)"
        ),
        transform=ax.get_yaxis_transform(),
        va="center",
        fontsize=9,
    )

    ax.text(
        1.005,
        current_price,
        (
            f"CURRENT "
            f"{fmt_price(current_price)} "
            f"({current_pct:+.2f}%)"
        ),
        transform=ax.get_yaxis_transform(),
        va="center",
        fontsize=9,
    )

    ax.axvline(
        px[-1],
        linestyle=":",
        linewidth=1,
    )

    ax.set_title(
        (
            f"NDS M30 | "
            f"{hook['symbol']} | "
            f"{hook['direction']} | "
            f"TP 86.4% | "
            f"TP Distance "
            f"{hook['tp_distance_pct']:.2f}% | "
            f"SL = 50%"
        )
    )

    ax.set_xlabel(
        "Time UTC"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        True,
        alpha=0.20,
    )

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M"
        )
    )

    fig.autofmt_xdate()

    plt.tight_layout()

    filename = (
        f"{hook['symbol']}_"
        f"{hook['direction']}_"
        f"{hook['id']}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename,
    )

    fig.savefig(
        path,
        dpi=140,
        bbox_inches="tight",
    )

    plt.close(fig)

    DIAG["charts"] += 1

    return path


# ============================================================
# SIGNAL
# ============================================================

def send_signal_message(
    hook,
    current_price,
):
    emoji = (
        "🔴"
        if hook["direction"] == "SHORT"
        else "🟢"
    )

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

    tp_pct = abs(
        pct_change(
            entry,
            tp,
            hook["direction"],
        )
    )

    sl_pct = abs(
        pct_change(
            entry,
            sl,
            hook["direction"],
        )
    )

    current_pct = pct_change(
        entry,
        current_price,
        hook["direction"],
    )

    telegram_send(
        (
            f"{emoji} <b>NDS "
            f"{hook['direction']}</b>\n"
            f"<b>{hook['symbol']}</b>\n\n"
            f"Entry: "
            f"<b>{fmt_price(entry)}</b>\n"
            f"TP 86.4%: "
            f"<b>{fmt_price(tp)}</b> "
            f"({tp_pct:+.2f}%)\n"
            f"SL: "
            f"<b>{fmt_price(sl)}</b> "
            f"({-sl_pct:+.2f}%)\n"
            f"Current: "
            f"<b>{fmt_price(current_price)}</b> "
            f"({current_pct:+.2f}%)\n\n"
            f"TP distance: "
            f"{hook['tp_distance_pct']:.2f}%\n"
            f"SL = 50% TP distance\n"
            f"M30 ONLY | PAPER"
        )
    )


# ============================================================
# OPEN TRADE MESSAGE
# ============================================================

def send_trade_open_message(
    hook,
    trade_id,
):
    emoji = (
        "🔴"
        if hook["direction"] == "SHORT"
        else "🟢"
    )

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

    tp_pct = abs(
        pct_change(
            entry,
            tp,
            hook["direction"],
        )
    )

    sl_pct = abs(
        pct_change(
            entry,
            sl,
            hook["direction"],
        )
    )

    telegram_send(
        (
            f"{emoji} "
            f"<b>PAPER TRADE OPENED</b>\n"
            f"{hook['symbol']} "
            f"{hook['direction']}\n\n"
            f"Entry: "
            f"<b>{fmt_price(entry)}</b>\n"
            f"TP: "
            f"<b>{fmt_price(tp)}</b> "
            f"({tp_pct:+.2f}%)\n"
            f"SL: "
            f"<b>{fmt_price(sl)}</b> "
            f"({-sl_pct:+.2f}%)\n\n"
            f"Paper trade #{trade_id}"
        )
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def send_trade_close_message(
    trade,
    reason,
    exit_price,
    pnl_pct,
):
    if reason == "TP":
        emoji = "✅"
        title = "TP HIT"
    else:
        emoji = "❌"
        title = "SL HIT"

    telegram_send(
        (
            f"{emoji} <b>{title}</b>\n"
            f"{trade['symbol']} "
            f"{trade['direction']}\n\n"
            f"Entry: "
            f"{fmt_price(trade['entry'])}\n"
            f"Exit: "
            f"<b>{fmt_price(exit_price)}</b>\n"
            f"TP: "
            f"{fmt_price(trade['tp'])}\n"
            f"SL: "
            f"{fmt_price(trade['sl'])}\n"
            f"PnL: "
            f"<b>{pnl_pct:+.2f}%</b>\n\n"
            f"Paper trade #{trade['id']}"
        )
    )


# ============================================================
# FIXED OPEN TRADE ENGINE
# ============================================================

def update_open_trades(
    market_data,
):
    """
    IMPORTANT:

    market_data[symbol] contains:

        close
        high
        low

    For SHORT:

        TP if low <= TP
        SL if high >= SL

    For LONG:

        TP if high >= TP
        SL if low <= SL

    When crossed:

        exit_price = EXACT TP or SL

    Never use a later/current price as the
    execution price.

    If both TP and SL are touched inside the
    same unseen candle, exact intrabar order
    is unknown. Current conservative implementation
    keeps TP priority, matching the previous logic.
    """

    trades = get_open_trades()

    if not trades:
        DIAG["open_trades"] = 0
        return

    conn = db_connect()

    for trade in trades:

        symbol = trade["symbol"]

        market = market_data.get(
            symbol
        )

        if not market:
            continue

        current = float(
            market["close"]
        )

        high = float(
            market["high"]
        )

        low = float(
            market["low"]
        )

        direction = trade["direction"]

        entry = float(
            trade["entry"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        current_pct = pct_change(
            entry,
            current,
            direction,
        )

        reason = None
        exit_price = None

        # ====================================================
        # SHORT
        # ====================================================

        if direction == "SHORT":

            # TP crossed
            if low <= tp:

                reason = "TP"

                # EXACT TP
                exit_price = tp

            # SL crossed
            elif high >= sl:

                reason = "SL"

                # EXACT SL
                exit_price = sl

        # ====================================================
        # LONG
        # ====================================================

        else:

            # TP crossed
            if high >= tp:

                reason = "TP"

                # EXACT TP
                exit_price = tp

            # SL crossed
            elif low <= sl:

                reason = "SL"

                # EXACT SL
                exit_price = sl

        # ====================================================
        # STILL OPEN
        # ====================================================

        if reason is None:

            conn.execute(
                """
                UPDATE paper_trades
                SET current_price = ?,
                    current_pct = ?,
                    last_update = ?
                WHERE id = ?
                  AND status = 'OPEN'
                """,
                (
                    current,
                    current_pct,
                    now_iso(),
                    trade["id"],
                ),
            )

            continue

        # ====================================================
        # CLOSED
        # ====================================================

        pnl_pct = pct_change(
            entry,
            exit_price,
            direction,
        )

        conn.execute(
            """
            UPDATE paper_trades
            SET current_price = ?,
                current_pct = ?,
                status = 'CLOSED',
                exit_price = ?,
                exit_time = ?,
                pnl_pct = ?,
                last_update = ?
            WHERE id = ?
              AND status = 'OPEN'
            """,
            (
                current,
                current_pct,
                exit_price,
                now_iso(),
                pnl_pct,
                now_iso(),
                trade["id"],
            ),
        )

        if reason == "TP":
            DIAG["tp_hits"] += 1
        else:
            DIAG["sl_hits"] += 1

        closed_trade = dict(trade)

        send_trade_close_message(
            closed_trade,
            reason,
            exit_price,
            pnl_pct,
        )

        print(
            f"CLOSED {symbol} "
            f"{reason} "
            f"EXIT={fmt_price(exit_price)} "
            f"PNL={pnl_pct:+.2f}%"
        )

    conn.commit()
    conn.close()

    DIAG["open_trades"] = len(
        get_open_trades()
    )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def send_open_trades_report(
    market_data,
):
    trades = get_open_trades()

    DIAG["open_trades"] = len(
        trades
    )

    stats = performance_stats()

    lines = [
        "📊 <b>OPEN TRADES</b>",
        "",
    ]

    if not trades:
        lines.append(
            "No open paper trades."
        )
        lines.append("")

    live_total = 0.0

    for trade in trades:

        symbol = trade["symbol"]
        direction = trade["direction"]

        market = market_data.get(
            symbol
        )

        if market:
            current = float(
                market["close"]
            )
        else:
            current = float(
                trade["current_price"]
            )

        current_pct = pct_change(
            trade["entry"],
            current,
            direction,
        )

        live_total += current_pct

        tp_pct = abs(
            pct_change(
                trade["entry"],
                trade["tp"],
                direction,
            )
        )

        sl_pct = abs(
            pct_change(
                trade["entry"],
                trade["sl"],
                direction,
            )
        )

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} <b>{symbol}</b> "
            f"{direction}"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(trade['entry'])}"
        )

        lines.append(
            f"Current: "
            f"{fmt_price(current)} "
            f"({current_pct:+.2f}%)"
        )

        lines.append(
            f"TP: "
            f"{fmt_price(trade['tp'])} "
            f"({tp_pct:+.2f}%)"
        )

        lines.append(
            f"SL: "
            f"{fmt_price(trade['sl'])} "
            f"({-sl_pct:+.2f}%)"
        )

        lines.append("")

    # ========================================================
    # PERFORMANCE
    # ========================================================

    realized = stats["realized_pnl"]

    total = (
        realized +
        live_total
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📈 <b>PERFORMANCE</b>"
    )

    lines.append(
        f"Closed Trades: "
        f"<b>{stats['closed']}</b>"
    )

    lines.append(
        f"Winning Trades: "
        f"<b>{stats['wins']}</b>"
    )

    lines.append(
        f"Losing Trades: "
        f"<b>{stats['losses']}</b>"
    )

    lines.append(
        f"Win Rate: "
        f"<b>{stats['win_rate']:.2f}%</b>"
    )

    lines.append(
        f"Realized PnL: "
        f"<b>{realized:+.2f}%</b>"
    )

    lines.append(
        f"Open / Live PnL: "
        f"<b>{live_total:+.2f}%</b>"
    )

    lines.append(
        f"Total PnL: "
        f"<b>{total:+.2f}%</b>"
    )

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# DIAGNOSTIC
# ============================================================

def diagnostic_text():
    stats = performance_stats()

    return (
        "🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {now_iso()}\n\n"

        "<b>M30</b>\n"
        f"Requests: "
        f"{DIAG['requests']}\n"
        f"Data OK: "
        f"{DIAG['data_ok']}\n"
        f"Empty: "
        f"{DIAG['empty']}\n"
        f"Short data: "
        f"{DIAG['short_data']}\n"
        f"Errors: "
        f"{DIAG['errors']}\n\n"

        "<b>PIVOTS</b>\n"
        f"Pivot Highs: "
        f"{DIAG['pivot_highs']}\n"
        f"Pivot Lows: "
        f"{DIAG['pivot_lows']}\n"
        f"Alternating Pivots: "
        f"{DIAG['alternating_pivots']}\n"
        f"6-point Sequences: "
        f"{DIAG['sequences']}\n\n"

        "<b>POSITIVE / SHORT</b>\n"
        f"Candidates: "
        f"{DIAG['short_candidates']}\n"
        f"Structure Valid: "
        f"{DIAG['short_structure_valid']}\n"
        f"START rejected: "
        f"{DIAG['short_start_rejected']}\n"
        f"H2 rejected: "
        f"{DIAG['short_h2_rejected']}\n"
        f"L2 rejected: "
        f"{DIAG['short_l2_rejected']}\n"
        f"H3 rejected: "
        f"{DIAG['short_h3_rejected']}\n\n"

        "<b>NEGATIVE / LONG</b>\n"
        f"Candidates: "
        f"{DIAG['long_candidates']}\n"
        f"Structure Valid: "
        f"{DIAG['long_structure_valid']}\n"
        f"START rejected: "
        f"{DIAG['long_start_rejected']}\n"
        f"L2 rejected: "
        f"{DIAG['long_l2_rejected']}\n"
        f"H2 rejected: "
        f"{DIAG['long_h2_rejected']}\n"
        f"L3 rejected: "
        f"{DIAG['long_l3_rejected']}\n\n"

        "<b>TP FILTER</b>\n"
        f"Valid distance: "
        f"{DIAG['tp_valid_distance']}\n"
        f"Distance rejected: "
        f"{DIAG['tp_distance_rejected']}\n"
        f"TP already touched: "
        f"{DIAG['tp_already_touched']}\n\n"

        "<b>TRADES</b>\n"
        f"Confirmed Hooks: "
        f"{DIAG['confirmed_hooks']}\n"
        f"New Signals: "
        f"{DIAG['new_signals']}\n"
        f"Charts: "
        f"{DIAG['charts']}\n"
        f"Opened Trades: "
        f"{DIAG['opened_trades']}\n"
        f"Open Trades: "
        f"{stats['open_count']}\n"
        f"TP Hits: "
        f"{DIAG['tp_hits']}\n"
        f"SL Hits: "
        f"{DIAG['sl_hits']}\n\n"

        "<b>PERFORMANCE</b>\n"
        f"Closed Trades: "
        f"{stats['closed']}\n"
        f"Win Rate: "
        f"{stats['win_rate']:.2f}%\n"
        f"Realized PnL: "
        f"{stats['realized_pnl']:+.2f}%\n"
        f"Open / Live PnL: "
        f"{stats['live_pnl']:+.2f}%\n"
        f"Total PnL: "
        f"{stats['total_pnl']:+.2f}%\n\n"

        "<b>TP = 86.4%</b>\n"
        f"TP distance: "
        f"{MIN_TP_DISTANCE_PCT:.2f}% "
        f"→ "
        f"{MAX_TP_DISTANCE_PCT:.2f}%\n"
        f"SL = 50% TP distance\n\n"

        "<b>M30 ONLY</b>\n"
        "NO M1 / NO 123F\n"
        "PAPER ONLY"
    )


# ============================================================
# SCAN
# ============================================================

def scan_once():
    reset_diag()

    market_data = {}
    new_hooks = []

    print()
    print("=" * 70)
    print(
        f"NDS M30 SCAN | VERSION {VERSION}"
    )
    print(now_iso())
    print("=" * 70)

    # --------------------------------------------------------
    # GET MARKET DATA
    # --------------------------------------------------------

    for symbol in ASSETS:

        df = fetch_m30(symbol)

        if df is None:
            continue

        last = df.iloc[-1]

        market_data[symbol] = {
            "close": float(
                last["close"]
            ),
            "high": float(
                last["high"]
            ),
            "low": float(
                last["low"]
            ),
        }

        hooks = detect_hooks(
            symbol,
            df,
        )

        if not hooks:
            continue

        hook = hooks[0]

        DIAG["confirmed_hooks"] += 1

        if hook_exists(
            hook["id"]
        ):
            continue

        save_hook(hook)

        DIAG["new_signals"] += 1

        new_hooks.append(
            (
                hook,
                float(last["close"]),
                df,
            )
        )

    # --------------------------------------------------------
    # FIRST CLOSE EXISTING TRADES
    # --------------------------------------------------------
    #
    # THIS MUST HAPPEN BEFORE THE OPEN-TRADES REPORT.
    #
    # Therefore an SL/TP crossed trade cannot remain
    # inside OPEN TRADES after this scan.
    # --------------------------------------------------------

    update_open_trades(
        market_data
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    for (
        hook,
        current_price,
        df,
    ) in new_hooks:

        send_signal_message(
            hook,
            current_price,
        )

        chart_path = make_chart(
            df,
            hook,
            current_price,
        )

        if chart_path:

            caption = (
                f"NDS "
                f"{hook['direction']} | "
                f"{hook['symbol']}\n"
                f"Entry: "
                f"{fmt_price(hook['entry'])}\n"
                f"TP 86.4%: "
                f"{fmt_price(hook['tp'])}\n"
                f"SL: "
                f"{fmt_price(hook['sl'])}\n"
                f"TP Distance: "
                f"{hook['tp_distance_pct']:.2f}%\n"
                f"M30 ONLY | PAPER"
            )

            telegram_send_photo(
                chart_path,
                caption,
            )

        # Avoid duplicate open trade
        if has_open_trade(
            hook["symbol"]
        ):
            continue

        trade_id = create_paper_trade(
            hook,
            current_price,
        )

        DIAG["opened_trades"] += 1

        send_trade_open_message(
            hook,
            trade_id,
        )

    # --------------------------------------------------------
    # OPEN TRADES REPORT
    # --------------------------------------------------------

    send_open_trades_report(
        market_data
    )

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    report = diagnostic_text()

    print()
    print(
        report
        .replace(
            "<b>",
            "",
        )
        .replace(
            "</b>",
            "",
        )
    )

    telegram_send(
        report
    )


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    print()
    print("=" * 70)
    print(
        f"NDS M30 LIVE SCANNER "
        f"VERSION {VERSION}"
    )
    print("=" * 70)
    print("PAPER ONLY")
    print("M30 ONLY")
    print("NO M1")
    print("NO 123F")
    print()
    print(
        "ENTRY = H3 / L3"
    )
    print(
        "TP = 86.4%"
    )
    print(
        "SL = 50% TP DISTANCE"
    )
    print()
    print(
        "EXIT CHECK = HIGH / LOW / CLOSE"
    )
    print(
        "EXIT PRICE = EXACT TP / SL"
    )
    print()
    print(
        "WIN RATE + REALIZED + LIVE + TOTAL PNL"
    )
    print("=" * 70)

    last_scan = 0

    while True:

        try:
            current_time = time.time()

            if (
                current_time - last_scan
                >= SCAN_SECONDS
            ):
                scan_once()
                last_scan = current_time

            time.sleep(5)

        except KeyboardInterrupt:
            print(
                "Scanner stopped."
            )
            break

        except Exception as exc:
            print(
                "MAIN LOOP ERROR:",
                repr(exc),
            )

            time.sleep(10)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
