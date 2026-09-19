# ============================================================
# KRAKEN FUTURES PUMP / DUMP DETECTOR
# VERSION 1.0
# ============================================================
#
# PURPOSE:
#   Independent pump/dump detection strategy
#   Automatic Kraken Futures historical data download
#   6-month 5-minute backtest
#   SQLite local storage
#   No input files required from the user
#
# DATA:
#   Kraken Futures public REST API
#
# MODE:
#   BACKTEST ONLY
#
# IMPORTANT:
#   This is NOT live trading code.
# ============================================================

import os
import time
import math
import sqlite3
import requests
import statistics
from datetime import datetime, timezone, timedelta
from collections import deque

# ============================================================
# CONFIG
# ============================================================

VERSION = "1.0"

BASE_URL = "https://futures.kraken.com/api/charts/v1"

DB_FILE = "pump_detector.db"

# ------------------------------------------------------------
# BACKTEST
# ------------------------------------------------------------

BACKTEST_DAYS = 180

TIMEFRAME = "5m"

# Extra candles needed before the first test candle
WARMUP_DAYS = 14

# ------------------------------------------------------------
# MARKET UNIVERSE
# ------------------------------------------------------------
#
# These are common Kraken Futures perpetual symbols.
#
# The program also tries to discover currently active markets
# automatically. If discovery fails, this fallback list is used.
#

FALLBACK_SYMBOLS = [
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_LINKUSD",
    "PF_AVAXUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",
    "PF_BCHUSD",
    "PF_UNIUSD",
    "PF_ATOMUSD",
    "PF_FILUSD",
    "PF_AAVEUSD",
    "PF_ALGOUSD",
    "PF_ETCUSD",
    "PF_XLMUSD",
    "PF_SUIUSD",
    "PF_NEARUSD",
]

# How many markets to test.
# 0 = all discovered USD perpetual markets.
TOP_N = 100

# ------------------------------------------------------------
# DATA DOWNLOAD
# ------------------------------------------------------------

REQUEST_TIMEOUT = 30

# Number of candles requested per API call.
# Smaller chunks are safer for API limits.
CANDLE_CHUNK = 1000

REQUEST_SLEEP = 0.35

MAX_RETRIES = 5

# ------------------------------------------------------------
# SIGNAL
# ------------------------------------------------------------

# Volume abnormality
RVOL_PERIOD = 48

MIN_RVOL = 3.0

# Price momentum
MOMENTUM_LOOKBACK = 3

MIN_PRICE_MOVE_PCT = 1.00

# Candle strength
MIN_BODY_RATIO = 0.55

# Breakout lookback
BREAKOUT_LOOKBACK = 24

# Minimum range expansion
MIN_RANGE_EXPANSION = 1.25

# Score
MIN_SCORE = 7

# ------------------------------------------------------------
# TRADE
# ------------------------------------------------------------

TP_PCT = 2.00
SL_PCT = 1.00

MIN_RR = 1.50

# Maximum holding period
MAX_HOLD_BARS = 24

# Prevent immediate repeated entries
COOLDOWN_BARS = 6

# One position per symbol
ONE_POSITION_PER_SYMBOL = True

# ------------------------------------------------------------
# COSTS
# ------------------------------------------------------------

# Estimated round-trip trading cost.
# Adjust later if you want to model your exact exchange fees.
FEE_PCT_PER_SIDE = 0.04

# Estimated slippage per side.
SLIPPAGE_PCT_PER_SIDE = 0.02

# Total cost applied to every completed trade.
ROUND_TRIP_COST_PCT = (
    FEE_PCT_PER_SIDE * 2
    + SLIPPAGE_PCT_PER_SIDE * 2
)

