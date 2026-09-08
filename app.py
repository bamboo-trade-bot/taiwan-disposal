# -*- coding: utf-8 -*-
"""處置股觀測網站：以 HTTP 提供頁面與 JSON API。只用標準函式庫。

    python app.py                 # http://localhost:8000
    python app.py --port 9000 --months 24 --ttl 900

路由
    GET  /                 頁面（資料由伺服器端內嵌，前端不需再打 API）
    GET  /api/disposal     完整資料 JSON
    GET  /api/status       快取狀態：資料時間、是否更新中、上次錯誤
    POST /api/refresh      立刻在背景重抓一次

資料在伺服器端快取並落地成 disposal_data.json，重啟後可立即服務。
快取過期時採 stale-while-revalidate：先把舊資料送出，同時在背景更新，
所以請求不會卡在兩、三分鐘的抓取上。
"""
import argparse
import datetime as dt
import io
import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import build_page
import fetch_disposal

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "disposal_data.json")

HOLDING_PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="6">
<title>台股處置股觀測</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f2f3f0;color:#15181d;
      font-family:"Noto Sans TC",-apple-system,"Segoe UI",sans-serif}
 .b{text-align:center;padding:32px}
 h1{font-size:19px;font-weight:600;margin:0 0 8px}
 p{margin:0;color:#5b6572;font-size:14px}
 code{font-family:ui-monospace,monospace;font-size:13px}
</style></head><body><div class="b">
<h1>正在抓取處置公告…</h1>
<p>首次啟動需向證交所與櫃買中心逐月查詢，約需二到四分鐘。<br>本頁每 6 秒自動重試。</p>
<p style="margin-top:12px"><code>%s</code></p>
</div></body></html>
"""


class Cache(object):
    """存放 payload 與算好的頁面，並負責背景更新。"""

    def __init__(self, months, ttl):
        self.months = months
        self.ttl = ttl
        self.lock = threading.Lock()
        self.payload = None
        self.html = None
        self.fetched_at = None          # datetime，上次成功抓取的時間
        self.refreshing = False
        self.last_error = None
        self._load_from_disk()

    # ---- 狀態 ----
    def _load_from_disk(self):
        if not os.path.exists(DATA_FILE):
            return
        try:
            with io.open(DATA_FILE, encoding="utf-8") as f:
                payload = json.load(f)
            self._store(payload, dt.datetime.fromtimestamp(os.path.getmtime(DATA_FILE)))
            log("loaded %d rows from %s" % (len(payload["rows"]), os.path.basename(DATA_FILE)))
        except Exception as e:
            log("could not load cached data: %s" % e)

    def _store(self, payload, when):
        html = build_page.render(payload).encode("utf-8")
        with self.lock:
            self.payload = payload
            self.html = html
            self.fetched_at = when

    def is_stale(self):
        with self.lock:
            if self.payload is None or self.fetched_at is None:
                return True
            age = (dt.datetime.now() - self.fetched_at).total_seconds()
            # 換日後 active/upcoming 的判定會過期，所以跨日也算過期（以台北時間為準）
            return (age > self.ttl
                    or self.payload.get("today") != fetch_disposal.taipei_today().isoformat())

    def status(self):
        with self.lock:
            return {
                "rows": len(self.payload["rows"]) if self.payload else 0,
                "generated_at": self.payload["generated_at"] if self.payload else None,
                "fetched_at": self.fetched_at.isoformat(timespec="seconds") if self.fetched_at else None,
                "age_seconds": int((dt.datetime.now() - self.fetched_at).total_seconds()) if self.fetched_at else None,
                "ttl_seconds": self.ttl,
                "months": self.months,
                "refreshing": self.refreshing,
                "last_error": self.last_error,
            }

    # ---- 更新 ----
    def refresh_async(self):
        """若尚未在更新，就開一條背景執行緒去抓。回傳是否真的啟動。"""
        with self.lock:
            if self.refreshing:
                return False
            self.refreshing = True
        t = threading.Thread(target=self._refresh, name="refresh", daemon=True)
        t.start()
        return True

    def _refresh(self):
        try:
            start, end = fetch_disposal.resolve_range(months=self.months)
            payload = fetch_disposal.collect(start, end, log=log)
            self._store(payload, dt.datetime.now())
            tmp = DATA_FILE + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, DATA_FILE)      # 換檔是原子操作，避免讀到寫到一半的檔
            with self.lock:
                self.last_error = None
            log("refreshed: %d rows" % len(payload["rows"]))
        except Exception:
            err = traceback.format_exc(limit=2).strip().splitlines()[-1]
            with self.lock:
                self.last_error = err
            log("refresh failed: %s" % err)
        finally:
            with self.lock:
                self.refreshing = False

    def ensure_fresh(self):
        """過期就在背景更新；已有舊資料時照樣先送出去。"""
        if self.is_stale():
            self.refresh_async()


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "DisposalWatch"
    protocol_version = "HTTP/1.1"
    cache = None                      # 由 main() 指派

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    # ---- 回應工具 ----
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=1),
                   "application/json; charset=utf-8")

    # ---- 路由 ----
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        c = self.cache

        if path in ("/", "/index.html"):
            c.ensure_fresh()
            with c.lock:
                html = c.html
            if html is None:
                hint = "更新中…" if c.status()["refreshing"] else (c.status()["last_error"] or "")
                self._send(503, HOLDING_PAGE % hint, "text/html; charset=utf-8")
                return
            self._send(200, html, "text/html; charset=utf-8")
            return

        if path == "/api/disposal":
            c.ensure_fresh()
            with c.lock:
                payload = c.payload
            if payload is None:
                self._json(503, {"error": "資料尚未就緒", "status": c.status()})
                return
            self._json(200, payload)
            return

        if path == "/api/status":
            self._json(200, c.status())
            return

        if path == "/api/refresh":
            self._json(405, {"error": "請用 POST /api/refresh"})
            return

        self._json(404, {"error": "not found", "path": path})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/refresh":
            started = self.cache.refresh_async()
            self._json(202 if started else 409, {
                "started": started,
                "note": "已在背景更新" if started else "已有一個更新在進行中",
                "status": self.cache.status(),
            })
            return
        self._json(404, {"error": "not found", "path": path})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1", help="設 0.0.0.0 可讓區網其他機器連入")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--months", type=int, default=12, help="統計區間月數")
    p.add_argument("--ttl", type=int, default=1800, help="快取秒數，過期後在背景重抓")
    args = p.parse_args()

    cache = Cache(months=args.months, ttl=args.ttl)
    Handler.cache = cache
    cache.ensure_fresh()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    log("serving on http://%s:%d" % ("localhost" if args.host == "127.0.0.1" else args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("bye")
        httpd.shutdown()


if __name__ == "__main__":
    main()
