# 2026-11-02 카탈로그 및 인용 근거 보강

## 카탈로그 후보 확정 근거

Fujitsu **ASYG30LMTA / AOYG30LMTA** 단일 조합, R410A 인버터 히트펌프를 선정했다.
제조사 작성 *Design & Technical Manual*, DR_AS010EF_04, 2016-04-05의 PDF 21쪽
(인쇄 17쪽), `6-2. Heating capacity` 첫 번째 표다. 36형의 데이터를 합치지 않았다.

- 원본 로컬 PDF: `/home/habin/papers/temp/compressor_factor/lambda_and_catalogues_20261019/PDF/Fujitsu_ASYG30LMTA_full.pdf`
- [원본 배포 PDF](https://www.viacomvs.com/wf-doc/fujitsu-klima-uredjaj-zidni-inverter-asyg30lmta-aoyg30lmta-technical-manual.pdf)
- SHA256: `39abcd854c3e44fe8f544adce5efce8102f32516ec6c56583f4f35cf5ad06a35`
- 제조사 공식 사양 재확인: [실내기 사양](https://webstore.uk.generalww.com/product/attachment/ASYG36LMTA/specifications.pdf), [실외기 사양](https://webstore.uk.generalww.com/product/attachment/AOYG30LMTA/specifications.pdf), [공식 product fiche](https://www.generalww.com/datafiles/00345345-02.pdf).
  공식 사양 PDF는 두 모델을 함께 포함한다. 실내기 URL의 36 모델명만으로 해당 열을 고르지 말고 표의 30형 열을 사용한다.
- 전체 기술매뉴얼은 제조사 작성본의 외부 배포 사본이다. 공식 사이트 전체 매뉴얼 URL의 직접 다운로드는 이번에 확인하지 못했다.

### 데이터의 성격과 수

원문은 **최대 능력에 기반하여 계산한 카탈로그 표**라고 명시한다. 독립 실험 50회 또는
부분부하 실험으로 표현하면 안 된다. 실내 온도 16/18/20/22/24°C와 실외 DB
−15/−10/−5/0/5/7/10/15/20/24°C의 50개 조합에 각각 TC[kW], IP[kW]가 있다.
실외 DB와 WB가 별도 열로 직접 주어져 WB→DB 추정이 필요 없다.
`fujitsu_asyg30lmta_heating_50.csv`는 원문 PDF를 `pdftotext -layout`으로 재추출했고,
기존 후보 CSV 50점의 온도·능력·전력 수치와 전부 일치했다.

최종 분할은 COP fitting 전에 다음과 같이 고정했다. 최초 ≥7°C 후보 25점의 수정된
baseline(냉방 정격 8000W, 압력비 상한 7.5)에서 24점이 유효했고,
실내22°C/실외24°C 한 점은 최적화 실패였다. 성공점만 골라 제거하지 않고 실외24°C
온도 그룹 5점 전체를 별도 solver-edge 진단으로 분리했다.

| 구분 | 실외 DB | 실내 DB | 점수 |
|---|---|---|---:|
| 학습 | 7, 10, 15, 20°C | 20°C | 4 |
| 미학습 실내 조건 평가 | 동일 | 16, 18, 22, 24°C | 16 |
| solver-edge 별도 진단 | 24°C | 모두 | 5 |
| 저온 별도 진단 | −15, −10, −5, 0, 5°C | 모두 | 25 |

실제 study 20점은 `../catalogue_points.csv`, 선정 기록은
`../fujitsu_selection_manifest.json`이 권위 파일이다. 이 디렉터리의 50점 원문 CSV에
있는 `proposed_split`은 최초 25점 후보 제안을 기록한 것이며 최종 학습 분할이 아니다.
원문 50점 전체는 보존했다. `../diagnostic_solver_edge.json`에 별도 기록된 24°C 그룹은
보정 전 4/5 유효·1실패, 보정 후 5/5 유효다. 보정 전 실패를 보존하므로 그 그룹의
동일 5점 기준 보정 전 RMSE는 null이며 개선율을 만들지 않는다. 이 그룹을 보정 후
성공했다고 최종 study에 되넣지 않았다.

이 분할은 원문 공개값을 보간하여 점수를 늘린 것이 아니다. 7°C 외기가 무조건 무착상임을
보장하지 않는다. TMHP가 제상을 모델링하지 않으므로 7°C의 증발기 온도/물리 상태를
진단하고, ≥10°C의 20점 소집합 결과도 구분할 수 있게 원본 조건을 보존했다.
모델 실패점은 같은 분모에 보존하고 임의 제거 후 RMSE만 제시하지 않는다.

### 확인된 입력과 미확인 입력

| 입력 | 원문 근거 | 적용 상태 |
|---|---|---|
| 냉매 R410A | PDF54, 실외 사양 | 확인 |
| 정격 냉방 8.00kW / 난방 8.80kW | PDF15 | 확인; 표의 최대 11.00kW와 구분 |
| 실내 HIGH 풍량 1380m³/h | PDF15, PDF21 AFR | 확인 |
| 실외 풍량 3600m³/h | PDF54, PDF63 | 확인; 각 운전점 실제 변동 미공개 |
| 실내 HIGH 팬 입력 60W | PDF15 `Input power / Fan / HIGH` | 확인 |
| 실외 팬 `Motor output` 100W | PDF54 | 출력 정격만 확인; 전기 입력으로 사용 금지 |
| 배제체적 Vdisp, 상세 압축기 모델, 속도 제한 | 선택 문서에 없음 | 모델 추정값이면 명시하고 fitting 중 고정 |
| UA 및 SH/SC | 직접 공개되지 않음 | 모델 가정값이면 명시하고 fitting 중 고정 |
| 냉동유 800cm³ | PDF54 `Refrigerant oil / Amount` | Vdisp와 무관 |

IP는 페어 시스템 표의 입력전력으로 취급한다. 원문에 모든 표 운전점에서의 팬/보조전력
분해나 TC의 팬 열 상세 경계가 별도로 명시되어 있지는 않다. 따라서 선택한 net heating
경계와 팬 모델을 **모델 가정**으로 기록해야 한다. 정격 IP 2.41kW와 최대 표의 3.30kW를
혼동하지 않는다. fitting 결과는 이 고정된 열교환기·팬·변위 가정에 조건부이다.

기존 Daikin RZQ100B8W1B/FBQ100B8V3B 36개 난방점은 외기 WB축, RH85%, 제상
능력 감소 포함이라는 추가 경계를 가진다. 최신 RXM35A 기존 검증 YAML도 WB를 그대로
입력하는 근사라 이번 DB 명시 후보보다 적합성이 낮다. 이전 Panasonic 9점은 별도 보존한다.

## Ref. [6] 직접 접근

2024 논문의 참고문헌 [6]은 Ossorio and Navarro-Peris (2023),
*Testing of Variable-Speed Scroll Compressors and their inverters for the evaluation of compact energy consumption models*,
Applied Thermal Engineering 230, 120725다.
[DOI](https://doi.org/10.1016/j.applthermaleng.2023.120725),
[출판사 페이지](https://www.sciencedirect.com/science/article/pii/S1359431123007548),
[UPV 공개 전문 PDF 직접 링크](https://riunet.upv.es/server/api/core/bitstreams/9bede58e-9ad8-470e-a499-72d2b562b4d0/content).

2026-11-02 위 UPV API 링크를 인증 없이 실제 다운로드했다. HTTP200,
`application/pdf;charset=UTF-8`, 3,030,840bytes. PDF 첫 페이지의 제목·저자·DOI와
EN13771/double-wattmeter 본문을 확인했다. 다운로드 파일:
`/home/habin/papers/temp/261102 presentation/Ossorio_2023_ATE230_120725_UPV.pdf`.
SHA256 `4c8bfc8c55547fff3db978e7880b8edd213bfaddfba6ae035195213d3a25621f`.
증거 메타데이터는 `ref6_direct_access.json`이다. 기존 `/download` 링크는 웹 도구에서
anti-bot HTML을 반환했으므로 발표에서는 위 직접 PDF 링크를 쓰는 편이 명확하다.
새 다운로드의 해시는 기존 crop에 사용한 로컬 PDF 해시와 다르며, 기존 crop provenance는
그대로 유지한다. 원본 전체 PDF는 git에 추가하지 않는다.

## Table 5와 효율/손실 표기 설명

2024 논문의 정확한 문헌은 *Development of compact empirical models for variable-speed
compressors for the prediction of energy consumption, mass flow and discharge temperature*,
Applied Thermal Engineering 244, 122666,
[DOI:10.1016/j.applthermaleng.2024.122666](https://doi.org/10.1016/j.applthermaleng.2024.122666).

원문 PDF14쪽 Table5 전체를 `crops/2024_original_table5_r290.png`로 렌더링하고
**R290 열**을 반투명 PDF annotation으로 강조했다. 글자를 다시 그리거나 계수 표기를
고치지 않았다. 작은 배치용 `2024_table5_r290_excerpt.png`는 행 이름과 R290 열만
원본에서 잘라낸 발췌다. 전체 캡션은 전체 표 이미지에 보존했다.
좌표·원본 해시·강조 범위는 `crops/catalogue_table5_manifest.json` 참조.

원문 표의 네 계수는 0.103, −0.00390, 0.008, −14.8이다. 압축기와 인버터에 넣은 전력
중 냉매 엔탈피 상승으로 남은 비율을 `η_em`, 외부로 잃은 비율을 `f_loss`로 잡으면
에너지 수지에서 `η_em = 1 − f_loss`가 된다.
2024 Eq22 인쇄본의 우변을 그대로 효율로 읽으면 약0.1이지만, 해당 계수와 Appendix의
유도·동일 저자의 학위논문 Eq3.22는 그 우변이 **손실률**이라는 해석을 지지한다.
따라서 모델은 `f_loss = k0+k1 PR+k2 PR/(N/70)+k3/Pel`, `η_em=1−f_loss`를 사용한다.
Pel은 여기에서 W 단위이며, N은 원문 회전수 기준이다.
이는 공식 정오표 확인이 아니라 학위논문 및 공개134점과 교차 검증한 해석이다.
134점에 대한 η_em RMSE 0.0116, 인쇄식을 직접 효율로 읽은 RMSE 0.7536.
2024의133점과 학위논문134점 차이는 해결되지 않았고, 학습 원자료 재현검증이지
독립 실험 검증이 아니다. 상세 계산은 기존 `audit.md`와 `audit_results.json`에 있다.
