# ============================================================
# VOLUME-KHAT 100 v2.4
# ============================================================
# KRAKEN FUTURES
#
# TOP 100 USD PERPETUAL MARKETS
#
# MTF:
#   1H  = Trend + Static Support/Resistance
#   15M = Dynamic S/R + RVOL + Setup
#   5M  = Entry Confirmation
#
# SETUPS:
#   BREAKOUT
#   REJECTION
#
# RULES:
#   - MAX 3 NEW SIGNALS PER RUN
#   - Signals ranked by SCORE
#   - SL = STRUCTURAL / STATIC S&R
#   - TP = STATIC S&R
#   - SL > 0.80% => REJECT SIGNAL
#   - MIN RR = 1:2
#   - ONE OPEN TRADE PER SYMBOL
#   - PAPER TRACKING ONLY
#   - CLOSED SIGNALS APPEAR IN NEXT REPORT
#
# GITHUB ACTIONS:
#   Run once every 5 minutes
#   No while True
# ============================================================

import os
import time
import math
import sqlite3
import requests
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

TOP_N = 100

TF_1H = "60"
TF_15M = "15"
TF_5M = "5"

LIMIT_1H = 180
LIMIT_15M = 180
LIMIT_5M = 120

RVOL_PERIOD = 20
RVOL_MIN = 2.0
RVOL_STRONG = 3.0

SWING_LEFT = 2
SWING_RIGHT = 2

DYNAMIC_MAX_DISTANCE = 0.01
DYNAMIC_STRONG_DISTANCE = 0.005

BREAKOUT_MIN_DISTANCE = 0.0015

BREAKOUT_BODY_MIN = 0.50
CONFIRM_BODY_MIN = 0.45

STATIC_CLUSTER_PCT = 0.003

ATR_PERIOD = 14
SL_BUFFER_ATR = 0.20

# ============================================================
# RISK FILTER
# ============================================================

MIN_RR = 2.0

# حداکثر فاصله مجاز SL نسبت به Entry
MAX_SL_PCT = 0.0080

# حداکثر تعداد سیگنال جدید در هر اجرای اسکن
MAX_NEW_SIGNALS = 3

MIN_SIGNAL_SCORE = 9

# ============================================================
# DATABASE
# ============================================================

DB_FILE = "volume_khat_100.db"

# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_MAX_LENGTH = 3900

# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/2.4"
})

# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def fmt_price(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:.2f}"
    if x >= 100:
        return f"{x:.4f}"
    if x >= 1:
        return f"{x:.5f}"
    if x >= 0.1:
        return f"{x:.6f}"
    if x >= 0.01:
        return f"{x:.7f}"
    if x >= 0.001:
        return f"{x:.8f}"
    return f"{x:.10f}"


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def pct(a, b):
    if not b:
        return 0.0
    return ((a - b) / b) * 100.0


def body_ratio(c):
    rng = c["high"] - c["low"]
    if rng <= 0:
        return 0.0
    return abs(c["close"] - c["open"]) / rng


def is_bull(c):
    return c["close"] > c["open"]


def is_bear(c):
    return c["close"] < c["open"]


# ============================================================
# KRAKEN MARKET DISCOVERY
# ============================================================

def get_instruments():
    url = BASE + "/derivatives/api/v3/instruments"

    r = SESSION.get(url, timeout=20)
    r.raise_for_status()

    data = r.json()

    instruments = data.get("instruments", [])

    result = []

    for x in instruments:
        symbol = x.get("symbol", "")
        tradeable = x.get("tradeable", True)
        quote = str(x.get("quoteCurrency", "")).upper()
        typ = str(x.get("type", "")).lower()

        if not symbol:
            continue

        # فقط USD perpetual
        if quote and quote != "USD":
            continue

        if typ and "perpetual" not in typ:
            continue

        if tradeable is False:
            continue

        result.append(symbol)

    return result


def get_tickers():
    url = BASE + "/derivatives/api/v3/tickers"

    r = SESSION.get(url, timeout=20)
    r.raise_for_status()

    data = r.json()

    return data.get("tickers", [])


