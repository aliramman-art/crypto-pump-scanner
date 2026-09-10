# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100
# VERSION 3.5
# ============================================================
#
# STRATEGY:
#   1H  = TREND
#   15M = SETUP
#   5M  = CONFIRMATION / ENTRY
#
# CHANGES v3.5:
#   - Strategy logic preserved
#   - SL must be between 0.50% and 0.80%
#   - SL below 0.50% -> REJECT
#   - SL above 0.80% -> REJECT
#   - Compact Telegram report
#   - Only new signals
#   - Open trades + live PnL
#   - Win / Loss / Win Rate / Realized PnL
#   - LONG = green emoji
#   - SHORT = red emoji
#   - Removed diagnostics / top candidate / closed detail
#
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

TOP_N = 100

TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"
TIMEFRAME_1H = "1h"

OHLCV_LIMIT = 150
REQUEST_TIMEOUT = 20


# ============================================================
# RVOL
# ============================================================

RVOL_PERIOD = 20

RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00


# ============================================================
# SETUP
# ============================================================

BREAKOUT_LOOKBACK = 5
SUPPORT_RESISTANCE_LOOKBACK = 20

REJECTION_DISTANCE = 0.008
WICK_BODY_RATIO = 1.15


# ============================================================
# SL / TP
# ============================================================

ATR_PERIOD = 14
ATR_SL_BUFFER = 0.15

# ------------------------------------------------------------
# IMPORTANT
# SL MUST BE BETWEEN 0.50% AND 0.80%
# ------------------------------------------------------------

MIN_SL_PCT = 0.0050
MAX_SL_PCT = 0.0080

MIN_RR = 2.0


# ============================================================
# 5M CONFIRMATION
# ============================================================

MIN_BODY_RATIO = 0.20


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 9


# ============================================================
# TRADE CONTROL
# ============================================================

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3


# ============================================================
# PAPER TRADING
# ============================================================

PAPER_TRADING = True

DB_FILE = "volume_khat_100.db"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

IRAN_TZ = timezone(
    timedelta(hours=3, minutes=30)
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "VOLUME-KHAT-100/3.5"
    }
)


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iran():
    return datetime.now(IRAN_TZ)


def iran_time_string(dt=None):

    if dt is None:
        dt = now_iran()

    return dt.strftime(
        "%Y/%m/%d %H:%M:%S"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        print(
            "Telegram credentials missing."
        )
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(
            f"Telegram error: {e}"
        )

        return False


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None,
    retries=3
):

    last_error = None

    for attempt in range(retries):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            last_error = e

            if attempt < retries - 1:

                time.sleep(1.5)

    raise last_error


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    cur = conn.cursor()

    cur.execute(
        """
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
        """
    )

    conn.commit()

    # --------------------------------------------------------
    # Automatic migration
    # --------------------------------------------------------

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    columns = {
        row["name"]
        for row in cur.fetchall()
    }

    required_columns = {

        "symbol": "TEXT",
        "side": "TEXT",
        "setup": "TEXT",
        "entry": "REAL",
        "sl": "REAL",
        "tp": "REAL",
        "rr": "REAL",
        "score": "REAL",
        "status": "TEXT",
        "result": "TEXT",
        "pnl": "REAL",
        "entry_time": "TEXT",
        "exit_time": "TEXT",
        "exit_reason": "TEXT",
        "closed_reported": (
            "INTEGER DEFAULT 0"
        ),
        "created_at": "TEXT"
    }

    for name, definition in (
        required_columns.items()
    ):

        if name not in columns:

            try:

                cur.execute(
                    f"ALTER TABLE trades "
                    f"ADD COLUMN {name} "
                    f"{definition}"
                )

                print(
                    f"DB migration: "
                    f"added column {name}"
                )

            except Exception as e:

                print(
                    f"DB migration error "
                    f"{name}: {e}"
                )

    conn.commit()

    # --------------------------------------------------------
    # Repair NULL closed_reported
    # --------------------------------------------------------

    try:

        cur.execute(
            """
            UPDATE trades
            SET closed_reported = 0
            WHERE closed_reported IS NULL
            """
        )

        conn.commit()

    except Exception:
        pass

    # --------------------------------------------------------
    # Indexes
    # --------------------------------------------------------

    try:

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trades_status
            ON trades(status)
            """
        )

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trades_symbol
            ON trades(symbol)
            """
        )

        conn.commit()

    except Exception:
        pass

    conn.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_open_trades():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


