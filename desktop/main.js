// Biggest River desktop app: runs `river serve` on a free local port and shows the page in a window.
const { app, BrowserWindow, dialog, shell } = require("electron");
const path = require("node:path");
const { startRiver } = require("./server");

// In development the river folder is the repo around desktop/; a packaged app carries a copy.
const riverRoot = app.isPackaged ? path.join(process.resourcesPath, "river-app") : path.join(__dirname, "..");
let river = null;

async function open() {
  try {
    river = await startRiver({ riverRoot });
  } catch (e) {
    dialog.showErrorBox("Biggest River could not start", e.message);
    app.quit();
    return;
  }
  console.log(`Biggest River app: ${river.url}`);
  const win = new BrowserWindow({ width: 1400, height: 900, title: "Biggest River",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
  // Links out of the page (GitHub, session links) open in the browser, not in the app window.
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(river.url)) { e.preventDefault(); shell.openExternal(url); } });
  win.loadURL(river.url);
}

app.whenReady().then(open);
app.on("window-all-closed", () => app.quit());
let stopping = false;
app.on("will-quit", (e) => {
  if (!river || stopping) return;
  stopping = true;
  e.preventDefault();
  river.stop().finally(() => app.quit());
});
