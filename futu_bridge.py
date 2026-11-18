#!/usr/bin/env python3
"""富途即時報價橋：喺你自己部電腦行，將富途 OpenD 嘅即時報價交畀決策卡網頁。

要求：
  1. 部電腦開咗富途 OpenD 並且登入咗（預設 127.0.0.1:11111）
  2. pip install futu-api
  3. python futu_bridge.py        （想試條路通唔通、未裝 OpenD：python futu_bridge.py --demo）
  4. 喺同一部電腦用 Chrome／Edge 開決策卡網頁 →「設定」→ 富途即時價撳「開」

安全：只聽 127.0.0.1（出面連唔到），只攞報價，唔會落單，唔需要任何密碼。
有冇即時價睇你富途戶口嘅行情權限；冇權限嘅市場富途會俾延遲價或者報錯。"""
import argparse, datetime as dt, json, random, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ALLOWED_ORIGINS = {"https://korbiwong.github.io", "http://localhost:8765", "http://127.0.0.1:8765"}
CACHE_SECS = 2          # 同一批股票 2 秒內唔重複問富途（富途限 30 秒 60 次）
MAX_CODES = 400         # 富途快照每次最多 400 隻

def to_futu(m, code):   # 決策卡代號 → 富途代號
    return f"HK.{int(code):05d}" if m == "HK" else "US." + code.replace("-", ".")

def from_futu(m, fcode):
    c = fcode.split(".", 1)[1]
    return f"{int(c):04d}" if m == "HK" else c.replace(".", "-")

class Futu:
    """富途 OpenD 連線。snapshot() 回傳 {代號: 報價}。"""
    def __init__(self, host, port):
        from futu import OpenQuoteContext, RET_OK
        self.ok = RET_OK; self.ctx = OpenQuoteContext(host=host, port=port); self.bad = set()
    def _rows(self, codes):
        if not codes: return []
        ret, data = self.ctx.get_market_snapshot(codes)
        if ret == self.ok: return data.to_dict("records")
        if len(codes) == 1: self.bad.add(codes[0]); print("skip", codes[0], str(data)[:80]); return []   # 呢隻富途冇／冇權限，之後唔再問
        h = len(codes) // 2; return self._rows(codes[:h]) + self._rows(codes[h:])                          # 成批失敗就拆開搵邊隻有問題
    def snapshot(self, m, codes):
        want = [to_futu(m, c) for c in codes]; want = [c for c in want if c not in self.bad]; out = {}
        for r in self._rows(want):
            try:
                p, prev = float(r["last_price"]), float(r["prev_close_price"])
                if not p or not prev: continue
                out[from_futu(m, r["code"])] = {"p": round(p, 3), "prev": round(prev, 3), "chg": round((p - prev) / prev * 100, 2), "h": round(float(r["high_price"]) or p, 3),
                                                "l": round(float(r["low_price"]) or p, 3), "d": str(r["update_time"])[:10], "lot": int(r.get("lot_size") or 1)}
            except Exception as e: print("bad row", r.get("code"), e)
        return out
    def close(self): self.ctx.close()

class Demo:
    """假報價，用嚟試網頁連唔連到呢個程式（唔使 OpenD）。"""
    def snapshot(self, m, codes):
        d = str(dt.date.today()); out = {}
        for c in codes:
            random.seed(c); prev = round(random.uniform(5, 300), 2); random.seed(); p = round(prev * (1 + random.uniform(-0.03, 0.03)), 3)
            out[c] = {"p": p, "prev": prev, "chg": round((p - prev) / prev * 100, 2), "h": round(max(p, prev) * 1.01, 3), "l": round(min(p, prev) * 0.99, 3), "d": d, "lot": 100 if m == "HK" else 1}
        return out
    def close(self): pass

class Bridge:
    def __init__(self, source): self.src = source; self.lock = threading.Lock(); self.cache = {}
    def quotes(self, m, codes):
        if m not in ("HK", "US"): return {"error": "m must be HK or US"}
        codes = [c for c in dict.fromkeys(c.strip().upper() for c in codes) if c and (c.isdigit() if m == "HK" else c.replace("-", "").isalnum())][:MAX_CODES]
        key = (m, tuple(codes))
        with self.lock:   # 富途連線一次只做一個請求
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < CACHE_SECS: return hit[1]
            try: q = self.src.snapshot(m, codes)
            except Exception as e: return {"error": f"富途攞唔到報價：{e}"}
            res = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "m": m, "q": q}
            self.cache = {key: (time.time(), res)}; return res

def make_handler(bridge):
    class H(BaseHTTPRequestHandler):
        def _cors(self):
            o = self.headers.get("Origin") or ""
            if o in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", o); self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Private-Network", "true"); self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode(); self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self._cors(); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def _allowed(self):
            o = self.headers.get("Origin"); return o is None or o in ALLOWED_ORIGINS   # 其他網站嘅網頁唔准攞
        def do_OPTIONS(self):
            self.send_response(204 if self._allowed() else 403); self._cors(); self.send_header("Content-Length", "0"); self.end_headers()
        def do_GET(self):
            if not self._allowed(): return self._send(403, {"error": "origin not allowed"})
            u = urlparse(self.path); qs = parse_qs(u.query)
            if u.path == "/health": return self._send(200, {"ok": True})
            if u.path == "/quotes": return self._send(200, bridge.quotes((qs.get("m") or [""])[0], ",".join(qs.get("codes") or []).split(",")))
            self._send(404, {"error": "not found"})
        def log_message(self, *a): pass
    return H

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="富途即時報價橋（決策卡）")
    ap.add_argument("--opend-host", default="127.0.0.1"); ap.add_argument("--opend-port", type=int, default=11111)
    ap.add_argument("--port", type=int, default=8766); ap.add_argument("--demo", action="store_true", help="用假報價，唔連 OpenD")
    a = ap.parse_args()
    if a.demo: src = Demo(); print("示範模式：報價係假嘅。")
    else:
        try: src = Futu(a.opend_host, a.opend_port)
        except ImportError: raise SystemExit("未裝 futu-api：pip install futu-api")
        print(f"已連接 OpenD {a.opend_host}:{a.opend_port}")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Bridge(src)))
    print(f"報價橋行緊：http://127.0.0.1:{a.port}　（Ctrl+C 停）")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: src.close()
