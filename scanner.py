# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v2
# ============================================================
#
# TOP 100 KRAKEN FUTURES
#
# TIMEFRAMES
# ------------------------------------------------------------
# 1H  = Main Trend
# 30m = Confirmation
# 15m = Pullback
# 5m  = Entry Trigger
#
# SIGNAL MODES
# ------------------------------------------------------------
# STRONG:
#   1H + 30m + 15m Pullback + 5m Trigger
#
# EARLY:
#   1H + 30m + 5m Trigger
#   15m pullback NOT mandatory
#
# ONLY ONE OPEN TRADE
#
# Virtual simulator only
# No real orders
# ============================================================

import os
import json
import time
import math
import traceback
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIG
# ============================================================

KRAKEN_BASE_URL = "https://futures.kraken.com"

KRAKEN_API_BASE = (
    KRAKEN_BASE_URL +
    "/derivatives/api/v3"
)

KRAKEN_CHART_BASE = (
    KRAKEN_BASE_URL +
    "/api/charts/v1"
)

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "")
    .strip()
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID", "")
    .strip()
)


# ============================================================
# MARKET
# ============================================================

TOP_SYMBOLS = 100

TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60
}

CANDLE_LIMIT = 180


# ============================================================
# ICHIMOKU
# ============================================================

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26


# ============================================================
# ATR
# ============================================================

ATR_PERIOD = 14

MAX_PULLBACK_DISTANCE_ATR = 1.50


# ============================================================
# SCORES
# ============================================================

MIN_1H_SCORE = 7.0
MIN_30M_SCORE = 6.0
MIN_15M_SCORE = 4.0

# EARLY signal requires stronger 1H/30m trend
MIN_EARLY_1H_SCORE = 8.0
MIN_EARLY_30M_SCORE = 7.0


# ============================================================
# TRADE
# ============================================================

MAX_OPEN_TRADES = 1

TARGET_RR = 1.0

SL_BUFFER_PERCENT = 0.15

MAX_HOLDING_HOURS = 4

MAX_SIGNAL_AGE_CANDLES = 0


# ============================================================
# RUNTIME
# ============================================================

MAX_WORKERS = 8

REQUEST_TIMEOUT = 20

REQUEST_RETRIES = 3

STATE_FILE = "kraken_ichimoku_state.json"

HISTORY_FILE = "kraken_ichimoku_trade_history.json"


# ============================================================
# HTTP
# ============================================================

KRAKEN_SESSION = requests.Session()

KRAKEN_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent":
        "Kraken-Futures-Ichimoku-Scanner/2.0"
})


TELEGRAM_SESSION = requests.Session()

TELEGRAM_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent":
        "Kraken-Futures-Ichimoku-Scanner/2.0"
})


STATE_LOCK = threading.Lock()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_timestamp():
    return int(
        utc_now().timestamp()
    )


def format_time(ts):

    try:

        if isinstance(
            ts,
            (int, float)
        ):

            dt = datetime.fromtimestamp(
                ts,
                tz=timezone.utc
            )

        else:

            dt = datetime.fromisoformat(
                str(ts).replace(
                    "Z",
                    "+00:00"
                )
            )

        return dt.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    except Exception:

        return str(ts)


# ============================================================
# NUMBERS
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)

    except Exception:
        return default


def fmt_price(value):

    value = safe_float(value)

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:,.3f}"

    if abs(value) >= 10:
        return f"{value:,.4f}"

    if abs(value) >= 1:
        return f"{value:,.5f}"

    if abs(value) >= 0.1:
        return f"{value:,.6f}"

    if abs(value) >= 0.01:
        return f"{value:,.7f}"

    return f"{value:.10f}"


# ============================================================
# JSON
# ============================================================

def load_json(
    path,
    default
):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[WARN] Cannot load "
            f"{path}: {e}"
        )

        return default


def save_json(
    path,
    data
):

    temp_path = path + ".tmp"

    try:

        with open(
            temp_path,
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
            temp_path,
            path
        )

    except Exception as e:

        print(
            f"[ERROR] Cannot save "
            f"{path}: {e}"
        )


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "open_trade": None,
        "last_signal_key": None,
        "last_checked": None
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


if not isinstance(
    STATE,
    dict
):

    STATE = default_state()


if not isinstance(
    HISTORY,
    dict
):

    HISTORY = default_history()


if not isinstance(
    HISTORY.get("trades"),
    list
):

    HISTORY["trades"] = []


# ============================================================
# KRAKEN HTTP
# ============================================================

def kraken_get(
    url,
    params=None,
    retries=REQUEST_RETRIES
):

    last_error = None

    for attempt in range(
        1,
        retries + 1
    ):

        try:

            response = KRAKEN_SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            last_error = e

            print(
                f"[WARN] Kraken request failed "
                f"{attempt}/{retries}: {e}"
            )

            if attempt < retries:

                time.sleep(
                    1.5 * attempt
                )

    raise RuntimeError(
        f"Kraken request failed: "
        f"{last_error}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] TELEGRAM_BOT_TOKEN "
            "not configured."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] TELEGRAM_CHAT_ID "
            "not configured."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendMessage"
    )

    try:

        payload = {
            "chat_id":
                str(TELEGRAM_CHAT_ID),
            "text":
                str(message),
            "parse_mode":
                "Markdown"
        }

        response = (
            TELEGRAM_SESSION.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT
            )
        )

        if response.status_code != 200:

            print(
                "[WARN] Telegram HTTP error:",
                response.status_code,
                response.text[:500]
            )

            return False

        result = response.json()

        if not result.get(
            "ok",
            False
        ):

            print(
                "[WARN] Telegram API error:",
                response.text[:500]
            )

            return False

        print(
            "[INFO] Telegram message sent."
        )

        return True

    except Exception as e:

        print(
            f"[WARN] Telegram exception: {e}"
        )

        return False


def send_telegram_error(
    message
):

    try:

        send_telegram(
            "🚨 *SCANNER ERROR*\n\n"
            + str(message)
        )

    except Exception:
        pass


# ============================================================
# INSTRUMENTS
# ============================================================

def get_futures_instruments():

    print(
        "[INFO] Loading Kraken Futures instruments..."
    )

    url = (
        KRAKEN_API_BASE +
        "/instruments"
    )

    data = kraken_get(url)

    instruments = data.get(
        "instruments",
        []
    )

    if not instruments:

        raise RuntimeError(
            "Kraken returned zero futures instruments."
        )

    print(
        f"[INFO] Instruments: "
        f"{len(instruments)}"
    )

    return instruments


# ============================================================
# TICKERS
# ============================================================

def get_futures_tickers():

    print(
        "[INFO] Loading Kraken Futures tickers..."
    )

    url = (
        KRAKEN_API_BASE +
        "/tickers"
    )

    data = kraken_get(url)

    tickers = data.get(
        "tickers",
        []
    )

    if not tickers:

        raise RuntimeError(
            "Kraken returned zero futures tickers."
        )

    print(
        f"[INFO] Tickers: "
        f"{len(tickers)}"
    )

    return tickers


