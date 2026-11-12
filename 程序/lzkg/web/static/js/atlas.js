/* 良渚时序（BP15 首页 · 阶段 1 样例版）
 *   形态借「江山时序」的交互骨架：整幅地图 + 左上 HUD + 右侧叙事栏 + 底部双轴时间轴。
 *   ★ 只借骨架：代码、数据、底图全部自写／复用本站本地文件（零外链、零 CDN、断网可用）。
 *   ★ 地图是**独立 Leaflet 实例**，不碰统计页那套单例（MAPST）——两个实例各管各的图层。
 *   纪律：纯 ES5；界面文案走全局 i18n 词典（键＝中文原文），元数据（题名/分区名）不翻。
 */
(function (global) {
  "use strict";
  var doc = global.document;

  /* 8 档分期带固定配色（按档位固定，不随篇数变化） */
  var BAND_COLOR = {
    PRE: "#7C8FA6", EARLY_MID: "#5B8FA8", MID: "#3E9A93", MID_LATE: "#6FA85A",
    LATE: "#C08A3E", SPAN_ALL: "#8E7BB0", GENERAL: "#9AA0A6", CONTEMP: "#C05B5B",
    OTHER: "#B0B7BD"
  };

  var A = {
    data: null,          /* /api/atlas 的 data */
    map: null,           /* 本页自己的 Leaflet 实例 */
    marks: {},           /* 分区名 → marker */
    sel: { year: null, period: "", region: "", q: "" },
    playing: false,
    timer: null,
    keyHandler: null,
    sg: null,            /* 遗址群范围（取景用） */
    satMeta: null,       /* 本地卫星瓦片清单；无瓦片（公开包）时为 null */
    satLayer: null,      /* 卫星瓦片图层 */
    baseLayers: []       /* 矢量底图图层（切卫星时要整批撤掉，否则多边形会盖住影像） */
  };

  /* ── 基础工具 ────────────────────────────────────────────── */
  function T(zh) { var f = global.LZ_tr; return f ? f(zh) : zh; }
  function TF(key, vars) {
    var s = T(key), k;
    for (k in vars) {
      if (!Object.prototype.hasOwnProperty.call(vars, k)) continue;
      s = s.split("{" + k + "}").join(vars[k]);
    }
    return s;
  }
  function esc(s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function svgEl(tag, attrs) {
    var n = doc.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }
  function api(path) {
    if (global.api) return global.api(path);
    return fetch(path).then(function (r) { return r.json(); });
  }
  /* 分区名有带编码（R-0101 良渚古城）与不带（良渚古城）两种写法，比较前统一去掉编码 */
  function regionKey(s) {
    return String(s === undefined || s === null ? "" : s).replace(/^[A-Za-z]+-\d+\s*/, "").trim();
  }

  /* ── 数据取值 ────────────────────────────────────────────── */
  function bands() { return (A.data && A.data.bands) || []; }
  function bandByKey(k) {
    var b = null;
    bands().forEach(function (x) { if (x.key === k) b = x; });
    return b;
  }
  function bandName(k) { var b = bandByKey(k); return b ? b.name : k; }
  /* 跳文献页用的筛选值＝该档在登记表里的**原文**（后端已挑好能命中整档的那一条） */
  function bandFilter(k) { var b = bandByKey(k); return b ? (b.filter_value || b.name) : ""; }

  function filtered() {
    var d = A.data || {}, sel = A.sel;
    var q = (sel.q || "").toLowerCase();
    var out = [];
    (d.papers || []).forEach(function (p) {
      /* 年份游标：无年份的文献无法判断先后 → 一律保留（界面里写明这个口径） */
      if (sel.year !== null && p.y !== null && p.y !== undefined && p.y > sel.year) return;
      if (sel.period && p.period !== sel.period) return;
      if (sel.region && regionKey(p.region) !== regionKey(sel.region)) return;
      if (q) {
        var hay = ((p.title || "") + " " + (p.lit_id || "") + " " +
                   (p.doc_type || "") + " " + (p.year || "")).toLowerCase();
        if (hay.indexOf(q) < 0) return;
      }
      out.push(p);
    });
    return out;
  }

  /* ── 地图（独立实例；底图同源 /basemap/*.geojson，本地文件、零联网） ── */
  function baseFetch(name) {
    if (global.mapstFetch) return global.mapstFetch(name);
    return fetch("/basemap/" + name).then(function (r) { return r.json(); });
  }
  function hasCoord(o) {
    if (!o || o.lat === null || o.lat === undefined || o.lat === "" ||
        o.lon === null || o.lon === undefined || o.lon === "") return false;
    return isFinite(Number(o.lat)) && isFinite(Number(o.lon));
  }

  function initMap() {
    var host = doc.getElementById("atlas-map");
    if (!host) return;
    if (!global.L) {
      host.innerHTML = '<div class="notice bad" style="margin:12px">' +
        T("Leaflet 未加载，地图不可用。") + "</div>";
      return;
    }
    destroyMap();
    /* 中心取遗址群的**真坐标**（数据文件里的 center_lat/center_lon），不写死 */
    var sg = ((A.data && A.data.map) || {}).site_group || {};
    var cl = sg.center_lat, co = sg.center_lon;
    if (cl === undefined || cl === null || cl === "" ||
        co === undefined || co === null || co === "") { cl = 30.52; co = 120.05; }
    /* 良渚区域尺度：minZoom 8 → 永远不画国界（也就没有边界争议问题） */
    A.map = global.L.map(host, {
      center: [Number(cl), Number(co)], zoom: 12, minZoom: 8, maxZoom: 14,
      zoomControl: true, attributionControl: true
    });
    A.sg = sg;
    /* 比例尺：让「多大」看得见 */
    try { global.L.control.scale({ metric: true, imperial: false, position: "bottomright" }).addTo(A.map); }
    catch (e) { /* 比例尺挂不上不影响其它功能 */ }
    drawBase();
    drawOverlays();
    /* 布局稳定后再取景：容器宽度没算出来时 fitBounds 会算出错的缩放 */
    setTimeout(function () {
      if (!A.map) return;
      A.map.invalidateSize();
      fitSite();
    }, 150);
  }
  /* 取景：按遗址群的真实范围框（库里没有就保持当前视图） */
  function fitSite() {
    var sg = A.sg || {};
    if (!A.map) return;
    if (sg.lat_min === undefined || sg.lat_min === null || sg.lon_min === undefined ||
        sg.lon_min === null || sg.lat_max === undefined || sg.lat_max === null ||
        sg.lon_max === undefined || sg.lon_max === null) return;
    try {
      A.map.fitBounds([[sg.lat_min, sg.lon_min], [sg.lat_max, sg.lon_max]],
        { padding: [56, 56], maxZoom: 14, animate: false });
    } catch (e) { /* 取景失败就用上面的中心 */ }
  }
  /* 底图模式：矢量 / 卫星影像（本地瓦片；公开包没带瓦片 → 切换条整条不出现） */
  function setMode(mode) {
    var bar = doc.getElementById("atlas-mode");
    if (bar) {
      var bs = bar.querySelectorAll("[data-am]");
      for (var i = 0; i < bs.length; i++) {
        bs[i].className = "segbtn" + (bs[i].getAttribute("data-am") === mode ? " on" : "");
      }
    }
    if (!A.map) return;
    if (mode === "sat") {
      if (!A.satMeta) return;
      if (!A.satLayer) {
        try { A.satLayer = global.litmapSatLayer(A.map, A.satMeta); }
        catch (e) { A.satLayer = null; }
      }
      if (!A.satLayer) return;
      /* 撤掉矢量底图（否则多边形盖住影像） */
      A.baseLayers.forEach(function (l) { try { A.map.removeLayer(l); } catch (e) {} });
      A.satLayer.addTo(A.map);
      var b = A.satMeta.bbox || {};
      try {
        A.map.fitBounds([[Number(b.lat_min), Number(b.lon_min)],
                         [Number(b.lat_max), Number(b.lon_max)]],
          { padding: [16, 16], maxZoom: 15, animate: false });
      } catch (e) { /* 取景失败就保持当前视图 */ }
    } else {
      if (A.satLayer) {
        try { A.map.removeLayer(A.satLayer); } catch (e) { /* 忽略 */ }
        A.satLayer = null;
      }
      A.baseLayers.forEach(function (l) { if (A.map) A.map.addLayer(l); });
      fitSite();
    }
  }
  function destroyMap() {
    if (A.map) { try { A.map.remove(); } catch (e) { /* 忽略 */ } }
    A.map = null;
    A.marks = {};
    A.baseLayers = [];
  }

  /* 矢量底图统一走这里：切到卫星影像时要把它们整批撤掉 ——
     Leaflet 的矢量多边形在 overlayPane，瓦片在 tilePane，**多边形永远盖在瓦片上面**。 */
  function addBase(layer) {
    if (!layer) return;
    A.baseLayers.push(layer);
    if (A.map) A.map.addLayer(layer);
  }
  function drawBase() {
    var m = A.map, C = global.MAP_COLORS || {};
    if (!m) return;
    var land = C.land || "#F4F6F8", border = C.border || "#C2CBD3";
    var water = C.water || "#DCEAF4", river = C.river || "#9CC3DE";
    /* 四个文件并行取回，再**按固定次序**叠加（次序乱了就压错层） */
    Promise.all([
      baseFetch("ne_50m_ocean.geojson"),
      baseFetch("ne_50m_admin_1_states_provinces.geojson"),
      baseFetch("ne_50m_lakes.geojson"),
      baseFetch("osm_qiantang_yuhang.geojson")
    ]).then(function (res) {
      if (!A.map) return;
      var ocean = res[0], prov = res[1], lakes = res[2], rivers = res[3];
      /* 1 海面 */
      addBase(global.L.geoJSON(ocean, {
        style: { color: water, weight: 0.8, fillColor: water, fillOpacity: 1 }
      }));
      /* 2 31 省（admin_1 里 adm0_a3 === 'CHN' 的要素）—— 只作陆地参照。
         ★ 绝不画 admin_0 里 CHN 那个国界多边形；台湾在 admin_0 里，
           本页是良渚区域尺度（z8–z14），画不到也不必画。 */
      var ps = [], zj = [];
      (prov.features || []).forEach(function (f) {
        var pr = f.properties || {};
        if (pr.adm0_a3 !== "CHN") return;
        ps.push(f);
        if (String(pr.name_zh || "").indexOf("浙江") === 0) zj.push(f);
      });
      if (ps.length) {
        addBase(global.L.geoJSON(ps, {
          style: { color: border, weight: 0.7, fillColor: land, fillOpacity: 1 }
        }));
      }
      if (zj.length) {
        addBase(global.L.geoJSON(zj, {
          style: { color: C.brand || "#4C78A8", weight: 1.4, fillColor: "#E4EDF5", fillOpacity: 1 }
        }));
      }
      /* 3 太湖 */
      var fs = [];
      (lakes.features || []).forEach(function (f) {
        var pr = f.properties || {};
        var n = String(pr.name || pr.NAME || pr.name_en || "");
        if (n.toLowerCase().indexOf("tai hu") >= 0) fs.push(f);
      });
      if (fs.length) {
        var ly = global.L.geoJSON(fs, {
          style: { color: river, weight: 0.8, fillColor: water, fillOpacity: 1 }
        });
        ly.bindTooltip("太湖", { permanent: true, direction: "center", className: "lz-tip" });
        addBase(ly);
      }
      /* 4 钱塘江水系 + 余杭区界 */
      var riv = [], bnd = [];
      /* ★ 这份 OSM 数据的属性键是 waterway / boundary（不是 kind）——
         按 kind 筛会一个都匹配不上（钱塘江与街道界就都不画了）。 */
      (rivers.features || []).forEach(function (f) {
        var pr = f.properties || {};
        if (String(pr.waterway || "") === "river") riv.push(f);
        else if (pr.boundary) bnd.push(f);
      });
      if (riv.length) addBase(global.L.geoJSON(riv, {
        style: { color: river, weight: 2.2, fill: false }
      }));
      if (bnd.length) addBase(global.L.geoJSON(bnd, {
        style: { color: border, weight: 1.3, dashArray: "4 3", fill: false }
      }));
    }).catch(function () {});
  }

  function drawOverlays() {
    var m = A.map, d = (A.data && A.data.map) || {};
    if (!m) return;
    /* 1 良渚遗址群范围框（真实范围，出处见数据文件） */
    var sg = d.site_group || {};
    if (sg.lat_min !== undefined && sg.lat_min !== null && sg.lon_min !== undefined &&
        sg.lon_min !== null && sg.lat_max !== undefined && sg.lat_max !== null &&
        sg.lon_max !== undefined && sg.lon_max !== null) {
      var box = global.L.rectangle(
        [[sg.lat_min, sg.lon_min], [sg.lat_max, sg.lon_max]],
        { color: "#C0392B", weight: 1.4, dashArray: "5 4", fillOpacity: 0.06 }
      );
      box.bindTooltip(sg.name || "良渚遗址群", { direction: "top", className: "lz-tip" });
      box.addTo(m);
    }
    /* 2 空间分区代表点（区域统一坐标，±1.1 km 示意） */
    (d.regions || []).forEach(function (r) {
      if (!r.on_map || !hasCoord(r)) return;
      var mk = global.L.marker([r.lat, r.lon], {
        icon: global.mapstRegionIcon ? global.mapstRegionIcon(false) : undefined,
        title: r.name
      });
      mk.on("click", function () { setRegion(r.name); });
      mk.addTo(m);
      A.marks[r.name] = mk;
    });
    /* 3 文献坐标点 */
    (d.points || []).forEach(function (p) {
      if (!hasCoord(p)) return;
      var mk = global.L.marker([p.lat, p.lon], {
        icon: global.mapstPointIcon ? global.mapstPointIcon() : undefined,
        title: p.name
      });
      mk.bindTooltip(p.name + "（" + (p.lit_count || 0) + " 篇）",
        { direction: "top", className: "lz-tip" });
      mk.addTo(m);
    });
    /* 4 候选遗址（只有实测坐标的才画） */
    (d.sites || []).forEach(function (s) {
      if (!hasCoord(s)) return;
      var mk = global.L.marker([s.lat, s.lon], {
        icon: global.mapstSiteIcon ? global.mapstSiteIcon() : undefined,
        title: s.name
      });
      mk.bindTooltip(s.name, { direction: "top", className: "lz-tip" });
      mk.addTo(m);
    });
  }

  /* ── 时间轴（双轴：上＝出版年刻度，下＝分期带） ───────────── */
  function drawTimeline() {
    var box = doc.getElementById("atlas-tl");
    if (!box || !A.data) return;
    var d = A.data, meta = d.meta || {}, sel = A.sel;
    var W = Math.max(360, box.clientWidth || 900);
    var H = 214;
    var ml = 46, mr = 20, iw = W - ml - mr;
    var yTop = 30, yBase = 108;      /* 年份柱区 */
    var bTop = 132, bH = 60;        /* 分期带区 */
    var ymin = meta.year_min, ymax = meta.year_max;

    function px(y) {
      if (ymax === ymin) return ml + iw / 2;
      return ml + (y - ymin) / (ymax - ymin) * iw;
    }
    function yearAt(x) {
      var t = (x - ml) / iw;
      if (t < 0) t = 0;
      if (t > 1) t = 1;
      return Math.round(ymin + t * (ymax - ymin));
    }

    var svg = svgEl("svg", {
      "class": "atlas-svg", width: W, height: H,
      viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": T("良渚时序双轴时间轴：上轴为文献出版年，下轴为良渚分期带")
    });

    /* 年份柱：每一年一根细柱，高＝该年文献数 */
    var years = d.years || [], maxN = 0;
    years.forEach(function (y) { if (y.n > maxN) maxN = y.n; });
    years.forEach(function (y) {
      var x = px(y.year);
      var h = maxN ? (y.n / maxN) * 56 : 0;
      var on = (sel.year === null || y.year <= sel.year);
      svg.appendChild(svgEl("rect", {
        x: x - 1.7, y: yBase - Math.max(h, 0.8), width: 3.4, height: Math.max(h, 0.8),
        fill: on ? "#5B7F9C" : "#D2DBE1", "data-year": y.year
      }));
    });
    /* 年份刻度 */
    svg.appendChild(svgEl("line", { x1: ml, y1: yBase, x2: ml + iw, y2: yBase,
      stroke: "#C6D0D7", "stroke-width": 1 }));
    for (var y = ymin; y <= ymax; y++) {
      if (y % 5 !== 0 && y !== ymin && y !== ymax) continue;
      /* 首尾年若紧挨着 5 的倍数就别再标（1984/1985、2025/2026 会叠字） */
      if (y === ymin && (ymin + 1) % 5 === 0) continue;
      if (y === ymax && (ymax - 1) % 5 === 0) continue;
      var xt = px(y);
      svg.appendChild(svgEl("line", { x1: xt, y1: yBase, x2: xt, y2: yBase + 4,
        stroke: "#B9C4CC", "stroke-width": 1 }));
      var tt = svgEl("text", { x: xt, y: yBase + 16, "text-anchor": "middle",
        "font-size": 10, fill: "#7B8A95" });
      tt.textContent = String(y);
      svg.appendChild(tt);
    }
    /* 游标（截至 Y 年） */
    if (sel.year !== null) {
      var cx = px(sel.year);
      svg.appendChild(svgEl("line", { x1: cx, y1: yTop - 12, x2: cx, y2: yBase,
        stroke: "#C0392B", "stroke-width": 1.6 }));
      svg.appendChild(svgEl("circle", { cx: cx, cy: yTop - 13, r: 5,
        fill: "#C0392B", stroke: "#fff", "stroke-width": 1.5 }));
    }

    /* 分期带（8 段等宽） */
    var bs = bands(), bw = iw / Math.max(1, bs.length);
    bs.forEach(function (b, i) {
      var x = ml + i * bw;
      var on = (sel.period === b.key);
      var g = svgEl("g", { "class": "atlas-band", tabindex: "0", role: "button",
        "aria-label": b.name + " " + b.n + " " + T("篇") });
      var r = svgEl("rect", {
        x: x + 1.5, y: bTop, width: Math.max(bw - 3, 1), height: bH, rx: 4,
        fill: BAND_COLOR[b.key] || "#9AA0A6",
        "fill-opacity": on ? 0.96 : 0.4,
        stroke: on ? "#33424D" : "#FFFFFF", "stroke-width": on ? 2 : 1
      });
      /* ★ 事件绑在 **g** 上，不绑在 rect 上：分段名与篇数是 <text>，
         盖在 rect 上面，点到文字时事件不会冒泡到 rect → 点了没反应。 */
      g.addEventListener("mousedown", function (e) { if (e.stopPropagation) e.stopPropagation(); });
      g.addEventListener("click", function (e) {
        if (e.stopPropagation) e.stopPropagation();
        togglePeriod(b.key);
      });
      g.appendChild(r);
      var nm = String(b.name || "");
      var cut = Math.ceil(nm.length / 2);
      var t1 = svgEl("text", { x: x + bw / 2, y: bTop + 17, "text-anchor": "middle",
        "font-size": 11, fill: "#24313B" });
      t1.textContent = nm.length > 5 ? nm.slice(0, cut) : nm;
      g.appendChild(t1);
      if (nm.length > 5) {
        var t2 = svgEl("text", { x: x + bw / 2, y: bTop + 30, "text-anchor": "middle",
          "font-size": 11, fill: "#24313B" });
        t2.textContent = nm.slice(cut);
        g.appendChild(t2);
      }
      var t3 = svgEl("text", { x: x + bw / 2, y: bTop + bH - 7, "text-anchor": "middle",
        "font-size": 10, fill: "#5B6B78" });
      t3.textContent = b.n + " " + T("篇");
      g.appendChild(t3);
      svg.appendChild(g);
    });

    /* 拖动游标 */
    svg.addEventListener("mousedown", function (ev) {
      if (bandZone(ev, svg, bTop)) return;
      setYear(yearAt(svgX(ev, svg)));
      drag(ev, svg);
    });

    box.innerHTML = "";
    box.appendChild(svg);
  }
  /* SVG 坐标换算（viewBox 与实际像素可能差一个缩放） */
  function svgX(ev, svg) {
    var rect = svg.getBoundingClientRect();
    var vb = svg.viewBox && svg.viewBox.baseVal;
    var w = vb && vb.width ? vb.width : rect.width;
    var k = rect.width / w;
    return ((ev.clientX - rect.left) / (k || 1));
  }
  function svgY(ev, svg) {
    var rect = svg.getBoundingClientRect();
    var vb = svg.viewBox && svg.viewBox.baseVal;
    var h = vb && vb.height ? vb.height : rect.height;
    var k = rect.height / h;
    return ((ev.clientY - rect.top) / (k || 1));
  }
  function bandZone(ev, svg, bTop) { return svgY(ev, svg) > bTop - 14; }

  function drag(ev, svg) {
    function move(e) { setYear(yearAt(svgX(e, svg))); }
    function up() {
      doc.removeEventListener("mousemove", move);
      doc.removeEventListener("mouseup", up);
    }
    doc.addEventListener("mousemove", move);
    doc.addEventListener("mouseup", up);
    if (ev.preventDefault) ev.preventDefault();
  }

  /* ── 状态操作 ────────────────────────────────────────────── */
  function setYear(y) {
    var meta = (A.data && A.data.meta) || {};
    var lo = meta.year_min, hi = meta.year_max;
    if (y === null) { A.sel.year = null; paint(); return; }
    y = Math.max(lo, Math.min(hi, Math.round(y)));
    if (y === A.sel.year) return;
    A.sel.year = y;
    paint();
  }
  function togglePeriod(key) {
    A.sel.period = (A.sel.period === key) ? "" : key;
    paint();
  }
  function setRegion(name) {
    A.sel.region = (A.sel.region === regionKey(name)) ? "" : regionKey(name);
    paint();
  }
  function clearSel() {
    A.sel.period = "";
    A.sel.region = "";
    A.sel.q = "";
    var q = doc.getElementById("atlas-q");
    if (q) q.value = "";
    paint();
  }

  /* 播放：逐年推进游标 */
  function togglePlay() {
    if (A.playing) { stopPlay(); return; }
    var meta = (A.data && A.data.meta) || {};
    A.playing = true;
    A.timer = setInterval(function () {
      var y = A.sel.year === null ? (meta.year_min || 0) : A.sel.year;
      if (y >= (meta.year_max || 0)) { stopPlay(); return; }
      setYear(y + 1);
    }, 140);
    paint();
  }
  function stopPlay() {
    A.playing = false;
    if (A.timer) { clearInterval(A.timer); A.timer = null; }
    paint();
  }

  /* ── 右侧叙事栏 ──────────────────────────────────────────── */
  function drawSide() {
    var box = doc.getElementById("atlas-side");
    if (!box || !A.data) return;
    var d = A.data, meta = d.meta || {}, sel = A.sel;
    var list = filtered();
    var h = "";

    h += '<div class="atlas-h"><b>' + T("当前状态") + "</b></div>";
    h += '<div class="atlas-state">';
    h += '<span class="chip">' + (sel.year === null ? T("全部年份") :
      TF("截至 {y} 年", { y: sel.year })) + "</span>";
    h += '<span class="chip">' + (sel.period ? bandName(sel.period) : T("全部分期")) + "</span>";
    h += '<span class="chip">' + (sel.region ? sel.region : T("全部分区")) + "</span>";
    if (sel.q) h += '<span class="chip">' + esc(sel.q) + "</span>";
    h += "</div>";
    h += '<div class="atlas-count">' + TF("命中 {n} 篇，共 {m} 篇", { n: list.length, m: meta.papers }) + "</div>";

    if (sel.period) h += '<button class="btn" id="atlas-jump-period">' + T("在文献页按该分期筛选") + "</button>";
    if (sel.region) h += '<button class="btn" id="atlas-jump-region">' + T("在文献页按该分区筛选") + "</button>";
    if (sel.period || sel.region || sel.q) h += '<button class="btn ghost" id="atlas-clear">' + T("清除筛选") + "</button>";

    /* 分区 chip：6 个上图分区挤在 ±1.1 km 内，点地图点不中 → 这里给可靠选择 */
    var mregs = ((A.data && A.data.map) || {}).regions || [];
    if (mregs.length) {
      h += '<div class="atlas-h"><b>' + T("空间分区") + "</b></div>";
      h += '<div class="atlas-chips">';
      mregs.forEach(function (r) {
        var on = (A.sel.region === regionKey(r.name));
        h += '<button class="achip' + (on ? " on" : "") + '" data-reg="' + esc(r.name) + '">' +
          esc(r.name) + " " + (r.lit_count || 0) + "</button>";
      });
      h += "</div>";
    }
    h += '<div class="atlas-h"><b>' + T("命中文献") + "</b></div>";
    if (!list.length) {
      h += '<div class="small muted">' + T("无结果") + "</div>";
    } else {
      h += '<div class="lits">';
      list.forEach(function (p) {
        h += '<a class="lit" href="#/lit/' + esc(p.lit_id) + '">' +
          '<span class="ttl">' + esc(p.title) + "</span>" +
          '<span class="yr">' + esc(p.year || "—") + "</span>" +
          '<span class="yr">' + esc(bandName(p.period)) + "</span>" +
          '<span class="yr">' + TF("图版 {n} 张", { n: p.img_count }) + "</span></a>";
      });
      h += "</div>";
    }

    h += '<div class="atlas-h"><b>' + T("口径与限制") + "</b></div>";
    h += '<ul class="atlas-notes">';
    h += "<li>" + T("上轴＝出版年（不是良渚分期年）") + "</li>";
    h += "<li>" + T("下轴＝良渚分期带（等宽档位，非年代跨度）") + "</li>";
    h += "<li>" + T("地图点位：分区代表点为区域统一坐标（±1.1 km 示意），不是遗址实测坐标。") + "</li>";
    h += "<li>" + T("本图为良渚区域尺度；不画国界与省界，边界不作官方界线依据。") + "</li>";
    h += "<li>" + TF("其中 {n} 篇无出版年信息（不参与年份筛选）", { n: meta.no_year }) + "</li>";
    h += "<li>" + T("年份区间无法用文献页表达，跳转只带分期或分区。") + "</li>";
    h += "</ul>";

    box.innerHTML = h;

    var jp = doc.getElementById("atlas-jump-period");
    if (jp) jp.addEventListener("click", function () {
      if (global.mapstGoFilter) global.mapstGoFilter({ period: bandFilter(sel.period) });
    });
    var jr = doc.getElementById("atlas-jump-region");
    if (jr) jr.addEventListener("click", function () {
      if (global.mapstGoFilter) global.mapstGoFilter({ region: sel.region });
    });
    var cl = doc.getElementById("atlas-clear");
    if (cl) cl.addEventListener("click", clearSel);
    var chips = box.querySelectorAll(".achip");
    for (var ci = 0; ci < chips.length; ci++) {
      chips[ci].addEventListener("click", function () {
        var nm = this.getAttribute("data-reg");
        if (nm) setRegion(nm);
      });
    }
  }

  /* 地图上分区点的提示：显示当前筛选命中数 */
  function refreshMapTips(list) {
    var d = (A.data && A.data.map) || {};
    var cnt = {};
    list.forEach(function (p) {
      var k = regionKey(p.region);
      if (k) cnt[k] = (cnt[k] || 0) + 1;
    });
    (d.regions || []).forEach(function (r) {
      var mk = A.marks[r.name];
      if (!mk) return;
      var n = cnt[regionKey(r.name)] || 0;
      mk.setTooltipContent(r.name + "（" + n + " 篇）");
      if (global.mapstRegionIcon) {
        mk.setIcon(global.mapstRegionIcon(A.sel.region === regionKey(r.name)));
      }
    });
  }

  function paint() {
    var list = filtered();
    drawTimeline();
    drawSide();
    refreshMapTips(list);
    var meta = (A.data && A.data.meta) || {};
    var note = doc.getElementById("atlas-hud-note");
    if (note) {
      note.textContent = TF("截至 {y} 年：{n} 篇", {
        y: A.sel.year === null ? T("全部年份") : A.sel.year, n: list.length
      }) + "　" + TF("全库 {m} 篇", { m: meta.papers });
    }
    var play = doc.getElementById("atlas-play");
    if (play) play.textContent = A.playing ? "❚❚ " + T("暂停") : "▶ " + T("播放");
    paintChrome();
  }
  /* 界面文案刷新（切语言时只改文字，不重建地图实例） */
  function paintChrome() {
    var ttl = doc.getElementById("atlas-title");
    if (ttl) ttl.textContent = T("良渚时序");
    var sub = doc.getElementById("atlas-sub");
    if (sub) sub.textContent = T("出版年 × 良渚分期 × 空间分区");
    var qin = doc.getElementById("atlas-q");
    if (qin) qin.setAttribute("placeholder", T("搜索题名 / 文献编号"));
    var rst = doc.getElementById("atlas-reset");
    if (rst) rst.textContent = T("复位");
    var th = doc.getElementById("atlas-tlhead");
    if (th) {
      th.textContent = T("上轴＝出版年（不是良渚分期年）") + "　|　" +
        T("下轴＝良渚分期带（等宽档位，非年代跨度）") + "　|　" +
        T("点年份＝设定「截至该年」；点分期带＝按该期筛选；点地图分区＝按该分区筛选。");
    }
    var mbar = doc.getElementById("atlas-mode");
    if (mbar) {
      var bs = mbar.querySelectorAll("[data-am]");
      for (var i = 0; i < bs.length; i++) {
        bs[i].textContent = (bs[i].getAttribute("data-am") === "sat") ? T("卫星影像") : T("矢量");
      }
    }
  }

  /* ── 切语言：重画本页（不重建地图） ── */
  var lastLang = null;
  function curLang() {
    var d = doc.documentElement;
    return (d && d.getAttribute("data-lang")) || "zh";
  }
  function onLang() {
    var l = curLang();
    if (lastLang === null) { lastLang = l; return; }
    if (l === lastLang) return;
    lastLang = l;
    if (A.data) paint();
  }
  if (global.MutationObserver && doc.documentElement) {
    var mo = new global.MutationObserver(function () { onLang(); });
    mo.observe(doc.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
  }

  /* ── 键盘：←→ 调年，Space 播放，Esc 清除 ── */
  function onKey(e) {
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    var meta = (A.data && A.data.meta) || {};
    if (e.key === "ArrowLeft") {
      setYear((A.sel.year === null ? meta.year_max : A.sel.year) - 1);
      if (e.preventDefault) e.preventDefault();
    } else if (e.key === "ArrowRight") {
      setYear((A.sel.year === null ? meta.year_min : A.sel.year) + 1);
      if (e.preventDefault) e.preventDefault();
    } else if (e.key === " " || e.keyCode === 32) {
      togglePlay();
      if (e.preventDefault) e.preventDefault();
    } else if (e.key === "Escape") {
      clearSel();
    }
  }

  /* ── 页面装配 ────────────────────────────────────────────── */
  function build() {
    var box = doc.getElementById("w-atlas");
    if (!box) return;
    var meta = (A.data && A.data.meta) || {};
    box.innerHTML =
      '<div class="atlas">' +
        '<div class="atlas-main">' +
          '<div class="atlas-mapwrap">' +
            '<div class="atlas-hud">' +
              '<div class="atlas-hud-t" id="atlas-title">' + T("良渚时序") + "</div>" +
              '<div class="atlas-hud-s" id="atlas-sub"></div>' +
              '<input type="text" id="atlas-q" placeholder="' + T("搜索题名 / 文献编号") + '">' +
              '<div class="atlas-hud-b">' +
                '<button class="btn" id="atlas-play">▶ ' + T("播放") + "</button>" +
                '<button class="btn ghost" id="atlas-reset">' + T("复位") + "</button>" +
              "</div>" +
              '<div class="atlas-hud-b" id="atlas-mode" hidden><span class="seg">' +
                '<button class="segbtn on" data-am="vec">' + T("矢量") + "</button>" +
                '<button class="segbtn" data-am="sat">' + T("卫星影像") + "</button>" +
              "</span></div>" +
              '<div class="atlas-hud-n small muted" id="atlas-hud-note"></div>' +
            "</div>" +
            '<div id="atlas-map"></div>' +
          "</div>" +
          '<aside class="atlas-side" id="atlas-side"></aside>' +
        "</div>" +
        '<div class="atlas-tl-head small muted" id="atlas-tlhead">' +
          T("上轴＝出版年（不是良渚分期年）") + "　|　" +
          T("下轴＝良渚分期带（等宽档位，非年代跨度）") + "　|　" +
          T("点年份＝设定「截至该年」；点分期带＝按该期筛选；点地图分区＝按该分区筛选。") +
        "</div>" +
        '<div class="atlas-tl" id="atlas-tl"></div>' +
      "</div>";

    var q = doc.getElementById("atlas-q");
    if (q) {
      q.addEventListener("input", function () {
        A.sel.q = q.value.replace(/^\s+|\s+$/g, "");
        paint();
      });
    }
    var play = doc.getElementById("atlas-play");
    if (play) play.addEventListener("click", togglePlay);
    var reset = doc.getElementById("atlas-reset");
    if (reset) {
      reset.addEventListener("click", function () {
        stopPlay();
        A.sel.period = ""; A.sel.region = ""; A.sel.q = "";
        A.sel.year = meta.year_max;
        if (q) q.value = "";
        paint();
      });
    }
    initMap();
    /* 本地卫星瓦片：有清单才露切换条（公开包没带瓦片 → 整条隐藏） */
    if (global.satManifest) {
      global.satManifest().then(function (m) {
        if (!m) return;
        A.satMeta = m;
        var bar = doc.getElementById("atlas-mode");
        if (bar) {
          bar.removeAttribute("hidden");
          bar.addEventListener("click", function (ev) {
            var b = ev.target;
            while (b && b !== bar && !b.getAttribute("data-am")) b = b.parentNode;
            if (!b || !b.getAttribute) return;
            var k = b.getAttribute("data-am");
            if (k) setMode(k);
          });
        }
      });
    }
    paint();
    if (!A.keyHandler) {
      A.keyHandler = onKey;
      doc.addEventListener("keydown", A.keyHandler);
    }
  }

  /* ── 对外接口 ────────────────────────────────────────────── */
  function render() {
    var box = doc.getElementById("w-atlas");
    if (!box) return;
    box.innerHTML = '<div class="loading"><span class="spin"></span>' +
      T("正在读取良渚时序…") + "</div>";
    api("/api/atlas").then(function (r) {
      if (!r.ok) {
        box.innerHTML = '<div class="notice bad">' + T("时序数据不可用：") +
          esc(r.error || "") + "</div>";
        return;
      }
      A.data = r.data || {};
      var meta = A.data.meta || {};
      A.sel = { year: meta.year_max || null, period: "", region: "", q: "" };
      build();
    });
  }

  function destroy() {
    stopPlay();
    destroyMap();
    A.satMeta = null;
    A.satLayer = null;
    A.sg = null;
    if (A.keyHandler) {
      doc.removeEventListener("keydown", A.keyHandler);
      A.keyHandler = null;
    }
    A.data = null;
  }

  global.LZKG_ATLAS = { render: render, destroy: destroy };
})(window);
