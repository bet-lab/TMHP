# GSHP 계열 압축기 효율모델 수정 + 재시뮬레이션 계획

## 목표

- GSHP / GSHPB도 ASHP 계열과 동일하게 압축기 3효율을 **압력비와 회전수 함수**로 사용.
- 고정 효율 사용 제거.
- Notion `저회전수 압축기 효율 저하를 통한 저부하 COP 감소 재현 (v26-10-01)`의 **baseline/default 압축기 효율 모델**을 우선 적용.
- 수정 후 GSHP 논문 시뮬레이션 전체 재실행.

---

# 1. GSHP / GSHPB compressor API 수정

대상:

```text
src/tmhp/ground_source_heat_pump.py
src/tmhp/ground_source_heat_pump_boiler.py
```

생성자 인자를 ASHP와 맞춤:

```python
eta_cmp_isen: float | Callable | None = None
eta_cmp_vol: float | Callable | None = None
eta_cmp: float | Callable | None = None   # electro-mechanical efficiency
```

의미:

```text
eta_cmp_isen → η_is(PR, rps)
eta_cmp_vol  → η_v(PR, rps)
eta_cmp      → η_em(PR, rps)
```

- float 입력도 허용
- callable 입력도 허용
- None이면 TMHP 공통 default compressor-efficiency model 사용

---

# 2. ASHP와 동일한 callable 평가 방식 사용

공통 helper 사용:

```python
_eval_eff(model, PR, rps)
```

동작:

```text
float              → 그대로 사용
callable(PR)       → PR 기반 평가
callable(PR, rps)  → PR + 회전수 기반 평가
```

GSHP에서 현재 `calc_ref_state()`에 `eta_cmp_isen` callable을 직접 넘기며 TypeError가 나는 구조 제거.

---

# 3. 회전수 solver 구조 수정

현재 GSHP 냉방은 대략:

```python
m_dot_ref = Q / Δh
rps = m_dot_ref / (V_disp × rho)
```

형태라 η_v가 회전수 계산에 직접 반영되지 않음.

ASHP 방식으로 변경:

\[
\dot m_r
=
V_{cmp,ref}
\rho_{suc}
\eta_v(PR,rps)
\,rps
\]

각 candidate `rps`마다:

```text
1. PR 계산
2. η_v(PR, rps)
3. η_is(PR, rps)
4. η_em(PR, rps)
5. refrigerant mass flow 계산
6. compressor discharge state 계산
7. Q_ref 계산
8. requested load와 residual 계산
```

그 후:

```python
solve_compressor_speed(...)
```

로 `rps` root solve.

---

# 4. Compressor power

최종 전력:

\[
E_{cmp}
=
\frac{
\dot m_r
\left(h_{out,is\ corrected}-h_{in}\right)
}
{\eta_{em}(PR,rps)}
\]

또는 ASHP에서 현재 사용 중인 정확한 정의와 동일하게 구현.

중요:

- η_is는 압축 엔탈피 상승 계산에 반영
- η_v는 냉매 질량유량 계산에 반영
- η_em은 electric compressor power에 반영
- 세 효율을 중복 적용하지 말 것

---

# 5. Default 효율모델

새 계수를 다시 만들지 말 것.

Notion:

```text
저회전수 압축기 효율 저하를 통한 저부하 COP 감소 재현 (v26-10-01)
```

에서 사용한 **baseline / 출하계수 v2026-09-24**를 그대로 사용.

적용 대상:

```text
η_v baseline
η_is baseline
η_em baseline
```

### 주의

이번 GSHP 논문 재시뮬레이션에서는 우선:

```text
baseline/default
```

만 사용.

아래 tuned case는 사용하지 않음:

```text
ETA_EM_N0 = 0.20
eta_is low-speed penalty c = 0.10
eta_is + eta_em tuned
```

즉 저부하 COP를 인위적으로 낮추기 위한 tuned 효율이 아니라,
TMHP 현재 기본 압축기 효율면을 GSHP에도 동일 적용.

---

# 6. 구현 방식

가능하면 compressor efficiency 정의를 한 모듈로 공통화.

예:

```text
compressor_efficiency.py
```

에서:

```python
default_eta_vol(PR, rps)
default_eta_isen(PR, rps)
default_eta_em(PR, rps)
```

제공.

다음 모델 모두 같은 source 사용:

```text
AirSourceHeatPump
AirSourceHeatPumpBoiler
GroundSourceHeatPump
GroundSourceHeatPumpBoiler
WaterSourceHeatPumpBoiler
```

모델별로 계수를 복사하여 중복 정의하지 말 것.

