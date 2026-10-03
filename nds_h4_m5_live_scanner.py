# ============================================================
# NDS M15 LIVE SCANNER
# VERSION 6.0.1
# PAPER TRADING ONLY - NO REAL ORDERS
#
# IMPORTANT:
# - Strategy timeframe: M15 ONLY
# - H4 is completely removed from the strategy
# - Hook nodes are built from Heikin Ashi candles
# - A node is VALID only when price direction changes around it
# - Entry = confirmed M15 H3/L3
# - TP = 86.4% M15 retracement
# - SL = nearest confirmed VALID opposite M15 node available at entry
# - No 6h hook-age cutoff; stale setups are rejected by TP-touched/DB/geometry rules
# - NEAR is excluded and XAUT is explicitly included
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

VERSION = "6.0.1"
REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

TARGET_ASSETS = 100
STRATEGY_INTERVAL = "15m"
STRATEGY_INTERVAL_MINUTES = 15
STRATEGY_CANDLES = 1800

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
NODE_DIRECTION_BARS = 2
NDS_RETRACE = 0.864
MIN_HOOK_RANGE_PCT = 0.20
SL_BUFFER_PCT = 0.15
MAX_HOOK_AGE_SECONDS = None  # No age cutoff; TP/SL/DB checks control stale setups
MAX_OPEN_TRADES = 3
REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CANDLES = 240

DB_FILE = "nds_m15_v600.db"
CHART_DIR = "nds_m15_charts"

# The Kraken UI can expose XAUT/USD and XAUT/USDT views while the
# derivatives instrument list can identify the tradable contract differently.
# We therefore resolve XAUT dynamically from the instrument list.
XAUT_PREFERRED_SYMBOLS = ("PF_XAUTUSDT", "PF_XAUTUSD")
EXCLUDED_BASES = {"NEAR"}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT") or ""

DIAG = {
    "assets_scanned": 0,
    "instrument_requests": 0,
    "instrument_errors": 0,
    "xaut_found": 0,
    "near_excluded": 0,
    "m15_requests": 0,
    "m15_data_ok": 0,
    "m15_data_error": 0,
    "m15_empty": 0,
    "m15_short": 0,
    "m15_pivots": 0,
    "valid_high_nodes": 0,
    "valid_low_nodes": 0,
    "invalid_direction_nodes": 0,
    "hooks": 0,
    "confirmed_hooks": 0,
    "recent_hooks": 0,
    "eligible_confirmed_hooks": 0,
    "range_valid_hooks": 0,
    "tp_touched": 0,
    "sl_found": 0,
    "sl_missing": 0,
    "geometry_valid": 0,
    "duplicates": 0,
    "max_open": 0,
    "signal_ready": 0,
    "signals": 0,
    "chart_sent": 0,
    "chart_errors": 0,
    "api_errors": [],
}

START_TIME = time.time()


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_ts():
    return int(utc_now().timestamp())


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "-"


def fmt_price(value):
    try:
        value = float(value)
        if value >= 1000:
            return f"{value:.2f}"
        if value >= 1:
            return f"{value:.5f}"
        if value >= 0.01:
            return f"{value:.7f}"
        return f"{value:.10f}"
    except Exception:
        return "-"


def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send(text):
    if not telegram_enabled():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            DIAG["api_errors"].append(f"Telegram text HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram text: {str(e)[:160]}")
        return False


def telegram_send_photo(photo_path, caption):
    if not telegram_enabled() or not photo_path or not os.path.exists(photo_path):
        return False
    try:
        with open(photo_path, "rb") as photo:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                files={"photo": photo},
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                timeout=REQUEST_TIMEOUT,
            )
        if not r.ok:
            DIAG["api_errors"].append(f"Telegram photo HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram photo: {str(e)[:160]}")
        return False


def _instrument_base(symbol):
    """Extract a base symbol from a Kraken futures symbol."""
    s = str(symbol or "").upper()
    if s.startswith("PF_"):
        s = s[3:]
    if s.startswith("PI_"):
        s = s[3:]
    for quote in ("USDT", "USD"):
        if s.endswith(quote):
            return s[:-len(quote)]
    return s


def _is_tradeable_pf(symbol):
    return str(symbol or "").upper().startswith("PF_")


