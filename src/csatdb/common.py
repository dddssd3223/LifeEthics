"""공통 경로, 설정 로딩, 로깅, 해시 유틸리티."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "sources.json"

DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
PAGES_DIR = PROCESSED / "pages"          # 페이지 렌더링/레이아웃/OCR 캐시 (git 제외)
STATE_PATH = PROCESSED / "state.json"    # checkpoint
QUESTIONS = DATA / "questions"
Q_IMAGES = QUESTIONS / "images"
Q_JSON = QUESTIONS / "json"
DB_DIR = DATA / "database"
DB_PATH = DB_DIR / "questions.db"
REPORTS = DATA / "reports"
REVIEWS = DATA / "reviews"
LOGS = ROOT / "logs"

RENDER_DPI = 300
PIPELINE_VERSION = "1.0.0"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def nfc(s: str) -> str:
    """macOS 업로드 파일명은 NFD 로 저장되는 경우가 있어 NFC 로 정규화한다."""
    return unicodedata.normalize("NFC", s)


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def rel(path: Path) -> str:
    """저장소 루트 기준 상대 경로 (POSIX)."""
    return Path(os.path.relpath(path, ROOT)).as_posix()


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class RunLog:
    """extraction_logs 테이블과 logs/*.jsonl 에 함께 기록되는 구조화 로그."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.entries: list[dict] = []
        LOGS.mkdir(parents=True, exist_ok=True)
        self.path = LOGS / f"run_{run_id}.jsonl"

    def log(self, level: str, stage: str, code: str, message: str, **ctx) -> dict:
        e = {"ts": now_iso(), "run_id": self.run_id, "level": level, "stage": stage,
             "code": code, "message": message}
        e.update({k: v for k, v in ctx.items() if v is not None})
        self.entries.append(e)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        if level in ("ERROR", "WARNING"):
            print(f"[{level}] {stage}/{code}: {message} {ctx if ctx else ''}")
        return e
