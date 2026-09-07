# ============================================================
# LBANK FUTURES + BINANCE FUTURES PRICE ACTION SCANNER
# ============================================================
# SOURCE:
#   LBank Futures  -> Volume ranking / TOP 30
#   Binance USD-M  -> 5m OHLCV / Price Action
#
# STRATEGY:
#   1) TOP 30 LBank Futures by turnover
#   2) Binance Futures equivalent symbol
#   3) 5m CLOSED candles only
#   4) Tenkan/Kijun = 9/26
#   5) ONLY the MOST RECENT Tenkan/Kijun cross
#   6) Cross candle is NOT part of Box
#   7) Box = 26 candles immediately BEFORE latest cross
#   8) BUY  = later closed candle closes above Box High
#   9) SELL = later closed candle closes below Box Low
#  10) Swing = confirmed pivot with 2 candles left + 2 right
#  11) BUY SL  = below latest valid Swing Low
#  12) SELL SL = above latest valid Swing High
#  13) TP = 50% of Box width
#  14) MAX OPEN TRADES = 1
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

LBANK_BASE = "https://lbkperp.lbank.com/cfd/openApi/v1/pub"
LBANK_MARKET_DATA = f"{LBANK_BASE}/marketData"

BINANCE_BASE = "https://fapi.binance.com"
BINANCE_EXCHANGE_INFO = f"{BINANCE_BASE}/fapi/v1/exchangeInfo"
BINANCE_KLINES = f"{BINANCE_BASE}/fapi/v1/klines"
BINANCE_TICKER = f"{BINANCE_BASE}/fapi/v1/ticker/price"

PRODUCT_GROUP = "SwapU"

TIMEFRAME = "5m"

TOP_LBANK = 30

# Enough candles for:
# latest cross + 26 box candles + breakout + swing
CANDLE_LIMIT = 300

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

BOX_SIZE = 26

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

VOLUME_LOOKBACK = 20

# TP = 50% of box width
TP_BOX_PERCENT = 0.50

# Small structural SL buffer
SL_BUFFER_PERCENT = 0.0015

MAX_OPEN_TRADES = 1

REQUEST_TIMEOUT = 20

STATE_FILE = "lbank_futures_price_action_state.json"
HISTORY_FILE = "lbank_futures_price_action_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 Crypto-Price-Action-Scanner/1.0"
    }
)


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def fmt_price(value):
    if value is None:
        return "N/A"

    try:
        value = float(value)
    except Exception:
        return str(value)

    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 100:
        return f"{value:,.3f}"
    if value >= 1:
        return f"{value:,.4f}"
    if value >= 0.1:
        return f"{value:,.5f}"
    if value >= 0.01:
        return f"{value:,.6f}"

    return f"{value:.8f}"


def fmt_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "N/A"


def ms_to_iso(ms):
    try:
        return datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "N/A"


def normalize_symbol(symbol):
    """
    Converts common LBank formats to Binance-style:

        btc_usdt  -> BTCUSDT
        BTC-USDT  -> BTCUSDT
        BTCUSDT   -> BTCUSDT
    """

    if not symbol:
        return ""

    s = str(symbol).upper().strip()

    for ch in ["_", "-", "/", ":", "."]:
        s = s.replace(ch, "")

    return s


def json_load(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"[WARN] Failed reading {path}: {e}")
        return default


def json_save(path, data):
    temp = path + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp, path)


def load_state():
    default = {
        "open_trade": None,
        "last_run": None,
        "last_signal": None
    }

    return json_load(STATE_FILE, default)


def save_state(state):
    state["last_run"] = now_iso()
    json_save(STATE_FILE, state)


def load_history():
    return json_load(HISTORY_FILE, [])


def save_history(history):
    json_save(HISTORY_FILE, history)


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    try:
        r = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:
        print(
            f"[HTTP ERROR] {url} "
            f"params={params} "
            f"error={e}"
        )
        return None


# ============================================================
# LBANK FUTURES
# ============================================================

