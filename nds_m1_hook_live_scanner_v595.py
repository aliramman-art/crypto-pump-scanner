# ============================================================
# NDS M1 LIVE SCANNER - VERSION 5.9.5 FIXED
# PAPER TRADING ONLY - NO REAL ORDERS
# File name intentionally unchanged; no workflow changes required.
# Fixes: HA reversal strictly after H3/L3; only closed candles confirm;
# strict Kraken last-traded price (no mark/bid/ask/candle-close fallback);
# case-insensitive symbols; stale prices are labeled as last known;
# updated chart is sent for each newly accepted signal, even for an old hook.
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

VERSION = "5.9.5"
REAL_TRADING = False
PAPER_ONLY = True
TARGET_ASSETS = 150
M1_INTERVAL = "1m"
M1_INTERVAL_MINUTES = 1
M1_CANDLES = 1500
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_NODE_CANDLES = 15
NDS_RETRACE = 0.864
M1_MIN_HOOK_RANGE_PCT = 0.20
M1_MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
MAX_OPEN_TRADES = 3
REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CONTEXT_BEFORE = 60
CHART_CONTEXT_AFTER = 60
DB_FILE = "nds_m1_v592.db"
CHART_DIR = "nds_m1_charts"
KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M1-Scanner/{VERSION}"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utc_now()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 10_000_000_000:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except Exception:
            return None
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
        return x if math.isfinite(x) else None
    except Exception:
        return None


def fmt_price(x: Optional[float]) -> str:
    if x is None:
        return "-"
    ax = abs(x)
    if ax >= 1000: return f"{x:.2f}"
    if ax >= 100: return f"{x:.3f}"
    if ax >= 1: return f"{x:.4f}"
    if ax >= 0.01: return f"{x:.6f}"
    if ax >= 0.0001: return f"{x:.8f}"
    return f"{x:.10f}"


def age_seconds(dt: datetime) -> float:
    return max(0.0, (utc_now() - dt).total_seconds())


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        r = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
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
    val = item.get("symbol") or item.get("ticker") or item.get("product_id") or item.get("instrument")
    return val.strip().upper() if isinstance(val, str) else None


def get_target_assets(limit: int = TARGET_ASSETS) -> List[str]:
    symbols = []
    data = get_json(KRAKEN_INSTRUMENTS_URL)
    if data:
        for item in extract_items(data):
            if isinstance(item, dict):
                sym = item_symbol(item)
                if sym and sym.startswith("PF_"):
                    symbols.append(sym)
    if not symbols:
        data = get_json(KRAKEN_TICKERS_URL)
        if data:
            for item in extract_items(data):
                if isinstance(item, dict):
                    sym = item_symbol(item)
                    if sym and sym.startswith("PF_"):
                        symbols.append(sym)
    return sorted(set(symbols))[:limit]


def get_live_prices(symbols: Optional[List[str]] = None) -> Dict[str, float]:
    """Return only Kraken ticker `last` prices; never invent a live price."""
    data = get_json(KRAKEN_TICKERS_URL)
    if not data:
        return {}
    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    prices: Dict[str, float] = {}
    for item in extract_items(data):
        if not isinstance(item, dict):
            continue
        symbol = item_symbol(item)
        if not symbol or not symbol.startswith("PF_"):
            continue
        if wanted is not None and symbol not in wanted:
            continue
        price = safe_float(item.get("last"))
        if price is not None and price > 0:
            prices[symbol] = price
    return prices


def normalize_candle(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, dict):
        t, o, h, l, c = item.get("time"), item.get("open"), item.get("high"), item.get("low"), item.get("close")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        t, o, h, l, c = item[:5]
    else:
        return None
    dt = parse_time(t)
    o, h, l, c = safe_float(o), safe_float(h), safe_float(l), safe_float(c)
    if dt is None or None in (o, h, l, c) or h < l:
        return None
    return {"time": dt, "open": o, "high": h, "low": l, "close": c}