---

# 7. GSHP 논문 simulation condition 유지

```text
Refrigerant = R410A
Cooling capacity = 8 kW
Indoor temperature = 26 °C
Ground temperature = 15 °C

Borefield:
1 × 2
H = 100 m
B = 6 m

Ground flow:
reference = 24 L/min
constant-flow = 24 L/min
optimal-flow min = 9.6 L/min
optimal-flow max = 36 L/min (= 1.5 × ref)

Pump overall efficiency = 0.60
PLR = 0.3–1.0
```

### 중요

기존 임의:

```text
eta_is = 0.70 constant
```

은 제거.

새 default callable efficiency model 사용.

---

# 8. Ground HX / flow 수정사항도 유지

기존 작업 내용 유지:

- rated/reference ≠ max
- 모든 flow scaling denominator = 24 L/min reference
- variable-flow는 1.5 × ref까지 허용
- `E_tot = E_cmp + E_pmp + E_iu_fan` 최소화
- ground HX UA는 capacity-based default 사용
- variable UA = f(water flow, refrigerant flow)
- ε-NTU만 사용
- HX infeasible candidate 제외

---

# 9. 먼저 unit test

## Callable test

GSHP / GSHPB에:

```python
eta_cmp_isen=lambda pr, rps: ...
eta_cmp_vol=lambda pr, rps: ...
eta_cmp=lambda pr, rps: ...
```

전달해 정상 실행되는지 확인.

## Float backward compatibility

```python
eta_cmp_isen=0.70
eta_cmp_vol=1.0
eta_cmp=0.90
```

도 정상 실행.

## ASHP parity

동일:

```text
PR
rps
```

입력에서 GSHP와 ASHP가 같은 default efficiency function 값을 반환하는지 확인.

---

# 10. 재시뮬레이션

수정 후 논문 study 전체 재실행.

각 PLR / flow strategy별 저장:

```text
PLR
ground flow [L/min]
ground flow ratio-to-ref

PR
compressor rps
normalized speed

eta_v
eta_is
eta_em

m_dot_ref
E_cmp
E_pmp
E_iu_fan
E_tot

T_evap_sat
T_cond_sat

UA_ground
Rb*

COP_comp
COP_sys
```

---

# 11. Figure 재생성

기존 논문 1×4 Figure 유지:

```text
(a) Pump power
(b) Indoor-fan power
(c) Compressor power
(d) System COP
```

Constant-flow vs Optimal-flow.

---

# 12. 추가 검증 Figure — 논문에는 넣지 않아도 됨

개발 확인용:

### A
```text
PLR vs compressor rps
```

### B
```text
PLR vs η_v / η_is / η_em
```

### C
```text
PLR vs pressure ratio
```

목적:
- callable efficiency가 실제 simulation에 반영됐는지 확인
- 저부하 COP가 높은 이유를 추적

---

# 13. 결과 검증

특히 PLR 0.3–0.5에서 확인:

```text
rps가 실제 감소하는가?
η_v가 rps 변화에 따라 변하는가?
η_is가 PR/rps 변화에 따라 변하는가?
η_em이 PR/rps 변화에 따라 변하는가?
E_cmp가 기존 constant-η_is 모델과 달라지는가?
COP_sys가 과도하게 높게 유지되는가?
```

PR floor가 걸린 점은 별도 표시.

---

# 14. 결과 비교

다음 두 case를 내부 비교:

```text
A. 이전 GSHP
eta_is = 0.70 constant
η_v 미적용
η_em 미적용

B. 수정 GSHP
η_v(PR,rps)
η_is(PR,rps)
η_em(PR,rps)
```

PLR별:

```text
ΔE_cmp
ΔCOP_comp
ΔCOP_sys
```

산출.

---

# 15. 산출물

- TMHP 수정 코드
- unit/regression tests
- 새 simulation CSV
- 새 논문 Figure PNG
- `compressor_efficiency_validation.md`
  - 적용 default source
  - GSHP/ASHP interface parity
  - PLR별 PR/rps/η_v/η_is/η_em
  - 기존 vs 신규 COP 비교

---

# 금지

- GSHP만 별도 효율계수 복사 정의 금지
- η_is만 callable로 만들고 η_v / η_em은 고정하는 반쪽 수정 금지
- tuned low-speed penalty를 baseline이라고 사용 금지
- JSON에 Python callable 저장하려 하지 말 것
- `run_study.py`에서만 monkey-patch하고 TMHP 본체를 그대로 두는 방식 금지
- 새 compressor model 적용 없이 기존 Figure 재사용 금지