def get_lbank_market_data():
    """
    Get LBank Futures market data.

    LBank is used ONLY for:
        - Futures universe
        - volume/turnover ranking
    """

    params = {
        "productGroup": PRODUCT_GROUP
    }

    data = http_get(
        LBANK_MARKET_DATA,
        params
    )

    if data is None:
        return []

    # LBank responses may vary slightly.
    if isinstance(data, list):
        rows = data

    elif isinstance(data, dict):
        rows = (
            data.get("data")
            or data.get("result")
            or data.get("markets")
            or data.get("rows")
            or []
        )

    else:
        rows = []

    if not isinstance(rows, list):
        return []

    markets = []

    for item in rows:

        if not isinstance(item, dict):
            continue

        raw_symbol = (
            item.get("symbol")
            or item.get("pair")
            or item.get("instrument")
        )

        symbol = normalize_symbol(raw_symbol)

        if not symbol:
            continue

        last_price = safe_float(
            item.get("lastPrice")
            or item.get("last")
            or item.get("price")
        )

        turnover = safe_float(
            item.get("turnover")
            or item.get("turnover24h")
            or item.get("quoteVolume")
            or item.get("amount")
        )

        volume = safe_float(
            item.get("volume")
            or item.get("baseVolume")
        )

        if turnover <= 0 and volume > 0 and last_price > 0:
            turnover = volume * last_price

        if turnover <= 0:
            continue

        markets.append(
            {
                "lbank_symbol": raw_symbol,
                "symbol": symbol,
                "last_price": last_price,
                "turnover": turnover,
                "volume": volume
            }
        )

    return markets


def rank_lbank_markets(markets):
    markets = sorted(
        markets,
        key=lambda x: x.get("turnover", 0),
        reverse=True
    )

    return markets[:TOP_LBANK]


# ============================================================
# BINANCE FUTURES SYMBOLS
# ============================================================

def get_binance_symbols():
    """
    Returns currently TRADING Binance USD-M Futures symbols.
    """

    data = http_get(BINANCE_EXCHANGE_INFO)

    if not isinstance(data, dict):
        return set()

    symbols = set()

    for item in data.get("symbols", []):
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol")
        status = item.get("status")

        if symbol and status == "TRADING":
            symbols.add(
                normalize_symbol(symbol)
            )

    return symbols


# ============================================================
# BINANCE FUTURES KLINES
# ============================================================

def get_binance_klines(symbol):
    """
    Binance USD-M Futures 5m candles.

    We intentionally request more than enough history.

    The final currently-open candle is removed.
    """

    params = {
        "symbol": symbol,
        "interval": TIMEFRAME,
        "limit": CANDLE_LIMIT
    }

    data = http_get(
        BINANCE_KLINES,
        params
    )

    if not isinstance(data, list):
        return []

    candles = []

    current_ms = int(time.time() * 1000)

    for row in data:

        if not isinstance(row, list):
            continue

        if len(row) < 7:
            continue

        try:
            open_time = int(row[0])
            open_price = float(row[1])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            volume = float(row[5])
            close_time = int(row[6])

        except Exception:
            continue

        # Only CLOSED candles
        if close_time >= current_ms:
            continue

        candles.append(
            {
                "open_time": open_time,
                "close_time": close_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume
            }
        )

    candles.sort(
        key=lambda x: x["open_time"]
    )

    return candles


# ============================================================
# BINANCE CURRENT PRICE
# ============================================================

def get_binance_price(symbol):
    data = http_get(
        BINANCE_TICKER,
        {"symbol": symbol}
    )

    if isinstance(data, dict):
        return safe_float(
            data.get("price")
        )

    return 0.0


# ============================================================
# TENKAN / KIJUN
# ============================================================

def calculate_tenkan(candles, index):
    if index + 1 < TENKAN_PERIOD:
        return None

    start = index - TENKAN_PERIOD + 1
    section = candles[start:index + 1]

    highest = max(
        x["high"] for x in section
    )

    lowest = min(
        x["low"] for x in section
    )

    return (highest + lowest) / 2.0


def calculate_kijun(candles, index):
    if index + 1 < KIJUN_PERIOD:
        return None

    start = index - KIJUN_PERIOD + 1
    section = candles[start:index + 1]

    highest = max(
        x["high"] for x in section
    )

    lowest = min(
        x["low"] for x in section
    )

    return (highest + lowest) / 2.0


