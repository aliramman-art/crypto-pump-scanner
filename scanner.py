import os
import json
import time
import requests
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "5.6 FAST"

# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://futures.kraken.com/api/charts/v1"

STATE_FILE = "signal_state.json"

CANDLE_HISTORY = 500

# تعداد همزمان درخواست‌ها
MAX_WORKERS = 10

# اتصال حداکثر 3 ثانیه
# خواندن پاسخ حداکثر 5 ثانیه
KRAKEN_TIMEOUT = (3, 5)

TELEGRAM_TIMEOUT = (3, 5)

# =========================
# UT BOT
# =========================

UT_KEY_VALUE = 3
UT_ATR_PERIOD = 10

# =========================
# COINS
# =========================

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


# =========================
# HTTP SESSION
# =========================

session = requests.Session()

session.headers.update({
    "User-Agent": "CryptoScanner/5.6"
})


# =========================
# STATE
# =========================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "ut_setups": {},
            "stats": {
                "total": 0,
                "wins": 0,
                "losses": 0
            }
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        state.setdefault("ut_setups", {})
        state.setdefault(
            "stats",
            {
                "total": 0,
                "wins": 0,
                "losses": 0
            }
        )

        return state

    except Exception:
        return {
            "ut_setups": {},
            "stats": {
                "total": 0,
                "wins": 0,
                "losses": 0
            }
        }


def save_state(state):
    tmp = STATE_FILE + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp, STATE_FILE)


# =========================
# TELEGRAM
# =========================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        r = session.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text
            },
            timeout=TELEGRAM_TIMEOUT
        )

        return r.ok

    except Exception as e:
        print("Telegram error:", repr(e))
        return False


def send_telegram_chunks(text, max_len=4000):
    chunks = []

    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)

        if cut <= 0:
            cut = max_len

        chunks.append(text[:cut])
        text = text[cut:]

    if text:
        chunks.append(text)

    for chunk in chunks:
        send_telegram(chunk)


# =========================
# KRAKEN CANDLES
# =========================

def normalize_candle(c):
    try:

        if isinstance(c, dict):

            ts = (
                c.get("time")
                or c.get("timestamp")
                or c.get("t")
            )

            o = c.get("open", c.get("o"))
            h = c.get("high", c.get("h"))
            l = c.get("low", c.get("l"))
            cl = c.get("close", c.get("c"))
            v = c.get("volume", c.get("v", 0))

        elif isinstance(c, (list, tuple)):

            if len(c) < 5:
                return None

            ts = c[0]
            o = c[1]
            h = c[2]
            l = c[3]
            cl = c[4]
            v = c[5] if len(c) > 5 else 0

        else:
            return None

        return {
            "time": int(float(ts)),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(cl),
            "volume": float(v or 0)
        }

    except Exception:
        return None


def get_candles(symbol, interval=5):

    url = f"{BASE_URL}/{symbol}/{interval}"

    start = time.time()

    try:

        r = session.get(
            url,
            timeout=KRAKEN_TIMEOUT
        )

        elapsed = time.time() - start

        if r.status_code != 200:
            print(
                f"❌ {symbol}: HTTP {r.status_code} "
                f"({elapsed:.2f}s)"
            )
            return []

        data = r.json()

        candles = None

        if isinstance(data, dict):

            candles = (
                data.get("candles")
                or data.get("data")
                or data.get("result")
            )

        elif isinstance(data, list):

            candles = data

        if not candles:
            print(f"⚠️ {symbol}: no candles")
            return []

        result = []

        for c in candles:

            item = normalize_candle(c)

            if item:
                result.append(item)

        result.sort(key=lambda x: x["time"])

        # حذف کندل باز فعلی
        if len(result) >= 2:

            now = int(time.time())

            last_ts = result[-1]["time"]

            candle_seconds = interval * 60

            if last_ts + candle_seconds > now:
                result = result[:-1]

        result = result[-CANDLE_HISTORY:]

        print(
            f"✅ {symbol}: {len(result)} candles "
            f"({elapsed:.2f}s)"
        )

        return result

    except requests.exceptions.Timeout:

        print(f"⏱️ {symbol}: timeout")
        return []

    except Exception as e:

        print(
            f"❌ {symbol}: "
            f"{type(e).__name__}: {e}"
        )

        return []


