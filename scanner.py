import os
import json
import time
import requests
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CRYPTO PUMP / DUMP SCANNER v5.5 ULTRA FAST
# KRAKEN FUTURES - CLOSED 5M CANDLES
# UT BOT SETUP ENGINE
# ============================================================

VERSION = "5.5 ULTRA FAST"

# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_TIMEOUT = 5

# Telegram ارسال جدا از Scan
TELEGRAM_WORKERS = 4

telegram_executor = ThreadPoolExecutor(
    max_workers=TELEGRAM_WORKERS
)

# ============================================================
# KRAKEN
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

STATE_FILE = "signal_state.json"

CANDLE_HISTORY = 500

# تعداد درخواست همزمان Kraken
MAX_WORKERS = 10

# Timeout اتصال و دریافت
KRAKEN_CONNECT_TIMEOUT = 3
KRAKEN_READ_TIMEOUT = 6

# ============================================================
# UT BOT
# ============================================================

UT_KEY_VALUE = 3
UT_ATR_PERIOD = 10

# ============================================================
# COINS
# ============================================================

COINS = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "ADA": "PF_ADAUSD",
    "DOGE": "PF_DOGEUSD",
    "AVAX": "PF_AVAXUSD",
    "LINK": "PF_LINKUSD",
    "DOT": "PF_DOTUSD",
    "LTC": "PF_LTCUSD",
    "BCH": "PF_BCHUSD",
    "UNI": "PF_UNIUSD",
    "ATOM": "PF_ATOMUSD",
    "FIL": "PF_FILUSD",
    "AAVE": "PF_AAVEUSD",
    "SUI": "PF_SUIUSD",
    "SEI": "PF_SEIUSD",
    "TRX": "PF_TRXUSD",
    "NEAR": "PF_NEARUSD",
    "APT": "PF_APTUSD",
    "ARB": "PF_ARBUSD",
    "OP": "PF_OPUSD",
    "INJ": "PF_INJUSD",
    "TIA": "PF_TIAUSD",
    "ETC": "PF_ETCUSD",
    "XLM": "PF_XLMUSD",
    "HBAR": "PF_HBARUSD",
    "ALGO": "PF_ALGOUSD",
    "VET": "PF_VETUSD",
    "MATIC": "PF_MATICUSD",
}


# ============================================================
# JSON STATE
# ============================================================

def load_json_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print("STATE LOAD ERROR:", e)

        return {}


def save_json_state(state):

    try:

        tmp_file = STATE_FILE + ".tmp"

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp_file,
            STATE_FILE
        )

    except Exception as e:

        print("STATE SAVE ERROR:", e)


# ============================================================
# TELEGRAM
# IMPORTANT:
# TELEGRAM DOES NOT BLOCK SCANNER
# ============================================================

def _telegram_send_worker(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("\n" + message)

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=(
                2,
                TELEGRAM_TIMEOUT
            )
        )

        if r.ok:
            return True

        print(
            "TELEGRAM ERROR:",
            r.status_code
        )

        return False

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )

        return False


def send_telegram(message):

    if not message:
        return

    # ارسال در Thread جدا
    telegram_executor.submit(
        _telegram_send_worker,
        message
    )


def send_telegram_chunks(
    message,
    chunk_size=3800
):

    if not message:
        return

    if len(message) <= chunk_size:

        send_telegram(message)

        return

    chunks = []

    while message:

        if len(message) <= chunk_size:

            chunks.append(message)

            break

        cut = message.rfind(
            "\n",
            0,
            chunk_size
        )

        if cut <= 0:
            cut = chunk_size

        chunks.append(
            message[:cut]
        )

        message = message[
            cut:
        ].lstrip()

    for chunk in chunks:

        send_telegram(chunk)


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(
    symbol,
    interval=5
):

    url = (
        f"{BASE_URL}/"
        f"{symbol}/"
        f"{interval}"
    )

    try:

        r = requests.get(
            url,
            timeout=(
                KRAKEN_CONNECT_TIMEOUT,
                KRAKEN_READ_TIMEOUT
            )
        )

        if not r.ok:

            print(
                f"✗ KRAKEN {symbol}: "
                f"HTTP {r.status_code}"
            )

            return []

        data = r.json()

    except requests.exceptions.Timeout:

        print(
            f"⏱️ KRAKEN TIMEOUT: "
            f"{symbol}"
        )

        return []

    except requests.exceptions.RequestException as e:

        print(
            f"✗ KRAKEN REQUEST: "
            f"{symbol} | {e}"
        )

        return []

    except Exception as e:

        print(
            f"✗ KRAKEN JSON: "
            f"{symbol} | {e}"
        )

        return []

    raw = []

    if isinstance(data, dict):

        if isinstance(
            data.get("candles"),
            list
        ):

            raw = data["candles"]

        elif isinstance(
            data.get("data"),
            list
        ):

            raw = data["data"]

        elif isinstance(
            data.get("result"),
            list
        ):

            raw = data["result"]

    elif isinstance(data, list):

        raw = data

    candles = []

    for x in raw:

        try:

            if isinstance(x, dict):

                timestamp = (
                    x.get("time")
                    or x.get("timestamp")
                    or x.get("ts")
                )

                o = x.get("open")
                h = x.get("high")
                l = x.get("low")
                c = x.get("close")
                v = x.get(
                    "volume",
                    0
                )

            elif (
                isinstance(x, list)
                and len(x) >= 6
            ):

                timestamp = x[0]
                o = x[1]
                h = x[2]
                l = x[3]
                c = x[4]
                v = x[5]

            else:

                continue

            if timestamp is None:
                continue

            timestamp = float(
                timestamp
            )

            if timestamp > 10_000_000_000:

                timestamp /= 1000

            candles.append({
                "time": int(timestamp),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v or 0)
            })

        except Exception:

            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    if not candles:
        return []

    # ========================================================
    # REMOVE CURRENT OPEN CANDLE
    # ========================================================

    now = int(
        time.time()
    )

    current_bucket = (
        now
        - (
            now
            % (interval * 60)
        )
    )

    candles = [
        c
        for c in candles
        if c["time"] < current_bucket
    ]

    return candles[
        -CANDLE_HISTORY:
    ]


