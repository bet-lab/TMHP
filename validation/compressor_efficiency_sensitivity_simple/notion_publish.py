"""Rebuild the PLR–COP sensitivity Notion page from the simplified study.

The page layout mirrors the hand-edited version of 2026-09-16: purpose →
common-input table → case table → the function assumptions → Figure 1 →
heating (Figure 2) → cooling (Figure 3) → summary (Figure 4 + tables).  An
operating-state figure was produced earlier and dropped from the page, so the
figures are numbered 1–4 here and in ``figures.py``.

Prose belongs to ``notion_observations.md`` next to this file, one ``## <key>``
block per section: a bare line is a paragraph, ``- `` a bullet, ``> `` a
callout, and an indented line after a callout becomes its child.  Keeping the
commentary there means a republish reproduces the page rather than flattening
it, and the wording can be edited without touching Python.  This module owns
only the structure: headings, tables, equations and figure captions.

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
#: figure stem -> number on the page.  Renumbering happens here only.
FIG_NO = {"fig1_functions": 1, "fig2_cop_heating": 2, "fig3_cop_cooling": 3, "fig4_summary": 4}


# ---------------------------------------------------------------------------
# prose
# ---------------------------------------------------------------------------
def observations() -> dict[str, list[dict]]:
    """Parse ``notion_observations.md`` into per-key block descriptions."""
    out: dict[str, list[dict]] = {}
    key: str | None = None
    for raw in (HERE / "notion_observations.md").read_text(encoding="utf-8").splitlines():
        if raw.startswith("## "):
            key = raw[3:].strip()
            out[key] = []
            continue
        if key is None or not raw.strip() or raw.startswith("#"):
            continue
        stripped = raw.strip()
        if raw.startswith(("  ", "\t")) and out[key] and out[key][-1]["kind"] == "callout":
            out[key][-1]["children"].append(stripped)
        elif stripped.startswith("- "):
            out[key].append({"kind": "bullet", "text": stripped[2:], "children": []})
        elif stripped.startswith("> "):
            out[key].append({"kind": "callout", "text": stripped[2:], "children": []})
        else:
            out[key].append({"kind": "paragraph", "text": stripped, "children": []})
    return out


def render(obs: dict[str, list[dict]], key: str) -> list[dict]:
    blocks: list[dict] = []
    for item in obs.get(key, []):
        if item["kind"] == "bullet":
            blocks.append(bullet(item["text"]))
        elif item["kind"] == "paragraph":
            blocks.append(para(item["text"]))
        else:
            block = callout(item["text"])
            if item["children"]:
                block["callout"]["children"] = [para(c) for c in item["children"]]
            blocks.append(block)
    return blocks


def f(v: float, nd: int = 2) -> str:
    return "—" if pd.isna(v) else f"{v:.{nd}f}"


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def common_table(params: dict[str, dict]) -> dict:
    ph, pc = params["heating"], params["cooling"]
    m = ph["model"]
    fn = ph["function"]
    n_c, p_c = fn["n_star_c"], fn["p_r_c"]
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
            f"$1 - {fn['a_n']:.2f}\\,(n^* - {n_c:.2f})^2$  /  $1 - {fn['a_p']:.2f}\\,(P_r - {p_c:.2f})^2$",
        ],
        [
            "—",
            "$\\eta_v$ multiplier (linear)",
            f"$1 - {fn['b_v']:.2f}\\,(n^* - {n_c:.2f})$  /  $1 - {fn['b_v']:.2f}\\,(P_r - {p_c:.2f})$",
        ],
        ["Q_r_iu", "PLR sweep (requested load = ±hp_capacity × PLR)", "0.10 → 1.00, step 0.025 (37점)"],
        ["V_cmp_ref", "Compressor displacement [cm³/rev]", f"{m['V_cmp_ref'] * 1e6:.1f}"],
        ["UA_ou_rated", "Outdoor HX UA [W/K]", f"{m['UA_ou_rated']:.0f}"],
        ["UA_iu_rated", "Indoor HX UA [W/K]", f"{m['UA_iu_rated']:.0f}"],
        ["dV_ou_fan_a_rated", "Rated outdoor airflow [m³/s]", f"{m['dV_ou_fan_a_rated']:.2f}"],
        ["dV_iu_fan_a_rated", "Rated indoor airflow [m³/s]", f"{m['dV_iu_fan_a_rated']:.2f}"],
    ]
    return table(["TMHP variable name", "Description", "Value / Function"], rows, code_cols=(0,))


def case_table() -> dict:
    eta_list = ", ".join(f"${ETA_TEX[k]}$ {v:.2f}" for k, v in ETA_BASE.items())
    rows = [["BASE", "—", "없음", "—", f"{eta_list} (전 구간 상수)"]]
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
        internal = []
        for kr, s in (("난방", sh), ("냉방", sc)):
            if bool(s.loc[case, "internal_max"]):
                internal.append(f"{kr} PLR {s.loc[case, 'plr_cop_max']:g}")
        rows.append(
            [
                case,
                f"${ETA_TEX[eff]}$",
                f"${'n^*' if drv == 'n_star' else 'P_r'}$",
                f"{f(sh.loc[case, 'd_cop_low'], 3)} ({sh.loc[case, 'd_cop_low_pct']:+.1f} %)",
                f"{f(sc.loc[case, 'd_cop_low'], 3)} ({sc.loc[case, 'd_cop_low_pct']:+.1f} %)",
                ", ".join(internal) if internal else "없음",
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
        rows.append([DUTY_KR[d], f(s.cop_100, 3), f(s.cop_low, 3), f"{s.plr_low:g}", f"{s.plr_floor:g}"])
    return table(
        ["Duty", "BASE COP @PLR 1.00", "BASE COP @low PLR", "공통 저부하점 PLR", "BASE 속도 하한 도달 PLR"], rows
    )


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------
def fig_block(stem: str, caption: str, tag: str = "") -> dict:
    """``tag`` rides with the number, e.g. ``Figure 2 (난방). …``."""
    label = f"Figure {FIG_NO[stem]}" + (f" {tag}" if tag else "")
    return image(FIG_DIR / f"{stem}.png", f"{label}. {caption}")


def build(params: dict[str, dict], summaries: dict[str, pd.DataFrame], obs: dict[str, list[dict]]) -> list[dict]:
    b: list[dict] = []

    b += [h(1, "1. 분석 목적")]
    b += render(obs, "purpose")

    b += [h(1, "2. 공통 입력조건"), common_table(params)]

    b += [h(1, "3. Case 정의"), case_table()]

    b += [h(1, "4. 효율 함수 가정")]
    b += [
        equation(f"\\eta_{{is}}(n^*) = \\eta_{{is,0}}\\left[1 - {A_N:.2f}\\,(n^* - {N_STAR_C:.2f})^2\\right]"),
        equation(f"\\eta_{{em}}(n^*) = \\eta_{{em,0}}\\left[1 - {A_N:.2f}\\,(n^* - {N_STAR_C:.2f})^2\\right]"),
        equation(
            f"\\eta_i(P_r) = \\eta_{{i,0}}\\left[1 - {A_P:.2f}\\,(P_r - {PR_C:.2f})^2\\right], "
            "\\qquad i \\in \\{is, em\\}"
        ),
        equation(f"\\eta_v(n^*) = \\eta_{{v,0}}\\left[1 - {B_V:.2f}\\,(n^* - {N_STAR_C:.2f})\\right]"),
        equation(f"\\eta_v(P_r) = \\eta_{{v,0}}\\left[1 - {B_V:.2f}\\,(P_r - {PR_C:.2f})\\right]"),
        equation(
            f"\\eta_{{v,0}} = {ETA_BASE['eta_cmp_vol']:.2f}, \\qquad "
            f"\\eta_{{is,0}} = {ETA_BASE['eta_cmp_isen']:.2f}, \\qquad "
            f"\\eta_{{em,0}} = {ETA_BASE['eta_cmp']:.2f}"
        ),
    ]
    b += render(obs, "functions")

    b += [h(1, f"5. 가정한 효율곡선 (Figure {FIG_NO['fig1_functions']})")]
    b.append(
        fig_block(
            "fig1_functions",
            "(a) $n^*$ 함수, (b) $P_r$ 함수 — $\\eta_v$ 선형(파랑), $\\eta_{is}$·$\\eta_{em}$ ∩ 이차식(주황·보라). "
            "색 띠 = BASE 스윕이 실제로 지나간 $n^*$·$P_r$ 범위(난방 주황 / 냉방 파랑). 점선 = 함수 중심.",
        )
    )
    b += render(obs, "fig1")

    panel = (
        "각 panel: BASE(검정) + 해당 case. 위 행 $n^*$ 함수, 아래 행 $P_r$ 함수; "
        "열은 $\\eta_v$ / $\\eta_{is}$ / $\\eta_{em}$. "
        "빈 마커 = 속도 하한(공급 > 요구), 회색 띠 = 하한 구간(비교 제외). PLR은 왼쪽이 저부하."
    )
    for sec, duty, stem in (("6", "heating", "fig2_cop_heating"), ("7", "cooling", "fig3_cop_cooling")):
        p = params[duty]
        b += [
            h(
                1,
                f"{sec}. {DUTY_KR[duty]} 결과 (Figure {FIG_NO[stem]}) — "
                f"outdoor {p['boundary']['T0']:g} °C / room {p['boundary']['T_a_room']:g} °C",
            )
        ]
        b.append(fig_block(stem, panel, tag=f"({DUTY_KR[duty]})"))
        b += render(obs, f"fig_{duty}")

    b += [h(1, f"8. 결과 요약 (Figure {FIG_NO['fig4_summary']})")]
    b.append(
        fig_block(
            "fig4_summary",
            "공통 저부하점(모든 case가 아직 modulating인 가장 낮은 PLR)에서 BASE 대비 ΔCOP [%] — 난방 주황 / 냉방 파랑.",
        )
    )
    b.append(summary_table(summaries))
    b.append(
        para(
            "ΔCOP = COP_case − COP_BASE, 같은 요구 PLR에서. 괄호는 BASE 대비 %. "
            "Internal COP maximum = modulating 구간 양 끝이 아닌 곳에 COP 최대가 있는 경우."
        )
    )
    b.append(base_table(summaries))
    b += render(obs, "summary")
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replace", action="store_true", help="delete existing child blocks first")
    a = ap.parse_args()
    frames, params = load_all()
    summaries = {d: summarise(frames[d]) for d in DUTIES}
    if a.replace:
        existing = children_all(PAGE_ID)
        for blk in existing:
            call("DELETE", f"/blocks/{blk['id']}")
        print(f"deleted {len(existing)} existing blocks")
    blocks = build(params, summaries, observations())
    append(PAGE_ID, blocks)
    print(f"appended {len(blocks)} blocks -> https://app.notion.com/p/PLR-COP-{PAGE_ID}")


if __name__ == "__main__":
    main()
