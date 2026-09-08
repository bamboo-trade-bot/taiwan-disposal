# 台股處置股觀測

彙整上市（證交所）與上櫃（櫃買中心）分開發布的處置有價證券公告，做成一頁可讀的網站。

三種用法：**部署成靜態站**（穩定網域，排程自動更新）、**本機跑成網站**（有 API、可隨時更新）、
**產生單一檔案**（丟到任何地方）。

---

## 一、部署到 GitHub Pages（穩定網域）

處置公告一天只在收盤後變動幾次，不需要常駐伺服器。`.github/workflows/deploy.yml`
會在每個交易日 **09:30 與 18:30（台北時間）**自動重抓、重建並發佈，另外可隨時手動觸發。

> **注意**：這個資料夾要當成**獨立的 repo**。不要在上層 `claude_code` 目錄執行 `git init`
> 再推上去——那裡有 `.env` 與 `.p12` 憑證檔，會連同金鑰一起公開。

### 步驟

```bash
cd taiwan-disposal
git init && git add -A && git commit -m "台股處置股觀測"
```

建立遠端 repo 並推上去（用 GitHub CLI 最快）：

```bash
gh repo create taiwan-disposal --public --source=. --push
```

沒有 `gh` 的話，先在 GitHub 網站上開一個空 repo，再：

```bash
git branch -M main
git remote add origin https://github.com/<你的帳號>/taiwan-disposal.git
git push -u origin main
```

接著在 GitHub repo 頁面：

1. **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**（不是 Deploy from a branch）。
2. **Actions** 分頁 → 選「抓取並發佈處置股頁面」→ **Run workflow** 手動跑一次，確認會綠燈。
3. 網址是 `https://<你的帳號>.github.io/taiwan-disposal/`。

repo 設為 public 最單純；private repo 要開 Pages 需要付費方案。這個專案只讀公開 API，
不含任何帳密或憑證。

### 綁自己的網域

**Settings → Pages → Custom domain** 填入網域（例如 `disposal.example.com`），
然後到你的 DNS 供應商加一筆 CNAME 指向 `<你的帳號>.github.io`。生效後勾選
**Enforce HTTPS**（GitHub 會自動簽憑證）。用 Actions 部署時網域設定存在 repo 設定裡，
不需要在專案內放 `CNAME` 檔。

### 排程說明

`cron` 用的是 UTC，台北時間 = UTC + 8：

| workflow 的 cron | 台北時間 | 用意 |
|---|---|---|
| `30 1 * * 1-5` | 09:30 | 盤前確認當日起算的處置名單 |
| `30 10 * * 1-5` | 18:30 | 收盤後公告都發完，抓當日新增 |

GitHub 的排程在尖峰時段可能延遲幾分鐘到十幾分鐘，不保證準時。
若某次抓取失敗，工作流程會中止部署，**線上維持上一版可用的頁面**。

---

## 二、本機跑成網站

```bash
python app.py
```

開 <http://localhost:8000>。只用 Python 標準函式庫，不需要安裝任何套件。

| 參數 | 預設 | 說明 |
|---|---|---|
| `--port` | 8000 | 連接埠 |
| `--host` | 127.0.0.1 | 設 `0.0.0.0` 可讓區網其他機器連入 |
| `--months` | 12 | 統計區間月數 |
| `--ttl` | 1800 | 快取秒數，過期後在背景重抓 |

| 方法 | 路徑 | 回應 |
|---|---|---|
| GET | `/` | 頁面 |
| GET | `/api/disposal` | 完整資料 JSON |
| GET | `/api/status` | 快取狀態：資料時間、是否更新中、上次錯誤 |
| POST | `/api/refresh` | 立刻在背景重抓一次 |

資料在伺服器端快取並落地成 `disposal_data.json`，重啟後可立即服務。快取過期時採
stale-while-revalidate：**先把手上的資料送出去，同時在背景抓新的**，所以請求不會卡在
兩、三分鐘的抓取上。跨日也視為過期，因為「處置中／即將開始」的判定會隨日期改變。

---

## 三、產生單一檔案的靜態頁

```bash
python fetch_disposal.py && python build_page.py
```

產出的 `disposal.html` 資料已內嵌，可直接用瀏覽器開啟，或丟到任何靜態空間。
缺點是資料為產出當下的快照，要更新就得重跑這兩行。

---

## 為什麼資料不能在瀏覽器端抓

兩個來源都沒有回傳 `Access-Control-Allow-Origin`，前端 `fetch` 會被 CORS 擋掉。
所以資料一律在伺服器端（或建置時）取得後寫進頁面，`app.py` 同時也就是那層代理。

## 檔案

