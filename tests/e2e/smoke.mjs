// E2E smoke: launches `yupyter`, adds a cell, types `2 + 2`, Shift+Enter, expects `4`,
// then checks a second page sees the same cell + output.
// Run:  node tests/e2e/smoke.mjs   (needs the venv and a built frontend)
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
const nb = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "yupyter-e2e-")), "x.md");

// click "+ New cell", type into the focused editor, run with Shift+Enter
async function runNew(page, src) {
  await page.click(".new-cell");
  await page.waitForFunction(() => document.activeElement?.classList.contains("cm-content"));
  await page.keyboard.type(src);
  await page.keyboard.press("Shift+Enter");
}

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
console.log("server:", url);

const exe = process.env.PW_CHROMIUM || (fs.existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
let failed = false;
try {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("pageerror:", e.message));
  await page.goto(url);
  await page.waitForFunction(() => document.querySelector(".app")?.dataset.synced === "true");
  await runNew(page, "2 + 2");
  const out = page.locator(".cell-code .outputs .out-text").first();
  await out.waitFor({ timeout: 60000 });
  const text = (await out.innerText()).trim();
  if (text !== "4") throw new Error(`expected 4, got ${JSON.stringify(text)}`);
  console.log("page 1 sees output:", text);

  // multi-line source, streamed output
  await runNew(page, "print('hi')\nprint('there')");
  await page.locator(".cell-code").nth(1).locator(".out-text", { hasText: "there" }).waitFor({ timeout: 30000 });

  // second page (same browser context => same cookie) sees the same state
  const page2 = await ctx.newPage();
  await page2.goto(url);
  await page2.waitForSelector(".cell-code");
  const n = await page2.locator(".cell-code").count();
  const t2 = (await page2.locator(".cell-code .outputs .out-text").first().innerText()).trim();
  if (n !== 2 || t2 !== "4") throw new Error(`page 2 mismatch: cells=${n} out=${t2}`);
  console.log("page 2 sees same cells + output");

  // edit in page 2's editor, see it in page 1 (browser -> server -> browser)
  const ed2 = page2.locator(".cell-code").first().locator(".cm-content");
  await ed2.click();
  await page2.keyboard.press("End");
  await page2.keyboard.type(" + 1");
  await page.locator(".cell-code").first().locator(".cm-content", { hasText: "2 + 2 + 1" }).waitFor({ timeout: 10000 });
  console.log("live edit propagated page 2 -> page 1");
  await page2.keyboard.press("Shift+Enter");
  await page.locator(".cell-code").first().locator(".out-text", { hasText: "5" }).waitFor({ timeout: 30000 });
  console.log("Shift+Enter re-run in page 2 updated output in page 1: 5");

  // switch a cell's type via the kernel dropdown: python -> markdown -> python
  const head = page.locator(".cell").first().locator(".cell-head");
  const label = head.locator("button.kernel");
  if ((await label.innerText()) !== "python") throw new Error("kernel label should read python");
  await label.click();
  await page.locator(".menu-pop li", { hasText: "markdown" }).click();
  await page.locator(".cell.cell-md").first().waitFor({ timeout: 10000 });
  // the label shows only while editing a markdown cell
  if (await page.locator(".cell.cell-md button.kernel").count()) throw new Error("rendered markdown cell should hide its label");
  await page.locator(".cell.cell-md .md-rendered").first().click();
  if ((await page.locator(".cell.cell-md button.kernel").first().innerText()) !== "markdown") throw new Error("label should read markdown");
  await page.locator(".cell.cell-md button.kernel").first().click();
  await page.locator(".menu-pop li", { hasText: /^python$/ }).click();
  await page.locator(".cell.cell-code").first().waitFor({ timeout: 10000 });
  console.log("kernel dropdown switched python -> markdown -> python");

  // cell id tooltip on the status dot
  await page.locator(".cell-code").first().locator(".runstate").hover();
  const tip = await page.locator(".cell-code").first().locator(".tip").innerText();
  if (!/^\w{6} · /.test(tip)) throw new Error("bad tooltip: " + tip);
  await page.screenshot({ path: path.join(os.tmpdir(), "yupyter-smoke.png") });
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
