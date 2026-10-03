# ============================================================
# NDS M15 LIVE SCANNER
# VERSION 6.1.0
# PAPER TRADING ONLY - NO REAL ORDERS
#
# IMPORTANT:
# - Strategy timeframe: M15 ONLY
# - H4 is completely removed
# - Hook nodes are built from HEIKIN ASHI candles
# - A node is valid only when HA direction changes cleanly around it
# - Each valid node must also be a strict HA local high/low
# - Positive Hook / SHORT: START(L) -> H1 -> L1 -> H2 -> L2 -> H3
# - Negative Hook / LONG : START(H) -> L1 -> H1 -> L2 -> H2 -> L3
# - Entry = confirmed H3/L3
# - TP = 86.4% retracement from START to H3/L3
# - SL = nearest confirmed valid opposite M15 node at entry
# - No age cutoff; old setups are still rejected by TP/DB/geometry rules
# - NEAR excluded, XAUT explicitly included
#
# MAJOR NODE FIXES IN 6.1.0:
# 1) No more "net delta" direction test.
# 2) All HA closes on each side of a node must move monotonically.
# 3) The node must also be a strict HA high/low across the confirmation window.
# 4) Same-direction duplicate candidates are collapsed only when they are
#    actually overlapping candidates from the same turn.
# 5) Hook charts explicitly show START/H1/L1/H2/L2/H3 or START/L1/H1/L2/H2/L3.
# 6) A fresh DB is used so old incorrectly detected hooks cannot suppress the
#    corrected scanner through duplicate checks.
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

VERSION = "6.1.0"
REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

TARGET_ASSETS = 100
STRATEGY_INTERVAL = "15m"
STRATEGY_INTERVAL_MINUTES = 15
STRATEGY_CANDLES = 1800

# A node needs this many strictly directional HA close steps before AND after.
NODE_DIRECTION_BARS = 2

# Kept as explicit pivot parameters for the strict local-extreme test.
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

NDS_RETRACE = 0.864
MIN_HOOK_RANGE_PCT = 0.20
SL_BUFFER_PCT = 0.15
MAX_HOOK_AGE_SECONDS = None
MAX_OPEN_TRADES = 3
REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CANDLES = 240

# Fresh DB for the corrected node engine.
DB_FILE = "nds_m15_v600.db"
CHART_DIR = "nds_m15_charts"

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
    "candidate_turns": 0,
    "valid_high_nodes": 0,
    "valid_low_nodes": 0,
    "rejected_non_monotonic": 0,
    "rejected_non_extreme": 0,
    "nodes_collapsed": 0,
    "m15_nodes": 0,
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


def strictly_monotonic(values, direction):
    if len(values) < 2:
        return False
    vals = [float(v) for v in values]
    if direction == "UP":
        return all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))
    return all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))


def direction_changed_around_node(df, index, node_type):
    """Strict HA turning-point validation.

    HIGH:
        close[index-n] < ... < close[index]
        close[index] > ... > close[index+n]

    LOW:
        close[index-n] > ... > close[index]
        close[index] < ... < close[index+n]

    This is intentionally stricter than the old net-delta test.
    """
    n = NODE_DIRECTION_BARS
    if index - n < 0 or index + n >= len(df):
        return False

    closes = df["close"].astype(float).tolist()
    before = closes[index - n : index + 1]
    after = closes[index : index + n + 1]

    if node_type == "H":
        return strictly_monotonic(before, "UP") and strictly_monotonic(after, "DOWN")
    return strictly_monotonic(before, "DOWN") and strictly_monotonic(after, "UP")


def _is_strict_local_extreme(df, index, node_type):
    """Require the HA wick itself to be the local extreme around the turn."""
    left = PIVOT_LEFT
    right = PIVOT_RIGHT
    if index - left < 0 or index + right >= len(df):
        return False

    if node_type == "H":
        value = float(df.iloc[index].high)
        left_vals = [float(df.iloc[j].high) for j in range(index - left, index)]
        right_vals = [float(df.iloc[j].high) for j in range(index + 1, index + right + 1)]
        return all(value > x for x in left_vals) and all(value > x for x in right_vals)

    value = float(df.iloc[index].low)
    left_vals = [float(df.iloc[j].low) for j in range(index - left, index)]
    right_vals = [float(df.iloc[j].low) for j in range(index + 1, index + right + 1)]
    return all(value < x for x in left_vals) and all(value < x for x in right_vals)


