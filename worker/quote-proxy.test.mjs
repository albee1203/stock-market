// 執行：node --test worker/quote-proxy.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import worker, { normalize } from "./quote-proxy.js";

const MIS_SAMPLE = {
  msgArray: [
    { c: "2330", n: "台積電", z: "1180.0000", y: "1175.0000", o: "1178.0000", h: "1185.0000", l: "1170.0000", v: "12345", t: "13:25:31", d: "20261117", a: "1180.0000_1185.0000_", b: "1175.0000_1170.0000_" },
    { c: "8069", n: "元太", z: "-", y: "238.6000", o: "239.0000", h: "240.0000", l: "236.0000", v: "800", t: "13:25:30", d: "20261117", a: "238.5000_239.0000_", b: "238.0000_237.5000_" },
  ],
};

function mockFetch(responses) {
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: String(url), opts });
    const r = responses.shift();
    return new Response(typeof r === "string" ? r : JSON.stringify(r), { status: 200, headers: { "set-cookie": "JSESSIONID=abc; Path=/" } });
  };
  return calls;
}

test("normalize：有成交用成交價，沒成交用買賣中間價", () => {
  const a = normalize(MIS_SAMPLE.msgArray[0]);
  assert.equal(a.price, 1180); assert.equal(a.prev, 1175); assert.equal(a.change, 5);
  assert.equal(a.change_pct, +(5 / 1175).toFixed(5)); assert.equal(a.volume, 12345); assert.equal(a.source, "trade");
  const b = normalize(MIS_SAMPLE.msgArray[1]);
  assert.equal(b.price, 238.25); assert.equal(b.source, "quote");
  const c = normalize({ c: "1101", z: "-", y: "40.0", a: "-", b: "-" });
  assert.equal(c.price, 40); assert.equal(c.source, "prev"); assert.equal(c.change, 0);
});

test("查詢：驗證參數、組出證交所網址、回傳 CORS 標頭", async () => {
  const calls = mockFetch([MIS_SAMPLE]);
  const req = new Request("https://x.workers.dev/?q=tse_2330,otc_8069,bad,tse_2330", { headers: { Origin: "https://albee1203.github.io" } });
  const res = await worker.fetch(req, {}, null);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://albee1203.github.io");
  const body = await res.json();
  assert.deepEqual(Object.keys(body.quotes).sort(), ["2330", "8069"]);
  assert.match(calls[0].url, /ex_ch=otc_8069\.tw\|tse_2330\.tw&json=1/);
});

test("第一次查不到時，先取得 cookie 再查一次", async () => {
  const calls = mockFetch([{ msgArray: [] }, "<html></html>", MIS_SAMPLE]);
  const res = await worker.fetch(new Request("https://x.workers.dev/?q=tse_2330"), {}, null);
  assert.equal(Object.keys((await res.json()).quotes).length, 2);
  assert.equal(calls.length, 3);
  assert.equal(calls[2].opts.headers.Cookie, "JSESSIONID=abc");
});

test("拒絕其他網站呼叫", async () => {
  mockFetch([MIS_SAMPLE]);
  const res = await worker.fetch(new Request("https://x.workers.dev/?q=tse_2330", { headers: { Origin: "https://evil.example" } }), {}, null);
  assert.equal(res.status, 403);
});

test("證交所錯誤時回 502", async () => {
  globalThis.fetch = async () => new Response("err", { status: 503 });
  const res = await worker.fetch(new Request("https://x.workers.dev/?q=tse_2330"), {}, null);
  assert.equal(res.status, 502);
  assert.match((await res.json()).error, /503/);
});
