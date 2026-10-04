# BITZER 기반 압축기 3효율 pilot 검증

2026-10-04 · 이슈 bet-lab/TMHP#81 · 브랜치 `feat/bitzer-efficiency-pilot`

BITZER Software 계산 결과로 TMHP 압축기 세 효율(η_v, η_is, η_em)을 압력비 r_p와 속도의 함수로 회귀할 수 있는지 확인했다. **TMHP 코드와 기본 계수는 바꾸지 않았다.** BITZER 값은 제조사 소프트웨어가 계산한 값이고(모든 ORBIT 결과에 "Tentative data" 표시), 독립 실험점이 아니다.

## 결론 요약

| 질문 | 답 |
| --- | --- |
| RQ1 속도 스윕이 되는가 | **된다.** 19대에서 4,275회 요청, 운전범위 안 3,604점. 스크롤 35–75 Hz(n* 0.7–1.5), 왕복동 25–87 Hz(n* 0.5–1.74), 스크루 25–75 Hz(n* 0.5–1.5) |
| RQ2 세 효율을 따로 계산할 수 있는가 | **η_v와 곱 η_is·η_em만 식별된다.** BITZER 토출온도는 단열 에너지 수지와 맞춰 낸 값이라(응축기 용량 = 냉동능력 + 소비전력, 오차 0.1 % 이내), ṁ(h₂−h₁)/P = η_em이 형식별 상수(스크롤 0.99~1.00, 반밀폐 0.97)로 나온다. η_is는 곱을 이 상수로 나눈 값일 뿐이다 |
| RQ3 형식·냉매별로 다른가 | 형식별로는 **분명히 다르다**(곱의 수준 스크롤 0.70, 왕복동·스크루 0.58; 속도 형상도 다름). 같은 스크롤 안에서 냉매로 더 나눠도 교차검증 오차가 줄지 않는다 |
| RQ4 η = f(r_p, n*)로 설명되는가 | 한 기계 안에서는 **잘 된다**(2차식 RMSE η_v 0.003~0.019, 곱 0.009~0.021). 기계 사이 수준 차가 남아서 압축기 단위 LOCO 오차는 η_v 2~5 %, 곱 3~7 % |
| RQ5 저속 손실이 보이는가 | **보인다.** 스크롤 n* 0.7에서 곱 −4.4 %(R32)~−8.8 %(R454B), η_v −1.2~−3.1 %. 현재 TMHP 기본식(−2.6~−2.9 %, −1.0 %)보다 2~3배 크다 |
| 권고 grouping | **compressor-type-specific** (global보다 확실히 낫고, type×refrigerant는 개선이 없다) |

## 1. 데이터 출처

- BITZER Software 웹판 **v7.1.11.4**, 2026-10-04 추출. Chrome에서 웹 UI의 통신을 기록해 계산 API(`/api/v1/calc`, JSON Patch → `/calc/results`)를 찾고, 같은 백엔드를 익명 세션으로 호출했다. 화면값과 1점 대조로 일치를 확인했다(GSD80235VA R410A 0/50 °C 50 Hz: 43.8 kW / 17.55 kW / 1077 kg/h / 95.8 °C). 상세: [`metadata/extraction_conditions.md`](metadata/extraction_conditions.md), [`metadata/source_versions.md`](metadata/source_versions.md)
- 인벤토리: [`metadata/compressor_inventory.csv`](metadata/compressor_inventory.csv) 102대. 속도 제어가 되는 것은 ORBIT(35–75 Hz)·ORBIT+(35–75 Hz)·ORBIT FIT(35–60 Hz) 스크롤, VARISPEED 왕복동(내장 인버터 25–87 Hz), HS 스크루(외부 인버터 20–75 Hz). **ORBIT Boreal 10대는 50/60 Hz 고정속이라 속도 회귀에서 제외**했다(메타데이터에만 기록).
- 격자: SST −15/−10/−5/0/5 °C × SDT 35/40/45/50/55 °C × 주파수 9점, SH 10 K, SC 0 K(EN 12900 조건과 같음).

