# ============================================================
# UT BOT ALERT — ADDITION ONLY
# Pine Script UT Bot Alerts
# Key Value = 3
# ATR Period = 10
# Heikin Ashi = False
# Closed 5m candles
# ============================================================

UT_KEY_VALUE = 3
UT_ATR_PERIOD = 10


def calculate_utbot_atr(candles, period=10):
    """
    Pine Script ATR = RMA(True Range, period)
    """
    if len(candles) < period + 1:
        return [0.0] * len(candles)

    tr = [0.0] * len(candles)

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr[i] = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

    atr = [0.0] * len(candles)

    # Pine RMA seed = SMA
    first_values = tr[1:period + 1]

    if len(first_values) < period:
        return atr

    atr[period] = sum(first_values) / period

    alpha = 1.0 / period

    for i in range(period + 1, len(candles)):
        atr[i] = (
            alpha * tr[i]
            + (1 - alpha) * atr[i - 1]
        )

    return atr


def calculate_utbot_signal(candles):
    """
    Exact UT Bot logic based on the supplied Pine Script.

    a = 3
    c = 10
    h = false
    """

    if len(candles) < 30:
        return None

    src = [
        c["close"]
        for c in candles
    ]

    atr = calculate_utbot_atr(
        candles,
        UT_ATR_PERIOD
    )

    nloss = [
        UT_KEY_VALUE * x
        for x in atr
    ]

    trailing = [0.0] * len(candles)
    pos = [0] * len(candles)

    # --------------------------------------------------------
    # Pine:
    #
    # xATRTrailingStop := iff(
    #   src > nz(stop[1],0) and src[1] > nz(stop[1],0),
    #   max(stop[1], src-nLoss),
    #
    #   iff(
    #     src < stop[1] and src[1] < stop[1],
    #     min(stop[1], src+nLoss),
    #
    #     iff(
    #       src > stop[1],
    #       src-nLoss,
    #       src+nLoss
    #     )
    #   )
    # )
    # --------------------------------------------------------

    for i in range(len(candles)):

        if i == 0:
            trailing[i] = 0.0
            pos[i] = 0
            continue

        prev_stop = trailing[i - 1]

        if (
            src[i] > prev_stop
            and src[i - 1] > prev_stop
        ):
            trailing[i] = max(
                prev_stop,
                src[i] - nloss[i]
            )

        elif (
            src[i] < prev_stop
            and src[i - 1] < prev_stop
        ):
            trailing[i] = min(
                prev_stop,
                src[i] + nloss[i]
            )

        elif src[i] > prev_stop:
            trailing[i] = src[i] - nloss[i]

        else:
            trailing[i] = src[i] + nloss[i]

        # ----------------------------------------------------
        # pos
        # ----------------------------------------------------

        if (
            src[i - 1] < prev_stop
            and src[i] > prev_stop
        ):
            pos[i] = 1

        elif (
            src[i - 1] > prev_stop
            and src[i] < prev_stop
        ):
            pos[i] = -1

        else:
            pos[i] = pos[i - 1]

    # --------------------------------------------------------
    # EMA(src, 1) = src
    #
    # crossover(ema, trailing)
    # crossover(trailing, ema)
    # --------------------------------------------------------

    i = len(candles) - 1

    if i < 1:
        return None

    ema_now = src[i]
    ema_prev = src[i - 1]

    stop_now = trailing[i]
    stop_prev = trailing[i - 1]

    above = (
        ema_now > stop_now
        and ema_prev <= stop_prev
    )

    below = (
        stop_now > ema_now
        and stop_prev <= ema_prev
    )

    buy = (
        src[i] > trailing[i]
        and above
    )

    sell = (
        src[i] < trailing[i]
        and below
    )

    if buy:
        return {
            "signal": "BUY",
            "price": src[i],
            "candle_time": candles[i]["time"],
            "trailing_stop": trailing[i],
            "atr": atr[i],
        }

    if sell:
        return {
            "signal": "SELL",
            "price": src[i],
            "candle_time": candles[i]["time"],
            "trailing_stop": trailing[i],
            "atr": atr[i],
        }

    return None