# ============================================================
# SQLITE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY(symbol, timeframe, timestamp)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            signal_time INTEGER,
            entry_time INTEGER,
            exit_time INTEGER,
            entry REAL,
            exit REAL,
            sl REAL,
            tp REAL,
            pnl_pct REAL,
            gross_pnl_pct REAL,
            cost_pct REAL,
            hold_bars INTEGER,
            exit_reason TEXT,
            score INTEGER,
            rvol REAL,
            momentum_pct REAL
        )
    """)

    conn.commit()
    return conn


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "PumpDetector/1.0",
    "Accept": "application/json",
})


def http_get(url, params=None):
    for attempt in range(1, MAX_RETRIES + 1):

        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code in (429, 500, 502, 503, 504):
                wait = attempt * 2
                print(
                    f"HTTP {response.status_code} "
                    f"| retry {attempt}/{MAX_RETRIES} "
                    f"| wait {wait}s"
                )
                time.sleep(wait)
                continue

            print(
                f"HTTP ERROR {response.status_code}: "
                f"{response.text[:300]}"
            )

        except requests.RequestException as e:
            print(
                f"NETWORK ERROR | attempt "
                f"{attempt}/{MAX_RETRIES}: {e}"
            )
            time.sleep(attempt * 2)

    return None


# ============================================================
# MARKET DISCOVERY
# ============================================================

def discover_symbols():

    """
    Try Kraken public instruments endpoint.

    If unavailable, use fallback symbols.
    """

    urls = [
        "https://futures.kraken.com/derivatives/api/v3/instruments",
        "https://futures.kraken.com/derivatives/api/v3/tickers",
    ]

    # --------------------------------------------------------
    # Try instruments
    # --------------------------------------------------------

    data = http_get(urls[0])

    symbols = []

    if data:

        instruments = data.get("instruments", [])

        for item in instruments:

            if not isinstance(item, dict):
                continue

            symbol = (
                item.get("symbol")
                or item.get("tradeable")
            )

            if not symbol:
                continue

            symbol = str(symbol)

            # Only USD perpetual-style products
            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            symbols.append(symbol)

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    if not symbols:

        print("Automatic market discovery failed.")
        print("Using fallback symbol list.")

        symbols = FALLBACK_SYMBOLS.copy()

    # Remove duplicates
    symbols = sorted(set(symbols))

    if TOP_N > 0:
        symbols = symbols[:TOP_N]

    print()
    print("Markets discovered:", len(symbols))

    return symbols


# ============================================================
# CANDLE DOWNLOAD
# ============================================================

def fetch_candles(
    symbol,
    resolution=TIMEFRAME,
    start_ts=None,
    end_ts=None
):

    url = (
        f"{BASE_URL}/trade/"
        f"{symbol}/"
        f"{resolution}"
    )

    all_candles = []

    current_from = start_ts

    while current_from < end_ts:

        params = {
            "from": int(current_from),
            "to": int(end_ts),
            "count": CANDLE_CHUNK,
        }

        data = http_get(url, params)

        if not data:
            print(
                f"{symbol}: no response "
                f"from Kraken."
            )
            break

        candles = data.get("candles", [])

        if not candles:
            break

        candles = sorted(
            candles,
            key=lambda x: int(x["time"])
        )

        all_candles.extend(candles)

        last_ts_ms = int(candles[-1]["time"])
        last_ts = last_ts_ms // 1000

        if last_ts <= current_from:
            break

        current_from = last_ts + 1

        print(
            f"{symbol}: downloaded "
            f"{len(all_candles):,} candles",
            end="\r"
        )

        if not data.get("more_candles", False):
            break

        time.sleep(REQUEST_SLEEP)

    print()

    # Deduplicate
    unique = {}

    for c in all_candles:

        try:
            ts = int(c["time"])

            unique[ts] = {
                "timestamp": ts // 1000,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }

        except Exception:
            continue

    return sorted(
        unique.values(),
        key=lambda x: x["timestamp"]
    )


# ============================================================
# DATABASE CANDLE STORAGE
# ============================================================

def save_candles(conn, symbol, candles):

    if not candles:
        return

    rows = []

    for c in candles:

        rows.append((
            symbol,
            TIMEFRAME,
            c["timestamp"],
            c["open"],
            c["high"],
            c["low"],
            c["close"],
            c["volume"],
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO candles
        (
            symbol,
            timeframe,
            timestamp,
            open,
            high,
            low,
            close,
            volume
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)

    conn.commit()


# ============================================================
# DATABASE LOAD
# ============================================================

def load_candles(conn, symbol):

    rows = conn.execute("""
        SELECT
            timestamp,
            open,
            high,
            low,
            close,
            volume
        FROM candles
        WHERE symbol = ?
          AND timeframe = ?
        ORDER BY timestamp ASC
    """, (symbol, TIMEFRAME)).fetchall()

    candles = []

    for r in rows:

        candles.append({
            "timestamp": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        })

    return candles


# ============================================================
# STATISTICS
# ============================================================

def mean(values):

    if not values:
        return 0.0

    return sum(values) / len(values)


def median(values):

    if not values:
        return 0.0

    return statistics.median(values)


def safe_pct(a, b):

    if b == 0:
        return 0.0

    return ((a / b) - 1.0) * 100.0


# ============================================================
# SIGNAL ENGINE
# ============================================================

def calculate_signal(candles, i):

    if i < max(
        RVOL_PERIOD,
        BREAKOUT_LOOKBACK,
        MOMENTUM_LOOKBACK
    ):
        return None

    c = candles[i]

    close = c["close"]
    high = c["high"]
    low = c["low"]
    open_price = c["open"]
    volume = c["volume"]

    if close <= 0 or volume <= 0:
        return None

    score_long = 0
    score_short = 0

    # --------------------------------------------------------
    # 1. RVOL
    # --------------------------------------------------------

    previous_volumes = [
        x["volume"]
        for x in candles[
            i - RVOL_PERIOD:i
        ]
        if x["volume"] > 0
    ]

    if len(previous_volumes) < RVOL_PERIOD // 2:
        return None

    avg_volume = mean(previous_volumes)

    if avg_volume <= 0:
        return None

    rvol = volume / avg_volume

    if rvol >= MIN_RVOL:
        score_long += 2
        score_short += 2

    elif rvol >= 2.0:
        score_long += 1
        score_short += 1

    # --------------------------------------------------------
    # 2. Price momentum
    # --------------------------------------------------------

    previous_close = candles[
        i - MOMENTUM_LOOKBACK
    ]["close"]

    momentum_pct = safe_pct(
        close,
        previous_close
    )

    if momentum_pct >= MIN_PRICE_MOVE_PCT:

        score_long += 2

    if momentum_pct <= -MIN_PRICE_MOVE_PCT:

        score_short += 2

    # --------------------------------------------------------
    # 3. Candle body
    # --------------------------------------------------------

    candle_range = high - low

    if candle_range > 0:

        body_ratio = abs(
            close - open_price
        ) / candle_range

    else:
        body_ratio = 0

    if body_ratio >= MIN_BODY_RATIO:

        if close > open_price:
            score_long += 1

        elif close < open_price:
            score_short += 1

    # --------------------------------------------------------
    # 4. Breakout
    # --------------------------------------------------------

    previous_highs = [
        x["high"]
        for x in candles[
            i - BREAKOUT_LOOKBACK:i
        ]
    ]

    previous_lows = [
        x["low"]
        for x in candles[
            i - BREAKOUT_LOOKBACK:i
        ]
    ]

    resistance = max(previous_highs)
    support = min(previous_lows)

    breakout_long = close > resistance
    breakout_short = close < support

    if breakout_long:
        score_long += 2

    if breakout_short:
        score_short += 2

    # --------------------------------------------------------
    # 5. Range expansion
    # --------------------------------------------------------

    ranges = [
        x["high"] - x["low"]
        for x in candles[
            i - RVOL_PERIOD:i
        ]
        if x["high"] > x["low"]
    ]

    avg_range = mean(ranges)

    current_range = high - low

    if avg_range > 0:

        range_ratio = (
            current_range / avg_range
        )

    else:
        range_ratio = 0

    if range_ratio >= MIN_RANGE_EXPANSION:

        if close > open_price:
            score_long += 1

        elif close < open_price:
            score_short += 1

    # --------------------------------------------------------
    # FINAL DECISION
    # --------------------------------------------------------

    direction = None
    score = 0

    if (
        score_long >= MIN_SCORE
        and score_long > score_short
    ):
        direction = "LONG"
        score = score_long

    elif (
        score_short >= MIN_SCORE
        and score_short > score_long
    ):
        direction = "SHORT"
        score = score_short

    if direction is None:
        return None

    return {
        "direction": direction,
        "score": score,
        "rvol": rvol,
        "momentum_pct": momentum_pct,
        "entry": close,
    }


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    candles,
    entry_index,
    signal
):

    direction = signal["direction"]

    entry = signal["entry"]

    if direction == "LONG":

        sl = entry * (
            1 - SL_PCT / 100
        )

        tp = entry * (
            1 + TP_PCT / 100
        )

    else:

        sl = entry * (
            1 + SL_PCT / 100
        )

        tp = entry * (
            1 - TP_PCT / 100
        )

    max_exit_index = min(
        len(candles) - 1,
        entry_index + MAX_HOLD_BARS
    )

    for j in range(
        entry_index + 1,
        max_exit_index + 1
    ):

        candle = candles[j]

        high = candle["high"]
        low = candle["low"]

        # ----------------------------------------------------
        # IMPORTANT:
        # If both SL and TP are touched in the same candle,
        # assume SL is hit first.
        #
        # This is deliberately conservative because OHLC
        # candles do not reveal intrabar order.
        # ----------------------------------------------------

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl and hit_tp:

                exit_price = sl
                reason = "SL_AND_TP_SAME_CANDLE"

                return make_trade_result(
                    signal,
                    entry,
                    exit_price,
                    j,
                    entry_index,
                    reason,
                    sl,
                    tp,
                    candles
                )

            if hit_sl:

                return make_trade_result(
                    signal,
                    entry,
                    sl,
                    j,
                    entry_index,
                    "SL",
                    sl,
                    tp,
                    candles
                )

            if hit_tp:

                return make_trade_result(
                    signal,
                    entry,
                    tp,
                    j,
                    entry_index,
                    "TP",
                    sl,
                    tp,
                    candles
                )

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl and hit_tp:

                exit_price = sl
                reason = "SL_AND_TP_SAME_CANDLE"

                return make_trade_result(
                    signal,
                    entry,
                    exit_price,
                    j,
                    entry_index,
                    reason,
                    sl,
                    tp,
                    candles
                )

            if hit_sl:

                return make_trade_result(
                    signal,
                    entry,
                    sl,
                    j,
                    entry_index,
                    "SL",
                    sl,
                    tp,
                    candles
                )

            if hit_tp:

                return make_trade_result(
                    signal,
                    entry,
                    tp,
                    j,
                    entry_index,
                    "TP",
                    sl,
                    tp,
                    candles
                )

    # --------------------------------------------------------
    # TIME EXIT
    # --------------------------------------------------------

    exit_index = max_exit_index

    exit_price = candles[
        exit_index
    ]["close"]

    return make_trade_result(
        signal,
        entry,
        exit_price,
        exit_index,
        entry_index,
        "TIME_EXIT",
        sl,
        tp,
        candles
    )


# ============================================================
# TRADE RESULT
# ============================================================

def make_trade_result(
    signal,
    entry,
    exit_price,
    exit_index,
    entry_index,
    reason,
    sl,
    tp,
    candles
):

    direction = signal["direction"]

    if direction == "LONG":

        gross_pnl = (
            (exit_price / entry) - 1
        ) * 100

    else:

        gross_pnl = (
            (entry / exit_price) - 1
        ) * 100

    net_pnl = (
        gross_pnl
        - ROUND_TRIP_COST_PCT
    )

    return {
        "direction": direction,
        "entry": entry,
        "exit": exit_price,
        "sl": sl,
        "tp": tp,
        "gross_pnl_pct": gross_pnl,
        "pnl_pct": net_pnl,
        "cost_pct": ROUND_TRIP_COST_PCT,
        "hold_bars": exit_index - entry_index,
        "exit_reason": reason,
        "exit_index": exit_index,
        "score": signal["score"],
        "rvol": signal["rvol"],
        "momentum_pct": signal["momentum_pct"],
    }


# ============================================================
# SAVE TRADE
# ============================================================

def save_trade(
    conn,
    symbol,
    signal_index,
    signal,
    result,
    candles
):

    signal_time = candles[
        signal_index
    ]["timestamp"]

    entry_time = signal_time

    exit_time = candles[
        result["exit_index"]
    ]["timestamp"]

    conn.execute("""
        INSERT INTO trades
        (
            symbol,
            direction,
            signal_time,
            entry_time,
            exit_time,
            entry,
            exit,
            sl,
            tp,
            pnl_pct,
            gross_pnl_pct,
            cost_pct,
            hold_bars,
            exit_reason,
            score,
            rvol,
            momentum_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        result["direction"],
        signal_time,
        entry_time,
        exit_time,
        result["entry"],
        result["exit"],
        result["sl"],
        result["tp"],
        result["pnl_pct"],
        result["gross_pnl_pct"],
        result["cost_pct"],
        result["hold_bars"],
        result["exit_reason"],
        result["score"],
        result["rvol"],
        result["momentum_pct"],
    ))


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    conn,
    symbol,
    candles
):

    if len(candles) < 300:

        print(
            f"{symbol}: insufficient candles"
        )

        return []

    trades = []

    i = max(
        RVOL_PERIOD,
        BREAKOUT_LOOKBACK,
        MOMENTUM_LOOKBACK
    )

    last_trade_index = -999999

    while i < len(candles) - 1:

        # ----------------------------------------------------
        # Cooldown
        # ----------------------------------------------------

        if (
            i - last_trade_index
            < COOLDOWN_BARS
        ):
            i += 1
            continue

        signal = calculate_signal(
            candles,
            i
        )

        if signal is None:

            i += 1
            continue

        # ----------------------------------------------------
        # Trade
        # ----------------------------------------------------

        result = simulate_trade(
            candles,
            i,
            signal
        )

        if result is None:

            i += 1
            continue

        save_trade(
            conn,
            symbol,
            i,
            signal,
            result,
            candles
        )

        trades.append({
            "symbol": symbol,
            **result,
            "signal_time": candles[i]["timestamp"],
        })

        last_trade_index = (
            result["exit_index"]
        )

        # ----------------------------------------------------
        # Jump after trade closes.
        # ----------------------------------------------------

        i = (
            result["exit_index"]
            + COOLDOWN_BARS
        )

    conn.commit()

    return trades


