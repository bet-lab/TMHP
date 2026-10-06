# BITZER 압축기 세 효율 pilot · v2 재검증

## 핵심 결론

BITZER 제조사 계산자료로 TMHP 압축기의 체적효율(η_v), 등엔트로피 효율(η_is), 전기기계 효율(η_em)을 압력비·속도의 함수로 독립 모델링할 수 있는지 점검했다. **η_v는 가정을 명시하면 사용할 수 있지만, η_is와 η_em의 독립 분리는 BITZER만으로 신뢰하기 어렵다.** 토출온도 없이 계산하는 곱 η_oi=η_is·η_em은 조건부 모델링이 가능하다.

권고는 **압축기 형식별 회귀와 기계별 기준점 정규화**다. 스크롤의 저속 전체효율 손실은 나타나지만, 세 효율을 모두 독립 식별했다는 뜻은 아니다. v2 pilot과 별도로 고정한 스크롤 자료의 ASHP 영향 분석을 구분해 읽어야 한다.

v2에서는 요청·반환 주파수가 달랐던 왕복동 25점을 회귀에서 제외했다. 분석점은 초기 3,604점에서 **3,579점**으로 줄었다. VARISPEED 전력 경계도 원자료의 「인버터 입력단」 표시로 바로잡았다. 제조사 값은 독립 실험점이 아니며, TMHP 기본 계수와 런타임을 변경하지 않았다.

## 1. 자료 출처와 운전 범위

