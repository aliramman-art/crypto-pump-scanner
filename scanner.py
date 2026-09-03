import os
import time
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CRYPTO DIVERGENCE + TRENDLINE SCANNER v8.2
# ============================================================

print("=" * 46)
print("CRYPTO DIVERGENCE + TRENDLINE SCANNER v8.2")
print("=" * 46)
print("30 Coins")
print("5M Timeframe")
print("Regular Divergence ONLY")
print("Trendline Break")
print("SL Buffer: 0.1%")
print("Minimum TP: 0.3%")
print("Ichimoku: OFF")
print()


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "divergence_state_v8.json"

TIMEFRAME = "5m"

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
# 30 COINS
# ============================================================

SYMBOLS = {
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
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {
            "trades": [],
            "last_scan": 0
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {
                "trades": [],
                "last_scan": 0
            }

        if "trades" not in data:
            data["trades"] = []

        return data

    except Exception as e:

        print("STATE LOAD ERROR:", e)

        return {
            "trades": [],
            "last_scan": 0
        }


def save_state(state):

    try:

        temp_file = STATE_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_file, STATE_FILE)

    except Exception as e:

        print("STATE SAVE ERROR:", e)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("Telegram secrets missing")

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=15
        )

        print("Telegram HTTP:", r.status_code)

        return r.status_code == 200

    except Exception as e:

        print("Telegram ERROR:", e)

        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def get_candles(symbol, limit=CANDLE_LIMIT):

    url = f"{BASE_URL}/trade/{symbol}/5m"

    try:

        r = requests.get(
            url,
            params={"count": limit},
            timeout=15,
            headers={
                "Accept": "application/json"
            }
        )

        print(f"KRAKEN {symbol} HTTP={r.status_code}")

        if r.status_code != 200:

            print(
                f"{symbol} => HTTP_ERROR "
                f"{r.status_code}"
            )

            return None

        try:
            data = r.json()

        except Exception as e:

            print(
                f"{symbol} => JSON_ERROR: {e}"
            )

            return None

        # ----------------------------------------------------
        # Kraken Futures current response
        # ----------------------------------------------------

        candles = data.get("candles")

        if not isinstance(candles, list):

            print(
                f"{symbol} => INVALID_RESPONSE"
            )

            print(
                str(data)[:500]
            )

            return None

        if len(candles) < 100:

            print(
                f"{symbol} => "
                f"NOT_ENOUGH_CANDLES: {len(candles)}"
            )

            return None

        rows = []

        for candle in candles:

            try:

                # Current Kraken format:
                #
                # {
                #   "time": ...,
                #   "open": ...,
                #   "high": ...,
                #   "low": ...,
                #   "close": ...,
                #   "volume": ...
                # }

                if isinstance(candle, dict):

                    rows.append({
                        "time": int(candle["time"]),
                        "open": float(candle["open"]),
                        "high": float(candle["high"]),
                        "low": float(candle["low"]),
                        "close": float(candle["close"]),
                        "volume": float(candle["volume"])
                    })

                # ------------------------------------------------
                # Fallback for array-based responses
                # ------------------------------------------------

                elif isinstance(candle, (list, tuple)):

                    if len(candle) >= 6:

                        rows.append({
                            "time": int(candle[0]),
                            "open": float(candle[1]),
                            "high": float(candle[2]),
                            "low": float(candle[3]),
                            "close": float(candle[4]),
                            "volume": float(candle[5])
                        })

            except Exception:

                continue

        if len(rows) < 100:

            print(
                f"{symbol} => "
                f"PARSE_ERROR: {len(rows)} valid candles"
            )

            return None

        df = pd.DataFrame(rows)

        if df.empty:

            print(
                f"{symbol} => EMPTY_DATAFRAME"
            )

            return None

        # Remove duplicates

        df = df.drop_duplicates(
            subset=["time"]
        )

        # Sort oldest -> newest

        df = df.sort_values(
            "time"
        ).reset_index(drop=True)

        # ----------------------------------------------------
        # Normalize timestamp
        # ----------------------------------------------------

        max_time = df["time"].max()

        # Kraken timestamps may be milliseconds
        # or seconds depending on response format.

        if max_time > 10_000_000_000:

            df["time"] = (
                df["time"] // 1000
            )

        # ----------------------------------------------------
        # Remove currently forming candle
        # ----------------------------------------------------

        current_time = int(time.time())

        current_bucket = (
            current_time // 300
        ) * 300

        df = df[
            df["time"] < current_bucket
        ]

        if len(df) < 100:

            print(
                f"{symbol} => "
                f"CLOSED_CANDLES_LOW: {len(df)}"
            )

            return None

        df = df.tail(
            limit
        ).reset_index(drop=True)

        print(
            f"{symbol} => DATA OK "
            f"({len(df)} candles)"
        )

        return df

    except requests.exceptions.Timeout:

        print(
            f"{symbol} => TIMEOUT"
        )

        return None

    except requests.exceptions.RequestException as e:

        print(
            f"{symbol} => REQUEST_ERROR: {e}"
        )

        return None

    except Exception as e:

        print(
            f"{symbol} => EXCEPTION: {e}"
        )

        return None


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

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_lows(df):

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
            lows[i] < left.min()
            and lows[i] < right.min()
        ):

            pivots.append(i)

    return pivots


