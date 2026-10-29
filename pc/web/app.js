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
    const visible = ["home", "mapping", "pen"].includes(view) && !document.hidden;
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
  if (S.calibrating) return { dot: "busy", lbl: "Calibrating", title: S.calibrating.prompt, text: "Open Calibrate to follow along." };
  if (S.connected) {
    const bits = [];
    bits.push(S.output ? "The pen is drawing in Windows." : "The pen is connected but not sent to Windows yet.");
    if (S.screen) bits.push("Your strokes show on the tablet screen.");
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

function render() {
  const s = status();
  $("#pillDot").className = "dot " + s.dot;
  $("#pillText").textContent = S.connected ? (S.output ? "Pen on" : "Connected") : s.lbl;
  $("#heroDot").className = "dot " + s.dot;
  $("#heroLbl").textContent = s.lbl;
  $("#heroTitle").textContent = s.title;
  $("#heroText").textContent = s.text;
  const acts = $("#heroActions");
  acts.innerHTML = "";
  if (!S.cfg.onboarded) acts.append(button("Open the setup guide", "acc lg", () => Wizard.open()));
  else if (!S.connected && S.ssh) acts.append(button("Connect", "acc", () => api().connect()));
  else if (S.connected && !S.calibration) acts.append(button("Calibrate now", "acc", () => go("calibrate")));
  else if (S.status.kind === "error") acts.append(button("Try again", "", () => api().connect()));

  $("#devName").textContent = S.device ? "reMarkable Paper Pro" : (S.ssh ? "Tablet found" : "Not connected");
  $("#devMeta").textContent = S.device ? `${S.cfg.host} · kernel ${S.device.kernel}` : (S.ssh ? S.cfg.host : "Plug in the USB cable");

  setToggle("#swOutput", "#cardOutput", S.output, S.connected);
  setToggle("#swScreen", "#cardScreen", S.screen, S.connected);
  $("#swScreen2").checked = S.screen;
  $("#swScreen2").disabled = !S.connected;
  $("#scrState").textContent = S.screen ? "On. The notebook app is paused." : (S.connected ? "Off" : "Connect the tablet first");

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
    else if (el.tagName === "SELECT" && k === "companion_rot") fillRotations(el);
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
  $("#traceDel").disabled = !tf;
  renderMasks();
  $("#maskInfo").textContent = S.mask_pct ? `${S.mask_pct}% of the screen is masked.` : "Nothing masked yet.";
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

  const ago = t => Math.max(0, Math.round(Date.now() / 1000 - t));
  const sl = S.snap_last;
  $("#snapLast").textContent = sl ? `Last: ${sl.text} (${ago(sl.at)} s ago).` : "";
  const la = S.last_action;
  $("#lastAction").textContent = (la ? `Last from the tablet: ${la.action} (${ago(la.at)} s ago).` : "") +
    (S.held_keys && S.held_keys.length ? ` Held down now: ${S.held_keys.join(", ")}.` : "");
  const pr = S.pen_rate;
  $("#rateText").innerHTML = pr ? `<span><b>tablet</b> ${pr.tablet} reports/s</span><span><b>reaching this PC</b> ${pr.pc ?? "…"} /s</span>`
                                : "<span>Not measured yet: draw a stroke.</span>";
  const log = $("#scrLog");
  log.textContent = (S.screen_log || []).join("\n") || "Nothing yet.";
  log.scrollTop = log.scrollHeight;

  renderCalibration();
  Panel.render();
  drawStatic();
  drawCurve();
  if (Wizard.isOpen) Wizard.render();
}

const pct = v => Math.round(v * 100) + "%";

function button(text, cls, fn) {
  const b = document.createElement("button");
  b.className = "btn " + cls;
  b.textContent = text;
  b.onclick = fn;
  return b;
}

function setToggle(sw, card, on, enabled) {
  $(sw).checked = on;
  $(sw).disabled = !enabled;
  $(card).classList.toggle("on", on);
  $(card).classList.toggle("disabled", !enabled);
}

function fillMonitors(el) {
  const sig = JSON.stringify(S.monitors);
  if (el.dataset.sig !== sig) {
    el.innerHTML = S.monitors.map(m => `<option value="${m.index}">${m.label}</option>`).join("");
    el.dataset.sig = sig;
  }
  el.value = S.cfg.monitor;
}

function fillRotations(el) {
  if (!el.options.length) el.innerHTML = S.rotations.map(r => `<option>${r}</option>`).join("");
  el.value = S.cfg.companion_rot;
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
    if (el.dataset.key === "mask_contrast") { $("#maskConV").textContent = "×" + v; drawTablet($("#mapPreview")); }
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
$("#swOutput").addEventListener("change", e => api().set_output(e.target.checked));
for (const id of ["#swScreen", "#swScreen2"]) {
  $(id).addEventListener("change", e => {
    api().set_screen(e.target.checked);
    toast(e.target.checked ? "Starting the tablet screen…" : "Giving the screen back to the notebook app…");
  });
}
$("#btnConnect").onclick = () => api().connect();
$("#btnDisconnect").onclick = () => api().disconnect();

function setArea(a) { setKey("area", a.map(v => Math.round(v * 1000) / 1000)).then(refresh); }

// ------------------------------------------------------------------ navigation

function go(v) {
  view = v;
  for (const n of $$(".nav[data-view]")) n.classList.toggle("on", n.dataset.view === v);
  for (const s of $$(".view")) s.classList.toggle("on", s.id === "view-" + v);
  requestAnimationFrame(() => { drawStatic(); drawCurve(); Panel.sig = ""; if (S) Panel.render(); });
}
for (const n of $$(".nav[data-view]")) n.onclick = () => go(n.dataset.view);

// ------------------------------------------------------------------ theme (title bar switcher, web/themes.css)

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
    b.classList.toggle("on", b.dataset.theme === t);
    b.setAttribute("aria-checked", b.dataset.theme === t);
  }
  themeColors();
  requestAnimationFrame(() => { drawStatic(); drawCurve(); Panel.sig = ""; if (S) Panel.render(); });
}
$("#themes").innerHTML = THEMES.map(([k, name, bg, acc]) =>
  `<button role="radio" data-theme="${k}" title="${name}" aria-label="${name} theme" style="--sw-bg:${bg};--sw-acc:${acc}"></button>`).join("");
