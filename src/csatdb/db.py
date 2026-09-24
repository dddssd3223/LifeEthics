"""SQLite DB (data/database/questions.db).

- DB 는 JSON(원천 구조화 결과) + 페이지 캐시 + 로그로부터 재생성되는 파생물이다.
- 사람 검수 결과(reviews)는 data/reviews/reviews.jsonl 에 append-only 로 보존되고
  DB 재생성 시 다시 적재되므로 유실되지 않는다.
- Phase 2 분석 필드는 annotation_fields / question_annotations (EAV) 로 스키마 변경 없이 확장.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .common import DB_DIR, DB_PATH, Q_JSON, REVIEWS, ROOT, sha256_file, read_json, now_iso

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE subjects (
    code TEXT PRIMARY KEY,
    name_ko TEXT NOT NULL,
    expected_questions_per_exam INTEGER
);

CREATE TABLE compilations (
    key TEXT PRIMARY KEY,
    subject TEXT NOT NULL REFERENCES subjects(code),
    name TEXT NOT NULL,
    total_pages INTEGER
);

CREATE TABLE source_files (
    id INTEGER PRIMARY KEY,
    compilation_key TEXT NOT NULL REFERENCES compilations(key),
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    split_index INTEGER,
    original_page_start INTEGER NOT NULL,
    original_page_end INTEGER NOT NULL,
    UNIQUE (compilation_key, filename)
);

CREATE TABLE exams (
    exam_key TEXT PRIMARY KEY,                 -- 예: 2024_SEPTEMBER_LIFE_ETHICS
    subject TEXT NOT NULL REFERENCES subjects(code),
    compilation_key TEXT REFERENCES compilations(key),
    academic_year INTEGER,                     -- 학년도
    exam_year INTEGER,                         -- 시행 연도
    exam_type TEXT CHECK (exam_type IN ('JUNE','SEPTEMBER','CSAT') OR exam_type IS NULL),
    first_original_page INTEGER,
    last_original_page INTEGER,
    page_count INTEGER,
    expected_question_count INTEGER,
    title_confidence REAL,
    title_ocr_texts TEXT,                      -- JSON
    review_required INTEGER NOT NULL DEFAULT 0,
    warnings TEXT                              -- JSON array
);

CREATE TABLE pages (
    compilation_key TEXT NOT NULL REFERENCES compilations(key),
    original_page INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_files(id),
    split_pdf_page INTEGER NOT NULL,
    exam_key TEXT REFERENCES exams(exam_key),
    is_scanned INTEGER,
    layout_method TEXT,
    layout_json TEXT,
    PRIMARY KEY (compilation_key, original_page)
);

CREATE TABLE questions (
    id TEXT PRIMARY KEY,                       -- {academic_year}_{exam_type}_{subject}_Q{nn}
    exam_key TEXT NOT NULL REFERENCES exams(exam_key),
    subject TEXT NOT NULL REFERENCES subjects(code),
    question_number INTEGER,
    split_pdf TEXT,
    split_pdf_page INTEGER,
    original_compilation_page INTEGER,
    column_side TEXT,
    bbox_px TEXT,
    question_image TEXT NOT NULL,
    raw_text TEXT,
    stem TEXT,
    passage TEXT,
    has_visual_material INTEGER,               -- 1/0/NULL(판정 불가)
    points INTEGER,
    answer_value INTEGER,
    answer_source TEXT,
    answer_verified INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    review_required INTEGER NOT NULL DEFAULT 0,
    warnings TEXT,                             -- JSON array
    question_number_source TEXT,
    choice_method TEXT,
    json_path TEXT NOT NULL,
    json_sha256 TEXT NOT NULL,
    duplicate_status TEXT,                     -- NULL | duplicate_candidate
    pipeline_version TEXT,
    run_id TEXT
);
CREATE INDEX idx_questions_exam ON questions(exam_key, question_number);

CREATE TABLE choices (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    choice_number INTEGER NOT NULL,
    text TEXT,
    PRIMARY KEY (question_id, choice_number)
);

CREATE TABLE assets (
    id INTEGER PRIMARY KEY,
    question_id TEXT REFERENCES questions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                        -- question_image | question_json
    path TEXT NOT NULL,
    sha256 TEXT,
    phash TEXT,
    width INTEGER,
    height INTEGER,
    dpi INTEGER
);

CREATE TABLE extraction_logs (
    id INTEGER PRIMARY KEY,
    ts TEXT, run_id TEXT, level TEXT, stage TEXT, code TEXT, message TEXT,
    exam_key TEXT, question_id TEXT, original_page INTEGER, context TEXT
);

CREATE TABLE duplicate_candidates (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                        -- file | page | question_image | question_text
    a TEXT NOT NULL, b TEXT NOT NULL,
    method TEXT, distance REAL, note TEXT
);

CREATE TABLE reviews (                         -- 사람 검수 (data/reviews/reviews.jsonl 원본)
    id INTEGER PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id),
    status TEXT NOT NULL CHECK (status IN ('ok','needs_review','source_error','crop_error','text_error')),
    note TEXT,
    reviewer TEXT,
    reviewed_at TEXT NOT NULL
);

-- Phase 2 확장용 (지금은 비워 둔다: AI 가 임의 생성하지 않음)
CREATE TABLE annotation_fields (
    field TEXT PRIMARY KEY,                    -- 예: integrated_social_relevance, unit, key_concepts ...
    description TEXT,
    value_type TEXT                            -- text | json | integer | real
);
CREATE TABLE question_annotations (
    id INTEGER PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id),
    field TEXT NOT NULL REFERENCES annotation_fields(field),
    value TEXT,
    source TEXT NOT NULL,                      -- human | model:<name> | rule:<name>
    created_at TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE VIEW v_question_latest_review AS
SELECT r.* FROM reviews r
JOIN (SELECT question_id, MAX(id) AS mid FROM reviews GROUP BY question_id) m ON r.id = m.mid;
"""

