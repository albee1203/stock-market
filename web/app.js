"use strict";
/* 台股觀察清單 PWA：讀取 data/ 底下由 `python -m app.cli export` 產生的靜態 JSON。 */

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const state = { index: null, date: null, day: null, stocks: null, listSide: "buy", foreignSide: "buy", newsFilter: "all" };
const cache = new Map();

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const sign = x => (x > 0 ? "+" : "") + (x ?? 0).toFixed(2);
const tone = x => (x > 0.1 ? "up" : x < -0.1 ? "down" : "");
const fmtInt = x => (x == null ? "—" : Math.round(x).toLocaleString("zh-TW"));
const fmtYi = x => (x == null ? "—" : (x / 1e8).toFixed(1) + " 億");
const safeUrl = u => (/^https?:\/\//i.test(u || "") ? u : "#");

async function getJSON(path, { fresh = false } = {}) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const res = await fetch(path, { cache: fresh ? "no-cache" : "default" });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  const data = await res.json();
  cache.set(path, data);
  return data;
}

async function loadIndex(fresh = false) {
  state.index = await getJSON("data/index.json", { fresh });
  const sel = $("#date");
  sel.innerHTML = state.index.dates.map(d => `<option value="${d}">${d}</option>`).join("");
  if (!state.date || !state.index.dates.includes(state.date)) state.date = state.index.latest;
  sel.value = state.date || "";
  $("#updated").textContent = state.index.latest ? `更新於 ${state.index.updated_at.slice(0, 16).replace("T", " ")}` : "尚無資料";
}

async function loadDay(fresh = false) {
  state.day = state.date ? await getJSON(`data/days/${state.date}.json`, { fresh }) : null;
}

/* ---------- 共用元件 ---------- */

function meter(label, v) {
  const w = Math.min(Math.abs(v), 1) * 50;
  const left = v >= 0 ? 50 : 50 - w;
  const color = v >= 0 ? "var(--up)" : "var(--down)";
  return `<span>${label}</span><span class="meter" role="img" aria-label="${label} ${sign(v)}"><i style="left:${left}%;width:${w}%;background:${color}"></i></span><span class="num">${sign(v)}</span>`;
}

function seg(name, value, options) {
  return `<div class="seg" role="group">${options.map(([v, label, cls]) =>
    `<button data-seg="${name}" data-v="${v}" class="${cls || ""}" aria-pressed="${v === value}">${label}</button>`).join("")}</div>`;
}

function empty(msg) { return `<div class="empty">${msg}</div>`; }
const disclaimer = () => `<p class="disclaimer">${esc(state.index?.disclaimer || "")}</p>`;

/* ---------- 頁面 ---------- */

function renderList() {
  const d = state.day;
  if (!d) return empty("還沒有資料。請等待每日自動更新，或在電腦上執行 <code>python -m app.cli run</code>。");
  const side = state.listSide;
  const rows = d[side];
  const cards = rows.map(r => `
    <a class="card" href="#/stock/${esc(r.code)}">
      <div class="card-head">
        <span class="rank num">${r.rank}</span>
        <span class="name">${esc(r.name)}</span><span class="code">${esc(r.code)}</span>
        <span class="pill ${side === "buy" ? "up" : "down"} num">${sign(r.total_score)}</span>
      </div>
      <div class="meters">${meter("外資", r.foreign_score)}${meter("新聞", r.news_score)}</div>
      <p class="reason">${esc(r.reason)}</p>
      <div class="chips">${(r.keywords || []).map(k => `<span class="chip ${side === "buy" ? "up" : "down"}">${esc(k)}</span>`).join("")}</div>
    </a>`).join("");
  return seg("listSide", side, [["buy", `建議買入 ${d.buy.length}`, "up"], ["sell", `賣出／避開 ${d.sell.length}`, "down"]])
    + `<p class="muted" style="margin-bottom:10px">${side === "buy" ? "外資連買或大買，且新聞偏利多" : "外資連賣或大賣，且新聞偏利空"}</p>`
    + (rows.length ? cards : empty("今日沒有同時符合外資與新聞條件的標的"))
    + disclaimer();
}

function renderForeign() {
  const d = state.day;
  if (!d) return empty("尚無資料");
  const side = state.foreignSide;
  const rows = d.foreign[side];
  const cls = side === "buy" ? "up-t" : "down-t";
  return seg("foreignSide", side, [["buy", "外資買超", "up"], ["sell", "外資賣超", "down"]])
    + `<div class="rows">${rows.map((r, i) => `
      <a class="row" href="#/stock/${esc(r.code)}">
        <span><span class="muted num" style="display:inline">${i + 1}.</span> <b>${esc(r.name)}</b> <span class="code">${esc(r.code)}</span></span>
        <span class="r big num ${cls}">${fmtInt(r.net_lots)} 張</span>
        <span class="chips" style="margin:0">${r.flags.map(f => `<span class="chip">${esc(f)}</span>`).join("") || `<span class="muted">連續 ${Math.abs(r.streak)} 日</span>`}</span>
        <span class="r muted num">${fmtYi(r.net_amount)}${r.net_ratio != null ? `・佔量 ${(Math.abs(r.net_ratio) * 100).toFixed(0)}%` : ""}</span>
      </a>`).join("")}</div>` + disclaimer();
}