# ============================================================
# FETCH ONE COIN
# ============================================================

def fetch_coin_data(
    symbol,
    kraken_symbol
):

    candles = get_candles(
        kraken_symbol,
        5
    )

    if len(candles) < 60:

        return {
            "symbol": symbol,
            "candles": [],
            "analysis": None,
            "error": "insufficient candles"
        }

    try:

        analysis = scanner_score(
            candles
        )

    except Exception as e:

        return {
            "symbol": symbol,
            "candles": [],
            "analysis": None,
            "error": f"analysis error: {e}"
        }

    return {
        "symbol": symbol,
        "candles": candles,
        "analysis": analysis,
        "error": None
    }


# ============================================================
# FAST MULTI FETCH
# ============================================================

def fetch_all_coins_fast():

    results_map = {}

    started = time.time()

    print(
        f"⚡ Starting parallel Kraken scan "
        f"({len(COINS)} coins / "
        f"{MAX_WORKERS} workers)..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for symbol, kraken_symbol in COINS.items():

            future = executor.submit(
                fetch_coin_data,
                symbol,
                kraken_symbol
            )

            futures[future] = symbol

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                result = future.result()

                results_map[
                    symbol
                ] = result

                if result["candles"]:

                    print(
                        f"✓ {symbol}: "
                        f"{result['analysis']['direction']} "
                        f"{result['analysis']['score']}"
                    )

                else:

                    print(
                        f"✗ {symbol}: "
                        f"{result['error']}"
                    )

            except Exception as e:

                print(
                    f"✗ {symbol}: "
                    f"THREAD ERROR {e}"
                )

    # ========================================================
    # حفظ ترتیب اصلی COINS
    # ========================================================

    results = []

    for symbol in COINS:

        if symbol in results_map:

            result = results_map[
                symbol
            ]

            if result["candles"]:

                results.append(
                    result
                )

    elapsed = (
        time.time()
        - started
    )

    print(
        f"\n⚡ Kraken fetch: "
        f"{elapsed:.2f}s"
    )

    return results


# ============================================================
# BASIC FUNCTIONS
# ============================================================

def pct_change(a, b):

    if a == 0:
        return 0

    return (
        (b - a) / a
    ) * 100


def calculate_rsi(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    closes = [
        x["close"]
        for x in candles
    ]

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        diff = (
            closes[i]
            - closes[i - 1]
        )

        if diff >= 0:

            gains.append(diff)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(diff))

    gains = gains[
        -period:
    ]

    losses = losses[
        -period:
    ]

    avg_gain = mean(gains)
    avg_loss = mean(losses)

    if avg_loss == 0:
        return 100

    rs = (
        avg_gain
        / avg_loss
    )

    return 100 - (
        100 / (1 + rs)
    )


def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )
        )

        trs.append(tr)

    return mean(
        trs[-period:]
    )


def volume_ratio(
    candles,
    period=20
):

    if len(candles) < period + 1:
        return 0

    current_volume = candles[-1][
        "volume"
    ]

    previous_volumes = [
        x["volume"]
        for x in candles[
            -period - 1:-1
        ]
    ]

    avg_volume = mean(
        previous_volumes
    )

    if avg_volume <= 0:
        return 0

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(candles):

    if len(candles) < 52:
        return None

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    tenkan = (
        max(highs[-9:])
        + min(lows[-9:])
    ) / 2

    kijun = (
        max(highs[-26:])
        + min(lows[-26:])
    ) / 2

    span_a = (
        tenkan
        + kijun
    ) / 2

    span_b = (
        max(highs[-52:])
        + min(lows[-52:])
    ) / 2

    price = candles[-1][
        "close"
    ]

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "price": price
    }


# ============================================================
# TRENDLINE
# ============================================================

def trendline_status(candles):

    if len(candles) < 30:
        return "NEUTRAL"

    recent = candles[-10:]
    previous = candles[-30:-10]

    recent_high = max(
        x["high"]
        for x in recent
    )

    previous_high = max(
        x["high"]
        for x in previous
    )

    recent_low = min(
        x["low"]
        for x in recent
    )

    previous_low = min(
        x["low"]
        for x in previous
    )

    close = candles[-1][
        "close"
    ]

    if (
        recent_high > previous_high
        and close > previous_high
    ):

        return "BREAKOUT"

    if (
        recent_low < previous_low
        and close < previous_low
    ):

        return "BREAKDOWN"

    return "NEUTRAL"


