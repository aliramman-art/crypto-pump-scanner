# ============================================================
# NDS M5 STRUCTURE + M1 CCI SCANNER - VERSION 6.0.0
# PAPER TRADING ONLY: NEVER SENDS REAL EXCHANGE ORDERS
# Existing entry filename and DB filename are retained so the
# current GitHub Actions workflow does not need a filename change.
#
# LONG pattern (5m pivots): prior lower-low trend -> START higher low
# -> F1 above START -> F2 above F1 -> F2 breaks down -> final low in
# (F1, F2). Enter paper LONG only when closed M1 CCI crosses -100 UP.
# SL = F1. TP = highest prior M5 high after START and before signal.
# SHORT is the exact mirror; M1 CCI crosses +100 DOWN, SL = F1 high,
# TP = lowest prior M5 low after START and before signal.
# ============================================================

import os
import time
import math
import json
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

VERSION = "6.0.0"
REAL_TRADING = False
PAPER_ONLY = True

TARGET_ASSETS = 100
REQUIRED_SYMBOLS = ("PF_XAUTUSD", "PF_XBTUSD")
M5_INTERVAL = "5m"
M1_INTERVAL = "1m"
M5_MINUTES = 5
M1_MINUTES = 1
M5_CANDLES = 1500
M1_CANDLES = 360
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_NODE_CANDLES = 5
CCI_PERIOD = 20
CCI_LEVEL = 100.0
CCI_CROSS_MAX_AGE_MINUTES = 15  # tolerate the existing 10-minute workflow cadence
MAX_OPEN_TRADES = 3
MAX_BID_ASK_SPREAD_PCT = 1.0
MAX_LAST_TRADE_AGE_SECONDS = 300
MAX_PATTERN_AGE_HOURS = 12
MAX_WORKERS = 8
REQUEST_TIMEOUT = 20
TELEGRAM_TIMEOUT = 30

KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"

# Keep the DB filename used by the existing workflow. New table names prevent
# schema collisions with prior NDS versions in that same SQLite file.
DB_FILE = "nds_m1_v592.db"
CHART_DIR = "nds_m5_cci_f1_f2_charts"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M5-CCI-Scanner/{VERSION}", "Accept": "application/json"})
LIVE_PRICE_SOURCES: Dict[str, str] = {}
LAST_TICKERS: Dict[str, Dict[str, Any]] = {}
DIAG: Dict[str, int] = {}


def reset_diag() -> None:
    DIAG.clear()
    for key in (
        "assets", "m5_ok", "m5_errors", "m5_candles", "pivots", "structures",
        "m1_requested", "cci_crosses", "signals_new", "signals_duplicate",
        "entries_opened", "entries_blocked", "trades_tp", "trades_sl",
    ):
        DIAG[key] = 0


def dinc(key: str, amount: int = 1) -> None:
    DIAG[key] = DIAG.get(key, 0) + amount


reset_diag()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    x = float(value)
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 100:
        return f"{x:.3f}"
    if ax >= 1:
        return f"{x:.4f}"
    if ax >= 0.01:
        return f"{x:.6f}"
    if ax >= 0.0001:
        return f"{x:.8f}"
    return f"{x:.10f}"


def pct_change(new_value: float, old_value: float) -> float:
    if not old_value:
        return 0.0
    return (new_value / old_value - 1.0) * 100.0


def trade_pnl_pct(direction: str, entry: float, current: float) -> float:
    if entry <= 0:
        return 0.0
    return ((current - entry) / entry * 100.0) if direction == "LONG" else ((entry - current) / entry * 100.0)


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        print(f"HTTP_JSON_ERROR | url={url} | {type(exc).__name__}: {exc}")
        return None


