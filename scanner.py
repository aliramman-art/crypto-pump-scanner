import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

DATA_FILE = "price_data.json"

API_URL = "https://api.coingecko.com/api/v3/coins/markets"

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
    "celestia"
]


# ==================================================
# TIME
# ==================================================

def now():
    return int(
        datetime.now(timezone.utc).timestamp()
    )


# ==================================================
# HISTORY
# ==================================================

def load_history():

    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)

    except Exception:
        return {}


def save_history(history):

    with open(DATA_FILE, "w") as f:
        json.dump(history, f)


# ==================================================
# MARKET DATA
# ==================================================

def get_market():

    response = requests.get(
        API_URL,
        params={
            "vs_currency": "usd",
            "ids": ",".join(COINS),
            "order": "market_cap_desc",
            "per_page": 30,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "1h,24h"
        },
        timeout=30
    )

    print(
        "MARKET STATUS:",
        response.status_code
    )

    response.raise_for_status()

    return response.json()


# ==================================================
# PERCENT CHANGE
# ==================================================

def pct(old, new):

    if old is None or old <= 0:
        return None

    return ((new - old) / old) * 100


# ==================================================
# HISTORICAL CHANGE
# ==================================================

def historical_change(
    records,
    minutes,
    current_price,
    current_time
):

    if not records:
        return None

    target = current_time - (
        minutes * 60
    )

    closest = None

    smallest_distance = None

    for item in records:

        distance = abs(
            item["time"] - target
        )

        if (
            smallest_distance is None
            or distance < smallest_distance
        ):

            smallest_distance = distance
            closest = item

    if closest is None:
        return None

    # اگر داده خیلی دور از زمان مورد نظر است
    if smallest_distance > 150:

        return None

    return pct(
        closest["price"],
        current_price
    )


# ==================================================
# RSI
# ==================================================

