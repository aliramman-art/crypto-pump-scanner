# ============================================================
# NDS M1 LIVE SCANNER - VERSION 5.9.6 FIXED
# PAPER TRADING ONLY - NO REAL ORDERS
# File name intentionally unchanged; no workflow changes required.
# Fixes in 5.9.6:
#   1) Open-trade charts fetch the historical interval needed to recover nodes.
#   2) Hook nodes are reconstructed by timestamp with tolerant mapping.
#   3) Open/pending trade symbols are always included in ticker refreshes.
#   4) Performance reports realized, floating, and combined PnL separately.
#   5) Pending trades only activate when a free slot exists and price returns
#      to the entry zone within a small, explicit tolerance.
#   6) Reject implausibly wide bid/ask spreads and fall back to mark/recent last.
#   7) Do not count TP touches on incomplete candles.
#   8) Avoid sending duplicate charts for the same new accepted signal.
# Preserved strategy:
#   SHORT: START(L) -> H1 -> L1 -> H2 -> L2 -> H3
#   LONG : START(H) -> L1 -> H1 -> L2 -> H2 -> L3
#   START is the absolute low/high of all six node prices.
#   H2 > H1, L2 < L1, final breaks H2/L2 respectively.
#   Final node is gated by a CLOSED Heikin-Ashi reversal strictly AFTER it.
#   TP remains the 86.4% retracement target. No stop-loss. Paper only.
# ============================================================
import os
import time
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

VERSION = "5.9.6"
REAL_TRADING = False
PAPER_ONLY = True

TARGET_ASSETS = 200
M1_INTERVAL = "1m"
M1_INTERVAL_MINUTES = 1
M1_CANDLES = 1500
# Used only for open trades whose historical nodes are outside the normal window.
OPEN_TRADE_CHART_CANDLES = 4320  # 72 hours
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_NODE_CANDLES = 10
NDS_RETRACE = 0.864
M1_MIN_HOOK_RANGE_PCT = 0.20
M1_MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
MAX_OPEN_TRADES = 3
# Pending setups only activate on the favorable side of entry and within this
# distance from the intended node price. This prevents replaying stale entries.
PENDING_MAX_ENTRY_DEVIATION_PCT = 0.20
MAX_BID_ASK_SPREAD_PCT = 1.00
MAX_LAST_TRADE_AGE_SECONDS = 300
REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CONTEXT_BEFORE = 60
CHART_CONTEXT_AFTER = 60
DB_FILE = "nds_m1_v592.db"  # Keep DB name so existing history is preserved.
CHART_DIR = "nds_m1_charts"

KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M1-Scanner/{VERSION}"})
LIVE_PRICE_SOURCES: Dict[str, str] = {}


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
        value = float(value)
        if value > 10_000_000_000:
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
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
    except Exception:
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except Exception:
            return None


def ts_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    x = float(value)
    ax = abs(x)
    if ax >= 1000:
        return f"{x:.2f}"
    if ax >= 100:
        return f"{x:.3f}"
    if ax >= 1:
        return f"{x:.4f}"
    if ax >= 0.01:
        return f"{x:.6f}"
    if ax >= 0.0001:
        return f"{x:.8f}"
    return f"{x:.10f}"


def age_seconds(dt: datetime) -> float:
    return max(0.0, (utc_now() - dt).total_seconds())


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        print(f"HTTP_JSON_ERROR | url={url} | {type(exc).__name__}: {exc}")
        return None