def discover_top_markets():
    instruments = get_instruments()
    tickers = get_tickers()

    allowed = set(instruments)

    rows = []

    for t in tickers:
        symbol = t.get("symbol", "")

        if symbol not in allowed:
            continue

        last = safe_float(t.get("last"))
        volume = safe_float(
            t.get("volume24h",
            t.get("volume", 0))
        )

        if last <= 0:
            continue

        rows.append({
            "symbol": symbol,
            "last": last,
            "volume": volume
        })

    rows.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return rows[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, limit):
    url = (
        f"{BASE}/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    r = SESSION.get(
        url,
        params={"periods": limit},
        timeout=20
    )

    r.raise_for_status()

    data = r.json()

    candles = data.get("candles", data)

    if not isinstance(candles, list):
        return []

    result = []

    for c in candles:
        try:
            result.append({
                "time": safe_float(
                    c.get("time", c.get("timestamp", 0))
                ),
                "open": safe_float(c.get("open")),
                "high": safe_float(c.get("high")),
                "low": safe_float(c.get("low")),
                "close": safe_float(c.get("close")),
                "volume": safe_float(
                    c.get("volume", c.get("vol", 0))
                )
            })
        except Exception:
            continue

    result.sort(key=lambda x: x["time"])

    # حذف کندل جاری و باز
    if len(result) > 1:
        result = result[:-1]

    return result[-limit:]


# ============================================================
# TREND
# ============================================================

def get_trend(candles):
    if len(candles) < 30:
        return "NEUTRAL"

    closes = [x["close"] for x in candles]

    sma20 = sum(closes[-20:]) / 20
    sma50 = (
        sum(closes[-50:]) / 50
        if len(closes) >= 50
        else sum(closes) / len(closes)
    )

    last = closes[-1]

    if last > sma20 and sma20 > sma50:
        return "LONG"

    if last < sma20 and sma20 < sma50:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# SWINGS
# ============================================================

def detect_swings(candles):
    highs = []
    lows = []

    if len(candles) < SWING_LEFT + SWING_RIGHT + 5:
        return highs, lows

    for i in range(
        SWING_LEFT,
        len(candles) - SWING_RIGHT
    ):
        h = candles[i]["high"]
        l = candles[i]["low"]

        left_highs = [
            candles[j]["high"]
            for j in range(
                i - SWING_LEFT,
                i
            )
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + SWING_RIGHT + 1
            )
        ]

        left_lows = [
            candles[j]["low"]
            for j in range(
                i - SWING_LEFT,
                i
            )
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + SWING_RIGHT + 1
            )
        ]

        if h > max(left_highs) and h > max(right_highs):
            highs.append((i, h))

        if l < min(left_lows) and l < min(right_lows):
            lows.append((i, l))

    return highs, lows


# ============================================================
# LEVEL PROJECTION
# ============================================================

def project_level(swings, price, direction):
    if not swings:
        return None

    values = [x[1] for x in swings]

    if direction == "RESISTANCE":
        candidates = [
            x for x in values
            if x > price
        ]

        if not candidates:
            return max(values)

        return min(candidates)

    candidates = [
        x for x in values
        if x < price
    ]

    if not candidates:
        return min(values)

    return max(candidates)


# ============================================================
# DYNAMIC SUPPORT / RESISTANCE
# ============================================================

def get_dynamic_sr(candles):
    if len(candles) < 20:
        return None, None

    price = candles[-1]["close"]

    highs, lows = detect_swings(candles)

    resistance = project_level(
        highs,
        price,
        "RESISTANCE"
    )

    support = project_level(
        lows,
        price,
        "SUPPORT"
    )

    return support, resistance


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(candles, period=RVOL_PERIOD):
    if len(candles) < period + 1:
        return 0.0

    volumes = [
        x["volume"]
        for x in candles[-period-1:-1]
        if x["volume"] > 0
    ]

    if not volumes:
        return 0.0

    avg = sum(volumes) / len(volumes)

    if avg <= 0:
        return 0.0

    return candles[-1]["volume"] / avg


# ============================================================
# CANDLE INFORMATION
# ============================================================

def candle_info(c):
    return {
        "bull": is_bull(c),
        "bear": is_bear(c),
        "body": body_ratio(c),
        "range": c["high"] - c["low"]
    }


# ============================================================
# BREAKOUT DETECTION
# ============================================================

