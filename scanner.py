# ============================================================
# LBANK FUTURES PRICE ACTION SCANNER v3.1
# ============================================================
#
# LBank Futures
# 5m CLOSED CANDLES
#
# LOGIC
# ------------------------------------------------------------
# 1) Get liquid LBank Futures markets
# 2) Calculate 5m Volume Ratio
# 3) Volume is ONLY used for ranking
# 4) Select TOP 30
# 5) For each market find ONLY THE LATEST
#    Tenkan / Kijun crossover
# 6) Box = 26 candles BEFORE the latest cross
# 7) Cross candle is NOT part of Box
# 8) BUY  = closed candle closes above Box High
# 9) SELL = closed candle closes below Box Low
# 10) Swing = confirmed 2-left / 2-right pivot
# 11) SL = slightly beyond swing
# 12) TP = 50% of Box Width
# 13) Maximum 1 open trade
#
# IMPORTANT
# ------------------------------------------------------------
# NO minimum volume filter.
# Volume ONLY ranks markets.
# Older crosses are NEVER checked.
#
# ============================================================

import os
import json
import time
import math
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

FUTURES_BASE = "https://lbkperp.lbank.com/cfd/openApi/v1/pub"
SPOT_BASE = "https://api.lbank.info"

PRODUCT_GROUP = "SwapU"

TIMEFRAME = "minute5"

TOP_LIQUID = 80
TOP_VOLUME = 30

CANDLE_LIMIT = 200

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

BOX_SIZE = 26

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

VOLUME_LOOKBACK = 20

TP_BOX_PERCENT = 0.50

SL_BUFFER_PERCENT = 0.0015

MAX_OPEN_TRADES = 1

REQUEST_TIMEOUT = 20

STATE_FILE = "lbank_futures_price_action_state.json"
HISTORY_FILE = "lbank_futures_price_action_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 LBank-Futures-Scanner/3.1",
    "Accept": "application/json",
})


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_str():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def ts_to_str(ts):
    """
    LBank Kline timestamp is in SECONDS.
    """
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "-"


# ============================================================
# NUMBERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        x = float(value)

        if math.isfinite(x):
            return x

    except Exception:
        pass

    return default


def fmt_price(x):
    x = safe_float(x)

    if x == 0:
        return "0"

    if abs(x) >= 1000:
        return f"{x:,.2f}"

    if abs(x) >= 100:
        return f"{x:.3f}"

    if abs(x) >= 10:
        return f"{x:.4f}"

    if abs(x) >= 1:
        return f"{x:.5f}"

    if abs(x) >= 0.1:
        return f"{x:.6f}"

    if abs(x) >= 0.01:
        return f"{x:.7f}"

    return f"{x:.10f}"


def fmt_pct(x):
    return f"{safe_float(x):+.2f}%"


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"[HTTP ERROR] "
            f"{url} "
            f"{params if params else ''} "
            f"| {e}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM] "
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured"
        )

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:
            return True

        print(
            "[TELEGRAM ERROR]",
            response.text[:1000]
        )

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

    return False


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[JSON LOAD ERROR] "
            f"{filename}: {e}"
        )

        return default


def save_json(filename, data):

    temp_file = filename + ".tmp"

    try:

        with open(
            temp_file,
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
            temp_file,
            filename
        )

    except Exception as e:

        print(
            f"[JSON SAVE ERROR] "
            f"{filename}: {e}"
        )


def default_state():

    return {
        "open_trade": None,
        "last_scan": None,
        "last_signal": None,
    }


def default_history():

    return {
        "trades": []
    }


STATE = load_json(
    STATE_FILE,
    default_state()
)

HISTORY = load_json(
    HISTORY_FILE,
    default_history()
)


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(symbol):

    if not symbol:
        return None

    symbol = str(symbol).upper().strip()

    return symbol


def futures_to_spot_symbol(symbol):

    symbol = normalize_symbol(symbol)

    if not symbol:
        return None

    if symbol.endswith("USDT"):

        base = symbol[:-4]

        return (
            f"{base.lower()}_usdt"
        )

    return symbol.lower()


# ============================================================
# FUTURES INSTRUMENTS
# ============================================================

def get_futures_instruments():

    url = (
        f"{FUTURES_BASE}/instrument"
    )

    params = {
        "productGroup": PRODUCT_GROUP
    }

    data = http_get(
        url,
        params
    )

    if not data:
        return []

    print(
        "[DEBUG] Instrument response keys:",
        list(data.keys())
        if isinstance(data, dict)
        else type(data).__name__
    )

    rows = None

    if isinstance(data, dict):

        if isinstance(
            data.get("data"),
            list
        ):
            rows = data["data"]

        elif isinstance(
            data.get("result"),
            list
        ):
            rows = data["result"]

        elif isinstance(
            data.get("rows"),
            list
        ):
            rows = data["rows"]

        elif isinstance(
            data.get("symbols"),
            list
        ):
            rows = data["symbols"]

    elif isinstance(data, list):

        rows = data

    if not isinstance(rows, list):
        return []

    result = []

    for item in rows:

        if isinstance(item, str):

            symbol = normalize_symbol(item)

            if symbol and symbol.endswith("USDT"):
                result.append(
                    {
                        "symbol": symbol
                    }
                )

            continue

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("contractCode")
            or item.get("instrument")
            or item.get("name")
        )

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        if not symbol.endswith("USDT"):
            continue

        result.append(item)

    return result


