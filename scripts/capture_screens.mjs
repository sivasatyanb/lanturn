// Capture the app at phone size (390 x 844 @2x) with headless Chrome over the DevTools
// protocol. No dependencies: uses Node's built-in fetch and WebSocket (Node 22+).
//
//   node scripts/capture_screens.mjs <name> [query]      e.g.  01-route-comparison "t=2230"
//   node scripts/capture_screens.mjs <name> [query] --click "#banner"
//
// Writes docs/screenshots/<name>.png. The app must be running on http://localhost:8000.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const [name, query = "", ...rest] = process.argv.slice(2);
if (!name) { console.error("usage: capture_screens.mjs <name> [query] [--click selector] [--wait ms] [--settle ms]"); process.exit(2); }
const arg = (flag, dflt) => { const i = rest.indexOf(flag); return i >= 0 ? rest[i + 1] : dflt; };
const clickSel = arg("--click", null);
const waitMs = Number(arg("--wait", "14000"));
const CHROME = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find((p) => p && existsSync(p));
if (!CHROME) { console.error("Chrome/Edge not found; set CHROME_PATH"); process.exit(2); }

const port = 9300 + Math.floor(Math.random() * 400);
const profile = join(ROOT, "var", "chrome-capture-profile");
mkdirSync(profile, { recursive: true });
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--hide-scrollbars", "--ignore-gpu-blocklist", "--enable-webgl", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
  "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch (_) { /* not up yet */ }
    await sleep(200);
  }
  throw new Error("Chrome did not start");
}

try {
  const ws = new WebSocket(await target());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, (m) => (m.error ? rej(new Error(method + ": " + m.error.message)) : res(m.result)));
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evalJs = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result.value;

  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  await send("Page.navigate", { url: `http://localhost:8000/${query ? "?" + query : ""}` });
  // Wait for routes, then for the basemap tiles (bounded).
  const t0 = Date.now();
  while (Date.now() - t0 < waitMs) {
    await sleep(500);
    const st = await evalJs(`(() => { const s = window.__lantern; if (!s || !s.data || !s.mapReady) return "loading";
      try { return s.map.getStyle().layers.length > 10 && s.map.areTilesLoaded() ? "ready" : "tiles"; } catch (e) { return "style"; } })()`);
    if (st === "ready") break;
  }
  await sleep(Number(arg("--settle", "1200")));     // extra time for sheets that load their own data
  if (clickSel) { await evalJs(`document.querySelector(${JSON.stringify(clickSel)}).click()`); await sleep(2500); }
  const info = await evalJs(`(() => { const s = window.__lantern; return JSON.stringify({ overlay: s.data && s.data.overlay.version, mode: s.data && s.data.overlay.mode,
    layers: (() => { try { return s.map.getStyle().layers.length; } catch (e) { return 0; } })(), font: document.fonts.check("700 28px Inter"), width: innerWidth }); })()`);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  const out = join(ROOT, "docs", "screenshots", name + ".png");
  writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.log("wrote", out, info);
  ws.close();
} finally {
  chrome.kill();
}