def fetch_ohlc(symbol: str, candles: int = M1_CANDLES) -> List[Dict[str, Any]]:
    end_dt = utc_now()
    start_dt = end_dt - timedelta(minutes=M1_INTERVAL_MINUTES * (candles + 20))
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/1m"
    cursor_s, end_s = int(start_dt.timestamp()), int(end_dt.timestamp())
    rows_by_ms: Dict[int, Dict[str, Any]] = {}
    for _ in range(8):
        try:
            r = SESSION.get(url, params={"from": cursor_s, "to": end_s}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            print(f"OHLC_ERROR | symbol={symbol} | {type(exc).__name__}: {exc}")
            return []
        raw = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            break
        page_times = []
        for item in raw:
            c = normalize_candle(item)
            if c:
                ms = int(c["time"].timestamp() * 1000)
                rows_by_ms[ms] = c
                page_times.append(ms)
        if not page_times:
            break
        last_s = max(page_times) // 1000
        if last_s <= cursor_s:
            break
        cursor_s = last_s + 60
        if cursor_s >= end_s:
            break
    rows = [rows_by_ms[k] for k in sorted(rows_by_ms)]
    return rows[-candles:] if len(rows) > candles else rows


def candle_is_closed(candle: Dict[str, Any]) -> bool:
    dt = parse_time(candle.get("time"))
    return bool(dt and dt + timedelta(minutes=1) <= utc_now())


def is_pivot_high(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows): return False
    v = rows[i]["high"]
    return v >= max(rows[j]["high"] for j in range(i-PIVOT_LEFT, i+PIVOT_RIGHT+1)) and v > max(rows[j]["high"] for j in range(i-PIVOT_LEFT, i))


def is_pivot_low(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows): return False
    v = rows[i]["low"]
    return v <= min(rows[j]["low"] for j in range(i-PIVOT_LEFT, i+PIVOT_RIGHT+1)) and v < min(rows[j]["low"] for j in range(i-PIVOT_LEFT, i))


def build_ordered_pivots(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pivots = []
    for i in range(len(rows)):
        ph, pl = is_pivot_high(rows, i), is_pivot_low(rows, i)
        if not ph and not pl: continue
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
    out, prev_o, prev_c = [], None, None
    for r in rows:
        hc = (r["open"] + r["high"] + r["low"] + r["close"]) / 4
        ho = (r["open"] + r["close"]) / 2 if prev_o is None else (prev_o + prev_c) / 2
        out.append({"time": r["time"], "open": ho, "high": max(r["high"], ho, hc), "low": min(r["low"], ho, hc), "close": hc})
        prev_o, prev_c = ho, hc
    return out


def find_closed_ha_reversal(rows: List[Dict[str, Any]], final_idx: int, direction: str) -> Optional[Dict[str, Any]]:
    """Find a closed HA reversal strictly AFTER the final H3/L3 candle."""
    if len(rows) < 2 or final_idx < 0 or final_idx >= len(rows): return None
    ha = heikin_ashi(rows)
    green = lambda c: c["close"] >= c["open"]
    for i in range(max(1, final_idx + 1), len(ha)):
        if not candle_is_closed(rows[i]):
            continue
        prev_green, now_green = green(ha[i-1]), green(ha[i])
        if direction == "SHORT" and prev_green and not now_green:
            return {"idx": i, "time": rows[i]["time"], "from_color": "GREEN", "to_color": "RED"}
        if direction == "LONG" and not prev_green and now_green:
            return {"idx": i, "time": rows[i]["time"], "from_color": "RED", "to_color": "GREEN"}
    return None


def make_hook_key(symbol: str, direction: str, final_time: datetime, final_price: float) -> str:
    return f"{symbol}|{direction}|{ts_key(final_time)}|{final_price:.12g}"


def build_hook(symbol: str, rows: List[Dict[str, Any]], nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(nodes) != 6 or not node_spacing_ok(nodes): return None
    types = "".join(n["type"] for n in nodes)
    if types == "LHLHLH":
        start, h1, l1, h2, l2, final = nodes
        if not all(start["price"] < n["price"] for n in nodes[1:]): return None
        if not (h2["price"] > h1["price"] and l2["price"] < l1["price"] and final["price"] > h2["price"]): return None
        direction = "SHORT"
        tp = final["price"] - NDS_RETRACE * (final["price"] - start["price"])
    elif types == "HLHLHL":
        start, l1, h1, l2, h2, final = nodes
        if not all(start["price"] > n["price"] for n in nodes[1:]): return None
        if not (l2["price"] < l1["price"] and h2["price"] > h1["price"] and final["price"] < l2["price"]): return None
        direction = "LONG"
        tp = final["price"] + NDS_RETRACE * (start["price"] - final["price"])
    else:
        return None
    start_price, final_price = start["price"], final["price"]
    if start_price == 0: return None
    reversal = find_closed_ha_reversal(rows, final["idx"], direction)
    if reversal is None: return None
    pivot_confirm_time = final["time"] + timedelta(minutes=(PIVOT_RIGHT + 1) * M1_INTERVAL_MINUTES)
    confirmed_time = max(pivot_confirm_time, reversal["time"] + timedelta(minutes=1))
    return {"symbol": symbol, "direction": direction, "start": start, "h1": h1, "l1": l1, "h2": h2, "l2": l2,
            "final": final, "tp": tp, "range_pct": abs(final_price-start_price)/abs(start_price)*100,
            "confirmed_time": confirmed_time, "ha_reversal_time": reversal["time"], "ha_reversal_idx": reversal["idx"],
            "ha_reversal_from": reversal["from_color"], "ha_reversal_to": reversal["to_color"],
            "hook_key": make_hook_key(symbol, direction, final["time"], final_price), "node_indices": [n["idx"] for n in nodes]}


def detect_hooks(symbol: str, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    pivots, hooks, seen = build_ordered_pivots(rows), [], set()
    for i in range(max(0, len(pivots)-5)):
        nodes = pivots[i:i+6]
        if len(nodes) != 6 or nodes[-1]["idx"] + PIVOT_RIGHT >= len(rows): continue
        if not candle_is_closed(rows[nodes[-1]["idx"] + PIVOT_RIGHT]): continue
        hook = build_hook(symbol, rows, nodes)
        if hook and hook["hook_key"] not in seen:
            seen.add(hook["hook_key"]); hooks.append(hook)
    return hooks, len(pivots)


def tp_touch_info(rows: List[Dict[str, Any]], hook: Dict[str, Any]) -> Dict[str, Any]:
    tp, direction = hook["tp"], hook["direction"]
    for i in range(hook["final"]["idx"]+1, len(rows)):
        c = rows[i]
        touched = c["low"] <= tp if direction == "SHORT" else c["high"] >= tp
        if touched:
            return {"touched": True, "idx": i, "time": c["time"], "price": tp, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
    return {"touched": False}


def trade_pnl_pct(direction: str, entry: float, current: float) -> float:
    if entry == 0: return 0.0
    return (current-entry)/entry*100 if direction == "LONG" else (entry-current)/entry*100


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db_connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS hooks (id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT UNIQUE, symbol TEXT, direction TEXT, start_time TEXT, start_price REAL, h1_time TEXT, h1_price REAL, l1_time TEXT, l1_price REAL, h2_time TEXT, h2_price REAL, l2_time TEXT, l2_price REAL, final_time TEXT, final_price REAL, tp REAL, range_pct REAL, confirmed_time TEXT, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT UNIQUE, symbol TEXT, direction TEXT, entry REAL, tp REAL, sl REAL, status TEXT, pnl_pct REAL, opened_at TEXT, closed_at TEXT, close_reason TEXT, duration_seconds REAL, last_checked_at TEXT, last_price REAL, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pending_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, hook_key TEXT UNIQUE, symbol TEXT, direction TEXT, entry REAL, tp REAL, sl REAL, pnl_pct REAL, last_price REAL, status TEXT, confirmed_at TEXT, created_at TEXT, last_checked_at TEXT)""")
        conn.execute("CREATE TABLE IF NOT EXISTS hook_chart_notifications (hook_key TEXT PRIMARY KEY, path TEXT, sent_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS trade_chart_notifications (hook_key TEXT PRIMARY KEY, path TEXT, sent_at TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol,status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_trades(status)")
        conn.commit()
    finally: conn.close()


def save_hook(hook: Dict[str, Any]) -> bool:
    conn = db_connect()
    try:
        cur = conn.execute("""INSERT OR IGNORE INTO hooks (hook_key,symbol,direction,start_time,start_price,h1_time,h1_price,l1_time,l1_price,h2_time,h2_price,l2_time,l2_price,final_time,final_price,tp,range_pct,confirmed_time,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hook["hook_key"], hook["symbol"], hook["direction"], utc_iso(hook["start"]["time"]), hook["start"]["price"], utc_iso(hook["h1"]["time"]), hook["h1"]["price"], utc_iso(hook["l1"]["time"]), hook["l1"]["price"], utc_iso(hook["h2"]["time"]), hook["h2"]["price"], utc_iso(hook["l2"]["time"]), hook["l2"]["price"], utc_iso(hook["final"]["time"]), hook["final"]["price"], hook["tp"], hook["range_pct"], utc_iso(hook["confirmed_time"]), utc_iso()))
        conn.commit(); return cur.rowcount == 1
    finally: conn.close()


def trade_exists(hook_key: str) -> bool:
    conn = db_connect()
    try: return conn.execute("SELECT 1 FROM trades WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone() is not None
    finally: conn.close()


def open_trade_count() -> int:
    conn = db_connect()
    try: return int(conn.execute("SELECT COUNT(*) n FROM trades WHERE status='OPEN'").fetchone()["n"] or 0)
    finally: conn.close()


def save_trade(hook: Dict[str, Any]) -> bool:
    conn = db_connect()
    try:
        now = utc_iso()
        cur = conn.execute("""INSERT OR IGNORE INTO trades (hook_key,symbol,direction,entry,tp,sl,status,pnl_pct,opened_at,created_at,last_checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (hook["hook_key"], hook["symbol"], hook["direction"], hook["final"]["price"], hook["tp"], None, "OPEN", 0.0, now, now, now))
        conn.commit()
        saved = conn.execute("SELECT id,status,opened_at FROM trades WHERE hook_key=? LIMIT 1", (hook["hook_key"],)).fetchone()
        print(f"DB_SAVE_TRADE | symbol={hook['symbol']} | inserted={cur.rowcount == 1} | verified={saved is not None} | status={saved['status'] if saved else 'MISSING'} | db={Path(DB_FILE).resolve()}")
        return cur.rowcount == 1 and saved is not None
    except Exception as exc:
        conn.rollback(); print(f"DB_SAVE_TRADE_ERROR | {type(exc).__name__}: {exc}"); raise
    finally: conn.close()


def trade_db_snapshot() -> Dict[str, Any]:
    conn = db_connect()
    try:
        counts = {str(r["status"]): int(r["n"]) for r in conn.execute("SELECT COALESCE(status,'NULL') status,COUNT(*) n FROM trades GROUP BY status")}
        total = int(conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] or 0)
        return {"total": total, "open": counts.get("OPEN",0), "tp": counts.get("TP",0), "statuses": counts, "db_path": str(Path(DB_FILE).resolve()), "db_bytes": Path(DB_FILE).stat().st_size if Path(DB_FILE).exists() else 0}
    finally: conn.close()


def save_pending_trade(hook: Dict[str, Any], current_price: float) -> None:
    entry, tp = float(hook["final"]["price"]), float(hook["tp"])
    conn = db_connect()
    try:
        conn.execute("""INSERT INTO pending_trades (hook_key,symbol,direction,entry,tp,sl,pnl_pct,last_price,status,confirmed_at,created_at,last_checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(hook_key) DO UPDATE SET entry=excluded.entry,tp=excluded.tp,pnl_pct=excluded.pnl_pct,last_price=excluded.last_price,status='PENDING',last_checked_at=excluded.last_checked_at""",
            (hook["hook_key"],hook["symbol"],hook["direction"],entry,tp,None,trade_pnl_pct(hook["direction"],entry,current_price),current_price,"PENDING",utc_iso(hook["confirmed_time"]),utc_iso(),utc_iso()))
        conn.commit()
    finally: conn.close()


def delete_pending_trade(hook_key: str, status: str) -> None:
    conn = db_connect()
    try: conn.execute("UPDATE pending_trades SET status=?,last_checked_at=? WHERE hook_key=? AND status='PENDING'", (status,utc_iso(),hook_key)); conn.commit()
    finally: conn.close()


def refresh_live_trade_prices(live_prices: Dict[str, float]) -> None:
    if not live_prices: return
    now, now_iso = utc_now(), utc_iso()
    conn = db_connect()
    try:
        for r in conn.execute("SELECT id,symbol,direction,entry,opened_at FROM trades WHERE status='OPEN'").fetchall():
            p = live_prices.get(str(r["symbol"]).upper())
            if p is None: continue
            opened = parse_time(r["opened_at"]) or now
            conn.execute("UPDATE trades SET pnl_pct=?,last_price=?,last_checked_at=?,duration_seconds=? WHERE id=? AND status='OPEN'", (trade_pnl_pct(r["direction"],float(r["entry"]),p),p,now_iso,max(0,(now-opened).total_seconds()),r["id"]))
        for r in conn.execute("SELECT hook_key,symbol,direction,entry FROM pending_trades WHERE status='PENDING'").fetchall():
            p = live_prices.get(str(r["symbol"]).upper())
            if p is not None:
                conn.execute("UPDATE pending_trades SET pnl_pct=?,last_price=?,last_checked_at=? WHERE hook_key=? AND status='PENDING'", (trade_pnl_pct(r["direction"],float(r["entry"]),p),p,now_iso,r["hook_key"]))
        conn.commit()
    finally: conn.close()


def load_hook_for_trade(hook_key: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try: row = conn.execute("SELECT * FROM hooks WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone()
    finally: conn.close()
    if not row: return None
    by_time = {r["time"]: i for i,r in enumerate(rows)}
    def node(tk,pk,typ):
        dt, price = parse_time(row[tk]), safe_float(row[pk])
        if dt is None or price is None: return None
        idx = by_time.get(dt)
        if idx is None and rows:
            idx = min(range(len(rows)), key=lambda i: abs((rows[i]["time"]-dt).total_seconds()))
            if abs((rows[idx]["time"]-dt).total_seconds()) > 120: return None
        return {"idx":idx,"time":dt,"price":price,"type":typ}
    ns = [node("start_time","start_price","L" if row["direction"]=="SHORT" else "H"),node("h1_time","h1_price","H"),node("l1_time","l1_price","L"),node("h2_time","h2_price","H"),node("l2_time","l2_price","L"),node("final_time","final_price","H" if row["direction"]=="SHORT" else "L")]
    if any(n is None for n in ns): return None
    return {"symbol":row["symbol"],"direction":row["direction"],"start":ns[0],"h1":ns[1],"l1":ns[2],"h2":ns[3],"l2":ns[4],"final":ns[5],"tp":float(row["tp"]),"range_pct":float(row["range_pct"] or 0),"confirmed_time":parse_time(row["confirmed_time"]) or ns[5]["time"],"hook_key":row["hook_key"],"node_indices":[n["idx"] for n in ns]}


def monitor_open_trades(rows_by_symbol: Dict[str,List[Dict[str,Any]]], stats: Dict[str,int], live_prices: Dict[str,float]) -> None:
    conn = db_connect()
    try: trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    finally: conn.close()
    now, now_iso = utc_now(), utc_iso()
    for tr in trades:
        symbol = tr["symbol"]
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol,M1_CANDLES)
        if not rows: continue
        rows_by_symbol[symbol] = rows
        entry,tp,direction = float(tr["entry"]),float(tr["tp"]),tr["direction"]
        opened = parse_time(tr["opened_at"]) or now
        hit = None
        for candle in rows:
            if candle["time"] < opened or not candle_is_closed(candle): continue
            if (direction=="LONG" and candle["high"]>=tp) or (direction=="SHORT" and candle["low"]<=tp):
                hit = candle; break
        conn2 = db_connect()
        try:
            if hit:
                closed = hit["time"] + timedelta(minutes=1)
                conn2.execute("UPDATE trades SET status='TP',pnl_pct=?,closed_at=?,close_reason='TP',duration_seconds=?,last_checked_at=?,last_price=? WHERE id=? AND status='OPEN'", (trade_pnl_pct(direction,entry,tp),utc_iso(closed),max(0,(closed-opened).total_seconds()),now_iso,tp,tr["id"]))
                stats["tp_closed"] += 1
            else:
                p = live_prices.get(str(symbol).upper())
                if p is not None:
                    conn2.execute("UPDATE trades SET pnl_pct=?,last_price=?,last_checked_at=?,duration_seconds=? WHERE id=? AND status='OPEN'", (trade_pnl_pct(direction,entry,p),p,now_iso,max(0,(now-opened).total_seconds()),tr["id"]))
            conn2.commit()
        finally: conn2.close()


def update_pending_trades(rows_by_symbol: Dict[str,List[Dict[str,Any]]], stats: Dict[str,int], live_prices: Dict[str,float]) -> None:
    conn = db_connect()
    try: pending = conn.execute("SELECT * FROM pending_trades WHERE status='PENDING'").fetchall()
    finally: conn.close()
    for tr in pending:
        confirmed = parse_time(tr["confirmed_at"]) or utc_now()
        if age_seconds(confirmed) > M1_MAX_HOOK_AGE_SECONDS:
            delete_pending_trade(tr["hook_key"],"EXPIRED"); stats["pending_expired"] += 1; continue
        symbol = tr["symbol"]
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol,M1_CANDLES)
        if not rows: continue
        rows_by_symbol[symbol] = rows
        hook = load_hook_for_trade(tr["hook_key"],rows)
        if hook and tp_touch_info(rows,hook)["touched"]:
            delete_pending_trade(tr["hook_key"],"EXPIRED_TP"); stats["pending_expired"] += 1; continue
        p = live_prices.get(str(symbol).upper())
        if p is None:
            print(f"PENDING_PRICE_UNAVAILABLE | symbol={symbol} | stale price not substituted")
            continue
        conn2 = db_connect()
        try:
            conn2.execute("UPDATE pending_trades SET pnl_pct=?,last_price=?,last_checked_at=? WHERE hook_key=? AND status='PENDING'", (trade_pnl_pct(tr["direction"],float(tr["entry"]),p),p,utc_iso(),tr["hook_key"]))
            conn2.commit()
        finally: conn2.close()
        stats["pending_checked"] += 1


def performance_summary() -> Dict[str,Any]:
    conn = db_connect()
    try:
        row = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(pnl_pct),0) pnl,COALESCE(SUM(CASE WHEN pnl_pct>0 THEN pnl_pct ELSE 0 END),0) gp,COALESCE(SUM(CASE WHEN pnl_pct<0 THEN pnl_pct ELSE 0 END),0) gl FROM trades WHERE status='TP'").fetchone()
        tp = int(row["n"] or 0); op = int(conn.execute("SELECT COUNT(*) n FROM trades WHERE status='OPEN'").fetchone()["n"] or 0)
        return {"closed":tp,"pnl":float(row["pnl"] or 0),"gross_profit":float(row["gp"] or 0),"gross_loss":float(row["gl"] or 0),"tp":tp,"open":op,"win_rate":100.0 if tp else 0.0}
    finally: conn.close()


def make_chart(symbol: str, rows: List[Dict[str,Any]], hook: Dict[str,Any], reason: str="", path: Optional[str]=None, current: Optional[float]=None, include_latest: bool=False):
    if hook["direction"] == "SHORT":
        nodes=[hook["start"],hook["h1"],hook["l1"],hook["h2"],hook["l2"],hook["final"]]; labels=["START","H1","L1","H2","L2","H3"]
    else:
        nodes=[hook["start"],hook["l1"],hook["h1"],hook["l2"],hook["h2"],hook["final"]]; labels=["START","L1","H1","L2","H2","L3"]
    idxs=[n["idx"] for n in nodes]; lo=max(0,min(idxs)-CHART_CONTEXT_BEFORE); hi=min(len(rows),max(idxs)+CHART_CONTEXT_AFTER+1)
    if include_latest:
        # Open-trade updates must show the newest candles, not only the candles
        # around the historical hook. Keep the full interval from START to now.
        hi=len(rows)
    base=rows[lo:hi]
    if not base: return None
    ha=heikin_ashi(base); fig,ax=plt.subplots(figsize=(18,10))
    for x,r in enumerate(ha):
        color="#159447" if r["close"]>=r["open"] else "#d9363e"
        ax.vlines(x,r["low"],r["high"],color=color,linewidth=.85,zorder=2)
        bottom=min(r["open"],r["close"]); height=max(abs(r["close"]-r["open"]),1e-12)
        ax.add_patch(Rectangle((x-.34,bottom),.68,height,facecolor=color,edgecolor=color,linewidth=.5,alpha=.95,zorder=3))
    xmap={r["time"]:i for i,r in enumerate(base)}
    tick_count=min(12,len(base))
    if tick_count>1:
        positions=sorted(set(round(i*(len(base)-1)/(tick_count-1)) for i in range(tick_count)))
        ax.set_xticks(positions); ax.set_xticklabels([base[i]["time"].strftime("%m-%d %H:%M") for i in positions],rotation=35,ha="right",fontsize=8)
    ax.set_xlabel("Candle time (UTC)"); ax.set_ylabel("Price")
    xs=[]; ys=[]
    for n in nodes:
        x=xmap.get(n["time"])
        if x is not None: xs.append(x); ys.append(n["price"])
    if len(xs)==6: ax.plot(xs,ys,linewidth=2,marker="o",color="#2463eb",zorder=5)
    for n,label in zip(nodes,labels):
        x=xmap.get(n["time"])
        if x is not None:
            ax.annotate(f"{label}\n{n['time'].strftime('%m-%d %H:%M')}",(x,n["price"]),xytext=(0,9),textcoords="offset points",ha="center",fontsize=7.5,fontweight="bold",bbox=dict(boxstyle="round,pad=.2",facecolor="white",edgecolor="#888",alpha=.88),zorder=7)
    entry=float(hook["final"]["price"]); tp=float(hook["tp"]); tp_pct=abs(entry-tp)/abs(entry)*100 if entry else 0
    ax.axhline(entry,linestyle="--",linewidth=1.2,label=f"ENTRY {fmt_price(entry)}")
    ax.axhline(tp,linestyle=":",linewidth=1.5,label=f"TP 86.4% ({tp_pct:.2f}%) {fmt_price(tp)}")
    if current is not None and current>0:
        ax.axhline(current,color="#ff8c00",linestyle="-.",linewidth=1.3,label=f"LATEST TRADED PRICE {fmt_price(current)}")
    reversal_time=hook.get("ha_reversal_time"); reversal_idx=hook.get("ha_reversal_idx")
    if reversal_time is not None and reversal_idx is not None:
        local_x=reversal_idx-lo
        if 0<=local_x<len(base):
            ax.axvline(local_x,color="#8a2be2",linestyle="-.",linewidth=1.2,label="Closed HA reversal")
            ax.annotate(f"HA REVERSAL\n{utc_iso(reversal_time)}",(local_x,base[local_x]["close"]),xytext=(8,-28),textcoords="offset points",fontsize=7.5,color="#6a1b9a",bbox=dict(boxstyle="round,pad=.2",facecolor="white",alpha=.9))
    touch=tp_touch_info(rows,hook)
    tp_status=f"TP 86.4%: {'TOUCHED | '+utc_iso(touch['time']) if touch['touched'] else 'NOT TOUCHED'} | {fmt_price(tp)}"
    chart_mode = "OPEN TRADE UPDATE" if include_latest else "HOOK / SIGNAL"
    ax.set_title(f"NDS M1 | {symbol} | {hook['direction']} | {chart_mode} | TP 86.4% | PAPER ONLY"); ax.legend(loc="best"); ax.grid(alpha=.20); ax.margins(x=.025)
    fig.text(.5,.035,tp_status,ha="center",va="bottom",fontsize=10,fontweight="bold",wrap=True)
    fig.text(.5,.012,f"DECISION / REASON: {reason or 'ACCEPTED: Candidate'}",ha="center",va="bottom",fontsize=9,fontweight="bold",wrap=True)
    fig.tight_layout(rect=(0,.075,1,1)); Path(CHART_DIR).mkdir(parents=True,exist_ok=True)
    out=path or str(Path(CHART_DIR)/f"{symbol}_{hook['direction']}_{ts_key(hook['final']['time'])}.png")
    fig.savefig(out,dpi=150); plt.close(fig); return out


def telegram_ready() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def tg_send_message(text: str) -> bool:
    if not telegram_ready(): return False
    try:
        r=SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",data={"chat_id":TELEGRAM_CHAT_ID,"text":text},timeout=REQUEST_TIMEOUT)
        try: payload=r.json()
        except Exception: payload={}
        return r.ok and payload.get("ok",True) is True
    except Exception as exc:
        print(f"TELEGRAM_MESSAGE_ERROR | {type(exc).__name__}: {exc}"); return False


def tg_send_photo(path: str, caption: str) -> bool:
    if not telegram_ready() or not path or not os.path.exists(path): return False
    try:
        with open(path,"rb") as f:
            r=SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",data={"chat_id":TELEGRAM_CHAT_ID,"caption":caption[:1024]},files={"photo":f},timeout=REQUEST_TIMEOUT)
        try: payload=r.json()
        except Exception: payload={}
        ok=r.ok and payload.get("ok",True) is True
        if not ok: print(f"TELEGRAM_PHOTO_FAILED | status={r.status_code} | body={r.text[:300]}")
        return ok
    except Exception as exc:
        print(f"TELEGRAM_PHOTO_ERROR | {type(exc).__name__}: {exc}"); return False


def send_new_signal(hook: Dict[str,Any], rows: List[Dict[str,Any]], current: Optional[float], reason: str="NEW SIGNAL - ACCEPTED") -> bool:
    entry,tp=hook["final"]["price"],hook["tp"]; tp_pct=abs(entry-tp)/abs(entry)*100 if entry else 0
    text=(f"{'🟢 LONG' if hook['direction']=='LONG' else '🔴 SHORT'} NEW SIGNAL\nSymbol: {hook['symbol']}\nEntry: {fmt_price(entry)}\nTP 86.4%: {fmt_price(tp)} ({tp_pct:.2f}%)\n"
          f"{'Latest traded price' if current is not None else 'Latest traded price unavailable'}: {fmt_price(current)}\nRange: {hook['range_pct']:.2f}%\nHA Reversal: {utc_iso(hook['ha_reversal_time'])}\nSignal Confirmed: {utc_iso(hook['confirmed_time'])}\nPAPER ONLY")
    path=make_chart(hook["symbol"],rows,hook,reason,current=current)
    return tg_send_photo(path,text+f"\n\nReason: {reason}") if path else False


def send_open_update(live_prices: Dict[str,float]) -> bool:
    conn=db_connect()
    try: rows=conn.execute("SELECT symbol,direction,entry,tp,pnl_pct,last_price,opened_at,duration_seconds,last_checked_at FROM trades WHERE status='OPEN' ORDER BY opened_at").fetchall()
    finally: conn.close()
    if not rows: return False
    lines=["━━━ OPEN TRADES ━━━"]; now=utc_now()
    for r in rows:
        symbol=str(r["symbol"]).upper(); opened=parse_time(r["opened_at"]) or now; dur=int(max(0,(now-opened).total_seconds())//60)
        current=live_prices.get(symbol)
        if current is not None:
            pnl=trade_pnl_pct(r["direction"],float(r["entry"]),current); price_label="Current (Kraken last)"
        else:
            current=safe_float(r["last_price"]); pnl=float(r["pnl_pct"] or 0); checked=parse_time(r["last_checked_at"]); price_label=f"Last known @ {utc_iso(checked) if checked else 'unknown time'}"
        lines.append(f"{'🟢' if r['direction']=='LONG' else '🔴'} {symbol} | Entry {fmt_price(float(r['entry']))} | TP {fmt_price(float(r['tp']))} | {price_label} {fmt_price(current)} | PnL {pnl:+.2f}% | {dur}m")
    return tg_send_message("\n".join(lines))


def send_open_trade_charts(rows_by_symbol: Dict[str,List[Dict[str,Any]]], live_prices: Dict[str,float], stats: Dict[str,int]) -> None:
    """Send a freshly rendered chart for every trade still OPEN on each scan."""
    if not telegram_ready():
        print("OPEN_TRADE_CHART_SKIP | Telegram credentials unavailable")
        return
    conn=db_connect()
    try:
        trades=conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at").fetchall()
    finally:
        conn.close()
    for tr in trades:
        symbol=str(tr["symbol"]).upper()
        try:
            rows=rows_by_symbol.get(symbol)
            if not rows:
                rows=fetch_ohlc(symbol,M1_CANDLES)
                if rows: rows_by_symbol[symbol]=rows
            if not rows:
                stats["open_chart_errors"]+=1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | no fresh OHLC candles")
                continue
            hook=load_hook_for_trade(tr["hook_key"],rows)
            if hook is None:
                # Recover from the current scan if a legacy DB has a trade but
                # its hook row is missing or cannot be mapped to chart candles.
                recovered,_=detect_hooks(symbol,rows)
                hook=next((h for h in recovered if h["hook_key"]==tr["hook_key"]),None)
            if hook is None:
                stats["open_chart_errors"]+=1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | hook points unavailable | hook_key={tr['hook_key']}")
                continue
            current=live_prices.get(symbol)
            entry=float(tr["entry"]); tp=float(tr["tp"]); opened=parse_time(tr["opened_at"]) or utc_now()
            duration=int(max(0,(utc_now()-opened).total_seconds())//60)
            if current is None:
                current=safe_float(tr["last_price"])
                price_line="Last known price"
                pnl=float(tr["pnl_pct"] or 0)
            else:
                price_line="Current Kraken last"
                pnl=trade_pnl_pct(tr["direction"],entry,current)
            stamp=utc_now().strftime("%Y%m%dT%H%M%S")
            chart_path=str(Path(CHART_DIR)/f"OPEN_{symbol}_{tr['direction']}_{stamp}.png")
            reason=(f"OPEN TRADE UPDATE | Entry {fmt_price(entry)} | TP {fmt_price(tp)} | "
                    f"{price_line}: {fmt_price(current)} | PnL {pnl:+.2f}% | Duration {duration}m | Updated {utc_iso()}")
            path=make_chart(symbol,rows,hook,reason,path=chart_path,current=current,include_latest=True)
            if not path:
                stats["open_chart_errors"]+=1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | chart generation returned no path")
                continue
            caption=(f"📊 OPEN TRADE CHART UPDATE\n{'🟢 LONG' if tr['direction']=='LONG' else '🔴 SHORT'} | {symbol}\n"
                     f"Entry: {fmt_price(entry)}\nTP 86.4%: {fmt_price(tp)}\n"
                     f"{price_line}: {fmt_price(current)}\nPnL: {pnl:+.2f}%\nDuration: {duration} min\n"
                     f"Chart updated: {utc_iso()}\nPAPER ONLY")
            if tg_send_photo(path,caption):
                stats["open_charts_sent"]+=1
                print(f"OPEN_TRADE_CHART_SENT | {symbol} | {path}")
            else:
                stats["open_chart_errors"]+=1
                print(f"OPEN_TRADE_CHART_ERROR | {symbol} | Telegram sendPhoto failed")
        except Exception as exc:
            stats["open_chart_errors"]+=1
            print(f"OPEN_TRADE_CHART_ERROR | {symbol} | {type(exc).__name__}: {exc}")


def send_pending_update(live_prices: Dict[str,float]) -> bool:
    conn=db_connect()
    try: rows=conn.execute("SELECT symbol,direction,entry,tp,pnl_pct,last_price,last_checked_at FROM pending_trades WHERE status='PENDING' ORDER BY confirmed_at").fetchall()
    finally: conn.close()
    if not rows: return False
    lines=["━━━ PENDING TRADES ━━━"]
    for r in rows:
        symbol=str(r["symbol"]).upper(); current=live_prices.get(symbol)
        if current is not None:
            pnl=trade_pnl_pct(r["direction"],float(r["entry"]),current); label="Current (Kraken last)"
        else:
            current=safe_float(r["last_price"]); pnl=float(r["pnl_pct"] or 0); checked=parse_time(r["last_checked_at"]); label=f"Last known @ {utc_iso(checked) if checked else 'unknown time'}"
        lines.append(f"{'🟢' if r['direction']=='LONG' else '🔴'} {symbol} | Entry {fmt_price(float(r['entry']))} | TP {fmt_price(float(r['tp']))} | {label} {fmt_price(current)} | PnL {pnl:+.2f}%")
    return tg_send_message("\n".join(lines))


def main() -> None:
    if REAL_TRADING or not PAPER_ONLY:
        raise RuntimeError("SAFETY STOP: paper-only mode is mandatory.")
    init_db(); Path(CHART_DIR).mkdir(parents=True,exist_ok=True)
    before=trade_db_snapshot(); print(f"DB_BEFORE_SCAN | total={before['total']} | open={before['open']} | tp={before['tp']} | statuses={before['statuses']} | bytes={before['db_bytes']} | path={before['db_path']}")
    stats={"hooks":0,"new_hooks":0,"candidates":0,"signals":0,"pending":0,"pending_checked":0,"pending_expired":0,"tp_closed":0,"hook_chart_errors":0,"hook_charts_sent":0,"open_charts_sent":0,"open_chart_errors":0}
    symbols=get_target_assets(TARGET_ASSETS)
    if not symbols: raise RuntimeError("No PF_ Kraken Futures instruments were returned; stopping rather than reporting stale prices as current.")
    live=get_live_prices(symbols); refresh_live_trade_prices(live)
    rows_by_symbol={}; monitor_open_trades(rows_by_symbol,stats,live); update_pending_trades(rows_by_symbol,stats,live)
    hook_reports=[]; candidates=[]
    for symbol in symbols:
        rows=fetch_ohlc(symbol,M1_CANDLES)
        if not rows: continue
        rows_by_symbol[symbol]=rows; hooks,_=detect_hooks(symbol,rows); stats["hooks"]+=len(hooks)
        for hook in hooks:
            is_new=save_hook(hook)
            if is_new: stats["new_hooks"]+=1
            report={"hook":hook,"rows":rows,"new":is_new,"reason":""}
            if age_seconds(hook["confirmed_time"]) > M1_MAX_HOOK_AGE_SECONDS:
                report["reason"]=f"REJECTED: Hook older than {M1_MAX_HOOK_AGE_SECONDS//3600}h"
            elif hook["range_pct"] < M1_MIN_HOOK_RANGE_PCT:
                report["reason"]=f"REJECTED: Range {hook['range_pct']:.2f}% < minimum {M1_MIN_HOOK_RANGE_PCT:.2f}%"
            else:
                ti=tp_touch_info(rows,hook)
                if ti["touched"]: report["reason"]=f"REJECTED: TP 86.4% already touched | TP {fmt_price(hook['tp'])} | {utc_iso(ti['time'])}"
                else:
                    report["reason"]="ACCEPTED: Candidate"; candidates.append((hook,rows,report))
            hook_reports.append(report)
        time.sleep(SCAN_SLEEP_SECONDS)
    live=get_live_prices(symbols); refresh_live_trade_prices(live)
    candidates.sort(key=lambda x:x[0]["confirmed_time"]); stats["candidates"]=len(candidates)
    for hook,rows,report in candidates:
        if trade_exists(hook["hook_key"]):
            report["reason"]="REJECTED: Trade already exists for this Hook"; continue
        if open_trade_count() >= MAX_OPEN_TRADES:
            p=live.get(hook["symbol"].upper())
            if p is None:
                report["reason"]="REJECTED: Max open trades reached; no live last-trade price for pending record"; continue
            save_pending_trade(hook,p); stats["pending"]+=1; report["reason"]="PENDING: MAX_OPEN_TRADES reached"; continue
        if save_trade(hook):
            stats["signals"]+=1; report["reason"]="ACCEPTED: NEW SIGNAL | DB VERIFIED"
            # Send a current chart for every newly accepted signal, even when
            # its hook row was already stored by a previous scan.
            try:
                if telegram_ready() and send_new_signal(hook,rows,live.get(hook["symbol"].upper()),report["reason"]):
                    stats["hook_charts_sent"]+=1
            except Exception as exc:
                stats["hook_chart_errors"]+=1; print(f"SIGNAL_CHART_ERROR | {hook['symbol']} | {type(exc).__name__}: {exc}")
        else:
            report["reason"]="REJECTED: DB insert ignored or hook_key already exists"
    # Keep the original behavior of notifying Telegram about newly detected hooks.
    if telegram_ready():
        for report in hook_reports:
            if not report["new"]: continue
            hook,rows=report["hook"],report["rows"]
            try:
                path=make_chart(hook["symbol"],rows,hook,report["reason"],current=live.get(hook["symbol"].upper()))
                if path:
                    caption=(f"NDS M1 HOOK | {hook['symbol']} | {hook['direction']}\nEntry: {fmt_price(hook['final']['price'])}\nTP 86.4%: {fmt_price(hook['tp'])}\nRange: {hook['range_pct']:.2f}%\nHA Reversal: {utc_iso(hook['ha_reversal_time'])}\nSignal Confirmed: {utc_iso(hook['confirmed_time'])}\nReason: {report['reason']}\nPAPER ONLY")
                    if tg_send_photo(path,caption): stats["hook_charts_sent"]+=1
            except Exception as exc:
                stats["hook_chart_errors"]+=1; print(f"HOOK_CHART_ERROR | {hook['symbol']} | {type(exc).__name__}: {exc}")
    # Final market snapshot and monitoring immediately before reporting.
    live_final=get_live_prices(symbols)
    monitor_open_trades(rows_by_symbol,stats,live_final); refresh_live_trade_prices(live_final); update_pending_trades(rows_by_symbol,stats,live_final)
    perf=performance_summary(); after=trade_db_snapshot()
    print(f"DB_AFTER_SCAN | total={after['total']} | open={after['open']} | tp={after['tp']} | statuses={after['statuses']} | bytes={after['db_bytes']} | path={after['db_path']}")
    # Send current charts for all trades that remain open after TP monitoring.
    send_open_trade_charts(rows_by_symbol,live_final,stats)
    diagnostic=(f"🔎 NDS M1 DIAGNOSTIC\nVersion: {VERSION}\nTime: {utc_iso()}\n\n━━━ ASSETS ━━━\nScanned: {len(symbols)}\n\n━━━ M1 ━━━\nMin node spacing: {MIN_NODE_CANDLES} candles\nFinal node gate: CLOSED Heikin-Ashi reversal strictly AFTER final node\nSHORT: GREEN->RED | LONG: RED->GREEN\nPrice source: Kraken Futures ticker last only\nHooks: {stats['hooks']}\nNew hooks: {stats['new_hooks']}\nHook charts/signals sent: {stats['hook_charts_sent']}\nOpen-trade charts sent: {stats['open_charts_sent']}\nOpen-trade chart errors: {stats['open_chart_errors']}\nChart errors: {stats['hook_chart_errors']}\nCandidates: {stats['candidates']}\nSignals: {stats['signals']}\nPending: {stats['pending']}\nPending checked: {stats['pending_checked']}\nPending expired: {stats['pending_expired']}\nTP closed this scan: {stats['tp_closed']}\n\n━━━ DATABASE ━━━\nTrades total: {after['total']}\nTrades OPEN: {after['open']} | TP: {after['tp']}\nDB bytes: {after['db_bytes']}\nDB path: {after['db_path']}\n\n━━━ PERFORMANCE ━━━\nClosed TP: {perf['tp']}\nOpen: {perf['open']}\nWin rate: {perf['win_rate']:.2f}%\nTotal PnL: {perf['pnl']:+.2f}%\nGross profit: {perf['gross_profit']:+.2f}%\nGross loss: {perf['gross_loss']:+.2f}%\n\nTP ONLY | 86.4% | NO SL | PAPER ONLY")
    print(diagnostic)
    if telegram_ready():
        tg_send_message(diagnostic); send_open_update(live_final); send_pending_update(live_final)


if __name__ == "__main__":
    main()
