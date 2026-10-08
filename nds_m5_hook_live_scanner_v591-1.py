# ============================================================
# NDS M5 LIVE SCANNER
# VERSION 5.9.1
# PAPER TRADING ONLY - NO REAL ORDERS
#
# Strategy preserved:
#   SHORT: START(L) -> H1 -> L1 -> H2 -> L2 -> H3
#   LONG : START(H) -> L1 -> H1 -> L2 -> H2 -> L3
#
# 5.8.9 changes:
#   1) New hooks remain tracked persistently in SQLite.
#   2) START must be the absolute low/high of all six NDS nodes.
#   3) Charts show the real six-node NDS path and zoom around the hook.
#   4) Generic hook charts are sent only when 86.4% TP is NOT touched.
#   5) Every new paper signal gets a dedicated Telegram chart.
#   6) Failed signal-chart sends are retried while the trade remains open.
#   7) Open trades are sent to Telegram with current PnL and duration.
#   8) Diagnostic performance includes win rate and total/gross PnL.
#   9) Heikin-Ashi remains chart-only; NDS detection uses real OHLC.
#  10) REAL_TRADING remains False / PAPER ONLY.
#  11) Protective SL uses the previous qualifying RAW M5 candle high/low,
#      not the ordered NDS pivot list, so the NDS geometry itself is unchanged.
#  12) Charts connect LONG nodes in true NDS order: START-L1-H1-L2-H2-L3.
#  13) Signals blocked by MAX_OPEN_TRADES are persisted as PENDING with live PnL.
#  14) TP/SL reports and chart labels include percentage distance from Entry.
#  15) Current Price/PnL uses the live Kraken Futures ticker last price,
#      never the latest M5 candle close when a live ticker is available.
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


VERSION = "5.9.1"
REAL_TRADING = False
PAPER_ONLY = True

TARGET_ASSETS = 100
M5_INTERVAL = "5m"
M5_INTERVAL_MINUTES = 5
M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_NODE_CANDLES = 5
NDS_RETRACE = 0.864
M5_MIN_HOOK_RANGE_PCT = 0.20
M5_MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CANDLES = 240
CHART_CONTEXT_BEFORE = 60
CHART_CONTEXT_AFTER = 60

DB_FILE = "nds_h4_m5_v546.db"
CHART_DIR = "nds_h4_m5_charts"

KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")


# ------------------------------------------------------------
# BASIC UTILITIES
# ------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utc_now()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 10_000_000_000:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        except Exception:
            return None


def ts_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        x = float(value)
        if not math.isfinite(x):
            return None
        return x
    except Exception:
        return None


def fmt_price(x: Optional[float]) -> str:
    if x is None:
        return "-"
    if x == 0:
        return "0"
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


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100.0


def age_seconds(dt: datetime) -> float:
    return max(0.0, (utc_now() - dt).total_seconds())


# ------------------------------------------------------------
# HTTP / KRAKEN
# ------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M5-Scanner/{VERSION}"})


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        r = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data
    except Exception:
        return None
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


def get_target_assets(limit: int = TARGET_ASSETS) -> List[str]:
    data = get_json(KRAKEN_INSTRUMENTS_URL)
    symbols: List[str] = []
    if data:
        for item in extract_items(data):
            if isinstance(item, dict):
                sym = item.get("symbol") or item.get("ticker") or item.get("instrument")
                if isinstance(sym, str) and sym.startswith("PF_"):
                    symbols.append(sym)

    # Fallback: use ticker endpoint if instruments endpoint is unavailable.
    if not symbols:
        data = get_json(KRAKEN_TICKERS_URL)
        if data:
            for item in extract_items(data):
                if isinstance(item, dict):
                    sym = item.get("symbol") or item.get("ticker")
                    if isinstance(sym, str) and sym.startswith("PF_"):
                        symbols.append(sym)

    # Stable order avoids needless changes in scan behavior.
    return sorted(set(symbols))[:limit]


def get_live_prices(symbols: Optional[List[str]] = None) -> Dict[str, float]:
    """Return the freshest available Futures ticker price for each symbol.

    This deliberately uses the Kraken Futures ticker `last` field rather than
    the latest M5 candle close. The M5 close can be several minutes old while
    the ticker represents the latest reported trade price at request time.
    Mark price, then bid/ask midpoint, are used only as defensive fallbacks.
    """
    data = get_json(KRAKEN_TICKERS_URL)
    if not data:
        return {}

    wanted = set(symbols) if symbols else None
    prices: Dict[str, float] = {}
    for item in extract_items(data):
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker") or item.get("product_id")
        if not isinstance(symbol, str) or not symbol.startswith("PF_"):
            continue
        if wanted is not None and symbol not in wanted:
            continue

        price = safe_float(item.get("last"))
        if price is None:
            price = safe_float(item.get("markPrice"))
        if price is None:
            bid = safe_float(item.get("bid"))
            ask = safe_float(item.get("ask"))
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                price = (bid + ask) / 2.0
            elif bid is not None:
                price = bid
            elif ask is not None:
                price = ask

        if price is not None and price > 0:
            prices[symbol] = price

    return prices


def normalize_candle(item: Any) -> Optional[Dict[str, Any]]:
    # Common Kraken Futures OHLC response forms:
    # dict: {time, open, high, low, close, ...}
    # list : [time, open, high, low, close, ...]
    if isinstance(item, dict):
        t = item.get("time")
        o = item.get("open")
        h = item.get("high")
        l = item.get("low")
        c = item.get("close")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        t, o, h, l, c = item[:5]
    else:
        return None

    dt = parse_time(t)
    o = safe_float(o)
    h = safe_float(h)
    l = safe_float(l)
    c = safe_float(c)
    if dt is None or None in (o, h, l, c):
        return None
    if h < l:
        return None
    return {"time": dt, "open": o, "high": h, "low": l, "close": c}


