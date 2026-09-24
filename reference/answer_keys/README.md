# 공식 정답표 (answer keys)

이 폴더에 **한국교육과정평가원 공식 정답표**를 옮긴 CSV 를 넣으면 다음 실행 시 문항에 자동 연결된다.
AI 가 문제를 풀어 정답을 채우는 것은 금지된다. 공식 출처가 없는 정답은 넣지 않는다.

파일 형식 (UTF-8, 헤더 필수):

```csv
exam_key,question_number,answer,source
2024_SEPTEMBER_LIFE_ETHICS,1,3,한국교육과정평가원 2024학년도 9월 모의평가 정답표
```

- `exam_key` 는 `data/reports/coverage_report.csv` 의 exam_key 와 동일해야 한다.
- 연결된 정답은 `answer.verified = true`, `answer.source = <source>` 로 기록된다.
- 현재(Phase 1 완료 시점) 저장소와 합본 PDF 안에는 공식 정답표가 없어 모든 문항의 `answer.value` 는 `null` 이다.
