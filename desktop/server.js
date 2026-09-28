// Starts `river serve` for the desktop app and stops it again. Plain Node, no Electron,
// so `npm test` can check it without downloading Electron.
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const http = require("node:http");

// An app started from the Dock gets a short PATH, so look in the usual places too.
const PYTHONS = ["python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"];

// The first python3 that is 3.10 or newer, or null.
function findPython(candidates = PYTHONS) {
  for (const py of candidates) {
    try {
      const out = execFileSync(py, ["-c", "import sys; print(int(sys.version_info >= (3, 10)))"],
        { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"] });
      if (out.trim() === "1") return py;
    } catch (e) { /* not there, or too old */ }
  }
  return null;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on("error", reject);
    s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

function get(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => { res.resume(); resolve(res.statusCode); });
    req.on("error", () => resolve(0));
    req.setTimeout(1000, () => { req.destroy(); resolve(0); });
  });
}

// Start the server from the river folder (the repo, or the app's bundled copy). Resolves to
// { url, port, child, stop } once /api/state answers; rejects with a message a person can act on.
async function startRiver({ riverRoot, python, env = process.env, timeoutMs = 20000, find = findPython } = {}) {
  const py = python || find();
  if (!py) throw new Error("Biggest River needs Python 3.10 or newer. Install it (for example: brew install python), then open the app again.");
  const bin = path.join(riverRoot, "bin", "river");
  if (!fs.existsSync(bin)) throw new Error(`river is missing from ${riverRoot}`);
  const port = await freePort();
  const child = spawn(py, [bin, "serve", "--port", String(port)],
    { cwd: riverRoot, env: { ...env, RIVER_DESKTOP: "1" }, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.on("data", (d) => { log += d; });
  child.stderr.on("data", (d) => { log += d; });
  const url = `http://127.0.0.1:${port}/`;
  const stop = () => new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode) return resolve();
    child.once("exit", () => resolve());
    child.kill("SIGTERM");
    setTimeout(() => { if (child.exitCode === null) child.kill("SIGKILL"); }, 3000).unref();
  });
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    if (child.exitCode !== null) throw new Error(`river serve stopped (exit ${child.exitCode}):\n${log.trim().slice(-800)}`);
    if (await get(url + "api/state") === 200) return { url, port, child, stop };
    await new Promise((r) => setTimeout(r, 200));
  }
  await stop();
  throw new Error(`river serve did not answer on ${url} within ${timeoutMs / 1000}s:\n${log.trim().slice(-800)}`);
}

module.exports = { findPython, freePort, startRiver };
