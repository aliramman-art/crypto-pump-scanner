# ============================================================
# KRAKEN PRICE ACTION BACKTEST
# ============================================================
# Strategy:
#   - Kraken Futures
#   - TOP 100 current high-volume perpetual USD markets
#   - 5m candles
#   - Tenkan 9
#   - Kijun 26
#   - Latest Tenkan/Kijun crossover
#   - Box = 26 candles BEFORE crossover
#   - Breakout after crossover
#   - Confirmed Swing = 2 left + 2 right
#   - SL = Swing +/- 0.15%
#   - TP = 50% of Box width
#   - Maximum 1 open trade
#
# NOTE:
# This script performs a historical simulation.
# It does NOT place real orders.
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

KRAKEN_BASE_URL = "https://futures.kraken.com"
KRAKEN_API_BASE = KRAKEN_BASE_URL + "/derivatives/api/v3"
KRAKEN_CHART_BASE = KRAKEN_BASE_URL + "/api/charts/v1"

TIMEFRAME = "5m"

TOP_SYMBOLS = 100

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
BOX_PERIOD = 26

SWING_LEFT = 2
SWING_RIGHT = 2

TP_BOX_PERCENT = 0.50
SL_BUFFER_PERCENT = 0.15

MAX_OPEN_TRADES = 1

MAX_BREAKOUT_AGE = 2

MAX_WORKERS = 8

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 4

# One year
START_DATE = "2025-09-07"
END_DATE = "2026-09-07"

