/* 頭肩底掃描 front end.
 * Draws only what data/scan.json and data/bars/*.json contain (built by build_site.py).
 * No detection or scoring logic lives here: just formatting, filtering, sorting by JSON fields
 * and linear mapping to pixels. Bars for a pattern are fetched only when it is opened. */
(function () {
  "use strict";

  var DATA_DIR = "data/";
  var DATA_URL = DATA_DIR + "scan.json";
  var PAGE = 150; // rows rendered per "show more"
  var TF_LABEL = { "4h": "4 小時", "1d": "日線", "1w": "週線" };
  var STATUS_LABEL = { watchlist: "觀察中", confirmed: "已突破", stale: "已過期" };
  var STATUS_ORDER = ["watchlist", "confirmed", "stale"];
  var POINT_NAME = { LS: "左肩", N1: "頸線點", H: "頭", N2: "頸線點", RS: "右肩" };
  var POINTS = ["LS", "N1", "H", "N2", "RS"];
  var C = {
    bg: "#FFFFFF", text: "#131722", muted: "#6A6D78", faint: "#787B86", line: "#E0E3EB", grid: "#F0F3FA",
    accent: "#2962FF", up: "#089981", down: "#F23645"
  };

  var state = { data: null, q: "", tf: "all", status: "all", sort: "score", shown: PAGE, selected: null };
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
  function decimalsFor(p) {
    var a = Math.abs(p);
    if (a >= 1000) return 2;
    if (a >= 1) return 3;
    if (a >= 0.01) return 4;
    if (a >= 0.0001) return 6;
    return 8;
  }
  function fmtPrice(p, ref) {
    if (p === null || p === undefined) return "—";
    var d = decimalsFor(ref === undefined ? p : ref);
    return Number(p).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function pct(x, digits) { return (x * 100).toFixed(digits === undefined ? 1 : digits) + "%"; }
  function symParts(sym) {
    return /USDT$/.test(sym) ? { base: sym.slice(0, -4), quote: "/USDT" } : { base: sym, quote: "" };
  }
  function tfName(tf) { return TF_LABEL[tf] || tf; }
  function distText(r) {
    if (!(r.current_neckline_price > 0)) return "頸線已延伸到 0 以下";
    if (r.neckline_side === "above") return "高於頸線 " + pct(r.distance_to_neckline);
    if (r.neckline_side === "below") return "低於頸線 " + pct(r.distance_to_neckline);
    return "正好在頸線上";
  }

  /* ---------- meta ---------- */
  function renderMeta() {
    var d = state.data, s = d.settings;
    var tfs = s.timeframes.map(tfName).join("、");
    var scanned = d.scanned_symbols !== undefined ? d.scanned_symbols : s.symbols.length;
    var next = d.next_scan_at, nextText;
    if (!next) nextText = "目前沒有定時掃描，資料只在手動執行時更新。";
    else if (new Date(next).getTime() < Date.now()) nextText = "預定的下次掃描（" + fmtStamp(next) + "）已經過了，這份資料可能不是最新的。";
    else nextText = "每 4 小時掃描一次，下次約在 " + (sameDay(next, new Date().toISOString()) ? fmtClock(next) : fmtStamp(next)) + "。";
    var fails = (d.failures || []).length;
    $("scope").textContent = (s.symbols_source === "all" ? "幣安全部現貨 USDT 交易對" : "幣安現貨 USDT 交易對") +
      "，" + tfs;
    $("meta").innerHTML =
      "資料更新於 <time datetime=\"" + esc(d.generated_at) + "\">" + fmtStamp(d.generated_at) + "</time>（" +
      tzLabel() + "），掃描了 " + scanned + " 個幣種。" +
      (fails ? "有 " + fails + " 組 K 線抓取失敗，已略過。" : "") + nextText +
      (s.include_stale ? "這份資料包含已過期的型態（預覽用）。" : "");
  }

  /* ---------- filtering / sorting (JSON fields only) ---------- */
  function matchQ(r) {
    if (!state.q) return true;
    return r.symbol.toUpperCase().indexOf(state.q) !== -1;
  }
  function pass(r, skip) {
    return matchQ(r) &&
      (skip === "tf" || state.tf === "all" || r.timeframe === state.tf) &&
      (skip === "status" || state.status === "all" || r.status === state.status);
  }
  var SORTS = {
    score: function (a, b) { return a.rank - b.rank; },
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
    var byTf = {}, bySt = {}, nTf = 0, nSt = 0, hasStale = false;
    res.forEach(function (r) {
      if (r.status === "stale") hasStale = true;
      if (pass(r, "tf")) { byTf[r.timeframe] = (byTf[r.timeframe] || 0) + 1; nTf++; }
      if (pass(r, "status")) { bySt[r.status] = (bySt[r.status] || 0) + 1; nSt++; }
    });
    $("filter-tf").innerHTML = chip("tf", "all", "全部", nTf) + s.timeframes.map(function (tf) {
      return chip("tf", tf, tfName(tf), byTf[tf] || 0);
    }).join("");
    $("filter-status").innerHTML = chip("status", "all", "全部", nSt) + STATUS_ORDER.filter(function (st) {
      return st !== "stale" || hasStale;
    }).map(function (st) { return chip("status", st, STATUS_LABEL[st], bySt[st] || 0); }).join("");
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
    var n0 = sh.neckline[0], n1 = sh.neckline[1];
    var svg = "<svg class=\"glyph\" viewBox=\"0 0 " + W + " " + H + "\" aria-hidden=\"true\" focusable=\"false\">" +
      "<line class=\"g-neck\" x1=\"" + x(n0[0]) + "\" y1=\"" + y(n0[1]) + "\" x2=\"" + x(n1[0]) + "\" y2=\"" + y(n1[1]) + "\"/>" +
      "<polyline class=\"g-shape\" points=\"" + pts.map(function (p) { return x(p[0]) + "," + y(p[1]); }).join(" ") + "\"/>";
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
    var sp = symParts(r.symbol);
    return "<li><button type=\"button\" class=\"row\" data-id=\"" + esc(r.pattern_id) + "\" aria-current=\"" +
      (state.selected === r.pattern_id) + "\" aria-label=\"" + esc(sp.base + sp.quote + " " + tfName(r.timeframe) + "，" +
      STATUS_LABEL[r.status] + "，" + distText(r) + "，分數 " + r.pattern_score.toFixed(1)) + "\">" +
      glyph(r) +
      "<span class=\"row__name\"><span class=\"sym\">" + esc(sp.base) + "<small>" + esc(sp.quote) + "</small></span>" +
      "<span class=\"tf\">" + esc(tfName(r.timeframe)) + "</span></span>" +
      "<span class=\"row__score\">" + r.pattern_score.toFixed(1) + "<small>分</small></span>" +
      "<span class=\"row__sub\"><span class=\"status status--" + esc(r.status) + "\">" + STATUS_LABEL[r.status] +
      "</span><span class=\"dist\">" + esc(distText(r)) + "</span></span>" +
      "</button></li>";
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
    state.q = ""; state.tf = "all"; state.status = "all"; state.shown = PAGE;
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
        " 個幣種的 " + esc(d.settings.timeframes.map(tfName).join("、")) +
        " K 線上，沒有找到正在成形或剛突破頸線的型態。" + (d.next_scan_at
          ? "下次掃描約在 " + fmtStamp(d.next_scan_at) + "（" + tzLabel() + "），到時新收盤的 K 線會再檢查一次。"
          : "目前沒有定時掃描；新的 K 線收盤後，重新執行一次掃描才會更新這裡。"));
      return rows;
    }
    if (!rows.length) {
      $("count").textContent = "沒有符合的型態";
      $("list").innerHTML = "";
      showNotice("empty", "沒有符合的型態",
        (state.q ? "找不到名稱含「" + esc(state.q) + "」的幣種" : "這個篩選下沒有型態") +
        "。換一個週期或狀態看看，或清除篩選看全部 " + all.length + " 個。",
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
    if (r.status === "watchlist") {
      return "右肩在 " + fmtBar(r.RS_time, tf) + " 成形，" + fmtBar(r.RS_confirm_time, tf) + " 確認；之後 " +
        r.bars_since_rs + " 根 K 線還沒有突破頸線。" + close;
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

  function renderDetail(r) {
    var sp = symParts(r.symbol), tf = r.timeframe;
    $("detail").hidden = false;
    $("detail-head").innerHTML =
      "<div class=\"detail__title\"><h2>" + esc(sp.base) + "<small>" + esc(sp.quote) + "</small></h2>" +
      "<span class=\"tf\">" + esc(tfName(tf)) + "</span>" +
      "<span class=\"status status--" + esc(r.status) + "\">" + STATUS_LABEL[r.status] + "</span>" +
      "<span class=\"score\">分數 <b>" + r.pattern_score.toFixed(1) + "</b></span></div>" +
      "<p class=\"summary\">" + esc(summary(r)) + "</p>";

    $("detail-body").innerHTML =
      "<section><h3>分數拆解</h3><div class=\"scorebars\">" +
      scoreBar("左右肩對稱", r.shoulder_score, 30) + scoreBar("頭部深度", r.head_depth_score, 25) +
      scoreBar("頸線平緩", r.neckline_score, 25) + scoreBar("突破／接近頸線", r.breakout_score, 20) +
      "</div></section>" +
      "<section><h3>量測</h3><dl class=\"facts\">" +
      "<dt>左右肩差</dt><dd>" + pct(r.shoulder_diff, 2) + "</dd>" +
      "<dt>頭部深度</dt><dd>" + pct(r.head_depth, 1) + "</dd>" +
      "<dt>頸線角度</dt><dd>" + r.neckline_angle.toFixed(1) + "°</dd>" +
      "<dt>目前頸線價</dt><dd>" + fmtPrice(r.current_neckline_price, r.current_close) + "</dd>" +
      "<dt>突破量比</dt><dd>" + (r.volume_ratio === null ? (r.breakout_detected ? "—" : "未突破") : r.volume_ratio.toFixed(2) + " 倍") + "</dd>" +
      "<dt>最後一根 K 線</dt><dd>" + fmtBar(r.as_of_time, tf) + (tf === "1w" ? " 當週" : "") + "</dd>" +
      "</dl></section>" +
      "<section><h3>五個點</h3><table class=\"points\"><thead><tr><th scope=\"col\">點</th><th scope=\"col\">K 線</th><th scope=\"col\">價格</th></tr></thead><tbody>" +
      POINTS.map(function (k) {
        return "<tr><td title=\"" + POINT_NAME[k] + "\">" + k + "</td><td>" + fmtBar(r[k + "_time"], tf) +
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
    var id = r.pattern_id, url = DATA_DIR + r.chart_file;
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
    var markers = LW.createSeriesMarkers(candles, []);
    chartApi = { chart: chart, candles: candles, volume: volume, shape: shape, neck: neck, markers: markers };
    return chartApi;
  }

  function drawChart(r, c) {
    var api = initChart();
    if (!api) {
      chartState("error", "<p>圖表程式庫（cdn.jsdelivr.net 上的 Lightweight Charts）載入失敗，所以畫不出 K 線；清單與數據仍可使用。檢查網路後重新整理。</p>");
      return;
    }
    chartState(null);
    var d = Math.abs(r.current_close) >= 10000 ? 0 : decimalsFor(r.current_close);
    api.candles.applyOptions({ priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) } });
    api.candles.setData(c.bars.map(function (b) {
      return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] };
    }));
    api.volume.setData(c.bars.map(function (b) {
      return { time: b[0], value: b[5], color: b[4] >= b[1] ? "rgba(8,153,129,0.32)" : "rgba(242,54,69,0.32)" };
    }));
    api.shape.setData(c.points.map(function (p) { return { time: p.time, value: p.price }; }));
    api.neck.setData(c.neckline.map(function (p) { return { time: p.time, value: p.price }; }));
    var ms = c.points.map(function (p) {
      var low = p.label === "LS" || p.label === "H" || p.label === "RS";
      return { time: p.time, position: low ? "belowBar" : "aboveBar", shape: low ? "arrowUp" : "arrowDown",
        color: C.accent, text: p.label, size: 1 };
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
    if (to >= lastIdx) api.chart.timeScale().fitContent();
    else api.chart.timeScale().setVisibleLogicalRange({ from: -2, to: to + 6 });
    $("chart").setAttribute("aria-label", symParts(r.symbol).base + " " + tfName(r.timeframe) +
      " K 線圖，標出 LS、N1、H、N2、RS 五個點與頸線" + (c.breakout ? "，以及突破點" : ""));
    $("chart-legend").hidden = false;
    var brk = document.querySelector(".lg--brk");
    if (brk) brk.hidden = !c.breakout;
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

  function fail(err) {
    $("meta").textContent = "讀不到掃描資料。";
    document.querySelector(".panel__head .filters").hidden = true;
    document.querySelector(".panel__head .search").hidden = true;
    $("detail").hidden = true;
    $("count").textContent = "";
    showNotice("error", "讀不到掃描資料",
      "<code>" + DATA_URL + "</code> 載入失敗（" + esc(err && err.message ? err.message : err) + "）。" +
      "如果網站剛建好，等第一次掃描跑完再重新整理；本機預覽請在 <code>site</code> 資料夾裡執行 " +
      "<code>python -m http.server</code> 再用瀏覽器開，不要直接雙擊 HTML 檔。",
      { label: "重新整理", run: function () { location.reload(); } });
  }

  function start() {
    bind();
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data || !Array.isArray(data.results) || !data.settings) throw new Error("檔案格式不對");
        state.data = data;
        var hash = decodeURIComponent(location.hash.slice(1));
        if (hash && data.results.some(function (r) { return r.pattern_id === hash; })) state.selected = hash;
        renderMeta();
        update();
      })
      .catch(fail);
  }

  start();
})();
