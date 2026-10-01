# GSHP Ground-side HX UA 수정 요청 — Capacity-based default

## 목표

- GSHP의 ground-side refrigerant–water HX UA를 `UA_cond = Q/10`, `UA_evap = 0.8 UA_cond` 방식에서 분리.
- **시스템 정격용량에 따라 자동 결정**:
\[
\boxed{UA_{ground,rated}=k_{UA}\,Q_{rated}}
\]
- 사용자가 직접 `UA_ground_rated`를 주면 그 값을 우선 사용.
- 코드 내 근거 문헌을 DOI와 함께 명시.

---

## 1. 문헌 인용

`heat_exchanger.py` 또는 ground-HX UA helper docstring에 아래 reference 추가.

```text
Longo, G.A. (2009).
R410A condensation inside a commercial brazed plate heat exchanger.
Experimental Thermal and Fluid Science, 33(2), 284–291.
DOI: 10.1016/j.expthermflusci.2008.09.004
```

이 논문은 commercial BPHE에서 R410A condensation heat-transfer coefficient가
refrigerant mass flux에 크게 의존하며, forced-convection 영역에서 mass flux 증가에 따라
heat-transfer coefficient가 증가함을 실험적으로 보여준다.

> 주의: 논문이 직접 `UA = kQ` 상관식을 제시하는 것은 아님.  
> `UA ∝ rated capacity`는 **TMHP의 reduced-order sizing assumption**으로 명확히 구분할 것.

---

## 2. TMHP 기본 UA 모델

### 기본식

\[
\boxed{
UA_{ground,rated}
=
k_{UA,ground}\,Q_{rated}
}
\]

권장 default:

\[
\boxed{
k_{UA,ground}=0.18\ \mathrm{K^{-1}}
}
\]

따라서:

```text
4 kW  → 720 W/K
8 kW  → 1440 W/K
10 kW → 1800 W/K
12 kW → 2160 W/K
```

8 kW 시스템에서는:

\[
UA_{ground,rated}=0.18\times8000
=1440\ \mathrm{W/K}
\]

즉 기존 800 W/K보다 큰 **약 1.4–1.5 kW/K** 수준.

---

## 3. 0.18 K⁻¹의 의미

이 값은 Longo 논문에서 직접 회귀한 값이 아니라,
TMHP용 **설계 수준 reduced-order default**.

다음 수준을 대표하도록 설정:

```text
ground-loop rated flow ≈ 3 L/min per kW
ground-side design leaving approach ≈ 4 K
cooling ground-HX duty ≈ 1.2 × rated cooling load
```

Phase-change refrigerant / water HX의 ε-NTU 관계에서:

\[
C_w=\dot m_wc_p
\]

\[
UA
=
C_w
\ln\left(
1+
\frac{Q_{HX,rated}}
{C_w\Delta T_{app,out}}
\right)
\]

이며 위 설계조건을 용량비례로 두면 결과적으로:

\[
UA_{ground,rated}\propto Q_{rated}
\]

가 된다.

따라서 `0.18 K⁻¹`는 **상세 HX geometry가 없을 때 사용하는 scalable default coefficient**로 정의.

---

## 4. 코드 API

### `GroundSourceHeatPump.__init__`

추가:

```python
UA_ground_rated: float | None = None
ground_hx_ua_per_capacity: float = 0.18  # [1/K]
UA_iu_rated: float | None = None
```

처리:

```python
if UA_ground_rated is None:
    UA_ground_rated = ground_hx_ua_per_capacity * hp_capacity
```

사용자 입력이 있으면:

```python
UA_ground_rated = user_value
```

를 그대로 사용.

---

## 5. Physical-location 기준으로 UA 연결

`UA_cond`, `UA_evap`를 GSHP physical HX 기본값으로 사용하지 말 것.

### Cooling

```text
ground-side refrigerant–water HX = UA_ground_rated
indoor air HX                    = UA_iu_rated
```

### Heating

```text
ground-side refrigerant–water HX = UA_ground_rated
indoor air HX                    = UA_iu_rated
```

즉 condenser / evaporator 역할이 바뀌어도 **물리 HX의 rated UA는 유지**.

기존 `UA_cond`, `UA_evap`는 backward compatibility용으로만 유지/deprecate.

---

## 6. Variable UA

운전 중:

\[
UA_{ground}
=
f(\dot m_w,\dot m_r)
\]

기존 resistance scaling 유지:

\[
\frac{1}{UA}
=
R_w+R_r+R_c
\]

단 기준값은 새:

```python
UA_ground_rated
```

사용.

`m_dot_ref_rated`는 새 rated condition에서 자동 산출하거나,
PLR=1 constant-flow baseline에서 가져올 것.

---

## 7. 코드 위치

### `src/tmhp/heat_exchanger.py`
- `calc_ground_hx_UA_from_capacity()` 추가
- Longo (2009) DOI docstring에 명시
- `UA = k_UA × Q_rated` reduced-order assumption임을 주석으로 명확히 표시

예:

```python
def calc_ground_hx_UA_from_capacity(
    rated_capacity: float,
    specific_UA: float = 0.18,
) -> float:
    """
    Return default rated UA [W/K] for the ground-side refrigerant-water HX.

    Reduced-order sizing assumption:
        UA_rated = specific_UA * rated_capacity

    Reference for BPHE refrigerant-side heat-transfer behaviour:
    Longo, G.A. (2009), Experimental Thermal and Fluid Science 33(2), 284-291.
    DOI: 10.1016/j.expthermflusci.2008.09.004

    Note:
    The paper does not propose this capacity-scaling correlation directly.
    The linear UA-capacity relation is a TMHP default sizing assumption.
    """
```

### `src/tmhp/ground_source_heat_pump.py`
- ground/load-side UA를 physical-location 기준으로 분리
- `UA_ground_rated=None`이면 helper로 자동산출

---

## 8. Validation

테스트 추가:

```text
Q_rated = 4 kW  → UA = 720 W/K
Q_rated = 8 kW  → UA = 1440 W/K
Q_rated = 12 kW → UA = 2160 W/K
```

확인:

- capacity 2배 → default rated UA 2배
- explicit `UA_ground_rated` 입력 시 자동값 override
- cooling/heating mode 변경 시 ground physical HX UA가 바뀌지 않음
- variable-UA rated point에서:
\[
UA(\dot m_w=\dot m_{w,rated},
   \dot m_r=\dot m_{r,rated})
=
UA_{ground,rated}
\]

---

## 9. 재시뮬레이션

8 kW 논문 case:

```text
UA_ground_rated = auto
                = 0.18 × 8000
                = 1440 W/K
```

이 값으로:

- Constant-flow PLR 0.3–1.0
- Optimal-flow PLR 0.3–1.0

전부 재실행.

Figure:

```text
(a) Pump power
(b) Indoor-fan power
(c) Compressor power
(d) System COP
```

재생성.

---

## 10. 논문 표기

Simulation condition에는:

```text
Ground-side HX rated UA = 1.44 kW/K
(auto-scaled from the 8 kW rated capacity)
```

정도로 표기.

본문에서 Longo 논문을 직접 UA-capacity 식의 근거라고 과장하지 말 것.

---

## 금지

- `UA_ground = hp_capacity / 10` 유지 금지
- `UA_ground = 0.8 × UA_air` 사용 금지
- Longo (2009)가 `UA = 0.18 Q`를 제안했다고 서술 금지
- water HX와 air HX에 동일한 UA sizing rule 사용 금지
- 새로운 UA로 재실행하지 않고 기존 Figure/결과 재사용 금지
