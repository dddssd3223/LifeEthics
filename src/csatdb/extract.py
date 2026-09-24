"""STEP 5~9: 시험 경계 -> 문항 분할 -> 이미지 crop -> 텍스트 구조화 -> JSON 저장.

원칙
- 문항을 삭제/선별하지 않는다. 불확실하면 review_required = true 로 보존한다.
- 정답은 공식 정답표(reference/answer_keys/*.csv)가 있을 때만 연결한다.
- 기존 산출물은 --force 가 없으면 덮어쓰지 않는다 (--force 시 백업 후 교체).
"""
from __future__ import annotations

import collections
import csv
import json
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .common import (Q_IMAGES, Q_JSON, PROCESSED, ROOT, RENDER_DPI, PIPELINE_VERSION,
                     read_json, write_json_atomic, rel, sha256_file, now_iso)
from .exams import detect_exams, sequence_anomalies
from .inventory import Inventory
from .pages import group_lines
from .parse import parse_question_text
from .stage_pages import page_dir

TOP_PAD = 14          # 문항 번호 위 여백(px @300dpi)
ANSWER_KEY_DIR = ROOT / "reference" / "answer_keys"


# --------------------------------------------------------------------------- candidates
_OCR_QNUM = re.compile(r"^([0-9OoIl|]{1,2})[.,·。:]")
_OCR_DIGIT = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def column_lines(ocr: dict, side: str) -> list[dict]:
    return group_lines(ocr.get("columns", {}).get(side, []))


def read_qnum_digits(page_png: Path, line: dict) -> tuple[int | None, str]:
    """문항 번호 위치만 잘라 숫자 전용 OCR(eng, whitelist)로 재판독."""
    from .pages import tesseract_tsv
    with Image.open(page_png) as im:
        x0 = max(0, int(line["x0"]) - 15); y0 = max(0, int(line["y0"]) - 12)
        crop = im.crop((x0, y0, x0 + 150, int(line["y1"]) + 12))
        words = tesseract_tsv(crop, 7, "eng", pad=30,
                              extra=["-c", "tessedit_char_whitelist=0123456789."])
    tok = "".join(w["text"] for w in words)
    m = re.match(r"^(\d{1,2})\.", tok) or re.match(r"^(\d{1,2})", tok)
    return (int(m.group(1)) if m else None), tok


def question_starts(meta: dict, ocr: dict, col: dict, page_png: Path | None = None) -> tuple[list[dict], list[str]]:
    """한 단(column)의 문항 시작 후보. embedded 숫자(디지털) 우선, 그 외는 OCR(+숫자 재판독)."""
    notes = []
    x0, x1 = col["x0"], col["x1"]
    emb = [q for q in meta["objects"].get("embedded_qnums", [])
           if x0 - 12 <= q["bbox"][0] <= x0 + 70 and q["bbox"][2] <= x1]
    digital = meta["objects"].get("embedded_text_chars", 0) > 200 and not meta["objects"].get("is_scanned")
    lines = column_lines(ocr, col["side"])
    starts = []
    if digital and emb:
        for q in sorted(emb, key=lambda q: q["bbox"][1]):
            starts.append({"num": q["num"], "y": int(q["bbox"][1]), "source": f"embedded:{q['decode']}"})
    else:
        if digital and not emb:
            notes.append("NO_EMBEDDED_QNUM_IN_COLUMN")
        xs = [l["x0"] for l in lines if len(l["text"]) >= 3]
        if xs:
            left = float(np.percentile(xs, 2))
            for l in lines:
                if l["x0"] > left + 25:
                    continue
                tok = l["words"][0]["text"]
                m = _OCR_QNUM.match(tok)
                looks_num = bool(m) or bool(re.match(r"^[0-9OoIl|]{0,2}[.,·。:]", tok))
                if not looks_num:
                    continue
                n_tok = int(m.group(1).translate(_OCR_DIGIT)) if m else None
                n_dig, dig_tok = (None, "")
                if page_png is not None:
                    n_dig, dig_tok = read_qnum_digits(page_png, l)
                num = n_dig if n_dig is not None else n_tok
                starts.append({"num": num, "y": int(l["y0"]), "source": "ocr", "ocr_token": tok,
                               "digit_ocr": dig_tok, "token_num": n_tok, "ocr_conf": l["words"][0]["conf"]})
    # 같은 y 근처 중복 제거
    dedup = []
    for s_ in starts:
        if dedup and abs(dedup[-1]["y"] - s_["y"]) < 20:
            continue
        dedup.append(s_)
    return dedup, notes