| 그룹 | 압축기 | 점 | r_p | f [Hz] |
| --- | --- | --- | --- | --- |
| scroll / R410A | GSD60120VA, GSD60182VA, GSD80235VA, GSD80385VA, GSD80485VA | 1,040 | 2.29–7.15 | 35–75 |
| scroll / R32 | GSD60120VL, GSD60182VL, GSD80295VL, GSD80485VL | 626 | 2.30–5.08 | 35–75 |
| scroll / R454B | GSD60120VL, GSD60182VL, GSD80295VL, GSD80485VL (R32와 같은 하드웨어) | 801 | 2.30–6.45 | 35–75 |
| reciprocating / R134a | 2DES-3.F1, 4FE-5.F1, 4CE-6.F1 (VARISPEED) | 674 | 2.54–9.10 | 25–87 |
| screw / R134a | HSK5343-30, HSK6451-40, HSK7451-50 | 463 | 2.54–9.10 | 25–75 |

범위 밖으로 거절된 671점은 raw CSV에 메시지와 함께 남겼다(최대 응축온도 한계, 고주파 전류 한계, 스크루 최소 주파수). R32는 저증발·고응축 쪽 envelope가 좁아 225점 중 149~163점만 남는다.

## 2. 효율 정의 (TMHP 코드와 같은 정의)

CoolProp HEOS, TMHP와 같은 `PropsSI` 호출. R454B는 `HEOS::R32[0.829248]&R1234yf[0.170752]`.

- 1 흡입: P₁ = P_sat,dew(SST), T₁ = SST + 10 K → h₁, s₁, ρ₁
- 2s: P₂ = P_sat,dew(SDT), h₂s = h(P₂, s₁)
- 2: h₂ = h(P₂, T_dis), T_dis = BITZER "Discharge gas temp. w/o cooling"
- r_p = P₂/P₁ (절대압)
- **η_v = ṁ / (ρ₁ V_d N)**, V_d = BITZER "Displacement (2900 rpm 50 Hz)"(왕복동은 1450 rpm), N = f × rpm₅₀/50 (가정)
- **η_is = (h₂s − h₁)/(h₂ − h₁)**
- **η_em = ṁ(h₂ − h₁)/P_el** — `air_source_heat_pump.py`의 `E_cmp = ṁ(h₂−h₁)/η_em`과 같다
- 곱 **η_is·η_em = ṁ(h₂s − h₁)/P_el** — 토출온도 없이 식별된다
- 속도: 원자료 f 보존, **n* = f/50 Hz**(제조사 공칭 주파수 기준, basis `frequency_ratio`). 이 표본은 모든 기계가 f_ref = 50 Hz라 f와 n*는 아핀 변환 관계이고, 다항 회귀는 같은 모델이 된다(CV 차이 2×10⁻¹⁴). Model family A(실제 속도)와 B(정규화 속도)는 여기서 구분되지 않는다.
- 스크루 모듈은 질량유량을 주지 않아 ṁ = Q₀/(h₁ − h₃)로 역산했다(`mass_flow_basis`).

## 3. 데이터 품질

| 항목 | 내용 | 처리 |
| --- | --- | --- |
| η_v > 1 | 174점, 최대 1.04, 모두 저압력비 | N을 50 Hz 슬립 비로 환산한 가정의 편향으로 본다. 저부하에서 슬립이 줄면 실제 회전수가 동기속도 60 rpm/Hz 쪽으로 붙는다(최대 +3.4 %). EST-420의 2000–4400 rpm @35–75 Hz(57.1–58.7 rpm/Hz)와도 맞다. 삭제하지 않고 `eta_v_gt_1` flag |
| η_em > 1 | R32·R454B 300점, 최대 1.004 | 표시 자릿수(전력 3자리, 토출온도 0.1 K) 반올림 범위. flag만 |
| 스크루 추가 냉각 | 212점 "Additional cooling/limitations" | 이 점에서는 응축기 수지가 깨진다(최소 0.65). η_is·η_em 회귀·그림에서만 제외, η_v·곱에는 포함. `additional_cooling_required` flag |
| 전력 경계 | 외부 인버터 손실 미포함(압축기 단자). VARISPEED는 내장 인버터라 포함 여부 미확인 | 아래 §6 |
| 토출온도 정의 | 소프트웨어·문서에 정의 없음 | 실측 수지로 판정(§6) |

