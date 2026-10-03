# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.7.0
# PAPER TRADING ONLY - NO REAL ORDERS
# H4 confirmed H3/L3 activates same-direction M5 hook search
# Entry at confirmed M5 H3/L3; TP = M5 hook 86.4%; SL = nearest valid H4 HA pivot
# ============================================================

import os, time, sqlite3, traceback
from datetime import datetime, timezone
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

VERSION = "5.7.0"
REAL_TRADING = False
KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"
TARGET_ASSETS = 100
H4_INTERVAL, M5_INTERVAL = "4h", "5m"
H4_INTERVAL_MINUTES, M5_INTERVAL_MINUTES = 240, 5
H4_CANDLES, M5_CANDLES = 320, 1500
PIVOT_LEFT, PIVOT_RIGHT = 2, 2
NDS_RETRACE = 0.864
M5_MIN_HOOK_RANGE_PCT = 0.20
H4_SL_BUFFER_PCT = 0.15
H4_MAX_HOOK_AGE_SECONDS = 24 * 3600
M5_MAX_HOOK_AGE_SECONDS = 6 * 3600
MAX_OPEN_TRADES = 3
REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CANDLES = 240
DB_FILE = "nds_h4_m5_v545.db"
CHART_DIR = "nds_h4_m5_charts"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT") or ""

DIAG = {k: 0 for k in [
    "assets_scanned", "h4_requests", "h4_data_ok", "h4_data_error", "h4_empty", "h4_short",
    "h4_pivots", "h4_hooks", "h4_confirmed", "h4_recent", "h4_valid_hooks",
    "h4_short_direction", "h4_long_direction", "h4_filtered", "m5_requests", "m5_data_ok",
    "m5_data_error", "m5_empty", "m5_short", "m5_pivots", "m5_hooks", "m5_confirmed",
    "m5_recent", "m5_range_valid", "m5_after_h4_activation", "m5_tp_touched", "m5_sl_found",
    "m5_geometry_valid", "m5_duplicate", "m5_max_open", "signal_ready", "hook_charts_sent",
    "hook_chart_errors", "signals", "duplicate_signals", "max_open"]}
DIAG["api_errors"] = []
H4_DIRECTION, H4_DATA = {}, {}
START_TIME = time.time()


def utc_now(): return datetime.now(timezone.utc)
def utc_now_ts(): return int(utc_now().timestamp())
def fmt_ts(ts):
    try: return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception: return "-"
def fmt_price(value):
    try:
        value = float(value)
        if value >= 1000: return f"{value:.2f}"
        if value >= 1: return f"{value:.5f}"
        if value >= 0.01: return f"{value:.7f}"
        return f"{value:.10f}"
    except Exception: return "-"
def telegram_enabled(): return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def telegram_send(text):
    if not telegram_enabled(): return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=REQUEST_TIMEOUT)
        if not r.ok: DIAG["api_errors"].append(f"Telegram text HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram text: {str(e)[:160]}"); return False

def telegram_send_photo(photo_path, caption):
    if not telegram_enabled() or not photo_path or not os.path.exists(photo_path): return False
    try:
        with open(photo_path, "rb") as photo:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                files={"photo": photo}, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"}, timeout=REQUEST_TIMEOUT)
        if not r.ok: DIAG["api_errors"].append(f"Telegram photo HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram photo: {str(e)[:160]}"); return False

def get_futures_instruments():
    try:
        r = requests.get(f"{KRAKEN_FUTURES_URL}/instruments", timeout=REQUEST_TIMEOUT); r.raise_for_status()
        symbols = []
        for item in r.json().get("instruments", []):
            s = str(item.get("symbol") or item.get("instrument") or "")
            if s.startswith("PF_") and "USD" in s and item.get("tradeable", True) is not False: symbols.append(s)
        return list(dict.fromkeys(symbols))[:TARGET_ASSETS]
    except Exception as e:
        DIAG["api_errors"].append(f"Instruments: {str(e)[:180]}"); return []

