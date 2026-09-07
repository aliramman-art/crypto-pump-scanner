# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v3.0
# ============================================================
# 1H  = TREND
# 30M = CONFIRM
# 15M = PULLBACK
# 5M  = TRIGGER
#
# TOP 100 Kraken Futures
# CLOSED CANDLES ONLY
# MAX OPEN TRADE = 1
# RR = 1:1
# VIRTUAL SIGNAL SIMULATOR
#
# REPORT:
# - Open trade
# - New signal
# - TOP 5 candidates
# - Rejection stage / reason
# ============================================================

import os
import json
import time
import math
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

INSTRUMENTS_URL = "https://futures.kraken.com/derivatives/api/v3/instruments"
TICKERS_URL = "https://futures.kraken.com/derivatives/api/v3/tickers"
CHART_URL = "https://futures.kraken.com/api/charts/v1/trade/{symbol}/{minutes}"

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TOP_N = 100
MAX_OPEN_TRADES = 1

TIMEOUT_MINUTES = 240
RR = 1.0

ATR_PERIOD = 14

MIN_1H_LONG = 6
MIN_1H_SHORT = -6

MIN_30M_LONG = 5
MIN_30M_SHORT = -5

PULLBACK_ATR_MAX = 1.5

MAX_WORKERS = 12

# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
})


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    os.replace(tmp, path)


state = load_json(
    STATE_FILE,
    {
        "open_trade": None,
        "last_signal": None
    }
)

history = load_json(
    HISTORY_FILE,
    []
)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        r = session.post(
            url,
            json=payload,
            timeout=15
        )

        if not r.ok:
            print("[TELEGRAM ERROR]", r.text)

    except Exception as e:
        print("[TELEGRAM ERROR]", e)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_string(ts=None):

    if ts is None:
        dt = utc_now()
    else:
        dt = datetime.fromtimestamp(ts, timezone.utc)

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(x):

    try:
        return float(x)
    except Exception:
        return None


def clamp(x, low, high):
    return max(low, min(high, x))


# ============================================================
# API
# ============================================================

def get_instruments():

    try:
        r = session.get(
            INSTRUMENTS_URL,
            timeout=20
        )
        r.raise_for_status()

        data = r.json()

        if isinstance(data, dict):
            return data.get("instruments", [])

        return []

    except Exception as e:
        print("[ERROR] instruments:", e)
        return []


def get_tickers():

    try:
        r = session.get(
            TICKERS_URL,
            timeout=20
        )
        r.raise_for_status()

        data = r.json()

        if isinstance(data, dict):
            return data.get("tickers", [])

        return []

    except Exception as e:
        print("[ERROR] tickers:", e)
        return []


# ============================================================
# MARKET DISCOVERY
# ============================================================

