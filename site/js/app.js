/* 頭肩底掃描 front end.
 * Draws only what data/scan.json and data/bars/*.json contain (built by build_site.py); D29 adds a second
 * market, Taiwan stocks, in data/tw/scan.json + data/tw/bars/ (same row format plus `name`).
 * No detection or scoring logic lives here: just formatting, filtering, sorting by JSON fields
 * and linear mapping to pixels. Bars for a pattern are fetched only when it is opened. */
(function () {
  "use strict";

  // D29: each market has its own scan.json + bars/ (crypto and Taiwan scans are published independently)
  var MARKETS = {
    crypto: { label: "加密貨幣", dir: "data/" },
    tw: { label: "台股", dir: "data/tw/" }
  };
  var MARKET_ORDER = ["crypto", "tw"];
  function dataUrl(m) { return MARKETS[m].dir + "scan.json"; }
  var PAGE = 150; // rows rendered per "show more"
  var TF_LABEL = { "4h": "4 小時", "1d": "日線", "1w": "週線" };
  var STATUS_LABEL = { forming: "右肩形成中", watchlist: "右肩已確認", confirmed: "已突破", stale: "已過期" };
  var STATUS_ORDER = ["forming", "watchlist", "confirmed", "stale"];
  var EX_LABEL = { binance: "幣安", okx: "OKX", gate: "Gate", twse: "上市", tpex: "上櫃" };
  var MKT_LABEL = { spot: "現貨", perp: "永續", stock: "" };
  var SOURCE_LABEL = { "binance-spot": "幣安現貨", "binance-perp": "幣安永續", "okx-spot": "OKX 現貨",
    "okx-perp": "OKX 永續", "gate-perp": "Gate 永續", "twse-stock": "上市", "tpex-stock": "上櫃" };
  var TV_INTERVAL = { "4h": "240", "1d": "1D", "1w": "1W" };
  var PROVISIONAL_NOTE = "右肩尚未確認，會隨新 K 線變動。";
  var TW_4H_NOTE = "台股 4 小時線一天兩根：09:00–13:00 與 13:00–13:30（台北時間，和 TradingView 相同），所以兩根長短不一。";
  var POINT_NAME = { LS: "左肩", N1: "頸線點", H: "頭", N2: "頸線點", RS: "右肩" };
  var POINTS = ["LS", "N1", "H", "N2", "RS"];
  var NECK_LABEL = { down: "下降", flat: "水平", up: "上升" };
  var NECK_ORDER = ["down", "flat", "up"];
  var WL_KEY = "ihs.watchlist.v1";
  var SET_KEY = "ihs.settings.v1";
  var SYNC_LS = "ihs.sync.v1"; // D34 sync URL + key: no longer used (D35), removed when found
  var SESSION_LS = "ihs.session.v1"; // D35 Discord login: { token, user_id, username, known, pendingRemove }
  // Filter defaults are shared by both markets; `market` remembers the market viewed last (D29).
  var DEFAULT_SETTINGS = { tf: "all", status: "all", neck: "all", sort: "score", minScore: 0, showLevels: true, showChecks: true,
    market: "crypto", theme: "auto" };
  // Night mode: "auto" follows the OS setting. index.html applies the saved theme before first paint.
  var THEME_LABEL = { light: "淺色", dark: "夜間", auto: "跟隨系統" };
  // Chart colours: keep in sync with the :root / html[data-theme="dark"] tokens in css/site.css
  var PALETTE = {
    light: {
      bg: "#FFFBF3", text: "#2B1E12", muted: "#6B5640", faint: "#8F7B63", line: "#EBDDC6", grid: "#F5ECDC",
      accent: "#A85A08", up: "#089981", down: "#F23645", forming: "#8B5CF6",
      tp: "#089981", tp2: "#22AB94", stop: "#8F7B63", accentSoft: "rgba(168,90,8,0.5)",
      shape: "rgba(168,90,8,0.6)", cross: "#A8957C", crossLabel: "#2B1E12",
      volUp: "rgba(8,153,129,0.32)", volDown: "rgba(242,54,69,0.32)"
    },
    dark: {
      bg: "#1A1410", text: "#F3E9D8", muted: "#BCA98E", faint: "#8F7D66", line: "#3A2E24", grid: "#241C16",
      accent: "#F5B83D", up: "#089981", down: "#F23645", forming: "#A78BFA",
      tp: "#089981", tp2: "#22AB94", stop: "#9A8873", accentSoft: "rgba(245,184,61,0.55)",
      shape: "rgba(245,184,61,0.65)", cross: "#8F7D66", crossLabel: "#4A3B2E",
      volUp: "rgba(8,153,129,0.4)", volDown: "rgba(242,54,69,0.4)"
    }
  };
  var C = PALETTE.light;

  // D38 converging triangles: a second list view ("tri") per market, data/triangles.json + data/tri_bars/
  // (data/tw/... for Taiwan), opened from the ☰ drawer; ?view=triangles in the URL. No stars / notifications yet.
  function triUrl(m) { return MARKETS[m].dir + "triangles.json"; }
  var TRI_STATUS_LABEL = { forming: "收斂中", breakout_up: "向上突破", breakout_down: "向下突破", stale: "已過期" };
  var TRI_STATUS_ORDER = ["forming", "breakout_up", "breakout_down", "stale"];
  var TRI_TYPE_LABEL = { ascending: "上升三角", descending: "下降三角", symmetrical: "對稱三角" };
  var TRI_TYPE_ORDER = ["ascending", "descending", "symmetrical"];
  var TRI_BIAS_LABEL = { up: "傳統上偏多", down: "傳統上偏空", neutral: "中性（看突破方向）" };
  var TRI_SIDE_LABEL = { resistance: "阻力線", support: "支撐線" };
  var TRI_SORT_OPTIONS = [["score", "分數高到低"], ["near", "離突破最近"], ["recent", "最新訊號"], ["name", "名稱"]];
  var TRI_NOSTAR = "收斂三角暫不提供觀察名單星號與 Discord 通知（目前只支援頭肩底）。";

  var state = { data: null, market: "crypto", q: "", tf: "all", status: "all", neck: "all", sort: "score", shown: PAGE,
    selected: null, minScore: 0, onlyWl: false,
    view: "ihs", tdata: null, ttf: "all", tst: "all", ttype: "all", tsort: "score", tselected: null };
  var triCache = {}; // market -> triangles.json payload
  var triErr = {};
  var triChartCache = {};
  var dataCache = {}; // market -> scan.json payload (loaded on demand)
  var loadErr = {}; // market -> last load error
  var chartApi = null;
  var chartCache = {};
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var narrow = window.matchMedia("(max-width: 959px)");

  var $ = function (id) { return document.getElementById(id); };

  /* ---------- formatting ---------- */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toDate(t) { return typeof t === "number" ? new Date(t * 1000) : new Date(t); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tzLabel() {
    var off = -new Date().getTimezoneOffset();
    var h = Math.floor(Math.abs(off) / 60), m = Math.abs(off) % 60;
    return "UTC" + (off >= 0 ? "+" : "−") + h + (m ? ":" + pad(m) : "");
  }
  function fmtDay(t, withYear) {
    var d = toDate(t);
    return (withYear ? d.getFullYear() + "年" : "") + (d.getMonth() + 1) + "月" + d.getDate() + "日";
  }
  function fmtClock(t) { var d = toDate(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function fmtStamp(t) { return fmtDay(t) + " " + fmtClock(t); }
  function fmtBar(t, tf) { return tf === "4h" ? fmtStamp(t) : fmtDay(t, true); }
  function sameDay(a, b) {
    var x = toDate(a), y = toDate(b);
    return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth() && x.getDate() === y.getDate();
  }
  // Small coins keep ~4 significant digits (0.0001750); notify_discord.py uses the same rule.
  function decimalsFor(p) {
    var a = Math.abs(p);
    if (a >= 1000) return 2;
    if (a >= 1) return 3;
    if (a >= 0.01) return 4;
    if (!(a > 0)) return 2;
    return Math.min(12, Math.ceil(-Math.log10(a)) + 3);
  }
  function isTw() { return state.market === "tw"; }
  function isTri() { return state.view === "tri"; }
  function rowMarket(r) { return r && (r.market === "stock" || r.exchange === "twse" || r.exchange === "tpex") ? "tw" : "crypto"; }
  // Taiwan stocks: always two decimals (NT$); crypto: by magnitude
  function fmtPrice(p, ref) {
    if (p === null || p === undefined) return "—";
    var d = isTw() ? 2 : decimalsFor(ref === undefined ? p : ref);
    return Number(p).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function pct(x, digits) { return (x * 100).toFixed(digits === undefined ? 1 : digits) + "%"; }
  function symParts(sym) {
    return /USDT$/.test(sym) ? { base: sym.slice(0, -4), quote: "/USDT" } : { base: sym, quote: "" };
  }
  // "BTC<small>/USDT</small>" or, for a Taiwan stock, "2330<small class=sym__name>台積電</small>"
  function symHtml(r) {
    if (rowMarket(r) === "tw") {
      return esc(r.symbol) + (r.name ? "<small class=\"sym__name\">" + esc(r.name) + "</small>" : "");
    }
    var sp = symParts(r.symbol);
    return esc(sp.base) + "<small>" + esc(sp.quote) + "</small>";
  }
  function symText(r) {
    if (rowMarket(r) === "tw") return r.symbol + (r.name ? " " + r.name : "");
    var sp = symParts(r.symbol);
    return sp.base + sp.quote;
  }
  function tfName(tf) { return TF_LABEL[tf] || tf; }
  function marketText(r) {
    var ex = r.exchange || "binance", mk = r.market || "spot";
    if (rowMarket(r) === "tw") return EX_LABEL[ex] || ex;
    return (EX_LABEL[ex] || ex) + " " + (MKT_LABEL[mk] || mk);
  }
  function tvUrl(r) {
    var sym = r.tv_symbol || ("BINANCE:" + r.symbol);
    return "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(sym) +
      "&interval=" + (TV_INTERVAL[r.timeframe] || r.timeframe);
  }
  function distText(r) {
    if (!(r.current_neckline_price > 0)) return "頸線已延伸到 0 以下";
    if (r.neckline_side === "above") return "高於頸線 " + pct(r.distance_to_neckline);
    if (r.neckline_side === "below") return "低於頸線 " + pct(r.distance_to_neckline);
    return "正好在頸線上";
  }

  function signedPct(x) { return (x < 0 ? "−" : "+") + pct(Math.abs(x), 1); }
  function neckText(r) { // N1 -> N2 total change, computed by build_site.py
    return typeof r.neckline_change_pct === "number" ? "頸線 " + signedPct(r.neckline_change_pct) : "";
  }

  /* ---------- meta ---------- */
  function unitWord() { return isTw() ? "檔股票" : "個幣種"; }
  function scopeText(d) {
    var s = d.settings, tfs = s.timeframes.map(tfName).join("、");
    if (d.market === "tw") {
      return "台灣上市＋上櫃" + (s.universe === "common+etf" ? "普通股與 ETF" : "普通股") +
        (s.symbols_source === "all" ? "" : "（部分）") + "，" + tfs;
    }
    var srcs = (s.sources || []).map(function (k) { return SOURCE_LABEL[k] || k; });
    return (srcs.length ? srcs.join("、") : "幣安現貨") +
      (s.symbols_source === "all" ? " 全部 USDT 交易對" : " USDT 交易對") + "，" + tfs;
  }
  function renderMeta() {
    var d = isTri() ? state.tdata : state.data, s = d.settings;
    var scanned = d.scanned_symbols !== undefined ? d.scanned_symbols : s.symbols.length;
    var next = d.next_scan_at, nextText;
    var every = isTw() ? "每個交易日收盤後（台北 14:10 左右）掃描一次" : "每 4 小時掃描一次";
    if (!next) nextText = "目前沒有定時掃描，資料只在手動執行時更新。";
    else if (new Date(next).getTime() < Date.now()) nextText = "預定的下次掃描（" + fmtStamp(next) + "）已經過了，這份資料可能不是最新的。";
    else nextText = every + "，下次約在 " + (sameDay(next, new Date().toISOString()) ? fmtClock(next) : fmtStamp(next)) + "。";
    var fails = (d.failures || []).length;
    $("scope").textContent = scopeText(d);
    $("meta").innerHTML =
      "<b class=\"meta__mkt\">" + esc(MARKETS[state.market].label + (isTri() ? "・收斂三角" : "")) + "</b>資料更新於 <time datetime=\"" + esc(d.generated_at) + "\">" +
      fmtStamp(d.generated_at) + "</time>（" + tzLabel() + "），掃描了 " + scanned + " " + unitWord() + "。" +
      (fails ? "有 " + fails + " 組 K 線抓取失敗，已略過。" : "") + nextText +
      (s.include_stale ? "這份資料包含已過期的型態（預覽用）。" : "");
  }

  /* ---------- filtering / sorting (JSON fields only) ---------- */
  function matchQ(r) {
    if (!state.q) return true;
    return r.symbol.toUpperCase().indexOf(state.q) !== -1 ||
      (!!r.name && String(r.name).toUpperCase().indexOf(state.q) !== -1);
  }
  function pass(r, skip) {
    return matchQ(r) &&
      (!state.onlyWl || isStarred(r.pattern_key)) &&
      !(r.pattern_score < state.minScore) &&
      (skip === "tf" || state.tf === "all" || r.timeframe === state.tf) &&
      (skip === "status" || state.status === "all" || r.status === state.status) &&
      (skip === "neck" || state.neck === "all" || r.neckline_trend === state.neck);
  }
  var SORTS = {
    score: function (a, b) { return a.rank - b.rank; },
    shape: function (a, b) { return ((b.shape_score || 0) - (a.shape_score || 0)) || (a.rank - b.rank); },
    dist: function (a, b) { return (a.distance_to_neckline - b.distance_to_neckline) || (a.rank - b.rank); },
    recent: function (a, b) {
      return (Date.parse(b.signal_time) - Date.parse(a.signal_time)) || (a.rank - b.rank);
    },
    name: function (a, b) { return a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : a.rank - b.rank; },
    // 52-week position (local trial): rows without the field (old data / short history) go last
    high52: function (a, b) {
      var x = typeof a.near_high_52w === "number" ? a.near_high_52w : -1, y = typeof b.near_high_52w === "number" ? b.near_high_52w : -1;
      return (y - x) || (a.rank - b.rank);
    }
  };
  function filtered() {
    return state.data.results.filter(function (r) { return pass(r); }).sort(SORTS[state.sort] || SORTS.score);
  }

  function chip(group, value, label, count) {
    var on = state[group] === value;
    return "<button type=\"button\" class=\"chip\" data-group=\"" + group + "\" data-value=\"" + esc(value) +
      "\" aria-pressed=\"" + on + "\">" + esc(label) + "<span class=\"chip__n\">" + count + "</span></button>";
  }

  function renderFilters() {
    var res = state.data.results, s = state.data.settings;
    var byTf = {}, bySt = {}, byNk = {}, nTf = 0, nSt = 0, nNk = 0, hasStale = false;
    res.forEach(function (r) {
      if (r.status === "stale") hasStale = true;
      if (pass(r, "tf")) { byTf[r.timeframe] = (byTf[r.timeframe] || 0) + 1; nTf++; }
      if (pass(r, "status")) { bySt[r.status] = (bySt[r.status] || 0) + 1; nSt++; }
      if (pass(r, "neck")) { byNk[r.neckline_trend] = (byNk[r.neckline_trend] || 0) + 1; nNk++; }
    });
    $("filter-tf").innerHTML = chip("tf", "all", "全部", nTf) + s.timeframes.map(function (tf) {
      return chip("tf", tf, tfName(tf), byTf[tf] || 0);
    }).join("");
    $("filter-status").innerHTML = chip("status", "all", "全部", nSt) + STATUS_ORDER.filter(function (st) {
      return st !== "stale" || hasStale;
    }).map(function (st) { return chip("status", st, STATUS_LABEL[st], bySt[st] || 0); }).join("");
    $("filter-neck").innerHTML = chip("neck", "all", "全部", nNk) + NECK_ORDER.map(function (k) {
      return chip("neck", k, NECK_LABEL[k], byNk[k] || 0);
    }).join("");
  }

  /* ---------- glyph: the pattern's silhouette from row.shape, mapped linearly ---------- */
  function glyph(r) {
    var W = 52, H = 30, P = 3;
    var sh = r.shape, pts = sh.points, last = sh.last;
    var t0 = pts[0][0], span = (pts[4][0] - pts[0][0]) || 1;
    var tEnd = Math.min(last[0], pts[4][0] + span * 0.35);
    var tStart = t0 - span * 0.08;
    var prices = pts.map(function (p) { return p[1]; });
    var showTail = last[0] <= tEnd && last[0] > pts[4][0];
    if (showTail) prices.push(last[1]);
    var lo = Math.min.apply(null, prices), hi = Math.max.apply(null, prices);
    var x = function (t) { return (P + (t - tStart) / ((tEnd - tStart) || 1) * (W - 2 * P)).toFixed(1); };
    var y = function (p) { return (H - P - (p - lo) / ((hi - lo) || 1) * (H - 2 * P)).toFixed(1); };
    var n0 = sh.neckline[0], n1 = sh.neckline[1], prov = !!r.rs_provisional;
    var drawn = prov ? pts.slice(0, 4) : pts;
    var svg = "<svg class=\"glyph\" viewBox=\"0 0 " + W + " " + H + "\" aria-hidden=\"true\" focusable=\"false\">" +
      "<line class=\"g-neck\" x1=\"" + x(n0[0]) + "\" y1=\"" + y(n0[1]) + "\" x2=\"" + x(n1[0]) + "\" y2=\"" + y(n1[1]) + "\"/>" +
      "<polyline class=\"g-shape\" points=\"" + drawn.map(function (p) { return x(p[0]) + "," + y(p[1]); }).join(" ") + "\"/>";
    if (prov) { // provisional right shoulder: dashed last leg + hollow RS
      svg += "<line class=\"g-prov\" x1=\"" + x(pts[3][0]) + "\" y1=\"" + y(pts[3][1]) + "\" x2=\"" + x(pts[4][0]) +
        "\" y2=\"" + y(pts[4][1]) + "\"/><circle class=\"g-rs\" r=\"2.2\" cx=\"" + x(pts[4][0]) + "\" cy=\"" + y(pts[4][1]) + "\"/>";
    }
    if (showTail) {
      svg += "<polyline class=\"g-tail\" points=\"" + x(pts[4][0]) + "," + y(pts[4][1]) + " " + x(last[0]) + "," + y(last[1]) + "\"/>";
    }
    if (sh.breakout && sh.breakout[0] <= tEnd) {
      svg += "<circle class=\"g-brk\" r=\"2.2\" cx=\"" + x(sh.breakout[0]) + "\" cy=\"" + y(sh.breakout[1]) + "\"/>";
    }
    return svg + "</svg>";
  }

  /* ---------- list ---------- */
  function w52Text(r) { return typeof r.pos_52w === "number" ? "52 週 " + Math.round(r.pos_52w * 100) + "%" : ""; }
  function rowHtml(r) {
    return "<li class=\"rowwrap\"><button type=\"button\" class=\"row\" data-id=\"" + esc(r.pattern_id) + "\" aria-current=\"" +
      (state.selected === r.pattern_id) + "\" aria-label=\"" + esc(symText(r) + " " + tfName(r.timeframe) + "，" +
      marketText(r) + "，" + STATUS_LABEL[r.status] + "，" + distText(r) + (neckText(r) ? "，" + neckText(r) : "") + "，分數 " + r.pattern_score.toFixed(1)) + "\">" +
      glyph(r) +
      "<span class=\"row__name\"><span class=\"sym\">" + symHtml(r) + "</span>" +
      "<span class=\"tf\">" + esc(tfName(r.timeframe)) + "</span>" +
      "<span class=\"mkt\">" + esc(marketText(r)) + "</span></span>" +
      "<span class=\"row__score\">" + r.pattern_score.toFixed(1) + "<small>分</small></span>" +
      "<span class=\"row__sub\"><span class=\"status status--" + esc(r.status) + "\">" + STATUS_LABEL[r.status] +
      "</span><span class=\"dist\">" + esc(distText(r)) + "</span>" +
      (neckText(r) ? "<span class=\"dist\">" + esc(neckText(r)) + "</span>" : "") +
      (r.shape_score !== null && r.shape_score !== undefined
        ? "<span class=\"shp\">形狀 " + r.shape_score.toFixed(0) + "</span>" : "") +
      (w52Text(r) ? "<span class=\"shp w52\" title=\"目前價在過去一年收盤區間的位置（0% 最低、100% 最高）\">" + esc(w52Text(r)) + "</span>" : "") + "</span>" +
      "</button>" + starBtn(r, "") + "</li>";
  }

  function showNotice(kind, title, body, action) {
    var n = $("notice");
    n.className = "notice" + (kind === "error" ? " notice--error" : "");
    n.innerHTML = "<h3>" + esc(title) + "</h3><p>" + body + "</p>" +
      (action ? "<button type=\"button\" id=\"notice-action\">" + esc(action.label) + "</button>" : "");
    n.hidden = false;
    if (action) $("notice-action").addEventListener("click", action.run);
  }

  function clearFilters() {
    state.q = ""; state.tf = "all"; state.status = "all"; state.neck = "all"; state.onlyWl = false; state.shown = PAGE;
    $("q").value = "";
    update();
  }

  function renderList() {
    var all = state.data.results, rows = filtered(), d = state.data;
    $("notice").hidden = true;
    $("more").hidden = true;
    if (!all.length) {
      $("count").textContent = "目前沒有型態";
      $("list").innerHTML = "";
      showNotice("empty", "目前沒有符合條件的頭肩底",
        "最近一次掃描（" + fmtStamp(d.generated_at) + "）在 " + (d.scanned_symbols || d.settings.symbols.length) +
        " " + unitWord() + "的 " + esc(d.settings.timeframes.map(tfName).join("、")) +
        " K 線上，沒有找到正在成形或剛突破頸線的型態。" + (d.next_scan_at
          ? "下次掃描約在 " + fmtStamp(d.next_scan_at) + "（" + tzLabel() + "），到時新收盤的 K 線會再檢查一次。"
          : "目前沒有定時掃描；新的 K 線收盤後，重新執行一次掃描才會更新這裡。"));
      return rows;
    }
    if (!rows.length) {
      $("count").textContent = "沒有符合的型態";
      $("list").innerHTML = "";
      showNotice("empty", "沒有符合的型態",
        (state.q ? "找不到代號或名稱含「" + esc(state.q) + "」的" + (isTw() ? "股票" : "幣種") : state.onlyWl ? "觀察名單裡沒有符合的型態（在每個型態旁按星號加入）" : "這個篩選下沒有型態") +
        (state.minScore > 0 ? "；目前設定了最低分數 " + state.minScore + "（可在選單的設定調整）" : "") +
        "。換一個週期、狀態或頸線斜率看看，或清除篩選看全部 " + all.length + " 個。",
        { label: "清除篩選", run: clearFilters });
      return rows;
    }
    var shown = Math.min(rows.length, state.shown);
    var sel = rows.findIndex(function (r) { return r.pattern_id === state.selected; });
    if (sel >= shown) shown = sel + 1; // a deep link must stay visible
    $("count").textContent = "共 " + rows.length + " 個型態" + (rows.length < all.length ? "（全部 " + all.length + " 個）" : "");
    $("list").innerHTML = rows.slice(0, shown).map(rowHtml).join("");
    if (shown < rows.length) {
      var n = Math.min(PAGE, rows.length - shown);
      $("more").textContent = "再顯示 " + n + " 個（還有 " + (rows.length - shown) + " 個）";
      $("more").hidden = false;
    }
    return rows;
  }

  /* ---------- detail ---------- */
  // D36: breakout_basis "high" = the bar's high (wick) crossing the neckline counts; "close" = a close above it
  function crossText(r) {
    return r.breakout_basis === "close" ? "收盤站上頸線" : "最高價（含影線）穿過頸線";
  }

  function summary(r) {
    var tf = r.timeframe, close = "最新收盤 " + fmtPrice(r.current_close) + "，" + distText(r) + "。";
    if (r.status === "confirmed") {
      return fmtBar(r.breakout_time, tf) + " 那根 K 線" + crossText(r) + "，到現在過了 " + r.bars_since_breakout + " 根。" + close;
    }
    if (r.status === "forming") {
      return "右肩形成中：N2 之後的最低點（" + fmtBar(r.RS_time, tf) + "）暫定為右肩。" + PROVISIONAL_NOTE +
        (r.breakout_detected ? "已有 K 線" + crossText(r) + "（" + fmtBar(r.breakout_time, tf) + "），右肩確認後才算已突破。" : "") +
        close;
    }
    if (r.status === "watchlist") {
      return "右肩在 " + fmtBar(r.RS_time, tf) + " 成形，" + fmtBar(r.RS_confirm_time, tf) + " 確認；之後 " +
        r.bars_since_rs + " 根 K 線還沒有突破頸線。" + close;
    }
    if (r.stale_reason === "runaway") {
      return "已經漲過頭，只供回顧：目前收盤高出頸線 " + headX(r.v3_runaway) + "（上限見下方形狀驗證 V3b）。" + close;
    }
    if (r.stale_reason === "failed_breakout") {
      return "突破後又跌回頸線下方 " + headX(r.v3_fallback) + "，視為突破失敗，只供回顧。" + close;
    }
    return "這個型態已過期，只供回顧：" + (r.breakout_detected
      ? fmtBar(r.breakout_time, tf) + " 突破頸線，已經過了 " + r.bars_since_breakout + " 根。"
      : "右肩之後 " + r.bars_since_rs + " 根 K 線都沒有突破。") + close;
  }

  function scoreBar(label, v, max) {
    return "<div class=\"sb\"><span>" + label + "</span><span class=\"sb__v\">" + v.toFixed(1) + " / " + max +
      "</span><div class=\"sb__track\" aria-hidden=\"true\"><div class=\"sb__fill\" style=\"width:" +
      Math.max(0, Math.min(100, v / max * 100)).toFixed(1) + "%\"></div></div></div>";
  }

  function pctOr(x) { return x === null || x === undefined ? "—" : pct(x, 1); }
  function headX(x) { return (x === null || x === undefined) ? "—" : x.toFixed(2) + " 個頭高"; }
  // D26 shape checks: value / limit / pass come from build_site.py (shape_checks); this only formats them.
  var CHECK_NAME = { V1: "中途沒有大轉折", V2: "貼著五點骨架", V2r: "骨架 R²", V3a: "頸線乾淨",
    V3b: "還沒漲過頭", V3c: "沒有突破失敗", V4: "整體形狀" };
  function templateName(t) {
    if (!t) return "";
    var parts = String(t).split("_");
    return parts[0].replace(/-?USDT$/, "") + " " + parts[parts.length - 1];
  }
  function checkValue(c, r) {
    if (c.value === null) return c.id === "V3c" ? "沒站上過頸線" : "—";
    if (c.id === "V3a") return (c.value >= 0 ? "+" : "−") + pct(Math.abs(c.value), 1);
    if (c.id === "V2r") return c.value.toFixed(2);
    if (c.id === "V4") return c.value.toFixed(3) + "（最像 " + templateName(r.v4_nearest_template) + "）";
    return c.value.toFixed(2) + " 頭高";
  }
  function checkLimit(c) {
    if (!c.enabled) return "不過濾";
    var op = c.kind === "max" ? "≤ " : "≥ ";
    if (c.id === "V3a") return op + "+" + pct(c.limit, 1);
    if (c.id === "V4") return op + c.limit.toFixed(2);
    if (c.id === "V2r") return op + c.limit.toFixed(2);
    return op + c.limit.toFixed(2) + " 頭高";
  }
  function checkResult(c) {
    if (c.pass === true) return "<span class=\"ck ck--ok\">通過</span>";
    if (c.pass === false) return "<span class=\"ck ck--bad\">未通過</span>";
    return "<span class=\"ck\">" + (c.enabled ? "不適用" : "參考") + "</span>";
  }
  function checksHtml(r) {
    // D30: the V4 template-distance check still filters, but its row is not shown on the site
    var list = (Array.isArray(r.shape_checks) ? r.shape_checks : []).filter(function (c) { return c.id !== "V4"; });
    if (!list.length) return "";
    return "<section class=\"checks-sec\"><h3>形狀驗證<span class=\"checks-sum\">形狀分 " +
      (r.shape_score === null || r.shape_score === undefined ? "—" : r.shape_score.toFixed(1)) + " / 100</span></h3>" +
      "<table class=\"checks\"><thead><tr><th scope=\"col\">項目</th><th scope=\"col\">檢查什麼</th>" +
      "<th scope=\"col\">數值</th><th scope=\"col\">門檻</th><th scope=\"col\">結果</th></tr></thead><tbody>" +
      list.map(function (c) {
        return "<tr><td class=\"checks__id\">" + esc(c.id) + "<small>" + esc(CHECK_NAME[c.id] || "") + "</small></td>" +
          "<td class=\"checks__text\">" + esc(c.text) + "</td><td>" + esc(checkValue(c, r)) + "</td><td>" +
          esc(checkLimit(c)) + "</td><td>" + checkResult(c) + "</td></tr>";
      }).join("") + "</tbody></table>" +
      "<p class=\"note\">頭高＝頭部低點到頸線的距離（對數價）。形狀條件全部通過才會列在清單上；V3b、V3c 不通過時標為已過期。" +
      "形狀分表示離門檻多遠：0 代表剛好卡在門檻，分數越高形狀越乾淨；它不是好壞的絕對分數。</p></section>";
  }

  function renderDetail(r) {
    var tf = r.timeframe;
    var px = function (p) { return fmtPrice(p, r.current_close); };
    var est = r.breakout_detected ? "" : "（預估）";
    $("detail").hidden = false;
    $("detail-head").innerHTML =
      "<div class=\"detail__title\"><h2>" + symHtml(r) + "</h2>" +
      starBtn(r, "star--lg") +
      "<span class=\"tf\">" + esc(tfName(tf)) + "</span>" +
      "<span class=\"mkt mkt--lg\">" + esc(marketText(r)) + "</span>" +
      "<span class=\"status status--" + esc(r.status) + "\">" + STATUS_LABEL[r.status] + "</span>" +
      "<span class=\"score\">分數 <b>" + r.pattern_score.toFixed(1) + "</b></span>" +
      (r.shape_score !== null && r.shape_score !== undefined
        ? "<span class=\"score\">形狀分 <b>" + r.shape_score.toFixed(1) + "</b></span>" : "") +
      "<a class=\"tvlink\" href=\"" + esc(tvUrl(r)) + "\" target=\"_blank\" rel=\"noopener\">在 TradingView 開啟</a></div>" +
      "<p class=\"summary\">" + esc(summary(r)) + "</p>";

    $("detail-body").innerHTML = (settings.showChecks ? checksHtml(r) : "") +
      "<section><h3>分數拆解</h3><div class=\"scorebars\">" +
      scoreBar("左右肩對稱", r.shoulder_score, 30) + scoreBar("頭部深度", r.head_depth_score, 25) +
      scoreBar("頸線平緩", r.neckline_score, 25) + scoreBar("突破／接近頸線", r.breakout_score, 20) +
      "</div></section>" +
      "<section><h3>目標與失效</h3><dl class=\"facts\">" +
      "<dt>" + (r.breakout_detected ? "突破時頸線價" : "預估突破價（目前頸線）") + "</dt><dd>" + px(r.breakout_level) + "</dd>" +
      "<dt><span class=\"key key--tp\" aria-hidden=\"true\"></span>TP1</dt><dd>" + px(r.tp1) + "</dd>" +
      "<dt><span class=\"key key--tp2\" aria-hidden=\"true\"></span>TP2" + est + "</dt><dd>" + px(r.tp2) + "</dd>" +
      "<dt><span class=\"key key--stop\" aria-hidden=\"true\"></span>失效價（頭部低點）</dt><dd>" + px(r.invalidation_price) + "</dd>" +
      "</dl><p class=\"note\">TP1＝以頭部低點為 0、頸線中點為 1 的對數斐波那契 1.414；TP2＝突破價 ×（1＋頭到頸線漲幅）。" +
      (r.breakout_detected ? "" : "還沒突破，TP2 的突破價先用目前頸線價，所以 TP2 是預估、會隨頸線移動。") +
      "收盤跌破失效價，型態就不成立。</p></section>" +
      "<section><h3>量測</h3><dl class=\"facts\">" +
      "<dt>左肩跌幅</dt><dd>" + pctOr(r.drop_left_pct) + "</dd>" +
      "<dt>右肩跌幅" + (r.rs_provisional ? "（暫定）" : "") + "</dt><dd>" + pctOr(r.drop_right_pct) + "</dd>" +
      "<dt>頭到頸線漲幅</dt><dd>" + pctOr(r.head_rise_pct) + "</dd>" +
      (typeof r.neckline_change_pct === "number"
        ? "<dt>頸線斜率（N1→N2）</dt><dd>" + signedPct(r.neckline_change_pct) + (NECK_LABEL[r.neckline_trend] ? "（" + NECK_LABEL[r.neckline_trend] + "）" : "") + "</dd>" : "") +
      "<dt>頸線角度</dt><dd>" + r.neckline_angle.toFixed(1) + "°</dd>" +
      "<dt>目前價</dt><dd>" + px(r.current_close) + "</dd>" +
      (typeof r.pos_52w === "number"
        ? "<dt>52 週區間位置</dt><dd>" + Math.round(r.pos_52w * 100) + "%（為 52 週高點的 " + Math.round(r.near_high_52w * 100) + "%）</dd>" : "") +
      "<dt>目前頸線價</dt><dd>" + px(r.current_neckline_price) + "</dd>" +
      "<dt>突破量比</dt><dd>" + (r.volume_ratio === null ? (r.breakout_detected ? "—" : "未突破") : r.volume_ratio.toFixed(2) + " 倍") + "</dd>" +
      "<dt>最後一根 K 線</dt><dd>" + fmtBar(r.as_of_time, tf) + (tf === "1w" ? " 當週" : "") + "</dd>" +
      "</dl>" + (typeof r.pos_52w === "number"
        ? "<p class=\"note\">52 週區間位置：0% 在一年最低、100% 在一年最高（用收盤價）；回測中接近一年高點的台股型態表現相對較好，但未勝過持有 0050。</p>" : "") + "</section>" +
      "<section><h3>五個點</h3><table class=\"points\"><thead><tr><th scope=\"col\">點</th><th scope=\"col\">K 線</th><th scope=\"col\">價格</th></tr></thead><tbody>" +
      POINTS.map(function (k) {
        var prov = k === "RS" && r.rs_provisional;
        return "<tr" + (prov ? " class=\"is-prov\"" : "") + "><td title=\"" + POINT_NAME[k] + "\">" + k +
          (prov ? "<small>暫定</small>" : "") + "</td><td>" + fmtBar(r[k + "_time"], tf) +
          "</td><td>" + fmtPrice(r[k + "_price"], r.current_close) + "</td></tr>";
      }).join("") + "</tbody></table></section>";

    loadChart(r);
  }

  /* ---------- chart ---------- */
  function chartState(kind, html) {
    var el = $("chart-state");
    if (!kind) { el.hidden = true; el.innerHTML = ""; return; }
    el.className = "chart-state" + (kind === "error" ? " chart-state--error" : "");
    el.innerHTML = html;
    el.hidden = false;
  }

  function loadChart(r) {
    var id = r.pattern_id, url = MARKETS[rowMarket(r)].dir + r.chart_file;
    if (chartCache[id]) { drawChart(r, chartCache[id]); return; }
    chartState("loading", "<p>正在載入 K 線…</p>");
    fetch(url, { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (c) {
        if (!c || !Array.isArray(c.bars) || !c.bars.length) throw new Error("檔案格式不對");
        chartCache[id] = c;
        if (state.selected === id) drawChart(r, c);
      })
      .catch(function (err) {
        if (state.selected !== id) return;
        chartState("error", "<p>這筆型態的 K 線檔 <code>" + esc(url) + "</code> 讀不到（" +
          esc(err && err.message ? err.message : err) + "）。可能是掃描剛好在更新，重新整理通常就好；右側數據仍可參考。</p>" +
          "<button type=\"button\" id=\"chart-retry\">再試一次</button>");
        $("chart-retry").addEventListener("click", function () { loadChart(r); });
      });
  }

  function initChart() {
    if (chartApi) return chartApi;
    var LW = window.LightweightCharts;
    if (!LW) return null;
    var font = getComputedStyle(document.body).fontFamily;
    var chart = LW.createChart($("chart"), {
      autoSize: true,
      layout: {
        background: { type: LW.ColorType.Solid, color: C.bg },
        textColor: C.text, fontFamily: font, fontSize: 12, attributionLogo: true,
        panes: { separatorColor: C.line, separatorHoverColor: C.grid, enableResize: false }
      },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.line, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: C.line, rightOffset: 6, timeVisible: true, secondsVisible: false,
        tickMarkFormatter: function (t, type) {
          var d = toDate(t);
          if (type === 0) return String(d.getFullYear());
          if (type === 1) return (d.getMonth() + 1) + "月";
          if (type === 2) return (d.getMonth() + 1) + "/" + d.getDate();
          return pad(d.getHours()) + ":" + pad(d.getMinutes());
        } },
      crosshair: {
        mode: LW.CrosshairMode.Normal,
        vertLine: { color: C.cross, width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: C.crossLabel },
        horzLine: { color: C.cross, width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: C.crossLabel }
      },
      localization: { locale: "zh-TW", timeFormatter: function (t) { return fmtStamp(t); } },
      handleScale: { mouseWheel: true, pinch: true },
      handleScroll: { vertTouchDrag: false }
    });
    var candles = chart.addSeries(LW.CandlestickSeries, {
      upColor: C.bg, downColor: C.text, borderUpColor: C.text, borderDownColor: C.text,
      wickUpColor: C.text, wickDownColor: C.text, priceLineVisible: false
    });
    // Volume in its own bottom pane, so the price axis never extends into negative prices.
    var volume = chart.addSeries(LW.HistogramSeries, {
      priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false
    }, 1);
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0 }, borderColor: C.line });
    var panes = chart.panes();
    if (panes[1]) {
      if (typeof panes[1].setStretchFactor === "function") panes[1].setStretchFactor(0.22);
      else panes[1].setHeight(90);
    }
    var noScale = function () { return null; };
    var shape = chart.addSeries(LW.LineSeries, {
      color: C.shape, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, autoscaleInfoProvider: noScale
    });
    var neck = chart.addSeries(LW.LineSeries, {
      color: C.accent, lineWidth: 2, lineStyle: LW.LineStyle.Dashed, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: noScale
    });
    // N2 -> RS leg while the right shoulder is still provisional (forming): dashed, lighter.
    var shapeProv = chart.addSeries(LW.LineSeries, {
      color: C.accentSoft, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: noScale
    });
    // D38 triangle lines: solid inside the pattern, dashed extension to the apex (empty while a head-and-shoulders
    // pattern is shown, and the IHS series are empty while a triangle is shown)
    var triLine = function (color, dashed) {
      return chart.addSeries(LW.LineSeries, {
        color: color, lineWidth: 2, lineStyle: dashed ? LW.LineStyle.Dashed : LW.LineStyle.Solid, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: noScale
      });
    };
    var markers = LW.createSeriesMarkers(candles, []);
    chartApi = { chart: chart, candles: candles, volume: volume, shape: shape, shapeProv: shapeProv, neck: neck,
      triRes: triLine(C.down, false), triResExt: triLine(C.down, true),
      triSup: triLine(C.up, false), triSupExt: triLine(C.up, true),
      markers: markers, levelLines: [], LW: LW };
    initPriceAxisZoom(chartApi);
    return chartApi;
  }

  // TradingView-style Y-axis zoom: the mouse wheel over the right price axis stretches /
  // squeezes the candles around the price under the cursor (dragging the axis and double-click
  // reset are built into Lightweight Charts). The "A" button returns to auto-scale.
  function initPriceAxisZoom(api) {
    var el = $("chart"), btn = $("chart-autoscale");
    var scale = api.candles.priceScale();
    var syncBtn = function () {
      if (!btn) return;
      var auto = scale.options().autoScale !== false;
      btn.setAttribute("aria-pressed", auto ? "true" : "false");
    };
    if (btn) {
      btn.addEventListener("click", function () { scale.setAutoScale(true); syncBtn(); });
    }
    el.addEventListener("wheel", function (ev) {
      var rect = el.getBoundingClientRect();
      var axisW = api.chart.priceScale("right").width();
      var paneH = api.chart.panes()[0].getHeight();
      var x = ev.clientX - rect.left, y = ev.clientY - rect.top;
      if (x < rect.width - axisW || y > paneH) return; // not over the main price axis
      var range = scale.getVisibleRange();
      if (!range) return;
      ev.preventDefault();
      ev.stopPropagation();
      // Same maths as Lightweight Charts' own price-axis drag (scale around the centre of the
      // visible range, k = (start + 0.2*(h-1)) / (now + 0.2*(h-1))): one wheel notch acts like
      // dragging the axis ~30 px from its middle. Wheel up = drag up = taller candles.
      var dy = ev.deltaY * (ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? paneH : 1);
      var c = 0.2 * (paneH - 1), start = paneH / 2;
      var now = Math.max(0, start - dy * 0.3);
      var k = Math.max((start + c) / (now + c), 0.1);
      var mid = (range.from + range.to) / 2, half = (range.to - range.from) / 2 * k;
      scale.setVisibleRange({ from: mid - half, to: mid + half });
      syncBtn();
    }, { passive: false, capture: true });
    // dragging the axis also turns auto-scale off: keep the button state in sync
    el.addEventListener("pointerup", syncBtn);
    el.addEventListener("dblclick", function () { setTimeout(syncBtn, 0); });
    api.syncAutoscaleBtn = syncBtn;
  }

  // Night mode switch: recolour the live chart. Price lines, markers and volume colours are set per pattern,
  // so the open pattern is redrawn, keeping the user's time range and manual price zoom.
  function restyleChart() {
    var api = chartApi;
    if (!api) return;
    var LW = api.LW;
    api.chart.applyOptions({
      layout: { background: { type: LW.ColorType.Solid, color: C.bg }, textColor: C.text,
        panes: { separatorColor: C.line, separatorHoverColor: C.grid } },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line },
      crosshair: { vertLine: { color: C.cross, labelBackgroundColor: C.crossLabel },
        horzLine: { color: C.cross, labelBackgroundColor: C.crossLabel } }
    });
    api.candles.applyOptions({ upColor: C.bg, downColor: C.text, borderUpColor: C.text, borderDownColor: C.text,
      wickUpColor: C.text, wickDownColor: C.text });
    api.volume.priceScale().applyOptions({ borderColor: C.line });
    api.shape.applyOptions({ color: C.shape });
    api.neck.applyOptions({ color: C.accent });
    api.shapeProv.applyOptions({ color: C.accentSoft });
    api.triRes.applyOptions({ color: C.down }); api.triResExt.applyOptions({ color: C.down });
    api.triSup.applyOptions({ color: C.up }); api.triSupExt.applyOptions({ color: C.up });
    if (isTri()) {
      var tr = state.tdata && state.tdata.results.find(function (x) { return x.pattern_id === state.tselected; });
      var tc = tr && triChartCache[tr.pattern_id];
      if (!tr || !tc) return;
      var tts = api.chart.timeScale(), tlr = tts.getVisibleLogicalRange();
      var tscale = api.candles.priceScale(), tpr = tscale.options().autoScale === false ? tscale.getVisibleRange() : null;
      drawTriChart(tr, tc);
      if (tlr) tts.setVisibleLogicalRange(tlr);
      if (tpr) { tscale.setVisibleRange(tpr); if (api.syncAutoscaleBtn) api.syncAutoscaleBtn(); }
      return;
    }
    var r = state.data && state.data.results.find(function (x) { return x.pattern_id === state.selected; });
    var c = r && chartCache[r.pattern_id];
    if (!r || !c) return;
    var ts = api.chart.timeScale(), lr = ts.getVisibleLogicalRange();
    var scale = api.candles.priceScale(), pr = scale.options().autoScale === false ? scale.getVisibleRange() : null;
    drawChart(r, c);
    if (lr) ts.setVisibleLogicalRange(lr);
    if (pr) { scale.setVisibleRange(pr); if (api.syncAutoscaleBtn) api.syncAutoscaleBtn(); }
  }

  function drawChart(r, c) {
    var api = initChart();
    if (!api) {
      chartState("error", "<p>圖表程式庫（cdn.jsdelivr.net 上的 Lightweight Charts）載入失敗，所以畫不出 K 線；清單與數據仍可使用。檢查網路後重新整理。</p>");
      return;
    }
    chartState(null);
    var d = rowMarket(r) === "tw" ? 2 : Math.abs(r.current_close) >= 10000 ? 0 : decimalsFor(r.current_close);
    api.candles.applyOptions({ priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) } });
    api.candles.setData(c.bars.map(function (b) {
      return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] };
    }));
    api.volume.setData(c.bars.map(function (b) {
      return { time: b[0], value: b[5], color: b[4] >= b[1] ? C.volUp : C.volDown };
    }));
    // a newly opened pattern always starts auto-scaled (manual Y zoom is per view)
    api.candles.priceScale().setAutoScale(true);
    if (api.syncAutoscaleBtn) api.syncAutoscaleBtn();
    var prov = !!c.rs_provisional;
    var line = function (p) { return { time: p.time, value: p.price }; };
    [api.triRes, api.triResExt, api.triSup, api.triSupExt].forEach(function (s) { s.setData([]); });
    $("tri-legend").hidden = true;
    api.shape.setData((prov ? c.points.slice(0, 4) : c.points).map(line));
    api.shapeProv.setData(prov ? c.points.slice(3).map(line) : []);
    api.neck.setData(c.neckline.map(line));
    // TP1 / TP2 / invalidation: horizontal dashed lines at the JSON values (bars file `levels`).
    api.levelLines.forEach(function (pl) { api.candles.removePriceLine(pl); });
    var LS_ = api.LW.LineStyle;
    var levels = settings.showLevels && Array.isArray(c.levels) ? c.levels : [];
    var LEVEL_STYLE = {
      tp1: { color: C.tp, style: LS_.Dashed }, tp2: { color: C.tp2, style: LS_.LargeDashed },
      invalidation_price: { color: C.stop, style: LS_.Dashed }
    };
    api.levelLines = levels.map(function (lv) {
      var st = LEVEL_STYLE[lv.key] || { color: C.faint, style: LS_.Dotted };
      var isTp = lv.key === "tp2"; // D28: TP1 does not depend on the breakout
      return api.candles.createPriceLine({
        price: lv.price, color: st.color, lineWidth: 1, lineStyle: st.style, axisLabelVisible: true,
        title: lv.label + (isTp && c.levels_estimated ? " 預估" : "")
      });
    });
    // keep the target / invalidation lines inside the visible price range
    var lvPrices = levels.map(function (lv) { return lv.price; });
    api.candles.applyOptions({
      autoscaleInfoProvider: function (orig) {
        var res = orig();
        if (!res || !res.priceRange || !lvPrices.length) return res;
        return { priceRange: {
          minValue: Math.min.apply(null, lvPrices.concat([res.priceRange.minValue])),
          maxValue: Math.max.apply(null, lvPrices.concat([res.priceRange.maxValue]))
        }, margins: res.margins };
      }
    });
    var ms = c.points.map(function (p) {
      var low = p.label === "LS" || p.label === "H" || p.label === "RS";
      var tentative = p.provisional === true;
      return { time: p.time, position: low ? "belowBar" : "aboveBar", shape: low ? "arrowUp" : "arrowDown",
        color: tentative ? C.accentSoft : C.accent, text: tentative ? "RS 暫定" : p.label, size: 1 };
    });
    if (c.breakout) {
      ms.push({ time: c.breakout.time, position: "aboveBar", shape: "circle", color: C.down, text: "突破", size: 1 });
    }
    ms.sort(function (a, b) { return a.time - b.time; });
    api.markers.setMarkers(ms);
    // Frame the pattern. Fresh patterns end near the last bar, so this shows everything;
    // for stale ones the user can scroll right to today.
    var lastIdx = c.bars.length - 1, lsIdx = 0, rsIdx = 0;
    for (var i = 0; i < c.bars.length; i++) {
      if (c.bars[i][0] === c.points[0].time) lsIdx = i;
      if (c.bars[i][0] === c.points[4].time) { rsIdx = i; break; }
    }
    var to = Math.min(lastIdx, rsIdx + Math.max(20, Math.round((rsIdx - lsIdx) * 0.8)));
    if (c.breakout && r.status !== "stale") to = lastIdx;
    // a provisional RS sits on the last bars: leave room for its "RS 暫定" label
    api.chart.timeScale().applyOptions({ rightOffset: prov ? 12 : 6 });
    if (to >= lastIdx) api.chart.timeScale().fitContent();
    else api.chart.timeScale().setVisibleLogicalRange({ from: -2, to: to + 6 });
    $("chart").setAttribute("aria-label", symText(r) + " " + tfName(r.timeframe) +
      " K 線圖，標出 LS、N1、H、N2、RS 五個點與頸線" + (prov ? "（右肩暫定）" : "") +
      (c.breakout ? "，以及突破點" : "") + (levels.length ? "，TP1、TP2 與失效價水平線" : ""));
    $("chart-legend").hidden = false;
    var toggle = function (sel, show) { var el = document.querySelector(sel); if (el) el.hidden = !show; };
    toggle(".lg--brk", !!c.breakout);
    toggle(".lg--prov", prov);
    toggle(".lg--tp", levels.length > 0);
    toggle(".lg--stop", levels.length > 0);
    var note = $("chart-note");
    var tw4h = rowMarket(r) === "tw" && r.timeframe === "4h";
    note.textContent = (prov ? "RS 是暫定點：" + PROVISIONAL_NOTE : "") +
      (tw4h ? (prov ? " " : "") + TW_4H_NOTE : "");
    note.hidden = !prov && !tw4h;
  }

  /* ---------- D38 converging triangles (data/triangles.json; every number comes from build_site.py) ---------- */
  function triPass(r, skip) {
    return matchQ(r) &&
      (skip === "ttf" || state.ttf === "all" || r.timeframe === state.ttf) &&
      (skip === "tst" || state.tst === "all" || r.status === state.tst) &&
      (skip === "ttype" || state.ttype === "all" || r.triangle_type === state.ttype);
  }
  function triNear(r) { // breakouts first, then the nearer of the two lines (JSON distances)
    if (r.breakout_direction) return -1;
    return Math.min(Math.abs(r.distance_to_resistance), Math.abs(r.distance_to_support));
  }
  var TRI_SORTS = {
    score: function (a, b) { return a.rank - b.rank; },
    near: function (a, b) { return (triNear(a) - triNear(b)) || (a.rank - b.rank); },
    recent: function (a, b) { return (Date.parse(b.signal_time) - Date.parse(a.signal_time)) || (a.rank - b.rank); },
    name: function (a, b) { return a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : a.rank - b.rank; }
  };
  function triFiltered() {
    return state.tdata.results.filter(function (r) { return triPass(r); }).sort(TRI_SORTS[state.tsort] || TRI_SORTS.score);
  }
  function renderTriFilters() {
    var res = state.tdata.results, s = state.tdata.settings;
    var byTf = {}, bySt = {}, byTy = {}, nTf = 0, nSt = 0, nTy = 0, hasStale = false;
    res.forEach(function (r) {
      if (r.status === "stale") hasStale = true;
      if (triPass(r, "ttf")) { byTf[r.timeframe] = (byTf[r.timeframe] || 0) + 1; nTf++; }
      if (triPass(r, "tst")) { bySt[r.status] = (bySt[r.status] || 0) + 1; nSt++; }
      if (triPass(r, "ttype")) { byTy[r.triangle_type] = (byTy[r.triangle_type] || 0) + 1; nTy++; }
    });
    $("filter-tf").innerHTML = chip("ttf", "all", "全部", nTf) + s.timeframes.map(function (tf) {
      return chip("ttf", tf, tfName(tf), byTf[tf] || 0);
    }).join("");
    $("filter-status").innerHTML = chip("tst", "all", "全部", nSt) + TRI_STATUS_ORDER.filter(function (st) {
      return st !== "stale" || hasStale;
    }).map(function (st) { return chip("tst", st, TRI_STATUS_LABEL[st], bySt[st] || 0); }).join("");
    $("filter-ttype").innerHTML = chip("ttype", "all", "全部", nTy) + TRI_TYPE_ORDER.map(function (k) {
      return chip("ttype", k, TRI_TYPE_LABEL[k], byTy[k] || 0);
    }).join("");
  }
  function triGlyph(r) {
    var W = 52, H = 30, P = 3, sh = r.shape;
    var pts = sh.resistance.concat(sh.support, [sh.last]);
    if (sh.breakout) pts.push(sh.breakout);
    var t0 = Math.min.apply(null, pts.map(function (p) { return p[0]; }));
    var t1 = Math.max.apply(null, pts.map(function (p) { return p[0]; }));
    var lo = Math.min.apply(null, pts.map(function (p) { return p[1]; }));
    var hi = Math.max.apply(null, pts.map(function (p) { return p[1]; }));
    var x = function (t) { return (P + (t - t0) / ((t1 - t0) || 1) * (W - 2 * P)).toFixed(1); };
    var y = function (p) { return (H - P - (p - lo) / ((hi - lo) || 1) * (H - 2 * P)).toFixed(1); };
    var seg = function (cls, a) {
      return "<line class=\"" + cls + "\" x1=\"" + x(a[0][0]) + "\" y1=\"" + y(a[0][1]) + "\" x2=\"" + x(a[1][0]) + "\" y2=\"" + y(a[1][1]) + "\"/>";
    };
    var svg = "<svg class=\"glyph\" viewBox=\"0 0 " + W + " " + H + "\" aria-hidden=\"true\" focusable=\"false\">" +
      seg("g-res", sh.resistance) + seg("g-sup", sh.support) +
      "<circle class=\"g-last\" r=\"2\" cx=\"" + x(sh.last[0]) + "\" cy=\"" + y(sh.last[1]) + "\"/>";
    if (sh.breakout) svg += "<circle class=\"g-tbrk\" r=\"2.2\" cx=\"" + x(sh.breakout[0]) + "\" cy=\"" + y(sh.breakout[1]) + "\"/>";
    return svg + "</svg>";
  }
  function signedPct2(x) { return (x < 0 ? "−" : "") + pct(Math.abs(x), 1); }
  function triDistText(r) {
    if (r.status === "breakout_up" || r.status === "breakout_down") {
      return (r.bars_since_breakout === 0 ? "最新一根" : r.bars_since_breakout + " 根前") + TRI_STATUS_LABEL[r.status];
    }
    return "距阻力 " + signedPct2(r.distance_to_resistance) + "，距支撐 " + signedPct2(r.distance_to_support);
  }
  function triRowHtml(r) {
    return "<li class=\"rowwrap\"><button type=\"button\" class=\"row row--tri\" data-id=\"" + esc(r.pattern_id) + "\" aria-current=\"" +
      (state.tselected === r.pattern_id) + "\" aria-label=\"" + esc(symText(r) + " " + tfName(r.timeframe) + "，" + marketText(r) + "，" +
      TRI_TYPE_LABEL[r.triangle_type] + "，" + TRI_STATUS_LABEL[r.status] + "，" + triDistText(r) + "，分數 " + r.score.toFixed(1)) + "\">" +
      triGlyph(r) +
      "<span class=\"row__name\"><span class=\"sym\">" + symHtml(r) + "</span>" +
      "<span class=\"tf\">" + esc(tfName(r.timeframe)) + "</span>" +
      "<span class=\"mkt\">" + esc(marketText(r)) + "</span></span>" +
      "<span class=\"row__score\">" + r.score.toFixed(1) + "<small>分</small></span>" +
      "<span class=\"row__sub\"><span class=\"ttag ttag--" + esc(r.triangle_type) + "\">" + TRI_TYPE_LABEL[r.triangle_type] + "</span>" +
      "<span class=\"status status--" + esc(r.status) + "\">" + TRI_STATUS_LABEL[r.status] + "</span>" +
      "<span class=\"dist\">" + esc(triDistText(r)) + "</span></span></button></li>";
  }
  function clearTriFilters() {
    state.q = ""; state.ttf = "all"; state.tst = "all"; state.ttype = "all"; state.shown = PAGE;
    $("q").value = "";
    update();
  }
  function renderTriList() {
    var all = state.tdata.results, rows = triFiltered(), d = state.tdata;
    $("notice").hidden = true;
    $("more").hidden = true;
    if (!all.length) {
      $("count").textContent = "目前沒有收斂三角";
      $("list").innerHTML = "";
      showNotice("empty", "目前沒有符合條件的收斂三角",
        "最近一次掃描（" + fmtStamp(d.generated_at) + "）在 " + d.scanned_symbols + " " + unitWord() + "的 " +
        esc(d.settings.timeframes.map(tfName).join("、")) + " K 線上，沒有找到收斂中或剛突破的三角形。");
      return rows;
    }
    if (!rows.length) {
      $("count").textContent = "沒有符合的三角形";
      $("list").innerHTML = "";
      showNotice("empty", "沒有符合的三角形",
        (state.q ? "找不到代號或名稱含「" + esc(state.q) + "」的" + (isTw() ? "股票" : "幣種") : "這個篩選下沒有三角形") +
        "。換一個週期、狀態或類型看看，或清除篩選看全部 " + all.length + " 個。", { label: "清除篩選", run: clearTriFilters });
      return rows;
    }
    var shown = Math.min(rows.length, state.shown);
    var sel = rows.findIndex(function (r) { return r.pattern_id === state.tselected; });
    if (sel >= shown) shown = sel + 1;
    $("count").textContent = "共 " + rows.length + " 個三角形" + (rows.length < all.length ? "（全部 " + all.length + " 個）" : "");
    $("list").innerHTML = rows.slice(0, shown).map(triRowHtml).join("");
    if (shown < rows.length) {
      var n = Math.min(PAGE, rows.length - shown);
      $("more").textContent = "再顯示 " + n + " 個（還有 " + (rows.length - shown) + " 個）";
      $("more").hidden = false;
    }
    return rows;
  }
  function triCrossText(r) { return r.breakout_basis === "high" ? "最高價／最低價（含影線）" : "收盤價"; }
  function triSummary(r) {
    var tf = r.timeframe, type = TRI_TYPE_LABEL[r.triangle_type];
    var touch = "阻力線 " + r.touches_resistance + " 次、支撐線 " + r.touches_support + " 次觸碰";
    var close = "最新收盤 " + fmtPrice(r.current_close) + "。";
    if (r.status === "breakout_up" || r.status === "breakout_down") {
      return type + "，" + fmtBar(r.breakout_time, tf) + " 那根 K 線的" + triCrossText(r) +
        (r.status === "breakout_up" ? "站上阻力線" : "跌破支撐線") + "（" + TRI_STATUS_LABEL[r.status] + "），到現在過了 " +
        r.bars_since_breakout + " 根。" + touch + "，突破時已走到頂點的 " + pct(r.apex_progress, 0) + "。" + close;
    }
    if (r.status === "stale") {
      return type + "，已過期，只供回顧" + (r.stale_reason === "apex" ? "：走到頂點都沒有突破。" :
        r.stale_reason === "failed_breakout" ? "：突破後又跌回三角形內。" : "：突破已久。") + close;
    }
    return type + "，收斂中：" + touch + "，已走到頂點的 " + pct(r.apex_progress, 0) + (r.near_apex ? "（接近頂點）" : "") +
      "。" + close + "距阻力線 " + signedPct2(r.distance_to_resistance) + "、距支撐線 " + signedPct2(r.distance_to_support) + "。";
  }
  var TRI_SCORE_PARTS = [["touch_score", "觸碰次數", 20], ["fit_score", "貼線程度", 15], ["convergence_score", "收斂程度", 20],
    ["fill_score", "型態填滿", 15], ["breakout_score", "突破／接近突破", 20], ["clarity_score", "類型明確", 10]];
  function renderTriDetail(r) {
    var tf = r.timeframe;
    var px = function (p) { return fmtPrice(p, r.current_close); };
    var up = r.target_direction === "up";
    var est = r.target_estimated ? "（預估）" : "";
    $("detail").hidden = false;
    $("detail-head").innerHTML =
      "<div class=\"detail__title\"><h2>" + symHtml(r) + "</h2>" +
      "<span class=\"tf\">" + esc(tfName(tf)) + "</span>" +
      "<span class=\"mkt mkt--lg\">" + esc(marketText(r)) + "</span>" +
      "<span class=\"ttag ttag--" + esc(r.triangle_type) + "\">" + TRI_TYPE_LABEL[r.triangle_type] + "</span>" +
      "<span class=\"status status--" + esc(r.status) + "\">" + TRI_STATUS_LABEL[r.status] + "</span>" +
      "<span class=\"score\">分數 <b>" + r.score.toFixed(1) + "</b></span>" +
      "<a class=\"tvlink\" href=\"" + esc(tvUrl(r)) + "\" target=\"_blank\" rel=\"noopener\">在 TradingView 開啟</a></div>" +
      "<p class=\"summary\">" + esc(triSummary(r)) + "</p>" +
      "<p class=\"tri-nostar\">" + esc(TRI_NOSTAR) + "</p>";
    var touches = (r.touches || []).map(function (t) {
      return "<tr><td class=\"tside tside--" + esc(t.side) + "\">" + TRI_SIDE_LABEL[t.side] + "</td><td>" + fmtBar(t.time, tf) +
        "</td><td>" + px(t.price) + "</td></tr>";
    }).join("");
    $("detail-body").innerHTML =
      "<section><h3>目標與失效</h3><dl class=\"facts\">" +
      "<dt>" + (r.target_estimated ? "預估突破價（目前" + (up ? "阻力線" : "支撐線") + "）" : "突破價（突破那根的" + (up ? "阻力線" : "支撐線") + "價）") +
      "</dt><dd>" + px(r.target_basis_price) + "</dd>" +
      "<dt><span class=\"key key--tp\" aria-hidden=\"true\"></span>目標價" + est + "</dt><dd>" + px(r.target) + "</dd>" +
      "<dt><span class=\"key key--tp2\" aria-hidden=\"true\"></span>保守目標" + est + "</dt><dd>" + px(r.target_conservative) + "</dd>" +
      "<dt><span class=\"key key--stop\" aria-hidden=\"true\"></span>失效價</dt><dd>" + px(r.invalidation_price) + "</dd>" +
      "</dl><p class=\"note\">目標價＝突破價" + (up ? "＋" : "−") + "三角形高度 " + px(r.target_height) + "（" +
      (up ? "向上" : "向下") + (r.target_estimated ? "，還沒突破，依" + (r.bias === "neutral" ? "較常見的向上方向" : "型態偏向") + "預估" : "") +
      "）；保守目標＝高度 × " + pct(r.target_hit_rate, 0) + "（Bulkowski 統計的" + TRI_TYPE_LABEL[r.triangle_type] +
      (up ? "向上" : "向下") + "突破達標比例，美股日線，僅供參考）。失效價：" +
      (up ? "跌破型態內最後一個低點" : r.triangle_type === "ascending" ? "站回阻力線" : "站上型態內最後一個高點") +
      "，突破就不成立。</p></section>" +
      "<section><h3>量測</h3><dl class=\"facts\">" +
      "<dt>類型</dt><dd>" + TRI_TYPE_LABEL[r.triangle_type] + "</dd>" +
      "<dt>偏向</dt><dd>" + esc(TRI_BIAS_LABEL[r.bias] || r.bias) + "</dd>" +
      "<dt>觸碰次數</dt><dd>阻力 " + r.touches_resistance + "／支撐 " + r.touches_support + "</dd>" +
      "<dt>收斂進度（到頂點）</dt><dd>" + pct(r.apex_progress, 0) + (r.near_apex ? "（接近頂點）" : "") + "</dd>" +
      "<dt>起點寬度</dt><dd>" + pct(r.height_start_pct, 1) + "</dd>" +
      "<dt>型態長度</dt><dd>" + r.pattern_bars + " 根</dd>" +
      "<dt>頂點</dt><dd>" + fmtBar(r.apex_time, tf) + (r.apex_bars_ahead > 0 ? "（約 " + Math.round(r.apex_bars_ahead) + " 根後）" : "") + "</dd>" +
      "<dt>目前價</dt><dd>" + px(r.current_close) + "</dd>" +
      "<dt>目前阻力線價</dt><dd>" + px(r.current_resistance) + "（" + signedPct2(r.distance_to_resistance) + "）</dd>" +
      "<dt>目前支撐線價</dt><dd>" + px(r.current_support) + "（" + signedPct2(r.distance_to_support) + "）</dd>" +
      "<dt>最後一根 K 線</dt><dd>" + fmtBar(r.as_of_time, tf) + (tf === "1w" ? " 當週" : "") + "</dd>" +
      "</dl>" + (r.triangle_type === "descending"
        ? "<p class=\"note\">下降三角傳統上偏空，但 Bulkowski 的統計裡有 53% 向上突破；方向請以實際突破為準。</p>" : "") +
      "</section>" +
      "<section><h3>分數拆解</h3><div class=\"scorebars\">" + TRI_SCORE_PARTS.map(function (p) {
        return scoreBar(p[1], r[p[0]], p[2]);
      }).join("") + "</div></section>" +
      "<section><h3>觸碰點</h3><table class=\"points\"><thead><tr><th scope=\"col\">線</th><th scope=\"col\">K 線</th><th scope=\"col\">價格</th></tr></thead><tbody>" +
      touches + "</tbody></table></section>";
    loadTriChart(r);
  }
  function loadTriChart(r) {
    var id = r.pattern_id, url = MARKETS[rowMarket(r)].dir + r.chart_file;
    if (triChartCache[id]) { drawTriChart(r, triChartCache[id]); return; }
    chartState("loading", "<p>正在載入 K 線…</p>");
    fetch(url, { cache: "no-store" })
      .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(function (c) {
        if (!c || !Array.isArray(c.bars) || !c.bars.length) throw new Error("檔案格式不對");
        triChartCache[id] = c;
        if (isTri() && state.tselected === id) drawTriChart(r, c);
      })
      .catch(function (err) {
        if (!isTri() || state.tselected !== id) return;
        chartState("error", "<p>這個三角形的 K 線檔 <code>" + esc(url) + "</code> 讀不到（" +
          esc(err && err.message ? err.message : err) + "）。可能是掃描剛好在更新，重新整理通常就好；右側數據仍可參考。</p>" +
          "<button type=\"button\" id=\"chart-retry\">再試一次</button>");
        $("chart-retry").addEventListener("click", function () { loadTriChart(r); });
      });
  }
  function drawTriChart(r, c) {
    var api = initChart();
    if (!api) {
      chartState("error", "<p>圖表程式庫（cdn.jsdelivr.net 上的 Lightweight Charts）載入失敗，所以畫不出 K 線；清單與數據仍可使用。檢查網路後重新整理。</p>");
      return;
    }
    chartState(null);
    var d = rowMarket(r) === "tw" ? 2 : Math.abs(r.current_close) >= 10000 ? 0 : decimalsFor(r.current_close);
    api.candles.applyOptions({ priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) } });
    var future = Array.isArray(c.future_times) ? c.future_times : [];
    // future_times: empty bars so the dashed extensions reach the apex on the bar-indexed time axis
    api.candles.setData(c.bars.map(function (b) {
      return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] };
    }).concat(future.map(function (t) { return { time: t }; })));
    api.volume.setData(c.bars.map(function (b) {
      return { time: b[0], value: b[5], color: b[4] >= b[1] ? C.volUp : C.volDown };
    }));
    api.candles.priceScale().setAutoScale(true);
    if (api.syncAutoscaleBtn) api.syncAutoscaleBtn();
    var line = function (p) { return { time: p.time, value: p.price }; };
    api.shape.setData([]); api.shapeProv.setData([]); api.neck.setData([]);
    api.triRes.setData(c.resistance_line.map(line)); api.triResExt.setData((c.resistance_ext || []).map(line));
    api.triSup.setData(c.support_line.map(line)); api.triSupExt.setData((c.support_ext || []).map(line));
    api.levelLines.forEach(function (pl) { api.candles.removePriceLine(pl); });
    var LS_ = api.LW.LineStyle;
    var levels = settings.showLevels && Array.isArray(c.levels) ? c.levels : [];
    var LEVEL_STYLE = { target: { color: C.tp, style: LS_.Dashed }, target_conservative: { color: C.tp2, style: LS_.LargeDashed },
      invalidation_price: { color: C.stop, style: LS_.Dashed } };
    api.levelLines = levels.map(function (lv) {
      var st = LEVEL_STYLE[lv.key] || { color: C.faint, style: LS_.Dotted };
      return api.candles.createPriceLine({
        price: lv.price, color: st.color, lineWidth: 1, lineStyle: st.style, axisLabelVisible: true,
        title: lv.label + (lv.key !== "invalidation_price" && c.levels_estimated ? " 預估" : "")
      });
    });
    var lvPrices = levels.map(function (lv) { return lv.price; });
    api.candles.applyOptions({
      autoscaleInfoProvider: function (orig) {
        var res = orig();
        if (!res || !res.priceRange || !lvPrices.length) return res;
        return { priceRange: {
          minValue: Math.min.apply(null, lvPrices.concat([res.priceRange.minValue])),
          maxValue: Math.max.apply(null, lvPrices.concat([res.priceRange.maxValue]))
        }, margins: res.margins };
      }
    });
    var ms = (c.touches || []).map(function (t) {
      var res = t.side === "resistance";
      return { time: t.time, position: res ? "aboveBar" : "belowBar", shape: "circle", color: res ? C.down : C.up, size: 0.6 };
    });
    if (c.breakout) {
      var bu = c.breakout.direction === "up";
      ms.push({ time: c.breakout.time, position: bu ? "aboveBar" : "belowBar", shape: bu ? "arrowUp" : "arrowDown",
        color: C.accent, text: bu ? "向上突破" : "向下突破", size: 1 });
    }
    ms.sort(function (a, b) { return a.time - b.time; });
    api.markers.setMarkers(ms);
    var startIdx = 0;
    for (var i = 0; i < c.bars.length; i++) { if (c.bars[i][0] === c.resistance_line[0].time) { startIdx = i; break; } }
    api.chart.timeScale().applyOptions({ rightOffset: 4 });
    api.chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, startIdx - 10), to: c.bars.length - 1 + future.length + 3 });
    $("chart").setAttribute("aria-label", symText(r) + " " + tfName(r.timeframe) + " K 線圖，標出" + TRI_TYPE_LABEL[r.triangle_type] +
      "的阻力線、支撐線與觸碰點" + (c.breakout ? "，以及突破點" : "") + (levels.length ? "，目標價與失效價水平線" : ""));
    $("chart-legend").hidden = true;
    $("tri-legend").hidden = false;
    var toggle = function (sel, show) { var el = document.querySelector(sel); if (el) el.hidden = !show; };
    toggle(".lg--tbrk", !!c.breakout);
    toggle("#tri-legend .lg--tp", levels.length > 0);
    toggle("#tri-legend .lg--stop", levels.length > 0);
    toggle(".lg--ext", (c.resistance_ext || []).length > 0);
    var note = $("chart-note");
    var tw4h = rowMarket(r) === "tw" && r.timeframe === "4h";
    var apexNote = c.apex && c.apex.shown === false
      ? "頂點約在 " + fmtBar(c.apex.time, r.timeframe) + "，離得太遠，虛線只畫到型態長度一倍處。" : "";
    note.textContent = apexNote + (tw4h ? (apexNote ? " " : "") + TW_4H_NOTE : "");
    note.hidden = !apexNote && !tw4h;
  }
  function selectTri(id, fromUser) {
    var r = state.tdata.results.find(function (x) { return x.pattern_id === id; });
    if (!r) return;
    state.tselected = id;
    Array.prototype.forEach.call(document.querySelectorAll(".row"), function (b) {
      b.setAttribute("aria-current", String(b.getAttribute("data-id") === id));
    });
    renderTriDetail(r);
    if (fromUser) {
      try { history.replaceState(null, "", location.pathname + location.search + "#" + encodeURIComponent(id)); } catch (e) { /* file:// */ }
      if (narrow.matches) $("detail").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    }
  }
  function updateTri(keepScroll) {
    renderTriFilters();
    var rows = renderTriList();
    if (!keepScroll) $("scroller").scrollTop = 0;
    if (!rows.length) { $("detail").hidden = true; return; }
    var keep = rows.some(function (r) { return r.pattern_id === state.tselected; });
    if (!keep || !chartApi) selectTri(keep ? state.tselected : rows[0].pattern_id, false);
  }
  var IHS_SORT_HTML = null;
  // Show the controls of the current view: type filter + triangle sorts, or neckline filter + IHS sorts and the star toggle.
  function renderViewChrome() {
    var tri = isTri(), rc = isRec();
    $("record").hidden = !rc;
    document.querySelector(".board").hidden = rc;
    if (IHS_SORT_HTML === null) IHS_SORT_HTML = $("sort").innerHTML;
    $("grp-ttype").hidden = !tri;
    $("grp-neck").hidden = tri;
    $("sort").innerHTML = tri ? TRI_SORT_OPTIONS.map(function (o) { return opt(o[0], o[1], state.tsort); }).join("") : IHS_SORT_HTML;
    $("sort").value = tri ? state.tsort : state.sort;
    $("only-wl").hidden = tri;
    var howIhs = document.querySelector("[aria-labelledby=\"howto-title\"]");
    if (howIhs) howIhs.hidden = tri || rc;
    $("howto-tri").hidden = !tri || rc;
    $("chart-legend").hidden = true;
    $("tri-legend").hidden = true;
    document.documentElement.setAttribute("data-view", rc ? "rec" : tri ? "tri" : "ihs");
    renderDownload();
  }
  // D39: the Taiwan 右肩已確認 Excel (data/tw/rs_confirmed.xlsx, written by every TW scan) -- TW IHS list only
  var XLSX_URL = MARKETS.tw.dir + "rs_confirmed.xlsx";
  function renderDownload() {
    var row = $("dl-xlsx-row");
    if (row) row.hidden = !(state.market === "tw" && !isTri() && !isRec());
  }
  function loadTri(m) {
    if (triCache[m]) return Promise.resolve(triCache[m]);
    return fetch(triUrl(m), { cache: "no-store" })
      .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(function (data) {
        if (!data || !Array.isArray(data.results) || !data.settings) throw new Error("檔案格式不對");
        triCache[m] = data; delete triErr[m];
        return data;
      }, function (err) { triErr[m] = err; throw err; });
  }
  function triFail(m, err) {
    var missing = err && /HTTP 404/.test(String(err.message || err));
    $("meta").innerHTML = "<b class=\"meta__mkt\">" + esc(MARKETS[m].label) + "・收斂三角</b>" +
      (missing ? "收斂三角資料還沒產生。" : "讀不到收斂三角資料。");
    showControls(false);
    $("detail").hidden = true;
    $("count").textContent = "";
    $("list").innerHTML = "";
    $("more").hidden = true;
    showNotice(missing ? "empty" : "error", missing ? "收斂三角資料還沒產生" : "讀不到收斂三角資料",
      missing ? "下一次掃描完成後，這裡就會出現" + esc(MARKETS[m].label) + "的上升、下降與對稱三角形。現在可以先看頭肩底。"
        : "<code>" + esc(triUrl(m)) + "</code> 載入失敗（" + esc(err && err.message ? err.message : err) + "）。",
      { label: "回到頭肩底", run: function () { showIhs(m); } });
  }
  // Open the triangle list of market m (selectId: a triangle to open).
  function showTriangles(m, selectId) {
    if (!MARKETS[m]) m = "crypto";
    var changed = m !== state.market || !isTri();
    state.view = "tri"; state.market = m;
    if (changed) {
      state.q = ""; $("q").value = ""; state.shown = PAGE; state.tselected = null;
      state.onlyWl = false; $("only-wl").setAttribute("aria-pressed", "false");
    }
    if (settings.market !== m) { settings.market = m; saveSettings(); }
    setUrlMarket(m, !!selectId);
    renderMarketChrome();
    renderViewChrome();
    $("meta").textContent = "正在讀取" + MARKETS[m].label + "收斂三角資料…";
    return loadTri(m).then(function (data) {
      if (!isTri() || state.market !== m) return;
      state.tdata = data;
      showControls(true);
      if (selectId && data.results.some(function (r) { return r.pattern_id === selectId; })) state.tselected = selectId;
      renderMeta();
      update();
      if (selectId && state.tselected === selectId) selectTri(selectId, false);
      if (drawerEl().classList.contains("is-open")) renderDrawer();
    }, function (err) {
      if (!isTri() || state.market !== m) return;
      state.tdata = null;
      triFail(m, err);
      if (drawerEl().classList.contains("is-open")) renderDrawer();
    });
  }
  // Back to the head-and-shoulders list of market m.
  function showIhs(m) {
    var prev = state.selected;
    state.view = "ihs";
    state.selected = null;
    renderViewChrome();
    state.q = ""; $("q").value = ""; state.shown = PAGE;
    setUrlMarket(m, false);
    return switchMarket(m, prev);
  }

  /* ---------- D41 track record (data/track_record.json; every number comes from ihs_scanner/track_record.py) ---------- */
  var REC_STATUS_LABEL = { open: "追蹤中", tp1: "已達 TP1", tp2: "已達 TP2", failed: "失效", expired: "過期" };
  var REC_STATUS_ORDER = ["open", "tp1", "tp2", "failed", "expired"];
  var REC_PT_LABEL = { ihs: "頭肩底", ascending: "上升三角", descending: "下降三角", symmetrical: "對稱三角" };
  var REC_PT_ORDER = ["ihs", "ascending", "descending", "symmetrical"];
  var REC_SORT_OPTIONS = [["recent", "最新突破"], ["ret_desc", "報酬高到低"], ["ret_asc", "報酬低到高"], ["held", "持有最久"], ["name", "名稱"]];
  var REC_PAGE = 60;
  var recCache = {}, recErr = {};
  var rec = { status: "all", pt: "all", tf: "all", sort: "recent", retro: false, shown: REC_PAGE, open: {} };

  function isRec() { return state.view === "rec"; }
  function recUrl(m) { return MARKETS[m].dir + "track_record.json"; }
  function loadRec(m) {
    if (recCache[m]) return Promise.resolve(recCache[m]);
    return fetch(recUrl(m), { cache: "no-store" })
      .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(function (data) {
        if (!data || !Array.isArray(data.items) || !data.summary) throw new Error("檔案格式不對");
        recCache[m] = data; delete recErr[m];
        return data;
      }, function (err) { recErr[m] = err; throw err; });
  }
  function recPt(it) { return it.kind === "triangle" ? (it.triangle_type || "symmetrical") : "ihs"; }
  function recRate(x) { return typeof x === "number" ? (x * 100).toFixed(0) + "%" : "—"; }
  function recRet(x) { return typeof x === "number" ? signedPct(x) : null; }
  function recRetHtml(x, unknown) {
    var t = recRet(x);
    if (t === null) return "<span class=\"ret ret--na\">" + esc(unknown || "—") + "</span>";
    return "<span class=\"ret " + (x > 0 ? "ret--up" : x < 0 ? "ret--down" : "") + "\">" + esc(t) + "</span>";
  }
  function recDays(it) {
    if (typeof it.days !== "number") return "—";
    var d = it.days < 10 ? it.days.toFixed(1) : String(Math.round(it.days));
    return (typeof it.bars === "number" ? it.bars + " 根・" : "") + d + " 天";
  }
  function recStatusTag(st) { return "<span class=\"status rst rst--" + esc(st) + "\">" + esc(REC_STATUS_LABEL[st] || st) + "</span>"; }
  function recItems() {
    var d = recCache[state.market];
    return d ? d.items.filter(function (it) { return rec.retro || !it.retro; }) : [];
  }
  function recPass(it, skip) {
    return (skip === "status" || rec.status === "all" || it.status === rec.status) &&
      (skip === "pt" || rec.pt === "all" || recPt(it) === rec.pt) &&
      (skip === "tf" || rec.tf === "all" || it.timeframe === rec.tf);
  }
  var REC_SORTS = {
    recent: function (a, b) { return Date.parse(b.breakout_time) - Date.parse(a.breakout_time); },
    ret_desc: function (a, b) { return (typeof b["return"] === "number" ? b["return"] : -9) - (typeof a["return"] === "number" ? a["return"] : -9); },
    ret_asc: function (a, b) { return (typeof a["return"] === "number" ? a["return"] : 9) - (typeof b["return"] === "number" ? b["return"] : 9); },
    held: function (a, b) { return (b.days || 0) - (a.days || 0); },
    name: function (a, b) { return a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : 0; }
  };
  function recChip(group, value, label, count) {
    var cur = rec[group];
    return "<button type=\"button\" class=\"chip\" data-rec=\"" + group + "\" data-value=\"" + esc(value) + "\" aria-pressed=\"" + (cur === value) + "\">" +
      esc(label) + (count === undefined ? "" : "<span class=\"chip__n\">" + count + "</span>") + "</button>";
  }
  function recGroupLabel(g) {
    var k = g.kind === "ihs" ? "頭肩底" : g.kind === "triangle" ? "收斂三角" : "全部型態";
    return k + "・" + (g.timeframe ? tfName(g.timeframe) : "全部週期");
  }
  // One card per overall number + a table by kind x timeframe (all numbers straight from summary)
  function recSummaryHtml(d) {
    var groups = (rec.retro ? d.summary.all : d.summary.live) || [];
    var all = groups.filter(function (g) { return !g.kind && !g.timeframe; })[0];
    if (!all) return "";
    if (!all.n) {
      return "<p class=\"rempty\">" + (rec.retro ? "" : "還沒有即時追蹤到的結果（目前追蹤中的還沒有達成 TP1、失效或過期）。") +
        (all.open ? "追蹤中 " + all.open + " 筆。" : "") + "</p>";
    }
    var card = function (label, val, sub) {
      return "<div class=\"rcard\"><span class=\"rcard__k\">" + esc(label) + "</span><b class=\"rcard__v\">" + val + "</b>" +
        (sub ? "<span class=\"rcard__s\">" + esc(sub) + "</span>" : "") + "</div>";
    };
    var h = "<div class=\"rcards\">" +
      card("已結算／達 TP1", String(all.n), "另有 " + all.open + " 筆追蹤中") +
      card("TP1 達成率", recRate(all.tp1_rate), all.tp1 + " / " + all.n) +
      card("TP2 達成率", recRate(all.tp2_rate), all.tp2 + " / " + all.n) +
      card("失效率", recRate(all.failed_rate), all.failed + " / " + all.n + (all.failed_after_tp1 ? "（其中 " + all.failed_after_tp1 + " 筆先達 TP1）" : "")) +
      card("過期率", recRate(all.expired_rate), all.expired + " / " + all.n) +
      card("平均報酬", recRetHtml(all.avg_return, "—"), all.n_return + " 筆有報酬資料") +
      "</div>";
    var rows = groups.filter(function (g) { return g.n > 0 || g.open > 0; });
    h += "<div class=\"rtable-wrap\"><table class=\"rtable\"><caption class=\"vh\">依型態與週期的彙總</caption><thead><tr>" +
      ["型態・週期", "樣本", "TP1", "TP2", "失效", "過期", "平均報酬", "追蹤中"].map(function (t, i) {
        return "<th scope=\"col\"" + (i ? " class=\"num\"" : "") + ">" + t + "</th>";
      }).join("") + "</tr></thead><tbody>" + rows.map(function (g) {
        return "<tr" + (!g.kind && !g.timeframe ? " class=\"is-total\"" : "") + "><th scope=\"row\">" + esc(recGroupLabel(g)) + "</th>" +
          "<td class=\"num\">" + g.n + "</td><td class=\"num\">" + recRate(g.tp1_rate) + "</td><td class=\"num\">" + recRate(g.tp2_rate) + "</td>" +
          "<td class=\"num\">" + recRate(g.failed_rate) + "</td><td class=\"num\">" + recRate(g.expired_rate) + "</td>" +
          "<td class=\"num\">" + recRetHtml(g.avg_return, "—") + "</td><td class=\"num\">" + g.open + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    return h;
  }
  // Price ladder: invalidation / breakout / TP1 / TP2 on one axis plus the result dot (positions only; values from JSON)
  function recLadder(it) {
    var lv = [["失效", it.invalidation_price, "stop"], ["突破", it.breakout_price, "brk"], ["TP1", it.tp1, "tp"], ["TP2", it.tp2, "tp2"]]
      .filter(function (l) { return typeof l[1] === "number"; });
    if (lv.length < 2) return "";
    var vals = lv.map(function (l) { return l[1]; });
    if (typeof it.result_price === "number") vals.push(it.result_price);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), W = 320, P = 26, Y = 34;
    var x = function (p) { return (P + (p - lo) / ((hi - lo) || 1) * (W - 2 * P)).toFixed(1); };
    var s = "<svg class=\"ladder\" viewBox=\"0 0 " + W + " 70\" role=\"img\" aria-label=\"失效價、突破價、TP1、TP2 與結果的相對位置\">" +
      "<line class=\"ladder__axis\" x1=\"" + P + "\" y1=\"" + Y + "\" x2=\"" + (W - P) + "\" y2=\"" + Y + "\"/>";
    lv.forEach(function (l, i) {
      var up = i % 2 === 0, tx = x(l[1]);
      s += "<line class=\"ladder__tick ladder__tick--" + l[2] + "\" x1=\"" + tx + "\" y1=\"" + (Y - 6) + "\" x2=\"" + tx + "\" y2=\"" + (Y + 6) + "\"/>" +
        "<text class=\"ladder__lbl\" x=\"" + tx + "\" y=\"" + (up ? 18 : 56) + "\" text-anchor=\"middle\">" + esc(l[0]) + "</text>" +
        "<text class=\"ladder__px\" x=\"" + tx + "\" y=\"" + (up ? 28 : 66) + "\" text-anchor=\"middle\">" + esc(fmtPrice(l[1], it.breakout_price)) + "</text>";
    });
    if (typeof it.result_price === "number") {
      s += "<circle class=\"ladder__dot ladder__dot--" + esc(it.status) + "\" cx=\"" + x(it.result_price) + "\" cy=\"" + Y + "\" r=\"5\"><title>" +
        esc((it.result_basis === "last_close" ? "最新收盤 " : "結果 ") + fmtPrice(it.result_price, it.breakout_price)) + "</title></circle>";
    }
    return s + "</svg>";
  }
  function recExists(it) {
    if (it.kind === "triangle") return !!(triCache[state.market] && triCache[state.market].results.some(function (r) { return r.pattern_id === it.pattern_id; }));
    return !!(dataCache[state.market] && dataCache[state.market].results.some(function (r) { return r.pattern_id === it.pattern_id; }));
  }
  function recDetail(it) {
    var px = function (p) { return fmtPrice(p, it.breakout_price); };
    var basis = { tp1: "TP1 價", tp2: "TP2 價", failed: "失效價", expired: "到期收盤", last_close: "最新收盤" }[it.result_basis];
    var kv = [
      ["突破時間", fmtBar(it.breakout_time, it.timeframe)], ["突破價", px(it.breakout_price)],
      ["TP1", px(it.tp1) + (it.tp1_time ? "（" + fmtBar(it.tp1_time, it.timeframe) + " 達成）" : "")],
      ["TP2", px(it.tp2) + (it.tp2_time ? "（" + fmtBar(it.tp2_time, it.timeframe) + " 達成）" : "")],
      ["失效價", px(it.invalidation_price)]
    ];
    if (it.result_time) kv.push([it.status === "open" ? "最新收盤 K 線" : "結果 K 線", fmtBar(it.result_time, it.timeframe)]);
    if (typeof it.result_price === "number") kv.push([basis ? "結果價（" + basis + "）" : "結果價", px(it.result_price)]);
    kv.push(["持有", recDays(it)]);
    if (typeof it.score === "number") kv.push(["偵測分數", it.score.toFixed(1)]);
    var h = "<div class=\"rdet\">" + recLadder(it) + "<dl class=\"rdet__kv\">" + kv.map(function (p) {
      return "<div><dt>" + esc(p[0]) + "</dt><dd>" + esc(p[1]) + "</dd></div>";
    }).join("") + "</dl>";
    if (it.retro) h += "<p class=\"rdet__note\">這筆在開始追蹤時結果就已經發生（追蹤開始前達成），不是即時追蹤到的。</p>";
    if (it.anomaly) h += "<p class=\"rdet__note\">資料異常：失效價高於突破價（或 TP1 不高於突破價），這筆在第一根就會算失效，不計入彙總、不算報酬。</p>";
    if (!it.anomaly && it.status === "expired" && typeof it["return"] !== "number") h += "<p class=\"rdet__note\">過期時的收盤價沒有記錄，報酬未知。</p>";
    if (!it.anomaly && it.status === "open" && typeof it["return"] !== "number") h += "<p class=\"rdet__note\">追蹤檔還沒有記錄最新收盤，下一次掃描後會出現。</p>";
    h += "<p class=\"rdet__links\">" + (recExists(it)
      ? "<button type=\"button\" class=\"rlink\" data-act=\"rec-goto\" data-id=\"" + esc(it.pattern_id) + "\" data-kind=\"" + esc(it.kind) + "\">在掃描頁查看這個型態</button>"
      : "<span class=\"rdet__gone\">這個型態已不在目前的掃描清單中。</span>") +
      "<a class=\"rlink\" href=\"" + esc(tvUrl(it)) + "\" target=\"_blank\" rel=\"noopener\">TradingView</a></p></div>";
    return h;
  }
  function recRowHtml(it) {
    var open = !!rec.open[it.pattern_id], id = "rr-" + esc(String(it.pattern_id).replace(/[^A-Za-z0-9]/g, "_"));
    return "<li class=\"rrow" + (open ? " is-open" : "") + "\"><button type=\"button\" class=\"rrow__btn\" data-act=\"rec-toggle\" data-id=\"" + esc(it.pattern_id) +
      "\" aria-expanded=\"" + open + "\" aria-controls=\"" + id + "\">" +
      "<span class=\"rrow__sym\"><span class=\"sym\">" + symHtml(it) + "</span><span class=\"tf\">" + esc(tfName(it.timeframe)) + "</span>" +
      "<span class=\"mkt\">" + esc(it.pattern_label || REC_PT_LABEL[recPt(it)] || "") + (it.retro ? "・追蹤前已發生" : "") + (it.anomaly ? "・資料異常" : "") + "</span></span>" +
      "<span class=\"rrow__st\">" + recStatusTag(it.status) + "</span>" +
      "<span class=\"rrow__ret\">" + recRetHtml(it["return"], it.anomaly ? "—" : it.status === "expired" ? "未知" : it.status === "open" ? "尚無收盤" : "—") + "</span>" +
      "<span class=\"rrow__held\">" + esc(recDays(it)) + "</span>" +
      "<span class=\"rrow__date\">" + esc(fmtBar(it.breakout_time, it.timeframe)) + "</span></button>" +
      "<div class=\"rrow__more\" id=\"" + id + "\"" + (open ? "" : " hidden") + ">" + (open ? recDetail(it) : "") + "</div></li>";
  }
  function recListHtml(d) {
    var base = recItems();
    var cnt = function (skip, test) { return base.filter(function (it) { return recPass(it, skip) && test(it); }).length; };
    var rows = base.filter(function (it) { return recPass(it); }).sort(REC_SORTS[rec.sort] || REC_SORTS.recent);
    var nRetro = d.items.filter(function (it) { return it.retro && recPass(it); }).length;
    var h = "<div class=\"rfilters\">" +
      "<div class=\"filters__group\" role=\"group\" aria-labelledby=\"rf-st\"><span class=\"filters__name\" id=\"rf-st\">狀態</span><div class=\"chips\">" +
      recChip("status", "all", "全部", cnt("status", function () { return true; })) + REC_STATUS_ORDER.map(function (s) {
        return recChip("status", s, REC_STATUS_LABEL[s], cnt("status", function (it) { return it.status === s; }));
      }).join("") + "</div></div>" +
      "<div class=\"filters__group\" role=\"group\" aria-labelledby=\"rf-pt\"><span class=\"filters__name\" id=\"rf-pt\">型態</span><div class=\"chips\">" +
      recChip("pt", "all", "全部", cnt("pt", function () { return true; })) + REC_PT_ORDER.map(function (p) {
        return recChip("pt", p, REC_PT_LABEL[p], cnt("pt", function (it) { return recPt(it) === p; }));
      }).join("") + "</div></div>" +
      "<div class=\"filters__group\" role=\"group\" aria-labelledby=\"rf-tf\"><span class=\"filters__name\" id=\"rf-tf\">週期</span><div class=\"chips\">" +
      recChip("tf", "all", "全部", cnt("tf", function () { return true; })) + ["4h", "1d", "1w"].map(function (t) {
        return recChip("tf", t, TF_LABEL[t], cnt("tf", function (it) { return it.timeframe === t; }));
      }).join("") + "</div></div>" +
      "<div class=\"filters__group\"><label class=\"filters__name\" for=\"rec-sort\">排序</label><select id=\"rec-sort\">" +
      REC_SORT_OPTIONS.map(function (o) { return "<option value=\"" + o[0] + "\"" + (rec.sort === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") +
      "</select></div>" +
      (d.counts && d.counts.retro ? "<div class=\"filters__group\"><span class=\"filters__name\">範圍</span><div class=\"chips\">" +
        "<button type=\"button\" class=\"chip\" data-rec=\"retro\" aria-pressed=\"" + rec.retro + "\">含追蹤前就已達成的結果<span class=\"chip__n\">" + d.counts.retro + "</span></button></div></div>" : "") +
      "</div>";
    h += "<p class=\"panel__count\" aria-live=\"polite\">共 " + rows.length + " 筆" + (rec.retro ? "" : (nRetro ? "（另有 " + nRetro + " 筆追蹤前已發生的結果未列出）" : "")) + "</p>";
    if (!rows.length) return h + "<p class=\"rempty\">沒有符合條件的紀錄。</p>";
    h += "<ol class=\"rlist\">" + rows.slice(0, rec.shown).map(recRowHtml).join("") + "</ol>";
    if (rows.length > rec.shown) h += "<button type=\"button\" class=\"more\" data-act=\"rec-more\">再顯示 " + Math.min(REC_PAGE, rows.length - rec.shown) + " 筆</button>";
    return h;
  }
  function recNotesHtml(d) {
    var since = d.tracking_since ? fmtDay(d.tracking_since, true) : "—";
    return "<ul class=\"rnotes\"><li><b>追蹤自 " + esc(since) + " 開始</b>，只計入開始追蹤之後的突破。</li>" +
      "<li>這是上線後的真實結果，樣本少時僅供參考。</li><li>型態偵測結果的回顧，<b>不是投資建議</b>。</li>" +
      (d.counts && d.counts.anomaly ? "<li>有 " + d.counts.anomaly + " 筆資料異常（失效價高於突破價），列在明細但不計入彙總。</li>" : "") + "</ul>" +
      "<details class=\"rdef\"><summary>怎麼算？</summary><div>" +
      "<p><b>追蹤對象：</b>已突破的頭肩底，以及向上突破的收斂三角。報酬以「突破價」（突破當根的頸線／阻力線價）為基準。</p>" +
      "<p><b>狀態：</b>先碰到 TP1 → 已達 TP1（繼續追蹤 TP2）；碰到 TP2 → 已達 TP2（結束）；TP 之前（或 TP1 之後）最低價跌到失效價 → 失效；期限內（4 小時線 180 根、日線 120 根、週線 52 根）什麼都沒碰到 → 過期。同一根 K 線同時碰到失效價與 TP，保守算失效。</p>" +
      "<p><b>分母：</b>樣本＝已結束（TP2、失效、過期）加上已達 TP1 的筆數；還在追蹤、什麼都還沒發生的不計入各種比率。TP1 達成率＝曾達 TP1 的筆數（含之後達 TP2 或失效的）÷ 樣本；TP2／失效／過期率各為該狀態筆數 ÷ 樣本，所以失效率包含「先達 TP1 再失效」的筆數。</p>" +
      "<p><b>報酬：</b>達成 TP 用 TP 價、失效用失效價、過期用到期收盤（舊紀錄沒有收盤價就標「未知」、不算進平均）、追蹤中用最新收盤；只達 TP1 還在追蹤的用 TP1 價（保守值）。平均報酬只算有報酬資料的樣本，沒有扣手續費與滑價。</p>" +
      "<p><b>「追蹤前已發生」：</b>剛開始追蹤時就已經達成的結果，不是即時追蹤到的，預設不列入彙總與清單，可用「含追蹤前就已達成的結果」打開。</p>" +
      "</div></details>";
  }
  function renderRecord() {
    var d = recCache[state.market];
    if (!d) return;
    var upd = d.tracker_updated_at || d.generated_at;
    $("meta").innerHTML = "<b class=\"meta__mkt\">" + esc(MARKETS[state.market].label + "・實際戰績") + "</b>追蹤檔更新於 <time datetime=\"" + esc(upd) + "\">" +
      fmtStamp(upd) + "</time>（" + tzLabel() + "），共 " + d.counts.items + " 筆追蹤" + (d.counts.retro ? "（含 " + d.counts.retro + " 筆追蹤前已發生）" : "") + "。";
    $("scope").textContent = MARKETS[state.market].label + "：已突破型態的 TP1／TP2／失效結果";
    var keepY = window.scrollY;
    $("record").innerHTML = "<div class=\"record__in\"><h2 class=\"record__title\">" + esc(MARKETS[state.market].label) + "實際戰績</h2>" +
      recNotesHtml(d) + "<section aria-labelledby=\"rs-t\"><h3 class=\"record__h\" id=\"rs-t\">彙總</h3>" + recSummaryHtml(d) + "</section>" +
      "<section aria-labelledby=\"rl-t\"><h3 class=\"record__h\" id=\"rl-t\">明細</h3><div id=\"rec-list\">" + recListHtml(d) + "</div></section></div>";
    window.scrollTo(0, keepY);
  }
  function renderRecList() { // filters / sort / expand: only the detail list is redrawn (keeps the summary + scroll)
    var el = $("rec-list"), d = recCache[state.market];
    if (!el || !d) { renderRecord(); return; }
    var y = window.scrollY, a = document.activeElement, sel = null;
    if (a && el.contains(a)) { // keep keyboard focus on the same control after the redraw
      if (a.id) sel = "#" + a.id;
      else if (a.getAttribute("data-rec")) sel = "[data-rec=\"" + a.getAttribute("data-rec") + "\"][data-value=\"" + (a.getAttribute("data-value") || "") + "\"]";
      else if (a.getAttribute("data-act") === "rec-toggle") sel = "[data-act=\"rec-toggle\"][data-id=\"" + a.getAttribute("data-id").replace(/["\\]/g, "") + "\"]";
    }
    el.innerHTML = recListHtml(d);
    if (sel) { try { var n = el.querySelector(sel); if (n) n.focus({ preventScroll: true }); } catch (err) { /* ignore */ } }
    window.scrollTo(0, y);
  }
  function recFail(m, err) {
    var missing = err && /HTTP 404/.test(String(err.message || err));
    $("meta").innerHTML = "<b class=\"meta__mkt\">" + esc(MARKETS[m].label + "・實際戰績") + "</b>" + (missing ? "實際戰績資料還沒產生。" : "讀不到實際戰績資料。");
    $("record").innerHTML = "<div class=\"record__in\"><div class=\"notice" + (missing ? "" : " notice--error") + "\"><h3>" +
      (missing ? "實際戰績還沒有資料" : "讀不到實際戰績資料") + "</h3><p>" +
      (missing ? "止盈追蹤每次掃描後更新；第一次掃描完成後這裡就會出現已突破型態的結果。" : "<code>" + esc(recUrl(m)) + "</code> 載入失敗（" + esc(err && err.message ? err.message : err) + "）。") +
      "</p><button type=\"button\" id=\"rec-back\">回到頭肩底</button></div></div>";
    $("rec-back").addEventListener("click", function () { showIhs(m); });
  }
  // Open the track record of market m. Pattern lists are loaded in the background so "查看型態" links know what still exists.
  function showRecord(m) {
    if (!MARKETS[m]) m = "crypto";
    var changed = m !== state.market || !isRec();
    state.view = "rec"; state.market = m;
    if (changed) rec.shown = REC_PAGE;
    if (settings.market !== m) { settings.market = m; saveSettings(); }
    setUrlMarket(m, false);
    renderMarketChrome();
    renderViewChrome();
    $("meta").textContent = "正在讀取" + MARKETS[m].label + "實際戰績…";
    if (!recCache[m]) $("record").innerHTML = "<div class=\"record__in\"><p class=\"rempty\">讀取中…</p></div>";
    var again = function () { if (isRec() && state.market === m && recCache[m]) renderRecList(); };
    loadMarket(m).then(again, function () { /* links just stay hidden */ });
    loadTri(m).then(again, function () { /* same */ });
    return loadRec(m).then(function () {
      if (!isRec() || state.market !== m) return;
      renderRecord();
      if (drawerEl().classList.contains("is-open")) renderDrawer();
    }, function (err) {
      if (!isRec() || state.market !== m) return;
      recFail(m, err);
      if (drawerEl().classList.contains("is-open")) renderDrawer();
    });
  }
  function onRecClick(e) {
    var f = e.target.closest("[data-rec]");
    if (f) {
      var g = f.getAttribute("data-rec");
      if (g === "retro") rec.retro = !rec.retro; else rec[g] = f.getAttribute("data-value");
      rec.shown = REC_PAGE;
      if (g === "retro") renderRecord(); else renderRecList();
      return;
    }
    var t = e.target.closest("[data-act]");
    if (!t) return;
    var act = t.getAttribute("data-act"), id = t.getAttribute("data-id");
    if (act === "rec-toggle") { rec.open[id] = !rec.open[id]; renderRecList(); }
    else if (act === "rec-more") { rec.shown += REC_PAGE; renderRecList(); }
    else if (act === "rec-goto") {
      var m = state.market;
      if (t.getAttribute("data-kind") === "triangle") showTriangles(m, id);
      else { state.selected = id; showIhs(m); }
    }
  }

  /* ---------- wiring ---------- */
  function select(id, fromUser) {
    if (isTri()) { selectTri(id, fromUser); return; }
    var r = state.data.results.find(function (x) { return x.pattern_id === id; });
    if (!r) return;
    state.selected = id;
    Array.prototype.forEach.call(document.querySelectorAll(".row"), function (b) {
      b.setAttribute("aria-current", String(b.getAttribute("data-id") === id));
    });
    renderDetail(r);
    if (fromUser) {
      try { history.replaceState(null, "", "#" + encodeURIComponent(id)); } catch (e) { /* file:// etc. */ }
      if (narrow.matches) {
        $("detail").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      }
    }
  }

  function update(keepScroll) {
    if (isTri()) { updateTri(keepScroll); return; }
    renderFilters();
    var rows = renderList();
    if (!keepScroll) $("scroller").scrollTop = 0;
    if (!rows.length) {
      $("detail").hidden = true;
      return;
    }
    var keep = rows.some(function (r) { return r.pattern_id === state.selected; });
    if (!keep || !chartApi) select(keep ? state.selected : rows[0].pattern_id, false);
  }

  function bind() {
    $("filter-tf").parentNode.parentNode.addEventListener("click", function (e) {
      var b = e.target.closest(".chip");
      if (!b) return;
      state[b.getAttribute("data-group")] = b.getAttribute("data-value");
      state.shown = PAGE;
      update();
      var again = document.querySelector(".chip[data-group=\"" + b.getAttribute("data-group") +
        "\"][data-value=\"" + b.getAttribute("data-value") + "\"]");
      if (again) again.focus();
    });
    $("q").addEventListener("input", function () {
      state.q = this.value.trim().toUpperCase().replace(/\s+/g, "");
      state.shown = PAGE;
      update();
    });
    $("sort").addEventListener("change", function () {
      if (isTri()) state.tsort = this.value; else state.sort = this.value;
      state.shown = PAGE;
      update();
    });
    $("more").addEventListener("click", function () {
      var firstNew = Math.min(state.shown, (isTri() ? triFiltered() : filtered()).length);
      state.shown += PAGE;
      update(true);
      var rows = document.querySelectorAll(".row");
      if (rows[firstNew]) rows[firstNew].focus();
    });
    $("list").addEventListener("click", function (e) {
      var b = e.target.closest(".row");
      if (b) select(b.getAttribute("data-id"), true);
    });
    $("list").addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      var rows = Array.prototype.slice.call(document.querySelectorAll(".row"));
      var i = rows.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].focus();
    });
  }

  /* ---------- storage: every read / write is guarded, the site works with storage disabled ---------- */
  var storageOk = (function () {
    try { var k = "ihs.probe"; window.localStorage.setItem(k, "1"); window.localStorage.removeItem(k); return true; }
    catch (e) { return false; }
  })();
  var demoMode = false; // only set by the ?demoStars=N test parameter: nothing is written to storage then
  function readStore(key) {
    try { var t = window.localStorage.getItem(key); return t === null ? null : JSON.parse(t); }
    catch (e) { return null; }
  }
  function writeStore(key, obj) {
    if (demoMode) return true;
    try { window.localStorage.setItem(key, JSON.stringify(obj)); return true; }
    catch (e) { storageOk = false; return false; }
  }
  function str(x) { return typeof x === "string" ? x.slice(0, 80) : null; }

  /* ---------- settings ---------- */
  function loadSettings() {
    var s = {}, raw = readStore(SET_KEY), k;
    for (k in DEFAULT_SETTINGS) s[k] = DEFAULT_SETTINGS[k];
    if (raw && typeof raw === "object") {
      if (raw.tf === "all" || TF_LABEL[raw.tf]) s.tf = raw.tf;
      if (raw.status === "all" || STATUS_LABEL[raw.status]) s.status = raw.status;
      if (raw.neck === "all" || NECK_LABEL[raw.neck]) s.neck = raw.neck;
      if (typeof raw.sort === "string" && SORTS[raw.sort]) s.sort = raw.sort;
      if (typeof raw.minScore === "number" && raw.minScore >= 0 && raw.minScore <= 100) s.minScore = raw.minScore;
      if (typeof raw.showLevels === "boolean") s.showLevels = raw.showLevels;
      if (typeof raw.showChecks === "boolean") s.showChecks = raw.showChecks;
      if (typeof raw.market === "string" && MARKETS[raw.market]) s.market = raw.market;
      if (typeof raw.theme === "string" && THEME_LABEL[raw.theme]) s.theme = raw.theme;
    }
    return s;
  }
  var settings = loadSettings();
  function saveSettings() { return writeStore(SET_KEY, settings); }

  /* ---------- night mode ---------- */
  var darkMq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function resolvedTheme() {
    if (settings.theme === "auto") return darkMq && darkMq.matches ? "dark" : "light";
    return settings.theme === "dark" ? "dark" : "light";
  }
  function applyTheme() {
    var t = resolvedTheme();
    document.documentElement.setAttribute("data-theme", t);
    C = PALETTE[t];
    var mc = document.querySelector("meta[name=\"theme-color\"]");
    if (mc) mc.setAttribute("content", C.bg);
    var b = $("theme-btn");
    if (b) {
      var label = t === "dark" ? "切換到淺色模式" : "切換到夜間模式";
      b.setAttribute("aria-label", label); b.title = label;
    }
    restyleChart();
  }
  applyTheme();
  if (darkMq) {
    var onOsTheme = function () { if (settings.theme === "auto") applyTheme(); };
    if (darkMq.addEventListener) darkMq.addEventListener("change", onOsTheme);
    else if (darkMq.addListener) darkMq.addListener(onOsTheme);
  }
  function applySettingsToState() {
    state.tf = settings.tf; state.status = settings.status; state.neck = settings.neck;
    state.sort = settings.sort; state.minScore = settings.minScore;
    $("sort").value = state.sort;
  }

  /* ---------- watchlist (keyed by pattern_key, D24: no RS) ---------- */
  function normItem(o) {
    if (!o || typeof o !== "object") return null;
    if (typeof o.pattern_key !== "string" || !o.pattern_key || typeof o.symbol !== "string" || !o.symbol) return null;
    return {
      pattern_key: o.pattern_key.slice(0, 200), symbol: o.symbol.slice(0, 40),
      exchange: str(o.exchange), market: str(o.market), timeframe: str(o.timeframe), name: str(o.name),
      added_at: typeof o.added_at === "string" && !isNaN(Date.parse(o.added_at)) ? o.added_at : new Date().toISOString(),
      status_at_add: STATUS_LABEL[o.status_at_add] ? o.status_at_add : null,
      score_at_add: typeof o.score_at_add === "number" && isFinite(o.score_at_add) ? o.score_at_add : null,
      tv_symbol: str(o.tv_symbol),
      via: o.via === "discord" || o.via === "site" || o.via === "api" ? o.via : null
    };
  }
  function mergeItems(base, incoming) { // -> { items, added, dup, bad }
    var seen = {}, out = [], added = 0, dup = 0, bad = 0;
    base.forEach(function (it) { seen[it.pattern_key] = true; out.push(it); });
    incoming.forEach(function (raw) {
      var it = normItem(raw);
      if (!it) { bad++; return; }
      if (seen[it.pattern_key]) { dup++; return; }
      if (out.length >= 2000) { bad++; return; }
      seen[it.pattern_key] = true; out.push(it); added++;
    });
    return { items: out, added: added, dup: dup, bad: bad };
  }
  var watch = (function () {
    var raw = readStore(WL_KEY);
    var arr = raw && Array.isArray(raw.items) ? raw.items : Array.isArray(raw) ? raw : [];
    return mergeItems([], arr).items;
  })();
  function saveWatch() { return writeStore(WL_KEY, { version: 1, items: watch }); }
  function findWatch(key) {
    for (var i = 0; i < watch.length; i++) if (watch[i].pattern_key === key) return watch[i];
    return null;
  }
  function isStarred(key) { return !!key && !!findWatch(key); }
  var keyIndex = {};
  function buildKeyIndex() { // pattern_key -> row, over every market loaded so far (keys never collide: D29)
    keyIndex = {};
    MARKET_ORDER.forEach(function (m) {
      if (!dataCache[m]) return;
      dataCache[m].results.forEach(function (r) { if (r.pattern_key && !keyIndex[r.pattern_key]) keyIndex[r.pattern_key] = r; });
    });
  }
  function watchInMarket(m) { return watch.filter(function (w) { return rowMarket(w) === m; }).length; }

  var STAR_SVG = "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 3.2l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 17l-5.4 2.9 1.1-6.1L3.2 9.6l6.1-.8z\"/></svg>";
  function starAttrs(on) {
    return { pressed: String(on), label: on ? "從觀察名單移除" : "加入觀察名單" };
  }
  function starBtn(r, cls) {
    if (!r.pattern_key) return "";
    var a = starAttrs(isStarred(r.pattern_key));
    return "<button type=\"button\" class=\"star " + cls + "\" data-key=\"" + esc(r.pattern_key) + "\" aria-pressed=\"" +
      a.pressed + "\" aria-label=\"" + a.label + "\" title=\"" + a.label + "\">" + STAR_SVG + "</button>";
  }
  function toggleStar(key) {
    var cur = findWatch(key), added = null;
    if (cur) {
      watch = watch.filter(function (w) { return w.pattern_key !== key; });
    } else {
      var r = keyIndex[key];
      if (!r) return;
      added = normItem({ pattern_key: key, symbol: r.symbol, exchange: r.exchange, market: r.market,
        timeframe: r.timeframe, added_at: new Date().toISOString(), status_at_add: r.status,
        score_at_add: r.pattern_score, tv_symbol: r.tv_symbol, name: r.name, via: "site" });
      watch.unshift(added);
    }
    saveWatch();
    delete synced[key];
    refreshStars();
    if (loggedIn()) pushChange(added, key);
  }
  function refreshStars() {
    Array.prototype.forEach.call(document.querySelectorAll(".star"), function (b) {
      var a = starAttrs(isStarred(b.getAttribute("data-key")));
      b.setAttribute("aria-pressed", a.pressed); b.setAttribute("aria-label", a.label); b.title = a.label;
    });
    $("wl-n").textContent = watchInMarket(state.market);
    var open = drawerEl().classList.contains("is-open");
    if (open && drawer.view === "menu") renderDrawer();
    if (open && drawer.view === "watchlist") {
      renderDrawer();
      if (!drawerEl().contains(document.activeElement)) focusFirst();
    }
    if (state.onlyWl && state.data) update(true);
  }

  /* ---------- cloud sync (optional, D35): log in with Discord -> your own watchlist on the Cloudflare Worker (bot/) ---------- */
  var synced = {};     // pattern_key -> true once the server is known to hold it
  var syncState = { busy: false, ok: null, err: "", count: 0 };
  // The Worker. Local preview only: ?worker=http://127.0.0.1:PORT is honoured when the page itself is served from
  // 127.0.0.1 / localhost, so a link can never send a session to another server.
  var WORKER_URL = (function () {
    var def = "https://ihs-watchlist-bot.a753951828282.workers.dev";
    try {
      var w = new URLSearchParams(location.search).get("worker");
      if (w && /^(127\.0\.0\.1|localhost)$/.test(location.hostname) && /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/.test(w)) return w;
    } catch (e) { /* ignore */ }
    return def;
  })();
  var LOGIN_ERR = { cancelled: "已取消 Discord 登入。", unavailable: "Discord 登入暫時無法使用（伺服器尚未設定），星號先存本機。",
    discord: "Discord 登入失敗，請稍後再試。" };
  var loginNotice = "", loginOk = false;
  function strs(a) { return Array.isArray(a) ? a.filter(function (k) { return typeof k === "string"; }).slice(0, 2000) : []; }
  // The session's payload is read only for display (name) and to tell accounts apart; the Worker verifies it.
  function sessionInfo(token) {
    try {
      var b = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
      var bin = atob(b + "===".slice((b.length + 3) % 4)), bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var p = JSON.parse(new TextDecoder().decode(bytes));
      if (!p || typeof p.u !== "string" || typeof p.exp !== "number") return null;
      return { user_id: p.u, username: typeof p.n === "string" ? p.n.slice(0, 40) : "", exp: p.exp };
    } catch (e) { return null; }
  }
  // ihs.session.v1 = { token|null, user_id, username, known:[pattern_key], pendingRemove:[pattern_key] }.
  // token null = logged out by expiry: known / pendingRemove are kept for when the same account logs in again.
  var sess = (function () {
    if (readStore(SYNC_LS) !== null) { try { window.localStorage.removeItem(SYNC_LS); } catch (e) { /* ignore */ } } // D34 key
    var raw = readStore(SESSION_LS), s = null;
    if (raw && typeof raw === "object" && typeof raw.user_id === "string") {
      s = { token: typeof raw.token === "string" ? raw.token : null, user_id: raw.user_id, username: str(raw.username) || "",
        known: strs(raw.known), pendingRemove: strs(raw.pendingRemove) };
    }
    var h = location.hash || "", m = /^#ihs_session=([A-Za-z0-9_.-]{20,2048})$/.exec(h), e = /^#ihs_login_error=([a-z]+)$/.exec(h);
    if (m || e) {
      try { history.replaceState(null, "", location.pathname + location.search); } catch (err) { /* ignore */ }
    }
    if (e) loginNotice = LOGIN_ERR[e[1]] || LOGIN_ERR.discord;
    if (m) {
      var info = sessionInfo(m[1]);
      if (info) {
        var same = s && s.user_id === info.user_id;
        s = { token: m[1], user_id: info.user_id, username: info.username, known: same ? s.known : [], pendingRemove: same ? s.pendingRemove : [] };
        loginNotice = "已用 Discord 登入：" + (info.username || info.user_id) + "。"; loginOk = true;
      }
    }
    if (s && s.token) {
      var cur = sessionInfo(s.token);
      if (!cur || cur.exp * 1000 <= Date.now()) s.token = null;
    }
    return s;
  })();
  var notifyMode = null; // "channel" | "dm" once /api/me answered
  function loggedIn() { return !!(sess && sess.token); }
  function saveSess() { if (sess) writeStore(SESSION_LS, sess); }
  if (sess) saveSess();
  function logout() {
    sess = null; synced = {}; notifyMode = null; syncState = { busy: false, ok: null, err: "", count: 0 };
    if (demoMode) return;
    try { window.localStorage.removeItem(SESSION_LS); } catch (e) { /* ignore */ }
  }
  function loginUrl() {
    return WORKER_URL + "/auth/login?return=" + encodeURIComponent(location.origin + location.pathname + location.search);
  }
  var toastTimer = null;
  function showToast(text, ok) { // ok: neutral confirmation; default: warning (orange)
    var t = $("toast");
    if (!t) return;
    t.textContent = text; t.hidden = false; t.className = "toast" + (ok ? " toast--ok" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 6000);
  }
  function friendly(msg) { var e = new Error(msg); e.friendly = true; return e; }
  function sessionExpired() {
    if (sess) { sess.token = null; saveSess(); }
    synced = {}; notifyMode = null;
    showToast("Discord 登入已過期，請到「設定 → 雲端同步」重新登入。本機星號都還在。");
    renderSyncSection();
  }
  // One request to the Worker's user API. The session only ever goes into the Authorization header of this fetch.
  function meCall(method, path, body) {
    if (!loggedIn()) return Promise.reject(friendly("尚未登入"));
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, 10000);
    var init = { method: method, headers: { Authorization: "Bearer " + sess.token }, cache: "no-store" };
    if (ctl) init.signal = ctl.signal;
    if (body) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    return fetch(WORKER_URL + path, init).then(function (res) {
      clearTimeout(timer);
      if (res.status === 401) { sessionExpired(); throw friendly("Discord 登入已過期，請重新登入"); }
      if (!res.ok) throw friendly("伺服器回應 HTTP " + res.status);
      return res.json().catch(function () { throw friendly("伺服器回傳的不是 JSON"); });
    }, function () {
      clearTimeout(timer);
      throw friendly("連不上同步伺服器（離線或被擋）");
    });
  }
  function itemsCall(method, body) {
    return meCall(method, "/api/me/watchlist", body).then(function (j) {
      if (!j || !Array.isArray(j.items)) throw friendly("伺服器回傳格式不對");
      return j.items;
    });
  }
  function toServer(w) {
    return { pattern_key: w.pattern_key, market: rowMarket(w), symbol: w.symbol, name: w.name, timeframe: w.timeframe,
      added_at: w.added_at, added_via: w.via || "site", last_status: w.status_at_add, exchange: w.exchange,
      tv_symbol: w.tv_symbol, status_at_add: w.status_at_add, score_at_add: w.score_at_add };
  }
  function fromServer(s) {
    if (!s || typeof s.pattern_key !== "string") return null;
    var p = s.pattern_key.split("|");
    var st = STATUS_LABEL[s.status_at_add] ? s.status_at_add : STATUS_LABEL[s.last_status] ? s.last_status : null;
    return normItem({ pattern_key: s.pattern_key, symbol: s.symbol, exchange: s.exchange || p[0], market: p[1] || null,
      timeframe: s.timeframe || p[3], name: s.name, added_at: s.added_at, status_at_add: st, score_at_add: s.score_at_add,
      tv_symbol: s.tv_symbol, via: s.added_via });
  }
  function adoptServerSet(items) { // the server list is now `items`: record it as known + synced
    synced = {}; sess.known = [];
    items.forEach(function (s) { if (s && typeof s.pattern_key === "string") { synced[s.pattern_key] = true; sess.known.push(s.pattern_key); } });
    syncState.count = sess.known.length;
    saveSess();
  }
  function loadMe() { // name + notification mode for the settings screen (never blocks the star sync)
    return meCall("GET", "/api/me").then(function (j) {
      if (!j || !sess) return;
      if (j.notify === "channel" || j.notify === "dm") notifyMode = j.notify;
      if (typeof j.username === "string" && j.username) { sess.username = j.username.slice(0, 40); saveSess(); }
      if (drawerEl().classList.contains("is-open") && drawer.view === "settings") renderSyncSection();
    }, function () { /* the star sync reports errors */ });
  }
  // Full merge, server wins: on the server but not here -> add here; here but not on the server -> upload,
  // unless this browser had synced it before (then it was removed elsewhere, e.g. Discord's cancel button).
  function syncAll() {
    if (!loggedIn() || syncState.busy) return Promise.resolve();
    syncState.busy = true;
    loadMe();
    var pre = sess.pendingRemove.length ? itemsCall("POST", { remove: sess.pendingRemove }) : Promise.resolve(null);
    return pre.then(function () {
      if (sess.pendingRemove.length) { sess.pendingRemove = []; saveSess(); }
      return itemsCall("GET");
    }).then(function (serverItems) {
      var srv = {}, known = {}, have = {}, changed = false, upload = [];
      serverItems.forEach(function (s) { if (s && s.pattern_key) srv[s.pattern_key] = true; });
      sess.known.forEach(function (k) { known[k] = true; });
      watch.forEach(function (w) { have[w.pattern_key] = true; });
      serverItems.forEach(function (s) {
        if (!s || have[s.pattern_key]) return;
        var it = fromServer(s);
        if (it && watch.length < 2000) { watch.push(it); have[it.pattern_key] = true; changed = true; }
      });
      watch = watch.filter(function (w) {
        if (srv[w.pattern_key]) return true;
        if (known[w.pattern_key]) { changed = true; return false; }
        upload.push(w); return true;
      });
      if (changed) saveWatch();
      if (!upload.length) return serverItems;
      return itemsCall("POST", { add: upload.map(toServer) });
    }).then(function (finalItems) {
      adoptServerSet(finalItems);
      syncState.ok = true; syncState.err = "";
    }).catch(function (err) {
      syncState.ok = false; syncState.err = err && err.message ? err.message : "同步失敗";
      if (loggedIn()) showToast("雲端同步失敗：" + syncState.err + "。已保留本機名單，下次載入會重試。");
    }).then(function () {
      syncState.busy = false;
      refreshStars();
      if (drawerEl().classList.contains("is-open") && drawer.view === "settings") renderSyncSection();
    });
  }
  function pushChange(added, key) { // one star toggled: tell the server now, remember to retry if it fails
    var p;
    if (added) {
      sess.pendingRemove = sess.pendingRemove.filter(function (k) { return k !== key; });
      p = itemsCall("POST", { add: [toServer(added)] });
    } else {
      p = itemsCall("POST", { remove: [key] });
    }
    p.then(function (items) {
      sess.pendingRemove = sess.pendingRemove.filter(function (k) { return k !== key; });
      adoptServerSet(items);
      syncState.ok = true; syncState.err = "";
    }, function (err) {
      if (sess) {
        if (!added && sess.pendingRemove.indexOf(key) < 0) sess.pendingRemove.push(key);
        saveSess();
      }
      syncState.ok = false; syncState.err = err && err.message ? err.message : "同步失敗";
      if (loggedIn()) showToast("沒有同步到雲端：" + syncState.err + "。已保留在本機，下次載入會重試。");
    }).then(refreshStars);
  }
  var CLOUD_SVG = "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M7 18a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 8.6a4.7 4.7 0 0 1 .3 9.4z\"/></svg>";
  function cloudMark(key) {
    if (!loggedIn()) return "";
    var on = !!synced[key];
    return "<span class=\"cloud" + (on ? " cloud--on" : "") + "\" title=\"" + (on ? "已同步到雲端" : "尚未同步到雲端") + "\">" + CLOUD_SVG +
      "<span class=\"vh\">" + (on ? "已同步到雲端" : "尚未同步到雲端") + "</span></span>";
  }
  function syncLine() { // one status line above the watchlist; nothing at all when never logged in
    if (!sess) return "";
    var who = sess.username || "Discord";
    var t = !loggedIn() ? "Discord 登入已過期，星號只存在這個瀏覽器；到「設定 → 雲端同步」重新登入。"
      : syncState.busy ? "雲端同步中…"
      : syncState.ok === true ? "已用 Discord 登入（" + who + "），伺服器上你的清單有 " + syncState.count + " 筆。"
      : syncState.ok === false ? "雲端同步失敗：" + syncState.err + "（本機名單不受影響）"
      : "雲端同步：尚未連線。";
    return "<p class=\"syncline" + (syncState.ok === false || !loggedIn() ? " syncline--bad" : "") + "\" role=\"status\">" + esc(t) + "</p>";
  }

  /* ---------- drawer ---------- */
  var drawer = { view: "menu", timer: null };
  function drawerEl() { return $("drawer"); }
  var VIEW_TITLE = { menu: "選單", watchlist: "觀察名單", settings: "設定", about: "關於" };
  var FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex=\"-1\"])";
  function focusables() {
    return Array.prototype.filter.call(drawerEl().querySelectorAll(FOCUSABLE), function (el) { return !el.hidden && !el.closest("[hidden]"); });
  }
  function focusFirst() {
    var el = drawerEl().querySelector(".dback") || drawerEl().querySelector(".dnav__btn") || $("drawer-close");
    if (el) el.focus();
  }
  function setInert(on) {
    ["topbar", "board", "record", "colophon"].forEach(function (c) {
      var el = document.querySelector("." + c);
      if (!el) return;
      try { el.inert = on; } catch (e) { /* no inert support: the focus trap still holds */ }
    });
  }
  function openDrawer(view) {
    var d = drawerEl();
    clearTimeout(drawer.timer);
    drawer.view = view || "menu";
    drawer.groups = null; // D46: groups open on the current market each time the drawer opens
    renderDrawer();
    $("scrim").hidden = false; d.hidden = false;
    void d.offsetWidth; // let the closed state paint so the slide-in transitions
    d.classList.add("is-open"); $("scrim").classList.add("is-open");
    document.body.classList.add("nav-open");
    $("menu-btn").setAttribute("aria-expanded", "true");
    setInert(true);
    focusFirst();
    if (drawer.view === "watchlist") loadAllMarkets();
    if (drawer.view === "menu") MARKET_ORDER.forEach(function (tm) { // counts next to 頭肩底／收斂三角 of both markets
      var again = function () { if (drawerEl().classList.contains("is-open") && drawer.view === "menu") renderDrawer(); };
      if (!dataCache[tm] && !loadErr[tm]) loadMarket(tm).then(null, function () { /* shown as 尚無資料 */ }).then(again);
      if (!triCache[tm] && !triErr[tm]) loadTri(tm).then(null, function () { /* shown as 尚無資料 */ }).then(again);
    });
  }
  function closeDrawer(focusTarget) {
    var d = drawerEl();
    if (!d.classList.contains("is-open")) return;
    d.classList.remove("is-open"); $("scrim").classList.remove("is-open");
    document.body.classList.remove("nav-open");
    $("menu-btn").setAttribute("aria-expanded", "false");
    setInert(false);
    var done = function () { d.hidden = true; $("scrim").hidden = true; };
    if (reduceMotion) done(); else drawer.timer = setTimeout(done, 220);
    (focusTarget || $("menu-btn")).focus();
  }
  function setView(v) { drawer.view = v; renderDrawer(); focusFirst(); if (v === "watchlist") loadAllMarkets(); }

  function statusTag(st) { return "<span class=\"status status--" + esc(st) + "\">" + esc(STATUS_LABEL[st] || st) + "</span>"; }

  // D46: 台股 / 加密貨幣 are expandable groups holding 頭肩底 and 收斂三角; then 實際戰績, 觀察名單, 設定 (關於 moved into 設定).
  var MENU_GROUP_ORDER = ["tw", "crypto"];
  function groupOpen(m) { return drawer.groups ? !!drawer.groups[m] : m === state.market; }
  function menuHtml() {
    return "<ul class=\"dnav\">" + MENU_GROUP_ORDER.map(function (m) {
      var open = groupOpen(m), here = m === state.market && !isRec();
      return "<li><button type=\"button\" class=\"dnav__btn dnav__btn--mkt dnav__btn--grp\" data-act=\"grp\" data-market=\"" + m + "\"" +
        " aria-expanded=\"" + open + "\" aria-controls=\"dsub-" + m + "\"><span>" + esc(MARKETS[m].label) + "</span>" +
        "<span class=\"dnav__end\">" + (here ? "<span class=\"dnav__sub\">目前</span>" : "") +
        "<svg class=\"dnav__chev\" viewBox=\"0 0 12 12\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3 4.5l3 3 3-3\"/></svg></span></button>" +
        "<ul class=\"dsub\" id=\"dsub-" + m + "\"" + (open ? "" : " hidden") + ">" + ihsMenuItem(m) + triMenuItem(m) + "</ul></li>";
    }).join("") +
      recMenuItem() +
      "<li><button type=\"button\" class=\"dnav__btn\" data-act=\"view\" data-view=\"watchlist\">觀察名單（" + watch.length + "）</button></li>" +
      "<li><button type=\"button\" class=\"dnav__btn\" data-act=\"view\" data-view=\"settings\">設定</button></li></ul>";
  }
  function ihsMenuItem(m) {
    var cur = m === state.market && !isTri() && !isRec(), d = dataCache[m];
    var n = d ? d.results.length + " 個" : (loadErr[m] ? "尚無資料" : "");
    return "<li><button type=\"button\" class=\"dnav__btn dnav__btn--mkt dnav__btn--sub\" data-act=\"market\" data-market=\"" + m + "\"" +
      (cur ? " aria-current=\"page\"" : "") + "><span>頭肩底</span><span class=\"dnav__sub\">" + esc(n) + "</span></button></li>";
  }
  // D38: the triangle list of market m
  function triMenuItem(m) {
    var d = triCache[m], cur = isTri() && m === state.market;
    var n = d ? d.results.length + " 個" : (triErr[m] ? "尚無資料" : "");
    return "<li><button type=\"button\" class=\"dnav__btn dnav__btn--mkt dnav__btn--sub\" data-act=\"tri\" data-market=\"" + m + "\"" +
      (cur ? " aria-current=\"page\"" : "") + "><span>收斂三角</span><span class=\"dnav__sub\">" + esc(n) + "</span></button></li>";
  }
  // D41: the track record of the current market
  function recMenuItem() {
    var d = recCache[state.market];
    return "<li><button type=\"button\" class=\"dnav__btn dnav__btn--mkt dnav__btn--tri\" data-act=\"rec\"" + (isRec() ? " aria-current=\"page\"" : "") +
      "><span>實際戰績</span><span class=\"dnav__sub\">" + esc(MARKETS[state.market].label + (d ? "・" + d.counts.items + " 筆" : "")) + "</span></button></li>";
  }
  function watchHtml() {
    var h = "<button type=\"button\" class=\"dback\" data-act=\"back\">‹ 選單</button>";
    if (!storageOk) h += "<p class=\"dwarn\">這個瀏覽器停用了網站儲存，觀察名單只在這次開啟期間有效，重新整理後會消失。</p>";
    h += syncLine();
    if (!watch.length) {
      return h + "<p class=\"dempty\">還沒有加入任何型態。在清單每一列或詳細頁標題旁按星號（☆）就會出現在這裡。</p>";
    }
    var items = watch.slice().sort(function (a, b) { return Date.parse(b.added_at) - Date.parse(a.added_at); });
    h += "<ul class=\"wls\">" + items.map(function (w) {
      var cur = keyIndex[w.pattern_key], wm = rowMarket(w);
      var head = "<span class=\"wl__top\"><span class=\"mtag mtag--" + wm + "\">" + esc(MARKETS[wm].label) + "</span>" +
        "<span class=\"sym\">" + symHtml(w) + "</span>" +
        (w.timeframe ? "<span class=\"tf\">" + esc(tfName(w.timeframe)) + "</span>" : "") +
        (w.exchange ? "<span class=\"mkt\">" + esc(marketText(w)) + "</span>" : "") +
        (w.via === "discord" ? "<span class=\"vtag\">來自 Discord</span>" : "") + cloudMark(w.pattern_key) + "</span>";
      var sub, note = "加入於 " + fmtDay(w.added_at, true) +
        (w.status_at_add ? "，當時 " + STATUS_LABEL[w.status_at_add] : "") +
        (w.score_at_add !== null ? " " + w.score_at_add.toFixed(1) + " 分" : "");
      if (cur) {
        if (w.status_at_add && w.status_at_add !== cur.status) {
          sub = "<span class=\"wl__chg\">" + esc(STATUS_LABEL[w.status_at_add]) + " → </span>" + statusTag(cur.status);
        } else sub = statusTag(cur.status);
        sub += "<span class=\"dist\">分數 " + cur.pattern_score.toFixed(1) + "</span>";
      } else if (!dataCache[wm]) {
        sub = "<span class=\"dist\">尚未讀到" + esc(MARKETS[wm].label) + "的最新掃描資料，無法比對</span>";
      } else {
        sub = "<span class=\"gone\">已不在最新結果（可能作廢或過期）</span>";
      }
      var inner = head + "<span class=\"wl__sub\">" + sub + "</span><span class=\"wl__note\">" + esc(note) + "</span>";
      var main = cur
        ? "<button type=\"button\" class=\"wl__main wl__open\" data-act=\"open\" data-key=\"" + esc(w.pattern_key) + "\">" + inner + "</button>"
        : "<div class=\"wl__main\">" + inner + "</div>";
      return "<li class=\"wl\">" + main + starBtn(w, "") + "</li>";
    }).join("") + "</ul>";
    return h;
  }
  function opt(v, label, cur) { return "<option value=\"" + esc(v) + "\"" + (v === cur ? " selected" : "") + ">" + esc(label) + "</option>"; }
  function settingsHtml() {
    var tfs = (state.data ? state.data.settings.timeframes : ["4h", "1d", "1w"]);
    return "<button type=\"button\" class=\"dback\" data-act=\"back\">‹ 選單</button>" +
      (storageOk ? "" : "<p class=\"dwarn\">這個瀏覽器停用了網站儲存，設定只在這次開啟期間有效。</p>") +
      "<h3 class=\"dh\">外觀</h3>" +
      "<div class=\"fld\"><label for=\"s-theme\">佈景主題</label><select id=\"s-theme\" data-set=\"theme\">" +
      ["light", "dark", "auto"].map(function (t) { return opt(t, THEME_LABEL[t], settings.theme); }).join("") + "</select></div>" +
      "<h3 class=\"dh\">預設篩選與排序</h3><p class=\"dnote\">改了立刻套用，也會記住，下次打開時還是這樣。加密貨幣與台股共用這組預設；網站也會記住你上次看的市場。</p>" +
      "<div class=\"fld\"><label for=\"s-tf\">週期</label><select id=\"s-tf\" data-set=\"tf\">" + opt("all", "全部", settings.tf) +
      tfs.map(function (t) { return opt(t, tfName(t), settings.tf); }).join("") + "</select></div>" +
      "<div class=\"fld\"><label for=\"s-st\">狀態</label><select id=\"s-st\" data-set=\"status\">" + opt("all", "全部", settings.status) +
      STATUS_ORDER.map(function (t) { return opt(t, STATUS_LABEL[t], settings.status); }).join("") + "</select></div>" +
      "<div class=\"fld\"><label for=\"s-nk\">頸線斜率</label><select id=\"s-nk\" data-set=\"neck\">" + opt("all", "全部", settings.neck) +
      NECK_ORDER.map(function (t) { return opt(t, NECK_LABEL[t], settings.neck); }).join("") + "</select></div>" +
      "<div class=\"fld\"><label for=\"s-so\">排序</label><select id=\"s-so\" data-set=\"sort\">" +
      Array.prototype.map.call($("sort").options, function (o) { return opt(o.value, o.textContent, settings.sort); }).join("") + "</select></div>" +
      "<div class=\"fld fld--col\"><label for=\"s-min\">最低分數門檻：<output id=\"s-min-out\">" + settings.minScore + "</output></label>" +
      "<input id=\"s-min\" type=\"range\" min=\"0\" max=\"100\" step=\"5\" value=\"" + settings.minScore + "\" data-set=\"minScore\"></div>" +
      "<h3 class=\"dh\">詳細頁顯示</h3>" +
      "<label class=\"chk\"><input type=\"checkbox\" data-set=\"showLevels\"" + (settings.showLevels ? " checked" : "") + "><span>在圖上顯示 TP／失效價線</span></label>" +
      "<label class=\"chk\"><input type=\"checkbox\" data-set=\"showChecks\"" + (settings.showChecks ? " checked" : "") + "><span>顯示形狀驗證區塊</span></label>" +
      syncSettingsHtml() +
      "<h3 class=\"dh\">觀察名單備份</h3><p class=\"dnote\">觀察名單預設只存在這個瀏覽器（未用 Discord 登入時）。換裝置或清除瀏覽資料前，請先匯出。</p>" +
      "<div class=\"btnrow\"><button type=\"button\" class=\"dbtn\" data-act=\"export-file\">下載 JSON</button>" +
      "<button type=\"button\" class=\"dbtn\" data-act=\"export-copy\">複製到剪貼簿</button></div>" +
      "<div class=\"fld fld--col\"><label for=\"imp-text\">匯入（貼上匯出的 JSON）</label>" +
      "<textarea id=\"imp-text\" rows=\"4\" spellcheck=\"false\" placeholder=\"{ &quot;items&quot;: [ … ] }\"></textarea></div>" +
      "<div class=\"btnrow\"><button type=\"button\" class=\"dbtn\" data-act=\"import-text\">匯入貼上的內容</button>" +
      "<label class=\"dbtn dbtn--file\" for=\"imp-file\">選擇檔案匯入</label>" +
      "<input id=\"imp-file\" class=\"vh\" type=\"file\" accept=\"application/json,.json\"></div>" +
      "<div class=\"btnrow\"><button type=\"button\" class=\"dbtn dbtn--danger\" data-act=\"clear-ask\">清空觀察名單</button></div>" +
      "<div class=\"ask\" id=\"clear-ask\" hidden><p>確定要清空全部 " + watch.length + " 筆觀察名單？這個動作無法復原。</p>" +
      "<div class=\"btnrow\"><button type=\"button\" class=\"dbtn dbtn--danger\" data-act=\"clear-yes\">確定清空</button>" +
      "<button type=\"button\" class=\"dbtn\" data-act=\"clear-no\">取消</button></div></div>" +
      "<p class=\"dmsg\" id=\"d-msg\" role=\"status\" aria-live=\"polite\"></p>" +
      "<ul class=\"dnav dnav--foot\"><li><button type=\"button\" class=\"dnav__btn\" data-act=\"view\" data-view=\"about\">關於這個網站</button></li></ul>";
  }
  var DISCORD_SVG = "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M19.3 5.3A16.6 16.6 0 0 0 15.2 4l-.5 1a15.4 15.4 0 0 0-5.4 0l-.5-1a16.6 16.6 0 0 0-4.1 1.3C2.1 9.2 1.4 13 1.7 16.7a16.7 16.7 0 0 0 5 2.5l1.1-1.7c-.6-.2-1.2-.5-1.7-.8l.4-.3a11.9 11.9 0 0 0 10.2 0l.4.3c-.5.3-1.1.6-1.7.8l1.1 1.7a16.6 16.6 0 0 0 5-2.5c.4-4.3-.7-8-2.2-11.4zM8.7 14.5c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2zm6.6 0c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2z\"/></svg>";
  function syncSettingsHtml() { return "<div id=\"sync-sec\" class=\"syncsec\">" + syncSectionInner() + "</div>"; }
  function syncSectionInner() {
    var h = "<h3 class=\"dh\">雲端同步（選用）</h3>";
    if (!loggedIn()) {
      h += "<p class=\"dnote\">用 Discord 登入後，星號會同步到<strong>你自己的</strong>關注清單（每個人各一份，和 Discord 表格選單加入的同一份），" +
        "型態狀態改變時機器人會通知你。不登入就只存在這個瀏覽器。</p>" +
        (sess ? "<p class=\"dwarn\">Discord 登入已過期，請重新登入。本機星號都還在。</p>" : "") +
        "<div class=\"btnrow\"><a class=\"dbtn dbtn--discord\" href=\"" + esc(loginUrl()) + "\">" + DISCORD_SVG + "<span>用 Discord 登入</span></a></div>" +
        "<p class=\"dnote\">只會取得你的 Discord 使用者名稱與 ID（權限 identify），不會讀取訊息或伺服器。</p>";
      return h;
    }
    var mode = notifyMode;
    var radio = function (v, label, sub) {
      return "<label class=\"chk chk--radio\"><input type=\"radio\" name=\"s-notify\" value=\"" + v + "\"" + (mode === v ? " checked" : "") +
        (mode ? "" : " disabled") + "><span>" + esc(label) + "<span class=\"chk__sub\">" + esc(sub) + "</span></span></label>";
    };
    h += "<p class=\"acct\">" + DISCORD_SVG + "<span>已用 Discord 登入：<strong>" + esc(sess.username || sess.user_id) + "</strong></span></p>" +
      "<fieldset class=\"nfs\"><legend class=\"nfs__lg\">關注清單的通知方式</legend>" +
      radio("channel", "在 #關注 頻道 @我", "和其他人的通知在同一個頻道") +
      radio("dm", "機器人私訊", "只有你看得到；收不到時會改在 #關注 頻道 @你") + "</fieldset>" +
      (mode === "dm" ? "<p class=\"dnote\">若收不到私訊：到這個 Discord 伺服器的「隱私設定」，打開「允許來自伺服器成員的私人訊息」。</p>" : "") +
      (mode ? "" : "<p class=\"dnote\">" + (syncState.ok === false ? "讀不到通知方式：" + esc(syncState.err) : "讀取通知方式中…") + "</p>") +
      "<div class=\"btnrow\"><button type=\"button\" class=\"dbtn\" data-act=\"sync-now\">立即同步</button>" +
      "<button type=\"button\" class=\"dbtn\" data-act=\"sync-logout\">登出</button></div>" +
      "<p class=\"dnote\" id=\"sy-state\" role=\"status\" aria-live=\"polite\">" + esc(syncStatusText()) + "</p>";
    return h;
  }
  function syncStatusText() {
    return syncState.busy ? "同步中…"
      : syncState.ok === true ? "已同步：伺服器上你的清單有 " + syncState.count + " 筆。"
      : syncState.ok === false ? "同步失敗：" + syncState.err + "（本機名單不受影響）" : "";
  }
  function renderSyncSection() {
    var el = $("sync-sec");
    if (!el) return;
    var had = el.contains(document.activeElement) && document.activeElement.name === "s-notify";
    el.innerHTML = syncSectionInner();
    if (had) { var r = el.querySelector("input[name=\"s-notify\"]:checked"); if (r) r.focus(); }
  }
  function syncMsg(text, bad) {
    var m = $("sy-state");
    if (!m) return;
    m.textContent = text; m.className = "dnote" + (bad ? " dmsg--bad" : "");
  }
  function onSyncAct(act) {
    if (act === "sync-logout") {
      logout(); renderSyncSection(); refreshStars();
      var b = document.querySelector(".dbtn--discord"); if (b) b.focus();
      showToast("已登出 Discord。觀察名單繼續保留在這個瀏覽器。", true);
      return;
    }
    if (act === "sync-now") {
      syncMsg("同步中…", false);
      syncAll();
    }
  }
  function onNotifyChange(el) {
    var mode = el.value, prev = notifyMode;
    if (mode !== "channel" && mode !== "dm" || mode === prev) return;
    notifyMode = mode; renderSyncSection(); syncMsg("儲存中…", false);
    meCall("POST", "/api/me/notify", { mode: mode }).then(function (j) {
      if (!j || (j.notify !== "channel" && j.notify !== "dm")) throw friendly("伺服器回傳格式不對");
      notifyMode = j.notify; renderSyncSection();
      syncMsg(j.notify === "dm" ? "已改成：機器人私訊。" : "已改成：在 #關注 頻道 @你。", false);
    }).catch(function (err) {
      notifyMode = loggedIn() ? prev : null; renderSyncSection();
      syncMsg("沒有改成功：" + (err && err.message ? err.message : "同步失敗"), true);
    });
  }
  function aboutHtml() {
    return "<button type=\"button\" class=\"dback\" data-act=\"view\" data-view=\"settings\">‹ 設定</button>" +
      "<div class=\"about\"><p>加密貨幣：幣安、OKX、Gate 的公開 K 線行情，每 4 小時自動掃描一次。</p>" +
      "<p>台股：上市＋上櫃普通股（清單與名稱來自證交所、櫃買中心 OpenAPI），K 線來自 Yahoo Finance（未還原權息，和 TradingView 預設相同），" +
      "每個交易日收盤後（台北 14:10 左右）掃描一次；4 小時線一天兩根：09:00–13:00、13:00–13:30。</p>" +
      "<p>兩邊都只用已收盤的 K 線，頁首會寫出該市場的資料更新時間。</p>" +
      "<p><strong>這是型態偵測工具，不是投資建議。</strong>型態成立不代表價格會上漲，請自行判斷風險。</p>" +
      "<p>觀察名單與設定預設只存在這個瀏覽器（localStorage）；在「設定 → 雲端同步」用 Discord 登入後，觀察名單會和你在 Discord 機器人上的關注清單（每人各一份）同步，狀態改變時機器人會在 #關注 頻道 @你或私訊你。換裝置也可以用「設定」裡的匯出／匯入。</p>" +
      "<p>「右肩已確認」是掃描狀態（右肩已成形、尚未突破頸線），和「觀察名單」是兩回事。</p></div>";
  }
  function renderDrawer() {
    $("drawer-title").textContent = VIEW_TITLE[drawer.view] || "選單";
    $("drawer-body").innerHTML = drawer.view === "watchlist" ? watchHtml() : drawer.view === "settings" ? settingsHtml()
      : drawer.view === "about" ? aboutHtml() : menuHtml();
  }
  function showMsg(text, bad) {
    var m = $("d-msg");
    if (!m) return;
    m.textContent = text; m.className = "dmsg" + (bad ? " dmsg--bad" : "");
  }

  /* ---------- export / import ---------- */
  function exportText() {
    return JSON.stringify({ app: "ihs-scanner-watchlist", version: 1, exported_at: new Date().toISOString(), items: watch }, null, 2);
  }
  function doImport(text) {
    var data;
    try { data = JSON.parse(text); } catch (e) { showMsg("格式不對：不是有效的 JSON。", true); return; }
    var arr = data && Array.isArray(data.items) ? data.items : Array.isArray(data) ? data : null;
    if (!arr) { showMsg("格式不對：找不到 items 清單，請貼上從這裡匯出的內容。", true); return; }
    var res = mergeItems(watch, arr);
    if (!res.added && !res.dup && res.bad) { showMsg("格式不對：" + res.bad + " 筆都缺少 pattern_key 或 symbol，沒有匯入。", true); return; }
    watch = res.items;
    var ok = saveWatch();
    refreshStars();
    showMsg("匯入完成：新增 " + res.added + " 筆，重複略過 " + res.dup + " 筆" + (res.bad ? "，格式不對略過 " + res.bad + " 筆" : "") +
      "。" + (ok ? "" : "（儲存被停用，只在這次開啟期間有效）"), false);
  }
  function onDrawerClick(e) {
    var t = e.target.closest("[data-act]");
    if (!t) return;
    var act = t.getAttribute("data-act");
    if (act === "sync-now" || act === "sync-logout") { onSyncAct(act); return; }
    if (act === "view") setView(t.getAttribute("data-view"));
    else if (act === "back") setView("menu");
    else if (act === "market") {
      state.onlyWl = false; $("only-wl").setAttribute("aria-pressed", "false");
      closeDrawer();
      if (isTri() || isRec()) showIhs(t.getAttribute("data-market"));
      else switchMarket(t.getAttribute("data-market"));
    } else if (act === "rec") {
      closeDrawer();
      showRecord(state.market);
    } else if (act === "grp") {
      var gm = t.getAttribute("data-market");
      if (!drawer.groups) drawer.groups = { tw: groupOpen("tw"), crypto: groupOpen("crypto") };
      drawer.groups[gm] = !drawer.groups[gm];
      renderDrawer();
      var gb = drawerEl().querySelector("[data-act=\"grp\"][data-market=\"" + gm + "\"]"); if (gb) gb.focus();
    } else if (act === "tri") {
      var tm = t.getAttribute("data-market") || state.market;
      state.onlyWl = false; $("only-wl").setAttribute("aria-pressed", "false");
      closeDrawer();
      showTriangles(tm);
    } else if (act === "open") {
      var r = keyIndex[t.getAttribute("data-key")];
      if (!r) return;
      state.q = ""; $("q").value = ""; state.tf = "all"; state.status = "all"; state.neck = "all"; state.onlyWl = false; state.shown = PAGE;
      $("only-wl").setAttribute("aria-pressed", "false");
      closeDrawer($("detail"));
      if (isTri() || isRec()) { state.view = "ihs"; state.selected = null; renderViewChrome(); setUrlMarket(rowMarket(r), false); }
      switchMarket(rowMarket(r), r.pattern_id);
    } else if (act === "export-file") {
      try {
        var url = URL.createObjectURL(new Blob([exportText()], { type: "application/json" }));
        var a = document.createElement("a");
        a.href = url; a.download = "ihs-watchlist.json";
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        showMsg("已下載 ihs-watchlist.json（" + watch.length + " 筆）。", false);
      } catch (err) { showMsg("無法下載檔案，請改用「複製到剪貼簿」。", true); }
    } else if (act === "export-copy") {
      var txt = exportText();
      var fallback = function () {
        var ta = $("imp-text");
        if (ta) { ta.value = txt; ta.focus(); ta.select(); }
        showMsg("無法自動複製，內容已放進下方文字框並選取，請按 Ctrl+C。", true);
      };
      try {
        navigator.clipboard.writeText(txt).then(function () { showMsg("已複製 " + watch.length + " 筆到剪貼簿。", false); }, fallback);
      } catch (err) { fallback(); }
    } else if (act === "import-text") {
      doImport($("imp-text").value);
    } else if (act === "clear-ask") {
      $("clear-ask").hidden = false; t.hidden = true;
      var y = document.querySelector("[data-act=\"clear-no\"]"); if (y) y.focus();
    } else if (act === "clear-no") {
      $("clear-ask").hidden = true;
      var b = document.querySelector("[data-act=\"clear-ask\"]"); b.hidden = false; b.focus();
    } else if (act === "clear-yes") {
      watch = []; var ok2 = saveWatch(); refreshStars();
      $("clear-ask").hidden = true;
      var b2 = document.querySelector("[data-act=\"clear-ask\"]"); b2.hidden = false;
      showMsg("已清空觀察名單。" + (ok2 ? "" : "（儲存被停用）"), false);
    }
  }
  function onSettingChange(el) {
    var k = el.getAttribute("data-set");
    if (!k) return;
    var v = el.type === "checkbox" ? el.checked : el.type === "range" ? Number(el.value) : el.value;
    settings[k] = v;
    saveSettings();
    if (k === "minScore") $("s-min-out").textContent = v;
    if (k === "theme") { applyTheme(); return; }
    if (isTri()) { // the triangle view only uses showLevels; IHS defaults apply when going back
      var tr = k === "showLevels" && state.tdata && state.tdata.results.find(function (x) { return x.pattern_id === state.tselected; });
      if (tr) renderTriDetail(tr);
      if (k !== "showLevels" && k !== "showChecks") state[k] = v;
      return;
    }
    if (!state.data) return;
    if (k === "showLevels" || k === "showChecks") {
      var r = state.data.results.find(function (x) { return x.pattern_id === state.selected; });
      if (r) renderDetail(r);
      return;
    }
    state[k] = v;
    if (k === "sort") $("sort").value = v;
    state.shown = PAGE;
    update();
  }

  function bindExtras() {
    $("menu-btn").addEventListener("click", function () {
      if (drawerEl().classList.contains("is-open")) closeDrawer(); else openDrawer("menu");
    });
    $("scrim").addEventListener("click", function () { closeDrawer(); });
    $("drawer-close").addEventListener("click", function () { closeDrawer(); });
    $("theme-btn").addEventListener("click", function () {
      settings.theme = resolvedTheme() === "dark" ? "light" : "dark";
      saveSettings();
      applyTheme();
    });
    $("drawer-body").addEventListener("click", onDrawerClick);
    $("record").addEventListener("click", onRecClick);
    $("record").addEventListener("change", function (e) {
      if (e.target.id === "rec-sort") { rec.sort = e.target.value; renderRecList(); }
    });
    $("drawer-body").addEventListener("change", function (e) {
      if (e.target.id === "imp-file") {
        var f = e.target.files && e.target.files[0];
        if (!f) return;
        var fr = new FileReader();
        fr.onload = function () { doImport(String(fr.result)); };
        fr.onerror = function () { showMsg("讀不到這個檔案。", true); };
        fr.readAsText(f);
        return;
      }
      if (e.target.name === "s-notify") { onNotifyChange(e.target); return; }
      onSettingChange(e.target);
    });
    $("drawer-body").addEventListener("input", function (e) { if (e.target.type === "range") onSettingChange(e.target); });
    drawerEl().addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); closeDrawer(); return; }
      if (e.key !== "Tab") return;
      var f = focusables();
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (!drawerEl().contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    document.addEventListener("click", function (e) {
      var b = e.target.closest(".star");
      if (b) toggleStar(b.getAttribute("data-key"));
    });
    $("only-wl").addEventListener("click", function () {
      state.onlyWl = !state.onlyWl;
      this.setAttribute("aria-pressed", String(state.onlyWl));
      state.shown = PAGE;
      if (state.data) update();
    });
    $("wl-n").textContent = watchInMarket(state.market);
    Array.prototype.forEach.call(document.querySelectorAll(".mtab"), function (b) {
      b.addEventListener("click", function () {
        var m = b.getAttribute("data-market");
        if (m !== state.market) { state.onlyWl = false; $("only-wl").setAttribute("aria-pressed", "false"); switchMarket(m); }
      });
    });
  }

  // Test-only URL parameters (never set in normal use): ?demoStars=N stars the first N rows in memory
  // (nothing is saved) plus one changed-status and one vanished entry; ?drawer=menu|watchlist|settings|about
  // opens the drawer after load.
  function applyTestParams(qs) {
    var n = parseInt(qs.get("demoStars"), 10);
    if (n > 0 && state.data) {
      demoMode = true;
      var seen = {};
      watch = [];
      state.data.results.forEach(function (r) {
        if (watch.length >= n || !r.pattern_key || seen[r.pattern_key]) return;
        seen[r.pattern_key] = true;
        var other = r.status === "forming" ? "watchlist" : "forming";
        watch.push(normItem({ pattern_key: r.pattern_key, symbol: r.symbol, exchange: r.exchange, market: r.market,
          timeframe: r.timeframe, added_at: new Date(Date.now() - watch.length * 86400000).toISOString(),
          status_at_add: watch.length === 0 ? other : r.status, score_at_add: r.pattern_score - (watch.length === 0 ? 3.2 : 0),
          tv_symbol: r.tv_symbol, name: r.name }));
      });
      watch.push(normItem({ pattern_key: "binance|spot|GONEUSDT|1d|2026-01-01T00:00:00Z|2026-02-01T00:00:00Z", symbol: "GONEUSDT",
        exchange: "binance", market: "spot", timeframe: "1d", added_at: new Date(Date.now() - 9 * 86400000).toISOString(),
        status_at_add: "watchlist", score_at_add: 61.5 }));
      refreshStars();
    }
    var dv = qs.get("drawer");
    if (VIEW_TITLE[dv]) openDrawer(dv);
    if (qs.get("sprite")) { spriteOpen(true); if (qs.get("sprite") !== "open") spriteAsk(qs.get("sprite")); } // D48: ?sprite=open|<question> for screenshots
  }

  function showControls(on) {
    document.querySelector(".panel__head .filters").hidden = !on;
    document.querySelector(".panel__head .search").hidden = !on;
    $("only-wl").hidden = !on || isTri();
  }
  function fail(m, err) {
    var missing = err && /HTTP 404/.test(String(err.message || err));
    $("meta").innerHTML = "<b class=\"meta__mkt\">" + esc(MARKETS[m].label) + "</b>" +
      (missing && m === "tw" ? "台股資料還沒產生。" : "讀不到掃描資料。");
    $("scope").textContent = m === "tw" ? "台灣上市＋上櫃普通股，4 小時、日線、週線" : "幣安、OKX、Gate 的 USDT 交易對，4 小時、日線、週線";
    showControls(false);
    $("detail").hidden = true;
    $("count").textContent = "";
    $("list").innerHTML = "";
    $("more").hidden = true;
    if (missing && m === "tw") {
      showNotice("empty", "台股資料還沒產生",
        "台股每個交易日收盤後（台北 14:10 左右）掃描一次；第一次掃描完成後，這裡就會出現上市、上櫃股票的頭肩底型態。" +
        "現在可以先看加密貨幣。", { label: "看加密貨幣", run: function () { switchMarket("crypto"); } });
      return;
    }
    showNotice("error", "讀不到掃描資料",
      "<code>" + esc(dataUrl(m)) + "</code> 載入失敗（" + esc(err && err.message ? err.message : err) + "）。" +
      "如果網站剛建好，等第一次掃描跑完再重新整理；本機預覽請在 <code>site</code> 資料夾裡執行 " +
      "<code>python -m http.server</code> 再用瀏覽器開，不要直接雙擊 HTML 檔。",
      { label: "重新整理", run: function () { location.reload(); } });
  }

  function loadMarket(m) {
    if (dataCache[m]) return Promise.resolve(dataCache[m]);
    return fetch(dataUrl(m), { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data || !Array.isArray(data.results) || !data.settings) throw new Error("檔案格式不對");
        dataCache[m] = data; delete loadErr[m];
        buildKeyIndex();
        return data;
      }, function (err) { loadErr[m] = err; throw err; });
  }
  function loadAllMarkets() { // for the watchlist view: compare stars of every market with its latest scan
    MARKET_ORDER.forEach(function (m) {
      if (dataCache[m] || loadErr[m]) return;
      loadMarket(m).then(function () {
        if (drawerEl().classList.contains("is-open") && drawer.view === "watchlist") renderDrawer();
      }, function () {
        if (drawerEl().classList.contains("is-open") && drawer.view === "watchlist") renderDrawer();
      });
    });
  }

  function renderMarketChrome() {
    var m = state.market;
    document.documentElement.setAttribute("data-market", m);
    Array.prototype.forEach.call(document.querySelectorAll(".mtab"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-market") === m));
    });
    $("panel-title").textContent = MARKETS[m].label + (isTri() ? "收斂三角" : "型態清單");
    $("q-label").textContent = m === "tw" ? "搜尋股票" : "搜尋幣種";
    $("q").placeholder = m === "tw" ? "搜尋代號或名稱，例如 2330、台積電" : "搜尋幣種，例如 BTC";
    $("wl-n").textContent = watchInMarket(m);
    document.title = isRec() ? (m === "tw" ? "台股" : "") + "實際戰績｜預米蛋餅" : isTri() ? (m === "tw" ? "台股" : "") + "收斂三角｜預米蛋餅" : (m === "tw" ? "台股" : "") + "預米蛋餅";
    renderDownload();
  }

  function setUrlMarket(m, keepHash) {
    try {
      var qs = new URLSearchParams(location.search);
      if (m === "crypto") qs.delete("market"); else qs.set("market", m);
      if (isTri()) qs.set("view", "triangles"); else if (isRec()) qs.set("view", "record"); else qs.delete("view");
      var q = qs.toString();
      history.replaceState(null, "", location.pathname + (q ? "?" + q : "") + (keepHash ? location.hash : ""));
    } catch (e) { /* file:// etc. */ }
  }

  // Switch the list, filters, counts and detail to market m. selectId: a pattern to open after loading.
  function switchMarket(m, selectId) {
    if (isRec()) return showRecord(m);
    if (isTri()) return showTriangles(m, selectId);
    if (!MARKETS[m]) m = "crypto";
    var changed = m !== state.market;
    state.market = m;
    if (changed) {
      state.q = ""; $("q").value = ""; state.shown = PAGE; state.selected = null;
      setUrlMarket(m, false);
    }
    if (settings.market !== m) { settings.market = m; saveSettings(); }
    renderMarketChrome();
    if (changed || !dataCache[m]) { $("meta").textContent = "正在讀取" + MARKETS[m].label + "掃描資料…"; }
    return loadMarket(m).then(function (data) {
      if (state.market !== m) return;
      state.data = data;
      showControls(true);
      if (selectId && data.results.some(function (r) { return r.pattern_id === selectId; })) state.selected = selectId;
      renderMeta();
      update();
      if (selectId && state.selected === selectId) select(selectId, true);
      if (drawerEl().classList.contains("is-open")) renderDrawer();
    }, function (err) {
      if (state.market !== m) return;
      state.data = null;
      fail(m, err);
      if (drawerEl().classList.contains("is-open")) renderDrawer();
    });
  }

  /* ---------- D46 entry screen ---------- */
  // Faint candlesticks drawing an inverse head-and-shoulders behind the card (fixed seed: same picture every visit).
  function splashCandles() {
    var svg = $("splash-bg"), W = 1600, H = 900, N = 96, seed = 7;
    var rnd = function () { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    var dip = function (x, c, w, d) { var t = (x - c) / w; return Math.abs(t) < 1 ? d * (1 + Math.cos(Math.PI * t)) / 2 : 0; };
    var path = function (i) { var x = i / (N - 1); return 0.62 - 0.25 * x + dip(x, 0.22, 0.13, 0.22) + dip(x, 0.5, 0.17, 0.42) + dip(x, 0.78, 0.13, 0.22) - (x > 0.88 ? (x - 0.88) * 1.6 : 0); };
    var bw = W / N, out = "", prev = path(0) * H;
    for (var i = 0; i < N; i++) {
      var o = prev, c = path(i) * H + (rnd() - 0.5) * 34, hi = Math.min(o, c) - rnd() * 22, lo = Math.max(o, c) + rnd() * 22;
      var cls = c <= o ? "c-up" : "c-dn", x = i * bw + bw / 2;
      out += "<g class=\"" + cls + "\"><line x1=\"" + x.toFixed(1) + "\" y1=\"" + hi.toFixed(1) + "\" x2=\"" + x.toFixed(1) + "\" y2=\"" + lo.toFixed(1) +
        "\" stroke-width=\"2\"/><rect x=\"" + (x - bw * 0.32).toFixed(1) + "\" y=\"" + Math.min(o, c).toFixed(1) + "\" width=\"" + (bw * 0.64).toFixed(1) +
        "\" height=\"" + Math.max(2, Math.abs(c - o)).toFixed(1) + "\"/></g>";
      prev = c;
    }
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.innerHTML = out;
  }
  function setupSplash() {
    var root = document.documentElement, el = $("splash");
    if (!root.classList.contains("show-splash")) { el.hidden = true; return; }
    splashCandles();
    var login = $("splash-login");
    if (loggedIn()) login.hidden = true; else login.href = loginUrl();
    setInert(true);
    var leave = function () {
      try { sessionStorage.setItem("ihs.splash.v1", "1"); } catch (e) { /* shown again next load */ }
      setInert(false);
      var done = function () { root.classList.remove("show-splash"); el.hidden = true; $("menu-btn").focus(); };
      if (reduceMotion) done(); else { el.classList.add("is-leaving"); setTimeout(done, 350); }
    };
    $("splash-go").addEventListener("click", leave);
    login.addEventListener("click", function () { try { sessionStorage.setItem("ihs.splash.v1", "1"); } catch (e) { /* ignore */ } });
    el.addEventListener("keydown", function (e) {
      if (e.key === "Escape") leave();
      else if (e.key === "Tab") { // keep focus on the two buttons
        var f = [$("splash-go")].concat(login.hidden ? [] : [login]), i = f.indexOf(document.activeElement);
        e.preventDefault(); f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
      }
    });
    $("splash-go").focus();
  }

  /* ---------- D47 蛋餅精靈: rule-based tips (no AI, no advice) ---------- */
  var SPRITE_GENERAL = [
    "失效價是頭部的最低點；收盤跌破它，這個頭肩底就不成立了。",
    "網站只用已收盤的 K 線計算；「右肩形成中」的右肩位置會隨新 K 線移動，等右肩確認會比較穩。",
    "回測結果：台股頭肩底試過的進出場方法，沒有一組在訓練期和測試期都贏過直接持有 0050。把它當觀察工具，部位要控制。",
    "回測裡，離 52 週新高越近（相對強勢）的訊號，表現比隨機挑選好，但仍沒有贏過 0050。",
    "移動停損可以讓單筆的報酬風險比變好看，但回測的長期期望值幾乎沒變。",
    "按清單旁的 ☆ 可以加入觀察名單；用 Discord 登入後，狀態改變時機器人會通知你。"
  ];
  function signedPct(x) { return (x >= 0 ? "+" : "") + pct(x); }
  function spritePatternTips(r) {
    var name = "<b>" + esc(symText(r)) + "</b> " + esc(tfName(r.timeframe)), c = r.current_close, t = [];
    var px = function (p) { return esc(fmtPrice(p, c)); };
    t.push(name + "：" + (r.status === "forming" ? "右肩還在形成，圖上的 RS 是暫定點，之後的 K 線可能讓它移動。"
      : r.status === "watchlist" ? "右肩已確認、還沒突破頸線，目前" + esc(distText(r)) + "，突破價約 " + px(r.breakout_level) + "。"
      : r.status === "confirmed" ? "已經突破頸線" + (r.bars_since_breakout !== null && r.bars_since_breakout !== undefined ? "（" + r.bars_since_breakout + " 根 K 線前）" : "") + "。" +
        (r.volume_ratio >= 1.5 ? "突破那根的成交量是 20 根均量的 " + r.volume_ratio.toFixed(1) + " 倍。" : "")
      : "這個型態已過期，參考看看就好。"));
    if (c > 0 && r.invalidation_price > 0 && r.tp1 > 0) {
      var risk = c - r.invalidation_price, reward = r.tp1 - c;
      var s = name + "：失效價 " + px(r.invalidation_price) + "（距現價 " + signedPct(r.invalidation_price / c - 1) + "），TP1 " + px(r.tp1) +
        "（" + signedPct(r.tp1 / c - 1) + "）。";
      if (risk > 0 && reward > 0) s += "以現價算，往 TP1 的空間是往失效價的 <b>" + (reward / risk).toFixed(1) + " 倍</b>" + (reward < risk ? "，報酬風險比偏低。" : "。");
      else if (reward <= 0) s += "現價已經超過 TP1，下一個目標是 TP2 " + px(r.tp2) + "。";
      t.push(s);
    }
    if (r.shape_score !== null && r.shape_score !== undefined && r.shape_score < 50) {
      t.push(name + "：形狀分 " + r.shape_score.toFixed(0) + "，和標準頭肩底的樣子差比較多，建議多看一眼圖。");
    }
    return t;
  }
  function spriteOverviewTips() {
    var m = MARKETS[state.market].label, d = state.data;
    if (isRec()) return ["這裡是" + m + "已突破型態後來的結果（碰到 TP1、TP2 或失效價）。看勝率也要看筆數，筆數少的時候參考價值有限。"];
    if (isTri()) return ["收斂三角的方向以實際突破為準：Bulkowski 的統計裡，下降三角反而有一半以上是向上突破。"];
    if (!d) return [];
    var n = { forming: 0, watchlist: 0, confirmed: 0 };
    d.results.forEach(function (r) { if (n[r.status] !== undefined) n[r.status]++; });
    var t = [m + "目前有 " + d.results.length + " 個頭肩底：已突破 " + n.confirmed + "、右肩已確認 " + n.watchlist + "、右肩形成中 " + n.forming + "。"];
    var near = d.results.filter(function (r) { return r.status === "watchlist" && r.neckline_side === "below"; })
      .sort(function (a, b) { return Math.abs(a.distance_to_neckline) - Math.abs(b.distance_to_neckline); })[0];
    if (near) t.push("右肩已確認的型態裡，最接近突破的是 <b>" + esc(symText(near)) + "</b> " + esc(tfName(near.timeframe)) + "，" + esc(distText(near)) + "。");
    return t;
  }
  // D48 Q&A: keyword-matched answers over the scan data already published with the site (no AI, no API cost).
  var SPRITE_CHIPS = ["這個型態怎麼看", "最接近突破的是？", "分數最高的？", "什麼是失效價", "TP1 怎麼算", "給我一個提示"];
  // first match wins, so the more specific patterns come first
  var SPRITE_GLOSSARY = [
    [/52\s*週|五十二週|一年高|年高|一年低|年線位置/, "「52 週區間位置」＝目前價在過去一年收盤價區間的位置：0% 在一年最低、100% 在一年最高（台股用 252 個交易日、加密貨幣用 365 天）。回測中接近一年高點的台股型態表現相對較好，但沒有勝過持有 0050，也還不到統計顯著。清單可以用排序「接近 52 週高點」。"],
    [/買|賣|進場|出場|推薦|明牌|該不該|能不能/, "我不能給買賣建議喔。我可以告訴你哪些型態最接近突破、分數最高，或幫你看某個型態離失效價和 TP1 多遠，決定還是要你自己來。"],
    [/回測|勝率|0050|有用嗎|準嗎|準不準|賺/, "老實說：台股頭肩底試過的進出場方法，沒有一組在訓練期和測試期都贏過直接持有 0050；離 52 週新高近的訊號比隨機挑好，但仍沒贏 0050。把它當觀察工具，部位要控制。"],
    [/失效|停損|止損/, "失效價＝頭部的最低點。收盤跌破它，代表這個頭肩底不成立了。"],
    [/tp1|第一目標|斐波|fib/i, "TP1：以頭部低點為 0、頸線中點為 1，畫對數斐波那契，取 1.414 的位置。"],
    [/tp2|第二目標/i, "TP2＝突破價 ×（1＋頭部到頸線的漲幅）。還沒突破時用目前頸線價當突破價，所以是預估。"],
    [/目標/, "頭肩底有兩個目標：TP1（對數斐波那契 1.414）和 TP2（突破價 ×（1＋頭到頸線漲幅））。問我「TP1 怎麼算」或「TP2 怎麼算」看細節。"],
    [/斜率/, "頸線斜率看 N1 到 N2 的價格變化：往下斜、水平或往上斜。清單上方的「頸線斜率」可以篩選。"],
    [/頸線/, "頸線是左右兩個反彈高點 N1、N2 連成的線。右肩之後的 K 線最高價（含影線）穿過頸線再加一點緩衝，就算突破。"],
    [/形成中/, "「右肩形成中」：右肩的最低點還沒被確認，圖上的 RS 是暫定點，會隨新 K 線移動。"],
    [/已確認/, "「右肩已確認」：右肩已經成形、還沒突破頸線。這階段要看的是能不能有效突破頸線。"],
    [/過期/, "放太久沒動靜、或突破已經很久的型態會標成「已過期」，參考就好。"],
    [/突破/, "右肩之後 K 線的最高價（含影線）穿過頸線再加一點緩衝，就算「已突破」。"],
    [/形狀/, "形狀分（0–100）衡量和標準頭肩底有多像：五點之間有沒有夾著更大的轉折、走勢有沒有貼著骨架等。越高越標準。"],
    [/分數|評分|幾分/, "分數滿分 100：左右肩對稱 30、頭部深度 25、頸線平緩 25、突破或接近頸線 20。"],
    [/三角|收斂/, "收斂三角由阻力線（連高點）和支撐線（連低點）往右收斂組成。從選單的台股／加密貨幣 →「收斂三角」可以看。方向以實際突破為準。"],
    [/風報|報酬風險|rr|盈虧/i, "報酬風險比＝往目標的空間 ÷ 往停損的空間。先點開一個型態，再問我「這個型態怎麼看」，我會用現價算往 TP1 和往失效價的比例。"],
    [/頭肩|五點|左肩|右肩|頭部/, "頭肩底由五個點組成：左肩 LS、頸線點 N1、頭 H、頸線點 N2、右肩 RS。頭是最低點，左右肩高度接近，N1 和 N2 連成頸線。"],
    [/觀察|星號|收藏|關注/, "在清單或詳細頁按 ☆ 就能加入觀察名單；用 Discord 登入後會同步到機器人，狀態改變時通知你。"],
    [/discord|通知|登入/i, "在「設定 → 雲端同步」用 Discord 登入，觀察名單會同步到 Discord 機器人，型態狀態改變時會 @你或私訊你。"],
    [/多久|更新|什麼時候|幾點/, "加密貨幣每 4 小時掃描一次；台股每個交易日收盤後（台北 14:10 左右）掃描一次。都只用已收盤的 K 線。"]
  ];
  var SPRITE_STOP = { TP1: 1, TP2: 1, RR: 1, AI: 1, OK: 1, USDT: 1, RS: 1, LS: 1, N1: 1, N2: 1, ATR: 1, DISCORD: 1, FIB: 1 };
  var sprite = { greeted: false, tip: -1 };
  function spriteMsg(html, me) {
    var log = $("sprite-log"), el = document.createElement("div");
    el.className = "sm " + (me ? "sm--me" : "sm--bot");
    el.innerHTML = html;
    log.appendChild(el);
    while (log.children.length > 30) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
  }
  function spriteGo(rows) { // buttons that open a pattern (head-and-shoulders or triangle)
    return "<div class=\"sm__go\">" + rows.map(function (x) {
      return "<button type=\"button\" data-go=\"" + x.kind + "\" data-market=\"" + rowMarket(x.r) + "\" data-id=\"" + esc(x.r.pattern_id) + "\">" + x.label + " ›</button>";
    }).join("") + "</div>";
  }
  function ihsLabel(r) {
    return "<b>" + esc(symText(r)) + "</b> " + esc(tfName(r.timeframe)) + "・" + esc(STATUS_LABEL[r.status] || r.status) + "・" + r.pattern_score.toFixed(1) + " 分";
  }
  function triLabel(r) {
    return "<b>" + esc(symText(r)) + "</b> " + esc(tfName(r.timeframe)) + "・" + esc(TRI_TYPE_LABEL[r.triangle_type] || "收斂三角") + "・" + esc(TRI_STATUS_LABEL[r.status] || r.status);
  }
  function spriteFind(q) { // pattern rows whose symbol / name appears in the question
    var toks = (q.toUpperCase().match(/[A-Z0-9]{2,15}/g) || []).filter(function (t) {
      return !SPRITE_STOP[t] && !/^\d{1,2}[HDW]$/.test(t);
    }).map(function (t) { return t.replace(/USDT$/, "") || t; });
    var hit = function (r) {
      if (rowMarket(r) === "tw") return toks.indexOf(r.symbol) >= 0 || (!!r.name && r.name.length >= 2 && q.indexOf(r.name) >= 0);
      return toks.indexOf(symParts(r.symbol).base) >= 0;
    };
    var out = [];
    MARKET_ORDER.forEach(function (m) {
      if (dataCache[m]) dataCache[m].results.forEach(function (r) { if (hit(r)) out.push({ kind: "ihs", r: r, label: "頭肩底 " + ihsLabel(r) }); });
      if (triCache[m]) triCache[m].results.forEach(function (r) { if (hit(r)) out.push({ kind: "tri", r: r, label: "收斂三角 " + triLabel(r) }); });
    });
    return { rows: out, toks: toks };
  }
  function selectedRow() {
    return !isTri() && !isRec() && state.data && state.selected
      ? state.data.results.find(function (x) { return x.pattern_id === state.selected; }) || null : null;
  }
  function spriteAnswer(q) {
    var d = dataCache[state.market], lab = MARKETS[state.market].label;
    if (/提示|小技巧/.test(q)) { sprite.tip = (sprite.tip + 1) % SPRITE_GENERAL.length; return SPRITE_GENERAL[sprite.tip]; }
    var f = spriteFind(q);
    if (f.rows.length) return "找到 " + f.rows.length + " 個型態" + (f.rows.length > 6 ? "（先列 6 個）" : "") + "：" + spriteGo(f.rows.slice(0, 6));
    if (/這個|這張|這檔|這支|怎麼看|解讀|分析|看法/.test(q)) {
      var r = selectedRow();
      return r ? spritePatternTips(r).join("<br>") : "先在清單點一個頭肩底型態，再問我「這個型態怎麼看」，我會用它的資料說明。";
    }
    if (/接近|快突破|快要突破|準備突破/.test(q)) {
      if (!d) return "資料還在讀取，等一下再問我。";
      var near = d.results.filter(function (r) { return r.status === "watchlist" && r.neckline_side === "below"; })
        .sort(function (a, b) { return Math.abs(a.distance_to_neckline) - Math.abs(b.distance_to_neckline); }).slice(0, 3);
      return near.length ? lab + "右肩已確認、最接近頸線的 " + near.length + " 個：" + spriteGo(near.map(function (r) {
        return { kind: "ihs", r: r, label: ihsLabel(r) + "・" + esc(distText(r)) };
      })) : lab + "目前沒有右肩已確認、還在頸線下方的型態。";
    }
    if (/最高|排名|前幾|最強|最好/.test(q)) {
      if (!d) return "資料還在讀取，等一下再問我。";
      var top = d.results.filter(function (r) { return r.status !== "stale"; })
        .sort(function (a, b) { return b.pattern_score - a.pattern_score; }).slice(0, 3);
      return lab + "分數最高的 " + top.length + " 個（分數高不代表一定會漲）：" + spriteGo(top.map(function (r) { return { kind: "ihs", r: r, label: ihsLabel(r) }; }));
    }
    if (/概況|有哪些|有什麼|今天|現在|總共|幾個|多少個/.test(q)) return spriteOverviewTips().join("<br>") || "資料還在讀取，等一下再問我。";
    for (var i = 0; i < SPRITE_GLOSSARY.length; i++) if (SPRITE_GLOSSARY[i][0].test(q)) return SPRITE_GLOSSARY[i][1];
    if (f.toks.length) return "目前的掃描結果裡沒有 <b>" + esc(f.toks.join("、")) + "</b> 的頭肩底或收斂三角。代號可以打英文（BTC）或台股代號、名稱（2330、台積電）。";
    return "這題我還不會 😅 可以問我：某個幣或股票有沒有型態（例如 BTC、2330）、「這個型態怎麼看」、「最接近突破的是？」，或名詞像失效價、頸線、TP1、形狀分。";
  }
  function spriteAsk(q) {
    q = String(q || "").trim();
    if (!q) return;
    spriteMsg(esc(q), true);
    var loads = [];
    MARKET_ORDER.forEach(function (m) { // symbol questions look in both markets
      if (!dataCache[m] && !loadErr[m]) loads.push(loadMarket(m).then(null, function () { /* answer with what we have */ }));
      if (!triCache[m] && !triErr[m]) loads.push(loadTri(m).then(null, function () { /* ignore */ }));
    });
    Promise.all(loads).then(function () { spriteMsg(spriteAnswer(q), false); });
  }
  function spriteOpen(on) {
    $("sprite-bubble").hidden = !on;
    $("sprite-btn").setAttribute("aria-expanded", String(on));
    if (!on) return;
    if (!sprite.greeted) {
      sprite.greeted = true;
      $("sprite-chips").innerHTML = SPRITE_CHIPS.map(function (c) { return "<button type=\"button\">" + esc(c) + "</button>"; }).join("");
      spriteMsg("嗨！我是蛋餅精靈。可以問我某個幣或股票有沒有型態（例如 BTC、2330、台積電），或點下面的問題。", false);
    }
    spriteBubbleSide(); // measured after the content is in
    $("sprite-q").focus();
  }
  /* D52: drag the sprite anywhere. The spot is a per-browser convenience (localStorage, stored as a fraction of the
     free space so it survives window resizes); a press that moves < 6px is still a click that opens the bubble. */
  var SPRITE_POS_KEY = "ihs.sprite.pos.v1";
  var SPRITE_BITS = ["♥", "✦", "♡", "✧", "★"];
  function spritePlace(x, y) {
    var el = $("sprite"), b = $("sprite-btn"), w = b.offsetWidth, h = b.offsetHeight;
    x = Math.max(4, Math.min(window.innerWidth - w - 4, x));
    y = Math.max(4, Math.min(window.innerHeight - h - 4, y));
    el.style.left = x + "px"; el.style.top = y + "px"; el.style.right = "auto"; el.style.bottom = "auto";
    return { x: x, y: y };
  }
  function spriteRestore() {
    var p = null;
    try { p = JSON.parse(localStorage.getItem(SPRITE_POS_KEY) || "null"); } catch (e) { /* storage off: default corner */ }
    if (!p || typeof p.xr !== "number" || typeof p.yr !== "number") return;
    var b = $("sprite-btn");
    spritePlace(p.xr * (window.innerWidth - b.offsetWidth), p.yr * (window.innerHeight - b.offsetHeight));
  }
  function spriteSave(pos) {
    var b = $("sprite-btn");
    try {
      localStorage.setItem(SPRITE_POS_KEY, JSON.stringify({
        xr: pos.x / Math.max(1, window.innerWidth - b.offsetWidth), yr: pos.y / Math.max(1, window.innerHeight - b.offsetHeight) }));
    } catch (e) { /* not kept: fine */ }
  }
  function spriteBubbleSide() { // open toward the roomier side and shrink the chat log so the bubble stays on screen
    var el = $("sprite"), bub = $("sprite-bubble"), log = $("sprite-log"), r = $("sprite-btn").getBoundingClientRect();
    var above = r.top - 14, below = window.innerHeight - r.bottom - 14;
    el.classList.toggle("is-left", r.left + r.width / 2 < window.innerWidth / 2);
    el.classList.toggle("is-below", below > above);
    if (bub.hidden) return;
    log.style.maxHeight = "";
    var fixed = bub.offsetHeight - log.offsetHeight; // header + chips + input
    log.style.maxHeight = Math.max(80, Math.min(320, Math.max(above, below) - fixed)) + "px";
  }
  function spriteParticle(x, y) {
    var p = document.createElement("span");
    p.className = "sprite-p";
    p.textContent = SPRITE_BITS[Math.floor(Math.random() * SPRITE_BITS.length)];
    p.style.left = x + "px"; p.style.top = y + "px";
    p.style.color = ["#F28B6B", "#F5B83D", "#E8743B", "#F7C548"][Math.floor(Math.random() * 4)];
    p.style.setProperty("--dx", Math.round(Math.random() * 30 - 15) + "px");
    p.style.setProperty("--rot", Math.round(Math.random() * 60 - 30) + "deg");
    p.addEventListener("animationend", function () { p.remove(); });
    document.body.appendChild(p);
  }
  function setupSpriteDrag() {
    var btn = $("sprite-btn"), el = $("sprite"), drag = null;
    btn.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      var r = btn.getBoundingClientRect();
      drag = { id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top, lx: e.clientX, px: e.clientX, py: e.clientY, moved: false };
      try { btn.setPointerCapture(e.pointerId); } catch (err) { /* old browsers: still works while over the button */ }
    });
    btn.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (!drag.moved) {
        if (Math.abs(dx) + Math.abs(dy) < 6) return;
        drag.moved = true;
        el.classList.add("is-dragging");
        btn.classList.remove("is-landing");
      }
      drag.pos = spritePlace(drag.ox + dx, drag.oy + dy);
      btn.style.setProperty("--tilt", Math.max(-25, Math.min(25, (e.clientX - drag.lx) * 1.6)) + "deg");
      drag.lx = e.clientX;
      if (!reduceMotion && Math.abs(e.clientX - drag.px) + Math.abs(e.clientY - drag.py) > 26) {
        spriteParticle(e.clientX, e.clientY); drag.px = e.clientX; drag.py = e.clientY;
      }
    });
    var end = function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var d = drag;
      drag = null;
      if (!d.moved) return;
      sprite.dragged = true; // swallow the click that follows this pointerup
      setTimeout(function () { sprite.dragged = false; }, 300);
      el.classList.remove("is-dragging");
      btn.style.setProperty("--tilt", "0deg");
      void btn.offsetWidth; // restart the landing bounce
      btn.classList.add("is-landing");
      if (d.pos) spriteSave(d.pos);
      if (!$("sprite-bubble").hidden) spriteBubbleSide();
    };
    btn.addEventListener("pointerup", end);
    btn.addEventListener("pointercancel", end);
    btn.addEventListener("animationend", function (e) { if (e.animationName === "sprite-land") btn.classList.remove("is-landing"); });
    btn.addEventListener("keydown", function (e) { // keyboard users: arrow keys nudge it
      var step = { ArrowLeft: [-24, 0], ArrowRight: [24, 0], ArrowUp: [0, -24], ArrowDown: [0, 24] }[e.key];
      if (!step) return;
      e.preventDefault();
      var r = btn.getBoundingClientRect(), pos = spritePlace(r.left + step[0], r.top + step[1]);
      spriteSave(pos);
      if (!$("sprite-bubble").hidden) spriteBubbleSide();
    });
    window.addEventListener("resize", spriteRestore);
    spriteRestore();
  }
  function setupSprite() {
    setupSpriteDrag();
    $("sprite-btn").addEventListener("click", function () {
      if (sprite.dragged) { sprite.dragged = false; return; }
      spriteOpen($("sprite-bubble").hidden);
    });
    $("sprite-close").addEventListener("click", function () { spriteOpen(false); $("sprite-btn").focus(); });
    $("sprite-form").addEventListener("submit", function (e) { e.preventDefault(); var i = $("sprite-q"); spriteAsk(i.value); i.value = ""; });
    $("sprite-chips").addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) spriteAsk(b.textContent); });
    $("sprite-log").addEventListener("click", function (e) {
      var b = e.target.closest("[data-go]");
      if (!b) return;
      var m = b.getAttribute("data-market"), id = b.getAttribute("data-id");
      if (b.getAttribute("data-go") === "tri") { showTriangles(m, id); return; }
      state.q = ""; $("q").value = ""; state.tf = "all"; state.status = "all"; state.neck = "all"; state.onlyWl = false; state.shown = PAGE;
      $("only-wl").setAttribute("aria-pressed", "false");
      if (isTri() || isRec()) { state.view = "ihs"; state.selected = null; renderViewChrome(); setUrlMarket(m, false); }
      switchMarket(m, id);
    });
    $("sprite").addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("sprite-bubble").hidden) { spriteOpen(false); $("sprite-btn").focus(); }
    });
  }

  function start() {
    setupSplash();
    setupSprite();
    bind();
    bindExtras();
    applySettingsToState();
    var qs = null;
    try { qs = new URLSearchParams(location.search); } catch (e) { /* old browsers */ }
    var hash = decodeURIComponent(location.hash.slice(1));
    // market: ?market=tw > a Taiwan pattern in the hash > the market viewed last
    var m = qs && MARKETS[qs.get("market")] ? qs.get("market")
      : /^(twse|tpex)\|/.test(hash) ? "tw" : /^[a-z]+\|/.test(hash) ? "crypto" : settings.market;
    state.market = MARKETS[m] ? m : "crypto";
    if (qs && qs.get("view") === "record") { // D41: ?view=record[&market=tw][&status=tp2&pt=ihs&tf=1d&sort=ret_desc&retro=1]
      state.view = "rec";
      if (REC_STATUS_LABEL[qs.get("status")]) rec.status = qs.get("status");
      if (REC_PT_LABEL[qs.get("pt")]) rec.pt = qs.get("pt");
      if (TF_LABEL[qs.get("tf")]) rec.tf = qs.get("tf");
      if (REC_SORTS[qs.get("sort")]) rec.sort = qs.get("sort");
      if (qs.get("retro") === "1") rec.retro = true;
      showRecord(state.market).then(function () {
        try { applyTestParams(qs); } catch (e) { /* ignore */ }
      });
      if (loginNotice) showToast(loginNotice, loginOk);
      if (loggedIn()) syncAll();
      return;
    }
    if (qs && qs.get("view") === "triangles") { // D38: ?view=triangles[&market=tw][&tf=1d&status=forming&type=ascending&q=BTC]
      state.view = "tri";
      if (TF_LABEL[qs.get("tf")]) state.ttf = qs.get("tf");
      if (TRI_STATUS_LABEL[qs.get("status")]) state.tst = qs.get("status");
      if (TRI_TYPE_LABEL[qs.get("type")]) state.ttype = qs.get("type");
      if (TRI_SORTS[qs.get("sort")]) state.tsort = qs.get("sort");
      if (qs.get("q")) { state.q = qs.get("q").trim().toUpperCase().replace(/\s+/g, ""); $("q").value = qs.get("q"); }
      showTriangles(state.market, hash || null).then(function () {
        try { applyTestParams(qs); } catch (e) { /* ignore */ }
      });
      if (loginNotice) showToast(loginNotice, loginOk);
      if (loggedIn()) syncAll();
      return;
    }
    renderMarketChrome();
    loadMarket(state.market)
      .then(function (data) {
        state.data = data;
        // optional deep-link filters: ?status=forming&tf=1d&neck=down&sort=dist&q=BTC
        if (qs) {
          if (STATUS_LABEL[qs.get("status")]) state.status = qs.get("status");
          if (TF_LABEL[qs.get("tf")]) state.tf = qs.get("tf");
          if (NECK_LABEL[qs.get("neck")]) state.neck = qs.get("neck");
          if (SORTS[qs.get("sort")]) { state.sort = qs.get("sort"); $("sort").value = state.sort; }
          if (qs.get("q")) { state.q = qs.get("q").trim().toUpperCase().replace(/\s+/g, ""); $("q").value = qs.get("q"); }
        }
        if (hash && data.results.some(function (r) { return r.pattern_id === hash; })) state.selected = hash;
        renderMeta();
        update();
        try { if (qs) applyTestParams(qs); } catch (e) { /* ignore */ }
      })
      .catch(function (err) {
        fail(state.market, err);
        try { if (qs && qs.get("drawer") && VIEW_TITLE[qs.get("drawer")]) openDrawer(qs.get("drawer")); } catch (e) { /* ignore */ }
      });
    if (loginNotice) showToast(loginNotice, loginOk);
    if (loggedIn()) syncAll();
  }

  start();
})();
