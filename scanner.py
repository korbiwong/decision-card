#!/usr/bin/env python3
"""全市場掃描（港股 + 美股）。收市後跑，寫 signals_HK.json / signals_US.json 畀網頁用。"""
import io, json, os, sys, datetime as dt
from pathlib import Path
import pandas as pd, requests, yfinance as yf

ROOT = Path(__file__).parent
HKT = dt.timezone(dt.timedelta(hours=8))
P = json.loads((ROOT / "params.json").read_text()) if (ROOT / "params.json").exists() else {}
def prm(k, d): return P.get(k, d)

HKEX_LIST = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
# 盡量多分析：港股日均成交 ≥ HK$5M、股價 ≥ $0.5；美股日均成交 ≥ US$20M、股價 ≥ $3（再低就太難買賣）
MIN_TURNOVER = {"HK": 5e6, "US": 2e7}
MIN_PRICE = {"HK": 0.5, "US": 3.0}
import time

def universe(m):
    out_path = ROOT / f"signals_{m}.json"
    try:
        if m == "HK":
            r = requests.get(HKEX_LIST, timeout=60, headers={"User-Agent": "Mozilla/5.0"}); r.raise_for_status()
            df = pd.read_excel(io.BytesIO(r.content), header=2); df.columns = [str(c).strip() for c in df.columns]
            code_col = next(c for c in df.columns if "Stock Code" in c); name_col = next(c for c in df.columns if "Name" in c); cat_col = next(c for c in df.columns if "Category" in c)
            eq = df[df[cat_col].astype(str).str.strip().str.lower() == "equity"]
            codes = {}
            for c, n in zip(eq[code_col], eq[name_col]):
                try: ci = int(c)
                except Exception: continue
                if 1 <= ci <= 9999: codes[f"{ci:04d}"] = str(n).strip()[:24]
            if len(codes) > 500: return codes
        else:
            UA={"User-Agent":"Mozilla/5.0 (decision-card scanner)"}
            codes = {}
            # S&P 500 / 400（中型）/ 600（細型）
            for page in ["List_of_S%26P_500_companies", "List_of_S%26P_400_companies", "List_of_S%26P_600_companies"]:
                try:
                    tabs = pd.read_html(io.StringIO(requests.get("https://en.wikipedia.org/wiki/" + page, headers=UA, timeout=60).text))
                    for t in tabs:
                        sc = next((c for c in t.columns if str(c) in ("Symbol", "Ticker symbol", "Ticker")), None)
                        nc = next((c for c in t.columns if str(c) in ("Security", "Company")), None)
                        if sc is None or nc is None: continue
                        for s_, n in zip(t[sc], t[nc]): codes.setdefault(str(s_).strip().replace(".", "-"), str(n)[:24])
                        break
                except Exception as e: print("wiki failed", page, e)
            try:
                t2 = pd.read_html(io.StringIO(requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=UA, timeout=60).text))
                for t in t2:
                    if "Ticker" in t.columns:
                        for s_, n in zip(t["Ticker"], t.iloc[:, 0]): codes.setdefault(str(s_).replace(".", "-"), str(n)[:24])
            except Exception: pass
            # 全美國上市普通股（Nasdaq 交易所目錄，包埋 NYSE），之後用成交額過濾
            try:
                txt = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt", headers=UA, timeout=60).text
                lines = txt.strip().splitlines(); hdr = lines[0].split("|")
                iS, iN, iE, iT = hdr.index("Symbol"), hdr.index("Security Name"), hdr.index("ETF"), hdr.index("Test Issue")
                bad = ("warrant", " unit", " right", "preferred", "notes due", "debenture", "%", "depositary share representing")
                for ln in lines[1:]:
                    p = ln.split("|")
                    if len(p) <= max(iS, iN, iE, iT) or p[iE] != "N" or p[iT] != "N": continue
                    sym, nm = p[iS].strip(), p[iN].strip()
                    if not sym or not sym.isalpha() or len(sym) > 5 or any(b in nm.lower() for b in bad): continue
                    codes.setdefault(sym, nm.split(" - ")[0][:24])
            except Exception as e: print("nasdaqtraded failed", e)
            print("US raw universe", len(codes))
            if len(codes) > 300: return codes
    except Exception as e:
        print("universe failed", m, e)
    try: return {x["code"]: x.get("name", "") for x in json.loads(out_path.read_text()).get("all", [])}
    except Exception: return {}