# ============================================================
# NORMAL SCANNER
# ============================================================

def scanner_score(candles):

    if len(candles) < 60:

        return {
            "score": 0,
            "direction": "NEUTRAL",
            "rsi": None,
            "atr": None,
            "vol_ratio": 0,
            "ichimoku": None,
            "trendline": "NEUTRAL"
        }

    close = candles[-1][
        "close"
    ]

    rsi = calculate_rsi(
        candles
    )

    atr = calculate_atr(
        candles
    )

    vol_ratio = volume_ratio(
        candles
    )

    ichi = calculate_ichimoku(
        candles
    )

    trendline = trendline_status(
        candles
    )

    score = 0

    if rsi is not None:

        if rsi >= 60:
            score += 20

        elif rsi <= 40:
            score -= 20

    if vol_ratio >= 2:
        score += 20

    elif vol_ratio >= 1.3:
        score += 10

    if ichi:

        if close > ichi["tenkan"]:
            score += 10
        else:
            score -= 10

        if close > ichi["kijun"]:
            score += 10
        else:
            score -= 10

        cloud_top = max(
            ichi["span_a"],
            ichi["span_b"]
        )

        cloud_bottom = min(
            ichi["span_a"],
            ichi["span_b"]
        )

        if close > cloud_top:
            score += 10

        elif close < cloud_bottom:
            score -= 10

    if trendline == "BREAKOUT":

        score += 20

    elif trendline == "BREAKDOWN":

        score -= 20

    score = max(
        -100,
        min(100, score)
    )

    if score >= 20:

        direction = "LONG"

    elif score <= -20:

        direction = "SHORT"

    else:

        direction = "NEUTRAL"

    return {
        "score": score,
        "direction": direction,
        "rsi": rsi,
        "atr": atr,
        "vol_ratio": vol_ratio,
        "ichimoku": ichi,
        "trendline": trendline
    }


# ============================================================
# 2 CANDLE CONFIRMATION
# ============================================================

def two_candle_confirmation(
    candles,
    direction
):

    if len(candles) < 3:
        return False

    c1 = candles[-2]
    c2 = candles[-1]

    if direction == "LONG":

        return (
            c1["close"] > c1["open"]
            and
            c2["close"] > c2["open"]
        )

    if direction == "SHORT":

        return (
            c1["close"] < c1["open"]
            and
            c2["close"] < c2["open"]
        )

    return False


# ============================================================
# PRICE
# ============================================================

def format_price(price):

    if price is None:
        return "N/A"

    price = float(price)

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# PNL
# ============================================================

def calculate_pnl_pct(
    entry,
    exit_price,
    direction
):

    entry = float(entry)
    exit_price = float(exit_price)

    if entry == 0:
        return 0

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100

    return (
        (entry - exit_price)
        / entry
    ) * 100


# ============================================================
# NORMAL LEVELS
# ============================================================

def calculate_normal_levels(
    candles,
    direction
):

    atr = calculate_atr(
        candles
    )

    if atr is None:
        return None

    entry = candles[-1][
        "close"
    ]

    if direction == "LONG":

        sl = entry - (
            atr * 1.2
        )

        risk = entry - sl

        tp1 = entry + risk
        tp2 = entry + (
            risk * 1.5
        )
        tp3 = entry + (
            risk * 2
        )

    else:

        sl = entry + (
            atr * 1.2
        )

        risk = sl - entry

        tp1 = entry - risk
        tp2 = entry - (
            risk * 1.5
        )
        tp3 = entry - (
            risk * 2
        )

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk,
        "atr": atr
    }


# ============================================================
# BTC REGIME
# ============================================================

def btc_regime_from_candles(
    btc_candles
):

    if not btc_candles:
        return "UNKNOWN"

    if len(btc_candles) < 60:
        return "UNKNOWN"

    result = scanner_score(
        btc_candles
    )

    if result["score"] >= 20:
        return "BULLISH"

    if result["score"] <= -20:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# NORMAL MESSAGE
# ============================================================

def normal_signal_message(
    symbol,
    candles,
    analysis,
    btc_regime_value
):

    direction = analysis[
        "direction"
    ]

    levels = calculate_normal_levels(
        candles,
        direction
    )

    if not levels:
        return None

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    rsi = analysis[
        "rsi"
    ]

    rsi_text = (
        f"{rsi:.1f}"
        if rsi is not None
        else "N/A"
    )

    return (
        f"🚨 **NORMAL SIGNAL v5.5**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} **#{symbol} — "
        f"{direction}**\n\n"
        f"⭐ Score: "
        f"{analysis['score']}/100\n"
        f"💰 Entry: "
        f"{format_price(levels['entry'])}\n"
        f"🛑 SL: "
        f"{format_price(levels['sl'])}\n"
        f"🎯 TP1: "
        f"{format_price(levels['tp1'])}\n"
        f"🎯 TP2: "
        f"{format_price(levels['tp2'])}\n"
        f"🎯 TP3: "
        f"{format_price(levels['tp3'])}\n\n"
        f"RSI: {rsi_text}\n"
        f"Volume: "
        f"{analysis['vol_ratio']:.2f}x\n"
        f"Trendline: "
        f"{analysis['trendline']}\n"
        f"BTC Regime: "
        f"{btc_regime_value}"
    )


