"""페이지 처리 stage: 모든 분할 PDF 의 페이지를 원본 합본 페이지 번호로 캐시한다."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .common import PAGES_DIR, read_json
from .inventory import Inventory
from .pages import process_page, PAGE_CACHE_VERSION
from .state import State


def page_dir(comp_key: str, original_page: int) -> Path:
    return PAGES_DIR / comp_key / f"p{original_page:03d}"


def page_cached(comp_key: str, original_page: int, sha: str) -> bool:
    m = read_json(page_dir(comp_key, original_page) / "page.json") or {}
    return (m.get("status") == "done" and m.get("source_sha256") == sha
            and m.get("cache_version") == PAGE_CACHE_VERSION
            and (page_dir(comp_key, original_page) / "page.png").exists())


def _job(args):
    pdf_path, page_index, out_dir, sha, force = args
    meta = process_page(Path(pdf_path), page_index, Path(out_dir), sha, force=force)
    return str(out_dir), meta.get("status"), bool(meta.get("_cached"))


def run_pages(inv: Inventory, state: State, log, force: bool = False,
              only_pages: set[int] | None = None, workers: int = 4) -> dict:
    comp_key = inv.compilation["key"]
    jobs = []
    skipped_files = []
    for f in inv.files:
        pages = range(1, f.page_count + 1)
        all_cached = all(page_cached(comp_key, f.original_start + sp - 1, f.sha256) for sp in pages)
        if not force and only_pages is None and state.file_done(f.filename, f.sha256) and all_cached:
            skipped_files.append(f.filename)
            continue
        for sp in pages:
            op = f.original_start + sp - 1
            if only_pages is not None and op not in only_pages:
                continue
            jobs.append((str(f.path), sp - 1, str(page_dir(comp_key, op)), f.sha256, force))
    if skipped_files:
        log.log("INFO", "pages", "FILE_SKIPPED_ALREADY_DONE",
                f"SHA-256 동일·처리완료 파일 {len(skipped_files)}개 재처리 생략 (--force 로 강제)",
                files=skipped_files)
    done = 0
    cached = 0
    failed = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, j): j for j in jobs}
        for fu in as_completed(futs):
            j = futs[fu]
            try:
                _, _, was_cached = fu.result()
                done += 1
                cached += int(was_cached)
                if done % 10 == 0:
                    print(f"  pages: {done}/{len(jobs)}", flush=True)
            except Exception as e:  # 실패 페이지는 조용히 넘기지 않고 기록
                failed.append(j)
                log.log("ERROR", "pages", "PAGE_PROCESS_FAILED", repr(e), page_dir=j[2])
    # 파일 단위 완료 표시 (전체 페이지를 처리한 경우만)
    for f in inv.files:
        ok = all(page_cached(comp_key, f.original_start + sp - 1, f.sha256) for sp in range(1, f.page_count + 1))
        state.mark_file(f.filename, f.sha256, "done" if ok else "partial",
                        page_count=f.page_count, original_start=f.original_start,
                        original_end=f.original_end)
    return {"pages_checked": done, "pages_from_cache": cached, "pages_newly_processed": done - cached,
            "failed": len(failed), "skipped_files": skipped_files}
