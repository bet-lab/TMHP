"""Publish the sensitivity study to its Notion page (REST, ``NOTION_API_KEY`` from ``.env``).

Builds the page body from ``parameters_*.json``, ``summary_*.csv`` and the PNG
figures: purpose, common-input table, case table, synthetic-function equations,
heating and cooling results, short observations and the data/code list.  Runs
after ``sweep`` (both duties) and ``figures``.

The observations are hand-written and read from ``notion_observations.md`` next
to this file: a ``## <section>`` heading per block (``fig2_heating`` …
``final``), one bullet per line.

Run::

    uv run python3 -m validation.compressor_efficiency_sensitivity.notion_publish --replace

``--replace`` deletes the page's existing child blocks first, so re-publishing
is idempotent.  The token is never printed.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DATA = REPO_ROOT / "validation" / "data" / "compressor_efficiency_sensitivity"
FIG = DATA / "figures"
API = "https://api.notion.com/v1"
VERSION = "2025-09-03"
PAGE_ID = "3dd6947d125d8003a73add07e6c7f4d4"
DUTIES = ("heating", "cooling")
DUTY_KR = {"heating": "난방", "cooling": "냉방"}
CASE_ORDER = ("C0", "CURRENT", "N-V", "N-I", "N-E", "PR-V", "PR-I", "PR-E")


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
def _token() -> str:
    env = os.environ.get("NOTION_API_KEY")
    if env:
        return env
    for parent in [HERE, *HERE.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("NOTION_API_KEY"):
                    return line.partition("=")[2].strip().strip("'\"")
    raise SystemExit("NOTION_API_KEY not found (.env or environment)")


def call(
    method: str, path: str, body: dict | None = None, *, raw: bytes | None = None, ctype: str | None = None
) -> dict:
    headers = {"Authorization": f"Bearer {_token()}", "Notion-Version": VERSION}
    data = raw
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in (409, 429, 500, 502, 503, 504):
                time.sleep(2.0 * (attempt + 1))
                continue
            raise SystemExit(f"{method} {path} failed: {exc.code} {msg}") from exc
    raise SystemExit(f"{method} {path} failed after retries")


def upload(path: Path) -> str:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    created = call("POST", "/file_uploads", {"filename": path.name, "content_type": ctype})
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    sent = call(
        "POST", f"/file_uploads/{created['id']}/send", raw=body, ctype=f"multipart/form-data; boundary={boundary}"
    )
    if sent.get("status") != "uploaded":
        raise SystemExit(f"upload of {path.name} did not complete: {sent}")
    return created["id"]


def children_all(block_id: str) -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        suffix = f"&start_cursor={cursor}" if cursor else ""
        page = call("GET", f"/blocks/{block_id}/children?page_size=100{suffix}")
        out.extend(page.get("results", []))
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")


def append(blocks: list[dict]) -> None:
    for i in range(0, len(blocks), 90):
        call("PATCH", f"/blocks/{PAGE_ID}/children", {"children": blocks[i : i + 90]})


# ---------------------------------------------------------------------------
# block builders
# ---------------------------------------------------------------------------
def rt(text: str, *, code: bool = False, bold: bool = False) -> list[dict]:
    """Rich text; ``$...$`` spans become inline equations."""
    parts: list[dict] = []
    for i, chunk in enumerate(text.split("$")):
        if not chunk:
            continue
        if i % 2 == 1:
            parts.append({"type": "equation", "equation": {"expression": chunk}})
        else:
            parts.append(
                {"type": "text", "text": {"content": chunk[:1900]}, "annotations": {"code": code, "bold": bold}}
            )
    return parts


def h(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": rt(text)}}


def para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(text)}}


def bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(text)}}


def equation(expr: str) -> dict:
    return {"object": "block", "type": "equation", "equation": {"expression": expr}}


def code(text: str, language: str = "plain text") -> dict:
    return {"object": "block", "type": "code", "code": {"rich_text": rt(text), "language": language}}


def table(header: list[str], rows: list[list[str]], *, code_cols: tuple[int, ...] = ()) -> dict:
    def row(cells: list[str], is_header: bool) -> dict:
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {
                "cells": [
                    rt(str(c), code=(j in code_cols and not is_header), bold=is_header) for j, c in enumerate(cells)
                ]
            },
        }

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": [row(header, True)] + [row(r, False) for r in rows],
        },
    }


def image(path: Path, caption: str) -> dict:
    fid = upload(path)
    return {
        "object": "block",
        "type": "image",
        "image": {"type": "file_upload", "file_upload": {"id": fid}, "caption": rt(caption)},
    }


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------
def load() -> tuple[dict[str, dict], dict[str, pd.DataFrame]]:
    params = {d: json.loads((DATA / f"parameters_{d}.json").read_text(encoding="utf-8")) for d in DUTIES}
    summaries = {d: pd.read_csv(DATA / f"summary_{d}.csv") for d in DUTIES}
    return params, summaries


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
    rows = [
        ["model_class", "모델 클래스", "AirSourceHeatPump (air-to-air, `tmhp.air_source_heat_pump`)"],
        ["ref", "Refrigerant", m["ref"]],
        ["hp_capacity", "Rated capacity [W] (명판 = 냉방 duty, RATED_POINT_AIR_TO_AIR)", f"{m['hp_capacity']:.0f}"],
        ["T0", "Outdoor air temperature [°C]", f"Heating {ph['boundary']['T0']:g} / Cooling {pc['boundary']['T0']:g}"],
        [
            "T_a_room",
            "Indoor (room) air temperature [°C]",
            f"Heating {ph['boundary']['T_a_room']:g} / Cooling {pc['boundary']['T_a_room']:g}",
        ],
        [
            "rps_rated",
            "Rated compressor speed [rev/s] (n* = rps / rps_rated)",
            f"{m['rps_rated']:g}  ({m['rated_point']['basis']})",
        ],
        [
            "rps_min",
            "Minimum compressor speed [rev/s]",
            f"{m['rps_min']:g}  (n*_min = {m['rps_min'] / m['rps_rated']:.3f})",
        ],
        ["rps_max", "Maximum compressor speed [rev/s]", f"{m['rps_max']:g}"],
        [
            "V_cmp_ref",
            "Compressor displacement [cm³/rev]",
            f"{m['V_cmp_ref'] * 1e6:.3f}  = default_displacement(hp_capacity, ref, RATED_POINT_AIR_TO_AIR)",
        ],
        ["UA_ou_rated", "Outdoor HX UA [W/K]", f"{m['UA_ou_rated']:.0f}  = hp_capacity / 5.0"],
        ["UA_iu_rated", "Indoor HX UA [W/K]", f"{m['UA_iu_rated']:.0f}  = UA_ou_rated × 0.8"],
        ["dV_ou_fan_a_rated", "Rated outdoor airflow [m³/s]", f"{m['dV_ou_fan_a_rated']:.3f}  = hp_capacity × 0.0002"],
        ["dV_iu_fan_a_rated", "Rated indoor airflow [m³/s]", f"{m['dV_iu_fan_a_rated']:.3f}  = hp_capacity × 0.0002"],
        [
            "Q_r_iu",
            "Requested indoor load [W] = ±hp_capacity × PLR (heating <0, cooling >0)",
            "PLR 1.000 → 0.100, step 0.025 (37점)",
        ],
        [
            "analyze_steady",
            "Solver / operation mode",
            '모델 기본 (_optimize_operation, specific-energy objective); capacity_clamped="min" = 속도 하한',
        ],
        [
            "eta_cmp_vol / eta_cmp_isen / eta_cmp",
            "CURRENT 효율 correlation",
            f"make_eta_vol(60) / eta_isen_default / make_eta_em(60), {ph['current_coefficients']['version']}",
        ],
    ]
    return table(["TMHP variable name", "Description", "Value / Function"], rows, code_cols=(0,))


def sensitivity_table(params: dict[str, dict]) -> dict:
    ph, pc = params["heating"], params["cooling"]
    sw = ph["sweep"]
    rows = [
        ["$\\delta_{\\mathrm{low}}$", f"{sw['d_low']:.2f}", f"{sw['d_low']:.2f}"],
        ["$\\delta_{\\mathrm{high}}$", f"{sw['d_high']:.2f}", f"{sw['d_high']:.2f}"],
        ["$PLR_{\\mathrm{ref}}$ (η_ref 추출점, CURRENT)", f"{sw['plr_ref']:.2f}", f"{sw['plr_ref']:.2f}"],
        ["$PLR_{\\mathrm{opt}}$", f"{sw['plr_opt']:.2f}", f"{sw['plr_opt']:.2f}"],
    ]
    for k, lab in (
        ("eta_cmp_vol", "\\eta_{v,\\mathrm{peak}}"),
        ("eta_cmp_isen", "\\eta_{is,\\mathrm{peak}}"),
        ("eta_cmp", "\\eta_{em,\\mathrm{peak}}"),
    ):
        rows.append([f"${lab}$ (= η_ref)", f"{ph['eta_ref'][k]:.4f}", f"{pc['eta_ref'][k]:.4f}"])
    for drv, lab in (("n_star", "n^*"), ("pr", "PR")):
        for a, sub in (("x_low", "low"), ("x_opt", "opt"), ("x_high", "high")):
            rows.append(
                [
                    f"${lab}_{{\\mathrm{{{sub}}}}}$ (C0 run)",
                    f"{ph['anchors'][drv][a]:.3f}",
                    f"{pc['anchors'][drv][a]:.3f}",
                ]
            )
    return table(["Parameter", "Heating", "Cooling"], rows)


def case_table() -> dict:
    rows = [
        ["C0", "—", "없음 (전부 상수)", "η_v, η_is, η_em = η_ref (CURRENT @ PLR 0.65)"],
        ["CURRENT", "PR, n*", "출하 correlation 그대로", "v2026-09-15b: η_v(PR, n*), η_is(PR), η_em(n*)"],
        ["N-V", "n*", "η_v", "η_is, η_em 상수"],
        ["N-I", "n*", "η_is", "η_v, η_em 상수"],
        ["N-E", "n*", "η_em", "η_v, η_is 상수"],
        ["PR-V", "PR", "η_v", "η_is, η_em 상수"],
        ["PR-I", "PR", "η_is", "η_v, η_em 상수"],
        ["PR-E", "PR", "η_em", "η_v, η_is 상수"],
    ]
    return table(["Case", "독립변수", "변화시키는 효율", "나머지"], rows)


def summary_table(s: pd.DataFrame) -> dict:
    s = s.set_index("case").loc[list(CASE_ORDER)]
    plr_low = s.plr_low.iloc[0]
    rows = []
    for case, r in s.iterrows():
        rows.append(
            [
                case,
                r.driver,
                r.varied,
                f(r.cop_100),
                f(r.cop_50),
                f(r.cop_low),
                f(r.d_cop_low, 3),
                f(r.d_cop_low_pct, 1),
                f(r.d_eta_low_pct, 1),
                f(r.S_low, 2),
                f(r.plr_cop_max, 3) + (" (내부)" if bool(r.internal_max) else ""),
                f(r.cop_range, 3),
            ]
        )
    header = [
        "Case",
        "Driver",
        "Varied η",
        "COP @100 %",
        "COP @50 %",
        f"COP @PLR {plr_low:.3f}",
        "ΔCOP_low",
        "ΔCOP_low [%]",
        "Δη_low [%]",
        "S_low",
        "PLR_COP,max",
        "COP_max − COP_min",
    ]
    return table(header, rows)


def build(
    params: dict[str, dict], summaries: dict[str, pd.DataFrame], obs: dict[str, list[str]], head: str
) -> list[dict]:
    ph = params["heating"]
    ver = ph["current_coefficients"]["version"]
    b: list[dict] = []

    b += [h(1, "1. 목적")]
    b += [
        para(
            "TMHP 공기→공기 모델에서 경계온도를 고정한 채 PLR을 내렸을 때, 압축기 3효율($\\eta_v$, $\\eta_{is}$, $\\eta_{em}$) 중 어느 것이 PLR–COP 곡선을 가장 크게 움직이는지, "
            "그리고 같은 효율을 회전수 함수로 둘 때와 압력비 함수로 둘 때 차이가 있는지를 분리해 본다. "
            "한 번에 한 효율만 synthetic ∩형 곡선으로 바꾸고 나머지 둘은 상수로 고정한 controlled sensitivity test다. "
            "여기의 곡선은 새 correlation 후보가 아니라 원인 분리용 perturbation이며, `src/tmhp` 기본값은 건드리지 않고 validation override(효율 callable 인자)로만 실행했다."
        )
    ]

    b += [h(1, "2. 공통 입력조건"), common_table(params)]
    b += [h(3, "Sensitivity parameter"), sensitivity_table(params)]
    b += [
        para(
            "η_ref는 CURRENT 모델이 PLR 0.65(정상 modulation, 속도 하한 밖)에서 계산한 값이고, anchor(x_low / x_opt / x_high)는 C0 control run의 궤적에서 "
            "속도 하한 직전 마지막 modulating 점 / PLR 0.60 보간 / PLR 1.00 값을 읽었다. 임의 숫자는 없다."
        )
    ]

    b += [h(1, "3. Sensitivity case 정의"), case_table()]

    b += [h(1, "4. Artificial efficiency function")]
    b += [
        para(
            "독립변수 $x \\in \\{n^*,\\ PR\\}$, $n^* = N / N_{\\mathrm{rated}}$. 세 효율에 동일한 normalized multiplier $F$를 곱한다:"
        ),
        equation("\\eta_i(x) = \\eta_{i,\\mathrm{ref}}\\, F(x), \\qquad i \\in \\{v, is, em\\}"),
        equation(
            "F(x) = \\begin{cases} 1 - \\delta_{\\mathrm{low}} \\left( \\dfrac{x - x_{\\mathrm{opt}}}{x_{\\mathrm{low}} - x_{\\mathrm{opt}}} \\right)^2, & x \\le x_{\\mathrm{opt}} \\\\[8pt] "
            "1 - \\delta_{\\mathrm{high}} \\left( \\dfrac{x - x_{\\mathrm{opt}}}{x_{\\mathrm{high}} - x_{\\mathrm{opt}}} \\right)^2, & x > x_{\\mathrm{opt}} \\end{cases}"
        ),
        para(
            "$[x_{\\mathrm{low}}, x_{\\mathrm{high}}]$ 밖에서는 끝값을 유지(hold)한다 — 속도 탐색이 rps 15~150 전 구간을 bracket하므로 2차식을 그대로 따라가면 효율이 음수가 된다. "
            "C0에서 정의한 범위 밖으로 운전점이 이동한 case는 Figure 4의 실제 궤적에서 확인한다."
        ),
    ]
    for d in DUTIES:
        b.append(
            image(
                FIG / f"fig1_curves_{d}.png",
                f"Figure 1 ({DUTY_KR[d]}). (a) n* 함수, (b) PR 함수 — 실제 효율 수준(η_ref × F). 점선: x_low / x_opt / x_high.",
            )
        )
    b += [bullet(t) for t in obs.get("fig1", [])]

    for sec, d in (("5", "heating"), ("6", "cooling")):
        p = params[d]
        b += [
            h(
                1,
                f"{sec}. {DUTY_KR[d].capitalize()} 결과 — outdoor {p['boundary']['T0']:g} °C / room {p['boundary']['T_a_room']:g} °C",
            )
        ]
        b += [h(2, f"{sec}.1 6-case PLR–COP (Figure 2)")]
        b.append(
            image(
                FIG / f"fig2_plr_cop_{d}.png",
                f"Figure 2 ({DUTY_KR[d]}). 각 panel: C0(검정) · CURRENT {ver}(회색 점선) · 해당 case. 빈 마커 = 속도 하한(공급 > 요구), 회색 띠 = 하한 구간(주 비교에서 제외).",
            )
        )
        b += [bullet(t) for t in obs.get(f"fig2_{d}", [])]
        b += [h(2, f"{sec}.2 ΔCOP vs PLR (Figure 3)")]
        b.append(
            image(
                FIG / f"fig3_dcop_{d}.png",
                f"Figure 3 ({DUTY_KR[d]}). ΔCOP% = (COP_case − COP_C0) / COP_C0 × 100. (a) n* 함수 3 case, (b) PR 함수 3 case; CURRENT는 참고.",
            )
        )
        b += [bullet(t) for t in obs.get(f"fig3_{d}", [])]
        b += [h(2, f"{sec}.3 실제 효율 trajectory (Figure 4)")]
        b.append(
            image(
                FIG / f"fig4_eta_traj_{d}.png",
                f"Figure 4 ({DUTY_KR[d]}). 각 case에서 solver가 실제로 지나간 변화 효율. 수평선: η_ref(C0), −5 %, −10 %.",
            )
        )
        b += [bullet(t) for t in obs.get(f"fig4_{d}", [])]
        b += [h(2, f"{sec}.4 State feedback (Figure 5)")]
        b.append(
            image(
                FIG / f"fig5_state_{d}.png",
                f"Figure 5 ({DUTY_KR[d]}). (a) n*, (b) PR, (c) 냉매 질량유량, (d) 압축기 전력 vs PLR — 8 case.",
            )
        )
        b += [bullet(t) for t in obs.get(f"fig5_{d}", [])]
        b += [h(2, f"{sec}.5 Summary table")]
        b.append(summary_table(summaries[d]))
        b += [
            para(
                "ΔCOP_low·Δη_low·S_low는 8 case가 모두 modulating인 가장 낮은 공통 PLR에서 C0 대비. "
                "S_low = (ΔCOP/COP_C0)/(Δη/η_ref)는 system-level effective sensitivity(순수 미분계수가 아님 — η_v는 질량유량·요구속도까지 바꾼다). "
                'PLR_COP,max는 그 case의 modulating 구간 안 최대 COP 위치; "내부"는 양 끝이 아닌 곳에 최대가 있을 때.'
            )
        ]

    b += [h(1, "7. 짧은 관찰사항")]
    b.append(
        image(
            FIG / "fig6_summary.png",
            "Figure 6. (a) 공통 저부하점 ΔCOP [%], (b) effective sensitivity S — 난방(주황)·냉방(파랑).",
        )
    )
    b += [bullet(t) for t in obs.get("final", [])]

    b += [h(1, "8. 데이터 및 코드")]
    b.append(
        code(
            "\n".join(
                [
                    f"repo   : TMHP, branch feat/compressor-efficiency-sensitivity @ {head}",
                    f"coeffs : {ver} (CURRENT)",
                    "code   : validation/compressor_efficiency_sensitivity/{__init__,synthetic_efficiencies,sweep,metrics,figures,notion_publish}.py",
                    "data   : validation/data/compressor_efficiency_sensitivity/results_{heating,cooling}.csv",
                    "         validation/data/compressor_efficiency_sensitivity/parameters_{heating,cooling}.json",
                    "         validation/data/compressor_efficiency_sensitivity/summary_{heating,cooling}.csv",
                    "figures: validation/data/compressor_efficiency_sensitivity/figures/*.png|svg",
                    "run    : uv run python3 -m validation.compressor_efficiency_sensitivity.sweep --duty heating",
                    "         uv run python3 -m validation.compressor_efficiency_sensitivity.sweep --duty cooling",
                    "         uv run python3 -m validation.compressor_efficiency_sensitivity.figures",
                    "CSV columns per PLR point: case, duty, plr_request, q_request_W, q_delivered_W, cr_actual, capacity_clamped,",
                    "  failure_reason, rps, n_star, pr, m_dot_ref, T_evap_C, T_cond_C, eta_cmp_vol, eta_cmp_isen, eta_cmp,",
                    "  E_cmp, E_ou_fan, E_iu_fan, E_fan, E_tot, cop_sys, dV_ou_a, fan_fraction, modulating, coefficient_version",
                ]
            ),
            "plain text",
        )
    )
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replace", action="store_true", help="delete existing child blocks first")
    ap.add_argument("--head", default=None, help="git HEAD short hash to record")
    a = ap.parse_args()
    params, summaries = load()
    head = a.head or params["heating"].get("git_head", "unknown")
    if a.replace:
        existing = children_all(PAGE_ID)
        for blk in existing:
            call("DELETE", f"/blocks/{blk['id']}")
        print(f"deleted {len(existing)} existing blocks")
    blocks = build(params, summaries, observations(), head)
    append(blocks)
    print(f"appended {len(blocks)} blocks -> https://app.notion.com/p/PLR-COP-{PAGE_ID}")


if __name__ == "__main__":
    main()
