import os
import requests
import pandas as pd
import numpy as np

# ==========================================
# SETTINGS
# ==========================================

SYMBOL = "ZECUSDT"
INTERVAL = "5m"

KEY_VALUE = 3
ATR_PERIOD = 10

HEIKIN_ASHI = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE_URL = "https://api.binance.com/api/v3/klines"


# ==========================================
# GET BINANCE DATA
# ==========================================

def get_klines(limit=300):

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": limit
    }

    response = requests.get(
        BINANCE_URL,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    df = pd.DataFrame(data, columns=[
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "buy_volume",
        "buy_quote_volume",
        "ignore"
    ])

    for column in ["open", "high", "low", "close"]:
        df[column] = df[column].astype(float)

    return df


# ==========================================
# HEIKIN ASHI
# ==========================================

def calculate_heikin_ashi(df):

    ha = pd.DataFrame(index=df.index)

    ha["close"] = (
        df["open"] +
        df["high"] +
        df["low"] +
        df["close"]
    ) / 4

    ha["open"] = 0.0

    for i in range(len(df)):

        if i == 0:
            ha.iloc[i, ha.columns.get_loc("open")] = (
                df.iloc[i]["open"] +
                df.iloc[i]["close"]
            ) / 2
        else:
            ha.iloc[i, ha.columns.get_loc("open")] = (
                ha.iloc[i - 1]["open"] +
                ha.iloc[i - 1]["close"]
            ) / 2

    ha["high"] = pd.concat(
        [
            df["high"],
            ha["open"],
            ha["close"]
        ],
        axis=1
    ).max(axis=1)

    ha["low"] = pd.concat(
        [
            df["low"],
            ha["open"],
            ha["close"]
        ],
        axis=1
    ).min(axis=1)

    return ha


# ==========================================
# TRUE RANGE
# ==========================================

def calculate_true_range(df):

    previous_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]

    tr2 = (
        df["high"] -
        previous_close
    ).abs()

    tr3 = (
        df["low"] -
        previous_close
    ).abs()

    return pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)


# ==========================================
# ATR
# ==========================================

def calculate_atr(df, period):

    tr = calculate_true_range(df)

    # TradingView Pine v4 ATR uses RMA
    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# ==========================================
# UT BOT
# ==========================================

def calculate_utbot(df):

    # Source
    if HEIKIN_ASHI:

        ha = calculate_heikin_ashi(df)

        src = ha["close"].copy()

        # For HA source, Pine's security() supplies HA close.
        # ATR in the original script is calculated from the
        # chart's regular OHLC.
        atr = calculate_atr(
            df,
            ATR_PERIOD
        )

    else:

        src = df["close"].copy()

        atr = calculate_atr(
            df,
            ATR_PERIOD
        )

    n_loss = KEY_VALUE * atr

    trailing_stop = np.zeros(len(df))

    # ==========================================
    # EXACT UT BOT TRAILING STOP LOGIC
    # ==========================================

    for i in range(len(df)):

        if i == 0:

            trailing_stop[i] = 0.0

            continue

        previous_stop = trailing_stop[i - 1]

        current_src = src.iloc[i]
        previous_src = src.iloc[i - 1]

        # Pine:
        #
        # iff(
        # src > stop[1] and src[1] > stop[1],
        # max(stop[1], src - nLoss),
        #
        # iff(
        # src < stop[1] and src[1] < stop[1],
        # min(stop[1], src + nLoss),
        #
        # iff(
        # src > stop[1],
        # src - nLoss,
        # src + nLoss
        # )))

        if (
            current_src > previous_stop
            and previous_src > previous_stop
        ):

            trailing_stop[i] = max(
                previous_stop,
                current_src - n_loss.iloc[i]
            )

        elif (
            current_src < previous_stop
            and previous_src < previous_stop
        ):

            trailing_stop[i] = min(
                previous_stop,
                current_src + n_loss.iloc[i]
            )

        elif current_src > previous_stop:

            trailing_stop[i] = (
                current_src -
                n_loss.iloc[i]
            )

        else:

            trailing_stop[i] = (
                current_src +
                n_loss.iloc[i]
            )

    df["src"] = src
    df["atr"] = atr
    df["nLoss"] = n_loss
    df["trailing_stop"] = trailing_stop

    # ==========================================
    # EMA 1
    # ==========================================

    # Pine:
    # ema = ema(src,1)
    #
    # EMA period 1 = source itself

    ema = src.copy()

    df["ema"] = ema

    # ==========================================
    # CROSSOVER
    # ==========================================

    # Pine:
    #
    # above = crossover(ema, trailingStop)
    #
    # crossover(a,b):
    # a > b AND previous a <= previous b

    above = (
        (ema > df["trailing_stop"]) &
        (
            ema.shift(1) <=
            df["trailing_stop"].shift(1)
        )
    )

    # Pine:
    #
    # below = crossover(trailingStop, ema)

    below = (
        (df["trailing_stop"] > ema) &
        (
            df["trailing_stop"].shift(1) <=
            ema.shift(1)
        )
    )

    # ==========================================
    # BUY / SELL
    # ==========================================

    buy = (
        (src > df["trailing_stop"]) &
        above
    )

    sell = (
        (src < df["trailing_stop"]) &
        below
    )

    df["buy"] = buy
    df["sell"] = sell

    return df