def fetch_ohlc(symbol: str, candles: int = M5_CANDLES) -> List[Dict[str, Any]]:
    """Fetch the latest M5 candles, paginating the Kraken Futures chart API."""
    end_dt = utc_now()
    start_dt = end_dt - timedelta(minutes=M5_INTERVAL_MINUTES * (candles + 20))
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/5m"

    cursor_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())
    rows_by_ms: Dict[int, Dict[str, Any]] = {}

    # Kraken's Futures chart endpoint can cap a response, so walk forward until
    # the requested lookback has been collected or no progress is possible.
    for _ in range(8):
        try:
            r = SESSION.get(
                url,
                params={"from": cursor_s, "to": end_s},
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception:
            return []

        raw = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            break

        page_times_ms: List[int] = []
        added = 0
        for item in raw:
            c = normalize_candle(item)
            if not c:
                continue
            ms = int(c["time"].timestamp() * 1000)
            rows_by_ms[ms] = c
            page_times_ms.append(ms)
            added += 1

        if not added or not page_times_ms:
            break
        last_ms = max(page_times_ms)
        last_s = last_ms // 1000
        if last_s <= cursor_s:
            break
        cursor_s = last_s + M5_INTERVAL_MINUTES * 60
        if cursor_s >= end_s:
            break

    rows = [rows_by_ms[k] for k in sorted(rows_by_ms)]
    if len(rows) > candles:
        rows = rows[-candles:]
    return rows


# ------------------------------------------------------------
# PIVOTS / NDS
# ------------------------------------------------------------

def is_pivot_high(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    v = rows[i]["high"]
    window = [rows[j]["high"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    return v >= max(window) and v > max(rows[j]["high"] for j in range(i - PIVOT_LEFT, i))


def is_pivot_low(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    v = rows[i]["low"]
    window = [rows[j]["low"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    return v <= min(window) and v < min(rows[j]["low"] for j in range(i - PIVOT_LEFT, i))


def build_ordered_pivots(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []
    for i in range(len(rows)):
        ph = is_pivot_high(rows, i)
        pl = is_pivot_low(rows, i)
        if not ph and not pl:
            continue

        # In the rare case a candle is both, keep the more pronounced side.
        if ph and pl:
            up = rows[i]["high"] - rows[i]["open"]
            dn = rows[i]["open"] - rows[i]["low"]
            ptype = "H" if up >= dn else "L"
        else:
            ptype = "H" if ph else "L"

        price = rows[i]["high"] if ptype == "H" else rows[i]["low"]
        node = {
            "idx": i,
            "time": rows[i]["time"],
            "price": price,
            "type": ptype,
        }

        # Collapse consecutive same-type pivots, keeping the more extreme one.
        if pivots and pivots[-1]["type"] == ptype:
            if ptype == "H" and price >= pivots[-1]["price"]:
                pivots[-1] = node
            elif ptype == "L" and price <= pivots[-1]["price"]:
                pivots[-1] = node
        else:
            pivots.append(node)

    return pivots


def node_spacing_ok(nodes: List[Dict[str, Any]]) -> bool:
    for a, b in zip(nodes, nodes[1:]):
        if b["idx"] - a["idx"] < MIN_NODE_CANDLES:
            return False
    return True


def make_hook_key(symbol: str, direction: str, final_time: datetime, final_price: float) -> str:
    return f"{symbol}|{direction}|{ts_key(final_time)}|{final_price:.12g}"


def build_hook(symbol: str, rows: List[Dict[str, Any]], nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(nodes) != 6:
        return None
    types = "".join(n["type"] for n in nodes)

    # SHORT = L H L H L H
    if types == "LHLHLH":
        start, h1, l1, h2, l2, final = nodes
        # START of a positive/SHORT hook must be the lowest point
        # among all six NDS nodes.
        if not all(start["price"] < n["price"] for n in nodes[1:]):
            return None
        if not (h2["price"] > h1["price"]):
            return None
        if not (l2["price"] < l1["price"]):
            return None
        if not (final["price"] > h2["price"]):
            return None
        direction = "SHORT"
        tp = final["price"] - NDS_RETRACE * (final["price"] - start["price"])

    # LONG = H L H L H L
    elif types == "HLHLHL":
        start, l1, h1, l2, h2, final = nodes
        # START of a negative/LONG hook must be the highest point
        # among all six NDS nodes.
        if not all(start["price"] > n["price"] for n in nodes[1:]):
            return None
        if not (l2["price"] < l1["price"]):
            return None
        if not (h2["price"] > h1["price"]):
            return None
        if not (final["price"] < l2["price"]):
            return None
        direction = "LONG"
        tp = final["price"] + NDS_RETRACE * (start["price"] - final["price"])
    else:
        return None

    if not node_spacing_ok(nodes):
        return None

    start_price = start["price"]
    final_price = final["price"]
    if start_price == 0:
        return None

    range_pct = abs(final_price - start_price) / abs(start_price) * 100.0
    confirmed_time = final["time"] + timedelta(minutes=PIVOT_RIGHT * M5_INTERVAL_MINUTES)

    hook = {
        "symbol": symbol,
        "direction": direction,
        "start": start,
        "h1": h1,
        "l1": l1,
        "h2": h2,
        "l2": l2,
        "final": final,
        "tp": tp,
        "range_pct": range_pct,
        "confirmed_time": confirmed_time,
        "hook_key": make_hook_key(symbol, direction, final["time"], final_price),
        "node_indices": [n["idx"] for n in nodes],
    }
    return hook


def detect_hooks(symbol: str, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    pivots = build_ordered_pivots(rows)
    hooks: List[Dict[str, Any]] = []
    seen = set()
    for i in range(len(pivots) - 5):
        nodes = pivots[i:i + 6]
        if nodes[-1]["idx"] + PIVOT_RIGHT >= len(rows):
            continue
        hook = build_hook(symbol, rows, nodes)
        if not hook:
            continue
        key = hook["hook_key"]
        if key in seen:
            continue
        seen.add(key)
        hooks.append(hook)
    return hooks, len(pivots)


# ------------------------------------------------------------
# TP / SL
# ------------------------------------------------------------

def tp_touch_info(rows: List[Dict[str, Any]], hook: Dict[str, Any]) -> Dict[str, Any]:
    final_idx = hook["final"]["idx"]
    tp = hook["tp"]
    direction = hook["direction"]
    for i in range(final_idx + 1, len(rows)):
        candle = rows[i]
        touched = candle["low"] <= tp if direction == "SHORT" else candle["high"] >= tp
        if touched:
            return {
                "touched": True,
                "idx": i,
                "time": candle["time"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
    return {"touched": False}


def find_protective_sl(hook: Dict[str, Any], rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the protective SL without changing NDS entry/TP logic.

    Primary rule:
      SHORT -> previous real M5 high above entry
      LONG  -> previous real M5 low below entry

    If no qualifying previous M5 extreme exists in the available history, use
    the historical scanner fallback used by the earlier paper version: 50% of
    the Entry-to-TP distance on the protective side of Entry. This fallback
    prevents a valid NDS hook from being discarded solely because the market
    has not printed a prior raw M5 extreme beyond the NDS final point.
    """
    final_idx = int(hook["final"]["idx"])
    entry = float(hook["final"]["price"])
    tp = float(hook["tp"])

    if not rows or final_idx <= 0:
        return None

    last_idx = min(final_idx - 1, len(rows) - 1)

    if hook["direction"] == "SHORT":
        # Primary: nearest previous real M5 high above Entry.
        for i in range(last_idx, -1, -1):
            candle = rows[i]
            high = safe_float(candle.get("high"))
            if high is not None and high > entry:
                return {
                    "idx": i,
                    "time": candle["time"],
                    "price": high,
                    "type": "H",
                    "source": "PREVIOUS_M5_HIGH",
                }

        # Fallback: 50% of Entry-to-TP distance above Entry.
        distance = entry - tp
        if distance > 0:
            sl = entry + (distance * 0.50)
            return {
                "idx": final_idx,
                "time": hook["final"]["time"],
                "price": sl,
                "type": "F",
                "source": "MIDPOINT_FALLBACK",
            }

    else:
        # Primary: nearest previous real M5 low below Entry.
        for i in range(last_idx, -1, -1):
            candle = rows[i]
            low = safe_float(candle.get("low"))
            if low is not None and low < entry:
                return {
                    "idx": i,
                    "time": candle["time"],
                    "price": low,
                    "type": "L",
                    "source": "PREVIOUS_M5_LOW",
                }

        # Fallback: 50% of Entry-to-TP distance below Entry.
        distance = tp - entry
        if distance > 0:
            sl = entry - (distance * 0.50)
            return {
                "idx": final_idx,
                "time": hook["final"]["time"],
                "price": sl,
                "type": "F",
                "source": "MIDPOINT_FALLBACK",
            }

    return None


def geometry_valid(hook: Dict[str, Any], sl: Optional[Dict[str, Any]]) -> bool:
    if sl is None:
        return False
    entry = hook["final"]["price"]
    tp = hook["tp"]
    if hook["direction"] == "SHORT":
        return tp < entry < sl["price"]
    return sl["price"] < entry < tp


# ------------------------------------------------------------
# SQLITE
# ------------------------------------------------------------

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    conn = db_connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hook_key TEXT UNIQUE,
                symbol TEXT,
                direction TEXT,
                start_time TEXT,
                start_price REAL,
                h1_time TEXT,
                h1_price REAL,
                l1_time TEXT,
                l1_price REAL,
                h2_time TEXT,
                h2_price REAL,
                l2_time TEXT,
                l2_price REAL,
                final_time TEXT,
                final_price REAL,
                tp REAL,
                range_pct REAL,
                confirmed_time TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hook_key TEXT UNIQUE,
                symbol TEXT,
                direction TEXT,
                entry REAL,
                tp REAL,
                sl REAL,
                status TEXT,
                pnl_pct REAL,
                opened_at TEXT,
                closed_at TEXT,
                close_reason TEXT,
                duration_seconds REAL,
                last_checked_at TEXT,
                last_price REAL,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hook_chart_notifications (
                hook_key TEXT PRIMARY KEY,
                path TEXT,
                sent_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_chart_notifications (
                hook_key TEXT PRIMARY KEY,
                path TEXT,
                sent_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hook_key TEXT UNIQUE,
                symbol TEXT,
                direction TEXT,
                entry REAL,
                tp REAL,
                sl REAL,
                pnl_pct REAL,
                last_price REAL,
                status TEXT,
                confirmed_at TEXT,
                created_at TEXT,
                last_checked_at TEXT
            )
            """
        )
        ensure_columns(conn, "hooks", {
            "hook_key": "TEXT",
            "symbol": "TEXT",
            "direction": "TEXT",
            "start_time": "TEXT",
            "start_price": "REAL",
            "h1_time": "TEXT",
            "h1_price": "REAL",
            "l1_time": "TEXT",
            "l1_price": "REAL",
            "h2_time": "TEXT",
            "h2_price": "REAL",
            "l2_time": "TEXT",
            "l2_price": "REAL",
            "final_time": "TEXT",
            "final_price": "REAL",
            "tp": "REAL",
            "range_pct": "REAL",
            "confirmed_time": "TEXT",
            "created_at": "TEXT",
        })
        ensure_columns(conn, "trades", {
            "hook_key": "TEXT",
            "symbol": "TEXT",
            "direction": "TEXT",
            "entry": "REAL",
            "tp": "REAL",
            "sl": "REAL",
            "status": "TEXT",
            "pnl_pct": "REAL",
            "opened_at": "TEXT",
            "closed_at": "TEXT",
            "close_reason": "TEXT",
            "duration_seconds": "REAL",
            "last_checked_at": "TEXT",
            "last_price": "REAL",
            "created_at": "TEXT",
        })
        ensure_columns(conn, "hook_chart_notifications", {
            "hook_key": "TEXT",
            "path": "TEXT",
            "sent_at": "TEXT",
        })
        ensure_columns(conn, "trade_chart_notifications", {
            "hook_key": "TEXT",
            "path": "TEXT",
            "sent_at": "TEXT",
        })
        ensure_columns(conn, "pending_trades", {
            "hook_key": "TEXT",
            "symbol": "TEXT",
            "direction": "TEXT",
            "entry": "REAL",
            "tp": "REAL",
            "sl": "REAL",
            "pnl_pct": "REAL",
            "last_price": "REAL",
            "status": "TEXT",
            "confirmed_at": "TEXT",
            "created_at": "TEXT",
            "last_checked_at": "TEXT",
        })
        conn.commit()
    finally:
        conn.close()


def hook_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        row = conn.execute("SELECT 1 FROM hooks WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone()
        return row is not None
    finally:
        conn.close()


def save_hook(hook: Dict[str, Any]) -> bool:
    """Return True only when this hook was first inserted in persistent DB."""
    n = utc_iso()
    conn = db_connect()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO hooks
            (hook_key,symbol,direction,start_time,start_price,h1_time,h1_price,
             l1_time,l1_price,h2_time,h2_price,l2_time,l2_price,final_time,final_price,
             tp,range_pct,confirmed_time,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                hook["hook_key"], hook["symbol"], hook["direction"],
                utc_iso(hook["start"]["time"]), hook["start"]["price"],
                utc_iso(hook["h1"]["time"]), hook["h1"]["price"],
                utc_iso(hook["l1"]["time"]), hook["l1"]["price"],
                utc_iso(hook["h2"]["time"]), hook["h2"]["price"],
                utc_iso(hook["l2"]["time"]), hook["l2"]["price"],
                utc_iso(hook["final"]["time"]), hook["final"]["price"],
                hook["tp"], hook["range_pct"], utc_iso(hook["confirmed_time"]), n,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def notification_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT 1 FROM hook_chart_notifications WHERE hook_key=? LIMIT 1", (hook_key,)
        ).fetchone() is not None
    finally:
        conn.close()


def mark_notification_sent(hook_key: str, path: str) -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO hook_chart_notifications(hook_key,path,sent_at) VALUES(?,?,?)",
            (hook_key, path, utc_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def trade_chart_notification_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT 1 FROM trade_chart_notifications WHERE hook_key=? LIMIT 1",
            (hook_key,),
        ).fetchone() is not None
    finally:
        conn.close()


def mark_trade_chart_notification_sent(hook_key: str, path: str) -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO trade_chart_notifications(hook_key,path,sent_at) VALUES(?,?,?)",
            (hook_key, path, utc_iso()),
        )
        conn.commit()
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
        row = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'").fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def save_trade(hook: Dict[str, Any], sl: Dict[str, Any]) -> bool:
    conn = db_connect()
    try:
        now = utc_iso()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO trades
            (hook_key,symbol,direction,entry,tp,sl,status,pnl_pct,opened_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                hook["hook_key"], hook["symbol"], hook["direction"],
                hook["final"]["price"], hook["tp"], sl["price"],
                "OPEN", 0.0, now, now,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def save_pending_trade(hook: Dict[str, Any], sl: Dict[str, Any], current_price: float) -> bool:
    """Persist a signal blocked only by MAX_OPEN_TRADES."""
    entry = float(hook["final"]["price"])
    tp = float(hook["tp"])
    sl_price = float(sl["price"])
    pnl = trade_pnl_pct(hook["direction"], entry, current_price)
    now = utc_iso()
    conn = db_connect()
    try:
        conn.execute(
            """
            INSERT INTO pending_trades
            (hook_key,symbol,direction,entry,tp,sl,pnl_pct,last_price,status,confirmed_at,created_at,last_checked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(hook_key) DO UPDATE SET
                entry=excluded.entry, tp=excluded.tp, sl=excluded.sl,
                pnl_pct=excluded.pnl_pct, last_price=excluded.last_price,
                status='PENDING', last_checked_at=excluded.last_checked_at
            """,
            (hook["hook_key"], hook["symbol"], hook["direction"], entry, tp, sl_price,
             pnl, current_price, "PENDING", utc_iso(hook["confirmed_time"]), now, now),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def delete_pending_trade(hook_key: str, status: str = "EXPIRED") -> None:
    conn = db_connect()
    try:
        conn.execute(
            "UPDATE pending_trades SET status=?, last_checked_at=? WHERE hook_key=? AND status='PENDING'",
            (status, utc_iso(), hook_key),
        )
        conn.commit()
    finally:
        conn.close()


def pending_trade_details() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT symbol,direction,entry,tp,sl,pnl_pct,last_price,confirmed_at,created_at "
            "FROM pending_trades WHERE status='PENDING' ORDER BY confirmed_at ASC"
        ).fetchall()
        out = []
        for row in rows:
            out.append({
                "symbol": row["symbol"],
                "direction": row["direction"],
                "entry": float(row["entry"]),
                "tp": float(row["tp"]),
                "sl": float(row["sl"]),
                "pnl_pct": float(row["pnl_pct"] or 0.0),
                "last_price": safe_float(row["last_price"]),
                "confirmed_at": parse_time(row["confirmed_at"]),
                "created_at": parse_time(row["created_at"]),
            })
        return out
    finally:
        conn.close()


def update_pending_trades(rows_by_symbol: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int], live_prices: Optional[Dict[str, float]] = None) -> None:
    """Refresh hypothetical PnL and expire pending signals whose TP/SL was reached."""
    conn = db_connect()
    try:
        pending = conn.execute("SELECT * FROM pending_trades WHERE status='PENDING'").fetchall()
    finally:
        conn.close()

    for tr in pending:
        confirmed = parse_time(tr["confirmed_at"]) or utc_now()
        if age_seconds(confirmed) > M5_MAX_HOOK_AGE_SECONDS:
            delete_pending_trade(tr["hook_key"], "EXPIRED")
            stats["pending_expired"] += 1
            continue

        symbol = tr["symbol"]
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol, M5_CANDLES)
        if not rows:
            continue
        rows_by_symbol[symbol] = rows

        entry = float(tr["entry"])
        tp = float(tr["tp"])
        sl = float(tr["sl"])
        direction = tr["direction"]
        last_price = (live_prices or {}).get(symbol) or rows[-1]["close"]

        hook = load_hook_for_trade(tr["hook_key"], rows)
        if hook:
            touch = tp_touch_info(rows, hook)
            if touch["touched"]:
                delete_pending_trade(tr["hook_key"], "EXPIRED_TP")
                stats["pending_expired"] += 1
                continue

        # If the pending hypothetical entry would already have hit its SL, it
        # is no longer a valid waiting signal. Do not turn it into a fake trade.
        opened = parse_time(tr["created_at"]) or confirmed
        for candle in rows:
            if candle["time"] < opened:
                continue
            hit_sl = candle["low"] <= sl if direction == "LONG" else candle["high"] >= sl
            if hit_sl:
                delete_pending_trade(tr["hook_key"], "EXPIRED_SL")
                stats["pending_expired"] += 1
                break
        else:
            pnl = trade_pnl_pct(direction, entry, last_price)
            conn2 = db_connect()
            try:
                conn2.execute(
                    "UPDATE pending_trades SET pnl_pct=?, last_price=?, last_checked_at=? "
                    "WHERE hook_key=? AND status='PENDING'",
                    (pnl, last_price, utc_iso(), tr["hook_key"]),
                )
                conn2.commit()
            finally:
                conn2.close()
            stats["pending_checked"] += 1


def refresh_live_trade_prices(live_prices: Dict[str, float]) -> None:
    """Refresh displayed/PnL prices from the live Futures ticker.

    Trade closure itself remains candle-based, preserving the existing paper
    trading simulation. This function only updates the current-price/PnL fields
    shown in Telegram and diagnostics.
    """
    if not live_prices:
        return

    conn = db_connect()
    try:
        open_rows = conn.execute(
            "SELECT id,symbol,direction,entry FROM trades WHERE status='OPEN'"
        ).fetchall()
        for row in open_rows:
            price = live_prices.get(row["symbol"])
            if price is None:
                continue
            pnl = trade_pnl_pct(row["direction"], float(row["entry"]), price)
            conn.execute(
                "UPDATE trades SET pnl_pct=?, last_checked_at=?, last_price=? "
                "WHERE id=? AND status='OPEN'",
                (pnl, utc_iso(), price, row["id"]),
            )

        pending_rows = conn.execute(
            "SELECT hook_key,symbol,direction,entry FROM pending_trades WHERE status='PENDING'"
        ).fetchall()
        for row in pending_rows:
            price = live_prices.get(row["symbol"])
            if price is None:
                continue
            pnl = trade_pnl_pct(row["direction"], float(row["entry"]), price)
            conn.execute(
                "UPDATE pending_trades SET pnl_pct=?, last_price=?, last_checked_at=? "
                "WHERE hook_key=? AND status='PENDING'",
                (pnl, price, utc_iso(), row["hook_key"]),
            )

        conn.commit()
    finally:
        conn.close()


def performance_summary() -> Dict[str, Any]:
    conn = db_connect()
    try:
        closed = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl_pct),0) AS pnl, "
            "COALESCE(SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END),0) AS gross_profit, "
            "COALESCE(SUM(CASE WHEN pnl_pct < 0 THEN pnl_pct ELSE 0 END),0) AS gross_loss "
            "FROM trades WHERE status IN ('TP','SL')"
        ).fetchone()
        tp_n = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='TP'").fetchone()["n"]
        sl_n = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='SL'").fetchone()["n"]
        op = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'").fetchone()["n"]
        total_closed = int(closed["n"] or 0)
        tp_count = int(tp_n or 0)
        win_rate = (tp_count / total_closed * 100.0) if total_closed else 0.0
        return {
            "closed": total_closed,
            "pnl": float(closed["pnl"] or 0.0),
            "gross_profit": float(closed["gross_profit"] or 0.0),
            "gross_loss": float(closed["gross_loss"] or 0.0),
            "tp": tp_count,
            "sl": int(sl_n or 0),
            "open": int(op or 0),
            "win_rate": win_rate,
        }
    finally:
        conn.close()


def load_hook_for_trade(hook_key: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT * FROM hooks WHERE hook_key=? LIMIT 1",
            (hook_key,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None

    by_time = {r["time"]: i for i, r in enumerate(rows)}

    def node(time_key: str, price_key: str, ptype: str) -> Optional[Dict[str, Any]]:
        dt = parse_time(row[time_key])
        price = safe_float(row[price_key])
        if dt is None or price is None:
            return None
        idx = by_time.get(dt)
        if idx is None:
            # Timestamps should normally match exactly. Keep a small fallback
            # for any API timestamp normalization difference.
            closest = min(range(len(rows)), key=lambda i: abs((rows[i]["time"] - dt).total_seconds())) if rows else None
            if closest is None or abs((rows[closest]["time"] - dt).total_seconds()) > 120:
                return None
            idx = closest
        return {"idx": idx, "time": dt, "price": price, "type": ptype}

    nodes = [
        node("start_time", "start_price", "L" if row["direction"] == "SHORT" else "H"),
        node("h1_time", "h1_price", "H"),
        node("l1_time", "l1_price", "L"),
        node("h2_time", "h2_price", "H"),
        node("l2_time", "l2_price", "L"),
        node("final_time", "final_price", "H" if row["direction"] == "SHORT" else "L"),
    ]
    if any(n is None for n in nodes):
        return None

    direction = row["direction"]
    return {
        "symbol": row["symbol"],
        "direction": direction,
        "start": nodes[0],
        "h1": nodes[1],
        "l1": nodes[2],
        "h2": nodes[3],
        "l2": nodes[4],
        "final": nodes[5],
        "tp": float(row["tp"]),
        "range_pct": float(row["range_pct"] or 0.0),
        "confirmed_time": parse_time(row["confirmed_time"]) or nodes[5]["time"],
        "hook_key": row["hook_key"],
        "node_indices": [n["idx"] for n in nodes],
    }


def ensure_pending_trade_charts(rows_by_symbol: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int]) -> None:
    """Retry Telegram charts for any open trade missing its signal chart."""
    conn = db_connect()
    try:
        trades = conn.execute(
            "SELECT hook_key,symbol,sl FROM trades WHERE status='OPEN' ORDER BY opened_at ASC"
        ).fetchall()
    finally:
        conn.close()

    for tr in trades:
        hook_key = tr["hook_key"]
        if trade_chart_notification_exists(hook_key):
            continue

        symbol = tr["symbol"]
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol, M5_CANDLES)
        if not rows:
            continue
        rows_by_symbol[symbol] = rows

        hook = load_hook_for_trade(hook_key, rows)
        if not hook:
            continue

        sl = {
            "price": float(tr["sl"]),
            "time": hook["final"]["time"],
            "type": "H" if hook["direction"] == "SHORT" else "L",
        }
        if send_new_signal_chart(hook, rows, sl):
            continue
        stats["hook_chart_errors"] += 1


def open_trade_details() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT symbol,direction,entry,tp,sl,pnl_pct,opened_at,last_price "
            "FROM trades WHERE status='OPEN' ORDER BY opened_at ASC"
        ).fetchall()
        out: List[Dict[str, Any]] = []
        now = utc_now()
        for row in rows:
            opened = parse_time(row["opened_at"]) or now
            duration_seconds = max(0.0, (now - opened).total_seconds())
            out.append({
                "symbol": row["symbol"],
                "direction": row["direction"],
                "entry": float(row["entry"]),
                "tp": float(row["tp"]),
                "sl": float(row["sl"]),
                "pnl_pct": float(row["pnl_pct"] or 0.0),
                "last_price": safe_float(row["last_price"]),
                "duration_seconds": duration_seconds,
            })
        return out
    finally:
        conn.close()


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    days, rem = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ------------------------------------------------------------
# PAPER TRADE MONITOR
# ------------------------------------------------------------

def trade_pnl_pct(direction: str, entry: float, last_price: float) -> float:
    if entry == 0:
        return 0.0
    if direction == "LONG":
        return (last_price - entry) / entry * 100.0
    return (entry - last_price) / entry * 100.0


def level_percentages(direction: str, entry: float, tp: float, sl: float) -> Tuple[float, float]:
    """Return favorable TP % and protective SL % relative to Entry.

    TP is shown as a positive reward percentage. SL is shown as a negative
    risk percentage, regardless of LONG/SHORT direction.
    """
    if entry == 0:
        return 0.0, 0.0
    tp_pct = abs(tp - entry) / abs(entry) * 100.0
    sl_pct = -abs(sl - entry) / abs(entry) * 100.0
    return tp_pct, sl_pct


def close_trade(trade_id: int, reason: str, last_price: float, opened_at: str) -> None:
    opened = parse_time(opened_at) or utc_now()
    now = utc_now()
    duration = max(0.0, (now - opened).total_seconds())
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT direction,entry FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not row:
            return
        pnl = trade_pnl_pct(row["direction"], row["entry"], last_price)
        conn.execute(
            """
            UPDATE trades
            SET status=?, pnl_pct=?, closed_at=?, close_reason=?, duration_seconds=?,
                last_checked_at=?, last_price=?
            WHERE id=? AND status='OPEN'
            """,
            (reason, pnl, utc_iso(now), reason, duration, utc_iso(now), last_price, trade_id),
        )
        conn.commit()
    finally:
        conn.close()


def monitor_open_trades(rows_by_symbol: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int], live_prices: Optional[Dict[str, float]] = None) -> None:
    conn = db_connect()
    try:
        trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    finally:
        conn.close()

    for tr in trades:
        symbol = tr["symbol"]
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol, M5_CANDLES)
        if not rows:
            continue
        rows_by_symbol[symbol] = rows
        stats["trades_checked"] += 1

        opened = parse_time(tr["opened_at"]) or utc_now()
        direction = tr["direction"]
        tp = float(tr["tp"])
        sl = float(tr["sl"])
        touched_reason = None
        touch_price = None

        # Search from the first candle that starts at/after the paper-open time.
        for candle in rows:
            if candle["time"] < opened:
                continue
            if direction == "LONG":
                hit_sl = candle["low"] <= sl
                hit_tp = candle["high"] >= tp
            else:
                hit_sl = candle["high"] >= sl
                hit_tp = candle["low"] <= tp

            if hit_sl and hit_tp:
                # Same-candle order cannot be reconstructed from OHLC.
                # Conservative choice: SL first.
                touched_reason = "SL"
                touch_price = sl
                break
            if hit_sl:
                touched_reason = "SL"
                touch_price = sl
                break
            if hit_tp:
                touched_reason = "TP"
                touch_price = tp
                break

        last_price = (live_prices or {}).get(symbol) or rows[-1]["close"]
        conn2 = db_connect()
        try:
            conn2.execute(
                "UPDATE trades SET last_checked_at=?, last_price=? WHERE id=? AND status='OPEN'",
                (utc_iso(), last_price, tr["id"]),
            )
            conn2.commit()
        finally:
            conn2.close()

        if touched_reason:
            close_trade(tr["id"], touched_reason, touch_price, tr["opened_at"])
            stats["trades_closed"] += 1


# ------------------------------------------------------------
# HEIKIN ASHI CHARTS
# ------------------------------------------------------------

def heikin_ashi(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    prev_ha_open = None
    prev_ha_close = None
    for r in rows:
        ha_close = (r["open"] + r["high"] + r["low"] + r["close"]) / 4.0
        if prev_ha_open is None:
            ha_open = (r["open"] + r["close"]) / 2.0
        else:
            ha_open = (prev_ha_open + prev_ha_close) / 2.0
        ha_high = max(r["high"], ha_open, ha_close)
        ha_low = min(r["low"], ha_open, ha_close)
        out.append({"time": r["time"], "open": ha_open, "high": ha_high, "low": ha_low, "close": ha_close})
        prev_ha_open = ha_open
        prev_ha_close = ha_close
    return out


def make_chart(symbol: str, rows: List[Dict[str, Any]], hook: Dict[str, Any], sl: Optional[Dict[str, Any]], tp_info: Dict[str, Any]) -> Optional[str]:
    try:
        final_idx = hook["final"]["idx"]
        start_idx = hook["start"]["idx"]
        left = max(0, start_idx - CHART_CONTEXT_BEFORE)
        right = min(len(rows), final_idx + CHART_CONTEXT_AFTER + 1)
        if right - left > CHART_CANDLES:
            right = min(len(rows), left + CHART_CANDLES)
        chart_rows = rows[left:right]
        if len(chart_rows) < 20:
            chart_rows = rows[max(0, len(rows) - CHART_CANDLES):]

        ha = heikin_ashi(chart_rows)
        x0 = chart_rows[0]["time"]
        xs = [(r["time"] - x0).total_seconds() / 60.0 for r in chart_rows]
        width = 3.4

        fig, ax = plt.subplots(figsize=(15, 8), dpi=150)
        for i, r in enumerate(ha):
            x = xs[i]
            o, h, l, c = r["open"], r["high"], r["low"], r["close"]
            ax.plot([x, x], [l, h], linewidth=0.8)
            bottom = min(o, c)
            height = max(abs(c - o), max(abs(o), 1e-12) * 0.00001)
            rect = Rectangle((x - width / 2, bottom), width, height, fill=False, linewidth=0.8)
            ax.add_patch(rect)

        if hook["direction"] == "LONG":
            nodes = [
                (hook["start"], "START"),
                (hook["l1"], "L1"),
                (hook["h1"], "H1"),
                (hook["l2"], "L2"),
                (hook["h2"], "H2"),
                (hook["final"], "L3"),
            ]
        else:
            nodes = [
                (hook["start"], "START"),
                (hook["h1"], "H1"),
                (hook["l1"], "L1"),
                (hook["h2"], "H2"),
                (hook["l2"], "L2"),
                (hook["final"], "H3"),
            ]

        # Draw the actual NDS path using each node's real time/price.
        # This makes the six-node geometry explicit instead of leaving
        # the viewer to infer it from the candles.
        node_xs = [(node["time"] - x0).total_seconds() / 60.0 for node, _ in nodes]
        node_ys = [node["price"] for node, _ in nodes]
        ax.plot(node_xs, node_ys, linewidth=1.6, linestyle="-")

        for node, label in nodes:
            x = (node["time"] - x0).total_seconds() / 60.0
            ax.scatter([x], [node["price"]], s=24)
            ax.annotate(label, (x, node["price"]), xytext=(4, 8), textcoords="offset points", fontsize=9)

        entry = hook["final"]["price"]
        tp = hook["tp"]
        tp_pct, sl_pct = level_percentages(hook["direction"], entry, tp, sl["price"] if sl is not None else entry)
        ax.axhline(entry, linewidth=1.0, linestyle="--")
        ax.axhline(tp, linewidth=1.0)
        ax.text(xs[-1], entry, f"  ENTRY {fmt_price(entry)}", va="bottom", fontsize=9)
        ax.text(xs[-1], tp, f"  86.4% TP {fmt_price(tp)} (+{tp_pct:.2f}%)", va="bottom", fontsize=9)

        if sl is not None:
            ax.axhline(sl["price"], linewidth=1.0, linestyle=":")
            ax.text(xs[-1], sl["price"], f"  SL {fmt_price(sl['price'])} ({sl_pct:.2f}%)", va="bottom", fontsize=9)

        title_status = "86.4% NOT TOUCHED"
        title = f"{symbol} | {hook['direction']} | NDS M5 | {title_status}\n"
        title += f"Range {hook['range_pct']:.2f}% | Final {utc_iso(hook['final']['time'])}"
        ax.set_title(title)
        ax.set_xlabel("Minutes")
        ax.set_ylabel("Price")
        ax.grid(alpha=0.15)

        # Focus the y-axis on the hook itself so the six NDS nodes and their
        # connecting lines remain visually meaningful instead of appearing flat
        # because of a much larger historical price swing.
        node_low = min(node_ys)
        node_high = max(node_ys)
        node_span = node_high - node_low
        if node_span > 0:
            pad = max(node_span * 0.18, abs(node_high) * 0.0008)
            low_limit = min(min(r["low"] for r in chart_rows), node_low)
            high_limit = max(max(r["high"] for r in chart_rows), node_high)
            # Keep some candle context but prioritize the NDS node range.
            low_limit = max(node_low - pad, low_limit)
            high_limit = min(node_high + pad, high_limit)
            if high_limit > low_limit:
                ax.set_ylim(low_limit, high_limit)

        fig.tight_layout()

        Path(CHART_DIR).mkdir(parents=True, exist_ok=True)
        name = f"{symbol}_{hook['direction']}_{ts_key(hook['final']['time'])}_{hook['final']['price']:.12g}.png".replace("/", "_")
        path = str(Path(CHART_DIR) / name)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None


# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send_photo(path: str, caption: str) -> bool:
    if not telegram_configured():
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"photo": f},
                timeout=REQUEST_TIMEOUT,
            )
        return r.ok
    except Exception:
        return False


def telegram_send_text(text: str) -> bool:
    if not telegram_configured():
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
        return r.ok
    except Exception:
        return False


def send_trade_alert(hook: Dict[str, Any], sl: Dict[str, Any], current_price: float) -> None:
    arrow = "🟢 LONG" if hook["direction"] == "LONG" else "🔴 SHORT"
    entry = hook["final"]["price"]
    tp_pct, sl_pct = level_percentages(hook["direction"], entry, hook["tp"], sl["price"])
    text = (
        f"{arrow} NEW SIGNAL\n"
        f"Symbol: {hook['symbol']}\n"
        f"Entry: {fmt_price(entry)}\n"
        f"SL: {fmt_price(sl['price'])} ({sl_pct:.2f}%)\n"
        f"TP: {fmt_price(hook['tp'])} (+{tp_pct:.2f}%)\n"
        f"Current: {fmt_price(current_price)}\n"
        f"Range: {hook['range_pct']:.2f}%\n"
        f"86.4%: NOT TOUCHED\n"
        f"Confirmed: {utc_iso(hook['confirmed_time'])}\n"
        f"Mode: PAPER ONLY"
    )
    telegram_send_text(text)


def send_new_signal_chart(hook: Dict[str, Any], rows: List[Dict[str, Any]], sl: Dict[str, Any]) -> bool:
    """Send a dedicated chart for a newly-created paper trade.

    This notification is separate from the generic hook-chart notification so
    a hook can have had a normal 86.4%-untouched chart sent earlier and still
    produce a distinct signal chart exactly when a new trade is created.
    """
    if trade_chart_notification_exists(hook["hook_key"]):
        return True

    tp_info = {"touched": False}
    path = make_chart(hook["symbol"], rows, hook, sl, tp_info)
    if not path:
        return False

    arrow = "🟢 LONG" if hook["direction"] == "LONG" else "🔴 SHORT"
    entry = hook["final"]["price"]
    tp_pct, sl_pct = level_percentages(hook["direction"], entry, hook["tp"], sl["price"])
    caption = (
        f"{arrow} NEW SIGNAL CHART\n"
        f"{hook['symbol']} | NDS M5\n"
        f"Entry: {fmt_price(entry)}\n"
        f"SL: {fmt_price(sl['price'])} ({sl_pct:.2f}%)\n"
        f"TP: {fmt_price(hook['tp'])} (+{tp_pct:.2f}%)\n"
        f"86.4%: NOT TOUCHED\n"
        f"Range: {hook['range_pct']:.2f}%\n"
        f"Confirmed: {utc_iso(hook['confirmed_time'])}"
    )
    if not telegram_send_photo(path, caption):
        return False

    mark_trade_chart_notification_sent(hook["hook_key"], path)
    return True


def send_open_trades_update(open_details: List[Dict[str, Any]], perf: Dict[str, Any]) -> None:
    if not open_details:
        return

    lines = [
        "📊 OPEN TRADES UPDATE",
        f"Open Trades: {len(open_details)}",
        f"Performance: {perf['win_rate']:.2f}% win | {perf['pnl']:+.2f}% total PnL",
        "",
    ]

    for tr in open_details:
        arrow = "🟢 LONG" if tr["direction"] == "LONG" else "🔴 SHORT"
        tp_pct, sl_pct = level_percentages(tr["direction"], tr["entry"], tr["tp"], tr["sl"])
        lines.extend([
            f"{arrow} | {tr['symbol']}",
            f"Entry: {fmt_price(tr['entry'])} | Current: {fmt_price(tr['last_price'])}",
            f"SL: {fmt_price(tr['sl'])} ({sl_pct:.2f}%) | TP: {fmt_price(tr['tp'])} (+{tp_pct:.2f}%)",
            f"PnL: {tr['pnl_pct']:+.2f}% | Duration: {format_duration(tr['duration_seconds'])}",
            "",
        ])

    telegram_send_text("\n".join(lines).rstrip())


def send_pending_trades_update(pending_details: List[Dict[str, Any]]) -> None:
    if not pending_details:
        return
    lines = [
        "⏳ PENDING TRADES",
        f"Waiting Trades: {len(pending_details)}",
        "Reason: MAX_OPEN_TRADES reached",
        "",
    ]
    for tr in pending_details:
        arrow = "🟢 LONG" if tr["direction"] == "LONG" else "🔴 SHORT"
        tp_pct, sl_pct = level_percentages(tr["direction"], tr["entry"], tr["tp"], tr["sl"])
        lines.extend([
            f"{arrow} | {tr['symbol']}",
            f"Entry: {fmt_price(tr['entry'])} | Current: {fmt_price(tr['last_price'])}",
            f"SL: {fmt_price(tr['sl'])} ({sl_pct:.2f}%) | TP: {fmt_price(tr['tp'])} (+{tp_pct:.2f}%)",
            f"PnL: {tr['pnl_pct']:+.2f}%",
            "",
        ])
    telegram_send_text("\n".join(lines).rstrip())


# ------------------------------------------------------------
# DIAGNOSTICS
# ------------------------------------------------------------

def count_hooks_by_age(all_hooks: List[Dict[str, Any]]) -> Dict[str, int]:
    now = utc_now()
    out = {"6h": 0, "12h": 0, "24h": 0, "48h": 0, "72h": 0}
    for h in all_hooks:
        age = (now - h["confirmed_time"]).total_seconds()
        if age <= 6 * 3600:
            out["6h"] += 1
        if age <= 12 * 3600:
            out["12h"] += 1
        if age <= 24 * 3600:
            out["24h"] += 1
        if age <= 48 * 3600:
            out["48h"] += 1
        if age <= 72 * 3600:
            out["72h"] += 1
    return out


def main() -> None:
    if REAL_TRADING or not PAPER_ONLY:
        raise RuntimeError("Safety stop: this scanner is configured for paper trading only.")

    init_db()
    scan_started = time.time()

    stats = {
        "scanned": 0,
        "requests": 0,
        "data_ok": 0,
        "data_error": 0,
        "empty": 0,
        "short": 0,
        "pivot_points": 0,
        "hooks": 0,
        "confirmed_hooks": 0,
        "recent_hooks": 0,
        "range_valid": 0,
        "valid_before_tp": 0,
        "tp_touched": 0,
        "sl_found": 0,
        "sl_fallback": 0,
        "geometry_valid": 0,
        "duplicate": 0,
        "max_open": 0,
        "signal_ready": 0,
        "new_hooks": 0,
        "hook_charts_sent": 0,
        "hook_chart_errors": 0,
        "signals": 0,
        "trade_duplicates": 0,
        "trades_checked": 0,
        "trades_closed": 0,
        "pending_checked": 0,
        "pending_expired": 0,
        "pending_saved": 0,
        "pending_promoted": 0,
    }

    all_recent_valid: List[Dict[str, Any]] = []
    all_hooks_for_age: List[Dict[str, Any]] = []
    rows_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    target_assets = get_target_assets(TARGET_ASSETS)
    stats["scanned"] = len(target_assets)

    # Snapshot the live Futures ticker before processing existing paper trades.
    # This is only for current-price/PnL display; candle OHLC remains the source
    # for NDS detection and TP/SL touch simulation.
    live_prices = get_live_prices(target_assets)
    refresh_live_trade_prices(live_prices)

    # Monitor DB-backed trades and waiting signals before scanning new hooks.
    monitor_open_trades(rows_by_symbol, stats, live_prices)
    update_pending_trades(rows_by_symbol, stats, live_prices)

    for symbol in target_assets:
        stats["requests"] += 1
        rows = fetch_ohlc(symbol, M5_CANDLES)
        if not rows:
            stats["data_error"] += 1
            time.sleep(SCAN_SLEEP_SECONDS)
            continue
        if len(rows) < PIVOT_LEFT + PIVOT_RIGHT + 20:
            stats["short"] += 1
            time.sleep(SCAN_SLEEP_SECONDS)
            continue

        stats["data_ok"] += 1
        rows_by_symbol[symbol] = rows
        hooks, pivot_count = detect_hooks(symbol, rows)
        stats["pivot_points"] += pivot_count
        stats["hooks"] += len(hooks)
        stats["confirmed_hooks"] += len(hooks)
        all_hooks_for_age.extend(hooks)

        pivots = build_ordered_pivots(rows)

        for hook in hooks:
            is_new = save_hook(hook)
            if is_new:
                stats["new_hooks"] += 1

            # The chart rule is intentionally independent from the trade rule:
            # send only charts where 86.4% has NOT been touched after H3/L3.
            touch = tp_touch_info(rows, hook)
            if touch["touched"]:
                stats["tp_touched"] += 1
            else:
                if not notification_exists(hook["hook_key"]):
                    sl_for_chart = find_protective_sl(hook, rows)
                    chart_path = make_chart(symbol, rows, hook, sl_for_chart, touch)
                    if chart_path:
                        tp_pct = abs(hook['tp'] - hook['final']['price']) / abs(hook['final']['price']) * 100.0
                        caption_lines = [
                            f"NDS M5 | {symbol} | {hook['direction']}",
                            f"H3/L3: {fmt_price(hook['final']['price'])}",
                        ]
                        if sl_for_chart:
                            _, sl_pct = level_percentages(hook['direction'], hook['final']['price'], hook['tp'], sl_for_chart['price'])
                            caption_lines.append(f"SL: {fmt_price(sl_for_chart['price'])} ({sl_pct:.2f}%)")
                        caption_lines += [
                            f"86.4% TP: {fmt_price(hook['tp'])} (+{tp_pct:.2f}%)",
                            "86.4% status: NOT TOUCHED",
                            f"Range: {hook['range_pct']:.2f}%",
                            f"Confirmed: {utc_iso(hook['confirmed_time'])}",
                        ]
                        caption = "\n".join(caption_lines)
                        if telegram_send_photo(chart_path, caption):
                            mark_notification_sent(hook["hook_key"], chart_path)
                            stats["hook_charts_sent"] += 1
                        else:
                            stats["hook_chart_errors"] += 1
                    else:
                        stats["hook_chart_errors"] += 1

            # Trade candidates are limited to recent + range valid + TP untouched.
            confirmed_age = age_seconds(hook["confirmed_time"])
            if confirmed_age <= M5_MAX_HOOK_AGE_SECONDS:
                stats["recent_hooks"] += 1
                if hook["range_pct"] >= M5_MIN_HOOK_RANGE_PCT:
                    stats["range_valid"] += 1
                    all_recent_valid.append({"hook": hook, "rows": rows, "tp_touch": touch})
                    if not touch["touched"]:
                        stats["valid_before_tp"] += 1

        time.sleep(SCAN_SLEEP_SECONDS)

    # Refresh immediately before creating alerts so NEW SIGNAL Current is not
    # taken from the last M5 candle close.
    live_prices = get_live_prices(target_assets)
    refresh_live_trade_prices(live_prices)

    # Create paper signals from the best/current valid hook candidates.
    # One signal per unique hook key. Existing DB prevents re-entry.
    for item in sorted(all_recent_valid, key=lambda x: x["hook"]["confirmed_time"]):
        hook = item["hook"]
        touch = item["tp_touch"]
        if touch["touched"]:
            continue

        sl = find_protective_sl(hook, item["rows"])
        if sl is None:
            continue
        stats["sl_found"] += 1
        if sl.get("source") == "MIDPOINT_FALLBACK":
            stats["sl_fallback"] += 1

        if not geometry_valid(hook, sl):
            continue
        stats["geometry_valid"] += 1

        if trade_exists(hook["hook_key"]):
            stats["duplicate"] += 1
            stats["trade_duplicates"] += 1
            continue

        if open_trade_count() >= MAX_OPEN_TRADES:
            stats["max_open"] += 1
            current_price = live_prices.get(hook["symbol"], hook["final"]["price"])
            if save_pending_trade(hook, sl, current_price):
                stats["pending_saved"] += 1
            continue

        stats["signal_ready"] += 1
        if save_trade(hook, sl):
            delete_pending_trade(hook["hook_key"], "PROMOTED")
            stats["pending_promoted"] += 1
            stats["signals"] += 1
            current_price = live_prices.get(hook["symbol"], hook["final"]["price"])
            send_trade_alert(hook, sl, current_price)

            # A new trade always gets its own Telegram chart. This is separate
            # from the general hook-chart notification and therefore cannot be
            # suppressed just because the hook chart was already sent.
            if not send_new_signal_chart(hook, item["rows"], sl):
                stats["hook_chart_errors"] += 1

    # Monitor newly created trades once more using the same candle data.
    monitor_open_trades(rows_by_symbol, stats, live_prices)

    # Guarantee that every still-open new signal has a Telegram chart.
    # Failed sends are retried on this run and on later runs until recorded.
    ensure_pending_trade_charts(rows_by_symbol, stats)
    update_pending_trades(rows_by_symbol, stats, live_prices)

    age_counts = count_hooks_by_age(all_hooks_for_age)
    newest = max(all_hooks_for_age, key=lambda x: x["confirmed_time"], default=None)

    # Final live ticker refresh keeps OPEN/PENDING Current and PnL genuinely
    # current for the Telegram diagnostic sent at the end of the run.
    live_prices = get_live_prices(target_assets)
    refresh_live_trade_prices(live_prices)

    perf = performance_summary()
    runtime = time.time() - scan_started

    lines = [
        "🔎 NDS M5 DIAGNOSTIC",
        f"Version: {VERSION}",
        f"Time: {utc_iso()}",
        f"Runtime: {runtime:.1f}s",
        "Current Price: Kraken Futures live ticker (last trade)",
        "",
        "━━━ ASSETS ━━━",
        f"Scanned: {stats['scanned']}",
        "",
        "━━━ M5 ━━━",
        f"Requests: {stats['requests']}",
        f"Data OK: {stats['data_ok']}",
        f"Data Error: {stats['data_error']}",
        f"Empty: {stats['empty']}",
        f"Short: {stats['short']}",
        f"Pivot Points: {stats['pivot_points']}",
        f"Hooks: {stats['hooks']}",
        f"Confirmed Hooks: {stats['confirmed_hooks']}",
        f"New Hooks Since Last Run: {stats['new_hooks']}",
        f"Recent Hooks ≤6h: {age_counts['6h']}",
        f"Recent ≤12h: {age_counts['12h']}",
        f"Recent ≤24h: {age_counts['24h']}",
        f"Recent ≤48h: {age_counts['48h']}",
        f"Recent ≤72h: {age_counts['72h']}",
        f"Range Valid Hooks ≥{M5_MIN_HOOK_RANGE_PCT:.2f}%: {stats['range_valid']}",
        f"Recent + Range Valid + TP Untouched: {stats['valid_before_tp']}",
        f"TP Already Touched After H3/L3: {stats['tp_touched']}",
        f"SL Found: {stats['sl_found']}",
        f"SL Fallback (50% Entry-TP): {stats['sl_fallback']}",
        f"Geometry Valid: {stats['geometry_valid']}",
        f"Duplicate: {stats['duplicate']}",
        f"Max Open: {stats['max_open']}",
        f"Signal Ready: {stats['signal_ready']}",
        f"Node Spacing: ≥{MIN_NODE_CANDLES} candles ({MIN_NODE_CANDLES * M5_INTERVAL_MINUTES} min)",
        f"Hook Charts Sent: {stats['hook_charts_sent']}",
        f"Hook Chart Errors: {stats['hook_chart_errors']}",
        f"Signals: {stats['signals']}",
        f"Trade Duplicates: {stats['trade_duplicates']}",
        "",
        "━━━ PAPER TRADES ━━━",
        f"Open Trades: {perf['open']}",
        f"Closed Trades: {perf['closed']}",
        f"Trades checked this run: {stats['trades_checked']}",
        f"Trades closed this run: {stats['trades_closed']}",
        f"TP: {perf['tp']}  SL: {perf['sl']}",
        f"Success Rate: {perf['win_rate']:.2f}%",
        f"Total PnL: {perf['pnl']:+.2f}%",
        f"Gross Profit: {perf['gross_profit']:+.2f}%",
        f"Gross Loss: {perf['gross_loss']:+.2f}%",
    ]

    open_details = open_trade_details()
    if open_details:
        lines += ["", "━━━ OPEN TRADES ━━━"]
        for tr in open_details:
            arrow = "🟢 LONG" if tr["direction"] == "LONG" else "🔴 SHORT"
            lines += [
                f"{arrow} | {tr['symbol']}",
                f"Entry: {fmt_price(tr['entry'])} | Current: {fmt_price(tr['last_price'])}",
                f"SL: {fmt_price(tr['sl'])} ({level_percentages(tr['direction'], tr['entry'], tr['tp'], tr['sl'])[1]:.2f}%) | TP: {fmt_price(tr['tp'])} (+{level_percentages(tr['direction'], tr['entry'], tr['tp'], tr['sl'])[0]:.2f}%)",
                f"Current PnL: {tr['pnl_pct']:+.2f}% | Duration: {format_duration(tr['duration_seconds'])}",
            ]

    pending_details = pending_trade_details()
    if pending_details:
        lines += ["", "━━━ PENDING TRADES ━━━", f"Waiting Trades: {len(pending_details)}", "Reason: MAX_OPEN_TRADES reached"]
        for tr in pending_details:
            arrow = "🟢 LONG" if tr["direction"] == "LONG" else "🔴 SHORT"
            tp_pct, sl_pct = level_percentages(tr["direction"], tr["entry"], tr["tp"], tr["sl"])
            lines += [
                f"{arrow} | {tr['symbol']}",
                f"Entry: {fmt_price(tr['entry'])} | Current: {fmt_price(tr['last_price'])}",
                f"SL: {fmt_price(tr['sl'])} ({sl_pct:.2f}%) | TP: {fmt_price(tr['tp'])} (+{tp_pct:.2f}%)",
                f"Current PnL: {tr['pnl_pct']:+.2f}% | Waiting",
            ]

    if newest:
        age_h = max(0.0, (utc_now() - newest["confirmed_time"]).total_seconds() / 3600.0)
        lines += [
            "",
            "━━━ NEWEST HOOK IN CURRENT WINDOW ━━━",
            f"{newest['symbol']} {newest['direction']}",
            f"Confirmed: {utc_iso(newest['confirmed_time'])}",
            f"Age: {age_h:.1f}h",
            f"H3/L3: {fmt_price(newest['final']['price'])}",
            f"TP: {fmt_price(newest['tp'])} (+{abs(newest['tp'] - newest['final']['price']) / abs(newest['final']['price']) * 100.0:.2f}%)",
        ]

    report_text = "\n".join(lines)
    print(report_text, flush=True)
    telegram_send_text(report_text)

    # Open trades are sent as a dedicated Telegram update every run while any
    # trade remains open, including current PnL and duration.
    if open_details:
        send_open_trades_update(open_details, perf)
    if pending_details:
        send_pending_trades_update(pending_details)


if __name__ == "__main__":
    main()
