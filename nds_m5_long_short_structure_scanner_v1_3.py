#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NDS M15 Four-Pivot Long/Short Scanner v1.0.0

Paper trading / signal detection only.
No exchange orders are ever placed.

LONG:  H1 -> L1 -> H2 -> L2
       H2 > H1 and L2 < L1
       TP = H2, SL = min(L1, L2)

SHORT: L1 -> H1 -> L2 -> H2
       L2 < L1 and H2 > H1
       TP = L2, SL = max(H1, H2)

Pivots are confirmed using closed M15 candles.
"""

import os
import time
import math
import sqlite3
import logging
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "1.0.0"

API_BASE = "https://futures.kraken.com/derivatives/api/v3"
CHART_BASE = "https://futures.kraken.com/api/charts/v1/trade"

TIMEFRAME = "15m"
CANDLE_LIMIT = 500

TARGET_SYMBOLS = 100

PIVOT_LEFT = 5
PIVOT_RIGHT = 5
MIN_PIVOT_SPACING = 3

MAX_OPEN_TRADES = 3

DB_PATH = "nds_m15_four_pivot_scanner.db"
CHART_DIR = "nds_m15_four_pivot_charts"

REQUEST_TIMEOUT = 20

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN", "")
)
TELEGRAM_CHAT = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT", "")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("NDS_M15")


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(value, default=0.0):
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def api_get(url, params=None):
    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": f"NDS-M15/{VERSION}"},
    )
    response.raise_for_status()
    return response.json()


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)

    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                p1_time TEXT NOT NULL,
                p2_time TEXT NOT NULL,
                p3_time TEXT NOT NULL,
                p4_time TEXT NOT NULL,
                p1_price REAL NOT NULL,
                p2_price REAL NOT NULL,
                p3_price REAL NOT NULL,
                p4_price REAL NOT NULL,
                entry REAL NOT NULL,
                tp REAL NOT NULL,
                sl REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(
                    symbol, side,
                    p1_time, p2_time, p3_time, p4_time
                )
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry REAL NOT NULL,
                tp REAL NOT NULL,
                sl REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                pnl_pct REAL,
                FOREIGN KEY(pattern_id) REFERENCES patterns(id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_status
            ON trades(status)
        """)


# ============================================================
# KRAKEN FUTURES MARKETS AND PRICES
# ============================================================

def get_symbols():
    data = api_get(f"{API_BASE}/instruments")
    instruments = data.get("instruments", [])

    symbols = []

    for item in instruments:
        symbol = str(item.get("symbol", "")).strip()

        if not symbol.startswith("PF_"):
            continue

        if item.get("tradeable", True) is False:
            continue

        if symbol and symbol not in symbols:
            symbols.append(symbol)

    # Prioritize reference markets if they are available.
    for required in ("PF_XBTUSD", "PF_XAUTUSD"):
        if required in symbols:
            symbols.remove(required)
            symbols.insert(0, required)

    return symbols[:TARGET_SYMBOLS]


def get_prices():
    data = api_get(f"{API_BASE}/tickers")
    tickers = data.get("tickers", [])

    prices = {}

    for item in tickers:
        symbol = str(item.get("symbol", ""))

        bid = safe_float(item.get("bid"))
        ask = safe_float(item.get("ask"))
        last = safe_float(item.get("last"))
        mark = safe_float(item.get("markPrice"))

        if bid > 0 and ask > 0:
            price = (bid + ask) / 2
        else:
            price = mark or last

        if symbol and price > 0:
            prices[symbol] = price

    return prices


# ============================================================
# M15 CANDLES
# ============================================================

