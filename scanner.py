# ============================================================
# CRYPTO PRICE ACTION SCANNER v1.0
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
# - Structural SL
# - TP / RR
# - Score 0-100
#
# Telegram:
# - Persian/Jalali date
# - Iran time
# - Persian numbers
# - Performance
# - Pending
# - Open
# - Closed
#
# GitHub Actions:
# scanner.py runs once per workflow
# workflow runs every 5 minutes
# ============================================================

import os
import json
import time
import math
import requests
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta


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

STATE_FILE = "ut_bot_state.json"
HISTORY_FILE = "ut_bot_trade_history.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 20

# جلوگیری از بررسی دوباره یک کندل
SIGNAL_COOLDOWN_CANDLES = 3


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

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"JSON LOAD ERROR {path}: {e}")
        return default


def save_json(path, data):
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp, path)


# ============================================================
# STATE
# ============================================================

state = load_json(
    STATE_FILE,
    {
        "open": {},
        "pending": {},
        "last_signals": {}
    }
)

history = load_json(
    HISTORY_FILE,
    {
        "trades": []
    }
)


# ============================================================
# PERSIAN NUMBERS
# ============================================================

def fa_num(value):
    if value is None:
        return ""

    text = str(value)

    table = str.maketrans(
        "0123456789.-+%",
        "۰۱۲۳۴۵۶۷۸۹٫−+٪"
    )

    return text.translate(table)


def fmt_price(price):
    if price is None:
        return "-"

    if price >= 1000:
        s = f"{price:.2f}"
    elif price >= 1:
        s = f"{price:.4f}"
    elif price >= 0.01:
        s = f"{price:.5f}"
    else:
        s = f"{price:.8f}"

    return fa_num(s)


def fmt_pct(value):
    return fa_num(f"{value:+.2f}%")


# ============================================================
# JALALI DATE
# ============================================================

def gregorian_to_jalali(gy, gm, gd):
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

    if gm2 > 1 and (
        gy % 4 == 0 and
        (gy % 100 != 0 or gy % 400 == 0)
    ):
        g_day_no += 1

    g_day_no += gd2

    j_day_no = g_day_no - 79

    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)

    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    jm = 0

    while jm < 11 and j_day_no >= j_days_in_month[jm]:
        j_day_no -= j_days_in_month[jm]
        jm += 1

    jm += 1
    jd = j_day_no + 1

    return jy, jm, jd


def iran_now():
    iran_tz = timezone(timedelta(hours=3, minutes=30))
    return datetime.now(timezone.utc).astimezone(iran_tz)


