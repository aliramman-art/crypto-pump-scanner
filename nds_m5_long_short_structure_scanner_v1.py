
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M5 FOUR PIVOT LONG + SHORT SCANNER v2.0.1

Kraken Futures | M5 only | OHLC pivots | Paper trading only

LONG:
    H1 -> L1 -> H2 -> L2
    H2 > H1
    L2 > L1
    Entry = close of candle confirming pivot L2
    TP = H2
    SL = L2

SHORT:
    L1 -> H1 -> L2 -> H2
    L2 < L1
    H2 < H1
    Entry = close of candle confirming pivot H2
    TP = L2
    SL = H2

Pivot confirmation:
    First 3 pivots: 5 left + 5 right
    Fourth pivot:   5 left + 2 right

No M1 data.
No Heikin Ashi.
No real exchange orders.
"""

from __future__ import annotations

import os
import time
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "2.0.1"

BASE = "https://futures.kraken.com/derivatives/api/v3"
CHARTS = "https://futures.kraken.com/api/charts/v1"
TELEGRAM = "https://api.telegram.org"

TIMEFRAME = "5m"
CANDLE_MS = 300000

UNIVERSE_SIZE = 100

REQUIRED_SYMBOLS = (
    "PF_XBTUSD",
    "PF_XAUTUSD",
)

M5_LIMIT = int(os.getenv("M5_CANDLE_COUNT", "3000"))

PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", "5"))
PIVOT_RIGHT = int(os.getenv("PIVOT_RIGHT", "5"))

# Only the fourth pivot uses 2 right candles.
PIVOT4_RIGHT = int(os.getenv("PIVOT4_RIGHT", "2"))

MAX_OPEN_TRADES = max(
    1,
    int(os.getenv("MAX_OPEN_TRADES", "3"))
)

MAX_SIGNAL_AGE_MINUTES = int(
    os.getenv("MAX_SIGNAL_AGE_MINUTES", "20")
)

DB_PATH = Path(
    os.getenv(
        "DB_PATH",
        "nds_m5_four_pivot_v2.db"
    )
)

TZ_NAME = os.getenv(
    "REPORT_TIMEZONE",
    "Europe/Berlin"
)

try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:
    TZ = timezone.utc


# ============================================================
# HTTP
# ============================================================

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": f"NDS-M5-FOUR-PIVOT/{VERSION}",
    "Accept": "application/json",
})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)

LOG = logging.getLogger("nds_m5_four_pivot")


# ============================================================
# UTILITIES
# ============================================================

def f(value):
    try:
        value = float(value)

        if value != value or abs(value) == float("inf"):
            return None

        return value

    except (TypeError, ValueError):
        return None


def fmt(value):

    if value is None:
        return "N/A"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    return f"{value:.8f}".rstrip("0").rstrip(".")


def stamp(ms):

    return datetime.fromtimestamp(
        ms / 1000,
        timezone.utc
    ).astimezone(TZ).strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )


def pct(entry, price, direction):

    if not entry or not price:
        return 0.0

    if direction == "LONG":
        return (price / entry - 1) * 100

    return (entry / price - 1) * 100


def api(url, params=None):

    error = None

    for attempt in range(3):

        try:

            response = HTTP.get(
                url,
                params=params,
                timeout=(8, 25)
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise ValueError("Invalid API response")

            return data

        except Exception as exc:

            error = exc

            if attempt < 2:
                time.sleep(0.7 * (attempt + 1))

    raise RuntimeError(
        f"API request failed: {error}"
    )


# ============================================================
# MARKET UNIVERSE
# ============================================================

def get_tickers():

    data = api(f"{BASE}/tickers")

    rows = data.get("tickers")

    if not isinstance(rows, list):
        raise RuntimeError("Invalid Kraken ticker response")

    result = {}

    for item in rows:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get("symbol", "")
        ).upper()

        if symbol:
            result[symbol] = item

    return result


def price_of(ticker):

    if not ticker:
        return None

    bid = (
        f(ticker.get("bid"))
        or f(ticker.get("bidPrice"))
    )

    ask = (
        f(ticker.get("ask"))
        or f(ticker.get("askPrice"))
    )

    if bid and ask and ask >= bid:
        return (bid + ask) / 2

    for key in (
        "markPrice",
        "mark_price",
        "last",
        "lastPrice",
        "lastTradePrice",
        "indexPrice"
    ):

        value = f(ticker.get(key))

        if value and value > 0:
            return value

    return None


def volume_usd(ticker):

    """
    Estimate 24h USD turnover.

    Prefer quote-volume fields.
    If unavailable, use base volume * current price.
    """

    for key in (
        "volumeQuote24h",
        "vol24hQuote",
        "quoteVolume24h",
        "volumeUsd24h",
        "turnover24h",
        "volumeQuote"
    ):

        value = f(ticker.get(key))

        if value is not None and value >= 0:
            return value

    price = price_of(ticker)

    if price is None:
        return 0.0

    for key in (
        "vol24h",
        "volume24h",
        "volume"
    ):

        value = f(ticker.get(key))

        if value is not None and value >= 0:
            return value * price

    return 0.0


def get_universe(ticker_map):

    candidates = []

    for symbol, ticker in ticker_map.items():

        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        if price_of(ticker) is None:
            continue

        volume = volume_usd(ticker)

        candidates.append(
            (symbol, volume)
        )

    candidates.sort(
        key=lambda item: item[1],
        reverse=True
    )

    selected = [
        symbol
        for symbol, _ in candidates[:UNIVERSE_SIZE]
    ]

    for symbol in REQUIRED_SYMBOLS:

        if symbol in ticker_map and symbol not in selected:
            selected.append(symbol)

    return selected


# ============================================================
# M5 CANDLES
# ============================================================

def candles(symbol, limit):

    now = int(time.time())

    data = api(
        f"{CHARTS}/trade/{symbol}/{TIMEFRAME}",
        {
            "from": now - 300 * (limit + 5),
            "to": now,
            "count": limit
        }
    )

    rows = data.get("candles", [])

    if not isinstance(rows, list):
        raise RuntimeError(
            f"Invalid M5 candles for {symbol}"
        )

    by_time = {}

    now_ms = int(time.time() * 1000)

    for row in rows:

        if not isinstance(row, dict):
            continue

        ts = f(row.get("time"))
        op = f(row.get("open"))
        hi = f(row.get("high"))
        lo = f(row.get("low"))
        cl = f(row.get("close"))

        if None in (ts, op, hi, lo, cl):
            continue

        ts = int(ts)

        if ts < 10**12:
            ts *= 1000

        # Ignore unfinished candles.
        if ts + CANDLE_MS > now_ms:
            continue

        if lo > hi:
            continue

        if min(op, hi, lo, cl) <= 0:
            continue

        by_time[ts] = {
            "time": ts,
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": f(row.get("volume")) or 0
        }

    return [
        by_time[key]
        for key in sorted(by_time)
    ][-limit:]


# ============================================================
# PIVOTS
# ============================================================

def pivots(bars, right=None):

    """
    Strict confirmed OHLC pivots.

    Default:
        5 left + 5 right candles.

    The fourth pivot is separately checked with:
        5 left + 2 right candles.
    """

    result = []

    left = PIVOT_LEFT
    right = PIVOT_RIGHT if right is None else right

    for i in range(left, len(bars) - right):

        current = bars[i]

        lo = current["low"]
        hi = current["high"]

        left_bars = bars[i-left:i]
        right_bars = bars[i+1:i+right+1]

        surrounding = left_bars + right_bars

        is_low = all(
            lo < candle["low"]
            for candle in surrounding
        )

        is_high = all(
            hi > candle["high"]
            for candle in surrounding
        )

        if is_low:
            result.append(
                (i, "L", lo)
            )

        if is_high:
            result.append(
                (i, "H", hi)
            )

    result.sort(
        key=lambda item: item[0]
    )

    return result


# ============================================================
# FOUR PIVOT SIGNAL
# ============================================================

def find_signal(symbol, bars, direction):

    """
    LONG:
        H1 -> L1 -> H2 -> L2

    SHORT:
        L1 -> H1 -> L2 -> H2

    First three pivots:
        5 left + 5 right candles.

    Fourth pivot:
        5 left + 2 right candles.

    Entry:
        Close of the second candle after the fourth pivot.
    """

    # First three pivots must have 5 right candles.
    ps = pivots(
        bars,
        right=PIVOT_RIGHT
    )

    # Fourth pivot candidates need only 2 right candles.
    fourth_pivots = pivots(
        bars,
        right=PIVOT4_RIGHT
    )

    if len(ps) < 3 or not fourth_pivots:
        return None

    expected = (
        ["H", "L", "H", "L"]
        if direction == "LONG"
        else ["L", "H", "L", "H"]
    )

    now_ms = int(time.time() * 1000)

    # Search newest three-pivot structures first.
    for j in range(len(ps) - 3, -1, -1):

        first_three = ps[j:j+3]

        if len(first_three) != 3:
            continue

        if [x[1] for x in first_three] != expected[:3]:
            continue

        p1, p2, p3 = first_three

        i1, _, v1 = p1
        i2, _, v2 = p2
        i3, _, v3 = p3

        # The fourth pivot must be the first confirmed pivot
        # after the third pivot, using the 2-right rule.
        candidates = [
            p for p in fourth_pivots
            if p[0] > i3
        ]

        if not candidates:
            continue

        p4 = candidates[0]

        i4, type4, v4 = p4

        if type4 != expected[3]:
            continue

        # The first three pivots must be consecutive
        # in the standard 5-right pivot sequence.
        # The fourth pivot must also be the next pivot
        # after pivot 3 in the 2-right sequence.

        if direction == "LONG":

            # Higher high and higher low.
            if not (
                v3 > v1
                and v4 > v2
            ):
                continue

            tp = v3
            sl = v4

        else:

            # Lower low and lower high.
            if not (
                v3 < v1
                and v4 < v2
            ):
                continue

            tp = v3
            sl = v4

        # Fourth pivot confirmation uses 2 right candles.
        confirmation_index = i4 + PIVOT4_RIGHT

        if confirmation_index >= len(bars):
            continue

        confirmation_bar = bars[confirmation_index]

        entry = float(
            confirmation_bar["close"]
        )

        entry_time = (
            confirmation_bar["time"] + CANDLE_MS
        )

        # Avoid old historical signals.
        age_ms = now_ms - entry_time

        if age_ms < 0:
            continue

        if age_ms > MAX_SIGNAL_AGE_MINUTES * 60 * 1000:
            continue

        # Validate entry location.
        if direction == "LONG":

            if not (
                sl < entry < tp
            ):
                continue

        else:

            if not (
                tp < entry < sl
            ):
                continue

        key = (
            f"{symbol}|{direction}|"
            f"{bars[i1]['time']}|"
            f"{bars[i2]['time']}|"
            f"{bars[i3]['time']}|"
            f"{bars[i4]['time']}"
        )

        return {
            "key": key,
            "symbol": symbol,
            "direction": direction,

            "entry": entry,
            "entry_time": entry_time,

            "tp": float(tp),
            "sl": float(sl),

            "pivot1": float(v1),
            "pivot2": float(v2),
            "pivot3": float(v3),
            "pivot4": float(v4),

            "confirmation_time": entry_time
        }

    return None


# ============================================================
# DATABASE
# ============================================================

def db():

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS setups(
        setup_key TEXT PRIMARY KEY,
        symbol TEXT,
        direction TEXT,
        confirmation_time INTEGER,
        created_at REAL
    );

    CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_key TEXT UNIQUE,
        symbol TEXT,
        direction TEXT,
        entry_time INTEGER,
        entry_price REAL,
        sl_price REAL,
        tp_price REAL,
        status TEXT DEFAULT 'OPEN',
        exit_time INTEGER,
        exit_price REAL,
        pnl_pct REAL,
        last_checked_time INTEGER DEFAULT 0,
        created_at REAL
    );
    """)

    conn.commit()

    return conn


