# ============================================================
# NDS M30 LIVE SCANNER
# VERSION 4.6.1
# ============================================================
#
# PAPER ONLY
#
# M30 ONLY
# NO M1
# NO 123F
#
# POSITIVE / SHORT:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   ENTRY = H3
#   TP    = 86.4% retracement from START to H3
#   SL    = 50% of TP distance
#
# NEGATIVE / LONG:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   ENTRY = L3
#   TP    = 86.4% retracement from START to L3
#   SL    = 50% of TP distance
#
# IMPORTANT:
#   86.4% IS TP.
#   ENTRY IS H3/L3.
#
# VERSION 4.6.1 FIX:
#   If price crosses TP/SL between two scans,
#   EXIT PRICE = EXACT TP/SL LEVEL
#   PNL = CALCULATED FROM EXACT TP/SL LEVEL
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

VERSION = "4.6.1"

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
MAX_CHARTS = 10

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
# GLOBAL DIAGNOSTICS
# ============================================================

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


# ============================================================
# UTILITIES
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def pct_change(entry, price, direction):
    if entry == 0:
        return 0.0

    if direction == "LONG":
        return ((price - entry) / entry) * 100.0

    return ((entry - price) / entry) * 100.0


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


def fmt_pct(value):
    return f"{float(value):+.2f}%"


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def hook_id(symbol, direction, points):
    raw = (
        f"{symbol}|{direction}|"
        + "|".join(
            f"{p['kind']}:{p['time']}:{p['price']:.12f}"
            for p in points
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


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


def hook_exists(hid):
    conn = db_connect()
    row = conn.execute(
        "SELECT id FROM hooks WHERE id = ?",
        (hid,)
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

    tp_pct = abs(pct_change(entry, tp, direction))
    sl_pct = abs(pct_change(entry, sl, direction))

    current_pct = pct_change(
        entry,
        current_price,
        direction
    )

    cur = conn.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN',
                NULL, NULL, NULL, ?)
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

    trade_id = cur.lastrowid

    conn.commit()
    conn.close()

    return trade_id


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send(text):
    if not telegram_enabled():
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=REQUEST_TIMEOUT,
        )

        return r.ok

    except Exception as e:
        print("Telegram text error:", e)
        return False


def telegram_send_photo(photo_path, caption=""):
    if not telegram_enabled():
        return False

    if not os.path.exists(photo_path):
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(photo_path, "rb") as photo:
            r = requests.post(
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

        return r.ok

    except Exception as e:
        print("Telegram photo error:", e)
        return False


# ============================================================
# KRAKEN M30 DATA
# ============================================================

def fetch_m30(symbol):
    DIAG["requests"] += 1

    url = f"{BASE_URL}/{symbol}/{INTERVAL}"

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

        candles = payload.get("candles", [])

        if not candles:
            DIAG["empty"] += 1
            return None

        rows = []

        for c in candles:
            try:
                ts = c.get("time")

                if ts is None:
                    continue

                ts = float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000.0

                rows.append(
                    {
                        "time": pd.to_datetime(
                            ts,
                            unit="s",
                            utc=True,
                        ),
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(
                            c.get("volume", 0)
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
            df.drop_duplicates("time")
            .sort_values("time")
            .tail(M30_COUNT)
            .reset_index(drop=True)
        )

        DIAG["data_ok"] += 1

        return df

    except Exception as e:
        DIAG["errors"] += 1
        print(f"{symbol} M30 error:", e)
        return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):
    highs = []
    lows = []

    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)

    n = len(df)

    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        left_h = h[i - PIVOT_LEFT:i]
        right_h = h[i + 1:i + 1 + PIVOT_RIGHT]

        left_l = l[i - PIVOT_LEFT:i]
        right_l = l[i + 1:i + 1 + PIVOT_RIGHT]

        if h[i] >= np.max(left_h) and h[i] >= np.max(right_h):
            highs.append(
                {
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": float(h[i]),
                    "kind": "H",
                }
            )

        if l[i] <= np.min(left_l) and l[i] <= np.min(right_l):
            lows.append(
                {
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": float(l[i]),
                    "kind": "L",
                }
            )

    DIAG["pivot_highs"] += len(highs)
    DIAG["pivot_lows"] += len(lows)

    return highs, lows


def alternating_pivots(highs, lows):
    all_pivots = highs + lows

    all_pivots.sort(
        key=lambda x: (
            x["index"],
            0 if x["kind"] == "H" else 1,
        )
    )

    result = []

    for p in all_pivots:
        if not result:
            result.append(p)
            continue

        last = result[-1]

        if p["kind"] == last["kind"]:
            if p["kind"] == "H":
                if p["price"] >= last["price"]:
                    result[-1] = p
            else:
                if p["price"] <= last["price"]:
                    result[-1] = p

        else:
            result.append(p)

    DIAG["alternating_pivots"] += len(result)

    return result


# ============================================================
# TP CALCULATION
# ============================================================

def calculate_short_levels(start_price, entry):
    move = entry - start_price

    if move <= 0:
        return None

    tp = entry - (move * TP_RETRACE)

    tp_distance_pct = (
        abs(entry - tp) / entry
    ) * 100.0

    sl_distance = abs(entry - tp) * SL_TP_MULTIPLIER

    sl = entry + sl_distance

    return {
        "tp": tp,
        "sl": sl,
        "tp_distance_pct": tp_distance_pct,
    }


def calculate_long_levels(start_price, entry):
    move = start_price - entry

    if move <= 0:
        return None

    tp = entry + (move * TP_RETRACE)

    tp_distance_pct = (
        abs(tp - entry) / entry
    ) * 100.0

    sl_distance = abs(entry - tp) * SL_TP_MULTIPLIER

    sl = entry - sl_distance

    return {
        "tp": tp,
        "sl": sl,
        "tp_distance_pct": tp_distance_pct,
    }


# ============================================================
# HISTORICAL TP TOUCH FILTER
# ============================================================

def tp_already_touched(df, confirmation_index, direction, tp):
    future = df.iloc[
        confirmation_index + 1:
    ]

    if future.empty:
        return False

    if direction == "SHORT":
        return bool(
            (future["low"] <= tp).any()
        )

    return bool(
        (future["high"] >= tp).any()
    )


# ============================================================
# HOOK DETECTION
# ============================================================

def build_short_hook(df, seq):
    if len(seq) != 6:
        return None

    if [p["kind"] for p in seq] != [
        "L", "H", "L", "H", "L", "H"
    ]:
        return None

    DIAG["short_candidates"] += 1

    start, h1, l1, h2, l2, h3 = seq

    # START MUST BE THE LOWEST POINT
    # OF THE WHOLE SIX-POINT HOOK
    min_price = min(p["price"] for p in seq)

    if abs(start["price"] - min_price) > 1e-12:
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

    total_range = (
        h3["price"] - start["price"]
    )

    if start["price"] <= 0:
        return None

    range_pct = (
        total_range / start["price"]
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
    tp_distance_pct = levels["tp_distance_pct"]

    if (
        tp_distance_pct < MIN_TP_DISTANCE_PCT
        or tp_distance_pct > MAX_TP_DISTANCE_PCT
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

    hid = hook_id(
        seq[0].get("symbol", ""),
        "SHORT",
        points,
    )

    return {
        "id": hid,
        "symbol": "",
        "direction": "SHORT",
        "start_price": float(start["price"]),
        "entry": float(h3["price"]),
        "tp": float(tp),
        "sl": float(sl),
        "tp_distance_pct": float(tp_distance_pct),
        "confirmation_time": h3["time"].isoformat(),
        "confirmation_index": h3["index"],
        "points": points,
    }


def build_long_hook(df, seq):
    if len(seq) != 6:
        return None

    if [p["kind"] for p in seq] != [
        "H", "L", "H", "L", "H", "L"
    ]:
        return None

    DIAG["long_candidates"] += 1

    start, l1, h1, l2, h2, l3 = seq

    # START MUST BE THE HIGHEST POINT
    # OF THE WHOLE SIX-POINT HOOK
    max_price = max(p["price"] for p in seq)

    if abs(start["price"] - max_price) > 1e-12:
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

    total_range = (
        start["price"] - l3["price"]
    )

    if l3["price"] <= 0:
        return None

    range_pct = (
        total_range / l3["price"]
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
    tp_distance_pct = levels["tp_distance_pct"]

    if (
        tp_distance_pct < MIN_TP_DISTANCE_PCT
        or tp_distance_pct > MAX_TP_DISTANCE_PCT
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

    hid = hook_id(
        seq[0].get("symbol", ""),
        "LONG",
        points,
    )

    return {
        "id": hid,
        "symbol": "",
        "direction": "LONG",
        "start_price": float(start["price"]),
        "entry": float(l3["price"]),
        "tp": float(tp),
        "sl": float(sl),
        "tp_distance_pct": float(tp_distance_pct),
        "confirmation_time": l3["time"].isoformat(),
        "confirmation_index": l3["index"],
        "points": points,
    }


def detect_hooks(symbol, df):
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

    for i in range(len(pivots) - 5):
        seq = pivots[i:i + 6]

        short_hook = build_short_hook(
            df,
            seq,
        )

        if short_hook:
            short_hook["symbol"] = symbol

            short_hook["id"] = hook_id(
                symbol,
                "SHORT",
                short_hook["points"],
            )

            candidates.append(short_hook)

        long_hook = build_long_hook(
            df,
            seq,
        )

        if long_hook:
            long_hook["symbol"] = symbol

            long_hook["id"] = hook_id(
                symbol,
                "LONG",
                long_hook["points"],
            )

            candidates.append(long_hook)

    if not candidates:
        return []

    # Most recent confirmations first
    candidates.sort(
        key=lambda x: x["confirmation_time"],
        reverse=True,
    )

    # Keep only the latest confirmed Hook
    return [candidates[0]]


# ============================================================
# CHART
# ============================================================

def make_chart(df, hook, current_price):
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    symbol = hook["symbol"]
    direction = hook["direction"]

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    fig, ax = plt.subplots(
        figsize=(16, 9)
    )

    x = mdates.date2num(
        chart_df["time"].dt.to_pydatetime()
    )

    candle_width = 0.018

    for xi, row in zip(
        x,
        chart_df.itertuples()
    ):
        o = float(row.open)
        h = float(row.high)
        l = float(row.low)
        c = float(row.close)

        ax.plot(
            [xi, xi],
            [l, h],
            linewidth=1.0,
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
                xi - candle_width / 2,
                bottom,
            ),
            candle_width,
            height,
            fill=False,
            linewidth=1.0,
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
        linewidth=2.0,
    )

    # --------------------------------------------------------
    # Hook points
    # --------------------------------------------------------

    if direction == "SHORT":
        offsets = [
            (-8, -25),
            (0, 18),
            (0, -25),
            (0, 18),
            (0, -25),
            (0, 20),
        ]

        labels = [
            "START",
            "H1",
            "L1",
            "H2",
            "L2",
            "H3 CONFIRMED",
        ]

    else:
        offsets = [
            (-8, 25),
            (0, -25),
            (0, 20),
            (0, -25),
            (0, 20),
            (0, -25),
        ]

        labels = [
            "START",
            "L1",
            "H1",
            "L2",
            "H2",
            "L3 CONFIRMED",
        ]

    for i, (
        xi,
        yi,
        label,
        offset,
    ) in enumerate(
        zip(
            px,
            py,
            labels,
            offsets,
        )
    ):
        ax.scatter(
            [xi],
            [yi],
            s=65,
            zorder=5,
        )

        ax.annotate(
            (
                f"{label}\n"
                f"{fmt_price(yi)}"
            ),
            (
                xi,
                yi,
            ),
            xytext=offset,
            textcoords="offset points",
            ha="center",
            va="center",
            fontsize=9,
            bbox={
                "boxstyle": "round,pad=0.3",
                "alpha": 0.80,
            },
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = hook["entry"]

    ax.axhline(
        entry,
        linestyle="--",
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

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    tp = hook["tp"]

    tp_pct = abs(
        pct_change(
            entry,
            tp,
            direction,
        )
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.5,
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

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = hook["sl"]

    sl_pct = abs(
        pct_change(
            entry,
            sl,
            direction,
        )
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.5,
    )

    ax.text(
        1.005,
        sl,
        (
            f"SL {fmt_price(sl)} "
            f"({-sl_pct:+.2f}%)"
        ),
        transform=ax.get_yaxis_transform(),
        va="center",
        fontsize=9,
    )

    # --------------------------------------------------------
    # Current
    # --------------------------------------------------------

    current_pct = pct_change(
        entry,
        current_price,
        direction,
    )

    ax.axhline(
        current_price,
        linestyle=":",
        linewidth=1.2,
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

    # --------------------------------------------------------
    # Confirmation line
    # --------------------------------------------------------

    confirmation_x = px[-1]

    ax.axvline(
        confirmation_x,
        linestyle=":",
        linewidth=1.0,
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    ax.set_title(
        (
            f"NDS M30 | {symbol} | "
            f"{direction} | "
            f"TP 86.4% | "
            f"TP Distance {hook['tp_distance_pct']:.2f}% | "
            f"SL = 50% TP Distance"
        ),
        fontsize=13,
    )

    ax.set_xlabel("Time UTC")
    ax.set_ylabel("Price")

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
        f"{symbol}_"
        f"{direction}_"
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
# SIGNAL MESSAGE
# ============================================================

def send_signal_message(
    hook,
    current_price,
):
    direction = hook["direction"]

    if direction == "SHORT":
        emoji = "🔴"
    else:
        emoji = "🟢"

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

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

    text = (
        f"{emoji} <b>NDS {direction}</b>\n"
        f"<b>{hook['symbol']}</b>\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"TP 86.4%: <b>{fmt_price(tp)}</b> "
        f"({tp_pct:+.2f}%)\n"
        f"SL: <b>{fmt_price(sl)}</b> "
        f"({-sl_pct:+.2f}%)\n"
        f"Current: <b>{fmt_price(current_price)}</b> "
        f"({current_pct:+.2f}%)\n\n"
        f"TP distance: {hook['tp_distance_pct']:.2f}%\n"
        f"SL = 50% TP distance\n"
        f"M30 ONLY | PAPER"
    )

    telegram_send(text)


# ============================================================
# TRADE OPEN MESSAGE
# ============================================================

def send_trade_open_message(
    hook,
    trade_id,
):
    direction = hook["direction"]

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

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

    text = (
        f"{emoji} <b>PAPER TRADE OPENED</b>\n"
        f"{hook['symbol']} {direction}\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"TP: <b>{fmt_price(tp)}</b> "
        f"({tp_pct:+.2f}%)\n"
        f"SL: <b>{fmt_price(sl)}</b> "
        f"({-sl_pct:+.2f}%)\n\n"
        f"Paper trade #{trade_id}"
    )

    telegram_send(text)


# ============================================================
# TRADE CLOSE MESSAGE
# ============================================================

def send_trade_close_message(
    trade,
    exit_reason,
    exit_price,
    pnl_pct,
):
    if exit_reason == "TP":
        emoji = "✅"
        title = "TP HIT"
    else:
        emoji = "❌"
        title = "SL HIT"

    text = (
        f"{emoji} <b>{title}</b>\n"
        f"{trade['symbol']} "
        f"{trade['direction']}\n\n"
        f"Entry: {fmt_price(trade['entry'])}\n"
        f"Exit: <b>{fmt_price(exit_price)}</b>\n"
        f"TP: {fmt_price(trade['tp'])}\n"
        f"SL: {fmt_price(trade['sl'])}\n"
        f"PnL: <b>{pnl_pct:+.2f}%</b>\n\n"
        f"Paper trade #{trade['id']}"
    )

    telegram_send(text)


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def send_open_trades_report(price_map):
    trades = get_open_trades()

    DIAG["open_trades"] = len(trades)

    if not trades:
        telegram_send(
            "📊 <b>OPEN TRADES</b>\n\n"
            "No open paper trades."
        )
        return

    lines = [
        "📊 <b>OPEN TRADES</b>",
        "",
    ]

    for trade in trades:
        symbol = trade["symbol"]
        direction = trade["direction"]

        current = price_map.get(symbol)

        if current is None:
            current = trade["current_price"]

        current_pct = pct_change(
            trade["entry"],
            current,
            direction,
        )

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
            f"Entry: {fmt_price(trade['entry'])}"
        )

        lines.append(
            f"Current: {fmt_price(current)} "
            f"({current_pct:+.2f}%)"
        )

        lines.append(
            f"TP: {fmt_price(trade['tp'])} "
            f"({tp_pct:+.2f}%)"
        )

        lines.append(
            f"SL: {fmt_price(trade['sl'])} "
            f"({-sl_pct:+.2f}%)"
        )

        lines.append("")

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# VERSION 4.6.1
# FIXED TRADE EXIT LOGIC
# ============================================================

def update_open_trades(price_map):
    """
    IMPORTANT VERSION 4.6.1 FIX

    The scanner does not run every tick.

    Example:
        SHORT
        Entry = 0.393200
        SL    = 0.398427

    If the next scan sees:
        Current = 0.473300

    the trade did NOT exit at 0.473300.

    It crossed the SL at:
        0.398427

    Therefore:

        exit_price = sl
        pnl        = pnl(entry, sl)

    Same logic for TP.

    The observed current price is still stored as
    current_price, but it is NEVER used as the
    execution price after a TP/SL crossing.
    """

    trades = get_open_trades()

    if not trades:
        DIAG["open_trades"] = 0
        return

    conn = db_connect()

    for trade in trades:
        symbol = trade["symbol"]

        current = price_map.get(symbol)

        if current is None:
            continue

        current = float(current)

        direction = trade["direction"]

        entry = float(trade["entry"])
        tp = float(trade["tp"])
        sl = float(trade["sl"])

        current_pct = pct_change(
            entry,
            current,
            direction,
        )

        exit_reason = None
        exit_price = None

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------
        if direction == "SHORT":

            # TP crossed
            if current <= tp:
                exit_reason = "TP"

                # IMPORTANT:
                # EXIT AT EXACT TP, NOT CURRENT
                exit_price = tp

            # SL crossed
            elif current >= sl:
                exit_reason = "SL"

                # IMPORTANT:
                # EXIT AT EXACT SL, NOT CURRENT
                exit_price = sl

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------
        else:

            # TP crossed
            if current >= tp:
                exit_reason = "TP"

                # IMPORTANT:
                # EXIT AT EXACT TP, NOT CURRENT
                exit_price = tp

            # SL crossed
            elif current <= sl:
                exit_reason = "SL"

                # IMPORTANT:
                # EXIT AT EXACT SL, NOT CURRENT
                exit_price = sl

        # ----------------------------------------------------
        # TRADE STILL OPEN
        # ----------------------------------------------------

        if exit_reason is None:

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

        # ----------------------------------------------------
        # TRADE CLOSED
        # ----------------------------------------------------
        #
        # CRITICAL FIX:
        #
        # PNL IS BASED ON exit_price
        # WHICH IS EXACT TP OR SL.
        #
        # NOT ON current.
        # ----------------------------------------------------

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

        if exit_reason == "TP":
            DIAG["tp_hits"] += 1
        else:
            DIAG["sl_hits"] += 1

        closed_trade = dict(trade)

        # Keep exact values used for notification
        closed_trade["current_price"] = current
        closed_trade["current_pct"] = current_pct

        send_trade_close_message(
            closed_trade,
            exit_reason,
            exit_price,
            pnl_pct,
        )

    conn.commit()
    conn.close()

    DIAG["open_trades"] = len(
        get_open_trades()
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):
    df = fetch_m30(symbol)

    if df is None:
        return None, None, None

    current_price = float(
        df.iloc[-1]["close"]
    )

    hooks = detect_hooks(
        symbol,
        df,
    )

    if not hooks:
        return None, current_price, df

    hook = hooks[0]

    DIAG["confirmed_hooks"] += 1

    if hook_exists(hook["id"]):
        return None, current_price, df

    save_hook(hook)

    DIAG["new_signals"] += 1

    return hook, current_price, df


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_text():
    return (
        "🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {now_iso()}\n\n"

        "<b>M30</b>\n"
        f"Requests: {DIAG['requests']}\n"
        f"Data OK: {DIAG['data_ok']}\n"
        f"Empty: {DIAG['empty']}\n"
        f"Short data: {DIAG['short_data']}\n"
        f"Errors: {DIAG['errors']}\n\n"

        "<b>PIVOTS</b>\n"
        f"Pivot Highs: {DIAG['pivot_highs']}\n"
        f"Pivot Lows: {DIAG['pivot_lows']}\n"
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
        f"{DIAG['open_trades']}\n"
        f"TP Hits: "
        f"{DIAG['tp_hits']}\n"
        f"SL Hits: "
        f"{DIAG['sl_hits']}\n\n"

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
# MAIN SCAN
# ============================================================

def scan_once():
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

    price_map = {}

    new_items = []

    print()
    print("=" * 70)
    print(
        f"NDS M30 SCAN | VERSION {VERSION}"
    )
    print(
        now_iso()
    )
    print("=" * 70)

    # --------------------------------------------------------
    # FIRST: GET CURRENT PRICES
    # --------------------------------------------------------

    for symbol in ASSETS:

        df = fetch_m30(symbol)

        if df is None:
            continue

        current_price = float(
            df.iloc[-1]["close"]
        )

        price_map[symbol] = current_price

        hooks = detect_hooks(
            symbol,
            df,
        )

        if not hooks:
            continue

        hook = hooks[0]

        DIAG["confirmed_hooks"] += 1

        if hook_exists(hook["id"]):
            continue

        save_hook(hook)

        DIAG["new_signals"] += 1

        new_items.append(
            (
                hook,
                current_price,
                df,
            )
        )

    # --------------------------------------------------------
    # IMPORTANT:
    # UPDATE OPEN TRADES AFTER PRICES ARE KNOWN
    # --------------------------------------------------------

    update_open_trades(
        price_map
    )

    # --------------------------------------------------------
    # OPEN NEW PAPER TRADES
    # --------------------------------------------------------

    for (
        hook,
        current_price,
        df,
    ) in new_items:

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
                f"NDS {hook['direction']} | "
                f"{hook['symbol']}\n"
                f"Entry: {fmt_price(hook['entry'])}\n"
                f"TP 86.4%: {fmt_price(hook['tp'])}\n"
                f"SL: {fmt_price(hook['sl'])}\n"
                f"TP Distance: "
                f"{hook['tp_distance_pct']:.2f}%\n"
                f"M30 ONLY | PAPER"
            )

            telegram_send_photo(
                chart_path,
                caption,
            )

        # Do not create duplicate open trade
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
        price_map
    )

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    DIAG["open_trades"] = len(
        get_open_trades()
    )

    report = diagnostic_text()

    print()
    print(
        report.replace(
            "<b>",
            ""
        ).replace(
            "</b>",
            ""
        )
    )

    telegram_send(report)


# ============================================================
# STARTUP
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
        "Positive Hook:"
    )
    print(
        "START -> H1 -> L1 -> H2 -> L2 -> H3"
    )
    print(
        "Entry = H3"
    )
    print()
    print(
        "Negative Hook:"
    )
    print(
        "START -> L1 -> H1 -> L2 -> H2 -> L3"
    )
    print(
        "Entry = L3"
    )
    print()
    print(
        "TP = 86.4%"
    )
    print(
        "SL = 50% TP distance"
    )
    print()
    print(
        "VERSION 4.6.1:"
    )
    print(
        "TP/SL CROSS = EXACT TP/SL EXIT PRICE"
    )
    print(
        "PNL = EXACT TP/SL EXIT LEVEL"
    )
    print("=" * 70)
    print()

    last_scan = 0
    last_report = 0

    while True:

        current_time = time.time()

        try:

            if (
                current_time - last_scan
                >= SCAN_SECONDS
            ):
                scan_once()
                last_scan = current_time

            if (
                current_time - last_report
                >= REPORT_SECONDS
            ):
                last_report = current_time

        except KeyboardInterrupt:
            print(
                "Scanner stopped."
            )
            break

        except Exception as e:
            print(
                "MAIN LOOP ERROR:",
                repr(e)
            )

        time.sleep(5)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
