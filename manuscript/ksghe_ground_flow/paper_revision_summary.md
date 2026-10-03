# GSHP auxiliary-pressure / fan revision

정유량은 기준과 별개의 실제 명령24 L/min이며, min/max9.6–36 L/min 안에서 모든 성공점에 동일하게 적용된다. 두 병렬 보어홀의 branch 유량은 각각12 L/min이다. BHE-only 손실16.973 kPa에 reference auxiliary121.027 kPa×유량비²를 추가하여 기준 총손실138 kPa, eta=.60, 정유량 펌프92 W를 얻는다. 이전11.315 W는 정유량이 작아서가 아니라 공통/HX 손실을 빠뜨린 결과였다. 보조손실 선정은 ASHRAE Table8 경계 시나리오이며 실측 보정값이 아니다.

팬 Method2 식은 유지한다. generic 풍량 적용범위를 기준1.6 m³/s의15–100%, 즉 .24–1.6 m³/s로 제한하며 하한·상한 flags를 기록한다. UA scaling과 reference/max는 분리한다. 제조사/사용자 custom 곡선은 명시한 별도 범위를 사용할 수 있다. Addendum u의16%는 multizone VAV 전력 상한이며 임의 electrical floor를 추가하지 않았다.

기존 물리 조건을 유지하고 PLR .3을 운전 불가로 명시하기로 사용자가 결정했다. PR≥1.5, 두 HX 열수지 및 새 팬 하한을 동시에 만족하는 정상 해가 없어 요청16점 중14점만 비교한다. 실패점의0 W/NaN COP는 운전 성능으로 사용하지 않는다. cycling과 제약 완화는 추가하지 않았다.

기본 결과: 최적 유량9.60–21.81 L/min, 펌프6.11–69.28 W, 정유량 펌프92 W. 최대 총전력 절감20.04%, COP 증가25.06% (공통 feasible PLR .4–1). PLR .4는 물 유량 하한을 선택하고, 36 L/min 상한 선택점은 없다. 전부하 최적 팬 풍량은 상한1.6 m³/s에 도달한다. 상한에서의 최적점은 명시한 제약하의 해이다.

aux 민감도는 같은 팬/UA 조건에서0/.75/1/1.25배를 새로 계산한다. 최대 절감은 각각2.74/16.23/20.04/23.49%, COP 증가는2.82/19.37/25.06/30.71%다. zero-aux case는 최대31.86 L/min을 선택한다. 원래 논문 UA800/1600의 교환 배치도 활성 연구의1600/800으로 정정했으므로, 이전/새 원고 차이는 순수 펌프 효과로 해석하지 않는다.

검증: 전체 모델344 passed,3 skipped; Sphinx -W 문서 빌드 통과(임시 stage에는 git timestamp extension만 제외). 새 계산184+182+237+88+16점을 생성하고 열수지·COP·효율·질량유량·유량한계·독립 격자·동일24 L/min 물리 parity를 검증했다. 최종 Figure1은1×4이며 실패점은 제외한다. 개발 그림을 포함한7개 그림은 actual Dartwork-mpl MCP15회·렌더 검사·시각 검수를 통과했다.

Notion 해당 pump/fan 섹션에 식·적용범위·실패점과6개 원문 하이라이트를 연결했다. Chrome Bridge가 없어 Chromium/Poppler로 직접 원문을 확인·캡처했으며 지정 Bridge 방식은 미완료로 기록했다. 출처 노트는 references/에 있다.

원고는 원래 native template의 저자·소속·제목·연락처·사사를 유지한1페이지 HWPX/PDF다. Linux font substitution 렌더링을 사용한다. 정확한 값·한계·민감도는 validation_notes.md, 원본 대비 수치 비교는 results/model_revision_comparison.csv, 파일·코드 해시는 QA JSON을 참조한다. 이전 활성 연구50개 파일은 archive/pre_aux_fan_revision_20261002/에 SHA-256과 함께 보존한다. 수정은 커밋하지 않았으며 base commit과 실행 working-tree hash를 구분한다.
