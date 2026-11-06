# 압축기 제조사 원문 대조·시험 추출 — 2026-10-27

범위: 읽기 전용. 새 원문 다운로드·NAS 쓰기·원본 이동/삭제·회귀·물성·효율 계산을 수행하지 않았다. 검색 루트는 `/home/habin/papers`이며, 검색 범위 밖까지 파일 부재를 일반화하지 않는다.

## 기존 등록부와 실제 파일

- `validation/registry/sources.yaml`의 Copeland OPI/응용문서, GMCC 2024, Highly 2024/발표본, Mitsubishi KVB/2023판은 원래 evidence 위치에 없다. `/home/habin/papers`의 PDF 파일을 bytes로 좁혀 실제 SHA-256 대조했지만 같은 파일은 찾지 못했다. OPI 디렉터리 원응답도 발견하지 못했다.
- LG 등록부 원문은 `validation/evidence/default_inputs/LG-ROTARY-2024.pdf`에 있다. 8,526,339 bytes, 20 PDF쪽, SHA `c65bf81f6180d36ea2c2df103b223d989d9af86d27cae18593462b86881a5d0a`. PDF p1 표기는 **Feb, 2024**. p5에는 141=14.1 cc/rev의 제조사 명명법, p6–10에 정격표, p10에 평가조건표가 있다.
- Mitsubishi 등록부의 10,495,890 bytes, 28쪽 원문은 `temp/compressor_factor/catalogue_replacement_20261019/rotary_catalogue.pdf`와 `temp/compressor_factor/lambda_and_catalogues_20261019/PDF/Mitsubishi_SCI_rotary_catalogue.pdf` 두 위치에서 SHA `85e2c806fac5bd1b3310ebc87c5e47125d19b2e085eddf9da44fd5adc7d20992`가 일치했다. 같은 판 사본 두 개이므로 하나의 배포본으로 취급한다. 문서에 인쇄된 판은 미확인이다. PDF 생성 메타데이터의 2016년을 발행연도로 대체하지 않는다.
- NAS 모든 도메인의 `files.jsonl`과 위 대상 SHA들을 대조한 결과 같은 SHA 등록은 0개였다. 파일 제목 유사성만으로 재사용 ID를 만들지 않았다.

## Mitsubishi: 이전 설명의 실제 오류와 D 후보

등록부의 “fixed-speed rated points and displacements only”는 불완전하다. PDF p12는 **Specifications of Inverter Twin Rotary Compressor**, **Performance at 60 rps**를 명시한다. PDF p4의 ASRE-T/ARI 평가조건, p5의 명명법, p12의 모델별 성능과 속도범위, p13의 허용범위를 각각 연결해야 한다.

p12의 R-410 / a) DC Inverter (ASHRAE-T Condition)에는 7개 모델이 있다. 용량은 제조사가 선언한 압축기 냉동능력이다. 같은 모델의 b) Heating Condition 재등장 4행은 새 기기가 아니다. p12의 R32 2개 유효 모델은 별도 층이며, 해당 소제목에 평가조건 표기가 없어 R410A의 ASHRAE-T를 전파하지 않는다. SVB140F--MT는 Under Developing으로 수치 후보에서 제외한다.

| 모델 | 용량 원문 [W] | Input 원문 [W] | 정격 속도 [rps] | 제조사 명명법 배제체적 [cm³/rev] |
|---|---:|---:|---:|---:|
| SNB092FQAMT | 2,510 | 820 | 60 | 9.2 |
| SNB110FGYMT | 3,400 | 1,035 | 60 | 11.0 |
| SNB130FGBMT | 3,630 | 1,140 | 60 | 13.0 |
| SNB140FRUMT | 3,880 | 1,230 | 60 | 14.0 |
| SNB172FEKMT | 4,830 | 1,560 | 60 | 17.2 |
| TNB220FLHMT | 7,130 | 2,200 | 60 | 22.0 |
| TNB306FPGMT | 9,880 | 3,010 | 60 | 30.6 |

