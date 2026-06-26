/**
 * TMHP integration diagrams — geometry engine + figure definitions.
 *
 * Renders the integration-page diagrams into any `.tmhp-diagram[data-diagram]`
 * container present on the page. Hybrid design: a static, dark-mode-aware SVG
 * base (styles in css/integration-diagrams.css) with light, optional motion
 * and interaction (lane focus on the hero, a step-through on the sequences).
 * Connectors are computed from box geometry so every line leaves an edge centre.
 *
 * Loaded with `defer`; renders on DOMContentLoaded. Honors prefers-reduced-motion
 * via CSS. No dark-mode JS — the stylesheet flips tokens for OS preference and
 * Shibuya's manual toggle.
 */
(function () {
  "use strict";

  /* ----------------------------- geometry SSOT ---------------------------- */
  var TOK = { R: 12, lh: 15.8, baseShift: 3.6, arrow: 10 };
  var esc = function (s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };
  // edge-centre anchor: side in l/r/t/b
  var A = function (b, s) {
    return s === "r" ? [b.x + b.w, b.y + b.h / 2]
      : s === "l" ? [b.x, b.y + b.h / 2]
      : s === "t" ? [b.x + b.w / 2, b.y]
      : [b.x + b.w / 2, b.y + b.h];
  };
  function box(b) {
    var cx = b.x + b.w / 2, cy = b.y + b.h / 2, n = b.lines.length, top = cy - (n - 1) * TOK.lh / 2, t = "";
    b.lines.forEach(function (ln, i) {
      t += '<text class="bx-' + (ln.k || "ttl") + '" x="' + cx + '" y="' + (top + i * TOK.lh + TOK.baseShift).toFixed(1) + '" text-anchor="middle">' + esc(ln.t) + "</text>";
    });
    return '<g class="node n-' + b.role + '"><rect class="nb" x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="' + (b.r || TOK.R) + '"/>' + t + "</g>";
  }
  function arrowDefs() {
    return '<defs>'
      + '<marker id="tid-arr" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--edge)"/></marker>'
      + '<marker id="tid-arrD" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--edge-dash)"/></marker>'
      + "</defs>";
  }
  function edge(d, o) {
    o = o || {};
    var arrow = o.arrow === false ? "" : ' marker-end="url(#' + (o.dash ? "tid-arrD" : "tid-arr") + ')"';
    return '<path class="edge' + (o.dash ? " dash" : "") + (o.cls ? " " + o.cls : "") + '" d="' + d + '"' + arrow + "/>";
  }
  function lbl(x, y, t) { return '<text class="elab mono" x="' + x + '" y="' + y + '" text-anchor="middle">' + esc(t) + "</text>"; }
  function glab(x, y, t) { return '<text class="glab" x="' + x + '" y="' + y + '">' + esc(t) + "</text>"; }
  function hLink(a, b, o) { var p = A(a, "r"), q = A(b, "l"); return edge("M" + p[0] + " " + p[1] + " L" + q[0] + " " + q[1], o); }
  function vDown(a, b, o) { var p = A(a, "b"); return edge("M" + p[0] + " " + p[1] + " L" + p[0] + " " + b.y, o); }
  // orthogonal polyline through waypoints with rounded corners (~box radius)
  function roundPath(pts, r) {
    var p = [];
    pts.forEach(function (q) { var l = p[p.length - 1]; if (!l || l[0] !== q[0] || l[1] !== q[1]) p.push(q); });
    if (p.length < 3) return "M" + p.map(function (q) { return q[0] + " " + q[1]; }).join(" L");
    var d = "M" + p[0][0] + " " + p[0][1];
    for (var i = 1; i < p.length - 1; i++) {
      var a = p[i - 1], b = p[i], c = p[i + 1];
      var v1 = [b[0] - a[0], b[1] - a[1]], v2 = [c[0] - b[0], c[1] - b[1]];
      var l1 = Math.hypot(v1[0], v1[1]) || 1, l2 = Math.hypot(v2[0], v2[1]) || 1, rr = Math.min(r, l1 / 2, l2 / 2);
      var p1 = [b[0] - v1[0] / l1 * rr, b[1] - v1[1] / l1 * rr], p2 = [b[0] + v2[0] / l2 * rr, b[1] + v2[1] / l2 * rr];
      d += " L" + p1[0].toFixed(2) + " " + p1[1].toFixed(2) + " Q " + b[0] + " " + b[1] + " " + p2[0].toFixed(2) + " " + p2[1].toFixed(2);
    }
    var z = p[p.length - 1];
    return d + " L" + z[0] + " " + z[1];
  }
  function mergeTo(srcs, P, busDX, o) {
    var bx = P[0] - busDX, s = "";
    srcs.forEach(function (q) { s += edge(roundPath([[q[0], q[1]], [bx, q[1]], [bx, P[1]], [P[0], P[1]]], TOK.R), o); });
    return s;
  }
  // hero merge: 3 seams converge at busX (placed OUTSIDE the package container), then ONE line enters the core
  function heroMerge(srcs, P, busX) {
    var s = "";
    srcs.forEach(function (q) { s += edge(roundPath([[q[0], q[1]], [busX, q[1]], [busX, P[1]]], TOK.R), { dash: true, arrow: false }); });
    return s + edge("M" + busX + " " + P[1] + " L" + P[0] + " " + P[1], { dash: true });
  }
  function svg(W, H, title, desc, inner) {
    return '<svg viewBox="0 0 ' + W + " " + H + '" role="img"><title>' + esc(title) + "</title><desc>" + esc(desc) + "</desc>" + arrowDefs() + inner + "</svg>";
  }

  /* ------------------------------- #1 hero -------------------------------- */
  function hero(el) {
    var W = 1000, H = 300, hostW = 150, varW = 150, seamW = 168, Hb = 50;
    var colHost = 44, colVar = 229, colSeam = 414, coreX = 637, retX = 827, coreW = 150, retW = 150;
    var rows = { py: 78, ep: 160, fm: 242 }, cy = function (r) { return rows[r] + Hb / 2; };
    var core = { x: coreX, y: cy("ep") - 72, w: coreW, h: 144, role: "core" };
    var ret = { x: retX, y: cy("ep") - 72, w: retW, h: 144, role: "out" };
    var B = {
      pyH: { x: colHost, y: rows.py, w: hostW, h: Hb, role: "host", lines: [{ t: "Python study" }, { t: "you own the loop", k: "sub" }] },
      pyS: { x: colSeam, y: rows.py, w: seamW, h: Hb, role: "seam", lines: [{ t: "analyze_dynamic()", k: "mono" }, { t: "/ step()", k: "mono" }] },
      epH: { x: colHost, y: rows.ep, w: hostW, h: Hb, role: "host", lines: [{ t: "EnergyPlus" }, { t: "owns loop·tank·meters", k: "sub" }] },
      epV: { x: colVar, y: rows.ep, w: varW, h: Hb, role: "vars", lines: [{ t: "T_in, mdot, cp", k: "mono" }, { t: "load, T0", k: "mono" }] },
      epS: { x: colSeam, y: rows.ep, w: seamW, h: Hb, role: "seam", lines: [{ t: "analyze_steady()", k: "mono" }, { t: "steady surrogate", k: "note" }] },
      fmH: { x: colHost, y: rows.fm, w: hostW, h: Hb, role: "host", lines: [{ t: "FMI master" }, { t: "FMPy·OMS·Dymola", k: "sub" }] },
      fmV: { x: colVar, y: rows.fm, w: varW, h: Hb, role: "vars", lines: [{ t: "T0, dhw_draw", k: "mono" }, { t: "T_sup_w", k: "mono" }] },
      fmS: { x: colSeam, y: rows.fm, w: seamW, h: Hb, role: "seam", lines: [{ t: "step()", k: "mono" }, { t: "FMU owns state", k: "note" }] }
    };
    var P = A(core, "l");
    var group = '<rect class="grect" x="623" y="78" width="368" height="214" rx="14"/>' + glab(639, 98, "TMHP PACKAGE");
    var conn = [
      hLink(B.pyH, B.pyS), hLink(B.epH, B.epV), hLink(B.epV, B.epS), hLink(B.fmH, B.fmV), hLink(B.fmV, B.fmS),
      heroMerge([A(B.pyS, "r"), A(B.epS, "r"), A(B.fmS, "r")], P, 600), hLink(core, ret)
    ].join("");
    var lane = function (k, parts) { return '<g class="lane" data-lane="' + k + '">' + parts + "</g>"; };
    var lanes = lane("py", box(B.pyH) + box(B.pyS)) + lane("ep", box(B.epH) + box(B.epV) + box(B.epS)) + lane("fm", box(B.fmH) + box(B.fmV) + box(B.fmS));
    var core2 = box({ x: core.x, y: core.y, w: core.w, h: core.h, role: "core", lines: [{ t: "TMHP core" }, { t: "cycle-resolved", k: "sub" }, { t: "heat-pump model", k: "sub" }] });
    var ret2 = box({ x: ret.x, y: ret.y, w: ret.w, h: ret.h, role: "out", lines: [{ t: "Returns" }, { t: "E_cmp · E_tot", k: "mono" }, { t: "Q_ref_tank", k: "mono" }, { t: "cop_sys · T_tank_w", k: "mono" }, { t: "+ diagnostics", k: "note" }] });
    var ctl = '<div class="tid-ctl">'
      + '<button data-lane="all" class="on">All paths</button>'
      + '<button data-lane="py">Python</button>'
      + '<button data-lane="ep">EnergyPlus</button>'
      + '<button data-lane="fm">FMI master</button></div>';
    el.innerHTML = ctl
      + svg(W, H, "TMHP integration dataflow", "Python, EnergyPlus and an FMI master each drive the same TMHP core through a public seam.",
        group + conn + lanes + core2 + ret2)
      + '<p class="tid-cap">Many entry points, one cycle-resolved model. EnergyPlus and FMI reach it through different public seams.</p>';
    var s = el.querySelector("svg");
    el.querySelectorAll(".tid-ctl button[data-lane]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        el.querySelectorAll(".tid-ctl button[data-lane]").forEach(function (b) { b.classList.toggle("on", b === btn); });
        var lane = btn.getAttribute("data-lane");
        if (lane === "all") { s.classList.remove("has-sel"); s.querySelectorAll(".lane").forEach(function (l) { l.classList.remove("sel"); }); return; }
        s.classList.add("has-sel");
        s.querySelectorAll(".lane").forEach(function (l) { l.classList.toggle("sel", l.getAttribute("data-lane") === lane); });
      });
    });
  }

  /* --------------------------- sequences (#2 #5) -------------------------- */
  function sequence(el, opt) {
    var W = 1000, cx = opt.actors.map(function (a) { return a.x + a.w / 2; });
    var aBoxes = opt.actors.map(box).join("");
    var llTop = Math.max.apply(null, opt.actors.map(function (a) { return a.y + a.h; }));
    var pitch = 52, y0 = llTop + (opt.banner ? 80 : 52);
    var rowY = function (i) { return y0 + i * pitch; };
    var lastY = rowY(opt.steps.length - 1), llBot = lastY + 30, H = llBot + 16;
    var lines = cx.map(function (x) { return '<line class="lifeline" x1="' + x + '" y1="' + llTop + '" x2="' + x + '" y2="' + llBot + '"/>'; }).join("");
    var bn = "";
    if (opt.banner) {
      var bb = opt.banner;
      bn = '<rect class="grect" x="' + bb.x + '" y="' + bb.y + '" width="' + bb.w + '" height="26" rx="8"/><text class="bx-note" x="' + (bb.x + bb.w / 2) + '" y="' + (bb.y + 16.5) + '" text-anchor="middle" fill="var(--muted)">' + esc(bb.text) + "</text>";
    }
    var ph = "", gi = 0, lx = 18;
    while (gi < opt.steps.length) {
      var gj = gi;
      while (gj + 1 < opt.steps.length && opt.steps[gj + 1].phase === opt.steps[gi].phase) gj++;
      ph += '<line x1="' + lx + '" y1="' + (rowY(gi) - 15) + '" x2="' + lx + '" y2="' + (rowY(gj) + 15) + '" stroke="var(--core-stroke)" stroke-width="1.6" opacity="0.45" stroke-linecap="round"/>';
      ph += '<text class="phaselab" x="' + (lx + 8) + '" y="' + (rowY(gi) - 5) + '" text-anchor="start">' + esc(opt.steps[gi].phase || "") + "</text>";
      gi = gj + 1;
    }
    var numNode = function (x, y, n) { return '<g class="numnode"><circle cx="' + x + '" cy="' + y + '" r="9.5"/><text x="' + x + '" y="' + (y + 3.6) + '" text-anchor="middle">' + n + "</text></g>"; };
    var mg = "";
    opt.steps.forEach(function (st, i) {
      var y = rowY(i), n = i + 1;
      if (st.kind === "self") {
        var x = cx[st.at];
        var loop = edge("M" + x + " " + (y - 7) + " C " + (x + 46) + " " + (y - 11) + " " + (x + 46) + " " + (y + 11) + " " + (x + 3) + " " + (y + 7), { dash: st.dash });
        var cap = '<text class="bx-note" x="' + (x + 58) + '" y="' + (y - 1) + '" text-anchor="start" fill="var(--muted)">' + esc(st.t) + "</text>"
          + (st.t2 ? '<text class="bx-note" x="' + (x + 58) + '" y="' + (y + 12) + '" text-anchor="start" fill="var(--muted)">' + esc(st.t2) + "</text>" : "");
        mg += '<g class="msg">' + loop + cap + numNode(x, y - 7, n) + "</g>";
      } else {
        var x1 = cx[st.from], x2 = cx[st.to];
        var arrow = edge("M" + x1 + " " + y + " L" + x2 + " " + y, { dash: st.dash });
        mg += '<g class="msg">' + arrow + lbl((x1 + x2) / 2, y - 9, st.t) + numNode(x1, y, n) + "</g>";
      }
    });
    var n = opt.steps.length;
    var ctl = '<div class="tid-ctl">'
      + '<button data-act="play">▶ play</button>'
      + '<button data-act="step">step ▸</button>'
      + '<button data-act="back">◂ back</button>'
      + '<button data-act="reset">reset</button>'
      + '<span class="tid-sp"></span><span class="tid-hint">rest · ' + n + " steps</span></div>";
    el.innerHTML = ctl + svg(W, H, opt.title, opt.desc || opt.title, bn + ph + aBoxes + lines + mg)
      + (opt.caption ? '<p class="tid-cap">' + esc(opt.caption) + "</p>" : "");
    var s = el.querySelector("svg"), hint = el.querySelector(".tid-hint"), msgs = s.querySelectorAll(".msg");
    var cur = 0, timer = null;
    function render() {
      msgs.forEach(function (g, i) { g.classList.toggle("active", cur > 0 && i + 1 === cur); g.classList.toggle("dim", cur > 0 && i + 1 !== cur); });
      hint.textContent = cur === 0 ? ("rest · " + n + " steps") : ("step " + cur + " / " + n);
    }
    function stop() { if (timer) { clearInterval(timer); timer = null; } var b = el.querySelector('[data-act="play"]'); b.textContent = "▶ play"; b.classList.remove("on"); }
    el.querySelector('[data-act="step"]').addEventListener("click", function () { cur = Math.min(n, cur + 1); render(); });
    el.querySelector('[data-act="back"]').addEventListener("click", function () { cur = Math.max(0, cur - 1); render(); });
    el.querySelector('[data-act="reset"]').addEventListener("click", function () { stop(); cur = 0; render(); });
    el.querySelector('[data-act="play"]').addEventListener("click", function () {
      var b = this;
      if (timer) { stop(); return; }
      if (cur >= n) cur = 0;
      b.textContent = "⏸ pause"; b.classList.add("on");
      timer = setInterval(function () { if (cur >= n) { stop(); return; } cur++; render(); }, 950);
    });
    render();
  }

  function fmuSeq(el) {
    sequence(el, {
      title: "FMU do_step protocol",
      desc: "Messages between the FMI master, the FMU adapter and the TMHP core across one communication step.",
      actors: [
        { x: 20, y: 20, w: 240, h: 53, role: "host", lines: [{ t: "FMI master" }] },
        { x: 380, y: 20, w: 240, h: 53, role: "seam", lines: [{ t: "FMU adapter" }, { t: "TmhpAshpbSlave", k: "note" }] },
        { x: 740, y: 20, w: 240, h: 53, role: "core", lines: [{ t: "TMHP core · step()" }] }
      ],
      steps: [
        { kind: "msg", from: 0, to: 1, t: "setReal(T0, dhw_draw, T_sup_w)", phase: "SET INPUTS" },
        { kind: "msg", from: 0, to: 1, t: "do_step(t, dt)", phase: "ADVANCE ONE dt" },
        { kind: "msg", from: 1, to: 2, t: "step(state, inputs, dt)", dash: true, phase: "ADVANCE ONE dt" },
        { kind: "msg", from: 2, to: 1, t: "(new_state, res)", phase: "ADVANCE ONE dt" },
        { kind: "self", at: 1, t: "carry DynamicState;", t2: "sanitize NaN / inf", phase: "ADVANCE ONE dt" },
        { kind: "msg", from: 1, to: 0, t: "getReal(E_cmp, E_tot, Q_ref_tank, cop_sys, T_tank_w)", phase: "READ OUTPUTS" }
      ],
      caption: "The loop runs every communication step. The FMU owns the DynamicState; step() is the only TMHP call per step."
    });
  }

  function epSeq(el) {
    sequence(el, {
      title: "EnergyPlus plant callback",
      desc: "Messages between the EnergyPlus plant solver, the TMHP plugin and analyze_steady on one plant-solver call.",
      actors: [
        { x: 9, y: 20, w: 252, h: 53, role: "host", lines: [{ t: "EnergyPlus plant solver" }, { t: "owns loop·tank·timestep", k: "note" }] },
        { x: 374, y: 20, w: 252, h: 53, role: "seam", lines: [{ t: "TMHP plugin" }, { t: "TmhpPlantSurrogate", k: "note" }] },
        { x: 739, y: 20, w: 252, h: 53, role: "core", lines: [{ t: "TMHP core" }, { t: "analyze_steady()", k: "note" }] }
      ],
      steps: [
        { kind: "msg", from: 0, to: 1, t: "T_in, mdot, cp, load, T0", phase: "READ LOOP STATE" },
        { kind: "msg", from: 1, to: 2, t: "analyze_steady(T_tank_w, T0, Q_ref_tank)", dash: true, phase: "STEADY SOLVE" },
        { kind: "msg", from: 2, to: 1, t: "res {E_cmp, Q_ref_tank, converged, failure_reason}", phase: "STEADY SOLVE" },
        { kind: "self", at: 1, t: "memoize on rounded inputs;", t2: "T_out = T_in + Q_ref_tank/(mdot·cp)", phase: "STEADY SOLVE" },
        { kind: "msg", from: 1, to: 0, t: "actuators: T_out, mdot", phase: "ACTUATE + METER" },
        { kind: "msg", from: 1, to: 0, t: "globals: tmhp_E_cmp_J (metered), _W", phase: "ACTUATE + METER" }
      ],
      banner: { x: 30, y: 78, w: 940, text: "Once before the run · TmhpPlantInit sizes the plant connection — design flow, min/max capacity" },
      caption: "EnergyPlus owns the loop, tank and timestep; TMHP only answers a steady cycle solve and meters compressor energy."
    });
  }

  /* ------------------------ #3 FMI 2.0 vs 3.0 ----------------------------- */
  function fmiCompare(el) {
    var W = 1000, H = 348, colW = 300, lc = 130, rc = 570, card = { y: 54, h: 154 }, th = 30;
    var kernel = { x: 130, y: 290, w: 740, h: 56 };
    function adapterCard(x, title, lines) {
      var w = colW, y = card.y, h = card.h, cxx = x + w / 2, r = TOK.R;
      var cardEl = '<g class="n-host"><rect class="nb" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="' + r + '"/></g>';
      var titleBg = '<path d="M' + x + " " + (y + th) + " L" + x + " " + (y + r) + " Q " + x + " " + y + " " + (x + r) + " " + y + " L " + (x + w - r) + " " + y + " Q " + (x + w) + " " + y + " " + (x + w) + " " + (y + r) + " L " + (x + w) + " " + (y + th) + ' Z" fill="var(--seam-fill)" stroke="var(--seam-stroke)" stroke-width="0.75"/>';
      var titleEl = '<g class="n-seam"><text class="bx-ttl" x="' + cxx + '" y="' + (y + th / 2 + 4) + '" text-anchor="middle">' + esc(title) + "</text></g>";
      var bcy = (y + th + y + h) / 2, n = lines.length, top = bcy - (n - 1) * TOK.lh / 2, bl = '<g class="n-host">';
      lines.forEach(function (ln, i) { bl += '<text class="bx-' + (ln.k || "mono") + '" x="' + cxx + '" y="' + (top + i * TOK.lh + TOK.baseShift).toFixed(1) + '" text-anchor="middle">' + esc(ln.t) + "</text>"; });
      bl += "</g>";
      return cardEl + titleBg + titleEl + bl;
    }
    var L = adapterCard(lc, "FMI 2.0 adapter · pythonfmu", [{ t: "type: Real" }, { t: "do_step → bool" }, { t: "units patched in XML" }, { t: "InitialUnknowns patch" }, { t: "broadest importer reach", k: "note" }]);
    var R = adapterCard(rc, "FMI 3.0 adapter · pythonfmu3", [{ t: "type: Float64" }, { t: "do_step → Fmi3StepResult" }, { t: "(ok · discard + earlyReturn)" }, { t: "inline unit= · explicit time" }, { t: "modern major version", k: "note" }]);
    var lb = lc + colW / 2, rb = rc + colW / 2, cbY = card.y + card.h, midY = (cbY + kernel.y) / 2;
    var conn = edge("M" + lb + " " + cbY + " L" + lb + " " + kernel.y, { dash: true }) + edge("M" + rb + " " + cbY + " L" + rb + " " + kernel.y, { dash: true })
      + '<text class="elab" x="' + lb + '" y="' + (midY + 3) + '" text-anchor="middle">wraps</text><text class="elab" x="' + rb + '" y="' + (midY + 3) + '" text-anchor="middle">wraps</text>';
    var kbox = box({ x: kernel.x, y: kernel.y, w: kernel.w, h: kernel.h, role: "core", lines: [{ t: "Shared kernel — AirSourceHeatPumpBoiler.step()" }, { t: "same 4 params · 3 inputs · 8 outputs · identical input mapping", k: "sub" }] });
    el.innerHTML = svg(W, H, "FMI 2.0 vs 3.0 adapters", "Two FMI adapters wrapping one TMHP step kernel.", conn + L + R + '<g class="pulse">' + kbox + "</g>")
      + '<p class="tid-cap">Pick FMI 2.0 for reach, FMI 3.0 for the modern major version — the physics is byte-for-byte the same.</p>';
  }

  /* ---------------------- #4 composite co-simulation ---------------------- */
  function fmuExample(el) {
    var W = 1000, H = 300;
    var env = { x: 84, y: 90, w: 196, h: 120, role: "host", lines: [{ t: "Building envelope" }, { t: "EnergyPlus → FMU", k: "sub" }, { t: "loads · zone temps", k: "note" }] };
    var hp = { x: 402, y: 75, w: 196, h: 150, role: "core", lines: [{ t: "TMHP heat pump" }, { t: "cycle-resolved FMU", k: "sub" }, { t: "E_cmp · Q_ref_tank · cop_sys", k: "note" }] };
    var ctl = { x: 720, y: 90, w: 196, h: 120, role: "seam", lines: [{ t: "Supervisory controller" }, { t: "Modelica / Simulink", k: "sub" }, { t: "setpoints · on/off", k: "note" }] };
    var e1 = A(env, "r"), h1 = A(hp, "l"), h2 = A(hp, "r"), c1 = A(ctl, "l");
    var cb = A(ctl, "b"), hb = A(hp, "b"), yb = Math.max(cb[1], hb[1]) + 28;
    var gTop = 75 - 30, gBot = yb + 30;
    var group = '<rect class="grect" x="40" y="' + gTop + '" width="920" height="' + (gBot - gTop) + '" rx="16"/>' + glab(58, gTop + 20, "FMI MASTER orchestrates the time loop");
    var link1 = '<path id="tid-cp1" class="edge" d="M' + e1[0] + " " + e1[1] + " L" + h1[0] + " " + h1[1] + '" marker-end="url(#tid-arr)"/>';
    var link2 = '<path id="tid-cp2" class="edge" d="M' + h2[0] + " " + h2[1] + " L" + c1[0] + " " + c1[1] + '" marker-end="url(#tid-arr)"/>';
    var sp = edge(roundPath([cb, [cb[0], yb], [hb[0], yb], hb], TOK.R), { dash: true });
    var labs = lbl((e1[0] + h1[0]) / 2, e1[1] - 8, "heat demand") + lbl((h2[0] + c1[0]) / 2, h2[1] - 8, "COP · power") + lbl((cb[0] + hb[0]) / 2, yb + 14, "setpoint");
    var dots = '<circle class="flowdot" r="3.6"><animateMotion dur="2s" repeatCount="indefinite"><mpath href="#tid-cp1"/></animateMotion></circle><circle class="flowdot" r="3.6"><animateMotion dur="2s" begin="-1s" repeatCount="indefinite"><mpath href="#tid-cp2"/></animateMotion></circle>';
    el.innerHTML = svg(W, H, "Composite co-simulation example", "Envelope, heat pump and controller coupled through an FMI master.",
      group + link1 + link2 + sp + box(env) + box(hp) + box(ctl) + labs + dots)
      + '<p class="tid-cap">You get real refrigerant-cycle physics inside a multi-tool study, with no model rewritten per host.</p>';
  }

  /* ------------------------ #6 cycle-resolved swap-in --------------------- */
  function epExample(el) {
    var W = 1000, H = 300;
    var bl = { x: 60, y: 96, w: 180, h: 64, role: "host", lines: [{ t: "Envelope + loads" }, { t: "IDF · schedules", k: "sub" }] };
    var pl = { x: 60, y: 184, w: 180, h: 64, role: "host", lines: [{ t: "Plant loop + tank" }, { t: "WaterHeater:Mixed", k: "sub" }] };
    var hp = { x: 300, y: 140, w: 170, h: 64, role: "core", lines: [{ t: "HP component" }, { t: "was catalogue curve", k: "sub" }, { t: "now: TMHP cycle solve", k: "note" }] };
    var gL = '<rect class="grect" x="30" y="40" width="468" height="232" rx="16"/>' + glab(48, 62, "ENERGYPLUS BUILDING (unchanged)");
    var gR = '<rect class="grect" x="528" y="40" width="442" height="232" rx="16"/>' + glab(546, 62, "NOW YOU CAN ASK: which refrigerant?");
    var P = A(hp, "l");
    var conn = mergeTo([A(bl, "r"), A(pl, "r")], P, 18, {});
    var pcx = (528 + 970) / 2;
    var cops = [{ l: "R32", v: 3.9, c: "core" }, { l: "R290", v: 4.3, c: "ok" }, { l: "R410A", v: 3.2, c: "out" }, { l: "R1234yf", v: 3.6, c: "host" }];
    var bw = 56, gap = 34, base = 252, maxh = 118, vmax = 4.3, sc = maxh / vmax, rTop = 6;
    var blockW = cops.length * bw + (cops.length - 1) * gap, x0 = pcx - blockW / 2;
    var barTop = function (x, h) { var y = base - h; return "M" + x + " " + base + " L" + x + " " + (y + rTop) + " Q " + x + " " + y + " " + (x + rTop) + " " + y + " L " + (x + bw - rTop) + " " + y + " Q " + (x + bw) + " " + y + " " + (x + bw) + " " + (y + rTop) + " L " + (x + bw) + " " + base + " Z"; };
    var bars = '<line class="edge" x1="' + (x0 - 22) + '" y1="' + base + '" x2="' + (x0 + blockW + 22) + '" y2="' + base + '"/>'
      + '<text class="bx-note" x="' + pcx + '" y="94" text-anchor="middle" fill="var(--muted)">same building · same dispatch · seasonal COP</text>';
    cops.forEach(function (c, i) {
      var x = x0 + i * (bw + gap), h = c.v * sc, ccx = x + bw / 2;
      bars += '<g class="n-' + c.c + '"><path class="nb" d="' + barTop(x, h) + '"/></g>'
        + '<text class="bx-ttl" x="' + ccx + '" y="' + (base - h - 8) + '" text-anchor="middle" fill="var(--ink)" style="font-size:12.5px">' + c.v.toFixed(1) + "</text>"
        + '<text class="bx-note" x="' + ccx + '" y="' + (base + 16) + '" text-anchor="middle" fill="var(--muted)">' + c.l + "</text>";
    });
    el.innerHTML = svg(W, H, "Cycle-resolved swap-in example", "EnergyPlus building with the heat-pump plant component replaced by a TMHP cycle solve.",
      gL + gR + conn + box(bl) + box(pl) + box(hp) + bars)
      + '<p class="tid-cap">You get catalogue-free refrigerant studies in a real building, without re-fitting a performance curve per candidate.</p>';
  }

  var DIAGRAMS = { hero: hero, "fmu-seq": fmuSeq, "ep-seq": epSeq, "fmi-compare": fmiCompare, "fmu-example": fmuExample, "ep-example": epExample };

  function init() {
    var nodes = document.querySelectorAll(".tmhp-diagram[data-diagram]");
    nodes.forEach(function (el) {
      var fn = DIAGRAMS[el.getAttribute("data-diagram")];
      if (fn) { try { fn(el); } catch (e) { if (window.console) console.error("tmhp-diagram render failed:", el.getAttribute("data-diagram"), e); } }
    });
  }
  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
