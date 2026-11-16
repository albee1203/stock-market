"use strict";
/* 台股觀察清單 PWA：讀取 data/ 底下由 `python -m app.cli export` 產生的靜態 JSON。 */

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const state = { editing: null, backtest: null, btSide: "buy", index: null, date: null, day: null, stocks: null, listSide: "buy", foreignSide: "buy", newsFilter: "all" };
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

/* ---------- 推薦指數 / 停損停利 ---------- */

const IDX_LEVELS = [[80, "強力關注"], [65, "偏多"], [36, "中性"], [21, "偏空"], [0, "強烈避開"]];
const idxLabel = i => IDX_LEVELS.find(([t]) => i >= t)[1];
const idxTone = i => (i >= 65 ? "up" : i <= 35 ? "down" : "");
function badge(idx) {
  if (idx == null) return "";
  return `<span class="badge ${idxTone(idx)}" title="推薦指數（0~100，50 為中性）"><b class="num">${idx}</b><small>${idxLabel(idx)}</small></span>`;
}
const fmtP = x => (x == null ? "—" : Number(x).toLocaleString("zh-TW", { maximumFractionDigits: 2 }));
function levelsLine(lv, prefix = "參考") {
  if (!lv) return "";
  return `<p class="levels"><span class="down-t">${prefix}停損 <b class="num">${fmtP(lv.stop)}</b>（-${Math.round(lv.stop_pct * 100)}%）</span>
    <span class="up-t">停利 <b class="num">${fmtP(lv.take)}</b>（+${Math.round(lv.take_pct * 100)}%）</span></p>`;
}

// 與 app/risk.py 相同的公式：停損 = clamp(2σ, 4%, 10%)，停利 = 停損 × 2
function calcLevels(entry, sigma, stopPct, takePct) {
  const r = state.index?.risk || { sl_sigma_mult: 2, sl_min: 0.04, sl_max: 0.1, default_sl: 0.07, reward_ratio: 2, near_pct: 0.02 };
  const sl = stopPct ?? (sigma == null ? r.default_sl : Math.min(r.sl_max, Math.max(r.sl_min, r.sl_sigma_mult * sigma)));
  const tp = takePct ?? sl * r.reward_ratio;
  return { entry, stop: +(entry * (1 - sl)).toFixed(2), take: +(entry * (1 + tp)).toFixed(2), stop_pct: sl, take_pct: tp };
}
function holdingStatus(price, lv) {
  const near = state.index?.risk?.near_pct ?? 0.02;
  if (price <= lv.stop) return ["stop", "已跌破停損價"];
  if (price >= lv.take) return ["take", "已達停利價"];
  if (price <= lv.stop * (1 + near)) return ["near_stop", "接近停損價"];
  if (price >= lv.take * (1 - near)) return ["near_take", "接近停利價"];
  return ["ok", "正常"];
}

// 我的持股：只存在這台裝置（localStorage），不會上傳
function loadHoldings() { try { return JSON.parse(localStorage.getItem("holdings") || "{}"); } catch { return {}; } }
function saveHoldings(h) { try { localStorage.setItem("holdings", JSON.stringify(h)); } catch { /* 私密模式等 */ } }
state.holdings = loadHoldings();

async function getHistory(code) {
  const data = await getJSON(`data/history/${encodeURIComponent(code)}.json`);
  return Array.isArray(data) ? { h: data, levels: null } : data;
}
const lastClose = hist => [...hist.h].reverse().find(x => x[2] != null)?.[2];

const ALERT_ICON = { stop: "🛑", near_stop: "⚠️", take: "🎯", near_take: "📈", expired: "⏱" };

