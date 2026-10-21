import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import worker, { signToken, verifyToken } from "../src/worker.js";

const SYNC_KEY = "test-sync-key-not-real-0123456789";
const SESSION_SECRET = "test-session-secret-not-real-abcdef";
const CLIENT_SECRET = "test-client-secret-not-real-xyz";
const CLIENT_ID = "1562504905804222494";
const KEY1 = "binance|spot|STARUSDT|4h|2026-09-01T00:00:00Z|2026-09-20T08:00:00Z";
const KEY2 = "twse|stock|2330|1d|2026-08-01T00:00:00Z|2026-09-01T00:00:00Z";
const A = "111111111111111111", B = "222222222222222222";
const SITE = "https://chasen8.github.io/head-shoulders-scanner/";

const toHex = (u8) => Array.from(u8, (b) => b.toString(16).padStart(2, "0")).join("");

function fakeKv() {
  const m = new Map();
  return {
    get: async (k) => (m.has(k) ? m.get(k) : null),
    put: async (k, v) => { m.set(k, v); },
    delete: async (k) => { m.delete(k); },
    list: async ({ prefix = "" } = {}) => ({ keys: [...m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true }),
    _m: m,
  };
}

const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const PUB_HEX = toHex(new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey)));

function mkEnv() {
  return {
    DISCORD_PUBLIC_KEY: PUB_HEX, SYNC_KEY, WATCHLIST: fakeKv(),
    DISCORD_CLIENT_ID: CLIENT_ID, DISCORD_CLIENT_SECRET: CLIENT_SECRET, SESSION_SECRET,
  };
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
/** A component / command interaction from user `uid` (in a server unless dm). */
const inter = (uid, data, { type = 3, dm = false } = {}) => {
  const user = { id: uid, username: "user" + uid.slice(0, 2) };
  return signed(dm ? { type, data, user } : { type, data, member: { user } });
};
const api = (method, body, headers = {}) =>
  new Request("https://w.example/api/watchlist", {
    method,
    headers: { Authorization: `Bearer ${SYNC_KEY}`, "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const userRec = (env, uid) => JSON.parse(env.WATCHLIST._m.get("user:" + uid) || "null");
const sessionFor = (uid, name = "alice", exp = Math.floor(Date.now() / 1000) + 3600, secret = SESSION_SECRET) =>
  signToken(secret, "s1", { u: uid, n: name, exp });
const me = async (method, path, token, body, headers = {}) =>
  new Request("https://w.example" + path, {
    method,
    headers: { Authorization: `Bearer ${await token}`, "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

/* ---------- interactions: signature ---------- */

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

/* ---------- interactions: per-user lists ---------- */

test("watch_select 加到點選者自己的清單、去重、ephemeral、users 索引", async () => {
  const env = mkEnv();
  const j = await (await call(env, await inter(A, { custom_id: "watch_select", component_type: 3, values: [KEY1, KEY2, "garbage"] }))).json();
  assert.equal(j.type, 4);
  assert.equal(j.data.flags, 64);
  assert.match(j.data.content, /已加入你的關注：STARUSDT 4h、2330 1d/);
  assert.match(j.data.content, /無法辨識/);
  assert.match(j.data.content, /\/notify/); // first time: how to switch to DMs
  const rec = userRec(env, A);
  assert.equal(rec.user_id, A);
  assert.equal(rec.username, "user11");
  assert.equal(rec.notify, "channel");
  assert.equal(rec.items.length, 2);
  assert.equal(rec.items[0].added_via, "discord");
  assert.equal(rec.items[0].market, "crypto");
  assert.equal(rec.items[1].market, "tw");
  assert.equal(rec.items[0].last_status, "watchlist");
  assert.deepEqual(JSON.parse(env.WATCHLIST._m.get("users")), [A]);

  const j2 = await (await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1] }))).json();
  assert.match(j2.data.content, /原本就在你的關注清單：1/);
  assert.equal(userRec(env, A).items.length, 2);
});

test("多使用者分離：A 加的 B 看不到；B 不能取消 A 的", async () => {
  const env = mkEnv();
  await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1] }));
  await call(env, await inter(B, { custom_id: "watch_select_2", values: [KEY2] }));
  assert.deepEqual(userRec(env, A).items.map((i) => i.pattern_key), [KEY1]);
  assert.deepEqual(userRec(env, B).items.map((i) => i.pattern_key), [KEY2]);
  assert.deepEqual(JSON.parse(env.WATCHLIST._m.get("users")).sort(), [A, B].sort());

  const lb = await (await call(env, await inter(B, { name: "watchlist" }, { type: 2 }))).json();
  assert.doesNotMatch(lb.data.content, /STARUSDT/);
  assert.match(lb.data.content, /2330 1d/);

  const r = await (await call(env, await inter(B, { custom_id: "unwatch:" + KEY1 }))).json();
  assert.match(r.data.content, /不在你的關注清單/);
  assert.equal(userRec(env, A).items.length, 1); // A's item untouched

  // the same pattern can be watched by both, independently
  await call(env, await inter(B, { custom_id: "watch_select", values: [KEY1] }));
  await call(env, await inter(A, { custom_id: "unwatch:" + KEY1 }));
  assert.equal(userRec(env, A).items.length, 0);
  assert.deepEqual(userRec(env, B).items.map((i) => i.pattern_key), [KEY2, KEY1]);
});