# ============================================================
# EQUITY / PERFORMANCE
# ============================================================

def calculate_performance(trades):

    if not trades:

        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "net_pnl": 0,
            "profit_factor": 0,
            "max_drawdown": 0,
            "avg_trade": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "expectancy": 0,
            "best": 0,
            "worst": 0,
        }

    ordered = sorted(
        trades,
        key=lambda x: x["signal_time"]
    )

    pnls = [
        float(x["pnl_pct"])
        for x in ordered
    ]

    wins = [
        x for x in pnls
        if x > 0
    ]

    losses = [
        x for x in pnls
        if x <= 0
    ]

    total = len(pnls)

    win_count = len(wins)

    loss_count = len(losses)

    win_rate = (
        win_count / total * 100
        if total
        else 0
    )

    net_pnl = sum(pnls)

    gross_profit = sum(wins)

    gross_loss = abs(sum(losses))

    if gross_loss > 0:

        profit_factor = (
            gross_profit / gross_loss
        )

    else:

        profit_factor = float("inf")

    # --------------------------------------------------------
    # Equity curve
    # --------------------------------------------------------

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for pnl in pnls:

        equity += pnl

        peak = max(
            peak,
            equity
        )

        drawdown = (
            equity - peak
        )

        max_drawdown = min(
            max_drawdown,
            drawdown
        )

    avg_trade = mean(pnls)

    avg_win = mean(wins)

    avg_loss = mean(losses)

    expectancy = (
        (win_rate / 100) * avg_win
        + ((100 - win_rate) / 100)
        * avg_loss
    )

    return {
        "total": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "avg_trade": avg_trade,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "best": max(pnls),
        "worst": min(pnls),
    }


