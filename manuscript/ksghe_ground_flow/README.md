# KSGHE GSHP cooling paper

`manuscript.json`은 편집 소스, `GSHP_variable_flow.{hwpx,pdf,png}`는 검증된 native 문서·1페이지 PDF·미리보기다. 저자·소속·제목·연락처·사사는 원래 `template_261001.hwpx`를 따른다.

현재 조건은 R410A8kW, 실내26°C, 고정 지중벽15°C,1×2×100m·간격6m, 지중/실내UA1600/800이다. 기준·정유량24 L/min, 변유량9.6–36 L/min, aux ref121.026847 kPa·지수2·펌프효율.60, 팬 기준/하한/상한1.6/.24/1.6 m³/s를 사용한다. generic 팬 곡선은15–100%에 한정하며 임의 power floor는 없다. 공유 압축기 baseline v2026-09-24와 variable UA/Rb*, ε–NTU를 유지한다.

PLR .3은 정상 해가 없어 CSV에 실패로 기록하고 그림·절감률에서 제외한다. 요청16점 중14점이 feasible이다. 최적 유량9.60–21.81 L/min, 정유량 펌프92 W, 최대 총전력 감소20.04%, COP 증가25.06%다. 제조사 실측 절감량이 아닌 미보정 정상 시나리오다.

`data/`와 `figure/`는 enex-engine 활성 연구의 새 계산을 복사한다. `validation_notes.md`에 압력 선정·유량 및 풍량 한계·실패점·민감도를, `paper_revision_summary.md`에 최종 변경을 기록했다. `references/`는 원문 URL과6개 하이라이트를 담는다. Chrome Bridge는 제공되지 않아 Chromium/Poppler fallback을 명시했다.

재생성:

```bash
python build_hwpx.py template_261001.hwpx GSHP_variable_flow.hwpx
UV_CACHE_DIR=/tmp/uv-cache uv run --offline --no-project --with 'pyhwpxlib[all]==0.18.3' --with cairosvg python render_pdf.py GSHP_variable_flow.hwpx GSHP_variable_flow
python check_paper.py GSHP_variable_flow.hwpx GSHP_variable_flow.pdf
```

`qa.json`은 새 CSV와 모든 본문·캡션·전력/COP·제약·해시·저자·1페이지·폰트 임베딩을 대조한다. `hwpx_validation.json`은 strict/compat 검증이다. 이전 문서 대비 native text 및 시각 diff는 `diff/aux_fan_revision_20261003.*`에 있다. Linux renderer의 font substitution을 사용하며 Hancom Office 렌더링이라고 주장하지 않는다.
