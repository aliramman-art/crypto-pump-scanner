# ============================================================
# LBANK FUTURES PRICE ACTION SCANNER v4.0
# ============================================================
# LBank Futures
#
# STRATEGY
# ------------------------------------------------------------
# 1) Get Futures markets directly from Futures marketData
# 2) Rank markets by Futures turnover/volume
# 3) Select TOP 30
# 4) Fetch 5m CLOSED candles only for TOP 30
# 5) Find ONLY the MOST RECENT Tenkan/Kijun crossover
# 6) Ignore all older crossovers
# 7) Crossover candle is EXCLUDED from the box
# 8) Box = 26 candles immediately BEFORE latest crossover
# 9) BUY  = later CLOSED candle closes above Box High
# 10) SELL = later CLOSED candle closes below Box Low
# 11) Swing = confirmed pivot with 2 candles left + 2 right
# 12) BUY SL = latest valid Swing Low - buffer
# 13) SELL SL = latest valid Swing High + buffer
# 14) TP = 50% of Box Width
# 15) Maximum 1 open trade
#
# Volume is used ONLY for market ranking.
# Volume Ratio is calculated only for display.
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


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
})


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_text():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)

    except Exception:
        return default


def normalize_symbol(symbol):
    """
    Convert different LBank symbol formats to a standard form.

    Examples:
        BTCUSDT
        btc_usdt
        BTC_USDT
        btcusdt
    """

    if symbol is None:
        return ""

    s = str(symbol).strip().upper()

    s = s.replace("-", "")
    s = s.replace("/", "")
    s = s.replace("_", "")

    return s


def futures_to_spot_symbol(symbol):
    """
    Convert Futures symbol to Spot Kline symbol.

    LBank Spot Kline commonly uses:
        btc_usdt
    """

    s = normalize_symbol(symbol)

    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base.lower()}_usdt"

    return s.lower()