START_TS = int(
    datetime.strptime(
        START_DATE,
        "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc).timestamp()
)

END_TS = int(
    datetime.strptime(
        END_DATE,
        "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc).timestamp()
)

DATA_DIR = "backtest_data"
RESULTS_DIR = "backtest_results"

TRADES_FILE = os.path.join(
    RESULTS_DIR,
    "backtest_trades.csv"
)

SUMMARY_FILE = os.path.join(
    RESULTS_DIR,
    "backtest_summary.json"
)

MONTHLY_FILE = os.path.join(
    RESULTS_DIR,
    "backtest_monthly.csv"
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Kraken-Price-Action-Backtester/1.0"
})


# ============================================================
# GENERAL HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.strip()

        return float(value)

    except Exception:
        return default


def utc_string(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        return str(ts)


def ensure_directories():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)


def print_header(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# KRAKEN REQUEST
# ============================================================

def kraken_get(url, params=None):

    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            return data

        except Exception as exc:

            last_error = exc

            print(
                f"[WARN] Request failed "
                f"{attempt}/{REQUEST_RETRIES}: {exc}"
            )

            time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"Kraken request failed: {last_error}"
    )


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    url = KRAKEN_API_BASE + "/instruments"

    data = kraken_get(url)

    instruments = data.get("instruments", [])

    if not instruments:
        raise RuntimeError(
            "No instruments returned from Kraken."
        )

    return instruments


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    url = KRAKEN_API_BASE + "/tickers"

    data = kraken_get(url)

    tickers = data.get("tickers", [])

    if not tickers:
        raise RuntimeError(
            "No tickers returned from Kraken."
        )

    return tickers


# ============================================================
# TOP 100 CURRENT MARKETS
# ============================================================

def get_top_symbols():

    print("[INFO] Loading Kraken instruments...")

    instruments = get_instruments()

    print("[INFO] Loading Kraken tickers...")

    tickers = get_tickers()

    instrument_map = {}

    for inst in instruments:

        symbol = str(
            inst.get("symbol", "")
        ).strip()

        if symbol:
            instrument_map[symbol] = inst

    candidates = []

    for ticker in tickers:

        symbol = str(
            ticker.get("symbol", "")
        ).strip()

        if not symbol:
            continue

        inst = instrument_map.get(symbol)

        if not inst:
            continue

        inst_text = json.dumps(
            inst,
            ensure_ascii=False
        ).lower()

        ticker_text = json.dumps(
            ticker,
            ensure_ascii=False
        ).lower()

        # Prefer perpetual USD futures.
        is_perpetual = (
            symbol.startswith("PF_")
            or "perpetual" in inst_text
            or "perpetual" in ticker_text
        )

        if not is_perpetual:
            continue

        # USD markets only.
        is_usd = (
            symbol.endswith("USD")
            or "usd" in symbol.lower()
        )

        if not is_usd:
            continue

        volume_quote = safe_float(
            ticker.get("volumeQuote"),
            0.0
        )

        if volume_quote <= 0:
            volume_quote = safe_float(
                ticker.get("vol24h"),
                0.0
            )

        if volume_quote <= 0:
            continue

        mark_price = safe_float(
            ticker.get("markPrice"),
            0.0
        )

        if mark_price <= 0:
            mark_price = safe_float(
                ticker.get("last"),
                0.0
            )

        candidates.append({
            "symbol": symbol,
            "volume_quote": volume_quote,
            "mark_price": mark_price
        })

    candidates.sort(
        key=lambda x: x["volume_quote"],
        reverse=True
    )

    selected = candidates[:TOP_SYMBOLS]

    print(
        f"[INFO] Selected TOP {len(selected)} markets."
    )

    for i, item in enumerate(selected, 1):

        print(
            f"{i:03d} "
            f"{item['symbol']:18s} "
            f"volume={item['volume_quote']:.2f}"
        )

    return selected


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def normalize_candle(candle):

    if isinstance(candle, dict):

        ts = (
            candle.get("time")
            or candle.get("timestamp")
            or candle.get("t")
        )

        o = (
            candle.get("open")
            or candle.get("o")
        )

        h = (
            candle.get("high")
            or candle.get("h")
        )

        l = (
            candle.get("low")
            or candle.get("l")
        )

        c = (
            candle.get("close")
            or candle.get("c")
        )

        v = (
            candle.get("volume")
            or candle.get("v")
        )

    else:

        if len(candle) < 6:
            return None

        ts, o, h, l, c, v = candle[:6]

    ts = safe_float(ts)

    if ts > 10_000_000_000:
        ts = ts / 1000.0

    o = safe_float(o)
    h = safe_float(h)
    l = safe_float(l)
    c = safe_float(c)
    v = safe_float(v)

    if ts <= 0:
        return None

    if min(o, h, l, c) <= 0:
        return None

    return {
        "time": int(ts),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v
    }


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(symbol):

    url = (
        f"{KRAKEN_CHART_BASE}"
        f"/trade/{symbol}/{TIMEFRAME}"
    )

    params = {
        "count": 1000
    }

    try:

        data = kraken_get(
            url,
            params=params
        )

        raw = data.get("candles", [])

        if isinstance(raw, dict):
            raw = raw.get(
                "data",
                raw.get("candles", [])
            )

        candles = []

        for item in raw:

            candle = normalize_candle(item)

            if candle:
                candles.append(candle)

        candles.sort(
            key=lambda x: x["time"]
        )

        return candles

    except Exception as exc:

        print(
            f"[ERROR] {symbol}: {exc}"
        )

        return []


# ============================================================
# FETCH ALL TOP 100
# ============================================================

def fetch_all_candles(symbols):

    results = {}

    print_header(
        f"DOWNLOADING {len(symbols)} MARKETS"
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_candles,
                item["symbol"]
            ): item["symbol"]

            for item in symbols
        }

        for future in as_completed(futures):

            symbol = futures[future]

            try:

                candles = future.result()

                if candles:

                    results[symbol] = candles

                    print(
                        f"[OK] "
                        f"{symbol:18s} "
                        f"{len(candles)} candles"
                    )

                else:

                    print(
                        f"[EMPTY] {symbol}"
                    )

            except Exception as exc:

                print(
                    f"[ERROR] "
                    f"{symbol}: {exc}"
                )

    return results


# ============================================================
# FILTER DATE RANGE
# ============================================================

def filter_period(candles):

    return [
        c for c in candles
        if START_TS <= c["time"] <= END_TS
    ]


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(candles, period=TENKAN_PERIOD):

    if len(candles) < period:
        return None

    recent = candles[-period:]

    highest = max(
        c["high"]
        for c in recent
    )

    lowest = min(
        c["low"]
        for c in recent
    )

    return (
        highest + lowest
    ) / 2.0


