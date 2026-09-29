
# ============================================================
# NDS M30 LIVE SCANNER
# VERSION 4.4.8
# ============================================================
# PAPER ONLY: this script NEVER submits exchange orders.
# M30 detects confirmed NDS Hooks.
# M1 timeframe and 1-2-3-F logic have been removed.
# SHORT: START is the lowest point; H3 confirms the hook.
# LONG:  START is the highest point; L3 confirms the hook.
# TP: 86.4% retracement level using the existing calculation.
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
from typing import Optional, List, Dict

import requests
import pandas as pd
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


VERSION = '4.4.8'
PAPER_ONLY = True

DB_FILE = os.getenv('NDS_DB_FILE', 'nds_m30_m1_v44.db')
CHART_DIR = Path(os.getenv('NDS_CHART_DIR', 'nds_charts'))
CHART_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = 'https://futures.kraken.com/api/charts/v1/trade'

TELEGRAM_TOKEN = (
    os.getenv('TELEGRAM_BOT_TOKEN')
    or os.getenv('TELEGRAM_TOKEN', '')
)
TELEGRAM_CHAT = (
    os.getenv('TELEGRAM_CHAT_ID')
    or os.getenv('TELEGRAM_CHAT', '')
)

M30_COUNT = int(os.getenv('NDS_M30_COUNT', '250'))

PIVOT_LEFT = int(os.getenv('NDS_PIVOT_LEFT', '2'))
PIVOT_RIGHT = int(os.getenv('NDS_PIVOT_RIGHT', '2'))

PRICE_TOL = float(os.getenv('NDS_PRICE_TOL', '0.002'))
MIN_SWING_PCT = float(os.getenv('NDS_MIN_SWING_PCT', '0.001'))

MAX_HOOK_AGE_MIN = int(
    os.getenv('NDS_MAX_HOOK_AGE_MIN', '240')
)

CANDIDATE_REPEAT_MIN = int(
    os.getenv('NDS_CANDIDATE_REPEAT_MIN', '180')
)

REQUEST_TIMEOUT = 20

SYMBOLS = [
    s.strip()
    for s in os.getenv(
        'NDS_SYMBOLS',
        ','.join([
            'PF_XBTUSD', 'PF_ETHUSD', 'PF_SOLUSD',
            'PF_XRPUSD', 'PF_DOGEUSD', 'PF_ADAUSD',
            'PF_LINKUSD', 'PF_AVAXUSD', 'PF_DOTUSD',
            'PF_LTCUSD', 'PF_BCHUSD', 'PF_TRXUSD',
            'PF_UNIUSD', 'PF_ATOMUSD', 'PF_NEARUSD',
            'PF_APTUSD', 'PF_ARBUSD', 'PF_OPUSD',
            'PF_INJUSD', 'PF_SUIUSD', 'PF_SEIUSD',
            'PF_TIAUSD', 'PF_FILUSD', 'PF_ETCUSD',
            'PF_AAVEUSD', 'PF_ALGOUSD', 'PF_FTMUSD',
            'PF_GALAUSD', 'PF_SANDUSD', 'PF_MANAUSD',
            'PF_CRVUSD', 'PF_SNXXUSD', 'PF_LDOUSD',
            'PF_RUNEUSD', 'PF_EOSUSD', 'PF_XLMUSD',
            'PF_PEPEUSD', 'PF_WIFUSD', 'PF_BONKUSD',
            'PF_ENSUSD'
        ])
    ).split(',')
    if s.strip()
]

# Remove SNXX and ensure LDO is included.
SYMBOLS = [s for s in SYMBOLS if 'SNXX' not in s]

if 'PF_LDOUSD' not in SYMBOLS:
    SYMBOLS.append('PF_LDOUSD')


