# ============================================================
# CRYPTO PRICE ACTION SCANNER v1.3
# ============================================================
# Kraken Futures
# TOP 30 high-volume coins
# 5M CLOSED CANDLES
#
# PRICE ACTION ONLY
# - Swing High / Swing Low
# - Market Structure
# - BOS
# - CHOCH
# - Pullback
# - Candle Confirmation
# - Volume
# - Structural SL
# - TP / RR
# - Score 0-100
#
# POSITION MANAGEMENT v1.3
# - MAX 5 OPEN TRADES
# - MAX 1 OPEN TRADE PER SYMBOL
# - WAITING QUEUE
# - HIGHER SCORE PRIORITY
# - DUPLICATE SIGNAL PROTECTION
# - SIGNAL DURATION
# - CLOSED TRADE DURATION
#
# TELEGRAM
# - Jalali date
# - Tehran time
# - English numbers
# - Performance
# - Open trades
# - Waiting signals
# - SL percentage
# - TP percentage
# - LIVE market P&L
# - Duration
#
# IMPORTANT
# - Signals generated from CLOSED 5M candles
# - Live P&L uses CURRENT MARKET PRICE
# - SL/TP closing logic uses CLOSED 5M candle
# - No automatic replacement of an open trade
# ============================================================

import os
import json
import time
import requests
import ccxt
import pandas as pd

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "5m"
TOP_N = 30

OHLCV_LIMIT = 100

SWING_LEFT = 2
SWING_RIGHT = 2

MIN_SCORE = 55

RR = 1.0

# ------------------------------------------------------------
# POSITION LIMITS
# ------------------------------------------------------------

MAX_OPEN_TRADES = 5
MAX_TRADES_PER_SYMBOL = 1

MAX_WAITING_TRADES = 20

# Prevent same signal from being recreated too quickly
SIGNAL_COOLDOWN_CANDLES = 3

# A signal with same symbol/side/candle cannot duplicate
DUPLICATE_CANDLE_PROTECTION = True

STATE_FILE = "ut_bot_state.json"
HISTORY_FILE = "ut_bot_trade_history.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 20

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
    "timeout": 20000,
})


# ============================================================
# FILE HELPERS
# ============================================================

def load_json(path, default):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"JSON LOAD ERROR {path}: {e}"
        )

        return default


def save_json(path, data):

    tmp = path + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        tmp,
        path
    )


# ============================================================
# STATE
# ============================================================

state = load_json(
    STATE_FILE,
    {
        "open": {},
        "waiting": {},
        "last_signals": {}
    }
)

history = load_json(
    HISTORY_FILE,
    {
        "trades": []
    }
)

if not isinstance(state, dict):

    state = {}


if not isinstance(history, dict):

    history = {}


if "open" not in state:
    state["open"] = {}

if "waiting" not in state:
    state["waiting"] = {}

if "last_signals" not in state:
    state["last_signals"] = {}

if "trades" not in history:
    history["trades"] = []


# Remove old legacy section
state.pop("pending", None)


# ============================================================
# NUMBER FORMAT
# ============================================================

def fmt_number(value, decimals=2):

    if value is None:
        return "-"

    try:
        return f"{float(value):.{decimals}f}"

    except Exception:
        return str(value)


def fmt_price(price):

    if price is None:
        return "-"

    try:
        price = float(price)

    except Exception:
        return "-"

    if price >= 1000:
        return f"{price:.4f}"

    elif price >= 1:
        return f"{price:.6f}"

    elif price >= 0.01:
        return f"{price:.6f}"

    else:
        return f"{price:.8f}"


def fmt_pct(value, signed=False):

    if value is None:
        return "-"

    try:
        value = float(value)

    except Exception:
        return "-"

    if signed:
        return f"{value:+.2f}%"

    return f"{value:.2f}%"


# ============================================================
# DURATION
# ============================================================

def parse_datetime(value):

    if not value:
        return None

    try:

        dt = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:

        return None


def duration_seconds(opened_at, now=None):

    opened = parse_datetime(
        opened_at
    )

    if opened is None:
        return 0

    if now is None:

        now = datetime.now(
            timezone.utc
        )

    try:

        seconds = (
            now - opened
        ).total_seconds()

        return max(
            0,
            int(seconds)
        )

    except Exception:

        return 0


def format_duration_from_seconds(seconds):

    try:
        seconds = int(seconds)

    except Exception:
        seconds = 0

    seconds = max(
        0,
        seconds
    )

    minutes = seconds // 60

    days = minutes // 1440

    minutes %= 1440

    hours = minutes // 60

    minutes %= 60

    if days > 0:

        return (
            f"{days}d "
            f"{hours}h "
            f"{minutes}m"
        )

    if hours > 0:

        return (
            f"{hours}h "
            f"{minutes}m"
        )

    return f"{minutes}m"


def format_duration(
    opened_at,
    now=None
):

    return format_duration_from_seconds(
        duration_seconds(
            opened_at,
            now
        )
    )


# ============================================================
# JALALI DATE
# ============================================================

