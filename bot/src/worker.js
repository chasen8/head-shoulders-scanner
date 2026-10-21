// Cloudflare Worker: Discord interactions endpoint + watchlist API (docs/watchlist-bot-design.md).
// Plain ES module, no dependencies, no build step. Secrets (DISCORD_PUBLIC_KEY, SYNC_KEY) come from
// the Worker environment only and are never echoed in any response.

const KV_KEY = "items";
const MAX_ITEMS = 500;
const MAX_BODY = 256 * 1024;
const MAX_KEY_LEN = 300;
const TIMEFRAMES = new Set(["4h", "1d", "1w"]);
const VIA = new Set(["discord", "site", "api"]);
const EPHEMERAL = 64;
const ALLOWED_ORIGINS = new Set(["https://chasen8.github.io"]);
const LOCAL_ORIGIN = /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/;

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

async function authorized(request, env) {
  if (!env.SYNC_KEY) return false;
  const h = request.headers.get("Authorization") || "";
  const m = /^Bearer (.+)$/.exec(h);
  if (!m) return false;
  return safeEqual(m[1], env.SYNC_KEY);
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

async function loadItems(env) {
  const raw = await env.WATCHLIST.get(KV_KEY);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

async function saveItems(env, items) {
  await env.WATCHLIST.put(KV_KEY, JSON.stringify(items));
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

/* ---------- Discord interactions ---------- */

async function handleInteraction(body, env) {
  if (body.type === 1) return json({ type: 1 });

  if (body.type === 3 && body.data) {
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
      const items = await loadItems(env);
      const res = addItems(items, valid, "discord");
      if (res.added.length) await saveItems(env, res.items);
      const addedKeys = new Set(res.added.map((a) => a.pattern_key));
      const dupes = valid.filter((v) => !addedKeys.has(v.pattern_key));
      const lines = [];
      if (res.added.length) lines.push("已加入關注：" + res.added.map(label).join("、"));
      if (dupes.length - res.full > 0) lines.push(`原本就在關注清單：${dupes.length - res.full} 筆`);
      if (res.full) lines.push(`關注清單已滿（上限 ${MAX_ITEMS} 筆），${res.full} 筆未加入`);
      if (bad) lines.push(`${bad} 筆格式無法辨識，已略過`);
      return reply(lines.join("\n"));
    }
    if (cid.startsWith("unwatch:")) {
      const ref = cid.slice("unwatch:".length);
      const items = await loadItems(env);
      let hit = null;
      for (const it of items) {
        if (it.pattern_key === ref || (/^[0-9a-f]{16}$/.test(ref) && (await sha1Prefix(it.pattern_key)) === ref)) {
          hit = it;
          break;
        }
      }
      if (!hit) return reply("這筆已經不在關注清單了。");
      await saveItems(env, items.filter((it) => it !== hit));
      return reply("已取消關注：" + label(hit));
    }
    return reply("不認得這個操作。");
  }

  if (body.type === 2 && body.data && body.data.name === "watchlist") {
    const items = await loadItems(env);
    if (!items.length) return reply("目前沒有關注任何標的。");
    const lines = items.slice(0, 40).map((it, i) => `${i + 1}. ${label(it)}${it.last_status ? "（" + it.last_status + "）" : ""}`);
    const more = items.length > 40 ? `\n…另有 ${items.length - 40} 筆` : "";
    return reply(`目前關注 ${items.length} 筆：\n` + lines.join("\n") + more);
  }

  return reply("不認得這個操作。");
}

/* ---------- watchlist API ---------- */

async function handleApi(request, env, cors) {
  if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401, cors);

  if (request.method === "GET") {
    return json({ items: await loadItems(env) }, 200, cors);
  }

  const text = await request.text();
  if (text.length > MAX_BODY) return json({ error: "bad_request" }, 400, cors);
  let body;
  try { body = JSON.parse(text); } catch { return json({ error: "bad_request" }, 400, cors); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ error: "bad_request" }, 400, cors);
  for (const f of ["add", "remove", "update"]) {
    if (body[f] !== undefined && !Array.isArray(body[f])) return json({ error: "bad_request" }, 400, cors);
  }

  let items = await loadItems(env);
  if (Array.isArray(body.add) && body.add.length) {
    items = addItems(items, body.add, "api").items;
  }
  if (Array.isArray(body.remove) && body.remove.length) {
    const rm = new Set(body.remove.filter((k) => typeof k === "string"));
    items = items.filter((it) => !rm.has(it.pattern_key));
  }
  if (Array.isArray(body.update)) {
    const byKey = new Map(items.map((it) => [it.pattern_key, it]));
    for (const u of body.update) {
      if (!u || typeof u.pattern_key !== "string") continue;
      const it = byKey.get(u.pattern_key);
      if (!it) continue;
      if ("last_status" in u) it.last_status = optStr(u.last_status, 20);
      if ("last_notified_at" in u) it.last_notified_at = validTime(u.last_notified_at) ? u.last_notified_at : null;
      if ("name" in u && optStr(u.name)) it.name = optStr(u.name);
    }
  }
  await saveItems(env, items);
  return json({ items }, 200, cors);
}

/* ---------- entry ---------- */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = corsHeaders(request);
    try {
      if (url.pathname === "/interactions") {
        if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
        const raw = new Uint8Array(await request.arrayBuffer());
        if (raw.length > MAX_BODY || !(await verifyDiscord(request, raw, env))) {
          return new Response("invalid request signature", { status: 401 });
        }
        let body;
        try { body = JSON.parse(new TextDecoder().decode(raw)); } catch { return json({ error: "bad_request" }, 400); }
        return await handleInteraction(body, env);
      }
      if (url.pathname === "/api/watchlist") {
        if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
        if (request.method !== "GET" && request.method !== "POST") return json({ error: "method_not_allowed" }, 405, cors);
        return await handleApi(request, env, cors);
      }
      return json({ error: "not_found" }, 404, cors);
    } catch {
      return json({ error: "internal_error" }, 500, cors);
    }
  },
};
