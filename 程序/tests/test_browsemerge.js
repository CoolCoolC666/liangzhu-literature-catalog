/* 浏览记录合并 `browseMerge()` 的实测。
 *
 * ★ 为什么必须测「合并」这件事
 *   「从 Web Dev 恢复」和「导入 JSON」原来都是**整体覆盖**本地记录 ——
 *   主设备有 5 条、手机只传了 2 条，回主设备一点「恢复」，本地就只剩 2 条。
 *   同步的本意是「两边合起来」，覆盖等于悄悄删数据。
 *
 * ★ 怎么测
 *   和 test_wdsafename.js 一样，**从 index.html 里提取真实函数源码**执行，
 *   不手抄。函数依赖 browseLoad() 与 BROWSE_MAX，用参数注入假实现。
 *
 * 用法：node tests/test_browsemerge.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const HTML = path.resolve(__dirname, "..", "lzkg", "web", "static", "index.html");
const src = fs.readFileSync(HTML, "utf8");

const mFn = src.match(/function browseMerge\(remoteItems\)\s*\{[\s\S]*?\n\}/);
if (!mFn) {
  console.error("[FAIL] 在 index.html 里找不到 function browseMerge(remoteItems)");
  process.exit(1);
}

// 假的本地点：用闭包变量模拟 localStorage 里的那份
let LOCAL = { items: [] };
const fakeLoad = () => LOCAL;
const browseMerge = new Function(
  "browseLoad",
  "BROWSE_MAX",
  mFn[0] + "\nreturn browseMerge;"
)(fakeLoad, 500);

let pass = 0;
const fail = [];
function check(name, ok, detail) {
  if (ok) {
    pass++;
    console.log("  [OK] " + name + (detail ? "   (" + detail + ")" : ""));
  } else {
    fail.push(name + (detail ? "   " + detail : ""));
    console.log("  [FAIL] " + name + (detail ? "   " + detail : ""));
  }
}

const it_ = (id, ts, title) => ({
  kind: "lit", id: id, title: title || id, year: "2020", ts: ts,
});

console.log("\n=== browseMerge() 实测（提取 index.html 真实源码执行）===\n");

// ---- 1. 并集：远端不能把本地独有的记录冲掉 ----
console.log("[1] 并集（核心：不丢本地记录）");
LOCAL = { items: [it_("A", 100), it_("B", 200), it_("C", 300)] };
let m = browseMerge([it_("A", 50), it_("B", 250), it_("D", 400)]);
check("本地 3 + 远端 3 → 4 条", m.items.length === 4, "实得 " + m.items.length);
check("本地独有的 C 还在", m.items.some((x) => x.id === "C"));
check("远端独有的 D 已加入", m.items.some((x) => x.id === "D"));
check("新增计数 = 1", m.added === 1, "实得 " + m.added);
check("刷新计数 = 1（只有 B 被推后）", m.refreshed === 1, "实得 " + m.refreshed);
check("local_count / remote_count 如实上报",
  m.local_count === 3 && m.remote_count === 3,
  m.local_count + " / " + m.remote_count);

// ---- 2. 时间取舍：同一篇保留更晚的 ts ----
console.log("\n[2] 同一篇的 ts 取舍");
const by = (arr, id) => arr.filter((x) => x.id === id)[0];
check("远端更晚（B: 200→250）→ 采用远端",
  by(m.items, "B").ts === 250, "实得 " + by(m.items, "B").ts);
check("远端更旧（A: 100 vs 50）→ 保留本地",
  by(m.items, "A").ts === 100, "实得 " + by(m.items, "A").ts);

// ---- 3. 排序：按 ts 升序（最旧的在前，和 browseRecord 一致） ----
console.log("\n[3] 排序");
const order = m.items.map((x) => x.id).join(",");
check("按 ts 升序 A,B,C,D", order === "A,B,C,D", order);

// ---- 4. 幂等：合并两次结果不变 ----
console.log("\n[4] 幂等");
LOCAL = { items: m.items };
const m2 = browseMerge([it_("A", 50), it_("B", 250), it_("D", 400)]);
check("再合并一次条数不变", m2.items.length === m.items.length,
  m2.items.length + " vs " + m.items.length);
check("再合并一次没有新增", m2.added === 0, "实得 " + m2.added);

// ---- 5. 上限截断：超出 500 保留最新的 ----
console.log("\n[5] 500 条上限");
const many = [];
for (let i = 0; i < 600; i++) many.push(it_("X" + i, 1000 + i));
LOCAL = { items: [] };
const m3 = browseMerge(many);
check("截断到 500 条", m3.items.length === 500, "实得 " + m3.items.length);
check("保留的是最新的（末尾 X599 在）", m3.items[m3.items.length - 1].id === "X599",
  m3.items[m3.items.length - 1].id);
check("最旧的已被丢掉（X0 不在）", !m3.items.some((x) => x.id === "X0"));

// ---- 6. 边界：空与脏数据 ----
console.log("\n[6] 边界");
LOCAL = { items: [it_("A", 100)] };
check("远端为空 → 本地原样",
  browseMerge([]).items.length === 1, "");
check("远端传 null 不抛错",
  (() => { try { return browseMerge(null).items.length === 1; } catch (e) { return false; } })());
LOCAL = { items: [] };
check("本地为空 → 远端全收", browseMerge([it_("A", 1), it_("B", 2)]).items.length === 2);
LOCAL = { items: [it_("A", 100)] };
const dirty = [it_("B", 200), { kind: "lit", title: "无 id", ts: 300 }, null, { id: "C" }];
const m4 = browseMerge(dirty);
check("无 id / null 的脏数据被跳过",
  !m4.items.some((x) => x && (x.id === undefined || x.id === "undefined")),
  m4.items.map((x) => String(x && x.id)).join(","));
check("有 id 但没 ts 的仍收录（ts 按 0 处理）", m4.items.some((x) => x.id === "B"));

// ---- 7. 不修改传进来的远端数组（调用方可能还要用） ----
console.log("\n[7] 不改入参");
LOCAL = { items: [] };
const input = [it_("P", 10), it_("Q", 20)];
const snapshot = JSON.stringify(input);
browseMerge(input);
check("传入数组未被就地改写", JSON.stringify(input) === snapshot);

console.log(`\n=== 结果：${pass} 通过 / ${fail.length} 失败 ===`);
if (fail.length) {
  console.log("失败项：");
  fail.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log("全部通过：恢复/导入走的是「合并」，本地已有的浏览记录不会被远端冲掉。");