def gregorian_to_jalali(
    gy,
    gm,
    gd
):

    g_days_in_month = [
        31, 28, 31, 30, 31, 30,
        31, 31, 30, 31, 30, 31
    ]

    j_days_in_month = [
        31, 31, 31, 31, 31, 31,
        30, 30, 30, 30, 30, 29
    ]

    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1

    g_day_no = (
        365 * gy2
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
    )

    for i in range(gm2):

        g_day_no += g_days_in_month[i]

    if (
        gm2 > 1
        and
        gy % 4 == 0
        and
        (
            gy % 100 != 0
            or
            gy % 400 == 0
        )
    ):

        g_day_no += 1

    g_day_no += gd2

    j_day_no = g_day_no - 79

    j_np = j_day_no // 12053

    j_day_no %= 12053

    jy = (
        979
        + 33 * j_np
        + 4 * (j_day_no // 1461)
    )

    j_day_no %= 1461

    if j_day_no >= 366:

        jy += (
            j_day_no - 1
        ) // 365

        j_day_no = (
            j_day_no - 1
        ) % 365

    jm = 0

    while (
        jm < 11
        and
        j_day_no >= j_days_in_month[jm]
    ):

        j_day_no -= j_days_in_month[jm]

        jm += 1

    jm += 1

    jd = j_day_no + 1

    return jy, jm, jd


def iran_now():

    return datetime.now(
        timezone.utc
    ).astimezone(
        TEHRAN_TZ
    )


def jalali_datetime_string():

    now = iran_now()

    jy, jm, jd = gregorian_to_jalali(
        now.year,
        now.month,
        now.day
    )

    return (
        f"{jy:04d}/"
        f"{jm:02d}/"
        f"{jd:02d} "
        f"{now.hour:02d}:"
        f"{now.minute:02d}:"
        f"{now.second:02d}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if (
        not TELEGRAM_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials not configured."
        )

        print(text)

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        print(
            "Telegram:",
            response.status_code
        )

        if not response.ok:

            print(
                response.text
            )

        return response.ok

    except Exception as e:

        print(
            "Telegram ERROR:",
            e
        )

        return False


# ============================================================
# SYMBOL DISCOVERY
# ============================================================

def get_top_symbols():

    print(
        "Loading Kraken Futures markets..."
    )

    try:

        markets = exchange.load_markets()

    except Exception as e:

        print(
            "MARKET LOAD ERROR:",
            e
        )

        return []

    candidates = []

    for symbol, market in markets.items():

        try:

            if not market.get(
                "active",
                True
            ):
                continue

            if market.get(
                "type"
            ) != "swap":
                continue

            quote = market.get(
                "quote",
                ""
            )

            if quote not in [
                "USD",
                "USDT"
            ]:
                continue

            base = market.get(
                "base",
                ""
            )

            if not base:
                continue

            candidates.append(
                symbol
            )

        except Exception:

            continue

    print(
        f"Candidate markets: {len(candidates)}"
    )

    tickers = {}

    try:

        tickers = exchange.fetch_tickers(
            candidates
        )

    except Exception as e:

        print(
            "fetch_tickers failed:",
            e
        )

        for symbol in candidates:

            try:

                tickers[symbol] = (
                    exchange.fetch_ticker(
                        symbol
                    )
                )

                time.sleep(0.05)

            except Exception:

                pass

    ranked = []

    for symbol in candidates:

        ticker = tickers.get(
            symbol
        )

        if not ticker:
            continue

        quote_volume = ticker.get(
            "quoteVolume"
        )

        if quote_volume is None:

            base_volume = ticker.get(
                "baseVolume"
            )

            last = ticker.get(
                "last"
            )

            if (
                base_volume
                and
                last
            ):

                quote_volume = (
                    base_volume * last
                )

        if not quote_volume:
            continue

        try:

            quote_volume = float(
                quote_volume
            )

        except Exception:

            continue

        ranked.append(
            (
                symbol,
                quote_volume
            )
        )

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    result = [
        symbol
        for symbol, volume
        in ranked[:TOP_N]
    ]

    print("TOP symbols:")

    for i, symbol in enumerate(
        result,
        1
    ):

        print(
            i,
            symbol
        )

    return result


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol):

    try:

        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=TIMEFRAME,
            limit=OHLCV_LIMIT
        )

        if (
            not data
            or
            len(data) < 30
        ):

            return None

        df = pd.DataFrame(
            data,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True
        )

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

        df.dropna(
            inplace=True
        )

        # Remove currently forming candle
        df = df.iloc[:-1].copy()

        if len(df) < 30:
            return None

        return df

    except Exception as e:

        print(
            f"OHLCV ERROR {symbol}: {e}"
        )

        return None


# ============================================================
# CURRENT MARKET PRICE
# ============================================================

def get_current_market_price(symbol):

    try:

        ticker = exchange.fetch_ticker(
            symbol
        )

        last = ticker.get(
            "last"
        )

        if last is not None:

            return float(last)

        bid = ticker.get(
            "bid"
        )

        ask = ticker.get(
            "ask"
        )

        if (
            bid is not None
            and
            ask is not None
        ):

            return (
                float(bid)
                +
                float(ask)
            ) / 2

        return None

    except Exception as e:

        print(
            f"CURRENT PRICE ERROR {symbol}: {e}"
        )

        return None


# ============================================================
# SWINGS
# ============================================================

def find_swings(df):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    start = SWING_LEFT
    end = len(df) - SWING_RIGHT

    for i in range(
        start,
        end
    ):

        left_high = max(
            h[
                i - SWING_LEFT:i
            ]
        )

        right_high = max(
            h[
                i + 1:
                i + 1 + SWING_RIGHT
            ]
        )

        if (
            h[i] > left_high
            and
            h[i] >= right_high
        ):

            highs.append(i)

        left_low = min(
            l[
                i - SWING_LEFT:i
            ]
        )

        right_low = min(
            l[
                i + 1:
                i + 1 + SWING_RIGHT
            ]
        )

        if (
            l[i] < left_low
            and
            l[i] <= right_low
        ):

            lows.append(i)

    return highs, lows


# ============================================================
# MARKET STRUCTURE
# ============================================================

