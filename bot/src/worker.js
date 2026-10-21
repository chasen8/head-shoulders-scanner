// Cloudflare Worker: Discord interactions endpoint + per-user watchlists + "log in with Discord" for the website
// (docs/watchlist-bot-design.md, D35). Plain ES module, no dependencies, no build step.
// Secrets (DISCORD_PUBLIC_KEY, SYNC_KEY, DISCORD_CLIENT_SECRET, SESSION_SECRET) come from the Worker environment
// only and are never echoed in any response; OAuth codes, access tokens and sessions are never logged.

const LEGACY_KEY = "items"; // D34 shared list: ignored (nobody knows who added what), can be deleted via the API
const USERS_KEY = "users";
const USER_PREFIX = "user:";
const MAX_ITEMS = 200; // per user
const MAX_USERS = 1000;
const MAX_BODY = 256 * 1024;
const MAX_KEY_LEN = 300;
const TIMEFRAMES = new Set(["4h", "1d", "1w"]);
const VIA = new Set(["discord", "site", "api"]);
const NOTIFY = new Set(["channel", "dm"]);
const EPHEMERAL = 64;
const ALLOWED_ORIGINS = new Set(["https://chasen8.github.io"]);
const LOCAL_ORIGIN = /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/;
const RETURN_OK = /^(https:\/\/chasen8\.github\.io\/|http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?\/)/;
const USER_ID = /^\d{5,25}$/;
const STATE_TTL = 600; // s
const SESSION_TTL = 30 * 86400; // s
const OAUTH_COOKIE = "ihs_oauth";
const DISCORD_API = "https://discord.com/api/v10";
const NOTIFY_TEXT = { channel: "在 #關注 頻道 @你", dm: "機器人私訊" };
const DM_HINT = "若收不到私訊，請到這個伺服器的「隱私設定」打開「允許來自伺服器成員的私人訊息」。";

const enc = new TextEncoder();

/* ---------- small helpers ---------- */

function hexToBytes(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function bytesToHex(buf) {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]*$/.test(text)) return null;
  try {
    const s = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4));
    return Uint8Array.from(s, (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
}

export async function sha1Prefix(text) {
  const d = await crypto.subtle.digest("SHA-1", enc.encode(text));
  return bytesToHex(d).slice(0, 16);
}

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra },
  });
}

function htmlError(status, text) {
  const safe = text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  return new Response(`<!doctype html><meta charset="utf-8"><title>登入失敗</title><p>${safe}</p>`, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "referrer-policy": "no-referrer" },
  });
}

function redirect(location, extra = {}) {
  return new Response(null, {
    status: 302,
    headers: { location, "cache-control": "no-store", "referrer-policy": "no-referrer", ...extra },
  });
}

function corsHeaders(request) {
  const origin = request.headers.get("Origin");
  if (origin && (ALLOWED_ORIGINS.has(origin) || LOCAL_ORIGIN.test(origin))) {
    return {
      "access-control-allow-origin": origin,
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "Authorization, Content-Type",
      "access-control-max-age": "86400",
      vary: "Origin",
    };
  }
  return { vary: "Origin" };
}

/** Constant-time string comparison (hash both sides first so lengths do not leak). */
async function safeEqual(a, b) {
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(String(a))),
    crypto.subtle.digest("SHA-256", enc.encode(String(b))),
  ]);
  const x = new Uint8Array(ha), y = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function bearer(request) {
  const m = /^Bearer (.+)$/.exec(request.headers.get("Authorization") || "");
  return m ? m[1] : null;
}

async function serverAuthorized(request, env) {
  if (!env.SYNC_KEY) return false;
  const t = bearer(request);
  return t ? safeEqual(t, env.SYNC_KEY) : false;
}

/* ---------- signed tokens (OAuth state, website session): base64url(JSON) "." base64url(HMAC-SHA256) ---------- */

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

/** kind ("o1" state / "s1" session) is part of the signed text, so one token type can never pass as the other. */
export async function signToken(secret, kind, payload) {
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(`${kind}.${body}`));
  return `${body}.${b64url(sig)}`;
}

