# ============================================================
# VOLUME-KHAT 100 v3.1
# ============================================================
# KRAKEN FUTURES
#
# Strategy:
#   1H  = Trend
#   15M = Volume + Structure Setup
#   5M  = Closed Candle Confirmation
#
# Philosophy:
#   Selective but not artificially throttled.
#
# IMPORTANT:
#   - Uses CLOSED candles only
#   - Max 3 OPEN trades
#   - No global 60-minute signal limiter
#   - Per-symbol cooldown only
#   - Existing SQLite DB is migrated automatically
#   - Historical trades are preserved
# ============================================================

import os
import time
import math
import sqlite3
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

TOP_N = 100

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

OHLCV_LIMIT = 150

# ------------------------------------------------------------
# VOLUME FILTERS
# ------------------------------------------------------------

RVOL_PERIOD = 20

# Slightly relaxed only at SETUP stage
RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# TRADE QUALITY
# ------------------------------------------------------------

MIN_SIGNAL_SCORE = 9

MIN_RR = 2.0
MAX_SL_PCT = 0.0080       # 0.80%

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

# Per-symbol cooldown only
COOLDOWN_CANDLES = 3

# ------------------------------------------------------------
# STRUCTURE
# ------------------------------------------------------------

SWING_LEFT = 2
SWING_RIGHT = 2

ATR_PERIOD = 14
ATR_BUFFER_MULT = 0.15

# 5M confirmation
MIN_BODY_RATIO_5M = 0.30

# ------------------------------------------------------------
# PAPER
# ------------------------------------------------------------

PAPER_TRADING = True

# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

DB_FILE = "volume_khat_100.db"

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/3.1"
})

# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

DIAG = {
    "markets": 0,
    "data_ok": 0,
    "data_errors": 0,

    "trend": 0,
    "neutral": 0,

    "setup": 0,
    "no_setup": 0,

    "confirmation": 0,
    "no_confirmation": 0,

    "valid_risk": 0,
    "invalid_risk": 0,

    "score_pass": 0,
    "low_score": 0,

    "final": 0,

    "risk_reasons": {}
}

# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iran_now():
    return utc_now() + timedelta(hours=3, minutes=30)


def format_iran_time():
    dt = iran_now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def to_persian_digits(value):
    if value is None:
        return ""

    table = str.maketrans(
        "0123456789",
        "۰۱۲۳۴۵۶۷۸۹"
    )

    return str(value).translate(table)


# ============================================================
# HTTP
# ============================================================

def get_json(url, params=None, timeout=20):

    try:

        r = SESSION.get(
            url,
            params=params,
            timeout=timeout
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:

        return None


# ============================================================
# KRAKEN MARKETS
# ============================================================

def get_markets():

    url = BASE + "/derivatives/api/v3/instruments"

    data = get_json(url)

    if not data:
        return []

    instruments = data.get("instruments", [])

    markets = []

    for item in instruments:

        symbol = item.get("symbol")

        if not symbol:
            continue

        # Only perpetual USD contracts
        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        markets.append(symbol)

    return markets


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    url = BASE + "/derivatives/api/v3/tickers"

    data = get_json(url)

    if not data:
        return {}

    result = data.get("tickers", [])

    output = {}

    for item in result:

        symbol = item.get("symbol")

        if not symbol:
            continue

        output[symbol] = item

    return output


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=None):

    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


# ============================================================
# MARKET VOLUME
# ============================================================

def ticker_volume(ticker):

    if not ticker:
        return 0.0

    for key in [
        "vol24h",
        "volume24h",
        "volume"
    ]:

        value = safe_float(
            ticker.get(key)
        )

        if value is not None:
            return value

    return 0.0


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    instruments = get_markets()

    tickers = get_tickers()

    ranked = []

    for symbol in instruments:

        ticker = tickers.get(symbol)

        volume = ticker_volume(ticker)

        ranked.append(
            (
                symbol,
                volume
            )
        )

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        x[0]
        for x in ranked[:TOP_N]
    ]


# ============================================================
# CHART DATA
# ============================================================

