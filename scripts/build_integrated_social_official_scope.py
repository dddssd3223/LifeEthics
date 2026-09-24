"""2022 개정 사회과 교육과정 [별책7] PDF의 통합사회1·2 부분을 판정 lookup MASTER로 구조화 (원문 그대로, 요약 없음).

입력: reference/curriculum/2022개정_사회과_별책7.pdf
출력: reference/curriculum/integrated_social_official_scope.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "reference/curriculum/2022개정_사회과_별책7.pdf"
OUT = ROOT / "reference/curriculum/integrated_social_official_scope.json"

HEADER = re.compile(r"^(선택 중심 교육과정 – 공통 과목 -|사회과 교육과정|\d{3})$")


def clean_lines(text: str) -> list[str]:
    out = []
    skip_box = False
    for ln in text.split("\n"):
        s = ln.strip()
        if not s or HEADER.match(s):
            continue
        # 성취기준 페이지 사이에 끼어든 내용 체계(과정⋅기능/가치⋅태도) 표 조각 제거
        if s == "가치⋅태도":
            skip_box = True
            continue
        if s.startswith("⋅"):
            continue
        if skip_box and not s.startswith(("•", "[", "(")):
            continue
        skip_box = False
        out.append(ln.lstrip())
    return out


def join(parts: list[str]) -> str:
    # PDF 줄바꿈 복원: 줄 끝 공백이 있으면 띄어쓰기, 없으면 단어 중간 줄바꿈으로 보고 그대로 이어 붙임
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def main():
    doc = pymupdf.open(PDF)
    pages = [doc[i].get_text() for i in range(len(doc))]
    course_pages = {"통합사회1": range(3, 9), "통합사회2": range(10, 16)}

    # 내용 체계(지식·이해) — 원문 표에서 영역별 내용 요소
    content_elements = {
        "통합사회1": {
            "통합적 관점": ["통합적 관점", "시간적 관점", "공간적 관점", "사회적 관점", "윤리적 관점"],
            "인간, 사회, 환경과 행복": ["행복의 의미", "행복의 조건"],
            "자연환경과 인간": ["자연환경", "자연관", "환경문제", "생태시민"],
            "문화와 다양성": ["문화권", "문화 변동", "문화 상대주의와 보편윤리", "다문화 사회"],
            "생활공간과 사회": ["산업화와 도시화", "교통⋅통신과 과학기술의 발달", "생활공간과 생활양식", "지역사회"],
        },
        "통합사회2": {
            "인권보장과 헌법": ["시민혁명", "인권", "헌법", "시민참여"],
            "사회정의와 불평등": ["정의의 실질적 기준", "정의관", "사회불평등", "공간불평등"],
            "시장경제와 지속가능발전": ["시장경제와 합리적 선택", "경제 주체의 역할", "국제 분업과 무역", "금융 생활"],
            "세계화와 평화": ["세계화", "국제분쟁", "평화", "세계시민"],
            "미래와 지속가능한 삶": ["인구 문제", "자원 위기", "미래 삶의 방향", "지속가능발전"],
        },
    }
    # 원문 대조: 내용 요소가 PDF 텍스트에 실제로 존재하는지 확인
    flat = re.sub(r"\s+", "", pages[2] + pages[9])
    for c in content_elements.values():
        for els in c.values():
            for e in els:
                assert re.sub(r"\s+", "", e) in flat, e

    core_ideas = {}
    for course, pi in (("통합사회1", 2), ("통합사회2", 9)):
        seg = pages[pi].split("핵심 아이디어")[1].split("범주")[0]
        items = [join(x.split("\n")) for x in seg.split("⋅") if x.strip()]
        core_ideas[course] = items

    areas = []
    for course, rng in course_pages.items():
        lines = []
        for i in rng:
            lines += clean_lines(pages[i])
        text = "\n".join(lines)
        text = text.split("나. 성취기준", 1)[-1]
        blocks = re.split(r"\n\((\d)\) ([^\n\[]+)\n", "\n" + text)
        # blocks: ['', num, name, body, num, name, body ...]
        for k in range(1, len(blocks), 3):
            num, name, body = blocks[k], blocks[k + 1].strip(), blocks[k + 2]
            std_part, rest = body.split("(가) 성취기준 해설", 1)
            expl_part, cons_part = rest.split("(나) 성취기준 적용 시 고려 사항", 1)
            stds = {}
            for m in re.finditer(r"\[(10통사\d-\d\d-\d\d)\](.*?)(?=\n\[10통사|\Z)", std_part, re.S):
                stds[m.group(1)] = join(m.group(2).split("\n"))
            expl = {}
            for b in expl_part.split("•")[1:]:
                m = re.match(r"\s*\[(10통사\d-\d\d-\d\d)\]", b)
                expl[m.group(1)] = join(b.split("\n"))
            cons = [join(b.split("\n")) for b in cons_part.split("•")[1:]]
            area_code = next(iter(stds)).rsplit("-", 1)[0]
            areas.append({
                "course": course,
                "area_code": area_code,
                "area_name": name,
                "content_elements_knowledge": content_elements[course][name],
                "standards": [{"code": c, "text": t, "explanation": expl.get(c)} for c, t in stds.items()],
                "application_considerations": cons,
            })

    codes = [s["code"] for a in areas for s in a["standards"]]
    assert len(codes) == len(set(codes)) == 30, len(codes)
    assert all(s["explanation"] for a in areas for s in a["standards"])
    master = {
        "source": "「(2022개정) 초·중등학교 교육과정 [별책7] 사회과」 통합사회1·통합사회2 (reference/curriculum/2022개정_사회과_별책7.pdf)",
        "note": "성취기준·해설·적용 시 고려 사항은 PDF 원문 텍스트를 줄바꿈만 이어 붙여 옮김(요약·축약 없음).",
        "core_ideas": core_ideas,
        "process_skills_and_values": "원문 내용 체계의 과정·기능/가치·태도는 두 과목 공통(PDF p.108, p.115).",
        "areas": areas,
    }
    json.dump(master, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(len(areas), len(codes))


if __name__ == "__main__":
    main()