/** -> payload, or null when malformed / bad signature / expired (payload.exp, seconds). */
export async function verifyToken(secret, kind, token, nowSec = Math.floor(Date.now() / 1000)) {
  if (!secret || typeof token !== "string" || token.length > 2048) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const sig = unb64url(parts[1]);
  const body = unb64url(parts[0]);
  if (!sig || !body) return null;
  let ok = false;
  try {
    ok = await crypto.subtle.verify("HMAC", await hmacKey(secret), sig, enc.encode(`${kind}.${parts[0]}`));
  } catch {
    return null;
  }
  if (!ok) return null;
  let p;
  try { p = JSON.parse(new TextDecoder().decode(body)); } catch { return null; }
  if (!p || typeof p !== "object" || typeof p.exp !== "number" || p.exp <= nowSec) return null;
  return p;
}

async function sessionUser(request, env) {
  const p = await verifyToken(env.SESSION_SECRET, "s1", bearer(request));
  if (!p || typeof p.u !== "string" || !USER_ID.test(p.u)) return null;
  return { user_id: p.u, username: typeof p.n === "string" ? p.n : null };
}

/* ---------- pattern_key / items ---------- */

/** keys.py format: exchange|market|symbol|timeframe|LS_time|H_time -> parts or null. */
export function parseKey(key) {
  if (typeof key !== "string" || !key || key.length > MAX_KEY_LEN) return null;
  const p = key.split("|");
  if (p.length !== 6 || !p[0] || !p[1] || !p[2] || !TIMEFRAMES.has(p[3])) return null;
  return {
    exchange: p[0],
    symbol: p[2],
    timeframe: p[3],
    market: p[0] === "twse" || p[0] === "tpex" ? "tw" : "crypto",
  };
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function optStr(x, max = 80) {
  return typeof x === "string" && x ? x.slice(0, max) : null;
}

function optNum(x) {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

function validTime(x) {
  return typeof x === "string" && !Number.isNaN(Date.parse(x));
}

/** Validate and normalize one incoming item. Returns null when unusable. */
export function normalizeItem(raw, defaultVia = "api") {
  if (!raw || typeof raw !== "object") return null;
  const key = raw.pattern_key;
  if (typeof key !== "string" || !key || key.length > MAX_KEY_LEN) return null;
  const parsed = parseKey(key);
  const symbol = optStr(raw.symbol, 40) || (parsed && parsed.symbol);
  if (!symbol) return null;
  const via = VIA.has(raw.added_via) ? raw.added_via : defaultVia;
  const item = {
    pattern_key: key,
    market: raw.market === "tw" || raw.market === "crypto" ? raw.market : parsed ? parsed.market : "crypto",
    symbol,
    name: optStr(raw.name),
    timeframe: TIMEFRAMES.has(raw.timeframe) ? raw.timeframe : parsed ? parsed.timeframe : null,
    added_at: validTime(raw.added_at) ? raw.added_at : nowIso(),
    added_via: via,
    last_status: optStr(raw.last_status, 20),
    last_notified_at: validTime(raw.last_notified_at) ? raw.last_notified_at : null,
  };
  // Optional extras the website keeps so another browser can rebuild its own rows.
  const exchange = optStr(raw.exchange, 20) || (parsed && parsed.exchange);
  if (exchange) item.exchange = exchange;
  if (optStr(raw.tv_symbol)) item.tv_symbol = optStr(raw.tv_symbol);
  if (optStr(raw.status_at_add, 20)) item.status_at_add = optStr(raw.status_at_add, 20);
  if (optNum(raw.score_at_add) !== null) item.score_at_add = optNum(raw.score_at_add);
  return item;
}

/** Add items (dedup by pattern_key). Returns { items, added: [item], full: n }. */
export function addItems(items, incoming, via) {
  const have = new Map(items.map((it) => [it.pattern_key, it]));
  const out = items.slice();
  const added = [];
  let full = 0;
  for (const raw of incoming) {
    const it = normalizeItem(raw, via);
    if (!it) continue;
    const cur = have.get(it.pattern_key);
    if (cur) {
      if (!cur.name && it.name) cur.name = it.name;
      continue;
    }
    if (out.length >= MAX_ITEMS) { full++; continue; }
    have.set(it.pattern_key, it);
    out.push(it);
    added.push(it);
  }
  return { items: out, added, full };
}

function label(it) {
  return `${it.symbol}${it.name ? " " + it.name : ""} ${it.timeframe || ""}`.trim();
}

/* ---------- per-user records: KV "user:<id>" = {user_id, username, notify, items}; "users" = [id...] ---------- */

function emptyUser(userId, username) {
  return { user_id: userId, username: optStr(username, 40), notify: "channel", items: [] };
}

async function loadUser(env, userId) {
  const raw = await env.WATCHLIST.get(USER_PREFIX + userId);
  if (!raw) return null;
  try {
    const u = JSON.parse(raw);
    if (!u || typeof u !== "object") return null;
    return {
      user_id: userId,
      username: optStr(u.username, 40),
      notify: NOTIFY.has(u.notify) ? u.notify : "channel",
      items: Array.isArray(u.items) ? u.items.filter((i) => i && typeof i.pattern_key === "string") : [],
    };
  } catch {
    return null;
  }
}

async function loadIndex(env) {
  const raw = await env.WATCHLIST.get(USERS_KEY);
  if (!raw) return [];
  try {
    const a = JSON.parse(raw);
    return Array.isArray(a) ? a.filter((x) => typeof x === "string" && USER_ID.test(x)) : [];
  } catch {
    return [];
  }
}

async function saveUser(env, user, isNew) {
  await env.WATCHLIST.put(USER_PREFIX + user.user_id, JSON.stringify(user));
  if (isNew) {
    const idx = await loadIndex(env);
    if (!idx.includes(user.user_id)) {
      idx.push(user.user_id);
      await env.WATCHLIST.put(USERS_KEY, JSON.stringify(idx));
    }
  }
}

/** Load (or start) one user's record; refreshes the stored username when a newer one is known. */
async function getUser(env, userId, username) {
  const u = await loadUser(env, userId);
  if (u) {
    const fresh = optStr(username, 40);
    return { user: fresh && fresh !== u.username ? { ...u, username: fresh } : u, isNew: false };
  }
  return { user: emptyUser(userId, username), isNew: true };
}

/** Every user id: the index, plus a KV prefix listing when available (repairs an index write lost to a race). */
async function allUserIds(env) {
  const ids = await loadIndex(env);
  const seen = new Set(ids);
  if (typeof env.WATCHLIST.list === "function") {
    let cursor;
    for (let page = 0; page < 10; page++) {
      const res = await env.WATCHLIST.list({ prefix: USER_PREFIX, cursor });
      for (const k of res.keys || []) {
        const id = String(k.name).slice(USER_PREFIX.length);
        if (USER_ID.test(id) && !seen.has(id)) { seen.add(id); ids.push(id); }
      }
      if (res.list_complete || !res.cursor) break;
      cursor = res.cursor;
    }
  }
  return ids.slice(0, MAX_USERS);
}

function removeFromItems(items, refs) {
  const rm = new Set(refs.filter((k) => typeof k === "string"));
  return items.filter((it) => !rm.has(it.pattern_key));
}

/* ---------- Discord signature ---------- */

async function verifyDiscord(request, rawBody, env) {
  const sig = hexToBytes(request.headers.get("X-Signature-Ed25519") || "");
  const ts = request.headers.get("X-Signature-Timestamp");
  const pub = hexToBytes(env.DISCORD_PUBLIC_KEY || "");
  if (!sig || !ts || !pub) return false;
  try {
    const key = await crypto.subtle.importKey("raw", pub, { name: "Ed25519" }, false, ["verify"]);
    const msg = new Uint8Array(enc.encode(ts).length + rawBody.length);
    msg.set(enc.encode(ts), 0);
    msg.set(rawBody, enc.encode(ts).length);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, sig, msg);
  } catch {
    return false;
  }
}

function reply(content) {
  return json({ type: 4, data: { content: content.slice(0, 1900), flags: EPHEMERAL, allowed_mentions: { parse: [] } } });
}

/* ---------- Discord interactions (every action works on the clicking user's own list only) ---------- */

/** The user behind an interaction: member.user inside a server, user in a DM. */
function interactionUser(body) {
  const u = (body.member && body.member.user) || body.user;
  if (!u || typeof u.id !== "string" || !USER_ID.test(u.id)) return null;
  return { id: u.id, username: typeof u.username === "string" ? u.username : null };
}

async function handleInteraction(body, env) {
  if (body.type === 1) return json({ type: 1 });
  const who = interactionUser(body);

  if (body.type === 3 && body.data) {
    if (!who) return reply("無法辨識你的 Discord 帳號。");
    const cid = String(body.data.custom_id || "");
    if (cid === "watch_select" || cid.startsWith("watch_select_")) { // a table message may carry up to 5 menus
      const values = Array.isArray(body.data.values) ? body.data.values : [];
      const valid = [];
      let bad = 0;
      for (const v of values.slice(0, 25)) {
        if (parseKey(v)) valid.push({ pattern_key: v, added_via: "discord", last_status: "watchlist" });
        else bad++;
      }
      if (!valid.length) return reply("沒有可加入的項目（選項格式無法辨識）。");
      const { user, isNew } = await getUser(env, who.id, who.username);
      const res = addItems(user.items, valid, "discord");
      if (res.added.length || isNew) await saveUser(env, { ...user, items: res.items }, isNew);
      const addedKeys = new Set(res.added.map((a) => a.pattern_key));
      const dupes = valid.filter((v) => !addedKeys.has(v.pattern_key));
      const lines = [];
      if (res.added.length) lines.push("已加入你的關注：" + res.added.map(label).join("、"));
      if (dupes.length - res.full > 0) lines.push(`原本就在你的關注清單：${dupes.length - res.full} 筆`);
      if (res.full) lines.push(`你的關注清單已滿（上限 ${MAX_ITEMS} 筆），${res.full} 筆未加入`);
      if (bad) lines.push(`${bad} 筆格式無法辨識，已略過`);
      if (isNew) lines.push(`狀態改變時會${NOTIFY_TEXT.channel}通知；要改成私訊請用 /notify。`);
      return reply(lines.join("\n"));
    }
    if (cid.startsWith("unwatch:")) {
      const ref = cid.slice("unwatch:".length);
      const user = await loadUser(env, who.id);
      let hit = null;
      for (const it of user ? user.items : []) {
        if (it.pattern_key === ref || (/^[0-9a-f]{16}$/.test(ref) && (await sha1Prefix(it.pattern_key)) === ref)) {
          hit = it;
          break;
        }
      }
      if (!hit) return reply("這筆不在你的關注清單（可能已取消，或是別人關注的）。");
      await saveUser(env, { ...user, items: user.items.filter((it) => it !== hit) }, false);
      return reply("已取消關注：" + label(hit));
    }
    return reply("不認得這個操作。");
  }

  if (body.type === 2 && body.data) {
    if (!who) return reply("無法辨識你的 Discord 帳號。");
    if (body.data.name === "watchlist") {
      const user = await loadUser(env, who.id);
      const mode = user ? user.notify : "channel";
      const tail = `\n通知方式：${NOTIFY_TEXT[mode]}（用 /notify 修改）`;
      if (!user || !user.items.length) return reply("你目前沒有關注任何標的。" + tail);
      const items = user.items;
      const lines = items.slice(0, 40).map((it, i) => `${i + 1}. ${label(it)}${it.last_status ? "（" + it.last_status + "）" : ""}`);
      const more = items.length > 40 ? `\n…另有 ${items.length - 40} 筆` : "";
      return reply(`你目前關注 ${items.length} 筆：\n` + lines.join("\n") + more + tail);
    }
    if (body.data.name === "notify") {
      const opt = (Array.isArray(body.data.options) ? body.data.options : []).find((o) => o && o.name === "mode");
      const mode = opt && NOTIFY.has(opt.value) ? opt.value : null;
      if (!mode) return reply("用法：/notify mode:channel（在 #關注 頻道 @你）或 /notify mode:dm（私訊）");
      const { user, isNew } = await getUser(env, who.id, who.username);
      await saveUser(env, { ...user, notify: mode }, isNew);
      return reply(`已改成：${NOTIFY_TEXT[mode]}。` + (mode === "dm" ? "\n" + DM_HINT : ""));
    }
  }

  return reply("不認得這個操作。");
}

/* ---------- body parsing ---------- */

async function readJson(request) {
  const text = await request.text();
  if (text.length > MAX_BODY) return null;
  let body;
  try { body = JSON.parse(text); } catch { return null; }
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  return body;
}

/* ---------- server API (GitHub Actions): Bearer SYNC_KEY ---------- */

async function handleServerApi(request, env, cors) {
  if (!(await serverAuthorized(request, env))) return json({ error: "unauthorized" }, 401, cors);

  if (request.method === "GET") {
    const users = [];
    for (const id of await allUserIds(env)) {
      const u = await loadUser(env, id);
      if (u) users.push(u);
    }
    return json({ users }, 200, cors);
  }

  const body = await readJson(request);
  if (!body || (body.update !== undefined && !Array.isArray(body.update))) return json({ error: "bad_request" }, 400, cors);

  let deletedLegacy = false;
  if (body.delete_legacy === true && typeof env.WATCHLIST.delete === "function") {
    await env.WATCHLIST.delete(LEGACY_KEY);
    deletedLegacy = true;
  }
  const byUser = new Map();
  for (const u of Array.isArray(body.update) ? body.update : []) {
    if (!u || typeof u.user_id !== "string" || !USER_ID.test(u.user_id) || typeof u.pattern_key !== "string") continue;
    if (!byUser.has(u.user_id)) byUser.set(u.user_id, []);
    byUser.get(u.user_id).push(u);
  }
  let updated = 0;
  for (const [id, ups] of byUser) {
    const user = await loadUser(env, id);
    if (!user) continue;
    const byKey = new Map(user.items.map((it) => [it.pattern_key, it]));
    let touched = false;
    for (const u of ups) {
      const it = byKey.get(u.pattern_key);
      if (!it) continue;
      if ("last_status" in u) it.last_status = optStr(u.last_status, 20);
      if ("last_notified_at" in u) it.last_notified_at = validTime(u.last_notified_at) ? u.last_notified_at : null;
      if ("name" in u && optStr(u.name)) it.name = optStr(u.name);
      touched = true;
      updated++;
    }
    if (touched) await saveUser(env, user, false);
  }
  return json({ ok: true, updated, ...(deletedLegacy ? { deleted_legacy: true } : {}) }, 200, cors);
}

/* ---------- website user API: Bearer <session> ---------- */

async function handleMeApi(request, env, cors, path) {
  const me = await sessionUser(request, env);
  if (!me) return json({ error: "unauthorized" }, 401, cors);

  if (path === "/api/me") {
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405, cors);
    const u = await loadUser(env, me.user_id);
    return json({ user_id: me.user_id, username: (u && u.username) || me.username, notify: u ? u.notify : "channel" }, 200, cors);
  }

  if (path === "/api/me/watchlist") {
    if (request.method === "GET") {
      const u = await loadUser(env, me.user_id);
      return json({ items: u ? u.items : [] }, 200, cors);
    }
    const body = await readJson(request);
    if (!body) return json({ error: "bad_request" }, 400, cors);
    for (const f of ["add", "remove"]) {
      if (body[f] !== undefined && !Array.isArray(body[f])) return json({ error: "bad_request" }, 400, cors);
    }
    const { user, isNew } = await getUser(env, me.user_id, me.username);
    let items = user.items;
    if (Array.isArray(body.remove) && body.remove.length) items = removeFromItems(items, body.remove);
    if (Array.isArray(body.add) && body.add.length) items = addItems(items, body.add, "site").items;
    await saveUser(env, { ...user, items }, isNew);
    return json({ items }, 200, cors);
  }

  if (path === "/api/me/notify") {
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405, cors);
    const body = await readJson(request);
    if (!body || !NOTIFY.has(body.mode)) return json({ error: "bad_request" }, 400, cors);
    const { user, isNew } = await getUser(env, me.user_id, me.username);
    await saveUser(env, { ...user, notify: body.mode }, isNew);
    return json({ notify: body.mode }, 200, cors);
  }

  return json({ error: "not_found" }, 404, cors);
}

