
# ============================================================
# NDS M5 LIVE SCANNER
# VERSION 5.0.0
# ============================================================
#
# PAPER ONLY
# M5 ONLY
# NO M1
# NO 123F
#
# POSITIVE / SHORT:
# START -> H1 -> L1 -> H2 -> L2 -> H3
# H2 > H1, L2 < L1, H3 > H2
# ENTRY = H3
#
# NEGATIVE / LONG:
# START -> L1 -> H1 -> L2 -> H2 -> L3
# L2 < L1, H2 > H1, L3 < L2
# ENTRY = L3
#
# TP = 86.4% RETRACEMENT FROM START TO ENTRY
# SL = 50% OF TP DISTANCE
#
# FEATURES:
# - SQLite persistent duplicate-hook prevention
# - Separate M5 database
# - Exact TP/SL exit price for paper trades
# - Candlestick chart sent to Telegram for new signals
# - Win rate, gross profit, gross loss, net realized PnL
# - Open/live PnL and total PnL
# - PAPER TRADING ONLY; no exchange orders
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

VERSION = "5.0.0"

# Separate database so M5 paper trades do not mix with M30.
DB_FILE = "nds_m5_live_v50.db"
CHART_DIR = "nds_charts_m5"

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

INTERVAL = "5m"
M5_COUNT = 500

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

def new_diagnostics():
    return {
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
        "duplicate_hooks": 0,
        "new_signals": 0,
        "charts": 0,
        "opened_trades": 0,
        "open_trades": 0,
        "tp_hits": 0,
        "sl_hits": 0,
    }


DIAG = new_diagnostics()


# ============================================================
# UTILITIES
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def pct_change(entry, price, direction):
    entry = float(entry)
    price = float(price)

    if entry == 0:
        return 0.0

    if direction == "LONG":
        return (price - entry) / entry * 100.0

    return (entry - price) / entry * 100.0


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
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()

    conn.execute("""
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
    """)

    conn.execute("""
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
    """)

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_paper_trades_hook_id
        ON paper_trades(hook_id)
    """)

    conn.commit()
    conn.close()


def hook_exists(hid):
    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM hooks WHERE id = ? LIMIT 1",
        (hid,),
    ).fetchone()
    conn.close()
    return row is not None


def save_hook(hook):
    conn = db_connect()
    cur = conn.execute("""
        INSERT OR IGNORE INTO hooks (
            id, symbol, direction, start_price,
            entry, tp, sl, confirmation_time, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        hook["id"],
        hook["symbol"],
        hook["direction"],
        hook["start_price"],
        hook["entry"],
        hook["tp"],
        hook["sl"],
        hook["confirmation_time"],
        now_iso(),
    ))

    inserted = cur.rowcount == 1
    conn.commit()
    conn.close()
    return inserted