def get_futures_instruments():
    DIAG["instrument_requests"] += 1
    try:
        r = requests.get(f"{KRAKEN_FUTURES_URL}/instruments", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        raw = r.json().get("instruments", [])
        all_pf = []
        xaut = None

        for item in raw:
            symbol = str(item.get("symbol") or item.get("instrument") or "")
            if not _is_tradeable_pf(symbol):
                continue
            if item.get("tradeable", True) is False:
                continue

            base = _instrument_base(symbol)
            if base == "XAUT":
                # Prefer an explicit USDT symbol if Kraken ever exposes one,
                # otherwise use the official XAUT/USD futures contract.
                upper = symbol.upper()
                if xaut is None or upper in XAUT_PREFERRED_SYMBOLS:
                    xaut = symbol
                continue

            if base in EXCLUDED_BASES:
                DIAG["near_excluded"] += 1
                continue

            if "USD" not in symbol.upper() and "USDT" not in symbol.upper():
                continue
            all_pf.append(symbol)

        all_pf = list(dict.fromkeys(all_pf))
        if not xaut:
            DIAG["api_errors"].append("XAUT futures instrument not found")
            return all_pf[:TARGET_ASSETS]

        DIAG["xaut_found"] = 1
        # Keep the total universe at TARGET_ASSETS while guaranteeing XAUT is present.
        selected = all_pf[: max(0, TARGET_ASSETS - 1)]
        if xaut not in selected:
            selected.append(xaut)
        return selected[:TARGET_ASSETS]

    except Exception as e:
        DIAG["instrument_errors"] += 1
        DIAG["api_errors"].append(f"Instruments: {str(e)[:180]}")
        return []


def get_candles(symbol, interval=STRATEGY_INTERVAL, count=STRATEGY_CANDLES):
    try:
        r = requests.get(
            f"{KRAKEN_CHART_URL}/trade/{symbol}/{interval}",
            params={"count": int(count)},
            timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            DIAG["api_errors"].append(f"{symbol} {interval} HTTP {r.status_code}")
            return None

        rows = []
        for c in r.json().get("candles", []):
            if isinstance(c, dict):
                vals = [
                    c.get("time", c.get("timestamp", c.get("t"))),
                    c.get("open", c.get("o")),
                    c.get("high", c.get("h")),
                    c.get("low", c.get("l")),
                    c.get("close", c.get("c")),
                    c.get("volume", c.get("v", 0)),
                ]
            elif isinstance(c, (list, tuple)) and len(c) >= 5:
                vals = list(c[:5]) + [c[5] if len(c) > 5 else 0]
            else:
                continue
            rows.append(vals)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["time", "open", "high", "low", "close"])
        if df.empty:
            return df
        if df.time.max() > 10_000_000_000:
            df["time"] /= 1000.0
        df["time"] = df["time"].astype(float)
        return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    except Exception as e:
        DIAG["api_errors"].append(f"{symbol} {interval}: {str(e)[:180]}")
        return None


def calculate_heikin_ashi(df):
    ha = df[["time", "open", "high", "low", "close"]].copy().reset_index(drop=True)
    ha["ha_close"] = (ha.open + ha.high + ha.low + ha.close) / 4.0

    opens = []
    for i in range(len(ha)):
        if i == 0:
            opens.append((float(ha.iloc[i].open) + float(ha.iloc[i].close)) / 2.0)
        else:
            opens.append((opens[i - 1] + float(ha.iloc[i - 1].ha_close)) / 2.0)

    ha["ha_open"] = opens
    ha["ha_high"] = ha[["high", "ha_open", "ha_close"]].max(axis=1)
    ha["ha_low"] = ha[["low", "ha_open", "ha_close"]].min(axis=1)
    return ha


def heikin_ashi_ohlc(df):
    ha = calculate_heikin_ashi(df)
    out = df.copy().reset_index(drop=True)
    out["open"] = ha.ha_open
    out["high"] = ha.ha_high
    out["low"] = ha.ha_low
    out["close"] = ha.ha_close
    return out


def direction_changed_around_node(df, index, node_type):
    """
    A valid node must show a real directional turn in HA close:

      HIGH: price was moving UP before the node and DOWN after it.
      LOW : price was moving DOWN before the node and UP after it.

    Net direction is measured across NODE_DIRECTION_BARS candles on each side.
    This intentionally rejects a local wick that does not actually reverse the
    short-term direction. Human beings love calling every wiggle a 'node'; the
    scanner will not indulge them.
    """
    n = NODE_DIRECTION_BARS
    if index - n < 0 or index + n >= len(df):
        return False

    closes = df["close"].astype(float).tolist()
    before_delta = closes[index] - closes[index - n]
    after_delta = closes[index + n] - closes[index]

    if node_type == "H":
        return before_delta > 0 and after_delta < 0
    return before_delta < 0 and after_delta > 0


def find_pivots(df, count_diag=True):
    highs, lows = [], []
    if df is None or df.empty or len(df) < PIVOT_LEFT + PIVOT_RIGHT + 1:
        return highs, lows

    for i in range(PIVOT_LEFT, len(df) - PIVOT_RIGHT):
        hi = float(df.iloc[i].high)
        lo = float(df.iloc[i].low)

        left_highs = [float(df.iloc[j].high) for j in range(i - PIVOT_LEFT, i)]
        right_highs = [float(df.iloc[j].high) for j in range(i + 1, i + PIVOT_RIGHT + 1)]
        left_lows = [float(df.iloc[j].low) for j in range(i - PIVOT_LEFT, i)]
        right_lows = [float(df.iloc[j].low) for j in range(i + 1, i + PIVOT_RIGHT + 1)]

        is_pivot_high = all(hi > x for x in left_highs) and all(hi >= x for x in right_highs)
        is_pivot_low = all(lo < x for x in left_lows) and all(lo <= x for x in right_lows)

        if is_pivot_high:
            if direction_changed_around_node(df, i, "H"):
                highs.append({"type": "H", "index": i, "time": float(df.iloc[i].time), "price": hi})
            else:
                DIAG["invalid_direction_nodes"] += 1

        if is_pivot_low:
            if direction_changed_around_node(df, i, "L"):
                lows.append({"type": "L", "index": i, "time": float(df.iloc[i].time), "price": lo})
            else:
                DIAG["invalid_direction_nodes"] += 1

    if count_diag:
        DIAG["valid_high_nodes"] += len(highs)
        DIAG["valid_low_nodes"] += len(lows)
    return highs, lows


def build_ordered_pivots(highs, lows):
    result = []
    for p in sorted(highs + lows, key=lambda x: (x["time"], x["index"])):
        if not result or p["type"] != result[-1]["type"]:
            result.append(p)
        elif p["type"] == "H" and p["price"] >= result[-1]["price"]:
            result[-1] = p
        elif p["type"] == "L" and p["price"] <= result[-1]["price"]:
            result[-1] = p
    return result


def detect_hooks(df, interval_minutes=STRATEGY_INTERVAL_MINUTES, pivots=None):
    if df is None or df.empty:
        return []

    if pivots is None:
        highs, lows = find_pivots(df)
    else:
        highs, lows = pivots
    ordered = build_ordered_pivots(highs, lows)
    hooks = []
    confirm_secs = interval_minutes * 60 * PIVOT_RIGHT

    for i in range(max(0, len(ordered) - 5)):
        p = ordered[i:i + 6]
        if len(p) < 6:
            continue

        types = [x["type"] for x in p]

        # Positive hook / SHORT:
        # START(L) -> H1 -> L1 -> H2 -> L2 -> H3
        if types == ["L", "H", "L", "H", "L", "H"]:
            start, h1, l1, h2, l2, h3 = p
            if not (h2["price"] > h1["price"] and l2["price"] < l1["price"] and h3["price"] > h2["price"]):
                continue
            if not (start["price"] < l1["price"] and start["price"] < l2["price"]):
                continue

            rng = h3["price"] - start["price"]
            if rng <= 0 or start["price"] <= 0:
                continue

            hooks.append({
                "direction": "SHORT",
                "start": start,
                "h1": h1,
                "l1": l1,
                "h2": h2,
                "l2": l2,
                "h3": h3,
                "final": h3,
                "entry": h3["price"],
                "tp": h3["price"] - NDS_RETRACE * rng,
                "range_pct": rng / start["price"] * 100,
                "confirmation_time": h3["time"] + confirm_secs,
                "created_time": h3["time"],
            })

        # Negative hook / LONG:
        # START(H) -> L1 -> H1 -> L2 -> H2 -> L3
        elif types == ["H", "L", "H", "L", "H", "L"]:
            start, l1, h1, l2, h2, l3 = p
            if not (l2["price"] < l1["price"] and h2["price"] > h1["price"] and l3["price"] < l2["price"]):
                continue
            if not (start["price"] > h1["price"] and start["price"] > h2["price"]):
                continue

            rng = start["price"] - l3["price"]
            if rng <= 0 or start["price"] <= 0:
                continue

            hooks.append({
                "direction": "LONG",
                "start": start,
                "l1": l1,
                "h1": h1,
                "l2": l2,
                "h2": h2,
                "l3": l3,
                "final": l3,
                "entry": l3["price"],
                "tp": l3["price"] + NDS_RETRACE * rng,
                "range_pct": rng / start["price"] * 100,
                "confirmation_time": l3["time"] + confirm_secs,
                "created_time": l3["time"],
            })

    return hooks


def hook_id(symbol, hook):
    return f"{symbol}|{hook['direction']}|{int(hook['final']['time'])}|{hook['final']['price']:.12f}"


def hook_is_confirmed(hook, now_ts=None):
    now_ts = utc_now_ts() if now_ts is None else now_ts
    return hook["confirmation_time"] <= now_ts


def hook_is_recent(hook, now_ts=None):
    """Time eligibility is intentionally not age-limited.

    A confirmed M15 hook can remain eligible until another condition rejects it,
    chiefly because TP may already have been touched or the hook may already
    exist in the paper-trading database.
    """
    now_ts = utc_now_ts() if now_ts is None else now_ts
    return bool(hook and float(hook.get("confirmation_time", 0)) <= float(now_ts))


def pivot_confirmation_time(pivot):
    return float(pivot["time"]) + PIVOT_RIGHT * STRATEGY_INTERVAL_MINUTES * 60


def calculate_m15_sl(m15_df, hook):
    """
    SL is derived ONLY from valid M15 nodes already confirmed by the time
    the entry becomes valid. For SHORT choose the nearest valid M15 HIGH above
    entry. For LONG choose the nearest valid M15 LOW below entry.
    """
    if m15_df is None or m15_df.empty or hook is None:
        DIAG["sl_missing"] += 1
        return None

    try:
        ha = heikin_ashi_ohlc(m15_df)
        highs, lows = find_pivots(ha, count_diag=False)
        signal_time = float(hook["confirmation_time"])
        entry = float(hook["entry"])

        confirmed_highs = [
            p for p in highs
            if pivot_confirmation_time(p) <= signal_time and float(p["time"]) <= signal_time
        ]
        confirmed_lows = [
            p for p in lows
            if pivot_confirmation_time(p) <= signal_time and float(p["time"]) <= signal_time
        ]

        if hook["direction"] == "SHORT":
            candidates = [p for p in confirmed_highs if float(p["price"]) > entry]
            if not candidates:
                DIAG["sl_missing"] += 1
                return None
            pivot = min(candidates, key=lambda p: float(p["price"]) - entry)
            return float(pivot["price"]) * (1 + SL_BUFFER_PCT / 100.0)

        candidates = [p for p in confirmed_lows if float(p["price"]) < entry]
        if not candidates:
            DIAG["sl_missing"] += 1
            return None
        pivot = min(candidates, key=lambda p: entry - float(p["price"]))
        return float(pivot["price"]) * (1 - SL_BUFFER_PCT / 100.0)

    except Exception as e:
        DIAG["sl_missing"] += 1
        DIAG["api_errors"].append(f"M15 SL: {str(e)[:180]}")
        return None


def tp_already_touched(df, hook):
    future = df[df.time > hook["final"]["time"]]
    if future.empty:
        return False
    if hook["direction"] == "SHORT":
        return bool((future.low <= hook["tp"]).any())
    return bool((future.high >= hook["tp"]).any())


def create_hook_chart(symbol, df, hook, sl, path_prefix="signal_m15"):
    try:
        chart_df = df.tail(CHART_CANDLES).copy().reset_index(drop=True)
        if chart_df.empty:
            return None

        ha = calculate_heikin_ashi(chart_df)
        fig, (ax_price, ax_ha) = plt.subplots(2, 1, figsize=(15, 11), sharex=True)
        x = mdates.date2num(pd.to_datetime(chart_df.time, unit="s", utc=True).dt.to_pydatetime())
        width = max((STRATEGY_INTERVAL_MINUTES / 1440.0) * 0.72, 0.0015)

        # Real-price candlesticks
        for i, row in chart_df.iterrows():
            xo = x[i]
            o, c, hi, lo = map(float, [row.open, row.close, row.high, row.low])
            candle_color = "#26a69a" if c >= o else "#ef5350"
            ax_price.vlines(xo, lo, hi, color="black", linewidth=0.8, zorder=2)
            ax_price.add_patch(
                plt.Rectangle(
                    (xo - width / 2, min(o, c)),
                    width,
                    max(abs(c - o), max(abs(c), 1) * 1e-7),
                    facecolor=candle_color,
                    edgecolor="black",
                    linewidth=0.5,
                    zorder=3,
                )
            )

        # Heikin Ashi candles
        for i, row in ha.iterrows():
            xo = x[i]
            o, c, hi, lo = map(float, [row.ha_open, row.ha_close, row.ha_high, row.ha_low])
            candle_color = "#26a69a" if c >= o else "#ef5350"
            ax_ha.vlines(xo, lo, hi, color="black", linewidth=0.8, zorder=2)
            ax_ha.add_patch(
                plt.Rectangle(
                    (xo - width / 2, min(o, c)),
                    width,
                    max(abs(c - o), max(abs(c), 1) * 1e-7),
                    facecolor=candle_color,
                    edgecolor="black",
                    linewidth=0.5,
                    zorder=3,
                )
            )

        if hook["direction"] == "SHORT":
            points = [hook[k] for k in ("start", "h1", "l1", "h2", "l2", "h3")]
            labels = ["START", "H1", "L1", "H2", "L2", "H3"]
        else:
            points = [hook[k] for k in ("start", "l1", "h1", "l2", "h2", "l3")]
            labels = ["START", "L1", "H1", "L2", "H2", "L3"]

        px = [datetime.fromtimestamp(p["time"], tz=timezone.utc) for p in points]
        py = [p["price"] for p in points]
        ax_price.plot(px, py, marker="o", linewidth=2, color="royalblue", label="VALID M15 HOOK", zorder=5)
        ax_ha.plot(px, py, marker="o", linewidth=2, color="royalblue", label="VALID M15 HOOK", zorder=5)

        offsets = (
            [(0, -28), (0, 18), (0, -30), (0, 18), (0, -30), (0, 22)]
            if hook["direction"] == "SHORT"
            else [(0, 24), (0, -30), (0, 18), (0, -30), (0, 18), (0, -32)]
        )

        for p, label, off in zip(points, labels, offsets):
            dt = datetime.fromtimestamp(p["time"], tz=timezone.utc)
            emphasized = label in ("START", "H3", "L3")
            txt = f"{label}\n{fmt_price(p['price'])}"
            for ax in (ax_price, ax_ha):
                ax.annotate(
                    txt,
                    (dt, p["price"]),
                    xytext=off,
                    textcoords="offset points",
                    ha="center",
                    fontsize=9 if emphasized else 8,
                    fontweight="bold" if emphasized else "normal",
                    bbox=dict(
                        boxstyle="round,pad=.22",
                        fc="white",
                        ec="black" if emphasized else "gray",
                        alpha=0.9,
                    ),
                    arrowprops=dict(arrowstyle="-", color="gray", linewidth=0.7),
                    zorder=10,
                )

        entry = float(hook["entry"])
        tp = float(hook["tp"])
        confirm_dt = datetime.fromtimestamp(hook["confirmation_time"], tz=timezone.utc)
        left_dt = pd.to_datetime(chart_df.time.iloc[0], unit="s", utc=True).to_pydatetime()
        right_dt = max(pd.to_datetime(chart_df.time.iloc[-1], unit="s", utc=True).to_pydatetime(), confirm_dt)

        for ax in (ax_price, ax_ha):
            ax.hlines(entry, left_dt, right_dt, linestyles="--", linewidth=1.4, label=f"ENTRY {fmt_price(entry)}", color="darkorange")
            ax.hlines(tp, left_dt, right_dt, linestyles="--", linewidth=1.5, label=f"TP 86.4% {fmt_price(tp)}", color="seagreen")
            if sl is not None:
                ax.hlines(float(sl), left_dt, right_dt, linestyles="--", linewidth=1.5, label=f"SL M15 {fmt_price(sl)}", color="crimson")
            ax.axvline(confirm_dt, linestyle=":", color="purple", label="M15 CONFIRMED")
            ax.grid(alpha=0.22)
            ax.legend(loc="best", fontsize=8)
            ax.set_xlim(left_dt, right_dt)

        ax_price.set_title(f"NDS M15 | {symbol} | {hook['direction']} | REAL PRICE CANDLES")
        ax_price.set_ylabel("Price")
        ax_ha.set_title("HEIKIN ASHI | VALID DIRECTION-CHANGE NODES")
        ax_ha.set_ylabel("HA Price")
        ax_ha.set_xlabel("Time UTC")

        fig.autofmt_xdate()
        path = os.path.join(
            CHART_DIR,
            f"{path_prefix}_{symbol.replace('/', '_').replace(':', '_')}_{hook['direction']}_{int(hook['final']['time'])}.png",
        )
        plt.tight_layout()
        plt.savefig(path, dpi=140)
        plt.close(fig)
        return path

    except Exception as e:
        DIAG["chart_errors"] += 1
        DIAG["api_errors"].append(f"Chart {symbol}: {str(e)[:180]}")
        plt.close("all")
        return None


def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)
    conn = db_connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS hooks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_key TEXT UNIQUE,
            symbol TEXT,
            direction TEXT,
            start_price REAL,
            h1_price REAL,
            l1_price REAL,
            h2_price REAL,
            l2_price REAL,
            final_price REAL,
            entry REAL,
            tp REAL,
            sl REAL,
            range_pct REAL,
            confirmation_time INTEGER,
            created_time INTEGER,
            detected_time INTEGER,
            chart_path TEXT
        );

        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_key TEXT UNIQUE,
            symbol TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            opened_at INTEGER,
            closed_at INTEGER,
            close_price REAL,
            status TEXT,
            pnl_pct REAL,
            pnl_price REAL,
            chart_path TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def hook_exists(key):
    c = db_connect()
    r = c.execute("SELECT 1 FROM hooks WHERE hook_key=?", (key,)).fetchone()
    c.close()
    return r is not None


