/* 頭肩底掃描 front end.
 * Draws only what data/scan.json contains (built by build_site.py). No detection or
 * scoring logic lives here: just formatting, filtering and linear mapping to pixels. */
(function () {
  "use strict";

  var DATA_URL = "data/scan.json";
  var TF_LABEL = { "4h": "4 小時", "1d": "日線", "1w": "週線" };
  var STATUS_LABEL = { watchlist: "觀察中", confirmed: "已突破", stale: "已過期" };
  var STATUS_ORDER = ["watchlist", "confirmed", "stale"];
  var POINT_NAME = { LS: "左肩", N1: "頸線點", H: "頭", N2: "頸線點", RS: "右肩" };
  var COLORS = {
    paper: "#EDF2EA", ink: "#1F2B2A", pencil: "#56635F", grid: "#C9D8CC",
    cobalt: "#2F45C8", carmine: "#B8174F"
  };

  var state = { data: null, tf: "all", status: "all", selected: null };
  var chartApi = null;
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
  /* bar time label: 4h bars need the hour, daily/weekly bars do not */
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
    return 6;
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
  function distText(r) {
    if (!(r.current_neckline_price > 0)) return "頸線已延伸到 0 以下";
    if (r.neckline_side === "above") return "高於頸線 " + pct(r.distance_to_neckline);
    if (r.neckline_side === "below") return "低於頸線 " + pct(r.distance_to_neckline);
    return "正好在頸線上";
  }

  /* ---------- meta / filters ---------- */
  function renderMeta() {
    var d = state.data, s = d.settings;
    var tfs = s.timeframes.map(function (tf) { return TF_LABEL[tf] || tf; }).join("、");
    var next = d.next_scan_at, nextText;
    if (!next) nextText = "目前沒有定時掃描，資料只在手動執行時更新。";
    else if (new Date(next).getTime() < Date.now()) nextText = "預定的下次掃描（" + fmtStamp(next) + "）已經過了，這份資料可能不是最新的。";
    else nextText = "每 4 小時掃描一次，下次約在 " + (sameDay(next, new Date().toISOString()) ? fmtClock(next) : fmtStamp(next)) + "。";
    $("meta").innerHTML =
      "資料更新於 <time datetime=\"" + esc(d.generated_at) + "\">" + fmtStamp(d.generated_at) + "</time>（" +
      tzLabel() + "）。這次掃描了 " + s.symbols.length + " 個幣種的 " + esc(tfs) + "。" + nextText +
      (s.include_stale ? "這份資料包含已過期的型態（預覽用）。" : "");
  }

  function chip(group, value, label, count) {
    var on = state[group] === value;
    return "<button type=\"button\" class=\"chip\" data-group=\"" + group + "\" data-value=\"" + esc(value) +
      "\" aria-pressed=\"" + on + "\">" + esc(label) +
      (count === undefined ? "" : "<span class=\"chip__n\">" + count + "</span>") + "</button>";
  }

  function renderFilters() {
    var res = state.data.results, s = state.data.settings;
    var byTf = {}, bySt = {};
    res.forEach(function (r) {
      byTf[r.timeframe] = (byTf[r.timeframe] || 0) + 1;
      bySt[r.status] = (bySt[r.status] || 0) + 1;
    });
    $("filter-tf").innerHTML = chip("tf", "all", "全部", res.length) + s.timeframes.map(function (tf) {
      return chip("tf", tf, TF_LABEL[tf] || tf, byTf[tf] || 0);
    }).join("");
    $("filter-status").innerHTML = chip("status", "all", "全部", res.length) + STATUS_ORDER.filter(function (st) {
      return st !== "stale" || bySt.stale;
    }).map(function (st) { return chip("status", st, STATUS_LABEL[st], bySt[st] || 0); }).join("");
  }

  function filtered() {
    return state.data.results.filter(function (r) {
      return (state.tf === "all" || r.timeframe === state.tf) &&
        (state.status === "all" || r.status === state.status);
    });
  }

  /* ---------- glyph: the pattern's silhouette, JSON prices mapped linearly ---------- */
  function glyph(r) {
    var W = 72, H = 40, P = 4;
    var c = r.chart, pts = c.points, lastBar = c.bars[c.bars.length - 1];
    var t0 = pts[0].time, span = pts[4].time - pts[0].time;
    var tEnd = Math.min(lastBar[0], pts[4].time + span * 0.35);
    var tStart = t0 - span * 0.08;
    var prices = pts.map(function (p) { return p.price; });
    var showTail = lastBar[0] <= tEnd;
    if (showTail) prices.push(lastBar[4]);
    var lo = Math.min.apply(null, prices), hi = Math.max.apply(null, prices);
    var x = function (t) { return P + (t - tStart) / (tEnd - tStart) * (W - 2 * P); };
    var y = function (p) { return H - P - (p - lo) / ((hi - lo) || 1) * (H - 2 * P); };
    var poly = pts.map(function (p) { return x(p.time).toFixed(1) + "," + y(p.price).toFixed(1); }).join(" ");
    var n0 = c.neckline[0], n1 = c.neckline[1];
    var svg = "<svg class=\"glyph\" viewBox=\"0 0 " + W + " " + H + "\" aria-hidden=\"true\" focusable=\"false\">" +
      "<line class=\"g-neck\" x1=\"" + x(n0.time).toFixed(1) + "\" y1=\"" + y(n0.price).toFixed(1) +
      "\" x2=\"" + x(n1.time).toFixed(1) + "\" y2=\"" + y(n1.price).toFixed(1) + "\"/>" +
      "<polyline class=\"g-shape\" points=\"" + poly + "\"/>";
    if (showTail) {
      svg += "<polyline class=\"g-tail\" points=\"" + x(pts[4].time).toFixed(1) + "," + y(pts[4].price).toFixed(1) +
        " " + x(lastBar[0]).toFixed(1) + "," + y(lastBar[4]).toFixed(1) + "\"/>";
    }
    [0, 2, 4].forEach(function (i) {
      svg += "<circle class=\"g-pt\" r=\"1.8\" cx=\"" + x(pts[i].time).toFixed(1) + "\" cy=\"" + y(pts[i].price).toFixed(1) + "\"/>";
    });
    if (c.breakout && c.breakout.time <= tEnd) {
      svg += "<circle class=\"g-brk\" r=\"2.6\" cx=\"" + x(c.breakout.time).toFixed(1) + "\" cy=\"" +
        y(c.breakout.price).toFixed(1) + "\"/>";
    }
    return svg + "</svg>";
  }

  /* ---------- list ---------- */
  function rowHtml(r) {
    var sp = symParts(r.symbol);
    var cur = state.selected === r.pattern_id;
    return "<li><button type=\"button\" class=\"row\" data-id=\"" + esc(r.pattern_id) + "\" aria-current=\"" + cur + "\"" +
      " aria-label=\"" + esc(sp.base + sp.quote + " " + (TF_LABEL[r.timeframe] || r.timeframe) + "，" +
      STATUS_LABEL[r.status] + "，" + distText(r) + "，分數 " + r.pattern_score.toFixed(1)) + "\">" +
      glyph(r) +
      "<span class=\"row__name\"><span class=\"sym\">" + esc(sp.base) + "<small>" + esc(sp.quote) + "</small></span>" +
      "<span class=\"tf\">" + esc(TF_LABEL[r.timeframe] || r.timeframe) + "</span></span>" +
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

  function renderList() {
    var all = state.data.results, rows = filtered(), d = state.data;
    $("notice").hidden = true;
    if (!all.length) {
      $("ledger-title").textContent = "目前沒有型態";
      $("list").innerHTML = "";
      var tfs = d.settings.timeframes.map(function (tf) { return TF_LABEL[tf] || tf; }).join("、");
      showNotice("empty", "目前沒有符合條件的頭肩底",
        "最近一次掃描（" + fmtStamp(d.generated_at) + "）在 " + d.settings.symbols.length + " 個幣種的 " + esc(tfs) +
        " K 線上，沒有找到正在成形或剛突破頸線的型態。" + (d.next_scan_at
          ? "下次掃描約在 " + fmtStamp(d.next_scan_at) + "（" + tzLabel() + "），到時新收盤的 K 線會再檢查一次。"
          : "目前沒有定時掃描；新的 K 線收盤後，重新執行一次掃描才會更新這裡。"));
      return rows;
    }
    if (!rows.length) {
      $("ledger-title").textContent = "這個篩選下沒有型態";
      $("list").innerHTML = "";
      showNotice("empty", "這個篩選下沒有型態", "換一個週期或狀態看看，或清除篩選看全部 " + all.length + " 個。",
        { label: "清除篩選", run: function () { state.tf = "all"; state.status = "all"; update(); } });
      return rows;
    }
    $("ledger-title").textContent = "依分數排序，共 " + rows.length + " 個型態";
    $("list").innerHTML = rows.map(rowHtml).join("");
    return rows;
  }

  /* ---------- detail ---------- */
  function summary(r) {
    var tf = r.timeframe, close = "最新收盤 " + fmtPrice(r.current_close) + "，" + distText(r) + "。";
    if (r.status === "confirmed") {
      return fmtBar(r.breakout_time, tf) + " 那根 K 線收盤突破頸線，到現在過了 " + r.bars_since_breakout +
        " 根。" + close;
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
      "<span class=\"tf\">" + esc(TF_LABEL[tf] || tf) + "</span>" +
      "<span class=\"status status--" + esc(r.status) + "\">" + STATUS_LABEL[r.status] + "</span></div>" +
      "<p class=\"summary\">" + esc(summary(r)) + "</p>";

    var pts = r.chart.points;
    $("detail-body").innerHTML =
      "<section><h3>分數 " + r.pattern_score.toFixed(1) + "</h3><div class=\"scorebars\">" +
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
      pts.map(function (p) {
        return "<tr><td title=\"" + POINT_NAME[p.label] + "\">" + p.label + "</td><td>" + fmtBar(p.time, tf) +
          "</td><td>" + fmtPrice(p.price, r.current_close) + "</td></tr>";
      }).join("") + "</tbody></table></section>";

    drawChart(r);
  }

  /* ---------- chart ---------- */
  function initChart() {
    if (chartApi) return chartApi;
    var LW = window.LightweightCharts;
    if (!LW) return null;
    var el = $("chart");
    var font = getComputedStyle(document.body).fontFamily;
    var chart = LW.createChart(el, {
      autoSize: true,
      layout: {
        background: { type: LW.ColorType.Solid, color: "rgba(0,0,0,0)" },
        textColor: COLORS.pencil, fontFamily: font, fontSize: 12, attributionLogo: true
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { borderColor: COLORS.grid, scaleMargins: { top: 0.08, bottom: 0.22 } },
      timeScale: { borderColor: COLORS.grid, rightOffset: 4, timeVisible: true, secondsVisible: false,
        tickMarkFormatter: function (t, type) {
          var d = toDate(t);
          if (type === 0) return String(d.getFullYear());
          if (type === 1) return (d.getMonth() + 1) + "月";
          if (type === 2) return (d.getMonth() + 1) + "/" + d.getDate();
          return pad(d.getHours()) + ":" + pad(d.getMinutes());
        } },
      crosshair: {
        mode: LW.CrosshairMode.Normal,
        vertLine: { color: COLORS.pencil, labelBackgroundColor: COLORS.ink },
        horzLine: { color: COLORS.pencil, labelBackgroundColor: COLORS.ink }
      },
      localization: { locale: "zh-TW", timeFormatter: function (t) { return fmtStamp(t); } },
      handleScale: { mouseWheel: true, pinch: true },
      handleScroll: { vertTouchDrag: false }
    });
    var candles = chart.addSeries(LW.CandlestickSeries, {
      upColor: COLORS.paper, downColor: COLORS.ink,
      borderUpColor: COLORS.ink, borderDownColor: COLORS.ink,
      wickUpColor: COLORS.ink, wickDownColor: COLORS.ink,
      priceLineVisible: false
    });
    var volume = chart.addSeries(LW.HistogramSeries, {
      priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    var noScale = function () { return null; };
    var shape = chart.addSeries(LW.LineSeries, {
      color: "rgba(47,69,200,0.55)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, autoscaleInfoProvider: noScale
    });
    var neck = chart.addSeries(LW.LineSeries, {
      color: COLORS.cobalt, lineWidth: 2, lineStyle: LW.LineStyle.Dashed, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: noScale
    });
    var markers = LW.createSeriesMarkers(candles, []);
    chartApi = { chart: chart, candles: candles, volume: volume, shape: shape, neck: neck, markers: markers };
    return chartApi;
  }

  function drawChart(r) {
    var api = initChart();
    if (!api) {
      $("chart").innerHTML = "<p class=\"notice notice--error\">圖表程式庫（cdn.jsdelivr.net 上的 Lightweight Charts）載入失敗，所以畫不出 K 線；清單與數據仍可使用。檢查網路後重新整理。</p>";
      return;
    }
    var c = r.chart, d = Math.abs(r.current_close) >= 10000 ? 0 : decimalsFor(r.current_close);
    api.candles.applyOptions({ priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) } });
    api.candles.setData(c.bars.map(function (b) {
      return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] };
    }));
    api.volume.setData(c.bars.map(function (b) {
      return { time: b[0], value: b[5], color: b[4] >= b[1] ? "rgba(86,99,95,0.30)" : "rgba(31,43,42,0.50)" };
    }));
    api.shape.setData(c.points.map(function (p) { return { time: p.time, value: p.price }; }));
    api.neck.setData(c.neckline.map(function (p) { return { time: p.time, value: p.price }; }));
    var ms = c.points.map(function (p) {
      var low = p.label === "LS" || p.label === "H" || p.label === "RS";
      return { time: p.time, position: low ? "belowBar" : "aboveBar", shape: low ? "arrowUp" : "arrowDown",
        color: COLORS.cobalt, text: p.label };
    });
    if (c.breakout) {
      ms.push({ time: c.breakout.time, position: "aboveBar", shape: "circle", color: COLORS.carmine, text: "突破" });
    }
    ms.sort(function (a, b) { return a.time - b.time; });
    api.markers.setMarkers(ms);
    // Frame the pattern itself. Fresh patterns end near the last bar, so this shows everything;
    // for stale ones the user can scroll right to today.
    var rsTime = c.points[4].time, lsTime = c.points[0].time, lastIdx = c.bars.length - 1, rsIdx = 0;
    for (var i = 0; i < c.bars.length; i++) { if (c.bars[i][0] === rsTime) { rsIdx = i; break; } }
    var lsIdx = 0;
    for (var j = 0; j < c.bars.length; j++) { if (c.bars[j][0] === lsTime) { lsIdx = j; break; } }
    var to = Math.min(lastIdx, rsIdx + Math.max(20, Math.round((rsIdx - lsIdx) * 0.8)));
    if (c.breakout && r.status !== "stale") to = lastIdx;
    if (to >= lastIdx) api.chart.timeScale().fitContent();
    else api.chart.timeScale().setVisibleLogicalRange({ from: -2, to: to + 4 });
    $("chart").setAttribute("aria-label", symParts(r.symbol).base + " " + (TF_LABEL[r.timeframe] || r.timeframe) +
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

  function update() {
    renderFilters();
    var rows = renderList();
    if (!rows.length) {
      $("detail").hidden = true;
      return;
    }
    var keep = rows.some(function (r) { return r.pattern_id === state.selected; });
    select(keep ? state.selected : rows[0].pattern_id, false);
  }

  function bind() {
    document.querySelector(".filters").addEventListener("click", function (e) {
      var b = e.target.closest(".chip");
      if (!b) return;
      state[b.getAttribute("data-group")] = b.getAttribute("data-value");
      update();
      var again = document.querySelector(".chip[data-group=\"" + b.getAttribute("data-group") +
        "\"][data-value=\"" + b.getAttribute("data-value") + "\"]");
      if (again) again.focus();
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
      var next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
      next.focus();
    });
  }

  function fail(err) {
    $("meta").textContent = "讀不到掃描資料。";
    document.querySelector(".filters").hidden = true;
    $("detail").hidden = true;
    $("ledger-title").textContent = "沒有資料";
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