def get_open_count():

    conn = get_db()

    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(row["c"])


def get_last_trade_for_symbol(
    symbol
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,)
    ).fetchone()

    conn.close()

    return row


def insert_trade(
    symbol,
    side,
    setup,
    entry,
    sl,
    tp,
    rr,
    score
):

    conn = get_db()

    now = now_utc().isoformat()

    conn.execute(
        """
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
        """,
        (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            now,
            now
        )
    )

    conn.commit()

    conn.close()


def close_trade(
    trade_id,
    result,
    pnl,
    exit_price,
    reason
):

    conn = get_db()

    conn.execute(
        """
        UPDATE trades
        SET
            status = 'CLOSED',
            result = ?,
            pnl = ?,
            exit_time = ?,
            exit_reason = ?,
            closed_reported = 0
        WHERE id = ?
        """,
        (
            result,
            pnl,
            now_utc().isoformat(),
            reason,
            trade_id
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# MARKET LIST
# ============================================================

def get_top_markets():

    url = (
        f"{BASE}/"
        f"derivatives/api/v3/tickers"
    )

    data = http_get(url)

    tickers = data.get(
        "tickers",
        []
    )

    markets = []

    for item in tickers:

        symbol = item.get(
            "symbol"
        )

        if not symbol:
            continue

        if not symbol.startswith(
            "PF_"
        ):
            continue

        if not symbol.endswith(
            "USD"
        ):
            continue

        volume = (
            item.get("vol24h")
            or item.get("volume24h")
            or item.get("volume")
            or 0
        )

        try:

            volume = float(
                volume
            )

        except Exception:

            volume = 0.0

        markets.append(
            {
                "symbol": symbol,
                "volume": volume
            }
        )

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return [
        x["symbol"]
        for x in markets[:TOP_N]
    ]


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(
    symbol,
    interval
):

    url = (
        f"{BASE}/"
        f"api/charts/v1/"
        f"trade/"
        f"{symbol}/"
        f"{interval}"
    )

    data = http_get(
        url,
        params={
            "count": OHLCV_LIMIT
        }
    )

    rows = (
        data.get("candles")
        or data.get("data")
        or data.get("results")
        or []
    )

    if not rows:

        raise ValueError(
            "No candle data"
        )

    parsed = []

    for row in rows:

        try:

            if isinstance(
                row,
                dict
            ):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("t")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

                v = (
                    row.get("volume")
                    or row.get("v")
                    or 0
                )

            else:

                if len(row) < 6:
                    continue

                ts, o, h, l, c, v = (
                    row[:6]
                )

            ts = float(ts)

            if ts > 10_000_000_000:

                ts /= 1000

            parsed.append(
                {
                    "timestamp":
                        datetime.fromtimestamp(
                            ts,
                            tz=timezone.utc
                        ),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v)
                }
            )

        except Exception:
            continue

    if len(parsed) < 60:

        raise ValueError(
            f"Insufficient candles: "
            f"{len(parsed)}"
        )

    df = pd.DataFrame(
        parsed
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    if len(df) > 1:

        df = df.iloc[
            :-1
        ].copy()

    if len(df) < 60:

        raise ValueError(
            "Not enough CLOSED candles"
        )

    return df.tail(
        OHLCV_LIMIT
    ).reset_index(
        drop=True
    )


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    period=RVOL_PERIOD
):

    volume = (
        df["volume"]
        .astype(float)
    )

    avg = (
        volume
        .rolling(period)
        .mean()
        .shift(1)
    )

    current = volume.iloc[-1]

    previous_avg = avg.iloc[-1]

    if (
        not np.isfinite(
            previous_avg
        )
        or previous_avg <= 0
    ):
        return 0.0

    return float(
        current / previous_avg
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - prev_close
    ).abs()

    tr3 = (
        low - prev_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    return (
        tr
        .rolling(period)
        .mean()
    )


# ============================================================
# TREND
# ============================================================

def get_trend(df):

    close = df["close"]

    sma20 = (
        close
        .rolling(20)
        .mean()
    )

    sma50 = (
        close
        .rolling(50)
        .mean()
    )

    c = float(
        close.iloc[-1]
    )

    s20 = float(
        sma20.iloc[-1]
    )

    s50 = float(
        sma50.iloc[-1]
    )

    if (
        np.isfinite(c)
        and np.isfinite(s20)
        and np.isfinite(s50)
    ):

        if c > s20 > s50:
            return "LONG"

        if c < s20 < s50:
            return "SHORT"

    return "NEUTRAL"


# ============================================================
# SUPPORT
# ============================================================

def get_support(
    df,
    lookback=SUPPORT_RESISTANCE_LOOKBACK
):

    if len(df) < lookback + 1:
        return float("nan")

    return float(
        df["low"]
        .iloc[-lookback:]
        .min()
    )


# ============================================================
# RESISTANCE
# ============================================================

def get_resistance(
    df,
    lookback=SUPPORT_RESISTANCE_LOOKBACK
):

    if len(df) < lookback + 1:
        return float("nan")

    return float(
        df["high"]
        .iloc[-lookback:]
        .max()
    )


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(
    df15,
    trend
):

    if len(df15) < (
        BREAKOUT_LOOKBACK + 2
    ):
        return None

    rvol = calculate_rvol(
        df15
    )

    last = df15.iloc[-1]

    previous = df15.iloc[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    previous_high = float(
        previous["high"].max()
    )

    previous_low = float(
        previous["low"].min()
    )

    close = float(
        last["close"]
    )

    if (
        trend == "LONG"
        and close > previous_high
        and rvol >= RVOL_ABNORMAL
    ):

        return "BREAKOUT"

    if (
        trend == "SHORT"
        and close < previous_low
        and rvol >= RVOL_ABNORMAL
    ):

        return "BREAKOUT"

    return None


# ============================================================
# REJECTION
# ============================================================

def detect_rejection(
    df15,
    trend
):

    if len(df15) < 30:
        return None

    rvol = calculate_rvol(
        df15
    )

    if rvol < RVOL_ABNORMAL:
        return None

    last = df15.iloc[-1]

    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])

    body = abs(c - o)

    if body <= 0:

        body = max(
            abs(h - l) * 0.01,
            1e-12
        )

    upper_wick = (
        h - max(o, c)
    )

    lower_wick = (
        min(o, c) - l
    )

    support = get_support(
        df15
    )

    resistance = get_resistance(
        df15
    )

    # --------------------------------------------------------
    # LONG rejection
    # --------------------------------------------------------

    if trend == "LONG":

        near_support = (
            np.isfinite(support)
            and
            abs(c - support) / c
            <= REJECTION_DISTANCE
        )

        bullish = c > o

        if (
            near_support
            and bullish
            and lower_wick
            >= body * WICK_BODY_RATIO
        ):

            return "REJECTION"

    # --------------------------------------------------------
    # SHORT rejection
    # --------------------------------------------------------

    if trend == "SHORT":

        near_resistance = (
            np.isfinite(resistance)
            and
            abs(c - resistance) / c
            <= REJECTION_DISTANCE
        )

        bearish = c < o

        if (
            near_resistance
            and bearish
            and upper_wick
            >= body * WICK_BODY_RATIO
        ):

            return "REJECTION"

    return None


# ============================================================
# SETUP
# ============================================================

def detect_setup(
    df15,
    trend
):

    setup = detect_breakout(
        df15,
        trend
    )

    if setup:
        return setup

    setup = detect_rejection(
        df15,
        trend
    )

    if setup:
        return setup

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df5,
    side
):

    if len(df5) < 2:
        return False, 0.0

    last = df5.iloc[-1]

    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])

    candle_range = h - l

    if candle_range <= 0:
        return False, 0.0

    body = abs(c - o)

    body_ratio = (
        body / candle_range
    )

    if side == "LONG":

        direction_ok = c > o

    else:

        direction_ok = c < o

    confirmed = (
        direction_ok
        and body_ratio
        >= MIN_BODY_RATIO
    )

    return (
        confirmed,
        body_ratio
    )


