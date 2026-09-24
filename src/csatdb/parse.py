"""문항 OCR 텍스트 -> 발문 / 제시문(자료·보기) / 선택지 구조화.

OCR 결과를 '교정'하지 않는다. 구조 경계만 찾고, 불확실하면 경고를 남긴다.
"""
from __future__ import annotations

import re

import numpy as np
from scipy import ndimage as ndi

# ①~⑤ 가 OCR 에서 '0)', '(3)', '@', '©', '(0' 등으로 읽히는 경우를 모두 '선택지 표지'로 인정
_CIRCLED = "①②③④⑤⑥"
_MARK_TOKEN = re.compile(r"^(?:[①-⑤]|[\(\[{]?[0-9@©®OoQ]{1,2}[\)\]}]|[\(\[{][0-9@©®OoQ]|[@©®])")
_MARK_EXACT = re.compile(r"^(?:[①-⑤]|[\(\[{]?[0-9@©®OoQ]{1,2}[\)\]}]|[\(\[{][0-9@©®OoQ]|[@©®])$")
_QNUM_PREFIX = re.compile(r"^\s*[0-9OoIl|]{1,2}\s*[.,·]\s*")


_STRICT_MARK = re.compile(r"^[\(\[{]?[0-9@©®OoQ]{1,2}[\)\]}]?$")
_HANGUL = re.compile(r"[\u3131-\u318E\uAC00-\uD7A3]")


def is_marker_token(tok: str) -> bool:
    """선택지 표지로 볼 수 있는 토큰 (원문자 또는 OCR 이 숫자/기호로 읽은 짧은 토큰)."""
    if not tok:
        return False
    if tok[0] in _CIRCLED:
        return True
    return bool(_STRICT_MARK.match(tok)) and not _HANGUL.search(tok)


def is_marker(tok: str) -> bool:
    return is_marker_token(tok) or bool(_MARK_TOKEN.match(tok) and not _HANGUL.search(tok[:2]))


def marker_split(tok: str) -> tuple[str, str] | None:
    """토큰이 선택지 표지로 시작하면 (표지, 나머지) 반환."""
    m = _MARK_TOKEN.match(tok)
    if not m:
        return None
    return m.group(0), tok[m.end():]


def _first_word_small(l: dict) -> bool:
    w = l["words"][0]
    return w["w"] <= 1.8 * max(1, w["h"])


def find_choice_block(lines: list[dict], stem_end: int) -> tuple[int, float | None]:
    """아래쪽부터 선택지 줄 묶음을 찾는다. 반환: (시작 줄 index, 표지 x 위치).

    선택지 표지(①~⑤)는 항상 같은 x 에 정렬된다. OCR 이 표지를 한글로 오인한 경우도
    있으므로(예: '어스'), 표지 열에 정렬된 하단 줄은 최대 5줄까지 선택지 줄로 본다.
    """
    strict = [i for i in range(stem_end + 1, len(lines)) if is_marker(lines[i]["words"][0]["text"])]
    if not strict:
        return len(lines), None
    mx = float(np.median([lines[i]["words"][0]["x"] for i in strict[-5:]]))
    i = len(lines) - 1
    start = len(lines)
    n_aligned = 0
    while i > stem_end and n_aligned < 5:
        x = lines[i]["words"][0]["x"]
        if abs(x - mx) <= 12:
            start = i
            n_aligned += 1
        elif x > mx + 20 and i - 1 > stem_end and abs(lines[i - 1]["words"][0]["x"] - mx) <= 12:
            pass    # 선택지의 줄바꿈 연속 줄
        else:
            break
        i -= 1
    return start, mx