def extract_items(payload: Dict[str, Any]) -> List[Any]:
    for key in ("tickers", "instruments", "candles", "result", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for subkey in ("tickers", "instruments", "data"):
                nested = value.get(subkey)
                if isinstance(nested, list):
                    return nested
    return []


def item_symbol(item: Dict[str, Any]) -> Optional[str]:
    value = item.get("symbol") or item.get("ticker") or item.get("product_id") or item.get("instrument")
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def volume_rank_value(item: Dict[str, Any]) -> float:
    """Prefer quote-currency notional volume; fall back to base volume x last price."""
    for key in ("volumeQuote", "volume_quote", "quoteVolume", "quote_volume", "turnover24h", "turnover_24h", "vol24hUsd", "vol24hUSD"):
        val = safe_float(item.get(key))
        if val is not None and val >= 0:
            return val
    for key in ("vol24h", "volume24h", "volume_24h", "volume"):
        val = safe_float(item.get(key))
        if val is not None and val >= 0:
            price = safe_float(item.get("last") or item.get("markPrice") or item.get("mark_price")) or 0.0
            return val * price
    return 0.0


def get_market_tickers() -> Dict[str, Dict[str, Any]]:
    payload = get_json(KRAKEN_TICKERS_URL)
    if not payload:
        raise RuntimeError("Kraken Futures ticker endpoint returned no data")
    tickers: Dict[str, Dict[str, Any]] = {}
    for item in extract_items(payload):
        if not isinstance(item, dict):
            continue
        symbol = item_symbol(item)
        if symbol and symbol.startswith("PF_"):
            tickers[symbol] = item
    if not tickers:
        raise RuntimeError("No PF_ Kraken Futures contracts were returned by ticker endpoint")
    LAST_TICKERS.clear()
    LAST_TICKERS.update(tickers)
    return tickers


def get_target_assets(tickers: Dict[str, Dict[str, Any]], limit: int = TARGET_ASSETS) -> List[str]:
    # Rank by 24h quote/notional volume, with volume * last-price as fallback.
    # Preserve the user's required XAUT and BTC contracts even if outside top 100.
    ranked = sorted(tickers.keys(), key=lambda sym: volume_rank_value(tickers[sym]), reverse=True)
    required_present = [s for s in REQUIRED_SYMBOLS if s in tickers]
    if len(required_present) < len(REQUIRED_SYMBOLS):
        missing = sorted(set(REQUIRED_SYMBOLS) - set(required_present))
        raise RuntimeError("Required Kraken Futures contract(s) not present: " + ", ".join(missing))
    others = [s for s in ranked if s not in REQUIRED_SYMBOLS]
    target = max(int(limit), len(REQUIRED_SYMBOLS))
    selected = required_present + others[:target - len(required_present)]
    selected = list(dict.fromkeys(selected))
    print("ASSET_SELECTION | " + json.dumps({
        "requested": target,
        "selected": len(selected),
        "ranking": "24h quote volume / estimated USD notional",
        "required": list(REQUIRED_SYMBOLS),
        "top5_by_volume": [
            {"symbol": s, "volume_score": round(volume_rank_value(tickers[s]), 2)} for s in ranked[:5]
        ],
    }))
    return selected


def get_live_prices(symbols: List[str], tickers: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, float]:
    data = tickers or get_market_tickers()
    wanted = set(symbols)
    LIVE_PRICE_SOURCES.clear()
    prices: Dict[str, float] = {}
    now = utc_now()
    for symbol in wanted:
        item = data.get(symbol)
        if not item:
            continue
        bid = safe_float(item.get("bid"))
        ask = safe_float(item.get("ask"))
        mark = safe_float(item.get("markPrice") or item.get("mark_price"))
        last = safe_float(item.get("last"))
        price = None
        source = ""
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else float("inf")
            if spread_pct <= MAX_BID_ASK_SPREAD_PCT:
                price, source = mid, "bid/ask midpoint"
        if price is None and mark is not None and mark > 0:
            price, source = mark, "mark price fallback"
        if price is None and last is not None and last > 0:
            last_time = parse_time(item.get("lastTime") or item.get("last_time"))
            if last_time is not None and (now - last_time).total_seconds() <= MAX_LAST_TRADE_AGE_SECONDS:
                price, source = last, "recent last trade fallback"
        if price is not None and price > 0:
            prices[symbol] = price
            LIVE_PRICE_SOURCES[symbol] = source
        else:
            print(f"LIVE_PRICE_UNAVAILABLE | symbol={symbol}")
    return prices


def live_price_label(symbol: str) -> str:
    source = LIVE_PRICE_SOURCES.get(symbol)
    if source == "bid/ask midpoint":
        return "Kraken bid/ask mid"
    if source == "mark price fallback":
        return "Kraken mark"
    if source == "recent last trade fallback":
        return "recent last"
    return "unavailable"


def normalize_candle(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, dict):
        t = item.get("time")
        o, h, low, c = item.get("open"), item.get("high"), item.get("low"), item.get("close")
        v = item.get("volume")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        t, o, h, low, c = item[:5]
        v = item[5] if len(item) > 5 else 0
    else:
        return None
    dt = parse_time(t)
    o, h, low, c = safe_float(o), safe_float(h), safe_float(low), safe_float(c)
    if dt is None or None in (o, h, low, c) or h < low:
        return None
    return {"time": dt, "open": o, "high": h, "low": low, "close": c, "volume": safe_float(v) or 0.0}


def fetch_ohlc(symbol: str, interval: str, candle_count: int) -> List[Dict[str, Any]]:
    minutes = M5_MINUTES if interval == M5_INTERVAL else M1_MINUTES
    end_dt = utc_now()
    start_dt = end_dt - timedelta(minutes=minutes * (candle_count + 30))
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/{interval}"
    cursor_s, end_s = int(start_dt.timestamp()), int(end_dt.timestamp())
    rows_by_ms: Dict[int, Dict[str, Any]] = {}
    # The charts endpoint can paginate the range, so move forward using the last returned candle.
    for page in range(8):
        payload = get_json(url, {"from": cursor_s, "to": end_s, "count": min(candle_count, 1000)})
        raw = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            break
        times: List[int] = []
        for item in raw:
            candle = normalize_candle(item)
            if candle is None:
                continue
            stamp_ms = int(candle["time"].timestamp() * 1000)
            rows_by_ms[stamp_ms] = candle
            times.append(stamp_ms)
        if not times:
            break
        next_cursor = max(times) // 1000 + minutes * 60
        if next_cursor <= cursor_s or next_cursor >= end_s:
            break
        cursor_s = next_cursor
        # A page smaller than the practical page size normally means the range is complete.
        if len(raw) < min(candle_count, 1000):
            more = payload.get("more_candles") if isinstance(payload, dict) else False
            if not more:
                break
        if page == 7:
            print(f"OHLC_PAGE_LIMIT | symbol={symbol} | interval={interval} | rows={len(rows_by_ms)}")
    rows = [rows_by_ms[k] for k in sorted(rows_by_ms)]
    now = utc_now()
    rows = [r for r in rows if r["time"] + timedelta(minutes=minutes) <= now]
    return rows[-max(100, int(candle_count)):]


def is_pivot_high(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    value = rows[i]["high"]
    window = [rows[j]["high"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    left_values = [rows[j]["high"] for j in range(i - PIVOT_LEFT, i)]
    return value >= max(window) and value > max(left_values)


def is_pivot_low(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    value = rows[i]["low"]
    window = [rows[j]["low"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    left_values = [rows[j]["low"] for j in range(i - PIVOT_LEFT, i)]
    return value <= min(window) and value < min(left_values)


def build_ordered_pivots(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []
    for i in range(len(rows)):
        ph, pl = is_pivot_high(rows, i), is_pivot_low(rows, i)
        if not ph and not pl:
            continue
        if ph and pl:
            ptype = "H" if rows[i]["high"] - rows[i]["open"] >= rows[i]["open"] - rows[i]["low"] else "L"
        else:
            ptype = "H" if ph else "L"
        price = rows[i]["high"] if ptype == "H" else rows[i]["low"]
        node = {"idx": i, "time": rows[i]["time"], "price": price, "type": ptype}
        if pivots and pivots[-1]["type"] == ptype:
            previous = pivots[-1]
            more_extreme = price >= previous["price"] if ptype == "H" else price <= previous["price"]
            if more_extreme:
                pivots[-1] = node
        else:
            pivots.append(node)
    # Avoid adjacent same-type pivots with impossible spacing from noisy candles.
    return pivots


def node_spacing_ok(nodes: List[Dict[str, Any]]) -> bool:
    return all((b["idx"] - a["idx"]) >= MIN_NODE_CANDLES for a, b in zip(nodes, nodes[1:]))


def pattern_key(pattern: Dict[str, Any]) -> str:
    pieces = [pattern["symbol"], pattern["direction"]]
    for name in ("start", "f1", "f2", "final"):
        node = pattern[name]
        pieces.append(f"{name}:{utc_iso(node['time'])}:{float(node['price']):.12g}")
    return "|".join(pieces)


def detect_structures(symbol: str, rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Return the most recent valid long/short structure candidates.

    The final low/high must be between F1 and F2, and a later closed M5
    candle must first cross the F2 level. All five-minute bars are closed.
    """
    now = now or utc_now()
    if len(rows) < 40:
        return []
    pivots = build_ordered_pivots(rows)
    dinc("pivots", len(pivots))
    if len(pivots) < 6:
        return []
    lows = [p for p in pivots if p["type"] == "L"]
    highs = [p for p in pivots if p["type"] == "H"]
    candidates: List[Dict[str, Any]] = []

    # LONG: a lower-low run, then START/F1/F2/final lows with START < F1 < final < F2.
    for s in range(2, len(lows) - 3):
        prior2, prior1, start = lows[s - 2], lows[s - 1], lows[s]
        if not (prior1["price"] < prior2["price"] and start["price"] > prior1["price"]):
            continue
        f1, f2 = lows[s + 1], lows[s + 2]
        if f1["idx"] - start["idx"] < MIN_NODE_CANDLES or f2["idx"] - f1["idx"] < MIN_NODE_CANDLES:
            continue
        if not (f1["price"] > start["price"] and f2["price"] > f1["price"]):
            continue
        for final in lows[s + 3:]:
            if final["idx"] - f2["idx"] < MIN_NODE_CANDLES:
                continue
            if not (f1["price"] < final["price"] < f2["price"]):
                continue
            # Price must have traded below F2 before the final pivot candle.
            broken = any(rows[k]["low"] < f2["price"] for k in range(f2["idx"] + 1, final["idx"]))
            stop_survived = all(rows[k]["low"] > f1["price"] for k in range(f2["idx"] + 1, final["idx"] + 1))
            if not broken or not stop_survived:
                continue
            nodes = [start, f1, f2, final]
            if not node_spacing_ok(nodes):
                continue
            if (now - final["time"]).total_seconds() > MAX_PATTERN_AGE_HOURS * 3600:
                continue
            candidates.append({
                "symbol": symbol, "direction": "LONG", "start": start, "f1": f1,
                "f2": f2, "final": final, "pivots": pivots, "rows": rows,
            })
            # The first valid final for this node sequence is the meaningful one.
            break

    # SHORT mirror: an HH run, then START/F1/F2/final highs with START > F1 > final > F2.
    for s in range(2, len(highs) - 3):
        prior2, prior1, start = highs[s - 2], highs[s - 1], highs[s]
        if not (prior1["price"] > prior2["price"] and start["price"] < prior1["price"]):
            continue
        f1, f2 = highs[s + 1], highs[s + 2]
        if f1["idx"] - start["idx"] < MIN_NODE_CANDLES or f2["idx"] - f1["idx"] < MIN_NODE_CANDLES:
            continue
        if not (f1["price"] < start["price"] and f2["price"] < f1["price"]):
            continue
        for final in highs[s + 3:]:
            if final["idx"] - f2["idx"] < MIN_NODE_CANDLES:
                continue
            if not (f2["price"] < final["price"] < f1["price"]):
                continue
            broken = any(rows[k]["high"] > f2["price"] for k in range(f2["idx"] + 1, final["idx"]))
            stop_survived = all(rows[k]["high"] < f1["price"] for k in range(f2["idx"] + 1, final["idx"] + 1))
            if not broken or not stop_survived:
                continue
            nodes = [start, f1, f2, final]
            if not node_spacing_ok(nodes):
                continue
            if (now - final["time"]).total_seconds() > MAX_PATTERN_AGE_HOURS * 3600:
                continue
            candidates.append({
                "symbol": symbol, "direction": "SHORT", "start": start, "f1": f1,
                "f2": f2, "final": final, "pivots": pivots, "rows": rows,
            })
            break

    # Remove duplicate keys and return newest first. More than one candidate per
    # symbol can exist, but only one position per symbol can be opened later.
    unique: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        item["pattern_key"] = pattern_key(item)
        unique[item["pattern_key"]] = item
    result = sorted(unique.values(), key=lambda x: x["final"]["time"], reverse=True)
    if result:
        dinc("structures", len(result))
    return result


def calculate_cci(rows: List[Dict[str, Any]], period: int = CCI_PERIOD) -> List[Optional[float]]:
    typical = [(r["high"] + r["low"] + r["close"]) / 3.0 for r in rows]
    result: List[Optional[float]] = [None] * len(rows)
    for i in range(period - 1, len(typical)):
        window = typical[i - period + 1:i + 1]
        mean = sum(window) / period
        mean_dev = sum(abs(x - mean) for x in window) / period
        result[i] = 0.0 if mean_dev == 0 else (typical[i] - mean) / (0.015 * mean_dev)
    return result


def find_cci_cross(rows: List[Dict[str, Any]], direction: str, after_time: datetime, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Return the newest recent closed-candle CCI threshold cross after final node."""
    now = now or utc_now()
    closed = [r for r in rows if r["time"] + timedelta(minutes=M1_MINUTES) <= now]
    if len(closed) < CCI_PERIOD + 2:
        return None
    values = calculate_cci(closed)
    candidates = []
    for i in range(1, len(closed)):
        prev, cur = values[i - 1], values[i]
        if prev is None or cur is None:
            continue
        cross_time = closed[i]["time"] + timedelta(minutes=M1_MINUTES)
        if cross_time < after_time:
            continue
        age_minutes = (now - cross_time).total_seconds() / 60.0
        if age_minutes < 0 or age_minutes > CCI_CROSS_MAX_AGE_MINUTES:
            continue
        if direction == "LONG":
            crossed = prev <= -CCI_LEVEL and cur > -CCI_LEVEL
        else:
            crossed = prev >= CCI_LEVEL and cur < CCI_LEVEL
        if crossed:
            candidates.append({"time": cross_time, "cci": cur, "previous_cci": prev, "age_minutes": age_minutes})
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["time"])


def target_price_for_pattern(pattern: Dict[str, Any], cross_time: datetime) -> Optional[float]:
    rows = pattern["rows"]
    start_idx = pattern["start"]["idx"]
    eligible = [r for r in rows[start_idx + 1:] if r["time"] + timedelta(minutes=M5_MINUTES) <= cross_time]
    if not eligible:
        return None
    if pattern["direction"] == "LONG":
        return max(r["high"] for r in eligible)
    return min(r["low"] for r in eligible)


def build_entry_signal(pattern: Dict[str, Any], m1_rows: List[Dict[str, Any]], live_price: Optional[float]) -> Optional[Dict[str, Any]]:
    cross = find_cci_cross(m1_rows, pattern["direction"], pattern["final"]["time"])
    if not cross:
        return None
    dinc("cci_crosses")
    tp = target_price_for_pattern(pattern, cross["time"])
    sl = float(pattern["f1"]["price"])
    entry = safe_float(live_price)
    if entry is None or entry <= 0 or tp is None or tp <= 0:
        return None
    direction = pattern["direction"]
    if direction == "LONG" and not (sl < entry < tp):
        print(f"ENTRY_LEVELS_INVALID | symbol={pattern['symbol']} | dir=LONG | entry={entry} | SL={sl} | TP={tp}")
        return None
    if direction == "SHORT" and not (tp < entry < sl):
        print(f"ENTRY_LEVELS_INVALID | symbol={pattern['symbol']} | dir=SHORT | entry={entry} | SL={sl} | TP={tp}")
        return None
    # Do not enter a structure if the crossover has become stale relative to its final pivot.
    if cross["age_minutes"] > CCI_CROSS_MAX_AGE_MINUTES:
        return None
    signal = dict(pattern)
    signal.update({
        "signal_key": pattern["pattern_key"],
        "entry": entry,
        "sl": sl,
        "tp": float(tp),
        "cross": cross,
        "signal_time": utc_iso(cross["time"]),
        "detected_time": utc_iso(),
    })
    return signal


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS nds6_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_key TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        sl_price REAL NOT NULL,
        tp_price REAL NOT NULL,
        entry_time TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        exit_price REAL,
        exit_time TEXT,
        pnl_pct REAL,
        exit_reason TEXT,
        nodes_json TEXT,
        cci_value REAL,
        cci_cross_time TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS nds6_processed_signals (
        signal_key TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        created_at TEXT NOT NULL,
        state TEXT NOT NULL,
        note TEXT
    )""")
    conn.commit()
    return conn


def get_open_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM nds6_trades WHERE status='OPEN' ORDER BY entry_time, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM nds6_trades ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def signal_was_processed(signal_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute("SELECT 1 FROM nds6_processed_signals WHERE signal_key=?", (signal_key,)).fetchone() is not None
    finally:
        conn.close()


def mark_signal_processed(signal: Dict[str, Any], state: str, note: str = "") -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO nds6_processed_signals(signal_key,symbol,direction,created_at,state,note) VALUES(?,?,?,?,?,?)",
            (signal["signal_key"], signal["symbol"], signal["direction"], utc_iso(), state, note[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def insert_trade(signal: Dict[str, Any]) -> Optional[int]:
    nodes_json = json.dumps({
        name: {"price": float(signal[name]["price"]), "time": utc_iso(signal[name]["time"])}
        for name in ("start", "f1", "f2", "final")
    }, ensure_ascii=False)
    conn = db_connect()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO nds6_trades
            (signal_key,symbol,direction,entry_price,sl_price,tp_price,entry_time,status,nodes_json,cci_value,cci_cross_time)
            VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?)""",
            (signal["signal_key"], signal["symbol"], signal["direction"], signal["entry"], signal["sl"], signal["tp"],
             utc_iso(), nodes_json, signal["cross"]["cci"], signal["signal_time"]),
        )
        conn.commit()
        if cur.rowcount <= 0:
            row = conn.execute("SELECT id FROM nds6_trades WHERE signal_key=?", (signal["signal_key"],)).fetchone()
            return int(row[0]) if row else None
        return int(cur.lastrowid)
    finally:
        conn.close()


def close_trade(trade_id: int, exit_price: float, exit_reason: str, direction: str, entry: float) -> float:
    pnl = trade_pnl_pct(direction, entry, exit_price)
    conn = db_connect()
    try:
        conn.execute(
            "UPDATE nds6_trades SET status='CLOSED',exit_price=?,exit_time=?,pnl_pct=?,exit_reason=? WHERE id=? AND status='OPEN'",
            (exit_price, utc_iso(), pnl, exit_reason, trade_id),
        )
        conn.commit()
    finally:
        conn.close()
    return pnl


def check_exit_for_trade(trade: Dict[str, Any], rows: List[Dict[str, Any]], current: Optional[float]) -> Optional[Tuple[float, str]]:
    direction = trade["direction"]
    sl = float(trade["sl_price"])
    tp = float(trade["tp_price"])
    entry_time = parse_time(trade["entry_time"]) or datetime.min.replace(tzinfo=timezone.utc)
    # Use completed M5 bars after entry. If TP and SL occur in the same bar,
    # assume SL first (conservative; intrabar order is unknowable from OHLC).
    for candle in rows:
        if candle["time"] < entry_time:
            continue
        if direction == "LONG":
            sl_hit = candle["low"] <= sl
            tp_hit = candle["high"] >= tp
        else:
            sl_hit = candle["high"] >= sl
            tp_hit = candle["low"] <= tp
        if sl_hit:
            return sl, "SL"
        if tp_hit:
            return tp, "TP"
    if current is not None:
        if direction == "LONG":
            if current <= sl:
                return sl, "SL"
            if current >= tp:
                return tp, "TP"
        else:
            if current >= sl:
                return sl, "SL"
            if current <= tp:
                return tp, "TP"
    return None


def manage_open_trades(prices: Dict[str, float], rows_by_symbol: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    closed: List[Dict[str, Any]] = []
    for trade in get_open_trades():
        symbol = trade["symbol"]
        exit_info = check_exit_for_trade(trade, rows_by_symbol.get(symbol, []), prices.get(symbol))
        if not exit_info:
            continue
        exit_price, reason = exit_info
        pnl = close_trade(int(trade["id"]), exit_price, reason, trade["direction"], float(trade["entry_price"]))
        trade.update({"status": "CLOSED", "exit_price": exit_price, "exit_reason": reason, "pnl_pct": pnl, "exit_time": utc_iso()})
        closed.append(trade)
        dinc("trades_tp" if reason == "TP" else "trades_sl")
    return closed


def performance_stats(prices: Dict[str, float]) -> Dict[str, Any]:
    trades = get_all_trades()
    closed = [t for t in trades if t["status"] == "CLOSED"]
    opened = [t for t in trades if t["status"] == "OPEN"]
    wins = [t for t in closed if safe_float(t.get("pnl_pct")) is not None and float(t["pnl_pct"]) > 0]
    losses = [t for t in closed if safe_float(t.get("pnl_pct")) is None or float(t["pnl_pct"]) <= 0]
    realized = sum(float(t.get("pnl_pct") or 0.0) for t in closed)
    floating = sum(
        trade_pnl_pct(t["direction"], float(t["entry_price"]), prices[t["symbol"]])
        for t in opened if t["symbol"] in prices and float(t["entry_price"]) > 0
    )
    denominator = len(wins) + len(losses)
    return {
        "total": len(trades), "closed": len(closed), "open": len(opened),
        "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / denominator * 100.0) if denominator else 0.0,
        "realized": realized, "floating": floating, "combined": realized + floating,
    }


def telegram_send(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_NOT_CONFIGURED | report follows in Actions log")
        print(message)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = SESSION.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": True}, timeout=TELEGRAM_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("ok") is True:
            return True
        print("TELEGRAM_SEND_ERROR | API returned ok=false")
    except Exception as exc:
        print(f"TELEGRAM_SEND_ERROR | {type(exc).__name__}: {exc}")
    return False


def telegram_send_photo(path: str, caption: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not os.path.isfile(path):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as image_file:
            response = SESSION.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                files={"photo": (Path(path).name, image_file, "image/png")},
                timeout=TELEGRAM_TIMEOUT,
            )
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and payload.get("ok") is True
    except Exception as exc:
        print(f"TELEGRAM_PHOTO_ERROR | {type(exc).__name__}: {exc}")
        return False


def create_signal_chart(signal: Dict[str, Any], rows: List[Dict[str, Any]]) -> Optional[str]:
    if not rows:
        return None
    try:
        Path(CHART_DIR).mkdir(parents=True, exist_ok=True)
        start_idx = int(signal["start"]["idx"])
        final_idx = int(signal["final"]["idx"])
        left = max(0, start_idx - 25)
        right = min(len(rows), max(final_idx + 35, start_idx + 150))
        view = rows[left:right]
        if len(view) < 5:
            return None
        fig, ax = plt.subplots(figsize=(12, 6))
        for x, candle in enumerate(view):
            op, hi, lo, cl = candle["open"], candle["high"], candle["low"], candle["close"]
            is_up = cl >= op
            candle_color = "#218c53" if is_up else "#c0392b"
            ax.vlines(x, lo, hi, linewidth=0.8, color=candle_color)
            body_low = min(op, cl)
            body_height = max(abs(cl - op), max(abs(cl), 1.0) * 0.00001)
            ax.add_patch(Rectangle((x - 0.28, body_low), 0.56, body_height, fill=True, facecolor=candle_color, edgecolor=candle_color, linewidth=0.5, alpha=0.65))
        annotations = (("START", signal["start"]), ("F1 / SL", signal["f1"]), ("F2", signal["f2"]), ("FINAL", signal["final"]))
        for label, node in annotations:
            x = int(node["idx"]) - left
            if 0 <= x < len(view):
                ax.scatter([x], [node["price"]], s=42, zorder=5)
                offset = 12 if signal["direction"] == "LONG" else -30
                ax.annotate(f"{label}\n{fmt_price(node['price'])}", (x, node["price"]),
                            xytext=(0, offset), textcoords="offset points",
                            ha="center", fontsize=8, fontweight="bold",
                            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "alpha": 0.75, "ec": "none"})

        # Mark entry explicitly on the latest closed M5 candle at the exact entry level.
        entry_x = len(view) - 1
        ax.axhline(signal["entry"], color="#1565c0", linestyle="--", linewidth=1.5,
                   label=f"ENTRY {fmt_price(signal['entry'])}")
        ax.scatter([entry_x], [signal["entry"]], marker="^" if signal["direction"] == "LONG" else "v",
                   s=95, color="#1565c0", edgecolors="white", linewidths=0.7, zorder=7)
        ax.annotate(f"ENTRY\n{fmt_price(signal['entry'])}", (entry_x, signal["entry"]),
                    xytext=(-8, 12 if signal["direction"] == "LONG" else -30),
                    textcoords="offset points", ha="right", fontsize=8, color="#1565c0",
                    fontweight="bold", bbox={"boxstyle": "round,pad=0.2", "fc": "white", "alpha": 0.85, "ec": "#1565c0"})
        ax.axhline(signal["sl"], color="#c62828", linestyle=":", linewidth=1.5,
                   label=f"STOP LOSS / F1 {fmt_price(signal['sl'])}")
        ax.axhline(signal["tp"], color="#2e7d32", linestyle="-.", linewidth=1.5,
                   label=f"TAKE PROFIT {fmt_price(signal['tp'])}")
        ax.set_title(f"NDS v{VERSION} | {signal['symbol']} | {signal['direction']} | M5 structure + M1 CCI")
        ax.set_xlabel("Closed M5 candles")
        ax.set_ylabel("Price")
        ax.grid(True, alpha=0.2)
        ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        out = os.path.join(CHART_DIR, f"{signal['symbol'].replace('/', '_')}_{signal['direction']}_{int(time.time())}.png")
        fig.savefig(out, dpi=130)
        plt.close(fig)
        return out
    except Exception as exc:
        print(f"CHART_ERROR | symbol={signal.get('symbol')} | {type(exc).__name__}: {exc}")
        return None


def signed_level_pct(direction: str, level: float, entry: float, name: str) -> float:
    # Return measured in the trade direction: TP is positive, SL is negative.
    return trade_pnl_pct(direction, entry, level)


def format_signal(signal: Dict[str, Any], trade_id: Optional[int], trade_opened: bool, current: Optional[float]) -> str:
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    entry, sl, tp = float(signal["entry"]), float(signal["sl"]), float(signal["tp"])
    live = current if current is not None else entry
    current_pnl = trade_pnl_pct(direction, entry, live)
    tp_entry_pct = signed_level_pct(direction, tp, entry, "TP")
    sl_entry_pct = signed_level_pct(direction, sl, entry, "SL")
    tp_from_current = ((tp / live) - 1.0) * 100.0 if direction == "LONG" and live else ((live / tp) - 1.0) * 100.0 if direction == "SHORT" and tp else 0.0
    sl_from_current = ((sl / live) - 1.0) * 100.0 if direction == "LONG" and live else ((live / sl) - 1.0) * 100.0 if direction == "SHORT" and sl else 0.0
    state = f"PAPER TRADE #{trade_id} OPENED" if trade_opened and trade_id else "SIGNAL ONLY (no free slot / duplicate symbol)"
    cross = signal["cross"]
    return "\n".join([
        f"{icon} {direction} SIGNAL | {signal['symbol']} | {state}",
        f"Entry: {fmt_price(entry)} | Current: {fmt_price(live)} ({current_pnl:+.2f}% PnL, {live_price_label(signal['symbol'])})",
        f"SL = F1: {fmt_price(sl)} ({sl_entry_pct:+.2f}% from entry; current→SL {sl_from_current:+.2f}%)",
        f"TP: {fmt_price(tp)} ({tp_entry_pct:+.2f}% from entry; current→TP {tp_from_current:+.2f}%)",
        f"Nodes: START {fmt_price(signal['start']['price'])} | F1 {fmt_price(signal['f1']['price'])} | F2 {fmt_price(signal['f2']['price'])} | FINAL {fmt_price(signal['final']['price'])}",
        f"CCI(20) M1: {float(cross['previous_cci']):+.1f} → {float(cross['cci']):+.1f} | cross {utc_iso(cross['time'])} | age {cross['age_minutes']:.1f}m",
    ])


def format_open_trade(trade: Dict[str, Any], current: Optional[float]) -> str:
    symbol, direction = trade["symbol"], trade["direction"]
    entry, sl, tp = float(trade["entry_price"]), float(trade["sl_price"]), float(trade["tp_price"])
    age = "?"
    dt = parse_time(trade.get("entry_time"))
    if dt:
        age = f"{max(0, int((utc_now() - dt).total_seconds() // 60))}m"
    if current is None:
        pnl = None
        current_text = "unavailable"
        tp_gap = sl_gap = None
    else:
        pnl = trade_pnl_pct(direction, entry, current)
        current_text = fmt_price(current)
        if direction == "LONG":
            tp_gap = (tp / current - 1) * 100 if current else 0.0
            sl_gap = (sl / current - 1) * 100 if current else 0.0
        else:
            tp_gap = (current / tp - 1) * 100 if tp else 0.0
            sl_gap = (current / sl - 1) * 100 if sl else 0.0
    tp_entry_pct = signed_level_pct(direction, tp, entry, "TP")
    sl_entry_pct = signed_level_pct(direction, sl, entry, "SL")
    pnl_text = f"{pnl:+.2f}%" if pnl is not None else "N/A"
    tp_text = f"{fmt_price(tp)} ({tp_entry_pct:+.2f}% entry; {tp_gap:+.2f}% from now)" if tp_gap is not None else f"{fmt_price(tp)} ({tp_entry_pct:+.2f}% entry)"
    sl_text = f"{fmt_price(sl)} ({sl_entry_pct:+.2f}% entry; {sl_gap:+.2f}% from now)" if sl_gap is not None else f"{fmt_price(sl)} ({sl_entry_pct:+.2f}% entry)"
    icon = "🟢" if direction == "LONG" else "🔴"
    return "\n".join([
        f"{icon} #{trade['id']} {direction} | {symbol} | age {age}",
        f"Entry: {fmt_price(entry)} | Current: {current_text} | PnL: {pnl_text}",
        f"SL/F1: {sl_text}",
        f"TP: {tp_text}",
    ])


def format_closed_trade(trade: Dict[str, Any]) -> str:
    pnl = float(trade.get("pnl_pct") or 0.0)
    icon = "✅" if pnl > 0 else "❌"
    return f"{icon} #{trade['id']} {trade['symbol']} {trade['direction']} | {trade['exit_reason']} | Exit {fmt_price(float(trade['exit_price']))} | PnL {pnl:+.2f}%"


def split_message(text: str, limit: int = 3500) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current = ""
    for block in text.split("\n\n"):
        addition = block if not current else "\n\n" + block
        if current and len(current) + len(addition) > limit:
            chunks.append(current)
            current = block
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def scan_m5_symbol(symbol: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    try:
        rows = fetch_ohlc(symbol, M5_INTERVAL, M5_CANDLES)
        dinc("m5_candles", len(rows))
        if len(rows) < 40:
            return symbol, rows, [], "insufficient_m5_candles"
        structures = detect_structures(symbol, rows)
        return symbol, rows, structures, None
    except Exception as exc:
        return symbol, [], [], f"{type(exc).__name__}: {exc}"


def main() -> None:
    started = utc_now()
    print(f"NDS_M5_M1_CCI_START | version={VERSION} | time={utc_iso(started)}")
    print("MODE | REAL_TRADING=False | PAPER_ONLY=True | no exchange order endpoints are used")
    try:
        tickers = get_market_tickers()
        selected_symbols = get_target_assets(tickers, TARGET_ASSETS)
    except Exception as exc:
        message = f"NDS M5/M1 CCI SCANNER ERROR\nTime: {utc_iso()}\n{type(exc).__name__}: {exc}"
        telegram_send(message)
        raise

    previously_open = get_open_trades()
    scan_symbols = list(dict.fromkeys(selected_symbols + [t["symbol"] for t in previously_open]))
    dinc("assets", len(scan_symbols))
    rows_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    structures_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_m5_symbol, symbol): symbol for symbol in scan_symbols}
        for future in as_completed(futures):
            symbol, rows, structures, error = future.result()
            rows_by_symbol[symbol] = rows
            structures_by_symbol[symbol] = structures
            if error:
                errors += 1
                dinc("m5_errors")
                print(f"M5_SCAN_ERROR | symbol={symbol} | error={error}")
            else:
                dinc("m5_ok")
            if structures:
                print(f"M5_STRUCTURES | symbol={symbol} | count={len(structures)} | newest={structures[0]['direction']} | final={fmt_price(structures[0]['final']['price'])}")

    prices = get_live_prices(scan_symbols, tickers)
    closed_trades = manage_open_trades(prices, rows_by_symbol)

    # Fetch M1 only for symbols that have at least one plausible M5 structure.
    candidate_patterns: List[Dict[str, Any]] = []
    for symbol, patterns in structures_by_symbol.items():
        candidate_patterns.extend(patterns[:3])
    symbols_needing_m1 = sorted({p["symbol"] for p in candidate_patterns})
    m1_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    if symbols_needing_m1:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_ohlc, symbol, M1_INTERVAL, M1_CANDLES): symbol for symbol in symbols_needing_m1}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    m1_by_symbol[symbol] = future.result()
                    dinc("m1_requested")
                except Exception as exc:
                    print(f"M1_SCAN_ERROR | symbol={symbol} | {type(exc).__name__}: {exc}")
                    m1_by_symbol[symbol] = []

    new_signals: List[Dict[str, Any]] = []
    for pattern in sorted(candidate_patterns, key=lambda x: x["final"]["time"], reverse=True):
        symbol = pattern["symbol"]
        live = prices.get(symbol)
        if not live:
            continue
        if signal_was_processed(pattern["pattern_key"]):
            dinc("signals_duplicate")
            continue
        signal = build_entry_signal(pattern, m1_by_symbol.get(symbol, []), live)
        if not signal:
            continue
        # One open paper position per symbol; also enforce portfolio open-trade cap.
        current_open = get_open_trades()
        same_symbol_open = any(t["symbol"] == symbol for t in current_open)
        can_open = len(current_open) < MAX_OPEN_TRADES and not same_symbol_open
        if can_open:
            trade_id = insert_trade(signal)
            if trade_id is None:
                mark_signal_processed(signal, "SIGNAL_ONLY", "duplicate signal key or trade was not inserted")
                continue
            mark_signal_processed(signal, "TRADE_OPENED", f"paper_trade_id={trade_id}")
            dinc("entries_opened")
            new_signals.append({"signal": signal, "trade_id": trade_id, "opened": True})
        else:
            note = "max open trades reached" if len(current_open) >= MAX_OPEN_TRADES else "symbol already has an open trade"
            mark_signal_processed(signal, "SIGNAL_ONLY", note)
            dinc("entries_blocked")
            new_signals.append({"signal": signal, "trade_id": None, "opened": False})

    # Build one report containing new signals, all open trades, realized/floating PnL,
    # and each trade's TP/SL distances. Send chunks safely under Telegram's limit.
    perf = performance_stats(prices)
    ended = utc_now()
    elapsed = (ended - started).total_seconds()
    lines = [
        f"🔎 NDS M5 STRUCTURE + M1 CCI | v{VERSION}",
        f"Time (UTC): {utc_iso(ended)} | Scan runtime: {elapsed:.1f}s",
        f"Universe: top {TARGET_ASSETS} PF_ contracts by 24h volume, XAUT + BTC retained",
        f"Scanned: {len(scan_symbols)} | M5 data OK: {DIAG['m5_ok']} | errors/short: {errors} | structures: {DIAG['structures']}",
        "Rules: M5 START/F1/F2/final pattern + closed M1 CCI(20) threshold cross. Paper trading only.",
        "",
        f"🆕 NEW SIGNALS: {len(new_signals)}",
    ]
    if new_signals:
        for item in new_signals:
            lines.extend([format_signal(item["signal"], item["trade_id"], item["opened"], prices.get(item["signal"]["symbol"])), ""])
    else:
        lines.append("No new CCI-confirmed entry signals this run.\n")

    open_now = get_open_trades()
    lines.append(f"📂 OPEN TRADES: {len(open_now)}/{MAX_OPEN_TRADES}")
    if open_now:
        for trade in open_now:
            lines.extend([format_open_trade(trade, prices.get(trade["symbol"])), ""])
    else:
        lines.append("No open paper trades.\n")

    lines.append(f"📊 PERFORMANCE | Closed: {perf['closed']} | Wins: {perf['wins']} | Losses: {perf['losses']} | Win rate: {perf['win_rate']:.1f}%")
    lines.append(f"Realized PnL: {perf['realized']:+.2f}% (sum of trade returns) | Floating PnL: {perf['floating']:+.2f}% | Combined: {perf['combined']:+.2f}%")
    lines.append(f"Exits this run: TP {DIAG['trades_tp']} | SL {DIAG['trades_sl']} | Total paper trades ever: {perf['total']}")
    if closed_trades:
        lines.extend(["", "✅ CLOSED THIS RUN"])
        lines.extend(format_closed_trade(t) for t in closed_trades)
    lines.extend(["", "Price source: Kraken bid/ask midpoint, then mark, then recent last. PnL excludes fees/funding."])
    report = "\n".join(lines)
    sent = True
    for chunk in split_message(report):
        if not telegram_send(chunk):
            sent = False
            break

    # Send an annotated M5 chart for every new CCI-confirmed signal, even if
    # the trade could not open because of the open-trade cap or an existing position.
    if sent:
        for item in new_signals:
            signal = item["signal"]
            chart_path = create_signal_chart(signal, rows_by_symbol.get(signal["symbol"], []))
            if chart_path:
                state = f"PAPER TRADE #{item['trade_id']} OPENED" if item["opened"] and item["trade_id"] else "SIGNAL ONLY"
                caption = (f"NDS v{VERSION} | {signal['symbol']} | {signal['direction']} | {state}\n"
                           f"START {fmt_price(signal['start']['price'])} | F1/SL {fmt_price(signal['f1']['price'])} | "
                           f"F2 {fmt_price(signal['f2']['price'])} | FINAL {fmt_price(signal['final']['price'])}\n"
                           f"ENTRY {fmt_price(signal['entry'])} | STOP LOSS {fmt_price(signal['sl'])} | TAKE PROFIT {fmt_price(signal['tp'])}\n"
                           f"CCI(20) M1 cross: {float(signal['cross']['previous_cci']):+.1f} → {float(signal['cross']['cci']):+.1f}")
                telegram_send_photo(chart_path, caption)

    print("NDS_M5_M1_CCI_DONE | " + json.dumps({
        "version": VERSION,
        "scanned": len(scan_symbols),
        "m5_ok": DIAG["m5_ok"],
        "m5_errors": errors,
        "structures": DIAG["structures"],
        "signals_new": len(new_signals),
        "entries_opened": DIAG["entries_opened"],
        "entries_blocked": DIAG["entries_blocked"],
        "open_trades": len(open_now),
        "closed": perf["closed"],
        "wins": perf["wins"],
        "losses": perf["losses"],
        "win_rate": round(perf["win_rate"], 2),
        "runtime_seconds": round(elapsed, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL | {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise
