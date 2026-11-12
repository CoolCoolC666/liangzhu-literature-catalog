/* 良渚时间图谱（阶段 1 样例版）
 *   X 轴 = 良渚文化分期带（登记表 period_tags 原文的精确映射）
 *   Y 轴 = 主题族带（topic_tags 的 S-08xx 码族）
 *   格内纵向 = 文献出版年（1984 → 2026，全表同一尺度）
 *   点 = 文献；点它 → 右侧抽屉出该文献的图版与文献链接
 *   与右侧地图双向联动：点文献/格子 → 地图高亮其空间分区；点地图分区 → 矩阵高亮该分区的点
 * 纪律：纯 ES5、零依赖、零外链；不动既有统计页地图的实现，只借用它的全局对象与抽屉容器。
 */
(function (global) {
  "use strict";
  var doc = global.document;

  var TL = {
    data: null,
    sel: null,        /* {type:"cell"|"point", period, family, lit_id} */
    hlRegion: "",     /* 地图那边选中的分区名 */
    onRegionPick: null,
    lastOpen: null     /* 语言切换时重开抽屉用 */
  };

  /* 界面文案：走全局 i18n 词典（键＝中文原文），{n} 等占位符在本模块替换。
     不走全局的「子串替换」通道 —— 那条通道会连数据里的中文一起翻。 */
  function T(zh) {
    var f = global.LZ_tr;
    return f ? f(zh) : zh;
  }
  function TF(key, vars) {
    var s = T(key), k;
    for (k in vars) {
      if (!Object.prototype.hasOwnProperty.call(vars, k)) continue;
      s = s.split("{" + k + "}").join(vars[k]);
    }
    return s;
  }

  /* 分区名有两种写法：带编码（R-0101 良渚古城）与不带（良渚古城），比较前统一去掉编码 */
  function regionKey(s) {
    return String(s === undefined || s === null ? "" : s).replace(/^[A-Za-z]+-\d+\s*/, "").trim();
  }

  function esc(s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function nz(s) { return (s === undefined || s === null) ? "" : String(s); }
  function el(tag, cls, txt) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function api(path) {
    if (global.api) return global.api(path);
    return fetch(path).then(function (r) { return r.json(); });
  }
  function svgEl(tag, attrs) {
    var n = doc.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  /* 年份色阶：早＝陶土暖色，晚＝青蓝 */
  function yearColor(t) {
    var h = 35 + t * 168;           /* 35 → 203 */
    var l = 44 + t * 4;
    return "hsl(" + h.toFixed(0) + "," + 52 + "%," + l.toFixed(0) + "%)";
  }

  function layout(box) {
    var W = Math.max(320, box.clientWidth || 400);
    var H = 540;
    var ml = 46, yl = 96, mt = 92, mb = 26;
    var d = TL.data;
    var cols = d.x.length, rows = d.y.length;
    /* 右侧留 66px：X 轴标签是斜排的，最后一列的文字要有地方放，否则被裁 */
    var gw = Math.max(120, W - ml - yl - 66);
    var gh = H - mt - mb;
    return {
      W: W, H: H, ml: ml, yl: yl, mt: mt, mb: mb,
      cw: gw / cols, ch: gh / rows, gw: gw, gh: gh,
      ymin: d.meta.year_min, ymax: d.meta.year_max
    };
  }
  function py(L, y) {
    if (y === null || y === undefined) return L.mt + L.gh * 0.5;
    var t = (L.ymax > L.ymin) ? (y - L.ymin) / (L.ymax - L.ymin) : 0;
    return L.mt + t * L.gh;
  }

  function isDim(p) {
    var s = TL.sel;
    if (s) {
      if (s.type === "cell") {
        if (p.period !== s.period || p.family !== s.family) return true;
      } else if (s.type === "point") {
        if (p.lit_id !== s.lit_id) return true;
      }
    }
    /* 地图那边选的分区也参与：两个条件都命中才算"亮" */
    if (TL.hlRegion && regionKey(p.region) !== regionKey(TL.hlRegion)) return true;
    return false;
  }

  function render() {
    var box = doc.getElementById("w-tl");
    if (!box || !TL.data) return;
    var d = TL.data;
    var L = layout(box);
    box.innerHTML = "";

    var head = el("div", "tl-head");
    head.innerHTML = "<b>" + T("良渚时间图谱") + "</b>" +
      "<span class=\"small muted\">" + T("X＝良渚文化分期带　Y＝主题族带　格内纵向＝出版年") + "</span>";
    box.appendChild(head);

    var svg = svgEl("svg", {
      "class": "tl-svg", width: L.W, height: L.H,
      viewBox: "0 0 " + L.W + " " + L.H, role: "img",
      "aria-label": T("良渚时间图谱矩阵：横轴为良渚文化分期，纵轴为主题族带，每个点是一篇文献")
    });

    /* 年份网格线 + 左侧年份尺 */
    var ticks = [1984, 1995, 2005, 2015, 2026];
    ticks.forEach(function (y) {
      var yy = py(L, y);
      svg.appendChild(svgEl("line", { x1: L.ml, y1: yy, x2: L.ml + L.yl + L.gw, y2: yy,
        stroke: "#e3eaef", "stroke-width": 1, "stroke-dasharray": "2 3" }));
      var t = svgEl("text", { x: L.ml - 6, y: yy + 3.5, "text-anchor": "end",
        "font-size": "10", fill: "#7b8a95" });
      t.textContent = String(y);
      svg.appendChild(t);
    });

    /* 格：底色 + 计数 */
    var cells = {};
    d.x.forEach(function (xb, ci) {
      d.y.forEach(function (yb, ri) {
        var x = L.ml + L.yl + ci * L.cw, y = L.mt + ri * L.ch;
        var key = xb.key + "|" + yb.key;
        var n = d.cells[key] || 0;
        cells[key] = { x: x, y: y, w: L.cw, h: L.ch, n: n };
        var rect = svgEl("rect", {
          x: x + 1, y: y + 1, width: L.cw - 2, height: L.ch - 2,
          fill: (ci + ri) % 2 ? "#f7fafb" : "#eef4f7",
          stroke: "#dde6ec", "stroke-width": 1, rx: 3
        });
        rect.addEventListener("click", function () { pickCell(xb.key, yb.key); });
        svg.appendChild(rect);
        if (n) {
          var ct = svgEl("text", { x: x + L.cw - 5, y: y + 13, "text-anchor": "end",
            "font-size": "10", fill: "#5b6b78" });
          ct.textContent = String(n);
          svg.appendChild(ct);
        }
      });
    });

    /* 点：按格内年份定位，同年水平微散开 */
    var byCell = {};
    d.points.forEach(function (p) {
      var k = p.period + "|" + p.family;
      if (!byCell[k]) byCell[k] = [];
      byCell[k].push(p);
    });
    Object.keys(byCell).forEach(function (k) {
      var arr = byCell[k];
      var cell = cells[k];
      if (!cell) return;
      var groups = {};
      arr.forEach(function (p) {
        var yv = (p.y === null || p.y === undefined) ? "NA" : p.y;
        if (!groups[yv]) groups[yv] = [];
        groups[yv].push(p);
      });
      Object.keys(groups).forEach(function (yv) {
        var g = groups[yv];
        var yy = py(L, yv === "NA" ? null : Number(yv));
        var step = Math.min(13, (L.cw - 18) / Math.max(1, g.length - 1));
        g.forEach(function (p, i) {
          var xx = cell.x + L.cw / 2 + (i - (g.length - 1) / 2) * step;
          var t = (L.ymax > L.ymin && p.y) ? (p.y - L.ymin) / (L.ymax - L.ymin) : 0.5;
          var c = svgEl("circle", {
            cx: xx, cy: yy, r: g.length > 1 ? 2.6 : 3.2,
            fill: yearColor(t), "fill-opacity": isDim(p) ? 0.14 : 0.82,
            stroke: isDim(p) ? "none" : "#ffffff", "stroke-width": 0.6,
            "data-lit": p.lit_id, "data-period": p.period, "data-family": p.family,
            tabindex: "0", role: "button",
            "aria-label": TF("{t}｜{y}｜图版 {n} 张", {
              t: p.title, y: (p.year || T("无年份")), n: p.img_count
            })
          });
          c.addEventListener("mouseover", function (ev) { tip(p, ev); });
          c.addEventListener("mousemove", moveTip);
          c.addEventListener("mouseout", hideTip);
          c.addEventListener("click", function (ev) {
            if (ev && ev.stopPropagation) ev.stopPropagation();
            pickPoint(p);
          });
          svg.appendChild(c);
        });
      });
    });

    /* Y 轴：主题族带 */
    d.y.forEach(function (yb, ri) {
      var y = L.mt + ri * L.ch + L.ch / 2;
      var g = svgEl("g", { "class": "tl-row", tabindex: "0", role: "button" });
      var t = svgEl("text", { x: L.ml + L.yl - 6, y: y - 2, "text-anchor": "end",
        "font-size": "11", "font-weight": "600", fill: "#33424d" });
      t.textContent = yb.name;
      g.appendChild(t);
      var t2 = svgEl("text", { x: L.ml + L.yl - 6, y: y + 11, "text-anchor": "end",
        "font-size": "9.5", fill: "#7b8a95" });
      t2.textContent = yb.key + " · " + yb.n + " " + T("篇");
      g.appendChild(t2);
      g.addEventListener("click", function () { pickRow(yb.key); });
      svg.appendChild(g);
    });

    /* X 轴：分期带 */
    var phNote = [];
    d.x.forEach(function (xb, ci) {
      var x = L.ml + L.yl + ci * L.cw + L.cw / 2;
      var g = svgEl("g", { "class": "tl-col", tabindex: "0", role: "button" });
      var two = String(xb.name || "").length > 5;
      var ly = L.mt - (two ? 26 : 10);
      var t = svgEl("text", {
        x: x, y: ly, "font-size": "11", "font-weight": "600", fill: "#33424d",
        transform: "rotate(-38 " + x + " " + ly + ")", "text-anchor": "start"
      });
      /* 长名拆两行：窄版式下相邻列不压字 */
      var nm = String(xb.name || "");
      if (two) {
        var cut = Math.ceil(nm.length / 2);
        var sp1 = svgEl("tspan", { x: x, dy: 0 });
        sp1.textContent = nm.slice(0, cut);
        var sp2 = svgEl("tspan", { x: x, dy: 12 });
        sp2.textContent = nm.slice(cut);
        t.appendChild(sp1);
        t.appendChild(sp2);
      } else {
        t.textContent = nm;
      }
      g.appendChild(t);
      var t2 = svgEl("text", {
        x: x, y: L.mt - 1, "font-size": "9.5", fill: "#7b8a95",
        transform: "rotate(-38 " + x + " " + (L.mt - 1) + ")", "text-anchor": "start"
      });
      t2.textContent = xb.n + " " + T("篇");
      g.appendChild(t2);
      if (xb.post_heritage) {
        phNote.push(TF("「{b}」中含申遗后文献 {n} 篇", { b: xb.name, n: xb.post_heritage }));
      }
      g.addEventListener("click", function () { pickCol(xb.key); });
      svg.appendChild(g);
    });

    box.appendChild(svg);

    var legend = el("div", "tl-legend small muted");
    legend.innerHTML = T("点＝一篇文献（一篇可带多个主题族，故在多条带各出现一次）；") +
      T("点的颜色＝出版年：") +
      "<span style=\"color:hsl(35,52%,44%)\">" + T("较早") + "</span> → " +
      "<span style=\"color:hsl(203,52%,48%)\">" + T("较晚") + "</span>" +
      T("；点它 → 右侧出图版与文献；点格子／列头／行头 → 筛选并联动地图。");
    box.appendChild(legend);
    if (phNote.length) {
      var note = el("div", "tl-legend small muted");
      note.textContent = phNote.join("；");
      box.appendChild(note);
    }
  }

  /* ---------------- 提示浮层 ---------------- */
  var tipEl = null;
  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = el("div", "tl-tip");
    tipEl.style.cssText = "position:fixed;z-index:9800;display:none;max-width:280px;" +
      "background:#10202b;color:#eef4f7;border-radius:5px;padding:7px 9px;" +
      "font-size:12px;line-height:1.5;box-shadow:0 6px 18px rgba(0,0,0,.3);pointer-events:none";
    doc.body.appendChild(tipEl);
    return tipEl;
  }
  function tip(p, ev) {
    var t = ensureTip();
    t.innerHTML = "<b>" + esc(p.title) + "</b><br>" +
      TF("出版年 {y}", { y: esc(p.year || "—") }) + "<br>" +
      TF("图版 {n} 张", { n: esc(p.img_count) }) + "<br>" +
      TF("分期 {p}", { p: esc(p.period) }) + "<br>" +
      TF("主题族 {f}", { f: esc(p.family) }) + "<br>" +
      TF("空间分区 {r}", { r: esc(p.region || "—") });
    t.style.display = "block";
    moveTip(ev);
  }
  function moveTip(ev) {
    if (!tipEl || !ev) return;
    var x = (ev.clientX || 0) + 14, y = (ev.clientY || 0) + 14;
    var w = tipEl.offsetWidth || 240, h = tipEl.offsetHeight || 60;
    if (x + w > global.innerWidth - 8) x = (ev.clientX || 0) - w - 12;
    if (y + h > global.innerHeight - 8) y = (ev.clientY || 0) - h - 12;
    tipEl.style.left = x + "px";
    tipEl.style.top = y + "px";
  }
  function hideTip() { if (tipEl) tipEl.style.display = "none"; }

  /* ---------------- 选点 / 选格 ---------------- */
  function pickPoint(p) {
    TL.sel = { type: "point", lit_id: p.lit_id, period: p.period, family: p.family };
    render();
    highlightRegion(p.region);
    openDrawer(p);
  }
  function pickCell(period, family) {
    TL.sel = { type: "cell", period: period, family: family };
    render();
    var pts = TL.data.points.filter(function (p) {
      return p.period === period && p.family === family;
    });
    highlightRegion("");
    openCellDrawer(period, family, pts);
  }
  function pickCol(period) {
    TL.sel = { type: "cell", period: period, family: "*" };
    render();
    var pts = TL.data.points.filter(function (p) { return p.period === period; });
    var names = {};
    pts.forEach(function (p) { if (p.region) names[p.region] = 1; });
    highlightRegion(Object.keys(names).join("|"));
    openCellDrawer(period, "*", pts);
  }
  function pickRow(family) {
    TL.sel = { type: "cell", period: "*", family: family };
    render();
    var pts = TL.data.points.filter(function (p) { return p.family === family; });
    highlightRegion("");
    openCellDrawer("*", family, pts);
  }

  /* 地图联动：把分区名交给地图的高亮（复用既有 mapstHighlightMarkers） */
  function highlightRegion(names) {
    var st = global.MAPST;
    if (!st) return;
    var first = (names || "").split("|")[0] || "";
    st.hl = regionKey(first);
    if (global.mapstHighlightMarkers) global.mapstHighlightMarkers();
  }

  /* ---------------- 抽屉（复用地图那块抽屉容器） ---------------- */
  function drawerBox() {
    var box = doc.getElementById("w-map-info");
    if (!box) return null;
    var wrap = doc.getElementById("mapatlas-wrap");
    if (wrap) wrap.classList.add("open");
    return box;
  }
  function closeBtn() {
    return "<button class=\"btn ghost\" id=\"tl-close\">✕ " + T("收起") + "</button>";
  }
  function bindClose(box) {
    var b = doc.getElementById("tl-close");
    if (!b) return;
    b.addEventListener("click", function () {
      var wrap = doc.getElementById("mapatlas-wrap");
      if (wrap) wrap.classList.remove("open");
      TL.lastOpen = null;
    });
  }
  function periodName(key) {
    var b = null;
    (TL.data.x || []).forEach(function (x) { if (x.key === key) b = x; });
    return b ? b.name : key;
  }
  function familyName(key) {
    var b = null;
    (TL.data.y || []).forEach(function (x) { if (x.key === key) b = x; });
    return b ? (b.key + " " + b.name) : key;
  }

  function openDrawer(p) {
    var box = drawerBox();
    if (!box) return;
    var h = "<div class=\"lz-info\"><div style=\"display:flex;align-items:center;gap:8px\">" +
      "<h4 style=\"flex:1\">" + esc(p.title) + "</h4>" + closeBtn() + "</div>";
    h += "<div class=\"kv\"><b>" + T("文献编号：") + "</b>" + esc(p.lit_id) + "</div>";
    h += "<div class=\"kv\"><b>" + T("出版年：") + "</b>" + esc(p.year || "—") + "</div>";
    h += "<div class=\"kv\"><b>" + T("类型：") + "</b>" + esc(p.doc_type || "—") + "</div>";
    h += "<div class=\"kv\"><b>" + T("良渚分期：") + "</b>" + esc(periodName(p.period)) + "</div>";
    h += "<div class=\"kv\"><b>" + T("主题族：") + "</b>" + esc(familyName(p.family)) + "</div>";
    h += "<div class=\"kv\"><b>" + T("空间分区：") + "</b>" + esc(p.region || "—") + "</div>";
    h += "<div class=\"kv\"><b>" + T("图版：") + "</b>" + esc(p.img_count) + "</div>";
    h += "<div style=\"margin-top:9px;display:flex;gap:8px;flex-wrap:wrap\">" +
      "<a class=\"btn\" href=\"#/lit/" + esc(p.lit_id) + "\">" + T("打开文献详情") + "</a>" +
      "<button class=\"btn ghost\" id=\"tl-find\">" + T("在文献页筛选") + "</button></div>";
    h += "<div id=\"tl-imgs\" class=\"small muted\" style=\"margin-top:9px\">" + T("正在读取图版…") + "</div></div>";
    box.innerHTML = h;
    bindClose(box);
    TL.lastOpen = function () { openDrawer(p); };
    var btn = doc.getElementById("tl-find");
    if (btn) btn.addEventListener("click", function () {
      if (global.mapstGoFilter) global.mapstGoFilter({ q: p.lit_id });
    });
    var holder = doc.getElementById("tl-imgs");
    api("/api/images?lit_id=" + encodeURIComponent(p.lit_id) + "&size=12").then(function (r) {
      if (!holder) return;
      if (!r.ok) { holder.textContent = T("图版读取失败：") + (r.error || ""); return; }
      var d = r.data || {};
      var items = d.items || [];
      if (!items.length) { holder.textContent = T("这篇没有图版。"); return; }
      var pending = 0;
      for (var i = 0; i < items.length; i++) {
        if ((items[i].review_status || "").indexOf("待") >= 0 ||
            (items[i].review_status || "").indexOf("PENDING") >= 0) pending++;
      }
      var s = "<div style=\"margin-bottom:5px\">" +
        TF("共 {n} 张，以下显示前 {m} 张", { n: esc(d.total), m: items.length }) +
        (pending ? TF("（其中 {n} 张尚未人工复核，图注与分类为 AI 初标）", { n: pending }) : "") + "</div>";
      s += "<div class=\"imgtiles\">";
      for (var j = 0; j < items.length; j++) {
        s += (global.imgTile ? global.imgTile(items[j], j, "tl-" + p.lit_id) : "");
      }
      s += "</div>";
      holder.innerHTML = s;
    });
  }

  function openCellDrawer(period, family, pts) {
    var box = drawerBox();
    if (!box) return;
    var title = (period === "*" ? T("全部分期") : periodName(period)) + " · " +
      (family === "*" ? T("全部主题族") : familyName(family));
    var seen = {}, uniq = [];
    pts.forEach(function (p) {
      if (!seen[p.lit_id]) { seen[p.lit_id] = 1; uniq.push(p); }
    });
    var h = "<div class=\"lz-info\"><div style=\"display:flex;align-items:center;gap:8px\">" +
      "<h4 style=\"flex:1\">" + esc(title) + "</h4>" + closeBtn() + "</div>";
    h += "<div class=\"kv\">" + TF("文献 {n} 篇（展开点 {m} 个）", { n: uniq.length, m: pts.length }) + "</div>";
    h += "<div class=\"lits\">";
    uniq.forEach(function (p) {
      h += "<a class=\"lit\" href=\"#/lit/" + esc(p.lit_id) + "\">" + esc(p.title) +
        (p.year ? "<span class=\"yr\">" + esc(p.year) + "</span>" : "") +
        "<span class=\"yr\"> · " + TF("图版 {n} 张", { n: esc(p.img_count) }) + "</span></a>";
    });
    h += "</div></div>";
    box.innerHTML = h;
    bindClose(box);
    TL.lastOpen = function () { openCellDrawer(period, family, pts); };
  }

  /* 地图那边点了分区 → 矩阵高亮该分区的点 */
  TL.onRegionPick = function (name) {
    TL.hlRegion = name || "";
    if (TL.data) render();
  };

  TL.render = function (data) {
    TL.data = data;
    TL.sel = null;
    render();
  };
  /* 语言切换 → 重画：本模块文案自己拼（不走全局子串替换），切语言须重渲染 */
  var lastLang = null;
  function curLang() {
    var d = global.document.documentElement;
    return (d && d.getAttribute("data-lang")) || "zh";
  }
  function onLangChange() {
    var l = curLang();
    if (lastLang === null) { lastLang = l; return; }
    if (l === lastLang) return;
    lastLang = l;
    if (TL.data) render();
    if (TL.lastOpen) TL.lastOpen();
  }
  if (global.MutationObserver && global.document.documentElement) {
    var mo = new global.MutationObserver(function () { onLangChange(); });
    mo.observe(global.document.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
  }

  global.LZKG_TL = TL;
})(window);