function newsItem(a) {
  const t = tone(a.sentiment);
  return `<div class="news-item">
    <a class="t" href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener"><span class="dot ${t}"></span>${esc(a.title)}</a>
    <p class="muted">${esc(a.source)}・${esc(a.published_at.slice(5).replace("T", " "))}・情緒 <span class="num ${t}-t">${sign(a.sentiment)}</span></p>
    <div class="chips">
      ${a.bullish_keywords.map(k => `<span class="chip up">${esc(k)}</span>`).join("")}
      ${a.bearish_keywords.map(k => `<span class="chip down">${esc(k)}</span>`).join("")}
      ${a.stocks.map(s => `<a class="chip" href="#/stock/${esc(s.code)}">${esc(s.name)} ${sign(s.score)}</a>`).join("")}
    </div></div>`;
}

function renderNews() {
  const d = state.day;
  if (!d) return empty("尚無資料");
  const f = state.newsFilter;
  const list = d.news.filter(a => f === "all" || (f === "up" ? a.sentiment > 0.1 : a.sentiment < -0.1));
  return seg("newsFilter", f, [["all", `全部 ${d.news.length}`], ["up", "利多", "up"], ["down", "利空", "down"]])
    + (list.length ? `<div class="rows">${list.map(newsItem).join("")}</div>` : empty("沒有符合的新聞"));
}