# ============================================================
# TOP 100
# ============================================================

def get_top_symbols():

    instruments = (
        get_futures_instruments()
    )

    tickers = (
        get_futures_tickers()
    )

    instrument_map = {}

    for item in instruments:

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).strip()

        if not symbol:
            continue

        instrument_map[
            symbol.upper()
        ] = item

    candidates = []

    for ticker in tickers:

        symbol = str(
            ticker.get(
                "symbol",
                ""
            )
        ).strip()

        if not symbol:
            continue

        upper_symbol = (
            symbol.upper()
        )

        instrument = (
            instrument_map.get(
                upper_symbol
            )
        )

        if not instrument:
            continue

        tag = str(
            ticker.get(
                "tag",
                ""
            )
        ).lower()

        instrument_type = str(
            instrument.get(
                "type",
                ""
            )
        ).lower()

        is_perpetual = (
            tag == "perpetual"
            or
            "perpetual"
            in instrument_type
        )

        if not is_perpetual:
            continue

        pair = str(
            ticker.get(
                "pair",
                ""
            )
        ).upper()

        underlying = str(
            instrument.get(
                "underlying",
                ""
            )
        ).upper()

        if (
            "USD" not in upper_symbol
            and
            "USD" not in pair
            and
            "USD" not in underlying
        ):
            continue

        volume_quote = safe_float(
            ticker.get(
                "volumeQuote",
                0
            )
        )

        if volume_quote <= 0:

            volume_quote = safe_float(
                ticker.get(
                    "vol24h",
                    0
                )
            )

        mark_price = safe_float(
            ticker.get(
                "markPrice",
                0
            )
        )

        last_price = safe_float(
            ticker.get(
                "last",
                0
            )
        )

        if mark_price <= 0:
            mark_price = last_price

        if mark_price <= 0:
            continue

        candidates.append({
            "symbol": symbol,
            "pair": pair,
            "volume_quote": volume_quote,
            "mark_price": mark_price,
            "ticker": ticker,
            "instrument": instrument
        })

    candidates.sort(
        key=lambda x:
            x["volume_quote"],
        reverse=True
    )

    top = candidates[
        :TOP_SYMBOLS
    ]

    print(
        f"[INFO] Eligible perpetuals: "
        f"{len(candidates)}"
    )

    print(
        f"[INFO] TOP {TOP_SYMBOLS}:"
    )

    for i, item in enumerate(
        top,
        start=1
    ):

        print(
            f"{i:03d}. "
            f"{item['symbol']} | "
            f"Vol: "
            f"{item['volume_quote']:,.0f} | "
            f"Price: "
            f"{fmt_price(item['mark_price'])}"
        )

    return top


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def normalize_candle(
    item
):

    try:

        ts = int(
            item.get("time")
        )

        if ts < 10_000_000_000:
            ts *= 1000

        return {
            "time": ts,
            "open": safe_float(
                item.get("open")
            ),
            "high": safe_float(
                item.get("high")
            ),
            "low": safe_float(
                item.get("low")
            ),
            "close": safe_float(
                item.get("close")
            ),
            "volume": safe_float(
                item.get("volume")
            )
        }

    except Exception:

        return None


# ============================================================
# CLOSED CANDLES
# ============================================================

def filter_closed_candles(
    candles,
    timeframe_minutes
):

    if not candles:
        return []

    now_ms = (
        utc_timestamp() *
        1000
    )

    timeframe_ms = (
        timeframe_minutes *
        60 *
        1000
    )

    closed = []

    for candle in candles:

        candle_end = (
            candle["time"] +
            timeframe_ms
        )

        if candle_end <= now_ms:

            closed.append(
                candle
            )

    return closed


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(
    symbol,
    timeframe
):

    if timeframe not in TIMEFRAMES:
        return []

    url = (
        KRAKEN_CHART_BASE +
        f"/trade/{symbol}/{timeframe}"
    )

    params = {
        "count": CANDLE_LIMIT
    }

    try:

        data = kraken_get(
            url,
            params=params
        )

        raw = data.get(
            "candles",
            []
        )

        candles = []

        for item in raw:

            candle = (
                normalize_candle(item)
            )

            if candle is None:
                continue

            if candle["close"] <= 0:
                continue

            if candle["high"] <= 0:
                continue

            if candle["low"] <= 0:
                continue

            candles.append(
                candle
            )

        candles.sort(
            key=lambda x:
                x["time"]
        )

        candles = (
            filter_closed_candles(
                candles,
                TIMEFRAMES[timeframe]
            )
        )

        return candles

    except Exception as e:

        print(
            f"[WARN] Candle error "
            f"{symbol} {timeframe}: {e}"
        )

        return []


# ============================================================
# FETCH ALL
# ============================================================

def fetch_all_candles(
    top_symbols
):

    result = {}

    jobs = []

    for item in top_symbols:

        symbol = item["symbol"]

        for timeframe in TIMEFRAMES:

            jobs.append(
                (
                    symbol,
                    timeframe
                )
            )

    print(
        f"[INFO] Downloading "
        f"{len(jobs)} candle sets..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for symbol, timeframe in jobs:

            future = executor.submit(
                fetch_candles,
                symbol,
                timeframe
            )

            futures[future] = (
                symbol,
                timeframe
            )

        for future in as_completed(
            futures
        ):

            symbol, timeframe = (
                futures[future]
            )

            try:

                candles = (
                    future.result()
                )

                if candles:

                    if symbol not in result:

                        result[symbol] = {}

                    result[symbol][
                        timeframe
                    ] = candles

            except Exception as e:

                print(
                    f"[WARN] "
                    f"{symbol} "
                    f"{timeframe}: "
                    f"{e}"
                )

    print(
        f"[INFO] Symbols with data: "
        f"{len(result)}"
    )

    return result


# ============================================================
# DONCHIAN
# ============================================================

def donchian(
    candles,
    index,
    period
):

    start = (
        index -
        period +
        1
    )

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    if len(window) != period:
        return None

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest +
        lowest
    ) / 2.0


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku_at(
    candles,
    index
):

    if index < (
        SENKOU_B_PERIOD - 1
    ):

        return None

    tenkan = donchian(
        candles,
        index,
        TENKAN_PERIOD
    )

    kijun = donchian(
        candles,
        index,
        KIJUN_PERIOD
    )

    senkou_b = donchian(
        candles,
        index,
        SENKOU_B_PERIOD
    )

    if (
        tenkan is None
        or kijun is None
        or senkou_b is None
    ):

        return None

    senkou_a = (
        tenkan +
        kijun
    ) / 2.0

    cloud_top = max(
        senkou_a,
        senkou_b
    )

    cloud_bottom = min(
        senkou_a,
        senkou_b
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "cloud_top": cloud_top,
        "cloud_bottom":
            cloud_bottom,
        "cloud_width":
            cloud_top -
            cloud_bottom
    }


# ============================================================
# ATR
# ============================================================