PHASE2_FIELDS = [
    ("integrated_social_relevance", "2028 통합사회 관련성", "json"),
    ("integrated_social_unit", "통합사회 단원", "json"),
    ("key_concepts", "핵심 개념", "json"),
    ("item_mechanism", "출제 메커니즘", "text"),
    ("material_type", "자료 유형", "json"),
    ("thinkers_theories", "사상가/이론", "json"),
    ("difficulty", "난이도", "text"),
    ("transformability", "변형 가능성", "text"),
    ("kice_2028_sample_mapping", "2028 평가원 예시문항과의 대응 관계", "json"),
]


def load_reviews() -> list[dict]:
    p = REVIEWS / "reviews.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def build_db(cfg: dict, inventories: list, extract_summaries: list[dict], log_entries: list[dict],
             duplicates: list[dict], phashes: dict[str, str]) -> Path:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".db.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    cur = con.cursor()
    cur.executemany("INSERT INTO schema_meta VALUES (?,?)",
                    [("schema_version", str(SCHEMA_VERSION)), ("built_at", now_iso())])
    for code, s in cfg["subjects"].items():
        cur.execute("INSERT INTO subjects VALUES (?,?,?)", (code, s["name_ko"], s.get("expected_questions_per_exam")))
    cur.executemany("INSERT INTO annotation_fields VALUES (?,?,?)", PHASE2_FIELDS)

    file_ids = {}
    for inv in inventories:
        comp = inv.compilation
        cur.execute("INSERT INTO compilations VALUES (?,?,?,?)", (comp["key"], comp["subject"], comp["name"], inv.total_pages))
        for f in inv.files:
            cur.execute("""INSERT INTO source_files (compilation_key, filename, path, sha256, page_count, split_index,
                           original_page_start, original_page_end) VALUES (?,?,?,?,?,?,?,?)""",
                        (comp["key"], f.filename, os.path.relpath(f.path, ROOT), f.sha256, f.page_count,
                         f.split_index, f.original_start, f.original_end))
            file_ids[(comp["key"], f.filename)] = cur.lastrowid

    page_exam = {}
    for inv, summ in zip(inventories, extract_summaries):
        comp = inv.compilation
        subj = comp["subject"]
        for e in summ["exams"]:
            pages = e["pages"]
            cur.execute("""INSERT INTO exams VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (e["exam_key"], subj, comp["key"], e.get("academic_year"), e.get("exam_year"),
                         e.get("exam_type"), pages[0], pages[-1], len(pages),
                         cfg["subjects"][subj]["expected_questions_per_exam"], e.get("title_confidence"),
                         json.dumps(e.get("title_texts"), ensure_ascii=False), int(bool(e.get("review_required"))),
                         json.dumps(e.get("warnings", []), ensure_ascii=False)))
            for p in pages:
                page_exam[(comp["key"], p)] = e["exam_key"]
        from .stage_pages import page_dir
        for r in inv.page_map():
            p = r["original_compilation_page"]
            meta = read_json(page_dir(comp["key"], p) / "page.json") or {}
            lay = meta.get("layout", {})
            cur.execute("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?)",
                        (comp["key"], p, file_ids[(comp["key"], r["split_pdf"])], r["split_pdf_page"],
                         page_exam.get((comp["key"], p)), int(bool(meta.get("objects", {}).get("is_scanned"))),
                         lay.get("method"), json.dumps(lay, ensure_ascii=False)))

    dup_ids = {d["a"] for d in duplicates if d["kind"].startswith("question")} | \
              {d["b"] for d in duplicates if d["kind"].startswith("question")}
    for jp in sorted(Q_JSON.glob("*.json")):
        r = read_json(jp)
        if not r:
            continue
        s, o, a, x = r["source"], r["original"], r["answer"], r["extraction"]
        hv = o.get("has_visual_material")
        cur.execute("""INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["id"], s["exam_key"], s["subject"], s["question_number"], s["split_pdf"],
                     s["split_pdf_page"], s["original_compilation_page"], s.get("column"),
                     json.dumps(s.get("bbox_px")), o["question_image"], o["raw_text"], o["stem"], o["passage"],
                     None if hv is None else int(hv), o.get("points"), a["value"], a["source"], int(a["verified"]),
                     x["confidence"], int(x["review_required"]), json.dumps(x["warnings"], ensure_ascii=False),
                     x.get("question_number_source"), x.get("choice_method"),
                     os.path.relpath(jp, ROOT), sha256_file(jp),
                     "duplicate_candidate" if r["id"] in dup_ids else None,
                     x.get("pipeline_version"), x.get("run_id")))
        for c in o["choices"]:
            cur.execute("INSERT INTO choices VALUES (?,?,?)", (r["id"], c["number"], c["text"]))
        ip = ROOT / o["question_image"]
        if ip.exists():
            from PIL import Image
            with Image.open(ip) as im:
                w, h = im.size
            cur.execute("INSERT INTO assets (question_id, kind, path, sha256, phash, width, height, dpi) VALUES (?,?,?,?,?,?,?,?)",
                        (r["id"], "question_image", o["question_image"], sha256_file(ip), phashes.get(r["id"]),
                         w, h, s.get("render_dpi")))
        cur.execute("INSERT INTO assets (question_id, kind, path, sha256) VALUES (?,?,?,?)",
                    (r["id"], "question_json", os.path.relpath(jp, ROOT), sha256_file(jp)))

    for e in log_entries:
        ctx = {k: v for k, v in e.items() if k not in ("ts", "run_id", "level", "stage", "code", "message",
                                                      "exam", "question_id", "page")}
        cur.execute("""INSERT INTO extraction_logs (ts, run_id, level, stage, code, message, exam_key, question_id,
                       original_page, context) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (e["ts"], e["run_id"], e["level"], e["stage"], e["code"], e["message"], e.get("exam"),
                     e.get("question_id"), e.get("page"), json.dumps(ctx, ensure_ascii=False) if ctx else None))
    for d in duplicates:
        cur.execute("INSERT INTO duplicate_candidates (kind, a, b, method, distance, note) VALUES (?,?,?,?,?,?)",
                    (d["kind"], d["a"], d["b"], d["method"], d["distance"], d.get("note")))
    qids = {row[0] for row in cur.execute("SELECT id FROM questions")}
    for rv in load_reviews():
        if rv["question_id"] in qids:
            cur.execute("INSERT INTO reviews (question_id, status, note, reviewer, reviewed_at) VALUES (?,?,?,?,?)",
                        (rv["question_id"], rv["status"], rv.get("note"), rv.get("reviewer"), rv["reviewed_at"]))
    con.commit()
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    if fk:
        raise RuntimeError(f"foreign key violations: {fk[:5]}")
    os.replace(tmp, DB_PATH)
    return DB_PATH
