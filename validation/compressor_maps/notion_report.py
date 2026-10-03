"""Publish the plan-v3 refit report to its Notion page.

Structure (headings, tables, equations, figure captions) is built here from the
archived result files; prose lives in ``notion_observations_v3.md`` next to this
file, one ``## <key>`` block per section (a bare line is a paragraph, ``- `` a
bullet, ``> `` a callout, an indented line after a callout is its child).  The
page owner edits the page by hand afterwards, so a republish with ``--replace``
must be preceded by dumping the page and folding the edits back into the
observations file.

Run after emit_coefficients, parity.run, parity.rated_point, fixed_boundary_plr.
{sweep, decompose, shape_metrics} and figures_v3::

    uv run python3 -m validation.compressor_maps.notion_report --page <id> [--replace]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from validation.compressor_maps._notion import (
    append,
    bullet,
    call,
    callout,
    children_all,
    divider,
    equation,
    h,
    image,
    para,
    table,
)
from validation.compressor_maps.schema import REPO_ROOT

HERE = Path(__file__).resolve().parent
ARCHIVE = REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION
RESULTS = REPO_ROOT / "validation" / "results"
GATE_A = RESULTS / "gate_a"
PLR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
FIGS = ARCHIVE / "figures"
DEFAULT_PAGE = "3e46947d125d80cfb89fc8175dcdf643"


def observations() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    key: str | None = None
    for raw in (HERE / "notion_observations_v3.md").read_text(encoding="utf-8").splitlines():
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


def f(v, nd: int = 2) -> str:
    try:
        if v is None or pd.isna(v):
            return "—"
    except TypeError:
        return str(v)
    return f"{float(v):.{nd}f}"


def fig(name: str, caption: str) -> list[dict]:
    p = FIGS / f"{name}.png"
    return [image(p, caption)] if p.exists() else [callout(f"그림 {name} 없음 ({p})", "⚠️")]


def build(obs: dict) -> list[dict]:
    c = json.loads((ARCHIVE / "coefficients.json").read_text())
    sel = pd.read_csv(ARCHIVE / "model_selection.csv")
    loco = pd.read_csv(ARCHIVE / "loco_pooled.csv")
    split = json.loads((ARCHIVE / "split.json").read_text())
    gate_a = json.loads((GATE_A / "gate_a_summary.json").read_text())
    rated = pd.read_csv(GATE_A / "rated_points.csv")
    summary = pd.read_csv(RESULTS / "summary.csv")
    shape = pd.read_csv(PLR / "shape_metrics.csv")
    base = (
        json.loads((ARCHIVE / "baseline_reproduction.json").read_text())
        if (ARCHIVE / "baseline_reproduction.json").exists()
        else {}
    )
    v, o, s = c["eta_vol"], c["eta_oi"], c["split"]
    mode = c["estimator"]
    B: list[dict] = []

    # 0 -------------------------------------------------------------------
    B += [h(1, "0 · 목적과 범위")] + render(obs, "purpose")
    B += [
        table(
            ["항목", "기준 (plan v3)", "결과"],
            [
                ["세 효율의 $n^*$ 의존", "$\\eta_v, \\eta_{is}, \\eta_{em}$ 모두 포함", "충족 — 아래 §5"],
                ["$r_p$ 단독 함수", "최종 후보에서 제외", "없음"],
                [
                    "Gate A 정격점",
                    "기기별 $\\pm10\\,\\%$",
                    f"{gate_a['rated_within_10pct']}/{gate_a['rated_total']} (최대 {gate_a['rated_worst_abs_pct']:.1f} %)",
                ],
                ["Gate A 전체", "COP MAPE $\\le 10\\,\\%$"]
                + ["; ".join(f"{r['model_class']} {r['cop_MAPE_pct']:.1f} %" for r in gate_a["headline"])],
                ["Gate B", "저부하 무조건 상승 완화·문헌 정합", "§7"],
            ],
        )
    ]

    # 1 -------------------------------------------------------------------
    B += [h(1, "1 · Baseline 재현 (v2026-09-15b)")] + render(obs, "baseline")
    if base:
        B += [
            table(
                ["항목", "아카이브", "재실행", "일치"],
                [[r["item"], r["archived"], r["rerun"], r["match"]] for r in base["rows"]],
            )
        ]

    # 2 -------------------------------------------------------------------
    B += [h(1, "2 · 압축기 fitting 데이터")] + render(obs, "data")
    ds = pd.read_csv(ARCHIVE / "data_sources.csv")
    ok = (
        ds.groupby(["source_id", "comp_type", "refrigerant"])
        .agg(
            machines=("compressor_key", "nunique"),
            records=("speeds", "sum"),
            n=("n_points", "sum"),
            nmin=("n_star_min", "min"),
            nmax=("n_star_max", "max"),
            prmin=("PR_min", "min"),
            prmax=("PR_max", "max"),
        )
        .reset_index()
    )
    B += [
        table(
            ["source", "type", "refrigerant", "machines", "speed records", "points", "$n^*$", "$r_p$"],
            [
                [
                    r.source_id,
                    r.comp_type,
                    r.refrigerant,
                    str(int(r.machines)),
                    str(int(r.records)),
                    str(int(r.n)),
                    f"{r.nmin:.2f}–{r.nmax:.2f}",
                    f"{r.prmin:.1f}–{r.prmax:.1f}",
                ]
                for r in ok.itertuples()
            ],
        ),
        para(
            f"유효 {c['n_rows']}행 · {c['machines']}기 · {c['speed_records']} 속도 레코드. 카탈로그 히트펌프 데이터는 여기 들어가지 않는다."
        ),
    ]

    # 3 -------------------------------------------------------------------
    B += [h(1, "3 · 기계 내부 증거 — 저속 손실은 압력비에 비례한다")] + render(obs, "evidence")
    B += fig(
        "G1_within_machine_lowspeed",
        "그림 1. 같은 기계의 정격 속도 레코드 대비 저속 레코드의 $\\ln(\\eta_{is}\\eta_{em})$ 변화 (같은 증발·응축 온도에서 대조). 점은 운전점, 큰 표식은 압력비 0.5 구간 중앙값.",
    )
    B += render(obs, "evidence_after")

    # 4 -------------------------------------------------------------------
    B += [h(1, "4 · 후보식과 선택")] + render(obs, "candidates")
    B += [
        equation(r"\eta_v = 1 - A(r_p-1) - B\,u - C\,(r_p-1)\,u,\qquad u=\max(0,\,1/n^*-1)"),
        equation(
            r"\eta_{is}\eta_{em} = g(r_p)\,s(n^*)\,x(r_p,n^*)\,h(n^*),\qquad g = A_{oi} - B_{oi} r_p - C_{oi}/r_p"
        ),
        equation(
            r"s(n^*)=\frac{n^*(1+n_0)}{n^*+n_0},\quad x = 1 - c\,(r_p-1)\,u_L,\quad h = 1 - d\,(n^{*2}-1)\ \text{or}\ 1-d\max(0,n^*-1)^2"
        ),
        equation(
            r"\eta_{em} = \eta_{em,\mathrm{ref}}\; s(n^*)\; m(r_p),\quad m=\frac{r_p-1}{r_p-1+p_0}\cdot\frac{2+p_0}{2},\qquad \eta_{is} = \frac{\eta_{is}\eta_{em}}{\eta_{em}}"
        ),
    ]
    B += render(obs, "rules")
    so = sel[(sel.kind == "eta_oi") & (sel["mode"] == mode)]
    keep = [
        "I2xE0xX0",
        "I2xE1xX0",
        "I2xE2xX0",
        "I2xE1xL1",
        "I2xE2xL1",
        "I2xF0xX0",
        "I2xF0xL1",
        "I2xF2xX0",
        "I2xF2xL1",
        "I2xF3xL1",
        "I2xG0xX0",
        "I2xG2xL1",
        "I2xE1xX1",
    ]
    so = so[so.family.isin(keep)]
    B += [
        para(
            f"곱 함수 후보 ({mode} 추정, 76기 LOCO). ST = 정격 레코드 수준을 알 때 다른 속도 레코드를 예측한 오차(speed transfer)."
        ),
        table(
            [
                "family",
                "parent",
                "coef",
                "LOCO %",
                "ST %",
                "ΔLOCO pp",
                "ΔST pp",
                "R1",
                "R2",
                "R3",
                "R4 (ratio·기계·소스)",
                "채택",
            ],
            [
                [
                    r.family,
                    r.parent if isinstance(r.parent, str) else "",
                    str(int(r.n_coef)),
                    f(r.loco_wmape_pct),
                    f(r.st_wmape_pct),
                    f(r.gain_loco_pp),
                    f(r.gain_st_pp),
                    "✓" if r.R1_parsimony else "✗",
                    "✓" if r.R2_no_big_stratum_worse else "✗",
                    "✓" if r.R3_pass else "✗",
                    (f"{f(r.R4_ratio)} · {int(r.R4_machines)} · {int(r.R4_sources)}" if r.R4_applicable else "n/a")
                    + ("" if r.R4_pass else " ✗"),
                    "✓" if r.accepted_over_parent else "✗",
                ]
                for r in so.itertuples()
            ],
            code_cols=(0, 1),
        ),
    ]
    sv = sel[(sel.kind == "eta_vol") & (sel["mode"] == mode)]
    B += [
        para("체적효율 후보."),
        table(
            ["family", "parent", "coef", "LOCO %", "ST %", "ΔLOCO pp", "ΔST pp", "R1", "R2", "R3", "채택"],
            [
                [
                    r.family,
                    r.parent if isinstance(r.parent, str) else "",
                    str(int(r.n_coef)),
                    f(r.loco_wmape_pct),
                    f(r.st_wmape_pct),
                    f(r.gain_loco_pp),
                    f(r.gain_st_pp),
                    "✓" if r.R1_parsimony else "✗",
                    "✓" if r.R2_no_big_stratum_worse else "✗",
                    "✓" if r.R3_pass else "✗",
                    "✓" if r.accepted_over_parent else "✗",
                ]
                for r in sv.itertuples()
            ],
            code_cols=(0, 1),
        ),
    ]
    lp = loco[(loco.kind == "eta_oi") & loco.family.isin(["I2xE1xX0", "I2xF2xL1", "I2xF0xL1", "I2xE2xL1"])]
    B += [
        para("추정기 비교 — 같은 family 를 pooled 와 fixed-effects 로 적합했을 때의 LOCO·ST."),
        table(
            ["family", "estimator", "LOCO %", "ST %"],
            [
                [r.family, r["mode"], f(r.loco_wmape_pct), f(r.st_wmape_pct)]
                for _, r in lp.sort_values(["family", "mode"]).iterrows()
            ],
            code_cols=(0,),
        ),
    ]
    B += render(obs, "selection")

    # 5 -------------------------------------------------------------------
    B += [h(1, f"5 · 최종 함수와 계수 — {COEFFICIENT_VERSION}")] + render(obs, "final")
    B += [
        table(
            ["효율", "식", "계수"],
            [
                [
                    "$\\eta_v$",
                    f"family {v['family']}: $1 - A(r_p-1) - B u - C(r_p-1)u$",
                    f"A = {v['ETA_VOL_A']:.5f}, B = {v['ETA_VOL_B']:.5f}, C = {v['ETA_VOL_C']:.5f}",
                ],
                [
                    "$\\eta_{is}\\eta_{em}$",
                    f"family {o['family']}: $g\\,s\\,x\\,h$",
                    f"A = {o['ETA_OI_A']:.4f}, B = {o['ETA_OI_B']:.5f}, C = {o['ETA_OI_C']:.4f}; c = {o['ETA_LEAK_C']:.5f} ({'two-sided' if o['ETA_LEAK_TWO_SIDED'] else 'one-sided'}); d = {o['ETA_FLOW_D']:.5f} ({'two-sided' if o['ETA_FLOW_TWO_SIDED'] else 'one-sided'})",
                ],
                [
                    "$\\eta_{em}$",
                    "$\\eta_{em,ref}\\,s(n^*)\\,m(r_p)$",
                    f"$\\eta_{{em,ref}}$ = {s['ETA_EM_REF']:.4f} (PR 3, $n^*$ 1), $n_0$ = {s['ETA_EM_N0']:.4f}, $p_0$ = {s['ETA_EM_P0']:.4f}",
                ],
                ["$\\eta_{is}$", "$g\\,x\\,h / (\\eta_{em,ref}\\,m)$", "위 계수로 결정; 하한 0.30"],
            ],
        ),
        para(
            f"LOCO MAPE: $\\eta_v$ {v['loco_wmape_pct']:.2f} % (legacy {v['legacy_loco_wmape_pct']:.2f}), 곱 {o['loco_wmape_pct']:.2f} % (legacy {o['legacy_loco_wmape_pct']:.2f}); speed transfer: $\\eta_v$ {v['st_wmape_pct']:.2f} %, 곱 {o['st_wmape_pct']:.2f} %."
        ),
    ]
    st = split["s_table"]
    B += [
        para(
            "분리(split) 근거 — Cuevas & Lebrun 2009 토출온도 실측 29행(인버터 구동)에서 적합한 $\\eta_{em}$ 형상과 Ossorio & Navarro-Peris 2023 드라이브 단독 하한."
        ),
        table(
            ["모델", "식", "RMSE", "leave-one-record-out MAPE %"],
            [[k, m["label"], f(m["rmse"], 4), f(m["loro_mape_pct"])] for k, m in split["models"].items()],
        ),
        table(
            ["$n^*$"] + [f"{r['n_star']:g}" for r in st],
            [["$s(n^*)$"] + [f(r["s"], 3) for r in st]],
        ),
        para(
            "드라이브 단독 $n_0$ (Ossorio, 인버터 A/B/C): "
            + ", ".join(f"{k} {d_['n0_drive']:.4f}" for k, d_ in split["ossorio_drive_only"]["drives"].items())
            + f" → 총합 $n_0$ = {split['ETA_EM_N0']:.4f} 은 하한 위에 있다."
        ),
    ]
    B += fig(
        "G2_efficiency_shapes", "그림 2. 세 효율의 $n^*$ 의존 (PR 2 / 3 / 4.5). 검정 = 이번 버전, 회색 = v2026-09-15b."
    )
    B += render(obs, "final_after")

    # 6 -------------------------------------------------------------------
    B += [h(1, "6 · Gate A — 정격·정상상태 COP")] + render(obs, "gate_a")
    B += [
        table(
            ["model class", "rating standard", "units", "points", "COP MAPE %", "bias %", "≤ 10 %"],
            [
                [
                    r["model_class"],
                    r["rating_standard"],
                    str(r["units"]),
                    str(r["points"]),
                    f(r["cop_MAPE_pct"]),
                    f(r["cop_bias_pct"]),
                    "✓" if r["meets_target"] else "✗",
                ]
                for r in gate_a["headline"]
            ],
        ),
    ]
    ad = rated[rated.status == "adopted"]
    B += [
        table(
            ["unit", "refrigerant", "rating", "COP cat.", "COP model", "error %", "±10 %", "$n^*$", "$r_p$"],
            [
                [
                    r.unit,
                    r.refrigerant,
                    r.rating,
                    f(r.cop_target),
                    f(r.cop_pred),
                    f(r.rel_err_pct, 1),
                    "✓" if r.within_10pct else "✗",
                    f(r.n_star),
                    f(r.pr_cmp),
                ]
                for r in ad.itertuples()
            ],
        ),
    ]
    B += fig(
        "G4_gate_a",
        "그림 3. (a) 채택 기기 정격점 상대오차, (b) $n^*$ 구간별·(c) 압력비 구간별 카탈로그 잔차 (bias ± sd).",
    )
    B += render(obs, "gate_a_after")
    prev = summary[["unit", "refrigerant", "model_class", "status", "cop_MAPE_pct", "cop_bias_pct"]]
    if (ARCHIVE / "summary_previous.csv").exists():
        ps = pd.read_csv(ARCHIVE / "summary_previous.csv")[["unit", "cop_MAPE_pct", "cop_bias_pct"]].rename(
            columns={"cop_MAPE_pct": "prev_mape", "cop_bias_pct": "prev_bias"}
        )
        prev = prev.merge(ps, on="unit", how="left")
        B += [
            para("기기별 MAPE·편향, 이전 버전 대비."),
            table(
                ["unit", "ref", "status", "MAPE % (prev)", "MAPE % (now)", "bias % (prev)", "bias % (now)"],
                [
                    [
                        r.unit,
                        r.refrigerant,
                        r.status,
                        f(r.prev_mape, 1),
                        f(r.cop_MAPE_pct, 1),
                        f(r.prev_bias, 1),
                        f(r.cop_bias_pct, 1),
                    ]
                    for r in prev.itertuples()
                ],
            ),
        ]

    # 7 -------------------------------------------------------------------
    B += [h(1, "7 · Gate B — 고정경계 PLR-COP")] + render(obs, "gate_b")
    B += fig(
        "G3_plr_cop_decomposition",
        "그림 4. 고정경계 PLR sweep. 검정 = 이번 버전, 점선 = 세 효율을 정격점 값에 고정(열교환기 효과만), 회색 = v2026-09-15b. 빈 표식 = 압축기 속도 하한.",
    )
    sh = shape[shape.file == "decomposition.csv"] if "file" in shape.columns else shape
    if "variant" in sh.columns:
        rows = []
        for key, g in sh.groupby(["model_class", "duty", "refrigerant", "t_outdoor_C", "t_sink_C"]):
            gv = g.set_index("variant")
            for var in ("current", "hx_only", "previous"):
                if var in gv.index:
                    r = gv.loc[var]
                    rows.append(
                        [
                            f"{key[0]} {key[1]} {key[2]} {key[3]:g}/{key[4]:g}",
                            var,
                            r.shape if isinstance(r["shape"], str) else "",
                            f(r.COP_rated),
                            f(r.PLR_peak),
                            f(r.COP_peak),
                            f(r.PLR_low),
                            f(r.COP_low),
                            f(100 * r.dCOP_low, 1),
                            f(r.n_star_low),
                            f(r.pr_rated, 2) + "→" + f(r.pr_low, 2),
                            f(r.eta_oi_low_over_rated, 3),
                        ]
                    )
        B += [
            table(
                [
                    "case",
                    "variant",
                    "shape",
                    "COP@1",
                    "PLR peak",
                    "COP peak",
                    "PLR low",
                    "COP low",
                    "ΔCOP low %",
                    "$n^*$ low",
                    "$r_p$ 1→low",
                    "$\\eta_{is}\\eta_{em}$ low/rated",
                ],
                rows,
                code_cols=(1,),
            )
        ]
    B += render(obs, "gate_b_after")

    # 8 -------------------------------------------------------------------
    B += [h(1, "8 · 문헌의 PLR-COP 형상과 정성 비교")] + render(obs, "literature")

    # 9 -------------------------------------------------------------------
    B += [h(1, "9 · 한계와 다음 단계")] + render(obs, "limits")
    B += [
        divider(),
        para(
            f"산출물: validation/coefficients/{COEFFICIENT_VERSION}/ (계수·CV·분리·그림), validation/results/ (패리티·정격점), validation/data/fixed_boundary_plr/ (sweep·분해·형상지표)."
        ),
    ]
    return B


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--page", default=DEFAULT_PAGE)
    ap.add_argument("--replace", action="store_true", help="delete the page's existing child blocks first")
    ap.add_argument("--title", default=None)
    a = ap.parse_args()
    obs = observations()
    blocks = build(obs)
    if a.replace:
        for b in children_all(a.page):
            call("DELETE", f"/blocks/{b['id']}")
    if a.title:
        call(
            "PATCH",
            f"/pages/{a.page}",
            {"properties": {"title": {"title": [{"type": "text", "text": {"content": a.title}}]}}},
        )
    append(a.page, blocks)
    print(f"published {len(blocks)} blocks to {a.page}")


if __name__ == "__main__":
    main()
