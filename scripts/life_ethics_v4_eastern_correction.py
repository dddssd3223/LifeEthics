"""LIFE_ETHICS V4 patched — 동양 사상 과잉 복구 표적 교정 (1회성). FINAL_TRANSFER는 만들지 않는다.

대상: v4_targeted_recovery.jsonl 중 decision=RESTORE 이고 (reuse_scope=PARTIAL 또는 review_required) 이며
      판정 기록(사상가·핵심 내용)에 동양 사상 신호가 있는 문항만(8). 신호는 후보 추출용이며 판정은 원문을 읽은 사람의 판단.
출력: data/phase2/LIFE_ETHICS/v4_strict_audit/v4_eastern_thought_correction.jsonl
      data/phase2/LIFE_ETHICS/v4_strict_audit/v4_final_content_asset_index_corrected.jsonl (= patched 317 - DROP)
기존 파일(patched index·recovery 등)은 읽기만 한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUD = ROOT / "data/phase2/LIFE_ETHICS/v4_strict_audit"
REC = AUD / "v4_targeted_recovery.jsonl"
PATCHED = AUD / "v4_final_content_asset_index_patched.jsonl"
OUT_C = AUD / "v4_eastern_thought_correction.jsonl"
OUT_I = AUD / "v4_final_content_asset_index_corrected.jsonl"
SIGNAL = r"공자|맹자|유교|유가|석가|불교|노자|장자|도가|도교|동양|심재|좌망|무위|상선약수|수기|치인|거경|사단|삼독|연기|자타불이|열반|불성"

E_HAP = {"자료1_교육과정": "10통사1-02-01 고려 사항 '행복에 대한 다양한 관점을 고전…'(유·불·도 개념 명시 없음) — PARTIAL",
         "자료2_2028예시문항안내": "Q1 해설은 아리스토텔레스·에피쿠로스 등 서양 사상가 관점만 예시 — 동양 수양론 근거 없음",
         "자료3_28예시": "Q1은 고대 서양 사상가(아리스토텔레스·에피쿠로스)만 출제 — 유·불·도 개념 귀속 평가 없음",
         "자료4_최소성취수준": "'행복의 기준이 다를 수 있음을 안다' 수준 — 동양 사상 개념 없음"}
E_NAT = {"자료1_교육과정": "10통사1-03-02: 인간 중심주의와 생태 중심주의 중심의 '자연에 대한 다양한 관점'(동양 자연관 명시 없음) — PARTIAL",
         "자료2_2028예시문항안내": "부록 Q4 ↔ 1-03-02(인간·생태 중심 비교) — 동양 자연관 근거 없음",
         "자료3_28예시": "Q4는 베이컨 vs 레오폴드만 평가 — 유·불·도 자연관 없음",
         "자료4_최소성취수준": "'인간중심주의 또는 생태중심주의를 사례를 통해 파악' — 동양 자연관 없음"}

DECISIONS = {
    "2014_CSAT_LIFE_ETHICS_Q16": dict(
        area="동양 행복관(도가)", decision="DROP",
        logic="노자 제시문에 맞는 (가)·(나) 진술 쌍을 고르는 문제로, 오답 쌍의 예(禮)·옳고 그름 구분(유교), 연기·탐욕과 집착(불교), 사단 확충·거경(유교), 자타불이 등을 각 사상에 귀속시켜 배제해야 정답이 결정됨",
        req=["노자 무위·상선약수", "예·사단·거경의 유교 귀속", "연기·탐욕과 집착의 불교 귀속", "자타 한몸 관념의 귀속"],
        reuse=["인위적 가치를 버리고 자연의 흐름과 하나 되는 삶이라는 제시문 논지"],
        spec=["예", "사단", "거경", "연기", "자타불이", "무위"], ev=E_HAP,
        why="사용자 확인 사례 1과 같은 구조. 통합사회와는 '바람직한 삶'이라는 넓은 주제로만 연결되고 정답 결정은 유·불·도 세부 개념 귀속에 달려 있음."),
    "2014_JUNE_LIFE_ETHICS_Q12": dict(
        area="동양 이상적 인간상(유·불·도)", decision="DROP",
        logic="흐름도에서 유교(수기치인)·불교(삼독 제거·중생 제도)·도가(심재·상선약수)의 이상적 인간상을 판단 질문으로 구분하는 문제",
        req=["심재(장자)", "수기치인(유교)", "삼독(불교)", "상선약수(노자)", "각 학파의 이상적 인간상"],
        reuse=["사상마다 이상적 삶의 모습이 다르다는 일반 논지(문항 내용 재사용은 거의 불가)"],
        spec=["심재", "수기치인", "삼독", "상선약수"], ev=E_HAP,
        why="사용자 확인 사례 2와 같은 구조. 핵심 정답 논리가 동양 수양론·전문 개념 식별이며 통합사회 행복 단원 내용으로 대체되지 않음."),
    "2015_CSAT_LIFE_ETHICS_Q03": dict(
        area="동양 행복관(도가)", decision="DROP",
        logic="노자의 물 비유 제시문에 맞는 삶의 태도를 고르는 문제로, 오답인 예에 따른 본성 회복(유교), 연기와 자비(불교), 신독·거경(유교)을 각 사상에 귀속시켜 배제해야 함",
        req=["노자 무욕·소박한 삶", "예·신독·거경의 유교 귀속", "연기·자비의 불교 귀속"],
        reuse=["다투지 않고 무욕의 소박한 삶을 추구하는 제시문 논지"],
        spec=["상선약수", "예", "신독", "거경", "연기"], ev=E_HAP,
        why="사례 1과 같은 구조. 선지 대부분이 유·불·도 세부 수양 개념의 귀속 판별이라 통합사회 원천으로 재사용할 부분이 제시문 한 줄에 그침."),
    "2021_JUNE_LIFE_ETHICS_Q02": dict(
        area="동양 행복관(유교·도가)", decision="DROP",
        logic="(가) 유교 수기안인·경과 (나) 노자 무위를 식별한 뒤, 사회적 지위에 따른 예의 규범 중시(유교 긍정·도가 부정)를 연기·만물 평등 등 다른 선지와 구별하는 문제",
        req=["수기안인·경(거경)의 유교 귀속", "무위(도가)", "예의 유교 귀속", "연기의 불교 귀속"],
        reuse=["수양과 인위 거부라는 대비 구도(일반 수준)"],
        spec=["수기안인", "거경", "무위", "예", "연기"], ev=E_HAP,
        why="정답이 유교·도가·불교 개념 귀속에 의존하며 통합사회 행복 관점으로는 선지를 판별할 수 없음."),
    "2015_SEPTEMBER_LIFE_ETHICS_Q11": dict(
        area="동양 자연관(도가·유교)", decision="DROP",
        logic="도가(천지불인)와 유교(천명·성·도·교)의 자연관을 식별해 '하늘이 인과 같은 덕의 근원'(유교)을 고르고 기계론·목적론·연기적 관점 선지를 배제하는 문제",
        req=["천지불인(노자)", "천명지위성(중용)", "하늘을 덕의 근원으로 보는 유교 형이상학", "연기의 불교 귀속"],
        reuse=["자연을 바라보는 관점이 사상마다 다르다는 일반 논지"],
        spec=["천지불인", "천명", "성·도·교", "연기"], ev=E_NAT,
        why="통합사회 자연관(인간 중심 vs 생태 중심)과 달리 동양 형이상학적 개념 귀속이 핵심이라 자연관 단원 원천으로의 재사용 가치가 낮음."),
    "2016_JUNE_LIFE_ETHICS_Q04": dict(
        area="동양 자연관(불교)", decision="DROP",
        logic="공·자성 제시문을 불교로 식별하고 자연을 '인연에 따라 생멸하는 관계의 그물(연기)'로 보는 선지를 예법 근거(유교)·무위 체계(도가) 등과 구별하는 문제",
        req=["공·자성 개념", "연기(인연 생멸)", "예법 근거로서의 자연(유교)", "무위 체계(도가)"],
        reuse=["자연을 인간의 도구적 가치 총체로 보는 인간 중심 관점 오답 선지 1개"],
        spec=["공", "자성", "연기", "해탈", "무위"], ev=E_NAT,
        why="정답이 불교 연기 개념의 식별에 의존. 인간 중심 오답 선지 하나를 빼면 통합사회 자연관과 직접 연결되는 내용이 없음."),
    "2018_CSAT_LIFE_ETHICS_Q12": dict(
        area="맹자 직업·분업관(자본주의 비교)", decision="KEEP",
        logic="맹자(노심·노력 분업, 항산과 항심)와 마르크스(자본주의 분업에서 노동자의 예속·소외)의 직업 노동관 비교 서술형에서 옳지 않은 서술을 고르는 문제",
        req=["제시문에 주어진 맹자 분업·항산론", "마르크스의 자본주의 분업·노동 소외 비판"],
        reuse=["자본주의 분업과 노동 소외에 대한 사상가 주장(마르크스)", "생업 안정과 삶의 기반(항산)"],
        spec=["노심·노력의 분업(제시문으로 제공)"],
        ev={"자료1_교육과정": "10통사2-03-01 해설: 자본주의의 역사적 전개와 특징을 사상가들의 주장을 통해 다룸 — YES",
            "자료2_2028예시문항안내": "Q17 해설: 사상가들의 주장을 담은 자료 탐구 — YES",
            "자료3_28예시": "Q17 자본주의 전개(중상주의) — PARTIAL",
            "자료4_최소성취수준": "'자본주의의 전개 과정을 파악' — YES"},
        why="동양 사상 세부 식별 문항이 아님. 맹자 측 논지는 제시문으로 주어지고 정답 결정의 중심은 자본주의 분업·노동 소외라는 2-03-01 사상가 주장 내용."),
    "2018_JUNE_LIFE_ETHICS_Q08": dict(
        area="장자·에피쿠로스 죽음관(행복)", decision="KEEP",
        logic="장자(삶과 죽음은 자연의 과정, 분별에서 벗어남)와 에피쿠로스(죽음으로 감각이 소멸하므로 두려워할 필요 없음, 즐거운 시간의 향유)의 입장 중 옳지 않은 것(불멸 욕망을 벗어날 근거가 내세의 행복이라는 진술)을 고르는 문제",
        req=["제시문에 주어진 장자 생사관", "에피쿠로스의 쾌락·평정 행복관과 죽음관"],
        reuse=["에피쿠로스 행복관(쾌락과 두려움으로부터의 자유)", "죽음을 두려워하지 않는 삶의 태도"],
        spec=["장자 진인(제시문으로 제공)"],
        ev={"자료1_교육과정": "10통사1-02-01 행복의 기준·다양한 관점(고전) — PARTIAL",
            "자료2_2028예시문항안내": "Q1 해설: 에피쿠로스 사상을 중심으로 행복의 의미 이해 — YES",
            "자료3_28예시": "Q1 을(에피쿠로스) 행복관 평가 — YES",
            "자료4_최소성취수준": "행복의 기준이 다를 수 있음 — PARTIAL"},
        why="정답이 에피쿠로스 입장 판별로 결정되며 에피쿠로스는 28예시 Q1에서 직접 평가된 사상가. 장자 선지는 제시문으로 판단 가능해 유·불·도 개념 귀속 문제가 아님."),
}


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def tracked_hashes():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    return {f.decode(): hashlib.sha256((ROOT / f.decode()).read_bytes()).hexdigest()
            for f in files if f and (ROOT / f.decode()).exists() and (ROOT / f.decode()) not in (OUT_C, OUT_I)}


def main():
    before = tracked_hashes()
    rec = {r["question_id"]: r for r in jl(REC)}
    patched = jl(PATCHED)
    cands = sorted(q for q, r in rec.items() if r["decision"] == "RESTORE" and (r["reuse_scope"] == "PARTIAL" or r["review_required"])
                   and re.search(SIGNAL, " ".join(r["philosophers"]) + " " + r["core_content"] + " " + (r["통합사회에서_활용가능한_내용"] or "")))
    assert cands == sorted(DECISIONS), (cands, sorted(DECISIONS))

    out = []
    for q in cands:
        r, d = rec[q], DECISIONS[q]
        out.append({
            "question_id": q, "previous_decision": "RESTORE", "previous_reuse_scope": r["reuse_scope"],
            "previous_review_required": r["review_required"], "previous_target_area": r["target_area"],
            "eastern_thought_area": d["area"], "core_question_logic": d["logic"],
            "required_knowledge_for_answer": d["req"], "integrated_social_reusable_content": d["reuse"],
            "specialized_eastern_ethics_content": d["spec"], "source_evidence": d["ev"],
            "final_decision": d["decision"], "rationale": d["why"],
        })
    with open(OUT_C, "w", encoding="utf-8") as fo:
        for x in out:
            fo.write(json.dumps(x, ensure_ascii=False) + "\n")
    drop = {x["question_id"] for x in out if x["final_decision"] == "DROP"}
    corrected = [r for r in patched if r["question_id"] not in drop]
    with open(OUT_I, "w", encoding="utf-8") as fo:
        for x in corrected:
            fo.write(json.dumps(x, ensure_ascii=False) + "\n")

    # ---- QA ----
    ids_p = [r["question_id"] for r in patched]
    ids_c = [r["question_id"] for r in corrected]
    v4 = {r["question_id"] for r in jl(AUD / "v4_final_content_asset_index.jsonl")}
    all_ids = {p.stem for p in (ROOT / "data/questions/json").glob("*.json")}
    by_area = lambda ids: {a: sum(1 for q in ids if q in rec and rec[q]["decision"] == "RESTORE" and rec[q]["target_area"] == a)
                           for a in ["JUSTICE", "CIVIL_DISOBEDIENCE", "PUNISHMENT", "ENVIRONMENT", "PEACE", "AID", "CULTURE", "HAPPINESS"]}
    pa, ca = by_area(set(ids_p) - set(cands)), by_area(set(ids_c) - set(cands))
    after = tracked_hashes()
    qa = {
        "patched_count": len(ids_p), "original_v4_keep_present": len(v4 & set(ids_c)), "original_v4_keep_loss": len(v4 - set(ids_c)),
        "targets": len(cands), "targets_all_restored_eastern": all(rec[q]["decision"] == "RESTORE" for q in cands),
        "new_restore": len(set(ids_c) - set(ids_p)), "drop": len(drop), "corrected_count": len(ids_c),
        "corrected_eq_patched_minus_drop": len(ids_c) == len(ids_p) - len(drop),
        "non_target_changed": len((set(ids_p) - set(cands)) ^ (set(ids_c) - set(cands))),
        "non_target_area_counts_unchanged": pa == ca,
        "duplicates": len(ids_c) - len(set(ids_c)), "unknown_ids": len(set(ids_c) - all_ids),
        "existing_files_modified": sum(1 for k, v in before.items() if after.get(k) != v),
    }
    assert qa["patched_count"] == 317 and qa["original_v4_keep_loss"] == 0 and qa["new_restore"] == 0
    assert qa["corrected_eq_patched_minus_drop"] and qa["non_target_changed"] == 0 and qa["non_target_area_counts_unchanged"]
    assert qa["duplicates"] == qa["unknown_ids"] == qa["existing_files_modified"] == 0, qa
    print(json.dumps({"qa": qa, "non_target_restore_by_area": ca, "drop_ids": sorted(drop)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
