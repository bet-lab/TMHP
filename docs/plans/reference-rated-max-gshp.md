# TMHP Reference / Rated / Max 분리 리팩터링 계획

## 목표

- **rated/reference 값을 maximum으로 해석하지 않도록 TMHP 전체 모델 수정**
- 동일 장비 비교에서 normalization 기준은 고정하고, control/hardware 범위만 별도로 변경
- 특히 GSHP variable-flow가 rated flow보다 높은 유량도 탐색할 수 있도록 수정

---

# 1. 공통 용어 규칙

앞으로 변수 의미를 명확히 분리.

```text
*_ref      = 수식 normalization용 기준점
*_rated    = 제조사/설계 정격 운전점
*_min      = 물리/제어 최소값
*_max      = 물리/제어 최대값
*_setpoint = 정유량/정속 운전 시 실제 고정값
```

### 핵심 원칙

\[
\boxed{\text{rated} \neq \text{max}}
\]

\[
\boxed{\text{reference} \neq \text{control limit}}
\]

Scaling 식의 denominator는 항상 **공통 reference/rated 값**을 사용.

예:

\[
r_{\dot V}=\frac{\dot V}{\dot V_{ref}}
\]

Variable-flow 최대유량이 커져도 denominator를 바꾸지 않는다.

논문 GSHP case에서는:

\[
oxed{
\dot V_{var,max}=1.5\,\dot V_{ref}
}
\]

로 두고, reference는 계속 24 L/min으로 유지한다.

---

# 2. GSHP Ground-flow 수정

## 현재 문제

현재 구조:

```python
volume_flow_rated
ground_flow_min_ratio
ground_flow_max_ratio
```

에서 `rated`가 normalization 기준이면서 flow range의 기준이 되어 의미가 섞임.

## 수정

권장 API:

```python
ground_flow_ref_lpm
ground_flow_constant_lpm
ground_flow_min_lpm
ground_flow_max_lpm
```

논문 case 예:

```text
ground_flow_ref_lpm      = 24
ground_flow_constant_lpm = 24
ground_flow_min_lpm      = 9.6
ground_flow_max_lpm      = 30.0
```

즉:

```text
Constant-flow = 항상 24 L/min
Optimal-flow  = 9.6–30 L/min
```

하지만 모든 scaling은 동일하게:

\[
r_w=\frac{\dot V}{24\ {\rm L/min}}
\]

사용.

---

## Ground UA

\[
UA_{ground}
=
f\left(
\frac{\dot m_w}{\dot m_{w,ref}},
\frac{\dot m_r}{\dot m_{r,ref}}
\right)
\]

- variable-flow max가 36 L/min이어도 water reference는 24 L/min 유지
- 36 L/min이면 ratio = 1.50
- UA가 rated/reference 값보다 커지는 것을 허용
- `ratio <= 1` clamp 금지

---

## Borehole \(R_b^*\)

가능하면 ratio가 아니라 **실제 borehole mass flow [kg/s]**를 그대로 입력.

\[
\dot m_b=\frac{\dot m_{total}}{N_b}
\]

- interpolation grid만 `min_flow`~`max_flow`까지 생성
- reference flow는 interpolation normalization 용도로 사용하지 않음
- max flow 변경 시 \(R_b^*\) reference 자체가 바뀌면 안 됨

---

## Pump

Pump physics는 actual flow 기반 유지.

\[
E_{pmp}
=
\frac{\Delta p(\dot V)\dot V}{\eta}
\]

- rated flow를 maximum으로 사용하지 않음
- hydraulic curve가 24 L/min을 넘어도 36 L/min까지 계산 가능해야 함
- pump/hydraulic hardware max는 `ground_flow_max_lpm=36 L/min`으로 별도 제한

---

# 3. `ground_loop.py` / `ground_flow_control.py`

### `configure_ground_flow()`

기존:

```python
volume_flow_rated
min_ratio
max_ratio
```

신규 내부 표현:

```python
volume_flow_ref
volume_flow_constant
volume_flow_min
volume_flow_max
```

### `select_ground_flow()`

- constant:
```python
flow = volume_flow_constant
```

- optimal:
```python
volume_flow_min <= flow <= volume_flow_max
```