| 檔案 | 用途 |
|---|---|
| `.github/workflows/deploy.yml` | 排程抓取、建置並發佈到 GitHub Pages |
| `app.py` | HTTP 伺服器：頁面、JSON API、快取與背景更新 |
| `fetch_disposal.py` | 抓取兩個來源、正規化欄位，輸出 `disposal_data.json` |
| `build_page.py` | 把資料內嵌進 `page_template.html`；`app.py` 也是呼叫這裡的 `render()` |
| `page_template.html` | 頁面版型與前端邏輯，資料位置為 `/*__DATA__*/` |

`disposal_data.json` 與 `disposal.html` 是產出物，已列入 `.gitignore`；CI 每次都會重新產生。

## 資料來源與抓取方式

| 市場 | 頁面 | API |
|---|---|---|
| 上市 | <https://www.twse.com.tw/zh/announcement/punish.html> | `twse.com.tw/rwd/zh/announcement/punish?startDate=&endDate=&response=json` |
| 上櫃 | <https://www.tpex.org.tw/zh-tw/announce/market/disposal.html> | `tpex.org.tw/www/zh-tw/bulletin/disposal?startDate=&endDate=&response=json` |
| 休市日曆 | 證交所市場開休市日期 | `twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&queryYear=<民國年>` |
| 股期標的 | <https://www.taifex.com.tw/cht/2/stockLists> | 同一網址，HTML 表格解析 |

休市日曆有個容易踩的地方：清單裡同時列出「國曆新年開始交易日」「農曆春節前最後交易日」
這類**照常交易**的資訊列，必須排除，否則會把交易日誤判成休市。股期清單是 HTML 解析，
較脆弱，取不到時只是少了標註，不影響其他資料。

兩個 API 的查詢語意不同，抓取時都以「月」為單位分段後去重：

- **上市**以「公布日期」查詢。跨月的長區間查詢會被伺服器**去重成每檔證券一列**（例：114/01/01–114/09/08 單次查詢只回 85 列，逐月加總則有 130 筆公告），所以不能用單次長區間查詢。
- **上櫃**以「處置期間」查詢，跨月的處置會在相鄰兩個月都出現，需去重。另因查詢條件是處置期間，抓取時會把結束日往後延 30 天，才涵蓋「已公告但尚未起算」的處置。

## 衍生欄位怎麼來的

兩邊的欄位不一致（上櫃沒有「處置措施」欄、上市沒有收盤價），以下欄位由公告全文擷取後統一：

| 欄位 | 判定方式 |
|---|---|
| `interval_min` | 公告內「約每 N 分鐘撮合一次」，上市為中文數字、上櫃為阿拉伯數字 |
| `days` | 處置營業日數。只認「起 N 個營業日」與括號內的中文數字，**不可**直接抓「N 個營業日」——會誤中處置原因裡的「連續 3 個營業日」「最近 30 個營業日內」 |
| `round` | 第幾次處置。優先取明文的「第一次／第二次處置」，其次看是否載明「最近 30 個營業日內曾發布處置」 |
| `prepay` | 預收款券規定。公告載明「單筆達 N 交易單位或多筆累積達 M 交易單位以上」才收取全部價金者為**條件**預收；沒有這個門檻就是**全面**預收（所有委託一律圈存，第二次處置的常態）。**必須在 `detail` 被截短前判定**——門檻條款位在公告後段，截掉會誤判成全面 |
| `type` | 由代號長度判定：4 碼股票、5 碼可轉債、6 碼權證、00xx 為 ETF |
| `reason` | 由處置條件與內容關鍵字歸類 |
| `release` | 出關日＝處置期滿後第一個交易日，依證交所休市日曆推算，已扣除週末與國定假日 |
| `has_future` | 是否為期交所股票期貨標的。處置期間現股受人工撮合與預收限制，股期仍連續交易 |
| `active` / `upcoming` | 以資料截止日與處置起訖日比對 |

## 現況為什麼以「證券」而非「公告」計數

同一檔可能同時被多張處置公告拘束（前一次還沒結束又接了一次），實際受限的是其中最嚴格的
條件。所以頁面上「現在的處置狀態」一律先依市場＋代號合併，取最晚的解除日、最長的撮合
間隔、以及較嚴格的預收規定；若不合併，像雙鴻、AMAX-KY 這類接續處置的個股會被重複計數。

## 資料上的一個轉折

過去一年的公告在 **2026 年 8 月**出現制度變更，兩個市場同步：撮合間隔由每 5 分鐘（加重每 20 分鐘）改為**每 2 分鐘**，處置期間由 10 個營業日縮短為 **5 個營業日**（加重 7 日）。用 8 月以前的處置經驗推估目前個股的流動性壓抑程度，基準已經不同。

## 免責

本專案整理公開資訊，不構成投資建議。實際處置措施與期間以交易所、櫃買中心公告為準。