test("unwatch：完整 key、sha1 版本、私訊中的按鈕（interaction.user）", async () => {
  const env = mkEnv();
  await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1, KEY2] }));
  const j1 = await (await call(env, await inter(A, { custom_id: "unwatch:" + KEY1 }))).json();
  assert.equal(j1.data.flags, 64);
  assert.match(j1.data.content, /已取消關注：STARUSDT/);
  assert.equal(userRec(env, A).items.length, 1);

  const h = createHash("sha1").update(KEY2).digest("hex").slice(0, 16);
  const j2 = await (await call(env, await inter(A, { custom_id: "unwatch:" + h }, { dm: true }))).json();
  assert.match(j2.data.content, /已取消關注：2330/);
  assert.equal(userRec(env, A).items.length, 0);

  const j3 = await (await call(env, await inter(A, { custom_id: "unwatch:" + KEY1 }))).json();
  assert.match(j3.data.content, /不在你的關注清單/);
});

test("互動缺使用者 id -> 拒絕、不寫入", async () => {
  const env = mkEnv();
  const j = await (await call(env, await signed({ type: 3, data: { custom_id: "watch_select", values: [KEY1] } }))).json();
  assert.match(j.data.content, /無法辨識你的 Discord 帳號/);
  assert.equal(env.WATCHLIST._m.size, 0);
});

test("/watchlist 列出自己的清單與通知方式", async () => {
  const env = mkEnv();
  const empty = await (await call(env, await inter(A, { name: "watchlist" }, { type: 2 }))).json();
  assert.equal(empty.data.flags, 64);
  assert.match(empty.data.content, /沒有關注/);
  assert.match(empty.data.content, /通知方式：在 #關注 頻道 @你/);
  await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1] }));
  const j = await (await call(env, await inter(A, { name: "watchlist" }, { type: 2, dm: true }))).json();
  assert.match(j.data.content, /你目前關注 1 筆/);
  assert.match(j.data.content, /STARUSDT 4h/);
});