# ============================================================
# DATA STRUCTURES
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
# GENERAL HELPERS
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_price(x: Optional[float]) -> str:
    if x is None:
        return 'N/A'

    if not math.isfinite(float(x)):
        return 'N/A'

    x = float(x)

    if abs(x) >= 1000:
        return f'{x:.2f}'

    if abs(x) >= 1:
        return f'{x:.5f}'.rstrip('0').rstrip('.')

    return f'{x:.8f}'.rstrip('0').rstrip('.')


def telegram_send(
    text: str,
    photo: Optional[Path] = None
) -> bool:

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print('Telegram credentials missing; message not sent.')
        return False

    try:
        if photo and photo.exists():
            with photo.open('rb') as f:
                r = requests.post(
                    f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto',
                    data={
                        'chat_id': TELEGRAM_CHAT,
                        'caption': text[:1024],
                        'parse_mode': 'HTML'
                    },
                    files={'photo': f},
                    timeout=35
                )
        else:
            r = requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                data={
                    'chat_id': TELEGRAM_CHAT,
                    'text': text[:4000],
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True
                },
                timeout=25
            )

        if not r.ok:
            print('Telegram error:', r.status_code, r.text[:300])

        return r.ok

    except Exception as e:
        print('Telegram exception:', repr(e))
        return False


# ============================================================
# DATABASE
# ============================================================

def init_db():

    with sqlite3.connect(DB_FILE) as con:

        con.execute('''
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
        ''')

        # Keep the existing database schema for compatibility.
        con.execute('''
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
        ''')

        con.execute('''
            CREATE TABLE IF NOT EXISTS candidate_notices (
                fingerprint TEXT PRIMARY KEY,
                last_sent TEXT,
                symbol TEXT,
                direction TEXT
            )
        ''')

        cols = {
            r[1]
            for r in con.execute(
                'PRAGMA table_info(signals)'
            ).fetchall()
        }

        migrations = [
            ('hook_fingerprint', 'TEXT'),
            ('chart_path', 'TEXT'),
            ('current_price', 'REAL'),
            ('pnl_pct', 'REAL DEFAULT 0')
        ]

        for name, typ in migrations:
            if name not in cols:
                con.execute(
                    f'ALTER TABLE signals ADD COLUMN {name} {typ}'
                )


# ============================================================
# KRAKEN CANDLE DATA
# ============================================================