# ============================================================
# PRINT PERFORMANCE
# ============================================================

def print_performance(performance):

    pf = performance["profit_factor"]

    if math.isinf(pf):
        pf_text = "INF"
    else:
        pf_text = f"{pf:.2f}"

    print()
    print("=" * 60)
    print("           PUMP / DUMP DETECTOR")
    print("              6M BACKTEST")
    print("=" * 60)

    print(
        f"Total Trades       : "
        f"{performance['total']:,}"
    )

    print(
        f"Wins               : "
        f"{performance['wins']:,}"
    )

    print(
        f"Losses             : "
        f"{performance['losses']:,}"
    )

    print(
        f"Win Rate           : "
        f"{performance['win_rate']:.2f}%"
    )

    print(
        f"Net P&L            : "
        f"{performance['net_pnl']:.2f}%"
    )

    print(
        f"Profit Factor      : "
        f"{pf_text}"
    )

    print(
        f"Max Drawdown       : "
        f"{performance['max_drawdown']:.2f}%"
    )

    print(
        f"Average Trade      : "
        f"{performance['avg_trade']:.3f}%"
    )

    print(
        f"Average Win        : "
        f"{performance['avg_win']:.3f}%"
    )

    print(
        f"Average Loss       : "
        f"{performance['avg_loss']:.3f}%"
    )

    print(
        f"Expectancy         : "
        f"{performance['expectancy']:.3f}%"
    )

    print(
        f"Best Trade         : "
        f"{performance['best']:.3f}%"
    )

    print(
        f"Worst Trade        : "
        f"{performance['worst']:.3f}%"
    )

    print("=" * 60)