test("/notify mode:dm／channel，ephemeral，dm 時提醒隱私設定", async () => {
  const env = mkEnv();
  const j = await (await call(env, await inter(A, { name: "notify", options: [{ name: "mode", type: 3, value: "dm" }] }, { type: 2 }))).json();
  assert.equal(j.data.flags, 64);
  assert.match(j.data.content, /已改成：機器人私訊/);
  assert.match(j.data.content, /隱私設定/);
  assert.equal(userRec(env, A).notify, "dm");
  assert.deepEqual(JSON.parse(env.WATCHLIST._m.get("users")), [A]);
  const lw = await (await call(env, await inter(A, { name: "watchlist" }, { type: 2 }))).json();
  assert.match(lw.data.content, /通知方式：機器人私訊/);

  const c = await (await call(env, await inter(A, { name: "notify", options: [{ name: "mode", type: 3, value: "channel" }] }, { type: 2 }))).json();
  assert.match(c.data.content, /已改成：在 #關注 頻道 @你/);
  assert.doesNotMatch(c.data.content, /隱私設定/);
  assert.equal(userRec(env, A).notify, "channel");

  const bad = await (await call(env, await inter(A, { name: "notify", options: [{ name: "mode", value: "sms" }] }, { type: 2 }))).json();
  assert.match(bad.data.content, /用法/);
  assert.equal(userRec(env, A).notify, "channel");
});

/* ---------- server API (GitHub Actions) ---------- */

test("伺服器 API 認證：缺少／錯誤／session token -> 401；SYNC_KEY 未設定 -> 401", async () => {
  const env = mkEnv();
  assert.equal((await call(env, new Request("https://w.example/api/watchlist"))).status, 401);
  assert.equal((await call(env, api("GET", undefined, { Authorization: "Bearer wrong" }))).status, 401);
  assert.equal((await call(env, api("GET", undefined, { Authorization: "Basic x" }))).status, 401);
  assert.equal((await call(env, api("GET", undefined, { Authorization: `Bearer ${await sessionFor(A)}` }))).status, 401);
  assert.equal((await call({ ...env, SYNC_KEY: undefined }, api("GET", undefined, { Authorization: "Bearer undefined" }))).status, 401);
  const ok = await call(env, api("GET"));
  assert.equal(ok.status, 200);
  assert.deepEqual(await ok.json(), { users: [] });
});

test("GET /api/watchlist 結構；舊的共用 items 被忽略，可刪除", async () => {
  const env = mkEnv();
  env.WATCHLIST._m.set("items", JSON.stringify([{ pattern_key: KEY1, symbol: "STARUSDT" }])); // D34 leftovers
  await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1] }));
  await call(env, await inter(B, { name: "notify", options: [{ name: "mode", value: "dm" }] }, { type: 2 }));
  const j = await (await call(env, api("GET"))).json();
  assert.deepEqual(Object.keys(j), ["users"]);
  const byId = Object.fromEntries(j.users.map((u) => [u.user_id, u]));
  assert.deepEqual(Object.keys(byId).sort(), [A, B].sort());
  assert.deepEqual(Object.keys(byId[A]).sort(), ["items", "notify", "user_id", "username"]);
  assert.equal(byId[A].notify, "channel");
  assert.equal(byId[A].items[0].pattern_key, KEY1);
  assert.equal(byId[B].notify, "dm");
  assert.deepEqual(byId[B].items, []);

  const d = await (await call(env, api("POST", { delete_legacy: true }))).json();
  assert.equal(d.deleted_legacy, true);
  assert.equal(env.WATCHLIST._m.has("items"), false);
});

test("users 索引遺失時，GET 仍以 KV list 找到所有使用者", async () => {
  const env = mkEnv();
  await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1] }));
  await call(env, await inter(B, { custom_id: "watch_select", values: [KEY2] }));
  env.WATCHLIST._m.set("users", JSON.stringify([A])); // B's index write lost to a race
  const j = await (await call(env, api("GET"))).json();
  assert.deepEqual(j.users.map((u) => u.user_id).sort(), [A, B].sort());
});

test("POST update 只改指定使用者的項目", async () => {
  const env = mkEnv();
  await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1, KEY2] }));
  await call(env, await inter(B, { custom_id: "watch_select", values: [KEY1] }));
  const res = await call(env, api("POST", { update: [
    { user_id: A, pattern_key: KEY1, last_status: "confirmed", last_notified_at: "2026-10-22T08:00:00Z" },
    { user_id: A, pattern_key: KEY2, name: "台積電" },
    { user_id: "999999999999", pattern_key: KEY1, last_status: "x" },
    { user_id: "../evil", pattern_key: KEY1 },
    { pattern_key: KEY1, last_status: "nouser" },
  ] }));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, updated: 2 });
  const a = userRec(env, A).items, b = userRec(env, B).items;
  assert.equal(a[0].last_status, "confirmed");
  assert.equal(a[0].last_notified_at, "2026-10-22T08:00:00Z");
  assert.equal(a[1].name, "台積電");
  assert.equal(a[1].last_status, "watchlist"); // untouched field kept
  assert.equal(b[0].last_status, "watchlist"); // B's copy of KEY1 untouched
  assert.equal(env.WATCHLIST._m.has("user:999999999999"), false);
});

