import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from csatdb import common, state as state_mod  # noqa: E402
from csatdb.inventory import original_page, discover  # noqa: E402
from csatdb.exams import parse_title, sequence_anomalies  # noqa: E402
from csatdb.parse import detect_rings, choose_markers_by_size, parse_question_text, split_choices  # noqa: E402
from csatdb.db import SCHEMA  # noqa: E402


# ------------------------------------------------------------------ 페이지 매핑
def test_original_page_mapping_example():
    # 생윤기출_03_p041-060.pdf 의 7페이지 -> 원본 47페이지
    assert original_page(41, 7) == 47
    assert original_page(1, 1) == 1
    with pytest.raises(ValueError):
        original_page(1, 0)


def test_inventory_real_data_is_continuous():
    cfg = common.load_config()
    inv = discover(cfg["compilations"][0])
    if not inv.files:
        pytest.skip("raw PDF 없음")
    assert inv.problems == []
    assert inv.total_pages == 160
    pm = inv.page_map()
    assert [r["original_compilation_page"] for r in pm] == list(range(1, 161))
    f3 = [r for r in pm if r["split_pdf"].startswith("생윤기출_03") and r["split_pdf_page"] == 7][0]
    assert f3["original_compilation_page"] == 47


# ------------------------------------------------------------------ 시험 제목 파싱
def _v(texts):
    return [{"lang": "kor" if i % 2 == 0 else "eng", "text": t, "factor": 1} for i, t in enumerate(texts)]


def test_parse_title_june():
    r = parse_title(_v(["2023학년도 대학수학능력시험 6월 모의평가 문제지", "2023StH 62SOB",
                        "2028학년도 대학수학능력시험 6월 모의평가 문제지", "2023SH",
                        "2023학년도 대학수학능력시험 6월 모의평가 문제지", "2023SH"]))
    assert r["academic_year"] == 2023 and r["exam_type"] == "JUNE"


def test_parse_title_csat_and_uncertain_year():
    r = parse_title(_v(["2020학년도 대학수학능력시험 문제지", "2020x", "2020학년도 대학수학능력시험 문제지", "2020y",
                        "2020학년도 대학수학능력시험 문제지", "2020z"]))
    assert r["exam_type"] == "CSAT" and r["academic_year"] == 2020
    r2 = parse_title(_v(["2016학년도 대학수학능력시험 문제지", "2010", "2019학년도 대학수학능력시험 문제지", "2011",
                         "2018학년도", "2012"]))
    assert r2["academic_year"] is None  # 합의 없음 -> 추측하지 않음
    assert any("YEAR_UNCERTAIN" in w for w in r2["warnings"])


def test_sequence_anomalies():
    ex = [{"exam_key": "2014_JUNE", "academic_year": 2014, "exam_type": "JUNE", "pages": [1]},
          {"exam_key": "2014_CSAT", "academic_year": 2014, "exam_type": "CSAT", "pages": [5]},
          {"exam_key": "2014_SEPTEMBER", "academic_year": 2014, "exam_type": "SEPTEMBER", "pages": [9]}]
    a = sequence_anomalies(ex)
    assert any(x["code"] == "EXAM_ORDER_NOT_CHRONOLOGICAL" for x in a)
    assert not any(x["code"] == "EXAM_SLOT_NOT_FOUND" for x in a)
    a2 = sequence_anomalies(ex[:2])
    assert any(x["code"] == "EXAM_SLOT_NOT_FOUND" and "SEPTEMBER" in x["message"] for x in a2)


# ------------------------------------------------------------------ 선택지 표지(원문자) 검출
def _circled_image(layout="vertical"):
    img = Image.new("L", (1200, 420), 255)
    d = ImageDraw.Draw(img)
    pos = [(50, 20 + 70 * i) for i in range(5)] if layout == "vertical" else \
          [(50, 20), (450, 20), (850, 20), (50, 100), (450, 100)]
    for k, (x, y) in enumerate(pos):
        d.ellipse((x, y, x + 45, y + 45), outline=0, width=3)
        d.line((x + 22, y + 10, x + 22, y + 35), fill=0, width=4)       # 숫자 획
        d.rectangle((x + 70, y + 12, x + 300, y + 30), fill=0)            # 본문
    d.ellipse((600, 350, 645, 395), outline=0, width=3)                  # 속이 빈 원(○) → 제외돼야 함
    return np.asarray(img)


