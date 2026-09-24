"""페이지 단위 처리: 렌더링 -> (스캔본) 기울기 보정 -> 2단 레이아웃 탐지 -> OCR.

결과는 data/processed/pages/<compilation>/p###/ 에 캐시되며,
원본 분할 PDF 의 SHA-256 이 같으면 재사용된다 (checkpoint/resume).
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from .common import RENDER_DPI, read_json, write_json_atomic, PIPELINE_VERSION

TESS_LANG = "kor"
PAGE_CACHE_VERSION = f"{PIPELINE_VERSION}-page6"


# --------------------------------------------------------------------------- render
def render_gray(page: pymupdf.Page, dpi: int = RENDER_DPI) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def page_objects(page: pymupdf.Page, scale: float) -> dict:
    """PDF 내부 이미지/벡터 정보 (픽셀 좌표). 시각 자료 판정에 사용."""
    prect = page.rect
    images = []
    for info in page.get_images(full=True):
        try:
            rects = page.get_image_rects(info[0])
        except Exception:
            rects = []
        for r in rects:
            cover = (r & prect).get_area() / prect.get_area() if prect.get_area() else 0
            images.append({"xref": info[0], "bbox": [r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale],
                           "page_cover": round(cover, 4), "w": info[2], "h": info[3]})
    drawings = []
    try:
        for d in page.get_drawings():
            r = d["rect"]
            kinds = [it[0] for it in d["items"]]
            drawings.append({"bbox": [r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale],
                             "n_curve": kinds.count("c"), "n_line": kinds.count("l"),
                             "n_rect": kinds.count("re") + kinds.count("qu"),
                             "fill": d.get("fill") is not None})
    except Exception:
        pass
    text_chars = len(page.get_text().strip())
    qnums = embedded_question_numbers(page, scale)
    scanned = any(im["page_cover"] > 0.8 for im in images) and len(drawings) < 5
    return {"images": images, "drawings": drawings, "embedded_text_chars": text_chars,
            "is_scanned": scanned, "embedded_qnums": qnums}


# 이 PDF 는 폰트 ToUnicode 가 깨져 한글은 복원 불가하지만, 숫자/마침표는
# (1) 그대로 또는 (2) 0x1F 만큼 밀린 코드(\x12\x0f == "1.")로 들어 있다.
_QNUM_RX = __import__("re").compile(r"^(\d{1,2})\.")


def _decode_variants(t: str) -> list[tuple[str, str]]:
    shifted = "".join(chr(ord(c) + 0x1F) if 0x0F <= ord(c) <= 0x1A else c for c in t)
    return [("identity", t), ("shift_0x1F", shifted)]


def embedded_question_numbers(page: pymupdf.Page, scale: float) -> list[dict]:
    out = []
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return out
    for b in raw.get("blocks", []):
        for l in b.get("lines", []):
            for sp in l.get("spans", []):
                t = "".join(c["c"] for c in sp.get("chars", [])).strip()
                if not t:
                    continue
                for how, dt in _decode_variants(t):
                    m = _QNUM_RX.match(dt)
                    if m:
                        x0, y0, x1, y1 = sp["bbox"]
                        out.append({"num": int(m.group(1)), "decode": how,
                                    "bbox": [x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                                    "size": round(sp.get("size", 0), 2)})
                        break
    return out


# --------------------------------------------------------------------------- layout
def _runs(ys: np.ndarray, max_gap: int) -> list[tuple[int, int]]:
    if len(ys) == 0:
        return []
    runs, start, prev = [], int(ys[0]), int(ys[0])
    for y in ys[1:]:
        y = int(y)
        if y - prev > max_gap:
            runs.append((start, prev))
            start = y
        prev = y
    runs.append((start, prev))
    return runs


def estimate_skew(a: np.ndarray) -> float:
    """projection-profile 방식 기울기 추정(도). 스캔 페이지에만 사용."""
    small = Image.fromarray(a).reduce(4)
    b = (np.asarray(small) < 160).astype(np.uint8) * 255
    im = Image.fromarray(b)
    best, best_angle = -1.0, 0.0
    for ang in np.arange(-2.0, 2.0001, 0.05):
        r = np.asarray(im.rotate(float(ang), resample=Image.NEAREST, fillcolor=0), dtype=np.float32)
        prof = r.sum(1)
        score = float(np.sum(np.diff(prof) ** 2))
        if score > best:
            best, best_angle = score, float(ang)
    # PIL rotate(+ang) 가 가장 곧게 만드는 각도 -> 페이지 기울기는 -ang
    return -best_angle


def deskew(a: np.ndarray, skew_deg: float) -> np.ndarray:
    im = Image.fromarray(a)
    im = im.rotate(-skew_deg, resample=Image.BICUBIC, expand=False, fillcolor=255)
    return np.asarray(im).copy()


def _find_divider(dark: np.ndarray):
    H, W = dark.shape
    cs = dark[int(H * .15):int(H * .95), int(W * .4):int(W * .6)].sum(0)
    xdiv = int(W * .4) + int(cs.argmax())
    colmask = dark[:, max(0, xdiv - 8):xdiv + 9].any(1)
    runs = _runs(np.where(colmask)[0], max_gap=25)
    if not runs:
        return xdiv, None
    top, bottom = max(runs, key=lambda r: r[1] - r[0])
    if (bottom - top) / H < 0.45:
        return xdiv, None
    return xdiv, (int(top), int(bottom))


def _find_gutter(dark: np.ndarray) -> int:
    H, W = dark.shape
    cs = dark[int(H * .2):int(H * .9), :].sum(0).astype(float)
    lo, hi = int(W * .4), int(W * .6)
    seg = cs[lo:hi]
    empty = seg <= max(2.0, np.percentile(seg, 5))
    best, cur, best_rng, st = 0, 0, (lo, lo), 0
    for i, e in enumerate(empty):
        if e:
            if cur == 0:
                st = i
            cur += 1
            if cur > best:
                best, best_rng = cur, (lo + st, lo + i)
        else:
            cur = 0
    return int((best_rng[0] + best_rng[1]) / 2)


def _header_rule_bottom(dark: np.ndarray) -> int | None:
    """상단 25% 안에서 가장 아래쪽의 긴 가로선(머리글 구분선) y."""
    H, W = dark.shape
    rows = dark[: int(H * .25), :].sum(1)
    ys = np.where(rows > W * 0.5)[0]
    return int(ys.max()) + 8 if len(ys) else None


def _footer_top(dark: np.ndarray, xmid: int) -> int | None:
    """하단 꼬리말(쪽번호 상자·저작권 문구) 바로 위 y.

    1순위: 페이지 맨 아래의 짧은 전폭 잉크 덩어리(꼬리말) 시작점.
    2순위: 가운데 쪽번호 상자 탐지 (스캔본은 여백에 구분선 조각이 남아 오판할 수 있음)."""
    H, W = dark.shape
    rows = dark[:, int(W * .03):int(W * .97)].sum(1)
    segs, st = [], None
    for y in range(int(H * .75), H):
        if rows[y] > 3 and st is None:
            st = y
        if rows[y] <= 3 and st is not None:
            if y - st > 3:
                segs.append((st, y))
            st = None
    if st is not None and H - st > 3:
        segs.append((st, H))
    if segs:
        s0, s1 = segs[-1]
        if s0 > H * .88 and (s1 - s0) < H * .04 and len(segs) >= 2:
            return s0 - 10
    y0 = int(H * .85)
    band = dark[y0:, xmid - int(W * .04): xmid + int(W * .04)].sum(1)
    ys = np.where(band > 3)[0]
    return y0 + int(ys.min()) - 10 if len(ys) else None


def _extend_bottom(dark: np.ndarray, y0: int, blank_rows: int = 20, search: int = 250) -> int | None:
    H, W = dark.shape
    rows = dark[:, int(W * .03):int(W * .97)].sum(1)
    run = 0
    for y in range(y0, min(H, y0 + search)):
        if rows[y] == 0:
            run += 1
            if run >= blank_rows:
                return y - blank_rows + 4
        else:
            run = 0
    return None


def detect_layout(a: np.ndarray) -> dict:
    H, W = a.shape
    dark = a < 170
    warnings = []
    xdiv, run = _find_divider(dark)
    if run:
        top, bottom = run
        method = "divider_line"
        # 구분선이 마지막 줄보다 먼저 끝나는 경우가 있어, 구분선 끝 이후 첫 '전폭 공백 띠'까지 확장
        ext = _extend_bottom(dark, bottom)
        if ext is None:
            warnings.append("BOTTOM_EXTENSION_NOT_FOUND")
        else:
            bottom = ext
    else:
        xdiv = _find_gutter(dark)
        top = _header_rule_bottom(dark)
        bottom = _footer_top(dark, xdiv)
        method = "gutter_fallback"
        warnings.append("DIVIDER_NOT_FOUND_GUTTER_FALLBACK")
        if top is None:
            top = int(H * .12); warnings.append("HEADER_RULE_NOT_FOUND")
        if bottom is None:
            bottom = int(H * .92); warnings.append("FOOTER_NOT_FOUND")
    band = dark[top:bottom, :].copy()
    band[:, max(0, xdiv - 8):xdiv + 9] = False
    xs = np.where(band.sum(0) > 2)[0]
    left = int(xs.min()) if len(xs) else int(W * .05)
    right_ink = int(xs.max()) if len(xs) else int(W * .95)
    right = min(right_ink, 2 * xdiv - left + int(W * 0.03))
    gap = 12
    return {
        "ok": True, "method": method, "W": W, "H": H, "divider_x": int(xdiv),
        "content_top": int(top), "content_bottom": int(bottom),
        "columns": [
            {"side": "L", "x0": max(0, left - 6), "x1": int(xdiv) - gap},
            {"side": "R", "x0": int(xdiv) + gap, "x1": min(W, right + 6)},
        ],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- OCR
def tesseract_tsv(img: Image.Image, psm: int, lang: str = TESS_LANG, pad: int = 40,
                  extra: list[str] | None = None) -> list[dict]:
    """tesseract TSV. 가장자리 글자 탈락을 막기 위해 흰 여백(pad)을 덧대고 좌표는 원래대로 되돌린다."""
    if pad:
        canvas = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 255)
        canvas.paste(img.convert("L"), (pad, pad))
        img = canvas
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    p = subprocess.run(["tesseract", "stdin", "stdout", "-l", lang, "--psm", str(psm), *(extra or []), "tsv"],
                       input=buf.getvalue(), capture_output=True, env=env, check=True)
    rows = []
    reader = csv.DictReader(io.StringIO(p.stdout.decode("utf-8")), delimiter="\t",
                            quoting=csv.QUOTE_NONE)
    for r in reader:
        if r.get("level") != "5":
            continue
        t = (r.get("text") or "").strip()
        if not t:
            continue
        rows.append({"block": int(r["block_num"]), "par": int(r["par_num"]), "line": int(r["line_num"]),
                     "word": int(r["word_num"]), "x": int(r["left"]) - pad, "y": int(r["top"]) - pad,
                     "w": int(r["width"]), "h": int(r["height"]), "conf": float(r["conf"]), "text": t})
    return rows


def group_lines(words: list[dict]) -> list[dict]:
    """tesseract (block,par,line) 단위로 줄을 구성."""
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    out = []
    for key, ws in lines.items():
        ws.sort(key=lambda w: w["x"])
        x0 = min(w["x"] for w in ws); y0 = min(w["y"] for w in ws)
        x1 = max(w["x"] + w["w"] for w in ws); y1 = max(w["y"] + w["h"] for w in ws)
        out.append({"key": list(key), "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "text": " ".join(w["text"] for w in ws), "words": ws,
                    "conf": float(np.mean([w["conf"] for w in ws]))})
    out.sort(key=lambda l: (l["y0"], l["x0"]))
    return out


def header_ocr_variants(head: np.ndarray) -> list[dict]:
    """머리글은 글자가 커서 원본 해상도 OCR 이 실패한다 -> 여러 배율/PSM 결과를 모두 보존(투표용)."""
    out = []
    for factor in (2, 3, 4):
        im = Image.fromarray(head).reduce(factor)
        for psm in (6, 11):
            words = tesseract_tsv(im, psm=psm)
            out.append({"factor": factor, "psm": psm,
                        "text": " ".join(w["text"] for w in words),
                        "words": [{**w, "x": w["x"] * factor, "y": w["y"] * factor,
                                   "w": w["w"] * factor, "h": w["h"] * factor} for w in words]})
    return out


# --------------------------------------------------------------------------- page driver
def process_page(pdf_path: Path, page_index: int, out_dir: Path, source_sha: str, force: bool = False) -> dict:
    meta_path = out_dir / "page.json"
    meta = read_json(meta_path)
    if (not force and meta and meta.get("source_sha256") == source_sha
            and meta.get("cache_version") == PAGE_CACHE_VERSION and meta.get("status") == "done"
            and (out_dir / "page.png").exists()):
        return {**meta, "_cached": True}

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    page = doc[page_index]
    scale = RENDER_DPI / 72.0
    objs = page_objects(page, scale)
    a = render_gray(page)
    doc.close()

    angle = estimate_skew(a) if objs["is_scanned"] else 0.0
    deskewed = False
    if abs(angle) >= 0.1:
        a = deskew(a, angle)
        deskewed = True
    layout = detect_layout(a)
    Image.fromarray(a).save(out_dir / "page.png", optimize=False)

    ocr = {"header": [], "columns": {}}
    if layout["ok"]:
        top = layout["content_top"]
        ocr["header"] = header_ocr_variants(a[: max(top, 50), :])
        foot_img = Image.fromarray(a[layout["content_bottom"]:, :])
        ocr["footer"] = tesseract_tsv(foot_img, psm=3)
        for col in layout["columns"]:
            crop = a[top:layout["content_bottom"], col["x0"]:col["x1"]]
            words = tesseract_tsv(Image.fromarray(crop), psm=4)
            for w in words:   # 페이지 좌표로 변환
                w["x"] += col["x0"]; w["y"] += top
            ocr["columns"][col["side"]] = words
    else:
        ocr["header"] = header_ocr_variants(a[: int(a.shape[0] * 0.2), :])

    write_json_atomic(out_dir / "ocr.json", ocr)
    meta = {
        "cache_version": PAGE_CACHE_VERSION, "source_sha256": source_sha,
        "page_index": page_index, "dpi": RENDER_DPI, "skew_deg": round(angle, 4),
        "deskewed": deskewed, "layout": layout, "objects": objs, "status": "done",
    }
    write_json_atomic(meta_path, meta)
    return meta
