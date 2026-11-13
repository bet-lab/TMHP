# 압축기 배제용적 자료 확장 — 2026-10-25

- 사용자 코멘트: Figma 1980184775 / 슬라이드2574:17717.
- 결과: 114개 관측행 중 적격113개, 2개 비교 프로토콜 × 냉매별6개 후보 함수. 세 제조사(Panasonic·Highly·LG)와 R22·R32·R410A·R290를 포함한다.
- near15kW 비교는 LG의 동일 ARI/변속 시험주파수60Hz/twin군이다. R32상한14.877kW·R410A상한13.921kW다. 공급/시험주파수를 측정 축속도60rps로 바꾸지 않았다.
- 기존 패키지 기본값(legacy)과 기존 자동 입력 프로필은 변경하지 않았다. 2026-10-26 이 선정식을 opt-in 프로필 `single_split_displacement_regression`으로 추가했다(이슈 #86).

## 왜 기존 그림이 좁았는가

- 원래92행에는 R32·R410A·R290가 있고 R32 twin 냉방은25.27kW까지다. 이전 슬라이드는 R32 single/P-JIS57.5Hz19행(1.355–3.465kW)만 선택해 나타냈다.
- 기존 세 곡선은 비례·선형·멱함수 후보식이다. 새 그림은 같은 검증 규칙에서 선정한 식만 냉매별로 한 개 표시한다.
- 원래 자료를 속도비로 환산하거나 배제용적을 바꿔 범위를 채우지 않았다.

## 비교 프로토콜

| 항목 | A: 고정속도 single | B: 변속 twin |
|---|---|---|
| 냉매측 경계 | Te7.2°C/Tc54.4°C/Ts35.0°C/Tl46.1°C | Te7.2°C/Tc54.4°C/Ts18.3°C/Tl46.1°C |
| 속도 정보 | 명시된 공급주파수50Hz | LG 공식 제품 페이지의 시험주파수60Hz |
| 제조사 | Panasonic·Highly·LG | LG |
| 냉매 | R22·R32·R410A·R290 | R32·R410A |
| 원천 조건 표기 | N-TEC / ASHRAE-T / ASHRAE | ARI |

- A는 냉매측 온도경계·같은 공급주파수·같은 single유형을 맞췄다. LG ambient는 미기재이며35°C로 채우지 않았다. Panasonic·Highly는 ambient35°C가 원문에 있다.
- LG는 SH/SC를 명시하므로 Ts=Te+SH, Tl=Tc−SC로 절대 경계를 정규화했다. 원문 SH/SC와 조건 페이지를 남겼다.
- B의 시험주파수는 공식LG제품페이지의 설명을 보완출처로 연결했다. frequency_confirmation을 cross_document_manufacturer_default로 명시했으며, 2024년 각 모델 용량행에 주파수가 직접 적혀 있다고 주장하지 않는다. 실측 shaft speed가 아니다. 다른 SH/속도/압축기유형인 A와 B를 한 회귀로 섞지 않는다.

## 회귀와 독립 검증

- x=냉방용량[kW], y=기하학적 배제용적[cm³/rev]. 제품군 균등 가중 피팅이다.
- 제품군을 통째로 보류한 중첩 교차검증으로 식을 고르고, 최저RMSE의2%이내이면 단순식을 우선한다.
- 모델 숫자 토큰을 묶어 접미부 변형이 학습/검증 양쪽에 들어가지 않게 한 부차 검증도 수행했다. A에서 제조사가2개이상이면 제조사 전체를 보류한 검증도 별도 기록했다.
- 아래 MAE/RMSE/상대오차는 nested family holdout이며 독립 실측 제품 전이 검증이 아니다.

| 프로토콜 | 냉매 | n | 용량[kW] | 선정식 | MAE[cm³/rev] | RMSE[cm³/rev] | MAPE(%) |
|---|---|---|---|---|---|---|---|
| A | R410A | 58 | 0.96–8.04 | 4.04 Q + 0.25 | 0.43 | 0.51 | 3.29 |
| A | R290 | 11 | 0.95–5.47 | 6.84 Q^0.99 | 0.45 | 0.62 | 2.25 |
| A | R32 | 5 | 2.26–5.90 | 3.93 Q | 0.57 | 0.70 | 3.58 |
| A | R22 | 35 | 2.22–8.30 | 5.86 Q | 0.47 | 0.70 | 1.46 |
| B | R32 | 15 | 4.31–14.88 | 3.05 Q | 0.71 | 1.07 | 2.23 |
| B | R410A | 12 | 3.92–13.92 | 3.09 Q + 1.11 | 0.39 | 0.43 | 2.07 |

- 표시식은 소수 둘째 자리로 줄인 설명용이며 정확한 계수는 regression.json에 보존한다.
- 제조사 보류 검증: A-R410A RMSE 0.53 cm³/rev·MAPE 3.57 %, A-R290 RMSE 0.56 cm³/rev·MAPE 1.95 %, A-R22 RMSE 0.67 cm³/rev·MAPE 1.05 %. 나머지 곡선은 단일 제조사여서 제조사 간 검증이 없다.
- 위 표와 이 줄은 릴리스 displacement-v2026-10-26(추가 수집과 정오표 반영 후) 값이다.

## 원문값과 제외

- 기하값 직접 표기74행, 제조사 명명법 해독40행. LG의 원문은141=14.1cc/rev로 정의한다(PDF5쪽/인쇄9쪽). 모델 토큰442→44.2처럼 공개된 규칙으로만 단위를 해독했다. raw code·규칙 페이지·변환계수0.1·정규화값을 따로 보존했다. 용량이나 속도비로 배제용적을 보정한 것이 아니다.
- LG DAT134MC의 원문 용량은3941W와14535Btu/h로 서로 모순이다(PDF6쪽/인쇄10쪽). 후자를 환산하면4259.79W이며 W기준8.09% 차이다. 한쪽을 임의 수정하지 않고 원문 두 값을 보존한 뒤 이1행은 피팅에서 제외했다.
- Highly의 G/TH페이지에 반복된 같은 R290행은 중복 제거했다. 같은TH모델의 전압별 관측은 같은 model-group에 묶어 누수를 막았다.
- Highly5개 웹표는 Chrome으로 공식HTML을 확보하고 PDF스냅샷을 만들었다. 제조사가 배포한 원본PDF라고 부르지 않는다. ASHRAE/T온도 정의는 기존2015공식PDF2쪽에 연결했다.

## 범위와 한계

- GPT442MA는 일반 AC표(PDF6쪽)에서13.921kW/4241W, 미국 Unitary표(PDF10쪽)에서 ARI@60Hz13.773kW/4360W로 반복된다. 표의 시장 범위가 다르지만 이148W(약1.06%) 용량 차이의 구동·전압·판본 원인은 확인되지 않았다. 같은모델값을 평균하거나 보정하지 않고 선택 표의 원문과 교차표 값을 함께 보존했다.

- A에서 세 제조사가 함께 있는 냉매는R410A다. R290는Panasonic·Highly, R32와R22는Highly뿐이다. 곡선을 냉매만의 인과효과로 해석하지 않는다.
- B의 약15kW구간은LG만이다. R410A의7.5kW이상2점은같은GPT제품군이며, 고용량에서 경향이 바뀌는 변곡점·기울기를 독립적으로 식별할 근거가 충분하지 않다. R32고용량은DKT/DJT/DPT3제품군7점이다.
- 일부 PanasonicR290는제습용이다. 동일냉매경계의단품비교이며, 완제품열펌프용으로전이검증한것이아니다.
- 선은 각 냉매의 확보 용량범위 안에서만 그린다. 산점도는기존s의0.7배,선은동일냉매색의더진한색을사용한다. 후보3개곡선·범위밖연장선을보여주지않는다.

## 재현·검증

절차와 파일 역할은 [README.md](README.md)에 있다. 요약하면 다음과 같다.

- `uv run python -m validation.default_inputs.compressor_displacement.build`: 관측 CSV → measurements → 적합. 최신 릴리스 대비 바뀐 점을 출력한다.
- `uv run python -m validation.default_inputs.compressor_displacement.audit`: 원본 PDF/HTML 해시·쪽수, 행의 쪽번호, 배제체적 해독 산술, 보류 검증 누수 여부를 검사한다.
- `... build --release <버전> --note "..."` 다음 `... export`: 릴리스를 기록하고 패키지 자료 `src/tmhp/data/compressor_displacement_regression.json`으로 내보낸다(이슈 #86). 라이브러리에서는 `default_input_profile="single_split_displacement_regression"`로 쓴다. 패키지와 최신 릴리스가 다르면 `tests/test_displacement_regression.py`가 실패한다.
- 정확한 계수와 자료 SHA-256은 `regression.json`과 `releases.json`을 따른다.

## 출처

- [LG 공식 Rotary Catalogue](https://www.lg.com/global/images/business/compressor-motor/resource-download/pdf-file/LG_Rotary_Compressor_Catalogue.pdf): PDF5쪽명명법,6쪽용량,7쪽50Hz용량,10쪽조건.
- [LG 공식 Rotary 제품 페이지](https://www.lg.com/global/business/compressor-motor/compressor/rotary-compressor/): 시험주파수설명,Chrome스냅샷4쪽.
- [Panasonic 공식 CGZ2025](https://industrial.panasonic.com/cdbs/www-data/pdf/ADM0000/ast-ind-368085.pdf): 기존행재사용·원문기하값유지.
- [Highly 공식 제품군](https://www.highly.co.in/compressor-series.html): D/G/L/H/TH원문표를연결보존.
- [Highly 공식2015PDF](https://www.highly.co.in/pdf/recommended-models.pdf):2쪽ASHRAE/T온도정의.

## 2026-10-25 추가 수집 (Figma 코멘트 1980214259 / 슬라이드 2653:36498)

- 목표였던 6–15 kW 구간을 프로토콜 A·B 조건까지 맞춰 채우는 **새 공식 출처는 찾지 못했다.** 탐색한 곳: Samsung 2018 카탈로그·공식 웹, Panasonic 웹 모델 페이지(응답 없음), GMCC-Welling(JS 렌더), Highly 공식 웹(배제용적 열 없음), Mitsubishi Electric·Toshiba-Carrier·Rechi·Landa·SHHC(공식 카탈로그 미발견).
- 기존 PANASONIC-CGZ-2025 PDF 안의 미추출 표에서 프로토콜 A(N-TEC·50 Hz·single)에 엄격히 맞는 23행을 추가했다: R22 정속(인쇄 19쪽) 8행, T3 중동향(인쇄 20쪽) R410A 4행·R22 2행, 제습기 R410A(인쇄 30쪽) 9행. 원문 렌더 이미지와 대조했고 W·Btu/h 두 열은 0.01 % 이내로 일치한다. 원천 행은 `observations/PANASONIC-CGZ-2025.csv`에 있다.
- 결과: 적격 113 → 136행. R22는 Highly·Panasonic 두 제조사(35행, 2.2–8.3 kW)가 되었다. A-R410A는 58행이며 선정식이 비례식에서 선형식으로 바뀌었다. 용량 상한은 바뀌지 않았다.
- 채택하지 않은 근접 후보: Panasonic R410A ARI twin 18행(4.7–25.5 kW, 시험 57.5 Hz로 B의 60 Hz와 다름), Samsung 50 Hz R22·R410A 39행(단일/트윈 표기 없음), Samsung 웹 Variable 표(조건 정의 없음).
- 별도 발견: 기존 라이브러리 행 `7VS355EA*`(R290)의 배제용적이 35.5로 들어가 있으나 원문 PDF 16쪽(인쇄 30쪽)은 35.6이다. 패키지 기본값 자료라 이 작업에서 고치지 않았다.

## 2026-10-26 아카이브 개편과 정오표

- 출처마다 다르던 추출 코드를 걷어 내고, 원문 값을 그대로 적은 출처별 관측 CSV(`observations/`)를 원천으로 삼았다. 출처 공통 사실은 `sources.json`, 시험 규격은 `protocols.json`, 정정은 `errata.csv`에 둔다. 빌드할 때마다 최신 릴리스 대비 변화가 나오고, 릴리스는 `releases.json`에 쌓인다.
- 이전 빌드를 그대로 옮긴 것이 `displacement-v2026-10-25`다. 137행 모두 값·제품군·적격 여부가 같고, 계수 차이는 행 순서에 따른 1e-14 이하뿐이다. 기계 판독 가능한 LG·Highly 원천은 `harvest/harvest.py`로 다시 추출해 보관 CSV와 같은지 확인했다.
- `displacement-v2026-10-26`: 정오표 1건. `7VS355EA*`(R290, PDF 16쪽·인쇄 30쪽)의 배제체적을 35.5에서 원문 값 35.6으로 고쳤다. 35.5는 같은 쪽 R454C 행 `GVS355EAA41`의 값이다. 바뀐 것은 50 Hz R290 계수(6.8441 Q^0.98542 → 6.8407 Q^0.98624)뿐이다. 136점 전체의 제품군 보류 오차는 MAE 0.47·RMSE 0.66 cm³/rev, MAPE 2.52 %다.
- 2026-10-22 기본 입력 자료(`../data/measurements.json`)의 같은 행은 별도 릴리스(`catalogue-v2026-10-22`)라 고치지 않았다.