def trade_exists(key):
    c = db_connect()
    r = c.execute("SELECT 1 FROM trades WHERE hook_key=?", (key,)).fetchone()
    c.close()
    return r is not None


def open_trade_count():
    c = db_connect()
    r = c.execute("SELECT COUNT(*) c FROM trades WHERE status='OPEN'").fetchone()
    c.close()
    return int(r["c"])


def save_hook(symbol, hook, sl, chart_path=None):
    key = hook_id(symbol, hook)
    c = db_connect()
    c.execute(
        """
        INSERT OR IGNORE INTO hooks(
            hook_key,symbol,direction,start_price,h1_price,l1_price,h2_price,l2_price,
            final_price,entry,tp,sl,range_pct,confirmation_time,created_time,detected_time,chart_path
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            key,
            symbol,
            hook["direction"],
            hook["start"]["price"],
            hook.get("h1", {}).get("price"),
            hook.get("l1", {}).get("price"),
            hook.get("h2", {}).get("price"),
            hook.get("l2", {}).get("price"),
            hook["final"]["price"],
            hook["entry"],
            hook["tp"],
            sl,
            hook["range_pct"],
            int(hook["confirmation_time"]),
            int(hook["created_time"]),
            utc_now_ts(),
            chart_path,
        ),
    )
    c.commit()
    c.close()
    return key


def save_trade(key, symbol, hook, sl, chart_path=None):
    c = db_connect()
    c.execute(
        """
        INSERT OR IGNORE INTO trades(
            hook_key,symbol,direction,entry,sl,tp,opened_at,status,chart_path
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            key,
            symbol,
            hook["direction"],
            hook["entry"],
            sl,
            hook["tp"],
            utc_now_ts(),
            "OPEN",
            chart_path,
        ),
    )
    c.commit()
    c.close()


