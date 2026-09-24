"""Checkpoint / resume 상태 관리 (data/processed/state.json).

- 파일 단위: SHA-256 이 같고 status == "done" 이면 기본적으로 재처리하지 않는다.
- 페이지 단위: 각 페이지 캐시(page.json)에 status 가 기록되며, 중단 후 재실행 시
  완료된 페이지는 건너뛰고 미완료 페이지부터 이어서 처리한다.
"""
from __future__ import annotations

import threading

from .common import STATE_PATH, read_json, write_json_atomic, now_iso


class State:
    _lock = threading.Lock()

    def __init__(self):
        self.data = read_json(STATE_PATH, default=None) or {"files": {}, "stages": {}}

    def save(self):
        with self._lock:
            write_json_atomic(STATE_PATH, self.data)

    # ---- files
    def file_done(self, filename: str, sha: str) -> bool:
        f = self.data["files"].get(filename)
        return bool(f and f.get("sha256") == sha and f.get("status") == "done")

    def mark_file(self, filename: str, sha: str, status: str, **extra):
        rec = self.data["files"].setdefault(filename, {})
        if rec.get("sha256") and rec["sha256"] != sha:
            rec.setdefault("previous_sha256", []).append(rec["sha256"])
        rec.update({"sha256": sha, "status": status, "updated_at": now_iso(), **extra})
        self.save()

    # ---- stages
    def mark_stage(self, stage: str, status: str, **extra):
        self.data["stages"][stage] = {"status": status, "updated_at": now_iso(), **extra}
        self.save()
