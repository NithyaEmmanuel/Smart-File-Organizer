"""Shared helpers: every test works inside a throw-away temporary folder."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from history import HistoryDB
from organizer import build_plan
from scanner import scan_folder


class TempFolderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "work"
        self.root.mkdir()
        self.db_path = self.base / "history.db"

    def make_file(self, name: str, content: bytes = b"data", folder: Path | None = None) -> Path:
        path = (folder or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def listing(self, folder: Path | None = None) -> list[str]:
        base = folder or self.root
        return sorted(
            str(p.relative_to(base)).replace("\\", "/")
            for p in base.rglob("*") if p.is_file()
        )

    def make_db(self) -> HistoryDB:
        db = HistoryDB(self.db_path)
        self.addCleanup(db.close)
        return db

    def scan(self):
        return scan_folder(self.root)

    def plan(self, **kwargs):
        return build_plan(self.scan(), protected_paths=[], **kwargs)


def can_symlink(base: Path) -> bool:
    try:
        target = base / "_t"
        target.write_bytes(b"x")
        link = base / "_l"
        os.symlink(target, link)
        link.unlink()
        target.unlink()
        return True
    except (OSError, NotImplementedError):
        return False
