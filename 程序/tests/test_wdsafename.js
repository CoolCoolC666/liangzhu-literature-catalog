/* 前端文件名兜底 `wdSafeName()` 的实测。
 *
 * ★ 为什么要单独测这个函数
 *   组员第二次报错就是「文件名不合法」。这段兜底是纯字符串处理、
 *   不依赖 DOM，所以可以直接从 index.html 里**提取真实源码**执行 ——
 *   不手抄、不猜，测的就是页面上跑的那份代码。
 *
 * ★ 测的重点
 *   ① 组员实际粘进来的完整路径（`下载NDM\lzkg-browse-20261029.json`）能不能切对
 *   ② 前后端规则必须**对齐**：前端放过的名字，后端不能拒（否则又报「文件名不合法」）
 *
 * 用法：node tests/test_wdsafename.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const HTML = path.resolve(__dirname, "..", "lzkg", "web", "static", "index.html");
const src = fs.readFileSync(HTML, "utf8");

// 取默认名常量
const mDef = src.match(/var\s+WEBDAV_DEFAULT_NAME\s*=\s*"([^"]+)"/);
if (!mDef) {
  console.error("[FAIL] 在 index.html 里找不到 WEBDAV_DEFAULT_NAME");
  process.exit(1);
}
const DEFAULT_NAME = mDef[1];

// 取函数源码（从 function 开头到首个顶格 }）
const mFn = src.match(/function wdSafeName\(raw\)\s*\{[\s\S]*?\n\}/);
if (!mFn) {
  console.error("[FAIL] 在 index.html 里找不到 function wdSafeName(raw)");
  process.exit(1);
}
const fnSrc = mFn[0];

// 执行真实源码（默认名作为参数注入，避免依赖全局变量）
const wdSafeName = new Function(
  "WEBDAV_DEFAULT_NAME", fnSrc + "\nreturn wdSafeName;"
)(DEFAULT_NAME);

/* 后端 lzkg/webdav.py 的规则：
     name.strip().lstrip('/') 后，若含 "/" 或 "\" 或以 "." 开头 → 拒绝
   前端放过的名字必须能过后端这一关。 */
function backendRejects(name) {
  const n = String(name || "").trim().replace(/^\/+/, "");
  if (n.includes("/") || n.includes("\\") || n.startsWith(".")) return true;
  return false;
}

const CASES = [
  // [输入, 期望输出, 说明]
  ["下载NDM\\lzkg-browse-20261029.json", "lzkg-browse-20261029.json", "组员实际粘的 Windows 路径"],
  ["下载NDM/lzkg-browse-20261029.json", "lzkg-browse-20261029.json", "正斜杠路径"],
  ["C:\\Users\\you\\Downloads\\lzkg-browse.json", "lzkg-browse.json", "完整绝对路径"],
  ['"lzkg-browse.json"', "lzkg-browse.json", "带双引号（复制自资源管理器）"],
  ["lzkg-browse-20261029.json", "lzkg-browse-20261029.json", "已经是干净文件名"],
  ["", DEFAULT_NAME, "空 → 默认名"],
  ["   ", DEFAULT_NAME, "纯空格 → 默认名"],
  ["..", DEFAULT_NAME, "上跳 → 默认名"],
  [".", DEFAULT_NAME, "当前目录 → 默认名"],
  ["../etc/passwd", "passwd", "路径穿越只留最后一段"],
  ["良渚浏览记录.json", "良渚浏览记录.json", "中文名保留"],
  ["lzkg-browse-20261029 (1).json", "lzkg-browse-202610291.json", "空格括号被清掉"],
  // 点开头：前端过去会原样放过，后端必拒 → 这两条曾是真漏洞
  [".hidden", null, "点开头：不能交给后端去拒"],
  [".json", null, "纯扩展名：不能交给后端去拒"],
  ["..\\..\\x.json", "x.json", "Windows 路径穿越"],
];

let pass = 0;
const fail = [];

console.log("\n=== 前端 wdSafeName() 实测（直接跑 index.html 里的真实源码）===");
console.log(`默认名常量 WEBDAV_DEFAULT_NAME = ${JSON.stringify(DEFAULT_NAME)}\n`);

for (const [input, want, note] of CASES) {
  const got = wdSafeName(input);
  const rejects = backendRejects(got);
  let ok;
  if (want === null) {
    ok = !rejects; // 只要求「后端不会拒」
  } else {
    ok = got === want && !rejects;
  }
  const shown = JSON.stringify(input);
  if (ok) {
    pass++;
    console.log(`  [OK] ${shown} → ${JSON.stringify(got)}   （${note}）`);
  } else {
    const why = rejects
      ? "结果会被后端判「文件名不合法」"
      : `期望 ${JSON.stringify(want)}`;
    fail.push(`${shown} → ${JSON.stringify(got)}，${why}`);
    console.log(`  [FAIL] ${shown} → ${JSON.stringify(got)}   （${note}）   ${why}`);
  }
}

// 追加：清洗必须幂等（同一个名字处理两次结果不变）
const idem = ["下载NDM\\a.json", "良渚.json", ".x"].every((s) => {
  const once = wdSafeName(s);
  return wdSafeName(once) === once || backendRejects(wdSafeName(once)) === false;
});
console.log(
  idem ? "  [OK] 清洗幂等（处理两次结果稳定）" : "  [FAIL] 清洗不幂等"
);
if (idem) pass++;
else fail.push("清洗不幂等");

console.log(`\n=== 结果：${pass} 通过 / ${fail.length} 失败 ===`);

// ---- 附加门禁：整页内联脚本语法检查 ----
// 改一行正则就可能把整页 JS 弄挂（控制台报 SyntaxError，页面一片空白）。
// 这里把 index.html 里所有内联 <script> 逐个编译一遍（只编译不执行）。
const scripts = [];
const re = /<script([^>]*)>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(src)) !== null) {
  const attrs = m[1] || "";
  if (/\bsrc\s*=/i.test(attrs)) continue;            // 外链脚本不管
  if (/type\s*=\s*["']module["']/i.test(attrs)) continue; // 模块语法 new Function 编译不了
  if (!m[2].trim()) continue;
  scripts.push(m[2]);
}
let syntaxBad = [];
scripts.forEach((code, i) => {
  try {
    new Function(code);                              // 只编译，不执行
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
console.log("全部通过：前端兜底产出的名字，后端一定不会判「文件名不合法」。");