# 2028 통합사회 문항 DB 구축 — Phase 1: 원천 기출문항 DB (생활과 윤리)

「생윤기출모음(첫해~26-6까지)」(160쪽, GitHub 업로드 제한으로 20쪽씩 8개로 분할)을
**손실 없이** 문항 단위로 구조화한 원천 DB 와, 이를 재현·재개(resume)할 수 있는 파이프라인이다.

> Phase 1 은 선별하지 않는다. 통합사회 관련성·중요도 판단 없이 식별 가능한 모든 문항을 보존하고,
> 불확실한 것은 `review_required = true` 로 남긴다. 정답은 공식 정답표가 있을 때만 연결한다.

## 결과물 위치

| 경로 | 내용 |
|---|---|
| `data/raw/LIFE_ETHICS/*.pdf` | 원천 분할 PDF 8개 (Source of Truth, 저장소 루트에서 이동) |
| `data/questions/images/{id}.png` | 문항별 원문 이미지 (300dpi, 16단계 회색조 PNG) |
| `data/questions/json/{id}.json` | 문항별 구조화 JSON |
| `data/database/questions.db` | SQLite DB (JSON·이미지와 sha256 으로 상호 추적) |
| `data/reports/coverage_report.csv` | 시험별 예상/추출/정답연결/검토필요/오류 수 |
| `data/reports/review_required.csv` | 사람 검토가 필요한 문항과 사유 |
| `data/reports/errors.csv` | 오류·경고 (문항번호 누락, 시험 경계 이상 등) |
| `data/reports/duplicate_candidates.csv` | 중복 후보 (자동 삭제하지 않음) |
| `data/reports/summary.json` | 최종 실행 요약 |
| `data/processed/inventory_*.json` | 분할 PDF 목록, SHA-256, 원본 페이지 매핑 표 |
| `data/processed/extract_summary_*.json` | 시험 경계 탐지 결과(제목 OCR 투표 포함) |
| `data/reviews/reviews.jsonl` | 검수 UI 에서 남긴 사람 판정 (append-only) |

문항 ID 형식: `{학년도}_{JUNE|SEPTEMBER|CSAT}_{과목}_Q{번호2자리}` (예: `2024_SEPTEMBER_LIFE_ETHICS_Q15`).
충돌 시 덮어쓰지 않고 `__DUP2` 접미사로 별도 보존하며 `errors.csv`/검토 목록에 기록한다.

## Phase 1 실행 결과 (전체 batch, 2026-09-24)

| 항목 | 값 |
|---|---|
| 분할 PDF | 8개 (`생윤기출_01`~`_08`, 각 20쪽) → 원본 160쪽 순서 복원, 연속성 검사 통과 |
| 탐지 시험 | 40개 (2014~2025학년도 6월·9월·수능 36개 + 2026 6월·2026 수능·2027 6월·2027 9월) |
| 추출 문항 | **800 / 예상 800** (모든 시험 20문항, 번호 누락·중복 0) |
| 자동 정상 | 702 |
| review_required | 98 (`data/reports/review_required.csv`) |
| 정답 연결 | 0 (저장소·PDF 안에 공식 정답표 없음 → 전부 `null`) |
| 오류(ERROR) | 0 / 경고(WARNING) 4 (시험 순서·누락 슬롯) |
| 중복 후보 | 0 (파일 SHA-256·페이지/문항 phash·텍스트 유사도 모두 기준 미달) |
| 선택지 분리 | 796문항은 이미지의 원문자 ①~⑤ 검출로 5개 분리, 4문항은 OCR 표지 대체 |

### 사람이 확인해야 할 사항
1. **수록 범위** — 합본 이름은 “~26-6”인데 PDF 안에는 `2026학년도 9월`이 없고 `2027학년도 9월`(p149)이 있다.
   p145 2026-6월 → p149 2027-9월 → p153 2027-6월 → p157 2026 수능 순서이며, 시험지 제목 그대로 기록했다(추측으로 고치지 않음).
