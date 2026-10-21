import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import worker from "../src/worker.js";

const SYNC_KEY = "test-sync-key-not-real-0123456789";
const KEY1 = "binance|spot|STARUSDT|4h|2026-09-01T00:00:00Z|2026-09-20T08:00:00Z";
const KEY2 = "twse|stock|2330|1d|2026-08-01T00:00:00Z|2026-09-01T00:00:00Z";

const toHex = (u8) => Array.from(u8, (b) => b.toString(16).padStart(2, "0")).join("");

function fakeKv() {
  const m = new Map();
  return { get: async (k) => (m.has(k) ? m.get(k) : null), put: async (k, v) => { m.set(k, v); }, _m: m };
}

const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const PUB_HEX = toHex(new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey)));

function mkEnv() {
  return { DISCORD_PUBLIC_KEY: PUB_HEX, SYNC_KEY, WATCHLIST: fakeKv() };
}

async function signed(body, { tamper = false, badKey = false } = {}) {
  const raw = JSON.stringify(body);
  const ts = String(Math.floor(Date.now() / 1000));
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, pair.privateKey, new TextEncoder().encode(ts + raw)));
  if (badKey) sig[0] ^= 0xff;
  return new Request("https://w.example/interactions", {
    method: "POST",
    headers: { "X-Signature-Ed25519": toHex(sig), "X-Signature-Timestamp": ts, "content-type": "application/json" },
    body: tamper ? raw.replace("1", "2") : raw,
  });
}

