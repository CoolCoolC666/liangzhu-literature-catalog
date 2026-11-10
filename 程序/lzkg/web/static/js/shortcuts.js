/* ================================================================
   良渚文化文献目录站 · 键盘快捷键
   ---------------------------------------------------------------
   ★ 为什么独立成文件：
     index.html 已是 300KB+ 的单文件应用，交互逻辑全在里面；
     快捷键属于「可独立演进的横切能力」，拆出来才不污染主文件，
     也便于将来换一套键位而不用在长文件里翻找。
   ★ 硬约束（改动本文件时必须守住）：
     ① 纯 ES5 语法（var / function），不用 const/let/箭头函数/模板字符串/
        async/await/class —— 与站点其它脚本保持同一语法基线。
     ② 零外链：不引 CDN、不 fetch 外部资源；样式全部由本文件注入，
        不依赖 index.html 的改动。断网可用是本项目的硬要求。
     ③ 只新增本文件，不修改 index.html、不新增依赖、不动其它文件。
     ④ 不写 console.log —— 正式页面不该往控制台喷日志。
   ================================================================ */
(function (global) {
  "use strict";

  /* ---- 键位 → 路由 ---- */
  var MAP = {
    o: "#/overview",
    l: "#/lit",
    p: "#/imgsearch",
    g: "#/graph",
    s: "#/stats",
    w: "#/work"
  };

  /* ---- 说明浮层内容（三语；键名本身不译） ---- */
  var ROWS = [
    { k: "o", zh: "总览", en: "Overview", ja: "概要" },
    { k: "l", zh: "文献", en: "Literature", ja: "文献" },
    { k: "p", zh: "图版", en: "Plates", ja: "図版" },
    { k: "g", zh: "图谱", en: "Graph", ja: "グラフ" },
    { k: "s", zh: "统计", en: "Statistics", ja: "統計" },
    { k: "w", zh: "工作台", en: "Workbench", ja: "ワークベンチ" },
    { k: "/", zh: "聚焦检索框", en: "Focus the search box", ja: "検索ボックスにフォーカス" },
    { k: "?", zh: "显示/隐藏本说明", en: "Toggle this help", ja: "この説明の表示/非表示" }
  ];
  var T = {
    zh: { title: "键盘快捷键", close: "关闭", note: "在输入框内输入时不生效。" },
    en: { title: "Keyboard shortcuts", close: "Close", note: "Not active while typing in a field." },
    ja: { title: "キーボードショートカット", close: "閉じる", note: "入力欄での入力時は無効です。" }
  };

  var STYLE_ID = "lzkg-shortcuts-style";
  var OVERLAY_ID = "lzkg-shortcuts-overlay";
  var overlay = null;   /* { root, card, tbody, title, note, close } */

  function lang() {
    var l = "zh";
    try { l = global.LZ_getLang ? global.LZ_getLang() : "zh"; } catch (e) { l = "zh"; }
    return (l === "en" || l === "ja") ? l : "zh";
  }

  function el(tag, cls, text) {
    var n = global.document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function injectStyle() {
    if (global.document.getElementById(STYLE_ID)) return;
    var css = [
      "#" + OVERLAY_ID + "{position:fixed;inset:0;background:rgba(20,40,60,.28);z-index:9998;display:none;}",
      "#" + OVERLAY_ID + " .lz-sc-card{position:fixed;right:22px;bottom:22px;z-index:9999;",
      "  background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:6px;",
      "  box-shadow:0 8px 24px rgba(20,40,60,.12);padding:14px 16px;width:300px;max-width:78vw;",
      "  font-family:inherit;font-size:13px;line-height:1.6;}",
      "#" + OVERLAY_ID + " .lz-sc-head{display:flex;align-items:center;justify-content:space-between;",
      "  gap:10px;margin-bottom:8px;}",
      "#" + OVERLAY_ID + " .lz-sc-title{font-size:14px;font-weight:700;color:var(--brand);}",
      "#" + OVERLAY_ID + " .lz-sc-close{font-family:inherit;font-size:12px;cursor:pointer;",
      "  background:var(--card);color:var(--brand);border:1px solid var(--brand);border-radius:4px;",
      "  padding:3px 10px;}",
      "#" + OVERLAY_ID + " .lz-sc-close:hover{background:var(--brand);color:var(--card);}",
      "#" + OVERLAY_ID + " table{border-collapse:collapse;width:100%;}",
      "#" + OVERLAY_ID + " td{padding:3px 0;vertical-align:top;color:var(--ink2);}",
      "#" + OVERLAY_ID + " td.lz-sc-key{width:44px;color:var(--ink);font-weight:600;",
      "  font-family:Consolas,Courier New,monospace;}",
      "#" + OVERLAY_ID + " td.lz-sc-key kbd{background:var(--bg);border:1px solid var(--line);",
      "  border-radius:4px;padding:0 6px;font-family:inherit;font-size:12px;}",
      "#" + OVERLAY_ID + " .lz-sc-note{margin-top:9px;font-size:11.5px;color:var(--ink2);}"
    ].join("\n");
    var st = el("style");
    st.id = STYLE_ID;
    st.type = "text/css";
    st.appendChild(global.document.createTextNode(css));
    var host = global.document.head || global.document.documentElement;
    if (host) host.appendChild(st);
  }

  function buildOverlay() {
    if (overlay) return overlay;
    var root = el("div");
    root.id = OVERLAY_ID;
    var card = el("div", "lz-sc-card");
    var head = el("div", "lz-sc-head");
    var title = el("div", "lz-sc-title");
    var close = el("button", "lz-sc-close");
    close.type = "button";
    head.appendChild(title);
    head.appendChild(close);
    card.appendChild(head);
    var tbl = el("table");
    var tbody = el("tbody");
    tbl.appendChild(tbody);
    card.appendChild(tbl);
    var note = el("div", "lz-sc-note");
    card.appendChild(note);
    root.appendChild(card);
    (global.document.body || global.document.documentElement).appendChild(root);

    overlay = { root: root, card: card, tbody: tbody, title: title, note: note, close: close };
    close.addEventListener("click", function (e) {
      if (e && e.stopPropagation) e.stopPropagation();
      hide();
    });
    /* 点遮罩关闭；点卡片本身不关 */
    root.addEventListener("click", function () { hide(); });
    card.addEventListener("click", function (e) { if (e && e.stopPropagation) e.stopPropagation(); });
    return overlay;
  }

  function render() {
    var ov = buildOverlay();
    var lg = lang();
    var dict = T[lg];
    ov.title.textContent = dict.title;
    ov.close.textContent = dict.close;
    ov.note.textContent = dict.note;
    while (ov.tbody.firstChild) ov.tbody.removeChild(ov.tbody.firstChild);
    for (var i = 0; i < ROWS.length; i++) {
      var tr = el("tr");
      var td1 = el("td", "lz-sc-key");
      var kbd = el("kbd");
      kbd.textContent = ROWS[i].k;
      td1.appendChild(kbd);
      var td2 = el("td");
      td2.textContent = ROWS[i][lg] || ROWS[i].zh;
      tr.appendChild(td1);
      tr.appendChild(td2);
      ov.tbody.appendChild(tr);
    }
  }

  function isOpen() {
    return !!overlay && overlay.root.style.display === "block";
  }

  function show() {
    render();
    overlay.root.style.display = "block";
    /* 故意不 focus 卡片里的按钮：浮层只是提示，键盘用户要能继续用快捷键 */
  }

  function hide() {
    if (overlay) overlay.root.style.display = "none";
  }

  function toggle() {
    if (isOpen()) { hide(); } else { show(); }
  }

  /* ---- 检索框聚焦：不在文献页就先跳过去，页面渲染完再聚焦 ---- */
  function focusSearch() {
    var h = String(global.location.hash || "");
    if (h.indexOf("#/lit") !== 0) {
      global.location.hash = "#/lit";
      global.setTimeout(function () { grab(); }, 300);
    } else {
      grab();
    }
  }

  function grab() {
    var q = global.document.getElementById("l-q");
    if (q) { try { q.focus(); } catch (e) { /* 聚焦失败静默 */ } }
  }

  function inField(t) {
    if (!t) return false;
    var tag = t.tagName ? String(t.tagName).toUpperCase() : "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return !!t.isContentEditable;
  }

  function onKeyDown(e) {
    if (!e) return;
    /* 组合键交给浏览器（复制/粘贴/刷新等） */
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var key = e.key;
    /* Escape 例外：输入框里也要能关掉说明浮层 */
    if (key === "Escape") {
      if (isOpen()) { hide(); if (e.preventDefault) e.preventDefault(); }
      return;
    }
    /* 输入框里的按键一律不抢 */
    if (inField(e.target)) return;
    if (key === "?") { toggle(); if (e.preventDefault) e.preventDefault(); return; }
    if (key === "/") { focusSearch(); if (e.preventDefault) e.preventDefault(); return; }
    if (!key || key.length !== 1) return;
    var k = key.toLowerCase();
    if (!Object.prototype.hasOwnProperty.call(MAP, k)) return;
    global.location.hash = MAP[k];
    if (e.preventDefault) e.preventDefault();
  }

  function init() {
    injectStyle();
    global.document.addEventListener("keydown", onKeyDown);
    /* 切语言时重绘浮层文案（i18n 只管它自己的字典表） */
    if (global.MutationObserver) {
      var mo = new global.MutationObserver(function () { if (isOpen()) render(); });
      mo.observe(global.document.documentElement, { attributes: true, attributeFilter: ["data-lang"] });
    }
  }

  global.LZKG_SHORTCUTS = {
    map: MAP,
    help: show,
    toggleHelp: toggle
  };

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
