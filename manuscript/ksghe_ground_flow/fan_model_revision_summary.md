# Fan model 원문 아카이브 및 설명 보완 — 2026-10-03

사용자가 열어둔 Windows Chrome 154.0.8037.92에 SSH 역방향 터널 127.0.0.1:19123과 CDP로 접속했다. 전용 Chrome Bridge MCP는 등록되어 있지 않으며, 이 아카이브는 같은 사용자 Chrome의 실제 원문 화면이다. PDF는 native viewer에서 텍스트를 직접 선택하고, Handbook은 실제 HTML 문단을 하이라이트했다. 기존 브라우저와 탭은 유지했다. 원문 3개에서 식·저부하·Addendum 서문·본문의 PNG 4개를 저장하고 시각 검수했다. sources.md 및 .capture.json에 URL·문서/쪽/절·방법·UTC 시각·해시를 기록했다.

핵심 해석: Handbook의 0.15는 저부하 외삽을 피하기 위한 예시다. Addendum u의 15% 풍량/16% 전력 설명은 정보성 서문이며, 개정 본문 6.5.3.2.1(b)은 이전 30% 전력 문장을 삭제하고 16% 문장을 추가하지 않는다. 본문의 최소 운전풍량 조건에는 설계 최소 외기량이 포함된다. 따라서 이를 모든 히트펌프 팬의 15% 하한 또는 16% 최소 전력으로 해석하지 않는다.

앞선 GSHP 작업에서 구현한 TMHP generic 범위 0.15–1.0은 모델링 선택으로 유지한다. ref는 고정 정규화 기준, min/max는 제어·장비 및 유효범위 한계이며 교집합이 비면 오류로 처리한다. 제조사/custom curve는 선언한 별도 범위를 사용한다. 실제 풍량은 UA와 fan power에 동일하게 적용하고, 열수지가 닫히지 않는 clamp는 운전 불가다. inactive/NaN 동작과 limit flags를 유지하며 임의 electrical floor를 추가하지 않는다.

다항식과 계수는 그대로다. x=0.10/0.15/0.50/1.00의 P*는 0.0254062/0.044401675/0.299975/0.9991이다. 기존 source note의 0.300475 오기를 0.299975로 정정했다. 시뮬레이션은 원래부터 올바른 식을 사용했으므로 계산 결과에는 영향이 없다.

이번 코드 변경은 hx_fan.py의 docstring·근거 경로뿐이며, docstring을 제외한 실행 AST가 동일하다. 원래 실행 파일을 provenance/에 보존하고 기존 시뮬레이션의 실행 해시는 덮어쓰지 않았다. results/source_documentation_changes.json으로 실행 시점과 이후 문서 보완을 구분하며 연구 검증기는 원본 해시·현재 해시·실행 AST 동등성을 함께 검사한다. 기존 HWPX/PDF·그림·CSV의 수치와 PLR 0.3 운전 불가 처리는 유지한다.

검증: 설명 보완본으로 tests/test_fan_part_load_bounds.py의 14개 테스트가 통과했다. Ruff 통과 및 기존 연구의 실행 지문·열수지·COP·유량/풍량 한계·실패점 제외·7개 그림 QA 재확인을 통과했다. 이전 전체 검증 344 passed, 3 skipped를 새 전체 테스트 실행으로 주장하지 않는다.

Notion Fan model 페이지에 4개 이미지·최종 source ledger·적용범위·서문/본문 구분·코드/테스트 상태를 반영했다. 예비 첨부는 유지한다. 원문 아카이브는 TMHP references/fan_model/ 및 활성 연구/원고의 references/fan_part_load/windows_chrome_20261003/에 있다.
