/**
 * ④ Filterable, sortable validation table.
 *
 * Reads /_static/data/validation-points.json. Columns mirror the static
 * rst table inside the dropdown below: case id, source / sink, Q_cond,
 * COP_target / COP_predicted, and the percent error on COP. (The model
 * is solved *for* the catalogue Q_cond, so q_mod_kw equals q_cat_kw by
 * construction — the semantically meaningful comparison is COP.)
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
      <input class="vt-filter" placeholder="Filter (try '45' or 'R32')…">
      <div class="vt-chips"></div>
    </div>
    <table class="vt-table">
      <thead><tr>
        <th data-sort="case_id">Case</th>
        <th data-sort="refrigerant">Ref.</th>
        <th data-sort="t_source_c">T_src [°C]</th>
        <th data-sort="t_sink_c">T_sink [°C]</th>
        <th data-sort="q_cat_kw">Q_cond [kW]</th>
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
  let sortKey = "case_id";
  let sortAsc = true;
  let chipFilter = null;

  function deltaPct(r) {
    return ((r.cop_mod - r.cop_cat) / r.cop_cat) * 100;
  }

  function render() {
    const q = filterEl.value.trim().toLowerCase();
    const visible = rows.filter(r => {
      const blob = `${r.case_id} ${r.refrigerant} ${r.t_source_c} ${r.t_sink_c} ${r.q_cat_kw} ${r.cop_cat} ${r.cop_mod}`.toLowerCase();
      const hitText = !q || blob.includes(q);
      const hitChip = !chipFilter || r.refrigerant === chipFilter;
      return hitText && hitChip;
    });

    visible.sort((a, b) => {
      const av = sortKey === "delta_pct" ? deltaPct(a) : a[sortKey];
      const bv = sortKey === "delta_pct" ? deltaPct(b) : b[sortKey];
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });

    tbody.innerHTML = visible.map(r => {
      const d = deltaPct(r);
      const cls = Math.abs(d) < 5 ? "ok" : "warn";
      const sign = d >= 0 ? "+" : "";
      return `<tr data-case="${r.case_id}" class="vt-row">
        <td>${r.case_id}</td>
        <td>${r.refrigerant}</td>
        <td>${r.t_source_c}</td>
        <td>${r.t_sink_c}</td>
        <td>${r.q_cat_kw.toFixed(2)}</td>
        <td>${r.cop_cat.toFixed(2)}</td>
        <td>${r.cop_mod.toFixed(2)}</td>
        <td class="${cls}">${sign}${d.toFixed(1)}</td>
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