def get_open_trades():
    conn = db_connect()
    rows = conn.execute("""
        SELECT *
        FROM paper_trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def has_open_trade(symbol):
    conn = db_connect()
    row = conn.execute("""
        SELECT id
        FROM paper_trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
    """, (symbol,)).fetchone()
    conn.close()
    return row is not None


def create_paper_trade(hook, current_price):
    entry = float(hook["entry"])
    tp = float(hook["tp"])
    sl = float(hook["sl"])
    direction = hook["direction"]

    tp_pct = abs(pct_change(entry, tp, direction))
    sl_pct = abs(pct_change(entry, sl, direction))
    current_pct = pct_change(entry, current_price, direction)

    conn = db_connect()

    # INSERT OR IGNORE prevents duplicate trades for the same hook.
    cur = conn.execute("""
        INSERT OR IGNORE INTO paper_trades (
            hook_id, symbol, direction, entry, tp, sl,
            entry_time, current_price, current_pct,
            tp_pct, sl_pct, status, exit_price,
            exit_time, pnl_pct, last_update
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN',
                NULL, NULL, NULL, ?)
    """, (
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
    ))

    trade_id = cur.lastrowid if cur.rowcount == 1 else None

    if trade_id is None:
        row = conn.execute(
            "SELECT id FROM paper_trades WHERE hook_id = ?",
            (hook["id"],),
        ).fetchone()
        trade_id = row["id"] if row else None

    conn.commit()
    conn.close()
    return trade_id


def get_performance():
    conn = db_connect()

    closed = conn.execute("""
        SELECT pnl_pct
        FROM paper_trades
        WHERE status = 'CLOSED'
          AND pnl_pct IS NOT NULL
    """).fetchall()

    opened = conn.execute("""
        SELECT current_pct
        FROM paper_trades
        WHERE status = 'OPEN'
    """).fetchall()

    conn.close()

    closed_pnls = [float(r["pnl_pct"]) for r in closed]
    live_pnls = [
        float(r["current_pct"] or 0.0)
        for r in opened
    ]

    wins = [p for p in closed_pnls if p > 0]
    losses = [p for p in closed_pnls if p <= 0]

    closed_count = len(closed_pnls)
    win_rate = (
        len(wins) / closed_count * 100.0
        if closed_count else 0.0
    )

    gross_profit = sum(wins)
    gross_loss = sum(losses)  # Negative value
    realized_net = sum(closed_pnls)
    live_pnl = sum(live_pnls)
    total_net = realized_net + live_pnl

    return {
        "closed_count": closed_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "realized_net": realized_net,
        "live_pnl": live_pnl,
        "total_net": total_net,
        "open_count": len(live_pnls),
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send(text):
    if not telegram_enabled():
        print("Telegram not configured.")
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

        if not response.ok:
            print("Telegram error:", response.text[:500])

        return response.ok

    except Exception as exc:
        print("Telegram text error:", repr(exc))
        return False


def telegram_send_photo(photo_path, caption=""):
    if not telegram_enabled() or not os.path.exists(photo_path):
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(photo_path, "rb") as photo:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={"photo": photo},
                timeout=REQUEST_TIMEOUT,
            )

        if not response.ok:
            print("Telegram photo error:", response.text[:500])

        return response.ok

    except Exception as exc:
        print("Telegram photo error:", repr(exc))
        return False


# ============================================================
# KRAKEN M5 DATA
# ============================================================

def fetch_m5(symbol):
    DIAG["requests"] += 1
    url = f"{BASE_URL}/{symbol}/{INTERVAL}"

    try:
        response = requests.get(
            url,
            params={"since": 0},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        candles = payload.get("candles", [])

        if not candles:
            DIAG["empty"] += 1
            return None

        rows = []

        for candle in candles:
            try:
                ts = candle.get("time")
                if ts is None:
                    continue

                ts = float(ts)
                if ts > 10_000_000_000:
                    ts /= 1000.0

                rows.append({
                    "time": pd.to_datetime(
                        ts,
                        unit="s",
                        utc=True,
                    ),
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle.get("volume", 0)),
                })

            except (ValueError, TypeError, KeyError):
                continue

        if len(rows) < 50:
            DIAG["short_data"] += 1
            return None

        df = pd.DataFrame(rows)
        df = (
            df.drop_duplicates("time")
            .sort_values("time")
            .tail(M5_COUNT)
            .reset_index(drop=True)
        )

        DIAG["data_ok"] += 1
        return df

    except Exception as exc:
        DIAG["errors"] += 1
        print(f"{symbol} M5 error:", repr(exc))
        return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):
    highs = []
    lows = []

    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)

    for i in range(PIVOT_LEFT, len(df) - PIVOT_RIGHT):
        left_h = h[i - PIVOT_LEFT:i]
        right_h = h[i + 1:i + 1 + PIVOT_RIGHT]
        left_l = l[i - PIVOT_LEFT:i]
        right_l = l[i + 1:i + 1 + PIVOT_RIGHT]

        if h[i] >= np.max(left_h) and h[i] >= np.max(right_h):
            highs.append({
                "index": i,
                "time": df.iloc[i]["time"],
                "price": float(h[i]),
                "kind": "H",
            })

        if l[i] <= np.min(left_l) and l[i] <= np.min(right_l):
            lows.append({
                "index": i,
                "time": df.iloc[i]["time"],
                "price": float(l[i]),
                "kind": "L",
            })

    DIAG["pivot_highs"] += len(highs)
    DIAG["pivot_lows"] += len(lows)
    return highs, lows


def alternating_pivots(highs, lows):
    all_pivots = highs + lows
    all_pivots.sort(
        key=lambda p: (
            p["index"],
            0 if p["kind"] == "H" else 1,
        )
    )

    result = []

    for pivot in all_pivots:
        if not result:
            result.append(pivot)
            continue

        last = result[-1]

        if pivot["kind"] == last["kind"]:
            if pivot["kind"] == "H":
                if pivot["price"] >= last["price"]:
                    result[-1] = pivot
            else:
                if pivot["price"] <= last["price"]:
                    result[-1] = pivot
        else:
            result.append(pivot)

    DIAG["alternating_pivots"] += len(result)
    return result


# ============================================================
# TP / SL
# ============================================================

def calculate_short_levels(start_price, entry):
    move = entry - start_price
    if move <= 0:
        return None

    tp = entry - move * TP_RETRACE
    tp_distance_pct = abs(entry - tp) / entry * 100.0
    sl = entry + abs(entry - tp) * SL_TP_MULTIPLIER

    return {
        "tp": tp,
        "sl": sl,
        "tp_distance_pct": tp_distance_pct,
    }


def calculate_long_levels(start_price, entry):
    move = start_price - entry
    if move <= 0:
        return None

    tp = entry + move * TP_RETRACE
    tp_distance_pct = abs(tp - entry) / entry * 100.0
    sl = entry - abs(entry - tp) * SL_TP_MULTIPLIER

    return {
        "tp": tp,
        "sl": sl,
        "tp_distance_pct": tp_distance_pct,
    }


def tp_already_touched(df, confirmation_index, direction, tp):
    # Ignore the confirmation candle and inspect candles after it.
    future = df.iloc[confirmation_index + 1:]

    if future.empty:
        return False

    if direction == "SHORT":
        return bool((future["low"] <= tp).any())

    return bool((future["high"] >= tp).any())


# ============================================================
# HOOK BUILDERS
# ============================================================

def build_short_hook(df, seq, symbol):
    if len(seq) != 6:
        return None

    if [p["kind"] for p in seq] != ["L", "H", "L", "H", "L", "H"]:
        return None

    DIAG["short_candidates"] += 1
    start, h1, l1, h2, l2, h3 = seq

    # START must be the lowest of the six points.
    if abs(start["price"] - min(p["price"] for p in seq)) > 1e-12:
        DIAG["short_start_rejected"] += 1
        return None

    if h2["price"] <= h1["price"]:
        DIAG["short_h2_rejected"] += 1
        return None

    if l2["price"] >= l1["price"]:
        DIAG["short_l2_rejected"] += 1
        return None

    if h3["price"] <= h2["price"]:
        DIAG["short_h3_rejected"] += 1
        return None

    DIAG["short_structure_valid"] += 1

    total_range = h3["price"] - start["price"]
    if start["price"] <= 0:
        return None

    if total_range / start["price"] < MIN_SWING_PCT:
        return None

    levels = calculate_short_levels(start["price"], h3["price"])
    if not levels:
        return None

    tp = levels["tp"]
    sl = levels["sl"]
    distance = levels["tp_distance_pct"]

    if distance < MIN_TP_DISTANCE_PCT or distance > MAX_TP_DISTANCE_PCT:
        DIAG["tp_distance_rejected"] += 1
        return None

    DIAG["tp_valid_distance"] += 1

    if tp_already_touched(df, h3["index"], "SHORT", tp):
        DIAG["tp_already_touched"] += 1
        return None

    points = [start, h1, l1, h2, l2, h3]

    return {
        "id": hook_id(symbol, "SHORT", points),
        "symbol": symbol,
        "direction": "SHORT",
        "start_price": float(start["price"]),
        "entry": float(h3["price"]),
        "tp": float(tp),
        "sl": float(sl),
        "tp_distance_pct": float(distance),
        "confirmation_time": h3["time"].isoformat(),
        "confirmation_index": h3["index"],
        "points": points,
    }


def build_long_hook(df, seq, symbol):
    if len(seq) != 6:
        return None

    if [p["kind"] for p in seq] != ["H", "L", "H", "L", "H", "L"]:
        return None

    DIAG["long_candidates"] += 1
    start, l1, h1, l2, h2, l3 = seq

    # START must be the highest of the six points.
    if abs(start["price"] - max(p["price"] for p in seq)) > 1e-12:
        DIAG["long_start_rejected"] += 1
        return None

    if l2["price"] >= l1["price"]:
        DIAG["long_l2_rejected"] += 1
        return None

    if h2["price"] <= h1["price"]:
        DIAG["long_h2_rejected"] += 1
        return None

    if l3["price"] >= l2["price"]:
        DIAG["long_l3_rejected"] += 1
        return None

    DIAG["long_structure_valid"] += 1

    total_range = start["price"] - l3["price"]
    if l3["price"] <= 0:
        return None

    if total_range / l3["price"] < MIN_SWING_PCT:
        return None

    levels = calculate_long_levels(start["price"], l3["price"])
    if not levels:
        return None

    tp = levels["tp"]
    sl = levels["sl"]
    distance = levels["tp_distance_pct"]

    if distance < MIN_TP_DISTANCE_PCT or distance > MAX_TP_DISTANCE_PCT:
        DIAG["tp_distance_rejected"] += 1
        return None

    DIAG["tp_valid_distance"] += 1

    if tp_already_touched(df, l3["index"], "LONG", tp):
        DIAG["tp_already_touched"] += 1
        return None

    points = [start, l1, h1, l2, h2, l3]

    return {
        "id": hook_id(symbol, "LONG", points),
        "symbol": symbol,
        "direction": "LONG",
        "start_price": float(start["price"]),
        "entry": float(l3["price"]),
        "tp": float(tp),
        "sl": float(sl),
        "tp_distance_pct": float(distance),
        "confirmation_time": l3["time"].isoformat(),
        "confirmation_index": l3["index"],
        "points": points,
    }


def detect_hooks(symbol, df):
    highs, lows = detect_pivots(df)
    pivots = alternating_pivots(highs, lows)

    DIAG["sequences"] += max(0, len(pivots) - 5)

    candidates = []

    for i in range(len(pivots) - 5):
        seq = pivots[i:i + 6]

        short_hook = build_short_hook(df, seq, symbol)
        if short_hook:
            candidates.append(short_hook)

        long_hook = build_long_hook(df, seq, symbol)
        if long_hook:
            candidates.append(long_hook)

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: item["confirmation_time"],
        reverse=True,
    )

    # Preserve original behavior: only latest confirmed hook per symbol.
    return [candidates[0]]


# ============================================================
# CHART
# ============================================================

def make_chart(df, hook, current_price):
    os.makedirs(CHART_DIR, exist_ok=True)

    symbol = hook["symbol"]
    direction = hook["direction"]
    chart_df = df.tail(CHART_CANDLES).copy()

    fig, ax = plt.subplots(figsize=(16, 9))

    x = mdates.date2num(chart_df["time"].dt.to_pydatetime())
    candle_width = 0.0030  # 5-minute candles

    for xi, row in zip(x, chart_df.itertuples()):
        o = float(row.open)
        h = float(row.high)
        l = float(row.low)
        c = float(row.close)

        ax.plot([xi, xi], [l, h], linewidth=1.0)

        bottom = min(o, c)
        height = abs(c - o)

        if height == 0:
            height = max(abs(h - l) * 0.002, 1e-12)

        rect = plt.Rectangle(
            (xi - candle_width / 2, bottom),
            candle_width,
            height,
            fill=False,
            linewidth=1.0,
        )
        ax.add_patch(rect)

    points = hook["points"]
    px = [
        mdates.date2num(
            pd.to_datetime(p["time"], utc=True).to_pydatetime()
        )
        for p in points
    ]
    py = [p["price"] for p in points]

    ax.plot(px, py, linewidth=2.0)

    if direction == "SHORT":
        labels = ["START", "H1", "L1", "H2", "L2", "H3 CONFIRMED"]
        offsets = [(-8, -25), (0, 18), (0, -25), (0, 18), (0, -25), (0, 20)]
    else:
        labels = ["START", "L1", "H1", "L2", "H2", "L3 CONFIRMED"]
        offsets = [(-8, 25), (0, -25), (0, 20), (0, -25), (0, 20), (0, -25)]

    for xi, yi, label, offset in zip(px, py, labels, offsets):
        ax.scatter([xi], [yi], s=65, zorder=5)
        ax.annotate(
            f"{label}\n{fmt_price(yi)}",
            (xi, yi),
            xytext=offset,
            textcoords="offset points",
            ha="center",
            va="center",
            fontsize=9,
            bbox={"boxstyle": "round,pad=0.3", "alpha": 0.80},
        )

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

    current_pct = pct_change(entry, current_price, direction)
    tp_pct = abs(pct_change(entry, tp, direction))
    sl_pct = abs(pct_change(entry, sl, direction))

    for price, style, width in [
        (entry, "--", 1.2),
        (tp, "--", 1.5),
        (sl, "--", 1.5),
        (current_price, ":", 1.2),
    ]:
        ax.axhline(price, linestyle=style, linewidth=width)

    ax.text(
        1.005, entry,
        f"ENTRY {fmt_price(entry)}",
        transform=ax.get_yaxis_transform(),
        va="center", fontsize=9,
    )
    ax.text(
        1.005, tp,
        f"TP 86.4% {fmt_price(tp)} (+{tp_pct:.2f}%)",
        transform=ax.get_yaxis_transform(),
        va="center", fontsize=9,
    )
    ax.text(
        1.005, sl,
        f"SL {fmt_price(sl)} (-{sl_pct:.2f}%)",
        transform=ax.get_yaxis_transform(),
        va="center", fontsize=9,
    )
    ax.text(
        1.005, current_price,
        f"CURRENT {fmt_price(current_price)} ({current_pct:+.2f}%)",
        transform=ax.get_yaxis_transform(),
        va="center", fontsize=9,
    )

    ax.axvline(px[-1], linestyle=":", linewidth=1.0)

    ax.set_title(
        f"NDS M5 | {symbol} | {direction} | TP 86.4% | "
        f"TP Distance {hook['tp_distance_pct']:.2f}% | "
        f"SL = 50% TP Distance"
    )
    ax.set_xlabel("Time UTC")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.20)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    plt.tight_layout()

    filename = f"{symbol}_{direction}_{hook['id']}.png"
    path = os.path.join(CHART_DIR, filename)

    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)

    DIAG["charts"] += 1
    return path


# ============================================================
# SIGNAL / TRADE MESSAGES
# ============================================================

def send_signal_message(hook, current_price):
    direction = hook["direction"]
    emoji = "🔴" if direction == "SHORT" else "🟢"

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

    tp_pct = abs(pct_change(entry, tp, direction))
    sl_pct = abs(pct_change(entry, sl, direction))
    current_pct = pct_change(entry, current_price, direction)

    text = (
        f"{emoji} <b>NDS M5 {direction}</b>\n"
        f"<b>{hook['symbol']}</b>\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"TP 86.4%: <b>{fmt_price(tp)}</b> (+{tp_pct:.2f}%)\n"
        f"SL: <b>{fmt_price(sl)}</b> (-{sl_pct:.2f}%)\n"
        f"Current: <b>{fmt_price(current_price)}</b> "
        f"({current_pct:+.2f}%)\n\n"
        f"TP distance: {hook['tp_distance_pct']:.2f}%\n"
        f"SL = 50% TP distance\n"
        f"M5 ONLY | PAPER"
    )
    telegram_send(text)


def send_trade_open_message(hook, trade_id):
    direction = hook["direction"]
    emoji = "🔴" if direction == "SHORT" else "🟢"

    entry = hook["entry"]
    tp = hook["tp"]
    sl = hook["sl"]

    tp_pct = abs(pct_change(entry, tp, direction))
    sl_pct = abs(pct_change(entry, sl, direction))

    telegram_send(
        f"{emoji} <b>PAPER TRADE OPENED</b>\n"
        f"{hook['symbol']} {direction}\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"TP: <b>{fmt_price(tp)}</b> (+{tp_pct:.2f}%)\n"
        f"SL: <b>{fmt_price(sl)}</b> (-{sl_pct:.2f}%)\n\n"
        f"Paper trade #{trade_id}\n"
        f"M5 ONLY"
    )


def send_trade_close_message(trade, exit_reason, exit_price, pnl_pct):
    if exit_reason == "TP":
        emoji = "✅"
        title = "TP HIT"
    else:
        emoji = "❌"
        title = "SL HIT"

    telegram_send(
        f"{emoji} <b>{title}</b>\n"
        f"{trade['symbol']} {trade['direction']}\n\n"
        f"Entry: {fmt_price(trade['entry'])}\n"
        f"Exit: <b>{fmt_price(exit_price)}</b>\n"
        f"TP: {fmt_price(trade['tp'])}\n"
        f"SL: {fmt_price(trade['sl'])}\n"
        f"PnL: <b>{pnl_pct:+.2f}%</b>\n\n"
        f"Paper trade #{trade['id']}"
    )


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(price_map):
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

        current_pct = pct_change(entry, current, direction)

        exit_reason = None
        exit_price = None

        # Current M5 candle close is used for this basic
        # crossing check. If both TP and SL were crossed
        # between scans, exact intrabar order is unknown.
        if direction == "SHORT":
            if current <= tp:
                exit_reason = "TP"
                exit_price = tp
            elif current >= sl:
                exit_reason = "SL"
                exit_price = sl
        else:
            if current >= tp:
                exit_reason = "TP"
                exit_price = tp
            elif current <= sl:
                exit_reason = "SL"
                exit_price = sl

        if exit_reason is None:
            conn.execute("""
                UPDATE paper_trades
                SET current_price = ?,
                    current_pct = ?,
                    last_update = ?
                WHERE id = ? AND status = 'OPEN'
            """, (current, current_pct, now_iso(), trade["id"]))
            continue

        # Exact TP/SL exit level, not the later market price.
        pnl_pct = pct_change(entry, exit_price, direction)

        conn.execute("""
            UPDATE paper_trades
            SET current_price = ?,
                current_pct = ?,
                status = 'CLOSED',
                exit_price = ?,
                exit_time = ?,
                pnl_pct = ?,
                last_update = ?
            WHERE id = ? AND status = 'OPEN'
        """, (
            current,
            current_pct,
            exit_price,
            now_iso(),
            pnl_pct,
            now_iso(),
            trade["id"],
        ))

        if exit_reason == "TP":
            DIAG["tp_hits"] += 1
        else:
            DIAG["sl_hits"] += 1

        send_trade_close_message(
            trade,
            exit_reason,
            exit_price,
            pnl_pct,
        )

    conn.commit()
    conn.close()
    DIAG["open_trades"] = len(get_open_trades())


# ============================================================
# PERFORMANCE REPORT
# ============================================================

def performance_lines():
    stats = get_performance()

    return [
        "<b>OVERALL PERFORMANCE</b>",
        f"Closed Trades: <b>{stats['closed_count']}</b>",
        f"Wins: <b>{stats['wins']}</b>",
        f"Losses: <b>{stats['losses']}</b>",
        f"Win Rate: <b>{stats['win_rate']:.2f}%</b>",
        f"Total Profit: <b>+{stats['gross_profit']:.2f}%</b>",
        f"Total Loss: <b>{stats['gross_loss']:.2f}%</b>",
        f"Realized Net PnL: <b>{stats['realized_net']:+.2f}%</b>",
        f"Open / Live PnL: <b>{stats['live_pnl']:+.2f}%</b>",
        f"Total PnL (Realized + Live): <b>{stats['total_net']:+.2f}%</b>",
    ]


def send_open_trades_report(price_map):
    trades = get_open_trades()
    DIAG["open_trades"] = len(trades)

    lines = ["📊 <b>OPEN TRADES | M5</b>", ""]

    if not trades:
        lines.append("No open paper trades.")
        lines.append("")
    else:
        for trade in trades:
            symbol = trade["symbol"]
            direction = trade["direction"]
            current = price_map.get(symbol)

            if current is None:
                current = trade["current_price"]

            if current is None:
                current = trade["entry"]

            current_pct = pct_change(
                trade["entry"],
                current,
                direction,
            )
            tp_pct = abs(pct_change(
                trade["entry"],
                trade["tp"],
                direction,
            ))
            sl_pct = abs(pct_change(
                trade["entry"],
                trade["sl"],
                direction,
            ))

            emoji = "🟢" if direction == "LONG" else "🔴"

            lines.extend([
                f"{emoji} <b>{symbol}</b> {direction}",
                f"Entry: {fmt_price(trade['entry'])}",
                f"Current: {fmt_price(current)} ({current_pct:+.2f}%)",
                f"TP: {fmt_price(trade['tp'])} (+{tp_pct:.2f}%)",
                f"SL: {fmt_price(trade['sl'])} (-{sl_pct:.2f}%)",
                "",
            ])

    lines.extend(performance_lines())
    telegram_send("\n".join(lines))


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_text():
    lines = [
        "🔎 <b>NDS DIAGNOSTIC</b>",
        f"Version: <b>{VERSION}</b>",
        f"Time: {now_iso()}",
        "",
        "<b>M5 DATA</b>",
        f"Requests: {DIAG['requests']}",
        f"Data OK: {DIAG['data_ok']}",
        f"Empty: {DIAG['empty']}",
        f"Short data: {DIAG['short_data']}",
        f"Errors: {DIAG['errors']}",
        "",
        "<b>PIVOTS</b>",
        f"Pivot Highs: {DIAG['pivot_highs']}",
        f"Pivot Lows: {DIAG['pivot_lows']}",
        f"Alternating Pivots: {DIAG['alternating_pivots']}",
        f"6-point Sequences: {DIAG['sequences']}",
        "",
        "<b>POSITIVE / SHORT</b>",
        f"Candidates: {DIAG['short_candidates']}",
        f"Structure Valid: {DIAG['short_structure_valid']}",
        f"START rejected: {DIAG['short_start_rejected']}",
        f"H2 rejected: {DIAG['short_h2_rejected']}",
        f"L2 rejected: {DIAG['short_l2_rejected']}",
        f"H3 rejected: {DIAG['short_h3_rejected']}",
        "",
        "<b>NEGATIVE / LONG</b>",
        f"Candidates: {DIAG['long_candidates']}",
        f"Structure Valid: {DIAG['long_structure_valid']}",
        f"START rejected: {DIAG['long_start_rejected']}",
        f"L2 rejected: {DIAG['long_l2_rejected']}",
        f"H2 rejected: {DIAG['long_h2_rejected']}",
        f"L3 rejected: {DIAG['long_l3_rejected']}",
        "",
        "<b>TP FILTER</b>",
        f"Valid distance: {DIAG['tp_valid_distance']}",
        f"Distance rejected: {DIAG['tp_distance_rejected']}",
        f"TP already touched: {DIAG['tp_already_touched']}",
        "",
        "<b>SCAN / TRADES</b>",
        f"Confirmed Hooks: {DIAG['confirmed_hooks']}",
        f"New Signals: {DIAG['new_signals']}",
        f"Duplicate Hooks Skipped: {DIAG['duplicate_hooks']}",
        f"Charts: {DIAG['charts']}",
        f"Opened Trades: {DIAG['opened_trades']}",
        f"Open Trades: {DIAG['open_trades']}",
        f"TP Hits: {DIAG['tp_hits']}",
        f"SL Hits: {DIAG['sl_hits']}",
        "",
    ]

    lines.extend(performance_lines())

    lines.extend([
        "",
        "<b>TP = 86.4%</b>",
        f"TP distance: {MIN_TP_DISTANCE_PCT:.2f}% → {MAX_TP_DISTANCE_PCT:.2f}%",
        "SL = 50% TP distance",
        "",
        "<b>M5 ONLY</b>",
        "NO M1 / NO 123F",
        "PAPER ONLY",
        "NO REAL ORDERS",
    ])

    return "\n".join(lines)


# ============================================================
# MAIN SCAN
# ============================================================

def scan_once():
    global DIAG
    DIAG = new_diagnostics()

    price_map = {}
    new_items = []

    print()
    print("=" * 70)
    print(f"NDS M5 SCAN | VERSION {VERSION}")
    print(now_iso())
    print("=" * 70)

    # Fetch each asset once for this scan.
    for symbol in ASSETS:
        df = fetch_m5(symbol)

        if df is None:
            continue

        current_price = float(df.iloc[-1]["close"])
        price_map[symbol] = current_price

        hooks = detect_hooks(symbol, df)

        if not hooks:
            continue

        hook = hooks[0]
        DIAG["confirmed_hooks"] += 1

        # Database-backed duplicate prevention.
        # Same hook ID will never send a second signal/chart.
        if hook_exists(hook["id"]):
            DIAG["duplicate_hooks"] += 1
            continue

        # Only process as a new signal if the hook was
        # successfully inserted into the database.
        if not save_hook(hook):
            DIAG["duplicate_hooks"] += 1
            continue

        DIAG["new_signals"] += 1
        new_items.append((hook, current_price, df))

    # Update existing trades before reporting.
    update_open_trades(price_map)

    # Process newly confirmed hooks.
    for hook, current_price, df in new_items:
        send_signal_message(hook, current_price)

        chart_path = make_chart(df, hook, current_price)

        if chart_path:
            caption = (
                f"NDS {hook['direction']} | {hook['symbol']}\n"
                f"Entry: {fmt_price(hook['entry'])}\n"
                f"TP 86.4%: {fmt_price(hook['tp'])}\n"
                f"SL: {fmt_price(hook['sl'])}\n"
                f"TP Distance: {hook['tp_distance_pct']:.2f}%\n"
                f"M5 ONLY | PAPER"
            )
            telegram_send_photo(chart_path, caption)

        # Preserve one open trade per symbol.
        if has_open_trade(hook["symbol"]):
            continue

        trade_id = create_paper_trade(hook, current_price)

        if trade_id is not None:
            DIAG["opened_trades"] += 1
            send_trade_open_message(hook, trade_id)

    send_open_trades_report(price_map)

    DIAG["open_trades"] = len(get_open_trades())

    report = diagnostic_text()
    print()
    print(report.replace("<b>", "").replace("</b>", ""))
    telegram_send(report)


# ============================================================
# STARTUP
# ============================================================

def main():
    init_db()
    os.makedirs(CHART_DIR, exist_ok=True)

    print()
    print("=" * 70)
    print(f"NDS M5 LIVE SCANNER VERSION {VERSION}")
    print("=" * 70)
    print("PAPER ONLY")
    print("M5 ONLY")
    print("NO M1")
    print("NO 123F")
    print()
    print("Positive Hook:")
    print("START -> H1 -> L1 -> H2 -> L2 -> H3")
    print("Entry = H3")
    print()
    print("Negative Hook:")
    print("START -> L1 -> H1 -> L2 -> H2 -> L3")
    print("Entry = L3")
    print()
    print("TP = 86.4% retracement")
    print("SL = 50% TP distance")
    print("Duplicate hooks skipped using SQLite")
    print("Win rate / gross profit / gross loss / net PnL enabled")
    print("No real exchange orders")
    print("=" * 70)

    last_scan = 0
    last_report = 0

    while True:
        current_time = time.time()

        try:
            if current_time - last_scan >= SCAN_SECONDS:
                scan_once()
                last_scan = time.time()

            # Kept from the original workflow logic.
            # The scan itself also sends reports each cycle.
            if current_time - last_report >= REPORT_SECONDS:
                last_report = current_time

        except KeyboardInterrupt:
            print("Scanner stopped.")
            break

        except Exception as exc:
            print("MAIN LOOP ERROR:", repr(exc))

        time.sleep(5)


if __name__ == "__main__":
    main()
