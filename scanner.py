import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

DATA_FILE = "price_data.json"

API_URL = "https://api.coingecko.com/api/v3/coins/markets"

COINS = [
    "bitcoin","ethereum","binancecoin","solana","ripple",
    "dogecoin","cardano","avalanche-2","chainlink","polkadot",
    "tron","litecoin","bitcoin-cash","cosmos","uniswap",
    "ethereum-classic","stellar","near","aptos","filecoin",
    "arbitrum","optimism","sui","injective-protocol","aave",
    "maker","algorand","vechain","sei-network","celestia"
]


def now():
    return int(datetime.now(timezone.utc).timestamp())


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

    print("MARKET:", response.status_code)

    response.raise_for_status()

    return response.json()


def pct(old, new):

    if old is None or old <= 0:
        return None

    return ((new - old) / old) * 100


def get_change(records, minutes, price, current_time):

    if len(records) < 2:
        return None

    target = current_time - minutes * 60

    candidate = None

    for item in records:

        if item["time"] <= target:
            candidate = item
        else:
            break

    if candidate is None:
        return None

    return pct(
        candidate["price"],
        price
    )


def rsi(prices, period=14):

    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):

        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    gain = sum(gains[-period:]) / period
    loss = sum(losses[-period:]) / period

    if loss == 0:
        return 100

    rs = gain / loss

    return 100 - (100 / (1 + rs))


def ema(prices, period):

    if len(prices) < period:
        return None

    result = sum(prices[:period]) / period

    multiplier = 2 / (period + 1)

    for price in prices[period:]:

        result = (
            (price - result) * multiplier
        ) + result

    return result


def volume_ratio(records):

    if len(records) < 6:
        return None

    current_volume = records[-1]["volume"]

    previous = [
        x["volume"]
        for x in records[-6:-1]
        if x["volume"] > 0
    ]

    if not previous:
        return None

    average = sum(previous) / len(previous)

    if average <= 0:
        return None

    return current_volume / average


def calculate_score(
    c5,
    c10,
    c15,
    rsi_value,
    ema5,
    ema10,
    price,
    vol_ratio,
    market_1h
):

    score = 0
    reasons = []

    # --------------------------------
    # 5m momentum
    # --------------------------------

    if c5 is not None:

        if 0.30 <= c5 < 0.80:
            score += 15
            reasons.append("5m momentum")

        elif 0.80 <= c5 < 1.50:
            score += 12

        elif c5 >= 1.50:
            score += 4

    # --------------------------------
    # 10m
    # --------------------------------

    if c10 is not None:

        if 0.60 <= c10 < 1.50:
            score += 10

        elif 1.50 <= c10 < 3:
            score += 7

        elif c10 >= 3:
            score += 2

    # --------------------------------
    # 15m
    # --------------------------------

    if c15 is not None:

        if 0.80 <= c15 < 2:
            score += 12
            reasons.append("15m trend")

        elif 2 <= c15 < 4:
            score += 7

        elif c15 >= 4:
            score += 2

    # --------------------------------
    # acceleration
    # --------------------------------

    if c5 is not None and c10 is not None:

        if c5 > 0 and c10 > 0:

            if c5 > c10 * 0.35:
                score += 12
                reasons.append("acceleration")

    # --------------------------------
    # RSI
    # --------------------------------

    if rsi_value is not None:

        if 52 <= rsi_value <= 65:
            score += 15
            reasons.append("RSI healthy")

        elif 65 < rsi_value <= 72:
            score += 10

        elif 72 < rsi_value <= 78:
            score += 3

        elif rsi_value > 82:
            score -= 10

    # --------------------------------
    # EMA
    # --------------------------------

    if (
        ema5 is not None
        and ema10 is not None
    ):

        if price > ema5 > ema10:
            score += 15
            reasons.append("EMA bullish")

        elif price > ema5:
            score += 7

    # --------------------------------
    # Volume spike
    # --------------------------------

    if vol_ratio is not None:

        if vol_ratio >= 3:
            score += 20
            reasons.append("volume spike")

        elif vol_ratio >= 2:
            score += 15
            reasons.append("volume rising")

        elif vol_ratio >= 1.5:
            score += 8

    # --------------------------------
    # BTC filter
    # --------------------------------

    if market_1h is not None:

        if market_1h > 0.30:
            score += 6

        elif market_1h < -1:
            score -= 10
            reasons.append("BTC weak")

    return max(
        0,
        min(100, score)
    ), reasons


def analyze(
    coin,
    history,
    current_time,
    btc_1h
):

    coin_id = coin["id"]
    symbol = coin["symbol"].upper()

    price = coin.get("current_price")
    volume = coin.get("total_volume")

    if price is None:
        return None

    records = history.get(
        coin_id,
        []
    )

    records.append({
        "time": current_time,
        "price": price,
        "volume": volume or 0
    })

    # نگهداری 2 ساعت
    cutoff = current_time - 7200

    records = [
        x for x in records
        if x["time"] >= cutoff
    ]

    history[coin_id] = records

    prices = [
        x["price"]
        for x in records
    ]

    c5 = get_change(
        records,
        5,
        price,
        current_time
    )

    c10 = get_change(
        records,
        10,
        price,
        current_time
    )

    c15 = get_change(
        records,
        15,
        price,
        current_time
    )

    rsi_value = rsi(prices)

    ema5 = ema(prices, 5)
    ema10 = ema(prices, 10)

    vol_ratio = volume_ratio(
        records
    )

    score, reasons = calculate_score(
        c5,
        c10,
        c15,
        rsi_value,
        ema5,
        ema10,
        price,
        vol_ratio,
        btc_1h
    )

    return {
        "symbol": symbol,
        "price": price,
        "c5": c5,
        "c10": c10,
        "c15": c15,
        "rsi": rsi_value,
        "ema5": ema5,
        "ema10": ema10,
        "volume_ratio": vol_ratio,
        "score": score,
        "reasons": reasons,
        "samples": len(records)
    }


def send_telegram(text):

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )

    print(
        "TELEGRAM:",
        response.text
    )

    response.raise_for_status()


def main():

    current_time = now()

    history = load_history()

    market = get_market()

    btc = next(
        (
            x for x in market
            if x["id"] == "bitcoin"
        ),
        None
    )

    btc_1h = None

    if btc:
        btc_1h = btc.get(
            "price_change_percentage_1h_in_currency"
        )

    results = []

    for coin in market:

        try:

            result = analyze(
                coin,
                history,
                current_time,
                btc_1h
            )

            if result:
                results.append(result)

        except Exception as e:

            print(
                coin["id"],
                "ERROR:",
                e
            )

    save_history(history)

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    strong = [
        x for x in results
        if (
            x["score"] >= 75
            and x["c5"] is not None
            and 0 < x["c5"] < 2
            and len(x["reasons"]) >= 3
        )
    ]

    message = (
        "🚨 EARLY PUMP SCANNER\n"