- optimizer 변수는 가능하면 **actual flow [m3/s]**
- 필요하면 diagnostic으로만:
```python
ground_flow_ref_ratio = flow / volume_flow_ref
```

### 결과 변수

```text
ground_flow [m3/s]
ground_flow_ref_ratio
ground_flow_at_min
ground_flow_at_max
```

`ground_flow_ratio`는 deprecated 또는 의미를 `ratio_to_ref`로 명확히 변경.

---

# 4. Fan 모델 전체 수정

## 현재 문제

공통 HX fan solver에서:

```python
dV_min = dV_fan_rated * 0.05
dV_max = dV_fan_rated
```

로 되어 있어 **rated airflow가 사실상 max airflow로 사용됨**.

ASHP / ASHPB / GSHP indoor fan 모두 영향 가능.

## 수정

API:

```python
dV_fan_a_ref
dV_fan_a_min
dV_fan_a_max
```

또는 backward-compatible하게:

```python
dV_fan_a_rated
dV_fan_a_min = None
dV_fan_a_max = None
```

기본값은 기존 동작 보존 가능:

```python
if dV_fan_a_max is None:
    dV_fan_a_max = dV_fan_a_rated
```

하지만 **수식 normalization과 solver bound는 분리**.

### UA scaling

\[
UA
=
UA_{ref}
\left(
\frac{\dot V_{fan}}{\dot V_{fan,ref}}
\right)^n
\]

- `dV_fan_ref`는 고정
- `dV_fan_max > dV_fan_ref` 허용
- ratio > 1 허용

### Fan power

\[
x=\frac{\dot V_{fan}}{\dot V_{fan,ref}}
\]

VSD polynomial도 x > 1 계산 가능하도록 하되:
- coefficient calibration 범위를 벗어나면 warning 가능
- `rated`를 임의 max clamp로 사용하지 말 것

---

# 5. `heat_exchanger.py`

`calc_HX_perf_for_target_heat()` 수정.

기존:

```python
dV_fan_rated
search = 0.05–1.0 × rated
```

변경:

```python
dV_fan_ref
dV_fan_min
dV_fan_max
```

예:

```python
UA = calc_UA_from_dV_fan(
    dV_fan=current,
    dV_fan_ref=reference,
    ...
)

root_scalar(
    ...,
    bracket=[dV_fan_min, dV_fan_max]
)
```

`rated`는 UA/reference condition일 뿐 solver ceiling이 아니어야 함.

---

# 6. ASHP / ASHPB / GSHP / WSHPB 전체 audit

다음 파일에서 **`rated`, `design`, `ratio`, `max` 사용처 전수 확인**:

```text
air_source_heat_pump.py
air_source_heat_pump_boiler.py
ground_source_heat_pump.py
ground_source_heat_pump_boiler.py
water_source_heat_pump_boiler.py
hx_fan.py
heat_exchanger.py
ground_loop.py
ground_flow_control.py
compressor_speed.py
```

각 변수마다 아래 중 하나로 분류:

```text
A. physical reference/rated value
B. normalization denominator
C. control setpoint
D. hardware min/max limit
```

한 변수가 B와 D를 동시에 담당하면 분리.

---

# 7. Compressor 관련

Compressor는 이미:

```python
rps_min
rps_max
```

가 별도로 존재하므로 이 구조를 유지.

확인할 것:

- `V_cmp_ref`가 max displacement/speed 의미로 잘못 사용되는 곳 없는지
- normalized speed 식이 있다면:
\[
n^*=\frac{n}{n_{ref}}
\]
로 정의
- `n_ref`와 `rps_max`를 동일시하지 말 것
- 효율모델 \(\eta(PR,n^*)\)에서 ratio > 1이 필요하면 허용

---

# 8. UA 관련 전체 규칙

`UA_rated` / `UA_ref`는:

> **정격/reference 운전점의 conductance**

이지 최대 UA가 아님.

따라서:

\[
UA(flow>flow_{ref}) > UA_{ref}
\]

가능.

다음과 같은 clamp가 있으면 제거/검토:

```python
min(UA, UA_rated)
ratio = min(flow / rated_flow, 1.0)
```

