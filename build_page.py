# -*- coding: utf-8 -*-
"""把處置資料內嵌進 page_template.html，產生單檔的 disposal.html。

兩個資料來源都沒有 CORS 標頭，瀏覽器無法直接抓，所以資料一律在伺服器端
（或建置時）就寫進頁面。app.py 也是呼叫這裡的 render()。

    python build_page.py
"""
import argparse
import io
import json
import os

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_template.html")
PLACEHOLDER = "/*__DATA__*/"

# 頁面實際用到的欄位；其餘（原始欄位、收盤價、本益比）不進頁面以縮小檔案
KEEP = ["market", "announce_date", "code", "name", "type", "reason", "round",
        "interval_min", "days", "start", "end", "active", "upcoming",
        "cumulative", "prepay", "prepay_note", "detail"]
DETAIL_CHARS = 180


def slim(payload):
    """只留頁面用得到的欄位，並截短公告全文。"""
    rows = []
    for r in payload["rows"]:
        o = {k: r.get(k) for k in KEEP}
        detail = (o.get("detail") or "").strip()
        if len(detail) > DETAIL_CHARS:
            detail = detail[:DETAIL_CHARS].rstrip() + "…"
        o["detail"] = detail
        rows.append(o)
    return {
        "generated_at": payload["generated_at"],
        "today": payload["today"],
        "range": payload["range"],
        "rows": rows,
    }


def read_template(path=TEMPLATE):
    with io.open(path, encoding="utf-8") as f:
        html = f.read()
    if PLACEHOLDER not in html:
        raise ValueError("template is missing the %s placeholder" % PLACEHOLDER)
    return html


def render(payload, template=None):
    """回傳內嵌好資料的完整 HTML 字串。"""
    html = template if template is not None else read_template()
    blob = json.dumps(slim(payload), ensure_ascii=False, separators=(",", ":"))
    # 內嵌在 <script> 裡，避免字串內出現 </script> 提前關閉標籤
    blob = blob.replace("</", "<\\/")
    return html.replace(PLACEHOLDER, blob)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="disposal_data.json")
    p.add_argument("--template", default=TEMPLATE)
    p.add_argument("--out", default="disposal.html")
    args = p.parse_args()

    with io.open(args.data, encoding="utf-8") as f:
        payload = json.load(f)

    html = render(payload, read_template(args.template))
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s  (%d rows, %.0f KB)"
          % (args.out, len(payload["rows"]), len(html.encode("utf-8")) / 1024))


if __name__ == "__main__":
    main()