def atr(
    candles,
    index,
    period=ATR_PERIOD
):

    if index < period:
        return None

    trs = []

    start = (
        index -
        period +
        1
    )

    for i in range(
        start,
        index + 1
    ):

        current = candles[i]

        previous_close = (
            candles[i - 1]["close"]
            if i > 0
            else current["close"]
        )

        tr = max(
            current["high"] -
            current["low"],

            abs(
                current["high"] -
                previous_close
            ),

            abs(
                current["low"] -
                previous_close
            )
        )

        trs.append(tr)

    if len(trs) != period:
        return None

    return (
        sum(trs) /
        len(trs)
    )


# ============================================================
# MARKET STATE
# ============================================================

def get_market_state(
    candles,
    index=None
):

    if not candles:
        return None

    if index is None:

        index = (
            len(candles) - 1
        )

    if index < (
        SENKOU_B_PERIOD + 2
    ):

        return None

    current = candles[index]

    ichi = ichimoku_at(
        candles,
        index
    )

    previous_ichi = ichimoku_at(
        candles,
        index - 1
    )

    if (
        ichi is None
        or previous_ichi is None
    ):

        return None

    current_close = (
        current["close"]
    )

    previous_close = (
        candles[index - 1]["close"]
    )

    current_atr = atr(
        candles,
        index
    )

    if current_atr is None:
        return None

    # --------------------------------------------------------
    # Slopes
    # --------------------------------------------------------

    kijun_change = (
        ichi["kijun"] -
        previous_ichi["kijun"]
    )

    kijun_slope_pct = (
        kijun_change /
        ichi["kijun"]
    ) * 100

    tenkan_change = (
        ichi["tenkan"] -
        previous_ichi["tenkan"]
    )

    tenkan_slope_pct = (
        tenkan_change /
        ichi["tenkan"]
    ) * 100

    # --------------------------------------------------------
    # Distances
    # --------------------------------------------------------

    distance_tenkan = (
        abs(
            current_close -
            ichi["tenkan"]
        ) /
        current_atr
    )

    distance_kijun = (
        abs(
            current_close -
            ichi["kijun"]
        ) /
        current_atr
    )

    # --------------------------------------------------------
    # Chikou
    # --------------------------------------------------------

    chikou_bullish = False
    chikou_bearish = False

    chikou_index = (
        index -
        DISPLACEMENT
    )

    if chikou_index >= 0:

        chikou_close = (
            candles[
                chikou_index
            ]["close"]
        )

        if current_close > chikou_close:

            chikou_bullish = True

        elif current_close < chikou_close:

            chikou_bearish = True

    # --------------------------------------------------------
    # Cloud
    # --------------------------------------------------------

    above_cloud = (
        current_close >
        ichi["cloud_top"]
    )

    below_cloud = (
        current_close <
        ichi["cloud_bottom"]
    )

    inside_cloud = (
        not above_cloud
        and
        not below_cloud
    )

    # --------------------------------------------------------
    # Future cloud
    # --------------------------------------------------------

    future_kumo_bullish = (
        ichi["senkou_a"] >
        ichi["senkou_b"]
    )

    future_kumo_bearish = (
        ichi["senkou_a"] <
        ichi["senkou_b"]
    )

    # --------------------------------------------------------
    # Tenkan / Kijun
    # --------------------------------------------------------

    tenkan_above_kijun = (
        ichi["tenkan"] >
        ichi["kijun"]
    )

    tenkan_below_kijun = (
        ichi["tenkan"] <
        ichi["kijun"]
    )

    # --------------------------------------------------------
    # 5m trigger
    # --------------------------------------------------------

    bullish_tenkan_cross = (
        previous_close <=
        previous_ichi["tenkan"]
        and
        current_close >
        ichi["tenkan"]
    )

    bearish_tenkan_cross = (
        previous_close >=
        previous_ichi["tenkan"]
        and
        current_close <
        ichi["tenkan"]
    )

    return {
        "close": current_close,
        "atr": current_atr,

        "tenkan": ichi["tenkan"],
        "kijun": ichi["kijun"],

        "senkou_a":
            ichi["senkou_a"],

        "senkou_b":
            ichi["senkou_b"],

        "cloud_top":
            ichi["cloud_top"],

        "cloud_bottom":
            ichi["cloud_bottom"],

        "cloud_width":
            ichi["cloud_width"],

        "above_cloud":
            above_cloud,

        "below_cloud":
            below_cloud,

        "inside_cloud":
            inside_cloud,

        "future_kumo_bullish":
            future_kumo_bullish,

        "future_kumo_bearish":
            future_kumo_bearish,

        "tenkan_above_kijun":
            tenkan_above_kijun,

        "tenkan_below_kijun":
            tenkan_below_kijun,

        "kijun_slope_pct":
            kijun_slope_pct,

        "tenkan_slope_pct":
            tenkan_slope_pct,

        "distance_tenkan_atr":
            distance_tenkan,

        "distance_kijun_atr":
            distance_kijun,

        "chikou_bullish":
            chikou_bullish,

        "chikou_bearish":
            chikou_bearish,

        "bullish_tenkan_cross":
            bullish_tenkan_cross,

        "bearish_tenkan_cross":
            bearish_tenkan_cross
    }


# ============================================================
# SCORE 1H
# ============================================================

def score_1h(
    state
):

    bull = 0.0
    bear = 0.0

    if state["above_cloud"]:
        bull += 2.0

    elif state["below_cloud"]:
        bear += 2.0

    if state["tenkan_above_kijun"]:
        bull += 2.0

    elif state["tenkan_below_kijun"]:
        bear += 2.0

    if state["kijun_slope_pct"] > 0:
        bull += 1.0

    elif state["kijun_slope_pct"] < 0:
        bear += 1.0

    if state["future_kumo_bullish"]:
        bull += 2.0

    elif state["future_kumo_bearish"]:
        bear += 2.0

    if state["chikou_bullish"]:
        bull += 1.0

    elif state["chikou_bearish"]:
        bear += 1.0

    if (
        state["distance_tenkan_atr"]
        <= 1.5
    ):

        if state["tenkan_above_kijun"]:
            bull += 2.0

        elif state["tenkan_below_kijun"]:
            bear += 2.0

    return {
        "bull": min(
            bull,
            10.0
        ),
        "bear": min(
            bear,
            10.0
        )
    }


# ============================================================
# SCORE 30M
# ============================================================

def score_30m(
    state
):

    bull = 0.0
    bear = 0.0

    if state["above_cloud"]:
        bull += 3.0

    elif state["below_cloud"]:
        bear += 3.0

    if state["tenkan_above_kijun"]:
        bull += 2.0

    elif state["tenkan_below_kijun"]:
        bear += 2.0

    if state["kijun_slope_pct"] > 0:
        bull += 2.0

    elif state["kijun_slope_pct"] < 0:
        bear += 2.0

    if state["future_kumo_bullish"]:
        bull += 2.0

    elif state["future_kumo_bearish"]:
        bear += 2.0

    # Only ONE distance point.
    # Previous version duplicated trend points here.
    if (
        state["distance_tenkan_atr"]
        <= 1.5
    ):

        if state["tenkan_above_kijun"]:
            bull += 1.0

        elif state["tenkan_below_kijun"]:
            bear += 1.0

    return {
        "bull": min(
            bull,
            10.0
        ),
        "bear": min(
            bear,
            10.0
        )
    }


