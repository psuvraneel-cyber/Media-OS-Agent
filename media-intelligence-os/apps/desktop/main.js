// Media OS desktop shell.
//
// A thin Electron window around the Next.js operator UI. Its job is the window itself: a
// see-through window so the real desktop shows behind the dashboard. Windows offers two
// see-through looks and no blur control, so both are available and switchable at runtime:
//
//   acrylic  (default) native Windows 11 Acrylic: the desktop shows through with the OS's
//            (fixed) blur.
//   clear    fully transparent window: the desktop shows through sharp, no blur. Windows can't
//            draw caption buttons on transparent windows, so the page draws its own.
//
// Switch with the toggle in the header or Ctrl+Shift+L. The choice is remembered.
// All app logic stays in the web UI and the API; this process loads no secrets and exposes no
// Node APIs to the page.
//
// Config (environment variables):
//   MEDIA_OS_URL        UI to load            (default http://127.0.0.1:3200/dashboard)
//   MEDIA_OS_AUTH_URL   origin the API trusts for sign-in (default http://127.0.0.1:3100)
//   MEDIA_OS_LOOK       acrylic (default) | clear   (overrides the remembered choice)

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { app, BrowserWindow, ipcMain, nativeTheme, shell, session } = require("electron");

const APP_URL = new URL(process.env.MEDIA_OS_URL || "http://127.0.0.1:3200/dashboard");
const AUTH_ORIGIN = new URL(process.env.MEDIA_OS_AUTH_URL || "http://127.0.0.1:3100").origin;
const ALLOWED_ORIGINS = new Set([APP_URL.origin, AUTH_ORIGIN]);
const LOOKS = ["clear", "acrylic"];
const HEADER_HEIGHT = 56; // matches --header-height in the web UI
const SHELL_UA_TOKEN = "MediaOSDesktop/0.1"; // the web UI reads this (and look/<name>) server-side

/* ------------------------------------------------------------------ preferences */

const prefsFile = () => path.join(app.getPath("userData"), "window-prefs.json");

function readLook() {
  if (LOOKS.includes(process.env.MEDIA_OS_LOOK)) return process.env.MEDIA_OS_LOOK;
  try {
    const saved = JSON.parse(fs.readFileSync(prefsFile(), "utf8")).look;
    if (LOOKS.includes(saved)) return saved;
  } catch {
    /* first run or unreadable: default */
  }
  return "acrylic";
}

function saveLook(look) {
  try {
    fs.mkdirSync(path.dirname(prefsFile()), { recursive: true });
    fs.writeFileSync(prefsFile(), JSON.stringify({ look }));
  } catch {
    /* not fatal: the choice just won't persist */
  }
}

/* ------------------------------------------------------------------ sign-in bridge */

/**
 * The API only accepts sign-in from the origin in its CORS allowlist (the production UI on
 * MEDIA_OS_AUTH_URL). When the window loads a different UI origin (e.g. a dev build on :3200),
 * route the login page to the trusted origin and the post-login landing back. Cookies are
 * host-scoped, not port-scoped, so the session carries across. No-op when both origins match.
 */
function installAuthBridge() {
  if (APP_URL.origin === AUTH_ORIGIN) return;
  session.defaultSession.webRequest.onBeforeRequest(
    { urls: [`${APP_URL.origin}/login*`, `${AUTH_ORIGIN}/dashboard*`] },
    (details, callback) => {
      if (details.resourceType !== "mainFrame") return callback({});
      const url = new URL(details.url);
      if (url.origin === APP_URL.origin && url.pathname === "/login") {
        return callback({ redirectURL: `${AUTH_ORIGIN}/login` });
      }
      if (url.origin === AUTH_ORIGIN && url.pathname.startsWith("/dashboard")) {
        return callback({ redirectURL: `${APP_URL.origin}${url.pathname}${url.search}` });
      }
      return callback({});
    },
  );
}