# ============================================================
# WATCHLIST
# ============================================================

def create_watchlist(
    results,
    limit=5
):

    sorted_results = sorted(
        results,
        key=lambda x: abs(
            x["analysis"]["score"]
        ),
        reverse=True
    )

    top = sorted_results[
        :limit
    ]

    lines = [
        "👀 **TOP 5 WATCHLIST v5.5**",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for i, item in enumerate(
        top,
        1
    ):

        symbol = item[
            "symbol"
        ]

        candles = item[
            "candles"
        ]

        a = item[
            "analysis"
        ]

        direction = a[
            "direction"
        ]

        if direction == "LONG":
            emoji = "🟢"

        elif direction == "SHORT":
            emoji = "🔴"

        else:
            emoji = "⚪"

        p5 = 0
        p10 = 0
        p15 = 0

        if len(candles) >= 2:

            p5 = pct_change(
                candles[-2]["close"],
                candles[-1]["close"]
            )

        if len(candles) >= 3:

            p10 = pct_change(
                candles[-3]["close"],
                candles[-1]["close"]
            )

        if len(candles) >= 4:

            p15 = pct_change(
                candles[-4]["close"],
                candles[-1]["close"]
            )

        rsi = a["rsi"]

        rsi_text = (
            f"{rsi:.1f}"
            if rsi is not None
            else "N/A"
        )

        lines.append(
            f"{i}. {emoji} **{symbol}** "
            f"⭐ {abs(a['score'])}/100\n"
            f"5m {p5:+.2f}% | "
            f"10m {p10:+.2f}% | "
            f"15m {p15:+.2f}%\n"
            f"RSI {rsi_text} | "
            f"Vol {a['vol_ratio']:.2f}x\n"
            f"📌 {a['trendline']} | "
            f"{direction}"
        )

    return "\n".join(lines)


# ============================================================
# UT ATR
# ============================================================

def calculate_ut_atr(
    candles,
    period=10
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )
        )

        trs.append(tr)

    return mean(
        trs[-period:]
    )


# ============================================================
# UT BOT SIGNAL
# ============================================================

def calculate_utbot_signal(
    candles
):

    if len(candles) < (
        UT_ATR_PERIOD + 5
    ):
        return None

    closes = [
        x["close"]
        for x in candles
    ]

    atr = calculate_ut_atr(
        candles,
        UT_ATR_PERIOD
    )

    if atr is None:
        return None

    stop_distance = (
        UT_KEY_VALUE
        * atr
    )

    trailing = [
        None
    ] * len(closes)

    trailing[0] = (
        closes[0]
        - stop_distance
    )

    for i in range(
        1,
        len(closes)
    ):

        prev_close = closes[
            i - 1
        ]

        close = closes[i]

        previous_stop = trailing[
            i - 1
        ]

        if previous_stop is None:

            previous_stop = (
                close
                - stop_distance
            )

        if (
            close > previous_stop
            and
            prev_close > previous_stop
        ):

            trailing[i] = max(
                previous_stop,
                close - stop_distance
            )

        elif (
            close < previous_stop
            and
            prev_close < previous_stop
        ):

            trailing[i] = min(
                previous_stop,
                close + stop_distance
            )

        elif close > previous_stop:

            trailing[i] = (
                close
                - stop_distance
            )

        else:

            trailing[i] = (
                close
                + stop_distance
            )

    previous_close = closes[
        -2
    ]

    current_close = closes[
        -1
    ]

    previous_stop = trailing[
        -2
    ]

    current_stop = trailing[
        -1
    ]

    if (
        previous_close <= previous_stop
        and
        current_close > current_stop
    ):

        signal = "BUY"

    elif (
        previous_close >= previous_stop
        and
        current_close < current_stop
    ):

        signal = "SELL"

    else:

        signal = None

    if not signal:
        return None

    return {
        "signal": signal,
        "price": current_close,
        "atr": atr,
        "candle_time": candles[-1]["time"],
        "trailing_stop": current_stop
    }


# ============================================================
# UT LEVELS
# ============================================================

def calculate_ut_setup_levels(
    candles,
    signal
):

    atr = calculate_ut_atr(
        candles,
        UT_ATR_PERIOD
    )

    if atr is None:
        return None

    entry = candles[-1][
        "close"
    ]

    if signal == "BUY":

        sl = entry - atr

        risk = entry - sl

        tp1 = entry + risk
        tp15 = entry + (
            risk * 1.5
        )
        tp2 = entry + (
            risk * 2
        )

    else:

        sl = entry + atr

        risk = sl - entry

        tp1 = entry - risk
        tp15 = entry - (
            risk * 1.5
        )
        tp2 = entry - (
            risk * 2
        )

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp15": tp15,
        "tp2": tp2,
        "risk": risk,
        "atr": atr
    }


# ============================================================
# UT MESSAGE
# ============================================================