def fetch_candles(
    symbol: str,
    resolution: str,
    count: int
) -> Optional[pd.DataFrame]:

    url = f'{BASE_URL}/{symbol}/{resolution}'

    try:
        r = requests.get(
            url,
            params={'count': count},
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:
            print(
                f'HTTP error {symbol} {resolution}: {r.status_code}'
            )
            return None

        payload = r.json()

        rows = (
            payload.get('candles')
            or payload.get('data')
            or payload.get('results')
            or []
        )

        if isinstance(rows, dict):
            rows = (
                rows.get('candles')
                or rows.get('data')
                or []
            )

        if not rows:
            return None

        df = pd.DataFrame(rows)

        aliases = {
            't': 'time',
            'timestamp': 'time',
            'o': 'open',
            'h': 'high',
            'l': 'low',
            'c': 'close',
            'v': 'volume'
        }

        df = df.rename(
            columns={
                k: v
                for k, v in aliases.items()
                if k in df.columns and v not in df.columns
            }
        )

        required = [
            'time', 'open', 'high', 'low', 'close'
        ]

        if not all(c in df.columns for c in required):
            return None

        for c in ['open', 'high', 'low', 'close']:
            df[c] = pd.to_numeric(
                df[c],
                errors='coerce'
            )

        if pd.api.types.is_numeric_dtype(df['time']):

            raw = pd.to_numeric(
                df['time'],
                errors='coerce'
            )

            median_time = raw.dropna().median()

            unit = 'ms' if median_time > 1e12 else 's'

            df['time'] = pd.to_datetime(
                raw,
                unit=unit,
                utc=True,
                errors='coerce'
            )

        else:
            df['time'] = pd.to_datetime(
                df['time'],
                utc=True,
                errors='coerce'
            )

        df = (
            df.dropna(subset=required)
            .sort_values('time')
            .drop_duplicates('time')
            .reset_index(drop=True)
        )

        if len(df) > count:
            df = df.iloc[-count:].reset_index(drop=True)

        if len(df) < 30:
            return None

        # Ignore the currently forming candle.
        return df.iloc[:-1].reset_index(drop=True)

    except Exception as e:
        print(f'Fetch error {symbol} {resolution}: {e}')
        return None


# ============================================================
# PIVOT DETECTION
# ============================================================

def find_pivots(
    df: pd.DataFrame,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
    alternating: bool = True
) -> List[Pivot]:

    raw: List[Pivot] = []

    if df is None or len(df) < left + right + 5:
        return raw

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()

    for i in range(left, len(df) - right):

        win_h = highs[i-left:i+right+1]
        win_l = lows[i-left:i+right+1]

        is_h = (
            highs[i] >= max(win_h)
            and (
                highs[i] > max(win_h[:left])
                or highs[i] > max(win_h[left+1:])
            )
        )

        is_l = (
            lows[i] <= min(win_l)
            and (
                lows[i] < min(win_l[:left])
                or lows[i] < min(win_l[left+1:])
            )
        )

        if is_h:
            raw.append(
                Pivot(
                    i,
                    df.iloc[i]['time'],
                    float(highs[i]),
                    'H'
                )
            )

        if is_l:
            raw.append(
                Pivot(
                    i,
                    df.iloc[i]['time'],
                    float(lows[i]),
                    'L'
                )
            )

    raw.sort(
        key=lambda p: (
            p.index,
            0 if p.kind == 'H' else 1
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
                p.kind == 'H' and p.price >= last.price
            ) or (
                p.kind == 'L' and p.price <= last.price
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


def pct(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), 1e-12)


# ============================================================
# NDS HOOK DETECTION - M30 ONLY
# ============================================================

def build_hooks(
    df: pd.DataFrame,
    pivots: List[Pivot]
) -> List[Hook]:

    hooks: List[Hook] = []

    # SHORT / positive hook:
    # START LOW -> H1 -> L1 -> H2 -> L2 -> H3
    #
    # Conditions:
    # H2 > H1
    # L2 < L1
    # H3 > H2
    # START is the lowest point of the hook.
    #
    # LONG / negative hook:
    # START HIGH -> L1 -> H1 -> L2 -> H2 -> L3
    #
    # Conditions:
    # L2 < L1
    # H2 > H1
    # L3 < L2
    # START is the highest point of the hook.

    for i in range(max(0, len(pivots) - 5)):

        q = pivots[i:i+6]

        if len(q) != 6:
            continue

        kinds = ''.join(p.kind for p in q)

        # ----------------------------------------------------
        # POSITIVE HOOK -> SHORT
        # ----------------------------------------------------

        if kinds == 'LHLHLH':

            start, h1, l1, h2, l2, h3 = q

            # START must be the lowest price in the structure.
            if not (
                start.price < h1.price
                and start.price < l1.price
                and start.price < h2.price
                and start.price < l2.price
                and start.price < h3.price
            ):
                continue

            if not (
                h2.price > h1.price
                and l2.price < l1.price
                and h3.price > h2.price
            ):
                continue

            span = h3.price - start.price

            if span <= 0:
                continue

            if pct(start.price, h3.price) < MIN_SWING_PCT:
                continue

            # Preserve the existing 86.4% TP calculation.
            tp = h3.price - 0.864 * (
                h3.price - start.price
            )

            age = max(
                0.0,
                (
                    df.iloc[-1]['time'] - h3.time
                ).total_seconds() / 60
            )

            score = (
                max(0, 100 - age / 3)
                + min(
                    30,
                    pct(start.price, h3.price) * 1000
                )
            )

            fp = (
                f'SHORT:{int(start.time.timestamp())}:'
                f'{int(h3.time.timestamp())}:'
                f'{h3.price:.8f}'
            )

            hooks.append(
                Hook(
                    'SHORT',
                    start,
                    h1,
                    l1,
                    h2,
                    l2,
                    h3,
                    tp,
                    score,
                    fp
                )
            )

        # ----------------------------------------------------
        # NEGATIVE HOOK -> LONG
        # ----------------------------------------------------

        elif kinds == 'HLHLHL':

            start, l1, h1, l2, h2, l3 = q

            # START must be the highest price in the structure.
            if not (
                start.price > l1.price
                and start.price > h1.price
                and start.price > l2.price
                and start.price > h2.price
                and start.price > l3.price
            ):
                continue

            if not (
                l2.price < l1.price
                and h2.price > h1.price
                and l3.price < l2.price
            ):
                continue

            span = start.price - l3.price

            if span <= 0:
                continue

            if pct(start.price, l3.price) < MIN_SWING_PCT:
                continue

            # Preserve the existing 86.4% TP calculation.
            tp = l3.price + 0.864 * (
                start.price - l3.price
            )

            age = max(
                0.0,
                (
                    df.iloc[-1]['time'] - l3.time
                ).total_seconds() / 60
            )

            score = (
                max(0, 100 - age / 3)
                + min(
                    30,
                    pct(start.price, l3.price) * 1000
                )
            )

            fp = (
                f'LONG:{int(start.time.timestamp())}:'
                f'{int(l3.time.timestamp())}:'
                f'{l3.price:.8f}'
            )

            hooks.append(
                Hook(
                    'LONG',
                    start,
                    l1,
                    h1,
                    l2,
                    h2,
                    l3,
                    tp,
                    score,
                    fp
                )
            )

    # Only keep recent, completed hooks.
    now = df.iloc[-1]['time']

    hooks = [
        h for h in hooks
        if (
            now - h.end.time
        ).total_seconds() / 60 <= MAX_HOOK_AGE_MIN
    ]

    hooks.sort(
        key=lambda h: (h.score, h.end.time.value),
        reverse=True
    )

    unique = {}

    for h in hooks:
        unique[h.fingerprint] = h

    return list(unique.values())


# ============================================================
# M30 STOP LOSS CALCULATION
# ============================================================

def calculate_stop_loss(
    hook: Hook,
    pivots: List[Pivot]
) -> float:

    # SHORT:
    # Look for a previous valid M30 high above the entry.
    #
    # LONG:
    # Look for a previous valid M30 low below the entry.
    #
    # If no qualifying pivot exists, retain the previous
    # scanner's 1% fallback behavior.

    if hook.direction == 'SHORT':

        candidates = [
            p.price
            for p in pivots
            if (
                p.kind == 'H'
                and p.index < hook.end.index
                and p.price > hook.end.price
            )
        ]

        if candidates:
            return min(candidates)

        return hook.end.price * 1.01

    candidates = [
        p.price
        for p in pivots
        if (
            p.kind == 'L'
            and p.index < hook.end.index
            and p.price < hook.end.price
        )
    ]

    if candidates:
        return max(candidates)

    return hook.end.price * 0.99


# ============================================================
# M30 CHART
# ============================================================

def make_chart(
    symbol: str,
    m30: pd.DataFrame,
    pivots: List[Pivot],
    hook: Hook,
    current: float,
    candidate: bool = True
) -> Path:

    fig, ax = plt.subplots(
        1,
        1,
        figsize=(15, 8)
    )

    view = m30.tail(100).reset_index(drop=True)

    for _, row in view.iterrows():

        x = mdates.date2num(
            row['time'].to_pydatetime()
        )

        o = float(row['open'])
        h = float(row['high'])
        l = float(row['low'])
        c = float(row['close'])

        ax.vlines(
            x, l, h,
            color='black',
            linewidth=0.7
        )

        color = '#159447' if c >= o else '#cf3030'

        candle_height = max(
            abs(c - o),
            max(abs(c), 1) * 0.00001
        )

        ax.add_patch(
            Rectangle(
                (x - 0.008, min(o, c)),
                0.016,
                candle_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.5
            )
        )

    if hook.direction == 'SHORT':

        labels = [
            ('START (0%)', hook.start),
            ('H1', hook.p1),
            ('L1', hook.p2),
            ('H2', hook.p3),
            ('L2', hook.p4),
            ('H3 / ENTRY', hook.end)
        ]

        entry = hook.end.price
        sl = calculate_stop_loss(hook, pivots)

    else:

        labels = [
            ('START (0%)', hook.start),
            ('L1', hook.p1),
            ('H1', hook.p2),
            ('L2', hook.p3),
            ('H2', hook.p4),
            ('L3 / ENTRY', hook.end)
        ]

        entry = hook.end.price
        sl = calculate_stop_loss(hook, pivots)

    # Keep labels readable by alternating their vertical offsets.
    for i, (label, p) in enumerate(labels):

        x = mdates.date2num(
            p.time.to_pydatetime()
        )

        ax.scatter(
            x,
            p.price,
            s=48,
            zorder=5
        )

        offset_y = 12 if i % 2 == 0 else -25

        ax.annotate(
            f'{label}\n{fmt_price(p.price)}',
            (x, p.price),
            xytext=(4, offset_y),
            textcoords='offset points',
            fontsize=8,
            zorder=6
        )

    ax.axhline(
        hook.tp864,
        linestyle='--',
        linewidth=1.4,
        color='#2471A3',
        label=f'86.4% TP {fmt_price(hook.tp864)}'
    )

    ax.axhline(
        entry,
        linestyle='-.',
        linewidth=1.0,
        color='#8E44AD',
        label=f'Entry {fmt_price(entry)}'
    )

    ax.axhline(
        sl,
        linestyle='--',
        linewidth=1.0,
        color='#C0392B',
        label=f'SL {fmt_price(sl)}'
    )

    ax.axhline(
        current,
        linestyle=':',
        linewidth=1.0,
        color='#117864',
        label=f'Current {fmt_price(current)}'
    )

    status = 'CANDIDATE' if candidate else 'PAPER SIGNAL'

    ax.set_title(
        f'{symbol} | M30 NDS {hook.direction} HOOK | {status}'
    )

    ax.set_ylabel('Price')
    ax.grid(alpha=0.2)
    ax.legend(loc='best', fontsize=8)

    ax.xaxis_date()

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            '%m-%d %H:%M',
            tz=timezone.utc
        )
    )

    fig.tight_layout()

    path = CHART_DIR / (
        f'{symbol}_{hook.direction}_{int(time.time())}.png'
    )

    fig.savefig(path, dpi=130)
    plt.close(fig)

    return path