def extract_items(data: Dict[str, Any]) -> List[Any]:
    for key in ("tickers", "instruments", "candles", "result", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for subkey in ("tickers", "instruments", "candles", "data"):
                sub = value.get(subkey)
                if isinstance(sub, list):
                    return sub
    return []


def item_symbol(item: Dict[str, Any]) -> Optional[str]:
    value = item.get("symbol") or item.get("ticker") or item.get("product_id") or item.get("instrument")
    return value.strip().upper() if isinstance(value, str) else None


def get_target_assets(limit: int = TARGET_ASSETS) -> List[str]:
    required = ["PF_XAUTUSD", "PF_XBTUSD"]
    symbols = set()
    data = get_json(KRAKEN_INSTRUMENTS_URL)
    if data:
        for item in extract_items(data):
            if isinstance(item, dict):
                symbol = item_symbol(item)
                if symbol and symbol.startswith("PF_"):
                    symbols.add(symbol)
    if not set(required).issubset(symbols):
        data = get_json(KRAKEN_TICKERS_URL)
        if data:
            for item in extract_items(data):
                if isinstance(item, dict):
                    symbol = item_symbol(item)
                    if symbol and symbol.startswith("PF_"):
                        symbols.add(symbol)
    missing = [s for s in required if s not in symbols]
    if missing:
        raise RuntimeError("Required Kraken Futures contract(s) missing: " + ", ".join(missing))
    limit = max(0, int(limit))
    if limit < len(required):
        raise ValueError(f"Asset limit must be at least {len(required)}")
    optional = sorted(symbols.difference(required))
    result = sorted(set(optional[:limit - len(required)] + required))
    if not set(required).issubset(result):
        raise RuntimeError(f"Asset selection failed: required contracts not selected: {required}")
    print(f"ASSET_SELECTION | requested={limit} | selected={len(result)} | XAUT={'PF_XAUTUSD' in result} | BTC={'PF_XBTUSD' in result}")
    return result


def get_managed_symbols() -> List[str]:
    """Return symbols with OPEN or PENDING paper trades, even outside scan selection."""
    try:
        conn = db_connect()
        try:
            rows = conn.execute(
                "SELECT symbol FROM trades WHERE status='OPEN' UNION SELECT symbol FROM pending_trades WHERE status='PENDING'"
            ).fetchall()
        finally:
            conn.close()
        return sorted({str(r[0]).strip().upper() for r in rows if r[0]})
    except sqlite3.Error as exc:
        print(f"MANAGED_SYMBOLS_ERROR | {type(exc).__name__}: {exc}")
        return []


def get_live_prices(symbols: Optional[List[str]] = None) -> Dict[str, float]:
    """Use sane live bid/ask midpoint, then mark, then a recent last trade."""
    data = get_json(KRAKEN_TICKERS_URL)
    LIVE_PRICE_SOURCES.clear()
    if not data:
        print("LIVE_PRICE_ERROR | Kraken ticker endpoint returned no data")
        return {}
    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    prices: Dict[str, float] = {}
    seen = set()
    for item in extract_items(data):
        if not isinstance(item, dict):
            continue
        symbol = item_symbol(item)
        if not symbol or not symbol.startswith("PF_") or (wanted is not None and symbol not in wanted):
            continue
        seen.add(symbol)
        bid = safe_float(item.get("bid"))
        ask = safe_float(item.get("ask"))
        mark = safe_float(item.get("markPrice") or item.get("mark_price"))
        last = safe_float(item.get("last"))
        price = None
        source = ""
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_pct = ((ask - bid) / mid * 100.0) if mid > 0 else float("inf")
            if spread_pct <= MAX_BID_ASK_SPREAD_PCT:
                price, source = mid, "bid/ask midpoint"
            else:
                print(f"LIVE_PRICE_SPREAD_REJECTED | symbol={symbol} | bid={bid} | ask={ask} | spread_pct={spread_pct:.3f} | max={MAX_BID_ASK_SPREAD_PCT:.2f}")
        if price is None and mark is not None and mark > 0:
            price, source = mark, "mark price fallback"
        if price is None and last is not None and last > 0:
            last_time = parse_time(item.get("lastTime") or item.get("last_time"))
            if last_time is not None and age_seconds(last_time) <= MAX_LAST_TRADE_AGE_SECONDS:
                price, source = last, "recent last trade fallback"
            else:
                age = round(age_seconds(last_time)) if last_time else "unknown"
                print(f"LIVE_PRICE_STALE | symbol={symbol} | last={last} | lastTime={item.get('lastTime') or item.get('last_time')} | age_seconds={age} | ignored")
        if price is not None and price > 0:
            prices[symbol] = price
            LIVE_PRICE_SOURCES[symbol] = source
            print(f"LIVE_PRICE | symbol={symbol} | price={price:.12g} | source={source} | bid={bid} | ask={ask} | last={last} | lastTime={item.get('lastTime') or item.get('last_time')}")
        else:
            print(f"LIVE_PRICE_UNAVAILABLE | symbol={symbol} | no valid tight bid/ask, mark price, or fresh last trade")
    if wanted is not None:
        for symbol in sorted(wanted - seen):
            print(f"LIVE_PRICE_UNAVAILABLE | symbol={symbol} | reason=symbol_not_present_in_ticker_response")
    return prices


def live_price_label(symbol: str) -> str:
    source = LIVE_PRICE_SOURCES.get(str(symbol).strip().upper())
    if source == "bid/ask midpoint":
        return "Current (Kraken bid/ask mid)"
    if source == "mark price fallback":
        return "Current (Kraken mark price)"
    if source == "recent last trade fallback":
        return "Recent last trade (fallback)"
    return "Live price unavailable"


def normalize_candle(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, dict):
        t, o, h, low, c = item.get("time"), item.get("open"), item.get("high"), item.get("low"), item.get("close")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        t, o, h, low, c = item[:5]
    else:
        return None
    dt = parse_time(t)
    o, h, low, c = safe_float(o), safe_float(h), safe_float(low), safe_float(c)
    if dt is None or None in (o, h, low, c) or h < low:
        return None
    return {"time": dt, "open": o, "high": h, "low": low, "close": c}


def fetch_ohlc(symbol: str, candles: int = M1_CANDLES, start_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Fetch paginated M1 data; optional start_time is used for old open trades."""
    candles = max(100, int(candles))
    end_dt = utc_now()
    start_dt = parse_time(start_time) if start_time is not None else end_dt - timedelta(minutes=candles + 20)
    if start_dt is None:
        start_dt = end_dt - timedelta(minutes=candles + 20)
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/{M1_INTERVAL}"
    cursor_s, end_s = int(start_dt.timestamp()), int(end_dt.timestamp())
    rows_by_ms: Dict[int, Dict[str, Any]] = {}
    for page in range(12):
        try:
            response = SESSION.get(url, params={"from": cursor_s, "to": end_s}, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            print(f"OHLC_ERROR | symbol={symbol} | {type(exc).__name__}: {exc}")
            break
        raw = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            break
        page_times = []
        for item in raw:
            candle = normalize_candle(item)
            if candle:
                ms = int(candle["time"].timestamp() * 1000)
                rows_by_ms[ms] = candle
                page_times.append(ms)
        if not page_times:
            break
        last_s = max(page_times) // 1000
        if last_s < cursor_s:
            break
        cursor_s = last_s + M1_INTERVAL_MINUTES * 60
        if cursor_s >= end_s:
            break
        if page >= 11:
            print(f"OHLC_PAGE_LIMIT | symbol={symbol} | pages=12 | candles={len(rows_by_ms)}")
    rows = [rows_by_ms[k] for k in sorted(rows_by_ms)]
    if len(rows) > candles:
        rows = rows[-candles:]
    return rows


def candle_is_closed(candle: Dict[str, Any]) -> bool:
    dt = parse_time(candle.get("time"))
    return bool(dt and dt + timedelta(minutes=M1_INTERVAL_MINUTES) <= utc_now())


def is_pivot_high(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    value = rows[i]["high"]
    return value >= max(rows[j]["high"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)) and value > max(rows[j]["high"] for j in range(i - PIVOT_LEFT, i))


def is_pivot_low(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    value = rows[i]["low"]
    return value <= min(rows[j]["low"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)) and value < min(rows[j]["low"] for j in range(i - PIVOT_LEFT, i))


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
            if (ptype == "H" and price >= pivots[-1]["price"]) or (ptype == "L" and price <= pivots[-1]["price"]):
                pivots[-1] = node
        else:
            pivots.append(node)
    return pivots


def node_spacing_ok(nodes: List[Dict[str, Any]]) -> bool:
    return all(b["idx"] - a["idx"] >= MIN_NODE_CANDLES for a, b in zip(nodes, nodes[1:]))


def heikin_ashi(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result, prev_o, prev_c = [], None, None
    for candle in rows:
        hc = (candle["open"] + candle["high"] + candle["low"] + candle["close"]) / 4.0
        ho = (candle["open"] + candle["close"]) / 2.0 if prev_o is None else (prev_o + prev_c) / 2.0
        result.append({"time": candle["time"], "open": ho, "high": max(candle["high"], ho, hc), "low": min(candle["low"], ho, hc), "close": hc})
        prev_o, prev_c = ho, hc
    return result


def find_closed_ha_reversal(rows: List[Dict[str, Any]], final_idx: int, direction: str) -> Optional[Dict[str, Any]]:
    if len(rows) < 2 or final_idx < 0 or final_idx >= len(rows):
        return None
    ha = heikin_ashi(rows)
    green = lambda c: c["close"] >= c["open"]
    for i in range(max(1, final_idx + 1), len(ha)):
        if not candle_is_closed(rows[i]):
            continue
        before_green, now_green = green(ha[i - 1]), green(ha[i])
        if direction == "SHORT" and before_green and not now_green:
            return {"idx": i, "time": rows[i]["time"], "from_color": "GREEN", "to_color": "RED"}
        if direction == "LONG" and not before_green and now_green:
            return {"idx": i, "time": rows[i]["time"], "from_color": "RED", "to_color": "GREEN"}
    return None


def make_hook_key(symbol: str, direction: str, final_time: datetime, final_price: float) -> str:
    return f"{symbol}|{direction}|{ts_key(final_time)}|{final_price:.12g}"


def build_hook(symbol: str, rows: List[Dict[str, Any]], nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(nodes) != 6 or not node_spacing_ok(nodes):
        return None
    types = "".join(n["type"] for n in nodes)
    if types == "LHLHLH":
        start, h1, l1, h2, l2, final = nodes
        if not all(start["price"] < n["price"] for n in nodes[1:]):
            return None
        if not (h2["price"] > h1["price"] and l2["price"] < l1["price"] and final["price"] > h2["price"]):
            return None
        direction = "SHORT"
        tp = final["price"] - NDS_RETRACE * (final["price"] - start["price"])
    elif types == "HLHLHL":
        start, l1, h1, l2, h2, final = nodes
        if not all(start["price"] > n["price"] for n in nodes[1:]):
            return None
        if not (l2["price"] < l1["price"] and h2["price"] > h1["price"] and final["price"] < l2["price"]):
            return None
        direction = "LONG"
        tp = final["price"] + NDS_RETRACE * (start["price"] - final["price"])
    else:
        return None
    start_price, final_price = start["price"], final["price"]
    if start_price <= 0 or final_price <= 0 or tp <= 0:
        return None
    reversal = find_closed_ha_reversal(rows, final["idx"], direction)
    if reversal is None:
        return None
    pivot_confirm_time = final["time"] + timedelta(minutes=(PIVOT_RIGHT + 1) * M1_INTERVAL_MINUTES)
    confirmed_time = max(pivot_confirm_time, reversal["time"] + timedelta(minutes=M1_INTERVAL_MINUTES))
    return {
        "symbol": symbol, "direction": direction, "start": start, "h1": h1, "l1": l1,
        "h2": h2, "l2": l2, "final": final, "tp": tp,
        "range_pct": abs(final_price - start_price) / abs(start_price) * 100.0,
        "confirmed_time": confirmed_time, "ha_reversal_time": reversal["time"],
        "ha_reversal_idx": reversal["idx"], "ha_reversal_from": reversal["from_color"],
        "ha_reversal_to": reversal["to_color"],
        "hook_key": make_hook_key(symbol, direction, final["time"], final_price),
        "node_indices": [n["idx"] for n in nodes],
    }


def detect_hooks(symbol: str, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    pivots = build_ordered_pivots(rows)
    hooks, seen = [], set()
    for i in range(max(0, len(pivots) - 5)):
        nodes = pivots[i:i + 6]
        if len(nodes) != 6 or nodes[-1]["idx"] + PIVOT_RIGHT >= len(rows):
            continue
        if not candle_is_closed(rows[nodes[-1]["idx"] + PIVOT_RIGHT]):
            continue
        hook = build_hook(symbol, rows, nodes)
        if hook and hook["hook_key"] not in seen:
            seen.add(hook["hook_key"])
            hooks.append(hook)
    return hooks, len(pivots)


def trade_pnl_pct(direction: str, entry: float, current: float) -> float:
    if entry == 0:
        return 0.0
    return (current - entry) / entry * 100.0 if direction == "LONG" else (entry - current) / entry * 100.0


def tp_touch_info(rows: List[Dict[str, Any]], hook: Dict[str, Any]) -> Dict[str, Any]:
    """Only count TP touches on closed candles strictly after the final node."""
    tp, direction = float(hook["tp"]), hook["direction"]
    final_time = parse_time(hook.get("final", {}).get("time"))
    if final_time is None:
        return {"touched": False}
    for i, candle in enumerate(rows):
        if candle["time"] <= final_time or not candle_is_closed(candle):
            continue
        touched = candle["low"] <= tp if direction == "SHORT" else candle["high"] >= tp
        if touched:
            return {"touched": True, "idx": i, "time": candle["time"], "price": tp, "open": candle["open"], "high": candle["high"], "low": candle["low"], "close": candle["close"]}
    return {"touched": False}


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    conn = db_connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT UNIQUE, symbol TEXT, direction TEXT,
            start_time TEXT, start_price REAL, h1_time TEXT, h1_price REAL, l1_time TEXT, l1_price REAL,
            h2_time TEXT, h2_price REAL, l2_time TEXT, l2_price REAL, final_time TEXT, final_price REAL,
            tp REAL, range_pct REAL, confirmed_time TEXT, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT UNIQUE, symbol TEXT, direction TEXT,
            entry REAL, tp REAL, sl REAL, status TEXT, pnl_pct REAL, opened_at TEXT, closed_at TEXT,
            close_reason TEXT, duration_seconds REAL, last_checked_at TEXT, last_price REAL, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pending_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT UNIQUE, symbol TEXT, direction TEXT,
            entry REAL, tp REAL, sl REAL, pnl_pct REAL, last_price REAL, status TEXT,
            confirmed_at TEXT, created_at TEXT, last_checked_at TEXT)""")
        conn.execute("CREATE TABLE IF NOT EXISTS hook_chart_notifications (hook_key TEXT PRIMARY KEY, path TEXT, sent_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS trade_chart_notifications (hook_key TEXT PRIMARY KEY, path TEXT, sent_at TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol,status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_trades(status)")
        conn.commit()
    finally:
        conn.close()


def save_hook(hook: Dict[str, Any]) -> bool:
    conn = db_connect()
    try:
        cur = conn.execute("""INSERT OR IGNORE INTO hooks
            (hook_key,symbol,direction,start_time,start_price,h1_time,h1_price,l1_time,l1_price,h2_time,h2_price,l2_time,l2_price,final_time,final_price,tp,range_pct,confirmed_time,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hook["hook_key"], hook["symbol"], hook["direction"], utc_iso(hook["start"]["time"]), hook["start"]["price"],
             utc_iso(hook["h1"]["time"]), hook["h1"]["price"], utc_iso(hook["l1"]["time"]), hook["l1"]["price"],
             utc_iso(hook["h2"]["time"]), hook["h2"]["price"], utc_iso(hook["l2"]["time"]), hook["l2"]["price"],
             utc_iso(hook["final"]["time"]), hook["final"]["price"], hook["tp"], hook["range_pct"], utc_iso(hook["confirmed_time"]), utc_iso()))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def trade_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute("SELECT 1 FROM trades WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone() is not None
    finally:
        conn.close()


def open_trade_count() -> int:
    conn = db_connect()
    try:
        return int(conn.execute("SELECT COUNT(*) n FROM trades WHERE status='OPEN'").fetchone()["n"] or 0)
    finally:
        conn.close()


def save_trade(hook: Dict[str, Any], entry_override: Optional[float] = None) -> bool:
    conn = db_connect()
    try:
        now = utc_iso()
        entry = float(entry_override if entry_override is not None else hook["final"]["price"])
        cur = conn.execute("""INSERT OR IGNORE INTO trades
            (hook_key,symbol,direction,entry,tp,sl,status,pnl_pct,opened_at,created_at,last_checked_at,last_price,duration_seconds)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hook["hook_key"], hook["symbol"], hook["direction"], entry, float(hook["tp"]), None, "OPEN", 0.0, now, now, now, entry, 0.0))
        conn.commit()
        saved = conn.execute("SELECT id,status,opened_at FROM trades WHERE hook_key=? LIMIT 1", (hook["hook_key"],)).fetchone()
        print(f"DB_SAVE_TRADE | symbol={hook['symbol']} | inserted={cur.rowcount == 1} | verified={saved is not None} | status={saved['status'] if saved else 'MISSING'} | db={Path(DB_FILE).resolve()}")
        return cur.rowcount == 1 and saved is not None
    except Exception as exc:
        conn.rollback()
        print(f"DB_SAVE_TRADE_ERROR | {type(exc).__name__}: {exc}")
        raise
    finally:
        conn.close()


