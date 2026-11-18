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
           ("離支撐最多輸>6%", lambda t: t["sr"] is not None and t["sr"] > 6),
           # 網上常見技巧
           ("貼近 52 週高位（5% 內）", lambda t: t["hi52"] is not None and t["hi52"] <= 5), ("離 52 週高位 >15%", lambda t: t["hi52"] is not None and t["hi52"] > 15),
           ("半年升 ≥20%", lambda t: t["mom6"] is not None and t["mom6"] >= 20), ("半年升 <20%", lambda t: t["mom6"] is not None and t["mom6"] < 20),
           ("MACD 向上", lambda t: t["macd"] is True), ("波幅收縮", lambda t: t["vcr"] is not None and t["vcr"] < 0.8)]

def fee_pct(riskfrac):
    """手續費佔注碼幾多 %（買 + 賣）。注碼同網頁一樣計：每單最多蝕本金 1%，單一隻最多佔本金 25%。"""
    cap = scanner.prm("btCapital", 10000); pos = min(cap * scanner.prm("btRiskPct", 1) / 100 / max(riskfrac, 1e-4), cap * 0.25)
    return 2 * scanner.prm("fee", 2) / pos * 100

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
        return dict(d=df.index[i], mk=mk, wk=a["tf"].get("week"), lt=a["tf"]["long"], rsi=a.get("rsi") or 50, volR=a["volR"], sr=a.get("supRisk"), hi52=a.get("hi52"), mom6=a.get("mom6"), macd=a.get("macd"), vcr=a.get("vcr"))
    trades, free = [], 0
    for i, a in sig.items():   # 主表：而家嘅規則
        if i < free: continue
        r = one_exit(i, a, a["stop"], "target", exp, o, h, l, c, s20, lo10, n)
        if not r: continue
        j, x, e = r; free = j + 1
        setup = "breakout_bearK" if a["setup"] == "breakout" and a.get("k") and a["k"]["dir"] == "bear" else a["setup"]
        rf = (e - a["stop"]) / e; fp = fee_pct(rf)
        trades.append(dict(setup=setup, x=df.index[j], ret=(x / e - 1) * 100 - fp, r=(x - e) / (e - a["stop"]) - fp / (rf * 100), **tag(i, a)))
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
                rf = (e - stop) / e; fp = fee_pct(rf)
                grid.append(dict(setup=setup, xid=xid, x=df.index[j], ret=(x / e - 1) * 100 - fp, r=(x - e) / (e - stop) - fp / (rf * 100), **tag(i, a)))
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

def study_one(df, start):
    """網上常見技巧驗證：訊號出現嗰日收市買，20 個交易日之後升定跌。回傳 {訊號名: (日期, 之後 20 日回報%)}，「基準」= 任何一日買。"""
    df = df.dropna(subset=["Open", "High", "Low", "Close"]); o, h, l, c, v = (df[x].astype(float) for x in ("Open", "High", "Low", "Close", "Volume"))
    fwd = (c.shift(-20) / c - 1) * 100; p = lambda x: x.shift(1)
    s20, s50, s200, sd = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean(), c.rolling(20).std()
    d = c.diff(); rsi = 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean() / (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean())
    mac = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean(); sg = mac.ewm(span=9, adjust=False).mean()
    tr = pd.concat([h - l, (h - p(c)).abs(), (l - p(c)).abs()], axis=1).max(axis=1); av = p(v).rolling(20).mean()
    body, rng = (c - o).abs(), (h - l); top, bot = pd.concat([o, c], axis=1).max(axis=1), pd.concat([o, c], axis=1).min(axis=1)
    fell, rose = p(c) < c.shift(6) * 0.97, p(c) > c.shift(6) * 1.03; hi250 = p(h).rolling(250).max(); strong = c / c.shift(126) - 1 >= 0.3
    cross = lambda a, b: (a > b) & (p(a) <= p(b))
    S = {"（基準）任何一日買": c.notna(),
         "黃金交叉（50 日線升穿 200 日線）": cross(s50, s200), "死亡交叉（50 日線跌穿 200 日線）": cross(s200, s50),
         "升穿 200 日線": cross(c, s200), "跌穿 200 日線": cross(s200, c),
         "RSI 跌穿 30（超賣）": cross(30 - rsi, 0 * rsi), "RSI 升穿 70（超買）": cross(rsi, 70 + 0 * rsi),
         "MACD 黃金交叉": cross(mac, sg), "MACD 死亡交叉": cross(sg, mac),
         "跌穿布林下軌": cross(s20 - 2 * sd, c), "升穿布林上軌": cross(c, s20 + 2 * sd),
         "20 日新高＋成交 1.3 倍（突破）": (c > p(h).rolling(20).max()) & (v > 1.3 * av), "52 週新高": (c > hi250) & (p(c) <= p(hi250)),
         "半年升咗 30% 以上（強勢股）": strong & ~p(strong).fillna(False).astype(bool), "波幅收縮＋貼近 52 週高位": (tr.rolling(10).mean() < 0.7 * tr.rolling(60).mean()) & (c >= 0.95 * hi250),
         "跳空高開 ≥4% 兼成交 2 倍": (o >= p(c) * 1.04) & (v > 2 * av) & (c >= o), "連跌 3 日": (c < p(c)) & (p(c) < c.shift(2)) & (c.shift(2) < c.shift(3)),
         "多頭吞噬（跌咗一段之後）": fell & (p(c) < p(o)) & (c > o) & (o <= p(c)) & (c >= p(o)), "空頭吞噬（升咗一段之後）": rose & (p(c) > p(o)) & (c < o) & (o >= p(c)) & (c <= p(o)),
         "錘子線（跌咗一段之後）": fell & (bot - l >= 2 * body) & (h - top <= 0.2 * rng) & (rng > 0), "流星線（升咗一段之後）": rose & (h - top >= 2 * body) & (bot - l <= 0.2 * rng) & (rng > 0)}
    ok = fwd.notna() & (df.index >= start) & (pd.Series(range(len(df)), index=df.index) >= 250)
    return {k: (df.index[(m.fillna(False).astype(bool) & ok).values], fwd[(m.fillna(False).astype(bool) & ok)].values) for k, m in S.items()}