# ============================================================
# SL / TP
# ============================================================

def build_sl_tp(
    side,
    entry,
    df15,
    df1h
):

    atr15_series = calculate_atr(
        df15,
        ATR_PERIOD
    )

    atr1h_series = calculate_atr(
        df1h,
        ATR_PERIOD
    )

    atr15 = float(
        atr15_series.iloc[-1]
    )

    atr1h = float(
        atr1h_series.iloc[-1]
    )

    atr_values = [
        x
        for x in [
            atr15,
            atr1h
        ]
        if np.isfinite(x)
        and x > 0
    ]

    if not atr_values:

        return {
            "valid": False,
            "reason": "NO ATR"
        }

    atr = min(
        atr_values
    )

    support15 = get_support(
        df15
    )

    resistance15 = get_resistance(
        df15
    )

    support1h = get_support(
        df1h
    )

    resistance1h = get_resistance(
        df1h
    )

    # ========================================================
    # LONG
    # ========================================================

    if side == "LONG":

        structural_candidates = [
            support15,
            support1h
        ]

        structural_candidates = [
            x
            for x in structural_candidates
            if np.isfinite(x)
            and x < entry
        ]

        structural_sl = (
            max(structural_candidates)
            if structural_candidates
            else None
        )

        sl = None
        sl_method = None

        # ----------------------------------------------------
        # Structural SL
        # ----------------------------------------------------

        if structural_sl is not None:

            candidate_sl = (
                structural_sl
                - atr * ATR_SL_BUFFER
            )

            if candidate_sl < entry:

                candidate_sl_pct = (
                    (entry - candidate_sl)
                    / entry
                )

                if (
                    candidate_sl_pct
                    >= MIN_SL_PCT
                    and
                    candidate_sl_pct
                    <= MAX_SL_PCT
                ):

                    sl = candidate_sl
                    sl_method = (
                        "STRUCTURAL"
                    )

        # ----------------------------------------------------
        # ATR fallback
        # ----------------------------------------------------

        if sl is None:

            candidate_sl = (
                entry
                - atr * ATR_SL_BUFFER
            )

            if candidate_sl >= entry:

                return {
                    "valid": False,
                    "reason": "INVALID ATR SL"
                }

            candidate_sl_pct = (
                (entry - candidate_sl)
                / entry
            )

            # ------------------------------------------------
            # IMPORTANT SL RANGE FILTER
            # ------------------------------------------------

            if candidate_sl_pct < MIN_SL_PCT:

                return {
                    "valid": False,
                    "reason": (
                        f"SL "
                        f"{candidate_sl_pct * 100:.2f}% "
                        f"< MIN "
                        f"{MIN_SL_PCT * 100:.2f}%"
                    )
                }

            if candidate_sl_pct > MAX_SL_PCT:

                return {
                    "valid": False,
                    "reason": (
                        f"SL "
                        f"{candidate_sl_pct * 100:.2f}% "
                        f"> MAX "
                        f"{MAX_SL_PCT * 100:.2f}%"
                    )
                }

            sl = candidate_sl
            sl_method = "ATR"

        # ----------------------------------------------------
        # Final SL validation
        # ----------------------------------------------------

        risk = entry - sl

        if risk <= 0:

            return {
                "valid": False,
                "reason": "INVALID LONG RISK"
            }

        sl_pct = (
            risk / entry
        )

        if sl_pct < MIN_SL_PCT:

            return {
                "valid": False,
                "reason": (
                    f"SL "
                    f"{sl_pct * 100:.2f}% "
                    f"< MIN "
                    f"{MIN_SL_PCT * 100:.2f}%"
                )
            }

        if sl_pct > MAX_SL_PCT:

            return {
                "valid": False,
                "reason": (
                    f"SL "
                    f"{sl_pct * 100:.2f}% "
                    f"> MAX "
                    f"{MAX_SL_PCT * 100:.2f}%"
                )
            }

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        structural_tp_candidates = [
            resistance15,
            resistance1h
        ]

        structural_tp_candidates = [
            x
            for x in structural_tp_candidates
            if np.isfinite(x)
            and x > entry
        ]

        min_tp = (
            entry
            + risk * MIN_RR
        )

        if structural_tp_candidates:

            structural_tp = max(
                structural_tp_candidates
            )

            tp = max(
                structural_tp,
                min_tp
            )

        else:

            tp = min_tp

        rr = (
            (tp - entry)
            / risk
        )

        if rr < MIN_RR:

            return {
                "valid": False,
                "reason": (
                    f"RR {rr:.2f} "
                    f"< MIN {MIN_RR:.2f}"
                )
            }

        return {
            "valid": True,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "sl_pct": sl_pct,
            "sl_method": sl_method
        }

    # ========================================================
    # SHORT
    # ========================================================

    structural_candidates = [
        resistance15,
        resistance1h
    ]

    structural_candidates = [
        x
        for x in structural_candidates
        if np.isfinite(x)
        and x > entry
    ]

    structural_sl = (
        min(structural_candidates)
        if structural_candidates
        else None
    )

    sl = None
    sl_method = None

    # --------------------------------------------------------
    # Structural SL
    # --------------------------------------------------------

    if structural_sl is not None:

        candidate_sl = (
            structural_sl
            + atr * ATR_SL_BUFFER
        )

        if candidate_sl > entry:

            candidate_sl_pct = (
                (candidate_sl - entry)
                / entry
            )

            if (
                candidate_sl_pct
                >= MIN_SL_PCT
                and
                candidate_sl_pct
                <= MAX_SL_PCT
            ):

                sl = candidate_sl
                sl_method = (
                    "STRUCTURAL"
                )

    # --------------------------------------------------------
    # ATR fallback
    # --------------------------------------------------------

    if sl is None:

        candidate_sl = (
            entry
            + atr * ATR_SL_BUFFER
        )

        if candidate_sl <= entry:

            return {
                "valid": False,
                "reason": "INVALID ATR SL"
            }

        candidate_sl_pct = (
            (candidate_sl - entry)
            / entry
        )

        # ----------------------------------------------------
        # IMPORTANT SL RANGE FILTER
        # ----------------------------------------------------

        if candidate_sl_pct < MIN_SL_PCT:

            return {
                "valid": False,
                "reason": (
                    f"SL "
                    f"{candidate_sl_pct * 100:.2f}% "
                    f"< MIN "
                    f"{MIN_SL_PCT * 100:.2f}%"
                )
            }

        if candidate_sl_pct > MAX_SL_PCT:

            return {
                "valid": False,
                "reason": (
                    f"SL "
                    f"{candidate_sl_pct * 100:.2f}% "
                    f"> MAX "
                    f"{MAX_SL_PCT * 100:.2f}%"
                )
            }

        sl = candidate_sl
        sl_method = "ATR"

    # --------------------------------------------------------
    # Final SL validation
    # --------------------------------------------------------

    risk = sl - entry

    if risk <= 0:

        return {
            "valid": False,
            "reason": "INVALID SHORT RISK"
        }

    sl_pct = (
        risk / entry
    )

    if sl_pct < MIN_SL_PCT:

        return {
            "valid": False,
            "reason": (
                f"SL "
                f"{sl_pct * 100:.2f}% "
                f"< MIN "
                f"{MIN_SL_PCT * 100:.2f}%"
            )
        }

    if sl_pct > MAX_SL_PCT:

        return {
            "valid": False,
            "reason": (
                f"SL "
                f"{sl_pct * 100:.2f}% "
                f"> MAX "
                f"{MAX_SL_PCT * 100:.2f}%"
            )
        }

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    structural_tp_candidates = [
        support15,
        support1h
    ]

    structural_tp_candidates = [
        x
        for x in structural_tp_candidates
        if np.isfinite(x)
        and x < entry
    ]

    min_tp = (
        entry
        - risk * MIN_RR
    )

    if structural_tp_candidates:

        structural_tp = min(
            structural_tp_candidates
        )

        tp = min(
            structural_tp,
            min_tp
        )

    else:

        tp = min_tp

    rr = (
        (entry - tp)
        / risk
    )

    if rr < MIN_RR:

        return {
            "valid": False,
            "reason": (
                f"RR {rr:.2f} "
                f"< MIN {MIN_RR:.2f}"
            )
        }

    return {
        "valid": True,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "sl_pct": sl_pct,
        "sl_method": sl_method
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    rvol,
    confirmation
):

    score = 0

    if trend in (
        "LONG",
        "SHORT"
    ):

        score += 3

    if setup:

        score += 4

    if rvol >= RVOL_VERY_STRONG:

        score += 4

    elif rvol >= RVOL_STRONG:

        score += 4

    elif rvol >= RVOL_ABNORMAL:

        score += 3

    if confirmation:

        score += 4

    return score


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(
    symbol
):

    row = get_last_trade_for_symbol(
        symbol
    )

    if not row:
        return False

    created = row["created_at"]

    if not created:
        return False

    try:

        dt = datetime.fromisoformat(
            created
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

    except Exception:

        return False

    elapsed = (
        now_utc() - dt
    ).total_seconds()

    candle_seconds = 5 * 60

    return (
        elapsed
        <
        COOLDOWN_CANDLES
        * candle_seconds
    )


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    exit_price
):

    if side == "LONG":

        return (
            (
                exit_price - entry
            )
            / entry
            * 100
        )

    return (
        (
            entry - exit_price
        )
        / entry
        * 100
    )


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    open_trades = get_open_trades()

    for trade in open_trades:

        symbol = trade["symbol"]
        side = trade["side"]

        try:

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            if df5.empty:
                continue

            entry = float(
                trade["entry"]
            )

            sl = float(
                trade["sl"]
            )

            tp = float(
                trade["tp"]
            )

            exit_price = None
            reason = None

            last = df5.iloc[-1]

            candle_low = float(
                last["low"]
            )

            candle_high = float(
                last["high"]
            )

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                if candle_low <= sl:

                    exit_price = sl
                    reason = "SL"

                elif candle_high >= tp:

                    exit_price = tp
                    reason = "TP"

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                if candle_high >= sl:

                    exit_price = sl
                    reason = "SL"

                elif candle_low <= tp:

                    exit_price = tp
                    reason = "TP"

            if exit_price is None:
                continue

            pnl = calculate_pnl(
                side,
                entry,
                exit_price
            )

            result = (
                "WIN"
                if pnl > 0
                else "LOSS"
            )

            close_trade(
                trade["id"],
                result,
                pnl,
                exit_price,
                reason
            )

        except Exception as e:

            print(
                f"Open trade update "
                f"error {symbol}: {e}"
            )


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = get_db()

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()[0]

    closed_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'WIN'
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'LOSS'
        """
    ).fetchone()[0]

    pnl_row = conn.execute(
        """
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    conn.close()

    pnl = float(
        pnl_row[0] or 0
    )

    if closed_count:

        wr = (
            wins
            / closed_count
            * 100
        )

    else:

        wr = 0.0

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl
    }