# ============================================================
# FUTURES MARKET DATA
# ============================================================

def get_futures_market_data():

    url = (
        f"{FUTURES_BASE}/marketData"
    )

    params = {
        "productGroup": PRODUCT_GROUP
    }

    data = http_get(
        url,
        params
    )

    if not data:
        return []

    print(
        "[DEBUG] MarketData response keys:",
        list(data.keys())
        if isinstance(data, dict)
        else type(data).__name__
    )

    rows = None

    if isinstance(data, dict):

        for key in [
            "data",
            "result",
            "rows",
            "marketData"
        ]:

            if isinstance(
                data.get(key),
                list
            ):

                rows = data[key]

                break

    elif isinstance(data, list):

        rows = data

    if not isinstance(rows, list):
        return []

    return rows


# ============================================================
# MARKET DATA HELPERS
# ============================================================

def market_symbol(row):

    if isinstance(row, str):
        return normalize_symbol(row)

    if not isinstance(row, dict):
        return None

    symbol = (
        row.get("symbol")
        or row.get("contractCode")
        or row.get("instrument")
        or row.get("name")
    )

    return normalize_symbol(symbol)


def market_price(row):

    if not isinstance(row, dict):
        return 0.0

    for key in [
        "lastPrice",
        "last",
        "price",
        "markedPrice",
        "markPrice"
    ]:

        value = safe_float(
            row.get(key)
        )

        if value > 0:
            return value

    return 0.0


def market_volume(row):

    if not isinstance(row, dict):
        return 0.0

    for key in [
        "turnover",
        "volume",
        "vol",
        "quoteVolume"
    ]:

        value = safe_float(
            row.get(key)
        )

        if value > 0:
            return value

    return 0.0


# ============================================================
# 5M KLINE
# ============================================================

def get_5m_candles(
    symbol,
    limit=CANDLE_LIMIT
):

    spot_symbol = (
        futures_to_spot_symbol(symbol)
    )

    if not spot_symbol:
        return []

    url = (
        f"{SPOT_BASE}/v2/kline.do"
    )

    params = {
        "symbol": spot_symbol,
        "type": "minute5",
        "size": min(
            int(limit),
            2000
        ),
    }

    data = http_get(
        url,
        params
    )

    if not data:
        return []

    rows = None

    if isinstance(data, dict):

        if isinstance(
            data.get("data"),
            list
        ):
            rows = data["data"]

        elif isinstance(
            data.get("result"),
            list
        ):
            rows = data["result"]

        elif isinstance(
            data.get("rows"),
            list
        ):
            rows = data["rows"]

    elif isinstance(data, list):

        rows = data

    if not isinstance(rows, list):

        print(
            f"[KLINE] {symbol}: "
            f"unexpected response"
        )

        return []

    candles = []

    now_sec = int(
        time.time()
    )

    for row in rows:

        if not isinstance(
            row,
            (list, tuple)
        ):
            continue

        if len(row) < 6:
            continue

        try:

            # IMPORTANT:
            # LBank Kline timestamp = seconds
            ts = int(
                float(row[0])
            )

            o = float(row[1])
            h = float(row[2])
            l = float(row[3])
            c = float(row[4])
            v = float(row[5])

            # 5-minute candle closes
            candle_close = (
                ts + 300
            )

            # Only CLOSED candles
            if candle_close > now_sec:
                continue

            candles.append(
                {
                    "ts": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                }
            )

        except Exception:

            continue

    candles.sort(
        key=lambda x: x["ts"]
    )

    candles = candles[-limit:]

    return candles


# ============================================================
# ICHIMOKU
# ============================================================

def donchian_mid(
    candles,
    end_index,
    period
):

    start = (
        end_index - period + 1
    )

    if start < 0:
        return None

    window = candles[
        start:end_index + 1
    ]

    if len(window) != period:
        return None

    highs = [
        x["high"]
        for x in window
    ]

    lows = [
        x["low"]
        for x in window
    ]

    return (
        max(highs)
        +
        min(lows)
    ) / 2.0


def calculate_ichimoku(candles):

    result = []

    for i in range(
        len(candles)
    ):

        tenkan = donchian_mid(
            candles,
            i,
            TENKAN_PERIOD
        )

        kijun = donchian_mid(
            candles,
            i,
            KIJUN_PERIOD
        )

        result.append(
            {
                "tenkan": tenkan,
                "kijun": kijun,
            }
        )

    return result