# ============================================================
# CANDIDATE NOTIFICATION TRACKING
# ============================================================

def candidate_was_sent(fp: str) -> bool:

    with sqlite3.connect(DB_FILE) as con:

        row = con.execute(
            '''
            SELECT last_sent
            FROM candidate_notices
            WHERE fingerprint=?
            ''',
            (fp,)
        ).fetchone()

    if not row:
        return False

    try:
        last_sent = datetime.fromisoformat(row[0])

        return (
            utc_now() - last_sent
        ).total_seconds() < CANDIDATE_REPEAT_MIN * 60

    except Exception:
        return False


def mark_candidate_sent(
    hook: Hook,
    symbol: str
):

    with sqlite3.connect(DB_FILE) as con:

        con.execute(
            '''
            INSERT INTO candidate_notices
                (fingerprint, last_sent, symbol, direction)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fingerprint)
            DO UPDATE SET
                last_sent=excluded.last_sent,
                symbol=excluded.symbol,
                direction=excluded.direction
            ''',
            (
                hook.fingerprint,
                utc_now().isoformat(),
                symbol,
                hook.direction
            )
        )


# ============================================================
# PAPER SIGNAL CREATION
# ============================================================

def create_signal(
    symbol: str,
    hook: Hook,
    pivots: List[Pivot],
    current: float,
    chart: Path
) -> bool:

    # Entry is at the confirmed M30 hook endpoint.
    # No M1 timeframe and no 1-2-3-F confirmation.

    entry = float(hook.end.price)

    sl = float(
        calculate_stop_loss(hook, pivots)
    )

    tp = float(hook.tp864)

    if hook.direction == 'SHORT':

        valid = sl > entry and tp < entry

    else:

        valid = sl < entry and tp > entry

    if not valid:

        print(
            f'Invalid entry geometry {symbol} '
            f'{hook.direction}: entry={entry}, '
            f'sl={sl}, tp={tp}'
        )

        return False

    with sqlite3.connect(DB_FILE) as con:

        cutoff = datetime.fromtimestamp(
            utc_now().timestamp() - 3600,
            timezone.utc
        ).isoformat()

        recent = con.execute(
            '''
            SELECT id
            FROM signals
            WHERE symbol=?
              AND direction=?
              AND hook_fingerprint=?
              AND created_at>?
            LIMIT 1
            ''',
            (
                symbol,
                hook.direction,
                hook.fingerprint,
                cutoff
            )
        ).fetchone()

        if recent:
            return False

        con.execute(
            '''
            INSERT INTO signals (
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
            ''',
            (
                symbol,
                hook.direction,
                'OPEN',
                entry,
                sl,
                tp,
                current,
                0,
                hook.fingerprint,
                utc_now().isoformat(),
                str(chart)
            )
        )

    emoji = (
        '🔴' if hook.direction == 'SHORT'
        else '🟢'
    )

    msg = (
        f'🚨 <b>NDS PAPER SIGNAL v{VERSION}</b>\n'
        f'{emoji} <b>{symbol} {hook.direction}</b>\n'
        f'Entry (M30 Hook): <code>{fmt_price(entry)}</code>\n'
        f'SL: <code>{fmt_price(sl)}</code>\n'
        f'TP 86.4%: <code>{fmt_price(tp)}</code>\n'
        f'Current: <code>{fmt_price(current)}</code>\n'
        f'M30 Hook: confirmed\n'
        f'<b>PAPER ONLY - no live orders</b>'
    )

    telegram_send(msg, chart)

    return True