# ============================================================
# LATEST CROSS
# ============================================================

def find_latest_cross(candles):
    """
    IMPORTANT:
    Only the latest cross is returned.

    Older crosses are ignored.
    """

    if len(candles) < KIJUN_PERIOD + 2:
        return None

    latest = None

    previous_tenkan = None
    previous_kijun = None

    for i in range(len(candles)):

        tenkan = calculate_tenkan(
            candles,
            i
        )

        kijun = calculate_kijun(
            candles,
            i
        )

        if tenkan is None or kijun is None:
            continue

        if previous_tenkan is not None and previous_kijun is not None:

            cross_type = None

            if (
                previous_tenkan <= previous_kijun
                and tenkan > kijun
            ):
                cross_type = "BUY"

            elif (
                previous_tenkan >= previous_kijun
                and tenkan < kijun
            ):
                cross_type = "SELL"

            if cross_type:
                latest = {
                    "index": i,
                    "type": cross_type,
                    "open_time": candles[i]["open_time"],
                    "close": candles[i]["close"],
                    "tenkan": tenkan,
                    "kijun": kijun
                }

        previous_tenkan = tenkan
        previous_kijun = kijun

    return latest


# ============================================================
# BOX
# ============================================================

def build_box(candles, cross):
    """
    Cross candle is EXCLUDED.

    Box = exactly 26 candles immediately before cross.
    """

    if not cross:
        return None

    cross_index = cross["index"]

    start = cross_index - BOX_SIZE
    end = cross_index

    if start < 0:
        return None

    box_candles = candles[start:end]

    if len(box_candles) != BOX_SIZE:
        return None

    box_high = max(
        x["high"] for x in box_candles
    )

    box_low = min(
        x["low"] for x in box_candles
    )

    width = box_high - box_low

    if width <= 0:
        return None

    return {
        "start_index": start,
        "end_index": end - 1,
        "high": box_high,
        "low": box_low,
        "width": width,
        "count": len(box_candles)
    }


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(candles, cross, box):
    """
    Search only candles AFTER the latest cross.

    BUY:
        close > Box High

    SELL:
        close < Box Low

    The latest qualifying closed breakout is kept.
    """

    if not cross or not box:
        return None

    cross_index = cross["index"]

    breakout = None

    for i in range(cross_index + 1, len(candles)):

        candle = candles[i]

        if candle["close"] > box["high"]:
            breakout = {
                "index": i,
                "type": "BUY",
                "open_time": candle["open_time"],
                "close": candle["close"]
            }

        elif candle["close"] < box["low"]:
            breakout = {
                "index": i,
                "type": "SELL",
                "open_time": candle["open_time"],
                "close": candle["close"]
            }

    return breakout


# ============================================================
# CONFIRMED SWINGS
# ============================================================

def is_swing_low(candles, index):
    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if index - left < 0:
        return False

    if index + right >= len(candles):
        return False

    value = candles[index]["low"]

    for i in range(
        index - left,
        index + right + 1
    ):
        if i == index:
            continue

        if candles[i]["low"] <= value:
            return False

    return True


def is_swing_high(candles, index):
    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if index - left < 0:
        return False

    if index + right >= len(candles):
        return False

    value = candles[index]["high"]

    for i in range(
        index - left,
        index + right + 1
    ):
        if i == index:
            continue

        if candles[i]["high"] >= value:
            return False

    return True