def build_signal_message(symbol, hook, sl):
    d = hook["direction"]
    emoji = "🔴" if d == "SHORT" else "🟢"
    entry, tp, sl = map(float, [hook["entry"], hook["tp"], sl])
    risk = (sl - entry) / entry * 100 if d == "SHORT" else (entry - sl) / entry * 100
    reward = (entry - tp) / entry * 100 if d == "SHORT" else (tp - entry) / entry * 100
    final_label = "H3" if d == "SHORT" else "L3"
    return (
        f"{emoji} <b>NDS {d} SIGNAL | M15</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"Entry M15 {final_label}: <b>{fmt_price(entry)}</b>\n"
        f"SL nearest valid M15 node: <b>{fmt_price(sl)}</b> ({risk:+.2f}%)\n"
        f"TP 86.4% M15: <b>{fmt_price(tp)}</b> ({reward:+.2f}%)\n\n"
        f"M15 hook confirmed: {fmt_ts(hook['confirmation_time'])}\n"
        f"Hook range: {hook['range_pct']:.2f}%\n"
        f"Nodes = HA + real direction change\n"
        f"<b>PAPER TRADING ONLY</b>"
    )


def process_symbol(symbol):
    DIAG["m15_requests"] += 1
    df = get_candles(symbol, STRATEGY_INTERVAL, STRATEGY_CANDLES)

    if df is None:
        DIAG["m15_data_error"] += 1
        return
    if df.empty:
        DIAG["m15_empty"] += 1
        return
    if len(df) < 100:
        DIAG["m15_short"] += 1
        return

    DIAG["m15_data_ok"] += 1
    ha = heikin_ashi_ohlc(df)
    highs, lows = find_pivots(ha)
    DIAG["m15_pivots"] += len(highs) + len(lows)

    hooks = detect_hooks(ha, STRATEGY_INTERVAL_MINUTES, pivots=(highs, lows))
    DIAG["hooks"] += len(hooks)
    now = utc_now_ts()

    for hook in hooks:
        if not hook_is_confirmed(hook, now):
            continue
        DIAG["confirmed_hooks"] += 1

        if not hook_is_recent(hook, now):
            continue
        DIAG["recent_hooks"] += 1
        DIAG["eligible_confirmed_hooks"] += 1

        if hook["range_pct"] < MIN_HOOK_RANGE_PCT:
            continue
        DIAG["range_valid_hooks"] += 1

        if tp_already_touched(df, hook):
            DIAG["tp_touched"] += 1
            continue

        sl = calculate_m15_sl(df, hook)
        if sl is None:
            continue
        DIAG["sl_found"] += 1

        entry = float(hook["entry"])
        tp = float(hook["tp"])
        sl = float(sl)
        geometry_ok = (sl > entry > tp) if hook["direction"] == "SHORT" else (sl < entry < tp)
        if not geometry_ok:
            continue
        DIAG["geometry_valid"] += 1

        key = hook_id(symbol, hook)
        if hook_exists(key) or trade_exists(key):
            DIAG["duplicates"] += 1
            continue

        if open_trade_count() >= MAX_OPEN_TRADES:
            DIAG["max_open"] += 1
            return

        DIAG["signal_ready"] += 1
        chart = create_hook_chart(symbol, df, hook, sl, "signal_m15")
        save_hook(symbol, hook, sl, chart)
        save_trade(key, symbol, hook, sl, chart)
        DIAG["signals"] += 1

        telegram_send(build_signal_message(symbol, hook, sl))
        if chart:
            sent = telegram_send_photo(
                chart,
                f"📍 <b>NDS {hook['direction']} SIGNAL | M15</b>\n"
                f"<b>{symbol}</b>\n"
                f"Entry: {fmt_price(entry)}\n"
                f"TP 86.4%: {fmt_price(tp)}\n"
                f"SL M15 node: {fmt_price(sl)}\n"
                f"PAPER TRADING ONLY",
            )
            if sent:
                DIAG["chart_sent"] += 1
        return