# =========================
# BASIC INDICATORS
# =========================

def pct_change(a, b):

    if not b:
        return 0

    return ((a - b) / b) * 100


def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):

        diff = closes[i] - closes[i - 1]

        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]

        tr = max(
            h - l,
            abs(h - pc),
            abs(l - pc)
        )

        trs.append(tr)

    return mean(trs[-period:])


def volume_ratio(candles, period=20):

    if len(candles) < period + 1:
        return 1

    current = candles[-1]["volume"]

    avg = mean(
        c["volume"]
        for c in candles[-period-1:-1]
    )

    if avg <= 0:
        return 1

    return current / avg


# =========================
# ICHIMOKU
# =========================

def calculate_ichimoku(candles):

    if len(candles) < 52:
        return None

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    tenkan_high = max(highs[-9:])
    tenkan_low = min(lows[-9:])

    kijun_high = max(highs[-26:])
    kijun_low = min(lows[-26:])

    tenkan = (tenkan_high + tenkan_low) / 2
    kijun = (kijun_high + kijun_low) / 2

    span_a = (tenkan + kijun) / 2

    span_b_high = max(highs[-52:])
    span_b_low = min(lows[-52:])

    span_b = (span_b_high + span_b_low) / 2

    price = candles[-1]["close"]

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "price": price
    }


# =========================
# SWINGS
# =========================

def swing_high(candles, lookback=20):

    if len(candles) < lookback:
        return None

    return max(
        c["high"]
        for c in candles[-lookback:]
    )


def swing_low(candles, lookback=20):

    if len(candles) < lookback:
        return None

    return min(
        c["low"]
        for c in candles[-lookback:]
    )


# =========================
# TRENDLINE
# =========================

def trendline_status(candles):

    if len(candles) < 30:
        return "NEUTRAL"

    price = candles[-1]["close"]

    high = swing_high(candles, 30)
    low = swing_low(candles, 30)

    if high is None or low is None:
        return "NEUTRAL"

    if price > high:
        return "BREAKOUT"

    if price < low:
        return "BREAKDOWN"

    return "NEUTRAL"


# =========================
# SCORING
# =========================

def scanner_score(candles):

    if len(candles) < 60:
        return {
            "score": 0,
            "rsi": None,
            "volume_ratio": 1,
            "ichimoku": None,
            "trendline": "NEUTRAL"
        }

    closes = [c["close"] for c in candles]

    price = closes[-1]

    rsi = calculate_rsi(closes)

    vr = volume_ratio(candles)

    ichi = calculate_ichimoku(candles)

    trendline = trendline_status(candles)

    score = 0

    # RSI
    if rsi is not None:

        if rsi >= 70:
            score += 15

        elif rsi >= 60:
            score += 10

        elif rsi <= 30:
            score -= 15

        elif rsi <= 40:
            score -= 10

    # Volume
    if vr >= 2:
        score += 15

    elif vr >= 1.3:
        score += 8

    elif vr <= 0.5:
        score -= 5

    # Ichimoku
    if ichi:

        tenkan = ichi["tenkan"]
        kijun = ichi["kijun"]
        span_a = ichi["span_a"]
        span_b = ichi["span_b"]

        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)

        if price > cloud_top:
            score += 15

        elif price < cloud_bottom:
            score -= 15

        if tenkan > kijun:
            score += 10

        elif tenkan < kijun:
            score -= 10

    # Trendline
    if trendline == "BREAKOUT":
        score += 15

    elif trendline == "BREAKDOWN":
        score -= 15

    score = max(-100, min(100, score))

    return {
        "score": score,
        "rsi": rsi,
        "volume_ratio": vr,
        "ichimoku": ichi,
        "trendline": trendline
    }


