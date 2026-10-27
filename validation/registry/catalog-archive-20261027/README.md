# TMHP 외부 입력자료 아카이브 인계 · 2026-10-27

계획서의 기존 자료 대조·원문 경계 검토와 **37묶음·43파일의 SMB NAS 반입·해시 검증을 완료했다.** 신규 웹 원본 취득·전체 수치표 추출·공유 warehouse 등록·메타데이터 PR 머지·사이트 게시는 미완료다. 메타데이터는 dataset-catalog 초안 PR로, 연구별 채택 판정은 이 목록으로 분리했다. 회귀·물성·효율·COP 평가와 TMHP 모델 변경은 수행하지 않았다.

## 범위와 실제 상태

- NAS 반입본: 37개 출처·배포본 묶음, 43파일, 450,867,643 bytes. 기존 `receipt-nas` SMB로 새 dataset의 `files/`에만 반입했다. 임시 인계 경로와 최종 경로에서 SMB 재다운로드 SHA-256을 각각 대조하고 NAS 읽기 파일 집합·bytes·tree hash를 확인했다. 기존 NAS 원자료·공유 체크아웃은 바꾸지 않았다. 브라우저 PDF 출력 6개는 원문 HTML 6개와 함께 보존하며 `extracted-copy`로 구별했다.
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
| [validate.py](validate.py) | 목록 연결·6개 목적·구간 조건 및 선택적으로 실제 payload SHA 검증 |
| [smb-transfer.json](smb-transfer.json) / [transfer-log.jsonl](transfer-log.jsonl) | NAS 반입·SMB 재다운로드·정본 매니페스트 대조 및 단계 전환 기록 |

목록 검사: `python3 validation/registry/catalog-archive-20261027/validate.py`

실제 NAS 원자료 검사에는 `--payload /mnt/datasets --catalog-checkout <dataset-catalog 체크아웃>`을 함께 지정한다. NFS는 읽기에만 사용하며 쓰기는 기존 SMB 연결로 수행했다. 로컬 인계물 기본 위치는 `/home/habin/tmhp-archive-handoff-20261027/`이다.

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

실제 NAS 37묶음·43파일의 SHA-256·bytes·파일 집합·tree 검증을 완료했다. `_incoming`과 최종 경로에서 각각 SMB로 43파일을 재다운로드해 원본과 대조했다. 클라이언트에서 원도구 `init_manifest.inventory`를 실제 NAS 파일에 적용한 37개 결과도 PR 매니페스트와 일치했다. 카탈로그 `render . --payload /mnt/datasets`는 **실제 NAS**에서 124개 기록의 원자료 참조를 확인했다. 원래 로컬 인계본의 원도구 `verify` 통과 이력은 보존한다.

공유 NAS에는 신규 `files/`만 추가했고 미머지 메타데이터를 쓰지 않았다. 따라서 공유 루트에서 신규 ID를 대상으로 하는 `dataset_pool.py verify`는 PR 메타데이터가 정식 반영된 뒤 실행한다. 이 제약과 별개로 새 원본 43파일의 NAS 해시·파일 집합·tree 검사는 완료됐다. 기존 37개 상세 페이지의 1440/390px 총74보기 검토 이력은 수치표 전 칸 검증을 대신하지 않는다.

LG 시험 11표는 1,423칸 중 670칸의 두 추출기 차이가 미해소라 정본 등록을 보류했다. 기존 Annex81 insights 1.09MB 경고와 PDF extraction 규칙의 기존 무매칭 glob 경고는 별도로 남겼다. 검토 후 원문·음영·조건 매핑을 고치고 정본 추출표와 warehouse를 등록해야 한다.

초기 NFS 쓰기·SSH 인증 실패는 해당 접속 방식의 실패였으며, NAS 파일 작업 전체의 차단으로 판단한 것은 잘못이었다. 2026-10-27 사용자가 알려준 기존 `receipt-nas` SMB 연결로 파일 반입·검증과 이 작업의 임시 인계본 정리를 완료했다. 새 SSH 키 등록이나 사용자 수동 업로드는 필요하지 않았다. 남은 작업은 누락 원문·LG 추출 불일치·권리와 조건 검토·수치표·공유 warehouse 보완, 메타데이터 PR 검토·머지 및 승인 후 게시다. NAS에서 명령을 실행하는 직접 웹 취득·게시 등의 경로를 선택하면 별도 실행 환경이 필요하지만 이번 SMB 파일 작업에는 NAS 명령을 실행하지 않았다.
