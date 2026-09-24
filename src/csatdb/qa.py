"""STEP 6/9/10: 중복 탐지, 자동 QA, SQLite 구축, 보고서 생성.

QA 에서 문제가 발견되어도 문항을 삭제/수정하지 않는다.
해당 문항 JSON 의 extraction.review_required / warnings 에 QA 결과를 추가하는 것만 허용한다.
"""
from __future__ import annotations

import collections
import csv
import itertools
import json
import re
import sqlite3
from pathlib import Path

import imagehash
from PIL import Image

from .common import (Q_JSON, REPORTS, PROCESSED, ROOT, DB_PATH, LOGS, read_json,
                     write_json_atomic, sha256_file, RunLog)
from .db import build_db
from .inventory import discover
from .stage_pages import page_dir

PHASH_MAX_DIST = 6
TEXT_JACCARD_MIN = 0.75


def _ngrams(t: str, n: int = 3) -> set[str]:
    t = re.sub(r"\s+", "", t)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def find_duplicates(records: list[dict], inventories: list) -> tuple[list[dict], dict[str, str]]:
    dups: list[dict] = []
    # 1) 파일 SHA-256
    for inv in inventories:
        by = collections.defaultdict(list)
        for f in inv.files:
            by[f.sha256].append(f.filename)
        for sha, names in by.items():
            for a, b in itertools.combinations(names, 2):
                dups.append({"kind": "file", "a": a, "b": b, "method": "sha256", "distance": 0})
    # 2) 페이지 perceptual hash (분할 경계 중복 업로드 등)
    for inv in inventories:
        ph = {}
        for r in inv.page_map():
            p = r["original_compilation_page"]
            img = page_dir(inv.compilation["key"], p) / "page.png"
            if img.exists():
                with Image.open(img) as im:
                    ph[p] = imagehash.phash(im.reduce(4), hash_size=16)
        for a, b in itertools.combinations(sorted(ph), 2):
            d = ph[a] - ph[b]
            if d <= 10:
                dups.append({"kind": "page", "a": f"p{a:03d}", "b": f"p{b:03d}", "method": "phash16", "distance": d})
    # 3) 문항 이미지 perceptual hash + 4) 문항 텍스트 3-gram Jaccard
    phashes = {}
    hashes = {}
    grams = {}
    for r in records:
        ip = ROOT / r["original"]["question_image"]
        if ip.exists():
            with Image.open(ip) as im:
                h = imagehash.phash(im.convert("L"), hash_size=16)
            hashes[r["id"]] = h
            phashes[r["id"]] = str(h)
        grams[r["id"]] = _ngrams(r["original"]["raw_text"])
    ids = sorted(hashes)
    for a, b in itertools.combinations(ids, 2):
        d = hashes[a] - hashes[b]
        if d <= PHASH_MAX_DIST:
            dups.append({"kind": "question_image", "a": a, "b": b, "method": "phash16", "distance": d})
    gids = sorted(g for g in grams if len(grams[g]) > 30)
    for a, b in itertools.combinations(gids, 2):
        ga, gb = grams[a], grams[b]
        if min(len(ga), len(gb)) / max(len(ga), len(gb)) < TEXT_JACCARD_MIN:
            continue
        j = len(ga & gb) / len(ga | gb)
        if j >= TEXT_JACCARD_MIN:
            dups.append({"kind": "question_text", "a": a, "b": b, "method": "char3gram_jaccard",
                         "distance": round(1 - j, 4)})
    return dups, phashes


