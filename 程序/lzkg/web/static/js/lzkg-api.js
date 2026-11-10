/* lzkg-api.js — 良渚文化文献目录站的浏览器端可编程接口（window.lzkgAPI）
   干什么：给浏览器控制台 / 自动化脚本 / 外部 AI Agent 一组稳定的读取与操控入口，
   免去手点界面、猜 URL、解析 DOM。
   为什么独立成文件：主页面脚本（index.html 内联）体量已大且属于页面逻辑，
   对外接口应当是**可单独加载、可单独测试、不被页面改版牵连**的一层。

   硬约束：
     1) 纯 ES5（var / function），不引入构建步骤，浏览器直接跑；
     2) 零外链：不引用任何 http(s) 资源与 CDN；
     3) 不改动 index.html，也不引入任何依赖；
     4) 所有方法返回 Promise，失败时 resolve 成 {ok:false,error:"..."}，**永不 reject**
        ——调用方（尤其是自动化脚本）不该被迫写 try/catch；
     5) 不打 console 日志，避免污染站点控制台。
*/
(function (global) {
  "use strict";

  var VERSION = "v3.9-BP12";

  /* 白名单路由：只允许站内真实存在的页签，防止 location.hash 被写入任意串（如 javascript:） */
  var ROUTES = { lit: 1, imgsearch: 1, graph: 1, stats: 1, work: 1, overview: 1 };
  var LANGS = { zh: 1, en: 1, ja: 1 };
  /* 壁纸名不做白名单校验：bpApply 自己认名单外的名字（atlas 由并行任务实现），
     调用方传什么就交给它，失败只吞异常 */

  function fail(msg) { return { ok: false, error: String(msg || "未知错误") }; }

  /* 统一出口：把站点 api() 的结果收成 {ok:true,data} / {ok:false,error} */
  function callApi(path, params) {
    return Promise.resolve().then(function () {
      if (typeof global.api !== "function") {
        return fail("站点 api() 尚不可用（主页面脚本未加载）");
      }
      return global.api(path, params);
    }).then(function (r) {
      if (!r) return fail("接口无返回");
      if (r.ok === false) return fail(r.error || "请求失败");
      return { ok: true, data: r.data };
    });
  }

  /* 只挑需要的字段，缺字段就不给，不抛错 */
  function pick(src, keys) {
    var out = {};
    if (!src || typeof src !== "object") return out;
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (src[k] !== undefined && src[k] !== null) out[k] = src[k];
    }
    return out;
  }

  /* 路由规范化：返回 null 表示非法 */
  function normRoute(r) {
    if (typeof r !== "string") return null;
    var s = r.replace(/^#/, "").replace(/^\//, "");
    if (!s) return null;
    var segs = s.split("/").filter(function (x) { return x !== ""; });
    if (!segs.length || !ROUTES[segs[0]]) return null;
    if (segs.length === 1) return segs[0];
    /* 只允许「文献详情」带 id 后缀，且 id 形如 LIT-000001 */
    if (segs.length === 2 && segs[0] === "lit" && /^LIT-\d+$/i.test(segs[1])) return segs.join("/");
    return null;
  }

  var lzkgAPI = {};

  /* version 是**字符串常量**（有调用方直接读它），故方法另名为 getVersion */
  lzkgAPI.version = VERSION;
  lzkgAPI.ready = true;

  lzkgAPI.getVersion = function () {
    return Promise.resolve({ ok: true, version: VERSION });
  };

  lzkgAPI.lang = function () {
    return Promise.resolve().then(function () {
      if (typeof global.LZ_getLang !== "function") return fail("多语言模块尚未加载");
      return { ok: true, lang: global.LZ_getLang() };
    });
  };

  lzkgAPI.setLang = function (lang) {
    return Promise.resolve().then(function () {
      if (!LANGS[lang]) return fail("lang 只能是 zh / en / ja");
      if (typeof global.LZ_setLang !== "function") return fail("多语言模块尚未加载");
      global.LZ_setLang(lang);
      return { ok: true, lang: lang };
    });
  };

  lzkgAPI.go = function (route) {
    return Promise.resolve().then(function () {
      var norm = normRoute(route);
      if (!norm) {
        return fail("route 只能是 " + Object.keys(ROUTES).join(" / ") + "，文献详情可带 /LIT-xxxxxx");
      }
      global.location.hash = "#/" + norm;
      return { ok: true, route: "/" + norm };
    });
  };

  lzkgAPI.search = function (q, opts) {
    return Promise.resolve().then(function () {
      if (typeof q !== "string" || !q.replace(/^\s+|\s+$/g, "")) return fail("q 不能为空");
      var size = (opts && opts.size) ? opts.size : 10;
      return callApi("/api/literatures", { q: q, size: size }).then(function (r) {
        if (!r.ok) return r;
        var d = r.data || {};
        var items = (d.items || []).map(function (it) {
          return pick(it, ["lit_id", "title", "year", "doc_type"]);
        });
        return { ok: true, total: d.total, items: items };
      });
    });
  };

  lzkgAPI.lit = function (lit_id) {
    return Promise.resolve().then(function () {
      if (!lit_id) return fail("lit_id 不能为空");
      return callApi("/api/literatures/" + encodeURIComponent(lit_id), null).then(function (r) {
        if (!r.ok) return r;
        var d = r.data || {};
        var body = d.lit && typeof d.lit === "object" ? d.lit : d;
        var out = pick(body, ["lit_id", "title", "year", "doc_type", "authors"]);
        var tags = d.tags !== undefined ? d.tags : body.tags;
        if (tags !== undefined && tags !== null) out.tags = tags;
        return { ok: true, lit: out };
      });
    });
  };

  lzkgAPI.stats = function () {
    return Promise.all([
      callApi("/api/stats/timeline", null),
      callApi("/api/stats/region", null)
    ]).then(function (res) {
      var tl = res[0], rg = res[1];
      if (!tl.ok && !rg.ok) return fail(tl.error || rg.error);
      /* 部分失败不丢弃已拿到的部分，只把失败原因附在 errors 里 */
      var out = { ok: true, timeline: tl.ok ? tl.data : null, region: rg.ok ? rg.data : null };
      if (!tl.ok || !rg.ok) {
        out.errors = [];
        if (!tl.ok) out.errors.push("timeline: " + tl.error);
        if (!rg.ok) out.errors.push("region: " + rg.error);
      }
      return out;
    });
  };

  lzkgAPI.regions = function () {
    return callApi("/api/stats/region", null).then(function (r) {
      if (!r.ok) return r;
      var d = r.data || {};
      return { ok: true, items: d.items || d.regions || [] };
    });
  };

  lzkgAPI.theme = function (name) {
    return Promise.resolve().then(function () {
      if (typeof global.bpApply !== "function") return fail("壁纸切换函数尚不可用");
      try {
        global.bpApply(name);
      } catch (e) {
        /* atlas 等壁纸由别处实现，可能尚未就绪：吞掉异常，不让调用方炸 */
        return { ok: true, theme: name, applied: false };
      }
      return { ok: true, theme: name, applied: true };
    });
  };

  /* 透传站点 api()，供需要原始信封（含 raw 字段）的调用方使用 */
  lzkgAPI.api = function (path, params) {
    return Promise.resolve().then(function () {
      if (typeof global.api !== "function") return fail("站点 api() 尚不可用（主页面脚本未加载）");
      return global.api(path, params);
    }).then(function (r) {
      return r || fail("接口无返回");
    });
  };

  global.lzkgAPI = lzkgAPI;
})(typeof window !== "undefined" ? window : this);