def get_open_trades():
    c = db_connect()
    rows = c.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at ASC").fetchall()
    c.close()
    return rows


def close_trade(trade, price, pnl_pct, pnl_price, reason):
    c = db_connect()
    c.execute(
        "UPDATE trades SET closed_at=?,close_price=?,status=?,pnl_pct=?,pnl_price=? WHERE id=?",
        (utc_now_ts(), price, reason, pnl_pct, pnl_price, trade["id"]),
    )
    c.commit()
    c.close()


def monitor_open_trades():
    for trade in get_open_trades():
        try:
            symbol = trade["symbol"]
            direction = trade["direction"]
            entry = float(trade["entry"])
            sl = float(trade["sl"])
            tp = float(trade["tp"])

            df = get_candles(symbol, STRATEGY_INTERVAL, 30)
            if df is None or df.empty:
                continue

            future = df[df.time >= trade["opened_at"]]
            if future.empty:
                continue

            reason = None
            price = None
            for _, candle in future.iterrows():
                hi = float(candle.high)
                lo = float(candle.low)
                hit_sl = hi >= sl if direction == "SHORT" else lo <= sl
                hit_tp = lo <= tp if direction == "SHORT" else hi >= tp

                # Conservative rule when the same candle touches both levels:
                # SL is treated as hit first.
                if hit_sl:
                    price, reason = sl, "SL"
                    break
                if hit_tp:
                    price, reason = tp, "TP"
                    break

            if reason is None:
                continue

            pnl_price = entry - price if direction == "SHORT" else price - entry
            pnl_pct = pnl_price / entry * 100 if entry else 0.0
            close_trade(trade, price, pnl_pct, pnl_price, reason)
            emoji = "✅" if pnl_pct >= 0 else "❌"
            telegram_send(
                f"{emoji} <b>NDS {direction} CLOSED | M15</b>\n"
                f"<b>{symbol}</b>\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Close: {fmt_price(price)}\n"
                f"Result: <b>{reason}</b>\n"
                f"PnL: <b>{pnl_pct:+.2f}%</b>"
            )
        except Exception as e:
            DIAG["api_errors"].append(f"Monitor {trade['symbol']}: {str(e)[:180]}")


