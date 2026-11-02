// 抽出 architecture.html 的内联脚本 -> arch.js，便于 node --check 独立校验
const fs = require("fs");
const path = require("path");
const outDir = path.join(__dirname, "results");
fs.mkdirSync(outDir, {recursive: true});
const html = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "architecture.html"), "utf8");
const m = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
console.log("script blocks:", m.length);
m.forEach((x, i) => {
  fs.writeFileSync(path.join(outDir, `arch${i}.js`), x[1], "utf8");
  console.log(`arch${i}.js bytes:`, Buffer.byteLength(x[1], "utf8"));
});
// 顺带把数据块的行数报一下，确认没有外链
console.log("external src attrs:", (html.match(/<script[^>]*\bsrc=/gi) || []).length);
console.log("cdn refs:", (html.match(/https?:\/\/[^\s"')]+/g) || []).filter(u => !u.startsWith("http://www.w3.org")).join(" | ") || "(none)");
