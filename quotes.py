#!/usr/bin/env python3
"""交易時段每 15 分鐘：攞最新價（Yahoo 延遲 15 分），寫 quotes.json，同時 check 提醒同真實／paper 單，有事推通知。"""
import json, datetime as dt
from pathlib import Path
import yfinance as yf
from notify import send

ROOT = Path(__file__).parent
HKT = dt.timezone(dt.timedelta(hours=8))
def load(p, d):
    try: return json.loads((ROOT / p).read_text())
    except Exception: return d
tick = lambda m, c: f"{int(c):04d}.HK" if m == "HK" else c

def main():
    now = dt.datetime.now(HKT)
    want = {"HK": set(), "US": set()}
    for m in ("HK", "US"):
        sig = load(f"signals_{m}.json", {})
        rows = sorted(sig.get("all", []), key=lambda r: (r["lamp"] != "🟢", r.get("word") != "等回調", -r.get("rr", 0)))
        for r in rows[:60]: want[m].add(r["code"])
    for t in load("trades.json", []):
        if t.get("status", "open") == "open": want[t.get("m", "HK")].add(str(t["code"]))
    for a in load("alerts.json", []): want[a.get("m", "HK")].add(str(a["code"]))
    for e in load("real.json", []):
        if e.get("sell") is None: want[e.get("m", "HK")].add(str(e["code"]))
    q = {"at": now.isoformat(timespec="minutes"), "HK": {}, "US": {}}
    for m in ("HK", "US"):
        syms = [tick(m, c) for c in want[m]] + (["^HSI", "^HSTECH"] if m == "HK" else ["^GSPC", "^IXIC"])
        if not syms: continue
        data = yf.download(syms, period="5d", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
        for s in syms:
            try:
                df = (data[s] if len(syms) > 1 else data).dropna(subset=["Close"])
                last, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
                key = s if s.startswith("^") else (f"{int(s.split('.')[0]):04d}" if m == "HK" else s)
                q[m][key] = {"p": round(last, 3), "prev": round(prev, 3), "chg": round((last - prev) / prev * 100, 2), "h": round(float(df["High"].iloc[-1]), 3), "l": round(float(df["Low"].iloc[-1]), 3), "d": str(df.index[-1].date())}
            except Exception: pass
    (ROOT / "quotes.json").write_text(json.dumps(q, ensure_ascii=False))
    # intraday 5m bars for day-trading tab: top signals + open positions + alerts
    intra = {"at": now.isoformat(timespec="minutes"), "HK": {}, "US": {}}
    tzs = {"HK": "Asia/Hong_Kong", "US": "America/New_York"}
    for m in ("HK", "US"):
        sig = load(f"signals_{m}.json", {})
        rows = sorted(sig.get("all", []), key=lambda r: (r["lamp"] != "🟢", -r.get("volR", 0)))
        codes = list(dict.fromkeys([r["code"] for r in rows[:24]] + [c for c in want[m] if c not in {r["code"] for r in rows[:24]}]))[:40]
        syms = [tick(m, c) for c in codes]
        if not syms: continue
        try:
            d5 = yf.download(syms, period="5d", interval="5m", group_by="ticker", auto_adjust=False, threads=True, progress=False)
        except Exception as e:
            print("intraday failed", e); continue
        for s in syms:
            try:
                df = (d5[s] if len(syms) > 1 else d5).dropna(subset=["Close"])
                if df.empty: continue
                idx = df.index.tz_convert(tzs[m]) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(tzs[m])
                days = idx.strftime("%Y-%m-%d"); today = days[-1]
                tb = df[days == today]; pb = df[days != today]
                pbv = float(pb["Volume"].mean()) if len(pb) else 0.0
                bars = [[int(i.timestamp()), round(float(r.Open), 3), round(float(r.High), 3), round(float(r.Low), 3), round(float(r.Close), 3), int(r.Volume or 0)] for i, r in tb.iterrows()]
                code = f"{int(s.split('.')[0]):04d}" if m == "HK" else s
                intra[m][code] = {"d": today, "pbv": round(pbv), "bars": bars}
            except Exception: pass
    (ROOT / "intraday.json").write_text(json.dumps(intra, ensure_ascii=False))
    # alerts + trades
    state = load("state.json", {}); hit = state.setdefault("alerts_hit", []); lines = []
    for a in load("alerts.json", []):
        m = a.get("m", "HK"); key = f'{m}|{a["code"]}|{a["dir"]}|{a["price"]}'; qq = q[m].get(str(a["code"]))
        if key in hit or not qq: continue
        if (a["dir"] == "up" and qq["p"] >= float(a["price"])) or (a["dir"] == "down" and qq["p"] <= float(a["price"])):
            lines.append(f'🔔 {a["code"]} {a.get("name","")} {"升到" if a["dir"]=="up" else "跌到"} ${a["price"]}　現價 ${qq["p"]}' + (f'　· {a["note"]}' if a.get("note") else "")); hit.append(key)
    done = state.setdefault("trades_done", [])
    for t in load("trades.json", []) + [dict(e, entry=e["buy"], real=True) for e in load("real.json", []) if e.get("sell") is None and e.get("stop")]:
        if t.get("status", "open") != "open": continue
        m = t.get("m", "HK"); qq = q[m].get(str(t["code"])); key = f'{m}|{t["code"]}|{t.get("entry")}'
        if not qq or key in done: continue
        if qq["p"] <= float(t["stop"]): lines.append(f'🛑 {"真倉" if t.get("real") else "Paper"} {t["code"]} {t.get("name","")} 跌穿止蝕 ${t["stop"]}，現價 ${qq["p"]}。規則係走。'); done.append(key)
        elif qq["p"] >= float(t["target"]): lines.append(f'🎯 {"真倉" if t.get("real") else "Paper"} {t["code"]} {t.get("name","")} 到咗目標 ${t["target"]}，現價 ${qq["p"]}。'); done.append(key)
    (ROOT / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))
    if lines: send(f"📊 決策卡 {now:%m/%d %H:%M}\n" + "\n".join(lines))
    else: print("no alerts")

if __name__ == "__main__":
    main()