@pytest.mark.parametrize("layout,how", [("vertical", "vertical"), ("horizontal", "horizontal_2rows")])
def test_ring_markers(layout, how):
    a = _circled_image(layout)
    rings = detect_rings(a, 45)
    m, got = choose_markers_by_size(rings)
    assert m is not None and len(m) == 5 and got == how


def _line(y, words):
    ws = [{"text": t, "x": x, "y": y, "w": 40, "h": 45, "conf": 90.0} for x, t in words]
    return {"x0": ws[0]["x"], "y0": y, "x1": ws[-1]["x"] + 40, "y1": y + 45,
            "text": " ".join(t for _, t in words), "words": ws, "conf": 90.0}


def test_parse_question_text_ocr_fallback():
    lines = [_line(0, [(0, "3."), (60, "다음"), (120, "것은?")]),
             _line(70, [(60, "제시문")]),
             _line(140, [(40, "(1)"), (100, "가")]),
             _line(210, [(40, "(2)"), (100, "나")]),
             _line(280, [(40, "(3)"), (100, "다")]),
             _line(350, [(40, "@)"), (100, "라")]),
             _line(420, [(40, "어스"), (100, "마")])]   # ⑤ 가 한글로 오인식돼도 표지 열 정렬로 인정
    r = parse_question_text(lines)
    assert r["stem"] == "다음 것은?"
    assert r["passage"] == "제시문"
    assert [c["text"] for c in r["choices"]] == ["가", "나", "다", "라", "마"]
    assert "CHOICES_COUNT_5" not in r["warnings"]


def test_split_choices_horizontal_prefers_five():
    lines = [_line(0, [(40, "(1)"), (100, "ㄱ,ㄴ"), (400, "(2)"), (460, "ㄱ,ㄷ"), (800, "(3)"), (860, "ㄴ,ㄷ")]),
             _line(70, [(40, "(4)"), (100, "ㄴ,ㄹ"), (400, "(5)"), (460, "ㄷ,ㄹ")])]
    assert split_choices(lines, 40) == ["ㄱ,ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄴ,ㄹ", "ㄷ,ㄹ"]


# ------------------------------------------------------------------ DB 스키마 / checkpoint
def test_schema_foreign_keys():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO subjects VALUES ('LIFE_ETHICS','생활과 윤리',20)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("""INSERT INTO questions (id, exam_key, subject, question_image, json_path, json_sha256)
                       VALUES ('x','NO_SUCH_EXAM','LIFE_ETHICS','a','b','c')""")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"exams", "questions", "choices", "assets", "extraction_logs",
            "question_annotations", "annotation_fields", "reviews"} <= tables


def test_state_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    st = state_mod.State()
    assert not st.file_done("a.pdf", "abc")
    st.mark_file("a.pdf", "abc", "done")
    st2 = state_mod.State()             # 재시작 후에도 유지
    assert st2.file_done("a.pdf", "abc")
    assert not st2.file_done("a.pdf", "changed-sha")   # 파일이 바뀌면 재처리 대상
    st2.mark_file("a.pdf", "changed-sha", "partial")
    assert json.loads((tmp_path / "state.json").read_text())["files"]["a.pdf"]["previous_sha256"] == ["abc"]


# ------------------------------------------------------------------ crop 완전성 QA
from csatdb.extract import unassigned_ink, extend_to_gap, edge_ink  # noqa: E402


def _page_with_blocks(blocks, H=1000, W=600):
    a = np.full((H, W), 255, np.uint8)
    for y0, y1 in blocks:
        a[y0:y1, 100:500] = 0
    return a


def test_unassigned_ink_detects_uncovered_block():
    a = _page_with_blocks([(100, 200), (600, 700)])
    col = {"x0": 50, "x1": 550}
    lay = {"content_top": 50, "content_bottom": 950}
    assert unassigned_ink(a, col, lay, covered=[(50, 400)], excluded=[]) == [(600, 699)]
    assert unassigned_ink(a, col, lay, covered=[(50, 400), (400, 950)], excluded=[]) == []
    assert unassigned_ink(a, col, lay, covered=[(50, 400)], excluded=[(550, 950)]) == []


def test_extend_to_gap_and_edge_ink():
    a = _page_with_blocks([(100, 300)])
    col = {"x0": 50, "x1": 550}
    y = extend_to_gap(a, col, 250)          # 잉크 중간에서 끊긴 경계 → 공백까지 확장
    assert 300 <= y <= 320
    assert extend_to_gap(a, col, 400) == 400  # 이미 공백
    assert edge_ink(a[100:250, 50:550]) == ["bottom", "top"]
    assert edge_ink(a[80:y, 50:550]) == []