# --------------------------------------------------------------------------- helpers
def shift_lines(lines: list[dict], dx: float, dy: float) -> list[dict]:
    """페이지 좌표 줄 -> 문항 이미지 좌표 줄 (복사본)."""
    out = []
    for l in lines:
        ws = [{**w, "x": w["x"] + dx, "y": w["y"] + dy} for w in l["words"]]
        out.append({**l, "x0": l["x0"] + dx, "x1": l["x1"] + dx, "y0": l["y0"] + dy, "y1": l["y1"] + dy, "words": ws})
    return out


def extend_to_gap(a: np.ndarray, col: dict, y_bot: int, gap: int = 18, limit: int = 250,
                  limit_y: int | None = None) -> int:
    """y_bot 경계에 잉크가 걸쳐 있으면 연속 공백 행(gap)이 나올 때까지 아래로 확장 (limit_y 를 넘지 않음)."""
    H = a.shape[0]
    w = col["x1"] - col["x0"]
    # 좌우 3% 는 제외 (스캔본 구분선 조각·테두리가 모든 행을 '잉크'로 만들지 않도록)
    band = a[:, col["x0"] + int(w * .03):col["x1"] - int(w * .03)] < 150
    rows = band.sum(1)
    if y_bot - 1 >= 0 and rows[max(0, y_bot - 3):y_bot].max(initial=0) == 0:
        return y_bot            # 경계가 이미 공백 위에 있음
    stop = min(H, y_bot + limit, limit_y if limit_y is not None else H)
    blank = 0
    y = y_bot
    while y < stop:
        blank = blank + 1 if rows[y] == 0 else 0
        if blank >= gap:
            return min(y - gap + 1 + 10, stop)     # 공백 여백 10px 포함
        y += 1
    return y_bot


def edge_ink(img: np.ndarray) -> list[str]:
    """crop 가장자리(상/하)에 글자 잉크가 걸쳐 있으면 잘림 가능성."""
    dark = img < 150
    w = dark.shape[1]
    dark = dark[:, int(w * 0.03):int(w * 0.97)]   # 좌우 가장자리(스캔 구분선 조각·테두리) 제외
    out = []
    if dark[-2:, :].sum() > 6:
        out.append("bottom")
    if dark[:2, :].sum() > 6:
        out.append("top")
    return out


