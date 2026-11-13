#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NDS M5/M1 Fractal F3 Scanner v1.0.0 - Kraken Futures paper mode only.

M5 detects the F1/F2 context and the post-F2 correction sweep.
M1 triggers at the beginning of the F3 reversal (closed candle breaks the
previous M1 candle high/low). All prices are real OHLC; Heikin Ashi is unused.
SL = latest confirmed M5 swing before entry. TP = first confirmed M5 swing
in the trade direction AFTER entry. No future candles/look-ahead are used.
"""
from __future__ import annotations
import os, time, json, sqlite3, logging
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional
import requests

VERSION = "1.0.0"
BASE = "https://futures.kraken.com/derivatives/api/v3"
CHARTS = "https://futures.kraken.com/api/charts/v1"
TELEGRAM = "https://api.telegram.org"
SYMBOLS = ("PF_XAUTUSD", "PF_XBTUSD")
M5_LIMIT = int(os.getenv("M5_CANDLE_COUNT", "3000"))
M1_LIMIT = int(os.getenv("M1_CANDLE_COUNT", "3000"))
PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", "5"))
PIVOT_RIGHT = int(os.getenv("PIVOT_RIGHT", "5"))
MAX_OPEN_TRADES = max(1, int(os.getenv("MAX_OPEN_TRADES", "3")))
DB_PATH = Path(os.getenv("DB_PATH", "nds_m5_m1_fractal_f3_v1.db"))
TZ_NAME = os.getenv("REPORT_TIMEZONE", "Europe/Berlin")
try: TZ = ZoneInfo(TZ_NAME)
except Exception: TZ = timezone.utc
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": f"NDS-M5-M1-F3/{VERSION}", "Accept": "application/json"})
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("nds_f3")


def f(x):
    try:
        v=float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError): return None

def fmt(x):
    if x is None: return "PENDING"
    return f"{x:,.2f}" if abs(x)>=1000 else f"{x:.8f}".rstrip("0").rstrip(".")

def pct(entry, price, direction):
    if not entry or not price: return 0.0
    return ((price/entry-1) if direction=="LONG" else (entry/price-1))*100

def stamp(ms): return datetime.fromtimestamp(ms/1000, timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

def api(url, params=None):
    err=None
    for attempt in range(3):
        try:
            r=HTTP.get(url, params=params, timeout=(8,25)); r.raise_for_status(); data=r.json()
            if not isinstance(data,dict): raise ValueError("Invalid JSON response")
            return data
        except Exception as e:
            err=e
            if attempt<2: time.sleep(.7*(attempt+1))
    raise RuntimeError(f"Request failed: {err}")

def get_tickers():
    data=api(f"{BASE}/tickers")
    rows=data.get("tickers")
    if not isinstance(rows,list): raise RuntimeError("Kraken tickers response is invalid")
    return {str(x.get("symbol","" )).upper():x for x in rows if isinstance(x,dict)}

def price_of(t):
    if not t: return None
    bid=f(t.get("bid")) or f(t.get("bidPrice")); ask=f(t.get("ask")) or f(t.get("askPrice"))
    if bid and ask and ask>=bid: return (bid+ask)/2
    for k in ("markPrice","mark_price","last","lastPrice","lastTradePrice","indexPrice"):
        v=f(t.get(k))
        if v and v>0: return v
    return None

def candles(symbol, tf, limit):
    ms=300000 if tf=="5m" else 60000
    now=int(time.time()); data=api(f"{CHARTS}/trade/{symbol}/{tf}", {"from":now-(ms//1000)*(limit+5),"to":now,"count":limit})
    rows=data.get("candles",[]); by={}; nowms=int(time.time()*1000)
    if not isinstance(rows,list): raise RuntimeError(f"Invalid {tf} candles for {symbol}")
    for r in rows:
        if not isinstance(r,dict): continue
        ts=f(r.get("time")); o=f(r.get("open")); h=f(r.get("high")); l=f(r.get("low")); c=f(r.get("close"))
        if None in (ts,o,h,l,c): continue
        ts=int(ts); ts=ts*1000 if ts<10**12 else ts
        if ts+ms>nowms or l>h or min(o,h,l,c)<=0: continue
        by[ts]={"time":ts,"open":o,"high":h,"low":l,"close":c,"volume":f(r.get("volume")) or 0}
    return [by[k] for k in sorted(by)][-limit:]

def pivots(bars):
    """Confirmed strict OHLC pivots; returned as (index, kind, price)."""
    out=[]; L=PIVOT_LEFT; R=PIVOT_RIGHT
    for i in range(L,len(bars)-R):
        lo=bars[i]["low"]; hi=bars[i]["high"]
        left=bars[i-L:i]; right=bars[i+1:i+R+1]
        islo=all(lo < x["low"] for x in left+right)
        ishi=all(hi > x["high"] for x in left+right)
        if islo: out.append((i,"L",lo))
        if ishi: out.append((i,"H",hi))
    out.sort(key=lambda z:z[0])
    # In an outside candle both may qualify; retain the more extreme side only
    # when a tie is impossible. This avoids inventing intrabar ordering.
    return out

def m5_setup(symbol, bars, direction):
    """Return active F3 sweep context using four alternating F1/F2 pivots.

    LONG: H1,L1,H2,L2 with H2>H1 and L2>L1; then price breaks H2,
    subsequently sweeps below L2 (wick counts). SHORT is mirrored.
    """
    ps=pivots(bars)
    for j in range(len(ps)-4,-1,-1):
        seq=ps[j:j+4]
        if len(seq)<4: continue
        kinds=[x[1] for x in seq]
        want=["H","L","H","L"] if direction=="LONG" else ["L","H","L","H"]
        if kinds!=want: continue
        a,b,c,d=seq
        if direction=="LONG":
            if not (c[2]>a[2] and d[2]>b[2]): continue
            # Break H2 must occur after L2, then sweep L2 after breakout.
            br=next((k for k in range(d[0]+1,len(bars)) if bars[k]["high"]>c[2]),None)
            if br is None: continue
            sw=next((k for k in range(br+1,len(bars)) if bars[k]["low"]<d[2]),None)
        else:
            if not (c[2]<a[2] and d[2]<b[2]): continue
            br=next((k for k in range(d[0]+1,len(bars)) if bars[k]["low"]<c[2]),None)
            if br is None: continue
            sw=next((k for k in range(br+1,len(bars)) if bars[k]["high"]>d[2]),None)
        if sw is None: continue
        # Latest setup only; don't reuse an old sweep beyond 6 hours.
        if (bars[-1]["time"]-bars[sw]["time"]) > 6*3600*1000: continue
        key=f"{symbol}|{direction}|{bars[a[0]]['time']}|{bars[b[0]]['time']}|{bars[c[0]]['time']}|{bars[d[0]]['time']}|{bars[sw]['time']}"
        return {"key":key,"direction":direction,"sweep_time":bars[sw]["time"],"sweep_index":sw,
                "f2_level":d[2],"h2_level":c[2],"pivot_indices":[a[0],b[0],c[0],d[0]]}
    return None

def db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=30000")
    c.executescript('''
    CREATE TABLE IF NOT EXISTS setups(setup_key TEXT PRIMARY KEY,symbol TEXT,direction TEXT,sweep_time INTEGER,created_at REAL);
    CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,trade_key TEXT UNIQUE,symbol TEXT,direction TEXT,entry_time INTEGER,entry_price REAL,sl_price REAL,tp_price REAL,status TEXT DEFAULT 'OPEN',exit_time INTEGER,exit_price REAL,pnl_pct REAL,created_at REAL);
    ''')
    c.commit(); return c

def tg_send(message):
    token=os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat=os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")
    if not token or not chat: LOG.info("Telegram not configured; report follows:\n%s",message); return
    try:
        r=HTTP.post(f"{TELEGRAM}/bot{token}/sendMessage",data={"chat_id":chat,"text":message},timeout=20); r.raise_for_status()
    except Exception: LOG.exception("Telegram send failed")

def last_pre_entry_sl(m5, entry_ms, entry_price, direction):
    pp=pivots(m5); eligible=[x for x in pp if x[0]+PIVOT_RIGHT<len(m5) and m5[x[0]+PIVOT_RIGHT]["time"]+300000<=entry_ms and m5[x[0]]["time"]<entry_ms and ((x[1]=="L" and x[2]<entry_price) if direction=="LONG" else (x[1]=="H" and x[2]>entry_price))]
    if not eligible: return None
    return eligible[-1][2]

def process_trades(conn, m5_by_symbol, prices):
    closed=[]
    for tr in conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall():
        bars=m5_by_symbol.get(tr["symbol"]);
        if not bars: continue
        direction=tr["direction"]; sl=tr["sl_price"]; tp=tr["tp_price"]
        # Find bars after entry that have not already been evaluated, via latest checked time.
        cols={r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        if "last_checked_time" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN last_checked_time INTEGER NOT NULL DEFAULT 0"); conn.commit()
        last=int(conn.execute("SELECT last_checked_time FROM trades WHERE id=?",(tr["id"],)).fetchone()[0])
        for bar in bars:
            if bar["time"]<tr["entry_time"] or bar["time"]<=last: continue
            if tp is None:
                # First newly confirmed M5 pivot in target direction after entry.
                pp=pivots(bars)
                candidate=None
                for ix,kind,val in pp:
                    if bars[ix]["time"]>tr["entry_time"] and ix+PIVOT_RIGHT<len(bars) and bars[ix+PIVOT_RIGHT]["time"]+300000<=bar["time"]+300000:
                        if (direction=="LONG" and kind=="H" and val>tr["entry_price"]) or (direction=="SHORT" and kind=="L" and val<tr["entry_price"]):
                            candidate=(ix,val); break
                if candidate:
                    tp=float(candidate[1]); conn.execute("UPDATE trades SET tp_price=? WHERE id=?",(tp,tr["id"]))
            hit_sl=(bar["low"]<=sl if direction=="LONG" else bar["high"]>=sl)
            hit_tp=(tp is not None and (bar["high"]>=tp if direction=="LONG" else bar["low"]<=tp))
            if hit_sl or hit_tp:
                status="CLOSED_SL" if hit_sl else "CLOSED_TP"; exitp=float(sl if hit_sl else tp)
                pnl=pct(float(tr["entry_price"]),exitp,direction)
                conn.execute("UPDATE trades SET status=?,exit_time=?,exit_price=?,pnl_pct=?,last_checked_time=? WHERE id=?",(status,bar["time"]+300000,exitp,pnl,bar["time"],tr["id"]))
                closed.append((tr["symbol"],direction,status,float(tr["entry_price"]),exitp,pnl)); break
            last=bar["time"]
            conn.execute("UPDATE trades SET last_checked_time=? WHERE id=?",(last,tr["id"]))
        conn.commit()
    return closed

def main():
    conn=db(); now=time.time(); ticker_map=get_tickers(); m5map={}; prices={}; new=[]; errors=[]; diag=[]
    for symbol in SYMBOLS:
        try:
            m5=candles(symbol,"5m",M5_LIMIT); m1=candles(symbol,"1m",M1_LIMIT)
            if len(m5)<PIVOT_LEFT+PIVOT_RIGHT+20 or len(m1)<3: raise RuntimeError("Not enough closed candles")
            m5map[symbol]=m5; prices[symbol]=price_of(ticker_map.get(symbol)) or m1[-1]["close"]
            for direction in ("LONG","SHORT"):
                setup=m5_setup(symbol,m5,direction)
                if not setup: continue
                conn.execute("INSERT OR IGNORE INTO setups VALUES(?,?,?,?,?)",(setup["key"],symbol,direction,setup["sweep_time"],time.time()))
                if conn.execute("SELECT 1 FROM trades WHERE trade_key=?",(setup["key"],)).fetchone(): continue
                # M1 must trigger strictly after the M5 sweep; latest closed candle
                # breaks previous candle's extreme. This is the early F3 trigger.
                cur=m1[-1]; prev=m1[-2]
                if cur["time"]<=setup["sweep_time"]: continue
                trigger=(cur["close"]>prev["high"] if direction=="LONG" else cur["close"]<prev["low"])
                if not trigger: continue
                entry=float(cur["close"]); entry_time=int(cur["time"]+60000)
                sl=last_pre_entry_sl(m5,entry_time,entry,direction)
                if sl is None or (direction=="LONG" and not sl<entry) or (direction=="SHORT" and not sl>entry):
                    diag.append(f"{symbol} {direction}: no valid pre-entry M5 stop pivot"); continue
                if conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0]>=MAX_OPEN_TRADES: continue
                # Only one trade per symbol/direction/setup; TP remains NULL until a future M5 pivot is confirmed.
                cur_insert=conn.execute("INSERT OR IGNORE INTO trades(trade_key,symbol,direction,entry_time,entry_price,sl_price,tp_price,status,created_at) VALUES(?,?,?,?,?,?,NULL,'OPEN',?)",
                                        (setup["key"],symbol,direction,entry_time,entry,sl,time.time()))
                if cur_insert.rowcount == 1:
                    new.append((symbol,direction,entry,sl,setup["key"]))
            conn.commit()
        except Exception as e:
            LOG.exception("Scan failed for %s",symbol); errors.append(f"{symbol}: {e}")
    closed=process_trades(conn,m5map,prices)
    opens=conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time").fetchall()
    done=conn.execute("SELECT pnl_pct,status FROM trades WHERE status LIKE 'CLOSED_%'").fetchall()
    wins=sum(1 for x in done if x["status"]=="CLOSED_TP"); losses=len(done)-wins
    realized=sum(float(x["pnl_pct"] or 0) for x in done); floating=sum(pct(x["entry_price"],prices.get(x["symbol"]),x["direction"]) for x in opens)
    lines=[f"NDS M5+M1 FRACTAL F3 v{VERSION} | {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
           "Symbols: XAUUSD + XBTUSD | OHLC only | Heikin Ashi: OFF | Paper only",
           f"M5 setups checked: {len(m5map)*2} | New M1 F3 entries: {len(new)} | Open: {len(opens)}/{MAX_OPEN_TRADES}"]
    for s,d,e,sl,k in new: lines.append(f"{'🟢' if d=='LONG' else '🔴'} NEW {d} {s} | Entry M1 {fmt(e)} | SL M5 {fmt(sl)} | TP M5 PENDING")
    if not new: lines.append("No new M1 F3 trigger this run.")
    for s,d,st,en,ex,p in closed: lines.append(f"CLOSED {st} {s} {d} | Entry {fmt(en)} | Exit {fmt(ex)} | PnL {p:+.2f}%")
    lines.append("OPEN TRADES:")
    for x in opens: lines.append(f"{x['symbol']} {x['direction']} | Entry {fmt(x['entry_price'])} | SL {fmt(x['sl_price'])} | TP {fmt(x['tp_price'])} | Now {fmt(prices.get(x['symbol']))}")
    if not opens: lines.append("None")
    lines += [f"Performance: closed {len(done)} | W/L {wins}/{losses} | Win rate {(wins/len(done)*100 if done else 0):.2f}%",
              f"Realized PnL {realized:+.2f}% | Floating PnL {floating:+.2f}% | Total {realized+floating:+.2f}%",
              "TP = first future confirmed M5 swing high (LONG) / swing low (SHORT). SL = latest confirmed pre-entry M5 swing.",
              "No real exchange orders are ever placed."]
    if errors: lines.append("Errors: " + " | ".join(errors))
    if diag: lines.append("Diagnostics: " + " | ".join(diag[:8]))
    report="\n".join(lines); LOG.info("\n%s",report); tg_send(report); conn.close(); return 0

if __name__=="__main__": raise SystemExit(main())
