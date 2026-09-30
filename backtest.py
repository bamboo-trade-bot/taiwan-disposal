# -*- coding: utf-8 -*-
"""驗證「處置起算日買進、出關日賣出」的歷史表現。

這是對歷史資料的統計，不是投資建議。

方法
  進場  處置起算日的開盤價。處置名單前一晚就公告，隔天開盤買得到，無前視偏誤。
  出場  出關日的收盤價。出關日已恢復正常交易。
  合併  同一檔重疊或相接的處置視為一整段，出場用最後一張的出關日。
        不合併會重複計數，且出場日會落在還在處置中的時點。
  成本  賣出證交稅 0.3%，手續費雙邊各 0.1425%，來回約 0.585%。
  篩選  進場前 5 個交易日的平均成交金額（億元）。

對照組
  同一段期間的加權指數報酬。若不比對大盤，多頭期間的任何買進策略看起來都會賺。

用法
  python backtest.py --min-value 10
  python backtest.py --min-value 10 --refresh      # 重新抓日線
"""
import argparse
import collections
import datetime as dt
import io
import json
import os
import sys

import history as H
from fetch_disposal import get_json

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bars_cache.json")
TAIEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ym}01&response=json"

TAX = 0.003             # 證交稅，賣出時課
FEE = 0.001425          # 手續費，買賣各一次


def load_cache():
    if os.path.exists(CACHE):
        with io.open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(c):
    tmp = CACHE + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE)


def month_bars(cache, market, code, year, month, log):
    """回傳 {iso: (開, 收, 成交值億元)}，走快取。"""
    key = "%s|%s|%04d%02d" % (market, code, year, month)
    if key in cache:
        return cache[key]
    fetch = H._rows_twse if market == "上市" else H._rows_tpex
    div = H.VALUE_DIVISOR.get(market, 1e8)
    out = {}
    try:
        for r in fetch(code, year, month):
            if len(r) < 7:
                continue
            iso = H.roc_to_iso(r[0])
            op, close, amt = H._f(r[3]), H._f(r[6]), H._f(r[2])
            if iso and close is not None:
                out[iso] = [op, close, (amt / div) if amt is not None else None]
    except Exception as e:
        log("  bars %s %s %04d%02d failed: %s" % (market, code, year, month, str(e)[:60]))
    cache[key] = out
    return out


