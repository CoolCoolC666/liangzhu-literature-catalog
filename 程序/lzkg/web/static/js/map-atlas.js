/* ================================================================
   良渚文化文献目录站 · 统计页地图增强
   ---------------------------------------------------------------
   借鉴「江湖时序」(DeanChensj/jiangshan-map) 的地图交互骨架：
     图层面板 / 比例尺 / 相机复位 / 地图内搜索 / 年份刷选器 / 悬停浮层
   ★ 硬约束
     ① 纯 ES5（var / function），与站点其它脚本同一语法基线
     ② 零外链：不引 CDN、不 fetch 外部资源，断网可用
     ③ **不编数据**：年份刷选只按「点位所关联文献的出版年」过滤，
        年份取自 /api/stats/mapdata 的 lits[].year；取不到年份的点位保持显示，
        并在界面上写明「无出版年信息」的口径，不推测、不填充。
     ④ 只做界面增强，不改 index.html 的业务逻辑。
   ================================================================ */
(function (global) {
  "use strict";

  var doc = global.document;
  var ST = null;          /* 主脚本里的 MAPST（顶层 var → window.MAPST） */
  var map = null;         /* Leaflet 实例 */
  var R = {};             /* DOM 引用 */
  var on = {};            /* 图层开关状态 */
  var yr = { lo: null, hi: null };   /* 年份刷选区间 */
  var idx = [];           /* 地图内搜索索引 */
  var hideSet = {};       /* 被年份筛选隐藏的点位 id 集合 */

  /* 多语言：优先用 i18n.js 的字典（键=中文原文），没加载就退回中文 */
  function tr(s) {
    try { return global.LZ_tr ? global.LZ_tr(s) : s; } catch (e) { return s; }
  }
  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function hasCoord(o) {
    if (!o) return false;
    if (o.lat === null || o.lat === undefined || o.lat === "") return false;
    if (o.lon === null || o.lon === undefined || o.lon === "") return false;
    return isFinite(Number(o.lat)) && isFinite(Number(o.lon));
  }

  /* ---------------- 比例尺 ---------------- */
  function addScale() {
    if (!global.L || !global.L.control || !global.L.control.scale) return;
    if (map.__mzScaled) return;          /* 同一个 map 只挂一次 */
    map.__mzScaled = true;
    try {
      global.L.control.scale({ metric: true, imperial: false, position: "bottomright" }).addTo(map);
    } catch (e) { /* 比例尺挂不上不影响其它功能 */ }
  }

  /* ---------------- 图层面板 ---------------- */
  function counts() {
    var d = (ST.data || {});
    var n = { box: 0, points: 0, regions: 0, sites: 0 };
    var sg = d.site_group || {};
    if (sg.lon_min !== undefined && sg.lon_min !== null && sg.lon_min !== "" &&
        sg.lon_max !== undefined && sg.lon_max !== null && sg.lon_max !== "" &&
        sg.lat_min !== undefined && sg.lat_min !== null && sg.lat_min !== "" &&
        sg.lat_max !== undefined && sg.lat_max !== null && sg.lat_max !== "") n.box = 1;
    (d.points || []).forEach(function (p) { if (hasCoord(p)) n.points++; });
    (d.regions || []).forEach(function (r) { if (r.on_map && hasCoord(r)) n.regions++; });
    (d.sites || []).forEach(function (s) { if (hasCoord(s)) n.sites++; });
    return n;
  }
  var LAYERS = [
    { k: "box", label: "遗址群范围框" },
    { k: "points", label: "文献坐标点" },
    { k: "regions", label: "空间分区代表点" },
    { k: "sites", label: "候选遗址（实测坐标）" }
  ];

  function setLayer(k, vis) {
    var g = ST.layers && ST.layers[k];
    if (!g) return;
    if (vis) { if (!map.hasLayer(g)) g.addTo(map); }
    else { if (map.hasLayer(g)) map.removeLayer(g); }
  }

  function buildPanel() {
    var n = counts();
    var h = '<div class="t">' + tr("图层面板") + "</div>";
    LAYERS.forEach(function (L) {
      h += '<label class="li"><input type="checkbox" data-lk="' + L.k + '" checked>' +
        '<span>' + tr(L.label) + '</span><span class="ct">' + n[L.k] + "</span></label>";
    });
    h += '<div class="nt">' + tr("勾掉即隐藏该类点位；数据本身不变。") + "</div>";
    R.panel.innerHTML = h;
    /* 监听只绑一次：重建面板（切语言）时不能重复绑 */
    if (R.panel.__mzBound) return;
    R.panel.__mzBound = true;
    R.panel.addEventListener("change", function (ev) {
      var t = ev.target;
      if (!t || t.getAttribute("data-lk") === null) return;
      var k = t.getAttribute("data-lk");
      on[k] = !!t.checked;
      setLayer(k, on[k]);
      if (k === "points" || k === "regions") applyYearFilter();   /* 图层开关后重算可见数 */
    });
  }

  /* ---------------- 年份刷选器（按关联文献出版年过滤点位） ---------------- */
  function yearOf(obj) {
    var ys = [];
    (obj.lits || []).forEach(function (l) {
      var y = parseInt(l && l.year, 10);
      if (!isNaN(y)) ys.push(y);
    });
    return ys;
  }
  function allYears() {
    var d = (ST.data || {});
    var lo = null, hi = null;
    function feed(o) {
      yearOf(o).forEach(function (y) {
        if (lo === null || y < lo) lo = y;
        if (hi === null || y > hi) hi = y;
      });
    }
    (d.points || []).forEach(feed);
    (d.regions || []).forEach(feed);
    if (lo === null) { lo = 1900; hi = 2100; }
    return { lo: lo, hi: hi };
  }

  function buildScrub() {
    var r = allYears();
    yr.lo = r.lo; yr.hi = r.hi;
    var h = '<div class="row">' +
      '<span>' + tr("出版年区间") + "</span>" +
      '<input type="range" id="mz-lo" min="' + r.lo + '" max="' + r.hi + '" value="' + r.lo + '">' +
      '<b id="mz-lo-v">' + r.lo + "</b><span>—</span>" +
      '<input type="range" id="mz-hi" min="' + r.lo + '" max="' + r.hi + '" value="' + r.hi + '">' +
      '<b id="mz-hi-v">' + r.hi + "</b>" +
      '<button class="btn ghost" id="mz-all">' + tr("全部") + "</button>" +
      '<span id="mz-stat"></span></div>' +
      '<div class="row"><span class="nt" id="mz-note"></span></div>';
    R.scrub.innerHTML = h;
    var lo = doc.getElementById("mz-lo"), hi = doc.getElementById("mz-hi");
    function onInput() {
      var a = parseInt(lo.value, 10), b = parseInt(hi.value, 10);
      if (a > b) { var t = a; a = b; b = t; }
      yr.lo = a; yr.hi = b;
      doc.getElementById("mz-lo-v").textContent = a;
      doc.getElementById("mz-hi-v").textContent = b;
      applyYearFilter();
    }
    lo.addEventListener("input", onInput);
    hi.addEventListener("input", onInput);
    doc.getElementById("mz-all").addEventListener("click", function () {
      lo.value = r.lo; hi.value = r.hi; onInput();
    });
    applyYearFilter();
  }

  function markerShow(m, vis) {
    if (!m) return;
    var e = m.getElement ? m.getElement() : null;
    if (e) { e.style.display = vis ? "" : "none"; return; }
    /* 元素还没生成（极端时序）→ 退回图层组增删 */
    var g = ST.layers && ST.layers.points;
    if (!g) return;
    if (vis) { if (!g.hasLayer(m)) g.addLayer(m); }
    else { if (g.hasLayer(m)) g.removeLayer(m); }
  }

  function applyYearFilter() {
    var d = (ST.data || {});
    var shown = 0, total = 0, noYear = 0;
    function deal(o, marker, groupKey) {
      if (!marker) return;
      total++;
      var ys = yearOf(o);
      var vis;
      if (!ys.length) { vis = true; noYear++; }
      else {
        vis = false;
        for (var i = 0; i < ys.length; i++) {
          if (ys[i] >= yr.lo && ys[i] <= yr.hi) { vis = true; break; }
        }
      }
      /* 图层本身被关掉的点，不用再管它显不显示 */
      if (vis && on[groupKey] === false) { /* 图层关着，显示与否由图层开关决定 */ }
      markerShow(marker, vis);
      if (vis) shown++;
    }
    (d.points || []).forEach(function (p) { deal(p, (ST.pts || {})[p.id], "points"); });
    (d.regions || []).forEach(function (r) {
      if (!r.on_map || !hasCoord(r)) return;
      deal(r, (ST.regs || {})[r.name], "regions");
    });
    var st = doc.getElementById("mz-stat");
    if (st) st.textContent = shown + " / " + total + " " + tr("个点位");
    var nt = doc.getElementById("mz-note");
    if (nt) {
      var s = tr("口径：按点位所关联文献的出版年过滤；无出版年信息的点位始终显示。");
      if (noYear) s += " · " + noYear + " " + tr("个点位") + tr("无出版年信息");
      nt.textContent = s;
    }
  }

  /* ---------------- 悬停浮层 ---------------- */
  function hoverHTML(o, kindLabel) {
    var h = '<div class="ht">' + esc(o.name || "") + "</div>";
    h += '<div class="hr">' + tr("类型") + "：" + esc(kindLabel) + "</div>";
    if (o.source) h += '<div class="hr">' + tr("出处") + "：" + esc(o.source) + "</div>";
    if (o.note) h += '<div class="hr">' + tr("说明") + "：" + esc(o.note) + "</div>";
    var ys = yearOf(o);
    if (ys.length) {
      var lo = Math.min.apply(null, ys), hi = Math.max.apply(null, ys);
      h += '<div class="hr">' + tr("出版年") + "：" + lo + (lo === hi ? "" : "–" + hi) + "</div>";
    }
    h += '<div class="hr">' + tr("关联文献") + "：" + esc(o.lit_count === undefined ? 0 : o.lit_count) + " " + tr("篇") + "</div>";
    return h;
  }
  function esc(s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }

  function bindHover() {
    var d = (ST.data || {});
    (d.points || []).forEach(function (p) {
      var m = (ST.pts || {})[p.id];
      if (!m) return;
      hoverOn(m, function () { return hoverHTML(p, p.kind || "文献坐标点"); });
    });
    (d.regions || []).forEach(function (r) {
      var m = (ST.regs || {})[r.name];
      if (!m) return;
      hoverOn(m, function () { return hoverHTML(r, "空间分区代表点（示意，±1.1 km）"); });
    });
    (d.sites || []).forEach(function (s) {
      var m = (ST.sgs || {})[s.name];
      if (!m) return;
      hoverOn(m, function () { return hoverHTML(s, "候选遗址（实测坐标）"); });
    });
  }
  function hoverOn(m, htmlFn) {
    m.on("mouseover", function (e) {
      R.hover.innerHTML = htmlFn();
      R.hover.style.display = "block";
      hoverMove(e);
    });
    m.on("mousemove", hoverMove);
    m.on("mouseout", function () { R.hover.style.display = "none"; });
  }
  function hoverMove(e) {
    if (!e || !e.originalEvent) return;
    var host = R.hover.offsetParent;
    if (!host) return;
    var rect = host.getBoundingClientRect();
    var x = e.originalEvent.clientX - rect.left + 14;
    var y = e.originalEvent.clientY - rect.top + 14;
    var w = R.hover.offsetWidth, hh = R.hover.offsetHeight;
    if (x + w > host.clientWidth) x = Math.max(0, host.clientWidth - w - 6);
    if (y + hh > host.clientHeight) y = Math.max(0, host.clientHeight - hh - 6);
    R.hover.style.left = x + "px";
    R.hover.style.top = y + "px";
  }

  /* ---------------- 地图内搜索 ---------------- */
  function buildIndex() {
    var d = (ST.data || {});
    idx = [];
    function push(type, o, name, extra) {
      var text = [name || "", extra || "", o.kind || "", o.source || ""].join(" ");
      (o.lits || []).forEach(function (l) { text += " " + (l.lit_id || "") + " " + (l.title || ""); });
      idx.push({ type: type, obj: o, name: name || "", text: text.toLowerCase() });
    }
    (d.points || []).forEach(function (p) { if (hasCoord(p)) push("point", p, p.name); });
    (d.regions || []).forEach(function (r) { if (r.on_map && hasCoord(r)) push("region", r, r.name); });
    (d.sites || []).forEach(function (s) { if (hasCoord(s)) push("site", s, s.name, s.note || ""); });
  }

  function doSearch() {
    var q = (R.q.value || "").replace(/^\s+|\s+$/g, "");
    var msg = doc.getElementById("mz-msg");
    if (!q) { if (msg) msg.textContent = ""; return; }
    var ql = q.toLowerCase();
    var hits = [];
    for (var i = 0; i < idx.length; i++) {
      if (idx[i].text.indexOf(ql) >= 0) hits.push(idx[i]);
    }
    /* 排序：名称完全相同 > 名称开头 > 名称包含 > 仅正文命中 */
    hits.sort(function (a, b) { return score(b) - score(a); });
    function score(it) {
      var n = it.name.toLowerCase();
      if (n === ql) return 3;
      if (n.indexOf(ql) === 0) return 2;
      if (n.indexOf(ql) >= 0) return 1;
      return 0;
    }
    if (!hits.length) { if (msg) msg.textContent = tr("无匹配点位"); return; }
    if (msg) msg.textContent = hits.length + " " + tr("个匹配点位");
    var it = hits[0], o = it.obj;
    if (it.type === "point") {
      map.setView([o.lat, o.lon], 13, { animate: false });
      if (global.mapstShowPoint) global.mapstShowPoint(o);
    } else if (it.type === "region") {
      map.setView([o.lat, o.lon], 12, { animate: false });
      if (global.mapstPickRegion) global.mapstPickRegion(o.name);
    } else {
      map.setView([o.lat, o.lon], 13, { animate: false });
      if (global.mapstShowSite) global.mapstShowSite(o);
    }
  }

  /* ---------------- 相机复位 ---------------- */
  function resetView() {
    if (ST.mode === "sat" && ST.satMeta && global.litmapSatBounds && global.L) {
      try {
        map.fitBounds(global.L.latLngBounds(global.litmapSatBounds(ST.satMeta)), { animate: false });
        return;
      } catch (e) { /* 退回矢量视野 */ }
    }
    map.setView([30.52, 120.05], 9, { animate: false });
  }

  /* ---------------- 工具条接线 ---------------- */
  function bindTools() {
    R.layersBtn.addEventListener("click", function () {
      var open = R.panel.className.indexOf("open") >= 0;
      R.panel.className = open ? R.panel.className.replace(/\s*\bopen\b/g, "") : R.panel.className + " open";
      R.layersBtn.className = open ? R.layersBtn.className.replace(/\s*\bon\b/g, "") : R.layersBtn.className + " on";
    });
    R.scrubBtn.addEventListener("click", function () {
      var open = R.scrub.className.indexOf("open") >= 0;
      R.scrub.className = open ? R.scrub.className.replace(/\s*\bopen\b/g, "") : R.scrub.className + " open";
      R.scrubBtn.className = open ? R.scrubBtn.className.replace(/\s*\bon\b/g, "") : R.scrubBtn.className + " on";
      var cont = map.getContainer();
      if (cont) cont.className = open ? cont.className.replace(/\s*\bmz-scrub\b/g, "") : cont.className + " mz-scrub";
    });
    R.reset.addEventListener("click", resetView);
    R.go.addEventListener("click", doSearch);
    R.q.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.keyCode === 13) { doSearch(); if (e.preventDefault) e.preventDefault(); }
    });
    /* 点面板外收起面板 */
    doc.addEventListener("click", function (ev) {
      var t = ev.target;
      if (R.panel.className.indexOf("open") < 0) return;
      if (R.panel.contains(t) || R.layersBtn.contains(t) || R.scrubBtn.contains(t)) return;
      R.panel.className = R.panel.className.replace(/\s*\bopen\b/g, "");
      R.layersBtn.className = R.layersBtn.className.replace(/\s*\bon\b/g, "");
    });
  }

  /* ---------------- 切语言：重建面板与刷选器 ---------------- */
  function rebuild() {
    if (!R.panel) return;
    buildPanel();
    buildScrub();
    /* 图层开关状态要重新落一遍（重建时 innerHTML 换掉了勾选框） */
    for (var k in on) {
      if (Object.prototype.hasOwnProperty.call(on, k)) setLayer(k, on[k]);
    }
    applyYearFilter();
  }
  function watchLang() {
    if (!global.MutationObserver) return;
    var mo = new global.MutationObserver(function () { try { rebuild(); } catch (e) { } });
    mo.observe(doc.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
  }

  /* ---------------- 初始化 ---------------- */
  function init() {
    ST = global.MAPST;
    if (!ST || !ST.map) return;
    map = ST.map;
    R.wrap = doc.getElementById("mapatlas-wrap");
    R.q = doc.getElementById("mapatlas-q");
    R.go = doc.getElementById("mapatlas-go");
    R.layersBtn = doc.getElementById("mapatlas-layers");
    R.panel = doc.getElementById("mapatlas-panel");
    R.scrubBtn = doc.getElementById("mapatlas-scrubbtn");
    R.scrub = doc.getElementById("mapatlas-scrub");
    R.reset = doc.getElementById("mapatlas-reset");
    R.hover = doc.getElementById("mapatlas-hover");
    if (!R.wrap || !R.panel || !R.scrub || !R.hover || !R.q || !R.go) return;
    /* 换页重建了地图块 → 状态重置 */
    on = { box: true, points: true, regions: true, sites: true };
    yr.lo = null; yr.hi = null;
    /* 搜索提示位 */
    if (!doc.getElementById("mz-msg")) {
      var m = el("span", "small muted");
      m.id = "mz-msg";
      R.q.parentNode.appendChild(m);
    }
    addScale();
    buildPanel();
    buildScrub();
    buildIndex();
    bindHover();
    bindTools();
    watchLang();
  }

  global.MAPATLAS = { init: init, reset: resetView, search: doSearch };

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", function () { try { init(); } catch (e) { } });
  }
})(typeof window !== "undefined" ? window : this);
