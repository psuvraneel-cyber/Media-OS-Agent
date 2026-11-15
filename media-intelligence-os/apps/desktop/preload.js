// Runs in the sandboxed renderer before the page. Exposes a small bridge: a marker that this is the
// desktop shell, the window look (clear / acrylic) with a way to switch it, and the caption-button
// actions the page needs in the frameless "clear" look. No Node or Electron APIs reach the page.
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld(
  "mediaOsDesktop",
  Object.freeze({
    shell: "electron",
    platform: process.platform,
    getLook: () => ipcRenderer.invoke("mediaos:get-look"),
    setLook: (look) => ipcRenderer.invoke("mediaos:set-look", String(look)),
    minimize: () => ipcRenderer.invoke("mediaos:window", "minimize"),
    toggleMaximize: () => ipcRenderer.invoke("mediaos:window", "toggle-maximize"),
    close: () => ipcRenderer.invoke("mediaos:window", "close"),
    onMaximized: (callback) => {
      const listener = (_event, value) => callback(Boolean(value));
      ipcRenderer.on("mediaos:maximized", listener);
      return () => ipcRenderer.removeListener("mediaos:maximized", listener);
    },
  }),
);
