/* 地图素材页（BP18）
   ★ 组员交付的第三方地图素材：总 89 幅，按「来源非论坛/自媒体 + 非翻拍 + 够清楚」筛出一批入站。
   ★ ES5、零外链：清单来自 /api/maps（由 _tools/build_maps.py 生成），图片来自 /media/map/。
   ★ 每幅都带来源与原文链接；只做站内阅读与引用，不提供原图下载入口。
   ★ 图名、说明、来源是**数据**，不翻译（容器类名在 i18n.js 的 SKIP 里）。 */
(function (global) {
  "use strict";

  var S = { data: null, dim: "", list: [] };

  function chipHtml(d, cur) {
    return '<button class="maps-chip' + (cur === d.k ? " on" : "") + '" data-mdim="' + esc(d.k) + '">' +
           esc(d.n) + '<em>' + (d.c === undefined ? "" : d.c) + "</em></button>";
  }

  function cardHtml(it, i) {
    var src = "/media/map/" + encodeURIComponent(it.file);
    var warn = it.caveat ? '<p class="maps-warn">' + esc(it.caveat) + "</p>" : "";
    var link = it.link ? '<a href="' + esc(it.link) + '" target="_blank" rel="noopener">' +
                        "原文链接</a>" : "";
    return '<div class="maps-card">' +
      '<button class="maps-thumb" data-mi="' + i + '"><img src="' + src + '" alt="' + esc(it.title) + '" loading="lazy"></button>' +
      '<div class="maps-meta">' +
      '<p class="maps-t">' + esc(it.title) + "</p>" +
      '<p class="maps-n">' + esc(it.note || "") + "</p>" +
      '<p class="maps-s"><span class="maps-src">' + esc(it.source) + " · " + it.w + "×" + it.h + "</span>" + link + "</p>" +
      warn + "</div></div>";
  }

  function dimsArr() {
    var d = (S.data && S.data.dims) || {};
    var out = [{ k: "", n: "全部维度", c: (S.data && S.data.included) || 0 }];
    for (var k in d) {
      if (Object.prototype.hasOwnProperty.call(d, k)) out.push({ k: k, n: k, c: d[k] });
    }
    return out;
  }

  function filter() {
    var items = (S.data && S.data.items) || [];
    S.list = [];
    for (var i = 0; i < items.length; i++) {
      if (!S.dim || items[i].category === S.dim) S.list.push(items[i]);
    }
  }

  function paint() {
    var d = S.data || {};
    /* 公开版不带这批素材（授权未核实）→ 明确说清，别让读者以为页面坏了 */
    if (!((d.items || []).length)) {
      view.innerHTML =
        '<div class="pagehead"><div><div class="ph-zh">地图素材</div>' +
        '<div class="ph-en">地图素材 · 组员交付的第三方地图 · 逐幅标注来源</div></div>' +
        '<div class="ph-read">端点 <span class="mono">/api/maps · /media/map/*.jpg</span></div></div>' +
        '<div class="notebox">' + esc(d.note || "地图素材未随包分发。") + "</div>";
      return;
    }
    var dims = dimsArr();
    var chips = "";
    for (var i = 0; i < dims.length; i++) chips += chipHtml(dims[i], S.dim);
    var cards = "";
    for (var j = 0; j < S.list.length; j++) cards += cardHtml(S.list[j], j);
    var rules = (d.rules || []).join("；");
    view.innerHTML =
      '<div class="pagehead"><div><div class="ph-zh">地图素材</div>' +
      '<div class="ph-en">地图素材 · 组员交付的第三方地图 · 逐幅标注来源</div></div>' +
      '<div class="ph-read">端点 <span class="mono">/api/maps · /media/map/*.jpg</span></div></div>' +
      '<div class="maps">' +
      '<div class="notebox">' +
        "本组地图素材均为第三方网页转载，原始权利人未核实：站内只提供阅读与引用，每幅均标注来源与原文链接；公开版不含本组素材。" +
      "</div>" +
      '<p class="maps-count"><span>收录数量（幅）</span> <b>' + (d.included || 0) +
      '</b> <span class="maps-sep">·</span> <span>素材总数（幅）</span> <b>' + (d.total_all || 0) + "</b></p>" +
      '<div class="maps-bar">' + chips + "</div>" +
      '<div class="maps-grid">' + cards + "</div>" +
      '<p class="maps-s"><span>收录口径：</span><span class="maps-src">' + esc(rules) + "</span></p>" +
      "</div>";
    bind();
  }

  function bind() {
    var chips = view.querySelectorAll("[data-mdim]");
    for (var i = 0; i < chips.length; i++) chips[i].addEventListener("click", onChip);
    var ths = view.querySelectorAll(".maps-thumb");
    for (var j = 0; j < ths.length; j++) ths[j].addEventListener("click", onThumb);
  }

  function onChip() {
    S.dim = this.getAttribute("data-mdim") || "";
    filter();
    paint();
  }

  function onThumb() {
    openBig(parseInt(this.getAttribute("data-mi"), 10) || 0);
  }

  function openBig(i) {
    var items = [];
    for (var k = 0; k < S.list.length; k++) {
      var it = S.list[k];
      items.push({
        src: "/media/map/" + encodeURIComponent(it.file),
        fig: it.id,
        caption: it.title + (it.note ? "：" + it.note : ""),
        meta: "来源 " + it.source + "　" + it.w + "×" + it.h + "　" + it.id + (it.caveat ? "　" + it.caveat : ""),
        lit_id: ""
      });
    }
    LB_GROUPS.maps = items;
    if (typeof lbOpen === "function") lbOpen("maps", i);
  }

  function render() {
    if (S.data) { filter(); paint(); return; }
    view.innerHTML = '<div class="loading">正在读取地图素材…</div>';
    api("/api/maps").then(function (r) {
      if (!r.ok) { view.innerHTML = '<div class="errbox">地图素材读取失败：</div>'; return; }
      S.data = r.data || {};
      filter();
      paint();
    });
  }

  global.renderMaps = render;
})(window);