# ============================================================
# KIJUN
# ============================================================

def calculate_kijun(candles, period=KIJUN_PERIOD):

    if len(candles) < period:
        return None

    recent = candles[-period:]

    highest = max(
        c["high"]
        for c in recent
    )

    lowest = min(
        c["low"]
        for c in recent
    )

    return (
        highest + lowest
    ) / 2.0


# ============================================================
# SERIES INDICATORS
# ============================================================

def add_indicators(candles):

    rows = []

    for i in range(len(candles)):

        if i + 1 < TENKAN_PERIOD:
            tenkan = np.nan
        else:

            section = candles[
                i + 1 - TENKAN_PERIOD:
                i + 1
            ]

            high = max(
                x["high"]
                for x in section
            )

            low = min(
                x["low"]
                for x in section
            )

            tenkan = (
                high + low
            ) / 2.0

        if i + 1 < KIJUN_PERIOD:
            kijun = np.nan
        else:

            section = candles[
                i + 1 - KIJUN_PERIOD:
                i + 1
            ]

            high = max(
                x["high"]
                for x in section
            )

            low = min(
                x["low"]
                for x in section
            )

            kijun = (
                high + low
            ) / 2.0

        row = dict(candles[i])

        row["tenkan"] = tenkan
        row["kijun"] = kijun

        rows.append(row)

    return rows


# ============================================================
# CROSSOVER
# ============================================================

def find_latest_cross(candles):

    if len(candles) < KIJUN_PERIOD + 2:
        return None

    latest = None

    for i in range(1, len(candles)):

        prev_t = candles[i - 1]["tenkan"]
        prev_k = candles[i - 1]["kijun"]

        curr_t = candles[i]["tenkan"]
        curr_k = candles[i]["kijun"]

        if any(
            pd.isna(x)
            for x in [
                prev_t,
                prev_k,
                curr_t,
                curr_k
            ]
        ):
            continue

        if (
            prev_t <= prev_k
            and curr_t > curr_k
        ):
            latest = {
                "index": i,
                "direction": "BUY",
                "time": candles[i]["time"]
            }

        elif (
            prev_t >= prev_k
            and curr_t < curr_k
        ):
            latest = {
                "index": i,
                "direction": "SELL",
                "time": candles[i]["time"]
            }

    return latest


# ============================================================
# BOX
# ============================================================

def build_box(candles, cross_index):

    start = (
        cross_index
        - BOX_PERIOD
    )

    end = cross_index

    if start < 0:
        return None

    box = candles[start:end]

    if len(box) != BOX_PERIOD:
        return None

    box_high = max(
        c["high"]
        for c in box
    )

    box_low = min(
        c["low"]
        for c in box
    )

    width = box_high - box_low

    if width <= 0:
        return None

    return {
        "high": box_high,
        "low": box_low,
        "width": width
    }


# ============================================================
# LATEST BREAKOUT
# ============================================================

def find_latest_breakout(
    candles,
    cross_index,
    box
):

    start = cross_index + 1

    if start >= len(candles):
        return None

    # Scan backwards so we use the MOST RECENT
    # valid breakout.
    for i in range(
        len(candles) - 1,
        start - 1,
        -1
    ):

        candle = candles[i]

        if (
            candle["close"]
            > box["high"]
        ):

            return {
                "index": i,
                "direction": "BUY",
                "time": candle["time"],
                "price": candle["close"]
            }

        if (
            candle["close"]
            < box["low"]
        ):

            return {
                "index": i,
                "direction": "SELL",
                "time": candle["time"],
                "price": candle["close"]
            }

    return None


# ============================================================
# CONFIRMED SWING LOW
# ============================================================

def is_confirmed_swing_low(
    candles,
    index
):

    if (
        index < SWING_LEFT
        or index + SWING_RIGHT
        >= len(candles)
    ):
        return False

    value = candles[index]["low"]

    for j in range(
        index - SWING_LEFT,
        index
    ):

        if candles[j]["low"] <= value:
            return False

    for j in range(
        index + 1,
        index + SWING_RIGHT + 1
    ):

        if candles[j]["low"] <= value:
            return False

    return True


