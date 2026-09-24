"""LIFE_ETHICS FINAL 136 targeted answer-logic correction 결과 조립 (1회성).

판정은 문항 원문(선지 포함)을 읽고 "정답을 결정하는 최소 필요 지식이 통합사회 범위인가"를 사람이 판단한
의미 판정이며 data/phase2/LIFE_ETHICS/final_logic_correction/targeted_review_decisions.txt 에 기록돼 있다.
대상은 FINAL 136 중 false-positive 패턴(KICE RESTORE 30 전체, 사상가 비교·시민 불복종·정의론·처벌·환경·평화)에
해당하는 문항만이며, 나머지 KEEP은 보호(재판정 없음)한다. 키워드 판정 없음. 기존 파일은 읽기만 한다.

출력(data/phase2/LIFE_ETHICS/final_logic_correction/):
  targeted_review.jsonl, final_logic_correction.jsonl, final_content_asset_index_v3.jsonl,
  final_logic_correction_summary.json
"""
from __future__ import annotations

import collections
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "data/phase2/LIFE_ETHICS"
OUT = P2 / "final_logic_correction"
DEC = OUT / "targeted_review_decisions.txt"
INDEX = P2 / "final_content_asset_index.jsonl"
ADAPT = {"N": "NONE", "L": "LIGHT", "S": "SUBSTANTIAL"}
REASONS = {"PHILOSOPHER_COMPARISON_REQUIRED", "OUTSIDE_KICE_DEPTH", "LIFE_ETHICS_SPECIFIC_THEORY",
           "SUBSTANTIAL_REWRITE_REQUIRED", "CONTENT_PRESENCE_ONLY", "OTHER"}
THEME = ["CIVIL_DISOBEDIENCE", "RAWLS_NOZICK", "PUNISHMENT", "ENVIRONMENT", "PEACE"]


def tracked_hashes():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    return {f.decode(): hashlib.sha256((ROOT / f.decode()).read_bytes()).hexdigest()
            for f in files if f and (ROOT / f.decode()).exists() and not f.decode().startswith(str(OUT.relative_to(ROOT)))}