def trade_metrics(entry, sl, tp, direction, current):
    if direction == "LONG":
        pnl = (current - entry) / entry * 100
        tp_pct = (tp - entry) / entry * 100
        sl_pct = (sl - entry) / entry * 100
    else:
        pnl = (entry - current) / entry * 100
        tp_pct = (entry - tp) / entry * 100
        sl_pct = (entry - sl) / entry * 100
    return pnl, tp_pct, sl_pct


def get_current_price(symbol):
    try:
        r = requests.get(f"{KRAKEN_FUTURES_URL}/tickers", timeout=REQUEST_TIMEOUT)
        if r.ok:
            for t in r.json().get("tickers", []):
                if not isinstance(t, dict):
                    continue
                ts = str(t.get("symbol") or t.get("pair") or t.get("instrument") or "")
                if ts != symbol:
                    continue
                for key in ("last", "lastPrice", "markPrice", "price"):
                    if t.get(key) is not None:
                        return float(t[key])
    except Exception as e:
        DIAG["api_errors"].append(f"Ticker {symbol}: {str(e)[:160]}")

    df = get_candles(symbol, STRATEGY_INTERVAL, 2)
    return None if df is None or df.empty else float(df.iloc[-1].close)


def open_trades_report_lines():
    rows = get_open_trades()
    lines = ["━━━ <b>OPEN TRADES</b> ━━━"]
    if not rows:
        return lines + ["None"]

    for t in rows:
        direction = t["direction"]
        symbol = t["symbol"]
        entry, sl, tp = map(float, [t["entry"], t["sl"], t["tp"]])
        current = get_current_price(symbol)
        icon = "🟢" if direction == "LONG" else "🔴"

        if current is None:
            lines.extend([
                f"{icon} <b>{symbol} {direction}</b>",
                f"Entry: {fmt_price(entry)}",
                "Current: -",
                f"TP: {fmt_price(tp)}",
                f"SL: {fmt_price(sl)}",
                "",
            ])
            continue

        pnl, tp_pct, sl_pct = trade_metrics(entry, sl, tp, direction, current)
        lines.extend([
            f"{icon} <b>{symbol} {direction}</b>",
            f"Entry: {fmt_price(entry)}",
            f"Current: <b>{fmt_price(current)} ({pnl:+.2f}%)</b>",
            f"TP: <b>{fmt_price(tp)} ({tp_pct:+.2f}%)</b>",
            f"SL: <b>{fmt_price(sl)} ({sl_pct:+.2f}%)</b>",
            "",
        ])

    if lines[-1] == "":
        lines.pop()
    return lines