def trade_db_snapshot() -> Dict[str, Any]:
    conn = db_connect()
    try:
        counts = {str(r["status"]): int(r["n"]) for r in conn.execute("SELECT COALESCE(status,'NULL') status,COUNT(*) n FROM trades GROUP BY status")}
        total = int(conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] or 0)
        return {"total": total, "open": counts.get("OPEN", 0), "tp": counts.get("TP", 0), "statuses": counts,
                "db_path": str(Path(DB_FILE).resolve()), "db_bytes": Path(DB_FILE).stat().st_size if Path(DB_FILE).exists() else 0}
    finally:
        conn.close()


def save_pending_trade(hook: Dict[str, Any], current_price: float) -> None:
    entry, tp = float(hook["final"]["price"]), float(hook["tp"])
    conn = db_connect()
    try:
        conn.execute("""INSERT INTO pending_trades
            (hook_key,symbol,direction,entry,tp,sl,pnl_pct,last_price,status,confirmed_at,created_at,last_checked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(hook_key) DO UPDATE SET entry=excluded.entry,tp=excluded.tp,pnl_pct=excluded.pnl_pct,
            last_price=excluded.last_price,status='PENDING',last_checked_at=excluded.last_checked_at""",
            (hook["hook_key"], hook["symbol"], hook["direction"], entry, tp, None,
             trade_pnl_pct(hook["direction"], entry, current_price), current_price, "PENDING",
             utc_iso(hook["confirmed_time"]), utc_iso(), utc_iso()))
        conn.commit()
    finally:
        conn.close()