- 기하값은 추정·효율 역산이 아니다. PDF p5가 S/T series 숫자토큰 ×0.1 cc/rev를 명시한다. 원래 숫자토큰·계수·규칙 페이지를 후보 JSON에 남겼다.
- p4 ASRE-T의 인쇄 조건은 증발7.2°C/응축54.4°C/return gas35°C/liquid46°C/ambient35°C다. liquid46°C를 다른 회사의46.1°C로 바꾸지 않았다.
- 모델·용량·전력·60rps·명명법 선택 칸을 PyMuPDF와 Poppler 텍스트 및 p5/p12 페이지 렌더로 대조했다. 전체 p12 표의 모든 칸을 대조한 것이 아니다. 최종 tidy의 `cross-checked`/`fully-checked` 판정으로 사용하지 않는다. 이 산출물은 **D 후보**이며 정본 추출표가 아니다.
- 전력 경계는 단순 `Input` 표기만 있어 미확인. 유량·흡입/토출 상태의 독립 관측이 없어 E 직접 식별 자료로 판정하지 않는다.
- 실제 취득 URL·원취득일·문서판·라이선스 미확인으로 D는 우선 conditional. 공식 출처/판 대조 뒤 직접후보 승격을 검토한다.

## LG 시험 추출 결과와 보류

저장소의 `pdf_tables.py --cross-check`를 실제 원문 p5,p6,p10에 실행했다. 표11개가 검출됐지만 **670/1423칸이 두 추출기에서 달랐다**. 복합 가로페이지·병합셀·머리글 대응이 포함된 차이며, 모든 차이의 원문 대조·해소는 수행하지 않았다. 따라서 정본 tidy로 게시하지 않는다. 결과 CSV/JSON/PNG는 `/tmp/tmhp-lg-tables/`에 있다. p5는 table finder 검출표가 없으므로 명명법 별도 명세가 필요하다.

기존 TMHP 관측 CSV의 LG40개 선택행을 제조사 전체 모델 수라고 부르지 않는다. p6의 모델코드 텍스트 단순 집계는71개, p7은41개, p8은16개, p9는16개, p10은15개지만 이는 제품표 중복/명명 패턴 검증 전 **검색 후보 수**다.

- 고정속 공급50/60Hz를 물리 축rps로 대입하지 않는다.
- 변속군의60Hz는 다른 시점 LG 제품페이지 보완근거다. 2024 각 모델행에 직접 명시됐다고 쓰지 않는다.
- DAT134MC(p6)는 원문3941W와14535Btu/h가 서로 충돌한다. 양쪽 원값 보존·보류. GPT442MA(p6 vs p10)는13921W vs13773W로 시장별 표가 다르므로 평균/보정하지 않는다.
- 독립 유량·흡입/토출 상태 데이터가 없어 E는 not-applicable. D는 조건·기하 명명법·속도 근거 제한을 둔 conditional.

## Highly 웹표와 원본 구분

현재 `default_inputs`에는 2015 India 2쪽 PDF와 D/G/H/L/TH 5개 웹표의 HTML·printToPDF가 있다. 기존 registry의 Highly2024와 다른 출처·판이다. 두 PDF 추출기 모두2015 India PDF의 텍스트층이0이었다. OCR/독립이중입력을 수행하지 않았으므로 수치 전사 검증을 새로 완료했다고 할 수 없다.

2026-10-27 공식 D-series 페이지를 열어 정속R32 50Hz GSD092TV와 R410A인버터 표가 나뉨을 확인했다. `Copyright 2014 ... All Rights Reserved`를 확인했으며 재배포허용은 미확인이다. 5개 기존 웹표에 대해 조건AS HRAE/T를2015 PDF에서 연결한 기존 프로젝트 결정을 원문 동일판 확인으로 표현하지 않는다. HTML과 printToPDF를 함께 보존하며, printToPDF를 제조사 배포 원본PDF로 분류하지 않는다.

