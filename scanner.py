import os
import json
import time
from datetime import datetime, timezone

import requests


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"

HISTORY_FILE = "price_data.json"
TRADE_FILE = "trade_state.json"

HISTORY_SECONDS = 8 * 60 * 60

# Risk / reward
SL_PERCENT = 1.20

TP1_R = 1.0
TP2_R = 2.0
TP3_R = 3.0

TRAIL_AFTER_TP1 = 0.50
TRAIL_AFTER_TP2 = 0.75

MAX_ACTIVE_TRADES = 5

COINS = [
    "bitcoin",
    "ethereum",
    "binancecoin",
    "solana",
    "ripple",
    "dogecoin",
    "cardano",
    "avalanche-2",
    "chainlink",
    "polkadot",
    "tron",
    "litecoin",
    "bitcoin-cash",
    "cosmos",
    "uniswap",
    "ethereum-classic",
    "stellar",
    "near",
    "aptos",
    "filecoin",
    "arbitrum",
    "optimism",
    "sui",
    "injective-protocol",
    "aave",
    "maker",
    "algorand",
    "vechain",
    "sei-network",
    "celestia",
]


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        return result.get("ok", False)

    except Exception as e:

        print("Telegram error:", e)

        return False


# =========================================================
# JSON HELPERS
# =========================================================

def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"{filename} read error:",
            e,
        )

        return default


def save_json(filename, data):

    try:

        temp = filename + ".tmp"

        with open(
            temp,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temp,
            filename,
        )

    except Exception as e:

        print(
            f"{filename} save error:",
            e,
        )


# =========================================================
# MARKET HISTORY
# =========================================================

def update_history(history, market_data):

    now = int(time.time())

    cutoff = now - HISTORY_SECONDS

    for coin in market_data:

        coin_id = coin.get("id")
        price = coin.get("current_price")
        volume = coin.get("total_volume")

        if not coin_id or price is None:
            continue

        if coin_id not in history:
            history[coin_id] = []

        history[coin_id].append(
            {
                "time": now,
                "price": float(price),
                "volume": float(volume or 0),
            }
        )

        history[coin_id] = [
            x
            for x in history[coin_id]
            if int(x.get("time", 0)) >= cutoff
        ]

        if len(history[coin_id]) > 150:
            history[coin_id] = history[coin_id][-150:]


# =========================================================
# PRICE CHANGE
# =========================================================

def get_change(items, minutes):

    if len(items) < 2:
        return None

    latest = items[-1]

    target_time = (
        int(latest["time"])
        - minutes * 60
    )

    candidates = [
        x
        for x in items[:-1]
        if int(x.get("time", 0))
        <= target_time
    ]

    if not candidates:
        return None

    previous = min(
        candidates,
        key=lambda x: abs(
            int(x["time"]) - target_time
        ),
    )

    old_price = float(
        previous["price"]
    )

    new_price = float(
        latest["price"]
    )

    if old_price <= 0:
        return None

    return (
        (new_price - old_price)
        / old_price
    ) * 100


# =========================================================
# RSI
# =========================================================

def calculate_rsi(items, period=14):

    prices = [
        float(x["price"])
        for x in items
    ]

    if len(prices) < period + 1:
        return None

    changes = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    recent = changes[-period:]

    gains = [
        x if x > 0 else 0
        for x in recent
    ]

    losses = [
        abs(x) if x < 0 else 0
        for x in recent
    ]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:

        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# =========================================================
# EMA
# =========================================================

def calculate_ema(items, period):

    prices = [
        float(x["price"])
        for x in items
    ]

    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(
        prices[:period]
    ) / period

    for price in prices[period:]:

        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# =========================================================
# VOLUME RATIO
# =========================================================

def calculate_volume_ratio(
    items,
    lookback=10,
):

    if len(items) < lookback + 1:
        return None

    current = float(
        items[-1].get("volume", 0)
    )

    previous = [
        float(x.get("volume", 0))
        for x in items[-lookback - 1:-1]
    ]

    previous = [
        x for x in previous
        if x > 0
    ]

    if not previous:
        return None

    average = (
        sum(previous)
        / len(previous)
    )

    if average <= 0:
        return None

    return current / average


# =========================================================
# BREAKOUT / BREAKDOWN
# =========================================================

def calculate_levels(items):

    if len(items) < 7:

        return {
            "breakout": False,
            "breakdown": False,
        }

    current = float(
        items[-1]["price"]
    )

    previous = [
        float(x["price"])
        for x in items[-7:-1]
    ]

    high = max(previous)
    low = min(previous)

    return {
        "breakout":
            current > high * 1.0005,

        "breakdown":
            current < low * 0.9995,
    }


