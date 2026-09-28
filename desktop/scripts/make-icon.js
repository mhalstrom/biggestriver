// Renders desktop/icon.svg to desktop/icon.png (1024x1024, transparent corners) with Electron, which the
// project has already; electron-builder makes the macOS .icns from icon.png. Run: npm run icon
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const svg = path.join(__dirname, "..", "icon.svg");
const out = path.join(__dirname, "..", "icon.png");

app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: 1024, height: 1024, show: false, frame: false, transparent: true,
    useContentSize: true, webPreferences: { offscreen: true } });
  win.webContents.setZoomFactor(1);
  const html = `<html><body style="margin:0;background:transparent">${fs.readFileSync(svg, "utf8")}</body></html>`;
  await win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
  await new Promise((r) => setTimeout(r, 300));
  const img = (await win.webContents.capturePage()).resize({ width: 1024, height: 1024 });
  fs.writeFileSync(out, img.toPNG());
  console.log(`wrote ${out} (${img.getSize().width}x${img.getSize().height})`);
  app.quit();
});