# ============================================================
# CONFIRMED SWING HIGH
# ============================================================

def is_confirmed_swing_high(
    candles,
    index
):

    if (
        index < SWING_LEFT
        or index + SWING_RIGHT
        >= len(candles)
    ):
        return False

    value = candles[index]["high"]

    for j in range(
        index - SWING_LEFT,
        index
    ):

        if candles[j]["high"] >= value:
            return False

    for j in range(
        index + 1,
        index + SWING_RIGHT + 1
    ):

        if candles[j]["high"] >= value:
            return False

    return True


# ============================================================
# FIND LAST SWING BEFORE BREAKOUT
# ============================================================

def find_swing(
    candles,
    breakout_index,
    direction
):

    end = (
        breakout_index
        - SWING_RIGHT
    )

    if end <= SWING_LEFT:
        return None

    if direction == "BUY":

        for i in range(
            end,
            SWING_LEFT - 1,
            -1
        ):

            if is_confirmed_swing_low(
                candles,
                i
            ):

                return {
                    "index": i,
                    "price": candles[i]["low"],
                    "time": candles[i]["time"]
                }

    else:

        for i in range(
            end,
            SWING_LEFT - 1,
            -1
        ):

            if is_confirmed_swing_high(
                candles,
                i
            ):

                return {
                    "index": i,
                    "price": candles[i]["high"],
                    "time": candles[i]["time"]
                }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles,
    index,
    lookback=20
):

    if index < lookback:
        return 0.0

    current = candles[index]["volume"]

    previous = [
        candles[i]["volume"]
        for i in range(
            index - lookback,
            index
        )
        if candles[i]["volume"] > 0
    ]

    if not previous:
        return 0.0

    avg = (
        sum(previous)
        / len(previous)
    )

    if avg <= 0:
        return 0.0

    return current / avg


# ============================================================
# ANALYZE SYMBOL AT A SPECIFIC INDEX
# ============================================================

def analyze_at_index(
    candles,
    end_index
):

    if end_index < KIJUN_PERIOD + BOX_PERIOD + 10:
        return None

    # Only candles available up to current point.
    visible = candles[:end_index + 1]

    cross = find_latest_cross(
        visible
    )

    if not cross:
        return None

    cross_index = cross["index"]

    # Crossover candle is NOT included in Box.
    box = build_box(
        visible,
        cross_index
    )

    if not box:
        return None

    breakout = find_latest_breakout(
        visible,
        cross_index,
        box
    )

    if not breakout:
        return None

    breakout_age = (
        end_index
        - breakout["index"]
    )

    if breakout_age > MAX_BREAKOUT_AGE:
        return None

    # Do not use a breakout before the current candle.
    if breakout["index"] > end_index:
        return None

    direction = breakout["direction"]

    swing = find_swing(
        visible,
        breakout["index"],
        direction
    )

    if not swing:
        return None

    entry = breakout["price"]

    if direction == "BUY":

        sl = (
            swing["price"]
            * (1.0 - SL_BUFFER_PERCENT / 100.0)
        )

        tp = (
            entry
            + box["width"]
            * TP_BOX_PERCENT
        )

        if sl >= entry:
            return None

        if tp <= entry:
            return None

    else:

        sl = (
            swing["price"]
            * (1.0 + SL_BUFFER_PERCENT / 100.0)
        )

        tp = (
            entry
            - box["width"]
            * TP_BOX_PERCENT
        )

        if sl <= entry:
            return None

        if tp >= entry:
            return None

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    if risk <= 0:
        return None

    rr = reward / risk

    volume_ratio = calculate_volume_ratio(
        visible,
        breakout["index"]
    )

    return {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "box_high": box["high"],
        "box_low": box["low"],
        "box_width": box["width"],
        "swing_price": swing["price"],
        "swing_time": swing["time"],
        "cross_time": cross["time"],
        "breakout_time": breakout["time"],
        "breakout_index": breakout["index"],
        "breakout_age": breakout_age,
        "volume_ratio": volume_ratio
    }