def detect_breakout(candles, support, resistance):
    if len(candles) < 3:
        return None

    prev = candles[-2]
    cur = candles[-1]

    info = candle_info(cur)

    # Bullish breakout
    if resistance and resistance > 0:

        distance = (
            (cur["close"] - resistance)
            / resistance
        )

        if (
            cur["close"] > resistance
            and distance >= BREAKOUT_MIN_DISTANCE
            and info["bull"]
            and info["body"] >= BREAKOUT_BODY_MIN
        ):
            return "LONG"

    # Bearish breakout
    if support and support > 0:

        distance = (
            (support - cur["close"])
            / support
        )

        if (
            cur["close"] < support
            and distance >= BREAKOUT_MIN_DISTANCE
            and info["bear"]
            and info["body"] >= BREAKOUT_BODY_MIN
        ):
            return "SHORT"

    return None


# ============================================================
# REJECTION DETECTION
# ============================================================

def detect_rejection(candles, support, resistance):
    if len(candles) < 2:
        return None

    c = candles[-1]

    rng = c["high"] - c["low"]

    if rng <= 0:
        return None

    body = abs(c["close"] - c["open"])

    upper_wick = c["high"] - max(
        c["open"],
        c["close"]
    )

    lower_wick = min(
        c["open"],
        c["close"]
    ) - c["low"]

    # Support rejection -> LONG
    if support and support > 0:

        distance = abs(
            c["low"] - support
        ) / support

        if (
            distance <= DYNAMIC_MAX_DISTANCE
            and lower_wick > body
            and c["close"] > c["open"]
        ):
            return "LONG"

    # Resistance rejection -> SHORT
    if resistance and resistance > 0:

        distance = abs(
            c["high"] - resistance
        ) / resistance

        if (
            distance <= DYNAMIC_MAX_DISTANCE
            and upper_wick > body
            and c["close"] < c["open"]
        ):
            return "SHORT"

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(candles, side):
    if len(candles) < 3:
        return False

    c = candles[-1]
    prev = candles[-2]

    info = candle_info(c)

    if info["body"] < CONFIRM_BODY_MIN:
        return False

    if side == "LONG":
        return (
            c["close"] > c["open"]
            and c["close"] > prev["close"]
        )

    if side == "SHORT":
        return (
            c["close"] < c["open"]
            and c["close"] < prev["close"]
        )

    return False


# ============================================================
# RETEST
# ============================================================

def detect_retest(candles, level, side):
    if not level or len(candles) < 3:
        return False

    c = candles[-1]

    distance = abs(
        c["close"] - level
    ) / level

    if distance > DYNAMIC_MAX_DISTANCE:
        return False

    if side == "LONG":
        return c["close"] >= c["open"]

    if side == "SHORT":
        return c["close"] <= c["open"]

    return False


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return sum(trs[-period:]) / period


# ============================================================
# STATIC SUPPORT / RESISTANCE
# ============================================================

def cluster_levels(levels, cluster_pct=STATIC_CLUSTER_PCT):
    if not levels:
        return []

    levels = sorted(
        x for x in levels
        if x and x > 0
    )

    clusters = []

    current = [levels[0]]

    for level in levels[1:]:

        center = sum(current) / len(current)

        if abs(level - center) / center <= cluster_pct:
            current.append(level)
        else:
            clusters.append(
                sum(current) / len(current)
            )
            current = [level]

    clusters.append(
        sum(current) / len(current)
    )

    return clusters