기존 선택 관측: H24모델, L12, G3, D1, TH2행/1모델. 시리즈 간 같은 모델 중복도 있어 합계42개를 독립 설계 수로 사용하지 않는다. 이는 프로젝트 선택행 수이며 전체 웹표 모델 수가 아니다.

## BITZER 분류 경계

기존 `bitzer_raw_points.csv`는4275행/15모델, R134a1350행·R410A1125행·R32900행·R454B900행이다. 제조사 계산격자이며 독립실측4275점이 아니다. CSV는 TMHP 코드가 원API 결과를 옮긴 파일이고, 원API응답 묶음은 이번 검색에서 찾지 못했다. 외부 원본 릴리스라고 등록하지 않는다.

- displacement_m3_rev는 체적유량과 명목rpm에서 환산한 값이 포함된다. 기하 직접값과 구분한다.
- requested/returned frequency가 다른 행이 있어 요청값으로 실제속도를 확정하지 않는다.
- warning674행에는 인버터입구 전력 경계가 명시된다. scroll 설명의 모터단자 전력 경계를 왕복동·스크루 전체로 전파하지 않는다.
- 토출온도는 제조사계산값이다. f_loss 독립식별에는 not-applicable. ηv/ηov는 제조사 맵 재현을 위한 conditional로만 남기며 독립실측학습 모집단과 분리한다.
- 102행/73모델의 compressor_inventory.csv는 탐색목록이다. 이를4275행에서 실제 성능이 확보된 모델 수15와 구분한다.

## 공식 출처 검색 현황

- [GMCC 공식 다운로드](https://www.gmcc-welling.com/en/download-list): 현 접속 확인. 직접 파일 URL·판·크기·다운로드 조건 미확인.
- [Copeland OPI](https://webapps.copeland.com/online-product-information/): 현재 접속 확인, 원모델응답 취득 안 함. 저작권 유보 표기 확인.
- [Copeland AE4-1414 공식 CDN](https://media.copeland.com/ece94407-1caa-45bd-adde-b16b00f84ab5/AE%201414%20ZPV066%20and%20ZPV096%20Copeland%20Scroll%20Variable%20Speed%20Compressors.pdf): 검색색인 발견만. 파일확보·hash검증과 구별.
- [SCI 공식 기술 도구](https://technic.siamcompressor.com/): 검색색인상 성능계산/개별사양 로그인 제한. 접근우회 안 함.
- [SCI 공식 R290 leaflet](https://www.siamcompressor.com/siamcompressor/assets/file/SCI%20compressor%20leaflet.pdf): 공식검색색인 발견만. 기존28쪽원문과 다른릴리스이며 취득하지 않음.
- [BITZER EST-420](https://www.bitzer.de/shared_media/html/est-420/en-GB/283737995283787787.html): 현판 EST-420-8 //06.2026 확인. 모터명목속도/인버터손실 구별설명 확인.

`mitsubishicompressors.com`의 현재 footer는 Sahara와Uranus/Sahara 유통사다. 기존 registry의 해당 obtain_from을 제조사 공식 배포 URL로 잘못 승격하지 않는다. 실제 공식 출발점은 `siamcompressor.com`/`technic.siamcompressor.com`이다.

## 인계 파일

- `/tmp/tmhp-compressor-source-review.json`: 등록부12항목 실물/중복대조, 판정, 미확인, BITZER 경계, 웹조회수준.
- `/tmp/tmhp-mitsubishi-D-candidates.json`: p12 ASHRAE-T7모델의조건·60rps·명명법·근거.
- `/tmp/tmhp-lg-tables/`: 시험추출11표. **불일치미해소**.
- `/tmp/tmhp-mitsubishi-p5.png`, `/tmp/tmhp-mitsubishi-p12.png`: 원문대조용렌더.
