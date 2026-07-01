"""Per-timestep solar routing — ground vs tank vs greedy, over a multi-day run.

`GSHPB_STC_routed` sends collected solar heat to the borehole field OR the
storage tank each step (exclusively). The routing decision is a control input, so
three policies are compared on the same 7-day diurnal schedule:

  - always-ground : pin every solar step to ground charging (seasonal COP bank)
  - always-tank   : pin every solar step to DHW tank preheat
  - greedy        : default policy — tank when below setpoint, else ground

The point is the *tradeoff* the router trades off: ground charging banks heat in
the soil (raising T_bhe and the HP source temperature for later COP), while tank
charging offsets DHW heating now. The greedy default does both opportunistically
without ever charging the two at once.

Run::

    OMP_NUM_THREADS=2 .venv/bin/python docs/solar_routing/compare_routing_policies.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tmhp import GSHPB_STC_routed
from tmhp.subsystems import SolarThermalCollector

HERE = Path(__file__).resolve().parent

DAYS = 7
N = DAYS * 24
DT = 3600.0
CFG = dict(ref="R32", N_1=2, N_2=1, H_b=100.0, dt_s=DT, t_max_s=(N + 8) * 3600)
KWH = DT / 3.6e6  # W·step -> kWh


def _schedules():
    dhw = np.zeros(N)
    T0 = np.zeros(N)
    I_DN = np.zeros(N)
    I_dH = np.zeros(N)
    for d in range(DAYS):
        h = d * 24
        dhw[h + np.array([6, 7, 19, 20])] = 6.0e-5  # morning + evening DHW draw
        day = h + np.arange(7, 18)
        I_DN[day] = 800.0
        I_dH[day] = 120.0
        T0[h:h + 24] = 8.0 + 6.0 * np.sin(np.linspace(-1.2, 1.9, 24))  # diurnal ambient
    return dhw, T0, I_DN, I_dH


def _run(router):
    dhw, T0, I_DN, I_dH = _schedules()
    model = GSHPB_STC_routed(stc=SolarThermalCollector(A_stc=6.0), solar_router=router,
                             T_tank_w_lower_bound=60.0, **CFG)
    return model.analyze_dynamic(
        simulation_period_sec=N * DT, dt_s=DT, T_tank_w_init_C=58.0,
        dhw_usage_schedule=dhw, T0_schedule=T0, I_DN_schedule=I_DN, I_dH_schedule=I_dH,
    )


def main() -> None:
    policies = {
        "always-ground": (lambda **_: "ground"),
        "always-tank": (lambda **_: "tank"),
        "greedy (default)": None,  # default_solar_router
    }
    out = {}
    # Report only directly-measured, unambiguous quantities: the solar energy
    # split and the ground-temperature trajectory. HP electricity / COP are NOT
    # reported here — over this short horizon the tank/greedy runs drive the small
    # test field into an over-extracted, unphysically cold regime (T_bhe_f below
    # any real evaporating range), where cop_sys is unreliable. The economic
    # payoff of ground charging is seasonal and needs a longer, field-sized study.
    print(f"{'policy':18s} {'sol_grnd':>9s} {'sol_tank':>9s} {'Tbhe_mean':>10s} {'Tbhe_min':>9s} {'Tbhe_end':>9s}")
    for name, router in policies.items():
        df = _run(router)
        q_grd = float(df["Q_solar_ground [W]"].sum()) * KWH
        q_tnk = float(df["Q_solar_tank [W]"].sum()) * KWH
        tbhe = df["T_bhe [°C]"].to_numpy()
        out[name] = dict(df=df, q_grd=q_grd, q_tnk=q_tnk,
                         tbhe_mean=float(tbhe.mean()), tbhe_min=float(tbhe.min()), tbhe_end=float(tbhe[-1]))
        print(f"{name:18s} {q_grd:9.2f} {q_tnk:9.2f} {out[name]['tbhe_mean']:10.2f} "
              f"{out[name]['tbhe_min']:9.2f} {out[name]['tbhe_end']:9.2f}")
    _plot(out)


def _plot(out) -> None:
    import dartwork_mpl as dm
    import matplotlib.pyplot as plt

    dm.style.use("scientific")
    cmap = {"always-ground": "C1", "always-tank": "C0", "greedy (default)": "C2"}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.4, 2.9))

    # Panel A — ground temperature trajectory (seasonal charging accumulation)
    for name, r in out.items():
        t = np.arange(N) / 24.0
        ax1.plot(t, r["df"]["T_bhe [°C]"].to_numpy(), color=cmap[name], lw=1.1, label=name)
    ax1.set_xlabel("time [days]", fontsize=dm.fs(-1))
    ax1.set_ylabel("borehole-wall temp $T_{bhe}$ [°C]", fontsize=dm.fs(-1))
    ax1.set_title("ground charging vs routing policy", fontsize=dm.fs(-1))
    ax1.legend(fontsize=dm.fs(-3), frameon=False, loc="best")

    # Panel B — where the solar energy went (ground vs tank), per policy
    names = list(out.keys())
    x = np.arange(len(names))
    w = 0.38
    ax2.bar(x - w / 2, [out[n]["q_grd"] for n in names], w, color="C1", label="solar to ground")
    ax2.bar(x + w / 2, [out[n]["q_tnk"] for n in names], w, color="C0", label="solar to tank")
    ax2.set_xticks(x)
    ax2.set_xticklabels([n.replace(" (default)", "") for n in names], fontsize=dm.fs(-2), rotation=12)
    ax2.set_ylabel("delivered solar energy [kWh]", fontsize=dm.fs(-1))
    ax2.set_title("solar energy split (7 days)", fontsize=dm.fs(-1))
    ax2.legend(fontsize=dm.fs(-3), frameon=False, loc="best")

    dm.simple_layout(fig, margin="2mm")
    dm.save_formats(fig, str(HERE / "fig1_routing_policies"))
    print(f"saved {HERE / 'fig1_routing_policies'}.pdf/.png")


if __name__ == "__main__":
    main()