2. **선택지 텍스트 OCR 불가 94문항** (`CHOICE_TEXT_UNRELIABLE`) — 선택지가 ㉠~㉤, A~C 같은 원문자 기호뿐이라 OCR 로 읽을 수 없다. 이미지는 정상.
3. **2014학년도 수능 7번** — 원본 PDF 에서 ④ 선택지에 취소선(필기 흔적)이 있어 표지 검출 실패.
4. **스캔본 2016학년도 6월(p25~28)** — 문항번호 OCR 보정 2건(9·11번), 시각 자료 판정 불가(`null`) 20문항.
5. **2025학년도 수능 1번** — 번호 숫자를 읽지 못해 순서상 1번으로 추정(`QNUM_UNREADABLE_INFERRED`).
6. 선택지 텍스트가 빈 3문항(2016-9월 19번, 2017 수능 19·20번).
7. 모든 한글 텍스트는 OCR 결과(음절 사이 공백 등 잡음 있음). 검색/분석 시 이미지로 대조할 것.

## 설치

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-kor
pip install -r requirements.txt
```

## 실행

```bash
export PYTHONPATH=src

python -m csatdb inventory            # STEP 1~3: 분할 PDF 탐색·순서 복원·연속성 검사
python -m csatdb run --pages 1-8      # 소규모 테스트 (원본 합본 페이지 기준)
python -m csatdb run                  # 전체 batch (이미 처리된 파일은 건너뜀)
python -m csatdb run --force          # 강제 재처리 (기존 산출물은 data/processed/backups/<run_id>/ 로 백업 후 교체)
python -m csatdb run --overwrite-outputs   # 페이지 OCR 캐시는 재사용, 문항 JSON/이미지만 재생성 (백업 후 교체)
python -m csatdb qa                   # QA·중복검사·SQLite·보고서만 재생성
python -m csatdb serve                # 검수 UI → http://localhost:8765
python -m pytest -q tests             # 단위 테스트
```

### Checkpoint / Resume
- 페이지 단위 캐시(`data/processed/pages/<compilation>/p###/`)에 렌더링·레이아웃·OCR 결과와
  원본 분할 PDF 의 SHA-256 이 저장된다. 중단 후 다시 `run` 하면 완료된 페이지는 건너뛰고 이어서 처리한다.
- 파일 단위 상태는 `data/processed/state.json`. SHA-256 이 같고 `done` 이면 재처리하지 않는다 (`--force` 제외).
- 문항 JSON/이미지는 기존 결과와 내용이 다르면 **덮어쓰지 않고** `data/processed/pending/<run_id>/` 에 보관한다.

## 처리 방식 (요약)

1. **원본 페이지 복원** — 분할 번호(`_01_`…`_08_`) 순으로 정렬하고 실제 페이지 수를 누적해
   `original_compilation_page = 분할 시작 페이지 + split_pdf_page − 1` 을 계산한다.
   파일명의 `p041-060` 범위는 검증에만 쓰며, **시험 정보는 파일명에서 추론하지 않는다.**
2. **텍스트 소스** — PDF embedded text 를 먼저 검사했으나, 이 합본은 폰트 ToUnicode 가 손상되어
   한글이 전부 깨진 코드로 들어 있다(숫자·마침표만 그대로 또는 0x1F 시프트로 복원 가능).
   그래서 한글 본문은 tesseract(kor) OCR 을 쓰고, 문항 번호는 embedded 숫자(디지털 페이지)를 우선 사용한다.
   OCR 결과는 교정하지 않는다.
3. **레이아웃** — 300dpi 렌더링 후 중앙 세로 구분선(없으면 여백)으로 2단을 찾고, 스캔 페이지는 기울기를 보정한다.
4. **시험 경계** — 각 페이지 머리글의 제목 줄(“YYYY학년도 대학수학능력시험 [6월|9월 모의평가] 문제지”)을
   kor/eng × 3배율 OCR 로 읽어 투표한다. 합의가 약하면 추측하지 않고 `review_required`.
   시행연도(`exam_year`)는 학년도 − 1.
5. **문항 분할** — 문항 번호 위치(단의 가장 왼쪽)부터 다음 번호 직전까지를 crop 하고 하단 공백을 잘라낸다.
   시험지 끝의 “※ 확인 사항” 상자는 제외한다. 번호 없는 단 상단 내용은 이전 문항의 연속으로 이어 붙이고 경고한다.
