# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.6
# ============================================================
# PAPER ONLY: this script NEVER submits exchange orders.
# M30 detects the NDS Hook; M1 searches for 1-2-3-F structure.
# Best Hook candidate + chart are reported even before F confirms.
# ============================================================
from __future__ import annotations

import os, time, math, sqlite3, traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

VERSION = '4.4.7'
PAPER_ONLY = True
DB_FILE = os.getenv('NDS_DB_FILE', 'nds_m30_m1_v44.db')
CHART_DIR = Path(os.getenv('NDS_CHART_DIR', 'nds_charts'))
CHART_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = 'https://futures.kraken.com/api/charts/v1/trade'
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT = os.getenv('TELEGRAM_CHAT_ID') or os.getenv('TELEGRAM_CHAT', '')
M30_COUNT = int(os.getenv('NDS_M30_COUNT', '250'))
M1_COUNT = int(os.getenv('NDS_M1_COUNT', '700'))
PIVOT_LEFT = int(os.getenv('NDS_PIVOT_LEFT', '2'))
PIVOT_RIGHT = int(os.getenv('NDS_PIVOT_RIGHT', '2'))
PRICE_TOL = float(os.getenv('NDS_PRICE_TOL', '0.002'))
MIN_SWING_PCT = float(os.getenv('NDS_MIN_SWING_PCT', '0.001'))
MAX_HOOK_AGE_MIN = int(os.getenv('NDS_MAX_HOOK_AGE_MIN', '240'))
MAX_SETUP_AGE_MIN = int(os.getenv('NDS_MAX_SETUP_AGE_MIN', '10'))
CANDIDATE_REPEAT_MIN = int(os.getenv('NDS_CANDIDATE_REPEAT_MIN', '180'))
REQUEST_TIMEOUT = 20
SYMBOLS = [s.strip() for s in os.getenv('NDS_SYMBOLS', ','.join([
    'PF_XBTUSD','PF_ETHUSD','PF_SOLUSD','PF_XRPUSD','PF_DOGEUSD','PF_ADAUSD','PF_LINKUSD','PF_AVAXUSD',
    'PF_DOTUSD','PF_LTCUSD','PF_BCHUSD','PF_TRXUSD','PF_UNIUSD','PF_ATOMUSD','PF_NEARUSD','PF_APTUSD',
    'PF_ARBUSD','PF_OPUSD','PF_INJUSD','PF_SUIUSD','PF_SEIUSD','PF_TIAUSD','PF_FILUSD','PF_ETCUSD',
    'PF_AAVEUSD','PF_ALGOUSD','PF_FTMUSD','PF_GALAUSD','PF_SANDUSD','PF_MANAUSD','PF_CRVUSD','PF_SNXXUSD',
    'PF_LDOUSD','PF_RUNEUSD','PF_EOSUSD','PF_XLMUSD','PF_PEPEUSD','PF_WIFUSD','PF_BONKUSD','PF_ENSUSD'
])).split(',') if s.strip()]
# Remove SNXX and ensure LDO is included, per scanner configuration.
SYMBOLS = [s for s in SYMBOLS if 'SNXX' not in s]
if 'PF_LDOUSD' not in SYMBOLS: SYMBOLS.append('PF_LDOUSD')

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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def fmt_price(x: Optional[float]) -> str:
    if x is None or not math.isfinite(float(x)): return 'N/A'
    x = float(x)
    if abs(x) >= 1000: return f'{x:.2f}'
    if abs(x) >= 1: return f'{x:.5f}'.rstrip('0').rstrip('.')
    return f'{x:.8f}'.rstrip('0').rstrip('.')

def telegram_send(text: str, photo: Optional[Path] = None) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print('Telegram credentials missing; message not sent.')
        return False
    try:
        if photo and photo.exists():
            with photo.open('rb') as f:
                r = requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto',
                    data={'chat_id': TELEGRAM_CHAT, 'caption': text[:1024], 'parse_mode':'HTML'},
                    files={'photo': f}, timeout=35)
        else:
            r = requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                data={'chat_id': TELEGRAM_CHAT, 'text': text[:4000], 'parse_mode':'HTML', 'disable_web_page_preview':True}, timeout=25)
        if not r.ok: print('Telegram error:', r.status_code, r.text[:300])
        return r.ok
    except Exception as e:
        print('Telegram exception:', repr(e)); return False

