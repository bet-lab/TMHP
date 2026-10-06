"""Write the pilot summary to the Notion page "Compressor 3 efficiency" (TMHP ledger row).

usage: uv run python3 notion_summary.py [--replace]
--replace deletes every child block of the page first (the page holds only this summary).
Numbers are read from regression/*.csv so a rerun after a refit stays consistent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from validation.compressor_maps._notion import (  # noqa: E402
    append,
    bullet,
    call,
    callout,
    children_all,
    divider,
    h,
    image,
    para,
    table,
)

PAGE = "3ee6947d125d80f4830ffcb408fa647d"
D = Path(__file__).resolve().parents[1]
REG, FIG = D / "regression", D / "figures"
ISSUE = "https://github.com/bet-lab/TMHP/issues/81"


def pct(x):
    return f"{x:.2f} %"


def build() -> list[dict]:
    g = pd.read_csv(REG / "grouping_comparison.csv")
    g = g[(g.speed_var == "nstar") & (g.model == "C3")]
    coef = pd.read_csv(REG / "regression_coefficients.csv")
    sel = coef[(coef.level == "B") & (coef.speed_var == "nstar") & (coef.model == "C3")]
    low = pd.read_csv(REG / "lowspeed_ratios.csv")
    tm = pd.read_csv(REG / "tmhp_default_speed_ratios.csv")

    b: list[dict] = []
    b.append(
        callout(
            "BITZER Software 계산값으로 TMHP 압축기 세 효율을 $r_p$와 $n^*$의 함수로 회귀할 수 있는지 본 pilot이다. "
            "결론: $\\eta_v$와 곱 $\\eta_{is}\\eta_{em}$은 식별되고 형식별 2차식으로 LOCO 오차 2~7 % 안에서 설명된다. "
            "$\\eta_{is}$와 $\\eta_{em}$의 분리는 BITZER만으로는 안 된다. 권고 grouping은 압축기 형식별이다. "
            "TMHP 코드와 기본 계수는 바꾸지 않았다.",
            "🧭",
        )
    )
    b.append(
        para(
            f"작업 기록: GitHub 이슈 #81 ({ISSUE}) · 브랜치 feat/bitzer-efficiency-pilot · "
            "폴더 validation/bitzer_efficiency_pilot/ (README에 전체 보고서)"
        )
    )

    b.append(h(2, "1. 무엇을 했나"))
    for t in [
        "BITZER Software 웹판 v7.1.11.4(2026-10-04). Chrome에서 웹 UI 통신을 기록해 계산 API를 찾았고, 같은 백엔드를 익명 세션으로 "
        "호출해 화면과 같은 값을 받았다(1점 대조 일치).",
        "인벤토리 102대 중 속도 제어가 되는 것: ORBIT·ORBIT+·ORBIT FIT 스크롤, VARISPEED 왕복동(내장 인버터), HS 스크루. "
        "ORBIT Boreal 10대는 50/60 Hz 고정속이라 제외했다.",
        "pilot 19대: 스크롤 R410A 5대, R32 4대, R454B 4대(R32와 같은 하드웨어), 왕복동 R134a 3대, 스크루 R134a 3대.",
        "격자: SST −15~5 °C(5점) × SDT 35~55 °C(5점) × 주파수 9점, SH 10 K, SC 0 K. 4,275회 요청 중 운전범위 안 3,604점. "
        "범위 밖 671점은 메시지와 함께 원자료에 남겼다.",
    ]:
        b.append(bullet(t))

    b.append(h(2, "2. 효율 정의 (TMHP 코드와 같음)"))
    for t in [
        "$r_p = P_2/P_1$, $P_1 = P_{sat,dew}(SST)$, $P_2 = P_{sat,dew}(SDT)$, 흡입 $T_1 = SST + 10$ K, CoolProp HEOS",
        "$\\eta_v = \\dot m / (\\rho_1 V_d N)$, $N = f \\cdot rpm_{50}/50$ (가정, 슬립 비 고정)",
        "$\\eta_{is} = (h_{2s}-h_1)/(h_2-h_1)$, $h_2 = h(P_2, T_{dis})$, $T_{dis}$ = BITZER 「Discharge gas temp. w/o cooling」",
        "$\\eta_{em} = \\dot m (h_2-h_1)/P_{el}$ — TMHP의 $E_{cmp} = \\dot m(h_2-h_1)/\\eta_{em}$과 같은 정의",
        "곱 $\\eta_{is}\\eta_{em} = \\dot m (h_{2s}-h_1)/P_{el}$ — 토출온도 없이 식별된다",
        "속도: 주파수 $f$를 원자료로 두고 $n^* = f/50$ Hz. 모든 기계가 50 Hz 기준이라 $f$ 회귀와 $n^*$ 회귀는 같은 모델이다.",
    ]:
        b.append(bullet(t))

    b.append(h(2, "3. 핵심 판정: 세 효율 중 둘만 식별된다"))
    b.append(
        para(
            "BITZER 결과에서 응축기 용량 = 냉동능력 + 소비전력이 모든 유효점에서 0.1 % 안에서 맞는다. 토출온도로 계산한 "
            "$\\dot m(h_2-h_1)/P_{el}$은 스크롤 0.99~1.00, 반밀폐 왕복동·스크루 0.97로 형식마다 거의 상수다. "
            "토출온도가 실측 상태가 아니라 에너지 수지에 고정 열손실을 두고 낸 값이라는 뜻이다. 그래서 이 값은 모터 효율이 아니고, "
            "$\\eta_{is}$는 곱을 이 상수로 나눈 값일 뿐이다."
        )
    )
    b.append(
        table(
            ["효율", "분류", "조건"],
            [
                [
                    "η_v",
                    "B. usable with stated assumptions",
                    "N ∝ f(50 Hz 슬립 비). 저압력비에서 최대 +3 % 편향(η_v > 1 174점, flag)",
                ],
                ["η_is·η_em (곱)", "B. usable with stated assumptions", "압축기 단자 전력, 외부 인버터 손실 제외"],
                ["η_is", "C. not identifiable from BITZER alone", "곱 ÷ 형식별 상수"],
                [
                    "η_em",
                    "C. not identifiable from BITZER alone",
                    "에너지 수지 열손실 비율. TMHP η_em(구동·모터 손실)과 뜻이 다르다",
                ],
            ],
        )
    )
    b.append(
        image(
            FIG / "eta_em_scatter_scroll_R410A.png",
            "η_em raw, scroll / R410A: 운전점·속도와 무관하게 0.99 근처에 붙는다(색 범위 0.987~0.997).",
        )
    )

    b.append(h(2, "4. 회귀와 그룹 비교"))
    b.append(
        para(
            "후보 C1 평면, C2 상호작용, C3 2차 반응면. 교차검증은 압축기 통째로 빼는 Leave-One-Compressor-Out. "
            "ST는 뺀 기계의 50 Hz 점으로 수준만 맞춘 뒤 다른 속도를 예측한 오차로, TMHP가 정격점으로 수준을 잡고 상관식이 형상을 맡는 구조와 같다."
        )
    )
    rows = []
    for eff, lab in (("eta_v", "η_v"), ("eta_oi", "η_is·η_em")):
        r = g[g.efficiency == eff].set_index("level")
        rows.append([lab] + [f"{pct(r.loc[lv, 'cv_MAPE'])} (ST {pct(r.loc[lv, 'st_MAPE'])})" for lv in ("A", "B", "C")])
    b.append(table(["효율 (C3, LOCO MAPE)", "A global", "B 형식별", "C 형식×냉매"], rows))
    b.append(
        para(
            "형식으로 나누면 오차가 1/1.5로 줄고, 냉매로 더 나눠도 줄지 않는다. 한 기계 안에서는 (r_p, n*) 2차식 RMSE가 "
            "η_v 0.003~0.019, 곱 0.009~0.021로 작다. 남는 오차는 기계 사이 수준 차다(잔차가 기계별로 위아래로 갈림)."
        )
    )
    crow = []
    for _, r in sel[sel.efficiency.isin(["eta_v", "eta_oi"])].iterrows():
        crow.append(
            [("η_v" if r.efficiency == "eta_v" else "η_is·η_em"), r.group]
            + [f"{r[f'a{i}']:.5f}" for i in range(6)]
            + [f"{int(r.n_compressors)} / {int(r.n_points)}", pct(r.CV_MAPE), pct(r.ST_MAPE)]
        )
    b.append(para("선택 모델: 형식별 C3, $\\eta = a_0 + a_1 r_p + a_2 n^* + a_3 r_p^2 + a_4 n^{*2} + a_5 r_p n^*$"))
    b.append(table(["효율", "형식", "a0", "a1", "a2", "a3", "a4", "a5", "기계/점", "LOCO", "ST"], crow))
    b.append(
        para(
            "적용 범위: 스크롤 n* 0.7–1.5, r_p 2.3–7.1 / 왕복동 n* 0.5–1.74, r_p 2.5–9.1 / 스크루 n* 0.5–1.5, r_p 2.5–9.1. 밖으로 외삽하지 않는다."
        )
    )
    for f, cap in [
        (
            "eta_v_scatter_scroll_R410A.png",
            "η_v raw, scroll / R410A 5대 (마커=기계, 같은 격자점에서 겹치지 않게 ±0.012 오프셋)",
        ),
        (
            "eta_v_heatmap_scroll_R410A.png",
            "η_v 적합 C3 + raw 위치. 데이터 convex hull 밖은 비움, 색 범위는 raw와 같다",
        ),
        ("eta_oi_scatter_scroll_R410A.png", "η_is·η_em raw, scroll / R410A"),
        ("eta_oi_heatmap_scroll_R410A.png", "η_is·η_em 적합 C3, scroll / R410A"),
        ("eta_oi_heatmap_screw_R134a.png", "η_is·η_em 적합 C3, screw / R134a: 저속·고압력비에서 급감"),
    ]:
        b.append(image(FIG / f, cap))

    b.append(h(2, "5. 저속 거동"))
    b.append(para("같은 SST/SDT에서 $\\eta(n^*)/\\eta(n^*=1)$의 중앙값. 모든 그룹에서 저속 손실이 나타난다."))
    lrows = []
    for grp, s in low.groupby("group"):
        s = s.set_index("normalized_speed")
        lo, hi = s.index.min(), s.index.max()
        p07 = s.loc[0.7, "eta_oi_rel_median"] if 0.7 in s.index else float("nan")
        t = tm[(tm.group == grp) & (tm.normalized_speed.round(3) == 0.7)]
        tmv = f"{t.tmhp_product_rel_without_drive.iloc[0]:.3f}" if len(t) else "-"
        lrows.append(
            [
                grp.replace("_", " / "),
                f"{s.loc[lo, 'eta_v_rel_median']:.3f} ({lo:g})",
                f"{p07:.3f}",
                tmv,
                f"{s.loc[lo, 'eta_oi_rel_median']:.3f} ({lo:g})",
                f"{s.loc[hi, 'eta_oi_rel_median']:.3f} ({hi:g})",
            ]
        )
    b.append(
        table(["그룹", "η_v 최저속 (n*)", "곱 @ n* 0.7", "TMHP 곱 @ 0.7", "곱 최저속 (n*)", "곱 최고속 (n*)"], lrows)
    )
    for t in [
        "스크롤·왕복동은 정격 근처에서 최대인 non-monotonic, 스크루는 저속으로 갈수록 단조 감소.",
        "스크롤 n* 0.7의 곱 감소(−4.4~−8.8 %)는 현재 TMHP 기본식(−2.6~−2.9 %, 인버터 항 제외)의 2~3배다. η_v도 BITZER가 더 크게 줄어든다.",
        "정격 위에서도 BITZER 스크롤은 감소한다(n* 1.5에서 곱 −0.6~−5.4 %). TMHP는 거의 그대로다.",
        "같은 GSD…VL 하드웨어에서 R32 0.956, R454B 0.912 — 저속 손실 크기는 냉매에도 걸린다. 다만 그룹 평균 수준 차는 기계 간 차보다 작아 냉매별 계수까지는 필요 없었다.",
        "TMHP의 n*는 설계 정격 회전수 기준이고 여기는 50 Hz 기준이라 1:1 대응은 아니다.",
    ]:
        b.append(bullet(t))
    b.append(
        image(
            FIG / "group_comparison_speed.png",
            "그룹별 η(n*)/η(1) 중앙값. 왼쪽 η_v, 가운데 곱 η_is·η_em, 오른쪽 η_em(±0.0007 안에서 평평)",
        )
    )

    b.append(h(2, "6. TMHP에 대한 권고 (코드 미반영)"))
    for t in [
        "형식별(compressor-type-specific) 상관식. global은 오차가 1.5배, 형식×냉매는 개선 없음.",
        "BITZER로 바꿀 수 있는 것은 η_v와 곱의 압력비·속도 형상이다. η_is/η_em 분리는 지금처럼 토출온도 실측 자료(Cuevas & Lebrun 2009)에 둔다.",
        "반영한다면 수준은 기계별 정격점, 상관식은 정격 대비 비만 맡는 형태가 맞다(ST < LOCO).",
        "기본값 변경 전에 BITZER를 기존 Copeland·Cuevas·Shao 자료와 한 데이터셋으로 묶어 기계 내 식별 규칙(R4)으로 다시 판정한다. "
        "BITZER ORBIT 결과는 전부 「tentative data」 표시가 붙어 있다.",
    ]:
        b.append(bullet(t))
    b.append(divider())
    b.append(
        para(
            "본 단계에서는 compressor efficiency model의 데이터 기반 feasibility만 검증했으며, 냉매별 compressor displacement 자동화, "
            "V_cmp_ref 자동 결정, rps_rated/reference-state initialization 자동화 및 TMHP runtime 코드 수정은 수행하지 않았다."
        )
    )
    return b


def main() -> None:
    if "--replace" in sys.argv:
        for blk in children_all(PAGE):
            call("DELETE", f"/blocks/{blk['id']}")
    blocks = build()
    append(PAGE, blocks)
    print(f"appended {len(blocks)} blocks")


if __name__ == "__main__":
    main()