6. **구조화** — 발문(‘?’까지), 제시문/자료/〈보기〉(passage), 선택지. 선택지 경계는 이미지에서 원문자(①~⑤)
   고리 모양을 직접 검출해 정하고(세로/가로 배열 모두), 실패 시에만 OCR 표지로 대체한다.
7. **시각 자료** — PDF 내부 래스터 이미지·벡터 곡선/선 개수로 판정(휴리스틱). 스캔 페이지는 `null`(판정 불가).
8. **crop 완전성** — 단의 마지막 문항은 잉크가 끝나는 공백 띠까지 하단을 확장하고, crop 상·하단 가장자리에
   잉크가 걸치면 `CROP_EDGE_INK`, 본문 영역의 잉크 중 어떤 문항 crop 에도 속하지 않은 부분이 있으면
   `UNASSIGNED_INK_REGION`(errors.csv) + 인접 문항 `review_required` 로 보고한다.
9. **QA/중복** — 번호 누락·중복, ID 충돌, 선택지≠5, 발문 누락, 이미지 누락/크기 이상, 텍스트 실패, 출처 미상,
   시험 순서·누락 슬롯, 페이지 매핑, 빈 JSON, DB↔JSON 불일치(sha256·선택지 수)를 자동 검사한다.
   파일 SHA-256, 페이지·문항 이미지 perceptual hash, 문항 텍스트 3-gram 유사도로 중복 후보를 표시한다(삭제하지 않음).

## JSON 구조

```jsonc
{
  "id": "2024_SEPTEMBER_LIFE_ETHICS_Q15",
  "source": { "academic_year": 2024, "exam_year": 2023, "exam_type": "SEPTEMBER", "subject": "LIFE_ETHICS",
              "original_compilation": "생윤기출모음(첫해~26-6까지)", "split_pdf": "...", "split_pdf_page": 7,
              "original_compilation_page": 127, "question_number": 15,
              "exam_key": "...", "column": "L|R", "bbox_px": [x0,y0,x1,y1], "render_dpi": 300, "split_pdf_sha256": "..." },
  "original": { "question_image": "data/questions/images/<id>.png", "raw_text": "", "stem": "", "passage": "",
                "choices": [{"number": 1, "text": "..."}], "has_visual_material": true,
                "visual_material_evidence": [], "points": 3 },
  "answer": { "value": null, "source": null, "verified": false },
  "extraction": { "confidence": 0.87, "review_required": false, "warnings": [], "qa_flags": [],
                  "text_source": "...", "question_number_source": "embedded:identity|ocr", "choice_method": "ring_markers:vertical", ... }
}
```

## SQLite 스키마

`subjects`, `compilations`, `source_files`, `pages`, `exams`, `questions`(FK→exams), `choices`, `assets`,
`extraction_logs`, `duplicate_candidates`, `reviews`, 그리고 Phase 2 확장용 `annotation_fields` +
`question_annotations`(EAV: 통합사회 관련성, 단원, 핵심 개념, 출제 메커니즘, 자료 유형, 사상가/이론, 난이도,
변형 가능성, 2028 예시문항 대응). Phase 2 필드는 **비어 있으며** 값의 출처(`source`)와 검증 여부를 함께 기록하도록 설계했다.

DB 는 JSON 으로부터 재생성되는 파생물이다(`questions.json_sha256`, `assets.sha256` 으로 추적).
사람 검수 결과는 `data/reviews/reviews.jsonl` 에 남아 DB 재생성 시 다시 적재된다.

## 과목 추가

`config/sources.json` 의 `subjects`(이미 9과목 정의) 에 맞춰 `compilations` 에 항목을 추가하고
`data/raw/<SUBJECT>/` 에 PDF 를 넣은 뒤 `python -m csatdb run` 하면 된다. 코드에 과목별 분기는 없다.

## 알려진 한계

- 한글 텍스트는 OCR 결과이므로 오탈자가 있다(특히 ㄱ·ㄴ·ㄷ 조합 선택지, 한자, 원문자 ㉠). **원문 판단은 항상 문항 이미지로 한다.**
- 선택지 번호는 원문자 위치의 읽기 순서로 부여한다.
- `has_visual_material` 은 휴리스틱이며 스캔 페이지는 `null`.