# ============================================================
# SCAN
# ============================================================

def scan_markets():

    final_candidates = []

    try:

        symbols = get_top_markets()

    except Exception as e:

        print(
            f"Market list error: {e}"
        )

        return final_candidates

    for symbol in symbols:

        try:

            # ------------------------------------------------
            # 1H
            # ------------------------------------------------

            df1h = fetch_ohlcv(
                symbol,
                TIMEFRAME_1H
            )

            # ------------------------------------------------
            # 15M
            # ------------------------------------------------

            df15 = fetch_ohlcv(
                symbol,
                TIMEFRAME_15M
            )

            # ------------------------------------------------
            # 5M
            # ------------------------------------------------

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            # ------------------------------------------------
            # TREND
            # ------------------------------------------------

            trend = get_trend(
                df1h
            )

            if trend not in (
                "LONG",
                "SHORT"
            ):
                continue

            # ------------------------------------------------
            # SETUP
            # ------------------------------------------------

            setup = detect_setup(
                df15,
                trend
            )

            if not setup:
                continue

            # ------------------------------------------------
            # RVOL
            # ------------------------------------------------

            rvol = calculate_rvol(
                df15
            )

            # ------------------------------------------------
            # 5M CONFIRMATION
            # ------------------------------------------------

            confirmed, body_ratio = (
                confirm_5m(
                    df5,
                    trend
                )
            )

            if not confirmed:
                continue

            # ------------------------------------------------
            # SCORE
            # ------------------------------------------------

            score = calculate_score(
                trend,
                setup,
                rvol,
                confirmed
            )

            if score < MIN_SCORE:
                continue

            # ------------------------------------------------
            # COOLDOWN
            # ------------------------------------------------

            if cooldown_active(
                symbol
            ):
                continue

            # ------------------------------------------------
            # ENTRY
            # ------------------------------------------------

            entry = float(
                df5["close"].iloc[-1]
            )

            # ------------------------------------------------
            # SL / TP
            # ------------------------------------------------

            sltp = build_sl_tp(
                trend,
                entry,
                df15,
                df1h
            )

            if (
                not sltp
                or not sltp.get(
                    "valid",
                    False
                )
            ):
                continue

            # ------------------------------------------------
            # EXTRA FINAL SL RANGE CHECK
            # ------------------------------------------------

            sl_pct = float(
                sltp["sl_pct"]
            )

            if sl_pct < MIN_SL_PCT:
                continue

            if sl_pct > MAX_SL_PCT:
                continue

            # ------------------------------------------------
            # FINAL CANDIDATE
            # ------------------------------------------------

            candidate = {
                "symbol": symbol,
                "side": trend,
                "setup": setup,
                "rvol": rvol,
                "body_ratio": body_ratio,
                "score": score,
                "entry": sltp["entry"],
                "sl": sltp["sl"],
                "tp": sltp["tp"],
                "rr": sltp["rr"],
                "sl_pct": sltp["sl_pct"],
                "sl_method": sltp[
                    "sl_method"
                ]
            }

            final_candidates.append(
                candidate
            )

        except Exception as e:

            print(
                f"Scan error "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # Ranking
    # --------------------------------------------------------

    final_candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"]
        ),
        reverse=True
    )

    return final_candidates