def _make_node(df, index, node_type):
    if node_type == "H":
        price = float(df.iloc[index].high)
        raw_key = "high"
    else:
        price = float(df.iloc[index].low)
        raw_key = "low"

    return {
        "type": node_type,
        "index": int(index),
        "time": float(df.iloc[index].time),
        "price": price,
        "raw_price": float(df.iloc[index][raw_key]),
        "ha_open": float(df.iloc[index].open),
        "ha_close": float(df.iloc[index].close),
        "ha_high": float(df.iloc[index].high),
        "ha_low": float(df.iloc[index].low),
    }


def _collapse_overlapping_same_direction_nodes(nodes):
    """Collapse only overlapping duplicate candidates from one actual turn."""
    if not nodes:
        return []

    nodes = sorted(nodes, key=lambda p: (p["time"], p["index"]))
    out = []
    max_gap = NODE_DIRECTION_BARS

    for node in nodes:
        if not out:
            out.append(node)
            continue

        prev = out[-1]
        if node["type"] != prev["type"] or node["index"] - prev["index"] > max_gap:
            out.append(node)
            continue

        DIAG["nodes_collapsed"] += 1
        if node["type"] == "H":
            if node["price"] > prev["price"]:
                out[-1] = node
        else:
            if node["price"] < prev["price"]:
                out[-1] = node

    return out


def find_pivots(df, count_diag=True):
    """Find nodes from strict HA direction changes plus strict HA extremes."""
    high_nodes, low_nodes = [], []
    if df is None or df.empty:
        return high_nodes, low_nodes

    n = NODE_DIRECTION_BARS
    start = max(PIVOT_LEFT, n)
    end = len(df) - max(PIVOT_RIGHT, n)
    if end <= start:
        return high_nodes, low_nodes

    for i in range(start, end):
        DIAG["candidate_turns"] += 2

        high_turn = direction_changed_around_node(df, i, "H")
        low_turn = direction_changed_around_node(df, i, "L")

        if high_turn:
            if _is_strict_local_extreme(df, i, "H"):
                high_nodes.append(_make_node(df, i, "H"))
            else:
                DIAG["rejected_non_extreme"] += 1
        elif low_turn:
            if _is_strict_local_extreme(df, i, "L"):
                low_nodes.append(_make_node(df, i, "L"))
            else:
                DIAG["rejected_non_extreme"] += 1
        else:
            # No directional turn around this candidate candle.
            pass

    high_nodes = _collapse_overlapping_same_direction_nodes(high_nodes)
    low_nodes = _collapse_overlapping_same_direction_nodes(low_nodes)

    if count_diag:
        DIAG["valid_high_nodes"] += len(high_nodes)
        DIAG["valid_low_nodes"] += len(low_nodes)

    return high_nodes, low_nodes


def build_ordered_pivots(highs, lows):
    """Return the complete chronological valid-node sequence.

    Since find_pivots already derives nodes from actual direction turns,
    consecutive same-type nodes should be rare. We do not replace a stronger
    node merely because its price is larger/smaller; only truly overlapping
    duplicates were collapsed above.
    """
    all_nodes = sorted(highs + lows, key=lambda p: (p["time"], p["index"]))
    result = []
    for node in all_nodes:
        if result and node["index"] == result[-1]["index"]:
            continue
        result.append(node)
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
        p = ordered[i : i + 6]
        if len(p) < 6:
            continue

        types = [x["type"] for x in p]

        # Positive Hook / SHORT:
        # START(L) -> H1 -> L1 -> H2 -> L2 -> H3
        if types == ["L", "H", "L", "H", "L", "H"]:
            start, h1, l1, h2, l2, h3 = p

            if not (h2["price"] > h1["price"]):
                continue
            if not (l2["price"] < l1["price"]):
                continue
            if not (h3["price"] > h2["price"]):
                continue

            # START must remain the lowest structural point of the Hook.
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

        # Negative Hook / LONG:
        # START(H) -> L1 -> H1 -> L2 -> H2 -> L3
        elif types == ["H", "L", "H", "L", "H", "L"]:
            start, l1, h1, l2, h2, l3 = p

            if not (l2["price"] < l1["price"]):
                continue
            if not (h2["price"] > h1["price"]):
                continue
            if not (l3["price"] < l2["price"]):
                continue

            # START must remain the highest structural point of the Hook.
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
    return float(hook["confirmation_time"]) <= float(now_ts)


def hook_is_recent(hook, now_ts=None):
    # Kept intentionally age-free per the current strategy definition.
    now_ts = utc_now_ts() if now_ts is None else now_ts
    return bool(hook and float(hook.get("confirmation_time", 0)) <= float(now_ts))


def pivot_confirmation_time(pivot):
    return float(pivot["time"]) + PIVOT_RIGHT * STRATEGY_INTERVAL_MINUTES * 60