def fetch_ohlcv(symbol, interval):

    url = (
        BASE
        + "/api/charts/v1/trade/"
        + symbol
        + "/"
        + interval
    )

    data = get_json(
        url,
        timeout=20
    )

    if not data:
        return []

    candles = (
        data.get("candles")
        or data.get("data")
        or []
    )

    result = []

    for c in candles:

        try:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("t")
                )

                o = (
                    c.get("open")
                    or c.get("o")
                )

                h = (
                    c.get("high")
                    or c.get("h")
                )

                l = (
                    c.get("low")
                    or c.get("l")
                )

                close = (
                    c.get("close")
                    or c.get("c")
                )

                volume = (
                    c.get("volume")
                    or c.get("v")
                    or 0
                )

            else:

                # Defensive support for list format
                if len(c) < 6:
                    continue

                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                close = c[4]
                volume = c[5]

            ts = safe_float(ts)
            o = safe_float(o)
            h = safe_float(h)
            l = safe_float(l)
            close = safe_float(close)
            volume = safe_float(volume, 0)

            if None in [
                ts,
                o,
                h,
                l,
                close
            ]:
                continue

            # Kraken timestamps can be ms or seconds
            if ts > 100000000000:
                ts /= 1000

            result.append({
                "time": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": volume
            })

        except Exception:
            continue

    result.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------
    #
    # Last candle can still be forming.
    # Drop it deliberately.
    #
    if len(result) > 2:
        result = result[:-1]

    return result[-OHLCV_LIMIT:]