def unassigned_ink(a: np.ndarray, col: dict, lay: dict, covered: list, excluded: list,
                   min_rows: int = 12) -> list[tuple[int, int]]:
    """단 본문(content_top ~ content_bottom)에서 문항 crop 에 포함되지 않은 잉크 행 구간."""
    from .pages import _footer_top
    H, W = a.shape
    top = lay["content_top"]
    bottom = lay["content_bottom"]
    if lay.get("method") != "divider_line":
        # 구분선이 없는 페이지(스캔본 여백 fallback)는 하단 추정이 틀릴 수 있어,
        # 독립적으로 찾은 꼬리말(쪽번호·저작권) 바로 위까지를 검사 범위로 삼는다
        ft = _footer_top(a < 170, lay.get("divider_x", W // 2))
        bottom = max(bottom, ft or 0)
    x0, x1 = col["x0"], col["x1"]
    w = x1 - x0
    band = a[:, x0 + int(w * .03):x1 - int(w * .03)] < 150
    rows = band.sum(1) > 3
    mask = np.zeros(H, bool)
    mask[top:bottom] = True
    for y0, y1 in list(covered) + list(excluded):
        mask[max(0, y0):max(0, y1)] = False
    ys = np.where(rows & mask)[0]
    out, st, pv = [], None, None
    for y in ys:
        if st is None:
            st = y
        elif y - pv > 8:
            if pv - st + 1 >= min_rows:
                out.append((int(st), int(pv)))
            st = y
        pv = y
    if st is not None and pv - st + 1 >= min_rows:
        out.append((int(st), int(pv)))
    return out


def ink_bbox(a: np.ndarray, thr: int = 200) -> tuple[int, int, int, int] | None:
    dark = a < thr
    rows = np.where(dark.sum(1) > 1)[0]
    cols = np.where(dark.sum(0) > 1)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    return int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())


def visual_material(meta: dict, box: tuple[int, int, int, int]) -> tuple[bool | None, list[str]]:
    """PDF 내부 이미지/벡터 정보 기반 시각 자료 판정 (휴리스틱)."""
    objs = meta["objects"]
    if objs.get("is_scanned"):
        return None, ["VISUAL_DETECTION_UNAVAILABLE_SCANNED_PAGE"]
    x0, y0, x1, y1 = box
    area = max(1, (x1 - x0) * (y1 - y0))

    def inter(b):
        ix = max(0, min(x1, b[2]) - max(x0, b[0])); iy = max(0, min(y1, b[3]) - max(y0, b[1]))
        return ix * iy

    reasons = []
    for im in objs.get("images", []):
        if im["page_cover"] < 0.8 and inter(im["bbox"]) > 0.01 * area:
            reasons.append("raster_image")
            break
    curves = lines = 0
    for d in objs.get("drawings", []):
        b = d["bbox"]
        if inter(b) <= 0:
            continue
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        curves += d["n_curve"]; lines += d["n_line"] + max(0, d["n_rect"] - 1)
    if curves >= 8:
        reasons.append(f"vector_curves={curves}")
    if lines >= 12:
        reasons.append(f"vector_lines={lines}")
    return bool(reasons), reasons


def load_answer_keys() -> dict:
    """reference/answer_keys/*.csv (exam_key,question_number,answer,source) — 공식 정답표만."""
    keys = {}
    if ANSWER_KEY_DIR.exists():
        for p in sorted(ANSWER_KEY_DIR.glob("*.csv")):
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    keys[(r["exam_key"], int(r["question_number"]))] = {
                        "value": int(r["answer"]), "source": r.get("source") or rel(p)}
    return keys


# --------------------------------------------------------------------------- main
def run_extract(inv: Inventory, cfg: dict, state, log, only_pages: set[int] | None = None,
                force: bool = False) -> dict:
    comp = inv.compilation
    comp_key, subject = comp["key"], comp["subject"]
    subj_cfg = cfg["subjects"][subject]
    pmap = {r["original_compilation_page"]: r for r in inv.page_map()}
    page_dirs = {p: page_dir(comp_key, p) for p in pmap
                 if (read_json(page_dir(comp_key, p) / "page.json") or {}).get("status") == "done"}
    missing_pages = sorted(set(pmap) - set(page_dirs))
    if missing_pages:
        log.log("ERROR", "extract", "PAGES_NOT_PROCESSED", f"페이지 캐시 없음: {missing_pages}",
                pages=missing_pages)

    exams = detect_exams(comp_key, subject, subj_cfg, page_dirs, log)
    anomalies = sequence_anomalies(exams)
    for a in anomalies:
        log.log("WARNING", "exams", a["code"], a["message"], exam=a.get("exam_key"))
    answer_keys = load_answer_keys()

    backup_dir = PROCESSED / "backups" / log.run_id
    Q_IMAGES.mkdir(parents=True, exist_ok=True); Q_JSON.mkdir(parents=True, exist_ok=True)

    all_records, issues = [], []
    id_seen: collections.Counter = collections.Counter()
    for ex in exams:
        pages = ex["pages"]
        if only_pages is not None and not (set(pages) & only_pages):
            continue
        # ---- 1) 시험 전체의 문항 시작 후보 (페이지 -> L -> R -> y 순)
        cands = []
        for p in pages:
            meta = read_json(page_dirs[p] / "page.json"); ocr = read_json(page_dirs[p] / "ocr.json")
            for col in meta["layout"]["columns"]:
                st, notes = question_starts(meta, ocr, col, page_dirs[p] / "page.png")
                for n in notes:
                    issues.append({"level": "WARNING", "code": n, "exam_key": ex["exam_key"], "page": p,
                                   "message": f"{col['side']} 단"})
                for s in st:
                    cands.append({**s, "page": p, "side": col["side"], "col": col})
        # ---- 2) 번호 순서 검증/보정 (OCR 유래 번호만 보정, 보정 시 review)
        for i, c in enumerate(cands):
            c["warnings"] = []
            prev_n = cands[i - 1]["num"] if i > 0 else 0
            prev_n = prev_n or 0
            next_n = cands[i + 1]["num"] if i + 1 < len(cands) else None
            if c["source"] != "ocr" or c["num"] == prev_n + 1:
                continue
            want = prev_n + 1
            alts = [v for v in (c.get("token_num"), c["num"]) if v is not None]
            if want in alts:
                c["warnings"].append(f"QNUM_OCR_CORRECTED: 숫자OCR '{c.get('digit_ocr')}' / 토큰 '{c.get('ocr_token')}' -> {want}")
            elif c["num"] is None:
                c["warnings"].append(f"QNUM_UNREADABLE_INFERRED: 판독 불가 '{c.get('ocr_token')}' -> {want} (앞 문항 기준)")
            elif (next_n is not None and next_n == prev_n + 2) or any(v % 10 == want % 10 for v in alts) \
                    or (next_n is None and want <= subj_cfg["expected_questions_per_exam"]):
                c["warnings"].append(f"QNUM_OCR_CORRECTED: OCR {c['num']} -> {want} (앞뒤 문항 순서 기준)")
            else:
                continue
            c["num"] = want
        nums = [c["num"] for c in cands]
        exp_n = subj_cfg["expected_questions_per_exam"]
        missing = sorted(set(range(1, exp_n + 1)) - set(nums))
        dups = sorted(n for n, k in collections.Counter(nums).items() if k > 1)
        extra = sorted(n for n in set(nums) if n < 1 or n > exp_n)
        ex["missing_numbers"], ex["duplicate_numbers"], ex["unexpected_numbers"] = missing, dups, extra
        ex["candidates"] = len(cands)
        for n in missing:
            issues.append({"level": "ERROR", "code": "QUESTION_NUMBER_MISSING", "exam_key": ex["exam_key"],
                           "message": f"{n}번 문항 시작을 찾지 못함 (인접 문항 영역에 포함되었을 수 있음)"})
        for n in dups:
            issues.append({"level": "ERROR", "code": "QUESTION_NUMBER_DUPLICATE", "exam_key": ex["exam_key"],
                           "message": f"{n}번이 {nums.count(n)}회 탐지됨"})
        non_monotonic = any(b <= a for a, b in zip(nums, nums[1:]))
        if non_monotonic:
            issues.append({"level": "ERROR", "code": "QUESTION_ORDER_ANOMALY", "exam_key": ex["exam_key"],
                           "message": f"탐지 순서 {nums}"})

        # ---- 3) 문항별 영역/이미지/텍스트
        by_col = collections.defaultdict(list)
        for c in cands:
            by_col[(c["page"], c["side"])].append(c)
        # 단 상단의 '번호 없는 내용' (이전 문항의 연속 가능성) 탐지
        orphan_regions = {}
        for p in pages:
            meta = read_json(page_dirs[p] / "page.json"); ocr = read_json(page_dirs[p] / "ocr.json")
            for col in meta["layout"]["columns"]:
                lst = sorted(by_col.get((p, col["side"]), []), key=lambda c: c["y"])
                first_y = lst[0]["y"] - TOP_PAD if lst else meta["layout"]["content_bottom"]
                lines = [l for l in column_lines(ocr, col["side"]) if l["y1"] < first_y]
                if len(lines) >= 2 and (first_y - meta["layout"]["content_top"]) > 120:
                    orphan_regions[(p, col["side"])] = (meta["layout"]["content_top"], first_y, lines)

        exam_outputs = []                           # 완전성 검사 후 일괄 저장
        covered = collections.defaultdict(list)    # (page, side) -> 문항 crop 이 덮은 y 구간
        excluded = collections.defaultdict(list)   # 의도적으로 제외한 구간 (확인 사항 상자)
        for idx, c in enumerate(cands):
            p, side, col = c["page"], c["side"], c["col"]
            meta = read_json(page_dirs[p] / "page.json"); ocr = read_json(page_dirs[p] / "ocr.json")
            lay = meta["layout"]
            lst = sorted(by_col[(p, side)], key=lambda k: k["y"])
            pos = [k["y"] for k in lst].index(c["y"])
            y_top = max(lay["content_top"], c["y"] - TOP_PAD)
            y_bot = lst[pos + 1]["y"] - TOP_PAD if pos + 1 < len(lst) else lay["content_bottom"]
            warnings = list(c["warnings"])
            # 영역 내 OCR 줄
            lines = [l for l in column_lines(ocr, side) if y_top - 5 <= (l["y0"] + l["y1"]) / 2 < y_bot]
            # 시험지 끝 '※ 확인 사항' 안내 상자는 문항이 아니므로 잘라낸다
            for k, l in enumerate(lines):
                t = l["text"].replace(" ", "")
                if "확인사항" in t or ("답안지" in t and "확인" in t):
                    y_bot = min(y_bot, l["y0"] - 30)
                    excluded[(p, side)].append((y_bot, lay["content_bottom"] + 400))
                    lines = lines[:k]
                    warnings.append("TRAILING_NOTICE_BOX_EXCLUDED")
                    break
            a = np.asarray(Image.open(page_dirs[p] / "page.png"))
            if pos + 1 == len(lst) and "TRAILING_NOTICE_BOX_EXCLUDED" not in warnings:
                # 단의 마지막 문항: 레이아웃 하단 추정이 마지막 줄을 자를 수 있어(스캔본 등) 공백 띠까지 확장
                from .pages import _footer_top
                if lay.get("method") == "divider_line":
                    lim = lay["content_bottom"] + 80
                else:
                    lim = _footer_top(a < 170, lay.get("divider_x", a.shape[1] // 2)) or lay["content_bottom"]
                y_ext = extend_to_gap(a, col, y_bot, limit_y=lim)
                if y_ext > y_bot:
                    warnings.append(f"BOTTOM_EXTENDED +{y_ext - y_bot}px")
                    y_bot = y_ext
            crop = a[y_top:y_bot, col["x0"]:col["x1"]]
            covered[(p, side)].append((y_top, y_bot))
            bb = ink_bbox(crop)
            parts = []
            if bb is None:
                warnings.append("CROP_EMPTY")
                img = crop
            else:
                bx0, by0, bx1, by1 = bb
                img = crop[:min(crop.shape[0], by1 + 16), :]
            parts.append(img)
            lines = shift_lines(lines, -col["x0"], -y_top)
            # 다음 단/페이지 상단의 번호 없는 내용이 이 문항의 연속일 가능성 → 이어 붙이고 검토 요청
            is_last_in_col = pos + 1 == len(lst)
            if is_last_in_col:
                # 이 단 이후 첫 '다음 단'
                seq = [(q, s) for q in pages for s in ("L", "R")]
                j = seq.index((p, side))
                if j + 1 < len(seq) and seq[j + 1] in orphan_regions:
                    oy0, oy1, olines = orphan_regions.pop(seq[j + 1])
                    oq, oside = seq[j + 1]
                    ometa = read_json(page_dirs[oq] / "page.json")
                    ocol = [cc for cc in ometa["layout"]["columns"] if cc["side"] == oside][0]
                    oa = np.asarray(Image.open(page_dirs[oq] / "page.png"))[oy0:oy1, ocol["x0"]:ocol["x1"]]
                    ob = ink_bbox(oa)
                    if ob:
                        off = sum(pt.shape[0] for pt in parts) + 20 * len(parts)
                        parts.append(oa[:ob[3] + 16, :])
                        covered[(oq, oside)].append((oy0, oy1))
                        lines = lines + shift_lines(olines, -ocol["x0"], -oy0 + off)
                        warnings.append(f"CONTINUATION_APPENDED_FROM p{oq}{oside} (번호 없는 단 상단 내용)")
            if len(parts) > 1:
                w = max(pt.shape[1] for pt in parts)
                canvas = np.full((sum(pt.shape[0] for pt in parts) + 20 * (len(parts) - 1), w), 255, np.uint8)
                y = 0
                for pt in parts:
                    canvas[y:y + pt.shape[0], :pt.shape[1]] = pt
                    y += pt.shape[0] + 20
                img = canvas
            edges = edge_ink(img)
            if edges:
                warnings.append(f"CROP_EDGE_INK {'/'.join(edges)} (잘림 가능성)")
            h_px, w_px = img.shape
            col_h = lay["content_bottom"] - lay["content_top"]
            if h_px < 180:
                warnings.append(f"CROP_TOO_SMALL h={h_px}px")
            if h_px > col_h * 1.02:
                warnings.append(f"CROP_TOO_LARGE h={h_px}px > column {col_h}px")

            parsed = parse_question_text(lines, img)
            warnings += parsed["warnings"]
            # 글자(한글·자모·영문)가 하나도 없는 선택지 텍스트 = OCR 불가(예: ㉠~㉤ 기호) → 사람 확인
            bad_ch = [c_["number"] for c_ in parsed["choices"]
                      if not re.search(r"[가-힣ㄱ-ㅎA-Za-z]", c_["text"] or "")]
            if bad_ch:
                warnings.append(f"CHOICE_TEXT_UNRELIABLE choices={bad_ch} (원문자 기호 등 OCR 불가, 이미지 확인)")
            if len(parsed["choices"]) > 5:
                warnings.append("POSSIBLE_MERGED_QUESTIONS (선택지 5개 초과)")
            if not parsed["stem"]:
                warnings.append("STEM_EMPTY")
            if len(parsed["raw_text"]) < 20:
                warnings.append("TEXT_EXTRACTION_FAILED")
            vis, vis_reasons = visual_material(meta, (col["x0"], y_top, col["x1"], y_bot))
            if vis is None:
                warnings.append("VISUAL_DETECTION_UNAVAILABLE_SCANNED_PAGE")
            for n in ex["missing_numbers"]:
                if idx + 1 < len(cands) and c["num"] < n < cands[idx + 1]["num"]:
                    warnings.append(f"MAY_CONTAIN_MISSING_Q{n}")
                if idx + 1 == len(cands) and n > c["num"]:
                    warnings.append(f"MAY_CONTAIN_MISSING_Q{n}")
            warnings += [f"EXAM:{w}" for w in ex["warnings"]]

            # ---- ID
            if ex["academic_year"] and ex["exam_type"]:
                base_id = f"{ex['academic_year']}_{ex['exam_type']}_{subject}_Q{c['num']:02d}"
            else:
                base_id = f"{ex['exam_key']}_Q{c['num']:02d}"
            id_seen[base_id] += 1
            qid = base_id
            if id_seen[base_id] > 1:
                qid = f"{base_id}__DUP{id_seen[base_id]}"
                warnings.append(f"ID_COLLISION with {base_id}")
                issues.append({"level": "ERROR", "code": "ID_COLLISION", "exam_key": ex["exam_key"],
                               "question_id": qid, "message": f"{base_id} 충돌 → {qid} 로 별도 보존"})

            # ---- confidence
            confs = [w["conf"] for l in lines for w in l["words"] if w["conf"] >= 0]
            conf = (np.mean(confs) / 100.0) if confs else 0.0
            if c["source"] == "ocr":
                conf *= 0.9
            if any(w.startswith("QNUM_OCR_CORRECTED") for w in warnings):
                conf *= 0.6
            if len(parsed["choices"]) != 5:
                conf *= 0.7
            if "STEM_END_NOT_FOUND" in parsed["warnings"]:
                conf *= 0.8
            conf = round(float(conf), 3)

            # 정보성 경고(판정 불가 표시, 안내상자 제외, 보조 OCR 사용)는 단독으로 검토 대상을 만들지 않는다
            blocking = [w for w in warnings if not (
                w.startswith("VISUAL_DETECTION_UNAVAILABLE") or w == "TRAILING_NOTICE_BOX_EXCLUDED"
                or re.match(r"CHOICE\d_TEXT_FROM_CELL_OCR", w))]
            review = bool(blocking) or conf < 0.55 or bool(ex["review_required"])

            src = pmap[p]
            ans = answer_keys.get((ex["exam_key"], c["num"]))
            rec = {
                "id": qid,
                "source": {
                    "academic_year": ex["academic_year"], "exam_year": ex.get("exam_year"),
                    "exam_type": ex["exam_type"], "subject": subject,
                    "original_compilation": comp["name"],
                    "split_pdf": src["split_pdf"], "split_pdf_page": src["split_pdf_page"],
                    "original_compilation_page": p,
                    "question_number": c["num"],
                    "exam_key": ex["exam_key"],
                    "column": side,
                    "bbox_px": [col["x0"], y_top, col["x1"], y_bot],
                    "render_dpi": RENDER_DPI,
                    "split_pdf_sha256": src["split_sha256"],
                },
                "original": {
                    "question_image": f"data/questions/images/{qid}.png",
                    "raw_text": parsed["raw_text"], "stem": parsed["stem"], "passage": parsed["passage"],
                    "choices": parsed["choices"], "has_visual_material": vis,
                    "visual_material_evidence": vis_reasons, "points": parsed["points"],
                },
                "answer": {"value": ans["value"] if ans else None, "source": ans["source"] if ans else None,
                           "verified": bool(ans)},
                "extraction": {
                    "confidence": conf, "review_required": review, "warnings": warnings,
                    "text_source": "tesseract_ocr_kor (PDF embedded text 는 폰트 인코딩 손상으로 한글 복원 불가)",
                    "question_number_source": c["source"],
                    "choice_method": parsed.get("choice_method"),
                    "choice_numbering": "선택지 번호는 표지(①~⑤) 위치의 읽기 순서 기준",
                    "pipeline_version": PIPELINE_VERSION, "run_id": log.run_id, "extracted_at": now_iso(),
                },
            }
            exam_outputs.append((rec, img))
            all_records.append(rec)

        # ---- 4) 완전성 검사: 본문 영역 잉크 중 어떤 문항에도 속하지 않은 부분
        for p in pages:
            meta = read_json(page_dirs[p] / "page.json")
            lay = meta["layout"]
            a = np.asarray(Image.open(page_dirs[p] / "page.png"))
            for col in lay["columns"]:
                for y0u, y1u in unassigned_ink(a, col, lay, covered[(p, col["side"])], excluded[(p, col["side"])]):
                    issues.append({"level": "ERROR", "code": "UNASSIGNED_INK_REGION", "exam_key": ex["exam_key"],
                                   "page": p, "message": f"p{p}{col['side']} y={y0u}-{y1u}px 잉크가 어떤 문항 crop 에도 포함되지 않음 (잘림/누락 가능성)"})
                    # 미할당 잉크 바로 위에서 끝나는 문항(없으면 그 단의 첫 문항)에 경고 → 검토 대상
                    same_col = [r_ for r_, _ in exam_outputs if r_["source"]["original_compilation_page"] == p
                                and r_["source"]["column"] == col["side"]]
                    above = [r_ for r_ in same_col if r_["source"]["bbox_px"][3] <= y0u + 5]
                    tgt = max(above, key=lambda r_: r_["source"]["bbox_px"][3]) if above else (same_col[0] if same_col else None)
                    if tgt is not None:
                        tgt["extraction"]["warnings"].append(f"UNASSIGNED_INK_NEAR y={y0u}-{y1u}px (잘림/누락 가능성)")
                        tgt["extraction"]["review_required"] = True
        for rec_, img_ in exam_outputs:
            _write_outputs(rec_, img_, force, backup_dir, log)
        # 남은 orphan (이전 문항 없음)
        for (p, side), (oy0, oy1, olines) in orphan_regions.items():
            issues.append({"level": "ERROR", "code": "ORPHAN_CONTENT", "exam_key": ex["exam_key"], "page": p,
                           "message": f"p{p}{side} 상단에 번호 없는 내용 {len(olines)}줄 (연결할 이전 문항 없음)"})

    for it in issues:
        log.log(it["level"], "extract", it["code"], it["message"], exam=it.get("exam_key"),
                page=it.get("page"), question_id=it.get("question_id"))
    summary = {"exams": exams, "anomalies": anomalies, "issues": issues,
               "question_ids": [r["id"] for r in all_records], "run_id": log.run_id}
    write_json_atomic(PROCESSED / f"extract_summary_{comp_key}.json",
                      {**summary, "exams": [{k: v for k, v in e.items() if k != "title"} | {"title_texts": e["title"]["texts"] if e.get("title") else None} for e in exams]})
    state.mark_stage(f"extract:{comp_key}", "done", questions=len(all_records), run_id=log.run_id)
    print(f"[extract] exams={len(exams)} questions={len(all_records)} issues={len(issues)}")
    return summary


def save_question_png(img: np.ndarray, path: Path) -> None:
    """300dpi 회색조 → 16단계 회색 팔레트 PNG (글자 선명도 유지, 용량 약 40% 절감)."""
    g = (img.astype(np.uint16) * 15 // 255 * 17).astype(np.uint8)
    im = Image.fromarray(g).convert("L")
    pal = im.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal.save(path, optimize=True, bits=4, dpi=(RENDER_DPI, RENDER_DPI))


def _stable(rec: dict) -> dict:
    r = json.loads(json.dumps(rec))
    # QA 단계가 추가하는 필드(qa_flags 등)와 실행 메타데이터는 비교에서 제외
    for k in ("run_id", "extracted_at", "qa_flags", "duplicate_status", "review_required"):
        r["extraction"].pop(k, None)
    return r


def _write_outputs(rec: dict, img: np.ndarray, force: bool, backup_dir: Path, log) -> None:
    qid = rec["id"]
    jpath = Q_JSON / f"{qid}.json"
    ipath = Q_IMAGES / f"{qid}.png"
    old = read_json(jpath)
    tmp_img = ipath.with_suffix(".tmp.png")
    save_question_png(img, tmp_img)
    if old is not None:
        same_json = _stable(old) == _stable(rec)
        same_img = ipath.exists() and sha256_file(ipath) == sha256_file(tmp_img)
        if same_json and same_img:
            tmp_img.unlink()
            return
        if not force:
            # 기존 데이터 자동 덮어쓰기 금지 → 새 결과는 pending 으로 보관
            pend = PROCESSED / "pending" / log.run_id
            pend.mkdir(parents=True, exist_ok=True)
            shutil.move(tmp_img, pend / f"{qid}.png")
            write_json_atomic(pend / f"{qid}.json", rec)
            log.log("WARNING", "extract", "OUTPUT_EXISTS_NOT_OVERWRITTEN",
                    f"{qid}: 기존 산출물과 다름 → {rel(pend)} 에 보관 (--force 로 교체)", question_id=qid)
            return
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(jpath, backup_dir / jpath.name)
        if ipath.exists():
            shutil.copy2(ipath, backup_dir / ipath.name)
    tmp_img.replace(ipath)
    write_json_atomic(jpath, rec)
