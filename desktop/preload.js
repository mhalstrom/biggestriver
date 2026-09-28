// The page's one bridge to the app: the system folder picker for "Add a project folder"
// (river/static/components/folderForm.js). Nothing else of Electron or Node reaches the page.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("riverDesktop", {
  pickFolder: () => ipcRenderer.invoke("river:pick-folder"),
});