# ============================================================
# OPEN PAPER TRADE MONITOR
# ============================================================

def monitor_open_trades(
    prices: Dict[str, float]
):

    with sqlite3.connect(DB_FILE) as con:

        con.row_factory = sqlite3.Row

        rows = con.execute(
            "SELECT * FROM signals WHERE status='OPEN'"
        ).fetchall()

        for tr in rows:

            symbol = tr['symbol']
            direction = tr['direction']

            current = prices.get(symbol)

            if current is None:
                continue

            entry = float(tr['entry'])
            sl = float(tr['sl'])
            tp = float(tr['tp'])

            if direction == 'SHORT':

                pnl = (
                    (entry - current) / entry * 100
                )

            else:

                pnl = (
                    (current - entry) / entry * 100
                )

            reason = None
            exit_price = None

            if direction == 'SHORT':

                if current <= tp:
                    reason = 'TP'
                    exit_price = tp

                elif current >= sl:
                    reason = 'SL'
                    exit_price = sl

            else:

                if current >= tp:
                    reason = 'TP'
                    exit_price = tp

                elif current <= sl:
                    reason = 'SL'
                    exit_price = sl

            if reason:

                con.execute(
                    '''
                    UPDATE signals
                    SET status='CLOSED',
                        closed_at=?,
                        exit_price=?,
                        exit_reason=?,
                        current_price=?,
                        pnl_pct=?
                    WHERE id=?
                    ''',
                    (
                        utc_now().isoformat(),
                        exit_price,
                        reason,
                        current,
                        pnl,
                        tr['id']
                    )
                )

                emoji = '✅' if reason == 'TP' else '❌'

                telegram_send(
                    f'{emoji} <b>NDS PAPER TRADE CLOSED</b>\n'
                    f'{symbol} {direction}\n'
                    f'Reason: {reason}\n'
                    f'Entry: <code>{fmt_price(entry)}</code>\n'
                    f'Exit: <code>{fmt_price(exit_price)}</code>\n'
                    f'PnL: <b>{pnl:+.2f}%</b>'
                )

            else:

                con.execute(
                    '''
                    UPDATE signals
                    SET current_price=?, pnl_pct=?
                    WHERE id=?
                    ''',
                    (
                        current,
                        pnl,
                        tr['id']
                    )
                )


