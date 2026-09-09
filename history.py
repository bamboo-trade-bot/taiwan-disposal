# -*- coding: utf-8 -*-
"""個股日線，用來取「進處置前一個交易日的收盤價」當作比較基準。

來源
  上市 https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=XXXX&response=json
  上櫃 https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=XXXX&date=YYYY/MM/01&response=json

兩邊都是「一次一檔一個月」，欄位順序也幾乎一樣：
  日期｜成交量｜成交金額｜開盤｜最高｜最低｜收盤｜漲跌｜筆數
"""
from fetch_disposal import TPEX_REFERER, get_json, roc_to_iso

TWSE_DAY = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
            "?date={ym}01&stockNo={code}&response=json")
TPEX_DAY = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
            "?code={code}&date={y}/{m}/01&response=json")

MAX_MONTHS = 4          # 處置起算日若落在月初，前一交易日會在上個月


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


def baseline(code, market, today, start_iso):
    """回傳 {pre_close, pre_close_date, last_close}。

    pre_close 是處置起算日「前一個交易日」的收盤價，也就是這檔進處置之前
    最後一個正常交易日的價格；頁面用它當漲跌基準。
    取不到就回 None，呼叫端自行處理。
    """
    fetch = _rows_twse if market == "上市" else _rows_tpex
    seen = set()
    pre = None          # (iso, close) 早於起算日、且最接近的那一天
    last = None         # (iso, close) 整體最新的一天

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
            close = _f(r[6])
            if not iso or close is None or iso in seen or iso > today.isoformat():
                continue
            seen.add(iso)
            if last is None or iso > last[0]:
                last = (iso, close)
            if start_iso and iso < start_iso and (pre is None or iso > pre[0]):
                pre = (iso, close)
        # 已經找到起算日之前的交易日就不必再往回抓
        if pre is not None or not start_iso:
            break

    return {
        "pre_close": round(pre[1], 4) if pre else None,
        "pre_close_date": pre[0] if pre else None,
        "last_close": last[1] if last else None,
    }
