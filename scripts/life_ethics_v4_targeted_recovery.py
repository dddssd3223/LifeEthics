"""LIFE_ETHICS V4 표적 누락(false negative) 복구 감사 결과 조립 (1회성). FINAL_TRANSFER는 만들지 않는다.

- 기준점: data/phase2/LIFE_ETHICS/v4_strict_audit/v4_final_content_asset_index.jsonl (V4 KEEP 전부 보호, 재판정 없음)
- 후보: V4 KEEP이 아닌 문항 중 기존 metadata·Phase 1 텍스트의 표적 영역 신호(행복·환경·시민 불복종·사회 정의·
  교정적 정의·평화·해외 원조·문화/다문화)로 찾은 문항(1차) + 핵심 개념 COVERAGE_GAP에 한정한 1회 추가 검색(2차).
  키워드는 후보 검색용일 뿐이며 판정은 문항 원문을 읽은 사람의 의미 판정
  (data/phase2/LIFE_ETHICS/v4_strict_audit/v4_targeted_recovery_decisions.txt).
- 4개 자료(자료1 [별책7] / 자료2 2028 예시문항 안내 / 자료3 28예시 실제 문항 / 자료4 최소 성취수준 자료)를
  문항마다 evidence profile로 대조해 기록한다.

출력(data/phase2/LIFE_ETHICS/v4_strict_audit/):
  v4_targeted_recovery.jsonl, v4_targeted_recovery_summary.json, v4_core_coverage_after_recovery.json,
  v4_final_content_asset_index_patched.jsonl
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "data/phase2/LIFE_ETHICS"
AUD = P2 / "v4_strict_audit"
DEC = AUD / "v4_targeted_recovery_decisions.txt"
V4 = AUD / "v4_final_content_asset_index.jsonl"
NEW_FILES = {AUD / n for n in ("v4_targeted_recovery.jsonl", "v4_targeted_recovery_summary.json",
                               "v4_core_coverage_after_recovery.json", "v4_final_content_asset_index_patched.jsonl",
                               "v4_targeted_recovery_decisions.txt")}
SOURCES = {
    "자료1_교육과정": ROOT / "reference/curriculum/2022개정_사회과_별책7.pdf",
    "자료2_2028예시문항안내": ROOT / "reference/2028_예시문항_안내.pdf",
    "자료3_28예시실제문항": ROOT / "reference/2028_통합사회_예시문항.pdf",
    "자료4_최소성취수준": ROOT / "reference/KICE_2022개정_최소성취수준_통합사회1_2.pdf",
}
# 각 자료에서 근거 문구가 실제로 존재하는지 로드 시 검증(공백 제거 후 포함 여부)
ANCHORS = {
    "자료1_교육과정": ["인간중심주의와생태중심주의", "교정적정의", "시민불복종", "소극적평화와적극적평화", "문화병존,문화융합,문화동화",
                   "자유주의적정의관과공동체주의적정의관", "빈부격차의심화", "행복에대한다양한관점", "사상가들의주장"],
    "자료2_2028예시문항안내": ["아리스토텔레스", "에피쿠로스", "10통사1-03-02", "10통사2-02-01", "10통사2-04-02", "10통사1-04-04"],
    "자료3_28예시실제문항": ["적어도한사람이긍정할진술", "시민불복종은공유된정의관", "원초적입장의사람들", "교정적정의에부합하지",
                     "문화적폭력은직접적폭력의정당화", "다문화주의정책", "문화융합", "국제법을바탕으로가입국간합의"],
    "자료4_최소성취수준": ["인간중심주의또는생태중심주의", "자유주의적정의관과공동체주의적정의관", "문화상대주의", "다문화사회에서문화적다양성",
                    "국제사회의갈등과협력사례", "행복의기준이다를수있음", "자본주의의전개과정"],
}
Y, P, N = "YES", "PARTIAL", "NO"
PROFILES = {
    "ENV_CORE": [(Y, "10통사1-03-02 성취기준·해설: 인간 중심주의와 생태 중심주의를 중심으로 자연관 비교"),
                 (Y, "부록 연계: 예시 Q4 ↔ 10통사1-03-02"),
                 (Y, "Q4: 베이컨(자연 지배) vs 레오폴드(대지 윤리) '적어도 한 사람이 긍정할 진술' — 인간 중심 vs 생태 중심 사상가 비교를 실제 평가"),
                 (Y, "1-03 최소 성취수준: '인간중심주의 또는 생태중심주의를 사례를 통해 파악할 수 있다'")],
    "ENV_SPECTRUM": [(P, "10통사1-03-02: '자연에 대한 인간의 다양한 관점' 비교(명시 축은 인간 중심·생태 중심)"),
                     (P, "부록 연계: 예시 Q4 ↔ 10통사1-03-02(동물·생명 중심은 명시 없음)"),
                     (P, "Q4는 인간 중심 vs 생태 중심만 평가; 탈인간 중심 내부 구분은 미평가"),
                     (P, "최소 성취수준은 인간중심/생태중심 구분 수준")],
    "ENV_EAST": [(P, "10통사1-03-02: '자연에 대한 인간의 다양한 관점'(동양 자연관 명시 없음)"),
                 (N, "해당 없음"), (N, "해당 없음"), (N, "해당 없음")],
    "ENV_SUSTAIN": [(Y, "10통사1-03-03 생태시민 실천, 10통사2-05-02·2-05-03 지속가능한 발전·미래 세대"),
                    (P, "부록 연계: 환경·에너지 문항(Q2·Q25) ↔ 환경·지속가능성 성취기준"),
                    (P, "Q2 환경 문제 탐구, Q25 에너지 소비(책임 윤리 사상가는 미평가)"),
                    (P, "환경 문제 해결·지속가능 발전 최소 성취수준")],
    "PUN_CORE": [(Y, "10통사2-02-01 해설 '분배적 정의와 교정적 정의', 고려 사항 '공정한 법 집행을 통해 교정적 정의'"),
                 (Y, "부록 연계: 예시 Q16 ↔ 10통사2-02-01"),
                 (Y, "Q16: 살인범의 존엄성(응보) vs 공동체 선 증진 수단·고통이 해악을 능가(공리주의 예방), 사형과 교정적 정의 선지"),
                 (P, "2-02 최소 성취수준은 정의의 필요성 수준(교정적 정의 명시 없음)")],
    "PUN_SPECTRUM": [(Y, "10통사2-02-01 해설·고려 사항: 교정적 정의"), (Y, "부록 연계: 예시 Q16 ↔ 10통사2-02-01"),
                     (P, "Q16은 응보 vs 예방 대비까지; 루소 사회계약론적 사형 논거는 미평가"),
                     (P, "정의의 필요성 수준")],
    "JUS_CORE": [(Y, "10통사2-02-01 분배적 정의의 실질적 기준(업적·능력·필요), 10통사2-02-02 자유주의적·공동체주의적 정의관 비교"),
                 (Y, "부록 연계: Q15 ↔ 10통사2-02-02, Q13 해설 '분배적 정의의 실질적 기준 및 다양한 정의관'"),
                 (Y, "Q15: 롤스(원초적 입장·우연적 여건·가장 불리한 상황) vs 노직(취득·이전·교정) 벤다이어그램; Q13 업적·필요 분배"),
                 (Y, "2-02 최소 성취수준 '자유주의적 정의관과 공동체주의적 정의관을 구분할 수 있다'")],
    "JUS_COMM": [(Y, "10통사2-02-02 해설: 공동체주의적 정의관, 개인과 공동체의 관계, 공동선"),
                 (Y, "부록 연계: Q13 해설 '다양한 정의관'"), (Y, "Q13 ㉢: 공동체 구성원의 책임·의무 vs 독립적 자아(자유주의) 대비"),
                 (Y, "자유주의적/공동체주의적 정의관 구분·비교")],
    "JUS_CAPITAL": [(Y, "10통사2-03-01 해설: 자본주의의 역사적 전개와 특징을 '사상가들의 주장'을 통해 다룸"),
                    (Y, "Q17 해설: 사상가들의 주장을 담은 자료 탐구를 교수학습 주안점으로 명시"),
                    (P, "Q17: 중상주의 시기 사실 문항(사상가 비교는 없음)"),
                    (Y, "2-03 최소 성취수준 '자본주의의 전개 과정을 파악'")],
    "PEACE_CORE": [(Y, "10통사2-04-02 해설: 소극적 평화와 적극적 평화"), (Y, "부록 연계: 예시 Q18 ↔ 10통사2-04-02"),
                   (Y, "Q18: 직접적·구조적·문화적 폭력의 삼각형, 문화적 폭력의 정당화 기능, 평화적 전환"),
                   (Y, "2-04 최소 성취수준: 국제 사회의 갈등과 협력 사례를 통해 평화의 소중함")],
    "PEACE_KANT": [(P, "10통사2-04-02: 국제 사회 갈등·협력, 국가·국제기구 등 행위 주체의 역할(칸트 조항 명시 없음)"),
                   (P, "Q12 해설: 국제 사회 행위 주체(국제기구)의 개념과 역할"),
                   (P, "Q12: 국제법을 바탕으로 가입국 간 합의로 활동하는 국제기구(칸트 평화론 자체는 미평가)"),
                   (P, "행위 주체들의 바람직한 역할이 평화 유지에 중요함")],
    "PEACE_IR": [(P, "10통사2-04-02: 국제 사회의 갈등과 협력 사례, 행위 주체의 역할(국제 관계 이론 명칭 명시 없음)"),
                 (P, "Q12 해설: 국제기구·비정부 기구의 역할"), (P, "Q12 국제기구·국제법 기반 협력(현실주의 등 이론 명칭 미평가)"),
                 (P, "국제 사회의 갈등과 협력 사례 나열, 행위 주체 역할")],
    "AID_CORE": [(P, "10통사2-04-01 해설 '빈부 격차의 심화'와 해결 방안, 2-04-02 세계시민의 역할, 2-01-03 세계 인권 문제(해외 원조 명시 없음)"),
                 (P, "Q12 해설: 국제기구·비정부 기구, 사회적 소수자(난민)"), (P, "Q12 난민·국제기구 협력(원조 사상가 비교는 미평가)"),
                 (P, "세계화 시대 문제점과 국제 협력")],
    "CULT_MODEL": [(Y, "10통사1-04-04 다문화 사회·문화적 다양성 존중, 1-04-02 해설 문화 병존·융합·동화"),
                   (Y, "부록 연계: 예시 Q3 ↔ 10통사1-04-01·1-04-04, Q7 ↔ 1-04-02"),
                   (Y, "Q3: ㉠ 소수 문화 동화 정책 vs ㉡ 다문화주의 정책; Q7: 문화 융합·병존"),
                   (Y, "다문화 사회에서 문화적 다양성 존중, 문화 변동(동화·병존·융합) 최소 성취수준")],
    "CULT_CONFLICT": [(P, "10통사2-04-02 국제 사회의 갈등과 협력 사례, 1-04-04 문화적 다양성 존중(문명 충돌론 명시 없음)"),
                      (N, "해당 없음"), (P, "Q3 문화권, Q12 국제 협력(문명론 미평가)"), (P, "국제 사회의 갈등과 협력 사례")],
    "HAP_CORE": [(Y, "10통사1-02-01 행복의 기준, 고려 사항 '행복에 대한 다양한 관점을 고전…을 통해 균형 있게'"),
                 (Y, "Q1 해설: 아리스토텔레스·에피쿠로스, '다양한 사상가의 관점을 이론적으로 정확히 이해'"),
                 (Y, "Q1: 고대 서양 사상가 갑(아리스토텔레스)·을(에피쿠로스)의 행복관 적용"),
                 (P, "'행복의 기준이 다를 수 있음을 안다' 수준(사상가 없음)")],
    "HAP_EAST": [(P, "10통사1-02-01 고려 사항: 행복에 대한 다양한 관점을 고전 등으로(동양 사상 명시 없음)"),
                 (P, "Q1 해설: 사상가 관점의 행복 이해(서양 사상가만 예시)"), (P, "Q1은 서양 고대 사상가만 출제"),
                 (P, "행복의 기준이 다를 수 있음")],
    "D_OFFTOPIC": [(N, "해당 문항의 핵심 내용에 대응하는 성취기준·해설 없음"), (N, "해당 없음"), (N, "해당 없음"), (N, "해당 없음")],
    "D_BIOETHICS": [(N, "생명 윤리(복제·동물 실험) 논쟁은 성취기준·해설에 없음(1-03-02 자연관 비교와 쟁점이 다름)"),
                    (N, "해당 없음"), (N, "Q4 자연관 비교와 쟁점 불일치"), (N, "해당 없음")],
    "D_RELIGION": [(P, "10통사1-04-01 해설: 종교는 문화권 형성의 인문환경 요인으로만 등장(종교 간 대화·관용 없음)"),
                   (N, "해당 없음"), (P, "Q3 문화권의 종교 신자 수 등 지리 정보뿐"), (N, "해당 없음")],
    "D_JUSTWAR": [(P, "10통사2-04-02: 평화·국제 갈등 일반(전쟁의 정당화 조건 없음)"), (N, "해당 없음"),
                  (P, "Q18 보기 ㄹ '대외적 선제공격은 평화 구축 활동이 될 수 있다'(오답)뿐"), (N, "해당 없음")],
}
KEYS = list(SOURCES)
THINKER_CONCEPTS = {  # 커버리지: 복구 문항은 판정 기록의 사상가 목록, V4 문항은 영역 내 원문 신호로 집계
    "HAPPINESS": {"공자": ("공자", r"공자|수기|수양|인\(|군자|예\("), "석가모니": ("석가모니|불교", r"연기|해탈|열반|윤회|삼독"),
                  "노자": ("노자", r"무위|상선약수|도\("), "소크라테스": ("소크라테스", r"소크라테스"),
                  "아리스토텔레스": ("아리스토텔레스", r"중용|탁월성|습관화|품성"), "에피쿠로스": ("에피쿠로스", r"에피쿠로스|아타락시아|죽음을두려워하지|즐거운시간"),
                  "스토아": ("스토아", r"스토아|아파테이아"), "벤담": ("벤담", r"최대다수의최대행복"), "칸트": ("칸트", r"선의지"),
                  "정약용": ("정약용", r"정약용"), "쇼펜하우어": ("쇼펜하우어", r"쇼펜하우어")},
    "ENVIRONMENT": {"인간 중심주의": ("칸트|베이컨|데카르트|아퀴나스|패스모어|아리스토텔레스", r"인간자신에대한의무|자기자신에대한의무|간접적|자연을사냥|노예|기계|인간을위해존재|인간중심"),
                    "생태 중심주의": ("레오폴드", r"대지|생태중심|생명공동체"), "베이컨": ("베이컨", r"자연을사냥|노예로만들|지배권"),
                    "데카르트": ("데카르트", r"자동기계|의식이없는기계|영혼과육체"), "칸트": ("칸트", r"인간자신에대한의무|자기자신에대한의무"),
                    "싱어": ("싱어", r"쾌고|이익평등고려|종차별"), "슈바이처": ("슈바이처", r"생명에의경외|생명외경"),
                    "레오폴드": ("레오폴드", r"대지"), "네스": ("네스", r"심층생태"), "동양의 자연관": ("노자|유교|불교", r"연기|천지불인|무위")},
    "CIVIL_DISOBEDIENCE": {"전체": (None, r"불복종"), "소로": ("소로", r"법에대한존경심보다|정의에대한존경심|양심에따라저항|먼저인간이어야|정의로운사람이진정있을곳"),
                           "롤스": ("롤스", r"거의정의로운|공유된정의관|다수의정의감|법에대한충실성|공공적정의관|다수자의정의감|정의감에호소|평등한자유의원칙|체제의합법성"),
                           "싱어": ("싱어", r"저울질|효용성을따져|결과의좋음|공리주의원리에의해|공리의관점|결과론적|전체결과"),
                           "공유된 정의관": (None, r"공유된정의관|공공적정의관|공통된정의감|다수의정의감|다수자의정의감"),
                           "공개성": (None, r"공개|공공적"), "비폭력성": (None, r"비폭력|폭력"), "위법성": (None, r"위법|법에반하는|불법"),
                           "법 준수 의무": (None, r"따라야할의무|준법|구속력|준수할의무|충실성"), "기본적 자유": (None, r"기본적자유|평등한자유")},
    "JUSTICE": {"롤스": ("롤스", r"원초적|무지의베일|차등의원칙|차등원칙|최소수혜자|공정으로서의정의"), "노직": ("노직", r"소유권리|최소국가|교정의원리|정형적"),
                "아리스토텔레스": ("아리스토텔레스", r"비례|기하학적|특수적정의|정치적동물"), "왈처": ("왈처", r"복합평등|분배영역"),
                "밀": ("밀", r"질적|고상한쾌락|위해의원칙|어떤종류의쾌락|쾌락의질"), "매킨타이어": ("매킨타이어", r"서사적|실천관행|이야기의부분|공동체의전통|덕을실천"),
                "자유주의": ("롤스|노직", r"자유주의|소유권리|최소국가|원초적"), "공동체주의": ("매킨타이어", r"공동체주의|공동선|공동체속|공동체의전통|서사적"),
                "분배적 정의": (None, r"분배"), "원초적 입장": (None, r"원초적"), "무지의 베일": (None, r"무지의베일|무지의테일"),
                "차등의 원칙": (None, r"차등의원칙|차등원칙|최소수혜자"), "취득": (None, r"취득"), "이전": (None, r"이전|양도"),
                "교정": (None, r"교정"), "업적": (None, r"업적"), "능력": (None, r"능력에따"), "필요": (None, r"필요에따")},
    "PUNISHMENT": {"전체": (None, r"형벌|사형|처벌"), "칸트": ("칸트", r"정언명령|동등성|보복법|의욕했기때문|저질렀기때문|목적으로대우"),
                   "루소": ("루소", r"일반의지|국가의적|공공의적|생명보존"), "벤담": ("벤담", r"공리의원칙|최대행복|더큰악|더큰해악|형벌자체는"),
                   "베카리아": ("베카리아", r"종신노역|지속성|국가의전쟁|생명을빼앗을권한|생명을양도"),
                   "응보": (None, r"응보|보복|동등성|상응"), "예방": (None, r"예방|억제|억지|본보기"), "비교형": (None, r"갑.{0,400}을.{0,400}(병|갑,을|갑과을|을과병)|갑,을|갑과을|을,병|을과병")},
    "PEACE": {"갈퉁": ("갈퉁", r"구조적폭력|문화적폭력|적극적평화|간접적폭력"), "칸트": ("칸트", r"영구평화|영원한평화|평화연맹|세계시민법|공화정체"),
              "모겐소": ("모겐소|현실주의", r"권력투쟁|권력으로정의된|권력을얻기위한|권력획득|세력균형"),
              "소극적 평화": (None, r"소극적평화|전쟁이없는상태"), "적극적 평화": (None, r"적극적평화"), "직접적 폭력": (None, r"직접적폭력|직접적ㆍ물리적|직접적이고"),
              "구조적 폭력": (None, r"구조적폭력|구조적ㆍ|구조적,문화적"), "문화적 폭력": (None, r"문화적폭력|문화적폭력"),
              "국제 협력": (None, r"협력"), "국제기구": (None, r"국제기구|국제연맹|평화연맹|연맹|연방"), "영구 평화": (None, r"영구평화|영원한평화")},
    "AID": {"해외 원조 전체": (None, r"원조"), "싱어": ("싱어", r"이익평등고려|절대빈곤|소득의일부|기부"), "롤스": ("롤스", r"질서정연한|고통받는사회|만민")},
    "CULTURE": {"문화 상대주의": (None, r"상대주의|맥락속에서이해|이해하는태도|관용|불관용"), "자문화 중심주의": (None, r"자문화|우월|주류문화의우위|문화위계"),
                "문화 사대주의": (None, r"사대주의"), "보편 윤리": (None, r"보편윤리|보편적가치|보편적인권|기본적권리|인권"),
                "다문화주의": (None, r"다문화주의|대등하게|평등하게|고유성|정체성을유지|샐러드"), "동화": (None, r"동화|편입|용광로|융합하여"),
                "병존": (None, r"병존|공존"), "융합": (None, r"융합|종합하여새로운|새로운문화|새로운금속"), "용광로": (None, r"용광로|국수|고명|금속"),
                "샐러드볼": (None, r"샐러드|야채"), "모자이크": (None, r"모자이크"), "다문화 사회 통합 모형": (None, r"샐러드|용광로|국수|고명|모자이크|야채")},
}
SOURCE_SUPPORT = {  # 핵심 개념 영역별 4개 자료 지원(COVERAGE 근거)
    "HAPPINESS": [Y, Y, Y, P], "ENVIRONMENT": [Y, Y, Y, Y], "CIVIL_DISOBEDIENCE": [Y, Y, Y, N], "JUSTICE": [Y, Y, Y, Y],
    "PUNISHMENT": [Y, Y, Y, P], "PEACE": [Y, Y, Y, Y], "AID": [P, P, P, P], "CULTURE": [Y, Y, Y, Y],
}


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def tracked_hashes():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    return {f.decode(): hashlib.sha256((ROOT / f.decode()).read_bytes()).hexdigest()
            for f in files if f and (ROOT / f.decode()).exists() and (ROOT / f.decode()) not in NEW_FILES}


def text(q):
    return re.sub(r"\s+", "", json.load(open(ROOT / f"data/questions/json/{q}.json", encoding="utf-8"))["original"]["raw_text"] or "")


def main():
    before = tracked_hashes()
    # ---- 4개 자료 로드 QA ----
    load = {}
    for k, p in SOURCES.items():
        doc = pymupdf.open(p)
        flat = re.sub(r"\s+", "", "".join(pg.get_text() for pg in doc))
        miss = [a for a in ANCHORS[k] if a not in flat]
        load[k] = {"path": str(p.relative_to(ROOT)), "pages": len(doc), "chars": len(flat), "anchors_found": len(ANCHORS[k]) - len(miss),
                   "anchors_missing": miss, "loaded": len(flat) > 5000 and not miss}
    if not all(v["loaded"] for v in load.values()):
        raise SystemExit(f"STOP: source load failed {load}")

    v4 = jl(V4)
    v4_ids = {r["question_id"] for r in v4}
    v4a = {r["question_id"]: r for r in jl(AUD / "v4_strict_audit.jsonl")}
    all_ids = {p.stem for p in (ROOT / "data/questions/json").glob("*.json")}

    def prev(q):
        if q in v4a:
            return "V4_DROP" if v4a[q]["final_decision"] == "DROP" else "V4_KEEP"
        for f, key, bad in [("final_logic_correction/final_logic_correction.jsonl", "final_decision", "DROP"),
                            ("kice_2028_reconsideration.jsonl", "reconsideration_decision", "DROP"),
                            ("official_curriculum_final_audit.jsonl", "final_audit_decision", "DROP")]:
            for r in PREV[f]:
                if r["question_id"] == q and r[key] == bad:
                    return {"final_logic_correction/final_logic_correction.jsonl": "V3_LOGIC_CORRECTION_DROP",
                            "kice_2028_reconsideration.jsonl": "KICE_RECONSIDERATION_DROP",
                            "official_curriculum_final_audit.jsonl": "OFFICIAL_AUDIT_DROP"}[f]
        fc = FC.get(q)
        return f"PHASE2C_{fc}" if fc else "NOT_CANDIDATE(PHASE2B)"

    def prev_reasons(q):
        if q in v4a:
            return v4a[q]["drop_reasons"]
        for r in PREV["final_logic_correction/targeted_review.jsonl"]:
            if r["question_id"] == q:
                return r["drop_reasons"]
        for r in PREV["kice_2028_reconsideration.jsonl"]:
            if r["question_id"] == q:
                return r["out_of_scope_dependency"]
        for r in PREV["official_curriculum_final_audit.jsonl"]:
            if r["question_id"] == q:
                return [r["drop_reason_category"]] if r["drop_reason_category"] else []
        return [FC_REASON[q]] if q in FC_REASON else []

    global PREV, FC, FC_REASON
    PREV = {f: jl(P2 / f) for f in ["final_logic_correction/final_logic_correction.jsonl", "final_logic_correction/targeted_review.jsonl",
                                    "kice_2028_reconsideration.jsonl", "official_curriculum_final_audit.jsonl"]}
    fcl = jl(P2 / "final_curation.jsonl")
    FC = {r["question_id"]: r["final_decision"] for r in fcl}
    FC_REASON = {r["question_id"]: r["reason"] for r in fcl}

    recs, seen = [], set()
    for line in open(DEC, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        f = line.rstrip("\n").split("|")
        assert len(f) == 11, line[:60]
        short, d, area, phil, prof, scope, core, usable, rationale, rr, rnd = f
        y, e, q = short.split("_")
        qid = f"{y}_{e}_LIFE_ETHICS_{q}"
        assert qid not in seen and qid in all_ids and qid not in v4_ids, qid
        seen.add(qid)
        assert prof in PROFILES and (d == "R") == (not prof.startswith("D_")), (qid, prof)
        ev = {k: {"지원정도": PROFILES[prof][i][0], "근거": PROFILES[prof][i][1]} for i, k in enumerate(KEYS)}
        rrs = [x for x in rr.split(";") if x]
        recs.append({
            "question_id": qid,
            "target_area": area,
            "philosophers": [x for x in phil.split(";") if x and x != "-"],
            "previous_decision": prev(qid),
            "previous_drop_reasons": prev_reasons(qid),
            "core_content": core,
            "source_evidence": ev,
            "evidence_profile": prof,
            "통합사회에서_활용가능한_내용": usable if d == "R" else None,
            "decision": {"R": "RESTORE", "D": "REMAIN_DROP"}[d],
            "reuse_scope": {"F": "FULL", "P": "PARTIAL", "-": None}[scope],
            "rationale": rationale,
            "search_round": int(rnd),
            "review_required": bool(rrs),
            "review_reasons": rrs,
        })
    recs.sort(key=lambda r: r["question_id"])
    restore = [r for r in recs if r["decision"] == "RESTORE"]
    remain = [r for r in recs if r["decision"] == "REMAIN_DROP"]

    patched = [dict(r, recovery_status="V4_KEEP_PROTECTED") for r in v4]
    for r in restore:
        patched.append({
            "question_id": r["question_id"], "source": "V4_TARGETED_RECOVERY", "review_required": r["review_required"],
            "primary_curriculum_code": None, "kice_sample_link": None, "adaptation_required": None,
            "v4_basis": "TARGETED_RECOVERY_RESTORE", "target_area": r["target_area"], "reuse_scope": r["reuse_scope"],
            "evidence_profile": r["evidence_profile"], "recovery_status": "RESTORED",
        })
    code = {"ENV_CORE": "10통사1-03-02", "ENV_SPECTRUM": "10통사1-03-02", "ENV_EAST": "10통사1-03-02", "ENV_SUSTAIN": "10통사1-03-03",
            "PUN_CORE": "10통사2-02-01", "PUN_SPECTRUM": "10통사2-02-01", "JUS_CORE": "10통사2-02-02", "JUS_COMM": "10통사2-02-02",
            "JUS_CAPITAL": "10통사2-03-01", "PEACE_CORE": "10통사2-04-02", "PEACE_KANT": "10통사2-04-02", "PEACE_IR": "10통사2-04-02",
            "AID_CORE": "10통사2-04-01", "CULT_MODEL": "10통사1-04-04", "CULT_CONFLICT": "10통사2-04-02", "HAP_CORE": "10통사1-02-01",
            "HAP_EAST": "10통사1-02-01"}
    kice = {"ENV_CORE": "Q4", "ENV_SPECTRUM": "Q4", "PUN_CORE": "Q16", "PUN_SPECTRUM": "Q16", "JUS_CORE": "Q15", "JUS_COMM": "Q13",
            "JUS_CAPITAL": "Q17", "PEACE_CORE": "Q18", "PEACE_KANT": "Q12", "PEACE_IR": "Q12", "AID_CORE": "Q12", "CULT_MODEL": "Q3",
            "HAP_CORE": "Q1", "HAP_EAST": "Q1"}
    for r in patched:
        if r["recovery_status"] == "RESTORED":
            r["primary_curriculum_code"] = code[r["evidence_profile"]]
            r["kice_sample_link"] = {"sample_question": kice[r["evidence_profile"]]} if r["evidence_profile"] in kice else None
    patched.sort(key=lambda r: r["question_id"])

    def dump(p, rows):
        with open(p, "w", encoding="utf-8") as fo:
            for x in rows:
                fo.write(json.dumps(x, ensure_ascii=False) + "\n")

    dump(AUD / "v4_targeted_recovery.jsonl", recs)
    dump(AUD / "v4_final_content_asset_index_patched.jsonl", patched)

    # ---- COVERAGE ----
    theme_area = {"CIVIL_DISOBEDIENCE": "CIVIL_DISOBEDIENCE", "RAWLS_NOZICK": "JUSTICE", "PUNISHMENT": "PUNISHMENT",
                  "PEACE_VIOLENCE": "PEACE", "CONFUCIUS": "CULTURE"}

    def v4_area(r):
        a = v4a[r["question_id"]]
        if a["audit_theme"] in theme_area:
            return theme_area[a["audit_theme"]]
        c = r["primary_curriculum_code"] or ""
        if c.startswith("10통사1-04"):
            return "CULTURE"
        if c.startswith("10통사1-03-02"):
            return "ENVIRONMENT"
        if c.startswith("10통사2-02-02"):
            return "JUSTICE"
        if c.startswith("10통사1-02"):
            return "HAPPINESS"
        if c.startswith("10통사2-04-02"):
            return "PEACE"
        return None

    v4_by_area = collections.defaultdict(list)
    for r in v4:
        a = v4_area(r)
        if a:
            v4_by_area[a].append((r["question_id"], text(r["question_id"])))
    rs_by_area = collections.defaultdict(list)
    for r in restore:
        rs_by_area[r["target_area"]].append((r["question_id"], text(r["question_id"]), "|".join(r["philosophers"])))
    coverage, gaps = {}, []
    for area, cs in THINKER_CONCEPTS.items():
        coverage[area] = {"4_source_support": dict(zip(KEYS, SOURCE_SUPPORT[area])), "concepts": {}}
        for c, (names, pat) in cs.items():
            a = [q for q, t in v4_by_area[area] if re.search(pat, t)]
            b = [q for q, t, ph in rs_by_area[area] if re.search(pat, t) and (not names or re.search(names, ph))]
            tot = len(a) + len(b)
            coverage[area]["concepts"][c] = {"v4_keep": len(a), "restored": len(b), "patched_total": tot, "restored_ids": b}
            if tot <= 1:
                gaps.append({"area": area, "concept": c, "patched_total": tot})
    gap_round = [r for r in recs if r["search_round"] == 2]
    cov = {
        "method": "영역별 문항(V4 KEEP은 V4 감사 테마·주 성취기준으로 영역 배정, 복구 문항은 판정 영역) 안에서 개념 신호로 집계. "
                  "사상가 개념은 복구 문항의 경우 판정 기록의 사상가 목록에 있고 해당 문항 원문에도 그 사상가의 신호가 있을 때만 집계. 근사 집계이며 판정에는 쓰이지 않음.",
        "coverage": coverage,
        "coverage_gaps_after_patch": gaps,
        "gap_targeted_search": {
            "performed": True, "rounds": 1,
            "searched_concepts": ["슈바이처", "네스", "왈처", "문화 사대주의", "모자이크", "샐러드볼/용광로", "문화 상대주의/자문화",
                                  "소크라테스", "스토아", "쇼펜하우어", "베이컨", "에피쿠로스/아리스토텔레스 행복", "동양 자연관"],
            "scope": "V4 KEEP이 아닌 686문항(기존 탈락자) 원문에서 해당 개념 신호만 재검색(800 전체 재검토 아님)",
            "new_candidates_reviewed": len(gap_round),
            "restored": sum(r["decision"] == "RESTORE" for r in gap_round),
            "restored_ids": [r["question_id"] for r in gap_round if r["decision"] == "RESTORE"],
        },
    }
    json.dump(cov, open(AUD / "v4_core_coverage_after_recovery.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---- QA ----
    ids_p = [r["question_id"] for r in patched]
    after = tracked_hashes()
    n = len(recs)
    ev_ok = lambda r: all(r["source_evidence"][k]["지원정도"] in (Y, P, N) and r["source_evidence"][k]["근거"] for k in KEYS)
    qa = {
        "sources_loaded": {k: v["loaded"] for k, v in load.items()},
        "v4_keep_original": len(v4),
        "v4_keep_protected": len(v4_ids & set(ids_p)),
        "v4_keep_lost": len(v4_ids - set(ids_p)),
        "duplicate_ids": len(ids_p) - len(set(ids_p)),
        "unknown_ids": len(set(ids_p) - all_ids),
        "restore_are_previous_drops": all(r["question_id"] not in v4_ids for r in restore),
        "patched_eq_v4_plus_restore": len(patched) == len(v4) + len(restore),
        "candidates": n,
        "source1_compared": f"{sum(bool(r['source_evidence'][KEYS[0]]['근거']) for r in recs)}/{n}",
        "source2_compared": f"{sum(bool(r['source_evidence'][KEYS[1]]['근거']) for r in recs)}/{n}",
        "source3_compared": f"{sum(bool(r['source_evidence'][KEYS[2]]['근거']) for r in recs)}/{n}",
        "source4_compared": f"{sum(bool(r['source_evidence'][KEYS[3]]['근거']) for r in recs)}/{n}",
        "restore_without_4source_evidence": sum(1 for r in restore if not ev_ok(r)),
        "remain_drop_without_4source_evidence": sum(1 for r in remain if not ev_ok(r)),
        "restore_without_usable_content_or_scope": sum(1 for r in restore if not (r["통합사회에서_활용가능한_내용"] and r["reuse_scope"])),
        "existing_files_modified": sum(1 for k, v in before.items() if after.get(k) != v),
    }
    assert all(qa["sources_loaded"].values()) and qa["v4_keep_lost"] == 0 and qa["duplicate_ids"] == 0 and qa["unknown_ids"] == 0
    assert qa["restore_are_previous_drops"] and qa["patched_eq_v4_plus_restore"]
    assert qa["restore_without_4source_evidence"] == qa["remain_drop_without_4source_evidence"] == 0
    assert qa["restore_without_usable_content_or_scope"] == 0 and qa["existing_files_modified"] == 0, qa

    by_area = {}
    for a in ["HAPPINESS", "ENVIRONMENT", "CIVIL_DISOBEDIENCE", "JUSTICE", "PUNISHMENT", "PEACE", "AID", "CULTURE"]:
        rs = [r for r in recs if r["target_area"] == a]
        by_area[a] = {"candidates": len(rs), "RESTORE": sum(r["decision"] == "RESTORE" for r in rs),
                      "REMAIN_DROP": sum(r["decision"] == "REMAIN_DROP" for r in rs),
                      "FULL": sum(r["reuse_scope"] == "FULL" for r in rs), "PARTIAL": sum(r["reuse_scope"] == "PARTIAL" for r in rs)}
    summary = {
        "baseline": {"v4_keep": len(v4), "v4_keep_protected": qa["v4_keep_protected"], "v4_keep_lost": qa["v4_keep_lost"]},
        "sources": load,
        "search": {"dropped_pool_searched": len(all_ids) - len(v4_ids), "initial_candidates": sum(r["search_round"] == 1 for r in recs),
                   "gap_search_candidates": sum(r["search_round"] == 2 for r in recs), "semantic_reviewed": n,
                   "method": "기존 탈락자 686문항의 원문·기존 판정 기록에서 표적 영역 개념 신호로 후보 추출(1차), 개념 COVERAGE_GAP에 한정해 1회 추가 검색(2차). "
                             "후보만 발문·제시문·선지를 읽어 판정. 키워드로 판정하지 않음. OCR·PNG 재생성 없음."},
        "result": {"RESTORE": len(restore), "FULL": sum(r["reuse_scope"] == "FULL" for r in restore),
                   "PARTIAL": sum(r["reuse_scope"] == "PARTIAL" for r in restore), "REMAIN_DROP": len(remain),
                   "patched_total": len(patched)},
        "restore_by_previous_decision": dict(collections.Counter(r["previous_decision"] for r in restore)),
        "by_area": by_area,
        "restored_ids": [r["question_id"] for r in restore],
        "qa": qa,
        "note": "FINAL_TRANSFER V5는 생성하지 않음(사용자 coverage 검토·승인 대기). 기존 V4 selection·audit·FINAL_TRANSFER 무수정.",
    }
    json.dump(summary, open(AUD / "v4_targeted_recovery_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in ("baseline", "search", "result", "restore_by_previous_decision", "by_area", "qa")},
                     ensure_ascii=False, indent=1))
    print("GAPS", json.dumps(gaps, ensure_ascii=False))


if __name__ == "__main__":
    main()