def sma(s, n): return s.rolling(n).mean()

def candle(df, atr, s20, s50):
    """K 線型態 + 位置。型態只係話你知發生咗咩事；位置（有冇支撐／係咪高位）先決定值唔值得理。
    回傳 {n 名, dir bull/bear, ok 位置啱唔啱, loc 位置描述, low 型態低位}，冇型態回 None。"""
    n = len(df)
    if n < 30 or atr <= 0: return None
    o, h, l, c = (df[x].astype(float).values for x in ("Open", "High", "Low", "Close"))
    if any(x != x for x in o[-3:]): return None
    body = lambda i: abs(c[i] - o[i]); rng = lambda i: h[i] - l[i]
    top = lambda i: max(o[i], c[i]); bot = lambda i: min(o[i], c[i])
    green = lambda i: c[i] > o[i]; red = lambda i: c[i] < o[i]
    big = lambda i: body(i) >= 0.7 * atr and body(i) >= 0.5 * rng(i)
    hammer = lambda i: rng(i) >= 0.6 * atr and body(i) <= 0.35 * rng(i) and bot(i) - l[i] >= 2 * body(i) and h[i] - top(i) <= 0.2 * rng(i)
    invham = lambda i: rng(i) >= 0.6 * atr and body(i) <= 0.35 * rng(i) and h[i] - top(i) >= 2 * body(i) and bot(i) - l[i] <= 0.2 * rng(i)
    a, b, z = n - 3, n - 2, n - 1   # 最近三支
    def ctx(k):   # 型態之前係咪跌咗／升咗一大段
        st = n - k; pre_h, pre_l = h[max(0, st - 8):st], l[max(0, st - 8):st]
        klo, khi = l[st:].min(), h[st:].max(); m = prm("kMoveATR", 2.5) * atr
        fell = len(pre_h) > 0 and pre_h.max() - klo >= m and klo <= l[max(0, st - 20):st].min() + 0.5 * atr
        rose = len(pre_l) > 0 and khi - pre_l.min() >= m and khi >= h[max(0, st - 20):st].max()
        return fell, rose, klo, khi
    cands = []   # (名, 方向, 用幾多支 K)；次序 = 優先次序
    if big(a) and red(a) and body(b) <= 0.4 * body(a) and top(b) <= bot(a) + 0.25 * body(a) and green(z) and c[z] > (o[a] + c[a]) / 2: cands.append(("晨星", "bull", 3))
    if big(a) and green(a) and body(b) <= 0.4 * body(a) and bot(b) >= top(a) - 0.25 * body(a) and red(z) and c[z] < (o[a] + c[a]) / 2: cands.append(("夜星", "bear", 3))
    if red(b) and green(z) and o[z] <= c[b] and c[z] >= o[b] and body(z) > body(b) and body(z) >= 0.5 * atr: cands.append(("多頭吞噬", "bull", 2))
    if green(b) and red(z) and o[z] >= c[b] and c[z] <= o[b] and body(z) > body(b) and body(z) >= 0.5 * atr: cands.append(("空頭吞噬", "bear", 2))
    if red(b) and green(z) and abs(l[b] - l[z]) <= 0.1 * atr and min(bot(b) - l[b], bot(z) - l[z]) >= 0.25 * atr: cands.append(("平底", "bull", 2))
    if big(b) and top(z) <= top(b) and bot(z) >= bot(b) and body(z) <= 0.5 * body(b): cands.append(("母子線", "bull" if red(b) else "bear", 2))
    if hammer(z): cands += [("錘子線", "bull", 1), ("吊人線", "bear", 1)]
    if invham(z): cands += [("倒錘子", "bull", 1), ("流星線", "bear", 1)]
    for name, d, k in cands:
        fell, rose, klo, khi = ctx(k)
        if d == "bull" and fell:
            st = n - k; loc = None
            # 前低支撐：之前有個彈過（≥2 ATR）嘅波段低位，而家跌返落嚟喺附近守住
            for j in range(st - 5, max(5, n - 120), -1):
                if l[j] == l[j - 5:j + 6].min() and h[j + 1:st].max() - l[j] >= 2 * atr and l[j] - 0.5 * atr <= klo <= l[j] + 0.75 * atr and c[z] >= l[j]:
                    loc = f"前低 {l[j]:.2f}"; break
            if not loc and s50 and s20 > s50 and klo <= s50 * 1.01 and c[z] >= s50: loc = "50 日線"
            if not loc and s50 and s20 > s50 and klo <= s20 * 1.01 and c[z] >= s20: loc = "20 日線"
            return dict(n=name, dir="bull", ok=bool(loc), loc=loc or "下跌半路", low=float(klo))
        if d == "bear" and rose: return dict(n=name, dir="bear", ok=True, loc="升咗一大段嘅高位", low=float(klo))
    return None

