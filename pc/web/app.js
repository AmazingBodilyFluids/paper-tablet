// Paper Tablet interface. All state lives in Python (core.Engine); this page renders
// snapshot() and live() and calls window.pywebview.api for every action.
"use strict";

const api = () => window.pywebview.api;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
let S = null;            // last snapshot
let LIVE = { pen: null }; // last live pen sample
let view = "home";
let calMode = "sheet9";
let editing = null;      // element the user is typing in: don't overwrite it from snapshots

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("on");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("on"), 2600);
}

// ------------------------------------------------------------------ boot and refresh

window.addEventListener("pywebviewready", async () => {
  for (let i = 0; i < 100 && !(await api().ready()); i++) await sleep(100);
  $("#appVersion").textContent = await api().version();
  await refresh();
  if (S && !S.cfg.onboarded) Wizard.open();
  setInterval(refresh, 1000);
  liveLoop();
});
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pending = false;
window.onEngineChange = () => {
  if (pending) return;
  pending = true;
  setTimeout(() => { pending = false; refresh(); }, 60);
};

async function refresh() {
  try {
    S = await api().snapshot();
  } catch (e) { return; }
  if (!S) return;
  render();
}

async function liveLoop() {
  for (;;) {
    const visible = ["home", "calibrate", "pen"].includes(view) && !document.hidden;
    if (visible && S && S.connected) {
      try { LIVE = await api().live(); } catch (e) { LIVE = { pen: null }; }
    } else {
      LIVE = { pen: null };
    }
    try { drawLive(); } catch (e) { console.error("live", e); }
    await sleep(visible ? 33 : 250);
  }
}

// ------------------------------------------------------------------ status text

function status() {
  if (!S) return { dot: "busy", lbl: "Starting", title: "Getting ready…", text: "" };
  const st = S.status;
  if (S.calibrating) return { dot: "busy", lbl: "Calibrating", title: S.calibrating.prompt, text: "Follow along in the calibration window." };
  if (S.connected) {
    const bits = [];
    bits.push(S.output ? "The pen is drawing in Windows." : "The pen is connected but not sent to Windows yet.");
    if (S.screen) bits.push("Your strokes show on the tablet screen.");
    else bits.push("The tablet screen was closed on the tablet, so your notebooks are showing.");
    return { dot: "ok", lbl: "Connected", title: S.output ? "Ready. Draw away." : "Connected", text: bits.join(" ") };
  }
  if (st.kind === "connecting") return { dot: "busy", lbl: "Connecting", title: "Connecting…", text: st.text };
  if (st.kind === "error") return { dot: "err", lbl: "Problem", title: "Couldn't connect", text: st.text };
  if (!S.cfg.onboarded) return { dot: "warn", lbl: "Setup", title: "Let's set up your tablet", text: "The setup guide walks you through it step by step." };
  if (S.ssh && !S.cfg.auto_connect) return { dot: "warn", lbl: "Tablet found", title: "Tablet plugged in", text: "Press Connect to start." };
  if (S.link && !S.ssh) return { dot: "warn", lbl: "Tablet found", title: "Almost there", text: "The tablet is plugged in but isn't accepting connections. Is developer mode on? Open the setup guide for help." };
  return { dot: "", lbl: "Waiting", title: "Plug in your tablet", text: "Connect the reMarkable with its USB cable and unlock it. Everything starts by itself." };
}

// ------------------------------------------------------------------ render

// Title bar: one light for the tablet's connection, one for the pen reaching Windows.
function indicators(s) {
  const st = S.status.kind;
  const [td, tt] = S.connected ? ["ok", S.via === "wifi" ? "Tablet on Wi-Fi" : "Tablet connected"]
    : st === "connecting" ? ["busy", "Connecting…"] : st === "error" ? ["err", "Can't connect"]
    : S.ssh || S.link ? ["warn", "Tablet found"] : ["", "No tablet"];
  $("#tabDot").className = "dot " + td;
  $("#indTab").className = "ind " + td;
  $("#indTab").title = tt + (s.text && !S.connected ? ". " + s.text : "");
  $("#indTab").setAttribute("aria-label", tt);
  const [pd, pt] = !S.connected ? ["", "Pen off"] : S.calibrating ? ["busy", "Calibrating"]
    : S.output ? ["ok", "Pen active"] : ["warn", "Pen paused"];
  $("#penDot").className = "dot " + pd;
  $("#indPen").className = "ind " + pd;
  $("#indPen").title = pt + (!S.connected ? ": connect the tablet to use the pen" : S.output
    ? ": the pen draws in Windows" : ": the pen isn't sent to Windows. Turn on Pen in Windows next to this, or from the tray icon");
  $("#indPen").setAttribute("aria-label", pt);
}

// Home's banner: only while something needs doing, so a working setup leaves the picture alone.
function banner(s) {
  const ok = S.connected && S.calibration && !S.calibrating && S.screen;
  $("#banner").hidden = !!ok;
  if (ok) return;
  const uncal = S.connected && !S.calibrating && !S.calibration;
  const closed = S.connected && !S.calibrating && !S.screen;
  $("#banDot").className = "dot " + (uncal ? "warn" : s.dot);
  $("#banTitle").textContent = closed ? "Tablet screen closed" : uncal ? "Not calibrated yet" : s.title;
  $("#banText").textContent = closed ? "The power button on the tablet handed it back to your notebooks. The pen still works in Windows."
    : uncal ? "The cursor follows the desk position picked under Calibrate, using the whole tablet. Calibrate once for exact tracking." : s.text;
  const acts = $("#banActions");
  acts.innerHTML = "";
  if (!S.cfg.onboarded) acts.append(button("Open the setup guide", "acc", () => Wizard.open()));
  else if (!S.connected && S.ssh) acts.append(button("Connect", "acc", () => api().connect()));
  else if (closed) acts.append(button("Show it again", "acc", () => { api().restart_screen(); toast("Starting the tablet screen…"); }));
  else if (uncal) acts.append(button("Calibrate now", "acc", () => Cal.open()));
  else if (S.calibrating) acts.append(button("Show calibration", "", () => Cal.open(2)));
  else if (S.status.kind === "error") acts.append(button("Try again", "", () => api().connect()));
}

function render() {
  const s = status();
  indicators(s);
  banner(s);

  if (document.activeElement !== $("#wifiOn")) $("#wifiOn").checked = !!S.cfg.wifi;
  $("#wifiInfo").textContent = S.cfg.wifi
    ? (S.via === "wifi" && S.connected ? `Connected over Wi-Fi to ${S.cfg.wifi_host}.` : `When the cable is out, the app looks for the tablet at ${S.cfg.wifi_host || "?"}. Its address is refreshed whenever you plug in.`)
    : "Turn this on with the cable plugged in and the tablet on your Wi-Fi.";
  $("#devName").textContent = S.device ? S.model.name + (S.model.verified ? "" : " (beta)") : (S.ssh ? "Tablet found" : "Not connected");
  $("#devMeta").textContent = S.device ? `${S.cfg.host} · kernel ${S.device.kernel}` : (S.ssh ? S.cfg.host : "Plug in the USB cable");

  setTbt("#tgOutput", S.output, S.connected, "Pen in Windows", "draw in Photoshop, Krita, anything");
  for (const seg of $$(".maskSide")) {
    const v = S.cfg.mask_panel === false ? "off" : S.cfg.mask_side || "right";
    for (const b of $$("button", seg)) b.classList.toggle("on", b.dataset.v === v);
  }
  // front light: the level in use, else the one saved for next time
  const lv = S.light ?? S.cfg.light_level;
  $("#lightRow").hidden = !S.model.frontlight;
  if (document.activeElement !== $("#lightR")) {
    $("#lightR").value = lv ?? 0; paintRange($("#lightR"));
    $("#lightV").textContent = lv == null ? "yours" : lv ? lv + "%" : "off";
  }
  setTbt("#tgSnap", !!S.cfg.snap, true, "Shapes", "draw a line, circle, rectangle or triangle and hold the pen still to snap it clean");

  if (Date.now() > themeHold) applyTheme(S.cfg.theme || "intaglio");

  // bound settings
  for (const el of $$("[data-key]")) {
    const k = el.dataset.key, v = S.cfg[k];
    if (el === editing) continue;
    if (el.classList.contains("seg")) {
      for (const b of $$("button", el)) b.classList.toggle("on", b.dataset.v === String(v));
    } else if (el.type === "checkbox") el.checked = !!v;
    else if (el.type === "range") { el.value = v; paintRange(el); }
    else if (el.tagName === "SELECT" && k === "monitor") fillMonitors(el);
    else el.value = v ?? "";
  }
  $("#gammaV").textContent = (+S.cfg.gamma).toFixed(2);
  $("#threshV").textContent = Math.round(S.cfg.threshold * 100) + "%";
  $("#paperV").textContent = (+S.cfg.paper_mm).toFixed(1).replace(/\.0$/, "") + " mm";
  $("#traceV").textContent = Math.round(S.cfg.trace_opacity * 100) + "%";
  const tf = S.cfg.trace_file || "";
  $("#traceName").textContent = tf ? tf.split(/[\\/]/).pop() : "None. Show a photo or sketch under your strokes to trace over.";
  $("#traceName").title = tf;
  $("#maskConV").textContent = "×" + (S.cfg.mask_contrast || 1);
  $("#maskInv").classList.toggle("on", !!S.cfg.mask_invert);
  $("#traceDel").disabled = !tf;
  renderMasks();
  $("#maskInfo").textContent = !S.cfg.mask_active ? "" : S.mask_pct ? `${S.mask_pct}% of the screen is masked.` : "Nothing masked.";
  $("#maskModeHint").textContent = S.cfg.mask_mode === "gate"
    ? "The darker the grey, the harder you press before ink starts. Black: no pen."
    : "Pressure is scaled by the shade: mid grey gives half, so half the opacity or size in a pressure brush. Black: no pen.";
  if (document.activeElement !== $("#trailR")) { $("#trailR").value = trailToSlider(S.cfg.trail_ms); paintRange($("#trailR")); }
  $("#trailV").textContent = trailText(S.cfg.trail_ms);
  $("#fitHint").textContent = {
    crop: "The whole display is reachable; a slice of the tablet goes unused if the shapes differ.",
    letterbox: "The whole tablet is used; parts of the display can't be reached if the shapes differ.",
    stretch: "Tablet stretched to the display. Circles may come out as ovals.",
  }[S.cfg.fit] || "";
  const a = S.cfg.area;
  $("#areaText").textContent = `left ${pct(a[0])} · top ${pct(a[1])} · right ${pct(a[2])} · bottom ${pct(a[3])}`;
  $("#ratios").innerHTML = ratiosHtml();

  const ago = t => Math.max(0, Math.round(Date.now() / 1000 - t));
  const sl = S.snap_last;
  $("#snapLast").textContent = sl ? `Last: ${sl.text} (${ago(sl.at)} s ago).` : "";
  const la = S.last_action;
  $("#lastAction").textContent = (la ? `Last from the tablet: ${la.action} (${ago(la.at)} s ago).` : "") +
    (S.held_keys && S.held_keys.length ? ` Held down now: ${S.held_keys.join(", ")}.` : "");
  const log = $("#scrLog");
  log.textContent = (S.screen_log || []).join("\n") || "Nothing yet.";
  log.scrollTop = log.scrollHeight;

  renderCalibration();
  Panel.render();
  drawStatic();
  drawCurve();
  Scr.draw();
  if (Wizard.isOpen) Wizard.render();
  Cal.render();
}

const pct = v => Math.round(v * 100) + "%";

function button(text, cls, fn) {
  const b = document.createElement("button");
  b.className = "btn " + cls;
  b.textContent = text;
  b.onclick = fn;
  return b;
}

// a title bar switch: lit when on; its tooltip names it and says what it does
function setTbt(id, on, enabled, name, what) {
  const b = $(id);
  b.classList.toggle("on", on);
  b.disabled = !enabled;
  b.setAttribute("aria-pressed", on);
  b.title = `${name}: ${on ? "on" : "off"}${enabled ? "" : " (connect the tablet first)"}. Click to turn it ${on ? "off" : "on"}; ${what}.`;
  b.setAttribute("aria-label", name);
}

function fillMonitors(el) {
  const sig = JSON.stringify(S.monitors);
  if (el.dataset.sig !== sig) {
    el.innerHTML = S.monitors.map(m => `<option value="${m.index}">${m.label}</option>`).join("");
    el.dataset.sig = sig;
  }
  el.value = S.cfg.monitor;
}

function paintRange(el) {
  const p = (el.value - el.min) / (el.max - el.min) * 100;
  el.style.setProperty("--p", p + "%");
}

// ------------------------------------------------------------------ inputs

async function setKey(k, v) {
  S.cfg[k] = v;
  await api().set(k, v);
}

document.addEventListener("change", e => {
  const el = e.target.closest("[data-key]");
  if (!el || el.classList.contains("seg")) return;
  let v = el.type === "checkbox" ? el.checked : el.value;
  if (el.dataset.type === "int") v = parseInt(v, 10);
  if (el.dataset.type === "float") v = parseFloat(v);
  setKey(el.dataset.key, v);
  if (el.dataset.key === "auto_connect" && v) api().connect();
});
document.addEventListener("input", e => {
  const el = e.target;
  if (el.type === "range" && el.dataset.key) {
    paintRange(el);
    const v = el.dataset.type === "int" ? parseInt(el.value, 10) : parseFloat(el.value);
    S.cfg[el.dataset.key] = v;
    if (el.dataset.key === "paper_mm") $("#paperV").textContent = v.toFixed(1).replace(/\.0$/, "") + " mm";
    if (el.dataset.key === "trace_opacity") $("#traceV").textContent = Math.round(v * 100) + "%";
    if (el.dataset.key === "mask_contrast") { $("#maskConV").textContent = "×" + v; Scr.draw(); }
    if (el.dataset.key === "gamma") $("#gammaV").textContent = v.toFixed(2);
    if (el.dataset.key === "threshold") $("#threshV").textContent = Math.round(v * 100) + "%";
    drawCurve();
    clearTimeout(el._t);
    el._t = setTimeout(() => api().set(el.dataset.key, v), 120);
  }
});
document.addEventListener("focusin", e => { if (e.target.matches("input.input")) editing = e.target; });
document.addEventListener("focusout", e => { if (e.target === editing) editing = null; });
for (const seg of $$(".seg[data-key]")) {
  seg.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    for (const x of $$("button", seg)) x.classList.toggle("on", x === b);
    setKey(seg.dataset.key, b.dataset.v).then(refresh);
  });
}
$("#tgOutput").onclick = () => { S.output = !S.output; render(); api().set_output(S.output); };
for (const seg of $$(".maskSide")) {  // the mask strip's side, or off (mask_side + mask_panel)
  seg.addEventListener("click", async e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.v === "off") await setKey("mask_panel", false);
    else { await setKey("mask_side", b.dataset.v); await setKey("mask_panel", true); }
    refresh();
  });
}
$("#lightR").addEventListener("input", e => {
  paintRange(e.target);
  $("#lightV").textContent = +e.target.value ? e.target.value + "%" : "off";
});
$("#lightR").addEventListener("change", e => { api().set_light(+e.target.value); e.target.blur(); });
$("#tgSnap").onclick = () => setKey("snap", !S.cfg.snap).then(refresh);
$("#btnConnect").onclick = () => api().connect();
$("#btnDisconnect").onclick = () => api().disconnect();