# ============================================================
# SCORE 15M
# ============================================================

def score_15m(
    state
):

    bull = 0.0
    bear = 0.0

    if state["above_cloud"]:
        bull += 2.0

    elif state["below_cloud"]:
        bear += 2.0

    if state["tenkan_above_kijun"]:
        bull += 2.0

    elif state["tenkan_below_kijun"]:
        bear += 2.0

    if state["kijun_slope_pct"] > 0:
        bull += 1.0

    elif state["kijun_slope_pct"] < 0:
        bear += 1.0

    distance = min(
        state["distance_tenkan_atr"],
        state["distance_kijun_atr"]
    )

    # Pullback quality
    if distance <= 0.75:

        if state["tenkan_above_kijun"]:
            bull += 3.0

        elif state["tenkan_below_kijun"]:
            bear += 3.0

    elif distance <= 1.50:

        if state["tenkan_above_kijun"]:
            bull += 2.0

        elif state["tenkan_below_kijun"]:
            bear += 2.0

    # Distance quality
    if distance <= 1.50:

        if state["tenkan_above_kijun"]:
            bull += 2.0

        elif state["tenkan_below_kijun"]:
            bear += 2.0

    return {
        "bull": min(
            bull,
            10.0
        ),
        "bear": min(
            bear,
            10.0
        )
    }


# ============================================================
# SWINGS
# ============================================================

def is_swing_low(
    candles,
    index,
    left=2,
    right=2
):

    if index < left:
        return False

    if index + right >= len(candles):
        return False

    value = candles[
        index
    ]["low"]

    for i in range(
        index - left,
        index
    ):

        if value >= candles[
            i
        ]["low"]:

            return False

    for i in range(
        index + 1,
        index + 1 + right
    ):

        if value >= candles[
            i
        ]["low"]:

            return False

    return True


def is_swing_high(
    candles,
    index,
    left=2,
    right=2
):

    if index < left:
        return False

    if index + right >= len(candles):
        return False

    value = candles[
        index
    ]["high"]

    for i in range(
        index - left,
        index
    ):

        if value <= candles[
            i
        ]["high"]:

            return False

    for i in range(
        index + 1,
        index + 1 + right
    ):

        if value <= candles[
            i
        ]["high"]:

            return False

    return True


# ============================================================
# LATEST CONFIRMED SWING
# ============================================================

def latest_swing(
    candles,
    direction,
    before_index
):

    if before_index <= 2:
        return None

    max_index = min(
        before_index - 1,
        len(candles) - 3
    )

    if direction == "BUY":

        for i in range(
            max_index,
            1,
            -1
        ):

            if is_swing_low(
                candles,
                i,
                2,
                2
            ):

                return {
                    "index": i,
                    "price":
                        candles[i]["low"],
                    "time":
                        candles[i]["time"],
                    "type":
                        "SWING_LOW"
                }

    else:

        for i in range(
            max_index,
            1,
            -1
        ):

            if is_swing_high(
                candles,
                i,
                2,
                2
            ):

                return {
                    "index": i,
                    "price":
                        candles[i]["high"],
                    "time":
                        candles[i]["time"],
                    "type":
                        "SWING_HIGH"
                }

    return None


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    symbol_data
):

    required = [
        "1h",
        "30m",
        "15m",
        "5m"
    ]

    for tf in required:

        if tf not in symbol_data:
            return None

        if len(
            symbol_data[tf]
        ) < 70:

            return None

    candles_1h = symbol_data["1h"]
    candles_30m = symbol_data["30m"]
    candles_15m = symbol_data["15m"]
    candles_5m = symbol_data["5m"]

    s1 = get_market_state(
        candles_1h
    )

    s30 = get_market_state(
        candles_30m
    )

    s15 = get_market_state(
        candles_15m
    )

    s5 = get_market_state(
        candles_5m
    )

    if not s1 or not s30 or not s15 or not s5:
        return None

    sc1 = score_1h(s1)
    sc30 = score_30m(s30)
    sc15 = score_15m(s15)

    # ========================================================
    # LONG BASE
    # ========================================================

    long_1h = (
        sc1["bull"] >= MIN_1H_SCORE
        and
        s1["above_cloud"]
        and
        s1["tenkan_above_kijun"]
        and
        s1["kijun_slope_pct"] > 0
        and
        s1["future_kumo_bullish"]
    )

    long_30m = (
        sc30["bull"] >= MIN_30M_SCORE
        and
        s30["above_cloud"]
        and
        s30["tenkan_above_kijun"]
        and
        s30["kijun_slope_pct"] > 0
    )

    long_15m = (
        sc15["bull"] >= MIN_15M_SCORE
        and
        s15["above_cloud"]
        and
        s15["tenkan_above_kijun"]
        and
        s15["kijun_slope_pct"] >= 0
        and
        min(
            s15["distance_tenkan_atr"],
            s15["distance_kijun_atr"]
        )
        <= MAX_PULLBACK_DISTANCE_ATR
    )

    long_5m = (
        s5["bullish_tenkan_cross"]
        and
        s5["tenkan_above_kijun"]
    )

    # ========================================================
    # SHORT BASE
    # ========================================================

    short_1h = (
        sc1["bear"] >= MIN_1H_SCORE
        and
        s1["below_cloud"]
        and
        s1["tenkan_below_kijun"]
        and
        s1["kijun_slope_pct"] < 0
        and
        s1["future_kumo_bearish"]
    )

    short_30m = (
        sc30["bear"] >= MIN_30M_SCORE
        and
        s30["below_cloud"]
        and
        s30["tenkan_below_kijun"]
        and
        s30["kijun_slope_pct"] < 0
    )

    short_15m = (
        sc15["bear"] >= MIN_15M_SCORE
        and
        s15["below_cloud"]
        and
        s15["tenkan_below_kijun"]
        and
        s15["kijun_slope_pct"] <= 0
        and
        min(
            s15["distance_tenkan_atr"],
            s15["distance_kijun_atr"]
        )
        <= MAX_PULLBACK_DISTANCE_ATR
    )

    short_5m = (
        s5["bearish_tenkan_cross"]
        and
        s5["tenkan_below_kijun"]
    )

    # ========================================================
    # DETERMINE SIGNAL TYPE
    # ========================================================

    direction = None
    signal_type = None

    # --------------------------------------------------------
    # STRONG LONG
    # --------------------------------------------------------

    if (
        long_1h
        and
        long_30m
        and
        long_15m
        and
        long_5m
    ):

        direction = "BUY"
        signal_type = "STRONG"

    # --------------------------------------------------------
    # STRONG SHORT
    # --------------------------------------------------------

    elif (
        short_1h
        and
        short_30m
        and
        short_15m
        and
        short_5m
    ):

        direction = "SELL"
        signal_type = "STRONG"

    # --------------------------------------------------------
    # EARLY LONG
    # --------------------------------------------------------

    elif (
        sc1["bull"] >= MIN_EARLY_1H_SCORE
        and
        sc30["bull"] >= MIN_EARLY_30M_SCORE
        and
        long_1h
        and
        long_30m
        and
        long_5m
    ):

        direction = "BUY"
        signal_type = "EARLY"

    # --------------------------------------------------------
    # EARLY SHORT
    # --------------------------------------------------------

    elif (
        sc1["bear"] >= MIN_EARLY_1H_SCORE
        and
        sc30["bear"] >= MIN_EARLY_30M_SCORE
        and
        short_1h
        and
        short_30m
        and
        short_5m
    ):

        direction = "SELL"
        signal_type = "EARLY"

    else:

        return None

    # ========================================================
    # ENTRY
    # ========================================================

    entry = s5["close"]

    # ========================================================
    # SWING
    # ========================================================

    swing = latest_swing(
        candles_5m,
        direction,
        len(candles_5m) - 1
    )

    if not swing:
        return None

    # ========================================================
    # SL
    # ========================================================

    if direction == "BUY":

        sl = (
            swing["price"] *
            (
                1 -
                SL_BUFFER_PERCENT / 100
            )
        )

        if sl >= entry:
            return None

    else:

        sl = (
            swing["price"] *
            (
                1 +
                SL_BUFFER_PERCENT / 100
            )
        )

        if sl <= entry:
            return None

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        return None

    # ========================================================
    # TP
    # ========================================================

    if direction == "BUY":

        tp = (
            entry +
            risk * TARGET_RR
        )

    else:

        tp = (
            entry -
            risk * TARGET_RR
        )

    reward = abs(
        tp - entry
    )

    rr = (
        reward / risk
        if risk > 0
        else 0
    )

    # ========================================================
    # COMBINED SCORE
    # ========================================================

    combined_score = (
        sc1["bull"] * 0.50
        +
        sc30["bull"] * 0.30
        +
        sc15["bull"] * 0.20
        if direction == "BUY"
        else
        sc1["bear"] * 0.50
        +
        sc30["bear"] * 0.30
        +
        sc15["bear"] * 0.20
    )

    # ========================================================
    # EARLY PENALTY
    # ========================================================

    # Strong signals remain above Early signals
    # when scores are otherwise similar.
    ranking_score = (
        combined_score
        +
        (
            0.50
            if signal_type == "STRONG"
            else 0.0
        )
    )

    # ========================================================
    # FRESHNESS
    # ========================================================

    signal_time = candles_5m[-1]["time"]

    now_ms = (
        utc_timestamp() *
        1000
    )

    candle_age_minutes = max(
        0,
        (
            now_ms -
            signal_time
        ) / 60000
    )

    max_age = (
        TIMEFRAMES["5m"]
        *
        (
            MAX_SIGNAL_AGE_CANDLES + 1
        )
    )

    if candle_age_minutes > max_age:
        return None

    # ========================================================
    # SIGNAL KEY
    # ========================================================

    signal_key = (
        f"{symbol}|"
        f"{direction}|"
        f"{signal_type}|"
        f"{signal_time}"
    )

    # ========================================================
    # RETURN
    # ========================================================

    return {
        "symbol": symbol,

        "direction": direction,

        "signal_type": signal_type,

        "entry": entry,

        "sl": sl,

        "tp": tp,

        "risk": risk,

        "reward": reward,

        "rr": rr,

        "score_1h":
            sc1["bull"]
            if direction == "BUY"
            else sc1["bear"],

        "score_30m":
            sc30["bull"]
            if direction == "BUY"
            else sc30["bear"],

        "score_15m":
            sc15["bull"]
            if direction == "BUY"
            else sc15["bear"],

        "combined_score":
            combined_score,

        "ranking_score":
            ranking_score,

        "atr_5m":
            s5["atr"],

        "distance_tenkan_atr":
            s15["distance_tenkan_atr"],

        "distance_kijun_atr":
            s15["distance_kijun_atr"],

        "tenkan_5m":
            s5["tenkan"],

        "kijun_5m":
            s5["kijun"],

        "tenkan_15m":
            s15["tenkan"],

        "kijun_15m":
            s15["kijun"],

        "cloud_top_15m":
            s15["cloud_top"],

        "cloud_bottom_15m":
            s15["cloud_bottom"],

        "future_kumo":
            (
                "BULLISH"
                if direction == "BUY"
                else "BEARISH"
            ),

        "swing_type":
            swing["type"],

        "swing_price":
            swing["price"],

        "swing_time":
            swing["time"],

        "signal_time":
            signal_time,

        "signal_age_minutes":
            candle_age_minutes,

        "signal_key":
            signal_key
    }


