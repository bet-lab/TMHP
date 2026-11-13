"""Physics checks for the tank backends (docs/tank_backends/tank_backends.md §2.4).

A. standard charge test against plug flow + axial conduction (analytic),
B. discharge / idle / switching / heater scenarios: energy residual and monotonicity.

Runs on the current tree and, for the "before" columns, on a pre-#96 checkout
(missing features — ``max_courant``, hybrid ``q_source``, the outflow keys — are
detected and skipped or replaced by the node values the old API returned).

Run::

    .venv/bin/python docs/tank_backends/physics_checks.py
"""

import inspect
import math
import warnings

import numpy as np
from scipy.special import erfc

from tmhp.constants import c_w, k_w, rho_w
from tmhp.hybrid_tank import HybridStratifiedTank as Hyb
from tmhp.stratified_tank import StratifiedTank as Strat

OLD = "max_courant" not in inspect.signature(Strat.__init__).parameters
warnings.simplefilter("ignore")
rc = rho_w * c_w
V, H = 0.2, 1.2
A = V / H
TC, TH = 20.0, 60.0
Q = 1.2e-4  # 7.2 L/min
TAU = V / Q  # 1666.7 s
v = Q / A
D = k_w / rc  # physical axial diffusivity
E_MAX = rc * V * (TH - TC)


def analytic_outlet(t):
    t = np.maximum(t, 1e-9)
    return TC + (TH - TC) * 0.5 * erfc((H - v * t) / (2 * np.sqrt(D * t)))


def analytic_step_mean(t0, t1, n=200):
    s = np.linspace(t0, t1, n)
    return np.trapezoid(analytic_outlet(s), s) / (t1 - t0)


def outflow(o, key_new, key_old):
    return o[key_new] if key_new in o else o[key_old]


def backends():
    yield "stratified", lambda N: Strat(N, V, H, k_eff=k_w, ua=0.0)
    if not OLD:
        yield "stratified Co<=1", lambda N: Strat(N, V, H, k_eff=k_w, ua=0.0, max_courant=1.0)
    yield "hybrid", lambda N: Hyb(N, V, H, k_eff=k_w, ua=0.0)