def chart_read(df, last, atr, s20, s50, s200, hi20):
    """像專業人士咁讀圖：週線趨勢、動能（RSI）、最近嘅支撐同阻力。情境（多／中性／空）由網頁用呢啲價位砌出嚟。"""
    c, h, l = df["Close"].astype(float), df["High"].astype(float).values, df["Low"].astype(float).values; n = len(df)
    wk = None
    try:
        w = c.resample("W-FRI").last().dropna()
        if len(w) >= 30:
            w10, w30 = float(w.tail(10).mean()), float(w.tail(30).mean()); wl = float(w.iloc[-1])
            wk = "up" if wl > w10 > w30 else "down" if wl < w10 < w30 else "side"
    except Exception: pass
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] > 0 else 100.0
    sups, ress = [], []   # 波段高低位（前後 5 日入面最高／最低）
    for j in range(max(5, n - 120), n - 5):
        if l[j] == l[j - 5:j + 6].min() and l[j] < last - 0.25 * atr: sups.append((float(l[j]), "前低"))
        if h[j] == h[j - 5:j + 6].max() and h[j] > last + 0.25 * atr: ress.append((float(h[j]), "前高"))
    for v, lbl in ((s20, "20 日線"), (s50, "50 日線"), (s200, "200 日線")):
        if v and v < last - 0.25 * atr: sups.append((v, lbl))
        if v and v > last + 0.25 * atr: ress.append((v, lbl))
    if hi20 > last + 0.25 * atr: ress.append((hi20, "20 日高位"))
    elif last > hi20: sups.append((hi20, "突破位（之前嘅平台頂）"))   # 突破咗：之前嘅阻力變支撐
    sup = max(sups) if sups else None; res = min(ress) if ress else None
    f = lambda x: {"p": round(x[0], 3), "lbl": x[1]} if x else None
    return wk, round(rsi, 1), f(sup), f(res)

