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
MIN_TURNOVER = {"HK": 2e7, "US": 5e7}
MIN_PRICE = {"HK": 1.0, "US": 5.0}

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
            tabs = pd.read_html(io.StringIO(requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=UA, timeout=60).text))
            df = tabs[0]; codes = {str(s).replace(".", "-"): str(n)[:24] for s, n in zip(df["Symbol"], df["Security"])}
            try:
                t2 = pd.read_html(io.StringIO(requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=UA, timeout=60).text))
                for t in t2:
                    if "Ticker" in t.columns:
                        for s, n in zip(t["Ticker"], t.iloc[:, 0]): codes.setdefault(str(s).replace(".", "-"), str(n)[:24])
            except Exception: pass
            if len(codes) > 300: return codes
    except Exception as e:
        print("universe failed", m, e)
    try: return {x["code"]: x.get("name", "") for x in json.loads(out_path.read_text()).get("all", [])}
    except Exception: return {}

def sma(s, n): return s.rolling(n).mean()

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
    entry = last; stop = max(lo10 - 0.2 * atr, entry - prm("stopATR", 2.5) * atr)
    target = hi60 if hi60 > entry * 1.03 else entry + prm("targetMult", 2.2) * (entry - stop)
    risk = entry - stop; riskP = risk / entry * 100; rewP = (target - entry) / entry * 100; rr = rewP / riskP if riskP > 0 else 0
    lamp, word, why, setup = "🟡", "等", [], "none"
    over = chg > prm("overATR", 2) * atrP; band = prm("pullBand", 0.03)
    pullback = trend == "up" and s20 * (1 - band) <= entry <= s20 * (1 + band); breakout = last > hi20 and volR > prm("volRMin", 1.3)
    if trend == "down": lamp, word, setup = "🔴", "唔做", "down"; why.append("股價喺 20 日線同 50 日線之下，趨勢向下。")
    elif over: word, setup = "等回調", "over"; why.append(f"今日升 {chg:+.1f}%，超過兩個 ATR，追入 R:R 變差。")
    elif breakout: lamp, word, setup = "🟢", "突破留意", "breakout"; why.append(f"收市高過 20 日高位，成交係平時 {volR:.1f} 倍。")
    elif pullback: lamp, word, setup = "🟢", "回調企穩", "pullback"; why.append("上升趨勢中回到 20 日線附近，係較好嘅入場區。")
    elif trend == "up": setup = "uptrend"; why.append("趨勢向上但未有突破或回調訊號。")
    else: why.append("橫行，方向未清。")
    if rr < prm("rrMin", 1.5) and lamp == "🟢": lamp, word = "🟡", "R:R 唔夠"; why.append(f"回報 {rewP:+.1f}% 對風險 −{riskP:.1f}%，唔夠 {prm('rrMin',1.5)}:1，降級。")
    if volR < 0.6: why.append("成交淡靜。")
    zone = [s20 * 0.98, s20 * 1.02] if setup in ("pullback", "uptrend") else [hi20, hi20 * 1.02] if setup == "breakout" else [s20, max(s20, entry - atr)] if setup == "over" else None
    lt = ("🟢" if last > s200 and s50 > s200 else "🔴" if last < s200 and s50 < s200 else "🟡") if s200 else "⚪"
    recent = [{"d": str(i.date()), "h": round(float(r.High), 3), "l": round(float(r.Low), 3), "c": round(float(r.Close), 3)} for i, r in df.tail(8).iterrows()]
    R = lambda x: None if x is None else round(x, 3)
    return dict(lamp=lamp, word=word, why=" ".join(why), setup=setup, entry=R(entry), stop=R(stop), target=R(target), t1=R(entry + 1.5 * risk), t2=R(target),
                zone=[R(zone[0]), R(zone[1])] if zone else None, rr=round(rr, 2), riskP=round(riskP, 2), rewP=round(rewP, 2), chg=round(chg, 2), trend=trend,
                volR=round(volR, 2), atrP=round(atrP, 2), s20=R(s20), s50=R(s50), s200=R(s200), hi20=R(hi20), turnover=round(turnover / 1e6, 1), aboveS20=bool(last > s20),
                tf={"today": "🟢" if chg > 1 else "🔴" if chg < -1 else "🟡", "mid": "🟢" if trend == "up" else "🔴" if trend == "down" else "🟡", "long": lt},
                date=str(df.index[-1].date()), recent=recent)

def index_line(sym):
    try:
        h = yf.Ticker(sym).history(period="10d")["Close"].dropna(); last, prev = float(h.iloc[-1]), float(h.iloc[-2]); d = (last - prev) / prev * 100
        return {"close": round(last, 2), "chg": round(d, 2), "lamp": "🟢" if d > 0.5 else "🔴" if d < -0.5 else "🟡"}
    except Exception: return None

def run(m):
    codes = universe(m); print(m, "universe:", len(codes))
    tick = lambda c: f"{int(c):04d}.HK" if m == "HK" else c
    tickers = [tick(c) for c in codes]; results = {}
    for i in range(0, len(tickers), 200):
        batch = tickers[i:i + 200]
        try: data = yf.download(batch, period="1y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
        except Exception as e: print("batch failed", e); continue
        for t in batch:
            try:
                df = data[t] if len(batch) > 1 else data; a = analyse(df, m)
                if a:
                    code = f"{int(t.split('.')[0]):04d}" if m == "HK" else t
                    a["code"] = code; a["name"] = codes.get(code, ""); results[code] = a
            except Exception: pass
        print(f"{min(i+200, len(tickers))}/{len(tickers)} scanned, {len(results)} liquid")
    rows = list(results.values()); above = sum(1 for r in rows if r["aboveS20"]); p = above / len(rows) if rows else 0
    idx = {"HK": [("^HSI", "恒指"), ("^HSTECH", "科指")], "US": [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq")]}[m]
    out = {"market": m, "date": rows[0]["date"] if rows else str(dt.date.today()), "generated": dt.datetime.now(HKT).isoformat(timespec="minutes"),
           "indices": [{"sym": s, "name": n, **(index_line(s) or {})} for s, n in idx],
           "breadth": {"above20": above, "total": len(rows), "lamp": "🟢" if p >= .6 else "🔴" if p <= .4 else "🟡"}, "all": rows}
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