test("伺服器 POST 格式錯誤 -> 400", async () => {
  const env = mkEnv();
  const bad = new Request("https://w.example/api/watchlist", { method: "POST", headers: { Authorization: `Bearer ${SYNC_KEY}` }, body: "{oops" });
  assert.equal((await call(env, bad)).status, 400);
  assert.equal((await call(env, api("POST", { update: "x" }))).status, 400);
  assert.equal((await call(env, api("POST", []))).status, 400);
});

/* ---------- sessions ---------- */

test("session 簽章：正確、過期、竄改、別的金鑰、state token 不能當 session", async () => {
  const now = Math.floor(Date.now() / 1000);
  const good = await sessionFor(A, "alice", now + 60);
  assert.equal((await verifyToken(SESSION_SECRET, "s1", good)).u, A);
  assert.equal(await verifyToken(SESSION_SECRET, "s1", await sessionFor(A, "alice", now - 1)), null);
  assert.equal(await verifyToken(SESSION_SECRET, "s1", await sessionFor(A, "alice", now + 60, "other-secret")), null);
  const [body, sig] = good.split(".");
  const forged = Buffer.from(JSON.stringify({ u: B, n: "mallory", exp: now + 60 })).toString("base64url");
  assert.equal(await verifyToken(SESSION_SECRET, "s1", `${forged}.${sig}`), null);
  assert.equal(await verifyToken(SESSION_SECRET, "s1", `${body}.${sig.slice(0, -2)}AA`), null);
  assert.equal(await verifyToken(SESSION_SECRET, "s1", "garbage"), null);
  assert.equal(await verifyToken(undefined, "s1", good), null);
  const st = await signToken(SESSION_SECRET, "o1", { r: SITE, n: "x", exp: now + 60 });
  assert.equal(await verifyToken(SESSION_SECRET, "s1", st), null);

  const env = mkEnv();
  assert.equal((await call(env, await me("GET", "/api/me", sessionFor(A, "alice", now - 5)))).status, 401);
  assert.equal((await call(env, await me("GET", "/api/me", Promise.resolve(`${forged}.${sig}`)))).status, 401);
  assert.equal((await call(env, await me("GET", "/api/me", Promise.resolve(SYNC_KEY)))).status, 401);
  assert.equal((await call(env, new Request("https://w.example/api/me"))).status, 401);
  assert.equal((await call({ ...env, SESSION_SECRET: undefined }, await me("GET", "/api/me", good))).status, 401);
});

/* ---------- website user API ---------- */

test("使用者 API：/api/me、/api/me/watchlist add/remove、/api/me/notify，只動自己的", async () => {
  const env = mkEnv();
  await call(env, await inter(B, { custom_id: "watch_select", values: [KEY1] }));
  const tA = sessionFor(A, "alice");

  let j = await (await call(env, await me("GET", "/api/me", tA))).json();
  assert.deepEqual(j, { user_id: A, username: "alice", notify: "channel" });
  assert.deepEqual(await (await call(env, await me("GET", "/api/me/watchlist", tA))).json(), { items: [] });

  const item = { pattern_key: KEY1, symbol: "STARUSDT", timeframe: "4h", market: "crypto", exchange: "binance", last_status: "forming" };
  j = await (await call(env, await me("POST", "/api/me/watchlist", tA, { add: [item, item, { symbol: "x" }, { pattern_key: KEY2, symbol: "2330", name: "台積電" }] }))).json();
  assert.deepEqual(j.items.map((i) => i.pattern_key), [KEY1, KEY2]);
  assert.equal(j.items[0].added_via, "site");
  assert.equal(j.items[0].last_status, "forming");
  assert.equal(j.items[1].market, "tw");
  assert.deepEqual(JSON.parse(env.WATCHLIST._m.get("users")).sort(), [A, B].sort());

  j = await (await call(env, await me("POST", "/api/me/watchlist", tA, { remove: [KEY1] }))).json();
  assert.deepEqual(j.items.map((i) => i.pattern_key), [KEY2]);
  assert.deepEqual(userRec(env, B).items.map((i) => i.pattern_key), [KEY1]); // B untouched

  j = await (await call(env, await me("POST", "/api/me/notify", tA, { mode: "dm" }))).json();
  assert.deepEqual(j, { notify: "dm" });
  assert.equal(userRec(env, A).notify, "dm");
  assert.equal((await call(env, await me("POST", "/api/me/notify", tA, { mode: "email" }))).status, 400);
  assert.equal((await call(env, await me("POST", "/api/me/watchlist", tA, { add: "x" }))).status, 400);
  assert.equal((await call(env, await me("POST", "/api/me/watchlist", tA, []))).status, 400);
  assert.equal((await call(env, await me("GET", "/api/me/nothing", tA))).status, 404);
  assert.equal((await call(env, await me("GET", "/api/me/notify", tA))).status, 405);
});

