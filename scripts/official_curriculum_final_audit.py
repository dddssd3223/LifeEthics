"""LIFE_ETHICS Phase 2C KEEP 261문항의 공식 교육과정(2022 개정 [별책7]) 최종 범위 감사 결과 조립 (1회성).

판정은 문항 원문(선지 포함)과 reference/curriculum/integrated_social_official_scope.json 을 대조해 사람이 내린
의미 판정이며 data/phase2/LIFE_ETHICS/official_curriculum_final_audit_decisions.txt 에 기록돼 있다.
이 스크립트는 그 기록을 스키마에 맞춰 조립·검증만 한다(키워드 판정 없음). 기존 Phase 1/2 파일은 읽기만 한다.

출력: data/phase2/LIFE_ETHICS/official_curriculum_final_audit.jsonl, official_curriculum_final_audit_summary.json
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "data/phase2/LIFE_ETHICS"
MASTER = ROOT / "reference/curriculum/integrated_social_official_scope.json"
DEC = P2 / "official_curriculum_final_audit_decisions.txt"
EVT = {"ST": "STANDARD_TEXT", "EX": "EXPLANATION", "CE": "CONTENT_ELEMENT", "AC": "APPLICATION_CONSIDERATION"}
CAT = {
    "PHIL_JUSTICE": "detailed philosopher knowledge (justice: Rawls/Nozick etc.)",
    "CD_DETAIL": "detailed civil disobedience theory (Rawls/Singer/Thoreau)",
    "PUNISH": "detailed punishment theory (Kant/Beccaria/Rousseau/Bentham)",
    "ENV_DETAIL": "detailed environmental ethics (Singer/Regan/Taylor/Leopold/Kant)",
    "IR_THEORY": "international relations / war ethics / Kant perpetual peace details",
    "INFO_ETHICS": "information ethics (ownership, cyber self, discourse ethics)",
    "OCCUPATIONAL_ETHICS": "occupational ethics",
    "OTHER_OUT_OF_SCOPE": "other out-of-scope content",
}


def main():
    master = json.load(open(MASTER, encoding="utf-8"))
    std_codes = {s["code"] for a in master["areas"] for s in a["standards"]}
    area_of = {s["code"]: a["area_name"] for a in master["areas"] for s in a["standards"]}

    cur = [json.loads(l) for l in open(P2 / "final_curation.jsonl", encoding="utf-8")]
    prev = {r["question_id"]: r["final_decision"] for r in cur}
    keep_ids = sorted(q for q, d in prev.items() if d == "KEEP")

    recs = []
    seen = set()
    for line in open(DEC, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("|")
        assert len(f) == 8, line[:80]
        short, d, cat, req, evid, oos, rationale, rr = f
        y, e, q = short.split("_")
        qid = f"{y}_{e}_LIFE_ETHICS_{q}"
        assert qid not in seen, qid
        seen.add(qid)
        ev = []
        for item in [x.strip() for x in evid.split(";;") if x.strip()]:
            code, typ, reason = item.split(":", 2)
            code = f"10통사{code}"
            assert code in std_codes, (qid, code)
            ev.append({"standard_code": code, "evidence_type": EVT[typ], "reason": reason})
        rrs = [x.strip() for x in rr.split(";") if x.strip()]
        recs.append({
            "question_id": qid,
            "previous_decision": prev.get(qid),
            "final_audit_decision": {"K": "KEEP", "D": "DROP"}[d],
            "required_content": [x.strip() for x in req.split(";") if x.strip()],
            "curriculum_evidence": ev if d == "K" else [],
            "out_of_scope_dependency": [x.strip() for x in oos.split(";") if x.strip()],
            "drop_reason_category": cat or None,
            "rationale": rationale.strip(),
            "review_required": bool(rrs),
            "review_reasons": rrs,
        })
    recs.sort(key=lambda r: r["question_id"])

    # ---------------- QA ----------------
    ids = [r["question_id"] for r in recs]
    dec = collections.Counter(r["final_audit_decision"] for r in recs)
    qa = {
        "A_phase2c_keep_equals_audit_input": len(keep_ids) == len(recs),
        "B_keep_plus_drop_equals_input": dec["KEEP"] + dec["DROP"] == len(recs),
        "C_duplicate_ids": len(ids) - len(set(ids)),
        "D_all_previous_keep": all(r["previous_decision"] == "KEEP" for r in recs) and set(ids) == set(keep_ids),
        "E_non_keep_contamination": sum(1 for r in recs if prev.get(r["question_id"]) != "KEEP"),
        "F_keep_without_evidence": sum(1 for r in recs if r["final_audit_decision"] == "KEEP" and not r["curriculum_evidence"]),
        "G_drop_without_exclusion_reason": sum(
            1 for r in recs if r["final_audit_decision"] == "DROP"
            and not (r["out_of_scope_dependency"] and r["drop_reason_category"] and r["rationale"])),
    }
    assert qa["A_phase2c_keep_equals_audit_input"] and qa["B_keep_plus_drop_equals_input"] and qa["D_all_previous_keep"]
    assert qa["C_duplicate_ids"] == qa["E_non_keep_contamination"] == qa["F_keep_without_evidence"] == qa["G_drop_without_exclusion_reason"] == 0

    with open(P2 / "official_curriculum_final_audit.jsonl", "w", encoding="utf-8") as fo:
        for r in recs:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")

    keep = [r for r in recs if r["final_audit_decision"] == "KEEP"]
    drop = [r for r in recs if r["final_audit_decision"] == "DROP"]
    by_std = collections.Counter(e["standard_code"] for r in keep for e in {x["standard_code"]: x for x in r["curriculum_evidence"]}.values())
    primary = collections.Counter(r["curriculum_evidence"][0]["standard_code"] for r in keep)
    ev_types = collections.Counter(e["evidence_type"] for r in keep for e in r["curriculum_evidence"])
    summary = {
        "source_of_truth": master["source"],
        "previous_phase2c_keep": len(keep_ids),
        "audited": len(recs),
        "KEEP": dec["KEEP"],
        "DROP": dec["DROP"],
        "review_required": sum(r["review_required"] for r in recs),
        "review_required_by_decision": dict(collections.Counter(r["final_audit_decision"] for r in recs if r["review_required"])),
        "keep_by_standard_any_evidence": {f"{k} {area_of[k]}": v for k, v in sorted(by_std.items(), key=lambda x: -x[1])},
        "keep_by_primary_standard": {f"{k} {area_of[k]}": v for k, v in sorted(primary.items(), key=lambda x: -x[1])},
        "keep_evidence_type_counts": dict(ev_types),
        "drop_by_reason": {f"{k}: {CAT[k]}": v for k, v in collections.Counter(r["drop_reason_category"] for r in drop).most_common()},
        "qa": qa,
        "method": "Phase 2C KEEP 261문항을 선지 포함 원문으로 20~30문항 batch(pilot 30 → 나머지 231) 판독, "
                  "공식 교육과정 MASTER(성취기준·해설·고려 사항·내용 요소)와 대조한 의미 판정. 외부 API 0회, 신규 OCR 없음.",
        "note": "기존 Phase 2C 판정·FINAL_TRANSFER는 변경하지 않은 별도 audit layer.",
    }
    json.dump(summary, open(P2 / "official_curriculum_final_audit_summary.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ["audited", "KEEP", "DROP", "review_required", "qa"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