def get_static_sr(candles):
    if len(candles) < 40:
        return None, None

    price = candles[-1]["close"]

    highs, lows = detect_swings(candles)

    high_levels = [x[1] for x in highs]
    low_levels = [x[1] for x in lows]

    levels = cluster_levels(
        high_levels + low_levels
    )

    supports = [
        x for x in levels
        if x < price
    ]

    resistances = [
        x for x in levels
        if x > price
    ]

    support = (
        max(supports)
        if supports
        else min(low_levels) if low_levels else None
    )

    resistance = (
        min(resistances)
        if resistances
        else max(high_levels) if high_levels else None
    )

    return support, resistance


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def calculate_trade(
    side,
    entry,
    static_support,
    static_resistance,
    dynamic_support=None,
    dynamic_resistance=None,
    atr=0.0
):
    if entry <= 0:
        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        # SL primarily from static support
        sl_candidates = []

        if static_support and static_support < entry:
            sl_candidates.append(static_support)

        # Dynamic support can help refine structural SL
        if dynamic_support and dynamic_support < entry:
            sl_candidates.append(dynamic_support)

        if not sl_candidates:
            return None

        # نزدیک‌ترین structural support زیر Entry
        structural_sl = max(sl_candidates)

        # ATR buffer so SL isn't exactly on support
        if atr > 0:
            sl = structural_sl - (
                atr * SL_BUFFER_ATR
            )
        else:
            sl = structural_sl

        # TP = next static resistance
        if (
            not static_resistance
            or static_resistance <= entry
        ):
            return None

        tp = static_resistance

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif side == "SHORT":

        sl_candidates = []

        if static_resistance and static_resistance > entry:
            sl_candidates.append(static_resistance)

        if (
            dynamic_resistance
            and dynamic_resistance > entry
        ):
            sl_candidates.append(dynamic_resistance)

        if not sl_candidates:
            return None

        # نزدیک‌ترین structural resistance بالای Entry
        structural_sl = min(sl_candidates)

        if atr > 0:
            sl = structural_sl + (
                atr * SL_BUFFER_ATR
            )
        else:
            sl = structural_sl

        # TP = next static support
        if (
            not static_support
            or static_support >= entry
        ):
            return None

        tp = static_support

    else:
        return None

    if sl <= 0 or tp <= 0:
        return None

    # ========================================================
    # SL DISTANCE FILTER
    # ========================================================

    sl_distance_pct = (
        abs(entry - sl) / entry
    )

    # بیشتر از 0.80% = رد کامل سیگنال
    if sl_distance_pct > MAX_SL_PCT:
        return None

    # ========================================================
    # RR
    # ========================================================

    risk = abs(entry - sl)
    reward = abs(tp - entry)

    if risk <= 0:
        return None

    rr = reward / risk

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "sl_pct": sl_distance_pct * 100.0
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    side,
    setup,
    rvol,
    confirm,
    rr
):
    score = 0

    # Trend
    if trend == side:
        score += 3

    # Setup
    if setup == "BREAKOUT":
        score += 3
    elif setup == "REJECTION":
        score += 2

    # RVOL
    if rvol >= RVOL_STRONG:
        score += 3
    elif rvol >= RVOL_MIN:
        score += 2

    # Confirmation
    if confirm:
        score += 3

    # RR
    if rr >= 4:
        score += 2
    elif rr >= 3:
        score += 1

    return score


# ============================================================
# DATABASE
# ============================================================

def db():
    return sqlite3.connect(
        DB_FILE,
        timeout=30
    )


