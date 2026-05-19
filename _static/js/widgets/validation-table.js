/**
 * ④ Filterable, sortable validation table.
 *
 * Reads the same /_static/data/validation-points.json as the parity
 * plot. On successful hydration, the existing rst-rendered table is
 * hidden via CSS class (and remains in the DOM as the JS-disabled
 * fallback). Row click → 'tmhp:table-selected' event (consumed by ②).
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

  // Hide the static rst table sibling once we're alive.
  const staticTable = document.querySelector(".validation-table-static");
  if (staticTable) staticTable.classList.add("hidden-by-js");

  mount.classList.add("validation-table");
  mount.innerHTML = `
    <div class="vt-chrome">
      <input class="vt-filter" placeholder="Filter (try '7' or 'R32')…">
      <div class="vt-chips"></div>
    </div>
    <table class="vt-table">
      <thead><tr>
        <th data-sort="case_id">Case</th>
        <th data-sort="refrigerant">Ref.</th>
        <th data-sort="t_source_c">T_src [°C]</th>
        <th data-sort="t_sink_c">T_sink [°C]</th>
        <th data-sort="q_cat_kw">Q_cat [kW]</th>
        <th data-sort="q_mod_kw">Q_mod [kW]</th>
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
  let selectedId = null;

  function deltaPct(r) {
    return ((r.q_mod_kw - r.q_cat_kw) / r.q_cat_kw) * 100;
  }

  function render() {
    const q = filterEl.value.trim().toLowerCase();
    let visible = rows.filter(r => {
      const blob = `${r.case_id} ${r.refrigerant} ${r.t_source_c} ${r.t_sink_c} ${r.q_cat_kw} ${r.q_mod_kw}`.toLowerCase();
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
      const sel = r.case_id === selectedId ? " is-selected" : "";
      return `<tr data-case="${r.case_id}" class="vt-row${sel}">
        <td>${r.case_id}</td>
        <td>${r.refrigerant}</td>
        <td>${r.t_source_c}</td>
        <td>${r.t_sink_c}</td>
        <td>${r.q_cat_kw.toFixed(2)}</td>
        <td>${r.q_mod_kw.toFixed(2)}</td>
        <td class="${cls}">${d >= 0 ? "+" : ""}${d.toFixed(1)}</td>
      </tr>`;
    }).join("");
  }

  (async () => {
    rows = await loadJson(`${staticDir()}/data/validation-points.json`);

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

    tbody.addEventListener("click", e => {
      const tr = e.target.closest("tr.vt-row");
      if (!tr) return;
      selectedId = +tr.dataset.case;
      render();
      window.dispatchEvent(new CustomEvent("tmhp:table-selected",
        { detail: { case_id: selectedId } }));
    });

    window.addEventListener("tmhp:parity-selected", e => {
      selectedId = e.detail.case_id;
      render();
      const tr = tbody.querySelector(`tr[data-case="${selectedId}"]`);
      if (tr) tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });

    render();
  })();
})();