function setArea(a) { setKey("area", a.map(v => Math.round(v * 1000) / 1000)).then(refresh); }

// ------------------------------------------------------------------ navigation

function go(v) {
  view = v;
  for (const n of $$(".nav[data-view]")) n.classList.toggle("on", n.dataset.view === v);
  for (const s of $$(".view")) s.classList.toggle("on", s.id === "view-" + v);
  requestAnimationFrame(() => { drawStatic(); drawCurve(); Scr.draw(); Panel.sig = ""; if (S) Panel.render(); });
}
for (const n of $$(".nav[data-view]")) n.onclick = () => go(n.dataset.view);

// ------------------------------------------------------------------ theme (Settings, web/themes.css)

// [key, name, swatch background, swatch accent]
const THEMES = [["default", "Paper Tablet", "#060e17", "#C4A882"], ["graphite", "Graphite", "#101215", "#d8bb8a"],
                ["ledger", "Ledger", "#f4f1e8", "#1f5c45"], ["intaglio", "Intaglio", "#0e1a15", "#cfb57a"]];
let theme = null;
let themeHold = 0;  // after a click, ignore snapshots taken before the new theme was saved

function applyTheme(t) {
  if (t === theme) return;
  theme = t;
  document.documentElement.dataset.theme = t;
  for (const b of $$("#themes button")) {
    b.classList.toggle("on", b.dataset.t === t);
    b.setAttribute("aria-checked", b.dataset.t === t);
  }
  $("#themeName").textContent = (THEMES.find(x => x[0] === t) || [, "Paper Tablet"])[1];
  themeColors();
  requestAnimationFrame(() => { drawStatic(); drawCurve(); Panel.sig = ""; if (S) Panel.render(); });
}
$("#themes").innerHTML = THEMES.map(([k, name, bg, acc]) =>
  `<button role="radio" data-t="${k}" aria-label="${name} theme"><i style="--sw-bg:${bg};--sw-acc:${acc}"></i>${name}</button>`).join("");
for (const b of $$("#themes button")) b.onclick = () => {
  themeHold = Date.now() + 1500;
  applyTheme(b.dataset.t);
  api().set("theme", b.dataset.t);
};

// ------------------------------------------------------------------ window edges and maximise

// The window has no frame, so grips along its edges resize it: Python reads the real cursor on
// each move (winextras.Resizer). One call in flight at a time; resize_end makes the last move.
for (const g of $$(".grips i")) g.onmousedown = async e => {
  if (e.button !== 0) return;
  e.preventDefault();
  let busy = false;
  const move = async () => {
    if (busy) return;
    busy = true;
    await api().resize_move();
    busy = false;
  };
  const up = () => {
    removeEventListener("mousemove", move);
    removeEventListener("mouseup", up);
    api().resize_end();
  };
  addEventListener("mousemove", move);
  addEventListener("mouseup", up);
  await api().resize_start(g.dataset.edge);
};
async function toggleMax() {
  document.documentElement.classList.toggle("maxed", await api().win_toggle_max());
}

// ------------------------------------------------------------------ aspect ratios

// Shapes as width:height, named when close to a common one (within 1.5%), else as n:1.
const RATIOS = [[1, 1], [5, 4], [4, 3], [3, 2], [16, 10], [5, 3], [16, 9], [2, 1], [21, 9], [32, 9]];
function ratioText(x) {
  if (!(x > 0) || !isFinite(x)) return "";
  const tall = x < 1, r = tall ? 1 / x : x;
  const m = RATIOS.find(([a, b]) => Math.abs(r / (a / b) - 1) < 0.015);
  if (m) return tall ? `${m[1]}:${m[0]}` : `${m[0]}:${m[1]}`;
  return tall ? `1:${r.toFixed(2)}` : `${r.toFixed(2)}:1`;
}
const areaAspect = a => S.aspect * (a[2] - a[0]) / (a[3] - a[1]);
function monitorNow() {
  return (S.monitors || []).find(m => m.index === S.cfg.monitor) || (S.monitors || [])[0];
}
function ratiosHtml() {
  const m = monitorNow(), A = areaAspect(S.cfg.area), D = m ? m.w / m.h : A;
  let out = `<span><b>active area</b> ${ratioText(A)}</span>` + (m ? `<span><b>display</b> ${ratioText(D)} · ${m.w} × ${m.h}</span>` : "");
  if (Math.abs(A / D - 1) < 0.015) return out + "<span>same shape: nothing cut off or stretched</span>";
  if (S.cfg.fit === "stretch")
    return out + `<span><b>stretched</b> ×${(D > A ? D / A : A / D).toFixed(2)} ${D > A ? "wider" : "taller"}</span>`;
  if (S.cfg.fit === "letterbox") return out + `<span><b>reachable</b> a ${ratioText(A)} part of the display</span>`;
  return out + `<span><b>used</b> a ${ratioText(D)} part of the area</span>`;
}

// ------------------------------------------------------------------ tablet preview canvases

// The tablet's body and paper screen keep their own colours; the rest follows the theme (themeColors).
const C = { bg: "#060e17", body: "#0f1d2c", screen: "#e9e6dc", screenEdge: "#23364a", used: "rgba(74,158,224,0.28)",
            usedEdge: "#4A9EE0", area: "#C4A882", pen: "#4A9EE0", down: "#e5484d", eraser: "#ff9d52", text: "rgba(255,255,255,0.5)",
            grid: "rgba(255,255,255,0.06)", frame: "rgba(196,168,130,0.18)", marker: "#fff", mono: "'IBM Plex Mono', Consolas, monospace" };

function themeColors() {
  const css = getComputedStyle(document.documentElement), v = n => css.getPropertyValue(n).trim();
  Object.assign(C, { bg: v("--bg"), used: v("--accent2-b"), usedEdge: v("--accent2"), area: v("--accent"), pen: v("--accent2"),
                     text: v("--dim2"), grid: v("--border2"), frame: v("--accent-b"), marker: v("--ink"), mono: v("--f-mono") });
}

function fitCanvas(cv) {
  const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(r.width * d) || cv.height !== Math.round(r.height * d)) {
    cv.width = Math.round(r.width * d);
    cv.height = Math.round(r.height * d);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(d, 0, 0, d, 0, 0);
  return [ctx, r.width, r.height];
}

function frameRect(w, h) {
  const aspect = S ? S.aspect : 4 / 3, pad = 34;
  let fw = w - 2 * pad, fh = h - 2 * pad;
  if (fw / fh > aspect) fw = fh * aspect; else fh = fw / aspect;
  return { x: (w - fw) / 2, y: (h - fh) / 2, w: fw, h: fh };
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

// A small label on the picture, anchored at a corner of its box (right / bottom: anchored there).
function tag(ctx, text, x, y, col, right, bottom) {
  ctx.font = `600 10.5px ${C.mono}`;
  const w = ctx.measureText(text).width + 14, h = 19, tx = right ? x - w : x, ty = bottom ? y - h : y;
  ctx.globalAlpha = 0.88; ctx.fillStyle = C.bg; roundRect(ctx, tx, ty, w, h, 3); ctx.fill(); ctx.globalAlpha = 1;
  ctx.fillStyle = col; ctx.textBaseline = "middle"; ctx.fillText(text, tx + 7, ty + h / 2 + 0.5); ctx.textBaseline = "alphabetic";
}

const statics = {};
function drawTablet(cv) {
  if (!S || !cv.offsetParent) return;
  const [ctx, w, h] = fitCanvas(cv);
  ctx.clearRect(0, 0, w, h);
  const f = frameRect(w, h);
  roundRect(ctx, f.x - 14, f.y - 14, f.w + 28, f.h + 28, 14);
  ctx.fillStyle = C.body; ctx.fill();
  ctx.strokeStyle = C.frame; ctx.lineWidth = 1; ctx.stroke();
  ctx.fillStyle = C.screen; ctx.fillRect(f.x, f.y, f.w, f.h);
  const u = S.used, cl = v => Math.min(1, Math.max(0, v));
  ctx.fillStyle = C.used;
  ctx.fillRect(f.x + cl(u[0]) * f.w, f.y + cl(u[1]) * f.h, (cl(u[2]) - cl(u[0])) * f.w, (cl(u[3]) - cl(u[1])) * f.h);
  ctx.strokeStyle = C.usedEdge; ctx.lineWidth = 1.5;
  ctx.strokeRect(f.x + cl(u[0]) * f.w, f.y + cl(u[1]) * f.h, (cl(u[2]) - cl(u[0])) * f.w, (cl(u[3]) - cl(u[1])) * f.h);
  const a = cv._drag || S.cfg.area;
  ctx.setLineDash([7, 4]); ctx.strokeStyle = C.area; ctx.lineWidth = 2;
  ctx.strokeRect(f.x + a[0] * f.w, f.y + a[1] * f.h, (a[2] - a[0]) * f.w, (a[3] - a[1]) * f.h);
  ctx.setLineDash([]);
  // the shapes: the active area's, and the display's (what the stretched area lands on)
  tag(ctx, "ACTIVE AREA " + ratioText(areaAspect(a)), f.x + a[0] * f.w + 6, f.y + a[1] * f.h + 6, C.area);
  const m = monitorNow();
  if (m && !cv._drag) {
    const d = ratioText(m.w / m.h);
    tag(ctx, S.cfg.fit === "stretch" ? "STRETCHED TO " + d : "DISPLAY " + d,
        f.x + cl(u[2]) * f.w - 6, f.y + cl(u[3]) * f.h - 6, C.usedEdge, true, true);
  }
  statics[cv.id] = ctx.getImageData(0, 0, cv.width, cv.height);
}

function drawStatic() {
  drawTablet($("#mapPreview"));
}

function drawLive() {
  for (const id of ["mapPreview", "scrPreview"]) {
    const cv = document.getElementById(id);
    if (!cv.offsetParent || !statics[id]) continue;
    const ctx = cv.getContext("2d");
    ctx.putImageData(statics[id], 0, 0);
    const p = LIVE.pen;
    const d = window.devicePixelRatio || 1, w = cv.width / d, h = cv.height / d;
    const f = id === "scrPreview" ? Scr.frame() : frameRect(w, h);
    if (!f) continue;
    drawFingers(ctx, f);
    if (!p) continue;
    const x = f.x + p.p * f.w, y = f.y + p.q * f.h;
    if (S.cfg.tilt && p.tilt) {
      ctx.strokeStyle = "rgba(20,30,40,0.55)"; ctx.lineWidth = 2; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + p.tilt[0] * 0.9, y + p.tilt[1] * 0.9); ctx.stroke();
    }
    const col = p.eraser ? C.eraser : (p.touching ? C.down : C.pen);
    const r = 5 + 11 * p.out / 1024;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
    if (p.touching) { ctx.fillStyle = col; ctx.fill(); }
    ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke();
  }
  const p = LIVE.pen, g = gestureText();
  $("#homeReadout").innerHTML = g ? `<span><b>fingers</b> ${esc(g)}</span>` : p
    ? `<span><b>${p.eraser ? "eraser" : "pen"}</b> ${p.touching ? "down" : "hover"}</span><span><b>pressure</b> ${Math.round(p.out / 10.24)}%</span>` +
      `<span><b>tilt</b> ${p.tilt[0]}°, ${p.tilt[1]}°</span><span><b>display</b> ${p.screen[0]}, ${p.screen[1]}</span>`
    : (S && S.connected ? "<span>Hover the pen over the tablet</span>" : "");
  if (view === "pen") {
    const max = S ? S.pressure_max : 4096, peak = LIVE.peak || 0;
    $("#peakBar").style.width = (100 * peak / max) + "%";
    $("#peakText").textContent = peak ? `Highest: ${Math.round(100 * peak / max)}% of the pen's range` : "Not pressed yet";
    if (p && p.touching) drawCurve(p.raw);
  }
}

// Fingers on the tablet: a ring per contact, grey when the pen or a palm has muted them.
function drawFingers(ctx, f) {
  const t = LIVE.touch;
  if (!t || !t.pts.length) return;
  ctx.lineWidth = 3;
  for (const [p, q] of t.pts) {
    const x = f.x + p * f.w, y = f.y + q * f.h;
    ctx.beginPath(); ctx.arc(x, y, 13, 0, Math.PI * 2);
    ctx.strokeStyle = t.live ? C.area : C.grid;
    ctx.fillStyle = t.live ? "rgba(120,170,255,0.25)" : "rgba(128,128,128,0.2)";
    ctx.fill(); ctx.stroke();
  }
}
const GESTURES = { undo: "swipe: undo", redo: "swipe: redo", zoomin: "pinch: zoom in", zoomout: "pinch: zoom out",
  "pan begin": "pan", "pan end": "pan", "rotate begin": "twist: rotate", "rotate end": "twist: rotate",
  "profile next": "next profile", "profile prev": "previous profile" };
function gestureText() {
  const t = LIVE.touch, a = LIVE.gesture || "";
  const n = t ? t.pts.length : 0;
  let g = GESTURES[a] || (a.startsWith("tap ") ? `${a.split(" ")[1]}-finger tap ×${a.split(" ")[2]}` : a.startsWith("press ") ? "panel button" : "");
  if (t && n && !t.live) g = `${n} finger${n > 1 ? "s" : ""} muted (pen near or palm)`;
  else if (n && !g) g = `${n} finger${n > 1 ? "s" : ""} down` + (S.touch_mode === "windows" ? ", sent to Windows as touch" : "");
  if (t && t.palms) g += (g ? " · " : "") + `${t.palms} palm contact${t.palms > 1 ? "s" : ""} ignored`;
  return g;
}

// ------------------------------------------------------------------ pen mask on the Home picture