def charge_test():
    print(f"\n## A. charge 20 °C tank with 60 °C, 7.2 L/min (tau = {TAU:.0f} s), run to t* = 2")
    print("eta = stored energy / E_max at the first step end t_e >= tau (analytic at the same t_e in brackets)")
    print(
        "| backend | dt [s] | N | Co | max abs resid [% E_max] | eta(t_e) [analytic] | t*_10 | t*_90 | RMSE outlet [K] |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    ts = np.linspace(0, 2 * TAU, 20001)
    Tout = analytic_outlet(ts)
    stored = rc * Q * np.concatenate(([0], np.cumsum(0.5 * ((TH - Tout[1:]) + (TH - Tout[:-1])) * np.diff(ts))))
    eta_a = np.interp(TAU, ts, stored) / E_MAX
    t10a = ts[np.argmax(Tout > TC + 0.1 * (TH - TC))] / TAU
    t90a = ts[np.argmax(Tout > TC + 0.9 * (TH - TC))] / TAU
    print(f"| analytic (plug flow + k_w diffusion) | – | – | – | – | {eta_a:.4f} | {t10a:.3f} | {t90a:.3f} | – |")
    for name, mk in backends():
        for dt in (10, 60, 600):
            for N in (10, 20, 60):
                tk = mk(N)
                tk.reset(TC)
                E0 = tk.stored_energy
                nsteps = int(math.ceil(2 * TAU / dt))
                inj = 0.0
                worst = 0.0
                outs, tt, err, est = [], [], [], []
                for k in range(nsteps):
                    o = tk.step(dt, charge_flow=Q, T_charge=TH, T_amb=20.0)
                    tout_k = outflow(o, "T_charge_out", "T_outlet")
                    inj += dt * rc * Q * (TH - tout_k)
                    worst = max(worst, abs(tk.stored_energy - E0 - inj) / E_MAX)
                    t1 = (k + 1) * dt
                    outs.append(tout_k)
                    tt.append(t1)
                    err.append(tout_k - analytic_step_mean(t1 - dt, t1))
                    est.append(tk.stored_energy - E0)
                tt, outs = np.array(tt), np.array(outs)
                ke = int(np.argmax(tt >= TAU - 1e-9))
                eta = est[ke] / E_MAX
                eta_ref = np.interp(tt[ke], ts, stored) / E_MAX
                a10 = np.nonzero(outs > TC + 0.1 * (TH - TC))[0]
                a90 = np.nonzero(outs > TC + 0.9 * (TH - TC))[0]
                t10 = tt[a10[0]] / TAU if a10.size else np.nan
                t90 = tt[a90[0]] / TAU if a90.size else np.nan
                rmse = float(np.sqrt(np.mean(np.square(err))))
                co = Q * dt / (V / N)
                print(
                    f"| {name} | {dt} | {N} | {co:.2f} | {worst * 100:.1e} | {eta:.4f} [{eta_ref:.4f}] | {t10:.3f} | {t90:.3f} | {rmse:.2f} |"
                )


def monotone(T, tol=1e-6):
    return bool(np.all(np.diff(T) <= tol))


SCEN = {
    "discharge (60 °C tank, makeup 20 °C, 1.5 tau)": (60.0, [(1.5 * TAU, dict(draw_flow=Q, T_makeup=TC))]),
    "half charge, then idle 6 h": (TC, [(0.5 * TAU, dict(charge_flow=Q, T_charge=TH)), (6 * 3600, {})]),
    "charge 0.5 tau, draw 0.25 tau, charge 0.5 tau": (
        TC,
        [
            (0.5 * TAU, dict(charge_flow=Q, T_charge=TH)),
            (0.25 * TAU, dict(draw_flow=Q, T_makeup=TC)),
            (0.5 * TAU, dict(charge_flow=Q, T_charge=TH)),
        ],
    ),
    "charge with simultaneous draw 0.5 Q (tau)": (
        TC,
        [(TAU, dict(charge_flow=Q, T_charge=TH, draw_flow=0.5 * Q, T_makeup=TC))],
    ),
    "bottom heater 3 kW 1 h, then idle 6 h": (40.0, [(3600, "HEAT"), (6 * 3600, {})]),
}


def scenario_tests():
    print("\n## B. discharge / idle / switching (ua = 2 W/K, N = 20)")
    print("residual = max over steps of |ΔE − Σ(port enthalpy + q − loss)| / E_max")
    print("| scenario | backend | dt [s] | max resid [% E_max] | monotone every step | final top / bottom [°C] |")
    print("|---|---|---|---|---|---|")
    for sname, (T0, seq) in SCEN.items():
        for name, mk in backends():
            if name == "stratified Co<=1":
                continue
            for dt in (60, 600):
                tk = mk(20)
                tk.ua_total, tk.ua_node = 2.0, 2.0 / 20
                tk.reset(T0)
                if OLD and name == "hybrid" and any(kw == "HEAT" for _, kw in seq):
                    print(f"| {sname} | {name} | {dt} | (no q_source before the fix) | – | – |")
                    continue
                E0 = tk.stored_energy
                acc = worst = 0.0
                mono = True
                for secs, kw in seq:
                    if kw == "HEAT":
                        q = np.zeros(20)
                        q[-1] = 3000.0
                        kw = dict(q_source=q)
                    for _ in range(int(round(secs / dt))):
                        o = tk.step(dt, T_amb=20.0, **kw)
                        cf, df = kw.get("charge_flow", 0.0), kw.get("draw_flow", 0.0)
                        tco = outflow(o, "T_charge_out", "T_outlet")
                        tdo = outflow(o, "T_draw_out", "T_top")
                        loss = o["Q_loss"] if "Q_loss" in o else tk.ua_node * float(np.sum(tk.T - 20.0))
                        acc += dt * (
                            rc * cf * (kw.get("T_charge", 0.0) - tco)
                            + rc * df * (kw.get("T_makeup", 10.0) - tdo)
                            + float(np.sum(kw.get("q_source", 0.0)))
                            - loss
                        )
                        worst = max(worst, abs(tk.stored_energy - E0 - acc) / E_MAX)
                        mono = mono and monotone(tk.T)
                print(
                    f"| {sname} | {name} | {dt} | {worst * 100:.1e} | {'yes' if mono else 'NO'} | {tk.T[0]:.1f} / {tk.T[-1]:.1f} |"
                )


if __name__ == "__main__":
    charge_test()
    scenario_tests()