# ============================================================
# CREATE NEW TRADES
# ============================================================

def create_new_trades(
    final_candidates
):

    created = []

    current_open = (
        get_open_count()
    )

    available_slots = max(
        0,
        MAX_OPEN_TRADES
        - current_open
    )

    if available_slots <= 0:

        return created

    for candidate in final_candidates:

        if (
            len(created)
            >= MAX_NEW_SIGNALS
        ):
            break

        if (
            len(created)
            >= available_slots
        ):
            break

        symbol = candidate[
            "symbol"
        ]

        # ----------------------------------------------------
        # Prevent duplicate OPEN symbol
        # ----------------------------------------------------

        existing = (
            get_last_trade_for_symbol(
                symbol
            )
        )

        if (
            existing
            and existing["status"]
            == "OPEN"
        ):
            continue

        # ----------------------------------------------------
        # Final SL protection
        # ----------------------------------------------------

        sl_pct = float(
            candidate["sl_pct"]
        )

        if sl_pct < MIN_SL_PCT:
            continue

        if sl_pct > MAX_SL_PCT:
            continue

        insert_trade(
            symbol=candidate[
                "symbol"
            ],
            side=candidate[
                "side"
            ],
            setup=candidate[
                "setup"
            ],
            entry=candidate[
                "entry"
            ],
            sl=candidate[
                "sl"
            ],
            tp=candidate[
                "tp"
            ],
            rr=candidate[
                "rr"
            ],
            score=candidate[
                "score"
            ]
        )

        created.append(
            candidate
        )

    return created


