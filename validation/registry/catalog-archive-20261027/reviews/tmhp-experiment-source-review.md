# E 실험자료 읽기 전용 대조 (2026-10-27)

지정 registry 경로의 Cuevas/Ossorio/Guth/Shao 원본은 모두 부재다. 대체 로컬 Cuevas PDF와 Guth webprint+supplement를 확인했고 NAS 기존 두 실험 자료는 SHA·bytes가 manifest와 일치한다. 새 다운로드·NAS쓰기·효율계산은 하지 않았다.

|출처|실제 확보|핵심 판정|
|---|---|---|
|Cuevas2009|대체 PDF304221bytes, registry304548bytes와 다름|유량은 증발기 열수지 계산량. Coriolis비교는60시험 중19시험만. Table2 18+Table3 30; physical shaft N 실측 아님. E 세목 모두 경계 조건부.|
|Guth2023|webprint7328122bytes+XLSX286867bytes|measured values 유량은 secondaryfluid; target values 냉매유량은 저자 계산량.345행145조건, 논문156조건과 분리. 독립Vdisp 미확인. ηv보류, ηov/floss조건부.|
|Ossorio2023|지정 원문·mmc1–3 미보유|공식 제목은 evaluation of compact energy consumption models. ScienceDirect403, 기관PDF JS challenge. 추상2전력계 정보만으로 원자료확보 주장 불가.|
|Shao2004|지정PDF·대체원본 미발견|registry의 제조사계수 설명은 이번 전문 재확인 못함. 직접실측 모집단 채택 보류.|
|Vigo2024 R410A|NAS XLSX46133bytes, SHA일치|B0W35 96+B0W45 79=175행. 공식설명75 차이 유지. 독립F01 근거는 기존2026-10-18 전문검토; 이번 본문 접근 실패. Ncomp/drive분기/전력경계 조건부.|
|R407C liquid-water v1|NAS XLSX24637bytes, SHA일치|64행, 냉매유량 독립채널 없음. LVHEx미사용+NoDHW는36/68행이나DS압력강하 잔존. 기존ID재사용.|

모든 출처의 정격용량은 이번 확인에서 미확인이므로 용량구간도 NULL이다. 실제 운전점 최고출력이나 모델명의 숫자를 정격용량으로 대체하지 않는다.

로컬파일 해시·자세한 변수/조건키·효율별 판정·web접근 결과는 JSON에 저장했다. 신규 추출표의 셀 전수대조나 NAS등록/원자료검증render는 수행하지 않았다.
