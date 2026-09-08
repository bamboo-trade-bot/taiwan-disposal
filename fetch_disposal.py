# -*- coding: utf-8 -*-
"""抓取台股處置股資料（上市 TWSE + 上櫃 TPEx），正規化後輸出 disposal_data.json。

資料來源
  上市：https://www.twse.com.tw/zh/announcement/punish.html
        API  https://www.twse.com.tw/rwd/zh/announcement/punish?startDate=&endDate=&response=json
        （以「公布日期」查詢；長區間會被去重成每檔一列，故以月為單位分段抓取）
  上櫃：https://www.tpex.org.tw/zh-tw/announce/market/disposal.html
        API  https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?startDate=&endDate=&response=json
        （以「處置期間」查詢；跨月處置會在相鄰兩個月都出現，故抓完後去重）

用法
  python fetch_disposal.py                # 近 12 個月
  python fetch_disposal.py --months 24
  python fetch_disposal.py --start 2024-01-01 --end 2025-09-08
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TWSE_URL = "https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={a}&endDate={b}&response=json"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal?startDate={a}&endDate={b}&response=json"
TPEX_REFERER = "https://www.tpex.org.tw/zh-tw/announce/market/disposal.html"


def get_json(url, referer=None, retries=3):
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 連線不穩時退避重試
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("fetch failed: %s (%s)" % (url, last))


# ---------- 共用工具 ----------

LINK_RE = re.compile(r"\((?:\.{1,2}/|https?://)[^)]*\)")          # TPEx 欄位夾帶的連結
TAG_RE = re.compile(r"<[^>]+>")


def clean(s):
    if s is None:
        return ""
    s = str(s)
    s = LINK_RE.sub("", s)
    s = TAG_RE.sub("", s)
    return s.strip()


def roc_to_iso(s):
    """民國日期字串 -> ISO。接受 114/09/08、1140908、114年09月08日。"""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d{2,3})[/年-](\d{1,2})[/月-](\d{1,2})", s)
    if not m:
        m = re.match(r"^(\d{3})(\d{2})(\d{2})$", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def split_period(s):
    """處置起訖字串 -> (start_iso, end_iso)。分隔符可能是 ～ ~ 或 -。"""
    if not s:
        return None, None
    parts = re.split(r"[~～–—]", str(s).strip())
    parts = [p for p in parts if p.strip()]
    if len(parts) >= 2:
        return roc_to_iso(parts[0]), roc_to_iso(parts[-1])
    return roc_to_iso(s), None


def match_interval(text):
    """從處置內容擷取撮合頻率，回傳分鐘數（int）或 None。"""
    if not text:
        return None
    m = re.search(r"每\s*([0-9０-９零一二三四五六七八九十]{1,3})\s*分鐘撮合", text)
    if not m:
        return None
    raw = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return int(raw) if raw.isdigit() else cn_to_int(raw)


CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s):
    """中文數字 -> int，支援 1~99（十、十二、二十、二十五）。"""
    s = s.strip()
    if not s or any(ch not in CN_DIGIT and ch != "十" for ch in s):
        return None
    if "十" not in s:
        return CN_DIGIT.get(s) if len(s) == 1 else None
    tens, _, ones = s.partition("十")
    t = 1 if tens == "" else CN_DIGIT.get(tens)
    o = 0 if ones == "" else CN_DIGIT.get(ones)
    if t is None or o is None:
        return None
    return t * 10 + o


def disposal_days(text):
    """從處置內容擷取處置營業日數。上櫃用阿拉伯數字、上市用中文數字。

    只認錨定過的樣式：不能直接抓「N個營業日」，否則會誤抓處置原因裡的
    「連續3個營業日」「最近30個營業日內」。
    """
    if not text:
        return None
    m = re.search(r"起\s*(\d+)\s*個營業日", text)
    if m:
        return int(m.group(1))
    m = (re.search(r"[﹝（(]\s*([零一二三四五六七八九十]{1,3})個營業日", text)
         or re.search(r"起\s*([零一二三四五六七八九十]{1,3})個營業日", text))
    if m:
        return cn_to_int(m.group(1))
    return None


SINGLE_RE = re.compile(r"單筆達\s*([0-9０-９零一二三四五六七八九十]{1,3})\s*交易單位")
CUM_RE = re.compile(r"多筆累積達\s*([0-9０-９零一二三四五六七八九十]{1,3})\s*交易單位")


def _units(raw):
    raw = raw.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return int(raw) if raw.isdigit() else cn_to_int(raw)


def prepay_rule(text):
    """處置期間的預收款券規定，回傳 (等級, 門檻說明)。

    公告裡若載明「單筆達 N 交易單位或多筆累積達 M 交易單位以上」才收取全部價金，
    代表只有大額委託需要預收；沒有這個門檻就是所有委託一律預收（第二次處置的常態）。
    這是當沖與短線最直接的差別，必須在 detail 被截短前判定。
    """
    if not text:
        return None, None
    m1, m2 = SINGLE_RE.search(text), CUM_RE.search(text)
    if m1 or m2:
        parts = []
        if m1:
            parts.append("單筆 %s" % _units(m1.group(1)))
        if m2:
            parts.append("累計 %s" % _units(m2.group(1)))
        return "條件", "／".join(parts) + " 交易單位以上"
    if "收取全部之買進價金" in text:
        return "全面", "所有委託一律預收"
    return None, None


def sec_type(code, name):
    """依代號/名稱判斷標的類型。"""
    code = (code or "").strip()
    name = name or ""
    if re.match(r"^00\d{2}", code):
        return "ETF"
    if len(code) == 6:
        return "權證"
    if len(code) == 5 and code[:4].isdigit():
        return "可轉債"
    if re.match(r"^\d{4}$", code):
        return "股票"
    return "其他"


REASON_RULES = [
    ("轉(交)換", "標的證券遭處置"),
    ("標的證券", "標的證券遭處置"),
    ("當日沖銷", "當日沖銷比率過高"),
    ("連續五次", "連續 5 日達注意標準"),
    ("連續5", "連續 5 日達注意標準"),
    ("連續三次", "連續 3 日達注意標準"),
    ("連續3", "連續 3 日達注意標準"),
    ("6個營業日", "10 日內 6 日達注意標準"),
    ("六個營業日", "10 日內 6 日達注意標準"),
]


def reason_class(reason, detail):
    text = (reason or "") + " " + (detail or "")
    for key, label in REASON_RULES:
        if key in text:
            return label
    return "其他"


def round_no(measure, detail):
    """第幾次處置。上市有『處置措施』欄；上櫃需由內容推斷。"""
    t = (measure or "") + " " + (detail or "")
    if "第二次處置" in t or "第2次處置" in t:
        return 2
    if "第一次處置" in t or "第1次處置" in t:
        return 1
    # 上櫃：文中提及「最近30個營業日內曾發布處置」即為加重（第二次）處置
    if "曾發布處置" in t or "曾經發布處置" in t:
        return 2
    mi = match_interval(detail)
    if mi is not None:
        return 2 if mi >= 20 else 1
    return None


# ---------- 上市 ----------

def fetch_twse(start, end):
    rows, seen = [], set()
    for a, b in month_chunks(start, end):
        d = get_json(TWSE_URL.format(a=a.strftime("%Y%m%d"), b=b.strftime("%Y%m%d")))
        for r in (d.get("data") or []):
            r = list(r) + [""] * (10 - len(r))
            code = clean(r[2])
            if not code:
                continue
            ann = roc_to_iso(r[1])
            period = clean(r[6])
            key = (ann, code, period)
            if key in seen:
                continue
            seen.add(key)
            s, e = split_period(period)
            rows.append({
                "market": "上市",
                "announce_date": ann,
                "code": code,
                "name": clean(r[3]),
                "cumulative": to_int(r[4]),
                "reason_raw": clean(r[5]),
                "period_raw": period,
                "start": s,
                "end": e,
                "measure_raw": clean(r[7]),
                "detail": clean(r[8]),
                "close": None,
                "pe": None,
            })
    return rows


# ---------- 上櫃 ----------

def fetch_tpex(start, end):
    rows, seen = [], set()
    for a, b in month_chunks(start, end):
        d = get_json(TPEX_URL.format(a=a.strftime("%Y/%m/%d"), b=b.strftime("%Y/%m/%d")),
                     referer=TPEX_REFERER)
        for table in (d.get("tables") or []):
            for r in (table.get("data") or []):
                r = list(r) + [""] * (11 - len(r))
                code = clean(r[2])
                if not code:            # 表格內的空白/註腳列
                    continue
                ann = roc_to_iso(r[1])
                period = clean(r[5])
                key = (ann, code, period)
                if key in seen:
                    continue
                seen.add(key)
                s, e = split_period(period)
                rows.append({
                    "market": "上櫃",
                    "announce_date": ann,
                    "code": code,
                    "name": clean(r[3]),
                    "cumulative": to_int(r[4]),
                    "reason_raw": clean(r[6]),
                    "period_raw": period,
                    "start": s,
                    "end": e,
                    "measure_raw": "",
                    "detail": clean(r[7]),
                    "close": to_float(r[8]),
                    "pe": to_float(r[9]),
                })
    return rows


def to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        s = str(v).replace(",", "").strip()
        if s in ("", "N/A", "-", "--"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def month_chunks(start, end):
    """把區間切成整月片段（含頭尾）。"""
    cur = dt.date(start.year, start.month, 1)
    out = []
    while cur <= end:
        nxt = dt.date(cur.year + 1, 1, 1) if cur.month == 12 else dt.date(cur.year, cur.month + 1, 1)
        out.append((max(cur, start), min(nxt - dt.timedelta(days=1), end)))
        cur = nxt
    return out


def enrich(rows, today):
    for r in rows:
        r["type"] = sec_type(r["code"], r["name"])
        r["reason"] = reason_class(r["reason_raw"], r["detail"])
        r["round"] = round_no(r["measure_raw"], r["detail"])
        r["interval_min"] = match_interval(r["detail"])
        r["days"] = disposal_days(r["detail"])
        r["active"] = bool(r["start"] and r["end"] and r["start"] <= today <= r["end"])
        r["upcoming"] = bool(r["start"] and r["start"] > today)
        # 預收規定要在截短前判定，門檻條款位在公告後段，截掉就會誤判成「全面」
        r["prepay"], r["prepay_note"] = prepay_rule(r["detail"])
        # 網頁上不需要整段公告全文，只留前段供懸浮檢視
        r["detail"] = r["detail"][:400]
    rows.sort(key=lambda x: (x["announce_date"] or "", x["code"]), reverse=True)
    return rows


def resolve_range(months=12, start=None, end=None, today=None):
    """把 --months / --start / --end 換算成實際的起訖日。"""
    today = today or dt.date.today()
    end = dt.date.fromisoformat(end) if end else today
    if start:
        return dt.date.fromisoformat(start), end
    y, m = end.year, end.month - months + 1
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, 1), end


def collect(start, end, today=None, log=None):
    """抓取並正規化兩個市場的處置公告，回傳可直接序列化的 payload。"""
    today = today or dt.date.today()
    log = log or (lambda msg: None)

    log("range %s ~ %s" % (start, end))
    twse = fetch_twse(start, end)
    log("TWSE %d rows" % len(twse))
    # 上櫃以「處置期間」查詢，故往後多查一個月，才涵蓋已公告但尚未開始的處置
    tpex = fetch_tpex(start, end + dt.timedelta(days=30))
    log("TPEx %d rows" % len(tpex))

    rows = enrich(twse + tpex, today.isoformat())
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "sources": {
            "listed": "https://www.twse.com.tw/zh/announcement/punish.html",
            "otc": "https://www.tpex.org.tw/zh-tw/announce/market/disposal.html",
        },
        "rows": rows,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--out", default="disposal_data.json")
    args = p.parse_args()

    start, end = resolve_range(args.months, args.start, args.end)
    payload = collect(start, end, log=lambda m: sys.stderr.write(m + "\n"))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    sys.stderr.write("wrote %s (%d rows)\n" % (args.out, len(payload["rows"])))


if __name__ == "__main__":
    main()
