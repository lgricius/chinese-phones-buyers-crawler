"""Raw HTTP responses, stored per crawl run so parsing can be redone offline.

data/raw/<run>/<site>.jsonl.gz  one JSON line per response:
  {"kind": "product", "url": ..., "status": 200, "fetched_at": ..., "meta": {...}, "body": "..."}
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path


class RawWriter:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = gzip.open(path, "wt", encoding="utf-8", compresslevel=6)
        self.count = 0

    def save(self, kind: str, url: str, body: str, status: int = 200, meta: dict | None = None) -> None:
        rec = {
            "kind": kind,
            "url": url,
            "status": status,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "meta": meta or {},
            "body": body,
        }
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        self._f.close()


def read(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