function isAllowed(url) {
  try {
    return ALLOWED_ORIGINS.has(new URL(url).origin);
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ window */

let mainWindow = null;
let currentLook = "acrylic";

function windowOptions(look, bounds) {
  const common = {
    ...(bounds ?? { width: 1440, height: 920 }),
    minWidth: 980,
    minHeight: 640,
    show: false,
    title: "Media OS",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      spellcheck: false,
    },
  };
  if (look === "clear") {
    // Transparent windows must be frameless on Windows; the page draws the caption buttons.
    return { ...common, frame: false, transparent: true, backgroundColor: "#00000000", hasShadow: true };
  }
  return {
    ...common,
    backgroundColor: "#00000000",
    backgroundMaterial: "acrylic",
    titleBarStyle: "hidden",
    titleBarOverlay: { color: "#00000000", symbolColor: "#e2e8f0", height: HEADER_HEIGHT },
  };
}

function createWindow(look, { bounds, maximized, url } = {}) {
  const win = new BrowserWindow(windowOptions(look, bounds));
  win.webContents.setUserAgent(`${win.webContents.getUserAgent()} ${SHELL_UA_TOKEN} look/${look}`);

  const sendMaximized = () => {
    if (!win.isDestroyed()) win.webContents.send("mediaos:maximized", win.isMaximized());
  };
  win.on("maximize", sendMaximized);
  win.on("unmaximize", sendMaximized);

  // Ctrl+Shift+L flips the look.
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type === "keyDown" && input.control && input.shift && !input.alt && input.key.toLowerCase() === "l") {
      event.preventDefault();
      switchLook(currentLook === "clear" ? "acrylic" : "clear");
    }
  });

  // Keep the window on this app; send anything else to the default browser.
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    if (/^https?:\/\//.test(target)) void shell.openExternal(target);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, target) => {
    if (!isAllowed(target)) {
      event.preventDefault();
      if (/^https?:\/\//.test(target)) void shell.openExternal(target);
    }
  });

  win.once("ready-to-show", () => {
    if (maximized) win.maximize();
    win.show();
  });
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });

  void win.loadURL(url && isAllowed(url) ? url : APP_URL.toString());
  return win;
}

/** Rebuilds the window in the other look at the same size, position and page. */
function switchLook(look) {
  if (!LOOKS.includes(look) || look === currentLook || !mainWindow) return;
  const old = mainWindow;
  const state = {
    bounds: old.getNormalBounds(),
    maximized: old.isMaximized(),
    url: old.webContents.getURL(),
  };
  currentLook = look;
  saveLook(look);
  const next = createWindow(look, state);
  mainWindow = next;
  // Swap once the new window is on screen, so there's no empty gap.
  next.once("show", () => {
    if (!old.isDestroyed()) old.destroy();
  });
}

/* ------------------------------------------------------------------ IPC (from our page only) */

function fromMainWindow(event) {
  return mainWindow && event.sender === mainWindow.webContents;
}

ipcMain.handle("mediaos:get-look", (event) => {
  if (!fromMainWindow(event)) return null;
  return { look: currentLook, maximized: mainWindow.isMaximized() };
});

ipcMain.handle("mediaos:set-look", (event, look) => {
  if (!fromMainWindow(event) || !LOOKS.includes(look)) return false;
  switchLook(look);
  return true;
});

ipcMain.handle("mediaos:window", (event, action) => {
  if (!fromMainWindow(event)) return false;
  const win = mainWindow;
  if (action === "minimize") win.minimize();
  else if (action === "toggle-maximize") (win.isMaximized() ? win.unmaximize() : win.maximize());
  else if (action === "close") win.close();
  else return false;
  return true;
});

/* ------------------------------------------------------------------ app lifecycle */

// One window per machine: a second launch focuses the existing one.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(() => {
    nativeTheme.themeSource = "dark";
    // Deny every permission prompt (camera, mic, notifications, ...): the UI needs none.
    session.defaultSession.setPermissionRequestHandler((_wc, _perm, callback) => callback(false));
    installAuthBridge();
    currentLook = readLook();
    mainWindow = createWindow(currentLook);
  });

  app.on("window-all-closed", () => app.quit());
}