# =========================
# FETCH COIN
# =========================

def fetch_coin_data(item):

    name, symbol = item

    candles = get_candles(symbol)

    if not candles:
        return name, symbol, [], None

    result = scanner_score(candles)

    return name, symbol, candles, result


def fetch_all_coins_fast():

    start = time.time()

    results = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                fetch_coin_data,
                item
            )
            for item in COINS.items()
        ]

        for future in as_completed(futures):

            try:

                name, symbol, candles, result = future.result()

                results[name] = {
                    "symbol": symbol,
                    "candles": candles,
                    "analysis": result
                }

            except Exception as e:

                print(
                    "Worker error:",
                    repr(e)
                )

    elapsed = time.time() - start

    print(
        f"\n⏱️ Kraken scan: "
        f"{elapsed:.2f}s"
    )

    print(
        f"📊 Successful coins: "
        f"{len(results)}/{len(COINS)}"
    )

    return results


# =========================
# CONFIRMATION
# =========================

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


# =========================
# PRICE FORMAT
# =========================

def fmt_price(price):

    if price is None:
        return "N/A"

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.5f}"

    return f"{price:.8f}"


# =========================
# NORMAL LEVELS
# =========================

def normal_levels(
    candles,
    direction
):

    price = candles[-1]["close"]

    atr = calculate_atr(candles)

    if not atr or atr <= 0:
        atr = price * 0.01

    if direction == "LONG":

        sl = price - atr * 1.2

        tp1 = price + atr * 1.0
        tp2 = price + atr * 2.0
        tp3 = price + atr * 3.0

    else:

        sl = price + atr * 1.2

        tp1 = price - atr * 1.0
        tp2 = price - atr * 2.0
        tp3 = price - atr * 3.0

    return {
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3
    }


# =========================
# BTC REGIME
# =========================

def btc_regime_from_candles(candles):

    if not candles:
        return "NEUTRAL"

    result = scanner_score(candles)

    score = result["score"]

    if score >= 20:
        return "BULLISH"

    if score <= -20:
        return "BEARISH"

    return "NEUTRAL"


# =========================
# WATCHLIST
# =========================

def create_watchlist(results):

    valid = []

    for name, data in results.items():

        analysis = data.get("analysis")

        if not analysis:
            continue

        valid.append(
            (
                name,
                data,
                analysis
            )
        )

    valid.sort(
        key=lambda x: abs(x[2]["score"]),
        reverse=True
    )

    return valid[:5]


