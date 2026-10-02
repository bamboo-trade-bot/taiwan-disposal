# -*- coding: utf-8 -*-
"""進出場時點的排列組合比較。

這是對歷史資料的統計，不是投資建議。

進場（D0 = 處置起算日，平盤價 = 起算日前一交易日收盤）
  D0_open       起算日開盤
  D0_close      起算日收盤
  D1_open       次一交易日開盤
  D1_close      次一交易日收盤
  D0_flat       起算日掛平盤限價買；當日最低價觸及才成交，成交價取 min(開盤, 平盤)
  D0_up         起算日收紅才以收盤買（追高）
  D0_down       起算日收黑才以收盤買（接刀）

出場（R = 出關日）
  end_close     處置最後一日收盤（仍在處置中賣出）
  R_open        出關日開盤
  R_close       出關日收盤
  R1_open       出關次一交易日開盤
  R1_close      出關次一交易日收盤

後三種進場方式會篩掉部分機會，樣本數因此不同，不能只比平均值——
n 欄要一起看。

用法
  python grid.py --min-value 10
"""
import argparse
import datetime as dt
import io
import json
import os
import statistics as st
import sys

import history as H
from backtest import build_events, FEE, TAX, months_between, prev_month

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bars_ohlc_cache.json")

ENTRIES = ["D0_open", "D0_close", "D1_open", "D1_close", "D0_flat", "D0_up", "D0_down"]
EXITS = ["end_close", "R_open", "R_close", "R1_open", "R1_close"]

ENTRY_LABEL = {
    "D0_open": "起算日開盤", "D0_close": "起算日收盤",
    "D1_open": "次日開盤", "D1_close": "次日收盤",
    "D0_flat": "起算日掛平盤", "D0_up": "起算日收紅才買", "D0_down": "起算日收黑才買",
}
EXIT_LABEL = {
    "end_close": "處置最後日收盤", "R_open": "出關日開盤", "R_close": "出關日收盤",
    "R1_open": "出關次日開盤", "R1_close": "出關次日收盤",
}


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
    """{iso: [開, 高, 低, 收, 成交值億]}"""
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
            o, hi, lo, c = H._f(r[3]), H._f(r[4]), H._f(r[5]), H._f(r[6])
            amt = H._f(r[2])
            if iso and c is not None:
                out[iso] = [o, hi, lo, c, (amt / div) if amt is not None else None]
    except Exception as e:
        log("  bars %s %s %04d%02d failed: %s" % (market, code, year, month, str(e)[:60]))
    cache[key] = out
    return out


def nth_trading_day(days, iso, n):
    """days 是排序過的交易日清單；取 iso 當天（或之後最近一天）再往後數 n 天。"""
    idx = None
    for i, d in enumerate(days):
        if d >= iso:
            idx = i
            break
    if idx is None or idx + n >= len(days):
        return None
    return days[idx + n]


def entry_price(kind, bars, days, e):
    """回傳 (成交價, 進場日) 或 (None, None) 代表這個條件下不會進場。"""
    d0 = nth_trading_day(days, e["start"], 0)
    if not d0:
        return None, None
    prev = [d for d in days if d < d0]
    flat = bars[prev[-1]][3] if prev else None         # 平盤價＝前一交易日收盤
    o, hi, lo, c = bars[d0][0], bars[d0][1], bars[d0][2], bars[d0][3]

    if kind == "D0_open":
        return o, d0
    if kind == "D0_close":
        return c, d0
    if kind == "D0_flat":
        if flat is None or lo is None or lo > flat:
            return None, None                          # 沒跌到平盤，掛單沒成交
        return (min(o, flat) if o is not None else flat), d0
    if kind == "D0_up":
        return (c, d0) if (flat is not None and c > flat) else (None, None)
    if kind == "D0_down":
        return (c, d0) if (flat is not None and c <= flat) else (None, None)

    d1 = nth_trading_day(days, e["start"], 1)
    if not d1:
        return None, None
    if kind == "D1_open":
        return bars[d1][0], d1
    if kind == "D1_close":
        return bars[d1][3], d1
    return None, None


def exit_price(kind, bars, days, e):
    if kind == "end_close":
        d = nth_trading_day(days, e["end"], 0)
        return (bars[d][3], d) if d else (None, None)
    if kind == "R_open":
        d = nth_trading_day(days, e["release"], 0)
        return (bars[d][0], d) if d else (None, None)
    if kind == "R_close":
        d = nth_trading_day(days, e["release"], 0)
        return (bars[d][3], d) if d else (None, None)
    if kind == "R1_open":
        d = nth_trading_day(days, e["release"], 1)
        return (bars[d][0], d) if d else (None, None)
    if kind == "R1_close":
        d = nth_trading_day(days, e["release"], 1)
        return (bars[d][3], d) if d else (None, None)
    return None, None


def net_return(entry, exit_):
    return 100.0 * ((exit_ * (1 - FEE - TAX)) / (entry * (1 + FEE)) - 1.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="disposal_data.json")
    p.add_argument("--min-value", type=float, default=10.0)
    p.add_argument("--out", default="grid_result.json")
    args = p.parse_args()

    def log(m):
        sys.stderr.write(m + "\n")

    payload = json.load(io.open(args.data, encoding="utf-8"))
    today = payload["today"]
    events = build_events(payload["rows"], today)
    new_start = min((r["start"] for r in payload["rows"]
                     if r["interval_min"] == 2 and r["start"]), default=None)
    log("處置段 %d，新制起算日界線 %s" % (len(events), new_start))

    cache = load_cache()
    recs = []
    for i, e in enumerate(events, 1):
        need = months_between(e["start"], e["release"])
        need.append(prev_month(e["start"]))
        # 出關次日可能落在下個月
        ry, rm = int(e["release"][:4]), int(e["release"][5:7])
        need.append((ry + 1, 1) if rm == 12 else (ry, rm + 1))
        bars = {}
        for y, m in sorted(set(need)):
            bars.update(month_bars(cache, e["market"], e["code"], y, m, log))
        days = sorted(bars)
        if not days:
            continue
        before = [d for d in days if d < e["start"]][-5:]
        vals = [bars[d][4] for d in before if bars[d][4] is not None]
        pre_value = (sum(vals) / len(vals)) if vals else None

        rec = {"market": e["market"], "code": e["code"], "start": e["start"],
               "round": e["round"], "pre_value": pre_value,
               "new_regime": bool(new_start and e["start"] >= new_start), "r": {}}
        for ek in ENTRIES:
            ep, _ = entry_price(ek, bars, days, e)
            if ep is None or ep <= 0:
                continue
            for xk in EXITS:
                xp, _ = exit_price(xk, bars, days, e)
                if xp is None or xp <= 0:
                    continue
                rec["r"]["%s|%s" % (ek, xk)] = round(net_return(ep, xp), 3)
        recs.append(rec)
        if i % 100 == 0:
            log("  %d/%d" % (i, len(events)))
            save_cache(cache)
    save_cache(cache)

    json.dump({"today": today, "new_start": new_start, "records": recs},
              io.open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
    log("寫出 %s（%d 段）" % (args.out, len(recs)))


if __name__ == "__main__":
    main()
