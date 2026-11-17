#!/usr/bin/env python3
"""回測規則引擎：用過去幾年日線，逐日行 scanner.analyse()，見 🟢 就第二日開市買，
用同一個止蝕／目標／到期日計結果。寫 backtest.json 畀網頁「檢討」頁睇。
用法：python backtest.py [HK] [US]   （BT_N=每個市場測幾多隻，預設 150；BT_YEARS=幾多年，預設 4）"""
import json, os, sys, time, datetime as dt
from pathlib import Path
import pandas as pd
import scanner

ROOT = Path(__file__).parent
WIN = 260   # analyse() 每次睇返一年日線，同正式掃描一樣

def naive(x):
    x = x.copy(); x.index = pd.to_datetime(x.index)
    if x.index.tz is not None: x.index = x.index.tz_localize(None)
    x.index = x.index.normalize(); return x[~x.index.duplicated(keep="last")].sort_index()

def simulate(df, m, regime=None, start=None):
    """一隻股由頭行到尾。同一時間只揸一注。回傳每單交易。"""
    df = df.dropna(subset=["Open", "High", "Low", "Close"]); n = len(df); trades = []
    o, h, l, c = (df[x].astype(float).values for x in ("Open", "High", "Low", "Close")); exp = int(scanner.prm("expiryDays", 20))
    i = WIN
    while i < n - 1:
        if start is not None and df.index[i] < start: i += 1; continue
        a = scanner.analyse(df.iloc[i - WIN:i + 1], m)
        shadow = bool(a) and a["word"] in ("突破但有賣壓", "支撐反轉（觀察）")   # 降級咗／只觀察嘅訊號照計，用嚟驗證個決定啱唔啱
        if not a or not (a["lamp"] == "🟢" or shadow): i += 1; continue
        e = o[i + 1]
        if e <= a["stop"] or e >= a["target"]: i += 1; continue   # 開市已經裂口穿咗止蝕／目標，唔入
        risk = e - a["stop"]; out = None
        for j in range(i + 1, min(i + 1 + exp, n)):
            if l[j] <= a["stop"]: out = (j, a["stop"] if j == i + 1 else min(o[j], a["stop"]), "stop"); break   # 同一日又穿止蝕又到目標 → 當止蝕（保守）
            if h[j] >= a["target"]: out = (j, a["target"] if j == i + 1 else max(o[j], a["target"]), "target"); break
        if out is None: j = min(i + exp, n - 1); out = (j, c[j], "expired")
        j, x, st = out
        mk = None
        if regime is not None:
            r = regime.asof(df.index[i]); mk = None if pd.isna(r) else bool(r)
        trades.append(dict(setup="breakout_bearK" if a["setup"] == "breakout" and a.get("k") and a["k"]["dir"] == "bear" else a["setup"], d=df.index[i], x=df.index[j], ret=(x / e - 1) * 100, r=(x - e) / risk, st=st, mk=mk))
        i = j + 1
    return trades

def stats(tr):
    n = len(tr)
    if not n: return dict(n=0, win=None, avgWin=None, avgLoss=None, pf=None, expR=None, maxDD=None)
    wins = [t["ret"] for t in tr if t["ret"] > 0]; loss = [t["ret"] for t in tr if t["ret"] <= 0]
    eq = pd.Series([t["r"] for t in sorted(tr, key=lambda t: t["x"])]).cumsum(); dd = float((eq - eq.cummax().clip(lower=0)).min())
    return dict(n=n, win=round(len(wins) / n * 100), avgWin=round(sum(wins) / len(wins), 2) if wins else None, avgLoss=round(sum(loss) / len(loss), 2) if loss else None,
                pf=round(sum(wins) / -sum(loss), 2) if loss and sum(loss) < 0 else None, expR=round(sum(t["r"] for t in tr) / n, 2), maxDD=round(dd, 1))

def summarise(m, trades):
    rows = []
    if not trades: return rows
    mid = sorted(t["d"] for t in trades)[len(trades) // 2]
    for setup in ("breakout", "reversal", "breakout_bearK"):
        ts = [t for t in trades if t["setup"] == setup]
        if not ts: continue
        groups = [("全部", ts), ("大市強", [t for t in ts if t["mk"] is True]), ("大市弱", [t for t in ts if t["mk"] is False]),
                  ("前半段", [t for t in ts if t["d"] < mid]), ("後半段", [t for t in ts if t["d"] >= mid])]   # 前後半段表現差好遠 = 可能只係撞啱
        for g, v in groups:
            if v: rows.append(dict(m=m, setup=setup, g=g, **stats(v)))
    return rows

def regime_series(sym):
    """同 scanner.index_trend 一樣：指數收市高過 50 日線，而 50 日線比 10 日前高。"""
    import yfinance as yf
    try:
        c = naive(yf.Ticker(sym).history(period="6y"))["Close"].dropna(); s50 = c.rolling(50).mean()
        return (c > s50) & (s50 > s50.shift(10))
    except Exception as e: print("regime failed", e); return None

def run(m, n_stocks, years):
    import yfinance as yf
    sig = json.loads((ROOT / f"signals_{m}.json").read_text()).get("all", [])
    codes = [x["code"] for x in sorted(sig, key=lambda x: -x.get("turnover", 0))[:n_stocks]]
    tick = lambda c: f"{int(c):04d}.HK" if m == "HK" else c
    regime = regime_series("^HSI" if m == "HK" else "^GSPC")
    start = pd.Timestamp(dt.date.today()) - pd.DateOffset(years=years); trades = []; done = 0
    for i in range(0, len(codes), 50):
        batch = [tick(c) for c in codes[i:i + 50]]; data = None
        for attempt in range(3):
            try: data = yf.download(batch, period=f"{years + 1}y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False); break
            except Exception as e: print("batch failed", attempt, e); time.sleep(20 * (attempt + 1))
        if data is None or data.empty: continue
        for t in batch:
            try: trades += simulate(naive(data[t] if len(batch) > 1 else data), m, regime, start); done += 1
            except Exception as e: print("skip", t, e)
        print(f"{m} {min(i + 50, len(codes))}/{len(codes)} stocks, {len(trades)} trades", flush=True)
    return done, trades

if __name__ == "__main__":
    markets = sys.argv[1:] or ["HK", "US"]; N = int(os.environ.get("BT_N", 150)); Y = int(os.environ.get("BT_YEARS", 4))
    rows, total = [], 0
    for m in markets:
        done, trades = run(m, N, Y); total += done; rows += summarise(m, trades)
    if not rows: sys.exit("no trades — data download failed?")
    out = {"generated": dt.datetime.now(scanner.HKT).isoformat(timespec="minutes"), "period": f"過去 {Y} 年", "stocks": total, "params": scanner.P, "rows": rows,
           "note": "只測而家成交最多嘅股票（有倖存者偏差，結果會偏樂觀）；第二日開市價入，未計佣金同滑價。R = 賺蝕除以入場到止蝕嘅距離。少過 30 單嘅分組唔好當真。"}
    (ROOT / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for r in rows: print(r)