const call = (env, req) => worker.fetch(req, env);
const api = (method, body, headers = {}) =>
  new Request("https://w.example/api/watchlist", {
    method,
    headers: { Authorization: `Bearer ${SYNC_KEY}`, "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const stored = (env) => JSON.parse(env.WATCHLIST._m.get("items") || "[]");

test("PING 簽章正確 -> type 1", async () => {
  const res = await call(mkEnv(), await signed({ type: 1 }));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { type: 1 });
});

test("簽章錯誤／內容被改／缺標頭 -> 401", async () => {
  const env = mkEnv();
  assert.equal((await call(env, await signed({ type: 1 }, { badKey: true }))).status, 401);
  assert.equal((await call(env, await signed({ type: 1, x: "1" }, { tamper: true }))).status, 401);
  const r = new Request("https://w.example/interactions", { method: "POST", body: "{}" });
  assert.equal((await call(env, r)).status, 401);
  const noKey = { ...env, DISCORD_PUBLIC_KEY: undefined };
  assert.equal((await call(noKey, await signed({ type: 1 }))).status, 401);
});

test("watch_select 加入、去重、ephemeral", async () => {
  const env = mkEnv();
  const inter = { type: 3, data: { custom_id: "watch_select", component_type: 3, values: [KEY1, KEY2, "garbage"] } };
  const res = await call(env, await signed(inter));
  const j = await res.json();
  assert.equal(j.type, 4);
  assert.equal(j.data.flags, 64);
  assert.match(j.data.content, /已加入關注：STARUSDT 4h、2330 1d/);
  assert.match(j.data.content, /無法辨識/);
  let items = stored(env);
  assert.equal(items.length, 2);
  assert.equal(items[0].added_via, "discord");
  assert.equal(items[0].market, "crypto");
  assert.equal(items[1].market, "tw");
  assert.equal(items[0].last_status, "watchlist");

  const res2 = await call(env, await signed({ type: 3, data: { custom_id: "watch_select", values: [KEY1] } }));
  const j2 = await res2.json();
  assert.match(j2.data.content, /原本就在關注清單：1/);
  assert.equal(stored(env).length, 2);
});

test("unwatch 按鈕：完整 key 與 sha1 版本", async () => {
  const env = mkEnv();
  await call(env, await signed({ type: 3, data: { custom_id: "watch_select", values: [KEY1, KEY2] } }));
  const r1 = await call(env, await signed({ type: 3, data: { custom_id: "unwatch:" + KEY1 } }));
  const j1 = await r1.json();
  assert.equal(j1.data.flags, 64);
  assert.match(j1.data.content, /已取消關注：STARUSDT/);
  assert.equal(stored(env).length, 1);

  const h = createHash("sha1").update(KEY2).digest("hex").slice(0, 16);
  const r2 = await call(env, await signed({ type: 3, data: { custom_id: "unwatch:" + h } }));
  assert.match((await r2.json()).data.content, /已取消關注：2330/);
  assert.equal(stored(env).length, 0);

  const r3 = await call(env, await signed({ type: 3, data: { custom_id: "unwatch:" + KEY1 } }));
  assert.match((await r3.json()).data.content, /已經不在/);
});

test("/watchlist 斜線指令列出清單", async () => {
  const env = mkEnv();
  const empty = await (await call(env, await signed({ type: 2, data: { name: "watchlist" } }))).json();
  assert.match(empty.data.content, /沒有關注/);
  await call(env, await signed({ type: 3, data: { custom_id: "watch_select", values: [KEY1] } }));
  const j = await (await call(env, await signed({ type: 2, data: { name: "watchlist" } }))).json();
  assert.equal(j.data.flags, 64);
  assert.match(j.data.content, /STARUSDT 4h/);
});

test("API 認證：缺少／錯誤 -> 401；SYNC_KEY 未設定 -> 401", async () => {
  const env = mkEnv();
  assert.equal((await call(env, new Request("https://w.example/api/watchlist"))).status, 401);
  assert.equal((await call(env, api("GET", undefined, { Authorization: "Bearer wrong" }))).status, 401);
  assert.equal((await call(env, api("GET", undefined, { Authorization: "Basic x" }))).status, 401);
  assert.equal((await call({ ...env, SYNC_KEY: undefined }, api("GET", undefined, { Authorization: "Bearer undefined" }))).status, 401);
  const ok = await call(env, api("GET"));
  assert.equal(ok.status, 200);
  assert.deepEqual(await ok.json(), { items: [] });
});

test("POST add / remove / update", async () => {
  const env = mkEnv();
  const item = { pattern_key: KEY1, symbol: "STARUSDT", timeframe: "4h", market: "crypto", added_via: "site", exchange: "binance" };
  let j = await (await call(env, api("POST", { add: [item, item, { symbol: "x" }] }))).json();
  assert.equal(j.items.length, 1);
  assert.equal(j.items[0].added_via, "site");
  assert.equal(j.items[0].last_status, null);

  j = await (await call(env, api("POST", { add: [{ pattern_key: KEY2, symbol: "2330", name: "台積電" }] }))).json();
  assert.equal(j.items.length, 2);
  assert.equal(j.items[1].market, "tw");
  assert.equal(j.items[1].added_via, "api");

  j = await (await call(env, api("POST", { update: [{ pattern_key: KEY1, last_status: "confirmed", last_notified_at: "2026-10-22T08:00:00Z" }, { pattern_key: "nope" }] }))).json();
  assert.equal(j.items[0].last_status, "confirmed");
  assert.equal(j.items[0].last_notified_at, "2026-10-22T08:00:00Z");

  j = await (await call(env, api("POST", { remove: [KEY1] }))).json();
  assert.deepEqual(j.items.map((i) => i.pattern_key), [KEY2]);
  assert.equal(stored(env).length, 1);
});

test("POST 格式錯誤 -> 400；上限 500", async () => {
  const env = mkEnv();
  const bad = new Request("https://w.example/api/watchlist", { method: "POST", headers: { Authorization: `Bearer ${SYNC_KEY}` }, body: "{oops" });
  assert.equal((await call(env, bad)).status, 400);
  assert.equal((await call(env, api("POST", { add: "x" }))).status, 400);
  assert.equal((await call(env, api("POST", []))).status, 400);

  const many = Array.from({ length: 520 }, (_, i) => ({ pattern_key: `binance|spot|S${i}USDT|1d|a|b`, symbol: `S${i}USDT` }));
  const j = await (await call(env, api("POST", { add: many }))).json();
  assert.equal(j.items.length, 500);
});

test("CORS 預檢與回應標頭", async () => {
  const env = mkEnv();
  for (const origin of ["https://chasen8.github.io", "http://127.0.0.1:8000", "http://localhost:5173"]) {
    const res = await call(env, new Request("https://w.example/api/watchlist", { method: "OPTIONS", headers: { Origin: origin } }));
    assert.equal(res.status, 204);
    assert.equal(res.headers.get("access-control-allow-origin"), origin);
    assert.match(res.headers.get("access-control-allow-methods"), /GET.*POST.*OPTIONS/);
    assert.match(res.headers.get("access-control-allow-headers"), /Authorization.*Content-Type/);
  }
  const evil = await call(env, new Request("https://w.example/api/watchlist", { method: "OPTIONS", headers: { Origin: "https://evil.example" } }));
  assert.equal(evil.headers.get("access-control-allow-origin"), null);
  const lookalike = await call(env, new Request("https://w.example/api/watchlist", { method: "OPTIONS", headers: { Origin: "http://localhost.evil.com" } }));
  assert.equal(lookalike.headers.get("access-control-allow-origin"), null);

  const get = await call(env, api("GET", undefined, { Origin: "https://chasen8.github.io" }));
  assert.equal(get.headers.get("access-control-allow-origin"), "https://chasen8.github.io");
  const unauth = await call(env, new Request("https://w.example/api/watchlist", { headers: { Origin: "https://chasen8.github.io" } }));
  assert.equal(unauth.status, 401);
  assert.equal(unauth.headers.get("access-control-allow-origin"), "https://chasen8.github.io");
});

test("錯誤回應不洩漏金鑰", async () => {
  const env = mkEnv();
  env.WATCHLIST.get = async () => { throw new Error("boom " + SYNC_KEY + " " + PUB_HEX); };
  const responses = [
    await call(env, api("GET")),
    await call(env, api("GET", undefined, { Authorization: "Bearer wrong" })),
    await call(env, new Request("https://w.example/nothing")),
    await call(env, await signed({ type: 3, data: { custom_id: "watch_select", values: [KEY1] } })),
    await call(env, await signed({ type: 1 }, { badKey: true })),
  ];
  assert.equal(responses[0].status, 500);
  for (const r of responses) {
    const t = await r.text();
    assert.ok(!t.includes(SYNC_KEY), "sync key leaked");
    assert.ok(!t.includes(PUB_HEX), "public key leaked");
    assert.ok(!t.includes("boom"), "internal error text leaked");
  }
});