def calculate_m15_sl(m15_df, hook):
    """Nearest confirmed opposite valid HA node at entry."""
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
        os.makedirs(CHART_DIR, exist_ok=True)
        if df is None or df.empty:
            return None

        node_keys = (
            ("start", "h1", "l1", "h2", "l2", "h3")
            if hook["direction"] == "SHORT"
            else ("start", "l1", "h1", "l2", "h2", "l3")
        )
        points = [hook[k] for k in node_keys]
        node_times = [float(p["time"]) for p in points]

        work_df = df.copy().reset_index(drop=True)
        times = work_df["time"].astype(float).tolist()
        if not times:
            return None

        first_node_idx = min(range(len(times)), key=lambda i: abs(times[i] - min(node_times)))
        last_node_idx = max(range(len(times)), key=lambda i: abs(times[i] - max(node_times)))

        pad_left = 30
        pad_right = 50
        start_idx = max(0, first_node_idx - pad_left)
        end_idx = min(len(work_df), last_node_idx + pad_right + 1)

        if end_idx - start_idx < 140:
            center = (first_node_idx + last_node_idx) // 2
            start_idx = max(0, center - 80)
            end_idx = min(len(work_df), start_idx + 180)
            if end_idx - start_idx < 180:
                start_idx = max(0, end_idx - 180)

        chart_df = work_df.iloc[start_idx:end_idx].copy().reset_index(drop=True)
        if chart_df.empty:
            return None

        ha = calculate_heikin_ashi(chart_df)
        fig, (ax_price, ax_ha) = plt.subplots(2, 1, figsize=(16, 12), sharex=True)
        x = mdates.date2num(pd.to_datetime(chart_df.time, unit="s", utc=True).dt.to_pydatetime())
        width = max((STRATEGY_INTERVAL_MINUTES / 1440.0) * 0.72, 0.0015)

        # Real-price candles.
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

        # Heikin Ashi candles.
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

        labels = [
            "START", "H1", "L1", "H2", "L2", "H3"
        ] if hook["direction"] == "SHORT" else [
            "START", "L1", "H1", "L2", "H2", "L3"
        ]

        px = [datetime.fromtimestamp(p["time"], tz=timezone.utc) for p in points]
        py = [float(p["price"]) for p in points]

        # Hook path. It is based on HA node prices.
        for ax in (ax_price, ax_ha):
            ax.plot(
                px, py,
                marker="o",
                linewidth=2.4,
                color="royalblue",
                label="VALID M15 HOOK",
                zorder=7,
            )

        if hook["direction"] == "SHORT":
            offsets = [(0, -34), (0, 28), (0, -34), (0, 28), (0, -34), (0, 30)]
        else:
            offsets = [(0, 30), (0, -34), (0, 28), (0, -34), (0, 28), (0, -38)]

        for p, label, off in zip(points, labels, offsets):
            dt = datetime.fromtimestamp(p["time"], tz=timezone.utc)
            is_final = label in ("H3", "L3")
            is_start = label == "START"
            ha_price = float(p["price"])

            for ax in (ax_price, ax_ha):
                ax.axvline(
                    dt,
                    linestyle="-." if is_final else "--",
                    linewidth=1.2 if is_final else 0.85,
                    color="purple" if is_final else "gray",
                    alpha=0.60,
                    zorder=1,
                )
                ax.scatter(
                    [dt], [ha_price],
                    s=125 if is_final else (95 if is_start else 78),
                    marker="o",
                    facecolors="white",
                    edgecolors="purple" if is_final else "royalblue",
                    linewidths=2.0 if is_final else 1.5,
                    zorder=10,
                )
                ax.annotate(
                    f"{label}\nHA {fmt_price(ha_price)}",
                    (dt, ha_price),
                    xytext=off,
                    textcoords="offset points",
                    ha="center",
                    va="center",
                    fontsize=10 if is_final else 9,
                    fontweight="bold",
                    bbox=dict(
                        boxstyle="round,pad=.30",
                        fc="#fffdf2" if not is_final else "#f3e8ff",
                        ec="purple" if is_final else "royalblue",
                        linewidth=1.4,
                        alpha=0.97,
                    ),
                    arrowprops=dict(arrowstyle="-", color="gray", linewidth=0.8),
                    zorder=12,
                )

        entry = float(hook["entry"])
        tp = float(hook["tp"])
        confirm_dt = datetime.fromtimestamp(hook["confirmation_time"], tz=timezone.utc)
        left_dt = pd.to_datetime(chart_df.time.iloc[0], unit="s", utc=True).to_pydatetime()
        right_dt = pd.to_datetime(chart_df.time.iloc[-1], unit="s", utc=True).to_pydatetime()
        right_dt = max(right_dt, confirm_dt)

        for ax in (ax_price, ax_ha):
            ax.hlines(entry, left_dt, right_dt, linestyles="--", linewidth=1.4,
                      label=f"ENTRY {fmt_price(entry)}", color="darkorange")
            ax.hlines(tp, left_dt, right_dt, linestyles="--", linewidth=1.6,
                      label=f"TP 86.4% {fmt_price(tp)}", color="seagreen")
            if sl is not None:
                ax.hlines(float(sl), left_dt, right_dt, linestyles="--", linewidth=1.6,
                          label=f"SL M15 {fmt_price(sl)}", color="crimson")
            ax.axvline(confirm_dt, linestyle=":", linewidth=1.1, color="purple", label="M15 CONFIRMED")
            ax.grid(alpha=0.22)
            ax.legend(loc="best", fontsize=8)
            ax.set_xlim(left_dt, right_dt)

        direction_text = (
            "SHORT: START→H1→L1→H2→L2→H3"
            if hook["direction"] == "SHORT"
            else "LONG: START→L1→H1→L2→H2→L3"
        )
        ax_price.set_title(
            f"NDS M15 | {symbol} | {hook['direction']}\n"
            f"{direction_text} | REAL PRICE CANDLES | NODES FROM HA"
        )
        ax_price.set_ylabel("Price")
        ax_ha.set_title("HEIKIN ASHI | STRICT DIRECTION-CHANGE NODES")
        ax_ha.set_ylabel("HA Price")
        ax_ha.set_xlabel("Time UTC")

        fig.autofmt_xdate()
        path = os.path.join(
            CHART_DIR,
            f"{path_prefix}_{symbol.replace('/', '_').replace(':', '_')}_{hook['direction']}_{int(hook['final']['time'])}.png",
        )
        plt.tight_layout()
        plt.savefig(path, dpi=150)
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
        f"Hook confirmed: {fmt_ts(hook['confirmation_time'])}\n"
        f"Hook range: {hook['range_pct']:.2f}%\n"
        f"Nodes = strict HA direction change + HA local extreme\n"
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
    DIAG["m15_nodes"] += len(highs) + len(lows)

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
                f"SL valid M15 node: {fmt_price(sl)}\n"
                f"NODES: HA STRICT TURN\n"
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


