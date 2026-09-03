# ============================================================
# CRYPTO DIVERGENCE SCANNER v8.4
# Kraken Futures
# 5M CLOSED CANDLES
#
# Strategy:
#   BUY  = Regular Bullish RSI Divergence + Descending
#          Trendline Breakout
#
#   SELL = Regular Bearish RSI Divergence + Ascending
#          Trendline Breakdown
#
# Features:
#   - 30 coins
#   - 5M timeframe
#   - Regular divergence only
#   - Trendline breakout/breakdown
#   - Swing-based SL
#   - Nearest S/R TP
#   - Minimum TP distance: 0.30%
#   - Persistent trade history
#   - Cumulative statistics
#   - Telegram
# ============================================================

import os
import time
import json
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

STATE_FILE = "divergence_state_v8.json"

CANDLE_LIMIT = 500
MAX_WORKERS = 15
TOP_SIGNAL_LIMIT = 10

RSI_PERIOD = 14

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MAX_PIVOT_GAP = 60

MIN_PRICE_DIFF_PERCENT = 0.05
MIN_RSI_DIFF = 2.0

MAX_DIVERGENCE_AGE_MINUTES = 120

SL_BUFFER_PERCENT = 0.10

MIN_TP_DISTANCE_PERCENT = 0.30


# ============================================================
# COINS
# ============================================================

COINS = {
    "BTC": "pf_xbtusd",
    "ETH": "pf_ethusd",
    "BNB": "pf_bnbusd",
    "SOL": "pf_solusd",
    "XRP": "pf_xrpusd",
    "DOGE": "pf_dogeusd",
    "ADA": "pf_adausd",
    "AVAX": "pf_avaxusd",
    "LINK": "pf_linkusd",
    "DOT": "pf_dotusd",
    "TRX": "pf_trxusd",
    "LTC": "pf_ltcusd",
    "BCH": "pf_bchusd",
    "ATOM": "pf_atomusd",
    "UNI": "pf_uniusd",
    "ETC": "pf_etcusd",
    "XLM": "pf_xlmusd",
    "NEAR": "pf_nearusd",
    "APT": "pf_aptusd",
    "FIL": "pf_filusd",
    "ARB": "pf_arbusd",
    "OP": "pf_opusd",
    "SUI": "pf_suiusd",
    "INJ": "pf_injusd",
    "AAVE": "pf_aaveusd",
    "MKR": "pf_mkrusd",
    "ALGO": "pf_algousd",
    "VET": "pf_vetusd",
    "SEI": "pf_seiusd",
    "TIA": "pf_tiausd",
}


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        print("Telegram HTTP:", response.status_code)

        if response.status_code != 200:
            print(response.text)

        return response.status_code == 200

    except Exception as e:

        print("Telegram error:", e)

        return False


# ============================================================
# STATE
# ============================================================

def load_state():

    default_state = {
        "version": 4,
        "trades": [],
        "last_scan": None
    }

    if not os.path.exists(STATE_FILE):
        return default_state

    try:

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return default_state

        if "trades" not in state:
            state["trades"] = []

        if "version" not in state:
            state["version"] = 4

        return state

    except Exception as e:

        print("State load error:", e)

        return default_state


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, STATE_FILE)


# ============================================================
# RSI
# ============================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


# ============================================================
# KRAKEN DATA
# ============================================================

