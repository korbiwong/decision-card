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

SHADOW = ("突破但有賣壓", "支撐反轉（觀察）")   # 降級咗／只觀察嘅訊號照計，用嚟驗證個決定啱唔啱

# 出場方法（止蝕 × 點樣走）。X1 = 而家用緊嘅規則
EXITS = [("X1", "規則止蝕＋目標價（而家）", "atr", "target", 20), ("X2", "規則止蝕＋賺 1.5 倍風險就走", "atr", "t1", 20),
         ("X3", "規則止蝕＋跌穿 20 日線先走", "atr", "trail20", 60), ("X4", "規則止蝕＋跌穿 10 日低位先走", "atr", "trail10", 60),
         ("X5", "支撐止蝕＋目標價", "sup", "target", 20), ("X6", "支撐止蝕＋跌穿 20 日線先走", "sup", "trail20", 60)]
# 入場過濾
FILTERS = [("全部", lambda t: True), ("大市強", lambda t: t["mk"] is True), ("大市弱", lambda t: t["mk"] is False),
           ("週線向上", lambda t: t["wk"] == "🟢"), ("長線向上", lambda t: t["lt"] == "🟢"), ("大市強＋週線向上", lambda t: t["mk"] is True and t["wk"] == "🟢"),
           ("RSI<70", lambda t: t["rsi"] < 70), ("RSI≥70", lambda t: t["rsi"] >= 70), ("成交≥2倍", lambda t: t["volR"] >= 2),
           ("離支撐最多輸≤3%", lambda t: t["sr"] is not None and t["sr"] <= 3), ("離支撐最多輸3–6%", lambda t: t["sr"] is not None and 3 < t["sr"] <= 6),
           ("離支撐最多輸>6%", lambda t: t["sr"] is not None and t["sr"] > 6)]

def one_exit(i, a, stop, how, days, o, h, l, c, s20, lo10, n):
    """第 i 日收市出訊號 → 第 i+1 日開市買。回傳 (出場日, 出場價, 入場價) 或 None（開市已經穿咗止蝕／目標）。"""
    e = o[i + 1]
    if stop is None or e <= stop: return None
    tgt = a["target"] if how == "target" else e + 1.5 * (e - stop) if how == "t1" else None
    if tgt is not None and e >= tgt: return None
    for j in range(i + 1, min(i + 1 + days, n)):
        if l[j] <= stop: return j, (stop if j == i + 1 else min(o[j], stop)), e   # 同一日又穿止蝕又到目標 → 當止蝕（保守）
        if tgt is not None and h[j] >= tgt: return j, (tgt if j == i + 1 else max(o[j], tgt)), e
        if j > i + 1 and how == "trail20" and c[j] < s20[j]: return j, c[j], e
        if j > i + 1 and how == "trail10" and c[j] < lo10[j - 1]: return j, c[j], e
    j = min(i + days, n - 1); return j, c[j], e

def simulate(df, m, regime=None, start=None):
    """一隻股由頭行到尾。回傳 (主表交易, 各種組合嘅交易)。每種玩法同一時間只揸一注。"""
    df = df.dropna(subset=["Open", "High", "Low", "Close"]); n = len(df)
    o, h, l, c = (df[x].astype(float).values for x in ("Open", "High", "Low", "Close")); exp = int(scanner.prm("expiryDays", 20))
    s20 = pd.Series(c).rolling(20).mean().values; lo10 = pd.Series(l).rolling(10).min().values
    sig = {}
    for i in range(WIN, n - 1):
        if start is not None and df.index[i] < start: continue
        a = scanner.analyse(df.iloc[i - WIN:i + 1], m)
        if a and (a["lamp"] == "🟢" or a["word"] in SHADOW): sig[i] = a
    def tag(i, a):
        mk = None
        if regime is not None:
            r = regime.asof(df.index[i]); mk = None if pd.isna(r) else bool(r)
        return dict(d=df.index[i], mk=mk, wk=a["tf"].get("week"), lt=a["tf"]["long"], rsi=a.get("rsi") or 50, volR=a["volR"], sr=a.get("supRisk"))
    trades, free = [], 0
    for i, a in sig.items():   # 主表：而家嘅規則
        if i < free: continue
        r = one_exit(i, a, a["stop"], "target", exp, o, h, l, c, s20, lo10, n)
        if not r: continue
        j, x, e = r; free = j + 1
        setup = "breakout_bearK" if a["setup"] == "breakout" and a.get("k") and a["k"]["dir"] == "bear" else a["setup"]
        trades.append(dict(setup=setup, x=df.index[j], ret=(x / e - 1) * 100, r=(x - e) / (e - a["stop"]), **tag(i, a)))
    grid = []
    for setup in ("breakout", "reversal"):
        for xid, _, stopk, how, days in EXITS:
            free = 0
            for i, a in sig.items():
                if a["setup"] != setup or i < free: continue
                stop = a["stop"] if stopk == "atr" else (a.get("supStop") if a.get("supRisk") is not None and a["supRisk"] <= 10 else None)
                r = one_exit(i, a, stop, how, exp if days == 20 else days, o, h, l, c, s20, lo10, n)
                if not r: continue
                j, x, e = r; free = j + 1
                grid.append(dict(setup=setup, xid=xid, x=df.index[j], ret=(x / e - 1) * 100, r=(x - e) / (e - stop), **tag(i, a)))
    return trades, grid