def ut_setup_message(
    symbol,
    signal,
    levels,
    setup_id
):

    direction = (
        "LONG"
        if signal == "BUY"
        else "SHORT"
    )

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return (
        f"🚨 **UT BOT SIGNAL**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} **#{symbol} — "
        f"{direction}**\n\n"
        f"🆔 ID: `{setup_id}`\n\n"
        f"💰 Entry: "
        f"{format_price(levels['entry'])}\n"
        f"🛑 SL: "
        f"{format_price(levels['sl'])}\n"
        f"🎯 TP1: "
        f"{format_price(levels['tp1'])} — 1R\n"
        f"🎯 TP2: "
        f"{format_price(levels['tp15'])} — 1.5R\n"
        f"🎯 TP3: "
        f"{format_price(levels['tp2'])} — 2R\n\n"
        f"📊 ATR: "
        f"{format_price(levels['atr'])}"
    )


# ============================================================
# UT STATE
# ============================================================

def get_ut_state():

    state = load_json_state()

    if not isinstance(
        state,
        dict
    ):

        state = {}

    if "_utbot" not in state:

        state["_utbot"] = {}

    if not isinstance(
        state["_utbot"],
        dict
    ):

        state["_utbot"] = {}

    if "setups" not in state[
        "_utbot"
    ]:

        state[
            "_utbot"
        ]["setups"] = []

    if not isinstance(
        state["_utbot"]["setups"],
        list
    ):

        state[
            "_utbot"
        ]["setups"] = []

    if "stats" not in state[
        "_utbot"
    ]:

        state[
            "_utbot"
        ]["stats"] = {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "tp1": 0,
            "tp15": 0,
            "tp2": 0
        }

    stats = state[
        "_utbot"
    ]["stats"]

    for key in [
        "total",
        "wins",
        "losses",
        "tp1",
        "tp15",
        "tp2"
    ]:

        if key not in stats:
            stats[key] = 0

    # ========================================================
    # REPAIR OLD EXIT PRICE
    # ========================================================

    repaired = False

    for setup in state[
        "_utbot"
    ]["setups"]:

        if setup.get(
            "status"
        ) != "CLOSED":

            continue

        result = setup.get(
            "result"
        )

        entry = setup.get(
            "entry"
        )

        if entry is None:
            continue

        try:

            entry = float(entry)

        except Exception:

            continue

        old_exit = setup.get(
            "exit_price"
        )

        try:

            old_exit = (
                float(old_exit)
                if old_exit is not None
                else None
            )

        except Exception:

            old_exit = None

        direction = setup.get(
            "direction",
            "LONG"
        )

        target = None

        if result == "WIN_1R":

            target = setup.get(
                "tp1"
            )

        elif result == "WIN_15R":

            target = setup.get(
                "tp15"
            )

        elif result == "WIN_2R":

            target = setup.get(
                "tp2"
            )

        elif result == "LOSS":

            target = setup.get(
                "sl"
            )

        if target is None:
            continue

        try:

            target = float(target)

        except Exception:

            continue

        if (
            old_exit is None
            or
            abs(
                old_exit - entry
            ) < 1e-12
        ):

            setup[
                "exit_price"
            ] = target

            setup[
                "result_pct"
            ] = calculate_pnl_pct(
                entry,
                target,
                direction
            )

            repaired = True

    if repaired:

        save_json_state(
            state
        )

        print(
            "✓ Legacy UT records repaired."
        )

    return state


# ============================================================
# UT STATISTICS
# ============================================================

def calculate_ut_stats(state):

    setups = state[
        "_utbot"
    ]["setups"]

    total = len(
        setups
    )

    closed = [
        x
        for x in setups
        if x.get(
            "status"
        ) == "CLOSED"
    ]

    wins = [
        x
        for x in closed
        if str(
            x.get(
                "result",
                ""
            )
        ).startswith("WIN")
    ]

    losses = [
        x
        for x in closed
        if x.get(
            "result"
        ) == "LOSS"
    ]

    win_rate = (
        len(wins)
        / len(closed)
        * 100
        if closed
        else 0
    )

    return {
        "total": total,
        "closed": len(closed),
        "open": total - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate
    }


# ============================================================
# UPDATE UT SETUPS
# ============================================================