/* ---------- "log in with Discord" (OAuth2 authorization code, scope identify) ---------- */

function cleanReturn(raw) {
  if (typeof raw !== "string" || raw.length > 1000 || !RETURN_OK.test(raw) || /[\s\\]/.test(raw)) return null;
  return raw.split("#")[0];
}

function loginConfigured(env) {
  return !!(env.DISCORD_CLIENT_ID && env.DISCORD_CLIENT_SECRET && env.SESSION_SECRET);
}

function readCookie(request, name) {
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

const clearCookie = `${OAUTH_COOKIE}=; Max-Age=0; Path=/auth; HttpOnly; Secure; SameSite=Lax`;

async function handleLogin(url, env) {
  const ret = cleanReturn(url.searchParams.get("return"));
  if (!ret) return htmlError(400, "登入連結不正確，請回到網站重新按「用 Discord 登入」。");
  if (!loginConfigured(env)) return redirect(ret + "#ihs_login_error=unavailable");
  const nonce = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
  const state = await signToken(env.SESSION_SECRET, "o1", { r: ret, n: nonce, exp: Math.floor(Date.now() / 1000) + STATE_TTL });
  const q = new URLSearchParams({
    client_id: env.DISCORD_CLIENT_ID,
    response_type: "code",
    scope: "identify",
    redirect_uri: `${url.origin}/auth/callback`,
    state,
    prompt: "none", // skip the consent screen after the first authorization
  });
  // The nonce cookie binds the state to this browser (login CSRF: a callback link made by someone else fails).
  return redirect(`https://discord.com/oauth2/authorize?${q}`, {
    "set-cookie": `${OAUTH_COOKIE}=${nonce}; Max-Age=${STATE_TTL}; Path=/auth; HttpOnly; Secure; SameSite=Lax`,
  });
}

async function handleCallback(url, request, env) {
  const st = await verifyToken(env.SESSION_SECRET, "o1", url.searchParams.get("state"));
  const ret = st && cleanReturn(st.r);
  if (!ret || typeof st.n !== "string") return htmlError(400, "登入連結無效或已過期（10 分鐘），請回到網站重新按「用 Discord 登入」。");
  const cookie = readCookie(request, OAUTH_COOKIE);
  if (!cookie || !(await safeEqual(cookie, st.n))) {
    return htmlError(400, "登入要在同一個瀏覽器完成，請回到網站重新按「用 Discord 登入」。");
  }
  const fail = (why) => redirect(`${ret}#ihs_login_error=${why}`, { "set-cookie": clearCookie });
  const code = url.searchParams.get("code");
  if (url.searchParams.get("error") || !code || code.length > 200) return fail("cancelled");
  if (!loginConfigured(env)) return fail("unavailable");

  let me;
  try {
    const tok = await fetch(`${DISCORD_API}/oauth2/token`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: `${url.origin}/auth/callback`,
        client_id: env.DISCORD_CLIENT_ID,
        client_secret: env.DISCORD_CLIENT_SECRET,
      }).toString(),
    });
    if (!tok.ok) return fail("discord");
    const tj = await tok.json();
    if (!tj || typeof tj.access_token !== "string") return fail("discord");
    const res = await fetch(`${DISCORD_API}/users/@me`, { headers: { authorization: `Bearer ${tj.access_token}` } });
    if (!res.ok) return fail("discord");
    me = await res.json();
  } catch {
    return fail("discord");
  }
  if (!me || typeof me.id !== "string" || !USER_ID.test(me.id)) return fail("discord");
  const username = optStr(me.username, 40);
  const { user, isNew } = await getUser(env, me.id, username);
  await saveUser(env, user, isNew);
  const session = await signToken(env.SESSION_SECRET, "s1", {
    u: me.id, n: username, exp: Math.floor(Date.now() / 1000) + SESSION_TTL,
  });
  // In the fragment: never sent to any server, so it stays out of access logs; the site clears it at once.
  return redirect(`${ret}#ihs_session=${session}`, { "set-cookie": clearCookie });
}

/* ---------- entry ---------- */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = corsHeaders(request);
    const path = url.pathname;
    try {
      if (path === "/interactions") {
        if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
        const raw = new Uint8Array(await request.arrayBuffer());
        if (raw.length > MAX_BODY || !(await verifyDiscord(request, raw, env))) {
          return new Response("invalid request signature", { status: 401 });
        }
        let body;
        try { body = JSON.parse(new TextDecoder().decode(raw)); } catch { return json({ error: "bad_request" }, 400); }
        return await handleInteraction(body, env);
      }
      if (path === "/auth/login" && request.method === "GET") return await handleLogin(url, env);
      if (path === "/auth/callback" && request.method === "GET") return await handleCallback(url, request, env);
      if (path === "/api/watchlist" || path === "/api/me" || path.startsWith("/api/me/")) {
        if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
        if (request.method !== "GET" && request.method !== "POST") return json({ error: "method_not_allowed" }, 405, cors);
        if (path === "/api/watchlist") return await handleServerApi(request, env, cors);
        return await handleMeApi(request, env, cors, path);
      }
      return json({ error: "not_found" }, 404, cors);
    } catch {
      return json({ error: "internal_error" }, 500, cors);
    }
  },
};