test("每人上限 200 筆", async () => {
  const env = mkEnv();
  const many = Array.from({ length: 230 }, (_, i) => ({ pattern_key: `binance|spot|S${i}USDT|1d|a|b`, symbol: `S${i}USDT` }));
  const j = await (await call(env, await me("POST", "/api/me/watchlist", sessionFor(A), { add: many }))).json();
  assert.equal(j.items.length, 200);
  const keys = Array.from({ length: 25 }, (_, i) => `binance|spot|T${i}USDT|1d|a|b`);
  const r = await (await call(env, await inter(A, { custom_id: "watch_select", values: keys }))).json();
  assert.match(r.data.content, /已滿（上限 200 筆），25 筆未加入/);
});

/* ---------- OAuth ---------- */

const login = (ret) => new Request("https://w.example/auth/login?return=" + encodeURIComponent(ret));

async function startLogin(env, ret = SITE + "?market=tw#old") {
  const res = await call(env, login(ret));
  assert.equal(res.status, 302);
  const loc = new URL(res.headers.get("location"));
  const cookie = res.headers.get("set-cookie").split(";")[0];
  return { res, loc, cookie, state: loc.searchParams.get("state") };
}

test("/auth/login：302 到 Discord，參數正確，state 帶簽章與 cookie 綁定", async () => {
  const env = mkEnv();
  const { res, loc, cookie, state } = await startLogin(env);
  assert.equal(loc.origin + loc.pathname, "https://discord.com/oauth2/authorize");
  assert.equal(loc.searchParams.get("client_id"), CLIENT_ID);
  assert.equal(loc.searchParams.get("response_type"), "code");
  assert.equal(loc.searchParams.get("scope"), "identify");
  assert.equal(loc.searchParams.get("redirect_uri"), "https://w.example/auth/callback");
  const p = await verifyToken(SESSION_SECRET, "o1", state);
  assert.equal(p.r, SITE + "?market=tw"); // fragment dropped
  assert.ok(p.exp - Date.now() / 1000 <= 600 && p.exp - Date.now() / 1000 > 590);
  assert.equal(cookie, "ihs_oauth=" + p.n);
  assert.match(res.headers.get("set-cookie"), /HttpOnly; Secure; SameSite=Lax/);
  assert.ok(!res.headers.get("location").includes(CLIENT_SECRET));
});

test("/auth/login：return 白名單", async () => {
  const env = mkEnv();
  for (const ok of [SITE, "https://chasen8.github.io/", "http://127.0.0.1:8000/index.html", "http://localhost:5173/"]) {
    assert.equal((await call(env, login(ok))).status, 302, ok);
  }
  for (const bad of ["https://evil.example/", "https://chasen8.github.io.evil.com/", "https://chasen8.github.io@evil.com/",
    "http://chasen8.github.io/", "javascript:alert(1)", "http://127.0.0.1.evil.com/", "//evil.example/", "", "https://chasen8.github.io"]) {
    const res = await call(env, login(bad));
    assert.equal(res.status, 400, bad);
    assert.equal(res.headers.get("location"), null);
  }
  assert.equal((await call(env, new Request("https://w.example/auth/login"))).status, 400);
  // login not configured -> back to the site with an error code, no Discord redirect
  const res = await call({ ...env, DISCORD_CLIENT_SECRET: undefined }, login(SITE));
  assert.equal(res.headers.get("location"), SITE + "#ihs_login_error=unavailable");
});

