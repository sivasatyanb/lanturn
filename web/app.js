/* Lantern mobile web app. Plain JS + MapLibre GL; no build step. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLORS = { fastest: "#62c6ff", preferred: "#ffc857", unlit: "#c07cff", unknown: "#7f8aa6", alert: "#ff7a6b" };

  const state = {
    config: null, from: null, to: null, timeKey: "now", customWhen: null,
    data: null, selected: "preferred", overlayVersion: null, loading: false,
    map: null, mapReady: false, markers: [], lastError: null,
  };

  // ---------- helpers ----------
  const km = (m) => (m >= 1000 ? (m / 1000).toFixed(1) + " km" : Math.round(m / 10) * 10 + " m");
  const pct = (x) => Math.round(x * 100) + "%";
  const mins = (s) => Math.round(Math.abs(s) / 60);
  const placeById = (id) => (state.config ? state.config.places.find((p) => p.id === id) : null);
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" });
  function ago(sec) {
    if (sec == null) return "never";
    if (sec < 90) return "just now";
    if (sec < 3600) return Math.round(sec / 60) + " min ago";
    return Math.round(sec / 3600) + " h ago";
  }
  function londonDateParts(d) {
    const f = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
    const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
    return { date: `${p.year}-${p.month}-${p.day}`, hour: +p.hour % 24, minute: +p.minute };
  }
  // Next occurrence of HH:MM London time, as a naive ISO string (server treats naive as London).
  function nextLocal(h, m) {
    const now = new Date();
    const p = londonDateParts(now);
    let day = new Date(p.date + "T00:00:00Z");
    if (p.hour > h || (p.hour === h && p.minute >= m)) day = new Date(day.getTime() + 86400000);
    const ds = day.toISOString().slice(0, 10);
    return `${ds}T${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:00`;
  }
  const TIMES = [
    { key: "now", label: () => "Leave now", when: () => "now" },
    { key: "2230", label: () => "22:30", when: () => nextLocal(22, 30) },
    { key: "0030", label: () => "00:30", when: () => nextLocal(0, 30) },
    { key: "0200", label: () => "02:00", when: () => nextLocal(2, 0) },
  ];
  async function api(path, opts) {
    const r = await fetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch (_) { /* non-JSON */ }
    if (!r.ok) {
      const d = body && (body.detail || body);
      const err = new Error((d && d.message) || `Request failed (${r.status})`);
      err.code = (d && d.code) || "http_" + r.status;
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
        container: "map", style: "https://tiles.openfreemap.org/styles/dark",
        bounds: [[b.west, b.south], [b.east, b.north]], attributionControl: false,
        maxBounds: [[b.west - 0.06, b.south - 0.04], [b.east + 0.06, b.north + 0.04]],
        dragRotate: false, pitchWithRotate: false,
      });
    } catch (e) { return; }
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Routes &amp; lighting © OpenStreetMap contributors · Powered by TfL Open Data" }));
    map.touchZoomRotate.disableRotation();
    const collapse = () => document.querySelectorAll(".maplibregl-ctrl-attrib").forEach((el) => el.classList.remove("maplibregl-compact-show"));
    collapse(); setTimeout(collapse, 800); map.on("load", collapse);
    state.map = map;
    const ready = () => { if (state.mapReady) return; state.mapReady = true; addLayers(); drawRoutes(); };
    map.on("load", ready);
    // If the basemap cannot load (offline), still draw routes on a plain background.
    map.on("error", () => { if (!state.mapReady && map.isStyleLoaded && !map.isStyleLoaded()) setTimeout(() => { try { if (!state.mapReady && map.getStyle()) ready(); } catch (_) {} }, 1500); });
    setTimeout(() => { if (!state.mapReady) { try { map.setStyle({ version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#0a0f1c" } }] }); map.once("styledata", ready); } catch (_) {} } }, 6000);
  }
  function addLayers() {
    const m = state.map;
    const empty = { type: "FeatureCollection", features: [] };
    for (const id of ["prev", "routes", "closed", "restricted"]) m.addSource(id, { type: "geojson", data: empty });
    const lineBase = { "line-cap": "round", "line-join": "round" };
    m.addLayer({ id: "prev", type: "line", source: "prev", layout: lineBase, paint: { "line-color": "#c9d1e6", "line-width": 3, "line-opacity": 0.55, "line-dasharray": [0.6, 2] } });
    m.addLayer({ id: "route-other", type: "line", source: "routes", filter: ["==", ["get", "sel"], 0], layout: lineBase,
      paint: { "line-color": ["get", "color"], "line-width": 5, "line-opacity": 0.8 } });
    m.addLayer({ id: "route-casing", type: "line", source: "routes", filter: ["==", ["get", "sel"], 1], layout: lineBase,
      paint: { "line-color": "#060a14", "line-width": 10, "line-opacity": 0.9 } });
    m.addLayer({ id: "route-lit", type: "line", source: "routes", filter: ["all", ["==", ["get", "sel"], 1], ["==", ["get", "lit"], "yes"]], layout: lineBase,
      paint: { "line-color": ["get", "color"], "line-width": 6 } });
    m.addLayer({ id: "route-unlit", type: "line", source: "routes", filter: ["all", ["==", ["get", "sel"], 1], ["==", ["get", "lit"], "no"]], layout: lineBase,
      paint: { "line-color": COLORS.unlit, "line-width": 6 } });
    m.addLayer({ id: "route-unknown", type: "line", source: "routes", filter: ["all", ["==", ["get", "sel"], 1], ["==", ["get", "lit"], "unknown"]], layout: lineBase,
      paint: { "line-color": COLORS.unknown, "line-width": 6 } });
    m.addLayer({ id: "restricted", type: "line", source: "restricted", layout: lineBase, paint: { "line-color": COLORS.preferred, "line-width": 5, "line-opacity": 0.9, "line-dasharray": [0.4, 1.6] } });
    m.addLayer({ id: "closed-casing", type: "line", source: "closed", layout: lineBase, paint: { "line-color": "#2b0d09", "line-width": 11 } });
    m.addLayer({ id: "closed", type: "line", source: "closed", layout: lineBase, paint: { "line-color": COLORS.alert, "line-width": 6, "line-dasharray": [0.7, 1.3] } });
    for (const id of ["route-other"]) m.on("click", id, (e) => { const k = e.features[0].properties.route; if (k) select(k); });
  }
  const fc = (features) => ({ type: "FeatureCollection", features });
  const line = (coords, props) => ({ type: "Feature", properties: props || {}, geometry: { type: "LineString", coordinates: coords } });
  function drawRoutes() {
    const m = state.map, d = state.data;
    if (!m || !state.mapReady) return;
    state.markers.forEach((mk) => mk.remove()); state.markers = [];
    if (!d) { for (const id of ["prev", "routes", "closed", "restricted"]) m.getSource(id).setData(fc([])); return; }
    const feats = [];
    const order = state.selected === "fastest" ? ["preferred", "fastest"] : ["fastest", "preferred"];
    for (const k of order) {
      if (d.identical && k === "fastest") continue;
      const sel = k === state.selected || d.identical ? 1 : 0;
      for (const s of d[k].segments) feats.push(line(s.coords, { route: k, lit: s.lit, sel, color: COLORS[k] }));
    }
    m.getSource("routes").setData(fc(feats));
    const closed = [], restricted = [];
    for (const en of d.changes.entries) {
      for (const g of en.geometry) (en.action === "close" ? closed : restricted).push(line(g));
      const all = en.geometry.flat();
      if (all.length) {
        const mid = all[Math.floor(all.length / 2)];
        const el = document.createElement("div");
        el.className = "mk-x" + (en.action === "close" ? "" : " mk-r"); el.textContent = en.action === "close" ? "✕" : "!";
        el.title = en.street || "";
        el.addEventListener("click", () => openChange());
        state.markers.push(new maplibregl.Marker({ element: el }).setLngLat(mid).addTo(m));
      }
    }
    m.getSource("closed").setData(fc(closed));
    m.getSource("restricted").setData(fc(restricted));
    const prev = [];
    if (d.changes.changed && d.changes.previous) {
      const k = d.identical ? "preferred" : state.selected;
      if (d.changes[k + "_changed"]) for (const s of d.changes.previous[k].segments) prev.push(line(s.coords));
    }
    m.getSource("prev").setData(fc(prev));
    const mk = (cls, p) => { const el = document.createElement("div"); el.className = "mk " + cls; state.markers.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(m)); };
    mk("from", d.from); mk("to", d.to);
  }
  function fitRoutes() {
    const m = state.map, d = state.data;
    if (!m || !d) return;
    const b = new maplibregl.LngLatBounds();
    for (const k of ["fastest", "preferred"]) for (const s of d[k].segments) for (const c of s.coords) b.extend(c);
    if (d.changes.previous) for (const k of ["fastest", "preferred"]) for (const s of d.changes.previous[k].segments) for (const c of s.coords) b.extend(c);
    const top = $("trip").getBoundingClientRect().bottom + 16;
    const wide = window.innerWidth >= 700;
    const bottom = wide ? 30 : $("sheet").getBoundingClientRect().height + 40;
    const left = wide ? 460 : 30;
    try { m.fitBounds(b, { padding: { top, bottom, left, right: 30 }, duration: 500, maxZoom: 16 }); } catch (_) { /* tiny viewport */ }
  }

  // ---------- rendering ----------
  function renderTrip() {
    const f = placeById(state.from), t = placeById(state.to);
    $("from-name").textContent = f ? f.name : "Choose start";
    $("to-name").textContent = t ? t.name : "Choose destination";
    $("times").innerHTML = TIMES.map((x) => `<button class="time-chip" data-time="${x.key}" aria-pressed="${state.timeKey === x.key}">${x.label()}</button>`).join("");
    document.documentElement.style.setProperty("--trip-h", $("trip").getBoundingClientRect().bottom + "px");
  }
  function bar(share) {
    const parts = [["yes", share.yes], ["no", share.no], ["unknown", share.unknown]].filter((p) => p[1] > 0.004);
    return `<div class="bar" role="img" aria-label="${pct(share.yes)} recorded lit, ${pct(share.no)} recorded unlit, ${pct(share.unknown)} not recorded">${parts.map((p) => `<i class="s-${p[0]}" style="flex:${p[1]}"></i>`).join("")}</div>`;
  }
  function card(kind, r, d) {
    const label = kind === "fastest" ? "Fastest" : "Better lit";
    const cls = kind === "fastest" ? "fast" : "pref";
    let sub = km(r.length_m);
    if (kind === "preferred" && !d.identical) { const x = Math.round(d.extra_s / 60); sub += x >= 1 ? ` · <em>+${x} min</em>` : " · <em>same time</em>"; }
    if (d.identical) sub += " · fastest is also best lit";
    const unk = r.lighting_share.unknown > 0.004 ? ` · ${pct(r.lighting_share.unknown)} unknown` : "";
    return `<button class="route ${cls}" data-route="${kind}" aria-pressed="${state.selected === kind || d.identical}">
      <span class="r-label"><i class="r-swatch"></i>${d.identical ? "Fastest &amp; best lit" : label}</span>
      <span class="r-eta">${r.minutes}<small>min</small></span>
      <span class="r-sub">${sub}</span>
      ${bar(r.lighting_share)}
      <span class="r-lit"><b>${pct(r.lighting_share.yes)}</b> lit${unk}</span>
    </button>`;
  }
  function explain(d) {
    const f = d.fastest, p = d.preferred, v = state.config.walk_speed_mps;
    if (d.identical) return `Tonight the quickest way is also the best-lit one we can find: ${pct(p.lighting_share.yes)} of it is on streets recorded as lit. <span class="dim">Via ${esc(p.via.slice(0, 2).join(" and "))}.</span>`;
    const x = Math.round(d.extra_s / 60);
    const bits = [];
    const dl = p.lighting_share.yes - f.lighting_share.yes;
    if (Math.abs(dl) >= 0.02) bits.push(`${pct(p.lighting_share.yes)} on streets recorded as lit, against ${pct(f.lighting_share.yes)}`);
    const iso = Math.round(((f.exposure_s.isolated - p.exposure_s.isolated) * v) / 10) * 10;
    if (iso >= 50) bits.push(`${km(iso)} less on paths away from any road`);
    const enc = Math.round((f.exposure_s.enclosed - p.exposure_s.enclosed) * v);
    if (enc >= 20) bits.push(`avoids ${km(enc)} of underpass or covered passage`);
    const lead = x >= 1 ? `<b>Better lit</b> takes ${x} min longer` : `<b>Better lit</b> takes about the same time`;
    const why = bits.length ? `: ${bits.join(", ")}.` : ", with slightly better recorded conditions.";
    return `${lead}${why} <span class="dim">Via ${esc(p.via.slice(0, 2).join(" and "))}.</span>`;
  }
  function renderBanner(d) {
    const el = $("banner"), ch = d.changes;
    if (!ch.entries.length) { el.hidden = true; return; }
    const en = ch.entries.find((e) => e.action === "close") || ch.entries[0];
    const tag = en.synthetic ? '<span class="tag tag-replay">REPLAY</span>' : '<span class="tag tag-live">LIVE</span>';
    const what = en.action === "close" ? "closed to pedestrians" : "footway restricted";
    const key = d.identical ? "preferred" : state.selected;
    const delta = ch[key + "_delta_s"] || 0;
    let sub;
    if (ch.changed && ch[key + "_changed"]) sub = `Route changed${Math.abs(delta) >= 30 ? `, ${delta > 0 ? "+" : "−"}${mins(delta)} min` : ""}. Dotted line: previous route.`;
    else if (ch.changed) sub = "The other route changed; this one is unaffected.";
    else sub = "On your route; it stays walkable.";
    el.innerHTML = `<span class="b-icon">${en.action === "close" ? "✕" : "!"}</span>
      <span><b>${tag}${esc(en.street || "Street")} ${what}</b><span class="b-sub">${sub}</span></span>
      <span class="b-more">Why?</span>`;
    el.hidden = false;
  }
  function renderNotice(d) {
    const el = $("notice"), parts = [];
    for (const z of d.closed_zones) {
      const t = z.opens ? `closed until ${hhmm(z.opens)}` : `gates close ${hhmm(z.closes)}`;
      parts.push(`<b>${esc(z.label)}</b> ${t}`);
    }
    if (d.overlay.stale) parts.push("Disruption data has not been refreshed recently");
    if (!parts.length) { el.hidden = true; return; }
    el.innerHTML = parts.join(" · ") + (d.closed_zones.length ? ": routes go around." : ".");
    el.hidden = false;
  }
  function renderAgentChip() {
    const d = state.data; if (!d) return;
    const o = d.overlay, dot = $("agent-dot");
    dot.className = "pulse" + (o.store_error ? " bad" : o.stale ? " warn" : "");
    const mode = o.mode === "replay" && o.active_entries ? "replay data" : "TfL feed";
    const n = o.active_entries;
    $("agent-chip-text").textContent = o.last_run_at
      ? `Agent checked ${mode} ${ago(o.age_s)} · ${n ? n + " update" + (n > 1 ? "s" : "") + " active" : "no changes needed"}`
      : "Agent has not run yet";
  }
  function render() {
    const d = state.data, cards = $("cards");
    renderTrip();
    if (state.loading && !d) {
      cards.className = "cards"; cards.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
      $("explain").textContent = "Finding walking routes…"; $("legend").hidden = true; $("banner").hidden = true; $("notice").hidden = true;
      return;
    }
    if (state.lastError) {
      const e = state.lastError;
      const titles = { no_route: "No open walking route", outside_coverage: "Outside our coverage", same_place: "Pick two different places" };
      cards.className = "cards single";
      cards.innerHTML = `<div class="error"><b>${esc(titles[e.code] || "Couldn't load routes")}</b><p>${esc(e.message || "Check your connection and try again.")}</p><button class="btn" id="btn-retry">Try again</button></div>`;
      $("explain").textContent = ""; $("legend").hidden = true; $("banner").hidden = true; $("notice").hidden = true;
      return;
    }
    if (!d) return;
    cards.className = "cards" + (d.identical ? " single" : "");
    cards.innerHTML = d.identical ? card("preferred", d.preferred, d) : card("fastest", d.fastest, d) + card("preferred", d.preferred, d);
    $("explain").innerHTML = explain(d);
    $("legend").hidden = false;
    renderBanner(d); renderNotice(d); renderAgentChip();
    syncSheetHeight();
  }
  function syncSheetHeight() {
    document.documentElement.style.setProperty("--sheet-h", Math.round($("sheet").getBoundingClientRect().height) + "px");
  }

  // ---------- data ----------
  let reqSeq = 0;
  async function loadRoutes(opts = {}) {
    const seq = ++reqSeq;
    state.loading = true; state.lastError = null;
    if (!opts.keep) state.data = null;
    render(); if (!opts.keep) drawRoutes();
    const t = TIMES.find((x) => x.key === state.timeKey) || TIMES[0];
    const q = new URLSearchParams({ from: state.from, to: state.to, when: t.when() });
    try {
      const d = await api("/api/route?" + q);
      if (seq !== reqSeq) return;
      state.data = d; state.overlayVersion = d.overlay.version;
      if (d.identical) state.selected = "preferred";
    } catch (e) {
      if (seq !== reqSeq) return;
      state.data = null; state.lastError = e;
    }
    state.loading = false;
    render(); drawRoutes();
    requestAnimationFrame(() => { if (!opts.keepView) fitRoutes(); });
  }
  function select(kind) {
    if (!state.data || state.data.identical || state.selected === kind) return;
    state.selected = kind; render(); drawRoutes();
  }
  async function pollOverlay() {
    try {
      const o = await api("/api/overlay?refresh=true");
      if (state.overlayVersion != null && o.version !== state.overlayVersion) {
        const n = o.entries.filter((e) => e.active_now).length;
        toast(`Agent published update v${o.version}: ${n ? n + " active change" + (n > 1 ? "s" : "") : "previous changes withdrawn"}`);
        state.overlayVersion = o.version;
        await loadRoutes({ keep: true });
      } else if (state.data) {
        Object.assign(state.data.overlay, { age_s: o.age_s, last_run_at: o.last_run_at, stale: o.stale, store_error: o.store_error, last_run_mode: o.last_run_mode });
        renderAgentChip();
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
  const PILL = { publish_close: ["pub", "Closed"], publish_restrict: ["res", "Restricted"], carried: ["res", "Kept"], ignore_not_pedestrian: ["ign", "No effect on foot"], ignore_cleared: ["ign", "Already cleared"],
    reject_uncertain: ["rej", "Held: not explicit"], reject_location: ["rej", "Rejected: location"], reject_stale: ["rej", "Rejected: stale"], reject_unverified_evidence: ["rej", "Rejected: evidence"],
    reject_validation: ["rej", "Rejected: tests"], reject_conflict: ["rej", "Rejected: conflict"], reject_not_active: ["rej", "Not active"], defer_future: ["ign", "Future"], withdrawn: ["ign", "Withdrawn"], expired: ["ign", "Expired"], hold_assessment_failed: ["rej", "Held: error"] };
  function decisionHtml(x) {
    const [cls, label] = PILL[x.decision] || ["ign", x.decision];
    const syn = String(x.event_id || "").startsWith("REPLAY-") ? '<span class="tag tag-replay">SYNTHETIC</span>' : "";
    const checks = x.match && x.match.checks ? `<div class="checks">Location checks: ${esc(x.match.checks.join(" · "))}${x.match.notes && x.match.notes.length ? " · " + esc(x.match.notes.join(" · ")) : ""}</div>` : "";
    const segs = x.edge_ids && x.edge_ids.length ? `<div class="checks">${x.edge_ids.length} network segments ${x.action === "close" ? "closed" : x.action === "restrict" ? "penalised" : "affected"}</div>` : "";
    const src = x.source_url && !syn ? ` · <a href="${esc(x.source_url)}" target="_blank" rel="noopener">source</a>` : "";
    return `<div class="dec"><div class="dec-head">${syn}<span class="dec-id">${esc(x.event_id)}</span><span class="pill ${cls}">${label}</span></div>
      <p>${esc(x.location || "")}</p>
      ${x.evidence ? `<blockquote>“${esc(x.evidence)}”</blockquote>` : ""}
      <p>${esc(x.reason || "")}${src}</p>${checks}${segs}</div>`;
  }
  async function openAgent() {
    openModal("Agent activity", '<p class="note">Loading…</p>');
    let lr, ov, st;
    try { [lr, ov, st] = await Promise.all([api("/api/agent/last-run"), api("/api/overlay"), api("/api/agent/status")]); }
    catch (e) { $("modal-content").innerHTML = `<div class="error"><b>Couldn't load agent activity</b><p>${esc(e.message)}</p></div>`; return; }
    const run = lr.run;
    const order = (x) => (x.decision.startsWith("publish") ? 0 : x.decision.startsWith("reject") || x.decision.startsWith("hold") ? 1 : 2);
    const decs = lr.decisions.slice().sort((a, b) => order(a) - order(b));
    const checks = run && run.checks ? `${run.checks.total - (run.checks.failed || []).length}/${run.checks.total} baseline journeys still valid` : "n/a";
    const modeTag = run ? (run.mode === "replay" ? '<span class="tag tag-replay">REPLAY</span>captured feed + synthetic records' : '<span class="tag tag-live">LIVE</span>TfL Unified API') : "";
    const html = `
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
      <div class="sec">Demo controls</div>
      <div class="row">
        <button class="btn" data-run="live" ${st.running ? "disabled" : ""}>Run on live feed</button>
        <button class="btn ghost" data-run="replay" data-step="1" ${st.running ? "disabled" : ""}>Replay: burst main</button>
        <button class="btn ghost" data-run="replay" data-step="2" ${st.running ? "disabled" : ""}>Replay: main fixed</button>
      </div>
      <p class="note">The agent normally runs on a schedule. Replay uses the captured TfL feed plus clearly labelled synthetic records, and goes through the same checks as a live run.</p>`;
    $("modal-content").innerHTML = html;
  }
  function openChange() {
    const d = state.data; if (!d || !d.changes.entries.length) return;
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
    openModal("Why your route changed", html + `<p class="note">Overlay v${d.overlay.version}. Each update is published only after its quote is found in the source text, its location is matched to the walking network, and all baseline journeys still work.</p><div class="row"><button class="btn ghost" id="open-agent">See every decision</button></div>`);
  }
  async function triggerRun(mode, step) {
    try {
      const q = new URLSearchParams({ mode }); if (step) q.set("step", step);
      await api("/api/agent/run?" + q, { method: "POST" });
      closeModal(); toast(mode === "replay" ? "Replay run started…" : "Agent run started on the live TfL feed…", 6000);
      const t0 = Date.now();
      const tick = async () => {
        let st; try { st = await api("/api/agent/status"); } catch (_) { st = { running: true }; }
        if (st.running && Date.now() - t0 < 600000) return setTimeout(tick, 2500);
        await pollOverlay();
        if (st.last && st.last.status === "no_change") toast("Agent run finished: nothing needed to change");
        if (st.last && st.last.status === "failed") toast("Agent run failed; previous version kept");
      };
      setTimeout(tick, 1500);
    } catch (e) { toast(e.message || "Could not start the run"); }
  }

  // ---------- events ----------
  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("button, [data-close]"); if (!t) return;
    if (t.dataset.close !== undefined) return closeModal();
    if (t.id === "btn-from") return openPlaces("from");
    if (t.id === "btn-to") return openPlaces("to");
    if (t.id === "btn-swap") { [state.from, state.to] = [state.to, state.from]; return loadRoutes(); }
    if (t.id === "btn-agent" || t.id === "open-agent") return openAgent();
    if (t.id === "btn-retry") return loadRoutes();
    if (t.id === "banner") return openChange();
    if (t.dataset.time) { state.timeKey = t.dataset.time; return loadRoutes(); }
    if (t.dataset.route) return select(t.dataset.route);
    if (t.dataset.place) {
      const which = t.dataset.which, other = which === "from" ? "to" : "from";
      if (state[other] === t.dataset.place) state[other] = state[which];
      state[which] = t.dataset.place; closeModal(); return loadRoutes();
    }
    if (t.dataset.run) return triggerRun(t.dataset.run, t.dataset.step);
  });
  $("banner").setAttribute("role", "button"); $("banner").tabIndex = 0;
  $("banner").addEventListener("click", openChange);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  window.addEventListener("resize", () => { renderTrip(); syncSheetHeight(); fitRoutes(); });
  window.__lantern = state;

  // ---------- boot ----------
  (async function boot() {
    render();
    try { state.config = await api("/api/config"); }
    catch (e) {
      $("cards").className = "cards single";
      $("cards").innerHTML = `<div class="error"><b>Can't reach Lantern</b><p>The route service is not responding. Check your connection.</p><button class="btn" onclick="location.reload()">Reload</button></div>`;
      $("explain").textContent = ""; return;
    }
    state.from = state.config.default.from; state.to = state.config.default.to;
    const p = new URLSearchParams(location.search);
    if (p.get("from") && placeById(p.get("from"))) state.from = p.get("from");
    if (p.get("to") && placeById(p.get("to"))) state.to = p.get("to");
    if (p.get("t") && TIMES.some((x) => x.key === p.get("t"))) state.timeKey = p.get("t");
    renderTrip();
    if (window.maplibregl) initMap();
    await loadRoutes();
    setInterval(pollOverlay, 12000);
  })();
})();
