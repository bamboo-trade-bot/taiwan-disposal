/**
 * 處置股報價 proxy（Cloudflare Worker）
 *
 * 存在的理由：證交所 MIS 不送 Access-Control-Allow-Origin，瀏覽器無法直連；
 * 而 GitHub Pages 是 HTTPS，也不能去打 http 的自架服務。這層負責補上 CORS、
 * 提供 HTTPS，並用短快取讓上游只看到極少量請求。
 *
 * 正規化欄位與 quotes.py 保持一致，前端接哪一邊都一樣。
 *
 *   GET /?codes=tse_3406,otc_3629
 */

const MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp";
const KEY_RE = /^(tse|otc)_[0-9A-Z]{4,6}$/;
const CHUNK = 40;      // 一次查詢的代號數上限
const CACHE_TTL = 5;   // 秒。處置股每 2 分鐘才撮合，5 秒完全無感
const MAX_CODES = 100;

function num(v) {
  if (v === undefined || v === null) return null;
  const s = String(v).trim();
  if (s === "" || s === "-" || s === "N/A") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

// 五檔字串 '19.80_19.70_' -> [19.8, 19.7]
// 會出現 '0.0000' 佔位（例如漲停只剩一檔買單），要濾掉否則誤判成還有掛單
function levels(v) {
  if (!v || v === "-") return [];
  return String(v).split("_").map(num).filter((x) => x !== null && x > 0);
}

function one(m) {
  // 處置股是人工撮合，兩次撮合之間 z 會是 '-'，此時退回前一盤成交價 pz
  let price = num(m.z);
  if (price === null) price = num(m.pz);

  const prev = num(m.y);
  const up = num(m.u);
  const down = num(m.w);
  const bids = levels(m.b);
  const asks = levels(m.a);

  let limit = null;
  if (price !== null) {
    if (up !== null && price >= up) limit = "up";
    else if (down !== null && price <= down) limit = "down";
  } else {
    // 漲停鎖死時整段時間都沒有成交，z 與 pz 會同時是 '-'，
    // 但「買不到」這件事仍看得出來：買方掛在漲停、賣方完全沒有掛單。
    if (bids.length && up !== null && bids[0] >= up && asks.length === 0) limit = "up";
    else if (asks.length && down !== null && asks[0] <= down && bids.length === 0) limit = "down";
  }
  const locked = (limit === "up" && asks.length === 0) || (limit === "down" && bids.length === 0);

  let change = null;
  let pct = null;
  if (price !== null && prev) {
    change = Math.round((price - prev) * 10000) / 10000;
    pct = Math.round((10000 * change) / prev) / 100;
  }

  const volume = Math.trunc(num(m.v) ?? 0);
  return {
    code: m.c,
    name: m.n,
    market: m.ex === "otc" ? "上櫃" : "上市",
    traded: volume > 0,
    price,
    prev_close: prev,
    change,
    change_pct: pct,
    open: num(m.o),
    high: num(m.h),
    low: num(m.l),
    limit_up: up,
    limit_down: down,
    bid: bids.length ? bids[0] : null,
    ask: asks.length ? asks[0] : null,
    volume,
    limit,
    locked,
    time: m.t || null,
    date: m.d || null,
  };
}

function corsHeaders(request, env) {
  const allowed = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const origin = request.headers.get("Origin") || "";
  const h = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    Vary: "Origin",
  };
  if (allowed.length === 0 || allowed.includes("*")) h["Access-Control-Allow-Origin"] = "*";
  else if (allowed.includes(origin)) h["Access-Control-Allow-Origin"] = origin;
  return h;
}

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers });
}

async function fetchChunk(keys) {
  const exCh = keys.map((k) => `${k}.tw`).join("|");
  const url = `${MIS}?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0`;
  // 同一批代號在 CACHE_TTL 內只會真的打一次上游
  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0" },
    cf: { cacheTtl: CACHE_TTL, cacheEverything: true },
  });
  if (!res.ok) throw new Error(`上游回應 HTTP ${res.status}`);
  const data = await res.json();
  if (String(data.rtcode) !== "0000") {
    throw new Error(`MIS rtcode=${data.rtcode} ${data.rtmessage || ""}`);
  }
  return data.msgArray || [];
}

export default {
  async fetch(request, env) {
    const headers = corsHeaders(request, env);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: { ...headers, "Access-Control-Allow-Methods": "GET, OPTIONS" },
      });
    }
    if (request.method !== "GET") {
      return json({ error: "只接受 GET" }, 405, headers);
    }

    const raw = new URL(request.url).searchParams.get("codes") || "";
    const keys = [...new Set(raw.split(",").map((s) => s.trim()))].filter((k) => KEY_RE.test(k));
    if (keys.length === 0) {
      return json({ error: "codes 參數缺少有效代號，格式如 tse_3406,otc_3629" }, 400, headers);
    }
    if (keys.length > MAX_CODES) {
      return json({ error: `一次最多 ${MAX_CODES} 個代號` }, 400, headers);
    }

    try {
      const quotes = {};
      for (let i = 0; i < keys.length; i += CHUNK) {
        const arr = await fetchChunk(keys.slice(i, i + CHUNK));
        for (const m of arr) {
          if (!m.c) continue;              // 查無此代號時 MIS 會回一個空物件
          quotes[`${m.ex}_${m.c}`] = one(m);
        }
      }
      return json({ source: "twse-mis", fetched_at: new Date().toISOString(), quotes }, 200, headers);
    } catch (e) {
      return json({ error: "報價來源暫時無法取得", detail: String(e.message).slice(0, 200) }, 502, headers);
    }
  },
};
