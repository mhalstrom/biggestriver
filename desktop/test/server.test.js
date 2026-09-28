// Smoke test: the app's server starter runs river serve from this repo and stops it again.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { findPython, startRiver } = require("../server");

const repo = path.join(__dirname, "..", "..");
const fetchJson = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => { let b = ""; res.on("data", (d) => { b += d; }); res.on("end", () => resolve(JSON.parse(b))); }).on("error", reject);
});

test("finds a Python 3.10+", () => {
  assert.ok(findPython(), "python3 3.10 or newer is on this machine");
  assert.strictEqual(findPython(["/no/such/python"]), null);
});

test("starts river serve on a free port, in desktop mode, and stops it", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "river-desktop-"));
  const r = await startRiver({ riverRoot: repo, env: { ...process.env, RIVER_DB: path.join(dir, "t.db") } });
  try {
    const st = await fetchJson(r.url + "api/state");
    assert.strictEqual(st.desktop, true);
    const up = await fetchJson(r.url + "api/update");
    assert.strictEqual(up.git, false);  // the page hides its git Update button
    assert.strictEqual(up.desktop, true);
  } finally {
    await r.stop();
  }
  assert.ok(r.child.exitCode !== null || r.child.signalCode, "the server process ended");
  fs.rmSync(dir, { recursive: true, force: true });
});

test("says clearly when Python is missing", async () => {
  await assert.rejects(startRiver({ riverRoot: repo, find: () => null }), /needs Python 3\.10 or newer/);
});