def market_structure(df):

    swing_highs, swing_lows = (
        find_swings(df)
    )

    if (
        len(swing_highs) < 2
        or
        len(swing_lows) < 2
    ):

        return None

    h1 = swing_highs[-1]
    h0 = swing_highs[-2]

    l1 = swing_lows[-1]
    l0 = swing_lows[-2]

    last_close = float(
        df["close"].iloc[-1]
    )

    last_high = float(
        df["high"].iloc[-1]
    )

    last_low = float(
        df["low"].iloc[-1]
    )

    prev_close = float(
        df["close"].iloc[-2]
    )

    structure = "RANGE"

    if (
        df["high"].iloc[h1]
        >
        df["high"].iloc[h0]
        and
        df["low"].iloc[l1]
        >
        df["low"].iloc[l0]
    ):

        structure = "BULLISH"

    elif (
        df["high"].iloc[h1]
        <
        df["high"].iloc[h0]
        and
        df["low"].iloc[l1]
        <
        df["low"].iloc[l0]
    ):

        structure = "BEARISH"

    resistance = float(
        df["high"].iloc[h1]
    )

    support = float(
        df["low"].iloc[l1]
    )

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    bos = None

    if (
        prev_close <= resistance
        and
        last_close > resistance
    ):

        bos = "BULLISH"

    elif (
        prev_close >= support
        and
        last_close < support
    ):

        bos = "BEARISH"

    # --------------------------------------------------------
    # CHOCH
    # --------------------------------------------------------

    choch = None

    if (
        structure == "BEARISH"
        and
        last_close > resistance
    ):

        choch = "BULLISH"

    elif (
        structure == "BULLISH"
        and
        last_close < support
    ):

        choch = "BEARISH"

    return {
        "structure": structure,
        "bos": bos,
        "choch": choch,
        "resistance": resistance,
        "support": support,
        "swing_high_index": h1,
        "swing_low_index": l1,
        "swing_high": float(
            df["high"].iloc[h1]
        ),
        "swing_low": float(
            df["low"].iloc[l1]
        ),
        "last_close": last_close,
        "last_high": last_high,
        "last_low": last_low
    }


# ============================================================
# CANDLE ANALYSIS
# ============================================================

def candle_confirmation(df):

    c = df.iloc[-1]
    p = df.iloc[-2]

    body = abs(
        c["close"] - c["open"]
    )

    candle_range = (
        c["high"] - c["low"]
    )

    if candle_range <= 0:

        return {
            "bullish": False,
            "bearish": False,
            "strength": 0,
            "name": "NONE"
        }

    upper_wick = (
        c["high"]
        -
        max(
            c["open"],
            c["close"]
        )
    )

    lower_wick = (
        min(
            c["open"],
            c["close"]
        )
        -
        c["low"]
    )

    body_ratio = (
        body / candle_range
    )

    bullish = False
    bearish = False

    strength = 0
    name = "NONE"

    # Bullish engulfing
    if (
        c["close"] > c["open"]
        and
        p["close"] < p["open"]
        and
        c["open"] <= p["close"]
        and
        c["close"] >= p["open"]
    ):

        bullish = True
        strength = 15
        name = "BULLISH ENGULFING"

    # Bearish engulfing
    elif (
        c["close"] < c["open"]
        and
        p["close"] > p["open"]
        and
        c["open"] >= p["close"]
        and
        c["close"] <= p["open"]
    ):

        bearish = True
        strength = 15
        name = "BEARISH ENGULFING"

    # Strong bullish
    elif (
        c["close"] > c["open"]
        and
        body_ratio >= 0.65
    ):

        bullish = True
        strength = 12
        name = "STRONG BULLISH"

    # Strong bearish
    elif (
        c["close"] < c["open"]
        and
        body_ratio >= 0.65
    ):

        bearish = True
        strength = 12
        name = "STRONG BEARISH"

    # Bullish rejection
    elif (
        lower_wick > body * 1.5
        and
        c["close"] > c["open"]
    ):

        bullish = True
        strength = 10
        name = "BULLISH REJECTION"

    # Bearish rejection
    elif (
        upper_wick > body * 1.5
        and
        c["close"] < c["open"]
    ):

        bearish = True
        strength = 10
        name = "BEARISH REJECTION"

    return {
        "bullish": bullish,
        "bearish": bearish,
        "strength": strength,
        "name": name
    }


# ============================================================
# PULLBACK
# ============================================================

def detect_pullback(
    df,
    structure
):

    if not structure:

        return {
            "bullish": False,
            "bearish": False,
            "distance": 999
        }

    close = float(
        df["close"].iloc[-1]
    )

    support = structure["support"]
    resistance = structure["resistance"]

    candle_range = (
        float(
            df["high"].iloc[-1]
        )
        -
        float(
            df["low"].iloc[-1]
        )
    )

    if candle_range <= 0:

        candle_range = (
            close * 0.001
        )

    tolerance = (
        candle_range * 2.5
    )

    bullish_pullback = (
        structure["structure"]
        ==
        "BULLISH"
        and
        abs(
            close - support
        )
        <= tolerance
    )

    bearish_pullback = (
        structure["structure"]
        ==
        "BEARISH"
        and
        abs(
            close - resistance
        )
        <= tolerance
    )

    return {
        "bullish": bullish_pullback,
        "bearish": bearish_pullback,
        "distance": min(
            abs(close - support),
            abs(close - resistance)
        )
    }


# ============================================================
# VOLUME
# ============================================================