# ============================================================
# MARKET SUMMARY
# ============================================================

def print_market_summary(all_trades):

    if not all_trades:
        print("No trades.")
        return

    by_symbol = {}

    for trade in all_trades:

        symbol = trade["symbol"]

        by_symbol.setdefault(
            symbol,
            []
        ).append(trade)

    rows = []

    for symbol, trades in by_symbol.items():

        perf = calculate_performance(
            trades
        )

        rows.append((
            symbol,
            perf["total"],
            perf["win_rate"],
            perf["net_pnl"],
            perf["profit_factor"],
        ))

    rows.sort(
        key=lambda x: x[3],
        reverse=True
    )

    print()
    print("=" * 75)
    print("MARKET PERFORMANCE")
    print("=" * 75)

    print(
        f"{'SYMBOL':15}"
        f"{'TRADES':>8}"
        f"{'WR%':>10}"
        f"{'PNL%':>12}"
        f"{'PF':>10}"
    )

    print("-" * 75)

    for row in rows:

        symbol, total, wr, pnl, pf = row

        pf_text = (
            "INF"
            if math.isinf(pf)
            else f"{pf:.2f}"
        )

        print(
            f"{symbol:15}"
            f"{total:8d}"
            f"{wr:10.2f}"
            f"{pnl:12.2f}"
            f"{pf_text:>10}"
        )

    print("=" * 75)


