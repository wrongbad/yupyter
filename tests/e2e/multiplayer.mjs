// E2E: presence, remote cursors, per-user undo, move cell, palette. Two browser contexts = two users.
// Run:  node tests/e2e/multiplayer.mjs   (needs the venv and a built frontend)
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
const nb = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "yupyter-mp-")), "x.md");

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
const T = 15000;
let step = "";
const ok = (m) => { step = m; console.log("ok:", m); };
let failed = false;

async function open(name) {
  const ctx = await browser.newContext(); // separate localStorage => separate identity
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log(`pageerror[${name}]:`, e.message));
  await page.goto(url);
  await page.waitForFunction(() => document.querySelector(".app")?.dataset.synced === "true");
  await page.keyboard.press("Control+k");
  await page.locator(".palette-input").waitFor();
  await page.keyboard.type("set your name");
  await page.keyboard.press("Enter");
  await page.locator(".palette-input").waitFor();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type(name);
  await page.keyboard.press("Enter");
  await page.locator(".palette").waitFor({ state: "detached" });
  return page;
}
const texts = (p) => p.locator(".cell-code .cm-content").allInnerTexts().then((a) => a.map((t) => t.trim()));

let p1, p2;
globalThis.DBG = async () => JSON.stringify([await texts(p1), await texts(p2)]);
try {
  p1 = await open("alice");
  p2 = await open("bob");
  await p2.locator('.avatar[data-name="alice"]').waitFor({ timeout: T });
  await p1.locator('.avatar[data-name="bob"]').waitFor({ timeout: T });
  ok("avatar rows show both users");

  // create first cell in p1 via command mode 'b'
  await p1.locator("body").click({ position: { x: 5, y: 300 } });
  await p1.keyboard.press("b");
  await p1.waitForFunction(() => document.activeElement?.classList.contains("cm-content"), null, { timeout: T });
  await p1.keyboard.type("alice_one");
  await p2.locator(".cell-code .cm-content", { hasText: "alice_one" }).waitFor({ timeout: T });

  // remote cursor label in p1 when p2 types
  await p2.locator(".cell-code .cm-content").first().click();
  await p2.keyboard.press("End");
  await p2.keyboard.type("!");
  await p1.locator(".cm-ySelectionInfo", { hasText: "bob" }).waitFor({ state: "attached", timeout: T });
  const col = await p1.locator(".cm-ySelectionInfo").first().evaluate((e) => getComputedStyle(e).backgroundColor);
  if (!col || col === "rgba(0, 0, 0, 0)") throw new Error("remote cursor label has no colour");
  ok("remote cursor label 'bob' rendered in p1 with colour " + col);
  await p1.locator('.cell-frame.has-remote[data-remote="bob"]').waitFor({ timeout: T });
  ok("cell shows remote-focus marker");

  // undo: p1 inserts a cell, p2 inserts one + types; p1 undo removes only p1's
  await p1.keyboard.press("Escape");
  await p1.keyboard.press("b");
  await p1.locator(".cell-code").nth(1).waitFor({ timeout: T });
  await p1.waitForFunction(() => document.activeElement?.classList.contains("cm-content"), null, { timeout: T });
  await p2.locator(".cell-code").nth(1).waitFor({ timeout: T });
  await p2.keyboard.press("Escape");
  await p2.keyboard.press("b"); // below the selected (first) cell
  await p2.locator(".cell-code").nth(2).waitFor({ timeout: T });
  await p2.waitForFunction(() => document.activeElement?.classList.contains("cm-content"), null, { timeout: T });
  await p2.keyboard.type("bobcell");
  await p1.locator(".cell-code .cm-content", { hasText: "bobcell" }).waitFor({ timeout: T });
  await p1.keyboard.press("Escape");
  await p1.keyboard.press("Control+z");
  await p1.waitForFunction(() => document.querySelectorAll(".cell-code").length === 2, null, { timeout: T });
  await p2.waitForFunction(() => document.querySelectorAll(".cell-code").length === 2, null, { timeout: T });
  const t1 = await texts(p1);
  if (!t1.some((t) => t.startsWith("bobcell"))) throw new Error("undo removed bob's cell: " + t1);
  ok("p1 undo reverted only p1's insert: " + JSON.stringify(t1));

  // move cell via shortcut (Alt+Up) in p1; both pages reorder
  await p1.locator(".cell-code .cm-content", { hasText: "bobcell" }).click();
  await p1.keyboard.press("Escape");
  await p1.keyboard.press("Alt+ArrowUp");
  for (const p of [p1, p2]) {
    await p.waitForFunction(() => document.querySelector(".cell-code .cm-content")?.textContent.includes("bobcell"), null, { timeout: T });
  }
  ok("move up reordered in both pages");

  // palette runs a command
  await p1.keyboard.press("Control+k");
  await p1.locator(".palette-input").waitFor();
  await p1.keyboard.type("toggle light");
  await p1.keyboard.press("Enter");
  await p1.waitForFunction(() => !!document.documentElement.dataset.theme, null, { timeout: T });
  ok("palette command toggled theme: " + (await p1.evaluate(() => document.documentElement.dataset.theme)));
  await p1.screenshot({ path: path.join(os.tmpdir(), "yupyter-mp.png") });
  console.log("OK");
} catch (e) {
  failed = true;
  console.error("FAIL after", step, ":", e.message.split("\n")[0], (e.stack.match(/multiplayer.mjs:\d+/)||[])[0]);
  if (globalThis.DBG) console.error(await DBG());
} finally {
  await browser.close();
  proc.kill("SIGINT");
  await new Promise((r) => proc.on("exit", r));
}
process.exit(failed ? 1 : 0);
