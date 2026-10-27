# TMHP 외부 입력자료 아카이브 인계 · 2026-10-27

계획서의 기존 자료 대조·원문 경계 검토·로컬 인계 준비를 수행했다. **NAS 등록·신규 웹 원본 취득·전체 수치표 추출·warehouse 등록·게시까지 완료한 상태는 아니다.** 메타데이터는 dataset-catalog 초안 PR로, 연구별 채택 판정은 이 목록으로 분리했다. 회귀·물성·효율·COP 평가와 TMHP 모델 변경은 수행하지 않았다.

## 범위와 실제 상태

- 새 인계본: 37개 출처·배포본 묶음, 43파일, 450,867,643 bytes. 원본 위치를 보존하고 복사 전후 SHA-256·bytes·파일 집합·tree hash를 확인했다. 브라우저 PDF 출력 6개는 원문 HTML 6개와 함께 보존하며 `extracted-copy`로 구별했다.
- 기존 등록 재사용: Vigo R410A, R407C 액체–물, hplib. NAS 원본 7파일(1+1+5)의 해시·크기를 확인했다. hplib의 원인증서 전체를 확보했다는 뜻은 아니다.
- `sources.yaml`의 기록 위치 81개는 현재 모두 부재다. LG·Mitsubishi 원본은 다른 위치의 동일 SHA 파일과 연결했다. 기존 그림·캡처·계산 CSV는 외부 입력자료로 새 등록하지 않았다. 다른 바이트의 Cuevas 보유본은 별도 배포본이다.
- source-model-condition 890행과 여섯 목적별 5,340행을 작성했다. 기존 압축기 137행·풍량 624행은 이전 연구의 추출 이력으로만 연결한다. 과거 `eligible`를 승계하지 않고, 이번 미검증 용량은 `recorded_capacity_value_kw`에만 남겼다.
- 실험자료 변수 사전은 161행이다. 2026-10-27 원시 XLSX 헤더·drawing anchor 확인과 기존 2026-10-18 전문 해석 재사용을 구분했다.

자료 현황 128행에는 81개 기존 경로의 별칭도 포함된다. 따라서 행 수를 독립 자료·장치 수로 세면 안 된다. 현재 상태는 있음 3, 일부 44, 없음·취득 가능 여부 미확인 61, 대상 아님 20이다. 빈 CSV 필드와 JSON null은 미확인을 뜻하며 false 또는 0으로 해석하지 않는다.

## 파일과 사용법

| 파일 | 역할 |
|---|---|
| [sources.csv](sources.csv) | 출처·배포본·등록 ID·실재 여부·권리·인계 상태. 기존 registry 별칭을 따로 연결 |
| [models.csv](models.csv) | 원본/등록 ID × 모델 × 조건집합, 원문 기준용량과 과거 기록용량의 분리 |
| [eligibility.csv](eligibility.csv) | D, E_eta_v, E_eta_ov, E_f_loss, A, V의 독립 판정·이유·근거·미확인 |
| [field-dictionary.csv](field-dictionary.csv) | 원시 열·시트·단위·측정/저자 계산 구분·경계·SHA·근거 좌표 |
| [condition-dictionary.json](condition-dictionary.json) | DB/WB·전기 Hz/물리 rps·게이지/절대압·제어/전력 경계 정의 |
| [capacity-bands.json](capacity-bands.json) / [capacity-coverage.csv](capacity-coverage.csv) | 잠정 구간 명세와 제조사·제품군·모델명 수 및 공백 |
| [extraction-trial.json](extraction-trial.json) | LG 시험 추출의 불일치와 보류 상태. 실행 가능한 최종 tidy 명세가 아님 |
| [run-provenance.json](run-provenance.json) | 계획서·기준 커밋·소비한 기존 목록의 해시·NAS 상태 |
| [reviews/](reviews/) | 원문 검토 결과·해석 정정·검토 범위. 전체 원문/수치표는 Git 밖에 보존 |
| [validate.py](validate.py) | 목록 연결·6개 목적·구간 조건 및 선택적으로 실제 인계 파일 SHA 검증 |

목록 검사: `python3 validation/registry/catalog-archive-20261027/validate.py`

원자료 검사에는 `--payload <인계물/payload> --catalog-checkout <dataset-catalog 체크아웃>`을 함께 지정한다. 로컬 인계물 기본 위치는 `/home/habin/tmhp-archive-handoff-20261027/`이다.

## 원문 대조에서 확인한 중요한 경계