function chartSVG(hist) {
  const W = 640, H = 220, padL = 8, padR = 8, top = 12, bottom = 24;
  const n = hist.length, bw = (W - padL - padR) / n;
  const lots = hist.map(h => h[1]);
  const maxAbs = Math.max(1, ...lots.map(Math.abs));
  const mid = top + (H - top - bottom) / 2, half = (H - top - bottom) / 2;
  const bars = hist.map((h, i) => {
    const v = h[1], hgt = Math.abs(v) / maxAbs * half;
    const y = v >= 0 ? mid - hgt : mid;
    return `<rect x="${padL + i * bw + bw * 0.15}" y="${y}" width="${bw * 0.7}" height="${Math.max(hgt, 0.5)}" rx="1.5" fill="${v >= 0 ? "var(--up)" : "var(--down)"}"><title>${h[0]}：${fmtInt(v)} 張</title></rect>`;
  }).join("");
  const closes = hist.map(h => h[2]).filter(x => x != null);
  let line = "";
  if (closes.length > 1) {
    const lo = Math.min(...closes), hi = Math.max(...closes);
    const span = hi - lo || 1, flat = hi === lo;
    const pts = hist.map((h, i) => h[2] == null ? null :
      `${padL + i * bw + bw / 2},${flat ? mid : top + (1 - (h[2] - lo) / span) * (H - top - bottom)}`).filter(Boolean);
    line = `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--text)" stroke-width="2" stroke-linejoin="round" opacity=".75"/>`;
  }
  const first = hist[0]?.[0]?.slice(5) ?? "", last = hist[n - 1]?.[0]?.slice(5) ?? "";
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="外資買賣超與收盤價走勢">
    <line x1="${padL}" x2="${W - padR}" y1="${mid}" y2="${mid}" stroke="var(--border)"/>
    ${bars}${line}
    <text x="${padL}" y="${H - 6}" font-size="12" fill="var(--muted)">${first}</text>
    <text x="${W - padR}" y="${H - 6}" font-size="12" fill="var(--muted)" text-anchor="end">${last}</text>
  </svg>`;
}

async function renderStock(code) {
  if (!state.stocks) state.stocks = await getJSON("data/stocks.json").catch(() => []);
  const options = state.stocks.map(s => `<option value="${esc(s[0])} ${esc(s[1])}">`).join("");
  const search = `<form class="search" id="stock-search"><input type="search" name="q" list="stock-list" placeholder="輸入代號或名稱，例如 2330 或 台積電" value="${esc(code || "")}" autocomplete="off" aria-label="搜尋個股"><button class="icon-btn" aria-label="搜尋">⌕</button></form><datalist id="stock-list">${options}</datalist>`;
  if (!code) {
    const picks = state.day ? [...state.day.buy, ...state.day.sell] : [];
    return search + (picks.length ? `<h2>今日清單個股</h2><div class="chips">${picks.map(r =>
      `<a class="chip ${state.day.buy.includes(r) ? "up" : "down"}" href="#/stock/${esc(r.code)}">${esc(r.code)} ${esc(r.name)}</a>`).join("")}</div>` : "");
  }
  const info = state.stocks.find(s => s[0] === code);
  let hist = [];
  try { hist = await getJSON(`data/history/${encodeURIComponent(code)}.json`); } catch { /* 無資料 */ }
  if (!info && !hist.length) return search + empty(`找不到 ${esc(code)} 的資料`);
  const rec = state.day && ([...state.day.buy.map(r => ["buy", r]), ...state.day.sell.map(r => ["sell", r])].find(([, r]) => r.code === code));
  const news = state.day ? state.day.news.filter(a => a.stocks.some(s => s.code === code)) : [];
  let streak = 0;
  for (let i = hist.length - 1; i >= 0; i--) {
    const v = hist[i][1];
    if (i === hist.length - 1) { streak = Math.sign(v); continue; }
    if (Math.sign(v) === Math.sign(streak) && v !== 0) streak += Math.sign(v); else break;
  }
  const sum5 = hist.slice(-5).reduce((a, h) => a + h[1], 0);
  const lastClose = [...hist].reverse().find(h => h[2] != null)?.[2];
  return search + `
    <h2 style="font-size:20px">${esc(info?.[1] || code)} <span class="code">${esc(code)}・${info?.[2] === "TPEX" ? "上櫃" : "上市"}</span></h2>
    ${rec ? `<div class="card" style="border-color:var(--${rec[0] === "buy" ? "up" : "down"})"><b class="${rec[0] === "buy" ? "up" : "down"}-t">今日列入${rec[0] === "buy" ? "建議買入" : "建議賣出／避開"} 第 ${rec[1].rank} 名（${sign(rec[1].total_score)}）</b><p class="reason">${esc(rec[1].reason)}</p></div>` : ""}
    <div class="stats">
      <div class="stat"><span class="muted">收盤</span><b class="num">${lastClose ?? "—"}</b></div>
      <div class="stat"><span class="muted">外資連續</span><b class="num ${streak > 0 ? "up-t" : streak < 0 ? "down-t" : ""}">${streak > 0 ? "買" : streak < 0 ? "賣" : ""} ${Math.abs(streak)} 日</b></div>
      <div class="stat"><span class="muted">近 5 日合計</span><b class="num ${sum5 > 0 ? "up-t" : sum5 < 0 ? "down-t" : ""}">${fmtInt(sum5)} 張</b></div>
    </div>
    ${hist.length ? `<div class="chart">${chartSVG(hist)}<div class="legend"><span><span class="dot up"></span>外資買超</span><span><span class="dot down"></span>外資賣超</span><span>— 收盤價</span></div></div>` : ""}
    <h2>當日相關新聞</h2>
    ${news.length ? `<div class="rows">${news.map(newsItem).join("")}</div>` : empty("當日沒有相關新聞")}
    ${disclaimer()}`;
}

/* ---------- 路由 ---------- */

async function render() {
  const [, tab = "list", arg] = (location.hash || "#/list").split("/");
  document.querySelectorAll(".tabbar a").forEach(a => { if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  try {
    if (tab === "foreign") view.innerHTML = renderForeign();
    else if (tab === "news") view.innerHTML = renderNews();
    else if (tab === "stock") view.innerHTML = await renderStock(arg ? decodeURIComponent(arg) : "");
    else view.innerHTML = renderList();
  } catch (e) {
    view.innerHTML = empty(`載入失敗：${esc(e.message)}`);
  }
}

view.addEventListener("click", e => {
  const b = e.target.closest("button[data-seg]");
  if (!b) return;
  state[b.dataset.seg] = b.dataset.v;
  render();
});
view.addEventListener("submit", e => {
  if (e.target.id !== "stock-search") return;
  e.preventDefault();
  const q = new FormData(e.target).get("q").trim();
  const hit = /^\d{4}/.test(q) ? q.slice(0, 4) : (state.stocks.find(s => s[1] === q || s[1].includes(q)) || [])[0];
  if (hit) location.hash = `#/stock/${hit}`;
});
view.addEventListener("change", e => {
  if (e.target.matches("#stock-search input") && /^\d{4}\s/.test(e.target.value)) location.hash = `#/stock/${e.target.value.slice(0, 4)}`;
});
window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });

$("#date").addEventListener("change", async e => {
  state.date = e.target.value;
  await loadDay();
  render();
});

$("#refresh").addEventListener("click", async () => {
  cache.clear();
  await boot(true);
});

let deferredPrompt = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); deferredPrompt = e; $("#install").hidden = false; });
$("#install").addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  $("#install").hidden = true;
});

const setOffline = () => { $("#offline").hidden = navigator.onLine; };
window.addEventListener("online", setOffline);
window.addEventListener("offline", setOffline);

async function boot(fresh = false) {
  setOffline();
  try {
    await loadIndex(fresh);
    await loadDay(fresh);
  } catch (e) {
    $("#updated").textContent = "無法載入資料";
  }
  render();
}

if ("serviceWorker" in navigator && location.protocol !== "file:") {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
boot(true);
