"""Publish the final validation report (v2026-09-24) to its Notion page.

Structure (headings, tables, equations, code, figure captions, PDF embeds) is
built here; prose lives in ``notion_observations_final.md`` (one ``## <key>``
block per section: plain line = paragraph, ``- `` bullet, ``> `` callout with
indented child lines, ``[ ] `` / ``[x] `` to-do).

Run after ``final_simulate`` and ``final_figures``::

    uv run python3 -m validation.compressor_maps.notion_final --replace [--no-pdf]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import uuid
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp import compressor_efficiency as ce
from validation.compressor_maps._notion import (
    append,
    bullet,
    call,
    callout,
    children_all,
    code,
    divider,
    equation,
    h,
    image,
    para,
    rt,
    table,
)
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT

HERE = Path(__file__).resolve().parent
ARCHIVE = REPO_ROOT / "validation" / "coefficients" / ce.COEFFICIENT_VERSION
FIGS = ARCHIVE / "figures" / "final"
SIM = REPO_ROOT / "validation" / "data" / "final_report"
GATE_A = REPO_ROOT / "validation" / "results" / "gate_a"
EVID = REPO_ROOT / "validation" / "evidence"
DEFAULT_PAGE = "3e46947d125d80cfb89fc8175dcdf643"
TITLE = "TMHP Compressor Efficiency Model — Final Validation (v2026-09-24)"

# external catalogue PDFs (not under the repo evidence tree)
EXT_REFS = Path("/home/habin/papers/enex-engine/01_active/ASHPB-validation-overall-refs/references")
PDF_FIT = [
    (EVID / "compressor_maps/papers/cuevas_lebrun_2009_ate.pdf", "Cuevas & Lebrun (2009), doi:10.1016/j.applthermaleng.2008.03.016"),
    (EVID / "compressor_maps/papers/shao_2004_ijr_rotary.pdf", "Shao et al. (2004), doi:10.1016/j.ijrefrig.2004.02.008"),
    (EVID / "compressor_maps/papers/ossorio_navarroperis_2023_ate.pdf", "Ossorio & Navarro-Peris (2023), doi:10.1016/j.applthermaleng.2023.120725"),
    (EVID / "pdfs/guth_atakan_2023_ijrefrig.pdf", "Guth & Atakan (2023), doi:10.1016/j.ijrefrig.2022.10.024"),
    (EVID / "compressor_maps/copeland/xpv_ypv_range.pdf", "Copeland XPV/ZPV variable-speed scroll range sheet (GPC-EN-2024)"),
    (DATA_DIR / "points_copeland_opi.csv", "Copeland OPI — parsed AHRI 540 records (63 machines, 5,531 points)"),
    (EVID / "compressor_maps/highly/highly_catalogue_2024.pdf", "Highly rotary catalogue 2024 (R290 inverter rated points)"),
]
PDF_CAT = [
    (EVID / "catalogs/daikin_rxm_a_databook_eeden24.pdf", "Daikin RXM-A engineering data book, EEDEN24"),
    (EXT_REFS / "Panasonic_Aquarea_2025.pdf", "Panasonic Aquarea 2025 catalogue"),
    (EXT_REFS / "Samsung_EHS_Mono_HT_Quiet_R32.pdf", "Samsung EHS Mono HT Quiet R32 technical data book"),
    (EVID / "catalogs/fujitsu_asuh09lpas_dtm.pdf", "Fujitsu ASUH/AOUH LPAS design & technical manual (held)"),
]


# ---------------------------------------------------------------------------
# prose file
# ---------------------------------------------------------------------------
def observations() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    key: str | None = None
    for raw in (HERE / "notion_observations_final.md").read_text(encoding="utf-8").splitlines():
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
        elif stripped.startswith(("[ ] ", "[x] ")):
            out[key].append({"kind": "todo", "text": stripped[4:], "checked": stripped.startswith("[x]"), "children": []})
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
        elif item["kind"] == "todo":
            blocks.append({"object": "block", "type": "to_do", "to_do": {"rich_text": rt(item["text"]), "checked": item["checked"]}})
        else:
            block = callout(item["text"], "📌")
            if item["children"]:
                block["callout"]["children"] = [para(c) for c in item["children"]]
            blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------
# file uploads (single or multi-part) and embed blocks
# ---------------------------------------------------------------------------
PART = 10 * 1024 * 1024


def upload_any(path: Path) -> str:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    if size <= 19 * 1024 * 1024:
        from validation.compressor_maps._notion import upload

        return upload(path)
    n_parts = (size + PART - 1) // PART
    created = call("POST", "/file_uploads", {"filename": path.name, "content_type": ctype, "mode": "multi_part", "number_of_parts": n_parts})
    with path.open("rb") as fh:
        for i in range(1, n_parts + 1):
            chunk = fh.read(PART)
            boundary = uuid.uuid4().hex
            body = b"".join(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
                    f"Content-Type: {ctype}\r\n\r\n".encode(),
                    chunk,
                    f"\r\n--{boundary}\r\n".encode(),
                    'Content-Disposition: form-data; name="part_number"\r\n\r\n'.encode(),
                    str(i).encode(),
                    f"\r\n--{boundary}--\r\n".encode(),
                ]
            )
            call("POST", f"/file_uploads/{created['id']}/send", raw=body, ctype=f"multipart/form-data; boundary={boundary}")
    done = call("POST", f"/file_uploads/{created['id']}/complete", {})
    if done.get("status") != "uploaded":
        raise SystemExit(f"multi-part upload of {path.name} did not complete: {done}")
    return created["id"]


def attach(path: Path, caption: str, *, enabled: bool = True) -> list[dict]:
    if not path.exists():
        return [callout(f"첨부 누락: {path.name} — {caption}", "⚠️")]
    if not enabled:
        return [bullet(f"{caption} — `{path.name}` ({path.stat().st_size / 1e6:.1f} MB, 첨부 생략)")]
    fid = upload_any(path)
    kind = "pdf" if path.suffix.lower() == ".pdf" else "file"
    block = {"object": "block", "type": kind, kind: {"type": "file_upload", "file_upload": {"id": fid}, "caption": rt(caption)}}
    return [block]


def fig(name: str, caption: str) -> list[dict]:
    p = FIGS / f"{name}.png"
    return [image(p, caption)] if p.exists() else [callout(f"그림 {name} 없음 ({p})", "⚠️")]


def f(v, nd: int = 2) -> str:
    try:
        if v is None or pd.isna(v):
            return "—"
    except TypeError:
        return str(v)
    return f"{float(v):.{nd}f}"


def h2(text: str) -> dict:
    return h(2, text)


def h3(text: str) -> dict:
    return h(3, text)


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------
def build(obs: dict, *, pdf: bool = True) -> list[dict]:
    B: list[dict] = []
    B += render(obs, "headline")

    # 1 ------------------------------------------------------------------
    B += [h(1, "1. 사용 데이터"), h2("1.1 압축기 효율식 fitting 데이터")]
    B += render(obs, "data_fit")
    ds = pd.read_csv(ARCHIVE / "data_sources.csv")
    grp = ds.groupby(["source_id", "comp_type"]).agg(machines=("compressor_key", "nunique"), n=("n_points", "sum"), refs=("refrigerant", lambda s: "/".join(sorted(set(s))))).reset_index()
    label = {"copeland_opi": "Copeland OPI (AHRI 540 maps)", "cuevas_lebrun_2009": "Cuevas & Lebrun 2009", "guth_atakan_2023": "Guth & Atakan 2023", "highly_catalogue_2024": "Highly catalogue 2024", "shao_2004": "Shao et al. 2004"}
    B += [table(["source", "compressor type", "refrigerant", "machines", "points"], [[label.get(r.source_id, r.source_id), r.comp_type, r.refs, str(int(r.machines)), f"{int(r.n):,}"] for r in grp.itertuples()])]
    B += [h3("원본 PDF / 제조사 자료")]
    for i, (p, cap) in enumerate(PDF_FIT):
        B += [para(obs_line(obs, "data_fit_pdfs", i))] if obs_line(obs, "data_fit_pdfs", i) else []
        B += attach(p, cap, enabled=pdf)
    B += [h2("1.2 Heat pump catalog validation 데이터")]
    B += render(obs, "data_catalog")
    B += [h3("사용한 catalog PDF")]
    for i, (p, cap) in enumerate(PDF_CAT):
        B += [para(obs_line(obs, "data_catalog_pdfs", i))] if obs_line(obs, "data_catalog_pdfs", i) else []
        B += attach(p, cap, enabled=pdf)

    # 2 ------------------------------------------------------------------
    c = json.loads((ARCHIVE / "coefficients.json").read_text())
    v, o, s = c["eta_vol"], c["eta_oi"], c["split"]
    B += [h(1, "2. 최종 압축기 효율 모델"), h2("2.1 최종 함수")]
    B += render(obs, "model_intro")
    B += [
        equation(r"\eta_v = 1 - A\,(r_p - 1) - B\,u,\qquad u = \max(0,\,1/n^* - 1)"),
        equation(r"\eta_{is}\,\eta_{em} = g(r_p)\,x(r_p, n^*),\qquad g = A_{oi} - B_{oi}\,r_p - C_{oi}/r_p,\qquad x = 1 - c\,(r_p - 1)\,u"),
        equation(r"\eta_{em} = \eta_{em,ref}\; s(n^*)\; m(r_p),\qquad s = \frac{n^*(1+n_0)}{n^* + n_0},\qquad m = \frac{r_p-1}{r_p-1+p_0}\cdot\frac{2+p_0}{2}"),
        equation(r"\eta_{is} = \frac{\eta_{is}\eta_{em}}{\eta_{em}} = \frac{g(r_p)\,x(r_p,n^*)}{\eta_{em,ref}\,s(n^*)\,m(r_p)}"),
        code(
            "r_p  = p_dis / p_suc               (dew-point saturation pressures)\n"
            "n*   = N / N_rated                 (rated speed: air-to-water 40 rev/s, air-to-air 60 rev/s)\n"
            "u    = max(0, 1/n* - 1)            (one-sided: no bonus above rated speed)\n"
            "hold n* at 2.0 above the data; floors eta_v >= 0.50, eta_is >= 0.30",
            "plain text",
        ),
    ]
    B += [h2("2.2 최종 계수")]
    B += [
        table(
            ["Efficiency", "coefficients"],
            [
                ["$\\eta_v$", f"A = {v['ETA_VOL_A']:.5f}, B = {v['ETA_VOL_B']:.5f}"],
                ["$\\eta_{is}\\eta_{em}$", f"$A_{{oi}}$ = {o['ETA_OI_A']:.4f}, $B_{{oi}}$ = {o['ETA_OI_B']:.5f}, $C_{{oi}}$ = {o['ETA_OI_C']:.4f}, c = {o['ETA_LEAK_C']:.5f}"],
                ["$\\eta_{em}$", f"$\\eta_{{em,ref}}$ = {s['ETA_EM_REF']:.4f} (at $r_p$ 3, $n^*$ 1), $n_0$ = {s['ETA_EM_N0']:.4f}, $p_0$ = {s['ETA_EM_P0']:.4f}"],
                ["$\\eta_{is}$", "위 계수로 결정 (곱 / $\\eta_{em}$)"],
            ],
        ),
        para(f"압축기 한 대를 통째로 제외한 뒤 예측한 검증 오차: $\\eta_v$ {v['loco_wmape_pct']:.2f} %, 곱 {o['loco_wmape_pct']:.2f} %. 정격속도 값을 기준으로 다른 회전수의 효율 변화를 예측한 오차: $\\eta_v$ {v['st_wmape_pct']:.2f} %, 곱 {o['st_wmape_pct']:.2f} % (부록 A)."),
    ]
    B += [h2("2.3 최종 함수의 형태")]
    B += fig("F1_efficiency_vs_speed", "Figure 1. Compressor efficiency vs relative speed $n^*$ at $r_p$ = 2 / 3 / 4.5. (a) $\\eta_v$, (b) $\\eta_{is}$, (c) $\\eta_{em}$. 회색 띠: 두 모델의 속도 하한.")
    B += render(obs, "model_fig")

    # 3 ------------------------------------------------------------------
    B += [h(1, "3. TMHP Simulation Input")]
    B += render(obs, "inputs_intro")
    a = AirSourceHeatPump(hp_capacity=3500.0, ref="R32")
    b = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    B += [h2("3.1 ASHP model input (공기→공기, 3.5 kW R32)")]
    B += [
        table(
            ["Parameter", "Value", "Source"],
            [
                ["Refrigerant", "R32", "Assumed for validation"],
                ["Rated capacity (cooling, ISO 5151 T1)", "3.5 kW", "Assumed for validation"],
                ["Compressor displacement", f"{a.V_cmp_ref * 1e6:.2f} cm³/rev", "TMHP default (derived from rated capacity, §4.1)"],
                ["Rated compressor speed", f"{a.rps_rated:g} rev/s", "TMHP default (Daikin SL manual 52–72 rev/s)"],
                ["Minimum compressor speed", f"{a.rps_min:g} rev/s ($n^*$ {a.rps_min / a.rps_rated:.2f})", "TMHP default"],
                ["Maximum compressor speed", f"{a.rps_max:g} rev/s ($n^*$ {a.rps_max / a.rps_rated:.1f})", "TMHP default"],
                ["Outdoor HX UA (rated)", f"{a.UA_ou_rated:.0f} W/K (= Q/5)", "TMHP default (EN 328 / ENV 327 band)"],
                ["Indoor HX UA (rated)", f"{a.UA_iu_rated:.0f} W/K (= 0.8 × outdoor)", "TMHP default"],
                ["UA vs air flow", "UA ∝ (V/V_rated)^0.65, fan flow 5–100 %", "TMHP default"],
                ["Rated outdoor airflow", f"{a.dV_ou_fan_a_rated:.2f} m³/s (720 m³/h per kW)", "TMHP default"],
                ["Rated indoor airflow", f"{a.dV_iu_fan_a_rated:.2f} m³/s (720 m³/h per kW)", "TMHP default"],
                ["Fan rated power (each)", f"{a.E_ou_fan_rated:.0f} W (60 Pa / 0.6)", "TMHP default"],
                ["Superheat / subcooling", "3 K / 3 K", "TMHP default"],
                ["Room temperature", "Heating 20 °C / Cooling 27 °C", "simulation condition"],
                ["Outdoor temperature", "Heating −15…15 °C / Cooling 20…45 °C", "simulation condition"],
                ["Compressor efficiencies", f"{ce.COEFFICIENT_VERSION} (§2)", "TMHP default"],
            ],
        )
    ]
    B += render(obs, "inputs_ashp_after")
    B += [h2("3.2 ASHPB model input (공기→물, 9 kW R32)")]
    B += [
        table(
            ["Parameter", "Value", "Source"],
            [
                ["Refrigerant", "R32", "Assumed for validation"],
                ["Rated capacity (heating, EN 14511 A7/W35)", "9.0 kW", "Assumed for validation"],
                ["Compressor displacement", f"{b.V_cmp_ref * 1e6:.2f} cm³/rev", "TMHP default (derived from rated capacity, §4.1)"],
                ["Rated compressor speed", f"{b.rps_rated:g} rev/s", "TMHP default (Panasonic 9 units inverted: median 42)"],
                ["Minimum compressor speed", f"{b.rps_min:g} rev/s ($n^*$ {b.rps_min / b.rps_rated:.3f})", "TMHP default"],
                ["Maximum compressor speed", f"{b.rps_max:g} rev/s", "TMHP default"],
                ["Tank (condenser) HX UA", f"{b.UA_tank_hx:.0f} W/K (= Q/5)", "TMHP default"],
                ["Outdoor HX UA (rated)", f"{b.UA_ou_rated:.0f} W/K (= 0.7 × tank HX)", "TMHP default"],
                ["Rated outdoor airflow", f"{b.dV_fan_a_rated:.2f} m³/s (540 m³/h per kW)", "TMHP default"],
                ["Fan rated power", f"{b.E_fan_rated:.0f} W (60 Pa / 0.6)", "TMHP default"],
                ["Water / tank temperature", "tank 42.5 °C (= leaving water 45 °C − 2.5 K)", "simulation condition"],
                ["Water flow / condenser approach", "no loop flow; ΔT = Q / UA_tank (closed form), Q = requested duty while modulating and delivered duty at a speed bound (§7.3)", "TMHP default"],
                ["Superheat / subcooling", "5 K / 5 K", "TMHP default"],
                ["Outdoor temperature", "−15…15 °C (map), 7 °C and −7 °C/32.5 °C (base cases)", "simulation condition"],
                ["Compressor efficiencies", f"{ce.COEFFICIENT_VERSION} (§2)", "TMHP default"],
            ],
        )
    ]
    B += render(obs, "inputs_ashpb_after")

    # 4 ------------------------------------------------------------------
    B += [h(1, "4. Compressor Displacement 검증"), h2("4.1 TMHP에서 displacement를 어떻게 결정하는가")]
    B += [
        code(
            "Rated capacity Q_nom (nameplate), refrigerant\n"
            "      ↓  rating point per class: air-to-water EN 14511 A7/W35 → T_evap 1 °C / T_cond 40 °C, N_rated 40 rev/s\n"
            "                                 air-to-air   ISO 5151 T1    → T_evap 10 °C / T_cond 50 °C, N_rated 60 rev/s\n"
            "      ↓  CoolProp at that point: suction density ρ1 (superheat 5 K), Δh of the nameplate duty (subcooling 5 K, η_is 0.70)\n"
            "m_dot  = Q_nom / Δh\n"
            "V_disp = m_dot / (ρ1 · η_vol · N_rated),   η_vol = 0.90\n"
            "(src/tmhp/compressor_speed.py::default_displacement)",
            "plain text",
        )
    ]
    B += render(obs, "disp_logic")
    B += [h2("4.2 TMHP default vs catalog displacement")]
    B += fig("F2_displacement_parity", "Figure 2. Compressor displacement parity. (a) heat-pump level: TMHP default from nameplate vs manufacturer-published displacement (Panasonic 9 units, air-to-water). (b) compressor level: the same rule evaluated at each compressor's own rated point and speed (Copeland 51, Highly 8). 1:1 line, ±10 % and ±20 % bands.")
    B += render(obs, "disp_parity")
    B += [h2("4.3 displacement가 PLR 하한에 미치는 영향")]
    B += fig("F3_displacement_sensitivity", "Figure 3. Displacement sensitivity. (a) lowest deliverable PLR vs displacement multiplier (three cases). (b, c) relative speed vs requested PLR at ×0.8 / ×1.0 / ×1.2; hollow = compressor at minimum speed, dotted line = $n^*_{min}$.")
    B += render(obs, "disp_sens")

    # 5 ------------------------------------------------------------------
    B += [h(1, "5. Catalog COP Validation")]
    B += fig("F4_cop_parity", "Figure 4. Catalogue COP vs TMHP COP with library defaults unchanged. (a) air-to-air, Daikin RXM-A 5 units (cooling/heating). (b) air-to-water, Panasonic 9 + Samsung 1 units by refrigerant. 1:1 line, ±10 % band; rings = rated points.")
    B += render(obs, "cop_parity")

    # 6 ------------------------------------------------------------------
    B += [h(1, "6. Final PLR-COP Result"), h2("6.1 ASHP — 외기온도별 PLR-COP")]
    B += fig("F5_ashp_plr_cop_map", "Figure 5. ASHP 3.5 kW R32: system $\\mathrm{COP}$ vs requested PLR by outdoor temperature. (a) heating, room 20 °C; (b) cooling, room 27 °C. Hollow circles: compressor at minimum speed (delivered > requested).")
    B += render(obs, "plr_ashp")
    B += [h2("6.2 ASHPB — 외기온도별 PLR-COP")]
    B += fig("F6_ashpb_plr_cop_map", "Figure 6. ASHPB 9 kW R32, tank 42.5 °C: system $\\mathrm{COP}$ vs requested PLR by outdoor temperature. Hollow circles: compressor at minimum speed.")
    B += render(obs, "plr_ashpb")

    # 7 ------------------------------------------------------------------
    B += [h(1, "7. Minimum-speed 이후 ASHP vs ASHPB 거동 진단과 수정"), h2("7.1 코드 로직 대조")]
    B += render(obs, "diag_intro")
    B += [
        table(
            ["질문", "ASHP (공기→공기)", "ASHPB (공기→물)"],
            [
                ["최소속도 도달 후 compressor speed", "`solve_compressor_speed`: 요청이 하한 공급열량보다 작으면 $N$ = $N_{min}$ 15 rev/s로 clamp, `capacity_clamped = \"min\"`", "동일 함수, 동일 처리"],
                ["delivered capacity", "$\\dot m(N_{min}) \\cdot \\Delta h$ — 요청과 무관", "동일"],
                ["요청이 더 줄면 solver 목적함수", "specific energy $E_{tot}/Q_{delivered} \\cdot Q_{request}$ (= 1/COP 최소화), 2-D Nelder–Mead: 증발·응축 접근온도", "같은 목적함수, 1-D bounded Brent: 증발 접근온도만"],
                ["응축측 접근온도", "최적화 변수 (1–20 K)", "닫힌 식 $\\Delta T = Q/UA_{tank}$. 변조 구간은 $Q$ = 요청열량, 속도 하한·상한에서는 공급열량으로 재수렴 (§7.3)"],
                ["fan flow", "실외·실내 팬 풍량을 ε-NTU로 풀어 공급열량을 처리 (5–100 %)", "실외 팬만, 동일"],
                ["water flow", "해당 없음", "정상상태 sweep에는 없음 (`m_dot_w = None`)"],
                ["HX UA / effectiveness", "정격 UA × (풍량비)^0.65", "동일"],
                ["source / load-side temperature", "외기·실내 고정", "외기·탱크 고정"],
                ["over-delivered load 처리", "보고만 (`delivered > request`), 사이클링 미모델", "동일"],
                ["COP numerator", "delivered heat $Q_{ref,iu}$", "delivered heat $Q_{ref,tank}$"],
                ["COP denominator", "$E_{cmp} + E_{fan,ou} + E_{fan,iu}$", "$E_{cmp} + E_{fan,ou}$ (펌프 동력 없음)"],
            ],
        )
    ]
    B += [h2("7.2 진단용 그래프")]
    B += fig("F7_min_speed_diagnostic", "Figure 7. Minimum-speed diagnostic for four base cases. (a) requested vs delivered PLR, (b) relative speed, (c) delivered capacity, (d) compressor power, (e) fan power, (f) saturation temperatures. Hollow: compressor at minimum speed.")
    B += render(obs, "diag_fig")
    B += [h2("7.3 수정: 속도 하한에서의 응축기 closure")]
    B += render(obs, "fix_intro")
    B += [
        code(
            "if capacity_clamped:                      # N = N_min (or N_max)\n"
            "    hold the compressor at that speed\n"
            "    unknown  : dT_ref_tank\n"
            "    residual : Q_delivered(dT) - K_tank * dT      # K_tank = UA_tank_hx\n"
            "                                                  #        = C_w * eps  (m_dot_w > 0)\n"
            "    brentq over dT, then re-evaluate the cycle at the root\n"
            "(src/tmhp/air_source_heat_pump_boiler.py::_calc_state)",
            "plain text",
        )
    ]
    B += fig("F8_speed_floor_closure", "Figure 8. Speed-floor closure of the air-to-water condenser, before and after. (a) requested vs delivered PLR, (b) relative speed with both floors, (c–f) condensing temperature, pressure ratio, system $\\mathrm{COP}$ and compressor power of the 9 kW R32 case at 7/42.5 °C — dashed grey = requested-duty closure (before), solid = delivered-duty closure (final). (g) outdoor coil relative to full load. (h) closure residual $|Q_{del} - UA_{tank}\\Delta T|$. Hollow markers: compressor at minimum speed.")
    B += render(obs, "fix_result")
    B += [h2("7.4 최종 판정")]
    B += render(obs, "diag_verdict")

    # 8 ------------------------------------------------------------------
    B += [h(1, "8. 남아 있는 ASHP 저부하 COP 상승 문제")]
    B += fig("F9_ashp_lowload_decomposition", "Figure 9. ASHP low-load $\\mathrm{COP}$ decomposition. (a, b) final model vs heat-exchangers-only (three efficiencies frozen at PLR 100 %) and compressor-only $\\mathrm{COP}$ = $Q/E_{cmp}$. (c) heat-exchanger gain, compressor penalty and net change vs PLR 100 %.")
    B += render(obs, "lowload_fig")
    B += [h3("핵심 질문에 대한 답")]
    B += render(obs, "lowload_questions")

    # 9 ------------------------------------------------------------------
    B += [h(1, "9. 최종 결론")]
    B += render(obs, "conclusions")

    # Appendix ------------------------------------------------------------
    B += [divider(), h(1, "Appendix A. 회전수 변화 검증")]
    B += render(obs, "appendix_a")
    B += fig("FA1_speed_transfer", "Figure A1. Speed-transfer check on the compressor data: change of efficiency between a machine's rated-speed record and its lower-speed records at the same evaporating/condensing condition, measured vs predicted by the final functions. (a) product $\\eta_{is}\\eta_{em}$, bin medians per source; (b) Copeland by $n^*$ bin against $r_p$; (c) $\\eta_v$.")
    B += [h(1, "Appendix B. 전체 PLR Metric")]
    B += render(obs, "appendix_b")
    m = pd.read_csv(SIM / "appendix_plr_metrics.csv")
    m = m.sort_values(["model_class", "duty", "refrigerant", "t_sink_C", "t_outdoor_C"])
    rows = []
    for r in m.itertuples():
        rows.append(
            [
                f"{r.model_class} {r.duty} {r.refrigerant} {r.capacity_W / 1000:g} kW",
                f"{r.t_outdoor_C:g} / {r.t_sink_C:g}",
                r.shape,
                f(r.COP_rated),
                f"{100 * r.PLR_peak:.0f}",
                f(r.COP_peak),
                f"{100 * r.PLR_low:.0f}",
                f(r.COP_low),
                f"{100 * r.dCOP_low:.1f}",
                f"{100 * r.plr_floor_delivered:.0f}" if pd.notna(r.plr_floor_delivered) else "—",
                f"{r.n_star_low:.2f}–{r.n_star_rated:.2f}",
                f"{r.pr_low:.2f}–{r.pr_rated:.2f}",
            ]
        )
    B += [
        table(
            ["case", "$T_o$ / $T_{sink}$ [°C]", "shape", "COP@100 %", "PLR_peak [%]", "COP_peak", "PLR_low [%]", "COP_low", "ΔCOP_low [%]", "min-speed PLR [%]", "$n^*$ range", "$r_p$ range"],
            rows,
        ),
        para("PLR_low = 연속 변조 최저점, ΔCOP_low = (COP_peak − COP_low)/COP_peak, min-speed PLR = 속도 하한에서 실제 공급되는 PLR. shape: monotonic_rise = 하한까지 상승, plateau = 최대점이 안에 있으나 ΔCOP_low < 2 %, interior_peak = 뚜렷한 내부 최대점."),
        divider(),
        para(f"산출물: validation/coefficients/{ce.COEFFICIENT_VERSION}/figures/final/ (그림 SVG·PNG), validation/data/final_report/ (그림별 source CSV·시뮬레이션 결과), validation/results/ (패리티), validation/data/fixed_boundary_plr/decomposition.csv (효율 고정 분해)."),
    ]
    return B


def obs_line(obs: dict, key: str, i: int) -> str | None:
    items = obs.get(key, [])
    return items[i]["text"] if i < len(items) else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--page", default=DEFAULT_PAGE)
    ap.add_argument("--replace", action="store_true", help="delete the page's existing child blocks first")
    ap.add_argument("--no-pdf", action="store_true", help="list the attachments instead of uploading them")
    ap.add_argument("--dry-run", action="store_true", help="build the block list and print a summary, no API writes")
    a = ap.parse_args()
    obs = observations()
    if a.dry_run:
        # no uploads in a dry run: monkeypatch attach/image to placeholders
        global attach, image  # noqa: PLW0603
        attach = lambda p, c, enabled=True: [para(f"[attach] {p.name} — {c}")]  # noqa: E731
        image = lambda p, c: para(f"[image] {p.name} — {c}")  # noqa: E731
        blocks = build(obs, pdf=False)
        print(f"{len(blocks)} blocks")
        for b_ in blocks:
            t = b_["type"]
            txt = b_[t].get("rich_text") if isinstance(b_[t], dict) else None
            print(" ", t, ("".join(x.get("text", {}).get("content", x.get("equation", {}).get("expression", "")) for x in txt)[:110] if txt else ""))
        return
    blocks = build(obs, pdf=not a.no_pdf)
    if a.replace:
        for b_ in children_all(a.page):
            call("DELETE", f"/blocks/{b_['id']}")
    call("PATCH", f"/pages/{a.page}", {"properties": {"title": {"title": [{"type": "text", "text": {"content": TITLE}}]}}})
    append(a.page, blocks)
    print(f"published {len(blocks)} blocks to {a.page}")


if __name__ == "__main__":
    main()
