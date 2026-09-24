"""LIFE_ETHICS V4 strict curriculum / answer-logic audit 결과 조립 (1회성).

판정은 V3 109문항 전부와 시민 불복종 정책 갱신에 따른 재검토 대상(V3 correction DROP 및 KICE 재검토 DROP 중
시민 불복종 문항)을 원문으로 읽고, "정답을 결정하는 내용 자체가 공식 통합사회([별책7]) 또는 KICE 2028 예시에서
실제로 확인되는가(A) + 그 내용이 실제 정답 결정 논리인가(B)"를 사람이 판단한 의미 판정이다.
기록: data/phase2/LIFE_ETHICS/v4_strict_audit/v4_strict_audit_decisions.txt (키워드 판정 없음, 기존 파일 읽기 전용)

출력(data/phase2/LIFE_ETHICS/v4_strict_audit/):
  v4_strict_audit.jsonl, v4_final_content_asset_index.jsonl, v4_strict_audit_summary.json
"""
from __future__ import annotations

import collections
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "data/phase2/LIFE_ETHICS"
OUT = P2 / "v4_strict_audit"
DEC = OUT / "v4_strict_audit_decisions.txt"
V3 = P2 / "final_logic_correction/final_content_asset_index_v3.jsonl"
MASTER = ROOT / "reference/curriculum/integrated_social_official_scope.json"
ADAPT = {"N": "NONE", "L": "LIGHT", "S": "SUBSTANTIAL"}
REASONS = {"NO_OFFICIAL_CURRICULUM_SUPPORT", "TOPIC_OVERLAP_ONLY", "PHILOSOPHER_SPECIFIC_CONTENT", "OUTSIDE_KICE_DEPTH",
           "ANSWER_LOGIC_OUTSIDE_SCOPE", "SUBSTANTIAL_REWRITE_REQUIRED", "OTHER"}
THEMES = ["KUNG", "CONFUCIUS", "CIVIL_DISOBEDIENCE", "RAWLS_NOZICK", "PUNISHMENT", "ENVIRONMENT", "PEACE_VIOLENCE", "OTHER_NAMED"]
KICE_Q = {"Q1", "Q2", "Q4", "Q8", "Q12", "Q13", "Q14", "Q15", "Q16", "Q17", "Q18", "Q23"}


