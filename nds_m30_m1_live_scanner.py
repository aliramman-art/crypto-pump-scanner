# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.7
# ============================================================
#
# PAPER ONLY
#
# M30:
#   Detect NDS Hook
#
# SHORT / POSITIVE HOOK:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   START = lowest point of the Hook
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# M1:
#   After M30 H3 activator:
#
#   1 < 2 < 3 < F
#
#   F = highest point and SHORT ENTRY
#
# LONG / NEGATIVE HOOK:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   START = highest point of the Hook
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
# M1:
#   After M30 L3 activator:
#
#   1 > 2 > 3 > F
#
#   F = lowest point and LONG ENTRY
#
# H3/L3 are M30 activators.
# They are also displayed on the M1 chart.
#
# TP:
#   86.4% measured from M30 START to M30 H3/L3.
#
# NO LIVE ORDERS ARE EVER SENT.
# ============================================================

from __future__ import annotations

import os
import time
import math
import sqlite3
import traceback

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import requests
import pandas as pd

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.4.7"

PAPER_ONLY = True

DB_FILE = os.getenv(
    "NDS_DB_FILE",
    "nds_m30_m1_v44.db"
)

CHART_DIR = Path(
    os.getenv(
        "NDS_CHART_DIR",
        "nds_charts"
    )
)

CHART_DIR.mkdir(
    parents=True,
    exist_ok=True
)

BASE_URL = (
    "https://futures.kraken.com/"
    "api/charts/v1/trade"
)

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN", "")
)

TELEGRAM_CHAT = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT", "")
)

M30_COUNT = int(
    os.getenv(
        "NDS_M30_COUNT",
        "250"
    )
)

M1_COUNT = int(
    os.getenv(
        "NDS_M1_COUNT",
        "700"
    )
)

PIVOT_LEFT = int(
    os.getenv(
        "NDS_PIVOT_LEFT",
        "2"
    )
)

PIVOT_RIGHT = int(
    os.getenv(
        "NDS_PIVOT_RIGHT",
        "2"
    )
)

PRICE_TOL = float(
    os.getenv(
        "NDS_PRICE_TOL",
        "0.002"
    )
)

MIN_SWING_PCT = float(
    os.getenv(
        "NDS_MIN_SWING_PCT",
        "0.001"
    )
)

MAX_HOOK_AGE_MIN = int(
    os.getenv(
        "NDS_MAX_HOOK_AGE_MIN",
        "240"
    )
)

MAX_SETUP_AGE_MIN = int(
    os.getenv(
        "NDS_MAX_SETUP_AGE_MIN",
        "10"
    )
)

CANDIDATE_REPEAT_MIN = int(
    os.getenv(
        "NDS_CANDIDATE_REPEAT_MIN",
        "180"
    )
)

REQUEST_TIMEOUT = 20


# ============================================================
# SYMBOLS
# ============================================================

DEFAULT_SYMBOLS = [
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_LINKUSD",
    "PF_AVAXUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",
    "PF_BCHUSD",
    "PF_TRXUSD",
    "PF_UNIUSD",
    "PF_ATOMUSD",
    "PF_NEARUSD",
    "PF_APTUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_INJUSD",
    "PF_SUIUSD",
    "PF_SEIUSD",
    "PF_TIAUSD",
    "PF_FILUSD",
    "PF_ETCUSD",
    "PF_AAVEUSD",
    "PF_ALGOUSD",
    "PF_FTMUSD",
    "PF_GALAUSD",
    "PF_SANDUSD",
    "PF_MANAUSD",
    "PF_CRVUSD",
    "PF_LDOUSD",
    "PF_RUNEUSD",
    "PF_EOSUSD",
    "PF_XLMUSD",
    "PF_PEPEUSD",
    "PF_WIFUSD",
    "PF_BONKUSD",
    "PF_ENSUSD",
    "PF_ATOMUSD",
]

SYMBOLS = [
    s.strip()
    for s in os.getenv(
        "NDS_SYMBOLS",
        ",".join(DEFAULT_SYMBOLS)
    ).split(",")
    if s.strip()
]

# Remove SNXX permanently.
SYMBOLS = [
    s
    for s in SYMBOLS
    if "SNXX" not in s.upper()
]

# Ensure LDO exists.
if "PF_LDOUSD" not in SYMBOLS:
    SYMBOLS.append("PF_LDOUSD")


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class Pivot:
    index: int
    time: pd.Timestamp
    price: float
    kind: str


@dataclass
class Hook:
    direction: str

    start: Pivot

    p1: Pivot
    p2: Pivot
    p3: Pivot
    p4: Pivot

    end: Pivot

    tp864: float

    score: float

    fingerprint: str


# ============================================================
# HELPERS
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_price(
    x: Optional[float]
) -> str:

    if x is None:
        return "N/A"

    try:
        x = float(x)
    except Exception:
        return "N/A"

    if not math.isfinite(x):
        return "N/A"

    if abs(x) >= 1000:
        return f"{x:.2f}"

    if abs(x) >= 1:
        return (
            f"{x:.5f}"
            .rstrip("0")
            .rstrip(".")
        )

    return (
        f"{x:.8f}"
        .rstrip("0")
        .rstrip(".")
    )