def watchlist_message(
    top5,
    btc_regime
):

    lines = [
        f"👀 TOP 5 WATCHLIST v{VERSION}",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for i, (
        name,
        data,
        analysis
    ) in enumerate(top5, 1):

        candles = data["candles"]

        score = analysis["score"]

        icon = "🟢" if score > 0 else "🔴"

        p5 = 0
        p10 = 0
        p15 = 0

        if len(candles) >= 4:
            p5 = pct_change(
                candles[-1]["close"],
                candles[-2]["close"]
            )

        if len(candles) >= 5:
            p10 = pct_change(
                candles[-1]["close"],
                candles[-3]["close"]
            )

        if len(candles) >= 7:
            p15 = pct_change(
                candles[-1]["close"],
                candles[-4]["close"]
            )

        rsi = analysis["rsi"]

        rsi_text = (
            f"{rsi:.1f}"
            if rsi is not None
            else "N/A"
        )

        vr = analysis["volume_ratio"]

        lines.append(
            f"{i}. {icon} #{name} ⭐ "
            f"{score}/100\n"
            f"5m {p5:+.2f}% | "
            f"10m {p10:+.2f}% | "
            f"15m {p15:+.2f}%\n"
            f"RSI {rsi_text} | "
            f"Vol {vr:.2f}x\n"
            f"📌 {analysis['trendline']}"
        )

    lines.append("")
    lines.append(
        f"₿ BTC REGIME: {btc_regime}"
    )

    return "\n".join(lines)


# =========================
# NORMAL SIGNAL
# =========================

def normal_signal_message(
    name,
    candles,
    analysis,
    direction
):

    levels = normal_levels(
        candles,
        direction
    )

    emoji = (
        "🟢 LONG"
        if direction == "LONG"
        else "🔴 SHORT"
    )

    return (
        f"🚨 NORMAL SIGNAL\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"#{name}/USDT\n"
        f"{emoji}\n\n"
        f"⭐ Score: {analysis['score']}/100\n"
        f"RSI: "
        f"{analysis['rsi']:.1f}\n"
        f"Volume: "
        f"{analysis['volume_ratio']:.2f}x\n"
        f"Trendline: "
        f"{analysis['trendline']}\n\n"
        f"💰 Entry: "
        f"{fmt_price(levels['entry'])}\n"
        f"🛑 SL: "
        f"{fmt_price(levels['sl'])}\n"
        f"🎯 TP1: "
        f"{fmt_price(levels['tp1'])}\n"
        f"🎯 TP2: "
        f"{fmt_price(levels['tp2'])}\n"
        f"🎯 TP3: "
        f"{fmt_price(levels['tp3'])}"
    )


# =========================
# UT ATR
# =========================

def ut_atr(candles):

    return calculate_atr(
        candles,
        UT_ATR_PERIOD
    )


# =========================
# UT BOT SIGNAL
# =========================

def ut_bot_signal(candles):

    if len(candles) < 30:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

    atr = ut_atr(candles)

    if not atr or atr <= 0:
        return None

    key = UT_KEY_VALUE

    stop = closes[0]

    previous_stop = stop

    signal = None

    for i in range(1, len(closes)):

        price = closes[i]

        previous_price = closes[i - 1]

        if (
            price > previous_stop
            and previous_price > previous_stop
        ):

            stop = max(
                previous_stop,
                price - key * atr
            )

        elif (
            price < previous_stop
            and previous_price < previous_stop
        ):

            stop = min(
                previous_stop,
                price + key * atr
            )

        elif price > previous_stop:

            stop = price - key * atr

        else:

            stop = price + key * atr

        if (
            previous_price <= previous_stop
            and price > stop
        ):
            signal = "LONG"

        elif (
            previous_price >= previous_stop
            and price < stop
        ):
            signal = "SHORT"

        previous_stop = stop

    return signal


# =========================
# UT LEVELS
# =========================

def ut_setup_levels(
    candles,
    direction
):

    entry = candles[-1]["close"]

    atr = ut_atr(candles)

    if not atr or atr <= 0:
        return None

    if direction == "LONG":

        sl = entry - atr * 1.5

        tp1 = entry + atr * 1.0
        tp15 = entry + atr * 1.5
        tp2 = entry + atr * 2.0

    else:

        sl = entry + atr * 1.5

        tp1 = entry - atr * 1.0
        tp15 = entry - atr * 1.5
        tp2 = entry - atr * 2.0

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp15": tp15,
        "tp2": tp2
    }


# =========================
# UT MESSAGE
# =========================

def ut_setup_message(
    name,
    direction,
    levels,
    setup_id
):

    emoji = (
        "🟢 LONG"
        if direction == "LONG"
        else "🔴 SHORT"
    )

    return (
        f"🚨 UT BOT SIGNAL\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"#{name}/USDT\n"
        f"{emoji}\n"
        f"🆔 ID: {setup_id}\n\n"
        f"💰 Entry: "
        f"{fmt_price(levels['entry'])}\n"
        f"🛑 SL: "
        f"{fmt_price(levels['sl'])}\n"
        f"🎯 TP1: "
        f"{fmt_price(levels['tp1'])}\n"
        f"🎯 TP1.5: "
        f"{fmt_price(levels['tp15'])}\n"
        f"🎯 TP2: "
        f"{fmt_price(levels['tp2'])}"
    )


# =========================
# LEGACY STATE REPAIR
# =========================