def find_latest_swing(candles, cross, breakout):
    """
    Swing must be confirmed.

    We search BEFORE the breakout candle,
    because the breakout candle itself should not
    become the structural swing.
    """

    if not cross or not breakout:
        return None

    cross_index = cross["index"]
    breakout_index = breakout["index"]

    start = cross_index + 1
    end = breakout_index - 1

    if end - start + 1 < 5:
        return None

    if breakout["type"] == "BUY":

        for i in range(
            end,
            start - 1,
            -1
        ):
            if is_swing_low(
                candles,
                i
            ):
                return {
                    "type": "LOW",
                    "index": i,
                    "price": candles[i]["low"],
                    "open_time": candles[i]["open_time"]
                }

    elif breakout["type"] == "SELL":

        for i in range(
            end,
            start - 1,
            -1
        ):
            if is_swing_high(
                candles,
                i
            ):
                return {
                    "type": "HIGH",
                    "index": i,
                    "price": candles[i]["high"],
                    "open_time": candles[i]["open_time"]
                }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(candles):
    if len(candles) < VOLUME_LOOKBACK + 1:
        return 0.0

    current = candles[-1]["volume"]

    previous = candles[
        -VOLUME_LOOKBACK - 1:-1
    ]

    avg = sum(
        x["volume"] for x in previous
    ) / len(previous)

    if avg <= 0:
        return 0.0

    return current / avg


# ============================================================
# SETUP
# ============================================================

def analyze_symbol(lbank_market, candles):
    result = {
        "symbol": lbank_market["symbol"],
        "lbank_symbol": lbank_market.get("lbank_symbol"),
        "status": "NO SIGNAL",
        "reason": "",
        "cross": None,
        "box": None,
        "breakout": None,
        "swing": None,
        "volume_ratio": calculate_volume_ratio(candles),
        "candles": len(candles)
    }

    if len(candles) < (
        KIJUN_PERIOD
        + BOX_SIZE
        + 10
    ):
        result["reason"] = (
            f"Insufficient Kline ({len(candles)})"
        )
        return result

    cross = find_latest_cross(candles)

    if not cross:
        result["reason"] = "NO LATEST CROSS"
        return result

    result["cross"] = cross

    box = build_box(
        candles,
        cross
    )

    if not box:
        result["reason"] = "BOX FAILED"
        return result

    result["box"] = box

    breakout = find_breakout(
        candles,
        cross,
        box
    )

    if not breakout:
        result["reason"] = "NO BREAKOUT"
        return result

    result["breakout"] = breakout

    swing = find_latest_swing(
        candles,
        cross,
        breakout
    )

    if not swing:
        result["reason"] = "NO CONFIRMED SWING"
        return result

    result["swing"] = swing

    # --------------------------------------------------------
    # Make sure swing direction matches breakout.
    # --------------------------------------------------------

    if breakout["type"] == "BUY":

        entry = breakout["close"]

        sl = swing["price"] * (
            1.0 - SL_BUFFER_PERCENT
        )

        risk = entry - sl

        if risk <= 0:
            result["reason"] = "INVALID BUY SL"
            return result

        tp = entry + (
            box["width"]
            * TP_BOX_PERCENT
        )

    else:

        entry = breakout["close"]

        sl = swing["price"] * (
            1.0 + SL_BUFFER_PERCENT
        )

        risk = sl - entry

        if risk <= 0:
            result["reason"] = "INVALID SELL SL"
            return result

        tp = entry - (
            box["width"]
            * TP_BOX_PERCENT
        )

    if breakout["type"] == "BUY" and tp <= entry:
        result["reason"] = "INVALID BUY TP"
        return result

    if breakout["type"] == "SELL" and tp >= entry:
        result["reason"] = "INVALID SELL TP"
        return result

    result["status"] = breakout["type"]

    result["entry"] = entry
    result["sl"] = sl
    result["tp"] = tp

    result["rr"] = (
        abs(tp - entry)
        / abs(entry - sl)
        if abs(entry - sl) > 0
        else 0
    )

    return result


# ============================================================
# OPEN TRADE
# ============================================================

def calculate_trade_pnl(trade, current_price):
    if not trade or current_price <= 0:
        return 0.0

    entry = safe_float(
        trade.get("entry")
    )

    if entry <= 0:
        return 0.0

    side = trade.get("side")

    if side == "BUY":
        return (
            (current_price - entry)
            / entry
        ) * 100.0

    return (
        (entry - current_price)
        / entry
    ) * 100.0