def _split(lines: list[dict], mx: float | None, mid_split: bool) -> list[str]:
    choices: list[list[str]] = []
    for l in lines:
        ws = l["words"]
        aligned = mx is not None and abs(ws[0]["x"] - mx) <= 12
        for k, w in enumerate(ws):
            t = w["text"]
            if k == 0:
                if aligned:
                    ms = marker_split(t)
                    if ms and is_marker(t):
                        rest = ms[1]
                    else:
                        rest = None   # 표지 위치의 토큰(OCR 오인식)은 표지로 간주하고 본문에서 제외
                    choices.append([rest] if rest else [])
                elif choices:
                    choices[-1].append(t)
                else:
                    choices.append([t])
                continue
            if t[0] in _CIRCLED or (mid_split and is_marker_token(t)):
                ms = marker_split(t)
                choices.append([ms[1]] if ms and ms[1] else [])
            elif choices:
                choices[-1].append(t)
            else:
                choices.append([t])
    return [" ".join(c).strip() for c in choices]


def split_choices(lines: list[dict], mx: float | None) -> list[str]:
    """줄 중간 표지 분할은 결과가 정확히 5개가 될 때만 채택 (가로 배열 선택지 대응)."""
    plain = _split(lines, mx, mid_split=False)
    if len(plain) == 5:
        return plain
    mid = _split(lines, mx, mid_split=True)
    if len(mid) == 5:
        return mid
    return plain if abs(len(plain) - 5) <= abs(len(mid) - 5) else mid


# --------------------------------------------------------------------------- 이미지 기반 선택지 표지(①~⑤) 검출
def detect_rings(img: np.ndarray, T: float) -> list[dict]:
    """원문자(①~⑤)의 바깥 고리: 정사각형에 가깝고, 속이 비어 있으며, 안쪽에 숫자 획이 있는 연결 요소."""
    dark = img < 190
    lab, _ = ndi.label(dark)
    out = []
    for i, sl in enumerate(ndi.find_objects(lab)):
        if sl is None:
            continue
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        if not (0.55 * T <= h <= 1.6 * T and 0.55 * T <= w <= 1.6 * T and 0.8 <= w / h <= 1.25):
            continue
        comp = lab[sl] == (i + 1)
        fill = comp.mean()
        if not (0.06 <= fill <= 0.35):
            continue
        # 안티앨리어싱으로 고리 획이 1px 끊긴 경우를 위해 1px 팽창 후 구멍 채움
        dil = ndi.binary_dilation(np.pad(comp, 2), iterations=1)
        filled = ndi.binary_fill_holes(dil)
        if filled.sum() - dil.sum() < 0.3 * h * w:
            continue
        # 속이 빈 원(○)은 제외: 중앙부에 숫자 획(고리와 붙어 있어도 됨)이 있어야 함
        cy0, cy1 = int(h * 0.25), int(h * 0.75)
        cx0, cx1 = int(w * 0.3), int(w * 0.7)
        if dark[sl][cy0:cy1, cx0:cx1].sum() < 0.02 * h * w:
            continue
        out.append({"x": sl[1].start, "y": sl[0].start, "w": w, "h": h, "cy": (sl[0].start + sl[0].stop) / 2})
    return out


def _rows(rings: list[dict], tol: float) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for r in sorted(rings, key=lambda r: r["cy"]):
        if rows and abs(rows[-1][0]["cy"] - r["cy"]) <= tol:
            rows[-1].append(r)
        else:
            rows.append([r])
    for row in rows:
        row.sort(key=lambda r: r["x"])
    return rows


def _cluster_leaders(row: list[dict], T: float) -> list[dict]:
    """한 행에서 고리 사이 간격이 2.5T 를 넘을 때마다 새 선택지로 보고, 각 군집의 첫 고리(=표지)만 반환.
    (①㉠, ㉡  ②㉠, ㉢ … 처럼 원문자 기호가 표지와 같은 크기로 섞인 가로 배열 대응)"""
    out = []
    for i, r in enumerate(row):
        if i == 0 or r["x"] - row[i - 1]["x"] > 2.5 * T:
            out.append(r)
    return out