def get_ut_state():

    state = load_state()

    repaired = False

    for setup in state.get(
        "ut_setups",
        {}
    ).values():

        if setup.get("status") != "CLOSED":
            continue

        entry = setup.get("entry")
        result = setup.get("result")

        if entry is None:
            continue

        old_exit = setup.get("exit_price")

        if (
            old_exit is not None
            and abs(old_exit - entry) > 1e-12
        ):
            continue

        target = None

        if result == "WIN_1R":
            target = setup.get("tp1")

        elif result == "WIN_15R":
            target = setup.get("tp15")

        elif result == "WIN_2R":
            target = setup.get("tp2")

        elif result == "LOSS":
            target = setup.get("sl")

        if target is not None:

            setup["exit_price"] = target

            direction = setup.get(
                "direction"
            )

            if direction == "LONG":

                setup["result_pct"] = (
                    (target - entry)
                    / entry
                    * 100
                )

            elif direction == "SHORT":

                setup["result_pct"] = (
                    (entry - target)
                    / entry
                    * 100
                )

            repaired = True

    if repaired:
        save_state(state)

    return state


# =========================
# STATS
# =========================

def calculate_ut_stats(state):

    total = 0
    wins = 0
    losses = 0

    for setup in state.get(
        "ut_setups",
        {}
    ).values():

        if setup.get("status") != "CLOSED":
            continue

        total += 1

        if str(
            setup.get("result", "")
        ).startswith("WIN"):

            wins += 1

        elif setup.get("result") == "LOSS":

            losses += 1

    winrate = (
        wins / total * 100
        if total
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winrate": winrate
    }


# =========================
# UPDATE UT SETUPS
# =========================

def update_ut_setups(
    state,
    results
):

    changed = False

    for setup_id, setup in list(
        state.get(
            "ut_setups",
            {}
        ).items()
    ):

        if setup.get("status") != "OPEN":
            continue

        symbol = setup.get("symbol")

        name = setup.get("name")

        if not symbol or not name:
            continue

        data = results.get(name)

        if not data:
            continue

        candles = data.get(
            "candles",
            []
        )

        if not candles:
            continue

        last_checked = setup.get(
            "last_checked_candle"
        )

        new_candles = candles

        if last_checked is not None:

            new_candles = [
                c for c in candles
                if c["time"] > last_checked
            ]

        if not new_candles:
            continue

        direction = setup.get(
            "direction"
        )

        entry = setup["entry"]

        sl = setup["sl"]

        tp1 = setup["tp1"]

        tp15 = setup["tp15"]

        tp2 = setup["tp2"]

        closed = False

        for candle in new_candles:

            high = candle["high"]
            low = candle["low"]

            if direction == "LONG":

                # SL first
                if low <= sl:

                    setup["status"] = "CLOSED"
                    setup["result"] = "LOSS"
                    setup["exit_price"] = sl

                    setup["result_pct"] = (
                        (sl - entry)
                        / entry
                        * 100
                    )

                    closed = True
                    break

                # highest target first
                if high >= tp2:

                    setup["status"] = "CLOSED"
                    setup["result"] = "WIN_2R"
                    setup["exit_price"] = tp2

                    setup["result_pct"] = (
                        (tp2 - entry)
                        / entry
                        * 100
                    )

                    closed = True
                    break

                if high >= tp15:

                    setup["status"] = "CLOSED"
                    setup["result"] = "WIN_15R"
                    setup["exit_price"] = tp15

                    setup["result_pct"] = (
                        (tp15 - entry)
                        / entry
                        * 100
                    )

                    closed = True
                    break

                if high >= tp1:

                    setup["status"] = "CLOSED"
                    setup["result"] = "WIN_1R"
                    setup["exit_price"] = tp1

                    setup["result_pct"] = (
                        (tp1 - entry)
                        / entry
                        * 100
                    )

                    closed = True
                    break

            else:

                if high >= sl:

                    setup["status"] = "CLOSED"
                    setup["result"] = "LOSS"
                    setup["exit_price"] = sl

                    setup["result_pct"] = (
                        (entry - sl)
                        / entry
                        * 100
                    )

                    closed = True
                    break

                if low <= tp2:

                    setup["status"] = "CLOSED"
                    setup["result"] = "WIN_2R"
                    setup["exit_price"] = tp2

                    setup["result_pct"] = (
                        (entry - tp2)
                        / entry
                        * 100
                    )

                    closed = True
                    break

                if low <= tp15:

                    setup["status"] = "CLOSED"
                    setup["result"] = "WIN_15R"
                    setup["exit_price"] = tp15

                    setup["result_pct"] = (
                        (entry - tp15)
                        / entry
                        * 100
                    )

                    closed = True
                    break

                if low <= tp1:

                    setup["status"] = "CLOSED"
                    setup["result"] = "WIN_1R"
                    setup["exit_price"] = tp1

                    setup["result_pct"] = (
                        (entry - tp1)
                        / entry
                        * 100
                    )

                    closed = True
                    break

        if not closed:

            last = candles[-1]

            setup["last_checked_candle"] = last["time"]

            setup["last_price"] = last["close"]

            if direction == "LONG":

                setup["current_pnl_pct"] = (
                    (last["close"] - entry)
                    / entry
                    * 100
                )

            else:

                setup["current_pnl_pct"] = (
                    (entry - last["close"])
                    / entry
                    * 100
                )

        changed = True

    if changed:
        save_state(state)

    return state