- Cuevas: 냉매유량은 증발기 열수지 계산량이다. Coriolis 비교는 전체 60시험 중 19시험이며 공개 표 전체가 독립 유량계 자료는 아니다. 직결과 인버터 시험의 전력 식별 경계를 구분한다.
- Guth: 직접 계측 `m_sf`는 이차유체 유량이고 `m_r`는 저자 열수지 계산량이다. 부록은 345행·145조건 문자열이며 논문이 말하는 156조건과 동일하다고 단정하지 않는다. 5시트의 측정·물성·효율·대상량·회귀계수를 구분한다.
- Daikin: 난방 WB를 DB처럼 대입한 기존 적용 이슈와 냉방 total/sensible 비교 경계를 분리했다. 원본 부재 상태에서 적합 판정을 올리지 않았다.
- Samsung: Flash Injection 구조와 Peak/Integrated 표를 구별한다. 기존 외기 +2°C/LWT40°C anchor는 Integrated 표에 대응하며, 제상 제외 정상상태 직접 비교를 보증하지 않는다. Integrated 표 제목과 반복된 Peak 각주의 불일치도 보존한다.
- Panasonic R290: Injection 2-Way Valve가 있는 회로다. 특정 시험점의 활성 여부는 미확인이다. 풍량 사양의 Heating/High를 최대풍량으로 재명명하지 않는다.
- Mitsubishi SCI: 기존 등록부의 fixed-speed-only 설명과 달리 60 rps 가변속 성능표가 있다. 7모델은 선택칸 검토 후보이며 정본 tidy의 전 칸 검증 상태는 아니다.
- Highly THU33WC6-U: 같은 모델명이라도 220–240V와 380V 정격행을 별도 조건으로 유지한다. 8.30/8.15 kW의 기존 기록을 하나로 합치지 않았다.
- BITZER: 기존 연구 CSV는 제조사 계산 출력의 추출본이며 원 API 응답이 없다. 독립 실측 원본으로 새 등록하지 않는다. 전력 경계를 다른 압축기 형식까지 일반화하지 않는다.

## 용량구간과 수집 공백

잠정 압축기 구간은 ASRE-T·60 rps·정격냉방 7모델에만 `[0,3)`, `[3,6)`, `[6,10)`, `[10,∞)` kW를 적용했다. 각각 모델명 수는 1·4·2·0이다. Mitsubishi 한 제조사·R410A inverter twin rotary에 치우쳐 있다.

히트펌프 구간은 A7DB/6WB, water in30/out35°C의 정격난방을 확인한 Panasonic 9모델에만 `[0,5)`, `[5,10)`, `[10,15)`, `[15,∞)` kW를 적용했다. 각각 0·3·3·3이며 MXC-J/UQ-H/WXG-M 세 제품군을 구별한다. 실제 물리 회전수는 미공개이고 제조사 제어조건으로 남긴다. 산업 표준·물리 변곡점이라는 주장은 하지 않는다. 삼성 14 kW는 정격조건 미확인으로 구간을 주지 않았다.

독립 설계 수·독립 실측 운전점 수는 확인되지 않아 비워 두었다. 장치별 시리즈·chassis 계보를 추가 확인하고 R32/R290·다른 제조사·빈 용량구간 및 독립 유량/전력 경계가 있는 가변속 E 자료를 보완해야 한다. Ossorio 전문·mmc1–3 및 Shao 원본은 미확보다. ScienceDirect는 403, Ossorio 기관 저장소는 JS challenge로 전문 재확인이 제한됐고 현재 Chrome CDP 세션도 연결되지 않았다.

## 검증과 남은 작업

실제 인계 37묶음·43파일의 bytes/SHA/tree/file-set 검사와 dataset-catalog 원도구의 `verify`가 통과했다. 카탈로그 render는 **로컬 신규 인계본 + NAS 기존 자료 읽기 전용 조회층**에서 124개 기록의 원자료 참조를 확인했다. 이는 새 원본이 NAS에 반영됐다는 뜻이 아니다. metadata audit, QA, harness 검사를 수행했고 37개 새 상세 페이지의 1440/390px 총74보기에서 HTTP 오류·가로 넘침은 없었다. 전체 수치표 검증을 대신하지 않는다.

LG 시험 11표는 1,423칸 중 670칸의 두 추출기 차이가 미해소라 정본 등록을 보류했다. 기존 Annex81 insights 1.09MB 경고와 PDF extraction 규칙의 기존 무매칭 glob 경고는 별도로 남겼다. 검토 후 원문·음영·조건 매핑을 고치고 정본 추출표와 warehouse를 등록해야 한다.

현재 `/mnt/datasets` 새 폴더 생성은 `Read-only file system`, NAS SSH는 `Permission denied (publickey)`로 실패한다. 쓰기 가능한 큐레이터 환경에서 원본 복사·해시 재확인 → NAS를 실제 payload로 한 render/verify → 수치표·warehouse 보완 → PR 검토 순으로 이어야 한다. `build`·머지·게시를 이번 작업에서 실행하지 않았다.