# =========================================================
# ICHIMOKU
# =========================================================

def calculate_ichimoku(items):

    if len(items) < 52:

        return {
            "ready": False,
            "bullish": False,
            "bearish": False,
        }

    prices = [
        float(x["price"])
        for x in items
    ]

    current = prices[-1]

    tenkan_data = prices[-9:]
    kijun_data = prices[-26:]
    span_b_data = prices[-52:]

    tenkan = (
        max(tenkan_data)
        + min(tenkan_data)
    ) / 2

    kijun = (
        max(kijun_data)
        + min(kijun_data)
    ) / 2

    span_a = (
        tenkan + kijun
    ) / 2

    span_b = (
        max(span_b_data)
        + min(span_b_data)
    ) / 2

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    return {
        "ready": True,
        "bullish":
            current > cloud_top
            and tenkan > kijun,

        "bearish":
            current < cloud_bottom
            and tenkan < kijun,

        "tenkan": tenkan,
        "kijun": kijun,
    }


# =========================================================
# PUMP SCORE
# =========================================================

def pump_score(
    c5,
    c10,
    c15,
    rsi,
    ema5,
    ema10,
    volume,
    breakout,
    ichimoku,
    btc_1h,
):

    score = 0
    reasons = []

    if c5 is not None:

        if 0.5 <= c5 < 2.5:
            score += 15
            reasons.append("5m momentum")

        elif 2.5 <= c5 < 5:
            score += 10

        elif c5 > 0:
            score += 4

    if c10 is not None:

        if c10 >= 2:
            score += 10
            reasons.append("10m momentum")

        elif c10 > 0:
            score += 5

    if c15 is not None:

        if c15 >= 3:
            score += 10
            reasons.append("15m trend")

        elif c15 > 0:
            score += 5

    if (
        c5 is not None
        and c10 is not None
        and c5 > 0
        and c10 > 0
        and c5 > c10 / 2
    ):

        score += 10
        reasons.append("Acceleration")

    if rsi is not None:

        if 55 <= rsi <= 70:
            score += 10
            reasons.append("RSI healthy")

        elif 70 < rsi <= 78:
            score += 6

        elif rsi > 82:
            score -= 5

    if (
        ema5 is not None
        and ema10 is not None
        and ema5 > ema10
    ):

        score += 10
        reasons.append("EMA bullish")

    if volume is not None:

        if volume >= 3:
            score += 10
            reasons.append(
                f"Volume {volume:.1f}x"
            )

        elif volume >= 2:
            score += 8

        elif volume >= 1.5:
            score += 5

    if breakout:

        score += 15
        reasons.append("BREAKOUT")

    if ichimoku.get("bullish"):

        score += 15
        reasons.append("Ichimoku bullish")

    elif (
        ichimoku.get("tenkan")
        and ichimoku.get("kijun")
        and ichimoku["tenkan"]
        > ichimoku["kijun"]
    ):

        score += 6

    if btc_1h is not None:

        if btc_1h > 0:
            score += 5

        elif btc_1h < -1:
            score -= 5

    return max(
        0,
        min(100, score)
    ), reasons


# =========================================================
# DUMP SCORE
# =========================================================

def dump_score(
    c5,
    c10,
    c15,
    rsi,
    ema5,
    ema10,
    volume,
    breakdown,
    ichimoku,
    btc_1h,
):

    score = 0
    reasons = []

    if c5 is not None:

        if -2.5 < c5 <= -0.5:
            score += 15
            reasons.append("5m selling")

        elif -5 < c5 <= -2.5:
            score += 10

        elif c5 < 0:
            score += 4

    if c10 is not None:

        if c10 <= -2:
            score += 10
            reasons.append("10m selling")

        elif c10 < 0:
            score += 5

    if c15 is not None:

        if c15 <= -3:
            score += 10
            reasons.append("15m bearish")

        elif c15 < 0:
            score += 5

    if (
        c5 is not None
        and c10 is not None
        and c5 < 0
        and c10 < 0
        and c5 < c10 / 2
    ):

        score += 10
        reasons.append("Down acceleration")

    if rsi is not None:

        if 30 <= rsi <= 45:
            score += 10
            reasons.append("RSI weak")

        elif rsi < 25:
            score -= 5

    if (
        ema5 is not None
        and ema10 is not None
        and ema5 < ema10
    ):

        score += 10
        reasons.append("EMA bearish")

    if volume is not None:

        if volume >= 3:
            score += 10
            reasons.append(
                f"Volume {volume:.1f}x"
            )

        elif volume >= 2:
            score += 8

        elif volume >= 1.5:
            score += 5

    if breakdown:

        score += 15
        reasons.append("BREAKDOWN")

    if ichimoku.get("bearish"):

        score += 15
        reasons.append("Ichimoku bearish")

    elif (
        ichimoku.get("tenkan")
        and ichimoku.get("kijun")
        and ichimoku["tenkan"]
        < ichimoku["kijun"]
    ):

        score += 6

    if btc_1h is not None:

        if btc_1h < 0:
            score += 5

    return max(
        0,
        min(100, score)
    ), reasons


