"""명령행 진입점.

python -m csatdb run [--pages 1-8] [--force] [--overwrite-outputs]   # 전체 파이프라인
python -m csatdb inventory                      # STEP 1~3 만
python -m csatdb qa                             # QA + 보고서만 재생성
python -m csatdb serve [--port 8765]            # 검토 UI
"""
from __future__ import annotations

import argparse
import datetime as dt

from .common import load_config, RunLog, write_json_atomic, PROCESSED
from .inventory import discover
from .state import State


def parse_pages(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def cmd_inventory(args, log):
    cfg = load_config()
    results = []
    for comp in cfg["compilations"]:
        inv = discover(comp)
        results.append(inv)
        print(f"[{comp['key']}] files={len(inv.files)} total_pages={inv.total_pages}")
        for f in inv.files:
            print(f"  {f.split_index:>2} {f.filename}  pages={f.page_count}  "
                  f"original p{f.original_start:03d}-p{f.original_end:03d}  sha256={f.sha256[:12]}…")
        for p in inv.problems:
            log.log("ERROR", "inventory", p["code"], p["message"], file=p.get("file"))
        if not inv.problems:
            print("  -> 분할 PDF 연속성 검사 통과 (파일명 범위 = 실제 누적 페이지, 번호 연속, 총 페이지 일치)")
        write_json_atomic(PROCESSED / f"inventory_{comp['key']}.json", {
            "compilation": comp, "total_pages": inv.total_pages,
            "files": [f.to_dict() for f in inv.files], "problems": inv.problems,
            "page_map": inv.page_map()})
    return results


def cmd_run(args, log):
    from .stage_pages import run_pages
    from .extract import run_extract
    from .qa import run_qa
    cfg = load_config()
    state = State()
    invs = cmd_inventory(args, log)
    only = parse_pages(args.pages)
    for inv in invs:
        r = run_pages(inv, state, log, force=args.force, only_pages=only, workers=args.workers)
        print(f"[pages] {r}")
        state.mark_stage("pages", "done" if r["failed"] == 0 else "partial", **{k: v for k, v in r.items() if k != "skipped_files"})
        run_extract(inv, cfg, state, log, only_pages=only, force=args.force or args.overwrite_outputs)
    run_qa(cfg, log)


def cmd_qa(args, log):
    from .qa import run_qa
    run_qa(load_config(), log)


def cmd_serve(args, log):
    from .review_ui import serve
    serve(port=args.port)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="csatdb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    r = sub.add_parser("run")
    r.add_argument("--pages", help="원본 합본 페이지 범위 (예: 1-8,41)")
    r.add_argument("--force", action="store_true", help="이미 처리된 파일/문항도 강제 재처리 (기존 산출물은 백업)")
    r.add_argument("--overwrite-outputs", action="store_true",
                   help="페이지 OCR 캐시는 재사용하고 문항 JSON/이미지만 재생성·교체 (기존 산출물은 백업)")
    r.add_argument("--workers", type=int, default=4)
    sub.add_parser("qa")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    log = RunLog(run_id)
    {"inventory": cmd_inventory, "run": cmd_run, "qa": cmd_qa, "serve": cmd_serve}[args.cmd](args, log)


if __name__ == "__main__":
    main()