def init_db():
    with sqlite3.connect(DB_FILE) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, direction TEXT, status TEXT,
            entry REAL, sl REAL, tp REAL, current_price REAL, pnl_pct REAL DEFAULT 0,
            hook_fingerprint TEXT, created_at TEXT, closed_at TEXT, exit_price REAL,
            exit_reason TEXT, chart_path TEXT)''')
        con.execute('''CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_time TEXT, scanned INTEGER, data_ok INTEGER,
            hooks INTEGER, m1_requests INTEGER, m1_ok INTEGER, candidates INTEGER, signals INTEGER,
            errors INTEGER, best_symbol TEXT, best_direction TEXT)''')
        con.execute('''CREATE TABLE IF NOT EXISTS candidate_notices (
            fingerprint TEXT PRIMARY KEY, last_sent TEXT, symbol TEXT, direction TEXT)''')
        # Safe schema migration for databases created by older versions.
        cols = {r[1] for r in con.execute('PRAGMA table_info(signals)').fetchall()}
        for name, typ in [('hook_fingerprint','TEXT'),('chart_path','TEXT'),('current_price','REAL'),('pnl_pct','REAL DEFAULT 0')]:
            if name not in cols: con.execute(f'ALTER TABLE signals ADD COLUMN {name} {typ}')

def fetch_candles(symbol: str, resolution: str, count: int) -> Optional[pd.DataFrame]:
    url = f'{BASE_URL}/{symbol}/{resolution}'
    try:
        r = requests.get(url, params={'count': count}, timeout=REQUEST_TIMEOUT)
        if not r.ok: return None
        payload = r.json()
        rows = payload.get('candles') or payload.get('data') or payload.get('results') or []
        if isinstance(rows, dict): rows = rows.get('candles') or rows.get('data') or []
        if not rows: return None
        df = pd.DataFrame(rows)
        # Kraken normally uses time/open/high/low/close/volume; tolerate common aliases.
        aliases = {'t':'time','timestamp':'time','o':'open','h':'high','l':'low','c':'close','v':'volume'}
        df = df.rename(columns={k:v for k,v in aliases.items() if k in df.columns and v not in df.columns})
        required = ['time','open','high','low','close']
        if not all(c in df.columns for c in required): return None
        for c in ['open','high','low','close']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        # API time can be ISO string or Unix seconds/milliseconds.
        if pd.api.types.is_numeric_dtype(df['time']):
            raw = pd.to_numeric(df['time'], errors='coerce')
            unit = 'ms' if raw.dropna().median() > 1e12 else 's'
            df['time'] = pd.to_datetime(raw, unit=unit, utc=True, errors='coerce')
        else: df['time'] = pd.to_datetime(df['time'], utc=True, errors='coerce')
        df = df.dropna(subset=required).sort_values('time').drop_duplicates('time').reset_index(drop=True)
        if len(df) > count: df = df.iloc[-count:].reset_index(drop=True)
        if len(df) < 30: return None
        # Ignore the still-forming latest candle to avoid unstable pivots.
        return df.iloc[:-1].reset_index(drop=True)
    except Exception as e:
        print(f'Fetch error {symbol} {resolution}: {e}')
        return None

def find_pivots(df: pd.DataFrame, left: int = PIVOT_LEFT, right: int = PIVOT_RIGHT,
                alternating: bool = True) -> List[Pivot]:
    raw: List[Pivot] = []
    if df is None or len(df) < left + right + 5: return raw
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy()
    for i in range(left, len(df)-right):
        win_h = highs[i-left:i+right+1]; win_l = lows[i-left:i+right+1]
        is_h = highs[i] >= max(win_h) and (highs[i] > max(win_h[:left]) or highs[i] > max(win_h[left+1:]))
        is_l = lows[i] <= min(win_l) and (lows[i] < min(win_l[:left]) or lows[i] < min(win_l[left+1:]))
        if is_h: raw.append(Pivot(i, df.iloc[i]['time'], float(highs[i]), 'H'))
        if is_l: raw.append(Pivot(i, df.iloc[i]['time'], float(lows[i]), 'L'))
    raw.sort(key=lambda p: (p.index, 0 if p.kind == 'H' else 1))
    if not alternating: return raw
    out: List[Pivot] = []
    for p in raw:
        if not out:
            out.append(p); continue
        last = out[-1]
        if p.kind == last.kind:
            if (p.kind == 'H' and p.price >= last.price) or (p.kind == 'L' and p.price <= last.price): out[-1] = p
        else:
            if abs(p.price-last.price)/max(abs(last.price), 1e-12) >= MIN_SWING_PCT: out.append(p)
    return out

def pct(a: float, b: float) -> float:
    return abs(a-b)/max(abs(a), 1e-12)

def build_hooks(df: pd.DataFrame, pivots: List[Pivot]) -> List[Hook]:
    hooks: List[Hook] = []
    # SHORT: START low -> H1 high -> L1 low -> H2 high -> L2 low -> H3 high.
    # LONG: START high -> L1 low -> H1 high -> L2 low -> H2 high -> L3 low.
    for i in range(max(0, len(pivots)-5)):
        q = pivots[i:i+6]
        kinds = ''.join(p.kind for p in q)
        direction = None
        if kinds == 'LHLHLH':
            start,h1,l1,h2,l2,h3 = q
            if (h2.price > h1.price and l2.price < l1.price and h3.price > h2.price
                    and start.price < l1.price and start.price < l2.price):
                # START must be the lowest low in this SHORT hook.
                direction = 'SHORT'
                span = h3.price-start.price
                if span <= 0: continue
                tp = h3.price - 0.864*(h3.price-start.price)
                # A completed SHORT hook's last point is H3; ensure enough movement.
                if pct(start.price, h3.price) < MIN_SWING_PCT: continue
                age = max(0.0, (df.iloc[-1]['time']-h3.time).total_seconds()/60)
                score = max(0, 100-age/3) + min(30, pct(start.price,h3.price)*1000)
                fp = f'{direction}:{int(start.time.timestamp())}:{int(h3.time.timestamp())}:{h3.price:.8f}'
                hooks.append(Hook(direction,start,h1,l1,h2,l2,h3,tp,score,fp))
        elif kinds == 'HLHLHL':
            start,l1,h1,l2,h2,l3 = q
            if l2.price < l1.price and h2.price > h1.price and l3.price < l2.price:
                direction = 'LONG'
                span = start.price-l3.price
                if span <= 0: continue
                tp = l3.price + 0.864*(start.price-l3.price)
                if pct(start.price,l3.price) < MIN_SWING_PCT: continue
                age = max(0.0, (df.iloc[-1]['time']-l3.time).total_seconds()/60)
                score = max(0, 100-age/3) + min(30, pct(start.price,l3.price)*1000)
                fp = f'{direction}:{int(start.time.timestamp())}:{int(l3.time.timestamp())}:{l3.price:.8f}'
                hooks.append(Hook(direction,start,l1,h1,l2,h2,l3,tp,score,fp))
    # Keep only recent hooks; sort newest/best first.
    now = df.iloc[-1]['time']
    hooks = [h for h in hooks if (now-h.end.time).total_seconds()/60 <= MAX_HOOK_AGE_MIN]
    hooks.sort(key=lambda h: (h.score, h.end.time.value), reverse=True)
    # Deduplicate same fingerprint.
    unique = {}
    for h in hooks: unique[h.fingerprint] = h
    return list(unique.values())

def m1_structure(df: pd.DataFrame, direction: str, hook_time: pd.Timestamp, hook_price: float) -> Dict[str, Any]:
    # M30 H3/L3 is only the activator. It is NOT point 1 of the M1 123F.
    # SHORT must be: H3(M30) < 1 < 2 < 3 < F.
    # LONG  must be: L3(M30) > 1 > 2 > 3 > F.
    # Every new point is checked against ALL previous points, not only the
    # immediately preceding point. Intervening opposite pivots are ignored.
    piv = find_pivots(df, alternating=False)
    piv = [p for p in piv if p.time >= hook_time]
    highs = [p for p in piv if p.kind == 'H']
    lows = [p for p in piv if p.kind == 'L']
    result = {
        'points': [], 'confirmed': False,
        'status': 'WAITING FOR 1M STRUCTURE',
        'entry': None, 'sl': None
    }

    if direction == 'SHORT':
        # H3 < 1
        points: List[Pivot] = []
        for p in highs:
            if not points:
                if p.price > hook_price:
                    points.append(p)
                continue

            # 2 > 1 AND 2 > H3
            if len(points) == 1:
                if p.price > points[0].price and p.price > hook_price:
                    points.append(p)
                continue

            # 3 > 2 AND 3 > 1 AND 3 > H3
            if len(points) == 2:
                if (
                    p.price > points[1].price
                    and p.price > points[0].price
                    and p.price > hook_price
                ):
                    points.append(p)
                continue

            # F > 3 AND F > 2 AND F > 1 AND F > H3
            if len(points) == 3:
                if (
                    p.price > points[2].price
                    and p.price > points[1].price
                    and p.price > points[0].price
                    and p.price > hook_price
                ):
                    points.append(p)
                    break

        result['points'] = points
        if len(points) == 4:
            f = points[-1]
            if (df.iloc[-1]['time'] - f.time).total_seconds() / 60 <= MAX_SETUP_AGE_MIN:
                result['status'] = '123F CONFIRMED'
                result['confirmed'] = True
                result['entry'] = f.price
                prior_lows = [
                    p for p in lows
                    if p.index > points[0].index and p.index < f.index
                ]
                result['sl'] = max(
                    [p.price for p in prior_lows],
                    default=f.price * 1.01
                )
            else:
                result['status'] = '123F FOUND - F TOO OLD'
        elif points:
            result['status'] = f'BUILDING SHORT: {len(points)}/4'

    else:
        # L3 > 1
        points = []
        for p in lows:
            if not points:
                if p.price < hook_price:
                    points.append(p)
                continue

            # 2 < 1 AND 2 < L3
            if len(points) == 1:
                if p.price < points[0].price and p.price < hook_price:
                    points.append(p)
                continue

            # 3 < 2 AND 3 < 1 AND 3 < L3
            if len(points) == 2:
                if (
                    p.price < points[1].price
                    and p.price < points[0].price
                    and p.price < hook_price
                ):
                    points.append(p)
                continue

            # F < 3 AND F < 2 AND F < 1 AND F < L3
            if len(points) == 3:
                if (
                    p.price < points[2].price
                    and p.price < points[1].price
                    and p.price < points[0].price
                    and p.price < hook_price
                ):
                    points.append(p)
                    break

        result['points'] = points
        if len(points) == 4:
            f = points[-1]
            if (df.iloc[-1]['time'] - f.time).total_seconds() / 60 <= MAX_SETUP_AGE_MIN:
                result['status'] = '123F CONFIRMED'
                result['confirmed'] = True
                result['entry'] = f.price
                prior_highs = [
                    p for p in highs
                    if p.index > points[0].index and p.index < f.index
                ]
                result['sl'] = min(
                    [p.price for p in prior_highs],
                    default=f.price * 0.99
                )
            else:
                result['status'] = '123F FOUND - F TOO OLD'
        elif points:
            result['status'] = f'BUILDING LONG: {len(points)}/4'

    return result

def make_chart(symbol: str, m30: pd.DataFrame, m1: Optional[pd.DataFrame], hook: Hook,
               structure: Optional[Dict[str, Any]], current: float, candidate: bool = True) -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(15, 10), gridspec_kw={'height_ratios':[1.15,1]})
    ax = axes[0]
    view = m30.tail(100).reset_index(drop=True)
    for i,row in view.iterrows():
        x = mdates.date2num(row['time'].to_pydatetime()); o,h,l,c = row['open'],row['high'],row['low'],row['close']
        ax.vlines(x,l,h,color='black',linewidth=.7)
        color = '#159447' if c >= o else '#cf3030'
        ax.add_patch(Rectangle((x-.008, min(o,c)), .016, max(abs(c-o), max(abs(c),1)*.00001), facecolor=color, edgecolor=color, linewidth=.5))
    point_map = [('START',hook.start), ('H1/L1',hook.p1), ('L1/H1',hook.p2), ('H2/L2',hook.p3), ('L2/H2',hook.p4), ('H3/L3',hook.end)]
    # Labels follow actual sequence names by direction.
    if hook.direction == 'SHORT': labels = [('START',hook.start),('H1',hook.p1),('L1',hook.p2),('H2',hook.p3),('L2',hook.p4),('H3',hook.end)]
    else: labels = [('START',hook.start),('L1',hook.p1),('H1',hook.p2),('L2',hook.p3),('H2',hook.p4),('L3',hook.end)]
    for label,p in labels:
        ax.scatter(mdates.date2num(p.time.to_pydatetime()),p.price,s=45,zorder=5)
        ax.annotate(f'{label}\n{fmt_price(p.price)}',(mdates.date2num(p.time.to_pydatetime()),p.price),xytext=(3,8),textcoords='offset points',fontsize=8)
    ax.axhline(hook.tp864,linestyle='--',linewidth=1.3,label=f'86.4% TP {fmt_price(hook.tp864)}')
    ax.axhline(current,linestyle=':',linewidth=1,label=f'Current {fmt_price(current)}')
    ax.set_title(f'{symbol} | M30 NDS {hook.direction} HOOK | CANDIDATE' if candidate else f'{symbol} | M30 NDS {hook.direction} SIGNAL')
    ax.grid(alpha=.2); ax.legend(loc='best',fontsize=8); ax.xaxis_date(); ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M', tz=timezone.utc))
    ax2 = axes[1]
    if m1 is not None and len(m1):
        # Keep the M30 activator visible on the M1 panel.
        hook_window = m1[m1['time'] >= hook.end.time - pd.Timedelta(minutes=30)]
        if len(hook_window) >= 30:
            view1 = hook_window.tail(240).reset_index(drop=True)
        else:
            view1 = m1.tail(240).reset_index(drop=True)
        for i,row in view1.iterrows():
            x = mdates.date2num(row['time'].to_pydatetime()); o,h,l,c = row['open'],row['high'],row['low'],row['close']
            ax2.vlines(x,l,h,color='black',linewidth=.55)
            color = '#159447' if c >= o else '#cf3030'
            ax2.add_patch(Rectangle((x-.00025,min(o,c)),.0005,max(abs(c-o),max(abs(c),1)*.00001),facecolor=color,edgecolor=color,linewidth=.4))
        if structure:
            # Mark the M30 activator on the M1 chart. For a SHORT hook this is H3;
            # for a LONG hook it is L3. It is NOT an M1 pivot.
            activator_label = 'H3 M30' if hook.direction == 'SHORT' else 'L3 M30'
            activator_x = mdates.date2num(hook.end.time.to_pydatetime())
            ax2.axvline(activator_x, linestyle='--', linewidth=1.4, label=f'{activator_label} activator')
            ax2.annotate(activator_label, (activator_x, 0.98), xycoords=('data','axes fraction'),
                         xytext=(5,0), textcoords='offset points', fontsize=9, rotation=90,
                         va='top', ha='left')
            for n,p in enumerate(structure.get('points',[]), start=1):
                label = 'F' if structure.get('confirmed') and n == len(structure['points']) else str(n)
                ax2.scatter(mdates.date2num(p.time.to_pydatetime()),p.price,s=45,zorder=5)
                ax2.annotate(f'{label}\n{fmt_price(p.price)}',(mdates.date2num(p.time.to_pydatetime()),p.price),xytext=(2,7),textcoords='offset points',fontsize=8)
        ax2.axhline(current,linestyle=':',linewidth=1,label=f'Current {fmt_price(current)}')
        ax2.set_title(f'M1 | {structure.get("status","WAITING") if structure else "WAITING"} | {"H3 M30" if hook.direction == "SHORT" else "L3 M30"} activator')
        ax2.xaxis_date(); ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=timezone.utc))
    else: ax2.text(.5,.5,'M1 DATA UNAVAILABLE',ha='center',va='center',transform=ax2.transAxes)
    ax2.grid(alpha=.2); ax2.legend(loc='best',fontsize=8)
    fig.tight_layout()
    path = CHART_DIR / f'{symbol}_{hook.direction}_{int(time.time())}.png'
    fig.savefig(path,dpi=130); plt.close(fig)
    return path

def candidate_was_sent(fp: str) -> bool:
    with sqlite3.connect(DB_FILE) as con:
        row = con.execute('SELECT last_sent FROM candidate_notices WHERE fingerprint=?',(fp,)).fetchone()
    if not row: return False
    try: return (utc_now()-datetime.fromisoformat(row[0])).total_seconds() < CANDIDATE_REPEAT_MIN*60
    except Exception: return False

def mark_candidate_sent(hook: Hook, symbol: str):
    with sqlite3.connect(DB_FILE) as con:
        con.execute('INSERT INTO candidate_notices(fingerprint,last_sent,symbol,direction) VALUES(?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET last_sent=excluded.last_sent,symbol=excluded.symbol,direction=excluded.direction',
                    (hook.fingerprint,utc_now().isoformat(),symbol,hook.direction))

def create_signal(symbol: str, hook: Hook, structure: Dict[str,Any], current: float, chart: Path):
    entry = float(structure['entry'])
    sl = float(structure['sl'])
    # TP is the M30 86.4% cycle level. If it lies on the wrong side of entry, retain it as the cycle target but don't open a paper trade.
    tp = float(hook.tp864)
    valid = (sl > entry and tp < entry) if hook.direction == 'SHORT' else (sl < entry and tp > entry)
    if not valid:
        print(f'Invalid entry geometry {symbol} {hook.direction}: entry={entry}, sl={sl}, tp={tp}')
        return False
    with sqlite3.connect(DB_FILE) as con:
        recent = con.execute('SELECT id FROM signals WHERE symbol=? AND direction=? AND hook_fingerprint=? AND created_at>? LIMIT 1',
            (symbol,hook.direction,hook.fingerprint,datetime.fromtimestamp(utc_now().timestamp()-3600, timezone.utc).isoformat())).fetchone()
        if recent: return False
        con.execute('INSERT INTO signals(symbol,direction,status,entry,sl,tp,current_price,pnl_pct,hook_fingerprint,created_at,chart_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (symbol,hook.direction,'OPEN',entry,sl,tp,current,0,hook.fingerprint,utc_now().isoformat(),str(chart)))
    emoji = '🔴' if hook.direction == 'SHORT' else '🟢'
    msg = (f'🚨 <b>NDS PAPER SIGNAL v{VERSION}</b>\n{emoji} <b>{symbol} {hook.direction}</b>\n'
           f'Entry (F): <code>{fmt_price(entry)}</code>\nSL: <code>{fmt_price(sl)}</code>\n'
           f'TP 86.4%: <code>{fmt_price(tp)}</code>\nCurrent: <code>{fmt_price(current)}</code>\n'
           f'M30 Hook: confirmed | M1: 1-2-3-F confirmed\n<b>PAPER ONLY - no live orders</b>')
    telegram_send(msg, chart)
    return True

def monitor_open_trades(prices: Dict[str,float]):
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM signals WHERE status='OPEN'").fetchall()
        for tr in rows:
            symbol=tr['symbol']; direction=tr['direction']; current=prices.get(symbol)
            if current is None: continue
            entry=float(tr['entry']); sl=float(tr['sl']); tp=float(tr['tp'])
            pnl=((entry-current)/entry*100) if direction=='SHORT' else ((current-entry)/entry*100)
            reason=None; exit_price=None
            if direction=='SHORT':
                if current <= tp: reason='TP'; exit_price=tp
                elif current >= sl: reason='SL'; exit_price=sl
            else:
                if current >= tp: reason='TP'; exit_price=tp
                elif current <= sl: reason='SL'; exit_price=sl
            if reason:
                con.execute("UPDATE signals SET status='CLOSED',closed_at=?,exit_price=?,exit_reason=?,current_price=?,pnl_pct=? WHERE id=?",
                    (utc_now().isoformat(),exit_price,reason,current,pnl,tr['id']))
                emoji='✅' if reason=='TP' else '❌'
                telegram_send(f'{emoji} <b>NDS PAPER TRADE CLOSED</b>\n{symbol} {direction}\nReason: {reason}\nEntry: <code>{fmt_price(entry)}</code>\nExit: <code>{fmt_price(exit_price)}</code>\nPnL: <b>{pnl:+.2f}%</b>')
            else:
                con.execute('UPDATE signals SET current_price=?,pnl_pct=? WHERE id=?',(current,pnl,tr['id']))

def hook_message(symbol: str, hook: Hook, current: float, structure: Dict[str,Any], age_min: float) -> str:
    if hook.direction == 'SHORT':
        names = [('START',hook.start),('H1',hook.p1),('L1',hook.p2),('H2',hook.p3),('L2',hook.p4),('H3 / ACTIVATOR',hook.end)]
        emoji='🔴'
    else:
        names = [('START',hook.start),('L1',hook.p1),('H1',hook.p2),('L2',hook.p3),('H2',hook.p4),('L3 / ACTIVATOR',hook.end)]
        emoji='🟢'
    points='\n'.join(f'{name}: <code>{fmt_price(p.price)}</code>' for name,p in names)
    s = structure.get('status','M1 pending')
    m1_points = structure.get('points',[])
    if m1_points:
        m1_text='\n'.join(f'{"F" if structure.get("confirmed") and i==len(m1_points)-1 else str(i+1)}: <code>{fmt_price(p.price)}</code>' for i,p in enumerate(m1_points))
    else: m1_text='No confirmed 1M swing points after Hook yet'
    return (f'🔎 <b>NDS BEST CANDIDATE v{VERSION}</b>\n{emoji} <b>{symbol} {hook.direction}</b>\n'
            f'Status: <b>{s}</b>\nHook age: {age_min:.0f} min\n\n<b>M30 HOOK POINTS</b>\n{points}\n'
            f'86.4% TP: <code>{fmt_price(hook.tp864)}</code>\nCurrent: <code>{fmt_price(current)}</code>\n\n'
            f'<b>M1 STRUCTURE</b>\n{m1_text}\n\n<b>PAPER ONLY</b>')

def run_once():
    init_db()
    stats={'scanned':0,'data_ok':0,'hooks':0,'m1_requests':0,'m1_ok':0,'candidates':0,'signals':0,'errors':0}
    all_candidates=[]; prices={}; best_for_signal=[]
    for symbol in SYMBOLS:
        stats['scanned'] += 1
        try:
            m30=fetch_candles(symbol,'30m',M30_COUNT)
            if m30 is None: continue
            stats['data_ok'] += 1
            current=float(m30.iloc[-1]['close']); prices[symbol]=current
            piv30=find_pivots(m30, alternating=True)
            hooks=build_hooks(m30,piv30)
            if not hooks: continue
            stats['hooks'] += len(hooks)
            # M1 is requested for each recent Hook, not just one global winner.
            for hook in hooks[:2]:
                stats['m1_requests'] += 1
                m1=fetch_candles(symbol,'1m',M1_COUNT)
                if m1 is None: continue
                stats['m1_ok'] += 1
                current=float(m1.iloc[-1]['close']); prices[symbol]=current
                structure=m1_structure(m1,hook.direction,hook.end.time,hook.end.price)
                age=max(0,(m30.iloc[-1]['time']-hook.end.time).total_seconds()/60)
                score=hook.score + (25 if structure.get('confirmed') else min(12,len(structure.get('points',[]))*3))
                all_candidates.append((score,symbol,m30,m1,hook,structure,current,age))
                if structure.get('confirmed'): best_for_signal.append((score,symbol,m30,m1,hook,structure,current,age))
        except Exception as e:
            stats['errors'] += 1; print('Scan error',symbol,repr(e)); traceback.print_exc(limit=1)
    monitor_open_trades(prices)
    all_candidates.sort(key=lambda x:(x[0],x[7]),reverse=True)
    # Create signals for confirmed setups; candidate report is independent of signal confirmation.
    for item in sorted(best_for_signal,key=lambda x:x[0],reverse=True):
        score,symbol,m30,m1,hook,structure,current,age=item
        chart=make_chart(symbol,m30,m1,hook,structure,current,candidate=False)
        if create_signal(symbol,hook,structure,current,chart): stats['signals'] += 1
    if all_candidates:
        best=all_candidates[0]
        score,symbol,m30,m1,hook,structure,current,age=best
        stats['candidates']=len(all_candidates)
        if not candidate_was_sent(hook.fingerprint):
            chart=make_chart(symbol,m30,m1,hook,structure,current,candidate=not structure.get('confirmed',False))
            telegram_send(hook_message(symbol,hook,current,structure,age),chart)
            mark_candidate_sent(hook,symbol)
    with sqlite3.connect(DB_FILE) as con:
        con.execute('INSERT INTO scanner_runs(run_time,scanned,data_ok,hooks,m1_requests,m1_ok,candidates,signals,errors,best_symbol,best_direction) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (utc_now().isoformat(),stats['scanned'],stats['data_ok'],stats['hooks'],stats['m1_requests'],stats['m1_ok'],stats['candidates'],stats['signals'],stats['errors'],all_candidates[0][1] if all_candidates else None,all_candidates[0][4].direction if all_candidates else None))
    with sqlite3.connect(DB_FILE) as con:
        open_rows=con.execute("SELECT symbol,direction,entry,sl,tp,current_price,pnl_pct FROM signals WHERE status='OPEN' ORDER BY created_at DESC").fetchall()
        closed=con.execute("SELECT COUNT(*),SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END),SUM(CASE WHEN pnl_pct<=0 THEN 1 ELSE 0 END),COALESCE(SUM(pnl_pct),0) FROM signals WHERE status='CLOSED'").fetchone()
    report=(f'📊 <b>NDS DIAGNOSTIC v{VERSION}</b>\nTime: {utc_now().strftime("%Y-%m-%d %H:%M UTC")}\n\n'
            f'M30 requests: {stats["scanned"]} | Data OK: {stats["data_ok"]}\nHooks: {stats["hooks"]}\n'
            f'M1 requests: {stats["m1_requests"]} | Data OK: {stats["m1_ok"]}\nCandidates: {stats["candidates"]}\n'
            f'New signals: {stats["signals"]} | Errors: {stats["errors"]}\n\n'
            f'Open trades: {len(open_rows)}\nClosed: {closed[0] or 0} | Wins: {closed[1] or 0} | Losses: {closed[2] or 0} | Realized PnL: {closed[3] or 0:+.2f}%\n'
            f'<b>PAPER ONLY</b>')
    telegram_send(report)
    print(report.replace('<b>','').replace('</b>','').replace('<code>','').replace('</code>',''))

def main():
    print(f'NDS M30->M1 Scanner v{VERSION} | PAPER_ONLY={PAPER_ONLY} | symbols={len(SYMBOLS)}')
    if not PAPER_ONLY: raise RuntimeError('Safety lock: this scanner must remain PAPER ONLY.')
    run_once()

if __name__ == '__main__':
    main()