def stats(tr):
    n = len(tr)
    if not n: return dict(n=0, win=None, avgWin=None, avgLoss=None, pf=None, avg=None, expR=None, maxDD=None)
    wins = [t["ret"] for t in tr if t["ret"] > 0]; loss = [t["ret"] for t in tr if t["ret"] <= 0]
    eq = pd.Series([t["r"] for t in sorted(tr, key=lambda t: t["x"])]).cumsum(); dd = float((eq - eq.cummax().clip(lower=0)).min())
    return dict(n=n, win=round(len(wins) / n * 100), avgWin=round(float(sum(wins) / len(wins)), 2) if wins else None, avgLoss=round(float(sum(loss) / len(loss)), 2) if loss else None,
                pf=round(float(sum(wins) / -sum(loss)), 2) if loss and sum(loss) < 0 else None, avg=round(float(sum(t["ret"] for t in tr) / n), 2),
                expR=round(float(sum(t["r"] for t in tr) / n), 2), maxDD=round(dd, 1))

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

def summarise_grid(m, grid):
    """每個「入場過濾 × 出場方法」組合：全期、前半段、後半段。前後半段都好先算數（避免過度擬合）。"""
    rows = []
    if not grid: return rows
    mid = sorted(t["d"] for t in grid)[len(grid) // 2]; names = {x[0]: x[1] for x in EXITS}
    for setup in ("breakout", "reversal"):
        for xid, _, *_ in EXITS:
            base = [t for t in grid if t["setup"] == setup and t["xid"] == xid]
            for fn, f in FILTERS:
                v = [t for t in base if f(t)]
                if len(v) < 30: continue
                st = stats(v); h1 = stats([t for t in v if t["d"] < mid]); h2 = stats([t for t in v if t["d"] >= mid])
                rows.append(dict(m=m, setup=setup, xid=xid, x=names[xid], f=fn, n=st["n"], win=st["win"], avgWin=st["avgWin"], avgLoss=st["avgLoss"], pf=st["pf"], avg=st["avg"], maxDD=st["maxDD"],
                                 pf1=h1["pf"], pf2=h2["pf"], win1=h1["win"], win2=h2["win"], n1=h1["n"], n2=h2["n"]))
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
    start = pd.Timestamp(dt.date.today()) - pd.DateOffset(years=years); trades = []; grid = []; done = 0
    for i in range(0, len(codes), 50):
        batch = [tick(c) for c in codes[i:i + 50]]; data = None
        for attempt in range(3):
            try: data = yf.download(batch, period=f"{years + 1}y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False); break
            except Exception as e: print("batch failed", attempt, e); time.sleep(20 * (attempt + 1))
        if data is None or data.empty: continue
        for t in batch:
            try: tr, gr = simulate(naive(data[t] if len(batch) > 1 else data), m, regime, start); trades += tr; grid += gr; done += 1
            except Exception as e: print("skip", t, e)
        print(f"{m} {min(i + 50, len(codes))}/{len(codes)} stocks, {len(trades)} trades", flush=True)
    return done, trades, grid

if __name__ == "__main__":
    markets = sys.argv[1:] or ["HK", "US"]; N = int(os.environ.get("BT_N", 150)); Y = int(os.environ.get("BT_YEARS", 4))
    rows, grid, total = [], [], 0
    for m in markets:
        done, trades, gr = run(m, N, Y); total += done; rows += summarise(m, trades); grid += summarise_grid(m, gr)
    if not rows: sys.exit("no trades — data download failed?")
    out = {"generated": dt.datetime.now(scanner.HKT).isoformat(timespec="minutes"), "period": f"過去 {Y} 年", "stocks": total, "params": scanner.P, "rows": rows, "grid": grid,
           "note": "只測而家成交最多嘅股票（有倖存者偏差，結果會偏樂觀）；第二日開市價入，未計佣金同滑價。R = 賺蝕除以入場到止蝕嘅距離。少過 30 單嘅分組唔好當真。"}
    (ROOT / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for r in rows: print(r)
