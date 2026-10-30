// E2E: file header + save status, Ctrl+S, rename, external edits (reload / conflict),
// file drawer navigation + "+ New notebook", multi-notebook server rooted at cwd.
// Run:  node tests/e2e/files.mjs   (needs the venv and a built frontend)
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const bin = process.env.YUPYTER_BIN || path.join(repo, ".venv/bin/yupyter");
const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "yupyter-files-")));
fs.mkdirSync(path.join(dir, "sub"));
const NB = (src) => `---\nyupyter: 1\n---\n\n\`\`\`py {#aaaaaa}\n${src}\n\`\`\`\n`;
fs.writeFileSync(path.join(dir, "sub", "b.md"), NB("b = 1"));
fs.writeFileSync(path.join(dir, "plain.md"), "# just markdown\n");
fs.writeFileSync(path.join(dir, "data.csv"), "a,b\n");

// `yupyter a.md` in `dir`: root = cwd, a.md opened
const proc = spawn(bin, ["a.md", "--no-browser"], { cwd: dir, stdio: ["ignore", "pipe", "inherit"] });
const url = await new Promise((resolve, reject) => {
  let buf = "";
  const t = setTimeout(() => reject(new Error("server did not start")), 30000);
  proc.stdout.on("data", (d) => {
    buf += d;
    const m = buf.match(/Open: (http\S+)/);
    if (m) { clearTimeout(t); resolve(m[1]); }
  });
});
const origin = new URL(url).origin;
const exe = process.env.PW_CHROMIUM || (fs.existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
let failed = false;
const T = 15000;
const ok = (m) => console.log("ok:", m);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const read = (f) => fs.readFileSync(path.join(dir, f), "utf8");
async function until(fn, what, ms = T) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await fn()) return; await sleep(100); }
  throw new Error("timeout: " + what);
}
const state = (page) => page.locator(".save-status").getAttribute("data-state");

