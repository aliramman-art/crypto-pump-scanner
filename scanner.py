# ============================================================
# CRYPTO PRICE ACTION SCANNER v2.2
# ============================================================
# KRAKEN FUTURES
# TOP 30 HIGH-VOLUME CONTRACTS
# 5M CLOSED CANDLES
#
# STRATEGY:
#   Market Structure
#   BOS
#   Pullback
#   Engulfing
#   Momentum
#   Volume
#
# RULES:
#   MIN SCORE      = 80
#   MAX OPEN       = 1
#   RR             = 1:1
#   SIGNAL         = CLOSED 5M CANDLE
#
# STATE:
#   price_action_state.json
#   price_action_trade_history.json
#
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd
import requests


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "5m"

TOP_N = 30

CANDLE_LIMIT = 120

MIN_SCORE = 80

MAX_OPEN_TRADES = 1

RR = 1.0

ATR_PERIOD = 14

SWING_LOOKBACK = 5

VOLUME_LOOKBACK = 20

VOLUME_MIN_RATIO = 1.15

BODY_MIN_RATIO = 0.45

MIN_SL_ATR = 0.35

MAX_SL_ATR = 3.0

SIGNAL_COOLDOWN_CANDLES = 3

STATE_FILE = "price_action_state.json"

HISTORY_FILE = "price_action_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

TEHRAN_TZ = timezone(
    timedelta(hours=3, minutes=30)
)


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[WARN] Could not load {filename}: {e}"
        )

        return default


def save_json(filename, data):

    temp = filename + ".tmp"

    with open(
        temp,
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
        temp,
        filename
    )


# ============================================================
# STATE
# ============================================================

state = load_json(
    STATE_FILE,
    {
        "open": {},
        "last_signals": {}
    }
)

history = load_json(
    HISTORY_FILE,
    []
)

if not isinstance(state, dict):

    state = {
        "open": {},
        "last_signals": {}
    }

if not isinstance(
    state.get("open"),
    dict
):

    state["open"] = {}

if not isinstance(
    state.get("last_signals"),
    dict
):

    state["last_signals"] = {}

if not isinstance(history, list):

    history = []


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):

    try:

        x = float(value)

        if math.isnan(x):
            return default

        if math.isinf(x):
            return default

        return x

    except Exception:

        return default


def clean_symbol(symbol):

    symbol = str(symbol)

    symbol = symbol.replace(
        ":USDT",
        ""
    )

    symbol = symbol.replace(
        ":USD",
        ""
    )

    if symbol.endswith("/USD"):

        symbol = (
            symbol[:-4]
            + "/USDT"
        )

    return symbol


def round_price(price):

    price = safe_float(price)

    if price >= 1000:
        return round(price, 2)

    if price >= 100:
        return round(price, 3)

    if price >= 10:
        return round(price, 4)

    if price >= 1:
        return round(price, 5)

    if price >= 0.1:
        return round(price, 6)

    if price >= 0.01:
        return round(price, 7)

    return round(price, 8)


def percentage_difference(a, b):

    a = safe_float(a)
    b = safe_float(b)

    if b == 0:
        return 0.0

    return (
        (a - b) / b
    ) * 100.0


# ============================================================
# TIME
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def now_tehran():

    return now_utc().astimezone(
        TEHRAN_TZ
    )


def gregorian_to_jalali(
    gy,
    gm,
    gd
):

    g_days = [
        31, 28, 31, 30, 31, 30,
        31, 31, 30, 31, 30, 31
    ]

    if (
        gy % 4 == 0
        and (
            gy % 100 != 0
            or gy % 400 == 0
        )
    ):

        g_days[1] = 29

    gy2 = (
        gy + 1
        if gm > 2
        else gy
    )

    days = (
        355666
        + 365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        + gd
    )

    jy = -1595 + 33 * (
        days // 12053
    )

    days %= 12053

    jy += 4 * (
        days // 1461
    )

    days %= 1461

    if days > 365:

        jy += (
            days - 1
        ) // 365

        days = (
            days - 1
        ) % 365

    if days < 186:

        jm = 1 + days // 31

        jd = 1 + days % 31

    else:

        jm = (
            7
            + (days - 186) // 30
        )

        jd = (
            1
            + (days - 186) % 30
        )

    return jy, jm, jd


def tehran_string():

    dt = now_tehran()

    jy, jm, jd = gregorian_to_jalali(
        dt.year,
        dt.month,
        dt.day
    )

    return (
        f"{jy:04d}/{jm:02d}/{jd:02d} "
        f"{dt.hour:02d}:"
        f"{dt.minute:02d}:"
        f"{dt.second:02d}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[INFO] Telegram not configured."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.ok:

            return True

        print(
            "[WARN] Telegram:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        print(
            "[WARN] Telegram exception:",
            e
        )

    return False


# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
    "timeout": 20000
})