def check_open_trade(state, history):
    trade = state.get("open_trade")

    if not trade:
        return None

    symbol = trade.get("binance_symbol")

    if not symbol:
        return None

    current_price = get_binance_price(symbol)

    if current_price <= 0:
        return None

    trade["current_price"] = current_price

    pnl = calculate_trade_pnl(
        trade,
        current_price
    )

    trade["pnl_percent"] = pnl

    side = trade.get("side")

    tp = safe_float(
        trade.get("tp")
    )

    sl = safe_float(
        trade.get("sl")
    )

    exit_reason = None
    exit_price = None

    if side == "BUY":

        if current_price >= tp:
            exit_reason = "TP"
            exit_price = current_price

        elif current_price <= sl:
            exit_reason = "SL"
            exit_price = current_price

    elif side == "SELL":

        if current_price <= tp:
            exit_reason = "TP"
            exit_price = current_price

        elif current_price >= sl:
            exit_reason = "SL"
            exit_price = current_price

    if exit_reason:

        final_pnl = calculate_trade_pnl(
            trade,
            exit_price
        )

        closed = dict(trade)

        closed["exit_price"] = exit_price
        closed["exit_time"] = now_iso()
        closed["exit_reason"] = exit_reason
        closed["final_pnl_percent"] = final_pnl

        history.append(closed)

        state["open_trade"] = None

        return {
            "closed": True,
            "reason": exit_reason,
            "pnl": final_pnl,
            "trade": closed
        }

    return {
        "closed": False,
        "reason": None,
        "pnl": pnl,
        "trade": trade
    }


# ============================================================
# OPEN NEW TRADE
# ============================================================

def open_trade_from_signal(
    state,
    signal,
    lbank_market
):
    if state.get("open_trade"):
        return False

    side = signal["status"]

    trade = {
        "id": f"{signal['symbol']}_{int(time.time())}",
        "symbol": signal["symbol"],
        "lbank_symbol": lbank_market.get(
            "lbank_symbol"
        ),
        "binance_symbol": signal["symbol"],

        "side": side,

        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],

        "box_high": signal["box"]["high"],
        "box_low": signal["box"]["low"],
        "box_width": signal["box"]["width"],

        "cross_time": ms_to_iso(
            signal["cross"]["open_time"]
        ),

        "breakout_time": ms_to_iso(
            signal["breakout"]["open_time"]
        ),

        "swing_time": ms_to_iso(
            signal["swing"]["open_time"]
        ),

        "volume_ratio": signal[
            "volume_ratio"
        ],

        "opened_at": now_iso()
    }

    state["open_trade"] = trade

    return True


# ============================================================
# PERFORMANCE
# ============================================================

def performance_summary(history):
    trades = len(history)

    wins = 0
    losses = 0
    breakeven = 0

    total_pnl = 0.0

    for trade in history:

        pnl = safe_float(
            trade.get(
                "final_pnl_percent"
            )
        )

        total_pnl += pnl

        if pnl > 0:
            wins += 1

        elif pnl < 0:
            losses += 1

        else:
            breakeven += 1

    if trades > 0:
        win_rate = (
            wins / trades
        ) * 100.0
    else:
        win_rate = 0.0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "total_pnl": total_pnl
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN:
        print("[WARN] TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[WARN] TELEGRAM_CHAT_ID missing")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        r = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        return True

    except Exception as e:
        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


# ============================================================
# REPORT
# ============================================================

