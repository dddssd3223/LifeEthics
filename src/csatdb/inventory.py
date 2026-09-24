"""STEP 1~3: 분할 PDF 탐색, 순서 복원, 원본 합본 페이지 매핑.

파일명은 '분할 순서'와 '원본 페이지 범위' 검증에만 쓰인다.
시험 연도/종류/문항 번호는 절대로 파일명에서 추론하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pymupdf

from .common import ROOT, nfc, sha256_file, rel


@dataclass
class SplitFile:
    path: Path
    filename: str              # NFC 정규화된 파일명
    sha256: str
    page_count: int
    split_index: int | None
    declared_start: int | None
    declared_end: int | None
    original_start: int = 0     # 실제 페이지 수 누적으로 계산한 원본 시작 페이지
    original_end: int = 0
    page_sizes: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["path"] = rel(self.path)
        return d


@dataclass
class Inventory:
    compilation: dict
    files: list[SplitFile]
    total_pages: int
    problems: list[dict]

    def page_map(self) -> list[dict]:
        """원본 페이지 번호(1-based) -> (분할 파일, 분할 파일 내 페이지) 매핑."""
        out = []
        for f in self.files:
            for sp in range(1, f.page_count + 1):
                out.append({
                    "original_compilation_page": f.original_start + sp - 1,
                    "split_pdf": f.filename,
                    "split_pdf_path": rel(f.path),
                    "split_pdf_page": sp,
                    "split_sha256": f.sha256,
                })
        return out


def original_page(split_original_start: int, split_pdf_page: int) -> int:
    """split_pdf_page(1-based) -> original_compilation_page."""
    if split_pdf_page < 1:
        raise ValueError("split_pdf_page is 1-based")
    return split_original_start + split_pdf_page - 1


def discover(compilation: dict) -> Inventory:
    raw_dir = ROOT / compilation["raw_dir"]
    rx = re.compile(compilation["split_name_regex"])
    files: list[SplitFile] = []
    problems: list[dict] = []
    for p in sorted(raw_dir.glob(compilation["split_glob"])):
        name = nfc(p.name)
        m = rx.search(name)
        doc = pymupdf.open(p)
        sf = SplitFile(
            path=p, filename=name, sha256=sha256_file(p), page_count=doc.page_count,
            split_index=int(m["index"]) if m else None,
            declared_start=int(m["start"]) if m else None,
            declared_end=int(m["end"]) if m else None,
            page_sizes=[[round(pg.rect.width, 1), round(pg.rect.height, 1)] for pg in doc],
        )
        doc.close()
        if not m:
            problems.append({"code": "SPLIT_NAME_UNPARSED", "file": name,
                             "message": "분할 순서/페이지 범위를 파일명에서 읽을 수 없음"})
        files.append(sf)

    # 순서: split_index -> declared_start -> 파일명
    files.sort(key=lambda f: (f.split_index if f.split_index is not None else 10**9,
                              f.declared_start or 10**9, f.filename))

    # 동일 SHA-256 파일 (중복 업로드)
    seen: dict[str, str] = {}
    for f in files:
        if f.sha256 in seen:
            problems.append({"code": "DUPLICATE_SPLIT_FILE", "file": f.filename,
                             "message": f"{seen[f.sha256]} 와 SHA-256 동일"})
        seen.setdefault(f.sha256, f.filename)

    cursor = 1
    idx_seen = set()
    for f in files:
        f.original_start = cursor
        f.original_end = cursor + f.page_count - 1
        cursor = f.original_end + 1
        if f.split_index in idx_seen:
            problems.append({"code": "SPLIT_INDEX_DUPLICATE", "file": f.filename,
                             "message": f"분할 번호 {f.split_index} 중복"})
        idx_seen.add(f.split_index)
        if f.declared_start is not None:
            if f.declared_start != f.original_start or f.declared_end != f.original_end:
                problems.append({
                    "code": "PAGE_RANGE_MISMATCH", "file": f.filename,
                    "message": (f"파일명 범위 p{f.declared_start}-{f.declared_end} 와 실제 누적 페이지 "
                                f"p{f.original_start}-{f.original_end} 불일치")})
            if f.declared_end - f.declared_start + 1 != f.page_count:
                problems.append({"code": "PAGE_COUNT_MISMATCH", "file": f.filename,
                                 "message": f"파일명 범위 길이와 실제 페이지 수({f.page_count}) 불일치"})
    idxs = sorted(i for i in idx_seen if i is not None)
    if idxs and idxs != list(range(idxs[0], idxs[0] + len(idxs))):
        problems.append({"code": "SPLIT_INDEX_GAP", "file": None,
                         "message": f"분할 번호 불연속: {idxs}"})
    total = cursor - 1
    exp = compilation.get("expected_total_pages")
    if exp and total != exp:
        problems.append({"code": "TOTAL_PAGES_MISMATCH", "file": None,
                         "message": f"총 페이지 {total} != 예상 {exp}"})
    return Inventory(compilation=compilation, files=files, total_pages=total, problems=problems)