def is_usdt_symbol(symbol):
    s = normalize_symbol(symbol)

    return (
        s.endswith("USDT")
        and len(s) > 4
        and s not in {
            "USDTUSDT",
        }
    )


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

        data = response.json()

        return data

    except Exception as exc:

        print(
            f"[HTTP ERROR] "
            f"{url} "
            f"params={params} "
            f"error={exc}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token/chat id not configured.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return True

    except Exception as exc:

        print(f"[TELEGRAM ERROR] {exc}")

        return False


# ============================================================
# FILE STATE
# ============================================================

def load_json(path, default):

    try:

        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as exc:

        print(f"[FILE ERROR] Could not load {path}: {exc}")

        return default


def save_json(path, data):

    try:

        temp_path = path + ".tmp"

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

        os.replace(temp_path, path)

    except Exception as exc:

        print(f"[FILE ERROR] Could not save {path}: {exc}")


# ============================================================
# FUTURES MARKET DATA
# ============================================================

def get_futures_market_data():

    url = (
        f"{FUTURES_BASE}"
        f"/marketData"
    )

    params = {
        "productGroup": PRODUCT_GROUP
    }

    data = http_get(
        url,
        params
    )

    if data is None:
        return []

    print(
        "[DEBUG] Futures marketData response type:",
        type(data).__name__
    )

    if isinstance(data, dict):

        if str(data.get("result")).lower() == "false":

            print(
                "[FUTURES API ERROR]",
                data
            )

            return []

        rows = data.get("data")

        if rows is None:
            rows = data.get("marketData")

        if rows is None:
            rows = data.get("result")

        if isinstance(rows, list):
            return rows

        if isinstance(rows, dict):

            for key in (
                "data",
                "marketData",
                "list",
                "rows"
            ):

                value = rows.get(key)

                if isinstance(value, list):
                    return value

    if isinstance(data, list):
        return data

    return []


# ============================================================
# MARKET VOLUME
# ============================================================

def market_volume(row):

    """
    Prefer turnover because it is quote-value based and
    comparable across different coins.

    Fall back to volume-like fields if turnover is unavailable.
    """

    if not isinstance(row, dict):
        return 0.0

    candidates = [
        "turnover",
        "turnover24h",
        "quoteVolume",
        "quote_volume",
        "volume",
        "vol",
    ]

    for key in candidates:

        value = safe_float(
            row.get(key),
            0.0
        )

        if value > 0:
            return value

    return 0.0


def market_last_price(row):

    if not isinstance(row, dict):
        return 0.0

    candidates = [
        "lastPrice",
        "last_price",
        "price",
        "close",
        "markPrice",
        "markedPrice",
    ]

    for key in candidates:

        value = safe_float(
            row.get(key),
            0.0
        )

        if value > 0:
            return value

    return 0.0


# ============================================================
# BUILD FUTURES UNIVERSE
# ============================================================

def build_liquid_universe():

    rows = get_futures_market_data()

    if not rows:

        print(
            "[ERROR] Futures marketData returned no rows."
        )

        return []

    markets = []

    seen = set()

    for row in rows:

        if not isinstance(row, dict):
            continue

        symbol = (
            row.get("symbol")
            or row.get("pair")
            or row.get("contract")
            or row.get("instrument")
            or row.get("productCode")
        )

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        if not is_usdt_symbol(symbol):
            continue

        if symbol in seen:
            continue

        price = market_last_price(row)

        volume = market_volume(row)

        if price <= 0:
            continue

        if volume <= 0:
            continue

        seen.add(symbol)

        markets.append({
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "raw": row,
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    selected = markets[:TOP_LIQUID]

    print(
        f"[INFO] Futures markets discovered: {len(markets)}"
    )

    print(
        f"[INFO] TOP liquid universe: {len(selected)}"
    )

    if selected:

        print(
            "[INFO] Top futures markets:"
        )

        for i, market in enumerate(
            selected[:10],
            start=1
        ):

            print(
                f"{i:02d}. "
                f"{market['symbol']} "
                f"volume={market['volume']:.2f}"
            )

    return selected


# ============================================================
# SPOT KLINE
# ============================================================

def parse_kline_rows(data):

    if data is None:
        return []

    rows = None

    if isinstance(data, list):
        rows = data

    elif isinstance(data, dict):

        if str(data.get("result")).lower() == "false":

            print(
                "[KLINE API ERROR]",
                data
            )

            return []

        for key in (
            "data",
            "kline",
            "klines",
            "result",
            "rows"
        ):

            value = data.get(key)

            if isinstance(value, list):

                rows = value
                break

    if not rows:
        return []

    candles = []

    for row in rows:

        if isinstance(row, dict):

            timestamp = (
                row.get("timestamp")
                or row.get("time")
                or row.get("ts")
            )

            open_price = (
                row.get("open")
                or row.get("Open")
            )

            high_price = (
                row.get("high")
                or row.get("High")
            )

            low_price = (
                row.get("low")
                or row.get("Low")
            )

            close_price = (
                row.get("close")
                or row.get("Close")
            )

            volume = (
                row.get("volume")
                or row.get("Trading Volume")
                or row.get("vol")
            )

        elif isinstance(row, list):

            if len(row) < 6:
                continue

            timestamp = row[0]
            open_price = row[1]
            high_price = row[2]
            low_price = row[3]
            close_price = row[4]
            volume = row[5]

        else:
            continue

        ts = safe_float(
            timestamp,
            0
        )

        if ts <= 0:
            continue

        # LBank timestamps are generally milliseconds
        # but accept seconds too.
        if ts > 10_000_000_000:
            ts = ts / 1000.0

        o = safe_float(
            open_price,
            0
        )

        h = safe_float(
            high_price,
            0
        )

        l = safe_float(
            low_price,
            0
        )

        c = safe_float(
            close_price,
            0
        )

        v = safe_float(
            volume,
            0
        )

        if (
            o <= 0
            or h <= 0
            or l <= 0
            or c <= 0
        ):
            continue

        candles.append({
            "timestamp": int(ts),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": max(v, 0.0),
        })

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    # Remove duplicates
    unique = {}

    for candle in candles:
        unique[candle["timestamp"]] = candle

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    return candles


def get_5m_candles(symbol, limit=CANDLE_LIMIT):

    spot_symbol = futures_to_spot_symbol(
        symbol
    )

    url = (
        f"{SPOT_BASE}"
        f"/v2/kline.do"
    )

    params = {
        "symbol": spot_symbol,
        "size": limit,
        "type": TIMEFRAME,
    }

    data = http_get(
        url,
        params
    )

    candles = parse_kline_rows(
        data
    )

    if candles:

        print(
            f"[KLINE] {symbol} "
            f"({spot_symbol}) "
            f"=> {len(candles)} candles"
        )

        return candles

    # --------------------------------------------------------
    # SECOND SYMBOL FORMAT ATTEMPT
    # --------------------------------------------------------

    alternative_symbols = [
        symbol.lower(),
        symbol.upper(),
    ]

    for alt in alternative_symbols:

        if alt == spot_symbol:
            continue

        params_alt = {
            "symbol": alt,
            "size": limit,
            "type": TIMEFRAME,
        }

        data_alt = http_get(
            url,
            params_alt
        )

        candles_alt = parse_kline_rows(
            data_alt
        )

        if candles_alt:

            print(
                f"[KLINE] {symbol} "
                f"alternative={alt} "
                f"=> {len(candles_alt)} candles"
            )

            return candles_alt

    print(
        f"[KLINE EMPTY] "
        f"{symbol} "
        f"spot={spot_symbol}"
    )

    return []


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def get_closed_candles(candles):

    if not candles:
        return []

    current_time = int(
        time.time()
    )

    closed = []

    for candle in candles:

        ts = candle["timestamp"]

        # A 5m candle is closed only after
        # its full 300 seconds have elapsed.
        if ts + 300 <= current_time:

            closed.append(candle)

    closed.sort(
        key=lambda x: x["timestamp"]
    )

    return closed


# ============================================================
# TENKAN / KIJUN
# ============================================================

def calculate_tenkan(candles, index):

    start = index - TENKAN_PERIOD + 1

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest + lowest
    ) / 2.0


def calculate_kijun(candles, index):

    start = index - KIJUN_PERIOD + 1

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest + lowest
    ) / 2.0


def build_ichimoku_values(candles):

    values = []

    for i in range(
        len(candles)
    ):

        tenkan = calculate_tenkan(
            candles,
            i
        )

        kijun = calculate_kijun(
            candles,
            i
        )

        values.append({
            "tenkan": tenkan,
            "kijun": kijun,
        })

    return values


# ============================================================
# MOST RECENT TENKAN / KIJUN CROSS
# ============================================================

def find_latest_cross(candles):

    if len(candles) < KIJUN_PERIOD + 2:
        return None

    ichimoku = build_ichimoku_values(
        candles
    )

    latest_cross = None

    for i in range(
        1,
        len(candles)
    ):

        prev = ichimoku[i - 1]
        curr = ichimoku[i]

        if (
            prev["tenkan"] is None
            or prev["kijun"] is None
            or curr["tenkan"] is None
            or curr["kijun"] is None
        ):
            continue

        prev_diff = (
            prev["tenkan"]
            - prev["kijun"]
        )

        curr_diff = (
            curr["tenkan"]
            - curr["kijun"]
        )

        cross_type = None

        # Bullish cross
        if (
            prev_diff <= 0
            and curr_diff > 0
        ):

            cross_type = "BUY"

        # Bearish cross
        elif (
            prev_diff >= 0
            and curr_diff < 0
        ):

            cross_type = "SELL"

        if cross_type:

            latest_cross = {
                "index": i,
                "type": cross_type,
                "timestamp": candles[i]["timestamp"],
                "price": candles[i]["close"],
                "tenkan": curr["tenkan"],
                "kijun": curr["kijun"],
            }

    return latest_cross


# ============================================================
# BOX
# ============================================================

def build_box(candles, cross_index):

    start = (
        cross_index
        - BOX_SIZE
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
        c["high"]
        for c in box_candles
    )

    box_low = min(
        c["low"]
        for c in box_candles
    )

    width = (
        box_high
        - box_low
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
# CONFIRMED SWINGS
# ============================================================

def is_swing_low(
    candles,
    index
):

    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if index - left < 0:
        return False

    if index + right >= len(candles):
        return False

    current = candles[index]["low"]

    for i in range(
        index - left,
        index
    ):

        if candles[i]["low"] <= current:
            return False

    for i in range(
        index + 1,
        index + right + 1
    ):

        if candles[i]["low"] <= current:
            return False

    return True


def is_swing_high(
    candles,
    index
):

    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if index - left < 0:
        return False

    if index + right >= len(candles):
        return False

    current = candles[index]["high"]

    for i in range(
        index - left,
        index
    ):

        if candles[i]["high"] >= current:
            return False

    for i in range(
        index + 1,
        index + right + 1
    ):

        if candles[i]["high"] >= current:
            return False

    return True


def find_latest_swing_low(
    candles,
    start_index,
    end_index
):

    end_index = min(
        end_index,
        len(candles) - PIVOT_RIGHT - 1
    )

    for i in range(
        end_index,
        start_index - 1,
        -1
    ):

        if is_swing_low(
            candles,
            i
        ):

            return {
                "index": i,
                "price": candles[i]["low"],
                "timestamp": candles[i]["timestamp"],
            }

    return None


def find_latest_swing_high(
    candles,
    start_index,
    end_index
):

    end_index = min(
        end_index,
        len(candles) - PIVOT_RIGHT - 1
    )

    for i in range(
        end_index,
        start_index - 1,
        -1
    ):

        if is_swing_high(
            candles,
            i
        ):

            return {
                "index": i,
                "price": candles[i]["high"],
                "timestamp": candles[i]["timestamp"],
            }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles,
    index
):

    if index < VOLUME_LOOKBACK:
        return 0.0

    current_volume = candles[
        index
    ]["volume"]

    previous = [
        candles[i]["volume"]
        for i in range(
            index - VOLUME_LOOKBACK,
            index
        )
    ]

    if not previous:
        return 0.0

    avg_volume = (
        sum(previous)
        / len(previous)
    )

    if avg_volume <= 0:
        return 0.0

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# FIND BREAKOUT
# ============================================================

def find_latest_breakout(
    candles,
    cross,
    box
):

    if not cross or not box:
        return None

    cross_index = cross["index"]

    # --------------------------------------------------------
    # Only candles AFTER crossover can trigger breakout.
    # --------------------------------------------------------

    if cross_index + 1 >= len(candles):
        return None

    latest_breakout = None

    for i in range(
        cross_index + 1,
        len(candles)
    ):

        candle = candles[i]

        close = candle["close"]

        direction = None

        if close > box["high"]:

            direction = "BUY"

        elif close < box["low"]:

            direction = "SELL"

        if direction:

            latest_breakout = {
                "index": i,
                "type": direction,
                "price": close,
                "timestamp": candle["timestamp"],
                "volume_ratio": calculate_volume_ratio(
                    candles,
                    i
                ),
            }

    return latest_breakout


# ============================================================
# BUILD TRADE
# ============================================================

def build_trade(
    symbol,
    candles,
    cross,
    box,
    breakout,
    market_volume
):

    if not breakout:
        return None

    direction = breakout["type"]

    breakout_index = breakout["index"]

    # --------------------------------------------------------
    # Swing must be AFTER crossover and BEFORE breakout.
    # Also must be CONFIRMED.
    # --------------------------------------------------------

    swing_start = cross["index"] + 1

    swing_end = breakout_index - 1

    if swing_end < swing_start:
        return None

    if direction == "BUY":

        swing = find_latest_swing_low(
            candles,
            swing_start,
            swing_end
        )

        if not swing:
            return None

        entry = breakout["price"]

        sl = (
            swing["price"]
            * (1.0 - SL_BUFFER_PERCENT)
        )

        risk = (
            entry - sl
        )

        if risk <= 0:
            return None

        tp = (
            entry
            + box["width"]
            * TP_BOX_PERCENT
        )

        if tp <= entry:
            return None

    else:

        swing = find_latest_swing_high(
            candles,
            swing_start,
            swing_end
        )

        if not swing:
            return None

        entry = breakout["price"]

        sl = (
            swing["price"]
            * (1.0 + SL_BUFFER_PERCENT)
        )

        risk = (
            sl - entry
        )

        if risk <= 0:
            return None

        tp = (
            entry
            - box["width"]
            * TP_BOX_PERCENT
        )

        if tp >= entry:
            return None

    return {
        "symbol": symbol,
        "direction": direction,

        "entry": entry,
        "sl": sl,
        "tp": tp,

        "risk": risk,

        "box_high": box["high"],
        "box_low": box["low"],
        "box_width": box["width"],

        "cross_index": cross["index"],
        "cross_price": cross["price"],
        "cross_timestamp": cross["timestamp"],

        "breakout_index": breakout_index,
        "breakout_price": breakout["price"],
        "breakout_timestamp": breakout["timestamp"],

        "swing_index": swing["index"],
        "swing_price": swing["price"],
        "swing_timestamp": swing["timestamp"],

        "volume": market_volume,
        "volume_ratio": breakout["volume_ratio"],

        "opened_at": now_utc().isoformat(),
        "status": "OPEN",
    }


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol,
    candles
):

    if candles:

        return candles[-1]["close"]

    return 0.0


