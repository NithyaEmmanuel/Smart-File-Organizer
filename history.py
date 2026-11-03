"""SQLite activity history and safe undo."""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from file_ops import hash_file, safe_move_no_overwrite
from scanner import ProgressCallback

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,            -- ORGANIZE or UNDO
    folder         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    summary        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS activity (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id     INTEGER NOT NULL REFERENCES operations(id),
    timestamp        TEXT NOT NULL,
    operation        TEXT NOT NULL,          -- Move, Skip, Undo
    filename         TEXT NOT NULL,
    source_path      TEXT NOT NULL,
    destination_path TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL,          -- Success, Failed, Skipped
    error_message    TEXT NOT NULL DEFAULT '',
    size_bytes       INTEGER,                -- file identity: size,
    mtime_ns         INTEGER,                -- modified time,
    sha256           TEXT,                   -- and content hash
    undone           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_activity_operation ON activity(operation_id);
"""


def default_db_path() -> Path:
    """Where the history database lives (a per-user folder, not next to the .exe)."""
    base = os.environ.get("LOCALAPPDATA")
    folder = Path(base) / "SmartFileOrganizer" if base else Path.home() / ".smart_file_organizer"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "history.db"


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


class HistoryDB:
    """Thread-safe wrapper around the SQLite history database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writing -----------------------------------------------------------
    def start_operation(self, operation_type: str, folder: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO operations (operation_type, folder, started_at) VALUES (?, ?, ?)",
                (operation_type, folder, _now()),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def finish_operation(self, operation_id: int, summary: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE operations SET finished_at = ?, summary = ? WHERE id = ?",
                (_now(), summary, operation_id),
            )
            self._conn.commit()

    def log_activity(
        self, operation_id: int | None, operation: str, filename: str,
        source_path: str, destination_path: str, status: str, error_message: str = "",
        size_bytes: int | None = None, mtime_ns: int | None = None,
        sha256: str | None = None,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO activity (operation_id, timestamp, operation, filename,
                   source_path, destination_path, status, error_message,
                   size_bytes, mtime_ns, sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (operation_id, _now(), operation, filename, source_path,
                 destination_path, status, error_message, size_bytes, mtime_ns, sha256),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def mark_undone(self, activity_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE activity SET undone = 1 WHERE id = ?", (activity_id,))
            self._conn.commit()

    # -- reading -----------------------------------------------------------
    def get_recent_activity(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_last_undoable_operation(self) -> dict[str, Any] | None:
        """Newest ORGANIZE operation that still has moved files to restore."""
        with self._lock:
            row = self._conn.execute(
                """SELECT o.*, (SELECT COUNT(*) FROM activity a
                                WHERE a.operation_id = o.id AND a.operation = 'Move'
                                  AND a.status = 'Success' AND a.undone = 0) AS pending
                   FROM operations o
                   WHERE o.operation_type = 'ORGANIZE'
                     AND EXISTS (SELECT 1 FROM activity a WHERE a.operation_id = o.id
                                 AND a.operation = 'Move' AND a.status = 'Success'
                                 AND a.undone = 0)
                   ORDER BY o.id DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def get_undoable_moves(self, operation_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM activity WHERE operation_id = ? AND operation = 'Move'
                   AND status = 'Success' AND undone = 0 ORDER BY id DESC""",
                (operation_id,),
            ).fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------
class UndoRefused(Exception):
    """A file was deliberately NOT restored because it was unsafe to do so."""


@dataclass
class UndoResult:
    operation_id: int | None = None
    restored: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)   # (filename, reason)
    nothing_to_undo: bool = False


def undo_last_operation(
    db: HistoryDB, progress_callback: ProgressCallback | None = None
) -> UndoResult:
    """Restore files moved by the most recent organize operation, where safe.

    A file is restored only if: it is still at its destination, it is unchanged
    (size, modified time, SHA-256), and its original path is free. Failures do
    not stop the rest; they are reported and can be retried later.
    """
    operation = db.get_last_undoable_operation()
    if operation is None:
        return UndoResult(nothing_to_undo=True)

    moves = db.get_undoable_moves(operation["id"])
    undo_id = db.start_operation("UNDO", operation["folder"])
    result = UndoResult(operation_id=undo_id)
    touched_dirs: set[Path] = set()

    for index, move in enumerate(moves, start=1):
        destination, original = Path(move["destination_path"]), Path(move["source_path"])
        try:
            _restore_one(move)
        except UndoRefused as exc:
            _log_undo(db, undo_id, move, "Failed", str(exc), result)
        except PermissionError as exc:
            _log_undo(db, undo_id, move, "Failed", f"Permission denied: {exc.strerror or exc}", result)
        except OSError as exc:
            _log_undo(db, undo_id, move, "Failed", exc.strerror or str(exc), result)
        else:
            result.restored += 1
            touched_dirs.add(destination.parent)
            db.mark_undone(move["id"])
            db.log_activity(undo_id, "Undo", move["filename"], str(destination),
                            str(original), "Success")
        if progress_callback is not None:
            progress_callback(index, len(moves))

    # Remove category folders we created if (and only if) they are now empty.
    for directory in touched_dirs:
        if directory.parent == Path(operation["folder"]):
            try:
                os.rmdir(directory)
            except OSError:
                pass

    db.finish_operation(undo_id, f"{result.restored} restored, {len(result.failed)} failed")
    return result


def _log_undo(db: HistoryDB, undo_id: int, move: dict, status: str,
              message: str, result: UndoResult) -> None:
    result.failed.append((move["filename"], message))
    db.log_activity(undo_id, "Undo", move["filename"], move["destination_path"],
                    move["source_path"], status, message)


def _restore_one(move: dict[str, Any]) -> None:
    destination, original = Path(move["destination_path"]), Path(move["source_path"])
    try:
        st = os.lstat(destination)
    except FileNotFoundError:
        raise UndoRefused("The moved file is no longer at its destination") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise UndoRefused("The file at the destination is no longer a regular file")
    if move["size_bytes"] is not None and st.st_size != move["size_bytes"]:
        raise UndoRefused("The file was modified after it was moved (size differs)")
    if move["mtime_ns"] is not None and st.st_mtime_ns != move["mtime_ns"]:
        raise UndoRefused("The file was modified after it was moved (modified time differs)")
    if move["sha256"] and hash_file(destination) != move["sha256"]:
        raise UndoRefused("The file was modified after it was moved (content differs)")
    if os.path.lexists(original):
        raise UndoRefused("The original location is occupied; nothing was overwritten")
    if not original.parent.is_dir():
        raise UndoRefused("The original folder no longer exists")
    safe_move_no_overwrite(destination, original)