def build_report(
    markets,
    valid_count,
    analyses,
    state,
    history,
    binance_supported,
    closed_trade
):
    perf = performance_summary(history)

    latest_cross_buy = 0
    latest_cross_sell = 0
    latest_cross_none = 0

    box_ready = 0
    box_failed = 0

    breakout_buy = 0
    breakout_sell = 0
    breakout_none = 0

    swing_valid = 0
    swing_failed = 0

    for item in analyses:

        cross = item.get("cross")

        if not cross:
            latest_cross_none += 1

        elif cross["type"] == "BUY":
            latest_cross_buy += 1

        elif cross["type"] == "SELL":
            latest_cross_sell += 1

        if item.get("box"):
            box_ready += 1
        else:
            box_failed += 1

        breakout = item.get("breakout")

        if not breakout:
            breakout_none += 1

        elif breakout["type"] == "BUY":
            breakout_buy += 1

        elif breakout["type"] == "SELL":
            breakout_sell += 1

        if item.get("swing"):
            swing_valid += 1
        else:
            swing_failed += 1

    signal_candidates = [
        x for x in analyses
        if x.get("status")
        in ("BUY", "SELL")
    ]

    signal_candidates.sort(
        key=lambda x: (
            x.get("volume_ratio", 0),
            x.get("breakout", {}).get(
                "open_time", 0
            )
        ),
        reverse=True
    )

    lines = []

    lines.append(
        "📡 *LBANK FUTURES PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 {now_iso()}"
    )

    lines.append(
        f"⏱ *{TIMEFRAME} CLOSED | TOP {TOP_LBANK}*"
    )

    lines.append(
        "🤖 *TENKAN/KIJUN 9/26 + BOX 26*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("📊 *PERFORMANCE*")

    lines.append(
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['breakeven']}"
    )

    lines.append(
        f"🏆 WR: {perf['win_rate']:.1f}%"
    )

    lines.append(
        f"📈 Total P&L: "
        f"{fmt_pct(perf['total_pnl'])}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("🔎 *PIPELINE*")

    lines.append(
        f"LBank Futures: {len(markets)}"
    )

    lines.append(
        f"Binance supported: "
        f"{len(binance_supported)}"
    )

    lines.append(
        f"Kline Valid: {valid_count}"
    )

    lines.append(
        f"Latest Cross: "
        f"🟢 {latest_cross_buy} / "
        f"🔴 {latest_cross_sell} / "
        f"⚪ {latest_cross_none}"
    )

    lines.append(
        f"Box Ready: {box_ready} | "
        f"Failed: {box_failed}"
    )

    lines.append(
        f"Breakout: "
        f"🟢 {breakout_buy} / "
        f"🔴 {breakout_sell} / "
        f"⚪ {breakout_none}"
    )

    lines.append(
        f"Swing: {swing_valid} | "
        f"Failed: {swing_failed}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # CLOSED TRADE
    # --------------------------------------------------------

    if closed_trade:
        lines.append("🔔 *TRADE CLOSED*")

        emoji = (
            "🟢"
            if closed_trade["pnl"] > 0
            else "🔴"
            if closed_trade["pnl"] < 0
            else "⚪"
        )

        lines.append(
            f"{emoji} "
            f"{closed_trade['trade']['symbol']} "
            f"{closed_trade['reason']}"
        )

        lines.append(
            f"P&L: "
            f"{fmt_pct(closed_trade['pnl'])}"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------------

    open_trade = state.get("open_trade")

    if open_trade:

        lines.append("🔴 *OPEN TRADE*")

        lines.append(
            f"{open_trade['symbol']} "
            f"{open_trade['side']}"
        )

        lines.append(
            f"Entry: {fmt_price(open_trade['entry'])}"
        )

        lines.append(
            f"SL: {fmt_price(open_trade['sl'])}"
        )

        lines.append(
            f"TP: {fmt_price(open_trade['tp'])}"
        )

        lines.append(
            f"Current: "
            f"{fmt_price(open_trade.get('current_price'))}"
        )

        lines.append(
            f"P&L: "
            f"{fmt_pct(open_trade.get('pnl_percent'))}"
        )

        lines.append(
            f"Box High: "
            f"{fmt_price(open_trade['box_high'])}"
        )

        lines.append(
            f"Box Low: "
            f"{fmt_price(open_trade['box_low'])}"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    else:

        lines.append(
            "OPEN TRADE: *NONE*"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    if signal_candidates:

        best = signal_candidates[0]

        emoji = (
            "🟢"
            if best["status"] == "BUY"
            else "🔴"
        )

        lines.append(
            f"{emoji} *NEW SIGNAL*"
        )

        lines.append(
            f"*{best['symbol']}* "
            f"{best['status']}"
        )

        lines.append(
            f"Entry: {fmt_price(best['entry'])}"
        )

        lines.append(
            f"SL: {fmt_price(best['sl'])}"
        )

        lines.append(
            f"TP: {fmt_price(best['tp'])}"
        )

        lines.append(
            f"RR: {best['rr']:.2f}"
        )

        lines.append(
            f"Box High: "
            f"{fmt_price(best['box']['high'])}"
        )

        lines.append(
            f"Box Low: "
            f"{fmt_price(best['box']['low'])}"
        )

        lines.append(
            f"Box Width: "
            f"{fmt_price(best['box']['width'])}"
        )

        lines.append(
            f"Cross: "
            f"{ms_to_iso(best['cross']['open_time'])}"
        )

        lines.append(
            f"Breakout: "
            f"{ms_to_iso(best['breakout']['open_time'])}"
        )

        lines.append(
            f"Swing: "
            f"{fmt_price(best['swing']['price'])}"
        )

        lines.append(
            f"Volume Ratio: "
            f"{best['volume_ratio']:.2f}x"
        )

    else:

        lines.append(
            "⚪ *NO VALID SIGNAL*"
        )

        # ----------------------------------------------------
        # Reasons
        # ----------------------------------------------------

        reason_count = {}

        for item in analyses:

            reason = item.get(
                "reason",
                "UNKNOWN"
            )

            reason_count[reason] = (
                reason_count.get(reason, 0)
                + 1
            )

        sorted_reasons = sorted(
            reason_count.items(),
            key=lambda x: x[1],
            reverse=True
        )

        if sorted_reasons:

            lines.append(
                "Main reasons:"
            )

            for reason, count in sorted_reasons[:5]:

                lines.append(
                    f"• {reason}: {count}"
                )

    # --------------------------------------------------------
    # TOP DIAGNOSTICS
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔍 *TOP DIAGNOSTICS*"
    )

    diagnostic_items = sorted(
        analyses,
        key=lambda x: x.get(
            "volume_ratio",
            0
        ),
        reverse=True
    )

    shown = 0

    for item in diagnostic_items:

        if shown >= 10:
            break

        symbol = item.get(
            "symbol",
            "?"
        )

        status = item.get(
            "status",
            "?"
        )

        reason = item.get(
            "reason",
            ""
        )

        volume_ratio = item.get(
            "volume_ratio",
            0
        )

        if status in ("BUY", "SELL"):

            lines.append(
                f"• {symbol} "
                f"→ {status} "
                f"| Vol {volume_ratio:.2f}x"
            )

        else:

            lines.append(
                f"• {symbol} "
                f"→ {reason}"
            )

        shown += 1

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("LBANK FUTURES + BINANCE FUTURES PRICE ACTION SCANNER")
    print("=" * 70)

    print(
        f"Timeframe: {TIMEFRAME}"
    )

    print(
        f"TOP LBank: {TOP_LBANK}"
    )

    print(
        f"Box: {BOX_SIZE} candles"
    )

    print(
        f"Max Open Trades: {MAX_OPEN_TRADES}"
    )

    print("=" * 70)

    state = load_state()
    history = load_history()

    # ========================================================
    # 1. Check existing trade first
    # ========================================================

    closed_trade = check_open_trade(
        state,
        history
    )

    if closed_trade:
        print(
            "[TRADE CLOSED] "
            f"{closed_trade['reason']} "
            f"P&L={closed_trade['pnl']:.2f}%"
        )

        save_history(history)

    # ========================================================
    # 2. Get LBank Futures markets
    # ========================================================

    all_markets = get_lbank_market_data()

    print(
        f"[INFO] LBank Futures Markets: "
        f"{len(all_markets)}"
    )

    if not all_markets:

        print(
            "[ERROR] No LBank Futures markets."
        )

        telegram_send(
            "❌ *SCANNER ERROR*\n"
            "LBank Futures marketData returned no markets."
        )

        save_state(state)

        return

    top_markets = rank_lbank_markets(
        all_markets
    )

    print(
        f"[INFO] TOP {TOP_LBANK}:"
    )

    for i, market in enumerate(
        top_markets,
        start=1
    ):
        print(
            f"{i:02d}. "
            f"{market['symbol']} "
            f"turnover={market['turnover']:.2f}"
        )

    # ========================================================
    # 3. Binance Futures symbols
    # ========================================================

    binance_symbols = get_binance_symbols()

    print(
        f"[INFO] Binance Futures symbols: "
        f"{len(binance_symbols)}"
    )

    # ========================================================
    # 4. Analyze TOP 30
    # ========================================================

    analyses = []

    valid_count = 0

    for market in top_markets:

        symbol = normalize_symbol(
            market["symbol"]
        )

        if symbol not in binance_symbols:

            analyses.append(
                {
                    "symbol": symbol,
                    "lbank_symbol": market.get(
                        "lbank_symbol"
                    ),
                    "status": "NO SIGNAL",
                    "reason": (
                        "NOT ON BINANCE FUTURES"
                    ),
                    "cross": None,
                    "box": None,
                    "breakout": None,
                    "swing": None,
                    "volume_ratio": 0,
                    "candles": 0
                }
            )

            print(
                f"[SKIP] {symbol} "
                f"-> NOT ON BINANCE FUTURES"
            )

            continue

        candles = get_binance_klines(
            symbol
        )

        if not candles:

            analyses.append(
                {
                    "symbol": symbol,
                    "lbank_symbol": market.get(
                        "lbank_symbol"
                    ),
                    "status": "NO SIGNAL",
                    "reason": "BAD BINANCE KLINE",
                    "cross": None,
                    "box": None,
                    "breakout": None,
                    "swing": None,
                    "volume_ratio": 0,
                    "candles": 0
                }
            )

            print(
                f"[BAD KLINE] {symbol}"
            )

            continue

        valid_count += 1

        result = analyze_symbol(
            market,
            candles
        )

        analyses.append(result)

        print(
            f"[SCAN] "
            f"{symbol} | "
            f"{result['status']} | "
            f"{result['reason']}"
        )

        if result["status"] in (
            "BUY",
            "SELL"
        ):

            print(
                f"       Entry="
                f"{fmt_price(result['entry'])} "
                f"SL="
                f"{fmt_price(result['sl'])} "
                f"TP="
                f"{fmt_price(result['tp'])}"
            )

    # ========================================================
    # 5. Signal candidates
    # ========================================================

    candidates = [
        x for x in analyses
        if x.get("status")
        in ("BUY", "SELL")
    ]

    candidates.sort(
        key=lambda x: (
            x.get("volume_ratio", 0),
            x.get("breakout", {}).get(
                "open_time",
                0
            )
        ),
        reverse=True
    )

    # ========================================================
    # 6. Open maximum ONE trade
    # ========================================================

    if (
        not state.get("open_trade")
        and candidates
    ):

        best = candidates[0]

        matching_market = None

        for market in top_markets:

            if normalize_symbol(
                market["symbol"]
            ) == best["symbol"]:

                matching_market = market
                break

        if matching_market:

            opened = open_trade_from_signal(
                state,
                best,
                matching_market
            )

            if opened:

                print(
                    "[NEW TRADE] "
                    f"{best['symbol']} "
                    f"{best['status']} "
                    f"Entry={best['entry']}"
                )

                # Recalculate open trade current P&L
                current_price = get_binance_price(
                    best["symbol"]
                )

                state["open_trade"][
                    "current_price"
                ] = current_price

                state["open_trade"][
                    "pnl_percent"
                ] = calculate_trade_pnl(
                    state["open_trade"],
                    current_price
                )

    elif state.get("open_trade"):

        print(
            "[INFO] Open trade exists. "
            "No new trade will be opened."
        )

    else:

        print(
            "[INFO] No valid signal."
        )

    # ========================================================
    # 7. Save
    # ========================================================

    save_history(history)
    save_state(state)

    # ========================================================
    # 8. Telegram report
    # ========================================================

    report = build_report(
        markets=top_markets,
        valid_count=valid_count,
        analyses=analyses,
        state=state,
        history=history,
        binance_supported=binance_symbols,
        closed_trade=closed_trade
    )

    print("=" * 70)
    print(report)
    print("=" * 70)

    telegram_send(report)

    print(
        "[DONE]"
    )


if __name__ == "__main__":
    main()