# ============================================================
# PNL
# ============================================================

def calculate_pnl_percent(
    trade,
    current_price
):

    if not trade:
        return 0.0

    entry = safe_float(
        trade.get("entry"),
        0
    )

    if entry <= 0:
        return 0.0

    direction = trade.get(
        "direction"
    )

    if direction == "BUY":

        return (
            (current_price - entry)
            / entry
        ) * 100.0

    return (
        (entry - current_price)
        / entry
    ) * 100.0


# ============================================================
# TRADE STATUS
# ============================================================

def check_trade_exit(
    trade,
    current_price
):

    if not trade:
        return None

    direction = trade["direction"]

    entry = trade["entry"]
    sl = trade["sl"]
    tp = trade["tp"]

    if direction == "BUY":

        if current_price <= sl:

            return "SL"

        if current_price >= tp:

            return "TP"

    else:

        if current_price >= sl:

            return "SL"

        if current_price <= tp:

            return "TP"

    return None


# ============================================================
# HISTORY
# ============================================================

def add_history_trade(
    history,
    trade,
    exit_reason,
    exit_price
):

    result = "LOSS"

    if exit_reason == "TP":
        result = "WIN"

    pnl = calculate_pnl_percent(
        trade,
        exit_price
    )

    item = dict(trade)

    item["exit_reason"] = exit_reason
    item["exit_price"] = exit_price
    item["pnl_percent"] = pnl
    item["result"] = result
    item["closed_at"] = now_utc().isoformat()

    history.append(item)

    return item


