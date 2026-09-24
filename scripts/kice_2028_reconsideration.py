"""LIFE_ETHICS 공식 교육과정 감사 DROP 155문항의 2028 KICE 예시문항 기반 최종 재검토 결과 조립 (1회성).

판정은 문항 원문(선지 포함)을 28예시 문항(SOURCE C)·예시문항 안내(SOURCE B)·공식 교육과정(SOURCE A)과
대조해 사람이 내린 의미 판정이며 data/phase2/LIFE_ETHICS/kice_2028_reconsideration_decisions.txt 에 기록돼 있다.
표본 요구 깊이 기준은 data/phase2/LIFE_ETHICS/kice_2028_sample_depth_reference.json 에 있다.
이 스크립트는 그 기록을 스키마에 맞춰 조립·검증만 한다(키워드 판정 없음). 기존 Phase 1/2/Audit 파일은 읽기만 한다.

출력: data/phase2/LIFE_ETHICS/kice_2028_reconsideration.jsonl, final_content_asset_index.jsonl,
      kice_2028_reconsideration_summary.json
"""
from __future__ import annotations

import collections
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "data/phase2/LIFE_ETHICS"
MASTER = ROOT / "reference/curriculum/integrated_social_official_scope.json"
DEC = P2 / "kice_2028_reconsideration_decisions.txt"
DEPTH = P2 / "kice_2028_sample_depth_reference.json"
OUT_RC = P2 / "kice_2028_reconsideration.jsonl"
OUT_IDX = P2 / "final_content_asset_index.jsonl"
OUT_SUM = P2 / "kice_2028_reconsideration_summary.json"
EVT = {"ST": "STANDARD_TEXT", "EX": "EXPLANATION", "CE": "CONTENT_ELEMENT", "AC": "APPLICATION_CONSIDERATION"}
BASIS = {"K": "KICE_SAMPLE_CONFIRMED", "C": "CURRICULUM_REINTERPRETATION", "-": None}
BUCKET = {"Q4": "Q4_environment", "Q14": "Q14_civil_disobedience", "Q15": "Q15_rawls_nozick",
          "Q16": "Q16_punishment", "Q18": "Q18_peace_violence"}