def volume_score(df):

    recent = float(
        df["volume"].iloc[-1]
    )

    avg = float(
        df["volume"].iloc[-21:-1].mean()
    )

    if avg <= 0:
        return 0, 0

    ratio = (
        recent / avg
    )

    if ratio >= 2.0:
        return 10, ratio

    if ratio >= 1.5:
        return 7, ratio

    if ratio >= 1.2:
        return 4, ratio

    return 0, ratio


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    df
):

    structure = market_structure(
        df
    )

    if not structure:
        return None

    candle = candle_confirmation(
        df
    )

    pullback = detect_pullback(
        df,
        structure
    )

    vol_points, vol_ratio = (
        volume_score(df)
    )

    score_buy = 0
    score_sell = 0

    reasons_buy = []
    reasons_sell = []

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if structure["structure"] == "BULLISH":

        score_buy += 25

        reasons_buy.append(
            "Bullish Structure"
        )

    elif structure["structure"] == "BEARISH":

        score_sell += 25

        reasons_sell.append(
            "Bearish Structure"
        )

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    if structure["bos"] == "BULLISH":

        score_buy += 20

        reasons_buy.append(
            "BOS"
        )

    elif structure["bos"] == "BEARISH":

        score_sell += 20

        reasons_sell.append(
            "BOS"
        )

    # --------------------------------------------------------
    # CHOCH
    # --------------------------------------------------------

    if structure["choch"] == "BULLISH":

        score_buy += 15

        reasons_buy.append(
            "CHOCH"
        )

    elif structure["choch"] == "BEARISH":

        score_sell += 15

        reasons_sell.append(
            "CHOCH"
        )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    if pullback["bullish"]:

        score_buy += 20

        reasons_buy.append(
            "Pullback"
        )

    if pullback["bearish"]:

        score_sell += 20

        reasons_sell.append(
            "Pullback"
        )

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    if candle["bullish"]:

        score_buy += candle["strength"]

        reasons_buy.append(
            candle["name"]
        )

    if candle["bearish"]:

        score_sell += candle["strength"]

        reasons_sell.append(
            candle["name"]
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    score_buy += vol_points
    score_sell += vol_points

    if vol_points:

        volume_reason = (
            f"Volume x{vol_ratio:.1f}"
        )

        reasons_buy.append(
            volume_reason
        )

        reasons_sell.append(
            volume_reason
        )

    # --------------------------------------------------------
    # FINAL SCORE
    # --------------------------------------------------------

    score_buy = min(
        score_buy,
        100
    )

    score_sell = min(
        score_sell,
        100
    )

    if score_buy >= score_sell:

        side = "BUY"
        score = score_buy
        reasons = reasons_buy

    else:

        side = "SELL"
        score = score_sell
        reasons = reasons_sell

    entry = float(
        df["close"].iloc[-1]
    )

    # --------------------------------------------------------
    # STRUCTURAL SL / TP
    # --------------------------------------------------------

    if side == "BUY":

        sl = structure["swing_low"]

        if sl >= entry:
            return None

        risk = entry - sl

        tp = (
            entry
            +
            risk * RR
        )

    else:

        sl = structure["swing_high"]

        if sl <= entry:
            return None

        risk = sl - entry

        tp = (
            entry
            -
            risk * RR
        )

    if risk <= 0:
        return None

    risk_pct = (
        risk / entry
    ) * 100

    # Avoid absurdly wide SL
    if risk_pct > 8:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "score": int(score),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_pct": risk_pct,
        "structure": structure,
        "candle": candle,
        "volume_ratio": vol_ratio,
        "reasons": reasons
    }


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    trades = history.get(
        "trades",
        []
    )

    total = len(trades)

    wins = sum(
        1
        for x in trades
        if x.get("result") == "WIN"
    )

    losses = sum(
        1
        for x in trades
        if x.get("result") == "LOSS"
    )

    neutral = sum(
        1
        for x in trades
        if x.get("result") == "NEUTRAL"
    )

    pnl = sum(
        float(
            x.get(
                "pnl_pct",
                0
            )
        )
        for x in trades
    )

    r_total = sum(
        float(
            x.get(
                "r",
                0
            )
        )
        for x in trades
    )

    durations = []

    win_durations = []

    loss_durations = []

    for trade in trades:

        duration = trade.get(
            "duration_seconds"
        )

        if duration is None:

            opened_at = trade.get(
                "opened_at"
            )

            closed_at = trade.get(
                "closed_at"
            )

            opened = parse_datetime(
                opened_at
            )

            closed = parse_datetime(
                closed_at
            )

            if (
                opened
                and
                closed
            ):

                duration = max(
                    0,
                    int(
                        (
                            closed
                            -
                            opened
                        ).total_seconds()
                    )
                )

        if duration is None:
            continue

        try:
            duration = int(duration)

        except Exception:
            continue

        durations.append(
            duration
        )

        if trade.get("result") == "WIN":

            win_durations.append(
                duration
            )

        elif trade.get("result") == "LOSS":

            loss_durations.append(
                duration
            )

    avg_duration = (
        sum(durations) / len(durations)
        if durations
        else 0
    )

    avg_win_duration = (
        sum(win_durations)
        /
        len(win_durations)
        if win_durations
        else 0
    )

    avg_loss_duration = (
        sum(loss_durations)
        /
        len(loss_durations)
        if loss_durations
        else 0
    )

    wr = (
        wins / total * 100
        if total
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "pnl": pnl,
        "r": r_total,
        "wr": wr,
        "avg_duration": avg_duration,
        "avg_win_duration": avg_win_duration,
        "avg_loss_duration": avg_loss_duration
    }


# ============================================================
# LIVE PNL
# ============================================================

def calculate_live_pnl(
    trade,
    current_price
):

    if current_price is None:
        return None

    try:

        entry = float(
            trade["entry"]
        )

        current_price = float(
            current_price
        )

    except Exception:

        return None

    if entry <= 0:
        return None

    if trade["side"] == "BUY":

        return (
            (
                current_price
                -
                entry
            )
            /
            entry
        ) * 100

    return (
        (
            entry
            -
            current_price
        )
        /
        entry
    ) * 100


# ============================================================
# SL / TP PERCENTAGES
# ============================================================

def trade_levels_percent(
    trade
):

    try:

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

    except Exception:

        return 0.0, 0.0

    if entry <= 0:

        return 0.0, 0.0

    if trade["side"] == "BUY":

        sl_pct = (
            (sl - entry)
            /
            entry
        ) * 100

        tp_pct = (
            (tp - entry)
            /
            entry
        ) * 100

    else:

        sl_pct = (
            (entry - sl)
            /
            entry
        ) * 100

        tp_pct = (
            (entry - tp)
            /
            entry
        ) * 100

    return sl_pct, tp_pct


# ============================================================
# OPEN SYMBOL CHECK
# ============================================================

