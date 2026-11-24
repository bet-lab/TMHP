/**
 * Pure evaluator for TMHP's default compressor efficiency maps (#253).
 *
 * No DOM: imported by the docs widget (`compressor-map.js`) and by the
 * agreement test, which runs it under `node --input-type=module`.
 * The rules follow `docs/notes/compressor-map-widget-contract.md`; the
 * Python library (`tmhp.compressor_efficiency`) is the reference:
 *
 *   l = ln(r_p); t7 = [1, l, l^2, l^3, N, N^2, l*N]
 *   eta_v  = c_v  . [...t7, T_evap]
 *   eta_ov = c_ov . [...t7, T_evap]
 *   f_loss = c_f  . t7
 *   eta_is = eta_ov / (1 - f_loss)    (null when 1 - f_loss <= 0)
 *
 * Values are returned raw: the cycle's rule (values above 1 set to 1,
 * nonpositive values infeasible) is not applied here.
 */

/**
 * Correctly rounded sum of floats (Shewchuk's partials, as Python's
 * `math.fsum`, which the library uses for the dot products).
 */
export function fsum(values) {
    const partials = [];
    for (let x of values) {
        let i = 0;
        for (let y of partials) {
            if (Math.abs(x) < Math.abs(y)) [x, y] = [y, x];
            const hi = x + y;
            const lo = y - (hi - x);
            if (lo !== 0) partials[i++] = lo;
            x = hi;
        }
        partials.length = i;
        partials.push(x);
    }
    let n = partials.length;
    if (n === 0) return 0;
    let hi = partials[--n];
    let lo = 0;
    while (n > 0) {
        const x = hi;
        const y = partials[--n];
        hi = x + y;
        const yr = hi - x;
        lo = y - yr;
        if (lo !== 0) break;
    }
    // Round half to even across the remaining partials (as CPython does).
    if (n > 0 && ((lo < 0 && partials[n - 1] < 0) || (lo > 0 && partials[n - 1] > 0))) {
        const y = lo * 2;
        const x = hi + y;
        const yr = x - hi;
        if (y === yr) hi = x;
    }
    return hi;
}

function dot(coefficients, terms) {
    if (coefficients.length !== terms.length) {
        throw new Error(`coefficient/term length mismatch: ${coefficients.length} vs ${terms.length}`);
    }
    return fsum(coefficients.map((c, i) => c * terms[i]));
}

function finite(x) {
    return typeof x === "number" && Number.isFinite(x);
}

/** The seven (r_p, N) terms, or null when the map has no value there. */
function terms7(PR, N) {
    if (!(finite(PR) && PR > 0 && finite(N))) return null;
    const l = Math.log(PR);
    return [1.0, l, l * l, l ** 3, N, N * N, l * N];
}

/**
 * Point-in-convex-polygon test, the same arithmetic as the library's
 * `HullDomain._inside`: an edge whose cross product is exactly zero is
 * skipped, so points on the boundary count as inside.
 */
export function insideHull(vertices, x, y) {
    if (!(finite(x) && finite(y))) return false;
    const n = vertices.length;
    let sign = 0.0;
    for (let i = 0; i < n; i++) {
        const [x1, y1] = vertices[i];
        const [x2, y2] = vertices[(i + 1) % n];
        const cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1);
        if (cross === 0.0) continue;
        if (sign === 0.0) sign = cross;
        else if ((cross > 0.0) !== (sign > 0.0)) return false;
    }
    return true;
}

/** `MapDomain.contains(PR, N, T_evap)` with rtol = 0. */
export function inDomain(data, PR, N_rps, T_evap_C) {
    const [lo, hi] = data.domain.N_rps;
    if (!(finite(N_rps) && lo <= N_rps && N_rps <= hi)) return false;
    return insideHull(data.domain.hull_PR_Te, PR, T_evap_C);
}

/**
 * Evaluate all four quantities at (r_p, N [rev/s], T_evap [degC]).
 * @returns {{eta_v: number|null, eta_ov: number|null, f_loss: number|null,
 *            eta_is: number|null, in_domain: boolean}}
 */
export function evaluate(data, PR, N_rps, T_evap_C) {
    const c = data.coefficients;
    const t = terms7(PR, N_rps);
    const in_domain = inDomain(data, PR, N_rps, T_evap_C);
    if (t === null) {
        return { eta_v: null, eta_ov: null, f_loss: null, eta_is: null, in_domain };
    }
    const f_loss = dot(c.f_loss, t);
    let eta_v = null;
    let eta_ov = null;
    if (finite(T_evap_C)) {
        const t8 = [...t, T_evap_C];
        eta_v = dot(c.eta_v, t8);
        eta_ov = dot(c.eta_ov, t8);
    }
    const denom = 1 - f_loss;
    const eta_is = eta_ov !== null && denom > 0 ? eta_ov / denom : null;
    return { eta_v, eta_ov, f_loss, eta_is, in_domain };
}

/**
 * The r_p interval inside the (r_p, T_evap) hull at one T_evap, or null when
 * that T_evap lies outside the hull's temperature span. The hull is convex,
 * so the section is one interval. Endpoints come from edge intersections;
 * use `insideHull` when the exact boundary rule matters.
 * @returns {[number, number] | null}
 */
export function rpRangeAt(data, T_evap_C) {
    const v = data.domain.hull_PR_Te;
    const n = v.length;
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = 0; i < n; i++) {
        const [x1, y1] = v[i];
        const [x2, y2] = v[(i + 1) % n];
        const ymin = Math.min(y1, y2);
        const ymax = Math.max(y1, y2);
        if (T_evap_C < ymin || T_evap_C > ymax) continue;
        if (y1 === y2) {
            lo = Math.min(lo, x1, x2);
            hi = Math.max(hi, x1, x2);
        } else {
            const x = x1 + ((T_evap_C - y1) * (x2 - x1)) / (y2 - y1);
            lo = Math.min(lo, x);
            hi = Math.max(hi, x);
        }
    }
    return lo <= hi ? [lo, hi] : null;
}

/** The (r_p, T_evap) extents of the hull: {PR: [min, max], T_evap: [min, max]}. */
export function hullExtent(data) {
    const v = data.domain.hull_PR_Te;
    const xs = v.map((p) => p[0]);
    const ys = v.map((p) => p[1]);
    return {
        PR: [Math.min(...xs), Math.max(...xs)],
        T_evap: [Math.min(...ys), Math.max(...ys)],
    };
}