def fetch_candles(symbol):
    end_time = int(time.time())
    start_time = end_time - CANDLE_LIMIT * 15 * 60

    url = f"{CHART_BASE}/{symbol}/{TIMEFRAME}"

    data = api_get(
        url,
        params={
            "from": start_time,
            "to": end_time,
        },
    )

    if isinstance(data, dict):
        rows = data.get("candles", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    aliases = {
        "time": "time",
        "timestamp": "time",
        "t": "time",
        "open": "open",
        "o": "open",
        "high": "high",
        "h": "high",
        "low": "low",
        "l": "low",
        "close": "close",
        "c": "close",
        "volume": "volume",
        "v": "volume",
    }

    rename = {}

    for col in df.columns:
        key = str(col).lower()
        if key in aliases:
            rename[col] = aliases[key]

    df = df.rename(columns=rename)

    required = {"time", "open", "high", "low", "close"}

    if not required.issubset(df.columns):
        log.warning(
            "%s: unexpected candle fields: %s",
            symbol,
            list(df.columns),
        )
        return pd.DataFrame()

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    raw_time = pd.to_numeric(df["time"], errors="coerce")

    if raw_time.notna().any():
        unit = "ms" if raw_time.dropna().median() > 10**11 else "s"
        df["dt"] = pd.to_datetime(
            raw_time,
            unit=unit,
            utc=True,
            errors="coerce",
        )
    else:
        df["dt"] = pd.to_datetime(
            df["time"],
            utc=True,
            errors="coerce",
        )

    df = df.dropna(
        subset=["dt", "open", "high", "low", "close"]
    )

    df = (
        df.sort_values("dt")
        .drop_duplicates("dt")
        .reset_index(drop=True)
    )

    # Only fully closed 15-minute candles may confirm pivots.
    current_candle_start = pd.Timestamp.now(
        tz="UTC"
    ).floor("15min")

    df = df[df["dt"] < current_candle_start]

    return df.tail(CANDLE_LIMIT).reset_index(drop=True)


# ============================================================
# PIVOT DETECTION
# ============================================================

def find_pivots(df):
    pivots = []

    if len(df) < PIVOT_LEFT + PIVOT_RIGHT + 3:
        return pivots

    highs = df["high"].tolist()
    lows = df["low"].tolist()

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT,
    ):
        left = i - PIVOT_LEFT
        right = i + PIVOT_RIGHT + 1

        high_window = highs[left:right]
        low_window = lows[left:right]

        other_highs = (
            highs[left:i] + highs[i + 1:right]
        )
        other_lows = (
            lows[left:i] + lows[i + 1:right]
        )

        is_high = (
            highs[i] >= max(high_window)
            and highs[i] > max(other_highs)
        )

        is_low = (
            lows[i] <= min(low_window)
            and lows[i] < min(other_lows)
        )

        candle_time = df.iloc[i]["dt"].isoformat()

        if is_high:
            pivots.append({
                "i": i,
                "kind": "H",
                "price": float(highs[i]),
                "time": candle_time,
            })

        if is_low:
            pivots.append({
                "i": i,
                "kind": "L",
                "price": float(lows[i]),
                "time": candle_time,
            })

    pivots.sort(
        key=lambda p: (
            p["i"],
            0 if p["kind"] == "L" else 1,
        )
    )

    # Remove consecutive same-type pivots.
    compact = []

    for pivot in pivots:
        if not compact:
            compact.append(pivot)
            continue

        previous = compact[-1]

        if previous["kind"] == pivot["kind"]:
            if pivot["kind"] == "H":
                replace = pivot["price"] > previous["price"]
            else:
                replace = pivot["price"] < previous["price"]

            if replace:
                compact[-1] = pivot

            continue

        if pivot["i"] - previous["i"] >= MIN_PIVOT_SPACING:
            compact.append(pivot)

    return compact


# ============================================================
# FOUR-PIVOT PATTERN
# ============================================================

def detect_pattern(pivots):
    if len(pivots) < 4:
        return None

    # Most recent confirmed four-pivot sequence first.
    for start in range(len(pivots) - 4, -1, -1):
        p = pivots[start:start + 4]

        if len(p) != 4:
            continue

        if any(
            p[i + 1]["i"] <= p[i]["i"]
            for i in range(3)
        ):
            continue

        kinds = "".join(x["kind"] for x in p)

        # LONG: high, low, higher high, lower low.
        if (
            kinds == "HLHL"
            and p[2]["price"] > p[0]["price"]
            and p[3]["price"] < p[1]["price"]
        ):
            return {
                "side": "LONG",
                "p": p,
            }

        # SHORT: low, high, lower low, higher high.
        if (
            kinds == "LHLH"
            and p[2]["price"] < p[0]["price"]
            and p[3]["price"] > p[1]["price"]
        ):
            return {
                "side": "SHORT",
                "p": p,
            }

    return None


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def count_open_trades():
    with db_connect() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS n
            FROM trades
            WHERE status = 'OPEN'
        """).fetchone()

    return int(row["n"])


def save_signal(symbol, pattern, entry):
    side = pattern["side"]
    p = pattern["p"]

    if side == "LONG":
        tp = p[2]["price"]
        sl = min(p[1]["price"], p[3]["price"])

        # Avoid recording an already invalid long setup.
        if not (sl < entry < tp):
            return None

    else:
        tp = p[2]["price"]
        sl = max(p[1]["price"], p[3]["price"])

        # Avoid recording an already invalid short setup.
        if not (tp < entry < sl):
            return None

    if min(entry, tp, sl) <= 0:
        return None

    with db_connect() as conn:
        try:
            cur = conn.execute("""
                INSERT INTO patterns (
                    symbol, side,
                    p1_time, p2_time, p3_time, p4_time,
                    p1_price, p2_price, p3_price, p4_price,
                    entry, tp, sl, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                side,
                p[0]["time"],
                p[1]["time"],
                p[2]["time"],
                p[3]["time"],
                p[0]["price"],
                p[1]["price"],
                p[2]["price"],
                p[3]["price"],
                entry,
                tp,
                sl,
                utc_now(),
            ))

            pattern_id = cur.lastrowid

            conn.execute("""
                INSERT INTO trades (
                    pattern_id, symbol, side,
                    entry, tp, sl, status, opened_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """, (
                pattern_id,
                symbol,
                side,
                entry,
                tp,
                sl,
                utc_now(),
            ))

            return int(pattern_id)

        except sqlite3.IntegrityError:
            return None