단, 실제 correlation validity limit이 있으면 **UA max가 아니라 correlation validity bound**로 별도 정의.

---

# 9. Backward compatibility

기존 API 즉시 삭제 금지.

예:

```python
dV_b_f_lpm
ground_flow_min_ratio
ground_flow_max_ratio
dV_fan_a_rated
```

는 한 버전 이상 유지하되 내부에서 신규 변수로 mapping.

예:

```python
ground_flow_ref_lpm = dV_b_f_lpm

if ground_flow_min_lpm is None:
    ground_flow_min_lpm = ground_flow_min_ratio * ground_flow_ref_lpm

if ground_flow_max_lpm is None:
    ground_flow_max_lpm = ground_flow_max_ratio * ground_flow_ref_lpm
```

Deprecation warning 추가.

---

# 10. Tests

## Ground flow

```text
reference = 24 L/min
constant  = 24 L/min
max       = 36 L/min
```

확인:

```text
24 L/min → ratio_to_ref = 1.00
36 L/min → ratio_to_ref = 1.50
```

36 L/min에서:
- UA > UA_ref 가능
- Rb*는 actual branch flow로 계산
- pump power 정상 증가
- optimizer가 필요하면 24 L/min 초과 선택 가능

## Fairness test

동일 actual flow 24 L/min를 두 case에 강제했을 때:

```text
Constant-flow result
Variable-flow prescribed 24 L/min result
```

의:
- UA
- Rb*
- pump power
- compressor state
- fan power

가 동일해야 함.

## Fan

```text
fan_ref = 1.0 m3/s
fan_max = 1.2 m3/s
```

1.2 m3/s 운전 가능하며:

\[
\dot V/\dot V_{ref}=1.2
\]

로 UA/power scaling 되는지 확인.

## Regression

- 기존 args만 사용하면 기존 결과 유지
- explicit max를 새로 줄 때만 expanded operating range 사용

---

# 11. 논문 GSHP case 재실행

## 최종 논문 조건

```text
ground_flow_ref_lpm      = 24
ground_flow_constant_lpm = 24
ground_flow_min_lpm      = 9.6   # 0.4 × ref
ground_flow_max_lpm      = 36.0  # 1.5 × ref
```

즉 정유량은 항상 24 L/min으로 운전하고,
변유량은 **9.6–36 L/min** 범위에서 \(E_{tot}\)가 최소가 되는 유량을 선택한다.

모든 UA / fan / Rb scaling의 denominator는 계속 **24 L/min reference**를 사용한다.
36 L/min을 새 rated/reference로 재정의하지 않는다.

리팩터링 후 sensitivity:

```text
ground_flow_max / ref = 1.0
ground_flow_max / ref = 1.2
ground_flow_max / ref = 1.5
```

확인:

- high PLR에서 optimum이 upper bound에 붙는지
- \(E_{pmp}\)
- \(E_{fan}\)
- \(E_{cmp}\)
- \(E_{tot}\)
- system COP

### 해석

```text
1.0에서 bound
1.2에서 interior optimum
1.4에서도 같은 optimum
→ 실제 optimum 확인
```

반대로 계속 max에 붙으면:
- pump pressure-drop model
- common resistance
- UA flow scaling
를 재검토.

---

# 12. Naming 원칙 문서화

TMHP developer docs에 추가:

> `rated` means a reference operating point, not a maximum operating limit.

> Any physical/control limit must use an explicit `_min` or `_max` parameter.

> Ratios must state their denominator in the variable name when ambiguity exists, e.g. `flow_ratio_to_ref`.

---

# 최종 산출물

- 수정 코드
- 신규/deprecated API 목록
- 전체 `rated/reference/max` audit 표
- unit/regression tests
- GSHP sensitivity CSV
- 논문 Figure 재생성
- 짧은 migration note

---

# 금지

- variable-flow max를 바꾸면서 UA/Rb/fan scaling denominator까지 같이 변경 금지
- `rated == max` 암묵적 가정 금지
- ratio를 무조건 `[0,1]`로 clamp 금지
- 동일 actual operating point가 control mode에 따라 다른 component physics를 갖게 만들지 말 것