def find_pivot_highs(df):

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
            highs[i] > left.max()
            and highs[i] > right.max()
        ):

            pivots.append(i)

    return pivots


# ============================================================
# REGULAR BULLISH DIVERGENCE
# ============================================================

def find_bullish_divergence(
    df,
    pivot_lows
):

    results = []

    if len(pivot_lows) < 2:
        return results

    for i in range(
        len(pivot_lows) - 1
    ):

        p1 = pivot_lows[i]
        p2 = pivot_lows[i + 1]

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        price1 = df.iloc[p1]["low"]
        price2 = df.iloc[p2]["low"]

        rsi1 = df.iloc[p1]["rsi"]
        rsi2 = df.iloc[p2]["rsi"]

        if price1 <= 0:
            continue

        price_change = (
            (price2 - price1)
            / price1
            * 100
        )

        rsi_change = (
            rsi2 - rsi1
        )

        # Regular bullish:
        #
        # Price = Lower Low
        # RSI   = Higher Low

        if (
            price_change
            <= -MIN_PRICE_DIFF_PERCENT
            and
            rsi_change
            >= MIN_RSI_DIFF
        ):

            results.append({
                "type": "BULLISH",
                "p1": p1,
                "p2": p2,
                "price1": price1,
                "price2": price2,
                "rsi1": rsi1,
                "rsi2": rsi2
            })

    return results


# ============================================================
# REGULAR BEARISH DIVERGENCE
# ============================================================

def find_bearish_divergence(
    df,
    pivot_highs
):

    results = []

    if len(pivot_highs) < 2:
        return results

    for i in range(
        len(pivot_highs) - 1
    ):

        p1 = pivot_highs[i]
        p2 = pivot_highs[i + 1]

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        price1 = df.iloc[p1]["high"]
        price2 = df.iloc[p2]["high"]

        rsi1 = df.iloc[p1]["rsi"]
        rsi2 = df.iloc[p2]["rsi"]

        if price1 <= 0:
            continue

        price_change = (
            (price2 - price1)
            / price1
            * 100
        )

        rsi_change = (
            rsi2 - rsi1
        )

        # Regular bearish:
        #
        # Price = Higher High
        # RSI   = Lower High

        if (
            price_change
            >= MIN_PRICE_DIFF_PERCENT
            and
            rsi_change
            <= -MIN_RSI_DIFF
        ):

            results.append({
                "type": "BEARISH",
                "p1": p1,
                "p2": p2,
                "price1": price1,
                "price2": price2,
                "rsi1": rsi1,
                "rsi2": rsi2
            })

    return results


# ============================================================
# TRENDLINE
# ============================================================

def get_descending_trendline(
    df,
    pivot_highs
):

    if len(pivot_highs) < 2:
        return None

    highs = df["high"]

    # Newest valid pair first

    for i in range(
        len(pivot_highs) - 1,
        0,
        -1
    ):

        p2 = pivot_highs[i]

        for j in range(
            i - 1,
            -1,
            -1
        ):

            p1 = pivot_highs[j]

            if p2 - p1 > MAX_PIVOT_GAP:
                continue

            y1 = highs.iloc[p1]
            y2 = highs.iloc[p2]

            # Descending line

            if y2 >= y1:
                continue

            slope = (
                (y2 - y1)
                / (p2 - p1)
            )

            return {
                "p1": p1,
                "p2": p2,
                "y1": y1,
                "y2": y2,
                "slope": slope
            }

    return None