# ==========================================
# TELEGRAM
# ==========================================

def send_telegram(message):

    if not TELEGRAM_TOKEN:
        raise ValueError(
            "TELEGRAM_TOKEN is missing"
        )

    if not TELEGRAM_CHAT_ID:
        raise ValueError(
            "TELEGRAM_CHAT_ID is missing"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    response = requests.post(
        url,
        data=payload,
        timeout=20
    )

    response.raise_for_status()


# ==========================================
# MAIN
# ==========================================

def main():

    print("================================")
    print("ZEC UT BOT ALERT")
    print("================================")
    print(f"Symbol: {SYMBOL}")
    print(f"Timeframe: {INTERVAL}")
    print(f"Key Value: {KEY_VALUE}")
    print(f"ATR Period: {ATR_PERIOD}")
    print(f"Heikin Ashi: {HEIKIN_ASHI}")
    print("================================")

    df = get_klines()

    df = calculate_utbot(df)

    # ==========================================
    # LAST CLOSED CANDLE
    # ==========================================

    # Binance's last candle can still be open.
    # Therefore use [-2].

    candle = df.iloc[-2]

    candle_time = pd.to_datetime(
        candle["time"],
        unit="ms",
        utc=True
    )

    price = candle["close"]

    print(
        f"Last closed candle: {candle_time}"
    )

    print(
        f"Price: {price}"
    )

    # ==========================================
    # SIGNAL
    # ==========================================

    if candle["buy"]:

        message = f"""🟢 ZEC/USDT BUY

🤖 UT Bot Alerts

⚙️ Key Value: 3
⚙️ ATR Period: 10
⏱ Timeframe: 5m

💰 Price: {price:.4f}

📊 Signal: BUY
🕯 Candle: {candle_time}

#ZEC #ZECUSDT #BUY
"""

        send_telegram(message)

        print("🟢 BUY SIGNAL SENT")

    elif candle["sell"]:

        message = f"""🔴 ZEC/USDT SELL

🤖 UT Bot Alerts

⚙️ Key Value: 3
⚙️ ATR Period: 10
⏱ Timeframe: 5m

💰 Price: {price:.4f}

📊 Signal: SELL
🕯 Candle: {candle_time}

#ZEC #ZECUSDT #SELL
"""

        send_telegram(message)

        print("🔴 SELL SIGNAL SENT")

    else:

        print("No new signal.")


if __name__ == "__main__":
    main()