def update_ut_setups(
    state,
    candles_by_symbol
):

    changed = False

    setups = state[
        "_utbot"
    ]["setups"]

    for setup in setups:

        if setup.get(
            "status"
        ) == "CLOSED":

            continue

        symbol = setup.get(
            "symbol"
        )

        candles = candles_by_symbol.get(
            symbol
        )

        if not candles:
            continue

        entry = float(
            setup["entry"]
        )

        sl = float(
            setup["sl"]
        )

        tp1 = float(
            setup["tp1"]
        )

        tp15 = float(
            setup["tp15"]
        )

        tp2 = float(
            setup["tp2"]
        )

        direction = setup[
            "direction"
        ]

        last_processed = setup.get(
            "last_checked_candle",
            setup.get(
                "candle_time",
                0
            )
        )

        new_candles = [
            c
            for c in candles
            if c["time"]
            > last_processed
        ]

        if not new_candles:

            setup[
                "last_price"
            ] = candles[-1][
                "close"
            ]

            setup[
                "current_pnl_pct"
            ] = calculate_pnl_pct(
                entry,
                candles[-1]["close"],
                direction
            )

            continue

        for candle in new_candles:

            high = candle[
                "high"
            ]

            low = candle[
                "low"
            ]

            if direction == "LONG":

                hit_sl = low <= sl
                hit_tp1 = high >= tp1
                hit_tp15 = high >= tp15
                hit_tp2 = high >= tp2

            else:

                hit_sl = high >= sl
                hit_tp1 = low <= tp1
                hit_tp15 = low <= tp15
                hit_tp2 = low <= tp2

            # =================================================
            # SL PRIORITY
            # =================================================

            if hit_sl and (
                hit_tp1
                or hit_tp15
                or hit_tp2
            ):

                setup[
                    "status"
                ] = "CLOSED"

                setup[
                    "result"
                ] = "LOSS"

                setup[
                    "exit_price"
                ] = sl

                setup[
                    "result_pct"
                ] = calculate_pnl_pct(
                    entry,
                    sl,
                    direction
                )

                setup[
                    "close_candle_time"
                ] = candle[
                    "time"
                ]

                state[
                    "_utbot"
                ]["stats"][
                    "losses"
                ] += 1

                changed = True

                break

            # =================================================
            # TP3
            # =================================================

            if hit_tp2:

                setup[
                    "status"
                ] = "CLOSED"

                setup[
                    "result"
                ] = "WIN_2R"

                setup[
                    "exit_price"
                ] = tp2

                setup[
                    "result_pct"
                ] = calculate_pnl_pct(
                    entry,
                    tp2,
                    direction
                )

                setup[
                    "close_candle_time"
                ] = candle[
                    "time"
                ]

                state[
                    "_utbot"
                ]["stats"][
                    "wins"
                ] += 1

                state[
                    "_utbot"
                ]["stats"][
                    "tp2"
                ] += 1

                changed = True

                break

            # =================================================
            # TP2
            # =================================================

            if hit_tp15:

                setup[
                    "status"
                ] = "CLOSED"

                setup[
                    "result"
                ] = "WIN_15R"

                setup[
                    "exit_price"
                ] = tp15

                setup[
                    "result_pct"
                ] = calculate_pnl_pct(
                    entry,
                    tp15,
                    direction
                )

                setup[
                    "close_candle_time"
                ] = candle[
                    "time"
                ]

                state[
                    "_utbot"
                ]["stats"][
                    "wins"
                ] += 1

                state[
                    "_utbot"
                ]["stats"][
                    "tp15"
                ] += 1

                changed = True

                break

            # =================================================
            # TP1
            # =================================================

            if hit_tp1:

                setup[
                    "status"
                ] = "CLOSED"

                setup[
                    "result"
                ] = "WIN_1R"

                setup[
                    "exit_price"
                ] = tp1

                setup[
                    "result_pct"
                ] = calculate_pnl_pct(
                    entry,
                    tp1,
                    direction
                )

                setup[
                    "close_candle_time"
                ] = candle[
                    "time"
                ]

                state[
                    "_utbot"
                ]["stats"][
                    "wins"
                ] += 1

                state[
                    "_utbot"
                ]["stats"][
                    "tp1"
                ] += 1

                changed = True

                break

            setup[
                "last_checked_candle"
            ] = candle[
                "time"
            ]

        if setup.get(
            "status"
        ) != "CLOSED":

            setup[
                "last_checked_candle"
            ] = new_candles[
                -1
            ]["time"]

            setup[
                "last_price"
            ] = candles[-1][
                "close"
            ]

            setup[
                "current_pnl_pct"
            ] = calculate_pnl_pct(
                entry,
                candles[-1]["close"],
                direction
            )

            changed = True

    if changed:

        save_json_state(
            state
        )

    return state


# ============================================================
# UT LIVE STATUS
# ============================================================