def performance_summary(history):

    trades = len(history)

    wins = sum(
        1
        for x in history
        if x.get("result") == "WIN"
    )

    losses = sum(
        1
        for x in history
        if x.get("result") == "LOSS"
    )

    breakeven = sum(
        1
        for x in history
        if x.get("result") == "BREAKEVEN"
    )

    if trades > 0:

        win_rate = (
            wins
            / trades
        ) * 100.0

    else:

        win_rate = 0.0

    total_pnl = sum(
        safe_float(
            x.get("pnl_percent"),
            0
        )
        for x in history
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


# ============================================================
# FORMATTERS
# ============================================================

def fmt_price(value):

    value = safe_float(
        value,
        0
    )

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    if abs(value) >= 0.01:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    if abs(value) >= 0.0001:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    return f"{value:.10f}".rstrip("0").rstrip(".")


def fmt_volume(value):

    value = safe_float(
        value,
        0
    )

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:.2f}"


def fmt_time(timestamp):

    try:

        return datetime.fromtimestamp(
            timestamp,
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

    except Exception:

        return "-"


# ============================================================
# REPORT
# ============================================================

def build_report(
    state,
    history,
    selected_count,
    valid_count
):

    performance = performance_summary(
        history
    )

    open_trade = state.get(
        "open_trade"
    )

    lines = []

    lines.append(
        "📡 *LBANK FUTURES PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 {now_text()}"
    )

    lines.append(
        f"⏱ *5m CLOSED | TOP {TOP_VOLUME}*"
    )

    lines.append(
        "🤖 *TENKAN/KIJUN 26 BOX*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        f"Trades {performance['trades']} | "
        f"🟢 {performance['wins']} | "
        f"🔴 {performance['losses']} | "
        f"⚪ {performance['breakeven']}"
    )

    lines.append(
        f"🏆 WR: {performance['win_rate']:.1f}%"
    )

    lines.append(
        f"💰 Total P&L: "
        f"{performance['total_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📈 Futures Universe: {selected_count}"
    )

    lines.append(
        f"📊 Kline Valid: {valid_count}"
    )

    if not open_trade:

        lines.append(
            "📭 *OPEN TRADE:* NONE"
        )

        lines.append(
            "🔎 No active trade."
        )

        return "\n".join(lines)

    symbol = open_trade["symbol"]

    current = safe_float(
        open_trade.get("current_price"),
        0
    )

    pnl = safe_float(
        open_trade.get("live_pnl"),
        0
    )

    direction = open_trade["direction"]

    emoji = (
        "🟢"
        if direction == "BUY"
        else "🔴"
    )

    lines.append(
        f"{emoji} *{symbol} - {direction}*"
    )

    lines.append(
        f"Entry: `{fmt_price(open_trade['entry'])}`"
    )

    lines.append(
        f"SL: `{fmt_price(open_trade['sl'])}`"
    )

    lines.append(
        f"TP: `{fmt_price(open_trade['tp'])}`"
    )

    lines.append(
        f"Current: `{fmt_price(current)}`"
    )

    lines.append(
        f"Live P&L: `{pnl:+.2f}%`"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📦 *BOX*"
    )

    lines.append(
        f"High: `{fmt_price(open_trade['box_high'])}`"
    )

    lines.append(
        f"Low: `{fmt_price(open_trade['box_low'])}`"
    )

    lines.append(
        f"Width: `{fmt_price(open_trade['box_width'])}`"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔄 *CROSS*"
    )

    lines.append(
        f"Type: `{open_trade['direction']}`"
    )

    lines.append(
        f"Price: `{fmt_price(open_trade['cross_price'])}`"
    )

    lines.append(
        f"Time: `{fmt_time(open_trade['cross_timestamp'])}`"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🚀 *BREAKOUT*"
    )

    lines.append(
        f"Price: `{fmt_price(open_trade['breakout_price'])}`"
    )

    lines.append(
        f"Time: `{fmt_time(open_trade['breakout_timestamp'])}`"
    )

    lines.append(
        f"Volume Ratio: `{open_trade['volume_ratio']:.2f}x`"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🧭 *SWING*"
    )

    lines.append(
        f"Price: `{fmt_price(open_trade['swing_price'])}`"
    )

    lines.append(
        f"Time: `{fmt_time(open_trade['swing_timestamp'])}`"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📊 Ranked Volume: `{fmt_volume(open_trade['volume'])}`"
    )

    return "\n".join(lines)


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    market
):

    symbol = market["symbol"]

    candles = get_5m_candles(
        symbol,
        CANDLE_LIMIT
    )

    if len(candles) < 60:

        print(
            f"[SKIP] {symbol}: "
            f"not enough candles ({len(candles)})"
        )

        return None

    candles = get_closed_candles(
        candles
    )

    if len(candles) < 60:

        print(
            f"[SKIP] {symbol}: "
            f"not enough CLOSED candles "
            f"({len(candles)})"
        )

        return None

    cross = find_latest_cross(
        candles
    )

    if not cross:

        print(
            f"[NO CROSS] {symbol}"
        )

        return None

    print(
        f"[LATEST CROSS] "
        f"{symbol} "
        f"{cross['type']} "
        f"@ {fmt_price(cross['price'])} "
        f"{fmt_time(cross['timestamp'])}"
    )

    box = build_box(
        candles,
        cross["index"]
    )

    if not box:

        print(
            f"[NO BOX] {symbol}"
        )

        return None

    print(
        f"[BOX] {symbol} "
        f"High={fmt_price(box['high'])} "
        f"Low={fmt_price(box['low'])}"
    )

    breakout = find_latest_breakout(
        candles,
        cross,
        box
    )

    if not breakout:

        print(
            f"[NO BREAKOUT] {symbol}"
        )

        return None

    print(
        f"[BREAKOUT] "
        f"{symbol} "
        f"{breakout['type']} "
        f"@ {fmt_price(breakout['price'])}"
    )

    trade = build_trade(
        symbol,
        candles,
        cross,
        box,
        breakout,
        market["volume"]
    )

    if not trade:

        print(
            f"[NO VALID TRADE] {symbol}"
        )

        return None

    current_price = get_current_price(
        symbol,
        candles
    )

    trade["current_price"] = current_price

    trade["live_pnl"] = calculate_pnl_percent(
        trade,
        current_price
    )

    return trade


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "======================================================================"
    )

    print(
        "LBANK FUTURES PRICE ACTION SCANNER v4.0"
    )

    print(
        "======================================================================"
    )

    print(
        f"Timeframe: {TIMEFRAME}"
    )

    print(
        f"Top Futures Universe: {TOP_LIQUID}"
    )

    print(
        f"Final Ranking: TOP {TOP_VOLUME}"
    )

    print(
        f"Box: {BOX_SIZE} candles"
    )

    print(
        "Latest Cross Only: YES"
    )

    print(
        "Cross Candle In Box: NO"
    )

    print(
        "Confirmed Swing: 2L + 2R"
    )

    print(
        f"TP: {TP_BOX_PERCENT * 100:.0f}% Box Width"
    )

    print(
        f"Max Open Trades: {MAX_OPEN_TRADES}"
    )

    print(
        "======================================================================"
    )

    # --------------------------------------------------------
    # LOAD STATE
    # --------------------------------------------------------

    state = load_json(
        STATE_FILE,
        {
            "open_trade": None,
            "last_scan": None,
            "last_signal_key": None,
        }
    )

    history = load_json(
        HISTORY_FILE,
        []
    )

    # --------------------------------------------------------
    # CHECK EXISTING OPEN TRADE
    # --------------------------------------------------------

    open_trade = state.get(
        "open_trade"
    )

    if open_trade:

        print(
            f"[OPEN TRADE] "
            f"{open_trade['symbol']} "
            f"{open_trade['direction']}"
        )

        symbol = open_trade["symbol"]

        candles = get_5m_candles(
            symbol,
            20
        )

        closed = get_closed_candles(
            candles
        )

        if closed:

            current_price = closed[-1]["close"]

            open_trade["current_price"] = (
                current_price
            )

            open_trade["live_pnl"] = (
                calculate_pnl_percent(
                    open_trade,
                    current_price
                )
            )

            exit_reason = check_trade_exit(
                open_trade,
                current_price
            )

            if exit_reason:

                print(
                    f"[TRADE CLOSED] "
                    f"{symbol} "
                    f"{exit_reason} "
                    f"@ {fmt_price(current_price)}"
                )

                closed_trade = add_history_trade(
                    history,
                    open_trade,
                    exit_reason,
                    current_price
                )

                save_json(
                    HISTORY_FILE,
                    history
                )

                state["open_trade"] = None

                state["last_signal_key"] = None

                print(
                    f"[RESULT] "
                    f"{closed_trade['result']} "
                    f"{closed_trade['pnl_percent']:+.2f}%"
                )

            else:

                print(
                    f"[OPEN] "
                    f"Current={fmt_price(current_price)} "
                    f"P&L={open_trade['live_pnl']:+.2f}%"
                )

        state["last_scan"] = now_utc().isoformat()

        save_json(
            STATE_FILE,
            state
        )

        report = build_report(
            state,
            history,
            0,
            0
        )

        send_telegram(
            report
        )

        return

    # --------------------------------------------------------
    # STEP 1:
    # DIRECT FUTURES MARKET DATA
    # --------------------------------------------------------

    universe = build_liquid_universe()

    if not universe:

        print(
            "[ERROR] No Futures markets available."
        )

        send_telegram(
            "⚠️ *LBANK FUTURES SCANNER*\n\n"
            "No Futures markets available from marketData.\n\n"
            "Check LBank Futures API response."
        )

        return

    # --------------------------------------------------------
    # STEP 2:
    # TOP 30 BY FUTURES VOLUME / TURNOVER
    # --------------------------------------------------------

    selected_markets = universe[
        :TOP_VOLUME
    ]

    print(
        "======================================================================"
    )

    print(
        f"[INFO] FINAL TOP {TOP_VOLUME} MARKETS"
    )

    print(
        "======================================================================"
    )

    for i, market in enumerate(
        selected_markets,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{market['symbol']:15s} "
            f"volume={fmt_volume(market['volume'])}"
        )

    # --------------------------------------------------------
    # STEP 3:
    # SCAN TOP 30 ONLY
    # --------------------------------------------------------

    candidates = []

    valid_count = 0

    for number, market in enumerate(
        selected_markets,
        start=1
    ):

        print(
            "------------------------------------------------------------------"
        )

        print(
            f"[SCAN {number}/{len(selected_markets)}] "
            f"{market['symbol']}"
        )

        try:

            trade = scan_market(
                market
            )

            if trade:

                candidates.append(
                    trade
                )

                print(
                    f"[CANDIDATE] "
                    f"{trade['symbol']} "
                    f"{trade['direction']}"
                )

            valid_count += 1

        except Exception as exc:

            print(
                f"[SCAN ERROR] "
                f"{market['symbol']} "
                f"{exc}"
            )

    # --------------------------------------------------------
    # STEP 4:
    # MAX ONE TRADE
    # --------------------------------------------------------

    selected_trade = None

    if candidates:

        # ----------------------------------------------------
        # Prefer strongest volume ratio.
        # If equal, prefer latest breakout.
        # ----------------------------------------------------

        candidates.sort(
            key=lambda x: (
                safe_float(
                    x.get("volume_ratio"),
                    0
                ),
                x.get(
                    "breakout_timestamp",
                    0
                )
            ),
            reverse=True
        )

        selected_trade = candidates[0]

    # --------------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------------

    if selected_trade:

        signal_key = (
            f"{selected_trade['symbol']}_"
            f"{selected_trade['direction']}_"
            f"{selected_trade['breakout_timestamp']}"
        )

        if (
            state.get("last_signal_key")
            == signal_key
        ):

            print(
                "[INFO] Same signal already processed."
            )

        else:

            state["open_trade"] = (
                selected_trade
            )

            state["last_signal_key"] = (
                signal_key
            )

            print(
                "======================================================================"
            )

            print(
                "[NEW TRADE]"
            )

            print(
                f"Symbol: {selected_trade['symbol']}"
            )

            print(
                f"Direction: {selected_trade['direction']}"
            )

            print(
                f"Entry: {fmt_price(selected_trade['entry'])}"
            )

            print(
                f"SL: {fmt_price(selected_trade['sl'])}"
            )

            print(
                f"TP: {fmt_price(selected_trade['tp'])}"
            )

            print(
                f"Box High: {fmt_price(selected_trade['box_high'])}"
            )

            print(
                f"Box Low: {fmt_price(selected_trade['box_low'])}"
            )

            print(
                f"Swing: {fmt_price(selected_trade['swing_price'])}"
            )

            print(
                f"Volume Ratio: "
                f"{selected_trade['volume_ratio']:.2f}x"
            )

            print(
                "======================================================================"
            )

    else:

        print(
            "======================================================================"
        )

        print(
            "[INFO] No valid trade found."
        )

        print(
            "======================================================================"
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    state["last_scan"] = (
        now_utc().isoformat()
    )

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        HISTORY_FILE,
        history
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    report = build_report(
        state,
        history,
        len(selected_markets),
        valid_count
    )

    print(
        "======================================================================"
    )

    print(
        "TELEGRAM REPORT"
    )

    print(
        report
    )

    print(
        "======================================================================"
    )

    send_telegram(
        report
    )

    print(
        "======================================================================"
    )

    print(
        "SCAN COMPLETED"
    )

    print(
        "======================================================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOPPED] Keyboard interrupt."
        )

    except Exception as exc:

        print(
            f"\n[FATAL ERROR] {exc}"
        )

        try:

            send_telegram(
                "🚨 *LBANK FUTURES SCANNER ERROR*\n\n"
                f"`{str(exc)[:3500]}`"
            )

        except Exception:
            pass

        raise