## 4. 회귀

후보: C1 a₀+a₁r_p+a₂n*, C2 C1+a₃r_p n*, C3 2차 반응면(+r_p², n*², r_p n*). 교차검증은 **압축기 단위 Leave-One-Compressor-Out**(같은 하드웨어의 R32·R454B 계산은 함께 뺀다). 추가 지표 **ST**: LOCO로 맞춘 식의 수준을 빼낸 기계의 n* = 1 점으로 맞춘 뒤 다른 속도를 예측한 오차. TMHP가 정격점으로 기계 수준을 정하고 상관식이 속도 형상을 맡는 구조와 같다.

### 그룹 수준 비교 (C3, LOCO MAPE %, 같은 3,604점)

| 효율 | A global | **B type** | C type×refrigerant |
| --- | --- | --- | --- |
| η_v | 4.59 (ST 3.29) | **3.06 (ST 1.94)** | 3.10 (ST 1.96) |
| η_is·η_em | 6.92 (ST 5.14) | **4.30 (ST 3.35)** | 4.61 (ST 3.59) |

C3가 C1·C2보다 ST에서 0.3~1.1 %p 낫다(η_v ST 2.27→1.94, 곱 4.49→3.35). 계수 해석: n*² 계수가 음수인 오목 형상, 즉 정격 근처에 효율 최대가 있다.

### 선택 모델: type-specific C3 (x = n*)

η = a₀ + a₁r_p + a₂n* + a₃r_p² + a₄n*² + a₅r_p n*

| 효율 | type | a₀ | a₁ | a₂ | a₃ | a₄ | a₅ | 기계 | 점 | LOCO MAPE | ST MAPE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| η_v | scroll | 0.91280 | −0.05274 | 0.34355 | 0.00228 | −0.17015 | 0.01145 | 9 | 2,467 | 2.46 | 2.25 |
| η_v | reciprocating | 0.89238 | −0.05184 | 0.23200 | 0.00141 | −0.09789 | 0.00000 | 3 | 674 | 3.68 | 1.09 |
| η_v | screw | 0.85974 | −0.03220 | 0.30126 | 0.00042 | −0.13548 | 0.00582 | 3 | 463 | 5.38 | 1.50 |
| η_is·η_em | scroll | 0.48996 | −0.01447 | 0.57908 | −0.00686 | −0.31021 | 0.03022 | 9 | 2,467 | 4.18 | 3.59 |
| η_is·η_em | reciprocating | 0.52209 | 0.01165 | 0.08288 | −0.00209 | −0.06835 | 0.01092 | 3 | 674 | 2.74 | 1.82 |
| η_is·η_em | screw | 0.51734 | −0.04092 | 0.43162 | −0.00102 | −0.19754 | 0.01332 | 3 | 463 | 7.20 | 4.38 |

전체 계수·지표(그룹 3수준 × 속도변수 2종 × 후보 3종 × 효율 4종): [`regression/regression_coefficients.csv`](regression/regression_coefficients.csv), [`regression/regression_metrics.csv`](regression/regression_metrics.csv), [`regression/grouping_comparison.csv`](regression/grouping_comparison.csv). 기계 단독 적합: [`regression/per_machine_fit.csv`](regression/per_machine_fit.csv). **적합 범위 밖(스크롤 n* < 0.7, r_p > 7)으로 외삽하지 않는다.**

## 5. 주요 결과

**기계 사이 수준 차가 오차를 지배한다.** 한 기계 안에서 (r_p, n*) 2차식은 η_v RMSE 0.003~0.019(R² 0.80~1.00), 곱 0.009~0.021(R² 0.54~0.98; 왕복동은 곱 자체의 변동폭이 작아 R²가 낮다)을 준다. 풀링하면 곱 RMSE가 0.031~0.035로 커지고, 잔차가 기계별로 위아래로 갈린다(예: GSD60182VA +0.05, GSD80235VA −0.05). 그래서 ST(수준만 맞춘 형상 전이)가 LOCO보다 낮다. 이전 TMHP 적합(v2026-09-24)에서 fixed-effects로 속도 항을 식별해야 했던 이유와 같은 구조다.