# ============================================================
# MARKET DETECTION
# ============================================================

def is_valid_futures_market(
    market
):

    try:

        if not market.get(
            "active",
            True
        ):

            return False

        if market.get(
            "contract"
        ) is not True:

            return False

        # Kraken Futures can expose
        # different quote/settle structures.
        #
        # We accept linear contracts
        # using several CCXT fields.

        linear = market.get(
            "linear"
        )

        inverse = market.get(
            "inverse"
        )

        if linear is True:

            return True

        if (
            linear is None
            and inverse is not True
        ):

            settle = str(
                market.get(
                    "settle",
                    ""
                )
            ).upper()

            quote = str(
                market.get(
                    "quote",
                    ""
                )
            ).upper()

            symbol = str(
                market.get(
                    "symbol",
                    ""
                )
            ).upper()

            if (
                settle in (
                    "USDT",
                    "USD"
                )
                or quote in (
                    "USDT",
                    "USD"
                )
            ):

                if (
                    "PERP" in symbol
                    or "SWAP" in symbol
                    or market.get(
                        "swap"
                    ) is True
                ):

                    return True

        return False

    except Exception:

        return False


def get_top_symbols():

    print(
        "[INFO] Loading Kraken Futures markets..."
    )

    try:

        markets = exchange.load_markets(
            reload=True
        )

    except Exception as e:

        print(
            "[ERROR] load_markets:",
            e
        )

        return []

    print(
        f"[INFO] Total markets returned: "
        f"{len(markets)}"
    )

    candidates = []

    for symbol, market in markets.items():

        if not is_valid_futures_market(
            market
        ):

            continue

        candidates.append(
            symbol
        )

    print(
        f"[INFO] Valid futures markets: "
        f"{len(candidates)}"
    )

    # --------------------------------------------------------
    # FALLBACK:
    # If CCXT does not expose the market flags correctly,
    # use Kraken Futures symbols that look like contracts.
    # --------------------------------------------------------

    if not candidates:

        print(
            "[WARN] Normal market filter found 0."
        )

        print(
            "[INFO] Trying Kraken Futures fallback..."
        )

        for symbol, market in markets.items():

            try:

                if not market.get(
                    "active",
                    True
                ):

                    continue

                symbol_upper = str(
                    symbol
                ).upper()

                market_id = str(
                    market.get(
                        "id",
                        ""
                    )
                ).upper()

                base = str(
                    market.get(
                        "base",
                        ""
                    )
                ).upper()

                quote = str(
                    market.get(
                        "quote",
                        ""
                    )
                ).upper()

                settle = str(
                    market.get(
                        "settle",
                        ""
                    )
                ).upper()

                is_contract = (
                    market.get(
                        "contract"
                    ) is True
                    or market.get(
                        "swap"
                    ) is True
                    or market.get(
                        "future"
                    ) is True
                )

                looks_usd = (
                    quote in (
                        "USD",
                        "USDT"
                    )
                    or settle in (
                        "USD",
                        "USDT"
                    )
                    or "/USD" in symbol_upper
                    or "/USDT" in symbol_upper
                    or "USD" in market_id
                )

                if (
                    is_contract
                    and looks_usd
                    and base
                ):

                    candidates.append(
                        symbol
                    )

            except Exception:

                continue

    # --------------------------------------------------------
    # Remove duplicates
    # --------------------------------------------------------

    candidates = list(
        dict.fromkeys(
            candidates
        )
    )

    print(
        f"[INFO] Candidate contracts: "
        f"{len(candidates)}"
    )

    if not candidates:

        print(
            "[ERROR] Kraken returned no usable futures contracts."
        )

        # Debug first markets
        print(
            "[DEBUG] Sample markets:"
        )

        for symbol, market in list(
            markets.items()
        )[:15]:

            print(
                symbol,
                {
                    "id": market.get("id"),
                    "base": market.get("base"),
                    "quote": market.get("quote"),
                    "settle": market.get("settle"),
                    "contract": market.get("contract"),
                    "linear": market.get("linear"),
                    "inverse": market.get("inverse"),
                    "swap": market.get("swap"),
                    "future": market.get("future"),
                    "active": market.get("active")
                }
            )

        return []

    # --------------------------------------------------------
    # Fetch tickers
    # --------------------------------------------------------

    try:

        tickers = exchange.fetch_tickers()

    except Exception as e:

        print(
            "[ERROR] fetch_tickers:",
            e
        )

        print(
            "[INFO] Using first contracts as fallback."
        )

        return candidates[:TOP_N]

    volumes = []

    for symbol in candidates:

        ticker = tickers.get(
            symbol
        )

        if not ticker:

            continue

        quote_volume = safe_float(
            ticker.get(
                "quoteVolume"
            )
        )

        if quote_volume <= 0:

            base_volume = safe_float(
                ticker.get(
                    "baseVolume"
                )
            )

            last = safe_float(
                ticker.get(
                    "last"
                )
            )

            quote_volume = (
                base_volume
                * last
            )

        if quote_volume <= 0:

            continue

        volumes.append(
            (
                symbol,
                quote_volume
            )
        )

    volumes.sort(
        key=lambda x: x[1],
        reverse=True
    )

    selected = [
        x[0]
        for x in volumes[:TOP_N]
    ]

    # --------------------------------------------------------
    # If ticker data is incomplete
    # --------------------------------------------------------

    if not selected:

        print(
            "[WARN] No ticker volumes available."
        )

        selected = candidates[:TOP_N]

    print("")
    print(
        f"[INFO] TOP {len(selected)}:"
    )

    for i, symbol in enumerate(
        selected,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{clean_symbol(symbol)}"
        )

    return selected


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            TIMEFRAME,
            limit=CANDLE_LIMIT
        )

        if not candles:

            return None

        df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
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
            subset=[
                "open",
                "high",
                "low",
                "close",
                "volume"
            ],
            inplace=True
        )

        df.reset_index(
            drop=True,
            inplace=True
        )

        if len(df) < 60:

            return None

        # ----------------------------------------------------
        # Remove currently forming candle
        # ----------------------------------------------------

        df = df.iloc[:-1].copy()

        df.reset_index(
            drop=True,
            inplace=True
        )

        if len(df) < 50:

            return None

        return df

    except Exception as e:

        print(
            f"[WARN] "
            f"{clean_symbol(symbol)} "
            f"OHLCV error: {e}"
        )

        return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD
):

    high = df["high"]

    low = df["low"]

    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    return tr.rolling(
        period
    ).mean()


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_range(row):

    return (
        safe_float(row["high"])
        - safe_float(row["low"])
    )