def jalali_datetime_string():
    now = iran_now()

    jy, jm, jd = gregorian_to_jalali(
        now.year,
        now.month,
        now.day
    )

    return fa_num(
        f"{jy:04d}/{jm:02d}/{jd:02d} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured.")
        print(text)
        return False

    url = (
        f"https://api.telegram.org/bot"
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

        print("Telegram:", response.status_code)

        if not response.ok:
            print(response.text)

        return response.ok

    except Exception as e:
        print("Telegram ERROR:", e)
        return False


# ============================================================
# SYMBOL DISCOVERY
# ============================================================

def get_top_symbols():
    print("Loading Kraken Futures markets...")

    markets = exchange.load_markets()

    candidates = []

    for symbol, market in markets.items():

        try:
            if not market.get("active", True):
                continue

            if market.get("type") != "swap":
                continue

            quote = market.get("quote", "")

            if quote not in ["USD", "USDT"]:
                continue

            base = market.get("base", "")

            if not base:
                continue

            candidates.append(symbol)

        except Exception:
            continue

    print(f"Candidate markets: {len(candidates)}")

    tickers = {}

    try:
        tickers = exchange.fetch_tickers(candidates)
    except Exception as e:
        print("fetch_tickers failed:", e)

        # fallback
        for symbol in candidates:
            try:
                tickers[symbol] = exchange.fetch_ticker(symbol)
                time.sleep(0.05)
            except Exception:
                pass

    ranked = []

    for symbol in candidates:

        ticker = tickers.get(symbol)

        if not ticker:
            continue

        quote_volume = ticker.get("quoteVolume")

        if quote_volume is None:
            base_volume = ticker.get("baseVolume")
            last = ticker.get("last")

            if base_volume and last:
                quote_volume = base_volume * last

        if not quote_volume:
            continue

        try:
            quote_volume = float(quote_volume)
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
        for symbol, volume in ranked[:TOP_N]
    ]

    print("TOP symbols:")

    for i, symbol in enumerate(result, 1):
        print(i, symbol)

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

        if not data or len(data) < 30:
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

        df.dropna(inplace=True)

        # آخرین کندل ممکن است هنوز باز باشد
        # بنابراین آخرین کندل حذف می‌شود.
        df = df.iloc[:-1].copy()

        if len(df) < 30:
            return None

        return df

    except Exception as e:
        print(f"OHLCV ERROR {symbol}: {e}")
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

    for i in range(start, end):

        left_high = max(
            h[i-SWING_LEFT:i]
        )

        right_high = max(
            h[i+1:i+1+SWING_RIGHT]
        )

        if h[i] > left_high and h[i] >= right_high:
            highs.append(i)

        left_low = min(
            l[i-SWING_LEFT:i]
        )

        right_low = min(
            l[i+1:i+1+SWING_RIGHT]
        )

        if l[i] < left_low and l[i] <= right_low:
            lows.append(i)

    return highs, lows


# ============================================================
# MARKET STRUCTURE
# ============================================================