// Where the mask comes from: the image it was made from (kept while this window is open, so moving
// and zooming it again stays sharp) or else the stored mask itself. s = zoom, ox / oy = offset as a
// fraction of the screen. The grid is rendered from it; the tablet gets it when a drag or zoom ends.
// Levels are 0 (open) .. 255 (pen ignored), MASK_N x MASK_N cells over the screen as it sits.
let MASK_N = 720;
let maskSrc = null, maskLoading = 0;
function gridCanvas(lv) {
  const c = document.createElement("canvas"); c.width = c.height = MASK_N;
  const g = c.getContext("2d"), d = g.createImageData(MASK_N, MASK_N);
  for (let i = 0; i < lv.length; i++) { const v = 255 - lv[i]; d.data[4 * i] = d.data[4 * i + 1] = d.data[4 * i + 2] = v; d.data[4 * i + 3] = 255; }
  g.putImageData(d, 0, 0);
  return c;
}
async function maskLoad(ver) {  // the stored mask from the PC, once per version
  maskLoading = ver;
  const m = await api().get_mask();
  if (!m || m.ver !== S.mask_ver || (maskSrc && maskSrc.ver === m.ver)) return;  // stale, or ours already
  MASK_N = m.n;
  const bin = atob(m.b64), lv = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) lv[i] = bin.charCodeAt(i);
  maskSrc = { el: gridCanvas(lv), grid: true, s: 1, ox: 0, oy: 0, ver: m.ver, lv };
  maskOverlays = {};
  Scr.draw();
}
function maskSource() {
  const committing = maskSrc && maskSrc.ver === null;
  if (!committing && (!maskSrc || maskSrc.ver !== S.mask_ver) && maskLoading !== S.mask_ver && S.mask_ver !== undefined)
    maskLoad(S.mask_ver);
  return maskSrc;
}
// The source drawn into the grid (white outside it): levels 0..255.
function maskRender(src) {
  const N = MASK_N, [vw, vh] = S.view || [1, 1], c = document.createElement("canvas");
  c.width = c.height = N;
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, N, N);
  g.imageSmoothingEnabled = !src.grid;
  let dw = N, dh = N;
  if (!src.grid) {
    const k = Math.max(vw / src.el.width, vh / src.el.height);
    dw = src.el.width * k * N / vw; dh = src.el.height * k * N / vh;
  }
  dw *= src.s; dh *= src.s;
  g.drawImage(src.el, (N - dw) / 2 + src.ox * N, (N - dh) / 2 + src.oy * N, dw, dh);
  const px = g.getImageData(0, 0, N, N).data, out = new Uint8Array(N * N);
  for (let i = 0; i < N * N; i++)
    out[i] = Math.round(255 - (0.2126 * px[4 * i] + 0.7152 * px[4 * i + 1] + 0.0722 * px[4 * i + 2]));
  return out;
}
let maskOverlays = {};  // by opacity, {key, canvas}: the shaded picture, redrawn only when something changes
// Black whose alpha is the level after contrast and invert (penmask.contrast_table), times a:
// what the tablet lays over its screen at a = 0.4.
function maskLayer(a) {
  const src = maskSource();
  if (!src) return null;
  const k = S.cfg.mask_contrast || 1, inv = !!S.cfg.mask_invert;
  const key = [src.ver, src.s, src.ox, src.oy, k, inv, src.el.width].join();
  let o = maskOverlays[a];
  if (!o || o.key !== key) {
    const lv = src.grid && src.s === 1 && !src.ox && !src.oy ? src.lv : maskRender(src);
    const c = document.createElement("canvas"); c.width = c.height = MASK_N;
    const g = c.getContext("2d"), d = g.createImageData(MASK_N, MASK_N);
    for (let i = 0; i < lv.length; i++)
      d.data[4 * i + 3] = Math.round(a * Math.max(0, Math.min(255, Math.floor(128 + ((inv ? 255 - lv[i] : lv[i]) - 127.5) * k))));
    g.putImageData(d, 0, 0);
    o = maskOverlays[a] = { key, canvas: c };
  }
  return o.canvas;
}
async function maskCommit(src = maskSource()) {
  if (!src) return;
  const lv = maskRender(src);
  let bin = "";
  for (let i = 0; i < lv.length; i += 0x8000) bin += String.fromCharCode.apply(null, lv.subarray(i, i + 0x8000));
  const ver = await api().set_mask(btoa(bin));
  src.ver = ver; S.mask_ver = ver; maskLoading = ver;  // the source (and its zoom / offset) stays
  if (src.grid) { src.lv = lv; }
  S.cfg.mask_on = true;
  refresh();
}
$("#dragHint").textContent = "Drag a box on the screen to use part of the tablet";

// drag on Calibrate's screen area picture: the active area
(() => {
  const cv = $("#mapPreview");
  let start = null;
  const frac = (e, clamp = true) => {
    const r = cv.getBoundingClientRect(), f = frameRect(r.width, r.height);
    const p = (e.clientX - r.left - f.x) / f.w, q = (e.clientY - r.top - f.y) / f.h;
    return clamp ? [Math.min(1, Math.max(0, p)), Math.min(1, Math.max(0, q))] : [p, q];
  };
  const box = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  const onScreen = e => { const [p, q] = frac(e, false); return p >= 0 && p <= 1 && q >= 0 && q <= 1; };
  cv.addEventListener("mousemove", e => {
    if (!start) cv.style.cursor = onScreen(e) ? "crosshair" : "default";
  });
  cv.addEventListener("mousedown", e => {
    if (onScreen(e)) start = frac(e);  // only the screen itself, not the frame or the space beside it
  });
  window.addEventListener("mousemove", e => {
    if (!start) return;
    cv._drag = box(start, frac(e));
    drawTablet(cv);
  });
  window.addEventListener("mouseup", e => {
    if (!start) return;
    const a = box(start, frac(e));
    start = null; cv._drag = null;
    if (a[2] - a[0] >= 0.05 && a[3] - a[1] >= 0.05) setArea(a); else drawTablet(cv);
  });
})();
window.addEventListener("resize", () => { drawStatic(); Scr.draw(); });

// ------------------------------------------------------------------ pressure curve

function pressureOut(raw) {
  const max = S ? S.pressure_max : 4096, th = +S.cfg.threshold, g = +S.cfg.gamma;
  let n = raw / max;
  if (n <= th) return 0;
  n = (n - th) / (1 - th);
  return Math.max(1, Math.min(1024, Math.round(1024 * Math.pow(n, g))));
}

function drawCurve(marker) {
  const cv = $("#curve");
  if (!S || !cv.offsetParent) return;
  const [ctx, w, h] = fitCanvas(cv);
  const pad = 26, W = w - 2 * pad, H = h - 2 * pad, max = S.pressure_max;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    ctx.beginPath(); ctx.moveTo(pad, pad + H * i / 4); ctx.lineTo(pad + W, pad + H * i / 4); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad + W * i / 4, pad); ctx.lineTo(pad + W * i / 4, pad + H); ctx.stroke();
  }
  ctx.setLineDash([3, 4]); ctx.strokeStyle = C.text; ctx.globalAlpha = 0.45;
  ctx.beginPath(); ctx.moveTo(pad, pad + H); ctx.lineTo(pad + W, pad); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  const grad = ctx.createLinearGradient(pad, 0, pad + W, 0);
  grad.addColorStop(0, C.pen); grad.addColorStop(1, C.area);
  ctx.strokeStyle = grad; ctx.lineWidth = 2.5; ctx.beginPath();
  for (let i = 0; i <= 200; i++) {
    const raw = max * i / 200, x = pad + W * i / 200, y = pad + H - H * pressureOut(raw) / 1024;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
  ctx.fillStyle = C.text; ctx.font = "10px 'IBM Plex Mono'";
  ctx.fillText("PEN PRESSURE →", pad, h - 7);
  ctx.save(); ctx.translate(11, pad + H); ctx.rotate(-Math.PI / 2); ctx.fillText("SENT TO APPS →", 0, 0); ctx.restore();
  if (marker != null) {
    const x = pad + W * marker / max, y = pad + H - H * pressureOut(marker) / 1024;
    ctx.fillStyle = C.marker; ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.fill();
  }
}

// ------------------------------------------------------------------ calibration

// How the tablet lies on the desk. The picture is the tablet in portrait (power button on the top
// edge and USB-C cable on the bottom edge, both near the left), turned the way it sits.
const DESK = [
  { key: "landscape-left-up", name: "Landscape, left side up", sub: "Cable on the left, near the top", turn: 90, tag: "most common" },
  { key: "landscape-right-up", name: "Landscape, right side up", sub: "Cable on the right, near the bottom", turn: -90 },
  { key: "portrait", name: "Portrait", sub: "Cable at the bottom, left", turn: 0 },
  { key: "portrait-flipped", name: "Upside down", sub: "Cable at the top, right", turn: 180 },
];
function deskSvg(turn) {
  return `<svg viewBox="0 0 110 96" fill="none" stroke-linecap="round"><g transform="rotate(${turn} 55 48)">
    <rect x="32" y="15" width="46" height="62" rx="4" fill="#0f1d2c" style="stroke:var(--accent)" stroke-width="1.4"/>
    <rect x="37.5" y="20.5" width="35" height="51" fill="#e9e6dc" opacity=".9"/>
    <rect x="37" y="10.5" width="11" height="4" rx="1.2" style="fill:var(--accent2)"/>
    <rect x="39.5" y="77" width="6" height="5" rx="1" style="fill:var(--dim)"/><path d="M42.5 82c0 5 3 6 3 12" style="stroke:var(--dim)" stroke-width="1.8"/>
  </g></svg>`;
}
function renderPositions(box) {
  if (!box || !S) return;
  const busy = !!S.calibrating;
  box.innerHTML = DESK.map(d => `<button class="pos ${S.position === d.key ? "on" : ""}" data-pos="${d.key}" ${busy ? "disabled" : ""}>
    ${deskSvg(d.turn)}<div>${d.name}</div><div class="sub">${d.sub}${d.tag ? ` · <span class="tag">${d.tag}</span>` : ""}</div></button>`).join("");
  for (const b of $$(".pos", box)) b.onclick = () => {
    if (S.calibrating) return;
    S.position = b.dataset.pos;
    for (const x of $$(".pos", box)) x.classList.toggle("on", x === b);
    api().set_position(b.dataset.pos).then(refresh);
  };
}
function positionWarning() {
  return S && S.position_mismatch ? `<div class="note warn mt"><svg><use href="#i-warn"/></svg><div>Your saved calibration was made with the tablet lying a different way. Calibrate again so the cursor follows the pen.</div></div>` : "";
}

const TARGET_NAMES = ["top left", "top middle", "top right", "middle left", "centre", "middle right",
                      "bottom left", "bottom middle", "bottom right"];
const GRID = [[.1, .1], [.5, .1], [.9, .1], [.1, .5], [.5, .5], [.9, .5], [.1, .9], [.5, .9], [.9, .9]];
function targetsFor(mode) {
  if (mode === "sheet4") return [1, 3, 9, 7].map(n => ({ n, uv: GRID[n - 1] }));
  if (mode === "corners") return [[0, 0], [1, 0], [1, 1], [0, 1]].map((uv, i) => ({ n: i + 1, uv }));
  return GRID.map((uv, i) => ({ n: i + 1, uv }));
}

for (const b of $$("#calModes .mode")) {
  b.onclick = () => {
    if (S && S.calibrating) return;
    calMode = b.dataset.mode;
    for (const x of $$("#calModes .mode")) x.classList.toggle("on", x === b);
    renderCalibration();
  };
}
$("#calStart").onclick = () => {
  if (!S.connected) { toast("Connect the tablet first"); return; }
  if (!S.position) { toast("First pick how your tablet is lying"); return; }
  Cal.seen = null;  // the result of this run shows when it's done
  api().start_calibration(calMode);
};

// ------------------------------------------------------------------ calibration window

// Calibrating, one step at a time, over the rest of the app: opened from Calibrate, from Home's
// banner, and as a step of the setup guide. The steps' contents are rendered by renderCalibration.
const Cal = {
  isOpen: false,
  step: 0,
  seen: null,  // the result already there when the window opened, so it isn't shown as new
  steps: ["Desk position", "Targets", "Tap the targets"],
  open(step = 0) {
    this.isOpen = true;
    this.step = step;
    this.seen = S ? JSON.stringify(S.cal_result) : null;
    $("#calwin").classList.add("on");
    if (S) renderCalibration();
    this.render();
  },
  close() {
    if (S && S.calibrating) api().cancel_calibration();
    this.isOpen = false;
    $("#calwin").classList.remove("on");
    if (Wizard.isOpen) { Wizard._rendered = null; Wizard.render(); }
  },
  canNext() {
    if (!S) return false;
    if (this.step === 0) return !!S.position;
    if (this.step === 2) return !S.calibrating;
    return true;
  },
  render() {
    if (!this.isOpen) return;
    $("#cwSide").innerHTML = `<div class="lbl">Calibration</div>` + this.steps.map((t, i) =>
      `<div class="wz-step ${i === this.step ? "on" : i < this.step ? "done" : ""}"><span class="n">${i < this.step ? "✓" : i + 1}</span>${t}</div>`).join("");
    for (const el of $$("#calwin .cw-step")) el.hidden = +el.dataset.step !== this.step;
    const last = this.step === this.steps.length - 1, c = S && S.calibrating;
    const done = S && S.cal_result && S.cal_result.ok && JSON.stringify(S.cal_result) !== this.seen;
    $("#cwBack").style.visibility = this.step && !c ? "visible" : "hidden";
    $("#cwNext").textContent = !last ? "Continue" : done ? "Done" : "Close";
    $("#cwNext").classList.toggle("acc", !last || !!done);  // Start calibration is the main button until then
    $("#cwNext").disabled = !this.canNext();
    $("#cwClose").style.display = last ? "none" : "";
  },
};
$("#cwNext").onclick = () => {
  if (!Cal.canNext()) return;
  if (Cal.step === Cal.steps.length - 1) { Cal.close(); return; }
  Cal.step++;
  Cal.render();
};
$("#cwBack").onclick = () => { if (Cal.step && !(S && S.calibrating)) { Cal.step--; Cal.render(); } };
$("#cwClose").onclick = () => Cal.close();
$("#calCancel").onclick = () => api().cancel_calibration();

