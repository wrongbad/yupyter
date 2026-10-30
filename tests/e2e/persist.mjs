// E2E persistence: run `2 + 2`, wait for autosave, stop the server, check the
// .md on disk, restart and check the cell + output reappear in the browser.
// Run:  node tests/e2e/persist.mjs   (needs the venv and a built frontend)
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
const nb = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "yupyter-persist-")), "p.md");

async function start() {
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
  return { proc, url };
}
async function stop(proc) {
  proc.kill("SIGINT");
  await new Promise((r) => proc.on("exit", r));
}

// click "+ New cell", type into the focused editor, run with Shift+Enter
async function runNew(page, src) {
  await page.click(".new-cell");
  await page.waitForFunction(() => document.activeElement?.classList.contains("cm-content"));
  await page.keyboard.type(src);
  await page.keyboard.press("Shift+Enter");
}

const exe = process.env.PW_CHROMIUM || (fs.existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
let failed = false;
let s;
try {
  s = await start();
  let page = await browser.newPage();
  page.on("pageerror", (e) => console.log("pageerror:", e.message));
  await page.goto(s.url);
  await page.waitForFunction(() => document.querySelector(".app")?.dataset.synced === "true");
  await runNew(page, "2 + 2");
  await page.locator(".cell-code .out-text").first().waitFor({ timeout: 60000 });

  // wait for the debounced autosave to land the output on disk
  const deadline = Date.now() + 20000;
  let text = "";
  while (Date.now() < deadline) {
    text = fs.existsSync(nb) ? fs.readFileSync(nb, "utf8") : "";
    if (/```output[^\n]*\n4\n```/.test(text) || /```output[^\n]*\n4\n/.test(text)) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  await page.close();
  await stop(s.proc);
  text = fs.readFileSync(nb, "utf8");
  if (!text.includes("2 + 2") || !/```output[^\n]*\n4\n```/.test(text)) throw new Error("md missing cell/output:\n" + text);
  console.log("md on disk:\n" + text);

  s = await start();
  page = await browser.newPage();
  await page.goto(s.url);
  await page.waitForSelector(".cell-code");
  const out = (await page.locator(".cell-code .out-text").first().innerText()).trim();
  const src = await page.locator(".cell-code .cm-content").first().innerText();
  if (out !== "4" || !src.includes("2 + 2")) throw new Error(`after restart: src=${JSON.stringify(src)} out=${JSON.stringify(out)}`);
  console.log("after restart: cell + output restored");
  console.log("OK");
} catch (e) {
  failed = true;
  console.error("FAIL:", e.message);
} finally {
  await browser.close();
  if (s) await stop(s.proc).catch(() => {});
}
process.exit(failed ? 1 : 0);