def has_open_symbol(symbol):

    for trade in state.get(
        "open",
        {}
    ).values():

        if trade.get(
            "symbol"
        ) == symbol:

            return True

    return False


# ============================================================
# WAITING SYMBOL CHECK
# ============================================================

def has_waiting_symbol(
    symbol
):

    for trade in state.get(
        "waiting",
        {}
    ).values():

        if trade.get(
            "symbol"
        ) == symbol:

            return True

    return False


# ============================================================
# SIGNAL KEY
# ============================================================

def signal_key(
    symbol,
    side
):

    return (
        f"{symbol}:{side}"
    )


# ============================================================
# DUPLICATE CHECK
# ============================================================

def is_duplicate_signal(
    candidate
):

    symbol = candidate["symbol"]
    side = candidate["side"]

    candle_time = (
        candidate[
            "structure"
        ].get(
            "last_candle_time",
            ""
        )
    )

    key = signal_key(
        symbol,
        side
    )

    # --------------------------------------------------------
    # Check OPEN
    # --------------------------------------------------------

    for trade in state.get(
        "open",
        {}
    ).values():

        if (
            trade.get("symbol")
            ==
            symbol
            and
            trade.get("side")
            ==
            side
        ):

            existing_candle = trade.get(
                "signal_candle_time",
                ""
            )

            if (
                not DUPLICATE_CANDLE_PROTECTION
                or
                existing_candle
                ==
                candle_time
            ):

                return True

    # --------------------------------------------------------
    # Check WAITING
    # --------------------------------------------------------

    for trade in state.get(
        "waiting",
        {}
    ).values():

        if (
            trade.get("symbol")
            ==
            symbol
            and
            trade.get("side")
            ==
            side
        ):

            existing_candle = trade.get(
                "signal_candle_time",
                ""
            )

            if (
                not DUPLICATE_CANDLE_PROTECTION
                or
                existing_candle
                ==
                candle_time
            ):

                return True

    # --------------------------------------------------------
    # Check last signal
    # --------------------------------------------------------

    previous = state[
        "last_signals"
    ].get(key)

    if previous:

        try:

            previous_ts = pd.Timestamp(
                previous
            )

            current_ts = pd.Timestamp(
                candle_time
            )

            diff = (
                current_ts
                -
                previous_ts
            ).total_seconds()

            if diff < (
                SIGNAL_COOLDOWN_CANDLES
                * 5
                * 60
            ):

                return True

        except Exception:

            pass

    return False


# ============================================================
# BUILD TRADE OBJECT
# ============================================================