# ============================================================
# SMA
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return sum(
        values[-period:]
    ) / period


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=ATR_PERIOD):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        prev_close = previous["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(candles, period=RVOL_PERIOD):

    if len(candles) < period + 1:
        return None

    current_volume = candles[-1]["volume"]

    previous = [
        c["volume"]
        for c in candles[-period-1:-1]
    ]

    if not previous:
        return None

    avg_volume = sum(previous) / len(previous)

    if avg_volume <= 0:
        return None

    return current_volume / avg_volume


# ============================================================
# SWINGS
# ============================================================

def find_swing_highs(
    candles,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    result = []

    if len(candles) < left + right + 1:
        return result

    for i in range(
        left,
        len(candles) - right
    ):

        high = candles[i]["high"]

        is_swing = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= high:
                is_swing = False
                break

        if is_swing:
            result.append(
                (
                    i,
                    high
                )
            )

    return result


def find_swing_lows(
    candles,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    result = []

    if len(candles) < left + right + 1:
        return result

    for i in range(
        left,
        len(candles) - right
    ):

        low = candles[i]["low"]

        is_swing = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["low"] <= low:
                is_swing = False
                break

        if is_swing:
            result.append(
                (
                    i,
                    low
                )
            )

    return result


# ============================================================
# STRUCTURE LEVELS
# ============================================================

def recent_resistance(candles, price):

    swings = find_swing_highs(candles)

    levels = [
        value
        for _, value in swings
        if value > price
    ]

    if not levels:
        return None

    return min(levels)


def recent_support(candles, price):

    swings = find_swing_lows(candles)

    levels = [
        value
        for _, value in swings
        if value < price
    ]

    if not levels:
        return None

    return max(levels)


# ============================================================
# TREND
# ============================================================

def detect_trend(candles):

    if len(candles) < 60:
        return "NEUTRAL"

    closes = [
        c["close"]
        for c in candles
    ]

    sma20 = sma(
        closes,
        20
    )

    sma50 = sma(
        closes,
        50
    )

    if sma20 is None or sma50 is None:
        return "NEUTRAL"

    last = closes[-1]

    if (
        sma20 > sma50
        and last > sma20
    ):
        return "LONG"

    if (
        sma20 < sma50
        and last < sma20
    ):
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# MARKET STRUCTURE
# ============================================================

def structure_direction(candles):

    if len(candles) < 20:
        return "NEUTRAL"

    highs = find_swing_highs(candles)
    lows = find_swing_lows(candles)

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    h1 = highs[-2][1]
    h2 = highs[-1][1]

    l1 = lows[-2][1]
    l2 = lows[-1][1]

    if h2 > h1 and l2 > l1:
        return "LONG"

    if h2 < h1 and l2 < l1:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# CANDLE ANALYSIS
# ============================================================

def candle_info(candle):

    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    total_range = h - l

    if total_range <= 0:
        return {
            "body": 0,
            "body_ratio": 0,
            "upper_wick": 0,
            "lower_wick": 0,
            "bull": False,
            "bear": False
        }

    body = abs(c - o)

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return {
        "body": body,
        "body_ratio": body / total_range,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "bull": c > o,
        "bear": c < o
    }


# ============================================================
# BREAKOUT SETUP
# ============================================================

def breakout_setup(candles, trend):

    if len(candles) < 30:
        return None

    last = candles[-1]

    previous_high = max(
        c["high"]
        for c in candles[-21:-1]
    )

    previous_low = min(
        c["low"]
        for c in candles[-21:-1]
    )

    rvol = calculate_rvol(candles)

    if rvol is None:
        return None

    info = candle_info(last)

    # --------------------------------------------------------
    # LONG BREAKOUT
    # --------------------------------------------------------

    if (
        trend == "LONG"
        and last["close"] > previous_high
        and rvol >= RVOL_ABNORMAL
        and info["bull"]
    ):

        return {
            "side": "LONG",
            "type": "BREAKOUT",
            "rvol": rvol
        }

    # --------------------------------------------------------
    # SHORT BREAKOUT
    # --------------------------------------------------------

    if (
        trend == "SHORT"
        and last["close"] < previous_low
        and rvol >= RVOL_ABNORMAL
        and info["bear"]
    ):

        return {
            "side": "SHORT",
            "type": "BREAKOUT",
            "rvol": rvol
        }

    return None


# ============================================================
# REJECTION SETUP
# ============================================================

def rejection_setup(candles, trend):

    if len(candles) < 30:
        return None

    last = candles[-1]

    rvol = calculate_rvol(candles)

    if rvol is None:
        return None

    info = candle_info(last)

    resistance = recent_resistance(
        candles[:-1],
        last["close"]
    )

    support = recent_support(
        candles[:-1],
        last["close"]
    )

    # --------------------------------------------------------
    # LONG REJECTION
    # --------------------------------------------------------

    if trend == "LONG" and support is not None:

        distance = abs(
            last["low"] - support
        )

        atr = calculate_atr(candles)

        if atr and distance <= atr * 1.0:

            if (
                info["bull"]
                and info["lower_wick"] > info["body"] * 1.2
                and rvol >= RVOL_ABNORMAL
            ):

                return {
                    "side": "LONG",
                    "type": "REJECTION",
                    "rvol": rvol
                }

    # --------------------------------------------------------
    # SHORT REJECTION
    # --------------------------------------------------------

    if trend == "SHORT" and resistance is not None:

        distance = abs(
            resistance - last["high"]
        )

        atr = calculate_atr(candles)

        if atr and distance <= atr * 1.0:

            if (
                info["bear"]
                and info["upper_wick"] > info["body"] * 1.2
                and rvol >= RVOL_ABNORMAL
            ):

                return {
                    "side": "SHORT",
                    "type": "REJECTION",
                    "rvol": rvol
                }

    return None


# ============================================================
# SETUP
# ============================================================

def detect_setup(candles, trend):

    setup = breakout_setup(
        candles,
        trend
    )

    if setup:
        return setup

    return rejection_setup(
        candles,
        trend
    )


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(candles, side):

    if len(candles) < 3:
        return False

    last = candles[-1]

    info = candle_info(last)

    if info["body_ratio"] < MIN_BODY_RATIO_5M:
        return False

    if side == "LONG":

        if not info["bull"]:
            return False

        previous_high = max(
            c["high"]
            for c in candles[-3:-1]
        )

        if last["close"] < previous_high:
            return False

        return True

    if side == "SHORT":

        if not info["bear"]:
            return False

        previous_low = min(
            c["low"]
            for c in candles[-3:-1]
        )

        if last["close"] > previous_low:
            return False

        return True

    return False


# ============================================================
# STOP LOSS
# ============================================================

def calculate_structural_sl(
    candles15,
    candles1h,
    side,
    entry
):

    atr15 = calculate_atr(
        candles15
    )

    atr1h = calculate_atr(
        candles1h
    )

    if atr15 is None:
        return None

    if side == "LONG":

        supports = []

        s15 = recent_support(
            candles15,
            entry
        )

        s1h = recent_support(
            candles1h,
            entry
        )

        if s15 is not None:
            supports.append(s15)

        if s1h is not None:
            supports.append(s1h)

        if not supports:
            return None

        base = max(supports)

        atr = max(
            atr15,
            atr1h or 0
        )

        sl = (
            base
            - atr * ATR_BUFFER_MULT
        )

        if sl >= entry:
            return None

        return sl

    if side == "SHORT":

        resistances = []

        r15 = recent_resistance(
            candles15,
            entry
        )

        r1h = recent_resistance(
            candles1h,
            entry
        )

        if r15 is not None:
            resistances.append(r15)

        if r1h is not None:
            resistances.append(r1h)

        if not resistances:
            return None

        base = min(resistances)

        atr = max(
            atr15,
            atr1h or 0
        )

        sl = (
            base
            + atr * ATR_BUFFER_MULT
        )

        if sl <= entry:
            return None

        return sl

    return None


# ============================================================
# STRUCTURAL TAKE PROFIT
# ============================================================

def calculate_structural_tp(
    candles15,
    candles1h,
    side,
    entry,
    sl
):

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        return None

    if side == "LONG":

        levels = []

        for candles in [
            candles15,
            candles1h
        ]:

            swings = find_swing_highs(
                candles
            )

            for _, level in swings:

                if level > entry:
                    levels.append(level)

        levels = sorted(
            set(levels)
        )

        for level in levels:

            rr = (
                level - entry
            ) / risk

            if rr >= MIN_RR:
                return level

        return None

    if side == "SHORT":

        levels = []

        for candles in [
            candles15,
            candles1h
        ]:

            swings = find_swing_lows(
                candles
            )

            for _, level in swings:

                if level < entry:
                    levels.append(level)

        levels = sorted(
            set(levels),
            reverse=True
        )

        for level in levels:

            rr = (
                entry - level
            ) / risk

            if rr >= MIN_RR:
                return level

        return None

    return None


# ============================================================
# TRADE BUILD
# ============================================================

def build_trade(
    symbol,
    side,
    setup_type,
    rvol,
    candles1h,
    candles15,
    candles5
):

    entry = candles5[-1]["close"]

    sl = calculate_structural_sl(
        candles15,
        candles1h,
        side,
        entry
    )

    if sl is None:

        DIAG["risk_reasons"]["NO_SL"] = (
            DIAG["risk_reasons"].get(
                "NO_SL",
                0
            ) + 1
        )

        return None

    if side == "LONG":

        sl_pct = (
            entry - sl
        ) / entry

    else:

        sl_pct = (
            sl - entry
        ) / entry

    if sl_pct <= 0:

        DIAG["risk_reasons"]["BAD_SL"] = (
            DIAG["risk_reasons"].get(
                "BAD_SL",
                0
            ) + 1
        )

        return None

    if sl_pct > MAX_SL_PCT:

        DIAG["risk_reasons"]["SL_OVER_0_8"] = (
            DIAG["risk_reasons"].get(
                "SL_OVER_0_8",
                0
            ) + 1
        )

        return None

    tp = calculate_structural_tp(
        candles15,
        candles1h,
        side,
        entry,
        sl
    )

    if tp is None:

        DIAG["risk_reasons"]["NO_STRUCTURAL_TP_2R"] = (
            DIAG["risk_reasons"].get(
                "NO_STRUCTURAL_TP_2R",
                0
            ) + 1
        )

        return None

    if side == "LONG":

        risk = entry - sl
        reward = tp - entry

    else:

        risk = sl - entry
        reward = entry - tp

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk

    if rr < MIN_RR:

        DIAG["risk_reasons"]["RR_LOW"] = (
            DIAG["risk_reasons"].get(
                "RR_LOW",
                0
            ) + 1
        )

        return None

    DIAG["valid_risk"] += 1

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # Trend
    score += 3

    # Setup
    score += 4

    # Volume
    if rvol >= RVOL_VERY_STRONG:
        score += 4

    elif rvol >= RVOL_STRONG:
        score += 4

    elif rvol >= RVOL_ABNORMAL:
        score += 3

    # Confirmation
    score += 4

    if score < MIN_SIGNAL_SCORE:

        DIAG["low_score"] += 1

        return None

    DIAG["score_pass"] += 1

    return {
        "symbol": symbol,
        "side": side,
        "setup": setup_type,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "score": score,
        "rvol": rvol
    }


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return conn


# ============================================================
# DATABASE MIGRATION
# ============================================================

def init_db():

    conn = db_connect()

    cur = conn.cursor()

    # --------------------------------------------------------
    # Create table if it does not exist
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT,

            side TEXT,

            setup TEXT,

            entry REAL,

            sl REAL,

            tp REAL,

            rr REAL,

            score REAL,

            status TEXT,

            result TEXT,

            pnl REAL,

            entry_time TEXT,

            exit_time TEXT,

            exit_reason TEXT,

            closed_reported INTEGER DEFAULT 0,

            created_at TEXT

        )
    """)

    conn.commit()

    # --------------------------------------------------------
    # Read existing schema
    # --------------------------------------------------------

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    columns = {
        row[1]
        for row in cur.fetchall()
    }

    # --------------------------------------------------------
    # Automatic migration
    # --------------------------------------------------------

    required_columns = {

        "symbol":
            "TEXT",

        "side":
            "TEXT",

        "setup":
            "TEXT",

        "entry":
            "REAL",

        "sl":
            "REAL",

        "tp":
            "REAL",

        "rr":
            "REAL",

        "score":
            "REAL",

        "status":
            "TEXT",

        "result":
            "TEXT",

        "pnl":
            "REAL",

        "entry_time":
            "TEXT",

        "exit_time":
            "TEXT",

        "exit_reason":
            "TEXT",

        "closed_reported":
            "INTEGER DEFAULT 0",

        "created_at":
            "TEXT"
    }

    for column, definition in required_columns.items():

        if column not in columns:

            try:

                cur.execute(
                    f"""
                    ALTER TABLE trades
                    ADD COLUMN {column} {definition}
                    """
                )

                print(
                    f"DB MIGRATION: added column {column}"
                )

            except Exception as e:

                print(
                    f"DB MIGRATION ERROR {column}: {e}"
                )

    conn.commit()

    # --------------------------------------------------------
    # Repair NULL values introduced by old schema
    # --------------------------------------------------------

    try:

        cur.execute("""
            UPDATE trades
            SET closed_reported = 0
            WHERE closed_reported IS NULL
        """)

        conn.commit()

    except Exception as e:

        print(
            "DB NULL repair error:",
            e
        )

    # --------------------------------------------------------
    # Indexes
    # --------------------------------------------------------

    indexes = [

        (
            "idx_trades_status",
            "CREATE INDEX IF NOT EXISTS "
            "idx_trades_status "
            "ON trades(status)"
        ),

        (
            "idx_trades_symbol",
            "CREATE INDEX IF NOT EXISTS "
            "idx_trades_symbol "
            "ON trades(symbol)"
        ),

        (
            "idx_trades_closed_reported",
            "CREATE INDEX IF NOT EXISTS "
            "idx_trades_closed_reported "
            "ON trades(closed_reported)"
        )
    ]

    for _, sql in indexes:

        try:
            cur.execute(sql)
        except Exception:
            pass

    conn.commit()

    conn.close()


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason,
            closed_reported,
            created_at
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# OPEN COUNT
# ============================================================

def count_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """)

    count = cur.fetchone()[0]

    conn.close()

    return count


# ============================================================
# COOLDOWN
# ============================================================

def symbol_on_cooldown(symbol):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT entry_time
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()

    conn.close()

    if not row or not row[0]:
        return False

    try:

        entry_dt = datetime.fromisoformat(
            row[0]
        )

        if entry_dt.tzinfo is None:

            entry_dt = entry_dt.replace(
                tzinfo=timezone.utc
            )

        # 3 x 5-minute candles
        seconds = COOLDOWN_CANDLES * 5 * 60

        return (
            utc_now() - entry_dt
        ).total_seconds() < seconds

    except Exception:

        return False


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(trade):

    symbol = trade["symbol"]

    # No duplicate OPEN trade
    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
    """, (symbol,))

    exists = cur.fetchone()

    if exists:

        conn.close()

        return False

    # Per-symbol cooldown
    cur.execute("""
        SELECT entry_time
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()

    if row and row[0]:

        try:

            last_time = datetime.fromisoformat(
                row[0]
            )

            if last_time.tzinfo is None:

                last_time = last_time.replace(
                    tzinfo=timezone.utc
                )

            elapsed = (
                utc_now() - last_time
            ).total_seconds()

            cooldown_seconds = (
                COOLDOWN_CANDLES * 5 * 60
            )

            if elapsed < cooldown_seconds:

                conn.close()

                return False

        except Exception:
            pass

    now = utc_now().isoformat()

    cur.execute("""
        INSERT INTO trades (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason,
            closed_reported,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            'OPEN',
            NULL,
            NULL,
            ?,
            NULL,
            NULL,
            0,
            ?
        )
    """, (
        trade["symbol"],
        trade["side"],
        trade["setup"],
        trade["entry"],
        trade["sl"],
        trade["tp"],
        trade["rr"],
        trade["score"],
        now,
        now
    ))

    conn.commit()

    conn.close()

    return True


# ============================================================
# MAX OPEN CLEANUP
# ============================================================

def cleanup_max_open():

    try:

        conn = db_connect()

        cur = conn.cursor()

        cur.execute("""
            SELECT
                id,
                symbol,
                entry_time
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY entry_time ASC
        """)

        rows = cur.fetchall()

        if len(rows) <= MAX_OPEN_TRADES:

            conn.close()

            return

        excess = (
            len(rows)
            - MAX_OPEN_TRADES
        )

        for row in rows[:excess]:

            trade_id = row[0]

            cur.execute("""
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = 'REMOVED',
                    pnl = 0,
                    exit_time = ?,
                    exit_reason = 'MAX_OPEN_CLEANUP',
                    closed_reported = 1
                WHERE id = ?
            """, (
                utc_now().isoformat(),
                trade_id
            ))

        conn.commit()

        conn.close()

    except Exception as e:

        print(
            "Max open cleanup error:",
            e
        )


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    exit_price
):

    if entry is None or exit_price is None:
        return 0.0

    if entry == 0:
        return 0.0

    if side == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100

    return (
        (entry - exit_price)
        / entry
    ) * 100


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    try:

        open_trades = get_open_trades()

        if not open_trades:
            return

        conn = db_connect()

        cur = conn.cursor()

        for row in open_trades:

            (
                trade_id,
                symbol,
                side,
                setup,
                entry,
                sl,
                tp,
                rr,
                score,
                status,
                result,
                pnl,
                entry_time,
                exit_time,
                exit_reason,
                closed_reported,
                created_at
            ) = row

            candles = fetch_ohlcv(
                symbol,
                TF_5M
            )

            if not candles:
                continue

            candle = candles[-1]

            high = candle["high"]
            low = candle["low"]

            exit_price = None
            result_value = None
            reason = None

            # ------------------------------------------------
            # SL FIRST
            # ------------------------------------------------

            if side == "LONG":

                if low <= sl:

                    exit_price = sl
                    result_value = "LOSS"
                    reason = "SL"

                elif high >= tp:

                    exit_price = tp
                    result_value = "WIN"
                    reason = "TP"

            else:

                if high >= sl:

                    exit_price = sl
                    result_value = "LOSS"
                    reason = "SL"

                elif low <= tp:

                    exit_price = tp
                    result_value = "WIN"
                    reason = "TP"

            if result_value is None:
                continue

            trade_pnl = calculate_pnl(
                side,
                entry,
                exit_price
            )

            cur.execute("""
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = ?,
                    pnl = ?,
                    exit_time = ?,
                    exit_reason = ?,
                    closed_reported = 0
                WHERE id = ?
            """, (
                result_value,
                trade_pnl,
                utc_now().isoformat(),
                reason,
                trade_id
            ))

        conn.commit()

        conn.close()

    except Exception as e:

        print(
            "Open trade update error:",
            e
        )


