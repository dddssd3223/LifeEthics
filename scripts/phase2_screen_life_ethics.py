"""Phase 2 — 2028 통합사회 예시문항 기준 생활과 윤리 800문항 선별 (1회성 batch 스크립트).

입력: data/questions/json/*.json (Phase 1 OCR 결과, 읽기 전용), data/phase2/LIFE_ETHICS/seed_2028.json
출력: data/phase2/LIFE_ETHICS/{analysis.jsonl, mechanism_families.json, summary.json}
      data/database/questions.db 에 phase2_* 테이블만 추가 (기존 테이블 변경 없음)

판정 방식: OCR 텍스트(공백 제거)에서 예시문항 개념 키워드와 발문 구조를 찾아 규칙으로 분류한다.
새 OCR·이미지 분석·AI 호출 없음.

사용: python scripts/phase2_screen_life_ethics.py [--pilot ID,ID,...] [--no-db]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/phase2/LIFE_ETHICS"
DB = ROOT / "data/database/questions.db"

# ---------------------------------------------------------------------------
# 예시문항 개념 → 키워드 (공백 제거 OCR 텍스트에 대한 부분 문자열)
# tier: 이 주제의 기본 관련도. DIRECT 는 예시문항 개념과 직접 대응할 때만.
# ---------------------------------------------------------------------------
TOPICS = {
    "CD": {  # Q14
        "samples": ["Q14"], "tier": "DIRECT",
        "kw": {"불복종": "시민 불복종", "준법": "준법 의무", "법에대한충실": "법에 대한 충실성",
               "공유된정의관": "공유된 정의관", "다수의정의감": "공유된 정의관", "비폭력": "비폭력성",
               "처벌을감수": "처벌 감수", "공개적": "공개성", "최후의수단": "최후의 수단"},
        "core": ["불복종"],
    },
    "RAWLS": {  # Q15
        "samples": ["Q15"], "tier": "DIRECT",
        "kw": {"원초적": "원초적 입장", "무지의베일": "무지의 베일", "차등의원칙": "차등의 원칙",
               "최소수혜": "최소 수혜자", "최대수혜": "최소 수혜자", "소유권리": "노직 소유 권리",
               "소유권": "소유권", "취득": "취득의 원리", "이전의원리": "이전의 원리", "교정의원리": "교정의 원리",
               "최소국가": "최소 국가", "우연적": "우연성 배제", "천부적": "천부적 재능"},
        "core": ["원초적", "무지의베일", "차등의원칙", "최소수혜", "소유권리", "취득", "이전의원리",
                 "교정의원리", "최소국가"],
    },
    "DIST": {  # Q13
        "samples": ["Q13"], "tier": "ADAPTABLE",
        "kw": {"분배": "분배 정의", "업적": "업적에 따른 분배", "필요에따른": "필요에 따른 분배",
               "능력에따른": "능력에 따른 분배", "우대": "적극적 우대 조치", "역차별": "역차별",
               "공동선": "공동선", "공동체주의": "공동체주의", "복지": "사회 복지", "왈처": "왈처",
               "샌델": "샌델", "연고적": "연고적 자아"},
        "core": ["분배", "업적", "필요에따른", "우대", "역차별", "공동선", "공동체주의", "연고적"],
        "direct_if": ["우대", "역차별"],
    },
    "PUNISH": {  # Q16
        "samples": ["Q16"], "tier": "DIRECT",
        "kw": {"형벌": "형벌의 정당화", "응보": "응보주의", "사형": "사형 제도", "교화": "교화",
               "범죄예방": "범죄 예방(공리주의)", "예방": "범죄 예방(공리주의)", "동등성": "동등성의 원리",
               "존엄": "인간 존엄성", "수단으로": "수단화 금지", "사회계약": "사회 계약"},
        "core": ["형벌", "응보", "사형"],
    },
    "PEACE": {  # Q18
        "samples": ["Q18"], "tier": "ADAPTABLE",
        "kw": {"구조적폭력": "구조적 폭력", "문화적폭력": "문화적 폭력", "직접적폭력": "직접적 폭력",
               "적극적평화": "적극적 평화", "소극적평화": "소극적 평화", "갈퉁": "갈퉁",
               "평화": "평화", "폭력": "폭력"},
        "core": ["구조적폭력", "문화적폭력", "직접적폭력", "적극적평화", "소극적평화", "갈퉁"],
        "direct_if": ["구조적폭력", "문화적폭력", "직접적폭력", "적극적평화", "소극적평화", "갈퉁"],
        "needs_both": ["평화", "폭력"],  # core 없을 때 둘 다 있어야 ADAPTABLE
    },
    "ENV": {  # Q4
        "samples": ["Q4"], "tier": "ADAPTABLE",
        "kw": {"대지윤리": "대지 윤리(레오폴드)", "대지의이용": "대지 윤리(레오폴드)", "인간중심": "인간 중심주의", "생태": "생태 중심주의",
               "정복": "자연 정복(베이컨)", "생명공동체": "생명 공동체", "대지공동체": "대지 공동체",
               "내재적가치": "자연의 내재적 가치", "도구적가치": "자연의 도구적 가치",
               "동물": "동물 중심주의", "생명중심": "생명 중심주의", "생명외경": "생명 외경"},
        "core": ["대지윤리", "대지의이용", "인간중심", "생태", "정복", "생명공동체", "대지공동체", "내재적가치",
                 "도구적가치", "동물", "생명중심", "생명외경"],
        "direct_if": ["대지윤리", "대지의이용", "인간중심", "생태중심", "정복", "대지공동체"],
    },
    "HAPPY": {  # Q1
        "samples": ["Q1"], "tier": "LOW",
        "kw": {"행복": "행복", "쾌락": "쾌락", "고통이없": "고통 없는 상태(에피쿠로스)",
               "동요": "마음의 평정(에피쿠로스)", "평정": "마음의 평정(에피쿠로스)",
               "궁극목적": "행복=궁극 목적(아리스토텔레스)", "궁극적목적": "행복=궁극 목적(아리스토텔레스)",
               "탁월": "탁월성·덕(아리스토텔레스)", "중용": "중용"},
        "core": [],
        "direct_if": ["고통이없", "동요", "평정", "궁극목적", "궁극적목적", "탁월"],
        "needs_both": ["행복", "쾌락"],
    },
    "CULT": {  # Q8 / Q3
        "samples": ["Q8"], "tier": "ADAPTABLE",
        "kw": {"상대주의": "문화 상대주의", "자문화": "자문화 중심주의", "문화사대": "문화 사대주의",
               "보편": "보편 윤리", "다문화": "다문화 정책", "동화": "동화주의", "샐러드": "샐러드 볼",
               "용광로": "용광로", "모자이크": "모자이크", "국수": "국수 대접", "인권": "인권"},
        "core": ["상대주의", "자문화", "문화사대", "다문화", "동화", "샐러드", "용광로", "모자이크"],
        "direct_if": ["상대주의", "자문화", "문화사대"],
        "q3_if": ["다문화", "동화", "샐러드", "용광로", "모자이크", "국수"],
    },
}
AID_KW = ["원조"]           # 해외 원조: 예시문항에 없음 → LOW (롤스·노직 개념만 약하게 연결)
WAR_KW = ["전쟁", "정전", "영구평화"]  # 전쟁 윤리: 예시문항에 없음 → LOW (Q18 약연결)

# ---------------------------------------------------------------------------
# 출제 메커니즘 family (학생이 정답을 결정하는 사고 구조)
# ---------------------------------------------------------------------------
FAMILIES = {
    "ETH-CD-01": ("시민불복종 정당화 조건 판별", "단일 사상가(롤스형) 제시문 → 공개성·비폭력·처벌 감수·공유된 정의관·체제 인정 등 조건별 진술 정오", ["Q14"]),
    "ETH-CD-02": ("시민불복종 사상가 간 조건 대조", "2인 이상 사상가(소로·롤스·싱어 등)의 불복종 대상·정당화 근거 차이를 진술별로 귀속", ["Q14"]),
    "ETH-RAWLS-01": ("원초적 입장·정의 원칙 조건 판별", "원초적 입장/무지의 베일/차등의 원칙 명제로 진술 정오 판정", ["Q15"]),
    "ETH-RAWLS-02": ("롤스-노직 분배 정의 대조", "두 사상가 진술을 갑만/을만/공통 영역으로 배치(벤다이어그램·비교형)", ["Q15"]),
    "ETH-JUST-01": ("분배 기준·우대 조치 판단", "필요·업적·능력 등 분배 기준 또는 적극적 우대 조치의 정당화 논거 판별", ["Q13"]),
    "ETH-JUST-02": ("자유주의-공동체주의 관점 판별", "개인 권리 우선 vs 공동선·구성원 책임 우선 진술 구별", ["Q13"]),
    "ETH-PUN-01": ("응보주의 형벌관 판별", "단일 사상가(칸트형) 제시문 → 존엄성·동등성·응보 진술 vs 예방 진술 구별", ["Q16"]),
    "ETH-PUN-02": ("응보주의-공리주의 형벌·사형 대조", "칸트/루소/베카리아/벤담 등 형벌·사형 근거를 사상가별로 귀속", ["Q16"]),
    "ETH-PEACE-01": ("갈퉁 폭력 유형·적극적 평화 판별", "직접적·구조적·문화적 폭력 구분과 평화 구축 방식 진술 정오", ["Q18"]),
    "ETH-PEACE-02": ("평화·폭력 관점 일반 판별", "평화 개념(소극/적극)·폭력 정당성에 대한 입장 진술 판별", ["Q18"]),
    "ETH-ENV-01": ("인간중심주의-생태적 관점 비교", "자연의 가치·인간의 지위·자연 이용 정당성 진술을 갑/을 각각 판정(적어도 한 사람/공통/차이)", ["Q4"]),
    "ETH-ENV-02": ("동물·생명 중심주의 도덕적 고려 범위 판별", "도덕적 고려 대상의 범위(쾌고 감수 능력·생명)를 기준으로 진술 귀속", ["Q4"]),
    "ETH-HAPPY-01": ("사상가 행복관의 사례 적용", "행복의 본질(덕·쾌락·평정)을 식별하고 사례 인물에게 줄 조언/평가 진술 판정", ["Q1"]),
    "ETH-CULT-01": ("문화 이해 태도 판별(자문화 중심·상대주의·보편 윤리)", "발언자의 문화 평가 기준을 식별하고 보편 가치 인정 여부로 진술 귀속", ["Q8"]),
    "ETH-CULT-02": ("다문화 정책 모형 구분", "동화주의·용광로·샐러드 볼 등 정책 모형의 특징과 사례 대응", ["Q3"]),
}
TOPIC_FAMILY_DEFAULT = {
    "CD": "ETH-CD-01", "RAWLS": "ETH-RAWLS-01", "DIST": "ETH-JUST-01", "PUNISH": "ETH-PUN-01",
    "PEACE": "ETH-PEACE-02", "ENV": "ETH-ENV-02", "HAPPY": "ETH-HAPPY-01", "CULT": "ETH-CULT-01",
}
RANK = {"DIRECT": 3, "ADAPTABLE": 2, "LOW": 1, "NONE": 0}


def squash(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def structure(stem: str, text: str) -> dict:
    st = {
        "venn": bool(re.search(r"그림", stem)) and bool(re.search(r"만의입장|공통입장|공통의입장", text)),
        "syllogism": bool(re.search(r"대전제|소전제|삼단논법|전제.{0,80}결론", text)),
        "at_least_one": "적어도" in stem,
        "advice": bool(re.search(r"조언|충고", stem)),
        "teacher_qa": bool(re.search(r"교사|학생의|질문에", stem)),
        "negative": bool(re.search(r"옳지않|옮지않|않은것", stem)),
        "yes_no": bool(re.search(r"긍정|부정", stem)),
        "multi": bool(re.search(r"갑,?을|갑과을|\(가\),\(나\)|A,B", stem + text[:200])),
        "three_way": bool(re.search(r"병[:;은의]", text)) or "갑,을,병" in stem,
        "flow": bool(re.search(r"흐름|순서도|→|\(다\)", stem)),
    }
    return st


def classify(q: dict) -> dict:
    o = q["original"]
    text = squash(o.get("raw_text"))
    stem = squash(o.get("stem"))[:160] or text[:160]
    st = structure(stem, text)

    best = None  # (rank, score, topic, rel, hits, direct_hits)
    matched_topics = []
    for tname, t in TOPICS.items():
        hits = [k for k in t["kw"] if k in text]
        core = [k for k in t["core"] if k in text]
        need = t.get("needs_both")
        if not core and not (need and all(k in text for k in need)):
            continue
        dhits = [k for k in t.get("direct_if", []) if k in text]
        rel = t["tier"]
        if tname == "RAWLS" and len(core) < 2 and "정의" not in text:
            rel = "ADAPTABLE"
        if tname == "PUNISH" and not re.search(r"형벌|사형|응보", text):
            continue
        if dhits:
            rel = "DIRECT"
        if tname == "HAPPY" and not dhits:
            rel = "LOW"  # 공리주의 쾌락 계산 등: Q1(덕·평정 행복관)과 약한 연결
        if tname == "PEACE" and not dhits:
            rel = "ADAPTABLE" if all(k in text for k in ("평화", "폭력")) else "LOW"
        if tname == "CULT" and not dhits:
            univ = "보편" in text and bool(re.search(r"인권|관용", text))
            rel = "ADAPTABLE" if (univ or [k for k in t["q3_if"] if k in text]) else "LOW"
        if tname == "DIST" and not dhits and not re.search(r"정의|업적|필요에따른|능력에따른|공동선|공동체주의", text):
            rel = "LOW"  # 의료 자원 분배 등 정의론과 무관한 '분배'
        if tname == "HAPPY" and text.count("행복") < 2 and not dhits:
            continue
        if tname == "ENV" and not dhits and not re.search(r"동물|생명중심|생명외경", text):
            rel = "LOW"
        score = len(set(t["kw"][k] for k in hits)) + 2 * len(core)
        matched_topics.append((tname, rel, score))
        cand = (RANK[rel], score, tname, rel, hits, dhits)
        if best is None or cand[:2] > best[:2]:
            best = cand

    aid = any(k in text for k in AID_KW)
    war = any(k in text for k in WAR_KW)

    if best is None:
        if aid:
            return _rec(q, "LOW", ["Q15"], ["해외 원조"], "", [], "해외 원조는 예시문항에 없음; 롤스·노직 개념만 Q15와 약하게 연결.", st, 0)
        if war:
            return _rec(q, "LOW", ["Q18"], ["전쟁 윤리"], "", [], "전쟁 윤리는 예시문항에 없음; 평화 관점만 Q18과 약하게 연결.", st, 0)
        return _rec(q, "NONE", [], [], "", [], "2028 예시문항의 생윤 연계 개념(행복·자연관·문화 태도·분배·시민불복종·정의론·형벌·평화)과 무관.", st, 0)

    _, score, tname, rel, hits, dhits = best
    t = TOPICS[tname]

    # 원조 문항의 롤스·노직 언급은 원조 맥락 → Q15 약연결
    if aid and tname == "RAWLS":
        rel = "LOW"
    # 전쟁 문항의 평화 언급은 Q18(갈퉁) 직접 대응이 아님
    if war and tname == "PEACE" and not dhits:
        rel = "LOW"

    # 깊이 조정: 3인 이상 비교·흐름도 등 예시문항보다 깊은 구조는 DIRECT → ADAPTABLE (Q8 제외)
    depth_note = ""
    if rel == "DIRECT" and tname != "CULT" and (st["three_way"] or st["flow"] or st["syllogism"]):
        rel = "ADAPTABLE"
        depth_note = " 3인 이상 비교·흐름도·논증 재구성 등 예시문항에 없는 구조라 변형 필요."

    samples = list(t["samples"])
    if tname == "CULT" and not dhits and not ("보편" in text and re.search(r"인권|관용", text)) \
            and [k for k in t["q3_if"] if k in text]:
        samples = ["Q3"]
    for other, orel, _ in matched_topics:
        if other != tname and RANK[orel] >= 2:
            for s in TOPICS[other]["samples"]:
                if s not in samples:
                    samples.append(s)

    concepts = sorted(set(t["kw"][k] for k in hits))

    fam = TOPIC_FAMILY_DEFAULT[tname]
    if tname == "CD" and (st["multi"] or st["three_way"]):
        fam = "ETH-CD-02"
    elif tname == "RAWLS" and (st["multi"] or st["venn"] or re.search(r"소유권리|취득|최소국가", text)):
        fam = "ETH-RAWLS-02"
    elif tname == "DIST" and re.search(r"공동선|공동체주의|연고적", text) and not re.search(r"우대|역차별|업적", text):
        fam = "ETH-JUST-02"
    elif tname == "PUNISH" and (st["multi"] or st["three_way"] or re.search(r"예방|베카리아|공리", text)):
        fam = "ETH-PUN-02"
    elif tname == "PEACE" and dhits:
        fam = "ETH-PEACE-01"
    elif tname == "ENV" and (dhits or st["at_least_one"]) and not re.search(r"동물|생명중심|생명외경", text):
        fam = "ETH-ENV-01"
    elif tname == "ENV" and dhits and st["multi"]:
        fam = "ETH-ENV-01"
    elif tname == "CULT" and samples[0] == "Q3":
        fam = "ETH-CULT-02"

    if rel in ("DIRECT", "ADAPTABLE"):
        reuse = []
        if rel == "DIRECT":
            reuse.append("concept_as_is")
        else:
            reuse.append("reduce_depth")
        if st["venn"]:
            reuse.append("venn_structure")
        if st["at_least_one"]:
            reuse.append("at_least_one_or")
        if st["advice"]:
            reuse.append("case_application")
        if st["teacher_qa"]:
            reuse.append("teacher_student_frame")
        if st["negative"]:
            reuse.append("negative_choice")
        reuse.append("passage_reuse")
        reuse.append("choice_statement_reuse")
        mech = f"{fam} {FAMILIES[fam][0]}"
    else:
        reuse, mech, fam = [], "", ""

    tag = {"DIRECT": "예시문항 개념·판단 방식과 직접 대응", "ADAPTABLE": "관련 영역이나 깊이·범위 조정 필요",
           "LOW": "주변 개념만 약하게 연결"}[rel]
    reason = f"{'/'.join(samples)} 연계: {', '.join(concepts[:4])} — {tag}.{depth_note}"
    rr = len([m for m in matched_topics if RANK[m[1]] >= 2]) >= 2
    return _rec(q, rel, samples, concepts, mech, reuse, reason, st, score, fam, rr)


def _rec(q, rel, samples, concepts, mech, reuse, reason, st, score, fam="", ambiguous=False):
    ex = q["extraction"]
    review = False
    why = []
    if rel in ("DIRECT", "ADAPTABLE"):
        if ex.get("review_required"):
            review, _ = True, why.append("phase1_review_required")
        if ex.get("confidence", 1) < 0.7:
            review, _ = True, why.append("low_ocr_confidence")
        if ambiguous:
            review, _ = True, why.append("multiple_sample_topics")
    return {
        "question_id": q["id"],
        "relevance": rel,
        "sample_matches": samples,
        "concepts": concepts,
        "mechanism": mech,
        "reuse_mode": reuse,
        "reason": reason,
        "mechanism_family": fam or None,
        "review_required": review,
        "review_reasons": why,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", help="쉼표로 구분한 question_id 목록 (pilot 결과만 출력)")
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()

    qs = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(str(ROOT / "data/questions/json/*.json")))]
    if a.pilot:
        ids = set(a.pilot.split(","))
        for q in qs:
            if q["id"] in ids:
                print(json.dumps(classify(q), ensure_ascii=False))
        return

    recs = [classify(q) for q in qs]
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "analysis.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fam_count = collections.Counter(r["mechanism_family"] for r in recs if r["mechanism_family"])
    fams = []
    for fid, (name, desc, smp) in FAMILIES.items():
        members = [r["question_id"] for r in recs if r["mechanism_family"] == fid]
        fams.append({"family_id": fid, "name": name, "thinking_structure": desc, "sample_matches": smp,
                     "count": len(members),
                     "direct": sum(1 for r in recs if r["mechanism_family"] == fid and r["relevance"] == "DIRECT"),
                     "question_ids": members})
    json.dump({"families": fams}, open(OUT / "mechanism_families.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    rel = collections.Counter(r["relevance"] for r in recs)
    samp = collections.Counter(s for r in recs if r["relevance"] != "NONE" for s in r["sample_matches"])
    main_s = ["Q4", "Q13", "Q14", "Q15", "Q16", "Q18"]
    summary = {
        "total": len(recs),
        "relevance": {k: rel.get(k, 0) for k in ["DIRECT", "ADAPTABLE", "LOW", "NONE"]},
        "sample_links": {**{s: samp.get(s, 0) for s in main_s},
                         "기타": {s: c for s, c in samp.items() if s not in main_s}},
        "sample_links_note": "relevance != NONE 문항 기준, 한 문항이 여러 예시문항에 연결될 수 있음",
        "mechanism_family_count": len([f for f in fams if f["count"]]),
        "direct_adaptable_review_required": sum(1 for r in recs if r["review_required"]),
        "top_families": [{"family_id": f, "name": FAMILIES[f][0], "count": c} for f, c in fam_count.most_common(10)],
        "method": "Phase 1 OCR 텍스트 기반 규칙 분류(예시문항 개념 키워드 + 발문 구조). 신규 OCR·AI 호출 없음.",
    }
    json.dump(summary, open(OUT / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if not a.no_db:
        con = sqlite3.connect(DB)
        con.executescript("""
        DROP TABLE IF EXISTS phase2_life_ethics_analysis;
        CREATE TABLE phase2_life_ethics_analysis (
            question_id TEXT PRIMARY KEY, relevance TEXT NOT NULL, sample_matches TEXT, concepts TEXT,
            mechanism TEXT, mechanism_family TEXT, reuse_mode TEXT, reason TEXT, review_required INTEGER);
        DROP TABLE IF EXISTS phase2_mechanism_families;
        CREATE TABLE phase2_mechanism_families (
            family_id TEXT PRIMARY KEY, name TEXT, thinking_structure TEXT, sample_matches TEXT, count INTEGER);
        """)
        con.executemany("INSERT INTO phase2_life_ethics_analysis VALUES (?,?,?,?,?,?,?,?,?)", [
            (r["question_id"], r["relevance"], json.dumps(r["sample_matches"], ensure_ascii=False),
             json.dumps(r["concepts"], ensure_ascii=False), r["mechanism"], r["mechanism_family"],
             json.dumps(r["reuse_mode"]), r["reason"], int(r["review_required"])) for r in recs])
        con.executemany("INSERT INTO phase2_mechanism_families VALUES (?,?,?,?,?)", [
            (f["family_id"], f["name"], f["thinking_structure"], json.dumps(f["sample_matches"]), f["count"]) for f in fams])
        con.commit()
        con.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
