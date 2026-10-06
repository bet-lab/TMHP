/**
 * ④ Filterable, sortable validation table.
 *
 * Reads /_static/data/validation-points.json, which the parity harness
 * writes for every unit in validation/catalogs/. Columns: unit, mode,
 * source / sink, Q, COP_target / COP_predicted, and the signed percent
 * error on COP. (The model is solved *for* the catalogue duty, so the
 * semantically meaningful comparison is COP, not capacity.)
 *
 * Points the model could not evaluate are kept and shown with their
 * failure reason rather than dropped — a validation table that silently
 * omits its hard cases is not telling you the truth.
 *
 * On successful hydration the static dropdown is hidden via the
 * `hidden-by-js` class so the JS-on view shows the widget alone. The
 * hide is deferred until after the fetch resolves; if the JSON load
 * fails the static dropdown remains visible as the fallback.
 */
(function () {
  "use strict";
  const mount = document.getElementById("validation-table-mount");
  if (!mount) return;
  if (!window.tmhpPlot) {
    console.warn("validation-table: tmhpPlot helpers missing — load _plot-common.js first");
    return;
  }
  const { loadJson, staticDir } = window.tmhpPlot;

  mount.classList.add("validation-table");
  mount.innerHTML = `
    <div class="vt-chrome">
      <input class="vt-filter" placeholder="Filter (try 'RXM35', 'heating' or '45')…">
      <div class="vt-chips"></div>
    </div>
    <table class="vt-table">
      <thead><tr>
        <th data-sort="unit">Unit</th>
        <th data-sort="refrigerant">Ref.</th>
        <th data-sort="nominal_kw">Rated [kW]</th>
        <th data-sort="mode">Mode</th>
        <th data-sort="t_source_c">T_src [°C]</th>
        <th data-sort="t_sink_c">T_sink [°C]</th>
        <th data-sort="q_cat_kw">Q [kW]</th>
        <th data-sort="cop_cat">COP_cat</th>
        <th data-sort="cop_mod">COP_pred</th>
        <th data-sort="delta_pct">Δ [%]</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  `;
  const filterEl = mount.querySelector(".vt-filter");
  const chipsEl = mount.querySelector(".vt-chips");
  const tbody = mount.querySelector("tbody");
  let rows = [];
  let sortKey = "unit";
  let sortAsc = true;
  let chipFilter = null;

  function deltaPct(r) {
    if (!r.usable || r.cop_mod == null || !r.cop_cat) return NaN;
    return ((r.cop_mod - r.cop_cat) / r.cop_cat) * 100;
  }

  function num(v, digits) {
    return v == null || Number.isNaN(v) ? "—" : v.toFixed(digits);
  }

  function render() {
    const q = filterEl.value.trim().toLowerCase();
    const visible = rows.filter(r => {
      const blob = `${r.unit} ${r.manufacturer} ${r.refrigerant} ${r.mode} ${r.nominal_kw} ${r.t_source_c} ${r.t_sink_c} ${r.q_cat_kw} ${r.cop_cat} ${r.cop_mod} ${r.failure_reason}`.toLowerCase();
      const hitText = !q || blob.includes(q);
      const hitChip = !chipFilter || r.refrigerant === chipFilter;
      return hitText && hitChip;
    });

    visible.sort((a, b) => {
      let av = sortKey === "delta_pct" ? deltaPct(a) : a[sortKey];
      let bv = sortKey === "delta_pct" ? deltaPct(b) : b[sortKey];
      // Unevaluated points sort to the end whichever way the column goes,
      // so they never displace real results from the top of the table.
      const aNaN = av == null || (typeof av === "number" && Number.isNaN(av));
      const bNaN = bv == null || (typeof bv === "number" && Number.isNaN(bv));
      if (aNaN && bNaN) return 0;
      if (aNaN) return 1;
      if (bNaN) return -1;
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });

    tbody.innerHTML = visible.map(r => {
      const d = deltaPct(r);
      const ok = Number.isFinite(d);
      const cls = !ok ? "warn" : Math.abs(d) < 5 ? "ok" : "warn";
      const delta = ok ? `${d >= 0 ? "+" : ""}${d.toFixed(1)}` : r.failure_reason;
      return `<tr data-case="${r.slug}-${r.case_id}" class="vt-row${ok ? "" : " vt-unevaluated"}">
        <td>${r.unit}</td>
        <td>${r.refrigerant}</td>
        <td>${num(r.nominal_kw, 1)}</td>
        <td>${r.mode}</td>
        <td>${num(r.t_source_c, 0)}</td>
        <td>${num(r.t_sink_c, 0)}</td>
        <td>${num(r.q_cat_kw, 2)}</td>
        <td>${num(r.cop_cat, 2)}</td>
        <td>${num(r.cop_mod, 2)}</td>
        <td class="${cls}">${delta}</td>
      </tr>`;
    }).join("");
  }

  (async () => {
    try {
      rows = await loadJson(`${staticDir()}/data/validation-points.json`);
    } catch (err) {
      console.error("validation-table: failed to load JSON", err);
      return;  // Leave the static dropdown visible as the fallback.
    }

    // JSON loaded successfully — only now hide the static dropdown.
    const staticTable = document.querySelector(".validation-table-static");
    if (staticTable) {
      const dropdown = staticTable.closest("details.sd-dropdown");
      (dropdown || staticTable).classList.add("hidden-by-js");
    }

    const refs = [...new Set(rows.map(r => r.refrigerant))];
    chipsEl.innerHTML = refs.map(r =>
      `<button class="vt-chip" data-ref="${r}">${r}</button>`).join("");
    chipsEl.addEventListener("click", e => {
      const b = e.target.closest(".vt-chip");
      if (!b) return;
      const r = b.dataset.ref;
      chipFilter = chipFilter === r ? null : r;
      chipsEl.querySelectorAll(".vt-chip").forEach(c =>
        c.classList.toggle("active", c.dataset.ref === chipFilter));
      render();
    });

    filterEl.addEventListener("input", render);

    mount.querySelectorAll("th[data-sort]").forEach(th => {
      th.addEventListener("click", () => {
        const k = th.dataset.sort;
        if (sortKey === k) sortAsc = !sortAsc;
        else { sortKey = k; sortAsc = true; }
        render();
      });
    });

    render();
  })();
})();
