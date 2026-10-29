// E2E: a plain .md opens as markdown + code cells, math renders (KaTeX), and a
// remote edit to a rendered markdown cell updates the other page live.
// Run:  node tests/e2e/markdown_cells.mjs
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const bin = process.env.YUPYTER_BIN || path.join(root, ".venv/bin/yupyter");
const nb = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "yupyter-md-")), "m.md");
fs.writeFileSync(nb, "# Hello\n\nEuler: $e^{i\\pi}+1=0$\n\n```python\nprint(1)\n```\n");

const proc = spawn(bin, [nb, "--no-browser"], { stdio: ["ignore", "pipe", "inherit"] });
const url = await new Promise((resolve, reject) => {
  let buf = "";
  const t = setTimeout(() => reject(new Error("server did not start")), 30000);
  proc.stdout.on("data", (d) => {
    buf += d;
    const m = buf.match(/Open: (http\S+)/);
    if (m) { clearTimeout(t); resolve(m[1]); }
  });
});
const exe = process.env.PW_CHROMIUM || (fs.existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
let failed = false;
try {
  const ctx = await browser.newContext();
  const p1 = await ctx.newPage();
  await p1.goto(url);
  await p1.waitForSelector(".cell-md");
  await p1.locator(".cell-md .katex").first().waitFor({ timeout: 10000 });
  console.log("math rendered");
  const p2 = await ctx.newPage();
  await p2.goto(url);
  await p2.waitForSelector(".cell-md");
  await p2.locator(".cell-md .md-rendered").first().click();
  await p2.locator(".cell-md .cm-content").first().click();
  await p2.keyboard.press("Control+End");
  await p2.keyboard.type("\n\nremote-edit-marker");
  await p1.locator(".cell-md .md-rendered", { hasText: "remote-edit-marker" }).waitFor({ timeout: 10000 });
  console.log("remote edit re-rendered markdown cell");
  console.log("OK");
} catch (e) {
  failed = true;
  console.error("FAIL:", e.message);
} finally {
  await browser.close();
  proc.kill("SIGINT");
  await new Promise((r) => proc.on("exit", r));
}
process.exit(failed ? 1 : 0);
