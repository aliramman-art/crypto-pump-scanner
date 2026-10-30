#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M5 Long + Short Structure Scanner v1.3.1

LONG + SHORT mirrored signal/paper-trade scanner. It never places real exchange orders.

Pattern and entry (all pivots confirmed on closed M5 candles):
  SHORT: bullish base 1 GREEN -> 1 RED -> 2 GREEN higher high -> 2 RED lower low;
         then wait for a confirmed new high above 2 GREEN. Enter SHORT at current
         market price after that new high is confirmed. TP = 2 RED; SL = nearest
         confirmed intact M5 swing high above entry (2-left/2-right).
  LONG:  mirrored bearish base 1 RED -> 1 GREEN -> 2 RED lower low -> 2 GREEN higher high;
         then wait for a confirmed new low below 2 RED. Enter LONG at current market
         price after that new low is confirmed. TP = 2 GREEN; SL = nearest confirmed
         intact M5 swing low below entry (2-left/2-right).

Reporting:
  - Sends a PNG chart for each new signal to Telegram.
  - Reports new signals, open trades with entry/TP/SL and percentage distances,
    closed-trade win rate, realized PnL, floating PnL, and total PnL.
  - PnL is gross, equal-notional percentage-point PnL (fees/slippage excluded).

Environment variables:
  TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID or TELEGRAM_CHAT
  UNIVERSE_SIZE (default 100)
  MAX_OPEN_TRADES (default 3)
  MAX_SIGNAL_AGE_HOURS (default 6)
  STOP_BUFFER_PCT (default 0.10, percent beyond swing stop)
  DB_PATH (default nds_m5_long_short_structure_v1.db)
  CHART_DIR (default nds_m5_long_short_structure_charts)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
except Exception as exc:  # Let startup explain the missing chart dependency clearly.
    raise RuntimeError("matplotlib is required; install it with: pip install matplotlib") from exc

# ----------------------------- Configuration -----------------------------

VERSION = "1.3.1"
FUTURES_REST = "https://futures.kraken.com/derivatives/api/v3"
CHARTS_REST = "https://futures.kraken.com/api/charts/v1"
TELEGRAM_API = "https://api.telegram.org"

UNIVERSE_SIZE = max(2, int(os.getenv("UNIVERSE_SIZE", "100")))
MAX_OPEN_TRADES = max(1, int(os.getenv("MAX_OPEN_TRADES", "3")))
MAX_SIGNAL_AGE_HOURS = max(1, int(os.getenv("MAX_SIGNAL_AGE_HOURS", "6")))
M5_CANDLE_COUNT = 300
PIVOT_LEFT = 5
PIVOT_RIGHT = 5
M5_PIVOT_LEFT = 5
M5_PIVOT_RIGHT = 5
STOP_BUFFER_PCT = max(0.0, float(os.getenv("STOP_BUFFER_PCT", "0.10")))
MAX_CONCURRENT_REQUESTS = 8
MAX_CHARTS_PER_RUN = 8
REQUIRED_SYMBOLS = ("PF_XBTUSD", "PF_XAUTUSD")
DB_PATH = Path(os.getenv("DB_PATH", "nds_m5_long_short_structure_v1.db"))
CHART_DIR = Path(os.getenv("CHART_DIR", "nds_m5_long_short_structure_charts"))
REPORT_TZ_NAME = os.getenv("REPORT_TIMEZONE", "Europe/Berlin")
try:
    REPORT_TZ = ZoneInfo(REPORT_TZ_NAME)
except Exception:
    REPORT_TZ = timezone.utc

M5_MS = 5 * 60 * 1000
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": f"NDS-M5-Structure-Scanner/{VERSION}", "Accept": "application/json"})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("nds_m5_long_short")


# ----------------------------- Generic helpers -----------------------------