def performance_summary():
    c = db_connect()
    total = c.execute("SELECT COUNT(*) c FROM trades WHERE status!='OPEN'").fetchone()["c"]
    wins = c.execute("SELECT COUNT(*) c FROM trades WHERE status='TP'").fetchone()["c"]
    losses = c.execute("SELECT COUNT(*) c FROM trades WHERE status='SL'").fetchone()["c"]
    pnl = c.execute("SELECT COALESCE(SUM(pnl_pct),0) p FROM trades WHERE status!='OPEN'").fetchone()["p"]
    c.close()
    return {
        "total": int(total or 0),
        "wins": int(wins or 0),
        "losses": int(losses or 0),
        "pnl": float(pnl or 0),
    }


def diagnostic_text():
    p = performance_summary()
    lines = [
        "🔎 <b>NDS M15 DIAGNOSTIC</b>",
        f"Version: <b>{VERSION}</b>",
        f"Time: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Runtime: <b>{time.time() - START_TIME:.1f}s</b>",
        "",
        "━━━ <b>ASSETS</b> ━━━",
        f"Scanned: <b>{DIAG['assets_scanned']}</b>",
        f"XAUT Included: <b>{DIAG['xaut_found']}</b>",
        f"NEAR Excluded: <b>{DIAG['near_excluded']}</b>",
        "",
        "━━━ <b>M15 ONLY</b> ━━━",
    ]

    for label, key in [
        ("Requests", "m15_requests"),
        ("Data OK", "m15_data_ok"),
        ("Data Error", "m15_data_error"),
        ("Empty", "m15_empty"),
        ("Short", "m15_short"),
        ("Pivot Points", "m15_pivots"),
        ("Valid High Nodes", "valid_high_nodes"),
        ("Valid Low Nodes", "valid_low_nodes"),
        ("Rejected: No Direction Change", "invalid_direction_nodes"),
        ("Hooks", "hooks"),
        ("Confirmed Hooks", "confirmed_hooks"),
        ("Confirmed Time-Eligible (no age cutoff)", "eligible_confirmed_hooks"),
        (f"Range Valid ≥{MIN_HOOK_RANGE_PCT:.2f}%", "range_valid_hooks"),
        ("TP Already Touched", "tp_touched"),
        ("SL Found", "sl_found"),
        ("SL Missing", "sl_missing"),
        ("Geometry Valid", "geometry_valid"),
        ("Duplicates", "duplicates"),
        ("Max Open", "max_open"),
        ("Signal Ready", "signal_ready"),
    ]:
        lines.append(f"{label}: {DIAG[key]}")

    lines += [
        "",
        "━━━ <b>SIGNALS</b> ━━━",
        f"Signals: {DIAG['signals']}",
        f"Charts Sent: {DIAG['chart_sent']}",
        f"Chart Errors: {DIAG['chart_errors']}",
        f"Open Trades: <b>{open_trade_count()}</b>",
        "",
        *open_trades_report_lines(),
        "",
        "━━━ <b>PAPER PERFORMANCE</b> ━━━",
        f"Closed Trades: {p['total']}",
        f"TP: {p['wins']}",
        f"SL: {p['losses']}",
        f"PnL: <b>{p['pnl']:+.2f}%</b>",
    ]

    if DIAG["api_errors"]:
        lines += ["", "━━━ <b>ERRORS</b> ━━━"] + ["• " + e for e in DIAG["api_errors"][-5:]]

    lines += ["", "<b>PAPER TRADING ONLY - NO REAL ORDERS</b>"]
    return "\n".join(lines)