async function alertsBlock() {
  const items = (state.day?.alerts || []).map(a => `
    <a class="alert ${a.kind.includes("stop") ? "down" : a.kind.includes("take") ? "up" : ""}" href="#/stock/${esc(a.code)}">
      <span>${ALERT_ICON[a.kind] || "•"} <b>${esc(a.name)}</b> <span class="code">${esc(a.code)}</span> ${esc(a.text)}</span>
      <span class="muted num">收盤 ${fmtP(a.exit_price ?? a.last_close)}・${esc(a.open_date.slice(5))} 入清單起 <span class="${pctCls(a.ret)}">${pct(a.ret)}</span>・停損 ${fmtP(a.stop)}／停利 ${fmtP(a.take)}</span>
    </a>`);
  for (const [code, h] of Object.entries(state.holdings)) {
    let hist;
    try { hist = await getHistory(code); } catch { continue; }
    const price = lastClose(hist);
    if (price == null) continue;
    const lv = calcLevels(h.cost, hist.levels?.sigma ?? null, h.stopPct, h.takePct);
    const [kind, text] = holdingStatus(price, lv);
    const name = state.stocks?.find(s => s[0] === code)?.[1] || code;
    const ret = price / h.cost - 1;
    items.push(`
      <a class="alert ${kind.includes("stop") ? "down" : kind.includes("take") ? "up" : "ok"}" href="#/stock/${esc(code)}">
        <span>${ALERT_ICON[kind] || "💼"} 持股 <b>${esc(name)}</b> <span class="code">${esc(code)}</span> ${esc(text)}</span>
        <span class="muted num">收盤 ${fmtP(price)}・成本 ${fmtP(h.cost)} <span class="${pctCls(ret)}">${pct(ret)}</span>・停損 ${fmtP(lv.stop)}／停利 ${fmtP(lv.take)}</span>
      </a>`);
  }
  // 夜盤跳空預警：清單追蹤（後端算好）＋本機持股（依夜盤漲跌估算開盤價）
  const night = state.day?.night;
  const gaps = [...(state.day?.gap_alerts || [])];
  if (night) {
    const beta = state.index?.night?.beta ?? 1;
    for (const [code, h] of Object.entries(state.holdings)) {
      let hist; try { hist = await getHistory(code); } catch { continue; }
      const price = lastClose(hist); if (price == null) continue;
      const lv = calcLevels(h.cost, hist.levels?.sigma ?? null, h.stopPct, h.takePct);
      const est = +(price * (1 + night.change_pct * beta)).toFixed(2);
      const name = state.stocks?.find(x => x[0] === code)?.[1] || code;
      if (est <= lv.stop && lv.stop < price) gaps.push({ code, name: "持股 " + name, last_close: price, est_open: est, stop: lv.stop, take: lv.take, kind: "gap_stop", text: "預估開盤可能跌破停損" });
      else if (price < lv.take && lv.take <= est) gaps.push({ code, name: "持股 " + name, last_close: price, est_open: est, stop: lv.stop, take: lv.take, kind: "gap_take", text: "預估開盤可能觸及停利" });
    }
  }
  items.unshift(...gaps.map(g => `
    <a class="alert ${g.kind === "gap_stop" ? "down" : "up"}" href="#/stock/${esc(g.code)}">
      <span>🌙 <b>${esc(g.name)}</b> <span class="code">${esc(g.code)}</span> ${esc(g.text)}</span>
      <span class="muted num">昨收 ${fmtP(g.last_close ?? g.close)} → 預估 ${fmtP(g.est_open)}・停損 ${fmtP(g.stop)}／停利 ${fmtP(g.take)}</span>
    </a>`));
  if (!items.length) return "";
  return `<h2 style="margin-top:4px">🔔 停損停利提醒</h2><div class="rows" style="margin-bottom:14px">${items.join("")}</div>`;
}

