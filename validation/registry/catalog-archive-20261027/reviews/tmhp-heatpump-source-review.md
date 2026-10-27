# A/V 출처 대조 결과 (2026-10-27)

6개 제조사 PDF 실제 존재·SHA/bytes/쪽수 확인. NAS 기존 manifest 동일 SHA 없음. 취득일/정확한 다운로드주소는 미확인. 원본은 이동·변경하지 않았다.

| 원본 | 판 | bytes | PDF쪽수 |
|---|---|---:|---:|
| Panasonic_Aquarea_2025.pdf | 2025; 보유 PDF 128쪽, 인쇄쪽 22부터 시작; 전체 종합 카탈로그 포함 범위 미확인 | 17707407 | 128 |
| Panasonic_R290_WH-WXG_Service_Manual.pdf | PCZ2411004CE; ©2024; Europe | 30386662 | 321 |
| Samsung_EHS_Mono_HT_Quiet_R32.pdf | 보유본 명시 판번호 미확인; PDF modDate 2022-10-05; TMHP YAML의 2024 표기는 판 확인 아님 | 14734164 | 64 |
| Panasonic_R32_WH-MXC_Service_Manual.pdf | PAPAMY2110029CE; ©2021; Europe/Turkey | 14074788 | 221 |
| Panasonic_R32_WH-MXC16_Service_Manual.pdf | PAPAMY2112029CE; ©2021; Europe/Turkey | 13949837 | 209 |
| Panasonic_R410A_WH-UQ_Service_Manual.pdf | PAPAMY1704053CE; ©2017; Europe/Turkey | 21541269 | 248 |

중요 적용 이슈:
- 삼성 기존 저온 YAML anchor는 Peak보다 Integrated 표와 일치한다. Flash Injection 구조도 명시돼 비분사·무제상 정상상태 직접 검증을 보류한다. 원문 Integrated 표에도 Peak 각주가 반복되므로 제목/각주 충돌을 보존한다.
- Panasonic R290 WH-WXG 서비스매뉴얼 PDF277/278쪽에 Injection 2-Way Valve가 있다. 시험 운전점 활성 여부 확인 전 비분사로 분류하지 않는다.
- Daikin/Fujitsu 원본이 없다. Daikin 난방 WB를 DB 입력처럼 쓰는 기존 코드와 Fujitsu IP 경계 hold를 현황표에 유지한다.
- 정격/High 풍량을 최대풍량으로 바꾸지 않는다. Panasonic/Samsung은 공기-물 별도 층이다.
- hplib 기존 ID hplib-keymark-database-125c2ef6 재사용. 5파일 실제 SHA 일치, tar input/csv2634개; fitted database9953행은 독립 실측점수가 아니다. CSV는 CC BY4.0, software는 MIT이며 upstream 인증 PDF 권리는 별도다.

표 전체 추출·전 칸 교차검증은 미실시. 이 보고는 아카이브 등록 완료 또는 TMHP 성능 검증 완료가 아니다. 상세 source/model별 evidence와 판정은 JSON 참조.