function discordMock({ tokenStatus = 200, meStatus = 200, meBody = { id: A, username: "alice", global_name: "Alice" } } = {}) {
  const calls = [];
  const orig = globalThis.fetch;
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === "https://discord.com/api/v10/oauth2/token") {
      return new Response(JSON.stringify(tokenStatus === 200 ? { access_token: "ACCESS-TOKEN-SECRET", token_type: "Bearer" } : { error: "invalid_grant" }), { status: tokenStatus });
    }
    if (url === "https://discord.com/api/v10/users/@me") {
      return new Response(JSON.stringify(meBody), { status: meStatus });
    }
    throw new Error("unexpected fetch " + url);
  };
  return { calls, restore: () => { globalThis.fetch = orig; } };
}

const callback = (qs, cookie) => new Request("https://w.example/auth/callback?" + new URLSearchParams(qs), { headers: cookie ? { Cookie: cookie } : {} });

test("/auth/callback：換 token、取 @me、簽發 session 放在 fragment", async () => {
  const env = mkEnv();
  const { state, cookie } = await startLogin(env);
  const m = discordMock();
  try {
    const res = await call(env, callback({ code: "CODE-SECRET-123", state }, "other=1; " + cookie));
    assert.equal(res.status, 302);
    const loc = res.headers.get("location");
    assert.ok(loc.startsWith(SITE + "?market=tw#ihs_session="), loc);
    const session = loc.split("#ihs_session=")[1];
    const p = await verifyToken(SESSION_SECRET, "s1", session);
    assert.equal(p.u, A);
    assert.equal(p.n, "alice");
    assert.ok(p.exp - Date.now() / 1000 > 29 * 86400);
    assert.match(res.headers.get("set-cookie"), /ihs_oauth=; Max-Age=0/);
    assert.equal(res.headers.get("referrer-policy"), "no-referrer");
    // token exchange
    const tok = m.calls[0];
    assert.equal(tok.init.method, "POST");
    const form = new URLSearchParams(tok.init.body);
    assert.equal(form.get("grant_type"), "authorization_code");
    assert.equal(form.get("code"), "CODE-SECRET-123");
    assert.equal(form.get("client_id"), CLIENT_ID);
    assert.equal(form.get("client_secret"), CLIENT_SECRET);
    assert.equal(form.get("redirect_uri"), "https://w.example/auth/callback");
    assert.equal(m.calls[1].init.headers.authorization, "Bearer ACCESS-TOKEN-SECRET");
    // user record created; the session works on the user API
    assert.equal(userRec(env, A).username, "alice");
    const j = await (await call(env, await me("GET", "/api/me", Promise.resolve(session)))).json();
    assert.deepEqual(j, { user_id: A, username: "alice", notify: "channel" });
    for (const secret of ["CODE-SECRET-123", "ACCESS-TOKEN-SECRET", CLIENT_SECRET, SESSION_SECRET]) assert.ok(!loc.includes(secret));
  } finally {
    m.restore();
  }
});

test("/auth/callback：state 竄改／過期／缺 cookie／cookie 不符 -> 400，不呼叫 Discord", async () => {
  const env = mkEnv();
  const { state, cookie } = await startLogin(env);
  const m = discordMock();
  try {
    const expired = await signToken(SESSION_SECRET, "o1", { r: SITE, n: cookie.split("=")[1], exp: Math.floor(Date.now() / 1000) - 1 });
    const evilRet = await signToken(SESSION_SECRET, "o1", { r: "https://evil.example/", n: cookie.split("=")[1], exp: Math.floor(Date.now() / 1000) + 60 });
    const session = await sessionFor(A);
    for (const [qs, ck] of [
      [{ code: "c", state: state.slice(0, -3) + "AAA" }, cookie],
      [{ code: "c", state: expired }, cookie],
      [{ code: "c", state: evilRet }, cookie],
      [{ code: "c", state: session }, cookie],
      [{ code: "c" }, cookie],
      [{ code: "c", state }, null],
      [{ code: "c", state }, "ihs_oauth=deadbeef"],
    ]) {
      const res = await call(env, callback(qs, ck));
      assert.equal(res.status, 400);
      assert.equal(res.headers.get("location"), null);
      const t = await res.text();
      assert.ok(!t.includes(SESSION_SECRET) && !t.includes(CLIENT_SECRET));
    }
    assert.equal(m.calls.length, 0);
  } finally {
    m.restore();
  }
});