def update_open_trades(prices):
    closed_count = 0

    with db_connect() as conn:
        trades = conn.execute("""
            SELECT *
            FROM trades
            WHERE status = 'OPEN'
        """).fetchall()

        for trade in trades:
            price = prices.get(trade["symbol"])

            if not price or price <= 0:
                continue

            side = trade["side"]
            entry = float(trade["entry"])
            tp = float(trade["tp"])
            sl = float(trade["sl"])

            exit_price = None

            if side == "LONG":
                if price >= tp:
                    exit_price = tp
                elif price <= sl:
                    exit_price = sl

            else:
                if price <= tp:
                    exit_price = tp
                elif price >= sl:
                    exit_price = sl

            if exit_price is None:
                continue

            if side == "LONG":
                pnl_pct = (exit_price / entry - 1) * 100
            else:
                pnl_pct = (entry / exit_price - 1) * 100

            conn.execute("""
                UPDATE trades
                SET status = 'CLOSED',
                    closed_at = ?,
                    exit_price = ?,
                    pnl_pct = ?
                WHERE id = ?
            """, (
                utc_now(),
                exit_price,
                pnl_pct,
                trade["id"],
            ))

            closed_count += 1

    return closed_count


# ============================================================
# CHART
# ============================================================

def create_chart(symbol, df, pattern, entry, tp, sl):
    p = pattern["p"]

    start_i = max(0, p[0]["i"] - 25)
    end_i = min(len(df), p[3]["i"] + 15)

    view = df.iloc[start_i:end_i].reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(13, 7))

    for i, row in view.iterrows():
        color = (
            "#159447"
            if row["close"] >= row["open"]
            else "#d62828"
        )

        ax.vlines(
            i,
            row["low"],
            row["high"],
            color=color,
            linewidth=1,
        )

        body_low = min(row["open"], row["close"])
        body_height = max(
            abs(row["close"] - row["open"]),
            abs(row["close"]) * 0.00001,
        )

        ax.add_patch(Rectangle(
            (i - 0.3, body_low),
            0.6,
            body_height,
            facecolor=color,
            edgecolor=color,
            alpha=0.85,
        ))

    def local_x(pivot):
        return pivot["i"] - start_i

    if pattern["side"] == "LONG":
        labels = [
            ("Green 1", p[0], "#159447"),
            ("Red 1", p[1], "#d62828"),
            ("Green 2", p[2], "#159447"),
            ("Red 2", p[3], "#d62828"),
        ]
    else:
        labels = [
            ("Red 1", p[0], "#d62828"),
            ("Green 1", p[1], "#159447"),
            ("Red 2", p[2], "#d62828"),
            ("Green 2", p[3], "#159447"),
        ]

    for label, pivot, color in labels:
        x = local_x(pivot)
        y = pivot["price"]

        ax.scatter(
            x,
            y,
            color=color,
            s=45,
            zorder=5,
        )

        ax.annotate(
            label,
            (x, y),
            xytext=(4, 8),
            textcoords="offset points",
            fontsize=8,
            color=color,
        )

    ax.axhline(
        entry,
        linestyle="--",
        color="#2878b5",
        label=f"Entry: {entry:.8g}",
    )

    ax.axhline(
        tp,
        linestyle="--",
        color="#159447",
        label=f"TP: {tp:.8g}",
    )

    ax.axhline(
        sl,
        linestyle="--",
        color="#d62828",
        label=f"SL: {sl:.8g}",
    )

    ax.set_title(
        f"NDS M15 Four-Pivot | {symbol} | {pattern['side']}"
    )
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    ax.legend(fontsize=8)
    fig.tight_layout()

    safe_symbol = symbol.replace("/", "_")
    filename = (
        f"{safe_symbol}_{pattern['side']}_"
        f"{int(time.time())}.png"
    )
    path = os.path.join(CHART_DIR, filename)

    fig.savefig(path, dpi=130)
    plt.close(fig)

    return path


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message, photo_path=None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log.info("Telegram credentials are missing.")
        return False

    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    try:
        if photo_path and os.path.isfile(photo_path):
            with open(photo_path, "rb") as photo:
                response = requests.post(
                    base + "/sendPhoto",
                    data={
                        "chat_id": TELEGRAM_CHAT,
                        "caption": message[:1024],
                    },
                    files={"photo": photo},
                    timeout=30,
                )
        else:
            response = requests.post(
                base + "/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            log.warning("Telegram API returned an unsuccessful result.")

        return bool(result.get("ok"))

    except Exception as exc:
        log.warning("Telegram error: %s", exc)
        return False


# ============================================================
# PERFORMANCE REPORT
# ============================================================

def performance_summary(prices):
    with db_connect() as conn:
        trades = conn.execute(
            "SELECT * FROM trades"
        ).fetchall()

    closed = [
        t for t in trades
        if t["status"] == "CLOSED"
    ]

    opened = [
        t for t in trades
        if t["status"] == "OPEN"
    ]

    wins = sum(
        1 for t in closed
        if safe_float(t["pnl_pct"]) > 0
    )

    realized = sum(
        safe_float(t["pnl_pct"])
        for t in closed
    )

    floating = 0.0

    for trade in opened:
        price = prices.get(trade["symbol"])

        if not price or trade["entry"] <= 0:
            continue

        if trade["side"] == "LONG":
            pnl = (price / trade["entry"] - 1) * 100
        else:
            pnl = (trade["entry"] / price - 1) * 100

        floating += pnl

    win_rate = (
        wins / len(closed) * 100
        if closed else 0.0
    )

    return (
        f"Trades: {len(trades)} | "
        f"Open: {len(opened)} | Closed: {len(closed)}\n"
        f"Win rate: {win_rate:.1f}%\n"
        f"Realized PnL: {realized:+.2f}%\n"
        f"Floating PnL: {floating:+.2f}%\n"
        f"Total PnL: {realized + floating:+.2f}%"
    )


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def process_symbol(symbol, prices):
    price = prices.get(symbol, 0)

    if price <= 0:
        return "no ticker"

    df = fetch_candles(symbol)

    if df.empty or len(df) < 50:
        return "no candles"

    pivots = find_pivots(df)
    pattern = detect_pattern(pivots)

    if pattern is None:
        return "no pattern"

    if count_open_trades() >= MAX_OPEN_TRADES:
        return "slots full"

    p = pattern["p"]
    side = pattern["side"]

    tp = p[2]["price"]

    if side == "LONG":
        sl = min(p[1]["price"], p[3]["price"])
    else:
        sl = max(p[1]["price"], p[3]["price"])

    # Save signal/trade to DB before attempting Telegram delivery.
    pattern_id = save_signal(symbol, pattern, price)

    if pattern_id is None:
        return "duplicate or invalid entry"

    chart_path = create_chart(
        symbol,
        df,
        pattern,
        price,
        tp,
        sl,
    )

    signal_label = (
        "🟢 LONG"
        if side == "LONG"
        else "🔴 SHORT"
    )

    message = (
        f"{signal_label} | NDS M15 v{VERSION}\n"
        f"Symbol: {symbol}\n"
        f"Entry: {price:.8g}\n"
        f"TP: {tp:.8g}\n"
        f"SL: {sl:.8g}\n"
        f"Pattern: four confirmed pivots\n"
        f"Mode: PAPER ONLY\n"
        f"Time: {utc_now()}"
    )

    telegram_send(message, chart_path)

    return "signal"


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()
    started = time.time()

    try:
        symbols = get_symbols()
        prices = get_prices()

    except Exception as exc:
        log.exception("Could not load Kraken markets.")

        telegram_send(
            f"⚠️ NDS M15 scanner failed\n{exc}"
        )
        raise

    closed_count = update_open_trades(prices)

    scanned = 0
    signals = 0
    errors = 0
    diagnostics = {}

    for symbol in symbols:
        scanned += 1

        try:
            result = process_symbol(symbol, prices)

            diagnostics[result] = (
                diagnostics.get(result, 0) + 1
            )

            if result == "signal":
                signals += 1

        except Exception as exc:
            errors += 1
            diagnostics["errors"] = (
                diagnostics.get("errors", 0) + 1
            )

            log.warning(
                "%s failed: %s",
                symbol,
                exc,
            )

    elapsed = time.time() - started

    report = (
        f"🔎 NDS M15 Four-Pivot v{VERSION}\n"
        f"Time: {utc_now()}\n"
        f"Scanned: {scanned}\n"
        f"Ticker symbols: {len(prices)}\n"
        f"New signals: {signals}\n"
        f"Errors: {errors}\n"
        f"Trades closed this scan: {closed_count}\n"
        f"Open trades: "
        f"{count_open_trades()}/{MAX_OPEN_TRADES}\n"
        f"Diagnostics: {diagnostics}\n\n"
        f"{performance_summary(prices)}\n\n"
        f"Duration: {elapsed:.1f}s\n"
        f"PAPER ONLY. No exchange orders are placed."
    )

    log.info("\n%s", report)
    telegram_send(report)


if __name__ == "__main__":
    main()
