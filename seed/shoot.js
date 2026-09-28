// Takes the pictures for the README and biggestriver.com from the page, with the demo data of
// seed/screenshots.sh, and writes them to site/img/. Uses the desktop app's Electron (no other packages):
//   cd desktop && npm ci && npx electron ../seed/shoot.js
// The server runs with a throwaway HOME (/tmp/.../alex), so no path of this computer shows in a picture.
// The page never clicks Start or Dispatch here: those open real terminal windows.
const { app, BrowserWindow } = require("electron");
const { execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const repo = path.join(__dirname, "..");
const out = path.join(repo, "site", "img");
const tmp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "river-shots-")));
const home = path.join(tmp, "alex");
const W = 1280, H = 820;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freePort() {
  return new Promise((resolve) => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); }); });
}

// A throwaway HOME whose login profile finds stand-in claude, codex and river commands (not this Mac's).
function makeHome() {
  const bin = path.join(home, "bin");
  fs.mkdirSync(bin, { recursive: true });
  fs.mkdirSync(path.join(home, "code", "shop"), { recursive: true });
  for (const c of ["claude", "codex", "river"]) fs.writeFileSync(path.join(bin, c), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  fs.writeFileSync(path.join(home, ".zprofile"), `export PATH="${bin}:$PATH"\n`);
}
const riverIn = (env) => (...a) => execFileSync("python3", [path.join(repo, "bin", "river"), "-q", ...a], { env, encoding: "utf8" });

// river serve on a database; returns its URL and a stop function.
async function serve(env) {
  const port = await freePort();
  const srv = spawn("python3", [path.join(repo, "bin", "river"), "serve", "--port", String(port)], { env, stdio: "ignore" });
  return { url: `http://127.0.0.1:${port}/`, stop: () => srv.kill() };
}

async function main() {
  fs.mkdirSync(out, { recursive: true });
  makeHome();
  const base = { ...process.env, HOME: home, SHELL: "/bin/zsh", TZ: "UTC" };
  for (const k of ["RIVER_AGENT", "RIVER_FOCUS", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "RIVER_DB"]) delete base[k];
  const win = new BrowserWindow({ width: W, height: H, show: false, paintWhenInitiallyHidden: true,
    webPreferences: { backgroundThrottling: false } });
  const js = (code) => win.webContents.executeJavaScript(code);
  let url;
  const ready = async () => { for (let i = 0; i < 50; i++) { try { await win.loadURL(url); return; } catch (e) { await sleep(200); } } };

  // Open the page on a link (#tab-graph&as-alex) in a theme, wait until it has drawn, run a step, take a picture.
  async function open(hash, theme = "light") {
    await js(`localStorage.setItem("river.theme", ${JSON.stringify(theme)})`);
    // A new hash alone does not load the page again: go through a blank page.
    await win.loadURL("about:blank");
    await win.loadURL(url + (hash ? "#" + hash : ""));
    for (let i = 0; i < 50; i++) { if (await js(`!!document.querySelector("#work .row, #work .card, #work > *")`)) break; await sleep(200); }
    await sleep(1200);
    // Paths: the throwaway HOME reads as ~, like on a person's own computer.
    await js(`(() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) n.nodeValue = n.nodeValue.split(${JSON.stringify(home)}).join("~").split(${JSON.stringify(os.homedir())}).join("~"); })()`);
  }
  async function shot(name, selector, pad = 0) {
    let rect;
    if (selector) {
      const r = await js(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return null;
        e.scrollIntoView({ block: "start" }); const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; })()`);
      if (!r) throw new Error(`${name}: nothing matches ${selector}`);
      await sleep(200);
      rect = { x: Math.max(0, Math.floor(r.x - pad)), y: Math.max(0, Math.floor(r.y - pad)),
        width: Math.ceil(r.width + 2 * pad), height: Math.ceil(Math.min(r.height + 2 * pad, H)) };
    }
    const img = await win.webContents.capturePage(rect);
    fs.writeFileSync(path.join(out, name), img.toPNG());
    console.log("wrote", name, img.getSize());
  }
  const frames = [];
  async function frame() { const img = await win.webContents.capturePage(); frames.push(img.toPNG()); }

  // The setup guide on a first start: only the person is known yet. It opens by itself.
  const first = { ...base, RIVER_DB: path.join(tmp, "first.db") };
  riverIn(first)("register", "alex", "--human");
  let s1 = await serve(first); url = s1.url; await ready();
  await open("as-alex");
  await shot("setup-guide.png", "#setup .box", 0);
  s1.stop();

  // Everything else: the demo data, with the guide dismissed.
  // The default database of the throwaway HOME, so the go briefing prints no QUEUE line about RIVER_DB.
  const env = { ...base };
  const river = riverIn(env);
  river("config", "set", "setup_done", "on");  // first, so the Recent panel shows only the demo work
  execFileSync("sh", [path.join(repo, "seed", "screenshots.sh")], { env, stdio: "ignore" });
  const s2 = await serve(env); url = s2.url; await ready();

  for (const theme of ["light", "dark"]) {
    await open("as-alex", theme);
    await shot(`board-${theme}.png`);
    await shot(`needs-you-${theme}.png`, "#needsYouPanel", 8);
  }
  await open("tab-capacity&as-alex"); await shot("parallel-work.png", "#tab-capacity", 8);
  await open("tab-graph&as-alex"); await sleep(1500); await shot("graph.png", "#tab-graph", 8);
  await open("tab-targets&as-alex"); await shot("release.png", "#tab-targets", 8);
  await open("tab-projects&as-alex");
  await js(`(() => { const s = document.querySelector("#goalFilter"); s.value = "checkout-launch"; s.dispatchEvent(new Event("change")); })()`);
  await sleep(600); await shot("goals.png", "#tab-projects", 8);

  // The tour: Board, an item, Projects, Graph, Targets, Done; two frames each, so each view stays a moment.
  for (const h of ["as-alex", "as-alex&item-4", "tab-projects&as-alex", "tab-graph&as-alex", "tab-targets&as-alex", "tab-done&as-alex"]) {
    await open(h); if (h.includes("graph")) await sleep(1500);
    await frame(); await frame();
  }
  const fd = path.join(tmp, "frames"); fs.mkdirSync(fd);
  frames.forEach((f, i) => fs.writeFileSync(path.join(fd, `f${String(i).padStart(3, "0")}.png`), f));
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", "0.8", "-i", path.join(fd, "f%03d.png"),
    "-vf", "scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=none",
    "-loop", "0", path.join(out, "board-tour.gif")]);
  console.log("wrote board-tour.gif");

  // The go briefing an agent reads, drawn as a terminal window.
  const brief = river("--as", "web-agent", "go", "--project", "website");
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  await win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html><body style="margin:0;background:#1e1f24">
    <div id="t" style="margin:24px;border-radius:10px;background:#15161a;box-shadow:0 8px 30px #0008;overflow:hidden;width:${W - 48}px">
    <div style="height:28px;background:#2a2b31;display:flex;align-items:center;gap:8px;padding-left:12px">
    <i style="width:12px;height:12px;border-radius:6px;background:#ff5f57"></i><i style="width:12px;height:12px;border-radius:6px;background:#febc2e"></i><i style="width:12px;height:12px;border-radius:6px;background:#28c840"></i></div>
    <pre style="margin:0;padding:16px 18px;color:#d8dae0;font:13px/1.45 Menlo,monospace;white-space:pre-wrap"><span style="color:#8bd5a0">~/code/shop $</span> river go\n${esc(brief.trim())}</pre></div></body>`));
  await sleep(500); await shot("go-briefing.png", "#t", 0);

  s2.stop();
  fs.rmSync(tmp, { recursive: true, force: true });
}

app.whenReady().then(main).then(() => app.quit(), (e) => { console.error(e); app.exit(1); });