test("/auth/callback：使用者取消、Discord 失敗 -> 回網站帶錯誤碼，不洩漏 code/token", async () => {
  const env = mkEnv();
  const { state, cookie } = await startLogin(env, SITE);
  let res = await call(env, callback({ error: "access_denied", state }, cookie));
  assert.equal(res.headers.get("location"), SITE + "#ihs_login_error=cancelled");

  for (const opts of [{ tokenStatus: 400 }, { meStatus: 401 }, { meBody: { id: "not-a-snowflake" } }]) {
    const m = discordMock(opts);
    try {
      res = await call(env, callback({ code: "CODE-SECRET-123", state }, cookie));
      const loc = res.headers.get("location");
      assert.equal(loc, SITE + "#ihs_login_error=discord");
      assert.ok(!(await res.text()).includes("CODE-SECRET"));
    } finally {
      m.restore();
    }
  }
  const orig = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("network down ACCESS-TOKEN-SECRET"); };
  try {
    res = await call(env, callback({ code: "CODE-SECRET-123", state }, cookie));
    assert.equal(res.headers.get("location"), SITE + "#ihs_login_error=discord");
  } finally {
    globalThis.fetch = orig;
  }
  assert.equal(env.WATCHLIST._m.has("user:" + A), false);
});

/* ---------- CORS / errors ---------- */

test("CORS 預檢與回應標頭（伺服器 API 與使用者 API）", async () => {
  const env = mkEnv();
  for (const path of ["/api/watchlist", "/api/me", "/api/me/watchlist", "/api/me/notify"]) {
    for (const origin of ["https://chasen8.github.io", "http://127.0.0.1:8000", "http://localhost:5173"]) {
      const res = await call(env, new Request("https://w.example" + path, { method: "OPTIONS", headers: { Origin: origin } }));
      assert.equal(res.status, 204);
      assert.equal(res.headers.get("access-control-allow-origin"), origin);
      assert.match(res.headers.get("access-control-allow-methods"), /GET.*POST.*OPTIONS/);
      assert.match(res.headers.get("access-control-allow-headers"), /Authorization.*Content-Type/);
    }
    const evil = await call(env, new Request("https://w.example" + path, { method: "OPTIONS", headers: { Origin: "https://evil.example" } }));
    assert.equal(evil.headers.get("access-control-allow-origin"), null);
    const lookalike = await call(env, new Request("https://w.example" + path, { method: "OPTIONS", headers: { Origin: "http://localhost.evil.com" } }));
    assert.equal(lookalike.headers.get("access-control-allow-origin"), null);
  }
  const get = await call(env, await me("GET", "/api/me/watchlist", sessionFor(A), undefined, { Origin: "https://chasen8.github.io" }));
  assert.equal(get.headers.get("access-control-allow-origin"), "https://chasen8.github.io");
  const unauth = await call(env, new Request("https://w.example/api/me", { headers: { Origin: "https://chasen8.github.io" } }));
  assert.equal(unauth.status, 401);
  assert.equal(unauth.headers.get("access-control-allow-origin"), "https://chasen8.github.io");
});

test("錯誤回應不洩漏金鑰或內部訊息", async () => {
  const env = mkEnv();
  const secrets = [SYNC_KEY, PUB_HEX, SESSION_SECRET, CLIENT_SECRET];
  env.WATCHLIST.get = async () => { throw new Error("boom " + secrets.join(" ")); };
  const responses = [
    await call(env, api("GET")),
    await call(env, api("GET", undefined, { Authorization: "Bearer wrong" })),
    await call(env, new Request("https://w.example/nothing")),
    await call(env, await inter(A, { custom_id: "watch_select", values: [KEY1] })),
    await call(env, await signed({ type: 1 }, { badKey: true })),
    await call(env, await me("GET", "/api/me/watchlist", sessionFor(A))),
    await call(env, login("https://evil.example/")),
  ];
  assert.equal(responses[0].status, 500);
  assert.equal(responses[5].status, 500);
  for (const r of responses) {
    const t = await r.text() + (r.headers.get("location") || "");
    for (const s of secrets) assert.ok(!t.includes(s), "secret leaked");
    assert.ok(!t.includes("boom"), "internal error text leaked");
  }
});
