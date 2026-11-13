# 기본 입력값 카탈로그 아카이브 (#86)

사용자가 압축기 배제체적과 팬 최대 풍량을 주지 않을 때 TMHP가 쓰는 기본값의 근거 자료다. 정격 용량을
x로 하는 회귀식을 제조사 카탈로그 원문에서 만든다. 아카이브는 둘이다.

| 폴더 | 응답 | 층(stratum) |
|---|---|---|
| [compressor_displacement/](compressor_displacement/README.md) | 기하 배제체적 [cm³/rev] | 압축기 형식 × 속도 기준 (냉매·시험 조건은 rho_1 · Δh로 정규화) |
| [fan_airflow/](fan_airflow/README.md) | 최대 풍량 [m³/s] | 모드 × 측(실내/실외) × 형식 |

두 폴더 모두 사람이 고치는 파일(`sources.json`, `observations/<source_id>.csv`, `errata.csv`)과 빌드가 만드는
파일(`measurements.*`, `regression.json`, `audit.json`), 덧붙이기만 하는 `releases.json`으로 나뉜다. 공통
빌드·정오표·릴리스 기록은 `archive.py`, 적합 함수는 `regression.py`에 있다.

## 수집 기준

새 자료를 넣을 때마다 아래를 확인한다.

| 항목 | 기준 |
|---|---|
| 원문 | 제조사 공식 도메인의 문서만. 대리점·2차 가공 자료는 쓰지 않는다 |
| 출처 기록 | 문서 ID·개정판, URL, 접근일, SHA-256, PDF 전체 쪽수, 값이 있는 PDF 쪽과 인쇄 쪽, 표 위치 |
| 원본 보관 | 원본은 연구실 NAS(`energy-systems/<id>-source/files/source/`)에 두고 git에는 넣지 않는다. git에는 전사값·해시·위치만. `sources.json`의 `nas_path`, 같은 원문이 #87 대장에 있으면 `archive87_dataset_id` |
| 전사 | 인쇄된 값과 단위를 그대로(Btu/h, cm³/rev, m³/h, CMM). 환산은 빌드가 한다 |
| 물성 정규화 | 원문 값은 바꾸지 않는다. 배제체적 아카이브의 빌드는 행마다 그 시험 조건의 rho_1 · Δh(CoolProp)를 계산하고, 실효 회전수 eta_vol · rps = Q_nom / (V_cmp_ref · rho_1 · Δh)를 산출한다 |
| 원문 출처가 끊긴 경우 | 제조사 공식 도메인 원문을 우선한다. 공식 최신판이 조건 라벨만 쓰고 숫자 정의를 생략하면, 같은 제조사의 이전 판 정의를 `conditions` 출처로 인용한다(배포처 사본이면 그 사실과 해시 일치를 기록) |
| 정정 | `errata.csv`로만. 바꾸기 전 값, 근거 쪽, 사유를 함께 |
| 섞으면 안 되는 경계 | 정격/최대/High 풍량, 냉방/난방, 공급 주파수와 실제 축 회전수, 같은 모델명의 전압 변형, 개발품 표시. 압축기 시험 규격(Te/Tc/과열도/과냉도)은 행마다 기록하고, 서로 다른 규격은 rho_1 · Δh로 정규화한 뒤에만 묶는다 |
| 표기 정규화 | 냉매 표기(`R32`, `R-32`, `R32(*1)`)는 원문 열 `refrigerant`에 그대로 두고, 빌드가 `refrigerant_normalized`를 만든다. 층은 정규화 열로 나눈다 |

## 필터 기준

| 기준 | 내용 |
|---|---|
| 제품 범위 | 1:1 단일 스플릿만. 멀티·VRF 제외 |
| 실내기 층 | 벽걸이·스탠드를 나눈다. 카세트·덕트는 범위 밖(2026-10-26 결정, 2026-11-13 재확인) |
| 형식별 층 | 공기-공기 스플릿, 물-공기 지열 히트펌프(GSHP 실내), 공기-물 실외기(ASHPB)를 섞지 않는다 |
| 중복 | 지역판·OEM 재표기로 같은 사양이 반복되면 하나만 남긴다 |
| 최대 풍량 | 같은 제품·모드에서 가장 큰 원문 High 값. 단계 표기가 없는 실내 풍량은 최대로 보지 않는다 |
| 이상치 | 통계적으로 지우지 않는다. 원문과 다시 대조해 정오표로만 고친다 |
| 출고 조건 | 제품군 3개 이상, 제조사 2곳 이상. 못 미치면 기본값으로 내보내지 않는다 |
| 외삽 | 관측 용량 범위 밖이면 값을 내되 외삽으로 표시한다 |
| 배제체적 식 | V_cmp_ref = Q_nom / (rho_1 · Δh · eta_vol · rps). eta_vol · rps / f는 압축기 형식·속도 기준(basis)별로 제품군 균등 가중 기하평균. 냉매·시험 조건 차이는 rho_1 · Δh가 맡는다. 50 Hz 정속과 60 rps 인버터는 섞지 않는다 |
| 풍량 회귀 방법 | 제품군 균등 가중 최소제곱. 비례·선형·거듭제곱 중 제품군 보류 교차검증 최저 RMSE의 2 % 이내에서 가장 단순한 식 |

## 실행

저장소 루트에서 실행한다. 감사(`audit`)는 원문 파일이 `validation/evidence/default_inputs/`(git 제외)에 있어야
하고, NAS 검사는 `/mnt/datasets`(읽기 전용 NFS)를 읽는다.

```bash
uv run python -m validation.default_inputs.compressor_displacement.build
uv run python -m validation.default_inputs.fan_airflow.build
uv run --with pymupdf python -m validation.default_inputs.compressor_displacement.audit
uv run --with pymupdf python -m validation.default_inputs.fan_airflow.audit
uv run python -m validation.default_inputs.nas_check
```

NAS 반입 기록: [nas-transfer-20261113.json](nas-transfer-20261113.json)(#87 대장에 없던 2건), [nas-transfer-20261114.json](nas-transfer-20261114.json)(SCI 2026 공식판). 나머지 21건은
#87(2026-10-27)이 반입·검증한 파일과 SHA-256이 같다.
