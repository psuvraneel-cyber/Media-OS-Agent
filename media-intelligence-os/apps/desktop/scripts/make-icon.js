// Builds build/icon.png (512×512, transparent corners) from the icon artwork in
// build/icon-source.(png|webp|jpg) using Electron's own renderer, so no image tooling is needed.
// electron-builder turns the PNG into the Windows .ico (all sizes).
//
// The artwork is a rounded tile on a solid black square. Windows would show that black square
// behind the rounded icon, so the tile's edges and corner radius are detected and everything
// outside that rounded rectangle is made transparent.
//
//   npx electron scripts/make-icon.js

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const SIZE = 512;
const PADDING = 0.035; // breathing room so it sits at a natural size beside other app icons
const buildDir = path.join(__dirname, "..", "build");

const source = ["icon-source.png", "icon-source.webp", "icon-source.jpg"]
  .map((f) => path.join(buildDir, f))
  .find((f) => fs.existsSync(f));
if (!source) throw new Error("No build/icon-source.(png|webp|jpg) found");
const mime = { ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg" }[path.extname(source)];
const dataUrl = `data:${mime};base64,${fs.readFileSync(source).toString("base64")}`;

/** Runs inside the page (Chromium decodes the image and does the canvas work). */
function processIcon(src, size, padding) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      const probe = document.createElement("canvas");
      probe.width = w;
      probe.height = h;
      const p = probe.getContext("2d");
      p.drawImage(img, 0, 0);
      const px = p.getImageData(0, 0, w, h).data;
      const lum = (x, y) => {
        const i = (y * w + x) * 4;
        return 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2];
      };
      const T = 28; // "not background" threshold
      const midX = w >> 1;
      const midY = h >> 1;
      let left = 0;
      while (left < midX && lum(left, midY) < T) left++;
      let right = w - 1;
      while (right > midX && lum(right, midY) < T) right--;
      let top = 0;
      while (top < midY && lum(midX, top) < T) top++;
      let bottom = h - 1;
      while (bottom > midY && lum(midX, bottom) < T) bottom--;
      const tw = right - left + 1;
      const th = bottom - top + 1;
      // Walk the diagonal in from the top-left corner; a corner arc of radius r is first hit at
      // r·(1 − 1/√2) along each axis.
      let d = 0;
      while (d < tw / 2 && lum(left + d, top + d) < T) d++;
      const radius = d / (1 - Math.SQRT1_2);

      const out = document.createElement("canvas");
      out.width = size;
      out.height = size;
      const c = out.getContext("2d");
      const inset = size * padding;
      const scale = (size - inset * 2) / Math.max(tw, th);
      const dw = tw * scale;
      const dh = th * scale;
      const dx = (size - dw) / 2;
      const dy = (size - dh) / 2;
      c.beginPath();
      c.roundRect(dx, dy, dw, dh, Math.min(radius * scale, Math.min(dw, dh) / 2));
      c.clip();
      c.imageSmoothingQuality = "high";
      c.drawImage(img, left, top, tw, th, dx, dy, dw, dh);
      resolve({
        png: out.toDataURL("image/png"),
        info: { source: `${w}x${h}`, tile: { left, top, right, bottom }, radius: Math.round(radius) },
      });
    };
    img.onerror = () => reject(new Error("could not decode icon source"));
    img.src = src;
  });
}

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  try {
    const win = new BrowserWindow({ show: false, webPreferences: { sandbox: true, offscreen: true } });
    await win.loadURL("data:text/html,<!doctype html><title>icon</title>");
    const call = `(${processIcon.toString()})(${JSON.stringify(dataUrl)}, ${SIZE}, ${PADDING})`;
    const { png, info } = await win.webContents.executeJavaScript(call);
    const out = path.join(buildDir, "icon.png");
    const bytes = Buffer.from(png.split(",")[1], "base64");
    fs.writeFileSync(out, bytes);
    // Runtime copy for the splash screen and window icon (buildResources aren't packaged).
    fs.mkdirSync(path.join(__dirname, "..", "assets"), { recursive: true });
    fs.writeFileSync(path.join(__dirname, "..", "assets", "icon.png"), bytes);
    console.log(`source ${path.basename(source)}`, JSON.stringify(info));
    console.log(`wrote ${out} (${SIZE}x${SIZE})`);
  } catch (err) {
    console.error(err);
    process.exitCode = 1;
  } finally {
    app.quit();
  }
});