# =========================
# LIVE STATUS
# =========================

def ut_live_status_message(state):

    stats = calculate_ut_stats(state)

    lines = [
        "📊 UT BOT LIVE STATUS",
        "━━━━━━━━━━━━━━━━━━"
    ]

    setups = state.get(
        "ut_setups",
        {}
    )

    if not setups:

        lines.append(
            "هیچ Setup ثبت نشده."
        )

    else:

        for setup_id, setup in list(
            setups.items()
        )[-10:]:

            status = setup.get(
                "status",
                "OPEN"
            )

            direction = setup.get(
                "direction",
                "?"
            )

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            name = setup.get(
                "name",
                "?"
            )

            entry = setup.get(
                "entry"
            )

            exit_price = setup.get(
                "exit_price"
            )

            result = setup.get(
                "result"
            )

            pnl = setup.get(
                "current_pnl_pct"
            )

            lines.append(
                f"{emoji} #{name}/USDT — "
                f"{direction}"
            )

            lines.append(
                f"🆔 ID: {setup_id}"
            )

            lines.append(
                f"📂 Status: {status}"
            )

            lines.append(
                f"💰 Entry: "
                f"{fmt_price(entry)}"
            )

            if status == "CLOSED":

                lines.append(
                    f"🏁 Exit: "
                    f"{fmt_price(exit_price)}"
                )

                if result:
                    lines.append(
                        f"🎯 Result: {result}"
                    )

            else:

                if pnl is not None:
                    lines.append(
                        f"📊 PnL: "
                        f"{pnl:+.2f}%"
                    )

            lines.append("")

    lines.append(
        f"📈 Total: {stats['total']}"
    )

    lines.append(
        f"✅ Wins: {stats['wins']}"
    )

    lines.append(
        f"❌ Losses: {stats['losses']}"
    )

    lines.append(
        f"🎯 Winrate: "
        f"{stats['winrate']:.1f}%"
    )

    return "\n".join(lines)


# =========================
# PROCESS UT TOP 5
# =========================