try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => console.log("pageerror:", e.message));
  await page.goto(url);
  await page.waitForFunction(() => document.querySelector(".app")?.dataset.synced === "true");
  if (!page.url().includes("/n/a.md")) throw new Error("expected /n/a.md, got " + page.url());
  await page.locator('[data-testid="file-name"]', { hasText: "a.md" }).waitFor();
  ok("header shows the file name");

  // typing -> Saving… -> Saved HH:MM, and the file is written
  await page.click(".new-cell");
  await page.waitForFunction(() => document.activeElement?.classList.contains("cm-content"));
  await page.keyboard.type("x = 41");
  await page.locator('.save-status[data-state="saving"]').waitFor({ timeout: T });
  await page.locator('.save-status[data-state="saved"]').waitFor({ timeout: T });
  const label = await page.locator(".save-status").innerText();
  if (!/^Saved \d{1,2}:\d{2}/.test(label)) throw new Error("bad saved label: " + label);
  if (!(await page.locator(".save-status").getAttribute("title")).startsWith("Last saved")) throw new Error("no tooltip");
  await until(() => fs.existsSync(path.join(dir, "a.md")) && read("a.md").includes("x = 41"), "autosave");
  ok("Saving… then " + label);

  // Ctrl+S flushes immediately (debounce is 2s)
  await page.keyboard.type("2");
  await page.keyboard.press("Control+s");
  await until(() => read("a.md").includes("x = 412"), "ctrl+s flush", 1200);
  ok("Ctrl+S flushes within the debounce window");

  // external edit with no pending changes -> reloaded into the live doc
  await page.waitForFunction(() => document.querySelector(".save-status")?.dataset.state === "saved");
  await sleep(300);
  fs.writeFileSync(path.join(dir, "a.md"), read("a.md").replace("x = 412", "x = 777"));
  await page.locator(".cell-code .cm-content", { hasText: "x = 777" }).waitFor({ timeout: T });
  ok("external edit reloaded into the page");

  // external edit while we have unsaved changes -> conflict banner, saving paused
  await page.locator(".cell-code .cm-content").first().click();
  await page.keyboard.press("End");
  await page.keyboard.type("0");
  fs.writeFileSync(path.join(dir, "a.md"), NB("x = 'disk'"));
  await page.locator('[data-testid="conflict"]').waitFor({ timeout: T });
  await sleep(2600);
  if (!read("a.md").includes("x = 'disk'")) throw new Error("saving was not paused during conflict");
  await page.locator('[data-testid="conflict"] button', { hasText: "Keep mine" }).click();
  await page.locator('[data-testid="conflict"]').waitFor({ state: "detached", timeout: T });
  await until(() => read("a.md").includes("x = 7770"), "keep mine overwrote the file");
  ok("conflict: saving paused, Keep mine wrote our version");

  fs.writeFileSync(path.join(dir, "a.md"), NB("x = 'theirs'"));
  await page.locator(".cell-code .cm-content", { hasText: "x = 'theirs'" }).waitFor({ timeout: T });
  await page.locator(".cell-code .cm-content").first().click();
  await page.keyboard.press("End");
  await page.keyboard.type("!");
  fs.writeFileSync(path.join(dir, "a.md"), NB("x = 'theirs2'"));
  await page.locator('[data-testid="conflict"]').waitFor({ timeout: T });
  await page.locator('[data-testid="conflict"] button', { hasText: "Reload" }).click();
  await page.locator(".cell-code .cm-content", { hasText: "x = 'theirs2'" }).waitFor({ timeout: T });
  await page.locator('[data-testid="conflict"]').waitFor({ state: "detached", timeout: T });
  ok("conflict: Reload took the disk version");

  // drawer: tree, greyed non-notebooks, navigation + back/forward
  await page.click('[data-testid="drawer-toggle"]');
  await page.locator('[data-testid="drawer"]').waitFor();
  await page.locator('[data-testid="tree-file"][data-path="a.md"].current').waitFor();
  if (!(await page.locator('[data-testid="tree-file"][data-path="data.csv"].disabled').count())) throw new Error("csv not greyed");
  if (!(await page.locator('[data-testid="tree-file"][data-path="plain.md"]:not(.disabled)').count())) throw new Error("plain md not clickable");
  await page.locator('[data-testid="tree-dir"]', { hasText: "sub" }).click();
  await page.locator('[data-testid="tree-file"][data-path="sub/b.md"].notebook').click();
  await page.waitForURL("**/n/sub/b.md");
  await page.locator(".cell-code .cm-content", { hasText: "b = 1" }).waitFor({ timeout: T });
  await page.locator('[data-testid="file-name"]', { hasText: "sub/b.md" }).waitFor();
  ok("drawer navigation to sub/b.md");
  await page.goBack();
  await page.waitForURL("**/n/a.md");
  await page.locator('[data-testid="file-name"]', { hasText: "a.md" }).waitFor();
  await page.goForward();
  await page.waitForURL("**/n/sub/b.md");
  ok("back/forward between notebooks");

  // + New notebook in the current folder (sub)
  await page.locator('[data-testid="new-notebook"]').click();
  await page.locator(".palette-input").waitFor();
  await page.keyboard.press("Control+a");
  await page.keyboard.press("Meta+a");
  await page.keyboard.type("made");
  await page.keyboard.press("Enter");
  await page.waitForURL("**/n/sub/made.md");
  if (!read("sub/made.md").includes("yupyter: 1")) throw new Error("new notebook has no frontmatter");
  ok("+ New notebook created sub/made.md");

  // rename (moves the file, URL follows); collisions are rejected
  await page.click('[data-testid="file-name"]');
  await page.fill('[data-testid="file-name-input"]', "sub/b.md");
  await page.keyboard.press("Enter");
  await page.locator('[data-testid="file-error"]').waitFor({ timeout: T });
  ok("rename onto an existing file is rejected: " + (await page.locator('[data-testid="file-error"]').innerText()));
  await page.fill('[data-testid="file-name-input"]', "sub/renamed.md");
  await page.keyboard.press("Enter");
  await page.waitForURL("**/n/sub/renamed.md");
  await until(() => fs.existsSync(path.join(dir, "sub/renamed.md")) && !fs.existsSync(path.join(dir, "sub/made.md")), "file moved");
  await page.locator('[data-testid="file-name"]', { hasText: "sub/renamed.md" }).waitFor();
  ok("rename moved the file and the URL");

  // the server refuses to leave its root
  for (const u of ["/api/tree?dir=..", "/api/info/%2e%2e/%2e%2e/etc/passwd.md"]) {
    const st = await page.evaluate((u) => fetch(u).then((r) => r.status), u);
    if (st !== 403 && st !== 404) throw new Error(`${u} -> ${st}`);
  }
  ok("traversal rejected");

  // home page (no file): drawer open, no editor
  await page.goto(origin + "/");
  await page.locator('[data-testid="drawer"]').waitFor();
  await page.locator('[data-testid="tree-file"][data-path="a.md"]').waitFor();
  ok("home page shows the drawer");
  console.log("OK");
} catch (e) {
  failed = true;
  console.log("FAIL:", e.message);
} finally {
  await browser.close();
  proc.kill("SIGINT");
  await new Promise((r) => proc.on("exit", r));
}
process.exit(failed ? 1 : 0);
