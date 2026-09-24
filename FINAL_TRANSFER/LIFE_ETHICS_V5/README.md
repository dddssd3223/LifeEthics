# LIFE_ETHICS FINAL TRANSFER V5

2028 통합사회 모의고사 제작을 위한 생활과 윤리 legacy content source database.

- 문항 수: 317 = 기존 V4 KEEP 114 + V4 targeted recovery RESTORE 203
- RESTORE는 제공된 통합사회 자료 4개([별책7] 교육과정, 2028 수능 예시문항 안내, 28예시 실제 문항, 최소 성취수준 자료)를 문항마다 대조한 표적 false-negative recovery 결과이다.
- `reuse_scope`(RESTORE만): FULL 76 / PARTIAL 127 — 원천 콘텐츠 재사용 범위. PARTIAL은 불량 문항이라는 뜻이 아니라 일부 생윤 고유 세부가 포함되어 있다는 뜻이다.
- `asset_origin`: V4_KEEP | V4_TARGETED_RESTORE. `evidence`·`rationale`에 판정 근거를 그대로 보존.
- `official_answer`: Life Ethics에는 공식 정답 source가 없어 모두 null(추론·외부 수집 없음).
- `questions.jsonl`의 stem/passage/choices는 Phase 1 OCR 값 그대로이며 원문 Source of Truth는 `images/<question_id>.png`.
- `review_required`는 후속 제작 시 사람이 주의해서 볼 문항 표시(제외 사유 아님).