# ============================================================
# M30 HOOK TELEGRAM MESSAGE
# ============================================================

def hook_message(
    symbol: str,
    hook: Hook,
    current: float,
    age_min: float,
    sl: float
) -> str:

    if hook.direction == 'SHORT':

        names = [
            ('START (0%)', hook.start),
            ('H1', hook.p1),
            ('L1', hook.p2),
            ('H2', hook.p3),
            ('L2', hook.p4),
            ('H3 / ENTRY', hook.end)
        ]

        emoji = '🔴'

    else:

        names = [
            ('START (0%)', hook.start),
            ('L1', hook.p1),
            ('H1', hook.p2),
            ('L2', hook.p3),
            ('H2', hook.p4),
            ('L3 / ENTRY', hook.end)
        ]

        emoji = '🟢'

    points = '\n'.join(
        f'{name}: <code>{fmt_price(p.price)}</code>'
        for name, p in names
    )

    return (
        f'🔎 <b>NDS M30 HOOK v{VERSION}</b>\n'
        f'{emoji} <b>{symbol} {hook.direction}</b>\n'
        f'Status: <b>CONFIRMED M30 HOOK</b>\n'
        f'Hook age: {age_min:.0f} min\n\n'
        f'<b>M30 HOOK POINTS</b>\n'
        f'{points}\n\n'
        f'Entry: <code>{fmt_price(hook.end.price)}</code>\n'
        f'SL: <code>{fmt_price(sl)}</code>\n'
        f'86.4% TP: <code>{fmt_price(hook.tp864)}</code>\n'
        f'Current: <code>{fmt_price(current)}</code>\n\n'
        f'<b>M30 ONLY - NO M1 / NO 123F</b>\n'
        f'<b>PAPER ONLY</b>'
    )