# =========================================================
# MARKET DATA
# =========================================================

def get_market_data():

    params = {
        "vs_currency": "usd",
        "ids": ",".join(COINS),
        "order": "market_cap_desc",
        "per_page": 30,
        "page": 1,
        "sparkline": "false",
    }

    headers = {
        "User-Agent":
            "early-pump-engine/4.0"
    }

    try:

        response = requests.get(
            COINGECKO_URL,
            params=params,
            headers=headers,
            timeout=30,
        )

        print(
            "CoinGecko HTTP:",
            response.status_code,
        )

        response.raise_for_status()

        data = response.json()

        return (
            data
            if isinstance(data, list)
            else []
        )

    except Exception as e:

        print(
            "CoinGecko error:",
            e,
        )

        return []


# =========================================================
# TRADE CALCULATIONS
# =========================================================

def create_trade(
    symbol,
    direction,
    score,
    entry,
):

    risk = entry * (
        SL_PERCENT / 100
    )

    if direction == "PUMP":

        sl = entry - risk

        tp1 = entry + (
            risk * TP1_R
        )

        tp2 = entry + (
            risk * TP2_R
        )

        tp3 = entry + (
            risk * TP3_R
        )

    else:

        sl = entry + risk

        tp1 = entry - (
            risk * TP1_R
        )

        tp2 = entry - (
            risk * TP2_R
        )

        tp3 = entry - (
            risk * TP3_R
        )

    now = datetime.now(
        timezone.utc
    )

    trade_id = (
        f"{symbol}-"
        f"{now.strftime('%Y%m%d-%H%M')}-"
        f"{direction}"
    )

    return {
        "id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "trailing_stop": None,
        "highest": entry,
        "lowest": entry,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "status": "ACTIVE",
        "opened_at":
            now.isoformat(),
        "closed_at": None,
        "exit_price": None,
        "exit_reason": None,
        "realized_percent": 0,
    }


# =========================================================
# TRADE PERCENT
# =========================================================

def trade_percent(trade, price):

    entry = trade["entry"]

    if trade["direction"] == "PUMP":

        return (
            (price - entry)
            / entry
        ) * 100

    return (
        (entry - price)
        / entry
    ) * 100


# =========================================================
# TRADE MANAGER
# =========================================================