def pct(
    a: float,
    b: float
) -> float:

    return (
        abs(a - b)
        / max(abs(a), 1e-12)
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text: str,
    photo: Optional[Path] = None
) -> bool:

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print(
            "Telegram credentials missing; "
            "message not sent."
        )
        return False

    try:

        if photo and photo.exists():

            with photo.open("rb") as f:

                response = requests.post(
                    (
                        "https://api.telegram.org/"
                        f"bot{TELEGRAM_TOKEN}/sendPhoto"
                    ),
                    data={
                        "chat_id": TELEGRAM_CHAT,
                        "caption": text[:1024],
                        "parse_mode": "HTML",
                    },
                    files={
                        "photo": f
                    },
                    timeout=35,
                )

        else:

            response = requests.post(
                (
                    "https://api.telegram.org/"
                    f"bot{TELEGRAM_TOKEN}/sendMessage"
                ),
                data={
                    "chat_id": TELEGRAM_CHAT,
                    "text": text[:4000],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=25,
            )

        if not response.ok:

            print(
                "Telegram error:",
                response.status_code,
                response.text[:500]
            )

        return response.ok

    except Exception as e:

        print(
            "Telegram exception:",
            repr(e)
        )

        return False


# ============================================================
# DATABASE
# ============================================================

def init_db():

    with sqlite3.connect(DB_FILE) as con:

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                direction TEXT,
                status TEXT,
                entry REAL,
                sl REAL,
                tp REAL,
                current_price REAL,
                pnl_pct REAL DEFAULT 0,
                hook_fingerprint TEXT,
                created_at TEXT,
                closed_at TEXT,
                exit_price REAL,
                exit_reason TEXT,
                chart_path TEXT
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS scanner_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time TEXT,
                scanned INTEGER,
                data_ok INTEGER,
                hooks INTEGER,
                m1_requests INTEGER,
                m1_ok INTEGER,
                candidates INTEGER,
                signals INTEGER,
                errors INTEGER,
                best_symbol TEXT,
                best_direction TEXT
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_notices (
                fingerprint TEXT PRIMARY KEY,
                last_sent TEXT,
                symbol TEXT,
                direction TEXT
            )
            """
        )

        cols = {
            r[1]
            for r in con.execute(
                "PRAGMA table_info(signals)"
            ).fetchall()
        }

        migrations = [
            ("hook_fingerprint", "TEXT"),
            ("chart_path", "TEXT"),
            ("current_price", "REAL"),
            ("pnl_pct", "REAL DEFAULT 0"),
        ]

        for name, typ in migrations:

            if name not in cols:

                con.execute(
                    f"ALTER TABLE signals "
                    f"ADD COLUMN {name} {typ}"
                )


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(
    symbol: str,
    resolution: str,
    count: int
) -> Optional[pd.DataFrame]:

    url = (
        f"{BASE_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    try:

        response = requests.get(
            url,
            params={
                "count": count
            },
            timeout=REQUEST_TIMEOUT
        )

        if not response.ok:
            return None

        payload = response.json()

        rows = (
            payload.get("candles")
            or payload.get("data")
            or payload.get("results")
            or []
        )

        if isinstance(rows, dict):

            rows = (
                rows.get("candles")
                or rows.get("data")
                or []
            )

        if not rows:
            return None

        df = pd.DataFrame(rows)

        aliases = {
            "t": "time",
            "timestamp": "time",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        }

        rename_map = {}

        for source, target in aliases.items():

            if (
                source in df.columns
                and target not in df.columns
            ):
                rename_map[source] = target

        if rename_map:
            df = df.rename(
                columns=rename_map
            )

        required = [
            "time",
            "open",
            "high",
            "low",
            "close",
        ]

        if not all(
            c in df.columns
            for c in required
        ):
            return None

        for column in [
            "open",
            "high",
            "low",
            "close",
        ]:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        if pd.api.types.is_numeric_dtype(
            df["time"]
        ):

            raw = pd.to_numeric(
                df["time"],
                errors="coerce"
            )

            median = (
                raw.dropna().median()
                if not raw.dropna().empty
                else 0
            )

            unit = (
                "ms"
                if median > 1e12
                else "s"
            )

            df["time"] = pd.to_datetime(
                raw,
                unit=unit,
                utc=True,
                errors="coerce"
            )

        else:

            df["time"] = pd.to_datetime(
                df["time"],
                utc=True,
                errors="coerce"
            )

        df = (
            df
            .dropna(subset=required)
            .sort_values("time")
            .drop_duplicates("time")
            .reset_index(drop=True)
        )

        if len(df) > count:

            df = (
                df
                .iloc[-count:]
                .reset_index(drop=True)
            )

        if len(df) < 30:
            return None

        # Ignore currently forming candle.
        df = (
            df
            .iloc[:-1]
            .reset_index(drop=True)
        )

        return df

    except Exception as e:

        print(
            f"Fetch error "
            f"{symbol} "
            f"{resolution}: "
            f"{e}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    df: pd.DataFrame,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
    alternating: bool = True
) -> List[Pivot]:

    raw: List[Pivot] = []

    if df is None:
        return raw

    if len(df) < left + right + 5:
        return raw

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    for i in range(
        left,
        len(df) - right
    ):

        high_window = highs[
            i-left:i+right+1
        ]

        low_window = lows[
            i-left:i+right+1
        ]

        is_high = (
            highs[i] >= max(high_window)
            and (
                highs[i] > max(
                    high_window[:left]
                )
                or
                highs[i] > max(
                    high_window[left+1:]
                )
            )
        )

        is_low = (
            lows[i] <= min(low_window)
            and (
                lows[i] < min(
                    low_window[:left]
                )
                or
                lows[i] < min(
                    low_window[left+1:]
                )
            )
        )

        if is_high:

            raw.append(
                Pivot(
                    index=i,
                    time=df.iloc[i]["time"],
                    price=float(highs[i]),
                    kind="H",
                )
            )

        if is_low:

            raw.append(
                Pivot(
                    index=i,
                    time=df.iloc[i]["time"],
                    price=float(lows[i]),
                    kind="L",
                )
            )

    raw.sort(
        key=lambda p: (
            p.index,
            0 if p.kind == "H" else 1
        )
    )

    if not alternating:
        return raw

    out: List[Pivot] = []

    for p in raw:

        if not out:

            out.append(p)
            continue

        last = out[-1]

        if p.kind == last.kind:

            if (
                p.kind == "H"
                and p.price >= last.price
            ):

                out[-1] = p

            elif (
                p.kind == "L"
                and p.price <= last.price
            ):

                out[-1] = p

        else:

            if (
                abs(p.price - last.price)
                / max(abs(last.price), 1e-12)
                >= MIN_SWING_PCT
            ):

                out.append(p)

    return out


# ============================================================
# M30 HOOK DETECTION
# ============================================================

def build_hooks(
    df: pd.DataFrame,
    pivots: List[Pivot]
) -> List[Hook]:

    hooks: List[Hook] = []

    if len(pivots) < 6:
        return hooks

    # --------------------------------------------------------
    # SHORT / POSITIVE HOOK
    #
    # START -> H1 -> L1 -> H2 -> L2 -> H3
    #
    # START MUST BE THE LOWEST POINT OF THE HOOK.
    #
    # H2 > H1
    # L2 < L1
    # H3 > H2
    # --------------------------------------------------------

    for i in range(
        0,
        len(pivots) - 5
    ):

        q = pivots[i:i+6]

        kinds = "".join(
            p.kind
            for p in q
        )

        if kinds == "LHLHLH":

            start, h1, l1, h2, l2, h3 = q

            valid = (
                start.price < l1.price
                and start.price < l2.price
                and h2.price > h1.price
                and l2.price < l1.price
                and h3.price > h2.price
            )

            if not valid:
                continue

            span = (
                h3.price
                - start.price
            )

            if span <= 0:
                continue

            if pct(
                start.price,
                h3.price
            ) < MIN_SWING_PCT:

                continue

            tp864 = (
                h3.price
                - 0.864 * (
                    h3.price
                    - start.price
                )
            )

            age = max(
                0.0,
                (
                    df.iloc[-1]["time"]
                    - h3.time
                ).total_seconds()
                / 60.0
            )

            score = (
                max(
                    0,
                    100 - age / 3
                )
                +
                min(
                    30,
                    pct(
                        start.price,
                        h3.price
                    ) * 1000
                )
            )

            fingerprint = (
                f"SHORT:"
                f"{int(start.time.timestamp())}:"
                f"{int(h3.time.timestamp())}:"
                f"{h3.price:.8f}"
            )

            hooks.append(
                Hook(
                    direction="SHORT",
                    start=start,
                    p1=h1,
                    p2=l1,
                    p3=h2,
                    p4=l2,
                    end=h3,
                    tp864=tp864,
                    score=score,
                    fingerprint=fingerprint,
                )
            )

        # ----------------------------------------------------
        # LONG / NEGATIVE HOOK
        #
        # START -> L1 -> H1 -> L2 -> H2 -> L3
        #
        # START MUST BE THE HIGHEST POINT OF THE HOOK.
        #
        # L2 < L1
        # H2 > H1
        # L3 < L2
        # ----------------------------------------------------

        elif kinds == "HLHLHL":

            start, l1, h1, l2, h2, l3 = q

            valid = (
                start.price > h1.price
                and start.price > h2.price
                and l2.price < l1.price
                and h2.price > h1.price
                and l3.price < l2.price
            )

            if not valid:
                continue

            span = (
                start.price
                - l3.price
            )

            if span <= 0:
                continue

            if pct(
                start.price,
                l3.price
            ) < MIN_SWING_PCT:

                continue

            tp864 = (
                l3.price
                + 0.864 * (
                    start.price
                    - l3.price
                )
            )

            age = max(
                0.0,
                (
                    df.iloc[-1]["time"]
                    - l3.time
                ).total_seconds()
                / 60.0
            )

            score = (
                max(
                    0,
                    100 - age / 3
                )
                +
                min(
                    30,
                    pct(
                        start.price,
                        l3.price
                    ) * 1000
                )
            )

            fingerprint = (
                f"LONG:"
                f"{int(start.time.timestamp())}:"
                f"{int(l3.time.timestamp())}:"
                f"{l3.price:.8f}"
            )

            hooks.append(
                Hook(
                    direction="LONG",
                    start=start,
                    p1=l1,
                    p2=h1,
                    p3=l2,
                    p4=h2,
                    end=l3,
                    tp864=tp864,
                    score=score,
                    fingerprint=fingerprint,
                )
            )

    # --------------------------------------------------------
    # RECENCY FILTER
    # --------------------------------------------------------

    now = df.iloc[-1]["time"]

    hooks = [
        h
        for h in hooks
        if (
            now - h.end.time
        ).total_seconds()
        / 60.0
        <= MAX_HOOK_AGE_MIN
    ]

    hooks.sort(
        key=lambda h: (
            h.score,
            h.end.time.value
        ),
        reverse=True
    )

    unique = {}

    for hook in hooks:

        unique[
            hook.fingerprint
        ] = hook

    return list(
        unique.values()
    )


# ============================================================
# M1 123F
# ============================================================

def m1_structure(
    df: pd.DataFrame,
    direction: str,
    hook_time: pd.Timestamp
) -> Dict[str, Any]:

    # IMPORTANT:
    # We intentionally use independent H/L pivots.
    # The four M1 highs/lows do NOT have to be adjacent
    # in the alternating pivot list.

    pivots = find_pivots(
        df,
        alternating=False
    )

    # Only points AFTER the M30 activator.
    pivots = [
        p
        for p in pivots
        if p.time > hook_time
    ]

    highs = [
        p
        for p in pivots
        if p.kind == "H"
    ]

    lows = [
        p
        for p in pivots
        if p.kind == "L"
    ]

    result = {
        "points": [],
        "confirmed": False,
        "status": "WAITING FOR 1M STRUCTURE",
        "entry": None,
        "sl": None,
    }

    # ========================================================
    # SHORT
    #
    # 1 < 2 < 3 < F
    #
    # F MUST BE THE HIGHEST POINT.
    # ========================================================

    if direction == "SHORT":

        candidates = []

        for i in range(
            3,
            len(highs)
        ):

            p1, p2, p3, pf = highs[
                i-3:i+1
            ]

            if not (
                p1.price
                < p2.price
                < p3.price
                < pf.price
            ):
                continue

            age = (
                df.iloc[-1]["time"]
                - pf.time
            ).total_seconds() / 60.0

            if age > MAX_SETUP_AGE_MIN:
                continue

            candidates.append(
                (
                    p1,
                    p2,
                    p3,
                    pf
                )
            )

        if candidates:

            seq = candidates[-1]

            result["points"] = list(seq)

            result["status"] = (
                "123F SHORT CONFIRMED"
            )

            result["confirmed"] = True

            result["entry"] = (
                seq[-1].price
            )

            # SL = highest valid M1 swing high
            # around the structure, otherwise 1%.
            prior_highs = [
                p
                for p in highs
                if (
                    p.index > seq[0].index
                    and p.index < seq[-1].index
                )
            ]

            if prior_highs:

                result["sl"] = max(
                    p.price
                    for p in prior_highs
                )

            else:

                result["sl"] = (
                    seq[-1].price
                    * 1.01
                )

        elif highs:

            # Show the latest developing sequence.
            result["points"] = highs[-4:]

            result["status"] = (
                "BUILDING SHORT 123F: "
                f"{len(result['points'])}/4 HIGHS"
            )

    # ========================================================
    # LONG
    #
    # 1 > 2 > 3 > F
    #
    # F MUST BE THE LOWEST POINT.
    # ========================================================

    else:

        candidates = []

        for i in range(
            3,
            len(lows)
        ):

            p1, p2, p3, pf = lows[
                i-3:i+1
            ]

            if not (
                p1.price
                > p2.price
                > p3.price
                > pf.price
            ):
                continue

            age = (
                df.iloc[-1]["time"]
                - pf.time
            ).total_seconds() / 60.0

            if age > MAX_SETUP_AGE_MIN:
                continue

            candidates.append(
                (
                    p1,
                    p2,
                    p3,
                    pf
                )
            )

        if candidates:

            seq = candidates[-1]

            result["points"] = list(seq)

            result["status"] = (
                "123F LONG CONFIRMED"
            )

            result["confirmed"] = True

            result["entry"] = (
                seq[-1].price
            )

            # SL = lowest valid M1 swing low
            # around the structure, otherwise 1%.
            prior_lows = [
                p
                for p in lows
                if (
                    p.index > seq[0].index
                    and p.index < seq[-1].index
                )
            ]

            if prior_lows:

                result["sl"] = min(
                    p.price
                    for p in prior_lows
                )

            else:

                result["sl"] = (
                    seq[-1].price
                    * 0.99
                )

        elif lows:

            result["points"] = lows[-4:]

            result["status"] = (
                "BUILDING LONG 123F: "
                f"{len(result['points'])}/4 LOWS"
            )

    return result


# ============================================================
# CHART
# ============================================================

def make_chart(
    symbol: str,
    m30: pd.DataFrame,
    m1: Optional[pd.DataFrame],
    hook: Hook,
    structure: Optional[Dict[str, Any]],
    current: float,
    candidate: bool = True
) -> Path:

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(15, 10),
        gridspec_kw={
            "height_ratios": [
                1.15,
                1
            ]
        }
    )

    # ========================================================
    # M30 PANEL
    # ========================================================

    ax = axes[0]

    view = (
        m30
        .tail(100)
        .reset_index(drop=True)
    )

    for _, row in view.iterrows():

        x = mdates.date2num(
            row["time"].to_pydatetime()
        )

        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        ax.vlines(
            x,
            l,
            h,
            color="black",
            linewidth=0.7
        )

        body_color = (
            "#159447"
            if c >= o
            else "#cf3030"
        )

        ax.add_patch(
            Rectangle(
                (
                    x - 0.008,
                    min(o, c)
                ),
                0.016,
                max(
                    abs(c - o),
                    max(abs(c), 1)
                    * 0.00001
                ),
                facecolor=body_color,
                edgecolor=body_color,
                linewidth=0.5,
            )
        )

    if hook.direction == "SHORT":

        labels = [
            ("START", hook.start),
            ("H1", hook.p1),
            ("L1", hook.p2),
            ("H2", hook.p3),
            ("L2", hook.p4),
            ("H3", hook.end),
        ]

    else:

        labels = [
            ("START", hook.start),
            ("L1", hook.p1),
            ("H1", hook.p2),
            ("L2", hook.p3),
            ("H2", hook.p4),
            ("L3", hook.end),
        ]

    for label, point in labels:

        x = mdates.date2num(
            point.time.to_pydatetime()
        )

        ax.scatter(
            x,
            point.price,
            s=50,
            zorder=5
        )

        ax.annotate(
            f"{label}\n"
            f"{fmt_price(point.price)}",
            (
                x,
                point.price
            ),
            xytext=(4, 8),
            textcoords="offset points",
            fontsize=8,
        )

    ax.axhline(
        hook.tp864,
        linestyle="--",
        linewidth=1.3,
        label=(
            f"86.4% TP "
            f"{fmt_price(hook.tp864)}"
        )
    )

    ax.axhline(
        current,
        linestyle=":",
        linewidth=1,
        label=(
            f"Current "
            f"{fmt_price(current)}"
        )
    )

    title_state = (
        "CANDIDATE"
        if candidate
        else "SIGNAL"
    )

    ax.set_title(
        f"{symbol} | M30 NDS "
        f"{hook.direction} HOOK | "
        f"{title_state}"
    )

    ax.grid(alpha=0.2)

    ax.legend(
        loc="best",
        fontsize=8
    )

    ax.xaxis_date()

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M",
            tz=timezone.utc
        )
    )

    # ========================================================
    # M1 PANEL
    # ========================================================

    ax2 = axes[1]

    if m1 is not None and len(m1):

        # Keep the M30 activator visible.
        hook_window = m1[
            m1["time"]
            >= (
                hook.end.time
                - pd.Timedelta(
                    minutes=30
                )
            )
        ]

        if len(hook_window) >= 30:

            view1 = (
                hook_window
                .tail(240)
                .reset_index(drop=True)
            )

        else:

            view1 = (
                m1
                .tail(240)
                .reset_index(drop=True)
            )

        for _, row in view1.iterrows():

            x = mdates.date2num(
                row["time"].to_pydatetime()
            )

            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])

            ax2.vlines(
                x,
                l,
                h,
                color="black",
                linewidth=0.55
            )

            body_color = (
                "#159447"
                if c >= o
                else "#cf3030"
            )

            ax2.add_patch(
                Rectangle(
                    (
                        x - 0.00025,
                        min(o, c)
                    ),
                    0.0005,
                    max(
                        abs(c - o),
                        max(abs(c), 1)
                        * 0.00001
                    ),
                    facecolor=body_color,
                    edgecolor=body_color,
                    linewidth=0.4,
                )
            )

        # ----------------------------------------------------
        # M30 H3 / L3 ACTIVATOR ON M1
        # ----------------------------------------------------

        if hook.direction == "SHORT":

            activator_label = "H3 M30"

        else:

            activator_label = "L3 M30"

        activator_x = mdates.date2num(
            hook.end.time.to_pydatetime()
        )

        ax2.axvline(
            activator_x,
            linestyle="--",
            linewidth=1.5,
            label=(
                f"{activator_label} activator"
            )
        )

        # Vertical label at top of M1 panel.
        ax2.annotate(
            activator_label,
            (
                activator_x,
                0.98
            ),
            xycoords=(
                "data",
                "axes fraction"
            ),
            xytext=(5, 0),
            textcoords="offset points",
            fontsize=9,
            rotation=90,
            va="top",
            ha="left",
        )

        # ----------------------------------------------------
        # M1 1-2-3-F
        # ----------------------------------------------------

        if structure:

            points = structure.get(
                "points",
                []
            )

            confirmed = structure.get(
                "confirmed",
                False
            )

            for n, point in enumerate(
                points,
                start=1
            ):

                if (
                    confirmed
                    and n == len(points)
                ):

                    label = "F"

                else:

                    label = str(n)

                x = mdates.date2num(
                    point.time.to_pydatetime()
                )

                ax2.scatter(
                    x,
                    point.price,
                    s=50,
                    zorder=5
                )

                ax2.annotate(
                    f"{label}\n"
                    f"{fmt_price(point.price)}",
                    (
                        x,
                        point.price
                    ),
                    xytext=(3, 8),
                    textcoords="offset points",
                    fontsize=8,
                )

        ax2.axhline(
            current,
            linestyle=":",
            linewidth=1,
            label=(
                f"Current "
                f"{fmt_price(current)}"
            )
        )

        structure_status = (
            structure.get(
                "status",
                "WAITING"
            )
            if structure
            else "WAITING"
        )

        ax2.set_title(
            f"M1 | "
            f"{structure_status} | "
            f"{activator_label} activator"
        )

        ax2.xaxis_date()

        ax2.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M",
                tz=timezone.utc
            )
        )

    else:

        ax2.text(
            0.5,
            0.5,
            "M1 DATA UNAVAILABLE",
            ha="center",
            va="center",
            transform=ax2.transAxes
        )

    ax2.grid(alpha=0.2)

    ax2.legend(
        loc="best",
        fontsize=8
    )

    fig.tight_layout()

    filename = (
        f"{symbol}_"
        f"{hook.direction}_"
        f"{int(time.time())}.png"
    )

    path = (
        CHART_DIR
        / filename
    )

    fig.savefig(
        path,
        dpi=130
    )

    plt.close(fig)

    return path


# ============================================================
# CANDIDATE NOTICE
# ============================================================

def candidate_was_sent(
    fingerprint: str
) -> bool:

    with sqlite3.connect(
        DB_FILE
    ) as con:

        row = con.execute(
            """
            SELECT last_sent
            FROM candidate_notices
            WHERE fingerprint=?
            """,
            (fingerprint,)
        ).fetchone()

    if not row:
        return False

    try:

        sent_at = datetime.fromisoformat(
            row[0]
        )

        return (
            utc_now() - sent_at
        ).total_seconds() < (
            CANDIDATE_REPEAT_MIN * 60
        )

    except Exception:

        return False


def mark_candidate_sent(
    hook: Hook,
    symbol: str
):

    with sqlite3.connect(
        DB_FILE
    ) as con:

        con.execute(
            """
            INSERT INTO candidate_notices
            (
                fingerprint,
                last_sent,
                symbol,
                direction
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(fingerprint)
            DO UPDATE SET
                last_sent=excluded.last_sent,
                symbol=excluded.symbol,
                direction=excluded.direction
            """,
            (
                hook.fingerprint,
                utc_now().isoformat(),
                symbol,
                hook.direction,
            )
        )


# ============================================================
# CREATE PAPER SIGNAL
# ============================================================

def create_signal(
    symbol: str,
    hook: Hook,
    structure: Dict[str, Any],
    current: float,
    chart: Path
) -> bool:

    entry = float(
        structure["entry"]
    )

    sl = float(
        structure["sl"]
    )

    tp = float(
        hook.tp864
    )

    if hook.direction == "SHORT":

        valid = (
            sl > entry
            and tp < entry
        )

    else:

        valid = (
            sl < entry
            and tp > entry
        )

    if not valid:

        print(
            "Invalid entry geometry:",
            symbol,
            hook.direction,
            entry,
            sl,
            tp
        )

        return False

    with sqlite3.connect(
        DB_FILE
    ) as con:

        cutoff = datetime.fromtimestamp(
            utc_now().timestamp() - 3600,
            timezone.utc
        ).isoformat()

        recent = con.execute(
            """
            SELECT id
            FROM signals
            WHERE symbol=?
              AND direction=?
              AND hook_fingerprint=?
              AND created_at>?
            LIMIT 1
            """,
            (
                symbol,
                hook.direction,
                hook.fingerprint,
                cutoff,
            )
        ).fetchone()

        if recent:
            return False

        con.execute(
            """
            INSERT INTO signals
            (
                symbol,
                direction,
                status,
                entry,
                sl,
                tp,
                current_price,
                pnl_pct,
                hook_fingerprint,
                created_at,
                chart_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                hook.direction,
                "OPEN",
                entry,
                sl,
                tp,
                current,
                0,
                hook.fingerprint,
                utc_now().isoformat(),
                str(chart),
            )
        )

    emoji = (
        "🔴"
        if hook.direction == "SHORT"
        else "🟢"
    )

    message = (
        f"🚨 <b>NDS PAPER SIGNAL "
        f"v{VERSION}</b>\n"
        f"{emoji} <b>{symbol} "
        f"{hook.direction}</b>\n\n"
        f"Entry F: "
        f"<code>{fmt_price(entry)}</code>\n"
        f"SL: "
        f"<code>{fmt_price(sl)}</code>\n"
        f"TP 86.4%: "
        f"<code>{fmt_price(tp)}</code>\n"
        f"Current: "
        f"<code>{fmt_price(current)}</code>\n\n"
        f"M30 Hook: confirmed\n"
        f"M1: 1-2-3-F confirmed\n"
        f"<b>PAPER ONLY - no live orders</b>"
    )

    telegram_send(
        message,
        chart
    )

    return True


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    prices: Dict[str, float]
):

    with sqlite3.connect(
        DB_FILE
    ) as con:

        con.row_factory = sqlite3.Row

        rows = con.execute(
            """
            SELECT *
            FROM signals
            WHERE status='OPEN'
            """
        ).fetchall()

        for trade in rows:

            symbol = trade["symbol"]

            current = prices.get(
                symbol
            )

            if current is None:
                continue

            direction = trade[
                "direction"
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

            if direction == "SHORT":

                pnl = (
                    (entry - current)
                    / entry
                    * 100
                )

            else:

                pnl = (
                    (current - entry)
                    / entry
                    * 100
                )

            reason = None
            exit_price = None

            if direction == "SHORT":

                if current <= tp:

                    reason = "TP"
                    exit_price = tp

                elif current >= sl:

                    reason = "SL"
                    exit_price = sl

            else:

                if current >= tp:

                    reason = "TP"
                    exit_price = tp

                elif current <= sl:

                    reason = "SL"
                    exit_price = sl

            if reason:

                con.execute(
                    """
                    UPDATE signals
                    SET
                        status='CLOSED',
                        closed_at=?,
                        exit_price=?,
                        exit_reason=?,
                        current_price=?,
                        pnl_pct=?
                    WHERE id=?
                    """,
                    (
                        utc_now().isoformat(),
                        exit_price,
                        reason,
                        current,
                        pnl,
                        trade["id"],
                    )
                )

                emoji = (
                    "✅"
                    if reason == "TP"
                    else "❌"
                )

                telegram_send(
                    f"{emoji} "
                    f"<b>NDS PAPER TRADE "
                    f"CLOSED</b>\n"
                    f"{symbol} "
                    f"{direction}\n"
                    f"Reason: {reason}\n"
                    f"Entry: "
                    f"<code>{fmt_price(entry)}</code>\n"
                    f"Exit: "
                    f"<code>{fmt_price(exit_price)}</code>\n"
                    f"PnL: "
                    f"<b>{pnl:+.2f}%</b>"
                )

            else:

                con.execute(
                    """
                    UPDATE signals
                    SET
                        current_price=?,
                        pnl_pct=?
                    WHERE id=?
                    """,
                    (
                        current,
                        pnl,
                        trade["id"],
                    )
                )


# ============================================================
# TELEGRAM CANDIDATE MESSAGE
# ============================================================

def hook_message(
    symbol: str,
    hook: Hook,
    current: float,
    structure: Dict[str, Any],
    age_min: float
) -> str:

    if hook.direction == "SHORT":

        names = [
            ("START", hook.start),
            ("H1", hook.p1),
            ("L1", hook.p2),
            ("H2", hook.p3),
            ("L2", hook.p4),
            ("H3 / ACTIVATOR", hook.end),
        ]

        emoji = "🔴"

    else:

        names = [
            ("START", hook.start),
            ("L1", hook.p1),
            ("H1", hook.p2),
            ("L2", hook.p3),
            ("H2", hook.p4),
            ("L3 / ACTIVATOR", hook.end),
        ]

        emoji = "🟢"

    point_text = "\n".join(
        f"{name}: "
        f"<code>{fmt_price(point.price)}</code>"
        for name, point in names
    )

    status = structure.get(
        "status",
        "M1 pending"
    )

    m1_points = structure.get(
        "points",
        []
    )

    if m1_points:

        m1_lines = []

        for i, point in enumerate(
            m1_points,
            start=1
        ):

            if (
                structure.get("confirmed")
                and i == len(m1_points)
            ):

                label = "F"

            else:

                label = str(i)

            m1_lines.append(
                f"{label}: "
                f"<code>{fmt_price(point.price)}</code>"
            )

        m1_text = "\n".join(
            m1_lines
        )

    else:

        m1_text = (
            "No confirmed 1M swing "
            "points after activator yet"
        )

    activator = (
        "H3 M30"
        if hook.direction == "SHORT"
        else "L3 M30"
    )

    return (
        f"🔎 <b>NDS BEST CANDIDATE "
        f"v{VERSION}</b>\n"
        f"{emoji} <b>{symbol} "
        f"{hook.direction}</b>\n"
        f"Status: <b>{status}</b>\n"
        f"Hook age: "
        f"{age_min:.0f} min\n\n"

        f"<b>M30 HOOK</b>\n"
        f"{point_text}\n\n"

        f"<b>{activator} → M1 SEARCH</b>\n"
        f"86.4% TP: "
        f"<code>{fmt_price(hook.tp864)}</code>\n"
        f"Current: "
        f"<code>{fmt_price(current)}</code>\n\n"

        f"<b>M1 123F</b>\n"
        f"{m1_text}\n\n"

        f"<b>PAPER ONLY</b>"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_once():

    init_db()

    stats = {
        "scanned": 0,
        "data_ok": 0,
        "hooks": 0,
        "m1_requests": 0,
        "m1_ok": 0,
        "candidates": 0,
        "signals": 0,
        "errors": 0,
    }

    all_candidates = []

    confirmed_candidates = []

    prices: Dict[str, float] = {}

    # ========================================================
    # M30 SCAN
    # ========================================================

    for symbol in SYMBOLS:

        stats["scanned"] += 1

        try:

            m30 = fetch_candles(
                symbol,
                "30m",
                M30_COUNT
            )

            if m30 is None:
                continue

            stats["data_ok"] += 1

            current = float(
                m30.iloc[-1]["close"]
            )

            prices[symbol] = current

            pivots30 = find_pivots(
                m30,
                alternating=True
            )

            hooks = build_hooks(
                m30,
                pivots30
            )

            if not hooks:
                continue

            stats["hooks"] += len(
                hooks
            )

            # =================================================
            # Every recent Hook gets an M1 check.
            # =================================================

            for hook in hooks[:2]:

                stats["m1_requests"] += 1

                m1 = fetch_candles(
                    symbol,
                    "1m",
                    M1_COUNT
                )

                if m1 is None:
                    continue

                stats["m1_ok"] += 1

                current = float(
                    m1.iloc[-1]["close"]
                )

                prices[symbol] = current

                structure = m1_structure(
                    m1,
                    hook.direction,
                    hook.end.time
                )

                age = max(
                    0,
                    (
                        m30.iloc[-1]["time"]
                        - hook.end.time
                    ).total_seconds()
                    / 60
                )

                score = (
                    hook.score
                    +
                    (
                        25
                        if structure.get(
                            "confirmed"
                        )
                        else min(
                            12,
                            len(
                                structure.get(
                                    "points",
                                    []
                                )
                            ) * 3
                        )
                    )
                )

                candidate = (
                    score,
                    symbol,
                    m30,
                    m1,
                    hook,
                    structure,
                    current,
                    age,
                )

                all_candidates.append(
                    candidate
                )

                if structure.get(
                    "confirmed"
                ):

                    confirmed_candidates.append(
                        candidate
                    )

        except Exception as e:

            stats["errors"] += 1

            print(
                "Scan error",
                symbol,
                repr(e)
            )

            traceback.print_exc(
                limit=1
            )

    # ========================================================
    # MONITOR EXISTING PAPER TRADES
    # ========================================================

    monitor_open_trades(
        prices
    )

    # ========================================================
    # BEST CONFIRMED SIGNALS
    # ========================================================

    confirmed_candidates.sort(
        key=lambda x: (
            x[0],
            x[7]
        ),
        reverse=True
    )

    for item in confirmed_candidates:

        (
            score,
            symbol,
            m30,
            m1,
            hook,
            structure,
            current,
            age,
        ) = item

        chart = make_chart(
            symbol,
            m30,
            m1,
            hook,
            structure,
            current,
            candidate=False
        )

        if create_signal(
            symbol,
            hook,
            structure,
            current,
            chart
        ):

            stats["signals"] += 1

    # ========================================================
    # BEST CANDIDATE
    # ========================================================

    all_candidates.sort(
        key=lambda x: (
            x[0],
            x[7]
        ),
        reverse=True
    )

    if all_candidates:

        stats["candidates"] = len(
            all_candidates
        )

        best = all_candidates[0]

        (
            score,
            symbol,
            m30,
            m1,
            hook,
            structure,
            current,
            age,
        ) = best

        # Only report unchanged candidate after cooldown.
        if not candidate_was_sent(
            hook.fingerprint
        ):

            chart = make_chart(
                symbol,
                m30,
                m1,
                hook,
                structure,
                current,
                candidate=not structure.get(
                    "confirmed",
                    False
                )
            )

            telegram_send(
                hook_message(
                    symbol,
                    hook,
                    current,
                    structure,
                    age
                ),
                chart
            )

            mark_candidate_sent(
                hook,
                symbol
            )

    # ========================================================
    # SAVE RUN
    # ========================================================

    with sqlite3.connect(
        DB_FILE
    ) as con:

        con.execute(
            """
            INSERT INTO scanner_runs
            (
                run_time,
                scanned,
                data_ok,
                hooks,
                m1_requests,
                m1_ok,
                candidates,
                signals,
                errors,
                best_symbol,
                best_direction
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now().isoformat(),
                stats["scanned"],
                stats["data_ok"],
                stats["hooks"],
                stats["m1_requests"],
                stats["m1_ok"],
                stats["candidates"],
                stats["signals"],
                stats["errors"],
                (
                    all_candidates[0][1]
                    if all_candidates
                    else None
                ),
                (
                    all_candidates[0][4].direction
                    if all_candidates
                    else None
                ),
            )
        )

    # ========================================================
    # PERFORMANCE REPORT
    # ========================================================

    with sqlite3.connect(
        DB_FILE
    ) as con:

        open_rows = con.execute(
            """
            SELECT
                symbol,
                direction,
                entry,
                sl,
                tp,
                current_price,
                pnl_pct
            FROM signals
            WHERE status='OPEN'
            ORDER BY created_at DESC
            """
        ).fetchall()

        closed = con.execute(
            """
            SELECT
                COUNT(*),
                SUM(
                    CASE
                        WHEN pnl_pct > 0
                        THEN 1
                        ELSE 0
                    END
                ),
                SUM(
                    CASE
                        WHEN pnl_pct <= 0
                        THEN 1
                        ELSE 0
                    END
                ),
                COALESCE(
                    SUM(pnl_pct),
                    0
                )
            FROM signals
            WHERE status='CLOSED'
            """
        ).fetchone()

    report = (
        f"📊 <b>NDS DIAGNOSTIC "
        f"v{VERSION}</b>\n"
        f"Time: "
        f"{utc_now().strftime('%Y-%m-%d %H:%M UTC')}\n\n"

        f"M30 requests: "
        f"{stats['scanned']}\n"
        f"M30 Data OK: "
        f"{stats['data_ok']}\n"
        f"M30 Hooks: "
        f"{stats['hooks']}\n\n"

        f"M1 requests: "
        f"{stats['m1_requests']}\n"
        f"M1 Data OK: "
        f"{stats['m1_ok']}\n"
        f"Candidates: "
        f"{stats['candidates']}\n"
        f"New signals: "
        f"{stats['signals']}\n"
        f"Errors: "
        f"{stats['errors']}\n\n"

        f"Open trades: "
        f"{len(open_rows)}\n"
        f"Closed: "
        f"{closed[0] or 0}\n"
        f"Wins: "
        f"{closed[1] or 0}\n"
        f"Losses: "
        f"{closed[2] or 0}\n"
        f"Realized PnL: "
        f"{closed[3] or 0:+.2f}%\n\n"

        f"<b>PAPER ONLY</b>"
    )

    telegram_send(
        report
    )

    print(
        report
        .replace("<b>", "")
        .replace("</b>", "")
        .replace("<code>", "")
        .replace("</code>", "")
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        f"NDS M30->M1 Scanner "
        f"v{VERSION} | "
        f"PAPER_ONLY={PAPER_ONLY} | "
        f"symbols={len(SYMBOLS)}"
    )

    if not PAPER_ONLY:

        raise RuntimeError(
            "Safety lock: "
            "this scanner must remain "
            "PAPER ONLY."
        )

    run_once()


if __name__ == "__main__":
    main()