function renderCalibration() {
  if (!S) return;
  const c = S.calibrating, mode = c ? c.mode : calMode;
  const tg = targetsFor(mode), done = c ? c.index : -1;
  renderPositions($("#calPositions"));
  $("#posWarn").innerHTML = positionWarning();
  const tall = S.position === "portrait" || S.position === "portrait-flipped";
  const svg = $("#calSvg"), W = tall ? 190 : 340, H = tall ? 254 : 255, x0 = (400 - W) / 2, y0 = 22;
  let out = `<rect x="${x0 - 12}" y="${y0 - 12}" width="${W + 24}" height="${H + 24}" rx="12" fill="#0f1d2c" style="stroke:var(--accent-b)"/>` +
            `<rect x="${x0}" y="${y0}" width="${W}" height="${H}" fill="#e9e6dc"/>`;
  tg.forEach((t, i) => {
    const x = x0 + t.uv[0] * W, y = y0 + t.uv[1] * H;
    const state = !c ? "idle" : i < done ? "done" : i === done ? "next" : "todo";
    const col = state === "done" ? "#2f9e6a" : state === "next" ? "#c0392b" : "#2a3a4c";
    if (state === "next") out += `<circle cx="${x}" cy="${y}" r="17" fill="none" stroke="${col}" stroke-width="2.5"><animate attributeName="r" values="14;19;14" dur="1.2s" repeatCount="indefinite"/></circle>`;
    if (state === "done") out += `<circle cx="${x}" cy="${y}" r="9" fill="${col}"/>`;
    else out += `<path d="M${x - 11} ${y}h22M${x} ${y - 11}v22" stroke="${col}" stroke-width="1.6"/><rect x="${x - 4.5}" y="${y - 4.5}" width="9" height="9" fill="none" stroke="${col}"/>`;
    const tx = t.uv[0] > .7 ? x - 20 : x + 10, ty = t.uv[1] > .7 ? y - 12 : y + 22;
    out += `<text x="${tx}" y="${ty}" font-family="IBM Plex Mono" font-size="12" fill="${col}">${t.n}</text>`;
  });
  svg.innerHTML = out;
  $("#calPrompt").textContent = c ? c.prompt : (S.connected ? "Ready when you are." : "Connect the tablet to calibrate.");
  $("#calBar").style.width = c ? (100 * c.index / c.total) + "%" : "0";
  $("#calStart").style.display = c ? "none" : "";
  $("#calStart").disabled = !S.connected;
  $("#calCancel").style.display = c ? "" : "none";
  const r = S.cal_result, fresh = r && JSON.stringify(r) !== Cal.seen;  // not one from an earlier session
  $("#calResult").innerHTML = fresh && !c ? `<div class="note ${r.ok ? "ok" : "warn"}"><svg><use href="#i-${r.ok ? "check" : "warn"}"/></svg><div>${esc(r.text)}</div></div>` : "";
  const k = S.calibration;
  $("#calOpen").textContent = k ? "Calibrate again" : "Calibrate";
  $("#calPageWarn").innerHTML = positionWarning();
  if (!k) $("#calSaved").textContent = S.position ? "Not calibrated yet. Until you are, the cursor follows the desk position you picked, using the whole tablet." : "Not calibrated yet. It takes three short steps.";
  else {
    let html = `<span>${k.points}-point · ${k.landscape ? "landscape" : "portrait"} · saved ${esc(k.date || "")}</span>`;
    const res = k.residual_mm || [];
    if (res.length === 9) {
      // a square per target, coloured by how far its tap was from a perfect fit (already corrected for)
      const avg = res.reduce((a, b) => a + b, 0) / res.length;
      html += `<div class="cal-map" aria-label="Accuracy at each target">` + res.map((v, i) =>
        `<i class="${v < 0.8 ? "good" : v < 2 ? "mid" : "bad"}" data-tip="${TARGET_NAMES[i]}: ${v.toFixed(2)} mm"></i>`).join("") + "</div>" +
        `<div class="cal-stats" title="How far each tap was from a perfect fit, already corrected for"><b>avg</b> ${avg.toFixed(2)} mm<br><b>worst</b> ${Math.max(...res).toFixed(2)} mm</div>`;
    }
    $("#calSaved").innerHTML = html;
  }
}

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ setup guide