def get_candles(symbol, interval, count):
    try:
        r = requests.get(f"{KRAKEN_CHART_URL}/trade/{symbol}/{interval}", params={"count": int(count)}, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            DIAG["api_errors"].append(f"{symbol} {interval} HTTP {r.status_code}"); return None
        rows = []
        for c in r.json().get("candles", []):
            if isinstance(c, dict):
                vals = [c.get("time", c.get("timestamp", c.get("t"))), c.get("open", c.get("o")), c.get("high", c.get("h")), c.get("low", c.get("l")), c.get("close", c.get("c")), c.get("volume", c.get("v", 0))]
            elif isinstance(c, (list, tuple)) and len(c) >= 5: vals = list(c[:5]) + [c[5] if len(c) > 5 else 0]
            else: continue
            rows.append(vals)
        if not rows: return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        for col in df.columns: df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["time", "open", "high", "low", "close"])
        if df.empty: return df
        if df.time.max() > 10_000_000_000: df["time"] /= 1000.0
        df["time"] = df["time"].astype(float)
        return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    except Exception as e:
        DIAG["api_errors"].append(f"{symbol} {interval}: {str(e)[:180]}"); return None

def calculate_heikin_ashi(df):
    ha = df[["time", "open", "high", "low", "close"]].copy().reset_index(drop=True)
    ha["ha_close"] = (ha.open + ha.high + ha.low + ha.close) / 4.0
    opens = []
    for i in range(len(ha)):
        opens.append((float(ha.iloc[i].open) + float(ha.iloc[i].close)) / 2 if i == 0 else (opens[i-1] + float(ha.iloc[i-1].ha_close)) / 2)
    ha["ha_open"] = opens
    ha["ha_high"] = ha[["high", "ha_open", "ha_close"]].max(axis=1)
    ha["ha_low"] = ha[["low", "ha_open", "ha_close"]].min(axis=1)
    return ha

def heikin_ashi_ohlc(df):
    ha = calculate_heikin_ashi(df); out = df.copy().reset_index(drop=True)
    out["open"], out["high"], out["low"], out["close"] = ha.ha_open, ha.ha_high, ha.ha_low, ha.ha_close
    return out

def find_pivots(df):
    highs, lows = [], []
    if df is None or df.empty or len(df) < PIVOT_LEFT + PIVOT_RIGHT + 1: return highs, lows
    for i in range(PIVOT_LEFT, len(df)-PIVOT_RIGHT):
        hi, lo = float(df.iloc[i].high), float(df.iloc[i].low)
        lh = [float(df.iloc[j].high) for j in range(i-PIVOT_LEFT, i)]
        rh = [float(df.iloc[j].high) for j in range(i+1, i+PIVOT_RIGHT+1)]
        ll = [float(df.iloc[j].low) for j in range(i-PIVOT_LEFT, i)]
        rl = [float(df.iloc[j].low) for j in range(i+1, i+PIVOT_RIGHT+1)]
        if all(hi > x for x in lh) and all(hi >= x for x in rh): highs.append({"type":"H","index":i,"time":float(df.iloc[i].time),"price":hi})
        if all(lo < x for x in ll) and all(lo <= x for x in rl): lows.append({"type":"L","index":i,"time":float(df.iloc[i].time),"price":lo})
    return highs, lows

def build_ordered_pivots(highs, lows):
    result = []
    for p in sorted(highs+lows, key=lambda x:(x["time"],x["index"])):
        if not result or p["type"] != result[-1]["type"]: result.append(p)
        elif (p["type"] == "H" and p["price"] >= result[-1]["price"]) or (p["type"] == "L" and p["price"] <= result[-1]["price"]): result[-1] = p
    return result

def detect_hooks(df, interval_minutes):
    if df is None or df.empty: return []
    highs, lows = find_pivots(df); ordered = build_ordered_pivots(highs, lows); hooks=[]
    confirm_secs = interval_minutes * 60 * PIVOT_RIGHT
    for i in range(max(0, len(ordered)-5)):
        p=ordered[i:i+6]
        if len(p)<6: continue
        types=[x["type"] for x in p]
        # Positive hook / SHORT: lowest START, H1, L1, H2, L2, H3
        if types == ["L","H","L","H","L","H"]:
            start,h1,l1,h2,l2,h3=p
            if not (h2["price"]>h1["price"] and l2["price"]<l1["price"] and h3["price"]>h2["price"]): continue
            if not (start["price"]<l1["price"] and start["price"]<l2["price"]): continue
            rng=h3["price"]-start["price"]
            if rng<=0 or start["price"]<=0: continue
            hooks.append({"direction":"SHORT","start":start,"h1":h1,"l1":l1,"h2":h2,"l2":l2,"h3":h3,"final":h3,"entry":h3["price"],"tp":h3["price"]-NDS_RETRACE*rng,"range_pct":rng/start["price"]*100,"confirmation_time":h3["time"]+confirm_secs,"created_time":h3["time"]})
        # Negative hook / LONG: highest START, L1, H1, L2, H2, L3
        elif types == ["H","L","H","L","H","L"]:
            start,l1,h1,l2,h2,l3=p
            if not (l2["price"]<l1["price"] and h2["price"]>h1["price"] and l3["price"]<l2["price"]): continue
            if not (start["price"]>h1["price"] and start["price"]>h2["price"]): continue
            rng=start["price"]-l3["price"]
            if rng<=0 or start["price"]<=0: continue
            hooks.append({"direction":"LONG","start":start,"l1":l1,"h1":h1,"l2":l2,"h2":h2,"l3":l3,"final":l3,"entry":l3["price"],"tp":l3["price"]+NDS_RETRACE*rng,"range_pct":rng/start["price"]*100,"confirmation_time":l3["time"]+confirm_secs,"created_time":l3["time"]})
    return hooks