# ============================================================
# PRICE P&L
# ============================================================

def calculate_pnl(
    direction,
    entry,
    exit_price
):

    if direction == "BUY":

        return (
            (exit_price - entry)
            / entry
        ) * 100.0

    return (
        (entry - exit_price)
        / entry
    ) * 100.0


# ============================================================
# SIMULATE TRADE AFTER ENTRY
# ============================================================

def simulate_trade(
    candles,
    signal
):

    entry_index = signal["breakout_index"]

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]
    direction = signal["direction"]

    # IMPORTANT:
    # Entry occurs at breakout candle CLOSE.
    # Therefore we start checking from the NEXT candle.
    start = entry_index + 1

    if start >= len(candles):
        return None

    for i in range(
        start,
        len(candles)
    ):

        candle = candles[i]

        high = candle["high"]
        low = candle["low"]

        hit_sl = False
        hit_tp = False

        if direction == "BUY":

            if low <= sl:
                hit_sl = True

            if high >= tp:
                hit_tp = True

        else:

            if high >= sl:
                hit_sl = True

            if low <= tp:
                hit_tp = True

        # Conservative assumption:
        # if both happen in same candle,
        # SL happens first.
        if hit_sl:

            exit_price = sl
            result = "LOSS"

            pnl = calculate_pnl(
                direction,
                entry,
                exit_price
            )

            return {
                "exit_index": i,
                "exit_time": candle["time"],
                "exit_price": exit_price,
                "result": result,
                "pnl_pct": pnl
            }

        if hit_tp:

            exit_price = tp
            result = "WIN"

            pnl = calculate_pnl(
                direction,
                entry,
                exit_price
            )

            return {
                "exit_index": i,
                "exit_time": candle["time"],
                "exit_price": exit_price,
                "result": result,
                "pnl_pct": pnl
            }

    # Still open at end of historical data.
    return {
        "exit_index": len(candles) - 1,
        "exit_time": candles[-1]["time"],
        "exit_price": candles[-1]["close"],
        "result": "OPEN",
        "pnl_pct": calculate_pnl(
            direction,
            entry,
            candles[-1]["close"]
        )
    }


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    candles
):

    candles = filter_period(candles)

    if len(candles) < 100:
        return []

    candles = add_indicators(
        candles
    )

    trades = []

    current_index = (
        KIJUN_PERIOD
        + BOX_PERIOD
        + 10
    )

    while current_index < len(candles):

        signal = analyze_at_index(
            candles,
            current_index
        )

        if not signal:

            current_index += 1
            continue

        result = simulate_trade(
            candles,
            signal
        )

        if not result:

            current_index += 1
            continue

        trade = {
            "symbol": symbol,
            "direction": signal["direction"],

            "entry_time": signal["breakout_time"],
            "entry": signal["entry"],

            "sl": signal["sl"],
            "tp": signal["tp"],

            "exit_time": result["exit_time"],
            "exit": result["exit_price"],

            "result": result["result"],
            "pnl_pct": result["pnl_pct"],

            "rr": signal["rr"],

            "box_high": signal["box_high"],
            "box_low": signal["box_low"],
            "box_width": signal["box_width"],

            "swing_price": signal["swing_price"],
            "swing_time": signal["swing_time"],

            "cross_time": signal["cross_time"],

            "volume_ratio": signal["volume_ratio"]
        }

        trades.append(trade)

        # Move directly after exit.
        exit_index = result["exit_index"]

        if result["result"] == "OPEN":
            break

        current_index = exit_index + 1

    return trades


# ============================================================
# COMBINE ALL SYMBOLS
# ============================================================

def run_symbol_backtests(
    market_data
):

    all_trades = []

    print_header(
        "RUNNING INDIVIDUAL MARKET BACKTESTS"
    )

    for symbol, candles in market_data.items():

        print(
            f"[BACKTEST] {symbol}"
        )

        trades = backtest_symbol(
            symbol,
            candles
        )

        print(
            f"           trades={len(trades)}"
        )

        all_trades.extend(
            trades
        )

    all_trades.sort(
        key=lambda x: x["entry_time"]
    )

    return all_trades