def market_structure(df):
    swing_highs, swing_lows = find_swings(df)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None

    h1 = swing_highs[-1]
    h0 = swing_highs[-2]

    l1 = swing_lows[-1]
    l0 = swing_lows[-2]

    last_close = float(df["close"].iloc[-1])

    last_high = float(df["high"].iloc[-1])
    last_low = float(df["low"].iloc[-1])

    prev_close = float(df["close"].iloc[-2])

    structure = "RANGE"

    if (
        df["high"].iloc[h1] > df["high"].iloc[h0]
        and
        df["low"].iloc[l1] > df["low"].iloc[l0]
    ):
        structure = "BULLISH"

    elif (
        df["high"].iloc[h1] < df["high"].iloc[h0]
        and
        df["low"].iloc[l1] < df["low"].iloc[l0]
    ):
        structure = "BEARISH"

    # آخرین نقاط ساختاری
    resistance = float(df["high"].iloc[h1])
    support = float(df["low"].iloc[l1])

    # BOS
    bos = None

    if prev_close <= resistance and last_close > resistance:
        bos = "BULLISH"

    elif prev_close >= support and last_close < support:
        bos = "BEARISH"

    # CHOCH
    choch = None

    if structure == "BEARISH" and last_close > resistance:
        choch = "BULLISH"

    elif structure == "BULLISH" and last_close < support:
        choch = "BEARISH"

    return {
        "structure": structure,
        "bos": bos,
        "choch": choch,
        "resistance": resistance,
        "support": support,
        "swing_high_index": h1,
        "swing_low_index": l1,
        "swing_high": float(df["high"].iloc[h1]),
        "swing_low": float(df["low"].iloc[l1]),
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

    body = abs(c["close"] - c["open"])

    candle_range = c["high"] - c["low"]

    if candle_range <= 0:
        return {
            "bullish": False,
            "bearish": False,
            "strength": 0,
            "name": "NONE"
        }

    upper_wick = c["high"] - max(
        c["open"],
        c["close"]
    )

    lower_wick = min(
        c["open"],
        c["close"]
    ) - c["low"]

    body_ratio = body / candle_range

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

    # Strong bullish body
    elif (
        c["close"] > c["open"]
        and
        body_ratio >= 0.65
    ):
        bullish = True
        strength = 12
        name = "STRONG BULLISH"

    # Strong bearish body
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

def detect_pullback(df, structure):

    if not structure:
        return {
            "bullish": False,
            "bearish": False,
            "distance": 999
        }

    close = float(df["close"].iloc[-1])

    support = structure["support"]
    resistance = structure["resistance"]

    candle_range = (
        float(df["high"].iloc[-1])
        -
        float(df["low"].iloc[-1])
    )

    if candle_range <= 0:
        candle_range = close * 0.001

    tolerance = candle_range * 2.5

    bullish_pullback = (
        structure["structure"] == "BULLISH"
        and
        abs(close - support) <= tolerance
    )

    bearish_pullback = (
        structure["structure"] == "BEARISH"
        and
        abs(close - resistance) <= tolerance
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

    recent = float(df["volume"].iloc[-1])

    avg = float(
        df["volume"].iloc[-21:-1].mean()
    )

    if avg <= 0:
        return 0, 0

    ratio = recent / avg

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

def analyze_symbol(symbol, df):

    structure = market_structure(df)

    if not structure:
        return None

    candle = candle_confirmation(df)

    pullback = detect_pullback(
        df,
        structure
    )

    vol_points, vol_ratio = volume_score(df)

    score_buy = 0
    score_sell = 0

    reasons_buy = []
    reasons_sell = []

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if structure["structure"] == "BULLISH":
        score_buy += 25
        reasons_buy.append("Bullish Structure")

    elif structure["structure"] == "BEARISH":
        score_sell += 25
        reasons_sell.append("Bearish Structure")

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    if structure["bos"] == "BULLISH":
        score_buy += 20
        reasons_buy.append("BOS")

    elif structure["bos"] == "BEARISH":
        score_sell += 20
        reasons_sell.append("BOS")

    # --------------------------------------------------------
    # CHOCH
    # --------------------------------------------------------

    if structure["choch"] == "BULLISH":
        score_buy += 15
        reasons_buy.append("CHOCH")

    elif structure["choch"] == "BEARISH":
        score_sell += 15
        reasons_sell.append("CHOCH")

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    if pullback["bullish"]:
        score_buy += 20
        reasons_buy.append("Pullback")

    if pullback["bearish"]:
        score_sell += 20
        reasons_sell.append("Pullback")

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    if candle["bullish"]:
        score_buy += candle["strength"]
        reasons_buy.append(candle["name"])

    if candle["bearish"]:
        score_sell += candle["strength"]
        reasons_sell.append(candle["name"])

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    score_buy += vol_points
    score_sell += vol_points

    if vol_points:
        reasons_buy.append(
            f"Volume x{vol_ratio:.1f}"
        )

        reasons_sell.append(
            f"Volume x{vol_ratio:.1f}"
        )

    # --------------------------------------------------------
    # LIMIT
    # --------------------------------------------------------

    score_buy = min(score_buy, 100)
    score_sell = min(score_sell, 100)

    if score_buy >= score_sell:
        side = "BUY"
        score = score_buy
        reasons = reasons_buy

    else:
        side = "SELL"
        score = score_sell
        reasons = reasons_sell

    entry = float(df["close"].iloc[-1])

    # --------------------------------------------------------
    # STRUCTURAL SL
    # --------------------------------------------------------

    if side == "BUY":

        sl = structure["swing_low"]

        if sl >= entry:
            return None

        risk = entry - sl
        tp = entry + risk * RR

    else:

        sl = structure["swing_high"]

        if sl <= entry:
            return None

        risk = sl - entry
        tp = entry - risk * RR

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
# GET PERFORMANCE
# ============================================================

def performance():

    trades = history.get("trades", [])

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
        float(x.get("pnl_pct", 0))
        for x in trades
    )

    r_total = sum(
        float(x.get("r", 0))
        for x in trades
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
        "wr": wr
    }


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(results):

    if not state.get("open"):
        return

    remaining = {}

    for trade_id, trade in state["open"].items():

        symbol = trade["symbol"]

        result = results.get(symbol)

        if not result:
            remaining[trade_id] = trade
            continue

        price = result["structure"]["last_close"]

        side = trade["side"]

        entry = trade["entry"]
        sl = trade["sl"]
        tp = trade["tp"]

        outcome = None
        pnl_pct = 0
        r = 0

        if side == "BUY":

            if price <= sl:
                outcome = "LOSS"
                pnl_pct = (
                    (sl - entry)
                    / entry
                ) * 100

                r = -1

            elif price >= tp:
                outcome = "WIN"
                pnl_pct = (
                    (tp - entry)
                    / entry
                ) * 100

                r = RR

        else:

            if price >= sl:
                outcome = "LOSS"
                pnl_pct = (
                    (entry - sl)
                    / entry
                ) * 100

                r = -1

            elif price <= tp:
                outcome = "WIN"
                pnl_pct = (
                    (entry - tp)
                    / entry
                ) * 100

                r = RR

        if outcome:

            closed = dict(trade)

            closed["result"] = outcome
            closed["exit"] = price
            closed["pnl_pct"] = pnl_pct
            closed["r"] = r
            closed["closed_at"] = datetime.now(
                timezone.utc
            ).isoformat()

            history["trades"].append(closed)

            print(
                f"CLOSED {symbol} "
                f"{side} {outcome}"
            )

        else:
            remaining[trade_id] = trade

    state["open"] = remaining


# ============================================================
# CREATE SIGNAL
# ============================================================

def create_signal(result):

    symbol = result["symbol"]
    side = result["side"]

    candle_time = (
        result["structure"]
        .get("last_candle_time", "")
    )

    key = f"{symbol}:{side}"

    previous = state["last_signals"].get(key)

    if previous:

        try:
            previous_ts = pd.Timestamp(previous)

            current_ts = pd.Timestamp(candle_time)

            diff = (
                current_ts - previous_ts
            ).total_seconds()

            if diff < (
                SIGNAL_COOLDOWN_CANDLES
                * 5
                * 60
            ):
                return False

        except Exception:
            pass

    trade_id = (
        f"{symbol}_{side}_"
        f"{int(time.time())}"
    )

    trade = {
        "id": trade_id,
        "symbol": symbol,
        "side": side,
        "entry": result["entry"],
        "sl": result["sl"],
        "tp": result["tp"],
        "score": result["score"],
        "opened_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    state["open"][trade_id] = trade

    state["last_signals"][key] = candle_time

    return True


# ============================================================
# TELEGRAM FORMAT
# ============================================================

def clean_symbol(symbol):

    return (
        symbol
        .replace(":USD", "")
        .replace(":USDT", "")
        .replace("/USD", "/USDT")
    )


def build_report(
    results,
    events,
    elapsed
):

    perf = performance()

    now = jalali_datetime_string()

    lines = []

    lines.append(
        "📡 <b>CRYPTO PRICE ACTION REPORT</b>"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        f"⏱ <b>۵m CLOSED | TOP {fa_num(TOP_N)}</b>"
    )

    lines.append(
        f"🤖 <b>PRICE ACTION | RR ۱:۱</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("📊 <b>PERFORMANCE</b>")

    lines.append(
        f"Trades {fa_num(perf['total'])} | "
        f"🟢 {fa_num(perf['wins'])} | "
        f"🔴 {fa_num(perf['losses'])} | "
        f"⚪ {fa_num(perf['neutral'])}"
    )

    lines.append(
        f"🏆 WR {fa_num(f'{perf['wr']:.1f}%')} | "
        f"P&L {fmt_pct(perf['pnl'])} | "
        f"R {fmt_pct(perf['r'])}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"⚡ <b>THIS RUN: {fa_num(len(events))} EVENTS</b>"
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

            lines.append("")

            lines.append(
                f"{emoji} <b>{symbol}</b>"
            )

            lines.append(
                f"{emoji} {event['side']}"
            )

            lines.append(
                f"💰 Entry: {fmt_price(event['entry'])}"
            )

            lines.append(
                f"🛑 SL: {fmt_price(event['sl'])}"
            )

            lines.append(
                f"🎯 TP: {fmt_price(event['tp'])}"
            )

            lines.append(
                f"📊 Score: "
                f"{fa_num(event['score'])}/۱۰۰"
            )

            lines.append(
                "🏗 "
                +
                " + ".join(
                    event["reasons"][:4]
                )
            )

    else:

        lines.append("⚪ None")

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    pending = state.get("pending", {})

    lines.append(
        f"⏳ <b>PENDING: {fa_num(len(pending))}</b>"
    )

    if pending:

        for p in pending.values():

            emoji = (
                "🟢"
                if p["side"] == "BUY"
                else "🔴"
            )

            lines.append(
                f"{emoji} "
                f"{clean_symbol(p['symbol'])} | "
                f"{p['side']} | "
                f"{fa_num(p['score'])}/۱۰۰"
            )

    else:

        lines.append("⚪ None")

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    opens = state.get("open", {})

    lines.append(
        f"📂 <b>OPEN: {fa_num(len(opens))}</b>"
    )

    if opens:

        for trade in opens.values():

            emoji = (
                "🟢"
                if trade["side"] == "BUY"
                else "🔴"
            )

            lines.append(
                f"{emoji} "
                f"{clean_symbol(trade['symbol'])} | "
                f"{trade['side']} | "
                f"E {fmt_price(trade['entry'])} | "
                f"TP {fmt_price(trade['tp'])}"
            )

    else:

        lines.append("⚪ None")

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"⚙️ Scan: {fa_num(f'{elapsed:.1f}')}s"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    print("=" * 60)
    print("CRYPTO PRICE ACTION SCANNER")
    print("TIMEFRAME:", TIMEFRAME)
    print("TOP:", TOP_N)
    print("=" * 60)

    symbols = get_top_symbols()

    if not symbols:

        send_telegram(
            "⚠️ <b>PRICE ACTION SCANNER</b>\n\n"
            "دریافت لیست ارزها از Kraken Futures ناموفق بود."
        )

        return

    results = {}

    candidates = []

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    for index, symbol in enumerate(symbols, 1):

        print(
            f"[{index}/{len(symbols)}] "
            f"{symbol}"
        )

        df = fetch_ohlcv(symbol)

        if df is None:
            continue

        # زمان آخرین کندل بسته‌شده
        candle_time = df["timestamp"].iloc[-1]

        analysis = analyze_symbol(
            symbol,
            df
        )

        if not analysis:
            continue

        analysis["structure"][
            "last_candle_time"
        ] = candle_time.isoformat()

        results[symbol] = analysis

        candidates.append(
            analysis
        )

    # --------------------------------------------------------
    # UPDATE OPEN
    # --------------------------------------------------------

    update_open_trades(
        results
    )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # --------------------------------------------------------
    # BEST CANDIDATE
    # --------------------------------------------------------

    events = []

    if candidates:

        best = candidates[0]

        print(
            "BEST:",
            best["symbol"],
            best["side"],
            best["score"]
        )

        # فقط بهترین سیگنال هر اجرا
        # در صورتی که به حداقل امتیاز برسد
        if best["score"] >= MIN_SCORE:

            created = create_signal(
                best
            )

            if created:
                events.append(best)

        else:

            # اگر امتیاز پایین باشد،
            # سیگنال باز نمی‌کنیم،
            # ولی بهترین ارز را به صورت WATCH
            # در Pending قرار می‌دهیم.
            state["pending"] = {
                best["symbol"]: {
                    "symbol": best["symbol"],
                    "side": best["side"],
                    "score": best["score"],
                    "entry": best["entry"],
                    "sl": best["sl"],
                    "tp": best["tp"],
                    "reasons": best["reasons"]
                }
            }

    else:

        state["pending"] = {}

    # --------------------------------------------------------
    # CLEAN PENDING IF EVENT CREATED
    # --------------------------------------------------------

    if events:

        state["pending"] = {}

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

    elapsed = time.time() - start

    report = build_report(
        results,
        events,
        elapsed
    )

    print("")
    print("=" * 60)
    print(report)
    print("=" * 60)

    send_telegram(report)


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
