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


# 成交金額換算成「億元」的除數。兩個市場的單位不同，實測反推確認：
#   上市 成交股數（股）、成交金額（元）      -> /1e8
#   上櫃 成交量（張＝仟股）、成交金額（仟元） -> *1000/1e8 = /1e5
# 弄錯會差 1000 倍。
VALUE_DIVISOR = {"上市": 1e8, "上櫃": 1e5}

VALUE_DAYS = 5          # 進處置前取幾個交易日算平均成交值


def baseline(code, market, today, start_iso, value_days=VALUE_DAYS):
    """回傳 {pre_close, pre_close_date, last_close, pre_value, pre_value_days}。

    pre_close 是處置起算日「前一個交易日」的收盤價，也就是這檔進處置之前
    最後一個正常交易日的價格；頁面用它當漲跌基準。
    pre_value 是進處置前 value_days 個交易日的成交金額平均，單位億元。
    取不到就回 None，呼叫端自行處理。
    """
    fetch = _rows_twse if market == "上市" else _rows_tpex
    div = VALUE_DIVISOR.get(market, 1e8)
    seen = set()
    bars = []           # (iso, close, 成交值億元)

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
            amt = _f(r[2])
            bars.append((iso, close, (amt / div) if amt is not None else None))
        if not start_iso:
            break
        # 平均成交值要湊滿 value_days 根，可能得往回多抓一個月
        if sum(1 for b in bars if b[0] < start_iso) >= value_days:
            break

    if not bars:
        return {"pre_close": None, "pre_close_date": None, "last_close": None,
                "pre_value": None, "pre_value_days": 0}

    bars.sort()
    last = bars[-1]
    before = [b for b in bars if start_iso and b[0] < start_iso]
    pre = before[-1] if before else None

    vals = [b[2] for b in before[-value_days:] if b[2] is not None]
    avg = round(sum(vals) / len(vals), 3) if vals else None

    return {
        "pre_close": round(pre[1], 4) if pre else None,
        "pre_close_date": pre[0] if pre else None,
        "last_close": last[1],
        "pre_value": avg,
        "pre_value_days": len(vals),
    }