# ============================================================
# LATEST CROSS ONLY
# ============================================================

def find_latest_cross(candles):

    if len(candles) < (
        KIJUN_PERIOD + 3
    ):
        return None

    ichi = calculate_ichimoku(
        candles
    )

    latest = None

    for i in range(
        1,
        len(candles)
    ):

        prev_t = (
            ichi[i - 1]["tenkan"]
        )

        prev_k = (
            ichi[i - 1]["kijun"]
        )

        cur_t = (
            ichi[i]["tenkan"]
        )

        cur_k = (
            ichi[i]["kijun"]
        )

        if (
            prev_t is None
            or prev_k is None
            or cur_t is None
            or cur_k is None
        ):
            continue

        cross = None

        # Bullish
        if (
            prev_t <= prev_k
            and cur_t > cur_k
        ):

            cross = "BUY"

        # Bearish
        elif (
            prev_t >= prev_k
            and cur_t < cur_k
        ):

            cross = "SELL"

        if cross:

            latest = {
                "index": i,
                "type": cross,
                "timestamp": candles[i]["ts"],
                "tenkan": cur_t,
                "kijun": cur_k,
            }

    return latest


# ============================================================
# BOX
# ============================================================

def get_box(
    candles,
    cross
):

    cross_index = (
        cross["index"]
    )

    start = (
        cross_index - BOX_SIZE
    )

    end = cross_index

    if start < 0:
        return None

    box_candles = candles[
        start:end
    ]

    if len(box_candles) != BOX_SIZE:
        return None

    box_high = max(
        x["high"]
        for x in box_candles
    )

    box_low = min(
        x["low"]
        for x in box_candles
    )

    width = (
        box_high - box_low
    )

    if width <= 0:
        return None

    return {
        "start_index": start,
        "end_index": end - 1,
        "high": box_high,
        "low": box_low,
        "width": width,
    }


# ============================================================
# PIVOT LOW
# ============================================================