def candle_body(row):

    return abs(
        safe_float(row["close"])
        - safe_float(row["open"])
    )


def body_ratio(row):

    r = candle_range(row)

    if r <= 0:

        return 0.0

    return (
        candle_body(row)
        / r
    )


def bullish(row):

    return (
        safe_float(row["close"])
        > safe_float(row["open"])
    )


def bearish(row):

    return (
        safe_float(row["close"])
        < safe_float(row["open"])
    )


# ============================================================
# MARKET STRUCTURE
# ============================================================

def detect_structure(df):

    last = df.iloc[-1]

    previous = df.iloc[-2]

    lookback = df.iloc[
        -SWING_LOOKBACK-1:-1
    ]

    recent_high = safe_float(
        lookback["high"].max()
    )

    recent_low = safe_float(
        lookback["low"].min()
    )

    close = safe_float(
        last["close"]
    )

    prev_close = safe_float(
        previous["close"]
    )

    last_high = safe_float(
        last["high"]
    )

    last_low = safe_float(
        last["low"]
    )

    bullish_bos = (
        close > recent_high
    )

    bearish_bos = (
        close < recent_low
    )

    structure = "RANGE"

    if bullish_bos:

        structure = "BULLISH"

    elif bearish_bos:

        structure = "BEARISH"

    elif (
        close > prev_close
        and last_low
        >= safe_float(
            previous["low"]
        )
    ):

        structure = "BULLISH"

    elif (
        close < prev_close
        and last_high
        <= safe_float(
            previous["high"]
        )
    ):

        structure = "BEARISH"

    return {
        "structure": structure,

        "bullish_bos": bool(
            bullish_bos
        ),

        "bearish_bos": bool(
            bearish_bos
        ),

        "recent_high": recent_high,

        "recent_low": recent_low,

        "last_close": close,

        "last_high": last_high,

        "last_low": last_low,

        "last_candle_timestamp": int(
            safe_float(
                last["timestamp"]
            )
        )
    }


# ============================================================
# PULLBACK
# ============================================================

