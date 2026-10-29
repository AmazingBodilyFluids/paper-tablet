// Paper Tablet website: theme swatches, copy buttons, entrance motion, and the canvas demos.
// The demos are drawn live from the same rules the app follows (pressure into width, tilt into angle,
// hold to snap, panel keys, finger gestures, the pen mask), so they stand in for screen recordings
// until real footage exists. To show footage for a demo, set data-video on its tab button.
(() => {
  "use strict";
  const root = document.documentElement;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ skins
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked: skin just isn't remembered */ } },
  };
  const skins = document.querySelectorAll(".skin");
  function applySkin(name) {
    if (name) root.dataset.skin = name; else delete root.dataset.skin;
    skins.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.skin === (root.dataset.skin || ""))));
    readTokens();
  }
  skins.forEach(b => b.addEventListener("click", () => { applySkin(b.dataset.skin); store.set("pt-skin", b.dataset.skin); }));

  // colours for the canvases come from the CSS tokens, so the demos follow the skin
  const T = {};
  function readTokens() {
    const cs = getComputedStyle(root);
    for (const k of ["bg", "s1", "s2", "s3", "s4", "border", "border2", "accent", "accent2", "ink", "text", "dim", "dim2",
                     "green", "amber", "red", "paper", "paper-ink", "bezel", "bezel-hi", "f-mono", "f-sans"]) {
      T[k] = cs.getPropertyValue("--" + k).trim();
    }
  }
  readTokens();
  applySkin(store.get("pt-skin"));
  matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", readTokens);
  new MutationObserver(readTokens).observe(root, { attributes: true, attributeFilter: ["data-theme"] });

  // ------------------------------------------------------------ copy buttons
  document.querySelectorAll("[data-copy]").forEach(btn => btn.addEventListener("click", () => {
    const el = document.getElementById(btn.dataset.copy);
    const done = () => { btn.textContent = "Copied"; setTimeout(() => { btn.textContent = "Copy"; }, 1600); };
    const select = () => { const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r); btn.textContent = "Selected"; };
    try { navigator.clipboard.writeText(el.textContent).then(done, select); } catch (e) { select(); }
  }));

  // --------------------------------------------------------------- entrance
  // Everything is visible at rest; blocks below the fold get a short rise the first time they scroll in.
  if (!reduced && "IntersectionObserver" in window) {
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) { e.target.classList.remove("pending"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px" });
    document.querySelectorAll(".rise").forEach(el => {
      if (el.getBoundingClientRect().top > innerHeight) { el.classList.add("pending"); io.observe(el); }
    });
  }

  // ---------------------------------------------------------------- helpers
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const seg = (t, a, b) => clamp((t - a) / (b - a));
  const ease = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const easeOut = t => 1 - Math.pow(1 - t, 3);
  const TAU = Math.PI * 2;

  // Catmull-Rom through control points, n samples per span
  function spline(pts, n = 24) {
    const out = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(i - 1, 0)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(i + 2, pts.length - 1)];
      for (let s = 0; s < n; s++) {
        const t = s / n, t2 = t * t, t3 = t2 * t;
        out.push([
          0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
          0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
        ]);
      }
    }
    out.push(pts[pts.length - 1].slice());
    return out;
  }

  function rrect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  // a variable-width stroke: pts are {x, y, w}; draws up to index n (fractional)
  function inkStroke(ctx, pts, n, color, alphaFn) {
    const last = Math.min(pts.length - 1, Math.floor(n));
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = color;
    for (let i = 1; i <= last; i++) {
      const a = pts[i - 1], b = pts[i];
      if (b.w <= 0.05) continue;
      ctx.globalAlpha = alphaFn ? alphaFn(i, b) : 1;
      if (ctx.globalAlpha <= 0.01) continue;
      ctx.lineWidth = b.w;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // the Marker Plus: tip at (x, y), body pointing along angle a (radians), lifted when hovering
  function pen(ctx, x, y, a, opts = {}) {
    const len = opts.len || 150, w = opts.w || 11, hover = opts.hover || 0;
    ctx.save();
    if (hover > 0) {
      ctx.strokeStyle = T.accent2; ctx.globalAlpha = 0.55 * hover; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, 7 + 3 * hover, 0, TAU); ctx.stroke(); ctx.globalAlpha = 1;
    }
    ctx.translate(x + Math.cos(a) * hover * 10, y + Math.sin(a) * hover * 10);
    ctx.rotate(a);
    ctx.shadowColor = "rgba(0,0,0,0.35)"; ctx.shadowBlur = 14; ctx.shadowOffsetX = 6; ctx.shadowOffsetY = 10;
    ctx.fillStyle = "#2b2f35";
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(20, -w / 2); ctx.lineTo(len, -w / 2);
    ctx.arc(len, 0, w / 2, -Math.PI / 2, Math.PI / 2); ctx.lineTo(20, w / 2); ctx.closePath(); ctx.fill();
    ctx.shadowColor = "transparent";
    ctx.fillStyle = "#8d939b"; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(8, -2.2); ctx.lineTo(8, 2.2); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#3d424a"; ctx.fillRect(20, -w / 2, len - 20, w * 0.28);
    ctx.fillStyle = "#1b1e22"; ctx.fillRect(len - 26, -w / 2, 3, w);
    ctx.restore();
  }

  function label(ctx, text, x, y, o = {}) {
    ctx.font = `${o.weight || 600} ${o.size || 11}px ${o.mono === false ? T["f-sans"] : T["f-mono"]}`;
    ctx.fillStyle = o.color || T.dim; ctx.textAlign = o.align || "left"; ctx.textBaseline = o.base || "alphabetic";
    if (o.spacing) { try { ctx.letterSpacing = o.spacing; } catch (e) { /* older canvas */ } }
    ctx.fillText(text, x, y);
    try { ctx.letterSpacing = "0px"; } catch (e) { /* older canvas */ }
  }

  function chip(ctx, text, x, y, o = {}) {
    ctx.font = `600 ${o.size || 13}px ${T["f-mono"]}`;
    const w = ctx.measureText(text).width + 20, h = (o.size || 13) + 14;
    ctx.globalAlpha = o.alpha ?? 1;
    rrect(ctx, x - (o.align === "center" ? w / 2 : 0), y, w, h, 4);
    ctx.fillStyle = o.bg || T.s1; ctx.fill(); ctx.strokeStyle = o.border || T.accent; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = o.color || T.ink; ctx.textBaseline = "middle"; ctx.textAlign = "center";
    ctx.fillText(text, x + (o.align === "center" ? 0 : w / 2), y + h / 2 + 0.5);
    ctx.globalAlpha = 1;
    return w;
  }

  function finger(ctx, x, y, alpha = 1, press = 0) {
    ctx.save(); ctx.globalAlpha = alpha;
    ctx.fillStyle = T.accent2; ctx.globalAlpha = alpha * (0.22 + 0.2 * press);
    ctx.beginPath(); ctx.arc(x, y, 17 - 3 * press, 0, TAU); ctx.fill();
    ctx.globalAlpha = alpha; ctx.strokeStyle = T.accent2; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(x, y, 17 - 3 * press, 0, TAU); ctx.stroke();
    ctx.restore();
  }

  function surface(ctx, W, H) {
    ctx.fillStyle = T.s2; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = T.border2;
    for (let x = 20; x < W; x += 24) for (let y = 20; y < H; y += 24) ctx.fillRect(x, y, 1.2, 1.2);
  }

  // ---------------------------------------------------------------- scenes
  // Each scene draws itself at time t (seconds) on a W x H logical canvas.
  const scenes = {};

  // HERO: the tablet in front, the PC behind; the stroke shows on both
  scenes.hero = (() => {
    const W = 1000, H = 640, period = 9.5;
    const tab = { x: 30, y: 236, w: 528, h: 396 };
    const scr = { x: tab.x + 18, y: tab.y + 18, w: tab.w - 36, h: tab.h - 36 };
    const panelW = 74;
    const area = { x: scr.x + panelW + 1, y: scr.y, w: scr.w - panelW - 1, h: scr.h };
    const mon = { x: 372, y: 24, w: 608, h: 384 };
    const doc = { x: mon.x + 16, y: mon.y + 44, w: mon.w - 32, h: mon.h - 84 };
    // the active area fills the document box at the tablet area's aspect
    const dh = doc.h - 20, dw = dh * area.w / area.h;
    const box = { x: doc.x + (doc.w - dw) / 2, y: doc.y + 10, w: dw, h: dh };

    const A = spline([[0.07, 0.62], [0.17, 0.36], [0.28, 0.5], [0.34, 0.72], [0.45, 0.42], [0.54, 0.28], [0.6, 0.52],
                      [0.68, 0.7], [0.78, 0.44], [0.86, 0.32], [0.93, 0.4]], 30);
    const B = spline([[0.12, 0.84], [0.4, 0.8], [0.66, 0.82], [0.9, 0.78]], 30);
    const pA = u => 0.2 + 0.8 * Math.pow(Math.sin(Math.PI * u), 0.6) * (0.78 + 0.22 * Math.sin(u * 17));
    const pB = u => 0.15 + 0.85 * Math.sin(Math.PI * u) * (1 - 0.4 * u);
    const strokes = [
      { pts: A, p: pA, t0: 0.9, t1: 3.9 },
      { pts: B, p: pB, t0: 4.5, t1: 5.6 },
    ];
    const toTab = ([u, v]) => [area.x + u * area.w, area.y + v * area.h];
    const toDoc = ([u, v]) => [box.x + u * box.w, box.y + v * box.h];

    function penAt(t) {
      // returns {u, v, p, down}
      const s0 = strokes[0], s1 = strokes[1];
      const hoverPath = (from, to, k) => [lerp(from[0], to[0], ease(k)), lerp(from[1], to[1], ease(k)) - Math.sin(Math.PI * k) * 0.08];
      if (t < s0.t0) { const k = seg(t, 0.1, s0.t0); const q = hoverPath([1.15, 1.2], s0.pts[0], k); return { u: q[0], v: q[1], p: 0, down: false, hover: 1 }; }
      for (let i = 0; i < strokes.length; i++) {
        const s = strokes[i];
        if (t >= s.t0 && t <= s.t1) {
          const k = seg(t, s.t0, s.t1), f = k * (s.pts.length - 1), j = Math.floor(f), r = f - j;
          const a = s.pts[j], b = s.pts[Math.min(j + 1, s.pts.length - 1)];
          return { u: lerp(a[0], b[0], r), v: lerp(a[1], b[1], r), p: s.p(k), down: true, hover: 0, k };
        }
        if (i < strokes.length - 1 && t > s.t1 && t < strokes[i + 1].t0) {
          const k = seg(t, s.t1, strokes[i + 1].t0); const q = hoverPath(s.pts[s.pts.length - 1], strokes[i + 1].pts[0], k);
          return { u: q[0], v: q[1], p: 0, down: false, hover: 1 };
        }
      }
      const k = seg(t, s1.t1, s1.t1 + 1.1); const q = hoverPath(s1.pts[s1.pts.length - 1], [1.2, 1.25], k);
      return { u: q[0], v: q[1], p: 0, down: false, hover: 1 };
    }

    function draw(ctx, t) {
      ctx.clearRect(0, 0, W, H);
      const docFade = 1 - seg(t, 8.2, 9.2);

      // monitor
      ctx.fillStyle = T.s4; rrect(ctx, mon.x + mon.w / 2 - 34, mon.y + mon.h, 68, 52, 3); ctx.fill();
      rrect(ctx, mon.x + mon.w / 2 - 110, mon.y + mon.h + 48, 220, 10, 5); ctx.fill();
      ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.35)"; ctx.shadowBlur = 30; ctx.shadowOffsetY = 14;
      ctx.fillStyle = "#07090c"; rrect(ctx, mon.x, mon.y, mon.w, mon.h, 10); ctx.fill(); ctx.restore();
      ctx.strokeStyle = T.border2; ctx.lineWidth = 1; rrect(ctx, mon.x + 0.5, mon.y + 0.5, mon.w - 1, mon.h - 1, 10); ctx.stroke();
      // app chrome
      ctx.fillStyle = T.s1; ctx.fillRect(mon.x + 10, mon.y + 10, mon.w - 20, mon.h - 20);
      ctx.fillStyle = T.s3; ctx.fillRect(mon.x + 10, mon.y + 10, mon.w - 20, 26);
      ["#ff5f57", "#febc2e", "#28c840"].forEach((c, i) => { ctx.fillStyle = c; ctx.globalAlpha = 0.7; ctx.beginPath(); ctx.arc(mon.x + 26 + i * 14, mon.y + 23, 4, 0, TAU); ctx.fill(); });
      ctx.globalAlpha = 1;
      label(ctx, "sketch.psd @ 66.7%", mon.x + mon.w / 2, mon.y + 27, { align: "center", size: 10.5, weight: 400, color: T.dim2 });
      ctx.fillStyle = "#f7f5f0"; ctx.fillRect(doc.x, doc.y, doc.w, doc.h);
      ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(74,158,224,0.45)"; ctx.strokeRect(box.x + 0.5, box.y + 0.5, box.w - 1, box.h - 1); ctx.setLineDash([]);

      const P = penAt(t);

      // ink on the PC: pressure sets the width
      for (const s of strokes) {
        if (t < s.t0) continue;
        const n = (seg(t, s.t0, s.t1)) * (s.pts.length - 1);
        const pts = s.pts.map((q, i) => { const d = toDoc(q); return { x: d[0], y: d[1], w: 1 + 9 * s.p(i / (s.pts.length - 1)) }; });
        inkStroke(ctx, pts, n, "#16273b", () => docFade);
      }
      // brush cursor on the PC
      const dP = toDoc([P.u, P.v]);
      if (P.u > -0.05 && P.u < 1.05 && P.v > -0.05 && P.v < 1.05) {
        ctx.strokeStyle = "#4A9EE0"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(dP[0], dP[1], 5 + 5 * P.p, 0, TAU); ctx.stroke();
      }
      // readout strip, like the app's live pen view
      const tiltX = Math.round(22 + 14 * Math.sin(t * 1.3)), tiltY = Math.round(-30 + 8 * Math.cos(t * 0.9));
      const ry = mon.y + mon.h - 22;
      label(ctx, P.down ? "pen down" : "hover", mon.x + 22, ry, { size: 10.5, color: P.down ? T.green : T.dim, weight: 600 });
      label(ctx, `pressure ${String(Math.round(P.p * 100)).padStart(3, " ")}%`, mon.x + 100, ry, { size: 10.5, color: T.dim, weight: 400 });
      label(ctx, `tilt ${tiltX}°, ${tiltY}°`, mon.x + 228, ry, { size: 10.5, color: T.dim, weight: 400 });
      label(ctx, P.down ? `${490 + Math.round(8 * Math.abs(Math.sin(t * 7)))} Hz` : "", mon.x + mon.w - 22, ry, { size: 10.5, color: T.accent2, weight: 600, align: "right" });

      // USB cable with data moving along it while the pen is near
      const c0 = [tab.x + tab.w - 6, tab.y + tab.h - 60], c1 = [mon.x + mon.w / 2 + 40, mon.y + mon.h + 54];
      const cp = [c0[0] + 120, c0[1] + 10], cq = [c1[0] - 20, c1[1] + 60];
      ctx.strokeStyle = T.s4; ctx.lineWidth = 4; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(c0[0], c0[1]); ctx.bezierCurveTo(cp[0], cp[1], cq[0], cq[1], c1[0], c1[1]); ctx.stroke();
      const live = P.down ? 1 : 0.35;
      for (let i = 0; i < 6; i++) {
        const k = ((t * 0.9 + i / 6) % 1);
        const bx = Math.pow(1 - k, 3) * c0[0] + 3 * Math.pow(1 - k, 2) * k * cp[0] + 3 * (1 - k) * k * k * cq[0] + k * k * k * c1[0];
        const by = Math.pow(1 - k, 3) * c0[1] + 3 * Math.pow(1 - k, 2) * k * cp[1] + 3 * (1 - k) * k * k * cq[1] + k * k * k * c1[1];
        ctx.fillStyle = T.accent2; ctx.globalAlpha = live * Math.sin(Math.PI * k);
        ctx.beginPath(); ctx.arc(bx, by, 2.4, 0, TAU); ctx.fill();
      }
      ctx.globalAlpha = 1;

      // tablet
      ctx.save(); ctx.shadowColor = "rgba(0,0,0,0.45)"; ctx.shadowBlur = 40; ctx.shadowOffsetY = 18;
      ctx.fillStyle = T.bezel; rrect(ctx, tab.x, tab.y, tab.w, tab.h, 22); ctx.fill(); ctx.restore();
      ctx.strokeStyle = T["bezel-hi"]; ctx.lineWidth = 1; rrect(ctx, tab.x + 0.5, tab.y + 0.5, tab.w - 1, tab.h - 1, 22); ctx.stroke();
      ctx.fillStyle = T.paper; ctx.fillRect(scr.x, scr.y, scr.w, scr.h);
      // the shortcut panel, 2 x 8
      ctx.fillStyle = T["paper-ink"]; ctx.fillRect(scr.x + panelW, scr.y, 1, scr.h);
      label(ctx, "PAPER", scr.x + 9, scr.y + 15, { size: 6.5, color: T["paper-ink"], spacing: "1.5px" });
      label(ctx, "TABLET", scr.x + 9, scr.y + 23, { size: 6.5, color: T["paper-ink"], spacing: "1.5px" });
      const bw = (panelW - 14) / 2, bh = (scr.h - 64) / 8;
      for (let r = 0; r < 8; r++) for (let c = 0; c < 2; c++) {
        const x = scr.x + 5 + c * (bw + 4), y = scr.y + 34 + r * bh;
        rrect(ctx, x, y, bw, bh - 4, 3);
        const on = (r === 6 && c === 1) || (r === 7 && c === 0);
        if (on) { ctx.fillStyle = T["paper-ink"]; ctx.fill(); } else { ctx.strokeStyle = T["paper-ink"]; ctx.lineWidth = 0.8; ctx.stroke(); }
        ctx.fillStyle = on ? T.paper : T["paper-ink"]; ctx.globalAlpha = 0.75;
        ctx.fillRect(x + bw / 2 - 7, y + bh / 2 - 4, 14, 2); ctx.globalAlpha = 1;
      }
      // the trail on e-paper: thin, fading after a moment
      for (const s of strokes) {
        if (t < s.t0) continue;
        const n = seg(t, s.t0, s.t1) * (s.pts.length - 1);
        const pts = s.pts.map(q => { const d = toTab(q); return { x: d[0], y: d[1], w: 2.2 }; });
        inkStroke(ctx, pts, n, T["paper-ink"], i => {
          const drawnAt = s.t0 + (i / (s.pts.length - 1)) * (s.t1 - s.t0);
          return 1 - 0.85 * seg(t, drawnAt + 1.4, drawnAt + 2.6);
        });
      }
      // the pen
      const tp = toTab([P.u, P.v]);
      const ang = -1.0 + 0.22 * Math.sin(t * 1.3);
      pen(ctx, tp[0], tp[1], ang, { len: 210, w: 14, hover: P.hover ? 1 : 0 });
    }
    return { W, H, period, draw };
  })();

  // PRESSURE: a wave whose weight follows the pressure, with a gauge in raw units
  scenes.pressure = (() => {
    const W = 640, H = 400, period = 6.4;
    const N = 260, pts = [];
    for (let i = 0; i <= N; i++) {
      const u = i / N, p = 0.06 + 0.94 * (0.5 - 0.5 * Math.cos(TAU * 1.5 * u)) * (0.55 + 0.45 * u);
      pts.push({ x: 50 + 410 * u, y: 205 - 70 * Math.sin(TAU * 1.25 * u) * (0.7 + 0.3 * u), w: 1 + 24 * p, p });
    }
    function draw(ctx, t) {
      surface(ctx, W, H);
      const k = seg(t, 0.4, 4.0), n = k * N, fade = 1 - seg(t, 5.6, 6.3);
      inkStroke(ctx, pts, n, T.ink, () => fade);
      const i = Math.min(N, Math.floor(n)), cur = pts[i];
      const p = t > 0.4 && t < 4.0 ? cur.p : 0;
      pen(ctx, cur.x, cur.y, -0.95, { len: 170, w: 12, hover: t < 0.4 || t > 4.0 ? 1 : 0 });
      // gauge
      const gx = 540, gy = 70, gh = 260;
      label(ctx, "PRESSURE", gx + 14, gy - 22, { align: "center", size: 10, color: T.dim2, spacing: "2px" });
      ctx.fillStyle = T.s1; rrect(ctx, gx, gy, 28, gh, 3); ctx.fill(); ctx.strokeStyle = T.border2; ctx.stroke();
      const grad = ctx.createLinearGradient(0, gy + gh, 0, gy);
      grad.addColorStop(0, T.accent2); grad.addColorStop(1, T.accent);
      ctx.fillStyle = grad; rrect(ctx, gx + 3, gy + 3 + (gh - 6) * (1 - p), 22, Math.max(0.01, (gh - 6) * p), 2); ctx.fill();
      for (let q = 0; q <= 4; q++) {
        const y = gy + gh - q * gh / 4;
        ctx.fillStyle = T.dim2; ctx.fillRect(gx + 32, y - 0.5, 6, 1);
        label(ctx, String(q * 1024), gx + 42, y + 3.5, { size: 9.5, color: T.dim2, weight: 400 });
      }
      label(ctx, String(Math.round(p * 4096)).padStart(4, " "), gx + 14, gy + gh + 30, { align: "center", size: 18, color: T.ink });
      label(ctx, "of 4096", gx + 14, gy + gh + 46, { align: "center", size: 9.5, color: T.dim2, weight: 400 });
      return k;
    }
    return { W, H, period, draw };
  })();

  // TILT: a flat brush whose mark turns with the pen, and a protractor
  scenes.tilt = (() => {
    const W = 640, H = 400, period = 7.4;
    const tiltAt = t => 60 * Math.sin(TAU * (t - 0.6) / 3.2);
    function draw(ctx, t) {
      surface(ctx, W, H);
      const k = seg(t, 0.6, 6.0), fade = 1 - seg(t, 6.6, 7.3);
      // stamps
      ctx.fillStyle = T.ink;
      for (let s = 0; s <= k * 220; s++) {
        const u = s / 220, tt = 0.6 + u * 5.4, tx = tiltAt(tt);
        const x = 60 + 380 * u, y = 250 + 28 * Math.sin(u * TAU * 1.2);
        ctx.save(); ctx.globalAlpha = 0.55 * fade; ctx.translate(x, y); ctx.rotate(tx * Math.PI / 180 * 0.9);
        ctx.beginPath(); ctx.ellipse(0, 0, 2.2 + 9 * Math.abs(tx) / 60, 2.2, 0, 0, TAU); ctx.fill(); ctx.restore();
      }
      const u = k, x = 60 + 380 * u, y = 250 + 28 * Math.sin(u * TAU * 1.2);
      const tx = t < 0.6 ? 0 : t > 6 ? tiltAt(6) : tiltAt(t), ty = Math.round(12 * Math.sin(t * 1.7));
      pen(ctx, x, y, -Math.PI / 2 + tx * Math.PI / 180, { len: 170, w: 12, hover: t < 0.6 || t > 6 ? 1 : 0 });
      // protractor
      const cx = 540, cy = 150, R = 70;
      ctx.strokeStyle = T.border2; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx, cy, R, Math.PI, TAU); ctx.stroke();
      for (let d = -90; d <= 90; d += 15) {
        const a = -Math.PI / 2 + d * Math.PI / 180, big = d % 30 === 0, inRange = Math.abs(d) <= 60;
        ctx.strokeStyle = inRange ? T.dim : T.border2; ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * (R - (big ? 10 : 5)), cy + Math.sin(a) * (R - (big ? 10 : 5))); ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R); ctx.stroke();
        if (d % 60 === 0) label(ctx, `${d}°`, cx + Math.cos(a) * (R + 14), cy + Math.sin(a) * (R + 14) + 3, { align: "center", size: 9.5, color: T.dim2, weight: 400 });
      }
      ctx.strokeStyle = T.accent2; ctx.globalAlpha = 0.25; ctx.lineWidth = 8;
      ctx.beginPath(); ctx.arc(cx, cy, R - 4, -Math.PI / 2 - Math.PI / 3, -Math.PI / 2 + Math.PI / 3); ctx.stroke(); ctx.globalAlpha = 1;
      const na = -Math.PI / 2 + tx * Math.PI / 180;
      ctx.strokeStyle = T.accent; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(na) * (R - 6), cy + Math.sin(na) * (R - 6)); ctx.stroke();
      ctx.fillStyle = T.accent; ctx.beginPath(); ctx.arc(cx, cy, 3, 0, TAU); ctx.fill();
      label(ctx, `X ${String(Math.round(tx)).padStart(3, " ")}°   Y ${String(ty).padStart(3, " ")}°`, cx, cy + 30, { align: "center", size: 12, color: T.ink });
      label(ctx, t < 0.6 || t > 6 ? "HOVER" : "CONTACT", cx, cy + 50, { align: "center", size: 9.5, color: t < 0.6 || t > 6 ? T.accent2 : T.green, spacing: "2px" });
      return k;
    }
    return { W, H, period, draw };
  })();

  // HOLD TO SNAP: wobbly shape, hold 0.6 s, Ctrl+Z, clean shape
  scenes.snap = (() => {
    const W = 640, H = 400, period = 9.6, N = 140;
    const wob = (i, f) => Math.sin(i * 0.21 + f) * 4 + Math.sin(i * 0.53 + f * 2) * 2.2;
    function circle() {
      const cx = 120, cy = 200, r = 72, w = [], c = [];
      for (let i = 0; i <= N; i++) {
        const a = -Math.PI / 2 + (i / N) * (TAU + 0.18);
        const rr = r + wob(i, 1) - 3 * (i / N);
        w.push([cx + Math.cos(a) * rr * 1.04, cy + Math.sin(a) * rr]);
        const ac = -Math.PI / 2 + (i / N) * TAU; c.push([cx + Math.cos(ac) * r, cy + Math.sin(ac) * r]);
      }
      return { w, c, name: "circle" };
    }
    function line() {
      const a = [250, 300], b = [390, 104], w = [], c = [];
      const nx = -(b[1] - a[1]), ny = b[0] - a[0], L = Math.hypot(nx, ny);
      for (let i = 0; i <= N; i++) {
        const k = i / N, o = wob(i, 3) * 0.9 + Math.sin(k * Math.PI) * 7;
        w.push([lerp(a[0], b[0], k) + nx / L * o, lerp(a[1], b[1], k) + ny / L * o]); c.push([lerp(a[0], b[0], k), lerp(a[1], b[1], k)]);
      }
      return { w, c, name: "line" };
    }
    function tri() {
      const v = [[515, 118], [588, 280], [442, 280], [515, 118]], w = [], c = [];
      for (let i = 0; i <= N; i++) {
        const k = i / N * 3, s = Math.min(2, Math.floor(k)), r = k - s;
        const p = [lerp(v[s][0], v[s + 1][0], r), lerp(v[s][1], v[s + 1][1], r)];
        c.push(p); w.push([p[0] + wob(i, 5) * 0.9, p[1] + wob(i, 7) * 0.9]);
      }
      return { w, c, name: "triangle" };
    }
    const shapes = [circle(), line(), tri()];
    function draw(ctx, t) {
      surface(ctx, W, H);
      const fade = 1 - seg(t, 9.1, 9.6);
      let penP = null, hold = 0, ctrlz = 0, hover = 1;
      shapes.forEach((s, j) => {
        const t0 = j * 3;
        if (t < t0) return;
        const lt = t - t0;
        const dk = seg(lt, 0.2, 1.4), mk = easeOut(seg(lt, 2.0, 2.35));
        const pts = s.w.map((q, i) => ({ x: lerp(q[0], s.c[i][0], mk), y: lerp(q[1], s.c[i][1], mk), w: 3.4 }));
        inkStroke(ctx, pts, dk * N, T.ink, () => fade);
        if (lt < 2.0) {
          const q = pts[Math.min(N, Math.floor(dk * N))];
          penP = q; hover = lt < 0.2 ? 1 : 0;
          if (lt > 1.4) hold = seg(lt, 1.4, 2.0);
        }
        if (lt >= 1.95 && lt < 2.6) ctrlz = 1 - seg(lt, 2.35, 2.6);
        if (lt >= 2.35) {
          const a = seg(lt, 2.35, 2.6) * fade;
          const cx = s.c.reduce((m, p) => m + p[0], 0) / s.c.length;
          ctx.globalAlpha = a;
          label(ctx, s.name, cx, 352, { align: "center", size: 11, color: T.green, spacing: "1px" });
          ctx.globalAlpha = 1;
        }
      });
      if (penP) {
        if (hold > 0) {
          ctx.strokeStyle = T.accent2; ctx.lineWidth = 2.5; ctx.beginPath();
          ctx.arc(penP.x, penP.y, 14, -Math.PI / 2, -Math.PI / 2 + TAU * hold); ctx.stroke();
          label(ctx, `hold ${(0.6 * hold).toFixed(1)} s`, penP.x + 22, penP.y + 34, { size: 10.5, color: T.accent2 });
        }
        pen(ctx, penP.x, penP.y, -1.0, { len: 160, w: 12, hover });
      }
      if (ctrlz > 0) chip(ctx, "Ctrl+Z", W - 22 - 76, H - 52, { alpha: ctrlz, size: 12 });
      return t / period;
    }
    return { W, H, period, draw };
  })();

  // SHORTCUT PANEL: finger taps on the tablet's panel, keys arrive on the PC
  scenes.panel = (() => {
    const W = 640, H = 400, period = 8.6;
    const names = [["Undo", "Ctrl Z"], ["Redo", "Ctrl Shift Z"], ["Size -", "["], ["Size +", "]"], ["Brush", "B"], ["Eraser", "E"],
                   ["Hand", "holds Space"], ["Pick", "holds Alt"], ["Ctrl", "holds Ctrl"], ["Shift", "holds Shift"],
                   ["Zoom -", "Ctrl -"], ["Zoom +", "Ctrl ="], ["Fit", "Ctrl 0"], ["Shapes", "on"], ["Pen", "on"], ["Save", "Ctrl S"]];
    const scr = { x: 24, y: 24, w: 270, h: 352 }, pw = 132;
    const bw = (pw - 18) / 2, bh = (scr.h - 52) / 8;
    const btn = i => ({ x: scr.x + 6 + (i % 2) * (bw + 6), y: scr.y + 44 + Math.floor(i / 2) * bh, w: bw, h: bh - 5 });
    // taps: [button index, time, hold seconds, key text]
    const taps = [[4, 0.8, 0, "B"], [3, 2.0, 0, "]"], [3, 2.7, 0, "]"], [0, 4.0, 0, "Ctrl+Z"], [6, 5.4, 1.6, "Space (held)"]];
    function draw(ctx, t) {
      surface(ctx, W, H);
      // tablet screen
      ctx.fillStyle = T.bezel; rrect(ctx, scr.x - 12, scr.y - 12, scr.w + 24, scr.h + 24, 14); ctx.fill();
      ctx.fillStyle = T.paper; ctx.fillRect(scr.x, scr.y, scr.w, scr.h);
      ctx.fillStyle = T["paper-ink"]; ctx.fillRect(scr.x + pw, scr.y, 1, scr.h);
      label(ctx, "PAPER TABLET", scr.x + 10, scr.y + 24, { size: 9, color: T["paper-ink"], spacing: "2px" });
      let active = -1, key = null, keyAge = 9, hand = 0;
      for (const [i, tt, hold, k] of taps) {
        if (t >= tt && t < tt + Math.max(0.28, hold)) active = i;
        if (t >= tt) { key = k; keyAge = t - tt; }
        if (hold && t >= tt && t < tt + hold) hand = seg(t, tt + 0.15, tt + hold - 0.2);
      }
      names.forEach(([n, s], i) => {
        const b = btn(i), on = i === active || i === 13 || i === 14;
        rrect(ctx, b.x, b.y, b.w, b.h, 5);
        if (on) { ctx.fillStyle = T["paper-ink"]; ctx.fill(); } else { ctx.strokeStyle = T["paper-ink"]; ctx.lineWidth = 1.1; ctx.stroke(); }
        label(ctx, n, b.x + b.w / 2, b.y + b.h / 2 - 1, { align: "center", size: 11, mono: false, weight: 600, color: on ? T.paper : T["paper-ink"] });
        label(ctx, s, b.x + b.w / 2, b.y + b.h / 2 + 11, { align: "center", size: 7.5, mono: false, weight: 400, color: on ? T.paper : T["paper-ink"] });
      });
      // finger path between taps
      let fx = scr.x + 210, fy = scr.y + 300, press = 0, fa = 1;
      for (let j = 0; j < taps.length; j++) {
        const [i, tt, hold] = taps[j], b = btn(i), prev = j ? btn(taps[j - 1][0]) : { x: scr.x + 190, y: scr.y + 290, w: 0, h: 0 };
        const prevEnd = j ? taps[j - 1][1] + Math.max(0.28, taps[j - 1][2]) : 0.1;
        if (t >= prevEnd && t <= tt + Math.max(0.28, hold)) {
          const k = ease(seg(t, prevEnd, tt));
          fx = lerp(prev.x + prev.w / 2, b.x + b.w / 2, k); fy = lerp(prev.y + prev.h / 2, b.y + b.h / 2, k);
          press = t >= tt ? 1 : 0;
        }
      }
      const lastEnd = 5.4 + 1.6;
      if (t > lastEnd) { const k = ease(seg(t, lastEnd, lastEnd + 0.8)); const b = btn(6); fx = lerp(b.x + b.w / 2, scr.x + 210, k); fy = lerp(b.y + b.h / 2, scr.y + 300, k); fa = 1 - seg(t, lastEnd + 0.4, lastEnd + 0.9); }
      // PC window
      const win = { x: 330, y: 52, w: 286, h: 296 };
      ctx.fillStyle = T.s1; rrect(ctx, win.x, win.y, win.w, win.h, 6); ctx.fill(); ctx.strokeStyle = T.border2; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = T.s3; ctx.fillRect(win.x + 1, win.y + 1, win.w - 2, 24);
      label(ctx, "PC", win.x + 12, win.y + 16, { size: 9.5, color: T.dim2, spacing: "2px" });
      const cv = { x: win.x + 12, y: win.y + 36, w: win.w - 24, h: win.h - 48 };
      ctx.fillStyle = "#f7f5f0"; ctx.fillRect(cv.x, cv.y, cv.w, cv.h);
      ctx.save(); ctx.beginPath(); ctx.rect(cv.x, cv.y, cv.w, cv.h); ctx.clip();
      const off = 46 * Math.sin(Math.PI * hand);
      const undone = t >= 4.0 ? 1 - seg(t, 4.0, 4.25) : 1;
      ctx.lineCap = "round"; ctx.strokeStyle = "#16273b";
      ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(cv.x + 30 + off, cv.y + 150); ctx.bezierCurveTo(cv.x + 80 + off, cv.y + 60, cv.x + 140 + off, cv.y + 200, cv.x + 230 + off, cv.y + 90); ctx.stroke();
      ctx.globalAlpha = undone; ctx.lineWidth = 7; ctx.beginPath(); ctx.moveTo(cv.x + 40 + off, cv.y + 205); ctx.quadraticCurveTo(cv.x + 130 + off, cv.y + 170, cv.x + 220 + off, cv.y + 210); ctx.stroke();
      ctx.globalAlpha = 1; ctx.restore();
      const size = t >= 2.7 ? 13 : t >= 2.0 ? 10 : 7;
      ctx.strokeStyle = "#4A9EE0"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(cv.x + cv.w - 34, cv.y + 30, size, 0, TAU); ctx.stroke();
      label(ctx, t >= 0.8 ? "Brush" : "", cv.x + cv.w - 34, cv.y + 58, { align: "center", size: 9, color: "#16273b", weight: 600 });
      if (key && keyAge < 1.4) chip(ctx, key, win.x + win.w / 2, win.y + win.h + 14, { alpha: 1 - seg(keyAge, 1.0, 1.4), align: "center" });
      finger(ctx, fx, fy, fa, press);
      return t / period;
    }
    return { W, H, period, draw };
  })();

  // FINGER GESTURES: three-finger undo/redo, pinch, five-finger twist and reset
  scenes.gestures = (() => {
    const W = 640, H = 400, period = 11;
    const doodles = [
      spline([[180, 150], [230, 110], [290, 140], [330, 120]], 16),
      spline([[200, 200], [260, 230], [320, 190], [400, 220], [450, 200]], 16),
      spline([[220, 270], [270, 250], [330, 285], [420, 260]], 16),
      spline([[380, 110], [420, 150], [460, 120], [470, 170]], 16),
    ];
    function draw(ctx, t) {
      surface(ctx, W, H);
      const cx = 320, cy = 200;
      let visible = 4, scale = 1, rot = 0, caption = "", sub = "", count = "";
      // phase A: undo / redo
      let fingers = [];
      if (t < 4.4) {
        caption = "3 fingers, swipe";
        const left = seg(t, 0.5, 2.0), right = seg(t, 2.5, 3.8);
        const x = 470 - 300 * ease(left) + 300 * ease(right);
        const undos = Math.min(3, Math.floor(left * 3.4)), redos = Math.min(3, Math.floor(right * 3.4));
        visible = 4 - undos + redos;
        count = right > 0 ? `Redo ×${redos}` : undos ? `Undo ×${undos}` : "";
        const a = 1 - seg(t, 3.9, 4.3);
        if (t > 0.3) fingers = [[x, 165, a], [x + 8, 200, a], [x + 2, 235, a]];
      } else if (t < 7) {
        caption = "2 fingers, pinch";
        const k = Math.sin(Math.PI * seg(t, 4.7, 6.7));
        scale = 1 + 0.38 * ease(k);
        const d = 55 + 85 * ease(k), a = Math.min(seg(t, 4.4, 4.7), 1 - seg(t, 6.7, 7));
        fingers = [[cx - d * 0.7, cy + d * 0.7, a], [cx + d * 0.7, cy - d * 0.7, a]];
        count = scale > 1.02 ? `Zoom ${Math.round(scale * 100)}%` : "";
      } else {
        caption = "5 fingers, twist"; sub = "next release";
        const twist = ease(seg(t, 7.3, 8.6)), back = ease(seg(t, 9.4, 9.9));
        rot = (26 * twist) * (1 - back);
        const a = Math.min(seg(t, 7.0, 7.3), 1 - seg(t, 10.2, 10.6));
        const tap = (seg(t, 8.9, 9.05) > 0 && t < 9.05) || (t > 9.2 && t < 9.35);
        for (let i = 0; i < 5; i++) {
          const ang = -Math.PI / 2 + (i - 2) * 0.62 + rot * Math.PI / 180;
          fingers.push([cx + Math.cos(ang) * 92, cy + 30 + Math.sin(ang) * 92, a, tap ? 1 : 0]);
        }
        count = t > 9.0 ? "Double tap: reset" : rot > 1 ? `Rotate ${Math.round(rot)}°` : "";
      }
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot * Math.PI / 180); ctx.scale(scale, scale); ctx.translate(-cx, -cy);
      ctx.strokeStyle = T.border2; ctx.lineWidth = 1; ctx.strokeRect(150, 90, 340, 220);
      doodles.forEach((d, i) => {
        const pts = d.map(q => ({ x: q[0], y: q[1], w: 3.2 }));
        inkStroke(ctx, pts, pts.length, T.ink, () => i < visible ? 1 : 0.08);
      });
      ctx.restore();
      fingers.forEach(f => finger(ctx, f[0], f[1], f[2], f[3] || 0));
      label(ctx, caption.toUpperCase(), 24, 34, { size: 10.5, color: T.dim, spacing: "2px" });
      if (sub) label(ctx, sub.toUpperCase(), 24, 52, { size: 9.5, color: T.accent2, spacing: "2px" });
      ctx.font = `600 12px ${T["f-mono"]}`;
      if (count) chip(ctx, count, W - 24 - ctx.measureText(count).width - 20, H - 54, { size: 12 });
      return t / period;
    }
    return { W, H, period, draw };
  })();

  // PEN MASK: a greyscale stencil; black ignores the pen, grey fades the pressure
  scenes.mask = (() => {
    const W = 640, H = 400, period = 8.4;
    // a soft white disc (pen works) beside a black-to-white ramp (pressure fades in), on black (pen ignored)
    const shade = (x, y) => {
      const disc = clamp((150 - Math.hypot(x - 175, y - 200)) / 45);
      const inRamp = x > 360 && x < 610 && y > 70 && y < 330;
      const edge = inRamp ? clamp(Math.min(x - 360, 610 - x, y - 70, 330 - y) / 6) : 0;
      return Math.max(disc, edge * clamp((x - 380) / 210));
    };
    let img = null;
    function maskImage() {
      const c = document.createElement("canvas"); c.width = 160; c.height = 100;
      const g = c.getContext("2d"), d = g.createImageData(160, 100);
      for (let y = 0; y < 100; y++) for (let x = 0; x < 160; x++) {
        const v = Math.round(255 * shade(x * 4, y * 4)), o = (y * 160 + x) * 4;
        d.data[o] = d.data[o + 1] = d.data[o + 2] = v; d.data[o + 3] = 255;
      }
      g.putImageData(d, 0, 0); return c;
    }
    const lines = [];
    for (let r = 0; r < 6; r++) {
      const pts = [], y0 = 70 + r * 52;
      for (let i = 0; i <= 160; i++) {
        const u = i / 160, x = 30 + 580 * (r % 2 ? 1 - u : u), y = y0 + 9 * Math.sin(u * TAU * 3 + r);
        const p = 0.85, s = shade(x, y);
        pts.push({ x, y, w: s < 0.03 ? 0 : 1 + 9 * p * s, s });
      }
      lines.push(pts);
    }
    function draw(ctx, t) {
      if (!img) img = maskImage();
      ctx.imageSmoothingEnabled = true; ctx.drawImage(img, 0, 0, W, H);
      const fade = 1 - seg(t, 7.8, 8.4);
      const k = seg(t, 0.3, 7.0) * lines.length;
      let cur = null;
      lines.forEach((pts, r) => {
        const n = clamp(k - r, 0, 1) * (pts.length - 1);
        if (n <= 0) return;
        inkStroke(ctx, pts, n, "#2f7fc4", () => fade);
        if (k - r < 1) cur = pts[Math.floor(n)];
      });
      if (cur) {
        pen(ctx, cur.x, cur.y, -1.0, { len: 160, w: 12 });
        const s = cur.s;
        chip(ctx, s < 0.03 ? "pen ignored" : s > 0.97 ? "full pressure" : `pressure × ${s.toFixed(2)}`, 24, 20,
             { size: 11, bg: "#0a1520", color: "#ffffff", border: s < 0.03 ? "#ff5d5d" : "#4A9EE0" });
      }
      // legend
      const items = [["#000000", "black: ignored"], ["#808080", "grey: fades"], ["#ffffff", "white: normal"]];
      ctx.fillStyle = "rgba(6,14,23,0.78)"; rrect(ctx, W - 336, H - 40, 316, 26, 4); ctx.fill();
      items.forEach(([c, s], i) => {
        const x = W - 326 + i * 104;
        ctx.fillStyle = c; ctx.fillRect(x, H - 33, 12, 12); ctx.strokeStyle = "rgba(255,255,255,0.4)"; ctx.strokeRect(x + 0.5, H - 32.5, 11, 11);
        label(ctx, s, x + 18, H - 23, { size: 9.5, color: "#ffffff", weight: 400 });
      });
      return t / period;
    }
    return { W, H, period, draw };
  })();

  // -------------------------------------------------------------- the loop
  class Player {
    constructor(canvas, name, onProgress) {
      this.canvas = canvas; this.ctx = canvas.getContext("2d"); this.onProgress = onProgress;
      this.visible = true; this.set(name);
      new ResizeObserver(() => this.fit()).observe(canvas);
      if ("IntersectionObserver" in window) new IntersectionObserver(es => { this.visible = es[0].isIntersecting; }).observe(canvas);
    }
    set(name) { this.scene = scenes[name]; this.name = name; this.start = performance.now(); this.loops = 0; this.fit(); }
    fit() {
      const r = this.canvas.getBoundingClientRect(); if (!r.width) return;
      const dpr = Math.min(2, devicePixelRatio || 1);
      this.canvas.width = Math.round(r.width * dpr); this.canvas.height = Math.round(r.height * dpr);
      this.scale = Math.min(r.width / this.scene.W, r.height / this.scene.H) * dpr;
      this.ox = (this.canvas.width - this.scene.W * this.scale) / 2; this.oy = (this.canvas.height - this.scene.H * this.scale) / 2;
      if (reduced) this.frame(this.scene.period * 0.42);
    }
    frame(t) {
      const c = this.ctx; c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, this.canvas.width, this.canvas.height);
      c.setTransform(this.scale, 0, 0, this.scale, this.ox, this.oy);
      this.scene.draw(c, t);
      this.onProgress?.(t / this.scene.period);
    }
    tick(now) {
      if (reduced || !this.visible || document.hidden) return;
      const el = (now - this.start) / 1000, p = this.scene.period;
      const loops = Math.floor(el / p);
      if (loops !== this.loops) { this.loops = loops; this.onLoop?.(loops); }
      this.frame(el % p);
    }
  }

  const players = [];
  const heroCanvas = document.getElementById("hero");
  if (heroCanvas) players.push(new Player(heroCanvas, "hero"));

  const demoCanvas = document.getElementById("demo"), scrub = document.getElementById("scrub");
  const screen = document.getElementById("screen"), caption = document.getElementById("caption");
  const tabs = [...document.querySelectorAll(".demo-tab")];
  let demo = null, userPicked = false, video = null;
  function show(name) {
    tabs.forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    const tpl = document.getElementById("cap-" + name); caption.replaceChildren(tpl.content.cloneNode(true));
    const tab = tabs.find(b => b.dataset.tab === name), src = tab?.dataset.video;
    video?.remove(); video = null; demoCanvas.hidden = false; screen.querySelector(".rec").textContent = "Animated demo";
    if (src) {
      // real footage: play it muted and looping, fall back to the animation if it can't load
      video = Object.assign(document.createElement("video"), { src, muted: true, loop: true, playsInline: true, autoplay: !reduced, controls: reduced });
      if (tab.dataset.poster) video.poster = tab.dataset.poster;
      video.addEventListener("loadeddata", () => { demoCanvas.hidden = true; screen.querySelector(".rec").textContent = "Recorded on a Paper Pro"; });
      video.addEventListener("error", () => { video.remove(); video = null; demoCanvas.hidden = false; });
      video.addEventListener("timeupdate", () => { if (video?.duration) scrub.style.width = (100 * video.currentTime / video.duration) + "%"; });
      screen.prepend(video);
    }
    demo.set(name);
  }
  if (demoCanvas) {
    demo = new Player(demoCanvas, "pressure", p => { if (!video) scrub.style.width = (p * 100).toFixed(2) + "%"; });
    // without a pick from the viewer, move on to the next demo after two plays
    demo.onLoop = loops => {
      if (userPicked || loops < 2) return;
      const i = tabs.findIndex(b => b.dataset.tab === demo.name);
      show(tabs[(i + 1) % tabs.length].dataset.tab);
    };
    players.push(demo);
    tabs.forEach(b => b.addEventListener("click", () => { userPicked = true; show(b.dataset.tab); }));
    show("pressure");
  }
  function loop(now) { players.forEach(p => p.tick(now)); requestAnimationFrame(loop); }
  if (!reduced) requestAnimationFrame(loop);
})();
