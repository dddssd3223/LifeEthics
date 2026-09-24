"""STEP 3 이후: PDF 내부 머리글(제목)에서 시험 경계를 탐지한다.

- 파일 경계는 무시하고 원본 합본 페이지(1..N) 전체를 하나의 연속 자료로 본다.
- 제목 페이지(“YYYY학년도 대학수학능력시험 [6월|9월 모의평가] 문제지”)가 새 시험의 시작.
- 제목 줄은 여러 OCR 변형(kor/eng × 배율) 결과를 투표하여 판정하고,
  합의가 약하면 추측하지 않고 review_required 로 넘긴다.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

import numpy as np
from PIL import Image

from .common import read_json, write_json_atomic
from .pages import tesseract_tsv, PAGE_CACHE_VERSION

TITLE_CACHE_VERSION = PAGE_CACHE_VERSION + "-title2"
TITLE_HINTS = ("학년", "년도", "문제지", "능력", "시험", "모의", "평가")


def title_bands(a: np.ndarray, top: int) -> list[tuple[int, int]]:
    dark = a[:top] < 150
    H, W = dark.shape
    rows = dark[:, int(W * .1):int(W * .8)].sum(1)
    bands, st = [], None
    for y, v in enumerate(rows > 3):
        if v and st is None:
            st = y
        if not v and st is not None:
            if y - st > 25:
                bands.append((st, y))
            st = None
    return bands


def ocr_title_line(page_dir: Path, force: bool = False) -> dict:
    cache = page_dir / "title.json"
    c = read_json(cache)
    if c and not force and c.get("cache_version") == TITLE_CACHE_VERSION:
        return c
    meta = read_json(page_dir / "page.json")
    a = np.asarray(Image.open(page_dir / "page.png"))
    top = meta["layout"].get("content_top", int(a.shape[0] * .2))
    bands = title_bands(a, top)
    variants = []
    if bands:
        b = bands[0]
        base = Image.fromarray(a[max(0, b[0] - 15):b[1] + 15, :int(a.shape[1] * 0.85)])
        for f in (1, 2, 3):
            im = base.reduce(f) if f > 1 else base
            for lang in ("kor", "eng"):
                words = tesseract_tsv(im, 7, lang)
                variants.append({"factor": f, "lang": lang,
                                 "text": " ".join(w["text"] for w in words),
                                 "conf": float(np.mean([w["conf"] for w in words])) if words else 0.0})
        # 연도 숫자 전용 OCR: 제목 줄 왼쪽 끝(잉크 시작)부터 글자 높이의 약 3배 폭
        band = a[max(0, b[0] - 15):b[1] + 15, :int(a.shape[1] * 0.85)]
        cols = np.where((band < 150).sum(0) > 0)[0]
        if len(cols):
            hgt = b[1] - b[0]
            x0 = max(0, int(cols.min()) - 10)
            ycrop = Image.fromarray(band[:, x0:x0 + int(hgt * 3.0)])
            for f in (1, 2, 3):
                im = ycrop.reduce(f) if f > 1 else ycrop
                words = tesseract_tsv(im, 7, "eng", extra=["-c", "tessedit_char_whitelist=0123456789"])
                variants.append({"factor": f, "lang": "digits",
                                 "text": "".join(w["text"] for w in words),
                                 "conf": float(np.mean([w["conf"] for w in words])) if words else 0.0})
    c = {"cache_version": TITLE_CACHE_VERSION, "band": list(bands[0]) if bands else None, "variants": variants}
    write_json_atomic(cache, c)
    return c


def is_title_candidate(page_dir: Path) -> bool:
    meta = read_json(page_dir / "page.json")
    ocr = read_json(page_dir / "ocr.json")
    head_txt = " ".join(v["text"] for v in ocr.get("header", [])).replace(" ", "")
    lay = meta["layout"]
    tall_header = lay.get("content_top", 0) > 0.13 * lay.get("H", 1)
    return tall_header or any(h in head_txt for h in TITLE_HINTS)


_YEAR = re.compile(r"(20\d\d)")


def parse_title(variants: list[dict]) -> dict:
    """제목 OCR 변형들을 투표하여 학년도/시험종류 판정."""
    years, types = collections.Counter(), collections.Counter()
    by_lang: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    texts = []
    for v in variants:
        t = v["text"].replace(" ", "")
        texts.append(t)
        m = _YEAR.match(t) or _YEAR.search(t[:8])
        if m:
            years[int(m.group(1))] += 1
            by_lang[v["lang"]][int(m.group(1))] += 1
        if v["lang"] == "kor":
            is_title = ("능력" in t or "시험" in t or "문제지" in t or "학년도" in t or "년도" in t)
            if not is_title:
                continue
            if re.search(r"6월", t):
                types["JUNE"] += 1
            elif re.search(r"9월", t):
                types["SEPTEMBER"] += 1
            elif "모의" in t or "평가" in t:
                types["MOCK_UNKNOWN_MONTH"] += 1
            elif "예비" in t:
                types["PRELIMINARY"] += 1
            else:
                types["CSAT"] += 1
    n_kor = sum(1 for v in variants if v["lang"] == "kor")
    out = {"texts": texts, "year_votes": dict(years), "type_votes": dict(types),
           "academic_year": None, "exam_type": None, "confidence": 0.0, "warnings": []}
    out["year_votes_by_lang"] = {k: dict(v) for k, v in by_lang.items()}
    if years:
        ranked = years.most_common()
        y, cnt = ranked[0]
        tie = len(ranked) > 1 and ranked[1][1] == cnt
        out["year_agreement"] = cnt / max(1, len(variants))
        # 숫자 전용 OCR(digits)·eng 는 숫자 인식이 kor 보다 안정적 → 이들의 다수결이 있으면 그것과 일치해야 함
        num_votes = by_lang.get("digits", collections.Counter()) + by_lang.get("eng", collections.Counter())
        num_top = num_votes.most_common(2)
        num_ok = bool(num_top) and (len(num_top) == 1 or num_top[0][1] > num_top[1][1])
        if num_ok and num_top[0][0] != y:
            y, cnt, tie = num_top[0][0], years[num_top[0][0]], False
            out["year_agreement"] = cnt / max(1, len(variants))
        if not tie and cnt >= max(3, len(variants) // 2) and (not num_votes or (num_ok and num_top[0][0] == y)):
            out["academic_year"] = y
        else:
            out["warnings"].append(f"TITLE_YEAR_UNCERTAIN votes={dict(years)}")
    else:
        out["warnings"].append("TITLE_YEAR_NOT_FOUND")
    if types:
        t, cnt = types.most_common(1)[0]
        out["type_agreement"] = cnt / max(1, n_kor)
        if t in ("JUNE", "SEPTEMBER", "CSAT") and cnt >= 2:
            out["exam_type"] = t
        else:
            out["warnings"].append(f"TITLE_EXAM_TYPE_UNCERTAIN votes={dict(types)}")
    else:
        out["warnings"].append("TITLE_EXAM_TYPE_NOT_FOUND")
    out["confidence"] = round(min(out.get("year_agreement", 0), out.get("type_agreement", 0)), 3)
    return out


def header_subject_ok(page_dir: Path, keywords: list[str]) -> bool | None:
    ocr = read_json(page_dir / "ocr.json")
    txt = " ".join(v["text"] for v in ocr.get("header", [])).replace(" ", "")
    if not txt:
        return None
    kws = [k.replace(" ", "") for k in keywords]
    # OCR 이 '생활과윤리' 를 '생활과윤기','생할과92' 등으로 읽는 경우가 있어 부분 일치 허용
    return any(k in txt for k in kws) or ("생활" in txt or "생할" in txt or "윤리" in txt)


def header_page_number(page_dir: Path) -> int | None:
    ocr = read_json(page_dir / "ocr.json")
    votes = collections.Counter()
    for v in ocr.get("header", []):
        t = v["text"].strip()
        m = re.match(r"^([1-4])\b", t) or re.search(r"\b([1-4])$", t)
        if m:
            votes[int(m.group(1))] += 1
    if not votes:
        return None
    n, c = votes.most_common(1)[0]
    return n if c >= 2 else None


def detect_exams(comp_key: str, subject: str, subject_cfg: dict, page_dirs: dict[int, Path], log) -> list[dict]:
    """page_dirs: original_page -> cache dir. 반환: 시험 목록 (페이지 범위 포함)."""
    pages = sorted(page_dirs)
    starts = []
    for p in pages:
        d = page_dirs[p]
        if not is_title_candidate(d):
            continue
        t = ocr_title_line(d)
        parsed = parse_title(t["variants"])
        if parsed["academic_year"] is None and parsed["exam_type"] is None:
            # 제목 형식이 전혀 없으면 제목 페이지가 아님
            if not any(h in "".join(parsed["texts"]) for h in ("학년도", "문제지", "능력시험")):
                continue
        starts.append((p, parsed))
    exams = []
    if not starts or starts[0][0] != pages[0]:
        first = starts[0][0] if starts else pages[-1] + 1
        orphan = [p for p in pages if p < first]
        if orphan:
            log.log("ERROR", "exams", "PAGES_BEFORE_FIRST_TITLE",
                    f"첫 제목 페이지 이전 페이지 {orphan} 는 출처 미상", pages=orphan)
            exams.append({"exam_key": f"UNKNOWN_P{orphan[0]:03d}", "academic_year": None, "exam_type": None,
                          "pages": orphan, "title": None, "warnings": ["NO_TITLE_PAGE"],
                          "review_required": True})
    for i, (p, parsed) in enumerate(starts):
        end = starts[i + 1][0] - 1 if i + 1 < len(starts) else pages[-1]
        pg = [q for q in pages if p <= q <= end]
        warnings = list(parsed["warnings"])
        exp_pages = subject_cfg.get("expected_pages_per_exam", 4)
        if len(pg) != exp_pages:
            warnings.append(f"EXAM_PAGE_COUNT_{len(pg)}_EXPECTED_{exp_pages}")
        # 쪽 번호 연속성 (머리글 숫자, 읽힌 경우만)
        for k, q in enumerate(pg[1:], start=2):
            n = header_page_number(page_dirs[q])
            if n is not None and n != k:
                warnings.append(f"HEADER_PAGE_NO_MISMATCH p{q}: read {n}, expected {k}")
        subj_flags = [header_subject_ok(page_dirs[q], subject_cfg["header_keywords"]) for q in pg]
        if any(f is False for f in subj_flags):
            warnings.append("SUBJECT_KEYWORD_NOT_FOUND_IN_SOME_HEADERS")
        y, t = parsed["academic_year"], parsed["exam_type"]
        key = f"{y}_{t}_{subject}" if (y and t) else f"UNKNOWN_P{p:03d}_{subject}"
        exams.append({
            "exam_key": key, "academic_year": y,
            "exam_year": (y - 1) if y else None,   # 학년도 Y 의 6월/9월 모평·수능은 Y-1 년에 시행
            "exam_type": t, "subject": subject, "pages": pg, "title": parsed,
            "title_confidence": parsed["confidence"],
            "warnings": warnings,
            "review_required": bool(parsed["warnings"]) or not (y and t) or parsed["confidence"] < 0.5,
        })
    # 동일 시험 키 중복 → 덮어쓰지 않고 표시
    cnt = collections.Counter(e["exam_key"] for e in exams)
    seen = collections.Counter()
    for e in exams:
        if cnt[e["exam_key"]] > 1:
            seen[e["exam_key"]] += 1
            e["warnings"].append(f"EXAM_KEY_COLLISION ({cnt[e['exam_key']]}회 등장)")
            e["review_required"] = True
            log.log("ERROR", "exams", "EXAM_KEY_COLLISION", f"{e['exam_key']} 가 여러 번 탐지됨",
                    exam=e["exam_key"], pages=e["pages"])
            if seen[e["exam_key"]] > 1:
                e["exam_key"] = f"{e['exam_key']}__DUP{seen[e['exam_key']]}"
    return exams


_TYPE_ORDER = {"JUNE": 0, "SEPTEMBER": 1, "CSAT": 2}


def sequence_anomalies(exams: list[dict]) -> list[dict]:
    """합본 내 시험 순서 이상 + 학년도 범위 내 누락 시험 슬롯 탐지 (판정은 사람에게)."""
    out = []
    known = [e for e in exams if e["academic_year"] and e["exam_type"] in _TYPE_ORDER]
    for a, b in zip(known, known[1:]):
        ka = (a["academic_year"], _TYPE_ORDER[a["exam_type"]])
        kb = (b["academic_year"], _TYPE_ORDER[b["exam_type"]])
        if kb <= ka:
            out.append({"code": "EXAM_ORDER_NOT_CHRONOLOGICAL",
                        "message": f"{a['exam_key']}(p{a['pages'][0]}) 다음에 {b['exam_key']}(p{b['pages'][0]})",
                        "exam_key": b["exam_key"]})
    if known:
        have = {(e["academic_year"], e["exam_type"]) for e in known}
        y0, y1 = min(e["academic_year"] for e in known), max(e["academic_year"] for e in known)
        for y in range(y0, y1 + 1):
            for t in _TYPE_ORDER:
                if (y, t) not in have:
                    out.append({"code": "EXAM_SLOT_NOT_FOUND",
                                "message": f"{y}학년도 {t} 시험이 합본에서 발견되지 않음 (범위 {y0}~{y1})",
                                "exam_key": f"{y}_{t}"})
    return out