def calculate_rsi(
    prices,
    period=14
):

    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):

        change = (
            prices[i]
            - prices[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(change))

    avg_gain = (
        sum(gains[-period:])
        / period
    )

    avg_loss = (
        sum(losses[-period:])
        / period
    )

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# ==================================================
# EMA
# ==================================================

def calculate_ema(
    prices,
    period
):

    if len(prices) < period:
        return None

    multiplier = 2 / (
        period + 1
    )

    result = (
        sum(prices[:period])
        / period
    )

    for price in prices[period:]:

        result = (
            (price - result)
            * multiplier
        ) + result

    return result


# ==================================================
# BREAKOUT DISTANCE
# ==================================================

def breakout_distance(prices):

    if len(prices) < 10:
        return None

    previous = prices[-10:-1]

    resistance = max(previous)

    current = prices[-1]

    if resistance <= 0:
        return None

    distance = (
        (resistance - current)
        / resistance
    ) * 100

    return distance


# ==================================================
# EARLY PUMP SCORE
# ==================================================

def calculate_early_score(
    change5,
    change10,
    change15,
    rsi,
    ema5,
    ema10,
    price,
    resistance_distance,
    market_change_1h
):

    score = 0

    reasons = []

    # ----------------------------------------------
    # 5M MOMENTUM
    # ----------------------------------------------

    if change5 is not None:

        if 0.30 <= change5 < 0.80:

            score += 15
            reasons.append(
                "5m momentum"
            )

        elif 0.80 <= change5 < 1.50:

            score += 12
            reasons.append(
                "5m momentum"
            )

        elif change5 >= 1.50:

            score += 5
            reasons.append(
                "5m already moving"
            )

    # ----------------------------------------------
    # 10M MOMENTUM
    # ----------------------------------------------

    if change10 is not None:

        if 0.60 <= change10 < 1.50:

            score += 10

        elif 1.50 <= change10 < 3:

            score += 8

        elif change10 >= 3:

            score += 3

    # ----------------------------------------------
    # 15M TREND
    # ----------------------------------------------

    if change15 is not None:

        if 0.80 <= change15 < 2:

            score += 12
            reasons.append(
                "15m confirmation"
            )

        elif 2 <= change15 < 4:

            score += 8

        elif change15 >= 4:

            score += 3

    # ----------------------------------------------
    # ACCELERATION
    # ----------------------------------------------

    if (
        change5 is not None
        and change10 is not None
        and change10 > 0
    ):

        if change5 > (
            change10 / 3
        ):

            score += 12
            reasons.append(
                "acceleration"
            )

    # ----------------------------------------------
    # RSI
    # ----------------------------------------------

    if rsi is not None:

        if 52 <= rsi <= 65:

            score += 15
            reasons.append(
                "healthy RSI"
            )

        elif 65 < rsi <= 72:

            score += 10

        elif 72 < rsi <= 78:

            score += 4

        elif rsi > 82:

            score -= 10
            reasons.append(
                "RSI overheated"
            )

    # ----------------------------------------------
    # EMA
    # ----------------------------------------------

    if (
        ema5 is not None
        and ema10 is not None
    ):

        if price > ema5 > ema10:

            score += 15
            reasons.append(
                "EMA bullish"
            )

        elif price > ema5:

            score += 7

    # ----------------------------------------------
    # RESISTANCE
    # ----------------------------------------------

    if resistance_distance is not None:

        if 0 <= resistance_distance <= 0.50:

            score += 10
            reasons.append(
                "near breakout"
            )

        elif 0.50 < resistance_distance <= 1.0:

            score += 7

        elif 1 < resistance_distance <= 2:

            score += 3

    # ----------------------------------------------
    # MARKET TREND
    # ----------------------------------------------

    if market_change_1h is not None:

        if market_change_1h > 0.50:

            score += 6

        elif market_change_1h < -1.0:

            score -= 10

    return max(
        0,
        min(
            100,
            score
        )
    ), reasons


# ==================================================
# ANALYZE COIN
# ==================================================

def analyze_coin(
    coin,
    history,
    current_time,
    btc_change_1h
):

    coin_id = coin["id"]

    symbol = coin["symbol"].upper()

    price = coin.get(
        "current_price"
    )

    if not price:
        return None

    records = history.get(
        coin_id,
        []
    )

    records.append({
        "time": current_time,
        "price": price
    })

    # نگهداری دو ساعت تاریخچه

    cutoff = (
        current_time
        - 2 * 60 * 60
    )

    records = [
        x for x in records
        if x["time"] >= cutoff
    ]

    history[coin_id] = records

    # ----------------------------------------------
    # CHANGES
    # ----------------------------------------------

    change5 = historical_change(
        records,
        5,
        price,
        current_time
    )

    change10 = historical_change(
        records,
        10,
        price,
        current_time
    )

    change15 = historical_change(
        records,
        15,
        price,
        current_time
    )

    # ----------------------------------------------
    # PRICES
    # ----------------------------------------------

    prices = [
        x["price"]
        for x in records
    ]

    # ----------------------------------------------
    # RSI
    # ----------------------------------------------

    rsi = calculate_rsi(
        prices
    )

    # ----------------------------------------------
    # EMA
    # ----------------------------------------------

    ema5 = calculate_ema(
        prices,
        5
    )

    ema10 = calculate_ema(
        prices,
        10
    )

    # ----------------------------------------------
    # RESISTANCE
    # ----------------------------------------------

    resistance_distance = (
        breakout_distance(prices)
    )

    # ----------------------------------------------
    # SCORE
    # ----------------------------------------------

    score, reasons = (
        calculate_early_score(
            change5,
            change10,
            change15,
            rsi,
            ema5,
            ema10,
            price,
            resistance_distance,
            btc_change_1h
        )
    )

    return {

        "symbol": symbol,

        "price": price,

        "change5": change5,

        "change10": change10,

        "change15": change15,

        "rsi": rsi,

        "ema5": ema5,

        "ema10": ema10,

        "resistance_distance":
            resistance_distance,

        "score": score,

        "reasons": reasons,

        "samples": len(records)
    }


# ==================================================
# TELEGRAM
# ==================================================

def send_telegram(message):

    response = requests.post(

        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage",

        data={
            "chat_id": CHAT_ID,
            "text": message
        },

        timeout=20
    )

    print(
        "TELEGRAM:",
        response.text
    )

    response.raise_for_status()


# ==================================================
# MAIN
# ==================================================

def main():

    current_time = now()

    history = load_history()

    market = get_market()

    # ----------------------------------------------
    # BTC FILTER
    # ----------------------------------------------

    btc = next(
        (
            x for x in market
            if x["id"] == "bitcoin"
        ),
        None
    )

    btc_change_1h = None

    if btc:

        btc_change_1h = btc.get(
            "price_change_percentage_1h_in_currency"
        )

    results = []

    failed = []

    for coin in market:

        try:

            result = analyze_coin(
                coin,
                history,
                current_time,
                btc_change_1h
            )

            if result:

                results.append(
                    result
                )

        except Exception as e:

            print(
                coin["id"],
                "ERROR:",
                e
            )

            failed.append(
                coin["id"]
            )

    save_history(history)

    if not results:

        raise Exception(
            "No valid market data"
        )

    # ----------------------------------------------
    # SORT
    # ----------------------------------------------

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # ----------------------------------------------
    # MESSAGE
    # ----------------------------------------------

    message = (
        "🚨 EARLY PUMP SCANNER\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 شکار پیش از پامپ\n\n"
    )

    # ----------------------------------------------
    # STRONG EARLY
    # ----------------------------------------------

    strong = [

        x for x in results

        if (
            x["score"] >= 75

            and x["change5"] is not None

            and 0 < x["change5"] < 2.0

            and len(x["reasons"]) >= 3
        )
    ]

    if strong:

        message += (
            "🔴 STRONG EARLY PUMP\n\n"
        )

        for x in strong[:5]:

            rsi = (
                f"{x['rsi']:.0f}"
                if x["rsi"] is not None
                else "N/A"
            )

            distance = (

                f"{x['resistance_distance']:.2f}%"

                if x[
                    "resistance_distance"
                ] is not None

                else "N/A"
            )

            message += (

                f"🚨 {x['symbol']}\n"

                f"⭐ Early Score: "
                f"{x['score']}/100\n"

                f"5m: "
                f"{x['change5']:+.2f}%\n"

                f"10m: "
                f"{x['change10']:+.2f}%\n"

                f"15m: "
                f"{x['change15']:+.2f}%\n"

                f"RSI: {rsi}\n"

                f"Resistance: "
                f"{distance}\n"

                f"EMA: "
                f"{'✅' if x['ema5'] and x['ema10'] and x['price'] > x['ema5'] > x['ema10'] else '❌'}\n"

                f"BTC 1H: "
                f"{btc_change_1h:+.2f}%"
                if btc_change_1h is not None
                else
                f"BTC 1H: N/A"

            )

            message += "\n\n"

    else:

        message += (
            "🟢 فعلاً Strong Early Pump نداریم.\n\n"
        )

    # ----------------------------------------------
    # TOP WATCHLIST
    # ----------------------------------------------

    message += (
        "👀 EARLY WATCHLIST\n\n"
    )

    for i, x in enumerate(
        results[:5],
        1
    ):

        c5 = (

            f"{x['change5']:+.2f}%"

            if x["change5"] is not None

            else "N/A"
        )

        c15 = (

            f"{x['change15']:+.2f}%"

            if x["change15"] is not None

            else "N/A"
        )

        rsi = (

            f"{x['rsi']:.0f}"

            if x["rsi"] is not None

            else "N/A"
        )

        message += (

            f"{i}. "
            f"{x['symbol']} "
            f"⭐ {x['score']}/100\n"

            f"   5m {c5} | "
            f"15m {c15} | "
            f"RSI {rsi}\n"
        )

    # ----------------------------------------------
    # SYSTEM
    # ----------------------------------------------

    message += (

        "\n━━━━━━━━━━━━━━━━━━\n"

        f"📊 Scanned: "
        f"{len(results)}/30\n"

        f"❌ Failed: "
        f"{len(failed)}\n"

        f"₿ BTC 1H: "

    )

    if btc_change_1h is not None:

        message += (
            f"{btc_change_1h:+.2f}%\n"
        )

    else:

        message += "N/A\n"

    message += (
        "⏱ Timeframe: 5m\n"
        "📡 Source: CoinGecko\n"
    )

    # ----------------------------------------------
    # SEND
    # ----------------------------------------------

    send_telegram(
        message
    )


if __name__ == "__main__":

    main()