**저속 거동** (같은 SST/SDT에서 η(n*)/η(1)의 중앙값, [`regression/lowspeed_ratios.csv`](regression/lowspeed_ratios.csv), `figures/group_comparison_speed.png`)

| 그룹 | η_v @ 최저속 | 곱 @ n* 0.7 | 곱 @ 최저속 | 곱 @ 최고속 | 형상 |
| --- | --- | --- | --- | --- | --- |
| scroll / R410A | 0.969 (0.7) | 0.919 | 0.919 | 0.961 (1.5) | 정격에서 최대, 양쪽으로 감소 |
| scroll / R32 | 0.988 (0.7) | 0.956 | 0.956 | 0.994 (1.5) | 최대 n* 1.2 |
| scroll / R454B | 0.976 (0.7) | 0.912 | 0.912 | 0.946 (1.5) | 최대 n* 1.0–1.2 |
| reciprocating / R134a | 0.945 (0.5) | 0.991 | 0.975 (0.5) | 0.937 (1.74) | 저속은 완만, 고속에서 감소 |
| screw / R134a | 0.937 (0.5) | 0.928 | 0.861 (0.5) | 1.025 (1.5) | 단조 증가 |

- 판정: 스크롤·왕복동은 **non-monotonic**(정격 근처 최대), 스크루는 **monotonic decrease toward low speed**. 크기는 **compressor-dependent**(스크롤 n* 0.7 곱 비가 기계·운전점에 따라 0.77~1.07)이고 **refrigerant-dependent**이기도 하다 — 같은 GSD…VL 하드웨어에서 R32 0.956, R454B 0.912.
- 현재 TMHP 기본식과 비교(같은 r_p 중앙값, [`regression/tmhp_default_speed_ratios.csv`](regression/tmhp_default_speed_ratios.csv)): 스크롤 n* 0.7 곱 비는 TMHP 0.971~0.974(인버터 항 제외) / 0.961~0.963(포함), BITZER 0.912~0.956. η_v는 TMHP 0.990, BITZER 0.969~0.988. **BITZER 저속 손실이 TMHP보다 크다.** 정격 위에서도 BITZER 스크롤은 감소하는데(n* 1.5 곱 0.946~0.994), TMHP는 거의 그대로다(유동손실 항이 한쪽만 작동, 1.000). 단 TMHP의 n*는 설계 정격 회전수 기준이고 여기의 n*는 50 Hz 기준이라 1:1 대응은 아니다.
- 압력비 의존: 곱은 r_p에 대해 아래로 볼록한 감소(스크롤 r_p 2.3에서 0.75 → 7에서 0.5), 상호작용 r_p·n* 계수는 양수(고압력비일수록 저속 손실이 커진다; 기존 TMHP 누설 항 c(PR−1)u와 같은 방향).

## 6. BITZER 유래 η_is, η_em의 신뢰도

| 질문 | 판단 |
| --- | --- |
| A. 토출온도가 실제 토출 상태를 대표하는가 | **근거 부족.** 정의가 공개돼 있지 않고, 모든 유효점에서 Q_cond = Q₀ + P_el(0.997–1.004)이며 ṁ(h₂−h₁)/P_el이 형식별 상수다. 측정된 토출 상태라기보다 에너지 수지에 고정 열손실(스크롤 0~1 %, 반밀폐 약 3 %)을 둔 계산값으로 보인다 |
| B. 소비전력 경계 | 압축기 단자 입력. 외부 인버터 손실은 EST-420이 별도 항목으로 다룬다(포함 안 됨). VARISPEED 내장 인버터 포함 여부는 미확인 |
| C. 밀폐형 모터 손실이 냉매 엔탈피에 얼마나 들어가는가 | 흡입가스 냉각 밀폐·반밀폐는 모터 손실 대부분이 냉매로 간다. BITZER 수지도 그렇게 계산돼 있어, ṁ(h₂−h₁)/P는 모터 효율이 아니라 "외피 열손실을 뺀 비율"이 된다 |
| D. ṁ(h₂−h₁)/P를 TMHP η_em으로 써도 되는가 | **아니다.** TMHP η_em은 구동·모터·베어링 손실을 뜻하지만, 이 값은 그 손실이 냉매로 돌아온 뒤의 열손실 비율이다 |

