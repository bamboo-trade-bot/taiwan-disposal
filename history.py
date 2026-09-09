# -*- coding: utf-8 -*-
"""個股日線，用來算「N 個交易日內從高點回落幾 %」。

來源
  上市 https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=XXXX&response=json
  上櫃 https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=XXXX&date=YYYY/MM/DD&response=json

兩邊都是「一次一個月、一次一檔」，欄位順序也幾乎一樣：
  日期｜成交量｜成交金額｜開盤｜最高｜最低｜收盤｜漲跌｜筆數
高點取的是盤中最高價（索引 4），不是收盤價——回落幅度本來就該從盤中高點起算。
"""
import datetime as dt
import re

from fetch_disposal import UA, TPEX_REFERER, get_json, roc_to_iso

TWSE_DAY = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
            "?date={ym}01&stockNo={code}&response=json")
TPEX_DAY = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
            "?code={code}&date={y}/{m}/01&response=json")

MAX_MONTHS = 3          # 往回抓幾個月就該夠 14 個交易日了


def _f(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "--", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rows_twse(code, year, month):
    d = get_json(TWSE_DAY.format(ym="%04d%02d" % (year, month), code=code))
    if d.get("stat") != "OK":
        return []
    return d.get("data") or []


def _rows_tpex(code, year, month):
    d = get_json(TPEX_DAY.format(code=code, y=year, m="%02d" % month),
                 referer=TPEX_REFERER)
    out = []
    for t in (d.get("tables") or []):
        out.extend(t.get("data") or [])
    return out


def _month_back(year, month, n):
    m = month - n
    while m <= 0:
        m += 12
        year -= 1
    return year, m


def daily_bars(code, market, today, days=14):
    """回傳最近 days 個交易日的 [(iso日期, 最高價, 收盤價)]，新到舊。

    取不到就回空清單，呼叫端自行處理。
    """
    fetch = _rows_twse if market == "上市" else _rows_tpex
    seen, bars = set(), []
    for back in range(MAX_MONTHS):
        y, m = _month_back(today.year, today.month, back)
        try:
            raw = fetch(code, y, m)
        except Exception:
            raw = []
        for r in raw:
            if len(r) < 7:
                continue
            iso = roc_to_iso(r[0])
            if not iso or iso in seen or iso > today.isoformat():
                continue
            high, close = _f(r[4]), _f(r[6])
            if high is None:
                continue
            seen.add(iso)
            bars.append((iso, high, close))
        if len(bars) >= days:
            break
    bars.sort(reverse=True)
    return bars[:days]


def drawdown(code, market, today, days=14):
    """回傳 {high, high_date, last_close, bars} ；資料不足時 high 為 None。"""
    bars = daily_bars(code, market, today, days)
    if not bars:
        return {"high": None, "high_date": None, "last_close": None, "bars": 0}
    top = max(bars, key=lambda b: b[1])
    return {
        "high": round(top[1], 4),
        "high_date": top[0],
        "last_close": bars[0][2],
        "bars": len(bars),
    }