def get_candles(symbol, limit=CANDLE_LIMIT):

    url = f"{BASE_URL}/trade/{symbol}/5m"

    try:

        response = requests.get(
            url,
            params={"count": limit},
            headers={"Accept": "application/json"},
            timeout=20
        )

        if response.status_code != 200:

            return None, (
                f"HTTP {response.status_code}"
            )

        data = response.json()

        candles = data.get("candles")

        if not candles:

            return None, "No candles returned"

        rows = []

        for c in candles:

            if isinstance(c, dict):

                rows.append({
                    "time": c.get("time"),
                    "open": c.get("open"),
                    "high": c.get("high"),
                    "low": c.get("low"),
                    "close": c.get("close"),
                    "volume": c.get("volume")
                })

            elif isinstance(c, list):

                if len(c) >= 6:

                    rows.append({
                        "time": c[0],
                        "open": c[1],
                        "high": c[2],
                        "low": c[3],
                        "close": c[4],
                        "volume": c[5]
                    })

        if not rows:

            return None, "Invalid candle format"

        df = pd.DataFrame(rows)

        required = [
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for col in required:

            if col not in df.columns:
                return None, f"Missing column: {col}"

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df["time"] = pd.to_datetime(
            df["time"],
            unit="ms",
            utc=True,
            errors="coerce"
        )

        df = df.dropna()

        df = df.sort_values("time")

        df = df.drop_duplicates(
            subset=["time"]
        )

        # Remove currently forming candle
        now = pd.Timestamp.now(tz="UTC")

        current_bucket = now.floor("5min")

        df = df[
            df["time"] < current_bucket
        ]

        if len(df) < 100:

            return None, (
                f"Only {len(df)} closed candles"
            )

        return df.reset_index(drop=True), None

    except Exception as e:

        return None, str(e)


# ============================================================
# PIVOTS
# ============================================================

def pivot_lows(df):

    lows = df["low"].values

    pivots = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left = lows[
            i - PIVOT_LEFT:i
        ]

        right = lows[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        if (
            lows[i] <= left.min()
            and lows[i] <= right.min()
        ):

            pivots.append(i)

    return pivots


def pivot_highs(df):

    highs = df["high"].values

    pivots = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left = highs[
            i - PIVOT_LEFT:i
        ]

        right = highs[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        if (
            highs[i] >= left.max()
            and highs[i] >= right.max()
        ):

            pivots.append(i)

    return pivots


# ============================================================
# REGULAR BULLISH DIVERGENCE
# ============================================================

def find_bullish_divergence(df):

    pivots = pivot_lows(df)

    if len(pivots) < 2:
        return None

    rsi = df["rsi"]

    for j in range(
        len(pivots) - 1,
        0,
        -1
    ):

        p1 = pivots[j - 1]
        p2 = pivots[j]

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        price1 = df.iloc[p1]["low"]
        price2 = df.iloc[p2]["low"]

        rsi1 = rsi.iloc[p1]
        rsi2 = rsi.iloc[p2]

        price_change = (
            (price2 - price1)
            / price1
            * 100
        )

        # Regular bullish:
        # Price lower low
        # RSI higher low

        if (
            price_change <= -MIN_PRICE_DIFF_PERCENT
            and
            rsi2 - rsi1 >= MIN_RSI_DIFF
        ):

            return {
                "type": "BULLISH",
                "p1": p1,
                "p2": p2,
                "price1": price1,
                "price2": price2,
                "rsi1": rsi1,
                "rsi2": rsi2
            }

    return None


# ============================================================
# REGULAR BEARISH DIVERGENCE
# ============================================================

def find_bearish_divergence(df):

    pivots = pivot_highs(df)

    if len(pivots) < 2:
        return None

    rsi = df["rsi"]

    for j in range(
        len(pivots) - 1,
        0,
        -1
    ):

        p1 = pivots[j - 1]
        p2 = pivots[j]

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        price1 = df.iloc[p1]["high"]
        price2 = df.iloc[p2]["high"]

        rsi1 = rsi.iloc[p1]
        rsi2 = rsi.iloc[p2]

        price_change = (
            (price2 - price1)
            / price1
            * 100
        )

        # Regular bearish:
        # Price higher high
        # RSI lower high

        if (
            price_change >= MIN_PRICE_DIFF_PERCENT
            and
            rsi1 - rsi2 >= MIN_RSI_DIFF
        ):

            return {
                "type": "BEARISH",
                "p1": p1,
                "p2": p2,
                "price1": price1,
                "price2": price2,
                "rsi1": rsi1,
                "rsi2": rsi2
            }

    return None


# ============================================================
# DESCENDING TRENDLINE BREAK
# ============================================================

def descending_trendline_break(df):

    pivots = pivot_highs(df)

    if len(pivots) < 2:
        return None

    closes = df["close"].values

    last_index = len(df) - 1

    for j in range(
        len(pivots) - 1,
        0,
        -1
    ):

        p1 = pivots[j - 1]
        p2 = pivots[j]

        if p2 <= p1:
            continue

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        h1 = df.iloc[p1]["high"]
        h2 = df.iloc[p2]["high"]

        # Descending highs
        if h2 >= h1:
            continue

        slope = (
            h2 - h1
        ) / (
            p2 - p1
        )

        prev_i = last_index - 1
        curr_i = last_index

        line_prev = h2 + slope * (
            prev_i - p2
        )

        line_curr = h2 + slope * (
            curr_i - p2
        )

        if (
            closes[prev_i] <= line_prev
            and
            closes[curr_i] > line_curr
        ):

            return {
                "p1": p1,
                "p2": p2,
                "line": line_curr
            }

    return None


# ============================================================
# ASCENDING TRENDLINE BREAK
# ============================================================

def ascending_trendline_break(df):

    pivots = pivot_lows(df)

    if len(pivots) < 2:
        return None

    closes = df["close"].values

    last_index = len(df) - 1

    for j in range(
        len(pivots) - 1,
        0,
        -1
    ):

        p1 = pivots[j - 1]
        p2 = pivots[j]

        if p2 <= p1:
            continue

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        l1 = df.iloc[p1]["low"]
        l2 = df.iloc[p2]["low"]

        # Ascending lows
        if l2 <= l1:
            continue

        slope = (
            l2 - l1
        ) / (
            p2 - p1
        )

        prev_i = last_index - 1
        curr_i = last_index

        line_prev = l2 + slope * (
            prev_i - p2
        )

        line_curr = l2 + slope * (
            curr_i - p2
        )

        if (
            closes[prev_i] >= line_prev
            and
            closes[curr_i] < line_curr
        ):

            return {
                "p1": p1,
                "p2": p2,
                "line": line_curr
            }

    return None


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def nearest_resistance(df, entry):

    pivots = pivot_highs(df)

    candidates = []

    for p in pivots:

        price = df.iloc[p]["high"]

        if price <= entry:
            continue

        distance = (
            (price - entry)
            / entry
            * 100
        )

        if distance >= MIN_TP_DISTANCE_PERCENT:

            candidates.append(price)

    if not candidates:
        return None

    return min(candidates)


def nearest_support(df, entry):

    pivots = pivot_lows(df)

    candidates = []

    for p in pivots:

        price = df.iloc[p]["low"]

        if price >= entry:
            continue

        distance = (
            (entry - price)
            / entry
            * 100
        )

        if distance >= MIN_TP_DISTANCE_PERCENT:

            candidates.append(price)

    if not candidates:
        return None

    return max(candidates)


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(name, symbol):

    df, error = get_candles(symbol)

    if df is None:

        return {
            "status": "DATA_ERROR",
            "name": name,
            "symbol": symbol,
            "error": error
        }

    try:

        df["rsi"] = calculate_rsi(
            df["close"],
            RSI_PERIOD
        )

        bullish = find_bullish_divergence(df)

        bearish = find_bearish_divergence(df)

        down_break = (
            descending_trendline_break(df)
        )

        up_break = (
            ascending_trendline_break(df)
        )

        last = df.iloc[-1]

        entry = float(last["close"])

        signal_time = last["time"]

        candidates = []

        # ====================================================
        # BUY
        # ====================================================

        if bullish and down_break:

            div_index = bullish["p2"]

            break_index = len(df) - 1

            age_bars = (
                break_index - div_index
            )

            age_minutes = age_bars * 5

            if (
                0 <= age_minutes
                <= MAX_DIVERGENCE_AGE_MINUTES
            ):

                swing_low = df.iloc[
                    bullish["p2"]
                ]["low"]

                sl = (
                    swing_low
                    * (
                        1
                        - SL_BUFFER_PERCENT / 100
                    )
                )

                tp = nearest_resistance(
                    df,
                    entry
                )

                if tp is not None:

                    risk = entry - sl
                    reward = tp - entry

                    if risk > 0:

                        rr = reward / risk

                        candidates.append({

                            "direction": "BUY",
                            "name": name,
                            "symbol": symbol,
                            "entry": entry,
                            "sl": float(sl),
                            "tp": float(tp),
                            "rr": float(rr),
                            "signal_time": signal_time.isoformat(),
                            "swing_time": df.iloc[
                                bullish["p2"]
                            ]["time"].isoformat(),
                            "divergence": "REGULAR BULLISH",
                            "trendline": "DESCENDING BREAKOUT",
                            "rsi": float(last["rsi"]),
                            "div_age_minutes": age_minutes
                        })

        # ====================================================
        # SELL
        # ====================================================

        if bearish and up_break:

            div_index = bearish["p2"]

            break_index = len(df) - 1

            age_bars = (
                break_index - div_index
            )

            age_minutes = age_bars * 5

            if (
                0 <= age_minutes
                <= MAX_DIVERGENCE_AGE_MINUTES
            ):

                swing_high = df.iloc[
                    bearish["p2"]
                ]["high"]

                sl = (
                    swing_high
                    * (
                        1
                        + SL_BUFFER_PERCENT / 100
                    )
                )

                tp = nearest_support(
                    df,
                    entry
                )

                if tp is not None:

                    risk = sl - entry
                    reward = entry - tp

                    if risk > 0:

                        rr = reward / risk

                        candidates.append({

                            "direction": "SELL",
                            "name": name,
                            "symbol": symbol,
                            "entry": entry,
                            "sl": float(sl),
                            "tp": float(tp),
                            "rr": float(rr),
                            "signal_time": signal_time.isoformat(),
                            "swing_time": df.iloc[
                                bearish["p2"]
                            ]["time"].isoformat(),
                            "divergence": "REGULAR BEARISH",
                            "trendline": "ASCENDING BREAKDOWN",
                            "rsi": float(last["rsi"]),
                            "div_age_minutes": age_minutes
                        })

        if not candidates:

            return {
                "status": "OK",
                "name": name,
                "symbol": symbol,
                "signal": None,
                "price": entry,
                "rsi": float(last["rsi"])
            }

        # Normally only one direction should exist.
        signal = candidates[0]

        return {
            "status": "OK",
            "name": name,
            "symbol": symbol,
            "signal": signal,
            "price": entry,
            "rsi": float(last["rsi"])
        }

    except Exception as e:

        return {
            "status": "ANALYSIS_ERROR",
            "name": name,
            "symbol": symbol,
            "error": str(e)
        }


# ============================================================
# TRADE KEY
# ============================================================

def trade_key(signal):

    return (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['signal_time']}_"
        f"{signal['swing_time']}"
    )


# ============================================================
# ADD NEW TRADE
# ============================================================

def add_new_trade(state, signal):

    key = trade_key(signal)

    for trade in state["trades"]:

        if trade.get("key") == key:
            return False

    trade = {
        "key": key,
        "symbol": signal["symbol"],
        "name": signal["name"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "rr": signal["rr"],
        "signal_time": signal["signal_time"],
        "swing_time": signal["swing_time"],
        "divergence": signal["divergence"],
        "trendline": signal["trendline"],
        "rsi": signal["rsi"],
        "div_age_minutes": signal["div_age_minutes"],
        "status": "OPEN",
        "open_time": datetime.now(
            timezone.utc
        ).isoformat()
    }

    state["trades"].append(trade)

    return True


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(state):

    open_trades = [
        t for t in state["trades"]
        if t.get("status") == "OPEN"
    ]

    if not open_trades:
        return 0

    closed_count = 0

    cache = {}

    for trade in open_trades:

        symbol = trade["symbol"]

        if symbol not in cache:

            df, error = get_candles(
                symbol,
                limit=20
            )

            cache[symbol] = (
                df,
                error
            )

        df, error = cache[symbol]

        if df is None:
            continue

        if len(df) == 0:
            continue

        candle = df.iloc[-1]

        high = float(candle["high"])
        low = float(candle["low"])

        entry = float(trade["entry"])
        sl = float(trade["sl"])
        tp = float(trade["tp"])

        direction = trade["direction"]

        result = None
        close_price = None

        # ====================================================
        # BUY
        # ====================================================

        if direction == "BUY":

            hit_sl = low <= sl
            hit_tp = high >= tp

            # Conservative:
            # if both touched in same candle,
            # SL is considered first.

            if hit_sl:

                result = "LOSS"
                close_price = sl

            elif hit_tp:

                result = "WIN"
                close_price = tp

        # ====================================================
        # SELL
        # ====================================================

        elif direction == "SELL":

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl:

                result = "LOSS"
                close_price = sl

            elif hit_tp:

                result = "WIN"
                close_price = tp

        if result:

            trade["status"] = "CLOSED"

            trade["result"] = result

            trade["close_price"] = close_price

            trade["close_time"] = (
                candle["time"].isoformat()
            )

            closed_count += 1

    return closed_count


# ============================================================
# CUMULATIVE STATISTICS
# ============================================================

def get_statistics(state):

    # IMPORTANT:
    # Statistics are calculated from ALL historical trades.
    # Nothing is reset between scanner executions.

    trades = state.get(
        "trades",
        []
    )

    total = len(trades)

    open_trades = [
        t for t in trades
        if t.get("status") == "OPEN"
    ]

    closed_trades = [
        t for t in trades
        if t.get("status") == "CLOSED"
    ]

    wins = [
        t for t in closed_trades
        if t.get("result") == "WIN"
    ]

    losses = [
        t for t in closed_trades
        if t.get("result") == "LOSS"
    ]

    buy_trades = [
        t for t in trades
        if t.get("direction") == "BUY"
    ]

    sell_trades = [
        t for t in trades
        if t.get("direction") == "SELL"
    ]

    buy_wins = [
        t for t in buy_trades
        if t.get("result") == "WIN"
    ]

    buy_losses = [
        t for t in buy_trades
        if t.get("result") == "LOSS"
    ]

    sell_wins = [
        t for t in sell_trades
        if t.get("result") == "WIN"
    ]

    sell_losses = [
        t for t in sell_trades
        if t.get("result") == "LOSS"
    ]

    closed_count = len(closed_trades)

    win_rate = (
        len(wins) / closed_count * 100
        if closed_count
        else 0
    )

    buy_closed = (
        len(buy_wins)
        + len(buy_losses)
    )

    sell_closed = (
        len(sell_wins)
        + len(sell_losses)
    )

    buy_win_rate = (
        len(buy_wins)
        / buy_closed
        * 100
        if buy_closed
        else 0
    )

    sell_win_rate = (
        len(sell_wins)
        / sell_closed
        * 100
        if sell_closed
        else 0
    )

    return {

        "total": total,

        "open": len(open_trades),

        "closed": closed_count,

        "wins": len(wins),

        "losses": len(losses),

        "win_rate": win_rate,

        "buy_total": len(buy_trades),

        "buy_wins": len(buy_wins),

        "buy_losses": len(buy_losses),

        "buy_win_rate": buy_win_rate,

        "sell_total": len(sell_trades),

        "sell_wins": len(sell_wins),

        "sell_losses": len(sell_losses),

        "sell_win_rate": sell_win_rate,
    }


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "BUY"
        else "🔴"
    )

    return (
        f"{emoji} <b>{signal['name']}/USD "
        f"{direction}</b>\n"
        f"Entry: <code>{signal['entry']:.8g}</code>\n"
        f"SL: <code>{signal['sl']:.8g}</code>\n"
        f"TP: <code>{signal['tp']:.8g}</code>\n"
        f"RR: <b>1:{signal['rr']:.2f}</b>\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"📌 {signal['divergence']}\n"
        f"📐 {signal['trendline']}\n"
        f"⏱ Divergence age: "
        f"{signal['div_age_minutes']}m"
    )


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    results,
    new_signals,
    state,
    closed_count
):

    data_ok = sum(
        1
        for r in results
        if r.get("status") == "OK"
    )

    data_errors = [
        r for r in results
        if r.get("status") == "DATA_ERROR"
    ]

    analysis_errors = [
        r for r in results
        if r.get("status") == "ANALYSIS_ERROR"
    ]

    stats = get_statistics(state)

    lines = []

    lines.append(
        "📡 <b>CRYPTO DIVERGENCE "
        "SCANNER v8.4</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 {datetime.now(timezone.utc)"
        f".strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        f"📊 DATA OK: {data_ok}/{len(COINS)}"
    )

    lines.append(
        f"⚠️ DATA ERROR: {len(data_errors)}"
    )

    lines.append(
        f"⚠️ ANALYSIS ERROR: "
        f"{len(analysis_errors)}"
    )

    lines.append("")

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    if new_signals:

        lines.append(
            "🚨 <b>NEW SIGNALS</b>"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for signal in new_signals[
            :TOP_SIGNAL_LIMIT
        ]:

            lines.append(
                format_signal(signal)
            )

            lines.append("")

    else:

        lines.append(
            "👀 <b>NO NEW SIGNAL</b>"
        )

        lines.append(
            "Regular divergence + "
            "trendline break conditions "
            "not confirmed."
        )

        lines.append("")

    # ========================================================
    # CLOSED TRADES THIS RUN
    # ========================================================

    if closed_count:

        lines.append(
            f"🔔 Closed this scan: "
            f"<b>{closed_count}</b>"
        )

        lines.append("")

    # ========================================================
    # CUMULATIVE STATISTICS
    # ========================================================

    lines.append(
        "📊 <b>CUMULATIVE TRADE "
        "STATISTICS</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"Total Trades: "
        f"<b>{stats['total']}</b>"
    )

    lines.append(
        f"🟢 Open: "
        f"<b>{stats['open']}</b>"
    )

    lines.append(
        f"🔒 Closed: "
        f"<b>{stats['closed']}</b>"
    )

    lines.append("")

    lines.append(
        f"✅ Wins: "
        f"<b>{stats['wins']}</b>"
    )

    lines.append(
        f"❌ Losses: "
        f"<b>{stats['losses']}</b>"
    )

    lines.append(
        f"🎯 Win Rate: "
        f"<b>{stats['win_rate']:.1f}%</b>"
    )

    lines.append("")

    lines.append(
        "🟢 <b>BUY</b>"
    )

    lines.append(
        f"Total: {stats['buy_total']} | "
        f"Wins: {stats['buy_wins']} | "
        f"Losses: {stats['buy_losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['buy_win_rate']:.1f}%"
    )

    lines.append("")

    lines.append(
        "🔴 <b>SELL</b>"
    )

    lines.append(
        f"Total: {stats['sell_total']} | "
        f"Wins: {stats['sell_wins']} | "
        f"Losses: {stats['sell_losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['sell_win_rate']:.1f}%"
    )

    # ========================================================
    # DATA ERRORS
    # ========================================================

    if data_errors:

        lines.append("")

        lines.append(
            "⚠️ <b>DATA PROBLEMS</b>"
        )

        for r in data_errors[:10]:

            lines.append(
                f"• {r['name']}: "
                f"{r.get('error', 'Unknown')}"
            )

    # ========================================================
    # ANALYSIS ERRORS
    # ========================================================

    if analysis_errors:

        lines.append("")

        lines.append(
            "⚠️ <b>ANALYSIS PROBLEMS</b>"
        )

        for r in analysis_errors[:10]:

            lines.append(
                f"• {r['name']}: "
                f"{r.get('error', 'Unknown')}"
            )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "CRYPTO DIVERGENCE SCANNER v8.4"
    )

    print("=" * 60)

    state = load_state()

    print(
        "Historical trades:",
        len(state.get("trades", []))
    )

    # ========================================================
    # UPDATE OLD OPEN TRADES FIRST
    # ========================================================

    closed_count = update_open_trades(
        state
    )

    if closed_count:

        print(
            "Closed trades:",
            closed_count
        )

    # ========================================================
    # ANALYZE ALL COINS
    # ========================================================

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_coin,
                name,
                symbol
            ): name

            for name, symbol
            in COINS.items()
        }

        for future in as_completed(
            futures
        ):

            name = futures[future]

            try:

                result = future.result()

                results.append(result)

            except Exception as e:

                results.append({
                    "status": "ANALYSIS_ERROR",
                    "name": name,
                    "symbol": COINS[name],
                    "error": str(e)
                })

    # Keep deterministic order
    results.sort(
        key=lambda x: x["name"]
    )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    new_signals = []

    for result in results:

        if result.get("signal"):

            signal = result["signal"]

            added = add_new_trade(
                state,
                signal
            )

            if added:

                new_signals.append(
                    signal
                )

                print(
                    "NEW SIGNAL:",
                    signal["name"],
                    signal["direction"]
                )

    # ========================================================
    # SAVE STATE
    # ========================================================

    state["last_scan"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    save_state(state)

    # ========================================================
    # STATISTICS
    # ========================================================

    stats = get_statistics(state)

    print("")
    print("CUMULATIVE STATISTICS")
    print("---------------------")
    print(
        "Total:",
        stats["total"]
    )
    print(
        "Open:",
        stats["open"]
    )
    print(
        "Closed:",
        stats["closed"]
    )
    print(
        "Wins:",
        stats["wins"]
    )
    print(
        "Losses:",
        stats["losses"]
    )
    print(
        "Win Rate:",
        f"{stats['win_rate']:.1f}%"
    )

    print("")
    print(
        "TOTAL COINS:",
        len(COINS)
    )

    print(
        "DATA ERRORS:",
        sum(
            1
            for r in results
            if r.get("status")
            == "DATA_ERROR"
        )
    )

    print(
        "ANALYSIS ERRORS:",
        sum(
            1
            for r in results
            if r.get("status")
            == "ANALYSIS_ERROR"
        )
    )

    print(
        "SIGNALS:",
        len(new_signals)
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    report = format_report(
        results,
        new_signals,
        state,
        closed_count
    )

    send_telegram(report)

    print("")
    print("SCAN COMPLETE")


if __name__ == "__main__":
    main()