def analyse(df, m):
    df = df.dropna(subset=["Close"]).copy()
    if len(df) < 55: return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    if last < MIN_PRICE[m]: return None
    turnover = float((c * v).tail(20).mean())
    if not turnover or turnover < MIN_TURNOVER[m]: return None
    chg = (last - prev) / prev * 100
    s20, s50 = float(sma(c, 20).iloc[-1]), float(sma(c, 50).iloc[-1])
    s200 = float(sma(c, 200).iloc[-1]) if len(df) >= 200 else None
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) or float(h.iloc[-1] - l.iloc[-1]); atrP = atr / last * 100
    hi20 = float(h.iloc[-21:-1].max()); lo10 = float(l.tail(int(prm("lookLow", 10))).min()); hi60 = float(h.tail(60).max())
    av20 = float(v.iloc[:-1].tail(20).mean()) or float(v.iloc[-1]); volR = float(v.iloc[-1]) / av20 if av20 else 1.0
    trend = "up" if last > s20 > s50 else "down" if last < s20 < s50 else "side"
    # 止蝕：backtest 2022–26 顯示 3 ATR 比 2.5 好；再窄（2 ATR、當日低位）反而更差
    entry = last; stop = max(lo10 - 0.2 * atr, entry - prm("stopATR", 3.0) * atr)
    target = hi60 if hi60 > entry * 1.03 else entry + prm("targetMult", 2.2) * (entry - stop)
    risk = entry - stop; riskP = risk / entry * 100; rewP = (target - entry) / entry * 100; rr = rewP / riskP if riskP > 0 else 0
    lamp, word, why, setup = "🟡", "等", [], "none"
    over = chg > prm("overATR", 2) * atrP; band = prm("pullBand", 0.03)
    pullback = trend == "up" and s20 * (1 - band) <= entry <= s20 * (1 + band); breakout = last > hi20 and volR > prm("volRMin", 1.3)
    chased = breakout and last > hi20 * (1 + prm("chaseMax", 0.05))  # 過咗突破位 5% 唔追
    if trend == "down": lamp, word, setup = "🔴", "唔做", "down"; why.append("股價喺 20 日線同 50 日線之下，趨勢向下。")
    elif over: word, setup = "等回調", "over"; why.append(f"今日升 {chg:+.1f}%，超過兩個 ATR，追入 R:R 變差。")
    elif chased: word, setup = "等回調", "over"; why.append(f"突破咗，但已經高過 20 日高位 {(last / hi20 - 1) * 100:.0f}%，唔追。")
    elif breakout: lamp, word, setup = "🟢", "突破留意", "breakout"; why.append(f"收市高過 20 日高位，成交係平時 {volR:.1f} 倍。")
    # 回調入市：backtest 4 年港美股都冇優勢（PF ≈ 1.0），所以只觀察、唔出 🟢
    elif pullback: word, setup = "回調（觀察）", "pullback"; why.append("上升趨勢中回到 20 日線附近。回測顯示回調入市冇優勢，等佢再突破先做。")
    elif trend == "up": setup = "uptrend"; why.append("趨勢向上但未有突破或回調訊號。")
    else: why.append("橫行，方向未清。")
    # K 線型態（位置啱先用）：支撐位見反轉 K → 可以入，止蝕放型態低位；突破日見頂部 K → 降級；下跌半路 → 忽略
    k = candle(df, atr, s20, s50) if prm("candle", True) else None
    if k and k["dir"] == "bull":
        if not k["ok"]: why.append(f"見到「{k['n']}」，但喺下跌半路、附近冇支撐，直接忽略。")
        elif trend == "down": why.append(f"「{k['n']}」出現喺{k['loc']}支撐，但趨勢仲向下，等企返 20 日線先算。")
        elif setup in ("pullback", "uptrend", "none") and not (s200 and last < s200 and s50 < s200):
            setup = "reversal"; stop = min(max(k["low"] - 0.2 * atr, entry - prm("stopATR", 3.0) * atr), entry - 0.5 * atr)
            target = hi60 if hi60 > entry * 1.03 else entry + prm("targetMult", 2.2) * (entry - stop)
            risk = entry - stop; riskP = risk / entry * 100; rewP = (target - entry) / entry * 100; rr = rewP / riskP if riskP > 0 else 0
            why = [f"跌到 {k['loc']} 支撐位出現「{k['n']}」：有人喺呢度接貨。止蝕放型態低位下面，跌穿即係支撐失效。"]
            # 回測 4 年（港美各 150 隻）：勝率 26–29%、PF 1.01／1.13，強弱市表現唔一致 → 冇優勢，預設只觀察
            if prm("candleEntry", False): lamp, word = "🟢", "支撐反轉"
            else: word = "支撐反轉（觀察）"; why.append("回測 4 年呢類入場勝率得約 27%、賺蝕打和，所以只觀察，等佢再突破先做。")
    elif k and k["dir"] == "bear":
        # 回測 4 年：港股呢類突破 PF 0.87（正常突破 1.46）→ 降級；美股 PF 1.50（正常 1.61）仲有效 → 只提示
        if lamp == "🟢" and not prm("bearKDemote", m == "HK"): why.append(f"今日收「{k['n']}」，有賣壓跡象；回測顯示美股呢類突破仍然有效，但注碼可以細啲。")
        elif lamp == "🟢": lamp, word = "🟡", "突破但有賣壓"; why.append(f"今日收「{k['n']}」，喺{k['loc']}出現，賣壓開始出現，等下一日確認先追。")
        elif trend != "down": why.append(f"{k['loc']}出現「{k['n']}」，賣壓訊號，持貨留意、唔好加注。")
    if rr < prm("rrMin", 1.5) and lamp == "🟢": lamp, word = "🟡", "R:R 唔夠"; why.append(f"回報 {rewP:+.1f}% 對風險 −{riskP:.1f}%，唔夠 {prm('rrMin',1.5)}:1，降級。")
    if volR < 0.6: why.append("成交淡靜。")
    zone = [max(k["low"], entry - atr), entry] if setup == "reversal" else [s20 * 0.98, s20 * 1.02] if setup in ("pullback", "uptrend") else [hi20, hi20 * 1.02] if setup == "breakout" else [s20, max(s20, entry - atr)] if setup == "over" else None
    lt = ("🟢" if last > s200 and s50 > s200 else "🔴" if last < s200 and s50 < s200 else "🟡") if s200 else "⚪"
    wk, rsi, sup, res = chart_read(df, last, atr, s20, s50, s200, hi20)
    # 離支撐（平台）幾遠 = 最多輸幾多：止蝕放喺支撐下面少少，跌穿就認錯；上面空間冇封頂
    supStop = sup["p"] - 0.2 * atr if sup else None
    supDist = (last - sup["p"]) / last * 100 if sup else None; supRisk = (last - supStop) / last * 100 if sup else None
    recent = [{"d": str(i.date()), "h": round(float(r.High), 3), "l": round(float(r.Low), 3), "c": round(float(r.Close), 3)} for i, r in df.tail(8).iterrows()]
    R = lambda x: None if x is None else round(x, 3)
    return dict(lamp=lamp, word=word, why=" ".join(why), setup=setup, entry=R(entry), stop=R(stop), target=R(target), t1=R(entry + 1.5 * risk), t2=R(target),
                zone=[R(zone[0]), R(zone[1])] if zone else None, rr=round(rr, 2), riskP=round(riskP, 2), rewP=round(rewP, 2), chg=round(chg, 2), trend=trend,
                volR=round(volR, 2), atrP=round(atrP, 2), s20=R(s20), s50=R(s50), s200=R(s200), hi20=R(hi20), turnover=round(turnover / 1e6, 1), aboveS20=bool(last > s20),
                tf={"today": "🟢" if chg > 1 else "🔴" if chg < -1 else "🟡", "mid": "🟢" if trend == "up" else "🔴" if trend == "down" else "🟡", "long": lt, "week": {"up": "🟢", "down": "🔴", "side": "🟡"}.get(wk, "⚪")}, rsi=rsi, sup=sup, res=res, supStop=R(supStop), supDist=R(supDist), supRisk=R(supRisk),
                date=str(df.index[-1].date()), recent=recent, k=({"n": k["n"], "dir": k["dir"], "ok": k["ok"], "loc": k["loc"]} if k else None))