def init_db():
    con = db()
    cur = con.cursor()

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
            score INTEGER,
            rvol REAL,
            trend TEXT,
            status TEXT,
            result TEXT,
            pnl REAL,
            created_at TEXT,
            closed_at TEXT,
            exit_reason TEXT,
            closed_reported INTEGER DEFAULT 0
        )
    """)

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    columns = {
        row[1]
        for row in cur.fetchall()
    }

    if "exit_reason" not in columns:
        cur.execute("""
            ALTER TABLE trades
            ADD COLUMN exit_reason TEXT
        """)

    if "closed_reported" not in columns:
        cur.execute("""
            ALTER TABLE trades
            ADD COLUMN closed_reported INTEGER DEFAULT 0
        """)

    con.commit()
    con.close()


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_symbol(symbol):
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT 1
        FROM trades
        WHERE symbol = ?
        AND status = 'OPEN'
        LIMIT 1
    """, (symbol,))

    result = cur.fetchone()

    con.close()

    return result is not None


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(signal):
    con = db()
    cur = con.cursor()

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
            rvol,
            trend,
            status,
            result,
            pnl,
            created_at,
            closed_at,
            exit_reason,
            closed_reported
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN',
                NULL, NULL, ?, NULL, NULL, 0)
    """, (
        signal["symbol"],
        signal["side"],
        signal["setup"],
        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["rr"],
        signal["score"],
        signal["rvol"],
        signal["trend"],
        signal["created_at"]
    ))

    con.commit()
    con.close()


# ============================================================
# UNREPORTED CLOSED TRADES
# ============================================================

def get_unreported_closed():
    con = db()
    cur = con.cursor()

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
            rvol,
            trend,
            status,
            result,
            pnl,
            created_at,
            closed_at,
            exit_reason
        FROM trades
        WHERE status = 'CLOSED'
        AND COALESCE(closed_reported, 0) = 0
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    con.close()

    return rows


# ============================================================
# MARK CLOSED AS REPORTED
# ============================================================

def mark_closed_reported(ids):
    if not ids:
        return

    con = db()
    cur = con.cursor()

    for trade_id in ids:
        cur.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE id = ?
        """, (trade_id,))

    con.commit()
    con.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(price_map):
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp
        FROM trades
        WHERE status = 'OPEN'
    """)

    rows = cur.fetchall()

    newly_closed = []

    for row in rows:

        trade_id, symbol, side, entry, sl, tp = row

        price = price_map.get(symbol)

        if price is None:
            continue

        result = None
        exit_reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "LONG":

            if price >= tp:
                result = "WIN"
                exit_reason = "TP"

            elif price <= sl:
                result = "LOSS"
                exit_reason = "SL"

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        elif side == "SHORT":

            if price <= tp:
                result = "WIN"
                exit_reason = "TP"

            elif price >= sl:
                result = "LOSS"
                exit_reason = "SL"

        if result:

            if side == "LONG":
                pnl = (
                    (price - entry)
                    / entry
                ) * 100.0

            else:
                pnl = (
                    (entry - price)
                    / entry
                ) * 100.0

            closed_at = now_utc().isoformat()

            cur.execute("""
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = ?,
                    pnl = ?,
                    closed_at = ?,
                    exit_reason = ?,
                    closed_reported = 0
                WHERE id = ?
            """, (
                result,
                pnl,
                closed_at,
                exit_reason,
                trade_id
            ))

            newly_closed.append(
                trade_id
            )

    con.commit()
    con.close()

    return newly_closed


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades(price_map):
    con = db()
    cur = con.cursor()

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
            rvol,
            trend
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    con.close()

    result = []

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
            rvol,
            trend
        ) = row

        price = price_map.get(
            symbol,
            entry
        )

        if side == "LONG":
            pnl = (
                (price - entry)
                / entry
            ) * 100.0
        else:
            pnl = (
                (entry - price)
                / entry
            ) * 100.0

        result.append({
            "id": trade_id,
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "score": score,
            "rvol": rvol,
            "trend": trend,
            "price": price,
            "pnl": pnl
        })

    return result


# ============================================================
# STATS
# ============================================================

def get_stats():
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """)

    open_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
    """)

    closed_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'WIN'
    """)

    wins = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'LOSS'
    """)

    losses = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status = 'CLOSED'
    """)

    net_pnl = cur.fetchone()[0]

    con.close()

    if closed_count:
        win_rate = (
            wins / closed_count
        ) * 100.0
    else:
        win_rate = 0.0

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net_pnl
    }


# ============================================================
# FORMAT OPEN
# ============================================================

def format_open_signal(x):
    icon = (
        "🟢"
        if x["side"] == "LONG"
        else "🔴"
    )

    return (
        f"{icon} {x['symbol']} {x['side']}\n"
        f"💰 Entry: {fmt_price(x['entry'])}\n"
        f"📍 Now: {fmt_price(x['price'])}\n"
        f"🛑 SL: {fmt_price(x['sl'])}\n"
        f"🎯 TP: {fmt_price(x['tp'])}\n"
        f"📊 PnL: {x['pnl']:+.2f}%"
    )


# ============================================================
# FORMAT NEW SIGNAL
# ============================================================

def format_new_signal(x):
    icon = (
        "🟢"
        if x["side"] == "LONG"
        else "🔴"
    )

    return (
        f"{icon} {x['symbol']} {x['side']}\n"
        f"📌 {x['setup']} | ⭐ {x['score']} "
        f"| RVOL {x['rvol']:.2f}x "
        f"| RR 1:{x['rr']:.2f}\n"
        f"💰 Entry: {fmt_price(x['entry'])}\n"
        f"🛑 SL: {fmt_price(x['sl'])}\n"
        f"🎯 TP: {fmt_price(x['tp'])}"
    )


# ============================================================
# FORMAT CLOSED
# ============================================================

def format_closed_signal(row):
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
        rvol,
        trend,
        status,
        result,
        pnl,
        created_at,
        closed_at,
        exit_reason
    ) = row

    if exit_reason == "TP":
        icon = "✅"
    elif exit_reason == "SL":
        icon = "❌"
    else:
        icon = "⚪"

    reason = exit_reason or result or "CLOSED"

    return (
        f"{icon} {symbol} {side} → {reason}\n"
        f"📊 PnL: {pnl:+.2f}%"
    )


# ============================================================
# PROCESS MARKET
# ============================================================

