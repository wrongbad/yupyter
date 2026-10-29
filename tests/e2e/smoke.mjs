// E2E smoke: launches `yupyter`, types `2 + 2` in the prompt bar, expects `4`,
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
  await page.waitForSelector("#prompt");
  await page.waitForFunction(() => document.querySelector(".app")?.dataset.synced === "true");
  await page.fill("#prompt", "2 + 2");
  await page.press("#prompt", "Enter");
  const out = page.locator(".cell-code .outputs .out-text").first();
  await out.waitFor({ timeout: 60000 });
  const text = (await out.innerText()).trim();
  if (text !== "4") throw new Error(`expected 4, got ${JSON.stringify(text)}`);
  console.log("page 1 sees output:", text);

  // multi-line + stream output via Shift+Enter newline
  await page.fill("#prompt", "print('hi')\nprint('there')");
  await page.press("#prompt", "Enter");
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