# ============================================================
# ENFORCE GLOBAL ONE-TRADE RULE
# ============================================================

def enforce_one_open_trade(
    trades
):

    if not trades:
        return []

    selected = []

    global_exit_time = None

    for trade in trades:

        entry_time = trade["entry_time"]

        if (
            global_exit_time is not None
            and entry_time < global_exit_time
        ):
            continue

        selected.append(
            trade
        )

        if trade["result"] == "OPEN":

            global_exit_time = float("inf")

        else:

            global_exit_time = trade[
                "exit_time"
            ]

    return selected


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    trades
):

    closed = [
        t for t in trades
        if t["result"] in (
            "WIN",
            "LOSS"
        )
    ]

    wins = [
        t for t in closed
        if t["result"] == "WIN"
    ]

    losses = [
        t for t in closed
        if t["result"] == "LOSS"
    ]

    total = len(closed)

    win_count = len(wins)
    loss_count = len(losses)

    if total > 0:
        win_rate = (
            win_count / total
        ) * 100.0
    else:
        win_rate = 0.0

    win_values = [
        t["pnl_pct"]
        for t in wins
    ]

    loss_values = [
        t["pnl_pct"]
        for t in losses
    ]

    avg_win = (
        sum(win_values)
        / len(win_values)
        if win_values
        else 0.0
    )

    avg_loss = (
        sum(loss_values)
        / len(loss_values)
        if loss_values
        else 0.0
    )

    gross_profit = sum(
        x for x in win_values
        if x > 0
    )

    gross_loss = abs(
        sum(
            x for x in loss_values
            if x < 0
        )
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    net_pnl = sum(
        t["pnl_pct"]
        for t in closed
    )

    best = max(
        closed,
        key=lambda x: x["pnl_pct"],
        default=None
    )

    worst = min(
        closed,
        key=lambda x: x["pnl_pct"],
        default=None
    )

    return {
        "total_trades": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,

        "avg_win": avg_win,
        "avg_loss": avg_loss,

        "gross_profit": gross_profit,
        "gross_loss": gross_loss,

        "profit_factor": profit_factor,
        "net_pnl": net_pnl,

        "best": best,
        "worst": worst
    }


# ============================================================
# MAX DRAWDOWN
# ============================================================

def calculate_drawdown(
    trades
):

    closed = [
        t for t in trades
        if t["result"] in (
            "WIN",
            "LOSS"
        )
    ]

    if not closed:
        return 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for trade in closed:

        equity += trade["pnl_pct"]

        if equity > peak:
            peak = equity

        drawdown = peak - equity

        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd


# ============================================================
# DIRECTION PERFORMANCE
# ============================================================

def direction_stats(
    trades,
    direction
):

    subset = [
        t for t in trades
        if t["direction"] == direction
        and t["result"] in (
            "WIN",
            "LOSS"
        )
    ]

    wins = [
        t for t in subset
        if t["result"] == "WIN"
    ]

    total = len(subset)

    if total:
        wr = (
            len(wins)
            / total
        ) * 100.0
    else:
        wr = 0.0

    pnl = sum(
        t["pnl_pct"]
        for t in subset
    )

    return {
        "trades": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate": wr,
        "net_pnl": pnl
    }


# ============================================================
# MONTHLY STATS
# ============================================================

def monthly_stats(
    trades
):

    rows = []

    closed = [
        t for t in trades
        if t["result"] in (
            "WIN",
            "LOSS"
        )
    ]

    if not closed:
        return rows

    df = pd.DataFrame(
        closed
    )

    df["entry_datetime"] = pd.to_datetime(
        df["entry_time"],
        unit="s",
        utc=True
    )

    df["month"] = (
        df["entry_datetime"]
        .dt.strftime("%Y-%m")
    )

    grouped = df.groupby(
        "month"
    )

    for month, group in grouped:

        total = len(group)

        wins = int(
            (group["result"] == "WIN")
            .sum()
        )

        losses = int(
            (group["result"] == "LOSS")
            .sum()
        )

        wr = (
            wins / total * 100.0
            if total
            else 0.0
        )

        pnl = group[
            "pnl_pct"
        ].sum()

        rows.append({
            "month": month,
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wr,
            "net_pnl": pnl
        })

    return rows


# ============================================================
# SAVE TRADES
# ============================================================

def save_trades(
    trades
):

    if not trades:
        print(
            "[WARN] No trades to save."
        )
        return

    df = pd.DataFrame(
        trades
    )

    for column in [
        "entry_time",
        "exit_time",
        "swing_time",
        "cross_time"
    ]:

        if column in df.columns:

            df[column + "_utc"] = (
                pd.to_datetime(
                    df[column],
                    unit="s",
                    utc=True
                )
                .dt.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

    df.to_csv(
        TRADES_FILE,
        index=False
    )

    print(
        f"[OK] Saved trades: {TRADES_FILE}"
    )


# ============================================================
# SAVE SUMMARY
# ============================================================

def save_summary(
    summary
):

    with open(
        SUMMARY_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            summary,
            f,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    print(
        f"[OK] Saved summary: {SUMMARY_FILE}"
    )


# ============================================================
# SAVE MONTHLY
# ============================================================

def save_monthly(
    rows
):

    if not rows:
        return

    df = pd.DataFrame(
        rows
    )

    df.to_csv(
        MONTHLY_FILE,
        index=False
    )

    print(
        f"[OK] Saved monthly stats: {MONTHLY_FILE}"
    )


# ============================================================
# PRINT REPORT
# ============================================================

def print_report(
    trades
):

    stats = calculate_performance(
        trades
    )

    dd = calculate_drawdown(
        trades
    )

    long_stats = direction_stats(
        trades,
        "BUY"
    )

    short_stats = direction_stats(
        trades,
        "SELL"
    )

    print_header(
        "KRAKEN PRICE ACTION BACKTEST RESULT"
    )

    print(
        f"Period        : "
        f"{START_DATE} -> {END_DATE}"
    )

    print(
        f"Timeframe     : {TIMEFRAME}"
    )

    print(
        f"Markets       : TOP {TOP_SYMBOLS}"
    )

    print(
        f"Max Open      : {MAX_OPEN_TRADES}"
    )

    print()

    print(
        f"Trades        : "
        f"{stats['total_trades']}"
    )

    print(
        f"Wins          : "
        f"{stats['wins']}"
    )

    print(
        f"Losses        : "
        f"{stats['losses']}"
    )

    print(
        f"WIN RATE      : "
        f"{stats['win_rate']:.2f}%"
    )

    print()

    print(
        f"Average Win   : "
        f"{stats['avg_win']:+.4f}%"
    )

    print(
        f"Average Loss  : "
        f"{stats['avg_loss']:+.4f}%"
    )

    print(
        f"Net P&L       : "
        f"{stats['net_pnl']:+.4f}%"
    )

    if math.isinf(
        stats["profit_factor"]
    ):

        pf = "INF"

    else:

        pf = (
            f"{stats['profit_factor']:.3f}"
        )

    print(
        f"Profit Factor : {pf}"
    )

    print(
        f"Max Drawdown  : "
        f"{dd:.4f}%"
    )

    print()

    print("LONG / BUY")

    print(
        f"  Trades      : "
        f"{long_stats['trades']}"
    )

    print(
        f"  Wins        : "
        f"{long_stats['wins']}"
    )

    print(
        f"  Losses      : "
        f"{long_stats['losses']}"
    )

    print(
        f"  Win Rate    : "
        f"{long_stats['win_rate']:.2f}%"
    )

    print(
        f"  Net P&L     : "
        f"{long_stats['net_pnl']:+.4f}%"
    )

    print()

    print("SHORT / SELL")

    print(
        f"  Trades      : "
        f"{short_stats['trades']}"
    )

    print(
        f"  Wins        : "
        f"{short_stats['wins']}"
    )

    print(
        f"  Losses      : "
        f"{short_stats['losses']}"
    )

    print(
        f"  Win Rate    : "
        f"{short_stats['win_rate']:.2f}%"
    )

    print(
        f"  Net P&L     : "
        f"{short_stats['net_pnl']:+.4f}%"
    )

    if stats["best"]:

        print()

        print(
            "BEST TRADE"
        )

        print(
            f"  {stats['best']['symbol']} "
            f"{stats['best']['direction']} "
            f"{stats['best']['pnl_pct']:+.4f}%"
        )

    if stats["worst"]:

        print()

        print(
            "WORST TRADE"
        )

        print(
            f"  {stats['worst']['symbol']} "
            f"{stats['worst']['direction']} "
            f"{stats['worst']['pnl_pct']:+.4f}%"
        )

    print()

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    ensure_directories()

    print_header(
        "KRAKEN PRICE ACTION BACKTEST"
    )

    print(
        f"Period     : "
        f"{START_DATE} -> {END_DATE}"
    )

    print(
        f"Timeframe  : {TIMEFRAME}"
    )

    print(
        f"Universe   : TOP {TOP_SYMBOLS}"
    )

    print(
        "Strategy   : Tenkan/Kijun + Box Breakout"
    )

    print(
        "Mode       : SIMULATION ONLY"
    )

    print()

    # --------------------------------------------------------
    # 1. Current TOP 100
    # --------------------------------------------------------

    markets = get_top_symbols()

    if not markets:

        raise RuntimeError(
            "No markets found."
        )

    # --------------------------------------------------------
    # 2. Download candles
    # --------------------------------------------------------

    market_data = fetch_all_candles(
        markets
    )

    if not market_data:

        raise RuntimeError(
            "No candle data available."
        )

    print()

    print(
        f"[INFO] Markets with data: "
        f"{len(market_data)}"
    )

    # --------------------------------------------------------
    # 3. Run per-symbol backtests
    # --------------------------------------------------------

    raw_trades = run_symbol_backtests(
        market_data
    )

    print()

    print(
        f"[INFO] Raw trades: "
        f"{len(raw_trades)}"
    )

    # --------------------------------------------------------
    # 4. Apply global MAX_OPEN_TRADES = 1
    # --------------------------------------------------------

    final_trades = enforce_one_open_trade(
        raw_trades
    )

    print(
        f"[INFO] After MAX_OPEN_TRADES=1: "
        f"{len(final_trades)}"
    )

    # --------------------------------------------------------
    # 5. Save results
    # --------------------------------------------------------

    save_trades(
        final_trades
    )

    stats = calculate_performance(
        final_trades
    )

    dd = calculate_drawdown(
        final_trades
    )

    long_stats = direction_stats(
        final_trades,
        "BUY"
    )

    short_stats = direction_stats(
        final_trades,
        "SELL"
    )

    summary = {
        "period": {
            "start": START_DATE,
            "end": END_DATE
        },

        "timeframe": TIMEFRAME,

        "top_symbols": TOP_SYMBOLS,

        "strategy": {
            "tenkan": TENKAN_PERIOD,
            "kijun": KIJUN_PERIOD,
            "box_period": BOX_PERIOD,
            "swing_left": SWING_LEFT,
            "swing_right": SWING_RIGHT,
            "tp_box_percent": TP_BOX_PERCENT,
            "sl_buffer_percent": SL_BUFFER_PERCENT,
            "max_open_trades": MAX_OPEN_TRADES,
            "max_breakout_age": MAX_BREAKOUT_AGE
        },

        "performance": stats,

        "max_drawdown": dd,

        "long": long_stats,

        "short": short_stats,

        "raw_trades": len(raw_trades),

        "final_trades": len(final_trades),

        "generated_at": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    }

    save_summary(
        summary
    )

    monthly = monthly_stats(
        final_trades
    )

    save_monthly(
        monthly
    )

    # --------------------------------------------------------
    # 6. Print final report
    # --------------------------------------------------------

    print_report(
        final_trades
    )

    elapsed = (
        time.time()
        - started
    )

    print(
        f"[INFO] Runtime: "
        f"{elapsed:.1f} seconds"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOP] Interrupted by user."
        )

    except Exception as exc:

        print()
        print(
            "[FATAL] Backtest failed."
        )

        print(
            f"[FATAL] {exc}"
        )

        traceback.print_exc()

        raise