def detect_pullback(
    df,
    direction
):

    if len(df) < 10:

        return False

    last = df.iloc[-1]

    previous = df.iloc[-2]

    recent = df.iloc[-6:-1]

    last_close = safe_float(
        last["close"]
    )

    last_open = safe_float(
        last["open"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    recent_high = safe_float(
        recent["high"].max()
    )

    recent_low = safe_float(
        recent["low"].min()
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if direction == "BUY":

        touched_pullback_zone = (
            safe_float(last["low"])
            <= recent_high
            and
            safe_float(last["low"])
            >= recent_low
        )

        recovery = (
            last_close > last_open
            and last_close
            >= previous_close
        )

        return (
            touched_pullback_zone
            and recovery
        )

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if direction == "SELL":

        touched_pullback_zone = (
            safe_float(last["high"])
            >= recent_low
            and
            safe_float(last["high"])
            <= recent_high
        )

        rejection = (
            last_close < last_open
            and last_close
            <= previous_close
        )

        return (
            touched_pullback_zone
            and rejection
        )

    return False


# ============================================================
# ENGULFING
# ============================================================

def detect_engulfing(df):

    previous = df.iloc[-2]

    last = df.iloc[-1]

    po = safe_float(
        previous["open"]
    )

    pc = safe_float(
        previous["close"]
    )

    lo = safe_float(
        last["open"]
    )

    lc = safe_float(
        last["close"]
    )

    bullish_engulfing = (
        pc < po
        and lc > lo
        and lo <= pc
        and lc >= po
    )

    bearish_engulfing = (
        pc > po
        and lc < lo
        and lo >= pc
        and lc <= po
    )

    if bullish_engulfing:

        return "BULLISH ENGULFING"

    if bearish_engulfing:

        return "BEARISH ENGULFING"

    return None


# ============================================================
# MOMENTUM
# ============================================================

def detect_momentum(df):

    last = df.iloc[-1]

    ratio = body_ratio(
        last
    )

    if ratio < BODY_MIN_RATIO:

        return None

    if bullish(last):

        return "BULLISH MOMENTUM"

    if bearish(last):

        return "BEARISH MOMENTUM"

    return None


# ============================================================
# VOLUME
# ============================================================

def detect_volume(df):

    if len(df) < (
        VOLUME_LOOKBACK + 2
    ):

        return False, 0.0

    current_volume = safe_float(
        df["volume"].iloc[-1]
    )

    average_volume = safe_float(
        df["volume"]
        .iloc[
            -VOLUME_LOOKBACK-1:-1
        ]
        .mean()
    )

    if average_volume <= 0:

        return False, 0.0

    ratio = (
        current_volume
        / average_volume
    )

    return (
        ratio >= VOLUME_MIN_RATIO,
        ratio
    )


# ============================================================
# SIGNAL
# ============================================================

def calculate_signal(df):

    structure = detect_structure(
        df
    )

    engulfing = detect_engulfing(
        df
    )

    momentum = detect_momentum(
        df
    )

    volume_ok, volume_ratio = (
        detect_volume(df)
    )

    buy_score = 0

    sell_score = 0

    buy_reasons = []

    sell_reasons = []

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if structure["structure"] == "BULLISH":

        buy_score += 25

        buy_reasons.append(
            "Bullish Structure"
        )

    elif structure["structure"] == "BEARISH":

        sell_score += 25

        sell_reasons.append(
            "Bearish Structure"
        )

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    if structure["bullish_bos"]:

        buy_score += 25

        buy_reasons.append(
            "BOS"
        )

    if structure["bearish_bos"]:

        sell_score += 25

        sell_reasons.append(
            "BOS"
        )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    if detect_pullback(
        df,
        "BUY"
    ):

        buy_score += 15

        buy_reasons.append(
            "Pullback"
        )

    if detect_pullback(
        df,
        "SELL"
    ):

        sell_score += 15

        sell_reasons.append(
            "Pullback"
        )

    # --------------------------------------------------------
    # ENGULFING
    # --------------------------------------------------------

    if engulfing == (
        "BULLISH ENGULFING"
    ):

        buy_score += 20

        buy_reasons.append(
            "Bullish Engulfing"
        )

    elif engulfing == (
        "BEARISH ENGULFING"
    ):

        sell_score += 20

        sell_reasons.append(
            "Bearish Engulfing"
        )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    if momentum == (
        "BULLISH MOMENTUM"
    ):

        buy_score += 10

        buy_reasons.append(
            "Bullish Momentum"
        )

    elif momentum == (
        "BEARISH MOMENTUM"
    ):

        sell_score += 10

        sell_reasons.append(
            "Bearish Momentum"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume_ok:

        if buy_score > sell_score:

            buy_score += 5

            buy_reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

        elif sell_score > buy_score:

            sell_score += 5

            sell_reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

    # --------------------------------------------------------
    # FINAL BUY
    # --------------------------------------------------------

    if (
        buy_score >= MIN_SCORE
        and buy_score > sell_score
    ):

        return {
            "side": "BUY",

            "score": min(
                buy_score,
                100
            ),

            "reasons": buy_reasons,

            "atr": safe_float(
                df["atr"].iloc[-1]
            ),

            "close": safe_float(
                df["close"].iloc[-1]
            )
        }

    # --------------------------------------------------------
    # FINAL SELL
    # --------------------------------------------------------

    if (
        sell_score >= MIN_SCORE
        and sell_score > buy_score
    ):

        return {
            "side": "SELL",

            "score": min(
                sell_score,
                100
            ),

            "reasons": sell_reasons,

            "atr": safe_float(
                df["atr"].iloc[-1]
            ),

            "close": safe_float(
                df["close"].iloc[-1]
            )
        }

    return None


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(symbol):

    df = fetch_ohlcv(
        symbol
    )

    if df is None:

        return None

    df["atr"] = calculate_atr(
        df
    )

    df.dropna(
        subset=["atr"],
        inplace=True
    )

    df.reset_index(
        drop=True,
        inplace=True
    )

    if len(df) < 30:

        return None

    structure = detect_structure(
        df
    )

    signal = calculate_signal(
        df
    )

    return {
        "symbol": symbol,

        "df": df,

        "last_close": safe_float(
            df["close"].iloc[-1]
        ),

        "last_candle_timestamp": int(
            safe_float(
                df["timestamp"].iloc[-1]
            )
        ),

        "structure": structure,

        "signal": signal
    }


# ============================================================
# COOLDOWN
# ============================================================

def in_cooldown(
    symbol,
    candle_timestamp
):

    previous = state[
        "last_signals"
    ].get(symbol)

    if not previous:

        return False

    previous_timestamp = safe_float(
        previous.get(
            "timestamp"
        )
    )

    if previous_timestamp <= 0:

        return False

    candle_size = (
        5 * 60 * 1000
    )

    difference = (
        candle_timestamp
        - previous_timestamp
    )

    candles = (
        difference
        / candle_size
    )

    return (
        candles
        < SIGNAL_COOLDOWN_CANDLES
    )


# ============================================================
# BUILD TRADE
# ============================================================

def build_trade(
    symbol,
    result
):

    signal = result["signal"]

    structure = result[
        "structure"
    ]

    side = signal["side"]

    entry = safe_float(
        signal["close"]
    )

    atr = safe_float(
        signal["atr"]
    )

    if entry <= 0:

        return None

    if atr <= 0:

        return None

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if side == "BUY":

        structural_sl = safe_float(
            structure["recent_low"]
        )

        atr_sl = (
            entry - atr
        )

        if (
            structural_sl > 0
            and structural_sl < entry
        ):

            sl = min(
                structural_sl,
                atr_sl
            )

        else:

            sl = atr_sl

        risk = (
            entry - sl
        )

        if risk <= 0:

            return None

        minimum_risk = (
            atr * MIN_SL_ATR
        )

        maximum_risk = (
            atr * MAX_SL_ATR
        )

        if risk < minimum_risk:

            risk = minimum_risk

            sl = (
                entry - risk
            )

        if risk > maximum_risk:

            risk = maximum_risk

            sl = (
                entry - risk
            )

        tp = (
            entry
            + risk * RR
        )

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    else:

        structural_sl = safe_float(
            structure["recent_high"]
        )

        atr_sl = (
            entry + atr
        )

        if (
            structural_sl > entry
        ):

            sl = max(
                structural_sl,
                atr_sl
            )

        else:

            sl = atr_sl

        risk = (
            sl - entry
        )

        if risk <= 0:

            return None

        minimum_risk = (
            atr * MIN_SL_ATR
        )

        maximum_risk = (
            atr * MAX_SL_ATR
        )

        if risk < minimum_risk:

            risk = minimum_risk

            sl = (
                entry + risk
            )

        if risk > maximum_risk:

            risk = maximum_risk

            sl = (
                entry + risk
            )

        tp = (
            entry
            - risk * RR
        )

    risk_pct = abs(
        percentage_difference(
            entry,
            sl
        )
    )

    candle_timestamp = (
        result[
            "last_candle_timestamp"
        ]
    )

    candle_time = datetime.fromtimestamp(
        candle_timestamp / 1000,
        tz=timezone.utc
    ).isoformat()

    trade_id = (
        f"{clean_symbol(symbol)}_"
        f"{side}_"
        f"{candle_timestamp}"
    )

    return {

        "trade_id": trade_id,

        "symbol": clean_symbol(
            symbol
        ),

        "exchange_symbol": symbol,

        "side": side,

        "entry": round_price(
            entry
        ),

        "sl": round_price(
            sl
        ),

        "tp": round_price(
            tp
        ),

        "risk_pct": round(
            risk_pct,
            3
        ),

        "score": int(
            signal["score"]
        ),

        "reasons": signal[
            "reasons"
        ],

        "atr": round_price(
            atr
        ),

        "opened_at": now_utc().isoformat(),

        "opened_at_tehran": (
            tehran_string()
        ),

        "entry_candle": candle_time,

        "entry_candle_timestamp": (
            candle_timestamp
        )
    }


# ============================================================
# CREATE SIGNAL
# ============================================================

def create_signal(
    symbol,
    result
):

    if (
        len(state["open"])
        >= MAX_OPEN_TRADES
    ):

        return None

    signal = result.get(
        "signal"
    )

    if not signal:

        return None

    if (
        signal["score"]
        < MIN_SCORE
    ):

        return None

    candle_timestamp = (
        result[
            "last_candle_timestamp"
        ]
    )

    if in_cooldown(
        symbol,
        candle_timestamp
    ):

        print(
            f"[COOLDOWN] "
            f"{clean_symbol(symbol)}"
        )

        return None

    # Same symbol already open
    for trade in state[
        "open"
    ].values():

        if (
            trade.get(
                "exchange_symbol"
            )
            == symbol
        ):

            return None

    trade = build_trade(
        symbol,
        result
    )

    if not trade:

        return None

    state[
        "open"
    ][
        trade["trade_id"]
    ] = trade

    state[
        "last_signals"
    ][
        symbol
    ] = {

        "timestamp":
            candle_timestamp,

        "side":
            trade["side"],

        "score":
            trade["score"]
    }

    return trade


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(
    results
):

    closed = []

    old_open = dict(
        state.get(
            "open",
            {}
        )
    )

    new_open = {}

    for trade_id, trade in old_open.items():

        symbol = trade.get(
            "exchange_symbol"
        )

        result = results.get(
            symbol
        )

        if not result:

            new_open[
                trade_id
            ] = trade

            continue

        price = safe_float(
            result.get(
                "last_close"
            )
        )

        if price <= 0:

            new_open[
                trade_id
            ] = trade

            continue

        side = trade.get(
            "side"
        )

        entry = safe_float(
            trade.get(
                "entry"
            )
        )

        sl = safe_float(
            trade.get(
                "sl"
            )
        )

        tp = safe_float(
            trade.get(
                "tp"
            )
        )

        status = None

        exit_price = None

        r_multiple = 0.0

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if side == "BUY":

            if price <= sl:

                status = "LOSS"

                exit_price = sl

                r_multiple = -1.0

            elif price >= tp:

                status = "WIN"

                exit_price = tp

                r_multiple = RR

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        elif side == "SELL":

            if price >= sl:

                status = "LOSS"

                exit_price = sl

                r_multiple = -1.0

            elif price <= tp:

                status = "WIN"

                exit_price = tp

                r_multiple = RR

        # ----------------------------------------------------
        # STILL OPEN
        # ----------------------------------------------------

        if status is None:

            new_open[
                trade_id
            ] = trade

            continue

        # ----------------------------------------------------
        # CLOSED
        # ----------------------------------------------------

        if side == "BUY":

            pnl_pct = (
                (
                    exit_price
                    - entry
                )
                / entry
            ) * 100

        else:

            pnl_pct = (
                (
                    entry
                    - exit_price
                )
                / entry
            ) * 100

        closed_trade = dict(
            trade
        )

        closed_trade.update({

            "status": status,

            "exit": round_price(
                exit_price
            ),

            "pnl_pct": round(
                pnl_pct,
                3
            ),

            "r": round(
                r_multiple,
                2
            ),

            "closed_at":
                now_utc().isoformat(),

            "closed_at_tehran":
                tehran_string(),

            "exit_candle_close":
                price
        })

        history.append(
            closed_trade
        )

        closed.append(
            closed_trade
        )

        print(
            f"[CLOSED] "
            f"{clean_symbol(symbol)} "
            f"{side} "
            f"{status} "
            f"Exit={exit_price}"
        )

    state[
        "open"
    ] = new_open

    return closed


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    trades = len(
        history
    )

    wins = sum(
        1
        for trade in history
        if trade.get(
            "status"
        ) == "WIN"
    )

    losses = sum(
        1
        for trade in history
        if trade.get(
            "status"
        ) == "LOSS"
    )

    neutral = (
        trades
        - wins
        - losses
    )

    if trades > 0:

        win_rate = (
            wins
            / trades
        ) * 100

    else:

        win_rate = 0.0

    total_r = sum(
        safe_float(
            trade.get(
                "r"
            )
        )
        for trade in history
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "win_rate": win_rate,
        "total_r": total_r
    }


# ============================================================
# CURRENT PRICE
# ============================================================

def current_price(symbol):

    try:

        ticker = exchange.fetch_ticker(
            symbol
        )

        return safe_float(
            ticker.get(
                "last"
            )
        )

    except Exception as e:

        print(
            f"[WARN] Current price failed "
            f"{symbol}: {e}"
        )

        return 0.0


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def open_trade_report():

    trades = state.get(
        "open",
        {}
    )

    if not trades:

        return (
            "📭 **OPEN TRADE**\n"
            "None"
        )

    lines = [
        "📌 **OPEN TRADE**"
    ]

    for trade in trades.values():

        symbol = trade.get(
            "symbol",
            "?"
        )

        side = trade.get(
            "side",
            "?"
        )

        entry = safe_float(
            trade.get(
                "entry"
            )
        )

        sl = safe_float(
            trade.get(
                "sl"
            )
        )

        tp = safe_float(
            trade.get(
                "tp"
            )
        )

        score = trade.get(
            "score",
            0
        )

        exchange_symbol = trade.get(
            "exchange_symbol"
        )

        current = current_price(
            exchange_symbol
        )

        if current > 0:

            if side == "BUY":

                live_pnl = (
                    (
                        current
                        - entry
                    )
                    / entry
                ) * 100

            else:

                live_pnl = (
                    (
                        entry
                        - current
                    )
                    / entry
                ) * 100

        else:

            live_pnl = 0.0

        try:

            opened = datetime.fromisoformat(
                trade[
                    "opened_at"
                ]
            )

            duration = (
                now_utc()
                - opened
            )

            minutes = int(
                duration.total_seconds()
                / 60
            )

        except Exception:

            minutes = 0

        emoji = (
            "🟢"
            if live_pnl >= 0
            else "🔴"
        )

        lines.extend([

            "",

            f"**{symbol} {side}**",

            f"Entry: `{entry}`",

            f"Stop Loss: `{sl}`",

            f"Target: `{tp}`",

            f"Current: `{round_price(current)}`",

            (
                f"{emoji} Live P&L: "
                f"`{live_pnl:+.2f}%`"
            ),

            (
                f"Score: "
                f"`{score}/100`"
            ),

            (
                f"Duration: "
                f"`{minutes}m`"
            )
        ])

    return "\n".join(
        lines
    )


# ============================================================
# CLOSED REPORT
# ============================================================

def closed_report(
    closed
):

    if not closed:

        return ""

    lines = [
        "📕 **CLOSED THIS RUN**"
    ]

    for trade in closed:

        symbol = trade.get(
            "symbol",
            "?"
        )

        side = trade.get(
            "side",
            "?"
        )

        status = trade.get(
            "status",
            "?"
        )

        exit_price = trade.get(
            "exit",
            0
        )

        pnl = safe_float(
            trade.get(
                "pnl_pct"
            )
        )

        r = safe_float(
            trade.get(
                "r"
            )
        )

        emoji = (
            "🟢"
            if status == "WIN"
            else "🔴"
        )

        lines.extend([

            "",

            (
                f"{emoji} **"
                f"{symbol} "
                f"{side} "
                f"{status}**"
            ),

            (
                f"Exit: "
                f"`{exit_price}`"
            ),

            (
                f"P&L: "
                f"`{pnl:+.2f}%`"
            ),

            (
                f"R: "
                f"`{r:+.2f}`"
            )
        ])

    return "\n".join(
        lines
    )


# ============================================================
# SIGNAL REPORT
# ============================================================

def signal_report(
    trade
):

    if not trade:

        return ""

    emoji = (
        "🟢"
        if trade["side"] == "BUY"
        else "🔴"
    )

    reasons = (
        " + ".join(
            trade.get(
                "reasons",
                []
            )
        )
    )

    return "\n".join([

        "━━━━━━━━━━━━━━━━━━",

        "🚨 **NEW SIGNAL**",

        "",

        (
            f"{emoji} **"
            f"{trade['symbol']} - "
            f"{trade['side']}**"
        ),

        "",

        (
            f"Entry: "
            f"`{trade['entry']}`"
        ),

        (
            f"Stop Loss: "
            f"`{trade['sl']}` "
            f"(-{trade['risk_pct']:.2f}%)"
        ),

        (
            f"Target: "
            f"`{trade['tp']}` "
            f"(+{trade['risk_pct']:.2f}%)"
        ),

        "",

        (
            f"Score: "
            f"`{trade['score']}/100`"
        ),

        "",

        (
            f"Reasons: "
            f"`{reasons}`"
        ),

        "",

        "⚖️ **RR 1:1**"
    ])


# ============================================================
# REPORT
# ============================================================

def build_report(
    scanned,
    new_trade,
    closed
):

    p = get_performance()

    open_count = len(
        state.get(
            "open",
            {}
        )
    )

    events = (
        len(closed)
        + (
            1
            if new_trade
            else 0
        )
    )

    lines = [

        "📡 **CRYPTO PRICE ACTION REPORT**",

        "",

        f"🕐 {tehran_string()}",

        (
            f"⏱ **{TIMEFRAME} CLOSED | "
            f"TOP {TOP_N}**"
        ),

        "🤖 **PRICE ACTION | RR 1:1**",

        (
            f"🎯 **MIN SCORE: "
            f"{MIN_SCORE}+**"
        ),

        (
            f"🔒 **MAX OPEN: "
            f"{MAX_OPEN_TRADES}**"
        ),

        "━━━━━━━━━━━━━━━━━━",

        "📊 **PERFORMANCE**",

        (
            f"Trades {p['trades']} | "
            f"🟢 {p['wins']} | "
            f"🔴 {p['losses']} | "
            f"⚪ {p['neutral']}"
        ),

        (
            f"🏆 WR: "
            f"{p['win_rate']:.1f}%"
        ),

        (
            f"📈 Total R: "
            f"{p['total_r']:+.2f}"
        ),

        "",

        (
            f"🔎 Scanned: "
            f"{scanned}"
        ),

        (
            f"⚡ THIS RUN: "
            f"{events} EVENTS"
        ),

        (
            f"📂 OPEN: "
            f"{open_count}/"
            f"{MAX_OPEN_TRADES}"
        )
    ]

    if closed:

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            closed_report(
                closed
            )
        ])

    if new_trade:

        lines.extend([
            "",
            signal_report(
                new_trade
            )
        ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        open_trade_report()
    ])

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "CRYPTO PRICE ACTION "
        "SCANNER v2.2"
    )

    print("=" * 70)

    print(
        f"Timeframe: {TIMEFRAME}"
    )

    print(
        f"Top: {TOP_N}"
    )

    print(
        f"Min Score: {MIN_SCORE}"
    )

    print(
        f"Max Open: "
        f"{MAX_OPEN_TRADES}"
    )

    print(
        f"State: {STATE_FILE}"
    )

    print(
        f"History: {HISTORY_FILE}"
    )

    # --------------------------------------------------------
    # TOP SYMBOLS
    # --------------------------------------------------------

    symbols = get_top_symbols()

    if not symbols:

        print(
            "[ERROR] No symbols."
        )

        return

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    results = {}

    candidates = []

    scanned = 0

    for symbol in symbols:

        try:

            print(
                f"[SCAN] "
                f"{clean_symbol(symbol)}"
            )

            result = analyze_symbol(
                symbol
            )

            if result is None:

                continue

            results[
                symbol
            ] = result

            scanned += 1

            signal = result.get(
                "signal"
            )

            if signal:

                candidates.append(
                    result
                )

                print(
                    f"[CANDIDATE] "
                    f"{clean_symbol(symbol)} "
                    f"{signal['side']} "
                    f"{signal['score']}/100"
                )

        except Exception as e:

            print(
                f"[WARN] "
                f"{symbol}: {e}"
            )

        time.sleep(
            0.15
        )

    # --------------------------------------------------------
    # CLOSE EXISTING TRADE FIRST
    # --------------------------------------------------------

    print(
        "[INFO] Checking open trade..."
    )

    closed = update_open_trades(
        results
    )

    # --------------------------------------------------------
    # OPEN NEW TRADE
    # --------------------------------------------------------

    new_trade = None

    if (
        len(state["open"])
        < MAX_OPEN_TRADES
    ):

        candidates.sort(
            key=lambda x:
                x["signal"]["score"],
            reverse=True
        )

        for candidate in candidates:

            symbol = candidate[
                "symbol"
            ]

            trade = create_signal(
                symbol,
                candidate
            )

            if trade:

                new_trade = trade

                print(
                    f"[OPEN] "
                    f"{trade['symbol']} "
                    f"{trade['side']} "
                    f"Score="
                    f"{trade['score']}"
                )

                break

    else:

        print(
            "[INFO] "
            "One trade already open. "
            "No new signal."
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

    print(
        "[INFO] State saved."
    )

    print(
        f"[INFO] Open: "
        f"{len(state['open'])}"
    )

    print(
        f"[INFO] History: "
        f"{len(history)}"
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        scanned=scanned,
        new_trade=new_trade,
        closed=closed
    )

    print("")
    print("=" * 70)
    print(report)
    print("=" * 70)

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_send(
        report
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "[INFO] Interrupted."
        )

    except Exception as e:

        print(
            "[FATAL ERROR]",
            e
        )

        traceback.print_exc()

        try:

            save_json(
                STATE_FILE,
                state
            )

            save_json(
                HISTORY_FILE,
                history
            )

        except Exception as save_error:

            print(
                "[ERROR] "
                f"State save failed: "
                f"{save_error}"
            )

        raise