# ============================================================
# CLOSED UNREPORTED
# ============================================================

def get_unreported_closed():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason
        FROM trades
        WHERE status = 'CLOSED'
          AND closed_reported = 0
        ORDER BY exit_time ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# MARK CLOSED REPORTED
# ============================================================

def mark_closed_reported(ids):

    if not ids:
        return

    conn = db_connect()

    cur = conn.cursor()

    for trade_id in ids:

        cur.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE id = ?
        """, (
            trade_id,
        ))

    conn.commit()

    conn.close()


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN result = 'LOSS'
                    THEN 1
                    ELSE 0
                END
            ),
            COALESCE(
                SUM(
                    CASE
                        WHEN status = 'CLOSED'
                        THEN pnl
                        ELSE 0
                    END
                ),
                0
            )
        FROM trades
        WHERE status = 'CLOSED'
          AND result IN ('WIN', 'LOSS')
    """)

    row = cur.fetchone()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """)

    open_count = cur.fetchone()[0]

    conn.close()

    total = row[0] or 0
    wins = row[1] or 0
    losses = row[2] or 0
    pnl = row[3] or 0.0

    if total > 0:

        win_rate = (
            wins / total
        ) * 100

    else:

        win_rate = 0.0

    return {
        "open": open_count,
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": pnl
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def build_open_report():

    rows = get_open_trades()

    if not rows:

        return (
            "📂 OPEN TRADES\n"
            "None"
        )

    lines = [
        "📂 OPEN TRADES "
        f"({len(rows)}/{MAX_OPEN_TRADES})"
    ]

    for row in rows:

        (
            trade_id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason,
            closed_reported,
            created_at
        ) = row

        candles = fetch_ohlcv(
            symbol,
            TF_5M
        )

        if candles:

            now_price = candles[-1]["close"]

            current_pnl = calculate_pnl(
                side,
                entry,
                now_price
            )

        else:

            now_price = entry
            current_pnl = 0.0

        emoji = (
            "🟢"
            if current_pnl >= 0
            else "🔴"
        )

        lines.append(
            f"{emoji} {symbol} {side}"
        )

        lines.append(
            f"Entry: {fmt_price(entry)}"
            f" | Now: {fmt_price(now_price)}"
        )

        lines.append(
            f"PnL: {current_pnl:+.2f}%"
        )

        lines.append(
            f"SL: {fmt_price(sl)}"
            f" | TP: {fmt_price(tp)}"
        )

        lines.append(
            f"RR 1:{rr:.2f}"
        )

    return "\n".join(lines)


# ============================================================
# CLOSED REPORT
# ============================================================

def build_closed_report():

    rows = get_unreported_closed()

    if not rows:
        return "", []

    lines = [
        "🔔 CLOSED"
    ]

    ids = []

    for row in rows:

        (
            trade_id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason
        ) = row

        ids.append(trade_id)

        if result == "WIN":
            emoji = "✅"
        elif result == "LOSS":
            emoji = "❌"
        else:
            emoji = "⚪"

        lines.append(
            f"{emoji} {symbol} "
            f"{side} {result} "
            f"{pnl:+.2f}%"
        )

        lines.append(
            f"Entry {fmt_price(entry)}"
            f" → Exit {fmt_price("
                sl if exit_reason == "SL" else tp
            )}"
        )

        lines.append(
            f"Reason: {exit_reason}"
        )

    return "\n".join(lines), ids


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def build_diagnostic():

    reasons = DIAG["risk_reasons"]

    reason_text = ""

    if reasons:

        reason_parts = []

        for key, value in sorted(
            reasons.items(),
            key=lambda x: x[1],
            reverse=True
        ):

            reason_parts.append(
                f"{key}:{value}"
            )

        reason_text = (
            "\nRisk Reject: "
            + " | ".join(reason_parts)
        )

    return (
        "🧪 DIAGNOSTIC\n"
        f"Markets {DIAG['markets']} | "
        f"Data OK {DIAG['data_ok']}\n"
        f"Trend {DIAG['trend']} | "
        f"Neutral {DIAG['neutral']}\n"
        f"Setup {DIAG['setup']} | "
        f"No Setup {DIAG['no_setup']}\n"
        f"Confirmation {DIAG['confirmation']} | "
        f"No 5M Confirm {DIAG['no_confirmation']}\n"
        f"Valid SL/TP {DIAG['valid_risk']} | "
        f"Invalid SL/TP {DIAG['invalid_risk']}\n"
        f"Score ≥ {MIN_SIGNAL_SCORE}: "
        f"{DIAG['score_pass']} | "
        f"Low Score {DIAG['low_score']}\n"
        f"Data Errors {DIAG['data_errors']}"
        f"{reason_text}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID missing")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    try:

        response = SESSION.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=20
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(
            "Telegram error:",
            e
        )

        return False


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(trade):

    side_emoji = (
        "🟢"
        if trade["side"] == "LONG"
        else "🔴"
    )

    return (
        "🚨 VOLUME-KHAT SIGNAL\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{side_emoji} {trade['symbol']} "
        f"{trade['side']}\n"
        f"Setup: {trade['setup']}\n"
        f"Entry: {fmt_price(trade['entry'])}\n"
        f"SL: {fmt_price(trade['sl'])}\n"
        f"TP: {fmt_price(trade['tp'])}\n"
        f"RR: 1:{trade['rr']:.2f}\n"
        f"RVOL: {trade['rvol']:.2f}x\n"
        f"Score: {trade['score']}/{15}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ PAPER TRADING"
    )


# ============================================================
# RESET DIAGNOSTICS
# ============================================================

def reset_diagnostics():

    for key in DIAG:

        if key == "risk_reasons":

            DIAG[key] = {}

        else:

            DIAG[key] = 0


# ============================================================
# MAIN SCAN
# ============================================================

def scan_markets():

    reset_diagnostics()

    markets = get_top_markets()

    DIAG["markets"] = len(markets)

    print(
        f"TOP MARKETS: {len(markets)}"
    )

    candidates = []

    for index, symbol in enumerate(
        markets,
        start=1
    ):

        print(
            f"[{index}/{len(markets)}] "
            f"{symbol}"
        )

        try:

            candles1h = fetch_ohlcv(
                symbol,
                TF_1H
            )

            candles15 = fetch_ohlcv(
                symbol,
                TF_15M
            )

            candles5 = fetch_ohlcv(
                symbol,
                TF_5M
            )

            if (
                len(candles1h) < 60
                or len(candles15) < 40
                or len(candles5) < 30
            ):

                DIAG["data_errors"] += 1

                continue

            DIAG["data_ok"] += 1

            # ------------------------------------------------
            # TREND
            # ------------------------------------------------

            trend = detect_trend(
                candles1h
            )

            if trend == "NEUTRAL":

                DIAG["neutral"] += 1

                DIAG["no_setup"] += 1

                continue

            DIAG["trend"] += 1

            # ------------------------------------------------
            # SETUP
            # ------------------------------------------------

            setup = detect_setup(
                candles15,
                trend
            )

            if not setup:

                DIAG["no_setup"] += 1

                continue

            DIAG["setup"] += 1

            # ------------------------------------------------
            # CONFIRMATION
            # ------------------------------------------------

            confirmed = confirm_5m(
                candles5,
                setup["side"]
            )

            if not confirmed:

                DIAG["no_confirmation"] += 1

                continue

            DIAG["confirmation"] += 1

            # ------------------------------------------------
            # BUILD TRADE
            # ------------------------------------------------

            trade = build_trade(
                symbol=symbol,
                side=setup["side"],
                setup_type=setup["type"],
                rvol=setup["rvol"],
                candles1h=candles1h,
                candles15=candles15,
                candles5=candles5
            )

            if not trade:

                DIAG["invalid_risk"] += 1

                continue

            candidates.append(
                trade
            )

        except Exception as e:

            DIAG["data_errors"] += 1

            print(
                f"SCAN ERROR {symbol}: {e}"
            )

    # --------------------------------------------------------
    # RANK CANDIDATES
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["rr"]
        ),
        reverse=True
    )

    return candidates


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("VOLUME-KHAT 100 v3.1")
    print("Kraken Futures | 5M CLOSED")
    print("=" * 60)

    print(
        "SYSTEM:",
        "GitHub Actions"
    )

    print(
        "TOP:",
        TOP_N
    )

    print(
        "MAX OPEN:",
        MAX_OPEN_TRADES
    )

    print(
        "RVOL:",
        RVOL_ABNORMAL,
        "/",
        RVOL_STRONG,
        "/",
        RVOL_VERY_STRONG
    )

    print(
        "MIN RR:",
        MIN_RR
    )

    print(
        "MAX SL:",
        MAX_SL_PCT * 100,
        "%"
    )

    print("=" * 60)

    # ========================================================
    # DATABASE MUST BE INITIALIZED FIRST
    # ========================================================

    init_db()

    # ========================================================
    # UPDATE EXISTING TRADES
    # ========================================================

    update_open_trades()

    cleanup_max_open()

    # ========================================================
    # CURRENT OPEN
    # ========================================================

    open_count = count_open_trades()

    print(
        "OPEN TRADES:",
        open_count
    )

    # ========================================================
    # SCAN
    # ========================================================

    candidates = scan_markets()

    # ========================================================
    # AVAILABLE SLOTS
    # ========================================================

    open_count = count_open_trades()

    available_slots = max(
        0,
        MAX_OPEN_TRADES - open_count
    )

    max_new = min(
        MAX_NEW_SIGNALS,
        available_slots
    )

    saved_signals = []

    # ========================================================
    # SAVE BEST CANDIDATES
    # ========================================================

    if max_new > 0:

        for trade in candidates:

            if len(saved_signals) >= max_new:
                break

            if symbol_on_cooldown(
                trade["symbol"]
            ):
                continue

            saved = save_signal(
                trade
            )

            if saved:

                saved_signals.append(
                    trade
                )

                DIAG["final"] += 1

    # ========================================================
    # PERFORMANCE
    # ========================================================

    stats = performance_stats()

    # ========================================================
    # CLOSED REPORT
    # ========================================================

    closed_text, closed_ids = (
        build_closed_report()
    )

    # ========================================================
    # REPORT
    # ========================================================

    report_lines = [

        "📊 VOLUME-KHAT 100",

        f"🕐 {format_iran_time()} IR",

        "⚡ Kraken Futures | 5M CLOSED",

        f"🔎 Markets: {DIAG['markets']} | "
        f"Signals: {len(saved_signals)}",

        "",

        "📈 PERFORMANCE",

        f"Open {stats['open']}/{MAX_OPEN_TRADES}"
        f" | Closed {stats['closed']}",

        f"W {stats['wins']} | "
        f"L {stats['losses']} | "
        f"WR {stats['win_rate']:.1f}%",

        f"Realized PnL "
        f"{stats['pnl']:+.3f}%",

        "",

        build_diagnostic()
    ]

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    if saved_signals:

        report_lines.append("")

        report_lines.append(
            "🚨 NEW SIGNALS"
        )

        for trade in saved_signals:

            report_lines.append("")

            report_lines.append(
                format_signal(
                    trade
                )
            )

    # ========================================================
    # CLOSED
    # ========================================================

    if closed_text:

        report_lines.append("")

        report_lines.append(
            closed_text
        )

    # ========================================================
    # OPEN
    # ========================================================

    report_lines.append("")

    report_lines.append(
        build_open_report()
    )

    # ========================================================
    # PAPER
    # ========================================================

    report_lines.append("")

    if PAPER_TRADING:

        report_lines.append(
            "🧪 PAPER TRADING"
        )

    else:

        report_lines.append(
            "⚠️ LIVE MODE"
        )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    report = "\n".join(
        report_lines
    )

    # Telegram safety
    if len(report) > 3900:

        report = report[:3850] + (
            "\n\n... REPORT TRIMMED"
        )

    print("")
    print("=" * 60)
    print(report)
    print("=" * 60)

    # ========================================================
    # SEND
    # ========================================================

    sent = send_telegram(
        report
    )

    # ========================================================
    # ONLY MARK CLOSED AS REPORTED
    # AFTER SUCCESSFUL TELEGRAM
    # ========================================================

    if sent and closed_ids:

        mark_closed_reported(
            closed_ids
        )

    print(
        "TELEGRAM:",
        "SENT" if sent else "FAILED"
    )

    print(
        "SAVED SIGNALS:",
        len(saved_signals)
    )

    print(
        "FINAL SIGNALS:",
        DIAG["final"]
    )

    print("=" * 60)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "Stopped by user."
        )

    except Exception as e:

        print(
            "FATAL ERROR:",
            e
        )

        raise