def main():
    before = tracked_hashes()
    idx = [json.loads(l) for l in open(INDEX, encoding="utf-8")]
    orig = {r["question_id"]: r for r in idx}

    recs, seen = [], set()
    for line in open(DEC, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("|")
        assert len(f) == 12, line[:80]
        ids, d, trig, logic, isc, extra, kice, match, adapt, reasons, why, rr = f
        assert d in ("K", "D") and match in ("Y", "N") and adapt in ADAPT
        rs = [x for x in reasons.split(";") if x]
        assert set(rs) <= REASONS, rs
        if d == "K":
            assert adapt in ("N", "L") and not rs and not why and not extra
        else:
            assert rs and why and logic
        for short in ids.split(","):
            y, e, q = short.strip().split("_")
            qid = f"{y}_{e}_LIFE_ETHICS_{q}"
            assert qid not in seen, qid
            seen.add(qid)
            rrs = [x for x in rr.split(";") if x]
            recs.append({
                "question_id": qid,
                "previous_status": "KEEP",
                "previous_source": orig[qid]["source"] if qid in orig else None,
                "trigger": [x for x in trig.split(";") if x],
                "answer_logic": logic,
                "integrated_social_content_present": bool(isc),
                "integrated_social_content": [x for x in isc.split(";") if x],
                "requires_life_ethics_specific_knowledge": bool(extra),
                "specific_extra_knowledge": extra or None,
                "kice_example_support": kice,
                "kice_depth_match": match == "Y",
                "adaptation_required": ADAPT[adapt],
                "final_decision": {"K": "KEEP", "D": "DROP"}[d],
                "drop_reasons": rs,
                "why_content_presence_is_insufficient": why or None,
                "review_required": bool(rrs),
                "review_reasons": rrs,
            })
    recs.sort(key=lambda r: r["question_id"])
    tr = {r["question_id"]: r for r in recs}
    drop = {q for q, r in tr.items() if r["final_decision"] == "DROP"}

    full, v3 = [], []
    for r in idx:
        q = r["question_id"]
        t = tr.get(q)
        dec = t["final_decision"] if t else "KEEP"
        full.append({
            "question_id": q,
            "previous_source": r["source"],
            "targeted": bool(t),
            "final_decision": dec,
            "decision_basis": ("TARGETED_REVIEW" if t else "PROTECTED_EXISTING_KEEP"),
            "adaptation_required": t["adaptation_required"] if t else "NONE",
            "drop_reasons": t["drop_reasons"] if t else [],
            "primary_curriculum_code": r["primary_curriculum_code"],
        })
        if dec == "KEEP":
            v3.append({
                "question_id": q,
                "source": r["source"],
                "review_required": bool(r["review_required"] or (t and t["review_required"])),
                "primary_curriculum_code": r["primary_curriculum_code"],
                "kice_sample_link": r["kice_sample_link"],
                "logic_correction": "TARGETED_KEEP" if t else "PROTECTED",
                "adaptation_required": t["adaptation_required"] if t else "NONE",
            })

    def dump(path, rows):
        with open(path, "w", encoding="utf-8") as fo:
            for x in rows:
                fo.write(json.dumps(x, ensure_ascii=False) + "\n")

    dump(OUT / "targeted_review.jsonl", recs)
    dump(OUT / "final_logic_correction.jsonl", full)
    dump(OUT / "final_content_asset_index_v3.jsonl", v3)

    # ---------------- QA ----------------
    ids_o = [r["question_id"] for r in idx]
    ids_v3 = [r["question_id"] for r in v3]
    audit = {json.loads(l)["question_id"]: json.loads(l) for l in open(P2 / "official_curriculum_final_audit.jsonl", encoding="utf-8")}
    rc = {json.loads(l)["question_id"]: json.loads(l) for l in open(P2 / "kice_2028_reconsideration.jsonl", encoding="utf-8")}

    def has_evidence(q):
        return bool(audit[q]["curriculum_evidence"] if audit[q]["final_audit_decision"] == "KEEP" else rc[q]["curriculum_evidence"])

    after = tracked_hashes()
    qa = {
        "original_final_ids": len(ids_o),
        "duplicate_original_ids": len(ids_o) - len(set(ids_o)),
        "targeted_ids": len(recs),
        "targeted_subset_of_original": set(tr) <= set(ids_o),
        "v3_subset_of_original": set(ids_v3) <= set(ids_o),
        "new_ids_introduced": len(set(ids_v3) - set(ids_o)),
        "drop_ids_in_v3": len(set(ids_v3) & drop),
        "v3_count_eq_136_minus_drop": len(v3) == len(ids_o) - len(drop),
        "v3_keep_without_curriculum_evidence": sum(1 for q in ids_v3 if not (orig[q]["primary_curriculum_code"] and has_evidence(q))),
        "v3_keep_without_content_rationale": sum(1 for q in ids_v3 if not (tr[q]["answer_logic"] if q in tr else audit[q]["rationale"])),
        "v3_keep_adaptation_not_none_light": sum(1 for r in v3 if r["adaptation_required"] not in ("NONE", "LIGHT")),
        "drop_missing_answer_logic_reason_or_why": sum(1 for q in drop if not (tr[q]["answer_logic"] and tr[q]["drop_reasons"]
                                                                           and tr[q]["why_content_presence_is_insufficient"])),
        "existing_files_modified": sum(1 for k, v in before.items() if after.get(k) != v),
    }
    assert qa["original_final_ids"] == 136 and qa["targeted_subset_of_original"] and qa["v3_subset_of_original"]
    assert qa["v3_count_eq_136_minus_drop"]
    assert all(qa[k] == 0 for k in qa if isinstance(qa[k], int) and not isinstance(qa[k], bool)
               and k not in ("original_final_ids", "targeted_ids")), qa

    dr = [tr[q] for q in drop]
    theme_of = lambda r: next((t for t in THEME if t in r["trigger"]), "OTHER_PHILOSOPHER_COMPARISON")
    by_theme = collections.defaultdict(lambda: {"targeted": 0, "keep": 0, "drop": 0})
    for r in recs:
        b = by_theme[theme_of(r)]
        b["targeted"] += 1
        b["keep" if r["final_decision"] == "KEEP" else "drop"] += 1
    summary = {
        "base": {"previous_final": len(idx), "previous_freeze_commit": "99bc4a6",
                 "previous_composition": dict(collections.Counter(r["source"] for r in idx))},
        "targeted_review_candidates": len(recs),
        "targeted_by_previous_source": dict(collections.Counter(r["previous_source"] for r in recs)),
        "KEEP_after_targeted_review": len(recs) - len(drop),
        "DROP_after_targeted_review": len(drop),
        "protected_not_targeted": len(idx) - len(recs),
        "final_v3_count": len(v3),
        "final_v3_composition": dict(collections.Counter(r["source"] for r in v3)),
        "drop_by_previous_source": dict(collections.Counter(r["previous_source"] for r in dr)),
        "drop_reason_counts": {k: sum(k in r["drop_reasons"] for r in dr) for k in sorted(REASONS)},
        "by_theme": dict(by_theme),
        "targeted_keep_adaptation": dict(collections.Counter(r["adaptation_required"] for r in recs if r["final_decision"] == "KEEP")),
        "review_required": {"targeted": sum(r["review_required"] for r in recs), "v3_index": sum(r["review_required"] for r in v3)},
        "qa": qa,
        "method": "FINAL 136 중 false-positive 패턴 문항(KICE RESTORE 30 전체 + 사상가 신호가 있는 CORE)만 선지 포함 원문으로 판독. "
                  "Counterfactual test A/B/C로 정답 결정 최소 지식이 통합사회·KICE 예시 깊이 안인지 판단. "
                  "사상가 이름이 없는 입장 비교형 CORE(교육과정 명시 주제, 제시문으로 정답 결정)는 보호. 외부 API·OCR·재분류 없음.",
    }
    json.dump(summary, open(OUT / "final_logic_correction_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ["targeted_review_candidates", "KEEP_after_targeted_review", "DROP_after_targeted_review",
                                              "final_v3_count", "final_v3_composition", "drop_reason_counts", "by_theme", "review_required", "qa"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
