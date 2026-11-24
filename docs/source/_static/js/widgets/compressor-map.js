/**
 * Compressor efficiency map explorer (#253).
 *
 * Draws TMHP's default compressor maps (η_v, η_ov, f_loss, η_is) from
 * `_static/data/compressor-maps.json`, evaluated by the pure module
 * `compressor-map-model.js` (the same rules the agreement test checks).
 * Contract: docs/notes/compressor-map-widget-contract.md.
 *
 * No D3: the self-hosted bundle has no contours or colour interpolators,
 * and the widget only needs a log/linear axis, a marching-squares contour
 * pass and a piecewise colour ramp, all written here in plain SVG/canvas.
 *
 * Usage: `mount(rootElement, data)`.
 */
import { evaluate, rpRangeAt, hullExtent } from "./compressor-map-model.js";

const SVG_NS = "http://www.w3.org/2000/svg";

const QUANTITIES = ["eta_ov", "eta_v", "f_loss", "eta_is"];
const PANEL_ORDER = ["eta_v", "eta_ov", "f_loss", "eta_is"];
const SYMBOL = { eta_v: "η_{v}", eta_ov: "η_{ov}", f_loss: "f_{loss}", eta_is: "η_{is}" };
const NAME = {
    eta_v: "Volumetric efficiency",
    eta_ov: "Overall efficiency",
    f_loss: "Loss fraction",
    eta_is: "Isentropic efficiency",
};
const SPEEDS = [30, 50, 70, 90, 110];
const T_MIN = -30, T_MAX = 25, T_DEFAULT = 0;
const N_AXIS = [15, 120];
const MEASURED_BAND_K = 2.5;

// Single-hue ordinal ramps, low speed -> high speed (light / dark page).
const RAMP = {
    violet: {
        light: ["#a78bfa", "#8b5cf6", "#7c3aed", "#6d28d9", "#4c1d95"],
        dark: ["#7c3aed", "#8b5cf6", "#a78bfa", "#c4b5fd", "#ede9fe"],
    },
    teal: {
        light: ["#2dd4bf", "#14b8a6", "#0d9488", "#0f766e", "#134e4a"],
        dark: ["#0d9488", "#14b8a6", "#2dd4bf", "#5eead4", "#ccfbf1"],
    },
};
// Blue sequential heat ramp, low -> high.
const HEAT = {
    light: ["#f0f6ff", "#dbeafe", "#bfdbfe", "#93c5fd", "#60a5fa", "#3b82f6", "#2563eb", "#1d4ed8", "#1e40af", "#1e3a8a"],
    dark: ["#0f1a3a", "#172554", "#1e3a8a", "#1e40af", "#1d4ed8", "#2563eb", "#3b82f6", "#60a5fa", "#93c5fd", "#c7defe"],
};

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, parent = null) {
    const node = tag.startsWith("svg:")
        ? document.createElementNS(SVG_NS, tag.slice(4))
        : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined) continue;
        if (k === "text") node.textContent = v;
        else if (k === "class") node.setAttribute("class", v);
        else node.setAttribute(k, v);
    }
    if (parent) parent.appendChild(node);
    return node;
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** `r_{p}` -> `r<sub>p</sub>` (input escaped first). */
function rich(s) {
    return escapeHtml(s).replace(/_\{([^}]*)\}/g, "<sub>$1</sub>");
}

/** Fill an SVG <text> with `r_{p}`-style markup as tspans. */
function svgRich(textNode, s) {
    const parts = s.split(/(_\{[^}]*\})/);
    for (const part of parts) {
        if (!part) continue;
        const m = part.match(/^_\{([^}]*)\}$/);
        if (m) {
            el("svg:tspan", { "baseline-shift": "sub", "font-size": "0.78em", text: m[1] }, textNode);
        } else {
            el("svg:tspan", { text: part }, textNode);
        }
    }
    return textNode;
}

function axisLabel(q) {
    return `${NAME[q]} (${SYMBOL[q]})`;
}

const MINUS = "−";
function fmt(v, digits = 3) {
    if (v === null || v === undefined || !Number.isFinite(v)) return "—";
    const s = v.toFixed(digits);
    return s.startsWith("-") ? MINUS + s.slice(1) : s;
}
function fmtSigned(v, digits = 3) {
    if (!Number.isFinite(v)) return "—";
    const s = Math.abs(v).toFixed(digits);
    return (v < 0 ? MINUS : "+") + s;
}
function fmtT(t) {
    return t < 0 ? MINUS + Math.abs(t) : String(t);
}

function niceStep(span, count) {
    const raw = span / Math.max(1, count);
    const mag = 10 ** Math.floor(Math.log10(raw));
    const r = raw / mag;
    const m = r < 1.5 ? 1 : r < 2.25 ? 2 : r < 3.5 ? 2.5 : r < 7.5 ? 5 : 10;
    return m * mag;
}
function ticksBetween(lo, hi, step) {
    const out = [];
    const start = Math.ceil(lo / step - 1e-9);
    for (let k = start; k * step <= hi + step * 1e-9; k++) out.push(+(k * step).toPrecision(12));
    return out;
}
function decimalsOf(step) {
    return Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
}