# ============================================================
# DOWNLOAD ALL DATA
# ============================================================

def download_all(conn, symbols):

    now = datetime.now(
        timezone.utc
    )

    end_dt = now

    start_dt = (
        now
        - timedelta(
            days=BACKTEST_DAYS
            + WARMUP_DAYS
        )
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    print()
    print("=" * 60)
    print("DOWNLOADING KRAKEN FUTURES DATA")
    print("=" * 60)

    print(
        "From:",
        start_dt.strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    )

    print(
        "To  :",
        end_dt.strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    )

    print(
        "Markets:",
        len(symbols)
    )

    print()

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"[{number}/{len(symbols)}] "
            f"{symbol}"
        )

        candles = fetch_candles(
            symbol,
            TIMEFRAME,
            start_ts,
            end_ts
        )

        if candles:

            save_candles(
                conn,
                symbol,
                candles
            )

            print(
                f"Saved {len(candles):,} candles."
            )

        else:

            print(
                f"No data for {symbol}."
            )

        print()


# ============================================================
# CLEAR OLD TRADES
# ============================================================

def clear_backtest_trades(conn):

    conn.execute(
        "DELETE FROM trades"
    )

    conn.commit()


# ============================================================
# RUN BACKTEST
# ============================================================

def run_backtest(conn, symbols):

    print()
    print("=" * 60)
    print("RUNNING BACKTEST")
    print("=" * 60)

    all_trades = []

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        candles = load_candles(
            conn,
            symbol
        )

        if not candles:

            print(
                f"[{number}/{len(symbols)}] "
                f"{symbol}: no candles"
            )

            continue

        print(
            f"[{number}/{len(symbols)}] "
            f"{symbol}: "
            f"{len(candles):,} candles"
        )

        trades = backtest_symbol(
            conn,
            symbol,
            candles
        )

        all_trades.extend(
            trades
        )

        if trades:

            print(
                f"    Signals: "
                f"{len(trades)}"
            )

    return all_trades