def hook_id(symbol, hook): return f"{symbol}|{hook['direction']}|{int(hook['final']['time'])}|{hook['final']['price']:.12f}"
def hook_is_confirmed(hook, now_ts=None): return hook["confirmation_time"] <= (utc_now_ts() if now_ts is None else now_ts)
def hook_is_recent(hook, now_ts=None, max_age_seconds=None):
    now_ts=utc_now_ts() if now_ts is None else now_ts; max_age_seconds=M5_MAX_HOOK_AGE_SECONDS if max_age_seconds is None else max_age_seconds
    age=now_ts-hook["confirmation_time"]; return 0 <= age <= max_age_seconds

def db_connect():
    conn=sqlite3.connect(DB_FILE); conn.row_factory=sqlite3.Row; return conn

def init_db():
    os.makedirs(CHART_DIR,exist_ok=True); conn=db_connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hooks(id INTEGER PRIMARY KEY AUTOINCREMENT,hook_key TEXT UNIQUE,symbol TEXT,direction TEXT,start_price REAL,h1_price REAL,l1_price REAL,h2_price REAL,l2_price REAL,final_price REAL,entry REAL,tp REAL,sl REAL,range_pct REAL,confirmation_time INTEGER,created_time INTEGER,detected_time INTEGER,chart_path TEXT);
    CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,hook_key TEXT UNIQUE,symbol TEXT,direction TEXT,entry REAL,sl REAL,tp REAL,opened_at INTEGER,closed_at INTEGER,close_price REAL,status TEXT,pnl_pct REAL,pnl_price REAL,chart_path TEXT);
    CREATE TABLE IF NOT EXISTS hook_chart_notifications(hook_key TEXT PRIMARY KEY,sent_at INTEGER,chart_path TEXT);
    """); conn.commit(); conn.close()
def hook_exists(key):
    c=db_connect(); r=c.execute("SELECT 1 FROM hooks WHERE hook_key=?",(key,)).fetchone(); c.close(); return r is not None
def trade_exists(key):
    c=db_connect(); r=c.execute("SELECT 1 FROM trades WHERE hook_key=?",(key,)).fetchone(); c.close(); return r is not None
def open_trade_count():
    c=db_connect(); r=c.execute("SELECT COUNT(*) c FROM trades WHERE status='OPEN'").fetchone(); c.close(); return int(r["c"])
def save_hook(symbol,hook,sl,chart_path=None):
    key=hook_id(symbol,hook); c=db_connect()
    c.execute("INSERT OR IGNORE INTO hooks(hook_key,symbol,direction,start_price,h1_price,l1_price,h2_price,l2_price,final_price,entry,tp,sl,range_pct,confirmation_time,created_time,detected_time,chart_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (key,symbol,hook["direction"],hook["start"]["price"],hook.get("h1",{}).get("price"),hook.get("l1",{}).get("price"),hook.get("h2",{}).get("price"),hook.get("l2",{}).get("price"),hook["final"]["price"],hook["entry"],hook["tp"],sl,hook["range_pct"],int(hook["confirmation_time"]),int(hook["created_time"]),utc_now_ts(),chart_path)); c.commit(); c.close(); return key
def save_trade(key,symbol,hook,sl,chart_path=None):
    c=db_connect(); c.execute("INSERT OR IGNORE INTO trades(hook_key,symbol,direction,entry,sl,tp,opened_at,status,chart_path) VALUES(?,?,?,?,?,?,?,?,?)",(key,symbol,hook["direction"],hook["entry"],sl,hook["tp"],utc_now_ts(),"OPEN",chart_path)); c.commit(); c.close()

def calculate_h4_sl(h4_df, hook):
    """Nearest confirmed H4 Heikin Ashi pivot on protective side, available by M5 entry confirmation."""
    if h4_df is None or h4_df.empty or hook is None: return None
    try:
        ha=heikin_ashi_ohlc(h4_df); highs,lows=find_pivots(ha); entry=float(hook["entry"]); signal_time=float(hook["confirmation_time"])
        # A pivot is confirmed only after PIVOT_RIGHT H4 candles have closed.
        cutoff=signal_time - PIVOT_RIGHT*H4_INTERVAL_MINUTES*60
        if hook["direction"]=="LONG":
            cand=[p for p in lows if p["price"]<entry and p["time"]<=cutoff]
            if not cand: return None
            p=min(cand,key=lambda x:entry-x["price"]); return float(p["price"])*(1-H4_SL_BUFFER_PCT/100)
        cand=[p for p in highs if p["price"]>entry and p["time"]<=cutoff]
        if not cand: return None
        p=min(cand,key=lambda x:x["price"]-entry); return float(p["price"])*(1+H4_SL_BUFFER_PCT/100)
    except Exception as e:
        DIAG["api_errors"].append(f"H4 SL: {str(e)[:180]}"); return None

def tp_already_touched(df,hook):
    # Only inspect candles after the M5 final pivot. The pivot candle itself forms the entry level.
    future=df[df.time>hook["final"]["time"]]
    if future.empty: return False
    return bool((future.low<=hook["tp"]).any()) if hook["direction"]=="SHORT" else bool((future.high>=hook["tp"]).any())

def create_hook_chart(symbol,df,hook,sl,path_prefix="signal_m5",timeframe="M5",h4_activation=None):
    try:
        chart_df=df.tail(CHART_CANDLES).copy().reset_index(drop=True)
        if chart_df.empty: return None
        ha=calculate_heikin_ashi(chart_df); fig,ax=plt.subplots(figsize=(15,8))
        x=mdates.date2num(pd.to_datetime(ha.time,unit="s",utc=True).dt.to_pydatetime())
        minutes=H4_INTERVAL_MINUTES if timeframe=="H4" else M5_INTERVAL_MINUTES; width=max(minutes/1440*.72,.0008)
        for i,row in ha.iterrows():
            xo=x[i]; o,c,hi,lo=map(float,[row.ha_open,row.ha_close,row.ha_high,row.ha_low]); color="#26a69a" if c>=o else "#ef5350"
            ax.vlines(xo,lo,hi,color="black",linewidth=.8,zorder=2); ax.add_patch(plt.Rectangle((xo-width/2,min(o,c)),width,max(abs(c-o),max(abs(c),1)*1e-7),facecolor=color,edgecolor="black",linewidth=.5,zorder=3))
        if hook["direction"]=="SHORT": points=[hook[k] for k in ("start","h1","l1","h2","l2","h3")]; labels=["START","H1","L1","H2","L2","H3"]
        else: points=[hook[k] for k in ("start","l1","h1","l2","h2","l3")]; labels=["START","L1","H1","L2","H2","L3"]
        px=[datetime.fromtimestamp(p["time"],tz=timezone.utc) for p in points]; py=[p["price"] for p in points]
        ax.plot(px,py,marker="o",linewidth=2,color="royalblue",label="Confirmed HA Hook",zorder=5)
        offsets=[(0,-28),(0,18),(0,-30),(0,18),(0,-30),(0,22)] if hook["direction"]=="SHORT" else [(0,24),(0,-30),(0,18),(0,-30),(0,18),(0,-32)]
        for p,label,off in zip(points,labels,offsets):
            dt=datetime.fromtimestamp(p["time"],tz=timezone.utc); emph=label in ("START","H3","L3")
            ax.annotate(f"{label}\n{fmt_price(p['price'])}",(dt,p["price"]),xytext=off,textcoords="offset points",ha="center",fontsize=9 if emph else 8,fontweight="bold" if emph else "normal",bbox=dict(boxstyle="round,pad=.22",fc="white",ec="black" if emph else "gray",alpha=.9),arrowprops=dict(arrowstyle="-",color="gray",linewidth=.7),zorder=10)
        entry,tp=float(hook["entry"]),float(hook["tp"]); confirm=datetime.fromtimestamp(hook["confirmation_time"],tz=timezone.utc)
        left=pd.to_datetime(ha.time.iloc[0],unit="s",utc=True).to_pydatetime(); right=max(pd.to_datetime(ha.time.iloc[-1],unit="s",utc=True).to_pydatetime(),confirm)
        ax.hlines(entry,left,right,linestyles="--",linewidth=1.4,label=f"ENTRY {fmt_price(entry)}",color="darkorange")
        ax.hlines(tp,left,right,linestyles="--",linewidth=1.5,label=f"TP 86.4% {fmt_price(tp)}",color="seagreen")
        if sl is not None: ax.hlines(float(sl),left,right,linestyles="--",linewidth=1.5,label=f"H4 SL {fmt_price(sl)}",color="crimson")
        ax.axvline(confirm,linestyle=":",color="purple",label="M5 CONFIRMED")
        if h4_activation: ax.axvline(datetime.fromtimestamp(h4_activation,tz=timezone.utc),linestyle="-.",color="black",alpha=.8,label="H4 ACTIVATION")
        ax.set_title(f"NDS {timeframe} | {symbol} | {hook['direction']} | HEIKIN ASHI")
        ax.set_xlabel("Time UTC"); ax.set_ylabel("Price"); ax.grid(alpha=.22); ax.legend(loc="best",fontsize=8)
        ax.set_xlim(min(mdates.date2num(left),px[0].timestamp()/86400+719163),max(mdates.date2num(right),px[-1].timestamp()/86400+719163)); fig.autofmt_xdate()
        path=os.path.join(CHART_DIR,f"{path_prefix}_{symbol.replace('/','_').replace(':','_')}_{hook['direction']}_{int(hook['final']['time'])}.png")
        plt.tight_layout(); plt.savefig(path,dpi=140); plt.close(fig); return path
    except Exception as e:
        DIAG["api_errors"].append(f"Chart {symbol}: {str(e)[:180]}")
        plt.close("all"); return None

def get_h4_direction(symbol):
    DIAG["h4_requests"]+=1; df=get_candles(symbol,H4_INTERVAL,H4_CANDLES)
    if df is None: DIAG["h4_data_error"]+=1; return None
    if df.empty: DIAG["h4_empty"]+=1; return None
    if len(df)<50: DIAG["h4_short"]+=1; return None
    DIAG["h4_data_ok"]+=1; H4_DATA[symbol]=df.copy(); ha=heikin_ashi_ohlc(df)
    hi,lo=find_pivots(ha); DIAG["h4_pivots"]+=len(hi)+len(lo); hooks=detect_hooks(ha,H4_INTERVAL_MINUTES); DIAG["h4_hooks"]+=len(hooks)
    now=utc_now_ts(); confirmed=[h for h in hooks if hook_is_confirmed(h,now)]; recent=[h for h in confirmed if hook_is_recent(h,now,H4_MAX_HOOK_AGE_SECONDS)]
    DIAG["h4_confirmed"]+=len(confirmed); DIAG["h4_recent"]+=len(recent); DIAG["h4_valid_hooks"]+=len(recent)
    if not recent: return None
    recent.sort(key=lambda h:(h["confirmation_time"],h["final"]["time"]),reverse=True); latest=recent[0]
    H4_DIRECTION[symbol]={"direction":latest["direction"],"hook":latest,"activation_time":int(latest["confirmation_time"])}
    if latest["direction"]=="SHORT": DIAG["h4_short_direction"]+=1
    else: DIAG["h4_long_direction"]+=1
    return latest["direction"]

def build_signal_message(symbol,hook,sl,h4_hook):
    d=hook["direction"]; emoji="🔴" if d=="SHORT" else "🟢"; entry,tp,sl=map(float,[hook["entry"],hook["tp"],sl])
    risk=(sl-entry)/entry*100 if d=="SHORT" else (entry-sl)/entry*100
    reward=(entry-tp)/entry*100 if d=="SHORT" else (tp-entry)/entry*100
    h4_label="H3" if d=="SHORT" else "L3"
    return (f"{emoji} <b>NDS {d} SIGNAL | H4 → M5</b>\n<b>{symbol}</b>\n\nEntry M5 {('H3' if d=='SHORT' else 'L3')}: <b>{fmt_price(entry)}</b>\n"
      f"SL nearest confirmed H4 pivot: <b>{fmt_price(sl)}</b> ({risk:+.2f}%)\nTP 86.4% M5: <b>{fmt_price(tp)}</b> ({reward:+.2f}%)\n\n"
      f"H4 activation {h4_label}: {fmt_ts(h4_hook['confirmation_time'])}\nM5 hook confirmed: {fmt_ts(hook['confirmation_time'])}\nHook range: {hook['range_pct']:.2f}%\n"
      f"HA hook nodes; trade monitoring uses real OHLC.\n<b>PAPER TRADING ONLY</b>")

def process_symbol(symbol):
    direction=get_h4_direction(symbol); h4info=H4_DIRECTION.get(symbol)
    if direction is None: DIAG["h4_filtered"]+=1; return
    activation=int(h4info["activation_time"]); h4hook=h4info["hook"]
    DIAG["m5_requests"]+=1; df=get_candles(symbol,M5_INTERVAL,M5_CANDLES)
    if df is None: DIAG["m5_data_error"]+=1; return
    if df.empty: DIAG["m5_empty"]+=1; return
    if len(df)<100: DIAG["m5_short"]+=1; return
    DIAG["m5_data_ok"]+=1; ha=heikin_ashi_ohlc(df); hi,lo=find_pivots(ha); DIAG["m5_pivots"]+=len(hi)+len(lo)
    hooks=detect_hooks(ha,M5_INTERVAL_MINUTES); DIAG["m5_hooks"]+=len(hooks); now=utc_now_ts()
    for hook in hooks:
        if not hook_is_confirmed(hook,now): continue
        DIAG["m5_confirmed"]+=1
        if not hook_is_recent(hook,now,M5_MAX_HOOK_AGE_SECONDS): continue
        DIAG["m5_recent"]+=1
        if hook["range_pct"]<M5_MIN_HOOK_RANGE_PCT: continue
        DIAG["m5_range_valid"]+=1
        # New rule: M5 hook must be confirmed AFTER the current H4 H3/L3 activation.
        if hook["confirmation_time"] <= activation: continue
        DIAG["m5_after_h4_activation"]+=1
        # Direction is same-side structure: H4 positive H3 -> M5 positive H3 SHORT;
        # H4 negative L3 -> M5 negative L3 LONG.
        if hook["direction"] != direction: continue
        if tp_already_touched(df,hook): DIAG["m5_tp_touched"]+=1; continue
        sl=calculate_h4_sl(H4_DATA.get(symbol),hook)
        if sl is None: continue
        DIAG["m5_sl_found"]+=1; entry,tp,sl=map(float,[hook["entry"],hook["tp"],sl])
        geometry_ok=(sl>entry>tp) if direction=="SHORT" else (sl<entry<tp)
        if not geometry_ok: continue
        DIAG["m5_geometry_valid"]+=1; key=hook_id(symbol,hook)
        if hook_exists(key) or trade_exists(key): DIAG["m5_duplicate"]+=1; DIAG["duplicate_signals"]+=1; continue
        if open_trade_count()>=MAX_OPEN_TRADES: DIAG["m5_max_open"]+=1; DIAG["max_open"]+=1; return
        DIAG["signal_ready"]+=1
        m5_chart=create_hook_chart(symbol,df,hook,sl,"signal_m5","M5",activation)
        save_hook(symbol,hook,sl,m5_chart); save_trade(key,symbol,hook,sl,m5_chart); DIAG["signals"]+=1
        telegram_send(build_signal_message(symbol,hook,sl,h4hook))
        if m5_chart: telegram_send_photo(m5_chart,f"📍 <b>NDS {direction} SIGNAL | M5</b>\n<b>{symbol}</b>\nEntry: {fmt_price(entry)}\nTP 86.4%: {fmt_price(tp)}\nH4 SL: {fmt_price(sl)}\nH4 activation: {fmt_ts(activation)}\nPAPER TRADING ONLY")
        h4chart=create_hook_chart(symbol,H4_DATA.get(symbol),h4hook,sl,"signal_h4","H4") if H4_DATA.get(symbol) is not None else None
        if h4chart: telegram_send_photo(h4chart,f"🧭 <b>H4 ACTIVATION HOOK</b>\n<b>{symbol}</b>\nDirection: {direction}\nActivation: {fmt_ts(activation)}\nThis H4 hook activated the later M5 hook search.\nPAPER TRADING ONLY")
        return

def get_open_trades():
    c=db_connect(); rows=c.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at ASC").fetchall(); c.close(); return rows
def close_trade(trade,price,pnl_pct,pnl_price,reason):
    c=db_connect(); c.execute("UPDATE trades SET closed_at=?,close_price=?,status=?,pnl_pct=?,pnl_price=? WHERE id=?",(utc_now_ts(),price,reason,pnl_pct,pnl_price,trade["id"])); c.commit(); c.close()
def monitor_open_trades():
    for trade in get_open_trades():
        try:
            symbol,d=trade["symbol"],trade["direction"]; entry,sl,tp=map(float,[trade["entry"],trade["sl"],trade["tp"]]); df=get_candles(symbol,M5_INTERVAL,30)
            if df is None or df.empty: continue
            future=df[df.time>=trade["opened_at"]]
            if future.empty: continue
            reason=price=None
            for _,candle in future.iterrows():
                hi,lo=float(candle.high),float(candle.low); hit_sl=(hi>=sl if d=="SHORT" else lo<=sl); hit_tp=(lo<=tp if d=="SHORT" else hi>=tp)
                if hit_sl: price,reason=sl,"SL"; break
                if hit_tp: price,reason=tp,"TP"; break
            if reason is None: continue
            pnl_price=entry-price if d=="SHORT" else price-entry; pnl_pct=pnl_price/entry*100 if entry else 0
            close_trade(trade,price,pnl_pct,pnl_price,reason); emoji="✅" if pnl_pct>=0 else "❌"
            telegram_send(f"{emoji} <b>NDS {d} CLOSED</b>\n<b>{symbol}</b>\nEntry: {fmt_price(entry)}\nClose: {fmt_price(price)}\nResult: <b>{reason}</b>\nPnL: <b>{pnl_pct:+.2f}%</b>")
        except Exception as e: DIAG["api_errors"].append(f"Monitor {trade['symbol']}: {str(e)[:180]}")
def trade_metrics(entry,sl,tp,d,current):
    if d=="LONG": return ((current-entry)/entry*100,(tp-entry)/entry*100,(sl-entry)/entry*100)
    return ((entry-current)/entry*100,(entry-tp)/entry*100,(entry-sl)/entry*100)
def get_current_price(symbol):
    try:
        r=requests.get(f"{KRAKEN_FUTURES_URL}/tickers",timeout=REQUEST_TIMEOUT)
        if r.ok:
            for t in r.json().get("tickers",[]):
                if isinstance(t,dict) and str(t.get("symbol") or t.get("pair") or t.get("instrument") or "")==symbol:
                    for k in ("last","lastPrice","markPrice","price"):
                        if t.get(k) is not None: return float(t[k])
    except Exception as e: DIAG["api_errors"].append(f"Ticker {symbol}: {str(e)[:160]}")
    df=get_candles(symbol,M5_INTERVAL,2)
    return None if df is None or df.empty else float(df.iloc[-1].close)
def open_trades_report_lines():
    rows=get_open_trades(); lines=["━━━ <b>OPEN TRADES</b> ━━━"]
    if not rows: return lines+["None"]
    for t in rows:
        d,s=t["direction"],t["symbol"]; entry,sl,tp=map(float,[t["entry"],t["sl"],t["tp"]]); cur=get_current_price(s)
        if cur is None: lines.extend([f"{'🟢' if d=='LONG' else '🔴'} <b>{s} {d}</b>",f"Entry: {fmt_price(entry)}","Current: -",f"TP: {fmt_price(tp)}",f"SL: {fmt_price(sl)}",""]); continue
        pnl,tp_pct,sl_pct=trade_metrics(entry,sl,tp,d,cur)
        lines.extend([f"{'🟢' if d=='LONG' else '🔴'} <b>{s} {d}</b>",f"Entry: {fmt_price(entry)}",f"Current: <b>{fmt_price(cur)} ({pnl:+.2f}%)</b>",f"TP: <b>{fmt_price(tp)} ({tp_pct:+.2f}%)</b>",f"SL: <b>{fmt_price(sl)} ({sl_pct:+.2f}%)</b>",""])
    if lines[-1]=="": lines.pop()
    return lines
def performance_summary():
    c=db_connect(); total=c.execute("SELECT COUNT(*) c FROM trades WHERE status!='OPEN'").fetchone()["c"]; wins=c.execute("SELECT COUNT(*) c FROM trades WHERE status='TP'").fetchone()["c"]; losses=c.execute("SELECT COUNT(*) c FROM trades WHERE status='SL'").fetchone()["c"]; pnl=c.execute("SELECT COALESCE(SUM(pnl_pct),0) p FROM trades WHERE status!='OPEN'").fetchone()["p"]; c.close()
    return {"total":int(total or 0),"wins":int(wins or 0),"losses":int(losses or 0),"pnl":float(pnl or 0)}
def diagnostic_text():
    p=performance_summary(); lines=["🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>",f"Version: <b>{VERSION}</b>",f"Time: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}",f"Runtime: <b>{time.time()-START_TIME:.1f}s</b>","","━━━ <b>ASSETS</b> ━━━",f"Scanned: <b>{DIAG['assets_scanned']}</b>","","━━━ <b>H4</b> ━━━"]
    for label,key in [("Requests","h4_requests"),("Data OK","h4_data_ok"),("Data Error","h4_data_error"),("Empty","h4_empty"),("Short","h4_short"),("Pivot Points","h4_pivots"),("Hooks","h4_hooks"),("Confirmed","h4_confirmed"),("Recent ≤24h","h4_recent"),("Valid Activation Hooks","h4_valid_hooks"),("SHORT H3 Activations","h4_short_direction"),("LONG L3 Activations","h4_long_direction"),("Filtered","h4_filtered")]: lines.append(f"{label}: {DIAG[key]}")
    lines += ["","━━━ <b>M5</b> ━━━"]
    for label,key in [("Requests","m5_requests"),("Data OK","m5_data_ok"),("Data Error","m5_data_error"),("Empty","m5_empty"),("Short","m5_short"),("Pivot Points","m5_pivots"),("Hooks","m5_hooks"),("Confirmed","m5_confirmed"),("Recent ≤6h","m5_recent"),(f"Range Valid ≥{M5_MIN_HOOK_RANGE_PCT:.2f}%","m5_range_valid"),("Confirmed After H4 Activation","m5_after_h4_activation"),("TP Already Touched","m5_tp_touched"),("SL Found","m5_sl_found"),("Geometry Valid","m5_geometry_valid"),("Duplicate","m5_duplicate"),("Max Open","m5_max_open"),("Signal Ready","signal_ready")]: lines.append(f"{label}: {DIAG[key]}")
    lines += ["","━━━ <b>SIGNALS</b> ━━━",f"Signals: {DIAG['signals']}",f"Duplicates: {DIAG['duplicate_signals']}",f"Max Open Limit: {DIAG['max_open']}",f"Open Trades: <b>{open_trade_count()}</b>","",*open_trades_report_lines(),"","━━━ <b>PAPER PERFORMANCE</b> ━━━",f"Closed Trades: {p['total']}",f"TP: {p['wins']}",f"SL: {p['losses']}",f"PnL: <b>{p['pnl']:+.2f}%</b>"]
    if DIAG["api_errors"]: lines += ["","━━━ <b>ERRORS</b> ━━━"]+["• "+e for e in DIAG["api_errors"][-5:]]
    lines += ["","<b>PAPER TRADING ONLY - NO REAL ORDERS</b>"]
    return "\n".join(lines)
def main():
    try:
        init_db(); print(f"NDS H4 -> M5 Scanner {VERSION}"); print("H4 confirmed H3/L3 activates same-direction M5 hook search"); print("Entry M5 H3/L3 | TP 86.4% M5 | SL nearest confirmed H4 HA pivot"); print("PAPER TRADING ONLY - NO REAL ORDERS")
        symbols=get_futures_instruments()
        if not symbols:
            msg="❌ <b>NDS Scanner</b>\n\nNo futures instruments found."; print(msg); telegram_send(msg); return
        DIAG["assets_scanned"]=len(symbols); monitor_open_trades()
        for symbol in symbols:
            try: process_symbol(symbol)
            except Exception as e: DIAG["api_errors"].append(f"Process {symbol}: {str(e)[:180]}"); traceback.print_exc()
            time.sleep(SCAN_SLEEP_SECONDS)
        monitor_open_trades(); report=diagnostic_text(); print("\n"+report+"\n"); telegram_send(report)
    except Exception as e:
        traceback.print_exc(); telegram_send(f"❌ <b>NDS Scanner Error</b>\n\n{type(e).__name__}: {str(e)[:500]}")
if __name__=="__main__": main()
