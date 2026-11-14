// Exercise the companion call lifecycle in headless Chrome with a simulated microphone.
// No dependencies (Node 22+). The app (port 8000) and the companion worker must be running.
//
//   node scripts/call_lifecycle.mjs
//
// Checks: Start! -> call button -> connecting -> live -> hang up (navigation stays open, the
// microphone is released) -> call again -> Exit navigation (call ends, microphone released).
// Chrome's fake audio device stands in for the microphone, so this proves the browser side of
// the call, not that a person can be heard. Writes docs/screenshots/10-call-live.png.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CHROME = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find((p) => p && existsSync(p));
if (!CHROME) { console.error("Chrome/Edge not found; set CHROME_PATH"); process.exit(2); }
const port = 9300 + Math.floor(Math.random() * 400);
const profile = join(ROOT, "var", "chrome-call-profile");
mkdirSync(profile, { recursive: true });
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required",
  "--hide-scrollbars", "--ignore-gpu-blocklist", "--enable-webgl", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
  "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`); };

try {
  let wsUrl;
  for (let i = 0; i < 50 && !wsUrl; i++) {
    try { wsUrl = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page")?.webSocketDebuggerUrl; } catch (_) { /* not up yet */ }
    if (!wsUrl) await sleep(200);
  }
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res, rej) => { const mid = ++id; pending.set(mid, (m) => (m.error ? rej(new Error(method + ": " + m.error.message)) : res(m.result))); ws.send(JSON.stringify({ id: mid, method, params })); });
  const js = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result.value;
  const until = async (expr, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await js(expr)) return (Date.now() - t0) / 1000; await sleep(400); } return null; };
  const state = () => js(`JSON.stringify({ status: window.__lanternCall.status, text: document.getElementById("call-state").textContent,
    nav: document.body.classList.contains("nav-on"), room: !!window.__lanternCall.room,
    mic: window.__lanternCall.room ? [...window.__lanternCall.room.localParticipant.trackPublications.values()].map(p => p.kind + ":" + (p.track && p.track.mediaStreamTrack.readyState)) : [],
    remote: window.__lanternCall.room ? [...window.__lanternCall.room.remoteParticipants.values()].map(p => p.identity.replace(/-.*/, "")) : [] })`).then(JSON.parse);

  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Page.navigate", { url: "http://localhost:8000/?t=2230" });
  check("routes loaded", (await until(`!!(window.__lantern && window.__lantern.data && window.__lantern.mapReady)`, 30000)) !== null);
  await js(`document.getElementById("btn-start").click()`);
  check("navigation preview open", (await until(`document.body.classList.contains("nav-on")`, 5000)) !== null);

  // --- call 1: connect, go live, hang up
  await js(`document.getElementById("btn-call").click()`);
  const tConn = await until(`window.__lanternCall.status !== "idle"`, 3000);
  check("connecting state shown", tConn !== null, (await state()).text);
  const tLive = await until(`["live","failed"].includes(window.__lanternCall.status) && document.getElementById("call").dataset.status !== "connecting"`, 90000);
  let s = await state();
  check("call is live", s.status === "live", `${tLive?.toFixed(1)} s, ${s.text || "avatar video"}, remote: ${s.remote.join(",")}`);
  check("microphone published, camera never requested", s.mic.length === 1 && s.mic[0] === "audio:live", s.mic.join(","));
  await sleep(1500);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  mkdirSync(join(ROOT, "docs", "screenshots"), { recursive: true });
  writeFileSync(join(ROOT, "docs", "screenshots", "10-call-live.png"), Buffer.from(shot.data, "base64"));
  const micTrack = await js(`(() => { const p = [...window.__lanternCall.room.localParticipant.trackPublications.values()][0]; window.__mic = p.track.mediaStreamTrack; return window.__mic.readyState; })()`);
  await js(`document.getElementById("btn-hangup").click()`);
  await sleep(1500);
  s = await state();
  check("hang up closes the call", s.status === "idle" && !s.room);
  check("hang up leaves navigation open", s.nav === true);
  check("hang up releases the microphone", (await js(`window.__mic.readyState`)) === "ended", `${micTrack} -> ended`);

  // --- call 2: connect, then Exit navigation
  await js(`document.getElementById("btn-call").click()`);
  await until(`window.__lanternCall.status === "live" || (window.__lanternCall.room && window.__lanternCall.room.localParticipant.trackPublications.size > 0)`, 60000);
  await js(`(() => { const pubs = window.__lanternCall.room ? [...window.__lanternCall.room.localParticipant.trackPublications.values()] : []; window.__mic2 = pubs[0] && pubs[0].track.mediaStreamTrack; })()`);
  await js(`document.getElementById("btn-exit").click()`);
  await sleep(1500);
  s = await state();
  check("Exit navigation ends the call", s.status === "idle" && !s.room && s.nav === false);
  check("Exit navigation releases the microphone", (await js(`window.__mic2 ? window.__mic2.readyState : "none"`)) === "ended");
  check("trip, route and time restored", await js(`(() => { const s = window.__lantern; return s.from === "imperial_south_ken" && s.to === "wilson_house" && s.selected === "preferred" && s.when.clock === "22:30" && s.map.getBearing() === 0; })()`));
  ws.close();
} finally {
  chrome.kill();
}
console.log(results.every(Boolean) ? "\nAll call lifecycle checks passed." : "\nSome checks failed.");
process.exit(results.every(Boolean) ? 0 : 1);
