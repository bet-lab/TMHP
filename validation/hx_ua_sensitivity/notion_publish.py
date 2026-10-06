"""Publish the heat-exchanger UA sensitivity report to its Notion page.

Structure (headings, tables, figures) is built here; prose lives in
``notion_observations.md`` (one ``## <key>`` block per section: plain line =
paragraph, ``- `` bullet, ``> `` callout with indented child lines).

Run after ``simulate`` and ``figures``::

    uv run python3 -m validation.hx_ua_sensitivity.notion_publish --replace [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from validation.compressor_maps._notion import (
    append,
    bullet,
    call,
    callout,
    children_all,
    code,
    divider,
    h,
    image,
    para,
    rt,
    table,
)
from validation.hx_ua_sensitivity.simulate import JOINT_SCALES, OUT_DIR

HERE = Path(__file__).resolve().parent
DEFAULT_PAGE = "3ea6947d125d80d1b1ebe10092bb1103"
TITLE = "UA 값 크기에 따른 PLR-COP 검증 (v26-09-29)"

#: (figure stem, Notion heading, prose key, caption)
FIGURES = {
    "heating": [
        ("H1_cop_vs_plr", "Figure H1. COP vs PLR — UA sensitivity", "h1", "난방 시스템 COP. (a) 절대값, (b) 공통 PLR에서 정규화한 형상. 채운 마커 = 연속 변조, 빈 마커 = 압축기 속도 하한."),
        ("H2_temperatures_vs_plr", "Figure H2. Refrigerant temperature / approach temperature vs PLR", "h2", "난방 냉매 포화온도(a)와 접근온도(b). 실선 = 응축측, 파선 = 증발측."),
        ("H3_pressure_ratio_vs_plr", "Figure H3. Pressure ratio vs PLR", "h3", "난방 압축기 압력비(a)와 상대속도 $n^*$(b)."),
        ("H4_compressor_vs_plr", "Figure H4. Compressor efficiency / power vs PLR", "h4", "난방 압축기 효율 3종과 소비전력. UA가 작을수록(압력비가 높을수록) $\\eta_{is}$가 오히려 높다."),
        ("H5_cop_definition", "Figure H5. $COP_{cmp}$ vs $COP_{sys}$", "h5", "난방 COP 정의 2종. 파선 = $Q/W_{cmp}$, 실선 = $Q/(W_{cmp}+W_{fan})$."),
        ("H6_power_split", "Figure H6. Compressor / fan power + fan share", "h6", "난방 출하 UA에서의 전력 구성(면적)과 팬 비중(오른쪽 축)."),
        ("H7_coil_split", "Figure H7. Outdoor vs indoor coil", "h7", "난방에서 한쪽 코일만 바꾼 경우의 시스템 COP."),
    ],
    "cooling": [
        ("C1_cop_vs_plr", "Figure C1. COP vs PLR — UA sensitivity", "c1", "냉방 시스템 COP. (a) 절대값, (b) 공통 PLR에서 정규화한 형상."),
        ("C2_temperatures_vs_plr", "Figure C2. Refrigerant temperature / approach temperature vs PLR", "c2", "냉방 냉매 포화온도(a)와 접근온도(b). 실선 = 응축측, 파선 = 증발측."),
        ("C3_pressure_ratio_vs_plr", "Figure C3. Pressure ratio vs PLR", "c3", "냉방 압축기 압력비(a)와 상대속도 $n^*$(b)."),
        ("C4_compressor_vs_plr", "Figure C4. Compressor efficiency / power vs PLR", "c4", "냉방 압축기 효율 3종과 소비전력."),
        ("C5_cop_definition", "Figure C5. $COP_{cmp}$ vs $COP_{sys}$", "c5", "냉방 COP 정의 2종."),
        ("C6_power_split", "Figure C6. Compressor / fan power + fan share", "c6", "냉방 출하 UA에서의 전력 구성과 팬 비중."),
        ("C7_coil_split", "Figure C7. Outdoor vs indoor coil", "c7", "냉방에서 한쪽 코일만 바꾼 경우의 시스템 COP."),
    ],
}


# ---------------------------------------------------------------------------
# prose file
# ---------------------------------------------------------------------------
def observations() -> dict[str, list[dict]]:
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
            block = callout(item["text"], "📌")
            if item["children"]:
                block["callout"]["children"] = [para(c) for c in item["children"]]
            blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def _metrics() -> pd.DataFrame:
    return pd.read_csv(OUT_DIR / "shape_metrics.csv")


def conditions_table(m: pd.DataFrame) -> dict:
    d = pd.read_csv(OUT_DIR / "hx_ua_sensitivity_once.csv")
    base = d[d["pass"] == "default"]
    ua_ou = float(base.UA_ou_rated.iloc[0])
    ua_iu = float(base.UA_iu_rated.iloc[0])
    return table(
        ["항목", "값"],
        [
            ["모델 / 냉매 / 정격용량", "AirSourceHeatPump (공기→공기) · R32 · 3.5 kW"],
            ["난방 조건", "외기 7 °C / 실내 20 °C"],
            ["냉방 조건", "외기 35 °C / 실내 27 °C"],
            ["PLR 격자", "요청 열량 1.00 → 0.12, 0.04 간격 (23점)"],
            ["기준 UA (출하값)", f"실외 {ua_ou:.0f} W/K · 실내 {ua_iu:.0f} W/K (= 정격용량/5, 그 0.8배)"],
            ["동시 배율 (Figure 1–6)", " · ".join(f"{s:g}x" for s in JOINT_SCALES)],
            ["개별 배율 (Figure 7)", "실외 0.5x / 2x · 실내 0.5x / 2x"],
            ["고정한 것", "풍량 의존식 $UA = UA_{rated}(V/V_{rated})^{0.65}$ · 압축기 계수 · 접근온도 탐색 영역 (1–20 K)"],
            ["압축기 계수 버전", str(base.coefficient_version.iloc[0])],
            ["총 해석점 / 미수렴", f"{len(d)}점 / {int((d.failure_reason != 'none').sum())}점"],
        ],
    )


def shape_table(m: pd.DataFrame) -> dict:
    rows = []
    for duty in ("heating", "cooling"):
        for s in JOINT_SCALES:
            g = m[(m.duty == duty) & (m.pass_name == "joint") & (m.scale_ou == s)]
            if g.empty:
                continue
            r = g.iloc[0]
            rise = 100.0 * (r.COP_low_over_rated - 1.0)
            rows.append(
                [
                    "난방" if duty == "heating" else "냉방",
                    f"{s:g}x",
                    f"{r.COP_rated:.2f}",
                    f"{100 * r.PLR_low:.0f} %",
                    f"{r.COP_low:.2f}",
                    f"+{rise:.1f} %",
                    f"{r.pr_rated:.2f} → {r.pr_low:.2f}",
                    f"{r.eta_isen_rated:.3f} → {r.eta_isen_low:.3f}",
                    str(r.shape),
                ]
            )
    return table(
        ["운전", "UA 배율", "정격 COP", "최저 변조 PLR", "그 지점 COP", "상승폭", "압력비 (정격→저부하)", "$\\eta_{is}$ (정격→저부하)", "형상"],
        rows,
    )


def coil_table(m: pd.DataFrame) -> dict:
    label = {
        "joint_1": "출하값",
        "ou_0.5_iu_1": "실외 0.5x",
        "ou_2_iu_1": "실외 2x",
        "ou_1_iu_0.5": "실내 0.5x",
        "ou_1_iu_2": "실내 2x",
    }
    rows = []
    for duty in ("heating", "cooling"):
        for variant, name in label.items():
            g = m[(m.duty == duty) & (m.variant == variant)]
            if g.empty:
                continue
            r = g.iloc[0]
            rows.append(
                [
                    "난방" if duty == "heating" else "냉방",
                    name,
                    f"{r.COP_rated:.2f}",
                    f"+{100 * (r.COP_low_over_rated - 1.0):.1f} %",
                    f"{r.approach_evap_rated_K:.1f} / {r.approach_cond_rated_K:.1f}",
                    str(r.shape),
                ]
            )
    return table(
        ["운전", "케이스", "정격 COP", "저부하 상승폭", "정격 접근온도 증발/응축 [K]", "형상"],
        rows,
    )


# ---------------------------------------------------------------------------
def fig_blocks(obs: dict, duty: str, *, with_images: bool) -> list[dict]:
    blocks: list[dict] = []
    for stem, heading, key, caption in FIGURES[duty]:
        blocks.append(h(3, heading))
        png = OUT_DIR / f"{stem}.png"
        if with_images:
            blocks.append(image(png, caption))
        else:
            blocks.append(para(f"[image] {png.name} — {caption}"))
        blocks += render(obs, key)
    return blocks


def build(obs: dict, *, with_images: bool = True) -> list[dict]:
    m = _metrics()
    blocks: list[dict] = []
    blocks += render(obs, "headline")

    blocks.append(h(2, "1. 검증 목적"))
    blocks += render(obs, "purpose")
    blocks.append(
        callout(
            "검증한 전달 경로:  UA → 접근온도 → 압력비 → 압축기 효율·동력 → COP",
            "🔗",
        )
    )

    blocks.append(h(2, "2. 분석 조건"))
    blocks.append(conditions_table(m))
    blocks += render(obs, "conditions_note")

    blocks.append(divider())
    blocks.append(h(2, "3. Heating 결과"))
    blocks += fig_blocks(obs, "heating", with_images=with_images)

    blocks.append(divider())
    blocks.append(h(2, "4. Cooling 결과"))
    blocks += fig_blocks(obs, "cooling", with_images=with_images)

    blocks.append(divider())
    blocks.append(h(2, "5. 종합 해석"))
    blocks.append(h(3, "형상 판정표 (동시 배율)"))
    blocks.append(shape_table(m))
    blocks.append(h(3, "코일 개별 배율"))
    blocks.append(coil_table(m))
    blocks.append(h(3, "질문별 답"))
    blocks += render(obs, "synthesis_q")

    blocks.append(h(2, "6. 결론"))
    blocks += render(obs, "conclusion")

    blocks.append(h(2, "7. 한계와 주의"))
    blocks += render(obs, "caveats")

    blocks.append(h(2, "8. 재현"))
    blocks.append(
        code(
            "uv run python3 -m validation.hx_ua_sensitivity.simulate --jobs 24\n"
            "uv run python3 -m validation.hx_ua_sensitivity.figures\n"
            "uv run python3 -m validation.hx_ua_sensitivity.notion_publish --replace",
            "bash",
        )
    )
    blocks.append(
        para(
            "결과물: validation/results/hx_ua_sensitivity_once/ "
            "(hx_ua_sensitivity_once.csv · shape_metrics.csv · 그림별 source CSV · PNG/SVG)"
        )
    )
    blocks.append(
        callout(
            "이 페이지는 notion_publish.py가 생성한다. 손으로 추가한 블록은 다음 --replace 실행에서 지워진다.",
            "⚠️",
        )
    )
    return blocks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--page", default=DEFAULT_PAGE)
    ap.add_argument("--replace", action="store_true", help="delete the page's existing child blocks first")
    ap.add_argument("--dry-run", action="store_true", help="build the block list and print a summary, no API writes")
    a = ap.parse_args()
    obs = observations()

    if a.dry_run:
        blocks = build(obs, with_images=False)
        print(f"{len(blocks)} blocks")
        for b in blocks:
            t = b["type"]
            body = b[t]
            txt = body.get("rich_text") if isinstance(body, dict) else None
            rendered = (
                "".join(
                    x.get("text", {}).get("content", x.get("equation", {}).get("expression", "")) for x in txt
                )[:110]
                if txt
                else ""
            )
            print(f"  {t:22s} {rendered}")
        return

    existing = children_all(a.page)
    if existing and not a.replace:
        raise SystemExit(f"page already has {len(existing)} blocks; pass --replace to rebuild it")
    blocks = build(obs, with_images=True)
    for b in existing:
        call("DELETE", f"/blocks/{b['id']}")
    call("PATCH", f"/pages/{a.page}", {"properties": {"title": {"title": [{"type": "text", "text": {"content": TITLE}}]}}})
    append(a.page, blocks)
    print(f"published {len(blocks)} blocks to {a.page}")


if __name__ == "__main__":
    main()
