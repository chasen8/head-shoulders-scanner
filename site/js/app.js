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
  // Filter defaults are shared by both markets; `market` remembers the market viewed last (D29).
  var DEFAULT_SETTINGS = { tf: "all", status: "all", neck: "all", sort: "score", minScore: 0, showLevels: true, showChecks: true,
    market: "crypto" };
  var C = {
    bg: "#FFFFFF", text: "#131722", muted: "#6A6D78", faint: "#787B86", line: "#E0E3EB", grid: "#F0F3FA",
    accent: "#2962FF", up: "#089981", down: "#F23645", forming: "#FF9800",
    tp: "#089981", tp2: "#22AB94", stop: "#787B86", accentSoft: "rgba(41,98,255,0.5)"
  };

  var state = { data: null, market: "crypto", q: "", tf: "all", status: "all", neck: "all", sort: "score", shown: PAGE,
    selected: null, minScore: 0, onlyWl: false };
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
    var d = state.data, s = d.settings;
    var scanned = d.scanned_symbols !== undefined ? d.scanned_symbols : s.symbols.length;
    var next = d.next_scan_at, nextText;
    var every = isTw() ? "每個交易日收盤後（台北 14:10 左右）掃描一次" : "每 4 小時掃描一次";
    if (!next) nextText = "目前沒有定時掃描，資料只在手動執行時更新。";
    else if (new Date(next).getTime() < Date.now()) nextText = "預定的下次掃描（" + fmtStamp(next) + "）已經過了，這份資料可能不是最新的。";
    else nextText = every + "，下次約在 " + (sameDay(next, new Date().toISOString()) ? fmtClock(next) : fmtStamp(next)) + "。";
    var fails = (d.failures || []).length;
    $("scope").textContent = scopeText(d);
    $("meta").innerHTML =
      "<b class=\"meta__mkt\">" + esc(MARKETS[state.market].label) + "</b>資料更新於 <time datetime=\"" + esc(d.generated_at) + "\">" +
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
    name: function (a, b) { return a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : a.rank - b.rank; }
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
        ? "<span class=\"shp\">形狀 " + r.shape_score.toFixed(0) + "</span>" : "") + "</span>" +
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
  function summary(r) {
    var tf = r.timeframe, close = "最新收盤 " + fmtPrice(r.current_close) + "，" + distText(r) + "。";
    if (r.status === "confirmed") {
      return fmtBar(r.breakout_time, tf) + " 那根 K 線收盤突破頸線，到現在過了 " + r.bars_since_breakout + " 根。" + close;
    }
    if (r.status === "forming") {
      return "右肩形成中：N2 之後的最低點（" + fmtBar(r.RS_time, tf) + "）暫定為右肩。" + PROVISIONAL_NOTE +
        (r.breakout_detected ? "已有 K 線收盤站上頸線（" + fmtBar(r.breakout_time, tf) + "），右肩確認後才算已突破。" : "") +
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
      "<dt>目前頸線價</dt><dd>" + px(r.current_neckline_price) + "</dd>" +
      "<dt>突破量比</dt><dd>" + (r.volume_ratio === null ? (r.breakout_detected ? "—" : "未突破") : r.volume_ratio.toFixed(2) + " 倍") + "</dd>" +
      "<dt>最後一根 K 線</dt><dd>" + fmtBar(r.as_of_time, tf) + (tf === "1w" ? " 當週" : "") + "</dd>" +
      "</dl></section>" +
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
        vertLine: { color: "#9598A1", width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: C.text },
        horzLine: { color: "#9598A1", width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: C.text }
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
      color: "rgba(41,98,255,0.6)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
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
    var markers = LW.createSeriesMarkers(candles, []);
    chartApi = { chart: chart, candles: candles, volume: volume, shape: shape, shapeProv: shapeProv, neck: neck,
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
      return { time: b[0], value: b[5], color: b[4] >= b[1] ? "rgba(8,153,129,0.32)" : "rgba(242,54,69,0.32)" };
    }));
    // a newly opened pattern always starts auto-scaled (manual Y zoom is per view)
    api.candles.priceScale().setAutoScale(true);
    if (api.syncAutoscaleBtn) api.syncAutoscaleBtn();
    var prov = !!c.rs_provisional;
    var line = function (p) { return { time: p.time, value: p.price }; };
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

  /* ---------- wiring ---------- */
  function select(id, fromUser) {
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
      state.sort = this.value;
      state.shown = PAGE;
      update();
    });
    $("more").addEventListener("click", function () {
      var firstNew = Math.min(state.shown, filtered().length);
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
    }
    return s;
  }
  var settings = loadSettings();
  function saveSettings() { return writeStore(SET_KEY, settings); }
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
      tv_symbol: str(o.tv_symbol)
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
    var cur = findWatch(key);
    if (cur) {
      watch = watch.filter(function (w) { return w.pattern_key !== key; });
    } else {
      var r = keyIndex[key];
      if (!r) return;
      watch.unshift(normItem({ pattern_key: key, symbol: r.symbol, exchange: r.exchange, market: r.market,
        timeframe: r.timeframe, added_at: new Date().toISOString(), status_at_add: r.status,
        score_at_add: r.pattern_score, tv_symbol: r.tv_symbol, name: r.name }));
    }
    saveWatch();
    refreshStars();
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
    ["topbar", "board", "colophon"].forEach(function (c) {
      var el = document.querySelector("." + c);
      if (!el) return;
      try { el.inert = on; } catch (e) { /* no inert support: the focus trap still holds */ }
    });
  }
  function openDrawer(view) {
    var d = drawerEl();
    clearTimeout(drawer.timer);
    drawer.view = view || "menu";
    renderDrawer();
    $("scrim").hidden = false; d.hidden = false;
    void d.offsetWidth; // let the closed state paint so the slide-in transitions
    d.classList.add("is-open"); $("scrim").classList.add("is-open");
    document.body.classList.add("nav-open");
    $("menu-btn").setAttribute("aria-expanded", "true");
    setInert(true);
    focusFirst();
    if (drawer.view === "watchlist") loadAllMarkets();
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

  function menuHtml() {
    return "<ul class=\"dnav\">" + MARKET_ORDER.map(function (m) {
      var cur = m === state.market, d = dataCache[m];
      var n = d ? d.results.length + " 個型態" : (loadErr[m] ? "尚無資料" : "");
      return "<li><button type=\"button\" class=\"dnav__btn dnav__btn--mkt\" data-act=\"market\" data-market=\"" + m + "\"" +
        (cur ? " aria-current=\"page\"" : "") + "><span>" + esc(MARKETS[m].label) + "</span>" +
        "<span class=\"dnav__sub\">" + (cur ? "目前" + (n ? "・" : "") : "") + esc(n) + "</span></button></li>";
    }).join("") +
      "<li><button type=\"button\" class=\"dnav__btn\" data-act=\"view\" data-view=\"watchlist\">觀察名單（" + watch.length + "）</button></li>" +
      "<li><button type=\"button\" class=\"dnav__btn\" data-act=\"view\" data-view=\"settings\">設定</button></li>" +
      "<li><button type=\"button\" class=\"dnav__btn\" data-act=\"view\" data-view=\"about\">關於</button></li></ul>";
  }
  function watchHtml() {
    var h = "<button type=\"button\" class=\"dback\" data-act=\"back\">‹ 選單</button>";
    if (!storageOk) h += "<p class=\"dwarn\">這個瀏覽器停用了網站儲存，觀察名單只在這次開啟期間有效，重新整理後會消失。</p>";
    if (!watch.length) {
      return h + "<p class=\"dempty\">還沒有加入任何型態。在清單每一列或詳細頁標題旁按星號（☆）就會出現在這裡。</p>";
    }
    var items = watch.slice().sort(function (a, b) { return Date.parse(b.added_at) - Date.parse(a.added_at); });
    h += "<ul class=\"wls\">" + items.map(function (w) {
      var cur = keyIndex[w.pattern_key], wm = rowMarket(w);
      var head = "<span class=\"wl__top\"><span class=\"mtag mtag--" + wm + "\">" + esc(MARKETS[wm].label) + "</span>" +
        "<span class=\"sym\">" + symHtml(w) + "</span>" +
        (w.timeframe ? "<span class=\"tf\">" + esc(tfName(w.timeframe)) + "</span>" : "") +
        (w.exchange ? "<span class=\"mkt\">" + esc(marketText(w)) + "</span>" : "") + "</span>";
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
      "<h3 class=\"dh\">觀察名單備份</h3><p class=\"dnote\">觀察名單只存在這個瀏覽器。換裝置或清除瀏覽資料前，請先匯出。</p>" +
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
      "<p class=\"dmsg\" id=\"d-msg\" role=\"status\" aria-live=\"polite\"></p>";
  }
  function aboutHtml() {
    return "<button type=\"button\" class=\"dback\" data-act=\"back\">‹ 選單</button>" +
      "<div class=\"about\"><p>加密貨幣：幣安、OKX、Gate 的公開 K 線行情，每 4 小時自動掃描一次。</p>" +
      "<p>台股：上市＋上櫃普通股（清單與名稱來自證交所、櫃買中心 OpenAPI），K 線來自 Yahoo Finance（未還原權息，和 TradingView 預設相同），" +
      "每個交易日收盤後（台北 14:10 左右）掃描一次；4 小時線一天兩根：09:00–13:00、13:00–13:30。</p>" +
      "<p>兩邊都只用已收盤的 K 線，頁首會寫出該市場的資料更新時間。</p>" +
      "<p><strong>這是型態偵測工具，不是投資建議。</strong>型態成立不代表價格會上漲，請自行判斷風險。</p>" +
      "<p>觀察名單與設定只存在這個瀏覽器（localStorage），不會上傳到任何地方；換裝置請用「設定」裡的匯出／匯入。</p>" +
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
    if (act === "view") setView(t.getAttribute("data-view"));
    else if (act === "back") setView("menu");
    else if (act === "market") {
      state.onlyWl = false; $("only-wl").setAttribute("aria-pressed", "false");
      closeDrawer();
      switchMarket(t.getAttribute("data-market"));
    } else if (act === "open") {
      var r = keyIndex[t.getAttribute("data-key")];
      if (!r) return;
      state.q = ""; $("q").value = ""; state.tf = "all"; state.status = "all"; state.neck = "all"; state.onlyWl = false; state.shown = PAGE;
      $("only-wl").setAttribute("aria-pressed", "false");
      closeDrawer($("detail"));
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
    $("drawer-body").addEventListener("click", onDrawerClick);
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
  }

  function showControls(on) {
    document.querySelector(".panel__head .filters").hidden = !on;
    document.querySelector(".panel__head .search").hidden = !on;
    $("only-wl").hidden = !on;
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
    $("panel-title").textContent = MARKETS[m].label + "型態清單";
    $("q-label").textContent = m === "tw" ? "搜尋股票" : "搜尋幣種";
    $("q").placeholder = m === "tw" ? "搜尋代號或名稱，例如 2330、台積電" : "搜尋幣種，例如 BTC";
    $("wl-n").textContent = watchInMarket(m);
    document.title = (m === "tw" ? "台股" : "") + "頭肩底掃描";
  }

  function setUrlMarket(m, keepHash) {
    try {
      var qs = new URLSearchParams(location.search);
      if (m === "crypto") qs.delete("market"); else qs.set("market", m);
      var q = qs.toString();
      history.replaceState(null, "", location.pathname + (q ? "?" + q : "") + (keepHash ? location.hash : ""));
    } catch (e) { /* file:// etc. */ }
  }

  // Switch the list, filters, counts and detail to market m. selectId: a pattern to open after loading.
  function switchMarket(m, selectId) {
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

  function start() {
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
  }

  start();
})();