# ============================================================
# OPEN TRADE
# ============================================================

def get_open_trade():

    with STATE_LOCK:

        trade = STATE.get(
            "open_trade"
        )

        if isinstance(
            trade,
            dict
        ):

            return dict(trade)

    return None


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    reason,
    candle_time
):

    direction = trade[
        "direction"
    ]

    entry = safe_float(
        trade["entry"]
    )

    exit_price = safe_float(
        exit_price
    )

    if direction == "BUY":

        pnl_pct = (
            (
                exit_price -
                entry
            ) /
            entry
        ) * 100

    else:

        pnl_pct = (
            (
                entry -
                exit_price
            ) /
            entry
        ) * 100

    risk = safe_float(
        trade.get(
            "risk",
            0
        )
    )

    if risk > 0:

        if direction == "BUY":

            r_multiple = (
                exit_price -
                entry
            ) / risk

        else:

            r_multiple = (
                entry -
                exit_price
            ) / risk

    else:

        r_multiple = 0.0

    if r_multiple > 0:

        result = "WIN"

    elif r_multiple < 0:

        result = "LOSS"

    else:

        result = "BE"

    completed = dict(
        trade
    )

    completed.update({

        "exit":
            exit_price,

        "exit_reason":
            reason,

        "exit_time":
            candle_time,

        "pnl_pct":
            pnl_pct,

        "r_multiple":
            r_multiple,

        "result":
            result
    })

    HISTORY.setdefault(
        "trades",
        []
    ).append(
        completed
    )

    STATE[
        "open_trade"
    ] = None

    save_json(
        STATE_FILE,
        STATE
    )

    save_json(
        HISTORY_FILE,
        HISTORY
    )

    print(
        "[TRADE CLOSED] "
        f"{trade['symbol']} "
        f"{direction} "
        f"{reason} "
        f"{pnl_pct:+.2f}% "
        f"{r_multiple:+.2f}R"
    )

    return completed


# ============================================================
# UPDATE OPEN TRADE
# ============================================================

