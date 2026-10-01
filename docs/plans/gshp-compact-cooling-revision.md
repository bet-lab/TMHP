# GSHP 1-page 논문 최종 수정 요청

## 1. 코드 수정

- `GroundSourceHeatPump` 냉방 조건
  - R410A
  - Rated cooling capacity = 8 kW
  - Indoor temperature = **26 °C**
  - \(T_s=15\ ^\circ\mathrm{C}\)
  - \(\eta_{is}=0.70\)
  - PLR = 0.3–1.0
- 코드 내 cooling/heating 실내온도 hard-coding 제거
  - 사용자가 입력한 `T_a_room`이 cycle / output에 동일하게 적용되는지 확인

### Borefield / flow
- `N_1 × N_2 = 1 × 2`
- `H_b = 100 m`
- `B = 6 m`
- total active borehole length = 200 m
- Rated ground-loop flow = **24 L/min**
- Optimal-flow range = **0.4–1.0 × rated**
- Overall pump efficiency = **0.60**
- Header/manifold loss = 0 유지

### Ground-load normalization
- 반드시:
\[
q'=\frac{Q_{bhe}}{N_bH_b}
\]
- GSHP / GSHPB 모두 동일 적용
- field-total heat rate 복원 시:
\[
Q_{bhe}=q'N_bH_b
\]

---

## 2. Variable-flow 최적화 수정

### Constant flow
\[
\dot V=\dot V_{rated}
\]

### Optimal flow
기존 `E_cmp + E_pmp` 기준 사용 금지.

\[
\boxed{
\dot m_w^*
=
\arg\min E_{tot}
}
\]

\[
\boxed{
E_{tot}=E_{cmp}+E_{pmp}+E_{iu,fan}
}
\]

- `ground_flow_control.select_ground_flow()` objective:
  - 기존 `E_cmp_plus_pmp [W]`
  - → **`E_tot [W]`로 변경**
- final candidate 선택도 `E_tot [W]` 기준
- `E_cmp_plus_pmp [W]`는 diagnostic으로만 유지

### 반드시 테스트
- fan power 때문에 최적 candidate가 달라지는 synthetic unit test 추가
- GSHPB는 `E_tot = E_cmp + E_pmp`이므로 regression 유지
- constant-flow 결과 regression 유지
- 각 selected point에서:
\[
COP_{sys}=\frac{Q_{load}}{E_{tot}}
\]
검증

---

## 3. Ground HX

- LMTD 사용하지 않음
- \(\varepsilon\)-NTU만 사용

\[
NTU=\frac{UA}{\dot m_wc_p}
\]

\[
\varepsilon=1-e^{-NTU}
\]

\[
Q_{HX}
=
\varepsilon\dot m_wc_p
|T_{w,in}-T_{ref,sat}|
\]

- 정상 운전점:
\[
Q_{HX}=Q_{ref}
\]
- HX capacity 부족 candidate는 optimizer에서 제외
- `ground_hx_capacity_insufficient` diagnostic 유지

### Variable UA
\[
UA=f(\dot m_w,\dot m_r)
\]

- 상세 HX geometry 필수입력으로 만들지 말 것
- rated-UA resistance model 유지
- `m_dot_ref_rated`는 새 조건의 **constant-flow, PLR=1 baseline**에서 산출
- 기존 임의 `0.04 kg/s` 재사용 금지
- 기존 임의 `UA = 2 kW/K` 재사용 금지
- 실제 TMHP default/config 적용값 확인 후 논문에 작성

---

## 4. Pump / borehole

- `pump.py` 사용
- 동일 pump physics + 다른 flow control policy 구조 유지
- \(R_b^*=f(\dot m_b)\) 적용
- branch flow:
\[
\dot m_b=\frac{\dot m_w}{N_b}
\]
- pressure drop:
\[
\Delta p=f\frac{2H_b}{D}\frac{\rho v^2}{2}
\]
- pump power:
\[
E_{pmp}=\frac{\Delta p\dot V}{\eta_{pmp}}
\]

---

## 5. Simulation 재실행

### Baseline
- constant flow
- PLR = 1.0
- `m_dot_ref_rated` 산출

### Full sweep
- Constant-flow
- Optimal-flow
- PLR 0.3–1.0

### 저장
- PLR
- ground flow ratio / L/min
- \(E_{pmp}\)
- \(E_{iu,fan}\)
- \(E_{cmp}\)
- \(E_{tot}\)
- \(UA_{ground}\)
- \(R_b^*\)
- \(T_{evap,sat}\)
- \(T_{cond,sat}\)
- pressure ratio
- \(COP_{comp}\)
- \(COP_{sys}\)
- HX feasibility flags