def main():
    try:
        init_db()
        print(f"NDS M15 Scanner {VERSION}")
        print("M15 ONLY - H4 REMOVED")
        print("VALID NODE = REAL DIRECTION CHANGE ON HEIKIN ASHI")
        print("Entry M15 H3/L3 | TP 86.4% M15 | SL nearest confirmed valid M15 opposite node | NO HOOK AGE CUTOFF")
        print("NEAR excluded | XAUT included")
        print("PAPER TRADING ONLY - NO REAL ORDERS")

        symbols = get_futures_instruments()
        if not symbols:
            msg = "❌ <b>NDS M15 Scanner</b>\n\nNo futures instruments found."
            print(msg)
            telegram_send(msg)
            return

        DIAG["assets_scanned"] = len(symbols)
        monitor_open_trades()

        for symbol in symbols:
            try:
                process_symbol(symbol)
            except Exception as e:
                DIAG["api_errors"].append(f"Process {symbol}: {str(e)[:180]}")
                traceback.print_exc()
            time.sleep(SCAN_SLEEP_SECONDS)

        monitor_open_trades()
        report = diagnostic_text()
        print("\n" + report + "\n")
        telegram_send(report)

    except Exception as e:
        traceback.print_exc()
        telegram_send(f"❌ <b>NDS Scanner Error</b>\n\n{type(e).__name__}: {str(e)[:500]}")


if __name__ == "__main__":
    main()