def is_pivot_low(
    candles,
    index
):

    if (
        index - PIVOT_LEFT
        < 0
    ):
        return False

    if (
        index + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    center = (
        candles[index]["low"]
    )

    for j in range(
        index - PIVOT_LEFT,
        index
    ):

        if (
            candles[j]["low"]
            <= center
        ):
            return False

    for j in range(
        index + 1,
        index + PIVOT_RIGHT + 1
    ):

        if (
            candles[j]["low"]
            <= center
        ):
            return False

    return True


# ============================================================
# PIVOT HIGH
# ============================================================

def is_pivot_high(
    candles,
    index
):

    if (
        index - PIVOT_LEFT
        < 0
    ):
        return False

    if (
        index + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    center = (
        candles[index]["high"]
    )

    for j in range(
        index - PIVOT_LEFT,
        index
    ):

        if (
            candles[j]["high"]
            >= center
        ):
            return False

    for j in range(
        index + 1,
        index + PIVOT_RIGHT + 1
    ):

        if (
            candles[j]["high"]
            >= center
        ):
            return False

    return True


# ============================================================
# LATEST CONFIRMED SWING
# ============================================================

def latest_swing_before(
    candles,
    start_index,
    end_index,
    side
):

    if end_index <= start_index:
        return None

    candidates = []

    last_index = min(
        end_index,
        len(candles)
        - PIVOT_RIGHT
        - 1
    )

    for i in range(
        start_index + PIVOT_LEFT,
        last_index + 1
    ):

        if side == "BUY":

            if is_pivot_low(
                candles,
                i
            ):

                candidates.append(
                    {
                        "index": i,
                        "price": candles[i]["low"],
                        "timestamp": candles[i]["ts"],
                        "type": "Swing Low",
                    }
                )

        else:

            if is_pivot_high(
                candles,
                i
            ):

                candidates.append(
                    {
                        "index": i,
                        "price": candles[i]["high"],
                        "timestamp": candles[i]["ts"],
                        "type": "Swing High",
                    }
                )

    if not candidates:
        return None

    return candidates[-1]


# ============================================================
# BREAKOUT
# ============================================================

def check_breakout(
    candles,
    cross,
    box
):

    cross_index = (
        cross["index"]
    )

    start = (
        cross_index + 1
    )

    if start >= len(candles):
        return None

    for i in range(
        start,
        len(candles)
    ):

        candle = candles[i]

        # BUY
        if (
            cross["type"] == "BUY"
            and candle["close"]
            > box["high"]
        ):

            return {
                "index": i,
                "type": "BUY",
                "timestamp": candle["ts"],
                "price": candle["close"],
            }

        # SELL
        if (
            cross["type"] == "SELL"
            and candle["close"]
            < box["low"]
        ):

            return {
                "index": i,
                "type": "SELL",
                "timestamp": candle["ts"],
                "price": candle["close"],
            }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles
):

    if len(candles) < (
        VOLUME_LOOKBACK + 1
    ):
        return 0.0

    latest_volume = (
        candles[-1]["volume"]
    )

    previous = [
        x["volume"]
        for x in candles[
            -VOLUME_LOOKBACK - 1:-1
        ]
        if x["volume"] >= 0
    ]

    if not previous:
        return 0.0

    average_volume = (
        sum(previous)
        /
        len(previous)
    )

    if average_volume <= 0:
        return 0.0

    return (
        latest_volume
        /
        average_volume
    )


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal(
    symbol,
    candles,
    cross,
    box,
    breakout,
    volume_ratio,
    rank
):

    breakout_index = (
        breakout["index"]
    )

    cross_index = (
        cross["index"]
    )

    swing = latest_swing_before(
        candles,
        cross_index,
        breakout_index,
        breakout["type"]
    )

    if not swing:
        return None

    entry = (
        breakout["price"]
    )

    box_width = (
        box["width"]
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if breakout["type"] == "BUY":

        sl = (
            swing["price"]
            *
            (
                1.0
                -
                SL_BUFFER_PERCENT
            )
        )

        tp = (
            entry
            +
            (
                box_width
                *
                TP_BOX_PERCENT
            )
        )

        if sl >= entry:
            return None

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    else:

        sl = (
            swing["price"]
            *
            (
                1.0
                +
                SL_BUFFER_PERCENT
            )
        )

        tp = (
            entry
            -
            (
                box_width
                *
                TP_BOX_PERCENT
            )
        )

        if sl <= entry:
            return None

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    if risk <= 0:
        return None

    rr = (
        reward / risk
    )

    return {
        "symbol": symbol,
        "side": breakout["type"],

        "entry": entry,
        "sl": sl,
        "tp": tp,

        "risk": risk,
        "reward": reward,
        "rr": rr,

        "rank": rank,
        "volume_ratio": volume_ratio,

        "cross_index": cross_index,
        "cross_timestamp": cross["timestamp"],
        "cross_price": candles[
            cross_index
        ]["close"],

        "breakout_index": breakout_index,
        "breakout_timestamp": breakout["timestamp"],
        "breakout_price": breakout["price"],

        "box_high": box["high"],
        "box_low": box["low"],
        "box_width": box["width"],

        "swing_type": swing["type"],
        "swing_index": swing["index"],
        "swing_price": swing["price"],
        "swing_timestamp": swing["timestamp"],

        "signal_time": now_str(),
    }


# ============================================================
# LIQUID UNIVERSE
# ============================================================

def build_liquid_universe(
    instruments,
    market_rows
):

    instrument_symbols = set()

    for item in instruments:

        symbol = market_symbol(item)

        if symbol:
            instrument_symbols.add(
                symbol
            )

    markets = []

    for row in market_rows:

        symbol = market_symbol(row)

        if not symbol:
            continue

        if (
            symbol
            not in instrument_symbols
        ):
            continue

        if not symbol.endswith(
            "USDT"
        ):
            continue

        price = market_price(
            row
        )

        volume = market_volume(
            row
        )

        if price <= 0:
            continue

        markets.append(
            {
                "symbol": symbol,
                "price": price,
                "volume": volume,
            }
        )

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_LIQUID]


# ============================================================
# RANK BY VOLUME
# ============================================================

def rank_markets_by_volume(
    liquid_markets
):

    ranked = []

    print()
    print("=" * 70)
    print("CALCULATING 5M VOLUME RANKING")
    print("=" * 70)

    for n, market in enumerate(
        liquid_markets,
        start=1
    ):

        symbol = (
            market["symbol"]
        )

        candles = get_5m_candles(
            symbol,
            CANDLE_LIMIT
        )

        if len(candles) < 60:

            print(
                f"[{n:02d}/"
                f"{len(liquid_markets):02d}] "
                f"{symbol:<20} "
                f"NO KLINES "
                f"({len(candles)})"
            )

            continue

        ratio = (
            calculate_volume_ratio(
                candles
            )
        )

        ranked.append(
            {
                "symbol": symbol,
                "futures_price":
                    market["price"],
                "futures_volume":
                    market["volume"],
                "volume_ratio": ratio,
                "candles": candles,
            }
        )

        print(
            f"[{n:02d}/"
            f"{len(liquid_markets):02d}] "
            f"{symbol:<20} "
            f"VOL={ratio:.2f}x "
            f"CANDLES={len(candles)}"
        )

        time.sleep(0.05)

    ranked.sort(
        key=lambda x:
        x["volume_ratio"],
        reverse=True
    )

    return ranked[:TOP_VOLUME]


# ============================================================
# SCAN TOP 30
# ============================================================

def scan_top_markets(
    top_markets
):

    signals = []

    diagnostics = {
        "markets": len(top_markets),
        "latest_cross": 0,
        "no_cross": 0,
        "box_ok": 0,
        "no_box": 0,
        "breakout": 0,
        "no_breakout": 0,
        "swing_ok": 0,
        "no_swing": 0,
        "signal": 0,
    }

    print()
    print("=" * 70)
    print("SCANNING TOP 30")
    print("=" * 70)

    for rank, market in enumerate(
        top_markets,
        start=1
    ):

        symbol = (
            market["symbol"]
        )

        candles = (
            market["candles"]
        )

        volume_ratio = (
            market["volume_ratio"]
        )

        print()
        print(
            f"[{rank:02d}/"
            f"{len(top_markets):02d}] "
            f"{symbol} "
            f"| Volume={volume_ratio:.2f}x"
        )

        # ----------------------------------------------------
        # ONLY LATEST CROSS
        # ----------------------------------------------------

        cross = find_latest_cross(
            candles
        )

        if not cross:

            diagnostics[
                "no_cross"
            ] += 1

            print(
                "  -> NO CROSS"
            )

            continue

        diagnostics[
            "latest_cross"
        ] += 1

        print(
            f"  -> Latest Cross: "
            f"{cross['type']} "
            f"@ "
            f"{ts_to_str(cross['timestamp'])}"
        )

        # ----------------------------------------------------
        # BOX
        # ----------------------------------------------------

        box = get_box(
            candles,
            cross
        )

        if not box:

            diagnostics[
                "no_box"
            ] += 1

            print(
                "  -> BOX UNAVAILABLE"
            )

            continue

        diagnostics[
            "box_ok"
        ] += 1

        print(
            f"  -> Box High: "
            f"{fmt_price(box['high'])}"
        )

        print(
            f"  -> Box Low : "
            f"{fmt_price(box['low'])}"
        )

        print(
            f"  -> Box Width: "
            f"{fmt_price(box['width'])}"
        )

        # ----------------------------------------------------
        # BREAKOUT
        # ----------------------------------------------------

        breakout = check_breakout(
            candles,
            cross,
            box
        )

        if not breakout:

            diagnostics[
                "no_breakout"
            ] += 1

            print(
                "  -> NO BREAKOUT "
                "AFTER LATEST CROSS"
            )

            continue

        diagnostics[
            "breakout"
        ] += 1

        print(
            f"  -> BREAKOUT: "
            f"{breakout['type']} "
            f"@ "
            f"{fmt_price(breakout['price'])}"
        )

        # ----------------------------------------------------
        # SWING
        # ----------------------------------------------------

        swing = latest_swing_before(
            candles,
            cross["index"],
            breakout["index"],
            breakout["type"]
        )

        if not swing:

            diagnostics[
                "no_swing"
            ] += 1

            print(
                "  -> NO CONFIRMED SWING"
            )

            continue

        diagnostics[
            "swing_ok"
        ] += 1

        print(
            f"  -> {swing['type']}: "
            f"{fmt_price(swing['price'])}"
        )

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        signal = build_signal(
            symbol,
            candles,
            cross,
            box,
            breakout,
            volume_ratio,
            rank
        )

        if not signal:
            continue

        diagnostics[
            "signal"
        ] += 1

        signals.append(
            signal
        )

        print(
            f"  -> FINAL SIGNAL: "
            f"{signal['side']}"
        )

    return (
        signals,
        diagnostics
    )


# ============================================================
# STATE
# ============================================================

def get_open_trade():

    return STATE.get(
        "open_trade"
    )


def set_open_trade(
    signal
):

    STATE[
        "open_trade"
    ] = signal

    STATE[
        "last_signal"
    ] = signal

    STATE[
        "last_scan"
    ] = now_str()

    save_json(
        STATE_FILE,
        STATE
    )


def clear_open_trade():

    STATE[
        "open_trade"
    ] = None

    STATE[
        "last_scan"
    ] = now_str()

    save_json(
        STATE_FILE,
        STATE
    )


# ============================================================
# PERFORMANCE
# ============================================================

def get_history_stats():

    trades = HISTORY.get(
        "trades",
        []
    )

    total = len(
        trades
    )

    wins = 0
    losses = 0
    breakeven = 0
    pnl_sum = 0.0

    for trade in trades:

        result = trade.get(
            "result"
        )

        if result == "WIN":

            wins += 1

        elif result == "LOSS":

            losses += 1

        else:

            breakeven += 1

        pnl_sum += safe_float(
            trade.get(
                "pnl_percent"
            )
        )

    win_rate = (
        (
            wins / total
        )
        * 100.0
        if total > 0
        else 0.0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "pnl": pnl_sum,
    }


# ============================================================
# MONITOR OPEN TRADE
# ============================================================

def monitor_open_trade(
    market_rows
):

    trade = get_open_trade()

    if not trade:
        return False

    symbol = trade[
        "symbol"
    ]

    current_price = 0.0

    for row in market_rows:

        if (
            market_symbol(row)
            == symbol
        ):

            current_price = (
                market_price(row)
            )

            if current_price > 0:
                break

    if current_price <= 0:

        print(
            f"[OPEN TRADE] "
            f"Current price unavailable: "
            f"{symbol}"
        )

        return False

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    side = trade[
        "side"
    ]

    result = None

    if side == "BUY":

        if current_price >= tp:

            result = "WIN"

        elif current_price <= sl:

            result = "LOSS"

        pnl = (
            (
                current_price
                - entry
            )
            / entry
        ) * 100.0

    else:

        if current_price <= tp:

            result = "WIN"

        elif current_price >= sl:

            result = "LOSS"

        pnl = (
            (
                entry
                - current_price
            )
            / entry
        ) * 100.0

    trade[
        "current_price"
    ] = current_price

    trade[
        "live_pnl_percent"
    ] = pnl

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    if result:

        closed_trade = dict(
            trade
        )

        closed_trade[
            "exit"
        ] = current_price

        closed_trade[
            "result"
        ] = result

        closed_trade[
            "pnl_percent"
        ] = pnl

        closed_trade[
            "close_time"
        ] = now_str()

        HISTORY.setdefault(
            "trades",
            []
        ).append(
            closed_trade
        )

        save_json(
            HISTORY_FILE,
            HISTORY
        )

        clear_open_trade()

        stats = (
            get_history_stats()
        )

        emoji = (
            "🟢"
            if result == "WIN"
            else "🔴"
        )

        message = (
            f"{emoji} "
            f"*TRADE CLOSED*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"#{symbol} | {side}\n"
            f"Entry: "
            f"`{fmt_price(entry)}`\n"
            f"Exit: "
            f"`{fmt_price(current_price)}`\n"
            f"Result: *{result}*\n"
            f"PnL: "
            f"*{fmt_pct(pnl)}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Trades: "
            f"{stats['total']} | "
            f"🟢 {stats['wins']} | "
            f"🔴 {stats['losses']}\n"
            f"WR: "
            f"*{stats['win_rate']:.1f}%*\n"
            f"Total PnL: "
            f"*{fmt_pct(stats['pnl'])}*"
        )

        print(
            message
        )

        send_telegram(
            message
        )

        return True

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    STATE[
        "open_trade"
    ] = trade

    STATE[
        "last_scan"
    ] = now_str()

    save_json(
        STATE_FILE,
        STATE
    )

    print()
    print("=" * 70)
    print("OPEN TRADE")
    print("=" * 70)

    print(
        f"{symbol} {side}"
    )

    print(
        f"Entry   : "
        f"{fmt_price(entry)}"
    )

    print(
        f"Current : "
        f"{fmt_price(current_price)}"
    )

    print(
        f"SL      : "
        f"{fmt_price(sl)}"
    )

    print(
        f"TP      : "
        f"{fmt_price(tp)}"
    )

    print(
        f"Live P&L: "
        f"{fmt_pct(pnl)}"
    )

    return True


# ============================================================
# SELECT SIGNAL
# ============================================================

def select_best_signal(
    signals
):

    if not signals:
        return None

    # TOP volume rank has priority
    signals.sort(
        key=lambda x: (
            x.get(
                "rank",
                999999
            ),
            -x.get(
                "rr",
                0
            )
        )
    )

    return signals[0]


# ============================================================
# SIGNAL REPORT
# ============================================================

def build_signal_message(
    signal,
    diagnostics
):

    stats = (
        get_history_stats()
    )

    symbol = signal[
        "symbol"
    ]

    side = signal[
        "side"
    ]

    emoji = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    return (
        f"🚨 *CRYPTO PRICE ACTION SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} *{symbol} - {side}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📍 Entry: "
        f"`{fmt_price(signal['entry'])}`\n"
        f"🛑 SL: "
        f"`{fmt_price(signal['sl'])}`\n"
        f"🎯 TP: "
        f"`{fmt_price(signal['tp'])}`\n"
        f"📐 RR: "
        f"`1:{signal['rr']:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 *BOX*\n"
        f"High: "
        f"`{fmt_price(signal['box_high'])}`\n"
        f"Low: "
        f"`{fmt_price(signal['box_low'])}`\n"
        f"Width: "
        f"`{fmt_price(signal['box_width'])}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔄 *LATEST CROSS*\n"
        f"Type: `{side}`\n"
        f"Time: "
        f"`{ts_to_str(signal['cross_timestamp'])}`\n"
        f"Price: "
        f"`{fmt_price(signal['cross_price'])}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💥 *BREAKOUT*\n"
        f"Time: "
        f"`{ts_to_str(signal['breakout_timestamp'])}`\n"
        f"Price: "
        f"`{fmt_price(signal['breakout_price'])}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧱 *SWING*\n"
        f"{signal['swing_type']}: "
        f"`{fmt_price(signal['swing_price'])}`\n"
        f"Time: "
        f"`{ts_to_str(signal['swing_timestamp'])}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Volume Rank: "
        f"`#{signal['rank']}`\n"
        f"🔥 Volume Ratio: "
        f"`{signal['volume_ratio']:.2f}x`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *PERFORMANCE*\n"
        f"Trades: "
        f"{stats['total']} | "
        f"🟢 {stats['wins']} | "
        f"🔴 {stats['losses']}\n"
        f"WR: "
        f"`{stats['win_rate']:.1f}%`\n"
        f"Total PnL: "
        f"`{fmt_pct(stats['pnl'])}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 `{now_str()}`"
    )


# ============================================================
# NO SIGNAL REPORT
# ============================================================

def build_no_signal_report(
    diagnostics,
    top_markets
):

    stats = (
        get_history_stats()
    )

    lines = []

    lines.append(
        "📡 *CRYPTO PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 `{now_str()}`"
    )

    lines.append(
        "⏱ *5m CLOSED | TOP 30*"
    )

    lines.append(
        "🤖 *LATEST CROSS + VOLUME RANKING*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📊 *PERFORMANCE*\n"
        f"Trades {stats['total']} | "
        f"🟢 {stats['wins']} | "
        f"🔴 {stats['losses']}\n"
        f"🏆 WR: "
        f"{stats['win_rate']:.1f}%\n"
        f"💰 Total PnL: "
        f"{stats['pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔎 *SCAN DIAGNOSTICS*"
    )

    lines.append(
        f"Markets: "
        f"`{diagnostics['markets']}`"
    )

    lines.append(
        f"Latest Cross: "
        f"`{diagnostics['latest_cross']}`"
    )

    lines.append(
        f"No Cross: "
        f"`{diagnostics['no_cross']}`"
    )

    lines.append(
        f"Valid Box: "
        f"`{diagnostics['box_ok']}`"
    )

    lines.append(
        f"Breakout: "
        f"`{diagnostics['breakout']}`"
    )

    lines.append(
        f"Swing Valid: "
        f"`{diagnostics['swing_ok']}`"
    )

    lines.append(
        f"Final Signals: "
        f"`{diagnostics['signal']}`"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if top_markets:

        lines.append(
            "🔥 *TOP VOLUME RANKING*"
        )

        for i, market in enumerate(
            top_markets[:10],
            start=1
        ):

            lines.append(
                f"{i}. "
                f"`{market['symbol']}` "
                f"`{market['volume_ratio']:.2f}x`"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "⚪ *NO VALID SIGNAL*"
    )

    return "\n".join(
        lines
    )


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def build_open_trade_report():

    trade = get_open_trade()

    if not trade:
        return None

    stats = (
        get_history_stats()
    )

    side = trade[
        "side"
    ]

    emoji = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    current = safe_float(
        trade.get(
            "current_price"
        )
    )

    pnl = safe_float(
        trade.get(
            "live_pnl_percent"
        )
    )

    return (
        f"📡 *CRYPTO PRICE ACTION REPORT*\n"
        f"🕐 `{now_str()}`\n"
        f"⏱ *5m CLOSED | TOP 30*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔒 *OPEN TRADE*\n"
        f"{emoji} "
        f"*{trade['symbol']} - {side}*\n"
        f"Entry: "
        f"`{fmt_price(trade['entry'])}`\n"
        f"Current: "
        f"`{fmt_price(current)}`\n"
        f"SL: "
        f"`{fmt_price(trade['sl'])}`\n"
        f"TP: "
        f"`{fmt_price(trade['tp'])}`\n"
        f"Live P&L: "
        f"*{fmt_pct(pnl)}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 Box High: "
        f"`{fmt_price(trade['box_high'])}`\n"
        f"📦 Box Low: "
        f"`{fmt_price(trade['box_low'])}`\n"
        f"🔥 Volume Ratio: "
        f"`{trade['volume_ratio']:.2f}x`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *PERFORMANCE*\n"
        f"Trades {stats['total']} | "
        f"🟢 {stats['wins']} | "
        f"🔴 {stats['losses']}\n"
        f"🏆 WR: "
        f"`{stats['win_rate']:.1f}%`\n"
        f"💰 Total PnL: "
        f"`{stats['pnl']:+.2f}%`"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print(
        "LBANK FUTURES PRICE ACTION SCANNER v3.1"
    )
    print("=" * 70)

    print(
        f"Timeframe       : {TIMEFRAME}"
    )

    print(
        f"Liquid Universe : TOP {TOP_LIQUID}"
    )

    print(
        f"Volume Ranking  : TOP {TOP_VOLUME}"
    )

    print(
        f"Box             : {BOX_SIZE}"
    )

    print(
        f"Tenkan          : {TENKAN_PERIOD}"
    )

    print(
        f"Kijun           : {KIJUN_PERIOD}"
    )

    print(
        "Cross           : LATEST ONLY"
    )

    print(
        "Volume Filter   : NONE"
    )

    print(
        f"Max Open        : {MAX_OPEN_TRADES}"
    )

    print("=" * 70)

    # ========================================================
    # FUTURES INSTRUMENTS
    # ========================================================

    instruments = (
        get_futures_instruments()
    )

    print(
        f"[INFO] Futures instruments: "
        f"{len(instruments)}"
    )

    if not instruments:

        message = (
            "⚠️ *LBANK FUTURES SCANNER*\n\n"
            "No Futures instruments found."
        )

        print(
            message
        )

        send_telegram(
            message
        )

        return

    # ========================================================
    # FUTURES MARKET DATA
    # ========================================================

    market_rows = (
        get_futures_market_data()
    )

    print(
        f"[INFO] Futures markets: "
        f"{len(market_rows)}"
    )

    if not market_rows:

        message = (
            "⚠️ *LBANK FUTURES SCANNER*\n\n"
            "No Futures market data found."
        )

        print(
            message
        )

        send_telegram(
            message
        )

        return

    # ========================================================
    # EXISTING TRADE
    # ========================================================

    if get_open_trade():

        print(
            "[INFO] Existing open trade."
        )

        monitor_open_trade(
            market_rows
        )

        if get_open_trade():

            report = (
                build_open_trade_report()
            )

            if report:

                send_telegram(
                    report
                )

            print(
                "[INFO] Maximum 1 open trade. "
                "New signals skipped."
            )

            return

    # ========================================================
    # LIQUID UNIVERSE
    # ========================================================

    liquid_markets = (
        build_liquid_universe(
            instruments,
            market_rows
        )
    )

    print(
        f"[INFO] Liquid Futures markets: "
        f"{len(liquid_markets)}"
    )

    if not liquid_markets:

        message = (
            "⚠️ *LBANK FUTURES SCANNER*\n\n"
            "No liquid Futures markets found."
        )

        print(
            message
        )

        send_telegram(
            message
        )

        return

    # ========================================================
    # VOLUME RANKING
    # ========================================================

    top_markets = (
        rank_markets_by_volume(
            liquid_markets
        )
    )

    print()
    print("=" * 70)
    print(
        "TOP VOLUME-RANKED MARKETS"
    )
    print("=" * 70)

    for i, market in enumerate(
        top_markets,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{market['symbol']:<20} "
            f"{market['volume_ratio']:.2f}x"
        )

    if not top_markets:

        message = (
            "⚠️ *LBANK FUTURES SCANNER*\n\n"
            "No markets available after "
            "volume ranking.\n\n"
            "Check Kline API response."
        )

        print(
            message
        )

        send_telegram(
            message
        )

        return

    # ========================================================
    # SCAN TOP 30
    # ========================================================

    signals, diagnostics = (
        scan_top_markets(
            top_markets
        )
    )

    print()
    print("=" * 70)
    print(
        "FINAL RESULTS"
    )
    print("=" * 70)

    print(
        f"Markets        : "
        f"{diagnostics['markets']}"
    )

    print(
        f"Latest Crosses : "
        f"{diagnostics['latest_cross']}"
    )

    print(
        f"Breakouts      : "
        f"{diagnostics['breakout']}"
    )

    print(
        f"Valid Swings   : "
        f"{diagnostics['swing_ok']}"
    )

    print(
        f"Signals        : "
        f"{diagnostics['signal']}"
    )

    # ========================================================
    # SELECT ONE
    # ========================================================

    selected = (
        select_best_signal(
            signals
        )
    )

    if not selected:

        report = (
            build_no_signal_report(
                diagnostics,
                top_markets
            )
        )

        print()
        print(
            report
        )

        send_telegram(
            report
        )

        STATE[
            "last_scan"
        ] = now_str()

        save_json(
            STATE_FILE,
            STATE
        )

        return

    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    print()
    print("=" * 70)
    print(
        "FINAL SIGNAL"
    )
    print("=" * 70)

    print(
        f"{selected['symbol']} "
        f"{selected['side']}"
    )

    print(
        f"Rank: "
        f"#{selected['rank']}"
    )

    print(
        f"Volume Ratio: "
        f"{selected['volume_ratio']:.2f}x"
    )

    print(
        f"Entry: "
        f"{fmt_price(selected['entry'])}"
    )

    print(
        f"SL: "
        f"{fmt_price(selected['sl'])}"
    )

    print(
        f"TP: "
        f"{fmt_price(selected['tp'])}"
    )

    print(
        f"RR: "
        f"1:{selected['rr']:.2f}"
    )

    # ========================================================
    # SAVE
    # ========================================================

    set_open_trade(
        selected
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    message = (
        build_signal_message(
            selected,
            diagnostics
        )
    )

    print()
    print(
        message
    )

    send_telegram(
        message
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "[STOPPED] "
            "Keyboard interrupt."
        )

    except Exception as e:

        print()
        print("=" * 70)
        print("FATAL ERROR")
        print("=" * 70)

        print(
            repr(e)
        )

        error_message = (
            "🚨 *LBANK FUTURES SCANNER ERROR*\n\n"
            f"`{str(e)[:3500]}`"
        )

        send_telegram(
            error_message
        )

        raise