def process_market(symbol):
    result = {
        "symbol": symbol,
        "data_ok": False,
        "rvol": 0.0,
        "breakout": 0,
        "rejection": 0,
        "confirm": 0,
        "rr_ok": 0,
        "qualified": None,
        "error": None
    }

    try:

        # ----------------------------------------------------
        # DATA
        # ----------------------------------------------------

        c1h = get_candles(
            symbol,
            TF_1H,
            LIMIT_1H
        )

        c15 = get_candles(
            symbol,
            TF_15M,
            LIMIT_15M
        )

        c5 = get_candles(
            symbol,
            TF_5M,
            LIMIT_5M
        )

        if (
            len(c1h) < 60
            or len(c15) < 40
            or len(c5) < 30
        ):
            return result

        result["data_ok"] = True

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        trend = get_trend(c1h)

        if trend == "NEUTRAL":
            return result

        # ----------------------------------------------------
        # 15M DYNAMIC S/R
        # ----------------------------------------------------

        dynamic_support, dynamic_resistance = (
            get_dynamic_sr(c15)
        )

        if (
            not dynamic_support
            and not dynamic_resistance
        ):
            return result

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(c15)

        result["rvol"] = rvol

        if rvol < RVOL_MIN:
            return result

        # ----------------------------------------------------
        # SETUP
        # ----------------------------------------------------

        setup = None
        side = None

        breakout_side = detect_breakout(
            c15,
            dynamic_support,
            dynamic_resistance
        )

        if breakout_side:
            setup = "BREAKOUT"
            side = breakout_side
            result["breakout"] = 1

        rejection_side = detect_rejection(
            c15,
            dynamic_support,
            dynamic_resistance
        )

        if rejection_side and setup is None:
            setup = "REJECTION"
            side = rejection_side
            result["rejection"] = 1

        if not setup or not side:
            return result

        # ----------------------------------------------------
        # TREND ALIGNMENT
        # ----------------------------------------------------

        if trend != side:
            return result

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        confirmed = confirm_5m(
            c5,
            side
        )

        if confirmed:
            result["confirm"] = 1

        if not confirmed:
            return result

        # ----------------------------------------------------
        # STATIC S/R
        # ----------------------------------------------------

        static_support, static_resistance = (
            get_static_sr(c1h)
        )

        if (
            not static_support
            or not static_resistance
        ):
            return result

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = c5[-1]["close"]

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = calculate_atr(c15)

        # ----------------------------------------------------
        # TRADE
        # ----------------------------------------------------

        trade = calculate_trade(
            side=side,
            entry=entry,
            static_support=static_support,
            static_resistance=static_resistance,
            dynamic_support=dynamic_support,
            dynamic_resistance=dynamic_resistance,
            atr=atr
        )

        # اگر SL > 0.80% یا RR < 1:2
        # calculate_trade مقدار None می‌دهد
        if not trade:
            return result

        result["rr_ok"] = 1

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            trend=trend,
            side=side,
            setup=setup,
            rvol=rvol,
            confirm=confirmed,
            rr=trade["rr"]
        )

        if score < MIN_SIGNAL_SCORE:
            return result

        # ----------------------------------------------------
        # OPEN SYMBOL FILTER
        # ----------------------------------------------------

        if has_open_symbol(symbol):
            return result

        # ----------------------------------------------------
        # QUALIFIED
        # ----------------------------------------------------

        signal = {
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "entry": trade["entry"],
            "sl": trade["sl"],
            "tp": trade["tp"],
            "rr": trade["rr"],
            "sl_pct": trade["sl_pct"],
            "score": score,
            "rvol": rvol,
            "trend": trend,
            "created_at": now_utc().isoformat()
        }

        result["qualified"] = signal

        return result

    except Exception as e:

        result["error"] = str(e)

        return result


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID missing")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        # Telegram limit protection
        chunks = []

        if len(text) <= TELEGRAM_MAX_LENGTH:
            chunks = [text]
        else:
            current = ""

            for line in text.splitlines(True):

                if len(current) + len(line) > TELEGRAM_MAX_LENGTH:
                    if current:
                        chunks.append(current)

                    current = line

                else:
                    current += line

            if current:
                chunks.append(current)

        for chunk in chunks:

            r = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk
                },
                timeout=20
            )

            if r.status_code != 200:
                print(
                    "Telegram error:",
                    r.status_code,
                    r.text
                )
                return False

        return True

    except Exception as e:
        print(
            "Telegram exception:",
            e
        )
        return False


# ============================================================
# BUILD REPORT
# ============================================================