def delete_pending_trade(hook_key: str, status: str) -> None:
    conn = db_connect()
    try:
        conn.execute("UPDATE pending_trades SET status=?,last_checked_at=? WHERE hook_key=? AND status='PENDING'", (status, utc_iso(), hook_key))
        conn.commit()
    finally:
        conn.close()


def refresh_live_trade_prices(live_prices: Dict[str, float]) -> None:
    now, now_iso = utc_now(), utc_iso()
    conn = db_connect()
    try:
        for row in conn.execute("SELECT id,symbol,direction,entry,opened_at FROM trades WHERE status='OPEN'").fetchall():
            price = live_prices.get(str(row["symbol"]).upper())
            if price is None:
                continue
            opened = parse_time(row["opened_at"]) or now
            conn.execute("UPDATE trades SET pnl_pct=?,last_price=?,last_checked_at=?,duration_seconds=? WHERE id=? AND status='OPEN'",
                         (trade_pnl_pct(row["direction"], float(row["entry"]), price), price, now_iso, max(0, (now-opened).total_seconds()), row["id"]))
        for row in conn.execute("SELECT hook_key,symbol,direction,entry FROM pending_trades WHERE status='PENDING'").fetchall():
            price = live_prices.get(str(row["symbol"]).upper())
            if price is not None:
                conn.execute("UPDATE pending_trades SET pnl_pct=?,last_price=?,last_checked_at=? WHERE hook_key=? AND status='PENDING'",
                             (trade_pnl_pct(row["direction"], float(row["entry"]), price), price, now_iso, row["hook_key"]))
        conn.commit()
    finally:
        conn.close()


def nearest_candle_index(rows: List[Dict[str, Any]], dt: datetime, tolerance_seconds: int = 65) -> Optional[int]:
    if not rows:
        return None
    idx = min(range(len(rows)), key=lambda i: abs((rows[i]["time"] - dt).total_seconds()))
    return idx if abs((rows[idx]["time"] - dt).total_seconds()) <= tolerance_seconds else None


def load_hook_for_trade(hook_key: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute("SELECT * FROM hooks WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone()
    finally:
        conn.close()
    if not row:
        print(f"HOOK_RELOAD_MISS | hook_key={hook_key} | reason=no hooks row")
        return None

    def build_node(time_col: str, price_col: str, typ: str) -> Optional[Dict[str, Any]]:
        dt, price = parse_time(row[time_col]), safe_float(row[price_col])
        if dt is None or price is None:
            return None
        return {"idx": nearest_candle_index(rows, dt), "time": dt, "price": price, "type": typ}

    is_short = row["direction"] == "SHORT"
    nodes = {
        "start": build_node("start_time", "start_price", "L" if is_short else "H"),
        "h1": build_node("h1_time", "h1_price", "H"),
        "l1": build_node("l1_time", "l1_price", "L"),
        "h2": build_node("h2_time", "h2_price", "H"),
        "l2": build_node("l2_time", "l2_price", "L"),
        "final": build_node("final_time", "final_price", "H" if is_short else "L"),
    }
    if any(node is None for node in nodes.values()):
        print(f"HOOK_RELOAD_INVALID | hook_key={hook_key} | reason=missing timestamp or price")
        return None
    hook = {"symbol": row["symbol"], "direction": row["direction"], **nodes,
            "tp": float(row["tp"]), "range_pct": float(row["range_pct"] or 0),
            "confirmed_time": parse_time(row["confirmed_time"]) or nodes["final"]["time"],
            "hook_key": row["hook_key"],
            "node_indices": [nodes[n]["idx"] for n in ("start", "h1", "l1", "h2", "l2", "final")]}
    # Recreate the reversal marker from stored OHLC when the reversal candle is present.
    final_idx = nodes["final"]["idx"]
    if final_idx is not None:
        reversal = find_closed_ha_reversal(rows, final_idx, hook["direction"])
        if reversal:
            hook.update({"ha_reversal_time": reversal["time"], "ha_reversal_idx": reversal["idx"],
                         "ha_reversal_from": reversal["from_color"], "ha_reversal_to": reversal["to_color"]})
    return hook


def hook_nodes_in_rows(hook: Dict[str, Any], rows: List[Dict[str, Any]]) -> bool:
    return bool(rows) and all(nearest_candle_index(rows, parse_time(hook[k]["time"]) or hook[k]["time"]) is not None
                              for k in ("start", "h1", "l1", "h2", "l2", "final"))


def monitor_open_trades(rows_by_symbol: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int], live_prices: Dict[str, float]) -> None:
    conn = db_connect()
    try:
        trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    finally:
        conn.close()
    now, now_iso = utc_now(), utc_iso()
    for trade in trades:
        symbol = str(trade["symbol"]).upper()
        opened = parse_time(trade["opened_at"]) or now
        rows = rows_by_symbol.get(symbol) or []
        # If the open position predates the ordinary 1500-candle window, fetch from open time
        # so a historical TP touch is not silently missed.
        if not rows or rows[0]["time"] > opened + timedelta(minutes=1):
            age_minutes = int(max(0, (now - opened).total_seconds()) // 60) + 20
            rows = fetch_ohlc(symbol, candles=max(M1_CANDLES, min(age_minutes, 11000)), start_time=opened - timedelta(minutes=5))
        if rows:
            rows_by_symbol[symbol] = rows
        entry, tp, direction = float(trade["entry"]), float(trade["tp"]), trade["direction"]
        hit = None
        for candle in rows:
            if candle["time"] < opened or not candle_is_closed(candle):
                continue
            if (direction == "LONG" and candle["high"] >= tp) or (direction == "SHORT" and candle["low"] <= tp):
                hit = candle
                break
        conn2 = db_connect()
        try:
            if hit:
                closed = hit["time"] + timedelta(minutes=M1_INTERVAL_MINUTES)
                conn2.execute("UPDATE trades SET status='TP',pnl_pct=?,closed_at=?,close_reason='TP',duration_seconds=?,last_checked_at=?,last_price=? WHERE id=? AND status='OPEN'",
                              (trade_pnl_pct(direction, entry, tp), utc_iso(closed), max(0, (closed-opened).total_seconds()), now_iso, tp, trade["id"]))
                stats["tp_closed"] += 1
                print(f"TRADE_CLOSED_TP | symbol={symbol} | tp={tp} | candle={utc_iso(hit['time'])}")
            else:
                price = live_prices.get(symbol)
                if price is not None:
                    conn2.execute("UPDATE trades SET pnl_pct=?,last_price=?,last_checked_at=?,duration_seconds=? WHERE id=? AND status='OPEN'",
                                  (trade_pnl_pct(direction, entry, price), price, now_iso, max(0, (now-opened).total_seconds()), trade["id"]))
                elif not rows:
                    print(f"OPEN_TRADE_MONITOR_WARNING | symbol={symbol} | no candles and no valid live price; kept last known quote")
            conn2.commit()
        finally:
            conn2.close()


def activate_pending_trade(pending: sqlite3.Row, current_price: float) -> bool:
    direction, original_entry, tp = pending["direction"], float(pending["entry"]), float(pending["tp"])
    if original_entry <= 0 or current_price <= 0:
        return False
    deviation_pct = abs(current_price - original_entry) / original_entry * 100.0
    favorable_side = current_price <= original_entry if direction == "LONG" else current_price >= original_entry
    target_still_profitable = current_price < tp if direction == "LONG" else current_price > tp
    if not favorable_side or deviation_pct > PENDING_MAX_ENTRY_DEVIATION_PCT or not target_still_profitable:
        return False
    # Reload the hook in memory for save_trade-compatible shape. Entry is the observed quote,
    # not the stale historical node price. TP stays at the original 86.4% target.
    conn = db_connect()
    try:
        row = conn.execute("SELECT * FROM hooks WHERE hook_key=? LIMIT 1", (pending["hook_key"],)).fetchone()
    finally:
        conn.close()
    if not row:
        print(f"PENDING_ACTIVATION_BLOCKED | symbol={pending['symbol']} | reason=hook row missing")
        return False
    hook_stub = {"hook_key": pending["hook_key"], "symbol": pending["symbol"], "direction": direction,
                 "final": {"price": original_entry}, "tp": tp}
    if open_trade_count() >= MAX_OPEN_TRADES:
        return False
    # Avoid a duplicate trade insert race or a previously activated setup.
    if not save_trade(hook_stub, entry_override=current_price):
        return False
    delete_pending_trade(pending["hook_key"], "ACTIVATED")
    print(f"PENDING_ACTIVATED | symbol={pending['symbol']} | direction={direction} | entry={current_price} | tp={tp} | deviation_pct={deviation_pct:.4f}")
    return True


def update_pending_trades(rows_by_symbol: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int], live_prices: Dict[str, float]) -> None:
    conn = db_connect()
    try:
        pending_rows = conn.execute("SELECT * FROM pending_trades WHERE status='PENDING' ORDER BY confirmed_at").fetchall()
    finally:
        conn.close()
    for trade in pending_rows:
        confirmed = parse_time(trade["confirmed_at"]) or utc_now()
        if age_seconds(confirmed) > M1_MAX_HOOK_AGE_SECONDS:
            delete_pending_trade(trade["hook_key"], "EXPIRED")
            stats["pending_expired"] += 1
            continue
        symbol = str(trade["symbol"]).upper()
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol, M1_CANDLES)
        if rows:
            rows_by_symbol[symbol] = rows
        hook = load_hook_for_trade(trade["hook_key"], rows) if rows else None
        if hook and tp_touch_info(rows, hook)["touched"]:
            delete_pending_trade(trade["hook_key"], "EXPIRED_TP")
            stats["pending_expired"] += 1
            continue
        current = live_prices.get(symbol)
        if current is None:
            print(f"PENDING_PRICE_UNAVAILABLE | symbol={symbol} | stale price not substituted")
            continue
        conn2 = db_connect()
        try:
            conn2.execute("UPDATE pending_trades SET pnl_pct=?,last_price=?,last_checked_at=? WHERE hook_key=? AND status='PENDING'",
                          (trade_pnl_pct(trade["direction"], float(trade["entry"]), current), current, utc_iso(), trade["hook_key"]))
            conn2.commit()
        finally:
            conn2.close()
        stats["pending_checked"] += 1
        # Activation is only allowed when a slot is available and price revisits entry favorably.
        if open_trade_count() < MAX_OPEN_TRADES:
            if activate_pending_trade(trade, current):
                stats["pending_activated"] += 1
                stats["signals"] += 1