# ============================================================
# EXPORT SIMPLE CSV
# ============================================================

def export_trades_csv(
    trades,
    filename="pump_trades.csv"
):

    if not trades:
        return

    import csv

    fields = [
        "symbol",
        "direction",
        "signal_time",
        "entry",
        "exit",
        "sl",
        "tp",
        "gross_pnl_pct",
        "pnl_pct",
        "cost_pct",
        "hold_bars",
        "exit_reason",
        "score",
        "rvol",
        "momentum_pct",
    ]

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for trade in trades:

            writer.writerow({
                key: trade.get(key)
                for key in fields
            })

    print()
    print(
        f"Trade CSV exported: "
        f"{filename}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("KRAKEN FUTURES PUMP / DUMP DETECTOR")
    print(f"VERSION {VERSION}")
    print("=" * 60)

    print()
    print("Mode              : BACKTEST")
    print("Timeframe         :", TIMEFRAME)
    print(
        "Backtest period   :",
        BACKTEST_DAYS,
        "days"
    )
    print(
        "Warmup            :",
        WARMUP_DAYS,
        "days"
    )
    print(
        "TP                :",
        f"{TP_PCT:.2f}%"
    )
    print(
        "SL                :",
        f"{SL_PCT:.2f}%"
    )
    print(
        "Minimum RR        :",
        f"{MIN_RR:.2f}"
    )
    print(
        "Round-trip cost   :",
        f"{ROUND_TRIP_COST_PCT:.3f}%"
    )

    conn = db_connect()

    symbols = discover_symbols()

    if not symbols:

        print(
            "No markets available."
        )

        return

    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    download_all(
        conn,
        symbols
    )

    # --------------------------------------------------------
    # Clear old results
    # --------------------------------------------------------

    clear_backtest_trades(
        conn
    )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    all_trades = run_backtest(
        conn,
        symbols
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    performance = calculate_performance(
        all_trades
    )

    print_performance(
        performance
    )

    print_market_summary(
        all_trades
    )

    # --------------------------------------------------------
    # Export
    # --------------------------------------------------------

    export_trades_csv(
        all_trades
    )

    print()
    print("=" * 60)
    print("BACKTEST FINISHED")
    print("=" * 60)

    print()
    print("Database :", DB_FILE)
    print("Trades   : pump_trades.csv")
    print()


if __name__ == "__main__":
    main()