# ============================================================
# MAIN SCANNER
# ============================================================

def run_once():

    init_db()

    stats = {
        'scanned': 0,
        'data_ok': 0,
        'hooks': 0,
        'candidates': 0,
        'signals': 0,
        'errors': 0
    }

    all_candidates = []
    prices: Dict[str, float] = {}

    for symbol in SYMBOLS:

        stats['scanned'] += 1

        try:

            m30 = fetch_candles(
                symbol,
                '30m',
                M30_COUNT
            )

            if m30 is None:
                continue

            stats['data_ok'] += 1

            current = float(
                m30.iloc[-1]['close']
            )

            prices[symbol] = current

            pivots = find_pivots(
                m30,
                alternating=True
            )

            hooks = build_hooks(
                m30,
                pivots
            )

            if not hooks:
                continue

            stats['hooks'] += len(hooks)

            # Process recent confirmed M30 hooks only.
            for hook in hooks[:2]:

                age = max(
                    0.0,
                    (
                        m30.iloc[-1]['time'] - hook.end.time
                    ).total_seconds() / 60
                )

                score = hook.score

                all_candidates.append(
                    (
                        score,
                        symbol,
                        m30,
                        pivots,
                        hook,
                        current,
                        age
                    )
                )

        except Exception as e:

            stats['errors'] += 1

            print(
                'Scan error',
                symbol,
                repr(e)
            )

            traceback.print_exc(limit=1)

    # Update existing paper trades.
    monitor_open_trades(prices)

    all_candidates.sort(
        key=lambda x: (x[0], x[6]),
        reverse=True
    )

    # Generate paper signals from confirmed M30 hooks.
    # No M1 data or 123F condition is used.
    for item in all_candidates:

        (
            score,
            symbol,
            m30,
            pivots,
            hook,
            current,
            age
        ) = item

        try:

            chart = make_chart(
                symbol,
                m30,
                pivots,
                hook,
                current,
                candidate=False
            )

            if create_signal(
                symbol,
                hook,
                pivots,
                current,
                chart
            ):
                stats['signals'] += 1

        except Exception as e:

            stats['errors'] += 1

            print(
                'Signal error',
                symbol,
                repr(e)
            )

    # Report the best candidate independently of signal creation.
    if all_candidates:

        best = all_candidates[0]

        (
            score,
            symbol,
            m30,
            pivots,
            hook,
            current,
            age
        ) = best

        stats['candidates'] = len(all_candidates)

        sl = calculate_stop_loss(
            hook,
            pivots
        )

        if not candidate_was_sent(hook.fingerprint):

            chart = make_chart(
                symbol,
                m30,
                pivots,
                hook,
                current,
                candidate=True
            )

            telegram_send(
                hook_message(
                    symbol,
                    hook,
                    current,
                    age,
                    sl
                ),
                chart
            )

            mark_candidate_sent(
                hook,
                symbol
            )

    # Keep old database columns for compatibility.
    with sqlite3.connect(DB_FILE) as con:

        con.execute(
            '''
            INSERT INTO scanner_runs (
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
            ''',
            (
                utc_now().isoformat(),
                stats['scanned'],
                stats['data_ok'],
                stats['hooks'],
                0,
                0,
                stats['candidates'],
                stats['signals'],
                stats['errors'],
                all_candidates[0][1] if all_candidates else None,
                all_candidates[0][4].direction
                if all_candidates else None
            )
        )

    with sqlite3.connect(DB_FILE) as con:

        open_rows = con.execute(
            '''
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
            '''
        ).fetchall()

        closed = con.execute(
            '''
            SELECT
                COUNT(*),
                SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl_pct<=0 THEN 1 ELSE 0 END),
                COALESCE(SUM(pnl_pct), 0)
            FROM signals
            WHERE status='CLOSED'
            '''
        ).fetchone()

    report = (
        f'📊 <b>NDS DIAGNOSTIC v{VERSION}</b>\n'
        f'Time: {utc_now().strftime("%Y-%m-%d %H:%M UTC")}\n\n'
        f'<b>M30</b>\n'
        f'Requests: {stats["scanned"]}\n'
        f'Data OK: {stats["data_ok"]}\n'
        f'Confirmed Hooks: {stats["hooks"]}\n'
        f'Candidates: {stats["candidates"]}\n'
        f'New Signals: {stats["signals"]}\n'
        f'Errors: {stats["errors"]}\n\n'
        f'<b>OPEN PAPER TRADES</b>: {len(open_rows)}\n'
        f'<b>CLOSED</b>: {closed[0] or 0}\n'
        f'Wins: {closed[1] or 0}\n'
        f'Losses: {closed[2] or 0}\n'
        f'Realized PnL: {closed[3] or 0:+.2f}%\n\n'
        f'<b>M30 ONLY - NO M1 / NO 123F</b>\n'
        f'<b>PAPER ONLY</b>'
    )

    telegram_send(report)

    print(
        report
        .replace('<b>', '')
        .replace('</b>', '')
        .replace('<code>', '')
        .replace('</code>', '')
    )


# ============================================================
# ENTRY POINT
# ============================================================

def main():

    print(
        f'NDS M30 Scanner v{VERSION} | '
        f'PAPER_ONLY={PAPER_ONLY} | '
        f'symbols={len(SYMBOLS)}'
    )

    if not PAPER_ONLY:
        raise RuntimeError(
            'Safety lock: this scanner must remain PAPER ONLY.'
        )

    run_once()


if __name__ == '__main__':
    main()
