/**
 * @deprecated This file is superseded by the integrated cycle_widget.html
 * widget (docs/source/_static/html/cycle_widget.html).  Kept for reference.
 *
 * Shared helpers for TMHP interactive plots.
 *
 * Reads Radix-DNA CSS variables off :root so every plot picks up the
 * same ink, accent, hairline, and muted-text tokens the rest of the
 * page uses — keeping the visual language consistent across the docs.
 */
(function (root) {
  "use strict";

  function tokens() {
    const cs = getComputedStyle(document.documentElement);
    return {
      accent:    cs.getPropertyValue("--rx-accent-9").trim()  || "#3e63dd",
      accent11:  cs.getPropertyValue("--rx-accent-11").trim() || "#3a5bc7",
      accent3:   cs.getPropertyValue("--rx-accent-3").trim()  || "#edf2fe",
      ink:       cs.getPropertyValue("--rx-ink").trim()       || "#202020",
      muted:     cs.getPropertyValue("--rx-ink-muted").trim() || "#646464",
      hairline:  cs.getPropertyValue("--rx-hairline").trim()  || "rgba(0,0,0,0.15)",
      amber:     cs.getPropertyValue("--rx-amber-9").trim()   || "#ffb224",
      green:     cs.getPropertyValue("--rx-green-9").trim()   || "#30a46c",
      red:       cs.getPropertyValue("--rx-red-9").trim()     || "#e5484d",
    };
  }

  async function loadJson(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(`fetch ${url} → ${r.status}`);
    return r.json();
  }

  /** Bilinear interpolation on a regular grid.
   *  @param {number[]} xs sorted ascending
   *  @param {number[]} ys sorted ascending
   *  @param {(number|null)[][]} z z[i][j] aligned to xs[i], ys[j]
   */
  function bilinear(xs, ys, z, x, y) {
    function bracket(arr, v) {
      if (v <= arr[0]) return [0, 0, 0];
      if (v >= arr[arr.length - 1]) return [arr.length - 1, arr.length - 1, 1];
      for (let i = 0; i < arr.length - 1; i++) {
        if (v >= arr[i] && v <= arr[i + 1]) {
          return [i, i + 1, (v - arr[i]) / (arr[i + 1] - arr[i])];
        }
      }
      return [arr.length - 1, arr.length - 1, 1];
    }
    const [i0, i1, tx] = bracket(xs, x);
    const [j0, j1, ty] = bracket(ys, y);
    const z00 = z[i0][j0], z01 = z[i0][j1], z10 = z[i1][j0], z11 = z[i1][j1];
    if ([z00, z01, z10, z11].some(v => v === null)) return null;
    return (z00 * (1 - tx) * (1 - ty)
          + z10 * tx       * (1 - ty)
          + z01 * (1 - tx) * ty
          + z11 * tx       * ty);
  }

  /** Linear-interpolated dome lookup.
   *  Returns h_liq (q < 0.5) or h_vap (q >= 0.5) at pressure `P_kpa`, by
   *  bracketing the two adjacent dome rows in pressure and lerping.
   *  Falls back to the nearest end if `P_kpa` is outside the dome range.
   */
  function domeLookup(dome, P_kpa, q) {
    const field = q < 0.5 ? "h_liq_kjkg" : "h_vap_kjkg";
    for (let i = 0; i < dome.length - 1; i++) {
      const a = dome[i], b = dome[i + 1];
      if (P_kpa >= a.P_kpa && P_kpa <= b.P_kpa) {
        const f = (P_kpa - a.P_kpa) / (b.P_kpa - a.P_kpa);
        return a[field] + f * (b[field] - a[field]);
      }
    }
    return P_kpa <= dome[0].P_kpa ? dome[0][field] : dome[dome.length - 1][field];
  }

  function staticDir() {
    const scripts = document.getElementsByTagName("script");
    for (let script of scripts) {
      if (script.src && script.src.includes("_static/js/plots/_plot-common.js")) {
        const idx = script.src.indexOf("_static/js/plots/_plot-common.js");
        return script.src.substring(0, idx + 7);
      }
    }
    return "_static";
  }

  root.tmhpPlot = { tokens, loadJson, bilinear, domeLookup, staticDir };
})(window);