function linear(d0, d1, r0, r1) {
    const f = (v) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
    f.invert = (p) => d0 + ((p - r0) / (r1 - r0)) * (d1 - d0);
    return f;
}
function logScale(d0, d1, r0, r1) {
    const l0 = Math.log(d0), l1 = Math.log(d1);
    const f = (v) => r0 + ((Math.log(v) - l0) / (l1 - l0)) * (r1 - r0);
    f.invert = (p) => Math.exp(l0 + ((p - r0) / (r1 - r0)) * (l1 - l0));
    return f;
}

function hexToRgb(h) {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function makeRamp(stops) {
    const rgb = stops.map(hexToRgb);
    return (t) => {
        const u = Math.min(1, Math.max(0, t)) * (rgb.length - 1);
        const i = Math.min(rgb.length - 2, Math.floor(u));
        const f = u - i;
        const a = rgb[i], b = rgb[i + 1];
        return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f));
    };
}
const rgbCss = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;

function isDarkPage() {
    const html = document.documentElement;
    if (html.classList.contains("dark")) return true;
    if (html.classList.contains("light")) return false;
    const mode = html.getAttribute("data-color-mode") || html.getAttribute("data-theme");
    if (mode === "dark") return true;
    if (mode === "light") return false;
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

/**
 * Marching squares on a row-major grid (values at cell-centre samples).
 * Returns segments [[x0, y0, x1, y1], ...] in grid index units.
 */
function contourSegments(values, nx, ny, level) {
    const segs = [];
    const at = (i, j) => values[j * nx + i];
    const lerp = (a, b) => (level - a) / (b - a);
    for (let j = 0; j < ny - 1; j++) {
        for (let i = 0; i < nx - 1; i++) {
            const v0 = at(i, j), v1 = at(i + 1, j), v2 = at(i + 1, j + 1), v3 = at(i, j + 1);
            if (![v0, v1, v2, v3].every(Number.isFinite)) continue;
            const c = (v0 >= level ? 1 : 0) | (v1 >= level ? 2 : 0) | (v2 >= level ? 4 : 0) | (v3 >= level ? 8 : 0);
            if (c === 0 || c === 15) continue;
            const top = [i + lerp(v0, v1), j];
            const right = [i + 1, j + lerp(v1, v2)];
            const bottom = [i + lerp(v3, v2), j + 1];
            const left = [i, j + lerp(v0, v3)];
            const add = (a, b) => segs.push([a[0], a[1], b[0], b[1]]);
            const centreHigh = (v0 + v1 + v2 + v3) / 4 >= level;
            switch (c) {
                case 1: case 14: add(left, top); break;
                case 2: case 13: add(top, right); break;
                case 3: case 12: add(left, right); break;
                case 4: case 11: add(right, bottom); break;
                case 6: case 9: add(top, bottom); break;
                case 7: case 8: add(left, bottom); break;
                case 5:
                    if (centreHigh) { add(left, bottom); add(top, right); } else { add(left, top); add(right, bottom); }
                    break;
                case 10:
                    if (centreHigh) { add(left, top); add(right, bottom); } else { add(left, bottom); add(top, right); }
                    break;
                default: break;
            }
        }
    }
    return segs;
}

// ------------------------------------------------------------------ mount

export function mount(root, data) {
    const ext = hullExtent(data);
    const [nLo, nHi] = data.domain.N_rps;
    const peak = data.peak_eta_ov;
    // r_p axis: log scale (the maps are polynomials in ln r_p), wide enough
    // for the whole hull so the axis does not jump as T_evap moves.
    const R_AXIS = [1.4, Math.max(8, Math.ceil(ext.PR[1] + 0.3))];
    const measured = data.measured_points.map((p) => ({
        ...p,
        eta_is: 1 - p.f_loss > 0 ? p.eta_ov / (1 - p.f_loss) : null,
    }));

    const state = { T: T_DEFAULT, q: "eta_ov", showMeasured: true, hoverR: null };

    // Fixed per-quantity value ranges: in-domain values over the slider's
    // whole T_evap span, so colours and y axes keep their meaning as T moves.
    const ranges = {};
    for (const q of QUANTITIES) ranges[q] = [Infinity, -Infinity];
    for (let T = T_MIN; T <= T_MAX; T++) {
        const rr = rpRangeAt(data, T);
        if (!rr) continue;
        for (let k = 0; k <= 40; k++) {
            const r = rr[0] * (rr[1] / rr[0]) ** (k / 40);
            for (let N = nLo; N <= nHi; N += 5) {
                const v = evaluate(data, r, N, T);
                for (const q of QUANTITIES) {
                    if (v[q] === null) continue;
                    ranges[q][0] = Math.min(ranges[q][0], v[q]);
                    ranges[q][1] = Math.max(ranges[q][1], v[q]);
                }
            }
        }
    }

    // ---------------------------------------------------------- skeleton
    root.innerHTML = "";
    root.classList.add("cmw-ready");
    const uid = "cmw" + Math.random().toString(36).slice(2, 8);

    const controls = el("div", { class: "cmw-controls" }, root);

    const tField = el("div", { class: "cmw-field cmw-field-t" }, controls);
    const tLabel = el("label", { for: `${uid}-t` }, tField);
    tLabel.innerHTML = 'Evaporating temperature (T<sub>evap</sub>) [°C]';
    const tRow = el("div", { class: "cmw-slider-row" }, tField);
    const tInput = el("input", {
        id: `${uid}-t`, type: "range", min: T_MIN, max: T_MAX, step: 1, value: T_DEFAULT,
        "aria-label": "Evaporating temperature T_evap in degrees Celsius",
    }, tRow);
    const tOut = el("output", { for: `${uid}-t`, class: "cmw-out" }, tRow);

    const qField = el("fieldset", { class: "cmw-field cmw-seg" }, controls);
    el("legend", { text: "Quantity" }, qField);
    const qWrap = el("div", { class: "cmw-seg-buttons", role: "radiogroup", "aria-label": "Quantity shown in the map" }, qField);
    for (const q of QUANTITIES) {
        const lab = el("label", { class: "cmw-seg-item" }, qWrap);
        const input = el("input", {
            type: "radio", name: `${uid}-q`, value: q, "aria-label": `${NAME[q]} ${SYMBOL[q].replace(/[_{}]/g, "")}`,
        }, lab);
        if (q === state.q) input.checked = true;
        const span = el("span", {}, lab);
        span.innerHTML = rich(SYMBOL[q]);
        input.addEventListener("change", () => { if (input.checked) { state.q = q; renderHeat(); renderReadout(); } });
    }

    const mField = el("div", { class: "cmw-field cmw-check" }, controls);
    const mLab = el("label", {}, mField);
    const mInput = el("input", { type: "checkbox", "aria-label": "Show measured points within 2.5 K of T_evap" }, mLab);
    mInput.checked = true;
    el("span", { text: "Show measured points" }, mLab);
    mInput.addEventListener("change", () => { state.showMeasured = mInput.checked; renderHeat(); });

    const status = el("p", { class: "cmw-status", "aria-live": "polite" }, root);

    const heatTitle = el("div", { class: "cmw-title" }, root);
    const heatBox = el("div", { class: "cmw-plot cmw-heat" }, root);
    const heatLegend = el("div", { class: "cmw-legend", role: "group", "aria-label": "Map legend" }, root);

    const lineTitle = el("div", { class: "cmw-title cmw-title-lines" }, root);
    const lineLegend = el("div", { class: "cmw-legend", role: "group", "aria-label": "Speed curves legend" }, root);
    const grid = el("div", { class: "cmw-grid" }, root);
    const panels = {};
    for (const q of PANEL_ORDER) {
        const box = el("div", { class: "cmw-panel" }, grid);
        const head = el("div", { class: "cmw-panel-title" }, box);
        head.innerHTML = rich(axisLabel(q));
        if (q === "eta_v" || q === "eta_is") {
            el("div", { class: "cmw-panel-note", text: "Dotted line at 1: the cycle sets values above 1 to 1." }, box);
        }
        const plot = el("div", { class: "cmw-plot" }, box);
        panels[q] = { box, plot, svg: null, x: null, y: null, m: null, cross: null };
    }

    const details = el("details", { class: "cmw-details" }, root);
    const summary = el("summary", {}, details);
    const tableWrap = el("div", { class: "cmw-table-wrap" }, details);

    const tip = el("div", { class: "cmw-tip", "aria-hidden": "true" }, root);

    // ------------------------------------------------------------ tooltip
    function showTip(html, clientX, clientY) {
        tip.innerHTML = html;
        tip.style.display = "block";
        const box = root.getBoundingClientRect();
        const w = tip.offsetWidth, h = tip.offsetHeight;
        let x = clientX - box.left + 14;
        let y = clientY - box.top + 14;
        if (x + w > box.width - 4) x = clientX - box.left - w - 14;
        if (x < 4) x = 4;
        if (y + h > box.height - 4) y = clientY - box.top - h - 14;
        tip.style.left = `${x}px`;
        tip.style.top = `${Math.max(4, y)}px`;
    }
    function hideTip() { tip.style.display = "none"; }

    // -------------------------------------------------------------- theme
    let dark = isDarkPage();
    const palette = () => ({
        heat: makeRamp(dark ? HEAT.dark : HEAT.light),
        violet: dark ? RAMP.violet.dark : RAMP.violet.light,
        teal: dark ? RAMP.teal.dark : RAMP.teal.light,
    });

    function xTicks(width) {
        const all = [1.5, 2, 3, 4, 5, 6, 8, 10, 12];
        const t = width < 300 ? [2, 3, 4, 6, 8, 12] : all;
        return t.filter((v) => v >= R_AXIS[0] && v <= R_AXIS[1]);
    }

    function drawAxes(svg, x, y, m, W, H, opts) {
        const g = el("svg:g", { class: "cmw-axis" }, svg);
        for (const v of opts.yTicks) {
            el("svg:line", { class: "cmw-gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, g);
            el("svg:text", { x: m.l - 5, y: y(v) + 3.5, "text-anchor": "end", text: opts.yFmt(v) }, g);
        }
        for (const v of opts.xTicks) {
            el("svg:line", { class: "cmw-gridline", x1: x(v), x2: x(v), y1: m.t, y2: H - m.b }, g);
            el("svg:text", { x: x(v), y: H - m.b + 14, "text-anchor": "middle", text: String(v) }, g);
        }
        el("svg:rect", { class: "cmw-frame", x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b }, g);
        const xl = el("svg:text", { class: "cmw-axis-label", x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle" }, g);
        svgRich(xl, "Pressure ratio (r_{p}), log scale");
        if (opts.yLabel) {
            const yl = el("svg:text", {
                class: "cmw-axis-label", transform: `translate(12,${(m.t + H - m.b) / 2}) rotate(-90)`, "text-anchor": "middle",
            }, g);
            svgRich(yl, opts.yLabel);
        }
    }

    // ------------------------------------------------------------ heatmap
    function renderHeat() {
        const pal = palette();
        const q = state.q;
        const T = state.T;
        const [lo, hi] = ranges[q];
        const colour = (v) => pal.heat((v - lo) / (hi - lo));
        heatTitle.innerHTML = `${rich(axisLabel(q))} over r<sub>p</sub> and N at T<sub>evap</sub> = ${fmtT(T)} °C`;

        heatBox.innerHTML = "";
        const W = Math.max(280, heatBox.clientWidth || 600);
        const H = Math.round(Math.min(400, Math.max(260, W * 0.56)));
        const m = { l: 50, r: 12, t: 10, b: 40 };
        const x = logScale(R_AXIS[0], R_AXIS[1], m.l, W - m.r);
        const y = linear(N_AXIS[0], N_AXIS[1], H - m.b, m.t);
        const svg = el("svg:svg", {
            width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
            "aria-label": `${NAME[q]} map over pressure ratio and speed at T_evap ${T} degrees Celsius`,
        }, heatBox);

        // sampled field
        const nx = Math.min(220, Math.round((W - m.l - m.r) / 3));
        const ny = Math.round((H - m.t - m.b) / 3);
        const vals = new Float64Array(nx * ny);
        const lx0 = Math.log(R_AXIS[0]), lx1 = Math.log(R_AXIS[1]);
        const rAt = (gi) => Math.exp(lx0 + ((gi + 0.5) / nx) * (lx1 - lx0));
        const nAt = (gj) => N_AXIS[1] - ((gj + 0.5) / ny) * (N_AXIS[1] - N_AXIS[0]);
        for (let j = 0; j < ny; j++) {
            for (let i = 0; i < nx; i++) {
                const v = evaluate(data, rAt(i), nAt(j), T)[q];
                vals[j * nx + i] = v === null ? NaN : v;
            }
        }
        const canvas = document.createElement("canvas");
        canvas.width = nx; canvas.height = ny;
        const ctx = canvas.getContext("2d");
        const img = ctx.createImageData(nx, ny);
        for (let k = 0; k < nx * ny; k++) {
            const v = vals[k];
            const c = Number.isFinite(v) ? colour(v) : [128, 128, 128];
            img.data.set([c[0], c[1], c[2], 255], 4 * k);
        }
        ctx.putImageData(img, 0, 0);
        const pw = W - m.l - m.r, ph = H - m.t - m.b;
        el("svg:image", {
            href: canvas.toDataURL(), x: m.l, y: m.t, width: pw, height: ph, preserveAspectRatio: "none",
        }, svg);

        // contours at the colour-bar ticks
        const step = niceStep(hi - lo, 6);
        const levels = ticksBetween(lo, hi, step);
        const sx = pw / nx, sy = ph / ny;
        const cg = el("svg:g", { class: "cmw-contour" }, svg);
        for (const level of levels) {
            const segs = contourSegments(vals, nx, ny, level);
            if (!segs.length) continue;
            let d = "";
            for (const s of segs) {
                d += `M${(m.l + (s[0] + 0.5) * sx).toFixed(1)},${(m.t + (s[1] + 0.5) * sy).toFixed(1)}`
                    + `L${(m.l + (s[2] + 0.5) * sx).toFixed(1)},${(m.t + (s[3] + 0.5) * sy).toFixed(1)}`;
            }
            el("svg:path", { d }, cg);
        }

        // data-domain veil and boundary
        const rr = rpRangeAt(data, T);
        const X0 = m.l, X1 = W - m.r, Y0 = m.t, Y1 = H - m.b;
        if (rr) {
            const a = Math.max(X0, x(rr[0])), b = Math.min(X1, x(rr[1]));
            const t = y(nHi), u = y(nLo);
            el("svg:path", {
                class: "cmw-veil", "fill-rule": "evenodd",
                d: `M${X0},${Y0}H${X1}V${Y1}H${X0}Z M${a},${t}V${u}H${b}V${t}Z`,
            }, svg);
            el("svg:rect", { class: "cmw-domain", x: a, y: t, width: Math.max(0, b - a), height: u - t }, svg);
        } else {
            el("svg:rect", { class: "cmw-veil", x: X0, y: Y0, width: X1 - X0, height: Y1 - Y0 }, svg);
        }

        drawAxes(svg, x, y, m, W, H, {
            xTicks: xTicks(W), yTicks: [20, 40, 60, 80, 100, 120], yFmt: String, yLabel: "Speed (N) [rev/s]",
        });

        // hover target under the measured points (their <title>s stay reachable)
        el("svg:rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent", "aria-hidden": "true" }, svg);

        // measured points (at their own coordinates)
        const shown = [];
        if (state.showMeasured) {
            const mg = el("svg:g", { class: "cmw-measured" }, svg);
            for (const p of measured) {
                if (Math.abs(p.T_evap_C - T) > MEASURED_BAND_K) continue;
                if (p.PR < R_AXIS[0] || p.PR > R_AXIS[1]) continue;
                const v = p[q];
                const cx = x(p.PR), cy = y(p.N_rps);
                el("svg:circle", { cx, cy, r: 5.4, class: "cmw-ring" }, mg);
                const c = el("svg:circle", {
                    cx, cy, r: 4.2, fill: Number.isFinite(v) ? rgbCss(colour(v)) : "none", class: "cmw-pt",
                }, mg);
                const mapV = evaluate(data, p.PR, p.N_rps, p.T_evap_C)[q];
                el("svg:title", { text: `${p.id}: measured ${fmt(v)}, map ${fmt(mapV)}` }, c);
                shown.push({ p, cx, cy, v, mapV });
            }
        }

        // η_ov peak (on top of the measured points)
        const px = x(peak.PR), py = y(peak.N_rps);
        const peakVal = evaluate(data, peak.PR, peak.N_rps, T).eta_ov;
        const pg = el("svg:g", { class: "cmw-peak" }, svg);
        el("svg:path", { d: `M${px},${py - 7}L${px + 6},${py}L${px},${py + 7}L${px - 6},${py}Z` }, pg);
        const flip = px > W - m.r - 120;
        const pl = el("svg:text", {
            x: flip ? px - 9 : px + 9, y: py - 13, "text-anchor": flip ? "end" : "start", class: "cmw-halo",
        }, pg);
        svgRich(pl, `η_{ov} peak ${fmt(peakVal)}`);

        // hover
        const onMove = (ev) => {
            const box = svg.getBoundingClientRect();
            const mx = ev.clientX - box.left, my = ev.clientY - box.top;
            let best = null, bd = 9 * 9;
            for (const s of shown) {
                const d = (s.cx - mx) ** 2 + (s.cy - my) ** 2;
                if (d < bd) { bd = d; best = s; }
            }
            if (best) {
                const { p, v, mapV } = best;
                const res = Number.isFinite(v) && Number.isFinite(mapV) ? v - mapV : NaN;
                showTip(
                    `<b>${escapeHtml(p.id)}</b> (measured)<br>`
                    + `T<sub>evap</sub> ${fmt(p.T_evap_C, 2)} °C · T<sub>cond</sub> ${fmt(p.T_cond_C, 2)} °C<br>`
                    + `r<sub>p</sub> ${fmt(p.PR, 2)} · N ${fmt(p.N_rps, 0)} rev/s<br>`
                    + `${rich(SYMBOL[q])}: measured <b>${fmt(v)}</b> · map ${fmt(mapV)}<br>`
                    + `measured − map <b>${fmtSigned(res)}</b>`
                    + `<div class="cmw-tip-note">Map evaluated at the point's own T<sub>evap</sub>.</div>`,
                    ev.clientX, ev.clientY,
                );
                return;
            }
            if (mx < m.l || mx > W - m.r || my < m.t || my > H - m.b) { hideTip(); return; }
            const r = x.invert(mx), N = y.invert(my);
            const e = evaluate(data, r, N, T);
            showTip(
                `r<sub>p</sub> ${fmt(r, 2)} · N ${fmt(N, 0)} rev/s · T<sub>evap</sub> ${fmtT(T)} °C<br>`
                + QUANTITIES.map((k) => `${rich(SYMBOL[k])} ${k === q ? "<b>" + fmt(e[k]) + "</b>" : fmt(e[k])}`).join(" · ")
                + (e.in_domain ? "" : `<div class="cmw-tip-out">Outside the measured data</div>`),
                ev.clientX, ev.clientY,
            );
        };
        svg.addEventListener("pointermove", onMove);
        svg.addEventListener("pointerdown", onMove);
        svg.addEventListener("pointerleave", hideTip);

        // legend
        const dec = decimalsOf(step);
        heatLegend.innerHTML = "";
        const cb = el("span", { class: "cmw-leg-item cmw-colourbar" }, heatLegend);
        const cbLab = el("span", { class: "cmw-leg-label" }, cb);
        cbLab.innerHTML = rich(SYMBOL[q]);
        const CBW = 190, CBH = 30;
        const cbs = el("svg:svg", { width: CBW, height: CBH, viewBox: `0 0 ${CBW} ${CBH}`, "aria-hidden": "true" }, cb);
        const gid = `${uid}-grad`;
        const defs = el("svg:defs", {}, cbs);
        const lg = el("svg:linearGradient", { id: gid, x1: "0", x2: "1", y1: "0", y2: "0" }, defs);
        for (let k = 0; k <= 10; k++) {
            el("svg:stop", { offset: `${k * 10}%`, "stop-color": rgbCss(pal.heat(k / 10)) }, lg);
        }
        const cx0 = 6, cx1 = CBW - 6;
        el("svg:rect", { x: cx0, y: 2, width: cx1 - cx0, height: 9, fill: `url(#${gid})`, class: "cmw-cb-rect" }, cbs);
        const cbx = linear(lo, hi, cx0, cx1);
        const every = levels.length > 6 ? 2 : 1;
        levels.forEach((v, k) => {
            el("svg:line", { x1: cbx(v), x2: cbx(v), y1: 2, y2: 14, class: "cmw-cb-tick" }, cbs);
            if (k % every === 0) el("svg:text", { x: cbx(v), y: 25, "text-anchor": "middle", text: fmt(v, dec) }, cbs);
        });

        const legItem = (html, swatch) => {
            const s = el("span", { class: "cmw-leg-item" }, heatLegend);
            s.innerHTML = swatch + `<span>${html}</span>`;
        };
        legItem("Contours at the colour-bar ticks", '<svg width="18" height="10" aria-hidden="true"><line x1="0" y1="5" x2="18" y2="5" class="cmw-sw-contour"/></svg>');
        legItem(
            rr
                ? `Data domain: r<sub>p</sub> ${fmt(rr[0], 2)}–${fmt(rr[1], 2)}, N ${nLo}–${nHi} rev/s (veiled outside)`
                : "No measured data at this T<sub>evap</sub>",
            '<svg width="18" height="10" aria-hidden="true"><line x1="0" y1="5" x2="18" y2="5" class="cmw-sw-domain"/></svg>',
        );
        legItem("η<sub>ov</sub> peak", '<svg width="12" height="12" aria-hidden="true"><path d="M6,0L12,6L6,12L0,6Z" class="cmw-sw-peak"/></svg>');
        if (state.showMeasured) {
            legItem(
                `Measured, T<sub>evap</sub> within ±${MEASURED_BAND_K} K (${shown.length}); fill = measured value`,
                '<svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4.5" class="cmw-sw-point"/></svg>',
            );
        }
        if ((q === "eta_v" || q === "eta_is") && hi > 1) {
            const note = el("span", { class: "cmw-leg-item cmw-leg-note" }, heatLegend);
            note.innerHTML = "Values above 1 are shown as they are; the cycle sets them to 1.";
        }
    }

    // ------------------------------------------------------ small multiples
    const rSamples = (() => {
        const out = [];
        const n = 180;
        for (let k = 0; k <= n; k++) out.push(R_AXIS[0] * (R_AXIS[1] / R_AXIS[0]) ** (k / n));
        return out;
    })();

    function renderLines() {
        const pal = palette();
        const T = state.T;
        const rr = rpRangeAt(data, T);
        lineTitle.innerHTML = `Map values vs r<sub>p</sub> at T<sub>evap</sub> = ${fmtT(T)} °C, one line per speed`;

        lineLegend.innerHTML = "";
        const head = el("span", { class: "cmw-leg-item" }, lineLegend);
        head.innerHTML = "<span>N [rev/s]</span>";
        SPEEDS.forEach((N, i) => {
            const s = el("span", { class: "cmw-leg-item" }, lineLegend);
            s.innerHTML = `<svg width="26" height="10" aria-hidden="true">`
                + `<line x1="0" y1="5" x2="12" y2="5" stroke="${pal.violet[i]}" stroke-width="3" stroke-linecap="round"/>`
                + `<line x1="14" y1="5" x2="26" y2="5" stroke="${pal.teal[i]}" stroke-width="3" stroke-linecap="round"/></svg>`
                + `<span>${N}</span>`;
        });
        const kinds = el("span", { class: "cmw-leg-item" }, lineLegend);
        kinds.innerHTML = `<span>violet: f(r<sub>p</sub>, N, T<sub>evap</sub>) · teal: f(r<sub>p</sub>, N)</span>`;
        const dash = el("span", { class: "cmw-leg-item" }, lineLegend);
        dash.innerHTML = '<svg width="22" height="10" aria-hidden="true"><line x1="0" y1="5" x2="22" y2="5" class="cmw-sw-extrap"/></svg>'
            + "<span>dashed: outside the measured data</span>";

        for (const q of PANEL_ORDER) {
            const P = panels[q];
            P.plot.innerHTML = "";
            const W = Math.max(240, P.plot.clientWidth || 300);
            const H = 200;
            const m = { l: 44, r: 10, t: 8, b: 38 };
            const x = logScale(R_AXIS[0], R_AXIS[1], m.l, W - m.r);
            let [lo, hi] = ranges[q];
            if (q === "eta_v" || q === "eta_is") hi = Math.max(hi, 1);
            const pad = (hi - lo) * 0.08;
            lo -= pad; hi += pad;
            const step = niceStep(hi - lo, 4);
            const yTicks = ticksBetween(lo, hi, step);
            const y = linear(lo, hi, H - m.b, m.t);
            const svg = el("svg:svg", {
                width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
                "aria-label": `${NAME[q]} versus pressure ratio at T_evap ${T} degrees Celsius for speeds 30 to 110 rev/s`,
            }, P.plot);
            drawAxes(svg, x, y, m, W, H, { xTicks: xTicks(W), yTicks, yFmt: (v) => fmt(v, decimalsOf(step)), yLabel: null });

            const clipId = `${uid}-clip-${q}`;
            el("svg:rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b },
                el("svg:clipPath", { id: clipId }, el("svg:defs", {}, svg)));
            const g = el("svg:g", { "clip-path": `url(#${clipId})` }, svg);

            if (q === "eta_v" || q === "eta_is") {
                el("svg:line", { class: "cmw-ref", x1: m.l, x2: W - m.r, y1: y(1), y2: y(1) }, g);
            }
            if (rr) {
                for (const v of rr) {
                    if (v > R_AXIS[0] && v < R_AXIS[1]) {
                        el("svg:line", { class: "cmw-domain-v", x1: x(v), x2: x(v), y1: m.t, y2: H - m.b }, g);
                    }
                }
            }
            const ramp = q === "f_loss" ? pal.teal : pal.violet;
            const clampY = (v) => Math.max(m.t - 40, Math.min(H - m.b + 40, y(v)));
            const rs = rr ? [...rSamples, rr[0], rr[1]].sort((a, b) => a - b) : rSamples;
            SPEEDS.forEach((N, i) => {
                let dIn = "", dOut = "";
                let prevIn = null, prev = null;
                for (const r of rs) {
                    const v = evaluate(data, r, N, T)[q];
                    if (v === null) { prev = null; continue; }
                    const inside = rr ? r >= rr[0] && r <= rr[1] : false;
                    const pt = `${x(r).toFixed(1)},${clampY(v).toFixed(1)}`;
                    // Each segment belongs to the in-domain path only if both ends are inside.
                    if (prev === null) {
                        if (inside) dIn += `M${pt}`; else dOut += `M${pt}`;
                    } else {
                        const segIn = inside && prevIn;
                        if (segIn) dIn += (dIn.endsWith(prev) ? "" : `M${prev}`) + `L${pt}`;
                        else dOut += (dOut.endsWith(prev) ? "" : `M${prev}`) + `L${pt}`;
                    }
                    prev = pt; prevIn = inside;
                }
                if (dOut) el("svg:path", { d: dOut, class: "cmw-line cmw-line-out", stroke: ramp[i] }, g);
                if (dIn) el("svg:path", { d: dIn, class: "cmw-line", stroke: ramp[i] }, g);
            });
            P.cross = el("svg:g", { class: "cmw-cross", style: "display:none" }, svg);
            Object.assign(P, { svg, x, y, m, W, H, ramp });

            const onMove = (ev) => {
                const box = svg.getBoundingClientRect();
                const mx = ev.clientX - box.left;
                if (mx < m.l || mx > W - m.r) { clearCross(); hideTip(); return; }
                state.hoverR = x.invert(mx);
                drawCross();
                const r = state.hoverR;
                const inside = rr && r >= rr[0] && r <= rr[1];
                const rows = SPEEDS.map((N, i) => {
                    const v = evaluate(data, r, N, T)[q];
                    return `<span class="cmw-tip-sw" style="background:${ramp[i]}"></span>N ${N}: <b>${fmt(v)}</b>`;
                }).join("<br>");
                showTip(
                    `${rich(SYMBOL[q])} at r<sub>p</sub> ${fmt(r, 2)}, T<sub>evap</sub> ${fmtT(T)} °C<br>${rows}`
                    + (inside ? "" : `<div class="cmw-tip-out">Outside the measured data</div>`),
                    ev.clientX, ev.clientY,
                );
            };
            svg.addEventListener("pointermove", onMove);
            svg.addEventListener("pointerdown", onMove);
            svg.addEventListener("pointerleave", () => { clearCross(); hideTip(); });
        }
        if (state.hoverR !== null) drawCross();
    }

    function clearCross() {
        state.hoverR = null;
        for (const q of PANEL_ORDER) if (panels[q].cross) panels[q].cross.style.display = "none";
    }
    function drawCross() {
        const r = state.hoverR;
        for (const q of PANEL_ORDER) {
            const P = panels[q];
            if (!P.cross) continue;
            P.cross.innerHTML = "";
            P.cross.style.display = "";
            const cx = P.x(r);
            el("svg:line", { x1: cx, x2: cx, y1: P.m.t, y2: P.H - P.m.b, class: "cmw-cross-line" }, P.cross);
            SPEEDS.forEach((N, i) => {
                const v = evaluate(data, r, N, state.T)[q];
                if (v === null) return;
                const cy = P.y(v);
                if (cy < P.m.t || cy > P.H - P.m.b) return;
                el("svg:circle", { cx, cy, r: 3.2, fill: P.ramp[i], class: "cmw-cross-dot" }, P.cross);
            });
        }
    }

    // ------------------------------------------------------ readout, table
    function renderReadout() {
        const T = state.T;
        const rr = rpRangeAt(data, T);
        const pv = evaluate(data, peak.PR, peak.N_rps, T);
        const dom = rr
            ? `r<sub>p</sub> ${fmt(rr[0], 2)}–${fmt(rr[1], 2)} at T<sub>evap</sub> ${fmtT(T)} °C, N ${nLo}–${nHi} rev/s`
            : `no measured data at T<sub>evap</sub> ${fmtT(T)} °C`;
        status.innerHTML = `<span>Coefficients <code>${escapeHtml(data.coefficient_version)}</code></span>`
            + `<span>Data domain: ${dom}</span>`
            + `<span>η<sub>ov</sub> peak <b>${fmt(pv.eta_ov)}</b> at r<sub>p</sub> ${fmt(peak.PR, 2)}, N ${fmt(peak.N_rps, 0)} rev/s`
            + `${pv.in_domain ? "" : " (outside the data at this T<sub>evap</sub>)"}</span>`;
    }

    function renderTable() {
        const T = state.T;
        const rr = rpRangeAt(data, T);
        summary.innerHTML = `Map values at T<sub>evap</sub> = ${fmtT(T)} °C (table)`;
        const rps = rr
            ? [rr[0], Math.sqrt(rr[0] * rr[1]), rr[1]]
            : [2, 4, 8];
        let html = `<table class="cmw-table"><caption>Map values at T<sub>evap</sub> = ${fmtT(T)} °C. `
            + `r<sub>p</sub> rows: low edge, geometric middle and high edge of the data domain at this T<sub>evap</sub>, and the η<sub>ov</sub> peak.</caption>`
            + "<thead><tr><th scope=\"col\">r<sub>p</sub></th><th scope=\"col\">N [rev/s]</th>"
            + QUANTITIES.map((q) => `<th scope="col">${rich(SYMBOL[q])}</th>`).join("")
            + "<th scope=\"col\">In data domain</th></tr></thead><tbody>";
        const rows = [];
        for (const r of rps) for (const N of [30, 70, 110]) rows.push([r, N]);
        rows.push([peak.PR, peak.N_rps]);
        for (const [r, N] of rows) {
            const e = evaluate(data, r, N, T);
            html += `<tr><td>${fmt(r, 3)}</td><td>${fmt(N, 0)}</td>`
                + QUANTITIES.map((q) => `<td>${fmt(e[q], 4)}</td>`).join("")
                + `<td>${e.in_domain ? "yes" : "no"}</td></tr>`;
        }
        html += "</tbody></table>";
        tableWrap.innerHTML = html;
    }

    function renderAll() {
        tOut.textContent = `${fmtT(state.T)} °C`;
        tInput.setAttribute("aria-valuetext", `${state.T} degrees Celsius`);
        renderReadout();
        renderHeat();
        renderLines();
        renderTable();
    }

    tInput.addEventListener("input", () => {
        state.T = Number(tInput.value);
        hideTip();
        renderAll();
    });

    // re-render on width or theme changes
    let lastW = root.clientWidth;
    let raf = 0;
    const schedule = () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => { hideTip(); renderHeat(); renderLines(); });
    };
    if (window.ResizeObserver) {
        new ResizeObserver(() => {
            const w = root.clientWidth;
            if (w !== lastW) { lastW = w; schedule(); }
        }).observe(root);
    }
    const themeChanged = () => {
        const d = isDarkPage();
        if (d !== dark) { dark = d; schedule(); }
    };
    new MutationObserver(themeChanged).observe(document.documentElement, {
        attributes: true, attributeFilter: ["class", "data-color-mode", "data-theme"],
    });
    if (window.matchMedia) {
        const mq = window.matchMedia("(prefers-color-scheme: dark)");
        if (mq.addEventListener) mq.addEventListener("change", themeChanged);
    }

    renderAll();
    return { state, rerender: renderAll };
}