def as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        if not math.isfinite(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:,.5f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "N/A"
    if signed:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def dt_text(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone(REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def candle_epoch_seconds(candle: dict[str, Any]) -> float:
    return float(candle["time"]) / 1000.0


def request_json(url: str, params: Optional[dict[str, Any]] = None, retries: int = 3) -> dict[str, Any]:
    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            response = HTTP.get(url, params=params, timeout=(8, 25))
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"Unexpected JSON shape from {url}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"Request failed for {url}: {last_exc}")


def get_tickers() -> list[dict[str, Any]]:
    payload = request_json(f"{FUTURES_REST}/tickers")
    tickers = payload.get("tickers")
    if not isinstance(tickers, list):
        raise RuntimeError(f"Kraken tickers response has no ticker list: {json.dumps(payload)[:500]}")
    return [item for item in tickers if isinstance(item, dict)]


def extract_volume(ticker: dict[str, Any]) -> float:
    # Kraken ticker field names can vary by contract/response generation.
    for key in ("vol24h", "volume24h", "volume", "volume24H", "turnover24h", "usdVolume24h"):
        value = as_float(ticker.get(key))
        if value is not None and value >= 0:
            return value
    return 0.0


def get_universe(tickers: list[dict[str, Any]]) -> list[str]:
    rows: list[tuple[str, float]] = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        # Keep USD-settled perpetual symbols for consistent cross-market behavior.
        if symbol.startswith("PF_") and symbol.endswith("USD"):
            rows.append((symbol, extract_volume(ticker)))
    rows.sort(key=lambda row: (row[1], row[0]), reverse=True)

    selected: list[str] = []
    for required in REQUIRED_SYMBOLS:
        selected.append(required)
    for symbol, _volume in rows:
        if symbol not in selected:
            selected.append(symbol)
        if len(selected) >= UNIVERSE_SIZE:
            break
    return selected[:UNIVERSE_SIZE] if len(selected) >= UNIVERSE_SIZE else selected


def current_price_from_ticker(ticker: Optional[dict[str, Any]]) -> Optional[float]:
    if not ticker:
        return None
    bid = as_float(ticker.get("bid")) or as_float(ticker.get("bidPrice"))
    ask = as_float(ticker.get("ask")) or as_float(ticker.get("askPrice"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2.0
    for key in ("markPrice", "mark_price", "last", "lastPrice", "lastTradePrice", "indexPrice", "index_price"):
        value = as_float(ticker.get(key))
        if value is not None and value > 0:
            return value
    return None


def ticker_map(tickers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("symbol", "")).upper(): item for item in tickers if item.get("symbol")}


# ----------------------------- Candle data -----------------------------

def get_candles(symbol: str, resolution: str, limit: int) -> list[dict[str, Any]]:
    """Fetch OHLC candles and remove the currently-open candle."""
    now = time.time()
    timeframe_ms = M5_MS
    timeframe_seconds = timeframe_ms / 1000
    params = {
        "from": int(now - timeframe_seconds * (limit + 5)),
        "to": int(now),
        "count": limit,
    }
    payload = request_json(f"{CHARTS_REST}/trade/{symbol}/{resolution}", params=params)
    rows = payload.get("candles", [])
    if not isinstance(rows, list):
        raise RuntimeError(f"Invalid candles shape for {symbol} {resolution}")

    by_time: dict[int, dict[str, Any]] = {}
    now_ms = int(time.time() * 1000)
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = as_float(row.get("time"))
        opn = as_float(row.get("open"))
        high = as_float(row.get("high"))
        low = as_float(row.get("low"))
        close = as_float(row.get("close"))
        if None in (ts, opn, high, low, close):
            continue
        ts_i = int(ts)
        if ts_i < 1_000_000_000_000:  # Defensive support for second timestamps.
            ts_i *= 1000
        # Only use a candle after its interval has ended.
        if ts_i + timeframe_ms > now_ms:
            continue
        if low > high or min(opn, high, low, close) <= 0:
            continue
        by_time[ts_i] = {
            "time": ts_i,
            "open": float(opn),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": as_float(row.get("volume")) or 0.0,
        }
    return [by_time[key] for key in sorted(by_time)][-limit:]


def is_pivot_low(candles: list[dict[str, Any]], index: int, left: int, right: int) -> bool:
    if index < left or index + right >= len(candles):
        return False
    value = candles[index]["low"]
    left_lows = [candles[j]["low"] for j in range(index - left, index)]
    right_lows = [candles[j]["low"] for j in range(index + 1, index + right + 1)]
    return value < min(left_lows) and value < min(right_lows)


def is_pivot_high(candles: list[dict[str, Any]], index: int, left: int, right: int) -> bool:
    if index < left or index + right >= len(candles):
        return False
    value = candles[index]["high"]
    left_highs = [candles[j]["high"] for j in range(index - left, index)]
    right_highs = [candles[j]["high"] for j in range(index + 1, index + right + 1)]
    return value > max(left_highs) and value > max(right_highs)


def find_confirmed_pivots(
    candles: list[dict[str, Any]], left: int, right: int
) -> tuple[list[int], list[int]]:
    lows: list[int] = []
    highs: list[int] = []
    for i in range(left, len(candles) - right):
        if is_pivot_low(candles, i, left, right):
            lows.append(i)
        if is_pivot_high(candles, i, left, right):
            highs.append(i)
    return lows, highs


# ----------------------------- Strategy detection -----------------------------

def pattern_key(symbol: str, candles: list[dict[str, Any]], points: dict[str, int], direction: str) -> str:
    timestamps = [str(candles[points[name]]["time"]) for name in ("start", "p1", "p2", "p3", "p4", "p5")]
    return f"{direction}|{symbol}|M5|" + "|".join(timestamps)


def detect_patterns(symbol: str, candles: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    """Detect the requested six-point M5 structure with confirmed 5x5 pivots.

    SHORT: START low -> H1 -> L1 -> H2 -> L2 -> H3.
    LONG:  START high -> L1 -> H1 -> L2 -> H2 -> L3 (exact mirror).
    Adjacent pivots of the same type are compressed to the more extreme pivot,
    so each leg uses the relevant swing extreme rather than an arbitrary pivot.
    """
    if len(candles) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        return []
    lows, highs = find_confirmed_pivots(candles, PIVOT_LEFT, PIVOT_RIGHT)
    if not lows or not highs:
        return []
    raw = sorted([(i, "L") for i in lows] + [(i, "H") for i in highs])
    swings: list[tuple[int, str]] = []
    for item in raw:
        if not swings or swings[-1][1] != item[1]:
            swings.append(item)
        else:
            old_i, kind = swings[-1]
            new_i = item[0]
            more_extreme = (candles[new_i]["low"] < candles[old_i]["low"]) if kind == "L" else (candles[new_i]["high"] > candles[old_i]["high"])
            if more_extreme:
                swings[-1] = item
    patterns: list[dict[str, Any]] = []
    wanted = ["L", "H", "L", "H", "L", "H"] if direction == "SHORT" else ["H", "L", "H", "L", "H", "L"]
    for j in range(0, len(swings) - 5):
        seq = swings[j:j + 6]
        if [kind for _, kind in seq] != wanted:
            continue
        idx = [i for i, _ in seq]
        start_i, p1_i, p2_i, p3_i, p4_i, p5_i = idx
        start_price = candles[start_i]["low"] if direction == "SHORT" else candles[start_i]["high"]
        p1 = candles[p1_i]["high"] if direction == "SHORT" else candles[p1_i]["low"]
        p2 = candles[p2_i]["low"] if direction == "SHORT" else candles[p2_i]["high"]
        p3 = candles[p3_i]["high"] if direction == "SHORT" else candles[p3_i]["low"]
        p4 = candles[p4_i]["low"] if direction == "SHORT" else candles[p4_i]["high"]
        p5 = candles[p5_i]["high"] if direction == "SHORT" else candles[p5_i]["low"]
        if direction == "SHORT":
            valid = (p1 > start_price and p2 > start_price and p3 > p1 and p4 > start_price and p4 < p2 and p5 > p1 and p5 > p3)
            # H1/H2 must be the highest prices on their respective full legs;
            # L1/L2 must be the lowest prices on their respective full legs.
            valid = valid and max(c["high"] for c in candles[start_i:p2_i + 1]) == p1
            valid = valid and min(c["low"] for c in candles[p1_i:p3_i + 1]) == p2
            valid = valid and max(c["high"] for c in candles[p2_i:p4_i + 1]) == p3
            valid = valid and min(c["low"] for c in candles[p3_i:p5_i + 1]) == p4
            valid = valid and min(c["low"] for c in candles[start_i:p5_i + 1]) == start_price
        else:
            # LONG mirror: L1 > L2 > L3 and H2 > H1.
            # p1=L1, p2=H1, p3=L2, p4=H2, p5=L3.
            # Critical guard: the second green high MUST exceed the first green high.
            valid = (p1 < start_price and p2 < start_price and p3 < p1 and p4 < start_price and p4 > p2 and p5 < p1 and p5 < p3)
            valid = valid and min(c["low"] for c in candles[start_i:p2_i + 1]) == p1
            valid = valid and max(c["high"] for c in candles[p1_i:p3_i + 1]) == p2
            valid = valid and min(c["low"] for c in candles[p2_i:p4_i + 1]) == p3
            valid = valid and max(c["high"] for c in candles[p3_i:p5_i + 1]) == p4
            valid = valid and max(c["high"] for c in candles[start_i:p5_i + 1]) == start_price
        if not valid or p5_i + PIVOT_RIGHT >= len(candles):
            continue
        pts = {"start": start_i, "p1": p1_i, "p2": p2_i, "p3": p3_i, "p4": p4_i, "p5": p5_i}
        patterns.append({
            "symbol": symbol, "direction": direction, "points": pts,
            "key": pattern_key(symbol, candles, pts, direction),
            "confirmation_time": (candles[p5_i + PIVOT_RIGHT]["time"] + M5_MS) / 1000.0,
            "start_price": start_price, "p1_price": p1, "p2_price": p2,
            "p3_price": p3, "p4_price": p4, "p5_price": p5,
            "terminal_time": candles[p5_i]["time"] / 1000.0,
        })
    # Return every valid structure, newest first. Returning only patterns[0]
    # can hide a newer eligible setup when the newest match is already expired
    # or recorded in SQLite.
    patterns.sort(key=lambda item: item["confirmation_time"], reverse=True)
    return patterns

def find_nearest_valid_m15_low(candles15: list[dict[str, Any]], entry: float) -> Optional[float]:
    """Closest intact confirmed M5 swing low below entry, with a small buffer applied later."""
    lows, _ = find_confirmed_pivots(candles15, M5_PIVOT_LEFT, M5_PIVOT_RIGHT)
    eligible = []
    for i in lows:
        price = candles15[i]["low"]
        if price < entry and not any(b["low"] <= price for b in candles15[i + 1:]):
            eligible.append((price, i))
    return max(eligible, key=lambda item: item[0])[0] if eligible else None


def find_nearest_valid_m15_high(candles15: list[dict[str, Any]], entry: float) -> Optional[float]:
    """Closest intact confirmed M5 swing high above entry, with a small buffer applied later."""
    _, highs = find_confirmed_pivots(candles15, M5_PIVOT_LEFT, M5_PIVOT_RIGHT)
    eligible = []
    for i in highs:
        price = candles15[i]["high"]
        if price > entry and not any(b["high"] >= price for b in candles15[i + 1:]):
            eligible.append((price, i))
    return min(eligible, key=lambda item: item[0])[0] if eligible else None


# ----------------------------- Database -----------------------------

def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS patterns (
            pattern_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            detected_at REAL NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            entry_time REAL NOT NULL,
            next_bar_time_ms INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_time REAL,
            exit_price REAL,
            pnl_pct REAL,
            created_at REAL NOT NULL,
            FOREIGN KEY(pattern_key) REFERENCES patterns(pattern_key)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol, status);
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "direction" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN direction TEXT NOT NULL DEFAULT 'LONG'")
    conn.commit()
    return conn


def pattern_seen(conn: sqlite3.Connection, key: str) -> bool:
    # A pattern is permanently consumed only after a paper trade was opened.
    # Old SKIPPED decisions (for example, temporarily full trade slots) must
    # not block a still-valid pattern from being evaluated again.
    row = conn.execute(
        "SELECT 1 FROM patterns p LEFT JOIN trades t ON t.pattern_key=p.pattern_key "
        "WHERE p.pattern_key=? AND (p.decision='TRADE' OR t.pattern_key IS NOT NULL) LIMIT 1",
        (key,),
    ).fetchone()
    return row is not None


def save_pattern_decision(conn: sqlite3.Connection, pattern: dict[str, Any], decision: str, reason: str = "") -> None:
    conn.execute(
        """INSERT INTO patterns(pattern_key,symbol,detected_at,decision,reason)
           VALUES(?,?,?,?,?)
           ON CONFLICT(pattern_key) DO UPDATE SET
               decision=excluded.decision,
               reason=excluded.reason
           WHERE patterns.decision != 'TRADE'""",
        (pattern["key"], pattern["symbol"], time.time(), decision, reason[:500]),
    )
    conn.commit()


def open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time ASC"))


def insert_trade(
    conn: sqlite3.Connection,
    pattern: dict[str, Any],
    entry_time: float,
    entry_price: float,
    tp: float,
    sl: float,
) -> None:
    direction = str(pattern.get("direction", "LONG"))
    entry_ms = int(entry_time * 1000)
    next_bar_time_ms = ((entry_ms // M5_MS) + 1) * M5_MS
    conn.execute(
        """INSERT INTO trades
        (pattern_key,symbol,direction,entry_time,next_bar_time_ms,entry_price,tp_price,sl_price,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,'OPEN',?)""",
        (pattern["key"], pattern["symbol"], direction, entry_time, next_bar_time_ms, entry_price, tp, sl, time.time()),
    )
    conn.commit()


def close_trade(conn: sqlite3.Connection, trade_id: int, status: str, exit_time: float, exit_price: float) -> None:
    row = conn.execute("SELECT entry_price,direction FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        return
    entry = float(row["entry_price"])
    pnl = ((exit_price / entry - 1.0) if row["direction"] == "LONG" else (entry / exit_price - 1.0)) * 100.0
    conn.execute(
        "UPDATE trades SET status=?, exit_time=?, exit_price=?, pnl_pct=? WHERE id=? AND status='OPEN'",
        (status, exit_time, exit_price, pnl, trade_id),
    )
    conn.commit()


def trade_pnl_percent(entry: float, price: Optional[float], direction: str = "LONG") -> Optional[float]:
    if price is None or entry <= 0 or price <= 0:
        return None
    return ((price / entry - 1.0) if direction == "LONG" else (entry / price - 1.0)) * 100.0


# ----------------------------- Charting -----------------------------

def create_signal_chart(symbol: str, candles: list[dict[str, Any]], pattern: dict[str, Any], entry: float, tp: float, sl: float, current_price: Optional[float] = None, chart_title: str = "NEW SIGNAL") -> Path:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    pts = pattern["points"]; direction = pattern["direction"]
    left_edge = max(0, min(min(pts.values()) - 12, len(candles) - 175))
    chart_candles = candles[left_edge:]
    if len(chart_candles) < 10:
        chart_candles = candles[-100:]; left_edge = len(candles) - len(chart_candles)
    fig, ax = plt.subplots(figsize=(13, 7), dpi=135)
    for x, c in enumerate(chart_candles):
        color = "#18a558" if c["close"] >= c["open"] else "#e74c3c"
        ax.vlines(x, c["low"], c["high"], color=color, linewidth=1.1, zorder=2)
        body_low=min(c["open"],c["close"]); body_h=max(abs(c["close"]-c["open"]),abs(c["close"])*0.000002)
        ax.add_patch(Rectangle((x-0.31,body_low),0.62,body_h,facecolor=color,edgecolor=color,linewidth=0.6,zorder=3))
    if direction == "LONG":
        style={"start":("START","#277da1","o",candles[pts["start"]]["high"]),"p1":("1 RED","#db2b39","v",candles[pts["p1"]]["low"]),"p2":("1 GREEN","#149447","^",candles[pts["p2"]]["high"]),"p3":("2 RED","#db2b39","v",candles[pts["p3"]]["low"]),"p4":("2 GREEN / TP","#149447","^",candles[pts["p4"]]["high"]),"p5":("3 RED / TRIGGER","#db2b39","v",candles[pts["p5"]]["low"])}
    else:
        style={"start":("START","#277da1","o",candles[pts["start"]]["low"]),"p1":("1 GREEN","#149447","^",candles[pts["p1"]]["high"]),"p2":("1 RED","#db2b39","v",candles[pts["p2"]]["low"]),"p3":("2 GREEN","#149447","^",candles[pts["p3"]]["high"]),"p4":("2 RED / TP","#db2b39","v",candles[pts["p4"]]["low"]),"p5":("3 GREEN / TRIGGER","#149447","^",candles[pts["p5"]]["high"])}
    for name,(label,color,marker,y) in style.items():
        x=pts[name]-left_edge
        if 0 <= x < len(chart_candles):
            ax.scatter([x],[y],s=75,marker=marker,color=color,edgecolors="white",linewidths=0.7,zorder=5)
            off=9 if marker in ("^","o") else -14
            ax.annotate(label,(x,y),xytext=(0,off),textcoords="offset points",fontsize=8,color=color,fontweight="bold",ha="center",va="bottom" if off>0 else "top",bbox={"boxstyle":"round,pad=0.18","fc":"white","ec":color,"alpha":0.86,"lw":0.65})
    last_x=len(chart_candles)-1
    ax.axhline(tp,color="#149447",linestyle="--",linewidth=1.25,label=f"TP {fmt_price(tp)}")
    ax.axhline(sl,color="#db2b39",linestyle="--",linewidth=1.25,label=f"SL {fmt_price(sl)}")
    ax.axhline(entry,color="#277da1",linestyle=":",linewidth=1.5,label=f"ENTRY {fmt_price(entry)}")
    ax.scatter([last_x],[entry],color="#277da1",marker="D",s=68,edgecolors="white",linewidths=0.7,zorder=6)
    ax.annotate("ENTRY",(last_x,entry),xytext=(-8,8),textcoords="offset points",ha="right",fontsize=8,color="#277da1",fontweight="bold")
    if current_price is not None and current_price > 0:
        ax.axhline(current_price,color="#8e44ad",linestyle="-.",linewidth=1.35,label=f"NOW {fmt_price(current_price)}")
        ax.scatter([last_x],[current_price],color="#8e44ad",marker="o",s=55,edgecolors="white",linewidths=0.7,zorder=7)
    n=min(8,len(chart_candles)); ticks=sorted(set(round(i*(len(chart_candles)-1)/max(1,n-1)) for i in range(n)))
    ax.set_xticks(ticks); ax.set_xticklabels([datetime.fromtimestamp(chart_candles[i]["time"]/1000,timezone.utc).astimezone(REPORT_TZ).strftime("%m-%d %H:%M") for i in ticks],rotation=25,ha="right",fontsize=8)
    ax.set_title(f"NDS M5 {direction} | {symbol} | {chart_title}\nConfirmed mirrored structure | {dt_text(pattern['confirmation_time'])}",fontsize=12,fontweight="bold")
    ax.set_ylabel("Price"); ax.grid(True,alpha=0.2); ax.legend(loc="best",fontsize=8,framealpha=0.9); ax.margins(x=0.03,y=0.1); fig.tight_layout()
    safe="".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol)
    path=CHART_DIR/f"{safe}_{direction}_{int(time.time())}.png"; fig.savefig(path,bbox_inches="tight"); plt.close(fig); return path


def restore_trade_pattern(trade: sqlite3.Row, candles: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Restore the six structural pivot indices saved in a trade's pattern_key."""
    parts = str(trade["pattern_key"]).split("|")
    if len(parts) != 9 or parts[0] not in ("LONG", "SHORT"):
        LOG.warning("Cannot restore structure for trade %s: invalid pattern_key", trade["id"])
        return None
    try:
        timestamps = [int(value) for value in parts[3:9]]
    except (TypeError, ValueError):
        LOG.warning("Cannot restore structure for trade %s: invalid timestamps", trade["id"])
        return None
    index_by_time = {int(candle["time"]): i for i, candle in enumerate(candles)}
    if not all(ts in index_by_time for ts in timestamps):
        LOG.warning("Cannot restore all six pivots for open trade %s (%s); chart skipped", trade["id"], trade["symbol"])
        return None
    points = dict(zip(("start", "p1", "p2", "p3", "p4", "p5"), [index_by_time[ts] for ts in timestamps]))
    return {
        "symbol": str(trade["symbol"]),
        "direction": str(trade["direction"]),
        "points": points,
        "confirmation_time": (timestamps[-1] + (PIVOT_RIGHT + 1) * M5_MS) / 1000.0,
    }


def send_open_trade_charts(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    prices: dict[str, Optional[float]],
) -> tuple[int, int]:
    """Send a fresh chart for every trade that remains OPEN in SQLite."""
    sent = 0
    attempted = 0
    for trade in open_trades(conn):
        symbol = str(trade["symbol"])
        bars = candles_by_symbol.get(symbol)
        if not bars:
            try:
                bars = get_candles(symbol, "5m", M5_CANDLE_COUNT)
                candles_by_symbol[symbol] = bars
            except Exception as exc:
                LOG.warning("Cannot fetch chart candles for open trade %s: %s", symbol, exc)
                continue
        if len(bars) < 10:
            LOG.warning("Not enough candles to chart open trade %s", trade["id"])
            continue
        pattern = restore_trade_pattern(trade, bars)
        if pattern is None:
            continue
        current = prices.get(symbol)
        if current is None or current <= 0:
            current = float(bars[-1]["close"])
            prices[symbol] = current
        entry = float(trade["entry_price"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        direction = str(trade["direction"])
        floating = trade_pnl_percent(entry, current, direction)
        try:
            chart_path = create_signal_chart(
                symbol, bars, pattern, entry, tp, sl,
                current_price=current, chart_title="OPEN TRADE UPDATE",
            )
            emoji = "🟢" if direction == "LONG" else "🔴"
            caption = (
                f"{emoji} OPEN {direction} | {symbol} | Trade #{trade['id']}\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Current: {fmt_price(current)} | Floating PnL: {fmt_pct(floating)}\n"
                f"TP: {fmt_price(tp)} ({fmt_pct(pct_from_entry(entry, tp, direction))})\n"
                f"SL: {fmt_price(sl)} ({fmt_pct(pct_from_entry(entry, sl, direction))})\n"
                "Chart: START, pivots 1/2, trigger, ENTRY, TP, SL and current price"
            )
            attempted += 1
            if telegram_send_photo(chart_path, caption):
                sent += 1
        except Exception:
            LOG.exception("Open-trade chart/send failed for %s trade #%s", symbol, trade["id"])
    return sent, attempted


# ----------------------------- Telegram -----------------------------

def telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")
    return token, chat_id


def telegram_send_message(text: str) -> bool:
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        LOG.warning("Telegram credentials missing; message not sent")
        return False
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    # Keep comfortably below Telegram's message length limit.
    chunks: list[str] = []
    remaining = text
    while len(remaining) > 3800:
        split_at = remaining.rfind("\n", 0, 3800)
        if split_at < 1000:
            split_at = 3800
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    ok = True
    for chunk in chunks:
        try:
            response = HTTP.post(url, data={"chat_id": chat_id, "text": chunk}, timeout=(8, 25))
            if response.status_code >= 400 or not response.json().get("ok", False):
                LOG.error("Telegram sendMessage failed: HTTP %s %s", response.status_code, response.text[:300])
                ok = False
        except Exception:
            LOG.exception("Telegram sendMessage error")
            ok = False
    return ok


def telegram_send_photo(path: Path, caption: str) -> bool:
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        LOG.warning("Telegram credentials missing; photo not sent")
        return False
    url = f"{TELEGRAM_API}/bot{token}/sendPhoto"
    try:
        with path.open("rb") as photo_file:
            response = HTTP.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"photo": (path.name, photo_file, "image/png")},
                timeout=(10, 45),
            )
        if response.status_code >= 400 or not response.json().get("ok", False):
            LOG.error("Telegram sendPhoto failed: HTTP %s %s", response.status_code, response.text[:300])
            return False
        return True
    except Exception:
        LOG.exception("Telegram sendPhoto error")
        return False


# ----------------------------- Trade management -----------------------------

def update_open_trades(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    live_prices: Optional[dict[str, Optional[float]]] = None,
) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    for trade in open_trades(conn):
        symbol = str(trade["symbol"])
        bars = candles_by_symbol.get(symbol)
        if bars is None:
            try:
                bars = get_candles(symbol, "5m", M5_CANDLE_COUNT)
                candles_by_symbol[symbol] = bars
            except Exception as exc:
                LOG.warning("Cannot refresh open trade %s: %s", symbol, exc)
                continue
        entry_ms = int(trade["next_bar_time_ms"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        for bar in bars:
            if int(bar["time"]) < entry_ms:
                continue
            direction = str(trade["direction"])
            hit_sl = bar["low"] <= sl if direction == "LONG" else bar["high"] >= sl
            hit_tp = bar["high"] >= tp if direction == "LONG" else bar["low"] <= tp
            if not hit_sl and not hit_tp:
                continue
            # Conservative resolution if both levels are touched in one M5 candle.
            if hit_sl:
                status, exit_price = "CLOSED_SL", sl
            else:
                status, exit_price = "CLOSED_TP", tp
            exit_time = (bar["time"] + M5_MS) / 1000.0
            close_trade(conn, int(trade["id"]), status, exit_time, exit_price)
            closed.append({
                "symbol": symbol,
                "status": status,
                "entry": float(trade["entry_price"]),
                "exit": exit_price,
                "pnl_pct": trade_pnl_percent(float(trade["entry_price"]), exit_price, str(trade["direction"])),
                "direction": str(trade["direction"]),
                "exit_time": exit_time,
            })
            break
        else:
            # Fallback to the latest live ticker. This catches a TP/SL touch that
            # may be missing from the locally fetched closed-candle window, and
            # prevents a trade remaining OPEN after the market has crossed a level.
            live_price = (live_prices or {}).get(symbol)
            if live_price is not None and live_price > 0:
                direction = str(trade["direction"])
                hit_sl_live = live_price <= sl if direction == "LONG" else live_price >= sl
                hit_tp_live = live_price >= tp if direction == "LONG" else live_price <= tp
                if hit_sl_live or hit_tp_live:
                    # If the current price is beyond both levels due to a gap or
                    # inconsistent feed, resolve conservatively in favor of SL.
                    if hit_sl_live:
                        status, exit_price = "CLOSED_SL", sl
                    else:
                        status, exit_price = "CLOSED_TP", tp
                    exit_time = time.time()
                    close_trade(conn, int(trade["id"]), status, exit_time, exit_price)
                    closed.append({
                        "symbol": symbol,
                        "status": status,
                        "entry": float(trade["entry_price"]),
                        "exit": exit_price,
                        "pnl_pct": trade_pnl_percent(float(trade["entry_price"]), exit_price, direction),
                        "direction": direction,
                        "exit_time": exit_time,
                    })
    return closed


def get_performance(conn: sqlite3.Connection, prices: dict[str, Optional[float]]) -> dict[str, Any]:
    closed_rows = list(conn.execute("SELECT status,pnl_pct FROM trades WHERE status IN ('CLOSED_TP','CLOSED_SL')"))
    open_rows = open_trades(conn)
    wins = sum(1 for row in closed_rows if row["status"] == "CLOSED_TP" and (row["pnl_pct"] or 0) > 0)
    losses = len(closed_rows) - wins
    win_rate = 100.0 * wins / len(closed_rows) if closed_rows else 0.0
    realized = sum(float(row["pnl_pct"] or 0.0) for row in closed_rows)
    floating = 0.0
    for row in open_rows:
        price = prices.get(str(row["symbol"]))
        pnl = trade_pnl_percent(float(row["entry_price"]), price, str(row["direction"]))
        if pnl is not None:
            floating += pnl
    return {
        "closed_count": len(closed_rows),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "open_count": len(open_rows),
        "realized_pct": realized,
        "floating_pct": floating,
        "total_pct": realized + floating,
        "open_rows": open_rows,
    }


def pct_from_entry(entry: float, level: float, direction: str = "LONG") -> float:
    return ((level / entry - 1.0) if direction == "LONG" else (entry / level - 1.0)) * 100.0


def make_report(scan_stats: dict[str, Any], new_signals: list[dict[str, Any]], closed_trades: list[dict[str, Any]], perf: dict[str, Any], prices: dict[str, Optional[float]]) -> str:
    lines=[f"🔎 NDS M5 LONG + SHORT v{VERSION} | {dt_text(time.time())}",f"Scan: {scan_stats['scanned']} symbols | M5 OK {scan_stats['m5_ok']} | errors {scan_stats['errors']} | structures {scan_stats['patterns']}",f"Universe: {scan_stats['universe_size']} | Open slots: {perf['open_count']}/{MAX_OPEN_TRADES}",""]
    diagnostics = scan_stats.get("diagnostics", {})
    if diagnostics:
        lines.append("🧭 SIGNAL DIAGNOSTICS")
        lines.append(
            f"Detected: {diagnostics.get('detected_total', scan_stats.get('patterns', 0))} | "
            f"Fresh candidates: {diagnostics.get('candidates', 0)} | "
            f"Already traded: {diagnostics.get('already_seen', 0)} | "
            f"Expired: {diagnostics.get('expired', 0)}"
        )
        for key, label in (("no_price", "No current price"), ("bad_tp", "TP on wrong side of entry"), ("symbol_open", "Symbol already has open trade"), ("slots_full", "Open-trade limit reached"), ("sl_missing", "No valid SL pivot"), ("bad_levels", "Invalid SL/entry/TP order"), ("candle_refresh_error", "Failed to refresh M5 candles"), ("opened", "Paper trades opened")):
            count = diagnostics.get(key, 0)
            if count:
                lines.append(f"• {label}: {count}")
        lines.append("")
    for direction, emoji in (("LONG","🟢"),("SHORT","🔴")):
        items=[x for x in new_signals if x["direction"]==direction]
        lines.append(f"{emoji} NEW {direction} SIGNALS: {len(items)}")
        for item in items:
            d=item["direction"]; lines.append(f"• {item['symbol']} | Entry {fmt_price(item['entry'])} | TP {fmt_price(item['tp'])} ({fmt_pct(pct_from_entry(item['entry'],item['tp'],d))}) | SL {fmt_price(item['sl'])} ({fmt_pct(pct_from_entry(item['entry'],item['sl'],d))})")
        if not items: lines.append("No new signals.")
    if closed_trades:
        lines.extend(["",f"✅ CLOSED THIS RUN: {len(closed_trades)}"])
        for t in closed_trades:
            emoji="✅" if t["status"]=="CLOSED_TP" else "🛑"
            lines.append(f"{emoji} {t['symbol']} {t['direction']} {t['status']} | Entry {fmt_price(t['entry'])} | Exit {fmt_price(t['exit'])} | PnL {fmt_pct(t['pnl_pct'])}")
    lines.extend(["",f"📂 OPEN TRADES: {perf['open_count']}"])
    for row in perf["open_rows"]:
        d=str(row["direction"]); entry=float(row["entry_price"]); tp=float(row["tp_price"]); sl=float(row["sl_price"]); price=prices.get(str(row["symbol"])); floating=trade_pnl_percent(entry,price,d); emoji="🟢" if d=="LONG" else "🔴"
        lines.append(f"{emoji} {row['symbol']} {d} | Entry {fmt_price(entry)} | Now {fmt_price(price)} ({fmt_pct(floating)}) | TP {fmt_price(tp)} ({fmt_pct(pct_from_entry(entry,tp,d))}) | SL {fmt_price(sl)} ({fmt_pct(pct_from_entry(entry,sl,d))})")
    if not perf["open_rows"]: lines.append("No open paper trades.")
    lines.extend(["","📊 PERFORMANCE",f"Closed trades: {perf['closed_count']} | W/L: {perf['wins']}/{perf['losses']} | Win rate: {perf['win_rate']:.2f}%",f"Realized PnL: {fmt_pct(perf['realized_pct'])}",f"Floating PnL: {fmt_pct(perf['floating_pct'])}",f"TOTAL PnL: {fmt_pct(perf['total_pct'])}","PnL is the sum of directional equal-notional trade percentages, gross before fees/slippage.","Paper/signal mode only. No real orders are placed."])
    return "\n".join(lines)


# ----------------------------- Main run -----------------------------

def scan_symbol(symbol: str) -> tuple[str, Optional[list[dict[str, Any]]], Optional[str]]:
    try:
        return symbol, get_candles(symbol, "5m", M5_CANDLE_COUNT), None
    except Exception as exc:
        return symbol, None, str(exc)


def main() -> int:
    started = time.time()
    conn = connect_db()
    try:
        tickers = get_tickers()
    except Exception as exc:
        message = f"❌ NDS M5 LONG+SHORT scanner startup error: unable to load Kraken Futures tickers.\n{exc}"
        LOG.exception("Ticker fetch failed")
        telegram_send_message(message)
        return 1

    tickers_by_symbol = ticker_map(tickers)
    universe = get_universe(tickers)
    LOG.info("Selected %d symbols (target=%d)", len(universe), UNIVERSE_SIZE)

    candles_by_symbol: dict[str, list[dict[str, Any]]] = {}
    errors_by_symbol: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as pool:
        futures = {pool.submit(scan_symbol, symbol): symbol for symbol in universe}
        for future in as_completed(futures):
            symbol, candles, error = future.result()
            if candles is not None:
                candles_by_symbol[symbol] = candles
            elif error:
                errors_by_symbol[symbol] = error
                LOG.warning("M5 data error %s: %s", symbol, error)

    prices: dict[str, Optional[float]] = {}
    for symbol in set(universe) | {str(row["symbol"]) for row in open_trades(conn)}:
        price = current_price_from_ticker(tickers_by_symbol.get(symbol))
        bars = candles_by_symbol.get(symbol)
        if price is None and bars:
            price = bars[-1]["close"]
        prices[symbol] = price

    # Update previously open paper trades before assessing fresh entries.
    closed_trades = update_open_trades(conn, candles_by_symbol, prices)

    new_signals: list[dict[str, Any]] = []
    patterns_count = 0
    scanned_candidates: list[dict[str, Any]] = []
    diagnostics: dict[str, int] = {
        "detected_total": 0, "candidates": 0, "already_seen": 0, "expired": 0, "no_price": 0,
        "bad_tp": 0, "symbol_open": 0, "slots_full": 0, "sl_missing": 0,
        "bad_levels": 0, "candle_refresh_error": 0, "opened": 0,
    }
    scan_time = time.time()
    for symbol, bars in candles_by_symbol.items():
        for direction in ("LONG", "SHORT"):
            # Evaluate all matches, not only the newest match. The previous
            # latest-only approach could let an expired/seen pattern mask a
            # newer independent structure in the same symbol and direction.
            detected_patterns = detect_patterns(symbol, bars, direction)
            # Count every structurally valid match, including expired ones. This
            # keeps the top-level structure count consistent with the diagnostics.
            diagnostics["detected_total"] += len(detected_patterns)
            for pattern in detected_patterns:
                age_seconds = scan_time - float(pattern["confirmation_time"])
                if age_seconds < 0 or age_seconds > MAX_SIGNAL_AGE_HOURS * 3600:
                    diagnostics["expired"] += 1
                    continue
                patterns_count += 1
                scanned_candidates.append(pattern)

    diagnostics["candidates"] = len(scanned_candidates)
    # Process most recently confirmed structures first to make the open-slot cap fair.
    scanned_candidates.sort(key=lambda item: item["confirmation_time"], reverse=True)
    for pattern in scanned_candidates:
        if pattern_seen(conn, pattern["key"]):
            diagnostics["already_seen"] += 1
            continue
        symbol = pattern["symbol"]
        bars = candles_by_symbol[symbol]
        direction = str(pattern["direction"])
        # Entry is the current market price after the terminal pivot is confirmed,
        # not the historical pivot price. The pivot is a trigger, not a fill.
        entry = prices.get(symbol)
        if entry is None or entry <= 0:
            diagnostics["no_price"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "No current market price")
            continue
        entry = float(entry)
        tp = float(pattern["p4_price"])
        if direction == "LONG" and tp <= entry:
            diagnostics["bad_tp"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "LONG TP (2 GREEN) is not above current market entry")
            continue
        if direction == "SHORT" and tp >= entry:
            diagnostics["bad_tp"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "SHORT TP (2 RED) is not below current market entry")
            continue
        if any(row["symbol"] == symbol for row in open_trades(conn)):
            diagnostics["symbol_open"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "Another open trade already exists for this symbol")
            continue
        if len(open_trades(conn)) >= MAX_OPEN_TRADES:
            diagnostics["slots_full"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "Maximum number of open trades reached")
            continue
        try:
            bars5 = get_candles(symbol, "5m", M5_CANDLE_COUNT)
        except Exception as exc:
            diagnostics["candle_refresh_error"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", f"M5 candle fetch failed: {exc}")
            LOG.warning("M5 data error for %s: %s", symbol, exc)
            continue
        # Refresh the entry from the latest available ticker/candle before final checks.
        refreshed_entry = current_price_from_ticker(tickers_by_symbol.get(symbol))
        if refreshed_entry is None and bars5:
            refreshed_entry = float(bars5[-1]["close"])
        if refreshed_entry is None or refreshed_entry <= 0:
            diagnostics["no_price"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "No current market price after refresh")
            continue
        entry = float(refreshed_entry)
        if direction == "LONG" and tp <= entry:
            diagnostics["bad_tp"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "LONG TP (2 GREEN) is not above refreshed market entry")
            LOG.info("Rejected %s %s after refresh: TP %.10g <= entry %.10g", symbol, direction, tp, entry)
            continue
        if direction == "SHORT" and tp >= entry:
            diagnostics["bad_tp"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "SHORT TP (2 RED) is not below refreshed market entry")
            LOG.info("Rejected %s %s after refresh: TP %.10g >= entry %.10g", symbol, direction, tp, entry)
            continue
        sl = find_nearest_valid_m15_low(bars5, entry) if direction == "LONG" else find_nearest_valid_m15_high(bars5, entry)
        if sl is not None:
            buffer = STOP_BUFFER_PCT / 100.0
            sl = sl * (1.0 - buffer) if direction == "LONG" else sl * (1.0 + buffer)
        if sl is None or (direction == "LONG" and sl >= entry) or (direction == "SHORT" and sl <= entry):
            diagnostics["sl_missing"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", f"No confirmed intact M5 pivot for {direction} stop")
            continue
        if direction == "LONG" and not (sl < entry < tp):
            diagnostics["bad_levels"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "Invalid LONG levels: require SL < market entry < TP")
            continue
        if direction == "SHORT" and not (tp < entry < sl):
            diagnostics["bad_levels"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "Invalid SHORT levels: require TP < market entry < SL")
            continue
        save_pattern_decision(conn, pattern, "TRADE", "Paper trade opened at current market price")
        insert_trade(conn, pattern, time.time(), entry, tp, sl)
        diagnostics["opened"] += 1
        signal = {"symbol": symbol, "direction": direction, "entry": entry, "tp": tp, "sl": sl, "pattern": pattern, "chart": None}
        new_signals.append(signal)
        try:
            signal["chart"] = create_signal_chart(symbol, bars5, pattern, entry, tp, sl)
        except Exception:
            LOG.exception("Chart creation failed for %s", symbol)

    # Refresh prices for symbols involved in open trades, now including newly opened ones.
    for row in open_trades(conn):
        symbol = str(row["symbol"])
        if prices.get(symbol) is None:
            prices[symbol] = float(row["entry_price"])

    perf = get_performance(conn, prices)
    stats = {
        "scanned": len(universe),
        "m5_ok": len(candles_by_symbol),
        "errors": len(errors_by_symbol),
        "patterns": diagnostics["detected_total"],
        "universe_size": len(universe),
        "diagnostics": diagnostics,
    }
    report = make_report(stats, new_signals, closed_trades, perf, prices)
    LOG.info("\n%s", report)
    telegram_send_message(report)

    # Every new signal gets its own chart/photo with labeled points and levels.
    sent_charts = 0
    for signal in new_signals:
        chart_path = signal.get("chart")
        if chart_path and sent_charts < MAX_CHARTS_PER_RUN:
            tp_pct = pct_from_entry(signal["entry"], signal["tp"], signal["direction"])
            sl_pct = pct_from_entry(signal["entry"], signal["sl"], signal["direction"])
            emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
            caption = (
                f"{emoji} NEW {signal['direction']} | {signal['symbol']}\n"
                f"Entry: {fmt_price(signal['entry'])}\n"
                f"TP: {fmt_price(signal['tp'])} ({fmt_pct(tp_pct)})\n"
                f"SL buffered M5 swing: {fmt_price(signal['sl'])} ({fmt_pct(sl_pct)})\n"
                "Chart labels: blue START, green highs, red lows, ENTRY, TP and buffered SL"
            )
            if telegram_send_photo(chart_path, caption):
                sent_charts += 1

    # Send a fresh chart for EVERY trade still open after this run's TP/SL updates.
    # This is independent of MAX_CHARTS_PER_RUN, which remains only for new signals.
    open_charts_sent, open_charts_attempted = send_open_trade_charts(conn, candles_by_symbol, prices)

    LOG.info(
        "Finished in %.1fs | new signals=%d | closed=%d | open=%d | new-signal charts sent=%d | open-trade charts sent=%d/%d",
        time.time() - started, len(new_signals), len(closed_trades), len(open_trades(conn)),
        sent_charts, open_charts_sent, open_charts_attempted,
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
