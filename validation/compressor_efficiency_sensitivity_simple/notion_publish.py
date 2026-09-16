"""Rebuild the PLR–COP sensitivity Notion page from the simplified study.

Page structure (plan §19): purpose → common-input table → case table → the two
function assumptions → Figure 1 → Figure 2 → heating (Figure 3) → cooling
(Figure 4) → summary (Figure 5 + table) → data and code.  Every figure gets at
most four short bullets, read from ``notion_observations.md`` next to this
file (``## <key>`` heading per block, one ``- `` bullet per line).

Run after ``sweep`` and ``figures``::

    uv run python3 -m validation.compressor_efficiency_sensitivity_simple.notion_publish --replace

``--replace`` deletes the page's existing child blocks first, so the page is
rebuilt rather than appended to.  The token is never printed.
"""

from __future__ import annotations

import argparse

import pandas as pd

from ._notion import (
    HERE,
    append,
    bullet,
    call,
    callout,
    children_all,
    code,
    equation,
    h,
    image,
    para,
    table,
)
from .config import (
    A_N,
    A_P,
    B_V,
    CASES,
    DUTIES,
    ETA_BASE,
    ETA_CLIP,
    FIG_DIR,
    N_STAR_C,
    PR_C,
    SHAPE,
)
from .metrics import load_all, summarise

PAGE_ID = "3dd6947d125d8003a73add07e6c7f4d4"
DUTY_KR = {"heating": "난방", "cooling": "냉방"}
ETA_TEX = {"eta_cmp_vol": "\\eta_v", "eta_cmp_isen": "\\eta_{is}", "eta_cmp": "\\eta_{em}"}
SHAPE_KR = {"quadratic": "∩ 이차식", "linear": "선형 감소"}


