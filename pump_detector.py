# ============================================================
# KRAKEN FUTURES PUMP DETECTOR
# VERSION 2.1
# ============================================================
#
# Independent 5-minute Pump / Dump detector
#
# - Downloads historical Kraken Futures data automatically
# - 6 months backtest + warmup
# - Selects PF_*USD perpetual markets
# - Ranks markets by current 24h volume
# - Detects abnormal volume + momentum + breakout
# - No look-ahead bias
# - Conservative same-candle TP/SL handling
# - Includes fees + slippage
# - SQLite database
# - CSV export
# - NO REAL TRADING
#
# ============================================================

import csv
import math
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.1"

BASE_URL = "https://futures.kraken.com/api/charts/v1"
INSTRUMENTS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/instruments"
)
TICKERS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

DB_FILE = "pump_detector.db"
CSV_FILE = "pump_trades.csv"

TIMEFRAME = "5m"
TICK_TYPE = "trade"

BACKTEST_DAYS = 180
WARMUP_DAYS = 14

TOP_N = 100

CANDLE_CHUNK = 1000

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.25
MAX_RETRIES = 5


# ============================================================
# SIGNAL PARAMETERS
# ============================================================

RVOL_LOOKBACK = 48
MIN_RVOL = 3.0

MOMENTUM_BARS = 3
MIN_MOMENTUM_PCT = 1.0

BODY_LOOKBACK = 20
MIN_BODY_RATIO = 0.55

BREAKOUT_LOOKBACK = 24

RANGE_LOOKBACK = 20
MIN_RANGE_EXPANSION = 1.25

MIN_SCORE = 7


# ============================================================
# TRADE PARAMETERS
# ============================================================

TP_PCT = 2.00
SL_PCT = 1.00

MIN_RR = 1.50

MAX_HOLD_BARS = 24

COOLDOWN_BARS = 6


# ============================================================
# COSTS
# ============================================================

FEE_PER_SIDE_PCT = 0.04
SLIPPAGE_PER_SIDE_PCT = 0.02

TOTAL_ROUND_TRIP_COST_PCT = (
    FEE_PER_SIDE_PCT * 2
    + SLIPPAGE_PER_SIDE_PCT * 2
)


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "PumpDetector/2.1",
        "Accept": "application/json",
    }
)


# ============================================================
# DATABASE
# ============================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    cur = conn.cursor()

    cur.execute(
        """
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
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_time INTEGER NOT NULL,
            entry_time INTEGER NOT NULL,
            exit_time INTEGER NOT NULL,
            entry REAL NOT NULL,
            exit REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            gross_pct REAL NOT NULL,
            cost_pct REAL NOT NULL,
            net_pct REAL NOT NULL,
            result TEXT NOT NULL,
            hold_bars INTEGER NOT NULL,
            score INTEGER NOT NULL,
            rvol REAL NOT NULL,
            momentum_pct REAL NOT NULL
        )
        """
    )

    conn.commit()

    return conn


# ============================================================
# HTTP REQUEST
# ============================================================

