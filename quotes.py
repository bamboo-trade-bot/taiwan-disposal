# -*- coding: utf-8 -*-
"""證交所 MIS 即時報價，正規化成頁面用得到的欄位。

來源 https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw|otc_6488.tw&json=1

實測（2026-09-09 盤中）：
  - 不需要 Referer 或 Cookie，直接 GET 即可
  - 回應沒有 Access-Control-Allow-Origin，瀏覽器不能直連，一定要經過這層
  - tse_ 與 otc_ 可混在同一次查詢
  - 股票、可轉債、權證都查得到

處置股是人工撮合，目前每 2 分鐘才成交一次，所以短快取完全無感，
也讓上游只看到極少量請求。
"""
import json
import re
import time
import urllib.parse
import urllib.request

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}&json=1&delay=0"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

KEY_RE = re.compile(r"^(tse|otc)_[0-9A-Z]{4,6}$")
CHUNK = 40                 # 一次查詢的代號數上限，保守值
CACHE_TTL = 5.0            # 秒


def valid_key(key):
    """代號鍵格式為 tse_2330 或 otc_6488。"""
    return bool(KEY_RE.match(key or ""))


def _num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _levels(v):
    """五檔字串 '19.80_19.70_' -> [19.8, 19.7]。

    欄位有時以 '0.0000' 當佔位（例如漲停只剩一檔買單時），要濾掉，
    否則會誤判成「還有掛單」。
    """
    if not v or v == "-":
        return []
    return [x for x in (_num(p) for p in str(v).split("_")) if x is not None and x > 0]


def _one(m):
    # 處置股是人工撮合，兩次撮合之間 z 會是 '-'，此時取前一盤成交價 pz，
    # 否則畫面上的價格會大部分時間空白。
    price = _num(m.get("z"))
    if price is None:
        price = _num(m.get("pz"))
    prev = _num(m.get("y"))
    up, down = _num(m.get("u")), _num(m.get("w"))
    bids, asks = _levels(m.get("b")), _levels(m.get("a"))

    limit = None
    if price is not None:
        if up is not None and price >= up:
            limit = "up"
        elif down is not None and price <= down:
            limit = "down"
    else:
        # 漲停鎖死時整段時間都沒有成交，z 與 pz 會同時是 '-'，
        # 但「買不到」這件事仍看得出來：買方掛在漲停、賣方完全沒有掛單。
        if bids and up is not None and bids[0] >= up and not asks:
            limit = "up"
        elif asks and down is not None and asks[0] <= down and not bids:
            limit = "down"

    # 鎖死＝停在漲跌停且對手方掛單全空，也就是實際上買不到或賣不掉
    locked = bool(limit == "up" and not asks) or bool(limit == "down" and not bids)

    change = pct = None
    if price is not None and prev:
        change = round(price - prev, 4)
        pct = round(100.0 * change / prev, 2)

    volume = int(_num(m.get("v")) or 0)
    return {
        "code": m.get("c"),
        "name": m.get("n"),
        "market": "上櫃" if m.get("ex") == "otc" else "上市",
        "traded": volume > 0,
        "price": price,
        "prev_close": prev,
        "change": change,
        "change_pct": pct,
        "open": _num(m.get("o")),
        "high": _num(m.get("h")),
        "low": _num(m.get("l")),
        "limit_up": up,
        "limit_down": down,
        "bid": bids[0] if bids else None,
        "ask": asks[0] if asks else None,
        "volume": volume,
        "limit": limit,
        "locked": locked,
        "time": m.get("t") or None,
        "date": m.get("d") or None,
    }


def _get(ex_ch, timeout=15):
    url = MIS_URL.format(ex_ch=urllib.parse.quote(ex_ch, safe="|_."))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(keys, timeout=15):
    """keys 形如 ['tse_3406', 'otc_3629']，回傳 {key: quote}。

    查不到的代號不會出現在結果裡（MIS 會回一個沒有 c 欄位的空物件）。
    """
    keys = [k for k in dict.fromkeys(keys) if valid_key(k)]
    out = {}
    for i in range(0, len(keys), CHUNK):
        part = keys[i:i + CHUNK]
        data = _get("|".join(k + ".tw" for k in part), timeout)
        if str(data.get("rtcode")) != "0000":
            raise RuntimeError("MIS rtcode=%s %s" % (data.get("rtcode"), data.get("rtmessage")))
        for m in (data.get("msgArray") or []):
            if not m.get("c"):
                continue
            out["%s_%s" % (m.get("ex"), m.get("c"))] = _one(m)
    return out


class Cache(object):
    """短 TTL 快取。多個瀏覽器同時輪詢時，上游只會被打一次。"""

    def __init__(self, ttl=CACHE_TTL):
        self.ttl = ttl
        self._at = 0.0
        self._key = None
        self._val = None

    def get(self, keys):
        sig = ",".join(sorted(keys))
        now = time.time()
        if self._val is not None and self._key == sig and now - self._at < self.ttl:
            return self._val, True
        val = fetch(keys)
        self._at, self._key, self._val = now, sig, val
        return val, False
