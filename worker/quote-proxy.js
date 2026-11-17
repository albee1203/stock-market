/**
 * 台股即時報價轉接站（Cloudflare Worker）
 *
 * App 是靜態網頁，瀏覽器不允許直接讀取證交所的資料，所以由這個 Worker 代為查詢，
 * 並加上 CORS 標頭讓 App 讀取。資料來源：證交所「基本市況報導」（mis.twse.com.tw，盤中約 5 秒更新）。
 *
 * 用法：GET https://<你的 worker>.workers.dev/?q=tse_2330,otc_8069
 *   q：最多 50 檔，格式「市場_代號」，市場 tse＝上市、otc＝上櫃
 * 回傳：{ time, quotes: { "2330": { price, prev, change, change_pct, open, high, low, volume, time, ... } } }
 *
 * 設定（Worker → Settings → Variables，選用）：
 *   ALLOWED_ORIGINS：允許呼叫的網站，逗號分隔；預設只允許 GitHub Pages 與本機測試
 */

const DEFAULT_ORIGINS = ["https://albee1203.github.io", "http://127.0.0.1:8765", "http://localhost:8765"];
const MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp";
const CACHE_SECONDS = 10;
const ITEM = /^(tse|otc)_[0-9A-Z]{4,6}$/;

function num(v) {
  if (v == null || v === "-" || v === "") return null;
  const n = parseFloat(String(v).split("_")[0]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function normalize(item) {
  const prev = num(item.y);
  let price = num(item.z);
  let source = "trade";
  if (price == null) {
    // 最近 5 秒沒有成交時 z 為 "-"：用最佳買賣價中間價；都沒有就用昨收
    const bid = num(item.b), ask = num(item.a);
    if (bid && ask) { price = +((bid + ask) / 2).toFixed(2); source = "quote"; }
    else if (bid || ask) { price = bid || ask; source = "quote"; }
    else { price = prev; source = "prev"; }
  }
  const change = price != null && prev ? +(price - prev).toFixed(2) : null;
  return {
    code: item.c, name: item.n, price, prev, change,
    change_pct: change != null && prev ? +(change / prev).toFixed(5) : null,
    open: num(item.o), high: num(item.h), low: num(item.l),
    volume: item.v && item.v !== "-" ? parseInt(item.v, 10) : null, // 累積成交量（張）
    time: item.t || null, date: item.d || null, source,
  };
}

function cors(origin, allowed) {
  const ok = allowed.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : allowed[0],
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Vary": "Origin",
  };
}

async function fetchMis(items, cookie) {
  const url = `${MIS}?ex_ch=${items.map(i => `${i}.tw`).join("|")}&json=1&delay=0&_=${Date.now()}`;
  const headers = { "User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp", "Accept": "application/json" };
  if (cookie) headers.Cookie = cookie;
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`證交所回應 ${res.status}`);
  return res.json();
}

export async function query(items) {
  let data = await fetchMis(items);
  if (!data.msgArray || data.msgArray.length === 0) {
    // 部分情況需要先取得工作階段 cookie 再查詢
    const home = await fetch("https://mis.twse.com.tw/stock/index.jsp", { headers: { "User-Agent": "Mozilla/5.0" } });
    const cookie = (home.headers.get("set-cookie") || "").split(";")[0];
    data = await fetchMis(items, cookie);
  }
  const quotes = {};
  for (const it of data.msgArray || []) {
    const q = normalize(it);
    if (q.code) quotes[q.code] = q;
  }
  return quotes;
}

export default {
  async fetch(request, env, ctx) {
    const allowed = (env?.ALLOWED_ORIGINS ? env.ALLOWED_ORIGINS.split(",").map(s => s.trim()) : DEFAULT_ORIGINS);
    const origin = request.headers.get("Origin") || "";
    const headers = { ...cors(origin, allowed), "Content-Type": "application/json; charset=utf-8" };
    if (request.method === "OPTIONS") return new Response(null, { headers });
    if (request.method !== "GET") return new Response('{"error":"只接受 GET"}', { status: 405, headers });
    if (origin && !allowed.includes(origin)) return new Response('{"error":"不允許的來源"}', { status: 403, headers });

    const url = new URL(request.url);
    const items = [...new Set((url.searchParams.get("q") || "").split(",").map(s => s.trim().toLowerCase()))]
      .map(s => s.replace(/_(\w+)$/, (_, c) => "_" + c.toUpperCase()))
      .filter(s => ITEM.test(s)).slice(0, 50);
    if (!items.length) {
      return new Response(JSON.stringify({ ok: true, usage: "?q=tse_2330,otc_8069" }), { headers });
    }

    // 相同查詢 10 秒內共用結果，減輕證交所負擔
    const cacheKey = new Request(`https://quote-cache.local/?q=${items.sort().join(",")}`);
    const cache = typeof caches !== "undefined" ? caches.default : null;
    if (cache) {
      const hit = await cache.match(cacheKey);
      if (hit) return new Response(hit.body, { headers });
    }
    try {
      const quotes = await query(items);
      const body = JSON.stringify({ time: new Date().toISOString(), quotes });
      if (cache && ctx) {
        ctx.waitUntil(cache.put(cacheKey, new Response(body, { headers: { "Cache-Control": `max-age=${CACHE_SECONDS}` } })));
      }
      return new Response(body, { headers });
    } catch (err) {
      return new Response(JSON.stringify({ error: String(err.message || err) }), { status: 502, headers });
    }
  },
};