for (const b of $$("#themes button")) b.onclick = () => {
  themeHold = Date.now() + 1500;
  applyTheme(b.dataset.theme);
  api().set("theme", b.dataset.theme);
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

// ------------------------------------------------------------------ tablet preview canvases

// The tablet's body and paper screen keep their own colours; the rest follows the theme (themeColors).
const C = { bg: "#060e17", body: "#0f1d2c", screen: "#e9e6dc", screenEdge: "#23364a", used: "rgba(74,158,224,0.28)",
            usedEdge: "#4A9EE0", area: "#C4A882", pen: "#4A9EE0", down: "#e5484d", eraser: "#ff9d52", text: "rgba(255,255,255,0.5)",
            grid: "rgba(255,255,255,0.06)", frame: "rgba(196,168,130,0.18)", marker: "#fff" };

function themeColors() {
  const css = getComputedStyle(document.documentElement), v = n => css.getPropertyValue(n).trim();
  Object.assign(C, { bg: v("--bg"), used: v("--accent2-b"), usedEdge: v("--accent2"), area: v("--accent"), pen: v("--accent2"),
                     text: v("--dim2"), grid: v("--border2"), frame: v("--accent-b"), marker: v("--ink") });
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
  if (cv.id === "mapPreview") drawMask(ctx, f);
  const a = cv._drag || S.cfg.area;
  ctx.setLineDash([7, 4]); ctx.strokeStyle = C.area; ctx.lineWidth = 2;
  ctx.strokeRect(f.x + a[0] * f.w, f.y + a[1] * f.h, (a[2] - a[0]) * f.w, (a[3] - a[1]) * f.h);
  ctx.setLineDash([]);
  statics[cv.id] = ctx.getImageData(0, 0, cv.width, cv.height);
}

function drawStatic() {
  drawTablet($("#homePreview"));
  drawTablet($("#mapPreview"));
}

function drawLive() {
  for (const id of ["homePreview", "mapPreview"]) {
    const cv = document.getElementById(id);
    if (!cv.offsetParent || !statics[id]) continue;
    const ctx = cv.getContext("2d");
    ctx.putImageData(statics[id], 0, 0);
    const p = LIVE.pen;
    const d = window.devicePixelRatio || 1, w = cv.width / d, h = cv.height / d;
    const f = frameRect(w, h);
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

// ------------------------------------------------------------------ pen mask on the Screen area picture

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
  maskOverlay = null;
  drawTablet($("#mapPreview"));
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
let maskOverlay = null;  // {key, canvas}: the shaded picture, redrawn only when something changes
function drawMask(ctx, f) {
  const src = maskSource();
  if (!src) return;
  const k = S.cfg.mask_contrast || 1, key = [src.ver, src.s, src.ox, src.oy, k, S.cfg.mask_on, src.el.width].join();
  if (!maskOverlay || maskOverlay.key !== key) {
    const lv = src.grid && src.s === 1 && !src.ox && !src.oy ? src.lv : maskRender(src);
    const c = document.createElement("canvas"); c.width = c.height = MASK_N;
    const g = c.getContext("2d"), d = g.createImageData(MASK_N, MASK_N), a = S.cfg.mask_on ? 0.7 : 0.3;
    for (let i = 0; i < lv.length; i++)
      d.data[4 * i + 3] = Math.round(a * Math.max(0, Math.min(255, Math.floor(128 + (lv[i] - 127.5) * k))));
    g.putImageData(d, 0, 0);
    maskOverlay = { key, canvas: c };
  }
  ctx.drawImage(maskOverlay.canvas, f.x, f.y, f.w, f.h);
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
let dragWhat = "area";
function dragHint() {
  $("#dragHint").textContent = dragWhat === "mask" ? "Mouse wheel zooms the mask " : "";
}
for (const b of $$("#dragWhat button")) b.onclick = () => {
  dragWhat = b.dataset.v;
  for (const x of $$("#dragWhat button")) x.classList.toggle("on", x === b);
  dragHint();
};

// drag on the Screen area picture: the active area, or the pen mask
(() => {
  const cv = $("#mapPreview");
  let start = null, m0 = null, wheelT = 0;
  const frac = (e, clamp = true) => {
    const r = cv.getBoundingClientRect(), f = frameRect(r.width, r.height);
    const p = (e.clientX - r.left - f.x) / f.w, q = (e.clientY - r.top - f.y) / f.h;
    return clamp ? [Math.min(1, Math.max(0, p)), Math.min(1, Math.max(0, q))] : [p, q];
  };
  const box = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  const onScreen = e => { const [p, q] = frac(e, false); return p >= 0 && p <= 1 && q >= 0 && q <= 1; };
  cv.addEventListener("mousemove", e => {
    if (!start) cv.style.cursor = onScreen(e) ? (dragWhat === "mask" ? "move" : "crosshair") : "default";
  });
  cv.addEventListener("mousedown", e => {
    if (!onScreen(e)) return;  // only the screen itself, not the frame or the space beside it
    if (dragWhat === "mask") { const s = maskSource(); if (!s) return; start = frac(e, false); m0 = [s.ox, s.oy]; }
    else start = frac(e);
  });
  window.addEventListener("mousemove", e => {
    if (!start) return;
    if (dragWhat === "mask") {
      const p = frac(e, false), s = maskSource();
      s.ox = m0[0] + p[0] - start[0]; s.oy = m0[1] + p[1] - start[1];
    } else cv._drag = box(start, frac(e));
    drawTablet(cv);
  });
  window.addEventListener("mouseup", e => {
    if (!start) return;
    if (dragWhat === "mask") {
      const p = frac(e, false), moved = Math.abs(p[0] - start[0]) + Math.abs(p[1] - start[1]) > 0.002;
      start = null;
      if (moved) maskCommit();
      return;
    }
    const a = box(start, frac(e));
    start = null; cv._drag = null;
    if (a[2] - a[0] >= 0.05 && a[3] - a[1] >= 0.05) setArea(a); else drawTablet(cv);
  });
  cv.addEventListener("wheel", e => {
    if (dragWhat !== "mask" || !onScreen(e)) return;  // elsewhere the wheel scrolls the page
    e.preventDefault();
    const s = maskSource();
    if (!s) return;
    s.s = Math.min(20, Math.max(0.05, s.s * Math.pow(1.1, -e.deltaY / 100)));
    drawTablet(cv);
    clearTimeout(wheelT);
    wheelT = setTimeout(maskCommit, 400);
  }, { passive: false });
})();
window.addEventListener("resize", () => drawStatic());

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
  api().start_calibration(calMode);
};
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
  const r = S.cal_result;
  $("#calResult").innerHTML = r && !c ? `<div class="note ${r.ok ? "ok" : "warn"}"><svg><use href="#i-${r.ok ? "check" : "warn"}"/></svg><div>${esc(r.text)}</div></div>` : "";
  const k = S.calibration;
  if (!k) $("#calSaved").textContent = S.position ? "None yet. Until you calibrate, the cursor follows the position you picked above, using the whole tablet." : "None yet. Pick how your tablet is lying above, then calibrate.";
  else {
    let html = `${k.points}-point calibration, tablet ${k.landscape ? "landscape" : "portrait"}, saved ${esc(k.date || "")}.`;
    if (k.residual_mm && k.residual_mm.length === 9) {
      html += `<div class="muted mt">How far each tap was from a perfect fit (mm), already corrected for:</div><div class="resid">` +
        k.residual_mm.map(v => `<div class="${v < 0.8 ? "good" : v < 2 ? "mid" : "bad"}">${v.toFixed(2)}</div>`).join("") + "</div>";
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
  steps: ["Welcome", "Back up", "Developer mode", "Plug in", "Pair this computer", "Calibrate", "Done"],

  open() { this.isOpen = true; this.step = 0; this.msg = null; $("#wizard").classList.add("on"); this.render(); },
  close() { this.isOpen = false; $("#wizard").classList.remove("on"); },

  canNext() {
    switch (this.step) {
      case 1: return this.backedUp;
      case 2: return this.devOn;
      case 3: return S && S.ssh;
      case 4: return this.keyOk || (S && S.connected);
      default: return true;
    }
  },

  // re-draw the body only when something it shows has changed, so clicks aren't lost mid-redraw
  sig() {
    const v = !S ? null : this.step === 3 ? [S.link, S.ssh]
      : this.step === 5 ? [S.connected, S.calibrating, S.cal_result, S.position, S.position_mismatch, !!S.calibration] : null;
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
        <div class="lbl">Welcome</div><h1 class="mt">Turn your Paper Pro into a drawing tablet</h1>
        <p class="lead">Your reMarkable becomes a pen tablet for Windows, with pressure, tilt and the eraser end, and it shows what you draw on its own screen. This guide takes about 15 minutes, once.</p>
        <div class="illus"><svg viewBox="0 0 300 140" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <rect x="8" y="30" width="120" height="86" rx="7" style="stroke:var(--accent)" stroke-width="1.5"/><rect x="18" y="38" width="100" height="70" style="fill:var(--accent-d)"/>
          <path d="M30 92c14-26 24-26 32-10s18 16 32-14" style="stroke:var(--text)" stroke-width="2"/>
          <path d="M136 73h40" style="stroke:var(--accent2)" stroke-width="1.5" stroke-dasharray="4 4"/>
          <rect x="184" y="20" width="108" height="72" rx="4" style="stroke:var(--accent2)" stroke-width="1.5"/><path d="M226 92v14M212 108h30" style="stroke:var(--accent2)" stroke-width="1.5"/>
          <path d="M200 68c14-26 24-26 32-10s18 16 32-14" style="stroke:var(--accent2)" stroke-width="2"/></svg></div>
        <div class="pane-title mt">You'll need</div>
        <ol class="steps"><li><div>The tablet, charged, and its USB-C cable</div></li><li><div>Your reMarkable account, for the backup</div></li><li><div>About 15 minutes</div></li></ol>`;
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
          <li><div>Open <b>Settings</b>, then go to<div class="path">General › Paper Tablet › Software › Advanced › Developer mode</div></div></li>
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
          <div class="row ${S_.ssh ? "ok" : ""}"><span class="dot ${S_.ssh ? "ok" : S_.link ? "warn" : ""}"></span><div>${S_.ssh ? "Developer mode is answering" : S_.link ? "The tablet is there, but developer mode isn't answering yet" : "Waiting for the tablet"}</div></div>
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
        const c = S && S.calibrating;
        return `
        <div class="lbl">Step 5 · Calibrate</div><h1 class="mt">Show the app where your pen lands</h1>
        <p class="lead">Put the tablet where it will sit on your desk, the way round you'll use it, then pick the picture that matches. Look at where the power button (blue) and the cable are. Nine targets then appear on the tablet, turned to match; tap the centre of each with the pen, in order.</p>
        <div class="positions mt2" id="wzPositions"></div>${positionWarning()}
        <div class="row mt2"><button class="btn acc lg" id="wzCal" ${!S || !S.connected || c || !S.position ? "disabled" : ""}>${c ? "Calibrating…" : S && S.connected ? (S.calibration ? "Calibrate again" : "Start calibration") : "Connecting…"}</button></div>
        <div class="big mt" style="font-size:20px">${c ? esc(c.prompt) : S && S.cal_result ? esc(S.cal_result.text) : ""}</div>
        ${c ? `<div class="progress"><i style="width:${100 * c.index / c.total}%"></i></div>` : ""}
        <p class="hint mt">Without the tablet screen you can open the printable target sheet on the tablet instead. You can always redo this from Calibrate.</p>`;
      }
      default: return `
        <div class="lbl">All set</div><h1 class="mt">That's it. It's plug and play from here.</h1>
        <p class="lead">Keep Paper Tablet running. Whenever you plug in the tablet, it connects, turns on the pen and shows the tablet screen. Unplug it and your notebooks come straight back.</p>
        <ol class="steps">
          <li><div>In Photoshop, turn on <b>Pen Pressure</b> for brush size or opacity.</div></li>
          <li><div>In Krita or Clip Studio, choose <b>Windows Ink</b> as the tablet input.</div></li>
          <li><div>Fine-tune pressure under <b>Pen feel</b>, and the area you draw in under <b>Screen area</b>.</div></li>
        </ol>`;
    }
  },

  wire() {
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
    renderPositions($("#wzPositions"));
    const c = $("#wzCal"); if (c) c.onclick = () => api().start_calibration("sheet9");
  },
};

$("#wzNext").onclick = () => {
  if (!Wizard.canNext()) return;
  if (Wizard.step === 4 && S && !S.connected) api().connect();
  if (Wizard.step === 5 && S && S.calibrating) api().cancel_calibration();
  if (Wizard.step === Wizard.steps.length - 1) {
    api().finish_onboarding();
    Wizard.close();
    go("home");
    return;
  }
  Wizard.step++; Wizard.render();
};
$("#wzBack").onclick = () => { if (Wizard.step) { Wizard.step--; Wizard.render(); } };
$("#wzSkip").onclick = () => Wizard.close();

// ------------------------------------------------------------------ panel buttons editor

// The panel mock-up is drawn from the tablet's own numbers (core.PANEL_PX, TAB_PX and the
// companion's header/footer/rows), scaled down, so what you see is what the tablet shows.
const Panel = {
  sel: 0,
  btns: null,       // working copy of cfg.panel_buttons
  sig: "",          // what the mock-up was last drawn from
  recording: false,
  HEAD: 230, FOOT: 190, ROWS: 8,

  PRESETS: [
    ["Undo", "shortcut", "ctrl+z"], ["Redo", "shortcut", "ctrl+shift+z"], ["Save", "shortcut", "ctrl+s"],
    ["Brush", "shortcut", "b"], ["Eraser", "shortcut", "e"], ["Size -", "shortcut", "["], ["Size +", "shortcut", "]"],
    ["Hand", "hold", "space"], ["Pick", "hold", "alt"], ["Rotate", "hold", "r"], ["Ctrl", "hold", "ctrl"],
    ["Shift", "hold", "shift"], ["Alt", "hold", "alt"], ["Zoom -", "shortcut", "ctrl+-"], ["Zoom +", "shortcut", "ctrl+="],
    ["Fit", "shortcut", "ctrl+0"], ["Move", "shortcut", "v"], ["Lasso", "shortcut", "l"], ["Fill", "shortcut", "g"],
    ["Swap col", "shortcut", "x"], ["Deselect", "shortcut", "ctrl+d"], ["Transform", "shortcut", "ctrl+t"],
    ["New layer", "shortcut", "ctrl+shift+n"], ["Copy", "shortcut", "ctrl+c"], ["Paste", "shortcut", "ctrl+v"],
    ["Pen", "pen", ""], ["Shapes", "snap", ""], ["Clear", "clear", ""], ["Move image", "trace", ""], ["Light", "light", ""], ["Mask", "mask", ""],
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
    if (b.kind === "light") return S.cfg.light ? "on" : "off";
    if (b.kind === "mask") return S.cfg.mask_edit ? "editing" : S.cfg.mask_on ? "on" : "off";
    return "";
  },

  render() {
    if (!S || !S.cfg.panel_buttons) return;
    const saved = JSON.stringify(S.cfg.panel_buttons);
    if (!this.btns || (!this.dirty && saved !== JSON.stringify(this.btns))) this.btns = JSON.parse(saved);
    if (view !== "panel") return;
    this.renderProfiles();
    const sig = JSON.stringify([this.btns, this.sel, S.cfg.panel_profiles, S.cfg.panel_profile, S.view, S.cfg.panel, S.cfg.panel_open, S.cfg.gestures, S.output, S.cfg.snap, S.cfg.light, S.cfg.trace_adjust, S.cfg.mask_on, S.cfg.mask_edit]);
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
    if (this.dirty) { await setKey("panel_buttons", this.btns); this.dirty = false; }
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
    const add = (name, buttons) => {
      const p = profs();
      p.push({ name, buttons: copy(buttons) });
      this.setProfiles(p, p.length - 1).then(() => { $("#profName").focus(); $("#profName").select(); });
    };
    $("#profNew").onclick = async () => { await this.flush(); add(`Profile ${S.cfg.panel_profiles.length + 1}`, S.default_buttons); };
    $("#profDup").onclick = async () => {
      await this.flush();
      add(S.cfg.panel_profiles[S.cfg.panel_profile].name + " copy", this.btns || S.cfg.panel_buttons);
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

  draw() {
    const wrap = $("#pmWrap"), [vw, vh] = S.view, right = S.cfg.panel === "right";
    const W = S.panel_px, T = S.tab_px, k = Math.min(640 / vh, 380 / (W + 260));
    const px = v => (v * k).toFixed(1) + "px";
    const cw = W / 2, ch = (vh - this.HEAD - this.FOOT) / this.ROWS;
    const open = S.cfg.panel_open !== false;
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
        <div class="pm-status" style="left:${px(22)};top:${px(T + 10 + 72 + 8)};width:${px(W - 44)};font-size:${px(21)}">Connected to PC. Pen on</div>
        ${cells}
        <div class="pm-foot" style="left:${px(22)};top:${px(vh - this.FOOT + 18)};width:${px(W - 44)};font-size:${px(19)}">${S.cfg.gestures ?
          "3 fingers swipe: left undo, right redo<br>2 fingers: pan, pinch to zoom<br>5 fingers: twist to rotate" : "Power button: back to notebooks"}</div>
      </div>`;
    const tab = `<div class="pm-tab" title="Folds the panel away" style="${right ? "right:0" : "left:0"};width:${px(T)};height:${px(T)};border-width:${px(3)}">${chev}</div>`;
    const sw = W + 260;
    wrap.innerHTML = `<div class="pm-bezel"><div class="pm-screen${right ? " right" : ""}" style="width:${px(sw)};height:${px(vh)}">
        ${open && S.cfg.panel !== "off" ? panel : ""}${S.cfg.panel !== "off" ? tab : ""}
        <div class="pm-ink" style="${right ? "right" : "left"}:${px(open ? W + 60 : 120)};top:${px(vh * 0.32)}"></div>
      </div></div>`;
    $("#pmNote").textContent = S.cfg.panel === "off" ? "The panel is switched off (Side: Off)." :
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
    if (t) t.onclick = () => setKey("panel_open", !open).then(refresh);
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
      await setKey("panel_buttons", this.btns);
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
      if (k === "mask" && !b.t) patch.t = "Mask";
      if (k === "none") patch.t = "";
      this.edit(patch);
    };
    $("#peRecord").onclick = () => this.record(!this.recording);
    window.addEventListener("keydown", e => this.onKey(e), true);
    window.addEventListener("keyup", e => this.onKey(e), true);
    this.initProfiles();
    $("#pmReset").onclick = () => {
      this.btns = JSON.parse(JSON.stringify(S.default_buttons));
      this.save();
      toast("Panel buttons back to the defaults.");
    };
  },
};
Panel.init();

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
$("#maskNew").onclick = async () => {
  await api().new_mask(null); maskSrc = null; await refresh();
  $("#maskName").focus(); $("#maskName").select();
};
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
    maskSrc = { el: img, grid: false, s: 1, ox: 0, oy: 0, ver: null };
    await maskCommit(maskSrc);
    toast("Mask made from " + file.name + ". On the Screen area picture, drag it into place and zoom it with the wheel.");
    dragWhat = "mask";
    for (const x of $$("#dragWhat button")) x.classList.toggle("on", x.dataset.v === "mask");
    dragHint();
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