def choose_markers(rings: list[dict], T: float) -> tuple[list[dict] | None, str]:
    if not rings:
        return None, "no_rings"
    rows = _rows(rings, tol=0.4 * T)
    mx = rows[-1][0]["x"]
    qual = []
    for row in reversed(rows):              # 아래 -> 위, 첫 고리가 같은 x 에 정렬된 행만
        if abs(row[0]["x"] - mx) <= 0.4 * T:
            qual.append(row)
        else:
            break
    qual.reverse()
    # A) 세로 배열: 정렬된 5개 행의 첫 고리
    if len(qual) >= 5:
        return [row[0] for row in qual[-5:]], "vertical"
    # B) 가로 배열: 하단 1~3개 행의 고리 합이 정확히 5
    #    (①㉠ ②㉡ … 처럼 표지와 원문자 기호가 같은 크기로 쌍을 이루면 쌍의 첫 고리만 표지로 채택)
    for k in (1, 2, 3):
        if k <= len(qual):
            for reducer, tag in ((lambda r: r, ""), (lambda r: _cluster_leaders(r, T), "_clustered")):
                sel = [x for row in qual[-k:] for x in reducer(row)]
                if len(sel) == 5:
                    return sel, f"horizontal_{k}rows{tag}"
    return None, f"ring_layout_unresolved rows={[len(r) for r in qual]}"


def choose_markers_by_size(rings: list[dict]) -> tuple[list[dict] | None, str]:
    """고리를 크기별로 묶고, 큰 군집부터 선택지 배열이 성립하는 군집을 채택."""
    if not rings:
        return None, "no_rings"
    groups: list[list[dict]] = []
    for r in sorted(rings, key=lambda r: -r["h"]):
        for g in groups:
            if abs(g[0]["h"] - r["h"]) <= 4 and abs(g[0]["w"] - r["w"]) <= 4:
                g.append(r)
                break
        else:
            groups.append([r])
    tried = []
    for g in groups:
        if len(g) < 5:
            tried.append(len(g))
            continue
        T = float(np.median([r["h"] for r in g]))
        m, how = choose_markers(g, T)
        if m:
            for x in m:
                x["T"] = T
            return m, how
        tried.append(how)
    return None, f"unresolved groups={tried}"


def assign_choice_text(lines: list[dict], markers: list[dict], T: float) -> tuple[int, list[str]]:
    """표지 위치 기준으로 단어를 선택지에 배정. 반환: (선택지 시작 줄 index, 선택지 텍스트 5개)."""
    mk = sorted(markers, key=lambda m: (round(m["cy"] / (0.4 * T)), m["x"]))
    texts: list[list[str]] = [[] for _ in mk]
    first_cy = mk[0]["cy"]
    start_line = len(lines)
    for li, l in enumerate(lines):
        lcy = (l["y0"] + l["y1"]) / 2
        if lcy < first_cy - 0.5 * T:
            continue
        start_line = min(start_line, li)
        for w in l["words"]:
            cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
            t = w["text"]
            # 표지 위치에서 시작하는 토큰 = 표지의 OCR 결과 → 표지 부분 제거
            if any(abs(w["x"] - m["x"]) <= 0.35 * T and abs(cy - m["cy"]) <= 0.6 * T for m in mk):
                ms = marker_split(t)
                if ms:
                    t = ms[1]
                elif len(t) <= 3:
                    t = ""
                if not t:
                    continue
            idx = None
            for k, m in enumerate(mk):
                same_row = abs(cy - m["cy"]) <= 0.6 * T
                if (m["cy"] < cy - 0.6 * T) or (same_row and m["x"] <= cx):
                    idx = k
            if idx is not None:
                texts[idx].append(t)
    return start_line, [" ".join(t).strip() for t in texts]


_STEM_END = re.compile(r"(것은|것인가|은가|는가|인가|옳은가|적절한가)[?？:;.,\[\]]*(\[?3점\]?)?$")


def is_stem_end(line: str) -> bool:
    """발문 끝 판정. OCR 이 '?' 를 ':' ';' '[]' 등으로 읽는 경우도 '…것은' 어미로 인정."""
    t = line.replace(" ", "")
    return "?" in t or "？" in t or bool(_STEM_END.search(t))