def candidate_to_trade(
    candidate
):

    symbol = candidate["symbol"]
    side = candidate["side"]

    candle_time = (
        candidate[
            "structure"
        ].get(
            "last_candle_time",
            ""
        )
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    trade_id = (
        f"{symbol}_"
        f"{side}_"
        f"{candle_time}_"
        f"{int(time.time() * 1000)}"
    )

    return {
        "id": trade_id,
        "symbol": symbol,
        "side": side,
        "entry": candidate["entry"],
        "sl": candidate["sl"],
        "tp": candidate["tp"],
        "score": candidate["score"],
        "reasons": candidate.get(
            "reasons",
            []
        ),
        "signal_candle_time": candle_time,
        "opened_at": now,
        "queued_at": now
    }


# ============================================================
# ADD WAITING
# ============================================================

def add_waiting(
    candidate
):

    symbol = candidate["symbol"]

    # Never queue another trade for
    # a symbol already open
    if has_open_symbol(symbol):

        return False

    # Never duplicate waiting symbol
    if has_waiting_symbol(symbol):

        return False

    # Duplicate signal protection
    if is_duplicate_signal(
        candidate
    ):

        return False

    if len(
        state.get(
            "waiting",
            {}
        )
    ) >= MAX_WAITING_TRADES:

        # Remove lowest score waiting
        waiting_items = list(
            state[
                "waiting"
            ].items()
        )

        waiting_items.sort(
            key=lambda x:
            x[1].get(
                "score",
                0
            )
        )

        if waiting_items:

            lowest_id, lowest = (
                waiting_items[0]
            )

            if candidate["score"] <= (
                lowest.get(
                    "score",
                    0
                )
            ):

                return False

            del state[
                "waiting"
            ][
                lowest_id
            ]

    trade = candidate_to_trade(
        candidate
    )

    state[
        "waiting"
    ][
        trade["id"]
    ] = trade

    key = signal_key(
        symbol,
        candidate["side"]
    )

    state[
        "last_signals"
    ][
        key
    ] = candidate[
        "structure"
    ].get(
        "last_candle_time",
        ""
    )

    print(
        f"WAITING "
        f"{symbol} "
        f"{candidate['side']} "
        f"Score={candidate['score']}"
    )

    return True


# ============================================================
# OPEN TRADE
# ============================================================

def open_candidate(
    candidate
):

    symbol = candidate["symbol"]

    if len(
        state.get(
            "open",
            {}
        )
    ) >= MAX_OPEN_TRADES:

        return False

    if has_open_symbol(symbol):

        return False

    if is_duplicate_signal(
        candidate
    ):

        return False

    trade = candidate_to_trade(
        candidate
    )

    # queued_at is not used as opening time
    trade["opened_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    state[
        "open"
    ][
        trade["id"]
    ] = trade

    key = signal_key(
        symbol,
        candidate["side"]
    )

    state[
        "last_signals"
    ][
        key
    ] = candidate[
        "structure"
    ].get(
        "last_candle_time",
        ""
    )

    print(
        f"OPEN "
        f"{symbol} "
        f"{candidate['side']} "
        f"Score={candidate['score']}"
    )

    return True


# ============================================================
# PROMOTE WAITING
# ============================================================

def promote_waiting():

    promoted = []

    while (
        len(
            state.get(
                "open",
                {}
            )
        )
        <
        MAX_OPEN_TRADES
        and
        state.get(
            "waiting",
            {}
        )
    ):

        candidates = list(
            state[
                "waiting"
            ].items()
        )

        # Highest Score first
        candidates.sort(
            key=lambda x: (
                x[1].get(
                    "score",
                    0
                ),
                x[1].get(
                    "queued_at",
                    ""
                )
            ),
            reverse=True
        )

        selected_id = None
        selected_trade = None

        for waiting_id, waiting_trade in candidates:

            symbol = waiting_trade.get(
                "symbol"
            )

            if has_open_symbol(
                symbol
            ):

                continue

            selected_id = waiting_id
            selected_trade = waiting_trade
            break

        if selected_trade is None:
            break

        del state[
            "waiting"
        ][
            selected_id
        ]

        # ----------------------------------------------------
        # Re-check duplicate protection.
        # A waiting trade may be stale.
        # ----------------------------------------------------

        if has_open_symbol(
            selected_trade["symbol"]
        ):

            continue

        state[
            "open"
        ][
            selected_id
        ] = selected_trade

        selected_trade["opened_at"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        selected_trade["promoted_from_waiting"] = True

        promoted.append(
            selected_trade
        )

        print(
            f"PROMOTED "
            f"{selected_trade['symbol']} "
            f"Score={selected_trade.get('score', 0)}"
        )

    return promoted


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(
    results
):

    if not state.get("open"):

        return []

    closed_events = []

    remaining = {}

    for trade_id, trade in (
        state["open"].items()
    ):

        symbol = trade["symbol"]

        result = results.get(
            symbol
        )

        if not result:

            remaining[
                trade_id
            ] = trade

            continue

        # IMPORTANT:
        # Closing logic uses last CLOSED 5M candle
        price = result[
            "structure"
        ][
            "last_close"
        ]

        side = trade["side"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        outcome = None
        pnl_pct = 0
        r = 0

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "BUY":

            if price <= sl:

                outcome = "LOSS"

                pnl_pct = (
                    (sl - entry)
                    /
                    entry
                ) * 100

                r = -1

            elif price >= tp:

                outcome = "WIN"

                pnl_pct = (
                    (tp - entry)
                    /
                    entry
                ) * 100

                r = RR

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if price >= sl:

                outcome = "LOSS"

                pnl_pct = (
                    (entry - sl)
                    /
                    entry
                ) * 100

                r = -1

            elif price <= tp:

                outcome = "WIN"

                pnl_pct = (
                    (entry - tp)
                    /
                    entry
                ) * 100

                r = RR

        # ----------------------------------------------------
        # CLOSE
        # ----------------------------------------------------

        if outcome:

            closed = dict(
                trade
            )

            closed_at = datetime.now(
                timezone.utc
            )

            opened_at = parse_datetime(
                trade.get(
                    "opened_at"
                )
            )

            if opened_at:

                duration_sec = max(
                    0,
                    int(
                        (
                            closed_at
                            -
                            opened_at
                        ).total_seconds()
                    )
                )

            else:

                duration_sec = 0

            closed["result"] = outcome
            closed["exit"] = price
            closed["pnl_pct"] = pnl_pct
            closed["r"] = r

            closed["closed_at"] = (
                closed_at.isoformat()
            )

            closed[
                "duration_seconds"
            ] = duration_sec

            closed[
                "duration"
            ] = format_duration_from_seconds(
                duration_sec
            )

            history[
                "trades"
            ].append(
                closed
            )

            closed_events.append(
                closed
            )

            print(
                f"CLOSED "
                f"{symbol} "
                f"{side} "
                f"{outcome} "
                f"Duration="
                f"{closed['duration']}"
            )

        else:

            remaining[
                trade_id
            ] = trade

    state[
        "open"
    ] = remaining

    return closed_events


# ============================================================
# CLEAN WAITING QUEUE
# ============================================================

def clean_waiting_queue():

    waiting = state.get(
        "waiting",
        {}
    )

    if not waiting:
        return

    cleaned = {}

    # Only one waiting trade per symbol
    used_symbols = set()

    # Highest score first
    items = list(
        waiting.items()
    )

    items.sort(
        key=lambda x: (
            x[1].get(
                "score",
                0
            ),
            x[1].get(
                "queued_at",
                ""
            )
        ),
        reverse=True
    )

    for trade_id, trade in items:

        symbol = trade.get(
            "symbol"
        )

        if not symbol:
            continue

        if has_open_symbol(
            symbol
        ):
            continue

        if symbol in used_symbols:
            continue

        used_symbols.add(
            symbol
        )

        cleaned[
            trade_id
        ] = trade

        if len(cleaned) >= MAX_WAITING_TRADES:
            break

    state[
        "waiting"
    ] = cleaned


# ============================================================
# CREATE EVENTS
# ============================================================

def process_candidates(
    candidates
):

    events = []
    waiting_events = []

    # Highest score first
    candidates = sorted(
        candidates,
        key=lambda x: x["score"],
        reverse=True
    )

    # --------------------------------------------------------
    # First promote existing WAITING
    # --------------------------------------------------------

    promoted = promote_waiting()

    # --------------------------------------------------------
    # Process fresh candidates
    # --------------------------------------------------------

    for candidate in candidates:

        if candidate["score"] < MIN_SCORE:
            continue

        symbol = candidate["symbol"]

        # One trade per symbol
        if has_open_symbol(symbol):
            continue

        # If already waiting, don't duplicate
        if has_waiting_symbol(symbol):
            continue

        # ----------------------------------------------------
        # OPEN SLOT AVAILABLE
        # ----------------------------------------------------

        if len(
            state["open"]
        ) < MAX_OPEN_TRADES:

            created = open_candidate(
                candidate
            )

            if created:

                events.append(
                    candidate
                )

            continue

        # ----------------------------------------------------
        # NO OPEN SLOT
        # ----------------------------------------------------

        queued = add_waiting(
            candidate
        )

        if queued:

            waiting_events.append(
                candidate
            )

    # --------------------------------------------------------
    # If fresh signals filled slots, try waiting again
    # --------------------------------------------------------

    more_promoted = promote_waiting()

    promoted.extend(
        more_promoted
    )

    # --------------------------------------------------------
    # Cleanup
    # --------------------------------------------------------

    clean_waiting_queue()

    return (
        events,
        waiting_events,
        promoted
    )


# ============================================================
# SYMBOL FORMAT
# ============================================================

def clean_symbol(symbol):

    return (
        symbol
        .replace(
            ":USD",
            ""
        )
        .replace(
            ":USDT",
            ""
        )
        .replace(
            "/USD",
            "/USDT"
        )
    )


# ============================================================
# BUILD TRADE BLOCK
# ============================================================

def build_trade_block(
    trade,
    results
):

    symbol = clean_symbol(
        trade["symbol"]
    )

    emoji = (
        "🟢"
        if trade["side"] == "BUY"
        else "🔴"
    )

    # --------------------------------------------------------
    # CURRENT PRICE
    # --------------------------------------------------------

    current_price = (
        get_current_market_price(
            trade["symbol"]
        )
    )

    if current_price is None:

        result = results.get(
            trade["symbol"]
        )

        if result:

            current_price = (
                result[
                    "structure"
                ][
                    "last_close"
                ]
            )

        else:

            current_price = float(
                trade["entry"]
            )

    # --------------------------------------------------------
    # LIVE PNL
    # --------------------------------------------------------

    live_pnl = calculate_live_pnl(
        trade,
        current_price
    )

    sl_pct, tp_pct = (
        trade_levels_percent(
            trade
        )
    )

    if live_pnl is not None:

        pnl_emoji = (
            "🟢"
            if live_pnl >= 0
            else "🔴"
        )

        live_pnl_text = fmt_pct(
            live_pnl,
            True
        )

    else:

        pnl_emoji = "⚪"
        live_pnl_text = "-"

    duration = format_duration(
        trade.get(
            "opened_at"
        )
    )

    lines = []

    lines.append(
        f"{emoji} <b>{symbol}</b> "
        f"| {trade['side']}"
    )

    lines.append(
        f"💰 Entry: "
        f"{fmt_price(trade['entry'])}"
    )

    lines.append(
        f"📍 Now: "
        f"{fmt_price(current_price)}"
    )

    lines.append(
        f"🛑 SL: "
        f"{fmt_price(trade['sl'])} "
        f"({fmt_pct(sl_pct, True)})"
    )

    lines.append(
        f"🎯 TP: "
        f"{fmt_price(trade['tp'])} "
        f"({fmt_pct(tp_pct, True)})"
    )

    lines.append(
        f"{pnl_emoji} "
        f"<b>Live P&L: "
        f"{live_pnl_text}</b>"
    )

    lines.append(
        f"📊 Score: "
        f"{trade.get('score', 0)}/100"
    )

    lines.append(
        f"⏱ Duration: "
        f"<b>{duration}</b>"
    )

    return "\n".join(
        lines
    )


# ============================================================
# TELEGRAM REPORT
# ============================================================

def build_report(
    results,
    events,
    waiting_events,
    promoted,
    elapsed
):

    perf = performance()

    now = jalali_datetime_string()

    lines = []

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    lines.append(
        "📡 <b>CRYPTO PRICE ACTION REPORT</b>"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        f"⏱ <b>5m CLOSED | TOP {TOP_N}</b>"
    )

    lines.append(
        "🤖 <b>PRICE ACTION | RR 1:1</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "📊 <b>PERFORMANCE</b>"
    )

    lines.append(
        f"Trades {perf['total']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['neutral']}"
    )

    lines.append(
        f"🏆 WR {perf['wr']:.1f}% | "
        f"P&L {fmt_pct(perf['pnl'], True)} | "
        f"R {perf['r']:+.2f}"
    )

    if perf["total"]:

        lines.append(
            f"⏱ Avg Duration: "
            f"{format_duration_from_seconds(perf['avg_duration'])}"
        )

        lines.append(
            f"🟢 Avg Win: "
            f"{format_duration_from_seconds(perf['avg_win_duration'])} | "
            f"🔴 Avg Loss: "
            f"{format_duration_from_seconds(perf['avg_loss_duration'])}"
        )

    # --------------------------------------------------------
    # POSITION LIMIT
    # --------------------------------------------------------

    lines.append(
        f"🔒 Open Limit: "
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append(
        f"⚡ <b>THIS RUN: "
        f"{len(events)} EVENTS</b>"
    )

    if events:

        for event in events:

            symbol = clean_symbol(
                event["symbol"]
            )

            emoji = (
                "🟢"
                if event["side"] == "BUY"
                else "🔴"
            )

            entry = float(
                event["entry"]
            )

            sl = float(
                event["sl"]
            )

            tp = float(
                event["tp"]
            )

            if event["side"] == "BUY":

                sl_pct = (
                    (sl - entry)
                    /
                    entry
                ) * 100

                tp_pct = (
                    (tp - entry)
                    /
                    entry
                ) * 100

            else:

                sl_pct = (
                    (entry - sl)
                    /
                    entry
                ) * 100

                tp_pct = (
                    (entry - tp)
                    /
                    entry
                ) * 100

            lines.append("")

            lines.append(
                f"{emoji} <b>{symbol}</b>"
            )

            lines.append(
                f"{emoji} "
                f"<b>{event['side']}</b>"
            )

            lines.append(
                f"💰 Entry: "
                f"{fmt_price(entry)}"
            )

            lines.append(
                f"🛑 SL: "
                f"{fmt_price(sl)} "
                f"({fmt_pct(sl_pct, True)})"
            )

            lines.append(
                f"🎯 TP: "
                f"{fmt_price(tp)} "
                f"({fmt_pct(tp_pct, True)})"
            )

            lines.append(
                f"📊 Score: "
                f"{event['score']}/100"
            )

            reasons = " + ".join(
                event["reasons"][:4]
            )

            lines.append(
                f"🏗 {reasons}"
            )

    else:

        lines.append(
            "⚪ None"
        )

    # --------------------------------------------------------
    # PROMOTED
    # --------------------------------------------------------

    if promoted:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            f"⬆️ <b>PROMOTED FROM WAITING: "
            f"{len(promoted)}</b>"
        )

        for trade in promoted:

            symbol = clean_symbol(
                trade["symbol"]
            )

            emoji = (
                "🟢"
                if trade["side"] == "BUY"
                else "🔴"
            )

            lines.append(
                f"{emoji} {symbol} | "
                f"{trade['side']} | "
                f"Score {trade.get('score', 0)}/100"
            )

    # --------------------------------------------------------
    # WAITING EVENTS
    # --------------------------------------------------------

    if waiting_events:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            f"⏳ <b>NEW WAITING: "
            f"{len(waiting_events)}</b>"
        )

        for candidate in waiting_events:

            symbol = clean_symbol(
                candidate["symbol"]
            )

            emoji = (
                "🟢"
                if candidate["side"] == "BUY"
                else "🔴"
            )

            lines.append(
                f"{emoji} {symbol} | "
                f"{candidate['side']} | "
                f"Score {candidate['score']}/100"
            )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    opens = state.get(
        "open",
        {}
    )

    lines.append(
        f"📂 <b>OPEN: "
        f"{len(opens)}/{MAX_OPEN_TRADES}</b>"
    )

    if opens:

        open_list = list(
            opens.values()
        )

        open_list.sort(
            key=lambda x:
            x.get(
                "score",
                0
            ),
            reverse=True
        )

        for trade in open_list:

            lines.append("")

            lines.append(
                build_trade_block(
                    trade,
                    results
                )
            )

    else:

        lines.append(
            "⚪ None"
        )

    # --------------------------------------------------------
    # WAITING QUEUE
    # --------------------------------------------------------

    waiting = state.get(
        "waiting",
        {}
    )

    if waiting:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            f"⏳ <b>WAITING: "
            f"{len(waiting)}</b>"
        )

        waiting_list = list(
            waiting.values()
        )

        waiting_list.sort(
            key=lambda x:
            x.get(
                "score",
                0
            ),
            reverse=True
        )

        for i, trade in enumerate(
            waiting_list,
            1
        ):

            symbol = clean_symbol(
                trade["symbol"]
            )

            emoji = (
                "🟢"
                if trade["side"] == "BUY"
                else "🔴"
            )

            queued_duration = format_duration(
                trade.get(
                    "queued_at"
                )
            )

            lines.append(
                f"{i}. {emoji} "
                f"<b>{symbol}</b> | "
                f"{trade['side']} | "
                f"Score "
                f"{trade.get('score', 0)}/100 | "
                f"⏱ {queued_duration}"
            )

    # --------------------------------------------------------
    # SCAN TIME
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"⚙️ Scan: "
        f"{elapsed:.1f}s"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    print(
        "=" * 60
    )

    print(
        "CRYPTO PRICE ACTION SCANNER v1.3"
    )

    print(
        "TIMEFRAME:",
        TIMEFRAME
    )

    print(
        "TOP:",
        TOP_N
    )

    print(
        "MAX OPEN:",
        MAX_OPEN_TRADES
    )

    print(
        "MAX PER SYMBOL:",
        MAX_TRADES_PER_SYMBOL
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # CLEAN OLD WAITING DATA
    # --------------------------------------------------------

    clean_waiting_queue()

    # --------------------------------------------------------
    # GET SYMBOLS
    # --------------------------------------------------------

    symbols = get_top_symbols()

    if not symbols:

        send_telegram(
            "⚠️ <b>PRICE ACTION SCANNER</b>\n\n"
            "Kraken Futures market list could not be loaded."
        )

        return

    results = {}

    candidates = []

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    for index, symbol in enumerate(
        symbols,
        1
    ):

        print(
            f"[{index}/{len(symbols)}] "
            f"{symbol}"
        )

        df = fetch_ohlcv(
            symbol
        )

        if df is None:
            continue

        candle_time = (
            df[
                "timestamp"
            ].iloc[-1]
        )

        analysis = analyze_symbol(
            symbol,
            df
        )

        if not analysis:
            continue

        analysis[
            "structure"
        ][
            "last_candle_time"
        ] = candle_time.isoformat()

        results[
            symbol
        ] = analysis

        if analysis["score"] >= MIN_SCORE:

            candidates.append(
                analysis
            )

    # --------------------------------------------------------
    # UPDATE OPEN TRADES
    # --------------------------------------------------------

    closed_events = (
        update_open_trades(
            results
        )
    )

    # --------------------------------------------------------
    # PROCESS CANDIDATES
    # --------------------------------------------------------

    (
        events,
        waiting_events,
        promoted
    ) = process_candidates(
        candidates
    )

    # --------------------------------------------------------
    # AFTER CLOSURES, PROMOTE AGAIN
    # --------------------------------------------------------

    if closed_events:

        extra_promoted = (
            promote_waiting()
        )

        promoted.extend(
            extra_promoted
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        HISTORY_FILE,
        history
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    elapsed = (
        time.time()
        -
        start
    )

    report = build_report(
        results,
        events,
        waiting_events,
        promoted,
        elapsed
    )

    print("")

    print(
        "=" * 60
    )

    print(
        report
    )

    print(
        "=" * 60
    )

    send_telegram(
        report
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "FATAL ERROR:",
            repr(e)
        )

        try:

            send_telegram(
                "🚨 <b>PRICE ACTION SCANNER ERROR</b>\n\n"
                f"<code>{str(e)[:1000]}</code>"
            )

        except Exception:

            pass

        raise
