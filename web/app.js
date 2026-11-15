/* Lanturn mobile web app. Plain JS + MapLibre GL; no build step. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Map colours come from the same design tokens as the rest of the UI (web/tokens.css, generated from Figma).
  const token = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const COLORS = { fastest: token("--fast", "#62c6ff"), preferred: token("--lit", "#ffc857"), lit: token("--lit", "#ffc857"),
    unlit: token("--unlit", "#d6dbe6"), unknown: token("--unknown", "#a9b2c7"), alert: token("--alert", "#ff7a6b"), onAlert: token("--on-alert", "#2b0d09"),
    casing: token("--casing", "#060a14"), prev: token("--prev", "#c9d1e6"), muted: token("--muted", "#a4aec6"), surface: token("--surface", "#141c33") };
  const MAP_COLORS = { bg: token("--map", "#1b2337"), park: token("--map-park", "#173f2d"), road: token("--map-road", "#3c4869"),
    roadMajor: token("--map-road-major", "#515f86"), path: token("--map-path", "#56628a"), water: token("--map-water", "#143352"),
    building: token("--map-building", "#232d47"), label: token("--map-label", "#ffffff"), halo: token("--map-label-halo", "#0a0f1c") };

  const state = {
    config: null, from: null, to: null,
    when: { mode: "now", clock: null },          // mode: now | leave | arrive; clock: "HH:MM" (Europe/London)
    demo: false,                                  // demo mode reads the replay data path, never the live one
    data: null, selected: "preferred", overlayVersion: null, loading: false, lastError: null,
    map: null, mapReady: false, markers: [], userMoved: false,
    nav: null,                                    // { kind, camera } while the navigation preview is open
  };

  // ---------- helpers ----------
  const km = (m) => (m >= 1000 ? (m / 1000).toFixed(1) + " km" : Math.max(10, Math.round(m / 10) * 10) + " m");
  const pct = (x) => Math.round(x * 100) + "%";
  const pad2 = (n) => String(n).padStart(2, "0");
  const placeById = (id) => (state.config ? state.config.places.find((p) => p.id === id) : null);
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Europe/London" });
  const src = () => (state.demo ? "demo" : "live");
  function ago(sec) {
    if (sec == null) return "never";
    if (sec < 90) return "just now";
    if (sec < 3600) return Math.round(sec / 60) + " min ago";
    return Math.round(sec / 3600) + " h ago";
  }
  function londonNow() {
    const f = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    const p = Object.fromEntries(f.formatToParts(new Date()).map((x) => [x.type, x.value]));
    return { date: `${p.year}-${p.month}-${p.day}`, hour: +p.hour % 24, minute: +p.minute };
  }
  // Which London day does the next HH:MM fall on? Mirrors the server rule (lantern/timing.py).
  function clockDay(clock) {
    const [h, m] = clock.split(":").map(Number), n = londonNow();
    const day = h < n.hour || (h === n.hour && m < n.minute) ? 1 : 0;
    const d = new Date(n.date + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + day);
    const date = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).replace(",", "");
    return { day, date };
  }
  const dayPhrase = (c) => (c.day === 0 ? "today" : c.day === 1 ? `tomorrow, ${c.date}` : c.date);
  const dayWord = (c) => (c.day === 0 ? "" : c.day === 1 ? " tomorrow" : " " + c.date);

  async function api(path, opts) {
    const r = await fetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch (_) { /* non-JSON */ }
    if (!r.ok) {
      const d = body && (body.detail || body);
      const err = new Error((d && d.message) || `Request failed (${r.status})`);
      err.code = (d && d.code) || "http_" + r.status;
      err.body = d;
      throw err;
    }
    return body;
  }
  let toastTimer = null;
  function toast(msg, ms = 4200) {
    const t = $("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), ms);
  }

  // ---------- map ----------
  function initMap() {
    const b = state.config.bbox;
    let map;
    try {
      map = new maplibregl.Map({
        container: "map", style: window.lanternMapStyle(MAP_COLORS),
        bounds: [[b.west, b.south], [b.east, b.north]], attributionControl: false,
        maxBounds: [[b.west - 0.25, b.south - 0.15], [b.east + 0.25, b.north + 0.15]], minZoom: 10.5,
        dragRotate: false, pitchWithRotate: false, touchPitch: false,
      });
    } catch (e) { return; }
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Routes &amp; lighting © OpenStreetMap contributors · Powered by TfL Open Data" }));
    map.touchZoomRotate.disableRotation();
    const collapse = () => document.querySelectorAll(".maplibregl-ctrl-attrib").forEach((el) => el.classList.remove("maplibregl-compact-show"));
    collapse(); setTimeout(collapse, 800);
    state.map = map;
    map.on("style.load", () => { state.mapReady = true; addLayers(); drawRoutes(); collapse(); });
    // Remember that the person has moved the map themselves: background updates must not undo it.
    map.on("movestart", (e) => { if (e.originalEvent) state.userMoved = true; });
    map.on("click", "route-other", (e) => { const k = e.features[0].properties.route; if (k && !state.nav) select(k); });
  }
  function addLayers() {
    const m = state.map;
    if (m.getSource("routes")) return;
    const empty = { type: "FeatureCollection", features: [] };
    for (const id of ["prev", "routes", "closed", "restricted"]) m.addSource(id, { type: "geojson", data: empty });
    const before = m.getLayer(window.LANTERN_FIRST_LABEL_LAYER) ? window.LANTERN_FIRST_LABEL_LAYER : undefined;   // routes sit under the street names
    const add = (layer) => m.addLayer(layer, before);
    const round = { "line-cap": "round", "line-join": "round" }, butt = { "line-cap": "butt", "line-join": "round" };
    const w = (k) => ["interpolate", ["linear"], ["zoom"], 12, 5 * k, 15, 6.5 * k, 18, 10 * k];
    const sel = (v) => ["==", ["get", "sel"], v];
    const lit = (v) => ["all", sel(1), ["==", ["get", "lit"], v]];
    // In everyday planning a verified closure is applied quietly; the coloured treatment is for demo mode.
    const closeColor = state.demo ? COLORS.alert : COLORS.muted, closeCasing = state.demo ? COLORS.onAlert : COLORS.casing;
    add({ id: "prev", type: "line", source: "prev", layout: round, paint: { "line-color": COLORS.prev, "line-width": 3, "line-opacity": 0.6, "line-dasharray": [0.6, 2] } });
    add({ id: "other-casing", type: "line", source: "routes", filter: sel(0), layout: round, paint: { "line-color": COLORS.casing, "line-width": w(1.25), "line-opacity": 0.85 } });
    add({ id: "route-other", type: "line", source: "routes", filter: sel(0), layout: round, paint: { "line-color": ["get", "color"], "line-width": w(0.75), "line-opacity": 0.9 } });
    // The Fastest route keeps its blue identity when selected: a blue edge around the lighting colours.
    add({ id: "route-glow", type: "line", source: "routes", filter: ["all", sel(1), ["==", ["get", "route"], "fastest"]], layout: round, paint: { "line-color": COLORS.fastest, "line-width": w(2.2) } });
    add({ id: "route-casing", type: "line", source: "routes", filter: sel(1), layout: round, paint: { "line-color": COLORS.casing, "line-width": w(1.6) } });
    add({ id: "route-lit", type: "line", source: "routes", filter: lit("yes"), layout: round, paint: { "line-color": COLORS.lit, "line-width": w(1) } });
    add({ id: "route-unlit", type: "line", source: "routes", filter: lit("no"), layout: round, paint: { "line-color": COLORS.unlit, "line-width": w(1) } });
    add({ id: "route-unknown", type: "line", source: "routes", filter: lit("unknown"), layout: butt, paint: { "line-color": COLORS.unknown, "line-width": w(1), "line-dasharray": [1.3, 1.1] } });
    add({ id: "restricted", type: "line", source: "restricted", layout: round, paint: { "line-color": closeColor, "line-width": 4, "line-opacity": 0.9, "line-dasharray": [0.4, 1.6] } });
    add({ id: "closed-casing", type: "line", source: "closed", layout: round, paint: { "line-color": closeCasing, "line-width": 9 } });
    add({ id: "closed", type: "line", source: "closed", layout: round, paint: { "line-color": closeColor, "line-width": 5, "line-dasharray": [0.7, 1.3] } });
  }
  const fc = (features) => ({ type: "FeatureCollection", features });
  const line = (coords, props) => ({ type: "Feature", properties: props || {}, geometry: { type: "LineString", coordinates: coords } });
  function drawRoutes() {
    const m = state.map, d = state.data;
    if (!m || !state.mapReady || !m.getSource("routes")) return;   // redrawn on "style.load"
    state.markers.forEach((mk) => mk.remove()); state.markers = [];
    if (!d) { for (const id of ["prev", "routes", "closed", "restricted"]) m.getSource(id).setData(fc([])); return; }
    const chosen = d.identical ? "preferred" : state.nav ? state.nav.kind : state.selected;
    const feats = [];
    for (const k of chosen === "fastest" ? ["preferred", "fastest"] : ["fastest", "preferred"]) {
      if (k !== chosen && (d.identical || state.nav)) continue;        // navigation shows only the route being walked
      for (const s of d[k].segments) feats.push(line(s.coords, { route: k, lit: s.lit, sel: k === chosen ? 1 : 0, color: COLORS[k] }));
    }
    m.getSource("routes").setData(fc(feats));
    const closed = [], restricted = [];
    for (const en of d.changes.entries) {
      for (const g of en.geometry) (en.action === "close" ? closed : restricted).push(line(g));
      const all = en.geometry.flat();
      if (all.length && !state.nav) {
        const el = document.createElement("div");
        el.className = "mk-x" + (en.action === "close" ? "" : " mk-r") + (state.demo ? " demo" : "");
        el.textContent = en.action === "close" ? "✕" : "!";
        el.title = en.street || "";
        el.addEventListener("click", () => openChange());
        state.markers.push(new maplibregl.Marker({ element: el }).setLngLat(all[Math.floor(all.length / 2)]).addTo(m));
      }
    }
    m.getSource("closed").setData(fc(closed));
    m.getSource("restricted").setData(fc(restricted));
    const prev = [];
    if (state.demo && !state.nav && d.changes.changed && d.changes.previous && d.changes[chosen + "_changed"])
      for (const s of d.changes.previous[chosen].segments) prev.push(line(s.coords));
    m.getSource("prev").setData(fc(prev));
    const mk = (cls, p, opts) => { const el = document.createElement("div"); el.className = "mk " + cls; state.markers.push(new maplibregl.Marker(Object.assign({ element: el }, opts)).setLngLat(p).addTo(m)); };
    if (state.nav) mk("puck", d[chosen].nav.start, { rotationAlignment: "viewport", pitchAlignment: "map" });
    else mk("from", [d.from.lon, d.from.lat]);
    mk("to", [d.to.lon, d.to.lat]);
  }
  function fitRoutes() {
    const m = state.map, d = state.data;
    if (!m || !d || state.nav) return;
    const b = new maplibregl.LngLatBounds();
    for (const k of ["fastest", "preferred"]) for (const s of d[k].segments) for (const c of s.coords) b.extend(c);
    const wide = window.innerWidth >= 700;
    const top = $("trip").getBoundingClientRect().bottom + (state.demo && !wide ? 80 : 46);
    const bottom = wide ? 30 : $("sheet").getBoundingClientRect().height + 20;
    try { m.fitBounds(b, { padding: { top, bottom, left: wide ? 460 : 30, right: 30 }, duration: 0, maxZoom: 16 }); } catch (_) { /* tiny viewport */ }
    state.userMoved = false;
  }
  // Fit on first load and on a deliberate trip change. A background update only refits if the map has not been touched.
  const fitIf = (intentional) => { if (intentional || !state.userMoved) fitRoutes(); };

  // ---------- rendering: planning ----------
  function renderTrip() {
    const f = placeById(state.from), t = placeById(state.to);
    $("from-name").textContent = f ? f.name : "Choose start";
    $("to-name").textContent = t ? t.name : "Choose destination";
    document.querySelectorAll(".when-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.when === state.when.mode)));
    const det = $("when-detail"), w = state.when;
    if (w.mode === "now") det.hidden = true;
    else {
      const server = state.data && state.data.time.mode === w.mode && state.data.time.clock === w.clock ? state.data.time.target : null;
      const day = server || clockDay(w.clock);
      det.innerHTML = `<span>${w.mode === "leave" ? "Leave at" : "Arrive by"} <b>${esc(w.clock)}</b> · ${esc(dayPhrase(day))}</span><span class="when-edit">Change</span>`;
      det.hidden = false;
    }
    document.documentElement.style.setProperty("--trip-h", $("trip").getBoundingClientRect().bottom + "px");
  }
  function bar(share) {
    const parts = [["yes", share.yes], ["no", share.no], ["unknown", share.unknown]].filter((p) => p[1] > 0.004);
    return `<div class="bar" role="img" aria-label="${pct(share.yes)} lit, ${pct(share.no)} unlit, ${pct(share.unknown)} unknown">${parts.map((p) => `<i class="s-${p[0]}" style="flex:${p[1]}"></i>`).join("")}</div>`;
  }
  function timesLine(r, d) {
    if (r.late) return `<span class="r-times late">Needed to leave <b>${r.depart.local}</b></span>`;
    if (d.time.mode === "arrive") {
      const day = r.depart.day !== d.time.target.day ? (r.depart.day === 0 ? " today" : dayWord(r.depart)) : "";
      return `<span class="r-times">Leave <b>${r.depart.local}</b>${day}</span>`;
    }
    return `<span class="r-times">Arrive <b>${r.arrive.local}</b>${r.arrive.day !== r.depart.day ? dayWord(r.arrive) : ""}</span>`;
  }
  function card(kind, r, d) {
    const label = kind === "fastest" ? "Fastest" : "Better lit";
    let sub = km(r.length_m);
    if (d.time.mode === "arrive") sub = r.late ? sub : `Arrive ${r.arrive.local} · ${sub}`;
    else if (kind === "preferred" && !d.identical) { const x = Math.round(d.extra_s / 60); sub += x >= 1 ? ` · +${x} min` : " · same time"; }
    const litPct = Math.round(r.lighting_share.yes * 100), noPct = Math.round(r.lighting_share.no * 100);
    const unkPct = Math.max(0, 100 - litPct - noPct);
    return `<button class="route ${kind === "fastest" ? "fast" : "pref"}" data-route="${kind}" aria-pressed="${state.selected === kind || d.identical}">
      <span class="r-label"><i class="r-swatch"></i>${d.identical ? "Fastest &amp; best lit" : label}</span>
      <span class="r-eta">${r.minutes}<small>min</small></span>
      ${timesLine(r, d)}
      <span class="r-sub">${sub}</span>
      ${bar(r.lighting_share)}
      <span class="r-lit"><b>${litPct}%</b> lit</span>
      <span class="r-lit2">${noPct}% unlit · ${unkPct}% unknown</span>
    </button>`;
  }
  function renderNotice(d) {
    const el = $("notice"), parts = [];
    for (const z of d.closed_zones) parts.push(`<b>${esc(z.label)}</b> ${z.opens ? `closed until ${hhmm(z.opens)}` : `gates close ${hhmm(z.closes)}`}`);
    let html = parts.length ? parts.join(" · ") + ": routes go around." : "";
    const e = d.time.earlier;
    if (e) {                       // arrive by: leaving a little earlier is a much shorter walk
      html = parts.length ? `${d.closed_zones.map((z) => `<b>${esc(z.label)}</b>`).join(" and ")} closed by then: routes go around.` : "";
      const why = e.open_zones.length ? `, while ${esc(e.open_zones.join(" and "))} ${e.open_zones.length > 1 ? "are" : "is"} open` : "";
      html += `<button class="hint" id="btn-earlier" data-clock="${e.depart.local}"><span class="hint-text">Shorter: leave by <b>${e.depart.local}</b>${dayWord(e.depart)} for a ${e.fastest_minutes} min walk${why}</span><span class="hint-go">Use</span></button>`;
    }
    if (!html) { el.hidden = true; return; }
    el.innerHTML = html;
    el.hidden = false;
  }
  function renderStatus() {
    const d = state.data; if (!d) return;
    const o = d.overlay, dot = $("agent-dot");
    dot.className = "pulse" + (o.store_error ? " bad" : o.stale ? " warn" : "");
    const ens = d.changes.entries, closes = ens.filter((e) => e.action === "close").length;
    const applied = !ens.length ? "no closures" : closes === ens.length ? `${closes} closure${closes > 1 ? "s" : ""} applied`
      : closes === 0 ? `${ens.length} restriction${ens.length > 1 ? "s" : ""} applied` : `${ens.length} updates applied`;
    const tag = '<span class="tag tag-replay">REPLAY</span>';
    $("agent-chip-text").innerHTML = state.demo
      ? (o.last_run_at ? `${tag}${applied.charAt(0).toUpperCase() + applied.slice(1)} · ${ago(o.age_s)}` : `${tag}No replay run yet`)
      : (o.last_run_at ? `TfL checked ${ago(o.age_s)} · ${applied}` : "TfL check has not run yet");
  }
  function render() {
    const d = state.data, cards = $("cards");
    renderTrip();
    $("demo-pill").hidden = !state.demo;
    if (state.loading && !d) {
      cards.className = "cards"; cards.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
      $("legend").hidden = true; $("notice").hidden = true; $("btn-start").hidden = true;
      return syncSheetHeight();
    }
    if (state.lastError) {
      const e = state.lastError;
      const titles = { no_route: "No open walking route", outside_coverage: "Outside our coverage", same_place: "Pick two different places",
        too_late: "Too late for that arrival", bad_time: "That time isn't valid" };
      const action = e.code === "too_late" ? '<button class="btn" id="btn-leave-now">Leave now instead</button>'
        : e.code === "no_route" && state.when.mode !== "now" ? '<button class="btn" id="btn-change-time">Choose another time</button>'
        : '<button class="btn" id="btn-retry">Try again</button>';
      cards.className = "cards single";
      cards.innerHTML = `<div class="error"><b>${esc(titles[e.code] || "Couldn't load routes")}</b><p>${esc(e.message || "Check your connection and try again.")}</p>${action}</div>`;
      $("legend").hidden = true; $("notice").hidden = true; $("btn-start").hidden = true;
      return syncSheetHeight();
    }
    if (!d) return;
    cards.className = "cards" + (d.identical ? " single" : "");
    cards.innerHTML = d.identical ? card("preferred", d.preferred, d) : card("fastest", d.fastest, d) + card("preferred", d.preferred, d);
    $("legend").hidden = false; $("btn-start").hidden = false;
    renderNotice(d); renderStatus();
    syncSheetHeight();
  }
  function syncSheetHeight() {
    document.documentElement.style.setProperty("--sheet-h", Math.round($("sheet").getBoundingClientRect().height) + "px");
  }

  // ---------- data ----------
  let reqSeq = 0;
  function routeQuery() {
    const q = new URLSearchParams({ from: state.from, to: state.to });
    if (state.when.mode !== "now") { q.set("mode", state.when.mode); q.set("time", state.when.clock); }
    if (state.demo) q.set("source", "demo");
    return q;
  }
  // opts.keep: background refresh (keep showing the current routes while loading, and do not refit a map the person has moved).
  async function loadRoutes(opts = {}) {
    const seq = ++reqSeq;
    state.loading = true; state.lastError = null;
    if (!opts.keep) state.data = null;
    render(); if (!opts.keep) drawRoutes();
    try {
      const d = await api("/api/route?" + routeQuery());
      if (seq !== reqSeq) return;
      state.data = d; state.overlayVersion = d.overlay.version;
      if (d.identical) state.selected = "preferred";
    } catch (e) {
      if (seq !== reqSeq) return;
      state.data = null; state.lastError = e;
      if (!e.code) { e.code = "network"; e.message = "We couldn't reach the route service. Check your connection and try again."; }
    }
    state.loading = false;
    render(); drawRoutes();
    requestAnimationFrame(() => fitIf(!opts.keep));
  }
  function select(kind) {
    if (!state.data || state.data.identical || state.selected === kind) return;
    state.selected = kind; render(); drawRoutes();              // choosing a card never moves the map
  }
  async function pollOverlay() {
    if (state.nav) return;                                        // the preview's values stay fixed
    try {
      const o = await api(`/api/overlay?refresh=true&source=${src()}`);
      if (state.overlayVersion != null && o.version !== state.overlayVersion) {
        const n = o.entries.filter((e) => e.active_now).length;
        state.overlayVersion = o.version;
        if (state.demo) toast(`Replay: agent published v${o.version} · ${n ? n + " active change" + (n > 1 ? "s" : "") : "changes withdrawn"}`);
        await loadRoutes({ keep: true });
        if (!state.demo && state.data && state.data.changes.changed) toast("Route updated for a verified TfL closure");
      } else if (state.data) {
        Object.assign(state.data.overlay, { age_s: o.age_s, last_run_at: o.last_run_at, stale: o.stale, store_error: o.store_error, last_run_mode: o.last_run_mode });
        renderStatus();
      }
    } catch (_) { /* keep last known state */ }
  }

  // ---------- modals ----------
  function openModal(title, html) { $("modal-title").textContent = title; $("modal-content").innerHTML = html; $("modal").hidden = false; }
  function closeModal() { $("modal").hidden = true; }
  function openPlaces(which) {
    const cur = state[which];
    const html = state.config.places.map((p) => `<button class="list-btn" data-place="${p.id}" data-which="${which}" aria-current="${p.id === cur}"><span class="dot ${which === "from" ? "dot-from" : "dot-to"}"></span><span><b>${esc(p.name)}</b><small>${esc(p.short)}</small></span></button>`).join("");
    openModal(which === "from" ? "Start from" : "Walk to", html + '<p class="note">Coverage: South Kensington to Paddington, including Hyde Park and Kensington Gardens.</p>');
  }
  // 24-hour time picker. Times are London clock times; the date is whichever day that time next falls on.
  function openPicker(mode) {
    let h, m;
    if (state.when.mode === mode && state.when.clock) [h, m] = state.when.clock.split(":").map(Number);
    else { const n = londonNow(); const t = n.hour * 60 + n.minute + (mode === "arrive" ? 45 : 5); const r = Math.ceil(t / 5) * 5; h = Math.floor(r / 60) % 24; m = r % 60; }
    openModal(mode === "leave" ? "Leave at" : "Arrive by", `
      <div class="tp" data-mode="${mode}">
        <div class="tp-col"><button class="tp-step" data-tp="h+" aria-label="Hour up">▲</button>
          <input id="tp-h" class="tp-num" inputmode="numeric" maxlength="2" value="${pad2(h)}" aria-label="Hour, 0 to 23">
          <button class="tp-step" data-tp="h-" aria-label="Hour down">▼</button></div>
        <span class="tp-colon">:</span>
        <div class="tp-col"><button class="tp-step" data-tp="m+" aria-label="Minute up">▲</button>
          <input id="tp-m" class="tp-num" inputmode="numeric" maxlength="2" value="${pad2(m)}" aria-label="Minute">
          <button class="tp-step" data-tp="m-" aria-label="Minute down">▼</button></div>
      </div>
      <p class="tp-date" id="tp-date"></p>
      <div class="row tp-actions"><button class="btn" id="tp-set">${mode === "leave" ? "Set departure" : "Set arrival"}</button><button class="btn ghost" data-close>Cancel</button></div>
      <p class="note">24-hour clock, London time.</p>`);
    pickerSync();
  }
  function pickerRead() {
    const clamp = (v, max) => Math.min(max, Math.max(0, parseInt(String(v).replace(/\D/g, ""), 10) || 0));
    return [clamp($("tp-h").value, 23), clamp($("tp-m").value, 59)];
  }
  function pickerSync(write) {
    const [h, m] = pickerRead();
    if (write) { $("tp-h").value = pad2(h); $("tp-m").value = pad2(m); }
    const day = clockDay(`${pad2(h)}:${pad2(m)}`);
    $("tp-date").textContent = (day.day === 0 ? "Today, " : "Tomorrow, ") + day.date;
  }
  function pickerStep(what) {
    let [h, m] = pickerRead();
    if (what === "h+") h = (h + 1) % 24;
    if (what === "h-") h = (h + 23) % 24;
    if (what === "m+") m = (Math.floor(m / 5) * 5 + 5) % 60;
    if (what === "m-") m = (Math.ceil(m / 5) * 5 + 55) % 60;
    $("tp-h").value = pad2(h); $("tp-m").value = pad2(m); pickerSync();
  }
  function pickerSet() {
    const [h, m] = pickerRead();
    state.when = { mode: document.querySelector(".tp").dataset.mode, clock: `${pad2(h)}:${pad2(m)}` };
    closeModal(); loadRoutes();
  }
  const PILL = { publish_close: ["pub", "Closed"], publish_restrict: ["res", "Restricted"], carried: ["res", "Kept"], ignore_not_pedestrian: ["ign", "No effect on foot"], ignore_cleared: ["ign", "Already cleared"],
    reject_uncertain: ["rej", "Held: not explicit"], reject_location: ["rej", "Rejected: location"], reject_stale: ["rej", "Rejected: stale"], reject_unverified_evidence: ["rej", "Rejected: evidence"],
    reject_validation: ["rej", "Rejected: tests"], reject_conflict: ["rej", "Rejected: conflict"], reject_not_active: ["rej", "Not active"], defer_future: ["ign", "Future"], withdrawn: ["ign", "Withdrawn"], expired: ["ign", "Expired"], hold_assessment_failed: ["rej", "Held: error"] };
  function decisionHtml(x) {
    const [cls, label] = PILL[x.decision] || ["ign", x.decision];
    const syn = String(x.event_id || "").startsWith("REPLAY-") ? '<span class="tag tag-replay">SYNTHETIC</span>' : "";
    const checks = x.match && x.match.checks ? `<div class="checks">Location checks: ${esc(x.match.checks.join(" · "))}${x.match.notes && x.match.notes.length ? " · " + esc(x.match.notes.join(" · ")) : ""}</div>` : "";
    const segs = x.edge_ids && x.edge_ids.length ? `<div class="checks">${x.edge_ids.length} network segments ${x.action === "close" ? "closed" : x.action === "restrict" ? "penalised" : "affected"}</div>` : "";
    const link = x.source_url && !syn ? ` · <a href="${esc(x.source_url)}" target="_blank" rel="noopener">source</a>` : "";
    return `<div class="dec"><div class="dec-head">${syn}<span class="dec-id">${esc(x.event_id)}</span><span class="pill ${cls}">${label}</span></div>
      <p>${esc(x.location || "")}</p>
      ${x.evidence ? `<blockquote>“${esc(x.evidence)}”</blockquote>` : ""}
      <p>${esc(x.reason || "")}${link}</p>${checks}${segs}</div>`;
  }
  async function openAgent() {
    openModal(state.demo ? "Replay: what the agent did" : "TfL disruption checks", '<p class="note">Loading…</p>');
    let lr, ov, st;
    try { [lr, ov, st] = await Promise.all([api(`/api/agent/last-run?source=${src()}`), api(`/api/overlay?source=${src()}`), api(`/api/agent/status?source=${src()}`)]); }
    catch (e) { $("modal-content").innerHTML = `<div class="error"><b>Couldn't load the agent's activity</b><p>${esc(e.message)}</p></div>`; return; }
    const run = lr.run;
    const order = (x) => (x.decision.startsWith("publish") ? 0 : x.decision.startsWith("reject") || x.decision.startsWith("hold") ? 1 : 2);
    const decs = lr.decisions.slice().sort((a, b) => order(a) - order(b));
    const checks = run && run.checks ? `${run.checks.total - (run.checks.failed || []).length}/${run.checks.total} baseline journeys still valid` : "n/a";
    const modeTag = run ? (run.mode === "replay" ? '<span class="tag tag-replay">REPLAY</span>captured feed + synthetic records' : '<span class="tag tag-live">LIVE</span>TfL Unified API') : "";
    const controls = state.demo ? `
      <div class="sec">Replay controls</div>
      <div class="row">
        <button class="btn" data-run="replay" data-step="1" ${st.running ? "disabled" : ""}>Replay: burst main</button>
        <button class="btn ghost" data-run="replay" data-step="2" ${st.running ? "disabled" : ""}>Replay: main fixed</button>
      </div>
      <p class="note">Replay runs on its own Databricks job, tables and volume: the captured TfL feed plus clearly labelled synthetic records, through the same checks as a live run. It never touches the live data that normal planning uses.</p>`
      : '<p class="note">The agent runs on Databricks every 30 minutes. A closure is applied to routes only after its quote is found in the TfL text, its location is matched to the walking network, and all baseline journeys still work.</p>';
    $("modal-content").innerHTML = `
      <dl class="kv">
        <dt>Published</dt><dd>Overlay v${ov.version}${ov.published_at ? ", " + hhmm(ov.published_at) : ""} · ${ov.entries.length} entr${ov.entries.length === 1 ? "y" : "ies"}</dd>
        <dt>Last run</dt><dd>${run ? `${esc(run.status.replace("_", " "))}, ${hhmm(run.finished_at)}` : "none yet"}</dd>
        <dt>Input</dt><dd>${modeTag}</dd>
        <dt>Feed</dt><dd>${run ? `${run.feed.records ?? "?"} records, ${run.feed.in_area ?? "?"} in this area` : ""}</dd>
        <dt>Judgment</dt><dd>${run ? esc(run.assessor || "none needed") : ""}</dd>
        <dt>Gate</dt><dd>${checks}</dd>
        <dt>Stored in</dt><dd>${esc(ov.store === "databricks" ? "Databricks (Delta + volume)" : ov.store || "local")}${ov.store_error ? " · " + esc(ov.store_error) : ""}</dd>
      </dl>
      ${run && run.error ? `<div class="dec"><div class="dec-head"><span class="pill rej">Run failed</span></div><p>${esc(run.error)}</p><p>The previous overlay stayed published.</p></div>` : ""}
      <div class="sec">Decisions in the last run (${decs.length})</div>
      ${decs.map(decisionHtml).join("") || '<p class="note">No records in this area.</p>'}
      ${controls}`;
  }
  function openChange() {
    const d = state.data; if (!d || !d.changes.entries.length) return openAgent();
    const html = d.changes.entries.map((en) => {
      const syn = en.synthetic ? '<span class="tag tag-replay">SYNTHETIC REPLAY RECORD</span>' : '<span class="tag tag-live">LIVE TfL RECORD</span>';
      return `<div class="dec"><div class="dec-head">${syn}</div>
        <div class="dec-head" style="margin-top:6px"><span class="dec-id">${esc(en.entry_id)}</span><span class="pill ${en.action === "close" ? "pub" : "res"}">${en.action === "close" ? "Closed to pedestrians" : "Restricted"}</span></div>
        <p>${esc(en.location || en.street || "")}</p>
        <blockquote>“${esc(en.evidence)}”</blockquote>
        <p>${esc(en.summary || "")}</p>
        <div class="checks">${en.length_m} m of ${esc(en.street || "street")} · valid until ${hhmm(en.valid_to)}${en.end_assumed ? " (no end time published; re-checked every run)" : ""} · decided ${hhmm(en.decided_at)}</div>
        ${en.synthetic ? "" : `<div class="checks"><a href="${esc(en.source_url)}" target="_blank" rel="noopener">Open the TfL record</a></div>`}</div>`;
    }).join("");
    const k = d.identical ? "preferred" : state.selected, delta = d.changes[k + "_delta_s"] || 0;
    const effect = d.changes[k + "_changed"] ? `This route goes around it${Math.abs(delta) >= 30 ? `, ${delta > 0 ? "+" : "−"}${Math.round(Math.abs(delta) / 60)} min` : ""}.` : "This route is not affected.";
    openModal("Closure applied to routing", `<p class="note" style="margin:0 0 10px">${effect}</p>${html}<p class="note">Overlay v${d.overlay.version}. Published only after the quote was found in the source text, the location was matched to the walking network, and all baseline journeys still worked.</p><div class="row"><button class="btn ghost" id="open-agent">See every decision</button></div>`);
  }
  async function triggerRun(step) {
    try {
      await api(`/api/agent/run?mode=replay&step=${step}`, { method: "POST" });
      closeModal(); toast("Replay run started on Databricks…", 6000);
      const t0 = Date.now();
      const tick = async () => {
        let st; try { st = await api("/api/agent/status?source=demo"); } catch (_) { st = { running: true }; }
        if (st.running && Date.now() - t0 < 600000) return setTimeout(tick, 2500);
        await pollOverlay();
        if (st.last && st.last.status === "no_change") toast("Replay finished: nothing needed to change");
        if (st.last && st.last.status === "failed") toast("Replay run failed; previous version kept");
      };
      setTimeout(tick, 1500);
    } catch (e) { toast(e.message || "Could not start the run"); }
  }

  // ---------- navigation preview ----------
  const ARROWS = {
    straight: '<path d="M12 20.500V5"/><path d="m6 10.500 6-6 6 6"/>',
    left: '<path d="M17.500 20.500v-7a4.500 4.500 0 0 0-4.500-4.500H5.500"/><path d="m10 4.500-4.500 4.500 4.500 4.500"/>',
    slight_left: '<path d="M16.500 20.500v-5.500L7 5.500"/><path d="M7 12V5.500h6.500"/>',
    sharp_left: '<path d="M17 20.500V7.500L7 17"/><path d="M7 10.500V17h6.500"/>',
  };
  function arrowSvg(type) {
    const flip = type.endsWith("right"), key = type.replace("right", "left");
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"${flip ? ' style="transform:scaleX(-1)"' : ""}>${ARROWS[key] || ARROWS.straight}</svg>`;
  }
  const lowerFirst = (s) => s.charAt(0).toLowerCase() + s.slice(1);
  // What to show at the top: the first manoeuvre, from real geometry and mapped street names.
  function firstInstruction(d, r) {
    const nav = r.nav, first = nav.maneuvers[0], dep = nav.depart;
    if (!first || first.type === "arrive")
      return { arrow: "straight", dist: nav.length_m, instr: `Continue to ${d.to.short || d.to.name}`, then: dep.instruction };
    if (first.at_m > 400)                              // no early turn: say what to follow, and what comes after
      return { arrow: "straight", dist: first.at_m, instr: dep.street ? `Continue on ${dep.street}` : "Follow the path", then: "Then " + lowerFirst(first.instruction) };
    return { arrow: first.type, dist: first.at_m, instr: first.instruction, then: dep.instruction };
  }
  function startNav() {
    const d = state.data, m = state.map; if (!d || state.nav) return;
    const kind = d.identical ? "preferred" : state.selected, r = d[kind], fi = firstInstruction(d, r);
    state.nav = { kind, camera: m ? { center: m.getCenter(), zoom: m.getZoom() } : null, moved: state.userMoved };
    $("nav-arrow").innerHTML = arrowSvg(fi.arrow);
    $("nav-dist").textContent = km(fi.dist);
    $("nav-instr").textContent = fi.instr;
    $("nav-then").textContent = fi.then;
    // Remaining time, distance and ETA are fixed for the preview: the position stays at the start.
    $("nav-min").textContent = r.minutes;
    $("nav-sub").innerHTML = `${km(r.length_m)} · ETA <b>${r.arrive.local}</b>${r.arrive.day !== r.depart.day ? dayWord(r.arrive) : ""}`;
    document.body.classList.add("nav-on");
    document.documentElement.style.setProperty("--nav-top-h", Math.round($("nav-top").getBoundingClientRect().bottom) + "px");
    closeModal(); drawRoutes();
    // Face along the first usable stretch of the route, with the start just below the middle of the screen.
    if (m) m.easeTo({ center: r.nav.start, zoom: 17.2, bearing: r.nav.heading, pitch: 52, offset: [0, Math.round(window.innerHeight * 0.14)], duration: 900 });
  }
  function exitNav() {
    if (!state.nav) return;
    hangUp();                                                   // leaving navigation ends the call and releases the microphone
    const { camera, moved } = state.nav, m = state.map;
    state.nav = null;
    document.body.classList.remove("nav-on");
    render(); drawRoutes();
    if (m && camera) { m.easeTo({ center: camera.center, zoom: camera.zoom, bearing: 0, pitch: 0, offset: [0, 0], duration: 600 }); state.userMoved = moved; }
    pollOverlay();
  }

  // ---------- walking companion (live avatar call) ----------
  const LIVEKIT_JS = "https://cdn.jsdelivr.net/npm/livekit-client@2.23.0/dist/livekit-client.umd.min.js";
  const call = { status: "idle", gen: 0, room: null, sessionId: null, timer: null, voiceTimer: null };
  const CALL_ERRORS = { not_configured: "Companion isn't set up on this server yet.", worker_offline: "Companion is offline right now.",
    mic_denied: "Microphone is blocked. Allow it, then retry.", mic_unavailable: "No microphone available here.", timeout: "Couldn't reach the companion.", lib: "Couldn't load the call. Check your connection." };
  function setCall(status, text) {
    call.status = status;
    const el = $("call");
    el.hidden = status === "idle"; el.dataset.status = status; el.dataset.voice = String(status === "live" && !!text);
    $("call-state").textContent = text || "";
    $("btn-retry-call").hidden = status !== "failed";
    $("btn-call").setAttribute("aria-pressed", String(status === "live" || status === "connecting"));
    $("btn-call").disabled = status === "live" || status === "connecting";
  }
  function loadLiveKit() {
    if (window.LivekitClient) return Promise.resolve();
    return new Promise((res, rej) => { const s = document.createElement("script"); s.src = LIVEKIT_JS; s.onload = res; s.onerror = () => rej(Object.assign(new Error("lib"), { code: "lib" })); document.head.appendChild(s); });
  }
  function tripContext() {
    const d = state.data, r = d[state.nav.kind], fi = firstInstruction(d, r), nav = r.nav;
    const first = `${nav.depart.instruction}, then in ${km(fi.dist)} ${lowerFirst(fi.instr)}`;
    return { from: d.from.name, to: d.to.name, route: state.nav.kind === "fastest" ? "Fastest" : "Better lit", minutes: r.minutes, distance: km(r.length_m), eta: r.arrive.local,
      first, steps: nav.maneuvers.map((mv) => `After ${km(mv.at_m)}: ${mv.instruction}`) };
  }
  async function releaseCall() {
    clearTimeout(call.timer); call.timer = null; clearTimeout(call.voiceTimer); call.voiceTimer = null;
    const room = call.room, sid = call.sessionId;
    call.room = null; call.sessionId = null;
    const v = $("call-video"); try { v.pause(); v.srcObject = null; } catch (_) { /* nothing attached */ }
    if (room) { try { await room.disconnect(); } catch (_) { /* already closed */ } }        // also stops and releases the microphone track
    if (sid) fetch("/api/companion/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sid }), keepalive: true }).catch(() => {});
  }
  function failCall(code) { call.gen++; releaseCall(); setCall("failed", CALL_ERRORS[code] || CALL_ERRORS.timeout); }
  function hangUp() { if (call.status === "idle") return; call.gen++; releaseCall(); setCall("idle"); }
  async function startCall() {
    if (!state.nav || call.status === "connecting" || call.status === "live") return;
    const gen = ++call.gen, stale = () => gen !== call.gen;
    setCall("connecting", "Connecting…");
    try {
      // 1. Microphone permission, asked for at the moment of the tap. The camera is never requested.
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw Object.assign(new Error("mic"), { code: "mic_unavailable" });
      let probe;
      try { probe = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (_) { throw Object.assign(new Error("mic"), { code: "mic_denied" }); }
      probe.getTracks().forEach((t) => t.stop());
      if (stale()) return;
      // 2. A short-lived room token from our server; no keys reach the browser.
      const sess = await api("/api/companion/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(tripContext()) });
      if (stale()) { fetch("/api/companion/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sess.session_id }) }).catch(() => {}); return; }
      call.sessionId = sess.session_id;
      await loadLiveKit();
      if (stale()) return;
      // 3. Join the room: microphone on, camera off. The avatar arrives as an ordinary participant.
      const { Room, RoomEvent } = window.LivekitClient;
      const room = new Room({ adaptiveStream: false });
      call.room = room;
      room.on(RoomEvent.TrackSubscribed, (track, publication) => {
        if (stale()) return;
        track.attach($("call-video"));                            // audio and video on one element so they stay in sync
        if (track.kind === "video") {
          try { publication.setVideoQuality(window.LivekitClient.VideoQuality.HIGH); } catch (_) { /* single-layer track */ }
          clearTimeout(call.timer); clearTimeout(call.voiceTimer); setCall("live", "");
        }
        // If the companion can be heard but no avatar video follows, carry on as a voice call.
        else if (!call.voiceTimer) call.voiceTimer = setTimeout(() => { if (!stale() && call.status === "connecting") { clearTimeout(call.timer); setCall("live", "Voice only"); } }, 6000);
      });
      room.on(RoomEvent.Disconnected, () => { if (!stale() && call.status !== "idle") { call.gen++; releaseCall(); setCall("idle"); toast("Call ended"); } });
      await room.connect(sess.url, sess.token);
      if (stale()) { room.disconnect(); return; }
      await room.localParticipant.setMicrophoneEnabled(true);
      try { await room.startAudio(); } catch (_) { /* the tap already unlocked audio */ }
      if (stale()) { room.disconnect(); return; }
      setCall("connecting", "Waiting for your companion…");
      call.timer = setTimeout(() => { if (!stale() && call.status === "connecting") failCall("timeout"); }, 75000);
    } catch (e) {
      if (!stale()) failCall(e.code || "timeout");
    }
  }

  // ---------- events ----------
  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("button, [data-close]"); if (!t) return;
    if (t.dataset.close !== undefined) return closeModal();
    if (t.id === "btn-from") return openPlaces("from");
    if (t.id === "btn-to") return openPlaces("to");
    if (t.id === "btn-swap") { [state.from, state.to] = [state.to, state.from]; return loadRoutes(); }
    if (t.id === "btn-agent") return openChange();
    if (t.id === "open-agent") return openAgent();
    if (t.id === "btn-retry") return loadRoutes();
    if (t.id === "btn-leave-now") { state.when = { mode: "now", clock: null }; return loadRoutes(); }
    if (t.id === "btn-earlier") { state.when = { mode: "leave", clock: t.dataset.clock }; return loadRoutes(); }
    if (t.id === "btn-change-time" || t.id === "when-detail") return openPicker(state.when.mode);
    if (t.dataset.when) { if (t.dataset.when === "now") { state.when = { mode: "now", clock: null }; return loadRoutes(); } return openPicker(t.dataset.when); }
    if (t.dataset.tp) return pickerStep(t.dataset.tp);
    if (t.id === "tp-set") return pickerSet();
    if (t.id === "btn-start") return startNav();
    if (t.id === "btn-exit") return exitNav();
    if (t.id === "btn-call" || t.id === "btn-retry-call") return startCall();
    if (t.id === "btn-hangup") return hangUp();
    if (t.dataset.route) return select(t.dataset.route);
    if (t.dataset.place) {
      const which = t.dataset.which, other = which === "from" ? "to" : "from";
      if (state[other] === t.dataset.place) state[other] = state[which];
      state[which] = t.dataset.place; closeModal(); return loadRoutes();
    }
    if (t.dataset.run) return triggerRun(t.dataset.step);
  });
  document.addEventListener("input", (e) => { if (e.target.classList && e.target.classList.contains("tp-num")) { e.target.value = e.target.value.replace(/\D/g, "").slice(0, 2); pickerSync(); } });
  document.addEventListener("focusout", (e) => { if (e.target.classList && e.target.classList.contains("tp-num") && !$("modal").hidden) pickerSync(true); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
    if (e.key === "Enter" && e.target.classList && e.target.classList.contains("tp-num")) pickerSet();
  });
  window.addEventListener("resize", () => { renderTrip(); syncSheetHeight(); fitIf(false); });
  window.addEventListener("pagehide", () => { if (call.status !== "idle") releaseCall(); });
  window.__lantern = state; window.__lanternCall = call;

  // ---------- boot ----------
  (async function boot() {
    const p = new URLSearchParams(location.search);
    state.demo = p.get("demo") === "1";
    document.body.classList.toggle("demo", state.demo);
    render();
    try { state.config = await api("/api/config"); }
    catch (e) {
      $("cards").className = "cards single";
      $("cards").innerHTML = `<div class="error"><b>Can't reach Lanturn</b><p>The route service is not responding. Check your connection.</p><button class="btn" onclick="location.reload()">Reload</button></div>`;
      return;
    }
    state.from = state.config.default.from; state.to = state.config.default.to;
    if (p.get("from") && placeById(p.get("from"))) state.from = p.get("from");
    if (p.get("to") && placeById(p.get("to"))) state.to = p.get("to");
    const clock = (v) => (/^\d{4}$/.test(v || "") && +v.slice(0, 2) < 24 && +v.slice(2) < 60 ? v.slice(0, 2) + ":" + v.slice(2) : null);
    if (clock(p.get("t"))) state.when = { mode: "leave", clock: clock(p.get("t")) };            // ?t=2230  leave at 22:30
    if (clock(p.get("arrive"))) state.when = { mode: "arrive", clock: clock(p.get("arrive")) };  // ?arrive=0030
    if (p.get("route") === "fastest") state.selected = "fastest";
    renderTrip();
    if (window.maplibregl) initMap();
    await loadRoutes();
    // Deep links used for documentation screenshots.
    if (p.get("open") === "why") openChange();
    if (p.get("open") === "agent") openAgent();
    if (p.get("open") === "leave" || p.get("open") === "arrive") openPicker(p.get("open"));
    if (p.get("nav") === "1") setTimeout(startNav, 600);
    setInterval(pollOverlay, 12000);
  })();
})();
