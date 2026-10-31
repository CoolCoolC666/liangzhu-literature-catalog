/* 收藏页检索的**前端高亮处理**实测。
 *
 * ★ 为什么要单独测
 *   收藏页的命中片段来自后端 FTS5 的 `snippet(..., '<mark>', '</mark>', ...)`，
 *   前端要把它插进 innerHTML —— 于是有个两难：
 *     ① 直接插 → 片段里若含 HTML 会被当标签执行（XSS）
 *     ② 整体转义 → <mark> 高亮标记也一起被转义掉，**高亮没了**
 *   `escMark()` 的做法是：先整体转义，再把转义后的 `&lt;mark&gt;` 换回 `<mark>`。
 *   这个函数是纯字符串处理、不依赖 DOM，所以可以直接从 index.html 里
 *   **提取真实源码**执行 —— 不手抄、不猜，测的就是页面上跑的那份代码。
 *
 * 用法：node tests/test_favsearch.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const HTML = path.resolve(__dirname, "..", "lzkg", "web", "static", "index.html");
const src = fs.readFileSync(HTML, "utf8");

function grab(re, label) {
  const m = src.match(re);
  if (!m) {
    console.error(`[FAIL] 在 index.html 里找不到 ${label}`);
    process.exit(1);
  }
  return m[0];
}

// 取 ENT_MAP / esc / nz / escMark 的真实源码（esc 依赖 ENT_MAP，escMark 依赖 esc 与 nz）
const entSrc = grab(/var ENT_MAP = \{\};[\s\S]*?ENT_MAP\[String\.fromCharCode\(39\)\][^\n]*\n/, "ENT_MAP");
const escSrc = grab(/function esc\(s\)\s*\{[\s\S]*?\n\}/, "function esc(s)");
const nzSrc = grab(/function nz\(s\)\s*\{[^\n]*\}/, "function nz(s)");
const markSrc = grab(/function escMark\(s\)\s*\{[\s\S]*?\n\}/, "function escMark(s)");

// 把四段源码拼在一起执行
const escMark = new Function(
  entSrc + "\n" + escSrc + "\n" + nzSrc + "\n" + markSrc + "\nreturn escMark;"
)();

let pass = 0;
const fail = [];

function t(name, ok, detail) {
  if (ok) {
    pass++;
    console.log(`  [OK] ${name}` + (detail ? `   （${detail}）` : ""));
  } else {
    fail.push(`${name}  ${detail || ""}`);
    console.log(`  [FAIL] ${name}` + (detail ? `   （${detail}）` : ""));
  }
}

console.log("\n=== 前端 escMark() 实测（直接跑 index.html 里的真实源码）===\n");

/* ① 高亮必须保住 */
const snip = "# 中国考古<mark>遗址公园</mark>保护利用研究综述 …";
const out = escMark(snip);
t("<mark> 高亮标记被保留", out.indexOf("<mark>") >= 0 && out.indexOf("</mark>") >= 0, out);
t("高亮只有一处开、闭合", (out.match(/<mark>/g) || []).length === 1 && (out.match(/<\/mark>/g) || []).length === 1);

/* ② 普通文本原样 */
t("无标签的片段原样输出", escMark("中国考古遗址公园保护利用") === "中国考古遗址公园保护利用");

/* ③ HTML 必须被转义（XSS） */
const xss = '<script>alert(1)</script>';
const o3 = escMark(xss);
t("片段里的 <script> 被转义", o3.indexOf("<script>") < 0 && o3.indexOf("&lt;script&gt;") >= 0, o3);

const xss2 = '"><img src=x onerror=alert(1)>';
const o4 = escMark(xss2);
t("片段里的引号与 img 标签被转义", o4.indexOf("<img") < 0 && o4.indexOf("&quot;") >= 0, o4);

/* ④ 已经是实体的文本不被二次破坏成可执行标签 */
t("转义后的文本不产生裸标签", !/<[a-zA-Z/]/.test(o3.replace(/<\/?mark>/g, "")));

/* ⑤ 空值：走 nz() 的项目约定「空值显示 —」（前端另有 `it.snippet` 非空才渲染的判断） */
t("空字符串 → 占位符 —", escMark("") === "—", escMark(""));
t("null / undefined 不炸（也走占位符）",
  escMark(null) === "—" && escMark(undefined) === "—",
  `${escMark(null)} / ${escMark(undefined)}`);

/* ⑥ 非字符串 */
t("数字按字符串处理", escMark(123) === "123");

/* ⑦ 中文照常 */
t("中文片段正常输出", escMark("良渚遗址群") === "良渚遗址群");

console.log(`\n=== 结果：${pass} 通过 / ${fail.length} 失败 ===`);

// ---- 附加门禁：整页内联脚本语法检查 ----
const scripts = [];
const re = /<script([^>]*)>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(src)) !== null) {
  const attrs = m[1] || "";
  if (/\bsrc\s*=/i.test(attrs)) continue;
  if (/type\s*=\s*["']module["']/i.test(attrs)) continue;
  if (!m[2].trim()) continue;
  scripts.push(m[2]);
}
let syntaxBad = [];
scripts.forEach((code, i) => {
  try {
    new Function(code);
  } catch (e) {
    syntaxBad.push(`第 ${i + 1} 个内联 script：${e.message}`);
  }
});
if (syntaxBad.length) {
  console.log(`  [FAIL] 内联脚本语法检查：${syntaxBad.length} / ${scripts.length} 个有错`);
  syntaxBad.forEach((s) => console.log("      " + s));
  fail.push("内联脚本语法错误");
} else {
  console.log(`  [OK] 内联脚本语法检查（${scripts.length} 个 script 块全部编译通过）`);
  pass++;
}

console.log(`\n=== 最终：${pass} 通过 / ${fail.length} 失败 ===`);
if (fail.length) {
  console.log("失败项：");
  fail.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log("全部通过：命中片段既保住 <mark> 高亮，又不会把片段里的 HTML 当标签执行。");