def monitor_open_trades():
    for trade in get_open_trades():
        try:
            symbol = trade["symbol"]
            direction = trade["direction"]
            entry = float(trade["entry"])
            sl = float(trade["sl"])
            tp = float(trade["tp"])
            opened_at = int(trade["opened_at"] or 0)

            df = get_candles(symbol, STRATEGY_INTERVAL, 500)
            reason = None
            price = None

            if df is not None and not df.empty:
                future = df[df.time >= opened_at]
                for _, candle in future.iterrows():
                    hi = float(candle.high)
                    lo = float(candle.low)
                    hit_sl = hi >= sl if direction == "SHORT" else lo <= sl
                    hit_tp = lo <= tp if direction == "SHORT" else hi >= tp
                    if hit_sl:
                        price, reason = sl, "SL"
                        break
                    if hit_tp:
                        price, reason = tp, "TP"
                        break

            if reason is None:
                current = get_current_price(symbol)
                if current is not None:
                    if direction == "SHORT":
                        if current >= sl:
                            price, reason = sl, "SL"
                        elif current <= tp:
                            price, reason = tp, "TP"
                    else:
                        if current <= sl:
                            price, reason = sl, "SL"
                        elif current >= tp:
                            price, reason = tp, "TP"

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
                f"PnL: <b>{pnl_pct:+.2f}%</b>\n"
                f"PAPER TRADING ONLY"
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
        "━━━ <b>M15 / HA NODE ENGINE</b> ━━━",
    ]

    for label, key in [
        ("Requests", "m15_requests"),
        ("Data OK", "m15_data_ok"),
        ("Data Error", "m15_data_error"),
        ("Empty", "m15_empty"),
        ("Short", "m15_short"),
        ("Candidate Turn Checks", "candidate_turns"),
        ("Valid High Nodes", "valid_high_nodes"),
        ("Valid Low Nodes", "valid_low_nodes"),
        ("Rejected: Non-Extreme", "rejected_non_extreme"),
        ("Same-Turn Nodes Collapsed", "nodes_collapsed"),
        ("Total Valid HA Nodes", "m15_nodes"),
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
        print("NODE = STRICT HA DIRECTION CHANGE + STRICT HA LOCAL EXTREME")
        print("SHORT: START-L -> H1 -> L1 -> H2 -> L2 -> H3")
        print("LONG : START-H -> L1 -> H1 -> L2 -> H2 -> L3")
        print("Entry H3/L3 | TP 86.4% M15 | SL nearest confirmed valid opposite M15 node")
        print("NO HOOK AGE CUTOFF | NEAR excluded | XAUT included")
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