def get_ascending_trendline(
    df,
    pivot_lows
):

    if len(pivot_lows) < 2:
        return None

    lows = df["low"]

    for i in range(
        len(pivot_lows) - 1,
        0,
        -1
    ):

        p2 = pivot_lows[i]

        for j in range(
            i - 1,
            -1,
            -1
        ):

            p1 = pivot_lows[j]

            if p2 - p1 > MAX_PIVOT_GAP:
                continue

            y1 = lows.iloc[p1]
            y2 = lows.iloc[p2]

            # Ascending line

            if y2 <= y1:
                continue

            slope = (
                (y2 - y1)
                / (p2 - p1)
            )

            return {
                "p1": p1,
                "p2": p2,
                "y1": y1,
                "y2": y2,
                "slope": slope
            }

    return None


def trendline_value(
    line,
    index
):

    return (
        line["y1"]
        +
        line["slope"]
        *
        (index - line["p1"])
    )


# ============================================================
# TRENDLINE BREAK
# ============================================================

def bullish_trendline_break(
    df,
    line
):

    if line is None:
        return False

    if len(df) < 2:
        return False

    current = len(df) - 1
    previous = len(df) - 2

    prev_close = df.iloc[
        previous
    ]["close"]

    curr_close = df.iloc[
        current
    ]["close"]

    prev_line = trendline_value(
        line,
        previous
    )

    curr_line = trendline_value(
        line,
        current
    )

    return (
        prev_close <= prev_line
        and
        curr_close > curr_line
    )


def bearish_trendline_break(
    df,
    line
):

    if line is None:
        return False

    if len(df) < 2:
        return False

    current = len(df) - 1
    previous = len(df) - 2

    prev_close = df.iloc[
        previous
    ]["close"]

    curr_close = df.iloc[
        current
    ]["close"]

    prev_line = trendline_value(
        line,
        previous
    )

    curr_line = trendline_value(
        line,
        current
    )

    return (
        prev_close >= prev_line
        and
        curr_close < curr_line
    )


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def nearest_resistance(
    df,
    pivot_highs,
    entry
):

    levels = []

    for idx in pivot_highs:

        level = float(
            df.iloc[idx]["high"]
        )

        distance = (
            (level - entry)
            / entry
            * 100
        )

        if (
            level > entry
            and
            distance >= MIN_TP_DISTANCE_PERCENT
        ):

            levels.append(level)

    if not levels:
        return None

    return min(levels)


def nearest_support(
    df,
    pivot_lows,
    entry
):

    levels = []

    for idx in pivot_lows:

        level = float(
            df.iloc[idx]["low"]
        )

        distance = (
            (entry - level)
            / entry
            * 100
        )

        if (
            level < entry
            and
            distance >= MIN_TP_DISTANCE_PERCENT
        ):

            levels.append(level)

    if not levels:
        return None

    return max(levels)


# ============================================================
# BUILD BUY
# ============================================================

def build_buy_setup(
    symbol,
    df,
    divergence,
    pivot_lows,
    pivot_highs
):

    entry = float(
        df.iloc[-1]["close"]
    )

    # Latest swing low

    valid_lows = [
        x
        for x in pivot_lows
        if x < len(df) - PIVOT_RIGHT
    ]

    if not valid_lows:
        return None

    swing_index = valid_lows[-1]

    swing_low = float(
        df.iloc[swing_index]["low"]
    )

    sl = (
        swing_low
        * (1 - SL_BUFFER_PERCENT / 100)
    )

    tp = nearest_resistance(
        df,
        pivot_highs,
        entry
    )

    if tp is None:
        return None

    risk = entry - sl
    reward = tp - entry

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk

    return {
        "symbol": symbol,
        "direction": "LONG",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "divergence": "REGULAR BULLISH",
        "signal_time": int(
            df.iloc[-1]["time"]
        ),
        "swing_time": int(
            df.iloc[swing_index]["time"]
        )
    }