def ut_live_status_message(
    state,
    candles_by_symbol
):

    setups = state[
        "_utbot"
    ]["setups"]

    if not setups:

        return (
            "📊 **UT BOT LIVE STATUS**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📭 هنوز سیگنال UT Bot ثبت نشده است."
        )

    lines = [
        "📊 **UT BOT LIVE STATUS**",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for i, setup in enumerate(
        setups,
        1
    ):

        symbol = setup.get(
            "symbol",
            "?"
        )

        direction = setup.get(
            "direction",
            "LONG"
        )

        status = setup.get(
            "status",
            "OPEN"
        )

        setup_id = setup.get(
            "id",
            "N/A"
        )

        entry = float(
            setup.get(
                "entry",
                0
            )
        )

        candles = candles_by_symbol.get(
            symbol
        )

        if status != "CLOSED":

            current_price = setup.get(
                "last_price",
                entry
            )

            if candles:

                current_price = candles[
                    -1
                ]["close"]

            pnl = calculate_pnl_pct(
                entry,
                current_price,
                direction
            )

            pnl_emoji = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            lines.append(
                f"\n"
                f"{'🟢' if direction == 'LONG' else '🔴'} "
                f"**#{i} {symbol}/USDT — "
                f"{direction}**\n"
                f"🆔 ID: `{setup_id}`\n"
                f"📂 Status: **OPEN**\n"
                f"💰 Entry: "
                f"{format_price(entry)}\n"
                f"📍 Current: "
                f"{format_price(current_price)}\n"
                f"{pnl_emoji} PnL: "
                f"{pnl:+.2f}%\n"
                f"🛑 SL: "
                f"{format_price(setup.get('sl'))}\n"
                f"🎯 TP1: "
                f"{format_price(setup.get('tp1'))}\n"
                f"🎯 TP2: "
                f"{format_price(setup.get('tp15'))}\n"
                f"🎯 TP3: "
                f"{format_price(setup.get('tp2'))}"
            )

        else:

            result = setup.get(
                "result",
                "UNKNOWN"
            )

            exit_price = setup.get(
                "exit_price",
                entry
            )

            result_pct = setup.get(
                "result_pct",
                0
            )

            if result == "LOSS":

                result_text = "❌ SL HIT"
                r_text = "0R"

            elif result == "WIN_1R":

                result_text = "✅ TP1 HIT — 1R"
                r_text = "1R"

            elif result == "WIN_15R":

                result_text = "✅ TP2 HIT — 1.5R"
                r_text = "1.5R"

            elif result == "WIN_2R":

                result_text = "🏆 TP3 HIT — 2R"
                r_text = "2R"

            else:

                result_text = (
                    f"ℹ️ {result}"
                )

                r_text = "?"

            lines.append(
                f"\n"
                f"{'🟢' if direction == 'LONG' else '🔴'} "
                f"**#{i} {symbol}/USDT — "
                f"{direction}**\n"
                f"🆔 ID: `{setup_id}`\n"
                f"📂 Status: **CLOSED**\n"
                f"💰 Entry: "
                f"{format_price(entry)}\n"
                f"🏁 Exit: "
                f"{format_price(exit_price)}\n"
                f"🎯 Result R: "
                f"{r_text}\n"
                f"📊 Result %: "
                f"{float(result_pct):+.2f}%\n"
                f"{result_text}"
            )

    stats = calculate_ut_stats(
        state
    )

    lines.append(
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📈 **UT BOT STATISTICS**\n"
        f"📊 Total Signals: "
        f"{stats['total']}\n"
        f"🟢 Wins: "
        f"{stats['wins']}\n"
        f"🔴 Losses: "
        f"{stats['losses']}\n"
        f"🟡 Open: "
        f"{stats['open']}\n"
        f"📉 Closed: "
        f"{stats['closed']}\n"
        f"🎯 Win Rate: "
        f"{stats['win_rate']:.1f}%"
    )

    return "\n".join(
        lines
    )


# ============================================================
# PROCESS UT TOP 5
# ============================================================

def process_ut_top5(
    top5_results,
    state,
    candles_by_symbol
):

    existing_keys = set()

    for setup in state[
        "_utbot"
    ]["setups"]:

        key = setup.get(
            "signal_key"
        )

        if key:
            existing_keys.add(
                key
            )

    new_signals = []

    for item in top5_results:

        symbol = item[
            "symbol"
        ]

        candles = item[
            "candles"
        ]

        ut = calculate_utbot_signal(
            candles
        )

        if not ut:
            continue

        signal = ut[
            "signal"
        ]

        candle_time = ut[
            "candle_time"
        ]

        signal_key = (
            f"{symbol}_"
            f"{signal}_"
            f"{candle_time}"
        )

        if signal_key in existing_keys:
            continue

        levels = calculate_ut_setup_levels(
            candles,
            signal
        )

        if not levels:
            continue

        direction = (
            "LONG"
            if signal == "BUY"
            else "SHORT"
        )

        setup_id = (
            f"UT-{symbol}-"
            f"{signal}-"
            f"{int(candle_time)}"
        )

        setup = {
            "id": setup_id,
            "symbol": symbol,
            "direction": direction,
            "signal": signal,
            "signal_key": signal_key,
            "candle_time": candle_time,

            "entry": levels["entry"],
            "sl": levels["sl"],
            "tp1": levels["tp1"],
            "tp15": levels["tp15"],
            "tp2": levels["tp2"],
            "risk": levels["risk"],
            "atr": levels["atr"],

            "status": "OPEN",
            "result": None,
            "exit_price": None,
            "result_pct": 0,

            "last_price": levels["entry"],
            "current_pnl_pct": 0,

            "last_checked_candle": candle_time,

            "created_at": int(
                time.time()
            )
        }

        state[
            "_utbot"
        ]["setups"].append(
            setup
        )

        existing_keys.add(
            signal_key
        )

        new_signals.append(
            setup
        )

    if new_signals:

        state[
            "_utbot"
        ]["stats"]["total"] = len(
            state[
                "_utbot"
            ]["setups"]
        )

        save_json_state(
            state
        )

        for setup in new_signals:

            levels = {
                "entry": setup["entry"],
                "sl": setup["sl"],
                "tp1": setup["tp1"],
                "tp15": setup["tp15"],
                "tp2": setup["tp2"],
                "risk": setup["risk"],
                "atr": setup["atr"]
            }

            message = ut_setup_message(
                setup["symbol"],
                setup["signal"],
                levels,
                setup["id"]
            )

            send_telegram_chunks(
                message
            )

    return state


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    total_started = time.time()

    print(
        "\n"
        "========================================\n"
        f"CRYPTO PUMP / DUMP SCANNER "
        f"{VERSION}\n"
        "KRAKEN FUTURES / CLOSED 5M CANDLES\n"
        "========================================\n"
    )

    # ========================================================
    # STATE
    # ========================================================

    state_started = time.time()

    state = get_ut_state()

    print(
        f"State: "
        f"{time.time() - state_started:.2f}s"
    )

    # ========================================================
    # KRAKEN
    # ========================================================

    fetch_started = time.time()

    results = fetch_all_coins_fast()

    print(
        f"Fetch section: "
        f"{time.time() - fetch_started:.2f}s"
    )

    if not results:

        print(
            "❌ No Kraken results."
        )

        return

    # ========================================================
    # CANDLE MAP
    # ========================================================

    candles_by_symbol = {}

    for item in results:

        candles_by_symbol[
            item["symbol"]
        ] = item["candles"]

    # ========================================================
    # TOP 5
    # ========================================================

    sorted_results = sorted(
        results,
        key=lambda x: abs(
            x["analysis"]["score"]
        ),
        reverse=True
    )

    top5 = sorted_results[
        :5
    ]

    # ========================================================
    # BTC REGIME
    # ========================================================

    btc_regime_value = (
        btc_regime_from_candles(
            candles_by_symbol.get(
                "BTC"
            )
        )
    )

    print(
        f"BTC Regime: "
        f"{btc_regime_value}"
    )

    # ========================================================
    # WATCHLIST
    # ========================================================

    watchlist_started = time.time()

    watchlist = create_watchlist(
        results,
        5
    )

    send_telegram_chunks(
        watchlist
    )

    print(
        f"Watchlist: "
        f"{time.time() - watchlist_started:.2f}s"
    )

    # ========================================================
    # NORMAL SIGNALS
    # ========================================================

    normal_started = time.time()

    normal_messages = []

    for item in top5:

        candles = item[
            "candles"
        ]

        analysis = item[
            "analysis"
        ]

        if analysis[
            "direction"
        ] == "NEUTRAL":

            continue

        if not two_candle_confirmation(
            candles,
            analysis["direction"]
        ):

            continue

        message = normal_signal_message(
            item["symbol"],
            candles,
            analysis,
            btc_regime_value
        )

        if message:

            normal_messages.append(
                message
            )

    # ========================================================
    # NORMAL SIGNALS:
    # SEND AS ONE TELEGRAM MESSAGE
    # ========================================================

    if normal_messages:

        combined_normal = (
            "\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        ).join(
            normal_messages
        )

        send_telegram_chunks(
            combined_normal
        )

    print(
        f"Normal signals: "
        f"{time.time() - normal_started:.2f}s"
    )

    # ========================================================
    # UPDATE OLD UT
    # ========================================================

    update_started = time.time()

    state = update_ut_setups(
        state,
        candles_by_symbol
    )

    print(
        f"UT update: "
        f"{time.time() - update_started:.2f}s"
    )

    # ========================================================
    # NEW UT
    # ========================================================

    ut_started = time.time()

    state = process_ut_top5(
        top5,
        state,
        candles_by_symbol
    )

    print(
        f"UT new signals: "
        f"{time.time() - ut_started:.2f}s"
    )

    # ========================================================
    # LIVE STATUS
    # ========================================================

    status_started = time.time()

    live_status = ut_live_status_message(
        state,
        candles_by_symbol
    )

    send_telegram_chunks(
        live_status
    )

    print(
        f"Live status: "
        f"{time.time() - status_started:.2f}s"
    )

    # ========================================================
    # TOTAL
    # ========================================================

    total_time = (
        time.time()
        - total_started
    )

    print(
        "\n"
        "========================================"
    )

    print(
        f"⚡ TOTAL SCAN TIME: "
        f"{total_time:.2f} seconds"
    )

    print(
        f"📊 Coins scanned: "
        f"{len(results)}/{len(COINS)}"
    )

    if total_time <= 15:

        print(
            "🚀 SCAN SPEED: EXCELLENT"
        )

    elif total_time <= 30:

        print(
            "🟢 SCAN SPEED: GOOD"
        )

    elif total_time <= 60:

        print(
            "🟡 SCAN SPEED: ACCEPTABLE"
        )

    else:

        print(
            "🔴 SCAN SPEED: SLOW"
        )

    print(
        "========================================\n"
    )


# ============================================================
# MAIN LOOP
# ============================================================

if __name__ == "__main__":

    while True:

        try:

            run_scanner()

        except KeyboardInterrupt:

            print(
                "\nScanner stopped."
            )

            break

        except Exception as e:

            print(
                "\nMAIN ERROR:",
                e
            )

        # ====================================================
        # NEXT CLOSED 5M CANDLE
        # ====================================================

        now = int(
            time.time()
        )

        next_5m = (
            (
                (now // 300) + 1
            )
            * 300
        )

        sleep_seconds = (
            next_5m
            - now
            + 5
        )

        print(
            f"Next scan in "
            f"{sleep_seconds} seconds..."
        )

        time.sleep(
            max(
                10,
                sleep_seconds
            )
        )