def get_json(url, params=None):

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            print(
                f"    Request failed "
                f"(attempt {attempt}/{MAX_RETRIES}): {exc}"
            )

            wait_seconds = min(
                2 ** (attempt - 1),
                10,
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        f"Request failed after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# MARKET DISCOVERY
# ============================================================

def discover_markets():

    print()
    print("=" * 75)
    print("DISCOVERING KRAKEN FUTURES MARKETS")
    print("=" * 75)

    markets = []

    try:

        data = get_json(
            INSTRUMENTS_URL
        )

        instruments = data.get(
            "instruments",
            []
        )

        for item in instruments:

            symbol = item.get("symbol")

            if not symbol:
                continue

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            markets.append(symbol)

    except Exception as exc:

        print(
            f"Instrument discovery failed: {exc}"
        )

    markets = sorted(
        set(markets)
    )

    print(
        f"Discovered {len(markets)} PF_*USD markets."
    )

    if not markets:

        raise RuntimeError(
            "No PF_*USD markets discovered."
        )

    return markets


# ============================================================
# MARKET VOLUME RANKING
# ============================================================

def rank_markets_by_volume(markets):

    print()
    print("=" * 75)
    print("RANKING MARKETS BY CURRENT 24H VOLUME")
    print("=" * 75)

    market_set = set(markets)

    try:

        data = get_json(
            TICKERS_URL
        )

    except Exception as exc:

        print(
            f"Ticker request failed: {exc}"
        )

        print(
            "Using first available markets as fallback."
        )

        return markets[:TOP_N]

    tickers = data.get(
        "tickers",
        []
    )

    ranked = []

    for ticker in tickers:

        symbol = ticker.get(
            "symbol"
        )

        if symbol not in market_set:
            continue

        raw_volume = (
            ticker.get("volume24h")
            or ticker.get("volume")
            or 0
        )

        try:
            volume = float(
                raw_volume
            )
        except Exception:
            volume = 0.0

        ranked.append(
            (
                symbol,
                volume,
            )
        )

    ranked.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    selected = [
        symbol
        for symbol, volume in ranked[:TOP_N]
    ]

    if not selected:

        print(
            "No usable volume data."
        )

        return markets[:TOP_N]

    print(
        f"Selected {len(selected)} markets."
    )

    print()
    print("Top 10 current-volume markets:")

    for symbol, volume in ranked[:10]:

        print(
            f"  {symbol:<18} "
            f"24h volume = {volume:,.2f}"
        )

    return selected


# ============================================================
# DOWNLOAD CANDLES
# ============================================================

def download_candles(
    symbol,
    start_ts,
    end_ts,
):

    print()
    print(
        f"Downloading {symbol}..."
    )

    all_rows = []

    current_from = int(
        start_ts
    )

    safety_counter = 0

    while current_from < end_ts:

        safety_counter += 1

        if safety_counter > 10000:

            raise RuntimeError(
                f"Pagination safety stop for {symbol}"
            )

        url = (
            f"{BASE_URL}/"
            f"{TICK_TYPE}/"
            f"{symbol}/"
            f"{TIMEFRAME}"
        )

        params = {
            "from": current_from,
            "to": int(end_ts),
            "count": CANDLE_CHUNK,
        }

        data = get_json(
            url,
            params=params,
        )

        candles = data.get(
            "candles",
            []
        )

        if not candles:
            break

        parsed = []

        for candle in candles:

            try:

                timestamp = int(
                    candle["time"]
                )

                row = (
                    symbol,
                    TIMEFRAME,
                    timestamp,
                    float(candle["open"]),
                    float(candle["high"]),
                    float(candle["low"]),
                    float(candle["close"]),
                    float(candle["volume"]),
                )

                parsed.append(row)

            except Exception:
                continue

        if not parsed:
            break

        parsed.sort(
            key=lambda row: row[2]
        )

        all_rows.extend(
            parsed
        )

        last_timestamp_ms = parsed[-1][2]

        last_timestamp_sec = (
            last_timestamp_ms // 1000
        )

        next_from = (
            last_timestamp_sec + 1
        )

        if next_from <= current_from:
            break

        current_from = next_from

        last_time_text = datetime.fromtimestamp(
            last_timestamp_sec,
            tz=timezone.utc,
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

        print(
            f"    candles={len(all_rows):,} "
            f"through {last_time_text} UTC"
        )

        more_candles = bool(
            data.get(
                "more_candles",
                False,
            )
        )

        if not more_candles:
            break

        time.sleep(
            REQUEST_SLEEP
        )

    unique = {}

    for row in all_rows:

        unique[row[2]] = row

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda row: row[2]
    )

    print(
        f"    downloaded {len(result):,} candles"
    )

    return result


# ============================================================
# SAVE CANDLES
# ============================================================

def save_candles(
    conn,
    rows,
):

    if not rows:
        return

    cur = conn.cursor()

    cur.executemany(
        """
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
        """,
        rows,
    )

    conn.commit()


# ============================================================
# LOAD CANDLES
# ============================================================

def load_candles(
    conn,
    symbol,
    start_ts_ms,
    end_ts_ms,
):

    cur = conn.cursor()

    cur.execute(
        """
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
          AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (
            symbol,
            TIMEFRAME,
            start_ts_ms,
            end_ts_ms,
        ),
    )

    return cur.fetchall()


# ============================================================
# SAFE DIVISION
# ============================================================

def safe_div(
    a,
    b,
    default=0.0,
):

    if b == 0:
        return default

    return a / b


# ============================================================
# PERCENT CHANGE
# ============================================================

def pct_change(
    old,
    new,
):

    if old == 0:
        return 0.0

    return (
        (new - old)
        / old
        * 100.0
    )


# ============================================================
# SIGNAL DETECTION
# ============================================================

def detect_signal(
    candles,
    i,
):

    minimum_history = max(
        RVOL_LOOKBACK + 1,
        MOMENTUM_BARS + 1,
        BODY_LOOKBACK + 1,
        BREAKOUT_LOOKBACK + 1,
        RANGE_LOOKBACK + 1,
    )

    if i < minimum_history:
        return None

    current = candles[i]

    timestamp = current[0]
    open_price = current[1]
    high = current[2]
    low = current[3]
    close = current[4]
    volume = current[5]

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    previous_volumes = [
        candles[j][5]
        for j in range(
            i - RVOL_LOOKBACK,
            i,
        )
    ]

    avg_volume = statistics.mean(
        previous_volumes
    )

    rvol = safe_div(
        volume,
        avg_volume,
    )

    if rvol < MIN_RVOL:
        return None

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    reference_close = candles[
        i - MOMENTUM_BARS
    ][4]

    momentum_pct = pct_change(
        reference_close,
        close,
    )

    # --------------------------------------------------------
    # Candle body
    # --------------------------------------------------------

    candle_range = (
        high - low
    )

    if candle_range <= 0:
        return None

    body = abs(
        close - open_price
    )

    body_ratio = safe_div(
        body,
        candle_range,
    )

    if body_ratio < MIN_BODY_RATIO:
        return None

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    previous_highs = [
        candles[j][2]
        for j in range(
            i - BREAKOUT_LOOKBACK,
            i,
        )
    ]

    previous_lows = [
        candles[j][3]
        for j in range(
            i - BREAKOUT_LOOKBACK,
            i,
        )
    ]

    previous_high = max(
        previous_highs
    )

    previous_low = min(
        previous_lows
    )

    long_breakout = (
        close > previous_high
    )

    short_breakout = (
        close < previous_low
    )

    # --------------------------------------------------------
    # Range expansion
    # --------------------------------------------------------

    previous_ranges = [
        candles[j][2] - candles[j][3]
        for j in range(
            i - RANGE_LOOKBACK,
            i,
        )
    ]

    avg_range = statistics.mean(
        previous_ranges
    )

    range_expansion = safe_div(
        candle_range,
        avg_range,
    )

    if (
        range_expansion
        < MIN_RANGE_EXPANSION
    ):
        return None

    # --------------------------------------------------------
    # Scores
    # --------------------------------------------------------

    long_score = 0
    short_score = 0

    # Volume
    if rvol >= 3.0:

        long_score += 2
        short_score += 2

    if rvol >= 5.0:

        long_score += 1
        short_score += 1

    # Momentum
    if momentum_pct >= 1.0:

        long_score += 2

    if momentum_pct <= -1.0:

        short_score += 2

    if momentum_pct >= 2.0:

        long_score += 1

    if momentum_pct <= -2.0:

        short_score += 1

    # Candle direction
    if close > open_price:

        long_score += 1

    elif close < open_price:

        short_score += 1

    # Strong body
    if body_ratio >= 0.65:

        if close > open_price:

            long_score += 1

        elif close < open_price:

            short_score += 1

    # Breakout
    if long_breakout:

        long_score += 2

    if short_breakout:

        short_score += 2

    # Range expansion
    if range_expansion >= 1.5:

        if close > open_price:

            long_score += 1

        elif close < open_price:

            short_score += 1

    # --------------------------------------------------------
    # Final direction
    # --------------------------------------------------------

    if (
        long_score >= MIN_SCORE
        and momentum_pct >= MIN_MOMENTUM_PCT
        and close > open_price
    ):

        return {
            "side": "LONG",
            "score": long_score,
            "rvol": rvol,
            "momentum_pct": momentum_pct,
        }

    if (
        short_score >= MIN_SCORE
        and momentum_pct <= -MIN_MOMENTUM_PCT
        and close < open_price
    ):

        return {
            "side": "SHORT",
            "score": short_score,
            "rvol": rvol,
            "momentum_pct": momentum_pct,
        }

    return None


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    candles,
    signal_index,
    signal,
):

    entry_index = signal_index

    entry = candles[
        entry_index
    ][4]

    entry_time = candles[
        entry_index
    ][0]

    side = signal["side"]

    # --------------------------------------------------------
    # Validate RR
    # --------------------------------------------------------

    risk_pct = SL_PCT
    reward_pct = TP_PCT

    rr = safe_div(
        reward_pct,
        risk_pct,
    )

    if rr < MIN_RR:

        return None

    # --------------------------------------------------------
    # SL / TP
    # --------------------------------------------------------

    if side == "LONG":

        sl = (
            entry
            * (1.0 - SL_PCT / 100.0)
        )

        tp = (
            entry
            * (1.0 + TP_PCT / 100.0)
        )

    else:

        sl = (
            entry
            * (1.0 + SL_PCT / 100.0)
        )

        tp = (
            entry
            * (1.0 - TP_PCT / 100.0)
        )

    # --------------------------------------------------------
    # Future candles only
    # --------------------------------------------------------

    last_index = min(
        len(candles) - 1,
        entry_index + MAX_HOLD_BARS,
    )

    exit_price = None
    exit_time = None
    result = None
    hold_bars = 0

    for j in range(
        entry_index + 1,
        last_index + 1,
    ):

        candle = candles[j]

        timestamp = candle[0]
        high = candle[2]
        low = candle[3]

        hold_bars += 1

        if side == "LONG":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            # Conservative:
            # if both TP and SL occur in the
            # same candle, count SL first.

            if hit_sl:

                exit_price = sl
                exit_time = timestamp
                result = "LOSS"

                break

            if hit_tp:

                exit_price = tp
                exit_time = timestamp
                result = "WIN"

                break

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            if hit_sl:

                exit_price = sl
                exit_time = timestamp
                result = "LOSS"

                break

            if hit_tp:

                exit_price = tp
                exit_time = timestamp
                result = "WIN"

                break

    # --------------------------------------------------------
    # Time exit
    # --------------------------------------------------------

    if exit_price is None:

        exit_candle = candles[
            last_index
        ]

        exit_price = exit_candle[4]
        exit_time = exit_candle[0]

        result = "TIME"

    # --------------------------------------------------------
    # Gross return
    # --------------------------------------------------------

    if side == "LONG":

        gross_pct = pct_change(
            entry,
            exit_price,
        )

    else:

        gross_pct = pct_change(
            exit_price,
            entry,
        )

    # --------------------------------------------------------
    # Costs
    # --------------------------------------------------------

    net_pct = (
        gross_pct
        - TOTAL_ROUND_TRIP_COST_PCT
    )

    return {
        "side": side,
        "signal_time": entry_time,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry": entry,
        "exit": exit_price,
        "sl": sl,
        "tp": tp,
        "gross_pct": gross_pct,
        "cost_pct": TOTAL_ROUND_TRIP_COST_PCT,
        "net_pct": net_pct,
        "result": result,
        "hold_bars": hold_bars,
        "score": signal["score"],
        "rvol": signal["rvol"],
        "momentum_pct": signal["momentum_pct"],
    }


# ============================================================
# BACKTEST ONE MARKET
# ============================================================

def backtest_symbol(
    candles,
    symbol,
):

    trades = []

    if len(candles) < 300:

        return trades

    last_trade_index = -10000

    start_index = (
        max(
            RVOL_LOOKBACK,
            MOMENTUM_BARS,
            BODY_LOOKBACK,
            BREAKOUT_LOOKBACK,
            RANGE_LOOKBACK,
        )
        + 2
    )

    for i in range(
        start_index,
        len(candles) - 2,
    ):

        if (
            i - last_trade_index
            <= COOLDOWN_BARS
        ):

            continue

        signal = detect_signal(
            candles,
            i,
        )

        if signal is None:

            continue

        trade = simulate_trade(
            candles,
            i,
            signal,
        )

        if trade is None:

            continue

        trade["symbol"] = symbol

        trades.append(
            trade
        )

        last_trade_index = i

    return trades


# ============================================================
# SAVE TRADES
# ============================================================

def save_trades(
    conn,
    trades,
):

    if not trades:

        return

    cur = conn.cursor()

    for trade in trades:

        cur.execute(
            """
            INSERT INTO trades
            (
                symbol,
                side,
                signal_time,
                entry_time,
                exit_time,
                entry,
                exit,
                sl,
                tp,
                gross_pct,
                cost_pct,
                net_pct,
                result,
                hold_bars,
                score,
                rvol,
                momentum_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["symbol"],
                trade["side"],
                trade["signal_time"],
                trade["entry_time"],
                trade["exit_time"],
                trade["entry"],
                trade["exit"],
                trade["sl"],
                trade["tp"],
                trade["gross_pct"],
                trade["cost_pct"],
                trade["net_pct"],
                trade["result"],
                trade["hold_bars"],
                trade["score"],
                trade["rvol"],
                trade["momentum_pct"],
            ),
        )

    conn.commit()


# ============================================================
# EXPORT CSV
# ============================================================

def export_csv(
    conn,
):

    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            symbol,
            side,
            signal_time,
            entry_time,
            exit_time,
            entry,
            exit,
            sl,
            tp,
            gross_pct,
            cost_pct,
            net_pct,
            result,
            hold_bars,
            score,
            rvol,
            momentum_pct
        FROM trades
        ORDER BY exit_time ASC
        """
    )

    rows = cur.fetchall()

    headers = [
        "symbol",
        "side",
        "signal_time",
        "entry_time",
        "exit_time",
        "entry",
        "exit",
        "sl",
        "tp",
        "gross_pct",
        "cost_pct",
        "net_pct",
        "result",
        "hold_bars",
        "score",
        "rvol",
        "momentum_pct",
    ]

    with open(
        CSV_FILE,
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.writer(
            file
        )

        writer.writerow(
            headers
        )

        writer.writerows(
            rows
        )

    print(
        f"\nCSV exported: {CSV_FILE}"
    )


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    conn,
):

    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            net_pct,
            result,
            hold_bars,
            exit_time
        FROM trades
        ORDER BY exit_time ASC
        """
    )

    rows = cur.fetchall()

    if not rows:

        return None

    net_returns = [
        float(row[0])
        for row in rows
    ]

    wins = [
        value
        for value in net_returns
        if value > 0
    ]

    losses = [
        value
        for value in net_returns
        if value <= 0
    ]

    total = len(
        net_returns
    )

    win_count = len(
        wins
    )

    loss_count = len(
        losses
    )

    win_rate = (
        win_count
        / total
        * 100.0
    )

    gross_profit = sum(
        wins
    )

    gross_loss = abs(
        sum(losses)
    )

    profit_factor = safe_div(
        gross_profit,
        gross_loss,
        default=math.inf,
    )

    net_pnl = sum(
        net_returns
    )

    average_trade = (
        net_pnl
        / total
    )

    average_win = (
        statistics.mean(wins)
        if wins
        else 0.0
    )

    average_loss = (
        statistics.mean(losses)
        if losses
        else 0.0
    )

    expectancy = average_trade

    # --------------------------------------------------------
    # Additive equity drawdown
    # --------------------------------------------------------

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for value in net_returns:

        equity += value

        if equity > peak:

            peak = equity

        drawdown = (
            peak - equity
        )

        if drawdown > max_drawdown:

            max_drawdown = drawdown

    best_trade = max(
        net_returns
    )

    worst_trade = min(
        net_returns
    )

    average_hold = statistics.mean(
        row[2]
        for row in rows
    )

    time_exits = sum(
        1
        for row in rows
        if row[1] == "TIME"
    )

    return {
        "total": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "net_pnl": net_pnl,
        "average_trade": average_trade,
        "average_win": average_win,
        "average_loss": average_loss,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "avg_hold_bars": average_hold,
        "time_exits": time_exits,
    }


