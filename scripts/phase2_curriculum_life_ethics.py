"""Phase 2-b — 기존 Phase 2 LOW/NONE 문항의 통합사회 교육과정(KICE 최소 성취수준 자료) 대응 판정 (1회성 batch).

입력(읽기 전용): data/phase2/LIFE_ETHICS/analysis.jsonl, curriculum_seed_2022.json, data/questions/json/*.json
출력: data/phase2/LIFE_ETHICS/curriculum_analysis.jsonl, curriculum_summary.json
기존 Phase 1/2 파일·DB·mechanism family 는 변경하지 않는다. DIRECT/ADAPTABLE 213문항은 대상 아님.

판정: OCR 텍스트(공백 제거)에서 KICE 자료에 명시된 개념 키워드를 찾아 성취기준에 대응.
  DIRECT    = 자료의 영역별/성취기준별 성취수준에 명시된 개념이 문항의 중심 소재
  ADAPTABLE = 명시 개념과 연결되나 생윤 고유 소재·깊이라 범위 조정 필요
  NONE      = 대응 성취기준 없음

사용: python scripts/phase2_curriculum_life_ethics.py [--pilot ID,ID,...]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "data/phase2/LIFE_ETHICS"

# (성취기준, 개념 라벨, 핵심 키워드 regex, 기본 판정, 근거 문구)
# 근거 문구는 curriculum_seed_2022.json 의 성취수준 진술에서 가져온 표현.
RULES = [
    ("10통사1-04-03", "문화 상대주의·보편 윤리", r"상대주의|자문화|문화사대|보편윤리|보편적윤리", "DIRECT", "문화 상대주의적 태도와 보편 윤리의 의미"),
    ("10통사1-04-04", "다문화 사회·문화적 다양성", r"다문화|문화적다양성|샐러드|용광로|동화주의|모자이크", "DIRECT", "다문화 사회의 의미, 문화적 다양성 존중"),
    ("10통사1-04-04", "종교·문화 관용", r"관용", "ADAPTABLE", "문화적 다양성을 존중하는 태도"),
    ("10통사1-04-02", "전통문화·의례", r"관혼상제|전통의례|제례|혼례|상례|전통문화", "ADAPTABLE", "우리나라 전통문화 사례를 통한 전통문화의 의미"),
    ("10통사1-03-02", "인간중심주의·생태중심주의", r"인간중심|생태중심|생태|대지윤리|대지의|생명공동체", "DIRECT", "인간중심주의 또는 생태중심주의에 대한 개념, 자연에 대한 인간의 관점"),
    ("10통사1-03-02", "자연관(동양·기타)", r"자연관|천인합일|무위자연|자연과인간|인간과자연", "ADAPTABLE", "자연에 대한 인간의 관점"),
    ("10통사1-03-01", "환경권", r"환경권|쾌적한환경", "DIRECT", "안전하고 쾌적한 환경에서 살아갈 권리"),
    ("10통사1-03-03", "환경문제 해결", r"기후|온난화|탄소|환경문제|환경오염|환경보전|오염", "DIRECT", "환경문제 해결을 위해 노력한 사례"),
    ("10통사1-03-03", "미래 세대 책임", r"미래세대|요나스|책임윤리", "ADAPTABLE", "생태계 위기와 환경문제에 관심"),
    ("10통사1-02-01", "행복의 기준·조건", r"행복", "DIRECT", "행복의 기준이 다를 수 있음, 행복한 삶의 조건"),
    ("10통사2-01-01", "인권의 의미·확장", r"인권", "DIRECT", "인권의 의미, 인권 확장의 사례"),
    ("10통사2-01-03", "사회적 소수자 차별·노동권", r"소수자|차별|양성평등|성평등|노동권|노동자의권리", "DIRECT", "사회적 소수자 차별, 청소년의 노동권 등 국내 인권 문제"),
    ("10통사2-01-02", "헌법·시민의 권익", r"헌법|기본권", "ADAPTABLE", "인권 보장을 위한 헌법의 역할, 시민의 권익 보호"),
    ("10통사2-02-02", "자유주의·공동체주의 정의관", r"공동체주의|자유주의|공동선|연고", "DIRECT", "자유주의적 정의관과 공동체주의적 정의관"),
    ("10통사2-02-01", "정의의 의미·판단 기준", r"분배적정의|교정적정의|절차적정의|정의의원칙|공정한|정의로운", "DIRECT", "정의의 의미, 정의를 판단하는 기준"),
    ("10통사2-02-03", "사회·공간 불평등과 제도", r"불평등|빈부|복지|빈곤|격차|우대", "DIRECT", "사회 및 공간 불평등, 정의로운 사회를 위한 제도적 방안"),
    ("10통사2-03-02", "경제 주체의 역할과 책임", r"사회적책임|윤리경영|윤리적소비|공정무역|소비자|기업", "DIRECT", "지속가능발전을 위한 정부·기업·노동자·소비자의 역할과 책임"),
    ("10통사2-03-02", "노동자의 역할·직업", r"직업|노동|근로", "ADAPTABLE", "노동자의 역할과 책임"),
    ("10통사2-03-01", "자본주의·경제 체제", r"자본주의|시장경제|계획경제", "ADAPTABLE", "경제체제가 인간의 삶에 미치는 영향"),
    ("10통사2-04-01", "세계화와 그 문제", r"세계화|지구촌|세계시민|민족주의", "DIRECT", "세계화의 양상과 문제점"),
    ("10통사2-04-02", "해외 원조(국제 협력)", r"원조", "ADAPTABLE", "국제 사회의 협력, 세계 평화를 위한 행위 주체"),
    ("10통사2-04-02", "국제 갈등·협력과 평화", r"평화|전쟁|분쟁|테러|국제", "ADAPTABLE", "평화의 관점에서 국제 사회의 갈등과 협력, 행위 주체"),
    ("10통사2-04-03", "남북 분단·통일", r"분단|남북|통일|북한", "DIRECT", "남북 분단 상황, 우리나라의 세계 평화 기여"),
    ("10통사2-05-01", "인구 문제", r"저출산|고령화|인구", "DIRECT", "인구 문제의 사례"),
    ("10통사2-05-02", "자원 소비·지속가능 발전", r"지속가능|(?<!의료)자원|에너지", "DIRECT", "자원 소비 실태의 문제점, 지속가능한 발전을 위한 개인적 실천"),
    ("10통사2-05-03", "미래 사회 변화", r"인공지능|로봇|미래사회", "ADAPTABLE", "미래 사회의 변화 양상"),
    ("10통사1-05-02", "과학기술·정보화와 생활양식", r"과학기술|과학자|정보사회|정보통신|인터넷|사이버|뉴미디어|정보화", "ADAPTABLE", "교통·통신 및 과학기술의 발달과 생활양식 변화"),
    ("10통사1-01-01", "윤리적 관점", r"윤리적관점|시간적관점|공간적관점|사회적관점", "ADAPTABLE", "시간적·공간적·사회적·윤리적 관점"),
]
# 생윤 고유 영역(교육과정 자료에 명시 개념 없음) — 이 표지가 중심이면 약한 키워드 매칭을 강등
OUT_OF_SCOPE = r"안락사|낙태|뇌사|장기이식|장기기증|복제|유전자|배아|자살|사랑과성|사랑은|사랑한다|사랑의|성적자기결정|결혼|부부|효(?:\(|도)|수양|해탈|열반|무위|메타윤리|도덕언어|정언명령|예술|대중문화|심미주의"
RANK = {"DIRECT": 2, "ADAPTABLE": 1, "NONE": 0}
# 일상어라 단발 언급으로는 대응 근거가 되지 않는 규칙: (성취기준, 개념 라벨) → 최소 등장 횟수
MIN_HITS = {("10통사2-02-01", "정의의 의미·판단 기준"): 2, ("10통사2-04-02", "국제 갈등·협력과 평화"): 2,
            ("10통사2-03-02", "노동자의 역할·직업"): 2, ("10통사2-05-02", "자원 소비·지속가능 발전"): 2,
            ("10통사1-03-03", "환경문제 해결"): 2, ("10통사1-02-01", "행복의 기준·조건"): 2,
            ("10통사1-04-04", "종교·문화 관용"): 2}


def squash(s):
    return re.sub(r"\s+", "", s or "")


def classify(q: dict, prev: dict, areas: dict) -> dict:
    ex = q["extraction"]
    text = squash(q["original"].get("raw_text"))
    oos = re.findall(OUT_OF_SCOPE, text)

    cands = []
    for std, label, pat, rel, basis in RULES:
        hits = re.findall(pat, text)
        if not hits:
            continue
        n = len(hits)
        if n < MIN_HITS.get((std, label), 1):
            continue
        r = rel
        if std == "10통사1-02-01" and n < 3:
            r = "ADAPTABLE"
        # 해외 원조 문항의 빈곤·불평등은 국내 사회 불평등(2-02-03)이 아니라 국제 협력 맥락
        if std == "10통사2-02-03" and "원조" in text:
            continue
        # 공리주의 '최대 행복'은 행복의 기준·조건과 다른 소재
        if std == "10통사1-02-01" and re.search(r"공리|최대다수|최대행복", text):
            r = "ADAPTABLE" if n >= 3 else None
        # '정의로운'만 한 번 나오는 경우 등 단발 언급은 강등
        if r == "DIRECT" and n < 2:
            r = "ADAPTABLE"
        # 원조·전쟁 중심 문항: 평화 다회 + 국제 협력 맥락이면 DIRECT
        if std == "10통사2-04-02" and text.count("평화") >= 2 and re.search(r"국제|협력|갈등", text):
            r = "DIRECT"
        if std == "10통사2-04-03" and not re.search(r"분단|남북", text):
            r = "ADAPTABLE"
        if std == "10통사2-03-02" and "기업" in hits and not re.search(r"책임|윤리경영|소비", text):
            r = "ADAPTABLE" if n >= 2 else None
        if r is None:
            continue
        # 생윤 고유 영역이 중심인 문항은 한 단계 강등
        if oos and len(oos) >= n and r == "DIRECT":
            r = "ADAPTABLE"
        elif oos and len(oos) >= 2 * n:
            r = None
        if r is None:
            continue
        cands.append((RANK[r], n, std, label, r, basis, sorted(set(hits))))

    base = {
        "question_id": q["id"],
        "phase2_relevance": prev["relevance"],
    }
    if not cands:
        return {**base, "curriculum_relevance": "NONE", "curriculum_area": None, "achievement_standard": None,
                "secondary_standards": [], "concepts": [],
                "reason": "KICE 통합사회1·2 자료의 영역·성취기준에 명시된 개념과 대응하지 않음"
                          + (f"(생윤 고유 소재: {', '.join(sorted(set(oos))[:3])})." if oos else "."),
                "review_required": False, "review_reasons": []}

    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    rank, n, std, label, rel, basis, hits = cands[0]
    area_code = std.rsplit("-", 1)[0]
    secondary = []
    for c in cands[1:]:
        if c[2] != std and c[2] not in secondary and c[0] >= 1:
            secondary.append(c[2])

    why = []
    if n < 2:
        why.append("single_keyword_hit")
    if ex.get("review_required"):
        why.append("phase1_review_required")
    if ex.get("confidence", 1) < 0.7:
        why.append("low_ocr_confidence")
    if len(cands) > 1 and cands[1][0] == rank and cands[1][2].rsplit("-", 1)[0] != area_code and cands[1][1] >= n:
        why.append("multiple_areas_tie")
    if oos:
        why.append("life_ethics_specific_topic_present")

    tag = "성취수준 진술에 명시된 개념이 문항 중심 소재" if rel == "DIRECT" else "명시 개념과 연결되나 생윤 소재·깊이 조정 필요"
    return {**base,
            "curriculum_relevance": rel,
            "curriculum_area": f"{area_code} {areas[area_code]}",
            "achievement_standard": std,
            "secondary_standards": secondary[:3],
            "concepts": [label] + [c[3] for c in cands[1:3] if c[3] != label],
            "reason": f"[{std}] '{basis}' ↔ 문항 키워드({', '.join(hits[:4])}) — {tag}.",
            "review_required": bool(why) and rel != "NONE",
            "review_reasons": why}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot")
    a = ap.parse_args()

    seed = json.load(open(P2 / "curriculum_seed_2022.json", encoding="utf-8"))
    areas = {x["area_code"]: x["area_name"] for x in seed["areas"]}
    known = {s["code"] for x in seed["areas"] for s in x["standards"]}
    assert all(r[0] in known for r in RULES)

    prev = {}
    for line in open(P2 / "analysis.jsonl", encoding="utf-8"):
        r = json.loads(line)
        prev[r["question_id"]] = r
    targets = [qid for qid, r in prev.items() if r["relevance"] in ("LOW", "NONE")]
    if a.pilot:
        targets = [t for t in a.pilot.split(",") if t in set(targets)]

    recs = []
    for qid in sorted(targets):
        q = json.load(open(ROOT / f"data/questions/json/{qid}.json", encoding="utf-8"))
        recs.append(classify(q, prev[qid], areas))

    if a.pilot:
        for r in recs:
            print(json.dumps(r, ensure_ascii=False))
        return

    with open(P2 / "curriculum_analysis.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rel = collections.Counter(r["curriculum_relevance"] for r in recs)
    hit = [r for r in recs if r["curriculum_relevance"] != "NONE"]
    summary = {
        "total_targets": len(recs),
        "target_filter": "Phase 2 relevance in (LOW, NONE)",
        "by_phase2_relevance": dict(collections.Counter(r["phase2_relevance"] for r in recs)),
        "curriculum_relevance": {k: rel.get(k, 0) for k in ["DIRECT", "ADAPTABLE", "NONE"]},
        "by_area": dict(collections.Counter(r["curriculum_area"] for r in hit).most_common()),
        "by_standard": dict(collections.Counter(r["achievement_standard"] for r in hit).most_common()),
        "direct_by_standard": dict(collections.Counter(r["achievement_standard"] for r in hit
                                                       if r["curriculum_relevance"] == "DIRECT").most_common()),
        "review_required": sum(r["review_required"] for r in recs),
        "method": "KICE 최소 성취수준 자료의 명시 개념 키워드 규칙 매칭(기존 OCR 텍스트). 신규 OCR·AI 호출 없음.",
    }
    json.dump(summary, open(P2 / "curriculum_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