# ============================================================
# TELEGRAM
# ============================================================

def tg_send(message):

    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
    )

    chat = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT")
    )

    if not token or not chat:

        LOG.info(
            "Telegram not configured:\n%s",
            message
        )

        return

    try:

        response = HTTP.post(
            f"{TELEGRAM}/bot{token}/sendMessage",
            data={
                "chat_id": chat,
                "text": message
            },
            timeout=20
        )

        response.raise_for_status()

    except Exception:

        LOG.exception(
            "Telegram send failed"
        )


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def process_trades(conn, m5map):

    closed = []

    open_trades = conn.execute(
        "SELECT * FROM trades WHERE status='OPEN'"
    ).fetchall()

    for trade in open_trades:

        symbol = trade["symbol"]

        bars = m5map.get(symbol)

        if not bars:
            continue

        direction = trade["direction"]

        entry_time = int(
            trade["entry_time"]
        )

        sl = float(
            trade["sl_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        last_checked = int(
            trade["last_checked_time"] or 0
        )

        for bar in bars:

            # Only candles after entry.
            if bar["time"] < entry_time:
                continue

            if bar["time"] <= last_checked:
                continue

            if direction == "LONG":

                hit_sl = bar["low"] <= sl
                hit_tp = bar["high"] >= tp

            else:

                hit_sl = bar["high"] >= sl
                hit_tp = bar["low"] <= tp

            # Conservative rule:
            # If TP and SL are touched in the same candle,
            # count SL first because OHLC cannot reveal the order.
            if hit_sl or hit_tp:

                if hit_sl:

                    status = "CLOSED_SL"
                    exit_price = sl

                else:

                    status = "CLOSED_TP"
                    exit_price = tp

                pnl = pct(
                    float(trade["entry_price"]),
                    exit_price,
                    direction
                )

                exit_time = (
                    bar["time"] + CANDLE_MS
                )

                conn.execute(
                    """
                    UPDATE trades
                    SET status=?,
                        exit_time=?,
                        exit_price=?,
                        pnl_pct=?,
                        last_checked_time=?
                    WHERE id=?
                    """,
                    (
                        status,
                        exit_time,
                        exit_price,
                        pnl,
                        bar["time"],
                        trade["id"]
                    )
                )

                closed.append({
                    "symbol": symbol,
                    "direction": direction,
                    "status": status,
                    "entry": float(trade["entry_price"]),
                    "exit": exit_price,
                    "pnl": pnl
                })

                break

            last_checked = bar["time"]

            conn.execute(
                """
                UPDATE trades
                SET last_checked_time=?
                WHERE id=?
                """,
                (
                    last_checked,
                    trade["id"]
                )
            )

    conn.commit()

    return closed


# ============================================================
# MAIN
# ============================================================

def main():

    conn = db()

    errors = []
    diagnostics = []
    new_trades = []

    m5map = {}
    prices = {}

    try:

        ticker_map = get_tickers()

        universe = get_universe(
            ticker_map
        )

        LOG.info(
            "Universe size: %s",
            len(universe)
        )

        # ----------------------------------------------------
        # Scan M5 symbols
        # ----------------------------------------------------

        for symbol in universe:

            try:

                bars = candles(
                    symbol,
                    M5_LIMIT
                )

                if len(bars) < (
                    PIVOT_LEFT + PIVOT_RIGHT + 20
                ):

                    raise RuntimeError(
                        "Not enough M5 candles"
                    )

                m5map[symbol] = bars

                prices[symbol] = (
                    price_of(
                        ticker_map.get(symbol)
                    )
                    or bars[-1]["close"]
                )

                for direction in (
                    "LONG",
                    "SHORT"
                ):

                    signal = find_signal(
                        symbol,
                        bars,
                        direction
                    )

                    if not signal:
                        continue

                    # Do not repeat the same structure.
                    exists = conn.execute(
                        """
                        SELECT 1 FROM trades
                        WHERE trade_key=?
                        """,
                        (signal["key"],)
                    ).fetchone()

                    if exists:
                        continue

                    # Record detected structure.
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO setups(
                            setup_key,
                            symbol,
                            direction,
                            confirmation_time,
                            created_at
                        )
                        VALUES(?,?,?,?,?)
                        """,
                        (
                            signal["key"],
                            symbol,
                            direction,
                            signal["confirmation_time"],
                            time.time()
                        )
                    )

                    open_count = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM trades
                        WHERE status='OPEN'
                        """
                    ).fetchone()[0]

                    if open_count >= MAX_OPEN_TRADES:

                        diagnostics.append(
                            f"{symbol} {direction}: "
                            "maximum open trades reached"
                        )

                        continue

                    # Insert paper trade.
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO trades(
                            trade_key,
                            symbol,
                            direction,
                            entry_time,
                            entry_price,
                            sl_price,
                            tp_price,
                            status,
                            last_checked_time,
                            created_at
                        )
                        VALUES(
                            ?,?,?,?,?,?,?,
                            'OPEN',0,?
                        )
                        """,
                        (
                            signal["key"],
                            symbol,
                            direction,
                            signal["entry_time"],
                            signal["entry"],
                            signal["sl"],
                            signal["tp"],
                            time.time()
                        )
                    )

                    if cur.rowcount == 1:

                        new_trades.append(
                            signal
                        )

                conn.commit()

            except Exception as exc:

                LOG.exception(
                    "Scan failed for %s",
                    symbol
                )

                errors.append(
                    f"{symbol}: {exc}"
                )

        # ----------------------------------------------------
        # Manage open trades
        # ----------------------------------------------------

        closed = process_trades(
            conn,
            m5map
        )

        # ----------------------------------------------------
        # Statistics
        # ----------------------------------------------------

        opens = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE status='OPEN'
            ORDER BY entry_time
            """
        ).fetchall()

        done = conn.execute(
            """
            SELECT pnl_pct,status
            FROM trades
            WHERE status LIKE 'CLOSED_%'
            """
        ).fetchall()

        wins = sum(
            1 for item in done
            if item["status"] == "CLOSED_TP"
        )

        losses = len(done) - wins

        realized = sum(
            float(item["pnl_pct"] or 0)
            for item in done
        )

        floating = sum(
            pct(
                float(item["entry_price"]),
                prices.get(item["symbol"]),
                item["direction"]
            )
            for item in opens
            if prices.get(item["symbol"]) is not None
        )

        win_rate = (
            wins / len(done) * 100
            if done else 0
        )

        # ----------------------------------------------------
        # REPORT
        # ----------------------------------------------------

        lines = [

            (
                f"NDS M5 FOUR PIVOT "
                f"v{VERSION} | "
                f"{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}"
            ),

            (
                f"Universe: {len(universe)} top USD perpetuals "
                "+ required BTC/XAU"
            ),

            (
                "Timeframe: M5 | "
                f"First 3 pivots: L/R {PIVOT_LEFT}/{PIVOT_RIGHT} | "
                f"Fourth pivot: L/R {PIVOT_LEFT}/{PIVOT4_RIGHT} | "
                "M1: OFF | HA: OFF | Paper only"
            ),

            (
                f"Symbols scanned: {len(m5map)} | "
                f"New signals: {len(new_trades)} | "
                f"Open: {len(opens)}/{MAX_OPEN_TRADES}"
            )
        ]

        # New signals
        for signal in new_trades:

            direction = signal["direction"]

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} NEW {direction} {signal['symbol']}"
            )

            lines.append(
                f"Entry: {fmt(signal['entry'])}"
            )

            lines.append(
                f"TP: {fmt(signal['tp'])}"
            )

            lines.append(
                f"SL: {fmt(signal['sl'])}"
            )

            lines.append(
                f"Confirmed: {stamp(signal['confirmation_time'])}"
            )

            lines.append(
                "--------------------"
            )

        if not new_trades:
            lines.append(
                "No new confirmed four-pivot signals."
            )

        # Closed trades
        for item in closed:

            lines.append(
                f"CLOSED {item['status']} "
                f"{item['symbol']} {item['direction']} | "
                f"Entry {fmt(item['entry'])} | "
                f"Exit {fmt(item['exit'])} | "
                f"PnL {item['pnl']:+.2f}%"
            )

        # Open trades
        lines.append("")
        lines.append("OPEN TRADES:")

        for trade in opens:

            current = prices.get(
                trade["symbol"]
            )

            current_pnl = (
                pct(
                    float(trade["entry_price"]),
                    current,
                    trade["direction"]
                )
                if current is not None
                else 0.0
            )

            lines.append(
                f"{trade['symbol']} {trade['direction']} | "
                f"Entry {fmt(trade['entry_price'])} | "
                f"TP {fmt(trade['tp_price'])} | "
                f"SL {fmt(trade['sl_price'])} | "
                f"Now {fmt(current)} | "
                f"PnL {current_pnl:+.2f}%"
            )

        if not opens:
            lines.append("None")

        lines += [

            "",

            (
                f"Performance: Closed {len(done)} | "
                f"W/L {wins}/{losses} | "
                f"Win rate {win_rate:.2f}%"
            ),

            (
                f"Realized PnL: {realized:+.2f}% | "
                f"Floating PnL: {floating:+.2f}% | "
                f"Total: {realized + floating:+.2f}%"
            ),

            (
                "LONG: TP=H2, SL=L2 | "
                "SHORT: TP=L2, SL=H2"
            ),

            (
                "Entry: close of the M5 candle "
                "confirming the fourth pivot after 2 right candles."
            ),

            "No M1 confirmation. No real orders."
        ]

        if errors:

            lines.append("")
            lines.append(
                "Errors: " + " | ".join(errors[:15])
            )

        if diagnostics:

            lines.append("")
            lines.append(
                "Diagnostics: "
                + " | ".join(diagnostics[:10])
            )

        report = "\n".join(lines)

        LOG.info("\n%s", report)

        tg_send(report)

        return 0

    finally:

        conn.close()


if __name__ == "__main__":

    raise SystemExit(main())