# ============================================================
# MARKET SUMMARY
# ============================================================

def print_market_summary(
    conn,
):

    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            symbol,
            COUNT(*) AS trades,
            SUM(
                CASE
                    WHEN net_pct > 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(net_pct) AS pnl
        FROM trades
        GROUP BY symbol
        ORDER BY pnl DESC
        """
    )

    rows = cur.fetchall()

    if not rows:

        return

    print()
    print("=" * 75)
    print("MARKET SUMMARY")
    print("=" * 75)

    print(
        f"{'SYMBOL':<18}"
        f"{'TRADES':>8}"
        f"{'WINS':>8}"
        f"{'WR%':>9}"
        f"{'NET P&L%':>13}"
    )

    print("-" * 75)

    for symbol, trades, wins, pnl in rows:

        if trades:

            win_rate = (
                wins
                / trades
                * 100.0
            )

        else:

            win_rate = 0.0

        print(
            f"{symbol:<18}"
            f"{trades:>8}"
            f"{wins:>8}"
            f"{win_rate:>8.2f}%"
            f"{pnl:>12.2f}%"
        )


# ============================================================
# FINAL REPORT
# ============================================================

def print_final_report(
    performance,
):

    print()
    print("=" * 75)
    print("PUMP DETECTOR BACKTEST RESULT")
    print("=" * 75)

    if performance is None:

        print()
        print("NO TRADES FOUND.")
        return

    profit_factor = (
        performance["profit_factor"]
    )

    if math.isinf(
        profit_factor
    ):

        pf_text = "INF"

    else:

        pf_text = (
            f"{profit_factor:.3f}"
        )

    rr = safe_div(
        TP_PCT,
        SL_PCT,
    )

    print()
    print(
        f"Version             : {VERSION}"
    )

    print(
        f"Timeframe           : {TIMEFRAME}"
    )

    print(
        f"Backtest period     : {BACKTEST_DAYS} days"
    )

    print(
        f"TP                  : {TP_PCT:.2f}%"
    )

    print(
        f"SL                  : {SL_PCT:.2f}%"
    )

    print(
        f"RR                  : {rr:.2f}"
    )

    print(
        f"Trading cost        : "
        f"{TOTAL_ROUND_TRIP_COST_PCT:.2f}%"
    )

    print()
    print("---------------- PERFORMANCE ----------------")

    print(
        f"Total trades        : "
        f"{performance['total']}"
    )

    print(
        f"Wins                : "
        f"{performance['wins']}"
    )

    print(
        f"Losses              : "
        f"{performance['losses']}"
    )

    print(
        f"Time exits          : "
        f"{performance['time_exits']}"
    )

    print(
        f"Win rate            : "
        f"{performance['win_rate']:.2f}%"
    )

    print(
        f"Profit factor       : "
        f"{pf_text}"
    )

    print(
        f"Net P&L             : "
        f"{performance['net_pnl']:.2f}%"
    )

    print(
        f"Average trade       : "
        f"{performance['average_trade']:.3f}%"
    )

    print(
        f"Average win         : "
        f"{performance['average_win']:.3f}%"
    )

    print(
        f"Average loss        : "
        f"{performance['average_loss']:.3f}%"
    )

    print(
        f"Expectancy          : "
        f"{performance['expectancy']:.3f}%"
    )

    print(
        f"Max drawdown        : "
        f"{performance['max_drawdown']:.2f}%"
    )

    print(
        f"Best trade          : "
        f"{performance['best_trade']:.3f}%"
    )

    print(
        f"Worst trade         : "
        f"{performance['worst_trade']:.3f}%"
    )

    print(
        f"Avg hold            : "
        f"{performance['avg_hold_bars']:.2f} candles"
    )

    print()
    print("IMPORTANT:")
    print(
        "Net P&L is the sum of individual "
        "trade returns."
    )

    print(
        "It is NOT a compounded portfolio return."
    )

    print(
        "No real orders were sent."
    )

    print("=" * 75)


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    print()
    print("=" * 75)
    print("KRAKEN FUTURES PUMP DETECTOR")
    print(f"VERSION {VERSION}")
    print("=" * 75)

    print()
    print("MODE: BACKTEST ONLY")
    print("REAL TRADING: DISABLED")

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    conn = init_db()

    # --------------------------------------------------------
    # Date range
    # --------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    end_dt = now

    start_dt = (
        now
        - timedelta(
            days=(
                BACKTEST_DAYS
                + WARMUP_DAYS
            )
        )
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    print()
    print("DATA RANGE")
    print(
        f"From: "
        f"{start_dt.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    print(
        f"To  : "
        f"{end_dt.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    # --------------------------------------------------------
    # Discover markets
    # --------------------------------------------------------

    markets = discover_markets()

    # --------------------------------------------------------
    # Rank markets
    # --------------------------------------------------------

    selected_markets = (
        rank_markets_by_volume(
            markets
        )
    )

    print()
    print(
        f"Markets to backtest: "
        f"{len(selected_markets)}"
    )

    # --------------------------------------------------------
    # Clear previous trades
    # --------------------------------------------------------

    conn.execute(
        "DELETE FROM trades"
    )

    conn.commit()

    # --------------------------------------------------------
    # Process markets
    # --------------------------------------------------------

    successful = 0
    failed = 0
    total_trades = 0

    for number, symbol in enumerate(
        selected_markets,
        start=1,
    ):

        print()
        print("-" * 75)

        print(
            f"[{number}/{len(selected_markets)}] "
            f"{symbol}"
        )

        try:

            rows = download_candles(
                symbol,
                start_ts,
                end_ts,
            )

            if not rows:

                print(
                    "    No candle data."
                )

                failed += 1
                continue

            save_candles(
                conn,
                rows,
            )

            candles = load_candles(
                conn,
                symbol,
                start_ts * 1000,
                end_ts * 1000,
            )

            if len(candles) < 300:

                print(
                    "    Not enough candles."
                )

                failed += 1
                continue

            trades = backtest_symbol(
                candles,
                symbol,
            )

            save_trades(
                conn,
                trades,
            )

            total_trades += len(
                trades
            )

            successful += 1

            print(
                f"    Trades found: "
                f"{len(trades)}"
            )

        except Exception as exc:

            failed += 1

            print(
                f"    ERROR: {exc}"
            )

        time.sleep(
            REQUEST_SLEEP
        )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    performance = (
        calculate_performance(
            conn
        )
    )

    # --------------------------------------------------------
    # Reports
    # --------------------------------------------------------

    print()
    print("=" * 75)
    print("DOWNLOAD / BACKTEST SUMMARY")
    print("=" * 75)

    print(
        f"Markets selected   : "
        f"{len(selected_markets)}"
    )

    print(
        f"Markets successful : "
        f"{successful}"
    )

    print(
        f"Markets failed     : "
        f"{failed}"
    )

    print(
        f"Total trades       : "
        f"{total_trades}"
    )

    print_final_report(
        performance
    )

    print_market_summary(
        conn
    )

    export_csv(
        conn
    )

    conn.close()

    elapsed_minutes = (
        time.time()
        - started
    ) / 60.0

    print()
    print("=" * 75)

    print(
        f"Completed in "
        f"{elapsed_minutes:.2f} minutes"
    )

    print(
        f"Database: {DB_FILE}"
    )

    print(
        f"CSV     : {CSV_FILE}"
    )

    print("=" * 75)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
