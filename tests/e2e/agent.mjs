// E2E for the agent cell, with the scripted fake backend (no real model):
// `%%claude please graph y=2+x` -> thinking streams, a python cell appears, an image renders.
// Run:  node tests/e2e/agent.mjs   (needs the venv and a built frontend)
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
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "yupyter-e2e-agent-"));
const nb = path.join(dir, "x.md");

const plot = "import matplotlib.pyplot as plt\nimport numpy as np\nx = np.linspace(-5, 5, 50)\nplt.plot(x, 2 + x)\nplt.title('y = 2 + x')\nplt.show()";
const script = {
  chunk: 6,
  delay: 0.04,
  steps: [
    { thinking: "The user wants y = 2 + x plotted. I will insert a matplotlib cell and run it to check the result." },
    { tool: "insert_cell", args: { source: plot, kernel: "py" }, save_as: "plot" },
    { tool: "run_cell", args: { id: "$plot" } },
    { text: "Plotted **y = 2 + x**: a line with slope 1 through (0, 2)." },
  ],
};
const scriptPath = path.join(dir, "script.json");
fs.writeFileSync(scriptPath, JSON.stringify(script));

// click "+ New cell", type into the focused editor, run with Shift+Enter
async function runNew(page, src) {
  await page.click(".new-cell");
  await page.waitForFunction(() => document.activeElement?.classList.contains("cm-content"));
  await page.keyboard.type(src);
  await page.keyboard.press("Shift+Enter");
}

const proc = spawn(bin, [nb, "--no-browser"], {
  stdio: ["ignore", "pipe", "inherit"],
  env: { ...process.env, YUPYTER_AGENT_BACKEND: `fake:${scriptPath}` },
});
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

  await runNew(page, "%%claude please graph y=2+x");
  await page.locator("button.kernel", { hasText: "claude" }).first().waitFor({ timeout: 10000 });

  // thinking streams in, open while streaming
  const thinking = page.locator(".agent-thinking").first();
  await thinking.waitFor({ timeout: 30000 });
  await page.waitForSelector(".agent-thinking[open]", { timeout: 30000 });
  console.log("thinking block visible and open while streaming");

  // a new python cell appears (inserted by the agent) and gets an image
  const cells = page.locator(".cell-code");
  await page.waitForFunction(() => document.querySelectorAll(".cell-code").length === 2, null, { timeout: 60000 });
  const agentCell = cells.nth(0);
  const pyCell = cells.nth(1);
  const img = pyCell.locator(".outputs img");
  await img.waitFor({ timeout: 60000 });
  console.log("agent-created python cell renders an image");
  if (!(await pyCell.locator(".cm-content", { hasText: "plt.plot" }).count())) throw new Error("python cell source missing");

  // final text rendered as markdown, thinking collapsed once done, tool lines present
  await agentCell.locator(".agent-text strong", { hasText: "y = 2 + x" }).waitFor({ timeout: 30000 });
  await page.waitForFunction(() => !document.querySelector(".agent-thinking[open]"), null, { timeout: 10000 });
  const tools = await agentCell.locator(".agent-tool").allInnerTexts();
  if (tools.length !== 2 || !/^inserted cell \w+ \(py\)$/.test(tools[0]) || !/^ran \w+ ✓$/.test(tools[1]))
    throw new Error("unexpected tool lines: " + JSON.stringify(tools));
  console.log("tool lines:", tools.join(" | "));
  await page.waitForFunction(() => document.querySelector(".cell-code")?.dataset.cellId && document.querySelector(".cell-code .runstate")?.dataset.state === "done", null, { timeout: 10000 });
  await page.screenshot({ path: path.join(os.tmpdir(), "yupyter-agent.png") });

  // a second tab sees the same thing
  const page2 = await ctx.newPage();
  await page2.goto(url);
  await page2.locator(".cell-code .outputs img").waitFor({ timeout: 30000 });
  if ((await page2.locator(".agent-tool").count()) !== 2) throw new Error("page 2 missing agent tool lines");

  // --- undo this run -------------------------------------------------------
  const nCells = (p) => p.locator(".cell-code").count();
  const undoBtn = page.locator(".run-undo button");
  await undoBtn.filter({ hasText: "Undo this run" }).waitFor({ timeout: 10000 });
  const sum = await page.locator(".run-summary").innerText();
  if (sum !== "Claude inserted 1 cell") throw new Error("unexpected summary: " + sum);
  if (!/NOT reverted/.test((await undoBtn.getAttribute("title")) || "")) throw new Error("tooltip missing kernel-state warning");
  await undoBtn.hover();
  if ((await page.locator(".cell.run-hl").count()) !== 1) throw new Error("hover did not highlight the run's cell");
  console.log("undo button shown with summary + tooltip, hover highlights the run's cell");

  await undoBtn.click();
  await page.waitForFunction(() => document.querySelectorAll(".cell-code").length === 1, null, { timeout: 10000 });
  await page.locator(".run-undo button", { hasText: "Redo" }).waitFor({ timeout: 10000 });
  await page2.locator(".run-undo button", { hasText: "Redo" }).waitFor({ timeout: 10000 }); // shared state
  if ((await nCells(page2)) !== 1) throw new Error("collaborator still sees Claude's cell");
  console.log("undo removed Claude's cell; collaborator sees it and the Redo button");

  await page.locator(".run-undo button", { hasText: "Redo" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".cell-code").length === 2, null, { timeout: 10000 });
  await page.locator(".cell-code").nth(1).locator(".outputs img").waitFor({ timeout: 10000 });
  await page.locator(".run-undo button", { hasText: "Undo this run" }).waitFor({ timeout: 10000 });
  console.log("redo restored the cell with its output");

  // Ctrl+Z by the user who ran it undoes the run; another user's Ctrl+Z does not
  const cellCount = (p) => p.evaluate(() => document.querySelectorAll(".cell-code").length);
  await page2.keyboard.press("Escape");
  await page2.keyboard.press("Control+z");
  await page2.waitForTimeout(500);
  if ((await cellCount(page)) !== 2) throw new Error("another user's Ctrl+Z undid the run");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+z");
  await page.waitForFunction(() => document.querySelectorAll(".cell-code").length === 1, null, { timeout: 10000 });
  await page.locator(".run-undo button", { hasText: "Redo" }).waitFor({ timeout: 10000 });
  await page.keyboard.press("Control+Shift+z");
  await page.waitForFunction(() => document.querySelectorAll(".cell-code").length === 2, null, { timeout: 10000 });
  console.log("Ctrl+Z / Ctrl+Shift+Z by the triggering user undo / redo the run");
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
