"""Preview (dry run) and safe execution of file organization.

build_plan()    -> decides what WOULD happen; touches nothing on disk.
execute_plan()  -> performs the moves, never overwriting or deleting files.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from file_ops import (
    IDENTITY_HASH_LIMIT,
    hash_file,
    path_key,
    safe_move_no_overwrite,
    unique_destination,
)
from scanner import FileInfo, ProgressCallback, ScanResult

DUPLICATES_FOLDER = "_Duplicates"


class PlanError(Exception):
    """Base class for organizer problems that should be shown to the user."""


class ProtectedFolderError(PlanError):
    """The chosen folder is too risky to reorganize (drive root, system folder)."""


class StalePlanError(PlanError):
    """Files changed after the preview; the user must preview again."""

    def __init__(self, changed: list[tuple[str, str]]) -> None:
        super().__init__("Some files changed since the preview was made.")
        self.changed = changed


class FileChangedError(Exception):
    """A single file changed right before it was moved."""


@dataclass
class PlannedAction:
    name: str
    source: Path
    destination: Path | None
    category: str
    duplicate_status: str
    action: str                 # "Move" or "Skip"
    note: str = ""              # e.g. "renamed to avoid conflict" / skip reason
    size_bytes: int = 0
    modified_ns: int = 0

    @property
    def description(self) -> str:
        if self.action == "Skip":
            return f"Skip: {self.note}"
        return f"Move ({self.note})" if self.note else "Move"


@dataclass
class OrganizationPlan:
    folder: Path
    actions: list[PlannedAction] = field(default_factory=list)
    separate_duplicates: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def moves(self) -> list[PlannedAction]:
        return [a for a in self.actions if a.action == "Move"]

    @property
    def skips(self) -> list[PlannedAction]:
        return [a for a in self.actions if a.action == "Skip"]


@dataclass
class OrganizeResult:
    operation_id: int | None = None
    moved: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    cancelled: bool = False


# --------------------------------------------------------------------------
# Protection rules
# --------------------------------------------------------------------------
def get_protected_paths(extra: Iterable[str | Path] = ()) -> set[str]:
    """Keys of files the app must never move (its own program files)."""
    protected: set[str] = set()
    if getattr(sys, "frozen", False):                 # running as a PyInstaller .exe
        protected.add(path_key(Path(sys.executable).resolve()))
    else:
        app_dir = Path(__file__).resolve().parent
        protected.update(path_key(p.resolve()) for p in app_dir.glob("*.py"))
    for item in extra:
        protected.add(path_key(Path(item).resolve()))
    return protected


def protected_folder_reason(folder: Path) -> str | None:
    """Return why a folder must not be organized, or None if it is fine."""
    resolved = folder.resolve()
    if resolved.parent == resolved:
        return "it is a drive root"
    for variable in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                     "ProgramW6432", "ProgramData"):
        value = os.environ.get(variable)
        if not value:
            continue
        base = Path(value).resolve()
        if resolved == base or base in resolved.parents:
            return f"it is part of a system folder ({base})"
    if resolved == Path.home().resolve():
        return "it is your whole user profile folder"
    return None


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------
def build_plan(
    scan_result: ScanResult,
    separate_duplicates: bool = False,
    protected_paths: Iterable[str] | None = None,
) -> OrganizationPlan:
    """Create a plan describing what would be moved. NEVER changes any file."""
    root = scan_result.folder
    reason = protected_folder_reason(root)
    if reason:
        raise ProtectedFolderError(f"Refusing to organize this folder because {reason}.")

    protected = set(protected_paths) if protected_paths is not None else get_protected_paths()
    plan = OrganizationPlan(folder=root, separate_duplicates=separate_duplicates)
    taken: set[str] = set()   # destinations already promised to other files

    for info in scan_result.files:
        plan.actions.append(_plan_one(info, root, separate_duplicates, protected, taken))
    return plan


def _plan_one(
    info: FileInfo, root: Path, separate_duplicates: bool,
    protected: set[str], taken: set[str],
) -> PlannedAction:
    def skip(why: str) -> PlannedAction:
        return PlannedAction(info.name, info.path, None, info.category,
                             info.duplicate_status, "Skip", why,
                             info.size_bytes, info.modified_ns)

    if path_key(info.path.resolve()) in protected:
        return skip("protected application file")
    if info.is_hidden_or_system:
        return skip("hidden or system file")

    is_extra_copy = info.duplicate_group is not None and not info.is_primary_copy
    folder_name = DUPLICATES_FOLDER if (separate_duplicates and is_extra_copy) else info.category
    dest_dir = root / folder_name
    if os.path.lexists(dest_dir) and not dest_dir.is_dir():
        return skip(f"a file named '{folder_name}' blocks the folder")

    destination = unique_destination(dest_dir, info.name, taken)
    taken.add(path_key(destination))
    note = "renamed to avoid conflict" if destination.name != info.name else ""
    return PlannedAction(info.name, info.path, destination, info.category,
                         info.duplicate_status, "Move", note,
                         info.size_bytes, info.modified_ns)


def find_changed_files(plan: OrganizationPlan) -> list[tuple[str, str]]:
    """List files whose size/time/type differ from when the preview was made."""
    changed: list[tuple[str, str]] = []
    for action in plan.moves:
        try:
            st = os.lstat(action.source)
        except FileNotFoundError:
            changed.append((action.name, "no longer exists"))
            continue
        except OSError as exc:
            changed.append((action.name, f"cannot be read ({exc.strerror})"))
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            changed.append((action.name, "is no longer a regular file"))
        elif st.st_size != action.size_bytes or st.st_mtime_ns != action.modified_ns:
            changed.append((action.name, "was modified"))
    return changed


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def execute_plan(
    plan: OrganizationPlan,
    db: "HistoryDB | None" = None,   # noqa: F821  (imported lazily for typing only)
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OrganizeResult:
    """Carry out the plan. Raises StalePlanError if any file changed since preview."""
    reason = protected_folder_reason(plan.folder)
    if reason:
        raise ProtectedFolderError(f"Refusing to organize this folder because {reason}.")
    changed = find_changed_files(plan)
    if changed:
        raise StalePlanError(changed)

    result = OrganizeResult()
    operation_id = db.start_operation("ORGANIZE", str(plan.folder)) if db else None
    result.operation_id = operation_id

    for action in plan.skips:
        result.skipped += 1
        if db:
            db.log_activity(operation_id, "Skip", action.name, str(action.source), "",
                            "Skipped", action.note)

    moves = plan.moves
    for index, action in enumerate(moves, start=1):
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
            break
        try:
            destination, digest = _move_one(action)
        except PermissionError as exc:
            _record_failure(result, db, operation_id, action,
                            f"Permission denied: {exc.strerror or exc}")
        except FileChangedError as exc:
            _record_failure(result, db, operation_id, action, str(exc))
        except OSError as exc:
            _record_failure(result, db, operation_id, action, exc.strerror or str(exc))
        else:
            result.moved += 1
            if db:
                db.log_activity(operation_id, "Move", action.name, str(action.source),
                                str(destination), "Success", "", action.size_bytes,
                                action.modified_ns, digest)
        if progress_callback is not None:
            progress_callback(index, len(moves))

    if db and operation_id is not None:
        db.finish_operation(
            operation_id,
            f"{result.moved} moved, {len(result.failed)} failed, {result.skipped} skipped",
        )
    return result


def _record_failure(result, db, operation_id, action, message: str) -> None:
    result.failed.append((action.name, message))
    if db:
        db.log_activity(operation_id, "Move", action.name, str(action.source),
                        str(action.destination or ""), "Failed", message,
                        action.size_bytes, action.modified_ns)


def _move_one(action: PlannedAction) -> tuple[Path, str | None]:
    """Move one file safely. Returns (final destination, sha256 or None)."""
    st = os.lstat(action.source)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise FileChangedError("File is no longer a regular file")
    if st.st_size != action.size_bytes or st.st_mtime_ns != action.modified_ns:
        raise FileChangedError("File changed just before it was moved")

    # Fingerprint for undo. Reading it also proves the file is accessible.
    digest = hash_file(action.source) if st.st_size <= IDENTITY_HASH_LIMIT else None

    assert action.destination is not None
    dest_dir = action.destination.parent
    if os.path.lexists(dest_dir) and not dest_dir.is_dir():
        raise OSError(f"'{dest_dir.name}' exists and is not a folder")
    dest_dir.mkdir(exist_ok=True)           # created only when a file needs it

    destination = action.destination
    for _ in range(10):
        if os.path.lexists(destination):
            destination = unique_destination(dest_dir, action.name)
        try:
            safe_move_no_overwrite(action.source, destination)
            return destination, digest
        except FileExistsError:             # someone took the name a moment ago
            destination = unique_destination(dest_dir, action.name)
    raise OSError("Could not find a free file name")