# ============================================================
# BUILD SELL
# ============================================================

def build_sell_setup(
    symbol,
    df,
    divergence,
    pivot_lows,
    pivot_highs
):

    entry = float(
        df.iloc[-1]["close"]
    )

    valid_highs = [
        x
        for x in pivot_highs
        if x < len(df) - PIVOT_RIGHT
    ]

    if not valid_highs:
        return None

    swing_index = valid_highs[-1]

    swing_high = float(
        df.iloc[swing_index]["high"]
    )

    sl = (
        swing_high
        * (1 + SL_BUFFER_PERCENT / 100)
    )

    tp = nearest_support(
        df,
        pivot_lows,
        entry
    )

    if tp is None:
        return None

    risk = sl - entry
    reward = entry - tp

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk

    return {
        "symbol": symbol,
        "direction": "SHORT",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "divergence": "REGULAR BEARISH",
        "signal_time": int(
            df.iloc[-1]["time"]
        ),
        "swing_time": int(
            df.iloc[swing_index]["time"]
        )
    }


# ============================================================
# ANALYZE ONE COIN
# ============================================================

def analyze_coin(
    symbol,
    kraken_symbol
):

    df = get_candles(
        kraken_symbol,
        CANDLE_LIMIT
    )

    if df is None:

        return {
            "symbol": symbol,
            "status": "DATA_ERROR"
        }

    try:

        df["rsi"] = calculate_rsi(
            df["close"],
            RSI_PERIOD
        )

        pivot_lows = find_pivot_lows(
            df
        )

        pivot_highs = find_pivot_highs(
            df
        )

        bullish_divs = (
            find_bullish_divergence(
                df,
                pivot_lows
            )
        )

        bearish_divs = (
            find_bearish_divergence(
                df,
                pivot_highs
            )
        )

        # ----------------------------------------------------
        # Latest bullish divergence
        # ----------------------------------------------------

        bullish_div = None

        if bullish_divs:

            bullish_div = (
                bullish_divs[-1]
            )

        # ----------------------------------------------------
        # Latest bearish divergence
        # ----------------------------------------------------

        bearish_div = None

        if bearish_divs:

            bearish_div = (
                bearish_divs[-1]
            )

        current_index = len(df) - 1

        current_time = int(
            df.iloc[-1]["time"]
        )

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if bullish_div is not None:

            div_index = bullish_div[
                "p2"
            ]

            div_time = int(
                df.iloc[div_index]["time"]
            )

            age_minutes = (
                current_time - div_time
            ) / 60

            if (
                age_minutes
                <= MAX_DIVERGENCE_AGE_MINUTES
            ):

                descending_line = (
                    get_descending_trendline(
                        df,
                        pivot_highs
                    )
                )

                if bullish_trendline_break(
                    df,
                    descending_line
                ):

                    setup = build_buy_setup(
                        symbol,
                        df,
                        bullish_div,
                        pivot_lows,
                        pivot_highs
                    )

                    if setup:

                        setup["divergence_age"] = (
                            age_minutes
                        )

                        setup["status"] = (
                            "SIGNAL"
                        )

                        return setup

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        if bearish_div is not None:

            div_index = bearish_div[
                "p2"
            ]

            div_time = int(
                df.iloc[div_index]["time"]
            )

            age_minutes = (
                current_time - div_time
            ) / 60

            if (
                age_minutes
                <= MAX_DIVERGENCE_AGE_MINUTES
            ):

                ascending_line = (
                    get_ascending_trendline(
                        df,
                        pivot_lows
                    )
                )

                if bearish_trendline_break(
                    df,
                    ascending_line
                ):

                    setup = build_sell_setup(
                        symbol,
                        df,
                        bearish_div,
                        pivot_lows,
                        pivot_highs
                    )

                    if setup:

                        setup["divergence_age"] = (
                            age_minutes
                        )

                        setup["status"] = (
                            "SIGNAL"
                        )

                        return setup

        return {
            "symbol": symbol,
            "status": "NO_SIGNAL",
            "price": float(
                df.iloc[-1]["close"]
            ),
            "rsi": float(
                df.iloc[-1]["rsi"]
            )
        }

    except Exception as e:

        print(
            f"{symbol} => ANALYSIS_ERROR: {e}"
        )

        return {
            "symbol": symbol,
            "status": "ANALYSIS_ERROR",
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
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(
    state
):

    open_trades = [
        t
        for t in state["trades"]
        if t.get("status") == "OPEN"
    ]

    if not open_trades:
        return []

    closed = []

    for trade in open_trades:

        symbol = trade["symbol"]

        kraken_symbol = SYMBOLS.get(
            symbol
        )

        if not kraken_symbol:
            continue

        df = get_candles(
            kraken_symbol,
            20
        )

        if df is None or df.empty:
            continue

        last = df.iloc[-1]

        high = float(
            last["high"]
        )

        low = float(
            last["low"]
        )

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        result = None

        # Conservative rule:
        # If one candle touches both,
        # SL wins.

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl:

                result = "LOSS"

            elif hit_tp:

                result = "WIN"

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl:

                result = "LOSS"

            elif hit_tp:

                result = "WIN"

        if result:

            trade["status"] = "CLOSED"

            trade["result"] = result

            trade["close_time"] = int(
                last["time"]
            )

            trade["close_price"] = (
                sl
                if result == "LOSS"
                else tp
            )

            closed.append(
                trade
            )

    return closed


# ============================================================
# STATISTICS
# ============================================================

def get_statistics(state):

    trades = state.get(
        "trades",
        []
    )

    closed = [
        t
        for t in trades
        if t.get("status") == "CLOSED"
    ]

    open_trades = [
        t
        for t in trades
        if t.get("status") == "OPEN"
    ]

    wins = [
        t
        for t in closed
        if t.get("result") == "WIN"
    ]

    losses = [
        t
        for t in closed
        if t.get("result") == "LOSS"
    ]

    total_closed = len(closed)

    if total_closed > 0:

        win_rate = (
            len(wins)
            /
            total_closed
            * 100
        )

    else:

        win_rate = 0

    return {
        "total": len(trades),
        "open": len(open_trades),
        "closed": total_closed,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate
    }


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    direction = signal[
        "direction"
    ]

    if direction == "LONG":
        emoji = "🟢"
        side = "BUY"
    else:
        emoji = "🔴"
        side = "SELL"

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]
    rr = signal["rr"]

    return (
        f"🚨 <b>{side} SIGNAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} <b>{signal['symbol']}/USDT</b>\n\n"
        f"📊 Timeframe: 5M\n"
        f"📌 Setup: "
        f"{signal['divergence']}\n"
        f"📈 Trendline Break: YES\n\n"
        f"💰 Entry: <b>{entry:.8g}</b>\n"
        f"🛑 SL: <b>{sl:.8g}</b>\n"
        f"🎯 TP: <b>{tp:.8g}</b>\n"
        f"⚖️ RR: <b>1:{rr:.2f}</b>\n"
        f"📏 TP Distance: "
        f"{abs(tp-entry)/entry*100:.2f}%\n"
        f"⏱ Divergence Age: "
        f"{signal.get('divergence_age', 0):.0f}m"
    )


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    signals,
    state,
    closed_trades
):

    stats = get_statistics(
        state
    )

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    text = (
        f"📡 <b>CRYPTO SCANNER 5M</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}\n\n"
    )

    if signals:

        text += (
            f"🚨 <b>NEW SIGNALS: "
            f"{len(signals)}</b>\n\n"
        )

        for i, s in enumerate(
            signals[:TOP_SIGNAL_LIMIT],
            1
        ):

            side = (
                "🟢 BUY"
                if s["direction"] == "LONG"
                else "🔴 SELL"
            )

            text += (
                f"{i}. {side} "
                f"<b>{s['symbol']}</b>\n"
                f"Entry: {s['entry']:.8g}\n"
                f"SL: {s['sl']:.8g}\n"
                f"TP: {s['tp']:.8g}\n"
                f"RR: 1:{s['rr']:.2f}\n\n"
            )

    else:

        text += (
            "👀 <b>NO NEW SIGNAL</b>\n\n"
        )

    if closed_trades:

        text += (
            "━━━━━━━━━━━━━━━━━━\n"
            "📊 <b>CLOSED TRADES</b>\n"
        )

        for trade in closed_trades:

            result = trade.get(
                "result"
            )

            emoji = (
                "✅"
                if result == "WIN"
                else "❌"
            )

            text += (
                f"{emoji} "
                f"{trade['symbol']} "
                f"{trade['direction']} "
                f"→ {result}\n"
            )

        text += "\n"

    text += (
        "━━━━━━━━━━━━━━━━━━\n"
        "📈 <b>STATISTICS</b>\n"
        f"Open: {stats['open']}\n"
        f"Closed: {stats['closed']}\n"
        f"Wins: {stats['wins']}\n"
        f"Losses: {stats['losses']}\n"
        f"Win Rate: "
        f"{stats['win_rate']:.1f}%\n"
    )

    return text


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    state = load_state()

    scan_number = int(
        time.time() // 300
    )

    print("=" * 46)
    print(f"SCAN #{scan_number}")
    print("=" * 46)

    # --------------------------------------------------------
    # Update previous open trades
    # --------------------------------------------------------

    closed_trades = update_open_trades(
        state
    )

    if closed_trades:

        print(
            f"CLOSED TRADES: "
            f"{len(closed_trades)}"
        )

    # --------------------------------------------------------
    # Analyze 30 coins
    # --------------------------------------------------------

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_coin,
                symbol,
                kraken_symbol
            ): symbol

            for symbol, kraken_symbol
            in SYMBOLS.items()
        }

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                result = future.result()

                results.append(
                    result
                )

            except Exception as e:

                print(
                    f"{symbol} => "
                    f"THREAD_ERROR: {e}"
                )

                results.append({
                    "symbol": symbol,
                    "status": "ERROR",
                    "error": str(e)
                })

    # --------------------------------------------------------
    # New signals
    # --------------------------------------------------------

    signals = [
        r
        for r in results
        if r.get("status") == "SIGNAL"
    ]

    # Sort by RR

    signals.sort(
        key=lambda x: x.get(
            "rr",
            0
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # Save new signals
    # --------------------------------------------------------

    new_signals = []

    existing_keys = {
        t.get("key")
        for t in state["trades"]
    }

    for signal in signals:

        key = trade_key(
            signal
        )

        if key in existing_keys:
            continue

        trade = {
            "key": key,
            "symbol": signal[
                "symbol"
            ],
            "direction": signal[
                "direction"
            ],
            "entry": signal[
                "entry"
            ],
            "sl": signal[
                "sl"
            ],
            "tp": signal[
                "tp"
            ],
            "rr": signal[
                "rr"
            ],
            "divergence": signal[
                "divergence"
            ],
            "signal_time": signal[
                "signal_time"
            ],
            "swing_time": signal[
                "swing_time"
            ],
            "status": "OPEN",
            "open_time": int(
                time.time()
            )
        }

        state["trades"].append(
            trade
        )

        existing_keys.add(
            key
        )

        new_signals.append(
            signal
        )

    # --------------------------------------------------------
    # Update state
    # --------------------------------------------------------

    state["last_scan"] = int(
        time.time()
    )

    save_state(
        state
    )

    # --------------------------------------------------------
    # Print results
    # --------------------------------------------------------

    data_errors = [
        r
        for r in results
        if r.get("status")
        == "DATA_ERROR"
    ]

    analysis_errors = [
        r
        for r in results
        if r.get("status")
        == "ANALYSIS_ERROR"
    ]

    print()

    print(
        f"TOTAL COINS: "
        f"{len(results)}"
    )

    print(
        f"DATA ERRORS: "
        f"{len(data_errors)}"
    )

    print(
        f"ANALYSIS ERRORS: "
        f"{len(analysis_errors)}"
    )

    print(
        f"SIGNALS: "
        f"{len(new_signals)}"
    )

    # --------------------------------------------------------
    # Send individual new signals
    # --------------------------------------------------------

    for signal in new_signals:

        message = format_signal(
            signal
        )

        telegram_send(
            message
        )

    # --------------------------------------------------------
    # Send report
    # --------------------------------------------------------

    report = format_report(
        new_signals,
        state,
        closed_trades
    )

    telegram_send(
        report
    )

    # --------------------------------------------------------
    # Finish
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start_time
    )

    print()

    print(
        f"SCAN #{scan_number} "
        f"FINISHED in "
        f"{elapsed:.2f}s"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
