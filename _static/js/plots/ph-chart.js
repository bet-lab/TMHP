/**
 * ① Live P–h chart with refrigerant selector.
 *
 * Reads /docs/source/_static/data/refrigerants/<REF>.json (built by
 * scripts/data/gen_refrigerant_data.py) and renders:
 *   - saturation dome (closed area on a log-P / h axis)
 *   - the four cycle state points (1: comp in, 2: comp out, 3: cond out,
 *     4: throttle out) with connecting line segments
 *
 * The COP at the chosen (T_evap, T_cond) is rendered inline at the
 * top-left of the plot area (no side panel — keeps the plot full-width).
 * Sliders interpolate the cycle_grid bilinearly — no runtime CoolProp.
 *
 * A clipPath constrains every drawn shape to the inner plot rectangle
 * so the Catmull-Rom curve on the saturation dome cannot overshoot the
 * axes (which would otherwise paint the fill below the x-axis).
 */
(function () {
  "use strict";
  const mount = document.getElementById("ph-chart-mount");
  if (!mount) return;
  if (!window.tmhpPlot) {
    console.warn("ph-chart: tmhpPlot helpers missing — load _plot-common.js before ph-chart.js");
    return;
  }
  const { tokens, loadJson, bilinear, staticDir, domeLookup } = window.tmhpPlot;

  const REFS = (mount.dataset.refrigerants || "R32").split(",");
  const DEFAULT_REF = mount.dataset.default || REFS[0];

  mount.classList.add("tmhp-plot-mount", "ph-chart");
  mount.innerHTML = `
    <div class="ph-chrome">
      <label>Refrigerant
        <select class="ph-ref">${REFS.map(r => `<option>${r}</option>`).join("")}</select>
      </label>
      <label>T_evap <output class="ph-t-evap-out"></output>
        <input type="range" class="ph-t-evap" min="-20" max="20" step="1">
      </label>
      <label>T_cond <output class="ph-t-cond-out"></output>
        <input type="range" class="ph-t-cond" min="25" max="65" step="1">
      </label>
    </div>
    <div class="ph-canvas-wrap">
      <svg class="ph-canvas" viewBox="0 0 720 420" preserveAspectRatio="xMidYMid meet"></svg>
    </div>
  `;
  const sel = mount.querySelector(".ph-ref");
  const sliderEvap = mount.querySelector(".ph-t-evap");
  const sliderCond = mount.querySelector(".ph-t-cond");
  const outEvap = mount.querySelector(".ph-t-evap-out");
  const outCond = mount.querySelector(".ph-t-cond-out");
  const svg = mount.querySelector("svg.ph-canvas");
  sel.value = DEFAULT_REF;
  sliderEvap.value = -5;
  sliderCond.value = 45;

  let payload = null;
  let loadToken = 0;
  async function load(ref) {
    const token = ++loadToken;
    const data = await loadJson(`${staticDir()}/data/refrigerants/${ref}.json`);
    if (token !== loadToken) return;
    payload = data;
    render();
  }

  function render() {
    if (!payload) return;
    const t = tokens();
    const dome = payload.saturation_dome;
    const grid = payload.cycle_grid;

    const margin = { top: 20, right: 20, bottom: 50, left: 60 };
    const W = 720, H = 420;
    const innerW = W - margin.left - margin.right;
    const innerH = H - margin.top - margin.bottom;

    // Compute cycle h-points up-front so the x-axis can include h2 (the
    // compressor outlet, which sits well to the right of the saturation
    // dome's vapor side for typical R32 cycles). Without this the
    // chart's right edge clips the cycle's upper horizontal leg.
    const te0 = +sliderEvap.value, tc0 = +sliderCond.value;
    const xs0 = grid.t_evap_c, ys0 = grid.t_cond_c;
    function P_sat_pre(T_target, side) {
      for (let i = 0; i < dome.length - 1; i++) {
        const a = dome[i], b = dome[i + 1];
        if (T_target >= a.T_c && T_target <= b.T_c) {
          const f = (T_target - a.T_c) / (b.T_c - a.T_c);
          return a.P_kpa + f * (b.P_kpa - a.P_kpa);
        }
      }
      return side === "evap" ? dome[0].P_kpa : dome[dome.length - 1].P_kpa;
    }
    const P_evap_pre = P_sat_pre(te0, "evap");
    const P_cond_pre = P_sat_pre(tc0, "cond");
    const h1_pre = domeLookup(dome, P_evap_pre, 1);
    const h3_pre = domeLookup(dome, P_cond_pre, 0);
    const h4_pre = h3_pre;
    const cop_pre = bilinear(xs0, ys0, grid.cop, te0, tc0);
    const h2_pre = (cop_pre && cop_pre > 1.05)
      ? h3_pre + (h1_pre - h4_pre) * cop_pre / (cop_pre - 1)
      : h1_pre + 30;

    const allH = dome.flatMap(d => [d.h_liq_kjkg, d.h_vap_kjkg]);
    const xMin = Math.min(...allH) - 20;
    const xMax = Math.max(Math.max(...allH), h2_pre) + 30;
    const xExtent = [xMin, xMax];
    const pMin = Math.max(50, Math.min(...dome.map(d => d.P_kpa)) * 0.3);
    const pMax = Math.max(...dome.map(d => d.P_kpa)) * 1.2;

    const x  = d3.scaleLinear().domain(xExtent).range([0, innerW]);
    const yP = d3.scaleLog().domain([pMin, pMax]).range([innerH, 0]);

    svg.innerHTML = "";
    const svgSel = d3.select(svg);

    // clipPath keeps the dome fill and cycle path inside the plot box,
    // so the Catmull-Rom interpolation can't paint below the x-axis.
    const clipId = "ph-clip-" + Math.random().toString(36).slice(2, 8);
    svgSel.append("defs").append("clipPath").attr("id", clipId)
      .append("rect").attr("width", innerW).attr("height", innerH);

    const root = svgSel
      .append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    // Axes
    root.append("g").attr("class", "axis")
        .attr("transform", `translate(0,${innerH})`)
        .call(d3.axisBottom(x).ticks(8).tickFormat(d3.format(",.0f")))
      .append("text").attr("x", innerW / 2).attr("y", 40)
        .attr("text-anchor", "middle").attr("fill", t.muted)
        .text("Enthalpy h [kJ/kg]");
    root.append("g").attr("class", "axis")
        .call(d3.axisLeft(yP).ticks(6, ".0f"))
      .append("text")
        .attr("transform", "rotate(-90)").attr("x", -innerH / 2).attr("y", -42)
        .attr("text-anchor", "middle").attr("fill", t.muted)
        .text("Pressure P [kPa, log]");

    // Everything past this point sits inside the clip rectangle.
    const clipped = root.append("g").attr("clip-path", `url(#${clipId})`);

    // Saturation dome: liq side + vap side, joined.
    const domePath = d3.line()
      .x(d => x(d.h)).y(d => yP(d.P))
      .curve(d3.curveCatmullRom);
    const liq = dome.map(d => ({ h: d.h_liq_kjkg, P: d.P_kpa }));
    const vap = dome.map(d => ({ h: d.h_vap_kjkg, P: d.P_kpa })).reverse();
    clipped.append("path")
      .attr("d", domePath([...liq, ...vap]))
      .attr("fill", t.accent3).attr("fill-opacity", 0.5)
      .attr("stroke", t.accent11).attr("stroke-width", 1.2);

    // Cycle 4 points at the chosen (T_evap, T_cond).
    const te = +sliderEvap.value, tc = +sliderCond.value;
    outEvap.textContent = `${te} °C`;
    outCond.textContent = `${tc} °C`;
    const xs = grid.t_evap_c, ys = grid.t_cond_c;

    function P_sat(T_target, side) {
      const tField = "T_c", pField = "P_kpa";
      const arr = dome;
      for (let i = 0; i < arr.length - 1; i++) {
        const a = arr[i], b = arr[i + 1];
        if (T_target >= a[tField] && T_target <= b[tField]) {
          const f = (T_target - a[tField]) / (b[tField] - a[tField]);
          return a[pField] + f * (b[pField] - a[pField]);
        }
      }
      return side === "evap" ? arr[0][pField] : arr[arr.length - 1][pField];
    }
    const P_evap = P_sat(te, "evap"), P_cond = P_sat(tc, "cond");

    const h1 = domeLookup(dome, P_evap, 1);
    const h3 = domeLookup(dome, P_cond, 0);
    const h4 = h3;
    const cop = bilinear(xs, ys, grid.cop, te, tc);

    const h2 = (cop && cop > 1.05)
      ? h3 + (h1 - h4) * cop / (cop - 1)
      : h1 + 30;   // visual fallback so the cycle still draws sensibly

    const cyclePoints = [
      { h: h1, P: P_evap, label: "1" },
      { h: h2, P: P_cond, label: "2" },
      { h: h3, P: P_cond, label: "3" },
      { h: h4, P: P_evap, label: "4" },
    ];
    const cycleClosed = [...cyclePoints, cyclePoints[0]];
    const cycleLine = d3.line().x(d => x(d.h)).y(d => yP(d.P));
    clipped.append("path")
      .attr("d", cycleLine(cycleClosed))
      .attr("fill", "none").attr("stroke", t.accent).attr("stroke-width", 1.8);

    clipped.selectAll(".pt").data(cyclePoints).enter().append("g")
      .attr("class", "pt")
      .attr("transform", d => `translate(${x(d.h)},${yP(d.P)})`)
      .call(g => {
        g.append("circle").attr("r", 4).attr("fill", t.accent);
        g.append("text").attr("x", 6).attr("y", -6).attr("fill", t.ink)
          .attr("font-size", 12).attr("font-weight", 600).text(d => d.label);
      });

    // Inline COP readout — top-left inside the plot, no background.
    const copText = cop ? cop.toFixed(2) : "—";
    root.append("text").attr("class", "ph-cop-inline")
      .attr("x", 12).attr("y", 16)
      .attr("fill", t.accent11)
      .attr("font-size", 18).attr("font-weight", 700)
      .style("font-variant-numeric", "tabular-nums")
      .text(`COP ${copText}`);
  }

  sel.addEventListener("change", () => load(sel.value));
  sliderEvap.addEventListener("input", render);
  sliderCond.addEventListener("input", render);

  load(DEFAULT_REF);
})();