def process_ut_top5(
    state,
    top5
):

    new_signals = []

    existing_keys = {
        s.get("signal_key")
        for s in state.get(
            "ut_setups",
            {}
        ).values()
    }

    for name, data, analysis in top5:

        candles = data.get(
            "candles",
            []
        )

        if len(candles) < 30:
            continue

        direction = ut_bot_signal(
            candles
        )

        if direction not in (
            "LONG",
            "SHORT"
        ):
            continue

        signal_key = (
            f"{name}_"
            f"{direction}_"
            f"{candles[-1]['time']}"
        )

        if signal_key in existing_keys:
            continue

        levels = ut_setup_levels(
            candles,
            direction
        )

        if not levels:
            continue

        setup_id = (
            f"{name}_"
            f"{'BUY' if direction == 'LONG' else 'SELL'}_"
            f"{int(time.time())}"
        )

        setup = {
            "id": setup_id,
            "name": name,
            "symbol": data["symbol"],
            "direction": direction,
            "status": "OPEN",

            "signal_key": signal_key,

            "entry": levels["entry"],
            "sl": levels["sl"],
            "tp1": levels["tp1"],
            "tp15": levels["tp15"],
            "tp2": levels["tp2"],

            "last_checked_candle": candles[-1]["time"],
            "last_price": candles[-1]["close"],

            "current_pnl_pct": 0
        }

        state.setdefault(
            "ut_setups",
            {}
        )[setup_id] = setup

        existing_keys.add(
            signal_key
        )

        new_signals.append(
            ut_setup_message(
                name,
                direction,
                levels,
                setup_id
            )
        )

    if new_signals:
        save_state(state)

    return state, new_signals


# =========================
# MAIN SCANNER
# =========================

def run_scanner():

    total_start = time.time()

    print(
        "\n"
        "====================================\n"
        f"🚀 CRYPTO PUMP / DUMP SCANNER "
        f"v{VERSION}\n"
        "===================================="
    )

    # 1
    start = time.time()

    state = get_ut_state()

    print(
        f"State: "
        f"{time.time() - start:.2f}s"
    )

    # 2
    results = fetch_all_coins_fast()

    if not results:

        print(
            "❌ No coin data received."
        )

        return

    # 3
    top5 = create_watchlist(
        results
    )

    if not top5:

        print(
            "❌ No valid watchlist."
        )

        return

    # 4
    btc_data = results.get(
        "BTC"
    )

    if btc_data:

        btc_regime = btc_regime_from_candles(
            btc_data["candles"]
        )

    else:

        btc_regime = "UNKNOWN"

    # 5
    watchlist = watchlist_message(
        top5,
        btc_regime
    )

    print("\n" + watchlist)

    send_telegram_chunks(
        watchlist
    )

    # 6
    normal_count = 0

    for name, data, analysis in top5:

        candles = data["candles"]

        score = analysis["score"]

        if score >= 40:

            direction = "LONG"

        elif score <= -40:

            direction = "SHORT"

        else:

            continue

        if not two_candle_confirmation(
            candles,
            direction
        ):
            continue

        msg = normal_signal_message(
            name,
            candles,
            analysis,
            direction
        )

        print("\n" + msg)

        send_telegram_chunks(msg)

        normal_count += 1

    print(
        f"Normal signals: "
        f"{normal_count}"
    )

    # 7
    start = time.time()

    state = update_ut_setups(
        state,
        results
    )

    print(
        f"UT update: "
        f"{time.time() - start:.2f}s"
    )

    # 8
    state, new_ut_signals = process_ut_top5(
        state,
        top5
    )

    for msg in new_ut_signals:

        print("\n" + msg)

        send_telegram_chunks(msg)

    print(
        f"New UT signals: "
        f"{len(new_ut_signals)}"
    )

    # 9
    status = ut_live_status_message(
        state
    )

    print("\n" + status)

    send_telegram_chunks(
        status
    )

    total = time.time() - total_start

    print(
        "\n"
        "====================================\n"
        f"⏱️ TOTAL SCAN TIME: {total:.2f}s\n"
        "===================================="
    )


# =========================
# LOOP
# =========================

if __name__ == "__main__":

    while True:

        try:

            run_scanner()

        except Exception as e:

            print(
                "❌ MAIN ERROR:",
                repr(e)
            )

        now = time.time()

        next_boundary = (
            ((int(now) // 300) + 1) * 300
        )

        wait = (
            next_boundary
            - now
            + 5
        )

        print(
            f"\n⏳ Next scan in "
            f"{wait:.1f}s"
        )

        time.sleep(
            max(1, wait)
        )