def summarise_study(m, acc):
    base = acc.get("（基準）任何一日買")
    if not base or not len(base[0]): return []
    bd = pd.DatetimeIndex(base[0]); bv = pd.Series(base[1]); mid = bd.min() + (bd.max() - bd.min()) / 2
    b0, b1, b2 = bv.mean(), bv[(bd < mid)].mean(), bv[(bd >= mid)].mean(); rows = []
    for k, (ds, vs) in acc.items():
        ds = pd.DatetimeIndex(ds); vs = pd.Series(vs)
        if len(vs) < 30: continue
        h1, h2 = vs[(ds < mid)], vs[(ds >= mid)]; f = lambda x: None if pd.isna(x) else round(float(x), 2)
        rows.append(dict(m=m, sig=k, n=int(len(vs)), avg=f(vs.mean()), win=round(float((vs > 0).mean() * 100)), ex=f(vs.mean() - b0), ex1=f(h1.mean() - b1) if len(h1) >= 15 else None, ex2=f(h2.mean() - b2) if len(h2) >= 15 else None))
    return sorted(rows, key=lambda r: -(r["ex"] or 0))

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
    start = pd.Timestamp(dt.date.today()) - pd.DateOffset(years=years); trades = []; grid = []; acc = {}; done = 0
    for i in range(0, len(codes), 50):
        batch = [tick(c) for c in codes[i:i + 50]]; data = None
        for attempt in range(3):
            try: data = yf.download(batch, period=f"{years + 1}y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False); break
            except Exception as e: print("batch failed", attempt, e); time.sleep(20 * (attempt + 1))
        if data is None or data.empty: continue
        for t in batch:
            try:
                d1 = naive(data[t] if len(batch) > 1 else data); tr, gr = simulate(d1, m, regime, start); trades += tr; grid += gr; done += 1
                for k, (ds, vs) in study_one(d1, start).items():
                    a0 = acc.setdefault(k, ([], [])); a0[0].extend(ds); a0[1].extend(vs)
            except Exception as e: print("skip", t, e)
        print(f"{m} {min(i + 50, len(codes))}/{len(codes)} stocks, {len(trades)} trades", flush=True)
    return done, trades, grid, summarise_study(m, acc)

if __name__ == "__main__":
    markets = sys.argv[1:] or ["HK", "US"]; N = int(os.environ.get("BT_N", 150)); Y = int(os.environ.get("BT_YEARS", 4))
    rows, grid, study, total = [], [], [], 0
    for m in markets:
        done, trades, gr, st = run(m, N, Y); total += done; rows += summarise(m, trades); grid += summarise_grid(m, gr); study += st
    if not rows: sys.exit("no trades — data download failed?")
    out = {"generated": dt.datetime.now(scanner.HKT).isoformat(timespec="minutes"), "period": f"過去 {Y} 年", "stocks": total, "params": scanner.P, "rows": rows, "grid": grid, "study": study, "fee": {"perTrade": scanner.prm("fee", 2), "capital": scanner.prm("btCapital", 10000)},
           "note": "只測而家成交最多嘅股票（有倖存者偏差，結果會偏樂觀）；第二日開市價入；已扣手續費（每次買或賣 2 蚊，假設本金 10,000、每單最多蝕 1%），未計滑價。R = 賺蝕除以入場到止蝕嘅距離。少過 30 單嘅分組唔好當真。"}
    (ROOT / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for r in rows: print(r)