const Wizard = {
  isOpen: false,
  step: 0,
  backedUp: false,
  devOn: false,
  keyOk: false,
  busy: false,
  msg: null,
  steps: ["Welcome", "Back up", "Developer mode", "Plug in", "Pair this computer", "Calibrate", "Wi-Fi", "Done"],
  wifiBusy: false,
  wifiMsg: null,

  MODELS: [["ferrari", "Paper Pro"], ["rm2", "reMarkable 2"], ["rm1", "reMarkable 1"]],
  // Only the Paper Pro needs developer mode (and the reset and backup it brings); the rM1 / rM2
  // answer on the cable as they are.
  devMode() { return !S || !S.cfg.model || S.cfg.model === "ferrari"; },
  open() { this.isOpen = true; this.step = 0; this.msg = null; $("#wizard").classList.add("on"); this.render(); },
  close() { this.isOpen = false; $("#wizard").classList.remove("on"); },

  canNext() {
    switch (this.step) {
      case 1: return this.backedUp || !this.devMode();
      case 2: return this.devOn || !this.devMode();
      case 3: return S && S.ssh;
      case 4: return this.keyOk || (S && S.connected);
      default: return true;
    }
  },

  // re-draw the body only when something it shows has changed, so clicks aren't lost mid-redraw
  sig() {
    const v = !S ? null : this.step === 0 ? S.cfg.model : this.step === 3 ? [S.link, S.ssh]
      : this.step === 5 ? [S.connected, !!S.calibrating, !!S.calibration, S.calibration && S.calibration.date, Cal.isOpen]
      : this.step === 6 ? [S.cfg.wifi, S.cfg.wifi_host, S.connected, this.wifiBusy, this.wifiMsg] : null;
    return JSON.stringify([this.step, v]);
  },

  render() {
    const side = $("#wzSide");
    side.innerHTML = `<div class="lbl">Setup guide</div>` + this.steps.map((t, i) =>
      `<div class="wz-step ${i === this.step ? "on" : i < this.step ? "done" : ""}"><span class="n">${i < this.step ? "✓" : i + 1}</span>${t}</div>`).join("");
    const sig = this.sig();
    if (!this._rendered || this._rendered !== sig) {
      const keep = $("#wzPw") && $("#wzPw").value;
      $("#wzBody").innerHTML = this.body();
      if (keep && $("#wzPw")) $("#wzPw").value = keep;
      this.wire();
      this._rendered = sig;
    }
    $("#wzBack").style.visibility = this.step ? "visible" : "hidden";
    const last = this.step === this.steps.length - 1;
    $("#wzNext").textContent = last ? "Start drawing" : this.step === 5 ? (S && S.calibration ? "Continue" : "Skip for now") : "Continue";
    $("#wzNext").disabled = !this.canNext();
    $("#wzSkip").style.display = last ? "none" : "";
  },

  body() {
    const S_ = S || { link: false, ssh: false, cfg: {} };
    switch (this.step) {
      case 0: return `
        <div class="lbl">Welcome</div><h1 class="mt">Turn your reMarkable into a drawing tablet</h1>
        <p class="lead">Your reMarkable becomes a pen tablet for Windows, with pressure, tilt and the eraser end, and it shows what you draw on its own screen. This guide takes about 15 minutes, once.</p>
        <div class="illus"><svg viewBox="0 0 300 140" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <rect x="8" y="30" width="120" height="86" rx="7" style="stroke:var(--accent)" stroke-width="1.5"/><rect x="18" y="38" width="100" height="70" style="fill:var(--accent-d)"/>
          <path d="M30 92c14-26 24-26 32-10s18 16 32-14" style="stroke:var(--text)" stroke-width="2"/>
          <path d="M136 73h40" style="stroke:var(--accent2)" stroke-width="1.5" stroke-dasharray="4 4"/>
          <rect x="184" y="20" width="108" height="72" rx="4" style="stroke:var(--accent2)" stroke-width="1.5"/><path d="M226 92v14M212 108h30" style="stroke:var(--accent2)" stroke-width="1.5"/>
          <path d="M200 68c14-26 24-26 32-10s18 16 32-14" style="stroke:var(--accent2)" stroke-width="2"/></svg></div>
        <div class="pane-title mt">Which tablet?</div>
        <div class="seg mt" id="wzModel">${this.MODELS.map(([k, n]) => `<button data-v="${k}" class="${(S_.cfg.model || "ferrari") === k ? "on" : ""}">${n}</button>`).join("")}</div>
        ${this.devMode() ? "" : `<p class="hint mt">Support for the ${this.MODELS.find(m => m[0] === S_.cfg.model)[1]} is new and not yet tested on one. If something is off, the pen's direction or the tablet screen, please report it.</p>`}
        <div class="pane-title mt">You'll need</div>
        <ol class="steps"><li><div>The tablet, charged, and its USB cable</div></li>${this.devMode() ? "<li><div>Your reMarkable account, for the backup</div></li>" : ""}<li><div>About ${this.devMode() ? 15 : 5} minutes</div></li></ol>`;
      case 1: return `
        <div class="lbl">Step 1 · Back up</div><h1 class="mt">Make sure your notebooks are in the cloud</h1>
        <p class="lead">Turning on developer mode resets the tablet and <b>erases everything on it</b>. Your notebooks come back from the reMarkable cloud when you sign in again.</p>
        <ol class="steps">
          <li><div>On the tablet, connect to Wi-Fi and check it's signed in to your reMarkable account with sync on.</div></li>
          <li><div>Wait for it to finish syncing.</div></li>
          <li><div>Open the <b>reMarkable app</b> on your phone or computer and check your notebooks and files are all there.</div></li>
        </ol>
        <div class="note mt"><svg><use href="#i-info"/></svg><div><b>No Connect subscription?</b> Without one, the cloud only keeps files you've used in the last 50 days (<a onclick="api().open_url('https://support.remarkable.com/articles/Knowledge/Using-reMarkable-without-a-subscription')">reMarkable's page</a>). Before resetting, either open each older notebook once so it syncs, or start the free Connect trial if your account still has it. As a last resort, send notebooks to Google Drive or download them through the reMarkable app. Those copies come back as PDFs, not editable notebooks.</div></div>
        <label class="check"><input type="checkbox" id="wzBackup" ${this.backedUp ? "checked" : ""}><div>My notebooks show up in the reMarkable app. I understand the tablet will be erased and restored from the cloud.</div></label>`;
      case 2: return `
        <div class="lbl">Step 2 · Developer mode</div><h1 class="mt">Turn on developer mode</h1>
        <p class="lead">This is reMarkable's official switch that lets a computer talk to the tablet over the cable. On the tablet:</p>
        <ol class="steps">
          <li><div>Open <b>Settings</b>, then go to<div class="path">Software › Advanced › Developer mode</div></div></li>
          <li><div>Confirm. The tablet resets itself and restarts.</div></li>
          <li><div>Go through the tablet's setup again and sign in to your account. Your notebooks download from the cloud.</div></li>
        </ol>
        <div class="note warn mt"><svg><use href="#i-warn"/></svg><div>From now on the tablet shows a developer-mode warning at every start. That's normal. reMarkable notes that developer mode weakens the tablet's security and isn't covered by the warranty; turning it off later needs their recovery tool. <a onclick="api().open_url('https://developer.remarkable.com/documentation/developer-mode')">Read reMarkable's page</a></div></div>
        <label class="check"><input type="checkbox" id="wzDev" ${this.devOn ? "checked" : ""}><div>Developer mode is on and the tablet is set up again.</div></label>`;
      case 3: return `
        <div class="lbl">Step 3 · Plug in</div><h1 class="mt">Connect the cable</h1>
        <p class="lead">Plug the tablet into this computer with the USB-C cable and unlock it. This page checks for it by itself.</p>
        <div class="live">
          <div class="row ${S_.link ? "ok" : ""}"><span class="dot ${S_.link ? "ok" : "busy"}"></span><div>${S_.link ? "Tablet found on the cable" : "Looking for the tablet…"}</div></div>
          <div class="row ${S_.ssh ? "ok" : ""}"><span class="dot ${S_.ssh ? "ok" : S_.link ? "warn" : ""}"></span><div>${S_.ssh ? "The tablet is answering" : S_.link ? (this.devMode() ? "The tablet is there, but developer mode isn't answering yet" : "The tablet is there, but isn't answering yet") : "Waiting for the tablet"}</div></div>
        </div>
        <div class="pane-title mt2">Not working?</div>
        <ol class="steps">
          <li><div>Wake and unlock the tablet. It disconnects while asleep.</div></li>
          <li><div>Unplug the cable, wait five seconds, plug it back in. Windows sometimes needs a second try.</div></li>
          <li><div>Try another USB port or cable. Some cables only charge.</div></li>
        </ol>`;
      case 4: return `
        <div class="lbl">Step 4 · Pair this computer</div><h1 class="mt">Enter the tablet's password, once</h1>
        <p class="lead">The tablet shows a password for developer access. You type it here one time; the app uses it to set up a private key so it never needs it again. The password itself is not saved.</p>
        <ol class="steps"><li><div>On the tablet, open<div class="path">Settings › General › Help › About › Copyrights and licenses</div><div class="sub">The password is under "General information". It's case-sensitive.</div></div></li></ol>
        <div class="row mt2" style="max-width:520px"><input class="input mono" type="password" id="wzPw" placeholder="tablet password" autocomplete="off" spellcheck="false">
          <button class="btn blue" id="wzPair" ${this.busy ? "disabled" : ""}>${this.busy ? "Pairing…" : "Pair"}</button></div>
        <div class="mt" id="wzMsg">${this.msg ? `<div class="note ${this.msg.ok ? "ok" : "warn"}"><svg><use href="#i-${this.msg.ok ? "check" : "warn"}"/></svg><div>${esc(this.msg.text)}</div></div>` : ""}</div>`;
      case 5: {
        const k = S && S.calibration;
        return `
        <div class="lbl">Step 5 · Calibrate</div><h1 class="mt">Show the app where your pen lands</h1>
        <p class="lead">Three short steps in their own window: how the tablet lies on your desk, which targets to use, then a tap on each target with the pen.</p>
        ${k ? `<div class="note ok mt"><svg><use href="#i-check"/></svg><div>Calibrated: ${k.points} points, tablet ${k.landscape ? "landscape" : "portrait"}.</div></div>` : ""}
        <div class="row mt2"><button class="btn acc lg" id="wzCal" ${!S || !S.connected ? "disabled" : ""}>${S && S.connected ? (k ? "Calibrate again" : "Calibrate") : "Connecting…"}</button></div>
        <p class="hint mt">You can always redo this from Calibrate.</p>`;
      }
      case 6: {
        const on = !!S_.cfg.wifi, m = this.wifiMsg;
        return `
        <div class="lbl">Step 6 · Wi-Fi (optional)</div><h1 class="mt">Use it without the cable too</h1>
        <p class="lead">With the cable plugged in, the app can switch on reMarkable's own SSH over Wi-Fi. From then on it finds the tablet on your network whenever the cable is out. The cable stays the faster link.</p>
        <div class="note warn mt"><svg><use href="#i-warn"/></svg><div>This makes the tablet's login reachable on your network. The app only logs in with its own key and only to this tablet, but the tablet's password works there too, so turn it on only on networks you trust.</div></div>
        <label class="check"><input type="checkbox" id="wzWifi" ${on ? "checked" : ""} ${this.wifiBusy ? "disabled" : ""}><div>${this.wifiBusy ? "Switching Wi-Fi…" : "Connect over Wi-Fi too"}${on && S_.cfg.wifi_host ? ` <span class="muted">(the tablet is at ${esc(S_.cfg.wifi_host)})</span>` : ""}</div></label>
        <div class="mt">${m ? `<div class="note ${m.ok ? "ok" : "warn"}"><svg><use href="#i-${m.ok ? "check" : "warn"}"/></svg><div>${esc(m.text)}</div></div>` : ""}</div>
        <p class="hint mt">Leave it off to use the cable only. You can change this any time in Settings.</p>`;
      }
      default: return `
        <div class="lbl">All set</div><h1 class="mt">That's it. It's plug and play from here.</h1>
        <p class="lead">Keep Paper Tablet running. Whenever you plug in the tablet, it connects, turns on the pen and shows the tablet screen. Unplug it and your notebooks come straight back.</p>
        <div class="pane-title mt2">Where things are</div>
        <ol class="steps">
          <li><div><b>Title bar</b>: two lights for the tablet and the pen, and buttons that turn the pen in Windows, the tablet screen and shapes on and off.</div></li>
          <li><div><b>Home</b>: the tablet screen as it will look, with paper, a tracing image and pen masks.</div></li>
          <li><div><b>Calibrate</b>: calibrate again, and choose the display and the part of the tablet you draw on.</div></li>
          <li><div><b>Pen feel</b> for pressure, and <b>Panel &amp; gestures</b> for the shortcut buttons on the tablet.</div></li>
        </ol>
        <div class="note mt"><svg><use href="#i-info"/></svg><div>Photoshop: turn on <b>Pen Pressure</b> for brush size or opacity. Krita and Clip Studio: choose <b>Windows Ink</b> as the tablet input.</div></div>`;
    }
  },

  wire() {
    for (const x of $$("#wzModel button")) x.onclick = async () => { await setKey("model", x.dataset.v); this._rendered = null; this.render(); };
    const b = $("#wzBackup"); if (b) b.onchange = () => { this.backedUp = b.checked; this.render(); };
    const d = $("#wzDev"); if (d) d.onchange = () => { this.devOn = d.checked; this.render(); };
    const p = $("#wzPair");
    if (p) {
      const go = async () => {
        const pw = $("#wzPw").value;
        if (!pw) return;
        this.busy = true; this.msg = null; this._rendered = null; this.render();
        const r = await api().install_key(pw);
        this.busy = false; this.msg = r; this.keyOk = r.ok; this._rendered = null;
        if (r.ok) api().connect();
        this.render();
      };
      p.onclick = go;
      $("#wzPw").onkeydown = e => { if (e.key === "Enter") go(); };
    }
    const c = $("#wzCal"); if (c) c.onclick = () => Cal.open();
    const w = $("#wzWifi");
    if (w) w.onchange = async () => {
      this.wifiBusy = true; this.wifiMsg = null; this.render();
      const r = await api().set_wifi(w.checked);
      this.wifiBusy = false; this.wifiMsg = r || null;
      await refresh();
      this.render();
    };
  },
};

$("#wzNext").onclick = () => {
  if (!Wizard.canNext()) return;
  if (Wizard.step === 4 && S && !S.connected) api().connect();

  if (Wizard.step === Wizard.steps.length - 1) {
    api().finish_onboarding();
    Wizard.close();
    go("home");
    return;
  }
  Wizard.step++;
  if (!Wizard.devMode() && (Wizard.step === 1 || Wizard.step === 2)) Wizard.step = 3;  // no reset, nothing to back up
  Wizard.render();
  if (Wizard.step === 5 && S && S.connected && !S.calibration) Cal.open();  // straight into calibrating
};
$("#wzBack").onclick = () => {
  if (!Wizard.step) return;
  Wizard.step--;
  if (!Wizard.devMode() && (Wizard.step === 1 || Wizard.step === 2)) Wizard.step = 0;
  Wizard.render();
};
$("#wzSkip").onclick = () => Wizard.close();

// ------------------------------------------------------------------ panel buttons editor

// The tablet panel's foot is taller when it carries the front-light bar (companion_qml.cpp footH).
const lightShown = () => !!(S && S.model && S.model.frontlight);
const foot = () => lightShown() ? 300 : 190;
// the light bar as the tablet draws it: "Light N%" over ten steps, black up to the level
function lightBar(ctx, x, y, w, F) {
  const lv = S.light ?? S.cfg.light_level, pct = lv == null ? -1 : lv;
  ctx.fillStyle = "#000"; ctx.font = `bold 21px ${F}`; ctx.textBaseline = "top";
  ctx.fillText(pct > 0 ? `Light ${pct}%` : pct === 0 ? "Light off" : "Light", x + 6, y - 34);
  ctx.textBaseline = "alphabetic";
  const cw = (w - 36) / 10;
  for (let i = 0; i < 10; i++) {
    const cx = x + i * (cw + 4);
    ctx.fillStyle = (i + 1) * 10 <= pct ? "#000" : "#fff";
    roundRect(ctx, cx + 1.5, y + 1.5, cw - 3, 63, 6); ctx.fill();
    ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.stroke();
  }
}

// The panel mock-up is drawn from the tablet's own numbers (core.PANEL_PX, TAB_PX and the
// companion's header/footer/rows), scaled down, so what you see is what the tablet shows.
const Panel = {
  sel: 0,
  btns: null,       // working copy of the side's buttons (cfg.panel_buttons / panel_buttons_right)
  side: "left",     // which panel the editor shows
  key() { return this.side === "right" ? "panel_buttons_right" : "panel_buttons"; },
  defaults() { return this.side === "right" ? S.default_buttons_right : S.default_buttons; },
  async setSide(side) {
    if (side === this.side) return;
    await this.flush();
    this.side = side; this.btns = null; this.sel = 0; this.sig = "";
    for (const b of $$("#pmSide button")) b.classList.toggle("on", b.dataset.v === side);
    this.render(); this.fill(true);
  },
  sig: "",          // what the mock-up was last drawn from
  recording: false,
  HEAD: 230, ROWS: 8,

  PRESETS: [
    ["Undo", "shortcut", "ctrl+z"], ["Redo", "shortcut", "ctrl+shift+z"], ["Save", "shortcut", "ctrl+s"],
    ["Brush", "shortcut", "b"], ["Eraser", "shortcut", "e"], ["Size -", "shortcut", "["], ["Size +", "shortcut", "]"],
    ["Hand", "hold", "space"], ["Pick", "hold", "alt"], ["Rotate", "hold", "r"], ["Ctrl", "hold", "ctrl"],
    ["Shift", "hold", "shift"], ["Alt", "hold", "alt"], ["Zoom -", "shortcut", "ctrl+-"], ["Zoom +", "shortcut", "ctrl+="],
    ["Fit", "shortcut", "ctrl+0"], ["Move", "shortcut", "v"], ["Lasso", "shortcut", "l"], ["Fill", "shortcut", "g"],
    ["Swap col", "shortcut", "x"], ["Deselect", "shortcut", "ctrl+d"], ["Transform", "shortcut", "ctrl+t"],
    ["New layer", "shortcut", "ctrl+shift+n"], ["Copy", "shortcut", "ctrl+c"], ["Paste", "shortcut", "ctrl+v"],
    ["Pen", "pen", ""], ["Shapes", "snap", ""], ["Clear", "clear", ""], ["Move image", "trace", ""], ["Light", "light", ""], ["Invert", "mask", ""],
  ],

  label(spec) {
    const L = (S && S.key_labels) || {};
    return String(spec || "").split("+").filter(Boolean).map(n => L[n] || n.toUpperCase()).join(" ");
  },
  valid(spec) {
    const names = String(spec || "").toLowerCase().replace(/\s/g, "").split("+").filter(Boolean);
    if (!names.length) return { ok: false, why: "Type a key, or press Record." };
    const bad = names.find(n => !S.key_names.includes(n));
    return bad ? { ok: false, why: `"${bad}" isn't a key this app knows.` } : { ok: true, spec: names.join("+") };
  },
  sub(b) {
    if (b.kind === "shortcut") return this.label(b.keys);
    if (b.kind === "hold") return "holds " + this.label(b.keys);
    if (b.kind === "pen") return S.output ? "on" : "off";
    if (b.kind === "snap") return S.cfg.snap ? "on" : "off";
    if (b.kind === "clear") return "tablet ink";
    if (b.kind === "trace") return S.cfg.trace_adjust ? "moving" : "image";
    if (b.kind === "light") return S.light != null ? S.light + "%" : "";
    if (b.kind === "mask") return S.cfg.mask_invert ? "inverted" : "normal";
    return "";
  },

  render() {
    if (!S || !S.cfg[this.key()]) return;
    const saved = JSON.stringify(S.cfg[this.key()]);
    if (!this.btns || (!this.dirty && saved !== JSON.stringify(this.btns))) this.btns = JSON.parse(saved);
    if (view !== "panel") return;
    this.renderProfiles();
    const sig = JSON.stringify([this.btns, this.sel, S.cfg.panel_profiles, S.cfg.panel_profile, S.view, this.side, S.cfg.panel, S.cfg.panel_open, S.cfg.panel_open_right, S.cfg.gestures, S.output, S.cfg.snap, S.light, S.cfg.trace_adjust, S.cfg.mask_on, S.cfg.mask_invert]);
    if (sig !== this.sig) { this.sig = sig; this.draw(); }
    if (!this.recording && document.activeElement !== $("#peName") && document.activeElement !== $("#peKeys")) this.fill();
    this.renderTaps();
  },

  // ---- tap gestures
  renderTaps() {
    const box = $("#tapList");
    if (!S.taps || box.contains(document.activeElement) || this.recTap) return;
    const sig = JSON.stringify(S.taps);
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig;
    box.innerHTML = Object.entries(S.tap_slots).map(([slot, name]) => {
      const t = S.taps[slot] || { t: "", keys: "" };
      return `<div class="tap" data-slot="${slot}">
        <div class="tap-name">${esc(name)}</div>
        <input class="input" data-f="t" maxlength="20" placeholder="title" value="${esc(t.t)}">
        <input class="input mono" data-f="keys" placeholder="off" value="${esc(t.keys)}">
        <button class="btn blue" data-rec>Record</button></div>`;
    }).join("");
    for (const row of $$(".tap", box)) {
      const slot = row.dataset.slot;
      for (const inp of $$("input", row)) inp.onchange = () => {
        const t = { ...S.taps[slot] };
        if (inp.dataset.f === "keys" && inp.value.trim()) {
          const v = this.valid(inp.value);
          if (!v.ok) { toast(v.why); inp.value = t.keys; return; }
          t.keys = v.spec;
        } else t[inp.dataset.f] = inp.value.trim();
        this.setTap(slot, t);
      };
      $("[data-rec]", row).onclick = e => {
        if (this.recTap === slot) { this.record(false); return; }
        this.recTap = slot;
        this.record(true);
        e.target.textContent = "Press keys…";
        e.target.classList.add("rec");
      };
    }
  },
  setTap(slot, t) {
    S.taps = { ...S.taps, [slot]: t };
    $("#tapList").dataset.sig = "";
    setKey("taps", S.taps).then(refresh);
  },

  // ---- profiles: named button sets, cycled with the arrows on the tablet
  profRow(px, W, T) {
    const profs = S.cfg.panel_profiles || [], many = profs.length > 1;
    const name = (profs[S.cfg.panel_profile] || {}).name || "";
    const tri = (left, flip) => `<svg viewBox="0 0 22 30" style="${left ? "left" : "right"}:${px(16)};width:${px(22)};height:${px(30)}">
      <path d="${flip ? "M22 0L0 15L22 30Z" : "M0 0L22 15L0 30Z"}" fill="#000"/></svg>`;
    return `<div class="pm-prof" title="Tap the arrows on the tablet to switch profile"
      style="left:${px(16)};top:${px(T + 10)};width:${px(W - 32)};height:${px(72)};border-radius:${px(14)};border-width:${px(3)};
      font-size:${px(26)};padding:0 ${px(many ? 56 : 12)}">${many ? tri(true, true) : ""}<span>${esc(name)}</span>${many ? tri(false, false) : ""}</div>`;
  },
  renderProfiles() {
    const profs = S.cfg.panel_profiles || [], cur = S.cfg.panel_profile || 0;
    const sel = $("#profSel"), sig = JSON.stringify([profs.map(p => p.name), cur]);
    if (sel.dataset.sig !== sig && document.activeElement !== sel) {
      sel.dataset.sig = sig;
      sel.innerHTML = profs.map((p, i) => `<option value="${i}"${i === cur ? " selected" : ""}>${esc(p.name)}</option>`).join("");
    }
    if (document.activeElement !== $("#profName")) $("#profName").value = (profs[cur] || {}).name || "";
    $("#profDel").disabled = profs.length < 2;
    const tm = (profs[cur] || {}).touch || "shortcuts";
    for (const b of $$("#profTouch button")) b.classList.toggle("on", b.dataset.v === tm);
  },
  async flush() {  // a pending button edit belongs to the profile it was made in
    if (!this.t) return;
    clearTimeout(this.t); this.t = null;
    if (this.dirty) { await setKey(this.key(), this.btns); this.dirty = false; }
  },
  async setProfiles(profs, cur) {
    await this.flush();
    await setKey("panel_profiles", profs);
    if (cur !== undefined) await setKey("panel_profile", cur);
    this.btns = null; this.sel = 0;
    await refresh();
  },
  async switchProfile(i) {
    await this.flush();
    await setKey("panel_profile", i);
    this.btns = null; this.sel = 0;
    await refresh();
  },
  initProfiles() {
    const copy = o => JSON.parse(JSON.stringify(o));
    const profs = () => copy(S.cfg.panel_profiles || []);
    $("#profSel").onchange = e => this.switchProfile(+e.target.value);
    for (const b of $$("#profTouch button")) b.onclick = () => {
      const p = profs();
      p[S.cfg.panel_profile].touch = b.dataset.v;
      this.setProfiles(p);
    };
    $("#profName").onchange = e => {
      const p = profs(), name = e.target.value.trim();
      if (!name) { e.target.value = p[S.cfg.panel_profile].name; return; }
      p[S.cfg.panel_profile].name = name;
      this.setProfiles(p);
    };
    const add = (name, buttons, right) => {
      const p = profs();
      p.push({ name, buttons: copy(buttons), buttons_right: copy(right) });
      this.setProfiles(p, p.length - 1).then(() => { $("#profName").focus(); $("#profName").select(); });
    };
    $("#profNew").onclick = async () => { await this.flush(); add(`Profile ${S.cfg.panel_profiles.length + 1}`, S.default_buttons, S.default_buttons_right); };
    $("#profDup").onclick = async () => {
      await this.flush();
      add(S.cfg.panel_profiles[S.cfg.panel_profile].name + " copy", S.cfg.panel_buttons, S.cfg.panel_buttons_right);
    };
    let armed = 0;
    $("#profDel").onclick = () => {  // two clicks, so a stray one can't lose a layout
      const b = $("#profDel");
      if (Date.now() - armed > 3000) { armed = Date.now(); b.textContent = "Sure?"; setTimeout(() => b.textContent = "Delete", 3000); return; }
      armed = 0; b.textContent = "Delete";
      const p = profs(), cur = S.cfg.panel_profile;
      const gone = p.splice(cur, 1)[0];
      this.setProfiles(p, Math.max(0, cur - 1)).then(() => toast(`Deleted "${gone.name}".`));
    };
  },

  lightRow(px, W, vh) {  // the front-light bar at the foot of the panel, as the tablet draws it
    const lv = S.light ?? S.cfg.light_level, pct = lv == null ? -1 : lv, cw = (W - 32 - 36) / 10;
    let cells = "";
    for (let i = 0; i < 10; i++) cells += `<i style="left:${px(16 + i * (cw + 4))};top:${px(vh - 92)};width:${px(cw)};height:${px(66)};border-width:${px(3)};border-radius:${px(6)};background:${(i + 1) * 10 <= pct ? "#000" : "#fff"}"></i>`;
    return `<div class="pm-light" title="Tap or drag along it on the tablet to set the front light">
      <b style="left:${px(22)};top:${px(vh - 126)};font-size:${px(21)}">${pct > 0 ? `Light ${pct}%` : pct === 0 ? "Light off" : "Light"}</b>${cells}</div>`;
  },

  draw() {
    const wrap = $("#pmWrap"), [vw, vh] = S.view, right = this.side === "right";
    const P = S.cfg.panel, here = P === this.side || P === "both", main = this.side === (P === "right" ? "right" : "left");
    const W = S.panel_px, T = S.tab_px, k = Math.min(640 / vh, 380 / (W + 260));
    const px = v => (v * k).toFixed(1) + "px";
    const FOOT = foot(), cw = W / 2, ch = (vh - this.HEAD - FOOT) / this.ROWS;
    const open = (right ? S.cfg.panel_open_right : S.cfg.panel_open) !== false;
    // like the tablet's Text.HorizontalFit: shrink long text to the button, down to a minimum
    const fit = (t, size, min, em) => Math.max(min, Math.min(size, (cw - 28) / (Math.max(1, String(t).length) * em)));
    let cells = "";
    this.btns.forEach((b, i) => {
      const x = (i % 2) * cw + 8, y = this.HEAD + Math.floor(i / 2) * ch + 8;
      const none = b.kind === "none";
      cells += `<div class="pm-cell${none ? " none" : ""}${i === this.sel ? " sel" : ""}" draggable="true" data-i="${i}"
        style="left:${px(x)};top:${px(y)};width:${px(cw - 16)};height:${px(ch - 16)};border-radius:${px(14)};border-width:${px(3)}">
        ${none ? `<span class="pm-empty" style="font-size:${px(18)}">empty</span>` :
        `<b style="font-size:${px(fit(b.t, 28, 16, 0.62))}">${esc(b.t)}</b><span style="font-size:${px(fit(this.sub(b), 18, 12, 0.56))}">${esc(this.sub(b))}</span>`}</div>`;
    });
    const chev = `<svg viewBox="0 0 30 36" style="width:${px(30)};height:${px(36)};transform:rotate(${(right === open) ? 180 : 0}deg)">
      <path d="M28 0L2 18L28 36Z" fill="#000"/></svg>`;
    const panel = `<div class="pm-panel" style="width:${px(W)};height:${px(vh)};${right ? "right:0" : "left:0"}">
        <div class="pm-edge" style="${right ? "left:0" : "right:0"};width:${px(3)}"></div>
        <div class="pm-head" style="left:${px(right ? 22 : T + 14)};top:${px(24)};font-size:${px(22)};letter-spacing:${px(4)}">PAPER<br>TABLET</div>
        ${this.profRow(px, W, T)}
        ${cells}
        ${main && S.cfg.gestures !== false ? `<div class="pm-gbtn" title="On the tablet this opens a list of the finger gestures; see Tap gestures here"
          style="left:${px(16)};top:${px(vh - FOOT + 24)};width:${px(W - 32)};height:${px(72)};border-radius:${px(14)};border-width:${px(3)};font-size:${px(24)}">Finger gestures</div>` : ""}
        ${main ? `<div class="pm-foot" style="left:${px(22)};top:${px(vh - FOOT + (S.cfg.gestures !== false ? 116 : 24))};width:${px(W - 44)};font-size:${px(19)}">Power button: back to notebooks</div>` : ""}
        ${main && lightShown() ? this.lightRow(px, W, vh) : ""}
      </div>`;
    const tab = `<div class="pm-tab" title="Folds the panel away" style="${right ? "right:0" : "left:0"};width:${px(T)};height:${px(T)};border-width:${px(3)}">${chev}</div>`;
    const sw = W + 260;
    wrap.innerHTML = `<div class="pm-bezel"><div class="pm-screen${right ? " right" : ""}" style="width:${px(sw)};height:${px(vh)}">
        ${open && here ? panel : ""}${here ? tab : ""}
        <div class="pm-ink" style="${right ? "right" : "left"}:${px(open ? W + 60 : 120)};top:${px(vh * 0.32)}"></div>
      </div></div>`;
    $("#pmNote").textContent = !here ? `The ${this.side} panel is switched off (Panels: ${P === "off" ? "Off" : P === "left" ? "Left" : "Right"}). You can still set its buttons.` :
      open ? `${vw} × ${vh} tablet pixels; the panel is ${W} wide.` : "Folded: only the tab shows. Unfold to edit, or tap the tab on the tablet.";
    for (const c of $$(".pm-cell", wrap)) {
      c.onclick = () => { this.sel = +c.dataset.i; this.render(); this.fill(true); };
      c.ondragstart = e => { e.dataTransfer.setData("text/plain", c.dataset.i); c.classList.add("drag"); };
      c.ondragend = () => c.classList.remove("drag");
      c.ondragover = e => { e.preventDefault(); c.classList.add("over"); };
      c.ondragleave = () => c.classList.remove("over");
      c.ondrop = e => {
        e.preventDefault();
        const a = +e.dataTransfer.getData("text/plain"), b = +c.dataset.i;
        if (a === b || isNaN(a)) return;
        [this.btns[a], this.btns[b]] = [this.btns[b], this.btns[a]];
        this.sel = b;
        this.save();
      };
    }
    const t = $(".pm-tab", wrap);
    if (t) t.onclick = () => setKey(right ? "panel_open_right" : "panel_open", !open).then(refresh);
  },

  fill(force) {
    const b = this.btns[this.sel];
    $("#peTitle").textContent = `Button ${this.sel + 1} · row ${Math.floor(this.sel / 2) + 1}, ${this.sel % 2 ? "right" : "left"}`;
    $("#peName").value = b.t;
    for (const x of $$("#peKinds button")) x.classList.toggle("on", x.dataset.k === b.kind);
    const keyed = b.kind === "shortcut" || b.kind === "hold";
    $("#peKeysRow").style.display = keyed ? "" : "none";
    if (force || document.activeElement !== $("#peKeys")) $("#peKeys").value = b.keys;
    if (keyed && !this.recording) this.hint(b.keys ? "Sends " + this.label(b.keys) + (b.kind === "hold" ? ", held down" : "") : "No key yet.");
    $("#pePreset").value = "";
  },
  hint(t, bad) { const h = $("#peKeysHint"); h.textContent = t; h.classList.toggle("bad", !!bad); },

  save() {
    this.dirty = true;
    this.sig = "";
    this.render();
    clearTimeout(this.t);
    this.t = setTimeout(async () => {
      await setKey(this.key(), this.btns);
      this.dirty = false;
      refresh();
    }, 250);
  },

  edit(patch) { Object.assign(this.btns[this.sel], patch); this.save(); this.fill(true); },

  // KeyboardEvent.code (layout independent) -> the key names core/keys.py knows
  codeName(code) {
    let m;
    if ((m = /^Key([A-Z])$/.exec(code))) return m[1].toLowerCase();
    if ((m = /^(?:Digit|Numpad)([0-9])$/.exec(code))) return m[1];
    if ((m = /^F([0-9]{1,2})$/.exec(code))) return "f" + m[1];
    return {
      Space: "space", Enter: "enter", NumpadEnter: "enter", Escape: "esc", Backspace: "backspace", Tab: "tab",
      Delete: "delete", Insert: "insert", Home: "home", End: "end", PageUp: "pageup", PageDown: "pagedown",
      ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down",
      BracketLeft: "[", BracketRight: "]", Backslash: "\\", Semicolon: ";", Quote: "'", Comma: ",", Period: ".",
      Slash: "/", Backquote: "`", Minus: "-", Equal: "=", NumpadSubtract: "-",
      ControlLeft: "ctrl", ControlRight: "ctrl", ShiftLeft: "shift", ShiftRight: "shift",
      AltLeft: "alt", AltRight: "alt", MetaLeft: "win", MetaRight: "win",
    }[code] || null;
  },

  record(on) {
    this.recording = on;
    this.mods = [];
    $("#peRecord").textContent = on ? "Cancel" : "Record";
    $("#peRecord").classList.toggle("rec", on);
    if (on && !this.recTap) this.hint("Press the keys now, the way you would on the keyboard.");
    if (!on) {
      if (this.recTap) { this.recTap = null; $("#tapList").dataset.sig = ""; this.renderTaps(); }
      this.fill(true);
    }
  },
  onKey(e) {
    if (!this.recording) return;
    e.preventDefault();
    e.stopPropagation();
    const n = this.codeName(e.code);
    if (e.type === "keydown") {
      if (!n) { this.hint(`That key (${e.code}) can't be sent yet.`, true); if (this.recTap) toast(`That key (${e.code}) can't be sent yet.`); return; }
      if (["ctrl", "shift", "alt", "win"].includes(n)) {
        if (!this.mods.includes(n)) this.mods.push(n);
        if (!this.recTap) this.hint(this.label(this.mods.join("+")) + " +");
        return;
      }
      this.finish([...this.order(this.mods), n].join("+"));
    } else if (e.type === "keyup" && n && this.mods.includes(n)) {
      this.finish(this.order(this.mods).join("+"));  // modifiers alone, e.g. a "hold Ctrl" button
    }
  },
  order(mods) { return ["ctrl", "shift", "alt", "win"].filter(m => mods.includes(m)); },
  finish(spec) {
    if (this.recTap) {
      const slot = this.recTap, t = { ...S.taps[slot], keys: spec };
      if (!t.t) t.t = this.label(spec);
      this.record(false);
      this.setTap(slot, t);
      return;
    }
    this.recording = false;
    const b = this.btns[this.sel];
    const patch = { keys: spec };
    if (b.kind !== "shortcut" && b.kind !== "hold") patch.kind = "shortcut";
    if (!b.t) patch.t = this.label(spec).slice(0, 14);
    this.record(false);
    this.edit(patch);
  },

  init() {
    $("#pePreset").innerHTML = `<option value="">Choose…</option>` +
      this.PRESETS.map((p, i) => `<option value="${i}">${p[0]}${p[2] ? "  ·  " + (p[1] === "hold" ? "hold " : "") + p[2] : ""}</option>`).join("");
    $("#pePreset").onchange = e => {
      const p = this.PRESETS[+e.target.value];
      if (p) this.edit({ t: p[0], kind: p[1], keys: p[2] });
    };
    $("#peName").oninput = e => { this.btns[this.sel].t = e.target.value.slice(0, 14); this.save(); };
    $("#peKeys").onchange = e => {
      const v = this.valid(e.target.value);
      if (!v.ok) { this.hint(v.why, true); return; }
      this.edit({ keys: v.spec });
    };
    for (const x of $$("#peKinds button")) x.onclick = () => {
      const k = x.dataset.k, b = this.btns[this.sel];
      const patch = { kind: k };
      if ((k === "shortcut" || k === "hold") && !b.keys) { this.btns[this.sel].kind = k; this.fill(true); this.record(true); return; }
      if (k === "pen" && !b.t) patch.t = "Pen";
      if (k === "snap" && !b.t) patch.t = "Shapes";
      if (k === "clear" && !b.t) patch.t = "Clear";
      if (k === "trace" && !b.t) patch.t = "Image";
      if (k === "light" && !b.t) patch.t = "Light";
      if (k === "mask" && !b.t) patch.t = "Invert";
      if (k === "none") patch.t = "";
      this.edit(patch);
    };
    $("#peRecord").onclick = () => this.record(!this.recording);
    window.addEventListener("keydown", e => this.onKey(e), true);
    window.addEventListener("keyup", e => this.onKey(e), true);
    this.initProfiles();
    for (const b of $$("#pmSide button")) b.onclick = () => this.setSide(b.dataset.v);
    $("#pmReset").onclick = () => {
      this.btns = JSON.parse(JSON.stringify(this.defaults()));
      this.save();
      toast(`${this.side === "right" ? "Right" : "Left"} panel buttons back to the defaults.`);
    };
  },
};
Panel.init();

// ------------------------------------------------------------------ the Tablet screen picture

// What the app tells the tablet screen to show, drawn from the same settings with the same
// numbers as companion_qml.cpp (view pixels as the tablet sits), scaled to fit: paper, tracing
// image, mask, active area, mask strip and side panel. It doesn't copy your strokes, and it shows
// the page as set even with no tablet connected. Drag the tracing image or the mask to place it.
const Scr = {
  HEAD: 230, ROWS: 8, PROF_Y: 94, PROF_H: 72,
  img: null, imgKey: null, thumbs: {}, thumbSig: null,
  drag: "trace", live: null,  // live = {x, y, s}: the tracing image while it is dragged or zoomed
  sheet: false,               // showing the gestures sheet (click Finger gestures in the picture)
  F: '"Noto Sans", "Segoe UI", system-ui, sans-serif',

  layout() {  // as companion_qml.cpp: a panel on either side or both, the strip inside the one on its side
    const [VW, VH] = S.view, c = S.cfg, cal = !!S.calibrating, P = S.panel_px, T = S.tab_px;
    const onL = !cal && (c.panel === "left" || c.panel === "both"), onR = !cal && (c.panel === "right" || c.panel === "both");
    const openL = c.panel_open !== false, openR = c.panel_open_right !== false;
    const wL = onL && openL ? P : 0, wR = onR && openR ? P : 0;
    const stripSide = c.mask_side === "left" ? "left" : "right";
    const stripAvail = !cal && c.mask_panel !== false && (c.masks || []).length > 0;
    const stripW = stripAvail && c.mask_strip_open ? S.strip_px : 0;
    const stripOff = stripSide === "left" ? (onL ? (openL ? P : T) : 0) : (onR ? (openR ? P : T) : 0);
    const resL = stripSide === "left" && stripW ? stripOff + stripW : wL;
    const resR = stripSide === "right" && stripW ? stripOff + stripW : wR;
    return { VW, VH, T, P, cal, onL, onR, openL, openR, wL, wR, panelOn: onL || onR,
             main: onL ? "left" : onR ? "right" : "", stripSide, stripAvail, stripW, stripOff,
             stripX: stripSide === "right" ? VW - stripOff - stripW : stripOff,
             stripTabX: stripSide === "right" ? VW - stripOff - T : stripOff,
             resL, resR, paperX: resL, paperW: VW - resL - resR };
  },
  area(L) {  // core.effective_area: the panels and the open strip keep the pen out
    const a = [...S.cfg.area];
    a[0] = Math.max(a[0], L.resL / L.VW); a[2] = Math.min(a[2], 1 - L.resR / L.VW);
    return a[2] - a[0] < 0.1 ? [...S.cfg.area] : a;
  },
  trace() {
    const c = S.cfg;
    return this.live || { x: +c.trace_x || 0, y: +c.trace_y || 0, s: +c.trace_scale || 1 };
  },
  // the images: fetched again only when the file or a mask changes
  load() {
    if (S.trace_key !== this.imgKey) {
      const key = this.imgKey = S.trace_key;
      this.img = null;
      if (key) api().trace_image().then(url => {
        if (!url || this.imgKey !== key) return;
        const im = new Image();
        im.onload = () => { if (this.imgKey === key) { this.img = im; this.draw(); } };
        im.src = url;
      });
    }
    const sig = JSON.stringify(S.mask_vers || {});
    if (sig !== this.thumbSig && (S.cfg.masks || []).length) {
      this.thumbSig = sig;
      api().mask_thumbs().then(m => {
        for (const [id, url] of Object.entries(m || {})) {
          const im = new Image();
          im.onload = () => { this.thumbs[id] = im; this.draw(); };
          im.src = url;
        }
      });
    }
  },

  draw() {
    const cv = $("#scrPreview");
    if (!S || !S.view || !cv.offsetParent) return;
    this.load();
    const [ctx, w, h] = fitCanvas(cv), L = this.layout(), pad = 30;
    const k = Math.min((w - 2 * pad) / L.VW, (h - 2 * pad) / L.VH);
    const ox = (w - L.VW * k) / 2, oy = (h - L.VH * k) / 2;
    Object.assign(this, { k, ox, oy, L });
    ctx.clearRect(0, 0, w, h);
    roundRect(ctx, ox - 14, oy - 14, L.VW * k + 28, L.VH * k + 28, 14);
    ctx.fillStyle = C.body; ctx.fill(); ctx.strokeStyle = C.frame; ctx.lineWidth = 1; ctx.stroke();
    ctx.save();
    ctx.translate(ox, oy); ctx.scale(k, k);
    ctx.beginPath(); ctx.rect(0, 0, L.VW, L.VH); ctx.clip();
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, L.VW, L.VH);
    if (L.cal) this.targets(ctx, L); else this.page(ctx, L);
    ctx.restore();
    statics[cv.id] = ctx.getImageData(0, 0, cv.width, cv.height);
    if (LIVE.pen || LIVE.touch) drawLive();  // the pen back on top straight away
    this.note(L);
  },
  frame() {  // the screen in canvas pixels, for the live pen
    return this.L ? { x: this.ox, y: this.oy, w: this.L.VW * this.k, h: this.L.VH * this.k } : null;
  },

  page(ctx, L) {
    const c = S.cfg, hair = Math.max(1, 0.8 / this.k);  // 1 px lines stay visible at this scale
    // under the ink: tracing image, paper, mask, only on the drawing area
    ctx.save();
    ctx.beginPath(); ctx.rect(L.paperX, 0, L.paperW, L.VH); ctx.clip();
    ctx.translate(L.paperX, 0);
    if (this.img && c.trace_file) {
      const t = this.trace(), bw = Math.max(1, L.VW * t.s), bh = Math.max(1, L.VH * t.s);
      const bx = (L.paperW - bw) / 2 + t.x, by = (L.VH - bh) / 2 + t.y;
      const f = Math.min(bw / this.img.width, bh / this.img.height), iw = this.img.width * f, ih = this.img.height * f;
      ctx.globalAlpha = +c.trace_opacity; ctx.imageSmoothingEnabled = true;
      ctx.drawImage(this.img, bx + (bw - iw) / 2, by + (bh - ih) / 2, iw, ih);
      ctx.globalAlpha = 1;
    }
    this.paper(ctx, L.paperW, L.VH, hair);
    if (c.mask_on && c.mask_active) {
      const m = maskLayer(0.4);
      if (m) { ctx.imageSmoothingEnabled = true; ctx.drawImage(m, -L.paperX, 0, L.VW, L.VH); }
    }
    if (c.trace_adjust && c.trace_file) {
      ctx.strokeStyle = "#000"; ctx.lineWidth = 6; ctx.strokeRect(9, 9, L.paperW - 18, L.VH - 18);
      ctx.font = `bold 26px ${this.F}`;
      const l1 = "Moving the image: 1 finger drags, 2 fingers zoom", l2 = "Tap here when done";
      const tw = Math.max(ctx.measureText(l1).width, ctx.measureText(l2).width) + 40;
      ctx.fillStyle = "#000"; roundRect(ctx, (L.paperW - tw) / 2, 30, tw, 88, 12); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(l1, L.paperW / 2, 58); ctx.fillText(l2, L.paperW / 2, 90);
      ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
    }
    ctx.restore();
    // the active area's frame
    const a = this.area(L);
    ctx.strokeStyle = "#000"; ctx.lineWidth = Math.max(2, hair);
    ctx.strokeRect(a[0] * L.VW + 1, a[1] * L.VH + 1, (a[2] - a[0]) * L.VW - 2, (a[3] - a[1]) * L.VH - 2);
    if (this.sheet) this.gestureSheet(ctx, L);
    if (L.stripW) this.strip(ctx, L);
    for (const side of ["left", "right"]) {
      if (side === "left" ? L.wL : L.wR) this.panel(ctx, L, side);
    }
    if (L.stripAvail) this.stripTab(ctx, L);
    if (L.onL) this.foldTab(ctx, L, "left");
    if (L.onR) this.foldTab(ctx, L, "right");
    if (!L.panelOn) {
      ctx.fillStyle = "#000"; ctx.font = `26px ${this.F}`; ctx.textBaseline = "top";
      ctx.fillText("Connected to PC    (power button: back to notebooks)", 30 + L.paperX, 20);
      ctx.textBaseline = "alphabetic";
    }
  },

  paper(ctx, W, H, hair) {
    const kind = S.cfg.paper || "plain";
    if (kind === "plain") return;
    const step = Math.max(12, Math.round(+(S.cfg.paper_mm || 5) * S.model.px_per_mm * 10) / 10);
    ctx.fillStyle = "#000";
    if (kind === "dots" || kind === "cross") {
      const cols = Math.floor(W / step) + 1, rows = Math.floor(H / step) + 1, d = Math.max(4, hair * 1.5);
      for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
        const x = i * step, y = j * step;
        if (kind === "dots") ctx.fillRect(x - d / 2, y - d / 2, d, d);
        else { ctx.fillRect(x - 7, y - Math.max(1, hair / 2), 14, Math.max(2, hair)); ctx.fillRect(x - Math.max(1, hair / 2), y - 7, Math.max(2, hair), 14); }
      }
      return;
    }
    const sets = { lined: [[0, step, 2]], squared: [[0, step, 1], [90, step, 1]],
      blueprint: [[0, step, 1], [90, step, 1], [0, step * 5, 3], [90, step * 5, 3]],
      isometric: [[0, step * 0.866, 1], [60, step * 0.866, 1], [120, step * 0.866, 1]] }[kind] || [];
    const diag = Math.ceil(Math.sqrt(W * W + H * H) / step + 2) * step;
    for (const [ang, sp, th] of sets) {
      ctx.save();
      ctx.translate(W / 2, H / 2); ctx.rotate(ang * Math.PI / 180);
      for (let i = 0, n = Math.floor(diag / sp) + 1; i < n; i++) ctx.fillRect(-diag / 2, -diag / 2 + i * sp, diag, Math.max(th, hair));
      ctx.restore();
    }
  },

  // a box with a black border, the companion's Rectangle { radius; border.width: 3 }
  box(ctx, x, y, w, h, r, fill, bw = 3) {
    roundRect(ctx, x + bw / 2, y + bw / 2, w - bw, h - bw, r);
    ctx.fillStyle = fill; ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = bw; ctx.stroke();
  },
  // Text.HorizontalFit: shrink to the width, down to a minimum
  text(ctx, t, x, y, maxW, size, min, bold, col, align = "center") {
    ctx.font = `${bold ? "bold " : ""}${size}px ${this.F}`;
    const tw = ctx.measureText(t).width;
    if (tw > maxW) ctx.font = `${bold ? "bold " : ""}${Math.max(min, size * maxW / tw)}px ${this.F}`;
    ctx.fillStyle = col; ctx.textAlign = align; ctx.textBaseline = "middle";
    ctx.fillText(t, x, y, maxW);
    ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
  },
  // Text with wrapMode: Text.Wrap, centred lines from the top
  wrap(ctx, t, cx, y, maxW, size) {
    ctx.font = `${size}px ${this.F}`; ctx.fillStyle = "#000"; ctx.textAlign = "center"; ctx.textBaseline = "top";
    let line = "";
    for (const word of t.split(" ")) {
      const next = line ? line + " " + word : word;
      if (line && ctx.measureText(next).width > maxW) { ctx.fillText(line, cx, y); y += size * 1.25; line = word; }
      else line = next;
    }
    ctx.fillText(line, cx, y);
    ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
  },
  triangle(ctx, cx, cy, w, h, left) {
    ctx.beginPath();
    if (left) { ctx.moveTo(cx + w / 2, cy - h / 2); ctx.lineTo(cx - w / 2, cy); ctx.lineTo(cx + w / 2, cy + h / 2); }
    else { ctx.moveTo(cx - w / 2, cy - h / 2); ctx.lineTo(cx + w / 2, cy); ctx.lineTo(cx - w / 2, cy + h / 2); }
    ctx.closePath(); ctx.fill();
  },

  panel(ctx, L, side) {
    const c = S.cfg, W = L.P, VH = L.VH, T = L.T, right = side === "right", main = side === L.main;
    ctx.save();
    ctx.translate(right ? L.VW - W : 0, 0);
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, VH);
    ctx.fillStyle = "#000"; ctx.fillRect(right ? 0 : W - 3, 0, 3, VH);
    ctx.font = `bold 22px ${this.F}`; ctx.letterSpacing = "4px"; ctx.textBaseline = "top";
    ctx.fillText("PAPER", right ? 22 : T + 14, 24); ctx.fillText("TABLET", right ? 22 : T + 14, 48);
    ctx.letterSpacing = "0px"; ctx.textBaseline = "alphabetic";
    // profile row: < name >
    const profs = c.panel_profiles || [], many = profs.length > 1, name = (profs[c.panel_profile] || {}).name || "";
    this.box(ctx, 16, this.PROF_Y, W - 32, this.PROF_H, 14, "#fff");
    const px = many ? 56 : 12;
    this.text(ctx, name, W / 2, this.PROF_Y + this.PROF_H / 2, W - 32 - 2 * px, 26, 14, true, "#000");
    if (many) {
      ctx.fillStyle = "#000";
      this.triangle(ctx, 16 + 16 + 11, this.PROF_Y + this.PROF_H / 2, 22, 30, true);
      this.triangle(ctx, W - 16 - 16 - 11, this.PROF_Y + this.PROF_H / 2, 22, 30, false);
    }
    // buttons
    const btns = (right ? c.panel_buttons_right : c.panel_buttons) || [], cw = W / 2, ch = (VH - this.HEAD - foot()) / this.ROWS;
    btns.forEach((b, i) => {
      if (!b || b.kind === "none") return;
      const on = (b.kind === "pen" && S.output) || (b.kind === "snap" && c.snap) || (b.kind === "trace" && c.trace_adjust)
        || (b.kind === "light" && S.light >= 100) || (b.kind === "mask" && c.mask_invert);
      const x = (i % 2) * cw + 8, y = this.HEAD + Math.floor(i / 2) * ch + 8, bw = cw - 16, bh = ch - 16;
      this.box(ctx, x, y, bw, bh, 14, on ? "#000" : "#fff");
      const fg = on ? "#fff" : "#000", sub = Panel.sub(b);
      this.text(ctx, b.t, x + bw / 2, y + bh / 2 - (sub ? 13 : 0), bw - 12, 28, 16, true, fg);
      if (sub) this.text(ctx, sub, x + bw / 2, y + bh / 2 + 17, bw - 12, 18, 12, false, fg);
    });
    // foot of the main panel: the gestures button, how to leave, and the light bar
    if (main) {
      const gy = VH - foot() + 24;
      if (c.gestures !== false) {
        this.box(ctx, 16, gy, W - 32, 72, 14, this.sheet ? "#000" : "#fff");
        this.text(ctx, "Finger gestures", W / 2, gy + 36, W - 52, 24, 14, true, this.sheet ? "#fff" : "#000");
      }
      this.wrap(ctx, "Power button: back to notebooks", W / 2, c.gestures !== false ? gy + 72 + 20 : gy, W - 44, 19);
      if (lightShown()) lightBar(ctx, 16, VH - 92, W - 32, this.F);
    }
    ctx.restore();
  },
  gestureBtn(L) {  // where "Finger gestures" sits, in view pixels
    return { x: (L.main === "right" ? L.VW - L.P : 0) + 16, y: L.VH - foot() + 24, w: L.P - 32, h: 72 };
  },

  gestureSheet(ctx, L) {
    const rows = S.gesture_list || [], areaX = L.paperX, areaW = L.paperW;
    const w = Math.min(areaW - 80, 960), h = 32 + 24 + 18 + rows.length * 62 + 22 + 26 + 32;
    const x = areaX + (areaW - w) / 2, y = Math.max(40, (L.VH - h) / 2);
    this.box(ctx, x, y, w, h, 18, "#fff", 4);
    ctx.fillStyle = "#000"; ctx.font = `bold 24px ${this.F}`; ctx.letterSpacing = "4px"; ctx.textBaseline = "top";
    ctx.fillText("FINGER GESTURES", x + 40, y + 32); ctx.letterSpacing = "0px";
    let ry = y + 32 + 24 + 18;
    for (const [g, act] of rows) {
      ctx.fillStyle = "#000"; ctx.fillRect(x + 40, ry, w - 80, 2);
      ctx.textBaseline = "middle";
      ctx.font = `bold 28px ${this.F}`; ctx.fillText(g, x + 40, ry + 32, (w - 80) * 0.48);
      ctx.font = `28px ${this.F}`; ctx.fillText(act, x + 40 + (w - 80) * 0.5, ry + 32, (w - 80) * 0.5);
      ry += 62;
    }
    ctx.font = `22px ${this.F}`; ctx.textBaseline = "top"; ctx.fillText("Tap anywhere to close", x + 40, ry + 22);
    ctx.textBaseline = "alphabetic";
  },

  strip(ctx, L) {
    const c = S.cfg, W = L.stripW, VW = L.VW, VH = L.VH, T = L.T, right = L.stripSide === "right";
    const thumbH = (W - 24) * VH / VW, tileH = thumbH + 46, invert = !!c.mask_invert, active = c.mask_active || "";
    ctx.save();
    ctx.translate(L.stripX, 0);
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, VH);
    ctx.fillStyle = "#000"; ctx.fillRect(right ? 0 : W - 3, 0, 3, VH);
    ctx.font = `bold 20px ${this.F}`; ctx.letterSpacing = "3px";
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText("MASKS", (right ? 0 : T) + (W - T) / 2, 28);
    ctx.letterSpacing = "0px"; ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
    this.box(ctx, 12, 96, W - 24, 70, 12, invert ? "#000" : "#fff");
    this.text(ctx, "Invert", W / 2, 131, W - 40, 24, 24, true, invert ? "#fff" : "#000");
    this.box(ctx, 12, 176, W - 24, 70, 12, !active ? "#000" : "#fff");
    this.text(ctx, "None", W / 2, 211, W - 40, 24, 24, true, !active ? "#fff" : "#000");
    (c.masks || []).forEach((m, i) => {
      const y = 256 + i * (tileH + 10), on = m.id === active;
      if (y + tileH > VH) return;
      ctx.fillStyle = "#fff"; ctx.fillRect(12, y, W - 24, thumbH);
      const bw = on ? 7 : 2;
      ctx.strokeStyle = "#000"; ctx.lineWidth = bw; ctx.strokeRect(12 + bw / 2, y + bw / 2, W - 24 - bw, thumbH - bw);
      const th = this.thumbs[m.id];
      if (th) ctx.drawImage(th, 16, y + 4, W - 32, thumbH - 8);
      ctx.fillStyle = on ? "#000" : "#fff"; roundRect(ctx, 12, y + thumbH + 4, W - 24, 38, 8); ctx.fill();
      this.text(ctx, m.name, W / 2, y + thumbH + 23, W - 24, 21, 21, true, on ? "#fff" : "#000");
    });
    ctx.restore();
  },
  stripTab(ctx, L) {  // a half-shaded square in the corner away from the panel
    const T = L.T, x = L.stripTabX, open = !!S.cfg.mask_strip_open;
    ctx.fillStyle = open ? "#000" : "#fff"; ctx.fillRect(x, 0, T, T);
    ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.strokeRect(x + 1.5, 1.5, T - 3, T - 3);
    const fg = open ? "#fff" : "#000", q = x + (T - 38) / 2, r = (T - 38) / 2;
    ctx.strokeStyle = fg; ctx.strokeRect(q + 1.5, r + 1.5, 35, 35);
    ctx.fillStyle = fg; ctx.fillRect(q + 3, r + 3, 16, 32);
  },
  foldTab(ctx, L, side) {  // a solid triangle pointing the way the panel will move
    const right = side === "right", T = L.T, x = right ? L.VW - T : 0, open = right ? L.openR : L.openL;
    ctx.fillStyle = "#fff"; ctx.fillRect(x, 0, T, T);
    ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.strokeRect(x + 1.5, 1.5, T - 3, T - 3);
    ctx.fillStyle = "#000";
    this.triangle(ctx, x + T / 2, T / 2, 26, 36, right !== open);
  },

  targets(ctx, L) {  // calibrating: 9 crosshairs, 10% in, the next one ringed
    const next = S.calibrating ? S.calibrating.number : 0;
    ctx.fillStyle = "#000"; ctx.strokeStyle = "#000";
    for (let i = 0; i < 9; i++) {
      const x = [0.1, 0.5, 0.9][i % 3] * L.VW, y = [0.1, 0.5, 0.9][Math.floor(i / 3)] * L.VH, hot = next === i + 1;
      ctx.fillRect(x - 40, y - 1.5, 80, 3); ctx.fillRect(x - 1.5, y - 40, 3, 80);
      ctx.lineWidth = 2; ctx.strokeRect(x - 13, y - 13, 26, 26);
      if (hot) { ctx.lineWidth = 6; ctx.beginPath(); ctx.arc(x, y, 31, 0, Math.PI * 2); ctx.stroke(); }
      ctx.font = `${hot ? "bold " : ""}${hot ? 44 : 30}px ${this.F}`; ctx.textBaseline = "top";
      ctx.fillText(String(i + 1), x + 22, y > L.VH * 0.6 ? y - 60 : y + 22);
    }
    ctx.font = `34px ${this.F}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(next ? `Tap the centre of crosshair ${next}` : "Calibration targets", L.VW / 2, L.VH * 0.7);
    ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
  },

  note(L) {
    $("#scrNote").textContent = (S.screen ? "" : S.connected ? "The tablet screen was closed on the tablet; this is how it will look. " : "No tablet connected; this is how its screen will look. ")
      + "Strokes aren't copied here. Click Finger gestures on the panel to preview that sheet.";
    const t = this.drag === "mask" ? "Mouse wheel zooms the mask" : S.cfg.trace_file ? "Mouse wheel zooms the image" : "No tracing image: choose one under Tracing image below";
    $("#scrHint").textContent = t;
  },

  // view pixels under the mouse
  at(e) {
    const r = $("#scrPreview").getBoundingClientRect();
    return [(e.clientX - r.left - this.ox) / this.k, (e.clientY - r.top - this.oy) / this.k];
  },
  init() {
    const cv = $("#scrPreview");
    let start = null, t0 = null, m0 = null, wheelT = 0;
    const onPaper = ([x, y]) => this.L && x >= this.L.paperX && x < this.L.paperX + this.L.paperW && y >= 0 && y < this.L.VH;
    const onBtn = ([x, y]) => {
      if (!this.L || !this.L.main || !(this.L.main === "left" ? this.L.wL : this.L.wR) || S.cfg.gestures === false) return false;
      const b = this.gestureBtn(this.L);
      return x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h;
    };
    for (const b of $$("#scrDrag button")) b.onclick = () => {
      this.drag = b.dataset.v;
      for (const x of $$("#scrDrag button")) x.classList.toggle("on", x === b);
      this.draw();
    };
    cv.addEventListener("mousemove", e => {
      if (start) return;
      const p = this.at(e);
      cv.style.cursor = onBtn(p) ? "pointer" : onPaper(p) && (this.drag === "mask" ? S.cfg.mask_active : S.cfg.trace_file) ? "move" : "default";
    });
    cv.addEventListener("mousedown", e => {
      const p = this.at(e);
      if (onBtn(p)) { this.sheet = !this.sheet; this.draw(); return; }
      if (this.sheet) { this.sheet = false; this.draw(); return; }  // as on the tablet: a tap closes it
      if (!onPaper(p)) return;
      if (this.drag === "mask") {
        const s = maskSource();
        if (!s || !S.cfg.mask_active) return;
        start = p; m0 = [s.ox, s.oy];
      } else if (S.cfg.trace_file) { start = p; t0 = this.trace(); }
    });
    window.addEventListener("mousemove", e => {
      if (!start) return;
      const p = this.at(e), dx = p[0] - start[0], dy = p[1] - start[1];
      if (this.drag === "mask") {
        const s = maskSource();
        s.ox = m0[0] + dx / this.L.VW; s.oy = m0[1] + dy / this.L.VH;
      } else this.live = { x: t0.x + dx, y: t0.y + dy, s: t0.s };
      this.draw();
    });
    window.addEventListener("mouseup", e => {
      if (!start) return;
      const p = this.at(e), moved = Math.abs(p[0] - start[0]) + Math.abs(p[1] - start[1]) > 2;
      start = null;
      if (!moved) { this.live = null; return; }
      if (this.drag === "mask") maskCommit(); else this.commit();
    });
    cv.addEventListener("wheel", e => {
      if (!onPaper(this.at(e))) return;  // elsewhere the wheel scrolls the page
      const z = Math.pow(1.1, -e.deltaY / 100);
      if (this.drag === "mask") {
        const s = maskSource();
        if (!s || !S.cfg.mask_active) return;
        e.preventDefault();
        s.s = Math.min(20, Math.max(0.05, s.s * z));
        this.draw();
        clearTimeout(wheelT); wheelT = setTimeout(maskCommit, 400);
      } else if (S.cfg.trace_file) {
        e.preventDefault();
        const t = this.trace();
        this.live = { x: t.x, y: t.y, s: Math.min(20, Math.max(0.05, t.s * z)) };
        this.draw();
        clearTimeout(wheelT); wheelT = setTimeout(() => this.commit(), 400);
      }
    }, { passive: false });
  },
  async commit() {  // the tracing image's new place, to the settings and the tablet
    const t = this.live;
    if (!t) return;
    await setKey("trace_x", Math.round(t.x * 10) / 10);
    await setKey("trace_y", Math.round(t.y * 10) / 10);
    await setKey("trace_scale", Math.round(t.s * 10000) / 10000);
    this.live = null;
    refresh();
  },
};
Scr.init();

// ------------------------------------------------------------------ tracing image
async function pickTrace() {
  const path = await api().pick_trace_image();
  if (path) {
    await resetTrace(); await setKey("trace_adjust", true);
    toast("Image sent. Drag it with one finger, pinch to zoom, then tap the black note on the tablet when done.");
  }
  refresh();
}
async function resetTrace() {
  for (const [k, v] of [["trace_x", 0], ["trace_y", 0], ["trace_scale", 1]]) await setKey(k, v);
  refresh();
}

// the mask library: pick, name, add, delete (the tablet's strip switches between the same masks)
function renderMasks() {
  const ms = S.cfg.masks || [], cur = S.cfg.mask_active || "", sel = $("#maskSel");
  const sig = JSON.stringify([ms.map(m => [m.id, m.name]), cur]);
  if (sel.dataset.sig !== sig && document.activeElement !== sel) {
    sel.dataset.sig = sig;
    sel.innerHTML = `<option value=""${cur ? "" : " selected"}>None</option>` +
      ms.map(m => `<option value="${esc(m.id)}"${m.id === cur ? " selected" : ""}>${esc(m.name)}</option>`).join("");
  }
  const m = ms.find(x => x.id === cur);
  if (document.activeElement !== $("#maskName")) $("#maskName").value = m ? m.name : "";
  $("#maskName").disabled = $("#maskDel").disabled = !m;
}
$("#maskSel").onchange = async e => { await api().pick_mask(e.target.value); maskSrc = null; refresh(); };
$("#maskName").onchange = async e => { if (S.cfg.mask_active) await api().rename_mask(S.cfg.mask_active, e.target.value); refresh(); };
{
  let armed = 0;
  $("#maskDel").onclick = async () => {  // two clicks, so a stray one can't lose a mask
    const b = $("#maskDel");
    if (Date.now() - armed > 3000) { armed = Date.now(); b.textContent = "Sure?"; setTimeout(() => b.textContent = "Delete", 3000); return; }
    armed = 0; b.textContent = "Delete";
    const m = (S.cfg.masks || []).find(x => x.id === S.cfg.mask_active);
    await api().delete_mask(S.cfg.mask_active); maskSrc = null; refresh();
    if (m) toast(`Deleted "${m.name}".`);
  };
}

$("#wifiOn").onchange = async e => {
  const r = await api().set_wifi(e.target.checked);
  if (r) toast(r.text);
  if (r && !r.ok) e.target.checked = !e.target.checked;
  refresh();
};
async function maskInvert() {
  await setKey("mask_invert", !S.cfg.mask_invert);
  refresh();
}
async function maskOp(op) {
  await api().mask_op(op);
  maskSrc = null;  // reloads from the PC
  refresh();
}

// An image as the pen mask: greyscale luminance, fitted to fill the tablet screen as it sits (cover).
function maskFromImage(input) {
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  const img = new Image(), url = URL.createObjectURL(file);
  img.onload = async () => {
    URL.revokeObjectURL(url);
    await api().new_mask(file.name.replace(/\.[^.]*$/, "").slice(0, 24) || null);  // each image is a mask of its own
    maskSrc = { el: img, grid: false, s: 1, ox: 0, oy: 0, ver: null };
    await maskCommit(maskSrc);
    toast("Mask added from " + file.name + ". On the tablet picture on Home, drag it into place and zoom it with the wheel.");
    Scr.drag = "mask";
    for (const x of $$("#scrDrag button")) x.classList.toggle("on", x.dataset.v === "mask");
    Scr.draw();
  };
  img.onerror = () => { URL.revokeObjectURL(url); toast("That file isn't an image the app can read."); };
  img.src = url;
}

// Trail: 0.25-5 s, and one step past 5 s is "until cleared" (stored as 0, which the tablet keeps forever)
const TRAIL_MAX = 5250;
const trailToSlider = ms => ms ? Math.min(5000, ms) : TRAIL_MAX;
const trailText = ms => ms ? (ms / 1000).toFixed(ms % 1000 ? 2 : 0) + " s" : "Until cleared";
$("#trailR").addEventListener("input", e => {
  const v = +e.target.value, ms = v >= TRAIL_MAX ? 0 : v;
  paintRange(e.target);
  $("#trailV").textContent = trailText(ms);
  S.cfg.trail_ms = ms;
  clearTimeout(e.target._t);
  e.target._t = setTimeout(() => api().set("trail_ms", ms), 120);
});