출처는 [BITZER Software](https://www.bitzer.de/websoftware/) 웹판 **v7.1.11.4**, 추출·재점검일 **2026-10-04**다. 현재 Chrome의 ORBIT 화면에서 External FI 옵션과 버전·잠정자료 표시를 재확인했다. 최초 UI–백엔드 대조 기록에 이어, 같은 백엔드의 익명 세션으로 스크롤 10점을 다시 추출해 주파수 반환과 단위를 확인했다. 전수 인벤토리 102개 모델–냉매 항목 중 ORBIT Boreal 고정속도 10항목은 속도 회귀에서 제외했다.

| 형식 / 냉매 | 압축기 모델 | 하드웨어 수 | 회귀점 | 주파수 Hz | 압력비 |
| --- | --- | --- | --- | --- | --- |
| reciprocating / R134a | 2DES-3.F1, 4CE-6.F1, 4FE-5.F1 | 3 | 649 | 25–87 | 2.54–9.10 |
| screw / R134a | HSK5343-30, HSK6451-40, HSK7451-50 | 3 | 463 | 25–75 | 2.54–9.10 |
| scroll / R32 | GSD60120VL, GSD60182VL, GSD80295VL, GSD80485VL | 4 | 626 | 35–75 | 2.30–5.08 |
| scroll / R410A | GSD60120VA, GSD60182VA, GSD80235VA, GSD80385VA, GSD80485VA | 5 | 1040 | 35–75 | 2.29–7.15 |
| scroll / R454B | GSD60120VL, GSD60182VL, GSD80295VL, GSD80485VL | 4 | 801 | 35–75 | 2.30–6.45 |


R32·R454B의 GSD…VL은 같은 하드웨어다. 총 **19개 모델–냉매 조합, 하드웨어 15종**이며 19대의 독립 압축기로 세지 않았다. 스크롤은 외부 인버터, VARISPEED 왕복동은 내장 인버터, HS 스크루는 외부 인버터 운전이다. 왕복동 그룹은 25–87 Hz지만 **2DES-3.F1의 최저속도는 30 Hz**다. 25 Hz 요청이 다른 속도를 반환한 점을 25 Hz 데이터로 취급하지 않았다. [제품 주파수 근거](https://www.bitzer.de/de/de/hubkolbenverdichter/ecoline-varispeed/?country=de).

기본 격자: 증발 포화온도(SST) −15/−10/−5/0/5 °C × 응축 포화온도(SDT) 35/40/45/50/55 °C × 형식별 주파수 9점. 흡입 과열(SH) **10 K**, 액 과냉(SC) **0 K**, 포화압력은 이슬점 기준이다. 거절된 운전점은 보존했고, 운전범위 밖 값을 강제로 만들지 않았다. 선택한 pilot에는 R290 모델을 포함하지 않았다.

**ASHP 저압력비 자료의 출처:** 같은 BITZER Software 백엔드에서 13개 스크롤–냉매 조합을 추가 계산했다. SST 10/15 °C 및 SDT 25–55 °C를 중심으로 35–75 Hz, SH/SC=10/0 K에서 1,638회 요청해 1,432점을 확보했다. 기존 스크롤 2,467점과 합친 고정 ASHP 자료는 3,899점이다. 이 보강자료는 §6–9의 ASHP 영향 분석에만 사용하며, v2의 3,579점 회귀와 구분한다. 낮은 압력비는 낮은 외기온도 자체를 뜻하지 않는다.

## 2. 세 효율을 어떻게 계산했나

TMHP와 같은 CoolProp HEOS 물성을 사용했다. 흡입 상태는 P₁=P_sat,dew(SST), T₁=SST+SH이며 h₁·s₁·ρ₁를 계산했다. 토출압력 P₂=P_sat,dew(SDT)에서 h₂s=h(P₂,s₁), BITZER 토출온도에서 h₂=h(P₂,T_dis)를 구했다.

| 양 | 식 | 뜻·조건 |
| --- | --- | --- |
| r_p | P₂/P₁ | 토출/흡입 절대압력비 |
| η_v | ṁ/(ρ₁ V_d N) | 체적효율 |
| η_is(app) | (h₂s−h₁)/(h₂−h₁) | BITZER 토출온도를 사용한 겉보기 등엔트로피 효율 |
| η_em(app) | ṁ(h₂−h₁)/P_el | 겉보기 냉매 엔탈피 증가/전기 입력 비 |
| η_oi | ṁ(h₂s−h₁)/P_el = η_is(app)·η_em(app) | 토출온도 없이 계산되는 전체 효율 |
| n_f* | f/(50 Hz) | 공칭 주파수 대비 비; 실제 축속도 비로 단정하지 않음 |


ṁ는 질량유량 [kg/s], ρ₁는 흡입 밀도 [kg/m³], V_d는 회전당 배기량 [m³/rev], N은 축 회전수 [rev/s], h는 비엔탈피 [J/kg], P_el은 전기 입력 [W]다. app는 apparent, 즉 독립 실측으로 확인되지 않은 계산상 분리를 뜻한다. TMHP의 정의 E_cmp=ṁ(h₂−h₁)/η_em 및 h₂=h₁+(h₂s−h₁)/η_is와 수식은 같지만, 입력자료의 물리적 의미까지 같다는 뜻은 아니다.

원자료에는 반환 주파수와 요청 주파수를 모두 보존했다. 실제 rpm은 제공되지 않아 빈 값으로 두었다. 체적효율에 쓰는 **가정 회전수 N=f×rpm₅₀/50/60**은 별도 열 N_rps_assumed에 저장했다. 스크롤·스크루의 공칭 2,900 rpm, 왕복동의 1,450 rpm을 쓰는 일정 상대슬립 가정이다. m³/h 배기유량은 V_d=(배기유량/3,600)/(rpm₅₀/60)으로 변환했다. [스크롤 속도 근거](https://www.bitzer.de/shared_media/html/est-420/en-GB/298527755298539787.html).

스크루 자료에는 질량유량이 없어 ṁ=Q₀/(h₁−h₃)로 역산했다. h₃는 응축압력의 포화액 엔탈피이며 SC=0 K 가정을 쓴다. R454B는 HEOS R32/R1234yf 혼합물로 계산했다. 이 가정과 원자료 경계를 함께 읽어야 한다.

별도 ASHP 영향 분석의 PLR은 요구 열량/명목 3,500 W다. COP_ref=Q_useful/E_cmp, COP_sys=Q_useful/(E_cmp+E_ou_fan+E_iu_fan)이며, Q_useful은 실제 공급 열량 또는 냉동능력, E_cmp는 압축기 전력, E_ou_fan/E_iu_fan은 실외/실내 팬 전력이다.

## 3. 자료 품질과 독립 식별의 한계

| 항목 | 점 수 / 관찰 | 처리 |
| --- | --- | --- |
| 제조사 요청 / 수락 / 거절 | 4,275 / 3,604 / 671 | 거절 이유·빈 성능값을 원자료에 보존 |
| 요청 ≠ 반환 주파수 | 왕복동 25점; 반환 좌표가 기존 점과 중복 | 원자료 보존, 회귀 가중치 0 → 3,579점 |
| η_v > 1 | 174점 | flag 유지; 회귀 전 clipping 없음 |
| η_em(app) > 1 | 300점, 최대 약 1.004 | flag 유지; 반올림·상태 정의·전력 경계의 영향 조사 |
| 추가 냉각 요구 | 스크루 212점 | w/o cooling 토출온도를 실제 냉각 후 상태로 쓰지 않음; η_is/η_em 진단 회귀에서 제외 |
| 토출온도 누락 | 1점 | η_is/η_em에는 사용하지 않음 |
| 물리적 이상값 | 삭제·보정 없이 보존 | 제조사 운전범위 오류와 상태·단위 문제를 구분 |


BITZER의 항목명은 「Discharge gas temp. w/o cooling」다. 이 온도가 독립 측정한 압축기 출구인지, 모터 발열을 포함하는 내부 계산점인지까지 확인되는 정의를 확보하지 못했다. 냉각 요구가 없는 점에서 응축기 열수지는 냉동능력+전력과 거의 닫히고, η_em(app)은 스크롤 약 0.99–1.00, 반밀폐 약 0.97로 거의 일정하다. **에너지 수지와 고정 열손실을 반영하는 계산값이라는 해석과 일치하지만, 소프트웨어 내부 알고리즘을 직접 확인한 것은 아니다.**

| 형식 | 전력 경계 | 해석 |
| --- | --- | --- |
| ORBIT 스크롤 | 압축기 단자 기준; 외부 인버터 손실 별도 | TMHP 구동계 전체 손실과 바로 동일시하지 않음 |
| VARISPEED 왕복동 | 원자료에 Power consumption at frequency inverter inlet 표시 | 내장 인버터 입력단 전력; 미확인으로 남기지 않음 |
| HS 스크루 | 외부 인버터 운전의 압축기 전력 | 별도 인버터 손실을 추가한 회로 입력으로 취급하지 않음 |


흡입가스 냉각형에서 모터 손실이 냉매 엔탈피에 돌아오면 ṁ(h₂−h₁)/P_el은 모터 효율만을 뜻하지 않는다. 따라서 η_em(app)의 작은 회귀 오차는 전기기계 효율을 독립 식별했다는 증거가 아니다. 형식별 전력 경계가 다르므로 global 회귀의 오차 감소를 순수한 압축기 구조 효과로만 해석하지도 않았다.

| 대상 | 판정 | 후속 적용 |
| --- | --- | --- |
| η_v | B · 가정을 명시하면 사용 가능 | 주파수–축속도·배기량 가정 확인 필요 |
| η_is | C · BITZER 단독으로 독립 식별 불가 | 겉보기 진단식만 제시 |
| η_em | C · BITZER 단독으로 독립 식별 불가 | 겉보기 0.97–1.00을 TMHP 모터 효율로 대입하지 않음 |
| η_oi | B · 전력 경계를 명시하면 사용 가능 | 독립 토출온도 자료와 함께 효율 분리 검토 |


**v2 성공 기준 판정:** 자료 취득·압축기 단위 회귀·과학적 한계 판정은 수행했다. 그러나 지침서에 나열된 세 효율 중 최소 두 개를 독립 식별한다는 기준은 충족하지 못했다. η_oi를 그 목록의 두 번째 효율로 바꿔 세지 않았다. 중단 조건 B/C에 따라 η_is·η_em의 확정 물리 모델 채택은 보류하고, 아래 회귀 그림은 진단용으로 구분했다.

## 4. 회귀식·그룹 비교와 그림

후보는 C1: η=a₀+a₁r_p+a₂x, C2: C1+a₃r_px, C3: η=a₀+a₁r_p+a₂x+a₃r_p²+a₄x²+a₅r_px다. x=f [Hz]와 x=n_f*를 모두 적합했다. 모든 기계의 기준이 50 Hz이므로 두 변수는 고정 배율 변환이며, 같은 차수의 다항식 예측·교차검증 성능이 수치 오차 범위에서 같다. rpm을 확보하지 못한 상황에서 가정 N을 실제 관측 속도로 제시하지 않았다.

**Leave-One-Compressor-Out(LOCO) 교차검증:** 하드웨어 한 종의 모든 운전점을 통째로 학습에서 제외하고 나머지로 회귀한 뒤, 제외한 하드웨어로 검증한다. 모든 하드웨어에 반복하며 R32·R454B의 같은 GSD…VL은 함께 제외한다. 운전점 무작위 분할은 사용하지 않았다. MAPE는 평균 절대 백분율 오차다.

| C3 · LOCO MAPE | global | 형식별 | 형식×냉매별 |
| --- | --- | --- | --- |
| η_v | 4.62% | 3.08% | 3.11% |
| η_oi | 6.91% | 4.32% | 4.63% |


형식을 나누면 오차가 줄지만 냉매까지 더 나누면 개선이 거의 없다. 같은 스크롤의 R32·R454B 속도 형상 차이는 관찰되지만, 별도 냉매 계수를 채택할 교차검증 이득은 부족하다. 따라서 **형식별 상관식**을 권고한다.

| 형식별 | 후보 | LOCO MAPE | 기준속도 자료로 보정 후 다른 속도 MAPE |
| --- | --- | --- | --- |
| η_v | C1 | 3.14% | 2.26% |
| η_v | C2 | 3.14% | 2.23% |
| η_v | C3 | 3.08% | 1.95% |
| η_oi | C1 | 4.91% | 4.48% |
| η_oi | C2 | 4.87% | 4.45% |
| η_oi | C3 | 4.32% | 3.36% |


마지막 열은 제외한 기계의 50 Hz 자료로 수준을 맞춘 뒤 다른 속도를 예측한 조건부 전이 오차(ST, speed-transfer error)다. **여러 압력비의 50 Hz 자료를 사용한 보정**이므로 정격 운전점 하나만으로 동일 성능을 얻는다는 증거가 아니다. C3는 속도 곡률을 표현하며 η_oi에서 개선이 더 뚜렷하다. η_v에서는 LOCO 개선이 작아 향후 단순식과 다시 비교할 수 있다.

<details>
<summary>형식별 C3 계수 · x=n_f*</summary>

| 효율 | 형식 | a₀ | a₁ | a₂ | a₃ | a₄ | a₅ | 점 수 | LOCO MAPE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eta_v | reciprocating | 0.88758 | -0.05174 | 0.24274 | 0.00142 | -0.10283 | -0.00026 | 649 | 3.77% |
| eta_v | screw | 0.85974 | -0.03220 | 0.30126 | 0.00042 | -0.13548 | 0.00582 | 463 | 5.38% |
| eta_v | scroll | 0.91280 | -0.05274 | 0.34355 | 0.00228 | -0.17015 | 0.01145 | 2467 | 2.46% |
| eta_oi | reciprocating | 0.52220 | 0.01164 | 0.08262 | -0.00209 | -0.06842 | 0.01101 | 649 | 2.81% |
| eta_oi | screw | 0.51734 | -0.04092 | 0.43162 | -0.00102 | -0.19754 | 0.01332 | 463 | 7.20% |
| eta_oi | scroll | 0.48996 | -0.01447 | 0.57908 | -0.00686 | -0.31021 | 0.03022 | 2467 | 4.18% |

</details>


원자료 산점도의 x/y는 실제 압력비와 주파수 비이며, 색은 효율값만 뜻한다. 기계별 패널·마커로 구분해 좌표를 흔들지 않았다. 대응 열지도는 **같은 색 범위**, 실제점의 다각형(convex hull) 밖은 빈 영역, 실제점 위치는 테두리 마커다. 겉보기 효율은 app 첨자를 붙였다. 모든 새 그림은 **dartwork-mpl MCP로 실제 렌더링**했고, PNG 600 dpi·PDF·SVG 총 121종을 생성했다.

![스크롤 R410A 체적효율 η_v 원자료: 각 패널은 한 압축기 모델이다.](pilot_v2/figures/eta_v_scatter_scroll_R410A.png)
![스크롤 R410A 체적효율 η_v C3 회귀: 원자료와 같은 색 범위, 자료 밖은 표시하지 않는다.](pilot_v2/figures/eta_v_heatmap_scroll_R410A.png)


<details>
<summary>겉보기 등엔트로피 효율 η_is(app) · 원자료와 회귀 비교</summary>

![스크롤 R410A 겉보기 등엔트로피 효율 η_is(app) 원자료: 각 패널은 한 압축기 모델이다.](pilot_v2/figures/eta_is_scatter_scroll_R410A.png)
![스크롤 R410A 겉보기 등엔트로피 효율 η_is(app) C3 회귀: 원자료와 같은 색 범위, 자료 밖은 표시하지 않는다.](pilot_v2/figures/eta_is_heatmap_scroll_R410A.png)

</details>


<details>
<summary>겉보기 전기기계 효율 η_em(app) · 원자료와 회귀 비교</summary>

![스크롤 R410A 겉보기 전기기계 효율 η_em(app) 원자료: 각 패널은 한 압축기 모델이다.](pilot_v2/figures/eta_em_scatter_scroll_R410A.png)
![스크롤 R410A 겉보기 전기기계 효율 η_em(app) C3 회귀: 원자료와 같은 색 범위, 자료 밖은 표시하지 않는다.](pilot_v2/figures/eta_em_heatmap_scroll_R410A.png)

</details>


<details>
<summary>전체 효율 η_oi · 원자료와 회귀 비교</summary>

![스크롤 R410A 전체 효율 η_oi 원자료: 각 패널은 한 압축기 모델이다.](pilot_v2/figures/eta_oi_scatter_scroll_R410A.png)
![스크롤 R410A 전체 효율 η_oi C3 회귀: 원자료와 같은 색 범위, 자료 밖은 표시하지 않는다.](pilot_v2/figures/eta_oi_heatmap_scroll_R410A.png)

</details>


<details>
<summary>냉매·형식별 전체 효율 비교</summary>

![scroll / R32 전체 효율 원자료.](pilot_v2/figures/eta_oi_scatter_scroll_R32.png)
![scroll / R32 전체 효율 C3 회귀. 그룹마다 색 범위가 다르므로 수치는 각 색 막대를 확인한다.](pilot_v2/figures/eta_oi_heatmap_scroll_R32.png)
![scroll / R454B 전체 효율 원자료.](pilot_v2/figures/eta_oi_scatter_scroll_R454B.png)
![scroll / R454B 전체 효율 C3 회귀. 그룹마다 색 범위가 다르므로 수치는 각 색 막대를 확인한다.](pilot_v2/figures/eta_oi_heatmap_scroll_R454B.png)
![reciprocating / R134a 전체 효율 원자료.](pilot_v2/figures/eta_oi_scatter_reciprocating_R134a.png)
![reciprocating / R134a 전체 효율 C3 회귀. 그룹마다 색 범위가 다르므로 수치는 각 색 막대를 확인한다.](pilot_v2/figures/eta_oi_heatmap_reciprocating_R134a.png)
![screw / R134a 전체 효율 원자료.](pilot_v2/figures/eta_oi_scatter_screw_R134a.png)
![screw / R134a 전체 효율 C3 회귀. 그룹마다 색 범위가 다르므로 수치는 각 색 막대를 확인한다.](pilot_v2/figures/eta_oi_heatmap_screw_R134a.png)

</details>


<details>
<summary>압축기 단위 예측과 잔차 진단</summary>

![스크롤 R410A eta_v 진단: 왼쪽은 LOCO 예측–원자료, 가운데/오른쪽은 압력비/속도별 LOCO 잔차.](pilot_v2/figures/eta_v_diagnostics_scroll_R410A.png)
![스크롤 R410A eta_is 진단: 왼쪽은 LOCO 예측–원자료, 가운데/오른쪽은 압력비/속도별 LOCO 잔차.](pilot_v2/figures/eta_is_diagnostics_scroll_R410A.png)
![스크롤 R410A eta_em 진단: 왼쪽은 LOCO 예측–원자료, 가운데/오른쪽은 압력비/속도별 LOCO 잔차.](pilot_v2/figures/eta_em_diagnostics_scroll_R410A.png)
![스크롤 R410A eta_oi 진단: 왼쪽은 LOCO 예측–원자료, 가운데/오른쪽은 압력비/속도별 LOCO 잔차.](pilot_v2/figures/eta_oi_diagnostics_scroll_R410A.png)

</details>


LOCO 잔차는 모델별로 수준 차를 보인다. 예를 들어 스크롤 R410A η_oi의 평균 잔차는 GSD60182VA 약 +0.049, GSD80235VA 약 −0.045다. 단일 기계의 운전점이 독립 표본처럼 늘어나는 효과를 피했다. 계수의 fold별 변동·부호와 잔차는 coefficient_stability.csv, fold_coefficients.csv, predictions.csv에 보존했다. 계수 하나의 부호만으로 보편적 손실 메커니즘을 확정하지 않았다.

## 5. 저속 손실과 후속 판단

같은 하드웨어·냉매·SST/SDT에서 η(f)/η(50 Hz)를 계산하고 운전점별 비의 중앙값을 비교했다. η_is/η_em은 진단용이며 추가 냉각 점을 제외했다. 최저속도를 지원하지 않는 기계는 그 속도 중앙값에 포함하지 않았다.

| 그룹 | 최저 Hz | n_f* | η_v 비 | η_is(app) 비 | η_em(app) 비 | η_oi 비 |
| --- | --- | --- | --- | --- | --- | --- |
| reciprocating / R134a | 25 | 0.50 | 0.945 | 0.976 | 1.000 | 0.975 |
| screw / R134a | 25 | 0.50 | 0.937 | 0.881 | 1.000 | 0.861 |
| scroll / R32 | 35 | 0.70 | 0.988 | 0.956 | 1.000 | 0.956 |
| scroll / R410A | 35 | 0.70 | 0.969 | 0.917 | 1.000 | 0.919 |
| scroll / R454B | 35 | 0.70 | 0.976 | 0.914 | 1.000 | 0.912 |


![같은 운전조건의 50 Hz 대비 효율 비. η_em(app) 패널의 좁은 세로축에서도 변화는 ±0.1% 수준이며, 모터 효율을 식별한 값이 아니다.](pilot_v2/figures/group_comparison_speed.png)


스크롤의 35 Hz 전체 효율 감소는 R32 **4.4%**, R410A **8.1%**, R454B **8.8%**다. 왕복동은 저속 감소가 완만하고, 스크루는 저속으로 갈수록 감소가 더 뚜렷하다. 스크롤·왕복동은 정격 부근 최대의 비단조 형상, 스크루는 표본 범위에서 대체로 속도 증가와 함께 효율이 높아진다. 크기는 기계와 냉매에 따라 달라 모든 운전점에 같은 손실을 부여하지 않았다.

기존 TMHP의 같은 정규화 속도에서 η_oi 비는 약 0.971–0.974(인버터 항 제외), BITZER 스크롤은 0.912–0.956이었다. 속도 기준과 전력 경계가 달라 1:1 성능 검증으로 읽지 않는다. 압력비에 따른 형상과 속도–압력비 상호작용이 있고, 재현 가능한 비교는 해당 데이터 영역 안으로 한정된다.

후속 후보는 **압축기 형식별 η_v·η_oi 형상 + 기계의 기준 성능 수준**이다. η_is/η_em 분리는 별도의 토출온도·전력 경계 자료로 확인해야 한다. 겉보기 효율 0.97–1.00을 기본값으로 대입하거나 회귀 계수를 곧바로 TMHP에 반영하지 않는다.

<details>
<summary>v2 자료·그림·재현 파일</summary>

원자료 4,275 요청, 분석용 3,579점, 216개 후보 회귀, 121개 그림과 MCP 검증 기록을 첨부했다. 원자료 값과 이상점은 보존했다.

분석 경로: validation/bitzer_efficiency_pilot/pilot_v2/. 저장소 루트에서 실행: `.venv/bin/python validation/bitzer_efficiency_pilot/pilot_v2/scripts/analyze.py`. MCP 그림 실행은 plot_mcp.py에 PAYLOAD(해당 plot_*.json 경로), KIND(scatter/heatmap/diagnostics)를 설정해 validate_generated_plot에 전달한다. plot_speed_mcp.py에는 ROOT를 설정한다. pandas·NumPy·SciPy·CoolProp·dartwork-mpl 의존성은 첨부 pyproject.toml/uv.lock을 참고한다.

핵심 출력: data/efficiency_points.csv, data/fit_points.csv, data/regression/regression_coefficients.csv, regression_metrics.csv, metadata/compressor_inventory.csv, data/mcp_render_audit.json. data/verification.json에 단위·곱·교차검증·소스 무변경 검사를 기록했다.

[BITZER Software](https://www.bitzer.de/websoftware/) · [스크롤 속도 한계](https://www.bitzer.de/shared_media/html/est-420/en-GB/298527755298539787.html) · [VARISPEED 모델별 주파수](https://www.bitzer.de/de/de/hubkolbenverdichter/ecoline-varispeed/?country=de)
</details>


본 단계에서는 compressor efficiency model의 데이터 기반 feasibility만 검증했으며, 냉매별 compressor displacement 자동화, V_cmp_ref 자동 결정, rps_rated/reference-state initialization 자동화 및 TMHP runtime 코드 수정은 수행하지 않았다.

기존 ASHP 영향 분석: [별도 보고서](plr_cop/README.md).