# ============================================================
# FORMAT NEW SIGNALS
# ============================================================

def format_signals(
    signals
):

    if not signals:

        return (
            "🎯 NEW SIGNALS\n"
            "None"
        )

    lines = [
        f"🎯 NEW SIGNALS "
        f"({len(signals)})"
    ]

    for x in signals:

        if x["side"] == "LONG":

            emoji = "🟢"

        else:

            emoji = "🔴"

        lines.append("")

        lines.append(
            f"{emoji} "
            f"{x['symbol']} "
            f"{x['side']}"
        )

        lines.append(
            f"Entry: "
            f"{x['entry']:.8f}"
        )

        lines.append(
            f"SL: "
            f"{x['sl']:.8f} "
            f"({x['sl_pct'] * 100:.2f}%)"
        )

        lines.append(
            f"TP: "
            f"{x['tp']:.8f}"
        )

        lines.append(
            f"RR: 1:{x['rr']:.2f}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def format_open_trades():

    trades = get_open_trades()

    if not trades:

        return (
            "📂 OPEN TRADES\n"
            "None"
        )

    lines = [
        f"📂 OPEN TRADES "
        f"({len(trades)}/"
        f"{MAX_OPEN_TRADES})"
    ]

    for trade in trades:

        symbol = trade[
            "symbol"
        ]

        side = trade[
            "side"
        ]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        try:

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            current = float(
                df5["close"].iloc[-1]
            )

        except Exception:

            current = entry

        pnl = calculate_pnl(
            side,
            entry,
            current
        )

        # ----------------------------------------------------
        # SIDE EMOJI
        # LONG = GREEN
        # SHORT = RED
        # ----------------------------------------------------

        if side == "LONG":

            emoji = "🟢"

        else:

            emoji = "🔴"

        # ----------------------------------------------------
        # SL PERCENT
        # ----------------------------------------------------

        if side == "LONG":

            sl_pct = (
                (entry - sl)
                / entry
                * 100
            )

        else:

            sl_pct = (
                (sl - entry)
                / entry
                * 100
            )

        lines.append("")

        lines.append(
            f"{emoji} "
            f"{symbol} "
            f"{side}"
        )

        lines.append(
            f"PnL: "
            f"{pnl:+.2f}%"
        )

        lines.append(
            f"Entry: "
            f"{entry:.8f}"
        )

        lines.append(
            f"Now: "
            f"{current:.8f}"
        )

        lines.append(
            f"SL: "
            f"{sl:.8f} "
            f"({sl_pct:.2f}%)"
        )

        lines.append(
            f"TP: "
            f"{tp:.8f}"
        )

        lines.append(
            f"RR: "
            f"1:{float(trade['rr']):.2f}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# PERFORMANCE
# ============================================================

def format_performance():

    p = get_performance()

    return (
        "📈 PERFORMANCE\n"
        f"✅ Wins: {p['wins']}\n"
        f"❌ Losses: {p['losses']}\n"
        f"🎯 Win Rate: "
        f"{p['wr']:.1f}%\n"
        f"💰 Realized PnL: "
        f"{p['pnl']:+.3f}%"
    )


# ============================================================
# MAIN REPORT
# ============================================================

def build_report(
    signals
):

    parts = []

    parts.append(
        "📊 VOLUME-KHAT 100"
    )

    parts.append(
        f"🕐 {iran_time_string()}"
    )

    parts.append(
        "⚡ Kraken Futures | "
        "5M CLOSED"
    )

    parts.append(
        format_signals(
            signals
        )
    )

    parts.append(
        format_open_trades()
    )

    parts.append(
        format_performance()
    )

    if PAPER_TRADING:

        parts.append(
            "🧪 PAPER TRADING"
        )

    else:

        parts.append(
            "⚠️ LIVE TRADING"
        )

    return "\n\n".join(
        parts
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        "VOLUME-KHAT 100 v3.5 START"
    )

    print(
        "=================================================="
    )

    init_db()

    # --------------------------------------------------------
    # 1. Update existing OPEN trades
    # --------------------------------------------------------

    update_open_trades()

    # --------------------------------------------------------
    # 2. Scan TOP 100
    # --------------------------------------------------------

    final_candidates = (
        scan_markets()
    )

    # --------------------------------------------------------
    # 3. Create new trades
    # --------------------------------------------------------

    signals = create_new_trades(
        final_candidates
    )

    # --------------------------------------------------------
    # 4. Build compact report
    # --------------------------------------------------------

    report = build_report(
        signals
    )

    print(report)

    # --------------------------------------------------------
    # 5. Telegram
    # --------------------------------------------------------

    send_telegram(
        report
    )

    print(
        "=================================================="
    )

    print(
        "VOLUME-KHAT 100 v3.5 END"
    )

    print(
        "=================================================="
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "FATAL ERROR:"
        )

        print(
            str(e)
        )

        traceback.print_exc()

        try:

            send_telegram(
                "🚨 VOLUME-KHAT 100 ERROR\n"
                f"{str(e)}"
            )

        except Exception:
            pass