def cell_ocr(img: np.ndarray, markers: list[dict], k: int, T: float) -> str:
    """선택지 k 의 칸(표지 오른쪽 ~ 같은 행 다음 표지 또는 오른쪽 끝)만 잘라 한 줄 OCR."""
    from .pages import tesseract_tsv
    from PIL import Image
    r = markers[k]
    nxt = [m for m in markers if abs(m["cy"] - r["cy"]) < 0.4 * T and m["x"] > r["x"]]
    x1 = min(m["x"] for m in nxt) - 5 if nxt else img.shape[1]
    y0, y1 = max(0, int(r["y"] - 0.3 * T)), min(img.shape[0], int(r["y"] + r["h"] + 0.3 * T))
    cell = img[y0:y1, r["x"] + r["w"] + 2:x1]
    if cell.size == 0:
        return ""
    return " ".join(w["text"] for w in tesseract_tsv(Image.fromarray(cell), 7, "kor")).strip()


def parse_question_text(lines: list[dict], img: np.ndarray | None = None) -> dict:
    """lines: [{'text':..., 'words':[{'text':...}], 'x0':...}] (문항 영역, 위->아래)."""
    warnings: list[str] = []
    texts = [l["text"] for l in lines]
    raw_text = "\n".join(texts)
    if not lines:
        return {"raw_text": "", "stem": "", "passage": "", "choices": [], "points": None,
                "warnings": ["TEXT_EXTRACTION_EMPTY"], "choice_method": None}

    # --- 발문: 첫 줄(번호 제거)부터 '?' 가 나오는 줄까지 (최대 4줄)
    first = _QNUM_PREFIX.sub("", texts[0], count=1)
    stem_lines = [first]
    stem_end = 0
    found_q = is_stem_end(first)
    if not found_q:
        for i in range(1, min(4, len(texts))):
            stem_lines.append(texts[i])
            if is_stem_end(texts[i]):
                stem_end = i
                found_q = True
                break
    if not found_q:
        stem_lines = [first]
        stem_end = 0
        warnings.append("STEM_END_NOT_FOUND")
    stem = " ".join(s.strip() for s in stem_lines).strip()
    # '[3점]' 이 발문 다음 줄에 단독으로 있는 경우
    if stem_end + 1 < len(texts) and re.fullmatch(r"\s*[\[\(]?\s*3\s*점\s*[\]\)]?\s*", texts[stem_end + 1] or ""):
        stem += " " + texts[stem_end + 1].strip()
        stem_end += 1
    points = 3 if re.search(r"3\s*점", stem) else None

    # --- 선택지: 아래에서부터 표지(또는 표지 열에 정렬된 짧은 첫 토큰)로 시작하는 연속 줄
    method = None
    choices = None
    if img is not None:
        hs = [w["h"] for l in lines for w in l["words"] if 0.5 * 45 < w["h"] < 2 * 45]
        T = float(np.median(hs)) if hs else 45.0
        markers, how = choose_markers_by_size(detect_rings(img, T))
        if markers:
            T_ = markers[0]["T"]
            mk_sorted = sorted(markers, key=lambda m: (round(m["cy"] / (0.4 * T_)), m["x"]))
            choice_start, choices = assign_choice_text(lines, mk_sorted, T_)
            for k, ctext in enumerate(choices):
                if not ctext.strip():
                    alt = cell_ocr(img, mk_sorted, k, T_)
                    if alt:
                        choices[k] = alt
                        warnings.append(f"CHOICE{k + 1}_TEXT_FROM_CELL_OCR")
            choice_start = max(choice_start, stem_end + 1)
            method = f"ring_markers:{how}"
        else:
            warnings.append(f"CHOICE_MARKERS_NOT_RESOLVED_BY_IMAGE ({how})")
    if choices is None:
        choice_start, mx = find_choice_block(lines, stem_end)
        choices = split_choices(lines[choice_start:], mx) if choice_start < len(lines) else []
        method = "ocr_text_markers"
    if len(choices) != 5:
        warnings.append(f"CHOICES_COUNT_{len(choices)}")
    passage = "\n".join(texts[stem_end + 1:choice_start]).strip()
    return {"raw_text": raw_text, "stem": stem, "passage": passage,
            "choices": [{"number": k + 1, "text": c} for k, c in enumerate(choices)],
            "points": points, "warnings": warnings, "choice_method": method}