def tracked_hashes():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    return {f.decode(): hashlib.sha256((ROOT / f.decode()).read_bytes()).hexdigest()
            for f in files if f and (ROOT / f.decode()).exists()}


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def main():
    before = tracked_hashes()
    std_codes = {s["code"] for a in json.load(open(MASTER, encoding="utf-8"))["areas"] for s in a["standards"]}
    v3 = {r["question_id"]: r for r in jl(V3)}
    corr = {r["question_id"]: r for r in jl(P2 / "final_logic_correction/final_logic_correction.jsonl")}
    rc = {r["question_id"]: r for r in jl(P2 / "kice_2028_reconsideration.jsonl")}
    audit = {r["question_id"]: r for r in jl(P2 / "official_curriculum_final_audit.jsonl")}

    def prev_status(q):
        if q in v3:
            return "V3_KEEP"
        if q in corr and corr[q]["final_decision"] == "DROP":
            return "V3_LOGIC_CORRECTION_DROP"
        if q in rc and rc[q]["reconsideration_decision"] == "DROP":
            return "KICE_RECONSIDERATION_DROP"
        raise AssertionError(q)

    recs, seen = [], set()
    for line in open(DEC, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("|")
        assert len(f) == 12, line[:80]
        ids, d, theme, logic, evid, tov, oos, adapt, reasons, rationale, rr, prev_tag = f
        assert d in ("K", "D") and adapt in ADAPT and tov in ("Y", "N") and theme in THEMES + ["NONE"]
        rs = [x for x in reasons.split(";") if x]
        assert set(rs) <= REASONS, rs
        ev = []
        for item in [x.strip() for x in evid.split(";;") if x.strip()]:
            src, ref, concept = item.split(":", 2)
            assert src in ("CURRICULUM", "KICE_SAMPLE"), src
            if src == "CURRICULUM":
                assert ref.split()[0] in std_codes, ref
            else:
                assert ref.split()[1] in KICE_Q, ref
            ev.append({"source": src, "reference": ref, "supported_concept": concept})
        for short in ids.split(","):
            y, e, q = short.strip().split("_")
            qid = f"{y}_{e}_LIFE_ETHICS_{q}"
            assert qid not in seen, qid
            seen.add(qid)
            ps = prev_status(qid)
            assert (prev_tag == "V3_KEEP") == (ps == "V3_KEEP"), (qid, prev_tag, ps)
            rrs = [x for x in rr.split(";") if x]
            recs.append({
                "question_id": qid,
                "previous_status": ps,
                "audit_theme": theme,
                "core_answer_logic": logic,
                "official_content_match": d == "K",
                "official_evidence": ev if d == "K" else [],
                "topical_reference": ev if d == "D" else [],
                "topic_overlap_only": tov == "Y",
                "requires_out_of_scope_knowledge": bool(oos),
                "out_of_scope_knowledge": oos,
                "adaptation_required": ADAPT[adapt],
                "final_decision": {"K": "KEEP", "D": "DROP"}[d],
                "civil_disobedience_policy_restore": ps != "V3_KEEP" and d == "K",
                "drop_reasons": rs,
                "rationale": rationale,
                "review_required": bool(rrs),
                "review_reasons": rrs,
            })
    recs.sort(key=lambda r: r["question_id"])

    keep = [r for r in recs if r["final_decision"] == "KEEP"]
    drop = [r for r in recs if r["final_decision"] == "DROP"]
    restored = [r for r in keep if r["civil_disobedience_policy_restore"]]

    def first(r, src):
        return next((e for e in r["official_evidence"] if e["source"] == src), None)

    index = []
    for r in keep:
        q = r["question_id"]
        cur = first(r, "CURRICULUM")
        kice = first(r, "KICE_SAMPLE")
        index.append({
            "question_id": q,
            "source": v3[q]["source"] if q in v3 else "CIVIL_DISOBEDIENCE_POLICY_RESTORED",
            "review_required": r["review_required"] or bool(v3.get(q, {}).get("review_required")),
            "primary_curriculum_code": cur["reference"].split()[0] if cur else v3[q]["primary_curriculum_code"],
            "kice_sample_link": {"sample_question": kice["reference"].split()[1], "concept": kice["supported_concept"]} if kice else None,
            "adaptation_required": r["adaptation_required"],
            "v4_basis": "V3_KEEP_CONFIRMED" if q in v3 else "CIVIL_DISOBEDIENCE_POLICY_RESTORE",
        })

    def dump(p, rows):
        with open(p, "w", encoding="utf-8") as fo:
            for x in rows:
                fo.write(json.dumps(x, ensure_ascii=False) + "\n")

    dump(OUT / "v4_strict_audit.jsonl", recs)
    dump(OUT / "v4_final_content_asset_index.jsonl", index)

    # ---------------- QA ----------------
    ids_in = set(v3)
    ids_rev = [r["question_id"] for r in recs]
    ids_out = [r["question_id"] for r in index]
    after = tracked_hashes()
    qa = {
        "input_ids_v3": len(ids_in),
        "v3_all_reviewed": ids_in <= set(ids_rev),
        "reviewed_total": len(recs),
        "duplicates": len(ids_rev) - len(set(ids_rev)) + len(ids_out) - len(set(ids_out)),
        "output_ids": len(ids_out),
        "new_ids_outside_v3_or_cd_policy": len(set(ids_out) - ids_in - {r["question_id"] for r in restored}),
        "restored_all_civil_disobedience": all(r["audit_theme"] == "CIVIL_DISOBEDIENCE" for r in restored),
        "keep_without_official_evidence": sum(1 for r in keep if not (r["official_content_match"] and r["official_evidence"]
                                                                     and r["core_answer_logic"])),
        "keep_with_topic_overlap_only": sum(1 for r in keep if r["topic_overlap_only"]),
        "keep_adaptation_not_none_light": sum(1 for r in keep if r["adaptation_required"] not in ("NONE", "LIGHT")),
        "drop_without_reason": sum(1 for r in drop if not (r["core_answer_logic"] and r["drop_reasons"] and r["rationale"])),
        "drop_contamination": len(set(ids_out) & {r["question_id"] for r in drop}),
        "output_eq_v3_minus_drop_plus_restore": len(index) == len(ids_in) - len(drop) + len(restored),
        "existing_files_modified": sum(1 for k, v in before.items() if after.get(k) != v),
    }
    assert qa["v3_all_reviewed"] and qa["restored_all_civil_disobedience"] and qa["output_eq_v3_minus_drop_plus_restore"]
    assert all(qa[k] == 0 for k in ("duplicates", "new_ids_outside_v3_or_cd_policy", "keep_without_official_evidence",
                                    "keep_with_topic_overlap_only", "keep_adaptation_not_none_light", "drop_without_reason",
                                    "drop_contamination", "existing_files_modified")), qa

    special = {}
    for t in THEMES:
        rs = [r for r in recs if r["audit_theme"] == t]
        special[t] = {"reviewed": len(rs), "KEEP": sum(r["final_decision"] == "KEEP" for r in rs),
                      "DROP": sum(r["final_decision"] == "DROP" for r in rs),
                      "restored": sum(r["civil_disobedience_policy_restore"] for r in rs)}
    summary = {
        "base": {"v3_input": len(ids_in), "v3_freeze_commit": "3a22b7f",
                 "cd_policy_rereview": len(recs) - len(ids_in)},
        "reviewed": len(recs),
        "v3_keep_confirmed": sum(1 for r in keep if r["previous_status"] == "V3_KEEP"),
        "v3_drop": len(drop),
        "civil_disobedience_policy_restore": {"count": len(restored), "question_ids": [r["question_id"] for r in restored],
                                              "by_previous_status": dict(collections.Counter(r["previous_status"] for r in restored))},
        "final_v4": {"KEEP": len(index), "composition": dict(collections.Counter(r["source"] for r in index)),
                     "adaptation": dict(collections.Counter(r["adaptation_required"] for r in index)),
                     "review_required": sum(r["review_required"] for r in index)},
        "drop_reason_counts": {k: sum(k in r["drop_reasons"] for r in drop) for k in sorted(REASONS)},
        "special_audit": special,
        "qa": qa,
        "official_sources": ["reference/curriculum/2022개정_사회과_별책7.pdf (MASTER integrated_social_official_scope.json)",
                             "reference/2028_예시문항_안내.pdf", "reference/2028_통합사회_예시문항.pdf",
                             "data/phase2/LIFE_ETHICS/kice_2028_sample_depth_reference.json"],
        "method": "V3 109문항 전부(4 batch)와 시민 불복종 정책 갱신 재검토 대상(Phase 2C KEEP 261 내 V3 미포함 시민 불복종 문항)을 "
                  "발문·제시문·선지로 판독. Exact concept test(Q1~Q5)로 정답 결정 명제가 공식 자료에 실제로 있는지 확인. "
                  "제시문만으로 풀림·사상가명 불필요·넓은 주제 유사는 KEEP 근거로 쓰지 않음. 시민 불복종만 정책상 비교형도 KEEP. "
                  "외부 API·OCR·재분류 없음.",
        "note": "시민 불복종 정책 재검토 범위는 Phase 2C KEEP 261 안에서 V3에 없는 시민 불복종 문항(21)이며, "
                "Phase 2C 이전 단계에서 제외된 문항은 재분류 금지 원칙에 따라 검토하지 않음.",
    }
    json.dump(summary, open(OUT / "v4_strict_audit_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ("reviewed", "v3_keep_confirmed", "v3_drop", "civil_disobedience_policy_restore",
                                              "final_v4", "drop_reason_counts", "special_audit", "qa")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