def manage_trade(
    trade,
    price,
):

    if trade["status"] != "ACTIVE":
        return None

    direction = trade["direction"]

    # -----------------------------------------------------
    # PUMP
    # -----------------------------------------------------

    if direction == "PUMP":

        if price > trade["highest"]:
            trade["highest"] = price

        # STOP LOSS
        if price <= trade["sl"]:

            trade["status"] = "CLOSED"
            trade["closed_at"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            trade["exit_price"] = price
            trade["exit_reason"] = "STOP LOSS"
            trade["realized_percent"] = (
                trade_percent(
                    trade,
                    price
                )
            )

            return "SL"

        # TP1
        if (
            not trade["tp1_hit"]
            and price >= trade["tp1"]
        ):

            trade["tp1_hit"] = True

            # Move SL to near breakeven
            trade["sl"] = (
                trade["entry"]
                * 1.0005
            )

            send_telegram(
                f"🎯 <b>TP1 HIT</b>\n\n"
                f"{trade['symbol']}\n"
                f"📈 +{trade_percent(trade, price):.2f}%\n\n"
                f"🛡 SL → BE"
            )

            return "TP1"

        # TP2
        if (
            trade["tp1_hit"]
            and not trade["tp2_hit"]
            and price >= trade["tp2"]
        ):

            trade["tp2_hit"] = True

            trade["sl"] = (
                trade["entry"]
                + (
                    price
                    - trade["entry"]
                ) * 0.50
            )

            return "TP2"

        # TP3
        if (
            trade["tp2_hit"]
            and not trade["tp3_hit"]
            and price >= trade["tp3"]
        ):

            trade["tp3_hit"] = True

            trade["trailing_stop"] = (
                price
                * (
                    1
                    - TRAIL_AFTER_TP2
                    / 100
                )
            )

            return "TP3"

        # TRAILING
        if trade["tp3_hit"]:

            trailing = (
                trade["highest"]
                * (
                    1
                    - TRAIL_AFTER_TP2
                    / 100
                )
            )

            trade["trailing_stop"] = trailing

            if price <= trailing:

                trade["status"] = "CLOSED"
                trade["closed_at"] = (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                )

                trade["exit_price"] = price
                trade["exit_reason"] = (
                    "TRAILING STOP"
                )

                trade["realized_percent"] = (
                    trade_percent(
                        trade,
                        price
                    )
                )

                return "TRAIL"

    # -----------------------------------------------------
    # DUMP / SHORT
    # -----------------------------------------------------

    else:

        if price < trade["lowest"]:
            trade["lowest"] = price

        # STOP LOSS
        if price >= trade["sl"]:

            trade["status"] = "CLOSED"
            trade["closed_at"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            trade["exit_price"] = price
            trade["exit_reason"] = "STOP LOSS"
            trade["realized_percent"] = (
                trade_percent(
                    trade,
                    price
                )
            )

            return "SL"

        # TP1
        if (
            not trade["tp1_hit"]
            and price <= trade["tp1"]
        ):

            trade["tp1_hit"] = True

            trade["sl"] = (
                trade["entry"]
                * 0.9995
            )

            send_telegram(
                f"🎯 <b>TP1 HIT</b>\n\n"
                f"{trade['symbol']}\n"
                f"📉 +{trade_percent(trade, price):.2f}%\n\n"
                f"🛡 SL → BE"
            )

            return "TP1"

        # TP2
        if (
            trade["tp1_hit"]
            and not trade["tp2_hit"]
            and price <= trade["tp2"]
        ):

            trade["tp2_hit"] = True

            trade["sl"] = (
                trade["entry"]
                - (
                    trade["entry"]
                    - price
                ) * 0.50
            )

            return "TP2"

        # TP3
        if (
            trade["tp2_hit"]
            and not trade["tp3_hit"]
            and price <= trade["tp3"]
        ):

            trade["tp3_hit"] = True

            trade["trailing_stop"] = (
                price
                * (
                    1
                    + TRAIL_AFTER_TP2
                    / 100
                )
            )

            return "TP3"

        # TRAILING
        if trade["tp3_hit"]:

            trailing = (
                trade["lowest"]
                * (
                    1
                    + TRAIL_AFTER_TP2
                    / 100
                )
            )

            trade["trailing_stop"] = trailing

            if price >= trailing:

                trade["status"] = "CLOSED"
                trade["closed_at"] = (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                )

                trade["exit_price"] = price
                trade["exit_reason"] = (
                    "TRAILING STOP"
                )

                trade["realized_percent"] = (
                    trade_percent(
                        trade,
                        price
                    )
                )

                return "TRAIL"

    return None


# =========================================================
# NEW SIGNAL
# =========================================================

def create_signal_if_valid(
    result,
    active_trades,
):

    if len(active_trades) >= MAX_ACTIVE_TRADES:
        return None

    if result["score"] < 75:
        return None

    symbol = result["symbol"]
    direction = result["direction"]

    # Don't duplicate same coin/direction
    for trade in active_trades.values():

        if (
            trade["symbol"] == symbol
            and trade["direction"] == direction
            and trade["status"] == "ACTIVE"
        ):

            return None

    # Strong signal requirements
    if direction == "PUMP":

        if not (
            result["c5"] is not None
            and result["c10"] is not None
            and result["c15"] is not None
            and result["c5"] > 0
            and result["c10"] > 0
            and result["c15"] > 0
            and result["c5"] < 5
        ):

            return None

    else:

        if not (
            result["c5"] is not None
            and result["c10"] is not None
            and result["c15"] is not None
            and result["c5"] < 0
            and result["c10"] < 0
            and result["c15"] < 0
        ):

            return None

    trade = create_trade(
        symbol,
        direction,
        result["score"],
        result["price"],
    )

    return trade


# =========================================================
# FORMAT TRADE
# =========================================================

def trade_message(trade):

    direction = trade["direction"]

    if direction == "PUMP":

        title = "🔥 <b>EARLY PUMP SIGNAL</b>"

        entry_icon = "🟢"

    else:

        title = "🔴 <b>EARLY DUMP SIGNAL</b>"

        entry_icon = "🔴"

    return (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{entry_icon} "
        f"<b>{trade['symbol']}</b>\n\n"
        f"⭐ Score: "
        f"<b>{trade['score']}/100</b>\n\n"
        f"💰 Entry: "
        f"<b>{trade['entry']:.8g}</b>\n"
        f"🛑 SL: "
        f"<b>{trade['sl']:.8g}</b>\n\n"
        f"🎯 TP1: "
        f"<b>{trade['tp1']:.8g}</b>\n"
        f"🎯 TP2: "
        f"<b>{trade['tp2']:.8g}</b>\n"
        f"🎯 TP3: "
        f"<b>{trade['tp3']:.8g}</b>\n\n"
        f"📊 Risk: "
        f"{SL_PERCENT:.2f}%\n"
        f"📈 R:R: 1 : 2 : 3\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Trade Manager ACTIVE</b>"
    )


# =========================================================
# MANAGE ACTIVE TRADES
# =========================================================

def process_active_trades(
    trades,
    market_data,
):

    prices = {
        x["symbol"].upper():
            float(x["current_price"])
        for x in market_data
        if x.get("symbol")
        and x.get("current_price") is not None
    }

    changed = False

    for trade_id, trade in list(
        trades.items()
    ):

        if trade["status"] != "ACTIVE":
            continue

        symbol = trade["symbol"]

        if symbol not in prices:
            continue

        price = prices[symbol]

        event = manage_trade(
            trade,
            price,
        )

        if event:

            changed = True

            if event == "TP1":

                send_telegram(
                    f"🎯 <b>TP1 HIT</b>\n\n"
                    f"{symbol}\n"
                    f"Price: {price:.8g}\n"
                    f"Progress: "
                    f"+{trade_percent(trade, price):.2f}%"
                )

            elif event == "TP2":

                send_telegram(
                    f"🔥 <b>TP2 HIT</b>\n\n"
                    f"{symbol}\n"
                    f"Price: {price:.8g}\n"
                    f"Progress: "
                    f"+{trade_percent(trade, price):.2f}%\n"
                    f"🛡 Trailing activated"
                )

            elif event == "TP3":

                send_telegram(
                    f"🚀 <b>TP3 HIT</b>\n\n"
                    f"{symbol}\n"
                    f"Price: {price:.8g}\n"
                    f"🛡 Trailing Stop ACTIVE"
                )

            elif event in (
                "SL",
                "TRAIL",
            ):

                emoji = (
                    "🛑"
                    if event == "SL"
                    else "🚪"
                )

                send_telegram(
                    f"{emoji} <b>TRADE CLOSED</b>\n\n"
                    f"{symbol}\n"
                    f"Reason: "
                    f"{trade['exit_reason']}\n"
                    f"Exit: "
                    f"{price:.8g}\n"
                    f"Result: "
                    f"<b>{trade['realized_percent']:+.2f}%</b>"
                )

    return changed


# =========================================================
# MAIN
# =========================================================

def scan():

    print(
        "================================"
    )

    print(
        "EARLY PUMP/DUMP ENGINE v4"
    )

    print(
        "SIGNAL + TRADE MANAGER"
    )

    print(
        "================================"
    )

    market_data = get_market_data()

    if not market_data:

        send_telegram(
            "🔴 <b>SCANNER ERROR</b>\n\n"
            "CoinGecko data unavailable."
        )

        return

    # -----------------------------------------------------
    # Load state
    # -----------------------------------------------------

    history = load_json(
        HISTORY_FILE,
        {}
    )

    trades = load_json(
        TRADE_FILE,
        {}
    )

    # -----------------------------------------------------
    # Update prices
    # -----------------------------------------------------

    update_history(
        history,
        market_data
    )

    save_json(
        HISTORY_FILE,
        history
    )

    # -----------------------------------------------------
    # Manage existing trades FIRST
    # -----------------------------------------------------

    process_active_trades(
        trades,
        market_data
    )

    # -----------------------------------------------------
    # BTC regime
    # -----------------------------------------------------

    btc_history = history.get(
        "bitcoin",
        []
    )

    btc_1h = get_change(
        btc_history,
        60
    )

    results = []

    # -----------------------------------------------------
    # Calculate signals
    # -----------------------------------------------------

    for coin in market_data:

        coin_id = coin.get("id")

        symbol = coin.get(
            "symbol",
            ""
        ).upper()

        price = coin.get(
            "current_price"
        )

        if not coin_id or price is None:
            continue

        items = history.get(
            coin_id,
            []
        )

        if len(items) < 3:
            continue

        c5 = get_change(
            items,
            5
        )

        c10 = get_change(
            items,
            10
        )

        c15 = get_change(
            items,
            15
        )

        rsi = calculate_rsi(
            items
        )

        ema5 = calculate_ema(
            items,
            5
        )

        ema10 = calculate_ema(
            items,
            10
        )

        volume = calculate_volume_ratio(
            items
        )

        levels = calculate_levels(
            items
        )

        ichimoku = calculate_ichimoku(
            items
        )

        p_score, p_reasons = pump_score(
            c5,
            c10,
            c15,
            rsi,
            ema5,
            ema10,
            volume,
            levels["breakout"],
            ichimoku,
            btc_1h,
        )

        d_score, d_reasons = dump_score(
            c5,
            c10,
            c15,
            rsi,
            ema5,
            ema10,
            volume,
            levels["breakdown"],
            ichimoku,
            btc_1h,
        )

        if p_score >= d_score:

            direction = "PUMP"
            score = p_score
            reasons = p_reasons

        else:

            direction = "DUMP"
            score = d_score
            reasons = d_reasons

        results.append(
            {
                "symbol": symbol,
                "price": float(price),
                "direction": direction,
                "score": score,
                "pump_score": p_score,
                "dump_score": d_score,
                "c5": c5,
                "c10": c10,
                "c15": c15,
                "rsi": rsi,
                "volume": volume,
                "breakout":
                    levels["breakout"],
                "breakdown":
                    levels["breakdown"],
                "ichimoku": ichimoku,
                "reasons": reasons,
            }
        )

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # -----------------------------------------------------
    # Create new trades
    # -----------------------------------------------------

    new_signals = []

    for result in results:

        trade = create_signal_if_valid(
            result,
            trades
        )

        if trade:

            trades[
                trade["id"]
            ] = trade

            new_signals.append(
                trade
            )

            if len(trades) >= MAX_ACTIVE_TRADES:
                break

    # -----------------------------------------------------
    # Save trade state
    # -----------------------------------------------------

    save_json(
        TRADE_FILE,
        trades
    )

    # -----------------------------------------------------
    # Send new signals
    # -----------------------------------------------------

    for trade in new_signals:

        send_telegram(
            trade_message(trade)
        )

    # -----------------------------------------------------
    # Watchlist
    # -----------------------------------------------------

    watchlist = results[:5]

    lines = [
        "👀 <b>TOP 5 WATCHLIST</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for i, item in enumerate(
        watchlist,
        1
    ):

        emoji = (
            "🟢"
            if item["direction"] == "PUMP"
            else "🔴"
        )

        rsi_text = (
            "N/A"
            if item["rsi"] is None
            else f"{item['rsi']:.1f}"
        )

        volume_text = (
            "N/A"
            if item["volume"] is None
            else f"{item['volume']:.1f}x"
        )

        lines.append(
            f"{i}. {emoji} "
            f"<b>{item['symbol']}</b> "
            f"⭐ {item['score']}/100"
        )

        lines.append(
            f"5m "
            f"{item['c5']:+.2f}% "
            if item["c5"] is not None
            else "5m N/A"
        )

        lines.append(
            f"10m "
            f"{item['c10']:+.2f}% | "
            f"15m "
            f"{item['c15']:+.2f}%"
            if (
                item["c10"] is not None
                and item["c15"] is not None
            )
            else "10m/15m N/A"
        )

        lines.append(
            f"RSI {rsi_text} | "
            f"Vol {volume_text}"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    btc_text = (
        "N/A"
        if btc_1h is None
        else f"{btc_1h:+.2f}%"
    )

    lines.append(
        f"₿ BTC 1H: {btc_text}"
    )

    active = [
        x
        for x in trades.values()
        if x["status"] == "ACTIVE"
    ]

    lines.append(
        f"📌 Active Trades: "
        f"{len(active)}"
    )

    lines.append(
        f"📊 Scanned: "
        f"{len(market_data)}/30"
    )

    lines.append(
        "📡 Source: CoinGecko"
    )

    lines.append(
        "🤖 v4 Signal + Trade Manager"
    )

    message = "\n".join(lines)

    print(message)

    send_telegram(message)

    print(
        "Scanner finished."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    scan()