def build_report(
    new_signals,
    open_trades,
    closed_trades,
    stats,
    scan_stats,
    elapsed
):
    now = now_utc()

    lines = []

    lines.append(
        "🤖 VOLUME-KHAT 100"
    )

    lines.append(
        "📡 VOLUME-KHAT 100 v2.4"
    )

    lines.append(
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        "⏱ TOP 100 | 5M CLOSED"
    )

    lines.append(
        "💻 Real trading: DISABLED"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    if new_signals:

        lines.append(
            "🚨 NEW SIGNALS"
        )

        for signal in new_signals:

            lines.append(
                format_new_signal(signal)
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    # ========================================================
    # OPEN SIGNALS
    # ========================================================

    lines.append(
        "📂 OPEN SIGNALS"
    )

    if open_trades:

        for trade in open_trades:

            lines.append(
                format_open_signal(trade)
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "No open signals"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # ========================================================
    # CLOSED
    # ========================================================

    if closed_trades:

        lines.append(
            "📌 CLOSED SINCE PREVIOUS REPORT"
        )

        for row in closed_trades:

            lines.append(
                format_closed_signal(row)
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    # ========================================================
    # STATS
    # ========================================================

    lines.append(
        "📈 STATS"
    )

    lines.append(
        f"Open: {stats['open']}"
    )

    lines.append(
        f"Closed: {stats['closed']}"
    )

    lines.append(
        f"Wins: {stats['wins']}"
    )

    lines.append(
        f"Losses: {stats['losses']}"
    )

    lines.append(
        f"Win Rate: {stats['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: {stats['net_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # SCAN
    # ========================================================

    lines.append(
        "📊 SCAN"
    )

    lines.append(
        f"Scanned: {scan_stats['scanned']} | "
        f"Data: {scan_stats['data']}"
    )

    lines.append(
        f"RVOL≥2: {scan_stats['rvol']} | "
        f"BO: {scan_stats['bo']} | "
        f"REJ: {scan_stats['rej']}"
    )

    lines.append(
        f"5M Confirm: {scan_stats['confirm']} | "
        f"RR≥1:2: {scan_stats['rr']}"
    )

    lines.append(
        f"Qualified: {scan_stats['qualified']}"
    )

    lines.append(
        f"New Signals: {len(new_signals)}/{MAX_NEW_SIGNALS}"
    )

    lines.append(
        f"⏱ {elapsed:.1f}s"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print(
        "\n=========================================="
    )

    print(
        "VOLUME-KHAT 100 v2.4"
    )

    print(
        "=========================================="
    )

    init_db()

    # --------------------------------------------------------
    # DISCOVER MARKETS
    # --------------------------------------------------------

    try:

        markets = discover_top_markets()

    except Exception as e:

        print(
            "Market discovery failed:",
            e
        )

        # مهم:
        # pending closed trades را REPORT نمی‌کنیم
        # چون گزارش واقعی ساخته نشده است.

        error_report = (
            "🤖 VOLUME-KHAT 100\n"
            "📡 VOLUME-KHAT 100 v2.4\n"
            "🚨 MARKET DISCOVERY FAILED\n"
            f"❌ {str(e)}"
        )

        send_telegram(error_report)

        return

    if not markets:

        print(
            "No markets found."
        )

        return

    print(
        f"Markets discovered: {len(markets)}"
    )

    # --------------------------------------------------------
    # PRICE MAP
    # --------------------------------------------------------

    price_map = {
        x["symbol"]: x["last"]
        for x in markets
    }

    # --------------------------------------------------------
    # CLOSE OLD TRADES
    #
    # IMPORTANT:
    # Newly closed trades remain closed_reported=0.
    # They are intentionally NOT shown in this report.
    # They will appear on the NEXT successful report.
    # --------------------------------------------------------

    update_open_trades(
        price_map
    )

    # --------------------------------------------------------
    # GET PENDING CLOSED TRADES
    #
    # This is taken AFTER update_open_trades.
    # Therefore newly closed trades are pending for NEXT run,
    # not current run.
    #
    # To enforce the exact requested behavior, we snapshot
    # the list that existed before this run's close operation.
    # --------------------------------------------------------

    pending_closed = get_unreported_closed()

    # --------------------------------------------------------
    # IMPORTANT NEXT-REPORT LOGIC
    #
    # Trades closed during this execution must not appear now.
    # We identify them from update_open_trades result above.
    # --------------------------------------------------------

    # Rebuild exact list:
    # Any closed trade whose closed_at is very recent from this
    # execution is held for the next report.
    #
    # More robustly, get IDs from the current execution.

    # Since update_open_trades already returned IDs, capture them.
    # Re-run is impossible, so derive current execution IDs from
    # the timestamp window.

    current_time = now_utc()

    current_closed_ids = set()

    # We can safely identify rows closed during this execution
    # by comparing closed_at with the run start time.
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT id
        FROM trades
        WHERE status = 'CLOSED'
        AND closed_at IS NOT NULL
        AND closed_at >= ?
        AND COALESCE(closed_reported, 0) = 0
    """, (
        datetime.fromtimestamp(
            start_time,
            tz=timezone.utc
        ).isoformat(),
    ))

    for row in cur.fetchall():
        current_closed_ids.add(row[0])

    con.close()

    # Pending list for THIS report excludes trades that closed
    # during the current execution.
    closed_for_report = [
        row
        for row in pending_closed
        if row[0] not in current_closed_ids
    ]

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    scan_stats = {
        "scanned": len(markets),
        "data": 0,
        "rvol": 0,
        "bo": 0,
        "rej": 0,
        "confirm": 0,
        "rr": 0,
        "qualified": 0
    }

    qualified = []

    max_workers = min(
        10,
        max(1, len(markets))
    )

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        futures = {
            executor.submit(
                process_market,
                m["symbol"]
            ): m["symbol"]
            for m in markets
        }

        for future in as_completed(futures):

            try:

                result = future.result()

            except Exception as e:

                print(
                    "Worker error:",
                    e
                )

                continue

            if result["data_ok"]:
                scan_stats["data"] += 1

            if result["rvol"] >= RVOL_MIN:
                scan_stats["rvol"] += 1

            if result["breakout"]:
                scan_stats["bo"] += 1

            if result["rejection"]:
                scan_stats["rej"] += 1

            if result["confirm"]:
                scan_stats["confirm"] += 1

            if result["rr_ok"]:
                scan_stats["rr"] += 1

            if result["qualified"]:

                qualified.append(
                    result["qualified"]
                )

    scan_stats["qualified"] = len(
        qualified
    )

    # --------------------------------------------------------
    # RANK SIGNALS
    # --------------------------------------------------------

    qualified.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["rr"]
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # MAX 3 NEW SIGNALS
    # --------------------------------------------------------

    selected = qualified[
        :MAX_NEW_SIGNALS
    ]

    # --------------------------------------------------------
    # SAVE SELECTED SIGNALS
    # --------------------------------------------------------

    new_signals = []

    for signal in selected:

        # Final safety check
        if has_open_symbol(
            signal["symbol"]
        ):
            continue

        save_signal(signal)

        new_signals.append(
            signal
        )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    open_trades = get_open_trades(
        price_map
    )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    stats = get_stats()

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    elapsed = time.time() - start_time

    report = build_report(
        new_signals=new_signals,
        open_trades=open_trades,
        closed_trades=closed_for_report,
        stats=stats,
        scan_stats=scan_stats,
        elapsed=elapsed
    )

    print("\n" + report)

    # --------------------------------------------------------
    # SEND TELEGRAM
    # --------------------------------------------------------

    telegram_ok = send_telegram(
        report
    )

    # --------------------------------------------------------
    # MARK CLOSED AS REPORTED
    #
    # ONLY if:
    #   1. Telegram succeeded
    #   2. The closed trade was actually included
    #
    # If Telegram fails, it remains pending for next run.
    # --------------------------------------------------------

    if telegram_ok and closed_for_report:

        mark_closed_reported([
            row[0]
            for row in closed_for_report
        ])

    print(
        "\n=========================================="
    )

    print(
        f"Finished in {elapsed:.1f}s"
    )

    print(
        f"New signals: {len(new_signals)}"
    )

    print(
        f"Qualified before max-3 filter: "
        f"{len(qualified)}"
    )

    print(
        f"Closed shown this report: "
        f"{len(closed_for_report)}"
    )

    print(
        "=========================================="
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as e:

        print(
            "FATAL ERROR:",
            e
        )

        traceback.print_exc()

        try:

            send_telegram(
                "🚨 VOLUME-KHAT 100 v2.4\n"
                "❌ SCANNER ERROR\n"
                f"{str(e)}"
            )

        except Exception:
            pass