def months_between(a, b):
    y0, m0 = int(a[:4]), int(a[5:7])
    y1, m1 = int(b[:4]), int(b[5:7])
    out = []
    for k in range((y1 - y0) * 12 + (m1 - m0) + 1):
        mm = m0 + k
        out.append((y0 + (mm - 1) // 12, (mm - 1) % 12 + 1))
    return out


def prev_month(iso):
    y, m = int(iso[:4]), int(iso[5:7])
    return (y - 1, 12) if m == 1 else (y, m - 1)


def build_events(rows, today):
    """把重疊或相接的處置併成一段。"""
    by = collections.defaultdict(list)
    for r in rows:
        if r["type"] != "股票" or not (r["start"] and r["end"] and r["release"]):
            continue
        by[(r["market"], r["code"], r["name"])].append(r)

    events = []
    for key, rs in by.items():
        rs.sort(key=lambda x: x["start"])
        cur = None
        for r in rs:
            if cur and r["start"] <= cur["end"]:
                if r["end"] > cur["end"]:
                    cur["end"], cur["release"] = r["end"], r["release"]
                cur["orders"] += 1
                cur["round"] = max(cur["round"], r["round"] or 1)
            else:
                if cur:
                    events.append(cur)
                cur = {"market": key[0], "code": key[1], "name": key[2],
                       "start": r["start"], "end": r["end"], "release": r["release"],
                       "orders": 1, "round": r["round"] or 1}
        if cur:
            events.append(cur)
    return [e for e in events if e["release"] < today]


def taiex_series(cache, start, end, log):
    """加權指數收盤，{iso: 指數}。"""
    key = "TAIEX|%s|%s" % (start[:7], end[:7])
    if key in cache:
        return cache[key]
    out = {}
    for y, m in months_between(start, end):
        try:
            d = get_json(TAIEX_URL.format(ym="%04d%02d" % (y, m)))
        except Exception as e:
            log("  taiex %04d%02d failed: %s" % (y, m, str(e)[:60]))
            continue
        for r in (d.get("data") or []):
            iso = H.roc_to_iso(r[0])
            idx = H._f(r[4]) if len(r) > 4 else None
            if iso and idx:
                out[iso] = idx
    cache[key] = out
    return out


def on_or_after(series, iso, limit=10):
    """取 iso 當天或之後最近一個有資料的交易日。"""
    d = dt.date.fromisoformat(iso)
    for _ in range(limit):
        k = d.isoformat()
        if k in series:
            return k
        d += dt.timedelta(days=1)
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="disposal_data.json")
    p.add_argument("--min-value", type=float, default=10.0, help="進場前 5 日均成交值下限（億）")
    p.add_argument("--refresh", action="store_true", help="清掉日線快取重抓")
    p.add_argument("--out", default="backtest_events.json")
    args = p.parse_args()

    def log(m):
        sys.stderr.write(m + "\n")

    with io.open(args.data, encoding="utf-8") as f:
        payload = json.load(f)
    today = payload["today"]
    events = build_events(payload["rows"], today)
    log("已完成出關的處置段 %d" % len(events))

    if args.refresh and os.path.exists(CACHE):
        os.remove(CACHE)
    cache = load_cache()

    # 逐段取得需要的日線
    for i, e in enumerate(events, 1):
        need = months_between(e["start"], e["release"])
        need.append(prev_month(e["start"]))
        bars = {}
        for y, m in sorted(set(need)):
            bars.update(month_bars(cache, e["market"], e["code"], y, m, log))
        e["_bars"] = bars
        if i % 50 == 0:
            log("  日線 %d/%d" % (i, len(events)))
            save_cache(cache)
    save_cache(cache)

    lo = min(e["start"] for e in events)
    hi = max(e["release"] for e in events)
    taiex = taiex_series(cache, lo, hi, log)
    save_cache(cache)

    out = []
    for e in events:
        bars = e.pop("_bars")
        days = sorted(bars)
        before = [d for d in days if d < e["start"]][-5:]
        vals = [bars[d][2] for d in before if bars[d][2] is not None]
        e["pre_value"] = round(sum(vals) / len(vals), 3) if vals else None

        ein = on_or_after(bars, e["start"])
        eout = on_or_after(bars, e["release"])
        entry = bars[ein][0] if ein else None          # 起算日開盤
        exit_ = bars[eout][1] if eout else None        # 出關日收盤
        e["entry_date"], e["exit_date"] = ein, eout
        e["entry"], e["exit"] = entry, exit_
        if entry and exit_ and entry > 0:
            gross = exit_ / entry - 1.0
            e["gross"] = round(100 * gross, 3)
            e["net"] = round(100 * ((exit_ * (1 - FEE - TAX)) / (entry * (1 + FEE)) - 1.0), 3)
        else:
            e["gross"] = e["net"] = None

        ti, to = on_or_after(taiex, e["start"]), on_or_after(taiex, e["release"])
        e["bench"] = round(100 * (taiex[to] / taiex[ti] - 1.0), 3) if ti and to else None
        e["hold_days"] = (dt.date.fromisoformat(eout) - dt.date.fromisoformat(ein)).days if ein and eout else None
        out.append(e)

    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump({"today": today, "cost_pct": round(100 * (FEE * 2 + TAX), 3),
                   "events": out}, f, ensure_ascii=False, indent=1)
    ok = [e for e in out if e["net"] is not None]
    log("寫出 %s（%d 段，其中 %d 段可計算報酬）" % (args.out, len(out), len(ok)))


if __name__ == "__main__":
    main()