def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    ticker_map = {}

    for t in tickers:

        symbol = t.get("symbol")

        if symbol:
            ticker_map[symbol] = t

    markets = []

    for ins in instruments:

        symbol = ins.get("symbol", "")

        if not symbol:
            continue

        if not symbol.startswith("PF_"):
            continue

        if "USD" not in symbol:
            continue

        status = str(
            ins.get("status", "")
        ).lower()

        if status and status not in (
            "online",
            "open",
            "trading"
        ):
            continue

        ticker = ticker_map.get(symbol, {})

        volume = (
            safe_float(ticker.get("vol24h"))
            or safe_float(ticker.get("volume24h"))
            or 0
        )

        markets.append({
            "symbol": symbol,
            "volume": volume
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, minutes, limit=250):

    url = CHART_URL.format(
        symbol=symbol,
        minutes=minutes
    )

    try:

        r = session.get(
            url,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        candles = (
            data.get("candles")
            if isinstance(data, dict)
            else data
        )

        if not candles:
            return []

        result = []

        now_ts = int(time.time())

        for c in candles:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("ts")
                )

                o = c.get("open")
                h = c.get("high")
                l = c.get("low")
                cl = c.get("close")

            else:
                continue

            ts = safe_float(ts)
            o = safe_float(o)
            h = safe_float(h)
            l = safe_float(l)
            cl = safe_float(cl)

            if None in (
                ts,
                o,
                h,
                l,
                cl
            ):
                continue

            # Kraken timestamps can be milliseconds
            if ts > 10_000_000_000:
                ts /= 1000

            # CLOSED CANDLE ONLY
            if ts + minutes * 60 > now_ts:
                continue

            result.append({
                "time": int(ts),
                "open": o,
                "high": h,
                "low": l,
                "close": cl
            })

        result.sort(
            key=lambda x: x["time"]
        )

        if len(result) > limit:
            result = result[-limit:]

        return result

    except Exception as e:
        print(
            f"[CANDLE ERROR] {symbol} {minutes}m:",
            e
        )
        return []


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku(candles):

    n = len(candles)

    if n < 80:
        return None

    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    closes = [x["close"] for x in candles]

    tenkan = [None] * n
    kijun = [None] * n
    span_a = [None] * n
    span_b = [None] * n

    for i in range(n):

        if i >= 8:
            tenkan[i] = (
                max(highs[i - 8:i + 1])
                +
                min(lows[i - 8:i + 1])
            ) / 2

        if i >= 25:
            kijun[i] = (
                max(highs[i - 25:i + 1])
                +
                min(lows[i - 25:i + 1])
            ) / 2

        if tenkan[i] is not None and kijun[i] is not None:
            span_a[i] = (
                tenkan[i] + kijun[i]
            ) / 2

        if i >= 51:
            span_b[i] = (
                max(highs[i - 51:i + 1])
                +
                min(lows[i - 51:i + 1])
            ) / 2

    i = n - 1

    if (
        tenkan[i] is None
        or kijun[i] is None
        or span_a[i] is None
        or span_b[i] is None
    ):
        return None

    # Current Kumo
    # Cloud visible NOW = span calculated 26 candles ago
    cloud_index = i - 26

    if cloud_index < 0:
        return None

    current_a = span_a[cloud_index]
    current_b = span_b[cloud_index]

    if current_a is None or current_b is None:
        return None

    current_cloud_top = max(
        current_a,
        current_b
    )

    current_cloud_bottom = min(
        current_a,
        current_b
    )

    # Future cloud
    future_a = span_a[i]
    future_b = span_b[i]

    # Chikou
    chikou_index = i - 26

    chikou = None

    if chikou_index >= 0:
        chikou = closes[i] > closes[chikou_index]

    # Previous values for cloud direction
    prev_cloud_index = i - 27

    prev_a = (
        span_a[prev_cloud_index]
        if prev_cloud_index >= 0
        else None
    )

    prev_b = (
        span_b[prev_cloud_index]
        if prev_cloud_index >= 0
        else None
    )

    cloud_direction = 0

    if (
        prev_a is not None
        and prev_b is not None
    ):
        prev_mid = (prev_a + prev_b) / 2
        current_mid = (current_a + current_b) / 2

        if current_mid > prev_mid:
            cloud_direction = 1

        elif current_mid < prev_mid:
            cloud_direction = -1

    return {
        "price": closes[i],
        "prev_price": closes[i - 1],

        "tenkan": tenkan[i],
        "prev_tenkan": tenkan[i - 1],

        "kijun": kijun[i],
        "prev_kijun": kijun[i - 1],

        "cloud_top": current_cloud_top,
        "cloud_bottom": current_cloud_bottom,

        "future_a": future_a,
        "future_b": future_b,

        "chikou_bull": chikou,

        "cloud_direction": cloud_direction
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 2:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# 1H SCORE
# ============================================================

def calculate_score(info):

    if not info:
        return 0

    price = info["price"]
    tenkan = info["tenkan"]
    kijun = info["kijun"]

    top = info["cloud_top"]
    bottom = info["cloud_bottom"]

    score = 0

    # Price vs cloud
    if price > top:
        score += 3

    elif price < bottom:
        score -= 3

    # Price vs Tenkan
    if price > tenkan:
        score += 1

    elif price < tenkan:
        score -= 1

    # Price vs Kijun
    if price > kijun:
        score += 2

    elif price < kijun:
        score -= 2

    # Future cloud
    future_a = info["future_a"]
    future_b = info["future_b"]

    if future_a > future_b:
        score += 2

    elif future_a < future_b:
        score -= 2

    # Chikou
    if info["chikou_bull"] is True:
        score += 2

    elif info["chikou_bull"] is False:
        score -= 2

    return int(
        clamp(score, -10, 10)
    )


# ============================================================
# DIRECTION
# ============================================================

def score_direction(score):

    if score > 0:
        return "LONG"

    if score < 0:
        return "SHORT"

    return "NONE"


# ============================================================
# 15M PULLBACK
# ============================================================

def check_pullback(info, atr, direction):

    if not info or not atr or atr <= 0:
        return {
            "ok": False,
            "distance_atr": None,
            "reason": "ATR unavailable"
        }

    price = info["price"]

    top = info["cloud_top"]
    bottom = info["cloud_bottom"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    if direction == "LONG":

        if price <= top:
            return {
                "ok": False,
                "distance_atr": None,
                "reason": "Below/inside Kumo"
            }

        distance = min(
            abs(price - tenkan),
            abs(price - kijun)
        )

        distance_atr = distance / atr

        if distance_atr <= PULLBACK_ATR_MAX:
            return {
                "ok": True,
                "distance_atr": distance_atr,
                "reason": "Valid bullish pullback"
            }

        return {
            "ok": False,
            "distance_atr": distance_atr,
            "reason": f"Pullback too far ({distance_atr:.2f} ATR)"
        }

    if direction == "SHORT":

        if price >= bottom:
            return {
                "ok": False,
                "distance_atr": None,
                "reason": "Above/inside Kumo"
            }

        distance = min(
            abs(price - tenkan),
            abs(price - kijun)
        )

        distance_atr = distance / atr

        if distance_atr <= PULLBACK_ATR_MAX:
            return {
                "ok": True,
                "distance_atr": distance_atr,
                "reason": "Valid bearish pullback"
            }

        return {
            "ok": False,
            "distance_atr": distance_atr,
            "reason": f"Pullback too far ({distance_atr:.2f} ATR)"
        }

    return {
        "ok": False,
        "distance_atr": None,
        "reason": "No direction"
    }


# ============================================================
# 5M TRIGGER
# ============================================================

def check_trigger(candles, info, direction):

    if not info or len(candles) < 3:
        return {
            "ok": False,
            "reason": "Not enough 5m candles"
        }

    prev = candles[-2]
    curr = candles[-1]

    price = curr["close"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    cloud_top = info["cloud_top"]
    cloud_bottom = info["cloud_bottom"]

    prev_tenkan = info["prev_tenkan"]
    prev_kijun = info["prev_kijun"]

    if direction == "LONG":

        cross_tenkan = (
            prev["close"] <= prev_tenkan
            and price > tenkan
        )

        cross_kijun = (
            prev["close"] <= prev_kijun
            and price > kijun
        )

        cloud_ok = price > cloud_top

        if (cross_tenkan or cross_kijun) and cloud_ok:

            trigger = (
                "Tenkan cross"
                if cross_tenkan
                else "Kijun cross"
            )

            return {
                "ok": True,
                "reason": trigger
            }

        if not cloud_ok:
            return {
                "ok": False,
                "reason": "5m price not above Kumo"
            }

        return {
            "ok": False,
            "reason": "No bullish Tenkan/Kijun cross"
        }

    if direction == "SHORT":

        cross_tenkan = (
            prev["close"] >= prev_tenkan
            and price < tenkan
        )

        cross_kijun = (
            prev["close"] >= prev_kijun
            and price < kijun
        )

        cloud_ok = price < cloud_bottom

        if (cross_tenkan or cross_kijun) and cloud_ok:

            trigger = (
                "Tenkan cross"
                if cross_tenkan
                else "Kijun cross"
            )

            return {
                "ok": True,
                "reason": trigger
            }

        if not cloud_ok:
            return {
                "ok": False,
                "reason": "5m price not below Kumo"
            }

        return {
            "ok": False,
            "reason": "No bearish Tenkan/Kijun cross"
        }

    return {
        "ok": False,
        "reason": "No direction"
    }


# ============================================================
# SWING
# ============================================================

def latest_swing_low(candles):

    if len(candles) < 7:
        return None

    for i in range(
        len(candles) - 3,
        2,
        -1
    ):

        c = candles[i]

        if (
            c["low"] < candles[i - 1]["low"]
            and
            c["low"] < candles[i - 2]["low"]
            and
            c["low"] < candles[i + 1]["low"]
            and
            c["low"] < candles[i + 2]["low"]
        ):
            return c["low"]

    return min(
        x["low"]
        for x in candles[-10:]
    )


def latest_swing_high(candles):

    if len(candles) < 7:
        return None

    for i in range(
        len(candles) - 3,
        2,
        -1
    ):

        c = candles[i]

        if (
            c["high"] > candles[i - 1]["high"]
            and
            c["high"] > candles[i - 2]["high"]
            and
            c["high"] > candles[i + 1]["high"]
            and
            c["high"] > candles[i + 2]["high"]
        ):
            return c["high"]

    return max(
        x["high"]
        for x in candles[-10:]
    )


# ============================================================
# COMPLETE MARKET ANALYSIS
# ============================================================

def analyze_market(market):

    symbol = market["symbol"]

    result = {
        "symbol": symbol,
        "valid": False,

        "direction": "NONE",

        "stage": "ERROR",
        "reason": "Unknown",

        "score_1h": 0,
        "score_30m": 0,

        "trend_ok": False,
        "confirm_ok": False,
        "pullback_ok": False,
        "trigger_ok": False,

        "distance_atr": None,

        "entry": None,
        "sl": None,
        "tp": None,

        "volume": market.get("volume", 0)
    }

    try:

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        c1h = get_candles(
            symbol,
            60,
            220
        )

        if len(c1h) < 100:

            result["stage"] = "1H"
            result["reason"] = "Not enough 1H candles"

            return result

        i1h = ichimoku(c1h)

        if not i1h:

            result["stage"] = "1H"
            result["reason"] = "Ichimoku unavailable"

            return result

        score1h = calculate_score(i1h)

        result["score_1h"] = score1h

        direction = score_direction(
            score1h
        )

        result["direction"] = direction

        # Important:
        # We DO NOT discard weak trends.
        # They remain candidates for the report.

        if score1h >= MIN_1H_LONG:

            direction = "LONG"
            result["direction"] = direction
            result["trend_ok"] = True

        elif score1h <= MIN_1H_SHORT:

            direction = "SHORT"
            result["direction"] = direction
            result["trend_ok"] = True

        else:

            result["stage"] = "1H"
            result["reason"] = (
                f"Trend too weak ({score1h:+d}/10)"
            )

            return result

        # ----------------------------------------------------
        # 30M
        # ----------------------------------------------------

        c30 = get_candles(
            symbol,
            30,
            220
        )

        if len(c30) < 100:

            result["stage"] = "30M"
            result["reason"] = "Not enough 30m candles"

            return result

        i30 = ichimoku(c30)

        if not i30:

            result["stage"] = "30M"
            result["reason"] = "Ichimoku unavailable"

            return result

        score30 = calculate_score(i30)

        result["score_30m"] = score30

        if direction == "LONG":

            if score30 < MIN_30M_LONG:

                result["stage"] = "30M"
                result["reason"] = (
                    f"30m confirmation weak "
                    f"({score30:+d}/10)"
                )

                return result

        elif direction == "SHORT":

            if score30 > MIN_30M_SHORT:

                result["stage"] = "30M"
                result["reason"] = (
                    f"30m confirmation weak "
                    f"({score30:+d}/10)"
                )

                return result

        result["confirm_ok"] = True

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        c15 = get_candles(
            symbol,
            15,
            220
        )

        if len(c15) < 100:

            result["stage"] = "15M"
            result["reason"] = "Not enough 15m candles"

            return result

        i15 = ichimoku(c15)
        atr15 = calculate_atr(
            c15,
            ATR_PERIOD
        )

        if not i15 or not atr15:

            result["stage"] = "15M"
            result["reason"] = (
                "15m Ichimoku/ATR unavailable"
            )

            return result

        pullback = check_pullback(
            i15,
            atr15,
            direction
        )

        result["distance_atr"] = (
            pullback.get("distance_atr")
        )

        if not pullback["ok"]:

            result["stage"] = "15M"
            result["reason"] = pullback["reason"]

            return result

        result["pullback_ok"] = True

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        c5 = get_candles(
            symbol,
            5,
            220
        )

        if len(c5) < 100:

            result["stage"] = "5M"
            result["reason"] = "Not enough 5m candles"

            return result

        i5 = ichimoku(c5)

        if not i5:

            result["stage"] = "5M"
            result["reason"] = "5m Ichimoku unavailable"

            return result

        trigger = check_trigger(
            c5,
            i5,
            direction
        )

        if not trigger["ok"]:

            result["stage"] = "5M"
            result["reason"] = trigger["reason"]

            return result

        result["trigger_ok"] = True

        # ----------------------------------------------------
        # VALID SIGNAL
        # ----------------------------------------------------

        entry = c5[-1]["close"]

        if direction == "LONG":

            sl = latest_swing_low(c5)

            if sl is None or sl >= entry:

                result["stage"] = "5M"
                result["reason"] = (
                    "Invalid structural SL"
                )

                return result

            risk = entry - sl
            tp = entry + risk * RR

        else:

            sl = latest_swing_high(c5)

            if sl is None or sl <= entry:

                result["stage"] = "5M"
                result["reason"] = (
                    "Invalid structural SL"
                )

                return result

            risk = sl - entry
            tp = entry - risk * RR

        result["valid"] = True
        result["stage"] = "VALID"
        result["reason"] = (
            f"Valid {direction} setup"
        )

        result["entry"] = entry
        result["sl"] = sl
        result["tp"] = tp

        return result

    except Exception as e:

        result["stage"] = "ERROR"
        result["reason"] = str(e)[:120]

        return result


# ============================================================
# CANDIDATE RANKING
# ============================================================

STAGE_SCORE = {
    "VALID": 1000,
    "5M": 800,
    "15M": 600,
    "30M": 400,
    "1H": 200,
    "ERROR": 0
}


def candidate_rank(r):

    stage_score = STAGE_SCORE.get(
        r.get("stage"),
        0
    )

    s1 = abs(
        r.get("score_1h", 0)
    )

    s30 = abs(
        r.get("score_30m", 0)
    )

    pullback_bonus = 0

    dist = r.get("distance_atr")

    if dist is not None:

        pullback_bonus = max(
            0,
            50 - dist * 20
        )

    return (
        stage_score
        + s1 * 10
        + s30 * 8
        + pullback_bonus
    )


# ============================================================
# OPEN TRADE
# ============================================================

def create_trade(signal):

    return {
        "symbol": signal["symbol"],
        "direction": signal["direction"],

        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],

        "opened_at": int(
            time.time()
        ),

        "opened_at_utc": utc_string(),

        "status": "OPEN"
    }


def check_open_trade():

    trade = state.get(
        "open_trade"
    )

    if not trade:
        return None

    symbol = trade["symbol"]

    direction = trade["direction"]

    entry = trade["entry"]
    sl = trade["sl"]
    tp = trade["tp"]

    opened_at = trade["opened_at"]

    age_minutes = (
        time.time() - opened_at
    ) / 60

    candles = get_candles(
        symbol,
        5,
        10
    )

    if not candles:
        return trade

    latest = candles[-1]

    high = latest["high"]
    low = latest["low"]

    result = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # Conservative:
        # if SL and TP are both touched,
        # SL is considered first.

        if low <= sl:

            result = {
                "status": "SL",
                "exit": sl,
                "pnl_r": -1
            }

        elif high >= tp:

            result = {
                "status": "TP",
                "exit": tp,
                "pnl_r": RR
            }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        if high >= sl:

            result = {
                "status": "SL",
                "exit": sl,
                "pnl_r": -1
            }

        elif low <= tp:

            result = {
                "status": "TP",
                "exit": tp,
                "pnl_r": RR
            }

    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    if result is None and age_minutes >= TIMEOUT_MINUTES:

        exit_price = latest["close"]

        if direction == "LONG":

            risk = entry - sl

            pnl_r = (
                exit_price - entry
            ) / risk if risk > 0 else 0

        else:

            risk = sl - entry

            pnl_r = (
                entry - exit_price
            ) / risk if risk > 0 else 0

        result = {
            "status": "TIMEOUT",
            "exit": exit_price,
            "pnl_r": pnl_r
        }

    if result:

        closed_trade = dict(trade)

        closed_trade.update({
            "status": result["status"],
            "exit": result["exit"],
            "pnl_r": result["pnl_r"],
            "closed_at": int(time.time()),
            "closed_at_utc": utc_string()
        })

        history.append(
            closed_trade
        )

        state["open_trade"] = None

        save_json(
            STATE_FILE,
            state
        )

        save_json(
            HISTORY_FILE,
            history
        )

        return None

    return trade


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    trades = len(history)

    wins = sum(
        1
        for x in history
        if x.get("status") == "TP"
    )

    losses = sum(
        1
        for x in history
        if x.get("status") == "SL"
    )

    timeouts = sum(
        1
        for x in history
        if x.get("status") == "TIMEOUT"
    )

    if trades:

        win_rate = (
            wins / trades
        ) * 100

    else:

        win_rate = 0

    total_r = sum(
        safe_float(x.get("pnl_r")) or 0
        for x in history
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": win_rate,
        "total_r": total_r
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(x):

    if x is None:
        return "-"

    if x >= 1000:
        return f"{x:.2f}"

    if x >= 1:
        return f"{x:.5f}"

    if x >= 0.01:
        return f"{x:.6f}"

    return f"{x:.8f}"


# ============================================================
# TOP CANDIDATES REPORT
# ============================================================

def candidate_line(index, r):

    symbol = r["symbol"].replace(
        "PF_",
        ""
    )

    direction = r["direction"]

    if direction == "LONG":
        arrow = "🟢"

    elif direction == "SHORT":
        arrow = "🔴"

    else:
        arrow = "⚪"

    stage = r["stage"]

    s1 = r["score_1h"]
    s30 = r["score_30m"]

    reason = r["reason"]

    line = (
        f"{index}. {arrow} `{symbol}` "
        f"{direction}\n"
        f"   1H {s1:+d} | "
        f"30m {s30:+d} | "
        f"Stage: {stage}\n"
        f"   {reason}"
    )

    if r.get("distance_atr") is not None:

        line += (
            f"\n   📏 Pullback: "
            f"{r['distance_atr']:.2f} ATR"
        )

    return line


# ============================================================
# REPORT
# ============================================================

def build_report(
    markets_count,
    results,
    open_trade,
    new_signal
):

    p = performance()

    valid = [
        x
        for x in results
        if x["valid"]
    ]

    ranked = sorted(
        results,
        key=candidate_rank,
        reverse=True
    )

    top5 = ranked[:5]

    now = utc_string()

    text = (
        "📡 *KRAKEN FUTURES ICHIMOKU REPORT*\n"
        f"🕐 {now}\n"
        "⏱ *5m CLOSED | TOP 100*\n"
        "🤖 *ICHIMOKU MTF*\n"
        "1H → Trend | 30m → Confirm | "
        "15m → Pullback | 5m → Trigger\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    text += (
        "📊 *PERFORMANCE*\n"
        f"Trades {p['trades']} | "
        f"🟢 {p['wins']} | "
        f"🔴 {p['losses']} | "
        f"⚪ {p['timeouts']}\n"
        f"🏆 WR: {p['win_rate']:.1f}%\n"
        f"📈 Total R: {p['total_r']:+.2f}\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------------

    text += "📌 *OPEN TRADE*\n"

    if open_trade:

        symbol = open_trade["symbol"].replace(
            "PF_",
            ""
        )

        emoji = (
            "🟢"
            if open_trade["direction"] == "LONG"
            else "🔴"
        )

        text += (
            f"{emoji} `{symbol}` "
            f"{open_trade['direction']}\n"
            f"Entry: `{fmt_price(open_trade['entry'])}`\n"
            f"SL: `{fmt_price(open_trade['sl'])}`\n"
            f"TP: `{fmt_price(open_trade['tp'])}`\n"
        )

    else:

        text += "None\n"

    text += "━━━━━━━━━━━━━━━━━━\n"

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    text += "🔎 *NEW SIGNAL*\n"

    if new_signal:

        symbol = new_signal["symbol"].replace(
            "PF_",
            ""
        )

        emoji = (
            "🟢"
            if new_signal["direction"] == "LONG"
            else "🔴"
        )

        text += (
            f"{emoji} *{symbol}* "
            f"{new_signal['direction']}\n"
            f"Entry: `{fmt_price(new_signal['entry'])}`\n"
            f"SL: `{fmt_price(new_signal['sl'])}`\n"
            f"TP: `{fmt_price(new_signal['tp'])}`\n"
            f"1H Score: `{new_signal['score_1h']:+d}/10`\n"
            f"30m Score: `{new_signal['score_30m']:+d}/10`\n"
        )

    else:

        text += "No new valid MTF setup.\n"

    text += "━━━━━━━━━━━━━━━━━━\n"

    # --------------------------------------------------------
    # TOP CANDIDATES
    # --------------------------------------------------------

    text += (
        "🧪 *TOP 5 CANDIDATES*\n"
        "_Shows how far each market reached._\n\n"
    )

    if top5:

        for i, r in enumerate(
            top5,
            1
        ):

            text += (
                candidate_line(i, r)
                + "\n\n"
            )

    else:

        text += "No candidates available.\n\n"

    text += "━━━━━━━━━━━━━━━━━━\n"

    # --------------------------------------------------------
    # SCANNER STATUS
    # --------------------------------------------------------

    stage_counts = {}

    for r in results:

        stage = r.get(
            "stage",
            "ERROR"
        )

        stage_counts[stage] = (
            stage_counts.get(stage, 0) + 1
        )

    text += (
        "📡 *SCANNER STATUS*\n"
        f"Scanned: {markets_count}\n"
        f"Valid: {len(valid)}\n"
        f"1H rejected: {stage_counts.get('1H', 0)}\n"
        f"30m rejected: {stage_counts.get('30M', 0)}\n"
        f"15m rejected: {stage_counts.get('15M', 0)}\n"
        f"5m rejected: {stage_counts.get('5M', 0)}\n"
        f"Errors: {stage_counts.get('ERROR', 0)}\n"
        f"🔒 Max Open: {MAX_OPEN_TRADES}\n"
        "⚠️ Virtual signal simulator only"
    )

    return text


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("KRAKEN FUTURES ICHIMOKU MTF SCANNER v3.0")
    print("=" * 70)

    # --------------------------------------------------------
    # CHECK EXISTING TRADE
    # --------------------------------------------------------

    open_trade = check_open_trade()

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    markets = get_top_markets()

    print(
        f"[INFO] Markets: {len(markets)}"
    )

    if not markets:

        msg = (
            "❌ *SCANNER ERROR*\n"
            "No Kraken Futures markets found."
        )

        send_telegram(msg)
        return

    # --------------------------------------------------------
    # ANALYZE ALL MARKETS
    # --------------------------------------------------------

    results = []

    print(
        "[INFO] Analyzing markets..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_market,
                market
            ): market
            for market in markets
        }

        for future in as_completed(
            futures
        ):

            market = futures[future]

            try:

                result = future.result()

                results.append(
                    result
                )

                print(
                    f"[SCAN] "
                    f"{result['symbol']} | "
                    f"{result['stage']} | "
                    f"1H {result['score_1h']:+d} | "
                    f"30M {result['score_30m']:+d} | "
                    f"{result['reason']}"
                )

            except Exception as e:

                results.append({
                    "symbol": market["symbol"],
                    "valid": False,
                    "direction": "NONE",
                    "stage": "ERROR",
                    "reason": str(e),
                    "score_1h": 0,
                    "score_30m": 0,
                    "trend_ok": False,
                    "confirm_ok": False,
                    "pullback_ok": False,
                    "trigger_ok": False,
                    "distance_atr": None,
                    "entry": None,
                    "sl": None,
                    "tp": None,
                    "volume": market.get(
                        "volume",
                        0
                    )
                })

    # --------------------------------------------------------
    # VALID SIGNALS
    # --------------------------------------------------------

    valid_signals = [
        x
        for x in results
        if x["valid"]
    ]

    valid_signals.sort(
        key=candidate_rank,
        reverse=True
    )

    new_signal = None

    # --------------------------------------------------------
    # ONLY ONE OPEN TRADE
    # --------------------------------------------------------

    if open_trade is None:

        if valid_signals:

            new_signal = valid_signals[0]

            open_trade = create_trade(
                new_signal
            )

            state["open_trade"] = (
                open_trade
            )

            state["last_signal"] = (
                new_signal
            )

            save_json(
                STATE_FILE,
                state
            )

            print(
                "[SIGNAL]",
                new_signal["symbol"],
                new_signal["direction"]
            )

    else:

        print(
            "[INFO] Existing trade active:",
            open_trade["symbol"]
        )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        len(markets),
        results,
        open_trade,
        new_signal
    )

    print("\n" + report)

    send_telegram(
        report
    )

    print("=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