def observations() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    key = None
    for line in (HERE / "notion_observations.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            key = line[3:].strip()
            out[key] = []
        elif key and line.strip().startswith("- "):
            out[key].append(line.strip()[2:])
    return out


def f(v: float, nd: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{nd}f}"


def common_table(params: dict[str, dict]) -> dict:
    ph, pc = params["heating"], params["cooling"]
    m = ph["model"]
    fn = ph["function"]
    rows = [
        ["ref", "Refrigerant", m["ref"]],
        ["hp_capacity", "Rated capacity [W]", f"{m['hp_capacity']:.0f}"],
        ["T0", "Outdoor air temperature [°C]", f"Heating {ph['boundary']['T0']:g} / Cooling {pc['boundary']['T0']:g}"],
        [
            "T_a_room",
            "Indoor air temperature [°C]",
            f"Heating {ph['boundary']['T_a_room']:g} / Cooling {pc['boundary']['T_a_room']:g}",
        ],
        ["rps_rated", "Rated compressor speed [rev/s]  ($n^* = N / N_{\\mathrm{rated}}$)", f"{m['rps_rated']:g}"],
        [
            "rps_min",
            "Minimum compressor speed [rev/s]",
            f"{m['rps_min']:g}  ($n^*_{{\\min}}$ = {m['rps_min'] / m['rps_rated']:.2f})",
        ],
        ["rps_max", "Maximum compressor speed [rev/s]", f"{m['rps_max']:g}"],
        ["eta_cmp_vol", "Volumetric efficiency baseline $\\eta_{v,0}$", f"{ETA_BASE['eta_cmp_vol']:.2f}"],
        ["eta_cmp_isen", "Isentropic efficiency baseline $\\eta_{is,0}$", f"{ETA_BASE['eta_cmp_isen']:.2f}"],
        ["eta_cmp", "Electromechanical efficiency baseline $\\eta_{em,0}$", f"{ETA_BASE['eta_cmp']:.2f}"],
        [
            "—",
            "$\\eta_{is}$, $\\eta_{em}$ multiplier (quadratic)",
            f"$1 - {fn['a_n']:.2f}\\,(n^* - {fn['n_star_c']:.2f})^2$  /  $1 - {fn['a_p']:.2f}\\,(P_r - {fn['p_r_c']:.2f})^2$",
        ],
        [
            "—",
            "$\\eta_v$ multiplier (linear)",
            f"$1 - {fn['b_v']:.2f}\\,(n^* - {fn['n_star_c']:.2f})$  /  $1 - {fn['b_v']:.2f}\\,(P_r - {fn['p_r_c']:.2f})$",
        ],
        [
            "—",
            "Harness clip on synthetic $\\eta$",
            f"{fn['eta_clip'][0]:.1f} ≤ η ≤ {fn['eta_clip'][1]:.1f} (보고된 운전점에서 clipping 0행)",
        ],
        ["Q_r_iu", "PLR sweep (requested load = ±hp_capacity × PLR)", "0.10 → 1.00, step 0.025 (37점)"],
        ["V_cmp_ref", "Compressor displacement [cm³/rev]", f"{m['V_cmp_ref'] * 1e6:.3f} (default_displacement)"],
        ["UA_ou_rated", "Outdoor HX UA [W/K]", f"{m['UA_ou_rated']:.0f} (= hp_capacity / 5)"],
        ["UA_iu_rated", "Indoor HX UA [W/K]", f"{m['UA_iu_rated']:.0f} (= 0.8 × UA_ou_rated)"],
        ["dV_ou_fan_a_rated", "Rated outdoor airflow [m³/s]", f"{m['dV_ou_fan_a_rated']:.2f} (= hp_capacity × 0.0002)"],
        ["dV_iu_fan_a_rated", "Rated indoor airflow [m³/s]", f"{m['dV_iu_fan_a_rated']:.2f} (= hp_capacity × 0.0002)"],
        [
            "dT_approach_bounds",
            "Approach-temperature search box [K]",
            f"({m['dT_approach_bounds'][0]:g}, {m['dT_approach_bounds'][1]:g}) 모델 기본",
        ],
        [
            "PR_cycle_min / PR_cycle_max",
            "Pressure-ratio sanity guard",
            f"{m['PR_cycle_min']:g} / {m['PR_cycle_max']:g}",
        ],
    ]
    return table(["TMHP variable name", "Description", "Value / Function"], rows, code_cols=(0,))


def case_table() -> dict:
    rows = [["BASE", "—", "없음", "—", "$\\eta_v$ 0.95, $\\eta_{is}$ 0.70, $\\eta_{em}$ 0.90 (전 구간 상수)"]]
    for case, (eff, drv) in CASES.items():
        others = ", ".join(f"${ETA_TEX[k]}$ {ETA_BASE[k]:.2f}" for k in ETA_BASE if k != eff)
        rows.append(
            [
                case,
                f"${'n^*' if drv == 'n_star' else 'P_r'}$",
                f"${ETA_TEX[eff]}$",
                SHAPE_KR[SHAPE[eff]],
                others + " (상수)",
            ]
        )
    return table(["Case", "독립변수", "함수로 변화시키는 효율", "함수형", "나머지 두 효율"], rows)


def summary_table(summaries: dict[str, pd.DataFrame]) -> dict:
    sh = summaries["heating"].set_index("case")
    sc = summaries["cooling"].set_index("case")
    rows = []
    for case in CASES:
        eff, drv = CASES[case]
        im = []
        for d, s in (("난방", sh), ("냉방", sc)):
            if bool(s.loc[case, "internal_max"]):
                im.append(f"{d} PLR {s.loc[case, 'plr_cop_max']:.2f}")
        rows.append(
            [
                case,
                f"${ETA_TEX[eff]}$",
                f"${'n^*' if drv == 'n_star' else 'P_r'}$",
                f"{f(sh.loc[case, 'd_cop_low'], 3)} ({sh.loc[case, 'd_cop_low_pct']:+.1f} %)",
                f"{f(sc.loc[case, 'd_cop_low'], 3)} ({sc.loc[case, 'd_cop_low_pct']:+.1f} %)",
                ", ".join(im) if im else "없음",
            ]
        )
    header = [
        "Case",
        "Varied efficiency",
        "Driver",
        f"ΔCOP at low PLR — Heating (PLR {sh.plr_low.iloc[0]:g})",
        f"ΔCOP at low PLR — Cooling (PLR {sc.plr_low.iloc[0]:g})",
        "Internal COP maximum",
    ]
    return table(header, rows)


def base_table(summaries: dict[str, pd.DataFrame]) -> dict:
    rows = []
    for d in DUTIES:
        s = summaries[d].set_index("case").loc["BASE"]
        rows.append([DUTY_KR[d], f(s.cop_100, 3), f(s.cop_low, 3), f"{s.plr_low:g}", f"{s.plr_floor:.3f}"])
    return table(
        ["Duty", "BASE COP @PLR 1.00", "BASE COP @low PLR", "공통 저부하점 PLR", "BASE 속도 하한 도달 PLR"], rows
    )


def build(
    frames: dict[str, pd.DataFrame],
    params: dict[str, dict],
    summaries: dict[str, pd.DataFrame],
    obs: dict[str, list[str]],
    head: str,
) -> list[dict]:
    max_n_star = float(max(df[df.modulating].n_star.max() for df in frames.values()))
    b: list[dict] = []
    b += [h(1, "1. 분석 목적")]
    b += [
        para(
            "TMHP 공기→공기 모델에서 경계온도를 고정하고 PLR을 내릴 때, 압축기 3효율($\\eta_v$, $\\eta_{is}$, $\\eta_{em}$) 중 어느 것의 함수형 변화가 PLR–COP 곡선을 가장 크게 바꾸는지 본다. "
            "세 효율을 단순 상수로 고정한 BASE 위에, 한 번에 한 효율에만 relative multiplier를 $n^*$ 또는 $P_r$의 함수로 얹어 6 case를 비교한다. "
            "$\\eta_{is}$·$\\eta_{em}$에는 ∩자형 이차식, $\\eta_v$에는 회전수·압력비가 커질수록 선형으로 감소하는 식을 쓴다(체적효율이 ∩형이면 질량유량이 속도에 비단조가 되어 속도 해법기가 해를 놓치기 때문). "
            "새 correlation을 만들거나 압축기 데이터를 fitting하는 작업이 아니며, `src/tmhp` 기본값은 건드리지 않고 validation override(효율 callable 인자)로만 실행했다."
        ),
        callout(
            "보고 싶은 것 세 가지: ① 어느 효율이 PLR–COP를 가장 크게 바꾸는가 ② 같은 효율을 $n^*$ 함수로 둘 때와 $P_r$ 함수로 둘 때 어느 쪽 영향이 큰가 ③ 중간 PLR에서 COP maximum이 생기는 case가 있는가",
            "🎯",
        ),
    ]

    b += [h(1, "2. 공통 입력조건"), common_table(params)]
    b += [para("변수명은 `tmhp.AirSourceHeatPump` 생성자·속성의 실제 이름. 표에 없는 값은 모델 기본값 그대로.")]

    b += [h(1, "3. Case 정의"), case_table()]

    b += [h(1, "4. 효율 함수 가정")]
    b += [
        para(
            "중심은 두 함수형이 공유한다($n^*_c$ 0.60, $P_{r,c}$ 2.00). $\\eta_{is}$·$\\eta_{em}$($i \\in \\{is, em\\}$)에는 같은 ∩ 이차 multiplier를, "
            "$\\eta_v$에는 선형 감소 multiplier를 곱한다. 그 외에 달라지는 것은 기본값 $\\eta_{i,0}$뿐이다."
        ),
        equation(
            f"\\eta_i(n^*) = \\eta_{{i,0}}\\left[1 - {A_N:.2f}\\,(n^* - {N_STAR_C:.2f})^2\\right], \\qquad i \\in \\{{is, em\\}}"
        ),
        equation(
            f"\\eta_i(P_r) = \\eta_{{i,0}}\\left[1 - {A_P:.2f}\\,(P_r - {PR_C:.2f})^2\\right], \\qquad i \\in \\{{is, em\\}}"
        ),
        equation(f"\\eta_v(n^*) = \\eta_{{v,0}}\\left[1 - {B_V:.2f}\\,(n^* - {N_STAR_C:.2f})\\right]"),
        equation(f"\\eta_v(P_r) = \\eta_{{v,0}}\\left[1 - {B_V:.2f}\\,(P_r - {PR_C:.2f})\\right]"),
        equation(
            f"\\eta_{{v,0}} = {ETA_BASE['eta_cmp_vol']:.2f}, \\qquad \\eta_{{is,0}} = {ETA_BASE['eta_cmp_isen']:.2f}, \\qquad \\eta_{{em,0}} = {ETA_BASE['eta_cmp']:.2f}"
        ),
        para(
            f"$n^* = N / N_{{\\mathrm{{rated}}}}$, $P_r = P_{{\\mathrm{{dis}}}} / P_{{\\mathrm{{suc}}}}$. 속도 탐색이 envelope 전체(n* ≤ 2.5)를 bracket하므로 callable은 {ETA_CLIP[0]:.1f} ≤ η ≤ {ETA_CLIP[1]:.1f}로 clip한다. "
            "결과로 보고된 운전점에서 clip에 걸린 행은 없다(각 case CSV의 `eta_clipped` 열)."
        ),
        para(
            f"$\\eta_v$를 선형으로 두는 이유: 속도 해법기는 rps_min~rps_max($n^*$ 0.25~2.5) 양끝의 용량 부호만 보고 해를 찾는데, 질량유량 ∝ $\\eta_v\\,n^*$이므로 $\\eta_v$가 ∩형이면 이 곱이 $n^* \\approx 1.17$에서 정점을 찍고 감소해 해를 놓친다(실측: N-V 전 구간 rps_max clamp). "
            f"선형 기울기 {B_V:.2f}이면 $n^*\\,[1 - {B_V:.2f}(n^* - {N_STAR_C:.2f})]$의 정점이 $n^*$ = 5.3으로 탐색 범위 밖이라 별도 hold 없이 잘 정의된다. 보고된 운전점의 최대 $n^*$는 {max_n_star:.3f}."
        ),
    ]

    b += [h(1, "5. 가정한 효율곡선 (Figure 1)")]
    b.append(
        image(
            FIG_DIR / "fig1_functions.png",
            "Figure 1. (a) $n^*$ 함수, (b) $P_r$ 함수 — $\\eta_v$ 선형(파랑), $\\eta_{is}$·$\\eta_{em}$ ∩ 이차식(주황·보라). 색 띠 = BASE 스윕이 실제로 지나간 $n^*$·$P_r$ 범위(난방 주황 / 냉방 파랑). 점선 = 함수 중심.",
        )
    )
    b += [bullet(t) for t in obs.get("fig1", [])]

    b += [h(1, "6. PLR에 따른 $n^*$, $P_r$ — BASE와 $\\eta_v$ case (Figure 2)")]
    b.append(
        image(
            FIG_DIR / "fig2_base_state.png",
            "Figure 2. (a) $n^*$, (b) $P_r$ vs PLR — BASE(실선), N-V(파선), P-V(점선); 난방 주황 / 냉방 파랑. 빈 마커 = 속도 하한, 회색 띠 = 하한 구간, 수평 점선 = 함수 중심.",
        )
    )
    b += [bullet(t) for t in obs.get("fig2", [])]

    for sec, d, fig_no in (("7", "heating", 3), ("8", "cooling", 4)):
        p = params[d]
        b += [
            h(
                1,
                f"{sec}. {DUTY_KR[d]} 결과 (Figure {fig_no}) — outdoor {p['boundary']['T0']:g} °C / room {p['boundary']['T_a_room']:g} °C",
            )
        ]
        b.append(
            image(
                FIG_DIR / f"fig{fig_no}_cop_{d}.png",
                f"Figure {fig_no} ({DUTY_KR[d]}). 각 panel: BASE(검정) + 해당 case. 위 행 $n^*$ 함수, 아래 행 $P_r$ 함수; 열은 $\\eta_v$ / $\\eta_{{is}}$ / $\\eta_{{em}}$. "
                "빈 마커 = 속도 하한(공급 > 요구), 회색 띠 = 하한 구간(비교 제외). PLR은 왼쪽이 저부하.",
            )
        )
        b += [bullet(t) for t in obs.get(f"fig_{d}", [])]

    b += [h(1, "9. 결과 요약 (Figure 5)")]
    b.append(
        image(
            FIG_DIR / "fig5_summary.png",
            "Figure 5. 공통 저부하점(모든 case가 아직 modulating인 가장 낮은 PLR)에서 BASE 대비 ΔCOP [%] — 난방 주황 / 냉방 파랑.",
        )
    )
    b += [summary_table(summaries)]
    b += [
        para(
            "ΔCOP = COP_case − COP_BASE, 같은 요구 PLR에서. 괄호는 BASE 대비 %. Internal COP maximum = modulating 구간 양 끝이 아닌 곳에 COP 최대가 있는 경우."
        )
    ]
    b += [base_table(summaries)]
    b += [bullet(t) for t in obs.get("summary", [])]

    b += [h(1, "10. 데이터 및 코드")]
    b.append(
        code(
            "\n".join(
                [
                    f"repo   : TMHP, branch feat/compressor-efficiency-sensitivity @ {head}",
                    "code   : validation/compressor_efficiency_sensitivity_simple/{config,functions,sweep,metrics,figures,notion_publish}.py",
                    "data   : validation/data/compressor_efficiency_sensitivity_simple/results_{heating,cooling}.csv",
                    "         validation/data/compressor_efficiency_sensitivity_simple/parameters_{heating,cooling}.json",
                    "         validation/data/compressor_efficiency_sensitivity_simple/summary_{heating,cooling}.csv",
                    "figures: validation/data/compressor_efficiency_sensitivity_simple/figures/fig{1..5}_*.png|svg",
                    "run    : uv run python3 -m validation.compressor_efficiency_sensitivity_simple.sweep",
                    "         uv run python3 -m validation.compressor_efficiency_sensitivity_simple.figures",
                    "         uv run python3 -m validation.compressor_efficiency_sensitivity_simple.notion_publish --replace",
                    "CSV columns: case, duty, plr_request, q_request_W, q_delivered_W, capacity_clamped, rps, n_star, p_r,",
                    "  eta_cmp_vol, eta_cmp_isen, eta_cmp, E_cmp, E_tot, cop_sys, T_evap_C, T_cond_C, m_dot_ref,",
                    "  failure_reason, modulating, eta_clipped, coefficient_version",
                    "archive: validation/compressor_efficiency_sensitivity/ (이전 anchored 분석; 이 페이지에서는 사용하지 않음)",
                ]
            )
        )
    )
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replace", action="store_true", help="delete existing child blocks first")
    ap.add_argument("--head", default=None, help="git HEAD short hash to record")
    a = ap.parse_args()
    frames, params = load_all()
    summaries = {d: summarise(frames[d]) for d in DUTIES}
    head = a.head or params["heating"].get("git_head", "unknown")
    if a.replace:
        existing = children_all(PAGE_ID)
        for blk in existing:
            call("DELETE", f"/blocks/{blk['id']}")
        print(f"deleted {len(existing)} existing blocks")
    blocks = build(frames, params, summaries, observations(), head)
    append(PAGE_ID, blocks)
    print(f"appended {len(blocks)} blocks -> https://app.notion.com/p/PLR-COP-{PAGE_ID}")


if __name__ == "__main__":
    main()