def performance_summary() -> Dict[str, Any]:
    conn = db_connect()
    try:
        closed = conn.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(pnl_pct),0) pnl,
            COALESCE(SUM(CASE WHEN pnl_pct>0 THEN pnl_pct ELSE 0 END),0) gp,
            COALESCE(SUM(CASE WHEN pnl_pct<0 THEN pnl_pct ELSE 0 END),0) gl,
            COALESCE(SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END),0) wins
            FROM trades WHERE status!='OPEN' AND status IS NOT NULL""").fetchone()
        floating = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(pnl_pct),0) pnl FROM trades WHERE status='OPEN'").fetchone()
        closed_n = int(closed["n"] or 0)
        realized = float(closed["pnl"] or 0.0)
        floating_pnl = float(floating["pnl"] or 0.0)
        return {
            "closed": closed_n, "pnl": realized, "realized_pnl": realized,
            "floating_pnl": floating_pnl, "combined_pnl": realized + floating_pnl,
            "gross_profit": float(closed["gp"] or 0.0), "gross_loss": float(closed["gl"] or 0.0),
            "tp": int(conn.execute("SELECT COUNT(*) n FROM trades WHERE status='TP'").fetchone()["n"] or 0),
            "open": int(floating["n"] or 0), "wins": int(closed["wins"] or 0),
            "win_rate": (int(closed["wins"] or 0) / closed_n * 100.0) if closed_n else 0.0,
        }
    finally:
        conn.close()


def make_chart(symbol: str, rows: List[Dict[str, Any]], hook: Dict[str, Any], reason: str = "",
               path: Optional[str] = None, current: Optional[float] = None, include_latest: bool = False) -> Optional[str]:
    if not rows:
        print(f"CHART_BUILD_ERROR | symbol={symbol} | reason=no OHLC rows")
        return None
    if hook["direction"] == "SHORT":
        nodes = [hook["start"], hook["h1"], hook["l1"], hook["h2"], hook["l2"], hook["final"]]
        labels = ["START", "H1", "L1", "H2", "L2", "H3"]
    else:
        nodes = [hook["start"], hook["l1"], hook["h1"], hook["l2"], hook["h2"], hook["final"]]
        labels = ["START", "L1", "H1", "L2", "H2", "L3"]

    mapped_indices = [nearest_candle_index(rows, parse_time(n["time"]) or n["time"]) for n in nodes]
    valid_indices = [i for i in mapped_indices if i is not None]
    if include_latest:
        # Include historical start node through the newest candle whenever the extended data exists.
        lo = max(0, min(valid_indices) - CHART_CONTEXT_BEFORE) if valid_indices else max(0, len(rows) - M1_CANDLES)
        hi = len(rows)
    elif valid_indices:
        lo = max(0, min(valid_indices) - CHART_CONTEXT_BEFORE)
        hi = min(len(rows), max(valid_indices) + CHART_CONTEXT_AFTER + 1)
    else:
        lo, hi = max(0, len(rows) - CHART_CONTEXT_BEFORE), len(rows)
    base = rows[lo:hi]
    if not base:
        return None
    ha = heikin_ashi(base)
    fig, ax = plt.subplots(figsize=(18, 10))
    for x, candle in enumerate(ha):
        color = "#159447" if candle["close"] >= candle["open"] else "#d9363e"
        ax.vlines(x, candle["low"], candle["high"], color=color, linewidth=0.75, zorder=2)
        bottom = min(candle["open"], candle["close"])
        height = max(abs(candle["close"] - candle["open"]), 1e-12)
        ax.add_patch(Rectangle((x - .34, bottom), .68, height, facecolor=color, edgecolor=color, linewidth=.5, alpha=.95, zorder=3))
    tick_count = min(12, len(base))
    if tick_count > 1:
        positions = sorted(set(round(i * (len(base) - 1) / (tick_count - 1)) for i in range(tick_count)))
        ax.set_xticks(positions)
        ax.set_xticklabels([base[i]["time"].strftime("%m-%d %H:%M") for i in positions], rotation=35, ha="right", fontsize=8)
    ax.set_xlabel("Candle time (UTC)")
    ax.set_ylabel("Price")
    xmap = {c["time"]: i for i, c in enumerate(base)}
    xs, ys = [], []
    for node in nodes:
        dt = parse_time(node["time"]) or node["time"]
        idx = nearest_candle_index(base, dt)
        if idx is not None:
            xs.append(idx)
            ys.append(float(node["price"]))
    if len(xs) == 6:
        ax.plot(xs, ys, linewidth=2, marker="o", color="#2463eb", zorder=5, label="NDS six-node structure")
    for node, label in zip(nodes, labels):
        dt = parse_time(node["time"]) or node["time"]
        x = nearest_candle_index(base, dt)
        if x is not None:
            ax.annotate(f"{label}\n{dt.strftime('%m-%d %H:%M')}", (x, float(node["price"])), xytext=(0, 9), textcoords="offset points",
                        ha="center", fontsize=7.5, fontweight="bold", bbox=dict(boxstyle="round,pad=.2", facecolor="white", edgecolor="#888", alpha=.88), zorder=7)
    entry = float(hook.get("trade_entry", hook["final"]["price"]))
    tp = float(hook["tp"])
    tp_pct = abs(entry - tp) / abs(entry) * 100 if entry else 0
    ax.axhline(entry, linestyle="--", linewidth=1.2, label=f"ENTRY {fmt_price(entry)}")
    ax.axhline(tp, linestyle=":", linewidth=1.5, label=f"TP 86.4% ({tp_pct:.2f}%) {fmt_price(tp)}")
    if current is not None and current > 0:
        ax.axhline(current, color="#ff8c00", linestyle="-.", linewidth=1.3, label=f"LATEST PRICE {fmt_price(current)}")
    reversal_time = hook.get("ha_reversal_time")
    if reversal_time is not None:
        x = nearest_candle_index(base, parse_time(reversal_time) or reversal_time)
        if x is not None:
            ax.axvline(x, color="#8a2be2", linestyle="-.", linewidth=1.2, label="Closed HA reversal")
            ax.annotate(f"HA REVERSAL\n{utc_iso(reversal_time)}", (x, base[x]["close"]), xytext=(8, -28), textcoords="offset points", fontsize=7.5, color="#6a1b9a",
                        bbox=dict(boxstyle="round,pad=.2", facecolor="white", alpha=.9))
    touch = tp_touch_info(rows, hook)
    tp_status = f"TP 86.4%: {'TOUCHED | ' + utc_iso(touch['time']) if touch['touched'] else 'NOT TOUCHED'} | {fmt_price(tp)}"
    if len(valid_indices) < 6:
        tp_status += " | Some historical node candles unavailable in fetched history"
    chart_mode = "OPEN TRADE UPDATE" if include_latest else "HOOK / SIGNAL"
    ax.set_title(f"NDS M1 | {symbol} | {hook['direction']} | {chart_mode} | TP 86.4% | PAPER ONLY")
    ax.legend(loc="best")
    ax.grid(alpha=.20)
    ax.margins(x=.025)
    fig.text(.5, .035, tp_status, ha="center", va="bottom", fontsize=10, fontweight="bold", wrap=True)
    fig.text(.5, .012, f"DECISION / REASON: {reason or 'ACCEPTED: Candidate'}", ha="center", va="bottom", fontsize=9, fontweight="bold", wrap=True)
    fig.tight_layout(rect=(0, .075, 1, 1))
    Path(CHART_DIR).mkdir(parents=True, exist_ok=True)
    out = path or str(Path(CHART_DIR) / f"{symbol}_{hook['direction']}_{ts_key(hook['final']['time'])}.png")
    try:
        fig.savefig(out, dpi=150)
    except Exception as exc:
        print(f"CHART_SAVE_ERROR | symbol={symbol} | {type(exc).__name__}: {exc}")
        plt.close(fig)
        return None
    plt.close(fig)
    return out


def telegram_ready() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def tg_send_message(text: str) -> bool:
    if not telegram_ready():
        return False
    try:
        response = SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=REQUEST_TIMEOUT)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        ok = response.ok and payload.get("ok", True) is True
        if not ok:
            print(f"TELEGRAM_MESSAGE_FAILED | status={response.status_code} | body={response.text[:300]}")
        return ok
    except Exception as exc:
        print(f"TELEGRAM_MESSAGE_ERROR | {type(exc).__name__}: {exc}")
        return False


def tg_send_photo(path: str, caption: str) -> bool:
    if not telegram_ready() or not path or not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as image_file:
            response = SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
                                    files={"photo": image_file}, timeout=REQUEST_TIMEOUT)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        ok = response.ok and payload.get("ok", True) is True
        if not ok:
            print(f"TELEGRAM_PHOTO_FAILED | status={response.status_code} | body={response.text[:300]}")
        return ok
    except Exception as exc:
        print(f"TELEGRAM_PHOTO_ERROR | {type(exc).__name__}: {exc}")
        return False


def send_new_signal(hook: Dict[str, Any], rows: List[Dict[str, Any]], current: Optional[float], reason: str = "NEW SIGNAL - ACCEPTED") -> bool:
    entry, tp = float(hook["final"]["price"]), float(hook["tp"])
    tp_pct = abs(entry - tp) / abs(entry) * 100 if entry else 0
    text = (f"{'🟢 LONG' if hook['direction'] == 'LONG' else '🔴 SHORT'} NEW SIGNAL\n"
            f"Symbol: {hook['symbol']}\nEntry: {fmt_price(entry)}\nTP 86.4%: {fmt_price(tp)} ({tp_pct:.2f}%)\n"
            f"{live_price_label(hook['symbol']) if current is not None else 'Live market price unavailable'}: {fmt_price(current)}\n"
            f"Range: {hook['range_pct']:.2f}%\nHA Reversal: {utc_iso(hook['ha_reversal_time'])}\n"
            f"Signal Confirmed: {utc_iso(hook['confirmed_time'])}\nPAPER ONLY")
    path = make_chart(hook["symbol"], rows, hook, reason, current=current)
    return tg_send_photo(path, text + f"\nReason: {reason}") if path else False


def send_open_update(live_prices: Dict[str, float]) -> bool:
    conn = db_connect()
    try:
        trades = conn.execute("SELECT symbol,direction,entry,tp,pnl_pct,last_price,opened_at,duration_seconds,last_checked_at FROM trades WHERE status='OPEN' ORDER BY opened_at").fetchall()
    finally:
        conn.close()
    if not trades:
        return False
    lines = ["━━━ OPEN TRADES ━━━"]
    now = utc_now()
    for trade in trades:
        symbol = str(trade["symbol"]).upper()
        opened = parse_time(trade["opened_at"]) or now
        duration = int(max(0, (now - opened).total_seconds()) // 60)
        current = live_prices.get(symbol)
        if current is not None:
            pnl, label = trade_pnl_pct(trade["direction"], float(trade["entry"]), current), live_price_label(symbol)
        else:
            current = safe_float(trade["last_price"])
            pnl = float(trade["pnl_pct"] or 0)
            checked = parse_time(trade["last_checked_at"])
            label = f"Last known @ {utc_iso(checked) if checked else 'unknown time'}"
        lines.append(f"{'🟢' if trade['direction']=='LONG' else '🔴'} {symbol} | Entry {fmt_price(float(trade['entry']))} | TP {fmt_price(float(trade['tp']))} | {label} {fmt_price(current)} | PnL {pnl:+.2f}% | {duration}m")
    return tg_send_message("\n".join(lines))


def send_open_trade_charts(rows_by_symbol: Dict[str, List[Dict[str, Any]]], live_prices: Dict[str, float], stats: Dict[str, int]) -> None:
    """Send a fresh chart for every still-open paper trade on each scan."""
    if not telegram_ready():
        print("OPEN_TRADE_CHART_SKIP | Telegram credentials unavailable")
        return
    conn = db_connect()
    try:
        trades = conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at").fetchall()
    finally:
        conn.close()
    for trade in trades:
        symbol = str(trade["symbol"]).upper()
        try:
            rows = rows_by_symbol.get(symbol) or []
            if not rows:
                rows = fetch_ohlc(symbol, M1_CANDLES)
            hook = load_hook_for_trade(trade["hook_key"], rows) if rows else None
            # Older trades are often >25h old. Fetch the full 72h chart window if the nodes
            # do not map to the ordinary last-1500-candle response.
            if hook is None or not hook_nodes_in_rows(hook, rows):
                extended = fetch_ohlc(symbol, OPEN_TRADE_CHART_CANDLES)
                if extended:
                    rows = extended
                    hook = load_hook_for_trade(trade["hook_key"], rows)
            if not rows:
                stats["open_chart_errors"] += 1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | no fresh OHLC candles")
                continue
            rows_by_symbol[symbol] = rows
            if hook is None:
                # Last fallback: chart current market history with the trade's entry/TP even if
                # the hook row itself is absent. This still gives the user an updated chart.
                final_time = parse_time(trade["opened_at"]) or rows[-1]["time"]
                final = {"idx": nearest_candle_index(rows, final_time), "time": final_time, "price": float(trade["entry"]),
                         "type": "H" if trade["direction"] == "SHORT" else "L"}
                hook = {"symbol": symbol, "direction": trade["direction"], "start": dict(final), "h1": dict(final), "l1": dict(final),
                        "h2": dict(final), "l2": dict(final), "final": dict(final), "tp": float(trade["tp"]),
                        "range_pct": 0.0, "confirmed_time": final_time, "hook_key": trade["hook_key"], "node_indices": []}
                print(f"OPEN_TRADE_CHART_FALLBACK | {symbol} | reason=hook row missing; rendering current chart with entry/TP only")
            current = live_prices.get(symbol)
            entry, tp = float(trade["entry"]), float(trade["tp"])
            opened = parse_time(trade["opened_at"]) or utc_now()
            duration = int(max(0, (utc_now() - opened).total_seconds()) // 60)
            if current is None:
                current = safe_float(trade["last_price"])
                price_line = "Last known price"
                pnl = float(trade["pnl_pct"] or 0)
            else:
                price_line = live_price_label(symbol)
                pnl = trade_pnl_pct(trade["direction"], entry, current)
            hook["trade_entry"] = entry
            stamp = utc_now().strftime("%Y%m%dT%H%M%S")
            chart_path = str(Path(CHART_DIR) / f"OPEN_{symbol}_{trade['direction']}_{stamp}.png")
            reason = (f"OPEN TRADE UPDATE | Entry {fmt_price(entry)} | TP {fmt_price(tp)} | {price_line}: {fmt_price(current)} | "
                      f"PnL {pnl:+.2f}% | Duration {duration}m | Updated {utc_iso()}")
            path = make_chart(symbol, rows, hook, reason, path=chart_path, current=current, include_latest=True)
            if not path:
                stats["open_chart_errors"] += 1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | chart generation returned no path")
                continue
            caption = (f"📊 OPEN TRADE CHART UPDATE\n{'🟢 LONG' if trade['direction']=='LONG' else '🔴 SHORT'} | {symbol}\n"
                       f"Entry: {fmt_price(entry)}\nTP 86.4%: {fmt_price(tp)}\n{price_line}: {fmt_price(current)}\n"
                       f"PnL: {pnl:+.2f}%\nDuration: {duration} min\nChart updated: {utc_iso()}\nPAPER ONLY")
            if tg_send_photo(path, caption):
                stats["open_charts_sent"] += 1
                print(f"OPEN_TRADE_CHART_SENT | {symbol} | {path}")
            else:
                stats["open_chart_errors"] += 1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | Telegram sendPhoto failed")
        except Exception as exc:
            stats["open_chart_errors"] += 1
            print(f"OPEN_TRADE_CHART_ERROR | {symbol} | {type(exc).__name__}: {exc}")


def send_pending_update(live_prices: Dict[str, float]) -> bool:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT symbol,direction,entry,tp,pnl_pct,last_price,last_checked_at FROM pending_trades WHERE status='PENDING' ORDER BY confirmed_at").fetchall()
    finally:
        conn.close()
    if not rows:
        return False
    lines = ["━━━ PENDING TRADES ━━━"]
    for row in rows:
        symbol = str(row["symbol"]).upper()
        current = live_prices.get(symbol)
        if current is not None:
            pnl, label = trade_pnl_pct(row["direction"], float(row["entry"]), current), live_price_label(symbol)
        else:
            current = safe_float(row["last_price"])
            pnl = float(row["pnl_pct"] or 0)
            checked = parse_time(row["last_checked_at"])
            label = f"Last known @ {utc_iso(checked) if checked else 'unknown time'}"
        lines.append(f"{'🟢' if row['direction']=='LONG' else '🔴'} {symbol} | Entry {fmt_price(float(row['entry']))} | TP {fmt_price(float(row['tp']))} | {label} {fmt_price(current)} | PnL {pnl:+.2f}%")
    lines.append(f"Activation rule: price must revisit entry favorably within {PENDING_MAX_ENTRY_DEVIATION_PCT:.2f}% with an open slot.")
    return tg_send_message("\n".join(lines))


def main() -> None:
    if REAL_TRADING or not PAPER_ONLY:
        raise RuntimeError("SAFETY STOP: paper-only mode is mandatory.")
    init_db()
    Path(CHART_DIR).mkdir(parents=True, exist_ok=True)
    before = trade_db_snapshot()
    print(f"DB_BEFORE_SCAN | total={before['total']} | open={before['open']} | tp={before['tp']} | statuses={before['statuses']} | bytes={before['db_bytes']} | path={before['db_path']}")
    stats = {"hooks": 0, "new_hooks": 0, "candidates": 0, "signals": 0, "pending": 0,
             "pending_checked": 0, "pending_expired": 0, "pending_activated": 0, "tp_closed": 0,
             "hook_chart_errors": 0, "hook_charts_sent": 0, "open_charts_sent": 0, "open_chart_errors": 0}
    symbols = get_target_assets(TARGET_ASSETS)
    if not symbols:
        raise RuntimeError("No PF_ Kraken Futures instruments returned; refusing stale-price scanning.")
    managed_symbols = get_managed_symbols()
    ticker_symbols = sorted(set(symbols).union(managed_symbols))
    live = get_live_prices(ticker_symbols)
    refresh_live_trade_prices(live)
    rows_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    monitor_open_trades(rows_by_symbol, stats, live)
    update_pending_trades(rows_by_symbol, stats, live)

    hook_reports, candidates = [], []
    for symbol in symbols:
        rows = fetch_ohlc(symbol, M1_CANDLES)
        if not rows:
            continue
        rows_by_symbol[symbol] = rows
        hooks, pivot_count = detect_hooks(symbol, rows)
        stats["hooks"] += len(hooks)
        for hook in hooks:
            is_new = save_hook(hook)
            if is_new:
                stats["new_hooks"] += 1
            report = {"hook": hook, "rows": rows, "new": is_new, "reason": ""}
            if age_seconds(hook["confirmed_time"]) > M1_MAX_HOOK_AGE_SECONDS:
                report["reason"] = f"REJECTED: Hook older than {M1_MAX_HOOK_AGE_SECONDS // 3600}h"
            elif hook["range_pct"] < M1_MIN_HOOK_RANGE_PCT:
                report["reason"] = f"REJECTED: Range {hook['range_pct']:.2f}% < minimum {M1_MIN_HOOK_RANGE_PCT:.2f}%"
            else:
                touch = tp_touch_info(rows, hook)
                if touch["touched"]:
                    report["reason"] = f"REJECTED: TP 86.4% already touched | TP {fmt_price(hook['tp'])} | {utc_iso(touch['time'])}"
                else:
                    report["reason"] = "ACCEPTED: Candidate"
                    candidates.append((hook, rows, report))
            hook_reports.append(report)
        time.sleep(SCAN_SLEEP_SECONDS)

    # Refresh all scanned and managed symbols, not just the 200 selected scan assets.
    live = get_live_prices(ticker_symbols)
    refresh_live_trade_prices(live)
    candidates.sort(key=lambda item: item[0]["confirmed_time"])
    stats["candidates"] = len(candidates)
    for hook, rows, report in candidates:
        if trade_exists(hook["hook_key"]):
            report["reason"] = "REJECTED: Trade already exists for this Hook"
            continue
        current = live.get(hook["symbol"].upper())
        if open_trade_count() >= MAX_OPEN_TRADES:
            if current is None:
                report["reason"] = "REJECTED: Max open trades reached; no valid live price for pending record"
                continue
            save_pending_trade(hook, current)
            stats["pending"] += 1
            report["reason"] = "PENDING: MAX_OPEN_TRADES reached"
            continue
        if save_trade(hook):
            stats["signals"] += 1
            report["reason"] = "ACCEPTED: NEW SIGNAL | DB VERIFIED"
            try:
                if telegram_ready() and send_new_signal(hook, rows, current, report["reason"]):
                    stats["hook_charts_sent"] += 1
            except Exception as exc:
                stats["hook_chart_errors"] += 1
                print(f"SIGNAL_CHART_ERROR | {hook['symbol']} | {type(exc).__name__}: {exc}")
        else:
            report["reason"] = "REJECTED: DB insert ignored or hook_key already exists"

    # Send generic hook notifications only when that same hook wasn't already sent as a new signal.
    if telegram_ready():
        for report in hook_reports:
            if not report["new"] or report["reason"].startswith("ACCEPTED: NEW SIGNAL"):
                continue
            hook, rows = report["hook"], report["rows"]
            try:
                path = make_chart(hook["symbol"], rows, hook, report["reason"], current=live.get(hook["symbol"].upper()))
                if path:
                    caption = (f"NDS M1 HOOK | {hook['symbol']} | {hook['direction']}\nEntry: {fmt_price(hook['final']['price'])}\n"
                               f"TP 86.4%: {fmt_price(hook['tp'])}\nRange: {hook['range_pct']:.2f}%\n"
                               f"HA Reversal: {utc_iso(hook['ha_reversal_time'])}\nSignal Confirmed: {utc_iso(hook['confirmed_time'])}\n"
                               f"Reason: {report['reason']}\nPAPER ONLY")
                    if tg_send_photo(path, caption):
                        stats["hook_charts_sent"] += 1
            except Exception as exc:
                stats["hook_chart_errors"] += 1
                print(f"HOOK_CHART_ERROR | {hook['symbol']} | {type(exc).__name__}: {exc}")

    # Final snapshot catches TP closures and price changes that occurred during scanning.
    live_final = get_live_prices(ticker_symbols)
    monitor_open_trades(rows_by_symbol, stats, live_final)
    refresh_live_trade_prices(live_final)
    update_pending_trades(rows_by_symbol, stats, live_final)
    perf = performance_summary()
    after = trade_db_snapshot()
    print(f"DB_AFTER_SCAN | total={after['total']} | open={after['open']} | tp={after['tp']} | statuses={after['statuses']} | bytes={after['db_bytes']} | path={after['db_path']}")
    send_open_trade_charts(rows_by_symbol, live_final, stats)
    diagnostics = (
        f"🔎 NDS M1 DIAGNOSTIC\nVersion: {VERSION}\nTime: {utc_iso()}\n\n━━━ ASSETS ━━━\nScanned: {len(symbols)}\nPrice symbols incl. managed trades: {len(ticker_symbols)}\n\n"
        f"━━━ M1 ━━━\nMin node spacing: {MIN_NODE_CANDLES} candles\nFinal node gate: CLOSED Heikin-Ashi reversal strictly AFTER final node\n"
        f"SHORT: GREEN->RED | LONG: RED->GREEN\nPrice: sane bid/ask midpoint (spread <= {MAX_BID_ASK_SPREAD_PCT:.2f}%); mark fallback; last trade only if <= {MAX_LAST_TRADE_AGE_SECONDS}s\n"
        f"Hooks: {stats['hooks']}\nNew hooks: {stats['new_hooks']}\nHook charts/signals sent: {stats['hook_charts_sent']}\n"
        f"Open-trade charts sent: {stats['open_charts_sent']}\nOpen-trade chart errors: {stats['open_chart_errors']}\nChart errors: {stats['hook_chart_errors']}\n"
        f"Candidates: {stats['candidates']}\nSignals created this scan: {stats['signals']}\nPending: {stats['pending']}\nPending checked: {stats['pending_checked']}\n"
        f"Pending activated: {stats['pending_activated']}\nPending expired: {stats['pending_expired']}\nTP closed this scan: {stats['tp_closed']}\n\n"
        f"━━━ DATABASE ━━━\nTrades total: {after['total']}\nTrades OPEN: {after['open']} | TP: {after['tp']}\nDB bytes: {after['db_bytes']}\nDB path: {after['db_path']}\n\n"
        f"━━━ PERFORMANCE (SUM OF TRADE PERCENTAGES, NOT ACCOUNT RETURN) ━━━\nClosed trades: {perf['closed']} | Wins: {perf['wins']} | Win rate: {perf['win_rate']:.2f}%\n"
        f"Realized PnL: {perf['realized_pnl']:+.2f}%\nFloating PnL: {perf['floating_pnl']:+.2f}%\nCombined PnL: {perf['combined_pnl']:+.2f}%\n"
        f"Gross realized profit: {perf['gross_profit']:+.2f}%\nGross realized loss: {perf['gross_loss']:+.2f}%\n\n"
        f"TP ONLY | 86.4% | NO SL | PAPER ONLY")
    print(diagnostics)
    if telegram_ready():
        tg_send_message(diagnostics)
        send_open_update(live_final)
        send_pending_update(live_final)


if __name__ == "__main__":
    main()