def update_open_trade(
    candles_by_symbol
):

    trade = get_open_trade()

    if not trade:
        return None

    symbol = trade[
        "symbol"
    ]

    symbol_data = (
        candles_by_symbol.get(
            symbol,
            {}
        )
    )

    candles = symbol_data.get(
        "5m",
        []
    )

    if not candles:

        print(
            f"[WARN] No 5m data for "
            f"open trade {symbol}"
        )

        return None

    direction = trade[
        "direction"
    ]

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    opened_at = safe_float(
        trade.get(
            "opened_at",
            utc_timestamp()
        )
    )

    last_checked = trade.get(
        "last_checked_candle"
    )

    max_hold_seconds = (
        MAX_HOLDING_HOURS *
        3600
    )

    for candle in candles:

        if (
            last_checked is not None
            and
            candle["time"] <=
            last_checked
        ):

            continue

        high = candle["high"]
        low = candle["low"]

        exit_price = None
        reason = None

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if direction == "BUY":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            if hit_sl:

                exit_price = sl
                reason = "SL"

            elif hit_tp:

                exit_price = tp
                reason = "TP"

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            if hit_sl:

                exit_price = sl
                reason = "SL"

            elif hit_tp:

                exit_price = tp
                reason = "TP"

        trade[
            "last_checked_candle"
        ] = candle["time"]

        if exit_price is not None:

            return close_trade(
                trade,
                exit_price,
                reason,
                candle["time"]
            )

        candle_seconds = (
            candle["time"] /
            1000
        )

        if (
            candle_seconds -
            opened_at
            >=
            max_hold_seconds
        ):

            return close_trade(
                trade,
                candle["close"],
                "TIMEOUT",
                candle["time"]
            )

    STATE[
        "open_trade"
    ] = trade

    save_json(
        STATE_FILE,
        STATE
    )

    return None


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    trades = HISTORY.get(
        "trades",
        []
    )

    total = len(
        trades
    )

    wins = sum(
        1
        for t in trades
        if t.get("result") == "WIN"
    )

    losses = sum(
        1
        for t in trades
        if t.get("result") == "LOSS"
    )

    be = sum(
        1
        for t in trades
        if t.get("result") == "BE"
    )

    win_values = [
        safe_float(
            t.get("pnl_pct", 0)
        )
        for t in trades
        if t.get("result") == "WIN"
    ]

    loss_values = [
        abs(
            safe_float(
                t.get("pnl_pct", 0)
            )
        )
        for t in trades
        if t.get("result") == "LOSS"
    ]

    avg_win = (
        sum(win_values) /
        len(win_values)
        if win_values
        else 0.0
    )

    avg_loss = (
        sum(loss_values) /
        len(loss_values)
        if loss_values
        else 0.0
    )

    gross_profit = sum(
        win_values
    )

    gross_loss = sum(
        loss_values
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit /
            gross_loss
        )

    else:

        profit_factor = (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    net_pct = sum(
        safe_float(
            t.get("pnl_pct", 0)
        )
        for t in trades
    )

    net_r = sum(
        safe_float(
            t.get("r_multiple", 0)
        )
        for t in trades
    )

    current_streak = 0
    max_losing_streak = 0

    for trade in trades:

        if trade.get(
            "result"
        ) == "LOSS":

            current_streak += 1

            max_losing_streak = max(
                max_losing_streak,
                current_streak
            )

        else:

            current_streak = 0

    win_rate = (
        wins /
        total *
        100
        if total > 0
        else 0.0
    )

    return {
        "total":
            total,

        "wins":
            wins,

        "losses":
            losses,

        "be":
            be,

        "win_rate":
            win_rate,

        "avg_win":
            avg_win,

        "avg_loss":
            avg_loss,

        "profit_factor":
            profit_factor,

        "net_pct":
            net_pct,

        "net_r":
            net_r,

        "max_losing_streak":
            max_losing_streak
    }


# ============================================================
# LIVE PNL
# ============================================================

def live_pnl(
    trade,
    current_price
):

    entry = safe_float(
        trade["entry"]
    )

    current = safe_float(
        current_price
    )

    if entry <= 0:
        return 0.0

    if trade[
        "direction"
    ] == "BUY":

        return (
            (
                current -
                entry
            ) /
            entry
        ) * 100

    return (
        (
            entry -
            current
        ) /
        entry
    ) * 100


# ============================================================
# CURRENT PRICES
# ============================================================

def get_current_prices(
    top_symbols,
    all_tickers=None
):

    prices = {}

    if all_tickers is None:

        all_tickers = (
            get_futures_tickers()
        )

    for ticker in all_tickers:

        symbol = str(
            ticker.get(
                "symbol",
                ""
            )
        ).strip()

        if not symbol:
            continue

        mark = safe_float(
            ticker.get(
                "markPrice",
                0
            )
        )

        if mark <= 0:

            mark = safe_float(
                ticker.get(
                    "last",
                    0
                )
            )

        if mark > 0:

            prices[
                symbol
            ] = mark

    return prices


# ============================================================
# BUILD TELEGRAM REPORT
# ============================================================

def build_report(
    top_symbols,
    selected_signal,
    current_prices,
    closed_trade=None
):

    perf = performance()

    open_trade = get_open_trade()

    now = utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    lines = []

    lines.append(
        "📡 *KRAKEN FUTURES ICHIMOKU REPORT*"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        f"⏱ *5m CLOSED | TOP {TOP_SYMBOLS}*"
    )

    lines.append(
        "🤖 *ICHIMOKU MTF*"
    )

    lines.append(
        "1H → Trend | 30m → Confirm | "
        "15m → Pullback | 5m → Trigger"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        f"Trades: `{perf['total']}`"
    )

    lines.append(
        f"🟢 Wins: `{perf['wins']}`"
    )

    lines.append(
        f"🔴 Losses: `{perf['losses']}`"
    )

    lines.append(
        f"⚪ BE: `{perf['be']}`"
    )

    lines.append(
        f"🏆 Win Rate: "
        f"`{perf['win_rate']:.2f}%`"
    )

    lines.append(
        f"📈 Net P&L: "
        f"`{perf['net_pct']:+.2f}%`"
    )

    lines.append(
        f"💰 Net R: "
        f"`{perf['net_r']:+.2f}R`"
    )

    lines.append(
        f"📊 Avg Win: "
        f"`{perf['avg_win']:+.2f}%`"
    )

    lines.append(
        f"📉 Avg Loss: "
        f"`-{perf['avg_loss']:.2f}%`"
    )

    if math.isinf(
        perf["profit_factor"]
    ):

        pf_text = "∞"

    else:

        pf_text = (
            f"{perf['profit_factor']:.2f}"
        )

    lines.append(
        f"⚖️ Profit Factor: "
        f"`{pf_text}`"
    )

    lines.append(
        f"🔥 Max Losing Streak: "
        f"`{perf['max_losing_streak']}`"
    )

    # ========================================================
    # CLOSED TRADE
    # ========================================================

    if closed_trade:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        result = closed_trade.get(
            "result",
            "BE"
        )

        emoji = (
            "🟢"
            if result == "WIN"
            else
            "🔴"
            if result == "LOSS"
            else
            "⚪"
        )

        lines.append(
            f"{emoji} *TRADE CLOSED*"
        )

        lines.append(
            f"{closed_trade['symbol']} "
            f"{closed_trade['direction']}"
        )

        lines.append(
            f"Type: "
            f"`{closed_trade.get('signal_type', '-')}`"
        )

        lines.append(
            f"Entry: "
            f"`{fmt_price(closed_trade['entry'])}`"
        )

        lines.append(
            f"Exit: "
            f"`{fmt_price(closed_trade['exit'])}`"
        )

        lines.append(
            f"Reason: "
            f"`{closed_trade['exit_reason']}`"
        )

        lines.append(
            f"Result: "
            f"`{result}`"
        )

        lines.append(
            f"P&L: "
            f"`{closed_trade['pnl_pct']:+.2f}%`"
        )

        lines.append(
            f"R: "
            f"`{closed_trade['r_multiple']:+.2f}R`"
        )

    # ========================================================
    # OPEN TRADE
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if open_trade:

        symbol = open_trade[
            "symbol"
        ]

        current_price = safe_float(
            current_prices.get(
                symbol,
                open_trade["entry"]
            )
        )

        pnl = live_pnl(
            open_trade,
            current_price
        )

        emoji = (
            "🟢"
            if pnl >= 0
            else
            "🔴"
        )

        entry = safe_float(
            open_trade["entry"]
        )

        sl = safe_float(
            open_trade["sl"]
        )

        tp = safe_float(
            open_trade["tp"]
        )

        if open_trade[
            "direction"
        ] == "BUY":

            tp_distance = (
                (
                    tp -
                    current_price
                ) /
                current_price
            ) * 100

            sl_distance = (
                (
                    current_price -
                    sl
                ) /
                current_price
            ) * 100

        else:

            tp_distance = (
                (
                    current_price -
                    tp
                ) /
                current_price
            ) * 100

            sl_distance = (
                (
                    sl -
                    current_price
                ) /
                current_price
            ) * 100

        age_hours = (
            utc_timestamp() -
            safe_float(
                open_trade.get(
                    "opened_at",
                    utc_timestamp()
                )
            )
        ) / 3600

        lines.append(
            "📌 *OPEN TRADE*"
        )

        lines.append(
            f"{emoji} *{symbol}* "
            f"*{open_trade['direction']}*"
        )

        lines.append(
            f"Type: "
            f"`{open_trade.get('signal_type', '-')}`"
        )

        lines.append(
            f"Signal: "
            f"`{fmt_price(entry)}`"
        )

        lines.append(
            f"Current: "
            f"`{fmt_price(current_price)}`"
        )

        lines.append(
            f"Live P&L: "
            f"`{pnl:+.2f}%`"
        )

        lines.append(
            f"🎯 TP: "
            f"`{fmt_price(tp)}` "
            f"({tp_distance:+.2f}% from current)"
        )

        lines.append(
            f"🛑 SL: "
            f"`{fmt_price(sl)}` "
            f"({sl_distance:+.2f}% from current)"
        )

        lines.append(
            f"RR: "
            f"`1:{safe_float(open_trade.get('rr', TARGET_RR)):.2f}`"
        )

        lines.append(
            f"Age: "
            f"`{age_hours:.1f}h / {MAX_HOLDING_HOURS}h`"
        )

        lines.append(
            f"1H Score: "
            f"`{open_trade.get('score_1h', 0):.1f}/10`"
        )

        lines.append(
            f"30m Score: "
            f"`{open_trade.get('score_30m', 0):.1f}/10`"
        )

        lines.append(
            f"15m Score: "
            f"`{open_trade.get('score_15m', 0):.1f}/10`"
        )

        lines.append(
            f"Combined: "
            f"`{open_trade.get('combined_score', 0):.2f}/10`"
        )

    else:

        lines.append(
            "📌 *OPEN TRADE*"
        )

        lines.append(
            "None"
        )

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if selected_signal:

        signal = selected_signal

        emoji = (
            "🟢"
            if signal["direction"] == "BUY"
            else
            "🔴"
        )

        lines.append(
            "🚨 *NEW SIGNAL*"
        )

        lines.append(
            f"{emoji} *{signal['symbol']}* "
            f"*{signal['direction']}*"
        )

        lines.append(
            f"Signal Type: "
            f"`{signal['signal_type']}`"
        )

        lines.append(
            f"Signal Price: "
            f"`{fmt_price(signal['entry'])}`"
        )

        lines.append(
            f"Current Price: "
            f"`{fmt_price(current_prices.get(signal['symbol'], signal['entry']))}`"
        )

        lines.append(
            f"🎯 TP: "
            f"`{fmt_price(signal['tp'])}`"
        )

        lines.append(
            f"🛑 SL: "
            f"`{fmt_price(signal['sl'])}`"
        )

        tp_pct = (
            abs(
                signal["tp"] -
                signal["entry"]
            )
            /
            signal["entry"]
            *
            100
        )

        sl_pct = (
            abs(
                signal["sl"] -
                signal["entry"]
            )
            /
            signal["entry"]
            *
            100
        )

        lines.append(
            f"TP %: "
            f"`{tp_pct:.2f}%`"
        )

        lines.append(
            f"SL %: "
            f"`{sl_pct:.2f}%`"
        )

        lines.append(
            f"RR: "
            f"`1:{signal['rr']:.2f}`"
        )

        lines.append(
            f"1H: "
            f"`{signal['score_1h']:.1f}/10`"
        )

        lines.append(
            f"30m: "
            f"`{signal['score_30m']:.1f}/10`"
        )

        lines.append(
            f"15m: "
            f"`{signal['score_15m']:.1f}/10`"
        )

        lines.append(
            f"🏆 Combined Score: "
            f"`{signal['combined_score']:.2f}/10`"
        )

        lines.append(
            f"ATR 5m: "
            f"`{fmt_price(signal['atr_5m'])}`"
        )

        lines.append(
            f"Distance Tenkan: "
            f"`{signal['distance_tenkan_atr']:.2f} ATR`"
        )

        lines.append(
            f"Distance Kijun: "
            f"`{signal['distance_kijun_atr']:.2f} ATR`"
        )

        lines.append(
            f"Swing: "
            f"`{signal['swing_type']}` "
            f"`{fmt_price(signal['swing_price'])}`"
        )

    else:

        lines.append(
            "🔎 *NEW SIGNAL*"
        )

        if open_trade:

            lines.append(
                "⛔ Signal locked: "
                "one trade is already open."
            )

        else:

            lines.append(
                "No valid MTF setup."
            )

    # ========================================================
    # SUMMARY
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📡 Scanned: "
        f"`{len(top_symbols)}`"
    )

    lines.append(
        f"🎯 New Signal: "
        f"`{1 if selected_signal else 0}`"
    )

    lines.append(
        f"🔒 Max Open: "
        f"`{MAX_OPEN_TRADES}`"
    )

    lines.append(
        "⚠️ Virtual signal simulator only"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 75)

    print(
        "KRAKEN FUTURES ICHIMOKU MTF SCANNER v2"
    )

    print("=" * 75)

    print(
        f"TOP SYMBOLS: {TOP_SYMBOLS}"
    )

    print(
        "1H  = Trend"
    )

    print(
        "30m = Confirmation"
    )

    print(
        "15m = Pullback / Strong Filter"
    )

    print(
        "5m  = Entry Trigger"
    )

    print(
        "STRONG = Full MTF confirmation"
    )

    print(
        "EARLY  = 1H + 30m + 5m"
    )

    print(
        f"RR = 1:{TARGET_RR}"
    )

    print(
        f"Max Holding = "
        f"{MAX_HOLDING_HOURS}h"
    )

    print(
        f"Max Open Trades = "
        f"{MAX_OPEN_TRADES}"
    )

    print("=" * 75)

    # ========================================================
    # TOP 100
    # ========================================================

    top_symbols = (
        get_top_symbols()
    )

    if not top_symbols:

        raise RuntimeError(
            "No eligible Kraken perpetuals found."
        )

    # ========================================================
    # OPEN TRADE SYMBOL
    # ========================================================

    existing_trade = (
        get_open_trade()
    )

    if existing_trade:

        open_symbol = (
            existing_trade[
                "symbol"
            ]
        )

        known_symbols = {
            x["symbol"]
            for x in top_symbols
        }

        if open_symbol not in known_symbols:

            print(
                "[INFO] Open trade symbol "
                "not in current TOP 100. "
                "Adding it temporarily: "
                f"{open_symbol}"
            )

            top_symbols.append({
                "symbol":
                    open_symbol,
                "pair":
                    "",
                "volume_quote":
                    0,
                "mark_price":
                    0,
                "ticker":
                    {},
                "instrument":
                    {}
            })

    # ========================================================
    # CANDLES
    # ========================================================

    candles_by_symbol = (
        fetch_all_candles(
            top_symbols
        )
    )

    if not candles_by_symbol:

        raise RuntimeError(
            "No candle data received."
        )

    # ========================================================
    # PRICES
    # ========================================================

    all_tickers = (
        get_futures_tickers()
    )

    current_prices = (
        get_current_prices(
            top_symbols,
            all_tickers
        )
    )

    # ========================================================
    # MANAGE OPEN TRADE
    # ========================================================

    closed_trade = (
        update_open_trade(
            candles_by_symbol
        )
    )

    if closed_trade:

        print(
            "[INFO] Open trade closed:"
        )

        print(
            json.dumps(
                closed_trade,
                ensure_ascii=False,
                indent=2
            )
        )

    # ========================================================
    # CHECK OPEN
    # ========================================================

    open_trade = (
        get_open_trade()
    )

    selected_signal = None

    # ========================================================
    # SCAN
    # ========================================================

    if open_trade:

        print(
            "[INFO] TRADE LOCK ACTIVE"
        )

        print(
            f"[INFO] Open: "
            f"{open_trade['symbol']} "
            f"{open_trade['direction']} "
            f"{open_trade.get('signal_type', '')}"
        )

        print(
            "[INFO] No new signal will be generated."
        )

    else:

        print(
            "[INFO] No open trade."
        )

        print(
            "[INFO] Scanning all symbols..."
        )

        candidates = []

        for item in top_symbols:

            symbol = item[
                "symbol"
            ]

            symbol_data = (
                candles_by_symbol.get(
                    symbol,
                    {}
                )
            )

            if not symbol_data:
                continue

            try:

                signal = (
                    analyze_symbol(
                        symbol,
                        symbol_data
                    )
                )

                if signal is None:
                    continue

                signal[
                    "volume_quote"
                ] = safe_float(
                    item.get(
                        "volume_quote",
                        0
                    )
                )

                candidates.append(
                    signal
                )

                print(
                    "[CANDIDATE] "
                    f"{symbol} "
                    f"{signal['direction']} "
                    f"{signal['signal_type']} "
                    f"Score="
                    f"{signal['combined_score']:.2f}"
                )

            except Exception as e:

                print(
                    f"[WARN] Analysis error "
                    f"{symbol}: {e}"
                )

        # ====================================================
        # SELECT BEST
        # ====================================================

        if candidates:

            candidates.sort(
                key=lambda x: (
                    x["ranking_score"],
                    -x["signal_age_minutes"],
                    math.log10(
                        max(
                            x.get(
                                "volume_quote",
                                1
                            ),
                            1
                        )
                    )
                ),
                reverse=True
            )

            selected_signal = (
                candidates[0]
            )

            print("=" * 75)

            print(
                "[BEST SIGNAL]"
            )

            print(
                f"Symbol: "
                f"{selected_signal['symbol']}"
            )

            print(
                f"Direction: "
                f"{selected_signal['direction']}"
            )

            print(
                f"Type: "
                f"{selected_signal['signal_type']}"
            )

            print(
                f"Combined Score: "
                f"{selected_signal['combined_score']:.2f}/10"
            )

            print(
                f"Ranking Score: "
                f"{selected_signal['ranking_score']:.2f}"
            )

            print(
                f"1H: "
                f"{selected_signal['score_1h']:.1f}"
            )

            print(
                f"30m: "
                f"{selected_signal['score_30m']:.1f}"
            )

            print(
                f"15m: "
                f"{selected_signal['score_15m']:.1f}"
            )

            print(
                f"Entry: "
                f"{fmt_price(selected_signal['entry'])}"
            )

            print(
                f"SL: "
                f"{fmt_price(selected_signal['sl'])}"
            )

            print(
                f"TP: "
                f"{fmt_price(selected_signal['tp'])}"
            )

            print(
                f"RR: "
                f"1:{selected_signal['rr']:.2f}"
            )

            print("=" * 75)

            # =================================================
            # DUPLICATE
            # =================================================

            last_signal_key = STATE.get(
                "last_signal_key"
            )

            if (
                last_signal_key ==
                selected_signal[
                    "signal_key"
                ]
            ):

                print(
                    "[INFO] Duplicate signal blocked."
                )

                selected_signal = None

            else:

                # =============================================
                # OPEN VIRTUAL TRADE
                # =============================================

                trade = dict(
                    selected_signal
                )

                trade[
                    "opened_at"
                ] = utc_timestamp()

                trade[
                    "last_checked_candle"
                ] = selected_signal[
                    "signal_time"
                ]

                STATE[
                    "open_trade"
                ] = trade

                STATE[
                    "last_signal_key"
                ] = selected_signal[
                    "signal_key"
                ]

                save_json(
                    STATE_FILE,
                    STATE
                )

                print(
                    "[INFO] Virtual trade opened."
                )

        else:

            print(
                "[INFO] No valid signal."
            )

    # ========================================================
    # LAST CHECK
    # ========================================================

    STATE[
        "last_checked"
    ] = utc_timestamp()

    save_json(
        STATE_FILE,
        STATE
    )

    save_json(
        HISTORY_FILE,
        HISTORY
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    report = build_report(
        top_symbols,
        selected_signal,
        current_prices,
        closed_trade
    )

    send_telegram(
        report
    )

    # ========================================================
    # FINAL
    # ========================================================

    print("=" * 75)

    print(
        "SCAN COMPLETE"
    )

    print("=" * 75)


# ============================================================
# FATAL ERROR
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("=" * 75)

        print(
            "FATAL SCANNER ERROR"
        )

        print("=" * 75)

        print(
            str(e)
        )

        traceback.print_exc()

        send_telegram_error(
            f"{type(e).__name__}: {e}"
        )

        raise