def tracked_hashes():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    out = {}
    for f in files:
        if not f:
            continue
        p = ROOT / f.decode()
        if p in (OUT_RC, OUT_IDX, OUT_SUM, DEC) or not p.exists():
            continue
        out[f.decode()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main():
    before = tracked_hashes()
    master = json.load(open(MASTER, encoding="utf-8"))
    std_codes = {s["code"] for a in master["areas"] for s in a["standards"]}
    json.load(open(DEPTH, encoding="utf-8"))  # 깊이 기준 파일 유효성 확인

    cur = {json.loads(l)["question_id"]: json.loads(l)["final_decision"]
           for l in open(P2 / "final_curation.jsonl", encoding="utf-8")}
    audit = {json.loads(l)["question_id"]: json.loads(l)
             for l in open(P2 / "official_curriculum_final_audit.jsonl", encoding="utf-8")}
    core = sorted(q for q, r in audit.items() if r["final_audit_decision"] == "KEEP")
    drop_ids = sorted(q for q, r in audit.items() if r["final_audit_decision"] == "DROP")

    recs, seen = [], set()
    for line in open(DEC, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("|")
        assert len(f) == 9, line[:80]
        short, d, samples, evid, basis, req, oos, rationale, rr = f
        y, e, q = short.split("_")
        qid = f"{y}_{e}_LIFE_ETHICS_{q}"
        assert qid not in seen, qid
        seen.add(qid)
        links = []
        for item in [x.strip() for x in samples.split(";;") if x.strip()]:
            sq, depth, matched = item.split(":", 2)
            assert depth in ("DIRECT", "CLOSE", "PARTIAL"), (qid, depth)
            links.append({"sample_question": f"28예시 {sq}", "matched_content": [m.strip() for m in matched.split(";") if m.strip()],
                          "match_depth": depth})
        ev = []
        for item in [x.strip() for x in evid.split(";;") if x.strip()]:
            code, typ, reason = item.split(":", 2)
            code = f"10통사{code}"
            assert code in std_codes, (qid, code)
            ev.append({"standard_code": code, "evidence_type": EVT[typ], "reason": reason})
        rrs = [x.strip() for x in rr.split(";") if x.strip()]
        for x in audit[qid]["review_reasons"]:  # 감사 단계 검토 플래그 승계(판정에는 영향 없음)
            if x not in rrs:
                rrs.append(x)
        recs.append({
            "question_id": qid,
            "previous_phase2c_decision": cur.get(qid),
            "official_curriculum_audit_decision": audit[qid]["final_audit_decision"],
            "reconsideration_decision": {"R": "RESTORE", "D": "DROP"}[d],
            "required_content": [x.strip() for x in req.split(";") if x.strip()],
            "kice_sample_links": links,
            "curriculum_evidence": ev,
            "restore_basis": BASIS[basis],
            "out_of_scope_dependency": [x.strip() for x in oos.split(";") if x.strip()],
            "rationale": rationale.strip(),
            "review_required": bool(rrs),
            "review_reasons": rrs,
        })
    recs.sort(key=lambda r: r["question_id"])
    restore = [r for r in recs if r["reconsideration_decision"] == "RESTORE"]
    remain = [r for r in recs if r["reconsideration_decision"] == "DROP"]

    index = []
    for q in core:
        a = audit[q]
        index.append({"question_id": q, "source": "CURRICULUM_EXPLICIT_CORE", "review_required": a["review_required"],
                      "primary_curriculum_code": a["curriculum_evidence"][0]["standard_code"], "kice_sample_link": None})
    for r in restore:
        primary_link = r["kice_sample_links"][0] if r["kice_sample_links"] else None
        index.append({"question_id": r["question_id"], "source": "KICE_2028_RESTORED", "review_required": r["review_required"],
                      "primary_curriculum_code": r["curriculum_evidence"][0]["standard_code"] if r["curriculum_evidence"] else None,
                      "kice_sample_link": {"sample_question": primary_link["sample_question"], "match_depth": primary_link["match_depth"]}
                      if primary_link else None})
    index.sort(key=lambda r: r["question_id"])

    with open(OUT_RC, "w", encoding="utf-8") as fo:
        for r in recs:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT_IDX, "w", encoding="utf-8") as fo:
        for r in index:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---------------- QA ----------------
    idx_ids = [r["question_id"] for r in index]
    phase2c_keep = sorted(q for q, d in cur.items() if d == "KEEP")
    after = tracked_hashes()
    qa = {
        "A_core_plus_reconsidered_equals_phase2c_keep": len(core) + len(recs) == len(phase2c_keep) == 261,
        "B_restore_plus_remain_equals_reconsidered": len(restore) + len(remain) == len(recs) == 155,
        "C_final_equals_core_plus_restore": len(index) == len(core) + len(restore),
        "D_duplicate_ids": (len(idx_ids) - len(set(idx_ids))) + (len([r["question_id"] for r in recs]) - len(seen)),
        "E_core_missing_from_index": len(set(core) - set(idx_ids)),
        "F_restore_missing_from_index": len({r["question_id"] for r in restore} - set(idx_ids)),
        "G_non_drop_contamination": sum(1 for r in recs if r["official_curriculum_audit_decision"] != "DROP"
                                        or r["previous_phase2c_decision"] != "KEEP") + len(set(seen) ^ set(drop_ids))
                                    + len(set(idx_ids) - set(core) - {r["question_id"] for r in restore}),
        "H_restore_without_evidence": sum(1 for r in restore if not r["restore_basis"] or not (
            (r["restore_basis"] == "KICE_SAMPLE_CONFIRMED" and r["kice_sample_links"])
            or (r["restore_basis"] == "CURRICULUM_REINTERPRETATION" and r["curriculum_evidence"]))),
        "I_drop_without_reason": sum(1 for r in remain if r["restore_basis"] is not None
                                     or not (r["out_of_scope_dependency"] and r["rationale"])),
        "J_existing_files_modified": sum(1 for k, v in before.items() if after.get(k) != v),
    }
    assert qa["A_core_plus_reconsidered_equals_phase2c_keep"] and qa["B_restore_plus_remain_equals_reconsidered"]
    assert qa["C_final_equals_core_plus_restore"]
    assert all(qa[k] == 0 for k in qa if k[0] in "DEFGHIJ"), qa

    def bucket(r):
        if r["restore_basis"] == "CURRICULUM_REINTERPRETATION":
            return "curriculum_reinterpretation"
        sq = r["kice_sample_links"][0]["sample_question"].split()[-1]
        return BUCKET.get(sq, "other_kice_sample")

    breakdown = collections.Counter(bucket(r) for r in restore)
    for k in list(BUCKET.values()) + ["other_kice_sample", "curriculum_reinterpretation"]:
        breakdown.setdefault(k, 0)
    by_cat_total = collections.Counter(audit[r["question_id"]]["drop_reason_category"] for r in recs)
    by_cat_restore = collections.Counter(audit[r["question_id"]]["drop_reason_category"] for r in restore)
    summary = {
        "phase2c_keep": len(phase2c_keep),
        "curriculum_explicit_core": len(core),
        "reconsidered": len(recs),
        "RESTORE": len(restore),
        "REMAIN_DROP": len(remain),
        "FINAL_CONTENT_ASSETS": len(index),
        "review_required": {
            "reconsidered_total": sum(r["review_required"] for r in recs),
            "restore": sum(r["review_required"] for r in restore),
            "remain_drop": sum(r["review_required"] for r in remain),
            "final_index": sum(r["review_required"] for r in index),
        },
        "restore_breakdown": {k: breakdown[k] for k in list(BUCKET.values()) + ["other_kice_sample", "curriculum_reinterpretation"]},
        "restore_by_basis": dict(collections.Counter(r["restore_basis"] for r in restore)),
        "restore_by_match_depth": dict(collections.Counter(r["kice_sample_links"][0]["match_depth"] for r in restore if r["kice_sample_links"])),
        "by_audit_drop_category": {k: {"reconsidered": v, "restore": by_cat_restore.get(k, 0), "remain_drop": v - by_cat_restore.get(k, 0)}
                                   for k, v in by_cat_total.most_common()},
        "qa": qa,
        "sources": {
            "A": "reference/curriculum/2022개정_사회과_별책7.pdf (MASTER: integrated_social_official_scope.json)",
            "B": "reference/2028_예시문항_안내.pdf ([별첨] 2028학년도 대학수학능력시험 예시문항 안내)",
            "C": "reference/2028_통합사회_예시문항.pdf (28예시 25문항)",
        },
        "method": "감사 DROP 155문항만 선지 포함 원문으로 20~30문항 batch 판독. 28예시 Q4/Q14/Q15/Q16/Q18이 실제 요구하는 깊이"
                  "(kice_2028_sample_depth_reference.json)와 정답 판별에 필요한 깊이를 비교해 RESTORE/DROP 판정. "
                  "사상가 등장만으로 선택 과목 세부를 확장하지 않음. CORE 106은 재판정하지 않음. 외부 API 0회, 신규 OCR 없음.",
        "note": "SOURCE B의 공식 해설은 Q1·Q2·Q12·Q13·Q17·Q23에만 있고 Q4·Q14·Q15·Q16·Q18은 부록의 성취기준 연결만 제공되어, "
                "해당 문항의 요구 깊이는 SOURCE C 문항 원문(발문·제시문·선지)에서 도출함. 기존 Phase 1/2/Audit/DB/FINAL_TRANSFER는 변경하지 않음.",
    }
    json.dump(summary, open(OUT_SUM, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ["phase2c_keep", "curriculum_explicit_core", "reconsidered", "RESTORE",
                                              "REMAIN_DROP", "FINAL_CONTENT_ASSETS", "review_required", "restore_breakdown", "qa"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