def index_line(sym):
    try:
        h = yf.Ticker("HSTECH.HK" if sym == "^HSTECH" else sym).history(period="10d")["Close"].dropna(); last, prev = float(h.iloc[-1]), float(h.iloc[-2]); d = (last - prev) / prev * 100
        return {"close": round(last, 2), "chg": round(d, 2), "lamp": "🟢" if d > 0.5 else "🔴" if d < -0.5 else "🟡"}
    except Exception: return None

def index_trend(sym):
    """指數收市高過 50 日線，而且 50 日線比 10 日前高 → True。攞唔到數據 → None（唔過濾）。"""
    try:
        c = yf.Ticker(sym).history(period="6mo")["Close"].dropna(); s50 = c.rolling(50).mean()
        return bool(c.iloc[-1] > s50.iloc[-1] and s50.iloc[-1] > s50.iloc[-11])
    except Exception: return None

def run(m):
    codes = universe(m); print(m, "universe:", len(codes))
    tick = lambda c: f"{int(c):04d}.HK" if m == "HK" else c
    tickers = [tick(c) for c in codes]; results = {}
    for i in range(0, len(tickers), 200):
        batch = tickers[i:i + 200]
        data = None
        for attempt in range(3):
            try: data = yf.download(batch, period="1y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False); break
            except Exception as e: print("batch failed", attempt, e); time.sleep(20 * (attempt + 1))
        if data is None or data.empty: continue
        time.sleep(1)
        # Yahoo 嘅 1 年數據有時會遲幾日，用最近 5 日數據補返最新幾支日線
        try: fresh = yf.download(batch, period="5d", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
        except Exception as e: print("fresh failed", e); fresh = None
        for t in batch:
            try:
                df = data[t] if len(batch) > 1 else data
                if fresh is not None:
                    f5 = fresh[t] if len(batch) > 1 else fresh
                    def naive(x):
                        x = x.copy(); x.index = pd.to_datetime(x.index); 
                        if x.index.tz is not None: x.index = x.index.tz_localize(None)
                        x.index = x.index.normalize(); return x
                    df = pd.concat([naive(df), naive(f5.dropna(subset=["Close"]))]); df = df[~df.index.duplicated(keep="last")].sort_index()
                a = analyse(df, m)
                if a:
                    code = f"{int(t.split('.')[0]):04d}" if m == "HK" else t
                    a["code"] = code; a["name"] = codes.get(code, ""); results[code] = a
            except Exception: pass
        print(f"{min(i+200, len(tickers))}/{len(tickers)} scanned, {len(results)} liquid")
    rows = list(results.values()); above = sum(1 for r in rows if r["aboveS20"]); p = above / len(rows) if rows else 0
    # 大市過濾（O'Neil／「先睇大環境」）：指數企喺上升緊嘅 50 日線上，或者 ≥55% 股票喺 20 日線上，先出 🟢
    idx_up = index_trend("^HSI" if m == "HK" else "^GSPC")
    mkt_ok = idx_up is None or idx_up or p >= prm("breadthMin", 0.55)
    if prm("marketFilter", True) and not mkt_ok:
        for r in rows:
            if r["lamp"] == "🟢":
                r["lamp"], r["word"] = "🟡", "大市弱"
                r["why"] += " 大市弱（指數喺 50 日線下、多數股票喺 20 日線下），回測顯示呢種時候突破多數失敗，唔做。"
    idx = {"HK": [("^HSI", "恒指"), ("^HSTECH", "科指")], "US": [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq")]}[m]
    out = {"market": m, "date": max(r["date"] for r in rows) if rows else str(dt.date.today()), "generated": dt.datetime.now(HKT).isoformat(timespec="minutes"),
           "indices": [{"sym": s, "name": n, **(index_line(s) or {})} for s, n in idx],
           "breadth": {"above20": above, "total": len(rows), "lamp": "🟢" if p >= .6 else "🔴" if p <= .4 else "🟡"}, "regime": {"indexUp": idx_up, "ok": bool(mkt_ok)}, "all": rows}
    (ROOT / f"signals_{m}.json").write_text(json.dumps(out, ensure_ascii=False))
    green = [r for r in rows if r["lamp"] == "🟢"]; print("wrote", m, len(rows), "stocks,", len(green), "green")
    return out, green

if __name__ == "__main__":
    markets = sys.argv[1:] or ["HK", "US"]
    summary = []
    for m in markets:
        out, green = run(m)
        best = sorted(green, key=lambda r: -r["rr"])[:5]
        summary.append(f"{'🇭🇰 港股' if m=='HK' else '🇺🇸 美股'} {out['date']}：{out['breadth']['above20']}/{out['breadth']['total']} 喺 20 日線上 {out['breadth']['lamp']}，🟢 {len(green)} 隻")
        for r in best: summary.append(f"  {r['code']} {r['name'][:10]} ${r['entry']}｜🎯+{r['rewP']:.0f}%｜🛑−{r['riskP']:.1f}%｜{r['rr']:.1f}:1")
    from notify import send; send("📊 收市掃描\n" + "\n".join(summary))