function nightBlock(n) {
  if (!n) {
    // 只在最新一天顯示「等待夜盤」提示；舊日期沒有夜盤就不顯示
    if (!state.index || state.date !== state.index.latest) return "";
    return `<div class="card night">
      <div class="card-head"><span class="name">🌙 台指期夜盤</span><span class="muted" style="margin-left:auto">尚未更新</span></div>
      <p class="muted" style="margin:6px 0 0">${esc(state.date.slice(5))} 收盤後的夜盤（15:00～隔日 05:00）會在隔天早上 08:40 抓取，並納入盤前推薦指數與跳空預警。</p>
    </div>`;
  }
  const t = n.change_pct > 0 ? "up" : n.change_pct < 0 ? "down" : "";
  const arrow = n.change > 0 ? "▲" : n.change < 0 ? "▼" : "－";
  return `<div class="card night ${t}">
    <div class="card-head"><span class="name">🌙 台指期夜盤</span>
      <span class="num ${t}-t" style="margin-left:auto;font-weight:700">${fmtP(n.close)} ${arrow}${fmtInt(Math.abs(n.change))}（${(n.change_pct * 100).toFixed(2)}%）</span></div>
    <ul class="notes">${n.notes.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    <p class="muted">已納入盤前推薦指數（${esc(n.ref_date.slice(5))} 日盤收 ${fmtP(n.ref_close)}，${esc(n.month)} 合約）</p>
  </div>`;
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

async function renderList() {
  if (!state.stocks) state.stocks = await getJSON("data/stocks.json").catch(() => []);
  const d = state.day;
  if (!d) return empty("還沒有資料。請等待每日自動更新，或在電腦上執行 <code>python -m app.cli run</code>。");
  const side = state.listSide;
  const rows = d[side];
  const cards = rows.map(r => `
    <a class="card" href="#/stock/${esc(r.code)}">
      <div class="card-head">
        <span class="rank num">${r.rank}</span>
        <span class="name">${esc(r.name)}</span><span class="code">${esc(r.code)}</span>
        ${r.pre_index != null ? `<span class="badge-wrap">${badge(r.pre_index)}<small class="muted">盤前・昨收 ${r.index}</small></span>` : badge(r.index)}
      </div>
      <div class="meters">${meter("外資", r.foreign_score)}${meter("新聞", r.news_score)}</div>
      <p class="reason">${esc(r.reason)}</p>
      ${side === "buy" ? levelsLine(r.levels) : `<p class="levels muted">持有者可考慮減碼${r.levels ? `；跌破 <b class="num">${fmtP(r.levels.stop)}</b>（-${Math.round(r.levels.stop_pct * 100)}%）宜停損` : ""}</p>`}
      <div class="chips">${(r.keywords || []).map(k => `<span class="chip ${side === "buy" ? "up" : "down"}">${esc(k)}</span>`).join("")}</div>
    </a>`).join("");
  return nightBlock(d.night) + (await alertsBlock())
    + seg("listSide", side, [["buy", `建議買入 ${d.buy.length}`, "up"], ["sell", `賣出／避開 ${d.sell.length}`, "down"]])
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
        <span class="r big num ${cls}">${fmtInt(r.net_lots)} 張${r.index != null ? ` <span class="mini ${idxTone(r.index)}">${r.index}</span>` : ""}</span>
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
  let histObj = { h: [], levels: null };
  try { histObj = await getHistory(code); } catch { /* 無資料 */ }
  const hist = histObj.h;
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
  const lastPx = lastClose(histObj);
  const idx = state.day?.scores?.[code];
  const mine = state.holdings[code];
  let mineHtml = "";
  if (mine && lastPx != null) {
    const lv = calcLevels(mine.cost, histObj.levels?.sigma ?? null, mine.stopPct, mine.takePct);
    const [kind, text] = holdingStatus(lastPx, lv);
    const ret = lastPx / mine.cost - 1;
    mineHtml = `<p class="${kind.includes("stop") ? "down-t" : kind.includes("take") ? "up-t" : ""}"><b>${ALERT_ICON[kind] || "💼"} ${esc(text)}</b>：成本 ${fmtP(mine.cost)}，目前 <span class="${pctCls(ret)}">${pct(ret)}</span></p>${levelsLine(lv, "我的")}`;
  }
  const holdForm = `<div class="card">
      <h3 style="margin:0 0 6px;font-size:15px">💼 我的持股</h3>
      ${mineHtml}
      <form id="holding-form" data-code="${esc(code)}" class="hold-form">
        <label>成本 <input name="cost" type="number" step="0.01" min="0" inputmode="decimal" value="${mine ? esc(mine.cost) : ""}" placeholder="${lastPx ?? ""}"></label>
        <label>停損% <input name="sl" type="number" step="0.5" min="0" inputmode="decimal" value="${mine?.stopPct != null ? esc(mine.stopPct * 100) : ""}" placeholder="自動"></label>
        <label>停利% <input name="tp" type="number" step="0.5" min="0" inputmode="decimal" value="${mine?.takePct != null ? esc(mine.takePct * 100) : ""}" placeholder="自動"></label>
        <button class="btn">${mine ? "更新" : "加入"}</button>${mine ? `<button type="button" class="btn ghost" data-remove="${esc(code)}">移除</button>` : ""}
      </form>
      <p class="muted">只存在這台裝置。停損%、停利% 留空＝依波動自動計算。</p>
    </div>`;
  return search + `
    <h2 style="font-size:20px">${esc(info?.[1] || code)} <span class="code">${esc(code)}・${info?.[2] === "TPEX" ? "上櫃" : "上市"}</span></h2>
    ${rec ? `<div class="card" style="border-color:var(--${rec[0] === "buy" ? "up" : "down"})"><b class="${rec[0] === "buy" ? "up" : "down"}-t">今日列入${rec[0] === "buy" ? "建議買入" : "建議賣出／避開"} 第 ${rec[1].rank} 名・推薦指數 ${rec[1].index}</b><p class="reason">${esc(rec[1].reason)}</p></div>` : ""}
    ${idx != null ? `<div class="card idx-card">${badge(idx)}<span class="muted">推薦指數（0～100，50 為中性）綜合當日外資籌碼與新聞情緒</span></div>` : ""}
    <div class="stats">
      <div class="stat"><span class="muted">收盤</span><b class="num">${fmtP(lastPx)}</b></div>
      <div class="stat"><span class="muted">外資連續</span><b class="num ${streak > 0 ? "up-t" : streak < 0 ? "down-t" : ""}">${streak > 0 ? "買" : streak < 0 ? "賣" : ""} ${Math.abs(streak)} 日</b></div>
      <div class="stat"><span class="muted">近 5 日合計</span><b class="num ${sum5 > 0 ? "up-t" : sum5 < 0 ? "down-t" : ""}">${fmtInt(sum5)} 張</b></div>
    </div>
    ${histObj.levels ? `<div class="card"><h3 style="margin:0 0 4px;font-size:15px">若今日收盤買進</h3>${levelsLine(histObj.levels)}<p class="muted">依近 20 日波動計算，風險報酬比 1:2；以收盤價判斷是否觸及。</p></div>` : ""}
    ${holdForm}
    ${hist.length ? `<div class="chart">${chartSVG(hist)}<div class="legend"><span><span class="dot up"></span>外資買超</span><span><span class="dot down"></span>外資賣超</span><span>— 收盤價</span></div></div>` : ""}
    <h2>當日相關新聞</h2>
    ${news.length ? `<div class="rows">${news.map(newsItem).join("")}</div>` : empty("當日沒有相關新聞")}
    ${disclaimer()}`;
}

const pct = x => (x == null ? "—" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`);
const pctCls = x => (x == null ? "" : x > 0 ? "up-t" : x < 0 ? "down-t" : "");

async function renderBacktest() {
  if (!state.backtest) state.backtest = await getJSON("data/backtest.json");
  const bt = state.backtest, side = state.btSide;
  if (!bt.list_days) return empty("還沒有回測資料。清單累積幾天後就會出現；也可以在電腦執行 <code>python -m app.cli replay --days 60</code> 用歷史資料補算。");
  const sum = bt.summary[side];
  const winLabel = side === "buy" ? "勝率" : "避開成功率";
  const tiles = bt.horizons.map(h => {
    const m = sum[String(h)];
    return `<div class="stat"><span class="muted">${h} 日後</span>
      <b class="num ${pctCls(m.avg)}">${pct(m.avg)}</b>
      <span class="muted num">${winLabel} ${m.win_rate == null ? "—" : Math.round(m.win_rate * 100) + "%"}</span><br>
      <span class="muted num">超額 ${pct(m.avg_excess)}</span><br>
      <span class="muted num">${m.n} 筆</span></div>`;
  }).join("");
  const mine = bt.details.filter(r => r.side === side);
  const pending = mine.filter(r => r.r1 == null).length;
  const rows = mine.filter(r => r.r1 != null).slice(0, 80).map(r => `
    <a class="row" href="#/stock/${esc(r.code)}">
      <span><span class="muted num" style="display:inline">${esc(r.date.slice(5))}</span> <b>${esc(r.name)}</b> <span class="code">${esc(r.code)}</span></span>
      <span class="r num">${bt.horizons.map(h => `<span class="${pctCls(r["r" + h])}" style="display:inline-block;min-width:4.2em">${pct(r["r" + h])}</span>`).join("")}</span>
    </a>`).join("");
  return seg("btSide", side, [["buy", "建議買入", "up"], ["sell", "賣出／避開", "down"]])
    + `<p class="muted">統計 ${bt.list_days} 份清單（${esc(bt.period[0])} ～ ${esc(bt.period[1])}）。${side === "sell" ? "賣出清單報酬越低代表避開越正確。" : ""}</p>`
    + `<div class="stats">${tiles}</div>`
    + (side === "buy" && bt.strategy ? `<div class="card"><h3 style="margin:0 0 6px;font-size:15px">照停損停利出場（最長持有 20 日）</h3>
        ${bt.strategy.closed ? `<p style="margin:0"><b class="num ${pctCls(bt.strategy.avg)}">平均 ${pct(bt.strategy.avg)}</b>・勝率 ${Math.round(bt.strategy.win_rate * 100)}%・平均持有 ${bt.strategy.avg_days} 日</p>
        <p class="muted">已出場 ${bt.strategy.closed} 筆：停利 ${bt.strategy.take}・停損 ${bt.strategy.stop}・到期 ${bt.strategy.expired}｜持有中 ${bt.strategy.open} 筆</p>`
        : `<p class="muted">尚無已出場部位（持有中 ${bt.strategy.open} 筆）</p>`}</div>` : "")
    + `<h2>明細 <span class="muted" style="display:inline;float:right">${bt.horizons.map(h => `<span style="display:inline-block;min-width:4.2em;text-align:right">${h}日</span>`).join("")}</span></h2>`
    + (pending ? `<p class="muted" style="margin-bottom:8px">另有 ${pending} 筆最新清單尚未滿 1 個交易日，明天起開始計算。</p>` : "")
    + `<div class="rows">${rows || '<div class="empty">尚無已實現的報酬</div>'}</div>`
    + `<p class="disclaimer">${esc(bt.method)}過去績效不代表未來表現。</p>`;
}

/* ---------- 我的持股 ---------- */

const KEYMAP = { "股數": "shares", shares: "shares", "張": "lots", lots: "lots", "停損": "sl", sl: "sl", "停利": "tp", tp: "tp", "日期": "date", date: "date" };

// 與 app/risk.py parse_holdings 相同格式：代號,成本[,停損%,停利%][,股數=1000][,日期=2026-11-01]
function parseHoldingsText(text) {
  const out = {};
  for (let line of text.replace(/[；;]/g, "\n").split(/\n/)) {
    line = line.split("#")[0].trim();
    if (!line) continue;
    const parts = line.replace(/，/g, ",").split(",").map(x => x.trim());
    const code = parts[0], cost = parseFloat(parts[1]);
    if (!/^\w{4,6}$/.test(code) || !(cost > 0)) continue;
    const h = { cost }, pos = [];
    for (const p of parts.slice(2)) {
      if (p.includes("=")) {
        const [k, v] = p.split("=").map(x => x.trim());
        const key = KEYMAP[k] || KEYMAP[k.toLowerCase()];
        if (key === "shares") h.shares = Math.round(+v);
        else if (key === "lots") h.shares = Math.round(+v * 1000);
        else if (key === "sl") h.stopPct = +v / 100;
        else if (key === "tp") h.takePct = +v / 100;
        else if (key === "date") h.date = v;
      } else pos.push(p);
    }
    if (pos[0]) h.stopPct ??= +pos[0] / 100;
    if (pos[1]) h.takePct ??= +pos[1] / 100;
    out[code] = h;
  }
  return out;
}

function holdingsToText(hs) {
  return Object.entries(hs).map(([code, h]) => {
    const parts = [code, h.cost];
    if (h.stopPct != null || h.takePct != null) parts.push(h.stopPct != null ? +(h.stopPct * 100).toFixed(2) : "", h.takePct != null ? +(h.takePct * 100).toFixed(2) : "");
    if (h.shares) parts.push(`股數=${h.shares}`);
    if (h.date) parts.push(`日期=${h.date}`);
    return parts.join(",");
  }).join("\n");
}

function histStreak(h) {
  let streak = 0;
  for (let i = h.length - 1; i >= 0; i--) {
    const v = h[i][1];
    if (i === h.length - 1) { streak = Math.sign(v); if (!v) break; continue; }
    if (v !== 0 && Math.sign(v) === Math.sign(streak)) streak += Math.sign(v); else break;
  }
  return streak;
}

function holdingSignals(code, streak) {
  const d = state.day, out = [];
  if (d?.sell.some(r => r.code === code)) out.push(["列入賣出／避開名單", "down"]);
  else if (d?.buy.some(r => r.code === code)) out.push(["列入建議買入名單", "up"]);
  if (streak <= -3) out.push([`外資連賣 ${-streak} 日`, "down"]);
  else if (streak >= 3) out.push([`外資連買 ${streak} 日`, "up"]);
  let bull = 0, bear = 0;
  for (const a of d?.news || []) {
    const st = a.stocks.find(x => x.code === code);
    if (!st) continue;
    if (st.score > 0.1) bull++; else if (st.score < -0.1) bear++;
  }
  if (bear) out.push([`利空新聞 ${bear} 則`, "down"]);
  if (bull) out.push([`利多新聞 ${bull} 則`, "up"]);
  return out;
}

const fmtMoney = x => (x == null ? "—" : `${x > 0 ? "+" : ""}${Math.round(x).toLocaleString("zh-TW")}`);
const STATUS_RANK = { stop: 0, near_stop: 1, take: 2, near_take: 3, ok: 5 };

async function renderPortfolio() {
  if (!state.stocks) state.stocks = await getJSON("data/stocks.json").catch(() => []);
  const entries = Object.entries(state.holdings);
  const items = [];
  for (const [code, h] of entries) {
    let hist = { h: [], levels: null };
    try { hist = await getHistory(code); } catch { /* 無資料 */ }
    const closes = hist.h.filter(x => x[2] != null);
    const price = closes.at(-1)?.[2] ?? null, prev = closes.at(-2)?.[2] ?? null;
    const name = state.stocks.find(x => x[0] === code)?.[1] || code;
    const it = { code, name, ...h, price, prev };
    if (price != null) {
      it.lv = calcLevels(h.cost, hist.levels?.sigma ?? null, h.stopPct, h.takePct);
      [it.kind, it.text] = holdingStatus(price, it.lv);
      it.ret = price / h.cost - 1;
      it.day = prev ? price / prev - 1 : null;
      if (h.shares) {
        it.value = h.shares * price; it.costValue = h.shares * h.cost;
        it.pnl = it.value - it.costValue; it.dayPnl = prev ? h.shares * (price - prev) : null;
      }
    } else { it.kind = "ok"; it.text = "查無股價"; }
    it.index = state.day?.scores?.[code];
    it.signals = holdingSignals(code, histStreak(hist.h));
    items.push(it);
  }
  items.sort((a, b) => (STATUS_RANK[a.kind] ?? 5) - (a.kind === "ok" && a.signals.some(x => x[1] === "down") ? 1 : 0)
    - ((STATUS_RANK[b.kind] ?? 5) - (b.kind === "ok" && b.signals.some(x => x[1] === "down") ? 1 : 0)) || (a.ret ?? 0) - (b.ret ?? 0));

  const priced = items.filter(i => i.value != null);
  const sum = k => priced.reduce((a, i) => a + (i[k] ?? 0), 0);
  const cost = sum("costValue"), value = sum("value"), pnl = value - cost, dayPnl = sum("dayPnl");
  const summary = priced.length ? `
    <div class="card">
      <div class="stats" style="margin:0">
        <div class="stat"><span class="muted">總市值</span><b class="num">${fmtInt(value)}</b></div>
        <div class="stat"><span class="muted">未實現損益</span><b class="num ${pctCls(pnl)}">${fmtMoney(pnl)}</b><span class="muted num ${pctCls(pnl)}">${pct(cost ? pnl / cost : null)}</span></div>
        <div class="stat"><span class="muted">今日損益</span><b class="num ${pctCls(dayPnl)}">${fmtMoney(dayPnl)}</b></div>
      </div>
      ${priced.length < items.length ? `<p class="muted" style="margin:8px 0 0">總計只含有填股數的 ${priced.length}/${items.length} 檔。</p>` : ""}
    </div>` : "";

  const cards = items.map(it => {
    const warn = it.kind !== "ok" ? `<p class="${it.kind.includes("stop") ? "down-t" : "up-t"}" style="margin:6px 0 0"><b>${ALERT_ICON[it.kind] || ""} ${esc(it.text)}</b></p>` : "";
    return `<div class="card holding" data-code="${esc(it.code)}">
      <div class="card-head">
        <a href="#/stock/${esc(it.code)}" class="name" style="text-decoration:none">${esc(it.name)}</a><span class="code">${esc(it.code)}</span>
        ${badge(it.index)}
      </div>
      <div class="hgrid num">
        <span class="muted">現價</span><b>${fmtP(it.price)}</b>
        <span class="muted">今日</span><b class="${pctCls(it.day)}">${pct(it.day)}</b>
        <span class="muted">成本</span><b>${fmtP(it.cost)}</b>
        <span class="muted">報酬</span><b class="${pctCls(it.ret)}">${pct(it.ret)}</b>
        ${it.shares ? `<span class="muted">股數</span><b>${fmtInt(it.shares)}</b><span class="muted">損益</span><b class="${pctCls(it.pnl)}">${fmtMoney(it.pnl)}</b>` : ""}
      </div>
      ${warn}
      ${it.lv ? levelsLine(it.lv, "") : ""}
      ${it.signals.length ? `<div class="chips">${it.signals.map(([t, tone]) => `<span class="chip ${tone}">${esc(t)}</span>`).join("")}</div>` : ""}
      <div class="row-actions">
        ${it.date ? `<span class="muted">買進 ${esc(it.date)}</span>` : ""}
        <button class="btn ghost" data-edit="${esc(it.code)}">編輯</button>
        <button class="btn ghost" data-del="${esc(it.code)}">刪除</button>
      </div>
    </div>`;
  }).join("");

  const ed = state.editing ? { code: state.editing, ...state.holdings[state.editing] } : {};
  const options = state.stocks.map(x => `<option value="${esc(x[0])}">${esc(x[1])}</option>`).join("");
  const form = `<div class="card" id="pf-form-card">
    <h3 style="margin:0 0 6px;font-size:15px">${state.editing ? `編輯 ${esc(ed.code)}` : "＋ 新增持股"}</h3>
    <form id="pf-form" class="hold-form">
      <label>代號 <input name="code" list="pf-stocks" required value="${esc(ed.code || "")}" ${state.editing ? "readonly" : ""} placeholder="2330" inputmode="numeric" autocomplete="off"></label>
      <label>成本 <input name="cost" type="number" step="0.01" min="0" required inputmode="decimal" value="${esc(ed.cost ?? "")}"></label>
      <label>股數 <input name="shares" type="number" step="1" min="0" inputmode="numeric" value="${esc(ed.shares ?? "")}" placeholder="1張=1000"></label>
      <label>買進日 <input name="date" type="date" value="${esc(ed.date || "")}"></label>
      <label>停損% <input name="sl" type="number" step="0.5" min="0" value="${ed.stopPct != null ? esc(+(ed.stopPct * 100).toFixed(2)) : ""}" placeholder="自動"></label>
      <label>停利% <input name="tp" type="number" step="0.5" min="0" value="${ed.takePct != null ? esc(+(ed.takePct * 100).toFixed(2)) : ""}" placeholder="自動"></label>
      <button class="btn">${state.editing ? "儲存" : "加入"}</button>
      ${state.editing ? `<button type="button" class="btn ghost" data-cancel>取消</button>` : ""}
    </form>
    <datalist id="pf-stocks">${options}</datalist>
    <p class="muted">持股只存在這台裝置，不會上傳。停損%／停利% 留空＝依波動自動計算。</p>
  </div>`;

  const backup = `<details class="card">
    <summary><b>備份／同步推播</b></summary>
    <p class="muted">下方文字可以備份、搬到另一支手機，或貼到 GitHub 的 <code>HOLDINGS</code> Secret，讓早上推播也追蹤同樣的持股。</p>
    <textarea id="pf-text" rows="5" spellcheck="false">${esc(holdingsToText(state.holdings))}</textarea>
    <div class="row-actions" style="justify-content:flex-start">
      <button class="btn" data-copy>複製</button>
      <button class="btn ghost" data-import>以上方文字取代持股</button>
    </div>
    <p class="muted" id="pf-msg"></p>
  </details>`;

  return (items.length ? summary + cards : `<div class="empty">還沒有持股。新增後會在這裡追蹤損益、停損停利與外資／新聞訊號。</div>`)
    + form + backup + disclaimer();
}

view.addEventListener("submit", e => {
  if (e.target.id !== "pf-form") return;
  e.preventDefault();
  const f = new FormData(e.target);
  const code = String(f.get("code")).trim().slice(0, 6), cost = parseFloat(f.get("cost"));
  if (!/^\w{4,6}$/.test(code) || !(cost > 0)) return;
  const shares = parseInt(f.get("shares"), 10), sl = parseFloat(f.get("sl")), tp = parseFloat(f.get("tp")), date = f.get("date");
  state.holdings[code] = {
    cost, ...(shares > 0 ? { shares } : {}), ...(date ? { date } : {}),
    ...(sl > 0 ? { stopPct: sl / 100 } : {}), ...(tp > 0 ? { takePct: tp / 100 } : {}),
  };
  saveHoldings(state.holdings);
  state.editing = null;
  render();
});
view.addEventListener("click", async e => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.edit) { state.editing = t.dataset.edit; await render(); $("#pf-form-card")?.scrollIntoView({ behavior: "smooth" }); }
  else if (t.dataset.del) {
    if (confirm(`刪除 ${t.dataset.del} 的持股紀錄？`)) { delete state.holdings[t.dataset.del]; saveHoldings(state.holdings); render(); }
  } else if ("cancel" in t.dataset) { state.editing = null; render(); }
  else if ("copy" in t.dataset) {
    const text = $("#pf-text").value;
    try { await navigator.clipboard.writeText(text); $("#pf-msg").textContent = "已複製"; }
    catch { $("#pf-text").select(); $("#pf-msg").textContent = "請手動複製選取的文字"; }
  } else if ("import" in t.dataset) {
    const parsed = parseHoldingsText($("#pf-text").value);
    const n = Object.keys(parsed).length;
    if (!n && !confirm("沒有讀到任何持股，確定要清空？")) return;
    if (n && !confirm(`以 ${n} 檔持股取代目前的持股？`)) return;
    state.holdings = parsed; saveHoldings(parsed); render();
  }
});

/* ---------- 路由 ---------- */

async function render() {
  const [, tab = "list", arg] = (location.hash || "#/list").split("/");
  document.querySelectorAll(".tabbar a").forEach(a => { if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  try {
    if (tab === "foreign") view.innerHTML = renderForeign();
    else if (tab === "news") view.innerHTML = renderNews();
    else if (tab === "backtest") view.innerHTML = await renderBacktest();
    else if (tab === "portfolio") view.innerHTML = await renderPortfolio();
    else if (tab === "stock") view.innerHTML = await renderStock(arg ? decodeURIComponent(arg) : "");
    else if (tab === "list" || !tab) view.innerHTML = await renderList();
    else view.innerHTML = empty(`找不到這個頁面，請按右上角 ↻ 更新 App`);
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
view.addEventListener("submit", e => {
  if (e.target.id !== "holding-form") return;
  e.preventDefault();
  const f = new FormData(e.target), code = e.target.dataset.code;
  const cost = parseFloat(f.get("cost"));
  if (!(cost > 0)) return;
  const sl = parseFloat(f.get("sl")), tp = parseFloat(f.get("tp"));
  const prev = state.holdings[code] || {};
  state.holdings[code] = {
    cost, ...(prev.shares ? { shares: prev.shares } : {}), ...(prev.date ? { date: prev.date } : {}),
    ...(sl > 0 ? { stopPct: sl / 100 } : {}), ...(tp > 0 ? { takePct: tp / 100 } : {}),
  };
  saveHoldings(state.holdings);
  render();
});
view.addEventListener("click", e => {
  const b = e.target.closest("button[data-remove]");
  if (!b) return;
  delete state.holdings[b.dataset.remove];
  saveHoldings(state.holdings);
  render();
});
window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });

$("#date").addEventListener("change", async e => {
  state.date = e.target.value;
  await loadDay();
  render();
});

$("#refresh").addEventListener("click", async () => {
  // 先檢查 App 本身有沒有新版；有的話換上新版後整頁重新載入
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    if (reg) {
      await reg.update();
      if (reg.installing || reg.waiting) return; // controllerchange 會觸發重新載入
    }
  } catch { /* 忽略 */ }
  cache.clear();
  state.backtest = null;
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
  const hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.register("sw.js").catch(() => {});
  // 新版 App 接手時自動重新載入一次，才能看到新畫面（第一次安裝時不重載）
  let reloaded = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (hadController && !reloaded) { reloaded = true; location.reload(); }
  });
}
boot(true);