| 효율 | 분류 |
| --- | --- |
| η_v | **B. usable with stated assumptions** (N ∝ f, 50 Hz 슬립 비; 저압력비에서 최대 +3 % 편향) |
| η_is·η_em (곱) | **B. usable with stated assumptions** (외부 인버터 손실 제외, 단자 전력 기준) |
| η_is | **C. not identifiable reliably from BITZER alone** (곱을 상수 η_em으로 나눈 값) |
| η_em | **C. not identifiable reliably from BITZER alone** (계획서 중단 조건 C에 해당. 임의 보정은 하지 않았다) |

계획서 성공 기준 대비: 형식 3개, 냉매 그룹 5개, 그룹마다 3대 이상, 효율 2개(η_v, 곱) 물리적으로 일관 → feasibility 충족. 세 효율 모두는 충족 못 함.

## 7. TMHP에 대한 권고 (코드 미반영)

1. **compressor-type-specific correlation**을 권고한다. global은 LOCO 오차가 1.5배이고, type×refrigerant는 개선이 없다(스크롤 안 냉매 차이는 기계 간 수준 차보다 작다).
2. BITZER로 바꿀 수 있는 것은 **η_v와 곱 η_is·η_em의 속도·압력비 형상**이다. η_is/η_em 분리는 지금처럼 토출온도 실측 자료(Cuevas & Lebrun 2009 등)에 둬야 한다.
3. 반영한다면 수준은 기계별 정격점이 정하고 상관식은 **정격 대비 비**만 맡는 형태가 맞다(ST가 LOCO보다 낮은 이유).
4. 후속 검토: 스크롤 저속 손실이 현재 TMHP보다 2~3배 크고, 정격 위 감소도 BITZER에는 있다. 다만 BITZER의 ORBIT 결과는 전부 "tentative data"이고 기준속도가 50 Hz라, 기존 Copeland·Cuevas 자료와 한 데이터셋에 넣어 R4(기계 내 식별) 규칙으로 다시 판정한 뒤에 기본값 변경 여부를 정해야 한다.

본 단계에서는 compressor efficiency model의 데이터 기반 feasibility만 검증했으며, 냉매별 compressor displacement 자동화, `V_cmp_ref` 자동 결정, `rps_rated`/reference-state initialization 자동화 및 TMHP runtime 코드 수정은 수행하지 않았다.

## 재현

```bash
P=validation/bitzer_efficiency_pilot/scripts
uv run python3 $P/inventory.py                     # (cwd = scripts) -> compressor_inventory.csv
uv run python3 $P/batch.py ../raw/per_compressor MODULE:SERIES:REF:MODEL:TYPE[:f1,f2,...] ...
uv run python3 $P/combine.py validation/bitzer_efficiency_pilot
uv run python3 $P/derive.py  validation/bitzer_efficiency_pilot/raw/bitzer_raw_points.csv validation/bitzer_efficiency_pilot/processed/efficiency_points.csv
uv run python3 $P/fit.py     validation/bitzer_efficiency_pilot/processed/efficiency_points.csv validation/bitzer_efficiency_pilot/regression
uv run python3 $P/figures.py validation/bitzer_efficiency_pilot/processed/efficiency_points.csv validation/bitzer_efficiency_pilot/regression validation/bitzer_efficiency_pilot/figures C3
uv run python3 $P/lowspeed.py validation/bitzer_efficiency_pilot/processed/efficiency_points.csv validation/bitzer_efficiency_pilot/regression validation/bitzer_efficiency_pilot/figures
uv run python3 $P/compare_tmhp.py validation/bitzer_efficiency_pilot/processed/efficiency_points.csv validation/bitzer_efficiency_pilot/regression/tmhp_default_speed_ratios.csv
```

그림 파일: `eta_{v,is,em,oi}_scatter_<group>`, `eta_*_heatmap_<group>`(데이터 convex hull 밖 mask, 같은 색 범위), `parity_*`, `residual_rp_*`, `residual_speed_*`, `diag_*`(세 진단 한 장), `lowspeed_<group>`, `group_comparison_speed` — 각 PNG + SVG. `eta_oi`는 곱 η_is·η_em.