---

# 6. Figure — 기존 Fig. 1, Fig. 2 삭제

논문에는 **Figure 하나만 사용**.

## Fig. 1 — 1 × 4 layout

모든 panel의 x-axis:

\[
PLR
\]

각 panel에서:

- Constant-flow
- Optimal-flow

두 case 비교.

### (a) Pump power
- y: \(E_{pmp}\) [W 또는 kW]
- Constant vs Optimal

### (b) Indoor fan power
- y: \(E_{iu,fan}\) [W 또는 kW]
- Constant vs Optimal

### (c) Compressor power
- y: \(E_{cmp}\) [kW]
- Constant vs Optimal

### (d) System COP
- y:
\[
COP_{sys}
=
\frac{Q_{cooling}}
{E_{cmp}+E_{pmp}+E_{iu,fan}}
\]
- Constant vs Optimal

### Figure style
- **총 4열, 1행**
- `(a) (b) (c) (d)` panel label
- x-axis range / ticks 동일
- Constant / Optimal의 marker와 line style 전 panel 동일
- legend는 가능하면 figure 상단 공통 legend 1개
- 과도한 annotation 금지
- 1-page HWPX에 들어갔을 때 축/범례가 읽히는 크기로 작성

### Caption 후보

> **Fig. 1. Part-load performance of the GSHP under constant- and optimal-flow control: (a) ground-loop pump power; (b) indoor-fan power; (c) compressor power; and (d) system COP.**

---

## 7. Figure에서 반드시 확인할 물리적 의미

- Pump:
  - optimal-flow에서 감소하는가?
- Fan:
  - flow strategy 변경에 따라 실제 차이가 생기는가?
  - 차이가 거의 없으면 그대로 보여줄 것
  - 억지로 차이를 강조하지 말 것
- Compressor:
  - ground flow 감소로 HX/lift가 불리해져 증가하는 구간이 존재하는가?
- System COP:
  - 위 세 전력의 trade-off 결과가 반영되는가?

> 특히 **fan power를 Figure에 포함한 이유는 최적화 objective에 fan power가 실제 포함되었음을 시각적으로 확인하기 위함**.

---

# 8. HWPX 수정

## 제목 권장
**물리 기반 지열히트펌프 모델 개발 및 정유량·최적 변유량 운전 비교**

**Physics-Based Modeling of a Ground Source Heat Pump with Constant- and Optimal-Flow Control**

## Extended Abstract
아래만 짧게 포함.

- regression model 한계
- detailed physical model의 과도한 입력 문제
- TMHP = catalogue-level input + reduced-order physics
- 변유량 모델:
  - \(E_{pmp}(\dot m_w)\)
  - \(R_b^*(\dot m_w)\)
  - \(UA(\dot m_w,\dot m_r)\)
- 최적화:
\[
E_{tot}=E_{cmp}+E_{pmp}+E_{iu,fan}
\]
최소화

## 운전조건 반드시 표기
- R410A
- 8 kW cooling
- indoor 26 °C
- ground 15 °C
- \(\eta_{is}=0.70\)
- 1×2 boreholes
- H = 100 m
- B = 6 m
- rated flow = 24 L/min
- flow range = 0.4–1.0
- pump overall efficiency = 0.60
- PLR = 0.3–1.0
- 실제 적용한 ground/load-side UA

## Results and Discussions
- 새 simulation 결과로 **2개 bullet만**
- 기존 수치 재사용 금지
- Pump / fan / compressor power 변화와 최종 system COP 변화 중심
- optimizer가 유량 bound에 붙으면 “internal optimum”이라고 표현하지 말 것

## Acknowledgement
2025년 과제만 유지:

```text
이 성과는 정부(과학기술정보통신부)의 재원으로 한국연구재단의 지원을 받아 수행된 연구임(No. RS-2025-00512551).
```

---

# 9. 최종 산출물

- 수정된 `.hwpx`
- 최종 `.pdf`
- 새 **1×4 Fig. 1 PNG**
- simulation result CSV
- 짧은 `validation_notes.md`
  - 적용조건
  - objective 변경 확인
  - COP 정의
  - HX feasibility
  - optimizer bound 여부
- **1 page 유지 확인**

---

# 금지

- 기존 Fig. 1 / Fig. 2 재사용 금지
- 기존 80 L/min / 2×2×100 m 결과 재사용 금지
- fan power를 optimal-flow objective에서 제외 금지
- LMTD 추가 금지
- 임의의 UA / refrigerant reference flow 사용 금지
- 결과에 없는 수치 작성 금지