def run_qa(cfg: dict, log: RunLog) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    inventories = [discover(c) for c in cfg["compilations"]]
    summaries = [read_json(PROCESSED / f"extract_summary_{inv.compilation['key']}.json") or {"exams": [], "issues": [], "anomalies": []}
                 for inv in inventories]
    records = []
    errors: list[dict] = []
    for jp in sorted(Q_JSON.glob("*.json")):
        try:
            r = read_json(jp)
        except Exception as e:
            errors.append({"level": "ERROR", "code": "JSON_UNREADABLE", "question_id": jp.stem, "message": repr(e)})
            continue
        if not r:
            errors.append({"level": "ERROR", "code": "EMPTY_JSON", "question_id": jp.stem, "message": "빈 JSON"})
            continue
        r["_path"] = jp
        records.append(r)

    qa_flags: dict[str, list[str]] = collections.defaultdict(list)
    # ---------------- 페이지 매핑 검증
    pm = {}
    for inv in inventories:
        for row in inv.page_map():
            pm[(row["split_pdf"], row["split_pdf_page"])] = row["original_compilation_page"]
    exam_pages = {}
    for summ in summaries:
        for e in summ["exams"]:
            exam_pages[e["exam_key"]] = set(e["pages"])
    for r in records:
        s = r["source"]
        if pm.get((s["split_pdf"], s["split_pdf_page"])) != s["original_compilation_page"]:
            qa_flags[r["id"]].append("QA_PAGE_MAPPING_ERROR")
        if s["exam_key"] in exam_pages and s["original_compilation_page"] not in exam_pages[s["exam_key"]]:
            qa_flags[r["id"]].append("QA_PAGE_OUTSIDE_EXAM")
        if not r["id"] or r["id"] != r["_path"].stem:
            qa_flags[r["id"]].append("QA_ID_FILENAME_MISMATCH")
        ip = ROOT / r["original"]["question_image"]
        if not ip.exists():
            qa_flags[r["id"]].append("QA_IMAGE_MISSING")
        else:
            with Image.open(ip) as im:
                w, h = im.size
            if h < 180:
                qa_flags[r["id"]].append(f"QA_CROP_TOO_SMALL h={h}")
            if h > 4200:
                qa_flags[r["id"]].append(f"QA_CROP_TOO_LARGE h={h}")
        if not r["original"]["stem"]:
            qa_flags[r["id"]].append("QA_STEM_MISSING")
        if len(r["original"]["choices"]) != 5:
            qa_flags[r["id"]].append(f"QA_CHOICES_{len(r['original']['choices'])}")
        elif any(not c["text"].strip() for c in r["original"]["choices"]):
            qa_flags[r["id"]].append("QA_CHOICE_TEXT_EMPTY")
        if len(r["original"]["raw_text"]) < 20:
            qa_flags[r["id"]].append("QA_TEXT_EXTRACTION_FAILED")
        if not (r["source"]["academic_year"] and r["source"]["exam_type"]):
            qa_flags[r["id"]].append("QA_SOURCE_UNIDENTIFIED")
        if r["answer"]["value"] is not None and not r["answer"]["source"]:
            qa_flags[r["id"]].append("QA_ANSWER_WITHOUT_SOURCE")

    # ---------------- 시험 단위: 번호 누락/중복
    by_exam = collections.defaultdict(list)
    for r in records:
        by_exam[r["source"]["exam_key"]].append(r)
    for ek, rs in by_exam.items():
        nums = collections.Counter(r["source"]["question_number"] for r in rs)
        for n, k in nums.items():
            if k > 1:
                for r in rs:
                    if r["source"]["question_number"] == n:
                        qa_flags[r["id"]].append(f"QA_DUPLICATE_NUMBER_IN_EXAM Q{n}")

    # ---------------- 중복 후보
    dups, phashes = find_duplicates(records, inventories)
    for d in dups:
        if d["kind"].startswith("question"):
            qa_flags[d["a"]].append(f"QA_DUPLICATE_CANDIDATE({d['kind']}) with {d['b']}")
            qa_flags[d["b"]].append(f"QA_DUPLICATE_CANDIDATE({d['kind']}) with {d['a']}")

    # ---------------- QA 결과를 JSON 에 반영 (추가만, 기존 경고 유지)
    for r in records:
        path = r.pop("_path")
        flags = sorted(set(qa_flags.get(r["id"], [])))
        x = r["extraction"]
        x["qa_flags"] = flags
        if flags and any(not f.startswith("QA_DUPLICATE_CANDIDATE") for f in flags):
            x["review_required"] = True
        if flags and any(f.startswith("QA_DUPLICATE_CANDIDATE") for f in flags):
            x["duplicate_status"] = "duplicate_candidate"
        else:
            x.pop("duplicate_status", None)
        write_json_atomic(path, r)
        r["_path"] = path

    # ---------------- 로그 (extract 실행 run + 이번 qa run)
    run_ids = {s.get("run_id") for s in summaries if s.get("run_id")}
    log_entries = []
    for rid in run_ids:
        lp = LOGS / f"run_{rid}.jsonl"
        if lp.exists():
            log_entries += [json.loads(l) for l in lp.read_text(encoding="utf-8").splitlines() if l.strip()]

    # ---------------- DB
    db_error = None
    try:
        build_db(cfg, inventories, summaries, log_entries, dups, phashes)
    except Exception as e:
        db_error = repr(e)
        errors.append({"level": "ERROR", "code": "DB_BUILD_FAILED", "message": db_error})

    # ---------------- DB ↔ JSON 일치 검사
    if DB_PATH.exists():
        con = sqlite3.connect(DB_PATH)
        db_rows = {row[0]: row for row in con.execute("SELECT id, json_sha256, json_path, question_number FROM questions")}
        db_choices = collections.Counter(row[0] for row in con.execute("SELECT question_id FROM choices"))
        con.close()
        json_ids = {r["id"] for r in records}
        for qid in json_ids - set(db_rows):
            errors.append({"level": "ERROR", "code": "DB_JSON_MISMATCH", "question_id": qid, "message": "JSON 에만 존재"})
        for qid in set(db_rows) - json_ids:
            errors.append({"level": "ERROR", "code": "DB_JSON_MISMATCH", "question_id": qid, "message": "DB 에만 존재"})
        for r in records:
            row = db_rows.get(r["id"])
            if row and row[1] != sha256_file(r["_path"]):
                errors.append({"level": "ERROR", "code": "DB_JSON_MISMATCH", "question_id": r["id"], "message": "json_sha256 불일치"})
            if row and db_choices.get(r["id"], 0) != len(r["original"]["choices"]):
                errors.append({"level": "ERROR", "code": "DB_JSON_MISMATCH", "question_id": r["id"], "message": "선택지 수 불일치"})

    # ---------------- 보고서
    for summ in summaries:
        for it in summ.get("issues", []):
            if it["level"] == "ERROR":
                errors.append(it)
        for a in summ.get("anomalies", []):
            errors.append({"level": "WARNING", "code": a["code"], "exam_key": a.get("exam_key"), "message": a["message"]})
    for inv in inventories:
        for p in inv.problems:
            errors.append({"level": "ERROR", "code": p["code"], "message": p["message"], "file": p.get("file")})

    _write_reports(cfg, summaries, records, errors, dups)
    n_rev = sum(1 for r in records if r["extraction"]["review_required"])
    result = {"questions": len(records), "review_required": n_rev, "ok": len(records) - n_rev,
              "answers_linked": sum(1 for r in records if r["answer"]["value"] is not None),
              "errors": sum(1 for e in errors if e.get("level") == "ERROR"),
              "warnings": sum(1 for e in errors if e.get("level") == "WARNING"),
              "duplicate_candidates": len(dups), "db": str(DB_PATH.relative_to(ROOT)), "db_error": db_error}
    write_json_atomic(REPORTS / "summary.json", result)
    print("[qa]", result)
    return result


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _write_reports(cfg, summaries, records, errors, dups):
    by_exam = collections.defaultdict(list)
    for r in records:
        by_exam[r["source"]["exam_key"]].append(r)
    err_by_exam = collections.Counter(e.get("exam_key") for e in errors if e.get("level") == "ERROR")
    rows = []
    for summ in summaries:
        for e in summ["exams"]:
            subj = e.get("subject") or (e["exam_key"].split("_", 2)[-1] if e["exam_key"] else None)
            exp = cfg["subjects"].get(subj, {}).get("expected_questions_per_exam", 20)
            rs = by_exam.get(e["exam_key"], [])
            rows.append([
                e.get("academic_year"), e.get("exam_type"), e["exam_key"], subj,
                f"{e['pages'][0]}-{e['pages'][-1]}", len(e["pages"]),
                exp, len(rs),
                sum(1 for r in rs if r["answer"]["value"] is not None),
                sum(1 for r in rs if r["extraction"]["review_required"]),
                err_by_exam.get(e["exam_key"], 0),
                " ".join(map(str, e.get("missing_numbers", []))),
                " ".join(map(str, e.get("duplicate_numbers", []))),
                e.get("title_confidence"),
                "; ".join(e.get("warnings", [])),
            ])
    _write_csv(REPORTS / "coverage_report.csv",
               ["학년도", "시험종류", "exam_key", "과목", "원본페이지", "페이지수", "예상문항수", "추출문항수",
                "정답연결수", "검토필요수", "오류수", "누락번호", "중복번호", "제목인식신뢰도", "시험경고"], rows)
    _write_csv(REPORTS / "review_required.csv",
               ["id", "exam_key", "question_number", "original_compilation_page", "split_pdf", "split_pdf_page",
                "confidence", "warnings", "qa_flags", "question_image"],
               [[r["id"], r["source"]["exam_key"], r["source"]["question_number"],
                 r["source"]["original_compilation_page"], r["source"]["split_pdf"], r["source"]["split_pdf_page"],
                 r["extraction"]["confidence"], " | ".join(r["extraction"]["warnings"]),
                 " | ".join(r["extraction"].get("qa_flags", [])), r["original"]["question_image"]]
                for r in records if r["extraction"]["review_required"]])
    _write_csv(REPORTS / "errors.csv", ["level", "code", "exam_key", "question_id", "page", "file", "message"],
               [[e.get("level"), e.get("code"), e.get("exam_key"), e.get("question_id"), e.get("page"),
                 e.get("file"), e.get("message")] for e in errors])
    _write_csv(REPORTS / "duplicate_candidates.csv", ["kind", "a", "b", "method", "distance", "status"],
               [[d["kind"], d["a"], d["b"], d["method"], d["distance"], "duplicate_candidate (자동 삭제 안 함)"]
                for d in dups])
